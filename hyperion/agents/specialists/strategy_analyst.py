"""
HYPERION Strategy Analyst, Agent 14, the strategic frameworks specialist.

This is NOT a generic "apply SWOT to everything" agent. This is a specialist
with 8 proprietary analytical frameworks:

- Porter's Five Forces: Analyze industry attractiveness through five forces.
  Score each force as strong/moderate/weak with specific rationale.
- BCG growth-share matrix: Plot products/business units as stars, cash cows,
  question marks, dogs. Identify resource allocation recommendations.
- SWOT analysis: Structured SWOT with the critical distinction that it's a
  snapshot, not a strategy. Convert SWOT into a TOWS matrix to generate
  strategic options (SO, WO, ST, WT strategies).
- Blue Ocean strategy: Create uncontested market space using the eliminate-
  reduce-raise-create framework. Build a strategy canvas.
- VRIO framework: Evaluate resources/capabilities on Value, Rarity,
  Imitability, and Organization. Identify sustainable competitive advantage.
- Core competence analysis: Identify the 2-3 core competencies. Assess
  defensibility and transferability.
- Strategic option grid: Build a grid of 3-5 strategic options, each scored
  on feasibility, impact, risk, time to value, and resource requirements.
- Game theory: Analyze competitive interactions (prisoner's dilemma,
  sequential games, signaling). Identify dominant strategies and Nash
  equilibria.

It doesn't apply every framework to every question. It selects the right
framework for the specific question, Porter's for industry attractiveness,
VRIO for resource-based strategy, Blue Ocean for market creation, game theory
for competitive dynamics. A generic strategist applies SWOT to everything.
The HYPERION Strategy Analyst applies the framework that actually illuminates
the specific question, and explicitly says why it chose that framework over
the alternatives. (§4.4, Agent 14)

Model Tier: STRONG (Nemotron 3 Super 120B, strategy requires the strongest
reasoning)
Tools: SearxNG, Jina, Obscura
Sub-agents: Max 3, Porter's data, competitor moves, VRIO resources
Output: StrategyAnalysis (Five Forces, VRIO, SWOT/TOWS, strategic options,
        game theory)

Methodology (§4.4, Agent 14):
1. Search for strategic context (SearxNG + Jina)
2. Run Porter's Five Forces
3. Run VRIO on company resources
4. Build SWOT → TOWS matrix
5. Generate 3-5 strategic options
6. Score options on strategic option grid
7. Run game theory analysis on competitive dynamics
8. Produce StrategyAnalysis model
"""

from __future__ import annotations

import asyncio
import json
import re
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
    SubAgentSpec,
    ToolName,
)
from hyperion.schemas.models import (
    SWOTTOWS,
    BCGCategory,
    BCGMatrix,
    BCGUnit,
    BlueOceanStrategy,
    ConfidenceLevel,
    CoreCompetence,
    ForceStrength,
    GameTheoryAnalysis,
    KeyFinding,
    PorterFiveForces,
    Source,
    SourceCredibility,
    StrategicOption,
    StrategicOptionGrid,
    StrategyAnalysis,
    SWOTItem,
    TOWSStrategy,
    VRIOAssessment,
    VRIOResult,
)
from hyperion.tools.query_utils import resolve_subject

# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


