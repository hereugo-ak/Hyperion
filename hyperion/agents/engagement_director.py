"""
HYPERION Engagement Director, Agent 1, the partner.

This is NOT a generic planner. This is the senior consulting partner who:
- Identifies the key question behind the question
- Knows which frameworks apply (and which don't)
- Anticipates which findings will change the analysis direction
- Adjusts team composition in real-time when new information emerges

The Engagement Director is the entry point for every engagement. It
receives the business question, decomposes it into research domains,
selects the right specialists (not all 12, only the ones that matter
for this question), builds a dependency graph, assigns model tiers
based on complexity and budget, and dispatches to the AgentBus.

During execution, it monitors the bus for ESCALATION messages and
adapts the plan mid-flight, spawning new agents, rerouting
dependencies, or reallocating tiers. This is adaptive replanning
(§10.2), and it is what makes HYPERION dynamic, not a fixed pipeline.

Model Tier: STRONG (Nemotron 3 Super 120B, planning requires strong reasoning)
Tools: All tools (read-only), can see everything, modify nothing directly
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
It doesn't just "plan." It thinks like a senior consulting partner, it
identifies the key question behind the question, knows which frameworks
apply, anticipates which findings will change the analysis direction,
and adjusts the team composition in real-time. A generic planner says
"research these 5 topics." The Engagement Director says "Market sizing
is the critical path, start it first and give it STRONG tier.
Competitive intelligence can run in parallel at STANDARD. Financial
depends on Market's TAM number, so queue it. If Regulatory finds a
compliance barrier, reroute to add a Legal Risk sub-task."
(§4.3, §0.1)
"""

from __future__ import annotations

import json
import sys
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
from hyperion.schemas.workflow import (
    QuestionType,
    ResearchDomain,
    RosterDecision,
    SubjectClass,
    TaskNode,
    TaskStatus,
    WorkflowDAG,
)
from hyperion.tools.query_utils import (
    canonicalize_geographies,
    detect_geographies,
    is_contentless,
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
        # Orchestrator, needs SECOND_BRAIN for prior research context, DEEP_SEARCH for initial
        # scoping
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
                "Technology. This is not a guess, it is a structured "
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
                "question. Not all 12 are spawned every time, that would waste "
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
                "it is strategic adaptation. The Director evaluates the "
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
        "consulting firm. You are the partner, the one who receives the "
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
        "adapt, not start over.\n\n"
        "4. Adjust team composition in real-time. If the Regulatory Analyst "
        "finds a compliance barrier, you spawn the Regulatory Analyst (even "
        "if it wasn't in the original plan) and reroute the Financial Analyst "
        "to include compliance costs. This is adaptive replanning, not error "
        "handling.\n\n"
        "5. Allocate budget deliberately. Market sizing is the critical path"
        "give it STRONG tier. Competitive intelligence can run at STANDARD. "
        "Keyword expansion can use MICRO. Synthesis gets DEEP. The 20% reserve "
        "is for the Quality Gate and final render, never spend it on research.\n\n"
        "Your output is a WorkflowDAG, a typed Pydantic model with task nodes, "
        "dependencies, tier assignments, and budget estimates. This is the "
        "blueprint for the entire engagement. Every task has a specific agent, "
        "a specific question, a specific tier, and specific dependencies. "
        "No task is generic. No dependency is accidental.\n\n"
        "You are the partner. The buck stops with you. If the engagement "
        "fails, it's because you decomposed the question wrong, selected the "
        "wrong team, or missed a critical dependency. That is the weight of "
        "being the Engagement Director."
    ),
    spawn_condition="Always active, the Engagement Director is the first agent initialized and the last to shut down.",
    max_sub_agents=0,  # Director does not spawn sub-agents directly
    output_model="WorkflowDAG",
)


# ─────────────────────────────────────────────────────────────────────────────
# W-06: Subject ontology + method eligibility, the roster's second axis
# ─────────────────────────────────────────────────────────────────────────────
#
# The roster used to be a function of question_type ALONE: QUESTION_TYPE_AGENTS
# put FINANCIAL_ANALYST in all six question types, so "Should India increase
# manufacturing?" got a DCF analyst, a firm-level method aimed at a nation
# state, which then spent three retrieval rounds discovering it had nothing to
# retrieve (RC-6: six empty chapters).
#
# Question type is the grammatical form of the question. It says nothing about
# what the analysis must operate ON. That is the subject class, and it is the
# subject class that decides which METHODS are meaningful. The roster is now
# gated by method eligibility: each specialist declares its analytical methods
# and the subject classes each method applies to. An agent with no eligible
# method for the classified subject class is not dispatched, and the
# exclusion is RECORDED with its reason, because the methodology section
# (W-10) and the report's scope note must state that the omission was
# deliberate, and the audit trail is what makes a future DCF-on-a-country
# immediately traceable.
#
# This is deliberately method eligibility, not an agent-exclusion table:
# FINANCIAL_ANALYST retains DCF for COMPANY and gains fiscal-cost and
# public-investment analysis for POLICY and NATION_OR_REGION, it is never
# "dropped"; some of its methods simply do not apply to some subjects.

# The pool from which every roster is drawn, question type no longer picks
# agents; it only shapes priority and tiers downstream.
_SPECIALIST_POOL: tuple[AgentName, ...] = (
    AgentName.MARKET_ANALYST,
    AgentName.COMPETITIVE_INTEL,
    AgentName.FINANCIAL_ANALYST,
    AgentName.RISK_ANALYST,
    AgentName.TECHNOLOGY_ANALYST,
    AgentName.OPERATIONS_ANALYST,
    AgentName.REGULATORY_ANALYST,
    AgentName.SUSTAINABILITY_ANALYST,
    AgentName.CONSUMER_INSIGHTS,
    AgentName.MA_ANALYST,
    AgentName.INNOVATION_ANALYST,
    AgentName.STRATEGY_ANALYST,
)

