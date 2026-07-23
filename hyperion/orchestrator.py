"""
HYPERION Orchestrator — the WorkflowEngine that ties everything together.

This is NOT a generic "run the agents" wrapper. It is the execution engine
that implements the 5-stage dynamic workflow pipeline from ARCHITECTURE.md §4.9:

  Stage 1: Engagement Director decomposes question → WorkflowDAG
  Stage 2: Specialists execute in parallel (asyncio.gather) with dependencies
  Stage 3: Fact Checker verifies all findings (parallel with Synthesis)
  Stage 4: Synthesis Lead reconciles → FinalReport → Quality Gate scores
  Stage 5: Presentation Designer → Data Visualizer → Render Engine → PDF

The orchestrator:
- Instantiates agents lazily (only when their task is ready to run)
- Executes tasks in topological order (dependencies first)
- Runs independent tasks in parallel via asyncio.gather
- Monitors the AgentBus for escalations and adapts the DAG
- Tracks budget consumption across the entire engagement
- Produces an EngagementResult with the final PDF path and metadata
- Saves engagement context to Second Brain for future learning (§12.8)

The orchestrator is the glue between the Engagement Director's plan and
the actual execution. The Director plans; the orchestrator executes.

Architecture reference: §4.9 Dynamic Workflow Engine, §10.2 Adaptive Replanning
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from hyperion.agents.bus import Channel, MessageType, get_bus, reset_bus
from hyperion.agents.engagement_director import EngagementDirector
from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.obs import ArtifactStore, RunJournal, RunManifest, trace
from hyperion.schemas.agents import AgentName, AgentState
from hyperion.schemas.models import (
    FactCheckReport,
    FinalReport,
    LayoutPlan,
    QualityScore,
    RenderOutput,
    VisualizationOutput,
)
from hyperion.schemas.workflow import (
    EngagementMetadata,
    TaskNode,
    TaskStatus,
    WorkflowDAG,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Registry — maps AgentName to the actual agent class
# ─────────────────────────────────────────────────────────────────────────────


def _instantiate_agent(agent_name: AgentName, bus: Any = None, router: Any = None) -> Any:
    """Instantiate an agent by name.

    This is NOT a generic factory. Each agent has a specific class with
    a specific spec and a specific run() method. This function maps
    AgentName enum values to their concrete classes.

    Agents are instantiated lazily — only when their task is ready to run.
    This prevents loading all 20 agents into memory at once.
    """
    if agent_name == AgentName.ENGAGEMENT_DIRECTOR:
        return EngagementDirector(bus=bus, router=router)
    elif agent_name == AgentName.SYNTHESIS_LEAD:
        return SynthesisLead(bus=bus, router=router)
    elif agent_name == AgentName.MARKET_ANALYST:
        from hyperion.agents.specialists.market_analyst import MarketAnalyst
        return MarketAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.COMPETITIVE_INTEL:
        from hyperion.agents.specialists.competitive_intel import CompetitiveIntel
        return CompetitiveIntel(bus=bus, router=router)
    elif agent_name == AgentName.FINANCIAL_ANALYST:
        from hyperion.agents.specialists.financial_analyst import FinancialAnalyst
        return FinancialAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.RISK_ANALYST:
        from hyperion.agents.specialists.risk_analyst import RiskAnalyst
        return RiskAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.TECHNOLOGY_ANALYST:
        from hyperion.agents.specialists.technology_analyst import TechnologyAnalyst
        return TechnologyAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.OPERATIONS_ANALYST:
        from hyperion.agents.specialists.operations_analyst import OperationsAnalyst
        return OperationsAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.REGULATORY_ANALYST:
        from hyperion.agents.specialists.regulatory_analyst import RegulatoryAnalyst
        return RegulatoryAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.SUSTAINABILITY_ANALYST:
        from hyperion.agents.specialists.sustainability_analyst import SustainabilityAnalyst
        return SustainabilityAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.CONSUMER_INSIGHTS:
        from hyperion.agents.specialists.consumer_insights import ConsumerInsightsAnalyst
        return ConsumerInsightsAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.MA_ANALYST:
        from hyperion.agents.specialists.ma_analyst import MAAnalyst
        return MAAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.INNOVATION_ANALYST:
        from hyperion.agents.specialists.innovation_analyst import InnovationAnalyst
        return InnovationAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.STRATEGY_ANALYST:
        from hyperion.agents.specialists.strategy_analyst import StrategyAnalyst
        return StrategyAnalyst(bus=bus, router=router)
    elif agent_name == AgentName.RESEARCH_LIBRARIAN:
        from hyperion.agents.support.research_librarian import ResearchLibrarian
        return ResearchLibrarian(bus=bus, router=router)
    elif agent_name == AgentName.FACT_CHECKER:
        from hyperion.agents.support.fact_checker import FactChecker
        return FactChecker(bus=bus, router=router)
    elif agent_name == AgentName.DATA_VISUALIZER:
        from hyperion.agents.support.data_visualizer import DataVisualizer
        return DataVisualizer(bus=bus, router=router)
    elif agent_name == AgentName.QUALITY_GATE:
        from hyperion.agents.support.quality_gate import QualityGate
        return QualityGate(bus=bus, router=router)
    elif agent_name == AgentName.PRESENTATION_DESIGNER:
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner
        return PresentationDesigner(bus=bus, router=router)
    elif agent_name == AgentName.RENDER_ENGINE:
        from hyperion.agents.delivery.render_engine import RenderEngine
        return RenderEngine(bus=bus, router=router)
    else:
        raise ValueError(f"Unknown agent: {agent_name}")


# ─────────────────────────────────────────────────────────────────────────────
# Engagement Result — the output of a complete engagement
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class EngagementResult:
    """The result of a complete HYPERION engagement.

    This is the final output of the orchestrator. It contains:
    - The final PDF path (the deliverable)
    - The FinalReport model (the analysis)
    - The QualityScore (the rubric score)
    - Engagement metadata (for the methodology page and Second Brain)
    - Success/failure status
    """

    engagement_id: str = ""
    question: str = ""
    pdf_path: str = ""
    markdown_path: str = ""
    final_report: FinalReport | None = None
    quality_score: QualityScore | None = None
    fact_check_report: FactCheckReport | None = None
    layout_plan: LayoutPlan | None = None
    visualization_output: VisualizationOutput | None = None
    render_output: RenderOutput | None = None
    metadata: EngagementMetadata | None = None
    dag: WorkflowDAG | None = None
    success: bool = False
    error: str = ""
    duration_seconds: float = 0.0
    adaptation_count: int = 0
    escalation_count: int = 0
    quality_iterations: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "engagement_id": self.engagement_id,
            "question": self.question,
            "pdf_path": self.pdf_path,
            "markdown_path": self.markdown_path,
            "success": self.success,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "adaptation_count": self.adaptation_count,
            "escalation_count": self.escalation_count,
            "quality_iterations": self.quality_iterations,
            "quality_score": self.quality_score.model_dump() if self.quality_score else None,
            "final_report": self.final_report.model_dump() if self.final_report else None,
            "metadata": self.metadata.model_dump() if self.metadata else None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Workflow Engine — the orchestrator
# ─────────────────────────────────────────────────────────────────────────────


class WorkflowEngine:
    """The HYPERION Workflow Engine — executes the dynamic engagement DAG.

    This is NOT a generic task runner. It is the specific implementation of
    the 5-stage pipeline from §4.9:

    Stage 1: Engagement Director → WorkflowDAG (planning)
    Stage 2: Specialists execute in parallel with dependency resolution
    Stage 3: Fact Checker verifies findings (parallel with Synthesis)
    Stage 4: Synthesis Lead → FinalReport → Quality Gate (with iteration loop)
    Stage 5: Presentation Designer → Data Visualizer → Render Engine → PDF

    The engine:
    1. Receives the WorkflowDAG from the Engagement Director
    2. Instantiates agents lazily (only when their task is ready)
    3. Executes tasks in topological order — independent tasks in parallel
    4. Collects outputs from each agent and passes them to dependent agents
    5. Monitors the bus for escalations — the Director handles adaptive replanning
    6. Runs the Quality Gate iteration loop (max 3 iterations, §4.5 Agent 18)
    7. Produces the final PDF via the Render Engine
    8. Saves engagement context to Second Brain for future learning

    Usage:
        engine = WorkflowEngine()
        result = await engine.run_engagement(
            question="Should we enter the Tier-2 Indian SaaS market?",
            conversation_context="Client is a B2B SaaS company...",
        )
        if result.success:
            print(f"PDF: {result.pdf_path}")
    """

    MAX_QUALITY_ITERATIONS = 2  # P7: capped at ≤2 (was 3) — content-aware quality gate
    TASK_TIMEOUT_SECONDS = 600  # 10 minutes — default for most agents
    SPECIALIST_TIMEOUT_SECONDS = 1200  # 20 minutes — specialists spawn up to 3 sub-agents
    # Each sub-agent does SearxNG search + Jina read + LLM analysis.
    # With SearxNG semaphore=3 and multiple specialists in parallel,
    # and potentially slow network conditions, 600s was not enough —
    # specialists were timing out (MARKET, REGULATORY, INNOVATE, etc.)

    def __init__(self, bus: Any = None, router: Any = None) -> None:
        self.bus = bus or get_bus()
        self.router = router
        self._director: EngagementDirector | None = None
        self._agent_instances: dict[AgentName, Any] = {}
        self._task_outputs: dict[str, Any] = {}  # task_id → agent output
        self._all_findings: list[Any] = []  # collected from bus
        self._start_time: float = 0.0
        self._engagement_id: str = ""
        # P10: Durable execution — journal, artifact store, manifest
        self._journal: RunJournal | None = None
        self._artifacts: ArtifactStore | None = None
        self._manifest: RunManifest | None = None

    def _log(self, message: str) -> None:
        """Publish a log message to the TUI via Channel.TUI."""
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    self.bus.publish(
                        channel=Channel.TUI,
                        msg_type=MessageType.STATUS,
                        sender=AgentName.ENGAGEMENT_DIRECTOR,
                        payload={
                            "agent": "ORCHESTRATOR",
                            "tool": "system",
                            "action": "log",
                            "detail": message,
                        },
                    )
                )
        except Exception:
            pass

    def _publish_dag_to_tui(self, dag: WorkflowDAG) -> None:
        """Publish the full DAG task list to the TUI as a checklist."""
        try:
            import asyncio

            tasks_info = []
            for task in dag.tasks:
                tasks_info.append({
                    "id": task.id,
                    "agent": task.agent.value,
                    "tier": task.model_tier.value,
                    "status": task.status.value,
                    "description": task.description[:80],
                    "dependencies": task.dependencies,
                })

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    self.bus.publish(
                        channel=Channel.TUI,
                        msg_type=MessageType.STATUS,
                        sender=AgentName.ENGAGEMENT_DIRECTOR,
                        payload={
                            "agent": "ORCHESTRATOR",
                            "tool": "dag",
                            "action": "task_list",
                            "detail": f"{len(tasks_info)} tasks dispatched",
                            "tasks": tasks_info,
                        },
                    )
                )
        except Exception:
            pass

    def _publish_task_update(self, task: TaskNode) -> None:
        """Publish a single task's status change to the TUI."""
        try:
            import asyncio

            loop = asyncio.get_event_loop()
            if loop.is_running():
                loop.create_task(
                    self.bus.publish(
                        channel=Channel.TUI,
                        msg_type=MessageType.STATUS,
                        sender=AgentName.ENGAGEMENT_DIRECTOR,
                        payload={
                            "agent": "ORCHESTRATOR",
                            "tool": "task",
                            "action": "status",
                            "detail": f"{task.agent.value}: {task.status.value}",
                            "task_id": task.id,
                            "task_agent": task.agent.value,
                            "task_status": task.status.value,
                        },
                    )
                )
        except Exception:
            pass

    def _get_agent(self, agent_name: AgentName) -> Any:
        """Get or instantiate an agent lazily.

        Agents are singletons within an engagement — instantiated once,
        reused for subsequent tasks (e.g., if the same agent is re-run
        during a quality iteration).
        """
        if agent_name not in self._agent_instances:
            self._agent_instances[agent_name] = _instantiate_agent(
                agent_name, bus=self.bus, router=self.router
            )
        return self._agent_instances[agent_name]

    async def _execute_task(self, task: TaskNode, dag: WorkflowDAG) -> Any:
        """Execute a single task — instantiate the agent and call its run() method.

        This is NOT a generic "call the agent" function. It maps each task
        to the specific arguments that agent's run() method expects, based
        on the agent's role in the pipeline:

        - Specialists receive: question, engagement_id, context (prior findings)
        - Fact Checker receives: question, engagement_id, findings
        - Synthesis Lead receives: engagement_id, question, dag
        - Quality Gate receives: question, engagement_id, final_report, fact_check_report
        - Presentation Designer receives: question, engagement_id, final_report, quality_score
        - Data Visualizer receives: question, engagement_id, chart_specs
        - Render Engine receives: question, engagement_id, layout_plan

        The task is marked RUNNING before execution and COMPLETED/FAILED after.

        P10: Before executing, check the RunJournal for a cached result.
        If the step already succeeded with the same inputs, load the
        cached artifact and skip execution entirely.
        """
        # P10: Check journal for cached result (durable execution replay)
        inputs_hash = self._compute_step_hash(task, dag)
        if self._journal:
            cached = self._journal.get_cached(task.id, inputs_hash)
            if cached and cached.output_ref and self._artifacts:
                cached_data = self._artifacts.load(task.id)
                if cached_data is not None:
                    trace("journal", step_id=task.id, status="cache_hit",
                          agent=task.agent.value, run_id=self._engagement_id)
                    task.status = TaskStatus.COMPLETED
                    task.completed_at = time.time()
                    # Reconstruct the output from cached data
                    cached_obj = self._reconstruct_output(task.agent, cached_data)
                    if cached_obj is not None:
                        self._task_outputs[task.id] = cached_obj
                        self._publish_task_update(task)
                        # Re-collect findings from cached specialist outputs
                        if hasattr(cached_obj, "_findings"):
                            self._all_findings.extend(cached_obj._findings)
                        return cached_obj

        agent = self._get_agent(task.agent)
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._publish_task_update(task)

        # Build context from dependency outputs
        context: dict[str, Any] = {}
        for dep_id in task.dependencies:
            if dep_id in self._task_outputs:
                dep_output = self._task_outputs[dep_id]
                dep_task = dag.get_task(dep_id)
                if dep_task:
                    context[dep_task.agent.value] = dep_output

        try:
            # Call the agent's run() method with the right arguments
            if task.agent in (
                AgentName.MARKET_ANALYST, AgentName.COMPETITIVE_INTEL,
                AgentName.FINANCIAL_ANALYST, AgentName.RISK_ANALYST,
                AgentName.TECHNOLOGY_ANALYST, AgentName.OPERATIONS_ANALYST,
                AgentName.REGULATORY_ANALYST, AgentName.SUSTAINABILITY_ANALYST,
                AgentName.CONSUMER_INSIGHTS, AgentName.MA_ANALYST,
                AgentName.INNOVATION_ANALYST, AgentName.STRATEGY_ANALYST,
            ):
                # P7 GAP-2: Enrich context with MICRO LLM classifier to
                # populate industry/geography/sector/etc. from the question
                # so specialists' search queries are never empty.
                try:
                    enriched = await agent._enrich_context(task.description or dag.question)
                    if enriched:
                        context.update(enriched)
                except Exception:
                    pass

                # Specialists — use extended timeout (they spawn sub-agents)
                result = await asyncio.wait_for(
                    agent.run(
                        question=task.description,
                        engagement_id=self._engagement_id,
                        context=context if context else None,
                    ),
                    timeout=self.SPECIALIST_TIMEOUT_SECONDS,
                )

            elif task.agent == AgentName.FACT_CHECKER:
                # Fact Checker needs all findings
                result = await asyncio.wait_for(
                    agent.run(
                        question=dag.question,
                        engagement_id=self._engagement_id,
                        findings=self._all_findings or None,
                    ),
                    timeout=self.SPECIALIST_TIMEOUT_SECONDS,
                )

            elif task.agent == AgentName.SYNTHESIS_LEAD:
                # Synthesis Lead needs the DAG and all findings.
                # The Synthesis Lead subscribes to Channel.FINDINGS on the bus,
                # but it's instantiated lazily here — AFTER specialists have
                # already published their findings. Bus retention (D4 fix)
                # replays retained findings on subscription, but we also
                # inject the orchestrator's collected findings directly
                # as a belt-and-suspenders guarantee.
                if hasattr(agent, "_collected_findings"):
                    # Merge: don't duplicate findings already replayed by bus
                    existing_ids = {id(f) for f in agent._collected_findings}
                    for finding in self._all_findings:
                        if id(finding) not in existing_ids:
                            agent._collected_findings.append(finding)
                            agent_name = finding.agent
                            if agent_name not in agent._findings_by_agent:
                                agent._findings_by_agent[agent_name] = []
                            agent._findings_by_agent[agent_name].append(finding)
                else:
                    # Fallback: set attributes directly
                    agent._collected_findings = list(self._all_findings)
                    agent._findings_by_agent = {}
                    for finding in self._all_findings:
                        agent_name = finding.agent
                        if agent_name not in agent._findings_by_agent:
                            agent._findings_by_agent[agent_name] = []
                        agent._findings_by_agent[agent_name].append(finding)

                self._log(
                    f"SYNTHESIS: injected {len(self._all_findings)} findings "
                    f"(total in agent: {len(agent._collected_findings)})"
                )

                result = await asyncio.wait_for(
                    agent.run(
                        engagement_id=self._engagement_id,
                        question=dag.question,
                        dag=dag,
                    ),
                    timeout=self.SPECIALIST_TIMEOUT_SECONDS,
                )

            elif task.agent == AgentName.QUALITY_GATE:
                # Quality Gate needs FinalReport + FactCheckReport
                final_report = self._get_output_by_agent(dag, AgentName.SYNTHESIS_LEAD)
                fact_check = self._get_output_by_agent(dag, AgentName.FACT_CHECKER)
                viz_output = self._get_output_by_agent(dag, AgentName.DATA_VISUALIZER)
                result = await asyncio.wait_for(
                    agent.run(
                        question=dag.question,
                        engagement_id=self._engagement_id,
                        final_report=final_report,
                        fact_check_report=fact_check,
                        visualization_output=viz_output,
                    ),
                    timeout=self.TASK_TIMEOUT_SECONDS,
                )

            elif task.agent == AgentName.PRESENTATION_DESIGNER:
                # Presentation Designer needs FinalReport + QualityScore
                final_report = self._get_output_by_agent(dag, AgentName.SYNTHESIS_LEAD)
                quality_score = self._get_output_by_agent(dag, AgentName.QUALITY_GATE)
                viz_output = self._get_output_by_agent(dag, AgentName.DATA_VISUALIZER)
                result = await asyncio.wait_for(
                    agent.run(
                        question=dag.question,
                        engagement_id=self._engagement_id,
                        final_report=final_report,
                        quality_score=quality_score,
                        visualization_output=viz_output,
                    ),
                    timeout=self.TASK_TIMEOUT_SECONDS,
                )

            elif task.agent == AgentName.DATA_VISUALIZER:
                # Data Visualizer needs chart specs from Presentation Designer
                # or from the FinalReport's chart specifications
                final_report = self._get_output_by_agent(dag, AgentName.SYNTHESIS_LEAD)
                chart_specs: list[dict[str, Any]] = []
                if final_report and hasattr(final_report, "chart_specifications"):
                    chart_specs = final_report.chart_specifications or []
                result = await asyncio.wait_for(
                    agent.run(
                        question=dag.question,
                        engagement_id=self._engagement_id,
                        chart_specs=chart_specs if chart_specs else None,
                    ),
                    timeout=self.TASK_TIMEOUT_SECONDS,
                )

            elif task.agent == AgentName.RENDER_ENGINE:
                # Render Engine needs layout plan from Presentation Designer
                layout_plan = self._get_output_by_agent(dag, AgentName.PRESENTATION_DESIGNER)
                viz_output = self._get_output_by_agent(dag, AgentName.DATA_VISUALIZER)
                result = await asyncio.wait_for(
                    agent.run(
                        question=dag.question,
                        engagement_id=self._engagement_id,
                        layout_plan=layout_plan,
                    ),
                    timeout=self.TASK_TIMEOUT_SECONDS,
                )

            elif task.agent == AgentName.RESEARCH_LIBRARIAN:
                # Research Librarian
                result = await asyncio.wait_for(
                    agent.run(
                        question=task.description,
                        engagement_id=self._engagement_id,
                        context=context if context else None,
                    ),
                    timeout=self.TASK_TIMEOUT_SECONDS,
                )

            else:
                # Unknown agent type — try generic call
                result = await asyncio.wait_for(
                    agent.run(
                        question=task.description,
                        engagement_id=self._engagement_id,
                    ),
                    timeout=self.TASK_TIMEOUT_SECONDS,
                )

            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.output = result.model_dump() if hasattr(result, "model_dump") else str(result)
            self._task_outputs[task.id] = result
            self._publish_task_update(task)

            # P10: Record success in journal + save artifact
            if self._journal:
                output_ref = ""
                if self._artifacts:
                    output_ref = self._artifacts.save(task.id, result)
                self._journal.record_success(task.id, inputs_hash, output_ref)
                trace("journal", step_id=task.id, status="success",
                      agent=task.agent.value, run_id=self._engagement_id)

            # Collect findings for Fact Checker and Synthesis Lead
            if hasattr(agent, "_findings"):
                findings_count = len(agent._findings)
                self._all_findings.extend(agent._findings)
                self._log(
                    f"{task.agent.value}: completed with {findings_count} findings "
                    f"(total collected: {len(self._all_findings)})"
                )
            else:
                self._log(f"{task.agent.value}: completed (no findings attribute)")

            return result

        except asyncio.TimeoutError:
            timeout_used = (
                self.SPECIALIST_TIMEOUT_SECONDS
                if task.agent in (
                    AgentName.MARKET_ANALYST, AgentName.COMPETITIVE_INTEL,
                    AgentName.FINANCIAL_ANALYST, AgentName.RISK_ANALYST,
                    AgentName.TECHNOLOGY_ANALYST, AgentName.OPERATIONS_ANALYST,
                    AgentName.REGULATORY_ANALYST, AgentName.SUSTAINABILITY_ANALYST,
                    AgentName.CONSUMER_INSIGHTS, AgentName.MA_ANALYST,
                    AgentName.INNOVATION_ANALYST, AgentName.STRATEGY_ANALYST,
                )
                else self.TASK_TIMEOUT_SECONDS
            )
            task.status = TaskStatus.FAILED
            task.error = f"Task timed out after {timeout_used}s"
            self._publish_task_update(task)
            if self._journal:
                self._journal.record_failure(task.id, inputs_hash, f"timeout:{timeout_used}s")
            await self.bus.publish_status(
                task.agent, AgentState.BLOCKED,
                detail=f"timed out after {timeout_used}s",
            )
            return None
        except (ValueError, RuntimeError, OSError) as e:
            task.status = TaskStatus.FAILED
            task.error = str(e)
            self._publish_task_update(task)
            if self._journal:
                self._journal.record_failure(task.id, inputs_hash, str(e)[:500])
            await self.bus.publish_status(
                task.agent, AgentState.BLOCKED,
                detail=str(e)[:200],
            )
            return None

    def _get_output_by_agent(self, dag: WorkflowDAG, agent_name: AgentName) -> Any:
        """Get the output of a completed task by agent name."""
        for task in dag.tasks:
            if task.agent == agent_name and task.id in self._task_outputs:
                return self._task_outputs[task.id]
        return None

    def _compute_step_hash(self, task: TaskNode, dag: WorkflowDAG) -> str:
        """P10: Compute a deterministic hash of a step's inputs.

        The inputs hash includes the task description, agent, dependencies'
        outputs, and the engagement question — so any change in inputs
        invalidates the cache and forces re-execution.
        """
        if self._journal is None:
            return ""
        inputs: dict[str, Any] = {
            "agent": task.agent.value,
            "description": task.description,
            "question": dag.question,
        }
        for dep_id in task.dependencies:
            if dep_id in self._task_outputs:
                dep_output = self._task_outputs[dep_id]
                if hasattr(dep_output, "model_dump"):
                    inputs[dep_id] = dep_output.model_dump()
                else:
                    inputs[dep_id] = str(dep_output)[:500]
        return self._journal.compute_inputs_hash(inputs)

    def _reconstruct_output(self, agent_name: AgentName, cached_data: Any) -> Any:
        """P10: Reconstruct a typed output object from cached JSON data.

        Maps each agent to its expected output type so we can rebuild
        the Pydantic model from the stored JSON artifact.
        """
        try:
            if agent_name == AgentName.SYNTHESIS_LEAD:
                return FinalReport.model_validate(cached_data)
            elif agent_name == AgentName.QUALITY_GATE:
                return QualityScore.model_validate(cached_data)
            elif agent_name == AgentName.PRESENTATION_DESIGNER:
                return LayoutPlan.model_validate(cached_data)
            elif agent_name == AgentName.RENDER_ENGINE:
                return RenderOutput.model_validate(cached_data)
            elif agent_name == AgentName.DATA_VISUALIZER:
                return VisualizationOutput.model_validate(cached_data)
            elif agent_name == AgentName.FACT_CHECKER:
                return FactCheckReport.model_validate(cached_data)
            else:
                # For specialists and others, return the raw dict —
                # downstream consumers handle both typed and dict outputs
                if isinstance(cached_data, dict):
                    return cached_data
                return cached_data
        except Exception:
            # If reconstruction fails, return raw data — better than crashing
            return cached_data

    async def _execute_wave(self, tasks: list[TaskNode], dag: WorkflowDAG) -> list[Any]:
        """Execute a wave of independent tasks in parallel via asyncio.gather.

        Tasks in the same wave have no dependencies on each other — they
        can all run simultaneously. This is the parallelism that makes
        HYPERION fast. (§4.9: "Tasks with no dependencies run in parallel")
        """
        if not tasks:
            return []

        coroutines = [self._execute_task(task, dag) for task in tasks]
        results = await asyncio.gather(*coroutines, return_exceptions=True)

        # Handle exceptions — don't let one failure kill the wave
        processed: list[Any] = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                tasks[i].status = TaskStatus.FAILED
                tasks[i].error = str(result)
                await self.bus.publish_status(
                    tasks[i].agent, AgentState.BLOCKED,
                    detail=str(result)[:200],
                )
                processed.append(None)
            else:
                processed.append(result)

        return processed

    # Delivery agents that must NOT run during _execute_dag — they run
    # AFTER the quality iteration loop on the final iterated report.
    _DELIVERY_AGENTS = frozenset({
        AgentName.PRESENTATION_DESIGNER,
        AgentName.DATA_VISUALIZER,
        AgentName.RENDER_ENGINE,
    })

    # Quality Gate is also excluded from _execute_dag because it runs
    # in _quality_iteration_loop with proper iteration tracking.
    # Running it in both places causes double-execution and wasted LLM calls.
    _DAG_EXCLUDED_AGENTS = _DELIVERY_AGENTS | frozenset({AgentName.QUALITY_GATE})

    async def _execute_dag(self, dag: WorkflowDAG) -> dict[str, Any]:
        """Execute the DAG in topological order — specialists through Quality Gate.

        This runs Stages 1-4: specialists → fact checker → synthesis → quality gate.
        Delivery tasks (Presentation Designer, Data Visualizer, Render Engine) are
        deliberately skipped here — they run AFTER the quality iteration loop
        on the final iterated report, not on the initial draft.
        """
        max_iterations = 100  # Safety valve — prevent infinite loops
        iteration = 0

        while not dag.is_complete and iteration < max_iterations:
            iteration += 1

            # Get ready tasks (pending with all dependencies met)
            ready_tasks = dag.get_ready_tasks()
            if not ready_tasks:
                # No ready tasks but DAG not complete — check for deadlocks
                running = dag.get_running_tasks()
                if not running:
                    # Deadlock — no tasks running and none ready
                    # Mark remaining tasks as failed
                    for task in dag.tasks:
                        if task.status == TaskStatus.PENDING:
                            task.status = TaskStatus.FAILED
                            task.error = "Deadlock — dependencies never satisfied"
                            await self.bus.publish_status(
                                task.agent, AgentState.BLOCKED,
                                detail="deadlock — dependencies never satisfied",
                            )
                    break
                # Wait for running tasks to complete
                await asyncio.sleep(0.5)
                continue

            # Filter out delivery + quality gate tasks — they run after
            # specialists complete (quality gate runs in _quality_iteration_loop)
            ready_non_delivery = [
                t for t in ready_tasks
                if t.agent not in self._DAG_EXCLUDED_AGENTS
            ]

            # If all ready tasks were excluded, mark them as skipped
            # (they'll be re-run after quality iteration)
            if not ready_non_delivery and ready_tasks:
                for task in ready_tasks:
                    if task.agent in self._DAG_EXCLUDED_AGENTS:
                        task.status = TaskStatus.PENDING  # Stay pending for later
                # Check if there are any non-excluded tasks left to run
                remaining = [
                    t for t in dag.tasks
                    if t.status == TaskStatus.PENDING
                    and t.agent not in self._DAG_EXCLUDED_AGENTS
                ]
                if not remaining:
                    break  # All non-delivery tasks done — exit loop
                await asyncio.sleep(0.1)
                continue

            # Execute the wave (non-delivery tasks only)
            await self._execute_wave(ready_non_delivery, dag)

            # Brief yield to allow bus messages to propagate
            await asyncio.sleep(0.1)

        # Collect all outputs
        return dict(self._task_outputs)

    async def _quality_iteration_loop(
        self,
        dag: WorkflowDAG,
        final_report: FinalReport,
        fact_check_report: FactCheckReport | None,
    ) -> tuple[FinalReport, QualityScore, int]:
        """Run the Quality Gate iteration loop (§4.5 Agent 18).

        The Quality Gate scores the report on a 10-dimension rubric.
        If score < 4.0/5.0, the Synthesis Lead iterates with targeted
        fixes. Max 3 iterations before escalation.

        Returns: (final_report, quality_score, iterations_run)
        """
        quality_agent = self._get_agent(AgentName.QUALITY_GATE)
        synthesis_agent = self._get_agent(AgentName.SYNTHESIS_LEAD)

        current_report = final_report
        current_score: QualityScore | None = None
        iterations = 0

        # Get visualization output for visual quality scoring (Dimension 10)
        viz_output = self._get_output_by_agent(dag, AgentName.DATA_VISUALIZER)

        # P7: Content-aware source-count floor — if the report has fewer
        # sources than this, stop iterating because more passes won't fix
        # thin evidence; the problem is insufficient data, not insufficient
        # synthesis.  This prevents wasteful LLM calls on thin reports.
        source_floor = 3
        try:
            from hyperion.config import get_settings
            _cfg = get_settings()
            source_floor = getattr(_cfg, "quality_source_floor", 3)
        except Exception:
            pass

        for iteration in range(1, self.MAX_QUALITY_ITERATIONS + 1):
            iterations = iteration

            # Score the report
            current_score = await asyncio.wait_for(
                quality_agent.run(
                    question=dag.question,
                    engagement_id=self._engagement_id,
                    final_report=current_report,
                    fact_check_report=fact_check_report,
                    visualization_output=viz_output,
                    iteration=iteration,
                ),
                timeout=self.SPECIALIST_TIMEOUT_SECONDS,
            )

            self._log(
                f"QUALITY iteration {iteration}/{self.MAX_QUALITY_ITERATIONS}: "
                f"score={current_score.total_score:.1f}/{current_score.threshold:.1f} "
                f"approved={current_score.approved} "
                f"critical={len(current_score.critical_dimensions)} "
                f"gaps={len(current_score.gaps)}"
            )

            if current_score is None:
                break

            # Check if score meets threshold (≥ 4.0/5.0)
            if current_score.total_score >= 4.0:
                self._log(f"QUALITY: threshold met at iteration {iteration}")
                break  # Quality threshold met

            # P7: Content-aware stop — if source count is below floor, stop
            # iterating.  More synthesis passes won't fix thin evidence.
            report_sources = getattr(current_report, "total_sources", 0)
            if report_sources < source_floor:
                self._log(
                    f"QUALITY: content-aware stop — only {report_sources} sources "
                    f"(< floor {source_floor}). More iterations won't fix thin evidence."
                )
                current_score.max_iterations_reached = True
                break

            # Score below threshold — iterate with targeted fixes
            if iteration < self.MAX_QUALITY_ITERATIONS:
                # Synthesis Lead applies targeted fixes to the specific
                # dimensions that scored below 4 (not a full re-synthesis)
                self._log(
                    f"SYNTHESIS: starting iteration {iteration + 1} — "
                    f"fixing {sum(1 for d in current_score.dimensions if d.score < 4)} dimensions"
                )
                fixed_report = await asyncio.wait_for(
                    synthesis_agent.iterate_on_quality(current_score),
                    timeout=self.SPECIALIST_TIMEOUT_SECONDS,
                )
                if fixed_report is not None:
                    current_report = fixed_report
                    self._log(f"SYNTHESIS: iteration {iteration + 1} complete — report updated")
                else:
                    self._log(f"SYNTHESIS: iteration {iteration + 1} returned None — using unchanged report")
            else:
                self._log(f"QUALITY: max iterations ({self.MAX_QUALITY_ITERATIONS}) reached — proceeding with best available")

        # If we exhausted all iterations without approval, mark it so
        # delivery agents know to proceed with the best available report
        if current_score and not current_score.approved and iterations >= self.MAX_QUALITY_ITERATIONS:
            current_score.max_iterations_reached = True

        # Mark the quality_gate task as COMPLETED in the DAG so that
        # delivery tasks (presentation_designer, etc.) that depend on
        # task_quality_gate can proceed.
        for task in dag.tasks:
            if task.agent == AgentName.QUALITY_GATE and task.status != TaskStatus.COMPLETED:
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                task.output = current_score.model_dump() if hasattr(current_score, "model_dump") else str(current_score)
                self._task_outputs[task.id] = current_score
                self._publish_task_update(task)
                break

        return current_report, current_score or QualityScore(
            dimensions=[],
            total_score=0.0,
            approved=False,
            iteration=iterations,
            gaps=["Quality Gate did not produce a score"],
            critical_dimensions=[],
            max_iterations_reached=True,
        ), iterations

    def _build_floor_report(self, question: str) -> FinalReport | None:
        """Build a minimal floor-report from collected findings (D13 fix).

        When the Synthesis Lead fails or times out, we still need a
        FinalReport so the delivery pipeline (Presentation Designer →
        Data Visualizer → Render Engine) can produce a PDF. This floor
        report is a best-effort synthesis of whatever findings were
        collected — not a full reconciliation, but enough to generate
        a deliverable.
        """
        from hyperion.schemas.models import (
            AnalysisSection,
            ConfidenceLevel,
            KeyFinding,
            Recommendation,
        )

        findings = self._all_findings
        if not findings:
            self._log("FLOOR-REPORT: no findings collected — cannot build floor report")
            return None

        # Group findings by agent
        by_agent: dict[str, list[KeyFinding]] = {}
        for f in findings:
            agent_name = f.agent if isinstance(f.agent, str) else str(f.agent)
            if agent_name not in by_agent:
                by_agent[agent_name] = []
            by_agent[agent_name].append(f)

        # Build sections from each agent's findings
        sections: list[AnalysisSection] = []
        for agent_name, agent_findings in by_agent.items():
            content_parts = []
            for f in agent_findings:
                content_parts.append(
                    f"**{f.title}** (confidence: {f.confidence.value})\n\n{f.content[:500]}"
                )
            sections.append(AnalysisSection(
                id=f"floor_{agent_name}",
                title=agent_name.replace("_", " ").title(),
                agent=agent_name,
                key_insight=agent_findings[0].title if agent_findings else "No key insight available",
                body="\n\n---\n\n".join(content_parts) or "No content available",
                findings=agent_findings,
                implications="Floor report — implications not synthesized.",
                confidence=ConfidenceLevel.LOW,
            ))

        # Build key findings list (top 5 by confidence)
        confidence_order = {
            ConfidenceLevel.HIGH: 0,
            ConfidenceLevel.MEDIUM: 1,
            ConfidenceLevel.LOW: 2,
        }
        key_findings = sorted(
            findings,
            key=lambda f: confidence_order.get(f.confidence, 3),
        )[:5]

        # Build executive summary from findings
        summary_lines = [
            f"This report was generated as a floor-report fallback because the "
            f"Synthesis Lead did not produce a full synthesis. It contains "
            f"{len(findings)} findings from {len(by_agent)} specialists.",
            "",
        ]
        for f in key_findings:
            summary_lines.append(f"- {f.title}: {f.content[:150]}")

        return FinalReport(
            engagement_id=self._engagement_id,
            question=question,
            recommendation=Recommendation.INVESTIGATE,
            recommendation_rationale=(
                "Insufficient synthesis — the Synthesis Lead did not complete. "
                "Recommendation defaults to INVESTIGATE pending full analysis. "
                f"Floor report assembled from {len(findings)} findings."
            ),
            critical_assumptions=[
                "Full synthesis was not completed — findings are not reconciled.",
                "Contradictions between agents may exist and are not resolved.",
            ],
            confidence=ConfidenceLevel.LOW,
            confidence_breakdown={agent: ConfidenceLevel.LOW for agent in by_agent},
            executive_summary="\n".join(summary_lines),
            key_findings=key_findings,
            sections=sections,
            agents_used=list(by_agent.keys()),
            total_sources=sum(1 for f in findings if hasattr(f, "sources") and f.sources),
            total_data_points=len(findings),
            limitations=[
                "Full synthesis was not completed.",
                "Contradictions are not resolved.",
                "Quality may be below standard threshold.",
            ],
        )

    async def _save_to_second_brain(self, result: EngagementResult) -> None:
        """Save engagement context to the Second Brain vault for future learning.

        HYPERION is a learning system (§12.8). Every engagement saves:
        - The question and question type
        - The agents used and their findings
        - The final recommendation and confidence
        - The quality score
        - Key sources accessed

        This makes the system smarter over time — future engagements on
        similar topics can retrieve this context via the Second Brain.
        """
        if not result.final_report or not result.metadata:
            return

        try:
            from hyperion.tools.second_brain import SecondBrainClient
            from hyperion.config import get_settings

            settings = get_settings()
            brain = SecondBrainClient(settings=settings)

            # Save as an engagement note
            note_content = (
                f"# Engagement: {result.question}\n\n"
                f"**Date:** {time.strftime('%Y-%m-%d')}\n"
                f"**ID:** {result.engagement_id}\n"
                f"**Question Type:** {result.dag.question_type.value if result.dag else 'unknown'}\n"
                f"**Recommendation:** {result.final_report.recommendation.value}\n"
                f"**Confidence:** {result.final_report.confidence.value}\n"
                f"**Quality Score:** {result.quality_score.total_score:.1f}/5.0\n"
                f"**Duration:** {result.duration_seconds:.0f}s\n"
                f"**Agents Used:** {', '.join(a.value for a in result.metadata.agents_used)}\n"
                f"**Sources Accessed:** {result.metadata.sources_accessed}\n"
                f"**LLM Calls:** {result.metadata.llm_calls_made}\n"
                f"**Adaptations:** {result.adaptation_count}\n\n"
                f"## Rationale\n{result.final_report.recommendation_rationale}\n\n"
                f"## Critical Assumptions\n"
            )
            for assumption in result.final_report.critical_assumptions:
                note_content += f"- {assumption}\n"

            await brain.save_note(
                category="engagements",
                filename=f"engagement-{result.engagement_id}",
                title=f"Engagement {result.engagement_id}: {result.question[:60]}",
                content=note_content,
                tags=[
                    result.dag.question_type.value if result.dag else "general",
                    result.final_report.recommendation.value,
                    result.final_report.confidence.value,
                ],
            )
        except (ImportError, ValueError, OSError, RuntimeError):
            # Second Brain save is best-effort — don't fail the engagement
            pass

    async def _generate_markdown(
        self,
        final_report: FinalReport,
        engagement_id: str,
    ) -> str:
        """Generate a markdown export of the report for TUI display.

        The TUI Deliverable View (§8.2) can display the report as markdown
        using Rich Markdown rendering, in addition to the PDF.
        """
        try:
            from hyperion.output.markdown import MarkdownExporter

            exporter = MarkdownExporter()
            report_dict = final_report.model_dump()
            result = exporter.export_to_file(report_dict)
            return result.file_path if result.success else ""
        except (ImportError, ValueError, OSError, RuntimeError, TypeError, AttributeError):
            return ""

    # ─────────────────────────────────────────────────────────────────────────
    # Main entry point — run a complete engagement
    # ─────────────────────────────────────────────────────────────────────────

    async def run_engagement(
        self,
        question: str,
        conversation_context: str = "",
    ) -> EngagementResult:
        """Run a complete HYPERION engagement from question to PDF.

        This is the main entry point. It executes the full 5-stage pipeline:

        Stage 1: Engagement Director decomposes question → WorkflowDAG
        Stage 2: Specialists execute in parallel with dependency resolution
        Stage 3: Fact Checker verifies findings (parallel with Synthesis)
        Stage 4: Synthesis Lead → FinalReport → Quality Gate (with iteration)
        Stage 5: Presentation Designer → Data Visualizer → Render Engine → PDF

        Returns an EngagementResult with the PDF path and all metadata.
        """
        self._start_time = time.time()
        self._engagement_id = f"eng_{uuid.uuid4().hex[:12]}"

        # P10: Durable execution — open journal, artifact store, manifest
        self._journal = RunJournal(self._engagement_id)
        self._journal.open()
        self._artifacts = ArtifactStore(self._engagement_id)
        self._manifest = RunManifest(
            run_id=self._engagement_id,
            question=question,
            conversation_context=conversation_context,
        )
        self._manifest.save()
        trace("durable", run_id=self._engagement_id, status="journal_opened")

        # P9 GAP-4: Startup health table — check every tool + tier
        try:
            from hyperion.obs.health import check_startup_health
            from hyperion.config import get_settings
            check_startup_health(get_settings())
        except Exception:
            pass  # Health check is best-effort — don't block the pipeline

        # Reset SearxNG search budget for this engagement
        from hyperion.tools.searxng import SearxNGClient
        SearxNGClient.reset_budget()

        # Use the existing bus if it's already running (TUI scenario),
        # otherwise create a fresh one for headless mode
        existing_bus = get_bus()
        if existing_bus._running:
            self.bus = existing_bus
        else:
            reset_bus()
            self.bus = get_bus()
            await self.bus.start()

        # Clear retained findings from any previous engagement (D4 fix)
        self.bus.clear_retained_findings()

        result = EngagementResult(
            engagement_id=self._engagement_id,
            question=question,
        )

        try:
            # ─────────────────────────────────────────────────────────────
            # Stage 1: Engagement Director — decompose and plan
            # ─────────────────────────────────────────────────────────────
            self._director = EngagementDirector(bus=self.bus, router=self.router)
            dag = await self._director.run(
                question=question,
                conversation_context=conversation_context,
            )
            result.dag = dag

            # Publish the DAG task list to the TUI so the user sees a
            # real-time checklist of all tasks and their statuses.
            self._publish_dag_to_tui(dag)

            # ─────────────────────────────────────────────────────────────
            # Stage 2-4: Execute the DAG (specialists → fact check → synthesis → quality)
            # ─────────────────────────────────────────────────────────────
            await self._execute_dag(dag)

            # Collect key outputs
            final_report = self._get_output_by_agent(dag, AgentName.SYNTHESIS_LEAD)
            fact_check_report = self._get_output_by_agent(dag, AgentName.FACT_CHECKER)

            if not final_report:
                # D13 fix: Build a floor-report fallback from collected findings
                # so delivery (PDF generation) always runs, even if synthesis failed.
                self._log(
                    f"SYNTHESIS: no FinalReport produced — building floor-report fallback "
                    f"from {len(self._all_findings)} collected findings"
                )
                final_report = self._build_floor_report(dag.question)
                if final_report is None:
                    result.error = "Synthesis Lead did not produce a FinalReport and floor-report fallback failed"
                    result.duration_seconds = time.time() - self._start_time
                    return result
                # Mark synthesis task as completed with the floor report
                for task in dag.tasks:
                    if task.agent == AgentName.SYNTHESIS_LEAD and task.status != TaskStatus.COMPLETED:
                        task.status = TaskStatus.COMPLETED
                        task.completed_at = time.time()
                        task.output = final_report.model_dump()
                        self._task_outputs[task.id] = final_report
                        self._publish_task_update(task)
                        break

            result.final_report = final_report
            result.fact_check_report = fact_check_report

            # ─────────────────────────────────────────────────────────────
            # Stage 4b: Quality Gate iteration loop
            # ─────────────────────────────────────────────────────────────
            final_report, quality_score, iterations = await self._quality_iteration_loop(
                dag, final_report, fact_check_report
            )
            result.final_report = final_report
            result.quality_score = quality_score
            result.quality_iterations = iterations

            # Update the task outputs with the iterated report
            self._task_outputs["task_synthesis_lead"] = final_report
            self._task_outputs["task_quality_gate"] = quality_score

            # D4-rest: Explicit FinalReport HANDOFF on the bus so delivery
            # agents receive it via their subscription, not just via local var.
            await self.bus.publish(
                channel=Channel.HANDOFF,
                msg_type=MessageType.HANDOFF,
                sender=AgentName.SYNTHESIS_LEAD,
                payload={
                    "to_agent": "presentation_designer",
                    "final_report": final_report.model_dump(),
                    "quality_score": quality_score.model_dump() if quality_score else None,
                },
            )

            # ─────────────────────────────────────────────────────────────
            # Stage 5: Delivery — Presentation Designer → Data Viz → Render
            # ─────────────────────────────────────────────────────────────
            # Execute remaining delivery tasks
            delivery_tasks = [
                t for t in dag.tasks
                if t.agent in (
                    AgentName.PRESENTATION_DESIGNER,
                    AgentName.DATA_VISUALIZER,
                    AgentName.RENDER_ENGINE,
                )
                and t.status == TaskStatus.PENDING
            ]

            # Execute delivery tasks in order (they have dependencies)
            self._log(f"DELIVERY: starting {len(delivery_tasks)} delivery tasks")
            for task in delivery_tasks:
                if task.status == TaskStatus.PENDING:
                    # Check if dependencies are met
                    ready = all(
                        dag.get_task(dep) and dag.get_task(dep).status == TaskStatus.COMPLETED
                        for dep in task.dependencies
                    )
                    if ready:
                        self._log(f"DELIVERY: executing {task.agent.value}")
                        try:
                            await self._execute_task(task, dag)
                        except Exception as e:
                            # D4-rest: Escalate delivery failure instead of crashing
                            self._log(f"DELIVERY: {task.agent.value} failed: {e!s:.200}")
                            await self.bus.publish(
                                channel=Channel.ESCALATION,
                                msg_type=MessageType.ESCALATION,
                                sender=task.agent,
                                payload={
                                    "agent": task.agent.value,
                                    "issue": f"Delivery agent failed: {e!s:.200}",
                                    "suggested_action": "Proceed with partial output or generate stub PDF",
                                },
                            )
                            task.status = TaskStatus.FAILED
                            task.error = str(e)[:200]
                    else:
                        self._log(f"DELIVERY: {task.agent.value} dependencies not met — skipping")

            # Collect delivery outputs
            result.layout_plan = self._get_output_by_agent(dag, AgentName.PRESENTATION_DESIGNER)
            result.visualization_output = self._get_output_by_agent(dag, AgentName.DATA_VISUALIZER)
            result.render_output = self._get_output_by_agent(dag, AgentName.RENDER_ENGINE)

            # Get PDF path
            if result.render_output and hasattr(result.render_output, "pdf_path"):
                result.pdf_path = result.render_output.pdf_path
            elif result.layout_plan and hasattr(result.layout_plan, "pdf_path"):
                result.pdf_path = result.layout_plan.pdf_path

            self._log(
                f"DELIVERY: complete — PDF={'YES' if result.pdf_path else 'NO'} "
                f"layout={'YES' if result.layout_plan else 'NO'} "
                f"viz={'YES' if result.visualization_output else 'NO'}"
            )

            # Generate markdown export
            result.markdown_path = await self._generate_markdown(
                final_report, self._engagement_id
            )

            # ─────────────────────────────────────────────────────────────
            # Build engagement metadata
            # ─────────────────────────────────────────────────────────────
            result.metadata = EngagementMetadata(
                engagement_id=self._engagement_id,
                question=question,
                question_type=dag.question_type,
                agents_used=dag.agents_selected,
                sources_accessed=sum(
                    1 for f in self._all_findings if hasattr(f, "sources") and f.sources
                ),
                data_points_collected=len(self._all_findings),
                duration_seconds=time.time() - self._start_time,
                llm_calls_made=sum(t.estimated_llm_calls for t in dag.tasks if t.status == TaskStatus.COMPLETED),
                tokens_consumed=sum(t.estimated_tokens for t in dag.tasks if t.status == TaskStatus.COMPLETED),
                sub_agents_spawned=sum(
                    len(t.sub_agents) for t in dag.tasks if t.status == TaskStatus.COMPLETED
                ),
                quality_iterations=iterations,
                final_quality_score=quality_score.total_score if quality_score else None,
            )

            result.duration_seconds = time.time() - self._start_time
            result.adaptation_count = len(dag.adaptation_log)
            result.escalation_count = self._director.get_escalation_count() if self._director else 0
            result.success = True

            self._log(
                f"ENGAGEMENT COMPLETE: success={result.success} "
                f"duration={result.duration_seconds:.0f}s "
                f"quality={quality_score.total_score:.1f}/{quality_score.threshold:.1f} "
                f"iterations={iterations} "
                f"PDF={'YES' if result.pdf_path else 'NO'}"
            )

            # P13 GAP-6: Completion health table
            try:
                from hyperion.obs.health import print_completion_health
                print_completion_health(result)
            except Exception:
                pass

            # P10: Save final manifest metrics
            if self._manifest:
                self._manifest.record_final_metrics(
                    duration_seconds=result.duration_seconds,
                    quality_score=quality_score.total_score if quality_score else None,
                    pdf_path=result.pdf_path,
                    success=result.success,
                    llm_calls=result.metadata.llm_calls_made if result.metadata else 0,
                    tokens_consumed=result.metadata.tokens_consumed if result.metadata else 0,
                )
                self._manifest.save()
                trace("durable", run_id=self._engagement_id, status="manifest_saved",
                      success=result.success, duration=result.duration_seconds)

            # ─────────────────────────────────────────────────────────────
            # Save to Second Brain for future learning (§12.8)
            # ─────────────────────────────────────────────────────────────
            await self._save_to_second_brain(result)

            # ─────────────────────────────────────────────────────────────
            # End-of-run summary
            # ─────────────────────────────────────────────────────────────
            self._print_run_summary(result)

            return result

        except (ValueError, RuntimeError, OSError, asyncio.TimeoutError) as e:
            result.error = str(e)
            result.duration_seconds = time.time() - self._start_time
            self._log(f"ENGAGEMENT FAILED: {type(e).__name__}: {e}")
            # P10: Record failure in manifest
            if self._manifest:
                self._manifest.record_final_metrics(
                    duration_seconds=result.duration_seconds,
                    quality_score=None,
                    pdf_path="",
                    success=False,
                )
                self._manifest.save()
            # Print summary even on failure
            self._print_run_summary(result)
            return result
        finally:
            # P10: Close journal
            if self._journal:
                self._journal.close()

    def _print_run_summary(self, result: EngagementResult) -> None:
        """Print end-of-run summary with report link, timing, status, and token breakdown."""
        duration = result.duration_seconds
        mins = int(duration // 60)
        secs = int(duration % 60)

        # Build separator
        sep = "=" * 72

        print(f"\n{sep}")
        print("  HYPERION ENGAGEMENT SUMMARY")
        print(sep)

        # Status
        status_str = "SUCCESS" if result.success else "FAILED"
        print(f"  Status:          {status_str}")
        if result.error and not result.success:
            print(f"  Error:           {result.error[:200]}")

        # Report links
        print(f"  PDF Report:      {result.pdf_path or 'N/A'}")
        print(f"  Markdown Report: {result.markdown_path or 'N/A'}")

        # Timing
        print(f"  Duration:        {mins}m {secs}s ({duration:.1f}s)")

        # Quality
        if result.quality_score:
            print(f"  Quality Score:   {result.quality_score.total_score:.1f}/5.0")
            print(f"  Quality Iters:   {result.quality_iterations}")
        else:
            print(f"  Quality Score:   N/A")

        # Agents & findings
        if result.dag:
            agents = ", ".join(a.value for a in result.dag.agents_selected)
            print(f"  Agents Used:     {agents}")
        print(f"  Adaptations:     {result.adaptation_count}")
        print(f"  Escalations:     {result.escalation_count}")

        # Token breakdown by provider
        token_summary: dict[str, Any] = {}
        if self.router and hasattr(self.router, "get_token_summary"):
            try:
                token_summary = self.router.get_token_summary()
            except Exception:
                pass

        total_tokens = token_summary.get("total_tokens", 0)
        total_calls = token_summary.get("total_calls", 0)
        print(f"\n  Total Tokens:    {total_tokens:,}")
        print(f"  Total LLM Calls: {total_calls:,}")

        by_provider = token_summary.get("by_provider", {})
        if by_provider:
            print(f"\n  Token Breakdown by Provider:")
            print(f"  {'Provider':<16} {'Input':>10} {'Output':>10} {'Total':>12} {'Calls':>8}")
            print(f"  {'-' * 16} {'-' * 10} {'-' * 10} {'-' * 12} {'-' * 8}")
            for provider_name in sorted(by_provider.keys()):
                stats = by_provider[provider_name]
                print(
                    f"  {provider_name:<16} "
                    f"{stats['input_tokens']:>10,} "
                    f"{stats['output_tokens']:>10,} "
                    f"{stats['total_tokens']:>12,} "
                    f"{stats['calls']:>8,}"
                )

        print(sep)
        print()

    async def close(self) -> None:
        """Clean up resources — close all agents and their tool clients."""
        for agent in self._agent_instances.values():
            close_method = getattr(agent, "close", None)
            if callable(close_method):
                try:
                    await close_method()
                except Exception:
                    pass
            else:
                cleanup_method = getattr(agent, "cleanup", None)
                if callable(cleanup_method):
                    try:
                        result = cleanup_method()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception:
                        pass

    async def __aenter__(self) -> WorkflowEngine:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function — run a single engagement
# ─────────────────────────────────────────────────────────────────────────────


async def run_engagement(
    question: str,
    conversation_context: str = "",
) -> EngagementResult:
    """Run a complete HYPERION engagement.

    Convenience function that creates a WorkflowEngine, runs the engagement,
    and cleans up.

    Usage:
        result = await run_engagement("Should we enter the Tier-2 Indian SaaS market?")
        if result.success:
            print(f"PDF: {result.pdf_path}")
    """
    engine = WorkflowEngine()
    try:
        return await engine.run_engagement(
            question=question,
            conversation_context=conversation_context,
        )
    finally:
        await engine.close()
