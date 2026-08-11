"""D-D (overhaul3_audit.md W1/S4): ``_all_findings`` must be fed from the BUS.

The 2026-08-11 run logged the count/collection mismatch twice:

    06:40:41 sustainability_analyst: completed with 1 findings (total collected: 0)
    06:41:27 market_analyst: completed with 8 findings (total collected: 7)

Specialists publish two ways (orchestrator.py:1054):

- ``_publish_finding()`` → ``agent._findings`` AND the bus.
- the **aggregate model publish** ``bus.publish(Channel.FINDINGS,
  MessageType.FINDING, payload={model_dump...})`` → the bus ONLY.

The count line used the bus (``bus.get_findings_count`` — authoritative), but
the collection into ``_all_findings`` read only ``agent._findings``. So an
aggregate publish was COUNTED but never COLLECTED — synthesis / floor report /
KPI-3 silently lost it.

Fix (P1 — count and collection read the same store): after
``extend(agent._findings)``, drain that agent's retained bus findings
(``bus.get_retained_findings()`` filtered by ``sender == task.agent``),
converting aggregate payloads with the same synthetic-finding path the
Synthesis Lead uses (``synthetic_finding_from_payload``), dedup by finding id.

These tests reproduce the real failure: a specialist whose ONLY output is the
aggregate bus publish must land in ``_all_findings`` (before the fix it is
counted but never collected — the "1 (0)" lie).
"""

from __future__ import annotations

import pytest

from hyperion.agents.bus import Channel, MessageType, reset_bus


@pytest.fixture(autouse=True)
def _clean_bus() -> None:
    """These tests publish to the singleton bus; leave it pristine for the
    next test module (SynthesisLead's subscribe replays retained findings)."""
    reset_bus()
    yield
    reset_bus()
from hyperion.config import ModelTier
from hyperion.orchestrator import WorkflowEngine
from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import ConfidenceLevel, KeyFinding
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


def _task(tid: str, agent: AgentName) -> TaskNode:
    return TaskNode(
        id=tid,
        agent=agent,
        model_tier=ModelTier.STANDARD,
        description=f"task {tid}",
    )


# ── the real failure: aggregate bus-only publish is collected ────────────────


@pytest.mark.asyncio
async def test_aggregate_bus_publish_lands_in_all_findings(monkeypatch) -> None:
    """D-D reproduction — the 06:40:41 "1 (0)" / 06:41:27 "8 (7)" mismatch.

    A specialist whose ONLY output is the aggregate ``bus.publish`` (no
    ``agent._findings`` entries) is counted by the bus but was never
    collected into ``_all_findings``. After the fix the aggregate lands in
    ``_all_findings`` and the completion line reads N/N, not N/(N-1).
    """
    reset_bus()
    engine = WorkflowEngine()
    engine._engagement_id = "eng_dd"

    class _AggregateOnlyAgent:
        _findings: list = []

        def __init__(self, bus: object) -> None:
            self.bus = bus

        async def run(self, **kwargs: object) -> dict:
            await self.bus.publish(
                channel=Channel.FINDINGS,
                msg_type=MessageType.FINDING,
                sender=AgentName.MARKET_ANALYST,
                payload={
                    "agent": "market_analyst",
                    "market_analysis": {
                        "tam_triangulated": "$2.4B",
                        "market_maturity": "Growth",
                    },
                    "confidence": "medium",
                },
            )
            return {"result": "aggregate-only"}

    agent = _AggregateOnlyAgent(engine.bus)
    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: agent)

    task = _task("t_market", AgentName.MARKET_ANALYST)
    dag = _make_dag("q", [task])

    await engine._execute_task(task, dag)

    # The bus counted it (1) — and now the collection sees it too.
    assert engine.bus.get_findings_count(AgentName.MARKET_ANALYST) == 1
    assert len(engine._all_findings) == 1, (
        "the aggregate bus-only publish must be collected into _all_findings "
        "(pre-fix: counted but never collected)"
    )
    collected = engine._all_findings[0]
    assert collected.agent == "market_analyst"
    # The synthetic aggregate finding was collected. (The KeyFinding
    # constructor retypes a source-less synthetic to unverified_assertion —
    # existing provenance-validator behaviour — so finding_type is not pinned.)
    assert "2.4B" in collected.content or "TAM" in collected.title


