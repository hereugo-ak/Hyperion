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
import hashlib
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from hyperion.agents.bus import Channel, MessageType, get_bus, reset_bus
from hyperion.agents.engagement_director import EngagementDirector
from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.obs import ArtifactStore, RunJournal, RunManifest, trace
from hyperion.schemas.agents import AgentName, AgentState
from hyperion.schemas.models import (
    AnalysisGap,
    FactCheckReport,
    FinalReport,
    LayoutPlan,
    QualityScore,
    QualityTerminalState,
    RenderOutput,
    VisualizationOutput,
)
from hyperion.schemas.workflow import (
    EngagementMetadata,
    TaskNode,
    TaskStatus,
    WorkflowDAG,
)
from hyperion.tools.query_utils import (
    canonicalize_geographies,
    clear_engagement_focus,
    detect_geographies,
    set_engagement_focus,
)

logger = logging.getLogger(__name__)


class DeliveryFailureError(RuntimeError):
    """W-04: a required delivery stage failed — the engagement fails closed.

    Raised from the delivery loop when any of DATA_VISUALIZER,
    PRESENTATION_DESIGNER, or RENDER_ENGINE raises or cannot run. There are
    no optional delivery tasks: a report without its charts, or without the
    render engine's audited PDF, is wrong — not merely plainer. The
    pre-W-04 `except Exception: log; continue` converted exactly such a
    crash into a silent success (a 34-page report with zero charts).

    Carries the agent name, the original exception type, and the full
    traceback so the failure is attributable without re-running.

    Subclasses RuntimeError so the outer run_engagement handler converts it
    into a failed EngagementResult (success=False, error=<traceback>)
    through the existing loud path rather than an unhandled crash.
    """

    def __init__(self, agent: str, exc_type: str, message: str, tb: str = "") -> None:
        self.agent = agent
        self.exc_type = exc_type
        self.traceback = tb
        super().__init__(f"DeliveryFailure[{agent}]: {exc_type}: {message}")


# Backward-compatible public name retained for existing integrations.
DeliveryFailure = DeliveryFailureError


class MissingDependencyOutputError(RuntimeError):
    """W-20: A DAG task declared a dependency that produced no output.

    Raised from ``_execute_task`` when a required dependency is FAILED (or
    otherwise absent from ``_task_outputs``). The pre-W-20 behaviour silently
    skipped the missing entry, so the dependent agent ran with a partial
    context and produced analysis that never knew an input was missing.

    Raised BEFORE the agent-dispatch try block, so it propagates to
    ``_execute_wave``, which marks the dependent task FAILED with this
    exception's message — loud and attributable, never a silent partial run.
    """


# Backward-compatible public name retained for existing integrations.
MissingDependencyOutput = MissingDependencyOutputError