# Per-agent declared methods → subject classes each method applies to.
# An agent is eligible for an engagement iff AT LEAST ONE of its methods
# lists the classified subject class.
AGENT_METHODS: dict[AgentName, dict[str, frozenset[SubjectClass]]] = {
    AgentName.MARKET_ANALYST: {
        # sizing/segmentation work on a market whatever entity the client is
        "market sizing": frozenset(SubjectClass),
        "segmentation": frozenset(SubjectClass),
        "growth decomposition": frozenset(SubjectClass),
        "share concentration": frozenset(SubjectClass),
        "demand analysis": frozenset(SubjectClass),
    },
    AgentName.COMPETITIVE_INTEL: {
        # a competitor matrix requires identifiable competing FIRMS. Nations
        # compete too, but that comparison is jurisdiction/policy work, it
        # belongs to regulatory_analyst and strategy_analyst, not to an
        # agent whose craft is profiling firms.
        "competitor matrix": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
        "moat assessment": frozenset({SubjectClass.COMPANY}),
        "positioning analysis": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
    },
    AgentName.FINANCIAL_ANALYST: {
        # firm-level methods: meaningless on a nation state
        "dcf valuation": frozenset({SubjectClass.COMPANY}),
        "ev/ebitda comparables": frozenset({SubjectClass.COMPANY}),
        "unit economics": frozenset({SubjectClass.COMPANY}),
        # the same agent, different craft: what a treasury team does
        "fiscal-cost analysis": frozenset({SubjectClass.NATION_OR_REGION, SubjectClass.POLICY}),
        "public-investment analysis": frozenset({SubjectClass.NATION_OR_REGION, SubjectClass.POLICY}),
        # maturity-curve cost work on a technology
        "cost curve": frozenset({SubjectClass.TECHNOLOGY, SubjectClass.MARKET, SubjectClass.COMPANY}),
    },
    AgentName.RISK_ANALYST: {
        # every subject class carries risk; the methods differ in name only
        "risk matrix": frozenset(SubjectClass),
        "scenario analysis": frozenset(SubjectClass),
        "black swan assessment": frozenset(SubjectClass),
        "mitigation planning": frozenset(SubjectClass),
        "geopolitical risk": frozenset({SubjectClass.NATION_OR_REGION, SubjectClass.POLICY, SubjectClass.COMPANY}),
    },
    AgentName.TECHNOLOGY_ANALYST: {
        "maturity curve": frozenset({SubjectClass.TECHNOLOGY}),
        "patent landscape": frozenset({SubjectClass.TECHNOLOGY, SubjectClass.COMPANY, SubjectClass.MARKET}),
        "tech stack assessment": frozenset({SubjectClass.COMPANY, SubjectClass.TECHNOLOGY}),
        "build vs buy": frozenset({SubjectClass.COMPANY}),
        "tco analysis": frozenset({SubjectClass.COMPANY, SubjectClass.TECHNOLOGY}),
        # industrial-capability lens for national manufacturing questions
        "industrial capability assessment": frozenset({SubjectClass.NATION_OR_REGION}),
    },
    AgentName.OPERATIONS_ANALYST: {
        "process mapping": frozenset({SubjectClass.COMPANY}),
        "bottleneck analysis": frozenset({SubjectClass.COMPANY}),
        "supply chain analysis": frozenset({SubjectClass.COMPANY, SubjectClass.NATION_OR_REGION, SubjectClass.MARKET}),
        "kpi design": frozenset({SubjectClass.COMPANY, SubjectClass.PERSON_OR_ORG}),
        # logistics/industrial-base capacity at national scale
        "capacity assessment": frozenset({SubjectClass.NATION_OR_REGION, SubjectClass.COMPANY, SubjectClass.MARKET}),
    },
    AgentName.REGULATORY_ANALYST: {
        "compliance mapping": frozenset(SubjectClass),
        "jurisdiction comparison": frozenset({SubjectClass.NATION_OR_REGION, SubjectClass.POLICY, SubjectClass.COMPANY}),
        "horizon scanning": frozenset(SubjectClass),
        "policy comparison": frozenset({SubjectClass.POLICY, SubjectClass.NATION_OR_REGION}),
    },
    AgentName.SUSTAINABILITY_ANALYST: {
        "esg assessment": frozenset({SubjectClass.COMPANY, SubjectClass.NATION_OR_REGION, SubjectClass.MARKET, SubjectClass.POLICY}),
        "carbon footprint": frozenset({SubjectClass.COMPANY, SubjectClass.NATION_OR_REGION, SubjectClass.TECHNOLOGY, SubjectClass.MARKET}),
        "green financing": frozenset({SubjectClass.COMPANY, SubjectClass.NATION_OR_REGION, SubjectClass.POLICY}),
    },
    AgentName.CONSUMER_INSIGHTS: {
        # consumers exist only downstream of a firm or a market
        "persona development": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
        "journey mapping": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
        "willingness to pay": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
        "nps analysis": frozenset({SubjectClass.COMPANY}),
        "demand research": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
    },
    AgentName.MA_ANALYST: {
        # acquisition is an act of firms (or firms inside markets)
        "target identification": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
        "synergy analysis": frozenset({SubjectClass.COMPANY}),
        "accretion dilution": frozenset({SubjectClass.COMPANY}),
        "comparable transactions": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
    },
    AgentName.INNOVATION_ANALYST: {
        "trl assessment": frozenset({SubjectClass.TECHNOLOGY}),
        "hype cycle positioning": frozenset({SubjectClass.TECHNOLOGY, SubjectClass.MARKET}),
        "disruption patterns": frozenset({SubjectClass.TECHNOLOGY, SubjectClass.MARKET, SubjectClass.COMPANY}),
        "adoption s-curve": frozenset({SubjectClass.TECHNOLOGY, SubjectClass.MARKET}),
        # national industrial-policy innovation lens
        "innovation ecosystem assessment": frozenset({SubjectClass.NATION_OR_REGION, SubjectClass.POLICY}),
    },
    AgentName.STRATEGY_ANALYST: {
        "porters five forces": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
        "vrio analysis": frozenset({SubjectClass.COMPANY}),
        "blue ocean analysis": frozenset({SubjectClass.COMPANY, SubjectClass.MARKET}),
        "strategic options analysis": frozenset(SubjectClass),
        # industrial strategy / trade-strategy lens for nations and policies
        "industrial strategy analysis": frozenset({SubjectClass.NATION_OR_REGION, SubjectClass.POLICY}),
    },
}


