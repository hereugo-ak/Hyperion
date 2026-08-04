"""
HYPERION Competitive Intelligence, Agent 4, the competitor profiling specialist.

This is NOT a generic "list the competitors" agent. This is a specialist
with proprietary analytical frameworks:

- Competitor matrix: Structured comparison across 7 dimensions
- Strategic group mapping: Cluster competitors into direct vs. adjacent rivals
- Market share analysis: Revenue/customer/search/download-based with confidence
- Moat assessment: Hamilton Helmer 7-force framework, scored strong→nascent
- Positioning map: 2D plot to identify white space and competitive density
- It uses Obscura's stealth mode because competitor sites actively block bots.
- It cross-references current pricing with Wayback historical pricing to show
- pricing trends, not just current prices. It doesn't just list competitors, it maps their moats and identifies which are defensible vs. eroding. It
- always identifies white space, where no competitor is currently playing.
- (§4.4, Agent 4)

Model Tier: STANDARD (the agent itself); the competitor-naming decision
borrows STRONG once, see _name_competitors.
Tools: SearxNG, Jina, Obscura, Wayback, SEC_EDGAR, DEEP_SEARCH
Sub-agents: Max 5, one isolated depth-dossier sub-agent per competitor
           (each does its own SearxNG + Jina + Obscura + Wayback search).
Output: CompetitiveLandscape (competitor matrix, moat assessments, strategic
        groups, positioning map, white space, pricing trends, confidence, sources)

Methodology (§4.4, Agent 4), rewritten to remove the fragile two-stage
"plan queries → web search → strict integer-ID judge" pipeline:
1. Resolve the arena (sector + region + engagement entity) from context.
2. STRONG-tier LLM names the 3-5 most DIRECT competitors (one call, retried
   once on empty/malformed output).
3. Spawn one isolated sub-agent per competitor (up to 5) that builds a
   CompetitorDossier. A block/timeout on one competitor is that competitor's
   gap finding only — it cannot zero the whole landscape.
4. Build competitor matrix from the dossiers (pure aggregation).
5. Assess moats for top 5 competitors (from dossiers, optional light synthesis).
6. Create strategic group map.
7. Create positioning map.
8. Identify white space opportunities.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

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
    CompetitorDossier,
    CompetitiveLandscape,
    ConfidenceLevel,
    KeyFinding,
    Source,
    SourceCredibility,
)
from hyperion.tools.query_utils import get_engagement_focus, resolve_subject

# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


COMPETITIVE_INTEL_SPEC = AgentSpec(
    name=AgentName.COMPETITIVE_INTEL,
    role=AgentRole.SPECIALIST,
    display_name="Competitive Intelligence",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.WAYBACK,
        ToolName.SEC_EDGAR,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Competitor matrix",
            description=(
                "Build a structured comparison of competitors across 7 dimensions: "
                "product features, pricing, target customer, geographic coverage, "
                "funding stage, headcount, key partnerships. Each cell must cite a "
                "source. The matrix is the foundation for all subsequent analysis"
                "moat assessment, strategic grouping, and positioning all reference it."
            ),
            inputs=["competitor_list", "competitor_websites", "pricing_data", "funding_data"],
            outputs=["competitor_matrix", "dimension_sources"],
        ),
        SkillSpec(
            name="Strategic group mapping",
            description=(
                "Cluster competitors into strategic groups based on similarities in "
                "strategy, target market, and competitive approach. This reveals which "
                "competitors are direct rivals vs. adjacent players. Direct rivals "
                "compete head-to-head on the same dimensions. Adjacent players overlap "
                "but differ on key dimensions (e.g., different target segment or "
                "geography). The map shows which groups are crowded and which are open."
            ),
            inputs=["competitor_matrix", "strategy_similarity", "target_market_overlap"],
            outputs=["strategic_groups", "direct_rivals", "adjacent_players", "group_density"],
        ),
        SkillSpec(
            name="Market share analysis",
            description=(
                "Estimate market share from available data: revenue, customer count, "
                "search volume, app downloads. Always with confidence intervals and "
                "source citations. If revenue data is available, use revenue-based "
                "share. If only app downloads are available, use download-based share "
                "with a caveat that downloads ≠ revenue. Cross-validate multiple "
                "proxies when possible."
            ),
            inputs=["competitor_revenue", "customer_counts", "search_volume", "app_downloads"],
            outputs=["market_share_estimates", "confidence_intervals", "share_proxies_used"],
        ),
        SkillSpec(
            name="Moat assessment",
            description=(
                "Evaluate each competitor's competitive moat using the Hamilton Helmer "
                "7-force framework: (1) network effects, (2) switching costs, "
                "(3) scale advantages, (4) brand, (5) regulatory, (6) IP/proprietary "
                "tech, (7) distribution. Score each moat as strong/moderate/weak/nascent. "
                "Identify which moats are defensible (getting stronger) vs. eroding "
                "(getting weaker). A competitor with a strong but eroding moat is more "
                "vulnerable than one with a moderate but strengthening moat."
            ),
            inputs=["competitor_profiles", "network_data", "switching_cost_indicators", "scale_data"],
            outputs=["moat_scores", "moat_trends", "defensible_vs_eroding"],
        ),
        SkillSpec(
            name="Positioning map",
            description=(
                "Plot competitors on a 2D map to identify white space and competitive "
                "density. Common axes: price vs. quality, feature breadth vs. focus, "
                "geographic reach vs. depth, enterprise vs. consumer. White space is "
                "where no competitor is currently playing, these are potential "
                "opportunities. Competitive density is where many competitors cluster"
                "these are red oceans."
            ),
            inputs=["competitor_matrix", "positioning_dimensions"],
            outputs=["positioning_map_data", "white_space_areas", "competitive_density_zones"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Competitive Intelligence analyst, the specialist who "
        "profiles competitors, maps competitive positioning, assesses moats, and "
        "tracks market share. You answer 'who are we up against and how do they win?'\n\n"
        "Your proprietary frameworks:\n"
        "1. Competitor matrix: 7-dimension structured comparison (features, pricing, "
        "target customer, geography, funding, headcount, partnerships).\n"
        "2. Strategic group mapping: Cluster competitors into direct rivals vs. "
        "adjacent players based on strategy similarity.\n"
        "3. Market share analysis: Estimate share from revenue, customers, search "
        "volume, or app downloads, always with confidence intervals.\n"
        "4. Moat assessment: Hamilton Helmer 7-force framework (network effects, "
        "switching costs, scale, brand, regulatory, IP, distribution). Score "
        "strong/moderate/weak/nascent. Identify defensible vs. eroding moats.\n"
        "5. Positioning map: 2D plot (price vs. quality, breadth vs. focus) to find "
        "white space and competitive density.\n\n"
        "Rules:\n"
        "- ALWAYS use Obscura's stealth mode for competitor sites, they block bots.\n"
        "- ALWAYS cross-reference current pricing with Wayback historical pricing. "
        "Show pricing trends, not just current prices.\n"
        "- DON'T just list competitors, map their moats and identify which are "
        "defensible vs. eroding.\n"
        "- ALWAYS identify white space, where no competitor is currently playing.\n"
        "- Each competitor matrix cell must cite a source. No unsourced claims.\n"
        "- Market share estimates must include confidence intervals and the proxy "
        "used (revenue, downloads, search volume).\n"
        "- Moat scores must include trend direction (strengthening/weakening/stable).\n"
        "- Strategic groups must distinguish direct rivals from adjacent players.\n\n"
        "Your competitor discovery pipeline (§4.4 rewrite):\n"
        "1. One STRONG-tier call names the 3-5 most DIRECT competitors for the "
        "resolved sector + region + engagement entity.\n"
        "2. One isolated sub-agent per competitor builds a CompetitorDossier "
        "(pricing tiers, funding stage, headcount, partnerships, moat signals).\n"
        "3. A single competitor's sub-agent failure is that competitor's gap only — "
        "it never zeroes the whole landscape.\n\n"
        "Your output is a CompetitiveLandscape Pydantic model, structured, not free text."
    ),
    spawn_condition="Spawned when the question involves competitive analysis, market entry, "
                     "or positioning (GO_NO_GO, MARKET_ENTRY, COMPARISON types)",
    max_sub_agents=5,
    output_model="CompetitiveLandscape",
)


# ─────────────────────────────────────────────────────────────────────────────
# Competitive Intelligence Agent
# ─────────────────────────────────────────────────────────────────────────────


class CompetitiveIntel(BaseAgent):
    """Agent 4: The competitive intelligence specialist.

    Profiles competitors, maps positioning, assesses moats using the
    Hamilton Helmer framework, and identifies white space. Uses Obscura
    stealth mode for competitor sites and Wayback for historical pricing
    trends. (§4.4, Agent 4)

    Lifecycle:
    1. Receives task from Engagement Director via AgentBus HANDOFF
    2. Identifies all competitors in the space (SearxNG)
    3. Scrapes competitor websites for product/pricing/team info (Obscura)
    4. Pulls historical snapshots for trend analysis (Wayback)
    5. Builds competitor matrix, assesses moats, creates strategic group map
    6. Creates positioning map and identifies white space
    7. Produces CompetitiveLandscape model and publishes to bus
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or COMPETITIVE_INTEL_SPEC, bus=bus, router=router)

        # Engagement context
        self._question: str = ""
        self._engagement_id: str = ""
        self._context: dict[str, Any] = {}

        # Resolved arena (sector + region + engagement entity) for this run
        self._arena: dict[str, str] = {}

        # Discovered competitors (names) from the STRONG naming call
        self._competitor_names: list[str] = []

        # Per-competitor depth dossiers produced by isolated sub-agents
        self._competitor_dossiers: list[CompetitorDossier] = []

        # Competitor website URLs (derived from dossiers) for Wayback snapshots
        self._competitor_urls: dict[str, str] = {}  # name → website URL

        # Historical snapshots (Wayback) keyed by competitor name
        self._historical_snapshots: dict[str, list[dict[str, Any]]] = {}

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings: gap findings from any competitor whose sub-agent
        # failed/blocked, plus dossier-backed competitor profiles.
        self._sub_agent_findings: list[KeyFinding] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Competitive Intelligence agent listens to:
        - HANDOFF: receives task assignment from Engagement Director
        - REQUESTS: responds to data requests from other agents (e.g., Strategy
          Analyst requesting moat assessment for positioning)
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "competitive_analysis":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")
                self._context = context_bundle.get("context", {})
                # Competitor discovery is now driven entirely by the STRONG
                # naming call in run() (see _name_competitors); the Engagement
                # Director no longer pre-seeds names here.

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
            if request_type == "moat_assessment":
                # Strategy Analyst requesting moat data for a specific competitor
                # Handled during run(), just note the request
                pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Resolve the arena (replaces resolve_subject(..., "company", ...))
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_arena(self) -> dict[str, str]:
        """Build a single arena dict from context + engagement focus.

        The old code called ``resolve_subject(self._context, "company",
        "sector", "industry")`` which collapsed to "" whenever the Engagement
        Director omitted those keys and silently dropped the engagement entity
        and region. We now assemble one dict:

        - sector  ← context industry/sector/market/space (then engagement focus)
        - region  ← context geography / jurisdictions, OPTIONAL; if absent we
                    search agnostic and NEVER default to "US" (the 119-wrong-
                    country bug class)
        - subject ← the engagement entity (e.g. "Tesla entering India") from the
                    real user question, so we get EV makers in India, not global
                    automotive giants.

        Region is deliberately conditional: an empty region means "no geography
        filter", which grounding propagates to every sub-agent search.
        """
        ctx = self._context or {}
        focus_q, focus_subject, focus_geo = get_engagement_focus()

        subject = (
            ctx.get("question") or focus_q or self._question or ""
        ).strip()

        sector = (
            ctx.get("industry")
            or ctx.get("sector")
            or ctx.get("market")
            or ctx.get("space")
            or focus_subject
            or resolve_subject(
                ctx, "industry", "sector", "market", question=self._question
            )
            or ""
        ).strip()

        region = ""
        if ctx.get("geography"):
            region = str(ctx["geography"]).strip()
        elif ctx.get("jurisdictions"):
            js = ctx["jurisdictions"]
            if isinstance(js, (list, tuple)) and js:
                region = str(js[0]).strip()
            elif isinstance(js, str):
                region = js.strip()
        elif focus_geo:
            region = focus_geo.strip()

        return {"sector": sector, "region": region, "subject": subject}

    # ─────────────────────────────────────────────────────────────────────
    # Step 1 (linchpin): STRONG-tier competitor naming
    # ─────────────────────────────────────────────────────────────────────

    async def _name_competitors(self, arena: dict[str, str]) -> list[dict[str, str]]:
        """Name the 3-5 most DIRECT competitors with one STRONG-tier call.

        This is the decision step that previously used a small-model integer-ID
        judge whose degenerate 19-char response zeroed the entire section. The
        naming call now borrows STRONG and is validated + retried once. A thin
        market (1-2 real competitors) is returned as-is and flagged as white
        space downstream — only a truly empty/malformed result (transport/LLM
        failure) is treated as a gap.
        """
        sector = arena.get("sector", "")
        region = arena.get("region", "")
        subject = arena.get("subject", "")

        region_text = f" in {region}" if region else " (search agnostic — no geography constraint was specified)"
        subject_text = f" for the engagement: {subject}" if subject else ""

        prompt = (
            "You are the senior competitive-intelligence analyst naming the most "
            "DIRECT competitors for an engagement.\n\n"
            f"Engagement entity: {subject or '(not specified)'}\n"
            f"Sector / market: {sector or '(not specified)'}\n"
            f"Region: {region or 'global / agnostic'}\n\n"
            "Name the 3-5 most DIRECT competitors that compete head-to-head with the "
            "engagement entity in THIS sector and THIS region. Prefer real companies "
            "that actually operate in this sector/region over global giants that only "
            "tangentially overlap. For each, give a one-line rationale tying it to the "
            "arena.\n\n"
            "Return JSON:\n"
            '{"competitors": [{"name": "...", "why_it_competes": "..."}]}\n\n'
            "If the market is nascent and fewer than 3 real competitors exist, return "
            "what exists (it is a white-space signal) — but NEVER invent companies that "
            "do not operate in this sector/region."
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
            tier=ModelTier.STRONG,
        )
        names = self._parse_competitor_names(response)

        # Validation + 1 retry: empty or malformed output (the degenerate-response
        # bug) gets one more chance; a legitimately thin market (1-2 names) is kept.
        if not names:
            retry = await self._llm_complete(
                user_prompt=(
                    f"{prompt}\n\n"
                    "Your previous answer was empty or not valid JSON. You MUST return "
                    f"real companies operating in {sector or 'the relevant market'}"
                    f"{region_text}. If fewer than 3 exist, return those that do."
                ),
                urgency=TaskUrgency.HIGH,
                temperature=0.2,
                response_format={"type": "json_object"},
                tier=ModelTier.STRONG,
            )
            retry_names = self._parse_competitor_names(retry)
            if retry_names:
                names = retry_names

        return names

    def _parse_competitor_names(self, response: Any) -> list[dict[str, str]]:
        """Parse + validate the STRONG naming response into a name list.

        Returns [] on empty content, malformed JSON, or a structurally-invalid
        payload, so the caller can decide between retry and gap declaration.
        """
        if not getattr(response, "success", False) or not getattr(response, "content", ""):
            return []
        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError, TypeError):
            return []
        if not isinstance(data, dict) or not isinstance(data.get("competitors"), list):
            return []

        out: list[dict[str, str]] = []
        seen: set[str] = set()
        for candidate in data["competitors"]:
            if not isinstance(candidate, dict):
                continue
            name = candidate.get("name")
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                "name": name.strip(),
                "why_it_competes": (
                    candidate.get("why_it_competes", "")
                    if isinstance(candidate.get("why_it_competes"), str)
                    else ""
                ),
            })
        return out

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Per-competitor depth sub-agents (isolated, one per competitor)
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_competitor_sub_agents(
        self,
        competitors: list[dict[str, str]],
        sector: str,
        region: str,
    ) -> tuple[list[CompetitorDossier], list[KeyFinding]]:
        """Spawn one isolated sub-agent per competitor (up to 5).

        Each sub-agent does its own SearxNG + Jina + Obscura + Wayback research
        and returns a structured CompetitorDossier. Isolation is the whole point:
        one Obscura block/timeout on competitor N produces only that
        competitor's gap finding (via base.py:1020-1040) and can never zero the
        landscape for the other competitors.

        Returns (dossiers, gaps) where gaps are KeyFinding research-gap objects
        for the competitors whose sub-agent failed.
        """
        if not competitors:
            return ([], [])

        specs = []
        for c in competitors[:5]:
            name = c["name"]
            why = c.get("why_it_competes", "")
            region_text = f" in {region}" if region else ""
            specs.append(SubAgentSpec(
                question=(
                    f"Build a depth dossier for competitor '{name}' "
                    f"({why}) operating in {sector or 'the relevant market'}{region_text}. "
                    "Research and report: (1) pricing tiers, (2) funding stage, "
                    "(3) total raised, (4) headcount, (5) key partnerships, and "
                    "(6) observable moat signals (network effects, switching costs, "
                    "scale, brand, regulatory, IP, distribution). Cite a source URL "
                    "for every claim in evidence_urls."
                ),
                parent_agent=self.name,
                model_tier=ModelTier.STANDARD,
                tools=[ToolName.SEARXNG, ToolName.JINA, ToolName.OBSCURA, ToolName.WAYBACK],
                findings_model="CompetitorDossier",
                timeout_seconds=300,
                context={"competitor": name, "sector": sector, "region": region},
            ))

        results = await asyncio.gather(
            *(self._spawn_sub_agent(spec) for spec in specs),
            return_exceptions=True,
        )

        dossiers: list[CompetitorDossier] = []
        gaps: list[KeyFinding] = []
        for result in results:
            if isinstance(result, Exception):
                # A sub-agent spawn that escaped the isolation boundary.
                logger.warning("%s: competitor sub-agent error: %s", self.name.value, result)
                continue
            if not isinstance(result, list):
                continue
            for item in result:
                if isinstance(item, CompetitorDossier):
                    dossiers.append(item)
                elif isinstance(item, KeyFinding):
                    gaps.append(item)

        return (dossiers, gaps)

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Build competitor matrix (pure aggregation from dossiers)
    # ─────────────────────────────────────────────────────────────────────

    def _build_matrix_from_dossiers(
        self,
        dossiers: list[CompetitorDossier],
        region: str,
    ) -> dict[str, dict[str, str]]:
        """Aggregate the 7-dimension competitor matrix from dossiers.

        Pure function — no LLM call. Each cell cites the dossier's evidence
        URLs so downstream synthesis stays sourced.
        """
        matrix: dict[str, dict[str, str]] = {}
        for d in dossiers:
            cite = f" [source: {', '.join(d.evidence_urls[:2])}]" if d.evidence_urls else ""
            pricing = ", ".join(d.pricing_tiers) or "Unknown"
            funding = "Unknown"
            if d.funding_stage or d.total_raised:
                funding = " ".join(
                    part for part in (d.funding_stage, d.total_raised) if part
                )
            partnerships = ", ".join(d.key_partnerships) or "Unknown"
            moat = "; ".join(d.moat_signals) or "Unknown"
            matrix[d.name] = {
                "product_features": (moat if moat != "Unknown" else "Unknown") + cite,
                "pricing": pricing + cite,
                "target_customer": "Unknown" + cite,
                "geographic_coverage": (region or "Unknown") + cite,
                "funding_stage": funding + cite,
                "headcount": (d.headcount or "Unknown") + cite,
                "key_partnerships": partnerships + cite,
            }
        return matrix

    def _build_moats_from_dossiers(
        self,
        dossiers: list[CompetitorDossier],
    ) -> list[KeyFinding]:
        """Aggregate Hamilton-Helmer moat signals from dossiers (no LLM call)."""
        findings: list[KeyFinding] = []
        for d in dossiers:
            if not d.moat_signals and not d.funding_stage:
                continue
            content = (
                f"Moat signals: {'; '.join(d.moat_signals) or 'none observed'}. "
                f"Funding: {d.funding_stage or 'unknown'} "
                f"({d.total_raised or 'amount unknown'}). "
                f"Headcount: {d.headcount or 'unknown'}."
            )
            findings.append(KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="moat_assessment",
                title=f"Moat Assessment, {d.name}",
                content=content,
                confidence=ConfidenceLevel.MEDIUM,
                implications="defensible" if d.moat_signals else "eroding",
                sources=[
                    Source(
                        id=f"src_{uuid.uuid4().hex[:6]}",
                        title=f"{d.name} evidence",
                        url=url,
                        credibility=SourceCredibility.NEWS,
                    )
                    for url in d.evidence_urls[:3]
                ],
            ))
        return findings

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Pull historical snapshots (Wayback)
    # ─────────────────────────────────────────────────────────────────────

    async def _pull_historical_snapshots(self, competitors: list[str]) -> None:
        """Pull historical competitor website snapshots from Wayback Machine.

        Cross-references current pricing with historical pricing to show
        pricing trends, not just current prices. Also tracks product
        evolution and strategic pivots over time.
        """
        try:
            wayback = self.get_tool(ToolName.WAYBACK)

            for competitor in competitors[:5]:  # Limit to top 5 for rate limits
                url = self._competitor_urls.get(competitor, "")
                if not url:
                    continue

                # Get snapshots from different time periods
                snapshots = await wayback.get_snapshots(url, intervals=["1y", "2y", "5y"])
                if snapshots:
                    self._historical_snapshots[competitor] = snapshots

                    self._sources.append(Source(
                        id=f"src_{len(self._sources):03d}",
                        title=f"Wayback Machine, {competitor} historical snapshots",
                        url=f"https://web.archive.org/web/*/{url}",
                        credibility=SourceCredibility.NEWS,
                        key_data=(
                        "\n---\n".join(
                            f"Snapshot {snap.timestamp}: {snap.snapshot_url}"
                            for snap in snapshots[:6]
                        )[:500]
                        or None
                    ),
                    ))

        except (ValueError, AttributeError, RuntimeError):
            pass

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Build competitor matrix
    # ─────────────────────────────────────────────────────────────────────
    #
    # The competitor matrix is now a PURE aggregation of the per-competitor
    # CompetitorDossiers (see _build_matrix_from_dossiers above), not an extra
    # LLM call. The previous LLM-backed _build_competitor_matrix was removed:
    # its only inputs were the (now-deleted) scraped pages, and rebuilding it
    # here would duplicate work the sub-agents already did.

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Assess moats (Hamilton Helmer framework)
    # ─────────────────────────────────────────────────────────────────────

    async def _assess_moats(
        self,
        competitors: list[str],
        competitor_matrix: dict[str, dict[str, str]],
        scraped_data: dict[str, dict[str, Any]],
    ) -> list[KeyFinding]:
        """Assess each competitor's moat using the Hamilton Helmer 7-force framework.

        Forces: (1) network effects, (2) switching costs, (3) scale advantages,
        (4) brand, (5) regulatory, (6) IP/proprietary tech, (7) distribution.

        Score each moat as strong/moderate/weak/nascent.
        Identify which moats are defensible (strengthening) vs. eroding (weakening).
        """
        matrix_summary = json.dumps(competitor_matrix, indent=2)[:3000]

        prompt = (
            "You are the Competitive Intelligence analyst performing moat assessment.\n\n"
            f"Competitors: {', '.join(competitors[:5])}\n\n"
            f"Competitor matrix:\n{matrix_summary}\n\n"
            "For each of the top 5 competitors, assess their moats using the "
            "Hamilton Helmer 7-force framework:\n"
            "1. Network effects, does the product get better as more users join?\n"
            "2. Switching costs, how hard is it for customers to leave?\n"
            "3. Scale advantages, do they have cost advantages from size?\n"
            "4. Brand, is their brand a competitive advantage?\n"
            "5. Regulatory, do they have licenses/patents/regulatory moats?\n"
            "6. IP/proprietary tech, do they have patented technology?\n"
            "7. Distribution, do they have exclusive distribution channels?\n\n"
            "Score each force as: strong, moderate, weak, or nascent.\n"
            "Also indicate trend: strengthening, stable, or eroding.\n\n"
            "Return JSON array:\n"
            "[{\n"
            '  "competitor": "name",\n'
            '  "network_effects": {"score": "strong|moderate|weak|nascent", "trend": "strengthening|stable|eroding", "rationale": "..."},\n'
            '  "switching_costs": {"score": "...", "trend": "...", "rationale": "..."},\n'
            '  "scale_advantages": {"score": "...", "trend": "...", "rationale": "..."},\n'
            '  "brand": {"score": "...", "trend": "...", "rationale": "..."},\n'
            '  "regulatory": {"score": "...", "trend": "...", "rationale": "..."},\n'
            '  "ip_proprietary_tech": {"score": "...", "trend": "...", "rationale": "..."},\n'
            '  "distribution": {"score": "...", "trend": "...", "rationale": "..."},\n'
            '  "overall_moat": "strong|moderate|weak|nascent",\n'
            '  "defensible_or_eroding": "defensible|eroding|mixed",\n'
            '  "summary": "1-2 sentence assessment"\n'
            "}]\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        moat_findings: list[KeyFinding] = []

        if not response.success or not response.content:
            return moat_findings

        try:
            data = json.loads(response.content)
            moat_list = data.get("moats", data) if isinstance(data, dict) else data
            if not isinstance(moat_list, list):
                moat_list = []

            for moat in moat_list:
                competitor = moat.get("competitor", "Unknown")
                forces = []
                for force_name in ["network_effects", "switching_costs", "scale_advantages",
                                   "brand", "regulatory", "ip_proprietary_tech", "distribution"]:
                    force = moat.get(force_name, {})
                    if isinstance(force, dict):
                        forces.append(
                            f"{force_name}: {force.get('score', 'unknown')} "
                            f"({force.get('trend', 'unknown')}), {force.get('rationale', '')[:100]}"
                        )

                moat_findings.append(KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="moat_assessment",
                    title=f"Moat Assessment, {competitor}",
                    content=(
                        f"Overall moat: {moat.get('overall_moat', 'unknown')}. "
                        f"Status: {moat.get('defensible_or_eroding', 'unknown')}. "
                        f"{' | '.join(forces)} "
                        f"Summary: {moat.get('summary', '')}"
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    implications=moat.get("defensible_or_eroding", ""),
                    sources=self._sources[:3],
                ))

        except (json.JSONDecodeError, ValueError):
            pass

        return moat_findings

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Strategic group mapping
    # ─────────────────────────────────────────────────────────────────────

    async def _create_strategic_group_map(
        self,
        competitors: list[str],
        competitor_matrix: dict[str, dict[str, str]],
    ) -> list[str]:
        """Cluster competitors into strategic groups.

        Direct rivals compete head-to-head on the same dimensions.
        Adjacent players overlap but differ on key dimensions.
        The map shows which groups are crowded and which are open.
        """
        matrix_summary = json.dumps(competitor_matrix, indent=2)[:3000]

        prompt = (
            "You are the Competitive Intelligence analyst creating a strategic group map.\n\n"
            f"Competitors: {', '.join(competitors[:10])}\n\n"
            f"Competitor matrix:\n{matrix_summary}\n\n"
            "Cluster competitors into strategic groups based on:\n"
            "- Similar strategy (low-cost vs. premium vs. niche)\n"
            "- Similar target market (same segment/geography)\n"
            "- Similar competitive approach (product-led vs. sales-led vs. channel)\n\n"
            "For each group, identify:\n"
            "- Group name (descriptive, e.g., 'Enterprise Platform Players')\n"
            "- Members (competitor names)\n"
            "- Group type: direct_rivals or adjacent_players\n"
            "- Density: crowded (3+ members) or open (<3 members)\n\n"
            "Return JSON array:\n"
            "[{\n"
            '  "group_name": "...",\n'
            '  "members": ["comp1", "comp2"],\n'
            '  "type": "direct_rivals|adjacent_players",\n'
            '  "density": "crowded|open",\n'
            '  "description": "..."\n'
            "}]\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        groups: list[str] = []

        if not response.success or not response.content:
            return groups

        try:
            data = json.loads(response.content)
            group_list = data.get("groups", data) if isinstance(data, dict) else data
            if not isinstance(group_list, list):
                group_list = []

            for group in group_list:
                name = group.get("group_name", "Unknown group")
                members = group.get("members", [])
                gtype = group.get("type", "unknown")
                density = group.get("density", "unknown")
                desc = group.get("description", "")
                groups.append(
                    f"{name} ({gtype}, {density}): {', '.join(members)}, {desc}"
                )

        except (json.JSONDecodeError, ValueError):
            pass

        return groups

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Positioning map
    # ─────────────────────────────────────────────────────────────────────

    async def _create_positioning_map(
        self,
        competitors: list[str],
        competitor_matrix: dict[str, dict[str, str]],
    ) -> dict[str, Any]:
        """Plot competitors on a 2D positioning map.

        Common axes: price vs. quality, feature breadth vs. focus,
        geographic reach vs. depth, enterprise vs. consumer.
        Identifies white space (no competitor) and competitive density
        (many competitors clustered).
        """
        matrix_summary = json.dumps(competitor_matrix, indent=2)[:3000]

        prompt = (
            "You are the Competitive Intelligence analyst creating a positioning map.\n\n"
            f"Competitors: {', '.join(competitors[:10])}\n\n"
            f"Competitor matrix:\n{matrix_summary}\n\n"
            "Create a 2D positioning map:\n"
            "1. Choose the two most strategically meaningful axes (e.g., price vs. quality, "
            "feature breadth vs. focus, enterprise vs. consumer)\n"
            "2. Plot each competitor on the map (x, y coordinates on a 1-10 scale)\n"
            "3. Identify white space areas (quadrants with no competitors)\n"
            "4. Identify competitive density zones (quadrants with 3+ competitors)\n\n"
            "Return JSON:\n"
            "{\n"
            '  "x_axis": "axis name",\n'
            '  "y_axis": "axis name",\n'
            '  "competitor_positions": [{"name": "...", "x": number, "y": number}],\n'
            '  "white_space": ["quadrant description 1", ...],\n'
            '  "density_zones": ["quadrant description 1", ...]\n'
            "}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return {}

        try:
            data = json.loads(response.content)
            if not isinstance(data, dict):
                return {}
            return {str(key): value for key, value in data.items()}
        except (json.JSONDecodeError, ValueError):
            return {}

    # ─────────────────────────────────────────────────────────────────────
    # Step 8: Identify white space
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_white_space(
        self,
        positioning_map: dict[str, Any],
        strategic_groups: list[str],
        competitor_matrix: dict[str, dict[str, str]],
    ) -> list[str]:
        """Identify white space opportunities from positioning map and groups.

        White space = where no competitor is currently playing. These are
        potential opportunities for differentiation or new market creation.
        """
        if not positioning_map:
            return []

        raw_white_space = positioning_map.get("white_space", [])
        white_space = (
            [item for item in raw_white_space if isinstance(item, str)]
            if isinstance(raw_white_space, list)
            else []
        )

        # Also check for open strategic groups (less than 3 members)
        for group in strategic_groups:
            if "open" in group.lower():
                white_space.append(f"Open strategic group: {group}")

        # Use LLM to synthesize and prioritize white space opportunities
        prompt = (
            "You are the Competitive Intelligence analyst identifying white space.\n\n"
            f"Positioning map white space: {json.dumps(white_space)}\n"
            f"Strategic groups: {json.dumps(strategic_groups)}\n\n"
            "Prioritize the top 3-5 white space opportunities based on:\n"
            "1. Market size potential (is the white space big enough to matter?)\n"
            "2. Strategic fit (can we realistically play there?)\n"
            "3. Competitive barrier (how hard is it to enter?)\n"
            "4. Timing (is the market ready for this positioning?)\n\n"
            "Return JSON: {\"white_space\": [\"opportunity1\", \"opportunity2\", ...]}\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return white_space

        try:
            data = json.loads(response.content)
            if not isinstance(data, dict):
                return white_space
            prioritized = data.get("white_space", white_space)
            if not isinstance(prioritized, list):
                return white_space
            return [item for item in prioritized if isinstance(item, str)]
        except (json.JSONDecodeError, ValueError):
            return white_space

    # ─────────────────────────────────────────────────────────────────────
    # Pricing trend analysis (from Wayback data)
    # ─────────────────────────────────────────────────────────────────────

    async def _analyze_pricing_trends(
        self,
        competitors: list[str],
        historical_snapshots: dict[str, list[dict[str, Any]]],
    ) -> list[KeyFinding]:
        """Analyze historical pricing trends from Wayback snapshots.

        Cross-references current pricing with historical pricing to show
        trends, not just current prices. Shows whether a competitor is
        raising prices (confidence in value), lowering prices (desperation
        or scale advantage), or holding steady.
        """
        if not historical_snapshots:
            return []

        snapshots_summary = ""
        for comp, snaps in historical_snapshots.items():
            for snap in snaps:
                snapshots_summary += (
                    f"\n{comp} ({snap.get('timestamp', 'unknown')}): "
                    f"{str(snap.get('content', ''))[:300]}\n"
                )

        prompt = (
            "You are the Competitive Intelligence analyst analyzing pricing trends.\n\n"
            f"Historical snapshots:\n{snapshots_summary[:3000]}\n\n"
            "Analyze pricing trends for each competitor:\n"
            "1. What was their pricing 1 year ago? 2 years ago? 5 years ago?\n"
            "2. Has pricing increased, decreased, or stayed stable?\n"
            "3. What does the trend signal? (raising prices = confidence/value; "
            "lowering = desperation or scale advantage; stable = market equilibrium)\n"
            "4. Any major pricing model changes? (e.g., freemium → paid, "
            "subscription → usage-based)\n\n"
            "Return JSON array:\n"
            "[{\n"
            '  "competitor": "name",\n'
            '  "current_pricing": "...",\n'
            '  "historical_pricing": "1y ago: ..., 2y ago: ...",\n'
            '  "trend": "increasing|decreasing|stable",\n'
            '  "signal": "what this trend means strategically",\n'
            '  "model_changes": "any pricing model changes"\n'
            "}]\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.NORMAL,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        pricing_findings: list[KeyFinding] = []

        if not response.success or not response.content:
            return pricing_findings

        try:
            data = json.loads(response.content)
            trend_list = data.get("trends", data) if isinstance(data, dict) else data
            if not isinstance(trend_list, list):
                trend_list = []

            for trend in trend_list:
                competitor = trend.get("competitor", "Unknown")
                pricing_findings.append(KeyFinding(
                    id=f"finding_{uuid.uuid4().hex[:8]}",
                    agent=self.name.value,
                    finding_type="pricing_trend",
                    title=f"Pricing Trend, {competitor}",
                    content=(
                        f"Current: {trend.get('current_pricing', 'Unknown')}. "
                        f"Historical: {trend.get('historical_pricing', 'Unknown')}. "
                        f"Trend: {trend.get('trend', 'Unknown')}. "
                        f"Signal: {trend.get('signal', 'Unknown')}. "
                        f"Model changes: {trend.get('model_changes', 'None')}"
                    ),
                    confidence=ConfidenceLevel.MEDIUM,
                    implications=trend.get("signal", ""),
                    sources=[s for s in self._sources if "wayback" in s.url.lower() or "archive" in s.url.lower()][:2],
                ))

        except (json.JSONDecodeError, ValueError):
            pass

        return pricing_findings

    # ─────────────────────────────────────────────────────────────────────
    # Sub-agent spawning for parallel competitor data collection
    # ─────────────────────────────────────────────────────────────────────
    #
    # The rewritten per-competitor depth sub-agent spawner lives in Step 2
    # (see _spawn_competitor_sub_agents above). It spawns one isolated
    # CompetitorDossier sub-agent per competitor (up to 5) instead of the old
    # fixed 3-sub-agent scrape pattern, and returns (dossiers, gaps).

    # ─────────────────────────────────────────────────────────────────────
    # Confidence calibration
    # ─────────────────────────────────────────────────────────────────────

    def _calibrate_confidence(
        self,
        competitors_found: int,
        sources_count: int,
        scraped_pages_count: int,
        has_historical_data: bool,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on data quality.

        HIGH: 5+ competitors found, 5+ sources, scraped pages for most
              competitors, historical data available
        MEDIUM: 3+ competitors, 3+ sources, some scraped pages
        LOW: <3 competitors, <3 sources, minimal scraping
        """
        if competitors_found >= 5 and sources_count >= 5 and scraped_pages_count >= 10 and has_historical_data:
            return ConfidenceLevel.HIGH
        if competitors_found >= 3 and sources_count >= 3 and scraped_pages_count >= 5:
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
    ) -> CompetitiveLandscape:
        """Execute the rewritten Competitive Intelligence methodology (§4.4).

        Pipeline (replaces the fragile two-stage "plan queries -> web search ->
        strict integer-ID judge" collapse vector):
        1. Resolve the arena (sector + region + engagement entity).
        2. STRONG-tier LLM names the 3-5 most DIRECT competitors (retried once).
        3. One isolated sub-agent per competitor builds a CompetitorDossier.
        4. Build competitor matrix from dossiers (pure aggregation).
        5. Assess moats from dossiers (pure aggregation, optional light synthesis).
        6. Create strategic group map.
        7. Create positioning map.
        8. Identify white space opportunities.
        """
        self._question = question or self._question
        self._engagement_id = engagement_id or self._engagement_id
        self._context = context or self._context

        # Subscribe to bus, specialists need findings + requests
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting competitive intelligence: {self._question[:80]}",
        )

        # Step 1: Resolve the arena (sector + region + engagement entity)
        arena = self._resolve_arena()
        self._arena = arena
        sector, region = arena["sector"], arena["region"]

        # Step 1 (linchpin): STRONG-tier competitor naming
        await self._transition(AgentState.WORKING, "Step 1: Naming direct competitors (STRONG tier)")
        named = await self._name_competitors(arena)
        self._competitor_names = [c["name"] for c in named]

        if not self._competitor_names:
            # True naming failure (transport/LLM). ONLY here is "zero
            # competitors" an error. A thin market is NOT a gap (see below).
            await self._escalate(
                issue="Competitor naming returned no companies (STRONG tier call failed)",
                suggested_action="Proceed with degraded analysis; flag data gap in report",
            )
            gap_finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="competitive_gap",
                title="Competitive analysis gap — no competitors named",
                content=(
                    f"STRONG-tier naming could not identify any direct competitors for "
                    f"'{self._question[:120]}' in sector '{sector}'"
                    f"{(' region ' + region) if region else ''}. "
                    f"This is a transport/LLM failure, not a thin market."
                ),
                confidence=ConfidenceLevel.LOW,
                sources=self._sources[:3],
            )
            await self._publish_finding(gap_finding)
            return CompetitiveLandscape(
                competitors=[gap_finding],
                competitor_matrix={},
                confidence=ConfidenceLevel.LOW,
                sources=self._sources,
            )

        # Thin market handling: fewer than 3 is a white-space signal, not a gap.
        if len(self._competitor_names) < 3:
            await self._transition(
                AgentState.WORKING,
                f"Thin market: only {len(self._competitor_names)} direct competitor(s) named — "
                f"treating as white-space candidate",
            )

        # Step 2: one isolated depth sub-agent per competitor (up to 5)
        await self._transition(
            AgentState.SUB_AGENT_SPAWNED,
            f"Spawning {min(len(named), 5)} competitor dossier sub-agents",
        )
        dossiers, gaps = await self._spawn_competitor_sub_agents(named, sector, region)
        self._competitor_dossiers = dossiers
        self._sub_agent_findings = gaps

        # Index websites for Wayback and collect dossier sources
        for d in dossiers:
            if d.website:
                self._competitor_urls[d.name] = d.website
            for url in d.evidence_urls:
                self._sources.append(Source(
                    id=f"src_{len(self._sources):03d}",
                    title=f"{d.name} evidence",
                    url=url,
                    credibility=SourceCredibility.NEWS,
                ))

        # Step 3: Pull historical snapshots (Wayback) for dossier websites
        await self._transition(AgentState.WORKING, "Step 3: Pulling historical snapshots (Wayback)")
        await self._pull_historical_snapshots(self._competitor_names)

        # Step 4: Build competitor matrix from dossiers (pure aggregation)
        await self._transition(AgentState.WORKING, "Step 4: Building competitor matrix")
        competitor_matrix = self._build_matrix_from_dossiers(dossiers, region)

        # Step 5: Assess moats from dossiers (pure aggregation) + optional synthesis
        await self._transition(AgentState.WORKING, "Step 5: Assessing moats (Hamilton Helmer)")
        moat_assessments = self._build_moats_from_dossiers(dossiers)
        if not moat_assessments and dossiers:
            # Fall back to the LLM synthesis fed by the matrix when dossiers
            # carried no explicit moat signals.
            moat_assessments = await self._assess_moats(
                self._competitor_names[:5], competitor_matrix, {},
            )

        # Step 6/7/8: strategic group map, positioning map, white space (LLM synthesis)
        await self._transition(AgentState.WORKING, "Step 6: Creating strategic group map")
        strategic_groups = await self._create_strategic_group_map(
            self._competitor_names, competitor_matrix,
        )
        await self._transition(AgentState.WORKING, "Step 7: Creating positioning map")
        positioning_map = await self._create_positioning_map(
            self._competitor_names, competitor_matrix,
        )
        await self._transition(AgentState.WORKING, "Step 8: Identifying white space")
        white_space = await self._identify_white_space(
            positioning_map, strategic_groups, competitor_matrix,
        )
        # A thin competitive field is itself a white-space signal.
        if len(self._competitor_names) < 3:
            white_space = [
                f"Thin competitive field ({len(self._competitor_names)} direct competitor(s) "
                f"named) — potential white-space / nascent-market opportunity."
            ] + white_space

        # Analyze pricing trends from Wayback snapshots + dossier pricing
        await self._transition(AgentState.WORKING, "Analyzing pricing trends from historical data")
        pricing_trends = await self._analyze_pricing_trends(
            self._competitor_names, self._historical_snapshots,
        )

        # Build competitor profiles as KeyFindings from matrix (fallback to name)
        competitor_findings: list[KeyFinding] = []
        for comp in self._competitor_names:
            matrix_data = competitor_matrix.get(comp, {})
            if matrix_data:
                profile_content = (
                    f"Features: {matrix_data.get('product_features', 'Unknown')}. "
                    f"Pricing: {matrix_data.get('pricing', 'Unknown')}. "
                    f"Target: {matrix_data.get('target_customer', 'Unknown')}. "
                    f"Geography: {matrix_data.get('geographic_coverage', 'Unknown')}. "
                    f"Funding: {matrix_data.get('funding_stage', 'Unknown')}. "
                    f"Headcount: {matrix_data.get('headcount', 'Unknown')}. "
                    f"Partnerships: {matrix_data.get('key_partnerships', 'Unknown')}."
                )
            else:
                profile_content = (
                    f"Named as a direct competitor but no dossier was collected "
                    f"(its sub-agent may have failed or returned no validated data)."
                )
            competitor_findings.append(KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="competitor_profile",
                title=f"Competitor Profile, {comp}",
                content=profile_content,
                confidence=ConfidenceLevel.MEDIUM,
                sources=[s for s in self._sources if comp.lower() in s.title.lower()][:3],
            ))

        # Calibrate confidence (dossier evidence count replaces scraped-page count)
        dossier_evidence = sum(len(d.evidence_urls) for d in dossiers)
        confidence = self._calibrate_confidence(
            competitors_found=len(self._competitor_names),
            sources_count=len(self._sources),
            scraped_pages_count=dossier_evidence,
            has_historical_data=bool(self._historical_snapshots),
        )

        # Everything the Synthesis Lead / Fact Checker should see.
        all_findings = competitor_findings + moat_assessments + pricing_trends + gaps

        landscape = CompetitiveLandscape(
            competitors=all_findings,
            competitor_matrix=competitor_matrix,
            moat_assessments=moat_assessments,
            strategic_groups=strategic_groups,
            positioning_map=positioning_map,
            white_space=white_space,
            pricing_trends=pricing_trends,
            confidence=confidence,
            sources=self._sources,
        )

        # Publish findings to bus for Synthesis Lead and Fact Checker
        for finding in all_findings:
            await self._publish_finding(finding)

        # Publish the full CompetitiveLandscape as a finding
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "competitive_landscape": landscape.model_dump(),
                "competitor_count": len(self._competitor_names),
                "white_space_count": len(white_space),
                "confidence": confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Competitive analysis complete: {len(self._competitor_names)} competitors, "
            f"{len(white_space)} white space opportunities, confidence={confidence.value}",
        )

        return landscape
