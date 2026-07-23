"""
HYPERION SearxNG Client — self-hosted meta-search, free, unlimited.

SearxNG is the primary search tool for ALL specialists. It aggregates
70+ search engines, has no API key, no rate limit, and no tracking.
It runs in Docker at the URL configured in settings.searxng_url.

This is NOT a generic "search the web" wrapper. It:
- Uses the SearxNG JSON API (/search?q=...&format=json)
- Supports category filtering (general, images, news, files, it, science)
- Supports language and time range filtering
- Returns structured results: title, url, snippet, engine, score
- Caches results to minimize redundant queries
- Handles network errors gracefully with retries
- Deduplicates results by URL

Architecture reference: §5.1 — "Self-hosted meta-search, free, unlimited.
Docker-based. Aggregates 70+ search engines. No API key, no rate limit,
no tracking."

Tool selection logic (§5.2):
  Search task:
    1. SearxNG (free, unlimited, fast) — always try first
    2. Jina search (if SearxNG returns poor results)
    3. Obscura (if the data is behind JS rendering)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote_plus

import httpx

from hyperion.tools.jina import JinaClient

logger = logging.getLogger(__name__)


@dataclass
class SearchResult:
    """A single search result from SearxNG."""

    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    score: float = 0.0
    category: str = "general"
    published_date: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "engine": self.engine,
            "score": self.score,
            "category": self.category,
            "published_date": self.published_date,
        }

    def get(self, key: str, default: Any = "") -> Any:
        """Dict-like access for compatibility with agents that use .get()."""
        mapping = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.snippet,
            "engine": self.engine,
            "score": self.score,
            "category": self.category,
            "published_date": self.published_date,
        }
        return mapping.get(key, default)


@dataclass
class SearchResponse:
    """A complete search response from SearxNG."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    total: int = 0
    took_ms: int = 0
    engines_used: list[str] = field(default_factory=list)
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "took_ms": self.took_ms,
            "engines_used": self.engines_used,
            "cached": self.cached,
        }

    def __iter__(self):
        """Iterate over results, yielding SearchResult items."""
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, key):
        """Support indexing and slicing: response[0], response[:5]."""
        return self.results[key]

    def __bool__(self) -> bool:
        return bool(self.results)


