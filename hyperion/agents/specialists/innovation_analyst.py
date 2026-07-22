"""
HYPERION Innovation Analyst — Agent 13, the emerging technology specialist.

This is NOT a generic "research new tech" agent. This is a specialist with
6 proprietary analytical frameworks:

- Technology readiness levels (TRL): Assess technologies on the NASA TRL
  scale (1-9) from basic research to deployed. Identify which emerging techs
  are ready for production use vs. still experimental. Not just 'AI is
  transformative' — 'LLM-based customer support is at TRL 8 (ready for
  production) while autonomous agents are at TRL 4 (2-3 years from production
  readiness).'
- Gartner hype cycle positioning: Plot technologies on the hype cycle
  (innovation trigger → peak of inflated expectations → trough of
  disillusionment → slope of enlightenment → plateau of productivity).
  Identify where each tech currently sits.
- Horizon scanning: Systematically scan for signals of change across 3
  horizons: H1 (current, 0-12 months), H2 (emerging, 1-3 years), H3 (future,
  3-10 years).
- Disruption pattern analysis: Identify which disruption pattern applies:
  low-end disruption (cheaper, simpler), new-market disruption (serving
  non-consumers), or architectural disruption (reconfiguring the value chain).
- First-mover vs. fast-follower: Analyze whether first-mover advantage
  applies in this market or whether fast-follower is the better strategy.
  Consider: network effects, switching costs, learning curve, patent
  protection, and brand. Not just 'be first' — 'first-mover advantage is
  weak here because switching costs are low and network effects are absent.
  Fast-follower is the better strategy.'
- Innovation portfolio: Map the company's innovation initiatives on the
  3-horizon portfolio. Identify if the portfolio is balanced or over-invested
  in one horizon.

It separates hype from reality using the Gartner hype cycle and TRL scale. It
doesn't say "AI is transformative" — it says "LLM-based customer support is at
the slope of enlightenment (TRL 8) and ready for production, while autonomous
agents are at the peak of inflated expectations (TRL 4) and 2-3 years from
production readiness." It always assesses first-mover advantage — because in
some markets, being first is a disadvantage. (§4.4, Agent 13)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura, Wayback
Sub-agents: Max 3 — emerging tech search, patent filings, historical adoption
Output: InnovationAnalysis (TRL, hype cycle, horizon scan, disruption,
        first-mover, portfolio)

Methodology (§4.4, Agent 13):
1. Search for emerging technologies in the space (SearxNG + Jina)
2. Scrape patent databases and research portals (Obscura)
3. Pull historical trend data (Wayback)
4. Assess TRL for each technology
5. Plot on hype cycle
6. Run horizon scan
7. Analyze disruption patterns
8. Assess first-mover vs. fast-follower
9. Produce InnovationAnalysis model
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
    DisruptionAnalysis,
    DisruptionPattern,
    FirstMoverAnalysis,
    HypeCyclePhase,
    HypeCyclePosition,
    HorizonScanItem,
    InnovationAnalysis,
    InnovationPortfolio,
    InnovationPortfolioItem,
    KeyFinding,
    Source,
    SourceCredibility,
    TechnologyTRL,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


INNOVATION_ANALYST_SPEC = AgentSpec(
    name=AgentName.INNOVATION_ANALYST,
    role=AgentRole.SPECIALIST,
    display_name="Innovation Analyst",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.WAYBACK,
        ToolName.SEMANTIC_SCHOLAR,
        ToolName.OPEN_ALEX,
        ToolName.GOOGLE_TRENDS,
        ToolName.HACKERNEWS,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Technology readiness levels (TRL)",
            description=(
                "Assess technologies on the NASA TRL scale (1-9) from basic "
                "research to deployed. Identify which emerging techs are ready "
                "for production use vs. still experimental. Not just 'AI is "
                "transformative' — 'LLM-based customer support is at TRL 8 "
                "(ready for production) while autonomous agents are at TRL 4 "
                "(2-3 years from production readiness).' Each technology has "
                "TRL level, description, is_production_ready, time_to_production, "
                "key bottlenecks, and evidence."
            ),
            inputs=["technology_list", "research_papers", "patent_filings", "deployment_data"],
            outputs=["trl_level", "is_production_ready", "time_to_production", "key_bottlenecks"],
        ),
        SkillSpec(
            name="Gartner hype cycle positioning",
            description=(
                "Plot technologies on the hype cycle: innovation trigger → "
                "peak of inflated expectations → trough of disillusionment → "
                "slope of enlightenment → plateau of productivity. Identify "
                "where each tech currently sits. Not just 'AI is hyped' — "
                "'LLM-based customer support is at the slope of enlightenment, "
                "2-5 years to plateau. Autonomous agents are at the peak of "
                "inflated expectations, 5-10 years to plateau.'"
            ),
            inputs=["technology_list", "hype_indicators", "adoption_data", "media_coverage"],
            outputs=["phase", "years_to_plateau", "is_overhyped", "hype_vs_reality_gap"],
        ),
        SkillSpec(
            name="Horizon scanning",
            description=(
                "Systematically scan for signals of change across 3 horizons: "
                "H1 (current, 0-12 months), H2 (emerging, 1-3 years), H3 "
                "(future, 3-10 years). Each signal has impact, probability, "
                "time horizon, and recommended action."
            ),
            inputs=["technology_trends", "research_signals", "market_signals", "regulatory_signals"],
            outputs=["h1_signals", "h2_signals", "h3_signals", "recommended_actions"],
        ),
        SkillSpec(
            name="Disruption pattern analysis",
            description=(
                "Identify which disruption pattern applies: low-end disruption "
                "(cheaper, simpler), new-market disruption (serving non-"
                "consumers), or architectural disruption (reconfiguring the "
                "value chain). Not just 'disruption is coming' — 'new-market "
                "disruption pattern: AI tutors serving non-consumers (students "
                "without access to private tutoring). Incumbents (Kumon, "
                "Sylvan) are vulnerable because they serve the high end.'"
            ),
            inputs=["technology_assessment", "market_analysis", "competitor_landscape"],
            outputs=["pattern", "disrupted_companies", "disrupting_companies", "defensibility"],
        ),
        SkillSpec(
            name="First-mover vs. fast-follower",
            description=(
                "Analyze whether first-mover advantage applies in this market "
                "or whether fast-follower is the better strategy. Consider: "
                "network effects, switching costs, learning curve, patent "
                "protection, and brand. Not just 'be first' — 'first-mover "
                "advantage is weak here because switching costs are low and "
                "network effects are absent. Fast-follower is the better "
                "strategy — let others bear the R&D cost and learn from "
                "their mistakes.'"
            ),
            inputs=["market_characteristics", "network_effects", "switching_costs", "patent_landscape"],
            outputs=["recommendation", "rationale", "network_effects", "switching_costs", "examples"],
        ),
        SkillSpec(
            name="Innovation portfolio",
            description=(
                "Map the company's innovation initiatives on the 3-horizon "
                "portfolio. Identify if the portfolio is balanced or over-"
                "invested in one horizon. Not just 'invest in innovation' — "
                "'Portfolio is over-invested in H1 (80% of initiatives) and "
                "under-invested in H3 (0%). Rebalance: shift 20% of H1 budget "
                "to H3 moonshots.'"
            ),
            inputs=["innovation_initiatives", "budget_allocation", "horizon_mapping"],
            outputs=["h1_count", "h2_count", "h3_count", "is_balanced", "rebalancing_recommendation"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Innovation Analyst — the specialist who scans "
        "for emerging technologies, maps disruption patterns, assesses "
        "innovation portfolios, and evaluates first-mover vs. fast-follower "
        "strategies.\n\n"
        "Your proprietary frameworks:\n"
        "1. TRL (Technology Readiness Levels): NASA TRL scale 1-9. Not just "
        "'AI is transformative' — 'LLM-based customer support is at TRL 8 "
        "(ready for production) while autonomous agents are at TRL 4 (2-3 "
        "years from production readiness).'\n"
        "2. Gartner hype cycle: Innovation trigger → peak of inflated "
        "expectations → trough of disillusionment → slope of enlightenment → "
        "plateau of productivity. Where does each tech sit?\n"
        "3. Horizon scanning: H1 (0-12 months), H2 (1-3 years), H3 (3-10 "
        "years). Signals of change with impact, probability, and action.\n"
        "4. Disruption patterns: Low-end (cheaper, simpler), new-market "
        "(serving non-consumers), architectural (reconfiguring value chain).\n"
        "5. First-mover vs. fast-follower: Network effects, switching costs, "
        "learning curve, patent protection, brand. In some markets, being "
        "first is a DISADVANTAGE.\n"
        "6. Innovation portfolio: 3-horizon portfolio. Is it balanced or "
        "over-invested in one horizon?\n\n"
        "Rules:\n"
        "- SEPARATE HYPE FROM REALITY. Use TRL and hype cycle together. A "
        "technology at the peak of inflated expectations but TRL 4 is "
        "OVERHYPED. A technology at the slope of enlightenment and TRL 8 is "
        "ready for production.\n"
        "- ALWAYS ASSESS FIRST-MOVER ADVANTAGE. Don't just say 'be first.' "
        "Analyze whether first-mover advantage actually applies. In some "
        "markets, fast-follower is the better strategy.\n"
        "- EACH TRL ASSESSMENT MUST HAVE EVIDENCE. Not just 'TRL 4' — 'TRL 4 "
        "based on 3 lab demonstrations and 0 production deployments.'\n"
        "- HYPE CYCLE MUST IDENTIFY OVERHYPED TECHNOLOGIES. Flag technologies "
        "where hype exceeds reality.\n"
        "- DISRUPTION PATTERN MUST BE SPECIFIC. Not just 'disruption is "
        "coming' — identify the pattern, the disrupted, the disruptors, and "
        "whether incumbents can defend.\n\n"
        "You can spawn up to 3 sub-agents for parallel data collection:\n"
        "- Sub-agent A: Find emerging tech in [space] (MICRO, SearxNG + Jina)\n"
        "- Sub-agent B: Find patent filings for [technology] (MICRO, Obscura)\n"
        "- Sub-agent C: Find historical adoption curves for [similar tech] "
        "(FAST, SearxNG + Wayback)\n\n"
        "Your output is an InnovationAnalysis Pydantic model — structured, "
        "not free text."
    ),
    spawn_condition="Spawned when the question involves emerging technologies, "
                     "innovation, disruption, TRL, hype cycle, first-mover "
                     "advantage, or innovation portfolio (INNOVATION, EMERGING_"
                     "TECH, DISRUPTION, TRL, HYPE_CYCLE, FIRST_MOVER types)",
    max_sub_agents=3,
    output_model="InnovationAnalysis",
)


# ─────────────────────────────────────────────────────────────────────────────
# Innovation Analyst Agent
# ─────────────────────────────────────────────────────────────────────────────


class InnovationAnalyst(BaseAgent):
    """Agent 13: The emerging technology and innovation specialist.

    Scans for emerging technologies, maps disruption patterns, assesses
    innovation portfolios, and evaluates first-mover vs. fast-follower
    strategies. Separates hype from reality using TRL scale and Gartner hype
    cycle. Always assesses first-mover advantage. (§4.4, Agent 13)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Searches for emerging technologies (SearxNG + Jina)
    3. Scrapes patent databases and research portals (Obscura)
    4. Pulls historical trend data (Wayback)
    5. Assesses TRL, plots hype cycle, runs horizon scan
    6. Analyzes disruption patterns and first-mover advantage
    7. Produces InnovationAnalysis model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or INNOVATION_ANALYST_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Collected raw data
        self._search_results: list[dict[str, Any]] = []
        self._extracted_content: list[dict[str, Any]] = []
        self._patent_data: list[dict[str, Any]] = []
        self._historical_data: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []

        # Identified technologies
        self._technologies: list[str] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Innovation Analyst listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests (e.g., Strategy Analyst
          requesting innovation landscape for strategy formulation)
        - FINDINGS: receives findings from other agents that may inform
          innovation analysis (e.g., Technology Analyst's tech assessment,
          Market Analyst's market growth data, Competitive Intel's
          competitor innovation initiatives)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "innovation_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                # Technology Analyst's tech assessment informs TRL
                if finding.finding_type == "technology_assessment":
                    self._context.setdefault("tech_data", []).append(finding.content)
                # Market Analyst's market growth informs disruption patterns
                elif finding.finding_type == "market_growth":
                    self._context.setdefault("market_data", []).append(finding.content)
                # Competitive Intel's competitor innovation informs portfolio
                elif finding.finding_type == "competitor_innovation":
                    self._context.setdefault("competitor_innovation", []).append(finding.content)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "emerging_tech":
                # Strategy Analyst requesting emerging tech landscape
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Search for emerging technologies (SearxNG + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _search_emerging_tech(self, sector: str, space: str) -> list[dict[str, Any]]:
        """Search for emerging technologies in the space.

        Uses SearxNG to find: emerging tech news, research papers, patent
        filings, and innovation case studies. Uses Jina to extract academic
        papers, tech blogs, and innovation reports.
        """
        results: list[dict[str, Any]] = []

        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            query_patterns = [
                f"emerging technologies {space or sector} 2024 2025",
                f"{space or sector} innovation trends breakthrough",
                f"{space or sector} research papers arXiv technology",
                f"{space or sector} Gartner hype cycle 2024",
                f"{space or sector} technology readiness level TRL",
                f"{space or sector} disruption innovation pattern",
                f"{space or sector} patent filings innovation",
                f"first mover advantage {space or sector} technology",
                f"{space or sector} innovation portfolio horizon scanning",
                f"{space or sector} emerging tech adoption curve",
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
    # Step 2: Scrape patent databases and research portals (Obscura)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_patent_databases(self, space: str, sector: str) -> list[dict[str, Any]]:
        """Scrape JS-rendered patent databases, arXiv, research portals, and
        innovation dashboards.

        Uses Obscura to scrape: Google Patents, arXiv, research portals,
        innovation dashboards. Extracts patent filing trends and research
        activity signals.
        """
        results: list[dict[str, Any]] = []

        try:
            obscura = self.get_tool(ToolName.OBSCURA)

            db_urls = [
                f"https://patents.google.com/?q={space or sector}&oq={space or sector}",
                f"https://arxiv.org/search/?query={space or sector}&searchtype=all",
                "https://www.gartner.com/en/research/megatrends",
                f"https://www.cbinsights.com/research-portal?industry={sector}",
            ]

            for url in db_urls[:5]:
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
                            title=f"Patent/Research — {url.split('/')[2]}",
                            url=url,
                            credibility=SourceCredibility.INDUSTRY_REPORT,
                            key_data=f"Patent/research data from {url.split('/')[2]}",
                        ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Pull historical trend data (Wayback)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_historical_trends(self, space: str, sector: str) -> list[dict[str, Any]]:
        """Pull historical snapshots of technology trends to track hype vs.
        reality over time.

        Uses Wayback Machine to pull historical snapshots of technology trend
        pages, hype cycle reports, and adoption curves. This is critical for
        separating hype from reality — if a technology was "2 years away" 5
        years ago, it's probably overhyped.
        """
        results: list[dict[str, Any]] = []

        try:
            wayback = self.get_tool(ToolName.WAYBACK)

            # URLs to check historical snapshots for
            trend_urls = [
                f"https://www.gartner.com/en/research/megatrends",
                f"https://en.wikipedia.org/wiki/{space or sector}_technology",
                f"https://www.technologyreview.com/topic/{space or sector}/",
            ]

            for url in trend_urls[:4]:
                try:
                    # Get snapshots from 2, 5, and 10 years ago
                    for years_ago in [2, 5]:
                        snapshot = await wayback.get_snapshot(url, years_ago=years_ago)
                        if snapshot:
                            results.append({
                                "url": url,
                                "years_ago": years_ago,
                                "snapshot": snapshot,
                            })
                            self._sources.append(Source(
                                id=f"src_{len(self._sources):03d}",
                                title=f"Wayback — {url.split('/')[2]} ({years_ago}y ago)",
                                url=url,
                                credibility=SourceCredibility.NEWS,
                                key_data=f"Historical snapshot from {years_ago} years ago",
                            ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError):
            pass

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Assess TRL for each technology
    # ─────────────────────────────────────────────────────────────────────

    async def _assess_trl(
        self,
        question: str,
        search_results: list[dict[str, Any]],
        patent_data: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[list[TechnologyTRL], list[str]]:
        """Assess Technology Readiness Levels for each identified technology.

        Assesses technologies on the NASA TRL scale (1-9). Identifies which
        are ready for production (TRL 7+) vs. still experimental. Each
        assessment has evidence supporting it.

        Returns (trl_assessments, technology_names).
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:12]
        )
        patent_summary = json.dumps(
            [{"url": d.get("url", ""), "data": str(d.get("data", ""))[:200]} for d in patent_data[:3]],
            default=str,
        )[:500]

        prompt = (
            "You are the HYPERION Innovation Analyst assessing Technology "
            "Readiness Levels (TRL).\n\n"
            f"Question: {question}\n\n"
            f"Search results:\n{search_summary}\n\n"
            f"Patent/research data:\n{patent_summary}\n\n"
            "Identify 3-6 emerging technologies in this space and assess each "
            "on the NASA TRL scale (1-9):\n"
            "TRL 1: Basic principles observed\n"
            "TRL 2: Technology concept formulated\n"
            "TRL 3: Experimental proof of concept\n"
            "TRL 4: Technology validated in lab\n"
            "TRL 5: Technology validated in relevant environment\n"
            "TRL 6: Technology demonstrated in relevant environment\n"
            "TRL 7: System prototype demonstration in operational environment\n"
            "TRL 8: System completed and qualified\n"
            "TRL 9: Actual system proven in operational environment\n\n"
            "For each technology:\n"
            "- technology: name\n"
            "- trl_level: 1-9\n"
            "- trl_description: what this TRL means for THIS technology\n"
            "- is_production_ready: true if TRL 7+\n"
            "- time_to_production: estimated time to production readiness\n"
            "- key_bottlenecks: what's preventing higher TRL\n"
            "- evidence: specific evidence supporting this TRL (papers, demos, deployments)\n\n"
            "NOT just 'AI is transformative.' Be specific: 'LLM-based customer "
            "support is at TRL 8 (ready for production) while autonomous agents "
            "are at TRL 4 (2-3 years from production readiness).'\n\n"
            "Return JSON:\n"
            "{\n"
            '  "technologies": ["..."],\n'
            '  "trl_assessments": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        trl_assessments: list[TechnologyTRL] = []
        technologies: list[str] = []

        if not response.success or not response.content:
            return (trl_assessments, technologies)

        try:
            data = json.loads(response.content)
            technologies = data.get("technologies", [])

            for t in data.get("trl_assessments", []):
                trl_level = int(t.get("trl_level", 1))
                trl_assessments.append(TechnologyTRL(
                    technology=t.get("technology", "Unknown"),
                    trl_level=trl_level,
                    trl_description=t.get("trl_description", ""),
                    is_production_ready=trl_level >= 7,
                    time_to_production=t.get("time_to_production", ""),
                    key_bottlenecks=t.get("key_bottlenecks", []),
                    evidence=t.get("evidence", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (trl_assessments, technologies)

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Plot on hype cycle
    # ─────────────────────────────────────────────────────────────────────

    async def _plot_hype_cycle(
        self,
        question: str,
        technologies: list[str],
        trl_assessments: list[TechnologyTRL],
        search_results: list[dict[str, Any]],
        historical_data: list[dict[str, Any]],
    ) -> list[HypeCyclePosition]:
        """Plot technologies on the Gartner hype cycle.

        Identifies where each technology sits: innovation trigger → peak of
        inflated expectations → trough of disillusionment → slope of
        enlightenment → plateau of productivity. Flags overhyped technologies
        where hype exceeds TRL reality.
        """
        trl_summary = "\n".join(
            f"- {t.technology}: TRL {t.trl_level} ({'production ready' if t.is_production_ready else 'experimental'})"
            for t in trl_assessments
        )
        tech_list = ", ".join(technologies[:6]) if technologies else "the identified technologies"
        historical_summary = json.dumps(
            [{"url": d.get("url", ""), "years_ago": d.get("years_ago", 0),
              "snapshot": str(d.get("snapshot", ""))[:200]} for d in historical_data[:3]],
            default=str,
        )[:500]

        prompt = (
            "You are the HYPERION Innovation Analyst plotting the Gartner "
            "hype cycle.\n\n"
            f"Question: {question}\n\n"
            f"Technologies: {tech_list}\n\n"
            f"TRL assessments:\n{trl_summary}\n\n"
            f"Historical trend data:\n{historical_summary or 'No historical data available'}\n\n"
            "Plot each technology on the Gartner hype cycle:\n"
            "1. Innovation Trigger — technology breakthrough, no usable product\n"
            "2. Peak of Inflated Expectations — overhyped, unrealistic expectations\n"
            "3. Trough of Disillusionment — hype fades, reality sets in\n"
            "4. Slope of Enlightenment — practical applications emerge\n"
            "5. Plateau of Productivity — mainstream adoption\n\n"
            "For each technology:\n"
            "- technology: name\n"
            "- phase: one of the 5 phases\n"
            "- phase_description: what this phase means for this technology\n"
            "- years_to_plateau: estimated years to plateau of productivity\n"
            "- is_overhyped: true if hype exceeds TRL reality (e.g., peak of "
            "expectations but TRL 4)\n"
            "- hype_vs_reality_gap: description of the gap\n\n"
            "Use historical data to validate: if a technology was '2 years away' "
            "5 years ago, it's probably overhyped.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "hype_cycle": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        positions: list[HypeCyclePosition] = []

        if not response.success or not response.content:
            return positions

        try:
            data = json.loads(response.content)

            phase_map = {
                "innovation_trigger": HypeCyclePhase.INNOVATION_TRIGGER,
                "peak_of_inflated_expectations": HypeCyclePhase.PEAK_OF_INFLATED_EXPECTATIONS,
                "trough_of_disillusionment": HypeCyclePhase.TROUGH_OF_DISILLUSIONMENT,
                "slope_of_enlightenment": HypeCyclePhase.SLOPE_OF_ENLIGHTENMENT,
                "plateau_of_productivity": HypeCyclePhase.PLATEAU_OF_PRODUCTIVITY,
            }

            for pos in data.get("hype_cycle", []):
                phase_str = pos.get("phase", "innovation_trigger")
                phase = phase_map.get(phase_str, HypeCyclePhase.INNOVATION_TRIGGER)

                positions.append(HypeCyclePosition(
                    technology=pos.get("technology", "Unknown"),
                    phase=phase,
                    phase_description=pos.get("phase_description", ""),
                    years_to_plateau=pos.get("years_to_plateau", ""),
                    is_overhyped=bool(pos.get("is_overhyped", False)),
                    hype_vs_reality_gap=pos.get("hype_vs_reality_gap", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return positions

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Run horizon scan
    # ─────────────────────────────────────────────────────────────────────

    async def _run_horizon_scan(
        self,
        question: str,
        technologies: list[str],
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> list[HorizonScanItem]:
        """Run horizon scan across H1, H2, H3.

        Systematically scans for signals of change across 3 horizons:
        H1 (current, 0-12 months), H2 (emerging, 1-3 years), H3 (future,
        3-10 years). Each signal has impact, probability, time horizon, and
        recommended action.
        """
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:200]}"
            for r in search_results[:8]
        )
        tech_list = ", ".join(technologies[:5]) if technologies else "the space"

        prompt = (
            "You are the HYPERION Innovation Analyst running a horizon scan.\n\n"
            f"Question: {question}\n\n"
            f"Technologies: {tech_list}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "Scan for signals of change across 3 horizons:\n"
            "H1 (0-12 months): Current trends, imminent changes\n"
            "H2 (1-3 years): Emerging signals, likely developments\n"
            "H3 (3-10 years): Future possibilities, speculative signals\n\n"
            "For each signal:\n"
            "- horizon: H1, H2, or H3\n"
            "- signal: name of the signal\n"
            "- description: what it is\n"
            "- impact: low, medium, high, or transformative\n"
            "- probability: probability of materializing\n"
            "- time_horizon: when expected to materialize\n"
            "- recommended_action: what the company should do about it\n\n"
            "Aim for 2-3 signals per horizon (6-9 total).\n\n"
            "Return JSON:\n"
            "{\n"
            '  "horizon_scan": [{...}]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        scan_items: list[HorizonScanItem] = []

        if not response.success or not response.content:
            return scan_items

        try:
            data = json.loads(response.content)

            for item in data.get("horizon_scan", []):
                scan_items.append(HorizonScanItem(
                    horizon=item.get("horizon", "H1"),
                    signal=item.get("signal", "Unknown"),
                    description=item.get("description", ""),
                    impact=item.get("impact", "medium"),
                    probability=item.get("probability", ""),
                    time_horizon=item.get("time_horizon", ""),
                    recommended_action=item.get("recommended_action", ""),
                ))

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return scan_items

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Analyze disruption patterns + first-mover + portfolio
    # ─────────────────────────────────────────────────────────────────────

    async def _analyze_disruption_and_strategy(
        self,
        question: str,
        technologies: list[str],
        trl_assessments: list[TechnologyTRL],
        search_results: list[dict[str, Any]],
        context: dict[str, Any],
    ) -> tuple[DisruptionAnalysis | None, FirstMoverAnalysis | None, InnovationPortfolio | None]:
        """Analyze disruption patterns, first-mover advantage, and innovation
        portfolio.

        Disruption: Identifies which pattern applies (low-end, new-market,
        architectural).

        First-mover: Analyzes whether first-mover advantage applies or
        fast-follower is better. Considers network effects, switching costs,
        learning curve, patent protection, brand.

        Portfolio: Maps innovation initiatives on 3-horizon portfolio.
        Identifies if balanced or over-invested.

        Returns (disruption_analysis, first_mover_analysis, innovation_portfolio).
        """
        trl_summary = "\n".join(
            f"- {t.technology}: TRL {t.trl_level}"
            for t in trl_assessments[:5]
        )
        search_summary = "\n".join(
            f"- {r.get('title', '')}: {r.get('snippet', '')[:150]}"
            for r in search_results[:6]
        )

        prompt = (
            "You are the HYPERION Innovation Analyst doing disruption + "
            "first-mover + portfolio analysis.\n\n"
            f"Question: {question}\n\n"
            f"Technologies:\n{trl_summary}\n\n"
            f"Search results:\n{search_summary}\n\n"
            "DISRUPTION PATTERN ANALYSIS:\n"
            "Identify which disruption pattern applies:\n"
            "- low_end: cheaper, simpler — serves least demanding customers\n"
            "- new_market: serving non-consumers — creates new market\n"
            "- architectural: reconfiguring the value chain\n\n"
            "For the disruption:\n"
            "- pattern: which pattern\n"
            "- description: how it applies\n"
            "- disrupted_companies: who's at risk (list)\n"
            "- disrupting_companies: who's driving it (list)\n"
            "- disruption_timeline: when it plays out\n"
            "- defensibility: can incumbents defend?\n\n"
            "FIRST-MOVER VS. FAST-FOLLOWER:\n"
            "Analyze whether first-mover advantage applies:\n"
            "- recommendation: first_mover, fast_follower, or fast_second\n"
            "- rationale: why\n"
            "- network_effects: strong, moderate, weak, none\n"
            "- switching_costs: high, medium, low\n"
            "- learning_curve: steep, moderate, flat\n"
            "- patent_protection: strong, moderate, weak, none\n"
            "- brand_advantage: strong, moderate, weak\n"
            "- first_mover_examples: examples and outcomes (list)\n"
            "- fast_follower_examples: examples and outcomes (list)\n\n"
            "INNOVATION PORTFOLIO:\n"
            "Map the company's innovation initiatives on 3-horizon portfolio:\n"
            "- items: list of initiatives with horizon, investment, ROI, status, risk\n"
            "- h1_count, h2_count, h3_count\n"
            "- is_balanced: true/false\n"
            "- imbalance_description: if unbalanced, what's wrong\n"
            "- recommendation: rebalancing recommendation\n\n"
            "Return JSON:\n"
            "{\n"
            '  "disruption": {...},\n'
            '  "first_mover": {...},\n'
            '  "portfolio": {...}\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        disruption: DisruptionAnalysis | None = None
        first_mover: FirstMoverAnalysis | None = None
        portfolio: InnovationPortfolio | None = None

        if not response.success or not response.content:
            return (disruption, first_mover, portfolio)

        try:
            data = json.loads(response.content)

            dis_data = data.get("disruption")
            if dis_data:
                pattern_str = dis_data.get("pattern", "low_end")
                pattern_map = {
                    "low_end": DisruptionPattern.LOW_END,
                    "new_market": DisruptionPattern.NEW_MARKET,
                    "architectural": DisruptionPattern.ARCHITECTURAL,
                }
                disruption = DisruptionAnalysis(
                    pattern=pattern_map.get(pattern_str, DisruptionPattern.LOW_END),
                    description=dis_data.get("description", ""),
                    disrupted_companies=dis_data.get("disrupted_companies", []),
                    disrupting_companies=dis_data.get("disrupting_companies", []),
                    disruption_timeline=dis_data.get("disruption_timeline", ""),
                    defensibility=dis_data.get("defensibility", ""),
                )

            fm_data = data.get("first_mover")
            if fm_data:
                first_mover = FirstMoverAnalysis(
                    recommendation=fm_data.get("recommendation", ""),
                    rationale=fm_data.get("rationale", ""),
                    network_effects=fm_data.get("network_effects", ""),
                    switching_costs=fm_data.get("switching_costs", ""),
                    learning_curve=fm_data.get("learning_curve", ""),
                    patent_protection=fm_data.get("patent_protection", ""),
                    brand_advantage=fm_data.get("brand_advantage", ""),
                    first_mover_examples=fm_data.get("first_mover_examples", []),
                    fast_follower_examples=fm_data.get("fast_follower_examples", []),
                )

            pf_data = data.get("portfolio")
            if pf_data:
                items: list[InnovationPortfolioItem] = []
                for item in pf_data.get("items", []):
                    items.append(InnovationPortfolioItem(
                        initiative=item.get("initiative", "Unknown"),
                        horizon=item.get("horizon", "H1"),
                        investment_level=item.get("investment_level", ""),
                        expected_roi=item.get("expected_roi", ""),
                        status=item.get("status", ""),
                        risk_level=item.get("risk_level", ""),
                    ))

                h1 = sum(1 for i in items if i.horizon == "H1")
                h2 = sum(1 for i in items if i.horizon == "H2")
                h3 = sum(1 for i in items if i.horizon == "H3")

                portfolio = InnovationPortfolio(
                    items=items,
                    h1_count=h1,
                    h2_count=h2,
                    h3_count=h3,
                    is_balanced=bool(pf_data.get("is_balanced", False)),
                    imbalance_description=pf_data.get("imbalance_description", ""),
                    recommendation=pf_data.get("recommendation", ""),
                )

        except (json.JSONDecodeError, ValueError, TypeError):
            pass

        return (disruption, first_mover, portfolio)

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel innovation data collection
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_innovation_sub_agents(
        self,
        space: str,
        sector: str,
        technologies: list[str],
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel innovation data collection.

        Per §4.4, Agent 13:
        - Sub-agent A: Find emerging tech in [space] (MICRO, SearxNG + Jina)
        - Sub-agent B: Find patent filings for [technology] (MICRO, Obscura)
        - Sub-agent C: Find historical adoption curves for [similar tech] (FAST, SearxNG + Wayback)
        """
        tech_str = ", ".join(technologies[:3]) if technologies else space or sector

        sub_specs = [
            SubAgentSpec(
                question=f"Find emerging technologies in {space or sector} — research papers, breakthroughs, new developments, innovation case studies",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"space": space, "sector": sector},
            ),
            SubAgentSpec(
                question=f"Find patent filings for {tech_str} — Google Patents, USPTO, WIPO. Extract filing trends, top patent holders, patent categories",
                parent_agent=self.name,
                model_tier=ModelTier.MICRO,
                tools=[ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"technologies": technologies[:3]},
            ),
            SubAgentSpec(
                question=f"Find historical adoption curves for technologies similar to {tech_str} — how long did similar technologies take to reach mainstream adoption? Use Wayback Machine for historical hype data",
                parent_agent=self.name,
                model_tier=ModelTier.FAST,
                tools=[ToolName.SEARXNG, ToolName.WAYBACK],
                findings_model="KeyFinding",
                timeout_seconds=300,
                context={"technologies": technologies[:3], "sector": sector},
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
        trl_count: int,
        hype_count: int,
        horizon_count: int,
        has_disruption: bool,
        has_first_mover: bool,
        has_portfolio: bool,
        sources_count: int,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on analysis completeness.

        HIGH: 3+ TRL assessments, 3+ hype cycle positions, 6+ horizon signals,
              disruption, first-mover, portfolio, 5+ sources
        MEDIUM: 2+ TRL, 2+ hype, 3+ horizon
        LOW: <2 TRL, missing core analysis
        """
        if (trl_count >= 3 and hype_count >= 3 and horizon_count >= 6
                and has_disruption and has_first_mover and has_portfolio
                and sources_count >= 5):
            return ConfidenceLevel.HIGH
        if trl_count >= 2 and hype_count >= 2 and horizon_count >= 3:
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
    ) -> InnovationAnalysis:
        """Execute the Innovation Analyst's 9-step methodology.

        Steps (§4.4, Agent 13):
        1. Search for emerging technologies in the space (SearxNG + Jina)
        2. Scrape patent databases and research portals (Obscura)
        3. Pull historical trend data (Wayback)
        4. Assess TRL for each technology
        5. Plot on hype cycle
        6. Run horizon scan
        7. Analyze disruption patterns
        8. Assess first-mover vs. fast-follower
        9. Produce InnovationAnalysis model
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting innovation analysis: {self._question[:80]}",
        )

        # Extract context
        sector = self._context.get("sector", self._context.get("industry", ""))
        space = self._context.get("space", sector)

        # Step 1: Search for emerging technologies
        await self._transition(AgentState.WORKING, f"Step 1: Searching for emerging technologies in {space or sector}")
        self._search_results = await self._search_emerging_tech(sector, space)

        # Step 2: Scrape patent databases and research portals
        await self._transition(AgentState.WORKING, "Step 2: Scraping patent databases and research portals (Obscura)")
        self._patent_data = await self._scrape_patent_databases(space, sector)

        # Step 3: Pull historical trend data
        await self._transition(AgentState.WORKING, "Step 3: Pulling historical trend data (Wayback)")
        self._historical_data = await self._pull_historical_trends(space, sector)

        # Step 4: Assess TRL for each technology
        await self._transition(AgentState.WORKING, "Step 4: Assessing Technology Readiness Levels (TRL 1-9)")
        trl_assessments, technologies = await self._assess_trl(
            self._question, self._search_results, self._patent_data, self._context,
        )
        self._technologies = technologies

        # Spawn sub-agents for parallel data collection
        if technologies or sector:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning innovation data collection sub-agents")
            sub_findings = await self._spawn_innovation_sub_agents(space, sector, technologies)
            self._sub_agent_findings = sub_findings
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 5: Plot on hype cycle
        await self._transition(AgentState.WORKING, "Step 5: Plotting technologies on Gartner hype cycle")
        hype_cycle_positions = await self._plot_hype_cycle(
            self._question, technologies, trl_assessments, self._search_results, self._historical_data,
        )

        # Step 6: Run horizon scan
        await self._transition(AgentState.WORKING, "Step 6: Running horizon scan (H1/H2/H3)")
        horizon_scan = await self._run_horizon_scan(
            self._question, technologies, self._search_results, self._context,
        )

        # Steps 7+8: Analyze disruption patterns + first-mover + portfolio
        await self._transition(AgentState.WORKING, "Step 7-8: Analyzing disruption patterns + first-mover advantage + innovation portfolio")
        disruption_analysis, first_mover_analysis, innovation_portfolio = await self._analyze_disruption_and_strategy(
            self._question, technologies, trl_assessments, self._search_results, self._context,
        )

        # Derive summary lists
        tech_ready = [t.technology for t in trl_assessments if t.is_production_ready]
        tech_overhyped = [p.technology for p in hype_cycle_positions if p.is_overhyped]

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            trl_count=len(trl_assessments),
            hype_count=len(hype_cycle_positions),
            horizon_count=len(horizon_scan),
            has_disruption=disruption_analysis is not None,
            has_first_mover=first_mover_analysis is not None,
            has_portfolio=innovation_portfolio is not None,
            sources_count=len(self._sources),
        )

        # Step 9: Produce InnovationAnalysis model
        await self._transition(AgentState.WORKING, "Step 9: Producing InnovationAnalysis model")

        analysis = InnovationAnalysis(
            trl_assessments=trl_assessments,
            hype_cycle_positions=hype_cycle_positions,
            horizon_scan=horizon_scan,
            disruption_analysis=disruption_analysis,
            first_mover_analysis=first_mover_analysis,
            innovation_portfolio=innovation_portfolio,
            key_emerging_technologies=technologies,
            technologies_ready_for_production=tech_ready,
            technologies_overhyped=tech_overhyped,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus
        # Publish production-ready technologies as a finding
        if tech_ready:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="production_ready_tech",
                title=f"Production-Ready Technologies: {', '.join(tech_ready[:3])}",
                content=(
                    f"Technologies at TRL 7+ ready for production: {', '.join(tech_ready)}. "
                    f"These have been validated in operational environments and "
                    f"are ready for deployment."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish overhyped technologies as a finding
        if tech_overhyped:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="overhyped_tech",
                title=f"Overhyped Technologies: {', '.join(tech_overhyped[:3])}",
                content=(
                    f"Technologies where hype exceeds reality: {', '.join(tech_overhyped)}. "
                    f"These are at the peak of inflated expectations but have "
                    f"low TRL — exercise caution before investing."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish first-mover recommendation as a finding
        if first_mover_analysis and first_mover_analysis.recommendation:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="first_mover_strategy",
                title=f"Strategy: {first_mover_analysis.recommendation}",
                content=(
                    f"Recommendation: {first_mover_analysis.recommendation}. "
                    f"Rationale: {first_mover_analysis.rationale}. "
                    f"Network effects: {first_mover_analysis.network_effects}. "
                    f"Switching costs: {first_mover_analysis.switching_costs}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish disruption pattern as a finding
        if disruption_analysis:
            finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="disruption_pattern",
                title=f"Disruption Pattern: {disruption_analysis.pattern.value}",
                content=(
                    f"Pattern: {disruption_analysis.pattern.value}. "
                    f"Description: {disruption_analysis.description}. "
                    f"Disrupted: {disruption_analysis.disrupted_companies[:2]}. "
                    f"Disruptors: {disruption_analysis.disrupting_companies[:2]}. "
                    f"Defensibility: {disruption_analysis.defensibility}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=self._sources[:2],
            )
            await self._publish_finding(finding)

        # Publish the full InnovationAnalysis as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "innovation_analysis": analysis.model_dump(),
                "trl_count": len(trl_assessments),
                "hype_cycle_count": len(hype_cycle_positions),
                "horizon_scan_count": len(horizon_scan),
                "has_disruption": disruption_analysis is not None,
                "has_first_mover": first_mover_analysis is not None,
                "has_portfolio": innovation_portfolio is not None,
                "production_ready": tech_ready,
                "overhyped": tech_overhyped,
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Innovation analysis complete: {len(trl_assessments)} TRL assessments, "
            f"{len(hype_cycle_positions)} hype cycle positions, "
            f"{len(horizon_scan)} horizon signals, "
            f"disruption={'yes' if disruption_analysis else 'no'}, "
            f"first_mover={'yes' if first_mover_analysis else 'no'}, "
            f"portfolio={'yes' if innovation_portfolio else 'no'}, "
            f"production_ready={len(tech_ready)}, "
            f"overhyped={len(tech_overhyped)}, "
            f"confidence={confidence.value}",
        )

        return analysis
