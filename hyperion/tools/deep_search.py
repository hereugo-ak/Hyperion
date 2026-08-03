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
from typing import Any, cast

from hyperion.tools._content_quality import is_quality_content
from hyperion.tools.content_selector import select_relevant_content
from hyperion.tools.evidence_scorer import EvidenceScorer, EvidenceSummary, ScoredResult
from hyperion.tools.query_utils import grounded_search_or_empty

logger = logging.getLogger(__name__)

# Retained-content budget — 15000 chars per source (Step 1.6).
#
# Fix 2.2 (§4.7 Finding B-6) did NOT change this number. It changed how the
# budget is *spent*: every tier below now calls `_fit_content`, which selects
# the most relevant 15000 chars via chunk → rerank → top-k, instead of the
# blind head-slice `content[:MAX_CONTENT_CHARS]` that retained a 60-page PDF's
# title page and table of contents while discarding its tables and conclusions.
MAX_CONTENT_CHARS = 15000

# Cache TTL — 1 hour
CACHE_TTL_SECONDS = 3600


# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class YieldMetrics:
    """Extraction-yield metrics for one deep-search call (fix 2.6).

    Audit §6 Phase 2 item 2.6: "Log an extraction-yield metric per engagement
    (``urls_discovered``, ``urls_extracted``, ``chars_retained``,
    ``sources_cited``) and surface it in the run report." These four numbers
    are what the Phase 2 exit criterion ("extraction success >=60% of
    discovered URLs; every cited source has >=500 chars of retained, reranked
    content") is computed FROM — before this fix they existed only
    implicitly, scattered across three fields, so the criterion was
    unmeasurable.
    """

    urls_discovered: int = 0
    urls_extracted: int = 0
    chars_retained: int = 0  # total retained content across cited sources
    sources_cited: int = 0

    @property
    def extraction_yield(self) -> float:
        """Fraction of discovered URLs that produced usable content."""
        if self.urls_discovered == 0:
            return 0.0
        return self.urls_extracted / self.urls_discovered

    @property
    def avg_chars_per_source(self) -> float:
        if self.sources_cited == 0:
            return 0.0
        return self.chars_retained / self.sources_cited

    def to_dict(self) -> dict[str, Any]:
        return {
            "urls_discovered": self.urls_discovered,
            "urls_extracted": self.urls_extracted,
            "chars_retained": self.chars_retained,
            "sources_cited": self.sources_cited,
            "extraction_yield": round(self.extraction_yield, 3),
            "avg_chars_per_source": round(self.avg_chars_per_source, 1),
        }


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
    # Fix 2.6: extraction-yield metrics for this call (audit §6 Phase 2).
    yield_metrics: YieldMetrics = field(default_factory=YieldMetrics)

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
        ym = self.yield_metrics
        lines.append(
            f"**Yield**: {ym.extraction_yield:.0%} of discovered URLs extracted | "
            f"{ym.chars_retained} chars retained across {ym.sources_cited} cited sources"
        )
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
            "yield_metrics": self.yield_metrics.to_dict(),
        }


# ─────────────────────────────────────────────────────────────────────────────
# Extracted Content — intermediate representation for scoring
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Per-engagement yield accumulator (fix 2.6) — the "run report" surface
# ─────────────────────────────────────────────────────────────────────────────


