"""
HYPERION Sustainability Analyst — Agent 10, the ESG and sustainability specialist.

This is NOT a generic "calculate carbon footprint" agent. This is a specialist
with 5 proprietary analytical frameworks:

- ESG scoring: Score the company/strategy on ESG frameworks: MSCI ESG Ratings,
  SASB standards, TCFD recommendations, GRI standards. Identify which framework
  is most relevant for the stakeholder audience. Investors want TCFD, regulators
  want CSRD, customers want GRI.
- Carbon footprint: Calculate Scope 1 (direct), Scope 2 (purchased electricity),
  and Scope 3 (value chain) emissions. Identify the specific emission sources
  that account for 80% of the footprint and calculate the abatement cost for
  each. Not just 'energy use' — 'electricity from coal-fired grid power, 5000
  MWh/yr, 2500 tCO2e, abatement: switch to renewable PPA at $20/tCO2e, total
  cost $50K/yr.'
- Sustainability reporting: Map reporting requirements (CSRD, SEC climate, TCFD,
  CDP). Identify which reports are mandatory vs. voluntary and the penalty for
  non-compliance.
- Green financing: Evaluate green bonds, sustainability-linked loans, and carbon
  credit opportunities. Calculate potential financing cost savings — not just
  'green bonds exist' but 'a $50M green bond at 3.5% vs. 4.5% conventional saves
  $500K/yr in interest.'
- Circular economy: Assess opportunities for circular economy models (reduce,
  reuse, recycle, refurbish) in the business model. Each opportunity has a $
  value and implementation cost.

It doesn't just calculate a carbon number — it identifies the specific emission
sources that account for 80% of the footprint and calculates the abatement cost
for each. It maps ESG to financial impact (green financing savings, regulatory
penalty avoidance, investor access) not just to compliance. It always identifies
which ESG framework matters for the specific stakeholder (investors want TCFD,
regulators want CSRD, customers want GRI). (§4.4, Agent 10)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura, FRED
Sub-agents: Max 3 — ESG ratings, carbon emission data, sustainability regulations
Output: SustainabilityAnalysis (ESG scores, carbon footprint with abatement costs,
        reporting requirements, green financing opportunities, circular economy,
        total savings, financial impact summary, confidence, sources)

Methodology (§4.4, Agent 10):
1. Search for ESG data and ratings (SearxNG + Jina)
2. Scrape ESG rating platforms (Obscura)
3. Pull environmental economic data (FRED)
4. Score on relevant ESG framework
5. Calculate carbon footprint (Scope 1/2/3)
6. Map reporting requirements
7. Identify green financing opportunities
8. Produce SustainabilityAnalysis model
"""

from __future__ import annotations

import asyncio
import json
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
    CarbonFootprint,
    CircularEconomyAssessment,
    ConfidenceLevel,
    EmissionSource,
    ESGFramework,
    ESGScore,
    GreenFinancingOpportunity,
    KeyFinding,
    ReportingRequirement,
    Source,
    SourceCredibility,
    SustainabilityAnalysis,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


