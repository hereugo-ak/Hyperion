"""
HYPERION Sub-Agent Runner — junior agent execution for context isolation.

This is NOT a generic sub-agent class. It is the mechanism that makes
HYPERION fundamentally different from a single-LLM system (§4.7).

A specialist hits a context window limit and needs deeper research.
Instead of truncating or compressing (which loses detail), the specialist
delegates: it sends a focused sub-question to a junior sub-agent, the
sub-agent does focused research in its own context window, and returns
structured findings (data, sources, confidence, gaps). The parent
synthesizes. The parent's context window is used for synthesis, not
for raw research.

This is how real consulting teams work — a partner doesn't read 200
pages of raw research. They read a senior associate's 5-page summary.
HYPERION's specialists are partners; sub-agents are associates.

Rules (§4.7):
- Max 3 sub-agents per specialist per engagement (enforced in BaseAgent)
- Sub-agents use MICRO or FAST tier only (don't burn STRONG/DEEP quota)
- Sub-agent findings are structured (KeyFinding), not free text
- Parent specialist receives structured findings and synthesizes them
- Sub-agents have 5-minute timeout — if a sub-agent doesn't return in
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
import re
import time
from typing import Any

from hyperion.agents.bus import AgentBus, get_bus
from hyperion.config import ModelTier
from hyperion.router.budget import TaskUrgency
from hyperion.router.providers.base import RouterResponse
from hyperion.router.router import LLMRouter, get_router
from hyperion.schemas.agents import SubAgentSpec
from hyperion.schemas.models import KeyFinding


class SubAgentRunner:
    """Executes a single sub-agent research task and returns structured findings.

    This is NOT a full agent — it has no bus subscription, no state
    management, no sub-agent spawning capability. It is a focused
    research executor that:

    1. Takes a SubAgentSpec (question, tier, tools, findings_model)
    2. Constructs a system prompt appropriate for a junior researcher
    3. Uses the specified tools to gather data
    4. Calls the LLM at the specified tier (MICRO or FAST)
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

        # Validate tier constraint (§4.7)
        if spec.model_tier not in (ModelTier.MICRO, ModelTier.FAST):
            raise ValueError(
                f"Sub-agent tier must be MICRO or FAST, got {spec.model_tier.value}. "
                f"Sub-agents don't burn STRONG/DEEP quota (§4.7)."
            )

        # Tool instances — only the subset specified in the spec
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

        return self._tools[tool_name]

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
            "4. Identify GAPS — what you couldn't find, what data is missing.\n"
            "5. Be DETAILED and SPECIFIC. Include exact numbers, percentages, "
            "dollar figures, dates, and company names. Vague findings are useless.\n"
            "6. Each finding's content should be 200-500 words of detailed analysis "
            "with specific data points, not a one-sentence summary.\n"
            "7. Use the tools available to you: {tools}.\n"
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
            "  - agent: '{parent}'\n"
            "  - finding_type: the type (e.g., 'market_data', 'competitor_info')\n"
            "  - title: short title for display\n"
            "  - content: the specific finding with data and evidence\n"
            "  - sources: array of source objects with id, title, url, credibility "
            "(one of: peer_reviewed, government, industry_report, news, blog, social_media)\n"
            "  - confidence: 'high', 'medium', or 'low'\n"
            "  - gaps: array of strings describing what you couldn't find"
        ).format(tools=tool_names, parent=self.parent_agent)

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
        - Extract: Obscura → Scrapling → Jina Reader → Crawl4AI → FlareSolverr
        - Historical: Wayback Machine
        - Financial: Alpha Vantage
        - Macro: FRED
        - Prior research: Second Brain
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

        # ── EXTRACTION (VIGIL fallback chain) ───────────────────────────
        # Obscura → Scrapling → Jina Reader → Crawl4AI → FlareSolverr
        extracted_urls: set[str] = set()

        # Tier 1: Obscura (stealth, fast, JS rendering)
        if self._has_tool("obscura") and all_urls:
            try:
                obscura = self._get_tool("obscura")
                for url in all_urls[:6]:
                    if url in extracted_urls:
                        continue
                    try:
                        fetch_result = await obscura.fetch(url)
                        if fetch_result and (fetch_result.markdown or fetch_result.content):
                            text = (fetch_result.markdown or fetch_result.content)[:15000]
                            raw_data.append(f"Obscura content from {url}:\n{text}")
                            extracted_urls.add(url)
                    except Exception:
                        continue
            except Exception as e:
                errors.append(f"Obscura: {e!s:.80}")

        # Tier 2: Scrapling (adaptive, anti-bot, Playwright)
        if self._has_tool("scrapling") and all_urls:
            try:
                scrapling = self._get_tool("scrapling")
                for url in all_urls[:6]:
                    if url in extracted_urls:
                        continue
                    try:
                        scrape_result = await scrapling.fetch(url, stealth=True)
                        if scrape_result and scrape_result.content:
                            text = scrape_result.content[:15000]
                            raw_data.append(f"Scrapling content from {url}:\n{text}")
                            extracted_urls.add(url)
                    except Exception:
                        continue
            except Exception as e:
                errors.append(f"Scrapling: {e!s:.80}")

        # Tier 3: Jina Reader (fast, simple extraction)
        if self._has_tool("jina") and all_urls:
            try:
                jina = self._get_tool("jina")
                for url in all_urls[:8]:
                    if url in extracted_urls:
                        continue
                    try:
                        read_result = await jina.read(url)
                        if read_result and (read_result.markdown or read_result.content):
                            text = (read_result.markdown or read_result.content)[:15000]
                            raw_data.append(f"Jina content from {url}:\n{text}")
                            extracted_urls.add(url)
                    except Exception:
                        continue
            except Exception as e:
                errors.append(f"Jina: {e!s:.80}")

        # Tier 4: Crawl4AI (heavy extraction, PDFs)
        if self._has_tool("crawl4ai") and all_urls:
            try:
                crawl4ai = self._get_tool("crawl4ai")
                for url in all_urls[:4]:
                    if url in extracted_urls:
                        continue
                    try:
                        crawl_result = await crawl4ai.crawl(url)
                        if crawl_result and (crawl_result.markdown or crawl_result.content):
                            text = (crawl_result.markdown or crawl_result.content)[:15000]
                            raw_data.append(f"Crawl4AI content from {url}:\n{text}")
                            extracted_urls.add(url)
                    except Exception:
                        continue
            except Exception as e:
                errors.append(f"Crawl4AI: {e!s:.80}")

        # Tier 5: FlareSolverr (CAPTCHA-protected pages)
        if self._has_tool("flaresolverr") and all_urls:
            try:
                from hyperion.tools.flaresolverr import FlareSolverrClient
                flare = FlareSolverrClient()
                for url in all_urls[:3]:
                    if url in extracted_urls:
                        continue
                    try:
                        flare_result = await flare.get(url)
                        if flare_result and flare_result.success and flare_result.html:
                            import re
                            text = re.sub(r"<[^>]+>", " ", flare_result.html)
                            text = re.sub(r"\s+", " ", text).strip()[:15000]
                            if text and len(text) > 100:
                                raw_data.append(f"FlareSolverr content from {url}:\n{text}")
                                extracted_urls.add(url)
                    except Exception:
                        continue
                await flare.close()
            except Exception as e:
                errors.append(f"FlareSolverr: {e!s:.80}")

        # ── DATA SOURCES (unchanged) ────────────────────────────────────

        # Historical data — Wayback
        if self._has_tool("wayback"):
            try:
                wayback = self._get_tool("wayback")
                snapshots = await wayback.search(self._condense_query(self.spec.question))
                if snapshots:
                    raw_data.append(f"Historical snapshots:\n{snapshots}")
            except Exception as e:
                errors.append(f"Wayback: {e!s:.80}")

        # Financial data — Alpha Vantage
        if self._has_tool("alpha_vantage"):
            try:
                av = self._get_tool("alpha_vantage")
                financials = await av.search(self._condense_query(self.spec.question))
                if financials:
                    raw_data.append(f"Financial data:\n{financials}")
            except Exception as e:
                errors.append(f"AlphaVantage: {e!s:.80}")

        # Macro data — FRED
        if self._has_tool("fred"):
            try:
                fred = self._get_tool("fred")
                macro = await fred.search(self._condense_query(self.spec.question))
                if macro:
                    raw_data.append(f"Macro data:\n{macro}")
            except Exception as e:
                errors.append(f"FRED: {e!s:.80}")

        # ── Phase 2 Data Sources ────────────────────────────────────────

        # SEC EDGAR — financial filings
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
                        raw_data.append(f"SEC filing content ({filings[0].filing_type} {filings[0].company_name}):\n{content.content[:15000]}")
            except Exception as e:
                errors.append(f"SEC EDGAR: {e!s:.80}")

        # Semantic Scholar — academic papers
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
            except Exception as e:
                errors.append(f"SemanticScholar: {e!s:.80}")

        # OpenAlex — scholarly works
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
            except Exception as e:
                errors.append(f"OpenAlex: {e!s:.80}")

        # World Bank — macro indicators
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
            except Exception as e:
                errors.append(f"WorldBank: {e!s:.80}")

        # Google Trends — demand signals
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
            except Exception as e:
                errors.append(f"GoogleTrends: {e!s:.80}")

        # HackerNews — tech community sentiment
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
            except Exception as e:
                errors.append(f"HackerNews: {e!s:.80}")

        # Reddit — community sentiment
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
            except Exception as e:
                errors.append(f"Reddit: {e!s:.80}")

        # Second Brain — prior research
        if self._has_tool("second_brain"):
            try:
                brain = self._get_tool("second_brain")
                prior = await brain.search(self._condense_query(self.spec.question))
                if prior:
                    raw_data.append(f"Prior research from vault:\n{prior}")
            except Exception as e:
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
        q = re.sub(r'\([^)]*\)', '', q)

        # Remove em-dashes and everything after them (usually instructions)
        q = re.sub(r'\s*[\u2014\u2013--]+\s*', ' ', q)

        # Remove filler words
        filler = {
            'the', 'a', 'an', 'for', 'of', 'to', 'in', 'on', 'at', 'by',
            'with', 'from', 'about', 'into', 'through', 'during', 'before',
            'after', 'above', 'below', 'between', 'under', 'further',
            'then', 'once', 'here', 'there', 'when', 'where', 'why',
            'how', 'all', 'any', 'both', 'each', 'few', 'more', 'most',
            'other', 'some', 'such', 'no', 'nor', 'not', 'only', 'own',
            'same', 'so', 'than', 'too', 'very', 'can', 'will', 'just',
            'should', 'now', 'is', 'are', 'was', 'were', 'be', 'been',
            'being', 'have', 'has', 'had', 'do', 'does', 'did', 'would',
            'could', 'may', 'might', 'must', 'shall', 'this', 'that',
            'these', 'those', 'i', 'you', 'he', 'she', 'it', 'we', 'they',
            'what', 'which', 'who', 'whom', 'whose', 'and', 'or', 'but',
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

    async def _search_searxng(self) -> tuple[str, list[str], str | None]:
        """Search via SearxNG. Returns (label, urls, formatted_results)."""
        try:
            searxng = self._get_tool("searxng")
            query = self._condense_query(self.spec.question)
            results = await searxng.search(query, num_results=15)
            if results and len(results) > 0:
                formatted = "\n".join(
                    f"- {r.title}: {r.url}\n  {r.snippet[:500]}"
                    for r in results[:15]
                )
                urls = [r.url for r in results[:8] if r.url]
                return ("searxng", urls, f"SearxNG results:\n{formatted}")
        except Exception as e:
            pass
        return ("searxng", [], None)

    async def _search_jina(self) -> tuple[str, list[str], str | None]:
        """Search via Jina s.jina.ai. Returns (label, urls, formatted_results)."""
        try:
            jina = self._get_tool("jina")
            query = self._condense_query(self.spec.question)
            results = await jina.search(query, num_results=10)
            if results and len(results) > 0:
                formatted = "\n".join(
                    f"- {r.title}: {r.url}\n  {r.snippet[:500]}"
                    for r in results[:10]
                )
                urls = [r.url for r in results[:6] if r.url]
                return ("jina", urls, f"Jina search results:\n{formatted}")
        except Exception as e:
            pass
        return ("jina", [], None)

    def _has_tool(self, tool_name: str) -> bool:
        """Check if this sub-agent has access to a specific tool."""
        return any(t.value == tool_name for t in self.spec.tools)

    async def _analyze_and_produce_findings(self, raw_data: str) -> list[KeyFinding]:
        """Analyze raw data and produce structured KeyFinding objects.

        This is the analysis phase of the sub-agent lifecycle. The LLM
        at the specified tier processes the raw data and produces
        structured findings.

        The temperature is low (0.2) for structured output — we want
        deterministic, factual results, not creative writing.
        """
        import json

        system_prompt = self._build_system_prompt()
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
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return []

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
            from hyperion.schemas.models import ConfidenceLevel
            findings = [
                KeyFinding(
                    id=f"gap_{self.parent_agent}_{int(time.time())}",
                    agent=self.parent_agent,
                    finding_type="research_gap",
                    title=f"Research gap: {self.spec.question[:100]}",
                    content=(
                        f"Sub-agent was unable to find data for this sub-question. "
                        f"This is a research gap that should be flagged to the parent. "
                        f"Tools used: {', '.join(self.tools)}. "
                        f"Time elapsed: {elapsed:.1f}s."
                    ),
                    sources=[],
                    confidence=ConfidenceLevel.LOW,
                    gaps=[self.spec.question],
                )
            ]

        return findings
