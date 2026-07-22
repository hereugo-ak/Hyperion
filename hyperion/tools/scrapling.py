"""
HYPERION Scrapling Client — adaptive web scraper, middle-tier extractor.

This is NOT a generic scraping wrapper. Scrapling (D4Vinci/Scrapling, 69K stars,
v0.4.11, BSD-3) is an adaptive web scraping framework with anti-bot bypass
(Cloudflare Turnstile), Playwright integration, and adaptive selectors that
survive page changes.

Position in the VIGIL extraction fallback chain (§5.2 updated):
  1. Obscura (stealth, fast, JS rendering)
  2. Scrapling (adaptive, anti-bot, Playwright)  ← THIS TOOL
  3. Jina Reader (fast, simple extraction)
  4. Crawl4AI (heavy extraction, PDFs)
  5. Wayback (if page is down or changed)

Use cases:
  - Pages where Obscura gets flagged but aren't behind Cloudflare
  - Pages with dynamic selectors that change frequently
  - Pages needing Playwright-level browser automation
  - Batch scraping with adaptive parsing

Dependencies: scrapling>=0.4.11 (pip install scrapling)
System requirement: scrapling install (downloads Chromium for StealthyFetcher)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx


# ─────────────────────────────────────────────────────────────────────────────
# Data Models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ScraplingResult:
    """Result of a single Scrapling fetch operation."""

    url: str = ""
    title: str = ""
    content: str = ""
    markdown: str = ""
    html: str = ""
    status_code: int = 0
    success: bool = False
    error: str = ""
    tool_used: str = ""  # "stealthy" | "dynamic" | "httpx-fallback"
    took_ms: int = 0
    selector_recovered: bool = False  # True if adaptive selectors recovered

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "html": self.html,
            "status_code": self.status_code,
            "success": self.success,
            "error": self.error,
            "tool_used": self.tool_used,
            "took_ms": self.took_ms,
            "selector_recovered": self.selector_recovered,
        }


@dataclass
class ScraplingBatchResult:
    """Result of a batch Scrapling scrape operation."""

    results: list[ScraplingResult] = field(default_factory=list)
    total: int = 0
    succeeded: int = 0
    failed: int = 0
    took_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "took_ms": self.took_ms,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Scrapling Client
# ─────────────────────────────────────────────────────────────────────────────


class ScraplingClient:
    """Adaptive web scraper — middle-tier extractor in the VIGIL chain.

    Uses Scrapling's StealthyFetcher (Playwright + anti-bot bypass) by default.
    Falls back to DynamicFetcher (lighter, no stealth) if stealth fails.
    Falls back to httpx + basic HTML parsing if scrapling is not installed.

    Usage:
        client = ScraplingClient(settings=settings)
        result = await client.fetch("https://competitor.com/pricing")
        if result.success:
            print(f"Extracted via {result.tool_used}: {result.content[:200]}")

        # Batch scraping:
        batch = await client.scrape_batch(["https://a.com", "https://b.com"])
    """

    # Content truncation — 15000 chars per VIGIL upgrade plan Step 1.6
    MAX_CONTENT_CHARS = 15000

    # Batch concurrency
    DEFAULT_BATCH_CONCURRENCY = 10

    # HTTP fallback settings
    HTTPX_TIMEOUT = 30.0
    HTTPX_HEADERS = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/125.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._fetcher: Any | None = None  # Lazy init StealthyFetcher
        self._dynamic_fetcher: Any | None = None  # Lazy init DynamicFetcher
        self._httpx_client: httpx.AsyncClient | None = None
        self._scrapling_available: bool | None = None  # None = not checked yet

    async def _check_scrapling(self) -> bool:
        """Check if scrapling is installed and available."""
        if self._scrapling_available is not None:
            return self._scrapling_available
        try:
            import scrapling  # noqa: F401
            self._scrapling_available = True
        except ImportError:
            self._scrapling_available = False
        return self._scrapling_available

    async def _get_stealthy_fetcher(self) -> Any:
        """Get or create the StealthyFetcher instance (lazy init)."""
        if self._fetcher is None:
            from scrapling.fetchers import StealthyFetcher
            self._fetcher = StealthyFetcher
        return self._fetcher

    async def _get_dynamic_fetcher(self) -> Any:
        """Get or create the DynamicFetcher instance (lazy init)."""
        if self._dynamic_fetcher is None:
            from scrapling.fetchers import DynamicFetcher
            self._dynamic_fetcher = DynamicFetcher
        return self._dynamic_fetcher

    async def _get_httpx_client(self) -> httpx.AsyncClient:
        """Get or create the httpx client for fallback."""
        if self._httpx_client is None:
            self._httpx_client = httpx.AsyncClient(
                timeout=self.HTTPX_TIMEOUT,
                headers=self.HTTPX_HEADERS,
                follow_redirects=True,
            )
        return self._httpx_client

    def _html_to_markdown(self, html: str) -> str:
        """Convert HTML to basic markdown.

        This is a lightweight converter for the httpx fallback path.
        Scrapling's native fetchers return parsed content directly.
        """
        if not html:
            return ""

        # Remove script and style tags
        text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL | re.IGNORECASE)

        # Convert common HTML elements to markdown
        text = re.sub(r"<h1[^>]*>(.*?)</h1>", r"\n# \1\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<h2[^>]*>(.*?)</h2>", r"\n## \1\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<h3[^>]*>(.*?)</h3>", r"\n### \1\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<h4[^>]*>(.*?)</h4>", r"\n#### \1\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", text, flags=re.IGNORECASE)
        text = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", text, flags=re.IGNORECASE)
        text = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", text, flags=re.IGNORECASE)
        text = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", text, flags=re.IGNORECASE)
        text = re.sub(r"<a[^>]*href=\"([^\"]+)\"[^>]*>(.*?)</a>", r"[\2](\1)", text, flags=re.IGNORECASE)

        # Strip remaining HTML tags
        text = re.sub(r"<[^>]+>", " ", text)

        # Clean up whitespace
        text = re.sub(r"\n{3,}", "\n\n", text)
        text = re.sub(r"[ \t]+", " ", text)
        text = text.strip()

        return text

    def _extract_title(self, html: str) -> str:
        """Extract title from HTML."""
        match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
        if match:
            return match.group(1).strip()
        return ""

    async def _fetch_with_scrapling(
        self,
        url: str,
        stealth: bool,
    ) -> ScraplingResult:
        """Fetch using Scrapling's StealthyFetcher or DynamicFetcher."""
        import time as _time
        start = _time.monotonic()

        try:
            if stealth:
                fetcher = await self._get_stealthy_fetcher()
                # StealthyFetcher.fetch returns an Adaptor object
                page = await asyncio.wait_for(
                    fetcher.fetch(url=url),
                    timeout=60.0,
                )
                tool_used = "stealthy"
            else:
                fetcher = await self._get_dynamic_fetcher()
                page = await asyncio.wait_for(
                    fetcher.fetch(url=url),
                    timeout=60.0,
                )
                tool_used = "dynamic"

            took_ms = int((_time.monotonic() - start) * 1000)

            # Extract content from Scrapling's Adaptor object
            # Scrapling Adaptor provides .get_all_text(), .css_select(), .xpath_select()
            content = ""
            html = ""
            title = ""

            if hasattr(page, "get_all_text"):
                content = page.get_all_text() or ""
            elif hasattr(page, "text"):
                content = page.text or ""

            if hasattr(page, "html_content"):
                html = page.html_content or ""
            elif hasattr(page, "html"):
                html = page.html or ""

            # Try to get title via CSS selector
            if hasattr(page, "css_select"):
                try:
                    title_el = page.css_select("title")
                    if title_el:
                        title = title_el[0].text or ""
                except Exception:
                    pass

            if not title and html:
                title = self._extract_title(html)

            # Check if adaptive selectors were used (Scrapling feature)
            selector_recovered = hasattr(page, "auto_match") and getattr(page, "auto_match", False)

            # Truncate content
            content = content[: self.MAX_CONTENT_CHARS]
            markdown = self._html_to_markdown(html)[: self.MAX_CONTENT_CHARS] if html else content

            status_code = getattr(page, "status", 200)

            return ScraplingResult(
                url=url,
                title=title,
                content=content,
                markdown=markdown,
                html=html,
                status_code=status_code,
                success=bool(content),
                tool_used=tool_used,
                took_ms=took_ms,
                selector_recovered=selector_recovered,
            )

        except asyncio.TimeoutError:
            took_ms = int((_time.monotonic() - start) * 1000)
            return ScraplingResult(
                url=url,
                success=False,
                error="Scrapling fetch timed out (60s)",
                tool_used="stealthy" if stealth else "dynamic",
                took_ms=took_ms,
            )
        except Exception as e:
            took_ms = int((_time.monotonic() - start) * 1000)
            return ScraplingResult(
                url=url,
                success=False,
                error=f"Scrapling: {e!s:.200}",
                tool_used="stealthy" if stealth else "dynamic",
                took_ms=took_ms,
            )

    async def _fetch_with_httpx(self, url: str) -> ScraplingResult:
        """Fallback: fetch using httpx + basic HTML parsing."""
        import time as _time
        start = _time.monotonic()

        try:
            client = await self._get_httpx_client()
            response = await asyncio.wait_for(
                client.get(url),
                timeout=self.HTTPX_TIMEOUT,
            )

            took_ms = int((_time.monotonic() - start) * 1000)
            html = response.text
            title = self._extract_title(html)
            markdown = self._html_to_markdown(html)
            content = markdown[: self.MAX_CONTENT_CHARS]

            return ScraplingResult(
                url=url,
                title=title,
                content=content,
                markdown=markdown[: self.MAX_CONTENT_CHARS],
                html=html[: self.MAX_CONTENT_CHARS * 2],  # Keep more HTML for parsing
                status_code=response.status_code,
                success=bool(content) and response.status_code == 200,
                tool_used="httpx-fallback",
                took_ms=took_ms,
            )

        except asyncio.TimeoutError:
            took_ms = int((_time.monotonic() - start) * 1000)
            return ScraplingResult(
                url=url,
                success=False,
                error="httpx fetch timed out",
                tool_used="httpx-fallback",
                took_ms=took_ms,
            )
        except Exception as e:
            took_ms = int((_time.monotonic() - start) * 1000)
            return ScraplingResult(
                url=url,
                success=False,
                error=f"httpx: {e!s:.200}",
                tool_used="httpx-fallback",
                took_ms=took_ms,
            )

    async def fetch(self, url: str, stealth: bool = True) -> ScraplingResult:
        """Fetch a single URL with adaptive parsing.

        Uses StealthyFetcher (Playwright + anti-bot) by default.
        Falls back to DynamicFetcher if stealth fails.
        Falls back to httpx + basic HTML parsing if scrapling is not installed.

        Args:
            url: URL to fetch
            stealth: Use StealthyFetcher (default) vs DynamicFetcher

        Returns:
            ScraplingResult with content, metadata, and provenance.
        """
        if not url or not url.startswith(("http://", "https://")):
            return ScraplingResult(url=url, success=False, error="Invalid URL")

        # Check if scrapling is available
        scrapling_available = await self._check_scrapling()

        if scrapling_available:
            # Try stealth first (if requested)
            if stealth:
                result = await self._fetch_with_scrapling(url, stealth=True)
                if result.success:
                    return result

                # Fall back to dynamic fetcher
                result = await self._fetch_with_scrapling(url, stealth=False)
                if result.success:
                    return result
            else:
                result = await self._fetch_with_scrapling(url, stealth=False)
                if result.success:
                    return result

            # Both scrapling methods failed — try httpx fallback
            return await self._fetch_with_httpx(url)

        # Scrapling not installed — use httpx fallback
        return await self._fetch_with_httpx(url)

    async def scrape_batch(
        self,
        urls: list[str],
        concurrency: int = DEFAULT_BATCH_CONCURRENCY,
        stealth: bool = True,
    ) -> ScraplingBatchResult:
        """Batch scrape multiple URLs in parallel.

        Uses asyncio.gather with semaphore for concurrency control.
        Each URL gets adaptive parsing — selectors auto-recover
        when page structure changes.

        Args:
            urls: List of URLs to scrape
            concurrency: Maximum concurrent fetches
            stealth: Use StealthyFetcher (default) vs DynamicFetcher

        Returns:
            ScraplingBatchResult with results for each URL (in same order).
        """
        import time as _time
        start = _time.monotonic()

        if not urls:
            return ScraplingBatchResult()

        semaphore = asyncio.Semaphore(concurrency)

        async def _fetch_with_semaphore(url: str) -> ScraplingResult:
            async with semaphore:
                return await self.fetch(url, stealth=stealth)

        tasks = [_fetch_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks)

        succeeded = sum(1 for r in results if r.success)
        failed = len(results) - succeeded
        took_ms = int((_time.monotonic() - start) * 1000)

        return ScraplingBatchResult(
            results=list(results),
            total=len(results),
            succeeded=succeeded,
            failed=failed,
            took_ms=took_ms,
        )

    async def close(self) -> None:
        """Clean up resources."""
        if self._httpx_client:
            await self._httpx_client.aclose()
            self._httpx_client = None

        # Scrapling fetchers don't need explicit cleanup —
        # Playwright browser instances are managed by Scrapling internally
        self._fetcher = None
        self._dynamic_fetcher = None

    async def __aenter__(self) -> ScraplingClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
