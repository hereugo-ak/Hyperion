"""
HYPERION M&A Analyst — Agent 12, the mergers & acquisitions specialist.

This is NOT a generic "find acquisition targets" agent. This is a specialist
with 6 proprietary analytical frameworks:

- Target identification: Screen for acquisition targets using criteria:
  strategic fit, size, geography, technology, talent, customer base. Build a
  long list (20-50) and short list (5-10) with rationale for each.
- Synergy analysis: Quantify revenue synergies (cross-sell, upsell, new
  markets) and cost synergies (headcount reduction, facility consolidation,
  procurement savings). ALWAYS with a reality discount — synergies rarely
  materialize at 100% of the estimate. 50-70% of estimated synergies typically
  materialize.
- Integration planning: Build a 100-day integration plan with workstreams,
  milestones, owners, and risk flags. Identify the top 3 integration risks.
- Valuation gap analysis: Compare the acquirer's maximum acceptable price to
  the target's minimum acceptable price. Identify the zone of possible
  agreement.
- Accretion/dilution analysis: Model the impact of the acquisition on the
  acquirer's EPS over 1-3 years. Identify whether the deal is accretive or
  dilutive and under what conditions.
- Cultural fit assessment: Evaluate cultural compatibility using public data
  (Glassdoor reviews, LinkedIn company pages, employee sentiment). Cultural
  mismatch is the #1 reason M&A deals fail to deliver synergies.

It always applies a reality discount to synergies — 50-70% of estimated
synergies typically materialize. It always assesses cultural fit because that's
the #1 failure cause. It always builds an integration plan, not just a deal
rationale — because the deal is the easy part, integration is the hard part.
(§4.4, Agent 12)

Model Tier: STRONG (Nemotron 3 Super 120B — M&A analysis is complex and
requires strong reasoning)
Tools: SearxNG, Jina, Obscura, Alpha Vantage
Sub-agents: Max 3 — target screening, financials pull, cultural reviews
Output: MAAnalysis (target list, synergy analysis, accretion/dilution,
        cultural fit, integration plan)

Methodology (§4.4, Agent 12):
1. Define acquisition criteria with Engagement Director
2. Search for potential targets (SearxNG + Jina + Obscura)
3. Build long list → short list
4. Pull financial data for targets (Alpha Vantage)
5. Run synergy analysis
6. Run accretion/dilution
7. Assess cultural fit
8. Build integration plan
9. Produce M&AAnalysis model
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
    AccretionDilution,
    AcquisitionTarget,
    ConfidenceLevel,
    CulturalFit,
    IntegrationPlan,
    IntegrationWorkstream,
    KeyFinding,
    MAAnalysis,
    Source,
    SourceCredibility,
    SynergyAnalysis,
    ValuationGap,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


MA_ANALYST_SPEC = AgentSpec(
    name=AgentName.MA_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="M&A Analyst",
    model_tier=ModelTier.STRONG,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.ALPHA_VANTAGE,
        ToolName.SEC_EDGAR,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Target identification",
            description=(
                "Screen for acquisition targets using criteria: strategic fit, "
                "size, geography, technology, talent, customer base. Build a "
                "long list (20-50) and short list (5-10) with rationale for "
                "each. Not just 'companies in the space' — each target has "
                "strategic_fit rationale, acquisition_rationale, revenue, "
                "employees, and key risks."
            ),
            inputs=["acquisition_criteria", "sector", "geography", "size_range"],
            outputs=["long_list", "short_list", "strategic_fit", "acquisition_rationale"],
        ),
        SkillSpec(
            name="Synergy analysis",
            description=(
                "Quantify revenue synergies (cross-sell, upsell, new markets) "
                "and cost synergies (headcount reduction, facility consolidation, "
                "procurement savings). ALWAYS with a reality discount — synergies "
                "rarely materialize at 100% of the estimate. 50-70% of estimated "
                "synergies typically materialize. Not just 'synergies exist' — "
                "'$15M revenue synergies + $10M cost synergies = $25M total, "
                "reality discount 40%, realizable = $15M over 24 months.'"
            ),
            inputs=["target_financials", "acquirer_financials", "overlap_analysis"],
            outputs=["revenue_synergies", "cost_synergies", "total_estimated", "reality_discount", "realizable_synergies"],
        ),
        SkillSpec(
            name="Integration planning",
            description=(
                "Build a 100-day integration plan with workstreams, milestones, "
                "owners, and risk flags. Identify the top 3 integration risks. "
                "Each workstream has Day 1 actions, Day 30 milestones, Day 100 "
                "milestones. The deal is the easy part — integration is the "
                "hard part."
            ),
            inputs=["deal_structure", "target_operations", "acquirer_operations", "risk_assessment"],
            outputs=["workstreams", "day_1_priorities", "day_30_milestones", "day_100_milestones", "top_3_risks"],
        ),
        SkillSpec(
            name="Valuation gap analysis",
            description=(
                "Compare the acquirer's maximum acceptable price to the target's "
                "minimum acceptable price. Identify the zone of possible "
                "agreement. Not just 'the company is worth $X' — 'acquirer max "
                "$500M, target min $420M, zone of possible agreement $420M-"
                "$500M, likely price $460M (15% premium to market).'"
            ),
            inputs=["target_valuation", "acquirer_capacity", "market_comparables"],
            outputs=["acquirer_max_price", "target_min_price", "zone_of_possible_agreement", "likely_price"],
        ),
        SkillSpec(
            name="Accretion/dilution analysis",
            description=(
                "Model the impact of the acquisition on the acquirer's EPS over "
                "1-3 years. Identify whether the deal is accretive or dilutive "
                "and under what conditions. Not just 'accretive' — 'Year 1: "
                "-2% dilutive, Year 2: +1% accretive, Year 3: +4% accretive. "
                "Becomes accretive in Year 2 if synergies are 60% realized.'"
            ),
            inputs=["deal_terms", "acquirer_eps", "target_earnings", "financing_structure"],
            outputs=["year_1_eps_impact", "year_2_eps_impact", "year_3_eps_impact", "is_accretive", "accretive_conditions"],
        ),
        SkillSpec(
            name="Cultural fit assessment",
            description=(
                "Evaluate cultural compatibility using public data (Glassdoor "
                "reviews, LinkedIn company pages, employee sentiment). Cultural "
                "mismatch is the #1 reason M&A deals fail to deliver synergies. "
                "Not just 'cultures are different' — 'Acquirer: mission-driven, "
                "hierarchical. Target: flat, engineering-led. Compatibility 6/10. "
                "Key misalignment: decision-making speed. Integration risk: HIGH.'"
            ),
            inputs=["glassdoor_reviews", "linkedin_data", "employee_sentiment"],
            outputs=["compatibility_score", "alignment_areas", "misalignment_areas", "integration_risk"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION M&A Analyst — the specialist who identifies "
        "acquisition targets, conducts due diligence, models synergies, and "
        "plans integration.\n\n"
        "Your proprietary frameworks:\n"
        "1. Target identification: Screen using strategic fit, size, geography, "
        "technology, talent, customer base. Long list (20-50) → short list "
        "(5-10) with rationale for each.\n"
        "2. Synergy analysis: Revenue synergies (cross-sell, upsell, new "
        "markets) + cost synergies (headcount, facilities, procurement). "
        "ALWAYS apply a reality discount — 50-70% of estimated synergies "
        "typically materialize. Not just 'synergies exist' — '$15M revenue + "
        "$10M cost = $25M total, reality discount 40%, realizable $15M.'\n"
        "3. Integration planning: 100-day plan with workstreams, milestones, "
        "owners, risk flags. Top 3 integration risks. The deal is the easy "
        "part — integration is the hard part.\n"
        "4. Valuation gap: Acquirer max price vs target min price. Zone of "
        "possible agreement. Likely transaction price with premium.\n"
        "5. Accretion/dilution: EPS impact over 1-3 years. Accretive or "
        "dilutive and under what conditions.\n"
        "6. Cultural fit: Glassdoor, LinkedIn, employee sentiment. Cultural "
        "mismatch is the #1 failure cause.\n\n"
        "Rules:\n"
        "- ALWAYS APPLY REALITY DISCOUNT TO SYNERGIES. 50-70% of estimated "
        "synergies typically materialize. Never present 100% as realizable.\n"
        "- ALWAYS ASSESS CULTURAL FIT. It's the #1 reason M&A deals fail to "
        "deliver synergies. Use Glassdoor, LinkedIn, public sentiment data.\n"
        "- ALWAYS BUILD AN INTEGRATION PLAN. Not just a deal rationale. The "
        "deal is the easy part, integration is the hard part.\n"
        "- Short list targets must have specific acquisition_rationale, not "
        "just 'they're in our space.'\n"
        "- Accretion/dilution must model 1-3 year EPS impact, not just "
        "'accretive.'\n"
        "- Valuation gap must identify the zone of possible agreement, not "
        "just a single price.\n\n"
        "You can spawn up to 3 sub-agents for parallel data collection:\n"
        "- Sub-agent A: Screen targets by [criteria] (MICRO, SearxNG + Obscura)\n"
        "- Sub-agent B: Pull financials for [target1, target2, target3] (MICRO, Alpha Vantage)\n"
        "- Sub-agent C: Find cultural reviews for [target companies] (FAST, Obscura)\n\n"
        "Your output is an MAAnalysis Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned when the question involves acquisitions, mergers, "
                     "target identification, synergy analysis, integration "
                     "planning, or M&A strategy (MA_ANALYSIS, ACQUISITION, "
                     "MERGER, SYNERGY, INTEGRATION types)",
    max_sub_agents=3,
    output_model="MAAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# M&A Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class MAAnalyst(BaseAgent):
    """Agent 12: The mergers & acquisitions specialist.

    Identifies acquisition targets, conducts due diligence, models synergies
    with reality discounts, plans 100-day integration, analyzes valuation gaps,
    models accretion/dilution, and assesses cultural fit. Always applies a
    reality discount to synergies. Always assesses cultural fit (#1 failure
    cause). Always builds an integration plan. (§4.4, Agent 12)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Defines acquisition criteria with Engagement Director
    3. Searches for potential targets (SearxNG + Jina + Obscura)
    4. Builds long list → short list
    5. Pulls financial data for targets (Alpha Vantage)
    6. Runs synergy analysis, accretion/dilution, cultural fit
    7. Builds integration plan
    8. Produces MAAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or MA_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._deal_database_data: list[dict[str, Any]] = []
        self._target_financials: list[dict[str, Any]] = []
        self._cultural_data: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

        # Acquisition criteria
        self._acquisition_criteria: str = ""

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The M&A Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting acquisition targets for growth strategy)
        - FINDINGS: receives findings from other agents that may inform
          M&A analysis (e.g., Financial Analyst's valuation data,
          Competitive Intel's competitor landscape, Market Analyst's
          market growth data)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "ma_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Financial Analyst's valuation data informs target pricing
                if finding.finding_type == "valuation":
                    self._context.setdefault("valuation_data", []).append(finding.content)
                # Competitive Intel's competitor landscape informs target identification
                elif finding.finding_type == "competitor_landscape":
                    self._context.setdefault("competitor_data", []).append(finding.content)
                # Market Analyst's market growth informs acquisition rationale
                elif finding.finding_type == "market_growth":
                    self._context.setdefault("market_data", []).append(finding.content)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "acquisition_targets":
                # Strategy Analyst requesting target list for growth strategy
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Define acquisition criteria with Engagement Director
    # ─────────────────────────────────────────────────────────────────────

    async def _define_acquisition_criteria(self, question: str, context: dict[str, Any]) -> str:
        """Define acquisition criteria based on the engagement question and
        context from the Engagement Director.

        Criteria include: strategic fit, size range, geography, technology,
        talent, customer base.
        """
        acquirer = context.get("company", context.get("acquirer", ""))
        sector = context.get("sector", context.get("industry", ""))
        size_range = context.get("size_range", "")
        geography = context.get("geography", "")

        prompt = (
            "You are the HYPERION M&A Analyst defining acquisition criteria.\n\n"
            f"Question: {question}\n\n"
            f"Acquirer: {acquirer}\n"
            f"Sector: {sector}\n"
            f"Size range: {size_range or 'Not specified'}\n"
            f"Geography: {geography or 'Not specified'}\n\n"
            "Define specific acquisition criteria:\n"
            "- Strategic fit: what capabilities/market access is the acquirer seeking?\n"
            "- Size: revenue/employee range for targets\n"
            "- Geography: which regions?\n"
            "- Technology: what technology stack/IP?\n"
            "- Talent: what key talent?\n"
            "- Customer base: what customer segments?\n\n"
            "Be specific. Not 'companies in tech' but 'mid-market SaaS companies "
            "with $10-50M revenue, 50-200 employees, North American HQ, AI/ML "
            "capabilities, enterprise customer base.'\n\n"
            "Return the criteria as a single string paragraph."
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
        )

        if response.success and response.content:
            return response.content.strip()
        return f"Acquisition criteria for {acquirer or sector}: {size_range} {geography} {sector}"

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Search for potential targets (SearxNG + Jina + Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_targets(self, criteria: str, sector: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Search for potential acquisition targets.

        Uses SearxNG to find: M&A transactions, deal databases, acquisition
        news. Uses Jina to extract deal announcements, merger documents, and
        M&A analysis reports. Uses Obscura to scrape JS-rendered M&A databases
        (PitchBook-style sites), company databases (Crunchbase-style), and
        deal trackers.
        """
        search_results: list[dict[str, Any]] = []
        deal_db_data: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{sector} acquisition targets M&A opportunities",
                f"{sector} companies for sale merger acquisition",
                f"{sector} recent M&A transactions deals",
                f"{sector} Crunchbase companies acquisition",
                f"{sector} PitchBook M&A database",
                f"{sector} startup acquisition targets",
                f"{sector} mid-market companies acquisition",
                f"{sector} vertical integration targets",
            ]

            for pattern in query_patterns[:10]:
                results = await searxng.search(pattern, max_results=6)
                for r in results:
                    search_results.append({
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
                top_urls = [r["url"] for r in search_results[:6] if r.get("url")]
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

            # Scrape M&A databases and company databases using Obscura
            try:
                obscura = self.get_tool(ToolName.OBSCURA)
                db_urls = [
                    f"https://www.crunchbase.com/discover/organization.companies?field=category&value={sector}",
                    "https://pitchbook.com/profiles/search",
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
                            deal_db_data.append({
                                "url": url,
                                "data": page_data,
                            })
                            self._sources.append(Source(
                                id=f"src_{len(self._sources):03d}",
                                title=f"Deal database — {url.split('/')[2]}",
                                url=url,
                                credibility=SourceCredibility.INDUSTRY_REPORT,
                                key_data=f"M&A database data from {url.split('/')[2]}",
                            ))
                    except (ValueError, AttributeError, RuntimeError):
                        continue
            except (ValueError, AttributeError, RuntimeError):
                pass

        except (ValueError, AttributeError, RuntimeError):
            pass

        return (search_results, deal_db_data)

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Build long list → short list
    # ─────────────────────────────────────────────────────────────────────

    async def _build_target_lists(
        self,
        question: str,
        criteria: str,
        search_results: list[dict[str, Any]],
        deal_db_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[list[AcquisitionTarget], list[AcquisitionTarget]]:
        """Build long list (20-50) and short list (5-10) of acquisition targets.

        Each target has strategic_fit rationale, acquisition_rationale,
        revenue, employees, and key risks.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:15]
        )
        db_summary = json.dumps(
            [{"url": d.get("url", ""), "data": str(d.get("data", ""))[:300]} for d in deal_db_data[:3]],
            default=str,
        )[:800]

        prompt = (
            "You are the HYPERION M&A Analyst building target lists.\n\n"
            f"Question: {question}\n\n"
            f"Acquisition criteria: {criteria}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Deal database data:\n{db_summary}\n\n"
            "Build a LONG LIST (aim for 10-20 targets) and a SHORT LIST (aim "
            "for 3-5 targets) of acquisition targets.\n\n"
            "For each target:\n"
            "- company_name: target company name\n"
            "- ticker: stock ticker if public\n"
            "- description: brief description\n"
            "- headquarters: HQ location\n"
            "- employees: employee count\n"
            "- revenue: annual revenue ($)\n"
            "- strategic_fit: why this fits the acquisition criteria\n"
            "- acquisition_rationale: why acquire this specific company\n"
            "- list_stage: 'long' or 'short'\n"
            "- risks: key risks (list)\n\n"
            "Short list targets must have SPECIFIC acquisition_rationale, not "
            "just 'they're in our space.' Each short list target should have "
            "a compelling strategic reason.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "long_list": [{...}],\n'
            '  "short_list": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        long_list: list[AcquisitionTarget] = []
        short_list: list[AcquisitionTarget] = []

        if not response.success or not response.content:
            return (long_list, short_list)

        try:
            data = json.loads(response.content)

            for t in data.get("long_list", []):
                long_list.append(AcquisitionTarget(
                    company_name=t.get("company_name", "Unknown"),
                    ticker=t.get("ticker", ""),
                    description=t.get("description", ""),
                    headquarters=t.get("headquarters", ""),
                    employees=t.get("employees", ""),
                    revenue=t.get("revenue", ""),
                    strategic_fit=t.get("strategic_fit", ""),
                    acquisition_rationale=t.get("acquisition_rationale", ""),
                    list_stage="long",
                    risks=t.get("risks", []),
                ))

            for t in data.get("short_list", []):
                short_list.append(AcquisitionTarget(
                    company_name=t.get("company_name", "Unknown"),
                    ticker=t.get("ticker", ""),
                    description=t.get("description", ""),
                    headquarters=t.get("headquarters", ""),
                    employees=t.get("employees", ""),
                    revenue=t.get("revenue", ""),
                    strategic_fit=t.get("strategic_fit", ""),
                    acquisition_rationale=t.get("acquisition_rationale", ""),
                    list_stage="short",
                    risks=t.get("risks", []),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (long_list, short_list)

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Pull financial data for targets (Alpha Vantage)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_target_financials(self, short_list: list[AcquisitionTarget]) -> list[dict[str, Any]]:
        """Pull financial data for short-listed targets using Alpha Vantage.

        Gets income statement, balance sheet, cash flow, and company overview
        for each public target. For private targets, notes that financials
        are unavailable.
        """
        results: list[dict[str, Any]] = []

        try:
            alpha_vantage = self.get_tool(ToolName.ALPHA_VANTAGE)

            for target in short_list[:5]:
                if not target.ticker:
                    results.append({
                        "company": target.company_name,
                        "ticker": "",
                        "data": None,
                        "note": "Private company — financials unavailable",
                    })
                    continue

                try:
                    # Get company overview
                    overview = await alpha_vantage.get_overview(target.ticker)
                    # Get income statement
                    income = await alpha_vantage.get_income_statement(target.ticker)
                    # Get balance sheet
                    balance = await alpha_vantage.get_balance_sheet(target.ticker)

                    results.append({
                        "company": target.company_name,
                        "ticker": target.ticker,
                        "overview": overview,
                        "income_statement": income,
                        "balance_sheet": balance,
                    })

                    self._sources.append(Source(
                        id=f"src_{len(self._sources):03d}",
                        title=f"Alpha Vantage — {target.ticker}",
                        url=f"https://www.alphavantage.co/query?function=OVERVIEW&symbol={target.ticker}",
                        credibility=SourceCredibility.GOVERNMENT,
                        key_data=f"Financial data for {target.company_name} ({target.ticker})",
                    ))
                except (ValueError, AttributeError, RuntimeError):
                    results.append({
                        "company": target.company_name,
                        "ticker": target.ticker,
                        "data": None,
                        "note": "Failed to pull financials",
                    })

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Run synergy analysis
    # ─────────────────────────────────────────────────────────────────────

    async def _run_synergy_analysis(
        self,
        question: str,
        short_list: list[AcquisitionTarget],
        target_financials: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> SynergyAnalysis:
        """Run synergy analysis with reality discount.

        Quantifies revenue synergies (cross-sell, upsell, new markets) and
        cost synergies (headcount, facilities, procurement). ALWAYS with a
        reality discount — 50-70% of estimated synergies typically materialize.
        """
        targets_summary = "\n".join(
            f"- {t.company_name}: {t.revenue}, {t.employees} employees, {t.strategic_fit[:100]}"
            for t in short_list[:3]
        )
        financials_summary = json.dumps(
            [{"company": f.get("company", ""), "overview": str(f.get("overview", ""))[:200]} for f in target_financials[:3]],
            default=str,
        )[:600]

        prompt = (
            "You are the HYPERION M&A Analyst running synergy analysis.\n\n"
            f"Question: {question}\n\n"
            f"Short list targets:\n{targets_summary}\n\n"
            f"Target financials:\n{financials_summary}\n\n"
            "Quantify synergies for the TOP short-listed target:\n\n"
            "REVENUE SYNERGIES:\n"
            "- Cross-sell opportunities\n"
            "- Upsell opportunities\n"
            "- New market access\n"
            "- Revenue synergy value ($/yr)\n\n"
            "COST SYNERGIES:\n"
            "- Headcount reduction\n"
            "- Facility consolidation\n"
            "- Procurement savings\n"
            "- Cost synergy value ($/yr)\n\n"
            "REALITY DISCOUNT:\n"
            "- Apply a reality discount (40-50% is standard — synergies rarely "
            "materialize at 100%)\n"
            "- Calculate realizable synergies after discount\n"
            "- Timeline for synergy realization\n\n"
            "NOT just 'synergies exist.' Be specific: '$15M revenue + $10M cost "
            "= $25M total, reality discount 40%, realizable $15M over 24 months.'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "revenue_synergies": ["..."],\n'
            '  "revenue_synergy_value": "$.../yr",\n'
            '  "cost_synergies": ["..."],\n'
            '  "cost_synergy_value": "$.../yr",\n'
            '  "total_estimated_synergies": "$.../yr",\n'
            '  "reality_discount_percentage": "...%",\n'
            '  "realizable_synergies": "$.../yr",\n'
            '  "synergy_timeline": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return SynergyAnalysis()

        try:
            data = json.loads(response.content)
            return SynergyAnalysis(
                revenue_synergies=data.get("revenue_synergies", []),
                revenue_synergy_value=data.get("revenue_synergy_value", ""),
                cost_synergies=data.get("cost_synergies", []),
                cost_synergy_value=data.get("cost_synergy_value", ""),
                total_estimated_synergies=data.get("total_estimated_synergies", ""),
                reality_discount_percentage=data.get("reality_discount_percentage", "40%"),
                realizable_synergies=data.get("realizable_synergies", ""),
                synergy_timeline=data.get("synergy_timeline", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return SynergyAnalysis()

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Run accretion/dilution + valuation gap
    # ─────────────────────────────────────────────────────────────────────

    async def _run_accretion_dilution_and_valuation(
        self,
        question: str,
        short_list: list[AcquisitionTarget],
        target_financials: list[dict[str, Any]],
        synergy_analysis: SynergyAnalysis,
        context: dict[str, Any],
    ) -> tuple[AccretionDilution | None, ValuationGap | None]:
        """Run accretion/dilution analysis and valuation gap analysis.

        Accretion/dilution: Models EPS impact over 1-3 years. Identifies
        whether the deal is accretive or dilutive and under what conditions.

        Valuation gap: Compares acquirer's max price to target's min price.
        Identifies the zone of possible agreement.

        Returns (accretion_dilution, valuation_gap).
        """
        top_target = short_list[0] if short_list else None
        if not top_target:
            return (None, None)

        financials_summary = json.dumps(
            [{"company": f.get("company", ""), "overview": str(f.get("overview", ""))[:200]} for f in target_financials[:2]],
            default=str,
        )[:600]

        prompt = (
            "You are the HYPERION M&A Analyst running accretion/dilution + valuation gap.\n\n"
            f"Question: {question}\n\n"
            f"Top target: {top_target.company_name} ({top_target.ticker or 'private'})\n"
            f"Target revenue: {top_target.revenue}\n\n"
            f"Target financials:\n{financials_summary}\n\n"
            f"Synergies: {synergy_analysis.total_estimated_synergies} (realizable: {synergy_analysis.realizable_synergies})\n\n"
            "ACCRETION/DILUTION:\n"
            "- year_1_eps_impact: Year 1 EPS impact (%)\n"
            "- year_2_eps_impact: Year 2 EPS impact (%)\n"
            "- year_3_eps_impact: Year 3 EPS impact (%)\n"
            "- is_accretive: is the deal accretive in year 1?\n"
            "- accretive_conditions: conditions for accretion\n"
            "- pro_forma_revenue: combined revenue ($)\n"
            "- pro_forma_ebitda: combined EBITDA ($)\n"
            "- deal_financing: cash, stock, or debt\n\n"
            "VALUATION GAP:\n"
            "- acquirer_max_price: max acceptable price ($)\n"
            "- target_min_price: min acceptable price ($)\n"
            "- zone_of_possible_agreement: range ($ - $)\n"
            "- likely_transaction_price: likely price ($)\n"
            "- premium_to_market: premium to market (%)\n"
            "- is_deal_feasible: is there a ZOPA?\n\n"
            "Return JSON:\n"
            "{\n"
            '  "accretion_dilution": {...},\n'
            '  "valuation_gap": {...}\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        accretion: AccretionDilution | None = None
        valuation: ValuationGap | None = None

        if not response.success or not response.content:
            return (accretion, valuation)

        try:
            data = json.loads(response.content)

            ad_data = data.get("accretion_dilution")
            if ad_data:
                accretion = AccretionDilution(
                    year_1_eps_impact=ad_data.get("year_1_eps_impact", ""),
                    year_2_eps_impact=ad_data.get("year_2_eps_impact", ""),
                    year_3_eps_impact=ad_data.get("year_3_eps_impact", ""),
                    is_accretive=bool(ad_data.get("is_accretive", False)),
                    accretive_conditions=ad_data.get("accretive_conditions", []),
                    pro_forma_revenue=ad_data.get("pro_forma_revenue", ""),
                    pro_forma_ebitda=ad_data.get("pro_forma_ebitda", ""),
                    deal_financing=ad_data.get("deal_financing", ""),
                )

            vg_data = data.get("valuation_gap")
            if vg_data:
                valuation = ValuationGap(
                    acquirer_max_price=vg_data.get("acquirer_max_price", ""),
                    target_min_price=vg_data.get("target_min_price", ""),
                    zone_of_possible_agreement=vg_data.get("zone_of_possible_agreement", ""),
                    likely_transaction_price=vg_data.get("likely_transaction_price", ""),
                    premium_to_market=vg_data.get("premium_to_market", ""),
                    is_deal_feasible=bool(vg_data.get("is_deal_feasible", False)),
                )

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (accretion, valuation)

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Assess cultural fit
    # ─────────────────────────────────────────────────────────────────────

    async def _assess_cultural_fit(
        self,
        question: str,
        short_list: list[AcquisitionTarget],
        cultural_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> CulturalFit | None:
        """Assess cultural fit using public data.

        Evaluates cultural compatibility using Glassdoor reviews, LinkedIn
        company pages, and employee sentiment. Cultural mismatch is the #1
        reason M&A deals fail to deliver synergies.
        """
        top_target = short_list[0] if short_list else None
        if not top_target:
            return None

        cultural_summary = json.dumps(
            [{"url": d.get("url", ""), "data": str(d.get("data", ""))[:300]} for d in cultural_data[:3]],
            default=str,
        )[:600]
        acquirer = context.get("company", context.get("acquirer", ""))

        prompt = (
            "You are the HYPERION M&A Analyst assessing cultural fit.\n\n"
            f"Question: {question}\n\n"
            f"Acquirer: {acquirer}\n"
            f"Top target: {top_target.company_name}\n\n"
            f"Cultural data (Glassdoor, LinkedIn):\n{cultural_summary or 'No cultural data available — use general knowledge'}\n\n"
            "Assess cultural compatibility:\n"
            "- acquirer_culture_summary: acquirer culture from public data\n"
            "- target_culture_summary: target culture from public data\n"
            "- compatibility_score: score (e.g., '7/10')\n"
            "- alignment_areas: areas of cultural alignment (list)\n"
            "- misalignment_areas: areas of misalignment (list)\n"
            "- glassdoor_ratings: ratings for both companies\n"
            "- integration_risk: low, medium, or high\n"
            "- data_basis: what data this is based on\n\n"
            "Cultural mismatch is the #1 reason M&A deals fail. Be specific:\n"
            "'Acquirer: mission-driven, hierarchical. Target: flat, engineering-"
            "led. Compatibility 6/10. Key misalignment: decision-making speed. "
            "Integration risk: HIGH.'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "acquirer_culture_summary": "...",\n'
            '  "target_culture_summary": "...",\n'
            '  "compatibility_score": "...",\n'
            '  "alignment_areas": ["..."],\n'
            '  "misalignment_areas": ["..."],\n'
            '  "glassdoor_ratings": {"acquirer": "...", "target": "..."},\n'
            '  "integration_risk": "low|medium|high",\n'
            '  "data_basis": "..."\n'
            "}\n"
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
            data = json.loads(response.content)
            return CulturalFit(
                acquirer_culture_summary=data.get("acquirer_culture_summary", ""),
                target_culture_summary=data.get("target_culture_summary", ""),
                compatibility_score=data.get("compatibility_score", ""),
                alignment_areas=data.get("alignment_areas", []),
                misalignment_areas=data.get("misalignment_areas", []),
                glassdoor_ratings=data.get("glassdoor_ratings", {}),
                integration_risk=data.get("integration_risk", "medium"),
                data_basis=data.get("data_basis", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    # ─────────────────────────────────────────────────────────────────────
    # Step 8: Build integration plan
    # ─────────────────────────────────────────────────────────────────────

    async def _build_integration_plan(
        self,
        question: str,
        short_list: list[AcquisitionTarget],
        synergy_analysis: SynergyAnalysis,
        cultural_fit: CulturalFit | None,
        context: dict[str, Any],
    ) -> IntegrationPlan:
        """Build a 100-day integration plan.

        Builds a 100-day integration plan with workstreams, milestones, owners,
        and risk flags. Identifies the top 3 integration risks. The deal is
        the easy part — integration is the hard part.
        """
        top_target = short_list[0] if short_list else None
        target_name = top_target.company_name if top_target else "the target"
        cultural_risk = cultural_fit.integration_risk if cultural_fit else "medium"

        prompt = (
            "You are the HYPERION M&A Analyst building a 100-day integration plan.\n\n"
            f"Question: {question}\n\n"
            f"Target: {target_name}\n"
            f"Synergies: {synergy_analysis.total_estimated_synergies} (realizable: {synergy_analysis.realizable_synergies})\n"
            f"Cultural integration risk: {cultural_risk}\n\n"
            "Build a 100-day integration plan with 4-6 workstreams:\n"
            "- Sales integration (cross-sell, account mapping)\n"
            "- IT systems migration\n"
            "- HR/cultural integration\n"
            "- Operations consolidation\n"
            "- Finance integration\n"
            "- Customer retention\n\n"
            "For each workstream:\n"
            "- workstream: name\n"
            "- owner: who owns it\n"
            "- day_1_actions: Day 1 actions (list)\n"
            "- day_30_milestones: Day 30 milestones (list)\n"
            "- day_100_milestones: Day 100 milestones (list)\n"
            "- risk_flags: risk flags (list)\n\n"
            "Also identify:\n"
            "- top_3_integration_risks: the top 3 risks\n"
            "- day_1_priorities: Day 1 priorities (list)\n"
            "- success_metrics: how to measure integration success (list)\n\n"
            "The deal is the easy part — integration is the hard part.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "workstreams": [{...}],\n'
            '  "top_3_integration_risks": ["..."],\n'
            '  "day_1_priorities": ["..."],\n'
            '  "success_metrics": ["..."]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return IntegrationPlan()

        try:
            data = json.loads(response.content)

            workstreams: list[IntegrationWorkstream] = []
            for ws in data.get("workstreams", []):
                workstreams.append(IntegrationWorkstream(
                    workstream=ws.get("workstream", "Unknown"),
                    owner=ws.get("owner", ""),
                    key_milestones=ws.get("key_milestones", []),
                    day_1_actions=ws.get("day_1_actions", []),
                    day_30_milestones=ws.get("day_30_milestones", []),
                    day_100_milestones=ws.get("day_100_milestones", []),
                    risk_flags=ws.get("risk_flags", []),
                ))

            return IntegrationPlan(
                workstreams=workstreams,
                top_3_integration_risks=data.get("top_3_integration_risks", []),
                day_1_priorities=data.get("day_1_priorities", []),
                success_metrics=data.get("success_metrics", []),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return IntegrationPlan()

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel M&A data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_ma_sub_agents(
        self,
        criteria: str,
        sector: str,
        short_list: list[AcquisitionTarget],
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel M&A data collection.

        Per §4.4, Agent 12:
        - Sub-agent A: Screen targets by [criteria] (MICRO, SearxNG + Obscura)
        - Sub-agent B: Pull financials for [target1, target2, target3] (MICRO, Alpha Vantage)
        - Sub-agent C: Find cultural reviews for [target companies] (FAST, Obscura)
        """
        target_names = [t.company_name for t in short_list[:3]]
        target_tickers = [t.ticker for t in short_list[:3] if t.ticker]

        sub_specs = [
            SubAgentSpec(
                question=f"Screen acquisition targets by criteria: {criteria[:200]} — find companies in {sector} matching strategic fit, size, geography",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"criteria": criteria, "sector": sector},
            ),
            SubAgentSpec(
                question=f"Pull financials for targets: {', '.join(target_tickers) or ', '.join(target_names)} — income statement, balance sheet, cash flow, company overview",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.ALPHA_VANTAGE],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"tickers": target_tickers, "companies": target_names},
            ),
            SubAgentSpec(
                question=f"Find cultural reviews for {', '.join(target_names)} — Glassdoor reviews, LinkedIn company pages, employee sentiment, culture ratings",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"companies": target_names},
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
        long_list_count: int,
        short_list_count: int,
        has_synergies: bool,
        has_accretion: bool,
        has_cultural_fit: bool,
        has_integration_plan: bool,
        has_valuation_gap: bool,
        has_financials: bool,
        sources_count: int,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 10+ long list, 3+ short list, synergies with reality discount,
              accretion/dilution, cultural fit, integration plan, valuation
              gap, financials, 5+ sources
        MEDIUM: 5+ long list, 2+ short list, synergies
        LOW: <5 long list, missing core analysis
        """
        if (long_list_count >= 10 and short_list_count >= 3
                and has_synergies and has_accretion and has_cultural_fit
                and has_integration_plan and has_valuation_gap
                and has_financials and sources_count >= 5):
            return ConfidenceLevel.HIGH
        if long_list_count >= 5 and short_list_count >= 2 and has_synergies:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 9-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> MAAnalysis:
        """Execute the M&A Analyst's 9-step methodology.

        Steps (§4.4, Agent 12):
        1. Define acquisition criteria with Engagement Director
        2. Search for potential targets (SearxNG + Jina + Obscura)
        3. Build long list → short list
        4. Pull financial data for targets (Alpha Vantage)
        5. Run synergy analysis
        6. Run accretion/dilution
        7. Assess cultural fit
        8. Build integration plan
        9. Produce M&AAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting M&A analysis: {self._question[:80]}",
        )

        # Extract context
        sector = self._context.get("sector", self._context.get("industry", ""))

        # Step 1: Define acquisition criteria
        await self._transition(AgentState.WORKING, "Step 1: Defining acquisition criteria with Engagement Director")
        self._acquisition_criteria = await self._define_acquisition_criteria(self._question, self._context)

        # Step 2: Search for potential targets
        await self._transition(AgentState.WORKING, "Step 2: Searching for potential targets (SearxNG + Jina + Obscura)")
        self._search_results, self._deal_database_data = await self._search_targets(self._acquisition_criteria, sector)

        # Step 3: Build long list → short list
        await self._transition(AgentState.WORKING, "Step 3: Building long list → short list")
        long_list, short_list = await self._build_target_lists(
            self._question, self._acquisition_criteria, self._search_results, self._deal_database_data, self._context,
        )

        # Spawn sub-agents for parallel data collection on short-listed targets
        if short_list:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning M&A data collection sub-agents")
            sub_findings = await self._spawn_ma_sub_agents(self._acquisition_criteria, sector, short_list)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 4: Pull financial data for targets
        await self._transition(AgentState.WORKING, "Step 4: Pulling financial data for short-listed targets (Alpha Vantage)")
        self._target_financials = await self._pull_target_financials(short_list)

        # Step 5: Run synergy analysis
        await self._transition(AgentState.WORKING, "Step 5: Running synergy analysis with reality discount")
        synergy_analysis = await self._run_synergy_analysis(
            self._question, short_list, self._target_financials, self._context,
        )

        # Step 6: Run accretion/dilution + valuation gap
        await self._transition(AgentState.WORKING, "Step 6: Running accretion/dilution + valuation gap analysis")
        accretion_dilution, valuation_gap = await self._run_accretion_dilution_and_valuation(
            self._question, short_list, self._target_financials, synergy_analysis, self._context,
        )

        # Step 7: Assess cultural fit
        await self._transition(AgentState.WORKING, "Step 7: Assessing cultural fit (Glassdoor, LinkedIn, employee sentiment)")
        cultural_fit = await self._assess_cultural_fit(
            self._question, short_list, self._cultural_data, self._context,
        )

        # Step 8: Build integration plan
        await self._transition(AgentState.WORKING, "Step 8: Building 100-day integration plan")
        integration_plan = await self._build_integration_plan(
            self._question, short_list, synergy_analysis, cultural_fit, self._context,
        )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            long_list_count=len(long_list),
            short_list_count=len(short_list),
            has_synergies=bool(synergy_analysis.total_estimated_synergies),
            has_accretion=accretion_dilution is not None and bool(accretion_dilution.year_1_eps_impact),
            has_cultural_fit=cultural_fit is not None and bool(cultural_fit.compatibility_score),
            has_integration_plan=bool(integration_plan.workstreams),
            has_valuation_gap=valuation_gap is not None and bool(valuation_gap.zone_of_possible_agreement),
            has_financials=bool(self._target_financials),
            sources_count=len(self._sources),
        )

        # Step 9: Produce M&AAnalysis model
        await self._transition(AgentState.WORKING, "Step 9: Producing M&AAnalysis model")

        top_risks = integration_plan.top_3_integration_risks if integration_plan else []
        if cultural_fit and cultural_fit.integration_risk == "high":
            top_risks.append(f"Cultural integration risk: HIGH (compatibility {cultural_fit.compatibility_score})")

        analysis = MAAnalysis(
            acquisition_criteria=self._acquisition_criteria,
            long_list=long_list,
            short_list=short_list,
            synergy_analysis=synergy_analysis,
            valuation_gap=valuation_gap,
            accretion_dilution=accretion_dilution,
            cultural_fit=cultural_fit,
            integration_plan=integration_plan,
            top_integration_risks=top_risks,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus
        # Publish top short-list target as a finding
        if short_list:
            top_target = short_list[0]
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="acquisition_target",
                title=f"Top Acquisition Target: {top_target.company_name}",
                content=(
                    f"{top_target.company_name} — {top_target.description}. "
                    f"Revenue: {top_target.revenue}. Employees: {top_target.employees}. "
                    f"Strategic fit: {top_target.strategic_fit}. "
                    f"Rationale: {top_target.acquisition_rationale}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish synergy analysis as a finding
        if synergy_analysis and synergy_analysis.total_estimated_synergies:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="synergy_analysis",
                title=f"Synergies: {synergy_analysis.total_estimated_synergies} (realizable: {synergy_analysis.realizable_synergies})",
                content=(
                    f"Total estimated: {synergy_analysis.total_estimated_synergies}. "
                    f"Reality discount: {synergy_analysis.reality_discount_percentage}. "
                    f"Realizable: {synergy_analysis.realizable_synergies}. "
                    f"Timeline: {synergy_analysis.synergy_timeline}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish cultural fit as a finding
        if cultural_fit and cultural_fit.compatibility_score:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="cultural_fit",
                title=f"Cultural Fit: {cultural_fit.compatibility_score} (risk: {cultural_fit.integration_risk})",
                content=(
                    f"Compatibility: {cultural_fit.compatibility_score}. "
                    f"Alignment: {cultural_fit.alignment_areas[:2] if cultural_fit.alignment_areas else []}. "
                    f"Misalignment: {cultural_fit.misalignment_areas[:2] if cultural_fit.misalignment_areas else []}. "
                    f"Integration risk: {cultural_fit.integration_risk}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish the full MAAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "ma_analysis": analysis.model_dump(),
                "long_list_count": len(long_list),
                "short_list_count": len(short_list),
                "has_synergies": synergy_analysis is not None and bool(synergy_analysis.total_estimated_synergies),
                "has_accretion": accretion_dilution is not None,
                "has_cultural_fit": cultural_fit is not None,
                "has_integration_plan": integration_plan is not None and bool(integration_plan.workstreams),
                "has_valuation_gap": valuation_gap is not None,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"M&A analysis complete: {len(long_list)} long list, "
            f"{len(short_list)} short list, "
            f"synergies={'yes' if synergy_analysis.total_estimated_synergies else 'no'}, "
            f"accretion={'yes' if accretion_dilution else 'no'}, "
            f"cultural={'yes' if cultural_fit else 'no'}, "
            f"integration={'yes' if integration_plan and integration_plan.workstreams else 'no'}, "
            f"confidence={confidence.value}",
        )

        return analysis