class _EngagementYield:
    """Aggregate extraction yield across every deep-search call in one run.

    Specialists each spawn sub-agents that each issue deep searches; no
    single ``DeepSearchResult`` can answer "how did retrieval perform on THIS
    ENGAGEMENT?" — the number the audit's Phase 2 exit criterion is defined
    on. This process-level accumulator is reset at engagement start
    (:func:`reset_engagement_yield`) and read at run-report time
    (:func:`engagement_yield_report`). Thread-safe: sub-agents search
    concurrently.
    """

    def __init__(self) -> None:
        import threading

        self._lock = threading.Lock()
        self._totals = YieldMetrics()
        self._calls = 0
        self._backend_queries: dict[str, int] = {}
        self._retrieval_constraints: list[str] = []

    def record(self, metrics: YieldMetrics) -> None:
        with self._lock:
            self._calls += 1
            self._totals.urls_discovered += metrics.urls_discovered
            self._totals.urls_extracted += metrics.urls_extracted
            self._totals.chars_retained += metrics.chars_retained
            self._totals.sources_cited += metrics.sources_cited

    def record_backend(self, backend: str, count: int = 1) -> None:
        if count <= 0:
            return
        with self._lock:
            self._backend_queries[backend] = self._backend_queries.get(backend, 0) + count

    def record_constraints(self, constraints: list[str]) -> None:
        with self._lock:
            for constraint in constraints:
                if constraint and constraint not in self._retrieval_constraints:
                    self._retrieval_constraints.append(constraint)

    def report(self) -> dict[str, Any]:
        with self._lock:
            out = self._totals.to_dict()
            out["search_calls"] = self._calls
            out["backend_query_counts"] = dict(sorted(self._backend_queries.items()))
            out["retrieval_constraints"] = list(self._retrieval_constraints)
            return out

    def reset(self) -> None:
        with self._lock:
            self._totals = YieldMetrics()
            self._calls = 0
            self._backend_queries = {}
            self._retrieval_constraints = []


_engagement_yield = _EngagementYield()


def reset_engagement_yield() -> None:
    """Reset the per-engagement accumulator (call at engagement start)."""
    _engagement_yield.reset()


def engagement_yield_report() -> dict[str, Any]:
    """The run-report surface for extraction and retrieval-backend telemetry."""
    return _engagement_yield.report()


def record_retrieval_backend(backend: str, count: int = 1) -> None:
    """Record a provider-reported query count outside DeepSearch discovery."""
    _engagement_yield.record_backend(backend, count)


