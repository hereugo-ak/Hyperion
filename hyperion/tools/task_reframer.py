"""HYPERION Task Reframer — LLM-based, schema-validated task rephrasing.

L3 fix. Mirror of :mod:`hyperion.tools.query_planner` for a different
purpose: not "how do I search this?" but "how do I ASK this differently
after the first attempt returned nothing?".

WHY THIS EXISTS
---------------
When a specialist sub-agent returns FAILED, times out, or returns only
research_gap findings, the orchestrator currently has two options:

  (a) Escalate to the Director's STRONG-tier strategic replanner
      (``_apply_adaptation`` at ``engagement_director.py:667``), which
      costs a STRONG-tier LLM call for what may be a common "thin query"
      case, and consumes one of the Director's 12 adaptation slots.
  (b) Drop the task.

Both are wasteful for the ROUTINE case: a well-formed task whose
question happened to be over-narrow, over-geography-anchored, or worded
in a way that returned nothing. That does not need STRONG-tier
reasoning; it needs a cheap FAST-tier "same question, three angles"
rephrasing.

This module IS that rephrasing step, kept deliberately small and
outside the orchestrator so it is unit-testable without spinning up the
full engagement.

DESIGN CONSTRAINTS
------------------
1. **FAST tier only.** Same rationale as ``PLANNER_TIER``: task
   rephrasing is a cheap, high-volume, low-stakes generation task. Never
   escalates onto STRONG/DEEP quota.
2. **Purpose-built prompt (NOT generic).** Inputs = failed task
   description, original sub-question, engagement subject/geography, and
   the failure signal (0 findings / timeout / research_gap). Output =
   schema-validated JSON of up to 3 rephrased task variants, each with a
   ``broaden_strategy`` from an enumerated set.
3. **Schema-validated.** Every variant is validated against
   ``RephrasedTask`` (Pydantic). Out-of-vocabulary strategies are
   rejected; empty/duplicate rephrasings are dropped.
4. **Cached by (question_hash, failure_signal).**
5. **Never raises, never returns empty.** On any LLM failure the
   reframer degrades to a deterministic broaden sequence
   (``_deterministic_variants``), which uses the same two knobs
   ``sub_agent._condense_query_variants`` already uses:
   drop-geography and industry-synonym substitution. That way the
   reframer can never make things worse — a genuine 5xx storm still
   yields at least one deterministic retry.

CONTRACT
--------
``reframe_task`` always returns 1-3 :class:`RephrasedTask`
instances. Callers should treat ``degraded=True`` as "LLM was
unavailable — you got the deterministic broadening ladder, not a
model-reasoned rephrase".
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import re
import threading
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator

from hyperion.agents.prompt_contract import compose_agent_prompt
from hyperion.config import ModelTier
from hyperion.router.budget import TaskUrgency

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────
# Contract constants
# ─────────────────────────────────────────────────────────────────────────

#: The reframer runs at FAST tier. NEVER STRONG or DEEP — that would defeat
#: the whole point of the reframer, which is to sit BENEATH the Director's
#: strategic replanner as the cheap first-attempt broadening step.
REFRAMER_TIER: ModelTier = ModelTier.FAST

#: The reframer always returns between 1 and this many variants. A single
#: LLM call is cheaper than three, so we ask for three and clamp downward
#: if the model returns fewer usable rephrasings.
MAX_VARIANTS: int = 3

#: Same cap as the query planner: search engines and provider prompts
#: degrade badly past ~200 chars. A rephrased task is a sub-question, not
#: a keyword string, so we allow more length than the planner's 120.
MAX_TASK_LEN: int = 400


class FailureSignal(str, Enum):
    """The classified signal that triggered a reframe attempt.

    Enumerated because the prompt template branches on it: an
    ``ZERO_FINDINGS`` failure is broadened one way (the retrieval layer
    got nothing), a ``TIMED_OUT`` failure another (the ladder ran but
    ran out of time), a ``RESEARCH_GAP`` failure a third (the ladder
    completed but every result was rejected by
    :meth:`SubAgentRunner.gap_finding`).
    """

    ZERO_FINDINGS = "zero_findings"
    TIMED_OUT = "timed_out"
    RESEARCH_GAP = "research_gap"
    FAILED = "failed"  # generic terminal failure (exception path)


class BroadenStrategy(str, Enum):
    """The enumerated broadening strategies. See ``_STRATEGY_HINTS``."""

    DROP_GEOGRAPHY = "drop_geography"
    BROADEN_ENTITY = "broaden_entity"
    COMPARATIVE_REFRAME = "comparative_reframe"
    INDUSTRY_SYNONYM = "industry_synonym"
    PERIOD_SHIFT = "period_shift"


_STRATEGY_HINTS: dict[BroadenStrategy, str] = {
    BroadenStrategy.DROP_GEOGRAPHY:
        "restate the question at a regional or global level "
        "when a country-specific frame returned nothing",
    BroadenStrategy.BROADEN_ENTITY:
        "swap a specific named entity for its category "
        "(e.g. 'Company X' → 'incumbent players in Y')",
    BroadenStrategy.COMPARATIVE_REFRAME:
        "reframe as a comparison between the subject and its "
        "closest analogue in a comparable market or period",
    BroadenStrategy.INDUSTRY_SYNONYM:
        "substitute industry vocabulary "
        "(e.g. 'ride-hailing' ↔ 'mobility-as-a-service')",
    BroadenStrategy.PERIOD_SHIFT:
        "shift the time window to an adjacent period "
        "with better data availability (last 5y ↔ last 10y)",
}


# ─────────────────────────────────────────────────────────────────────────
# Schema
# ─────────────────────────────────────────────────────────────────────────


class RephrasedTask(BaseModel):
    """One rephrased task variant plus how it broadens the original."""

    rephrased_question: str = Field(..., min_length=8, max_length=MAX_TASK_LEN)
    broaden_strategy: BroadenStrategy
    rationale: str = Field(default="", max_length=400)
    #: Where the model expects the answer to live (e.g. "industry report",
    #: "government registry", "news archive", "peer-reviewed paper"). Used
    #: by the retrieval path to skew the search stack. Optional; empty
    #: string means "no preference".
    expected_source_type: str = Field(default="", max_length=80)

    @field_validator("rephrased_question")
    @classmethod
    def _question_is_shaped(cls, v: str) -> str:
        q = re.sub(r"\s+", " ", (v or "")).strip()
        if not q:
            raise ValueError("rephrased_question is empty")
        # Must contain at least three word tokens of >=3 chars — one-word
        # or symbol-only "questions" are unusable downstream.
        if len(re.findall(r"[A-Za-z]{3,}", q)) < 3:
            raise ValueError(f"rephrased_question is too thin: {v!r}")
        return q


class ReframeResult(BaseModel):
    """The full reframer output: sub-question plus its variants."""

    sub_question: str
    failure_signal: FailureSignal
    variants: list[RephrasedTask] = Field(default_factory=list)
    #: True when the LLM path did not produce the plan and the
    #: deterministic fallback was used. Callers that log reframer
    #: outcomes should surface this — the audit's whole "silent planner
    #: outage" lesson applies verbatim here.
    degraded: bool = False
    cached: bool = False


# ─────────────────────────────────────────────────────────────────────────
# Cache — keyed by (question_hash, failure_signal)
# ─────────────────────────────────────────────────────────────────────────


def _reframe_hash(
    sub_question: str,
    *,
    failure_signal: FailureSignal,
    subject: str = "",
    geography: str = "",
) -> str:
    """Stable cache key for a reframe attempt."""
    normalized = re.sub(r"\s+", " ", (sub_question or "").strip().lower()).strip(" .?!,;:")
    payload = "\u241f".join(
        (
            normalized,
            failure_signal.value,
            re.sub(r"\s+", " ", (subject or "").strip().lower()),
            re.sub(r"\s+", " ", (geography or "").strip().lower()),
        )
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]


class _ReframeCache:
    """Small thread-safe LRU-ish cache for reframer results."""

    def __init__(self, max_entries: int = 256) -> None:
        self._lock = threading.Lock()
        self._store: dict[str, ReframeResult] = {}
        self._order: list[str] = []
        self._max_entries = max_entries
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> ReframeResult | None:
        with self._lock:
            result = self._store.get(key)
            if result is None:
                self.misses += 1
                return None
            self.hits += 1
            with contextlib.suppress(ValueError):
                self._order.remove(key)
            self._order.append(key)
            return result.model_copy(update={"cached": True})

    def put(self, key: str, result: ReframeResult) -> None:
        with self._lock:
            if key not in self._store and len(self._order) >= self._max_entries:
                oldest = self._order.pop(0)
                self._store.pop(oldest, None)
            self._store[key] = result.model_copy(update={"cached": False})
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


_CACHE = _ReframeCache()


def clear_reframe_cache() -> None:
    """Reset the module-level cache (used by tests and per-engagement teardown)."""
    _CACHE.clear()


def reframe_cache_stats() -> dict[str, int]:
    """Cache counters, for the per-engagement metrics surface."""
    return _CACHE.stats()


# ─────────────────────────────────────────────────────────────────────────
# Prompting
# ─────────────────────────────────────────────────────────────────────────

_SYSTEM_PROMPT = (
    "You are a research strategist at a top-tier management consulting firm. "
    "One of your junior researchers just returned no findings on a task. "
    "Your job: propose up to 3 RE-FRAMED versions of the same research "
    "question that a different retrieval attempt would answer.\n\n"
    "You do NOT answer the question. You do NOT invent facts. You produce "
    "up to 3 rephrasings, each pursuing the same underlying insight from a "
    "different angle.\n\n"
    "For every rephrasing, pick exactly one broaden_strategy from this "
    "vocabulary:\n"
    + "\n".join(
        f"  {s.value:<22} — {hint}" for s, hint in _STRATEGY_HINTS.items()
    )
    + "\n\n"
    "Rules:\n"
    f"1. Produce between 1 and {MAX_VARIANTS} rephrased questions.\n"
    "2. Every rephrased_question must preserve the SAME UNDERLYING INSIGHT "
    "the original was after; it must NOT change the topic.\n"
    "3. Every rephrased_question must be genuinely different from the "
    "original — a synonym swap is not a rephrasing.\n"
    "4. Choose a distinct broaden_strategy for each variant when possible.\n"
    "5. Preserve the subject; drop or broaden geography/period/entities per "
    "the chosen strategy.\n"
    "6. Never invent entities, numbers, or claims not present in the "
    "original question or context.\n\n"
    'Return ONLY a JSON object of the form {"variants": [{'
    '"rephrased_question": "...", "broaden_strategy": "...", '
    '"rationale": "...", "expected_source_type": "..."}]}'
)


def _build_user_prompt(
    sub_question: str,
    *,
    failure_signal: FailureSignal,
    task_description: str = "",
    subject: str = "",
    geography: str = "",
    context: dict[str, Any] | None = None,
) -> str:
    parts = [f"Original sub-question: {sub_question}"]
    if task_description and task_description.strip() != sub_question.strip():
        parts.append(f"Original task description: {task_description}")
    parts.append(
        f"Failure signal from the first attempt: {failure_signal.value}"
    )
    if subject:
        parts.append(f"Engagement subject: {subject}")
    if geography:
        parts.append(f"Engagement geography: {geography}")
    if context:
        ctx_lines = [
            f"  {k}: {v}"
            for k, v in list(context.items())[:8]
            if v not in (None, "", [], {})
        ]
        if ctx_lines:
            parts.append("Known context:\n" + "\n".join(ctx_lines))
    parts.append(
        f"Produce 1-{MAX_VARIANTS} rephrased research questions as JSON."
    )
    return "\n\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────
# Deterministic fallback
# ─────────────────────────────────────────────────────────────────────────


def _deterministic_variants(
    sub_question: str,
    *,
    subject: str = "",
    geography: str = "",
) -> list[RephrasedTask]:
    """Build 1-3 deterministic broadening variants with no LLM call.

    Same knobs the sub-agent already uses in ``_condense_query_variants``:
    (i) drop geography, (ii) substitute an industry synonym, (iii) shift
    period. Never raises. Always returns >= 1 variant so a total LLM
    outage still yields at least one retry attempt.
    """
    base = re.sub(r"\s+", " ", (sub_question or "")).strip()[:MAX_TASK_LEN]
    if not base:
        return []

    variants: list[RephrasedTask] = []
    seen_norms: set[str] = set()

    def _add(text: str, strategy: BroadenStrategy, rationale: str) -> None:
        text = re.sub(r"\s+", " ", text).strip()[:MAX_TASK_LEN]
        if not text:
            return
        norm = _normalize_for_dedup(text)
        if norm in seen_norms:
            return
        try:
            variants.append(RephrasedTask(
                rephrased_question=text,
                broaden_strategy=strategy,
                rationale=rationale,
            ))
        except ValueError:
            return
        seen_norms.add(norm)

    # Variant 1: drop geography if present.
    geo = (geography or "").strip()
    if geo and geo.lower() in base.lower():
        broadened = re.sub(
            re.escape(geo), "", base, flags=re.IGNORECASE
        )
        broadened = re.sub(r"\s+", " ", broadened).strip(" ,.-")
        if broadened and broadened.lower() != base.lower():
            _add(
                broadened,
                BroadenStrategy.DROP_GEOGRAPHY,
                "removed geography anchor after first attempt returned nothing",
            )

    # Variant 2: prepend an industry-context reframe.
    subj = (subject or "").strip()
    if subj:
        reframe = f"How do peer markets to {subj} approach: {base}"
        _add(
            reframe,
            BroadenStrategy.COMPARATIVE_REFRAME,
            "reframed as a peer-market comparative to widen the corpus",
        )

    # Variant 3: broaden with an industry-context lens.
    _add(
        f"industry-level analysis relevant to: {base}",
        BroadenStrategy.INDUSTRY_SYNONYM,
        "escalated from entity-specific to industry-level framing",
    )

    return variants[:MAX_VARIANTS]


def _normalize_for_dedup(text: str) -> str:
    tokens = sorted({
        t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2
    })
    return " ".join(tokens)


# ─────────────────────────────────────────────────────────────────────────
# LLM parsing
# ─────────────────────────────────────────────────────────────────────────


def _parse_llm_variants(content: str) -> list[RephrasedTask]:
    """Parse + schema-validate the reframer LLM's JSON output.

    Never raises. Individually-invalid variants are dropped with a debug
    log rather than sinking the whole result.
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
        logger.warning("task reframer: response was not JSON: %r", raw[:200])
        return []

    if isinstance(data, dict):
        items = data.get("variants") or data.get("rephrasings") or data.get("results") or []
    elif isinstance(data, list):
        items = data
    else:
        items = []

    if not isinstance(items, list):
        logger.warning(
            "task reframer: 'variants' was not a list: %r", str(items)[:120]
        )
        return []

    variants: list[RephrasedTask] = []
    seen_norms: set[str] = set()
    for item in items:
        if isinstance(item, str):
            item = {
                "rephrased_question": item,
                "broaden_strategy": BroadenStrategy.INDUSTRY_SYNONYM.value,
            }
        if not isinstance(item, dict):
            continue
        try:
            variant = RephrasedTask.model_validate(item)
        except Exception as e:  # noqa: BLE001 - each variant is best-effort
            logger.debug(
                "task reframer: dropped invalid variant %r: %s",
                str(item)[:120], e,
            )
            continue
        norm = _normalize_for_dedup(variant.rephrased_question)
        if norm in seen_norms:
            continue
        variants.append(variant)
        seen_norms.add(norm)
        if len(variants) >= MAX_VARIANTS:
            break

    return variants


