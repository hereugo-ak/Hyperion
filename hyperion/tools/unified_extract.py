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
import logging
from dataclasses import dataclass, field
from typing import Any

from hyperion.tools.crawl4ai import Crawl4AIClient, CrawlResult
from hyperion.tools.camoufox_client import CamoufoxClient, CamoufoxResult
from hyperion.tools.curl_cffi_client import CurlCffiClient, CurlCffiResult
from hyperion.tools.jina import JinaClient, JinaReadResult
from hyperion.tools.nodriver_client import NodriverClient, NodriverResult
from hyperion.tools.obscura import ObscuraClient, ObscuraFetchResult
from hyperion.tools.wayback import WaybackClient, WaybackContentResult

logger = logging.getLogger(__name__)


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

    # Ladder order, cheapest first. Named so availability reporting and the
    # ladder itself cannot list different tiers.
    #
    # NOTE the position of `wayback`: it is the LAST RESORT, after camoufox.
    # The chain previously ran wayback BEFORE camoufox, contradicting both this
    # class's own docstring ("Tier 5: Camoufox … Tier 6: Wayback") and the
    # comment labelling camoufox "Tier 5". The consequence was not cosmetic: an
    # anti-bot page that camoufox could have rendered live was instead answered
    # from a Wayback snapshot, so agents silently analysed a stale archived copy
    # of a page that was available right now.
    TIER_ORDER: tuple[str, ...] = (
        "curl_cffi",
        "jina",
        "obscura",
        "nodriver",
        "crawl4ai",
        "camoufox",
        "wayback",
    )

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
        # Cached per-tier availability. The probes are cheap but not free
        # (Obscura's shells out), and the answer cannot change mid-run.
        self._availability: dict[str, bool] = {}
        self._skipped: dict[str, str] = {}

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

    # ── Capability gating ────────────────────────────────────────────────────
    #
    # THE DEFECT THIS FIXES
    # ---------------------
    # The ladder attempted every tier unconditionally, including tiers that
    # *cannot* run in the current environment. Measured on a checkout where the
    # optional stealth extras are not installed, a single `extract()` call
    # produced:
    #
    #   tools_tried : [curl_cffi, jina, obscura, nodriver, crawl4ai, wayback, camoufox]
    #   error       : curl_cffi: curl_cffi not installed; Obscura: Obscura binary
    #                 not available on linux; nodriver: nodriver not installed;
    #                 ...; Camoufox: camoufox not installed
    #
    # Four of the seven attempts were structurally impossible. Three costs, all
    # real:
    #
    #   1. Every extraction pays the construct-call-fail round trip for tools
    #      that will never work — repeated for every URL of every agent, of
    #      which an engagement makes hundreds.
    #   2. `tools_tried` is provenance. Listing a tool that never ran makes the
    #      audit trail wrong, and a genuine Obscura failure is indistinguishable
    #      from Obscura not being installed.
    #   3. The real error is buried. "no content extracted" arrives with four
    #      not-installed messages in front of the one cause that matters.
    #
    # Every client already exposed an availability probe (`_check_available()`,
    # or `_binary_available()` for Obscura) — the ladder simply never consulted
    # them. Availability is cached per instance because the answer cannot change
    # during a run, and the Obscura probe in particular shells out.

    def _tier_available(self, tool: str) -> bool:
        """True when ``tool`` can actually run here.

        Unknown tools default to True: a tier with no probe is assumed usable
        (Jina and Wayback are plain HTTP, and Crawl4AI has an httpx fallback),
        so adding a tier without a probe degrades to the old behaviour rather
        than silently disabling it.
        """
        cached = self._availability.get(tool)
        if cached is not None:
            return cached

        available = True
        try:
            if tool == "curl_cffi":
                available = CurlCffiClient(settings=self.settings)._check_available()
            elif tool == "nodriver":
                available = NodriverClient(settings=self.settings)._check_available()
            elif tool == "camoufox":
                available = CamoufoxClient(settings=self.settings)._check_available()
            elif tool == "obscura":
                available = ObscuraClient(settings=self.settings)._binary_available()
        except Exception:
            # A probe that raises must not disable a tier outright — attempting
            # it and failing is strictly better than skipping something usable.
            available = True

        self._availability[tool] = available
        if not available:
            self._skipped[tool] = "not installed/available in this environment"
            logger.debug("extraction tier %s unavailable — skipping", tool)
        return available

    def available_tiers(self) -> list[str]:
        """Tiers that can run here, in ladder order. Useful for health output."""
        return [t for t in self.TIER_ORDER if self._tier_available(t)]

    def unavailable_tiers(self) -> dict[str, str]:
        """Tiers that cannot run here, mapped to why."""
        for tool in self.TIER_ORDER:
            self._tier_available(tool)
        return dict(self._skipped)

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
        if not force_js_render and self._tier_available("curl_cffi"):
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
        #
        # Gated on the binary actually being runnable. Obscura ships as a
        # Windows .exe, so on Linux/macOS this tier was previously attempted —
        # and reported in `tools_tried` — for every single URL, despite being
        # structurally impossible.
        if self._tier_available("obscura"):
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
        if self._tier_available("nodriver"):
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

        # P12: Tier 5 — Camoufox (stealth Firefox — nuclear option)
        #
        # ORDERING FIX: camoufox now runs BEFORE wayback.
        #
        # The chain used to try Wayback first and camoufox afterwards, which
        # contradicted this class's own docstring ("Tier 5: Camoufox … Tier 6:
        # Wayback (archived version — last resort)") and the tier labels in
        # these very comments. The effect was substantive, not cosmetic: for an
        # anti-bot-protected page that camoufox can render perfectly well, the
        # chain returned a Wayback SNAPSHOT instead — so agents analysed a stale
        # archived copy, with `tool_used="wayback"`, of a page that was live and
        # fetchable at that moment. Archive-before-live is exactly backwards for
        # a system whose output is dated market analysis.
        if self._tier_available("camoufox"):
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

        # Tier 6 — Wayback Machine (genuine last resort: an ARCHIVED copy).
        # Only reached once every live-fetch tier has failed, so a snapshot can
        # no longer displace a page that is actually reachable now.
        if self._tier_available("wayback"):
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

        # Every tier failed (or was unavailable).
        #
        # The error now distinguishes the two, because "no content extracted"
        # with four "not installed" messages in front of it hides the one cause
        # that matters. Skipped tiers are named separately so a thin engagement
        # can be traced to a missing optional dependency rather than to the web.
        detail = "; ".join(errors) if errors else "no extraction tier produced content"
        if self._skipped:
            skipped = ", ".join(sorted(self._skipped))
            detail += f" [tiers unavailable here: {skipped}]"
        return UnifiedExtractResult(
            url=url,
            tools_tried=tools_tried,
            success=False,
            error=detail,
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
