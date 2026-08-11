"""
HYPERION Firecrawl client — self-hosted crawl/scrape engine (OVERHAUL4 P7).

WHY THIS TIER EXISTS
--------------------
The extraction ladder's browser tiers (obscura/nodriver/camoufox) spin a
local browser per page — slow and heavy for the 10-URL batch a sub-agent
wants. Firecrawl self-hosted (`firecrawl/firecrawl`) consolidates scraping,
crawling and extraction behind one HTTP API and does **parallel multi-URL
work server-side**:

- ``POST /v1/scrape``          — one URL -> clean markdown/HTML (JS rendering
  via the companion playwright service when configured)
- ``POST /v1/batch/scrape``    — many URLs in ONE parallel request (the
  ladder's 10-URL batch becomes a single round trip instead of 3 waves of
  per-URL calls)
- ``POST /v1/crawl``           — async site-wide crawl (start + poll)
- ``POST /v1/map``             — discover every URL on a site

RANK IN THE LADDER (cheap-first): ``curl_cffi -> jina -> http -> firecrawl ->
obscura -> nodriver -> crawl4ai -> ...`` — firecrawl sits immediately after
the no-JS tiers because it is one self-hosted HTTP call, cheaper per page
than any local browser tier, and reaches the most sites.

ERROR-SAFE CONTRACT: no ``firecrawl_url`` configured, endpoint unreachable,
HTTP error, or any exception -> logged + ``None``/failure result. A dead
firecrawl must never take the ladder down with it — the tier is skipped and
the next tier runs.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

FIRECRAWL_DEFAULT_URL = "http://localhost:3002"
FIRECRAWL_TIMEOUT = 30.0
FIRECRAWL_BATCH_LIMIT = 100  # firecrawl's documented max URLs per batch


@dataclass
class FirecrawlScrapeResult:
    """One scraped page from Firecrawl (or one item of a batch response)."""

    url: str
    title: str = ""
    markdown: str = ""
    html: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    error: str = ""
    took_ms: int = 0

    @property
    def success(self) -> bool:
        return bool(self.markdown or self.html) and not self.error


class FirecrawlClient:
    """Keyless client for a self-hosted Firecrawl instance. Always fail-open."""

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._base_url = FIRECRAWL_DEFAULT_URL
        if settings is not None:
            self._base_url = str(
                getattr(settings, "firecrawl_url", "") or FIRECRAWL_DEFAULT_URL
            ).rstrip("/")
        self._client: httpx.AsyncClient | None = None
        self._probed: bool | None = None

    # ── plumbing ─────────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(FIRECRAWL_TIMEOUT),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                },
            )
        return self._client

    async def available(self) -> bool:
        """True when the self-hosted instance answers. Cached; never raises."""
        if self._probed is not None:
            return self._probed
        try:
            client = await self._get_client()
            # /test is firecrawl's health endpoint; fall back to a HEAD on /
            for probe in ("/test", "/"):
                try:
                    response = await client.get(probe)
                    if response.status_code < 500:
                        self._probed = True
                        return True
                except httpx.HTTPError:
                    continue
            self._probed = False
        except Exception as exc:  # noqa: BLE001 - probe must never raise
            logger.debug("firecrawl availability probe failed: %s", exc)
            self._probed = False
        return self._probed

    # ── single URL ───────────────────────────────────────────────────────

    async def scrape(self, url: str) -> FirecrawlScrapeResult:
        """Scrape one URL to clean markdown (JS-rendered when playwright
        service is configured). Never raises."""
        if not url:
            return FirecrawlScrapeResult(url=url, error="no url")
        start = time.monotonic()
        try:
            if not await self.available():
                return FirecrawlScrapeResult(
                    url=url, error="firecrawl self-host not reachable"
                )
            client = await self._get_client()
            payload: dict[str, Any] = {
                "url": url,
                "formats": ["markdown"],
                "onlyMainContent": True,
            }
            response = await client.post(
                f"{self._base_url}/v1/scrape",
                json=payload,
            )
            if response.status_code >= 400:
                return FirecrawlScrapeResult(
                    url=url,
                    error=f"firecrawl /v1/scrape HTTP {response.status_code}",
                    took_ms=int((time.monotonic() - start) * 1000),
                )
            data = response.json()
            success = bool(data.get("success"))
            meta = (data.get("metadata") or {})
            content = (data.get("data") or {})
            markdown = str(content.get("markdown", "") or "")
            html = str(content.get("html", "") or "")
            if not success or not (markdown or html):
                return FirecrawlScrapeResult(
                    url=url,
                    error="firecrawl returned no usable content",
                    took_ms=int((time.monotonic() - start) * 1000),
                )
            return FirecrawlScrapeResult(
                url=url,
                title=str(meta.get("title", "") or ""),
                markdown=markdown,
                html=html,
                links=[
                    {"url": str(l.get("url", "")), "title": str(l.get("title", "") or "")}
                    for l in (content.get("links") or [])
                    if l.get("url")
                ],
                took_ms=int((time.monotonic() - start) * 1000),
            )
        except (httpx.HTTPError, httpx.RequestError, ValueError, KeyError) as exc:
            logger.warning("firecrawl scrape failed for %s: %s", url, exc)
            return FirecrawlScrapeResult(
                url=url,
                error=f"{type(exc).__name__}: {exc}",
                took_ms=int((time.monotonic() - start) * 1000),
            )

    # ── parallel batch ───────────────────────────────────────────────────

    async def scrape_batch(self, urls: list[str]) -> list[FirecrawlScrapeResult]:
        """Scrape many URLs in ONE parallel request (firecrawl-side fanout).

        This is the whole point of the tier for sub-agent batches: the
        current per-URL ladder does 10 URLs in ~3 waves of local calls; a
        single ``/v1/batch/scrape`` does them server-side in parallel. Never
        raises; per-URL failures are returned as failed items.
        """
        urls = [u for u in (urls or []) if u]
        if not urls:
            return []
        start = time.monotonic()
        try:
            if not await self.available():
                return [
                    FirecrawlScrapeResult(u, error="firecrawl self-host not reachable")
                    for u in urls
                ]
            client = await self._get_client()
            results: list[FirecrawlScrapeResult] = []
            # Firecrawl caps a batch; chunk past the documented limit.
            for offset in range(0, len(urls), FIRECRAWL_BATCH_LIMIT):
                chunk = urls[offset:offset + FIRECRAWL_BATCH_LIMIT]
                payload = {"urls": chunk, "formats": ["markdown"], "onlyMainContent": True}
                response = await client.post(
                    f"{self._base_url}/v1/batch/scrape",
                    json=payload,
                )
                if response.status_code >= 400:
                    results.extend(
                        FirecrawlScrapeResult(u, error=f"HTTP {response.status_code}")
                        for u in chunk
                    )
                    continue
                data = response.json()
                for item in (data.get("data") or []):
                    item_url = str(item.get("metadata", {}).get("url", ""))
                    content = item.get("data") or {}
                    markdown = str(content.get("markdown", "") or "")
                    html = str(content.get("html", "") or "")
                    results.append(FirecrawlScrapeResult(
                        url=item_url,
                        title=str(item.get("metadata", {}).get("title", "") or ""),
                        markdown=markdown,
                        html=html,
                        error="" if (markdown or html) else "no usable content",
                    ))
                # Failed items may be absent from `data`; mark them explicitly.
                done = {r.url for r in results}
                for u in chunk:
                    if u not in done:
                        results.append(FirecrawlScrapeResult(u, error="no item in batch response"))
            took = int((time.monotonic() - start) * 1000)
            for r in results:
                r.took_ms = took
            return results
        except (httpx.HTTPError, httpx.RequestError, ValueError, KeyError) as exc:
            logger.warning("firecrawl batch scrape failed (%d urls): %s", len(urls), exc)
            return [
                FirecrawlScrapeResult(u, error=f"{type(exc).__name__}: {exc}")
                for u in urls
            ]

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> "FirecrawlClient":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


__all__ = [
    "FIRECRAWL_DEFAULT_URL",
    "FIRECRAWL_BATCH_LIMIT",
    "FirecrawlClient",
    "FirecrawlScrapeResult",
]
