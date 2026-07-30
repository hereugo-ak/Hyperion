"""
HYPERION Unified Search — SearxNG → Jina → Obscura fallback chain.

This is NOT a generic "search multiple engines" wrapper. It implements
the exact tool selection logic from §5.2:

  Search task:
    1. SearxNG (free, unlimited, fast) — always try first
    2. Jina search (if SearxNG returns poor results)
    3. Obscura (if the data is behind JS rendering)

The unified search chain:
1. Tries SearxNG first (free, unlimited, aggregates 70+ engines)
2. If SearxNG returns fewer than `min_results` results, tries Jina
3. If Jina also returns poor results, tries Obscura fetch on top URLs
4. Merges and deduplicates all results
5. Returns a unified response with provenance (which tool found what)

This is how agents get the best possible search results without
wasting API calls on tools that aren't needed.

Robustness contract (mirrors ``unified_search``'s sibling ``unified_extract``):

* A tier that physically cannot run here is SKIPPED and NAMED, not attempted.
* A tier that fails records WHY. Search returning nothing must never be
  indistinguishable from search finding nothing.
* ``tools_used`` lists tiers that actually contributed results; ``tools_tried``
  lists every tier we attempted.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from hyperion.tools.jina import JinaClient
from hyperion.tools.obscura import ObscuraClient
from hyperion.tools.searxng import SearxNGClient
from hyperion.tools.stealth_search import StealthSearchClient

logger = logging.getLogger(__name__)


@dataclass
class UnifiedSearchResult:
    """A unified search result from multiple search tools."""

    query: str
    results: list[dict[str, Any]] = field(default_factory=list)
    total: int = 0
    tools_used: list[str] = field(default_factory=list)
    searxng_results: int = 0
    jina_results: int = 0
    obscura_results: int = 0
    stealth_results: int = 0
    took_ms: int = 0
    cached: bool = False
    # Every tier we actually attempted, in ladder order. Distinct from
    # ``tools_used``, which lists only the tiers that contributed results.
    tools_tried: list[str] = field(default_factory=list)
    # Why each attempted tier produced nothing. Previously every failure was
    # swallowed by `except (...): pass`, so a dead SearxNG container and a
    # genuinely empty result set were indistinguishable to the caller.
    errors: dict[str, str] = field(default_factory=dict)
    # Tiers skipped because they cannot run in this environment at all.
    tiers_unavailable: dict[str, str] = field(default_factory=dict)
    # Human-readable roll-up. Empty when results were found.
    error: str = ""

    @property
    def success(self) -> bool:
        return bool(self.results)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": self.results,
            "total": self.total,
            "tools_used": self.tools_used,
            "searxng_results": self.searxng_results,
            "jina_results": self.jina_results,
            "obscura_results": self.obscura_results,
            "stealth_results": self.stealth_results,
            "took_ms": self.took_ms,
            "cached": self.cached,
            "tools_tried": self.tools_tried,
            "errors": self.errors,
            "tiers_unavailable": self.tiers_unavailable,
            "error": self.error,
            "success": self.success,
        }


class UnifiedSearch:
    """Unified search with fallback chain: SearxNG → Jina → Obscura.

    Implements the tool selection logic from §5.2. Tries SearxNG first
    (free, unlimited), falls back to Jina if results are poor, and
    finally tries Obscura for JS-rendered content.

    Usage:
        search = UnifiedSearch(settings=settings)
        result = await search.search("Indian SaaS market size 2024", min_results=5)
        for r in result.results:
            print(f"[{r['source']}] {r['title']} — {r['url']}")
        if not result.success:
            print(result.error)  # tells you which tier failed and why
    """

    MIN_RESULTS_THRESHOLD = 5  # If SearxNG returns fewer than this, try Jina
    JINA_MIN_RESULTS = 3       # If Jina also returns fewer than this, try Obscura
    OBSCURA_MAX_URLS = 3       # Max URLs to fetch with Obscura (expensive)

    # Ladder order, cheapest first. Named so availability reporting and the
    # ladder itself cannot disagree about which tiers exist.
    #
    # NOTE `stealth`: StealthSearchClient was exported from hyperion.tools but
    # called by no orchestrator — a whole browser-based search capability that
    # nothing could reach. It sits last because launching Chromium is by far the
    # most expensive way to obtain a result, and it is the only tier that can
    # still find sources when SearxNG is down and Jina is rate-limited.
    TIER_ORDER: tuple[str, ...] = ("searxng", "jina", "obscura", "stealth")

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._searxng: SearxNGClient | None = None
        self._jina: JinaClient | None = None
        self._obscura: ObscuraClient | None = None
        self._stealth: StealthSearchClient | None = None
        # Cached per-tier availability. Obscura's probe shells out, and the
        # answer cannot change mid-run.
        self._availability: dict[str, bool] = {}
        self._skipped: dict[str, str] = {}

    async def _get_searxng(self) -> SearxNGClient:
        if self._searxng is None:
            self._searxng = SearxNGClient(settings=self.settings)
        return self._searxng

    async def _get_jina(self) -> JinaClient:
        if self._jina is None:
            self._jina = JinaClient(settings=self.settings)
        return self._jina

    async def _get_obscura(self) -> ObscuraClient:
        if self._obscura is None:
            self._obscura = ObscuraClient(settings=self.settings)
        return self._obscura

    async def _get_stealth(self) -> StealthSearchClient:
        if self._stealth is None:
            # headless: never surface a browser window during a consultation.
            self._stealth = StealthSearchClient(headless=True, settings=self.settings)
        return self._stealth

    def _tier_available(self, tool: str) -> bool:
        """True when ``tool`` can actually run here.

        Unknown tiers default to True: SearxNG and Jina are plain HTTP, so
        "available" for them means "worth attempting" — their failure mode is a
        network error we can report, not an impossibility. Obscura is different:
        when the binary is absent or not executable on this platform, attempting
        it can only ever waste time and bury the real error behind a bogus one.
        """
        cached = self._availability.get(tool)
        if cached is not None:
            return cached

        available = True
        detail = ""
        try:
            if tool == "obscura":
                client = ObscuraClient(settings=self.settings)
                available = client._binary_available()
                if not available:
                    binary = client._find_obscura()
                    detail = (
                        f"binary present but not executable here ({binary})"
                        if binary
                        else "binary not found"
                    )
            elif tool == "stealth":
                available = StealthSearchClient(settings=self.settings)._check_available()
                if not available:
                    detail = "playwright not installed"
        except Exception:  # noqa: BLE001 - best-effort, returns a safe default
            # A probe that raises must not disable a tier outright — attempting
            # it and failing is strictly better than skipping something usable.
            available = True

        self._availability[tool] = available
        if not available:
            self._skipped[tool] = detail or "not available in this environment"
            logger.debug("search tier %s unavailable — skipping", tool)
        return available

    def available_tiers(self) -> list[str]:
        """Tiers that can run here, in ladder order. Useful for health output."""
        return [t for t in self.TIER_ORDER if self._tier_available(t)]

    def unavailable_tiers(self) -> dict[str, str]:
        """Tiers that cannot run here, mapped to why."""
        for tool in self.TIER_ORDER:
            self._tier_available(tool)
        return dict(self._skipped)

    def _deduplicate(self, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Deduplicate results by URL, merging source information.

        Merging is not just bookkeeping. The Obscura tier re-fetches URLs that
        SearxNG/Jina already returned in order to add JS-rendered ``content``;
        this method previously kept only the first-seen record and merged the
        ``sources`` list, silently discarding that content. The expensive tier's
        entire contribution was thrown away. We now promote any field the
        incumbent record lacks.
        """
        seen: dict[str, dict[str, Any]] = {}
        for result in results:
            url = result.get("url", "")
            if not url:
                continue
            if url in seen:
                incumbent = seen[url]
                existing_sources = incumbent.get("sources", [])
                new_source = result.get("source", "")
                if new_source and new_source not in existing_sources:
                    existing_sources.append(new_source)
                    incumbent["sources"] = existing_sources
                # Promote richer values from the later (more expensive) tier
                # instead of dropping them.
                for key in ("title", "snippet", "content"):
                    incoming = result.get(key) or ""
                    if len(incoming) > len(incumbent.get(key) or ""):
                        incumbent[key] = incoming
            else:
                result["sources"] = [result.get("source", "")]
                seen[url] = result
        return list(seen.values())

    async def search(
        self,
        query: str,
        num_results: int = 10,
        min_results: int = MIN_RESULTS_THRESHOLD,
        categories: str = "general",
        language: str = "en",
        time_range: str = "",
        use_jina_fallback: bool = True,
        use_obscura_fallback: bool = True,
        use_stealth_fallback: bool = True,
    ) -> UnifiedSearchResult:
        """Search with the full fallback chain.

        Args:
            query: Search query string
            num_results: Maximum number of results to return
            min_results: Minimum results before trying fallback tools
            categories: SearxNG category filter
            language: Language code
            time_range: Recency filter passed through to SearxNG ("day",
                "week", "month", "year"). Empty means no restriction.
            use_jina_fallback: Whether to try Jina if SearxNG is insufficient
            use_obscura_fallback: Whether to try Obscura if Jina is insufficient
            use_stealth_fallback: Whether to try a stealth browser search when
                every text tier came back empty

        Returns:
            UnifiedSearchResult with merged, deduplicated results. When nothing
            was found, ``errors`` and ``error`` explain which tier failed and
            why, and ``tiers_unavailable`` names tiers that were skipped.
        """
        started = time.monotonic()
        tools_used: list[str] = []
        tools_tried: list[str] = []
        errors: dict[str, str] = {}
        all_results: list[dict[str, Any]] = []
        searxng_count = 0
        jina_count = 0
        obscura_count = 0
        stealth_count = 0

        # NOTE on fix 1.1/1.2 and grounding at THIS layer specifically:
        # unified_search.py deliberately does NOT re-ground the query here.
        # Each leaf tier it calls (SearxNGClient.search, JinaClient.search,
        # StealthSearchClient.search) already grounds internally at its own
        # network/browser boundary — that is where fixes 1.1/1.2 were
        # applied (see those modules). Re-grounding at this orchestration
        # layer was tried and reverted: it is redundant in production (the
        # leaf clients already do it) and it silently swallowed short,
        # deliberately-placeholder queries used by this module's own
        # tier-selection tests (e.g. `search("q")` with every leaf client
        # mocked out to test fan-out logic in isolation from query
        # semantics) — turning "tier X was skipped by design" into "tier X
        # was skipped because grounding ate the query first", which is a
        # different failure mode this layer must not introduce. Obscura's
        # step is unaffected either way: it re-fetches URLs, it never takes
        # a query.
        #
        # Step 1: SearxNG (always try first — free, unlimited, fast)
        if self._tier_available("searxng"):
            tools_tried.append("searxng")
            try:
                searxng = await self._get_searxng()
                searxng_resp = await searxng.search(
                    query=query,
                    num_results=num_results,
                    categories=categories,
                    language=language,
                    time_range=time_range,
                )

                for result in searxng_resp.results:
                    all_results.append({
                        "title": result.title,
                        "url": result.url,
                        "snippet": result.snippet,
                        "source": "searxng",
                        "engine": result.engine,
                        "score": result.score,
                    })
                searxng_count = len(searxng_resp.results)
                if searxng_count:
                    tools_used.append("searxng")
                else:
                    errors["searxng"] = "returned no results"

            except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
                errors["searxng"] = f"{type(exc).__name__}: {exc}"
                logger.debug("searxng search failed: %s", exc)

        # Step 2: Jina (if SearxNG returned insufficient results)
        if searxng_count < min_results and use_jina_fallback and self._tier_available("jina"):
                tools_tried.append("jina")
                try:
                    jina = await self._get_jina()
                    jina_resp = await jina.search(query=query, num_results=num_results)

                    for result in jina_resp.results:
                        all_results.append({
                            "title": result.title,
                            "url": result.url,
                            "snippet": result.snippet,
                            "source": "jina",
                            "content": result.content,
                        })
                    jina_count = len(jina_resp.results)
                    if jina_count:
                        tools_used.append("jina")
                    else:
                        errors["jina"] = "returned no results"

                except Exception as exc:  # noqa: BLE001 - failure is recorded in errors[], logged
                    errors["jina"] = f"{type(exc).__name__}: {exc}"
                    logger.debug("jina search failed: %s", exc)

        # Step 3: Obscura (if Jina also returned insufficient results).
        # Obscura does not search — it re-fetches the URLs we already have with
        # a real browser, to recover JS-rendered content the text tiers missed.
        if (searxng_count + jina_count) < min_results and use_obscura_fallback:
            top_urls = [r["url"] for r in all_results[:self.OBSCURA_MAX_URLS] if r.get("url")]
            if not top_urls:
                # Nothing to enrich. Say so rather than looking like a success.
                errors.setdefault("obscura", "no candidate URLs to render")
            elif self._tier_available("obscura"):
                tools_tried.append("obscura")
                try:
                    obscura = await self._get_obscura()
                    scrape_result = await obscura.scrape(top_urls, concurrency=3)

                    for fetch_result in scrape_result.results:
                        if fetch_result.status_code == 200 and fetch_result.content:
                            all_results.append({
                                "title": fetch_result.title,
                                "url": fetch_result.url,
                                "snippet": fetch_result.content[:200],
                                "source": "obscura",
                                "content": fetch_result.content,
                            })
                            obscura_count += 1

                    if obscura_count > 0:
                        tools_used.append("obscura")
                    else:
                        errors["obscura"] = "rendered no usable content"

                except Exception as exc:  # noqa: BLE001 - failure is recorded in errors[], logged
                    errors["obscura"] = f"{type(exc).__name__}: {exc}"
                    logger.debug("obscura enrichment failed: %s", exc)

        # Step 4: Stealth browser search — last resort, and the only tier that
        # can discover NEW sources when SearxNG is down and Jina is blocked.
        # (Obscura above can only re-render URLs we already had, so without this
        # tier a dead SearxNG plus a rate-limited Jina meant zero results with
        # no recourse, even though a working fallback existed in the codebase.)
        if not all_results and use_stealth_fallback and self._tier_available("stealth"):
                tools_tried.append("stealth")
                try:
                    stealth = await self._get_stealth()
                    stealth_results = await stealth.search(query, num_results=num_results)
                    for sr in stealth_results:
                        if not sr.url:
                            continue
                        all_results.append({
                            "title": sr.title,
                            "url": sr.url,
                            "snippet": sr.snippet,
                            "source": "stealth",
                            "engine": sr.engine,
                        })
                        stealth_count += 1

                    if stealth_count:
                        tools_used.append("stealth")
                    else:
                        errors["stealth"] = "returned no results"

                except Exception as exc:  # noqa: BLE001 - failure is recorded in errors[], logged
                    errors["stealth"] = f"{type(exc).__name__}: {exc}"
                    logger.debug("stealth search failed: %s", exc)

        # Deduplicate and sort
        all_results = self._deduplicate(all_results)
        all_results.sort(
            key=lambda r: (
                r.get("score", 0) if r.get("source") == "searxng" else 0.5,
                len(r.get("snippet", "")),
            ),
            reverse=True,
        )
        all_results = all_results[:num_results]

        unavailable = dict(self._skipped)
        error = ""
        if not all_results:
            parts = [f"{tool}: {why}" for tool, why in errors.items()]
            error = "; ".join(parts) or "no search tier produced results"
            if unavailable:
                error += f" [tiers unavailable here: {', '.join(sorted(unavailable))}]"

        return UnifiedSearchResult(
            query=query,
            results=all_results,
            total=len(all_results),
            tools_used=tools_used,
            searxng_results=searxng_count,
            jina_results=jina_count,
            obscura_results=obscura_count,
            stealth_results=stealth_count,
            took_ms=int((time.monotonic() - started) * 1000),
            tools_tried=tools_tried,
            errors=errors,
            tiers_unavailable=unavailable,
            error=error,
        )

    async def search_news(
        self,
        query: str,
        num_results: int = 10,
        time_range: str = "",
    ) -> UnifiedSearchResult:
        """Search for news articles with the fallback chain.

        Previously this forwarded ``time_range`` to :meth:`search`, which had no
        such parameter — so every call raised TypeError and the method was dead
        code. ``search`` now accepts and forwards it to SearxNG.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="news",
            time_range=time_range,
        )

    async def close(self) -> None:
        """Close all underlying clients."""
        if self._searxng:
            await self._searxng.close()
        if self._jina:
            await self._jina.close()
        if self._obscura:
            await self._obscura.close()
        if self._stealth:
            await self._stealth.close()

    async def __aenter__(self) -> UnifiedSearch:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
