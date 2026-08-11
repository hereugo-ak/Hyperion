"""D-E (overhaul3_audit.md W2/S5): contain reframe waste.

The 2026-08-11 run exploded variants against a dead fleet:

    06:47:43 REFRAMER: task_competitive_intel (failed) → 3 reframed variant(s) [attempt 1/2]
    06:58:03 REFRAMER: task_reframed_1_1_task_competitive_intel (failed) → 3 reframed [attempt 2/2]
    06:58:06 REFRAMER: task_reframed_1_2_task_competitive_intel (failed) → 1 reframed [attempt 2/2]

Nothing stopped an already-reframed variant from being reframed again, and the
health-gate checked only that SOME source class was alive — never the class
the query actually targets.

Fix (D-E, the three refusals):

(a) a task that is itself a ``task_reframed_*`` variant is never reframed
    (``reframed_from`` set) — the variant TREE is capped, not just per-task
    attempts;
(b) a task whose target source class is dead is never reframed (per-class
    living check — the old gate was "any class alive");
(c) a task whose OWN dependency FAILED is never reframed — rewording cannot
    fix an upstream crash, it only re-runs the same dead path.

Each test reproduces the real failure: before the fix, the variant is
reframed again / the dead-class query is reframed / the crashed-dep task is
reframed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

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
    # The REAL _task_needs_reframe reads the bus for a findings count; a
    # MagicMock would be truthy and the eligibility check would short-circuit.
    orch.bus.get_findings_count.return_value = 0
    orch._SPECIALIST_AGENTS = {"competitive_intel"}
    orch._task_outputs = {}
    return orch


@pytest.fixture(autouse=True)
def _fresh_health():
    reset_engine_health()
    tracker = get_engine_health()
    tracker.reset()
    yield
    reset_engine_health()


def _make_task(
    agent: str = "competitive_intel",
    *,
    status: str = "FAILED",
    reframe_attempts: int = 0,
    reframed_from: str | None = None,
    description: str = "Find competitor evidence on Indian space startups",
    deps: list[str] | None = None,
):
    from hyperion.schemas.workflow import TaskNode, TaskStatus

    return TaskNode(
        id=f"task_{id(object())}",
        agent=agent,
        model_tier="standard",
        description=description,
        dependencies=deps or [],
        status=TaskStatus.FAILED if status == "FAILED" else TaskStatus.PENDING,
        error="timed out" if status == "FAILED" else "",
        reframe_attempts=reframe_attempts,
        reframed_from=reframed_from,
    )


# ── (a) never reframe an already-reframed variant ────────────────────────────


def test_reframed_variant_is_never_reframed() -> None:
    """D-E (a) — the 06:58:03 ``task_reframed_1_1_* → 3 more`` explosion.
    A FAILED variant that still has retries left must be refused purely
    because it is a variant."""
    orch = _orch()
    variant = _make_task(reframe_attempts=1, reframed_from="task_competitive_intel")

    assert orch._task_needs_reframe(variant) is False, (
        "an already-reframed variant must never be reframed again — that is "
        "the unbounded variant tree from the 2026-08-11 run"
    )


# ── (c) never reframe a task whose own dependency failed ─────────────────────


@pytest.mark.asyncio
async def test_reframing_refused_when_own_dependency_failed(monkeypatch) -> None:
    """D-E (c) — STRATEGY's dep COMPETE crashed (06:57:51). Reframing STRATEGY
    cannot fix the upstream crash; it only re-runs the same dead path."""
    orch = _orch()
    # STRATEGY must be an eligible specialist so the refusal is caused by the
    # dependency check, not by the specialist-set gate.
    orch._SPECIALIST_AGENTS = {"competitive_intel", "strategy_analyst"}

    reframe_called = AsyncMock()
    monkeypatch.setattr("hyperion.tools.task_reframer.reframe_task", reframe_called)

    dep_task = _make_task("competitive_intel", status="FAILED")  # the crashed dep
    dependent = _make_task("strategy_analyst", deps=[dep_task.id])
    dag = MagicMock()
    dag.get_task.return_value = dep_task  # returns the FAILED dep for any id

    await orch._maybe_reframe_failed_tasks([dependent], dag)

    assert not reframe_called.await_count, (
        "a task whose own dependency FAILED must not be reframed — rewording "
        "cannot fix an upstream crash"
    )


# ── (b) never reframe a query targeting a dead source class ─────────────────


@pytest.mark.asyncio
async def test_reframing_refused_when_target_class_dead(monkeypatch) -> None:
    """D-E (b) — the audited run: brave 429-suspended and wikipedia 400s, so
    the WEB class is dead while scholar/reference stay alive. A web-targeted
    query must NOT be reframed even though the fleet is not fully dead — the
    old gate checked only that SOME class was alive."""
    from hyperion.tools.engine_health import _SOURCE_CLASS_ENGINES

    tracker = get_engine_health()
    # Kill ONLY the web class (the target of the test query).
    for engine in _SOURCE_CLASS_ENGINES["web"]:
        tracker.record_response(
            unresponsive_engines=[[engine, "HTTP error 429 (suspended_time=180)"]],
            responding_engines=[],
        )
    assert get_engine_health().class_healthy("web") is False
    assert get_engine_health().living_classes(), "scholar/reference still alive"

    orch = _orch()

    reframe_called = AsyncMock()
    monkeypatch.setattr("hyperion.tools.task_reframer.reframe_task", reframe_called)

    task = _make_task(description="Find competitor evidence on Indian space startups")
    dag = MagicMock()
    dag.get_task.return_value = None

    await orch._maybe_reframe_failed_tasks([task], dag)

    assert not reframe_called.await_count, (
        "a query targeting a DEAD source class must not be reframed — the "
        "per-class health-gate replaces the 'any class alive' check"
    )


# ── the healthy path still reframes (regression pin) ─────────────────────────


@pytest.mark.asyncio
async def test_reframing_still_runs_when_target_class_living(monkeypatch) -> None:
    """A web-targeted query with a living web class still earns a reframe —
    D-E removes the refusals, not the remedy."""
    orch = _orch()

    variant = MagicMock(rephrased_question="Broadened: India space startup market")

    async def _fake_reframe(*args, **kwargs):
        return MagicMock(variants=[variant])

    monkeypatch.setattr("hyperion.tools.task_reframer.reframe_task", _fake_reframe)

    task = _make_task(description="Find competitor evidence on Indian space startups")
    dag = MagicMock()
    dag.get_task.return_value = None
    dag.add_task.return_value = None
    dag.adapted = False
    dag.adaptation_log = []

    await orch._maybe_reframe_failed_tasks([task], dag)

    assert orch._reframes_spawned >= 1
    assert dag.adapted is True