# ─────────────────────────────────────────────────────────────────────────
# The public entry point
# ─────────────────────────────────────────────────────────────────────────


async def reframe_task(
    sub_question: str,
    *,
    failure_signal: FailureSignal | str,
    router: Any = None,
    task_description: str = "",
    subject: str = "",
    geography: str = "",
    context: dict[str, Any] | None = None,
    use_cache: bool = True,
) -> ReframeResult:
    """Turn one failed task into 1-3 rephrased variants.

    Contract:
      - Runs at ``REFRAMER_TIER`` (FAST). Never escalates.
      - Caches by ``_reframe_hash(sub_question, failure_signal, subject, geography)``.
      - Always returns >= 1 variant unless ``sub_question`` is empty. Never
        raises.
      - Sets ``degraded=True`` when the LLM path did not produce the
        result, so a silent reframer outage is visible rather than
        looking like success.

    Args:
        sub_question: The original question the sub-agent failed to
            answer. Used verbatim as the LLM input and as the cache key
            after normalization.
        failure_signal: What kind of failure triggered the reframe (0
            findings / timeout / research_gap / generic FAILED). Accepts
            the enum or its string value.
        router: An ``LLMRouter``-like object. When omitted, the process
            router is used. Pass ``None`` plus ``use_cache=False`` in
            tests to exercise the deterministic path.
    """
    if isinstance(failure_signal, str):
        try:
            failure_signal = FailureSignal(failure_signal)
        except ValueError:
            failure_signal = FailureSignal.FAILED

    question = re.sub(r"\s+", " ", (sub_question or "")).strip()
    if not question:
        return ReframeResult(
            sub_question="", failure_signal=failure_signal,
            variants=[], degraded=True,
        )

    key = _reframe_hash(
        question,
        failure_signal=failure_signal,
        subject=subject,
        geography=geography,
    )
    if use_cache:
        cached = _CACHE.get(key)
        if cached is not None:
            logger.debug(
                "task reframer: cache hit for %r (%d variants)",
                question[:80], len(cached.variants),
            )
            return cached

    if router is None:
        try:
            from hyperion.router.router import get_router

            router = get_router()
        except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning(
                "task reframer: no router available (%s); using deterministic path", e
            )
            router = None

    variants: list[RephrasedTask] = []
    degraded = True

    if router is not None:
        try:
            response = await router.complete(
                tier=REFRAMER_TIER,
                messages=[
                    {"role": "system", "content": compose_agent_prompt(_SYSTEM_PROMPT)},
                    {
                        "role": "user",
                        "content": _build_user_prompt(
                            question,
                            failure_signal=failure_signal,
                            task_description=task_description,
                            subject=subject,
                            geography=geography,
                            context=context,
                        ),
                    },
                ],
                agent_name="task_reframer",
                urgency=TaskUrgency.LOW,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            if getattr(response, "success", False) and getattr(response, "content", ""):
                variants = _parse_llm_variants(response.content)
                degraded = not variants
                if not variants:
                    logger.warning(
                        "task reframer: LLM returned no usable variants for %r; "
                        "falling back to deterministic path",
                        question[:100],
                    )
            else:
                logger.warning(
                    "task reframer: LLM call unsuccessful for %r (error=%r); "
                    "falling back to deterministic path",
                    question[:100],
                    getattr(response, "error", ""),
                )
        except Exception as e:
            logger.warning(
                "task reframer: LLM path failed for %r: %s",
                question[:100], e, exc_info=True,
            )

    if not variants:
        variants = _deterministic_variants(
            question, subject=subject, geography=geography
        )
        degraded = True

    result = ReframeResult(
        sub_question=question,
        failure_signal=failure_signal,
        variants=variants[:MAX_VARIANTS],
        degraded=degraded,
    )

    logger.info(
        "task reframer: %d variant(s) for %r (signal=%s, degraded=%s)",
        len(result.variants),
        question[:80],
        failure_signal.value,
        result.degraded,
    )

    if use_cache and result.variants:
        _CACHE.put(key, result)

    return result


__all__ = [
    "MAX_TASK_LEN",
    "MAX_VARIANTS",
    "REFRAMER_TIER",
    "BroadenStrategy",
    "FailureSignal",
    "RephrasedTask",
    "ReframeResult",
    "clear_reframe_cache",
    "reframe_cache_stats",
    "reframe_task",
]
