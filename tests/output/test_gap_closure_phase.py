"""P2-18 / P2-G19: a GAP_CLOSURE phase sits between fact check and quality
gate, and specialists are not COMPLETED before it closes.

Before the fix, a specialist task reached ``TaskStatus.COMPLETED`` the moment
its ``run()`` returned, so a later ``verify_claims`` request arrived at an
agent whose task was done and whose state was DONE. The gap-closure loop had
nobody to dispatch to.

After the fix:
  * ``TaskStatus.AWAITING_FOLLOWUP`` exists; specialist tasks rest there
    (not COMPLETED) after their initial run.
  * A synthetic ``task_gap_closure`` node sits in the DAG between fact check
    and quality gate, owned by the Engagement Director.
  * ``_gap_closure_phase`` re-dispatches gaps (round 1: originating
    specialist, one tier up, urgency HIGH), then finalizes specialists to
    COMPLETED and the closure task itself.
  * Specialist completion still counts toward ``get_ready_tasks`` /
    ``is_complete`` while awaiting followup, so downstream tasks are not
    deadlocked.
"""

from __future__ import annotations

from hyperion.schemas.agents import AgentName
from hyperion.schemas.workflow import QuestionType, TaskNode, TaskStatus, WorkflowDAG


def _specialist_task(agent: AgentName = AgentName.MARKET_ANALYST) -> TaskNode:
    return TaskNode(
        id=f"task_{agent.value}",
        agent=agent,
        model_tier="standard",
        description="specialist analysis",
    )


def _dag(tasks: list[TaskNode]) -> WorkflowDAG:
    return WorkflowDAG(
        engagement_id="e1",
        question="Should we enter the market?",
        question_type=QuestionType.GO_NO_GO,
        tasks=tasks,
        estimated_total_llm_calls=10,
        estimated_total_tokens=10000,
        estimated_duration_minutes=5.0,
    )


class TestAwaitingFollowupStatus:
    def test_status_exists(self):
        assert TaskStatus.AWAITING_FOLLOWUP.value == "awaiting_followup"

    def test_awaiting_followup_counts_toward_readiness(self):
        """A downstream task whose dependency rests in AWAITING_FOLLOWUP is
        still ready: the specialist finished its initial run, it is simply
        alive for follow-up."""
        dep = _specialist_task()
        dep.status = TaskStatus.AWAITING_FOLLOWUP
        downstream = TaskNode(
            id="task_synthesis_lead",
            agent=AgentName.SYNTHESIS_LEAD,
            model_tier="strong",
            description="synthesize",
            dependencies=[dep.id],
        )
        dag = _dag([dep, downstream])
        ready_ids = {t.id for t in dag.get_ready_tasks()}
        assert "task_synthesis_lead" in ready_ids

    def test_awaiting_followup_counts_toward_completeness(self):
        dep = _specialist_task()
        dep.status = TaskStatus.AWAITING_FOLLOWUP
        dag = _dag([dep])
        assert dag.is_complete


class TestGapClosureTaskNode:
    def _orch(self):
        from hyperion.orchestrator import WorkflowEngine

        orch = WorkflowEngine.__new__(WorkflowEngine)
        return orch

    def test_gap_closure_task_inserted_between_fact_check_and_quality_gate(self):
        orch = self._orch()
        specialist = _specialist_task()
        fact_check = TaskNode(
            id="task_fact_checker",
            agent=AgentName.FACT_CHECKER,
            model_tier="standard",
            description="verify",
            dependencies=[specialist.id],
        )
        quality = TaskNode(
            id="task_quality_gate",
            agent=AgentName.QUALITY_GATE,
            model_tier="strong",
            description="score",
            dependencies=["task_fact_checker"],
        )
        dag = _dag([specialist, fact_check, quality])

        orch._ensure_gap_closure_task(dag)

        closure = dag.get_task("task_gap_closure")
        assert closure is not None, "GAP_CLOSURE phase task missing from DAG"
        assert closure.agent == AgentName.ENGAGEMENT_DIRECTOR
        # It runs after fact check, and the quality gate runs after it.
        assert "task_fact_checker" in closure.dependencies
        quality_task = dag.get_task("task_quality_gate")
        assert "task_gap_closure" in quality_task.dependencies

    def test_insert_is_idempotent(self):
        orch = self._orch()
        dag = _dag([_specialist_task()])
        orch._ensure_gap_closure_task(dag)
        n1 = len(dag.tasks)
        orch._ensure_gap_closure_task(dag)
        assert len(dag.tasks) == n1


