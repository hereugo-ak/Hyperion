"""
HYPERION Competitive Intelligence, Agent 4, the competitor profiling specialist.

This is NOT a generic "list the competitors" agent. This is a specialist
with proprietary analytical frameworks:

- Competitor matrix: Structured comparison across 7 dimensions
- Strategic group mapping: Cluster competitors into direct vs. adjacent rivals
- Market share analysis: Revenue/customer/search/download-based with confidence
- Moat assessment: Hamilton Helmer 7-force framework, scored strong→nascent
- Positioning map: 2D plot to identify white space and competitive density

It uses Obscura's stealth mode because competitor sites actively block bots.
It cross-references current pricing with Wayback historical pricing to show
pricing trends, not just current prices. It doesn't just list competitors, it maps their moats and identifies which are defensible vs. eroding. It
always identifies white space, where no competitor is currently playing.
(§4.4, Agent 4)

Model Tier: STANDARD
Tools: SearxNG, Jina, Obscura, Wayback
Sub-agents: Max 3, scrape competitor pricing pages, find funding/headcount
Output: CompetitiveLandscape (competitor matrix, moat assessments, strategic
        groups, positioning map, white space, pricing trends, confidence, sources)

Methodology (§4.4, Agent 4):
1. Identify all competitors in the space (SearxNG)
2. Scrape each competitor's website for product/pricing/team info (Obscura)
3. Pull historical snapshots for trend analysis (Wayback)
4. Build competitor matrix
5. Assess moats for top 5 competitors
6. Create strategic group map
7. Create positioning map
8. Identify white space opportunities
"""

from __future__ import annotations

import asyncio
import json
import logging
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
    CompetitiveLandscape,
    ConfidenceLevel,
    KeyFinding,
    Source,
    SourceCredibility,
)
from hyperion.tools.query_utils import resolve_subject

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


