"""D-C / S10 (overhaul3_audit.md W4/S10 + §5.4 P2): failure-class accuracy.

When a sub-agent self-heal is REFUSED by the budget gate, the RUNNER's typed
outcome must be stamped ``BUDGET_REFUSED`` — never the ``ANALYSIS_FAILED`` /
``RETRY_EXHAUSTED`` the runner typed before the heal was attempted. Those
classes assert "the STRONG tier ran and failed"; a gate refusal is a
different typed truth. Telemetry reads the typed class, never the prose.

Before the fix the parent logs "REFUSED BY BUDGET" (D-C) but leaves the
runner's outcome stamped as the original failure — so KPI/telemetry that
reads ``runner.outcome`` still reports a STRONG-tier failure that never
happened.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hyperion.agents.base import BaseAgent
from hyperion.agents.sub_agent import SubAgentRunner
from hyperion.config import ModelTier
from hyperion.schemas.agents import AgentName, SubAgentSpec
from hyperion.schemas.models import (
    RESEARCH_GAP_TYPE,
    ConfidenceLevel,
    KeyFinding,
    ResearchOutcome,
)


def _spec(**overrides) -> SubAgentSpec:
    base = {
        "question": "Scrape competitor pricing page, extract pricing tiers",
        "parent_agent": AgentName.COMPETITIVE_INTEL,
        "model_tier": ModelTier.STANDARD,
        "tools": [],
        "findings_model": "KeyFinding",
        "timeout_seconds": 600,
        "context": {"url": "https://competitor.example/pricing"},
    }
    base.update(overrides)
    return SubAgentSpec(**base)


def _parent() -> MagicMock:
    parent = MagicMock()
    parent.max_sub_agents = 3
    parent.SUB_AGENT_TOTAL_CEILING = 6
    parent.SUB_AGENT_CONCURRENT_MAX = 5
    parent.spec = SimpleNamespace(max_sub_agents=3)
    parent._sub_agent_specs = []
    parent._sub_agent_respawned = set()
    parent._sub_agent_questions = set()
    parent._deferred_specs = []
    parent._last_spawn_refused = False
    parent.state = SimpleNamespace(sub_agents_spawned=0, sub_agents_active=0)
    parent._transition = AsyncMock()
    parent._log = MagicMock()
    parent._dependency_health_green = lambda: True
    parent._should_respawn_broadened = (
        BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
    )
    return parent


def _gap_finding() -> KeyFinding:
    return KeyFinding(
        id="f", agent="sub", finding_type=RESEARCH_GAP_TYPE, title="t",
        content="no validated findings here", confidence=ConfidenceLevel.LOW,
    )


@pytest.mark.asyncio
async def test_budget_refused_self_heal_stamps_runner_outcome() -> None:
    """S10 — the 2026-08-11 lie, at the typed-outcome level.

    Fault injection: the STRONG-tier heal is REFUSED at the budget gate. The
    runner's typed outcome must be stamped ``BUDGET_REFUSED`` — not the
    ``ANALYSIS_FAILED`` it typed when PROVIDER_FAILURE was recorded, which
    would tell telemetry a STRONG-tier run failed when the gate never let it
    run.
    """
    parent = _parent()
    captured: list[SubAgentRunner] = []

    async def _fake_run(self):  # noqa: ANN001
        self.recovery_hint = "PROVIDER_FAILURE"
        self.outcome = ResearchOutcome.ANALYSIS_FAILED  # typed by the runner
        captured.append(self)
        return [_gap_finding()]

    async def _refusing_spawn(spec: SubAgentSpec) -> list:
        if spec.model_tier == ModelTier.STRONG:
            parent._last_spawn_refused = True
            return []
        return await real_spawn(spec)

    real_spawn = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
    parent._spawn_sub_agent = _refusing_spawn  # type: ignore[method-assign]

    with patch.object(SubAgentRunner, "run", new=_fake_run):
        await real_spawn(_spec())

    assert captured, "the STANDARD runner must have run before the heal"
    runner = captured[0]
    assert runner.outcome == ResearchOutcome.BUDGET_REFUSED, (
        "a heal refused by the budget gate must stamp BUDGET_REFUSED on the "
        "runner outcome — ANALYSIS_FAILED/RETRY_EXHAUSTED would claim the "
        "STRONG tier ran and failed when the gate never let it run"
    )
    assert runner.recovery_hint == "BUDGET_REFUSED"
    log_lines = [c.args[0] for c in parent._log.call_args_list]
    assert any("REFUSED BY BUDGET" in line for line in log_lines)