class TestGapClosurePhase:
    def _orch(self):
        from hyperion.orchestrator import WorkflowEngine

        orch = WorkflowEngine.__new__(WorkflowEngine)
        orch._agents = {}
        orch._engagement_id = "e1"
        return orch

    def test_specialists_finalized_after_phase(self):
        import asyncio

        orch = self._orch()
        specialist = _specialist_task()
        specialist.status = TaskStatus.AWAITING_FOLLOWUP
        dag = _dag([specialist])

        asyncio.run(orch._gap_closure_phase(dag, gaps=[]))

        assert dag.get_task(specialist.id).status == TaskStatus.COMPLETED

    def test_round1_redispatches_originating_specialist_one_tier_up(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.models import AnalysisGap

        orch = self._orch()
        specialist_agent = AsyncMock()
        specialist_agent.run = AsyncMock(return_value=None)
        orch._agents = {AgentName.MARKET_ANALYST: specialist_agent}
        orch._get_agent = lambda name: orch._agents[name]  # type: ignore[method-assign]

        specialist = _specialist_task()
        specialist.status = TaskStatus.AWAITING_FOLLOWUP
        dag = _dag([specialist])
        gap = AnalysisGap(
            id="gap_1",
            section_id="section_market_analyst",
            agent=AgentName.MARKET_ANALYST,
            field="implications",
            question="What are the 'so what' implications?",
        )

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))

        # Round 1 re-dispatched the originating specialist with the question.
        assert specialist_agent.run.await_count >= 1
        kwargs = specialist_agent.run.await_args.kwargs
        assert gap.question in str(kwargs)


