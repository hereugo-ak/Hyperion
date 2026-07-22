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
from dataclasses import dataclass
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# Content truncation — match deep_search MAX_CONTENT_CHARS
MAX_CONTENT_CHARS = 15000

# Request settings
REQUEST_TIMEOUT = 20  # seconds
MAX_RETRIES = 2
RETRY_DELAY = 1  # seconds

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

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(REQUEST_TIMEOUT),
                headers=DEFAULT_HEADERS,
                follow_redirects=True,
                max_redirects=5,
            )
        return self._client

    async def extract(self, url: str) -> HttpExtractResult:
        """Extract content from a URL using HTTP fetch + trafilatura.

        Args:
            url: The URL to extract content from.

        Returns:
            HttpExtractResult with extracted content, or error status.
        """
        import asyncio

        client = await self._get_client()

        for attempt in range(MAX_RETRIES):
            try:
                response = await client.get(url)
                response.raise_for_status()

                html = response.text
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

                content = (text or "")[:MAX_CONTENT_CHARS]
                md = (markdown or text or "")[:MAX_CONTENT_CHARS]

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

        return HttpExtractResult(
            url=url,
            success=False,
            error="Max retries exceeded",
        )

    async def extract_batch(
        self,
        urls: list[str],
        concurrency: int = 5,
    ) -> list[HttpExtractResult]:
        """Extract content from multiple URLs concurrently.

        Args:
            urls: List of URLs to extract from.
            concurrency: Maximum concurrent requests.

        Returns:
            List of HttpExtractResult, one per URL (in order).
        """
        import asyncio

        semaphore = asyncio.Semaphore(concurrency)

        async def _bounded_extract(url: str) -> HttpExtractResult:
            async with semaphore:
                return await self.extract(url)

        results = await asyncio.gather(*[_bounded_extract(u) for u in urls])
        return list(results)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> HttpExtractClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
