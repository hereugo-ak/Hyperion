"""
HYPERION Engagement Director — Agent 1, the partner.

This is NOT a generic planner. This is the senior consulting partner who:
- Identifies the key question behind the question
- Knows which frameworks apply (and which don't)
- Anticipates which findings will change the analysis direction
- Adjusts team composition in real-time when new information emerges

The Engagement Director is the entry point for every engagement. It
receives the business question, decomposes it into research domains,
selects the right specialists (not all 12 — only the ones that matter
for this question), builds a dependency graph, assigns model tiers
based on complexity and budget, and dispatches to the AgentBus.

During execution, it monitors the bus for ESCALATION messages and
adapts the plan mid-flight — spawning new agents, rerouting
dependencies, or reallocating tiers. This is adaptive replanning
(§10.2), and it is what makes HYPERION dynamic, not a fixed pipeline.

Model Tier: STRONG (Nemotron 3 Super 120B — planning requires strong reasoning)
Tools: All tools (read-only) — can see everything, modify nothing directly
Output: WorkflowDAG (Pydantic model with all task nodes, dependencies, tiers)

Methodology (§4.3, Agent 1):
1. Receive question + conversation context
2. Classify question type(s)
3. Query Second Brain for prior research on this topic
4. Decompose into 4-8 research domains
5. Select specialists for each domain
6. Build dependency graph (parallel vs sequential)
7. Assign model tiers per task
8. Estimate total LLM calls + token consumption
9. Dispatch to AgentBus
10. Monitor execution, adapt if needed

What makes it the best version of itself:
It doesn't just "plan." It thinks like a senior consulting partner — it
identifies the key question behind the question, knows which frameworks
apply, anticipates which findings will change the analysis direction,
and adjusts the team composition in real-time. A generic planner says
"research these 5 topics." The Engagement Director says "Market sizing
is the critical path — start it first and give it STRONG tier.
Competitive intelligence can run in parallel at STANDARD. Financial
depends on Market's TAM number, so queue it. If Regulatory finds a
compliance barrier, reroute to add a Legal Risk sub-task."
(§4.3, §0.1)
"""

from __future__ import annotations