class TestGapClosureRounds:
    """P2-16 sub-fix 5.5: the 3-round closure ladder, and 5.6: an
    unresolvable gap omits the field and its question lands in limitations."""

    def _orch(self, agents: dict):
        from hyperion.orchestrator import WorkflowEngine

        orch = WorkflowEngine.__new__(WorkflowEngine)
        orch._agents = agents
        orch._engagement_id = "e1"
        orch._get_agent = lambda name: orch._agents[name]  # type: ignore[method-assign]
        return orch

    def _gap(self, agent: AgentName = AgentName.MARKET_ANALYST):
        from hyperion.schemas.models import AnalysisGap

        return AnalysisGap(
            id="gap_1",
            section_id="section_market_analyst",
            agent=agent,
            field="implications",
            question="What are the 'so what' implications?",
        )

    def test_round1_success_resolves_gap_no_further_rounds(self):
        import asyncio
        from unittest.mock import AsyncMock

        origin = AsyncMock()
        origin.run = AsyncMock(return_value={"finding": "resolved answer"})
        other = AsyncMock()
        other.run = AsyncMock(return_value=None)
        orch = self._orch({
            AgentName.MARKET_ANALYST: origin,
            AgentName.RISK_ANALYST: other,
        })

        specialist = _specialist_task()
        specialist.status = TaskStatus.AWAITING_FOLLOWUP
        risk_task = _specialist_task(AgentName.RISK_ANALYST)
        risk_task.status = TaskStatus.AWAITING_FOLLOWUP
        dag = _dag([specialist, risk_task])
        gap = self._gap()

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))

        assert gap.resolved is True
        assert gap.attempts == 1
        assert other.run.await_count == 0, "round 2 must not fire after round 1 resolves"

    def test_scope_round_uses_different_specialist_after_strategy_budget(self):
        """W-07: the 3 RETRY_STRATEGY rounds re-dispatch the ORIGINATING
        specialist (same question, different query construction); only after
        that budget is spent does a RETRY_SCOPE round go to a DIFFERENT live
        specialist with a recorded scope change."""
        import asyncio
        from unittest.mock import AsyncMock

        origin = AsyncMock()
        origin.run = AsyncMock(return_value=None)  # all 3 strategy rounds fail
        other = AsyncMock()
        other.run = AsyncMock(return_value={"finding": "scope-broadened answer"})
        orch = self._orch({
            AgentName.MARKET_ANALYST: origin,
            AgentName.RISK_ANALYST: other,
        })

        specialist = _specialist_task()
        specialist.status = TaskStatus.AWAITING_FOLLOWUP
        risk_task = _specialist_task(AgentName.RISK_ANALYST)
        risk_task.status = TaskStatus.AWAITING_FOLLOWUP
        dag = _dag([specialist, risk_task])
        gap = self._gap()

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))

        assert origin.run.await_count == 3, "3 strategy retries on the originator"
        assert other.run.await_count == 1, "first scope round reaches another specialist"
        assert gap.attempts == 4
        assert gap.resolved is True
        kwargs = other.run.await_args.kwargs
        assert "scope" in str(kwargs).lower()
        assert gap.question in str(kwargs)

    def test_full_budget_exhaustion_classifies_and_stays_unresolved(self):
        """W-07: 3 strategy + 2 scope rounds, all failing, leaves the gap
        unresolved and records a classification with the tried-triples log."""
        import asyncio
        from unittest.mock import AsyncMock

        origin = AsyncMock()
        origin.run = AsyncMock(return_value=None)
        other = AsyncMock()
        other.run = AsyncMock(return_value=None)
        orch = self._orch({
            AgentName.MARKET_ANALYST: origin,
            AgentName.RISK_ANALYST: other,
        })

        specialist = _specialist_task()
        specialist.status = TaskStatus.AWAITING_FOLLOWUP
        risk_task = _specialist_task(AgentName.RISK_ANALYST)
        risk_task.status = TaskStatus.AWAITING_FOLLOWUP
        dag = _dag([specialist, risk_task])
        gap = self._gap()

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))

        assert gap.attempts == 5, "3 strategy + 2 scope rounds maximum"
        assert gap.resolved is False
        # A classification was recorded with every tried triple logged.
        resolutions = orch._insufficiency_resolutions
        assert len(resolutions) == 1
        resolution = resolutions[0]
        assert resolution.gap_id == gap.id
        assert len(resolution.tried_triples) == 5
        # Every tried triple is distinct (non-repetition by construction).
        identities = [t.identity() for t in resolution.tried_triples]
        assert len(set(identities)) == len(identities)
        # The justification is retained in one sentence.
        assert resolution.justification

    def test_budget_is_5_dispatches_even_with_everything_failing(self):
        import asyncio
        from unittest.mock import AsyncMock

        origin = AsyncMock()
        origin.run = AsyncMock(return_value=None)
        other = AsyncMock()
        other.run = AsyncMock(return_value=None)
        orch = self._orch({
            AgentName.MARKET_ANALYST: origin,
            AgentName.RISK_ANALYST: other,
        })

        specialist = _specialist_task()
        specialist.status = TaskStatus.AWAITING_FOLLOWUP
        risk_task = _specialist_task(AgentName.RISK_ANALYST)
        risk_task.status = TaskStatus.AWAITING_FOLLOWUP
        dag = _dag([specialist, risk_task])
        gap = self._gap()

        asyncio.run(orch._gap_closure_phase(dag, gaps=[gap]))

        total = origin.run.await_count + other.run.await_count
        assert total == 5, "W-07 ladder is exactly 3 strategy + 2 scope dispatches"
        assert origin.run.await_count == 3, "strategy rounds stay with the originator"
        assert other.run.await_count == 2, "scope rounds rotate to another specialist"

    def test_unresolved_gap_question_recorded_in_limitations(self):
        """P2-16 rule 3 / sub-fix 5.6: a gap that survives all 3 rounds is
        declared in FinalReport.limitations with its specific question."""
        orch = self._orch({})
        gap = self._gap()
        gap.attempts = 3
        gap.resolved = False

        report = type("R", (), {"limitations": []})()
        orch._record_unresolved_gaps(report, [gap])

        assert any(gap.question in lim for lim in report.limitations)

    def test_resolved_gap_not_recorded_in_limitations(self):
        orch = self._orch({})
        gap = self._gap()
        gap.resolved = True
        gap.resolution = "answered"

        report = type("R", (), {"limitations": []})()
        orch._record_unresolved_gaps(report, [gap])

        assert report.limitations == []