def record_retrieval_constraints(constraints: list[str]) -> None:
    """Record fail-open quota, safety or provider constraints for methodology."""
    _engagement_yield.record_constraints(constraints)


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
        self._grounded: Any | None = None
        self._evidence_scorer: EvidenceScorer | None = None
        # Fix 2.1: the single extraction ladder. This client no longer owns a
        # copy of the climb — it owns the tier subset and the per-tier calls.
        self._unified_extract: Any | None = None

        # In-memory cache: key → (result, timestamp)
        self._cache: dict[str, tuple[DeepSearchResult, float]] = {}

        # Cached per-tier availability. Obscura's probe shells out, and the
        # answer cannot change mid-run.
        self._availability: dict[str, bool] = {}
        self._skipped: dict[str, str] = {}

        # Fix 2.2: the query the in-flight extraction batch is being performed
        # for, so `_fit_content` can select the most *relevant* 15000 chars
        # rather than the first 15000.
        #
        # Held as state rather than threaded through every `_extract_<tier>`
        # signature on purpose: `tests/test_tool_capability_gating.py`
        # monkeypatches `client._extract_jina(semaphore, url)` etc. to exercise
        # ladder behaviour without a network, and `UnifiedExtract`'s
        # `tier_resolver` contract is `(url) -> UnifiedExtractResult`. Adding a
        # `query` parameter to those methods would break both, for no gain: the
        # batch is single-query by construction (`search()` grounds one query,
        # then extracts for it), so per-call threading would only pass the same
        # value to every call.
        self._active_query: str = ""
        # Per-batch selection provenance, keyed by URL. Consumed by fix 2.6's
        # extraction-yield metric; kept here so the metric reports what
        # *actually* happened rather than re-deriving it.
        self._selection_stats: dict[str, dict[str, Any]] = {}

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
        except Exception:  # noqa: BLE001 - best-effort, returns a safe default
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

    def _get_grounded(self) -> Any:
        if self._grounded is None:
            from hyperion.tools.grounded_search import GroundedSearchClient

            self._grounded = GroundedSearchClient(settings=self.settings)
        return self._grounded

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

        # Fix 1.1/1.2 (HYPERION_DEEP_AUDIT_2026-07-27.md Finding B-2 +
        # item 1.2): `deep_search.search()` is the entry point every
        # specialist actually calls — `_discover()` below fans out to
        # `_search_searxng` and `_search_jina` in parallel, and before this
        # fix only the SearxNG leg was grounded internally (the Jina leg
        # called `jina.search()` with the raw query). Grounding once HERE,
        # before either leg runs, means the fan-out can never diverge again
        # even if a leg's own client-level grounding is ever changed —
        # this is the "single shared choke point" item 1.2 asked for at the
        # orchestrator level, not just inside individual HTTP clients.
        original_query = query
        grounded, empty = grounded_search_or_empty(
            query,
            lambda: DeepSearchResult(
                query=original_query,
                depth=depth,
                error="query has no subject after grounding",
            ),
            geography=geography or "",
            logger=logger,
            tool_name="DeepSearch",
        )
        if empty is not None:
            return cast("DeepSearchResult", empty)
        query = grounded

        # Check cache
        cache_key = self._cache_key(query, depth, geography)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        num_sources = self.DEPTH_SOURCES.get(depth, 6)
        start_time = time.time()

        # D-12: every exit from a search attempt must be counted — success,
        # zero discovery, or an exception anywhere in the ladder. The 07-30
        # run issued dozens of real SearXNG queries and the engagement metric
        # read "0 search calls" because the only recorder sat at the end of
        # the success path. Success and zero-discovery record inside
        # _search_execute(); this except closes the last hole (a raise
        # mid-ladder) so no future exit path can bypass the counter.
        try:
            return await self._search_execute(
                query, depth, geography, num_sources, start_time, cache_key
            )
        except Exception:
            _engagement_yield.record(YieldMetrics(urls_discovered=0))
            raise

    async def _search_execute(
        self,
        query: str,
        depth: str,
        geography: str | None,
        num_sources: int,
        start_time: float,
        cache_key: str,
    ) -> DeepSearchResult:
        """The discovery→extraction→scoring phases of :meth:`search`.

        Split out so :meth:`search` can guarantee per-attempt yield recording
        at every exit (D-12). Behaviour is unchanged.
        """
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
            # D-12: record the yield HERE, not only at the end of the success
            # path. The 07-30 run issued dozens of real SearXNG queries that
            # all returned zero URLs, and this early-return skipped the
            # recorder at the bottom of search() — so the engagement metric
            # read "0 search calls" while Docker logged dozens. A search that
            # finds nothing still counts as a search call; the zero-URL
            # YieldMetrics is exactly what the zero-evidence hard error
            # downstream needs to see.
            zero_yield = YieldMetrics(urls_discovered=0)
            _engagement_yield.record(zero_yield)
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

        # Fix 2.2: pass the grounded query down so every extraction tier fits
        # its 15000-char budget by relevance to *this* question instead of by
        # position in the document.
        extracted, extraction_tools, extraction_tried, extraction_errors = (
            await self._extract_batch(urls_to_extract, query)
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

        # Fix 2.6 (audit §6 Phase 2): the four audit-named yield metrics.
        # ``chars_retained`` counts only CITED sources (the ranked cut), so
        # "every cited source has >=500 chars" (Phase 2 exit criterion) is
        # directly checkable via ``avg_chars_per_source``.
        yield_metrics = YieldMetrics(
            urls_discovered=len(discovered_urls),
            urls_extracted=len(extracted),
            chars_retained=sum(len(r.content or "") for r in ranked),
            sources_cited=len(sources),
        )

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
            yield_metrics=yield_metrics,
        )

        # Fix 2.6: log the per-call yield and accumulate into the
        # per-engagement report. The audit's Phase 2 exit criterion
        # (">=60% of discovered URLs extracted") is only checkable if this
        # number exists in logs and in the run report.
        logger.info(
            "extraction yield: %d/%d URLs (%.0f%%), %d chars retained across %d cited sources",
            yield_metrics.urls_extracted,
            yield_metrics.urls_discovered,
            yield_metrics.extraction_yield * 100,
            yield_metrics.chars_retained,
            yield_metrics.sources_cited,
        )
        _engagement_yield.record(yield_metrics)

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

        # W-14: grounded retrieval is a scarce escalation, never a routine
        # third fan-out task. It is attempted only when the independent-index
        # pool is below its healthy-engine floor; all failures remain fail-open.
        try:
            from hyperion.tools.grounded_search import (
                GroundedSearchClient,
                GroundingReason,
            )

            if GroundedSearchClient.searxng_is_degraded():
                grounded = await self._get_grounded().search(
                    query,
                    reason=GroundingReason.SEARXNG_DEGRADED,
                    geography=geography or "",
                )
                _engagement_yield.record_backend("gemini", grounded.actual_units)
                _engagement_yield.record_constraints(grounded.constraints)
                if grounded.results:
                    all_urls.extend(item.url for item in grounded.results if item.url)
                    tools_used.append("grounded-search")
                elif grounded.constraints:
                    errors["grounded-search"] = "; ".join(grounded.constraints)
        except Exception as exc:  # noqa: BLE001 - fail-open to normal discovery
            detail = f"{type(exc).__name__}: {exc}"
            errors["grounded-search"] = detail
            _engagement_yield.record_constraints([detail])

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
        _engagement_yield.record_backend("searxng")
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
        except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("SearxNG discovery failed: %s", e)
            return ([], "searxng", f"{type(e).__name__}: {e}")

    async def _search_jina(
        self,
        query: str,
        num_results: int,
    ) -> tuple[list[str], str, str]:
        """Search via Jina s.jina.ai. Returns (urls, tool_name, error_detail)."""
        _engagement_yield.record_backend("jina")
        try:
            jina = self._get_jina()
            response = await jina.search(query=query, num_results=num_results)

            urls = [r.url for r in response.results if r.url]
            return (urls, "jina", "" if urls else "returned no results")
        except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("Jina discovery failed: %s", e)
            return ([], "jina", f"{type(e).__name__}: {e}")

    # ─────────────────────────────────────────────────────────────────
    # Phase 2: Extraction (VIGIL Fallback Chain)
    # ─────────────────────────────────────────────────────────────────

    def _get_unified_extract(self) -> Any:
        """The single extraction ladder (fix 2.1). Lazy — only built if used."""
        if self._unified_extract is None:
            from hyperion.tools.unified_extract import UnifiedExtract

            self._unified_extract = UnifiedExtract(settings=self.settings)
        return self._unified_extract

    def _resolve_extraction_tier(
        self,
        tier: str,
        semaphore: asyncio.Semaphore,
        *,
        extract_tables: bool = True,
        extract_links: bool = True,
    ) -> Any:
        """Adapt this client's ``_extract_<tier>`` methods for the shared driver.

        Fix 2.1: the *ladder logic* now lives in exactly one place
        (:meth:`UnifiedExtract.extract_ladder`), but the *substitution point*
        stays here. That is deliberate, and it is what makes this a collapse of
        three ladders into one rather than a fourth one:

          * The climb — tier order, capability skipping, tier-major batching,
            stop-when-done, honest ``tools_used``/``tools_tried``/``errors`` —
            is no longer duplicated. Bug fixes to it now reach every consumer.
          * The per-tier *calls* remain overridable methods on this class,
            because ``tests/test_tool_capability_gating.py`` monkeypatches
            ``client._extract_jina`` etc. to test ladder behaviour without a
            network, and because ``deep_search`` needs its own
            :class:`ExtractedContent` shape and 15,000-char budget applied at
            the point of extraction.

        The returned callable owns its concurrency bounding: the shared driver
        must not wrap it, because ``asyncio.Semaphore`` is not reentrant and
        this adapter's underlying ``_extract_<tier>(semaphore, url)`` methods
        acquire it themselves.
        """
        from hyperion.tools.unified_extract import UnifiedExtractResult

        extractor = getattr(self, f"_extract_{tier}", None)
        if extractor is None:
            return None

        async def _call(url: str) -> UnifiedExtractResult:
            content = await extractor(semaphore, url)
            # Normalise this client's ExtractedContent into the driver's
            # result shape. `content` truthiness IS this ladder's quality
            # signal: `_extract_*` already applied `_is_quality_content`, and
            # returns an empty-content sentinel on failure.
            ok = bool(getattr(content, "content", ""))
            result = UnifiedExtractResult(
                url=getattr(content, "url", url),
                title=getattr(content, "title", "") or "",
                content=getattr(content, "content", "") or "",
                markdown=getattr(content, "markdown", "") or "",
                tool_used=getattr(content, "tool_used", "") or tier,
                success=ok,
                error="" if ok else f"no usable content from {url}",
                # Carry the original through so the caller keeps published_date
                # and the tier's own label rather than a lossy reconstruction.
                raw=content,
            )
            return result

        return _call

    async def _extract_batch(
        self,
        urls: list[str],
        query: str = "",
    ) -> tuple[list[ExtractedContent], list[str], list[str], dict[str, str]]:
        """Extract content from URLs using the VIGIL fallback chain.

        Fix 2.1 (§4.5 Finding B-4): this method no longer *implements* a
        ladder. It declares which tiers it is entitled to
        (:attr:`EXTRACTION_TIERS`), supplies its own per-tier extractors via
        :meth:`_resolve_extraction_tier`, and delegates the climb to
        :meth:`UnifiedExtract.extract_ladder` — the single implementation.
        Before this fix there were three separately-maintained copies of the
        climb, and the best-engineered one had no callers at all.

        Tiers, in :attr:`EXTRACTION_TIERS` order:
          1. Jina Reader (fast, keyless, reliable — always works)
          2. HTTP Extract (httpx + trafilatura — keyless, browserless)
          3. Obscura (stealth, JS rendering — capability-gated)
          4. Crawl4AI (heavy extraction, PDFs — browser-based)
          5. Scrapling (adaptive anti-bot, Playwright)
          6. FlareSolverr (CAPTCHA-protected pages — last resort)

        Once a URL is successfully extracted it is not retried by lower tiers,
        and a tier that cannot run here is skipped rather than attempted.

        Returns ``(extracted, tools_used, tools_tried, errors)``, with tier
        names mapped through :attr:`TIER_LABELS` so this client's public
        provenance vocabulary is unchanged by the delegation. The two extra
        members exist so the caller can distinguish "every tier failed" from
        "nothing needed extracting" — previously both looked identical.

        Fix 2.2: ``query`` is what every tier's :meth:`_fit_content` call ranks
        chunks against. It is optional and defaults to empty so the pre-existing
        one-argument call signature keeps working — with an empty query the
        selection honestly degrades to the old head-slice rather than silently
        ranking against nothing.
        """
        if not urls:
            return ([], [], [], {})

        self._active_query = query or ""
        self._selection_stats = {}

        outcome = await self._get_unified_extract().extract_ladder(
            urls,
            concurrency=self.EXTRACTION_CONCURRENCY,
            tiers=self.EXTRACTION_TIERS,
            tier_resolver=self._resolve_extraction_tier,
            tier_available=self._tier_available,
        )

        def _label(tier: str) -> str:
            return self.TIER_LABELS.get(tier, tier)

        extracted: list[ExtractedContent] = []
        for result in outcome.results:
            carried = result.raw
            extracted.append(
                carried
                if isinstance(carried, ExtractedContent)
                else ExtractedContent(
                    url=result.url,
                    title=result.title,
                    content=result.content,
                    markdown=result.markdown,
                    tool_used=result.tool_used,
                )
            )

        return (
            extracted,
            [_label(t) for t in outcome.tools_used],
            [_label(t) for t in outcome.tools_tried],
            {_label(t): why for t, why in outcome.errors.items()},
        )

    # ─────────────────────────────────────────────────────────────────
    # Per-tool extraction methods
    # ─────────────────────────────────────────────────────────────────

    def _fit_content(self, content: str, url: str = "") -> str:
        """Fit ``content`` into :data:`MAX_CONTENT_CHARS` **by relevance**.

        Fix 2.2 (§4.7 Finding B-6). This method is the single replacement for
        the six identical ``[:MAX_CONTENT_CHARS]`` head-slices that used to sit
        in the six ``_extract_<tier>`` methods below.

        Why one helper instead of six inline calls: the audit's §4.5 lesson is
        that N copies of the same logic diverge (three extraction ladders, and
        the archive-ordering fix landed only in the one with no callers). Six
        copies of a truncation rule would drift the same way — and a tier that
        kept the old head-slice would silently produce worse evidence than its
        neighbours while reporting the same ``tool_used``.

        Degrades safely: with no active query there is nothing to rank against,
        so :func:`select_relevant_content` head-slices and says so. That keeps
        this a strict improvement — never worse than the previous behaviour.
        """
        if not content:
            return ""
        selection = select_relevant_content(
            content,
            self._active_query,
            budget_chars=MAX_CONTENT_CHARS,
        )
        if url:
            self._selection_stats[url] = selection.to_dict()
        if selection.degraded and selection.strategy != "empty":
            logger.debug(
                "DeepSearch: relevance selection degraded for %s (%s) — %s",
                url or "<url unknown>",
                selection.strategy,
                selection.reason,
            )
        return selection.content

    def _is_quality_content(self, content: str) -> bool:
        """Check if extracted content meets quality thresholds.

        Phase 5.1d: shared with `unified_extract` via
        :mod:`hyperion.tools._content_quality`. The previous inline substring
        counter let 404/403/captcha bodies through as successful extractions,
        which both poisoned the evidence base and prevented the ladder from
        descending to a stronger rung.
        """
        return is_quality_content(content, self.MIN_CONTENT_LENGTH)

    async def _extract_jina(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Jina Reader — fast, keyless, reliable extraction."""
        async with semaphore:
            try:
                jina = self._get_jina()
                result = await jina.read(url)
                if result and (result.markdown or result.content):
                    content = self._fit_content(result.markdown or result.content, url)
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="jina-reader",
                        )
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
                logger.debug("Jina Reader extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="jina-reader")

    async def _extract_http(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via HTTP + trafilatura — keyless, browserless extraction."""
        async with semaphore:
            try:
                http_extract = self._get_http_extract()
                result = await http_extract.extract(url)
                if result and result.success and result.content:
                    content = self._fit_content(result.content, url)
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title,
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="http-extract",
                        )
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
                logger.debug("HTTP extract failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="http-extract")

    async def _extract_obscura(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Obscura — stealth, fast, JS rendering."""
        async with semaphore:
            try:
                obscura = self._get_obscura()
                result = await obscura.fetch(url, output_format="markdown")
                if result and (result.markdown or result.content):
                    content = self._fit_content(result.markdown or result.content, url)
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="obscura",
                        )
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
                logger.debug("Obscura extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="obscura")

    async def _extract_scrapling(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Scrapling — adaptive, anti-bot, Playwright."""
        async with semaphore:
            try:
                scrapling = self._get_scrapling()
                result = await scrapling.fetch(url, stealth=True)
                if result and result.content:
                    content = self._fit_content(result.content, url)
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="scrapling",
                        )
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
                logger.debug("Scrapling extraction failed for %s: %s", url, e)
            return ExtractedContent(url=url, tool_used="scrapling")

    async def _extract_crawl4ai(self, semaphore: asyncio.Semaphore, url: str) -> ExtractedContent:
        """Extract via Crawl4AI — heavy extraction, PDFs."""
        async with semaphore:
            try:
                crawl4ai = self._get_crawl4ai()
                result = await crawl4ai.crawl(url)
                if result and (result.markdown or result.content):
                    content = self._fit_content(result.markdown or result.content, url)
                    if self._is_quality_content(content):
                        return ExtractedContent(
                            url=url,
                            title=result.title or "",
                            content=content,
                            markdown=result.markdown or content,
                            tool_used="crawl4ai",
                        )
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
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
                    text = self._fit_content(re.sub(r"\s+", " ", text).strip(), url)
                    if self._is_quality_content(text):
                        return ExtractedContent(
                            url=url,
                            title="",
                            content=text,
                            markdown=text,
                            tool_used="flaresolverr",
                        )
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
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
        if self._unified_extract:
            await self._unified_extract.close()
            self._unified_extract = None

    async def __aenter__(self) -> DeepSearchClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
