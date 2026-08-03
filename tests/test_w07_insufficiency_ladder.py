"""W-07 — Evidence insufficiency is a decision with four outcomes.

Verifies the acceptance criteria from HYPERION_DEEP_AUDIT_2026-07-31.md §W-07:

1. All four outcomes (RETRY_STRATEGY, RETRY_SCOPE, OUT_OF_SCOPE,
   DECLARED_GAP) are reachable.
2. The ladder never retries an already-zero (query_form, engine_set,
   window, locale) quadruple; every retried sub-question has a logged list
   of distinct strategy triples.
3. At least one strategy escalation observably changes the engine set or
   time window.
4. OUT_OF_SCOPE sections are absent from the document (and therefore from
   the W-03-derived TOC); the scope note carries one consolidated line.
5. DECLARED_GAP statements name the question, the strategies attempted,
   and what source would resolve it; the banned filler phrasings are
   unconstructible and render-banned.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from hyperion.agents.insufficiency import (
    SCOPE_LADDER,
    STRATEGY_LADDER,
    EngineSet,
    InsufficiencyLadder,
    InsufficiencyOutcome,
    QueryForm,
    StrategyTriple,
    classify_gap,
    suppress_out_of_scope_sections,
)
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    FinalReport,
    Recommendation,
)

# ─────────────────────────────────────────────────────────────────────────────
# 1. Four-outcome coverage
# ─────────────────────────────────────────────────────────────────────────────


class TestFourOutcomes:
    def test_all_four_outcomes_reachable(self):
        """RETRY_STRATEGY and RETRY_SCOPE are ladder phases; OUT_OF_SCOPE and
        DECLARED_GAP are the two terminal classifications."""
        # RETRY phases: the ladders produce rounds.
        ladder = InsufficiencyLadder("g1", "q?", "sec")
        assert ladder.next_strategy_round() is not None  # RETRY_STRATEGY
        for t in STRATEGY_LADDER:
            ladder.record_attempt(t, produced_evidence=False)
        assert ladder.next_strategy_round() is None  # strategy budget spent
        scope = ladder.next_scope_round()
        assert scope is not None  # RETRY_SCOPE
        _, change = scope
        assert change  # scope change recorded

        # Terminal classifications.
        oos, _ = classify_gap(
            "What is the firm-level valuation multiple?",
            "sec_valuation",
            {"subject": "national AI policy"},
            list(STRATEGY_LADDER),
        )
        assert oos == InsufficiencyOutcome.OUT_OF_SCOPE

        dg, _ = classify_gap(
            "What is the adoption rate of the policy?",
            "sec_adoption",
            {"subject": "national AI policy"},
            list(STRATEGY_LADDER),
        )
        assert dg == InsufficiencyOutcome.DECLARED_GAP

    def test_out_of_scope_reserved_for_subject_class_mismatch(self):
        """W-07 failure mode: OUT_OF_SCOPE is not a convenience for anything
        hard — a thin but on-subject question classifies as DECLARED_GAP."""
        outcome, justification = classify_gap(
            "How many enterprises adopted the framework last year?",
            "sec_adoption",
            {"subject": "national AI policy", "geographies": ["germany"]},
            list(STRATEGY_LADDER),
        )
        assert outcome == InsufficiencyOutcome.DECLARED_GAP
        assert "thin" in justification.lower()

    def test_geography_mismatch_classifies_out_of_scope(self):
        outcome, _ = classify_gap(
            "What does the brazil regulator require?",
            "sec_regulatory",
            {
                "subject": "national AI policy",
                "geographies": ["germany"],
                "jurisdictions": ["germany", "brazil"],
            },
            list(STRATEGY_LADDER),
        )
        assert outcome == InsufficiencyOutcome.OUT_OF_SCOPE


# ─────────────────────────────────────────────────────────────────────────────
# 2. Strategy non-repetition and observable escalation
# ─────────────────────────────────────────────────────────────────────────────


class TestStrategyTriples:
    def test_ladder_never_repeats_a_zero_triple(self):
        ladder = InsufficiencyLadder("g1", "q?", "sec")
        seen: set[tuple[str, str, str, str]] = set()
        while True:
            triple = ladder.next_strategy_round()
            if triple is None:
                break
            assert triple.identity() not in seen, "repeated a zero triple"
            seen.add(triple.identity())
            ladder.record_attempt(triple, produced_evidence=False)
        assert len(seen) == 3, "3 distinct strategy triples tried"

    def test_scope_rounds_also_non_repeating(self):
        ladder = InsufficiencyLadder("g1", "q?", "sec")
        for t in STRATEGY_LADDER:
            ladder.record_attempt(t, produced_evidence=False)
        seen: set[tuple[str, str, str, str]] = set()
        while True:
            planned = ladder.next_scope_round()
            if planned is None:
                break
            triple, _ = planned
            assert triple.identity() not in seen
            seen.add(triple.identity())
            ladder.record_attempt(triple, produced_evidence=False)
        assert len(seen) == 2, "2 distinct scope triples tried"

    def test_strategy_plan_is_distinct_by_construction(self):
        identities = [t.identity() for t in STRATEGY_LADDER + SCOPE_LADDER]
        assert len(set(identities)) == len(identities)

    def test_escalation_changes_engine_set_and_window(self):
        """W-07 acceptance: at least one escalation observably changes the
        engine set or time window. The ladder changes BOTH across rounds."""
        first, *rest = STRATEGY_LADDER
        assert any(
            t.engine_set != first.engine_set or t.window != first.window
            for t in rest
        )
        # And scope rounds change them again relative to the strategy phase.
        assert all(
            t.engine_set != first.engine_set or t.window != first.window
            for t in SCOPE_LADDER
        )

    def test_locale_is_part_of_triple_identity(self):
        a = StrategyTriple(
            query_form=QueryForm.KEYWORD_CONJUNCTION,
            engine_set=EngineSet.RELIABLE,
            locale="en",
        )
        b = StrategyTriple(
            query_form=QueryForm.KEYWORD_CONJUNCTION,
            engine_set=EngineSet.RELIABLE,
            locale="de",
        )
        assert a.identity() != b.identity()

    def test_engine_sets_map_to_registered_pools(self):
        """The triple's engine sets must be servable by the retrieval layer
        (no dead category routes — the W-11 failure mode)."""
        from hyperion.tools.searxng import SearxNGClient

        reliable = set(SearxNGClient.RELIABLE_ENGINES.split(","))
        standby = set(SearxNGClient.STANDBY_ENGINES.split(","))
        assert reliable and standby
        assert reliable.isdisjoint(standby), "pools must be disjoint"
        categories = set(SearxNGClient.CATEGORY_ENGINES.keys())
        # Every category route the ladder names must exist.
        assert {"science", "news", "it"} <= categories


# ─────────────────────────────────────────────────────────────────────────────
# 3. Section suppression and scope note
# ─────────────────────────────────────────────────────────────────────────────


def _report_with_sections() -> FinalReport:
    return FinalReport(
        engagement_id="ENG-W07",
        question="Should the state adopt the framework?",
        recommendation=Recommendation.ENTER,
        recommendation_rationale="Evidence supports adoption.",
        critical_assumptions=["Funding holds."],
        confidence=ConfidenceLevel.MEDIUM,
        confidence_breakdown={"policy": ConfidenceLevel.MEDIUM},
        executive_summary="Adopt the framework.",
        sections=[
            AnalysisSection(
                id="policy_landscape",
                title="Policy Landscape",
                agent="regulatory_analyst",
                key_insight="Framework is enforceable.",
                body="The framework sets binding targets.",
                confidence=ConfidenceLevel.MEDIUM,
            ),
            AnalysisSection(
                id="firm_valuation",
                title="Firm Valuation",
                agent="financial_analyst",
                key_insight="n/a",
                body="Out of scope for a national policy question.",
                confidence=ConfidenceLevel.LOW,
            ),
        ],
    )


class TestSectionSuppression:
    def _resolution(self, section_id: str, outcome: InsufficiencyOutcome) -> object:
        from hyperion.agents.insufficiency import InsufficiencyResolution

        return InsufficiencyResolution(
            gap_id="g1",
            question="What is the firm-level valuation multiple?",
            section_id=section_id,
            outcome=outcome,
            tried_triples=list(STRATEGY_LADDER),
            justification=(
                "The question asks for firm-level evidence, but the subject "
                "is a national policy question."
            ),
        )

    def test_out_of_scope_section_removed_entirely(self):
        report = _report_with_sections()
        resolutions = [self._resolution("firm_valuation", InsufficiencyOutcome.OUT_OF_SCOPE)]
        scope_lines = suppress_out_of_scope_sections(report, resolutions)

        remaining_ids = [s.id for s in report.sections]
        assert "firm_valuation" not in remaining_ids, "no heading survives"
        assert "policy_landscape" in remaining_ids, "other sections untouched"
        # One consolidated scope-note line, not N filler occurrences.
        assert len(scope_lines) == 1
        assert "firm_valuation" in scope_lines[0]
        assert "does not include" in scope_lines[0]

    def test_declared_gap_section_retained(self):
        report = _report_with_sections()
        resolutions = [self._resolution("firm_valuation", InsufficiencyOutcome.DECLARED_GAP)]
        scope_lines = suppress_out_of_scope_sections(report, resolutions)

        assert "firm_valuation" in [s.id for s in report.sections]
        assert scope_lines == []

    def test_no_resolutions_is_a_no_op(self):
        report = _report_with_sections()
        assert suppress_out_of_scope_sections(report, []) == []
        assert len(report.sections) == 2


# ─────────────────────────────────────────────────────────────────────────────
# 4. Specific declared gaps; banned filler unconstructible
# ─────────────────────────────────────────────────────────────────────────────


class TestDeclaredGapSpecificity:
    def test_declared_gap_names_question_strategies_and_resolution(self):
        from hyperion.agents.insufficiency import InsufficiencyResolution

        resolution = InsufficiencyResolution(
            gap_id="g1",
            question="What is the sector-level compliance cost?",
            section_id="cost_analysis",
            outcome=InsufficiencyOutcome.DECLARED_GAP,
            tried_triples=list(STRATEGY_LADDER),
            justification="thin public record",
        )
        statement = resolution.declared_gap_statement()

        # Question, strategies attempted, and what would resolve it.
        assert "What is the sector-level compliance cost?" in statement
        assert "keyword_conjunction" in statement
        assert "entity_metric" in statement
        assert "would resolve it" in statement
        # The banned filler phrasings are unconstructible.
        for banned in (
            "Insufficient evidence",
            "requires additional research",
            "Confidence: low",
        ):
            assert banned not in statement

    def test_banned_substrings_cover_w07_fillers(self):
        from hyperion.output.page_audit import BANNED_SUBSTRINGS

        joined = " ".join(BANNED_SUBSTRINGS).lower()
        assert "insufficient evidence" in joined
        assert "requires additional research" in joined
        assert "confidence: low" in joined


# ─────────────────────────────────────────────────────────────────────────────
# 5. End-to-end ladder through the orchestrator phase
# ─────────────────────────────────────────────────────────────────────────────


class TestOrchestratorLadder:
    def _orch(self, agents: dict):
        from hyperion.orchestrator import WorkflowEngine

        orch = WorkflowEngine.__new__(WorkflowEngine)
        orch._agents = agents
        orch._engagement_id = "e1"
        orch._engagement_context = {"subject": "national AI policy"}
        orch._get_agent = lambda name: orch._agents[name]  # type: ignore[method-assign]
        return orch

    def _dag(self):
        from hyperion.config import ModelTier
        from hyperion.schemas.agents import AgentName
        from hyperion.schemas.workflow import (
            QuestionType,
            TaskNode,
            TaskStatus,
            WorkflowDAG,
        )

        task = TaskNode(
            id="task_market_analyst",
            agent=AgentName.MARKET_ANALYST,
            model_tier=ModelTier.STANDARD,
            description="specialist",
        )
        task.status = TaskStatus.AWAITING_FOLLOWUP
        return WorkflowDAG(
            engagement_id="e1", question="q", question_type=QuestionType.GENERAL,
            tasks=[task], estimated_total_llm_calls=1,
            estimated_total_tokens=100, estimated_duration_minutes=1.0,
        )

    def test_gap_resolved_on_second_strategy_round(self):
        from hyperion.schemas.agents import AgentName
        from hyperion.schemas.models import AnalysisGap

        origin = AsyncMock()
        # First strategy round fails, second produces evidence.
        origin.run = AsyncMock(side_effect=[None, {"finding": "found it"}])
        orch = self._orch({AgentName.MARKET_ANALYST: origin})
        dag = self._dag()
        gap = AnalysisGap(
            id="g1", section_id="policy_landscape",
            agent=AgentName.MARKET_ANALYST, field="body",
            question="What is the adoption rate?",
        )

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))

        assert gap.resolved is True
        assert gap.attempts == 2
        assert origin.run.await_count == 2
        # The second dispatch embeds a different strategy description.
        second_kwargs = origin.run.await_args.kwargs
        assert "entity_metric" in str(second_kwargs) or "last_3_years" in str(second_kwargs)

    def test_unresolvable_gap_records_resolution_with_log(self):
        from hyperion.schemas.agents import AgentName
        from hyperion.schemas.models import AnalysisGap

        origin = AsyncMock()
        origin.run = AsyncMock(return_value=None)
        other = AsyncMock()
        other.run = AsyncMock(return_value=None)
        orch = self._orch({
            AgentName.MARKET_ANALYST: origin,
            AgentName.RISK_ANALYST: other,
        })
        dag = self._dag()
        gap = AnalysisGap(
            id="g1", section_id="firm_valuation",
            agent=AgentName.MARKET_ANALYST, field="body",
            question="What is the firm-level valuation multiple?",
        )

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))

        assert gap.resolved is False
        resolutions = orch._insufficiency_resolutions
        assert len(resolutions) == 1
        assert resolutions[0].outcome == InsufficiencyOutcome.OUT_OF_SCOPE
        assert len(resolutions[0].tried_triples) == 5

    def test_record_unresolved_gaps_suppresses_and_writes_scope_note(self):
        from hyperion.schemas.agents import AgentName
        from hyperion.schemas.models import AnalysisGap

        origin = AsyncMock()
        origin.run = AsyncMock(return_value=None)
        orch = self._orch({AgentName.MARKET_ANALYST: origin})
        dag = self._dag()
        report = _report_with_sections()
        gap = AnalysisGap(
            id="g1", section_id="firm_valuation",
            agent=AgentName.MARKET_ANALYST, field="body",
            question="What is the firm-level valuation multiple?",
        )

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))
        orch._record_unresolved_gaps(report, [gap])

        # Section suppressed; one consolidated scope-note line; no filler.
        assert "firm_valuation" not in [s.id for s in report.sections]
        scope_lines = [lim for lim in report.limitations if "does not include" in lim]
        assert len(scope_lines) == 1
        for banned in ("Insufficient evidence", "requires additional research", "Confidence: low"):
            assert all(banned not in lim for lim in report.limitations)
