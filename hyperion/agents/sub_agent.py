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
from hyperion.schemas.models import CompetitorDossier, KeyFinding
from hyperion.tools.content_selector import select_content

# Maps the `findings_model` string in a SubAgentSpec to the Pydantic class the
# sub-agent must produce. The default (KeyFinding) preserves the historical
# behaviour; richer models let a parent agent receive structured output
# (e.g. a CompetitorDossier) instead of free-text KeyFindings, so downstream
# aggregation can be pure functions rather than more LLM calls.
_FINDINGS_MODEL_REGISTRY: dict[str, type] = {
    "KeyFinding": KeyFinding,
    "CompetitorDossier": CompetitorDossier,
}

logger = logging.getLogger(__name__)

# Fix 2.2 (§4.7 Finding B-6): the retained-content budget for a fetched SEC
# filing. Same number as before; what changed is that the budget is now filled
# by relevance to the sub-question instead of by position in the document.
SEC_FILING_BUDGET_CHARS = 15000


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
    """

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

    @property
    def question(self) -> str:
        return self.spec.question

    @property
    def parent_agent(self) -> str:
        return self.spec.parent_agent.value

    @property
    def tier(self) -> ModelTier:
        return self.spec.model_tier

    @property
    def tools(self) -> list[str]:
        return [t.value for t in self.spec.tools]

    def _findings_model_class(self) -> type:
        """Resolve the Pydantic class named by ``spec.findings_model``.

        Falls back to KeyFinding for any unknown/model-less spec, so the
        historical behaviour is fully preserved.
        """
        return _FINDINGS_MODEL_REGISTRY.get(self.spec.findings_model, KeyFinding)

    def _findings_model_hint(self) -> str:
        """Prompt appendix describing the expected structured output schema."""
        model_cls = self._findings_model_class()
        if model_cls is KeyFinding:
            return ""
        if model_cls is CompetitorDossier:
            return (
                "\n\n10. DO NOT return a KeyFinding. Instead return a SINGLE JSON "
                "object matching the CompetitorDossier schema:\n"
                "  - name: competitor's name (string)\n"
                "  - website: primary website URL (string or null)\n"
                "  - pricing_tiers: array of strings describing pricing plans\n"
                "  - funding_stage: string (bootstrapped/seed/A/B/C/IPO/revenue-funded)\n"
                "  - total_raised: string (e.g. '$120M') or null\n"
                "  - headcount: string (approximate employee count) or null\n"
                "  - key_partnerships: array of strings\n"
                "  - moat_signals: array of strings describing defensibility signals\n"
                "  - evidence_urls: array of source URLs backing the dossier\n"
                "Return ONLY that JSON object (no findings wrapper)."
            )
        # Generic fallback for any future richer model: name the model and let
        # the model follow its own schema by class name.
        return (
            f"\n\n10. Return a SINGLE JSON object matching the {model_cls.__name__} "
            "schema (no findings wrapper)."
        )

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
            return SearxNGClient(settings=settings)
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
            "  - gaps: array of strings describing what you couldn't find"
            + self._findings_model_hint()
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

        # Merge + dedup URLs from both search sources (preserves order)
        all_urls = list(dict.fromkeys(searxng_urls + jina_search_urls))

        # ── EXTRACTION (fix 2.1: the single UnifiedExtract ladder) ──────
        # Fix 2.2: the sub-question is passed down so the ladder fills its
        # retained-content budget by relevance rather than by document position.
        if all_urls:
            extracted, extract_errors = await self._extract_urls(
                all_urls, self.spec.question
            )
            raw_data.extend(extracted)
            errors.extend(extract_errors)

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
        """Extraction tiers this sub-agent is entitled to use, in ladder order.

        Ordering is left to :class:`UnifiedExtract`, this returns a *set* of
        permitted tiers, and the ladder normalises them back into its own
        cheap-first order so a sub-agent cannot accidentally promote a browser
        tier ahead of a free one.
        """
        tiers = list(self.ALWAYS_AVAILABLE_EXTRACT_TIERS)
        tiers += [
            tier
            for tier, tool in self.EXTRACT_TIER_TOOLS.items()
            if self._has_tool(tool)
        ]
        return tiers

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
        targets = urls[: self.MAX_EXTRACT_URLS]
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

        if not outcome.results:
            for tier, why in outcome.errors.items():
                errors.append(f"{tier}: {why!s:.80}")
            for tier, why in outcome.tiers_unavailable.items():
                errors.append(f"{tier} unavailable: {why!s:.80}")

        logger.info(
            "SubAgent extraction: %d/%d URL(s) extracted via %s (tried: %s)",
            len(outcome.results),
            len(targets),
            ", ".join(outcome.tools_used) or "none",
            ", ".join(outcome.tools_tried) or "none",
        )
        return (raw_data, errors)

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

    async def _fan_out_search(
        self,
        search_fn: Any,
        queries: list[str],
        num_results: int,
        *,
        drop_geography: bool = False,
    ) -> list[Any]:
        """Run ``search_fn`` over each query variant, merging results and
        deduplicating by URL (first-seen order preserved).

        Shared by `_search_searxng`/`_search_jina` for both the normal pass
        and the fix-1.5 low-yield broadened retry, so the two callers cannot
        drift on how variants are merged.
        """
        merged: list[Any] = []
        seen_urls: set[str] = set()
        for query in queries:
            kwargs: dict[str, Any] = {"num_results": num_results}
            if drop_geography:
                kwargs["drop_geography"] = True
            variant_results = await search_fn(query, **kwargs)
            for r in (variant_results or []):
                url = getattr(r, "url", "") or ""
                if url and url in seen_urls:
                    continue
                if url:
                    seen_urls.add(url)
                merged.append(r)
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
            all_results = await self._fan_out_search(searxng.search, queries, 15)
            if len(all_results) < self.LOW_YIELD_THRESHOLD:
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
            all_results = await self._fan_out_search(jina.search, queries, 10)
            if len(all_results) < self.LOW_YIELD_THRESHOLD:
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

    async def _analyze_and_produce_findings(self, raw_data: str) -> list[Any]:
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
            return []

        model_cls = self._findings_model_class()

        # Richer structured models (e.g. CompetitorDossier) are returned as a
        # single JSON object (or a wrapping dict/list), not the KeyFinding
        # schema. Validate directly and return them so the parent agent can
        # aggregate without another LLM call.
        if model_cls is not KeyFinding:
            try:
                data = json.loads(response.content)
            except (json.JSONDecodeError, ValueError):
                return []
            return self._parse_rich_model(data, model_cls)

        try:
            data = json.loads(response.content)

            # The LLM should return a JSON array of findings or an object
            # with a "findings" key
            if isinstance(data, list):
                findings_data = data
            elif isinstance(data, dict) and "findings" in data:
                findings_data = data["findings"]
            elif isinstance(data, dict):
                findings_data = [data]
            else:
                return []

            findings: list[KeyFinding] = []
            for item in findings_data:
                try:
                    finding = KeyFinding.model_validate(item)
                    findings.append(finding)
                except (ValueError, TypeError):
                    continue

            return findings

        except (json.JSONDecodeError, ValueError):
            return []

    def _parse_rich_model(self, data: Any, model_cls: type) -> list[Any]:
        """Extract and validate richer findings-model objects from parsed JSON.

        A sub-agent asked for a CompetitorDossier returns a single JSON object
        (possibly wrapped in a dict keyed by the model name, "dossiers", etc.,
        or as a one-element list). Normalise all of those into a list of
        validated model instances; malformed items are dropped, never invented.
        """
        if isinstance(data, list):
            items: list[Any] = data
        elif isinstance(data, dict):
            for key in (model_cls.__name__, "dossiers", "items", "results", "findings"):
                if key in data and isinstance(data[key], list):
                    items = data[key]
                    break
            else:
                # A bare dossier object (top-level dict with the model's fields).
                items = [data]
        else:
            return []

        parsed: list[Any] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(model_cls.model_validate(item))
            except (ValueError, TypeError):
                continue
        return parsed

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

    async def run(self) -> list[Any]:
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
        asyncio.wait_for in BaseAgent._spawn_sub_agent.
        """
        start = time.time()

        # Phase 1: Gather raw data
        raw_data = await self._gather_raw_data()

        # Phase 2: Analyze and produce structured findings
        findings = await self._analyze_and_produce_findings(raw_data)

        elapsed = time.time() - start

        # If no findings were produced, return a gap finding
        if not findings:
            findings = [
                self.gap_finding(
                    "retrieval or LLM analysis returned no validated findings",
                    elapsed,
                )
            ]

        return findings
