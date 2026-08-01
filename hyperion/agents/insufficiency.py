"""W-07: Evidence insufficiency is a decision with four outcomes.

This module replaces terminal filler prose ("Insufficient evidence to state
implications", "Confidence: low") with an escalation ladder that ends in
either evidence or an explicit, single, well-placed declaration of scope
limits.

The four outcomes (PART 1 §W-07 step 1):

- ``RETRY_STRATEGY``: same question, different query construction. Retrieval
  continues; nothing is written to the report.
- ``RETRY_SCOPE``: broadened entity, period, or geography. Retrieval
  continues; the scope change is recorded.
- ``OUT_OF_SCOPE``: the sub-question is not answerable for this subject.
  The section is REMOVED from the report (no heading, no placeholder, no
  TOC entry) and one consolidated line is added to the scope note.
- ``DECLARED_GAP``: answerable, genuinely under-documented. The section is
  retained and the gap is stated specifically: the question, the strategies
  attempted, and what source would resolve it.

Strategy triples (PART 1 §W-07 step 2): every retry is a concrete
``(query_form, engine_set, window, locale)`` quadruple, and the ladder never
repeats a triple that already returned zero. The triple log is the durable
artifact that makes "try again with different settings" observable rather
than a loop that looks identical in the logs.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class InsufficiencyOutcome(str, Enum):
    """The four terminal outcomes of an insufficiency event (W-07 step 1)."""

    RETRY_STRATEGY = "retry_strategy"
    RETRY_SCOPE = "retry_scope"
    OUT_OF_SCOPE = "out_of_scope"
    DECLARED_GAP = "declared_gap"


class QueryForm(str, Enum):
    """Concrete query constructions a RETRY_STRATEGY may use (W-07 step 2)."""

    NATURAL_QUESTION = "natural_question"
    KEYWORD_CONJUNCTION = "keyword_conjunction"
    EXACT_PHRASE = "exact_phrase"
    ENTITY_METRIC = "entity_metric"
    SITE_SCOPED = "site_scoped"


class EngineSet(str, Enum):
    """Engine pools a retry may route to.

    Values mirror the pools registered on ``SearXNGClient`` so a triple
    never names an engine set the retrieval layer cannot serve (the W-11
    reconciliation keeps these honest — see ``tests/test_w11_engine_registry``).
    """

    RELIABLE = "reliable"      # bing,duckduckgo,brave,mojeek,startpage,qwant
    STANDBY = "standby"        # google,ecosia,swisscows
    CATEGORY_SCIENCE = "category_science"
    CATEGORY_NEWS = "category_news"
    CATEGORY_IT = "category_it"


class TimeWindow(str, Enum):
    """Time windows a retry may apply (W-07 step 2)."""

    UNBOUNDED = "unbounded"
    LAST_3_YEARS = "last_3_years"
    LAST_10_YEARS = "last_10_years"
    SPECIFIC_YEAR = "specific_year"


class StrategyTriple(BaseModel):
    """One concrete retrieval attempt: what was asked, where, and when.

    A triple is the unit of non-repetition: the ladder refuses to issue a
    triple identical to one that already returned zero. ``locale`` is part
    of the identity because a locale variation is a legitimately different
    retrieval for a non-Anglophone subject.
    """

    query_form: QueryForm
    engine_set: EngineSet
    window: TimeWindow = TimeWindow.UNBOUNDED
    locale: str = "en"

    def identity(self) -> tuple[str, str, str, str]:
        return (
            self.query_form.value,
            self.engine_set.value,
            self.window.value,
            self.locale,
        )

    def describe(self) -> str:
        return (
            f"{self.query_form.value} via {self.engine_set.value} "
            f"({self.window.value}, locale={self.locale})"
        )


# The canonical ladder sequence (W-07 step 3): up to 3 RETRY_STRATEGY, each
# of which MUST change something specific, then up to 2 RETRY_SCOPE.
# Each entry is the triple the round uses plus the query-construction hint
# the dispatch text embeds. Every triple in the plan is distinct, so the
# non-repetition invariant holds by construction for a single pass; the
# runtime check additionally guards against a caller-supplied history.
STRATEGY_LADDER: tuple[StrategyTriple, ...] = (
    StrategyTriple(
        query_form=QueryForm.KEYWORD_CONJUNCTION,
        engine_set=EngineSet.RELIABLE,
        window=TimeWindow.UNBOUNDED,
    ),
    StrategyTriple(
        query_form=QueryForm.ENTITY_METRIC,
        engine_set=EngineSet.RELIABLE,
        window=TimeWindow.LAST_3_YEARS,
    ),
    StrategyTriple(
        query_form=QueryForm.EXACT_PHRASE,
        engine_set=EngineSet.STANDBY,
        window=TimeWindow.UNBOUNDED,
    ),
)

# RETRY_SCOPE rounds broaden entity/period/geography. The triple changes
# observably (window widens, engine set rotates to a category route).
SCOPE_LADDER: tuple[StrategyTriple, ...] = (
    StrategyTriple(
        query_form=QueryForm.NATURAL_QUESTION,
        engine_set=EngineSet.CATEGORY_NEWS,
        window=TimeWindow.LAST_10_YEARS,
    ),
    StrategyTriple(
        query_form=QueryForm.SITE_SCOPED,
        engine_set=EngineSet.CATEGORY_SCIENCE,
        window=TimeWindow.UNBOUNDED,
    ),
)

MAX_STRATEGY_RETRIES = len(STRATEGY_LADDER)  # 3
MAX_SCOPE_RETRIES = len(SCOPE_LADDER)        # 2


class InsufficiencyResolution(BaseModel):
    """The durable record of how one insufficiency event resolved (W-07)."""

    gap_id: str
    question: str
    section_id: str
    outcome: InsufficiencyOutcome
    tried_triples: list[StrategyTriple] = Field(default_factory=list)
    justification: str = Field(
        default="",
        description="One sentence, retained, justifying OUT_OF_SCOPE or DECLARED_GAP",
    )
    scope_change: str | None = Field(
        default=None,
        description="What broadened, when outcome was RETRY_SCOPE",
    )

    def declared_gap_statement(self) -> str:
        """The specific declaration a DECLARED_GAP writes (W-07 step 5).

        States the question, the strategies attempted, and what source would
        resolve it. The banned filler phrasings are structurally
        unconstructible here: there is no code path that emits
        "Insufficient evidence" or "requires additional research".
        """
        tried = (
            "; ".join(t.describe() for t in self.tried_triples)
            if self.tried_triples
            else "no retrieval strategies completed"
        )
        return (
            f"Declared research gap in section '{self.section_id}': "
            f"{self.question} — strategies attempted: {tried}. "
            f"A primary source naming this entity and period directly "
            f"(regulator filing, audited disclosure, or official statistical "
            f"release) would resolve it."
        )


class InsufficiencyLadder:
    """Drives one gap through the W-07 budget: 3 RETRY_STRATEGY, then 2
    RETRY_SCOPE, then classification.

    The ladder itself performs no I/O. The orchestrator hands each planned
    round to a live agent; the ladder records the triple and the outcome so
    the plan is enumerable and non-repetition is enforceable.
    """

    def __init__(self, gap_id: str, question: str, section_id: str) -> None:
        self.resolution = InsufficiencyResolution(
            gap_id=gap_id, question=question, section_id=section_id,
            outcome=InsufficiencyOutcome.RETRY_STRATEGY,
        )
        self._zero_triples: set[tuple[str, str, str, str]] = set()
        self._strategy_round = 0
        self._scope_round = 0

    @property
    def tried_triples(self) -> list[StrategyTriple]:
        return self.resolution.tried_triples

    def next_strategy_round(self) -> StrategyTriple | None:
        """The next RETRY_STRATEGY triple, or None when the budget is spent.

        Never returns a triple identical to one that already returned zero.
        """
        while self._strategy_round < MAX_STRATEGY_RETRIES:
            triple = STRATEGY_LADDER[self._strategy_round]
            self._strategy_round += 1
            if triple.identity() not in self._zero_triples:
                return triple
        return None

    def next_scope_round(self) -> tuple[StrategyTriple, str] | None:
        """The next RETRY_SCOPE triple plus what broadened, or None."""
        broadenings = (
            "period broadened to last 10 years",
            "entity broadened to the parent market and geography lifted",
        )
        while self._scope_round < MAX_SCOPE_RETRIES:
            idx = self._scope_round
            triple = SCOPE_LADDER[self._scope_round]
            self._scope_round += 1
            if triple.identity() not in self._zero_triples:
                return triple, broadenings[idx]
        return None

    def record_attempt(self, triple: StrategyTriple, produced_evidence: bool) -> None:
        """Record one executed attempt.

        A zero-evidence attempt goes on the non-repetition blocklist; every
        attempt goes on the durable tried-triples log.
        """
        self.resolution.tried_triples.append(triple)
        if not produced_evidence:
            self._zero_triples.add(triple.identity())

    def budget_exhausted(self) -> bool:
        return (
            self._strategy_round >= MAX_STRATEGY_RETRIES
            and self._scope_round >= MAX_SCOPE_RETRIES
        )


def classify_gap(
    question: str,
    section_id: str,
    engagement_context: dict[str, Any] | None,
    tried_triples: list[StrategyTriple],
) -> tuple[InsufficiencyOutcome, str]:
    """Decide OUT_OF_SCOPE vs DECLARED_GAP with a retained justification.

    W-07 step 3: the classification is a judgement made with the
    tried-triples log in context, justified in one sentence that is kept.
    This deterministic core implements the judgement; an LLM refinement may
    wrap it, but the decision must remain reproducible offline.

    OUT_OF_SCOPE is reserved for subject-class mismatch (W-07 failure
    modes): the sub-question presupposes an entity class, geography, or
    period the engagement's subject does not have. DECLARED_GAP is the
    honest answer for a genuinely thin topic.
    """
    ctx = engagement_context or {}
    subject = str(ctx.get("subject", "") or "").lower()
    geographies = [str(g).lower() for g in (ctx.get("geographies") or [])]
    jurisdictions = [str(j).lower() for j in (ctx.get("jurisdictions") or [])]
    q = question.lower()

    # Subject-class mismatch signals: the question presupposes an entity or
    # market structure the engagement's subject does not have.
    _FIRM_LEVEL = (
        "valuation", "share price", "ticker", "earnings per share",
        "consumer survey", "brand perception", "nps", "churn rate",
        "customer interview",
    )
    _POLICY_SUBJECTS = (
        "policy", "regulation", "national", "government", "legislation",
        "public sector", "geopolitic", "treaty", "sanction",
    )
    subject_is_policy = any(s in subject for s in _POLICY_SUBJECTS)
    firm_level_asked = any(k in q for k in _FIRM_LEVEL)
    if subject_is_policy and firm_level_asked:
        return (
            InsufficiencyOutcome.OUT_OF_SCOPE,
            (
                f"The question asks for firm-level or consumer evidence, but "
                f"the engagement subject ({subject or 'unspecified'}) is a "
                f"policy-level question, so the sub-question is unanswerable "
                f"for this subject."
            ),
        )

    # Geography mismatch: the question names a jurisdiction outside every
    # geography the engagement scoped to.
    if geographies and jurisdictions:
        mentions_foreign = any(
            j in q and not any(j in g or g in j for g in geographies)
            for j in jurisdictions
        )
        if mentions_foreign:
            return (
                InsufficiencyOutcome.OUT_OF_SCOPE,
                (
                    f"The question names a jurisdiction outside the "
                    f"engagement geographies ({', '.join(geographies)}), so "
                    f"it is out of scope for this subject."
                ),
            )

    tried = len(tried_triples)
    return (
        InsufficiencyOutcome.DECLARED_GAP,
        (
            f"The question is answerable in principle but the public record "
            f"is thin: {tried} distinct retrieval strategies "
            f"(query form, engine set, time window) returned no usable "
            f"evidence, so the gap is declared rather than filled."
        ),
    )


def suppress_out_of_scope_sections(
    report: Any,
    resolutions: list[InsufficiencyResolution],
) -> list[str]:
    """Remove OUT_OF_SCOPE sections from the report entirely (W-07 step 4).

    No heading, no placeholder, no TOC entry — W-03 derives the TOC from
    the document, so removing the section removes the entry automatically.
    Returns the consolidated scope-note lines (one per suppression), which
    the caller places in a single visible scope statement.
    """
    removed: list[str] = []
    sections = getattr(report, "sections", None)
    if sections is None:
        return removed
    out_of_scope = [r for r in resolutions if r.outcome == InsufficiencyOutcome.OUT_OF_SCOPE]
    if not out_of_scope:
        return removed
    for resolution in out_of_scope:
        before = len(sections)
        sections[:] = [
            s for s in sections
            if getattr(s, "id", None) != resolution.section_id
            and getattr(s, "agent", "") != resolution.section_id
        ]
        if len(sections) < before or True:
            removed.append(
                f"This engagement does not include '{resolution.section_id}': "
                f"{resolution.justification}"
            )
    return removed
