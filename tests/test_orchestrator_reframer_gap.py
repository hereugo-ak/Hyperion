"""
Regression tests for the L3 reframer gap detection (resilience fix).

Before this fix, a specialist whose sub-agents all timed out / returned
nothing would still come back with a single ``research_gap`` finding. That
finding was treated as "1 finding" by the parent and by the orchestrator's
``_task_needs_reframe``, so the reframer (which only inspects specialist
``TaskNode`` outputs) never fired for exactly the failure mode seen in the
field logs. These tests lock in that an all-gap output is now detected as
zero real findings and routed to the reframer.
"""

from __future__ import annotations

import pytest

from hyperion.orchestrator import Orchestrator
from hyperion.schemas.agents import AgentName, ModelTier
from hyperion.schemas.models import ConfidenceLevel, KeyFinding
from hyperion.schemas.workflow import TaskNode, TaskStatus


def _gap(agent: AgentName = AgentName.MARKET_ANALYST) -> KeyFinding:
    return KeyFinding(
        id="gap1",
        agent=agent,
        finding_type="research_gap",
        title="Gap",
        content="no data",
        sources=[],
        confidence=ConfidenceLevel.LOW,
        gaps=["q"],
    )


def _real(agent: AgentName = AgentName.MARKET_ANALYST) -> KeyFinding:
    return KeyFinding(
        id="real1",
        agent=agent,
        finding_type="market_data",
        title="Real",
        content="Real finding with data",
        sources=[],
        confidence=ConfidenceLevel.HIGH,
        gaps=[],
    )


def _orchestrator() -> Orchestrator:
    orch = Orchestrator()
    orch._task_outputs = {}
    return orch


class TestCountRealFindings:
    def test_none_shape_returns_none(self):
        assert _orchestrator()._count_real_findings(None) is None

    def test_empty_list_is_zero(self):
        assert _orchestrator()._count_real_findings([]) == 0

    def test_only_gaps_is_zero(self):
        assert _orchestrator()._count_real_findings([_gap(), _gap()]) == 0

    def test_one_real_is_one(self):
        assert _orchestrator()._count_real_findings([_real()]) == 1

    def test_mixed_counts_only_real(self):
        assert _orchestrator()._count_real_findings([_real(), _gap(), _gap()]) == 1

    def test_dict_with_findings_key(self):
        orch = _orchestrator()
        assert orch._count_real_findings({"findings": [_gap()]}) == 0
        assert orch._count_real_findings({"findings": [_real()]}) == 1

    def test_object_with_findings_attr(self):
        class _Out:
            findings = [_gap(), _real()]

        assert _orchestrator()._count_real_findings(_Out()) == 1


class TestTaskNeedsReframeGap:
    @staticmethod
    def _task(*, agent: AgentName, reframe_attempts: int = 0) -> TaskNode:
        return TaskNode(
            id="task_market_1",
            agent=agent,
            model_tier=ModelTier.STANDARD,
            description="Find TAM data for: automotive",
            status=TaskStatus.COMPLETED,
            reframe_attempts=reframe_attempts,
        )

    def test_all_gap_specialist_reframes(self):
        orch = _orchestrator()
        task = self._task(agent=AgentName.MARKET_ANALYST)
        orch._task_outputs[task.id] = [_gap(), _gap()]
        assert orch._task_needs_reframe(task) is True

    def test_real_finding_specialist_does_not_reframe(self):
        orch = _orchestrator()
        task = self._task(agent=AgentName.MARKET_ANALYST)
        orch._task_outputs[task.id] = [_real(), _gap()]
        assert orch._task_needs_reframe(task) is False

    def test_exhausted_retries_does_not_reframe(self):
        orch = _orchestrator()
        task = self._task(agent=AgentName.MARKET_ANALYST, reframe_attempts=Orchestrator.MAX_REFRAMER_RETRIES)
        orch._task_outputs[task.id] = [_gap(), _gap()]
        assert orch._task_needs_reframe(task) is False

    def test_non_specialist_does_not_reframe(self):
        orch = _orchestrator()
        task = self._task(agent=AgentName.SYNTHESIS_LEAD)
        orch._task_outputs[task.id] = [_gap(), _gap()]
        assert orch._task_needs_reframe(task) is False

    def test_failed_task_reframes(self):
        orch = _orchestrator()
        task = TaskNode(
            id="task_fail_1",
            agent=AgentName.MARKET_ANALYST,
            model_tier=ModelTier.STANDARD,
            description="x",
            status=TaskStatus.FAILED,
            error="boom",
        )
        assert orch._task_needs_reframe(task) is True
