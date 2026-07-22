"""
HYPERION CurlCffiClient — TLS fingerprint spoofing for anti-bot bypass.

Uses ``curl_cffi`` to make HTTP requests with browser-like TLS
fingerprints (Chrome, Firefox, Safari). This bypasses TLS-based
bot detection that blocks standard ``requests`` / ``httpx`` calls.

This is the first tier in the cheap-first extraction ladder (IV.1.4):
cheaper than a full browser, but bypasses most TLS fingerprinting.

When ``curl_cffi`` is not installed, the client gracefully degrades.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from hyperion.obs import trace

logger = logging.getLogger(__name__)


@dataclass
class CurlCffiResult:
    """Result of a curl_cffi fetch."""

    url: str
    status_code: int = 0
    content: str = ""
    markdown: str = ""
    headers: dict[str, str] = None  # type: ignore
    success: bool = False
    error: str = ""
    took_ms: int = 0
    impersonated: str = ""  # Which browser was impersonated


class CurlCffiClient:
    """HTTP client with TLS fingerprint spoofing via curl_cffi.

    Makes requests that appear to come from a real browser by spoofing
    the TLS fingerprint. This is the cheapest stealth layer — no
    browser process needed, just a curl call with the right JA3 hash.
    """

    DEFAULT_IMPERSONATE = "chrome"  # chrome, firefox, safari, edge
    DEFAULT_TIMEOUT = 20

    def __init__(self, settings: Any = None, impersonate: str | None = None) -> None:
        self.settings = settings
        self.impersonate = impersonate or self.DEFAULT_IMPERSONATE
        self._available: bool | None = None

    def _check_available(self) -> bool:
        if self._available is not None:
            return self._available
        try:
            import curl_cffi  # noqa: F401
            self._available = True
        except ImportError:
            self._available = False
            logger.info("curl_cffi not installed — CurlCffiClient will degrade gracefully")
        return self._available

    async def fetch(
        self,
        url: str,
        impersonate: str | None = None,
        timeout: int | None = None,
        headers: dict[str, str] | None = None,
    ) -> CurlCffiResult:
        """Fetch a URL with TLS fingerprint spoofing.

        Args:
            url: URL to fetch
            impersonate: Browser to impersonate (chrome, firefox, safari)
            timeout: Request timeout in seconds
            headers: Additional headers

        Returns:
            CurlCffiResult with the response content.
        """
        if not self._check_available():
            return CurlCffiResult(url=url, error="curl_cffi not installed")

        from curl_cffi import requests as cffi_requests

        imp = impersonate or self.impersonate
        timeout = timeout or self.DEFAULT_TIMEOUT
        start = time.time()

        trace("extract", tool="curl_cffi", url=url, status="start", impersonate=imp)

        try:
            # curl_cffi is sync — run in executor
            import asyncio
            loop = asyncio.get_event_loop()

            def _do_fetch():
                return cffi_requests.get(
                    url,
                    impersonate=imp,
                    timeout=timeout,
                    headers=headers or {},
                    allow_redirects=True,
                )

            response = await loop.run_in_executor(None, _do_fetch)

            content = response.text
            markdown = self._html_to_text(content)

            took_ms = int((time.time() - start) * 1000)

            trace("extract", tool="curl_cffi", url=url, status="ok",
                  took_ms=took_ms, status_code=response.status_code,
                  content_len=len(markdown))

            return CurlCffiResult(
                url=url,
                status_code=response.status_code,
                content=content,
                markdown=markdown,
                headers=dict(response.headers),
                success=response.status_code == 200,
                took_ms=took_ms,
                impersonated=imp,
            )

        except Exception as e:
            took_ms = int((time.time() - start) * 1000)
            trace("extract", tool="curl_cffi", url=url, status="error",
                  error=str(e)[:200], took_ms=took_ms)
            return CurlCffiResult(
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
        """No persistent resources to close."""
        pass