class SearxNGClient:
    """SearxNG meta-search client.

    Self-hosted meta-search that aggregates 70+ search engines.
    No API key, no rate limit, no tracking. Docker-based.
    (§5.1)

    Usage:
        client = SearxNGClient(settings=settings)
        response = await client.search("Indian SaaS market size 2024", num_results=10)
        for result in response.results:
            print(f"{result.title} — {result.url}")
    """

    CACHE_DIR = "output/.searxng_cache"
    CACHE_TTL_SECONDS = 3600  # 1 hour
    REQUEST_TIMEOUT = 45  # seconds — must match SearxNG max_request_timeout
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    MAX_CONCURRENT = 10  # allow more parallel searches across 12 specialists

    # Search budget cap — 200 discovery searches per engagement
    # 12 specialists × ~10-15 searches each + 3 sub-agents × ~3 searches each = 150-200
    SEARCH_BUDGET_CAP = 200

    # Class-level semaphore shared across all instances
    _semaphore: asyncio.Semaphore | None = None
    _search_count: int = 0
    _budget_exceeded: bool = False

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._base_url = "http://localhost:8888"
        if settings:
            self._base_url = getattr(settings, "searxng_url", "http://localhost:8888")
        self._base_url = self._base_url.rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, SearchResponse]] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        if SearxNGClient._semaphore is None:
            SearxNGClient._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

    @property
    def base_url(self) -> str:
        """Public accessor for the SearxNG base URL."""
        return self._base_url

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers={"Accept": "application/json"},
            )
        return self._client

    def _cache_key(self, query: str, **kwargs: Any) -> str:
        """Generate a cache key from query and parameters."""
        key_str = f"{query}:{kwargs}"
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached(self, key: str) -> SearchResponse | None:
        """Get a cached response if it exists and is not expired."""
        if key in self._cache:
            timestamp, response = self._cache[key]
            if time.time() - timestamp < self.CACHE_TTL_SECONDS:
                response.cached = True
                return response
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, response: SearchResponse) -> None:
        """Cache a response."""
        self._cache[key] = (time.time(), response)

    def _deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """Deduplicate results by URL, keeping the highest-scored version."""
        seen: dict[str, SearchResult] = {}
        for result in results:
            if result.url in seen:
                if result.score > seen[result.url].score:
                    seen[result.url] = result
            else:
                seen[result.url] = result
        return list(seen.values())

    @classmethod
    def reset_budget(cls) -> None:
        """Reset the search budget counter — called at the start of each engagement."""
        cls._search_count = 0
        cls._budget_exceeded = False

    @classmethod
    def get_search_count(cls) -> int:
        """Return the current search count for this engagement."""
        return cls._search_count

    async def _search_searxng_json(
        self,
        query: str,
        num_results: int,
        categories: str,
        language: str,
        time_range: str,
        engines: str,
        safesearch: int,
    ) -> SearchResponse | None:
        """Query the SearXNG JSON API directly.

        SearXNG aggregates 70+ search engines in a single request.
        No API key, no rate limit, no browser, no CAPTCHA.
        Returns None if the request fails or SearXNG is unavailable.
        """
        client = await self._get_client()

        params: dict[str, Any] = {
            "q": query,
            "format": "json",
            "categories": categories,
            "language": language,
            "safesearch": safesearch,
        }
        if time_range:
            params["time_range"] = time_range
        if engines:
            params["engines"] = engines

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get("/search", params=params)
                response.raise_for_status()

                data = response.json()
                raw_results = data.get("results", [])

                results: list[SearchResult] = []
                engines_used_set: set[str] = set()

                for item in raw_results:
                    url = item.get("url", "")
                    if not url:
                        continue
                    engine_name = item.get("engine", "unknown")
                    engines_used_set.add(engine_name)
                    results.append(SearchResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("content", ""),
                        engine=engine_name,
                        score=float(item.get("score", 1.0)),
                        category=item.get("category", categories),
                        published_date=item.get("publishedDate", ""),
                    ))

                if results:
                    results = self._deduplicate(results)[:num_results]
                    return SearchResponse(
                        query=query,
                        results=results,
                        total=len(results),
                        took_ms=int(data.get("number_of_results", 0)),
                        engines_used=sorted(engines_used_set),
                    )

                # Log unresponsive engines for debugging
                unresponsive = data.get("unresponsive_engines", [])
                if unresponsive:
                    logger.warning(
                        "SearXNG unresponsive engines for '%s': %s",
                        query[:80], unresponsive,
                    )

                # SearXNG returned zero results — don't retry, engines are likely blocked
                logger.debug("SearXNG returned 0 results for '%s' (attempt %d)", query, attempt + 1)
                break  # No point retrying if engines are blocked/CAPTCHA'd

            except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError) as e:
                logger.warning("SearXNG JSON API error (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))
                continue

        return None

    async def _search_jina_fallback(
        self,
        query: str,
        num_results: int,
        categories: str,
    ) -> SearchResponse | None:
        """Fallback: search via Jina (s.jina.ai).

        Jina is keyless and reliable but returns fewer results.
        Only used when SearXNG is unavailable or returns nothing.
        """
        if not self.settings:
            return None

        try:
            jina = JinaClient(settings=self.settings)
            jina_resp = await jina.search(query=query, num_results=num_results)
            await jina.close()

            if jina_resp.results:
                results: list[SearchResult] = []
                for jr in jina_resp.results:
                    results.append(SearchResult(
                        title=jr.title,
                        url=jr.url,
                        snippet=jr.snippet,
                        engine="jina",
                        score=1.0,
                        category=categories,
                    ))

                if results:
                    results = self._deduplicate(results)[:num_results]
                    return SearchResponse(
                        query=query,
                        results=results,
                        total=len(results),
                        took_ms=jina_resp.took_ms,
                        engines_used=["jina"],
                    )
        except (httpx.HTTPError, httpx.RequestError, RuntimeError, OSError) as e:
            logger.warning("Jina fallback search failed: %s", e)

        return None

    # Only use engines that are reliable (no CAPTCHA/403 issues)
    # Must match searxng_settings.yml engine list
    RELIABLE_ENGINES = "bing,wikipedia,arxiv,github,hackernews"

    async def search(
        self,
        query: str,
        num_results: int = 10,
        categories: str = "general",
        language: str = "en",
        time_range: str = "",
        engines: str = "",
        safesearch: int = 0,
        max_results: int | None = None,
    ) -> SearchResponse:
        """Search via SearXNG JSON API — the primary discovery engine.

        SearXNG aggregates 70+ search engines in a single request.
        No API key, no rate limit, no browser, no CAPTCHA.
        If SearXNG is unavailable or returns no results, falls back to Jina.

        Search budget cap: 60 discovery searches per engagement (§5.2).
        Cached results do not count against the budget.

        Args:
            query: Search query string
            num_results: Maximum number of results to return
            categories: Search category (general, images, news, it, science)
            language: Language code (en, fr, de, etc.)
            time_range: Time filter (day, week, month, year, or empty)
            engines: Comma-separated list of specific engines to use
            safesearch: Safe search level (0=off, 1=moderate, 2=strict)

        Returns:
            SearchResponse with deduplicated, scored results.
        """
        if max_results is not None:
            num_results = max_results

        # Use reliable engines by default to avoid CAPTCHA/403 from flaky defaults
        effective_engines = engines if engines else self.RELIABLE_ENGINES

        cache_key = self._cache_key(query, num_results=num_results, categories=categories,
                                     language=language, time_range=time_range, engines=effective_engines)
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        # Enforce search budget cap (cached results don't count)
        if SearxNGClient._search_count >= SearxNGClient.SEARCH_BUDGET_CAP:
            if not SearxNGClient._budget_exceeded:
                logger.warning("Search budget cap reached (%d searches) — returning cached/empty",
                               SearxNGClient.SEARCH_BUDGET_CAP)
                SearxNGClient._budget_exceeded = True
            return SearchResponse(query=query, results=[], total=0, engines_used=[])

        SearxNGClient._search_count += 1

        assert SearxNGClient._semaphore is not None
        async with SearxNGClient._semaphore:
            # ── PRIMARY: SearXNG JSON API ──
            searxng_response = await self._search_searxng_json(
                query=query,
                num_results=num_results,
                categories=categories,
                language=language,
                time_range=time_range,
                engines=effective_engines,
                safesearch=safesearch,
            )

            if searxng_response and searxng_response.results:
                self._set_cached(cache_key, searxng_response)
                return searxng_response

            # ── FALLBACK: Jina Search (s.jina.ai) ──
            logger.info("SearXNG returned no results for '%s' — falling back to Jina", query)
            jina_response = await self._search_jina_fallback(
                query=query,
                num_results=num_results,
                categories=categories,
            )

            if jina_response and jina_response.results:
                self._set_cached(cache_key, jina_response)
                return jina_response

        # All search paths exhausted
        logger.warning("All search paths exhausted for query: '%s'", query)
        return SearchResponse(
            query=query,
            results=[],
            total=0,
            engines_used=[],
        )

    async def search_images(
        self,
        query: str,
        num_results: int = 5,
        safesearch: int = 1,
    ) -> SearchResponse:
        """Search for images via SearxNG.

        Uses the 'images' category to find image results.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="images",
            safesearch=safesearch,
        )

    async def search_news(
        self,
        query: str,
        num_results: int = 10,
        time_range: str = "",
        language: str = "en",
    ) -> SearchResponse:
        """Search for news articles via SearxNG.

        Uses the 'news' category with optional time range filtering.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="news",
            time_range=time_range,
            language=language,
        )

    async def search_science(
        self,
        query: str,
        num_results: int = 10,
    ) -> SearchResponse:
        """Search for scientific/academic content via SearxNG.

        Uses the 'science' category which targets academic databases.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="science",
        )

    async def search_it(
        self,
        query: str,
        num_results: int = 10,
    ) -> SearchResponse:
        """Search for IT/technology content via SearxNG.

        Uses the 'it' category which targets tech-specific engines.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="it",
        )

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> SearxNGClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