SUSTAINABILITY_ANALYST_SPEC = AgentSpec(
    name=AgentName.SUSTAINABILITY_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Sustainability Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.FRED,
        ToolName.WORLD_BANK,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="ESG scoring",
            description=(
                "Score the company/strategy on ESG frameworks: MSCI ESG Ratings, "
                "SASB standards, TCFD recommendations, GRI standards, CSRD, CDP. "
                "Identify which framework is most relevant for the stakeholder "
                "audience — investors want TCFD, regulators want CSRD, customers "
                "want GRI. Each score includes key strengths, weaknesses, and "
                "whether the framework is mandatory."
            ),
            inputs=["company", "sector", "stakeholder_audience", "esg_data"],
            outputs=["esg_scores", "framework_relevance", "strengths", "weaknesses", "most_relevant_framework"],
        ),
        SkillSpec(
            name="Carbon footprint",
            description=(
                "Calculate Scope 1 (direct emissions — combustion, fleet), Scope 2 "
                "(purchased electricity, heat, steam), and Scope 3 (value chain — "
                "suppliers, product use, end-of-life) emissions. Identify the "
                "specific emission sources that account for 80% of the footprint "
                "and calculate the abatement cost for each. Not just 'energy use' "
                "— 'electricity from coal-fired grid power, 5000 MWh/yr, 2500 "
                "tCO2e, abatement: switch to renewable PPA at $20/tCO2e, total "
                "cost $50K/yr.'"
            ),
            inputs=["energy_consumption", "fuel_usage", "supply_chain_data", "operations_data"],
            outputs=["scope1_emissions", "scope2_emissions", "scope3_emissions", "top_80_sources", "abatement_costs"],
        ),
        SkillSpec(
            name="Sustainability reporting",
            description=(
                "Map reporting requirements (CSRD, SEC climate disclosure, TCFD, "
                "CDP). Identify which reports are mandatory vs. voluntary, the "
                "key disclosure requirements, penalties for non-compliance, and "
                "estimated compliance cost. Each requirement has a deadline and "
                "jurisdiction."
            ),
            inputs=["jurisdictions", "company_size", "listing_status", "sector"],
            outputs=["mandatory_reports", "voluntary_reports", "deadlines", "penalties", "compliance_costs"],
        ),
        SkillSpec(
            name="Green financing",
            description=(
                "Evaluate green bonds, sustainability-linked loans, and carbon "
                "credit opportunities. Calculate potential financing cost savings "
                "— not just 'green bonds exist' but 'a $50M green bond at 3.5% vs. "
                "4.5% conventional saves $500K/yr in interest.' Each opportunity "
                "has eligibility criteria and estimated savings. Uses FRED data "
                "for green bond rates and clean energy investment trends."
            ),
            inputs=["financing_needs", "credit_profile", "green_projects", "fred_data"],
            outputs=["green_bond_opportunities", "sustainability_linked_loans", "carbon_credits", "annual_savings", "eligibility_criteria"],
        ),
        SkillSpec(
            name="Circular economy",
            description=(
                "Assess opportunities for circular economy models (reduce, reuse, "
                "recycle, refurbish) in the business model. Each opportunity has "
                "a $ value (cost savings + new revenue), implementation cost, ROI, "
                "and feasibility rating. Not just 'recycle more' — 'remanufacturing "
                "returns saves $2M/yr in raw material costs, implementation cost "
                "$500K, ROI = 300%, feasibility: high.'"
            ),
            inputs=["business_model", "waste_streams", "product_lifecycle", "material_costs"],
            outputs=["circular_opportunities", "implementation_costs", "annual_value", "roi", "feasibility"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Sustainability Analyst — the specialist who "
        "assesses ESG performance, calculates carbon footprint, evaluates "
        "sustainability strategy, and maps ESG reporting requirements.\n\n"
        "Your proprietary frameworks:\n"
        "1. ESG scoring: MSCI ESG, SASB, TCFD, GRI, CSRD, CDP. Identify which "
        "framework matters for the STAKEHOLDER — investors want TCFD, regulators "
        "want CSRD, customers want GRI. Each score has strengths, weaknesses, "
        "and mandatory status.\n"
        "2. Carbon footprint: Scope 1 (direct), Scope 2 (purchased electricity), "
        "Scope 3 (value chain). Identify the SPECIFIC sources that account for "
        "80% of the footprint and calculate abatement cost for EACH. Not just "
        "'energy use' — 'coal-fired grid power, 5000 MWh/yr, 2500 tCO2e, "
        "abatement: renewable PPA at $20/tCO2e, total $50K/yr.'\n"
        "3. Sustainability reporting: CSRD, SEC climate, TCFD, CDP. Mandatory vs. "
        "voluntary. Penalties for non-compliance. Compliance cost estimates.\n"
        "4. Green financing: Green bonds, sustainability-linked loans, carbon "
        "credits. Calculate $ savings — not 'green bonds exist' but '$50M green "
        "bond at 3.5% vs 4.5% conventional saves $500K/yr.' Use FRED data for "
        "green bond rates and clean energy investment trends.\n"
        "5. Circular economy: Reduce, reuse, recycle, refurbish. Each opportunity "
        "has $ value, implementation cost, ROI, and feasibility.\n\n"
        "Rules:\n"
        "- DON'T JUST CALCULATE A CARBON NUMBER — IDENTIFY THE SPECIFIC SOURCES "
        "THAT ACCOUNT FOR 80% OF THE FOOTPRINT AND CALCULATE ABATEMENT COST FOR "
        "EACH.\n"
        "- MAP ESG TO FINANCIAL IMPACT: green financing savings, regulatory "
        "penalty avoidance, investor access. Not just compliance.\n"
        "- ALWAYS IDENTIFY WHICH ESG FRAMEWORK MATTERS FOR THE SPECIFIC "
        "STAKEHOLDER. Investors want TCFD, regulators want CSRD, customers want "
        "GRI.\n"
        "- Green financing must have $ savings calculated, not just 'opportunities "
        "exist.'\n"
        "- Circular economy must have $ value AND implementation cost AND ROI.\n"
        "- Abatement costs must be per-tonne AND total.\n\n"
        "You can spawn up to 3 sub-agents for parallel ESG data collection:\n"
        "- Sub-agent A: Find ESG ratings for [company/sector] (MICRO, SearxNG + Jina)\n"
        "- Sub-agent B: Find carbon emission data for [industry] (MICRO, SearxNG)\n"
        "- Sub-agent C: Find sustainability regulations for [jurisdiction] (FAST, SearxNG + Obscura)\n\n"
        "Your output is a SustainabilityAnalysis Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned when the question involves ESG assessment, carbon "
                     "footprint, sustainability strategy, green financing, or "
                     "circular economy (SUSTAINABILITY_ANALYSIS, ESG, CARBON, "
                     "GREEN_FINANCING types)",
    max_sub_agents=3,
    output_model="SustainabilityAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Sustainability Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class SustainabilityAnalyst(BaseAgent):
    """Agent 10: The ESG and sustainability specialist.

    Assesses ESG performance across frameworks (MSCI, SASB, TCFD, GRI, CSRD,
    CDP), calculates carbon footprint by scope with abatement costs for the
    top 80% of emission sources, maps sustainability reporting requirements,
    evaluates green financing opportunities with $ savings, and assesses
    circular economy models. Maps ESG to financial impact, not just compliance.
    Always identifies which framework matters for the specific stakeholder.
    (§4.4, Agent 10)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for ESG data and ratings (SearxNG + Jina)
    3. Scrapes ESG rating platforms (Obscura)
    4. Pulls environmental economic data (FRED)
    5. Scores on relevant ESG framework, calculates carbon footprint
    6. Maps reporting requirements, identifies green financing + circular economy
    7. Produces SustainabilityAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or SUSTAINABILITY_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._esg_platform_data: list[dict[str, Any]] = []
        self._fred_data: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Sustainability Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting ESG scores for investor presentation)
        - FINDINGS: receives findings from other agents that may inform
          sustainability analysis (e.g., Regulatory Analyst's environmental
          regulations, Operations Analyst's supply chain data)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "sustainability_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Regulatory Analyst's environmental regulations inform reporting requirements
                if finding.finding_type == "environmental_regulation":
                    self._context.setdefault("environmental_regs", []).append(finding.content)
                # Operations Analyst's supply chain data informs Scope 3 emissions
                elif finding.finding_type == "supply_chain":
                    self._context.setdefault("supply_chain_data", []).append(finding.content)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "esg_scores":
                # Strategy Analyst requesting ESG scores for investor presentation
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for ESG data and ratings (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_esg_data(self, company: str, sector: str) -> list[dict[str, Any]]:
        """Search for ESG ratings, sustainability reports, climate data, and
        environmental regulations.

        Uses SearxNG to find: ESG ratings, sustainability reports, climate data,
        environmental regulations. Uses Jina to extract sustainability reports,
        ESG ratings, and environmental impact assessments.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{company} ESG rating MSCI Sustainalytics",
                f"{sector} ESG performance benchmarks",
                f"{sector} carbon footprint emissions data",
                f"{sector} sustainability report best practices",
                f"{company} sustainability report carbon emissions",
                f"{sector} Scope 1 Scope 2 Scope 3 emissions",
                f"{sector} green bond sustainability-linked loan rates",
                f"{sector} circular economy opportunities",
                f"{sector} CSRD TCFD CDP reporting requirements",
            ]

            for pattern in query_patterns[:12]:
                search_results = await searxng.search(pattern, max_results=6)
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

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Scrape ESG rating platforms (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_esg_platforms(self, company: str, sector: str) -> list[dict[str, Any]]:
        """Scrape JS-rendered ESG rating platforms (MSCI, Sustainalytics),
        carbon calculators, and sustainability databases.

        Uses Obscura to scrape: MSCI ESG ratings, Sustainalytics, CDP,
        carbon calculators, and sustainability databases that require JS
        rendering.
        """
        results: list[dict[str, Any]] = []

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            # ESG rating platforms and databases
            platform_urls = [
                f"https://www.msci.com/our-solutions/esg-investing/esg-ratings-climate-search-tool/issuer?id={company}",
                "https://www.sustainalytics.com/esg-rating",
                "https://www.cdp.net/en/responses",
                f"https://www.gri.org/report-search?sector={sector}",
            ]

            for url in platform_urls[:6]:
                try:
                    fetch_result = await obscura.fetch(url, stealth=True)
                    if fetch_result and (fetch_result.markdown or fetch_result.content):
                        page_data = {"content": (fetch_result.markdown or fetch_result.content)[:15000]}
                    else:
                        page_data = None
                    if page_data:
                        results.append({
                            "url": url,
                            "data": page_data,
                        })
                        self._sources.append(Source(
                            id=f"src_{len(self._sources):03d}",
                            title=f"ESG platform — {url.split('/')[2]}",
                            url=url,
                            credibility=SourceCredibility.INDUSTRY_REPORT,
                            key_data=f"ESG rating data from {url}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Pull environmental economic data (FRED)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_fred_data(self) -> list[dict[str, Any]]:
        """Pull environmental economic data from FRED.

        Uses FRED to pull: carbon prices, green bond rates, clean energy
        investment trends, and environmental economic indicators. This data
        informs green financing calculations and abatement cost benchmarks.
        """
        results: list[dict[str, Any]] = []

        try:
            fred = self.get_tool(ToolName.FRED)

            # Environmental economic data series
            series_ids = [
                "ECOCEM",       # Carbon emissions data
                "GREENBOND",    # Green bond indices (if available)
                "ENERGYPRICES", # Energy prices for abatement cost calculations
            ]

            for series_id in series_ids:
                try:
                    data = await fred.get_series(series_id)
                    if data:
                        results.append({
                            "series_id": series_id,
                            "data": data,
                        })
                        self._sources.append(Source(
                            id=f"src_{len(self._sources):03d}",
                            title=f"FRED — {series_id}",
                            url=f"https://fred.stlouisfed.org/series/{series_id}",
                            credibility=SourceCredibility.GOVERNMENT,
                            key_data=f"FRED economic data: {series_id}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Score on relevant ESG framework
    # ─────────────────────────────────────────────────────────────────────

    async def _score_esg(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        esg_platform_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[list[ESGScore], str]:
        """Score the company/strategy on relevant ESG frameworks.

        Identifies which framework is most relevant for the stakeholder
        audience. Investors want TCFD, regulators want CSRD, customers want
        GRI.

        Returns (esg_scores, most_relevant_framework).
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:10]
        )
        platform_summary = json.dumps(
            [{"url": d.get("url", ""), "data": str(d.get("data", ""))[:300]} for d in esg_platform_data[:3]],
            default=str,
        )[:800]

        stakeholder = context.get("stakeholder_audience", "investors")

        prompt = (
            "You are the HYPERION Sustainability Analyst scoring ESG performance.\n\n"
            f"Question: {question}\n\n"
            f"Stakeholder audience: {stakeholder}\n\n"
            f"ESG search results:\n{search_summary}\n\n"
            f"ESG platform data:\n{platform_summary}\n\n"
            "Score the company/strategy on ALL relevant ESG frameworks:\n"
            "1. MSCI ESG Ratings — investor-focused, letter grade (AAA-CCC)\n"
            "2. SASB standards — industry-specific, materiality-based\n"
            "3. TCFD recommendations — climate risk disclosure, investor-focused\n"
            "4. GRI standards — stakeholder-focused, comprehensive sustainability\n"
            "5. CSRD — EU mandatory corporate sustainability reporting\n"
            "6. CDP — carbon disclosure, investor/environmental-focused\n\n"
            "For each framework:\n"
            "- framework: which framework\n"
            "- score: the rating/score\n"
            "- key_strengths: 2-3 specific ESG strengths\n"
            "- key_weaknesses: 2-3 specific ESG weaknesses\n"
            "- stakeholder_audience: who cares about this framework\n"
            "- is_mandatory: is this mandatory for the business?\n\n"
            f"Identify which framework is MOST RELEVANT for the {stakeholder} audience.\n"
            "Investors want TCFD, regulators want CSRD, customers want GRI.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "esg_scores": [{\n'
            '    "framework": "msci_esg|sasb|tcfd|gri|csrd|cdp",\n'
            '    "score": "...",\n'
            '    "key_strengths": ["..."],\n'
            '    "key_weaknesses": ["..."],\n'
            '    "stakeholder_audience": "...",\n'
            '    "is_mandatory": true|false\n'
            '  }],\n'
            '  "most_relevant_framework": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        esg_scores: list[ESGScore] = []
        most_relevant = ""

        if not response.success or not response.content:
            return (esg_scores, most_relevant)

        try:
            data = json.loads(response.content)

            framework_map = {
                "msci_esg": ESGFramework.MSCI_ESG,
                "sasb": ESGFramework.SASB,
                "tcfd": ESGFramework.TCFD,
                "gri": ESGFramework.GRI,
                "csrd": ESGFramework.CSRD,
                "cdp": ESGFramework.CDP,
            }

            for score in data.get("esg_scores", []):
                fw_str = score.get("framework", "gri")
                fw = framework_map.get(fw_str, ESGFramework.GRI)

                esg_scores.append(ESGScore(
                    framework=fw,
                    score=score.get("score", "Unknown"),
                    key_strengths=score.get("key_strengths", []),
                    key_weaknesses=score.get("key_weaknesses", []),
                    stakeholder_audience=score.get("stakeholder_audience", ""),
                    is_mandatory=bool(score.get("is_mandatory", False)),
                ))

            most_relevant = data.get("most_relevant_framework", "")

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (esg_scores, most_relevant)

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Calculate carbon footprint (Scope 1/2/3)
    # ─────────────────────────────────────────────────────────────────────

    async def _calculate_carbon_footprint(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        fred_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> CarbonFootprint:
        """Calculate carbon footprint broken down by Scope 1/2/3.

        Identifies the specific emission sources that account for 80% of the
        footprint and calculates the abatement cost for each. Not just 'energy
        use' — 'electricity from coal-fired grid power, 5000 MWh/yr, 2500
        tCO2e, abatement: switch to renewable PPA at $20/tCO2e, total $50K/yr.'
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )
        fred_summary = json.dumps(
            [{"series": d.get("series_id", ""), "data": str(d.get("data", ""))[:200]} for d in fred_data[:3]],
            default=str,
        )[:600]

        prompt = (
            "You are the HYPERION Sustainability Analyst calculating carbon footprint.\n\n"
            f"Question: {question}\n\n"
            f"ESG/climate search results:\n{search_summary}\n\n"
            f"FRED environmental economic data:\n{fred_summary or 'No FRED data available'}\n\n"
            "Calculate the carbon footprint by scope:\n"
            "- Scope 1: Direct emissions (combustion, fleet, process emissions)\n"
            "- Scope 2: Purchased electricity, heat, steam\n"
            "- Scope 3: Value chain (suppliers, product use, end-of-life, business travel)\n\n"
            "Identify the SPECIFIC emission sources that account for 80% of the footprint.\n"
            "For each source, calculate the abatement cost:\n"
            "- source_name: specific source (not 'energy use' but 'coal-fired grid power')\n"
            "- scope: Scope 1, 2, or 3\n"
            "- emissions_tco2e: annual emissions in tonnes CO2e\n"
            "- percentage_of_total: % of total footprint\n"
            "- abatement_action: specific action to reduce (not 'use less energy')\n"
            "- abatement_cost_per_tco2e: cost per tonne to abate ($)\n"
            "- total_abatement_cost: total annual cost ($)\n"
            "- is_top_80: is this in the top 80% of sources?\n\n"
            "Example: 'electricity from coal-fired grid power, 5000 MWh/yr, 2500 tCO2e, "
            "abatement: switch to renewable PPA at $20/tCO2e, total cost $50K/yr'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "scope1_tco2e": "...",\n'
            '  "scope2_tco2e": "...",\n'
            '  "scope3_tco2e": "...",\n'
            '  "total_tco2e": "...",\n'
            '  "emission_sources": [{...}],\n'
            '  "total_abatement_cost": "$.../yr"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return CarbonFootprint()

        try:
            data = json.loads(response.content)

            emission_sources: list[EmissionSource] = []
            for src in data.get("emission_sources", []):
                emission_sources.append(EmissionSource(
                    source_name=src.get("source_name", "Unknown"),
                    scope=src.get("scope", "Scope 2"),
                    emissions_tco2e=src.get("emissions_tco2e", "Unknown"),
                    percentage_of_total=src.get("percentage_of_total", ""),
                    abatement_action=src.get("abatement_action", ""),
                    abatement_cost_per_tco2e=src.get("abatement_cost_per_tco2e", ""),
                    total_abatement_cost=src.get("total_abatement_cost", ""),
                    is_top_80=bool(src.get("is_top_80", False)),
                ))

            # Identify top 80% sources
            top_80 = [s for s in emission_sources if s.is_top_80]

            return CarbonFootprint(
                scope1_tco2e=data.get("scope1_tco2e", ""),
                scope2_tco2e=data.get("scope2_tco2e", ""),
                scope3_tco2e=data.get("scope3_tco2e", ""),
                total_tco2e=data.get("total_tco2e", ""),
                emission_sources=emission_sources,
                top_80_sources=top_80,
                total_abatement_cost=data.get("total_abatement_cost", ""),
            )

        except (json.JSONDecodeError, ValueError, TypeError):
            return CarbonFootprint()

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Map reporting requirements
    # ─────────────────────────────────────────────────────────────────────

    async def _map_reporting_requirements(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        jurisdictions: list[str],
        context: dict[str, Any],
    ) -> list[ReportingRequirement]:
        """Map sustainability reporting requirements.

        Maps reporting requirements (CSRD, SEC climate, TCFD, CDP). Identifies
        which reports are mandatory vs. voluntary and the penalty for non-
        compliance.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )

        prompt = (
            "You are the HYPERION Sustainability Analyst mapping reporting requirements.\n\n"
            f"Question: {question}\n\n"
            f"Jurisdictions: {', '.join(jurisdictions)}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Map ALL sustainability reporting requirements:\n"
            "1. CSRD (EU Corporate Sustainability Reporting Directive)\n"
            "2. SEC Climate Disclosure (US)\n"
            "3. TCFD (Task Force on Climate-related Financial Disclosures)\n"
            "4. CDP (Carbon Disclosure Project)\n"
            "5. GRI (Global Reporting Initiative)\n"
            "6. SASB (Sustainability Accounting Standards Board)\n\n"
            "For each:\n"
            "- report_name: name of the report\n"
            "- jurisdiction: jurisdiction (EU, US, Global)\n"
            "- is_mandatory: is this mandatory?\n"
            "- deadline: reporting deadline/frequency\n"
            "- key_disclosures: what must be disclosed (list)\n"
            "- penalty_for_non_compliance: penalty for not reporting\n"
            "- estimated_compliance_cost: cost of compliance ($)\n\n"
            "Return JSON:\n"
            "{\n"
            '  "requirements": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        requirements: list[ReportingRequirement] = []

        if not response.success or not response.content:
            return requirements

        try:
            data = json.loads(response.content)
            for req in data.get("requirements", []):
                requirements.append(ReportingRequirement(
                    report_name=req.get("report_name", "Unknown"),
                    jurisdiction=req.get("jurisdiction", ""),
                    is_mandatory=bool(req.get("is_mandatory", False)),
                    deadline=req.get("deadline", ""),
                    key_disclosures=req.get("key_disclosures", []),
                    penalty_for_non_compliance=req.get("penalty_for_non_compliance", ""),
                    estimated_compliance_cost=req.get("estimated_compliance_cost", ""),
                ))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return requirements

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Identify green financing opportunities
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_green_financing(
        self,
        question: str,
        fred_data: list[dict[str, Any]],
        carbon_footprint: CarbonFootprint,
        context: dict[str, Any],
    ) -> tuple[list[GreenFinancingOpportunity], list[CircularEconomyAssessment], str, str]:
        """Identify green financing opportunities and circular economy models.

        Evaluates green bonds, sustainability-linked loans, and carbon credit
        opportunities. Calculates $ savings. Also assesses circular economy
        opportunities (reduce, reuse, recycle, refurbish).

        Returns (green_financing_opportunities, circular_economy_assessments,
                 total_green_savings, financial_impact_summary).
        """
        fred_summary = json.dumps(
            [{"series": d.get("series_id", ""), "data": str(d.get("data", ""))[:200]} for d in fred_data[:3]],
            default=str,
        )[:600]
        footprint_summary = (
            f"Total: {carbon_footprint.total_tco2e}, "
            f"Abatement cost: {carbon_footprint.total_abatement_cost}"
            if carbon_footprint.total_tco2e else "No carbon data available"
        )

        prompt = (
            "You are the HYPERION Sustainability Analyst identifying green financing and circular economy.\n\n"
            f"Question: {question}\n\n"
            f"FRED environmental economic data:\n{fred_summary or 'No FRED data available'}\n\n"
            f"Carbon footprint: {footprint_summary}\n\n"
            "Identify green financing opportunities:\n"
            "1. Green bonds — calculate $ savings vs conventional financing\n"
            "2. Sustainability-linked loans — interest rate reductions for ESG targets\n"
            "3. Carbon credits — revenue from carbon offset projects\n\n"
            "For each:\n"
            "- instrument: green bond, sustainability-linked loan, or carbon credits\n"
            "- description: description of the opportunity\n"
            "- estimated_amount: financing amount ($)\n"
            "- conventional_rate: conventional rate (%)\n"
            "- green_rate: green financing rate (%)\n"
            "- annual_savings: $ savings per year\n"
            "- eligibility_criteria: criteria to qualify (list)\n\n"
            "Also identify circular economy opportunities (reduce, reuse, recycle, refurbish):\n"
            "- opportunity: which circular model\n"
            "- description: how to implement\n"
            "- current_waste: what waste this addresses\n"
            "- implementation_cost: cost to implement ($)\n"
            "- annual_value: annual $ value (savings + revenue)\n"
            "- roi: ROI of implementing\n"
            "- feasibility: low, medium, high\n\n"
            "Calculate total green financing savings and a financial impact summary "
            "(green financing savings + regulatory penalty avoidance + investor access).\n\n"
            "Return JSON:\n"
            "{\n"
            '  "green_financing": [{...}],\n'
            '  "circular_economy": [{...}],\n'
            '  "total_green_savings": "$.../yr",\n'
            '  "financial_impact_summary": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        green_ops: list[GreenFinancingOpportunity] = []
        circular: list[CircularEconomyAssessment] = []
        total_savings = ""
        impact_summary = ""

        if not response.success or not response.content:
            return (green_ops, circular, total_savings, impact_summary)

        try:
            data = json.loads(response.content)

            for gf in data.get("green_financing", []):
                green_ops.append(GreenFinancingOpportunity(
                    instrument=gf.get("instrument", "green bond"),
                    description=gf.get("description", ""),
                    estimated_amount=gf.get("estimated_amount", ""),
                    conventional_rate=gf.get("conventional_rate", ""),
                    green_rate=gf.get("green_rate", ""),
                    annual_savings=gf.get("annual_savings", ""),
                    eligibility_criteria=gf.get("eligibility_criteria", []),
                    sources=self._sources[:2],
                ))

            for ce in data.get("circular_economy", []):
                circular.append(CircularEconomyAssessment(
                    opportunity=ce.get("opportunity", "reduce"),
                    description=ce.get("description", ""),
                    current_waste=ce.get("current_waste", ""),
                    implementation_cost=ce.get("implementation_cost", ""),
                    annual_value=ce.get("annual_value", ""),
                    roi=ce.get("roi", ""),
                    feasibility=ce.get("feasibility", "medium"),
                ))

            total_savings = data.get("total_green_savings", "")
            impact_summary = data.get("financial_impact_summary", "")

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (green_ops, circular, total_savings, impact_summary)

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel ESG data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_sustainability_sub_agents(
        self,
        company: str,
        sector: str,
        jurisdictions: list[str],
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel ESG data collection.

        Per §4.4, Agent 10:
        - Sub-agent A: Find ESG ratings for [company/sector] (MICRO, SearxNG + Jina)
        - Sub-agent B: Find carbon emission data for [industry] (MICRO, SearxNG)
        - Sub-agent C: Find sustainability regulations for [jurisdiction] (FAST, SearxNG + Obscura)
        """
        sub_specs = [
            SubAgentSpec(
                question=f"Find ESG ratings for {company or sector} — MSCI ESG, Sustainalytics, CDP scores, sustainability report ratings",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"company": company, "sector": sector},
            ),
            SubAgentSpec(
                question=f"Find carbon emission data for {sector} — Scope 1, 2, 3 emissions benchmarks, industry average carbon footprint, emission factors",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"sector": sector},
            ),
            SubAgentSpec(
                question=f"Find sustainability regulations for {', '.join(jurisdictions)} — CSRD, SEC climate disclosure, TCFD, CDP requirements, mandatory vs voluntary",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.SEARXNG, ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"jurisdictions": jurisdictions, "sector": sector},
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
        esg_score_count: int,
        has_carbon_footprint: bool,
        reporting_count: int,
        green_financing_count: int,
        circular_count: int,
        sources_count: int,
        has_fred_data: bool,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 3+ ESG scores, carbon footprint with abatement costs, 3+ reporting
              requirements, 2+ green financing opportunities, 2+ circular economy,
              5+ sources, FRED data
        MEDIUM: 2+ ESG scores, some carbon data, 2+ reporting requirements
        LOW: <2 ESG scores, missing core analysis
        """
        if (esg_score_count >= 3 and has_carbon_footprint
                and reporting_count >= 3 and green_financing_count >= 2
                and circular_count >= 2 and sources_count >= 5
                and has_fred_data):
            return ConfidenceLevel.HIGH
        if esg_score_count >= 2 and reporting_count >= 2:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 8-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> SustainabilityAnalysis:
        """Execute the Sustainability Analyst's 8-step methodology.

        Steps (§4.4, Agent 10):
        1. Search for ESG data and ratings (SearxNG + Jina)
        2. Scrape ESG rating platforms (Obscura)
        3. Pull environmental economic data (FRED)
        4. Score on relevant ESG framework
        5. Calculate carbon footprint (Scope 1/2/3)
        6. Map reporting requirements
        7. Identify green financing opportunities
        8. Produce SustainabilityAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus — specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting sustainability analysis: {self._question[:80]}",
        )

        # Extract context
        company = self._context.get("company", "")
        sector = self._context.get("sector", self._context.get("industry", ""))
        jurisdictions = self._context.get("jurisdictions", ["US", "EU"])

        # Spawn sub-agents for parallel ESG data collection
        if sector or company:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning ESG data collection sub-agents")
            sub_findings = await self._spawn_sustainability_sub_agents(company, sector, jurisdictions)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 1: Search for ESG data and ratings
        await self._transition(AgentState.WORKING, f"Step 1: Searching ESG data for {company or sector}")
        self._search_results = await self._search_esg_data(company, sector)

        # Step 2: Scrape ESG rating platforms
        await self._transition(AgentState.WORKING, "Step 2: Scraping ESG rating platforms (Obscura)")
        self._esg_platform_data = await self._scrape_esg_platforms(company, sector)

        # Step 3: Pull environmental economic data
        await self._transition(AgentState.WORKING, "Step 3: Pulling environmental economic data (FRED)")
        self._fred_data = await self._pull_fred_data()

        # Step 4: Score on relevant ESG framework
        await self._transition(AgentState.WORKING, "Step 4: Scoring on ESG frameworks (MSCI, SASB, TCFD, GRI, CSRD, CDP)")
        esg_scores, most_relevant_framework = await self._score_esg(
            self._question, self._search_results, self._esg_platform_data, self._context,
        )

        # Step 5: Calculate carbon footprint
        await self._transition(AgentState.WORKING, "Step 5: Calculating carbon footprint (Scope 1/2/3) with abatement costs")
        carbon_footprint = await self._calculate_carbon_footprint(
            self._question, self._search_results, self._fred_data, self._context,
        )

        # Step 6: Map reporting requirements
        await self._transition(AgentState.WORKING, "Step 6: Mapping sustainability reporting requirements")
        reporting_requirements = await self._map_reporting_requirements(
            self._question, self._search_results, jurisdictions, self._context,
        )

        # Step 7: Identify green financing + circular economy
        await self._transition(AgentState.WORKING, "Step 7: Identifying green financing opportunities and circular economy models")
        green_financing, circular_economy, total_savings, impact_summary = (
            await self._identify_green_financing(
                self._question, self._fred_data, carbon_footprint, self._context,
            )
        )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            esg_score_count=len(esg_scores),
            has_carbon_footprint=bool(carbon_footprint.total_tco2e),
            reporting_count=len(reporting_requirements),
            green_financing_count=len(green_financing),
            circular_count=len(circular_economy),
            sources_count=len(self._sources),
            has_fred_data=bool(self._fred_data),
        )

        # Step 8: Produce SustainabilityAnalysis model
        await self._transition(AgentState.WORKING, "Step 8: Producing SustainabilityAnalysis model")

        analysis = SustainabilityAnalysis(
            esg_scores=esg_scores,
            most_relevant_framework=most_relevant_framework,
            carbon_footprint=carbon_footprint,
            reporting_requirements=reporting_requirements,
            green_financing_opportunities=green_financing,
            circular_economy=circular_economy,
            total_green_financing_savings=total_savings,
            total_abatement_cost=carbon_footprint.total_abatement_cost,
            financial_impact_summary=impact_summary,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        # Publish most relevant framework as a finding
        if most_relevant_framework:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="esg_framework",
                title=f"Most Relevant ESG Framework: {most_relevant_framework}",
                content=(
                    f"For the {self._context.get('stakeholder_audience', 'stakeholder')} audience, "
                    f"{most_relevant_framework} is the most relevant ESG framework. "
                    f"Scored across {len(esg_scores)} frameworks total."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish top emission sources as findings
        if carbon_footprint.top_80_sources:
            for src in carbon_footprint.top_80_sources[:3]:
                finding = KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="emission_source",
                    title=f"Top Emission Source: {src.source_name} ({src.emissions_tco2e})",
                    content=(
                        f"{src.source_name} ({src.scope}): {src.emissions_tco2e} ({src.percentage_of_total}). "
                        f"Abatement: {src.abatement_action}. Cost: {src.abatement_cost_per_tco2e}/tCO2e. "
                        f"Total: {src.total_abatement_cost}."
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    sources=self._sources[:2],
                )
                await self._publish_finding(finding)

        # Publish green financing savings as a finding
        if total_savings:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="green_financing",
                title=f"Green Financing Savings: {total_savings}",
                content=(
                    f"Total annual green financing savings: {total_savings}. "
                    f"Opportunities: {len(green_financing)} instruments identified. "
                    f"Financial impact: {impact_summary[:200]}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:3],
            )
            await self._publish_finding(finding)

        # Publish the full SustainabilityAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "sustainability_analysis": analysis.model_dump(),
                "esg_score_count": len(esg_scores),
                "most_relevant_framework": most_relevant_framework,
                "total_emissions": carbon_footprint.total_tco2e,
                "total_abatement_cost": carbon_footprint.total_abatement_cost,
                "green_financing_count": len(green_financing),
                "circular_economy_count": len(circular_economy),
                "total_green_savings": total_savings,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Sustainability analysis complete: {len(esg_scores)} ESG scores, "
            f"carbon={carbon_footprint.total_tco2e or 'N/A'}, "
            f"{len(reporting_requirements)} reporting requirements, "
            f"{len(green_financing)} green financing ops, "
            f"{len(circular_economy)} circular economy ops, "
            f"savings={total_savings or 'N/A'}, confidence={confidence.value}",
        )

        return analysis
