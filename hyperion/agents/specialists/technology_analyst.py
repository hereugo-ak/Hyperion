"""
HYPERION Technology Analyst — Agent 7, the technology evaluation specialist.

This is NOT a generic "compare tech stacks" agent. This is a specialist
with 6 proprietary analytical frameworks:

- Architecture review: Evaluates a technology architecture against business
  requirements (scalability, reliability, maintainability, cost). Identifies
  anti-patterns and single points of failure.
- Tech debt assessment: Quantifies technical debt using the SIG/TÜViT model
  or similar. Categorizes debt as intentional vs. unintentional, estimates
  remediation cost.
- Vendor evaluation: Scores vendors across 7 dimensions — feature fit,
  pricing, scalability, support quality, ecosystem, lock-in risk, and
  roadmap alignment. Produces a vendor comparison matrix.
- Build-vs-buy framework: Structured analysis comparing build vs. buy on
  time to market, 5-year TCO, strategic differentiation, maintenance burden,
  team capability, and opportunity cost.
- TCO analysis: 5-year total cost of ownership including licensing,
  infrastructure, maintenance, integration, and switching costs.
- Platform assessment: Evaluates platform play vs. point solution. Assesses
  API quality, integration ecosystem, and extensibility.

It evaluates tech against business requirements, not engineering preferences.
It doesn't recommend Kubernetes because it's "modern" — it recommends the
simplest technology that meets the scalability/reliability requirements. It
always calculates 5-year TCO, not just licensing cost. It always assesses
lock-in risk — a vendor that's 20% cheaper but impossible to leave is more
expensive than one that's 20% pricier but easy to switch. (§4.4, Agent 7)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura
Sub-agents: Max 3 — vendor1 pricing/features, vendor2 pricing/features,
            developer reviews
Output: TechnologyAssessment (vendor matrix, build-vs-buy, TCO analysis,
        architecture review, platform assessment, lock-in risk, confidence,
        sources)

Methodology (§4.4, Agent 7):
1. Search for vendor/technology options (SearxNG + Jina)
2. Scrape vendor pricing and feature pages (Obscura)
3. Search for developer sentiment and reviews (SearxNG)
4. Build vendor comparison matrix
5. Run build-vs-buy analysis
6. Calculate 5-year TCO
7. Assess architecture if applicable
8. Produce TechnologyAssessment model
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
    ArchitectureReview,
    BuildVsBuyAnalysis,
    ConfidenceLevel,
    KeyFinding,
    Source,
    SourceCredibility,
    TCOAnalysis,
    TechnologyAssessment,
    VendorComparison,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


TECHNOLOGY_ANALYST_SPEC = AgentSpec(
    name=AgentName.TECHNOLOGY_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Technology Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.SEMANTIC_SCHOLAR,
        ToolName.HACKERNEWS,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Architecture review",
            description=(
                "Evaluate a technology architecture against business requirements "
                "(scalability, reliability, maintainability, cost). Identify "
                "architectural anti-patterns (e.g., distributed monolith, shared "
                "database, chatty services) and single points of failure. The review "
                "is against BUSINESS requirements, not engineering preferences — "
                "if the business needs 99.9% uptime, the architecture must deliver "
                "that, not 'we can add redundancy later.'"
            ),
            inputs=["architecture_description", "business_requirements", "scale_requirements"],
            outputs=["scalability_assessment", "reliability_assessment", "anti_patterns", "spofs", "recommendations"],
        ),
        SkillSpec(
            name="Tech debt assessment",
            description=(
                "Quantify technical debt using the SIG/TÜViT model or similar. "
                "Categorize debt as intentional (deliberate trade-off) vs. "
                "unintentional (negligence). Estimate remediation cost in engineer-"
                "months and dollars. Prioritize debt by business impact — debt in "
                "the payment system is higher priority than debt in the admin panel."
            ),
            inputs=["codebase_description", "architecture", "team_size", "business_priorities"],
            outputs=["debt_categories", "remediation_cost", "priority_ranking", "intentional_vs_unintentional"],
        ),
        SkillSpec(
            name="Vendor evaluation",
            description=(
                "Score vendors across 7 dimensions: feature fit, pricing, "
                "scalability, support quality, ecosystem, lock-in risk, and "
                "roadmap alignment. Each dimension scored 1-5. Produce a vendor "
                "comparison matrix with weighted overall scores. The weights are "
                "business-specific — a startup might weight pricing 3x while an "
                "enterprise might weight support quality 3x."
            ),
            inputs=["vendor_list", "business_requirements", "weight_priorities"],
            outputs=["vendor_matrix", "dimension_scores", "weighted_rankings", "recommendation"],
        ),
        SkillSpec(
            name="Build-vs-buy framework",
            description=(
                "Structured analysis comparing build vs. buy on: time to market, "
                "total cost of ownership (5-year), strategic differentiation, "
                "maintenance burden, team capability, and opportunity cost. The "
                "recommendation is not 'build is better' or 'buy is better' — it's "
                "the one that best fits the business context. If the feature is "
                "strategic differentiation, build. If it's table stakes, buy."
            ),
            inputs=["feature_description", "team_capability", "timeline", "budget", "strategic_importance"],
            outputs=["recommendation", "tco_comparison", "time_comparison", "strategic_assessment", "opportunity_cost"],
        ),
        SkillSpec(
            name="TCO analysis",
            description=(
                "5-year total cost of ownership including licensing, infrastructure, "
                "maintenance, integration, and switching costs. Not just licensing "
                "cost — the full picture. A vendor that's 20% cheaper but impossible "
                "to leave is more expensive than one that's 20% pricier but easy to "
                "switch. Include switching cost as a hidden tax on the decision."
            ),
            inputs=["vendor_pricing", "infrastructure_needs", "team_size", "integration_complexity"],
            outputs=["year_by_year_costs", "total_5yr", "cost_breakdown", "switching_cost", "cost_drivers"],
        ),
        SkillSpec(
            name="Platform assessment",
            description=(
                "Evaluate platform play vs. point solution. Assess API quality, "
                "integration ecosystem, and extensibility. A platform is more "
                "expensive upfront but enables future use cases. A point solution "
                "is cheaper and faster but creates silos. The assessment depends on "
                "the business roadmap — if multiple use cases are planned, platform. "
                "If one use case, point solution."
            ),
            inputs=["vendor_platform", "use_case_count", "roadmap", "integration_requirements"],
            outputs=["platform_vs_point", "api_quality", "ecosystem_assessment", "extensibility_score"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Technology Analyst — the specialist who evaluates "
        "technology stacks, assesses build-vs-buy decisions, maps digital "
        "transformation paths, and evaluates vendor platforms.\n\n"
        "Your proprietary frameworks:\n"
        "1. Architecture review: Evaluate against BUSINESS requirements (scalability, "
        "reliability, maintainability, cost). Identify anti-patterns and SPOFs.\n"
        "2. Tech debt assessment: SIG/TÜViT model. Intentional vs. unintentional. "
        "Estimate remediation cost. Prioritize by business impact.\n"
        "3. Vendor evaluation: 7 dimensions (feature fit, pricing, scalability, "
        "support, ecosystem, lock-in risk, roadmap). Weighted scores.\n"
        "4. Build-vs-buy: Time to market, 5-year TCO, strategic differentiation, "
        "maintenance burden, team capability, opportunity cost. Strategic = build, "
        "table stakes = buy.\n"
        "5. TCO analysis: 5-year total cost — licensing + infrastructure + "
        "maintenance + integration + switching costs. Switching cost is a hidden tax.\n"
        "6. Platform assessment: Platform vs. point solution. API quality, ecosystem, "
        "extensibility. Multiple use cases → platform. One use case → point solution.\n\n"
        "Rules:\n"
        "- EVALUATE TECH AGAINST BUSINESS REQUIREMENTS, NOT ENGINEERING PREFERENCES. "
        "Don't recommend Kubernetes because it's 'modern' — recommend the simplest "
        "technology that meets the scalability/reliability requirements.\n"
        "- ALWAYS calculate 5-year TCO, not just licensing cost.\n"
        "- ALWAYS assess lock-in risk. A vendor that's 20% cheaper but impossible "
        "to leave is more expensive than one that's 20% pricier but easy to switch.\n"
        "- Vendor scores must be 1-5 per dimension with justification.\n"
        "- Build-vs-buy must consider opportunity cost — what are we NOT doing if "
        "we build this?\n"
        "- Architecture review must identify SPECIFIC anti-patterns, not generic "
        "'could be better.' Name the pattern (distributed monolith, shared DB, etc.).\n"
        "- TCO must include switching cost — the cost to leave the vendor.\n\n"
        "You can spawn up to 3 sub-agents for parallel vendor data collection:\n"
        "- Sub-agent A: Scrape [vendor1] pricing and features (MICRO, Obscura)\n"
        "- Sub-agent B: Scrape [vendor2] pricing and features (MICRO, Obscura)\n"
        "- Sub-agent C: Find developer reviews for [technology] (FAST, SearxNG + Jina)\n\n"
        "Your output is a TechnologyAssessment Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned when the question involves technology selection, "
                     "build-vs-buy decisions, vendor evaluation, digital "
                     "transformation, or architecture review (TECHNOLOGY_SELECTION, "
                     "BUILD_VS_BUY, DIGITAL_TRANSFORMATION types)",
    max_sub_agents=3,
    output_model="TechnologyAssessment",
)


# ─────────────────────────────────────────────────────────────────────────────
# Technology Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class TechnologyAnalyst(BaseAgent):
    """Agent 7: The technology evaluation specialist.

    Evaluates tech stacks, assesses build-vs-buy, maps digital transformation
    paths, and evaluates vendor platforms. Scores vendors across 7 dimensions,
    calculates 5-year TCO, assesses lock-in risk, and reviews architectures
    against business requirements. Always recommends the simplest technology
    that meets requirements — not the trendiest. (§4.4, Agent 7)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for vendor/technology options (SearxNG + Jina)
    3. Scrapes vendor pricing and feature pages (Obscura)
    4. Searches for developer sentiment and reviews (SearxNG)
    5. Builds vendor comparison matrix, runs build-vs-buy, calculates TCO
    6. Assesses architecture if applicable
    7. Produces TechnologyAssessment model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or TECHNOLOGY_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._vendor_pricing: dict[str, dict[str, Any]] = {}
        self._developer_reviews: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Technology Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting build-vs-buy recommendation for strategy framing)
        - FINDINGS: receives findings from other agents that may inform
          technology evaluation (e.g., Operations Analyst's process
          requirements, Financial Analyst's budget constraints)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "technology_assessment":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Operations Analyst's process requirements inform tech needs
                if finding.finding_type == "process_requirements":
                    self._context.setdefault("process_requirements", []).append(finding.content)
                # Financial Analyst's budget constraints inform vendor selection
                elif finding.finding_type == "budget_constraint":
                    self._context["budget"] = finding.content

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "build_vs_buy_recommendation":
                # Strategy Analyst requesting build-vs-buy for strategy framing
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for vendor/technology options (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_vendor_options(self, technology_category: str, use_case: str) -> list[dict[str, Any]]:
        """Search for vendor/technology options in the category.

        Uses SearxNG to find: tech stack reviews, vendor comparisons,
        engineering blog posts, architecture case studies. Uses Jina to
        extract documentation, API specs, pricing pages, and technical
        whitepapers.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"best {technology_category} for {use_case}",
                f"{technology_category} vendor comparison 2024 2025",
                f"{technology_category} alternatives pricing",
                f"{technology_category} architecture case study",
                f"{technology_category} API documentation review",
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
    # Step 2: Scrape vendor pricing and feature pages (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_vendor_pricing(self, vendors: list[str]) -> dict[str, dict[str, Any]]:
        """Scrape JS-rendered vendor sites for pricing and features.

        Uses Obscura to scrape AWS, GCP, Azure pricing calculators, vendor
        pricing pages, and feature comparison pages. These are often JS-
        rendered and require a real browser to access.
        """
        vendor_data: dict[str, dict[str, Any]] = {}

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            for vendor in vendors:
                # Common vendor pricing URLs
                vendor_safe = (vendor or "").lower().replace(' ', '')
                pricing_urls = [
                    f"https://www.{vendor_safe}.com/pricing",
                    f"https://aws.amazon.com/marketplace/seller-profile?id={vendor}",
                    f"https://www.{vendor_safe}.com/features",
                ]

                for url in pricing_urls:
                    try:
                        fetch_result = await obscura.fetch(url, stealth=True)
                        if fetch_result and (fetch_result.markdown or fetch_result.content):
                            page_data = {"content": (fetch_result.markdown or fetch_result.content)[:15000]}
                        else:
                            page_data = None
                        if page_data:
                            vendor_data.setdefault(vendor, {})[url] = page_data
                            self._sources.append(Source(
                                id=f"src_{len(self._sources):03d}",
                                title=f"{vendor} pricing page",
                                url=url,
                                credibility=SourceCredibility.VENDOR,
                                key_data=f"Pricing and features for {vendor}",
                            ))
                            break  # Got data for this vendor, move to next
                    except (ValueError, AttributeError, RuntimeError):
                        continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return vendor_data

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Search for developer sentiment and reviews (SearxNG)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_developer_reviews(self, technology: str) -> list[dict[str, Any]]:
        """Search for developer sentiment, reviews, and community feedback.

        Uses SearxNG to find: Stack Overflow discussions, GitHub issues,
        Reddit threads, Hacker News discussions, engineering blog posts
        about the technology. Developer sentiment is a leading indicator
        of long-term viability — if developers hate a tool, it'll be
        hard to hire and retain.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{technology} developer review reddit",
                f"{technology} pros and cons stack overflow",
                f"{technology} complaints issues github",
                f"{technology} hacker news discussion",
                f"why I left {technology} engineering blog",
                f"{technology} vs alternatives developer experience",
            ]

            for pattern in query_patterns:
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
                        credibility=SourceCredibility.NEWS,
                    ))

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Build vendor comparison matrix
    # ─────────────────────────────────────────────────────────────────────

    async def _build_vendor_matrix(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        vendor_pricing: dict[str, dict[str, Any]],
        developer_reviews: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[VendorComparison]:
        """Build vendor comparison matrix scoring vendors across 7 dimensions.

        Dimensions: feature fit, pricing, scalability, support quality,
        ecosystem, lock-in risk, roadmap alignment. Each scored 1-5.
        Overall score is a weighted average based on business priorities.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:12]
        )
        pricing_summary = json.dumps(
            {k: str(v)[:500] for k, v in vendor_pricing.items()},
            default=str,
        )[:1500] if vendor_pricing else "No pricing data scraped"
        review_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in developer_reviews[:10]
        )

        prompt = (
            "You are the HYPERION Technology Analyst building a vendor comparison matrix.\n\n"
            f"Question: {question}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Vendor pricing data:\n{pricing_summary}\n\n"
            f"Developer reviews:\n{review_summary}\n\n"
            "Build a vendor comparison matrix scoring each vendor across 7 dimensions:\n"
            "1. feature_fit (1-5): How well do features match the requirements?\n"
            "2. pricing (1-5): How competitive is pricing? (1=expensive, 5=affordable)\n"
            "3. scalability (1-5): Can it scale with the business?\n"
            "4. support_quality (1-5): Support, documentation, community?\n"
            "5. ecosystem (1-5): Integration ecosystem, community size?\n"
            "6. lock_in_risk (1-5): How hard is it to switch? (1=high lock-in, 5=easy to switch)\n"
            "7. roadmap_alignment (1-5): Does the roadmap align with business needs?\n\n"
            "Calculate overall_score as a weighted average. Default weights: "
            "feature_fit=0.25, pricing=0.15, scalability=0.15, support=0.10, "
            "ecosystem=0.10, lock_in=0.15, roadmap=0.10.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "vendors": [{\n'
            '    "vendor_name": "...",\n'
            '    "feature_fit": 1-5,\n'
            '    "pricing": 1-5,\n'
            '    "scalability": 1-5,\n'
            '    "support_quality": 1-5,\n'
            '    "ecosystem": 1-5,\n'
            '    "lock_in_risk": 1-5,\n'
            '    "roadmap_alignment": 1-5,\n'
            '    "overall_score": number,\n'
            '    "notes": "qualitative assessment",\n'
            '    "pricing_details": "pricing tier info"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        vendors: list[VendorComparison] = []

        if not response.success or not response.content:
            return vendors

        try:
            data = json.loads(response.content)
            vendor_list = data.get("vendors", [])

            for v in vendor_list:
                def clamp(val: Any, default: int = 3) -> int:
                    try:
                        return max(1, min(5, int(val)))
                    except (ValueError, TypeError):
                        return default

                vendors.append(VendorComparison(
                    vendor_name=v.get("vendor_name", "Unknown"),
                    feature_fit=clamp(v.get("feature_fit")),
                    pricing=clamp(v.get("pricing")),
                    scalability=clamp(v.get("scalability")),
                    support_quality=clamp(v.get("support_quality")),
                    ecosystem=clamp(v.get("ecosystem")),
                    lock_in_risk=clamp(v.get("lock_in_risk")),
                    roadmap_alignment=clamp(v.get("roadmap_alignment")),
                    overall_score=float(v.get("overall_score", 0)),
                    notes=v.get("notes", ""),
                    pricing_details=v.get("pricing_details"),
                    sources=self._sources[:3],
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return vendors

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Run build-vs-buy analysis
    # ─────────────────────────────────────────────────────────────────────

    async def _run_build_vs_buy(
        self,
        question: str,
        vendor_matrix: list[VendorComparison],
        context: dict[str, Any],
    ) -> BuildVsBuyAnalysis:
        """Run structured build-vs-buy analysis.

        Compares build vs. buy on: time to market, 5-year TCO, strategic
        differentiation, maintenance burden, team capability, and opportunity
        cost. The recommendation is not 'build is better' or 'buy is better'
        — it's the one that best fits the business context.
        """
        vendor_summary = "\n".join(
            f"- {v.vendor_name}: Score {v.overall_score}, Pricing {v.pricing}/5, "
            f"Lock-in {v.lock_in_risk}/5"
            for v in vendor_matrix
        )

        prompt = (
            "You are the HYPERION Technology Analyst running a build-vs-buy analysis.\n\n"
            f"Question: {question}\n\n"
            f"Vendor options:\n{vendor_summary or 'No vendors identified'}\n\n"
            f"Business context:\n{json.dumps(context, default=str)[:1000]}\n\n"
            "Compare BUILD vs. BUY on:\n"
            "1. Time to market: How long if building vs. buying?\n"
            "2. 5-year TCO: Total cost of ownership over 5 years ($)\n"
            "3. Strategic differentiation: Does this feature create competitive advantage?\n"
            "   - If YES → lean toward BUILD (strategic features should be owned)\n"
            "   - If NO → lean toward BUY (table stakes should be bought)\n"
            "4. Maintenance burden: Who maintains it? What's the ongoing cost?\n"
            "5. Team capability: Can the team build and maintain this?\n"
            "6. Opportunity cost: What are we NOT doing if we build this?\n\n"
            "Recommendation: BUILD, BUY, or HYBRID (buy core, build differentiator).\n\n"
            "Return JSON:\n"
            "{\n"
            '  "recommendation": "BUILD|BUY|HYBRID",\n'
            '  "time_to_market_build": "...",\n'
            '  "time_to_market_buy": "...",\n'
            '  "tco_5yr_build": number,\n'
            '  "tco_5yr_buy": number,\n'
            '  "strategic_differentiation_build": "...",\n'
            '  "strategic_differentiation_buy": "...",\n'
            '  "maintenance_burden_build": "...",\n'
            '  "maintenance_burden_buy": "...",\n'
            '  "team_capability_assessment": "...",\n'
            '  "opportunity_cost": "...",\n'
            '  "rationale": "specific to the business context"\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return BuildVsBuyAnalysis(
                recommendation="BUY",
                time_to_market_build="Unknown",
                time_to_market_buy="Unknown",
                tco_5yr_build=0,
                tco_5yr_buy=0,
                strategic_differentiation_build="Unknown",
                strategic_differentiation_buy="Unknown",
                maintenance_burden_build="Unknown",
                maintenance_burden_buy="Unknown",
                team_capability_assessment="Unknown",
                opportunity_cost="Unknown",
                rationale="Build-vs-buy analysis failed — insufficient data",
            )

        try:
            data = json.loads(response.content)
            return BuildVsBuyAnalysis(
                recommendation=data.get("recommendation", "BUY"),
                time_to_market_build=data.get("time_to_market_build", "Unknown"),
                time_to_market_buy=data.get("time_to_market_buy", "Unknown"),
                tco_5yr_build=float(data.get("tco_5yr_build", 0)),
                tco_5yr_buy=float(data.get("tco_5yr_buy", 0)),
                strategic_differentiation_build=data.get("strategic_differentiation_build", ""),
                strategic_differentiation_buy=data.get("strategic_differentiation_buy", ""),
                maintenance_burden_build=data.get("maintenance_burden_build", ""),
                maintenance_burden_buy=data.get("maintenance_burden_buy", ""),
                team_capability_assessment=data.get("team_capability_assessment", ""),
                opportunity_cost=data.get("opportunity_cost", ""),
                rationale=data.get("rationale", ""),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return BuildVsBuyAnalysis(
                recommendation="BUY",
                time_to_market_build="Parse error",
                time_to_market_buy="Parse error",
                tco_5yr_build=0,
                tco_5yr_buy=0,
                strategic_differentiation_build="Parse error",
                strategic_differentiation_buy="Parse error",
                maintenance_burden_build="Parse error",
                maintenance_burden_buy="Parse error",
                team_capability_assessment="Parse error",
                opportunity_cost="Parse error",
                rationale="Build-vs-buy analysis failed — parsing error",
            )

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Calculate 5-year TCO
    # ─────────────────────────────────────────────────────────────────────

    async def _calculate_tco(
        self,
        question: str,
        vendor_matrix: list[VendorComparison],
        build_vs_buy: BuildVsBuyAnalysis,
        context: dict[str, Any],
    ) -> list[TCOAnalysis]:
        """Calculate 5-year TCO for each option.

        Includes licensing, infrastructure, maintenance, integration, and
        switching costs. Not just licensing cost — the full picture.
        """
        vendor_summary = "\n".join(
            f"- {v.vendor_name}: Pricing {v.pricing}/5, {v.pricing_details or 'No pricing details'}"
            for v in vendor_matrix
        )

        prompt = (
            "You are the HYPERION Technology Analyst calculating 5-year TCO.\n\n"
            f"Question: {question}\n\n"
            f"Vendor options:\n{vendor_summary or 'No vendors identified'}\n\n"
            f"Build-vs-buy recommendation: {build_vs_buy.recommendation}\n"
            f"Build 5yr TCO: ${build_vs_buy.tco_5yr_build}\n"
            f"Buy 5yr TCO: ${build_vs_buy.tco_5yr_buy}\n\n"
            "Calculate 5-year TCO for each viable option. Include:\n"
            "1. Licensing costs (per-year and total)\n"
            "2. Infrastructure costs (hosting, compute, storage)\n"
            "3. Maintenance costs (updates, patches, upgrades)\n"
            "4. Integration costs (connecting to existing systems)\n"
            "5. Switching costs (cost to leave this option — the hidden tax)\n\n"
            "Return JSON:\n"
            "{\n"
            '  "tco_options": [{\n'
            '    "option_name": "...",\n'
            '    "year_1": number,\n'
            '    "year_2": number,\n'
            '    "year_3": number,\n'
            '    "year_4": number,\n'
            '    "year_5": number,\n'
            '    "total_5yr": number,\n'
            '    "licensing": number,\n'
            '    "infrastructure": number,\n'
            '    "maintenance": number,\n'
            '    "integration": number,\n'
            '    "switching_cost": number,\n'
            '    "notes": "key cost drivers"\n'
            '  }]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        tco_results: list[TCOAnalysis] = []

        if not response.success or not response.content:
            return tco_results

        try:
            data = json.loads(response.content)
            options = data.get("tco_options", [])

            for opt in options:
                tco_results.append(TCOAnalysis(
                    option_name=opt.get("option_name", "Unknown"),
                    year_1=float(opt.get("year_1", 0)),
                    year_2=float(opt.get("year_2", 0)),
                    year_3=float(opt.get("year_3", 0)),
                    year_4=float(opt.get("year_4", 0)),
                    year_5=float(opt.get("year_5", 0)),
                    total_5yr=float(opt.get("total_5yr", 0)),
                    licensing=float(opt.get("licensing", 0)),
                    infrastructure=float(opt.get("infrastructure", 0)),
                    maintenance=float(opt.get("maintenance", 0)),
                    integration=float(opt.get("integration", 0)),
                    switching_cost=float(opt.get("switching_cost", 0)),
                    notes=opt.get("notes", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return tco_results

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Assess architecture if applicable
    # ─────────────────────────────────────────────────────────────────────

    async def _assess_architecture(
        self,
        question: str,
        context: dict[str, Any],
        search_results: list[dict[str, Any]],
    ) -> ArchitectureReview | None:
        """Assess technology architecture against business requirements.

        Evaluates scalability, reliability, maintainability, and cost.
        Identifies anti-patterns and single points of failure. Only
        runs if the question involves architecture review.
        """
        architecture_desc = context.get("architecture_description", "")
        if not architecture_desc:
            return None

        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )

        prompt = (
            "You are the HYPERION Technology Analyst conducting an architecture review.\n\n"
            f"Question: {question}\n\n"
            f"Architecture to review:\n{architecture_desc}\n\n"
            f"Relevant search results:\n{search_summary}\n\n"
            "Evaluate the architecture against BUSINESS requirements:\n"
            "1. Scalability: Can it scale to 10x current load? What breaks first?\n"
            "2. Reliability: Single points of failure? Redundancy? Failure modes?\n"
            "3. Maintainability: Code quality, documentation, team familiarity?\n"
            "4. Cost: Infrastructure cost at scale?\n"
            "5. Anti-patterns: Name SPECIFIC anti-patterns (distributed monolith, "
            "shared database, chatty services, etc.) — not generic 'could be better'\n"
            "6. Single points of failure: List each SPOF and its blast radius\n\n"
            "Return JSON:\n"
            "{\n"
            '  "architecture_description": "...",\n'
            '  "scalability_assessment": "...",\n'
            '  "reliability_assessment": "...",\n'
            '  "maintainability_assessment": "...",\n'
            '  "cost_assessment": "...",\n'
            '  "anti_patterns": ["pattern1", "pattern2"],\n'
            '  "single_points_of_failure": ["spof1", "spof2"],\n'
            '  "recommendations": ["rec1", "rec2"]\n'
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
            return ArchitectureReview(
                architecture_description=data.get("architecture_description", architecture_desc),
                scalability_assessment=data.get("scalability_assessment", "Not assessed"),
                reliability_assessment=data.get("reliability_assessment", "Not assessed"),
                maintainability_assessment=data.get("maintainability_assessment", "Not assessed"),
                cost_assessment=data.get("cost_assessment", "Not assessed"),
                anti_patterns=data.get("anti_patterns", []),
                single_points_of_failure=data.get("single_points_of_failure", []),
                recommendations=data.get("recommendations", []),
            )
        except (json.JSONDecodeError, ValueError, TypeError):
            return None

    # ─────────────────────────────────────────────────────────────────────
    # Platform assessment and lock-in risk summary
    # ─────────────────────────────────────────────────────────────────────

    async def _assess_platform_and_lockin(
        self,
        question: str,
        vendor_matrix: list[VendorComparison],
        context: dict[str, Any],
    ) -> tuple[str, str]:
        """Assess platform play vs. point solution and summarize lock-in risk.

        Returns (platform_assessment, lock_in_risk_summary).
        """
        vendor_summary = "\n".join(
            f"- {v.vendor_name}: Lock-in {v.lock_in_risk}/5, Ecosystem {v.ecosystem}/5"
            for v in vendor_matrix
        )

        prompt = (
            "You are the HYPERION Technology Analyst assessing platform vs. point solution "
            "and lock-in risk.\n\n"
            f"Question: {question}\n\n"
            f"Vendors:\n{vendor_summary or 'No vendors identified'}\n\n"
            "1. Platform assessment: Is the recommended option a platform (enables multiple "
            "use cases) or a point solution (solves one problem)? Which is better for this "
            "business context?\n"
            "2. Lock-in risk summary: Across all recommended vendors, what is the lock-in "
            "risk? A vendor that's 20% cheaper but impossible to leave is more expensive "
            "than one that's 20% pricier but easy to switch.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "platform_assessment": "...",\n'
            '  "lock_in_risk_summary": "..."\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return ("", "")

        try:
            data = json.loads(response.content)
            platform = data.get("platform_assessment", "")
            lock_in = data.get("lock_in_risk_summary", "")
            if not isinstance(platform, str):
                platform = json.dumps(platform) if isinstance(platform, dict) else str(platform)
            if not isinstance(lock_in, str):
                lock_in = json.dumps(lock_in) if isinstance(lock_in, dict) else str(lock_in)
            return (platform, lock_in)
        except (json.JSONDecodeError, ValueError):
            return ("", "")

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel vendor data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_vendor_sub_agents(
        self,
        vendors: list[str],
        technology: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel vendor data collection.

        Per §4.4, Agent 7:
        - Sub-agent A: Scrape [vendor1] pricing and features (MICRO, Obscura)
        - Sub-agent B: Scrape [vendor2] pricing and features (MICRO, Obscura)
        - Sub-agent C: Find developer reviews for [technology] (FAST, SearxNG + Jina)
        """
        sub_specs: list[SubAgentSpec] = []

        # Sub-agent A: vendor1 pricing
        if len(vendors) >= 1:
            sub_specs.append(SubAgentSpec(
                question=f"Scrape {vendors[0]} pricing page and feature list. Extract pricing tiers, feature matrix, and any limitations.",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"vendor": vendors[0]},
            ))

        # Sub-agent B: vendor2 pricing
        if len(vendors) >= 2:
            sub_specs.append(SubAgentSpec(
                question=f"Scrape {vendors[1]} pricing page and feature list. Extract pricing tiers, feature matrix, and any limitations.",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"vendor": vendors[1]},
            ))

        # Sub-agent C: developer reviews
        sub_specs.append(SubAgentSpec(
            question=f"Find developer reviews and sentiment for {technology}. Check Stack Overflow, Reddit, Hacker News, engineering blogs. What do developers say about it?",
            parent_agent=self.name,
            model_tier=ModelTier.FAST,
            tools=[ToolName.SEARXNG, ToolName.JINA],
            findings_model="KeyFinding",
            timeout_seconds=300,
            context={"technology": technology},
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
        vendor_count: int,
        sources_count: int,
        has_build_vs_buy: bool,
        has_tco: bool,
        has_architecture_review: bool,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 3+ vendors scored, 5+ sources, build-vs-buy done, TCO calculated,
              architecture reviewed (if applicable)
        MEDIUM: 2+ vendors, 3+ sources, build-vs-buy or TCO done
        LOW: <2 vendors, <3 sources, missing core analysis
        """
        if (vendor_count >= 3 and sources_count >= 5
                and has_build_vs_buy and has_tco):
            return ConfidenceLevel.HIGH
        if vendor_count >= 2 and sources_count >= 3 and (has_build_vs_buy or has_tco):
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
    ) -> TechnologyAssessment:
        """Execute the Technology Analyst's 8-step methodology.

        Steps (§4.4, Agent 7):
        1. Search for vendor/technology options (SearxNG + Jina)
        2. Scrape vendor pricing and feature pages (Obscura)
        3. Search for developer sentiment and reviews (SearxNG)
        4. Build vendor comparison matrix
        5. Run build-vs-buy analysis
        6. Calculate 5-year TCO
        7. Assess architecture if applicable
        8. Produce TechnologyAssessment model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus — specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting technology assessment: {self._question[:80]}",
        )

        # Extract context
        technology_category = self._context.get("technology_category", "")
        use_case = self._context.get("use_case", "")
        vendors = self._context.get("vendors", [])
        technology = self._context.get("technology", technology_category)

        # Spawn sub-agents for parallel vendor data collection
        if vendors or technology:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning vendor data collection sub-agents")
            sub_findings = await self._spawn_vendor_sub_agents(vendors, technology)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 1: Search for vendor/technology options
        await self._transition(AgentState.WORKING, f"Step 1: Searching for {technology_category} options (SearxNG + Jina)")
        self._search_results = await self._search_vendor_options(technology_category, use_case)

        # Step 2: Scrape vendor pricing and feature pages
        if vendors:
            await self._transition(AgentState.WORKING, f"Step 2: Scraping vendor pricing pages (Obscura) for {len(vendors)} vendors")
            self._vendor_pricing = await self._scrape_vendor_pricing(vendors)

        # Step 3: Search for developer sentiment and reviews
        if technology:
            await self._transition(AgentState.WORKING, f"Step 3: Searching developer reviews for {technology}")
            self._developer_reviews = await self._search_developer_reviews(technology)

        # Step 4: Build vendor comparison matrix
        await self._transition(AgentState.WORKING, "Step 4: Building vendor comparison matrix (7 dimensions)")
        vendor_matrix = await self._build_vendor_matrix(
            self._question, self._search_results, self._vendor_pricing,
            self._developer_reviews, self._context,
        )

        # Step 5: Run build-vs-buy analysis
        await self._transition(AgentState.WORKING, "Step 5: Running build-vs-buy analysis")
        build_vs_buy = await self._run_build_vs_buy(
            self._question, vendor_matrix, self._context,
        )

        # Step 6: Calculate 5-year TCO
        await self._transition(AgentState.WORKING, "Step 6: Calculating 5-year TCO for each option")
        tco_analysis = await self._calculate_tco(
            self._question, vendor_matrix, build_vs_buy, self._context,
        )

        # Step 7: Assess architecture if applicable
        architecture_review = None
        if self._context.get("architecture_description"):
            await self._transition(AgentState.WORKING, "Step 7: Assessing architecture against business requirements")
            architecture_review = await self._assess_architecture(
                self._question, self._context, self._search_results,
            )

        # Platform assessment and lock-in risk summary
        await self._transition(AgentState.WORKING, "Assessing platform vs. point solution and lock-in risk")
        platform_assessment, lock_in_summary = await self._assess_platform_and_lockin(
            self._question, vendor_matrix, self._context,
        )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            vendor_count=len(vendor_matrix),
            sources_count=len(self._sources),
            has_build_vs_buy=bool(build_vs_buy.recommendation),
            has_tco=bool(tco_analysis),
            has_architecture_review=architecture_review is not None,
        )

        # Step 8: Produce TechnologyAssessment model
        await self._transition(AgentState.WORKING, "Step 8: Producing TechnologyAssessment model")

        assessment = TechnologyAssessment(
            vendor_matrix=vendor_matrix,
            build_vs_buy=build_vs_buy,
            tco_analysis=tco_analysis,
            architecture_review=architecture_review,
            platform_assessment=platform_assessment,
            lock_in_risk_summary=lock_in_summary,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        # Publish build-vs-buy recommendation as a finding
        finding = KeyFinding(
            id=f"finding_{uuid.uuid4().hex[:8]}",
            agent=self.name.value,
            finding_type="build_vs_buy",
            title=f"Build-vs-Buy: {build_vs_buy.recommendation}",
            content=(
                f"Recommendation: {build_vs_buy.recommendation}. "
                f"Rationale: {build_vs_buy.rationale}. "
                f"5yr TCO: Build ${build_vs_buy.tco_5yr_build:,.0f} vs Buy ${build_vs_buy.tco_5yr_buy:,.0f}. "
                f"Opportunity cost: {build_vs_buy.opportunity_cost}."
            ),
            confidence=ConfidenceLevel.MEDIUM,
            sources=self._sources[:3],
        )
        await self._publish_finding(finding)

        # Publish top vendor as a finding
        if vendor_matrix:
            top_vendor = max(vendor_matrix, key=lambda v: v.overall_score)
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="vendor_recommendation",
                title=f"Top Vendor: {top_vendor.vendor_name} (Score {top_vendor.overall_score:.1f})",
                content=(
                    f"{top_vendor.vendor_name}: Overall score {top_vendor.overall_score:.1f}/5. "
                    f"Feature fit: {top_vendor.feature_fit}, Pricing: {top_vendor.pricing}, "
                    f"Lock-in risk: {top_vendor.lock_in_risk}/5. "
                    f"Notes: {top_vendor.notes}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=top_vendor.sources[:2],
            )
            await self._publish_finding(finding)

        # Publish architecture review as a finding if applicable
        if architecture_review and architecture_review.anti_patterns:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="architecture_review",
                title="Architecture Anti-Patterns Identified",
                content=(
                    f"Anti-patterns: {', '.join(architecture_review.anti_patterns)}. "
                    f"SPOFs: {', '.join(architecture_review.single_points_of_failure)}. "
                    f"Recommendations: {', '.join(architecture_review.recommendations[:3])}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish the full TechnologyAssessment as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "technology_assessment": assessment.model_dump(),
                "vendor_count": len(vendor_matrix),
                "build_vs_buy_recommendation": build_vs_buy.recommendation,
                "tco_options_count": len(tco_analysis),
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Technology assessment complete: {len(vendor_matrix)} vendors, "
            f"build-vs-buy={build_vs_buy.recommendation}, "
            f"{len(tco_analysis)} TCO options, confidence={confidence.value}",
        )

        return assessment
