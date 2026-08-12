"""
HYPERION Sub-Agent Runner, junior agent execution for context isolation.

This is NOT a generic sub-agent class. It is the mechanism that makes
HYPERION fundamentally different from a single-LLM system (§4.7).

A specialist hits a context window limit and needs deeper research.
Instead of truncating or compressing (which loses detail), the specialist
delegates: it sends a focused sub-question to a junior sub-agent, the
sub-agent does focused research in its own context window, and returns
structured findings (data, sources, confidence, gaps). The parent
synthesizes. The parent's context window is used for synthesis, not
for raw research.

This is how real consulting teams work, a partner doesn't read 200
pages of raw research. They read a senior associate's 5-page summary.
HYPERION's specialists are partners; sub-agents are associates.

Rules (§4.7):
- Max 3 sub-agents per specialist per engagement (enforced in BaseAgent)
- Sub-agents use STANDARD or higher tier so research has a large context window
- Sub-agent findings are structured (KeyFinding), not free text
- Parent specialist receives structured findings and synthesizes them
- Sub-agents have 5-minute timeout, if a sub-agent doesn't return in
  5 min, the parent proceeds with available findings and flags the gap
- Sub-agents have access to a subset of parent's tools (specified at
  spawn time)
- Sub-agents cannot spawn their own sub-agents (no recursive spawning)
- Sub-agent findings include: data, sources, confidence score, and gaps
  (what the sub-agent couldn't find)

Sub-agent lifecycle (§4.7):
    Specialist identifies sub-question
      → Creates SubAgent spec (question, tier, tools, findings_model)
      → SubAgent dispatched to LLMRouter with appropriate tier
      → SubAgent executes: searches → extracts → analyzes → produces findings
      → SubAgent returns structured findings to parent
      → Parent synthesizes sub-agent findings into its own analysis
      → Parent reports to Engagement Director
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from typing import Any

from hyperion.agents.bus import AgentBus, Channel, MessageType, get_bus
from hyperion.agents.prompt_contract import compose_agent_prompt
from hyperion.config import TIER_OUTPUT_BUDGET, ModelTier
from hyperion.router.budget import TaskUrgency
from hyperion.router.providers.base import RouterResponse
from hyperion.router.router import LLMRouter, get_router
from hyperion.schemas.agents import SubAgentSpec
from hyperion.schemas.models import (
    NON_SUBSTANTIVE_FINDING_TYPES,
    UNVERIFIED_ASSERTION_TYPE,
    EvidenceFinding,
    KeyFinding,
    ResearchOutcome,
    Source,
)
from hyperion.tools.content_selector import select_content
from hyperion.tools.evidence_ledger import (
    Evidence,
    content_hash_of,
    get_evidence_ledger,
    record_evidence,
)

logger = logging.getLogger(__name__)


class ResearchCounters:
    """F-01/F-07: machine-readable counters for one sub-agent research run.

    The audit's F-07 requires that no failure class disappears through a
    silent ``[]``. Every run publishes these counters so the parent (and the
    TUI) can distinguish raw results from extracted documents from valid
    findings from gaps without re-deriving them from list lengths.
    """

    __slots__ = (
        "raw_results",
        "extracted_documents",
        "valid_findings",
        "invalid_findings",
        "provider_failures",
        "gaps",
        "unverified_assertions",
        # F-0.1-5: 1 when the sufficiency gate failed (a pricing/fetch task
        # extracted content but found no pricing artifacts). Lets run() type
        # FETCH_INSUFFICIENT instead of a generic gap, and feeds the fallback
        # routes (F-0.1-6) rather than accepting thin extraction as success.
        "sufficiency_failed",
    )

    def __init__(self) -> None:
        self.raw_results = 0
        self.extracted_documents = 0
        self.valid_findings = 0
        self.invalid_findings = 0
        self.provider_failures = 0
        self.gaps = 0
        self.unverified_assertions = 0
        self.sufficiency_failed = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "raw_results": self.raw_results,
            "extracted_documents": self.extracted_documents,
            "valid_findings": self.valid_findings,
            "invalid_findings": self.invalid_findings,
            "provider_failures": self.provider_failures,
            "gaps": self.gaps,
            "unverified_assertions": self.unverified_assertions,
            "sufficiency_failed": self.sufficiency_failed,
        }

    def __repr__(self) -> str:
        return (
            f"ResearchCounters(raw={self.raw_results}, extracted="
            f"{self.extracted_documents}, valid={self.valid_findings}, "
            f"invalid={self.invalid_findings}, provider_failures="
            f"{self.provider_failures}, gaps={self.gaps}, "
            f"sufficiency_failed={self.sufficiency_failed})"
        )

# Fix 2.2 (§4.7 Finding B-6): the retained-content budget for a fetched SEC
# filing. Same number as before; what changed is that the budget is now filled
# by relevance to the sub-question instead of by position in the document.
SEC_FILING_BUDGET_CHARS = 15000


def _normalize_evidence_url(url: str) -> str:
    """Light URL canonicalization for evidence binding (P3).

    Drops the fragment, strips trailing slashes, lower-cases scheme/host and
    removes common tracking params, so the LLM's URL echo still binds to the
    ledger record. Never raises.
    """
    from urllib.parse import urlsplit, urlunsplit

    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urlsplit(url)
        scheme = (parts.scheme or "https").lower()
        host = (parts.hostname or "").lower()
        path = parts.path.rstrip("/")
        if parts.query:
            kept = [
                kv
                for kv in parts.query.split("&")
                if kv and not kv.lower().startswith(("utm_", "ref=", "source="))
            ]
            query = "&".join(kept)
        else:
            query = ""
        return urlunsplit((scheme, host, path, query, ""))
    except ValueError:
        return url.lower()


class SubAgentRunner:
    """Executes a single sub-agent research task and returns structured findings.

    This is NOT a full agent, it has no bus subscription, no state
    management, no sub-agent spawning capability. It is a focused
    research executor that:

    1. Takes a SubAgentSpec (question, tier, tools, findings_model)
    2. Constructs a system prompt appropriate for a junior researcher
    3. Uses the specified tools to gather data
    4. Calls the LLM at the specified tier (STANDARD or higher)
    5. Parses the response into structured KeyFinding objects
    6. Returns the findings to the parent specialist

    The parent specialist is responsible for:
    - Synthesizing sub-agent findings into its own analysis
    - Reporting to the Engagement Director via AgentBus
    - Flagging gaps (what the sub-agent couldn't find)

    The SubAgentRunner is responsible for:
    - Executing the research within its own context window
    - Producing structured findings (not free text)
    - Including confidence scores and gap identification
    - Respecting the 5-minute timeout (enforced by the parent via
      asyncio.wait_for in BaseAgent._spawn_sub_agent)

    Class-level defaults for ``outcome``/``counters`` let tests (and any
    caller) construct a runner via ``object.__new__`` without running
    ``__init__`` while still reading a deterministic, populated state.
    """

    # F-01/F-07: default outcome for __new__-constructed runners. ``counters``
    # defaults to None (never a shared mutable class instance) and is created
    # lazily on first use, so runners built via ``object.__new__`` never leak
    # counter state across tests/callers.
    outcome: ResearchOutcome = ResearchOutcome.NO_EVIDENCE
    counters: ResearchCounters | None = None

    def _ensure_counters(self) -> ResearchCounters:
        """Lazily create the per-run counter block.

        ``object.__new__``-constructed runners skip ``__init__``; they still
        get a fresh, deterministic ``ResearchCounters`` on first write instead
        of sharing one mutable class-level instance.
        """
        if self.counters is None:
            self.counters = ResearchCounters()
        return self.counters

    def __init__(
        self,
        spec: SubAgentSpec,
        bus: AgentBus | None = None,
        router: LLMRouter | None = None,
    ) -> None:
        self.spec = spec
        self.bus = bus or get_bus()
        self.router = router or get_router()

        # Research requires a large context window; MICRO/FAST models can
        # truncate the evidence bundle before analysis begins.
        if spec.model_tier not in (
            ModelTier.STANDARD,
            ModelTier.STRONG,
            ModelTier.DEEP,
        ):
            raise ValueError(
                f"Sub-agent tier must be STANDARD or higher, got {spec.model_tier.value}."
            )

        # Tool instances, only the subset specified in the spec
        self._tools: dict[str, Any] = {}

        # F-01/F-07: the typed outcome of this run and its counters. Defaults
        # are filled in by run(); a caller that never runs the runner can
        # still read a deterministic, unset outcome rather than None.
        self.outcome: ResearchOutcome = ResearchOutcome.NO_EVIDENCE
        self.counters = ResearchCounters()
        # F-0.1-10: the typed recovery hint the parent's respawn policy reads.
        # Set by run() to FETCH_BLOCKED / LOW_YIELD / PROVIDER_FAILURE so the
        # parent recovers the RIGHT failure class instead of always broadening
        # a search.
        self.recovery_hint: str = "NO_EVIDENCE"

    @property
    def question(self) -> str:
        return self.spec.question

    @property
    def broadened(self) -> bool:
        """F-07: True when this is a broadened respawn pass.

        Broadened mode is faster AND wider: the LLM query planner is skipped
        (deterministic ``_condense_query_variants`` only), the geography
        anchor is dropped on the primary search pass (whole-corpus breadth
        around the main question), and extraction is capped at 3 URLs.
        """
        return getattr(self.spec, "broadened", False)

    @property
    def parent_agent(self) -> str:
        return self.spec.parent_agent.value

    @property
    def tier(self) -> ModelTier:
        return self.spec.model_tier

    @property
    def tools(self) -> list[str]:
        return [t.value for t in self.spec.tools]

    def _get_tool(self, tool_name: str) -> Any:
        """Get a tool instance by name.

        Sub-agents only have access to the subset of parent's tools
        specified at spawn time (§4.7). This is enforced by the spec.
        """
        tool_enum = None
        for t in self.spec.tools:
            if t.value == tool_name:
                tool_enum = t
                break

        if tool_enum is None:
            raise ValueError(
                f"Sub-agent does not have access to tool '{tool_name}'. "
                f"Available tools: {self.tools}"
            )

        if tool_name not in self._tools:
            self._tools[tool_name] = self._instantiate_tool(tool_enum)

        self._publish_tool_access(tool_name)
        return self._tools[tool_name]

    def _publish_tool_access(self, tool_name: str) -> None:
        """Expose sub-agent tool activity to the same live TUI telemetry feed."""
        try:
            coro = self.bus.publish(
                channel=Channel.TUI,
                msg_type=MessageType.STATUS,
                sender=self.spec.parent_agent,
                payload={
                    "agent": self.parent_agent,
                    "tool": tool_name,
                    "action": "access",
                    "detail": "sub-agent",
                    "success": None,
                    "telemetry_kind": "tool_call",
                    "display": False,
                },
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                coro.close()
                return
            task = loop.create_task(coro)
            task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
        except Exception as exc:  # noqa: BLE001 - telemetry cannot break research
            logger.debug("sub-agent tool telemetry failed for %s: %s", tool_name, exc)

    def _instantiate_tool(self, tool: Any) -> Any:
        """Instantiate a tool by enum value.

        Deferred imports to avoid circular dependencies.
        """
        from hyperion.config import get_settings
        from hyperion.schemas.agents import ToolName

        settings = get_settings()

        if tool == ToolName.SEARXNG:
            from hyperion.tools.searxng import SearxNGClient
            # F-05c: attribute sub-agent searches to the parent specialist so
            # per-owner budget accounting covers the whole specialist stack.
            return SearxNGClient(settings=settings, owner=self.parent_agent)
        elif tool == ToolName.JINA:
            from hyperion.tools.jina import JinaClient
            return JinaClient(settings=settings)
        elif tool == ToolName.OBSCURA:
            from hyperion.tools.obscura import ObscuraClient
            return ObscuraClient(settings=settings)
        elif tool == ToolName.SCRAPLING:
            from hyperion.tools.scrapling import ScraplingClient
            return ScraplingClient(settings=settings)
        elif tool == ToolName.CRAWL4AI:
            from hyperion.tools.crawl4ai import Crawl4AIClient
            return Crawl4AIClient(settings=settings)
        elif tool == ToolName.WAYBACK:
            from hyperion.tools.wayback import WaybackClient
            return WaybackClient(settings=settings)
        elif tool == ToolName.ALPHA_VANTAGE:
            from hyperion.tools.alpha_vantage import AlphaVantageClient
            return AlphaVantageClient(settings=settings)
        elif tool == ToolName.FRED:
            from hyperion.tools.fred import FredClient
            return FredClient(settings=settings)
        elif tool == ToolName.SECOND_BRAIN:
            from hyperion.tools.second_brain import SecondBrainClient
            return SecondBrainClient(settings=settings)
        elif tool == ToolName.DEEP_SEARCH:
            from hyperion.tools.deep_search import DeepSearchClient
            return DeepSearchClient(settings=settings)
        elif tool == ToolName.SEC_EDGAR:
            from hyperion.tools.sec_edgar import SECEdgarClient
            return SECEdgarClient(settings=settings)
        elif tool == ToolName.SEMANTIC_SCHOLAR:
            from hyperion.tools.semantic_scholar import SemanticScholarClient
            return SemanticScholarClient(settings=settings)
        elif tool == ToolName.OPEN_ALEX:
            from hyperion.tools.openalex import OpenAlexClient
            return OpenAlexClient(settings=settings)
        elif tool == ToolName.WORLD_BANK:
            from hyperion.tools.world_bank import WorldBankClient
            return WorldBankClient(settings=settings)
        elif tool == ToolName.GOOGLE_TRENDS:
            from hyperion.tools.google_trends import GoogleTrendsClient
            return GoogleTrendsClient(settings=settings)
        elif tool == ToolName.HACKERNEWS:
            from hyperion.tools.hackernews import HackerNewsClient
            return HackerNewsClient(settings=settings)
        elif tool == ToolName.REDDIT:
            from hyperion.tools.reddit import RedditClient
            return RedditClient(settings=settings)
        else:
            raise ValueError(f"Sub-agents cannot use tool: {tool}")

    def _build_system_prompt(self) -> str:
        """Build the system prompt for a junior researcher.

        This is NOT a generic prompt. It is a focused research directive
        that instructs the sub-agent to:
        - Answer the specific sub-question with data, not opinion
        - Cite sources for every claim
        - Report confidence level
        - Identify gaps (what it couldn't find)
        - Return structured JSON output
        """
        tool_names = ", ".join(self.tools)
        return (
            "You are a senior research associate at HYPERION Consulting, a "
            "premium AI consulting firm. You have been assigned a focused "
            "research sub-question by a senior specialist.\n\n"
            "Your directive:\n"
            "1. Answer the specific sub-question with DATA, not opinion.\n"
            "2. Cite a source for every factual claim. No source = no claim.\n"
            "3. Report your confidence level: HIGH, MEDIUM, or LOW.\n"
            "4. Identify GAPS, what you couldn't find, what data is missing.\n"
            "5. Be DETAILED and SPECIFIC. Include exact numbers, percentages, "
            "dollar figures, dates, and company names. Vague findings are useless.\n"
            "6. Each finding's content should be 200-500 words of detailed analysis "
            "with specific data points, not a one-sentence summary.\n"
            f"7. Use the tools available to you: {tool_names}.\n"
            "8. Follow the tool selection strategy: SearxNG + Jina Search in "
            "parallel for discovery, then Obscura → Scrapling → Jina Reader → "
            "Crawl4AI for extraction. Use SEC EDGAR for financial filings, "
            "Semantic Scholar/OpenAlex for academic papers, World Bank for "
            "macro indicators, Google Trends for demand signals, HackerNews/Reddit "
            "for community sentiment. Scrapling handles anti-bot pages.\n"
            "9. Return your findings as structured JSON matching the "
            "KeyFinding schema.\n\n"
            "You are NOT a generalist. You are a focused researcher answering "
            "one specific question. Do not expand scope. Do not speculate "
            "beyond the data. If you can't find data, say so explicitly.\n\n"
            "Your output must be a JSON object with a 'findings' key containing "
            "an array of finding objects. Each finding must have:\n"
            "  - id: a unique identifier (e.g., 'finding_001')\n"
            f"  - agent: '{self.parent_agent}'\n"
            "  - finding_type: the type (e.g., 'market_data', 'competitor_info')\n"
            "  - title: short title for display\n"
            "  - content: the specific finding with data and evidence\n"
            "  - sources: array of source objects with id, title, url, credibility "
            "(one of: peer_reviewed, government, industry_report, news, blog, social_media)\n"
            "  - confidence: 'high', 'medium', or 'low'\n"
            "  - gaps: array of strings describing what you couldn't find\n"
            "  - source id: the EVIDENCE INDEX id ([E1], [E2], ...) of the exact "
            "retrieved document backing this claim; source url must be that "
            "document's real URL from the EVIDENCE INDEX — never invent a URL"
        )

    def _build_user_prompt(self) -> str:
        """Build the user prompt with the sub-question and parent context."""
        context_str = ""
        if self.spec.context:
            context_parts = []
            for key, value in self.spec.context.items():
                context_parts.append(f"  {key}: {value}")
            context_str = "\n\nParent context (use this as starting point):\n" + "\n".join(context_parts)

        return (
            "Research question: {question}\n\n"
            "Parent agent: {parent}\n"
            "Research tier: {tier}\n"
            "Available tools: {tools}\n"
            "{context}\n\n"
            "Conduct focused research on this sub-question. Use the available "
            "tools to find data. Return your findings as a JSON array of "
            "KeyFinding objects."
        ).format(
            question=self.spec.question,
            parent=self.parent_agent,
            tier=self.tier.value,
            tools=", ".join(self.tools),
            context=context_str,
        )

    async def _gather_raw_data(self) -> str:
        self._ensure_counters()
        """Gather raw data using the available tools.

        This is the research phase of the sub-agent lifecycle:
        searches → extracts → collects raw data for analysis.

        VIGIL-aligned fallback chain (§5.2 updated):
        - Search: SearxNG + Jina Search in parallel (discovery layer)
        - Extract: the single ``UnifiedExtract`` ladder (fix 2.1)
        - Historical: Wayback Machine
        - Financial: Alpha Vantage
        - Macro: FRED
        - Prior research: Second Brain

        Fix 2.1 (§4.5 Finding B-4): extraction used to be a THIRD hand-rolled
        ladder here, five tiers unrolled inline as five near-identical
        ``for url in all_urls[:N]`` blocks (Obscura → Scrapling → Jina Reader →
        Crawl4AI → FlareSolverr). It now delegates to
        :meth:`UnifiedExtract.extract_ladder`, the single implementation. Four
        specific defects of the inline version disappear with it:

          * **No capability gating whatsoever.** Obscura ships as a Windows
            binary; this ladder attempted it per URL, for every sub-agent of
            every specialist, on every Linux/macOS run. The shared ladder probes
            once per instance and names the skip.
          * **Arbitrary, inconsistent per-tier URL budgets** (``[:6]``, ``[:6]``,
            ``[:8]``, ``[:4]``, ``[:3]``): URLs 7-8 were reachable only by the
            *third* tier, and URLs past 8 by none of them. A URL's chance of
            being extracted depended on its rank in a merged search list, which
            is not a retrieval policy anyone chose.
          * **Tier-minor climbing**: an expensive browser tier could run for
            URL A while URL B had not yet been attempted at the free tier.
          * **Tiers the codebase already ships were unreachable from here**:
            curl_cffi, the httpx+trafilatura workhorse, nodriver, camoufox and
            wayback were all absent from this ladder.

        The sub-agent's granted tool subset (§4.7) is still honoured: only tiers
        backed by a tool in ``self.spec.tools`` are offered to the ladder.
        """
        raw_data: list[str] = []
        errors: list[str] = []

        # ── PARALLEL DISCOVERY ──────────────────────────────────────────
        # Run SearxNG and Jina Search simultaneously, merge + dedup results
        searxng_urls: list[str] = []
        jina_search_urls: list[str] = []

        search_tasks: list[Any] = []

        if self._has_tool("searxng"):
            search_tasks.append(self._search_searxng())
        if self._has_tool("jina"):
            search_tasks.append(self._search_jina())

        # Run searches in parallel
        if search_tasks:
            results = await asyncio.gather(*search_tasks, return_exceptions=True)
            for result in results:
                if isinstance(result, Exception):
                    errors.append(f"Search: {result!s:.80}")
                elif isinstance(result, tuple):
                    label, urls, formatted = result
                    if formatted:
                        raw_data.append(formatted)
                    if label == "searxng":
                        searxng_urls = urls
                    elif label == "jina":
                        jina_search_urls = urls

        # F-0.1-1 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md, P0 core): seed the
        # extraction targets with the explicit URL from the parent's context
        # bundle AND any URL named in the question, ranked FIRST. Previously
        # the context URL (e.g. a competitor's pricing page) was read only for
        # the LLM prompt and query planning — never fetched — so an OBSCURA-
        # only scrape spec could never succeed regardless of data availability.
        context_urls = self._context_urls()
        # Merge + dedup URLs: context URLs ranked first, then search legs.
        all_urls = list(dict.fromkeys(context_urls + searxng_urls + jina_search_urls))
        # P1.4 (overhaul §6 P1, 2026-08-10): RESCUE DISCOVERY TIER. When the
        # web discovery legs (SearXNG + Jina) return ZERO URLs, the Aug-10
        # failure class (ENGINE_BLOCKED — banned scrapers) is indistinguishable
        # here from NO_RESULTS. The correct move is not to reword and retry the
        # same dead class (anti-pattern 5) but to pull candidate URLs from the
        # free scholarly/reference API classes (OpenAlex, Semantic Scholar,
        # HackerNews), which do not ban datacenter IPs the way crawlers do.
        # Their results feed the SAME extraction ladder, so a rescued URL gets
        # full content extraction + ledger binding like any other evidence.
        if not all_urls:
            rescue_urls = await self._rescue_discovery()
            if rescue_urls:
                logger.info(
                    "SubAgent P1.4 rescue discovery: SearXNG+Jina returned 0 URLs; "
                    "%d candidate URL(s) recovered from free API classes",
                    len(rescue_urls),
                )
                all_urls = list(dict.fromkeys(rescue_urls))
        # F-07: raw discovery yield is a counter, not a log line. Zero URLs
        # after a search leg is exactly the ``RETRIEVAL_DEGRADED`` signal the
        # audit's F-01 wants typed instead of silently absorbed.
        self._ensure_counters().raw_results = len(all_urls)

        # ── EXTRACTION (fix 2.1: the single UnifiedExtract ladder) ──────
        # Fix 2.2: the sub-question is passed down so the ladder fills its
        # retained-content budget by relevance rather than by document position.
        if all_urls:
            extracted, extract_errors = await self._extract_urls(
                all_urls, self.spec.question
            )
            raw_data.extend(extracted)
            errors.extend(extract_errors)
            # F-07: how many documents actually survived extraction, versus
            # how many URLs were discovered. A 20-URL discovery with 0
            # extracted documents is a typed extraction failure class.
            self._ensure_counters().extracted_documents = len(extracted)

        # ── DATA SOURCES (unchanged) ────────────────────────────────────

        # Historical data, Wayback
        if self._has_tool("wayback"):
            try:
                wayback = self._get_tool("wayback")
                snapshots = await wayback.search(self._condense_query(self.spec.question))
                if snapshots:
                    raw_data.append(f"Historical snapshots:\n{snapshots}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"Wayback: {e!s:.80}")

        # Financial data, Alpha Vantage
        if self._has_tool("alpha_vantage"):
            try:
                av = self._get_tool("alpha_vantage")
                financials = await av.search(self._condense_query(self.spec.question))
                if financials:
                    raw_data.append(f"Financial data:\n{financials}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"AlphaVantage: {e!s:.80}")

        # Macro data, FRED
        if self._has_tool("fred"):
            try:
                fred = self._get_tool("fred")
                macro = await fred.search(self._condense_query(self.spec.question))
                if macro:
                    raw_data.append(f"Macro data:\n{macro}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"FRED: {e!s:.80}")

        # ── Phase 2 Data Sources ────────────────────────────────────────

        # SEC EDGAR, financial filings
        if self._has_tool("sec_edgar"):
            try:
                sec = self._get_tool("sec_edgar")
                filings = await sec.search_full_text(self._condense_query(self.spec.question), limit=10)
                if filings:
                    formatted = "\n".join(
                        f"- {f.company_name} ({f.filing_type}, {f.filing_date}): {f.description[:200]}"
                        for f in filings[:10]
                    )
                    raw_data.append(f"SEC EDGAR filings:\n{formatted}")
                    # Fetch most recent filing content
                    content = await sec.get_filing_content(filings[0])
                    if content and content.content:
                        # Fix 2.2: a 10-K is the worst case for a blind
                        # head-slice anywhere in this file. The first 15,000
                        # chars of one are the cover page, the cross-reference
                        # table and the opening of Item 1A risk factors, while
                        # the segment revenue tables and the MD&A a financial
                        # analyst actually needs sit tens of thousands of
                        # characters further in.
                        body = select_content(
                            content.content,
                            self.spec.question,
                            budget_chars=SEC_FILING_BUDGET_CHARS,
                        )
                        raw_data.append(f"SEC filing content ({filings[0].filing_type} {filings[0].company_name}):\n{body}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"SEC EDGAR: {e!s:.80}")

        # Semantic Scholar, academic papers
        if self._has_tool("semantic_scholar"):
            try:
                ss = self._get_tool("semantic_scholar")
                papers = await ss.search(self._condense_query(self.spec.question), limit=10, year_range="2020-")
                if papers:
                    formatted = "\n".join(
                        f"- {p.title} ({p.year}, {p.venue}, citations={p.citation_count}): {p.abstract[:300]}"
                        for p in papers[:10]
                    )
                    raw_data.append(f"Semantic Scholar papers:\n{formatted}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"SemanticScholar: {e!s:.80}")

        # OpenAlex, scholarly works
        if self._has_tool("open_alex"):
            try:
                oa = self._get_tool("open_alex")
                works = await oa.search_works(self._condense_query(self.spec.question), limit=10)
                if works:
                    formatted = "\n".join(
                        f"- {w.title} ({w.year}, cited_by={w.cited_by_count}): {w.abstract[:300]}"
                        for w in works[:10]
                    )
                    raw_data.append(f"OpenAlex works:\n{formatted}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"OpenAlex: {e!s:.80}")

        # World Bank, macro indicators
        if self._has_tool("world_bank"):
            try:
                wb = self._get_tool("world_bank")
                # Try GDP indicator as a general macro signal
                indicator = await wb.get_indicator("gdp", country="all", date_range="2020:2024")
                if indicator and indicator.data_points:
                    formatted = "\n".join(
                        f"- {dp.get('country', 'N/A')}: {dp.get('value', 'N/A')} ({dp.get('date', 'N/A')})"
                        for dp in indicator.data_points[:15]
                    )
                    raw_data.append(f"World Bank data ({indicator.indicator_name}):\n{formatted}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"WorldBank: {e!s:.80}")

        # Google Trends, demand signals
        if self._has_tool("google_trends"):
            try:
                gt = self._get_tool("google_trends")
                # Extract keywords from the condensed query
                condensed = self._condense_query(self.spec.question)
                keywords = condensed.split()[:3]
                kw_list = [" ".join(keywords)]
                trend = await gt.get_interest_over_time(kw_list, timeframe="today 12-m")
                if trend and trend.interest_data:
                    formatted = "\n".join(
                        f"- {d.get('date', 'N/A')}: {d.get(' '.join(kw_list), 0)}"
                        for d in trend.interest_data[:20]
                    )
                    raw_data.append(f"Google Trends interest ({', '.join(kw_list)}):\n{formatted}")
                # Also get related rising queries
                related = await gt.get_related_queries(kw_list[0], rising=True)
                if related:
                    rel_formatted = "\n".join(
                        f"- {r.query} ({r.value})" for r in related[:10]
                    )
                    raw_data.append(f"Google Trends rising queries:\n{rel_formatted}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"GoogleTrends: {e!s:.80}")

        # HackerNews, tech community sentiment
        if self._has_tool("hackernews"):
            try:
                hn = self._get_tool("hackernews")
                stories = await hn.search_stories(self._condense_query(self.spec.question), hits=15)
                if stories:
                    formatted = "\n".join(
                        f"- {s.title} (points={s.points}, comments={s.num_comments}): {s.url}"
                        for s in stories[:15]
                    )
                    raw_data.append(f"HackerNews stories:\n{formatted}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"HackerNews: {e!s:.80}")

        # Reddit, community sentiment
        if self._has_tool("reddit"):
            try:
                reddit = self._get_tool("reddit")
                posts = await reddit.search_posts(
                    self._condense_query(self.spec.question), sort="relevance", time_filter="year", limit=15
                )
                if posts:
                    formatted = "\n".join(
                        f"- [{p.subreddit}] {p.title} (upvote={p.upvote_ratio:.0%}, comments={p.num_comments})"
                        for p in posts[:15]
                    )
                    raw_data.append(f"Reddit posts:\n{formatted}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"Reddit: {e!s:.80}")

        # Second Brain, prior research
        if self._has_tool("second_brain"):
            try:
                brain = self._get_tool("second_brain")
                prior = await brain.search(self._condense_query(self.spec.question))
                if prior:
                    raw_data.append(f"Prior research from vault:\n{prior}")
            except Exception as e:  # noqa: BLE001 - failure is recorded in the result
                errors.append(f"SecondBrain: {e!s:.80}")

        if errors:
            raw_data.append(f"Tool errors encountered: {'; '.join(errors)}")

        return "\n\n---\n\n".join(raw_data) if raw_data else "No raw data available from tools."

    @staticmethod
    def _condense_query(question: str, max_len: int = 120) -> str:
        """Condense a long research question into a concise search query.

        Sub-agent questions are often full paragraphs (e.g., 'Find TAM data
        for: Should India enter the blockchain market?'). Search engines
        return poor results for paragraph-length queries. This method:
        1. Strips common prefixes ('Find ', 'Search for ', 'Research ')
        2. Removes parenthetical asides and em-dashes
        3. Removes filler words that add noise
        4. Truncates to max_len at a word boundary

        WHY THE FILLER SET IS SMALLER THAN IT LOOKS (fix 1.4, audit §4.4
        Finding B-3): the previous filler set deleted the exact words that
        carry a consulting question's analytical intent, ``'not'`` inverted the meaning of any negative-framed question
        ("should we NOT enter" -> "enter", the opposite of what was asked),
        and ``'should'``, ``'how'``, ``'why'``, ``'what'``, ``'which'``,
        ``'most'``, ``'more'`` are the interrogative/comparative words that
        distinguish "the biggest market" from "the fastest-growing market"
        or "how to enter" from "why not to enter". Those eight words are
        removed from the filler set below. Grammatical connective tissue
        (articles, prepositions, auxiliary verbs) is still stripped, that
        part of the pipeline was never the problem.
        """
        q = question.strip()

        # Strip common instruction prefixes
        for prefix in (
            "Find ", "Search for ", "Research ", "Identify ",
            "Look up ", "Gather ", "Collect ", "Analyze ",
            "Investigate ", "Explore ", "Discover ",
        ):
            if q.lower().startswith(prefix.lower()):
                q = q[len(prefix):]
                break

        # Remove 'TAM data for:', 'spending data for:', etc.
        q = re.sub(r'^\s*(?:[A-Z]{2,}\s+)?(?:data|information|details|facts|statistics|metrics|numbers|figures|reports?|studies|trends?|analysis|insights?)\s+(?:for|on|about|regarding|related to)\s*:?\s*', '', q, flags=re.IGNORECASE)

        # Remove parenthetical asides: (e.g., Bitcoin, Ethereum)
        #
        # NOTE: the removed content is NOT thrown away here, parentheticals
        # frequently name the specific entities the question is actually
        # about ("(Bitcoin, Ethereum)"), so deleting them outright turned a
        # concrete comparison query into a generic one. `_condense_query`
        # keeps producing the entity-free primary query (still useful, and
        # keeps this method's existing return type/behavior for the 11
        # callers that only need one query), but `_condense_query_variants`
        # below reconstructs a second query that folds the parenthetical
        # content back in, for the two callers (SearxNG, Jina) that fan out
        # multiple queries in parallel and can afford a second search leg.
        q = re.sub(r'\([^)]*\)', '', q)

        # Remove em-dashes, en-dashes, and hyphens used as separators.
        # NOTE: hyphen MUST be last (or escaped) inside a character class,
        # `\u2013--` was parsed as a character RANGE (\u2013 .. -), which is
        # invalid and raised `re.PatternError` on every call under Python
        # 3.13. That crash was swallowed by the callers' bare
        # `except Exception: pass`, silently zeroing out all sub-agent
        # search (see HYPERION_DEEP_AUDIT_2026-07-27.md §0 / finding B-1).
        q = re.sub(r'\s*[\u2013\u2014-]+\s*', ' ', q)

        # Remove filler words, grammatical connective tissue only.
        #
        # fix 1.4 (audit §4.4 Finding B-3): 'not', 'should', 'how', 'why',
        # 'what', 'which', 'most', 'more' used to be in this set and were
        # removed. Those are not noise, they carry the question's
        # analytical intent (negation, comparison, interrogative framing).
        # Deleting them silently rewrote "should we NOT enter this market"
        # into "enter market", the opposite question, and collapsed "the
        # MOST effective strategy" into "effective strategy", discarding
        # the superlative that made the question specific.
        filler = {
            'the', 'a', 'an', 'for', 'of', 'to', 'in', 'on', 'at', 'by',
            'with', 'from', 'about', 'into', 'through', 'during', 'before',
            'after', 'above', 'below', 'between', 'under', 'further',
            'then', 'once', 'here', 'there', 'when', 'where',
            'all', 'any', 'both', 'each', 'few',
            'other', 'some', 'such', 'no', 'nor', 'only', 'own',
            'same', 'so', 'than', 'too', 'very', 'can', 'will', 'just',
            'now', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'would',
            'could', 'may', 'might', 'must', 'shall', 'this', 'that',
            'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
            'who', 'whom', 'whose', 'and', 'or', 'but',
            'if', 'because', 'as', 'until', 'while', 'also', 'use',
            'using', 'used', 'like', 'e.g.', 'e.g', 'eg', 'i.e.', 'i.e',
            'ie', 'etc', 'etc.', 'similar', 'target', 'specific',
        }
        words = q.split()
        kept = [w for w in words if w.lower().strip('.,;:!?') not in filler]
        q = ' '.join(kept) if kept else q

        # Collapse whitespace
        q = re.sub(r'\s+', ' ', q).strip()

        # Truncate at word boundary
        if len(q) > max_len:
            q = q[:max_len].rsplit(' ', 1)[0]

        return q.strip() or question[:max_len]

    @classmethod
    def _condense_query_variants(cls, question: str, max_len: int = 120) -> list[str]:
        """Return 1-2 search queries: the condensed primary query, plus a
        second variant that folds any parenthetical content back in.

        fix 1.4 (audit §4.4 Finding B-3): `_condense_query` strips
        parenthetical asides like "(Bitcoin, Ethereum)" entirely, those
        asides are frequently where a question's specific entities live,
        so simply deleting them produced a generic query with no way to
        recover the named entities. This method keeps the primary
        (entity-free) query as variant 1, and, when the question actually
        contained a non-trivial parenthetical, appends a second variant
        that appends the parenthetical's content to the primary query, so
        callers that can afford two search legs (SearxNG, Jina) get one
        query anchored on the general topic and one anchored on the named
        entities.

        Returns a list of length 1 when there is no usable parenthetical
        (the common case), or length 2 when there is one. Never returns an
        empty list.
        """
        primary = cls._condense_query(question, max_len=max_len)

        # Pull out parenthetical content the same way _condense_query
        # strips it, so the variant is built from exactly what was removed.
        parens = re.findall(r'\(([^)]*)\)', question)
        # Filter out trivial/instructional parentheticals ("(see above)",
        # single short words that are unlikely to be a named entity list),
        # keeping ones that look like a comma/semicolon-separated entity
        # list or a multi-word phrase, that is the pattern the audit's
        # example ("(Bitcoin, Ethereum)") and real sub-agent questions
        # actually use.
        entity_text = ""
        for p in parens:
            p = p.strip()
            if not p:
                continue
            if len(p) < 3:
                continue
            if re.match(r'^(?:e\.?g\.?|i\.?e\.?|see .*|etc\.?)$', p, flags=re.IGNORECASE):
                continue
            # Strip a leading "e.g."/"i.e."/"etc." label so a parenthetical
            # like "(e.g. Salesforce, HubSpot)" contributes the real entity
            # names ("Salesforce, HubSpot") to the variant query without the
            # literal abbreviation token riding along (fix 1.4 polish).
            p = re.sub(r'^(?:e\.?g\.?|i\.?e\.?|etc\.?)\s*[:,]?\s*', '', p, flags=re.IGNORECASE).strip()
            if len(p) < 3:
                continue
            entity_text = p
            break

        if not entity_text:
            return [primary]

        # Build the second variant: primary query + the entity list,
        # truncated the same way as the primary.
        variant = f"{primary} {entity_text}".strip()
        variant = re.sub(r'\s+', ' ', variant).strip()
        if len(variant) > max_len:
            variant = variant[:max_len].rsplit(' ', 1)[0]

        if not variant or variant == primary:
            return [primary]

        return [primary, variant]

    # fix 1.5 (audit §7 item 1.5): a query yielding fewer than this many
    # results is treated as "low yield" and gets one broadened retry with
    # the geography anchor dropped, rather than being accepted as the final
    # answer. 3 matches the audit's own wording ("<3 scored results").
    LOW_YIELD_THRESHOLD = 3

    # fix 1.3 (audit §4.4 Finding B-3, §7 item 1.3): how many of the
    # planner's diversified queries each search leg actually dispatches.
    #
    # The planner produces 5-10 queries. Sending *all* of them to *both*
    # SearxNG and Jina would be up to 20 near-duplicate requests per
    # sub-question, that blows the search budget for almost no marginal
    # recall, since both engines index largely the same open web.
    #
    # Instead the plan is **partitioned** across the two legs (see
    # `_plan_queries(leg=...)`): SearxNG takes the even-indexed planner
    # queries, Jina takes the odd-indexed ones. Because `_top_up` orders
    # the plan angle-first, an alternating split gives *each* leg a
    # diversified subset while the **union across legs is the whole plan**,
    # so the audit's Phase 1 exit criterion (">=8 distinct grounded queries
    # per sub-question") is met at roughly half the request cost of sending
    # every query down every leg.
    PLANNED_QUERIES_PER_LEG = 5

    # fix 2.1 (audit §4.5 Finding B-4, §6 Phase 2 item 2.1) ──────────────────
    #
    # Extraction tier → the granted tool (§4.7 `spec.tools`) that backs it.
    # A sub-agent may only use the tool subset its parent granted at spawn
    # time, so the ladder offered to `UnifiedExtract` is filtered to tiers whose
    # backing tool is present.
    #
    # Three tiers are deliberately absent from this table, and the reason
    # differs in each case:
    # * `http`, httpx + trafilatura, keyless and browserless, with no
    #     `ToolName` of its own. It is the cheapest *parsing* tier the codebase
    #     ships, it cannot leak an API key or launch a browser, and the audit
    #     names it "the keyless, browserless workhorse" (§4.6). Gating it behind
    #     a tool grant nobody can express would make it permanently unreachable
    #     from sub-agents, which is how it came to be missing from this path in
    #     the first place. It is therefore ALWAYS offered.
    # * `curl_cffi`, likewise a plain HTTP fetch with a spoofed TLS
    #     fingerprint. Always offered, same reasoning.
    # * `nodriver` / `camoufox`, real browser launches with no `ToolName`.
    #     These are NOT auto-granted: the audit's §4.7 quota discipline exists
    #     precisely so a junior agent cannot spend an expensive resource it was
    #     never handed. They are reachable through `UnifiedExtract` elsewhere in
    #     the system, just not by unilateral sub-agent decision.
    EXTRACT_TIER_TOOLS: dict[str, str] = {
        "jina": "jina",
        "obscura": "obscura",
        "crawl4ai": "crawl4ai",
        "scrapling": "scrapling",
        "flaresolverr": "flaresolverr",
        "wayback": "wayback",
    }

    # Tiers offered regardless of the granted tool subset, see above.
    ALWAYS_AVAILABLE_EXTRACT_TIERS: tuple[str, ...] = ("curl_cffi", "http")

    # How many discovered URLs to attempt extraction on.
    #
    # The inline ladder this replaces used a DIFFERENT budget per tier
    # (`[:6]`, `[:6]`, `[:8]`, `[:4]`, `[:3]`), which meant URLs 7-8 were
    # reachable only by the third tier and URLs past 8 by no tier at all, a
    # URL's chance of being extracted depended on its rank in a merged search
    # list rather than on any deliberate policy. One budget, applied once,
    # against the whole ladder.
    MAX_EXTRACT_URLS = 10

    def _extraction_tiers(self) -> list[str]:
        """Extraction tiers this sub-agent may use, in ladder order.

        OVERHAUL4 P7 (2026-08-11): tiers were previously gated on the agent's
        LLM-visible tool grants (``EXTRACT_TIER_TOOLS`` + ``_has_tool``), so a
        sub-agent whose parent specialist hadn't granted ``jina``/``obscura``
        silently lost those extraction tiers even when the backends were
        installed — the root cause of the 0 ``stage=extraction`` ledger
        records (findings built from snippets only). Extraction is internal
        plumbing, not an LLM tool surface: every backend that can run here
        should be on the ladder. ``UnifiedExtract._tier_available`` decides
        availability at runtime (curl_cffi installed, jina keyless-capable,
        obscura binary present, craw4ai installed, ...); ``flaresolverr``
        stays governed by its own circuit breaker (W-12: CAPTCHA tooling is
        investigation-only). Ordering is still normalised cheap-first by the
        ladder itself.
        """
        from hyperion.tools.unified_extract import UnifiedExtract

        return list(UnifiedExtract.TIER_ORDER)

    async def _extract_urls(
        self, urls: list[str], query: str = ""
    ) -> tuple[list[str], list[str]]:
        """Extract discovered URLs via the single ladder (fix 2.1).

        Returns ``(raw_data_blocks, errors)`` in the shape
        :meth:`_gather_raw_data` already accumulates, so the delegation is
        invisible to its caller.

        ``query`` (fix 2.2) is the sub-question these URLs were discovered for.
        It is what the ladder ranks chunks against when a page exceeds the
        retained-content budget. This matters most for the sub-agent path
        specifically: a sub-agent's whole output is a handful of KeyFindings
        distilled from this text, so a budget spent on a PDF's front matter
        rather than its tables produces a finding with no number in it, which
        is indistinguishable, downstream, from the ``research_gap`` the audit's
        P0 was manufacturing (§4.2).

        Never raises. A ladder that returns nothing yields an ``errors`` entry
        naming each tier that was tried and why it produced nothing, the
        inline version it replaces could only append a single per-tier
        ``f"Obscura: {e}"`` and lost the reason entirely when the failure was
        "returned no usable content" rather than an exception.
        """
        from hyperion.config import get_settings
        from hyperion.tools.unified_extract import UnifiedExtract

        tiers = self._extraction_tiers()
        # F-07c: a broadened respawn is a fast second pass — cap extraction at
        # 3 URLs instead of the full ladder budget.
        extract_cap = 3 if self.broadened else self.MAX_EXTRACT_URLS
        targets = urls[:extract_cap]
        raw_data: list[str] = []
        errors: list[str] = []

        # Same settings source `_instantiate_tool` uses for every other tool, so
        # the ladder's leaf clients see the identical configuration the granted
        # tools would have seen.
        #
        # L2 fix: concurrency is bounded explicitly at the sub-agent boundary
        # (4 in flight) rather than relying on `EXTRACTION_CONCURRENCY`'s
        # implicit default. Rationale: `_gather_raw_data` is the biggest single
        # latency contributor in a specialist sub-agent, but each extraction
        # tier internally opens sockets / browser tabs, so unbounded fanout
        # can starve the whole event loop when several sub-agents run in
        # parallel. A cap of 4 keeps 10 URLs at ~3 waves per tier while still
        # exploiting most of the available parallelism.
        extractor = UnifiedExtract(settings=get_settings())
        try:
            outcome = await extractor.extract_ladder(
                targets,
                tiers=tiers,
                query=query or self.spec.question,
                concurrency=4,
            )
        except Exception as e:
            # The ladder documents a never-raises contract, but a sub-agent
            # losing its entire research phase to an unexpected error here is
            # exactly the class of silent total outage the audit's P0 was
            # (§4.2). Report it and let the data-source blocks below still run.
            logger.warning(
                "Extraction ladder failed for %d URL(s): %s", len(targets), e, exc_info=True
            )
            return ([], [f"Extraction: {e!s:.80}"])
        finally:
            try:
                await extractor.close()
            except Exception as close_err:  # noqa: BLE001 - failure is logged, not swallowed
                logger.debug("Closing extraction ladder failed: %s", close_err)

        for result in outcome.results:
            text = result.markdown or result.content
            if not text:
                continue
            raw_data.append(f"{result.tool_used} content from {result.url}:\n{text}")
            # P0 (overhaul §6 P0.2): extraction-ladder survivors are evidence
            # too — attach the content fingerprint so the ledger measures
            # corpus depth (extracted documents), not just URL discovery.
            record_evidence(
                url=result.url,
                title=getattr(result, "title", "") or "",
                snippet=text[:200],
                content_hash=content_hash_of(text),
                engine=result.tool_used or "unified_extract",
                profile="extraction_ladder",
                stage="extraction",
            )

        if not outcome.results:
            for tier, why in outcome.errors.items():
                errors.append(f"{tier}: {why!s:.80}")
            for tier, why in outcome.tiers_unavailable.items():
                errors.append(f"{tier} unavailable: {why!s:.80}")

            # F-0.1-3 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): route probing.
            # The primary page failed (404 / JS-shell / empty). Bounded probing
            # of sibling pricing routes — {url}/pricing, /plans, /packages,
            # /pricing/ — max 3 probes, first page yielding usable text wins.
            # Without this, a site whose landing page is a JS shell but whose
            # /pricing page is static would deterministically exhaust.
            probes = self._route_probe_candidates(targets)
            if probes:
                logger.info(
                    "F-0.1-3 route probe: primary extraction empty for %d URL(s); "
                    "probing %d sibling pricing route(s)",
                    len(targets),
                    len(probes),
                )
                try:
                    probe_outcome = await extractor.extract_ladder(
                        probes,
                        tiers=tiers,
                        query=query or self.spec.question,
                        concurrency=2,
                    )
                except Exception as probe_exc:  # noqa: BLE001 - probing is best-effort
                    logger.warning("route probe ladder failed (non-fatal): %s", probe_exc)
                    probe_outcome = None
                if probe_outcome and probe_outcome.results:
                    for result in probe_outcome.results:
                        text = result.markdown or result.content
                        if not text:
                            continue
                        raw_data.append(
                            f"{result.tool_used} content from {result.url}:\n{text}"
                        )
                        record_evidence(
                            url=result.url,
                            title=getattr(result, "title", "") or "",
                            snippet=text[:200],
                            content_hash=content_hash_of(text),
                            engine=result.tool_used or "unified_extract",
                            profile="extraction_ladder",
                            stage="extraction",
                        )
                    errors.append(
                        f"route_probe: primary URLs yielded nothing; recovered "
                        f"{len(probe_outcome.results)} via sibling route(s)"
                    )

        logger.info(
            "SubAgent extraction: %d/%d URL(s) extracted via %s (tried: %s)",
            len(outcome.results),
            len(targets),
            ", ".join(outcome.tools_used) or "none",
            ", ".join(outcome.tools_tried) or "none",
        )
        return (raw_data, errors)

    @staticmethod
    def _route_probe_candidates(urls: list[str]) -> list[str]:
        """F-0.1-3: sibling pricing routes to probe when a page yields nothing.

        Bounded: at most 3 probes per URL, first page yielding usable text
        wins upstream. Deterministic route set (pricing/plans/packages), plus
        the same routes with a trailing slash.
        """
        routes = ("/pricing", "/plans", "/packages", "/pricing/")
        candidates: list[str] = []
        seen: set[str] = set()
        for url in urls:
            if not url:
                continue
            base = url.rstrip("/")
            for route in routes:
                candidate = base + route
                if candidate not in seen:
                    seen.add(candidate)
                    candidates.append(candidate)
            if len(candidates) >= 12:
                break
        return candidates[:12]

    def _check_sufficiency(self, raw_data: list[str], query: str = "") -> bool:
        """F-0.1-5: deterministic sufficiency gate for fetch/pricing tasks.

        A scrape task that extracted *content* but no *pricing artifacts* is
        not a success — it is a thin fetch that should feed the fallback
        routes (F-0.1-6) instead of being accepted as-is. This cheap check
        scans the extracted text for pricing signals (``$``, "per month",
        "per year", tier names, plan columns) and stamps
        ``counters.sufficiency_failed`` when the question asks for pricing but
        none is present. Returns True when the gate PASSED (sufficient).
        """
        try:
            import re

            question = (query or "").lower()
            # Only gate on tasks that actually ask for pricing/monetary data.
            if not any(tok in question for tok in ("pricing", "price", "cost", "per month",
                                                   "per year", "plan", "tier", "$")):
                return True
            if not raw_data:
                return True  # no content at all is handled by other gates
            combined = "\n".join(raw_data)
            artifacts = re.findall(
                r"(\$\s?\d[\d,]*\.?\d*|per month|per year|per user|per seat|"
                r"\b(starting at|starts at|from \$|/mo|/month|/year)\b)",
                combined,
                flags=re.IGNORECASE,
            )
            sufficient = bool(artifacts)
            if not sufficient:
                self._ensure_counters().sufficiency_failed = 1
                logger.info(
                    "F-0.1-5 sufficiency gate FAILED for %r — extracted content "
                    "but no pricing artifacts; feeding fallback routes",
                    question[:60],
                )
            return sufficient
        except Exception as exc:  # noqa: BLE001 - the gate must never break research
            logger.debug("sufficiency gate errored (treated as pass): %s", exc)
            return True

    def _context_urls(self) -> list[str]:
        """F-0.1-1: explicit URLs from the parent's context bundle + question.

        The plan's P0 core: a spec like the pricing-scrape type receives
        ``context={"url": "https://competitor.com/pricing"}`` but the runner
        previously never fetched it. This returns those URLs (deduplicated, in
        order: ``context["url"]`` first, then any ``url`` key anywhere in the
        context dict, then any http(s) URL named in the question text) so
        ``_gather_raw_data`` can seed extraction with them, ranked first.
        """
        urls: list[str] = []
        try:
            ctx = getattr(self.spec, "context", None) or {}

            def _candidate(value: object) -> None:
                if isinstance(value, str) and value.startswith("http") and value not in urls:
                    urls.append(value)

            direct = ctx.get("url") or ctx.get("page_url") or ctx.get("target_url")
            _candidate(direct)
            for key, value in ctx.items():
                if key in ("url", "page_url", "target_url"):
                    continue
                if isinstance(value, str) and value.startswith("http"):
                    _candidate(value)
                elif isinstance(value, (list, tuple)):
                    for item in value:
                        _candidate(item)

            import re

            question = getattr(self.spec, "question", "") or ""
            for m in re.findall(r"https?://[^\s\)\"']+", question):
                url = m.rstrip(".,;")
                _candidate(url)
        except Exception as exc:  # noqa: BLE001 - URL extraction must never break research
            logger.debug("_context_urls failed: %s", exc)
        return urls[:5]

    async def _rescue_discovery(self) -> list[str]:
        """P1.4: pull candidate URLs from the free scholarly/reference APIs.

        Called ONLY when SearXNG + Jina returned zero URLs. The rescuer never
        rewrites the query (anti-pattern 5) — it reroutes to a DIFFERENT source
        class that does not ban datacenter IPs the way web crawlers do:

        * OpenAlex          (``open_alex`` tool) — scholarly works, mailto-raised
                            rate ceiling, no CAPTCHA.
        * Semantic Scholar  (``semantic_scholar`` tool) — academic papers.
        * HackerNews        (``hackernews`` tool) — Algolia API, free, no key.

        Each tool is exercised only when granted to this sub-agent, and every
        failure is recorded as a typed ``errors`` entry — never swallowed.
        """
        try:
            from hyperion.tools.engine_health import get_engine_health
        except Exception:  # noqa: BLE001 - health is best-effort, rescue still runs
            get_engine_health = None  # type: ignore[assignment]

        # Skip the rescue when the WEB class is healthy — if SearXNG's own
        # scraper pool is up, a zero-result pass is NO_RESULTS (handled by the
        # broaden retry), not ENGINE_BLOCKED. When the web class is dead but
        # scholar/reference are alive, the rescue reroutes to them — that is
        # precisely the Aug-10 rescue pattern. Only a fleet-wide outage (no
        # living class at all) would also be caught here.
        if get_engine_health is not None:
            try:
                if get_engine_health().class_healthy("web"):
                    return []
            except Exception as exc:  # noqa: BLE001 - a health read must not block rescue
                logger.debug("rescue discovery health read failed (non-blocking): %s", exc)

        query = self._condense_query(self.spec.question)
        candidate_urls: list[str] = []
        tasks: list[tuple[str, Any]] = []

        if self._has_tool("open_alex"):
            try:
                oa = self._get_tool("open_alex")
                works = await oa.search_works(query, limit=10)
                for work in works:
                    url = (getattr(work, "url", "") or "").strip()
                    if url and url not in candidate_urls:
                        candidate_urls.append(url)
            except Exception as e:  # noqa: BLE001 - recorded, not swallowed
                tasks.append(("openalex", f"{e!s:.80}"))

        if self._has_tool("semantic_scholar"):
            try:
                ss = self._get_tool("semantic_scholar")
                papers = await ss.search(query, limit=10, year_range="2020-")
                for paper in papers:
                    url = (getattr(paper, "url", "") or getattr(paper, "paper_url", "") or "").strip()
                    if url and url not in candidate_urls:
                        candidate_urls.append(url)
            except Exception as e:  # noqa: BLE001 - recorded, not swallowed
                tasks.append(("semantic_scholar", f"{e!s:.80}"))

        if self._has_tool("hackernews"):
            try:
                hn = self._get_tool("hackernews")
                stories = await hn.search_stories(query, hits=15)
                for story in stories:
                    url = (getattr(story, "url", "") or "").strip()
                    if url and url not in candidate_urls:
                        candidate_urls.append(url)
            except Exception as e:  # noqa: BLE001 - recorded, not swallowed
                tasks.append(("hackernews", f"{e!s:.80}"))

        for label, detail in tasks:
            logger.warning("SubAgent rescue discovery %s failed: %s", label, detail)

        return candidate_urls[: self.MAX_EXTRACT_URLS]

    async def _plan_queries(self, leg: str = "") -> list[str]:
        """Return the diversified query set for this sub-agent's question.

        **fix 1.3 (audit §4.4 Finding B-3 / §7 Phase 1 item 1.3.)** Before
        this, `grep -E "_llm_complete|generate.*quer|query.*llm"
        hyperion/agents/sub_agent.py` returned *no matches*, there was no
        reasoning step anywhere in the sub-agent path. Query construction
        was a pure regex + stopword pipeline emitting **exactly one query
        per tool**, where a human MBB associate given "should we enter now
        or wait?" runs 8-15 differently angled searches.

        This method calls `hyperion.tools.query_planner.plan_queries`, which:
          - runs at **FAST** tier (never STRONG/DEEP, §4.7 quota rule),
          - emits **5-10 schema-validated** queries across the
            entity / metric / counter-thesis / regulatory / competitor /
            time-series angles named in the audit,
          - **caches by sub-question hash** so several specialists spawning
            the same sub-question in one engagement cost one LLM call,
          - and **never returns empty and never raises**, on any failure it
            degrades to a deterministic angle-suffix plan, so a planner
            outage can never reproduce the audit's P0 (a silent query-layer
            failure zeroing out all research).

        The result is merged with `_condense_query_variants` (fix 1.4) so the
        deterministic entity-recovering variant is always present regardless
        of what the planner returned, the planner *adds* angles, it does not
        replace the proven regex path.

        Args:
            leg: which search leg is asking (``"searxng"``, ``"jina"``, or
                ``""`` for "give me everything"). The deterministic
                `_condense_query_variants` baseline is returned to **both**
                legs unconditionally (it is the proven pre-1.3 behaviour and
                must not depend on the split), while the *planner's* queries
                are partitioned by parity so the two legs cover disjoint
                angles rather than duplicating each other's requests. See
                `PLANNED_QUERIES_PER_LEG`.
        """
        from hyperion.tools.query_planner import plan_queries
        from hyperion.tools.query_utils import get_engagement_focus

        # Deterministic baseline first, this is the pre-1.3 behaviour and
        # must survive planner failure untouched.
        baseline = self._condense_query_variants(self.spec.question)

        # F-07c: a broadened respawn runs AFTER a full primary pass — it is
        # a deterministic, fast, whole-corpus second attempt. Skip the LLM
        # query planner entirely (one less call, no latency gamble) and use
        # only the deterministic variants.
        if self.broadened:
            return baseline

        _focus_q, subject, geography = get_engagement_focus()
        try:
            plan = await plan_queries(
                self.spec.question,
                router=self.router,
                subject=subject,
                geography=geography,
                context=self.spec.context or {},
                parent_agent=self.parent_agent,
            )
        except Exception as e:
            # plan_queries is contractually non-raising, but a sub-agent
            # must never die because query planning misbehaved.
            logger.warning(
                "SubAgent query planning failed for question=%r: %s",
                self.spec.question[:120], e, exc_info=True,
            )
            return baseline

        # Partition the planner's queries across the two search legs by
        # index parity so their union is the full plan and neither leg pays
        # for the other's requests. An unrecognised/empty `leg` gets the
        # whole plan (used by tests and any future single-leg caller).
        all_planned = plan.query_strings
        if leg == "searxng":
            planned = all_planned[0::2]
        elif leg == "jina":
            planned = all_planned[1::2]
        else:
            planned = all_planned
        planned = planned[: self.PLANNED_QUERIES_PER_LEG]

        merged: list[str] = []
        seen: set[str] = set()
        for q in baseline + planned:
            q = (q or "").strip()
            if not q:
                continue
            norm = " ".join(sorted(set(q.lower().split())))
            if norm in seen:
                continue
            seen.add(norm)
            merged.append(q)

        logger.debug(
            "SubAgent query plan for %r leg=%r: %d queries (%d planner of %d, "
            "%d baseline, angles=%s, degraded=%s, cached=%s)",
            self.spec.question[:80], leg or "all", len(merged), len(planned),
            len(all_planned), len(baseline), sorted(plan.angles_covered),
            plan.degraded, plan.cached,
        )
        return merged or baseline

    # F-03: the audit's E-06/E-07 found the sub-agent search leg was SERIAL
    # (``for query in queries: await search_fn(query)``). With the query
    # planner emitting 5-10 variants per leg and each SearXNG request able to
    # retry across endpoints for up to 45s each, a serial leg consumed the
    # entire 420s search allocation before extraction or analysis ever ran.
    #
    # The scheduler below bounds concurrency AND stops dispatching new
    # queries once the evidence minimum is met (E-03 exit gate: "Do not wait
    # for every planned query after the minimum evidence contract is met")
    # AND cancels pending work when the phase deadline expires.
    FAN_OUT_CONCURRENCY = 3
    # Stop dispatching new queries once this many merged results exist. The
    # audit's Phase 1 exit criterion wants >=8 distinct grounded queries per
    # sub-question to actually REACH the network, so the early stop must only
    # fire on a genuinely rich pool — never truncate the normal planner plan
    # (~7 queries/leg × up to 15 results). 30 merged results is comfortably
    # above the extraction budget (MAX_EXTRACT_URLS=10) and above any
    # realistic single-query yield, so a healthy pool still dispatches the
    # full diversified plan while a pathological (slow-but-productive) pool
    # is bounded by the deadline instead of the evidence minimum.
    FAN_OUT_MIN_EVIDENCE = 30
    # Wall-clock budget for the whole fan-out phase, in seconds. The caller
    # ALSO wraps the phase in asyncio.wait_for; this deadline is the tighter,
    # phase-attributable bound so the search budget is never the analysis
    # budget by accident (F-03 exit gate: telemetry reports planning,
    # discovery, extraction and analysis separately).
    FAN_OUT_DEADLINE_SECONDS = 120

    async def _fan_out_search(
        self,
        search_fn: Any,
        queries: list[str],
        num_results: int,
        *,
        drop_geography: bool = False,
    ) -> list[Any]:
        """Run ``search_fn`` over each query variant with bounded parallelism
        and a deadline, merging and deduplicating by URL (first-seen order
        preserved).

        F-03: the audit found the previous implementation was serial. It now
        dispatches up to :data:`FAN_OUT_CONCURRENCY` searches concurrently,
        stops launching new queries once :data:`FAN_OUT_MIN_EVIDENCE` results
        are merged, and cancels pending work when the phase deadline passes.
        A per-query failure is logged and skipped, never fatal: one dead
        query variant must not abort the leg.

        Shared by `_search_searxng`/`_search_jina` for both the normal pass
        and the fix-1.5 low-yield broadened retry, so the two callers cannot
        drift on how variants are merged.
        """
        merged: list[Any] = []
        seen_urls: set[str] = set()
        if not queries:
            return merged

        deadline = time.monotonic() + self.FAN_OUT_DEADLINE_SECONDS
        lock = asyncio.Lock()
        iterator = iter(queries)

        def _merge(query: str, variant_results: list[Any]) -> None:
            """Merge one variant into the dedup set (caller holds ``lock``)."""
            for r in variant_results:
                url = getattr(r, "url", "") or ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                merged.append(r)

        async def _worker() -> None:
            """One bounded worker: grab the next undispatched query, search it,
            merge its results. Exits when the iterator is exhausted, the phase
            deadline passes, or the evidence minimum is met (F-03 early stop)."""
            while True:
                async with lock:
                    # Stop conditions are checked under the lock so the
                    # early-stop is strict: once ``merged`` reaches the
                    # minimum, no worker may claim another query.
                    if time.monotonic() >= deadline:
                        logger.warning(
                            "SubAgent fan-out deadline reached (%ds); "
                            "%d result(s) so far",
                            self.FAN_OUT_DEADLINE_SECONDS,
                            len(merged),
                        )
                        return
                    if len(merged) >= self.FAN_OUT_MIN_EVIDENCE:
                        return
                    try:
                        query = next(iterator)
                    except StopIteration:
                        return
                kwargs: dict[str, Any] = {"num_results": num_results}
                if drop_geography:
                    kwargs["drop_geography"] = True
                try:
                    variant_results = await search_fn(query, **kwargs)
                except Exception as exc:  # noqa: BLE001 - one variant must not kill the leg
                    logger.warning(
                        "SubAgent fan-out query failed (%r): %s",
                        query[:80], exc,
                    )
                    variant_results = []
                async with lock:
                    _merge(query, list(variant_results or []))

        worker_count = min(self.FAN_OUT_CONCURRENCY, len(queries))
        workers = [asyncio.create_task(_worker()) for _ in range(worker_count)]
        try:
            await asyncio.gather(*workers)
        except asyncio.CancelledError:
            # Phase deadline or parent cancellation: cancel in-flight work so
            # the event loop is not left holding orphaned searches.
            for worker in workers:
                worker.cancel()
            raise

        return merged

    async def _search_searxng(self) -> tuple[str, list[str], str | None]:
        """Search via SearxNG. Returns (label, urls, formatted_results).

        fix 1.3: the query set now comes from `_plan_queries()`, which fans
        the sub-question out into the deterministic condensed variants
        (fix 1.4, below) *plus* the LLM query planner's diversified
        entity/metric/counter-thesis/regulatory/competitor/time-series
        angles. Before 1.3 this leg dispatched one regex-built query.

        fix 1.4: runs the primary condensed query, and, when the question
        contained a parenthetical naming specific entities (e.g. "(Bitcoin,
        Ethereum)"), a second query that folds those entities back in, so
        a named-entity comparison question isn't reduced to a single,
        entity-free search.

        fix 1.5: if that combined pass yields fewer than
        `LOW_YIELD_THRESHOLD` results, retry the same variants once more
        with `drop_geography=True`, a jurisdiction anchor that is too
        narrow for the live corpus (a small/emerging market, a niche
        regulatory topic) can starve every query built from it, and simply
        accepting "0-2 results" as final throws away whatever the general
        (ungeo-anchored) corpus would have returned. The broadened results
        are merged into the same dedup set, not returned instead of it, so
        a query that was merely thin (not zero) keeps its original, more
        specific hits ranked first.
        """
        try:
            searxng = self._get_tool("searxng")
            queries = await self._plan_queries(leg="searxng")
            # F-07c: broadened mode drops the geography anchor on the PRIMARY
            # pass (whole-corpus breadth around the main question) instead of
            # only on the low-yield retry.
            all_results = await self._fan_out_search(
                searxng.search, queries, 15, drop_geography=self.broadened
            )
            if not self.broadened and len(all_results) < self.LOW_YIELD_THRESHOLD:
                yield_before = len(all_results)
                broadened = await self._fan_out_search(
                    searxng.search, queries, 15, drop_geography=True
                )
                seen_urls = {getattr(r, "url", "") for r in all_results if getattr(r, "url", "")}
                for r in broadened:
                    url = getattr(r, "url", "") or ""
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    all_results.append(r)
                if len(all_results) > yield_before:
                    logger.info(
                        "SubAgent SearxNG low-yield retry for question=%r: "
                        "%d -> %d results after dropping geography",
                        self.spec.question[:120], yield_before, len(all_results),
                    )
            if all_results:
                formatted = "\n".join(
                    f"- {r.title}: {r.url}\n  {r.snippet[:500]}"
                    for r in all_results[:15]
                )
                urls = [r.url for r in all_results[:8] if r.url]
                return ("searxng", urls, f"SearxNG results:\n{formatted}")
        except Exception as e:
            # Fix 0.3: fail loud, not silent, a swallowed exception here
            # is exactly what hid the total sub-agent research outage in
            # HYPERION_DEEP_AUDIT_2026-07-27.md §0 / Finding B-1.
            logger.warning(
                "SubAgent SearxNG search failed for question=%r: %s",
                self.spec.question[:120], e, exc_info=True,
            )
        return ("searxng", [], None)

    async def _search_jina(self) -> tuple[str, list[str], str | None]:
        """Search via Jina s.jina.ai. Returns (label, urls, formatted_results).

        fix 1.3: same planner-driven query set as `_search_searxng`, see
        `_plan_queries`. The plan is cached by sub-question hash, so this
        leg and the SearxNG leg share one planner LLM call rather than
        paying for two.

        fix 1.4: same two-variant strategy as `_search_searxng`, see its
        docstring.

        fix 1.5: same low-yield broadened retry as `_search_searxng`, see
        its docstring.
        """
        try:
            jina = self._get_tool("jina")
            queries = await self._plan_queries(leg="jina")
            # F-07c: same broadened geography-drop rule as _search_searxng.
            all_results = await self._fan_out_search(
                jina.search, queries, 10, drop_geography=self.broadened
            )
            if not self.broadened and len(all_results) < self.LOW_YIELD_THRESHOLD:
                yield_before = len(all_results)
                broadened = await self._fan_out_search(
                    jina.search, queries, 10, drop_geography=True
                )
                seen_urls = {getattr(r, "url", "") for r in all_results if getattr(r, "url", "")}
                for r in broadened:
                    url = getattr(r, "url", "") or ""
                    if url and url in seen_urls:
                        continue
                    if url:
                        seen_urls.add(url)
                    all_results.append(r)
                if len(all_results) > yield_before:
                    logger.info(
                        "SubAgent Jina low-yield retry for question=%r: "
                        "%d -> %d results after dropping geography",
                        self.spec.question[:120], yield_before, len(all_results),
                    )
            if all_results:
                formatted = "\n".join(
                    f"- {r.title}: {r.url}\n  {r.snippet[:500]}"
                    for r in all_results[:10]
                )
                urls = [r.url for r in all_results[:6] if r.url]
                return ("jina", urls, f"Jina search results:\n{formatted}")
        except Exception as e:
            # Fix 0.3: fail loud, not silent, see note in _search_searxng.
            logger.warning(
                "SubAgent Jina search failed for question=%r: %s",
                self.spec.question[:120], e, exc_info=True,
            )
        return ("jina", [], None)

    def _has_tool(self, tool_name: str) -> bool:
        """Check if this sub-agent has access to a specific tool."""
        return any(t.value == tool_name for t in self.spec.tools)

    # ── P3 (overhaul §6 P3.1): retrieval-bound provenance ──────────────

    # P3: cap the EVIDENCE INDEX so a rich ledger cannot burn the analysis
    # token budget. First-seen records win (insertion order); a hard char
    # budget bounds the tail even when many records are short.
    EVIDENCE_INDEX_MAX_RECORDS = 40
    EVIDENCE_INDEX_MAX_CHARS = 6000

    def _evidence_index_block(self) -> str:
        """P3: the EVIDENCE INDEX block appended to the analysis prompt.

        Every ledger URL gets a stable citation ID (``E1``, ``E2``, ...) that
        the LLM is told to cite. Built from the run-scoped Evidence Ledger,
        never from the raw-data text, so an ID that appears in the prompt is
        guaranteed to resolve back to a retrieved URL. Bounded by
        :data:`EVIDENCE_INDEX_MAX_RECORDS` and :data:`EVIDENCE_INDEX_MAX_CHARS`
        so a 100-record engagement does not pay 20KB of index text per
        sub-agent analysis call.
        """
        try:
            ledger = get_evidence_ledger()
            records = list(ledger.all())[: self.EVIDENCE_INDEX_MAX_RECORDS]
        except Exception as exc:  # noqa: BLE001 - indexing must never break analysis
            logger.debug("evidence index unavailable: %s", exc)
            return ""
        if not records:
            return ""
        lines: list[str] = []
        used = 0
        for ev in records:
            eid = ledger.evidence_id_for(ev.url) or ""
            head = f"[{eid}] {ev.title or ev.url}"
            if ev.snippet:
                head += f" — {ev.snippet[:160]}"
            line = f"{head} — {ev.url}"
            if used + len(line) > self.EVIDENCE_INDEX_MAX_CHARS:
                break
            used += len(line)
            lines.append(line)
        return "EVIDENCE INDEX (cite ONLY these evidence IDs):\n" + "\n".join(lines)

    def _bind_sources(
        self,
        item: dict[str, Any],
        by_url: dict[str, Evidence],
    ) -> list[Source]:
        """P3: map the LLM's cited sources to ledger Evidence in code.

        A cited URL that does not resolve to a ledger record is DROPPED —
        the LLM can no longer mint, drop, or mangle URLs (invariant I-3).
        Sources are constructed from ``Evidence`` objects, never from the
        LLM's transcription. Never raises: binding failure means "no bound
        sources", which the caller types as ``unverified_assertion``.

        ``by_url`` is the normalized-URL → Evidence map built ONCE per
        analysis call (``_evidence_url_map``), so N findings do not rebuild
        the map N times.
        """
        try:
            ledger = get_evidence_ledger()
        except Exception as exc:  # noqa: BLE001 - binding must never raise
            logger.debug("evidence binding unavailable: %s", exc)
            return []

        bound: list[Source] = []
        seen: set[str] = set()
        raw_sources = item.get("sources")
        # Tolerate a single source object instead of a list.
        if isinstance(raw_sources, dict):
            raw_sources = [raw_sources]
        raw_sources = raw_sources if isinstance(raw_sources, list) else []
        for src in raw_sources:
            if not isinstance(src, dict):
                continue
            evidence = None
            evidence_id = ""
            # The prompt asks for ``[E1]``; strip any brackets the LLM echoes.
            cited_id = str(src.get("id") or "").strip().strip("[]")
            if cited_id:
                try:
                    evidence = ledger.by_evidence_id(cited_id)
                except Exception:  # noqa: BLE001 - a bad ID is a miss, not a crash
                    evidence = None
                if evidence is not None:
                    evidence_id = cited_id
            if evidence is None:
                url = str(src.get("url") or "").strip()
                if url:
                    evidence = by_url.get(_normalize_evidence_url(url))
                if evidence is not None:
                    try:
                        evidence_id = ledger.evidence_id_for(evidence.url)
                    except Exception:  # noqa: BLE001 - a ledger ID lookup failure must not drop a finding
                        evidence_id = ""
            if evidence is None:
                # Cited URL is not in the ledger — dropped (I-3).
                continue
            if evidence.url in seen:
                continue
            seen.add(evidence.url)
            bound.append(self._source_from_evidence(evidence, evidence_id))
        return bound

    def _evidence_url_map(self) -> dict[str, Evidence]:
        """P3: normalized-URL → Evidence map, built once per analysis call."""
        try:
            ledger = get_evidence_ledger()
            by_url: dict[str, Evidence] = {}
            for ev in ledger.all():
                by_url.setdefault(_normalize_evidence_url(ev.url), ev)
            return by_url
        except Exception as exc:  # noqa: BLE001 - binding must never raise
            logger.debug("evidence binding unavailable: %s", exc)
            return {}

    def _source_from_evidence(self, evidence: Evidence, evidence_id: str) -> Source:
        """P3: construct a ``Source`` from ledger Evidence in code.

        Credibility is derived from the URL by the source classifier — never
        transcribed by the LLM. The evidence ID is reused as the source id so
        citations stay traceable back into the ledger.
        """
        from hyperion.schemas.models import SourceCredibility, SourceType
        from hyperion.tools.source_classifier import classify_source_type

        source_type = classify_source_type(evidence.url)
        credibility = {
            SourceType.GOVERNMENT: SourceCredibility.GOVERNMENT,
            SourceType.ACADEMIC: SourceCredibility.PEER_REVIEWED,
            SourceType.INDUSTRY: SourceCredibility.INDUSTRY_REPORT,
            SourceType.NEWS: SourceCredibility.NEWS,
            SourceType.REFERENCE: SourceCredibility.NEWS,
            SourceType.BLOG: SourceCredibility.BLOG,
        }.get(source_type, SourceCredibility.BLOG)
        return Source(
            id=evidence_id or f"src_{evidence.url[:40]}",
            title=evidence.title or evidence.url,
            url=evidence.url,
            credibility=credibility,
            key_data=(evidence.snippet or "")[:300] or None,
        )

    async def _analyze_and_produce_findings(self, raw_data: str) -> list[KeyFinding]:
        self._ensure_counters()
        """Analyze raw data and produce structured KeyFinding objects.

        This is the analysis phase of the sub-agent lifecycle. The LLM
        at the specified tier processes the raw data and produces
        structured findings.

        The temperature is low (0.2) for structured output, we want
        deterministic, factual results, not creative writing.
        """
        import json

        system_prompt = compose_agent_prompt(self._build_system_prompt())
        user_prompt = self._build_user_prompt() + f"\n\nRaw data from tools:\n{raw_data}"
        index_block = self._evidence_index_block()
        if index_block:
            user_prompt += f"\n\n{index_block}"
        # P3: one URL→Evidence map for the whole analysis call, shared by
        # every finding's binding (no per-finding rebuild).
        by_url = self._evidence_url_map()

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        response: RouterResponse = await self.router.complete(
            tier=self.spec.model_tier,
            messages=messages,
            agent_name=f"subagent_{self.parent_agent}",
            urgency=TaskUrgency.LOW,  # Sub-agents are LOW urgency (§3.5)
            temperature=0.2,
            max_tokens=TIER_OUTPUT_BUDGET[self.spec.model_tier],
            response_format={"type": "json_object"},
        )

        provider = getattr(response.provider, "value", str(response.provider))
        await self.bus.publish(
            channel=Channel.TUI,
            msg_type=MessageType.STATUS,
            sender=self.spec.parent_agent,
            payload={
                "agent": self.parent_agent,
                "tool": "llm",
                "action": f"{provider}/{response.model}",
                "detail": (
                    f"{self.spec.model_tier.value} tier · "
                    f"{'OK' if response.success else 'FAIL'} · sub-agent"
                ),
                "success": response.success,
                "provider": provider,
                "input_tokens": max(0, int(response.input_tokens or 0)),
                "output_tokens": max(0, int(response.output_tokens or 0)),
                "total_tokens": max(0, int(response.total_tokens or 0)),
            },
        )

        if not response.success or not response.content:
            # F-07: a provider failure is a distinct outcome, never "the
            # world has no evidence". The gap produced by run() now carries
            # the reason, and ANALYSIS_FAILED is typed on the runner.
            self._ensure_counters().provider_failures += 1
            return []

        payload = response.content
        try:
            data = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            # F-07: one bounded format-repair attempt before accepting an
            # analysis failure. LLM JSON is frequently fenced or wrapped in
            # prose; the shared extractor recovers the balanced payload that
            # ``json.loads`` alone cannot see. If repair also fails, the run
            # is a typed ANALYSIS_FAILED — never a silent ``[]`` that the
            # caller mistakes for an empty world.
            from hyperion.router.structured_validator import extract_json

            repaired = extract_json(payload)
            if repaired is None:
                self._ensure_counters().invalid_findings += 1
                return []
            try:
                data = json.loads(repaired)
            except (json.JSONDecodeError, ValueError):
                self._ensure_counters().invalid_findings += 1
                return []

        # The LLM should return a JSON array of findings or an object
        # with a "findings" key
        if isinstance(data, list):
            findings_data = data
        elif isinstance(data, dict) and "findings" in data:
            findings_data = data["findings"]
        elif isinstance(data, dict):
            findings_data = [data]
        else:
            self._ensure_counters().invalid_findings += 1
            return []

        findings: list[KeyFinding] = []
        for item in findings_data:
            if not isinstance(item, dict):
                self._ensure_counters().invalid_findings += 1
                continue
            # P3 (I-3): the LLM's own ``sources`` are DISCARDED before
            # validation — a malformed or hallucinated source block must not
            # invalidate an otherwise valid finding, and provenance is bound
            # in code below from ledger evidence only.
            clean_item = {k: v for k, v in item.items() if k != "sources"}
            payload_type = str(item.get("finding_type") or "").strip()

            if payload_type in NON_SUBSTANTIVE_FINDING_TYPES:
                # P3: a gap/unverified payload is a separate typed object —
                # never counted as a finding. OVERHAUL2 S8: validating this
                # bare finding is safe here (non-substantive passes the
                # provenance validator unchanged).
                try:
                    finding = KeyFinding.model_validate(clean_item)
                except (ValueError, TypeError):
                    # F-07: invalid schema items are counted, not silently
                    # dropped. They are still excluded from substantive
                    # findings, but the count tells the operator the contract
                    # is broken.
                    self._ensure_counters().invalid_findings += 1
                    continue
                findings.append(finding)
                self._ensure_counters().gaps += 1
                continue

            # OVERHAUL2 S8: a substantive payload must NOT be validated as a
            # bare KeyFinding here — its sources were stripped for code-bound
            # attachment, and the provenance validator would retype it
            # ``unverified_assertion`` BEFORE the ledger-bound sources exist
            # (the Aug-10 "87 findings → 0 sources" mechanism). Bind first,
            # then validate the fully-attached object.
            bound = self._bind_sources(item, by_url)
            if bound:
                try:
                    findings.append(
                        EvidenceFinding.model_validate(
                            {
                                **clean_item,
                                "sources": [s.model_dump() for s in bound],
                            }
                        )
                    )
                    continue
                except (ValueError, TypeError):  # pragma: no cover - contract anomaly
                    # P3: a code-built EvidenceFinding cannot fail its own
                    # contract; count it as a validation anomaly if it ever
                    # does rather than silently dropping the claim.
                    self._ensure_counters().invalid_findings += 1
                    continue

            # P3: zero ledger-bound citations → typed unverified_assertion.
            # Never counted as yield, never rendered (I-3 / P3.1). Validating
            # the bare object here is correct: there is nothing to bind, so
            # S8's retype is the truthful final type.
            try:
                finding = KeyFinding.model_validate(clean_item)
            except (ValueError, TypeError):  # pragma: no cover - contract anomaly
                self._ensure_counters().invalid_findings += 1
                continue
            findings.append(
                finding.model_copy(
                    update={
                        "finding_type": UNVERIFIED_ASSERTION_TYPE,
                        "sources": [],
                    }
                )
            )
            self._ensure_counters().unverified_assertions += 1

        self._ensure_counters().valid_findings = sum(
            1
            for f in findings
            if f.finding_type not in NON_SUBSTANTIVE_FINDING_TYPES
        )
        return findings

    def gap_finding(self, reason: str, elapsed: float) -> KeyFinding:
        """Build the explicit evidence gap returned on any research failure."""
        from hyperion.schemas.models import ConfidenceLevel

        return KeyFinding(
            id=f"gap_{self.parent_agent}_{time.time_ns()}",
            agent=self.parent_agent,
            finding_type="research_gap",
            title=f"Research gap: {self.spec.question[:100]}",
            content=(
                f"Sub-agent could not complete this research question: {reason}. "
                f"Tools attempted: {', '.join(self.tools) or 'none'}. "
                f"Time elapsed: {elapsed:.1f}s. No factual claim should be "
                "inferred from this missing evidence."
            ),
            sources=[],
            confidence=ConfidenceLevel.LOW,
            gaps=[self.spec.question],
        )

    def labeled_estimate_finding(self, elapsed: float) -> KeyFinding:
        """F-0.1-7: closure contract — never ship a blank cell.

        When a QUANTITATIVE question (pricing/sizing/funding) has no public
        data, the deliverable is a LABELED ESTIMATE, not a gap: benchmark the
        question against comparable public peers, stamp confidence=low +
        assumption=analog_estimate, and surface "data not publicly available"
        as a limitation. This is the difference between a consultant-grade
        answer and a wrapper that returns "no data".
        """
        from hyperion.schemas.models import ConfidenceLevel

        return KeyFinding(
            id=f"estimate_{self.parent_agent}_{time.time_ns()}",
            agent=self.parent_agent,
            finding_type="analog_estimate",
            title=f"Labeled estimate (no public data): {self.spec.question[:100]}",
            content=(
                f"Quantitative data for this question is not publicly available "
                f"({self.spec.question[:120]}). Rather than a blank cell, this "
                f"answer is a LABELED ANALOG ESTIMATE: benchmark against 2-3 "
                f"comparable public peers and apply the closest analog's range. "
                f"This is an assumption with confidence=LOW, NOT a sourced "
                f"figure. Surface 'data not publicly available' in the report "
                f"limitations. Tools attempted: {', '.join(self.tools) or 'none'}; "
                f"elapsed {elapsed:.1f}s."
            ),
            sources=[],
            confidence=ConfidenceLevel.LOW,
            gaps=[self.spec.question],
        )

    async def run(self) -> list[KeyFinding]:
        """Execute the sub-agent research task.

        This is the full sub-agent lifecycle:
        1. Gather raw data using available tools
        2. Analyze the data and produce structured findings
        3. Return findings to the parent specialist

        The parent specialist synthesizes these findings into its own
        analysis. The parent's context window is used for synthesis,
        not for raw research. This is the context isolation strategy
        (§4.7).

        The 5-minute timeout is enforced by the parent via
        asyncio.wait_for in BaseAgent._spawn_sub_agent. F-06: search gets
        its OWN sub-budget (70% of the spec timeout) and the analysis LLM
        call gets the remainder, so a stuck search phase can never eat the
        LLM's time budget.
        """
        start = time.time()
        search_timed_out = False
        analysis_timed_out = False

        # Phase 1: Gather raw data — search fan-out + extraction. On a dead
        # pool this phase alone can exceed the whole budget before the LLM
        # ever runs; bound it to 70% of the sub-agent's wall-clock.
        search_budget = max(60, int(self.spec.timeout_seconds * 0.7))
        try:
            raw_data = await asyncio.wait_for(
                self._gather_raw_data(), timeout=search_budget
            )
        except TimeoutError:
            search_timed_out = True
            raw_data = (
                "No raw data available from tools — the search phase exceeded "
                f"its {search_budget}s budget (dead engine pool or slow "
                "extraction)."
            )

        # F-0.1-5: sufficiency gate. A pricing/fetch task that extracted
        # content but found no pricing artifacts is NOT a success — it feeds
        # the fallback routes instead of burning the analysis LLM on a thin
        # fetch. The gate stamps counters.sufficiency_failed; run() types the
        # outcome accordingly (FETCH_INSUFFICIENT path below).
        self._check_sufficiency(
            [raw_data] if raw_data else [], self.spec.question
        )

        # Phase 2: Analyze and produce structured findings — the LLM call
        # always gets at least the remainder of the budget.
        analysis_budget = max(60, self.spec.timeout_seconds - search_budget)
        try:
            findings = await asyncio.wait_for(
                self._analyze_and_produce_findings(raw_data),
                timeout=analysis_budget,
            )
        except TimeoutError:
            analysis_timed_out = True
            findings = []

        elapsed = time.time() - start

        # F-01: type the outcome from what actually happened, BEFORE the
        # synthetic gap is appended. ``len(findings)`` is not evidence yield;
        # the parent reads ``runner.outcome`` and ``runner.counters`` instead.
        # P3: SUCCESS requires at least one SUBSTANTIVE (ledger-bound)
        # finding — a list containing only gaps or only unverified_assertions
        # has no citable evidence and is typed below, never a fake success.
        if any(
            f.finding_type not in NON_SUBSTANTIVE_FINDING_TYPES
            for f in findings
        ):
            self.outcome = ResearchOutcome.SUCCESS
            self.recovery_hint = "SUCCESS"
        elif self.broadened:
            # F-01/F-02: this pass already IS the one permitted broadened
            # respawn (parent spawns it with ``broadened=True``). If it still
            # produced no findings, every recovery path is spent — a typed
            # RETRY_EXHAUSTED terminal state, never a fake success.
            self.outcome = ResearchOutcome.RETRY_EXHAUSTED
            self.recovery_hint = "RETRY_EXHAUSTED"
        elif search_timed_out or analysis_timed_out:
            self.outcome = ResearchOutcome.TIMEOUT
            self.recovery_hint = "TIMEOUT"
        elif self._ensure_counters().provider_failures > 0 or self._ensure_counters().invalid_findings > 0:
            self.outcome = ResearchOutcome.ANALYSIS_FAILED
            self.recovery_hint = "PROVIDER_FAILURE"
        elif self._ensure_counters().sufficiency_failed:
            # F-0.1-5: content was extracted but a pricing/fetch sufficiency
            # gate failed — the extraction is thin for what the question asked.
            # Typed distinctly from NO_EVIDENCE so the parent's failure-class
            # respawn (F-0.1-10) can route to the fallback routes rather than
            # re-broadening a search.
            self.outcome = ResearchOutcome.ANALYSIS_FAILED
            self.recovery_hint = "FETCH_INSUFFICIENT"
        elif self._ensure_counters().raw_results == 0 or self._ensure_counters().extracted_documents == 0:
            # Nothing came back from retrieval at all — either the pool is
            # degraded (dead/cooled engines, budget exhaustion) or the world
            # has no evidence. Check the engine-health telemetry to pick the
            # honest label.
            try:
                from hyperion.tools.engine_health import get_engine_health

                degraded = bool(get_engine_health().degradation_events())
            except Exception:  # noqa: BLE001 - telemetry must not break typing
                degraded = False
            if degraded:
                self.outcome = ResearchOutcome.RETRIEVAL_DEGRADED
                self.recovery_hint = "ENGINE_BLOCKED"
            else:
                self.outcome = ResearchOutcome.NO_EVIDENCE
                self.recovery_hint = "LOW_YIELD"
        else:
            self.outcome = ResearchOutcome.NO_EVIDENCE
            self.recovery_hint = "LOW_YIELD"

        # If no findings were produced, return a gap finding. The gap is a
        # first-class citizen in the counters, never a fake "1 finding".
        if not findings:
            reason = (
                "search phase timed out" if search_timed_out else
                "analysis phase timed out" if analysis_timed_out else
                "retrieval or LLM analysis returned no validated findings"
            )
            findings = [
                self.gap_finding(reason, elapsed)
            ]
            # F-0.1-7: closure contract. A QUANTITATIVE question (pricing,
            # sizing, funding) with no public data must never ship a blank
            # cell — attach the labeled-estimate finding so the report carries
            # an analog benchmark with confidence=LOW, not a gap-only blank.
            if self._is_quantitative_question() and not search_timed_out:
                findings.append(self.labeled_estimate_finding(elapsed))
            self._ensure_counters().gaps = 1

        return findings

    def _is_quantitative_question(self) -> bool:
        """F-0.1-7: True when the sub-question asks for a number/price/size."""
        q = (getattr(self.spec, "question", "") or "").lower()
        tokens = (
            "pricing", "price", "cost", "tam", "sam", "som", "market size",
            "funding", "raised", "valuation", "revenue", "how much",
            "per month", "per year", "growth rate", "cagr", "headcount",
        )
        return any(tok in q for tok in tokens)