def derive_run_id(question: str, engagement_key: str = "") -> str:
    """W-20: deterministic engagement id from the engagement's inputs.

    The durable-execution journal (P10) keys on ``run_id``. Seeding it from a
    random UUID made every engagement a brand-new run id, so the cache-hit
    machinery was structurally inert — a resumed run could never match a
    prior run's journal. Deriving the id deterministically means re-invoking
    the same question re-opens the same journal and replays completed steps.

    The question is NORMALISED (lowercase, whitespace-collapsed) before
    hashing so trivial rephrasing ("Market X?" vs "market   x ?") does not
    defeat resumption; a genuinely different question still gets its own id.
    ``engagement_key`` lets a caller namespace two runs of the same question.
    """
    normalized = " ".join((question or "").split()).lower()
    key_part = " ".join((engagement_key or "").split()).lower()
    digest = hashlib.sha256(f"{normalized}\x00{key_part}".encode()).hexdigest()
    return f"eng_{digest[:12]}"


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
    # W-04: machine-readable failure attribution. "delivery" when a required
    # delivery task failed, "" otherwise. The zero-evidence hard-fail path
    # sets error text directly; this field lets a caller distinguish a
    # delivery failure from a research-stack failure without parsing strings.
    failure_reason: str = ""
    duration_seconds: float = 0.0
    adaptation_count: int = 0
    escalation_count: int = 0
    quality_iterations: int = 0
    # Fix 2.6 (audit §6 Phase 2): per-engagement extraction-yield metrics,
    # populated at engagement completion from engagement_yield_report().
    extraction_yield: dict[str, Any] = field(default_factory=dict)
    # W-18: actual provider-reported tokens priced with the dated planning
    # table in router/budget.py. This is an estimate, never presented as an invoice.
    estimated_llm_cost_usd: float = 0.0

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
            "extraction_yield": self.extraction_yield,
            "estimated_llm_cost_usd": self.estimated_llm_cost_usd,
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

    # W-08: the class-level cap is a fallback only; the authoritative value
    # is settings.max_quality_iterations (now 4) so the operator can tune it
    # without a code change. The wall-clock budget below is the real bound.
    MAX_QUALITY_ITERATIONS = 4  # W-08: raised from 2 (was 3, then P7 cap 2)
    TASK_TIMEOUT_SECONDS = 600  # 10 minutes — default for most agents
    SPECIALIST_TIMEOUT_SECONDS = 1200  # 20 minutes — specialists spawn up to 3 sub-agents
    # F-10: the corpus-floor integrity blocker (quality_gate._CORPUS_FLOOR_DOMAINS)
    # demands a targeted retrieval escalation with floor 8 before terminal
    # state is computed, not only the generic configurable source floor.
    _CORPUS_FLOOR_SOURCE_FLOOR = 8
    # L3 fix: the task reframer sits BENEATH the Director's STRONG-tier
    # strategic replanner and handles the common "thin query" case. Each
    # failed / zero-finding task may be reframed at most this many times
    # per DAG. Cap of 2 was chosen deliberately: the reframer emits up to
    # 3 variants per call, so 2 retries is enough to explore multiple
    # broadening strategies before the Director's slot is spent, without
    # letting a genuinely un-answerable question loop forever.
    MAX_REFRAMER_RETRIES = 2
    # P4.3 (overhaul §6 P4, 2026-08-10): a GLOBAL reframe budget per
    # engagement. The per-task retry cap alone let A-6's competitive_intel burn
    # ~8 full pipelines by re-framing DIFFERENT tasks against the same dead
    # pool. The reframer is the cheapest way to waste a dead fleet's tokens, so
    # the whole engagement gets one small bucket: once it is spent, no task is
    # reworded again — failures route to capacity recovery instead.
    MAX_REFRAMER_GLOBAL_BUDGET = 6
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
        # Engagement-scoped question classification (industry/geography/
        # jurisdictions), computed once and shared by every specialist so all
        # agents analyse the same market. None = not yet computed.
        self._engagement_context: dict[str, Any] | None = None
        # P10: Durable execution — journal, artifact store, manifest
        self._journal: RunJournal | None = None
        self._artifacts: ArtifactStore | None = None
        self._manifest: RunManifest | None = None
        # P0/P2: the run-scoped Evidence Ledger and its preflight verdict.
        self._evidence_ledger: Any = None
        self._corpus_contract: Any = None
        self._evidence_reduced_budget: bool = False
        self._evidence_budget_default: int = 6
        # P4.3: reframes actually SPAWNED this engagement (global budget).
        self._reframes_spawned: int = 0
        # P4.4: progress signal — Δdomains + Δevidence per orchestration
        # iteration. An iteration with ZERO progress consumes the progress
        # budget; two consecutive zero-delta iterations is a stop signal
        # (the fleet is yielding nothing and more waves only burn tokens).
        self._consecutive_zero_progress: int = 0
        self._last_domains_seen: int = -1
        # P6.2: per-run KPI telemetry (recorded to reports/diagnostics at end).
        self._first_evidence_seconds: float = -1.0
        self._domains_before_synthesis: int = -1
        self._provenance_binding_pct: float = -1.0
        # OVERHAUL3 D-F (overhaul3_audit.md §5.5): Recovery Supervisor
        # telemetry. A BLOCKED run enters the supervisor at most
        # ``quality_recovery_max_passes`` times; every pass is recorded so the
        # final give-up (or ship) is replayable, never a discarded diagnosis.
        self._recovery_telemetry: dict[str, Any] = {
            "attempted": False,
            "passes": 0,
            "recovered": False,
            "outcomes_by_class": {},
            "passes_detail": [],
        }
        # W-20: guard every mutation of ``_all_findings`` from gathered tasks.
        # ``_execute_wave`` runs tasks via ``asyncio.gather`` and two sites
        # (the cache-hit replay and the live-run collector) extend this list
        # from inside those coroutines. The lock converts the previously
        # unstated single-event-loop invariant into an enforced one, so a
        # future move to threads or subprocesses cannot silently corrupt the
        # findings corpus.
        self._findings_lock = asyncio.Lock()

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
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_log", exc)

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
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_publish_dag_to_tui", exc)

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
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_publish_task_update", exc)

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

    async def _get_engagement_context(self, agent: Any, dag: WorkflowDAG) -> dict[str, Any]:
        """Classify the engagement question ONCE and reuse for every specialist.

        Why this is engagement-scoped rather than task-scoped:

        1. Correctness — the user's question holds the geography/industry
           ("should India reduce its dependence on the imports"). Individual
           task descriptions often don't, so classifying per task silently
           dropped "India" and specialists fell back to hardcoded US/EU
           defaults, producing a US-procurement-law analysis of an India
           question. Classify the real question, once.
        2. Consistency — all 12 specialists must analyse the SAME market.
           Per-task classification let different agents disagree.
        3. Cost — one MICRO call per engagement instead of one per task.

        Guarantees a usable `geography`/`industry`/`jurisdictions` set even if
        the LLM classifier is unavailable, so no downstream default can win.
        """
        if self._engagement_context is not None:
            return self._engagement_context

        ctx: dict[str, Any] = {}
        try:
            ctx = await agent._enrich_context(dag.question) or {}
        except Exception:  # noqa: BLE001 - best-effort, returns a safe default
            ctx = {}

        # Always union with the deterministic regex pass. The LLM sometimes
        # returns a partial object; the regex reliably catches explicit
        # country/industry mentions. Regex only fills gaps, never overrides.
        try:
            regex_ctx = agent._enrich_context_regex(dag.question) or {}
            for k, v in regex_ctx.items():
                if v not in (None, "", [], {}) and ctx.get(k) in (None, "", [], {}):
                    ctx[k] = v
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_get_engagement_context", exc)

        # ── Scope: prefer the decomposing agent's decision ──────────────────
        #
        # The Engagement Director reads the question and breaks it down, so IT
        # decides which country and industry the engagement is about. Its
        # answer travels on the DAG (dag.geographies / dag.subject) and is
        # authoritative here.
        #
        # This ordering is the fix for a genuine inversion. Geography used to
        # be decided at THIS line by a regex gazetteer scanning the raw
        # question, while the Director — already spending an LLM call on the
        # same question — was never asked for it. The gazetteer then matched
        # the English pronoun "us" in "help us decide whether to enter India"
        # and anchored the whole engagement to the United States. The word
        # list is now the third choice, not the first.
        #
        # Precedence, strongest signal first:
        #   1. dag.geographies — the Director's extraction (an LLM that read
        #      the sentence), canonicalised.
        #   2. ctx from _enrich_context — the per-agent classifier.
        #   3. detect_geographies — deterministic scan of the user's words,
        #      for when both LLM calls failed.
        #
        # No step invents a default. If all three yield nothing, the question
        # named no jurisdiction and the analysis runs without a jurisdiction
        # filter — honest, and strictly better than a confident report about a
        # country the user never mentioned.
        dag_geos = canonicalize_geographies(getattr(dag, "geographies", None))
        if dag_geos:
            ctx["geography"] = dag_geos[0]
            ctx["jurisdictions"] = dag_geos

        geo = ctx.get("geography") or ctx.get("jurisdiction")
        if not geo:
            detected = detect_geographies(dag.question or "")
            if detected:
                geo = detected[0]
                ctx["geography"] = geo
                if len(detected) > 1 and not ctx.get("jurisdictions"):
                    ctx["jurisdictions"] = detected
        if geo:
            ctx.setdefault("jurisdiction", geo)
            if not ctx.get("jurisdictions"):
                ctx["jurisdictions"] = [geo]

        # Subject, same precedence: the Director's extraction first, then the
        # per-agent classifier, then a deterministic derivation from the
        # question. The last resort exists because an empty {sector} once
        # interpolated into real searches like "carbon footprint emissions
        # data" — grammatical, subject-less, and 34 minutes of useless traffic.
        subject = (
            str(getattr(dag, "subject", "") or "").strip()
            or ctx.get("industry")
            or ctx.get("sector")
            or ctx.get("space")
            or self._derive_subject_from_question(dag.question)
        )
        if subject:
            for key in ("industry", "sector", "space"):
                ctx.setdefault(key, subject)

        # W-06 / A1 (competitor discovery): thread the Director's subject_class
        # through to every specialist. COMPETE's discovery queries must be
        # shaped by what kind of entity the arena is (a company, a country's
        # players, a technology, a market) — "space startups in India" is a
        # COMPANY-class arena even when the question's subject is a
        # NATION_OR_REGION.
        dag_subject_class = str(getattr(dag, "subject_class", "") or "").strip()
        if dag_subject_class:
            ctx["subject_class"] = dag_subject_class

        ctx.setdefault("question", dag.question)
        self._engagement_context = ctx

        # Publish the anchor to the search layer. Every outbound search query
        # is grounded against this, so no specialist can issue a subject-less
        # or off-geography search regardless of its hardcoded templates.
        try:
            set_engagement_focus(
                question=dag.question,
                subject=str(ctx.get("industry") or subject or ""),
                geography=str(ctx.get("geography") or ""),
            )
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            # If the anchor is never set, every outbound search query goes
            # out ungrounded — the original P0 failure class. This must be
            # loud, not silent.
            logger.error(
                "engagement focus anchor failed — outbound queries will be "
                "ungrounded: %s: %s",
                type(exc).__name__, exc,
            )

        self._log(
            "CONTEXT: "
            f"industry={ctx.get('industry') or '?'} · "
            f"geography={ctx.get('geography') or '?'} · "
            f"jurisdictions={ctx.get('jurisdictions') or '?'}"
        )
        return ctx

    @staticmethod
    def _derive_subject_from_question(question: str) -> str:
        """Extract a usable search subject from the raw question.

        Deterministic, no LLM. Strips interrogatives/stopwords and keeps the
        content words so a query template always has a real subject to
        interpolate, even when classification fails entirely.
        """
        import re

        stop = {
            "should", "would", "could", "do", "does", "did", "is", "are", "was",
            "were", "can", "will", "the", "a", "an", "its", "it", "we", "our",
            "us", "i", "my", "on", "in", "of", "to", "for", "and", "or", "but",
            "if", "then", "than", "that", "this", "these", "those", "be", "been",
            "have", "has", "had", "how", "what", "why", "when", "where", "which",
            "who", "reduce", "increase", "there", "their", "from", "with", "by",
            "at", "as", "so", "not", "no", "yes", "any", "all", "more", "most",
        }
        words = re.findall(r"[A-Za-z][A-Za-z\-]+", question or "")
        keep = [w for w in words if w.lower() not in stop and len(w) > 2]
        return " ".join(keep[:6])

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
                            # W-20: gathered-wave mutation — under the lock.
                            async with self._findings_lock:
                                self._all_findings.extend(cached_obj._findings)
                        return cached_obj

        agent = self._get_agent(task.agent)
        task.status = TaskStatus.RUNNING
        task.started_at = time.time()
        self._publish_task_update(task)

        # Build context from dependency outputs.
        #
        # W-20: a declared dependency that produced no output is a LOUD
        # failure, not a skipped dict entry. The pre-W-20 code simply omitted
        # the missing dep from ``context``, so the dependent agent ran on a
        # partial context and its output never indicated an input was missing.
        # Now the dependent raises ``MissingDependencyOutput`` before any agent
        # dispatch; ``_execute_wave`` marks it FAILED with that reason.
        context: dict[str, Any] = {}
        missing_deps: list[str] = []
        for dep_id in task.dependencies:
            if dep_id in self._task_outputs:
                dep_output = self._task_outputs[dep_id]
                dep_task = dag.get_task(dep_id)
                if dep_task:
                    context[dep_task.agent.value] = dep_output
            else:
                missing_deps.append(dep_id)
        if missing_deps:
            # OVERHAUL2 S4: the scheduler (workflow.get_ready_tasks) already
            # licenses FAILED dependencies — "run with partial findings".
            # Synthesis and fact-check are the aggregation stages: their
            # inputs are the FINDINGS CHANNEL plus whatever outputs exist.
            # Crashing them on a missing dep converts one specialist failure
            # into a zero-report run (the 17:51:21 incident). Specialists
            # keep the strict contract (they need real inputs).
            #
            # OVERHAUL3 D-B (overhaul3_audit.md W1/S2): the strict raise was
            # too blunt — it also fired when the missing dep is a FAILED
            # *specialist* (upstream crash, e.g. COMPETE at 06:31:36), which
            # cascaded into MissingDependencyOutput on STRATEGY at 06:57:51.
            # The scheduler licenses FAILED deps as a ready condition
            # (workflow.get_ready_tasks), so a crashed upstream is exactly
            # the partial-context case, not a scheduling anomaly. Distinguish:
            #   - dep task exists and status == FAILED (specialist crash) →
            #     run on reduced context carrying missing_dependencies, like
            #     synthesis — for ALL agents that consume specialist output.
            #   - dep task absent / not FAILED (PENDING, scheduling anomaly,
            #     a genuinely missing retrieval artifact) → strict raise.
            is_agg_stage = task.agent in (
                AgentName.SYNTHESIS_LEAD, AgentName.FACT_CHECKER,
            )
            missing_are_crashed = all(
                (dep_task := dag.get_task(dep_id)) is not None
                and dep_task.status == TaskStatus.FAILED
                for dep_id in missing_deps
            )
            if not (is_agg_stage or missing_are_crashed):
                dep_status = "unknown"
                dep_task = dag.get_task(missing_deps[0])
                if dep_task is not None:
                    dep_status = dep_task.status.value
                raise MissingDependencyOutput(
                    f"task '{task.id}' ({task.agent.value}) depends on "
                    f"'{missing_deps[0]}' which has no output (status={dep_status}) — "
                    f"refusing to run with a partial context"
                )
            self._log(
                f"{task.agent.value}: proceeding with partial context — "
                f"missing dependency outputs: {missing_deps}"
            )
            context["missing_dependencies"] = missing_deps
            async with self._findings_lock:
                context["collected_findings"] = list(self._all_findings)

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
                # P7 GAP-2: Enrich context so specialists' search queries are
                # never empty and never default to the wrong jurisdiction.
                #
                # Enrichment is computed ONCE per engagement from the USER'S
                # question (dag.question), not per-task from task.description.
                # task.description is an internal sub-goal like "How competitive
                # are potential domestic substitutes..." which frequently omits
                # the country/industry — that omission is what caused an INDIA
                # question to be analysed under the US Buy American Act.
                try:
                    enriched = await self._get_engagement_context(agent, dag)
                    for k, v in enriched.items():
                        # Never let a null/blank classification overwrite a real
                        # value, and never inject empty strings that would
                        # interpolate into f-string queries as "" (that is what
                        # produced searches like "carbon footprint emissions data"
                        # with no subject at all).
                        if v in (None, "", [], {}):
                            continue
                        context.setdefault(k, v)
                except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                    logger.warning("%s: %s", "_execute_task", exc)

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
                # P5.1 (overhaul §6 P5): the corpus floor is measured at the
                # pre-factcheck boundary too. Fact-checking re-searches for
                # verification; running it against a corpus that has collapsed
                # since preflight wastes the verification pass. A below-floor
                # read here degrades to AMBER so the verification step is
                # bounded, never hallucinated.
                await self._recheck_corpus_midrun(dag)
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

                # P1.4 (overhaul §6 P1, 2026-08-10): mid-run corpus re-probe.
                # The preflight fires once at engagement start (P2); if the
                # fleet collapses mid-run (the Aug-10 A-8 scenario), synthesis
                # must not proceed over an evidence vacuum. Read the ledger now
                # and degrade to AMBER (halved sub-agent budget) when the
                # corpus has collapsed since start.
                await self._recheck_corpus_midrun(dag)

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
                # Data Visualizer needs chart specs from the Synthesis Lead's
                # FinalReport. If the Synthesis Lead did not emit any, mine them
                # deterministically from the numbers the agents already found.
                #
                # HISTORY: `FinalReport` had no `chart_specifications` field, so
                # the old `hasattr(...)` guard was always False and this branch
                # silently passed chart_specs=None on EVERY run. The Data
                # Visualizer then reported "No chart specifications received"
                # and returned 0 charts — every report ever produced was
                # text-only while a full Plotly/300-DPI/Pillow pipeline sat
                # unused. The field now exists; the miner guarantees it is
                # populated whenever the findings actually contain numbers.
                final_report = self._get_output_by_agent(dag, AgentName.SYNTHESIS_LEAD)
                chart_specs: list[dict[str, Any]] = []
                if final_report is not None:
                    chart_specs = list(getattr(final_report, "chart_specifications", None) or [])
                    if not chart_specs:
                        try:
                            from hyperion.output.chart_specs import mine_chart_specs

                            chart_specs = mine_chart_specs(
                                final_report, question=dag.question
                            )
                            if chart_specs:
                                logger.info(
                                    "Mined %d chart spec(s) from report findings "
                                    "(Synthesis Lead supplied none)",
                                    len(chart_specs),
                                )
                                # Persist onto the report so the Presentation
                                # Designer and Render Engine see the same specs.
                                try:
                                    final_report.chart_specifications = chart_specs
                                except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                                    # Mined specs that never reach the report
                                    # mean a text-only deliverable despite
                                    # chartable data existing — record it.
                                    logger.warning(
                                        "mined chart specs could not be persisted "
                                        "to the report: %s: %s",
                                        type(exc).__name__, exc,
                                    )
                            else:
                                logger.warning(
                                    "No chartable numeric series found in findings; "
                                    "report will be text-only"
                                )
                        except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
                            logger.warning("Chart spec mining failed: %s: %s", type(e).__name__, e)
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
                        # Fix 4.2: hand the Render Engine the same budget the
                        # report was written under, so its page-count gate can
                        # tell a report that is short because the word ceiling
                        # bound it from one that is short because it is thin.
                        page_budget=self._page_budget_for(dag),
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

            # P2-18: specialists do NOT complete here — they rest in
            # AWAITING_FOLLOWUP so a verify_claims request or a GAP_CLOSURE
            # re-dispatch reaches a live, subscribed agent. The closure
            # phase finalizes them to COMPLETED after it runs.
            if task.agent in self._SPECIALIST_AGENTS:
                task.status = TaskStatus.AWAITING_FOLLOWUP
            else:
                task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.output = (
                result.model_dump()
                if isinstance(result, BaseModel)
                else {"result": str(result)}
            )
            self._task_outputs[task.id] = result
            self._publish_task_update(task)
            # OVERHAUL2 S5: a successful reframed variant satisfies the
            # ORIGINAL task's downstream contract. Without this alias,
            # dependents keep pointing at an output slot the reframer's new
            # task IDs never fill. Walk the ``reframed_from`` chain to the
            # ROOT origin so nested reframes (a reframed variant that is
            # itself reframed — the 17:43:39 ``task_reframed_1_1_*`` chain)
            # all backfill the one slot synthesis actually depends on.
            reframed_from = getattr(task, "reframed_from", None)
            if reframed_from:
                origin = reframed_from
                seen: set[str] = set()
                while origin and origin not in seen:
                    seen.add(origin)
                    if origin not in self._task_outputs:
                        self._task_outputs[origin] = result
                        original = dag.get_task(origin)
                        if original is not None and original.status == TaskStatus.FAILED:
                            original.status = TaskStatus.COMPLETED
                            original.error = ""
                            self._publish_task_update(original)
                    parent = dag.get_task(origin)
                    next_origin = getattr(parent, "reframed_from", None) if parent else None
                    if next_origin and next_origin not in self._task_outputs:
                        origin = next_origin
                    else:
                        break

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
                # F-0.1-13 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): the single
                # ledger of record is the bus's FINDINGS channel — it captures
                # both individual findings (_publish_finding) AND aggregate
                # model publishes (bus.publish Channel.FINDINGS) that bypass the
                # specialist's _findings attribute. Counting only len(_findings)
                # produced "completed with 0 findings" despite 23 recorded. The
                # bus count is authoritative; _findings is a fallback.
                try:
                    bus_count = self.bus.get_findings_count(task.agent)
                except Exception:  # noqa: BLE001 - count must never break the wave
                    bus_count = 0
                findings_count = bus_count or len(agent._findings)
                # W-20: gathered-wave mutation — under the lock.
                async with self._findings_lock:
                    self._all_findings.extend(agent._findings)
                    # OVERHAUL3 D-D (overhaul3_audit.md W1/S4): count and
                    # collection must read the SAME store. Specialists publish
                    # two ways — _publish_finding (agent._findings + bus) and
                    # the aggregate model publish (bus.publish Channel.FINDINGS
                    # with a model_dump, bus ONLY). The count used the bus; the
                    # collection used only agent._findings — so the aggregate
                    # was counted but never collected (06:40:41 "1 (0)",
                    # 06:41:27 "8 (7)"). Drain this agent's retained bus
                    # findings, convert aggregate payloads with the same
                    # synthetic-finding path the Synthesis Lead uses, and dedup
                    # by finding id (individual findings already collected from
                    # agent._findings are also on the bus).
                    seen_ids = {
                        getattr(f, "id", None) for f in self._all_findings
                    }
                    for retained in self.bus.get_retained_findings():
                        if retained.sender != task.agent:
                            continue
                        finding = retained.finding
                        if finding is None:
                            from hyperion.agents.synthesis_lead import (
                                synthetic_finding_from_payload,
                            )

                            finding = synthetic_finding_from_payload(
                                retained.payload, task.agent.value,
                            )
                        if finding is None:
                            continue
                        finding_id = getattr(finding, "id", None)
                        if finding_id in seen_ids:
                            continue
                        seen_ids.add(finding_id)
                        self._all_findings.append(finding)
                self._log(
                    f"{task.agent.value}: completed with {findings_count} findings "
                    f"(total collected: {len(self._all_findings)})"
                )
            else:
                self._log(f"{task.agent.value}: completed (no findings attribute)")

            return result

        except TimeoutError:
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

    def _page_budget_for(self, dag: WorkflowDAG) -> Any | None:
        """Reconstruct the page budget the final report was written under (4.2).

        Recomputed from the section count of the finished `FinalReport` rather
        than carried as orchestrator state. `plan_budget` is a pure function of
        the section count, so recomputation yields the identical budget while
        avoiding a mutable field that the quality-iteration loop could leave
        stale: that loop can revise the report — and therefore its section
        count — several times before delivery, and a budget captured at synthesis
        time would then describe a report that no longer exists.

        Returns None when there is no report to measure, which the Render
        Engine's gate treats as "judge against the flat contract band" rather
        than "skip the check".
        """
        report = self._get_output_by_agent(dag, AgentName.SYNTHESIS_LEAD)
        sections = getattr(report, "sections", None)
        if not sections:
            return None
        from hyperion.output.page_budget import plan_budget

        return plan_budget(len(sections))

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
        except Exception:  # noqa: BLE001 - best-effort, returns a safe default
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

        # L3 fix: reframer retry loop. After the wave settles, check each
        # task for FAILED status or a zero-finding COMPLETED sub-agent run
        # (research_gap). If the task has retries left, ask the task
        # reframer for up to 3 broadened variants and spawn them as
        # ``task_reframed_*`` TaskNodes on the DAG so a later wave picks
        # them up. This sits BENEATH the Director's STRONG-tier strategic
        # replanner — it handles the common "thin query" case without
        # burning the Director's slot.
        try:
            await self._maybe_reframe_failed_tasks(tasks, dag)
        except Exception as exc:  # noqa: BLE001 - reframer must never break a wave
            logger.warning("_maybe_reframe_failed_tasks: %s", exc)

        return processed

    def _task_needs_reframe(self, task: TaskNode) -> bool:
        """L3: return True if this task failed or produced zero findings
        and still has reframer retries left.

        Only specialists are eligible — the Director, Synthesis Lead,
        Quality Gate, delivery agents etc. have their own recovery paths
        and shouldn't be reframed.

        OVERHAUL2 S5c: a task that ALREADY produced substantive findings
        is never reframed, whatever its status field says. The 17:23 run
        showed a risk analyst completing with 12 findings and then being
        marked FAILED (a stale "blocked" bus broadcast from its own gap
        escalation) and REFRAMED — rerunning the whole analysis and
        stranding the first 12 findings in a task slot nothing reads.
        """
        if task.reframe_attempts >= self.MAX_REFRAMER_RETRIES:
            return False
        # OVERHAUL3 D-E (overhaul3_audit.md W2/S5) (a): an already-reframed
        # variant is NEVER reframed again. The 06:58:03 chain reframed
        # ``task_reframed_1_1_task_competitive_intel`` into 3 more variants
        # because the per-task attempt cap counted the VARIANT's fresh chain
        # instead of the original's. Refusing any ``reframed_from`` node caps
        # the variant TREE, not just the branch — a variant fails once and
        # the chain dies.
        if task.reframed_from:
            return False
        # OVERHAUL3 D-E (b): the health-gate must be per-class, not "any
        # class alive". The Aug-11 run had brave 429-suspended (and wikipedia
        # 400ing) while scholar/reference stayed up — the old gate still
        # reframed web-targeted COMPETE into a dead path. A query whose
        # target source class has no living engine is rewording a dead pool.
        try:
            from hyperion.tools.engine_health import get_engine_health, query_target_class

            target_class = query_target_class(task.description or "")
            if not get_engine_health().class_healthy(target_class):
                logger.warning(
                    "REFRAMER TARGET-CLASS GATE: source class %r is dead for "
                    "'%s' — reframing refused (per-class health-gate)",
                    target_class,
                    (task.description or "")[:80],
                )
                return False
        except Exception as exc:  # noqa: BLE001 - a health read must not change policy
            logger.debug("reframer per-class gate read failed (reframe allowed): %s", exc)
        if task.agent not in self._SPECIALIST_AGENTS:
            return False
        # S5c: the bus is the single ledger of record for findings — if this
        # agent already published substantive yield, its work product exists;
        # a FAILED status is a status-writer bug, not missing evidence.
        try:
            bus_count = self.bus.get_findings_count(task.agent)
        except Exception:  # noqa: BLE001 - a read failure must not change policy
            bus_count = 0
        if bus_count > 0:
            return False
        # FAILED — clearly worth a reframe.
        if task.status == TaskStatus.FAILED:
            return True
        # COMPLETED but produced no findings for this task. We can't ask
        # the task directly (findings live on the specialist agent under
        # ``_findings``), so we check the task_outputs bag: a completed
        # specialist that didn't emit any finding usually means gap.
        if task.status == TaskStatus.COMPLETED:
            output = self._task_outputs.get(task.id)
            if output is None:
                return True
            # Some specialists return dicts/objects with a ``findings``
            # attr or key — treat 0 findings as a zero-finding signal.
            findings_attr = getattr(output, "findings", None)
            if findings_attr is not None and len(findings_attr) == 0:
                return True
            if isinstance(output, dict) and isinstance(output.get("findings"), list) \
                    and len(output["findings"]) == 0:
                return True
        return False

    def _failure_signal_for(self, task: TaskNode) -> str:
        """L3: classify a task's failure into a reframer signal string."""
        if task.status == TaskStatus.FAILED:
            err = (task.error or "").lower()
            if "timed out" in err or "timeout" in err:
                return "timed_out"
            return "failed"
        return "zero_findings"

    def _dependency_failed(self, task: TaskNode, dag: WorkflowDAG) -> bool:
        """OVERHAUL3 D-E (c): True when any of this task's own dependencies
        FAILED.

        Reframing cannot repair an upstream crash — every variant re-runs the
        same dead dependency path. The Aug-11 STRATEGY chain is the proof:
        COMPETE failed at 06:47:43, STRATEGY was reframed at 06:57:55, and
        each variant re-failed against the same missing output. When the
        upstream crash is recovered (the dep is later COMPLETED), the task
        becomes eligible again.
        """
        for dep_id in task.dependencies or []:
            dep = dag.get_task(dep_id)
            if dep is not None and dep.status == TaskStatus.FAILED:
                return True
        return False

    async def _maybe_reframe_failed_tasks(
        self, tasks: list[TaskNode], dag: WorkflowDAG,
    ) -> None:
        """L3: dispatch the task reframer for eligible failed / zero-finding
        specialist tasks and add ``task_reframed_*`` nodes to the DAG.

        This method is non-raising: a reframer outage must not stall the
        pipeline. Bounded per task-chain (``MAX_REFRAMER_RETRIES``) and per
        engagement (``MAX_REFRAMER_GLOBAL_BUDGET``). P4.3 health-gate: the
        reframer only runs when at least one source class is alive — rewording
        a query against a fully-dead fleet is pure token spend (the A-6 loop).
        """
        try:
            from hyperion.tools.task_reframer import reframe_task
        except Exception as exc:  # noqa: BLE001 - escalation fallback must not crash the loop
            logger.warning("task_reframer import failed: %s", exc)
            return

        # P4.3 health-gate: when NO source class has a live engine, the
        # failure class is ENGINE_BLOCKED, and rewording cannot recover it —
        # reroute to capacity recovery (orchestrator._escalate_retrieval)
        # instead of burning the reframe budget. The reframer is a NO_RESULTS
        # remedy (anti-pattern 5); a dead fleet is a routing problem.
        try:
            from hyperion.tools.engine_health import get_engine_health

            if not get_engine_health().living_classes():
                logger.error(
                    "REFRAMER HEALTH-GATE: zero living source classes — "
                    "reframing suppressed (failure class ENGINE_BLOCKED); "
                    "rerouting to capacity recovery"
                )
                return
        except Exception as exc:  # noqa: BLE001 - health read must not block a wave
            logger.debug("reframer health-gate read failed (reframe allowed): %s", exc)

        eligible = [
            t for t in tasks
            if self._task_needs_reframe(t) and not self._dependency_failed(t, dag)
        ]
        if not eligible:
            return

        # P4.3 global budget: reframes spawned THIS engagement are counted
        # against one shared bucket, not just the per-task cap.
        if self._reframes_spawned >= self.MAX_REFRAMER_GLOBAL_BUDGET:
            logger.error(
                "REFRAMER GLOBAL BUDGET reached (%d/%d) — no further "
                "rewordings this engagement; failures route to capacity recovery",
                self._reframes_spawned,
                self.MAX_REFRAMER_GLOBAL_BUDGET,
            )
            return

        # Pull engagement context (subject/geography) for better prompts.
        ctx = self._engagement_context or {}
        subject = str(ctx.get("subject") or ctx.get("industry") or "")
        geography = str(ctx.get("geography") or "")

        for original in eligible:
            failure_signal = self._failure_signal_for(original)
            try:
                result = await reframe_task(
                    original.description or "",
                    failure_signal=failure_signal,
                    router=self.router,
                    task_description=original.description or "",
                    subject=subject,
                    geography=geography,
                    context=ctx,
                )
            except Exception as exc:  # noqa: BLE001 - reframer is best-effort
                logger.warning("reframe_task failed for %s: %s", original.id, exc)
                continue

            variants = getattr(result, "variants", None) or []
            if not variants:
                continue

            spawned = 0
            for idx, variant in enumerate(variants):
                # Compact defensive access — variant may be a Pydantic
                # model or a plain dict depending on deterministic vs
                # LLM path.
                new_question = (
                    getattr(variant, "rephrased_question", None)
                    or (variant.get("rephrased_question") if isinstance(variant, dict) else None)
                    or ""
                ).strip()
                if not new_question:
                    continue
                new_id = f"task_reframed_{original.reframe_attempts + 1}_{idx}_{original.id}"
                # Idempotence: don't re-add if a previous wave already
                # spawned an identical node for this original.
                if dag.get_task(new_id) is not None:
                    continue
                # Reframed tasks reuse the original's tier and (empty)
                # dependencies — they slot in as fresh work for the next
                # wave. reframed_from lets the DAG/log trace the chain.
                new_task = TaskNode(
                    id=new_id,
                    agent=original.agent,
                    model_tier=original.model_tier,
                    description=new_question[:400],
                    dependencies=[],
                    status=TaskStatus.PENDING,
                    reframe_attempts=original.reframe_attempts + 1,
                    reframed_from=original.id,
                    estimated_llm_calls=original.estimated_llm_calls,
                    estimated_tokens=original.estimated_tokens,
                )
                try:
                    dag.add_task(new_task)
                    spawned += 1
                    self._publish_task_update(new_task)
                except Exception as exc:  # noqa: BLE001 - reframing is best-effort
                    logger.warning("add_task(reframed) failed for %s: %s", new_id, exc)
                    continue

            if spawned:
                # P4.3: count every actually-spawned reframe against the
                # engagement-wide budget, so a fleet-wide outage cannot spend
                # the bucket across unrelated tasks.
                self._reframes_spawned += spawned
                try:
                    dag.adapted = True
                    if hasattr(dag, "adaptation_log"):
                        dag.adaptation_log.append(
                            f"Reframed {original.id} ({failure_signal}) → "
                            f"{spawned} variant(s), attempt "
                            f"{original.reframe_attempts + 1}/"
                            f"{self.MAX_REFRAMER_RETRIES}"
                        )
                except Exception as exc:  # noqa: BLE001 - adaptation log is best-effort
                    logger.warning("dag adaptation log append failed: %s", exc)
                self._log(
                    f"REFRAMER: {original.id} ({original.agent.value}, "
                    f"{failure_signal}) → {spawned} reframed variant(s) "
                    f"[attempt {original.reframe_attempts + 1}/"
                    f"{self.MAX_REFRAMER_RETRIES}]"
                )

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

    # P2-18: the specialists that stay ALIVE (subscribed, task resting in
    # TaskStatus.AWAITING_FOLLOWUP rather than COMPLETED) until the
    # GAP_CLOSURE phase closes, so a verify_claims request or a gap
    # re-dispatch has a live recipient.
    _SPECIALIST_AGENTS = frozenset({
        AgentName.MARKET_ANALYST, AgentName.COMPETITIVE_INTEL,
        AgentName.FINANCIAL_ANALYST, AgentName.RISK_ANALYST,
        AgentName.TECHNOLOGY_ANALYST, AgentName.OPERATIONS_ANALYST,
        AgentName.REGULATORY_ANALYST, AgentName.SUSTAINABILITY_ANALYST,
        AgentName.CONSUMER_INSIGHTS, AgentName.MA_ANALYST,
        AgentName.INNOVATION_ANALYST, AgentName.STRATEGY_ANALYST,
    })

    # P2-18: the GAP_CLOSURE phase node, inserted between fact check and
    # quality gate. Owned by the Engagement Director per audit P2-16.
    _GAP_CLOSURE_TASK_ID = "task_gap_closure"

    def _ensure_gap_closure_task(self, dag: WorkflowDAG) -> None:
        """Insert the GAP_CLOSURE phase into the DAG (idempotent).

        It depends on the fact checker; every task that previously depended
        on the fact checker directly (quality gate, synthesis) now depends on
        the closure phase, so specialists are re-dispatchable until it runs.
        """
        if dag.get_task(self._GAP_CLOSURE_TASK_ID) is not None:
            return
        from hyperion.config import ModelTier

        fact_check_ids = [
            t.id for t in dag.tasks if t.agent == AgentName.FACT_CHECKER
        ]
        closure = TaskNode(
            id=self._GAP_CLOSURE_TASK_ID,
            agent=AgentName.ENGAGEMENT_DIRECTOR,
            model_tier=ModelTier.STRONG,
            description=(
                "GAP_CLOSURE phase: re-dispatch unresolved AnalysisGap objects "
                "to live specialists (max 3 rounds), then finalize specialist "
                "tasks."
            ),
            dependencies=list(fact_check_ids),
            estimated_llm_calls=3,
            estimated_tokens=6000,
        )
        for task in dag.tasks:
            if any(fc in task.dependencies for fc in fact_check_ids):
                task.dependencies = [
                    self._GAP_CLOSURE_TASK_ID if d in fact_check_ids else d
                    for d in task.dependencies
                ]
        dag.tasks.append(closure)

    async def _gap_closure_phase(
        self,
        dag: WorkflowDAG,
        gaps: list[AnalysisGap] | None = None,
    ) -> list[AnalysisGap]:
        """Run the W-07 evidence-insufficiency ladder (owned by the Director).

        Each unresolved gap walks a budgeted ladder that ends in one of four
        named outcomes (``hyperion.agents.insufficiency``):

        - up to 3 ``RETRY_STRATEGY`` rounds, each changing the concrete
          ``(query_form, engine_set, window, locale)`` triple and never
          repeating a triple that already returned zero;
        - then up to 2 ``RETRY_SCOPE`` rounds, broadening period/entity/
          geography and recording the scope change;
        - then classification as ``OUT_OF_SCOPE`` (subject-class mismatch —
          the section is suppressed) or ``DECLARED_GAP`` (thin public record
          — the specific gap is declared).

        The first truthy agent result resolves the gap. Resolutions are
        collected on ``self._insufficiency_resolutions`` for the scope note
        and the declared-gap statements. After all rounds, specialist tasks
        are finalized to COMPLETED and the closure task itself is marked
        COMPLETED so the quality gate can proceed.
        """
        from hyperion.agents.insufficiency import (
            InsufficiencyLadder,
            classify_gap,
        )

        gaps = list(gaps or [])
        self._insufficiency_resolutions: list[Any] = []
        closure = dag.get_task(self._GAP_CLOSURE_TASK_ID)
        if closure is not None:
            closure.status = TaskStatus.RUNNING
            closure.started_at = time.time()
            self._publish_task_update(closure)

        engagement_context = getattr(self, "_engagement_context", None) or {}

        for gap in gaps:
            if gap.resolved:
                continue
            ladder = InsufficiencyLadder(
                gap_id=gap.id, question=gap.question, section_id=gap.section_id
            )
            # Phase 1: RETRY_STRATEGY — concrete, non-repeating triples.
            while not gap.resolved:
                triple = ladder.next_strategy_round()
                if triple is None:
                    break
                gap.attempts += 1
                evidence = await self._dispatch_gap_round(
                    dag, gap, triple.describe(), round_kind="strategy"
                )
                ladder.record_attempt(triple, produced_evidence=evidence)
                if evidence:
                    gap.resolved = True
                    gap.resolution = f"resolved via {triple.describe()}"
            # Phase 2: RETRY_SCOPE — broaden period/entity/geography.
            while not gap.resolved:
                planned = ladder.next_scope_round()
                if planned is None:
                    break
                triple, scope_change = planned
                gap.attempts += 1
                ladder.resolution.scope_change = scope_change
                evidence = await self._dispatch_gap_round(
                    dag,
                    gap,
                    f"{triple.describe()} — scope change: {scope_change}",
                    round_kind="scope",
                )
                ladder.record_attempt(triple, produced_evidence=evidence)
                if evidence:
                    gap.resolved = True
                    gap.resolution = (
                        f"resolved after scope change ({scope_change}) "
                        f"via {triple.describe()}"
                    )
            # Phase 3: one scarce, independently failed grounded-search attempt
            # after the W-07 strategies are exhausted and before declaring a gap.
            if not gap.resolved and ladder.budget_exhausted():
                try:
                    from hyperion.tools.deep_search import (
                        record_retrieval_backend,
                        record_retrieval_constraints,
                    )
                    from hyperion.tools.grounded_search import (
                        GroundedSearchClient,
                        GroundingReason,
                    )

                    grounded = await GroundedSearchClient().search(
                        gap.question,
                        engagement_id=self._engagement_id,
                        reason=GroundingReason.RETRY_EXHAUSTED,
                    )
                    record_retrieval_backend("gemini", grounded.actual_units)
                    record_retrieval_constraints(grounded.constraints)
                    # This scarce authority lookup is an escalation after the
                    # declared 3-strategy + 2-scope closure ladder, not a sixth
                    # ladder round. ``gap.attempts`` intentionally remains the
                    # number of specialist closure dispatches (maximum five).
                    if grounded.results:
                        gap.resolved = True
                        authorities = ", ".join(
                            result.url for result in grounded.results[:3]
                        )
                        gap.resolution = (
                            "resolved by grounded authority retrieval: "
                            f"{authorities}"
                        )
                except Exception as exc:  # noqa: BLE001 - gap classification continues
                    logger.warning(
                        "gap_closure: grounded escalation for %s failed open: %s",
                        gap.id,
                        exc,
                    )
            # Phase 4: classify the survivors.
            if not gap.resolved:
                outcome, justification = classify_gap(
                    gap.question,
                    gap.section_id,
                    engagement_context,
                    ladder.tried_triples,
                )
                ladder.resolution.outcome = outcome
                ladder.resolution.justification = justification
                self._insufficiency_resolutions.append(ladder.resolution)
                self._log(
                    f"GAP_CLOSURE: gap '{gap.id}' -> {outcome.value} "
                    f"after {len(ladder.tried_triples)} strategy attempts"
                )

        # Finalize: specialists leave AWAITING_FOLLOWUP, the phase completes.
        for task in dag.tasks:
            if (
                task.agent in self._SPECIALIST_AGENTS
                and task.status == TaskStatus.AWAITING_FOLLOWUP
            ):
                task.status = TaskStatus.COMPLETED
                task.completed_at = task.completed_at or time.time()
                self._publish_task_update(task)
        if closure is not None:
            closure.status = TaskStatus.COMPLETED
            closure.completed_at = time.time()
            self._publish_task_update(closure)
        return gaps

    async def _dispatch_gap_round(
        self,
        dag: WorkflowDAG,
        gap: AnalysisGap,
        strategy_description: str,
        round_kind: str,
    ) -> bool:
        """Dispatch one W-07 ladder round to a live specialist.

        Strategy rounds re-dispatch the ORIGINATING specialist: the section
        owner retries the same question with a different concrete query
        construction (W-07 RETRY_STRATEGY semantics). Scope rounds prefer a
        DIFFERENT live specialist: broadening entity/period/geography is a
        cross-domain ask (W-07 RETRY_SCOPE semantics). The strategy
        description is embedded in the question so the agent's query
        construction changes observably. Returns True when the agent
        produced evidence.
        """
        from hyperion.router.budget import TaskUrgency

        origin = gap.agent
        others = sorted(self._SPECIALIST_AGENTS - {origin}, key=lambda a: a.value)
        candidates = (others + [origin]) if round_kind == "scope" else ([origin] + others)
        live = getattr(self, "_agents", None) or {}
        target = next(
            (c for c in candidates if c in live), candidates[0]
        )
        agent = self._resolve_gap_agent(target)
        if agent is None:
            return False
        question = (
            f"GAP_CLOSURE {round_kind} retry (urgency HIGH) for section "
            f"'{gap.section_id}' field '{gap.field}' — retrieval strategy: "
            f"{strategy_description}. Answer this specific unresolved "
            f"question: {gap.question}"
        )
        try:
            result = await agent.run(
                question=question,
                engagement_id=self._engagement_id,
                urgency=TaskUrgency.HIGH,
            )
        except TypeError:
            result = await agent.run(
                question=question,
                engagement_id=self._engagement_id,
            )
        except Exception as exc:  # noqa: BLE001 - logged, round fails
            logger.warning(
                "gap_closure: %s round for gap %s failed: %s",
                round_kind, gap.id, exc,
            )
            return False
        return bool(result)

    def _resolve_gap_agent(self, agent_name: AgentName) -> Any | None:
        """Locate a live agent for a closure dispatch (None if unreachable)."""
        agents = getattr(self, "_agents", None) or {}
        agent = agents.get(agent_name)
        if agent is not None:
            return agent
        try:
            return self._get_agent(agent_name)
        except Exception as exc:  # noqa: BLE001 - logged, gap stays open
            logger.warning(
                "gap_closure: cannot reach agent %s: %s", agent_name.value, exc,
            )
            return None

    def _record_unresolved_gaps(
        self, report: Any, gaps: list[AnalysisGap] | None
    ) -> None:
        """Write the W-07 outcomes of survived gaps into the report.

        - ``OUT_OF_SCOPE``: the section is suppressed entirely (no heading,
          no placeholder, no TOC entry) and one consolidated line is added
          to the scope note.
        - ``DECLARED_GAP``: the section is retained and a SPECIFIC gap is
          declared — the question, the strategies attempted, and what source
          would resolve it. The banned filler phrasings ("Insufficient
          evidence", "requires additional research") are structurally
          unconstructible here.

        Any gap with no recorded resolution (defensive: resolved or
        unclassified) is omitted without filler prose.
        """
        from hyperion.agents.insufficiency import (
            InsufficiencyOutcome,
            suppress_out_of_scope_sections,
        )

        limitations = getattr(report, "limitations", None)
        if limitations is None:
            return

        resolutions = list(getattr(self, "_insufficiency_resolutions", []) or [])

        # OUT_OF_SCOPE: suppress sections, collect the consolidated scope note.
        scope_note_lines = suppress_out_of_scope_sections(report, resolutions)
        for line in scope_note_lines:
            if line not in limitations:
                limitations.append(line)

        # DECLARED_GAP: specific statements, never filler.
        for resolution in resolutions:
            if resolution.outcome != InsufficiencyOutcome.DECLARED_GAP:
                continue
            statement = resolution.declared_gap_statement()
            if not any(resolution.question in existing for existing in limitations):
                limitations.append(statement)

        # Defensive: a gap that survived with no classification is still
        # declared specifically, with its real attempt count.
        classified_ids = {r.gap_id for r in resolutions}
        for gap in gaps or []:
            if gap.resolved or gap.id in classified_ids:
                continue
            entry = (
                f"Declared research gap in section '{gap.section_id}' "
                f"({gap.field}), unanswered after {gap.attempts} closure "
                f"rounds: {gap.question}. A primary source naming this "
                f"entity and period directly would resolve it."
            )
            if not any(gap.question in existing for existing in limitations):
                limitations.append(entry)

    async def _handle_thin_evidence(self, report: Any, source_floor: int) -> bool:
        """P2-25: thin evidence triggers retrieval escalation, not a stop.

        The old content-aware stop broke out of the quality loop the moment
        source count fell below the floor ("more synthesis won't fix thin
        evidence"). Sound reasoning, wrong conclusion: thin evidence calls
        for MORE retrieval, not less synthesis. This escalates retrieval
        first; only a failed escalation is terminal, and then the correct
        output is a stated evidence limitation, not silent delivery.

        Returns True when the loop may proceed (escalation recovered
        sources), False when it is terminal.
        """
        needed = max(12, source_floor)
        recovered = await self._escalate_retrieval(report, needed)
        if recovered > 0:
            logger.info(
                "RETRIEVAL ESCALATION: recovered %d source(s) for thin evidence",
                recovered,
            )
            return True
        # F-10b: a failed escalation must be LOUD — silent empty recovery is
        # how the Aug 9 run reached BLOCKED with no retrieval attempt visible.
        logger.error(
            "RETRIEVAL ESCALATION: recovered 0 source(s) for thin evidence "
            "(needed %d) — reason: %s",
            needed,
            getattr(report, "question", "?")[:80],
        )
        limitations = getattr(report, "limitations", None)
        if limitations is not None:
            entry = (
                f"Evidence limitation: only {getattr(report, 'total_sources', 0)} "
                "sources could be gathered even after a targeted retrieval "
                "escalation round (new engines, reformulated queries); "
                "findings rest on thin evidence."
            )
            if entry not in limitations:
                limitations.append(entry)
        return False

    async def _escalate_retrieval(self, report: Any, needed: int) -> int:
        """Dispatch a targeted retrieval round and return sources recovered.

        Uses the engagement subject/geography to fire reformulated queries
        through the (now widened and rotating) search pool. Returns the
        number of new source URLs found. Overridable in tests.
        """
        try:
            from hyperion.tools.query_utils import get_engagement_focus
            from hyperion.tools.searxng import SearxNGClient

            _, subject, geography = get_engagement_focus()
            if not subject:
                logger.error("RETRIEVAL ESCALATION: recovered 0 — reason: no engagement subject")
                return 0
            client = SearxNGClient(owner="retrieval_escalation")

            # L4 fix: route through the query planner's fan-out instead of
            # 3 hardcoded strings. When the corpus is at 1 domain the
            # static queries have already failed once — replaying near-
            # identical strings just hits the same empty pocket. The
            # planner emits diversified variants (semantic broadening,
            # source-type targeting, temporal shifts) so we actually
            # explore new pockets of the web. Falls back to the old
            # static set if the planner is unavailable.
            queries: list[str] = []
            try:
                from hyperion.tools.query_planner import plan_queries

                plan_result = await plan_queries(
                    sub_question=(
                        f"Find sources on {subject}"
                        + (f" in {geography}" if geography else "")
                    ),
                    router=self.router,
                    subject=subject,
                    geography=geography,
                    target=6,
                )
                planner_queries = getattr(plan_result, "queries", None) or []
                for q in planner_queries:
                    text = (
                        getattr(q, "query", None)
                        or (q.get("query") if isinstance(q, dict) else None)
                        or ""
                    ).strip()
                    if text:
                        queries.append(text)
            except Exception as exc:  # noqa: BLE001 - planner is best-effort
                logger.warning("query planner in _escalate_retrieval failed: %s", exc)

            # Deterministic fallback: keep the original 3 strings so we
            # never end up with an empty list.
            if not queries:
                queries = [
                    f"{subject} {geography} market analysis".strip(),
                    f"{subject} {geography} industry report 2025".strip(),
                    f"{subject} {geography} news".strip(),
                ]

            # OVERHAUL4 P5.1: when the whole fleet is suspended/cooling, the
            # SearxNG loop below is a <1s ritual that recovers nothing (the
            # 11:58:53 -> 11:58:54 failure). Skip it and go straight to the
            # direct API legs that bypass the suspensions entirely.
            from hyperion.tools.engine_health import get_engine_health
            from hyperion.tools.searxng import referenced_engines

            fleet_healthy = get_engine_health().healthy_count(referenced_engines())
            found: dict[str, Any] = {}
            if fleet_healthy >= 2:
                for query in queries:
                    try:
                        response = await client.search(query=query, num_results=5)
                    except Exception as exc:  # noqa: BLE001 - escalation is best-effort
                        logger.warning("escalate search '%s' failed: %s", query[:60], exc)
                        continue
                    if response and response.results:
                        for r in response.results:
                            if r.url:
                                found.setdefault(r.url, r)
            else:
                logger.warning(
                    "RETRIEVAL ESCALATION: only %d/%d fleet engines healthy — "
                    "skipping SearxNG, using direct API legs",
                    fleet_healthy, len(referenced_engines()),
                )

            # OVERHAUL4 P5.2/P5.3: direct-API recovery legs (they bypass the
            # SearxNG suspensions entirely — the same engines that are dead in
            # the pool are healthy through their own clients) + a wall-clock
            # cap so the escalation cannot consume the quality-loop budget.
            from types import SimpleNamespace

            def _as_hit(item: Any) -> SimpleNamespace:
                """Normalize any direct-API item to title/url/snippet/date.

                OVERHAUL4 P5.2 FIX: this used to drop ``published_date``, and
                the persistence loop below reads ``result.published_date`` — so
                every direct-API hit (OpenAlex/Scholar/Jina) crashed the whole
                escalation with AttributeError and recovered 0. The direct-API
                legs P5 built to bypass the dead fleet were therefore
                dead-on-arrival at the persist step. ``published_date`` is
                carried through (either spelling) so recovery survives.
                """
                return SimpleNamespace(
                    title=getattr(item, "title", "") or "",
                    url=getattr(item, "url", "") or "",
                    snippet=(
                        getattr(item, "abstract", "")
                        or getattr(item, "tldr", "")
                        or ""
                    ),
                    published_date=(
                        getattr(item, "published_date", None)
                        or getattr(item, "publication_date", None)
                        or None
                    ),
                )

            deadline = time.monotonic() + 45.0
            primary = queries[0] if queries else subject

            async def _api_leg(name: str, builder: Any) -> None:
                if time.monotonic() >= deadline:
                    return
                try:
                    items = await builder
                except Exception as exc:  # noqa: BLE001 - one leg must not kill escalation
                    logger.warning("escalate %s leg failed: %s", name, exc)
                    return
                seq = getattr(items, "results", None)
                if seq is None:
                    seq = items
                for item in seq or []:
                    hit = _as_hit(item)
                    if hit.url:
                        found.setdefault(hit.url, hit)

            from hyperion.tools.openalex import OpenAlexClient
            from hyperion.tools.semantic_scholar import SemanticScholarClient

            await _api_leg(
                "openalex",
                OpenAlexClient().search_works(primary, limit=5),
            )
            await _api_leg(
                "semantic-scholar",
                SemanticScholarClient().search(primary, limit=5),
            )
            try:
                from hyperion.config import get_settings
                from hyperion.tools.jina import JinaClient

                jina = JinaClient(settings=get_settings())
                await _api_leg("jina", jina.search(query=primary, num_results=5))
                await jina.close()
            except Exception as exc:  # noqa: BLE001 - Jina is optional
                logger.warning("escalate jina leg failed: %s", exc)

            # A count is not evidence. The previous implementation incremented
            # total_sources but discarded every recovered URL, so QualityGate
            # correctly saw zero cited domains immediately afterward. Persist
            # recovered sources onto the report before claiming recovery.
            citation_target = None
            if getattr(report, "sections", None):
                citation_target = report.sections[0]
            elif getattr(report, "key_findings", None):
                citation_target = report.key_findings[0]
            if citation_target is None:
                logger.error(
                    "RETRIEVAL ESCALATION: recovered 0 — reason: report has no "
                    "sections or key_findings to attach recovered sources to"
                )
                return 0

            from hyperion.schemas.models import Source, SourceCredibility, SourceType
            from hyperion.tools.source_classifier import classify_source_type

            credibility_by_type = {
                SourceType.GOVERNMENT: SourceCredibility.GOVERNMENT,
                SourceType.ACADEMIC: SourceCredibility.PEER_REVIEWED,
                SourceType.INDUSTRY: SourceCredibility.INDUSTRY_REPORT,
                SourceType.NEWS: SourceCredibility.NEWS,
                SourceType.REFERENCE: SourceCredibility.BLOG,
                SourceType.BLOG: SourceCredibility.BLOG,
                SourceType.UNKNOWN: SourceCredibility.BLOG,
            }
            existing_urls = {
                source.url
                for section in getattr(report, "sections", [])
                for source in getattr(section, "sources", [])
                if source.url
            }
            existing_urls.update(
                source.url
                for finding in getattr(report, "key_findings", [])
                for source in getattr(finding, "sources", [])
                if source.url
            )
            recovered = 0
            for url, result in found.items():
                if url in existing_urls:
                    continue
                source_type = classify_source_type(url)
                citation_target.sources.append(Source(
                    id=f"retrieval_{len(existing_urls) + recovered:04d}",
                    title=result.title or url,
                    url=url,
                    credibility=credibility_by_type[source_type],
                    publication_date=result.published_date or None,
                    key_data=(result.snippet or "")[:500] or None,
                ))
                recovered += 1
            report.total_sources = len(existing_urls) + recovered
            logger.info(
                "RETRIEVAL ESCALATION: recovered %d new source(s) via targeted "
                "round (queries=%d)",
                recovered, len(queries),
            )
            return recovered
        except Exception as exc:  # noqa: BLE001 - logged, treated as no recovery
            logger.error(
                "RETRIEVAL ESCALATION: recovered 0 — reason: %s: %s",
                type(exc).__name__, exc,
            )
            return 0

    async def _execute_dag(self, dag: WorkflowDAG) -> dict[str, Any]:
        """Execute the DAG in topological order — specialists through Quality Gate.

        This runs Stages 1-4: specialists → fact checker → synthesis → quality gate.
        Delivery tasks (Presentation Designer, Data Visualizer, Render Engine) are
        deliberately skipped here — they run AFTER the quality iteration loop
        on the final iterated report, not on the initial draft.
        """
        max_iterations = 100  # Safety valve — prevent infinite loops
        iteration = 0

        # P4.4: the loop's progress budget — consecutive iterations that add
        # ZERO new evidence domains are a stop signal, not a retry reason.
        max_zero_progress = 2

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
            domains_before = self._ledger_domains()
            await self._execute_wave(ready_non_delivery, dag)

            # P4.4: record Δ evidence after this wave. Zero new domains across
            # consecutive waves means the fleet is yielding nothing — terminate
            # the DAG rather than burn the remaining budget on empty waves.
            if not self._record_wave_progress(domains_before, max_zero_progress):
                # Progress budget exhausted — stop the loop; downstream will
                # handle the thin corpus (AMBER) or report the limitation.
                self._log(
                    f"CORPUS PROGRESS SIGNAL: {self._consecutive_zero_progress} consecutive iteration(s) with "
                    f"zero new evidence domains — terminating the DAG wave loop",
                )
                break

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

        # W-08: the iteration cap is settings-driven (default 4, was 2) and
        # the loop is bounded by a wall-clock budget so a raised cap cannot
        # turn a 34-minute engagement into a two-hour one.
        try:
            from hyperion.config import get_settings as _get_quality_settings
            _qs = _get_quality_settings()
            max_iterations = int(getattr(_qs, "max_quality_iterations", self.MAX_QUALITY_ITERATIONS))
            wall_clock = float(getattr(_qs, "quality_iteration_wall_clock_seconds", 900))
        except Exception:  # noqa: BLE001 - fall back to class constants
            max_iterations = self.MAX_QUALITY_ITERATIONS
            wall_clock = 900.0
        loop_started = time.time()
        prev_total: float | None = None

        # P7: Content-aware source-count floor — if the report has fewer
        # sources than this, stop iterating because more passes won't fix
        # thin evidence; the problem is insufficient data, not insufficient
        # synthesis.  This prevents wasteful LLM calls on thin reports.
        source_floor = 3
        try:
            from hyperion.config import get_settings
            _cfg = get_settings()
            source_floor = getattr(_cfg, "quality_source_floor", 3)
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_quality_iteration_loop", exc)

        for iteration in range(1, max_iterations + 1):
            iterations = iteration

            # W-08: wall-clock budget. Running out of time is NOT approval;
            # it ends the loop and the terminal-state computation below
            # decides the outcome from the last real score.
            if time.time() - loop_started > wall_clock:
                self._log(
                    f"QUALITY: wall-clock budget ({wall_clock:.0f}s) exhausted "
                    f"after {iteration - 1} iteration(s) — stopping loop"
                )
                if current_score is not None:
                    current_score.max_iterations_reached = True
                break

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

            if current_score is None:
                self._log(f"QUALITY iteration {iteration}: no score returned")
                break

            self._log(
                f"QUALITY iteration {iteration}/{max_iterations}: "
                f"score={current_score.total_score:.1f}/{current_score.threshold:.1f} "
                f"approved={current_score.approved} "
                f"critical={len(current_score.critical_dimensions)} "
                f"gaps={len(current_score.gaps)}"
            )

            # P2-22: exit the loop only on the authoritative `approved` flag,
            # not on the weighted score alone. `approved` already folds in
            # the score threshold (see QualityGate._determine_approval) AND
            # the Layer 4 hard-blocker scan (QualityGate._detect_hard_blockers),
            # which catches leaked objects, banned filler, verdict
            # contradictions and dishonest confidence. Reading `total_score`
            # here let reports with `approved=False` ship anyway.
            if current_score.approved:
                self._log(f"QUALITY: approved at iteration {iteration}")
                break  # Quality gate approved

            # W-08: an iteration that produced no score change on any
            # dimension terminates the loop early. Looping without
            # improvement is the signal that the input is the problem, not
            # the polish — more passes will not fix it.
            if prev_total is not None and abs(current_score.total_score - prev_total) < 1e-9:
                self._log(
                    f"QUALITY: iteration {iteration} produced no score change "
                    f"({current_score.total_score:.2f}) — terminating early; "
                    "the input, not the polish, is the problem"
                )
                current_score.max_iterations_reached = True
                break
            prev_total = current_score.total_score

            # F-10: the CORPUS FLOOR integrity blocker is the strongest
            # thin-evidence signal. When the gate reports it, run a targeted
            # retrieval escalation with the corpus floor (8 distinct domains)
            # BEFORE the generic source-count floor check — a report with
            # many sources from one domain must still get a retrieval round
            # instead of blocking.
            if any(
                "CORPUS FLOOR" in blocker
                for blocker in (current_score.integrity_blockers or [])
            ):
                corpus_floor = self._CORPUS_FLOOR_SOURCE_FLOOR
                self._log(
                    f"QUALITY: CORPUS FLOOR blocker active — running targeted "
                    f"retrieval escalation with floor {corpus_floor}"
                )
                proceeded = await self._handle_thin_evidence(
                    current_report, corpus_floor
                )
                if not proceeded:
                    # F-09: the corpus-floor blocker is the STRONGEST
                    # thin-evidence signal, and its failed escalation is
                    # TERMINAL — the report must not keep spending LLM polish
                    # on an evidence base the integrity gate already refused.
                    # The old code only logged here, so the loop continued to
                    # ``iterate_on_quality`` and the final state claimed
                    # recovery was attempted while the score never changed
                    # (the audit's exact F-09/E-12 defect).
                    self._log(
                        f"QUALITY: corpus-floor retrieval escalation failed — "
                        f"report remains below {corpus_floor} distinct domains; "
                        "ending with terminal INSUFFICIENT_EVIDENCE state"
                    )
                    current_score.max_iterations_reached = True
                    break
                self._log(
                    f"QUALITY: corpus-floor retrieval escalation recovered "
                    f"sources (now {getattr(current_report, 'total_sources', 0)})"
                )

                # OVERHAUL2 S12: polishing cannot create evidence. A CORPUS
                # FLOOR integrity blocker skips quality iteration entirely —
                # whether the escalation above recovered sources or not. Two
                # prose-polish passes on a sourceless floor report is how the
                # 17:52 run burned its last minutes before the no-score-change
                # early-terminate fired. The report's floor is now known; the
                # terminal-state computation below renders the honest verdict.
                self._log(
                    "QUALITY: CORPUS FLOOR blocker — skipping iterations, "
                    "terminal BLOCKED"
                )
                current_score.max_iterations_reached = True
                break

            # P2-25: thin evidence triggers retrieval escalation, not a stop.
            # The old content-aware stop broke here; now a below-floor source
            # count dispatches a targeted retrieval round (new engines,
            # reformulated queries). Only a failed escalation is terminal, and
            # then as a stated limitation, never silently.
            report_sources = getattr(current_report, "total_sources", 0)
            if report_sources < source_floor:
                proceed = await self._handle_thin_evidence(current_report, source_floor)
                if not proceed:
                    self._log(
                        f"QUALITY: retrieval escalation failed — "
                        f"{report_sources} sources (< floor {source_floor}) and "
                        "no recovery; stopping with a stated evidence limitation."
                    )
                    current_score.max_iterations_reached = True
                    break
                self._log(
                    f"QUALITY: retrieval escalation recovered sources "
                    f"(was {report_sources}, now {getattr(current_report, 'total_sources', 0)})"
                )

            # Score below threshold — iterate with targeted fixes
            if iteration < max_iterations:
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
                self._log(f"QUALITY: max iterations ({max_iterations}) reached — loop ends")

        # W-08: iteration exhaustion is recorded for DIAGNOSTICS ONLY. It is
        # never read in a ship condition — the terminal-state computation
        # below is the single ship/no-ship decision point, and it does not
        # reference max_iterations_reached.
        if current_score and not current_score.approved and iterations >= max_iterations:
            current_score.max_iterations_reached = True

        # W-08: three terminal states, computed here and only here.
        if current_score is not None:
            self._compute_quality_terminal_state(current_score)
            self._log(
                f"QUALITY: terminal state = {current_score.terminal_state.value} "
                f"(score={current_score.total_score:.2f}, "
                f"blockers={len(current_score.integrity_blockers)})"
            )

        final_score = current_score or QualityScore(
            dimensions=[],
            total_score=0.0,
            approved=False,
            iteration=max(iterations, 1),
            gaps=["Quality Gate did not produce a score"],
            critical_dimensions=[],
            max_iterations_reached=True,
            terminal_state=QualityTerminalState.BLOCKED,
            blocked_reason="Quality Gate did not produce a score",
        )

        # Mark the quality_gate task as COMPLETED in the DAG so that
        # delivery tasks (presentation_designer, etc.) that depend on
        # task_quality_gate can proceed.
        for task in dag.tasks:
            if task.agent == AgentName.QUALITY_GATE and task.status != TaskStatus.COMPLETED:
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
                task.output = final_score.model_dump()
                self._task_outputs[task.id] = final_score
                self._publish_task_update(task)
                break

        return current_report, final_score, iterations

    def _compute_quality_terminal_state(self, score: QualityScore) -> None:
        """W-08: compute the Quality Gate terminal state. The ONLY ship/no-ship decision point.

        Mutates ``score.terminal_state`` (and ``score.blocked_reason`` when
        BLOCKED) in place. Called once by ``_quality_iteration_loop`` after the
        iteration loop ends, and read once by ``run_engagement`` to decide
        whether Stage 5 (delivery) runs.

        Derivation, in order:
        - BLOCKED: any integrity blocker (leaked object, banned filler, verdict
          contradiction, dishonest confidence, broken URL), OR total score below
          the configured ship floor (``quality_ship_floor``, default 3.0).
        - APPROVED: the gate's authoritative ``approved`` flag (score >=
          threshold AND no hard blockers, per QualityGate._determine_approval).
        - otherwise SHIP_WITH_CAVEAT, but only when the operator has explicitly
          set ``allow_ship_with_caveat``; without that setting the run is
          BLOCKED, because silent degradation is the failure mode this item
          exists to remove.

        ``max_iterations_reached`` is deliberately NOT read here. Iteration
        exhaustion is a diagnostic, never a ship condition.
        """
        try:
            from hyperion.config import get_settings

            _s = get_settings()
            ship_floor = float(getattr(_s, "quality_ship_floor", 3.0))
            allow_caveat = bool(getattr(_s, "allow_ship_with_caveat", False))
        except Exception:  # noqa: BLE001 - fall back to spec defaults
            ship_floor = 3.0
            allow_caveat = False

        blockers = list(score.integrity_blockers or [])
        if blockers or score.total_score < ship_floor:
            score.terminal_state = QualityTerminalState.BLOCKED
            reasons: list[str] = []
            if blockers:
                reasons.append(
                    f"{len(blockers)} integrity blocker(s): " + "; ".join(blockers[:5])
                )
            if score.total_score < ship_floor:
                critical_names = [
                    d.value if hasattr(d, "value") else str(d)
                    for d in (score.critical_dimensions or [])
                ]
                reasons.append(
                    f"score {score.total_score:.2f} below ship floor {ship_floor:.2f}"
                )
                if critical_names:
                    reasons.append(
                        f"critical dimensions failing: {', '.join(critical_names)}"
                    )
            score.blocked_reason = " | ".join(reasons)
            return

        if score.approved:
            score.terminal_state = QualityTerminalState.APPROVED
            return

        if allow_caveat:
            score.terminal_state = QualityTerminalState.SHIP_WITH_CAVEAT
            return

        score.terminal_state = QualityTerminalState.BLOCKED
        score.blocked_reason = (
            f"score {score.total_score:.2f} below approval threshold "
            f"{score.threshold:.2f} and allow_ship_with_caveat is disabled"
        )

    # ─────────────────────────────────────────────────────────────────────
    # OVERHAUL3 D-F — the Recovery Supervisor (overhaul3_audit.md §5.1)
    # ─────────────────────────────────────────────────────────────────────
    #
    # BLOCKED is a diagnostic input, not an exit. When the Quality Gate
    # blocks, classify each integrity blocker into a RecoveryClass, plan a
    # remediation that can_make_progress (P3), re-dispatch ONLY the
    # responsible agent(s) with blocker-specific directives (idempotent task
    # ids), re-score via the EXISTING ``_quality_iteration_loop``, and commit
    # only when the score strictly improves (monotonicity). Bounded by
    # ``config.quality_recovery_max_passes`` and a wall-clock sub-budget. The
    # supervisor never overrides the gate — it changes the INPUT, then asks
    # the same gate again (§5.3 non-authoritative).

    _RECOVERY_CLASS_PLACEHOLDER = "PLACEHOLDER_VALUE"
    _RECOVERY_CLASS_VERDICT = "VERDICT_CONFLICT"
    _RECOVERY_CLASS_SECTION = "MISSING_SECTION"
    _RECOVERY_CLASS_THIN = "THIN_EVIDENCE"
    _RECOVERY_CLASS_PRESENTATION = "PRESENTATION_DEFECT"

    # dimension-name substring → specialist that owns it. Used to pick the
    # responsible agent for a DATA VOID blocker, whose text names no agent;
    # the report's critical dimensions / gaps usually do ("Risk Coverage").
    _RECOVERY_DIMENSION_AGENTS = {
        "risk": AgentName.RISK_ANALYST,
        "market": AgentName.MARKET_ANALYST,
        "financial": AgentName.FINANCIAL_ANALYST,
        "compet": AgentName.COMPETITIVE_INTEL,
        "technolog": AgentName.TECHNOLOGY_ANALYST,
        "operation": AgentName.OPERATIONS_ANALYST,
        "regulator": AgentName.REGULATORY_ANALYST,
        "sustainab": AgentName.SUSTAINABILITY_ANALYST,
        "consumer": AgentName.CONSUMER_INSIGHTS,
        "m&a": AgentName.MA_ANALYST,
        "innovat": AgentName.INNOVATION_ANALYST,
        "strateg": AgentName.STRATEGY_ANALYST,
    }

    def _recovery_config(self) -> dict[str, Any]:
        """OVERHAUL3 D-F: read the bounded recovery-loop knobs (§5.5)."""
        try:
            from hyperion.config import get_settings

            _s = get_settings()
            return {
                "max_passes": int(getattr(_s, "quality_recovery_max_passes", 1)),
                "min_gain": float(getattr(_s, "quality_recovery_min_score_gain", 0.05)),
                "wall_clock": float(getattr(_s, "recovery_wall_clock_seconds", 300)),
            }
        except Exception:  # noqa: BLE001 - fall back to spec defaults
            return {"max_passes": 1, "min_gain": 0.05, "wall_clock": 300.0}

    def _recovery_agent_for_text(self, *texts: str) -> AgentName | None:
        """Pick the specialist that owns a blocker from dimension names."""
        blob = " ".join(t for t in texts if t).lower()
        for key, agent in self._RECOVERY_DIMENSION_AGENTS.items():
            if key in blob:
                return agent
        return None

    def _remediation_for(
        self, blocker: str, score: QualityScore,
    ) -> dict[str, Any] | None:
        """DIAGNOSE + PLAN (§5.2): classify one blocker into a remediation.

        Returns an action dict ``{recovery_class, agent, directive, description}``
        or None when nothing is capable of helping (P3 → dropped).
        """
        text = blocker or ""
        low = text.lower()

        if "corpus floor" in low:
            return {
                "recovery_class": self._RECOVERY_CLASS_THIN,
                "agent": None,
                "directive": (
                    "escalate retrieval on living source classes only; if the "
                    "fleet is dead, degrade to a floor report with the evidence "
                    "limitation stated"
                ),
                "description": "Recovery: retrieval escalation for corpus floor",
            }

        if "verdict contradiction" in low:
            return {
                "recovery_class": self._RECOVERY_CLASS_VERDICT,
                "agent": AgentName.SYNTHESIS_LEAD,
                "directive": (
                    "single-verdict reconciliation: pick ONE recommendation and "
                    "purge every conflicting phrase from cover, summary and "
                    "body — never leave two verdicts in one report"
                ),
                "description": (
                    f"RECOVERY: reconcile to a single verdict — {text[:160]}"
                ),
            }

        if "risk" in low and (
            "no risk analysis" in low or "risk_coverage" in low
            or "missing risk section" in low
        ):
            # D-K (overhaul3_audit.md): the RISK findings already exist on the
            # bus — this is a body-assembly wiring gap, not a research gap, so
            # no re-research. Re-synthesis maps the aggregate → report.
            return {
                "recovery_class": self._RECOVERY_CLASS_SECTION,
                "agent": AgentName.SYNTHESIS_LEAD,
                "directive": (
                    "rebuild the report body from the bus's RISK aggregate "
                    "payload (the findings already exist — no re-research); "
                    "assign FinalReport.risk_analysis from them"
                ),
                "description": "RECOVERY: populate risk_analysis from RISK findings",
            }

        if "data void" in low or "'unknown'" in low or "out of scope" in low:
            # PLACEHOLDER_VALUE (§5.2): re-dispatch the finding-bearing
            # specialist that emitted the placeholder with the hard directive
            # "no data is a typed gap; never emit 'Unknown' as a value".
            agent = self._recovery_agent_for_text(
                text,
                " ".join(d.value if hasattr(d, "value") else str(d)
                          for d in (score.critical_dimensions or [])),
                " ".join(score.gaps or []),
            ) or AgentName.FINANCIAL_ANALYST
            return {
                "recovery_class": self._RECOVERY_CLASS_PLACEHOLDER,
                "agent": agent,
                "directive": (
                    "no data is a typed research_gap; NEVER emit 'Unknown' or "
                    "'OUT OF SCOPE' as a data value — emit a typed gap finding "
                    "instead and state the limitation in prose"
                ),
                "description": (
                    f"RECOVERY: re-run with the no-placeholder directive — "
                    f"{text[:120]}"
                ),
            }

        # Leaked object / banned filler / broken URL / meta-text — a targeted
        # synthesis polish of the offending field.
        return {
            "recovery_class": self._RECOVERY_CLASS_PRESENTATION,
            "agent": AgentName.SYNTHESIS_LEAD,
            "directive": (
                "repair the presentation defect flagged by the Quality Gate: "
                "remove the offending value or string and never re-introduce it"
            ),
            "description": f"RECOVERY: presentation polish — {text[:160]}",
        }

    async def _dispatch_recovery(
        self, action: dict[str, Any], dag: WorkflowDAG, pass_no: int,
    ) -> Any:
        """RECOVER (§5.1 step 4): re-dispatch ONE responsible agent.

        Deterministic ``task_recover_<pass>_<agent>`` ids make re-entry a
        no-op (the reframer's ``dag.get_task`` guard) — no variant-tree
        explosion (§5.3 idempotent). Returns the produced output when the
        agent ran, else None. Best-effort: a recovery dispatch failure is
        logged, never fatal.
        """
        agent = action["agent"]
        if agent is None:
            return None  # THIN_EVIDENCE is handled by the caller
        task_id = f"task_recover_{pass_no}_{agent.value}"
        if dag.get_task(task_id) is not None:
            return self._task_outputs.get(task_id)

        task = TaskNode(
            id=task_id,
            agent=agent,
            model_tier="standard",
            description=action["description"],
            dependencies=[],
            status=TaskStatus.PENDING,
        )
        try:
            dag.add_task(task)
            self._publish_task_update(task)
        except Exception as exc:  # noqa: BLE001 - recovery is best-effort
            logger.warning("recovery task %s could not be added: %s", task_id, exc)
            return None

        if agent == AgentName.SYNTHESIS_LEAD:
            # The directive must reach the agent as fix_instructions — the
            # quality loop uses the same argument for its fix-instruction pass.
            # Mirror _execute_task's findings injection so the re-synthesis
            # reads the bus-fed corpus (P1).
            synth = self._get_agent(agent)
            try:
                if hasattr(synth, "_collected_findings"):
                    existing_ids = {id(f) for f in synth._collected_findings}
                    for finding in self._all_findings:
                        if id(finding) not in existing_ids:
                            synth._collected_findings.append(finding)
                            if finding.agent not in synth._findings_by_agent:
                                synth._findings_by_agent[finding.agent] = []
                            synth._findings_by_agent[finding.agent].append(finding)
                else:
                    synth._collected_findings = list(self._all_findings)
                    synth._findings_by_agent = {}
                    for finding in self._all_findings:
                        synth._findings_by_agent.setdefault(finding.agent, []).append(finding)
            except Exception as exc:  # noqa: BLE001 - injection is best-effort
                logger.warning("recovery synthesis injection failed: %s", exc)
            try:
                result = await asyncio.wait_for(
                    synth.run(
                        engagement_id=self._engagement_id,
                        question=dag.question,
                        dag=dag,
                        fix_instructions=action["directive"],
                    ),
                    timeout=self.SPECIALIST_TIMEOUT_SECONDS,
                )
            except Exception as exc:  # noqa: BLE001 - recovery must not crash the run
                logger.warning("recovery synthesis re-run failed: %s", exc)
                task.status = TaskStatus.FAILED
                task.error = str(exc)[:300]
                self._publish_task_update(task)
                return None
        else:
            try:
                result = await self._execute_task(task, dag)
            except Exception as exc:  # noqa: BLE001 - recovery must not crash the run
                logger.warning("recovery dispatch for %s failed: %s", agent.value, exc)
                return None
        if result is None:
            return None
        if isinstance(result, BaseModel):
            task.output = result.model_dump()
        task.status = TaskStatus.COMPLETED
        task.completed_at = time.time()
        self._task_outputs[task_id] = result
        self._publish_task_update(task)
        return result

    def _record_recovery_pass(
        self,
        pass_no: int,
        plan: list[dict[str, Any]],
        score_before: QualityScore,
        score_after: QualityScore,
        committed: bool,
    ) -> None:
        """OVERHAUL3 D-F (§5.5): append one pass to the telemetry + manifest."""
        classes = [a["recovery_class"] for a in plan]
        entry = {
            "pass": pass_no,
            "blocker_classes": classes,
            "agents": [
                a["agent"].value if a["agent"] is not None else None
                for a in plan
            ],
            "score_before": round(score_before.total_score, 3),
            "score_after": round(score_after.total_score, 3) if score_after else None,
            "committed": committed,
        }
        self._recovery_telemetry["passes_detail"].append(entry)
        for cls in classes:
            bucket = self._recovery_telemetry["outcomes_by_class"].setdefault(
                cls, {"committed": 0, "discarded": 0, "skipped": 0}
            )
            bucket["committed" if committed else "discarded"] += 1
        if self._manifest is not None:
            try:
                self._manifest.ledger.setdefault("recovery_passes", []).append(entry)
            except Exception as exc:  # noqa: BLE001 - telemetry must not break recovery
                logger.debug("recovery manifest record failed: %s", exc)

    async def _recover_from_blocked(
        self,
        dag: WorkflowDAG,
        report: FinalReport,
        score: QualityScore,
        fact_check_report: FactCheckReport | None,
    ) -> tuple[FinalReport, QualityScore]:
        """OVERHAUL3 D-F (§5.1): the bounded BLOCKED → diagnose → plan →
        recover → re-score → decide state machine.

        Invoked from the BLOCKED branch of ``run_engagement`` BEFORE the
        ``return result``. Re-uses the existing ``_quality_iteration_loop`` as
        the scorer (no second ship/no-ship authority) and ``_write_blocked_diagnostic``
        for the honest give-up. Returns ``(best_report, best_score)`` — the
        caller re-reads ``terminal_state``; when still BLOCKED the run ends
        with the recovery attempt RECORDED, never a discarded diagnosis.
        """
        cfg = self._recovery_config()
        max_passes = cfg["max_passes"]
        if max_passes <= 0:
            return report, score  # feature-flagged off → old behaviour
        self._recovery_telemetry["attempted"] = True
        self._log(
            "RECOVERY: BLOCKED verdict entered the supervisor — "
            f"classifying {len(score.integrity_blockers or [])} blocker(s)"
        )
        deadline = time.time() + cfg["wall_clock"]
        best_report, best_score = report, score  # monotonicity snapshot

        for pass_no in range(1, max_passes + 1):
            if time.time() > deadline:
                self._log("RECOVERY: wall-clock budget exhausted — degrading")
                break

            # DIAGNOSE: integrity blockers plus the risk-coverage dimension
            # (D-K: risk_coverage=1 with risk_analysis None is not a hard
            # blocker string, but it IS a MISSING_SECTION recovery signal).
            signals = list(best_score.integrity_blockers or [])
            for dim in (best_score.critical_dimensions or []):
                dname = dim.value if hasattr(dim, "value") else str(dim)
                if str(dname).lower() == "risk_coverage":
                    signals.append(
                        "MISSING RISK SECTION: risk_coverage failing with "
                        "risk_analysis absent"
                    )
            # PLAN + P3: drop signals nothing is capable of helping.
            plan = [
                action
                for signal in signals
                for action in [self._remediation_for(signal, best_score)]
                if action is not None
            ]
            if not plan:
                self._log(
                    "RECOVERY: no remediation capable of changing this block — "
                    "degrading honestly"
                )
                break

            # RECOVER: re-dispatch only the responsible agents.
            recovered_report = best_report
            for action in plan:
                self._log(
                    f"RECOVERY pass {pass_no}: {action['recovery_class']} → "
                    f"{action['agent'].value if action['agent'] is not None else 'retrieval'}"
                )
                outcome = await self._dispatch_recovery(action, dag, pass_no)
                if (
                    outcome is not None
                    and action["agent"] == AgentName.SYNTHESIS_LEAD
                    and isinstance(outcome, FinalReport)
                ):
                    recovered_report = outcome

            # RE-SCORE via the existing authority.
            cand_report, cand_score, _ = await self._quality_iteration_loop(
                dag, recovered_report, fact_check_report,
            )
            if cand_score is None:
                break
            approved = (
                cand_score.approved
                or cand_score.terminal_state == QualityTerminalState.APPROVED
            )
            gain = cand_score.total_score - best_score.total_score
            committed = approved or gain >= cfg["min_gain"]
            self._record_recovery_pass(pass_no, plan, best_score, cand_score, committed)
            self._recovery_telemetry["passes"] = pass_no
            if approved:
                self._recovery_telemetry["recovered"] = True
                return cand_report, cand_score  # SHIP — Stage 5 runs
            if gain >= cfg["min_gain"]:
                self._log(
                    f"RECOVERY pass {pass_no} committed: {best_score.total_score:.2f} "
                    f"→ {cand_score.total_score:.2f}"
                )
                best_report, best_score = cand_report, cand_score
                continue
            self._log(
                f"RECOVERY pass {pass_no} discarded (score {cand_score.total_score:.2f} "
                f"not ≥ best+{cfg['min_gain']:.2f}) — keeping best, degrading"
            )
            break

        return best_report, best_score

    def _attach_methodology(self, report: Any, dag: Any) -> None:
        """W-10: attach the six-subsection methodology record to the report.

        Built from recorded structures only (the DAG's W-06 roster decisions,
        the W-07 insufficiency resolutions, the fact checker's counters and the
        Source corpus), never from an LLM prompt: a prompt asked to "describe
        the methodology" will describe research that did not happen, which is
        precisely W-10's third failure mode.

        A failure here must not take the engagement down. The methodology page
        is important but it is not the answer, and the designer carries a
        report-only fallback plus a template branch that says so honestly. The
        exception types are enumerated rather than blanket-caught (ruff BLE001):
        ``ValueError`` covers a ClientProse rejection inside the record,
        ``TypeError``/``AttributeError`` cover a malformed report or DAG.
        """
        from hyperion.output.methodology import build_methodology
        from hyperion.tools.deep_search import engagement_yield_report

        try:
            retrieval = engagement_yield_report()
            report.methodology = build_methodology(
                report,
                dag=dag,
                resolutions=list(getattr(self, "_insufficiency_resolutions", []) or []),
                backend_query_counts=retrieval.get("backend_query_counts", {}),
                retrieval_constraints=retrieval.get("retrieval_constraints", []),
            )
            n = len(report.methodology.subsections)
            self._log(f"METHODOLOGY: {n} subsections built from recorded structures")
        except (ValueError, TypeError, AttributeError) as exc:
            logger.warning("W-10: methodology build failed: %s", exc)
            self._log(f"METHODOLOGY: build failed ({exc})")

    def _write_blocked_diagnostic(self, score: QualityScore) -> str:
        """W-08: write the machine-readable operator diagnostic for a BLOCKED run.

        Contains dimension scores, hard blockers, open gaps, corpus statistics
        and roster decisions, so a blocked run is actionable instead of merely
        disappointing. Written under ``<reports_dir>/diagnostics/`` and NEVER
        to the deliverable path — a blocked run produces no client artifact.

        Returns the diagnostic file path ("" if writing failed; the failure is
        logged, never raised — the block decision itself must not fail).
        """
        import json as _json

        try:
            from hyperion.config import get_settings

            reports_dir = get_settings().reports_dir
        except Exception:  # noqa: BLE001 - settings may be unconfigured on a
            # blocked run; the diagnostic must still be written, so fall back
            # to the conventional ./reports directory rather than raise.
            reports_dir = Path("./reports")

        try:
            diag_dir = reports_dir / "diagnostics"
            diag_dir.mkdir(parents=True, exist_ok=True)
            path = diag_dir / f"blocked_{self._engagement_id or 'unknown'}.json"

            dimensions = [
                {
                    "dimension": d.dimension_id.value,
                    "score": d.score,
                    "rationale": d.feedback[:500],
                }
                for d in (score.dimensions or [])
            ]
            roster_decisions = []
            if self._director is not None:
                roster_decisions = list(
                    getattr(self._director, "roster_decisions", []) or []
                )
            diagnostic = {
                "engagement_id": self._engagement_id,
                "terminal_state": score.terminal_state.value,
                "blocked_reason": score.blocked_reason or "",
                "total_score": score.total_score,
                "threshold": score.threshold,
                "iteration": score.iteration,
                "max_iterations_reached": score.max_iterations_reached,
                "integrity_blockers": list(score.integrity_blockers or []),
                "critical_dimensions": [
                    d.value if hasattr(d, "value") else str(d)
                    for d in (score.critical_dimensions or [])
                ],
                "dimension_scores": dimensions,
                "open_gaps": list(score.gaps or []),
                "fix_priority": list(score.fix_priority or []),
                "corpus_stats": {
                    "findings_collected": len(self._all_findings),
                    "task_outputs": len(self._task_outputs),
                },
                # OVERHAUL3 D-F (§5.5): a blocked run is now replayable — the
                # recovery attempt (or its absence) is recorded, never hidden.
                "recovery": dict(self._recovery_telemetry),
                "roster_decisions": [
                    str(r) for r in roster_decisions
                ],
            }
            path.write_text(_json.dumps(diagnostic, indent=2, default=str))
            self._log(f"QUALITY: operator diagnostic written to {path}")
            return str(path)
        except Exception as exc:  # noqa: BLE001 - logged, never raised
            logger.warning("_write_blocked_diagnostic: %s", exc)
            return ""

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

        # P3.4: Filter out non-substantive findings (research_gaps and
        # unverified_assertions) — these are admission of evidential failure,
        # not evidence. The floor report reports what the system found, not
        # what it failed to find.
        from hyperion.schemas.models import NON_SUBSTANTIVE_FINDING_TYPES
        substantive_findings = [
            f for f in findings
            if f.finding_type not in NON_SUBSTANTIVE_FINDING_TYPES
        ]
        if not substantive_findings:
            self._log(
                "FLOOR-REPORT: all findings are non-substantive (gaps/unverified) — "
                "cannot build a floor report from admission of failure alone"
            )
            return None

        # Group findings by agent
        by_agent: dict[str, list[KeyFinding]] = {}
        for f in substantive_findings:
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
            section_sources = []
            seen_source_urls: set[str] = set()
            for finding in agent_findings:
                for source in finding.sources:
                    if source.url and source.url not in seen_source_urls:
                        seen_source_urls.add(source.url)
                        section_sources.append(source)
            sections.append(AnalysisSection(
                id=f"floor_{agent_name}",
                title=agent_name.replace("_", " ").title(),
                agent=agent_name,
                key_insight=agent_findings[0].title if agent_findings else "No key insight available",
                body="\n\n---\n\n".join(content_parts) or "No content available",
                findings=agent_findings,
                sources=section_sources,
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
            substantive_findings,
            key=lambda f: confidence_order.get(f.confidence, 3),
        )[:5]

        # Build executive summary from findings
        summary_lines = [
            f"This report was generated as a floor-report fallback because the "
            f"Synthesis Lead did not produce a full synthesis. It contains "
            f"{len(substantive_findings)} substantive findings from {len(by_agent)} specialists.",
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
            total_sources=len({
                source.url
                for finding in findings
                for source in getattr(finding, "sources", [])
                if source.url
            }),
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
        if not result.final_report or not result.metadata or result.quality_score is None:
            return

        try:
            from hyperion.config import get_settings
            from hyperion.tools.second_brain import SecondBrainClient

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
        fresh: bool = False,
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
        # W-18: the router singleton can serve multiple consultations in one
        # shell; reset only the engagement cost accumulator, never daily usage.
        # WorkflowEngine intentionally permits router=None for deterministic
        # offline quality-gate runs, which accrue no LLM cost.
        if self.router is not None:
            self.router.budget_planner.reset_engagement_cost()
        # W-20: deterministic run id — the durable-execution journal below
        # only resumes a crashed engagement if a re-run of the same question
        # lands on the SAME run_id. ``fresh=True`` forces a random id for a
        # genuine from-zero re-run (the CLI's ``--fresh`` flag).
        if fresh:
            self._engagement_id = f"eng_{uuid.uuid4().hex[:12]}"
        else:
            self._engagement_id = derive_run_id(question)
        # Reset per-engagement question classification so a new question can
        # never inherit the previous engagement's industry/geography.
        self._engagement_context = None
        clear_engagement_focus()
        # P0 (overhaul §6 P0.1): open the run-scoped Evidence Ledger. Every
        # retrieved URL from here on lands in it BEFORE any LLM sees text,
        # and the Phase-2 corpus preflight will gate the DAG on it.
        from hyperion.tools.evidence_ledger import new_ledger

        self._evidence_ledger = new_ledger(self._engagement_id)
        trace("evidence", run_id=self._engagement_id, event="ledger_started")
        # F-0.1-8: reset the shared per-engagement fetch cache so no URL fetched
        # by a previous engagement leaks into this one.
        try:
            from hyperion.tools.unified_extract import clear_fetch_cache

            clear_fetch_cache()
        except Exception as exc:  # noqa: BLE001 - cache reset must never break boot
            logger.debug("fetch cache reset failed (non-fatal): %s", exc)
        # Fix 2.6 (audit §6 Phase 2): reset the extraction-yield accumulator
        # so engagement_yield_report() covers THIS engagement only.
        try:
            from hyperion.tools.deep_search import reset_engagement_yield
            from hyperion.tools.grounded_search import set_grounding_engagement_id

            reset_engagement_yield()
            set_grounding_engagement_id(self._engagement_id)
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "run_engagement", exc)
        # Seed the search anchor from the raw question immediately, so any
        # search firing before classification completes is still on-topic.
        #
        # WHY THE GAZETTEER IS THE RIGHT TOOL *HERE* SPECIFICALLY. Everywhere
        # else, geography is the Engagement Director's decision — it reads the
        # question and breaks it down, so it scopes it. But this line runs
        # BEFORE the Director has been invoked: there is no decomposition to
        # consult yet, and a search fired during early boot would otherwise go
        # out with no country anchor at all. A deterministic scan of the user's
        # own words is the only signal available at this instant.
        #
        # It is a provisional anchor, and it is superseded: once the Director
        # returns, _get_engagement_context re-publishes the focus from
        # dag.geographies, which wins. Detected-only either way — [] stays ""
        # rather than becoming an invented default.
        try:
            _early_geo = detect_geographies(question or "")
            set_engagement_focus(
                question=question,
                subject=self._derive_subject_from_question(question),
                geography=_early_geo[0] if _early_geo else "",
            )
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "run_engagement", exc)

        # P9 GAP-4: Startup health table — check every tool + tier. Runs BEFORE
        # the journal/artifact setup so a refusal aborts in seconds with no
        # engagement artifacts written (D-06/§4 0.2).
        _settings = None
        try:
            from hyperion.config import get_settings
            from hyperion.obs.health import check_startup_health

            _settings = get_settings()
            check_startup_health(_settings)
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "run_engagement", exc)

        # P2-29: startup credential preflight. One real minimal completion
        # per configured provider. A TCP probe or key-presence check cannot
        # detect a dead key; the deleted Google credential passed every
        # reachability probe for two entire engagements while every
        # completion returned 401. A 401/403 marks the provider
        # UNAUTHENTICATED (distinct from quota) and is logged loudly here.
        if _settings is not None:
            try:
                from hyperion.obs.health import credential_preflight
                from hyperion.router.router import get_router

                preflight = await credential_preflight(get_router())
                dead = [pt.value for pt, s in preflight.items() if s == "UNAUTHENTICATED"]
                if dead:
                    logger.error(
                        "CREDENTIAL PREFLIGHT: unauthenticated providers %s — "
                        "these keys are dead (401/403), NOT rate limited. "
                        "Replace the key; no quota window will recover them.",
                        dead,
                    )
            except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                logger.warning("%s: %s", "run_engagement", exc)

        # D-06/§4 0.2: refuse to start an engagement on a dead research stack.
        # The 07-30 run was allowed to begin with every engine banned and
        # shipped a fabricated report; a stack that returns zero evidence can
        # only produce ungrounded output. Raises BEFORE any DAG or journal is
        # built. DEGRADED (some engines answering) is allowed through.
        if _settings is not None:
            from hyperion.infra.preflight import assert_research_stack_usable

            assert_research_stack_usable(_settings)

        # W-20: resume detection. If this deterministic run_id already has a
        # journal on disk, this invocation is a RESUME of a crashed run, not a
        # fresh engagement. The existing P10 cache-hit path in ``_execute_task``
        # (journal ``get_cached`` -> artifact load -> skip dispatch) does the
        # actual replay; here we surface that fact loudly so an operator can
        # tell a resume from a fresh run and see the frontier being skipped.
        _prior_journal = os.path.join(
            "artifacts", self._engagement_id, "journal.sqlite"
        )
        if os.path.exists(_prior_journal):
            _prior = RunJournal(self._engagement_id)
            _prior.open()
            try:
                _done = _prior.get_completed_steps()
                _failed = _prior.get_failed_steps()
            finally:
                _prior.close()
            self._log(
                f"RESUME: journal found for {self._engagement_id} — "
                f"{len(_done)} completed step(s) will replay from cache, "
                f"{len(_failed)} previously-failed step(s) will re-run"
            )
            trace(
                "durable",
                run_id=self._engagement_id,
                status="resume_detected",
                completed=len(_done),
                failed=len(_failed),
            )

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

        # Reset SearxNG search budget for this engagement
        from hyperion.tools.searxng import SearxNGClient
        SearxNGClient.reset_budget()
        # OVERHAUL4 P8: reset the multi-provider paid search chain (per-run
        # budget buckets + suspension + metrics) at the same boundary.
        try:
            from hyperion.search.orchestrator import reset_search_run

            reset_search_run()
        except Exception as exc:  # noqa: BLE001 - reset is best-effort
            logger.debug("search layer run-reset failed: %s", exc)

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
            # Phase 2 (overhaul §6 P2): Corpus Contract preflight. The
            # system decides whether it CAN research before spending a token
            # on research. RED raises CorpusPreflightError — the existing
            # failure path records the typed INSUFFICIENT_EVIDENCE terminal
            # state in seconds; AMBER runs a reduced DAG with a smaller
            # sub-agent budget; GREEN runs the full DAG.
            # ─────────────────────────────────────────────────────────────
            await self._run_corpus_preflight(question, settings=_settings)

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
            # Stage 4a: GAP_CLOSURE phase (P2-18) — between fact check and
            # quality gate. Specialists have been resting in
            # AWAITING_FOLLOWUP (alive, subscribed) since their initial
            # runs; unresolved gaps are re-dispatched to them here, then
            # their tasks are finalized to COMPLETED.
            # ─────────────────────────────────────────────────────────────
            self._ensure_gap_closure_task(dag)
            synthesis_gaps = getattr(
                self._get_agent(AgentName.SYNTHESIS_LEAD), "section_gaps", []
            ) or []
            closure_gaps = [
                AnalysisGap(
                    id=f"gap_{i}",
                    section_id="synthesis",
                    agent=AgentName.SYNTHESIS_LEAD,
                    field="body",
                    question=g,
                )
                for i, g in enumerate(synthesis_gaps)
            ]
            await self._gap_closure_phase(dag, gaps=closure_gaps)
            # P2-16 sub-fix 5.6: gaps that survived all three closure rounds
            # are declared in the report's limitations with their questions.
            self._record_unresolved_gaps(final_report, closure_gaps)

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

            # ─────────────────────────────────────────────────────────────
            # W-08: the Quality Gate can refuse to ship.
            # BLOCKED means Stage 5 never runs — no HANDOFF to the delivery
            # agents, no Presentation Designer, no Data Visualizer, no Render
            # Engine, no client PDF under any name. The engagement ends with
            # a machine-readable operator diagnostic instead.
            # SHIP_WITH_CAVEAT (opt-in via allow_ship_with_caveat) ships but
            # forces a prominent limitations declaration onto the report.
            # ─────────────────────────────────────────────────────────────
            terminal_state = (
                quality_score.terminal_state if quality_score else QualityTerminalState.BLOCKED
            )
            if terminal_state == QualityTerminalState.BLOCKED:
                blocked_reason = (
                    quality_score.blocked_reason
                    if quality_score and quality_score.blocked_reason
                    else "Quality Gate blocked the run"
                )
                self._log(
                    f"QUALITY: BLOCKED — refusing to ship. {blocked_reason}"
                )

                # ─────────────────────────────────────────────────────────
                # OVERHAUL3 D-F (§5.1): BLOCKED is a diagnostic input, not
                # an exit. Run the bounded Recovery Supervisor BEFORE
                # terminating. It re-dispatches the responsible agent(s)
                # with blocker-specific directives and re-scores via the
                # SAME Quality Gate (never overriding it). If the re-score
                # leaves BLOCKED, fall through to the honest give-up below —
                # now with the recovery attempt recorded.
                # ─────────────────────────────────────────────────────────
                if quality_score is not None:
                    recovered_report, recovered_score = await self._recover_from_blocked(
                        dag, final_report, quality_score, fact_check_report,
                    )
                    if (
                        recovered_score is not None
                        and recovered_score.terminal_state != QualityTerminalState.BLOCKED
                    ):
                        final_report = recovered_report
                        quality_score = recovered_score
                        result.final_report = final_report
                        result.quality_score = quality_score
                        result.quality_iterations += self._recovery_telemetry["passes"]
                        terminal_state = recovered_score.terminal_state
                        self._log(
                            f"QUALITY: RECOVERED after "
                            f"{self._recovery_telemetry['passes']} recovery pass(es) — "
                            f"terminal state {terminal_state.value} "
                            f"(score {quality_score.total_score:.2f}); proceeding"
                        )
                    else:
                        self._log(
                            "QUALITY: still BLOCKED after "
                            f"{self._recovery_telemetry['passes']} recovery pass(es) — "
                            "giving up with the recovery attempt recorded"
                        )

                if terminal_state == QualityTerminalState.BLOCKED:
                    diagnostic_path = self._write_blocked_diagnostic(quality_score) if quality_score else ""
                    await self.bus.publish(
                        channel=Channel.ESCALATION,
                        msg_type=MessageType.ESCALATION,
                        sender=AgentName.QUALITY_GATE,
                        payload={
                            "agent": AgentName.QUALITY_GATE.value,
                            "issue": f"Quality Gate BLOCKED the run: {blocked_reason}",
                            "suggested_action": (
                                "Operator diagnostic written"
                                f"{' to ' + diagnostic_path if diagnostic_path else ''}; "
                                "engagement ends without a deliverable"
                            ),
                        },
                    )
                    result.success = False
                    result.failure_reason = "quality_gate"
                    result.error = f"Quality Gate BLOCKED: {blocked_reason}"
                    result.duration_seconds = time.time() - self._start_time
                    result.metadata = EngagementMetadata(
                        engagement_id=self._engagement_id,
                        question=question,
                        question_type=dag.question_type,
                        agents_used=dag.agents_selected,
                        sources_accessed=sum(
                            1 for f in self._all_findings if hasattr(f, "sources") and f.sources
                        ),
                        data_points_collected=len(self._all_findings),
                        duration_seconds=result.duration_seconds,
                    )
                    if diagnostic_path:
                        result.metadata.blocked_diagnostic_path = diagnostic_path
                    return result

            if terminal_state == QualityTerminalState.SHIP_WITH_CAVEAT:
                caveat_notice = (
                    "QUALITY CAVEAT: this report shipped below the approval "
                    f"threshold (score {quality_score.total_score:.2f}/"
                    f"{quality_score.threshold:.2f}) under the explicit "
                    "allow_ship_with_caveat setting. Findings should be "
                    "treated as provisional and independently verified before "
                    "any decision is made on them."
                )
                if caveat_notice not in final_report.limitations:
                    final_report.limitations.insert(0, caveat_notice)
                self._log(
                    "QUALITY: SHIP_WITH_CAVEAT — limitations page notice prepended "
                    f"(score {quality_score.total_score:.2f} below threshold "
                    f"{quality_score.threshold:.2f})"
                )

            # W-10: build the methodology section HERE, before the handoff.
            # The orchestrator is the only holder of both the DAG (whose
            # roster_decisions carry the W-06 method eligibility, including the
            # exclusions that answer "why is there no company valuation on a
            # nation state?") and the W-07 insufficiency resolutions (which
            # carry the retrieval strategies attempted and the declared-gap /
            # out-of-scope outcomes). The designer can only build a thinner
            # report-only record, so building it here is what makes subsections
            # 1, 2 and 3 substantive. Deterministic and LLM-free by design.
            self._attach_methodology(final_report, dag)

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

            # Execute delivery tasks in topological order. W-03 re-pointed
            # the delivery chain (visualizer -> designer -> render engine),
            # so a single pass in DAG-declaration order would skip the
            # designer permanently. Iterate to a fix-point: each pass
            # executes every task whose dependencies are now met, and the
            # loop stops when no task changed state.
            #
            # W-04: every delivery task is REQUIRED and the stage fails
            # CLOSED. The first failure raises DeliveryFailure — there is no
            # `continue` path that would let a later stage render a PDF on
            # top of a missing chart set or a missing layout (the exact bug
            # that produced a 34-page report with zero charts reported as
            # SUCCESS). Unmet dependencies after the fix-point are likewise
            # an invariant violation, not a skip-and-continue condition.
            self._log(f"DELIVERY: starting {len(delivery_tasks)} delivery tasks")
            progressed = True
            while progressed:
                progressed = False
                for task in delivery_tasks:
                    if task.status != TaskStatus.PENDING:
                        continue
                    # Check if dependencies are met
                    ready = all(
                        (dependency_task := dag.get_task(dep)) is not None
                        and dependency_task.status == TaskStatus.COMPLETED
                        for dep in task.dependencies
                    )
                    if ready:
                        self._log(f"DELIVERY: executing {task.agent.value}")
                        try:
                            await self._execute_task(task, dag)
                        except Exception as e:  # noqa: BLE001 - W-04: fail closed, with the traceback
                            import traceback as _tb

                            task.status = TaskStatus.FAILED
                            task.error = str(e)[:200]
                            await self.bus.publish(
                                channel=Channel.ESCALATION,
                                msg_type=MessageType.ESCALATION,
                                sender=task.agent,
                                payload={
                                    "agent": task.agent.value,
                                    "issue": f"Delivery agent failed: {e!s:.200}",
                                    "suggested_action": "Engagement fails closed — no deliverable",
                                },
                            )
                            raise DeliveryFailure(
                                agent=task.agent.value,
                                exc_type=type(e).__name__,
                                message=str(e)[:300],
                                tb=_tb.format_exc(),
                            ) from e
                        progressed = True
            stuck = [t for t in delivery_tasks if t.status == TaskStatus.PENDING]
            if stuck:
                stuck_agent = stuck[0].agent.value
                unmet: dict[str, str] = {}
                for dep in stuck[0].dependencies:
                    dependency_task = dag.get_task(dep)
                    unmet[dep] = (
                        dependency_task.status.value
                        if dependency_task is not None
                        else "missing"
                    )
                self._log(
                    f"DELIVERY: INVARIANT VIOLATION — {stuck_agent} cannot run, "
                    f"dependencies never completed: {unmet}"
                )
                raise DeliveryFailure(
                    agent=stuck_agent,
                    exc_type="UnmetDependencies",
                    message=f"delivery task could not run; dependency states: {unmet}",
                )

            # Collect delivery outputs
            result.layout_plan = self._get_output_by_agent(dag, AgentName.PRESENTATION_DESIGNER)
            result.visualization_output = self._get_output_by_agent(dag, AgentName.DATA_VISUALIZER)
            result.render_output = self._get_output_by_agent(dag, AgentName.RENDER_ENGINE)

            # Get PDF path — W-03: exactly ONE source, the Render Engine.
            # The deleted `elif layout_plan.pdf_path` branch was the RC-4
            # mechanism: an unaudited designer-rendered PDF could become the
            # deliverable when the designer wrote one and the render engine
            # did not. The designer no longer writes PDFs at all.
            if result.render_output and hasattr(result.render_output, "pdf_path"):
                result.pdf_path = result.render_output.pdf_path

            # W-04: PDF=NO must imply failure — the two can never diverge.
            # An empty pdf_path here means the render engine produced no
            # audited PDF, which is a failed engagement, not a "no-PDF
            # success". (W-02 guarantees a rejected render never occupies the
            # deliverable name, so an empty path is never a quarantine artifact.)
            if not result.pdf_path:
                result.success = False
                result.failure_reason = "delivery"
                if not result.error:
                    result.error = (
                        "Delivery failed closed: the render engine produced no "
                        "audited PDF (verification_failed or render failure)."
                    )
                self._log("DELIVERY: no audited PDF — engagement marked FAILED")
            assert not (not result.pdf_path and result.success), (
                "W-04 invariant: result.pdf_path empty must imply result.success is False"
            )

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

            # Fix 2.6 (audit §6 Phase 2): surface the per-engagement
            # extraction-yield metrics in the run report. This is the number
            # the Phase 2 exit criterion ("extraction success >=60% of
            # discovered URLs; every cited source >=500 chars retained") is
            # computed from — before this fix it was unmeasurable per run.
            try:
                from hyperion.tools.deep_search import engagement_yield_report

                result.extraction_yield = engagement_yield_report()
                ym = result.extraction_yield
                self._log(
                    "EXTRACTION YIELD: "
                    f"{ym['urls_extracted']}/{ym['urls_discovered']} URLs "
                    f"({ym['extraction_yield']:.0%}) extracted, "
                    f"{ym['chars_retained']} chars retained across "
                    f"{ym['sources_cited']} cited sources "
                    f"(avg {ym['avg_chars_per_source']:.0f} chars/source, "
                    f"{ym['search_calls']} search calls)"
                )

                # D-12: zero searches AND zero evidence is a hard failure, not
                # a tidy "0%" log line. The 07-30 run read exactly this state —
                # every engine banned, 0 URLs, 0 chars — yet the metric was
                # instrumented on a path that never executed, so it read 0/0
                # and the run reported SUCCESS over a fabricated report. Now
                # that every search call is recorded (deep_search.search
                # records on every exit, including the zero-discovery early
                # return), this state is real and must flip the run to FAILED.
                failure = zero_evidence_failure(ym)
                if failure:
                    result.success = False
                    result.error = failure
                    self._log(
                        "EXTRACTION YIELD: HARD FAILURE — 0 search calls and 0 "
                        "chars retained. Marking engagement FAILED (ungrounded "
                        "output must not report success)."
                    )
            except Exception as e:
                logger.warning("extraction-yield report failed: %s", e, exc_info=True)

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
            except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                logger.warning("%s: %s", "run_engagement", exc)

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

            # P6.2 (overhaul §6 P6): record the 5 KPIs per run and diff against
            # the previous run. A KPI regression auto-opens the owning phase
            # node (KPI_OWNER_PHASE) so the master loop can route there.
            # OVERHAUL2 S15: also record the Output Contract gates (kpi_6..8).
            try:
                from hyperion.eval.kpi import (
                    RunKPIs,
                    diff_kpis,
                    record_run_kpis,
                    regressed_phase,
                )

                # OC-1 (S15): the share of COMPLETED tasks that carry an output
                # object. The "status=completed but no output" task that crashed
                # synthesis (B-5) drives this below 100.
                _completed = [
                    t for t in dag.tasks if t.status == TaskStatus.COMPLETED
                ]
                _completed_with_output = [
                    t for t in _completed if t.id in self._task_outputs
                ]
                _pct_completed_with_output = (
                    100.0 * len(_completed_with_output) / len(_completed)
                    if _completed else -1.0
                )
                # S11 (S15): sum the per-agent off-topic drop counters (the
                # parent specialists increment their own ``_off_topic_dropped``).
                _off_topic_dropped_total = 0
                for _agent_inst in (getattr(self, "_agents", None) or {}).values():
                    _off_topic_dropped_total += getattr(
                        _agent_inst, "_off_topic_dropped", 0
                    )

                kpis = RunKPIs(
                    run_id=self._engagement_id,
                    question=dag.question[:200] if dag else "",
                    kpi_1_time_to_first_evidence_s=self._first_evidence_seconds,
                    kpi_2_distinct_domains_pre_synthesis=self._domains_before_synthesis,
                    kpi_3_provenance_binding_pct=self._provenance_binding_pct,
                    kpi_4_duration_s=result.duration_seconds,
                    kpi_4_tokens=result.metadata.tokens_consumed if result.metadata else 0,
                    kpi_4_typed_terminal=bool(result.error),
                    kpi_5_blockers=len(
                        getattr(quality_score, "blockers", []) or []
                    ) if quality_score else -1,
                    kpi_5_verdict_consistent=True,
                    # OVERHAUL2 S15 (OC-1): every COMPLETED task must carry an
                    # output object — the "completed but no output" status-writer
                    # bug (B-5) is structurally impossible when this is 100%.
                    kpi_6_pct_tasks_completed_with_output=_pct_completed_with_output,
                    # OVERHAUL2 S15 (OC-2): a run that reached synthesis produced
                    # a FinalReport (directly or via the floor fallback).
                    kpi_7_synthesis_produced_final_report=bool(
                        self._task_outputs.get("task_synthesis_lead")
                    ),
                    # OVERHAUL2 S15 (S11): off-topic funnel drops, visible in telemetry.
                    kpi_8_off_topic_dropped=self._off_topic_dropped_total,
                    # OVERHAUL3 D-F (§5.5): Recovery Supervisor telemetry.
                    kpi_9_recovery_attempted=self._recovery_telemetry["attempted"],
                    kpi_9_recovery_passes=self._recovery_telemetry["passes"],
                    kpi_9_recovered=self._recovery_telemetry["recovered"],
                )
                record_run_kpis(kpis)
                kdiff = diff_kpis(self._engagement_id)
                if kdiff.get("regressions"):
                    self._log(
                        f"KPI REGRESSION: {', '.join(kdiff['regressions'])} vs "
                        f"{kdiff.get('prev_id')} — owning phase node(s): "
                        f"{', '.join(regressed_phase(kdiff))}",
                    )
            except Exception as exc:  # noqa: BLE001 - KPI recording must never break the run
                logger.warning("KPI recording failed (non-fatal): %s", exc)

            # ─────────────────────────────────────────────────────────────
            # Save to Second Brain for future learning (§12.8)
            # ─────────────────────────────────────────────────────────────
            await self._save_to_second_brain(result)

            # ─────────────────────────────────────────────────────────────
            # End-of-run summary
            # ─────────────────────────────────────────────────────────────
            self._print_run_summary(result)

            return result

        except (TimeoutError, ValueError, RuntimeError, OSError) as e:
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
            # W-18: every success and failure result carries the cost accrued
            # before return; Python executes this mutation before returning it.
            # Router-free deterministic runs truthfully report zero cost.
            if self.router is not None:
                result.estimated_llm_cost_usd = self.router.get_engagement_cost_usd()
            # P10: Close journal
            if self._journal:
                self._journal.close()
            # P0: persist the Evidence Ledger snapshot on success AND failure
            # so the run autopsy is reproducible from telemetry alone.
            self._snapshot_evidence_ledger()
            # Phase 2: restore the sub-agent ceiling if AMBER reduced it.
            if getattr(self, "_evidence_reduced_budget", False):
                from hyperion.agents.base import BaseAgent

                BaseAgent.SUB_AGENT_TOTAL_CEILING = getattr(
                    self, "_evidence_budget_default", 6
                )
                self._evidence_reduced_budget = False

    async def _run_corpus_preflight(
        self, question: str, settings: Any | None = None
    ) -> Any:
        """Phase 2 (overhaul §6 P2): fire the canary battery, apply the verdict.

        RED raises ``CorpusPreflightError`` — the existing failure path records
        the typed ``INSUFFICIENT_EVIDENCE`` terminal state in seconds. AMBER
        halves the sequential sub-agent ceiling for this engagement (restored
        in ``run_engagement``'s ``finally``); GREEN does nothing. Separated
        into a method so the network-touching battery can be stubbed in tests
        that drive ``run_engagement`` for other concerns.
        """
        from hyperion.agents.support.corpus_preflight import (
            CorpusPreflightError,
            CorpusStatus,
            run_corpus_preflight,
        )

        try:
            contract = await run_corpus_preflight(
                question,
                settings=settings,
                run_id=self._engagement_id,
            )
        except CorpusPreflightError as exc:
            # P2: RED must notify the TUI (overhaul §6 P2) before the existing
            # failure path records the typed INSUFFICIENT_EVIDENCE terminal.
            try:
                await self.bus.publish(
                    channel=Channel.TUI,
                    msg_type=MessageType.STATUS,
                    sender="orchestrator",
                    payload={
                        "agent": "orchestrator",
                        "tool": "corpus_preflight",
                        "action": "RED",
                        "detail": str(exc)[:200],
                        "success": False,
                    },
                )
            except Exception:  # noqa: BLE001 - a TUI notify must not mask the terminal
                logger.warning("corpus preflight TUI notify failed: %s", exc)
            raise

        self._corpus_contract = contract
        self._evidence_reduced_budget = contract.status is CorpusStatus.AMBER
        if self._evidence_reduced_budget:
            from hyperion.agents.base import BaseAgent

            # AMBER: halve the sequential sub-agent ceiling for this
            # engagement; capture the default so the restore is exact even if
            # the class constant ever changes.
            default_ceiling = BaseAgent.SUB_AGENT_TOTAL_CEILING
            self._evidence_budget_default = default_ceiling
            BaseAgent.SUB_AGENT_TOTAL_CEILING = max(1, default_ceiling // 2)
            self._log(
                f"CORPUS PREFLIGHT AMBER — reduced sub-agent budget "
                f"({BaseAgent.SUB_AGENT_TOTAL_CEILING}/{default_ceiling} ceiling): "
                f"{contract.detail}"
            )
        if self._manifest:
            self._manifest.record_ledger_entry(
                "corpus_preflight", contract.to_dict()
            )
        self._log(
            f"CORPUS PREFLIGHT {contract.status.value.upper()}: {contract.detail}"
        )
        return contract

    async def _recheck_corpus_midrun(self, dag: Any) -> None:
        """P1.4/P5.1: mid-run corpus re-probe at a stage boundary.

        The preflight (P2) samples the corpus once at engagement start. If the
        fleet collapses mid-run (Aug-10 A-8: specialists "complete" with empty
        domain models while the corpus dies underneath them), downstream stages
        (synthesis, fact-check) must not run their full pipelines over an
        evidence vacuum. This reads the ledger at each boundary:

        * Corpus still at/above the contract → GREEN, no action.
        * Corpus collapsed since start → degrade to AMBER (halve the sub-agent
          ceiling for the remaining run) and log the typed state, so the stage
          can still produce a grounded-if-thin result rather than a fabricated
          one. Never raises — a re-probe failure must not abort the stage.
        """
        try:
            from hyperion.tools.evidence_ledger import get_evidence_ledger

            contract = getattr(self, "_corpus_contract", None)
            if contract is None:
                return
            # P6.2: sample the ledger for per-run KPI telemetry at this
            # boundary (fires at both synthesis and fact-check).
            self._record_kpi_telemetry()
            ledger = get_evidence_ledger()
            # OVERHAUL2 S7: measure ENGAGEMENT-retrieved evidence only.
            # Preflight canary records persist in the ledger; counting them
            # made this gate read the t=0 probe forever and never detect a
            # mid-run fleet collapse (B-7).
            domains = len({
                e.domain for e in ledger.all()
                if e.domain and e.stage != "preflight"
            })
            floor = int(getattr(contract, "min_domains", 8))
            if domains >= floor:
                return
            if self._evidence_reduced_budget:
                return
            # Corpus collapsed below the contract since the preflight.
            self._evidence_reduced_budget = True
            from hyperion.agents.base import BaseAgent

            default_ceiling = getattr(self, "_evidence_budget_default", None)
            if default_ceiling is None:
                default_ceiling = BaseAgent.SUB_AGENT_TOTAL_CEILING
                self._evidence_budget_default = default_ceiling
            BaseAgent.SUB_AGENT_TOTAL_CEILING = max(1, default_ceiling // 2)
            self._log(
                f"CORPUS MID-RUN RECHECK — corpus dropped to {domains}/{floor} "
                f"domains since preflight; degraded to AMBER (reduced sub-agent "
                f"budget) before the next stage boundary"
            )
        except Exception as exc:  # noqa: BLE001 - a re-probe must never abort synthesis
            logger.warning("corpus mid-run recheck failed (non-fatal): %s", exc)

    def _record_kpi_telemetry(self) -> None:
        """P6.2: sample the Evidence Ledger for per-run KPI telemetry.

        Called at every stage boundary (via ``_recheck_corpus_midrun``) and
        recorded to ``reports/diagnostics/kpis.json`` at run end. Fills
        ``_first_evidence_seconds`` (KPI-1), ``_domains_before_synthesis``
        (KPI-2) and ``_provenance_binding_pct`` (KPI-3). Never raises.
        """
        try:
            from hyperion.schemas.models import NON_SUBSTANTIVE_FINDING_TYPES
            from hyperion.tools.evidence_ledger import get_evidence_ledger

            ledger = get_evidence_ledger()
            first = ledger.first_fetched_at()
            if first is not None and self._start_time:
                elapsed = max(0.0, first - self._start_time)
                if self._first_evidence_seconds < 0 or elapsed < self._first_evidence_seconds:
                    self._first_evidence_seconds = elapsed
            domains = len(ledger.distinct_domains())
            if domains > self._domains_before_synthesis:
                self._domains_before_synthesis = domains
            # KPI-3: share of substantive findings carrying >= 1 bound source URL.
            findings = self._all_findings or []
            substantive = [
                f for f in findings
                if getattr(f, "finding_type", "") not in NON_SUBSTANTIVE_FINDING_TYPES
            ]
            if substantive:
                bound = sum(
                    1 for f in substantive
                    if any(getattr(s, "url", "") for s in getattr(f, "sources", []))
                )
                self._provenance_binding_pct = round(bound / len(substantive) * 100.0, 1)
        except Exception as exc:  # noqa: BLE001 - KPI sampling must never break a stage
            logger.debug("KPI telemetry sample failed (non-fatal): %s", exc)

    def _ledger_domains(self) -> int:
        """P4.4: distinct domains currently in the run-scoped Evidence Ledger."""
        try:
            from hyperion.tools.evidence_ledger import get_evidence_ledger

            return len(get_evidence_ledger().distinct_domains())
        except Exception:  # noqa: BLE001 - progress is best-effort, never fatal
            return self._last_domains_seen if self._last_domains_seen >= 0 else 0

    def _record_wave_progress(self, domains_before: int, max_zero: int = 2) -> bool:
        """P4.4: consume/produce the progress signal for one orchestration wave.

        Returns True when the loop may continue, False when the progress budget
        is exhausted (consecutive zero-delta waves) and the DAG should stop.
        """
        domains_after = self._ledger_domains()
        delta = max(0, domains_after - domains_before)
        self._last_domains_seen = domains_after
        if delta > 0:
            self._consecutive_zero_progress = 0
            return True
        self._consecutive_zero_progress += 1
        return self._consecutive_zero_progress < max_zero

    def _snapshot_evidence_ledger(self) -> None:
        """P0: persist the Evidence Ledger snapshot at run end.

        Runs on success AND failure so the Aug-10 autopsy is reproducible
        from telemetry alone (P0 exit gate). Never raises — a failed snapshot
        must not mask the run result it describes.

        D-06/§4 0.2: a refused engagement aborts with NO artifacts — the
        manifest is only opened after the refusal check, so its absence means
        the run never started and there is nothing to snapshot.
        """
        if getattr(self, "_manifest", None) is None:
            return
        try:
            from hyperion.infra.paths import project_file
            from hyperion.tools.evidence_ledger import get_evidence_ledger

            ledger = getattr(self, "_evidence_ledger", None) or get_evidence_ledger()
            path = project_file(
                "reports", "diagnostics", self._engagement_id, "evidence_ledger.json"
            )
            saved = ledger.snapshot(path)
            self._manifest.record_ledger_entry("evidence", ledger.summary())
            trace(
                "evidence",
                run_id=self._engagement_id,
                event="ledger_snapshot",
                items=ledger.count(),
                domains=len(ledger.distinct_domains()),
                path=saved,
            )
        except Exception as exc:  # noqa: BLE001 - telemetry must not break the run
            logger.warning("evidence ledger snapshot failed: %s", exc)

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
            print("  Quality Score:   N/A")

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
            except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                logger.debug("token summary unavailable: %s: %s", type(exc).__name__, exc)

        total_tokens = token_summary.get("total_tokens", 0)
        total_calls = token_summary.get("total_calls", 0)
        print(f"\n  Total Tokens:    {total_tokens:,}")
        print(f"  Total LLM Calls: {total_calls:,}")
        print(f"  Est. LLM Cost:   ${result.estimated_llm_cost_usd:.6f}")

        by_provider = token_summary.get("by_provider", {})
        if by_provider:
            print("\n  Token Breakdown by Provider:")
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
                except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                    # Teardown must not abort the remaining closes, but a
                    # leaking tool client should be diagnosable.
                    logger.debug(
                        "agent %s close() failed during shutdown: %s: %s",
                        getattr(agent, "name", "?"), type(exc).__name__, exc,
                    )
            else:
                cleanup_method = getattr(agent, "cleanup", None)
                if callable(cleanup_method):
                    try:
                        result = cleanup_method()
                        if asyncio.iscoroutine(result):
                            await result
                    except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                        logger.warning("%s: %s", "close", exc)

    async def __aenter__(self) -> WorkflowEngine:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


# ─────────────────────────────────────────────────────────────────────────────
# Convenience function — run a single engagement
# ─────────────────────────────────────────────────────────────────────────────


def zero_evidence_failure(ym: dict[str, Any]) -> str | None:
    """D-12: return an error message when the yield report proves zero evidence.

    Zero search calls AND zero chars retained means the research stack was
    non-functional for the entire engagement — the 07-30 state that previously
    formatted as a tidy "0/0 URLs (0%)" and let the run report SUCCESS over a
    fabricated report. Extracted as a module-level function so the gate is
    testable without stubbing the whole pipeline.
    """
    if ym.get("search_calls", 0) == 0 and ym.get("chars_retained", 0) == 0:
        return (
            "Zero evidence retrieved: the engagement made no successful "
            "search calls and retained 0 chars. The research stack is "
            "non-functional — the report is ungrounded. See D-04/D-12."
        )
    return None


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
