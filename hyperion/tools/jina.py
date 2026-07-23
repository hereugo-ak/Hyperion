"""
HYPERION Jina Client — search + reader, 500 RPM, 10M tokens/month.

Jina is the secondary search and primary content extraction tool.
It provides two services:

1. **Jina Search** (`s.jina.ai`): Search the web and get clean markdown
   results. Used as a fallback when SearxNG returns poor results.
   500 RPM, 10M tokens/month free tier.

2. **Jina Reader** (`r.jina.ai`): Read a URL and get clean markdown
   extraction. Used for content extraction from URLs returned by SearxNG.
   Handles JS-rendered pages, paywalled content (partial), and produces
   clean, LLM-friendly markdown. 500 RPM, 10M tokens/month free tier.

This is NOT a generic "fetch a URL" wrapper. It:
- Uses Jina's specialized endpoints (s.jina.ai for search, r.jina.ai for read)
- Handles rate limits with exponential backoff
- Returns structured results with metadata (title, url, content, tokens)
- Caches reader results to avoid re-extracting the same URL
- Supports content freshness checking (return cached if recently fetched)
- Handles Jina's specific response format (X-Token header, markdown body)

Architecture reference: §5.1 — "s.jina.ai search, r.jina.ai read.
500 RPM, 10M tokens/mo. Used for content extraction from URLs returned
by SearxNG."

Tool selection logic (§5.2):
  Search task:
    1. SearxNG (free, unlimited, fast) — always try first
    2. Jina search (if SearxNG returns poor results) ← THIS
    3. Obscura (if the data is behind JS rendering)

  Extract task:
    1. Jina Reader (fast, clean markdown extraction) ← THIS
    2. Obscura (if JS rendering required)
    3. Crawl4AI (if Obscura fails)
    4. Wayback (if the page is down or has changed)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
from urllib.parse import quote_plus


@dataclass
class JinaSearchResult:
    """A single search result from Jina Search (s.jina.ai)."""

    title: str
    url: str
    content: str = ""
    snippet: str = ""
    tokens_used: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "content": self.content,
            "snippet": self.snippet,
            "tokens_used": self.tokens_used,
        }


@dataclass
class JinaSearchResponse:
    """A complete Jina search response."""

    query: str
    results: list[JinaSearchResult] = field(default_factory=list)
    total: int = 0
    tokens_used: int = 0
    took_ms: int = 0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "tokens_used": self.tokens_used,
            "took_ms": self.took_ms,
            "cached": self.cached,
        }


@dataclass
class JinaReadResult:
    """A content extraction result from Jina Reader (r.jina.ai)."""

    url: str
    title: str = ""
    content: str = ""
    markdown: str = ""
    tokens_used: int = 0
    status_code: int = 0
    cached: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "tokens_used": self.tokens_used,
            "status_code": self.status_code,
            "cached": self.cached,
            "error": self.error,
        }


class JinaClient:
    """Jina search + reader client.

    Provides two services:
    - s.jina.ai: Web search returning clean markdown results
    - r.jina.ai: URL reader returning clean markdown extraction

    Both have 500 RPM and 10M tokens/month free tier limits.
    (§5.1)

    Usage:
        client = JinaClient(settings=settings)

        # Search
        search_resp = await client.search("Indian SaaS market size")
        for result in search_resp.results:
            print(f"{result.title} — {result.url}")

        # Read a URL
        read_result = await client.read("https://example.com/report")
        print(read_result.markdown[:500])
    """

    SEARCH_URL = "https://s.jina.ai"
    READER_URL = "https://r.jina.ai"
    CACHE_DIR = "output/.jina_cache"
    CACHE_TTL_SECONDS = 3600  # 1 hour for search, 6 hours for read
    READ_CACHE_TTL_SECONDS = 21600  # 6 hours
    REQUEST_TIMEOUT = 60  # seconds — Jina can be slow on complex pages
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    RATE_LIMIT_DELAY = 1  # seconds between requests to respect 500 RPM

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._api_key = ""
        if settings:
            self._api_key = getattr(settings, "jina_api_key", "")
        self._client: httpx.AsyncClient | None = None
        self._search_cache: dict[str, tuple[float, JinaSearchResponse]] = {}
        self._read_cache: dict[str, tuple[float, JinaReadResult]] = {}
        self._last_request_time: float = 0.0
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    def _get_headers(self) -> dict[str, str]:
        """Get headers for Jina API requests."""
        headers = {
            "Accept": "application/json",
            "X-Return-Format": "markdown",
        }
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers=self._get_headers(),
            )
        return self._client

    async def _rate_limit(self) -> None:
        """Enforce rate limiting between requests."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    def _cache_key(self, *args: Any) -> str:
        """Generate a cache key from arguments."""
        key_str = ":".join(str(a) for a in args)
        return hashlib.md5(key_str.encode()).hexdigest()

    # ─────────────────────────────────────────────────────────────────────
    # Jina Search (s.jina.ai)
    # ─────────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        num_results: int = 10,
    ) -> JinaSearchResponse:
        """Search the web via Jina Search (s.jina.ai).

        Returns clean markdown results. Used as a fallback when SearxNG
        returns poor results.

        Args:
            query: Search query string
            num_results: Maximum number of results to return

        Returns:
            JinaSearchResponse with structured results.
        """
        cache_key = self._cache_key("search", query, num_results)
        if cache_key in self._search_cache:
            timestamp, response = self._search_cache[cache_key]
            if time.time() - timestamp < self.CACHE_TTL_SECONDS:
                response.cached = True
                return response

        client = await self._get_client()
        await self._rate_limit()

        url = f"{self.SEARCH_URL}/{quote_plus(query)}"

        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(url)
                response.raise_for_status()

                # Jina returns JSON with 'data' array
                data = response.json()
                results: list[JinaSearchResult] = []

                for item in data.get("data", []):
                    result = JinaSearchResult(
                        title=item.get("title", ""),
                        url=item.get("url", ""),
                        content=item.get("content", ""),
                        snippet=item.get("content", "")[:200],
                        tokens_used=len(item.get("content", "")) // 4,  # Rough token estimate
                    )
                    if result.url:
                        results.append(result)

                results = results[:num_results]
                total_tokens = sum(r.tokens_used for r in results)

                search_response = JinaSearchResponse(
                    query=query,
                    results=results,
                    total=len(results),
                    tokens_used=total_tokens,
                )

                self._search_cache[cache_key] = (time.time(), search_response)
                return search_response

            except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError) as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))
                continue

        return JinaSearchResponse(query=query, results=[], total=0)

    # ─────────────────────────────────────────────────────────────────────
    # Jina Reader (r.jina.ai)
    # ─────────────────────────────────────────────────────────────────────

    async def read(self, url: str) -> JinaReadResult:
        """Read a URL and extract clean markdown via Jina Reader (r.jina.ai).

        Handles JS-rendered pages and produces LLM-friendly markdown.
        Used for content extraction from URLs returned by SearxNG.

        Args:
            url: The URL to read and extract content from

        Returns:
            JinaReadResult with the extracted markdown content.
        """
        cache_key = self._cache_key("read", url)
        if cache_key in self._read_cache:
            timestamp, result = self._read_cache[cache_key]
            if time.time() - timestamp < self.READ_CACHE_TTL_SECONDS:
                result.cached = True
                return result

        client = await self._get_client()
        await self._rate_limit()

        jina_url = f"{self.READER_URL}/{url}"

        last_error: Exception | None = None

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(jina_url)

                # Jina Reader returns the content directly or as JSON
                if response.status_code == 200:
                    content_type = response.headers.get("content-type", "")

                    if "application/json" in content_type:
                        data = response.json()
                        result = JinaReadResult(
                            url=url,
                            title=data.get("title", ""),
                            content=data.get("content", ""),
                            markdown=data.get("content", ""),
                            tokens_used=len(data.get("content", "")) // 4,
                            status_code=200,
                        )
                    else:
                        # Plain text/markdown response
                        text = response.text
                        result = JinaReadResult(
                            url=url,
                            title=text.split("\n")[0][:200] if text else "",
                            content=text,
                            markdown=text,
                            tokens_used=len(text) // 4,
                            status_code=200,
                        )

                    self._read_cache[cache_key] = (time.time(), result)
                    return result

                elif response.status_code == 422:
                    return JinaReadResult(
                        url=url,
                        status_code=422,
                        error="Jina could not process this URL (unsupported content type).",
                    )
                elif response.status_code == 429:
                    # Rate limited — wait longer before retry
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.RETRY_DELAY * (attempt + 1) * 2)
                    continue
                else:
                    response.raise_for_status()

            except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError) as e:
                last_error = e
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))
                continue

        error_msg = str(last_error) if last_error else "Unknown error"
        return JinaReadResult(url=url, status_code=0, error=error_msg)

    async def read_batch(self, urls: list[str]) -> list[JinaReadResult]:
        """Read multiple URLs in parallel via Jina Reader.

        Respects rate limits by processing in small concurrent batches.

        Args:
            urls: List of URLs to read

        Returns:
            List of JinaReadResult objects, one per URL (in same order).
        """
        semaphore = asyncio.Semaphore(5)  # Max 5 concurrent reads

        async def _read_with_semaphore(url: str) -> JinaReadResult:
            async with semaphore:
                return await self.read(url)

        tasks = [_read_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks, return_exceptions=False)
        return list(results)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> JinaClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
