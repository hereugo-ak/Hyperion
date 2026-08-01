"""
HYPERION Regulatory Analyst, Agent 9, the regulatory intelligence specialist.

This is NOT a generic "list the regulations" agent. This is a specialist with
5 proprietary analytical frameworks:

- Regulatory mapping: Map all regulations applicable to the business across
  jurisdictions. Categorize by type (data protection, financial, industry-
  specific, labor, environmental, tax, consumer protection, antitrust).
- Jurisdiction comparison: Compare regulatory requirements across
  jurisdictions (US, EU, India, etc.) to identify the most favorable
  regulatory environment and the most restrictive.
- Compliance checklist: Build a structured compliance checklist with specific
  requirements, documentation needed, and estimated compliance cost.
- Regulatory horizon scanning: Identify pending regulations, proposed rules,
  and regulatory trends that could impact the business in 1-3 years.
- Precedent analysis: Find regulatory enforcement actions against similar
  companies to understand regulatory priorities and penalties.

It knows it is not a lawyer. It maps the landscape, identifies risks, and
recommends legal counsel for definitive opinions. It doesn't give legal
advice, it gives regulatory intelligence. It tracks regulatory evolution
using Wayback Machine, not just current state. It always identifies the
jurisdiction with the lightest regulatory touch as a potential strategic
advantage. (§4.4, Agent 9)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura, Wayback
Sub-agents: Max 3, jurisdiction1 regulations, jurisdiction2 regulations,
            pending/proposed regulations
Output: RegulatoryAnalysis (regulatory map, jurisdiction comparison,
        compliance checklist, horizon scan, enforcement precedents,
        lightest jurisdiction, regulatory evolution, legal disclaimer,
        confidence, sources)

Methodology (§4.4, Agent 9):
1. Search for applicable regulations (SearxNG + Jina)
2. Scrape government portals for current rules (Obscura)
3. Pull historical regulatory data (Wayback)
4. Map regulations by jurisdiction
5. Build compliance checklist
6. Scan regulatory horizon
7. Analyze enforcement precedents
8. Produce RegulatoryAnalysis model
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from typing import Any

from pydantic import ValidationError

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
    ComplianceItem,
    ConfidenceLevel,
    EnforcementPrecedent,
    JurisdictionComparison,
    KeyFinding,
    Regulation,
    RegulationType,
    RegulatoryAnalysis,
    RegulatoryHorizonItem,
    Source,
    SourceCredibility,
)
from hyperion.tools.query_utils import resolve_subject

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


REGULATORY_ANALYST_SPEC = AgentSpec(
    name=AgentName.REGULATORY_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Regulatory Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.WAYBACK,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Regulatory mapping",
            description=(
                "Map all regulations applicable to the business across "
                "jurisdictions. Categorize by type: data protection (GDPR, CCPA, "
                "DPDP), financial (SEC, RBI, MiFID II), industry-specific (FDA, "
                "FAA, FERC), labor (wage, safety, discrimination), environmental "
                "(EPA, emissions, waste), tax (corporate, VAT/GST), consumer "
                "protection (FTC), antitrust (competition law). Each regulation "
                "has a risk level and estimated compliance cost."
            ),
            inputs=["business_model", "jurisdictions", "industry", "data_practices"],
            outputs=["regulatory_map", "regulation_types", "risk_levels", "compliance_costs"],
        ),
        SkillSpec(
            name="Jurisdiction comparison",
            description=(
                "Compare regulatory requirements across jurisdictions (US, EU, "
                "India, UK, etc.) to identify the most favorable regulatory "
                "environment and the most restrictive. The jurisdiction with the "
                "lightest regulatory touch can be a strategic advantage, not "
                "for evasion, but for operational efficiency. Score each "
                "jurisdiction on regulation count, compliance burden, and "
                "estimated annual cost."
            ),
            inputs=["jurisdictions", "regulatory_map", "business_model"],
            outputs=["jurisdiction_rankings", "compliance_burden_scores", "lightest_jurisdiction", "strategic_assessment"],
        ),
        SkillSpec(
            name="Compliance checklist",
            description=(
                "Build a structured compliance checklist with specific "
                "requirements, documentation needed, and estimated compliance "
                "cost. Each item is actionable, not 'comply with GDPR' but "
                "'appoint a Data Protection Officer within 30 days, estimated "
                "cost $80K/yr, documentation: DPO appointment letter, reporting "
                "structure.' Items are prioritized by risk level."
            ),
            inputs=["regulatory_map", "business_operations", "current_compliance_status"],
            outputs=["checklist_items", "documentation_requirements", "cost_estimates", "priorities", "timelines"],
        ),
        SkillSpec(
            name="Regulatory horizon scanning",
            description=(
                "Identify pending regulations, proposed rules, and regulatory "
                "trends that could impact the business in 1-3 years. Each item "
                "has a probability assessment (low/medium/high), potential impact, "
                "and recommended preparatory action. Uses Wayback Machine to "
                "track how regulations have evolved, a regulation that's been "
                "tightening for 3 years is likely to continue tightening."
            ),
            inputs=["industry", "jurisdictions", "regulatory_trends", "historical_data"],
            outputs=["pending_regulations", "probability_assessments", "impact_assessments", "preparatory_actions"],
        ),
        SkillSpec(
            name="Precedent analysis",
            description=(
                "Find regulatory enforcement actions against similar companies "
                "to understand regulatory priorities and penalties. Not just "
                "'company X was fined $Y', what did they do wrong, what was the "
                "penalty, and what does it tell us about where the regulator "
                "focuses enforcement? This reveals regulatory priorities beyond "
                "the written rules."
            ),
            inputs=["industry", "company_comparables", "regulatory_map"],
            outputs=["enforcement_cases", "penalty_analysis", "regulatory_priorities", "lessons_learned"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Regulatory Analyst, the specialist who maps "
        "regulatory landscapes, identifies compliance requirements, assesses "
        "regulatory risks, and scans the regulatory horizon.\n\n"
        "Your proprietary frameworks:\n"
        "1. Regulatory mapping: All applicable regulations across jurisdictions. "
        "Categorize by type (data protection, financial, industry-specific, "
        "labor, environmental, tax, consumer protection, antitrust). Each has "
        "risk level and compliance cost.\n"
        "2. Jurisdiction comparison: Compare regulatory burden across "
        "jurisdictions. Identify the lightest regulatory touch as a strategic "
        "advantage, not for evasion, but for operational efficiency.\n"
        "3. Compliance checklist: Actionable items, not 'comply with X.' Each "
        "item has documentation needed, cost estimate, timeline, and priority.\n"
        "4. Horizon scanning: Pending regulations (1-3 years). Probability, "
        "impact, and preparatory actions. Use Wayback Machine to track "
        "regulatory evolution, tightening trends continue tightening.\n"
        "5. Precedent analysis: Enforcement actions against similar companies. "
        "What was the violation, penalty, and lesson? Reveals regulatory "
        "priorities beyond written rules.\n\n"
        "Rules:\n"
        "- YOU ARE NOT A LAWYER. You provide regulatory intelligence, not legal "
        "advice. Always include the disclaimer: 'This is regulatory intelligence, "
        "not legal advice. Consult qualified legal counsel for definitive opinions.'\n"
        "- ALWAYS identify the jurisdiction with the lightest regulatory touch "
        "as a potential strategic advantage.\n"
        "- Track regulatory EVOLUTION using Wayback Machine, not just current "
        "state. A regulation that's been tightening for 3 years will continue.\n"
        "- Compliance checklist items must be SPECIFIC and ACTIONABLE, not "
        "'comply with GDPR' but 'appoint a DPO within 30 days, cost $80K/yr.'\n"
        "- Enforcement precedents must include the LESSON, what does this tell "
        "us about regulatory priorities?\n"
        "- Each regulation must have: risk level, compliance cost, and penalty "
        "range for non-compliance.\n\n"
        "You can spawn up to 3 sub-agents for parallel regulatory research:\n"
        "- Sub-agent A: Find regulations for [jurisdiction1] (MICRO, SearxNG + Jina)\n"
        "- Sub-agent B: Find regulations for [jurisdiction2] (MICRO, SearxNG + Jina)\n"
        "- Sub-agent C: Find pending/proposed regulations (FAST, SearxNG + Obscura)\n\n"
        "Your output is a RegulatoryAnalysis Pydantic model, structured, not free text."
    ),
    spawn_condition="Spawned when the question involves regulatory compliance, "
                     "jurisdictional analysis, regulatory risk, or regulatory "
                     "horizon scanning (REGULATORY_ANALYSIS, COMPLIANCE, "
                     "JURISDICTION_COMPARISON types)",
    max_sub_agents=3,
    output_model="RegulatoryAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Regulatory Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class RegulatoryAnalyst(BaseAgent):
    """Agent 9: The regulatory intelligence specialist.

    Maps regulatory landscapes across jurisdictions, builds compliance
    checklists, scans the regulatory horizon for pending regulations, and
    analyzes enforcement precedents. Tracks regulatory evolution using
    Wayback Machine. Always identifies the lightest-touch jurisdiction as
    a strategic advantage. Knows it's not a lawyer, gives regulatory
    intelligence, not legal advice. (§4.4, Agent 9)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for applicable regulations (SearxNG + Jina)
    3. Scrapes government portals for current rules (Obscura)
    4. Pulls historical regulatory data (Wayback)
    5. Maps regulations by jurisdiction, builds compliance checklist
    6. Scans horizon, analyzes enforcement precedents
    7. Produces RegulatoryAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or REGULATORY_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._government_data: list[dict[str, Any]] = []
        self._historical_snapshots: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Regulatory Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting jurisdictional comparison for market entry strategy)
        - FINDINGS: receives findings from other agents that may inform
          regulatory analysis (e.g., Market Analyst's target markets,
          Risk Analyst's regulatory risk flags)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "regulatory_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Market Analyst's target markets inform which jurisdictions to analyze
                if finding.finding_type == "target_markets":
                    self._context.setdefault("jurisdictions", []).append(finding.content)
                # Risk Analyst's regulatory risk flags inform priority
                elif finding.finding_type == "regulatory_risk":
                    self._context.setdefault("regulatory_risks", []).append(finding.content)

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
            if request_type == "jurisdiction_comparison":
                # Strategy Analyst requesting jurisdiction comparison for market entry
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for applicable regulations (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_regulations(self, industry: str, jurisdictions: list[str]) -> list[dict[str, Any]]:
        """Search for applicable regulations across jurisdictions.

        Uses SearxNG to find: regulations, compliance requirements, regulatory
        news, and legal analyses. Uses Jina to extract regulatory documents,
        compliance guides, and legal commentaries.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            # Build the subject anchor from whatever the engagement actually
            # gave us. `industry` is resolved via `resolve_subject` in `run()`
            # now, but this method is also reachable with a bare `industry`
            # string from other callers, so re-resolve defensively rather
            # than assume the caller already did it, an f-string like
            # f"{industry} regulations" degrading to " regulations" is a
            # subject-less search that cannot answer the user's question.
            subject = resolve_subject(
                self._context, "industry", "sector", question=self._question
            ) or industry

            query_patterns: list[str] = []
            for jurisdiction in jurisdictions:
                query_patterns.extend([
                    f"{subject} regulations {jurisdiction} compliance requirements",
                    f"{subject} regulatory framework {jurisdiction} policy",
                    f"{subject} compliance cost {jurisdiction}",
                    f"{subject} regulatory penalties {jurisdiction}",
                ])

            # Cross-cutting regulatory themes. Deliberately NOT naming
            # specific regimes: the old query hardcoded "GDPR CCPA DPDP",
            # which forced European and Californian privacy law onto every
            # engagement regardless of geography. Naming the *theme* lets the
            # search engine surface whichever regime is actually in force for
            # the jurisdiction under analysis.
            themes = [
                "data protection privacy law",
                "licensing permits statutory requirements",
                "labor law wage safety compliance",
                "environmental regulations emissions waste",
                "trade policy tariffs import export controls",
            ]
            geo_hint = jurisdictions[0] if jurisdictions else ""
            for theme in themes:
                query_patterns.append(f"{subject} {theme} {geo_hint}".strip())

            for pattern in query_patterns[:15]:  # Cap to avoid excessive searches
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
                        credibility=SourceCredibility.GOVERNMENT,
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
    # Step 2: Scrape government portals for current rules (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_government_portals(self, jurisdictions: list[str]) -> list[dict[str, Any]]:
        """Scrape government regulatory portals, agency websites, and
        regulatory databases that require JS rendering.

        Uses Obscura to scrape: government regulatory portals, agency websites
        (SEC.gov, ECB.europa.eu, RBI.org.in, etc.), and regulatory databases.
        """
        results: list[dict[str, Any]] = []

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            # Government regulatory portals by jurisdiction
            portal_urls = {
                "US": [
                    "https://www.sec.gov/regulations",
                    "https://www.ftc.gov/legal-library",
                    "https://www.epa.gov/regulations",
                ],
                "EU": [
                    "https://eur-lex.europa.eu",
                    "https://ec.europa.eu/info/law_en",
                ],
                "India": [
                    "https://www.rbi.org.in/scripts/Regulations.aspx",
                    "https://www.meity.gov.in/data-protection-framework",
                ],
                "UK": [
                    "https://www.legislation.gov.uk",
                    "https://www.fca.org.uk/regulation",
                ],
            }

            urls_to_scrape: list[str] = []
            for jurisdiction in jurisdictions:
                urls = portal_urls.get(jurisdiction, [])
                urls_to_scrape.extend(urls)

            # If we have jurisdictions but no curated portal URLs for them
            # (this map only covers US/EU/India/UK), discover official sources
            # by search instead of silently scraping nothing. Without this, a
            # question about e.g. Vietnam or Brazil produced an empty
            # government-data set with no explanation in the report.
            unmapped = [j for j in jurisdictions if j not in portal_urls]
            if unmapped:
                try:
                    searxng = self.get_tool(ToolName.SEARXNG)
                    # `industry` is not a parameter of this method, resolve
                    # the subject the same way `run()` does, so the discovery
                    # query is never subject-less.
                    sector = resolve_subject(
                        self._context, "industry", "sector", question=self._question
                    )
                    for jurisdiction in unmapped[:2]:
                        discovery = await searxng.search(
                            f"{jurisdiction} official government regulatory authority "
                            f"regulations {sector}".strip(),
                            max_results=4,
                        )
                        for r in (getattr(discovery, "results", None) or [])[:3]:
                            url = getattr(r, "url", "")
                            if url and url not in urls_to_scrape:
                                urls_to_scrape.append(url)
                    if urls_to_scrape:
                        self._log(
                            f"REGULATORY: discovered {len(urls_to_scrape)} official "
                            f"source(s) by search for unmapped jurisdiction(s): {unmapped}"
                        )
                except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
                    self._log(
                        f"REGULATORY: portal discovery search failed "
                        f"({type(e).__name__}); continuing with curated portals only"
                    )

            for url in urls_to_scrape[:8]:  # Cap to avoid excessive scraping
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
                            title=f"Government portal, {url.split('/')[2]}",
                            url=url,
                            credibility=SourceCredibility.GOVERNMENT,
                            key_data=f"Regulatory portal data from {url}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Pull historical regulatory data (Wayback)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_historical_data(self, jurisdictions: list[str], industry: str) -> list[dict[str, Any]]:
        """Pull historical regulatory snapshots to track how regulations have
        evolved over time.

        Uses Wayback Machine to pull historical snapshots of regulatory pages.
        A regulation that's been tightening for 3 years is likely to continue
        tightening. This is unique to the HYPERION Regulatory Analyst, no
        generic regulatory analyst tracks evolution over time.
        """
        results: list[dict[str, Any]] = []

        try:
            wayback = self.get_tool(ToolName.WAYBACK)

            # Key regulatory URLs to track historically
            tracking_urls = [
                "https://www.sec.gov/regulations",
                "https://ec.europa.eu/info/law_en",
                "https://www.rbi.org.in/scripts/Regulations.aspx",
            ]

            for url in tracking_urls:
                try:
                    snapshots = await wayback.get_snapshots(url, years_back=3)
                    if snapshots:
                        results.append({
                            "url": url,
                            "snapshots": snapshots,
                        })
                        self._sources.append(Source(
                            id=f"src_{len(self._sources):03d}",
                            title=f"Historical regulatory snapshots, {url}",
                            url=url,
                            credibility=SourceCredibility.GOVERNMENT,
                            key_data=f"3-year regulatory evolution for {url}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Map regulations by jurisdiction
    # ─────────────────────────────────────────────────────────────────────

    async def _map_regulations(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        government_data: list[dict[str, Any]],
        jurisdictions: list[str],
        context: dict[str, Any],
    ) -> tuple[list[Regulation], list[JurisdictionComparison]]:
        """Map all applicable regulations by jurisdiction and compare
        jurisdictions.

        Returns (regulatory_map, jurisdiction_comparison).
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:15]
        )
        gov_summary = json.dumps(
            [{"url": d.get("url", ""), "data": str(d.get("data", ""))[:300]} for d in government_data[:5]],
            default=str,
        )[:1000]

        prompt = (
            "You are the HYPERION Regulatory Analyst mapping regulations by jurisdiction.\n\n"
            f"Question: {question}\n\n"
            f"Jurisdictions: {', '
                ''.join(jurisdictions) if jurisdictions else 'NOT SPECIFIED by the user, infer the relevant jurisdiction(s) from the question and the evidence below; do NOT assume US or EU'}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Government portal data:\n{gov_summary}\n\n"
            "Map ALL applicable regulations across jurisdictions:\n"
            "For each regulation:\n"
            "- name: regulation name (e.g., 'GDPR', 'CCPA', 'DPDP Act 2023')\n"
            "- jurisdiction: jurisdiction (e.g., 'EU', 'US-California', 'India')\n"
            "- regulation_type: one of data_protection, financial, industry_specific, "
            "labor, environmental, tax, consumer_protection, antitrust\n"
            "- description: what the regulation requires\n"
            "- key_requirements: specific compliance requirements (list)\n"
            "- penalty_range: range of penalties for non-compliance\n"
            "- compliance_cost: estimated compliance cost ($)\n"
            "- risk_level: low, medium, high, or critical\n"
            "- effective_date: when it takes effect\n\n"
            "Also compare jurisdictions:\n"
            "- regulation_count: number of applicable regulations\n"
            "- compliance_burden: low, medium, high, or very high\n"
            "- estimated_annual_cost: estimated annual compliance cost ($)\n"
            "- key_advantages: regulatory advantages of this jurisdiction\n"
            "- key_disadvantages: regulatory disadvantages\n"
            "- strategic_assessment: strategic implications\n\n"
            "Return JSON:\n"
            "{\n"
            '  "regulations": [{...}],\n'
            '  "jurisdiction_comparison": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        regulations: list[Regulation] = []
        jurisdiction_comparison: list[JurisdictionComparison] = []

        if not response.success or not response.content:
            return (regulations, jurisdiction_comparison)

        try:
            data = json.loads(response.content)

            reg_type_map = {
                "data_protection": RegulationType.DATA_PROTECTION,
                "financial": RegulationType.FINANCIAL,
                "industry_specific": RegulationType.INDUSTRY_SPECIFIC,
                "labor": RegulationType.LABOR,
                "environmental": RegulationType.ENVIRONMENTAL,
                "tax": RegulationType.TAX,
                "consumer_protection": RegulationType.CONSUMER_PROTECTION,
                "antitrust": RegulationType.ANTITRUST,
            }

            for reg in data.get("regulations", []):
                reg_type_str = reg.get("regulation_type", "industry_specific")
                reg_type = reg_type_map.get(reg_type_str, RegulationType.INDUSTRY_SPECIFIC)

                regulations.append(Regulation(
                    name=reg.get("name", "Unknown"),
                    jurisdiction=reg.get("jurisdiction", "Unknown"),
                    regulation_type=reg_type,
                    description=reg.get("description", ""),
                    key_requirements=reg.get("key_requirements", []),
                    penalty_range=reg.get("penalty_range", ""),
                    compliance_cost=reg.get("compliance_cost", ""),
                    risk_level=reg.get("risk_level", "medium"),
                    effective_date=reg.get("effective_date", ""),
                    sources=self._sources[:2],
                ))

            for jc in data.get("jurisdiction_comparison", []):
                jurisdiction_comparison.append(JurisdictionComparison(
                    jurisdiction=jc.get("jurisdiction", "Unknown"),
                    regulation_count=int(jc.get("regulation_count", 0)),
                    compliance_burden=jc.get("compliance_burden", "medium"),
                    estimated_annual_cost=jc.get("estimated_annual_cost", ""),
                    key_advantages=jc.get("key_advantages", []),
                    key_disadvantages=jc.get("key_disadvantages", []),
                    strategic_assessment=jc.get("strategic_assessment", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (regulations, jurisdiction_comparison)

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Build compliance checklist
    # ─────────────────────────────────────────────────────────────────────

    async def _build_compliance_checklist(
        self,
        question: str,
        regulations: list[Regulation],
        context: dict[str, Any],
    ) -> list[ComplianceItem]:
        """Build a structured compliance checklist.

        Each item is actionable, not 'comply with GDPR' but 'appoint a Data
        Protection Officer within 30 days, estimated cost $80K/yr, documentation:
        DPO appointment letter, reporting structure.' Items are prioritized
        by risk level.
        """
        reg_summary = "\n".join(
            f"- {r.name} ({r.jurisdiction}): {r.description[:150]} | "
            f"Risk: {r.risk_level} | Cost: {r.compliance_cost}"
            for r in regulations
        )

        prompt = (
            "You are the HYPERION Regulatory Analyst building a compliance checklist.\n\n"
            f"Question: {question}\n\n"
            f"Applicable regulations:\n{reg_summary}\n\n"
            "Build a structured compliance checklist. Each item must be SPECIFIC and ACTIONABLE:\n"
            "- requirement: not 'comply with GDPR' but 'appoint a Data Protection Officer within 30 days'\n"
            "- regulation: which regulation this satisfies\n"
            "- documentation_needed: specific documents required\n"
            "- estimated_cost: estimated compliance cost ($)\n"
            "- estimated_timeline: time to achieve compliance\n"
            "- priority: low, medium, high, or critical (based on risk level)\n"
            "- status: not_started (default)\n\n"
            "Return JSON:\n"
            "{\n"
            '  "checklist": [{\n'
            '    "requirement": "...",\n'
            '    "regulation": "...",\n'
            '    "documentation_needed": ["doc1", "doc2"],\n'
            '    "estimated_cost": "$...",\n'
            '    "estimated_timeline": "...",\n'
            '    "priority": "critical|high|medium|low",\n'
            '    "status": "not_started"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        checklist: list[ComplianceItem] = []

        if not response.success or not response.content:
            return checklist

        try:
            data = json.loads(response.content)
            for item in data.get("checklist", []):
                checklist.append(ComplianceItem(
                    requirement=item.get("requirement", ""),
                    regulation=item.get("regulation", ""),
                    documentation_needed=item.get("documentation_needed", []),
                    estimated_cost=item.get("estimated_cost", ""),
                    estimated_timeline=item.get("estimated_timeline", ""),
                    priority=item.get("priority", "medium"),
                    status=item.get("status", "not_started"),
                ))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return checklist

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Scan regulatory horizon
    # ─────────────────────────────────────────────────────────────────────

    async def _scan_horizon(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        historical_snapshots: list[dict[str, Any]],
        jurisdictions: list[str],
        context: dict[str, Any],
    ) -> list[RegulatoryHorizonItem]:
        """Scan the regulatory horizon for pending and proposed regulations.

        Identifies pending regulations, proposed rules, and regulatory trends
        that could impact the business in 1-3 years. Uses historical data
        from Wayback Machine to identify trends, a regulation that's been
        tightening for 3 years is likely to continue tightening.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )
        historical_summary = json.dumps(
            [{"url": s.get("url", ""), "snapshots": str(s.get("snapshots", ""))[:200]} for s in historical_snapshots[:3]],
            default=str,
        )[:800]

        prompt = (
            "You are the HYPERION Regulatory Analyst scanning the regulatory horizon.\n\n"
            f"Question: {question}\n\n"
            f"Jurisdictions: {', '
                ''.join(jurisdictions) if jurisdictions else 'NOT SPECIFIED by the user, infer the relevant jurisdiction(s) from the question and the evidence below; do NOT assume US or EU'}\n\n"
            f"Current regulatory search results:\n{search_summary}\n\n"
            f"Historical regulatory evolution (Wayback Machine):\n{historical_summary or 'No '
                'historical data available'}\n\n"
            "Identify pending regulations, proposed rules, and regulatory trends (1-3 year horizon):\n"
            "For each item:\n"
            "- regulation_name: name of pending/proposed regulation\n"
            "- jurisdiction: jurisdiction\n"
            "- status: proposed, draft, consultation, or pending_vote\n"
            "- timeline: expected timeline (e.g., 'Q3 2025', '2026-2027')\n"
            "- probability: low, medium, or high (based on historical trends)\n"
            "- potential_impact: potential impact on the business\n"
            "- recommended_action: what to do NOW to prepare\n\n"
            "Use historical data to assess probability, a regulation that's been "
            "tightening for 3 years is likely to continue tightening (high probability).\n\n"
            "Return JSON:\n"
            "{\n"
            '  "horizon_items": [{\n'
            '    "regulation_name": "...",\n'
            '    "jurisdiction": "...",\n'
            '    "status": "...",\n'
            '    "timeline": "...",\n'
            '    "probability": "low|medium|high",\n'
            '    "potential_impact": "...",\n'
            '    "recommended_action": "..."\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        horizon_items: list[RegulatoryHorizonItem] = []

        if not response.success or not response.content:
            return horizon_items

        # D5.1: this block used to end in `except (json.JSONDecodeError,
        # ValueError, TypeError): pass`, and that silence is what let the
        # HorizonScanItem name collision (see RegulatoryHorizonItem's docstring)
        # hide for the project's entire life. `ValidationError` subclasses
        # `ValueError`, so *every* item failed construction and every failure was
        # swallowed, `horizon_scan` was structurally guaranteed `[]` with nothing
        # logged anywhere.
        #
        # Two changes make that impossible to repeat:
        #   1. Per-item construction, so one malformed item costs one item rather
        #      than aborting the whole batch at the first bad element.
        #   2. Loud logging, and a `ValidationError` is logged as a *schema* error
        #      (a bug on our side) rather than lumped in with bad model output.
        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "Regulatory horizon scan: model returned unparseable JSON: %s", exc
            )
            return horizon_items

        raw_items = data.get("horizon_items", []) if isinstance(data, dict) else []
        if not isinstance(raw_items, list):
            logger.warning(
                "Regulatory horizon scan: 'horizon_items' is %s, expected list",
                type(raw_items).__name__,
            )
            return horizon_items

        for item in raw_items:
            if not isinstance(item, dict):
                logger.debug("Regulatory horizon scan: skipping non-dict item %r", item)
                continue

            # D5.1b: these two fields used to default to the literal string
            # "Unknown". That is wrong twice over. First, "Unknown" is one of the
            # template-leak tokens the render probe counts and §11 criterion 11
            # requires to be zero, so an unnamed item would print as a regulation
            # called "Unknown" in a client-facing compliance deliverable. Second
            # and worse: a horizon item whose regulation cannot be named is not a
            # finding at all, emitting it fabricates a phantom regulation the
            # model never actually identified. An unnamed item is dropped, loudly.
            regulation_name = str(item.get("regulation_name") or "").strip()
            jurisdiction = str(item.get("jurisdiction") or "").strip()
            if not regulation_name:
                logger.warning(
                    "Regulatory horizon scan: dropping item with no "
                    "regulation_name (keys offered: %s), an unnameable "
                    "regulation is not a finding",
                    sorted(item),
                )
                continue

            try:
                horizon_items.append(RegulatoryHorizonItem(
                    regulation_name=regulation_name,
                    jurisdiction=jurisdiction or "Not specified",
                    status=item.get("status", "proposed"),
                    timeline=item.get("timeline", ""),
                    probability=item.get("probability", "medium"),
                    potential_impact=item.get("potential_impact", ""),
                    recommended_action=item.get("recommended_action", ""),
                    sources=self._sources[:2],
                ))
            except ValidationError:
                # A schema mismatch is OUR bug, not bad model output. Log it as
                # such, with a stack trace, so it can never again be mistaken for
                # "the LLM returned nothing useful".
                logger.error(
                    "Regulatory horizon scan: RegulatoryHorizonItem rejected a "
                    "well-formed item, this is a SCHEMA defect, not model output. "
                    "Keys offered: %s",
                    sorted(item),
                    exc_info=True,
                )
            except (ValueError, TypeError) as exc:
                logger.warning("Regulatory horizon scan: skipping malformed item: %s", exc)

        if raw_items and not horizon_items:
            # The exact shape of the outage this fix closes: the model produced
            # items and none survived construction.
            logger.error(
                "Regulatory horizon scan: %d items offered, 0 constructed"
                "horizon_scan will be empty",
                len(raw_items),
            )

        return horizon_items

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Analyze enforcement precedents
    # ─────────────────────────────────────────────────────────────────────

    async def _analyze_precedents(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        regulations: list[Regulation],
        industry: str,
        context: dict[str, Any],
    ) -> list[EnforcementPrecedent]:
        """Analyze regulatory enforcement actions against similar companies.

        Finds enforcement actions to understand regulatory priorities and
        penalties. Not just 'company X was fined $Y', what did they do,
        what was the penalty, and what does it tell us about regulatory
        priorities?
        """
        reg_names = [r.name for r in regulations[:8]]
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )

        prompt = (
            "You are the HYPERION Regulatory Analyst analyzing enforcement precedents.\n\n"
            f"Question: {question}\n\n"
            f"Industry: {industry}\n\n"
            f"Applicable regulations: {', '.join(reg_names) or 'None identified'}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Find regulatory enforcement actions against SIMILAR companies:\n"
            "For each case:\n"
            "- company: company that was penalized\n"
            "- regulation: which regulation was violated\n"
            "- violation: what the company did wrong (specific)\n"
            "- penalty: penalty amount and type (fine, consent decree, ban, etc.)\n"
            "- date: when the enforcement action occurred\n"
            "- lesson: what this tells us about regulatory priorities\n"
            "- relevance: how relevant this is to our situation (high/medium/low)\n\n"
            "Return JSON:\n"
            "{\n"
            '  "precedents": [{\n'
            '    "company": "...",\n'
            '    "regulation": "...",\n'
            '    "violation": "...",\n'
            '    "penalty": "...",\n'
            '    "date": "...",\n'
            '    "lesson": "...",\n'
            '    "relevance": "high|medium|low"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        precedents: list[EnforcementPrecedent] = []

        if not response.success or not response.content:
            return precedents

        try:
            data = json.loads(response.content)
            for p in data.get("precedents", []):
                precedents.append(EnforcementPrecedent(
                    company=p.get("company", "Unknown"),
                    regulation=p.get("regulation", ""),
                    violation=p.get("violation", ""),
                    penalty=p.get("penalty", ""),
                    date=p.get("date", ""),
                    lesson=p.get("lesson", ""),
                    relevance=p.get("relevance", "medium"),
                    sources=self._sources[:2],
                ))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return precedents

    # ─────────────────────────────────────────────────────────────────────
    # Identify lightest jurisdiction and regulatory evolution
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_lightest_jurisdiction_and_evolution(
        self,
        jurisdiction_comparison: list[JurisdictionComparison],
        historical_snapshots: list[dict[str, Any]],
        regulations: list[Regulation],
    ) -> tuple[str, str]:
        """Identify the jurisdiction with the lightest regulatory touch and
        summarize regulatory evolution.

        Returns (lightest_jurisdiction, regulatory_evolution).
        """
        if not jurisdiction_comparison:
            return ("", "")

        burden_rank = {"low": 1, "medium": 2, "high": 3, "very high": 4}
        lightest = min(
            jurisdiction_comparison,
            key=lambda j: burden_rank.get((j.compliance_burden or "").lower(), 2),
        )

        # Build evolution summary from historical data
        evolution_parts: list[str] = []
        for snapshot in historical_snapshots:
            url = snapshot.get("url", "")
            snapshots_data = snapshot.get("snapshots", [])
            if snapshots_data:
                evolution_parts.append(
                    f"{url}: {len(snapshots_data)} historical snapshots tracked over 3 years. "
                    f"Trend analysis shows regulatory {'tightening' if len(snapshots_data) > 2 else 'stability'}."
                )

        evolution = " ".join(evolution_parts) if evolution_parts else (
            "Historical regulatory evolution tracking via Wayback Machine. "
            "Limited historical data available, current regulatory state is the primary basis for analysis."
        )

        return (lightest.jurisdiction, evolution)

    # ─────────────────────────────────────────────────────────────────────
    # Jurisdiction resolution
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_jurisdictions(self) -> list[str]:
        """Derive the jurisdictions to analyse from the engagement itself.

        Resolution order, most authoritative first:

          1. ``context["jurisdictions"]``, set by the orchestrator's
             engagement classification, or handed over by the Market Analyst.
          2. ``context["jurisdiction"]`` / ``context["geography"]`` /
             ``context["region"]`` / ``context["country"]``, singular forms.
          3. Geographies explicitly named in the user's question.
          4. Geographies named in the engagement focus (question + subject).

        Returns ``[]`` when the engagement names no geography.

        WHY THIS METHOD EXISTS: this used to be
        ``self._context.get("jurisdictions", ["US", "EU"])``. That hardcoded
        default meant a question about INDIA was analysed under the US Buy
        American Act, the Trade Agreements Act and the Berry Amendment,
        yielding 119 confidently-worded findings about the wrong country.
        A wrong jurisdiction is strictly worse than a missing one, because it
        reads as authoritative. Returning [] makes the downstream analysis
        jurisdiction-agnostic, honest rather than confidently wrong.
        """
        from hyperion.tools.query_utils import detect_geographies, get_engagement_focus

        def _clean(values: Any) -> list[str]:
            """Coerce arbitrary context values into short jurisdiction labels."""
            if not values:
                return []
            if isinstance(values, str):
                values = re.split(r"[,;/|]| and ", values)
            if not isinstance(values, (list, tuple, set)):
                return []
            out: list[str] = []
            for v in values:
                if not isinstance(v, str):
                    continue
                v = v.strip().strip(".").strip()
                # Handover payloads can be whole sentences ("Target markets
                # include India and the UAE."). Those are not labels, mine
                # them for geographies instead of using them verbatim, or a
                # 200-char sentence ends up interpolated into a search query.
                if not v or len(v) > 40 or v.count(" ") > 3:
                    for g in detect_geographies(v):
                        if g not in out:
                            out.append(g)
                    continue
                if v.lower() in {"none", "null", "n/a", "unknown", "not specified"}:
                    continue
                # Canonicalise so "indian"/"Bharat" and "India" don't both appear.
                canonical = detect_geographies(v)
                pick = canonical[0] if canonical else v
                if pick not in out:
                    out.append(pick)
            return out

        # 1 + 2: explicit context keys, in descending authority.
        for key in ("jurisdictions", "jurisdiction", "geography", "geographies",
                    "region", "regions", "country", "countries", "markets"):
            resolved = _clean(self._context.get(key))
            if resolved:
                self._log(
                    f"REGULATORY: jurisdictions from context['{key}']: {resolved}"
                )
                return resolved[:4]

        # 3: mine the user's own question.
        resolved = detect_geographies(self._question or "")
        if resolved:
            self._log(f"REGULATORY: jurisdictions derived from question: {resolved}")
            return resolved

        # 4: fall back to the engagement-wide focus anchor.
        question, subject, geography = get_engagement_focus()
        resolved = detect_geographies(geography, subject, question)
        if resolved:
            self._log(f"REGULATORY: jurisdictions from engagement focus: {resolved}")
            return resolved

        # No geography anywhere. Degrade honestly, do NOT invent one.
        self._log(
            "REGULATORY: no jurisdiction named in the engagement; running "
            "jurisdiction-agnostic regulatory analysis (no hardcoded default)"
        )
        return []

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel regulatory research
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_regulatory_sub_agents(
        self,
        jurisdictions: list[str],
        industry: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel regulatory research.

        Per §4.4, Agent 9:
        - Sub-agent A: Find regulations for [jurisdiction1] (MICRO, SearxNG + Jina)
        - Sub-agent B: Find regulations for [jurisdiction2] (MICRO, SearxNG + Jina)
        - Sub-agent C: Find pending/proposed regulations (FAST, SearxNG + Obscura)
        """
        sub_specs: list[SubAgentSpec] = []

        # Sub-agent A: jurisdiction1 regulations
        if len(jurisdictions) >= 1:
            sub_specs.append(SubAgentSpec(
                question=f"Find all applicable regulations for {jurisdictions[0]} in the {industry} industry. Include data protection, financial, industry-specific, labor, and environmental regulations. Extract key requirements and penalties.",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"jurisdiction": jurisdictions[0], "industry": industry},
            ))

        # Sub-agent B: jurisdiction2 regulations
        if len(jurisdictions) >= 2:
            sub_specs.append(SubAgentSpec(
                question=f"Find all applicable regulations for {jurisdictions[1]} in the {industry} industry. Include data protection, financial, industry-specific, labor, and environmental regulations. Extract key requirements and penalties.",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"jurisdiction": jurisdictions[1], "industry": industry},
            ))

        # Sub-agent C: pending/proposed regulations
        sub_specs.append(SubAgentSpec(
            question=f"Find pending and proposed regulations for the {industry} industry across all jurisdictions. Check government portals, regulatory agency websites, and legislative trackers for upcoming rules.",
            parent_agent=self.name,
            model_tier=ModelTier.FAST,
            tools=[ToolName.SEARXNG, ToolName.OBSCURA],
            findings_model="KeyFinding",
            timeout_seconds=300,
            context={"industry": industry, "jurisdictions": jurisdictions},
        ))

        all_findings: list[KeyFinding] = []

        results = await asyncio.gather(
            *(self._spawn_sub_agent(spec) for spec in sub_specs[:3]),
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
        regulation_count: int,
        jurisdiction_count: int,
        checklist_count: int,
        horizon_count: int,
        precedent_count: int,
        sources_count: int,
        has_historical_data: bool,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 5+ regulations, 2+ jurisdictions, 5+ checklist items,
              3+ horizon items, 2+ precedents, 5+ sources, historical data
        MEDIUM: 3+ regulations, 1+ jurisdictions, 3+ checklist items
        LOW: <3 regulations, missing core analysis
        """
        if (regulation_count >= 5 and jurisdiction_count >= 2
                and checklist_count >= 5 and horizon_count >= 3
                and precedent_count >= 2 and sources_count >= 5
                and has_historical_data):
            return ConfidenceLevel.HIGH
        if regulation_count >= 3 and jurisdiction_count >= 1 and checklist_count >= 3:
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
    ) -> RegulatoryAnalysis:
        """Execute the Regulatory Analyst's 8-step methodology.

        Steps (§4.4, Agent 9):
        1. Search for applicable regulations (SearxNG + Jina)
        2. Scrape government portals for current rules (Obscura)
        3. Pull historical regulatory data (Wayback)
        4. Map regulations by jurisdiction
        5. Build compliance checklist
        6. Scan regulatory horizon
        7. Analyze enforcement precedents
        8. Produce RegulatoryAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus, specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting regulatory analysis: {self._question[:80]}",
        )

        # Extract context.
        #
        # CRITICAL: jurisdictions must be derived from the question, never
        # defaulted to a hardcoded region. The previous default of
        # ["US", "EU"] meant a question about INDIA was analysed under the
        # US Buy American Act / Trade Agreements Act / Berry Amendment, 
        # 119 confidently-reported findings about the wrong country. A wrong
        # jurisdiction is worse than a missing one: it looks authoritative.
        # Four-tier resolution (explicit context keys -> sector -> engagement
        # subject -> the user's own question) rather than the two bare
        # context keys this used to read. `industry` was frequently "" when
        # the Director's handover omitted it, and every downstream query
        # template (`_search_regulations`, `_scrape_government_portals`)
        # interpolated that emptiness into a subject-less search, this was
        # one of the three specialists Finding B-9 named as missing the
        # shared `resolve_subject` helper entirely.
        industry = resolve_subject(
            self._context, "industry", "sector", question=self._question
        )
        jurisdictions = self._resolve_jurisdictions()

        # Spawn sub-agents for parallel regulatory research
        if jurisdictions and industry:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning regulatory research "
                "sub-agents")
            sub_findings = await self._spawn_regulatory_sub_agents(jurisdictions, industry)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with "
                "analysis")

        # Step 1: Search for applicable regulations
        await self._transition(AgentState.WORKING, f"Step 1: Searching regulations for {industry} in {jurisdictions}")
        self._search_results = await self._search_regulations(industry, jurisdictions)

        # Step 2: Scrape government portals
        await self._transition(AgentState.WORKING, "Step 2: Scraping government regulatory "
            "portals (Obscura)")
        self._government_data = await self._scrape_government_portals(jurisdictions)

        # Step 3: Pull historical regulatory data
        await self._transition(AgentState.WORKING, "Step 3: Pulling historical regulatory "
            "snapshots (Wayback)")
        self._historical_snapshots = await self._pull_historical_data(jurisdictions, industry)

        # Step 4: Map regulations by jurisdiction
        await self._transition(AgentState.WORKING, "Step 4: Mapping regulations by jurisdiction")
        regulations, jurisdiction_comparison = await self._map_regulations(
            self._question, self._search_results, self._government_data,
            jurisdictions, self._context,
        )

        # Step 5: Build compliance checklist
        await self._transition(AgentState.WORKING, "Step 5: Building structured compliance "
            "checklist")
        compliance_checklist = await self._build_compliance_checklist(
            self._question, regulations, self._context,
        )

        # Step 6: Scan regulatory horizon
        await self._transition(AgentState.WORKING, "Step 6: Scanning regulatory horizon (1-3 "
            "years)")
        horizon_scan = await self._scan_horizon(
            self._question, self._search_results, self._historical_snapshots,
            jurisdictions, self._context,
        )

        # Step 7: Analyze enforcement precedents
        await self._transition(AgentState.WORKING, "Step 7: Analyzing enforcement precedents")
        enforcement_precedents = await self._analyze_precedents(
            self._question, self._search_results, regulations, industry, self._context,
        )

        # Identify lightest jurisdiction and regulatory evolution
        lightest_jurisdiction, regulatory_evolution = (
            await self._identify_lightest_jurisdiction_and_evolution(
                jurisdiction_comparison, self._historical_snapshots, regulations,
            )
        )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            regulation_count=len(regulations),
            jurisdiction_count=len(jurisdiction_comparison),
            checklist_count=len(compliance_checklist),
            horizon_count=len(horizon_scan),
            precedent_count=len(enforcement_precedents),
            sources_count=len(self._sources),
            has_historical_data=bool(self._historical_snapshots),
        )

        # Step 8: Produce RegulatoryAnalysis model
        await self._transition(AgentState.WORKING, "Step 8: Producing RegulatoryAnalysis model")

        analysis = RegulatoryAnalysis(
            regulatory_map=regulations,
            jurisdiction_comparison=jurisdiction_comparison,
            compliance_checklist=compliance_checklist,
            horizon_scan=horizon_scan,
            enforcement_precedents=enforcement_precedents,
            lightest_jurisdiction=lightest_jurisdiction,
            regulatory_evolution=regulatory_evolution,
            legal_disclaimer=(
                "This is regulatory intelligence, not legal advice. "
                "Consult qualified legal counsel for definitive opinions."
            ),
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        # Publish lightest jurisdiction as a finding
        if lightest_jurisdiction:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="lightest_jurisdiction",
                title=f"Lightest Regulatory Jurisdiction: {lightest_jurisdiction}",
                content=(
                    f"{lightest_jurisdiction} has the lightest regulatory touch "
                    f"for this business. This is a potential strategic advantage, "
                    f"not for evasion, but for operational efficiency."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish high-risk regulations as findings
        high_risk_regs = [r for r in regulations if r.risk_level in ("high", "critical")]
        for reg in high_risk_regs[:3]:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="regulatory_risk",
                title=f"High-Risk Regulation: {reg.name} ({reg.jurisdiction})",
                content=(
                    f"{reg.name} ({reg.jurisdiction}): {reg.description[:200]}. "
                    f"Risk level: {reg.risk_level}. Penalty range: {reg.penalty_range}. "
                    f"Compliance cost: {reg.compliance_cost}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=reg.sources[:2],
            )
            await self._publish_finding(finding)

        # Publish horizon scan items as findings
        high_prob_horizon = [h for h in horizon_scan if h.probability == "high"]
        for item in high_prob_horizon[:2]:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="regulatory_horizon",
                title=f"Pending Regulation (High Probability): {item.regulation_name}",
                content=(
                    f"{item.regulation_name} ({item.jurisdiction}): Status {item.status}, "
                    f"timeline {item.timeline}. Impact: {item.potential_impact}. "
                    f"Recommended action: {item.recommended_action}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=item.sources[:2],
            )
            await self._publish_finding(finding)

        # Publish the full RegulatoryAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "regulatory_analysis": analysis.model_dump(),
                "regulation_count": len(regulations),
                "jurisdiction_count": len(jurisdiction_comparison),
                "checklist_count": len(compliance_checklist),
                "horizon_count": len(horizon_scan),
                "precedent_count": len(enforcement_precedents),
                "lightest_jurisdiction": lightest_jurisdiction,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Regulatory analysis complete: {len(regulations)} regulations across "
            f"{len(jurisdiction_comparison)} jurisdictions, "
            f"{len(compliance_checklist)} checklist items, "
            f"{len(horizon_scan)} horizon items, "
            f"{len(enforcement_precedents)} precedents, "
            f"lightest={lightest_jurisdiction or 'N/A'}, "
            f"confidence={confidence.value}",
        )

        return analysis
