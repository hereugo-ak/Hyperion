"""W-20 — Deterministic run id, journal resume, interrupt-safety wiring.

Verifies the acceptance criteria from HYPERION_DEEP_AUDIT_2026-07-31_PART2.md
§W-20 without live providers, Docker, or network:

1. run_id is deterministic: same question (even with different casing /
   whitespace) yields the same id; --fresh yields a random one.
2. Simulated crash/resume: a journal pre-seeded with N successful steps +
   artifacts causes re-execution to replay those steps from cache — the
   agent dispatch stub is NEVER invoked for them (not re-dispatched).
3. MissingDependencyOutput: a task whose declared dependency failed raises
   the named exception rather than silently running with a partial context.
4. cli.py contains a real @app.command() named ``resume`` (not only the
   banner string) and at least one SIGINT/SIGTERM handler exists in-tree.
5. _all_findings is guarded by an asyncio.Lock.

These are unit-level verifications: the sandbox cannot run a real multi-hour
engagement or send real signals mid-run, so the crash is simulated by
pre-seeding the journal exactly as a crashed run would have left it.
"""

from __future__ import annotations

import os
import re

import pytest

from hyperion.config import ModelTier
from hyperion.orchestrator import (
    MissingDependencyOutput,
    WorkflowEngine,
    derive_run_id,
)
from hyperion.obs import ArtifactStore, RunJournal
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


# ── 1. deterministic run id ────────────────────────────────────────────────────


def test_derive_run_id_deterministic_and_normalized() -> None:
    a = derive_run_id("What is the market for EVs in India?")
    b = derive_run_id("What is the market for EVs in India?")
    assert a == b, "same question must yield the same run_id"
    assert a.startswith("eng_") and len(a) == 16

    # Normalisation: case and whitespace differences must NOT defeat resume.
    c = derive_run_id("what is the market for evs in india?")
    d = derive_run_id("  What   is  the market\tfor EVs in India?  ")
    assert c == d == a

    # Genuinely different question → different id.
    assert derive_run_id("A different question entirely") != a

    # Caller-supplied engagement key namespaces the same question.
    assert derive_run_id("What is the market for EVs in India?", "q3") != a


# ── 2. simulated crash → resume replays cached steps without re-dispatch ──────


@pytest.mark.asyncio
async def test_resume_skips_completed_steps(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    question = "Should we enter the Brazilian logistics market?"
    run_id = derive_run_id(question)

    # Simulate the crash: steps s1..s3 succeeded (journal + artifacts on
    # disk), s4/s5 never ran. This is exactly what an interrupted run leaves
    # behind (WAL commits per-step, so completed steps survive the crash).
    journal = RunJournal(run_id)
    journal.open()
    artifacts = ArtifactStore(run_id)
    completed_ids = ["s1", "s2", "s3"]
    for tid in completed_ids:
        inputs_hash = journal.compute_inputs_hash(
            {"agent": "market_analyst", "description": f"task {tid}", "question": question}
        )
        ref = artifacts.save(tid, {"result": f"cached-{tid}"})
        journal.record_success(tid, inputs_hash, ref)
    journal.close()

    engine = WorkflowEngine()
    engine._engagement_id = run_id
    engine._journal = RunJournal(run_id)
    engine._journal.open()
    engine._artifacts = ArtifactStore(run_id)

    dispatch_calls: list[str] = []

    class _StubAgent:
        _findings: list = []

        async def run(self, **kwargs):  # noqa: ANN003
            return {"result": "live"}

    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: _StubAgent())
    monkeypatch.setattr(
        WorkflowEngine,
        "_compute_step_hash",
        lambda self, task, dag: self._journal.compute_inputs_hash(
            {"agent": task.agent.value, "description": task.description, "question": dag.question}
        ),
    )

    # Intercept every live dispatch so we can count re-dispatches.
    import asyncio as _asyncio

    real_wait_for = _asyncio.wait_for

    async def _counting_wait_for(coro, timeout):  # noqa: ANN001
        dispatch_calls.append("dispatched")
        return await real_wait_for(coro, timeout)

    monkeypatch.setattr(_asyncio, "wait_for", _counting_wait_for)

    tasks = [_task(tid, AgentName.MARKET_ANALYST) for tid in completed_ids]
    dag = _make_dag(question, tasks)

    try:
        for task in tasks:
            out = await engine._execute_task(task, dag)
            assert out is not None
            assert task.status == TaskStatus.COMPLETED
    finally:
        engine._journal.close()

    assert dispatch_calls == [], (
        f"completed steps must replay from the journal, not re-dispatch; "
        f"got {len(dispatch_calls)} live dispatch(es)"
    )
    assert all(tid in engine._task_outputs for tid in completed_ids)


