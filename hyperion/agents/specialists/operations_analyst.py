"""
HYPERION Operations Analyst — Agent 8, the process optimization specialist.

This is NOT a generic "map the process" agent. This is a specialist with 7
proprietary analytical frameworks:

- Process mapping: SIPOC (Supplier-Input-Process-Output-Customer) and value
  stream mapping. Identifies non-value-adding steps.
- Lean/Six Sigma: Eliminate waste (Lean) and reduce variation (Six Sigma).
  Calculate process sigma level and DPMO (defects per million opportunities).
- Bottleneck analysis: Theory of constraints. Calculate throughput at each
  stage and identify the binding constraint.
- Supply chain mapping: Raw materials to end customer. Identify single-source
  suppliers, geographic concentration risks, and lead time vulnerabilities.
- Capacity planning: Current capacity utilization, capacity constraints, and
  expansion scenarios.
- Operational KPI design: Not generic metrics — the 5-7 metrics that actually
  drive performance for this specific operational model.
- Efficiency benchmarking: Benchmark against industry leaders. Identify the
  gap and estimate the improvement potential.

It doesn't just map processes — it identifies the binding constraint and
estimates the improvement potential in dollars. A generic ops analyst says
"the process has bottlenecks." The HYPERION Operations Analyst says "Step 3
is the bottleneck at 40 units/hour vs. 60 units/hour for the rest of the
process. Adding one worker to Step 3 costs $50K/year but increases throughput
by 50%, generating $200K/year in additional contribution margin. ROI = 300%."
(§4.4, Agent 8)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura
Sub-agents: Max 3 — operational benchmarks, supply chain data, efficiency metrics
Output: OperationsAnalysis (process map, bottlenecks, capacity, benchmarks,
        KPI dashboard, improvement opportunities, total improvement value,
        confidence, sources)

Methodology (§4.4, Agent 8):
1. Search for operational data and benchmarks (SearxNG + Jina)
2. Map the end-to-end process
3. Identify bottlenecks
4. Calculate capacity utilization
5. Benchmark against industry leaders
6. Identify improvement opportunities (Lean/Six Sigma)
7. Design KPI dashboard
8. Produce OperationsAnalysis model
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
    BenchmarkComparison,
    Bottleneck,
    ConfidenceLevel,
    KeyFinding,
    OperationalKPI,
    OperationsAnalysis,
    ProcessStep,
    Source,
    SourceCredibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


OPERATIONS_ANALYST_SPEC = AgentSpec(
    name=AgentName.OPERATIONS_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Operations Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Process mapping",
            description=(
                "Map end-to-end processes using SIPOC (Supplier-Input-Process-"
                "Output-Customer) and value stream mapping. Identify non-value-"
                "adding steps — steps that the customer wouldn't pay for if they "
                "knew about them. Each step has a cycle time, throughput rate, "
                "and value-adding classification. The map is not a flowchart — "
                "it's a diagnostic tool that reveals where time and money leak."
            ),
            inputs=["process_description", "business_model", "industry"],
            outputs=["sipoc_map", "value_stream_map", "non_value_adding_steps", "cycle_times"],
        ),
        SkillSpec(
            name="Lean/Six Sigma",
            description=(
                "Apply Lean principles (eliminate waste — the 8 wastes: defects, "
                "overproduction, waiting, non-utilized talent, transportation, "
                "inventory, motion, extra-processing) and Six Sigma (reduce "
                "variation — calculate process sigma level and DPMO). Identify "
                "which wastes exist in this process and prioritize by $ impact."
            ),
            inputs=["process_map", "defect_data", "cycle_times", "throughput_data"],
            outputs=["waste_identification", "sigma_level", "dpmo", "improvement_priorities"],
        ),
        SkillSpec(
            name="Bottleneck analysis",
            description=(
                "Identify process bottlenecks using theory of constraints. "
                "Calculate throughput at each stage and identify the binding "
                "constraint — the one step that limits the entire process. "
                "Quantify the improvement potential in DOLLARS: cost to resolve, "
                "throughput increase, annual $ value, and ROI. Not just 'Step 3 "
                "is slow' — 'Step 3 at 40 units/hr vs 60 units/hr downstream. "
                "Adding 1 worker costs $50K, generates $200K/yr. ROI=300%.'"
            ),
            inputs=["process_map", "throughput_per_step", "cost_data", "contribution_margin"],
            outputs=["binding_constraint", "improvement_actions", "roi_calculations", "throughput_increase"],
        ),
        SkillSpec(
            name="Supply chain mapping",
            description=(
                "Map the supply chain from raw materials to end customer. "
                "Identify single-source suppliers (one supplier = one point of "
                "failure), geographic concentration risks (all suppliers in one "
                "region = geopolitical risk), and lead time vulnerabilities "
                "(long lead times = inventory risk). Each risk has a mitigation."
            ),
            inputs=["supplier_list", "geography", "lead_times", "inventory_levels"],
            outputs=["supply_chain_map", "single_source_risks", "geographic_concentration", "lead_time_vulnerabilities"],
        ),
        SkillSpec(
            name="Capacity planning",
            description=(
                "Calculate current capacity utilization (actual output / maximum "
                "output). Identify capacity constraints (what limits output). "
                "Model capacity expansion scenarios: what if we add a shift, a "
                "line, a facility? Each scenario has a cost, capacity increase, "
                "and break-even utilization."
            ),
            inputs=["current_output", "maximum_capacity", "demand_forecast", "expansion_options"],
            outputs=["capacity_utilization", "constraints", "expansion_scenarios", "break_even_utilization"],
        ),
        SkillSpec(
            name="Operational KPI design",
            description=(
                "Design a KPI dashboard specific to the business — not generic "
                "metrics, but the 5-7 metrics that actually drive performance for "
                "this specific operational model. Each KPI has a formula, target, "
                "measurement frequency, and the levers that move it. A SaaS company "
                "needs different KPIs than a manufacturing plant."
            ),
            inputs=["business_model", "industry", "operational_priorities", "available_data"],
            outputs=["kpi_dashboard", "kpi_formulas", "targets", "levers", "measurement_frequency"],
        ),
        SkillSpec(
            name="Efficiency benchmarking",
            description=(
                "Benchmark operational metrics against industry leaders. Identify "
                "the gap between current performance and best-in-class. Estimate "
                "the improvement potential — both the operational improvement (% "
                "efficiency gain) and the financial improvement ($ annual value "
                "of closing the gap)."
            ),
            inputs=["current_metrics", "industry_benchmarks", "best_in_class_data"],
            outputs=["benchmark_comparison", "gap_analysis", "improvement_potential", "annual_value"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Operations Analyst — the specialist who optimizes "
        "processes, maps supply chains, identifies bottlenecks, and designs "
        "operational KPIs.\n\n"
        "Your proprietary frameworks:\n"
        "1. Process mapping: SIPOC + value stream mapping. Each step has cycle "
        "time, throughput, and value-adding classification. Non-value-adding "
        "steps are waste.\n"
        "2. Lean/Six Sigma: 8 wastes (DOWNTIME: Defects, Overproduction, Waiting, "
        "Non-utilized talent, Transportation, Inventory, Motion, Extra-processing). "
        "Six Sigma: sigma level and DPMO.\n"
        "3. Bottleneck analysis: Theory of constraints. Identify the BINDING "
        "constraint. Quantify improvement in DOLLARS: cost to resolve, throughput "
        "increase, annual $ value, ROI.\n"
        "4. Supply chain mapping: Raw materials to end customer. Single-source "
        "risks, geographic concentration, lead time vulnerabilities.\n"
        "5. Capacity planning: Current utilization, constraints, expansion "
        "scenarios with break-even analysis.\n"
        "6. KPI design: 5-7 metrics that drive performance for THIS operational "
        "model. Not generic — specific to the business.\n"
        "7. Efficiency benchmarking: Gap to industry leaders + $ improvement "
        "potential.\n\n"
        "Rules:\n"
        "- DON'T JUST MAP PROCESSES — IDENTIFY THE BINDING CONSTRAINT AND "
        "ESTIMATE THE IMPROVEMENT POTENTIAL IN DOLLARS.\n"
        "- A generic ops analyst says 'the process has bottlenecks.' You say "
        "'Step 3 is the bottleneck at 40 units/hr vs 60 units/hr downstream. "
        "Adding 1 worker costs $50K, generates $200K/yr. ROI=300%.'\n"
        "- Each bottleneck must have: improvement action, cost, throughput "
        "increase, annual $ value, and ROI.\n"
        "- KPIs must be SPECIFIC to the operational model, not generic. A SaaS "
        "company needs different KPIs than a manufacturing plant.\n"
        "- Supply chain risks must have MITIGATIONS — not just 'single source "
        "risk' but 'qualify a second supplier within 90 days.'\n"
        "- Benchmark gaps must be quantified in both % and $ annual value.\n"
        "- Lean waste identification must categorize each waste type (DOWNTIME) "
        "and prioritize by $ impact.\n\n"
        "You can spawn up to 3 sub-agents for parallel data collection:\n"
        "- Sub-agent A: Find operational benchmarks for [industry] (MICRO, SearxNG)\n"
        "- Sub-agent B: Find supply chain data for [sector] (MICRO, SearxNG + Jina)\n"
        "- Sub-agent C: Find efficiency metrics for [process type] (FAST, SearxNG)\n\n"
        "Your output is an OperationsAnalysis Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned when the question involves process optimization, "
                     "supply chain analysis, capacity planning, operational "
                     "efficiency, or KPI design (OPERATIONS_ANALYSIS, "
                     "SUPPLY_CHAIN, CAPACITY_PLANNING types)",
    max_sub_agents=3,
    output_model="OperationsAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Operations Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class OperationsAnalyst(BaseAgent):
    """Agent 8: The operations optimization specialist.

    Maps processes using SIPOC, identifies bottlenecks via theory of constraints,
    quantifies improvement potential in dollars, designs operational KPIs
    specific to the business model, benchmarks against industry leaders, and
    applies Lean/Six Sigma to identify waste. Doesn't just say "there are
    bottlenecks" — says "Step 3 is the bottleneck, costs $50K to fix, generates
    $200K/yr, ROI=300%." (§4.4, Agent 8)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for operational data and benchmarks (SearxNG + Jina)
    3. Maps the end-to-end process using SIPOC
    4. Identifies bottlenecks with $ improvement potential
    5. Calculates capacity utilization and benchmarks
    6. Designs KPI dashboard and identifies Lean/Six Sigma improvements
    7. Produces OperationsAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or OPERATIONS_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._supply_chain_data: list[dict[str, Any]] = []
        self._benchmark_data: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Operations Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting capacity constraints for strategy framing)
        - FINDINGS: receives findings from other agents that may inform
          operational analysis (e.g., Financial Analyst's cost structure,
          Technology Analyst's automation potential)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "operations_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Financial Analyst's cost structure informs operational costs
                if finding.finding_type == "cost_structure":
                    self._context.setdefault("cost_data", []).append(finding.content)
                # Technology Analyst's automation potential informs process optimization
                elif finding.finding_type == "architecture_review":
                    self._context.setdefault("tech_capabilities", []).append(finding.content)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "capacity_constraints":
                # Strategy Analyst requesting capacity constraints for strategy
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for operational data and benchmarks (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_operational_data(self, industry: str, process_type: str) -> list[dict[str, Any]]:
        """Search for operational benchmarks, supply chain data, and process
        optimization case studies.

        Uses SearxNG to find: operational benchmarks, supply chain data,
        process optimization case studies. Uses Jina to extract operational
        reports, industry efficiency data, and supply chain analyses.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{industry} operational benchmarks efficiency metrics",
                f"{industry} process optimization case study",
                f"{industry} supply chain analysis best practices",
                f"{process_type} throughput benchmarks industry",
                f"{industry} capacity utilization industry average",
                f"{industry} Lean Six Sigma improvement results",
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

            # Extract content from top URLs using Jina
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
    # Step 1b: Scrape supply chain databases (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_supply_chain_data(self, sector: str) -> list[dict[str, Any]]:
        """Scrape JS-rendered supply chain databases, logistics platforms,
        and operational dashboards.

        Uses Obscura to scrape supply chain visibility platforms, logistics
        databases, and operational dashboards that require JS rendering.
        """
        results: list[dict[str, Any]] = []

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            # Supply chain and logistics data sources
            data_urls = [
                f"https://www.supplychaindive.com/search?q={sector}",
                f"https://logisticsiq.com/sector/{sector}",
            ]

            for url in data_urls:
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
                            title=f"Supply chain data — {sector}",
                            url=url,
                            credibility=SourceCredibility.INDUSTRY_REPORT,
                            key_data=f"Supply chain data for {sector}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Map the end-to-end process
    # ─────────────────────────────────────────────────────────────────────

    async def _map_process(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[ProcessStep]:
        """Map the end-to-end process using SIPOC methodology.

        Each step has: Supplier, Input, Process, Output, Customer, cycle time,
        throughput rate, and value-adding classification. Non-value-adding
        steps are flagged for elimination.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:10]
        )
        context_summary = json.dumps(context, default=str)[:1500]

        prompt = (
            "You are the HYPERION Operations Analyst mapping an end-to-end process.\n\n"
            f"Question: {question}\n\n"
            f"Industry benchmarks:\n{search_summary}\n\n"
            f"Business context:\n{context_summary}\n\n"
            "Map the end-to-end process using SIPOC methodology:\n"
            "For each step, provide:\n"
            "- step_number: sequential number\n"
            "- step_name: name of the step\n"
            "- supplier: who/what provides the input\n"
            "- input: what comes into this step\n"
            "- process: what happens in this step\n"
            "- output: what comes out of this step\n"
            "- customer: who receives the output\n"
            "- cycle_time: time to complete (e.g., '2 hours', '3 days')\n"
            "- throughput: units per time period (e.g., '50 units/hour')\n"
            "- is_value_adding: does the customer pay for this step?\n"
            "- is_bottleneck: is this the binding constraint?\n\n"
            "Identify 5-15 steps. Flag non-value-adding steps. Mark bottlenecks.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "process_steps": [{\n'
            '    "step_number": 1,\n'
            '    "step_name": "...",\n'
            '    "supplier": "...",\n'
            '    "input": "...",\n'
            '    "process": "...",\n'
            '    "output": "...",\n'
            '    "customer": "...",\n'
            '    "cycle_time": "...",\n'
            '    "throughput": "...",\n'
            '    "is_value_adding": true|false,\n'
            '    "is_bottleneck": true|false\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        steps: list[ProcessStep] = []

        if not response.success or not response.content:
            return steps

        try:
            data = json.loads(response.content)
            step_list = data.get("process_steps", [])

            for s in step_list:
                steps.append(ProcessStep(
                    step_number=int(s.get("step_number", len(steps) + 1)),
                    step_name=s.get("step_name", "Unknown step"),
                    supplier=s.get("supplier", "Unknown"),
                    input=s.get("input", "Unknown"),
                    process=s.get("process", "Unknown"),
                    output=s.get("output", "Unknown"),
                    customer=s.get("customer", "Unknown"),
                    cycle_time=s.get("cycle_time", "Unknown"),
                    throughput=s.get("throughput", "Unknown"),
                    is_value_adding=bool(s.get("is_value_adding", False)),
                    is_bottleneck=bool(s.get("is_bottleneck", False)),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return steps

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Identify bottlenecks with $ improvement potential
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_bottlenecks(
        self,
        question: str,
        process_map: list[ProcessStep],
        context: dict[str, Any],
    ) -> list[Bottleneck]:
        """Identify bottlenecks using theory of constraints.

        For each bottleneck, quantify: improvement action, cost, throughput
        increase, annual $ value, and ROI. Not just "Step 3 is slow" —
        "Step 3 at 40 units/hr vs 60 units/hr. Adding 1 worker costs $50K,
        generates $200K/yr. ROI=300%."
        """
        process_summary = "\n".join(
            f"- Step {s.step_number}: {s.step_name} | "
            f"Throughput: {s.throughput} | "
            f"Cycle: {s.cycle_time} | "
            f"Value-adding: {s.is_value_adding} | "
            f"Bottleneck: {s.is_bottleneck}"
            for s in process_map
        )

        prompt = (
            "You are the HYPERION Operations Analyst identifying bottlenecks.\n\n"
            f"Question: {question}\n\n"
            f"Process map:\n{process_summary}\n\n"
            "For each bottleneck, quantify the improvement potential IN DOLLARS:\n"
            "1. current_throughput: current throughput at this step\n"
            "2. max_downstream_throughput: max throughput of downstream steps\n"
            "3. constraint_type: capacity, policy, market, or material\n"
            "4. improvement_action: SPECIFIC action to resolve (not 'improve efficiency')\n"
            "5. improvement_cost: estimated cost to resolve ($)\n"
            "6. improvement_potential: estimated throughput increase (%)\n"
            "7. annual_value: annual $ value of resolving this bottleneck\n"
            "8. roi: ROI of the improvement action\n\n"
            "Example: Step 3 at 40 units/hr vs 60 units/hr downstream. Adding 1 "
            "worker costs $50K/yr but increases throughput by 50%, generating "
            "$200K/yr in additional contribution margin. ROI = 300%.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "bottlenecks": [{\n'
            '    "step_name": "...",\n'
            '    "current_throughput": "...",\n'
            '    "max_downstream_throughput": "...",\n'
            '    "constraint_type": "capacity|policy|market|material",\n'
            '    "improvement_action": "specific action",\n'
            '    "improvement_cost": "$...",\n'
            '    "improvement_potential": "...%",\n'
            '    "annual_value": "$.../yr",\n'
            '    "roi": "...%"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        bottlenecks: list[Bottleneck] = []

        if not response.success or not response.content:
            return bottlenecks

        try:
            data = json.loads(response.content)
            bn_list = data.get("bottlenecks", [])

            for bn in bn_list:
                bottlenecks.append(Bottleneck(
                    step_name=bn.get("step_name", "Unknown"),
                    current_throughput=bn.get("current_throughput", "Unknown"),
                    max_downstream_throughput=bn.get("max_downstream_throughput", "Unknown"),
                    constraint_type=bn.get("constraint_type", "capacity"),
                    improvement_action=bn.get("improvement_action", ""),
                    improvement_cost=bn.get("improvement_cost", ""),
                    improvement_potential=bn.get("improvement_potential", ""),
                    annual_value=bn.get("annual_value", ""),
                    roi=bn.get("roi", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return bottlenecks

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Calculate capacity utilization
    # ─────────────────────────────────────────────────────────────────────

    async def _calculate_capacity(
        self,
        question: str,
        process_map: list[ProcessStep],
        bottlenecks: list[Bottleneck],
        context: dict[str, Any],
    ) -> str:
        """Calculate current capacity utilization and identify constraints.

        Capacity utilization = actual output / maximum output. Identify what
        limits output and model expansion scenarios.
        """
        process_summary = "\n".join(
            f"- Step {s.step_number}: {s.step_name} | Throughput: {s.throughput}"
            for s in process_map
        )
        bottleneck_summary = "\n".join(
            f"- {b.step_name}: {b.current_throughput} → {b.improvement_potential} increase"
            for b in bottlenecks
        )

        prompt = (
            "You are the HYPERION Operations Analyst calculating capacity utilization.\n\n"
            f"Question: {question}\n\n"
            f"Process map:\n{process_summary}\n\n"
            f"Bottlenecks:\n{bottleneck_summary or 'None identified'}\n\n"
            "Calculate:\n"
            "1. Current capacity utilization (actual / maximum)\n"
            "2. What limits output (the binding constraint)\n"
            "3. Capacity expansion scenarios:\n"
            "   - Add a shift: cost, capacity increase, break-even utilization\n"
            "   - Add a line/machine: cost, capacity increase, break-even\n"
            "   - Add a facility: cost, capacity increase, break-even\n"
            "4. Demand forecast vs. capacity (when do we hit the wall?)\n\n"
            "Return a concise assessment (2-3 paragraphs) covering all 4 points.\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
        )

        if response.success and response.content:
            return response.content

        return "Capacity utilization analysis failed — insufficient data"

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Benchmark against industry leaders
    # ─────────────────────────────────────────────────────────────────────

    async def _benchmark_against_leaders(
        self,
        question: str,
        process_map: list[ProcessStep],
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[BenchmarkComparison]:
        """Benchmark operational metrics against industry leaders.

        Identifies the gap between current performance and best-in-class,
        and estimates the improvement potential in both % and $ annual value.
        """
        process_summary = "\n".join(
            f"- Step {s.step_number}: {s.step_name} | Throughput: {s.throughput} | Cycle: {s.cycle_time}"
            for s in process_map
        )
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )

        prompt = (
            "You are the HYPERION Operations Analyst benchmarking against industry leaders.\n\n"
            f"Question: {question}\n\n"
            f"Current process:\n{process_summary}\n\n"
            f"Industry benchmarks:\n{search_summary}\n\n"
            "Benchmark 5-8 operational metrics against industry leaders:\n"
            "For each metric:\n"
            "1. metric: name of the operational metric\n"
            "2. current_value: current performance\n"
            "3. industry_average: industry average\n"
            "4. industry_leader: best-in-class performance\n"
            "5. gap_to_leader: gap between current and leader\n"
            "6. improvement_potential: estimated improvement if gap closed\n"
            "7. annual_value: annual $ value of closing the gap\n\n"
            "Return JSON:\n"
            "{\n"
            '  "benchmarks": [{\n'
            '    "metric": "...",\n'
            '    "current_value": "...",\n'
            '    "industry_average": "...",\n'
            '    "industry_leader": "...",\n'
            '    "gap_to_leader": "...",\n'
            '    "improvement_potential": "...",\n'
            '    "annual_value": "$.../yr"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        benchmarks: list[BenchmarkComparison] = []

        if not response.success or not response.content:
            return benchmarks

        try:
            data = json.loads(response.content)
            bm_list = data.get("benchmarks", [])

            for bm in bm_list:
                benchmarks.append(BenchmarkComparison(
                    metric=bm.get("metric", "Unknown"),
                    current_value=bm.get("current_value", "Unknown"),
                    industry_average=bm.get("industry_average", "Unknown"),
                    industry_leader=bm.get("industry_leader", "Unknown"),
                    gap_to_leader=bm.get("gap_to_leader", "Unknown"),
                    improvement_potential=bm.get("improvement_potential", "Unknown"),
                    annual_value=bm.get("annual_value", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return benchmarks

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Identify improvement opportunities (Lean/Six Sigma)
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_improvements(
        self,
        question: str,
        process_map: list[ProcessStep],
        bottlenecks: list[Bottleneck],
        benchmarks: list[BenchmarkComparison],
        context: dict[str, Any],
    ) -> tuple[list[str], str]:
        """Identify Lean/Six Sigma improvement opportunities.

        Apply the 8 wastes (DOWNTIME: Defects, Overproduction, Waiting,
        Non-utilized talent, Transportation, Inventory, Motion, Extra-
        processing). Calculate process sigma level and DPMO. Prioritize
        improvements by $ impact.

        Returns (improvement_opportunities, total_improvement_value).
        """
        non_value_adding = [s for s in process_map if not s.is_value_adding]
        nva_summary = "\n".join(
            f"- Step {s.step_number}: {s.step_name} ({s.cycle_time})"
            for s in non_value_adding
        )
        bottleneck_value = "\n".join(
            f"- {b.step_name}: {b.annual_value} (ROI: {b.roi})"
            for b in bottlenecks
        )
        benchmark_value = "\n".join(
            f"- {bm.metric}: {bm.annual_value}"
            for bm in benchmarks if bm.annual_value
        )

        prompt = (
            "You are the HYPERION Operations Analyst identifying Lean/Six Sigma improvements.\n\n"
            f"Question: {question}\n\n"
            f"Non-value-adding steps:\n{nva_summary or 'None identified'}\n\n"
            f"Bottleneck improvement values:\n{bottleneck_value or 'None identified'}\n\n"
            f"Benchmark improvement values:\n{benchmark_value or 'None identified'}\n\n"
            "Apply Lean (eliminate waste) and Six Sigma (reduce variation):\n"
            "1. Identify the 8 wastes (DOWNTIME): Defects, Overproduction, Waiting, "
            "Non-utilized talent, Transportation, Inventory, Motion, Extra-processing\n"
            "2. For each waste found, specify: where it occurs, $ impact, and how to eliminate it\n"
            "3. Calculate process sigma level (if defect data available)\n"
            "4. Calculate DPMO (defects per million opportunities) if applicable\n"
            "5. Prioritize improvements by $ impact (highest first)\n"
            "6. Sum all improvement values for total annual $ potential\n\n"
            "Return JSON:\n"
            "{\n"
            '  "improvements": ["improvement1 — $X/yr", "improvement2 — $Y/yr", ...],\n'
            '  "wastes_identified": [{"waste_type": "...", "location": "...", "impact": "$..."}],\n'
            '  "sigma_level": "...",\n'
            '  "dpmo": "...",\n'
            '  "total_annual_value": "$.../yr"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return ([], "")

        try:
            data = json.loads(response.content)
            improvements = data.get("improvements", [])
            total_value = data.get("total_annual_value", "")
            return (improvements, total_value)
        except (json.JSONDecodeError, ValueError):
            return ([], "")

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Design KPI dashboard
    # ─────────────────────────────────────────────────────────────────────

    async def _design_kpi_dashboard(
        self,
        question: str,
        process_map: list[ProcessStep],
        benchmarks: list[BenchmarkComparison],
        context: dict[str, Any],
    ) -> list[OperationalKPI]:
        """Design a KPI dashboard specific to the business.

        Not generic metrics — the 5-7 metrics that actually drive performance
        for this specific operational model. Each KPI has a formula, target,
        measurement frequency, and the levers that move it.
        """
        business_model = context.get("business_model", "")
        industry = context.get("industry", "")
        process_summary = "\n".join(
            f"- Step {s.step_number}: {s.step_name}"
            for s in process_map
        )

        prompt = (
            "You are the HYPERION Operations Analyst designing a KPI dashboard.\n\n"
            f"Question: {question}\n\n"
            f"Business model: {business_model}\n"
            f"Industry: {industry}\n\n"
            f"Process:\n{process_summary}\n\n"
            "Design 5-7 KPIs that ACTUALLY DRIVE PERFORMANCE for this operational model.\n"
            "NOT generic metrics — specific to this business.\n"
            "For each KPI:\n"
            "1. name: KPI name\n"
            "2. category: efficiency, quality, throughput, cost, or customer\n"
            "3. formula: how to calculate it\n"
            "4. target: target value\n"
            "5. current: current value (if known, else 'TBD')\n"
            "6. unit: unit of measurement\n"
            "7. frequency: hourly, daily, weekly, or monthly\n"
            "8. levers: what moves this KPI (2-3 specific levers)\n"
            "9. benchmark: industry benchmark for this KPI\n\n"
            "Return JSON:\n"
            "{\n"
            '  "kpis": [{\n'
            '    "name": "...",\n'
            '    "category": "...",\n'
            '    "formula": "...",\n'
            '    "target": "...",\n'
            '    "current": "...",\n'
            '    "unit": "...",\n'
            '    "frequency": "...",\n'
            '    "levers": ["lever1", "lever2"],\n'
            '    "benchmark": "..."\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        kpis: list[OperationalKPI] = []

        if not response.success or not response.content:
            return kpis

        try:
            data = json.loads(response.content)
            kpi_list = data.get("kpis", [])

            for k in kpi_list:
                kpis.append(OperationalKPI(
                    name=k.get("name", "Unknown KPI"),
                    category=k.get("category", "efficiency"),
                    formula=k.get("formula", ""),
                    target=k.get("target", ""),
                    current=k.get("current", "TBD"),
                    unit=k.get("unit", ""),
                    frequency=k.get("frequency", "weekly"),
                    levers=k.get("levers", []),
                    benchmark=k.get("benchmark", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return kpis

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_ops_sub_agents(
        self,
        industry: str,
        sector: str,
        process_type: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel operational data collection.

        Per §4.4, Agent 8:
        - Sub-agent A: Find operational benchmarks for [industry] (MICRO, SearxNG)
        - Sub-agent B: Find supply chain data for [sector] (MICRO, SearxNG + Jina)
        - Sub-agent C: Find efficiency metrics for [process type] (FAST, SearxNG)
        """
        sub_specs = [
            SubAgentSpec(
                question=f"Find operational benchmarks for {industry} — throughput rates, cycle times, capacity utilization, efficiency metrics",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"industry": industry},
            ),
            SubAgentSpec(
                question=f"Find supply chain data for {sector} — supplier concentration, lead times, logistics costs, single-source risks",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"sector": sector},
            ),
            SubAgentSpec(
                question=f"Find efficiency metrics for {process_type} — best-in-class performance, industry averages, improvement potential",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.SEARXNG],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"process_type": process_type},
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
        process_steps: int,
        bottlenecks_count: int,
        benchmarks_count: int,
        kpis_count: int,
        sources_count: int,
        has_improvement_value: bool,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 5+ process steps, bottlenecks with $ values, 5+ benchmarks,
              5+ KPIs, 5+ sources, total improvement value calculated
        MEDIUM: 3+ process steps, some bottlenecks, 3+ benchmarks, 3+ KPIs
        LOW: <3 process steps, missing core analysis
        """
        if (process_steps >= 5 and bottlenecks_count >= 1
                and benchmarks_count >= 5 and kpis_count >= 5
                and sources_count >= 5 and has_improvement_value):
            return ConfidenceLevel.HIGH
        if process_steps >= 3 and (bottlenecks_count >= 1 or benchmarks_count >= 3) and kpis_count >= 3:
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
    ) -> OperationsAnalysis:
        """Execute the Operations Analyst's 8-step methodology.

        Steps (§4.4, Agent 8):
        1. Search for operational data and benchmarks (SearxNG + Jina)
        2. Map the end-to-end process
        3. Identify bottlenecks
        4. Calculate capacity utilization
        5. Benchmark against industry leaders
        6. Identify improvement opportunities (Lean/Six Sigma)
        7. Design KPI dashboard
        8. Produce OperationsAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus — specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting operations analysis: {self._question[:80]}",
        )

        # Extract context
        industry = self._context.get("industry", "")
        sector = self._context.get("sector", industry)
        process_type = self._context.get("process_type", "manufacturing")

        # Spawn sub-agents for parallel data collection
        if industry or sector:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning operational data collection sub-agents")
            sub_findings = await self._spawn_ops_sub_agents(industry, sector, process_type)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 1: Search for operational data and benchmarks
        await self._transition(AgentState.WORKING, f"Step 1: Searching operational benchmarks for {industry}")
        self._search_results = await self._search_operational_data(industry, process_type)

        # Scrape supply chain data
        if sector:
            await self._transition(AgentState.WORKING, f"Step 1b: Scraping supply chain data for {sector}")
            self._supply_chain_data = await self._scrape_supply_chain_data(sector)

        # Step 2: Map the end-to-end process
        await self._transition(AgentState.WORKING, "Step 2: Mapping end-to-end process (SIPOC)")
        process_map = await self._map_process(self._question, self._search_results, self._context)

        if not process_map:
            await self._transition(
                AgentState.BLOCKED,
                "No process steps identified — cannot proceed with operations analysis",
            )
            return OperationsAnalysis(
                confidence=ConfidenceLevel.LOW,
                sources=self._sources,
            )

        # Step 3: Identify bottlenecks
        await self._transition(AgentState.WORKING, "Step 3: Identifying bottlenecks with $ improvement potential")
        bottlenecks = await self._identify_bottlenecks(self._question, process_map, self._context)

        # Step 4: Calculate capacity utilization
        await self._transition(AgentState.WORKING, "Step 4: Calculating capacity utilization")
        capacity_assessment = await self._calculate_capacity(
            self._question, process_map, bottlenecks, self._context,
        )

        # Step 5: Benchmark against industry leaders
        await self._transition(AgentState.WORKING, "Step 5: Benchmarking against industry leaders")
        benchmarks = await self._benchmark_against_leaders(
            self._question, process_map, self._search_results, self._context,
        )

        # Step 6: Identify improvement opportunities (Lean/Six Sigma)
        await self._transition(AgentState.WORKING, "Step 6: Identifying Lean/Six Sigma improvements")
        improvements, total_value = await self._identify_improvements(
            self._question, process_map, bottlenecks, benchmarks, self._context,
        )

        # Step 7: Design KPI dashboard
        await self._transition(AgentState.WORKING, "Step 7: Designing operational KPI dashboard")
        kpis = await self._design_kpi_dashboard(
            self._question, process_map, benchmarks, self._context,
        )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            process_steps=len(process_map),
            bottlenecks_count=len(bottlenecks),
            benchmarks_count=len(benchmarks),
            kpis_count=len(kpis),
            sources_count=len(self._sources),
            has_improvement_value=bool(total_value),
        )

        # Step 8: Produce OperationsAnalysis model
        await self._transition(AgentState.WORKING, "Step 8: Producing OperationsAnalysis model")

        analysis = OperationsAnalysis(
            process_map=process_map,
            bottlenecks=bottlenecks,
            capacity_utilization=capacity_assessment,
            benchmark_comparison=benchmarks,
            kpi_dashboard=kpis,
            improvement_opportunities=improvements,
            total_improvement_value=total_value,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        # Publish bottlenecks as findings
        for bn in bottlenecks:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="bottleneck",
                title=f"Bottleneck — {bn.step_name} (ROI: {bn.roi})",
                content=(
                    f"{bn.step_name}: Current {bn.current_throughput} vs downstream "
                    f"{bn.max_downstream_throughput}. Constraint type: {bn.constraint_type}. "
                    f"Action: {bn.improvement_action}. Cost: {bn.improvement_cost}. "
                    f"Potential: {bn.improvement_potential}. Annual value: {bn.annual_value}. "
                    f"ROI: {bn.roi}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish total improvement value as a finding
        if total_value:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="improvement_value",
                title=f"Total Operational Improvement Potential: {total_value}",
                content=(
                    f"Total annual improvement value from all opportunities: {total_value}. "
                    f"Improvements: {'; '.join(improvements[:5])}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:3],
            )
            await self._publish_finding(finding)

        # Publish the full OperationsAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "operations_analysis": analysis.model_dump(),
                "process_steps": len(process_map),
                "bottlenecks": len(bottlenecks),
                "kpis": len(kpis),
                "total_improvement_value": total_value,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Operations analysis complete: {len(process_map)} process steps, "
            f"{len(bottlenecks)} bottlenecks, {len(kpis)} KPIs, "
            f"improvement value={total_value or 'N/A'}, confidence={confidence.value}",
        )

        return analysis