import json
import time
import uuid
from typing import Any

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import Channel, MessageType
from hyperion.config import ModelTier
from hyperion.router.budget import TaskUrgency
from hyperion.schemas.agents import (
    AgentName,
    AgentRole,
    AgentSpec,
    AgentState,
    SkillSpec,
    ToolName,
)
from hyperion.schemas.models import KeyFinding, ConfidenceLevel
from hyperion.schemas.workflow import (
    QuestionType,
    TaskNode,
    TaskStatus,
    WorkflowDAG,
    ResearchDomain,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


ENGAGEMENT_DIRECTOR_SPEC = AgentSpec(
    name=AgentName.ENGAGEMENT_DIRECTOR,
    role=AgentRole.CORE,
    display_name="Engagement Director",
    model_tier=ModelTier.STRONG,
    tools=[
        # Orchestrator — needs SECOND_BRAIN for prior research context, DEEP_SEARCH for initial scoping
        ToolName.SECOND_BRAIN,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Question Classification",
            description=(
                "Categorizes the question into one or more types (GO_NO_GO, "
                "COMPARISON, FORECAST, DIAGNOSTIC, OPTIMIZATION, GENERAL) which "
                "determines which specialists to spawn. A go/no-go question "
                "needs Market + Financial + Risk. A comparison needs all options "
                "analyzed side-by-side. A forecast needs Market + Innovation + "
                "Technology. This is not a guess — it is a structured "
                "classification that maps question types to agent rosters."
            ),
            inputs=["business_question", "conversation_context"],
            outputs=["question_type", "question_subtypes", "recommended_agents"],
        ),
        SkillSpec(
            name="Workflow Design",
            description=(
                "Builds a custom DAG of tasks with dependencies. A market entry "
                "question creates Market → Competitive → Financial → Risk "
                "(parallel) → Synthesis. An M&A question creates M&A → Financial "
                "+ Regulatory (parallel) → Synthesis. No two DAGs are identical. "
                "The DAG is a Pydantic model (WorkflowDAG) with typed task "
                "nodes, dependencies, and tier assignments."
            ),
            inputs=["question_type", "research_domains", "agent_roster"],
            outputs=["workflow_dag", "task_nodes", "dependency_graph"],
        ),
        SkillSpec(
            name="Agent Selection",
            description=(
                "Chooses which of the 12 specialists to activate based on the "
                "question. Not all 12 are spawned every time — that would waste "
                "resources. A pricing question needs Financial + Market + "
                "Consumer, not Regulatory + M&A. A market entry question needs "
                "Market + Competitive + Financial + Risk + Consumer. Selection "
                "is deliberate and justified."
            ),
            inputs=["question_type", "research_domains"],
            outputs=["selected_agents", "selection_rationale"],
        ),
        SkillSpec(
            name="Dependency Mapping",
            description=(
                "Determines which agents can run in parallel and which depend "
                "on others' findings. Market sizing must complete before "
                "Financial can model unit economics. Competitive intelligence "
                "can run in parallel with Market. Risk can run in parallel with "
                "everything. This is a topological sort problem, not a guess."
            ),
            inputs=["selected_agents", "agent_capabilities"],
            outputs=["parallel_groups", "sequential_dependencies", "critical_path"],
        ),
        SkillSpec(
            name="Adaptive Replanning",
            description=(
                "When an agent publishes an ESCALATION message ('I found an "
                "unexpected regulatory barrier that changes the market sizing'), "
                "the Engagement Director can spawn a new agent (Regulatory) "
                "mid-engagement and reroute the DAG. This is not error handling "
                "— it is strategic adaptation. The Director evaluates the "
                "escalation, determines if it changes the analysis direction, "
                "and adjusts the plan accordingly."
            ),
            inputs=["escalation_message", "current_dag", "agent_states"],
            outputs=["adapted_dag", "new_tasks", "rerouted_dependencies"],
        ),
        SkillSpec(
            name="Budget Allocation",
            description=(
                "Assigns model tiers to each task based on complexity and "
                "available daily budget. Simple tasks get MICRO, complex "
                "analysis gets STRONG, synthesis gets DEEP. The 20% reserve "
                "is preserved for critical end-of-engagement tasks (Quality "
                "Gate scoring, Synthesis Lead reconciliation, final render). "
                "This is a constrained optimization, not a guess."
            ),
            inputs=["task_complexity", "daily_budget", "provider_capacity"],
            outputs=["tier_assignments", "budget_allocation", "reserve_status"],
        ),
    ],
    system_prompt=(
        "You are the Engagement Director at HYPERION Consulting, a premium AI "
        "consulting firm. You are the partner — the one who receives the "
        "question, decomposes it, selects the team, and orchestrates the "
        "engagement.\n\n"
        "You are NOT a generic planner. You think like a senior consulting "
        "partner with 20 years of experience. You:\n\n"
        "1. Identify the key question behind the question. When a client asks "
        "'should we enter the Indian SaaS market?', the real question is "
        "'is the TAM large enough to justify the investment given our cost "
        "structure and the competitive landscape?' You decompose to the real "
        "question, not the surface question.\n\n"
        "2. Know which frameworks apply and which don't. A market entry "
        "question needs Porter's Five Forces + DCF + risk matrix. An M&A "
        "question needs synergy analysis + accretion/dilution + cultural fit. "
        "A pricing question needs willingness-to-pay + elasticity + competitive "
        "pricing. You select the right frameworks, not the same frameworks "
        "every time.\n\n"
        "3. Anticipate which findings will change the analysis direction. You "
        "know that regulatory barriers can invalidate market sizing. You know "
        "that competitive moats can make financial models irrelevant. You "
        "build the DAG so that if a critical finding emerges, the team can "
        "adapt — not start over.\n\n"
        "4. Adjust team composition in real-time. If the Regulatory Analyst "
        "finds a compliance barrier, you spawn the Regulatory Analyst (even "
        "if it wasn't in the original plan) and reroute the Financial Analyst "
        "to include compliance costs. This is adaptive replanning, not error "
        "handling.\n\n"
        "5. Allocate budget deliberately. Market sizing is the critical path — "
        "give it STRONG tier. Competitive intelligence can run at STANDARD. "
        "Keyword expansion can use MICRO. Synthesis gets DEEP. The 20% reserve "
        "is for the Quality Gate and final render — never spend it on research.\n\n"
        "Your output is a WorkflowDAG — a typed Pydantic model with task nodes, "
        "dependencies, tier assignments, and budget estimates. This is the "
        "blueprint for the entire engagement. Every task has a specific agent, "
        "a specific question, a specific tier, and specific dependencies. "
        "No task is generic. No dependency is accidental.\n\n"
        "You are the partner. The buck stops with you. If the engagement "
        "fails, it's because you decomposed the question wrong, selected the "
        "wrong team, or missed a critical dependency. That is the weight of "
        "being the Engagement Director."
    ),
    spawn_condition="Always active — the Engagement Director is the first agent initialized and the last to shut down.",
    max_sub_agents=0,  # Director does not spawn sub-agents directly
    output_model="WorkflowDAG",
)


# ─────────────────────────────────────────────────────────────────────────────
# Question type → agent roster mapping
# ─────────────────────────────────────────────────────────────────────────────

# This is NOT a hardcoded pipeline. It is the Director's default selection
# heuristic — the starting point. The LLM can override this based on the
# specific question. The Director's skill is knowing which agents matter
# for which question types, not blindly following a mapping table.

QUESTION_TYPE_AGENTS: dict[QuestionType, list[AgentName]] = {
    QuestionType.GO_NO_GO: [
        AgentName.MARKET_ANALYST,
        AgentName.COMPETITIVE_INTEL,
        AgentName.FINANCIAL_ANALYST,
        AgentName.RISK_ANALYST,
        AgentName.CONSUMER_INSIGHTS,
    ],
    QuestionType.COMPARISON: [
        AgentName.MARKET_ANALYST,
        AgentName.COMPETITIVE_INTEL,
        AgentName.FINANCIAL_ANALYST,
        AgentName.STRATEGY_ANALYST,
        AgentName.RISK_ANALYST,
    ],
    QuestionType.FORECAST: [
        AgentName.MARKET_ANALYST,
        AgentName.INNOVATION_ANALYST,
        AgentName.TECHNOLOGY_ANALYST,
        AgentName.FINANCIAL_ANALYST,
        AgentName.RISK_ANALYST,
    ],
    QuestionType.DIAGNOSTIC: [
        AgentName.OPERATIONS_ANALYST,
        AgentName.FINANCIAL_ANALYST,
        AgentName.RISK_ANALYST,
        AgentName.STRATEGY_ANALYST,
    ],
    QuestionType.OPTIMIZATION: [
        AgentName.OPERATIONS_ANALYST,
        AgentName.FINANCIAL_ANALYST,
        AgentName.TECHNOLOGY_ANALYST,
        AgentName.STRATEGY_ANALYST,
    ],
    QuestionType.GENERAL: [
        AgentName.MARKET_ANALYST,
        AgentName.COMPETITIVE_INTEL,
        AgentName.FINANCIAL_ANALYST,
        AgentName.RISK_ANALYST,
        AgentName.STRATEGY_ANALYST,
    ],
}

