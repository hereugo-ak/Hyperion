"""
HYPERION HTTP Extract — keyless, browserless content extraction.

This is the Tier 2 extraction tool in the VIGIL fallback chain. It uses
httpx to fetch HTML and trafilatura to extract clean text/markdown —
no browser, no API key, no CAPTCHA solving, no Playwright.

It is designed to be fast and reliable for the 80% of web pages that
serve meaningful HTML to standard HTTP requests. Pages that require
JavaScript rendering fall through to browser-based tools (Obscura,
Crawl4AI, FlareSolverr) further down the chain.

Architecture reference: §5.2 — "Structured API → Jina Reader →
curl_cffi+Trafilatura → nodriver → Camoufox → Obscura(native) →
FlareSolverr"

This module implements the httpx+Trafilatura tier.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx

from hyperion.tools.content_selector import select_content

logger = logging.getLogger(__name__)

# Retained-content budget — matches ``deep_search.MAX_CONTENT_CHARS``.
#
# Fix 2.2 (§4.7 Finding B-6) leaves the number alone and changes how it is
# spent: `extract(url, query=...)` now selects the most relevant 15000 chars
# via chunk → rerank → top-k rather than head-slicing. With no query supplied
# the behaviour is byte-identical to the old head-slice, so every existing
# caller is unaffected until it opts in.
MAX_CONTENT_CHARS = 15000

# Request settings
REQUEST_TIMEOUT = 20  # seconds
MAX_RETRIES = 2
RETRY_DELAY = 1  # seconds

# Per-host jitter — random delay before each request to avoid burst patterns
JITTER_MIN = 0.5  # seconds
JITTER_MAX = 2.5  # seconds

# Track last request time per host for rate limiting
_host_last_request: dict[str, float] = {}
MIN_HOST_INTERVAL = 1.0  # min seconds between requests to same host

# Realistic browser headers to avoid basic bot detection
DEFAULT_HEADERS: dict[str, str] = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/128.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "DNT": "1",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


@dataclass
class HttpExtractResult:
    """Result of an HTTP-based extraction."""

    url: str = ""
    title: str = ""
    content: str = ""
    markdown: str = ""
    success: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "success": self.success,
            "error": self.error,
        }


class HttpExtractClient:
    """Keyless, browserless content extraction via httpx + trafilatura.

    Fetches HTML with httpx using realistic browser headers, then
    extracts clean text and markdown using trafilatura. No browser,
    no API key, no CAPTCHA solving.

    Usage:
        client = HttpExtractClient()
        result = await client.extract("https://example.com/article")
        if result.success:
            print(result.content[:500])
    """

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._use_curl_cffi = False
        self._curl_cffi_session: Any | None = None

        # Check if curl_cffi is available for TLS fingerprint impersonation
        try:
            from curl_cffi.requests import AsyncSession  # noqa: F401

            self._use_curl_cffi = True
            logger.info("HTTP Extract: curl_cffi available — using TLS fingerprint impersonation")
        except ImportError:
            logger.info("HTTP Extract: curl_cffi not available — using httpx fallback")

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(REQUEST_TIMEOUT),
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
                max_redirects=5,
            )
        return self._client

    async def _get_curl_cffi_session(self) -> Any:
        if self._curl_cffi_session is None:
            from curl_cffi.requests import AsyncSession

            self._curl_cffi_session = AsyncSession(impersonate="chrome")
        return self._curl_cffi_session

    async def _fetch_html(self, url: str) -> tuple[str, int]:
        """Fetch HTML from URL using curl_cffi if available, else httpx.

        Returns (html_content, status_code). Raises on network errors.
        """
        import asyncio

        # Per-host jitter — wait before request to avoid burst patterns
        host = urlparse(url).netloc
        now = time.monotonic()
        last = _host_last_request.get(host, 0.0)
        elapsed = now - last
        if elapsed < MIN_HOST_INTERVAL:
            wait = MIN_HOST_INTERVAL - elapsed + random.uniform(JITTER_MIN, JITTER_MAX)
            await asyncio.sleep(wait)
        else:
            # Still add small random jitter
            await asyncio.sleep(random.uniform(JITTER_MIN, JITTER_MAX))
        _host_last_request[host] = time.monotonic()

        if self._use_curl_cffi:
            session = await self._get_curl_cffi_session()
            response = await session.get(
                url,
                timeout=REQUEST_TIMEOUT,
                allow_redirects=True,
            )
            return response.text, response.status_code
        else:
            client = await self._get_client()
            response = await client.get(url)
            response.raise_for_status()
            return response.text, response.status_code

    async def extract(self, url: str, query: str = "") -> HttpExtractResult:
        """Extract content from a URL using HTTP fetch + trafilatura.

        Args:
            url: The URL to extract content from.
            query: Fix 2.2 — the question this page was retrieved to answer.
                When supplied, the ``MAX_CONTENT_CHARS`` budget is filled with
                the most *relevant* passages (chunk → rerank → top-k) instead
                of the document's first N characters. Trafilatura is the tier
                that handles PDFs and long institutional documents, so this is
                precisely where a blind head-slice did the most damage: it kept
                the title page and table of contents and dropped the tables.
                Omitted ⇒ head-slice, exactly as before.

        Returns:
            HttpExtractResult with extracted content, or error status.
        """
        import asyncio

        for attempt in range(MAX_RETRIES):
            try:
                html, status_code = await self._fetch_html(url)

                if not html or len(html) < 200:
                    return HttpExtractResult(
                        url=url,
                        success=False,
                        error="Response too short or empty",
                    )

                # Extract with trafilatura
                import trafilatura

                # Extract clean text
                text = trafilatura.extract(
                    html,
                    include_comments=False,
                    include_tables=True,
                    favor_precision=True,
                )

                if not text or len(text) < 100:
                    # Try with less strict settings
                    text = trafilatura.extract(html, include_comments=False)

                if not text or len(text) < 100:
                    return HttpExtractResult(
                        url=url,
                        success=False,
                        error="Trafilatura extracted insufficient content",
                    )

                # Extract markdown
                markdown = trafilatura.extract(
                    html,
                    output_format="markdown",
                    include_comments=False,
                    include_tables=True,
                )

                # Extract metadata
                metadata = trafilatura.extract(
                    html,
                    output_format="xml",
                    include_comments=False,
                ) or ""

                # Try to get title from metadata
                title = ""
                if metadata:
                    import re
                    title_match = re.search(r'<doc[^>]*title="([^"]*)"', metadata)
                    if title_match:
                        title = title_match.group(1)

                # Fallback: extract title from HTML
                if not title:
                    import re
                    title_match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
                    if title_match:
                        title = title_match.group(1).strip()[:200]

                # Fix 2.2: relevance-aware budget fill. `content` and
                # `markdown` are selected independently because trafilatura's
                # markdown output has different chunk boundaries (it retains
                # table markup, which `_TABLE_MARKERS` scores as evidence) —
                # ranking the plain-text rendering and then slicing markdown to
                # match would misalign the two.
                content = select_content(
                    text or "", query, budget_chars=MAX_CONTENT_CHARS
                )
                md = select_content(
                    markdown or text or "", query, budget_chars=MAX_CONTENT_CHARS
                )

                return HttpExtractResult(
                    url=url,
                    title=title,
                    content=content,
                    markdown=md,
                    success=True,
                )

            except httpx.HTTPStatusError as e:
                if e.response.status_code in (403, 429, 503):
                    # Anti-bot block — don't retry, let fallback chain handle it
                    return HttpExtractResult(
                        url=url,
                        success=False,
                        error=f"HTTP {e.response.status_code} — anti-bot block",
                    )
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return HttpExtractResult(
                    url=url,
                    success=False,
                    error=f"HTTP {e.response.status_code}",
                )

            except (httpx.RequestError, httpx.HTTPError) as e:
                logger.debug("HTTP extract failed for %s (attempt %d): %s", url, attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return HttpExtractResult(
                    url=url,
                    success=False,
                    error=f"Request error: {e!s:.200}",
                )

            except ImportError:
                return HttpExtractResult(
                    url=url,
                    success=False,
                    error="trafilatura not installed — run: pip install trafilatura",
                )

            except (ValueError, RuntimeError, OSError) as e:
                logger.debug("HTTP extract error for %s: %s", url, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return HttpExtractResult(
                    url=url,
                    success=False,
                    error=f"Extraction error: {e!s:.200}",
                )

            except Exception as e:
                # curl_cffi errors or other unexpected exceptions
                logger.debug("HTTP extract failed for %s (attempt %d): %s", url, attempt + 1, e)
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_DELAY * (attempt + 1))
                    continue
                return HttpExtractResult(
                    url=url,
                    success=False,
                    error=f"Fetch error: {e!s:.200}",
                )

        return HttpExtractResult(
            url=url,
            success=False,
            error="Max retries exceeded",
        )

    async def extract_batch(
        self,
        urls: list[str],
        concurrency: int = 5,
        query: str = "",
    ) -> list[HttpExtractResult]:
        """Extract content from multiple URLs concurrently.

        Args:
            urls: List of URLs to extract from.
            concurrency: Maximum concurrent requests.
            query: Fix 2.2 — forwarded to :meth:`extract` for relevance-aware
                budget fill. One query per batch, because a batch is by
                construction the set of URLs discovered for a single question.

        Returns:
            List of HttpExtractResult, one per URL (in order).
        """
        import asyncio

        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded_extract(url: str) -> HttpExtractResult:
            async with semaphore:
                return await self.extract(url, query)

        results = await asyncio.gather(*[_bounded_extract(u) for u in urls])
        return list(results)

    async def close(self) -> None:
        """Close the HTTP client and curl_cffi session."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
        if self._curl_cffi_session is not None:
            try:
                await self._curl_cffi_session.close()
            except Exception:
                pass
            self._curl_cffi_session = None

    async def __aenter__(self) -> HttpExtractClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
