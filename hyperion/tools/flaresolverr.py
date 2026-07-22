"""HYPERION — FlareSolverr Client.

FlareSolverr is a proxy server that uses a headless browser (Chromium/Undetected-Chromedriver)
to solve Cloudflare, DDoS-GUARD, and other CAPTCHA challenges, then returns the unblocked
HTML content.

Used as a fallback when:
1. SearxNG engines return CAPTCHA/403 errors
2. Jina search returns no results
3. Jina/Obscura content extraction hits a Cloudflare challenge page

API: POST http://localhost:8191/v1
Body: {"cmd": "request.get", "url": "...", "maxTimeout": 60000}
Response: {"status": "ok", "solution": {"url": "...", "status": 200, "response": "<html>..."}}

§5.1 — Tool Registry: FLARESOLVERR
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger(__name__)


@dataclass
class FlareSolverrResult:
    """Result of a FlareSolverr request."""

    url: str = ""
    status: int = 0
    html: str = ""
    markdown: str = ""
    success: bool = False
    error: str = ""
    took_ms: int = 0


class FlareSolverrClient:
    """Client for FlareSolverr — solves CAPTCHAs via headless browser.

    FlareSolverr runs as a Docker container on port 8191.
    It accepts POST requests with a URL and returns the unblocked content.

    Usage:
        client = FlareSolverrClient()
        result = await client.get("https://example.com")
        if result.success:
            print(result.html[:500])
    """

    DEFAULT_URL = "http://localhost:8191/v1"
    MAX_TIMEOUT_MS = 60000  # 60 seconds for CAPTCHA solving
    REQUEST_TIMEOUT = 90.0  # HTTP timeout for the FlareSolverr API itself

    def __init__(
        self,
        solver_url: str = "",
        max_timeout_ms: int = MAX_TIMEOUT_MS,
    ) -> None:
        self.solver_url = solver_url or self.DEFAULT_URL
        self.max_timeout_ms = max_timeout_ms
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=self.REQUEST_TIMEOUT,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def get(self, url: str, max_timeout_ms: int | None = None) -> FlareSolverrResult:
        """Fetch a URL via FlareSolverr, solving any CAPTCHA challenges.

        Args:
            url: The URL to fetch (must include http:// or https://)
            max_timeout_ms: Maximum time for FlareSolverr to solve the challenge

        Returns:
            FlareSolverrResult with the unblocked HTML content.
        """
        if not url or not url.startswith(("http://", "https://")):
            return FlareSolverrResult(url=url, error="Invalid URL")

        timeout = max_timeout_ms or self.max_timeout_ms
        client = await self._get_client()

        payload = {
            "cmd": "request.get",
            "url": url,
            "maxTimeout": timeout,
        }

        start = time.time()

        try:
            response = await client.post(self.solver_url, json=payload)
            response.raise_for_status()
            data = response.json()

            took_ms = int((time.time() - start) * 1000)

            if data.get("status") == "ok":
                solution = data.get("solution", {})
                html = solution.get("response", "")
                status_code = solution.get("status", 200)
                solved_url = solution.get("url", url)

                return FlareSolverrResult(
                    url=solved_url,
                    status=status_code,
                    html=html,
                    success=bool(html),
                    took_ms=took_ms,
                )
            else:
                error_msg = data.get("message", "Unknown FlareSolverr error")
                logger.warning("FlareSolverr failed for %s: %s", url, error_msg)
                return FlareSolverrResult(url=url, error=error_msg, took_ms=took_ms)

        except httpx.HTTPError as e:
            return FlareSolverrResult(url=url, error=f"HTTP error: {e!s:.100}")
        except (KeyError, ValueError, TypeError) as e:
            return FlareSolverrResult(url=url, error=f"Parse error: {e!s:.100}")
        except Exception as e:
            return FlareSolverrResult(url=url, error=f"Unexpected: {e!s:.100}")

    async def search_google(self, query: str, num_results: int = 10) -> list[dict]:
        """Search Google via FlareSolverr, bypassing CAPTCHA.

        Constructs a Google search URL, fetches it through FlareSolverr,
        and parses the HTML to extract search results.

        Returns:
            List of dicts with 'title', 'url', 'snippet' keys.
        """
        google_url = f"https://www.google.com/search?q={query.replace(' ', '+')}&num={num_results}"
        result = await self.get(google_url)

        if not result.success or not result.html:
            return []

        return self._parse_google_html(result.html, num_results)

    async def search_duckduckgo(self, query: str, num_results: int = 10) -> list[dict]:
        """Search DuckDuckGo via FlareSolverr, bypassing CAPTCHA.

        Returns:
            List of dicts with 'title', 'url', 'snippet' keys.
        """
        ddg_url = f"https://duckduckgo.com/html/?q={query.replace(' ', '+')}"
        result = await self.get(ddg_url)

        if not result.success or not result.html:
            return []

        return self._parse_ddg_html(result.html, num_results)

    async def search(self, query: str, num_results: int = 10) -> list[dict]:
        """Search via FlareSolverr — tries Google first, then DuckDuckGo.

        Returns:
            List of dicts with 'title', 'url', 'snippet' keys.
        """
        # Try Google first (best results)
        results = await self.search_google(query, num_results)
        if results:
            return results[:num_results]

        # Fallback to DuckDuckGo HTML
        results = await self.search_duckduckgo(query, num_results)
        return results[:num_results]

    def _parse_google_html(self, html: str, max_results: int) -> list[dict]:
        """Parse Google search results HTML into structured data."""
        import re

        results: list[dict] = []

        # Google result links are in <a href="/url?q=..."> or <a href="https://..."> within result divs
        # Pattern for Google's result links
        link_pattern = re.compile(
            r'<a[^>]+href="/url\?q=([^&"]+)[^"]*"[^>]*>(.*?)</a>',
            re.DOTALL,
        )
        # Fallback pattern for direct links
        direct_link_pattern = re.compile(
            r'<a[^>]+href="(https?://[^"]+)"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        seen_urls: set[str] = set()

        for match in link_pattern.finditer(html):
            url = match.group(1)
            # Skip Google-internal URLs
            if "google.com" in url or "googleapis.com" in url:
                continue
            if url in seen_urls:
                continue
            seen_urls.add(url)

            # Extract title (strip HTML tags)
            title_html = match.group(2)
            title = re.sub(r"<[^>]+>", "", title_html).strip()
            if not title:
                continue

            # Try to find a snippet near this result
            snippet = ""
            # Look for nearby text content
            pos = match.end()
            nearby = html[pos:pos + 500]
            snippet_match = re.search(r"<span[^>]*>(.*?)</span>", nearby, re.DOTALL)
            if snippet_match:
                snippet = re.sub(r"<[^>]+>", "", snippet_match.group(1)).strip()

            results.append({
                "title": title[:200],
                "url": url,
                "snippet": snippet[:300],
                "engine": "flaresolverr_google",
            })

            if len(results) >= max_results:
                break

        # If /url?q= pattern didn't match, try direct links
        if len(results) < max_results:
            for match in direct_link_pattern.finditer(html):
                url = match.group(1)
                if "google.com" in url or "googleapis.com" in url or "gstatic.com" in url:
                    continue
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                title_html = match.group(2)
                title = re.sub(r"<[^>]+>", "", title_html).strip()
                if not title or len(title) < 5:
                    continue

                results.append({
                    "title": title[:200],
                    "url": url,
                    "snippet": "",
                    "engine": "flaresolverr_google",
                })

                if len(results) >= max_results:
                    break

        return results

    def _parse_ddg_html(self, html: str, max_results: int) -> list[dict]:
        """Parse DuckDuckGo HTML search results into structured data."""
        import re

        results: list[dict] = []

        # DuckDuckGo HTML results have class="result__a" for links
        # and class="result__snippet" for snippets
        result_pattern = re.compile(
            r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>.*?'
            r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>',
            re.DOTALL,
        )

        seen_urls: set[str] = set()

        for match in result_pattern.finditer(html):
            url = match.group(1)
            # DDG uses redirect URLs like //duckduckgo.com/l/?uddg=...
            if "uddg=" in url:
                from urllib.parse import parse_qs, urlparse
                parsed = urlparse(url if url.startswith("http") else "https:" + url)
                qs = parse_qs(parsed.query)
                url = qs.get("uddg", [url])[0]

            if url in seen_urls:
                continue
            seen_urls.add(url)

            title = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            snippet = re.sub(r"<[^>]+>", "", match.group(3)).strip()

            if title:
                results.append({
                    "title": title[:200],
                    "url": url,
                    "snippet": snippet[:300],
                    "engine": "flaresolverr_ddg",
                })

            if len(results) >= max_results:
                break

        return results

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
