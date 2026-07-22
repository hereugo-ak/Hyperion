"""
HYPERION CamoufoxClient — stealth Firefox for anti-bot bypass.

Uses ``camoufox`` (patched Firefox with fingerprint spoofing) for
sites that detect and block Chrome-based automation. This is the
nuclear option in the extraction ladder — used only when nodriver
also gets detected.

When ``camoufox`` is not installed, the client gracefully degrades —
``extract()`` returns an error result, and the unified extract chain
falls through to the next tool.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

from hyperion.obs import trace

logger = logging.getLogger(__name__)


@dataclass
class CamoufoxResult:
    """Result of a Camoufox extraction."""

    url: str
    title: str = ""
    content: str = ""
    markdown: str = ""
    html: str = ""
    status_code: int = 0
    success: bool = False
    error: str = ""
    took_ms: int = 0
    rendered: bool = False


class CamoufoxClient:
    """Stealth Firefox automation client for anti-bot bypass.

    Uses ``camoufox`` (patched Firefox with fingerprint spoofing) for
    sites that detect Chrome-based automation. Falls back gracefully
    when the package is not installed.
    """

    DEFAULT_TIMEOUT = 30
    WAIT_FOR_CONTENT = 2

    def __init__(self, headless: bool = True, settings: Any = None) -> None:
        self.headless = headless
        self.settings = settings
        self._browser: Any = None
        self._available: bool | None = None

    def _check_available(self) -> bool:
        """Check if camoufox is available."""
        if self._available is not None:
            return self._available
        try:
            import camoufox  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            logger.info("camoufox not installed — CamoufoxClient will degrade gracefully")
        return self._available

    async def _ensure_browser(self) -> Any:
        """Start the browser if not already running."""
        if self._browser is not None:
            return self._browser
        if not self._check_available():
            raise RuntimeError("camoufox not installed")

        from camoufox.async_api import AsyncCamoufox
        self._browser = AsyncCamoufox(headless=self.headless)
        await self._browser.start()
        return self._browser

    async def extract(
        self,
        url: str,
        wait_for: str | None = None,
        timeout: int | None = None,
    ) -> CamoufoxResult:
        """Extract content from a URL using stealth Firefox.

        Args:
            url: URL to extract content from
            wait_for: CSS selector to wait for before extracting
            timeout: Page load timeout in seconds

        Returns:
            CamoufoxResult with extracted content.
        """
        if not self._check_available():
            return CamoufoxResult(url=url, error="camoufox not installed")

        import time
        start = time.time()
        timeout = timeout or self.DEFAULT_TIMEOUT

        trace("extract", tool="camoufox", url=url, status="start")

        try:
            browser = await self._ensure_browser()
            page = await browser.new_page()
            await page.goto(url, timeout=timeout * 1000)

            # Wait for dynamic content
            await asyncio.sleep(self.WAIT_FOR_CONTENT)

            if wait_for:
                try:
                    await page.wait_for_selector(wait_for, timeout=timeout * 1000)
                except Exception:
                    pass

            html = await page.content()
            title = await page.title() or ""
            content = self._html_to_text(html)

            took_ms = int((time.time() - start) * 1000)

            trace("extract", tool="camoufox", url=url, status="ok",
                  took_ms=took_ms, content_len=len(content))

            await page.close()

            return CamoufoxResult(
                url=url,
                title=title,
                content=content,
                markdown=content,
                html=html,
                status_code=200,
                success=True,
                took_ms=took_ms,
                rendered=True,
            )

        except Exception as e:
            took_ms = int((time.time() - start) * 1000)
            trace("extract", tool="camoufox", url=url, status="error",
                  error=str(e)[:200], took_ms=took_ms)
            return CamoufoxResult(
                url=url,
                error=str(e)[:500],
                took_ms=took_ms,
            )

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text using Trafilatura or regex fallback."""
        try:
            from trafilatura import extract
            text = extract(html, include_comments=False, include_tables=True)
            if text and len(text) > 50:
                return text
        except ImportError:
            pass
        except Exception:
            pass

        import re
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        text = re.sub(r"<[^>]+>", " ", html)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    async def close(self) -> None:
        """Close the browser."""
        if self._browser is not None:
            try:
                await self._browser.stop()
            except Exception:
                pass
            self._browser = None
