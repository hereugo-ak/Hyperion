"""
HYPERION Google Trends Client — search interest and demand signals.

Google Trends provides:
- Search interest over time (relative 0-100 scale)
- Related queries (rising and top)
- Related topics
- Interest by region (geo distribution)
- No API key required (unofficial pytrends library)

This is NOT a generic "fetch trends" wrapper. It:
- Uses the unofficial pytrends library (Google has no official API)
- ~100 req/hour before throttling — 36s delay between calls
- pytrends is synchronous — all calls wrapped in asyncio.to_thread()
- Returns structured TrendResult data for demand signal analysis
- Provides rising query detection for emerging trend identification
- Caches responses to minimize API calls (critical with rate limits)
- Handles Google throttling gracefully (429 errors)

Architecture reference: §5.1 — "Search interest, related queries,
interest by region. Unofficial pytrends. ~100 req/hour. Used for
demand signals, hype cycle positioning, consumer interest analysis."

Tool selection logic (§5.2):
  Demand/trend signal task:
    1. Google Trends (always — only demand signal source) ← THIS

Used by: Market Analyst (demand signals), Consumer Insights (consumer
interest by geography), Innovation Analyst (hype cycle positioning) (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 15000
CACHE_TTL_SECONDS = 3600  # 1 hour — trend data changes slowly
RATE_LIMIT_DELAY = 36.0  # 36s between calls to avoid Google throttling


@dataclass
class TrendResult:
    """Google Trends interest over time result."""

    keywords: list[str] = field(default_factory=list)
    interest_data: list[dict[str, Any]] = field(default_factory=list)
    timeframe: str = ""
    geography: str = ""  # "" = worldwide, "US" = country, "US-CA" = state

    def to_dict(self) -> dict[str, Any]:
        return {
            "keywords": self.keywords,
            "interest_data": self.interest_data,
            "timeframe": self.timeframe,
            "geography": self.geography,
        }


@dataclass
class RelatedQuery:
    """A related query from Google Trends."""

    query: str = ""
    value: str = ""  # "100" for top, "BREAKOUT" for rising

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "value": self.value,
        }


@dataclass
class RelatedTopic:
    """A related topic from Google Trends."""

    topic: str = ""
    topic_type: str = ""  # e.g., "Technology", "Company"
    value: str = ""  # "100" for top, "BREAKOUT" for rising

    def to_dict(self) -> dict[str, Any]:
        return {
            "topic": self.topic,
            "topic_type": self.topic_type,
            "value": self.value,
        }


class GoogleTrendsClient:
    """Google Trends search interest client.

    Provides search interest over time, related queries, related topics,
    and interest by region. Uses unofficial pytrends library.
    ~100 req/hour before throttling. (§5.1)

    Usage:
        client = GoogleTrendsClient(settings=settings)

        # Interest over time (12 months)
        result = await client.get_interest_over_time(
            keywords=["artificial intelligence", "machine learning"],
            timeframe="today 12-m",
        )
        for point in result.interest_data:
            print(f"{point['date']}: {point}")

        # Related rising queries
        queries = await client.get_related_queries("AI", rising=True)
        for q in queries:
            print(f"{q.query}: {q.value}")

        # Interest by region
        regions = await client.get_interest_by_region("cloud computing")
        for region, value in regions.items():
            print(f"{region}: {value}")
    """

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 10  # Longer delay for Google throttling recovery

    # Timeframe presets
    TIMEFRAME_5_YEARS = "today 5-y"
    TIMEFRAME_12_MONTHS = "today 12-m"
    TIMEFRAME_3_MONTHS = "today 3-m"
    TIMEFRAME_1_MONTH = "today 1-m"
    TIMEFRAME_7_DAYS = "now 7-d"
    TIMEFRAME_1_DAY = "now 1-d"

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._pytrends: Any | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_request_time: float = 0.0

    def _get_pytrends(self) -> Any:
        """Lazily initialize pytrends TrendReq object."""
        if self._pytrends is None:
            try:
                from pytrends.request import TrendReq
            except ImportError:
                logger.error("pytrends not installed. Install with: pip install pytrends")
                raise ImportError("pytrends is required for GoogleTrendsClient. Install with: pip install pytrends>=4.7.3")

            self._pytrends = TrendReq(
                hl="en-US",
                tz=360,  # UTC
                timeout=self.REQUEST_TIMEOUT,
                retries=self.MAX_RETRIES,
                backoff_factor=self.RETRY_DELAY,
            )
        return self._pytrends

    def _cache_key(self, *args: Any) -> str:
        key_str = ":".join(str(a) for a in args)
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached(self, key: str) -> Any | None:
        if key in self._cache:
            timestamp, data = self._cache[key]
            if time.time() - timestamp < CACHE_TTL_SECONDS:
                return data
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = (time.time(), data)

    async def _rate_limit(self) -> None:
        """Enforce 36s delay between calls to avoid Google throttling."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    # ─────────────────────────────────────────────────────────────────────
    # Interest Over Time
    # ─────────────────────────────────────────────────────────────────────

    async def get_interest_over_time(
        self,
        keywords: list[str],
        timeframe: str = "today 12-m",
        geography: str = "",
    ) -> TrendResult:
        """Get search interest over time for keywords.

        Args:
            keywords: List of 1-5 keywords to compare (max 5 per Google limits)
            timeframe: Time range (use TIMEFRAME_* constants or custom)
            geography: Geo filter ("" = worldwide, "US" = country, "US-CA" = state)

        Returns:
            TrendResult with time series interest data (relative 0-100 scale).
        """
        if not keywords:
            return TrendResult()

        # Limit to 5 keywords (Google Trends constraint)
        keywords = keywords[:5]

        cache_key = self._cache_key("iot", *keywords, timeframe, geography)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        def _fetch() -> TrendResult:
            pytrends = self._get_pytrends()
            pytrends.build_payload(
                kw_list=keywords,
                cat=0,  # All categories
                timeframe=timeframe,
                geo=geography,
                gprop="",  # Web search
            )
            df = pytrends.interest_over_time()

            if df is None or df.empty:
                return TrendResult(keywords=keywords, timeframe=timeframe, geography=geography)

            interest_data: list[dict[str, Any]] = []
            for date, row in df.iterrows():
                point: dict[str, Any] = {"date": str(date.date()) if hasattr(date, "date") else str(date)}
                for kw in keywords:
                    if kw in row.index:
                        point[kw] = int(row[kw]) if row[kw] is not None else 0
                # isPartial flag
                if "isPartial" in row.index:
                    point["isPartial"] = bool(row["isPartial"])
                interest_data.append(point)

            return TrendResult(
                keywords=keywords,
                interest_data=interest_data,
                timeframe=timeframe,
                geography=geography,
            )

        try:
            result = await asyncio.to_thread(_fetch)
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            logger.warning("Google Trends interest_over_time failed: %s", e)
            return TrendResult(keywords=keywords, timeframe=timeframe, geography=geography)

    # ─────────────────────────────────────────────────────────────────────
    # Related Queries
    # ─────────────────────────────────────────────────────────────────────

    async def get_related_queries(
        self,
        keyword: str,
        rising: bool = True,
        timeframe: str = "today 12-m",
        geography: str = "",
    ) -> list[RelatedQuery]:
        """Get related queries for a keyword.

        Args:
            keyword: Search keyword
            rising: If True, get rising (breakout) queries. If False, get top queries.
            timeframe: Time range for the analysis
            geography: Geo filter

        Returns:
            List of RelatedQuery objects.
        """
        if not keyword:
            return []

        cache_key = self._cache_key("rq", keyword, rising, timeframe, geography)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        def _fetch() -> list[RelatedQuery]:
            pytrends = self._get_pytrends()
            pytrends.build_payload(
                kw_list=[keyword],
                cat=0,
                timeframe=timeframe,
                geo=geography,
                gprop="",
            )
            df = pytrends.related_queries()

            if df is None:
                return []

            # df is a dict: {keyword: {"top": DataFrame, "rising": DataFrame}}
            keyword_data = df.get(keyword, {})
            section = "rising" if rising else "top"
            section_df = keyword_data.get(section)

            if section_df is None or section_df.empty:
                return []

            results: list[RelatedQuery] = []
            for _, row in section_df.iterrows():
                query = str(row.get("query", ""))
                value = str(row.get("value", ""))
                if query:
                    results.append(RelatedQuery(query=query, value=value))

            return results

        try:
            result = await asyncio.to_thread(_fetch)
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            logger.warning("Google Trends related_queries failed: %s", e)
            return []

    # ─────────────────────────────────────────────────────────────────────
    # Related Topics
    # ─────────────────────────────────────────────────────────────────────

    async def get_related_topics(
        self,
        keyword: str,
        rising: bool = True,
        timeframe: str = "today 12-m",
        geography: str = "",
    ) -> list[RelatedTopic]:
        """Get related topics for a keyword.

        Args:
            keyword: Search keyword
            rising: If True, get rising topics. If False, get top topics.
            timeframe: Time range
            geography: Geo filter

        Returns:
            List of RelatedTopic objects.
        """
        if not keyword:
            return []

        cache_key = self._cache_key("rt", keyword, rising, timeframe, geography)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        def _fetch() -> list[RelatedTopic]:
            pytrends = self._get_pytrends()
            pytrends.build_payload(
                kw_list=[keyword],
                cat=0,
                timeframe=timeframe,
                geo=geography,
                gprop="",
            )
            df = pytrends.related_topics()

            if df is None:
                return []

            keyword_data = df.get(keyword, {})
            section = "rising" if rising else "top"
            section_df = keyword_data.get(section)

            if section_df is None or section_df.empty:
                return []

            results: list[RelatedTopic] = []
            for _, row in section_df.iterrows():
                topic = str(row.get("topic_title", ""))
                topic_type = str(row.get("topic_type", ""))
                value = str(row.get("value", ""))
                if topic:
                    results.append(RelatedTopic(topic=topic, topic_type=topic_type, value=value))

            return results

        try:
            result = await asyncio.to_thread(_fetch)
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            logger.warning("Google Trends related_topics failed: %s", e)
            return []

    # ─────────────────────────────────────────────────────────────────────
    # Interest by Region
    # ─────────────────────────────────────────────────────────────────────

    async def get_interest_by_region(
        self,
        keyword: str,
        resolution: str = "COUNTRY",  # COUNTRY, REGION, DMA, CITY
        timeframe: str = "today 12-m",
        geography: str = "",
    ) -> dict[str, int]:
        """Get search interest by geographic region.

        Args:
            keyword: Search keyword
            resolution: Geographic resolution (COUNTRY, REGION, DMA, CITY)
            timeframe: Time range
            geography: Higher-level geo filter (e.g., "US" for US states)

        Returns:
            Dict mapping region name to interest value (0-100).
        """
        if not keyword:
            return {}

        cache_key = self._cache_key("ibr", keyword, resolution, timeframe, geography)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        def _fetch() -> dict[str, int]:
            pytrends = self._get_pytrends()
            pytrends.build_payload(
                kw_list=[keyword],
                cat=0,
                timeframe=timeframe,
                geo=geography,
                gprop="",
            )
            df = pytrends.interest_by_region(
                resolution=resolution,
                inc_low_vol=True,
                inc_geo_code=False,
            )

            if df is None or df.empty:
                return {}

            results: dict[str, int] = {}
            for region, row in df.iterrows():
                value = int(row.get(keyword, 0)) if row.get(keyword) is not None else 0
                if value > 0:
                    results[str(region)] = value

            # Sort by interest descending
            results = dict(sorted(results.items(), key=lambda x: x[1], reverse=True))

            return results

        try:
            result = await asyncio.to_thread(_fetch)
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            logger.warning("Google Trends interest_by_region failed: %s", e)
            return {}

    # ─────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────

    async def get_trend_summary(
        self,
        keyword: str,
        timeframe: str = "today 12-m",
    ) -> dict[str, Any]:
        """Get a comprehensive trend summary for a keyword.

        Combines interest over time, rising queries, and top regions
        into a single summary dict.

        Args:
            keyword: Search keyword
            timeframe: Time range

        Returns:
            Dict with interest_data, rising_queries, top_regions.
        """
        # Get interest over time
        interest = await self.get_interest_over_time(
            keywords=[keyword],
            timeframe=timeframe,
        )

        # Get rising queries
        rising = await self.get_related_queries(keyword, rising=True, timeframe=timeframe)

        # Get top regions
        regions = await self.get_interest_by_region(keyword, timeframe=timeframe)

        return {
            "keyword": keyword,
            "timeframe": timeframe,
            "interest_data": interest.interest_data[:20],  # Last 20 data points
            "rising_queries": [q.to_dict() for q in rising[:10]],
            "top_regions": dict(list(regions.items())[:10]),
        }

    async def close(self) -> None:
        """Cleanup — pytrends doesn't hold persistent connections."""
        self._pytrends = None

    async def __aenter__(self) -> GoogleTrendsClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
