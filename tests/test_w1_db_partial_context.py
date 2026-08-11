"""D-B (overhaul3_audit.md W1/S2): a specialist whose upstream dependency
CRASHED runs on reduced context instead of raising ``MissingDependencyOutput``.

The 2026-08-11 run: COMPETE crashed (D-A, 06:31:36) → its task is FAILED →
STRATEGY (a specialist) raised ``MissingDependencyOutput`` at 06:57:51 because
the OVERHAUL2 S4 partial-context exemption covered only SYNTHESIS_LEAD and
FACT_CHECKER. One specialist failure became a zero-report run.

The scheduler (``WorkflowDAG.get_ready_tasks``) explicitly licenses FAILED
dependencies as a ready condition — a FAILED dep is a *specialist crash*, not
a scheduling anomaly. D-B distinguishes:

- dep task exists and ``status == FAILED`` → run the dependent on reduced
  context carrying ``context["missing_dependencies"]`` (exactly like
  synthesis). The strict raise was meant for genuinely missing *retrieval
  inputs*, not upstream crashes.
- dep task absent from the DAG, or not FAILED (e.g. PENDING) → keep the
  strict ``MissingDependencyOutput`` raise (a scheduling anomaly / a
  retrieval artifact that will never arrive).

These tests reproduce the real failure (STRATEGY after COMPETE crashed), not a
happy-path mock: before the fix the FAILED-dep test raised and failed.
"""

from __future__ import annotations

import pytest

from hyperion.config import ModelTier
from hyperion.orchestrator import MissingDependencyOutput, WorkflowEngine
from hyperion.schemas.agents import AgentName
from hyperion.schemas.workflow import QuestionType, TaskNode, TaskStatus, WorkflowDAG


def _make_dag(question: str, tasks: list[TaskNode]) -> WorkflowDAG:
    return WorkflowDAG(
        engagement_id="eng_test",
        question=question,
        question_type=QuestionType.GENERAL,
        tasks=tasks,
        estimated_total_llm_calls=len(tasks),
        estimated_total_tokens=5000 * len(tasks),
        estimated_duration_minutes=1.0,
    )


def _task(tid: str, agent: AgentName, deps: list[str] | None = None) -> TaskNode:
    return TaskNode(
        id=tid,
        agent=agent,
        model_tier=ModelTier.STANDARD,
        description=f"task {tid}",
        dependencies=deps or [],
    )


class _RecordingAgent:
    """Stub specialist that records the context it actually received."""

    _findings: list = []

    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def run(self, **kwargs: object) -> dict:
        self.calls.append(kwargs)
        return {"result": "partial-context-analysis"}


# ── the real failure: a crashed specialist dep must not cascade ──────────────


@pytest.mark.asyncio
async def test_specialist_with_crashed_dep_runs_with_partial_context(monkeypatch) -> None:
    """D-B reproduction — STRATEGY after COMPETE crashed (06:57:51).

    Before the fix this raised ``MissingDependencyOutput`` ("refusing to run
    with a partial context") because the S4 exemption covered only
    SYNTHESIS_LEAD/FACT_CHECKER. After the fix the dependent runs, with
    ``context["missing_dependencies"]`` naming the crashed dep — the audit's
    exact demand.
    """
    engine = WorkflowEngine()
    engine._engagement_id = "eng_db"

    crashed_dep = _task("task_competitive_intel", AgentName.COMPETITIVE_INTEL)
    crashed_dep.status = TaskStatus.FAILED
    crashed_dep.error = "BaseAgent._log() takes 2 positional arguments but 3 were given"
    dependent = _task(
        "task_strategy_analyst", AgentName.STRATEGY_ANALYST,
        deps=["task_competitive_intel"],
    )
    dag = _make_dag("q", [crashed_dep, dependent])

    agent = _RecordingAgent()
    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: agent)

    result = await engine._execute_task(dependent, dag)

    assert result is not None
    assert agent.calls, "the dependent must actually dispatch on partial context"
    ctx = agent.calls[0]["context"]
    assert ctx["missing_dependencies"] == ["task_competitive_intel"]
    assert "collected_findings" in ctx
    # Ran on reduced context — NOT marked FAILED by a cascade.
    assert dependent.status == TaskStatus.AWAITING_FOLLOWUP


# ── a genuinely missing retrieval input still raises ─────────────────────────


@pytest.mark.asyncio
async def test_specialist_with_pending_dep_still_raises(monkeypatch) -> None:
    """A dep that is NOT a crashed specialist (PENDING — the scheduler never
    released this dependent) is a scheduling anomaly: strict raise stays."""
    engine = WorkflowEngine()
    engine._engagement_id = "eng_db"

    not_yet_run = _task("dep_pending", AgentName.MARKET_ANALYST)  # status PENDING
    dependent = _task("dep2", AgentName.FINANCIAL_ANALYST, deps=["dep_pending"])
    dag = _make_dag("q", [not_yet_run, dependent])

    class _NeverAgent:
        async def run(self, **kwargs: object) -> dict:  # noqa: ANN003
            raise AssertionError("dependent must never dispatch on a pending dep")

    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: _NeverAgent())

    with pytest.raises(MissingDependencyOutput, match="dep_pending"):
        await engine._execute_task(dependent, dag)


@pytest.mark.asyncio
async def test_specialist_with_absent_dep_still_raises(monkeypatch) -> None:
    """A dep id that does not exist anywhere in the DAG is a genuinely missing
    retrieval artifact: strict raise stays."""
    engine = WorkflowEngine()
    engine._engagement_id = "eng_db"

    dependent = _task("dep2", AgentName.FINANCIAL_ANALYST, deps=["no_such_task"])
    dag = _make_dag("q", [dependent])

    class _NeverAgent:
        async def run(self, **kwargs: object) -> dict:  # noqa: ANN003
            raise AssertionError("dependent must never dispatch on an absent dep")

    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: _NeverAgent())

    with pytest.raises(MissingDependencyOutput, match="no_such_task"):
        await engine._execute_task(dependent, dag)
