"""
HYPERION Financial Analyst — Agent 5, the financial modeling specialist.

This is NOT a generic "analyze the finances" agent. This is a specialist
with 7 proprietary analytical frameworks:

- DCF (Discounted Cash Flow): Explicit forecast period, terminal value,
  WACC, sensitivity table on discount rate × terminal growth rate.
- LBO (Leveraged Buyout): Debt structure, interest coverage, IRR, exit
  assumptions. Used for M&A support.
- Comparable company analysis: 5-10 comparables, EV/Revenue, EV/EBITDA,
  P/E multiples applied to target.
- Unit economics: LTV, CAC, LTV/CAC, payback period, gross margin,
  contribution margin, burn rate.
- Sensitivity analysis: Two-variable tables (price × volume, discount ×
  terminal growth, penetration × ARPU).
- Scenario modeling: Best/base/worst case with probabilities and EV.
- Break-even analysis: Break-even units, revenue, contribution margin,
  margin of safety.

It NEVER reports a single valuation number. It always reports a range
with sensitivity tables. It always identifies the key value drivers —
the 2-3 assumptions that account for 80% of the valuation variance. It
always cross-validates DCF with comparable company analysis. If the DCF
says $100M but comparables say $50M, it flags the discrepancy and
explains it. (§4.4, Agent 5)

Model Tier: STANDARD+ (STANDARD for research, STRONG for modeling)
Tools: Alpha Vantage, FRED, SearxNG, Jina
Sub-agents: Max 3 — financial statements, margin benchmarks, cost structure
Output: FinancialAnalysis (DCF, comparables, unit economics, sensitivity,
        scenarios, break-even, key value drivers, confidence, sources)

Methodology (§4.4, Agent 5):
1. Pull comparable company financials (Alpha Vantage)
2. Pull macroeconomic inputs for discount rates (FRED)
3. Search for industry benchmarks (SearxNG + Jina)
4. Build DCF model with sensitivity tables
5. Build comparable company analysis
6. Calculate unit economics
7. Run scenario analysis (best/base/worst)
8. Calculate break-even
9. Produce FinancialAnalysis model
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
    FinancialAnalysis,
    FinancialMetric,
    KeyFinding,
    Source,
    SourceCredibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


FINANCIAL_ANALYST_SPEC = AgentSpec(
    name=AgentName.FINANCIAL_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Financial Analyst",
    model_tier=ModelTier.STRONG,
    tools=[
        ToolName.ALPHA_VANTAGE,
        ToolName.FRED,
        ToolName.SEC_EDGAR,
        ToolName.WORLD_BANK,
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="DCF (Discounted Cash Flow)",
            description=(
                "Build a DCF model with: (1) explicit forecast period (5-7 years), "
                "(2) free cash flow projections from revenue/margin assumptions, "
                "(3) terminal value via Gordon growth or exit multiple, (4) WACC "
                "calculation (cost of equity via CAPM, cost of debt after-tax, "
                "capital structure weights), (5) sensitivity table on discount rate "
                "× terminal growth rate. The DCF is only as good as its assumptions "
                "— each assumption must cite a source."
            ),
            inputs=["revenue_projections", "margin_assumptions", "capex", "wacc_inputs", "terminal_growth_rate"],
            outputs=["dcf_valuation", "sensitivity_table", "wacc_breakdown", "key_assumptions"],
        ),
        SkillSpec(
            name="LBO (Leveraged Buyout)",
            description=(
                "Model LBO scenarios with: (1) debt structure (senior debt, subordinated "
                "debt, equity contribution), (2) interest coverage ratios, (3) IRR "
                "calculation for equity holders, (4) exit assumptions (exit multiple, "
                "timing). Used for M&A support — the M&A Analyst requests LBO scenarios "
                "when evaluating leveraged acquisitions."
            ),
            inputs=["target_financials", "debt_structure", "exit_multiple", "holding_period"],
            outputs=["lbo_valuation", "irr", "interest_coverage", "debt_amortization_schedule"],
        ),
        SkillSpec(
            name="Comparable company analysis",
            description=(
                "Identify 5-10 comparable public companies, pull their trading multiples "
                "(EV/Revenue, EV/EBITDA, P/E), and apply to the target company. "
                "Comparables must be in the same industry, similar growth stage, and "
                "similar geography. Adjust for growth premium/discount. The comp set "
                "must be justified — don't just grab the nearest 10 tickers."
            ),
            inputs=["target_company", "industry", "geography", "growth_stage"],
            outputs=["comparable_multiples", "valuation_range", "comp_set_justification"],
        ),
        SkillSpec(
            name="Unit economics",
            description=(
                "Calculate LTV, CAC, LTV/CAC ratio, payback period, gross margin, "
                "contribution margin, and burn rate. Identify which unit economics "
                "assumptions are the most sensitive. LTV/CAC > 3 is healthy, < 1 is "
                "unsustainable. Payback < 12 months is strong, > 24 months is concerning. "
                "Each metric must show the formula, inputs, and source."
            ),
            inputs=["arpu", "gross_margin", "churn_rate", "cac", "sales_efficiency"],
            outputs=["ltv", "cac", "ltv_cac_ratio", "payback_period", "gross_margin", "burn_rate"],
        ),
        SkillSpec(
            name="Sensitivity analysis",
            description=(
                "Build two-variable sensitivity tables showing how the recommendation "
                "changes under different assumptions. Common pairs: price × volume, "
                "discount rate × terminal growth, penetration × ARPU. The table shows "
                "the valuation/recommendation in each cell, making it clear which "
                "assumptions matter and which don't."
            ),
            inputs=["base_case_assumptions", "sensitivity_variables", "valuation_model"],
            outputs=["sensitivity_tables", "critical_assumptions", "tolerance_ranges"],
        ),
        SkillSpec(
            name="Scenario modeling",
            description=(
                "Build best case, base case, worst case scenarios with assigned "
                "probabilities and expected values. Best case is not 'everything goes "
                "right' — it's 'the upside scenario with a 20% probability.' Worst "
                "case is not 'everything goes wrong' — it's 'the downside scenario with "
                "a 20% probability.' Base case is the most likely (60%). Expected "
                "value = sum of (scenario_value × probability)."
            ),
            inputs=["base_case_model", "upside_drivers", "downside_drivers", "probabilities"],
            outputs=["best_case", "base_case", "worst_case", "expected_value", "scenario_probabilities"],
        ),
        SkillSpec(
            name="Break-even analysis",
            description=(
                "Calculate break-even point in units and revenue, contribution margin "
                "per unit, and margin of safety. Break-even = fixed costs / contribution "
                "margin per unit. Margin of safety = (actual sales - break-even sales) / "
                "actual sales. A margin of safety < 10% is risky, > 30% is comfortable."
            ),
            inputs=["fixed_costs", "variable_costs", "price_per_unit", "actual_sales"],
            outputs=["break_even_units", "break_even_revenue", "contribution_margin", "margin_of_safety"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Financial Analyst — the specialist who builds financial "
        "models, evaluates unit economics, runs valuations, and assesses financial "
        "viability. You answer 'do the numbers work?'\n\n"
        "Your proprietary frameworks:\n"
        "1. DCF: 5-7 year explicit forecast, terminal value (Gordon growth or exit "
        "multiple), WACC (CAPM for cost of equity, after-tax cost of debt), sensitivity "
        "table on discount rate × terminal growth.\n"
        "2. LBO: Debt structure, interest coverage, IRR, exit assumptions. For M&A support.\n"
        "3. Comparable company analysis: 5-10 comparables, EV/Revenue, EV/EBITDA, P/E. "
        "Comp set must be justified — same industry, growth stage, geography.\n"
        "4. Unit economics: LTV, CAC, LTV/CAC (>3 healthy, <1 unsustainable), payback "
        "(<12mo strong, >24mo concerning), gross margin, contribution margin, burn rate.\n"
        "5. Sensitivity analysis: Two-variable tables (price×volume, discount×terminal "
        "growth, penetration×ARPU). Shows which assumptions matter.\n"
        "6. Scenario modeling: Best (20%), base (60%), worst (20%) with expected value.\n"
        "7. Break-even: Units, revenue, contribution margin, margin of safety "
        "(<10% risky, >30% comfortable).\n\n"
        "Rules:\n"
        "- NEVER report a single valuation number. ALWAYS report a range with "
        "sensitivity tables.\n"
        "- ALWAYS identify the key value drivers — the 2-3 assumptions that account "
        "for 80% of the valuation variance.\n"
        "- ALWAYS cross-validate DCF with comparable company analysis. If DCF says "
        "$100M but comparables say $50M, flag the discrepancy and explain it.\n"
        "- Each assumption must cite a source. No unsourced financial assumptions.\n"
        "- WACC must show the full calculation: risk-free rate (FRED), equity risk "
        "premium, beta, cost of debt, capital structure.\n"
        "- Terminal growth rate must be ≤ long-term GDP growth (2-3%). Higher is "
        "unrealistic.\n"
        "- Scenario probabilities must sum to 100%.\n"
        "- Sensitivity tables must show the valuation in each cell, not just ranges.\n\n"
        "You can spawn up to 3 sub-agents for parallel data collection:\n"
        "- Sub-agent A: Pull financial statements for [company1, company2, company3] "
        "(MICRO, Alpha Vantage)\n"
        "- Sub-agent B: Find industry margin benchmarks for [industry] (MICRO, "
        "SearxNG + Jina)\n"
        "- Sub-agent C: Find cost structure data for [business model] (FAST, "
        "SearxNG + Jina)\n\n"
        "Your output is a FinancialAnalysis Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned when the question involves financial viability, valuation, "
                     "unit economics, or investment decision (GO_NO_GO, MARKET_ENTRY, "
                     "MA_EVALUATION types)",
    max_sub_agents=3,
    output_model="FinancialAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Financial Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class FinancialAnalyst(BaseAgent):
    """Agent 5: The financial modeling specialist.

    Builds DCF models, comparable company analysis, unit economics,
    sensitivity tables, scenario models, and break-even analysis.
    NEVER reports a single valuation — always a range with sensitivity.
    Always cross-validates DCF with comparables. Always identifies key
    value drivers. (§4.4, Agent 5)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Pulls comparable company financials (Alpha Vantage)
    3. Pulls macroeconomic inputs for discount rates (FRED)
    4. Searches for industry benchmarks (SearxNG + Jina)
    5. Builds DCF model with sensitivity tables
    6. Builds comparable company analysis
    7. Calculates unit economics
    8. Runs scenario analysis and break-even
    9. Produces FinancialAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or FINANCIAL_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._comparable_companies: list[dict[str, Any]] = []
        self._macro_data: dict[str, Any] = {}
        self._industry_benchmarks: list[dict[str, Any]] = []
        self._cost_structure_data: list[dict[str, Any]] = []
        self._search_results: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Financial Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., M&A Analyst requesting
          LBO scenarios, Synthesis Lead requesting key value drivers)
        - FINDINGS: receives TAM from Market Analyst for DCF revenue projections
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "financial_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            # Collect TAM from Market Analyst for DCF revenue projections
            finding = msg.finding
            if finding is not None and finding.finding_type == "market_size":
                self._context["tam"] = finding.content

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "lbo_scenario":
                # M&A Analyst requesting LBO model for a target
                # Handled during run() — just note the request
                pass
            elif request_type == "key_value_drivers":
                # Synthesis Lead requesting key value drivers for confidence calibration
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Pull comparable company financials (Alpha Vantage)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_comparable_financials(self, tickers: list[str]) -> list[dict[str, Any]]:
        """Pull financial statements, ratios, and market data for comparable companies.

        Uses Alpha Vantage to get: income statement, balance sheet, cash flow,
        key ratios (P/E, EV/EBITDA, ROE, margins), and market capitalization.
        These feed into both the DCF (for WACC inputs) and comparable company
        analysis (for trading multiples).
        """
        companies: list[dict[str, Any]] = []

        try:
            av = self.get_tool(ToolName.ALPHA_VANTAGE)

            for ticker in tickers[:10]:  # Limit to 10 comparables
                overview = await av.get_overview(ticker)
                income_stmt = await av.get_income_statement(ticker)
                balance_sheet = await av.get_balance_sheet(ticker)
                cash_flow = await av.get_cash_flow(ticker)

                if overview:
                    company_data = {
                        "ticker": ticker,
                        "name": overview.get("Name", ""),
                        "sector": overview.get("Sector", ""),
                        "industry": overview.get("Industry", ""),
                        "market_cap": overview.get("MarketCapitalization", ""),
                        "pe_ratio": overview.get("PERatio", ""),
                        "ev_to_revenue": overview.get("EVToRevenue", ""),
                        "ev_to_ebitda": overview.get("EVToEBITDA", ""),
                        "profit_margin": overview.get("ProfitMargin", ""),
                        "roe": overview.get("ReturnOnEquityTTM", ""),
                        "revenue_ttm": overview.get("RevenueTTM", ""),
                        "gross_margin_ttm": overview.get("GrossProfitTTM", ""),
                        "beta": overview.get("Beta", ""),
                        "income_statement": income_stmt,
                        "balance_sheet": balance_sheet,
                        "cash_flow": cash_flow,
                    }
                    companies.append(company_data)

                    self._sources.append(Source(
                        id=f"src_{len(self._sources):03d}",
                        title=f"Alpha Vantage — {ticker} ({overview.get('Name', '')})",
                        url=f"https://www.alphavantage.co/query?symbol={ticker}",
                        credibility=SourceCredibility.GOVERNMENT,
                        key_data=f"P/E: {overview.get('PERatio', 'N/A')}, "
                                 f"EV/Revenue: {overview.get('EVToRevenue', 'N/A')}, "
                                 f"Margin: {overview.get('ProfitMargin', 'N/A')}",
                    ))

        except (ValueError, AttributeError, RuntimeError):
            pass

        return companies

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Pull macroeconomic inputs for discount rates (FRED)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_macro_inputs(self, geography: str = "US") -> dict[str, Any]:
        """Pull macroeconomic data for DCF discount rates and scenario modeling.

        Key inputs:
        - Risk-free rate (10-year Treasury yield) → CAPM input
        - Inflation rate → nominal vs real cash flow adjustment
        - GDP growth → terminal growth rate ceiling
        - Interest rates → cost of debt
        """
        macro: dict[str, Any] = {}

        try:
            fred = self.get_tool(ToolName.FRED)

            # 10-year Treasury yield (risk-free rate proxy)
            tnx_data = await fred.get_series("DGS10")
            if tnx_data:
                macro["risk_free_rate"] = tnx_data

            # CPI (inflation)
            cpi_data = await fred.get_series("CPIAUCSL")
            if cpi_data:
                macro["inflation"] = cpi_data

            # GDP growth (terminal growth rate ceiling)
            gdp_data = await fred.get_series("GDP")
            if gdp_data:
                macro["gdp_growth"] = gdp_data

            # Federal funds rate (cost of debt proxy)
            fed_data = await fred.get_series("FEDFUNDS")
            if fed_data:
                macro["fed_funds_rate"] = fed_data

            self._sources.append(Source(
                id=f"src_{len(self._sources):03d}",
                title=f"FRED Macroeconomic Data — {geography}",
                url="https://fred.stlouisfed.org",
                credibility=SourceCredibility.GOVERNMENT,
                key_data="Risk-free rate, inflation, GDP growth, Fed funds rate",
            ))

        except (ValueError, AttributeError, RuntimeError):
            pass

        return macro

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Search for industry benchmarks (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_industry_benchmarks(self, industry: str) -> list[dict[str, Any]]:
        """Search for industry financial benchmarks, margin data, cost structures.

        Uses SearxNG to find industry reports and Jina to extract content.
        Benchmarks are critical for: (1) validating margin assumptions in DCF,
        (2) checking if unit economics are industry-appropriate, (3) setting
        realistic scenario ranges.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{industry} industry profit margins benchmark",
                f"{industry} industry CAC LTV benchmarks",
                f"{industry} industry cost structure breakdown",
                f"{industry} industry average growth rates",
            ]

            for pattern in query_patterns:
                search_results = await searxng.search(pattern, max_results=8)
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

            # Extract content from top URLs
            try:
                jina = self.get_tool(ToolName.JINA)
                top_urls = [r["url"] for r in results[:5] if r.get("url")]
                for url in top_urls:
                    read_result = await jina.read(url)
                    if read_result and (read_result.markdown or read_result.content):
                        content = read_result.markdown or read_result.content
                    else:
                        continue
                    if content:
                        results.append({
                            "url": url,
                            "extracted_content": content[:15000],
                            "source": "jina",
                        })
            except (ValueError, AttributeError, RuntimeError):
                pass

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Build DCF model with sensitivity tables
    # ─────────────────────────────────────────────────────────────────────

    async def _build_dcf_model(
        self,
        question: str,
        comparable_data: list[dict[str, Any]],
        macro_data: dict[str, Any],
        benchmark_data: list[dict[str, Any]],
        tam_from_market: str | None,
    ) -> tuple[FinancialMetric, list[FinancialMetric]]:
        """Build a DCF model with sensitivity tables.

        DCF components:
        1. Revenue projections (5-7 years) based on TAM and adoption curve
        2. Margin assumptions from industry benchmarks
        3. Free cash flow = EBIT × (1 - tax rate) + D&A - CapEx - ΔWC
        4. WACC = E/(D+E) × Cost of Equity + D/(D+E) × Cost of Debt × (1 - tax)
           - Cost of Equity = Risk-free rate + Beta × Equity Risk Premium (CAPM)
           - Cost of Debt = Risk-free rate + Credit spread
        5. Terminal Value = FCF_final × (1 + g) / (WACC - g) (Gordon growth)
        6. Enterprise Value = Σ FCF / (1 + WACC)^t + TV / (1 + WACC)^T
        7. Sensitivity table: WACC (±2%) × Terminal growth (±1%)

        Returns (dcf_valuation, sensitivity_tables).
        """
        # Prepare data summaries for LLM
        comp_summary = "\n".join(
            f"- {c.get('name', c.get('ticker', ''))}: "
            f"Revenue {c.get('revenue_ttm', 'N/A')}, "
            f"Margin {c.get('profit_margin', 'N/A')}, "
            f"Beta {c.get('beta', 'N/A')}"
            for c in comparable_data[:5]
        )
        macro_summary = json.dumps(macro_data, default=str)[:1500] if macro_data else "No macro data"
        benchmark_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in benchmark_data[:8]
        )

        prompt = (
            "You are the Financial Analyst building a DCF model.\n\n"
            f"Question: {question}\n"
            f"TAM from Market Analyst: {tam_from_market or 'Not available — estimate from benchmarks'}\n\n"
            f"Comparable companies:\n{comp_summary}\n\n"
            f"Macroeconomic data:\n{macro_summary}\n\n"
            f"Industry benchmarks:\n{benchmark_summary}\n\n"
            "Build a DCF model:\n"
            "1. Project revenue for 5-7 years based on TAM and adoption curve\n"
            "2. Estimate margins from industry benchmarks (cite source)\n"
            "3. Calculate free cash flow: EBIT × (1 - tax) + D&A - CapEx - ΔWC\n"
            "4. Calculate WACC using CAPM:\n"
            "   - Risk-free rate from FRED 10-year Treasury\n"
            "   - Equity risk premium (use 5-6% if no data)\n"
            "   - Beta from comparable companies\n"
            "   - Cost of debt = risk-free rate + credit spread\n"
            "   - WACC = weighted average\n"
            "5. Terminal value via Gordon growth (g ≤ 3% — GDP ceiling)\n"
            "6. Discount everything to present value\n"
            "7. Build sensitivity table: WACC (±2% in 0.5% steps) × Terminal growth (0-3% in 0.5% steps)\n\n"
            "Return JSON:\n"
            "{\n"
            '  "dcf_value": "range string",\n'
            '  "dcf_low": number,\n'
            '  "dcf_high": number,\n'
            '  "dcf_base": number,\n'
            '  "unit": "$",\n'
            '  "wacc": "calculated WACC with breakdown",\n'
            '  "wacc_components": {"risk_free_rate": ..., "erp": ..., "beta": ..., "cost_of_equity": ..., "cost_of_debt": ..., "wacc": ...},\n'
            '  "terminal_growth_rate": number,\n'
            '  "terminal_value": number,\n'
            '  "revenue_projections": ["year1: ...", "year2: ...", ...],\n'
            '  "margin_assumptions": ["..."],\n'
            '  "fcf_projections": ["year1: ...", ...],\n'
            '  "key_assumptions": ["assumption1", "assumption2", ...],\n'
            '  "sensitivity_table": {\n'
            '    "wacc_8%": {"g_0%": number, "g_0.5%": number, "g_1%": number, "g_1.5%": number, "g_2%": number, "g_2.5%": number, "g_3%": number},\n'
            '    "wacc_8.5%": {...},\n'
            '    "wacc_9%": {...},\n'
            '    "wacc_9.5%": {...},\n'
            '    "wacc_10%": {...},\n'
            '    "wacc_10.5%": {...},\n'
            '    "wacc_11%": {...},\n'
            '    "wacc_11.5%": {...},\n'
            '    "wacc_12%": {...}\n'
            '  }\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return (
                FinancialMetric(
                    name="DCF Valuation",
                    value="Unable to calculate — insufficient data",
                    unit="$",
                    assumptions=["DCF model failed — LLM error or no data"],
                ),
                [],
            )

        try:
            data = json.loads(response.content)

            dcf = FinancialMetric(
                name="DCF Valuation",
                value=data.get("dcf_value", "Unknown"),
                unit=data.get("unit", "$"),
                low_estimate=data.get("dcf_low"),
                high_estimate=data.get("dcf_high"),
                base_case=data.get("dcf_base"),
                assumptions=data.get("key_assumptions", []),
                sensitivity=data.get("sensitivity_table"),
                sources=[s for s in self._sources if s.credibility in (
                    SourceCredibility.GOVERNMENT,
                    SourceCredibility.INDUSTRY_REPORT,
                )][:5],
            )

            # Build sensitivity table as a FinancialMetric
            sensitivity_metrics: list[FinancialMetric] = []
            sens_table = data.get("sensitivity_table")
            if sens_table and isinstance(sens_table, dict):
                sensitivity_metrics.append(FinancialMetric(
                    name="DCF Sensitivity (WACC × Terminal Growth)",
                    value=f"Range: {data.get('dcf_low', '?')} - {data.get('dcf_high', '?')}",
                    unit="$",
                    assumptions=["WACC varied ±2%, terminal growth 0-3%"],
                    sensitivity=sens_table,
                    sources=self._sources[:2],
                ))

            return dcf, sensitivity_metrics

        except (json.JSONDecodeError, ValueError):
            return (
                FinancialMetric(
                    name="DCF Valuation",
                    value="Parse error",
                    unit="$",
                    assumptions=["DCF model failed — parsing error"],
                ),
                [],
            )

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Build comparable company analysis
    # ─────────────────────────────────────────────────────────────────────

    async def _build_comparable_analysis(
        self,
        question: str,
        comparable_data: list[dict[str, Any]],
    ) -> FinancialMetric:
        """Build comparable company analysis using trading multiples.

        Pulls EV/Revenue, EV/EBITDA, and P/E for 5-10 comparable public
        companies. Applies median multiples to the target's metrics to
        get a valuation range. Adjusts for growth premium/discount.
        """
        comp_summary = "\n".join(
            f"- {c.get('name', c.get('ticker', ''))}: "
            f"EV/Revenue {c.get('ev_to_revenue', 'N/A')}, "
            f"EV/EBITDA {c.get('ev_to_ebitda', 'N/A')}, "
            f"P/E {c.get('pe_ratio', 'N/A')}, "
            f"Revenue {c.get('revenue_ttm', 'N/A')}, "
            f"Margin {c.get('profit_margin', 'N/A')}"
            for c in comparable_data
        )

        prompt = (
            "You are the Financial Analyst building comparable company analysis.\n\n"
            f"Question: {question}\n\n"
            f"Comparable companies:\n{comp_summary}\n\n"
            "Build the comp analysis:\n"
            "1. Calculate median EV/Revenue, EV/EBITDA, and P/E across the comp set\n"
            "2. Apply median multiples to the target's projected revenue/EBITDA/earnings\n"
            "3. Adjust for growth premium (if target grows faster than comp median) "
            "or discount (if slower)\n"
            "4. Produce a valuation range (low = 25th percentile multiple, "
            "high = 75th percentile)\n"
            "5. Justify the comp set — why these companies are comparable\n\n"
            "Return JSON:\n"
            "{\n"
            '  "comp_value": "range string",\n'
            '  "comp_low": number,\n'
            '  "comp_high": number,\n'
            '  "comp_base": number,\n'
            '  "unit": "$",\n'
            '  "median_ev_revenue": number,\n'
            '  "median_ev_ebitda": number,\n'
            '  "median_pe": number,\n'
            '  "comp_set_justification": "...",\n'
            '  "growth_adjustment": "premium/discount applied and why",\n'
            '  "assumptions": ["..."]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return FinancialMetric(
                name="Comparable Company Analysis",
                value="Unable to calculate — insufficient data",
                unit="$",
                assumptions=["Comp analysis failed — no data or LLM error"],
            )

        try:
            data = json.loads(response.content)
            return FinancialMetric(
                name="Comparable Company Analysis",
                value=data.get("comp_value", "Unknown"),
                unit=data.get("unit", "$"),
                low_estimate=data.get("comp_low"),
                high_estimate=data.get("comp_high"),
                base_case=data.get("comp_base"),
                assumptions=data.get("assumptions", []) + [
                    f"Median EV/Revenue: {data.get('median_ev_revenue', 'N/A')}",
                    f"Median EV/EBITDA: {data.get('median_ev_ebitda', 'N/A')}",
                    f"Growth adjustment: {data.get('growth_adjustment', 'None')}",
                ],
                sources=[s for s in self._sources if "alpha_vantage" in s.url.lower() or "alphavantage" in s.url.lower()][:5],
            )
        except (json.JSONDecodeError, ValueError):
            return FinancialMetric(
                name="Comparable Company Analysis",
                value="Parse error",
                unit="$",
                assumptions=["Comp analysis failed — parsing error"],
            )

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Calculate unit economics
    # ─────────────────────────────────────────────────────────────────────

    async def _calculate_unit_economics(
        self,
        question: str,
        benchmark_data: list[dict[str, Any]],
    ) -> list[FinancialMetric]:
        """Calculate LTV, CAC, LTV/CAC, payback period, gross margin, burn rate.

        LTV = ARPU × Gross Margin × (1 / Churn Rate)
        CAC = Sales & Marketing Spend / New Customers
        LTV/CAC > 3 is healthy, < 1 is unsustainable
        Payback = CAC / (ARPU × Gross Margin) — in months
        """
        benchmark_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in benchmark_data[:8]
        )

        prompt = (
            "You are the Financial Analyst calculating unit economics.\n\n"
            f"Question: {question}\n\n"
            f"Industry benchmarks:\n{benchmark_summary}\n\n"
            "Calculate unit economics:\n"
            "1. ARPU (Average Revenue Per User) — with source\n"
            "2. Gross Margin — with source\n"
            "3. Churn Rate — with source\n"
            "4. LTV = ARPU × Gross Margin × (1 / Churn)\n"
            "5. CAC — with source (or estimate from industry benchmarks)\n"
            "6. LTV/CAC ratio (>3 healthy, <1 unsustainable)\n"
            "7. Payback Period = CAC / (ARPU × Gross Margin) — in months\n"
            "8. Contribution Margin = (ARPU - Variable Cost) / ARPU\n"
            "9. Burn Rate (if applicable)\n"
            "10. Identify which assumption is most sensitive\n\n"
            "Return JSON:\n"
            "{\n"
            '  "arpu": {"value": number, "unit": "$", "source": "..."},\n'
            '  "gross_margin": {"value": number, "unit": "%", "source": "..."},\n'
            '  "churn_rate": {"value": number, "unit": "%", "source": "..."},\n'
            '  "ltv": {"value": number, "unit": "$", "formula": "ARPU × GM × (1/Churn)"},\n'
            '  "cac": {"value": number, "unit": "$", "source": "..."},\n'
            '  "ltv_cac": {"value": number, "unit": "x", "health": "healthy|concerning|unsustainable"},\n'
            '  "payback_months": {"value": number, "unit": "months", "health": "strong|ok|concerning"},\n'
            '  "contribution_margin": {"value": number, "unit": "%"},\n'
            '  "burn_rate": {"value": "number or N/A", "unit": "$/month"},\n'
            '  "most_sensitive_assumption": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        metrics: list[FinancialMetric] = []

        if not response.success or not response.content:
            return metrics

        try:
            data = json.loads(response.content)

            # Build FinancialMetric for each unit economic
            for key, label in [
                ("ltv", "LTV (Lifetime Value)"),
                ("cac", "CAC (Customer Acquisition Cost)"),
                ("ltv_cac", "LTV/CAC Ratio"),
                ("payback_months", "Payback Period"),
                ("gross_margin", "Gross Margin"),
                ("contribution_margin", "Contribution Margin"),
            ]:
                item = data.get(key, {})
                if isinstance(item, dict):
                    metrics.append(FinancialMetric(
                        name=label,
                        value=item.get("value", "Unknown"),
                        unit=item.get("unit", ""),
                        assumptions=[item.get("source", ""), item.get("formula", ""), item.get("health", "")],
                        sources=self._sources[:2],
                    ))

        except (json.JSONDecodeError, ValueError):
            pass

        return metrics

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Run scenario analysis (best/base/worst)
    # ─────────────────────────────────────────────────────────────────────

    async def _run_scenario_analysis(
        self,
        question: str,
        dcf_valuation: FinancialMetric,
        comp_valuation: FinancialMetric,
        benchmark_data: list[dict[str, Any]],
    ) -> dict[str, FinancialMetric]:
        """Build best/base/worst case scenarios with probabilities and expected value.

        Best case (20%): Upside drivers materialize
        Base case (60%): Most likely outcome
        Worst case (20%): Downside drivers materialize
        Expected value = Σ (scenario_value × probability)
        """
        prompt = (
            "You are the Financial Analyst running scenario analysis.\n\n"
            f"Question: {question}\n\n"
            f"DCF valuation: {dcf_valuation.value} (base: {dcf_valuation.base_case})\n"
            f"Comp valuation: {comp_valuation.value} (base: {comp_valuation.base_case})\n\n"
            "Build three scenarios:\n"
            "1. Best case (20% probability): What upside drivers could materialize?\n"
            "   - Higher growth, better margins, lower CAC, faster payback\n"
            "2. Base case (60% probability): Most likely outcome\n"
            "   - Use DCF/comp base case as starting point\n"
            "3. Worst case (20% probability): What could go wrong?\n"
            "   - Lower growth, margin compression, higher CAC, slower payback\n\n"
            "Probabilities must sum to 100%.\n"
            "Expected value = best × 0.2 + base × 0.6 + worst × 0.2\n\n"
            "Return JSON:\n"
            "{\n"
            '  "best_case": {"value": "range", "low": number, "high": number, "base": number, "probability": 0.2, "drivers": ["..."]},\n'
            '  "base_case": {"value": "range", "low": number, "high": number, "base": number, "probability": 0.6, "drivers": ["..."]},\n'
            '  "worst_case": {"value": "range", "low": number, "high": number, "base": number, "probability": 0.2, "drivers": ["..."]},\n'
            '  "expected_value": number,\n'
            '  "unit": "$"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        scenarios: dict[str, FinancialMetric] = {}

        if not response.success or not response.content:
            return scenarios

        try:
            data = json.loads(response.content)
            unit = data.get("unit", "$")

            for key, label in [("best_case", "Best Case (20%)"), ("base_case", "Base Case (60%)"), ("worst_case", "Worst Case (20%)")]:
                scenario = data.get(key, {})
                if isinstance(scenario, dict):
                    scenarios[label] = FinancialMetric(
                        name=label,
                        value=scenario.get("value", "Unknown"),
                        unit=unit,
                        low_estimate=scenario.get("low"),
                        high_estimate=scenario.get("high"),
                        base_case=scenario.get("base"),
                        assumptions=scenario.get("drivers", []) + [f"Probability: {scenario.get('probability', 0)}"],
                        sources=self._sources[:3],
                    )

        except (json.JSONDecodeError, ValueError):
            pass

        return scenarios

    # ─────────────────────────────────────────────────────────────────────
    # Step 8: Calculate break-even
    # ─────────────────────────────────────────────────────────────────────

    async def _calculate_break_even(
        self,
        question: str,
        unit_economics: list[FinancialMetric],
        benchmark_data: list[dict[str, Any]],
    ) -> FinancialMetric:
        """Calculate break-even point in units and revenue.

        Break-even = Fixed Costs / Contribution Margin per unit
        Margin of Safety = (Actual Sales - Break-even Sales) / Actual Sales
        < 10% is risky, > 30% is comfortable.
        """
        ue_summary = "\n".join(
            f"- {m.name}: {m.value} {m.unit}"
            for m in unit_economics
        )

        prompt = (
            "You are the Financial Analyst calculating break-even analysis.\n\n"
            f"Question: {question}\n\n"
            f"Unit economics:\n{ue_summary}\n\n"
            "Calculate break-even:\n"
            "1. Estimate fixed costs (salaries, rent, infrastructure) — with source\n"
            "2. Contribution margin per unit = Price - Variable Cost per unit\n"
            "3. Break-even units = Fixed Costs / Contribution Margin per unit\n"
            "4. Break-even revenue = Break-even units × Price\n"
            "5. Margin of safety = (Projected sales - Break-even) / Projected sales\n"
            "   (< 10% risky, > 30% comfortable)\n\n"
            "Return JSON:\n"
            "{\n"
            '  "break_even_units": number,\n'
            '  "break_even_revenue": number,\n'
            '  "contribution_margin_per_unit": number,\n'
            '  "fixed_costs": number,\n'
            '  "price_per_unit": number,\n'
            '  "projected_sales_units": number,\n'
            '  "margin_of_safety_pct": number,\n'
            '  "margin_of_safety_assessment": "risky|comfortable|moderate",\n'
            '  "assumptions": ["..."]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return FinancialMetric(
                name="Break-Even Analysis",
                value="Unable to calculate",
                unit="$",
                assumptions=["Break-even calculation failed — no data or LLM error"],
            )

        try:
            data = json.loads(response.content)
            return FinancialMetric(
                name="Break-Even Analysis",
                value=f"{data.get('break_even_units', 'Unknown')} units / ${data.get('break_even_revenue', 'Unknown')}",
                unit="$",
                base_case=data.get("break_even_revenue"),
                assumptions=data.get("assumptions", []) + [
                    f"Fixed costs: ${data.get('fixed_costs', 'N/A')}",
                    f"Contribution margin/unit: ${data.get('contribution_margin_per_unit', 'N/A')}",
                    f"Margin of safety: {data.get('margin_of_safety_pct', 'N/A')}% ({data.get('margin_of_safety_assessment', 'N/A')})",
                ],
                sources=self._sources[:3],
            )
        except (json.JSONDecodeError, ValueError):
            return FinancialMetric(
                name="Break-Even Analysis",
                value="Parse error",
                unit="$",
                assumptions=["Break-even calculation failed — parsing error"],
            )

    # ─────────────────────────────────────────────────────────────────────
    # Cross-validate DCF with comparables and identify key value drivers
    # ─────────────────────────────────────────────────────────────────────

    async def _cross_validate_and_identify_drivers(
        self,
        dcf_valuation: FinancialMetric,
        comp_valuation: FinancialMetric,
        scenarios: dict[str, FinancialMetric],
    ) -> tuple[list[str], list[KeyFinding]]:
        """Cross-validate DCF with comparables and identify key value drivers.

        If DCF says $100M but comparables say $50M, flag the discrepancy
        and explain it. Identify the 2-3 assumptions that account for 80%
        of the valuation variance.
        """
        dcf_base = dcf_valuation.base_case
        comp_base = comp_valuation.base_case

        discrepancy_flag = ""
        if dcf_base is not None and comp_base is not None and dcf_base > 0 and comp_base > 0:
            divergence = abs(dcf_base - comp_base) / max(dcf_base, comp_base)
            if divergence > 0.3:
                if dcf_base > comp_base:
                    discrepancy_flag = (
                        f"DCF (${dcf_base:,.0f}) is significantly higher than comparables "
                        f"(${comp_base:,.0f}) — {divergence:.0%} divergence. This suggests "
                        f"the DCF assumes growth/margins above industry norms."
                    )
                else:
                    discrepancy_flag = (
                        f"Comparables (${comp_base:,.0f}) are significantly higher than DCF "
                        f"(${dcf_base:,.0f}) — {divergence:.0%} divergence. This suggests "
                        f"the market is pricing in growth not captured in the DCF."
                    )

        prompt = (
            "You are the Financial Analyst identifying key value drivers.\n\n"
            f"DCF valuation: {dcf_valuation.value} (base: {dcf_base})\n"
            f"Comp valuation: {comp_valuation.value} (base: {comp_base})\n"
            f"Discrepancy: {discrepancy_flag or 'No significant discrepancy'}\n\n"
            f"DCF assumptions: {dcf_valuation.assumptions}\n"
            f"Comp assumptions: {comp_valuation.assumptions}\n\n"
            "Identify the 2-3 key value drivers — the assumptions that account "
            "for 80% of the valuation variance. These are the assumptions that, "
            "if wrong, would most significantly change the recommendation.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "key_value_drivers": ["driver1", "driver2", "driver3"],\n'
            '  "driver_rationale": ["why each driver matters"],\n'
            '  "discrepancy_explanation": "...",\n'
            '  "cross_validation_assessment": "DCF and comps are consistent|divergent"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        key_drivers: list[str] = []
        findings: list[KeyFinding] = []

        if not response.success or not response.content:
            return key_drivers, findings

        try:
            data = json.loads(response.content)
            key_drivers = data.get("key_value_drivers", [])

            # Create a cross-validation finding
            if discrepancy_flag:
                findings.append(KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="valuation_discrepancy",
                    title="DCF vs. Comparable Valuation Discrepancy",
                    content=(
                        f"{discrepancy_flag} "
                        f"Explanation: {data.get('discrepancy_explanation', 'N/A')}. "
                        f"Cross-validation: {data.get('cross_validation_assessment', 'N/A')}"
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    sources=self._sources[:3],
                ))

            # Create key value driver findings
            for i, driver in enumerate(key_drivers):
                rationale = data.get("driver_rationale", [])
                rationale_text = rationale[i] if i < len(rationale) else ""
                findings.append(KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="key_value_driver",
                    title=f"Key Value Driver — {driver}",
                    content=rationale_text,
                    confidence=ConfidenceLevel.HIGH,
                    sources=self._sources[:2],
                ))

        except (json.JSONDecodeError, ValueError):
            pass

        return key_drivers, findings

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_financial_sub_agents(
        self,
        tickers: list[str],
        industry: str,
        business_model: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel financial data collection.

        Per §4.4, Agent 5:
        - Sub-agent A: Pull financial statements for [company1, company2, company3]
          (MICRO, Alpha Vantage)
        - Sub-agent B: Find industry margin benchmarks for [industry]
          (MICRO, SearxNG + Jina)
        - Sub-agent C: Find cost structure data for [business model]
          (FAST, SearxNG + Jina)
        """
        sub_specs = [
            SubAgentSpec(
                question=f"Pull financial statements for: {', '.join(tickers[:3])}",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.ALPHA_VANTAGE],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"tickers": tickers[:3]},
            ),
            SubAgentSpec(
                question=f"Find industry margin benchmarks for: {industry}",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"industry": industry},
            ),
            SubAgentSpec(
                question=f"Find cost structure data for: {business_model} business model",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"business_model": business_model},
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
        dcf_valuation: FinancialMetric,
        comp_valuation: FinancialMetric,
        sources_count: int,
        has_discrepancy: bool,
        scenario_count: int,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on data quality and cross-validation.

        HIGH: DCF and comps within 20% of each other, 5+ sources, 3 scenarios,
              no major discrepancy
        MEDIUM: DCF and comps within 40%, 3+ sources, 2+ scenarios
        LOW: DCF and comps diverge >40%, <3 sources, or major discrepancy
        """
        dcf_base = dcf_valuation.base_case
        comp_base = comp_valuation.base_case

        if dcf_base is not None and comp_base is not None and dcf_base > 0 and comp_base > 0:
            divergence = abs(dcf_base - comp_base) / max(dcf_base, comp_base)
            if divergence < 0.2 and sources_count >= 5 and scenario_count >= 3 and not has_discrepancy:
                return ConfidenceLevel.HIGH
            if divergence < 0.4 and sources_count >= 3:
                return ConfidenceLevel.MEDIUM
            return ConfidenceLevel.LOW

        if sources_count >= 5 and scenario_count >= 3:
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
    ) -> FinancialAnalysis:
        """Execute the Financial Analyst's 9-step methodology.

        Steps (§4.4, Agent 5):
        1. Pull comparable company financials (Alpha Vantage)
        2. Pull macroeconomic inputs for discount rates (FRED)
        3. Search for industry benchmarks (SearxNG + Jina)
        4. Build DCF model with sensitivity tables
        5. Build comparable company analysis
        6. Calculate unit economics
        7. Run scenario analysis (best/base/worst)
        8. Calculate break-even
        9. Produce FinancialAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus — specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting financial analysis: {self._question[:80]}",
        )

        # Extract context
        tickers = self._context.get("tickers", [])
        industry = self._context.get("industry", "")
        business_model = self._context.get("business_model", "")
        geography = self._context.get("geography", "US")
        tam_from_market = self._context.get("tam")

        # Spawn sub-agents for parallel data collection
        if tickers or industry:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning financial data collection sub-agents")
            sub_findings = await self._spawn_financial_sub_agents(tickers, industry, business_model)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 1: Pull comparable company financials
        if tickers:
            await self._transition(AgentState.WORKING, f"Step 1: Pulling financials for {len(tickers)} comparables (Alpha Vantage)")
            self._comparable_companies = await self._pull_comparable_financials(tickers)

        # Step 2: Pull macroeconomic inputs
        await self._transition(AgentState.WORKING, f"Step 2: Pulling macro inputs (FRED) for {geography}")
        self._macro_data = await self._pull_macro_inputs(geography)

        # Step 3: Search for industry benchmarks
        if industry:
            await self._transition(AgentState.WORKING, f"Step 3: Searching industry benchmarks (SearxNG + Jina) for {industry}")
            self._industry_benchmarks = await self._search_industry_benchmarks(industry)

        # Step 4: Build DCF model with sensitivity tables
        await self._transition(AgentState.WORKING, "Step 4: Building DCF model with sensitivity tables")
        dcf_valuation, sensitivity_tables = await self._build_dcf_model(
            self._question,
            self._comparable_companies,
            self._macro_data,
            self._industry_benchmarks,
            tam_from_market,
        )

        # Step 5: Build comparable company analysis
        await self._transition(AgentState.WORKING, "Step 5: Building comparable company analysis")
        comp_valuation = await self._build_comparable_analysis(
            self._question,
            self._comparable_companies,
        )

        # Step 6: Calculate unit economics
        await self._transition(AgentState.WORKING, "Step 6: Calculating unit economics")
        unit_economics = await self._calculate_unit_economics(
            self._question,
            self._industry_benchmarks,
        )

        # Step 7: Run scenario analysis
        await self._transition(AgentState.WORKING, "Step 7: Running scenario analysis (best/base/worst)")
        scenarios = await self._run_scenario_analysis(
            self._question,
            dcf_valuation,
            comp_valuation,
            self._industry_benchmarks,
        )

        # Step 8: Calculate break-even
        await self._transition(AgentState.WORKING, "Step 8: Calculating break-even")
        break_even = await self._calculate_break_even(
            self._question,
            unit_economics,
            self._industry_benchmarks,
        )

        # Cross-validate DCF with comparables and identify key value drivers
        await self._transition(AgentState.WORKING, "Cross-validating DCF with comparables")
        key_value_drivers, validation_findings = await self._cross_validate_and_identify_drivers(
            dcf_valuation, comp_valuation, scenarios,
        )

        # Calibrate confidence
        has_discrepancy = any(f.finding_type == "valuation_discrepancy" for f in validation_findings)
        confidence = self._calibrate_confidence(
            dcf_valuation,
            comp_valuation,
            len(self._sources),
            has_discrepancy,
            len(scenarios),
        )

        # Step 9: Produce FinancialAnalysis model
        await self._transition(AgentState.WORKING, "Step 9: Producing FinancialAnalysis model")

        analysis = FinancialAnalysis(
            dcf_valuation=dcf_valuation,
            comparable_analysis=comp_valuation,
            unit_economics=unit_economics,
            sensitivity_tables=sensitivity_tables,
            scenarios=scenarios,
            break_even=break_even,
            key_value_drivers=key_value_drivers,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        for finding in validation_findings:
            await self._publish_finding(finding)

        # Publish the full FinancialAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "financial_analysis": analysis.model_dump(),
                "dcf_valuation": str(dcf_valuation.value),
                "comp_valuation": str(comp_valuation.value),
                "key_value_drivers": key_value_drivers,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Financial analysis complete: DCF {dcf_valuation.value}, "
            f"Comp {comp_valuation.value}, confidence={confidence.value}",
        )

        return analysis
