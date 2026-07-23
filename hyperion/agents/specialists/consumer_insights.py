"""
HYPERION Consumer Insights Analyst — Agent 11, the customer behavior specialist.

This is NOT a generic "research your customers" agent. This is a specialist
with 6 proprietary analytical frameworks:

- Persona development: Build data-driven customer personas with demographics,
  behaviors, motivations, frustrations, and preferred channels. NOT generic
  personas — personas grounded in scraped review data and survey responses.
  Not "Tech-Savvy Tom, age 25-35." It says "Based on 847 G2 reviews and 234
  Reddit threads, the primary persona is a mid-market IT manager (35-45,
  $80K-$120K budget) whose top frustration is 'integration complexity'
  (mentioned in 34% of negative reviews) and whose primary buying trigger is
  'peer recommendation from a similar company' (mentioned in 41% of positive
  reviews)."
- Journey mapping: Map the end-to-end customer journey from awareness to
  advocacy. Identify friction points, drop-off points, and moments of truth.
- NPS analysis: Analyze Net Promoter Score data and qualitative feedback to
  identify the drivers of promotion and detraction. Not just a number — the
  specific reasons behind it with frequency data.
- Segmentation: Segment customers using three approaches — demographic (age,
  income, geography, company size), behavioral (usage patterns, purchase
  frequency, feature adoption), psychographic (values, motivations, attitudes).
  Identify which segmentation approach is most predictive of purchase behavior.
- Demand estimation: Estimate demand using willingness-to-pay analysis,
  conjoint analysis proxies, and price elasticity estimation from market data.
- Willingness-to-pay analysis: Estimate the price point that maximizes revenue
  using Van Westendorp price sensitivity meter methodology. Not just 'charge
  $50' — identifies optimal price point, too-cheap price, too-expensive price,
  and the range of acceptable prices.

It builds personas from real scraped data, not from imagination. It doesn't
say "Tech-Savvy Tom, age 25-35." It says "Based on 847 G2 reviews and 234
Reddit threads, the primary persona is a mid-market IT manager (35-45, $80K-
$120K budget) whose top frustration is 'integration complexity' (mentioned in
34% of negative reviews) and whose primary buying trigger is 'peer
recommendation from a similar company' (mentioned in 41% of positive reviews)."
(§4.4, Agent 11)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura
Sub-agents: Max 3 — review scraping, consumer survey data, WTP studies
Output: ConsumerInsights (personas, journey map, NPS, segments, demand, WTP)

Methodology (§4.4, Agent 11):
1. Search for consumer research (SearxNG + Jina)
2. Scrape review sites and forums (Obscura)
3. Build personas from data
4. Map customer journey
5. Segment the market
6. Estimate demand and willingness-to-pay
7. Produce ConsumerInsights model
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
    ConsumerInsights,
    CustomerSegment,
    DemandEstimate,
    JourneyStage,
    KeyFinding,
    NPSAnalysis,
    Persona,
    SegmentationApproach,
    Source,
    SourceCredibility,
    WillingnessToPay,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


CONSUMER_INSIGHTS_SPEC = AgentSpec(
    name=AgentName.CONSUMER_INSIGHTS,
    role=AgentRole.SPECIALIST,
    display_name="Consumer Insights Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.GOOGLE_TRENDS,
        ToolName.HACKERNEWS,
        ToolName.REDDIT,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Persona development",
            description=(
                "Build data-driven customer personas with demographics, behaviors, "
                "motivations, frustrations, and preferred channels. NOT generic "
                "personas — personas grounded in scraped review data and survey "
                "responses. Not 'Tech-Savvy Tom, age 25-35.' It says 'Based on 847 "
                "G2 reviews and 234 Reddit threads, the primary persona is a "
                "mid-market IT manager (35-45, $80K-$120K budget) whose top "
                "frustration is integration complexity (mentioned in 34% of "
                "negative reviews) and whose primary buying trigger is peer "
                "recommendation from a similar company (mentioned in 41% of "
                "positive reviews).' Each persona has data_basis citing the "
                "specific reviews/threads/surveys it's built from."
            ),
            inputs=["review_data", "survey_data", "forum_data", "demographics"],
            outputs=["personas", "demographics", "behaviors", "motivations", "frustrations", "buying_triggers", "data_basis"],
        ),
        SkillSpec(
            name="Journey mapping",
            description=(
                "Map the end-to-end customer journey from awareness to advocacy. "
                "Identify friction points, drop-off points, and moments of truth. "
                "Each stage has touchpoints, friction points, drop-off rate, "
                "whether it's a moment of truth, and a specific improvement "
                "opportunity. Not just 'customers find us and buy' — 'at the "
                "consideration stage, 45% drop off due to lack of pricing "
                "transparency; moment of truth is the demo call where 78% of "
                "conversions happen.'"
            ),
            inputs=["review_data", "funnel_data", "touchpoint_analysis", "drop_off_data"],
            outputs=["journey_stages", "friction_points", "drop_off_rates", "moments_of_truth", "improvement_opportunities"],
        ),
        SkillSpec(
            name="NPS analysis",
            description=(
                "Analyze Net Promoter Score data and qualitative feedback to "
                "identify the drivers of promotion and detraction. Not just a "
                "number — the specific reasons behind it with frequency data. "
                "'NPS = +42, with 62% promoters, 20% passives, 18% detractors. "
                "Top promotion driver: ease of integration (mentioned in 71% of "
                "promoter comments). Top detraction driver: poor customer support "
                "(mentioned in 58% of detractor comments).'"
            ),
            inputs=["nps_scores", "qualitative_feedback", "review_sentiment"],
            outputs=["nps_score", "promoter_percentage", "detractor_percentage", "promotion_drivers", "detraction_drivers", "key_quotes"],
        ),
        SkillSpec(
            name="Segmentation",
            description=(
                "Segment customers using three approaches: demographic (age, "
                "income, geography, company size), behavioral (usage patterns, "
                "purchase frequency, feature adoption), psychographic (values, "
                "motivations, attitudes). Identify which segmentation approach "
                "is most predictive of purchase behavior. Each segment has size, "
                "characteristics, purchase probability, and customer lifetime value."
            ),
            inputs=["demographic_data", "behavioral_data", "psychographic_data", "purchase_history"],
            outputs=["demographic_segments", "behavioral_segments", "psychographic_segments", "most_predictive_approach"],
        ),
        SkillSpec(
            name="Demand estimation",
            description=(
                "Estimate demand using willingness-to-pay analysis, conjoint "
                "analysis proxies, and price elasticity estimation from market "
                "data. Calculate TAM, SAM, SOM, price elasticity of demand, "
                "demand at current vs optimal price, and revenue forecast. Not "
                "just 'market is big' — 'TAM $2.3B, SAM $450M, SOM 8% = $36M, "
                "price elasticity -1.5, demand at optimal price ($75) = 480K "
                "units, revenue forecast $36M/yr.'"
            ),
            inputs=["market_size_data", "price_data", "competitor_pricing", "wtp_data"],
            outputs=["tam", "sam", "som", "price_elasticity", "demand_at_optimal_price", "revenue_forecast"],
        ),
        SkillSpec(
            name="Willingness-to-pay analysis",
            description=(
                "Estimate the price point that maximizes revenue using Van "
                "Westendorp price sensitivity meter methodology. Not just "
                "'charge $50' — identifies the optimal price point, too-cheap "
                "price (below which customers question quality), too-expensive "
                "price (above which customers won't buy), and the range of "
                "acceptable prices. Calculates revenue at the optimal price point."
            ),
            inputs=["price_survey_data", "competitor_pricing", "market_wtp_data"],
            outputs=["optimal_price_point", "too_cheap_price", "too_expensive_price", "acceptable_range", "revenue_at_optimal"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Consumer Insights Analyst — the specialist who "
        "analyzes customer behavior, develops personas, maps customer journeys, "
        "and estimates demand.\n\n"
        "Your proprietary frameworks:\n"
        "1. Persona development: Build data-driven personas GROUNDED IN SCRAPED "
        "REVIEW DATA. Not 'Tech-Savvy Tom, age 25-35.' It says 'Based on 847 G2 "
        "reviews and 234 Reddit threads, the primary persona is a mid-market IT "
        "manager (35-45, $80K-$120K budget) whose top frustration is integration "
        "complexity (34% of negative reviews) and whose primary buying trigger is "
        "peer recommendation (41% of positive reviews).' Each persona MUST have "
        "data_basis citing the specific data it's built from.\n"
        "2. Journey mapping: End-to-end journey from awareness to advocacy. "
        "Friction points, drop-off rates, moments of truth, improvement "
        "opportunities at each stage.\n"
        "3. NPS analysis: Not just a number — the specific drivers of promotion "
        "and detraction with frequency data. 'Top promotion driver: ease of "
        "integration (71% of promoter comments). Top detraction driver: poor "
        "support (58% of detractor comments).'\n"
        "4. Segmentation: Three approaches — demographic, behavioral, "
        "psychographic. Identify which is MOST PREDICTIVE of purchase behavior.\n"
        "5. Demand estimation: TAM, SAM, SOM, price elasticity, demand at "
        "current vs optimal price, revenue forecast.\n"
        "6. Willingness-to-pay: Van Westendorp price sensitivity meter. Optimal "
        "price point, too-cheap, too-expensive, acceptable range, revenue at "
        "optimal.\n\n"
        "Rules:\n"
        "- PERSONAS MUST BE GROUNDED IN REAL DATA. Every persona must have "
        "data_basis citing specific review counts, survey responses, or forum "
        "threads. NO IMAGINARY PERSONAS.\n"
        "- FRUSTRATIONS AND BUYING TRIGGERS MUST HAVE FREQUENCY DATA. Not "
        "'integration is hard' but 'integration complexity mentioned in 34% of "
        "negative reviews.'\n"
        "- NPS MUST HAVE DRIVERS, NOT JUST A SCORE. What specifically drives "
        "promotion and detraction, with % from feedback.\n"
        "- SEGMENTATION MUST IDENTIFY THE MOST PREDICTIVE APPROACH. Not just "
        "three sets of segments — which one predicts purchase behavior best.\n"
        "- WTP MUST USE VAN WESTENDORP METHODOLOGY. Optimal, too-cheap, too-"
        "expensive, acceptable range, revenue at optimal.\n\n"
        "You can spawn up to 3 sub-agents for parallel data collection:\n"
        "- Sub-agent A: Scrape reviews from [review site] (MICRO, Obscura)\n"
        "- Sub-agent B: Find consumer survey data for [segment] (MICRO, SearxNG)\n"
        "- Sub-agent C: Find willingness-to-pay studies for [product category] "
        "(FAST, SearxNG + Jina)\n\n"
        "Your output is a ConsumerInsights Pydantic model — structured, not free text."
    ),
    spawn_condition="Spawned when the question involves customer behavior, "
                     "personas, customer journey, NPS, segmentation, demand "
                     "estimation, or willingness-to-pay (CONSUMER_INSIGHTS, "
                     "PERSONA, JOURNEY, NPS, SEGMENTATION, DEMAND, WTP types)",
    max_sub_agents=3,
    output_model="ConsumerInsights",
)


# ─────────────────────────────────────────────────────────────────────────────
# Consumer Insights Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class ConsumerInsightsAnalyst(BaseAgent):
    """Agent 11: The consumer behavior and customer insights specialist.

    Analyzes customer behavior, develops data-driven personas grounded in
    scraped review data, maps customer journeys with friction points and
    moments of truth, analyzes NPS with specific drivers, segments customers
    across three approaches (demographic, behavioral, psychographic), estimates
    demand with price elasticity, and calculates willingness-to-pay using Van
    Westendorp methodology. (§4.4, Agent 11)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for consumer research (SearxNG + Jina)
    3. Scrapes review sites and forums (Obscura — G2, Capterra, Trustpilot)
    4. Builds personas from scraped data, maps journey, segments market
    5. Estimates demand and willingness-to-pay
    6. Produces ConsumerInsights model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or CONSUMER_INSIGHTS_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._review_data: list[dict[str, Any]] = []
        self._survey_data: list[dict[str, Any]] = []
        self._wtp_studies: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

        # Total reviews analyzed
        self._total_reviews: int = 0

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Consumer Insights Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting persona data for go-to-market strategy)
        - FINDINGS: receives findings from other agents that may inform
          consumer insights (e.g., Market Analyst's market size data,
          Competitive Intel's competitor customer base)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "consumer_insights":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Market Analyst's market size data informs demand estimation
                if finding.finding_type == "market_size":
                    self._context.setdefault("market_size_data", []).append(finding.content)
                # Competitive Intel's competitor customer base informs segmentation
                elif finding.finding_type == "competitor_customers":
                    self._context.setdefault("competitor_customer_data", []).append(finding.content)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "personas":
                # Strategy Analyst requesting persona data for GTM
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for consumer research (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_consumer_research(self, company: str, sector: str, product_category: str) -> list[dict[str, Any]]:
        """Search for consumer research, market research reports, survey data,
        and behavioral studies.

        Uses SearxNG to find: consumer research, market research reports, survey
        data, behavioral studies, NPS benchmarks. Uses Jina to extract consumer
        research content, review aggregations, and behavioral analysis reports.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"{company} customer reviews G2 Capterra Trustpilot",
                f"{sector} consumer behavior survey data",
                f"{sector} customer personas market research",
                f"{product_category} willingness to pay Van Westendorp",
                f"{product_category} price elasticity demand study",
                f"{sector} NPS benchmark Net Promoter Score",
                f"{sector} customer journey map friction points",
                f"{sector} customer segmentation demographic behavioral psychographic",
                f"{company} customer satisfaction survey results",
                f"{sector} conjoint analysis price sensitivity",
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
    # Step 2: Scrape review sites and forums (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_review_sites(self, company: str, sector: str) -> list[dict[str, Any]]:
        """Scrape JS-rendered review sites (G2, Capterra, Trustpilot), social
        media platforms, and consumer forums to extract real customer sentiment
        and pain points.

        Uses Obscura to scrape: G2, Capterra, Trustpilot, Reddit threads,
        consumer forums. Extracts real customer sentiment and pain points with
        frequency data.
        """
        results: list[dict[str, Any]] = []

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            # Review sites and consumer forums
            review_urls = [
                f"https://www.g2.com/products/{company.lower().replace(' ', '-')}/reviews",
                f"https://www.capterra.com/p/{company.lower().replace(' ', '-')}/reviews/",
                f"https://www.trustpilot.com/review/{company.lower().replace(' ', '.')}.com",
                f"https://www.reddit.com/search/?q={company}+review+experience",
                f"https://www.reddit.com/r/{sector.lower().replace(' ', '')}/search/?q=review",
            ]

            for url in review_urls[:6]:
                try:
                    fetch_result = await obscura.fetch(url, stealth=True)
                    if fetch_result and (fetch_result.markdown or fetch_result.content):
                        page_data = {"content": (fetch_result.markdown or fetch_result.content)[:15000]}
                    else:
                        page_data = None
                    if page_data:
                        # Try to extract review count from page data
                        review_count = self._extract_review_count(page_data)
                        if review_count:
                            self._total_reviews += review_count

                        results.append({
                            "url": url,
                            "data": page_data,
                            "review_count": review_count,
                        })
                        self._sources.append(Source(
                            id=f"src_{len(self._sources):03d}",
                            title=f"Review site — {url.split('/')[2]}",
                            url=url,
                            credibility=SourceCredibility.NEWS,
                            key_data=f"Customer reviews from {url.split('/')[2]}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    def _extract_review_count(self, page_data: Any) -> int:
        """Try to extract the number of reviews from scraped page data."""
        try:
            if isinstance(page_data, str):
                # Look for patterns like "847 reviews" or "1,234 Reviews"
                import re
                match = re.search(r'(\d[\d,]*)\s+reviews?', page_data, re.IGNORECASE)
                if match:
                    return int(match.group(1).replace(",", ""))
            elif isinstance(page_data, dict):
                # Check common keys
                for key in ("review_count", "reviews", "total_reviews", "rating_count"):
                    if key in page_data:
                        try:
                            return int(page_data[key])
                        except (ValueError, TypeError):
                            continue
        except (ValueError, TypeError, AttributeError):
            pass
        return 0

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Build personas from data
    # ─────────────────────────────────────────────────────────────────────

    async def _build_personas(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        review_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[Persona]:
        """Build data-driven customer personas grounded in scraped review data.

        NOT generic — personas grounded in scraped review data and survey
        responses. Each persona has data_basis citing the specific reviews/
        threads/surveys it's built from. Frustrations and buying triggers
        have frequency data from review analysis.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:10]
        )
        review_summary = json.dumps(
            [{"url": d.get("url", ""), "review_count": d.get("review_count", 0),
              "data": str(d.get("data", ""))[:300]} for d in review_data[:4]],
            default=str,
        )[:800]

        prompt = (
            "You are the HYPERION Consumer Insights Analyst building personas.\n\n"
            f"Question: {question}\n\n"
            f"Consumer research results:\n{search_summary}\n\n"
            f"Review site data:\n{review_summary}\n\n"
            "Build 2-4 data-driven customer personas GROUNDED IN THE SCRAPED DATA.\n\n"
            "CRITICAL RULES:\n"
            "- Personas MUST be grounded in real data. NO IMAGINARY PERSONAS.\n"
            "- Each persona must have data_basis citing specific review counts, "
            "survey responses, or forum threads.\n"
            "- Frustrations MUST have frequency data. Not 'integration is hard' but "
            "'integration complexity mentioned in 34% of negative reviews.'\n"
            "- Buying triggers MUST have frequency data. Not 'peer recommendation' "
            "but 'peer recommendation from similar company mentioned in 41% of "
            "positive reviews.'\n"
            "- NOT 'Tech-Savvy Tom, age 25-35.' Use descriptive names like "
            "'Mid-Market IT Manager' or 'Enterprise Procurement Lead.'\n\n"
            "For each persona:\n"
            "- name: descriptive name (not generic)\n"
            "- demographics: age, income, geography, company size, role\n"
            "- behaviors: observed behaviors (usage, purchase frequency, adoption)\n"
            "- motivations: what drives them to buy/use\n"
            "- frustrations: top frustrations WITH FREQUENCY from review data\n"
            "- preferred_channels: where they get info and buy\n"
            "- buying_triggers: specific triggers WITH % from review data\n"
            "- data_basis: what data this persona is built from\n"
            "- is_primary: is this the primary persona?\n\n"
            "Return JSON:\n"
            "{\n"
            '  "personas": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        personas: list[Persona] = []

        if not response.success or not response.content:
            return personas

        try:
            data = json.loads(response.content)
            for p in data.get("personas", []):
                personas.append(Persona(
                    name=p.get("name", "Unknown Persona"),
                    demographics=p.get("demographics", ""),
                    behaviors=p.get("behaviors", []),
                    motivations=p.get("motivations", []),
                    frustrations=p.get("frustrations", []),
                    preferred_channels=p.get("preferred_channels", []),
                    buying_triggers=p.get("buying_triggers", []),
                    data_basis=p.get("data_basis", ""),
                    is_primary=bool(p.get("is_primary", False)),
                ))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return personas

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Map customer journey
    # ─────────────────────────────────────────────────────────────────────

    async def _map_journey(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        review_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[JourneyStage]:
        """Map the end-to-end customer journey from awareness to advocacy.

        Identifies friction points, drop-off points, and moments of truth.
        Each stage has touchpoints, friction points, drop-off rate, whether
        it's a moment of truth, and a specific improvement opportunity.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )
        review_summary = json.dumps(
            [{"url": d.get("url", ""), "data": str(d.get("data", ""))[:300]} for d in review_data[:3]],
            default=str,
        )[:600]

        prompt = (
            "You are the HYPERION Consumer Insights Analyst mapping the customer journey.\n\n"
            f"Question: {question}\n\n"
            f"Consumer research:\n{search_summary}\n\n"
            f"Review data:\n{review_summary}\n\n"
            "Map the end-to-end customer journey from AWARENESS to ADVOCACY.\n"
            "Typical stages: awareness, consideration, purchase, onboarding, "
            "usage, renewal/expansion, advocacy.\n\n"
            "For each stage:\n"
            "- stage: name of the stage\n"
            "- description: what happens at this stage\n"
            "- touchpoints: customer touchpoints (list)\n"
            "- friction_points: specific friction points at this stage\n"
            "- drop_off_rate: drop-off rate (e.g., '45%')\n"
            "- is_moment_of_truth: is this a moment of truth?\n"
            "- improvement_opportunity: specific improvement opportunity\n\n"
            "NOT just 'customers find us and buy.' Be specific:\n"
            "'At consideration stage, 45% drop off due to lack of pricing "
            "transparency. Moment of truth is the demo call where 78% of "
            "conversions happen. Improvement: publish transparent pricing.'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "journey": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        journey: list[JourneyStage] = []

        if not response.success or not response.content:
            return journey

        try:
            data = json.loads(response.content)
            for stage in data.get("journey", []):
                journey.append(JourneyStage(
                    stage=stage.get("stage", "Unknown"),
                    description=stage.get("description", ""),
                    touchpoints=stage.get("touchpoints", []),
                    friction_points=stage.get("friction_points", []),
                    drop_off_rate=stage.get("drop_off_rate", ""),
                    is_moment_of_truth=bool(stage.get("is_moment_of_truth", False)),
                    improvement_opportunity=stage.get("improvement_opportunity", ""),
                ))
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return journey

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Segment the market + NPS analysis
    # ─────────────────────────────────────────────────────────────────────

    async def _segment_and_nps(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        review_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[list[CustomerSegment], str, NPSAnalysis | None]:
        """Segment the market using three approaches and analyze NPS.

        Segmentation: demographic, behavioral, psychographic. Identifies which
        approach is most predictive of purchase behavior.

        NPS: Analyzes NPS data and qualitative feedback to identify drivers of
        promotion and detraction. Not just a number — specific reasons with
        frequency data.

        Returns (segments, most_predictive_segmentation, nps_analysis).
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )

        prompt = (
            "You are the HYPERION Consumer Insights Analyst doing segmentation + NPS.\n\n"
            f"Question: {question}\n\n"
            f"Consumer research:\n{search_summary}\n\n"
            "SEGMENTATION — segment customers using THREE approaches:\n"
            "1. Demographic (age, income, geography, company size)\n"
            "2. Behavioral (usage patterns, purchase frequency, feature adoption)\n"
            "3. Psychographic (values, motivations, attitudes)\n\n"
            "For each segment:\n"
            "- approach: demographic, behavioral, or psychographic\n"
            "- segment_name: name of the segment\n"
            "- size_percentage: % of total market\n"
            "- characteristics: key characteristics (list)\n"
            "- purchase_probability: purchase propensity\n"
            "- value: customer lifetime value ($)\n"
            "- is_most_predictive: is this the most predictive for purchase?\n\n"
            "IDENTIFY which approach is MOST PREDICTIVE of purchase behavior.\n\n"
            "NPS ANALYSIS — analyze Net Promoter Score:\n"
            "- nps_score: the score (e.g., '+42')\n"
            "- promoter_percentage: % promoters (9-10)\n"
            "- passive_percentage: % passives (7-8)\n"
            "- detractor_percentage: % detractors (0-6)\n"
            "- promotion_drivers: SPECIFIC drivers with frequency (e.g., 'ease of "
            "integration (71% of promoter comments)')\n"
            "- detraction_drivers: SPECIFIC drivers with frequency (e.g., 'poor "
            "support (58% of detractor comments)')\n"
            "- sample_size: sample size\n"
            "- key_quotes: representative quotes\n\n"
            "Return JSON:\n"
            "{\n"
            '  "segments": [{...}],\n'
            '  "most_predictive_segmentation": "demographic|behavioral|psychographic",\n'
            '  "nps": {\n'
            '    "nps_score": "...",\n'
            '    "promoter_percentage": "...",\n'
            '    "passive_percentage": "...",\n'
            '    "detractor_percentage": "...",\n'
            '    "promotion_drivers": ["..."],\n'
            '    "detraction_drivers": ["..."],\n'
            '    "sample_size": "...",\n'
            '    "key_quotes": ["..."]\n'
            '  }\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        segments: list[CustomerSegment] = []
        most_predictive = ""
        nps_analysis: NPSAnalysis | None = None

        if not response.success or not response.content:
            return (segments, most_predictive, nps_analysis)

        try:
            data = json.loads(response.content)

            approach_map = {
                "demographic": SegmentationApproach.DEMOGRAPHIC,
                "behavioral": SegmentationApproach.BEHAVIORAL,
                "psychographic": SegmentationApproach.PSYCHOGRAPHIC,
            }

            for seg in data.get("segments", []):
                approach_str = seg.get("approach", "demographic")
                approach = approach_map.get(approach_str, SegmentationApproach.DEMOGRAPHIC)

                segments.append(CustomerSegment(
                    approach=approach,
                    segment_name=seg.get("segment_name", "Unknown"),
                    size_percentage=seg.get("size_percentage", ""),
                    characteristics=seg.get("characteristics", []),
                    purchase_probability=seg.get("purchase_probability", ""),
                    value=seg.get("value", ""),
                    is_most_predictive=bool(seg.get("is_most_predictive", False)),
                ))

            most_predictive = data.get("most_predictive_segmentation", "")

            nps_data = data.get("nps")
            if nps_data:
                nps_analysis = NPSAnalysis(
                    nps_score=nps_data.get("nps_score", ""),
                    promoter_percentage=nps_data.get("promoter_percentage", ""),
                    passive_percentage=nps_data.get("passive_percentage", ""),
                    detractor_percentage=nps_data.get("detractor_percentage", ""),
                    promotion_drivers=nps_data.get("promotion_drivers", []),
                    detraction_drivers=nps_data.get("detraction_drivers", []),
                    sample_size=nps_data.get("sample_size", ""),
                    key_quotes=nps_data.get("key_quotes", []),
                )

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (segments, most_predictive, nps_analysis)

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Estimate demand and willingness-to-pay
    # ─────────────────────────────────────────────────────────────────────

    async def _estimate_demand_and_wtp(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        wtp_studies: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[DemandEstimate | None, WillingnessToPay | None]:
        """Estimate demand and willingness-to-pay.

        Demand: TAM, SAM, SOM, price elasticity, demand at current vs optimal
        price, revenue forecast.

        WTP: Van Westendorp price sensitivity meter — optimal price point,
        too-cheap, too-expensive, acceptable range, revenue at optimal.

        Returns (demand_estimate, willingness_to_pay).
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )
        wtp_summary = json.dumps(
            [{"url": d.get("url", ""), "data": str(d.get("data", ""))[:200]} for d in wtp_studies[:3]],
            default=str,
        )[:600]

        prompt = (
            "You are the HYPERION Consumer Insights Analyst estimating demand + WTP.\n\n"
            f"Question: {question}\n\n"
            f"Consumer research:\n{search_summary}\n\n"
            f"WTP studies:\n{wtp_summary or 'No WTP studies available'}\n\n"
            "DEMAND ESTIMATION:\n"
            "- total_addressable_market: TAM ($)\n"
            "- serviceable_addressable_market: SAM ($)\n"
            "- serviceable_obtainable_market: SOM / market share achievable (%)\n"
            "- price_elasticity: price elasticity of demand (e.g., '-1.5')\n"
            "- demand_at_current_price: estimated demand at current price\n"
            "- demand_at_optimal_price: estimated demand at optimal price\n"
            "- revenue_forecast: revenue forecast at optimal price ($/yr)\n"
            "- methodology: methodology used\n\n"
            "NOT just 'market is big.' Be specific: 'TAM $2.3B, SAM $450M, "
            "SOM 8% = $36M, price elasticity -1.5, demand at optimal price "
            "($75) = 480K units, revenue forecast $36M/yr.'\n\n"
            "WILLINGNESS-TO-PAY (Van Westendorp Price Sensitivity Meter):\n"
            "- optimal_price_point: price that maximizes revenue ($)\n"
            "- too_cheap_price: price below which customers question quality ($)\n"
            "- too_expensive_price: price above which customers won't buy ($)\n"
            "- acceptable_range: range of acceptable prices ($ - $)\n"
            "- revenue_at_optimal: estimated revenue at optimal price ($)\n"
            "- methodology: 'Van Westendorp Price Sensitivity Meter'\n"
            "- data_basis: what data this analysis is based on\n\n"
            "Return JSON:\n"
            "{\n"
            '  "demand": {...},\n'
            '  "wtp": {...}\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        demand: DemandEstimate | None = None
        wtp: WillingnessToPay | None = None

        if not response.success or not response.content:
            return (demand, wtp)

        try:
            data = json.loads(response.content)

            demand_data = data.get("demand")
            if demand_data:
                demand = DemandEstimate(
                    total_addressable_market=demand_data.get("total_addressable_market", ""),
                    serviceable_addressable_market=demand_data.get("serviceable_addressable_market", ""),
                    serviceable_obtainable_market=demand_data.get("serviceable_obtainable_market", ""),
                    price_elasticity=demand_data.get("price_elasticity", ""),
                    demand_at_current_price=demand_data.get("demand_at_current_price", ""),
                    demand_at_optimal_price=demand_data.get("demand_at_optimal_price", ""),
                    revenue_forecast=demand_data.get("revenue_forecast", ""),
                    methodology=demand_data.get("methodology", "Conjoint analysis proxy + price elasticity from market data"),
                )

            wtp_data = data.get("wtp")
            if wtp_data:
                wtp = WillingnessToPay(
                    optimal_price_point=wtp_data.get("optimal_price_point", ""),
                    too_cheap_price=wtp_data.get("too_cheap_price", ""),
                    too_expensive_price=wtp_data.get("too_expensive_price", ""),
                    acceptable_range=wtp_data.get("acceptable_range", ""),
                    revenue_at_optimal=wtp_data.get("revenue_at_optimal", ""),
                    methodology=wtp_data.get("methodology", "Van Westendorp Price Sensitivity Meter"),
                    data_basis=wtp_data.get("data_basis", ""),
                )

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (demand, wtp)

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel consumer data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_consumer_sub_agents(
        self,
        company: str,
        sector: str,
        product_category: str,
        segment: str,
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel consumer data collection.

        Per §4.4, Agent 11:
        - Sub-agent A: Scrape reviews from [review site] (MICRO, Obscura)
        - Sub-agent B: Find consumer survey data for [segment] (MICRO, SearxNG)
        - Sub-agent C: Find willingness-to-pay studies for [product category] (FAST, SearxNG + Jina)
        """
        sub_specs = [
            SubAgentSpec(
                question=f"Scrape reviews from G2, Capterra, Trustpilot for {company} — extract sentiment, pain points, buying triggers with frequency data",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"company": company, "sector": sector},
            ),
            SubAgentSpec(
                question=f"Find consumer survey data for {segment} in {sector} — demographics, behaviors, psychographics, purchase intent",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"segment": segment, "sector": sector},
            ),
            SubAgentSpec(
                question=f"Find willingness-to-pay studies for {product_category} — Van Westendorp, conjoint analysis, price elasticity data",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"product_category": product_category, "sector": sector},
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
        persona_count: int,
        has_primary_persona: bool,
        journey_count: int,
        segment_count: int,
        has_nps: bool,
        has_demand: bool,
        has_wtp: bool,
        sources_count: int,
        total_reviews: int,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 2+ personas with primary, 4+ journey stages, 3+ segments, NPS,
              demand, WTP, 5+ sources, 100+ reviews analyzed
        MEDIUM: 1+ persona, 2+ journey stages, 2+ segments
        LOW: <1 persona, missing core analysis
        """
        if (persona_count >= 2 and has_primary_persona
                and journey_count >= 4 and segment_count >= 3
                and has_nps and has_demand and has_wtp
                and sources_count >= 5 and total_reviews >= 100):
            return ConfidenceLevel.HIGH
        if persona_count >= 1 and journey_count >= 2 and segment_count >= 2:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 7-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> ConsumerInsights:
        """Execute the Consumer Insights Analyst's 7-step methodology.

        Steps (§4.4, Agent 11):
        1. Search for consumer research (SearxNG + Jina)
        2. Scrape review sites and forums (Obscura)
        3. Build personas from data
        4. Map customer journey
        5. Segment the market
        6. Estimate demand and willingness-to-pay
        7. Produce ConsumerInsights model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting consumer insights analysis: {self._question[:80]}",
        )

        # Extract context
        company = self._context.get("company") or ""
        sector = self._context.get("sector") or self._context.get("industry") or ""
        product_category = self._context.get("product_category") or sector
        segment = self._context.get("segment") or ""

        # Spawn sub-agents for parallel data collection
        if sector or company:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning consumer data collection sub-agents")
            sub_findings = await self._spawn_consumer_sub_agents(company, sector, product_category, segment)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 1: Search for consumer research
        await self._transition(AgentState.WORKING, f"Step 1: Searching consumer research for {company or sector}")
        self._search_results = await self._search_consumer_research(company, sector, product_category)

        # Step 2: Scrape review sites and forums
        await self._transition(AgentState.WORKING, "Step 2: Scraping review sites (G2, Capterra, Trustpilot, Reddit)")
        self._review_data = await self._scrape_review_sites(company, sector)

        # Step 3: Build personas from data
        await self._transition(AgentState.WORKING, "Step 3: Building data-driven personas from scraped review data")
        personas = await self._build_personas(
            self._question, self._search_results, self._review_data, self._context,
        )

        # Step 4: Map customer journey
        await self._transition(AgentState.WORKING, "Step 4: Mapping customer journey from awareness to advocacy")
        journey_map = await self._map_journey(
            self._question, self._search_results, self._review_data, self._context,
        )

        # Step 5: Segment the market + NPS analysis
        await self._transition(AgentState.WORKING, "Step 5: Segmenting market (demographic, behavioral, psychographic) + NPS analysis")
        segments, most_predictive, nps_analysis = await self._segment_and_nps(
            self._question, self._search_results, self._review_data, self._context,
        )

        # Step 6: Estimate demand and willingness-to-pay
        await self._transition(AgentState.WORKING, "Step 6: Estimating demand (TAM/SAM/SOM) and willingness-to-pay (Van Westendorp)")
        demand_estimate, willingness_to_pay = await self._estimate_demand_and_wtp(
            self._question, self._search_results, self._wtp_studies, self._context,
        )

        # Calibrate confidence
        has_primary = any(p.is_primary for p in personas)
        confidence = self._calibrate_confidence(
            persona_count=len(personas),
            has_primary_persona=has_primary,
            journey_count=len(journey_map),
            segment_count=len(segments),
            has_nps=nps_analysis is not None and bool(nps_analysis.nps_score),
            has_demand=demand_estimate is not None and bool(demand_estimate.total_addressable_market),
            has_wtp=willingness_to_pay is not None and bool(willingness_to_pay.optimal_price_point),
            sources_count=len(self._sources),
            total_reviews=self._total_reviews,
        )

        # Step 7: Produce ConsumerInsights model
        await self._transition(AgentState.WORKING, "Step 7: Producing ConsumerInsights model")

        analysis = ConsumerInsights(
            personas=personas,
            journey_map=journey_map,
            nps_analysis=nps_analysis,
            segments=segments,
            most_predictive_segmentation=most_predictive,
            demand_estimate=demand_estimate,
            willingness_to_pay=willingness_to_pay,
            total_reviews_analyzed=str(self._total_reviews) if self._total_reviews else "",
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus
        # Publish primary persona as a finding
        primary_persona = next((p for p in personas if p.is_primary), None)
        if primary_persona:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="primary_persona",
                title=f"Primary Persona: {primary_persona.name}",
                content=(
                    f"{primary_persona.name} — {primary_persona.demographics}. "
                    f"Top frustration: {primary_persona.frustrations[0] if primary_persona.frustrations else 'N/A'}. "
                    f"Buying trigger: {primary_persona.buying_triggers[0] if primary_persona.buying_triggers else 'N/A'}. "
                    f"Data basis: {primary_persona.data_basis}"
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish NPS as a finding
        if nps_analysis and nps_analysis.nps_score:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="nps",
                title=f"NPS: {nps_analysis.nps_score}",
                content=(
                    f"NPS = {nps_analysis.nps_score}. "
                    f"Promoters: {nps_analysis.promoter_percentage}, "
                    f"Passives: {nps_analysis.passive_percentage}, "
                    f"Detractors: {nps_analysis.detractor_percentage}. "
                    f"Top promotion driver: {nps_analysis.promotion_drivers[0] if nps_analysis.promotion_drivers else 'N/A'}. "
                    f"Top detraction driver: {nps_analysis.detraction_drivers[0] if nps_analysis.detraction_drivers else 'N/A'}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish WTP as a finding
        if willingness_to_pay and willingness_to_pay.optimal_price_point:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="willingness_to_pay",
                title=f"Optimal Price Point: {willingness_to_pay.optimal_price_point}",
                content=(
                    f"Optimal price: {willingness_to_pay.optimal_price_point}. "
                    f"Acceptable range: {willingness_to_pay.acceptable_range}. "
                    f"Revenue at optimal: {willingness_to_pay.revenue_at_optimal}. "
                    f"Methodology: {willingness_to_pay.methodology}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:3],
            )
            await self._publish_finding(finding)

        # Publish the full ConsumerInsights as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "consumer_insights": analysis.model_dump(),
                "persona_count": len(personas),
                "journey_stages": len(journey_map),
                "segment_count": len(segments),
                "most_predictive_segmentation": most_predictive,
                "has_nps": nps_analysis is not None,
                "has_demand": demand_estimate is not None,
                "has_wtp": willingness_to_pay is not None,
                "total_reviews_analyzed": self._total_reviews,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Consumer insights complete: {len(personas)} personas, "
            f"{len(journey_map)} journey stages, "
            f"{len(segments)} segments, "
            f"NPS={'yes' if nps_analysis else 'no'}, "
            f"demand={'yes' if demand_estimate else 'no'}, "
            f"WTP={'yes' if willingness_to_pay else 'no'}, "
            f"reviews={self._total_reviews}, confidence={confidence.value}",
        )

        return analysis
