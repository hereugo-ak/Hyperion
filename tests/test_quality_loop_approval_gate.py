"""P2-22 / P2-G14 — the quality iteration loop must exit on `approved`,
never on the weighted `total_score` alone.

Part 2 audit finding: `orchestrator.py:1113` broke the loop on
`current_score.total_score >= 4.0` and never read `current_score.approved`.
`QualityScore.approved` already folds in the score threshold AND the Layer 4
hard-blocker scan (leaked objects, banned filler, verdict contradictions,
dishonest confidence) — see `quality_gate.py::_detect_hard_blockers`, which
sets `approved = False` even when the weighted score clears 4.0. Reading the
wrong field is why two unshippable PDFs were produced and delivered.

This test builds exactly that pathological QualityScore — total_score=4.5,
approved=False (a hard blocker fired) — and asserts the orchestrator's loop
does NOT stop there; it must keep iterating (or exhaust) rather than treat a
high score with `approved=False` as done.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from hyperion.orchestrator import WorkflowEngine
from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import (
    ConfidenceLevel,
    FinalReport,
    QualityDimension,
    QualityDimensionName,
    QualityScore,
    Recommendation,
)
from hyperion.schemas.workflow import QuestionType, TaskNode, TaskStatus, WorkflowDAG
from hyperion.config import ModelTier


def _minimal_report(sources: int = 12) -> FinalReport:
    return FinalReport(
        engagement_id="ENG-TEST",
        question="Should Acme enter the market?",
        recommendation=Recommendation.ENTER,
        recommendation_rationale="Evidence supports entry.",
        critical_assumptions=["Prices stay flat."],
        confidence=ConfidenceLevel.MEDIUM,
        confidence_breakdown={"market": ConfidenceLevel.MEDIUM},
        executive_summary="Enter the market given favorable conditions.",
        total_sources=sources,
    )


def _score_with_integrity_blocker(total_score: float = 4.5) -> QualityScore:
    """The pathological case from the audit: score clears the 4.0 threshold
    but a Layer 4 hard blocker fired, so `approved` is False.
    """
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id=QualityDimensionName.TONE_AND_VOICE,
                name="Tone and Voice",
                score=5,
                weight=0.1,
                feedback="Fine.",
            )
        ],
        total_score=total_score,
        approved=False,  # a hard blocker (leaked object / banned filler) fired
        iteration=1,
        gaps=["LEAK: a raw Python object/dict ({'...) reached the report body"],
        max_iterations_reached=False,
    )


def _approved_score(total_score: float = 4.5) -> QualityScore:
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id=QualityDimensionName.TONE_AND_VOICE,
                name="Tone and Voice",
                score=5,
                weight=0.1,
                feedback="Fine.",
            )
        ],
        total_score=total_score,
        approved=True,
        iteration=1,
    )


def _make_dag(sources: int = 12) -> WorkflowDAG:
    return WorkflowDAG(
        engagement_id="ENG-TEST",
        question="Should Acme enter the market?",
        question_type=QuestionType.GO_NO_GO,
        tasks=[
            TaskNode(
                id="task_quality_gate",
                agent=AgentName.QUALITY_GATE,
                model_tier=ModelTier.STANDARD,
                description="score",
                status=TaskStatus.PENDING,
            )
        ],
        estimated_total_llm_calls=1,
        estimated_total_tokens=1,
        estimated_duration_minutes=1.0,
    )


class TestQualityLoopReadsApproved:
    @pytest.mark.asyncio
    async def test_high_score_with_blocker_does_not_exit_loop_early(self):
        """total_score=4.5 with approved=False (integrity blocker) must NOT
        cause the loop to stop at iteration 1. It should run through all
        MAX_QUALITY_ITERATIONS attempting fixes, and never emit an approved
        result, because the score was never truly approved.
        """
        engine = WorkflowEngine(bus=MagicMock())

        quality_agent = MagicMock()
        # Every iteration returns the same pathological score: score is high,
        # approved is False. If the orchestrator (incorrectly) reads
        # total_score, it will break after iteration 1. If it correctly
        # reads `approved`, it must keep iterating until max iterations.
        quality_agent.run = AsyncMock(side_effect=lambda **kw: _score_with_integrity_blocker())

        synthesis_agent = MagicMock()
        synthesis_agent.iterate_on_quality = AsyncMock(return_value=_minimal_report())

        def _get_agent(name):
            if name == AgentName.QUALITY_GATE:
                return quality_agent
            if name == AgentName.SYNTHESIS_LEAD:
                return synthesis_agent
            raise AssertionError(f"unexpected agent requested: {name}")

        engine._get_agent = _get_agent  # type: ignore[assignment]

        dag = _make_dag()
        report, score, iterations = await engine._quality_iteration_loop(
            dag, _minimal_report(), None
        )

        # The loop must have run to its iteration cap — it must NOT have
        # broken out early because total_score looked fine.
        assert iterations == engine.MAX_QUALITY_ITERATIONS
        assert score.approved is False
        # quality_agent.run must have been called MAX_QUALITY_ITERATIONS times,
        # proving the loop did not exit after the first high-but-unapproved
        # score.
        assert quality_agent.run.await_count == engine.MAX_QUALITY_ITERATIONS

    @pytest.mark.asyncio
    async def test_approved_score_exits_immediately(self):
        """Sanity check: a genuinely approved score DOES stop the loop at
        iteration 1, so the fix isn't just "always iterate to the cap".
        """
        engine = WorkflowEngine(bus=MagicMock())

        quality_agent = MagicMock()
        quality_agent.run = AsyncMock(return_value=_approved_score())

        synthesis_agent = MagicMock()
        synthesis_agent.iterate_on_quality = AsyncMock(return_value=_minimal_report())

        def _get_agent(name):
            if name == AgentName.QUALITY_GATE:
                return quality_agent
            if name == AgentName.SYNTHESIS_LEAD:
                return synthesis_agent
            raise AssertionError(f"unexpected agent requested: {name}")

        engine._get_agent = _get_agent  # type: ignore[assignment]

        dag = _make_dag()
        report, score, iterations = await engine._quality_iteration_loop(
            dag, _minimal_report(), None
        )

        assert iterations == 1
        assert score.approved is True
        assert quality_agent.run.await_count == 1