COMPETITIVE_INTEL_SPEC = AgentSpec(
    name=AgentName.COMPETITIVE_INTEL,
    role=AgentRole.SPECIALIST,
    display_name="Competitive Intelligence",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.UNIFIED_EXTRACT,
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
        "You can spawn up to 3 sub-agents for parallel competitor data collection:\n"
        "- Sub-agent A: Scrape [competitor1] pricing page (MICRO, Obscura)\n"
        "- Sub-agent B: Scrape [competitor2] pricing page (MICRO, Obscura)\n"
        "- Sub-agent C: Find [competitor3] funding/headcount (FAST, SearxNG + Jina)\n\n"
        "Your output is a CompetitiveLandscape Pydantic model, structured, not free text."
    ),
    spawn_condition="Spawned when the question involves competitive analysis, market entry, "
                     "or positioning (GO_NO_GO, MARKET_ENTRY, COMPARISON types)",
    max_sub_agents=3,
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

        # Collected raw data
        self._competitor_names: list[str] = []
        self._llm_competitor_candidates: list[dict[str, Any]] = []  # Stage-A model-knowledge names
        self._competitor_urls: dict[str, str] = {}  # name → website URL
        self._scraped_pages: dict[str, dict[str, Any]] = {}  # name → scraped data
        self._extracted_content: dict[str, str] = {}  # name → extracted text
        self._historical_snapshots: dict[str, list[dict[str, Any]]] = {}  # name → snapshots
        self._search_results: list[dict[str, Any]] = []

        # Collected sources
        self._sources: list[Source] = []

        # Sub-agent findings
        self._sub_agent_findings: list[KeyFinding] = []
        # P-CORE: reconciled substantive sub-agent findings (published so sub-agent
        # evidence reaches the report, not just the parent's own analysis).
        self._sub_agent_reconciled: list[KeyFinding] = []
        # P-CORE: numeric contradictions surfaced between sub-agent findings.
        self._sub_agent_contradictions: list[str] = []

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
                # Pre-seeded competitor names from Engagement Director
                self._competitor_names = context_bundle.get("competitors", [])

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
    # Step 1: Identify all competitors (SearxNG)
    # ─────────────────────────────────────────────────────────────────────

    async def _plan_competitor_searches(self, market_query: str) -> dict[str, Any]:
        """Use the LLM to translate any engagement into focused search queries."""
        context_summary = json.dumps(self._context, default=str)[:3000]
        prompt = (
            "You are planning web research for a competitive-intelligence engagement.\n\n"
            f"User question: {self._question or market_query}\n"
            f"Resolved subject: {market_query}\n"
            f"Engagement context: {context_summary}\n\n"
            "Interpret the exact competitive arena dynamically. Identify the product or "
            "service boundary, customer/job-to-be-done, value-chain position, and any "
            "geographic constraints. Then create 4-7 high-precision web search queries "
            "that will find direct and genuinely adjacent competitors. Each query must "
            "carry enough semantic context to disambiguate the arena. Avoid generic "
            "country-wide company lists and do not assume a particular industry.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "subject": "precise competitive arena",\n'
            '  "inclusion_criteria": ["..."],\n'
            '  "exclusion_criteria": ["..."],\n'
            '  "queries": ["...", "..."]\n'
            "}\n"
        )
        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.2,
            response_format={"type": "json_object"},
        )

        fallback_subject = market_query or self._question
        fallback = {
            "subject": fallback_subject,
            "inclusion_criteria": [],
            "exclusion_criteria": [],
            "queries": [f"{fallback_subject} direct competitors"],
        }
        if not response.success or not response.content:
            return fallback

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError, TypeError):
            return fallback
        if not isinstance(data, dict):
            return fallback

        queries = data.get("queries")
        clean_queries = list(dict.fromkeys(
            query.strip()
            for query in queries
            if isinstance(query, str) and query.strip()
        )) if isinstance(queries, list) else []
        if not clean_queries:
            return fallback

        subject = data.get("subject")
        inclusion = data.get("inclusion_criteria")
        exclusion = data.get("exclusion_criteria")
        return {
            "subject": subject.strip() if isinstance(subject, str) and subject.strip() else fallback_subject,
            "inclusion_criteria": [
                item for item in inclusion if isinstance(item, str)
            ] if isinstance(inclusion, list) else [],
            "exclusion_criteria": [
                item for item in exclusion if isinstance(item, str)
            ] if isinstance(exclusion, list) else [],
            "queries": clean_queries[:7],
        }

    def _arena_class(self) -> str:
        """A1: the entity class the COMPETITIVE ARENA operates on.

        The Director's ``subject_class`` is what the question is ABOUT (India =
        NATION_OR_REGION), but the competitive arena can be a different class
        (a country's SPACE STARTUPS = COMPANY). COMPETE needs the arena class to
        shape discovery queries. Defaults to the Director's subject_class when
        no arena refinement is available.
        """
        arena = str((self._context or {}).get("competitive_arena_class", "") or "").strip()
        if arena:
            return arena
        return str((self._context or {}).get("subject_class", "") or "company").strip()

    def _build_discovery_queries(self, arena: str) -> list[str]:
        """A2: entity-class-correct discovery queries.

        The same arena needs DIFFERENT queries depending on what kind of entity
        we are profiling: a country's players, a company's rivals, a
        technology's vendors, a market's key players. The old code always asked
        "<subject> direct competitors", which is wrong for a NATION_OR_REGION
        arena (nobody calls countries "competitors" in search).
        """
        geo = str(self._context.get("geography") or self._context.get("jurisdiction") or "").strip()
        geo_qualifier = f" {geo}" if geo else ""
        arena_class = (self._arena_class() or "company").lower()

        if "nation" in arena_class or "region" in arena_class:
            # A country/region's players, not "competitors of a country".
            base = [
                f"{arena}{geo_qualifier} companies startups players".strip(),
                f"top {arena} companies in {geo}".strip() if geo else f"top {arena} companies",
                f"{arena}{geo_qualifier} industry landscape leading firms".strip(),
                f"{arena}{geo_qualifier} public private players".strip(),
            ]
        elif "technology" in arena_class:
            base = [
                f"{arena} vendors providers manufacturers",
                f"leading {arena} technology providers",
                f"{arena} solution suppliers market leaders",
            ]
        elif "market" in arena_class:
            base = [
                f"{arena} key players market share",
                f"leading companies in {arena} market",
                f"{arena} market competitors leaders",
            ]
        elif "person" in arena_class or "org" in arena_class:
            base = [
                f"{arena} competitors alternatives",
                f"organizations competing with {arena}",
            ]
        else:  # company / default
            base = [
                f"{arena} competitors alternatives rivals",
                f"top {arena} companies in {geo}".strip() if geo else f"top {arena} companies",
                f"{arena} competitive landscape key players",
                f"who competes with {arena}",
            ]
        return list(dict.fromkeys(q.strip() for q in base if q.strip()))[:6]

    async def _discover_competitors_llm(self, arena: str) -> list[dict[str, Any]]:
        """A3 Stage A: model-knowledge competitor discovery (Mistral STRONG).

        NOT gated on search. A STRONG-tier (Mistral Large) call names 3-5
        concrete entities for the arena from the model's own knowledge and any
        grounding, even when the live web pool returns zero results. The
        output is later search-validated (Stage B) so provenance stays bound.
        """
        from hyperion.schemas.workflow import SubjectClass

        subject_class = (self._arena_class() or "company").lower()
        arena_class_name = {
            "nation_or_region": SubjectClass.NATION_OR_REGION.value,
            "technology": SubjectClass.TECHNOLOGY.value,
            "market": SubjectClass.MARKET.value,
            "person_or_org": SubjectClass.PERSON_OR_ORG.value,
            "policy": SubjectClass.POLICY.value,
        }.get(subject_class, SubjectClass.COMPANY.value)

        geo = str(self._context.get("geography") or self._context.get("jurisdiction") or "").strip()
        prompt = (
            "You are a competitive-intelligence analyst naming the KEY PLAYERS in a "
            "precise arena. The arena's entity class is: "
            f"{arena_class_name.upper()}.\n\n"
            f"User question: {self._question}\n"
            f"Arena / subject: {arena}\n"
            f"Geography: {geo or 'global/unspecified'}\n\n"
            "Name 3-5 concrete, real organizations that are the most relevant "
            "players in THIS arena. The entity class matters:"
            "\n- if NATION_OR_REGION: name the country's companies / startups / "
            "agencies / players IN that sector (NOT 'the country as a competitor')."
            "\n- if COMPANY: name its direct and adjacent competitors."
            "\n- if TECHNOLOGY: name the vendors / providers / manufacturers."
            "\n- if MARKET: name the key players and their market role."
            "\n- if PERSON_OR_ORG: name the rivals or alternatives.\n\n"
            "Prefer organizations with verifiable public presence. Do NOT invent "
            "entities; if you cannot name real ones, return an empty list. Every "
            "name must be a specific organization, never a category.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "competitors": [\n'
            '    {"name": "organization name", "arena_role": "why it is a key player"}\n'
            "  ]\n"
            "}\n"
        )
        # Stage A escalates to STRONG (Mistral Large leads the STRONG tier).
        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.1,
            response_format={"type": "json_object"},
            tier=ModelTier.STRONG,
        )
        if not response.success or not response.content:
            return []

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError, TypeError):
            return []

        competitors = []
        seen: set[str] = set()
        for candidate in (data.get("competitors") or []) if isinstance(data, dict) else []:
            if not isinstance(candidate, dict):
                continue
            name = candidate.get("name")
            role = candidate.get("arena_role")
            if not isinstance(name, str) or not name.strip():
                continue
            key = name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            competitors.append({
                "name": name.strip(),
                "arena_role": role if isinstance(role, str) and role.strip() else "",
            })
        return competitors[:6]

    async def _identify_competitors(self, market_query: str) -> list[dict[str, Any]]:
        """A2/A3: entity-aware two-stage competitor discovery.

        Stage A: a STRONG-tier (Mistral Large) call names 3-5 concrete entities
        from model knowledge — NOT gated on the live web pool.
        Stage B: the search-validated pass binds each name to real URLs via
        entity-class-correct queries, so provenance stays ledger-bound.

        The zero-competitor gap is now unreachable unless BOTH stages fail —
        a real total outage the corpus preflight handles.
        """
        subject = resolve_subject(
            self._context, "company", "sector", "industry", question=self._question
        ) or market_query
        arena = subject

        # ── Stage A: model-knowledge discovery (always runs) ───────────────
        llm_candidates = await self._discover_competitors_llm(arena)
        self._llm_competitor_candidates = llm_candidates
        if llm_candidates:
            self._log(
                f"COMPETE Stage A: model-knowledge discovery named {len(llm_candidates)} candidate(s)",
            )

        # ── Stage B: search validation + URL binding ───────────────────────
        # Entity-class-correct queries (country's players, company rivals,
        # technology vendors, ...) instead of the old generic "direct
        # competitors" that returned nothing for a NATION_OR_REGION arena.
        queries = self._build_discovery_queries(arena)
        results: list[dict[str, Any]] = []
        try:
            searxng = self.get_tool(ToolName.SEARXNG)
            for pattern in queries:
                try:
                    search_results = await searxng.search(pattern, max_results=10)
                except Exception as exc:  # noqa: BLE001 - one bad query must not kill discovery
                    logger.warning("COMPETE search '%s' failed: %s", pattern, exc)
                    continue
                for row in search_results:
                    results.append({
                        "result_id": len(results),
                        "title": row.get("title", ""),
                        "url": row.get("url", ""),
                        "snippet": row.get("content", ""),
                        "query": pattern,
                    })
        except (ValueError, AttributeError, RuntimeError) as exc:
            logger.warning("COMPETE search layer unavailable: %s", exc)

        # Stage B judge: reconcile search evidence against the arena; the
        # model-knowledge candidates are pre-seeded so even thin search can
        # confirm them.
        competitors, evidence_ids = await self._extract_competitor_names(plan=None, search_results=results)

        # If search-validated names are empty but Stage A named real entities,
        # fall back to the model-knowledge set (typed LOW confidence upstream).
        if not competitors and llm_candidates:
            competitors = [c["name"] for c in llm_candidates]
            self._log(
                f"COMPETE Stage B: search yielded no citable rows — using "
                f"{len(competitors)} model-knowledge candidate(s); provenance will be "
                f"search-bound downstream or typed unverified",
            )
        self._competitor_names = competitors

        relevant_results = [
            result for result in results if result["result_id"] in evidence_ids
        ]
        for result in relevant_results:
            self._sources.append(Source(
                id=f"src_{len(self._sources):03d}",
                title=result["title"],
                url=result["url"],
                credibility=SourceCredibility.NEWS,
            ))
        return relevant_results

    async def _extract_competitor_names(
        self,
        plan: dict[str, Any] | None,
        search_results: list[dict[str, Any]],
    ) -> tuple[list[str], set[int]]:
        """Use an LLM semantic judge to select competitors and cite evidence rows."""
        search_summary = "\n".join(
            f"[{result['result_id']}] {result['title']}: "
            f"{result.get('snippet', '')[:300]} ({result.get('url', '')})"
            for result in search_results[:60]
        )
        # A3: seed the judge with Stage-A model-knowledge candidates so even a
        # thin search can confirm/rank them instead of returning nothing.
        seed_candidates = [
            (c.get("name") if isinstance(c, dict) else c)
            for c in getattr(self, "_llm_competitor_candidates", []) or []
            if isinstance(c, dict) and c.get("name")
        ]
        prompt = (
            "You are the senior semantic relevance judge for competitive intelligence.\n\n"
            f"Original user question: {self._question}\n"
            f"Research plan: {json.dumps(plan or {}, default=str)}\n"
            f"Director-suggested candidates: {json.dumps(self._competitor_names)}\n"
            f"Model-knowledge candidate names: {json.dumps(seed_candidates)}\n\n"
            f"Numbered search results:\n{search_summary}\n\n"
            "Determine which organizations are direct competitors or strategically "
            "relevant adjacent competitors in the precise arena defined by the user. "
            "Reason from business model, offering, customer need, and value-chain role—not "
            "mere keyword or geographic overlap. Reject unrelated organizations and generic "
            "listicle entries. Every accepted organization must cite one or more numbered "
            "results that support both its identity and relevance. If evidence is weak, omit "
            "the organization. Do not use industry-specific assumptions.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "competitors": [{\n'
            '    "name": "organization name",\n'
            '    "evidence_result_ids": [0],\n'
            '    "relevance": "why it competes in this precise arena"\n'
            "  }]\n"
            "}\n"
        )
        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.1,
            response_format={"type": "json_object"},
        )
        if not response.success or not response.content:
            return ([], set())

        try:
            data = json.loads(response.content)
        except (json.JSONDecodeError, ValueError, TypeError):
            return ([], set())
        if not isinstance(data, dict) or not isinstance(data.get("competitors"), list):
            return ([], set())

        valid_ids = {result["result_id"] for result in search_results}
        names: list[str] = []
        evidence_ids: set[int] = set()
        seen: set[str] = set()
        for candidate in data["competitors"]:
            if not isinstance(candidate, dict):
                continue
            name = candidate.get("name")
            relevance = candidate.get("relevance")
            raw_ids = candidate.get("evidence_result_ids")
            cited_ids = {
                result_id
                for result_id in raw_ids
                if isinstance(result_id, int) and result_id in valid_ids
            } if isinstance(raw_ids, list) else set()
            if not isinstance(name, str) or not name.strip() or not cited_ids:
                continue
            if not isinstance(relevance, str) or not relevance.strip():
                continue
            key = name.strip().casefold()
            if key in seen:
                continue
            seen.add(key)
            names.append(name.strip())
            evidence_ids.update(cited_ids)

        return (names, evidence_ids)

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Scrape competitor websites (Obscura + Jina)
    # ─────────────────────────────────────────────────────────────────────

    async def _scrape_competitor_sites(self, competitors: list[str]) -> None:
        """Scrape each competitor's website for product/pricing/team info.

        OVERHAUL5 W5 (D-07) + W7 (D-12): routed through the single
        UnifiedExtract ladder (page-aware: paywall/PDF/media/js_heavy
        profiles, availability-probed tiers) instead of the bespoke
        obscura->jina loop whose ``content`` binding crashed exactly when
        Obscura succeeded (UnboundLocalError, 08-12 run 14:56:52) and whose
        tool grants were partial.
        """
        try:
            extractor = self.get_tool(ToolName.UNIFIED_EXTRACT)
        except (ValueError, AttributeError, RuntimeError):
            return

        for competitor in competitors[:10]:  # Limit to 10 competitors
            # First, find the competitor's website URL
            website_url = await self._find_competitor_website(competitor)
            if not website_url:
                continue
            self._competitor_urls[competitor] = website_url

            # Scrape key pages: homepage, pricing, product, about/team
            pages_to_scrape = [
                ("homepage", website_url),
                ("pricing", f"{website_url}/pricing"),
                ("product", f"{website_url}/product"),
                ("about", f"{website_url}/about"),
            ]

            for page_type, url in pages_to_scrape:
                try:
                    hits = await extractor.extract([url], query=competitor)
                except (ValueError, AttributeError, RuntimeError):
                    hits = []
                if not hits:
                    continue
                content = hits[0]["content"]
                page_data = {"content": content[:15000]}
                if competitor not in self._scraped_pages:
                    self._scraped_pages[competitor] = {}
                self._scraped_pages[competitor][page_type] = page_data

                self._sources.append(Source(
                    id=f"src_{len(self._sources):03d}",
                    title=f"{competitor}, {page_type} page",
                    url=url,
                    credibility=SourceCredibility.BLOG,
                    key_data=content[:500],
                ))

    async def _find_competitor_website(self, competitor_name: str) -> str:
        """Find a competitor's website URL using SearxNG."""
        try:
            searxng = self.get_tool(ToolName.SEARXNG)
            results = await searxng.search(f"{competitor_name} official website", max_results=3)
            for r in results:
                url = r.get("url", "")
                if isinstance(url, str) and url and not any(
                    blocked in url
                    for blocked in ["linkedin.com", "crunchbase.com", "bloomberg.com"]
                ):
                    return url
        except (ValueError, AttributeError, RuntimeError):
            pass
        return ""

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

    async def _build_competitor_matrix(
        self,
        competitors: list[str],
        scraped_data: dict[str, dict[str, Any]],
        search_results: list[dict[str, Any]],
    ) -> dict[str, dict[str, str]]:
        """Build a structured competitor comparison matrix.

        7 dimensions: product features, pricing, target customer, geographic
        coverage, funding stage, headcount, key partnerships.
        Each cell must cite a source.
        """
        # Prepare scraped data summary
        data_summary = ""
        for comp, pages in scraped_data.items():
            for page_type, page_data in pages.items():
                content = page_data.get("content", "")[:500] if isinstance(page_data, dict) else str(page_data)[:500]
                data_summary += f"\n{comp}, {page_type}: {content}\n"

        search_summary = "\n".join(
            f"- {r['title']}: {r.get('snippet', '')[:150]}"
            for r in search_results[:10]
        )

        prompt = (
            "You are the Competitive Intelligence analyst building a competitor matrix.\n\n"
            f"Competitors: {', '.join(competitors[:10])}\n\n"
            f"Scraped website data:\n{data_summary[:3000]}\n\n"
            f"Search results:\n{search_summary[:2000]}\n\n"
            "Build a competitor matrix with these 7 dimensions:\n"
            "1. Product features (key features, differentiation)\n"
            "2. Pricing (price range, model, subscription/one-time/usage-based)\n"
            "3. Target customer (SMB/mid-market/enterprise, industry vertical)\n"
            "4. Geographic coverage (regions/countries served)\n"
            "5. Funding stage (bootstrapped/seed/A/B/C/IPO/revenue-funded)\n"
            "6. Headcount (approximate employee count)\n"
            "7. Key partnerships (integrations, channel partners, alliances)\n\n"
            "For each cell, include the value and source. If unknown, say 'Unknown'.\n\n"
            "Return JSON:\n"
            "{\n"
            '  "competitor1": {\n'
            '    "product_features": "value [source]",\n'
            '    "pricing": "value [source]",\n'
            '    "target_customer": "value [source]",\n'
            '    "geographic_coverage": "value [source]",\n'
            '    "funding_stage": "value [source]",\n'
            '    "headcount": "value [source]",\n'
            '    "key_partnerships": "value [source]"\n'
            '  },\n'
            '  "competitor2": { ... }\n'
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
            data = json.loads(response.content)
            if not isinstance(data, dict):
                return {}
            return {
                str(competitor): {
                    str(dimension): str(value)
                    for dimension, value in details.items()
                }
                for competitor, details in data.items()
                if isinstance(details, dict)
            }
        except (json.JSONDecodeError, ValueError):
            return {}

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

    async def _spawn_competitor_sub_agents(
        self,
        competitors: list[str],
    ) -> list[KeyFinding]:
        """Spawn up to 3 sub-agents for parallel competitor data collection.

        Per §4.4, Agent 4:
        - Sub-agent A: Scrape [competitor1] pricing page (MICRO, Obscura)
        - Sub-agent B: Scrape [competitor2] pricing page (MICRO, Obscura)
        - Sub-agent C: Find [competitor3] funding/headcount (FAST, SearxNG + Jina)
        """
        if len(competitors) < 3:
            return []

        # F-0.1-4 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): scrape specs must
        # carry a discovery leg (SEARXNG + JINA) alongside the browser tier
        # (OBSCURA), and the parent passes the CONCRETE pricing page URL (not
        # the bare website root) so the sub-agent's F-0.1-1 fetch targets the
        # right page. Previously OBSCURA-only specs could never succeed: no
        # discovery, and the context URL (root) was never fetched.
        def _pricing_url(competitor: str) -> str:
            root = (self._competitor_urls or {}).get(competitor, "")
            return f"{root}/pricing" if root else ""

        sub_specs = [
            SubAgentSpec(
                question=f"Scrape {competitors[0]} pricing page, extract pricing tiers, features per tier, and any discounts",
                parent_agent=self.name,
                model_tier=ModelTier.STANDARD,
                tools=[ToolName.SEARXNG, ToolName.JINA, ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=600,
                context={"competitor": competitors[0], "url": _pricing_url(competitors[0])},
            ),
            SubAgentSpec(
                question=f"Scrape {competitors[1]} pricing page, extract pricing tiers, features per tier, and any discounts",
                parent_agent=self.name,
                model_tier=ModelTier.STANDARD,
                tools=[ToolName.SEARXNG, ToolName.JINA, ToolName.OBSCURA],
                findings_model="KeyFinding",
                timeout_seconds=600,
                context={"competitor": competitors[1], "url": _pricing_url(competitors[1])},
            ),
            SubAgentSpec(
                question=f"Find {competitors[2]} funding stage, total raised, headcount, and key investors",
                parent_agent=self.name,
                model_tier=ModelTier.STANDARD,
                tools=[ToolName.SEARXNG, ToolName.JINA],
                findings_model="KeyFinding",
                timeout_seconds=600,
                context={"competitor": competitors[2]},
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
        """Execute the Competitive Intelligence 8-step methodology.

        Steps (§4.4, Agent 4):
        1. Identify all competitors in the space (SearxNG)
        2. Scrape each competitor's website for product/pricing/team info (Obscura)
        3. Pull historical snapshots for trend analysis (Wayback)
        4. Build competitor matrix
        5. Assess moats for top 5 competitors
        6. Create strategic group map
        7. Create positioning map
        8. Identify white space opportunities
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

        # Step 1: Identify all competitors
        await self._transition(AgentState.WORKING, "Step 1: Identifying competitors (SearxNG)")
        self._search_results = await self._identify_competitors(self._question)

        if not self._competitor_names:
            await self._escalate(
                issue="No competitors identified from search, publishing gap finding",
                suggested_action="Proceed with degraded analysis; flag data gap in report",
            )
            gap_finding = KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="competitive_gap",
                title="Competitive analysis gap, no competitors identified",
                content=(
                    f"No competitors could be identified for the question: "
                    f"'{self._question[:120]}'. This is a data-availability gap. "
                    f"Sources checked: {len(self._sources)}."
                ),
                confidence=ConfidenceLevel.LOW,
                sources=self._sources[:3],
            )
            await self._publish_finding(gap_finding)
            return CompetitiveLandscape(
                competitors=[],
                competitor_matrix={},
                confidence=ConfidenceLevel.LOW,
                sources=self._sources,
            )

        # Step 2: Scrape competitor websites
        await self._transition(
            AgentState.WORKING,
            f"Step 2: Scraping {len(self._competitor_names)} competitor websites (Obscura)",
        )
        await self._scrape_competitor_sites(self._competitor_names)

        # Spawn sub-agents for parallel data collection
        await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning competitor data collection "
            "sub-agents")
        sub_findings = await self._spawn_competitor_sub_agents(self._competitor_names)
        # OVERHAUL2 S2: single ingestion path — merge/reconcile/contradiction/
        # publish all live in BaseAgent._ingest_sub_findings.
        await self._ingest_sub_findings(sub_findings)

        await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with analysis")

        # Step 3: Pull historical snapshots
        await self._transition(AgentState.WORKING, "Step 3: Pulling historical snapshots (Wayback)")
        await self._pull_historical_snapshots(self._competitor_names)

        # P3.3: Zero-evidence gate
        if await self._check_zero_evidence(f"no competitor data for {self._question[:60]}"):
            return CompetitiveLandscape(
                competitors=[],
                competitor_matrix={},
                confidence=ConfidenceLevel.LOW,
                sources=[],
            )

        # Step 4: Build competitor matrix
        await self._transition(AgentState.WORKING, "Step 4: Building competitor matrix")
        competitor_matrix = await self._build_competitor_matrix(
            self._competitor_names, self._scraped_pages, self._search_results,
        )

        # Step 5: Assess moats for top 5 competitors
        await self._transition(AgentState.WORKING, "Step 5: Assessing moats (Hamilton Helmer "
            "framework)")
        moat_assessments = await self._assess_moats(
            self._competitor_names[:5], competitor_matrix, self._scraped_pages,
        )

        # Step 6: Create strategic group map
        await self._transition(AgentState.WORKING, "Step 6: Creating strategic group map")
        strategic_groups = await self._create_strategic_group_map(
            self._competitor_names, competitor_matrix,
        )

        # Step 7: Create positioning map
        await self._transition(AgentState.WORKING, "Step 7: Creating positioning map")
        positioning_map = await self._create_positioning_map(
            self._competitor_names, competitor_matrix,
        )

        # Step 8: Identify white space
        await self._transition(AgentState.WORKING, "Step 8: Identifying white space opportunities")
        white_space = await self._identify_white_space(
            positioning_map, strategic_groups, competitor_matrix,
        )

        # Analyze pricing trends from Wayback data
        await self._transition(AgentState.WORKING, "Analyzing pricing trends from historical data")
        pricing_trends = await self._analyze_pricing_trends(
            self._competitor_names, self._historical_snapshots,
        )

        # Build competitor profiles as KeyFindings
        competitor_findings: list[KeyFinding] = []
        for comp in self._competitor_names[:10]:
            matrix_data = competitor_matrix.get(comp, {})
            competitor_findings.append(KeyFinding(
                id=f"finding_{uuid.uuid4().hex[:8]}",
                agent=self.name.value,
                finding_type="competitor_profile",
                title=f"Competitor Profile, {comp}",
                content=(
                    f"Features: {matrix_data.get('product_features', 'Unknown')}. "
                    f"Pricing: {matrix_data.get('pricing', 'Unknown')}. "
                    f"Target: {matrix_data.get('target_customer', 'Unknown')}. "
                    f"Geography: {matrix_data.get('geographic_coverage', 'Unknown')}. "
                    f"Funding: {matrix_data.get('funding_stage', 'Unknown')}. "
                    f"Headcount: {matrix_data.get('headcount', 'Unknown')}. "
                    f"Partnerships: {matrix_data.get('key_partnerships', 'Unknown')}."
                ),
                confidence=ConfidenceLevel.MEDIUM,
                sources=[s for s in self._sources if comp.lower() in s.title.lower()][:3],
            ))

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            competitors_found=len(self._competitor_names),
            sources_count=len(self._sources),
            scraped_pages_count=sum(len(pages) for pages in self._scraped_pages.values()),
            has_historical_data=bool(self._historical_snapshots),
        )

        # Produce CompetitiveLandscape model
        await self._transition(AgentState.WORKING, "Producing CompetitiveLandscape model")

        landscape = CompetitiveLandscape(
            competitors=competitor_findings,
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
        for finding in competitor_findings + moat_assessments + pricing_trends:
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