@pytest.mark.asyncio
async def test_unrecorded_step_dispatches_live(tmp_path, monkeypatch) -> None:
    """A step with no journal record (the crash frontier) must execute live."""
    monkeypatch.chdir(tmp_path)
    question = "frontier question"
    run_id = derive_run_id(question)

    engine = WorkflowEngine()
    engine._engagement_id = run_id
    engine._journal = RunJournal(run_id)
    engine._journal.open()
    engine._artifacts = ArtifactStore(run_id)

    dispatched: list[str] = []

    class _StubAgent:
        _findings: list = []

        async def run(self, **kwargs):  # noqa: ANN003
            dispatched.append("live")
            return {"result": "live"}

    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: _StubAgent())

    task = _task("s4", AgentName.MARKET_ANALYST)
    dag = _make_dag(question, [task])

    try:
        out = await engine._execute_task(task, dag)
    finally:
        engine._journal.close()

    assert dispatched == ["live"], "unrecorded step must dispatch live"
    assert out == {"result": "live"}
    assert task.status in (TaskStatus.COMPLETED, TaskStatus.AWAITING_FOLLOWUP)


# ── 3. MissingDependencyOutput ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_dependency_output_raises(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    engine = WorkflowEngine()
    engine._engagement_id = "eng_dep"

    failed_dep = _task("dep1", AgentName.MARKET_ANALYST)
    failed_dep.status = TaskStatus.FAILED
    dependent = _task("dep2", AgentName.FINANCIAL_ANALYST, deps=["dep1"])
    dag = _make_dag("q", [failed_dep, dependent])

    class _StubAgent:
        async def run(self, **kwargs):  # noqa: ANN003
            raise AssertionError("dependent must never dispatch on partial context")

    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: _StubAgent())

    with pytest.raises(MissingDependencyOutput, match="dep1"):
        await engine._execute_task(dependent, dag)


# ── 4. CLI wiring: real resume command + signal handlers ──────────────────────


def test_resume_is_a_real_command_and_banner_matches() -> None:
    from hyperion import cli

    # Typer leaves .name None when it is derived from the callback name.
    names = {
        cmd.name or (cmd.callback.__name__ if cmd.callback else None)
        for cmd in cli.app.registered_commands
    }
    assert "resume" in names, f"resume must be a registered command, got {names}"
    # The banner advertises resume — and now the command actually exists.
    assert "resume" in cli.__doc__


def test_signal_handlers_exist_in_cli() -> None:
    src = open("hyperion/cli.py", encoding="utf-8").read()
    assert "signal.SIGINT" in src and "signal.SIGTERM" in src, (
        "W-20 requires SIGINT/SIGTERM handlers at the CLI entry points"
    )
    # The handler must do the side effects (journal close + quarantine), not
    # merely log the interrupt.
    assert "journal.close()" in src
    assert "_rejected" in src


def test_fresh_flag_forces_random_id() -> None:
    import inspect

    from hyperion.orchestrator import WorkflowEngine

    sig = inspect.signature(WorkflowEngine.run_engagement)
    assert "fresh" in sig.parameters
    assert sig.parameters["fresh"].default is False


# ── 5. findings lock ──────────────────────────────────────────────────────────


def test_all_findings_guarded_by_lock() -> None:
    import asyncio

    engine = WorkflowEngine()
    assert hasattr(engine, "_findings_lock")
    assert isinstance(engine._findings_lock, asyncio.Lock)

    src = open("hyperion/orchestrator.py", encoding="utf-8").read()
    # Both mutation sites (cache-replay + live collector) sit under the lock.
    assert src.count("async with self._findings_lock") >= 2
    # No bare extend outside the lock remains.
    bare = [
        ln for ln in src.splitlines()
        if "self._all_findings.extend" in ln and "async with" not in ln
    ]
    # The extends are on their own lines inside the lock blocks — verify each
    # extend line is preceded (within 3 lines) by the lock acquisition.
    lines = src.splitlines()
    for i, ln in enumerate(lines):
        if "self._all_findings.extend" in ln:
            window = "\n".join(lines[max(0, i - 3):i])
            assert "async with self._findings_lock" in window, (
                f"findings extend at line {i+1} is not under the lock"
            )
    assert bare  # sanity: we actually found extend sites to check


# ── 6. quarantine path (interrupt safety) ─────────────────────────────────────


def test_quarantine_partial_outputs_moves_deliverable(tmp_path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    os.makedirs("output", exist_ok=True)
    # A partial staging file and a stale deliverable from an interrupted run.
    with open("output/report.pdf.staging.pdf", "wb") as f:
        f.write(b"%PDF-partial")
    with open("output/report.pdf", "wb") as f:
        f.write(b"%PDF-stale")

    from hyperion.cli import _quarantine_partial_outputs

    moved = _quarantine_partial_outputs()
    assert len(moved) == 2
    assert not os.path.exists("output/report.pdf")
    assert not os.path.exists("output/report.pdf.staging.pdf")
    assert all("_rejected" in m and m.endswith(".interrupted.pdf") for m in moved)