# ── dedup: findings already in agent._findings are not double-collected ──────


@pytest.mark.asyncio
async def test_bus_drain_dedups_by_finding_id(monkeypatch) -> None:
    """A specialist that publishes N individual findings (agent._findings AND
    bus) PLUS an aggregate must yield exactly N+1 collected findings — the
    drain must dedup the individual findings already collected from
    ``agent._findings``."""
    reset_bus()
    engine = WorkflowEngine()
    engine._engagement_id = "eng_dd"

    individual = [
        KeyFinding(
            id=f"kf_{i}", agent="market_analyst", finding_type="market_size",
            title=f"finding {i}", content=f"evidence {i}",
            confidence=ConfidenceLevel.MEDIUM,
        )
        for i in range(2)
    ]

    class _MixedAgent:
        _findings: list = []

        def __init__(self, bus: object) -> None:
            self.bus = bus
            self._findings = list(individual)

        async def run(self, **kwargs: object) -> dict:
            for f in individual:
                await self.bus.publish_finding(AgentName.MARKET_ANALYST, f)
            await self.bus.publish(
                channel=Channel.FINDINGS,
                msg_type=MessageType.FINDING,
                sender=AgentName.MARKET_ANALYST,
                payload={
                    "agent": "market_analyst",
                    "market_analysis": {"market_maturity": "Mature"},
                    "confidence": "low",
                },
            )
            return {"result": "mixed"}

    agent = _MixedAgent(engine.bus)
    monkeypatch.setattr(engine, "_get_agent", lambda agent_name: agent)

    task = _task("t_market", AgentName.MARKET_ANALYST)
    dag = _make_dag("q", [task])

    await engine._execute_task(task, dag)

    assert engine.bus.get_findings_count(AgentName.MARKET_ANALYST) == 3
    # 2 individual + 1 aggregate, deduped by id — NOT 2 + 2 + 1.
    ids = [getattr(f, "id", None) for f in engine._all_findings]
    assert len(ids) == len(set(ids)), "collected findings must be deduped by id"
    assert len(engine._all_findings) == 3, (
        "expected 2 individual + 1 aggregate = 3 collected; got "
        f"{len(engine._all_findings)} (pre-fix: 2, the aggregate was lost)"
    )


# ── the completion telemetry reads N/N, not N/(N-1) ──────────────────────────


@pytest.mark.asyncio
async def test_completion_line_reports_count_collection_parity(monkeypatch) -> None:
    """The log line that shipped the lie on 2026-08-11 must now read
    ``completed with N findings (total collected: N)`` for a bus-only agent."""
    reset_bus()
    engine = WorkflowEngine()
    engine._engagement_id = "eng_dd"

    logged: list[str] = []
    real_log = engine._log

    def _capture(message: str) -> None:
        logged.append(message)
        real_log(message)

    monkeypatch.setattr(engine, "_log", _capture)

    class _AggregateOnlyAgent:
        _findings: list = []

        def __init__(self, bus: object) -> None:
            self.bus = bus

        async def run(self, **kwargs: object) -> dict:
            await self.bus.publish(
                channel=Channel.FINDINGS,
                msg_type=MessageType.FINDING,
                sender=AgentName.SUSTAINABILITY_ANALYST,
                payload={
                    "agent": "sustainability_analyst",
                    "sustainability_analysis": {"esg_score_count": 4},
                    "esg_score_count": 4,
                    "confidence": "medium",
                },
            )
            return {"result": "aggregate-only"}

    monkeypatch.setattr(
        engine, "_get_agent", lambda agent_name: _AggregateOnlyAgent(engine.bus)
    )

    task = _task("t_sus", AgentName.SUSTAINABILITY_ANALYST)
    dag = _make_dag("q", [task])

    await engine._execute_task(task, dag)

    assert any(
        "completed with 1 findings (total collected: 1)" in line for line in logged
    ), (
        "the completion line must read 1 (1) — the bus-only aggregate is now "
        f"collected; got: {logged}"
    )
