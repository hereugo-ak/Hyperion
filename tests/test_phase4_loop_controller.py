"""Overhaul Phase 4 (overhaul.md §6 P4) — progress-driven loop controller.

Pins the failure-class-routed, progress-signalled loop behaviour that replaces
the old attempt-count loops:

- P4.3 REFRAMER health-gate: no reframing against a fully-dead fleet.
- P4.3 global reframe budget: reframes spawned per engagement are bounded.
- P4.4 progress signal: zero-delta evidence waves consume a progress budget.
- P4.6 hard total sub-agent ceiling including broadened respawns.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hyperion.orchestrator import WorkflowEngine
from hyperion.tools.engine_health import get_engine_health, reset_engine_health


def _orch() -> WorkflowEngine:
    orch = WorkflowEngine.__new__(WorkflowEngine)
    orch._reframes_spawned = 0
    orch._consecutive_zero_progress = 0
    orch._last_domains_seen = -1
    orch.MAX_REFRAMER_GLOBAL_BUDGET = 6
    orch._engagement_context = {}
    orch.router = MagicMock()
    orch._log = MagicMock()
    orch._publish_task_update = MagicMock()
    orch.bus = MagicMock()
    return orch


@pytest.fixture(autouse=True)
def _fresh_health():
    reset_engine_health()
    tracker = get_engine_health()
    tracker.reset()
    yield
    reset_engine_health()


# ── P4.3 · REFRAMER health-gate ────────────────────────────────────────────


def _make_task(agent, *, status="FAILED", reframe_attempts=0):
    from hyperion.schemas.workflow import TaskNode, TaskStatus

    return TaskNode(
        id=f"task_{id(object())}",
        agent=agent,
        model_tier="standard",
        description="Find competitor evidence on Indian space startups",
        dependencies=[],
        status=TaskStatus.FAILED if status == "FAILED" else TaskStatus.PENDING,
        error="timed out" if status == "FAILED" else "",
        reframe_attempts=reframe_attempts,
    )


@pytest.mark.asyncio
async def test_reframer_suppressed_when_no_living_class(monkeypatch, caplog) -> None:
    """P4.3: a fully-dead fleet (no living source class) must never be
    reframed — rewording a dead pool is pure token spend (the A-6 loop)."""
    from hyperion.tools.engine_health import _SOURCE_CLASS_ENGINES

    tracker = get_engine_health()
    for engine in sorted(
        set().union(*_SOURCE_CLASS_ENGINES.values())
    ):
        tracker.record_response(
            unresponsive_engines=[[engine, "HTTP error 403 (suspended_time=180)"]],
            responding_engines=[],
        )
    assert not tracker.living_classes()

    orch = _orch()
    orch._SPECIALIST_AGENTS = {"competitive_intel"}
    orch._task_outputs = {}

    reframe_called = AsyncMock()
    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._task_needs_reframe",
        lambda self, task: True,
    )
    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine.MAX_REFRAMER_RETRIES",
        2,
    )
    monkeypatch.setattr("hyperion.tools.task_reframer.reframe_task", reframe_called)

    task = _make_task("competitive_intel")
    await orch._maybe_reframe_failed_tasks([task], MagicMock())

    assert not reframe_called.await_count
    assert any(
        "REFRAMER HEALTH-GATE" in record.message
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_reframer_runs_when_class_is_living(monkeypatch) -> None:
    """P4.3: with a living scholar class, a failed specialist task is a
    NO_RESULTS case and the reframer is the correct remedy."""
    orch = _orch()
    orch._SPECIALIST_AGENTS = {"competitive_intel"}
    orch._task_outputs = {}
    orch._reframes_spawned = 0

    variant = MagicMock(rephrased_question="Broadened: India space startup market")

    async def _fake_reframe(*args, **kwargs):
        return MagicMock(variants=[variant])

    monkeypatch.setattr("hyperion.tools.task_reframer.reframe_task", _fake_reframe)
    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._task_needs_reframe",
        lambda self, task: True,
    )

    task = _make_task("competitive_intel")
    dag = MagicMock()
    dag.get_task.return_value = None
    dag.add_task.return_value = None
    dag.adapted = False
    dag.adaptation_log = []

    await orch._maybe_reframe_failed_tasks([task], dag)
    assert orch._reframes_spawned >= 1
    assert dag.adapted is True


@pytest.mark.asyncio
async def test_reframer_global_budget_blocks_after_exhaustion(monkeypatch, caplog) -> None:
    """P4.3: once the engagement-wide reframe budget is spent, no task is
    reworded again even if it would otherwise be eligible."""
    orch = _orch()
    orch._reframes_spawned = orch.MAX_REFRAMER_GLOBAL_BUDGET  # already exhausted
    orch._SPECIALIST_AGENTS = {"competitive_intel"}
    orch._task_outputs = {}

    reframe_called = AsyncMock()
    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._task_needs_reframe",
        lambda self, task: True,
    )
    monkeypatch.setattr("hyperion.tools.task_reframer.reframe_task", reframe_called)

    task = _make_task("competitive_intel")
    await orch._maybe_reframe_failed_tasks([task], MagicMock())

    assert not reframe_called.await_count
    assert any(
        "REFRAMER GLOBAL BUDGET" in record.message
        for record in caplog.records
    )


# ── P4.4 · Progress signal ──────────────────────────────────────────────────


def test_progress_signal_positive_delta_resets_budget(monkeypatch) -> None:
    orch = _orch()
    # domains_before=0 passed by the loop; the after-read sees 3 new domains.
    monkeypatch.setattr(orch, "_ledger_domains", lambda: 3)
    assert orch._record_wave_progress(0, max_zero=2) is True
    assert orch._consecutive_zero_progress == 0


def test_progress_signal_zero_delta_exhausts_budget(monkeypatch) -> None:
    """P4.4: two consecutive zero-delta waves consume the progress budget and
    signal the loop to stop."""
    orch = _orch()
    monkeypatch.setattr(orch, "_ledger_domains", lambda: 4)
    assert orch._record_wave_progress(4, max_zero=2) is True  # 1st zero
    assert orch._consecutive_zero_progress == 1
    assert orch._record_wave_progress(4, max_zero=2) is False  # 2nd zero → stop
    assert orch._consecutive_zero_progress == 2


def test_progress_signal_recovery_after_new_evidence(monkeypatch) -> None:
    """P4.4: a wave that adds domains resets the consecutive-zero counter."""
    orch = _orch()
    monkeypatch.setattr(orch, "_ledger_domains", lambda: 4)
    assert orch._record_wave_progress(4, max_zero=2) is True
    assert orch._consecutive_zero_progress == 1
    # Next wave: domains_before=0, read_after=4 → 4 new domains.
    assert orch._record_wave_progress(0, max_zero=2) is True
    assert orch._consecutive_zero_progress == 0


# ── P4.6 · Hard total ceiling ───────────────────────────────────────────────


def test_total_ceiling_constant_is_hard() -> None:
    from hyperion.agents.base import BaseAgent

    assert BaseAgent.SUB_AGENT_TOTAL_CEILING == 6


@pytest.mark.asyncio
async def test_broadened_respawn_refused_at_total_ceiling() -> None:
    """P4.6: even a broadened respawn is refused when the sequential total
    ceiling is exhausted — no more "8/6" overshoots."""
    from hyperion.agents.base import BaseAgent
    from hyperion.agents.sub_agent import SubAgentRunner
    from hyperion.schemas.agents import AgentName, ModelTier, SubAgentSpec

    parent = MagicMock()
    parent.max_sub_agents = 3
    parent.SUB_AGENT_TOTAL_CEILING = BaseAgent.SUB_AGENT_TOTAL_CEILING
    parent._sub_agent_respawned = set()
    # F-0.1-14: distinct-work-item budget set.
    parent._sub_agent_questions = {f"q_{i}" for i in range(BaseAgent.SUB_AGENT_TOTAL_CEILING)}
    parent.state = MagicMock(sub_agents_spawned=0, sub_agents_active=0)
    parent._transition = AsyncMock()
    parent._log = MagicMock()

    spec = SubAgentSpec(
        question="Find competitor evidence",
        parent_agent=AgentName.COMPETITIVE_INTEL,
        model_tier=ModelTier.STANDARD,
        tools=[],
        findings_model="KeyFinding",
        timeout_seconds=600,
        broadened=True,
    )
    bound = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
    with patch.object(SubAgentRunner, "run", new=AsyncMock(return_value=[])):
        findings = await bound(spec)
    assert findings == []
    log_lines = [c.args[0] for c in parent._log.call_args_list]
    assert any("total budget reached" in line for line in log_lines)
