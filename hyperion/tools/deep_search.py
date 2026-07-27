"""
HYPERION DeepSearch Client — unified search orchestration (VIGIL Layer 5).

This is VIGIL's key innovation — a single tool that wraps the entire
search → extract → score pipeline. Agents call deep_search(query, depth)
instead of individually invoking SearxNG, Jina, Obscura, Scrapling, etc.

Pipeline:
  1. Parallel discovery (SearxNG + Jina Search)
  2. URL dedup + ranking by source credibility
  3. Extraction (Jina Reader → HTTP Extract → Obscura → Crawl4AI → FlareSolverr)
  4. Evidence scoring (support/conflict/neutral heuristic)
  5. Result ranking by relevance + evidence score + freshness
  6. Return ranked, cited markdown

This is NOT a generic "search and summarize" wrapper. It implements the
exact VIGIL-aligned fallback chain from §5.2/§5.3 and integrates the
heuristic EvidenceScorer (Step 1.8) — no pgvector, no Ollama, no new
infrastructure.

Architecture reference: §5.1 — "Unified search orchestration. Wraps
discovery → extraction → scoring into one call."

Tool selection logic (§5.2 updated):
  Search: SearxNG + Jina Search in parallel (discovery layer)
  Extract: Jina Reader → HTTP Extract → Obscura → Crawl4AI → FlareSolverr
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from hyperion.tools.evidence_scorer import EvidenceScorer, EvidenceSummary, ScoredResult

logger = logging.getLogger(__name__)

# Content truncation — 15000 chars per source (Step 1.6)
MAX_CONTENT_CHARS = 15000

# Cache TTL — 1 hour
CACHE_TTL_SECONDS = 3600


# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class DeepSearchResult:
    """Result of a deep search operation.

    Contains ranked extraction results, an evidence summary, the source
    list with credibility scores, and all discovered URLs for further
    scraping if needed.
    """

    query: str = ""
    depth: str = "standard"
    ranked_results: list[ScoredResult] = field(default_factory=list)
    evidence_summary: EvidenceSummary = field(default_factory=EvidenceSummary)
    sources: list[dict[str, Any]] = field(default_factory=list)
    raw_urls: list[str] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    total_discovered: int = 0
    total_extracted: int = 0
    took_ms: int = 0
    cached: bool = False
    # Every extraction tier we attempted, in ladder order. Distinct from
    # ``tools_used``, which lists only tiers that produced usable content.
    tools_tried: list[str] = field(default_factory=list)
    # Why a tier or phase produced nothing. Discovery and extraction failures
    # were previously only logged at debug/warning level, so a caller holding
    # an empty DeepSearchResult had no way to distinguish "SearxNG is down"
    # from "this query genuinely has no sources".
    errors: dict[str, str] = field(default_factory=dict)
    # Tiers skipped because they cannot run in this environment at all.
    tiers_unavailable: dict[str, str] = field(default_factory=dict)
    # Human-readable roll-up. Empty when results were found.
    error: str = ""

    @property
    def success(self) -> bool:
        return bool(self.ranked_results)

    def to_markdown(self) -> str:
        """Render the result as cited markdown for agent consumption.

        Each result is formatted with its rank, title, URL, stance,
        composite score, and truncated content. The evidence summary
        is prepended as a header.
        """
        lines: list[str] = []

        # Evidence summary header
        summary = self.evidence_summary
        lines.append(f"# Deep Search: {self.query}")
        lines.append(f"**Depth**: {self.depth} | **Sources**: {self.total_extracted} extracted / {self.total_discovered} discovered")
        lines.append(f"**Evidence**: {summary.overall_stance} (support={summary.support_count}, conflict={summary.conflict_count}, neutral={summary.neutral_count}, confidence={summary.confidence:.2f})")
        lines.append("")

        # Key findings
        if summary.key_findings:
            lines.append("## Key Findings")
            for finding in summary.key_findings:
                lines.append(f"- {finding}")
            lines.append("")

        # Ranked results
        lines.append("## Ranked Results")
        for i, result in enumerate(self.ranked_results, 1):
            lines.append(f"### {i}. {result.title or 'Untitled'}")
            lines.append(f"- **URL**: {result.url}")
            lines.append(f"- **Tool**: {result.tool_used}")
            lines.append(f"- **Stance**: {result.stance}")
            lines.append(f"- **Scores**: relevance={result.relevance_score:.2f}, credibility={result.credibility_score:.2f}, freshness={result.freshness_score:.2f}, evidence={result.evidence_score:.2f}, composite={result.composite_score:.2f}")
            if result.published_date:
                lines.append(f"- **Published**: {result.published_date}")
            content = result.content or result.markdown or ""
            if content:
                lines.append("")
                lines.append(content[:MAX_CONTENT_CHARS])
            lines.append("")
            lines.append("---")
            lines.append("")

        return "\n".join(lines)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "depth": self.depth,
            "ranked_results": [r.to_dict() for r in self.ranked_results],
            "evidence_summary": self.evidence_summary.to_dict(),
            "sources": self.sources,
            "raw_urls": self.raw_urls,
            "tools_used": self.tools_used,
            "total_discovered": self.total_discovered,
            "total_extracted": self.total_extracted,
            "took_ms": self.took_ms,
            "cached": self.cached,
            "tools_tried": self.tools_tried,
            "errors": self.errors,
            "tiers_unavailable": self.tiers_unavailable,
            "error": self.error,
            "success": self.success,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Extracted Content — intermediate representation for scoring
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ExtractedContent:
    """Intermediate extraction result before scoring.

    Normalized output from any extraction tool, ready for EvidenceScorer.
    """

    url: str = ""
    title: str = ""
    content: str = ""
    markdown: str = ""
    tool_used: str = ""
    published_date: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "tool_used": self.tool_used,
            "published_date": self.published_date,
        }


# ─────────────────────────────────────────────────────────────────────────────
# DeepSearchClient
# ─────────────────────────────────────────────────────────────────────────────


class DeepSearchClient:
    """Unified search orchestration tool — VIGIL Layer 5.

    Wraps the entire discovery → extraction → scoring pipeline into
    a single call. Agents don't need to know about SearxNG, Obscura,
    Scrapling, or Jina — they just call deep_search().

    Pipeline:
      1. Parallel discovery (SearxNG + Jina Search)
      2. URL dedup + ranking by source credibility
      3. Extraction, cheapest tier first (see EXTRACTION_TIERS):
         Jina Reader → HTTP Extract → Obscura → Crawl4AI → Scrapling
         → FlareSolverr. Tiers that cannot run here are skipped and named.
      4. Evidence scoring (support/conflict/neutral heuristic)
      5. Result ranking by relevance + evidence score + freshness
      6. Return ranked, cited markdown

    Usage:
        client = DeepSearchClient(settings=settings)
        result = await client.search("Indian SaaS market size 2024", depth="standard")
        print(result.to_markdown())

        # Quick depth for fast lookups
        quick = await client.search("Tesla Q3 2024 revenue", depth="quick")

        # Deep depth for comprehensive research
        deep = await client.search("EU AI Act impact on healthcare AI", depth="deep")
    """

    # Depth → number of sources to fully extract and score
    DEPTH_SOURCES: dict[str, int] = {
        "quick": 3,
        "standard": 6,
        "deep": 10,
    }

    # Number of URLs to attempt extraction from (2x the target, to account
    # for extraction failures)
    EXTRACTION_MULTIPLIER = 2

    # Concurrency for batch extraction
    EXTRACTION_CONCURRENCY = 5

    # Minimum content length to consider extraction successful
    MIN_CONTENT_LENGTH = 100

    # Extraction ladder order, cheapest first. Declared once so the ladder,
    # the class docstring and availability reporting cannot disagree.
    #
    # NOTE `scrapling`: the docstring has always advertised Scrapling as part
    # of the chain and `_extract_scrapling` has always existed, but
    # `_extract_batch` never called it — an entire anti-bot tier was dead code.
    # It sits after crawl4ai (both are Playwright-based) and before
    # flaresolverr, which remains the last resort.
    EXTRACTION_TIERS: tuple[str, ...] = (
        "jina",
        "http",
        "obscura",
        "crawl4ai",
        "scrapling",
        "flaresolverr",
    )

    # Ladder tier → the name reported in ``tools_used``.
    TIER_LABELS: dict[str, str] = {
        "jina": "jina-reader",
        "http": "http-extract",
        "obscura": "obscura",
        "crawl4ai": "crawl4ai",
        "scrapling": "scrapling",
        "flaresolverr": "flaresolverr",
    }

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._searxng: Any | None = None
        self._jina: Any | None = None
        self._http_extract: Any | None = None
        self._obscura: Any | None = None
        self._scrapling: Any | None = None
        self._crawl4ai: Any | None = None
        self._flaresolverr: Any | None = None
        self._evidence_scorer: EvidenceScorer | None = None

        # In-memory cache: key → (result, timestamp)
        self._cache: dict[str, tuple[DeepSearchResult, float]] = {}

        # Cached per-tier availability. Obscura's probe shells out, and the
        # answer cannot change mid-run.
        self._availability: dict[str, bool] = {}
        self._skipped: dict[str, str] = {}

    # ─────────────────────────────────────────────────────────────────
    # Capability gating
    # ─────────────────────────────────────────────────────────────────

    def _tier_available(self, tier: str) -> bool:
        """True when extraction tier ``tier`` can actually run here.

        Tiers with no probe default to True: Jina and HTTP-extract are plain
        HTTP, so their failure mode is a network error we can report rather
        than an impossibility. Obscura is the one that must be gated — when the
        binary is missing or not executable on this platform, attempting it can
        only waste a round of concurrency and bury the real error.
        """
        cached = self._availability.get(tier)
        if cached is not None:
            return cached

        available = True
        detail = ""
        try:
            if tier == "obscura":
                from hyperion.tools.obscura import ObscuraClient

                client = ObscuraClient(settings=self.settings)
                available = client._binary_available()
                if not available:
                    binary = client._find_obscura()
                    detail = (
                        f"binary present but not executable here ({binary})"
                        if binary
                        else "binary not found"
                    )
            elif tier == "scrapling":
                from hyperion.tools.scrapling import ScraplingClient

                probe = getattr(ScraplingClient(settings=self.settings), "_check_available", None)
                if probe is not None:
                    available = bool(probe())
                    if not available:
                        detail = "scrapling/playwright not installed"
            elif tier == "crawl4ai":
                from hyperion.tools.crawl4ai import Crawl4AIClient

                probe = getattr(Crawl4AIClient(settings=self.settings), "_check_available", None)
                if probe is not None:
                    available = bool(probe())
                    if not available:
                        detail = "crawl4ai not installed"
        except Exception:
            # A probe that raises must not disable a tier outright — attempting
            # it and failing is strictly better than skipping something usable.
            available = True

        self._availability[tier] = available
        if not available:
            self._skipped[tier] = detail or "not available in this environment"
            logger.debug("extraction tier %s unavailable — skipping", tier)
        return available

    def available_tiers(self) -> list[str]:
        """Extraction tiers that can run here, in ladder order."""
        return [t for t in self.EXTRACTION_TIERS if self._tier_available(t)]

    def unavailable_tiers(self) -> dict[str, str]:
        """Extraction tiers that cannot run here, mapped to why."""
        for tier in self.EXTRACTION_TIERS:
            self._tier_available(tier)
        return dict(self._skipped)

    # ─────────────────────────────────────────────────────────────────
    # Lazy tool initialization
    # ─────────────────────────────────────────────────────────────────

    def _get_searxng(self) -> Any:
        if self._searxng is None:
            from hyperion.tools.searxng import SearxNGClient
            self._searxng = SearxNGClient(settings=self.settings)
        return self._searxng

    def _get_jina(self) -> Any:
        if self._jina is None:
            from hyperion.tools.jina import JinaClient
            self._jina = JinaClient(settings=self.settings)
        return self._jina

    def _get_http_extract(self) -> Any:
        if self._http_extract is None:
            from hyperion.tools.http_extract import HttpExtractClient
            self._http_extract = HttpExtractClient(settings=self.settings)
        return self._http_extract

    def _get_obscura(self) -> Any:
        if self._obscura is None:
            from hyperion.tools.obscura import ObscuraClient
            self._obscura = ObscuraClient(settings=self.settings)
        return self._obscura

    def _get_scrapling(self) -> Any:
        if self._scrapling is None:
            from hyperion.tools.scrapling import ScraplingClient
            self._scrapling = ScraplingClient(settings=self.settings)
        return self._scrapling

    def _get_crawl4ai(self) -> Any:
        if self._crawl4ai is None:
            from hyperion.tools.crawl4ai import Crawl4AIClient
            self._crawl4ai = Crawl4AIClient(settings=self.settings)
        return self._crawl4ai

    def _get_flaresolverr(self) -> Any:
        if self._flaresolverr is None:
            from hyperion.tools.flaresolverr import FlareSolverrClient
            solver_url = getattr(self.settings, "flaresolverr_url", "http://localhost:8191/v1") if self.settings else "http://localhost:8191/v1"
            self._flaresolverr = FlareSolverrClient(solver_url=solver_url)
        return self._flaresolverr

    def _get_evidence_scorer(self) -> EvidenceScorer:
        if self._evidence_scorer is None:
            self._evidence_scorer = EvidenceScorer()
        return self._evidence_scorer

    # ─────────────────────────────────────────────────────────────────
    # Cache
    # ─────────────────────────────────────────────────────────────────

    def _cache_key(self, query: str, depth: str, geography: str | None) -> str:
        return f"{query}:{depth}:{geography or 'global'}"

    def _get_cached(self, key: str) -> DeepSearchResult | None:
        if key in self._cache:
            result, timestamp = self._cache[key]
            if time.time() - timestamp < CACHE_TTL_SECONDS:
                result.cached = True
                return result
            # Expired
            del self._cache[key]
        return None

    def _set_cached(self, key: str, result: DeepSearchResult) -> None:
        self._cache[key] = (result, time.time())

    # ─────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        depth: str = "standard",
        geography: str | None = None,
    ) -> DeepSearchResult:
        """Execute a deep search with parallel discovery and ranked extraction.

        Args:
            query: The search query
            depth: "quick" (3 sources), "standard" (6 sources), "deep" (10 sources)
            geography: Optional geography filter for search results

        Returns:
            DeepSearchResult with ranked results, evidence summary, and sources.
        """
        if not query or not query.strip():
            return DeepSearchResult(query=query, depth=depth)

        # Check cache
        cache_key = self._cache_key(query, depth, geography)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        num_sources = self.DEPTH_SOURCES.get(depth, 6)
        start_time = time.time()
        tools_used: list[str] = []
        tools_tried: list[str] = []
        errors: dict[str, str] = {}

        # Phase 1: Parallel discovery
        discovered_urls, discovery_tools, discovery_errors = await self._discover(
            query, geography, num_sources
        )
        tools_used.extend(discovery_tools)
        errors.update(discovery_errors)

        if not discovered_urls:
            # Discovery found nothing. Say why — a dead SearxNG container and a
            # query with genuinely no sources produced identical empty results
            # before, so callers could not tell a broken deployment from a
            # legitimately obscure question.
            detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
            result = DeepSearchResult(
                query=query,
                depth=depth,
                raw_urls=[],
                total_discovered=0,
                total_extracted=0,
                took_ms=int((time.time() - start_time) * 1000),
                tools_tried=list(dict.fromkeys(tools_tried)),
                errors=errors,
                tiers_unavailable=dict(self._skipped),
                error=f"discovery found no URLs ({detail})" if detail
                      else "discovery found no URLs",
            )
            self._set_cached(cache_key, result)
            return result

        # Phase 2: Extraction (VIGIL fallback chain)
        # Attempt extraction from 2x the target number of URLs to account
        # for extraction failures
        extraction_target = num_sources * self.EXTRACTION_MULTIPLIER
        urls_to_extract = discovered_urls[:extraction_target]

        extracted, extraction_tools, extraction_tried, extraction_errors = (
            await self._extract_batch(urls_to_extract)
        )
        tools_used.extend(extraction_tools)
        tools_tried.extend(extraction_tried)
        errors.update(extraction_errors)

        # Phase 3: Evidence scoring
        scorer = self._get_evidence_scorer()
        extracted_dicts = [e.to_dict() for e in extracted if e.content]
        scored = scorer.score(query, extracted_dicts)

        # Phase 4: Build evidence summary
        evidence_summary = scorer.summarize(scored)

        # Phase 5: Select top results by depth
        ranked = scored[:num_sources]

        # Build sources list
        sources = [r.source for r in ranked]

        took_ms = int((time.time() - start_time) * 1000)

        unavailable = dict(self._skipped)
        error = ""
        if not ranked:
            detail = "; ".join(f"{k}: {v}" for k, v in errors.items())
            error = detail or (
                f"discovered {len(discovered_urls)} URL(s) but no tier "
                "extracted usable content"
            )
            if unavailable:
                error += f" [tiers unavailable here: {', '.join(sorted(unavailable))}]"

        result = DeepSearchResult(
            query=query,
            depth=depth,
            ranked_results=ranked,
            evidence_summary=evidence_summary,
            sources=sources,
            raw_urls=discovered_urls,
            tools_used=list(dict.fromkeys(tools_used)),  # dedup preserving order
            total_discovered=len(discovered_urls),
            total_extracted=len(extracted),
            took_ms=took_ms,
            tools_tried=list(dict.fromkeys(tools_tried)),
            errors=errors,
            tiers_unavailable=unavailable,
            error=error,
        )

        # Cache for 1 hour
        self._set_cached(cache_key, result)
        return result

    # ─────────────────────────────────────────────────────────────────
    # Phase 1: Parallel Discovery
    # ─────────────────────────────────────────────────────────────────

    async def _discover(
        self,
        query: str,
        geography: str | None,
        num_sources: int,
    ) -> tuple[list[str], list[str], dict[str, str]]:
        """Parallel discovery via SearxNG + Jina Search.

        Runs both search engines simultaneously, merges and deduplicates
        URLs. Returns ``(deduplicated_urls, tools_used, errors)``.

        ``errors`` is new: discovery failures used to be logged and dropped, so
        an empty URL list carried no explanation up to the caller.

        The number of results requested is scaled by the depth parameter
        so deeper searches discover more URLs.
        """
        # Request more results than needed — extraction will filter
        search_count = max(num_sources * 3, 15)
        tools_used: list[str] = []
        errors: dict[str, str] = {}

        # Both engines are plain HTTP, so both are always worth attempting:
        # their failure mode is a reportable network error, not impossibility.
        search_tasks = [
            self._search_searxng(query, search_count, geography),
            self._search_jina(query, search_count),
        ]

        # Run searches in parallel
        results = await asyncio.gather(*search_tasks, return_exceptions=True)

        all_urls: list[str] = []
        for result in results:
            if isinstance(result, BaseException):
                logger.warning("Discovery search failed: %s", result)
                errors["discovery"] = f"{type(result).__name__}: {result}"
                continue
            urls, tool_name, detail = result
            if urls:
                all_urls.extend(urls)
                if tool_name and tool_name not in tools_used:
                    tools_used.append(tool_name)
            elif detail:
                errors[tool_name or "discovery"] = detail

        # Deduplicate preserving order
        deduped = list(dict.fromkeys(all_urls))
        return deduped, tools_used, errors

    async def _search_searxng(
        self,
        query: str,
        num_results: int,
        geography: str | None,
    ) -> tuple[list[str], str, str]:
        """Search via SearxNG. Returns (urls, tool_name, error_detail)."""
        try:
            searxng = self._get_searxng()
            language = "en"
            if geography:
                # Map common geography codes to SearxNG language codes
                geo_map = {"US": "en", "EU": "en", "UK": "en", "IN": "en",
                           "CN": "zh", "JP": "ja", "DE": "de", "FR": "fr"}
                language = geo_map.get(geography.upper(), "en")

            response = await searxng.search(
                query=query,
                num_results=num_results,
                language=language,
            )

            urls = [r.url for r in response.results if r.url]
            return (urls, "searxng", "" if urls else "returned no results")
        except Exception as e:
            logger.warning("SearxNG discovery failed: %s", e)
            return ([], "searxng", f"{type(e).__name__}: {e}")

    async def _search_jina(
        self,
        query: str,
        num_results: int,
    ) -> tuple[list[str], str, str]:
        """Search via Jina s.jina.ai. Returns (urls, tool_name, error_detail)."""
        try:
            jina = self._get_jina()
            response = await jina.search(query=query, num_results=num_results)

            urls = [r.url for r in response.results if r.url]
            return (urls, "jina", "" if urls else "returned no results")
        except Exception as e:
            logger.warning("Jina discovery failed: %s", e)
            return ([], "jina", f"{type(e).__name__}: {e}")

    # ─────────────────────────────────────────────────────────────────
    # Phase 2: Extraction (VIGIL Fallback Chain)
    # ─────────────────────────────────────────────────────────────────

    async def _extract_batch(
        self,
        urls: list[str],
    ) -> tuple[list[ExtractedContent], list[str], list[str], dict[str, str]]:
        """Extract content from URLs using the VIGIL fallback chain.

        For each URL, tries extraction tiers in :attr:`EXTRACTION_TIERS` order:
          1. Jina Reader (fast, keyless, reliable — always works)
          2. HTTP Extract (httpx + trafilatura — keyless, browserless)
          3. Obscura (stealth, JS rendering — capability-gated)
          4. Crawl4AI (heavy extraction, PDFs — browser-based)
          5. Scrapling (adaptive anti-bot, Playwright)
          6. FlareSolverr (CAPTCHA-protected pages — last resort)

        Once a URL is successfully extracted it is not retried by lower tiers,
        and a tier that cannot run here is skipped rather than attempted.

        Returns ``(extracted, tools_used, tools_tried, errors)``. The two extra
        members exist so the caller can distinguish "every tier failed" from
        "nothing needed extracting" — previously both looked identical.

        Uses a semaphore to limit concurrency.
        """
        if not urls:
            return ([], [], [], {})

        extracted: list[ExtractedContent] = []
        extracted_urls: set[str] = set()
        tools_used: list[str] = []
        tools_tried: list[str] = []
        errors: dict[str, str] = {}

        semaphore = asyncio.Semaphore(self.EXTRACTION_CONCURRENCY)

        for tier in self.EXTRACTION_TIERS:
            pending = [u for u in urls if u not in extracted_urls]
            if not pending:
                break  # everything already extracted — stop climbing the ladder
            if not self._tier_available(tier):
                continue

            label = self.TIER_LABELS.get(tier, tier)
            extractor = getattr(self, f"_extract_{tier}", None)
            if extractor is None:  # pragma: no cover — guards a typo in the table
                errors[label] = "no extractor implemented"
                continue

            tools_tried.append(label)
            tasks = [extractor(semaphore, u) for u in pending]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            produced = 0
            failures: list[str] = []
            for result in results:
                if isinstance(result, BaseException):
                    failures.append(f"{type(result).__name__}: {result}")
                    continue
                if isinstance(result, ExtractedContent) and result.content:
                    extracted.append(result)
                    extracted_urls.add(result.url)
                    produced += 1

            if produced:
                tools_used.append(label)
            elif failures:
                errors[label] = failures[0]
            else:
                errors[label] = f"no usable content from {len(pending)} URL(s)"

        return (extracted, tools_used, tools_tried, errors)

    # ─────────────────────────────────────────────────────────────────
    # Per-tool extraction methods
    # ─────────────────────────────────────────────────────────────────

    def _is_quality_content(self, content: str) -> bool:
        """Check if extracted content meets quality thresholds."""
        if not content or len(content) < self.MIN_CONTENT_LENGTH:
            return False
        # Check it's not just an error message or boilerplate
        error_indicators = ["404", "not found", "access denied", "forbidden", "captcha"]
        content_lower = content.lower()
        error_count = sum(1 for indicator in error_indicators if indicator in content_lower)
        if error_count > 2 and len(content) < 500:
            return False
        return True

    async def _extract_jina(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Jina Reader — fast, keyless, reliable extraction."""
        async with semaphore:
            try:
                jina = self._get_jina()
                result = await jina.read(url)
                if result and (result.markdown or result.content):
                    content = (result.markdown or result.content)[:MAX_CONTENT_CHARS]
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="jina-reader",
                        )
            except Exception as e:
                logger.debug("Jina Reader extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="jina-reader")

    async def _extract_http(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via HTTP + trafilatura — keyless, browserless extraction."""
        async with semaphore:
            try:
                http_extract = self._get_http_extract()
                result = await http_extract.extract(url)
                if result and result.success and result.content:
                    content = result.content[:MAX_CONTENT_CHARS]
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title,
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="http-extract",
                        )
            except Exception as e:
                logger.debug("HTTP extract failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="http-extract")

    async def _extract_obscura(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Obscura — stealth, fast, JS rendering."""
        async with semaphore:
            try:
                obscura = self._get_obscura()
                result = await obscura.fetch(url, output_format="markdown")
                if result and (result.markdown or result.content):
                    content = (result.markdown or result.content)[:MAX_CONTENT_CHARS]
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="obscura",
                        )
            except Exception as e:
                logger.debug("Obscura extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="obscura")

    async def _extract_scrapling(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Scrapling — adaptive, anti-bot, Playwright."""
        async with semaphore:
            try:
                scrapling = self._get_scrapling()
                result = await scrapling.fetch(url, stealth=True)
                if result and result.content:
                    content = result.content[:MAX_CONTENT_CHARS]
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="scrapling",
                        )
            except Exception as e:
                logger.debug("Scrapling extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="scrapling")

    async def _extract_crawl4ai(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Crawl4AI — heavy extraction, PDFs."""
        async with semaphore:
            try:
                crawl4ai = self._get_crawl4ai()
                result = await crawl4ai.crawl(url)
                if result and (result.markdown or result.content):
                    content = (result.markdown or result.content)[:MAX_CONTENT_CHARS]
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="crawl4ai",
                        )
            except Exception as e:
                logger.debug("Crawl4AI extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="crawl4ai")

    async def _extract_flaresolverr(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via FlareSolverr — CAPTCHA-protected pages."""
        async with semaphore:
            try:
                flare = self._get_flaresolverr()
                result = await flare.get(url)
                if result and result.success and result.html:
                    # Strip HTML tags for basic text extraction
                    text = re.sub(r"<[^>]+>", " ", result.html)
                    text = re.sub(r"\s+", " ", text).strip()[:MAX_CONTENT_CHARS]
                    if self._is_quality_content(text):
                        return ExtractedContent(
                            url=url,
                            title="",
                            content=text,
                            markdown=text,
                            tool_used="flaresolverr",
                        )
            except Exception as e:
                logger.debug("FlareSolverr extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="flaresolverr")

    # ─────────────────────────────────────────────────────────────────
    # Cleanup
    # ─────────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Clean up all tool instances."""
        if self._searxng:
            await self._searxng.close()
            self._searxng = None
        if self._jina:
            await self._jina.close()
            self._jina = None
        if self._http_extract:
            await self._http_extract.close()
            self._http_extract = None
        if self._obscura:
            await self._obscura.close()
            self._obscura = None
        if self._scrapling:
            await self._scrapling.close()
            self._scrapling = None
        if self._crawl4ai:
            await self._crawl4ai.close()
            self._crawl4ai = None
        if self._flaresolverr:
            await self._flaresolverr.close()
            self._flaresolverr = None

    async def __aenter__(self) -> DeepSearchClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
