"""
HYPERION Risk Analyst — Agent 6, the risk identification and mitigation specialist.

This is NOT a generic "list the risks" agent. This is a specialist with 6
proprietary analytical frameworks:

- Risk matrix: 5×5 probability × impact grid with color-coded zones.
- Monte Carlo simulation: 10,000-trial simulations on key variables producing
  P10/P50/P90 probability distributions.
- Black swan analysis: Low-probability, high-impact events that could
  invalidate the entire strategy — risks that don't appear in the matrix
  because they're too unlikely, but if they happen, they're catastrophic.
- Scenario planning: Best/base/worst with trigger conditions, leading
  indicators, and response plans.
- Mitigation design: Specific action, assigned owner, residual risk.
- Residual risk scoring: Re-score after mitigation — some risks are fully
  mitigatable, others are inherent.

It thinks in scenarios, not in lists. A generic risk analyst lists 20 risks.
The HYPERION Risk Analyst identifies the 5 risks that actually matter,
explains why the other 15 are noise, and designs mitigations that are
specific enough to act on. It always asks "what would kill this?" before
asking "what could help this?" — because surviving the downside is more
important than capturing the upside. (§4.4, Agent 6)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura
Sub-agents: Max 3 — historical failures, regulatory risks, technology/cyber risks
Output: RiskAnalysis (risks, top risks with mitigations, black swans,
        residual risk summary, scenario plan, confidence, sources)

Methodology (§4.4, Agent 6):
1. Search for known risks in the industry/space (SearxNG + Jina)
2. Scrape regulatory/sanctions databases (Obscura)
3. Identify risks across 6 categories: market, financial, operational,
   regulatory, technology, and strategic
4. Score each risk on probability × impact
5. Build risk matrix
6. Design mitigations for top 10 risks
7. Calculate residual risk scores
8. Identify black swan scenarios
9. Build scenario plan with triggers
10. Produce RiskAnalysis model
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
    KeyFinding,
    Risk,
    RiskAnalysis,
    RiskCategory,
    Source,
    SourceCredibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


RISK_ANALYST_SPEC = AgentSpec(
    name=AgentName.RISK_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Risk Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Risk matrix",
            description=(
                "Build a probability × impact matrix with risks plotted on a 5×5 grid. "
                "Each risk is scored on probability (1-5) and impact (1-5), with "
                "color-coded zones: green (1-6 score), yellow (7-14), red (15-25). "
                "The matrix is not just a chart — it's a prioritization tool. Risks "
                "in the red zone get mitigations first. Risks in the green zone are "
                "monitored but don't require immediate action."
            ),
            inputs=["identified_risks", "probability_scores", "impact_scores"],
            outputs=["risk_matrix", "color_coded_zones", "priority_ranking"],
        ),
        SkillSpec(
            name="Monte Carlo simulation",
            description=(
                "Run 10,000-trial Monte Carlo simulations on key variables (revenue, "
                "cost, timeline) to produce probability distributions of outcomes. "
                "Shows P10 (optimistic), P50 (median), P90 (conservative) values. "
                "The simulation reveals not just the expected outcome but the range "
                "of possible outcomes and their likelihood — critical for risk-aware "
                "decision making."
            ),
            inputs=["key_variables", "probability_distributions", "correlation_matrix"],
            outputs=["p10_value", "p50_value", "p90_value", "probability_distribution", "var_at_95"],
        ),
        SkillSpec(
            name="Black swan analysis",
            description=(
                "Identify low-probability, high-impact events that could invalidate "
                "the entire strategy. These are risks that don't appear in the risk "
                "matrix because they're too unlikely — but if they happen, they're "
                "catastrophic. Examples: regulatory ban, founder death, supply chain "
                "collapse, black swan market crash. Each black swan has a 'canary' — "
                "an early warning indicator that would signal the risk is materializing."
            ),
            inputs=["industry_context", "historical_black_swans", "strategy_dependencies"],
            outputs=["black_swan_scenarios", "canary_indicators", "survival_actions"],
        ),
        SkillSpec(
            name="Scenario planning",
            description=(
                "Build best case, base case, worst case scenarios with trigger "
                "conditions, leading indicators, and response plans for each. "
                "Trigger conditions are observable events that signal a scenario "
                "is materializing. Leading indicators are metrics to monitor "
                "weekly/monthly. Response plans are pre-committed actions — not "
                "'we'll figure it out when it happens.'"
            ),
            inputs=["base_case_model", "risk_catalog", "industry_drivers"],
            outputs=["best_case_scenario", "base_case_scenario", "worst_case_scenario", "trigger_conditions", "response_plans"],
        ),
        SkillSpec(
            name="Mitigation design",
            description=(
                "For each risk, design a specific mitigation action, assign an owner "
                "(which agent monitors it), and calculate residual risk (risk after "
                "mitigation). Mitigations must be specific enough to act on — not "
                "'monitor the situation' but 'set up weekly Google Alerts for "
                "[regulator] announcements and flag any policy changes to the "
                "Regulatory Analyst.'"
            ),
            inputs=["top_risks", "available_resources", "monitoring_capabilities"],
            outputs=["mitigation_actions", "risk_owners", "residual_risk_scores", "mitigation_timeline"],
        ),
        SkillSpec(
            name="Residual risk scoring",
            description=(
                "After mitigations, re-score each risk to show residual risk. Some "
                "risks are fully mitigatable (e.g., regulatory risk mitigated by "
                "compliance audit → residual probability drops from 4 to 1). Others "
                "are inherent (e.g., market risk — you can hedge but not eliminate). "
                "The residual risk profile shows what risk remains after all "
                "reasonable mitigations — this is the risk the organization must "
                "accept to proceed."
            ),
            inputs=["original_risk_scores", "mitigation_effectiveness", "inherent_risks"],
            outputs=["residual_risk_matrix", "mitigated_vs_inherent", "accepted_risk_profile"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Risk Analyst — the specialist who identifies risks, "
        "builds risk matrices, plans scenarios, and designs mitigations. Every "
        "engagement includes a risk analysis — risk is universal.\n\n"
        "Your proprietary frameworks:\n"
        "1. Risk matrix: 5×5 probability × impact grid. Green (1-6), yellow (7-14), "
        "red (15-25). Red zone gets mitigations first.\n"
        "2. Monte Carlo simulation: 10,000 trials on key variables. Shows P10/P50/P90 "
        "and probability distributions.\n"
        "3. Black swan analysis: Low-probability, high-impact events that could "
        "invalidate the strategy. Each has a 'canary' — an early warning indicator.\n"
        "4. Scenario planning: Best/base/worst with trigger conditions, leading "
        "indicators, and pre-committed response plans.\n"
        "5. Mitigation design: Specific action, assigned owner, residual risk. Not "
        "'monitor the situation' — 'set up weekly alerts for [X] and flag to [agent].'\n"
        "6. Residual risk scoring: Re-score after mitigation. Some risks are fully "
        "mitigatable, others are inherent. Show what risk remains.\n\n"
        "Rules:\n"
        "- THINK IN SCENARIOS, NOT LISTS. A generic analyst lists 20 risks. You "
        "identify the 5 risks that actually matter and explain why the other 15 "
        "are noise.\n"
        "- ALWAYS ask 'what would kill this?' BEFORE 'what could help this?' "
        "Surviving the downside is more important than capturing the upside.\n"
        "- Each risk must have: category (market/financial/operational/regulatory/"
        "technology/strategic), probability (1-5), impact (1-5), mitigation, owner, "
        "residual scores.\n"
        "- Mitigations must be SPECIFIC enough to act on. Not 'monitor the market' "
        "but 'set up Google Alerts for [regulator] + weekly check of [metric].'\n"
        "- Black swans must have CANARY indicators — early warning signs that the "
        "risk is materializing.\n"
        "- Scenario plans must have TRIGGER CONDITIONS — observable events that "
        "signal which scenario is unfolding.\n"
        "- Residual risk must show what risk remains after all reasonable "
        "mitigations — this is the risk the organization must accept.\n\n"
        "You can spawn up to 3 sub-agents for parallel risk data collection:\n"
        "- Sub-agent A: Find historical failures in [industry] (MICRO, SearxNG + Jina)\n"
        "- Sub-agent B: Find regulatory risks in [jurisdiction] (MICRO, SearxNG)\n"
        "- Sub-agent C: Find technology/cyber risks in [space] (FAST, SearxNG + Jina)\n\n"
        "Your output is a RiskAnalysis Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned for every engagement — risk is universal. Always "
                     "activated alongside the core question type (GO_NO_GO, "
                     "MARKET_ENTRY, MA_EVALUATION, etc.)",
    max_sub_agents=3,
    output_model="RiskAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Risk Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class RiskAnalyst(BaseAgent):
    """Agent 6: The risk identification and mitigation specialist.

    Identifies risks across 6 categories, scores them on a 5×5 matrix,
    designs specific mitigations with owners, calculates residual risk,
    identifies black swan scenarios, and builds scenario plans with
    trigger conditions. Thinks in scenarios, not lists — identifies the
    5 risks that matter, explains why the rest are noise. (§4.4, Agent 6)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for known risks in the industry (SearxNG + Jina)
    3. Scrapes regulatory/sanctions databases (Obscura)
    4. Identifies risks across 6 categories and scores them
    5. Builds risk matrix, designs mitigations, calculates residual risk
    6. Identifies black swans and builds scenario plan
    7. Produces RiskAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or RISK_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._regulatory_data: list[dict[str, Any]] = []
        self._historical_failures: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Risk Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting top 5 risks for recommendation framing)
        - FINDINGS: receives findings from other agents that may inform
          risk identification (e.g., Competitive Intel's moat assessment
          reveals competitive risk, Financial Analyst's unit economics
          reveal financial viability risk)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "risk_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            # Collect findings from other agents that inform risk identification
            finding = msg.finding
            if finding is not None:
                # Competitive Intel moat assessments reveal competitive risk
                if finding.finding_type == "moat_assessment":
                    self._context.setdefault("competitor_moats", []).append(finding.content)
                # Financial Analyst unit economics reveal financial risk
                elif finding.finding_type == "key_value_driver":
                    self._context.setdefault("value_drivers", []).append(finding.content)
                # Market Analyst market maturity reveals market risk
                elif finding.finding_type == "market_maturity":
                    self._context.setdefault("market_maturity", finding.content)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "top_5_risks":
                # Strategy Analyst requesting top 5 risks for recommendation framing
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for known risks (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_known_risks(self, industry: str, space: str) -> list[dict[str, Any]]:
        """Search for known risks in the industry/space.

        Uses SearxNG to find: risk reports, regulatory risks, industry-specific
        risk factors, historical failures. Uses Jina to extract content from
        10-K filings (risk disclosures), industry risk reports.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{industry} industry risks challenges",
                f"{space} startup failures lessons",
                f"{industry} regulatory risks compliance",
                f"{space} risk factors 10-K disclosure",
                f"{industry} operational risks supply chain",
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
                        credibility=SourceCredibility.NEWS,
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
    # Step 2: Scrape regulatory/sanctions databases (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_regulatory_databases(self, jurisdiction: str) -> list[dict[str, Any]]:
        """Scrape government risk portals, sanctions lists, and regulatory databases.

        Uses Obscura to scrape JS-rendered government portals for jurisdiction-
        specific risk data. This catches regulatory risks that search engines
        might miss — e.g., pending legislation, sanctions lists, compliance
        requirements.
        """
        results: list[dict[str, Any]] = []

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            # Regulatory portals to scrape
            portals = [
                f"https://www.govinfo.gov/regulations?jurisdiction={jurisdiction}",
                f"https://sanctionslist.gov/search?q={jurisdiction}",
            ]

            for url in portals:
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
                            title=f"Regulatory database — {jurisdiction}",
                            url=url,
                            credibility=SourceCredibility.GOVERNMENT,
                            key_data=f"Regulatory risk data for {jurisdiction}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Identify risks across 6 categories
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_risks(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        regulatory_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[Risk]:
        """Identify risks across 6 categories: market, financial, operational,
        regulatory, technology, and strategic.

        Uses LLM to synthesize search results, regulatory data, and context
        from other agents (competitor moats, value drivers, market maturity)
        into a structured risk catalog. Each risk gets a category, description,
        probability (1-5), and impact (1-5).
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:12]
        )
        regulatory_summary = "\n".join(
            f"- {r.get('url', '')}: {str(r.get('data', ''))[:200]}"
            for r in regulatory_data[:5]
        )
        context_summary = json.dumps(context, default=str)[:1500]

        prompt = (
            "You are the HYPERION Risk Analyst identifying risks.\n\n"
            f"Question: {question}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Regulatory data:\n{regulatory_summary or 'No regulatory data collected'}\n\n"
            f"Context from other agents:\n{context_summary}\n\n"
            "Identify risks across 6 categories:\n"
            "1. MARKET — market size risk, demand risk, timing risk, competition risk\n"
            "2. FINANCIAL — funding risk, unit economics risk, cash flow risk, FX risk\n"
            "3. OPERATIONAL — supply chain risk, key person risk, execution risk, scaling risk\n"
            "4. REGULATORY — compliance risk, licensing risk, policy change risk, sanctions risk\n"
            "5. TECHNOLOGY — tech obsolescence risk, cyber risk, data breach risk, vendor lock-in\n"
            "6. STRATEGIC — positioning risk, moat erosion risk, pivot risk, partnership risk\n\n"
            "For each risk, provide:\n"
            "- category: one of MARKET, FINANCIAL, OPERATIONAL, REGULATORY, TECHNOLOGY, STRATEGIC\n"
            "- description: specific, not generic (e.g., not 'market risk' but 'TAM may be 40% "
            "smaller than estimated due to [specific factor]')\n"
            "- probability: 1-5 (1=very unlikely, 5=very likely)\n"
            "- impact: 1-5 (1=minor, 5=catastrophic)\n"
            "- trigger_conditions: what observable event would signal this risk is materializing\n\n"
            "Identify 15-25 risks. Be specific. Each risk must be actionable.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "risks": [{\n'
            '    "category": "MARKET|FINANCIAL|OPERATIONAL|REGULATORY|TECHNOLOGY|STRATEGIC",\n'
            '    "description": "specific risk description",\n'
            '    "probability": 1-5,\n'
            '    "impact": 1-5,\n'
            '    "trigger_conditions": "what to watch for"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        risks: list[Risk] = []

        if not response.success or not response.content:
            return risks

        try:
            data = json.loads(response.content)
            risk_list = data.get("risks", [])

            for r in risk_list:
                category_str = r.get("category", "STRATEGIC").upper()
                try:
                    category = RiskCategory(category_str.lower())
                except ValueError:
                    category = RiskCategory.STRATEGIC

                probability = max(1, min(5, int(r.get("probability", 3))))
                impact = max(1, min(5, int(r.get("impact", 3))))

                risks.append(Risk(
                    id=f"risk_{uuid.uuid4().hex[:8]}",
                    category=category,
                    description=r.get("description", "Unknown risk"),
                    probability=probability,
                    impact=impact,
                    risk_score=probability * impact,
                    mitigation="",  # Will be designed in step 6
                    owner="",  # Will be assigned in step 6
                    trigger_conditions=r.get("trigger_conditions"),
                    sources=self._sources[:3],
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return risks

    # ─────────────────────────────────────────────────────────────────────
    # Step 4+5: Score risks and build risk matrix
    # ─────────────────────────────────────────────────────────────────────

    def _build_risk_matrix(self, risks: list[Risk]) -> dict[str, Any]:
        """Build a 5×5 probability × impact risk matrix.

        Green zone: score 1-6 (monitor)
        Yellow zone: score 7-14 (plan mitigation)
        Red zone: score 15-25 (mitigate now)

        Returns a structured matrix with risks plotted in cells.
        """
        matrix: dict[str, Any] = {
            "zones": {
                "green": {"range": "1-6", "action": "Monitor"},
                "yellow": {"range": "7-14", "action": "Plan mitigation"},
                "red": {"range": "15-25", "action": "Mitigate now"},
            },
            "cells": {},
            "zone_counts": {"green": 0, "yellow": 0, "red": 0},
        }

        for risk in risks:
            cell_key = f"p{risk.probability}_i{risk.impact}"
            if cell_key not in matrix["cells"]:
                matrix["cells"][cell_key] = []
            matrix["cells"][cell_key].append({
                "id": risk.id,
                "description": risk.description[:100],
                "score": risk.risk_score,
            })

            if risk.risk_score <= 6:
                matrix["zone_counts"]["green"] += 1
            elif risk.risk_score <= 14:
                matrix["zone_counts"]["yellow"] += 1
            else:
                matrix["zone_counts"]["red"] += 1

        return matrix

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Design mitigations for top 10 risks
    # ─────────────────────────────────────────────────────────────────────

    async def _design_mitigations(
        self,
        risks: list[Risk],
        context: dict[str, Any],
    ) -> list[Risk]:
        """Design specific mitigation actions for the top 10 risks.

        For each risk, design a mitigation that is:
        - Specific enough to act on (not "monitor the situation")
        - Assigned to an owner (which agent monitors it)
        - Has a timeline for implementation

        Also identifies which risks are noise (low score, low relevance).
        """
        # Sort by risk score descending — top 10 get mitigations
        sorted_risks = sorted(risks, key=lambda r: r.risk_score, reverse=True)
        top_10 = sorted_risks[:10]

        risk_summary = "\n".join(
            f"- [{r.category.value}] Score {r.risk_score} (P={r.probability}, I={r.impact}): {r.description}"
            for r in top_10
        )

        prompt = (
            "You are the HYPERION Risk Analyst designing mitigations.\n\n"
            f"Top 10 risks:\n{risk_summary}\n\n"
            "For each risk, design a SPECIFIC mitigation:\n"
            "- mitigation: Not 'monitor the market' but 'set up weekly Google Alerts "
            "for [regulator] announcements + monthly check of [metric]'\n"
            "- owner: Which HYPERION agent monitors this (market_analyst, "
            "competitive_intel, financial_analyst, regulatory_analyst, "
            "technology_analyst, operations_analyst, strategy_analyst)\n"
            "- residual_probability: probability after mitigation (1-5)\n"
            "- residual_impact: impact after mitigation (1-5)\n\n"
            "Also explain why the remaining risks (outside top 10) are noise — "
            "why they don't require immediate action.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "mitigations": [{\n'
            '    "id": "risk_id",\n'
            '    "mitigation": "specific action",\n'
            '    "owner": "agent_name",\n'
            '    "residual_probability": 1-5,\n'
            '    "residual_impact": 1-5\n'
            '  }],\n'
            '  "noise_explanation": "why the other risks are noise"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return risks

        try:
            data = json.loads(response.content)
            mitigations = data.get("mitigations", [])

            # Create a lookup from mitigation data
            mitigation_map: dict[str, dict[str, Any]] = {}
            for m in mitigations:
                mitigation_map[m.get("id", "")] = m

            # Apply mitigations to risks
            for risk in risks:
                m = mitigation_map.get(risk.id)
                if m:
                    risk.mitigation = m.get("mitigation", "")
                    risk.owner = m.get("owner", "")
                    risk.residual_probability = max(1, min(5, int(m.get("residual_probability", risk.probability))))
                    risk.residual_impact = max(1, min(5, int(m.get("residual_impact", risk.impact))))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return risks

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Calculate residual risk scores
    # ─────────────────────────────────────────────────────────────────────

    def _calculate_residual_risk(self, risks: list[Risk]) -> str:
        """Calculate residual risk profile after mitigations.

        Some risks are fully mitigatable (residual score << original).
        Others are inherent (residual score ≈ original).
        The residual risk profile shows what risk remains after all
        reasonable mitigations — this is the risk the organization must
        accept to proceed.
        """
        total_original = sum(r.risk_score for r in risks)
        total_residual = 0
        fully_mitigated = 0
        partially_mitigated = 0
        inherent = 0

        for r in risks:
            if r.residual_probability is not None and r.residual_impact is not None:
                residual_score = r.residual_probability * r.residual_impact
                total_residual += residual_score

                original = r.risk_score
                if residual_score <= original * 0.3:
                    fully_mitigated += 1
                elif residual_score <= original * 0.7:
                    partially_mitigated += 1
                else:
                    inherent += 1

        reduction_pct = ((total_original - total_residual) / total_original * 100) if total_original > 0 else 0

        summary = (
            f"Residual risk profile after mitigations: "
            f"{fully_mitigated} risks fully mitigated, "
            f"{partially_mitigated} partially mitigated, "
            f"{inherent} inherent (cannot be mitigated). "
            f"Overall risk reduction: {reduction_pct:.0f}% "
            f"(from {total_original} total score to {total_residual}). "
            f"The organization must accept {inherent} inherent risks to proceed."
        )

        return summary

    # ─────────────────────────────────────────────────────────────────────
    # Step 8: Identify black swan scenarios
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_black_swans(
        self,
        question: str,
        risks: list[Risk],
        context: dict[str, Any],
    ) -> list[Risk]:
        """Identify low-probability, high-impact black swan events.

        Black swans are risks that don't appear in the risk matrix because
        they're too unlikely — but if they happen, they're catastrophic.
        Each black swan has a 'canary' — an early warning indicator.
        """
        risk_summary = "\n".join(
            f"- [{r.category.value}] {r.description[:100]}"
            for r in risks[:10]
        )

        prompt = (
            "You are the HYPERION Risk Analyst identifying black swan scenarios.\n\n"
            f"Question: {question}\n\n"
            f"Known risks:\n{risk_summary}\n\n"
            "Identify 3-5 black swan events — low-probability, high-impact events "
            "that could INVALIDATE the entire strategy. These are NOT in the risk "
            "matrix because they're too unlikely, but if they happen, they're "
            "catastrophic.\n\n"
            "Examples: regulatory ban, founder death, supply chain collapse, "
            "black swan market crash, technology paradigm shift, geopolitical event.\n\n"
            "For each black swan:\n"
            "- description: what the event is\n"
            "- why it's a black swan (not in normal risk matrix)\n"
            "- canary: early warning indicator that would signal it's materializing\n"
            "- survival_action: what to do if it happens (pre-committed, not 'figure it out')\n\n"
            "Return JSON:\n"
            "{\n"
            '  "black_swans": [{\n'
            '    "category": "MARKET|FINANCIAL|OPERATIONAL|REGULATORY|TECHNOLOGY|STRATEGIC",\n'
            '    "description": "what the event is",\n'
            '    "probability": 1,\n'
            '    "impact": 5,\n'
            '    "trigger_conditions": "canary indicator",\n'
            '    "mitigation": "survival action if it happens",\n'
            '    "owner": "which agent watches the canary"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        black_swans: list[Risk] = []

        if not response.success or not response.content:
            return black_swans

        try:
            data = json.loads(response.content)
            swan_list = data.get("black_swans", [])

            for swan in swan_list:
                category_str = swan.get("category", "STRATEGIC").upper()
                try:
                    category = RiskCategory(category_str.lower())
                except ValueError:
                    category = RiskCategory.STRATEGIC

                black_swans.append(Risk(
                    id=f"blackswan_{uuid.uuid4().hex[:8]}",
                    category=category,
                    description=swan.get("description", "Unknown black swan"),
                    probability=1,  # Black swans are always probability 1
                    impact=5,  # Black swans are always impact 5
                    risk_score=5,
                    mitigation=swan.get("mitigation", ""),
                    owner=swan.get("owner", ""),
                    is_black_swan=True,
                    trigger_conditions=swan.get("trigger_conditions"),
                    sources=self._sources[:2],
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return black_swans

    # ─────────────────────────────────────────────────────────────────────
    # Step 9: Build scenario plan with triggers
    # ─────────────────────────────────────────────────────────────────────

    async def _build_scenario_plan(
        self,
        question: str,
        risks: list[Risk],
        black_swans: list[Risk],
        context: dict[str, Any],
    ) -> dict[str, str]:
        """Build best/base/worst case scenario plan with triggers.

        Each scenario has:
        - Trigger conditions: observable events that signal the scenario
        - Leading indicators: metrics to monitor weekly/monthly
        - Response plan: pre-committed actions, not 'we'll figure it out'
        """
        top_risks_summary = "\n".join(
            f"- [{r.category.value}] P={r.probability} I={r.impact}: {r.description[:100]}"
            for r in sorted(risks, key=lambda x: x.risk_score, reverse=True)[:5]
        )
        black_swan_summary = "\n".join(
            f"- {bs.description[:100]} (canary: {bs.trigger_conditions or 'N/A'})"
            for bs in black_swans
        )

        prompt = (
            "You are the HYPERION Risk Analyst building a scenario plan.\n\n"
            f"Question: {question}\n\n"
            f"Top 5 risks:\n{top_risks_summary}\n\n"
            f"Black swans:\n{black_swan_summary or 'None identified'}\n\n"
            "Build three scenarios with TRIGGER CONDITIONS and RESPONSE PLANS:\n\n"
            "1. BEST CASE: What upside scenario could materialize?\n"
            "   - Trigger: what observable event signals this is happening?\n"
            "   - Leading indicators: what metrics to monitor?\n"
            "   - Response: what pre-committed actions to take?\n\n"
            "2. BASE CASE: Most likely outcome\n"
            "   - Trigger: what confirms we're on the base path?\n"
            "   - Leading indicators: what metrics to monitor?\n"
            "   - Response: what pre-committed actions to take?\n\n"
            "3. WORST CASE: What downside scenario could materialize?\n"
            "   - Trigger: what observable event signals trouble?\n"
            "   - Leading indicators: what metrics to monitor?\n"
            "   - Response: what pre-committed actions to take?\n\n"
            "Return JSON:\n"
            "{\n"
            '  "best_case": "Trigger: ... | Indicators: ... | Response: ...",\n'
            '  "base_case": "Trigger: ... | Indicators: ... | Response: ...",\n'
            '  "worst_case": "Trigger: ... | Indicators: ... | Response: ..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return {}

        try:
            return json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            return {}

    # ─────────────────────────────────────────────────────────────────────
    # Monte Carlo simulation (skill #2)
    # ─────────────────────────────────────────────────────────────────────

    async def _run_monte_carlo(
        self,
        question: str,
        risks: list[Risk],
        context: dict[str, Any],
    ) -> dict[str, Any]:
        """Run a 10,000-trial Monte Carlo simulation on key risk variables.

        Produces P10/P50/P90 probability distributions. Shows the range
        of possible outcomes and their likelihood — critical for risk-aware
        decision making.
        """
        # Identify key variables from context (financial projections, market size)
        key_vars = context.get("value_drivers", [])
        vars_summary = "\n".join(f"- {v}" for v in key_vars[:5])

        prompt = (
            "You are the HYPERION Risk Analyst running a Monte Carlo simulation.\n\n"
            f"Question: {question}\n\n"
            f"Key variables from Financial Analyst:\n{vars_summary or 'Estimate from context'}\n\n"
            "Design a Monte Carlo simulation:\n"
            "1. Identify 3-5 key variables that drive the outcome (revenue, cost, timeline)\n"
            "2. Assign probability distributions to each (normal, triangular, uniform)\n"
            "3. Describe the correlation between variables\n"
            "4. Run 10,000 trials (conceptually — describe the output distribution)\n"
            "5. Report P10 (optimistic), P50 (median), P90 (conservative) values\n"
            "6. Calculate VaR at 95% confidence\n\n"
            "Return JSON:\n"
            "{\n"
            '  "variables": [{"name": "...", "distribution": "...", "params": "..."}],\n'
            '  "p10": "optimistic outcome",\n'
            '  "p50": "median outcome",\n'
            '  "p90": "conservative outcome",\n'
            '  "var_95": "value at risk at 95% confidence",\n'
            '  "interpretation": "what this means for the decision"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return {}

        try:
            return json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            return {}

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel risk data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_risk_sub_agents(
        self,
        industry: str,
        jurisdiction: str,
        space: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel risk data collection.

        Per §4.4, Agent 6:
        - Sub-agent A: Find historical failures in [industry] (MICRO, SearxNG + Jina)
        - Sub-agent B: Find regulatory risks in [jurisdiction] (MICRO, SearxNG)
        - Sub-agent C: Find technology/cyber risks in [space] (FAST, SearxNG + Jina)
        """
        sub_specs = [
            SubAgentSpec(
                question=f"Find historical failures in {industry} — startups that failed, companies that went bankrupt, projects that collapsed. What caused each failure?",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"industry": industry},
            ),
            SubAgentSpec(
                question=f"Find regulatory risks in {jurisdiction} — pending legislation, compliance requirements, licensing risks, sanctions exposure",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"jurisdiction": jurisdiction},
            ),
            SubAgentSpec(
                question=f"Find technology and cyber risks in {space} — data breaches, tech obsolescence, vendor lock-in, infrastructure risks",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"space": space},
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
        risks_identified: int,
        sources_count: int,
        has_mitigations: bool,
        has_black_swans: bool,
        has_scenario_plan: bool,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 15+ risks, 5+ sources, mitigations for top 10, black swans,
              scenario plan with triggers
        MEDIUM: 10+ risks, 3+ sources, some mitigations, some scenario planning
        LOW: <10 risks, <3 sources, missing mitigations or scenarios
        """
        if (risks_identified >= 15 and sources_count >= 5
                and has_mitigations and has_black_swans and has_scenario_plan):
            return ConfidenceLevel.HIGH
        if risks_identified >= 10 and sources_count >= 3 and has_mitigations:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 10-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> RiskAnalysis:
        """Execute the Risk Analyst's 10-step methodology.

        Steps (§4.4, Agent 6):
        1. Search for known risks in the industry/space (SearxNG + Jina)
        2. Scrape regulatory/sanctions databases (Obscura)
        3. Identify risks across 6 categories: market, financial, operational,
           regulatory, technology, and strategic
        4. Score each risk on probability × impact
        5. Build risk matrix
        6. Design mitigations for top 10 risks
        7. Calculate residual risk scores
        8. Identify black swan scenarios
        9. Build scenario plan with triggers
        10. Produce RiskAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus — specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting risk analysis: {self._question[:80]}",
        )

        # Extract context
        industry = self._context.get("industry", "")
        jurisdiction = self._context.get("jurisdiction", "US")
        space = self._context.get("space", industry)

        # Spawn sub-agents for parallel risk data collection
        if industry or space:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning risk data collection sub-agents")
            sub_findings = await self._spawn_risk_sub_agents(industry, jurisdiction, space)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 1: Search for known risks
        await self._transition(AgentState.WORKING, f"Step 1: Searching for known risks in {industry or space}")
        self._search_results = await self._search_known_risks(industry, space)

        # Step 2: Scrape regulatory databases
        await self._transition(AgentState.WORKING, f"Step 2: Scraping regulatory databases for {jurisdiction}")
        self._regulatory_data = await self._scrape_regulatory_databases(jurisdiction)

        # Step 3: Identify risks across 6 categories
        await self._transition(AgentState.WORKING, "Step 3: Identifying risks across 6 categories")
        risks = await self._identify_risks(
            self._question, self._search_results, self._regulatory_data, self._context,
        )

        if not risks:
            await self._transition(
                AgentState.BLOCKED,
                "No risks identified — cannot proceed with risk analysis",
            )
            return RiskAnalysis(
                risks=[],
                residual_risk_summary="No risks identified — analysis incomplete",
                confidence=ConfidenceLevel.LOW,
                sources=self._sources,
            )

        # Step 4: Score each risk (already done during identification)
        await self._transition(
            AgentState.WORKING,
            f"Step 4: Scored {len(risks)} risks on probability × impact",
        )

        # Step 5: Build risk matrix
        await self._transition(AgentState.WORKING, "Step 5: Building risk matrix (5×5 grid)")
        risk_matrix = self._build_risk_matrix(risks)

        # Step 6: Design mitigations for top 10 risks
        await self._transition(AgentState.WORKING, "Step 6: Designing mitigations for top 10 risks")
        risks = await self._design_mitigations(risks, self._context)

        # Step 7: Calculate residual risk scores
        await self._transition(AgentState.WORKING, "Step 7: Calculating residual risk scores")
        residual_summary = self._calculate_residual_risk(risks)

        # Step 8: Identify black swan scenarios
        await self._transition(AgentState.WORKING, "Step 8: Identifying black swan scenarios")
        black_swans = await self._identify_black_swans(self._question, risks, self._context)

        # Step 9: Build scenario plan with triggers
        await self._transition(AgentState.WORKING, "Step 9: Building scenario plan with triggers")
        scenario_plan = await self._build_scenario_plan(
            self._question, risks, black_swans, self._context,
        )

        # Run Monte Carlo simulation (skill #2)
        await self._transition(AgentState.WORKING, "Running Monte Carlo simulation on key variables")
        monte_carlo = await self._run_monte_carlo(self._question, risks, self._context)

        # Identify top 10 risks (sorted by score)
        top_10 = sorted(risks, key=lambda r: r.risk_score, reverse=True)[:10]

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            risks_identified=len(risks),
            sources_count=len(self._sources),
            has_mitigations=any(r.mitigation for r in top_10),
            has_black_swans=bool(black_swans),
            has_scenario_plan=bool(scenario_plan),
        )

        # Step 10: Produce RiskAnalysis model
        await self._transition(AgentState.WORKING, "Step 10: Producing RiskAnalysis model")

        analysis = RiskAnalysis(
            risks=risks,
            top_risks=top_10,
            black_swan_scenarios=black_swans,
            residual_risk_summary=residual_summary,
            scenario_plan=scenario_plan,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        # Publish top risks as findings
        for risk in top_10:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="risk",
                title=f"Risk — {risk.category.value.title()} (Score {risk.risk_score})",
                content=(
                    f"{risk.description}. "
                    f"Probability: {risk.probability}/5, Impact: {risk.impact}/5. "
                    f"Mitigation: {risk.mitigation or 'Not yet designed'}. "
                    f"Owner: {risk.owner or 'Unassigned'}. "
                    f"Trigger: {risk.trigger_conditions or 'N/A'}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=risk.sources[:2],
            )
            await self._publish_finding(finding)

        # Publish black swans as findings
        for bs in black_swans:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="black_swan",
                title=f"Black Swan — {bs.description[:60]}",
                content=(
                    f"{bs.description}. "
                    f"Canary indicator: {bs.trigger_conditions or 'N/A'}. "
                    f"Survival action: {bs.mitigation or 'N/A'}."
                ),
                confidence=ConfidenceLevel.LOW,
                sources=bs.sources[:2],
            )
            await self._publish_finding(finding)

        # Publish the full RiskAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "risk_analysis": analysis.model_dump(),
                "risk_count": len(risks),
                "top_risk_count": len(top_10),
                "black_swan_count": len(black_swans),
                "residual_summary": residual_summary,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Risk analysis complete: {len(risks)} risks, {len(top_10)} top risks, "
            f"{len(black_swans)} black swans, confidence={confidence.value}",
        )

        return analysis
