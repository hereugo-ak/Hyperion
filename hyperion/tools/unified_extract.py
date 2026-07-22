"""
HYPERION Unified Extract — Obscura → Crawl4AI → Jina → Wayback fallback chain.

This is NOT a generic "extract content from URL" wrapper. It implements
the exact tool selection logic from §5.2:

  Extract task:
    1. Jina Reader (fast, clean markdown extraction)
    2. Obscura (if JS rendering required — pricing calculators,
       interactive dashboards, review sites)
    3. Crawl4AI (if Obscura fails — heavy extraction, PDFs)
    4. Wayback (if the page is down or has changed)

Extraction fallback chain (§5.3):
  Obscura (stealth, JS rendering)
    → Crawl4AI (heavy extraction, PDFs)
      → Jina Reader (fast, simple extraction)
        → Wayback (if page is down or changed)

The unified extract chain:
1. Tries Jina Reader first (fastest, cleanest markdown extraction)
2. If Jina fails or returns poor content, tries Obscura (JS rendering)
3. If Obscura fails, tries Crawl4AI (heavy extraction, PDFs)
4. If all fail, tries Wayback Machine (archived version of the page)
5. Returns the best extraction with provenance (which tool succeeded)
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any

from hyperion.tools.crawl4ai import Crawl4AIClient, CrawlResult
from hyperion.tools.camoufox_client import CamoufoxClient, CamoufoxResult
from hyperion.tools.curl_cffi_client import CurlCffiClient, CurlCffiResult
from hyperion.tools.jina import JinaClient, JinaReadResult
from hyperion.tools.nodriver_client import NodriverClient, NodriverResult
from hyperion.tools.obscura import ObscuraClient, ObscuraFetchResult
from hyperion.tools.wayback import WaybackClient, WaybackContentResult


@dataclass
class UnifiedExtractResult:
    """A unified extraction result from the fallback chain."""

    url: str
    title: str = ""
    content: str = ""
    markdown: str = ""
    html: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    tool_used: str = ""
    tools_tried: list[str] = field(default_factory=list)
    success: bool = False
    error: str = ""
    took_ms: int = 0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "html": self.html,
            "links": self.links,
            "tables": self.tables,
            "tool_used": self.tool_used,
            "tools_tried": self.tools_tried,
            "success": self.success,
            "error": self.error,
            "took_ms": self.took_ms,
            "cached": self.cached,
        }


class UnifiedExtract:
    """Unified extraction with tiered cheap-first fallback chain.

    P12: Implements the tiered cheap-first extraction ladder from IV.1.4:

      Tier 0: curl_cffi (TLS fingerprint spoof — cheapest, no browser)
      Tier 1: Jina Reader (fast, clean markdown extraction)
      Tier 2: Obscura (JS rendering — local binary)
      Tier 3: nodriver (undetected Chrome — for JS-heavy anti-bot sites)
      Tier 4: Crawl4AI (heavy extraction, PDFs)
      Tier 5: Camoufox (stealth Firefox — nuclear option for anti-bot)
      Tier 6: Wayback (archived version — last resort)

    Each tier is tried in order. If a tier succeeds with quality content,
    we return immediately — no need to try more expensive tiers.

    Usage:
        extractor = UnifiedExtract(settings=settings)
        result = await extractor.extract("https://competitor.com/pricing")
        if result.success:
            print(f"Extracted via {result.tool_used}: {result.content[:200]}")
    """

    MIN_CONTENT_LENGTH = 100  # Minimum content length to consider extraction successful
    JINA_TIMEOUT = 30
    OBSCURA_TIMEOUT = 60
    CRAWL4AI_TIMEOUT = 120
    WAYBACK_TIMEOUT = 30
    CURL_CFFI_TIMEOUT = 20
    NODRIVER_TIMEOUT = 30
    CAMOUFOX_TIMEOUT = 30

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._jina: JinaClient | None = None
        self._obscura: ObscuraClient | None = None
        self._crawl4ai: Crawl4AIClient | None = None
        self._wayback: WaybackClient | None = None
        # P12: New stealth extraction tiers
        self._curl_cffi: CurlCffiClient | None = None
        self._nodriver: NodriverClient | None = None
        self._camoufox: CamoufoxClient | None = None

    async def _get_jina(self) -> JinaClient:
        if self._jina is None:
            self._jina = JinaClient(settings=self.settings)
        return self._jina

    async def _get_obscura(self) -> ObscuraClient:
        if self._obscura is None:
            self._obscura = ObscuraClient(settings=self.settings)
        return self._obscura

    async def _get_crawl4ai(self) -> Crawl4AIClient:
        if self._crawl4ai is None:
            self._crawl4ai = Crawl4AIClient(settings=self.settings)
        return self._crawl4ai

    async def _get_wayback(self) -> WaybackClient:
        if self._wayback is None:
            self._wayback = WaybackClient(settings=self.settings)
        return self._wayback

    async def _get_curl_cffi(self) -> CurlCffiClient:
        if self._curl_cffi is None:
            self._curl_cffi = CurlCffiClient(settings=self.settings)
        return self._curl_cffi

    async def _get_nodriver(self) -> NodriverClient:
        if self._nodriver is None:
            self._nodriver = NodriverClient(settings=self.settings)
        return self._nodriver

    async def _get_camoufox(self) -> CamoufoxClient:
        if self._camoufox is None:
            self._camoufox = CamoufoxClient(settings=self.settings)
        return self._camoufox

    def _is_quality_content(self, content: str) -> bool:
        """Check if extracted content meets quality thresholds."""
        if not content or len(content) < self.MIN_CONTENT_LENGTH:
            return False
        # Check it's not just an error message or boilerplate
        error_indicators = ["404", "not found", "access denied", "forbidden", "captcha"]
        content_lower = content.lower()
        error_count = sum(1 for indicator in error_indicators if indicator in content_lower)
        # If more than 2 error indicators in first 500 chars, likely an error page
        if error_count > 2 and len(content) < 500:
            return False
        return True

    async def extract(
        self,
        url: str,
        extract_tables: bool = True,
        extract_links: bool = True,
        force_js_render: bool = False,
    ) -> UnifiedExtractResult:
        """Extract content from a URL with the full fallback chain.

        Args:
            url: URL to extract content from
            extract_tables: Whether to extract tables as structured data
            extract_links: Whether to extract all links
            force_js_render: Skip Jina, go straight to Obscura (for JS-heavy pages)

        Returns:
            UnifiedExtractResult with the best extraction available.
        """
        tools_tried: list[str] = []
        errors: list[str] = []

        # P12: Tier 0 — curl_cffi (TLS fingerprint spoof — cheapest)
        if not force_js_render:
            tools_tried.append("curl_cffi")
            try:
                cffi = await self._get_curl_cffi()
                cffi_result = await cffi.fetch(url, timeout=self.CURL_CFFI_TIMEOUT)

                if cffi_result.success and self._is_quality_content(cffi_result.markdown):
                    return UnifiedExtractResult(
                        url=url,
                        content=cffi_result.markdown,
                        markdown=cffi_result.markdown,
                        tool_used="curl_cffi",
                        tools_tried=tools_tried,
                        success=True,
                    )
                elif cffi_result.error:
                    errors.append(f"curl_cffi: {cffi_result.error}")

            except (ConnectionError, RuntimeError, OSError) as e:
                errors.append(f"curl_cffi: {e}")

        # Step 1: Jina Reader (fastest — try first unless JS rendering is required)
        if not force_js_render:
            tools_tried.append("jina")
            try:
                jina = await self._get_jina()
                jina_result = await jina.read(url)

                if jina_result.status_code == 200 and self._is_quality_content(jina_result.markdown):
                    return UnifiedExtractResult(
                        url=url,
                        title=jina_result.title,
                        content=jina_result.content,
                        markdown=jina_result.markdown,
                        tool_used="jina",
                        tools_tried=tools_tried,
                        success=True,
                    )
                elif jina_result.error:
                    errors.append(f"Jina: {jina_result.error}")

            except (ConnectionError, RuntimeError, OSError) as e:
                errors.append(f"Jina: {e}")

        # Step 2: Obscura (JS rendering — for interactive pages)
        tools_tried.append("obscura")
        try:
            obscura = await self._get_obscura()
            obscura_result = await obscura.fetch(url, output_format="markdown")

            if obscura_result.status_code == 200 and self._is_quality_content(obscura_result.markdown):
                return UnifiedExtractResult(
                    url=url,
                    title=obscura_result.title,
                    content=obscura_result.content,
                    markdown=obscura_result.markdown,
                    tool_used="obscura",
                    tools_tried=tools_tried,
                    success=True,
                )
            elif obscura_result.error:
                errors.append(f"Obscura: {obscura_result.error}")

        except (ConnectionError, RuntimeError, OSError) as e:
            errors.append(f"Obscura: {e}")

        # P12: Tier 3 — nodriver (undetected Chrome — for JS-heavy anti-bot sites)
        tools_tried.append("nodriver")
        try:
            nodriver = await self._get_nodriver()
            nodriver_result = await nodriver.extract(url, timeout=self.NODRIVER_TIMEOUT)

            if nodriver_result.success and self._is_quality_content(nodriver_result.content):
                return UnifiedExtractResult(
                    url=url,
                    title=nodriver_result.title,
                    content=nodriver_result.content,
                    markdown=nodriver_result.markdown,
                    html=nodriver_result.html,
                    tool_used="nodriver",
                    tools_tried=tools_tried,
                    success=True,
                )
            elif nodriver_result.error:
                errors.append(f"nodriver: {nodriver_result.error}")

        except (ConnectionError, RuntimeError, OSError) as e:
            errors.append(f"nodriver: {e}")

        # Step 4: Crawl4AI (heavy extraction — for complex pages, PDFs)
        tools_tried.append("crawl4ai")
        try:
            crawl4ai = await self._get_crawl4ai()
            crawl_result = await crawl4ai.crawl(
                url=url,
                extract_tables=extract_tables,
                extract_links=extract_links,
            )

            if crawl_result.status_code == 200 and self._is_quality_content(crawl_result.markdown):
                return UnifiedExtractResult(
                    url=url,
                    title=crawl_result.title,
                    content=crawl_result.content,
                    markdown=crawl_result.markdown,
                    html=crawl_result.html,
                    links=crawl_result.links,
                    tables=crawl_result.tables,
                    tool_used="crawl4ai",
                    tools_tried=tools_tried,
                    success=True,
                )
            elif crawl_result.error:
                errors.append(f"Crawl4AI: {crawl_result.error}")

        except (ConnectionError, RuntimeError, OSError) as e:
            errors.append(f"Crawl4AI: {e}")

        # Step 4: Wayback Machine (last resort — archived version)
        tools_tried.append("wayback")
        try:
            wayback = await self._get_wayback()
            wayback_result = await wayback.fetch_snapshot(url)

            if wayback_result.status_code == 200 and self._is_quality_content(wayback_result.content):
                return UnifiedExtractResult(
                    url=url,
                    title=wayback_result.title,
                    content=wayback_result.content,
                    markdown=wayback_result.content,
                    tool_used="wayback",
                    tools_tried=tools_tried,
                    success=True,
                )
            elif wayback_result.error:
                errors.append(f"Wayback: {wayback_result.error}")

        except (ConnectionError, RuntimeError, OSError) as e:
            errors.append(f"Wayback: {e}")

        # P12: Tier 5 — Camoufox (stealth Firefox — nuclear option)
        tools_tried.append("camoufox")
        try:
            camoufox = await self._get_camoufox()
            camoufox_result = await camoufox.extract(url, timeout=self.CAMOUFOX_TIMEOUT)

            if camoufox_result.success and self._is_quality_content(camoufox_result.content):
                return UnifiedExtractResult(
                    url=url,
                    title=camoufox_result.title,
                    content=camoufox_result.content,
                    markdown=camoufox_result.markdown,
                    html=camoufox_result.html,
                    tool_used="camoufox",
                    tools_tried=tools_tried,
                    success=True,
                )
            elif camoufox_result.error:
                errors.append(f"Camoufox: {camoufox_result.error}")

        except (ConnectionError, RuntimeError, OSError) as e:
            errors.append(f"Camoufox: {e}")

        # All tools failed
        return UnifiedExtractResult(
            url=url,
            tools_tried=tools_tried,
            success=False,
            error="; ".join(errors),
        )

    async def extract_pdf(self, url: str) -> UnifiedExtractResult:
        """Extract text from a PDF file.

        Uses Crawl4AI's PDF extraction capability (PyMuPDF or PyPDF2).
        """
        tools_tried: list[str] = []

        try:
            crawl4ai = await self._get_crawl4ai()
            tools_tried.append("crawl4ai")
            pdf_result = await crawl4ai.crawl_pdf(url)

            if pdf_result.status_code == 200 and self._is_quality_content(pdf_result.content):
                return UnifiedExtractResult(
                    url=url,
                    title=pdf_result.title,
                    content=pdf_result.content,
                    markdown=pdf_result.content,
                    tool_used="crawl4ai-pdf",
                    tools_tried=tools_tried,
                    success=True,
                )
            elif pdf_result.error:
                return UnifiedExtractResult(
                    url=url,
                    tools_tried=tools_tried,
                    success=False,
                    error=pdf_result.error,
                )

        except (ConnectionError, RuntimeError, OSError) as e:
            return UnifiedExtractResult(
                url=url,
                tools_tried=tools_tried,
                success=False,
                error=str(e),
            )

        return UnifiedExtractResult(
            url=url,
            tools_tried=tools_tried,
            success=False,
            error="PDF extraction failed",
        )

    async def extract_batch(
        self,
        urls: list[str],
        concurrency: int = 5,
    ) -> list[UnifiedExtractResult]:
        """Extract content from multiple URLs in parallel.

        Args:
            urls: List of URLs to extract
            concurrency: Maximum concurrent extractions

        Returns:
            List of UnifiedExtractResult objects, one per URL (in same order).
        """
        semaphore = asyncio.Semaphore(concurrency)

        async def _extract_with_semaphore(url: str) -> UnifiedExtractResult:
            async with semaphore:
                return await self.extract(url)

        tasks = [_extract_with_semaphore(url) for url in urls]
        results = await asyncio.gather(*tasks)
        return list(results)

    async def close(self) -> None:
        """Close all underlying clients."""
        if self._jina:
            await self._jina.close()
        if self._obscura:
            await self._obscura.close()
        if self._crawl4ai:
            await self._crawl4ai.close()
        if self._wayback:
            await self._wayback.close()
        if self._curl_cffi:
            await self._curl_cffi.close()
        if self._nodriver:
            await self._nodriver.close()
        if self._camoufox:
            await self._camoufox.close()

    async def __aenter__(self) -> UnifiedExtract:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
