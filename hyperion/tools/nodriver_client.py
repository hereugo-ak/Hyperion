"""
HYPERION NodriverClient — undetected Chrome automation for JS-heavy sites.

Uses ``nodriver`` (undetected-chromedriver successor) to render and
extract content from JavaScript-heavy pages that Jina Reader and
HTTP-based extractors cannot handle. This is the SOTA stealth browser
layer from IV.1.4 / P12.

The client is async, headless by default, and integrates into the
unified extract chain as a high-tier fallback for pages that require
full browser rendering with anti-detection.

When ``nodriver`` is not installed, the client gracefully degrades —
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
class NodriverResult:
    """Result of a nodriver extraction."""

    url: str
    title: str = ""
    content: str = ""
    markdown: str = ""
    html: str = ""
    status_code: int = 0
    success: bool = False
    error: str = ""
    took_ms: int = 0
    rendered: bool = False  # Whether JS rendering was required


class NodriverClient:
    """Undetected Chrome automation client for JS-heavy page extraction.

    Uses ``nodriver`` for anti-detection Chrome automation. Falls back
    gracefully when the package is not installed.
    """

    DEFAULT_TIMEOUT = 30  # seconds
    WAIT_FOR_SELECTOR = 2  # seconds to wait for dynamic content

    def __init__(self, headless: bool = True, settings: Any = None) -> None:
        self.headless = headless
        self.settings = settings
        self._browser: Any = None
        self._available: bool | None = None

    def _check_available(self) -> bool:
        """Check if nodriver is available."""
        if self._available is not None:
            return self._available
        try:
            import nodriver  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            logger.info("nodriver not installed — NodriverClient will degrade gracefully")
        return self._available

    async def _ensure_browser(self) -> Any:
        """Start the browser if not already running."""
        if self._browser is not None:
            return self._browser
        if not self._check_available():
            raise RuntimeError("nodriver not installed")
        import nodriver
        self._browser = await nodriver.start(
            headless=self.headless,
            sandbox=False,
        )
        return self._browser

    async def extract(
        self,
        url: str,
        wait_for: str | None = None,
        timeout: int | None = None,
    ) -> NodriverResult:
        """Extract content from a URL using undetected Chrome.

        Args:
            url: URL to extract content from
            wait_for: CSS selector to wait for before extracting
            timeout: Page load timeout in seconds

        Returns:
            NodriverResult with extracted content.
        """
        if not self._check_available():
            return NodriverResult(url=url, error="nodriver not installed")

        import time
        start = time.time()
        timeout = timeout or self.DEFAULT_TIMEOUT

        trace("extract", tool="nodriver", url=url, status="start")

        try:
            browser = await self._ensure_browser()
            page = await browser.get(url)

            # Wait for page load
            await asyncio.sleep(self.WAIT_FOR_SELECTOR)

            # Wait for specific selector if provided
            if wait_for:
                try:
                    await page.wait_for(wait_for, timeout=timeout)
                except Exception:
                    pass  # Selector not found — proceed anyway

            # Extract page content
            html = await page.get_content()
            title = await page.evaluate("document.title") or ""

            # Convert HTML to text (simple extraction — no external deps)
            content = self._html_to_text(html)

            took_ms = int((time.time() - start) * 1000)

            trace("extract", tool="nodriver", url=url, status="ok",
                  took_ms=took_ms, content_len=len(content))

            return NodriverResult(
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
            trace("extract", tool="nodriver", url=url, status="error",
                  error=str(e)[:200], took_ms=took_ms)
            return NodriverResult(
                url=url,
                error=str(e)[:500],
                took_ms=took_ms,
            )

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to plain text using a lightweight approach.

        Uses Trafilatura if available, otherwise falls back to a
        regex-based approach that strips tags and preserves text.
        """
        try:
            from trafilatura import extract
            text = extract(html, include_comments=False, include_tables=True)
            if text and len(text) > 50:
                return text
        except ImportError:
            pass
        except Exception:
            pass

        # Fallback: regex-based HTML to text
        import re
        # Remove script and style elements
        html = re.sub(r"<(script|style)[^>]*>.*?</\1>", "", html, flags=re.DOTALL | re.IGNORECASE)
        # Remove tags
        text = re.sub(r"<[^>]+>", " ", html)
        # Normalize whitespace
        text = re.sub(r"\s+", " ", text).strip()
        return text

    async def close(self) -> None:
        """Close the browser."""
        if self._browser is not None:
            try:
                self._browser.stop()
            except Exception:
                pass
            self._browser = None
