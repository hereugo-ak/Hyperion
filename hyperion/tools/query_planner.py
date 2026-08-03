"""
HYPERION Query Planner — LLM-reasoned, schema-validated query diversification.

This module is the fix for audit §4.4 Finding B-3 ("no agent or sub-agent
ever *reasons* about what to search") and §7 Phase 1 item 1.3 ("Add an LLM
query planner: sub-question -> 5-10 schema-validated diversified queries
(entity / metric / counter-thesis / regulatory / competitor / time-series).
Run at FAST tier, cache by sub-question hash.").

WHY THIS EXISTS
---------------
Before this module, `SubAgentRunner` built **exactly one** query per tool
via `_condense_query`, a pure regex + stopword pipeline. A human MBB
associate handed "should we enter now or wait?" runs 8-15 differently
angled searches:

  - the *entity* angle      — who are the named players/products/agencies
  - the *metric* angle      — market size, CAGR, unit economics, margins
  - the *counter-thesis*    — failures, why-not, risks, bear case
  - the *regulatory* angle  — statutes, licences, compliance, bans
  - the *competitor* angle  — incumbents, share, benchmarking
  - the *time-series* angle — trend, forecast, historical trajectory

A stopword filter cannot produce those. Only a reasoning step can. This
module is that step, kept deliberately small and *outside* `sub_agent.py`
so it is unit-testable without spinning up a sub-agent, and reusable by
any future caller (specialists, `deep_search`, the research librarian).

DESIGN CONSTRAINTS (from the audit)
-----------------------------------
1. **FAST tier only.** Query planning is a cheap, high-volume, low-stakes
   generation task. It must never burn STRONG/DEEP quota (§4.7). The tier
   is a module constant, not a caller argument, so a caller cannot
   accidentally escalate it.
2. **Schema-validated.** The LLM's raw output is never trusted. Every
   query is validated against `PlannedQuery` (Pydantic), out-of-vocabulary
   angles are rejected, empty/duplicate/over-long queries are dropped, and
   the whole set is clamped to `[MIN_QUERIES, MAX_QUERIES]`.
3. **Cached by sub-question hash.** Multiple specialists frequently spawn
   near-identical sub-questions in one engagement; and a low-yield retry
   re-enters the same code path. The cache is keyed on a normalized
   sub-question hash so those all collapse to one LLM call.
4. **Never raises, never returns empty.** A planner failure must degrade
   to the deterministic regex path, not zero queries — the audit's whole
   point is that a silent failure in the query layer caused a 100%
   research outage. `plan_queries` therefore always returns >= 1 query,
   and logs loudly (WARNING) whenever it had to fall back.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
from typing import Any

from pydantic import BaseModel, Field, field_validator

from hyperion.agents.prompt_contract import compose_agent_prompt
from hyperion.config import ModelTier
from hyperion.router.budget import TaskUrgency

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Contract constants
# ─────────────────────────────────────────────────────────────────────────

#: The audit is explicit: "Run at FAST tier" (§7 item 1.3). Not a caller
#: argument — a module constant, so no call site can escalate query
#: planning onto STRONG/DEEP quota.
PLANNER_TIER: ModelTier = ModelTier.FAST

#: The audit's exit criterion for Phase 1 is ">=8 distinct grounded queries
#: per sub-question", and item 1.3 asks for "5-10". We therefore *request*
#: 10 and accept down to 5 from the model, then top up deterministically to
#: at least `TARGET_QUERIES` so the exit criterion is met even when the LLM
#: under-delivers.
MIN_QUERIES = 5
TARGET_QUERIES = 8
MAX_QUERIES = 10

#: Search engines degrade badly past ~120 chars (same rationale as
#: `_condense_query`'s cap).
MAX_QUERY_LEN = 120

#: The angle vocabulary named verbatim in audit §7 item 1.3 and §5.2 item 3.
#: A planned query whose angle is not in this set is rejected by the schema —
#: this is what stops the model from inventing an "angle" that is really a
#: restatement of the question.
ANGLES: tuple[str, ...] = (
    "entity",
    "metric",
    "counter_thesis",
    "regulatory",
    "competitor",
    "time_series",
)

#: Angles that must be present for a plan to be considered *diversified*
#: rather than six rephrasings of one idea. If the model returns fewer
#: distinct angles than this, deterministic angles are appended.
MIN_DISTINCT_ANGLES = 4


# ─────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────


class PlannedQuery(BaseModel):
    """One diversified search query plus the angle it covers.

    The `angle` field is what makes this a *plan* rather than a list of
    strings: it lets the caller reason about coverage (e.g. "we have no
    counter-thesis query, top one up"), lets tests assert diversification
    structurally, and lets the yield logging in Phase 2.6 attribute
    extraction yield per angle.
    """

    query: str = Field(..., min_length=3, max_length=MAX_QUERY_LEN)
    angle: str
    rationale: str = ""

    @field_validator("angle")
    @classmethod
    def _angle_in_vocabulary(cls, v: str) -> str:
        normalized = (v or "").strip().lower().replace("-", "_").replace(" ", "_")
        # Tolerate the most common near-misses a model produces for the
        # multi-word angles rather than discarding an otherwise-good query.
        aliases = {
            "counterthesis": "counter_thesis",
            "counter": "counter_thesis",
            "contrarian": "counter_thesis",
            "bear_case": "counter_thesis",
            "timeseries": "time_series",
            "time": "time_series",
            "trend": "time_series",
            "historical": "time_series",
            "regulation": "regulatory",
            "regulatory_legal": "regulatory",
            "legal": "regulatory",
            "policy": "regulatory",
            "metrics": "metric",
            "quantitative": "metric",
            "market_size": "metric",
            "entities": "entity",
            "players": "entity",
            "competitors": "competitor",
            "competitive": "competitor",
        }
        normalized = aliases.get(normalized, normalized)
        if normalized not in ANGLES:
            raise ValueError(
                f"angle must be one of {ANGLES}, got {v!r}"
            )
        return normalized

    @field_validator("query")
    @classmethod
    def _query_is_searchable(cls, v: str) -> str:
        q = re.sub(r"\s+", " ", (v or "")).strip()
        if not q:
            raise ValueError("query is empty")
        # A query with no alphabetic content ("2024 2025 $100 50%") is the
        # exact "contentless" failure mode `query_utils.is_contentless`
        # exists to catch. Reject it at the schema boundary too, so the
        # planner can never *originate* one.
        if len(re.findall(r"[A-Za-z]{2,}", q)) < 2:
            raise ValueError(f"query has no searchable content: {v!r}")
        if len(q) > MAX_QUERY_LEN:
            q = q[:MAX_QUERY_LEN].rsplit(" ", 1)[0]
        return q


class QueryPlan(BaseModel):
    """A validated, diversified set of queries for one sub-question."""

    sub_question: str
    queries: list[PlannedQuery] = Field(default_factory=list)
    #: True when the plan came from the deterministic fallback rather than
    #: the LLM. Surfaced so callers/tests can distinguish "planner worked"
    #: from "planner degraded" instead of both looking like success —
    #: exactly the distinction whose absence hid the audit's P0.
    degraded: bool = False
    cached: bool = False

    @property
    def query_strings(self) -> list[str]:
        return [q.query for q in self.queries]

    @property
    def angles_covered(self) -> set[str]:
        return {q.angle for q in self.queries}


# ─────────────────────────────────────────────────────────────────────────
# Cache — keyed by normalized sub-question hash (audit §7 item 1.3)
# ─────────────────────────────────────────────────────────────────────────


def sub_question_hash(sub_question: str, *, subject: str = "", geography: str = "") -> str:
    """Stable cache key for a sub-question.

    Normalizes case, whitespace and trailing punctuation *before* hashing,
    so "Market size in Nigeria?" and "market  size in nigeria" collapse to
    one cache entry — which is the common case when several specialists
    independently spawn the same sub-question in one engagement.

    Subject and geography participate in the key because the same literal
    sub-question ("what is the regulatory outlook?") must not reuse a plan
    built for a different engagement focus.
    """
    normalized = re.sub(r"\s+", " ", (sub_question or "").strip().lower()).strip(" .?!,;:")
    payload = "\u241f".join(
        (
            normalized,
            re.sub(r"\s+", " ", (subject or "").strip().lower()),
            re.sub(r"\s+", " ", (geography or "").strip().lower()),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class _PlanCache:
    """Small thread-safe LRU-ish cache for query plans.

    Deliberately process-local and unbounded-until-`max_entries`: a query
    plan is a handful of short strings, and an engagement is minutes long.
    Persisting it would add a cache-invalidation problem for no benefit.
    """

    def __init__(self, max_entries: int = 512) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, QueryPlan] = {}
        self._order: list[str] = []
        self._max_entries = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> QueryPlan | None:
        with self._lock:
            plan = self._store.get(key)
            if plan is None:
                self.misses += 1
                return None
            self.hits += 1
            # Refresh recency
            with contextlib.suppress(ValueError):
                self._order.remove(key)
            self._order.append(key)
            # Return a copy flagged as cached so the caller can tell.
            return plan.model_copy(update={"cached": True})

    def put(self, key: str, plan: QueryPlan) -> None:
        with self._lock:
            if key not in self._store and len(self._order) >= self._max_entries:
                oldest = self._order.pop(0)
                self._store.pop(oldest, None)
            self._store[key] = plan.model_copy(update={"cached": False})
            if key in self._order:
                self._order.remove(key)
            self._order.append(key)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()
            self._order.clear()
            self.hits = 0
            self.misses = 0

    def stats(self) -> dict[str, int]:
        with self._lock:
            return {"entries": len(self._store), "hits": self.hits, "misses": self.misses}


_CACHE = _PlanCache()


def clear_plan_cache() -> None:
    """Reset the module-level plan cache (used by tests and per-engagement teardown)."""
    _CACHE.clear()


def plan_cache_stats() -> dict[str, int]:
    """Cache counters, for the Phase 2.6 per-engagement metrics surface."""
    return _CACHE.stats()


# ─────────────────────────────────────────────────────────────────────────
# Prompting
# ─────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a search strategist at a top-tier management consulting firm. "
    "Your only job is to turn one research sub-question into a diversified "
    "set of web search queries that a research associate will actually run.\n\n"
    "You do NOT answer the question. You do NOT speculate. You produce queries.\n\n"
    "Cover these distinct angles (use the exact angle labels):\n"
    "  entity          — the specific named companies, products, agencies, or people involved\n"
    "  metric          — the quantitative measures: market size, CAGR, unit cost, margin, share\n"
    "  counter_thesis  — the bear case: failures, cancellations, why this does NOT work, criticism\n"
    "  regulatory      — statutes, licences, tariffs, standards, compliance obligations, bans\n"
    "  competitor      — incumbents, challengers, benchmarking, competitive response\n"
    "  time_series     — historical trajectory, trend, forecast, year-over-year change\n\n"
    "Rules:\n"
    f"1. Produce between {MIN_QUERIES} and {MAX_QUERIES} queries.\n"
    "2. Every query must be a keyword-style search string, NOT a sentence and NOT a question.\n"
    f"3. Every query must be under {MAX_QUERY_LEN} characters.\n"
    "4. Queries must be genuinely different in what they would retrieve — "
    "not the same query with a synonym swapped.\n"
    "5. Preserve the subject and geography of the sub-question in most queries; "
    "one or two deliberately broader queries are allowed.\n"
    f"6. Cover at least {MIN_DISTINCT_ANGLES} different angles.\n"
    "7. Never invent facts, entity names, or numbers that are not implied by the "
    "sub-question or the provided context. If you do not know the incumbents, "
    "write a query that would FIND them, do not name a guess.\n\n"
    'Return ONLY a JSON object of the form {"queries": [{"query": "...", '
    '"angle": "...", "rationale": "..."}]}'
)


def _build_user_prompt(
    sub_question: str,
    *,
    subject: str = "",
    geography: str = "",
    context: dict[str, Any] | None = None,
    parent_agent: str = "",
) -> str:
    parts = [f"Sub-question: {sub_question}"]
    if subject:
        parts.append(f"Engagement subject: {subject}")
    if geography:
        parts.append(f"Engagement geography: {geography}")
    if parent_agent:
        # The parent agent's *analytical lens* is useful planning context.
        # NOTE: it must never leak into a query string — audit §4.9 Finding
        # B-8 is exactly the bug where an internal agent name ("market
        # analyst") ended up inside an outbound search query. The system
        # prompt above never asks for it, and `_sanitize` below strips it.
        parts.append(
            f"Requesting analyst's lens (planning context only — do NOT put this "
            f"in any query): {parent_agent.replace('_', ' ')}"
        )
    if context:
        ctx_lines = [
            f"  {k}: {v}"
            for k, v in list(context.items())[:8]
            if v not in (None, "", [], {})
        ]
        if ctx_lines:
            parts.append("Known context:\n" + "\n".join(ctx_lines))
    parts.append(
        f"Produce {TARGET_QUERIES}-{MAX_QUERIES} diversified search queries as JSON."
    )
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────
# Deterministic fallback / top-up
# ─────────────────────────────────────────────────────────────────────────

#: Keyword suffixes that turn a bare subject into an angled query without
#: any LLM call. These are the deterministic floor: they guarantee the
#: audit's ">=8 distinct queries per sub-question" exit criterion holds
#: even when the planner LLM is unavailable, rate-limited, or returns junk.
_ANGLE_SUFFIXES: dict[str, tuple[str, ...]] = {
    "entity": ("key players companies", "leading providers list"),
    "metric": ("market size forecast", "growth rate statistics"),
    "counter_thesis": ("risks criticism failure", "why it failed problems"),
    "regulatory": ("regulation compliance requirements", "policy law framework"),
    "competitor": ("competitive landscape market share", "competitor benchmarking"),
    "time_series": ("trend historical data", "outlook forecast by year"),
}


def _sanitize(query: str, *, forbidden: tuple[str, ...] = ()) -> str:
    """Clean a model-proposed query into something safe to dispatch.

    Strips quoting debris, collapses whitespace, removes any forbidden
    internal-vocabulary token (audit §4.9), and truncates at a word
    boundary.
    """
    q = (query or "").strip().strip("\"'`")
    q = re.sub(r"\s+", " ", q)
    for token in forbidden:
        token = (token or "").strip()
        if len(token) < 3:
            continue
        q = re.sub(rf"(?<![A-Za-z0-9]){re.escape(token)}(?![A-Za-z0-9])", " ", q, flags=re.IGNORECASE)
    # Drop a trailing question mark — search engines do better with keywords.
    q = re.sub(r"\s+", " ", q).strip().rstrip("?").strip()
    if len(q) > MAX_QUERY_LEN:
        q = q[:MAX_QUERY_LEN].rsplit(" ", 1)[0]
    return q.strip()


def _dedup_key(query: str) -> str:
    """Normalized key for near-duplicate detection across planned queries."""
    tokens = sorted({t for t in re.findall(r"[a-z0-9]+", query.lower()) if len(t) > 2})
    return " ".join(tokens)


def deterministic_plan(
    sub_question: str,
    *,
    subject: str = "",
    geography: str = "",
    target: int = TARGET_QUERIES,
) -> QueryPlan:
    """Build a diversified plan with no LLM call at all.

    This is both (a) the fallback when the planner LLM fails, and (b) the
    top-up source when the LLM returns fewer than `target` usable queries.
    It is intentionally boring and deterministic: same input, same output,
    no network, never raises.
    """
    from hyperion.agents.sub_agent import SubAgentRunner  # local: avoid cycle

    base = ""
    try:
        base = SubAgentRunner._condense_query(sub_question, max_len=MAX_QUERY_LEN)
    except Exception as e:  # pragma: no cover — _condense_query is hardened
        logger.warning("deterministic_plan: _condense_query failed: %s", e, exc_info=True)
    if not base:
        base = re.sub(r"\s+", " ", (sub_question or "")).strip()[:MAX_QUERY_LEN]

    anchor_parts = [p for p in (subject.strip(), base) if p]
    # Avoid "Nigeria lithium Nigeria lithium ..." when the subject is
    # already inside the condensed question.
    if subject and subject.strip().lower() in base.lower():
        anchor_parts = [base]
    anchor = " ".join(anchor_parts).strip() or base
    # Interrogative punctuation is noise in a keyword query, and it is
    # actively harmful mid-string once an angle suffix is appended
    # ("... market wait? regulation compliance"). Strip it here rather than
    # in `_sanitize`, which only sees the finished query and can therefore
    # only fix a *trailing* '?'.
    anchor = anchor.strip(" ?!.,;:")
    # Reserve room for the angle suffix so appending one does not push the
    # angle keywords off the end of the `MAX_QUERY_LEN` truncation — a
    # "regulatory" query whose only regulatory word got truncated away is
    # not a regulatory query.
    _suffix_room = max(len(s) for suffixes in _ANGLE_SUFFIXES.values() for s in suffixes) + 1
    short_anchor = anchor
    if len(short_anchor) > MAX_QUERY_LEN - _suffix_room:
        short_anchor = short_anchor[: MAX_QUERY_LEN - _suffix_room].rsplit(" ", 1)[0]
    geo = geography.strip()

    planned: list[PlannedQuery] = []
    seen: set[str] = set()

    # Variant 0: the plain condensed query (what the pre-1.3 code did).
    for candidate, angle, why in _iter_deterministic_candidates(anchor, short_anchor, geo):
        q = _sanitize(candidate)
        if not q:
            continue
        key = _dedup_key(q)
        if key in seen:
            continue
        try:
            planned.append(PlannedQuery(query=q, angle=angle, rationale=why))
        except ValueError:
            continue
        seen.add(key)
        if len(planned) >= max(target, MIN_QUERIES):
            break

    return QueryPlan(sub_question=sub_question, queries=planned[:MAX_QUERIES], degraded=True)


def _iter_deterministic_candidates(
    anchor: str, short_anchor: str, geo: str
) -> list[tuple[str, str, str]]:
    """Ordered (query, angle, rationale) candidates for the deterministic plan.

    Order matters: the first candidate is the plain condensed query (so the
    deterministic plan is a strict superset of pre-1.3 behaviour), then one
    query per angle round-robin so a truncated plan is still diversified,
    then the geography-dropped broadening variants.

    Args:
        anchor: the full condensed anchor, used verbatim for the baseline query.
        short_anchor: `anchor` pre-trimmed to leave room for an angle suffix,
            so the suffix survives `MAX_QUERY_LEN` truncation.
        geo: geography anchor, appended only when not already in the anchor.
    """
    out: list[tuple[str, str, str]] = [
        (anchor, "entity", "plain condensed sub-question (pre-planner baseline)")
    ]
    if geo and geo.lower() not in short_anchor.lower():
        geo_anchor = f"{short_anchor} {geo}".strip()
    else:
        geo_anchor = short_anchor

    # Round 1: one query per angle, geography-anchored.
    for angle in ANGLES:
        suffixes = _ANGLE_SUFFIXES[angle]
        out.append(
            (
                f"{geo_anchor} {suffixes[0]}",
                angle,
                f"deterministic {angle} angle (geography-anchored)",
            )
        )
    # Round 2: second suffix per angle, and a geography-free broadening pass.
    for angle in ANGLES:
        suffixes = _ANGLE_SUFFIXES[angle]
        if len(suffixes) > 1:
            out.append(
                (
                    f"{short_anchor} {suffixes[1]}",
                    angle,
                    f"deterministic {angle} angle (broadened, no geography)",
                )
            )
    return out


# ─────────────────────────────────────────────────────────────────────────
# The planner
# ─────────────────────────────────────────────────────────────────────────


def _parse_llm_queries(content: str, *, forbidden: tuple[str, ...]) -> list[PlannedQuery]:
    """Parse + schema-validate the planner LLM's JSON output.

    Never raises. Every individually-invalid query is dropped with a debug
    log rather than sinking the whole plan — a model that returns 9 good
    queries and 1 malformed one should give us 9 queries, not zero.
    """
    from hyperion.router.structured_validator import extract_json

    raw = (content or "").strip()
    if not raw:
        return []

    data: Any = None
    for candidate in (raw, extract_json(raw) or ""):
        if not candidate:
            continue
        try:
            data = json.loads(candidate)
            break
        except (json.JSONDecodeError, ValueError):
            continue
    if data is None:
        logger.warning("query planner: response was not JSON: %r", raw[:200])
        return []

    if isinstance(data, dict):
        items = data.get("queries") or data.get("plan") or data.get("results") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if not isinstance(items, list):
        logger.warning("query planner: 'queries' was not a list: %r", str(items)[:120])
        return []

    planned: list[PlannedQuery] = []
    seen: set[str] = set()
    for item in items:
        if isinstance(item, str):
            item = {"query": item, "angle": "entity"}
        if not isinstance(item, dict):
            continue
        q = _sanitize(str(item.get("query", "")), forbidden=forbidden)
        if not q:
            continue
        key = _dedup_key(q)
        if key in seen:
            continue
        try:
            planned.append(
                PlannedQuery(
                    query=q,
                    angle=str(item.get("angle", "entity")),
                    rationale=str(item.get("rationale", ""))[:300],
                )
            )
        except ValueError as e:
            logger.debug("query planner: dropped invalid query %r: %s", q[:80], e)
            continue
        seen.add(key)
        if len(planned) >= MAX_QUERIES:
            break

    return planned


def _top_up(
    plan_queries: list[PlannedQuery],
    sub_question: str,
    *,
    subject: str,
    geography: str,
    target: int,
) -> list[PlannedQuery]:
    """Append deterministic queries until `target` count and angle coverage are met."""
    seen = {_dedup_key(q.query) for q in plan_queries}
    covered = {q.angle for q in plan_queries}
    fallback = deterministic_plan(
        sub_question, subject=subject, geography=geography, target=MAX_QUERIES
    )

    # First pass: fill missing *angles* (diversification beats raw count).
    for candidate in fallback.queries:
        if len(plan_queries) >= MAX_QUERIES:
            break
        if candidate.angle in covered and len(covered) >= MIN_DISTINCT_ANGLES:
            continue
        key = _dedup_key(candidate.query)
        if key in seen:
            continue
        plan_queries.append(candidate)
        seen.add(key)
        covered.add(candidate.angle)

    # Second pass: fill raw count up to target.
    for candidate in fallback.queries:
        if len(plan_queries) >= max(target, MIN_QUERIES) or len(plan_queries) >= MAX_QUERIES:
            break
        key = _dedup_key(candidate.query)
        if key in seen:
            continue
        plan_queries.append(candidate)
        seen.add(key)

    return plan_queries[:MAX_QUERIES]


async def plan_queries(
    sub_question: str,
    *,
    router: Any = None,
    subject: str = "",
    geography: str = "",
    context: dict[str, Any] | None = None,
    parent_agent: str = "",
    target: int = TARGET_QUERIES,
    use_cache: bool = True,
) -> QueryPlan:
    """Turn one sub-question into 5-10 validated, diversified search queries.

    This is the public entry point for audit §7 item 1.3.

    Contract:
      - Runs at `PLANNER_TIER` (FAST). Never escalates.
      - Caches by `sub_question_hash(sub_question, subject, geography)`.
      - Always returns >= 1 query. Never raises.
      - Sets `degraded=True` when the LLM path did not produce the plan,
        so a silent planner outage is visible rather than looking like
        success (the lesson of the audit's P0).

    Args:
        router: An `LLMRouter`-like object exposing
            ``await complete(tier=..., messages=..., ...) -> RouterResponse``.
            When omitted, the process router is used. Pass `None` explicitly
            plus `use_cache=False` in tests to exercise the fallback path.
    """
    question = re.sub(r"\s+", " ", (sub_question or "")).strip()
    if not question:
        return QueryPlan(sub_question="", queries=[], degraded=True)

    key = sub_question_hash(question, subject=subject, geography=geography)
    if use_cache:
        cached = _CACHE.get(key)
        if cached is not None:
            logger.debug(
                "query planner: cache hit for %r (%d queries)", question[:80], len(cached.queries)
            )
            return cached

    if router is None:
        try:
            from hyperion.router.router import get_router

            router = get_router()
        except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("query planner: no router available (%s); using deterministic plan", e)
            router = None

    # Forbidden vocabulary = the internal agent *phrase* plus the role nouns
    # that identify HYPERION's own org chart (audit §4.9 Finding B-8: the
    # fact checker used to glue "market analyst" onto its search query).
    #
    # CRITICAL: this must be the multi-word phrase, NOT its tokens. Splitting
    # "market_analyst" into {"market", "analyst"} and stripping each would
    # delete the word "market" from "Nigeria battery market size 2025 CAGR" —
    # destroying the query it was meant to protect. Only the full phrase and
    # the unambiguous role nouns are stripped.
    forbidden_list: list[str] = ["analyst", "hyperion", "sub-agent", "subagent"]
    agent_phrase = (parent_agent or "").replace("_", " ").strip()
    if agent_phrase:
        forbidden_list.insert(0, agent_phrase)
    forbidden = tuple(forbidden_list)

    planned: list[PlannedQuery] = []
    degraded = True

    if router is not None:
        try:
            response = await router.complete(
                tier=PLANNER_TIER,
                messages=[
                    {"role": "system", "content": compose_agent_prompt(_SYSTEM_PROMPT)},
                    {
                        "role": "user",
                        "content": _build_user_prompt(
                            question,
                            subject=subject,
                            geography=geography,
                            context=context,
                            parent_agent=parent_agent,
                        ),
                    },
                ],
                agent_name="query_planner",
                urgency=TaskUrgency.LOW,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            if getattr(response, "success", False) and getattr(response, "content", ""):
                planned = _parse_llm_queries(response.content, forbidden=forbidden)
                degraded = not planned
                if not planned:
                    logger.warning(
                        "query planner: LLM returned no usable queries for %r; "
                        "falling back to deterministic plan",
                        question[:100],
                    )
            else:
                logger.warning(
                    "query planner: LLM call unsuccessful for %r (error=%r); "
                    "falling back to deterministic plan",
                    question[:100],
                    getattr(response, "error", ""),
                )
        except Exception as e:
            # Fail LOUD (audit fix 0.3) — but still degrade to a usable plan.
            logger.warning(
                "query planner: LLM planning failed for %r: %s", question[:100], e, exc_info=True
            )

    if not planned:
        plan = deterministic_plan(
            question, subject=subject, geography=geography, target=target
        )
        planned = list(plan.queries)
        degraded = True
    else:
        planned = _top_up(
            planned, question, subject=subject, geography=geography, target=target
        )

    plan = QueryPlan(sub_question=question, queries=planned[:MAX_QUERIES], degraded=degraded)

    logger.info(
        "query planner: %d queries across %d angles for %r (degraded=%s)",
        len(plan.queries),
        len(plan.angles_covered),
        question[:80],
        plan.degraded,
    )

    if use_cache and plan.queries:
        _CACHE.put(key, plan)

    return plan


# ─────────────────────────────────────────────────────────────────────────────
# P2-28: no-corpus subject detection (P2-G27)
#
# The planner validates query SHAPE (>= 2 alphabetic tokens), which correctly
# rejects a bare "EMERGING" — but multi-word queries about an entity with no
# web footprint still return results, matched on the common nouns (dictionary
# definitions). The planner had no feedback signal for that condition. These
# helpers provide it: subject recall after round 1, a strategy switch to
# registries/news/own-domain queries, and a no_corpus escalation when recall
# stays below threshold.
# ─────────────────────────────────────────────────────────────────────────────

NO_CORPUS_RECALL_THRESHOLD = 0.15


class NoCorpusError(RuntimeError):
    """The engagement subject has no findable web corpus (P2-28).

    Raised by :func:`assess_subject_recall` when recall stays below
    ``NO_CORPUS_RECALL_THRESHOLD`` even after the strategy switch. Report B
    was a 32-page report about an entity that does not appear on the web;
    this error is how the system says so instead.
    """

    def __init__(self, subject: str, recall: float) -> None:
        self.subject = subject
        self.recall = recall
        super().__init__(
            f"no_corpus: subject '{subject}' recall {recall:.2f} "
            f"(< {NO_CORPUS_RECALL_THRESHOLD}) after the strategy switch; "
            "the entity has no findable web footprint, declare the "
            "limitation and stop rather than writing around it."
        )


def subject_recall(subject: str, results: list[Any]) -> float:
    """Fraction of results whose title or snippet contains the subject.

    Token-boundary: every subject token of length >= 3 must appear as a
    token in the combined title+snippet. Accepts dicts (``title`` /
    ``snippet`` keys) or SearchResult-like objects.
    """
    tokens = [
        t for t in re.findall(r"[a-z0-9]+", (subject or "").lower()) if len(t) >= 3
    ]
    if not tokens or not results:
        return 0.0
    hits = 0
    for r in results:
        if isinstance(r, dict):
            text = f"{r.get('title', '')} {r.get('snippet', '')}"
        else:
            text = f"{getattr(r, 'title', '')} {getattr(r, 'snippet', '')}"
        text_tokens = set(re.findall(r"[a-z0-9]+", text.lower()))
        if all(tok in text_tokens for tok in tokens):
            hits += 1
    return hits / len(results)


def no_corpus_fallback_queries(subject: str, geography: str = "") -> list[str]:
    """Strategy-switch queries for a low-recall subject (P2-28).

    When general-web recall is below threshold, query where an entity MUST
    appear if it exists: its own domain, corporate registries, and news
    archives.
    """
    s = re.sub(r"\s+", " ", (subject or "")).strip()
    if not s:
        return []
    geo = f" {geography.strip()}" if geography and geography.strip() else ""
    slug = re.sub(r"[^a-z0-9]+", "", s.lower())
    queries = [
        f'"{s}"{geo} company registry registration',
        f'"{s}" corporate affairs commission{geo}',
        f'"{s}"{geo} news announcement',
        f'"{s}" annual report OR filing OR press release',
    ]
    if slug:
        queries.append(f"site:{slug}.com OR site:{slug}.co {s}")
    return queries


def assess_subject_recall(subject: str, round1: float, round2: float) -> float:
    """Decide whether a subject has a web corpus (P2-G27).

    Round 1 recall below threshold triggers the strategy switch (the caller
    issues :func:`no_corpus_fallback_queries`); round 2 recall still below
    threshold raises :class:`NoCorpusError`. A recovered round 2 returns the
    recall and the engagement proceeds.
    """
    if round2 < NO_CORPUS_RECALL_THRESHOLD:
        raise NoCorpusError(subject, round2)
    logger.info(
        "subject recall recovered after strategy switch: %.2f -> %.2f",
        round1, round2,
    )
    return round2


__all__ = [
    "ANGLES",
    "MAX_QUERIES",
    "MAX_QUERY_LEN",
    "MIN_DISTINCT_ANGLES",
    "MIN_QUERIES",
    "NO_CORPUS_RECALL_THRESHOLD",
    "PLANNER_TIER",
    "TARGET_QUERIES",
    "NoCorpusError",
    "PlannedQuery",
    "QueryPlan",
    "assess_subject_recall",
    "clear_plan_cache",
    "deterministic_plan",
    "no_corpus_fallback_queries",
    "plan_cache_stats",
    "plan_queries",
    "sub_question_hash",
    "subject_recall",
]