STRATEGY_ANALYST_SPEC = AgentSpec(
    name=AgentName.STRATEGY_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Strategy Analyst",
    model_tier=ModelTier.STRONG,
    tools=[
        ToolName.UNIFIED_EXTRACT,
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Porter's Five Forces",
            description=(
                "Analyze industry attractiveness through five forces: threat "
                "of new entrants, bargaining power of suppliers, bargaining "
                "power of buyers, threat of substitutes, and competitive "
                "rivalry. Score each force as strong/moderate/weak with "
                "specific rationale. Not just 'rivalry is high''rivalry "
                "is STRONG because 3 well-funded competitors compete on price "
                "in a mature market with 70% combined market share.'"
            ),
            inputs=["industry_data", "competitor_landscape", "barriers_to_entry", "supplier_data", "buyer_data"],
            outputs=["five_force_scores", "rationale_per_force", "overall_attractiveness"],
        ),
        SkillSpec(
            name="BCG growth-share matrix",
            description=(
                "Plot the company's products/business units on the growth-"
                "share matrix (stars, cash cows, question marks, dogs). "
                "Identify resource allocation recommendations: invest stars, "
                "harvest cash cows, invest selectively in question marks, "
                "divest dogs."
            ),
            inputs=["business_units", "market_growth_rates", "market_share_data"],
            outputs=["bcg_categories", "resource_allocation_recommendations", "portfolio_balance"],
        ),
        SkillSpec(
            name="SWOT analysis with TOWS conversion",
            description=(
                "Structured SWOT with the critical distinction that SWOT is "
                "a snapshot, not a strategy. Convert SWOT into a TOWS matrix "
                "to generate strategic options: SO (strengths+opportunities), "
                "WO (weaknesses+opportunities), ST (strengths+threats), WT "
                "(weaknesses+threats)."
            ),
            inputs=["internal_analysis", "external_analysis", "company_resources"],
            outputs=["swot_factors", "tows_strategies", "strategic_options"],
        ),
        SkillSpec(
            name="Blue Ocean strategy",
            description=(
                "Identify whether the company can create uncontested market "
                "space using the eliminate-reduce-raise-create framework. "
                "Build a strategy canvas comparing the company to competitors. "
                "Not just 'differentiate''Eliminate: premium pricing. "
                "Reduce: feature bloat. Raise: customer support quality. "
                "Create: self-service onboarding.'"
            ),
            inputs=["industry_factors", "competitor_positioning", "customer_preferences"],
            outputs=["eliminate", "reduce", "raise", "create", "strategy_canvas", "new_market_space"],
        ),
        SkillSpec(
            name="VRIO framework",
            description=(
                "Evaluate resources/capabilities on Value, Rarity, "
                "Imitability, and Organization. Identify which resources "
                "provide sustainable competitive advantage vs. temporary "
                "advantage vs. competitive parity vs. disadvantage. Not just "
                "'our brand is valuable''Brand: V=yes, R=yes, I=yes "
                "(60-year history), O=yes → sustained competitive advantage.'"
            ),
            inputs=["resource_list", "capability_assessment", "competitor_resources"],
            outputs=["vrio_scores", "sustained_advantages", "temporary_advantages", "competitive_parity"],
        ),
        SkillSpec(
            name="Core competence analysis",
            description=(
                "Identify the 2-3 core competencies that give the company its "
                "competitive advantage. Assess whether these competencies are "
                "defensible (can competitors copy?) and transferable (can "
                "they be applied to new markets?)."
            ),
            inputs=["capability_inventory", "competitive_analysis", "market_analysis"],
            outputs=["core_competencies", "defensibility_assessment", "transferability_assessment"],
        ),
        SkillSpec(
            name="Strategic option grid",
            description=(
                "Build a grid of 3-5 strategic options, each scored on: "
                "feasibility, impact, risk, time to value, and resource "
                "requirements. Not just 'option A is good''Option A: "
                "feasibility HIGH, impact HIGH, risk MEDIUM, time 6-12mo, "
                "resources MEDIUM. Recommended.'"
            ),
            inputs=["strategic_options", "feasibility_data", "resource_constraints"],
            outputs=["scored_options", "recommended_option", "rationale"],
        ),
        SkillSpec(
            name="Game theory",
            description=(
                "Analyze competitive interactions using game theory "
                "(prisoner's dilemma, sequential games, signaling). Identify "
                "dominant strategies and Nash equilibria. Not just "
                "'competition is intense''This is a prisoner's dilemma: "
                "if both competitors cut prices, both lose. The Nash "
                "equilibrium is to maintain prices, but the temptation to "
                "defect is high.'"
            ),
            inputs=["competitor_strategies", "payoff_data", "market_dynamics"],
            outputs=["game_type", "dominant_strategy", "nash_equilibrium", "implications"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Strategy Analyst, the specialist who applies "
        "strategic frameworks to the question, evaluates competitive "
        "positioning, and designs strategic options.\n\n"
        "Your proprietary frameworks:\n"
        "1. Porter's Five Forces: Industry attractiveness through 5 forces. "
        "Score each as strong/moderate/weak with rationale.\n"
        "2. BCG growth-share matrix: Stars, cash cows, question marks, dogs. "
        "Resource allocation recommendations.\n"
        "3. SWOT → TOWS: SWOT is a SNAPSHOT, not a strategy. Convert to TOWS "
        "matrix for strategic options (SO, WO, ST, WT).\n"
        "4. Blue Ocean strategy: Eliminate-reduce-raise-create. Strategy "
        "canvas. Uncontested market space.\n"
        "5. VRIO: Value, Rarity, Imitability, Organization. Sustained vs. "
        "temporary advantage vs. parity vs. disadvantage.\n"
        "6. Core competence: 2-3 core competencies. Defensible? Transferable?\n"
        "7. Strategic option grid: 3-5 options scored on feasibility, impact, "
        "risk, time to value, resources.\n"
        "8. Game theory: Prisoner's dilemma, sequential games, signaling. "
        "Dominant strategies and Nash equilibria.\n\n"
        "CRITICAL RULE: You do NOT apply every framework to every question. "
        "You SELECT the right framework for the specific question:\n"
        "- Porter's for industry attractiveness questions\n"
        "- VRIO for resource-based strategy questions\n"
        "- Blue Ocean for market creation questions\n"
        "- Game theory for competitive dynamics questions\n"
        "- BCG for portfolio questions\n"
        "- SWOT/TOWS for general strategic positioning\n\n"
        "You must EXPLICITLY say which frameworks you selected and WHY, and "
        "which frameworks you did NOT select and WHY NOT. A generic "
        "strategist applies SWOT to everything. You apply the framework that "
        "actually illuminates the specific question.\n\n"
        "Rules:\n"
        "- EACH PORTER'S FORCE MUST HAVE SPECIFIC RATIONALE. Not just 'high' "
        "'STRONG because 3 competitors with 70% combined share compete on "
        "price in a mature market.'\n"
        "- SWOT MUST BE CONVERTED TO TOWS. SWOT alone is just a snapshot. "
        "TOWS generates strategic options.\n"
        "- VRIO MUST HAVE ALL 4 DIMENSIONS per resource. V, R, I, O, each "
        "yes/no with reasoning.\n"
        "- GAME THEORY MUST IDENTIFY THE GAME TYPE. Not just 'competition is "
        "intense', identify the specific game (prisoner's dilemma, "
        "sequential, signaling) and the Nash equilibrium.\n"
        "- STRATEGIC OPTIONS MUST BE SCORED. Not just 'option A is good'"
        "scored on feasibility, impact, risk, time to value, resources.\n\n"
        "You can spawn up to 3 sub-agents for parallel data collection:\n"
        "- Sub-agent A: Find Porter's Five Forces data for [industry] (MICRO, "
        "SearxNG + Jina)\n"
        "- Sub-agent B: Find competitor strategic moves in [space] (MICRO, "
        "SearxNG)\n"
        "- Sub-agent C: Find VRIO-relevant resources for [company] (FAST, "
        "SearxNG + Obscura)\n\n"
        "Your output is a StrategyAnalysis Pydantic model, structured, not "
        "free text."
    ),
    spawn_condition="Spawned when the question involves strategy, competitive "
                     "positioning, strategic options, industry attractiveness, "
                     "or strategic frameworks (STRATEGY, COMPETITIVE_"
                     "POSITIONING, FIVE_FORCES, VRIO, BLUE_OCEAN, GAME_THEORY, "
                     "SWOT types)",
    max_sub_agents=3,
    output_model="StrategyAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class StrategyAnalyst(BaseAgent):
    """Agent 14: The strategy frameworks specialist.

    Applies strategic frameworks to the question, evaluates competitive
    positioning, and designs strategic options. Selects the right framework
    for the specific question, doesn't apply SWOT to everything. Explicitly
    says why it chose that framework over alternatives. (§4.4, Agent 14)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for strategic context (SearxNG + Jina)
    3. Runs Porter's Five Forces
    4. Runs VRIO on company resources
    5. Builds SWOT → TOWS matrix
    6. Generates 3-5 strategic options and scores them
    7. Runs game theory analysis on competitive dynamics
    8. Produces StrategyAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or STRATEGY_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._strategy_db_data: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []
        # P-CORE: reconciled substantive sub-agent findings (published so sub-agent
        # evidence reaches the report, not just the parent's own analysis).
        self._sub_agent_reconciled: list[KeyFinding] = []
        # P-CORE: numeric contradictions surfaced between sub-agent findings.
        self._sub_agent_contradictions: list[str] = []

        # Framework selection
        self._frameworks_selected: list[str] = []
        self._frameworks_not_selected: list[str] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Strategy Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests
        - FINDINGS: receives findings from other agents (Competitive Intel's
          competitor data, Market Analyst's market data, Financial Analyst's
          financial performance, Innovation Analyst's disruption patterns)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "strategy_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Competitive Intel's competitor data informs Five Forces
                if finding.finding_type == "competitor_landscape":
                    self._context.setdefault("competitor_data", []).append(finding.content)
                # Market Analyst's market data informs industry attractiveness
                elif finding.finding_type == "market_growth":
                    self._context.setdefault("market_data", []).append(finding.content)
                # Innovation Analyst's disruption patterns inform strategy
                elif finding.finding_type == "disruption_pattern":
                    self._context.setdefault("disruption_data", []).append(finding.content)
                # Financial Analyst's financial data informs BCG matrix
                elif finding.finding_type == "financial_performance":
                    self._context.setdefault("financial_data", []).append(finding.content)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "verify_claims":
                # P2-17: the Fact Checker's Step 6 feedback path is handled
                # by the shared base handler, never dropped.
                await self._handle_verify_claims(payload)
                return
            if request_type == "strategic_options":
                # M&A Analyst requesting strategic options for acquisition rationale
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for strategic context (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_strategic_context(self, sector: str, company: str) -> list[dict[str, Any]]:
        """Search for strategic context.

        Uses SearxNG to find: strategic analyses, industry reports, competitive
        strategy research. Uses Jina to extract strategy reports, case studies,
        and academic strategic management papers. Uses Obscura to scrape
        JS-rendered strategy databases and competitive intelligence platforms.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{sector} Porter's Five Forces analysis",
                f"{sector} industry attractiveness competitive analysis",
                f"{company} strategic positioning competitive advantage",
                f"{sector} VRIO resources capabilities analysis",
                f"{sector} Blue Ocean strategy uncontested market",
                f"{sector} game theory competitive dynamics",
                f"{sector} BCG growth share matrix portfolio",
                f"{sector} strategic options analysis framework",
                f"{sector} SWOT TOWS strategic management",
                f"{sector} core competence competitive advantage",
            ]

            for pattern in query_patterns[:12]:
                search_results = await searxng.search(pattern, max_results=5)
                for r in search_results:
                    results.append({
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "snippet": r.get("content", ""),
                        "query": pattern,
                    })
                    self._sources.append(Source(
                        id=f"src_{len(self._sources):03d}",
                        title=r.get("title", ""),
                        url=r.get("url", ""),
                        credibility=SourceCredibility.INDUSTRY_REPORT,
                    ))

            # Extract content from top URLs using Jina
            try:
                jina = self.get_tool(ToolName.JINA)
                top_urls = [r["url"] for r in results[:6] if r.get("url")]
                for url in top_urls:
                    read_result = await jina.read(url)
                    if read_result and (read_result.markdown or read_result.content):
                        content = read_result.markdown or read_result.content
                    else:
                        continue
                    if content:
                        self._extracted_content.append({
                            "url": url,
                            "content": content[:15000],
                        })
            except (ValueError, AttributeError, RuntimeError):
                pass

            # Scrape strategy databases using Obscura
            try:
                obscura = self.get_tool(ToolName.OBSCURA)
                db_urls = [
                    f"https://www.bcg.com/capabilities/strategy/industry-{sector}",
                    "https://www.mckinsey.com/business-functions/strategy-and-corporate-finance/our-insights",
                    f"https://www.cbinsights.com/research-portal?industry={sector}",
                ]
                for url in db_urls[:4]:
                    try:
                        fetch_result = await obscura.fetch(url, stealth=True)
                        if fetch_result and (fetch_result.markdown or fetch_result.content):
                            page_data = {"content": (fetch_result.markdown or fetch_result.content)[:15000]}
                        else:
                            page_data = None
                        if page_data:
                            self._strategy_db_data.append({
                                "url": url,
                                "data": page_data,
                            })
                            self._sources.append(Source(
                                id=f"src_{len(self._sources):03d}",
                                title=f"Strategy DB, {url.split('/')[2]}",
                                url=url,
                                credibility=SourceCredibility.INDUSTRY_REPORT,
                                key_data=page_data["content"][:500],
                            ))
                    except (ValueError, AttributeError, RuntimeError):
                        continue
            except (ValueError, AttributeError, RuntimeError):
                pass

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Framework selection, the critical differentiator
    # ─────────────────────────────────────────────────────────────────────

    async def _select_frameworks(self, question: str, context: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Select the right frameworks for this specific question.

        The HYPERION Strategy Analyst doesn't apply every framework to every
        question. It selects the framework that actually illuminates the
        specific question, and explicitly says why it chose that framework
        over the alternatives.
        """
        sector = context.get("sector", context.get("industry", ""))
        company = context.get("company", "")

        prompt = (
            "You are the HYPERION Strategy Analyst selecting which frameworks "
            "to apply to this question.\n\n"
            f"Question: {question}\n\n"
            f"Sector: {sector}\n"
            f"Company: {company}\n\n"
            "Available frameworks:\n"
            "1. Porter's Five Forces, for industry attractiveness questions\n"
            "2. BCG growth-share matrix, for portfolio questions\n"
            "3. SWOT/TOWS, for general strategic positioning\n"
            "4. Blue Ocean strategy, for market creation questions\n"
            "5. VRIO framework, for resource-based strategy questions\n"
            "6. Core competence analysis, for capability assessment\n"
            "7. Strategic option grid, for evaluating strategic options\n"
            "8. Game theory, for competitive dynamics questions\n\n"
            "SELECT 3-5 frameworks that best illuminate this specific question. "
            "Do NOT apply all 8, select the RIGHT ones.\n\n"
            "Also list frameworks you are NOT selecting and why not.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "selected": [{"framework": "...", "reason": "..."}],\n'
            '  "not_selected": [{"framework": "...", "reason": "..."}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        selected: list[str] = []
        not_selected: list[str] = []

        if not response.success or not response.content:
            # Default: apply the most common frameworks
            return (["Porter's Five Forces", "SWOT/TOWS", "Strategic option grid"],
                    ["Blue Ocean strategy", "Game theory"])

        try:
            data = json.loads(response.content)

            for item in data.get("selected", []):
                framework = item.get("framework", "")
                reason = item.get("reason", "")
                selected.append(f"{framework}: {reason}")

            for item in data.get("not_selected", []):
                framework = item.get("framework", "")
                reason = item.get("reason", "")
                not_selected.append(f"{framework}: {reason}")

        except (json.JSONDecodeError, ValueError, TypeError):
            selected = ["Porter's Five Forces", "SWOT/TOWS", "Strategic option grid"]
            not_selected = ["Blue Ocean strategy", "Game theory"]

        return (selected, not_selected)

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Run Porter's Five Forces
    # ─────────────────────────────────────────────────────────────────────

    async def _run_porter_five_forces(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> PorterFiveForces:
        """Run Porter's Five Forces analysis.

        Each force scored as strong/moderate/weak with specific rationale.
        """
        sector = context.get("sector", context.get("industry", ""))
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )

        prompt = (
            "You are the HYPERION Strategy Analyst running Porter's Five "
            "Forces analysis.\n\n"
            f"Question: {question}\n"
            f"Sector: {sector}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Score each force as strong, moderate, or weak with SPECIFIC "
            "rationale:\n\n"
            "1. Threat of new entrants: barriers to entry (capital, regulation, "
            "brand loyalty, economies of scale, switching costs)\n"
            "2. Bargaining power of suppliers: supplier concentration, switching "
            "costs, substitute inputs\n"
            "3. Bargaining power of buyers: buyer concentration, price "
            "sensitivity, switching costs\n"
            "4. Threat of substitutes: alternative products/services, price-"
            "performance tradeoffs\n"
            "5. Competitive rivalry: number of competitors, growth rate, "
            "differentiation, exit barriers\n\n"
            "NOT just 'rivalry is high''rivalry is STRONG because 3 well-"
            "funded competitors compete on price in a mature market with 70% "
            "combined market share.'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "threat_of_new_entrants": "strong|moderate|weak",\n'
            '  "new_entrants_rationale": "...",\n'
            '  "bargaining_power_suppliers": "strong|moderate|weak",\n'
            '  "suppliers_rationale": "...",\n'
            '  "bargaining_power_buyers": "strong|moderate|weak",\n'
            '  "buyers_rationale": "...",\n'
            '  "threat_of_substitutes": "strong|moderate|weak",\n'
            '  "substitutes_rationale": "...",\n'
            '  "competitive_rivalry": "strong|moderate|weak",\n'
            '  "rivalry_rationale": "...",\n'
            '  "overall_attractiveness": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return PorterFiveForces()

        try:
            data = json.loads(response.content)

            force_map = {
                "strong": ForceStrength.STRONG,
                "moderate": ForceStrength.MODERATE,
                "weak": ForceStrength.WEAK,
            }

            return PorterFiveForces(
                threat_of_new_entrants=force_map.get(data.get("threat_of_new_entrants", "moderate"), ForceStrength.MODERATE),
                new_entrants_rationale=data.get("new_entrants_rationale", ""),
                bargaining_power_suppliers=force_map.get(data.get("bargaining_power_suppliers", "moderate"), ForceStrength.MODERATE),
                suppliers_rationale=data.get("suppliers_rationale", ""),
                bargaining_power_buyers=force_map.get(data.get("bargaining_power_buyers", "moderate"), ForceStrength.MODERATE),
                buyers_rationale=data.get("buyers_rationale", ""),
                threat_of_substitutes=force_map.get(data.get("threat_of_substitutes", "moderate"), ForceStrength.MODERATE),
                substitutes_rationale=data.get("substitutes_rationale", ""),
                competitive_rivalry=force_map.get(data.get("competitive_rivalry", "moderate"), ForceStrength.MODERATE),
                rivalry_rationale=data.get("rivalry_rationale", ""),
                overall_attractiveness=data.get("overall_attractiveness", ""),
                frameworks_used=self._frameworks_selected,
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return PorterFiveForces()

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Run VRIO on company resources
    # ─────────────────────────────────────────────────────────────────────

    async def _run_vrio(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> VRIOAssessment:
        """Run VRIO framework on company resources/capabilities.

        Evaluates each resource on Value, Rarity, Imitability, Organization.
        Identifies sustained vs. temporary advantages vs. parity vs. disadvantage.
        """
        company = context.get("company", "")
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in search_results[:6]
        )

        prompt = (
            "You are the HYPERION Strategy Analyst running VRIO analysis.\n\n"
            f"Question: {question}\n"
            f"Company: {company}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Identify 3-5 key resources/capabilities and evaluate each on:\n"
            "- is_valuable: does it exploit opportunities or neutralize threats?\n"
            "- is_rare: is it controlled by only a few firms?\n"
            "- is_inimitable: is it costly for others to imitate?\n"
            "- is_organized: is the firm organized to exploit it?\n"
            "- competitive_implication: competitive disadvantage, parity, "
            "temporary advantage, or sustained advantage\n\n"
            "VRIO decision rules:\n"
            "V=no → competitive disadvantage\n"
            "V=yes, R=no → competitive parity\n"
            "V=yes, R=yes, I=no → temporary advantage\n"
            "V=yes, R=yes, I=yes, O=yes → sustained advantage\n"
            "V=yes, R=yes, I=yes, O=no → unused advantage\n\n"
            "NOT just 'our brand is valuable''Brand: V=yes, R=yes, I=yes "
            "(60-year history), O=yes → sustained competitive advantage.'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "resources": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return VRIOAssessment()

        try:
            data = json.loads(response.content)

            resources: list[VRIOResult] = []
            sustained: list[str] = []
            temporary: list[str] = []
            parity: list[str] = []
            disadvantage: list[str] = []

            for r in data.get("resources", []):
                vrio_result = VRIOResult(
                    resource=r.get("resource", "Unknown"),
                    is_valuable=bool(r.get("is_valuable", False)),
                    is_rare=bool(r.get("is_rare", False)),
                    is_inimitable=bool(r.get("is_inimitable", False)),
                    is_organized=bool(r.get("is_organized", False)),
                    competitive_implication=r.get("competitive_implication", ""),
                    description=r.get("description", ""),
                )
                resources.append(vrio_result)

                impl = (vrio_result.competitive_implication or "").lower()
                if "sustained" in impl:
                    sustained.append(vrio_result.resource)
                elif "temporary" in impl:
                    temporary.append(vrio_result.resource)
                elif "parity" in impl:
                    parity.append(vrio_result.resource)
                elif "disadvantage" in impl:
                    disadvantage.append(vrio_result.resource)

            return VRIOAssessment(
                resources=resources,
                sustained_advantages=sustained,
                temporary_advantages=temporary,
                competitive_parity=parity,
                competitive_disadvantage=disadvantage,
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return VRIOAssessment()

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Build SWOT → TOWS matrix
    # ─────────────────────────────────────────────────────────────────────

    async def _build_swot_tows(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        porter: PorterFiveForces,
        vrio: VRIOAssessment,
        context: dict[str, Any],
    ) -> SWOTTOWS:
        """Build SWOT analysis and convert to TOWS matrix.

        SWOT is a snapshot, not a strategy. TOWS converts it to strategic
        options: SO, WO, ST, WT.
        """
        company = context.get("company", "")
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in search_results[:6]
        )
        vrio_summary = ", ".join(vrio.sustained_advantages[:3]) if vrio.sustained_advantages else "none identified"

        prompt = (
            "You are the HYPERION Strategy Analyst building SWOT → TOWS.\n\n"
            f"Question: {question}\n"
            f"Company: {company}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Porter's overall attractiveness: {porter.overall_attractiveness}\n"
            f"VRIO sustained advantages: {vrio_summary}\n\n"
            "Build a structured SWOT (2-4 factors per quadrant):\n"
            "- Strengths: internal advantages (from VRIO sustained advantages)\n"
            "- Weaknesses: internal disadvantages\n"
            "- Opportunities: external favorable factors\n"
            "- Threats: external unfavorable factors (from Porter's forces)\n\n"
            "Then convert SWOT to TOWS matrix:\n"
            "- SO: Use strengths to maximize opportunities\n"
            "- WO: Overcome weaknesses to pursue opportunities\n"
            "- ST: Use strengths to minimize threats\n"
            "- WT: Minimize weaknesses and avoid threats\n\n"
            "CRITICAL: SWOT is a SNAPSHOT, not a strategy. TOWS converts it "
            "to strategic options.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "strengths": [{"factor": "...", "description": "...", "evidence": "..."}],\n'
            '  "weaknesses": [{...}],\n'
            '  "opportunities": [{...}],\n'
            '  "threats": [{...}],\n'
            '  "tows_strategies": [{"strategy_type": "SO|WO|ST|WT", "strategy": "...", "description": "..."}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return SWOTTOWS()

        try:
            data = json.loads(response.content)

            def parse_items(key: str) -> list[SWOTItem]:
                return [
                    SWOTItem(
                        factor=item.get("factor", ""),
                        description=item.get("description", ""),
                        evidence=item.get("evidence", ""),
                    )
                    for item in data.get(key, [])
                ]

            tows: list[TOWSStrategy] = []
            for s in data.get("tows_strategies", []):
                tows.append(TOWSStrategy(
                    strategy_type=s.get("strategy_type", "SO"),
                    strategy=s.get("strategy", ""),
                    description=s.get("description", ""),
                ))

            return SWOTTOWS(
                strengths=parse_items("strengths"),
                weaknesses=parse_items("weaknesses"),
                opportunities=parse_items("opportunities"),
                threats=parse_items("threats"),
                tows_strategies=tows,
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return SWOTTOWS()

    # ─────────────────────────────────────────────────────────────────────
    # Steps 5-6: Generate strategic options + score on grid
    # ─────────────────────────────────────────────────────────────────────

    async def _generate_and_score_options(
        self,
        question: str,
        porter: PorterFiveForces,
        vrio: VRIOAssessment,
        swot_tows: SWOTTOWS,
        context: dict[str, Any],
    ) -> StrategicOptionGrid:
        """Generate 3-5 strategic options and score them on the strategic
        option grid.

        Each option scored on: feasibility, impact, risk, time to value,
        resource requirements.
        """
        tows_summary = "\n".join(
            f"- {s.strategy_type}: {s.strategy}"
            for s in swot_tows.tows_strategies[:6]
        )
        vrio_summary = ", ".join(vrio.sustained_advantages[:3]) if vrio.sustained_advantages else "none"

        prompt = (
            "You are the HYPERION Strategy Analyst generating strategic "
            "options and scoring them.\n\n"
            f"Question: {question}\n\n"
            f"Porter's overall: {porter.overall_attractiveness}\n"
            f"VRIO sustained advantages: {vrio_summary}\n\n"
            f"TOWS strategies:\n{tows_summary}\n\n"
            "Generate 3-5 strategic options based on the TOWS strategies. "
            "Score each on:\n"
            "- feasibility: high, medium, low\n"
            "- impact: high, medium, low\n"
            "- risk: high, medium, low\n"
            "- time_to_value: 0-6mo, 6-12mo, 1-2yr, 2-5yr\n"
            "- resource_requirements: high, medium, low\n"
            "- overall_score: ranking or score\n"
            "- recommendation: pursue, explore, or reject\n\n"
            "NOT just 'option A is good''Option A: feasibility HIGH, "
            "impact HIGH, risk MEDIUM, time 6-12mo, resources MEDIUM. "
            "Recommended.'\n\n"
            "Also identify the recommended option and why.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "options": [{...}],\n'
            '  "recommended_option": "...",\n'
            '  "rationale": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return StrategicOptionGrid()

        try:
            data = json.loads(response.content)

            options: list[StrategicOption] = []
            for opt in data.get("options", []):
                options.append(StrategicOption(
                    option_name=opt.get("option_name", "Unknown"),
                    description=opt.get("description", ""),
                    feasibility=opt.get("feasibility", ""),
                    impact=opt.get("impact", ""),
                    risk=opt.get("risk", ""),
                    time_to_value=opt.get("time_to_value", ""),
                    resource_requirements=opt.get("resource_requirements", ""),
                    overall_score=opt.get("overall_score", ""),
                    recommendation=opt.get("recommendation", ""),
                ))

            return StrategicOptionGrid(
                options=options,
                recommended_option=data.get("recommended_option", ""),
                rationale=data.get("rationale", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return StrategicOptionGrid()

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Run game theory analysis on competitive dynamics
    # ─────────────────────────────────────────────────────────────────────

    async def _run_game_theory(
        self,
        question: str,
        porter: PorterFiveForces,
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> GameTheoryAnalysis:
        """Run game theory analysis on competitive dynamics.

        Identifies game type (prisoner's dilemma, sequential, signaling),
        dominant strategies, and Nash equilibria.
        """
        sector = context.get("sector", context.get("industry", ""))
        rivalry = porter.competitive_rivalry.value if porter.competitive_rivalry else "moderate"
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in search_results[:5]
        )

        prompt = (
            "You are the HYPERION Strategy Analyst running game theory "
            "analysis on competitive dynamics.\n\n"
            f"Question: {question}\n"
            f"Sector: {sector}\n"
            f"Competitive rivalry: {rivalry}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Analyze competitive interactions using game theory:\n"
            "- game_type: prisoner's_dilemma, sequential, signaling, chicken, "
            "stag_hunt, or other\n"
            "- players: key competitors\n"
            "- strategies: available strategies for each player\n"
            "- payoff_matrix: payoff matrix for the game\n"
            "- dominant_strategy: dominant strategy if one exists\n"
            "- nash_equilibrium: Nash equilibrium description\n"
            "- implications: strategic implications for the company\n\n"
            "NOT just 'competition is intense''This is a prisoner's "
            "dilemma: if both competitors cut prices, both lose. The Nash "
            "equilibrium is to maintain prices, but the temptation to defect "
            "is high.'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "game_type": "...",\n'
            '  "players": ["..."],\n'
            '  "strategies": ["..."],\n'
            '  "payoff_matrix": [{"player": "...", "strategy": "...", "payoff": "..."}],\n'
            '  "dominant_strategy": "...",\n'
            '  "nash_equilibrium": "...",\n'
            '  "implications": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return GameTheoryAnalysis()

        try:
            data = json.loads(response.content)
            return GameTheoryAnalysis(
                game_type=data.get("game_type", ""),
                players=data.get("players", []),
                strategies=data.get("strategies", []),
                payoff_matrix=data.get("payoff_matrix", []),
                dominant_strategy=data.get("dominant_strategy", ""),
                nash_equilibrium=data.get("nash_equilibrium", ""),
                implications=data.get("implications", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return GameTheoryAnalysis()

    # ─────────────────────────────────────────────────────────────────────
    # Conditional frameworks (Phase 5.1e)
    #
    # `_select_frameworks` offers the LLM 8 frameworks and asks it to pick
    # 3-5. Five had implementations. Three, BCG growth-share matrix, Blue
    # Ocean, and core competence analysis, did not: `run()` hardcoded
    #
    #     bcg_matrix=None,       # Only applied if portfolio question
    #     blue_ocean=None,       # Only applied if market creation question
    #     core_competence=None,  # Derived from VRIO if needed
    #
    # and no such conditional path existed anywhere. So the selector could
    # (and for portfolio questions, would) choose a framework the agent was
    # structurally incapable of delivering, `StrategyAnalysis` carried three
    # permanently-null fields, and `frameworks_selected` advertised analysis
    # that was never performed, a false claim in the deliverable.
    #
    # These three run only when actually selected, which is what the original
    # comments described but never implemented.
    # ─────────────────────────────────────────────────────────────────────

    #: Selector labels -> the token we match on. Matching is substring-based
    #: against the lowercased "framework: reason" strings that
    #: `_select_frameworks` returns, because the LLM echoes the label with
    #: minor variations ("BCG matrix", "BCG growth-share matrix").
    _BCG_TOKENS = ("bcg", "growth-share", "growth share")
    _BLUE_OCEAN_TOKENS = ("blue ocean",)
    _CORE_COMPETENCE_TOKENS = ("core competence", "core competency", "core competencies")

    def _framework_selected(self, tokens: tuple[str, ...]) -> bool:
        """Did `_select_frameworks` pick a framework matching `tokens`?"""
        haystack = " ".join(self._frameworks_selected).lower()
        return any(token in haystack for token in tokens)

    async def _run_bcg_matrix(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> BCGMatrix:
        """Plot business units on the BCG growth-share matrix.

        Categorisation is derived from the two axes the framework is defined
        by, market growth rate and relative market share, not taken on the
        model's word. If the model says "star" but reports low growth, the
        axes win: the framework is the framework.
        """
        company = context.get("company", "")
        sector = context.get("sector", context.get("industry", ""))
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in search_results[:6]
        )

        prompt = (
            "You are the HYPERION Strategy Analyst building a BCG "
            "growth-share matrix.\n\n"
            f"Question: {question}\n"
            f"Company: {company}\n"
            f"Sector: {sector}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Identify 3-6 business units or product lines and for each give:\n"
            "- unit_name\n"
            "- market_growth_rate: the market's annual growth rate as a "
            "percentage, e.g. \"12%\" (the MARKET's growth, not the unit's "
            "revenue growth)\n"
            "- relative_market_share: share relative to the LARGEST "
            "competitor, e.g. \"1.4x\" means 40% larger than the leader, "
            "\"0.6x\" means 60% of the leader\n"
            "- recommendation: invest, harvest, hold, or divest\n\n"
            "The two axes are what define the category:\n"
            "  high growth + high relative share -> star (invest)\n"
            "  low growth  + high relative share -> cash cow (harvest)\n"
            "  high growth + low relative share  -> question mark (invest "
            "selectively)\n"
            "  low growth  + low relative share  -> dog (divest)\n\n"
            "Conventional thresholds: growth is 'high' above 10%; relative "
            "share is 'high' at or above 1.0x.\n\n"
            "Also assess portfolio_balance: does this portfolio fund itself? "
            "A portfolio of all question marks has no cash cow paying for "
            "them; a portfolio of all cash cows has no future.\n\n"
            "Do NOT invent units. If the sources do not identify distinct "
            "business units, return an empty list.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "units": [{"unit_name": "...", "market_growth_rate": "...", '
            '"relative_market_share": "...", "recommendation": "..."}],\n'
            '  "portfolio_balance": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return BCGMatrix()

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            await self._log_tool_use(
                "llm", "bcg_matrix", f"FAIL · unparseable JSON · {exc}", success=False
            )
            return BCGMatrix()

        units: list[BCGUnit] = []
        buckets: dict[BCGCategory, list[str]] = {c: [] for c in BCGCategory}

        for raw in data.get("units", []) or []:
            if not isinstance(raw, dict):
                continue
            name = str(raw.get("unit_name") or "").strip()
            if not name:
                # A unit with no name cannot be plotted or cited. Skipping it
                # silently is how "Unknown" placeholders reach the deliverable.
                continue

            growth = str(raw.get("market_growth_rate") or "").strip()
            share = str(raw.get("relative_market_share") or "").strip()
            category = self._classify_bcg(growth, share, raw.get("category"))

            unit = BCGUnit(
                unit_name=name,
                category=category,
                market_growth_rate=growth,
                relative_market_share=share,
                recommendation=str(raw.get("recommendation") or "").strip()
                or self._BCG_DEFAULT_ACTION[category],
            )
            units.append(unit)
            buckets[category].append(name)

        return BCGMatrix(
            units=units,
            stars=buckets[BCGCategory.STAR],
            cash_cows=buckets[BCGCategory.CASH_COW],
            question_marks=buckets[BCGCategory.QUESTION_MARK],
            dogs=buckets[BCGCategory.DOG],
            portfolio_balance=str(data.get("portfolio_balance") or "").strip()
            or self._describe_portfolio_balance(buckets),
        )

    #: The framework's own prescription per quadrant, used when the model
    #: omits a recommendation. Not a guess, this is what BCG says.
    _BCG_DEFAULT_ACTION = {
        BCGCategory.STAR: "invest",
        BCGCategory.CASH_COW: "harvest",
        BCGCategory.QUESTION_MARK: "invest selectively or divest",
        BCGCategory.DOG: "divest",
    }

    #: Conventional BCG axis thresholds.
    BCG_HIGH_GROWTH_PCT = 10.0
    BCG_HIGH_RELATIVE_SHARE = 1.0

    @staticmethod
    def _parse_leading_number(text: str) -> float | None:
        """Pull the first number out of strings like "12%", "1.4x", "~8.5 %"."""
        match = re.search(r"-?\d+(?:\.\d+)?", text or "")
        if match is None:
            return None
        try:
            return float(match.group(0))
        except ValueError:
            return None

    def _classify_bcg(
        self,
        growth: str,
        share: str,
        model_category: Any = None,
    ) -> BCGCategory:
        """Derive the BCG quadrant from the axes, falling back to the model.

        The axes are authoritative when both are quantified: BCG's categories
        are *defined* by them, so a model that labels a low-growth unit a
        "star" is contradicting the framework it was asked to apply. When an
        axis is missing we fall back to the model's own label rather than
        guessing, and only then to QUESTION_MARK (the genuinely
        "insufficient information" quadrant, high potential, unproven).
        """
        growth_val = self._parse_leading_number(growth)
        share_val = self._parse_leading_number(share)

        if growth_val is not None and share_val is not None:
            high_growth = growth_val >= self.BCG_HIGH_GROWTH_PCT
            high_share = share_val >= self.BCG_HIGH_RELATIVE_SHARE
            if high_growth and high_share:
                return BCGCategory.STAR
            if not high_growth and high_share:
                return BCGCategory.CASH_COW
            if high_growth and not high_share:
                return BCGCategory.QUESTION_MARK
            return BCGCategory.DOG

        if model_category:
            label = str(model_category).strip().lower().replace(" ", "_").replace("-", "_")
            for candidate in BCGCategory:
                if candidate.value == label:
                    return candidate

        return BCGCategory.QUESTION_MARK

    @staticmethod
    def _describe_portfolio_balance(buckets: dict[BCGCategory, list[str]]) -> str:
        """State the funding logic of the portfolio when the model didn't.

        Every imbalance present is reported, not just the first one found. An
        earlier version returned on the first match, so a portfolio of three
        dogs and one question mark was described only as "no cash cow to fund
        the growth unit", true, but it silently omitted that three quarters of
        the portfolio should be divested. A partial diagnosis of a portfolio is
        arguably worse than none: the reader believes they have the whole
        picture and acts on one finding while the larger one goes unmentioned.
        """
        if not any(buckets.values()):
            return ""
        cows = len(buckets[BCGCategory.CASH_COW])
        marks = len(buckets[BCGCategory.QUESTION_MARK])
        stars = len(buckets[BCGCategory.STAR])
        dogs = len(buckets[BCGCategory.DOG])

        census = (
            f"{stars} star(s), {cows} cash cow(s), "
            f"{marks} question mark(s), {dogs} dog(s)"
        )

        issues: list[str] = []
        if cows == 0 and (marks or stars):
            issues.append(
                f"{marks + stars} growth unit(s) with no cash cow generating "
                "the cash to fund them"
            )
        if cows and not (stars or marks):
            issues.append(
                f"{cows} cash cow(s) but no stars or question marks, the "
                "portfolio has no future growth engine"
            )
        if dogs > (stars + cows + marks):
            issues.append(f"{dogs} dog(s) dominate the portfolio")

        if issues:
            return f"Unbalanced ({census}): " + "; ".join(issues) + "."
        return f"Balanced: {census}."

    async def _run_blue_ocean(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> BlueOceanStrategy:
        """Apply the eliminate-reduce-raise-create framework.

        Note the schema field is `raise_factors` with `validation_alias="raise"`
        (``raise`` is a Python keyword), so the JSON key we ask for is "raise"
        but we populate `raise_factors` explicitly rather than relying on the
        alias surviving a hand-built constructor call.
        """
        company = context.get("company", "")
        sector = context.get("sector", context.get("industry", ""))
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in search_results[:6]
        )

        prompt = (
            "You are the HYPERION Strategy Analyst applying Blue Ocean "
            "strategy.\n\n"
            f"Question: {question}\n"
            f"Company: {company}\n"
            f"Sector: {sector}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Apply the eliminate-reduce-raise-create grid:\n"
            "- eliminate: which factors the industry competes on should be "
            "eliminated entirely?\n"
            "- reduce: which should be reduced well BELOW the industry "
            "standard?\n"
            "- raise: which should be raised well ABOVE the industry "
            "standard?\n"
            "- create: which factors has the industry never offered?\n\n"
            "Then judge honestly whether a blue ocean is actually available. "
            "Most industries are red oceans and most companies should compete "
            "in them better rather than pretend otherwise. Set "
            "is_blue_ocean_feasible to false and say why if so, a false "
            "negative here is far cheaper than recommending a market that "
            "does not exist.\n\n"
            "If feasible, describe new_market_space concretely: who is the "
            "non-customer being converted, and what makes them a customer?\n\n"
            "Also build a strategy_canvas: for each key competing factor, "
            "give the industry's typical level and the proposed level.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "eliminate": ["..."],\n'
            '  "reduce": ["..."],\n'
            '  "raise": ["..."],\n'
            '  "create": ["..."],\n'
            '  "strategy_canvas": [{"factor": "...", "industry": "...", '
            '"proposed": "..."}],\n'
            '  "is_blue_ocean_feasible": true,\n'
            '  "new_market_space": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return BlueOceanStrategy()

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            await self._log_tool_use(
                "llm", "blue_ocean", f"FAIL · unparseable JSON · {exc}", success=False
            )
            return BlueOceanStrategy()

        def string_list(key: str) -> list[str]:
            """Coerce a JSON array to clean strings, dropping non-values.

            `None` is filtered *before* stringification. `str(None)` is the
            literal `"None"`, which is truthy and 4 characters long, so a
            model emitting `["Showroom footprint", null]` would have put the
            word "None" into the eliminate axis of a client-facing exhibit.
            Booleans and dicts are rejected for the same reason: a factor is
            a phrase, and anything that is not one is model noise.
            """
            raw = data.get(key)
            if not isinstance(raw, list):
                return []
            out: list[str] = []
            for v in raw:
                if v is None or isinstance(v, bool | dict | list):
                    continue
                text = str(v).strip()
                if text:
                    out.append(text)
            return out

        canvas: list[dict[str, str]] = []
        for row in data.get("strategy_canvas") or []:
            if isinstance(row, dict):
                canvas.append({str(k): str(v) for k, v in row.items()})

        feasible = bool(data.get("is_blue_ocean_feasible", False))
        space = str(data.get("new_market_space") or "").strip()

        # A "feasible blue ocean" with no described market space is an empty
        # claim, the single most seductive failure mode of this framework.
        # Downgrade rather than publish it.
        if feasible and not space:
            feasible = False
            space = (
                "Claimed feasible but no uncontested market space was "
                "described; treated as not feasible."
            )

        return BlueOceanStrategy(
            eliminate=string_list("eliminate"),
            reduce=string_list("reduce"),
            raise_factors=string_list("raise") or string_list("raise_factors"),
            create=string_list("create"),
            strategy_canvas=canvas,
            is_blue_ocean_feasible=feasible,
            new_market_space=space,
        )

    async def _run_core_competence(
        self,
        question: str,
        vrio: VRIOAssessment,
        context: dict[str, Any],
    ) -> CoreCompetence:
        """Identify core competencies, grounded in the VRIO assessment.

        The original comment said "Derived from VRIO if needed", this is that
        derivation, made real. VRIO's sustained advantages are exactly the
        Prahalad-and-Hamel test for a core competence, so they are passed in
        as the evidence base rather than asking the model to start over.
        """
        company = context.get("company", "")
        vrio_summary = "\n".join(
            f"- {r.resource}: {r.competitive_implication}"
            for r in vrio.resources[:8]
        ) or "(no VRIO resources were identified)"

        prompt = (
            "You are the HYPERION Strategy Analyst identifying core "
            "competencies.\n\n"
            f"Question: {question}\n"
            f"Company: {company}\n\n"
            f"VRIO assessment already performed:\n{vrio_summary}\n\n"
            f"Sustained advantages: {', '.join(vrio.sustained_advantages) or 'none identified'}\n\n"
            "Identify the 2-3 genuine core competencies. A core competence "
            "must pass all three tests:\n"
            "1. It provides disproportionate customer value\n"
            "2. It is difficult for competitors to imitate\n"
            "3. It can be leveraged across multiple products or markets\n\n"
            "A large budget is not a competence. Being big is not a "
            "competence. If only one competence passes all three tests, "
            "return one, do not pad to three.\n\n"
            "Assess is_defensible and is_transferable as booleans, and "
            "justify each in defensibility_assessment and "
            "transferability_assessment.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "competencies": ["..."],\n'
            '  "competency_descriptions": ["..."],\n'
            '  "is_defensible": true,\n'
            '  "is_transferable": true,\n'
            '  "defensibility_assessment": "...",\n'
            '  "transferability_assessment": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return CoreCompetence()

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            await self._log_tool_use(
                "llm", "core_competence", f"FAIL · unparseable JSON · {exc}", success=False
            )
            return CoreCompetence()

        competencies = [
            str(c).strip() for c in (data.get("competencies") or []) if str(c).strip()
        ]
        descriptions = [
            str(d).strip()
            for d in (data.get("competency_descriptions") or [])
            if str(d).strip()
        ]

        # Keep descriptions aligned with competencies. The two are parallel
        # lists, so a length mismatch silently mis-attributes a description to
        # the wrong competence, pad instead of letting them slip.
        if descriptions and len(descriptions) != len(competencies):
            if len(descriptions) < len(competencies):
                descriptions = descriptions + [""] * (len(competencies) - len(descriptions))
            else:
                descriptions = descriptions[: len(competencies)]

        return CoreCompetence(
            competencies=competencies,
            competency_descriptions=descriptions,
            is_defensible=bool(data.get("is_defensible", False)),
            is_transferable=bool(data.get("is_transferable", False)),
            defensibility_assessment=str(data.get("defensibility_assessment") or "").strip(),
            transferability_assessment=str(
                data.get("transferability_assessment") or ""
            ).strip(),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel strategy data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_strategy_sub_agents(
        self,
        sector: str,
        company: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel strategy data collection.

        Per §4.4, Agent 14:
        - Sub-agent A: Find Porter's Five Forces data for [industry] (MICRO, SearxNG + Jina)
        - Sub-agent B: Find competitor strategic moves in [space] (MICRO, SearxNG)
        - Sub-agent C: Find VRIO-relevant resources for [company] (FAST, SearxNG + Obscura)
        """
        sub_specs = [
            SubAgentSpec(
                question=f"Find Porter's Five Forces data for {sector} industry, barriers to entry, supplier power, buyer power, substitutes, rivalry intensity",
                parent_agent=self.name,
                model_tier=ModelTier.STANDARD,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=600,
                context={"sector": sector},
            ),
            SubAgentSpec(
                question=f"Find competitor strategic moves in {sector}, recent announcements, M&A, product launches, pricing changes, market entry/exit",
                parent_agent=self.name,
                model_tier=ModelTier.STANDARD,
                tools=[ToolName.SEARXNG],
                findings_model="KeyFinding",
                timeout_seconds=600,
                context={"sector": sector},
            ),
            SubAgentSpec(
                question=f"Find VRIO-relevant resources for {company or sector}, brand value, patents, proprietary technology, distribution networks, key partnerships, unique capabilities",
                parent_agent=self.name,
                model_tier=ModelTier.STANDARD,
                tools=[ToolName.SEARXNG, ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=600,
                context={"company": company, "sector": sector},
            ),
        ]

        all_findings: list[KeyFinding] = []

        results = await asyncio.gather(
            *(self._spawn_sub_agent(spec) for spec in sub_specs),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, list):
                all_findings.extend(result)

        return all_findings

    # ─────────────────────────────────────────────────────────────────────
    # Confidence calibration
    # ─────────────────────────────────────────────────────────────────────

    def _calibrate_confidence(
        self,
        has_porter: bool,
        has_vrio: bool,
        has_swot: bool,
        has_option_grid: bool,
        has_game_theory: bool,
        option_count: int,
        sources_count: int,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: Porter's with rationale per force, VRIO with 3+ resources, SWOT/TOWS,
              3+ strategic options scored, game theory, 5+ sources
        MEDIUM: Porter's + SWOT/TOWS + 2+ options
        LOW: Missing core analysis
        """
        if (has_porter and has_vrio and has_swot and has_option_grid
                and has_game_theory and option_count >= 3 and sources_count >= 5):
            return ConfidenceLevel.HIGH
        if has_porter and has_swot and option_count >= 2:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # Main execution, the 8-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> StrategyAnalysis:
        """Execute the Strategy Analyst's 8-step methodology.

        Steps (§4.4, Agent 14):
        1. Search for strategic context (SearxNG + Jina)
        2. Run Porter's Five Forces
        3. Run VRIO on company resources
        4. Build SWOT → TOWS matrix
        5. Generate 3-5 strategic options
        6. Score options on strategic option grid
        7. Run game theory analysis on competitive dynamics
        8. Produce StrategyAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting strategy analysis: {self._question[:80]}",
        )

        # Extract context
        # Four-tier resolution ending at the user's question, so the subject
        # is always the thing the user actually asked about.
        sector = resolve_subject(
            self._context, "sector", "industry", question=self._question
        )
        company = self._context.get("company", "")

        # Step 1: Search for strategic context
        await self._transition(AgentState.WORKING, f"Step 1: Searching for strategic context in {sector}")
        self._search_results = await self._search_strategic_context(sector, company)

        # Framework selection, the critical differentiator
        await self._transition(AgentState.WORKING, "Selecting frameworks based on the specific "
            "question")
        self._frameworks_selected, self._frameworks_not_selected = await self._select_frameworks(
            self._question, self._context,
        )

        # Spawn sub-agents for parallel data collection
        sub_findings: list[KeyFinding] = []
        if sector or company:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning strategy data "
                "collection sub-agents")
            sub_findings = await self._spawn_strategy_sub_agents(sector, company)
        await self._ingest_sub_findings(sub_findings)
        if sector or company:
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with "
                "analysis")

        # P3.3: Zero-evidence gate
        if await self._check_zero_evidence(f"no strategic context for {sector}"):
            return StrategyAnalysis(
                confidence=ConfidenceLevel.LOW,
                sources=[],
            )

        # Step 2: Run Porter's Five Forces
        await self._transition(AgentState.WORKING, "Step 2: Running Porter's Five Forces analysis")
        porter = await self._run_porter_five_forces(self._question, self._search_results, self._context)

        # Step 3: Run VRIO on company resources
        await self._transition(AgentState.WORKING, "Step 3: Running VRIO analysis on company "
            "resources")
        vrio = await self._run_vrio(self._question, self._search_results, self._context)

        # Step 4: Build SWOT → TOWS matrix
        await self._transition(AgentState.WORKING, "Step 4: Building SWOT → TOWS matrix")
        swot_tows = await self._build_swot_tows(self._question, self._search_results, porter, vrio, self._context)

        # Steps 5-6: Generate strategic options + score on grid
        await self._transition(AgentState.WORKING, "Steps 5-6: Generating and scoring strategic "
            "options")
        option_grid = await self._generate_and_score_options(
            self._question, porter, vrio, swot_tows, self._context,
        )

        # Step 7: Run game theory analysis
        await self._transition(AgentState.WORKING, "Step 7: Running game theory analysis on "
            "competitive dynamics")
        game_theory = await self._run_game_theory(self._question, porter, self._search_results, self._context)

        # Step 7b: Conditional frameworks, run ONLY when _select_frameworks
        # actually chose them (Phase 5.1e). Previously these three were
        # hardcoded to None, so the selector could advertise analysis the agent
        # could not produce. Running them unconditionally would be the opposite
        # error: §4.4 is explicit that the Strategy Analyst "selects the
        # framework that actually illuminates the specific question" rather
        # than applying all eight.
        bcg: BCGMatrix | None = None
        if self._framework_selected(self._BCG_TOKENS):
            await self._transition(
                AgentState.WORKING, "Step 7b: Building BCG growth-share matrix (selected)"
            )
            bcg = await self._run_bcg_matrix(
                self._question, self._search_results, self._context
            )

        blue_ocean: BlueOceanStrategy | None = None
        if self._framework_selected(self._BLUE_OCEAN_TOKENS):
            await self._transition(
                AgentState.WORKING, "Step 7b: Running Blue Ocean analysis (selected)"
            )
            blue_ocean = await self._run_blue_ocean(
                self._question, self._search_results, self._context
            )

        core_competence: CoreCompetence | None = None
        if self._framework_selected(self._CORE_COMPETENCE_TOKENS):
            await self._transition(
                AgentState.WORKING, "Step 7b: Running core competence analysis (selected)"
            )
            core_competence = await self._run_core_competence(
                self._question, vrio, self._context
            )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            has_porter=bool(porter.overall_attractiveness),
            has_vrio=bool(vrio.resources),
            has_swot=bool(swot_tows.tows_strategies),
            has_option_grid=bool(option_grid.options),
            has_game_theory=bool(game_theory.game_type),
            option_count=len(option_grid.options),
            sources_count=len(self._sources),
        )

        # Step 8: Produce StrategyAnalysis model
        await self._transition(AgentState.WORKING, "Step 8: Producing StrategyAnalysis model")

        recommended = option_grid.recommended_option if option_grid.recommended_option else ""

        analysis = StrategyAnalysis(
            frameworks_selected=self._frameworks_selected,
            frameworks_not_selected=self._frameworks_not_selected,
            porter_five_forces=porter,
            bcg_matrix=bcg,  # Populated when a portfolio framework was selected
            swot_tows=swot_tows,
            blue_ocean=blue_ocean,  # Populated when market creation was selected
            vrio_assessment=vrio,
            core_competence=core_competence,  # Derived from VRIO when selected
            strategic_option_grid=option_grid,
            game_theory=game_theory,
            recommended_strategy=recommended,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus
        # Publish Porter's Five Forces as a finding
        if porter.overall_attractiveness:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="porter_five_forces",
                title=f"Industry Attractiveness: {porter.overall_attractiveness[:80]}",
                content=(
                    f"Rivalry: {porter.competitive_rivalry.value}. "
                    f"New entrants: {porter.threat_of_new_entrants.value}. "
                    f"Suppliers: {porter.bargaining_power_suppliers.value}. "
                    f"Buyers: {porter.bargaining_power_buyers.value}. "
                    f"Substitutes: {porter.threat_of_substitutes.value}. "
                    f"Overall: {porter.overall_attractiveness}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish VRIO sustained advantages as a finding
        if vrio.sustained_advantages:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="sustained_advantage",
                title=f"Sustained Competitive Advantages: {', '
                    ''.join(vrio.sustained_advantages[:3])}",
                content=(
                    f"Resources providing sustained competitive advantage: "
                    f"{', '.join(vrio.sustained_advantages)}. "
                    f"These are Valuable, Rare, Inimitable, and Organized."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish recommended strategy as a finding
        if recommended:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="recommended_strategy",
                title=f"Recommended Strategy: {recommended[:80]}",
                content=(
                    f"Recommended: {recommended}. "
                    f"Rationale: {option_grid.rationale}. "
                    f"Game theory: {game_theory.nash_equilibrium if game_theory.nash_equilibrium else 'N/A'}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish game theory as a finding
        if game_theory.game_type:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="game_theory",
                title=f"Game Theory: {game_theory.game_type}",
                content=(
                    f"Game type: {game_theory.game_type}. "
                    f"Players: {', '.join(game_theory.players[:3])}. "
                    f"Dominant strategy: {game_theory.dominant_strategy}. "
                    f"Nash equilibrium: {game_theory.nash_equilibrium}. "
                    f"Implications: {game_theory.implications}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish the full StrategyAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "strategy_analysis": analysis.model_dump(),
                "frameworks_selected": self._frameworks_selected,
                "frameworks_not_selected": self._frameworks_not_selected,
                "has_porter": porter is not None and bool(porter.overall_attractiveness),
                "has_vrio": vrio is not None and bool(vrio.resources),
                "has_swot_tows": swot_tows is not None and bool(swot_tows.tows_strategies),
                "has_option_grid": option_grid is not None and bool(option_grid.options),
                "has_game_theory": game_theory is not None and bool(game_theory.game_type),
                "recommended_strategy": recommended,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Strategy analysis complete: "
            f"porter={'yes' if porter.overall_attractiveness else 'no'}, "
            f"vrio={'yes' if vrio.resources else 'no'}, "
            f"swot_tows={'yes' if swot_tows.tows_strategies else 'no'}, "
            f"options={len(option_grid.options) if option_grid.options else 0}, "
            f"game_theory={'yes' if game_theory.game_type else 'no'}, "
            f"confidence={confidence.value}",
        )

        return analysis
