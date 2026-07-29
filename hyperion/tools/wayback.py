"""
HYPERION Wayback Machine Client — historical page snapshots.

The Wayback Machine is the ONLY source for historical web page snapshots.
It is used for tracking changes over time — pricing evolution, regulatory
changes, tech hype cycles, competitor strategy shifts.

This is NOT a generic "fetch an archived page" wrapper. It:
- Uses the Wayback Machine Availability API to find snapshots
- Uses the CDX Server API for time-range queries
- Fetches archived page content via the web archive
- Supports "closest snapshot" queries (find snapshot nearest to a date)
- Supports "all snapshots" queries (get timeline of changes)
- Returns structured results with snapshot dates and content

Architecture reference: §5.1 — "Historical page snapshots via Wayback
Machine. Used for tracking changes over time — pricing evolution,
regulatory changes, tech hype cycles."

Tool selection logic (§5.2):
  Historical task:
    1. Wayback Machine (always — it's the only source for historical
       snapshots) ← THIS

Extraction fallback chain (§5.3):
  Obscura (stealth, JS rendering)
    → Crawl4AI (heavy extraction, PDFs)
      → Jina Reader (fast, simple extraction)
        → Wayback (if the page is down or changed) ← THIS (last resort)

Used by: Regulatory, Innovation, Competitive Intel (§5.1)
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import quote_plus

import httpx

logger = logging.getLogger(__name__)


@dataclass
class WaybackSnapshot:
    """A single Wayback Machine snapshot."""

    url: str
    snapshot_url: str = ""
    timestamp: str = ""
    status_code: int = 0
    content_length: int = 0
    mime_type: str = ""

    @property
    def snapshot_date(self) -> datetime | None:
        """Parse the snapshot timestamp into a datetime."""
        if not self.timestamp:
            return None
        try:
            return datetime.strptime(self.timestamp, "%Y%m%d%H%M%S")
        except ValueError:
            return None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "snapshot_url": self.snapshot_url,
            "timestamp": self.timestamp,
            "snapshot_date": self.snapshot_date.isoformat() if self.snapshot_date else "",
            "status_code": self.status_code,
            "content_length": self.content_length,
            "mime_type": self.mime_type,
        }


@dataclass
class WaybackAvailabilityResult:
    """Result of a Wayback Machine availability check."""

    url: str
    available: bool = False
    closest_snapshot: WaybackSnapshot | None = None
    closest_timestamp: str = ""
    closest_status: int = 0
    cached: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "available": self.available,
            "closest_snapshot": self.closest_snapshot.to_dict() if self.closest_snapshot else None,
            "closest_timestamp": self.closest_timestamp,
            "closest_status": self.closest_status,
            "cached": self.cached,
        }


@dataclass
class WaybackTimelineResult:
    """Result of a Wayback Machine CDX timeline query."""

    url: str
    snapshots: list[WaybackSnapshot] = field(default_factory=list)
    total: int = 0
    first_snapshot: str = ""
    last_snapshot: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "snapshots": [s.to_dict() for s in self.snapshots],
            "total": self.total,
            "first_snapshot": self.first_snapshot,
            "last_snapshot": self.last_snapshot,
            "error": self.error,
        }


@dataclass
class WaybackContentResult:
    """Result of fetching content from a Wayback Machine snapshot."""

    url: str
    snapshot_url: str
    content: str = ""
    title: str = ""
    status_code: int = 0
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "snapshot_url": self.snapshot_url,
            "content": self.content,
            "title": self.title,
            "status_code": self.status_code,
            "error": self.error,
        }


class WaybackClient:
    """Wayback Machine archive client.

    Provides access to historical web page snapshots via the Internet
    Archive's Wayback Machine. Used for tracking changes over time.
    (§5.1)

    Usage:
        client = WaybackClient(settings=settings)

        # Check if a URL has been archived
        availability = await client.check_availability("https://competitor.com/pricing")
        if availability.available:
            print(f"Closest snapshot: {availability.closest_timestamp}")

        # Get timeline of all snapshots
        timeline = await client.get_timeline("https://competitor.com/pricing")
        for snapshot in timeline.snapshots:
            print(f"{snapshot.timestamp} — {snapshot.status_code}")

        # Fetch content from closest snapshot
        content = await client.fetch_snapshot(
            "https://competitor.com/pricing",
            timestamp="20240101",
        )
        print(content.content[:500])
    """

    AVAILABILITY_API = "https://archive.org/wayback/available"
    CDX_API = "https://web.archive.org/cdx/search/cdx"
    WEB_ARCHIVE_BASE = "https://web.archive.org/web"
    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 2

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                follow_redirects=True,
            )
        return self._client

    async def check_availability(
        self,
        url: str,
        timestamp: str = "",
    ) -> WaybackAvailabilityResult:
        """Check if a URL has been archived by the Wayback Machine.

        Uses the Availability API to find the closest snapshot to the
        given timestamp.

        Args:
            url: The URL to check
            timestamp: Target timestamp (YYYYMMDDhhmmss format). If empty,
                      returns the closest available snapshot.

        Returns:
            WaybackAvailabilityResult with availability info and closest snapshot.
        """
        client = await self._get_client()

        params = {"url": url}
        if timestamp:
            params["timestamp"] = timestamp

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(self.AVAILABILITY_API, params=params)
                response.raise_for_status()
                data = response.json()

                closest = data.get("archived_snapshots", {}).get("closest", {})
                if closest and closest.get("available", False):
                    snapshot = WaybackSnapshot(
                        url=url,
                        snapshot_url=closest.get("url", ""),
                        timestamp=closest.get("timestamp", ""),
                        status_code=closest.get("status", 0),
                    )
                    return WaybackAvailabilityResult(
                        url=url,
                        available=True,
                        closest_snapshot=snapshot,
                        closest_timestamp=snapshot.timestamp,
                        closest_status=snapshot.status_code,
                    )

                return WaybackAvailabilityResult(url=url, available=False)

            except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError) as e:
                # D5.1: ruff F841 (`as e` never read) exposed the same broken
                # retry loop as unsplash.py — an unconditional `return` after the
                # sleep, so the function gave up on the FIRST failure while
                # appearing to retry twice. Wayback is the citation-rot fallback:
                # when a cited URL 404s, this is what recovers it, so a
                # first-transient-failure surrender means avoidable dead
                # citations in the report's endnotes.
                logger.debug(
                    "Wayback availability attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    self.MAX_RETRIES,
                    url,
                    e,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue  # actually retry
                logger.warning(
                    "Wayback availability exhausted all %d attempts for %s. Last error: %s",
                    self.MAX_RETRIES,
                    url,
                    e,
                )
                return WaybackAvailabilityResult(url=url, available=False)

        return WaybackAvailabilityResult(url=url, available=False)

    async def get_timeline(
        self,
        url: str,
        from_date: str = "",
        to_date: str = "",
        limit: int = 100,
        filter_status: list[int] | None = None,
    ) -> WaybackTimelineResult:
        """Get a timeline of all snapshots for a URL via the CDX API.

        Args:
            url: The URL to get snapshots for
            from_date: Start date (YYYYMMDD format). Empty = earliest.
            to_date: End date (YYYYMMDD format). Empty = latest.
            limit: Maximum number of snapshots to return
            filter_status: Only include snapshots with these status codes

        Returns:
            WaybackTimelineResult with all snapshots in chronological order.
        """
        client = await self._get_client()

        params: dict[str, str] = {
            "url": url,
            "output": "json",
            "fl": "timestamp:statuscode:length:mimetype",
            "limit": str(limit),
            "collapse": "timestamp:6",  # Collapse by month for overview
        }
        if from_date:
            params["from"] = from_date
        if to_date:
            params["to"] = to_date

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(self.CDX_API, params=params)
                response.raise_for_status()
                data = response.json()

                if not data or len(data) < 2:
                    return WaybackTimelineResult(url=url, snapshots=[], total=0)

                # First row is headers, rest are data
                headers = data[0]
                snapshots: list[WaybackSnapshot] = []

                for row in data[1:]:
                    row_dict = dict(zip(headers, row))
                    status_code = int(row_dict.get("statuscode", 0))

                    if filter_status and status_code not in filter_status:
                        continue

                    snapshot = WaybackSnapshot(
                        url=url,
                        snapshot_url=f"{self.WEB_ARCHIVE_BASE}/{row_dict.get('timestamp', '')}/{url}",
                        timestamp=row_dict.get("timestamp", ""),
                        status_code=status_code,
                        content_length=int(row_dict.get("length", 0) or 0),
                        mime_type=row_dict.get("mimetype", ""),
                    )
                    snapshots.append(snapshot)

                snapshots.sort(key=lambda s: s.timestamp)

                return WaybackTimelineResult(
                    url=url,
                    snapshots=snapshots,
                    total=len(snapshots),
                    first_snapshot=snapshots[0].timestamp if snapshots else "",
                    last_snapshot=snapshots[-1].timestamp if snapshots else "",
                )

            except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError, IndexError) as e:
                # D5.1: same dead-retry defect (the `return` below the sleep was
                # unconditional). Unlike the two sibling legs this one at least
                # propagated `str(e)` into the result, so the cause was not lost
                # — only the retry was.
                logger.debug(
                    "Wayback timeline attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    self.MAX_RETRIES,
                    url,
                    e,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue  # actually retry
                return WaybackTimelineResult(url=url, error=str(e))

        return WaybackTimelineResult(url=url, error="All retries exhausted")

    async def fetch_snapshot(
        self,
        url: str,
        timestamp: str = "",
    ) -> WaybackContentResult:
        """Fetch the content of a Wayback Machine snapshot.

        Args:
            url: The original URL
            timestamp: Target timestamp (YYYYMMDDhhmmss). If empty, uses
                      the closest available snapshot.

        Returns:
            WaybackContentResult with the archived page content.
        """
        # If no timestamp, find the closest snapshot
        if not timestamp:
            availability = await self.check_availability(url)
            if not availability.available or not availability.closest_snapshot:
                return WaybackContentResult(
                    url=url,
                    snapshot_url="",
                    error="No snapshot available for this URL.",
                )
            timestamp = availability.closest_timestamp

        # Build the Wayback Machine URL
        snapshot_url = f"{self.WEB_ARCHIVE_BASE}/{timestamp}/{url}"

        client = await self._get_client()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(snapshot_url)
                response.raise_for_status()

                content = response.text
                title = ""

                # Extract title from HTML
                if "<title>" in content:
                    start = content.find("<title>") + 7
                    end = content.find("</title>", start)
                    title = content[start:end].strip()

                return WaybackContentResult(
                    url=url,
                    snapshot_url=snapshot_url,
                    content=content,
                    title=title,
                    status_code=response.status_code,
                )

            except (httpx.HTTPError, httpx.RequestError) as e:
                # D5.1: third instance of the dead-retry defect in this module.
                # This one is the actual content fetch, so surrendering on the
                # first transient error is the difference between recovering a
                # rotted citation's text and reporting the source as unavailable.
                logger.debug(
                    "Wayback snapshot fetch attempt %d/%d failed for %s: %s",
                    attempt + 1,
                    self.MAX_RETRIES,
                    snapshot_url,
                    e,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                    continue  # actually retry
                return WaybackContentResult(
                    url=url,
                    snapshot_url=snapshot_url,
                    error=str(e),
                )

        return WaybackContentResult(
            url=url,
            snapshot_url=snapshot_url,
            error="All retries exhausted",
        )

    async def compare_snapshots(
        self,
        url: str,
        timestamp1: str,
        timestamp2: str,
    ) -> dict[str, Any]:
        """Compare two snapshots of the same URL to detect changes.

        Useful for tracking pricing changes, regulatory updates, or
        competitor strategy shifts over time.

        Args:
            url: The URL to compare
            timestamp1: First snapshot timestamp (YYYYMMDDhhmmss)
            timestamp2: Second snapshot timestamp (YYYYMMDDhhmmss)

        Returns:
            Dict with both snapshots' content and basic change indicators.
        """
        result1 = await self.fetch_snapshot(url, timestamp1)
        result2 = await self.fetch_snapshot(url, timestamp2)

        return {
            "url": url,
            "snapshot1": {
                "timestamp": timestamp1,
                "title": result1.title,
                "content_length": len(result1.content),
                "status_code": result1.status_code,
            },
            "snapshot2": {
                "timestamp": timestamp2,
                "title": result2.title,
                "content_length": len(result2.content),
                "status_code": result2.status_code,
            },
            "title_changed": result1.title != result2.title,
            "content_length_changed": len(result1.content) != len(result2.content),
            "content_length_diff": len(result2.content) - len(result1.content),
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> WaybackClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
