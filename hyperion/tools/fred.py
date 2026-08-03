"""
HYPERION FRED Client — Federal Reserve Economic Data.

FRED is the ONLY macroeconomic data source in HYPERION. It provides:
- GDP (nominal and real, quarterly and annual)
- Inflation (CPI, PCE, core CPI)
- Interest rates (federal funds, treasury yields)
- Unemployment rates
- Sector spending
- Consumer sentiment
- Housing data
- Industrial production

This is NOT a generic "fetch economic data" wrapper. It:
- Uses the FRED REST API with proper series ID handling
- Free, unlimited API access (no rate limit concerns)
- Returns structured time series data for macroeconomic context
- Supports date range filtering for historical analysis
- Supports frequency conversion (annual, quarterly, monthly, weekly, daily)
- Used for DCF discount rates (treasury yields as risk-free rate)
- Used for market sizing (GDP for TAM, sector spending for SAM)

Architecture reference: §5.1 — "Federal Reserve economic data. GDP,
inflation, interest rates, sector spending. Free, unlimited. Used for
macroeconomic context in market sizing and DCF discount rates."

Tool selection logic (§5.2):
  Macro data task:
    1. FRED (always — it's the only macroeconomic data source) ← THIS

Used by: Market Analyst, Financial Analyst, Sustainability (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class FREDSeries:
    """A FRED data series (e.g., GDP, CPI, FEDFUNDS)."""

    series_id: str
    title: str = ""
    units: str = ""
    frequency: str = ""
    seasonal_adjustment: str = ""
    observation_start: str = ""
    observation_end: str = ""
    data_points: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "series_id": self.series_id,
            "title": self.title,
            "units": self.units,
            "frequency": self.frequency,
            "seasonal_adjustment": self.seasonal_adjustment,
            "observation_start": self.observation_start,
            "observation_end": self.observation_end,
            "data_points": self.data_points,
        }


@dataclass
class FREDSearchResult:
    """Result of searching for FRED series."""

    query: str
    series: list[dict[str, str]] = field(default_factory=list)
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "series": self.series,
            "total": self.total,
        }


class FredClient:
    """FRED economic data client.

    Provides access to Federal Reserve economic data including GDP,
    inflation, interest rates, and sector spending. Free, unlimited.
    (§5.1)

    Usage:
        client = FredClient(settings=settings)

        # Get GDP data
        gdp = await client.get_series("GDP", observation_start="2020-01-01")
        for point in gdp.data_points:
            print(f"{point['date']}: {point['value']} {gdp.units}")

        # Search for series
        results = await client.search_series("inflation rate india")
        for series in results.series:
            print(f"{series['id']}: {series['title']}")
    """

    BASE_URL = "https://api.stlouisfed.org/fred"
    CACHE_DIR = "output/.fred_cache"
    CACHE_TTL_SECONDS = 7200  # 2 hours — economic data doesn't change frequently
    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 2

    # Common series IDs for quick access
    GDP = "GDP"                    # Gross Domestic Product
    GDPC1 = "GDPC1"               # Real GDP
    CPI = "CPIAUCSL"              # Consumer Price Index (All Urban)
    CORE_CPI = "CPILFESL"         # Core CPI (less food and energy)
    FEDFUNDS = "FEDFUNDS"         # Federal Funds Rate
    DGS10 = "DGS10"               # 10-Year Treasury Yield
    DGS30 = "DGS30"               # 30-Year Treasury Yield
    UNRATE = "UNRATE"             # Unemployment Rate
    UMCSENT = "UMCSENT"           # Consumer Sentiment
    INDPRO = "INDPRO"             # Industrial Production Index
    HOUST = "HOUST"               # Housing Starts
    M2 = "M2SL"                   # M2 Money Supply
    VIX = "VIXCLS"                # VIX Volatility Index

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._api_key = ""
        if settings:
            self._api_key = getattr(settings, "fred_api_key", "")
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
            )
        return self._client

    def _cache_key(self, *args: Any) -> str:
        key_str = ":".join(str(a) for a in args)
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached(self, key: str) -> Any | None:
        if key in self._cache:
            timestamp, data = self._cache[key]
            if time.time() - timestamp < self.CACHE_TTL_SECONDS:
                return data
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = (time.time(), data)

    async def _make_request(self, endpoint: str, params: dict[str, str]) -> dict[str, Any]:
        """Make a cached request to the FRED API."""
        cache_key = self._cache_key(endpoint, *sorted(params.items()))
        cached = self._get_cached(cache_key)
        if isinstance(cached, dict):
            return {str(key): value for key, value in cached.items()}

        params["api_key"] = self._api_key
        params["file_type"] = "json"

        client = await self._get_client()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(f"{self.BASE_URL}/{endpoint}", params=params)
                response.raise_for_status()
                payload = response.json()
                if not isinstance(payload, dict):
                    return {"error": "FRED returned a non-object response"}
                data: dict[str, Any] = {
                    str(key): value for key, value in payload.items()
                }

                self._set_cached(cache_key, data)
                return data

            except (httpx.HTTPError, httpx.RequestError, ValueError) as e:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    async def get_series(
        self,
        series_id: str,
        observation_start: str = "",
        observation_end: str = "",
        frequency: str = "",
        units: str = "",
    ) -> FREDSeries:
        """Get observations for a FRED series.

        Args:
            series_id: FRED series ID (e.g., "GDP", "CPIAUCSL", "FEDFUNDS")
            observation_start: Start date (YYYY-MM-DD). Empty = earliest.
            observation_end: End date (YYYY-MM-DD). Empty = latest.
            frequency: Frequency conversion (d, w, m, q, a). Empty = native.
            units: Units transformation (e.g., "pc1" for percent change).
                   Empty = native units.

        Returns:
            FREDSeries with metadata and data points.
        """
        params: dict[str, str] = {"series_id": series_id}
        if observation_start:
            params["observation_start"] = observation_start
        if observation_end:
            params["observation_end"] = observation_end
        if frequency:
            params["frequency"] = frequency
        if units:
            params["units"] = units

        # Get series info
        info_data = await self._make_request("series", {"series_id": series_id})
        series_info = info_data.get("seriess", [{}])[0] if info_data.get("seriess") else {}

        # Get observations
        obs_data = await self._make_request("series/observations", params)
        observations = obs_data.get("observations", [])

        data_points: list[dict[str, Any]] = []
        for obs in observations:
            value_str = obs.get("value", ".")
            try:
                value = float(value_str) if value_str != "." else None
            except (ValueError, TypeError):
                value = None

            data_points.append({
                "date": obs.get("date", ""),
                "value": value,
                "realtime_start": obs.get("realtime_start", ""),
                "realtime_end": obs.get("realtime_end", ""),
            })

        return FREDSeries(
            series_id=series_id,
            title=series_info.get("title", ""),
            units=series_info.get("units", ""),
            frequency=series_info.get("frequency", ""),
            seasonal_adjustment=series_info.get("seasonal_adjustment", ""),
            observation_start=series_info.get("observation_start", ""),
            observation_end=series_info.get("observation_end", ""),
            data_points=data_points,
        )

    async def search_series(
        self,
        search_text: str,
        limit: int = 20,
    ) -> FREDSearchResult:
        """Search for FRED series by text.

        Args:
            search_text: Search query (e.g., "GDP india", "inflation rate")
            limit: Maximum number of results

        Returns:
            FREDSearchResult with matching series.
        """
        data = await self._make_request("series/search", {
            "search_text": search_text,
            "limit": str(limit),
            "order_by": "popularity",
            "sort_order": "desc",
        })

        series_list: list[dict[str, str]] = []
        for s in data.get("seriess", []):
            series_list.append({
                "id": s.get("id", ""),
                "title": s.get("title", ""),
                "frequency": s.get("frequency", ""),
                "units": s.get("units", ""),
                "seasonal_adjustment": s.get("seasonal_adjustment", ""),
                "popularity": str(s.get("popularity", 0)),
            })

        return FREDSearchResult(
            query=search_text,
            series=series_list,
            total=data.get("count", len(series_list)),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Convenience methods for common economic indicators
    # ─────────────────────────────────────────────────────────────────────

    async def get_gdp(self, observation_start: str = "") -> FREDSeries:
        """Get nominal GDP data. Used for market sizing (TAM)."""
        return await self.get_series(self.GDP, observation_start=observation_start)

    async def get_real_gdp(self, observation_start: str = "") -> FREDSeries:
        """Get real (inflation-adjusted) GDP data."""
        return await self.get_series(self.GDPC1, observation_start=observation_start)

    async def get_inflation(self, observation_start: str = "") -> FREDSeries:
        """Get CPI (inflation) data."""
        return await self.get_series(self.CPI, observation_start=observation_start)

    async def get_federal_funds_rate(self, observation_start: str = "") -> FREDSeries:
        """Get federal funds rate. Used for DCF discount rates."""
        return await self.get_series(self.FEDFUNDS, observation_start=observation_start)

    async def get_treasury_yield(self, years: int = 10, observation_start: str = "") -> FREDSeries:
        """Get treasury yield. Used as risk-free rate in DCF.

        Args:
            years: 10 or 30 year treasury yield
            observation_start: Start date
        """
        series_id = self.DGS10 if years == 10 else self.DGS30
        return await self.get_series(series_id, observation_start=observation_start)

    async def get_unemployment(self, observation_start: str = "") -> FREDSeries:
        """Get unemployment rate."""
        return await self.get_series(self.UNRATE, observation_start=observation_start)

    async def get_consumer_sentiment(self, observation_start: str = "") -> FREDSeries:
        """Get consumer sentiment index. Used for demand forecasting."""
        return await self.get_series(self.UMCSENT, observation_start=observation_start)

    async def get_industrial_production(self, observation_start: str = "") -> FREDSeries:
        """Get industrial production index."""
        return await self.get_series(self.INDPRO, observation_start=observation_start)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> FredClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