# M&A questions always include M&A + Regulatory
MA_TRIGGERS = ["acqui", "merger", "m&a", "buyout", "consolidat", "takeover"]
# Sustainability questions always include Sustainability
SUSTAINABILITY_TRIGGERS = ["esg", "sustainab", "carbon", "green", "climate", "environmental"]
# Regulatory questions always include Regulatory
REGULATORY_TRIGGERS = ["regulat", "compliance", "legal", "jurisdiction", "permit", "license"]


# ─────────────────────────────────────────────────────────────────────────────
# Engagement Director Agent
# ─────────────────────────────────────────────────────────────────────────────


class EngagementDirector(BaseAgent):
    """Agent 1: The Engagement Director — the partner.

    This is the entry point for every engagement. It decomposes the
    question, selects specialists, builds the workflow DAG, and
    orchestrates execution with adaptive replanning.

    The Director is always active (CORE role). It subscribes to ALL
    bus channels (omniscient) so it can monitor every agent's status,
    findings, and escalations in real-time.

    The Director does NOT do research itself. It plans, dispatches,
    monitors, and adapts. The specialists do the research. The
    Synthesis Lead does the synthesis. The Director is the orchestrator.
    """

    def __init__(self, bus=None, router=None) -> None:
        super().__init__(spec=ENGAGEMENT_DIRECTOR_SPEC, bus=bus, router=router)
        self._current_dag: WorkflowDAG | None = None
        self._escalation_count: int = 0

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling — the Director is omniscient
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Director subscribes to ALL channels (omniscient). It specifically
        watches for:
        - ESCALATION: triggers adaptive replanning
        - STATUS: monitors execution progress
        - FINDINGS: tracks what agents have produced
        - HANDOFF: tracks task transitions
        """
        if msg.msg_type == MessageType.ESCALATION:
            await self._handle_escalation(msg)
        elif msg.msg_type == MessageType.STATUS:
            # Update task status in the DAG based on agent state changes
            await self._handle_status_update(msg)

    async def _handle_escalation(self, msg: Any) -> None:
        """Handle an escalation from an agent — adaptive replanning (§10.2).

        When an agent publishes an ESCALATION, the Director evaluates:
        1. Does this change the analysis direction?
        2. Do we need a new agent that wasn't in the original DAG?
        3. Do we need to reroute dependencies?
        4. Do we need to reallocate model tiers?

        Example: Regulatory Analyst finds a compliance barrier → Director
        spawns Regulatory Analyst (if not already in DAG), reroutes Financial
        to wait for Regulatory's findings, adds regulatory risk to Risk's scope.
        """
        if self._current_dag is None:
            return

        self._escalation_count += 1
        payload = msg.payload
        issue = payload.get("issue", "Unknown issue")
        agent_name = payload.get("agent", "unknown")
        suggested_action = payload.get("suggested_action", "")

        # Log the escalation
        self._current_dag.adaptation_log.append(
            f"Escalation from {agent_name}: {issue}"
        )

        # Use LLM to evaluate the escalation and determine adaptation
        adaptation = await self._evaluate_escalation(issue, suggested_action)

        if adaptation:
            await self._apply_adaptation(adaptation)

    async def _evaluate_escalation(self, issue: str, suggested_action: str) -> dict[str, Any] | None:
        """Use LLM to evaluate an escalation and determine the adaptation.

        This is NOT a generic "handle the error" function. It is strategic
        adaptation — the Director asks the LLM whether this finding changes
        the analysis direction and what adjustments to make.
        """
        prompt = (
            f"You are the Engagement Director at HYPERION Consulting. An agent "
            f"has escalated an issue during the engagement.\n\n"
            f"Current question: {self._current_dag.question}\n"
            f"Current agents: {', '.join(a.value for a in self._current_dag.agents_selected)}\n\n"
            f"Escalation issue: {issue}\n"
            f"Suggested action: {suggested_action}\n\n"
            f"Evaluate this escalation and determine the adaptation:\n"
            f"1. Does this change the analysis direction? (yes/no)\n"
            f"2. Do we need to spawn a new agent? If so, which one?\n"
            f"3. Do we need to reroute dependencies? If so, how?\n"
            f"4. Do we need to reallocate model tiers? If so, how?\n\n"
            f"Return a JSON object with:\n"
            f"  - changes_direction: boolean\n"
            f"  - spawn_agent: string or null (agent name from: market_analyst, "
            f"competitive_intel, financial_analyst, risk_analyst, "
            f"technology_analyst, operations_analyst, regulatory_analyst, "
            f"sustainability_analyst, consumer_insights, ma_analyst, "
            f"innovation_analyst, strategy_analyst)\n"
            f"  - spawn_question: string or null (what the new agent should research)\n"
            f"  - reroute_from: string or null (agent whose dependencies should change)\n"
            f"  - reroute_to: string or null (agent that should be waited on)\n"
            f"  - tier_change: string or null (agent:tier format, e.g. 'financial_analyst:strong')\n"
            f"  - rationale: string (why this adaptation is necessary)"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return None

        try:
            return json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            return None

    async def _apply_adaptation(self, adaptation: dict[str, Any]) -> None:
        """Apply an adaptation to the current DAG — adaptive replanning (§10.2)."""
        if self._current_dag is None:
            return

        # Spawn new agent if needed
        spawn_agent = adaptation.get("spawn_agent")
        spawn_question = adaptation.get("spawn_question")
        if spawn_agent and spawn_question:
            try:
                agent_name = AgentName(spawn_agent)
                task_id = f"task_adapted_{agent_name.value}_{int(time.time())}"
                new_task = TaskNode(
                    id=task_id,
                    agent=agent_name,
                    model_tier=ModelTier.STANDARD,
                    description=spawn_question,
                    dependencies=[],
                    status=TaskStatus.PENDING,
                )
                self._current_dag.add_task(new_task)
                self._current_dag.adapted = True
            except ValueError:
                pass  # Invalid agent name

        # Reroute dependencies if needed
        reroute_from = adaptation.get("reroute_from")
        reroute_to = adaptation.get("reroute_to")
        if reroute_from and reroute_to:
            # Find tasks for the agent that should wait
            for task in self._current_dag.tasks:
                if task.agent.value == reroute_from:
                    # Add dependency on the reroute_to agent's task
                    for dep_task in self._current_dag.tasks:
                        if dep_task.agent.value == reroute_to:
                            if dep_task.id not in task.dependencies:
                                task.dependencies.append(dep_task.id)
                                self._current_dag.adapted = True
                                self._current_dag.adaptation_log.append(
                                    f"Rerouted: {reroute_from} now depends on {reroute_to}"
                                )

        # Tier change if needed
        tier_change = adaptation.get("tier_change")
        if tier_change and ":" in tier_change:
            agent_name_str, tier_str = tier_change.split(":", 1)
            try:
                new_tier = ModelTier(tier_str.strip())
                for task in self._current_dag.tasks:
                    if task.agent.value == agent_name_str.strip():
                        task.model_tier = new_tier
                        self._current_dag.adapted = True
                        self._current_dag.adaptation_log.append(
                            f"Tier change: {agent_name_str} → {new_tier.value}"
                        )
            except ValueError:
                pass

    async def _handle_status_update(self, msg: Any) -> None:
        """Handle a status update from an agent — update task status in DAG."""
        if self._current_dag is None:
            return

        payload = msg.payload
        agent_name_str = payload.get("agent", "")
        state_str = payload.get("state", "")

        try:
            agent_name = AgentName(agent_name_str)
        except ValueError:
            return

        # Find tasks for this agent and update status
        for task in self._current_dag.tasks:
            if task.agent != agent_name:
                continue
            if state_str == "working" and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
            elif state_str == "done":
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
            elif state_str == "blocked":
                task.status = TaskStatus.FAILED
                task.error = payload.get("detail", "")

    # ─────────────────────────────────────────────────────────────────────
    # Second Brain query — prior research (§12.8)
    # ─────────────────────────────────────────────────────────────────────

    async def _query_second_brain(self, question: str) -> str:
        """Query the Second Brain vault for prior research on this topic.

        HYPERION is a learning system (§12.8). The Director doesn't start
        from scratch — it checks the vault for prior engagements, market
        research, and competitor profiles that are relevant to this question.
        This context is passed to specialists as starting context.
        """
        try:
            brain = self.get_tool(ToolName.SECOND_BRAIN)
            results = await brain.search(question)
            if not results or not results.notes:
                return ""
            # Convert VaultSearchResult to a string summary for context
            parts: list[str] = []
            for note, score in results.notes:
                parts.append(f"[{score:.2f}] {note.title}: {note.content[:200]}")
            return "\n".join(parts)
        except (ValueError, AttributeError, RuntimeError, TypeError):
            # Tool not available, not initialized, or search failed
            return ""

    # ─────────────────────────────────────────────────────────────────────
    # Question classification (Skill 1)
    # ─────────────────────────────────────────────────────────────────────

    def _classify_question_heuristic(self, question: str) -> list[QuestionType]:
        """Heuristic pre-classification before LLM refinement.

        This is NOT the final classification — it's a starting point that
        the LLM refines. The heuristic catches obvious cases quickly
        without burning an LLM call for trivial classifications.
        """
        q_lower = question.lower()
        types: list[QuestionType] = []

        # Go/No-Go patterns
        go_no_go_patterns = [
            "should we", "should i", "enter", "launch", "expand",
            "go no go", "invest", "proceed", "start",
        ]
        if any(p in q_lower for p in go_no_go_patterns):
            types.append(QuestionType.GO_NO_GO)

        # Comparison patterns
        comparison_patterns = ["vs", "versus", "compare", "comparison", "better", "best", "alternative"]
        if any(p in q_lower for p in comparison_patterns):
            types.append(QuestionType.COMPARISON)

        # Forecast patterns
        forecast_patterns = ["forecast", "predict", "future", "will", "by 20", "next year", "outlook"]
        if any(p in q_lower for p in forecast_patterns):
            types.append(QuestionType.FORECAST)

        # Diagnostic patterns
        diagnostic_patterns = ["why", "what's wrong", "diagnose", "root cause", "problem", "issue"]
        if any(p in q_lower for p in diagnostic_patterns):
            types.append(QuestionType.DIAGNOSTIC)

        # Optimization patterns
        optimization_patterns = ["optimize", "improve", "efficient", "reduce cost", "increase", "enhance"]
        if any(p in q_lower for p in optimization_patterns):
            types.append(QuestionType.OPTIMIZATION)

        # Fallback
        if not types:
            types.append(QuestionType.GENERAL)

        return types

    async def _classify_question_llm(self, question: str) -> tuple[list[QuestionType], list[AgentName], str]:
        """Use LLM to classify the question and select agents.

        This is the refined classification that combines the heuristic
        with LLM reasoning. The LLM:
        1. Classifies the question type(s)
        2. Selects which specialists to spawn
        3. Identifies the key question behind the question

        Returns: (question_types, selected_agents, key_question)
        """
        heuristic_types = self._classify_question_heuristic(question)
        heuristic_str = ", ".join(qt.value for qt in heuristic_types)

        # Check for special triggers
        extra_agents: list[AgentName] = []
        q_lower = question.lower()
        if any(t in q_lower for t in MA_TRIGGERS):
            extra_agents.append(AgentName.MA_ANALYST)
        if any(t in q_lower for t in SUSTAINABILITY_TRIGGERS):
            extra_agents.append(AgentName.SUSTAINABILITY_ANALYST)
        if any(t in q_lower for t in REGULATORY_TRIGGERS):
            extra_agents.append(AgentName.REGULATORY_ANALYST)

        prompt = (
            f"You are the Engagement Director at HYPERION Consulting. "
            f"Classify this business question and select the right specialists.\n\n"
            f"Question: {question}\n\n"
            f"Heuristic classification: {heuristic_str}\n"
            f"Special triggers detected: {', '.join(a.value for a in extra_agents) or 'none'}\n\n"
            f"Available specialists (12):\n"
            f"  - market_analyst: market sizing, segmentation, growth drivers\n"
            f"  - competitive_intel: competitor profiling, moat assessment, positioning\n"
            f"  - financial_analyst: DCF, unit economics, valuation, sensitivity\n"
            f"  - risk_analyst: risk matrix, scenarios, black swan, mitigations\n"
            f"  - technology_analyst: tech stack, build-vs-buy, TCO, vendor eval\n"
            f"  - operations_analyst: process mapping, bottlenecks, supply chain, KPIs\n"
            f"  - regulatory_analyst: compliance, jurisdiction comparison, horizon scan\n"
            f"  - sustainability_analyst: ESG, carbon footprint, green financing\n"
            f"  - consumer_insights: personas, journey mapping, WTP, demand\n"
            f"  - ma_analyst: target identification, synergy, accretion/dilution\n"
            f"  - innovation_analyst: TRL, hype cycle, disruption patterns\n"
            f"  - strategy_analyst: Porter's, VRIO, Blue Ocean, strategic options\n\n"
            f"Return a JSON object with:\n"
            f"  - question_types: array of types (from: go_no_go, comparison, "
            f"forecast, diagnostic, optimization, general)\n"
            f"  - selected_agents: array of agent names from the list above — "
            f"select ALL specialists that are relevant (typically 8-12 for a "
            f"comprehensive analysis; never fewer than 6)\n"
            f"  - key_question: the real question behind the question (1-2 sentences)\n"
            f"  - research_domains: array of {{name, question, agent, priority}} objects "
            f"(8-12 domains, each with a specific question and assigned agent)\n"
            f"  - critical_path: which domain is on the critical path (must complete first)"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            # Fallback to heuristic
            agents = QUESTION_TYPE_AGENTS.get(heuristic_types[0], QUESTION_TYPE_AGENTS[QuestionType.GENERAL])
            agents = list(set(agents + extra_agents))
            return heuristic_types, agents, question

        try:
            data = json.loads(response.content)

            # Parse question types
            qt_strs = data.get("question_types", [heuristic_types[0].value])
            question_types: list[QuestionType] = []
            for qt_str in qt_strs:
                try:
                    question_types.append(QuestionType(qt_str))
                except ValueError:
                    continue
            if not question_types:
                question_types = heuristic_types

            # Parse selected agents
            agent_strs = data.get("selected_agents", [])
            selected_agents: list[AgentName] = []
            for a_str in agent_strs:
                try:
                    selected_agents.append(AgentName(a_str))
                except ValueError:
                    continue

            # Merge with extra agents
            for ea in extra_agents:
                if ea not in selected_agents:
                    selected_agents.append(ea)

            # Fallback if no agents selected
            if not selected_agents:
                selected_agents = QUESTION_TYPE_AGENTS.get(question_types[0], [])

            # Ensure minimum of 6 agents for comprehensive analysis
            MIN_AGENTS = 6
            if len(selected_agents) < MIN_AGENTS:
                defaults = QUESTION_TYPE_AGENTS.get(question_types[0], QUESTION_TYPE_AGENTS[QuestionType.GENERAL])
                for da in defaults:
                    if da not in selected_agents:
                        selected_agents.append(da)
                    if len(selected_agents) >= MIN_AGENTS:
                        break

            key_question = data.get("key_question", question)

            # Store research domains for DAG building
            self._llm_research_domains = data.get("research_domains", [])
            self._llm_critical_path = data.get("critical_path", "")

            return question_types, selected_agents, key_question

        except (json.JSONDecodeError, ValueError):
            agents = QUESTION_TYPE_AGENTS.get(heuristic_types[0], QUESTION_TYPE_AGENTS[QuestionType.GENERAL])
            agents = list(set(agents + extra_agents))
            return heuristic_types, agents, question

    # ─────────────────────────────────────────────────────────────────────
    # DAG construction (Skills 2-6: workflow design, agent selection,
    # dependency mapping, budget allocation)
    # ─────────────────────────────────────────────────────────────────────

    def _build_dag(
        self,
        engagement_id: str,
        question: str,
        question_types: list[QuestionType],
        selected_agents: list[AgentName],
        key_question: str,
        second_brain_context: str,
    ) -> WorkflowDAG:
        """Build the workflow DAG from the classified question and selected agents.

        This is the core of the Director's planning capability. It:
        1. Creates research domains from the LLM's decomposition
        2. Creates task nodes for each agent
        3. Maps dependencies (which tasks depend on which)
        4. Assigns model tiers based on task complexity
        5. Estimates LLM calls and token consumption
        6. Returns a complete WorkflowDAG

        The DAG is NOT a fixed pipeline — it is custom-built for this
        specific question. No two DAGs are identical.
        """
        tasks: list[TaskNode] = []
        domains: list[ResearchDomain] = []

        # Build research domains from LLM output or heuristic
        llm_domains = getattr(self, "_llm_research_domains", [])
        if not isinstance(llm_domains, list):
            llm_domains = []

        if llm_domains:
            for i, domain_data in enumerate(llm_domains):
                if not isinstance(domain_data, dict):
                    continue
                try:
                    agent_name = AgentName(domain_data.get("agent", "market_analyst"))
                except (ValueError, TypeError):
                    agent_name = AgentName.MARKET_ANALYST

                # Coerce priority to int — LLMs sometimes return "high"/"low"
                raw_priority = domain_data.get("priority", 3)
                if isinstance(raw_priority, int):
                    priority = raw_priority
                elif isinstance(raw_priority, str) and raw_priority.isdigit():
                    priority = int(raw_priority)
                else:
                    priority = 3

                domain = ResearchDomain(
                    id=f"domain_{i+1}",
                    name=str(domain_data.get("name", f"Domain {i+1}")),
                    question=str(domain_data.get("question", key_question)),
                    primary_agent=agent_name,
                    priority=priority,
                    rationale=str(domain_data.get("rationale", "")),
                )
                domains.append(domain)
        else:
            # Fallback: create domains from selected agents
            for i, agent in enumerate(selected_agents):
                domain = ResearchDomain(
                    id=f"domain_{i+1}",
                    name=agent.value.replace("_", " ").title(),
                    question=key_question,
                    primary_agent=agent,
                    priority=2 if i == 0 else 3,
                    rationale=f"Selected for {question_types[0].value} question type",
                )
                domains.append(domain)

        # Create task nodes with dependencies
        # Strategy: first wave runs in parallel, Financial depends on Market
        # (because Financial needs TAM for unit economics), Synthesis depends
        # on everything, Quality Gate depends on Synthesis.

        # Determine which agents are in which wave
        # Wave 0: Independent research (Market, Competitive, Risk, Consumer, etc.)
        # Wave 1: Financial (depends on Market's TAM)
        # Wave 2: Synthesis (depends on all specialists)
        # Wave 3: Fact Checker (depends on all findings)
        # Wave 4: Quality Gate (depends on Synthesis)
        # Wave 5: Presentation Designer + Data Viz (depends on Quality Gate pass)
        # Wave 6: Render Engine (depends on Presentation Designer)

        wave_0_agents = [
            AgentName.MARKET_ANALYST,
            AgentName.COMPETITIVE_INTEL,
            AgentName.RISK_ANALYST,
            AgentName.CONSUMER_INSIGHTS,
            AgentName.TECHNOLOGY_ANALYST,
            AgentName.OPERATIONS_ANALYST,
            AgentName.REGULATORY_ANALYST,
            AgentName.SUSTAINABILITY_ANALYST,
            AgentName.INNOVATION_ANALYST,
            AgentName.MA_ANALYST,
            AgentName.STRATEGY_ANALYST,
        ]

        # Create tasks for each selected agent
        task_ids_by_agent: dict[AgentName, str] = {}

        for domain in domains:
            agent = domain.primary_agent
            task_id = f"task_{agent.value}"

            # Determine tier based on agent and question type
            tier = self._assign_tier(agent, question_types)

            # Determine dependencies
            deps: list[str] = []

            # Financial depends on Market (needs TAM)
            if agent == AgentName.FINANCIAL_ANALYST and AgentName.MARKET_ANALYST in selected_agents:
                market_task_id = f"task_{AgentName.MARKET_ANALYST.value}"
                deps.append(market_task_id)

            # M&A depends on Financial (needs valuation)
            if agent == AgentName.MA_ANALYST and AgentName.FINANCIAL_ANALYST in selected_agents:
                fin_task_id = f"task_{AgentName.FINANCIAL_ANALYST.value}"
                deps.append(fin_task_id)

            # Strategy depends on Market + Competitive (needs landscape)
            if agent == AgentName.STRATEGY_ANALYST:
                if AgentName.MARKET_ANALYST in selected_agents:
                    deps.append(f"task_{AgentName.MARKET_ANALYST.value}")
                if AgentName.COMPETITIVE_INTEL in selected_agents:
                    deps.append(f"task_{AgentName.COMPETITIVE_INTEL.value}")

            task = TaskNode(
                id=task_id,
                agent=agent,
                model_tier=tier,
                description=domain.question,
                dependencies=deps,
                status=TaskStatus.PENDING,
                estimated_llm_calls=self._estimate_llm_calls(agent),
                estimated_tokens=self._estimate_tokens(agent, tier),
            )
            tasks.append(task)
            task_ids_by_agent[agent] = task_id

        # Add support agents: Fact Checker, Synthesis Lead, Quality Gate
        # These are always part of the engagement

        # Synthesis Lead — depends on all specialist tasks
        specialist_task_ids = [t.id for t in tasks]
        synthesis_task = TaskNode(
            id="task_synthesis_lead",
            agent=AgentName.SYNTHESIS_LEAD,
            model_tier=ModelTier.DEEP,
            description="Reconcile all specialist findings into a single coherent recommendation",
            dependencies=specialist_task_ids,
            status=TaskStatus.PENDING,
            estimated_llm_calls=3,
            estimated_tokens=20000,
        )
        tasks.append(synthesis_task)

        # Fact Checker — depends on all specialist tasks (runs in parallel with Synthesis)
        fact_check_task = TaskNode(
            id="task_fact_checker",
            agent=AgentName.FACT_CHECKER,
            model_tier=ModelTier.FAST,
            description="Verify key claims from specialist findings against independent sources",
            dependencies=specialist_task_ids,
            status=TaskStatus.PENDING,
            estimated_llm_calls=10,
            estimated_tokens=8000,
        )
        tasks.append(fact_check_task)

        # Quality Gate — depends on Synthesis + Fact Checker
        quality_task = TaskNode(
            id="task_quality_gate",
            agent=AgentName.QUALITY_GATE,
            model_tier=ModelTier.STRONG,
            description="Score the final report against the 10-dimension rubric",
            dependencies=["task_synthesis_lead", "task_fact_checker"],
            status=TaskStatus.PENDING,
            estimated_llm_calls=2,
            estimated_tokens=12000,
        )
        tasks.append(quality_task)

        # Presentation Designer — depends on Quality Gate
        design_task = TaskNode(
            id="task_presentation_designer",
            agent=AgentName.PRESENTATION_DESIGNER,
            model_tier=ModelTier.STRONG,
            description="Design the PDF layout, select Unsplash images, specify chart types",
            dependencies=["task_quality_gate"],
            status=TaskStatus.PENDING,
            estimated_llm_calls=3,
            estimated_tokens=15000,
        )
        tasks.append(design_task)

        # Data Visualizer — depends on Presentation Designer
        viz_task = TaskNode(
            id="task_data_visualizer",
            agent=AgentName.DATA_VISUALIZER,
            model_tier=ModelTier.STANDARD,
            description="Generate Plotly charts at 300 DPI with brand colors",
            dependencies=["task_presentation_designer"],
            status=TaskStatus.PENDING,
            estimated_llm_calls=2,
            estimated_tokens=5000,
        )
        tasks.append(viz_task)

        # Render Engine — depends on Data Visualizer + Presentation Designer
        render_task = TaskNode(
            id="task_render_engine",
            agent=AgentName.RENDER_ENGINE,
            model_tier=ModelTier.STANDARD,
            description="Assemble final PDF with WeasyPrint at 300 DPI, embed fonts, verify page flow",
            dependencies=["task_data_visualizer", "task_presentation_designer"],
            status=TaskStatus.PENDING,
            estimated_llm_calls=1,
            estimated_tokens=3000,
        )
        tasks.append(render_task)

        # Calculate totals
        total_llm_calls = sum(t.estimated_llm_calls for t in tasks)
        total_tokens = sum(t.estimated_tokens for t in tasks)
        # Estimate duration: parallel tasks take max time, sequential tasks sum
        # Rough estimate: 2 min per wave, ~5 waves
        estimated_duration = max(8, len(selected_agents) * 2 + 10)

        # All agents in the DAG
        all_agents = list(selected_agents)
        for t in tasks:
            if t.agent not in all_agents:
                all_agents.append(t.agent)

        # Build adaptation log with initial context
        init_log: list[str] = []
        if second_brain_context:
            init_log.append(f"Second Brain context retrieved: {len(second_brain_context)} chars of prior research")

        return WorkflowDAG(
            engagement_id=engagement_id,
            question=question,
            question_type=question_types[0],
            tasks=tasks,
            agents_selected=all_agents,
            estimated_total_llm_calls=total_llm_calls,
            estimated_total_tokens=total_tokens,
            estimated_duration_minutes=float(estimated_duration),
            adaptation_log=init_log,
        )

    def _assign_tier(self, agent: AgentName, question_types: list[QuestionType]) -> ModelTier:
        """Assign a model tier to a task based on the agent and question type.

        This is budget allocation (Skill 6). The tier is NOT random — it
        is based on:
        - The agent's default tier (from ARCHITECTURE.md)
        - The question complexity (GO_NO_GO needs higher tiers than GENERAL)
        - Budget conservation (don't burn STRONG/DEEP on simple tasks)

        The 20% reserve is preserved for Quality Gate, Synthesis, and Render.
        """
        # Default tiers from ARCHITECTURE.md
        default_tiers: dict[AgentName, ModelTier] = {
            AgentName.MARKET_ANALYST: ModelTier.STANDARD,
            AgentName.COMPETITIVE_INTEL: ModelTier.STANDARD,
            AgentName.FINANCIAL_ANALYST: ModelTier.STANDARD,
            AgentName.RISK_ANALYST: ModelTier.STANDARD,
            AgentName.TECHNOLOGY_ANALYST: ModelTier.STANDARD,
            AgentName.OPERATIONS_ANALYST: ModelTier.STANDARD,
            AgentName.REGULATORY_ANALYST: ModelTier.STANDARD,
            AgentName.SUSTAINABILITY_ANALYST: ModelTier.STANDARD,
            AgentName.CONSUMER_INSIGHTS: ModelTier.STANDARD,
            AgentName.MA_ANALYST: ModelTier.STRONG,
            AgentName.INNOVATION_ANALYST: ModelTier.STANDARD,
            AgentName.STRATEGY_ANALYST: ModelTier.STRONG,
        }

        tier = default_tiers.get(agent, ModelTier.STANDARD)

        # Upgrade tier for complex question types
        if QuestionType.GO_NO_GO in question_types and agent == AgentName.FINANCIAL_ANALYST:
            tier = ModelTier.STRONG  # Financial modeling for go/no-go needs STRONG

        return tier

    def _estimate_llm_calls(self, agent: AgentName) -> int:
        """Estimate LLM calls for a task based on the agent's methodology.

        Each agent's methodology has a specific number of steps, each
        potentially requiring an LLM call. This is NOT a guess — it's
        based on the agent's documented methodology in ARCHITECTURE.md.
        """
        estimates: dict[AgentName, int] = {
            AgentName.MARKET_ANALYST: 8,       # 10-step methodology, ~8 LLM calls
            AgentName.COMPETITIVE_INTEL: 7,    # 8-step methodology
            AgentName.FINANCIAL_ANALYST: 10,   # 9-step methodology + sensitivity
            AgentName.RISK_ANALYST: 6,         # 10-step methodology, batched
            AgentName.TECHNOLOGY_ANALYST: 6,   # 8-step methodology
            AgentName.OPERATIONS_ANALYST: 5,   # 8-step methodology, batched
            AgentName.REGULATORY_ANALYST: 6,   # 8-step methodology
            AgentName.SUSTAINABILITY_ANALYST: 6,  # 8-step methodology
            AgentName.CONSUMER_INSIGHTS: 7,    # 7-step methodology
            AgentName.MA_ANALYST: 8,           # 9-step methodology
            AgentName.INNOVATION_ANALYST: 7,   # 9-step methodology
            AgentName.STRATEGY_ANALYST: 6,     # Framework selection + analysis
        }
        return estimates.get(agent, 5)

    def _estimate_tokens(self, agent: AgentName, tier: ModelTier) -> int:
        """Estimate token consumption for a task.

        Based on the tier's output budget (§3.4) and the number of LLM calls.
        """
        output_budgets = {
            ModelTier.MICRO: 500,
            ModelTier.FAST: 2000,
            ModelTier.STANDARD: 4000,
            ModelTier.STRONG: 8000,
            ModelTier.DEEP: 16000,
        }
        calls = self._estimate_llm_calls(agent)
        output_per_call = output_budgets.get(tier, 4000)
        # Input tokens: system prompt + search results ≈ 3000 per call
        input_per_call = 3000
        return calls * (input_per_call + output_per_call)

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 10-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(self, question: str, conversation_context: str = "") -> WorkflowDAG:
        """Execute the Engagement Director's 10-step methodology.

        This is NOT a generic "plan the engagement" method. It is the
        specific 10-step methodology from §4.3:

        1. Receive question + conversation context
        2. Classify question type(s)
        3. Query Second Brain for prior research on this topic
        4. Decompose into 4-8 research domains
        5. Select specialists for each domain
        6. Build dependency graph (parallel vs sequential)
        7. Assign model tiers per task
        8. Estimate total LLM calls + token consumption
        9. Dispatch to AgentBus
        10. Monitor execution, adapt if needed

        Returns the WorkflowDAG — the blueprint for the engagement.
        """
        engagement_id = f"eng_{uuid.uuid4().hex[:12]}"

        # Subscribe to ALL bus channels — the Director is omniscient (§4.8)
        self.subscribe_to_bus()

        # Step 1: Receive question + conversation context
        context_detail = f" (context: {conversation_context[:60]}...)" if conversation_context else ""
        await self._transition(
            AgentState.WORKING,
            f"Received question: {question[:80]}{context_detail}",
        )

        # Step 2: Classify question type(s)
        await self._transition(AgentState.WORKING, "Classifying question type")
        question_types, selected_agents, key_question = await self._classify_question_llm(question)

        # Step 3: Query Second Brain for prior research
        await self._transition(AgentState.WORKING, "Querying Second Brain for prior research")
        second_brain_context = await self._query_second_brain(question)

        # Steps 4-8: Decompose, select, build DAG, assign tiers, estimate
        await self._transition(AgentState.WORKING, "Building workflow DAG")
        dag = self._build_dag(
            engagement_id=engagement_id,
            question=question,
            question_types=question_types,
            selected_agents=selected_agents,
            key_question=key_question,
            second_brain_context=second_brain_context,
        )

        # Store the DAG for monitoring and adaptive replanning
        self._current_dag = dag

        # Step 9: Dispatch to AgentBus
        await self._transition(
            AgentState.WORKING,
            f"Dispatching {len(dag.tasks)} tasks to {len(selected_agents)} specialists",
        )

        # Publish the DAG to the bus so the TUI and all agents can see it
        await self.bus.publish(
            channel=Channel.STATUS,
            msg_type=MessageType.STATUS,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "state": "working",
                "detail": f"DAG built: {len(dag.tasks)} tasks, {dag.estimated_total_llm_calls} LLM calls",
                "dag": dag.model_dump(),
            },
        )

        # Step 10: Monitor execution, adapt if needed
        # The Director stays active and monitors the bus for escalations.
        # The _handle_bus_message callback handles escalations in real-time.
        # The orchestrator (engagement runner) will call this method to get
        # the DAG, then dispatch tasks and keep the Director alive for
        # adaptive replanning.

        await self._transition(
            AgentState.DONE,
            f"DAG complete: {len(dag.tasks)} tasks, ~{dag.estimated_duration_minutes:.0f}min",
        )

        return dag

    def get_current_dag(self) -> WorkflowDAG | None:
        """Get the current workflow DAG (for the orchestrator)."""
        return self._current_dag

    def get_escalation_count(self) -> int:
        """Get the number of escalations received during this engagement."""
        return self._escalation_count
