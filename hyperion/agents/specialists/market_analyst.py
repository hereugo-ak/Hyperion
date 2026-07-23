"""
HYPERION Market Analyst — Agent 3, the market sizing specialist.

This is NOT a generic "research the market" agent. This is a specialist
with proprietary analytical frameworks that no other agent has:

- Top-down market sizing: Global SaaS → India SaaS → India Tier-2 SaaS
- Bottom-up market sizing: 50M businesses × 2% adoption × $500 ARPU = $500M
- CAGR triangulation: Cross-validate estimates from different sources
- Market maturity assessment: emerging/growing/mature/declining
- Growth driver decomposition: population × penetration × ARPU × new use cases
- Segment analysis: demographics, behavior, psychographics → entry point

It NEVER reports a single market size number. It always reports a range
with top-down, bottom-up, and triangulated estimates. It always cites
the source for each number. It always flags when market data is sparse.
It always segments before sizing — a market size without segmentation
is useless for strategy. (§4.4, Agent 3)

Model Tier: STANDARD (GPT OSS 120B on Groq primary, Nemotron 3 Nano 30B backup)
Tools: SearxNG, Jina, Obscura, Alpha Vantage, FRED
Sub-agents: Max 3 — TAM data, geography spending, adoption/penetration rates
Output: MarketAnalysis (TAM range, SAM, SOM, CAGR, segments, growth drivers,
        market maturity, confidence, sources)

Methodology (§4.4, Agent 3):
1. Search for existing market reports (SearxNG + Jina)
2. If no direct data, scrape interactive dashboards (Obscura)
3. Pull macroeconomic context (FRED)
4. Pull public company revenue data for market sizing (Alpha Vantage)
5. Apply top-down sizing
6. Apply bottom-up sizing
7. Cross-validate via CAGR triangulation
8. Segment the market
9. Identify growth drivers
10. Produce structured MarketAnalysis model
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
    ConfidenceLevel,
    FinancialMetric,
    KeyFinding,
    MarketAnalysis,
    Source,
    SourceCredibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


MARKET_ANALYST_SPEC = AgentSpec(
    name=AgentName.MARKET_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Market Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.ALPHA_VANTAGE,
        ToolName.FRED,
        ToolName.WORLD_BANK,
        ToolName.GOOGLE_TRENDS,
        ToolName.SEMANTIC_SCHOLAR,
        ToolName.REDDIT,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Top-down market sizing",
            description=(
                "Start with a large known market, apply filters (geography, segment, "
                "price point) to narrow to TAM. Example: Global SaaS market → India SaaS "
                "→ India Tier-2 SaaS. Each filter step must cite a source. The final "
                "TAM is only as credible as the weakest filter assumption."
            ),
            inputs=["global_market_size", "geography_filter", "segment_filter", "price_filter"],
            outputs=["tam_top_down", "filter_chain_with_sources"],
        ),
        SkillSpec(
            name="Bottom-up market sizing",
            description=(
                "Start with unit economics and customer count. Example: 50M Tier-2 "
                "businesses × 2% SaaS adoption × $500 ARPU = $500M. Each variable "
                "(customer count, adoption rate, ARPU) must have a source. Sensitivity "
                "analysis shows how TAM changes when each variable moves ±20%."
            ),
            inputs=["customer_count", "adoption_rate", "arpu", "geography_scope"],
            outputs=["tam_bottom_up", "variable_sources", "sensitivity_analysis"],
        ),
        SkillSpec(
            name="CAGR triangulation",
            description=(
                "Cross-validate market size estimates by calculating implied CAGR from "
                "different sources and checking for consistency. If Source A says the "
                "market is $2B growing to $5B in 5 years (20% CAGR) but Source B says "
                "the market is growing at 10% CAGR, there's a contradiction that must "
                "be flagged. Triangulated estimate weights sources by credibility."
            ),
            inputs=["market_size_estimates", "growth_rate_estimates", "source_credibility"],
            outputs=["cagr_triangulated", "consistency_check", "contradiction_flags"],
        ),
        SkillSpec(
            name="Market maturity assessment",
            description=(
                "Classify market as emerging/growing/mature/declining using indicators: "
                "penetration rate, growth rate, number of competitors, price compression. "
                "Emerging: <10% penetration, few competitors, high growth. Growing: "
                "10-30% penetration, increasing competitors, moderate growth. Mature: "
                ">30% penetration, many competitors, low growth, price compression. "
                "Declining: negative growth, consolidation."
            ),
            inputs=["penetration_rate", "growth_rate", "competitor_count", "price_trends"],
            outputs=["market_maturity_classification", "maturity_indicators", "maturity_rationale"],
        ),
        SkillSpec(
            name="Growth driver decomposition",
            description=(
                "Break market growth into components: population growth, penetration "
                "increase, ARPU expansion, new use cases. Total growth = sum of drivers. "
                "If population grows 2%, penetration grows 5%, ARPU grows 3%, and new "
                "use cases add 4%, total market growth ≈ 14%. Each driver must have a "
                "source and a contribution percentage."
            ),
            inputs=["market_size_history", "population_data", "penetration_data", "arpu_data"],
            outputs=["growth_driver_breakdown", "driver_contributions", "driver_sources"],
        ),
        SkillSpec(
            name="Segment analysis",
            description=(
                "Segment the market by demographics, behavior, and psychographics. "
                "Identify which segment is the most attractive entry point based on "
                "size, growth rate, competition intensity, and fit with the client's "
                "capabilities. A market size without segmentation is useless for "
                "strategy — 'the SaaS market is $500M' is less useful than 'the "
                "mid-market segment is $150M growing at 25% with only 3 competitors.'"
            ),
            inputs=["market_size", "demographic_data", "behavioral_data", "competitive_intensity"],
            outputs=["market_segments", "segment_attractiveness_scores", "recommended_entry_segment"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Market Analyst — the specialist who sizes markets, "
        "maps market structure, identifies growth drivers, and segments markets.\n\n"
        "You are the go-to agent for any 'how big is this opportunity' question.\n\n"
        "Your proprietary frameworks:\n"
        "1. Top-down sizing: Start large, filter down. Global → regional → segment → price point.\n"
        "2. Bottom-up sizing: Start small, build up. Customers × adoption × ARPU.\n"
        "3. CAGR triangulation: Cross-validate estimates from different sources.\n"
        "4. Market maturity: emerging/growing/mature/declining based on penetration, "
        "growth, competitors, price trends.\n"
        "5. Growth driver decomposition: Break growth into population, penetration, "
        "ARPU, and new use case components.\n"
        "6. Segment analysis: Segment by demographics, behavior, psychographics. "
        "Identify the most attractive entry segment.\n\n"
        "Rules:\n"
        "- NEVER report a single market size number. Always report a range with "
        "top-down, bottom-up, and triangulated estimates.\n"
        "- ALWAYS cite the source for each number. No unsourced numbers.\n"
        "- ALWAYS flag when market data is sparse or unreliable. Don't hide uncertainty.\n"
        "- ALWAYS segment before sizing. A market size without segmentation is useless.\n"
        "- If two sources disagree on market size, flag the contradiction and explain "
        "which source is more credible and why.\n"
        "- TAM is Total Addressable Market. SAM is Serviceable Addressable Market "
        "(what we can reach). SOM is Serviceable Obtainable Market (what we can capture).\n"
        "- CAGR must be calculated from the data, not guessed. If data doesn't support "
        "a CAGR calculation, say so.\n"
        "- Growth drivers must sum to total growth. If they don't, there's a missing "
        "driver or a calculation error.\n"
        "- The most attractive segment is not always the largest — it's the one with "
        "the best size × growth × low-competition × fit combination.\n\n"
        "You can spawn up to 3 sub-agents for parallel data collection:\n"
        "- Sub-agent A: Find TAM data for [specific market] (MICRO, SearxNG + Jina)\n"
        "- Sub-agent B: Find [geography] spending data (MICRO, SearxNG + Obscura)\n"
        "- Sub-agent C: Find adoption/penetration rates (FAST, Obscura + Jina)\n\n"
        "Your output is a MarketAnalysis Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned when the question involves market sizing, market entry, "
                     "or opportunity assessment (GO_NO_GO, MARKET_ENTRY, FORECAST types)",
    max_sub_agents=3,
    output_model="MarketAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Market Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class MarketAnalyst(BaseAgent):
    """Agent 3: The market sizing specialist.

    Sizes markets using top-down and bottom-up approaches, triangulates
    estimates, segments the market, identifies growth drivers, and
    classifies market maturity. NEVER reports a single number — always
    a range with sources. (§4.4, Agent 3)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for existing market reports (SearxNG + Jina)
    3. Scrapes interactive dashboards if needed (Obscura)
    4. Pulls macro context (FRED) and public company data (Alpha Vantage)
    5. Applies top-down and bottom-up sizing
    6. Cross-validates via CAGR triangulation
    7. Segments market and identifies growth drivers
    8. Produces MarketAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or MARKET_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data from tools
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._scraped_data: list[dict[str, Any]] = []
        self._macro_data: dict[str, Any] = {}
        self._public_company_data: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Market Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests from other agents (e.g., Financial
          Analyst requesting TAM number for DCF model)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "market_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "tam_number":
                # Financial Analyst is requesting our TAM for their DCF model
                # We respond with our triangulated TAM if available
                # This is handled during run() — just note the request
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for existing market reports (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_market_reports(self, market_query: str) -> list[dict[str, Any]]:
        """Search for existing market reports using SearxNG.

        This is the starting point — before doing original sizing, check
        what market reports already exist. SearxNG aggregates 70+ search
        engines, so this catches reports from Statista, Grand View Research,
        McKinsey, government data portals, etc.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)
            search_results = await searxng.search(
                f"{market_query} market size TAM report",
                max_results=15,
            )

            for r in search_results:
                results.append({
                    "title": r.get("title", ""),
                    "url": r.get("url", ""),
                    "snippet": r.get("content", ""),
                    "source": "searxng",
                })
                self._sources.append(Source(
                    id=f"src_{len(self._sources):03d}",
                    title=r.get("title", ""),
                    url=r.get("url", ""),
                    credibility=SourceCredibility.INDUSTRY_REPORT,
                ))

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    async def _extract_content_from_urls(
        self,
        urls: list[str],
    ) -> list[dict[str, Any]]:
        """Extract full content from market report URLs using Jina.

        SearxNG gives us URLs and snippets. Jina gives us the full text
        so we can extract actual market size numbers, growth rates, and
        segmentation data from the reports.
        """
        extracted: list[dict[str, Any]] = []

        try:
            jina = self.get_tool(ToolName.JINA)
            for url in urls[:10]:  # Limit to top 10 URLs to stay within rate limits
                read_result = await jina.read(url)
                if read_result and (read_result.markdown or read_result.content):
                    content = read_result.markdown or read_result.content
                else:
                    continue
                if content:
                    extracted.append({
                        "url": url,
                        "content": content[:15000],  # Truncate for context management
                        "source": "jina",
                    })
        except (ValueError, AttributeError, RuntimeError):
            pass

        return extracted

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Scrape interactive dashboards (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_dashboards(self, market_query: str) -> list[dict[str, Any]]:
        """Scrape JS-rendered market data dashboards using Obscura.

        Many market data sources (Statista, IBISWorld, government portals)
        render data via JavaScript. SearxNG and Jina can't access this data
        because it's loaded dynamically. Obscura uses stealth mode to
        render the page and extract the data. (§5.1)
        """
        scraped: list[dict[str, Any]] = []

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            # Search for interactive market data sites
            searxng = self.get_tool(ToolName.SEARXNG)
            dashboard_results = await searxng.search(
                f"{market_query} market data dashboard statista IBISWorld",
                max_results=5,
            )

            for r in dashboard_results:
                url = r.get("url", "")
                if not url:
                    continue
                fetch_result = await obscura.fetch(url, stealth=True)
                if fetch_result and (fetch_result.markdown or fetch_result.content):
                    page_data = {"content": (fetch_result.markdown or fetch_result.content)[:15000]}
                else:
                    page_data = None
                if page_data:
                    scraped.append({
                        "url": url,
                        "data": page_data,
                        "source": "obscura",
                    })
                    self._sources.append(Source(
                        id=f"src_{len(self._sources):03d}",
                        title=r.get("title", ""),
                        url=url,
                        credibility=SourceCredibility.INDUSTRY_REPORT,
                    ))

        except (ValueError, AttributeError, RuntimeError):
            pass

        return scraped

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Pull macroeconomic context (FRED)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_macro_context(self, geography: str) -> dict[str, Any]:
        """Pull macroeconomic indicators from FRED.

        GDP growth, sector spending, inflation, and interest rates drive
        market size. A market in a country with 7% GDP growth behaves
        differently than one in a country with 1% GDP growth. FRED provides
        the macro context that frames the market sizing. (§5.1)
        """
        macro: dict[str, Any] = {}

        try:
            fred = self.get_tool(ToolName.FRED)

            # Pull GDP growth for the relevant geography
            gdp_data = await fred.get_series("GDP")
            if gdp_data:
                macro["gdp_growth"] = gdp_data

            # Pull inflation data
            inflation_data = await fred.get_series("CPIAUCSL")
            if inflation_data:
                macro["inflation"] = inflation_data

            # Pull sector spending if available
            sector_spending = await fred.get_series("PCES")
            if sector_spending:
                macro["sector_spending"] = sector_spending

            self._sources.append(Source(
                id=f"src_{len(self._sources):03d}",
                title=f"FRED Macroeconomic Data — {geography}",
                url="https://fred.stlouisfed.org",
                credibility=SourceCredibility.GOVERNMENT,
                key_data=f"GDP growth, inflation, sector spending for {geography}",
            ))

        except (ValueError, AttributeError, RuntimeError):
            pass

        return macro

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Pull public company revenue data (Alpha Vantage)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_public_company_data(self, tickers: list[str]) -> list[dict[str, Any]]:
        """Pull revenue data for publicly traded companies in the space.

        Public company revenue is a proxy for market size. If the top 5
        public companies in a space generate $3B combined revenue and
        they have ~60% market share, the total market is ~$5B. This
        cross-validates top-down and bottom-up estimates. (§5.1)
        """
        company_data: list[dict[str, Any]] = []

        try:
            av = self.get_tool(ToolName.ALPHA_VANTAGE)

            for ticker in tickers[:5]:  # Limit to 5 companies (API rate limits)
                overview = await av.get_overview(ticker)
                if overview:
                    company_data.append({
                        "ticker": ticker,
                        "name": overview.get("Name", ""),
                        "revenue": overview.get("RevenueTTM", ""),
                        "market_cap": overview.get("MarketCapitalization", ""),
                        "sector": overview.get("Sector", ""),
                        "industry": overview.get("Industry", ""),
                    })
                    self._sources.append(Source(
                        id=f"src_{len(self._sources):03d}",
                        title=f"Alpha Vantage — {ticker} ({overview.get('Name', '')})",
                        url=f"https://www.alphavantage.co/query?symbol={ticker}",
                        credibility=SourceCredibility.GOVERNMENT,
                        key_data=f"Revenue TTM: {overview.get('RevenueTTM', 'N/A')}",
                    ))

        except (ValueError, AttributeError, RuntimeError):
            pass

        return company_data

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Top-down market sizing
    # ─────────────────────────────────────────────────────────────────────

    async def _top_down_sizing(
        self,
        market_query: str,
        search_data: list[dict[str, Any]],
        macro_data: dict[str, Any],
    ) -> FinancialMetric:
        """Apply top-down market sizing.

        Start with a large known market, apply filters (geography, segment,
        price point) to narrow to TAM. Each filter step must cite a source.

        Example: Global SaaS market ($250B, Gartner) → India SaaS ($15B,
        NASSCOM, 6% of global) → India Tier-2 SaaS ($3B, 20% of India SaaS,
        internal estimate based on Tier-2 business count).

        The LLM does the reasoning — it takes the raw data from search results
        and macro context, applies the filter chain, and produces a TAM
        estimate with sources for each step.
        """
        # Prepare data summary for LLM
        search_summary = "\n".join(
            f"- {r['title']}: {r.get('snippet', '')[:200]}"
            for r in search_data[:10]
        )
        macro_summary = json.dumps(macro_data, default=str)[:2000] if macro_data else "No macro data available"

        prompt = (
            "You are the Market Analyst applying top-down market sizing.\n\n"
            f"Market question: {market_query}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Macroeconomic context:\n{macro_summary}\n\n"
            "Apply the top-down sizing framework:\n"
            "1. Identify the largest relevant market (global, regional, or national)\n"
            "2. Apply geography filter (what % of the large market is in our target geography?)\n"
            "3. Apply segment filter (what % is in our target segment?)\n"
            "4. Apply price point filter if relevant\n"
            "5. Calculate TAM = large market × geography % × segment % × price %\n\n"
            "Return JSON:\n"
            "{\n"
            '  "tam_value": "number or range string",\n'
            '  "tam_low": number_or_null,\n'
            '  "tam_high": number_or_null,\n'
            '  "tam_base_case": number_or_null,\n'
            '  "unit": "$ or other unit",\n'
            '  "filter_chain": ["step 1: ...", "step 2: ..."],\n'
            '  "assumptions": ["assumption 1", "assumption 2"],\n'
            '  "data_quality": "high|medium|low"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return FinancialMetric(
                name="TAM (Top-Down)",
                value="Unable to estimate — insufficient data",
                unit="$",
                assumptions=["Top-down sizing failed due to LLM error or no data"],
            )

        try:
            data = json.loads(response.content)
            return FinancialMetric(
                name="TAM (Top-Down)",
                value=data.get("tam_value", "Unknown"),
                unit=data.get("unit", "$"),
                low_estimate=data.get("tam_low"),
                high_estimate=data.get("tam_high"),
                base_case=data.get("tam_base_case"),
                assumptions=data.get("assumptions", []),
                sources=[s for s in self._sources if s.credibility == SourceCredibility.INDUSTRY_REPORT][:5],
            )
        except (json.JSONDecodeError, ValueError):
            return FinancialMetric(
                name="TAM (Top-Down)",
                value="Parse error",
                unit="$",
                assumptions=["Top-down sizing failed — LLM output parsing error"],
            )

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Bottom-up market sizing
    # ─────────────────────────────────────────────────────────────────────

    async def _bottom_up_sizing(
        self,
        market_query: str,
        search_data: list[dict[str, Any]],
        public_company_data: list[dict[str, Any]],
    ) -> FinancialMetric:
        """Apply bottom-up market sizing.

        Start with unit economics and customer count.
        TAM = customer_count × adoption_rate × ARPU

        Each variable must have a source. Sensitivity analysis shows how
        TAM changes when each variable moves ±20%.

        Example: 50M Tier-2 businesses (census data) × 2% SaaS adoption
        (NASSCOM report) × $500 ARPU (industry survey) = $500M TAM.
        """
        search_summary = "\n".join(
            f"- {r['title']}: {r.get('snippet', '')[:200]}"
            for r in search_data[:10]
        )
        company_summary = "\n".join(
            f"- {c.get('name', c.get('ticker', ''))}: Revenue TTM {c.get('revenue', 'N/A')}"
            for c in public_company_data
        )

        prompt = (
            "You are the Market Analyst applying bottom-up market sizing.\n\n"
            f"Market question: {market_query}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Public company data:\n{company_summary}\n\n"
            "Apply the bottom-up sizing framework:\n"
            "1. Estimate total customer count in the target market (with source)\n"
            "2. Estimate adoption/penetration rate (with source)\n"
            "3. Estimate ARPU (Average Revenue Per User) (with source)\n"
            "4. Calculate TAM = customers × adoption × ARPU\n"
            "5. Run sensitivity: what if each variable moves ±20%?\n\n"
            "Return JSON:\n"
            "{\n"
            '  "tam_value": "number or range string",\n'
            '  "tam_low": number,\n'
            '  "tam_high": number,\n'
            '  "tam_base_case": number,\n'
            '  "unit": "$",\n'
            '  "customer_count": "estimate with source",\n'
            '  "adoption_rate": "estimate with source",\n'
            '  "arpu": "estimate with source",\n'
            '  "assumptions": ["assumption 1", ...],\n'
            '  "sensitivity": {"customers": {"low": number, "medium": number, "high": number}}\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return FinancialMetric(
                name="TAM (Bottom-Up)",
                value="Unable to estimate — insufficient data",
                unit="$",
                assumptions=["Bottom-up sizing failed due to LLM error or no data"],
            )

        try:
            data = json.loads(response.content)
            sensitivity = data.get("sensitivity")
            return FinancialMetric(
                name="TAM (Bottom-Up)",
                value=data.get("tam_value", "Unknown"),
                unit=data.get("unit", "$"),
                low_estimate=data.get("tam_low"),
                high_estimate=data.get("tam_high"),
                base_case=data.get("tam_base_case"),
                assumptions=data.get("assumptions", []),
                sensitivity=sensitivity if isinstance(sensitivity, dict) else None,
                sources=[s for s in self._sources if s.credibility in (
                    SourceCredibility.GOVERNMENT,
                    SourceCredibility.INDUSTRY_REPORT,
                )][:5],
            )
        except (json.JSONDecodeError, ValueError):
            return FinancialMetric(
                name="TAM (Bottom-Up)",
                value="Parse error",
                unit="$",
                assumptions=["Bottom-up sizing failed — LLM output parsing error"],
            )

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: CAGR triangulation
    # ─────────────────────────────────────────────────────────────────────

    async def _cagr_triangulation(
        self,
        tam_top_down: FinancialMetric,
        tam_bottom_up: FinancialMetric,
        search_data: list[dict[str, Any]],
    ) -> tuple[FinancialMetric, list[KeyFinding]]:
        """Cross-validate market size estimates via CAGR triangulation.

        If Source A says the market is $2B growing to $5B in 5 years (20% CAGR)
        but Source B says the market is growing at 10% CAGR, there's a
        contradiction. The triangulated estimate weights sources by credibility.

        Returns the triangulated TAM and a list of contradiction findings
        (if any sources disagree).
        """
        td_summary = f"Top-down: {tam_top_down.value} (base: {tam_top_down.base_case})"
        bu_summary = f"Bottom-up: {tam_bottom_up.value} (base: {tam_bottom_up.base_case})"
        search_summary = "\n".join(
            f"- {r['title']}: {r.get('snippet', '')[:150]}"
            for r in search_data[:8]
        )

        prompt = (
            "You are the Market Analyst performing CAGR triangulation.\n\n"
            f"{td_summary}\n{bu_summary}\n\n"
            f"Search results (may contain growth rate data):\n{search_summary}\n\n"
            "Triangulate the market size:\n"
            "1. If multiple sources provide market size estimates, calculate implied CAGR for each\n"
            "2. Check if CAGRs are consistent (within 5 percentage points = consistent)\n"
            "3. If inconsistent, flag the contradiction and determine which source is more credible\n"
            "4. Produce a triangulated best estimate weighted by source credibility\n"
            "5. Calculate CAGR from the triangulated estimate\n\n"
            "Return JSON:\n"
            "{\n"
            '  "triangulated_value": "number or range",\n'
            '  "triangulated_low": number,\n'
            '  "triangulated_high": number,\n'
            '  "triangulated_base": number,\n'
            '  "cagr_value": "number or range",\n'
            '  "cagr_low": number,\n'
            '  "cagr_high": number,\n'
            '  "cagr_base": number,\n'
            '  "unit": "$",\n'
            '  "cagr_unit": "%",\n'
            '  "contradictions": [{"title": "...", "description": "..."}],\n'
            '  "assumptions": ["..."]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        contradictions: list[KeyFinding] = []

        if not response.success or not response.content:
            return (
                FinancialMetric(
                    name="TAM (Triangulated)",
                    value="Unable to triangulate",
                    unit="$",
                    assumptions=["CAGR triangulation failed — no data or LLM error"],
                ),
                contradictions,
            )

        try:
            data = json.loads(response.content)

            # Extract contradictions as KeyFindings
            for c in data.get("contradictions", []):
                contradictions.append(KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="market_size_contradiction",
                    title=c.get("title", "Market size contradiction"),
                    content=c.get("description", ""),
                    confidence=ConfidenceLevel.MEDIUM,
                    sources=[],
                ))

            return (
                FinancialMetric(
                    name="TAM (Triangulated)",
                    value=data.get("triangulated_value", "Unknown"),
                    unit=data.get("unit", "$"),
                    low_estimate=data.get("triangulated_low"),
                    high_estimate=data.get("triangulated_high"),
                    base_case=data.get("triangulated_base"),
                    assumptions=data.get("assumptions", []),
                    sources=self._sources[:5],
                ),
                FinancialMetric(
                    name="CAGR",
                    value=data.get("cagr_value", "Unknown"),
                    unit=data.get("cagr_unit", "%"),
                    low_estimate=data.get("cagr_low"),
                    high_estimate=data.get("cagr_high"),
                    base_case=data.get("cagr_base"),
                    assumptions=data.get("assumptions", []),
                    sources=self._sources[:3],
                ),
                contradictions,
            )
        except (json.JSONDecodeError, ValueError):
            return (
                FinancialMetric(
                    name="TAM (Triangulated)",
                    value="Parse error",
                    unit="$",
                    assumptions=["CAGR triangulation failed — parsing error"],
                ),
                FinancialMetric(
                    name="CAGR",
                    value="Parse error",
                    unit="%",
                    assumptions=["CAGR calculation failed — parsing error"],
                ),
                contradictions,
            )

    # ─────────────────────────────────────────────────────────────────────
    # Step 8: Segment the market
    # ─────────────────────────────────────────────────────────────────────

    async def _segment_market(
        self,
        market_query: str,
        search_data: list[dict[str, Any]],
        tam_triangulated: FinancialMetric,
    ) -> list[KeyFinding]:
        """Segment the market by demographics, behavior, and psychographics.

        Identify which segment is the most attractive entry point based on
        size, growth rate, competition intensity, and fit. A market size
        without segmentation is useless for strategy.
        """
        search_summary = "\n".join(
            f"- {r['title']}: {r.get('snippet', '')[:150]}"
            for r in search_data[:8]
        )

        prompt = (
            "You are the Market Analyst performing market segmentation.\n\n"
            f"Market question: {market_query}\n\n"
            f"Triangulated TAM: {tam_triangulated.value}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Segment the market:\n"
            "1. Identify 3-5 meaningful segments (by demographics, behavior, or psychographics)\n"
            "2. Estimate size and growth rate for each segment\n"
            "3. Assess competition intensity in each segment (high/medium/low)\n"
            "4. Score each segment's attractiveness (size × growth × low competition)\n"
            "5. Recommend the most attractive entry segment with rationale\n\n"
            "Return JSON array of segments:\n"
            "[{\n"
            '  "name": "segment name",\n'
            '  "size": "estimated size",\n'
            '  "growth_rate": "estimated growth",\n'
            '  "competition": "high|medium|low",\n'
            '  "attractiveness_score": number_1_to_10,\n'
            '  "description": "why this segment matters",\n'
            '  "is_entry_point": true_or_false\n'
            "}]\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        segments: list[KeyFinding] = []

        if not response.success or not response.content:
            return segments

        try:
            data = json.loads(response.content)
            segment_list = data.get("segments", data) if isinstance(data, dict) else data
            if not isinstance(segment_list, list):
                segment_list = []

            for seg in segment_list:
                segments.append(KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="market_segment",
                    title=seg.get("name", "Unknown segment"),
                    content=(
                        f"Size: {seg.get('size', 'Unknown')}. "
                        f"Growth: {seg.get('growth_rate', 'Unknown')}. "
                        f"Competition: {seg.get('competition', 'Unknown')}. "
                        f"Attractiveness: {seg.get('attractiveness_score', 'N/A')}/10. "
                        f"{seg.get('description', '')}"
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    implications=(
                        f"Recommended entry point: {seg.get('is_entry_point', False)}"
                        if seg.get("is_entry_point") else None
                    ),
                    sources=self._sources[:3],
                ))

        except (json.JSONDecodeError, ValueError):
            pass

        return segments

    # ─────────────────────────────────────────────────────────────────────
    # Step 9: Identify growth drivers
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_growth_drivers(
        self,
        market_query: str,
        search_data: list[dict[str, Any]],
        macro_data: dict[str, Any],
    ) -> list[KeyFinding]:
        """Decompose market growth into component drivers.

        Total growth = population growth + penetration increase + ARPU expansion
        + new use cases. Each driver must have a source and contribution %.
        """
        search_summary = "\n".join(
            f"- {r['title']}: {r.get('snippet', '')[:150]}"
            for r in search_data[:8]
        )
        macro_summary = json.dumps(macro_data, default=str)[:1500] if macro_data else "No macro data"

        prompt = (
            "You are the Market Analyst decomposing growth drivers.\n\n"
            f"Market question: {market_query}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Macroeconomic context:\n{macro_summary}\n\n"
            "Decompose market growth into drivers:\n"
            "1. Population/customer base growth (how many more potential customers?)\n"
            "2. Penetration rate increase (what % of potential customers are adopting?)\n"
            "3. ARPU expansion (are customers spending more over time?)\n"
            "4. New use cases (are new applications of the product emerging?)\n"
            "5. Estimate each driver's contribution to total growth\n"
            "6. Verify: do the drivers sum to total market growth?\n\n"
            "Return JSON array:\n"
            "[{\n"
            '  "driver": "driver name",\n'
            '  "contribution_pct": number,\n'
            '  "description": "how this driver works",\n'
            '  "source": "where this data comes from"\n'
            "}]\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        drivers: list[KeyFinding] = []

        if not response.success or not response.content:
            return drivers

        try:
            data = json.loads(response.content)
            driver_list = data.get("drivers", data) if isinstance(data, dict) else data
            if not isinstance(driver_list, list):
                driver_list = []

            for drv in driver_list:
                drivers.append(KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="growth_driver",
                    title=drv.get("driver", "Unknown driver"),
                    content=(
                        f"Contribution: {drv.get('contribution_pct', 'Unknown')}% of total growth. "
                        f"{drv.get('description', '')} "
                        f"Source: {drv.get('source', 'Unspecified')}"
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    sources=self._sources[:2],
                ))

        except (json.JSONDecodeError, ValueError):
            pass

        return drivers

    # ─────────────────────────────────────────────────────────────────────
    # Market maturity assessment
    # ─────────────────────────────────────────────────────────────────────

    async def _assess_market_maturity(
        self,
        segments: list[KeyFinding],
        growth_drivers: list[KeyFinding],
        search_data: list[dict[str, Any]],
    ) -> str:
        """Classify market as emerging/growing/mature/declining.

        Based on: penetration rate, growth rate, number of competitors,
        price compression. This classification drives strategy — you enter
        an emerging market differently than a mature one.
        """
        seg_summary = "\n".join(f"- {s.title}: {s.content[:100]}" for s in segments[:5])
        driver_summary = "\n".join(f"- {d.title}: {d.content[:100]}" for d in growth_drivers[:5])
        search_summary = "\n".join(
            f"- {r['title']}: {r.get('snippet', '')[:100]}"
            for r in search_data[:5]
        )

        prompt = (
            "You are the Market Analyst assessing market maturity.\n\n"
            f"Segments:\n{seg_summary}\n\n"
            f"Growth drivers:\n{driver_summary}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Classify the market as one of: emerging, growing, mature, declining.\n"
            "- Emerging: <10% penetration, few competitors, high growth (>20% CAGR)\n"
            "- Growing: 10-30% penetration, increasing competitors, moderate growth (10-20% CAGR)\n"
            "- Mature: >30% penetration, many competitors, low growth (<10%), price compression\n"
            "- Declining: negative growth, market consolidation\n\n"
            "Return JSON: {\"maturity\": \"emerging|growing|mature|declining\", "
            "\"rationale\": \"why this classification\"}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return "unknown"

        try:
            data = json.loads(response.content)
            maturity = data.get("maturity") or "unknown"
            maturity = maturity.lower() if isinstance(maturity, str) else "unknown"
            valid = {"emerging", "growing", "mature", "declining"}
            return maturity if maturity in valid else "unknown"
        except (json.JSONDecodeError, ValueError):
            return "unknown"

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_data_collection_sub_agents(
        self,
        market_query: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel market data collection.

        Per §4.4, Agent 3:
        - Sub-agent A: Find TAM data for [specific market] (MICRO, SearxNG + Jina)
        - Sub-agent B: Find [geography] spending data (MICRO, SearxNG + Obscura)
        - Sub-agent C: Find adoption/penetration rates (FAST, Obscura + Jina)

        These run in parallel to speed up data collection. Each returns
        structured KeyFinding objects that feed into the sizing frameworks.
        """
        # Extract geography and segment from the market query
        # This is a simple heuristic — the LLM in the sizing steps will refine
        geography = self._context.get("geography") or ""
        segment = self._context.get("segment") or ""

        sub_specs = [
            SubAgentSpec(
                question=f"Find TAM data for: {market_query}",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"market_query": market_query},
            ),
            SubAgentSpec(
                question=f"Find {geography or 'target geography'} spending data for: {market_query}",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"geography": geography, "market_query": market_query},
            ),
            SubAgentSpec(
                question=f"Find adoption and penetration rates for: {market_query}",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.OBSCURA, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"market_query": market_query, "segment": segment},
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
        tam_top_down: FinancialMetric,
        tam_bottom_up: FinancialMetric,
        sources_count: int,
        contradiction_count: int,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on data quality.

        HIGH: 3+ sources, top-down and bottom-up within 30% of each other,
              no contradictions
        MEDIUM: 2+ sources, estimates within 50%, ≤1 contradiction
        LOW: <2 sources, estimates diverge >50%, or multiple contradictions
        """
        # Source count check
        if sources_count < 2:
            return ConfidenceLevel.LOW

        # Contradiction check
        if contradiction_count > 1:
            return ConfidenceLevel.LOW

        # Estimate convergence check
        td_base = tam_top_down.base_case
        bu_base = tam_bottom_up.base_case
        if td_base is not None and bu_base is not None and td_base > 0 and bu_base > 0:
            divergence = abs(td_base - bu_base) / max(td_base, bu_base)
            if divergence < 0.3 and sources_count >= 3 and contradiction_count == 0:
                return ConfidenceLevel.HIGH
            if divergence < 0.5:
                return ConfidenceLevel.MEDIUM
            return ConfidenceLevel.LOW

        # If we can't compare base cases, use source count
        if sources_count >= 3 and contradiction_count == 0:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # SAM and SOM calculation
    # ─────────────────────────────────────────────────────────────────────

    async def _calculate_sam_som(
        self,
        tam_triangulated: FinancialMetric,
        market_query: str,
    ) -> tuple[FinancialMetric, FinancialMetric]:
        """Calculate SAM and SOM from the triangulated TAM.

        SAM (Serviceable Addressable Market): The portion of TAM we can
        reach with our business model, geography, and channel strategy.
        Typically 20-60% of TAM.

        SOM (Serviceable Obtainable Market): The portion of SAM we can
        realistically capture in 3-5 years. Typically 5-20% of SAM,
        depending on competition and our capabilities.
        """
        prompt = (
            "You are the Market Analyst calculating SAM and SOM.\n\n"
            f"Market question: {market_query}\n"
            f"Triangulated TAM: {tam_triangulated.value} "
            f"(base case: {tam_triangulated.base_case})\n\n"
            "Calculate:\n"
            "1. SAM = TAM × serviceable_percentage (what % can we reach with our model?)\n"
            "2. SOM = SAM × capture_percentage (what % can we capture in 3-5 years?)\n"
            "Both must have rationale and assumptions.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "sam_value": "number or range",\n'
            '  "sam_base": number,\n'
            '  "sam_low": number,\n'
            '  "sam_high": number,\n'
            '  "sam_pct_of_tam": number,\n'
            '  "sam_assumptions": ["..."],\n'
            '  "som_value": "number or range",\n'
            '  "som_base": number,\n'
            '  "som_low": number,\n'
            '  "som_high": number,\n'
            '  "som_pct_of_sam": number,\n'
            '  "som_assumptions": ["..."]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return (
                FinancialMetric(name="SAM", value="Unable to estimate", unit="$", assumptions=["SAM calculation failed"]),
                FinancialMetric(name="SOM", value="Unable to estimate", unit="$", assumptions=["SOM calculation failed"]),
            )

        try:
            data = json.loads(response.content)
            sam = FinancialMetric(
                name="SAM (Serviceable Addressable Market)",
                value=data.get("sam_value", "Unknown"),
                unit="$",
                low_estimate=data.get("sam_low"),
                high_estimate=data.get("sam_high"),
                base_case=data.get("sam_base"),
                assumptions=data.get("sam_assumptions", []),
                sources=self._sources[:3],
            )
            som = FinancialMetric(
                name="SOM (Serviceable Obtainable Market)",
                value=data.get("som_value", "Unknown"),
                unit="$",
                low_estimate=data.get("som_low"),
                high_estimate=data.get("som_high"),
                base_case=data.get("som_base"),
                assumptions=data.get("som_assumptions", []),
                sources=self._sources[:3],
            )
            return sam, som
        except (json.JSONDecodeError, ValueError):
            return (
                FinancialMetric(name="SAM", value="Parse error", unit="$", assumptions=["SAM parsing failed"]),
                FinancialMetric(name="SOM", value="Parse error", unit="$", assumptions=["SOM parsing failed"]),
            )

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 10-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> MarketAnalysis:
        """Execute the Market Analyst's 10-step methodology.

        Steps (§4.4, Agent 3):
        1. Search for existing market reports (SearxNG + Jina)
        2. If no direct data, scrape interactive dashboards (Obscura)
        3. Pull macroeconomic context (FRED)
        4. Pull public company revenue data for market sizing (Alpha Vantage)
        5. Apply top-down sizing
        6. Apply bottom-up sizing
        7. Cross-validate via CAGR triangulation
        8. Segment the market
        9. Identify growth drivers
        10. Produce structured MarketAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus — specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting market analysis: {self._question[:80]}",
        )

        # Spawn sub-agents for parallel data collection
        await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning data collection sub-agents")
        sub_findings = await self._spawn_data_collection_sub_agents(self._question)
        self._sub_agent_findings = sub_findings

        await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 1: Search for existing market reports
        await self._transition(AgentState.WORKING, "Step 1: Searching for market reports (SearxNG)")
        self._search_results = await self._search_market_reports(self._question)

        # Extract content from top URLs
        top_urls = [r["url"] for r in self._search_results if r.get("url")]
        if top_urls:
            await self._transition(AgentState.WORKING, "Step 1b: Extracting content (Jina)")
            self._extracted_content = await self._extract_content_from_urls(top_urls)

        # Step 2: Scrape interactive dashboards if search didn't find enough
        if len(self._search_results) < 5:
            await self._transition(AgentState.WORKING, "Step 2: Scraping dashboards (Obscura)")
            self._scraped_data = await self._scrape_dashboards(self._question)

        # Step 3: Pull macroeconomic context
        geography = self._context.get("geography") or "US"
        await self._transition(AgentState.WORKING, f"Step 3: Pulling macro data (FRED) for {geography}")
        self._macro_data = await self._pull_macro_context(geography)

        # Step 4: Pull public company revenue data
        tickers = self._context.get("tickers", [])
        if tickers:
            await self._transition(AgentState.WORKING, "Step 4: Pulling company data (Alpha Vantage)")
            self._public_company_data = await self._pull_public_company_data(tickers)

        # Combine all data for sizing
        all_search_data = self._search_results + self._scraped_data

        # Step 5: Top-down sizing
        await self._transition(AgentState.WORKING, "Step 5: Top-down market sizing")
        tam_top_down = await self._top_down_sizing(self._question, all_search_data, self._macro_data)

        # Step 6: Bottom-up sizing
        await self._transition(AgentState.WORKING, "Step 6: Bottom-up market sizing")
        tam_bottom_up = await self._bottom_up_sizing(
            self._question, all_search_data, self._public_company_data,
        )

        # Step 7: CAGR triangulation
        await self._transition(AgentState.WORKING, "Step 7: CAGR triangulation")
        triangulated_result = await self._cagr_triangulation(
            tam_top_down, tam_bottom_up, all_search_data,
        )
        # Handle both 2-tuple (error case) and 3-tuple (success case)
        if len(triangulated_result) == 3:
            tam_triangulated, cagr_metric, contradiction_findings = triangulated_result
        else:
            tam_triangulated, contradiction_findings = triangulated_result
            cagr_metric = FinancialMetric(
                name="CAGR",
                value="Unable to calculate",
                unit="%",
                assumptions=["CAGR calculation failed"],
            )

        # Calculate SAM and SOM
        await self._transition(AgentState.WORKING, "Calculating SAM and SOM")
        sam, som = await self._calculate_sam_som(tam_triangulated, self._question)

        # Step 8: Segment the market
        await self._transition(AgentState.WORKING, "Step 8: Market segmentation")
        segments = await self._segment_market(self._question, all_search_data, tam_triangulated)

        # Step 9: Identify growth drivers
        await self._transition(AgentState.WORKING, "Step 9: Growth driver decomposition")
        growth_drivers = await self._identify_growth_drivers(
            self._question, all_search_data, self._macro_data,
        )

        # Assess market maturity
        await self._transition(AgentState.WORKING, "Assessing market maturity")
        market_maturity = await self._assess_market_maturity(
            segments, growth_drivers, all_search_data,
        )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            tam_top_down,
            tam_bottom_up,
            len(self._sources),
            len(contradiction_findings),
        )

        # Step 10: Produce MarketAnalysis model
        await self._transition(AgentState.WORKING, "Step 10: Producing MarketAnalysis model")

        analysis = MarketAnalysis(
            tam_top_down=tam_top_down,
            tam_bottom_up=tam_bottom_up,
            tam_triangulated=tam_triangulated,
            sam=sam,
            som=som,
            cagr=cagr_metric,
            segments=segments,
            growth_drivers=growth_drivers,
            market_maturity=market_maturity,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        for finding in segments + growth_drivers + contradiction_findings:
            await self._publish_finding(finding)

        # Publish the full MarketAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "market_analysis": analysis.model_dump(),
                "tam_triangulated": str(tam_triangulated.value),
                "market_maturity": market_maturity,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Market analysis complete: TAM {tam_triangulated.value}, "
            f"maturity={market_maturity}, confidence={confidence.value}",
        )

        return analysis