class SubjectClassAbstainError(RuntimeError):
    """Subject class could not be established with sufficient confidence (W-06).

    Raised by the planning path when the classifier abstains and no
    interactive user is available to answer a clarifying question. The
    engagement must fail at PLANNING time, before a single token is spent
    on dispatch, rather than guess a subject class and staff the roster
    on the guess. A guessed subject class is exactly the bug this work item
    removes.
    """

    def __init__(self, question: str, confidence: float, raw: str = "", detail: str = ""):
        self.question = question
        self.confidence = confidence
        self.raw = raw
        if detail:
            message = f"{detail}. Engagement fails at planning time: {question[:120]!r}."
        else:
            message = (
                f"Subject class confidence too low ({confidence:.2f} < "
                f"{SUBJECT_CLASS_CONFIDENCE_THRESHOLD:.2f}) for: {question[:120]!r}. "
                "Interactive runs must ask the user a clarifying question; "
                "scripted runs abstain and fail rather than guess."
            )
        super().__init__(message)


# Backward-compatible public name retained for callers and persisted diagnostics.
SubjectClassAbstain = SubjectClassAbstainError


# Below this confidence the Director must not proceed on the classifier's
# guess, it asks one clarifying question (interactive) or abstains-and-fails
# (scripted). Never guess: a wrong subject class staffs the wrong roster.
SUBJECT_CLASS_CONFIDENCE_THRESHOLD: float = 0.6


def eligible_methods(agent: AgentName, subject_class: SubjectClass) -> list[str]:
    """The agent's declared methods that apply to this subject class.

    Order-stable (declaration order) so records and tests are deterministic.
    """
    return [
        method
        for method, classes in AGENT_METHODS.get(agent, {}).items()
        if subject_class in classes
    ]

# ── Keyword fallbacks, the LAST resort, never the decision ──────────────────
#
# These lists used to run UNCONDITIONALLY, forcing specialists onto the roster
# before the LLM was even asked. That is backwards. The Director already makes
# a real LLM call to decompose the question, and an LLM reading "should India
# reduce its dependence on the imports" understands that this is a trade-policy
# and regulatory question far better than a substring scan for "regulat" can.
#
# A keyword list cannot tell "we have no plans to acquire anyone" from "we are
# evaluating an acquisition", and it silently misses every phrasing its author
# did not think of"tuck-in""roll-up""bolt-on""carve-out" are all
# ordinary M&A vocabulary and none of them contain "acqui" or "merger".
#
# They are therefore consulted ONLY when the LLM decomposition fails outright.
# That call goes to one of five providers and can time out or be rate-limited,
# and when it does a roster chosen by weak keywords beats no roster at all.
# This is a degradation path with a knowingly inferior signal, and the code
# now says so rather than presenting it as the primary mechanism.
MA_TRIGGERS = ["acqui", "merger", "m&a", "buyout", "consolidat", "takeover"]
SUSTAINABILITY_TRIGGERS = ["esg", "sustainab", "carbon", "green", "climate", "environmental"]
REGULATORY_TRIGGERS = ["regulat", "compliance", "legal", "jurisdiction", "permit", "license"]

# Which specialist each fallback list implies, when the LLM cannot answer.
_TRIGGER_FALLBACKS: tuple[tuple[list[str], AgentName], ...] = (
    (MA_TRIGGERS, AgentName.MA_ANALYST),
    (SUSTAINABILITY_TRIGGERS, AgentName.SUSTAINABILITY_ANALYST),
    (REGULATORY_TRIGGERS, AgentName.REGULATORY_ANALYST),
)


# ─────────────────────────────────────────────────────────────────────────────
# Engagement Director Agent
# ─────────────────────────────────────────────────────────────────────────────


class EngagementDirector(BaseAgent):
    """Agent 1: The Engagement Director, the partner.

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
        # Escalation storm defences. Each escalation evaluation is a
        # STRONG-tier LLM call, so the Director must bound how many it will
        # make and refuse to re-evaluate an issue it has already ruled on.
        self._seen_escalations: set[str] = set()
        self._escalations_evaluated: int = 0
        self._max_escalation_evaluations: int = 12

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling, the Director is omniscient
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
        """Handle an escalation from an agent, adaptive replanning (§10.2).

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

        # ── Storm defence 1: never evaluate the same issue twice ──────────
        # _evaluate_escalation() is an unconditional STRONG-tier LLM call.
        # Identical escalations can only produce an identical adaptation, so
        # re-evaluating them is pure waste of the scarcest quota we have.
        # Agents already deduplicate locally (BaseAgent._escalate), but the
        # Director must be independently safe: distinct agents can raise the
        # same issue, and a retried/replanned agent starts with a clean set.
        fingerprint = f"{agent_name}:{issue.strip().lower()[:160]}"
        if fingerprint in self._seen_escalations:
            self._log(
                f"DIRECTOR: skipping duplicate escalation from {agent_name}: {issue[:100]}"
            )
            return
        self._seen_escalations.add(fingerprint)

        # ── Storm defence 2: hard cap on LLM evaluations ──────────────────
        # Beyond the cap we still RECORD every escalation in the adaptation
        # log (so nothing is lost from the audit trail) but stop paying for
        # strategic re-planning. A run that escalates this much is degraded
        # already; the right move is to finish and deliver, not to keep
        # re-planning until the budget is gone and no PDF is produced.
        if self._escalations_evaluated >= self._max_escalation_evaluations:
            self._log(
                f"DIRECTOR: escalation evaluation cap reached "
                f"({self._max_escalation_evaluations}); logging without LLM "
                f"re-planning: {issue[:100]}"
            )
            return
        self._escalations_evaluated += 1

        # Use LLM to evaluate the escalation and determine adaptation
        try:
            adaptation = await self._evaluate_escalation(issue, suggested_action)
        except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
            # An escalation handler must never itself abort the engagement.
            self._log(
                f"DIRECTOR: escalation evaluation failed "
                f"({type(e).__name__}: {e!s:.150}); continuing"
            )
            return

        if adaptation:
            try:
                await self._apply_adaptation(adaptation)
            except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
                self._log(
                    f"DIRECTOR: applying adaptation failed "
                    f"({type(e).__name__}: {e!s:.150}); continuing"
                )

    async def _evaluate_escalation(self, issue: str, suggested_action: str) -> dict[str, Any] | None:
        """Use LLM to evaluate an escalation and determine the adaptation.

        This is NOT a generic "handle the error" function. It is strategic
        adaptation, the Director asks the LLM whether this finding changes
        the analysis direction and what adjustments to make.
        """
        # D-22: this method is callable independently of _handle_escalation.
        # Keep its nullable-DAG contract local so future call sites cannot
        # dereference engagement state before a DAG exists or after teardown.
        if self._current_dag is None:
            return None

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
        """Apply an adaptation to the current DAG, adaptive replanning (§10.2)."""
        if self._current_dag is None:
            return

        # Spawn new agent if needed
        spawn_agent = adaptation.get("spawn_agent")
        spawn_question = adaptation.get("spawn_question")
        if spawn_agent and spawn_question:
            try:
                agent_name = AgentName(spawn_agent)
                # Never duplicate work that is already in flight or complete.
                # A FAILED task may be deliberately retried by an adaptation.
                already_active = any(
                    task.agent == agent_name
                    and task.status in (TaskStatus.RUNNING, TaskStatus.COMPLETED)
                    for task in self._current_dag.tasks
                )
                if already_active:
                    self._current_dag.adaptation_log.append(
                        f"Spawn skipped: {agent_name.value} is already running or complete"
                    )
                else:
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
        if reroute_from and reroute_to and reroute_from != reroute_to:
            # Find tasks for the agent that should wait. The name inequality
            # prevents an adaptation from introducing a self-dependency cycle.
            for task in self._current_dag.tasks:
                if task.agent.value == reroute_from:
                    # Add dependency on the reroute_to agent's task
                    for dep_task in self._current_dag.tasks:
                        if (
                            dep_task.agent.value == reroute_to
                            and dep_task.id != task.id
                            and dep_task.id not in task.dependencies
                        ):
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
        """Handle a status update from an agent, update task status in DAG."""
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
    # Second Brain query, prior research (§12.8)
    # ─────────────────────────────────────────────────────────────────────

    async def _query_second_brain(self, question: str) -> str:
        """Query the Second Brain vault for prior research on this topic.

        HYPERION is a learning system (§12.8). The Director doesn't start
        from scratch, it checks the vault for prior engagements, market
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

        This is NOT the final classification, it's a starting point that
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
        forecast_patterns = ["forecast", "predict", "future", "will", "by "
            "20", "next year", "outlook"]
        if any(p in q_lower for p in forecast_patterns):
            types.append(QuestionType.FORECAST)

        # Diagnostic patterns
        diagnostic_patterns = ["why", "what's wrong", "diagnose", "root cause", "problem", "issue"]
        if any(p in q_lower for p in diagnostic_patterns):
            types.append(QuestionType.DIAGNOSTIC)

        # Optimization patterns
        optimization_patterns = ["optimize", "improve", "efficient", "reduce "
            "cost", "increase", "enhance"]
        if any(p in q_lower for p in optimization_patterns):
            types.append(QuestionType.OPTIMIZATION)

        # Fallback
        if not types:
            types.append(QuestionType.GENERAL)

        return types

    def _trigger_fallback_agents(self, question: str) -> list[AgentName]:
        """Keyword-implied specialists, only for use when the LLM call fails.

        See the note on MA_TRIGGERS. This is deliberately NOT called on the
        happy path: substring matching cannot read intent, and running it
        unconditionally meant a question mentioning "green" got a
        sustainability analyst whether or not the question was about
        sustainability.
        """
        q_lower = (question or "").lower()
        out: list[AgentName] = []
        for triggers, agent in _TRIGGER_FALLBACKS:
            if any(t in q_lower for t in triggers) and agent not in out:
                out.append(agent)
        return out

    async def _classify_question_llm(self, question: str) -> tuple[list[QuestionType], SubjectClass, list[AgentName], str]:
        """Decompose the question: classify it, scope it, and staff it.

        THE DECOMPOSING AGENT OWNS THE SCOPE. This method is where the
        question is first read and broken down, so it is also where the
        question's *scope*, which country it concerns and which industry, is established. Everything downstream consumes that decision instead
        of re-deriving it.

        This is a correction of a real architectural inversion. Geography used
        to be decided further down the pipeline by a regex gazetteer scanning
        the raw question, while the Director, which was already spending an
        LLM call on this very question, was never asked. The gazetteer then
        matched the English pronoun "us" in "help us decide whether to enter
        India" and anchored the entire engagement to the United States. No
        word list can be trusted with a judgement like that; a model that has
        read the sentence can.

        The LLM now returns, in one call:
        1. question_types, the grammatical classification
        2. subject_class, W-06 second axis: what the question is ABOUT
                                 (company/nation_or_region/technology/policy/
                                 market/person_or_org), with a confidence, the roster is gated on this, never on
                                 question type alone
        3. selected_agents, the LLM's proposed roster, INCLUDING any
                                 specialist implied by the question's
                                 substance (M&A, ESG, regulatory), a PROPOSAL
                                 that the subject-class gate then filters by
                                 method eligibility
        4. key_question, the question behind the question
        5. geographies, jurisdictions the question is about, primary
                                 first, or [] if it names none
        6. subject, the industry/sector/topic
        7. research_domains, the decomposition proper
        8. critical_path, sequencing

        Geography and subject are recorded on ``self._llm_geographies`` and
        ``self._llm_subject``; subject class on ``self._llm_subject_class``
        for ``_build_dag`` to publish on the DAG. Every roster decision, dispatched or excluded, with its reason, is recorded on
        ``self._roster_decisions`` (W-06; consumed by W-10's methodology
        section and the report scope note).

        When subject-class confidence is below the threshold the Director
        does NOT guess: it asks one clarifying question (interactive shell)
        or raises SubjectClassAbstain (scripted run), a thirty second
        clarification is cheaper than a thirty minute engagement that
        produces six empty chapters.

        Returns: (question_types, subject_class, selected_agents, key_question)
        """
        heuristic_types = self._classify_question_heuristic(question)
        heuristic_str = ", ".join(qt.value for qt in heuristic_types)

        # Reset per-call so a failed decomposition cannot silently reuse the
        # scope of the previous engagement, that is exactly the kind of leak
        # that produces a confident report about the wrong country.
        self._llm_geographies: list[str] = []
        self._llm_subject: str = ""
        self._llm_subject_class: SubjectClass | None = None
        self._roster_decisions: list[RosterDecision] = []

        prompt = (
            f"You are the Engagement Director at HYPERION Consulting. "
            f"Classify this business question and select the right specialists.\n\n"
            f"Question: {question}\n\n"
            f"Heuristic classification: {heuristic_str}\n\n"
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
            f"  - selected_agents: array of agent names from the list above, "
            f"select ALL specialists that are relevant (typically 8-12 for a "
            f"comprehensive analysis; never fewer than 6)\n"
            f"  - key_question: the real question behind the question (1-2 sentences)\n"
            f"  - geographies: array of the countries/regions this question is "
            f"actually about, MOST IMPORTANT FIRST. Use canonical names "
            f"(\"India\", \"US\", \"EU\", \"China\", \"Brazil\"). Include a country "
            f"ONLY if the question is about it. Return [] if the question names "
            f"no jurisdiction, [] is a correct and useful answer meaning "
            f"\"analyse without a country filter\". NEVER guess or default to "
            f"\"US\"/\"EU\": a wrong country makes the entire report wrong. "
            f"Beware the English pronoun \"us\" (as in \"help us decide\"), that "
            f"is NOT the United States.\n"
            f"  - subject: the industry, sector or topic the question is about, "
            f"as a short noun phrase of 1-5 words (e.g. \"electronics imports\", "
            f"\"electric vehicles\", \"pharmaceutical manufacturing\"). Return \"\" "
            f"if the question names no industry. Do NOT return a sentence.\n"
            f"  - subject_class: what entity the analysis must operate ON, "
            f"exactly one of: company (a firm or business unit), "
            f"nation_or_region (a country, trade bloc, state or province), "
            f"technology (a technology or technology family), policy (a "
            f"tariff, subsidy, regulation or scheme), market (a market or "
            f"industry as a whole), person_or_org (an individual, regulator "
            f"or institution). This is the decision that gates which "
            f"analytical methods are meaningful, a DCF values a firm, it "
            f"cannot value a nation state. If the question is ambiguous, "
            f"choose the best fit and lower your confidence; NEVER omit the "
            f"field.\n"
            f"  - subject_class_confidence: your confidence in subject_class, "
            f"a number 0.0-1.0. Below 0.6 the Director will ask the user a "
            f"clarifying question rather than proceed on your guess, an "
            f"honest low number is a correct and useful answer.\n"
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
            return self._classification_fallback(question, heuristic_types)

        try:
            data = json.loads(response.content)
            if not isinstance(data, dict):
                raise ValueError("decomposition did not return a JSON object")

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

            # No unconditional keyword injection here. The LLM's roster is a
            # PROPOSAL: W-06 gates it below by method eligibility against the
            # subject class, a firm-level method is never aimed at a nation
            # state, however strongly the decomposition asks for it. Keyword
            # triggers only rescue an EMPTY proposal.
            if not selected_agents:
                selected_agents = self._trigger_fallback_agents(question)

            key_question = data.get("key_question", question)

            # ── Scope: the decomposing agent's answer, canonicalised ──────
            #
            # canonicalize_geographies NORMALISES what the Director said; it
            # does not second-guess it. "the Indian market" becomes "India",
            # and a country the gazetteer has never heard of is kept verbatim
            # rather than dropped, because an incomplete alias table is not
            # evidence that the model is wrong.
            #
            # The deterministic scan is consulted ONLY if the Director
            # returned nothing at all, a partial JSON object is common enough
            # that losing the country anchor to it is a real risk. Even then
            # it only DETECTS what the user wrote; [] stays [].
            geographies = canonicalize_geographies(data.get("geographies"))
            if not geographies:
                geographies = canonicalize_geographies(
                    data.get("geography") or data.get("jurisdictions")
                )
            if not geographies:
                geographies = detect_geographies(question or "")
            self._llm_geographies = geographies

            subject = data.get("subject") or data.get("industry") or ""
            self._llm_subject = self._clean_subject(subject)

            # ── W-06: the second axis, subject class, with an abstain path ──
            #
            # The roster below is gated on this classification, so accepting
            # it on low confidence would staff the engagement on a guess.
            # Below the threshold the Director asks one clarifying question
            # when a user is present, and abstains-and-fails when it is not,
            # never guesses.
            subject_class, sc_confidence = self._parse_subject_class(data)
            self._llm_subject_class = subject_class
            if sc_confidence < SUBJECT_CLASS_CONFIDENCE_THRESHOLD:
                subject_class = self._resolve_low_confidence_subject(
                    question, sc_confidence, str(data.get("subject_class", "") or "")
                )
                self._llm_subject_class = subject_class

            # ── W-06: the roster is gated by method eligibility ──────────
            #
            # Every agent considered is recorded, dispatched WITH its
            # eligible methods, or excluded WITH its reason. The exclusion
            # record is not optional: it is the audit trail that makes the
            # omission deliberate and quotable in the methodology section.
            selected_agents = self._gate_roster_by_subject(
                selected_agents, subject_class, question
            )

            # Store research domains for DAG building
            self._llm_research_domains = data.get("research_domains", [])
            self._llm_critical_path = data.get("critical_path", "")

            return question_types, subject_class, selected_agents, key_question

        except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
            return self._classification_fallback(question, heuristic_types)

    def _classification_fallback(
        self, question: str, heuristic_types: list[QuestionType]
    ) -> tuple[list[QuestionType], SubjectClass, list[AgentName], str]:
        """Degradation path when the decomposition call fails outright (W-06).

        Reached when the LLM is unreachable, rate-limited, or returns
        unparseable content. Before W-06 this method staffed the engagement
        from the single-axis QUESTION_TYPE_AGENTS table, knowingly weak,
        but survivable, because question type only shaped the roster.

        W-06 changed the stakes: the roster is now gated by SUBJECT CLASS,
        and this path has no subject class. Guessing one here (or worse,
        defaulting to COMPANY) would dispatch firm-level methods at nation
        states, precisely the DCF-on-a-country failure this work item
        removes, dressed up as a resilience feature. The documented
        behaviour for scripted runs is therefore abstain-and-fail: the
        engagement stops at planning time, before a single dispatch token
        is spent. Interactive runs ask one clarifying question instead.
        """
        self._llm_geographies = detect_geographies(question or "")
        self._llm_subject = ""
        self._llm_subject_class = None
        self._llm_research_domains = []
        self._llm_critical_path = ""
        self._roster_decisions = []

        subject_class = self._resolve_low_confidence_subject(
            question, 0.0, "decomposition call failed"
        )
        types = heuristic_types or [QuestionType.GENERAL]
        agents = self._trigger_fallback_agents(question)
        agents = self._gate_roster_by_subject(agents, subject_class, question)
        return types, subject_class, agents, question

    @staticmethod
    def _parse_subject_class(data: dict[str, Any]) -> tuple[SubjectClass, float]:
        """Read the LLM's subject classification, tolerating its sloppiness.

        Unknown or misspelled class values do not raise, they collapse to
        the default class with zero confidence, which routes to the abstain
        path. That is deliberate: an unparseable classification IS low
        confidence, and the low-confidence path is already the safe one.
        """
        raw = str(data.get("subject_class") or "").strip().lower()
        if not raw:
            return SubjectClass.COMPANY, 0.0
        normalized = raw.replace("-", "_").replace(" ", "_")
        try:
            subject_class = SubjectClass(normalized)
        except ValueError:
            return SubjectClass.COMPANY, 0.0

        conf_raw = data.get("subject_class_confidence", data.get("confidence", 1.0))
        try:
            confidence = float(conf_raw)
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        return subject_class, confidence

    def _resolve_low_confidence_subject(
        self, question: str, confidence: float, raw: str
    ) -> SubjectClass:
        """The abstain path: clarify with the user, or fail, never guess (W-06).

        Interactive shell: one clarifying question, the user's own words
        decide the subject class. Scripted run: SubjectClassAbstain, the
        engagement dies at planning time, before any dispatch token is
        spent, rather than proceed on a guessed subject class. A guessed
        subject class is how a DCF ends up aimed at a nation state.
        """
        if sys.stdin.isatty():
            # Exactly one clarifying question, thirty seconds of the user's
            # time against thirty minutes of an engagement with empty
            # chapters.
            print(
                "\nHYPERION could not establish what this question is about "
                f"with enough confidence (confidence {confidence:.2f}).",
                flush=True,
            )
            print(f"  Question: {question}", flush=True)
            print(
                "  Is it primarily about: [1] a company/firm  "
                "[2] a country or region  [3] a technology  [4] a policy "
                "[5] a market/industry  [6] a person or organisation?",
                flush=True,
            )
            choice = input("  Your answer (1-6): ").strip()
            mapping = {
                "1": SubjectClass.COMPANY,
                "2": SubjectClass.NATION_OR_REGION,
                "3": SubjectClass.TECHNOLOGY,
                "4": SubjectClass.POLICY,
                "5": SubjectClass.MARKET,
                "6": SubjectClass.PERSON_OR_ORG,
            }
            if choice in mapping:
                return mapping[choice]
            # Even interactively, an unusable answer is not a licence to
            # guess: fail and let the user rerun.
            raise SubjectClassAbstain(question, confidence, raw)
        raise SubjectClassAbstain(question, confidence, raw)

    def _gate_roster_by_subject(
        self,
        proposed: list[AgentName],
        subject_class: SubjectClass,
        question: str,
    ) -> list[AgentName]:
        """Filter the proposed roster by method eligibility, recording every call.

        The roster is a function of (question_type, subject_class), this
        method is the subject-class half. Each specialist is dispatched only
        if at least one of its declared methods applies to the classified
        subject class, and the decision is recorded either way:

          - dispatched:    (agent, eligible_methods, dispatched=True, reason)
          - excluded:      (agent, [], dispatched=False, reason)
          - added (empty proposal): recorded with the reason that the LLM
            proposed no eligible specialist, visibility, not silence.

        Every dispatched agent is guaranteed to carry at least one eligible
        method; _build_dag asserts that invariant again before the DAG
        ships.
        """
        min_agents = 6
        roster: list[AgentName] = []
        decisions: list[RosterDecision] = []
        excluded: list[AgentName] = []

        for agent in proposed:
            methods = eligible_methods(agent, subject_class)
            if methods and agent not in roster:
                roster.append(agent)
                decisions.append(RosterDecision(
                    agent=agent,
                    subject_class=subject_class,
                    eligible_methods=methods,
                    dispatched=True,
                    reason=f"{len(methods)} declared method(s) apply to {subject_class.value}",
                ))
            elif not methods:
                excluded.append(agent)
                decisions.append(RosterDecision(
                    agent=agent,
                    subject_class=subject_class,
                    eligible_methods=[],
                    dispatched=False,
                    reason=(
                        f"no declared method applies to {subject_class.value}: "
                        f"all of this agent's methods are "
                        f"{sorted({sc.value for ms in AGENT_METHODS.get(agent, {}).values() for sc in ms})}"
                    ),
                ))

        # Pad from the pool when the (filtered) proposal is thin. Padding is
        # itself subject-gated, the pool never smuggles a firm-level-only
        # agent past the gate the way QUESTION_TYPE_AGENTS used to.
        if len(roster) < min_agents:
            for agent in _SPECIALIST_POOL:
                if len(roster) >= min_agents:
                    break
                if agent in roster or agent in excluded:
                    continue
                methods = eligible_methods(agent, subject_class)
                if methods:
                    roster.append(agent)
                    decisions.append(RosterDecision(
                        agent=agent,
                        subject_class=subject_class,
                        eligible_methods=methods,
                        dispatched=True,
                        reason=(
                            f"added from the specialist pool to reach the minimum "
                            f"roster of {min_agents}; {len(methods)} method(s) "
                            f"apply to {subject_class.value}"
                        ),
                    ))
                else:
                    excluded.append(agent)
                    decisions.append(RosterDecision(
                        agent=agent,
                        subject_class=subject_class,
                        eligible_methods=[],
                        dispatched=False,
                        reason=f"no declared method applies to {subject_class.value}",
                    ))

        if not roster:
            # The subject gate found nothing defensible to dispatch. An empty
            # roster is a planning failure, not a plan: proceed silently and
            # the engagement burns budget to produce an empty report.
            raise SubjectClassAbstain(
                question, 1.0,
                detail=(
                    f"No specialist has an eligible method for subject class "
                    f"{subject_class.value} (proposed: {[a.value for a in proposed]})"
                ),
            )

        self._roster_decisions = decisions
        return roster

    @staticmethod
    def _clean_subject(value: Any) -> str:
        """Accept a subject only if it is a LABEL, not prose.

        LLMs asked for "the industry" sometimes answer with a sentence. A
        sentence spliced into a search query is as useless as an empty string,
        so an over-long answer is rejected outright and the pipeline falls
        back to deriving a subject from the question.
        """
        if isinstance(value, (list, tuple)):
            value = next((v for v in value if isinstance(v, str) and v.strip()), "")
        if not isinstance(value, str):
            return ""
        text = value.strip().strip("\"'").strip(" ,;:-")
        if not text:
            return ""
        if len(text.split()) > 8 or any(ch in text for ch in ".?!"):
            return ""
        if is_contentless(text):
            return ""
        return text[:80]

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

        The DAG is NOT a fixed pipeline, it is custom-built for this
        specific question. No two DAGs are identical.
        """
        # ── W-06 sanity assertion ─────────────────────────────────────────
        # Every dispatched specialist must have at least one declared method
        # eligible for the classified subject class. If this fails, the
        # subject gate was bypassed, fail at PLANNING time, before any
        # tokens are spent, rather than discover empty chapters at delivery.
        subject_class = getattr(self, "_llm_subject_class", None)
        if isinstance(subject_class, SubjectClass):
            for agent in selected_agents:
                methods = eligible_methods(agent, subject_class)
                assert methods, (
                    f"W-06 roster invariant violated: {agent.value} was "
                    f"dispatched with no method eligible for subject class "
                    f"{subject_class.value}. The subject gate in "
                    f"_classify_question_llm must have been bypassed."
                )

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

                # Coerce priority to int, LLMs sometimes return "high"/"low"
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

        # D5.1: a `wave_0_agents = [...]` list of 11 AgentNames sat here,
        # assigned and never read (ruff F841). It was not merely unused, it was
        # actively *misleading*: it listed MA_ANALYST and STRATEGY_ANALYST as
        # wave-0 (independent) agents, while the dependency edges built below
        # give M&A a dependency on Financial and Strategy dependencies on Market
        # and Competitive. So the dead list contradicted the live logic, and a
        # reader trusting it would conclude the graph was wrong.
        #
        # Waves are not declared anywhere; they *emerge* from the `dependencies`
        # edges assigned per task below, which is the correct design, one source
        # of truth. The comment block above documents the resulting shape; this
        # list pretended to implement it and did not.

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

        # Synthesis Lead, depends on all specialist tasks
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

        # Fact Checker, depends on all specialist tasks (runs in parallel with Synthesis)
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

        # Quality Gate, depends on Synthesis + Fact Checker
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

        # W-03: delivery chain re-pointed so the writer runs LAST and every
        # input exists before the stage that consumes it:
        #   Data Visualizer  (charts exist as FILES before any HTML references
        # them, previously the designer staged HTML that
        #                     pointed at chart files the visualizer had not
        #                     rendered yet)
        #   Presentation Designer  (consumes charts, stages HTML + layout plan
        # ONLY, W-03 removes its PDF authorship)
        #   Render Engine    (the single PDF writer; reads the staged HTML,
        #                     renders, audits, finalises)

        # Data Visualizer, depends on Quality Gate (chart data comes from the
        # FinalReport, gated by quality)
        viz_task = TaskNode(
            id="task_data_visualizer",
            agent=AgentName.DATA_VISUALIZER,
            model_tier=ModelTier.STANDARD,
            description="Generate Plotly charts at 300 DPI with brand colors",
            dependencies=["task_quality_gate"],
            status=TaskStatus.PENDING,
            estimated_llm_calls=2,
            estimated_tokens=5000,
        )
        tasks.append(viz_task)

        # Presentation Designer, depends on Quality Gate + Data Visualizer
        design_task = TaskNode(
            id="task_presentation_designer",
            agent=AgentName.PRESENTATION_DESIGNER,
            model_tier=ModelTier.STRONG,
            description="Design the PDF layout, select Unsplash images, stage HTML with charts",
            dependencies=["task_quality_gate", "task_data_visualizer"],
            status=TaskStatus.PENDING,
            estimated_llm_calls=3,
            estimated_tokens=15000,
        )
        tasks.append(design_task)

        # Render Engine, depends on Presentation Designer (the ONLY writer)
        render_task = TaskNode(
            id="task_render_engine",
            agent=AgentName.RENDER_ENGINE,
            model_tier=ModelTier.STANDARD,
            description="Assemble final PDF with WeasyPrint at 300 DPI, embed fonts, verify page flow",
            dependencies=["task_presentation_designer"],
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
            # Carry the scope the Director extracted onto the DAG, so every
            # downstream consumer reads ONE decision made by the agent that
            # read the question, instead of each re-deriving geography from
            # the raw text with its own heuristics and disagreeing.
            geographies=list(getattr(self, "_llm_geographies", []) or []),
            subject=str(getattr(self, "_llm_subject", "") or ""),
            # W-06: the subject class the roster was gated on, and the full
            # recorded roster decisions (dispatched AND excluded, with
            # reasons), the methodology section (W-10) and the report's
            # scope note quote these verbatim.
            subject_class=(
                self._llm_subject_class.value
                if isinstance(getattr(self, "_llm_subject_class", None), SubjectClass)
                else ""
            ),
            roster_decisions=list(getattr(self, "_roster_decisions", []) or []),
            agents_selected=all_agents,
            estimated_total_llm_calls=total_llm_calls,
            estimated_total_tokens=total_tokens,
            estimated_duration_minutes=float(estimated_duration),
            adaptation_log=init_log,
        )

    def _assign_tier(self, agent: AgentName, question_types: list[QuestionType]) -> ModelTier:
        """Assign a model tier to a task based on the agent and question type.

        This is budget allocation (Skill 6). The tier is NOT random, it
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
        potentially requiring an LLM call. This is NOT a guess, it's
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
    # Main execution, the 10-step methodology
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

        Returns the WorkflowDAG, the blueprint for the engagement.
        """
        engagement_id = f"eng_{uuid.uuid4().hex[:12]}"

        # Subscribe to ALL bus channels, the Director is omniscient (§4.8)
        self.subscribe_to_bus()

        # Step 1: Receive question + conversation context
        context_detail = f" (context: {conversation_context[:60]}...)" if conversation_context else ""
        await self._transition(
            AgentState.WORKING,
            f"Received question: {question[:80]}{context_detail}",
        )

        # Step 2: Classify question type(s)
        await self._transition(AgentState.WORKING, "Classifying question type")
        question_types, subject_class, selected_agents, key_question = await self._classify_question_llm(question)

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
