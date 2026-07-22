"""
HYPERION World Bank Client — international development indicators.

The World Bank API provides:
- GDP (nominal and per capita) for 200+ countries
- Inflation rates, trade as % of GDP
- Population, health spending, education spending
- Unemployment, FDI, CO2 emissions
- Renewable energy consumption
- Country profiles with income group, region, capital
- Time series data from 1960 to present
- No API key required — fully open data

This is NOT a generic "fetch economic data" wrapper. It:
- Uses the World Bank Indicators API (api.worldbank.org/v2)
- No rate limit (100ms delay recommended)
- Returns structured time series for international market sizing
- Supports country comparison for competitive analysis
- Provides ESG indicators for sustainability assessments
- Caches responses to minimize API calls
- Handles missing data points gracefully (not all countries report all years)

Architecture reference: §5.1 — "International development indicators.
GDP, inflation, trade, population, health, education, CO2. Free,
unlimited. Used for international market sizing and country risk
assessment."

Tool selection logic (§5.2):
  International macro data task:
    1. FRED (US-specific macro data)
    2. World Bank (international macro data) ← THIS

Used by: Market Analyst (international market sizing), Financial Analyst
(country risk premiums, inflation for DCF), Sustainability Analyst
(ESG indicators) (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 15000
CACHE_TTL_SECONDS = 3600  # 1 hour — World Bank data updates infrequently


@dataclass
class WorldBankIndicator:
    """A single indicator data point from the World Bank."""

    indicator_code: str
    indicator_name: str = ""
    country_code: str = ""
    country_name: str = ""
    value: float | None = None
    year: int = 0
    unit: str = ""
    decimal_places: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "indicator_code": self.indicator_code,
            "indicator_name": self.indicator_name,
            "country_code": self.country_code,
            "country_name": self.country_name,
            "value": self.value,
            "year": self.year,
            "unit": self.unit,
            "decimal_places": self.decimal_places,
        }


@dataclass
class WorldBankIndicatorData:
    """A time series of indicator data for a country."""

    indicator_code: str
    indicator_name: str = ""
    country_code: str = ""
    country_name: str = ""
    data_points: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "indicator_code": self.indicator_code,
            "indicator_name": self.indicator_name,
            "country_code": self.country_code,
            "country_name": self.country_name,
            "data_points": self.data_points,
        }


@dataclass
class WorldBankCountryProfile:
    """A country profile from the World Bank."""

    country_code: str
    name: str = ""
    region: str = ""
    income_group: str = ""
    capital_city: str = ""
    latitude: float | None = None
    longitude: float | None = None
    iso2: str = ""
    iso3: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "name": self.name,
            "region": self.region,
            "income_group": self.income_group,
            "capital_city": self.capital_city,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "iso2": self.iso2,
            "iso3": self.iso3,
        }


class WorldBankClient:
    """World Bank development indicators client.

    Provides access to GDP, inflation, trade, population, health,
    education, CO2, and other indicators for 200+ countries. Free,
    unlimited. (§5.1)

    Usage:
        client = WorldBankClient(settings=settings)

        # Get GDP for India
        gdp = await client.get_indicator("NY.GDP.MKTP.CD", country="IN")
        for point in gdp.data_points:
            print(f"{point['year']}: ${point['value']}")

        # Compare countries
        comparison = await client.compare_countries(
            "NY.GDP.PCAP.CD", ["US", "CN", "IN"], year=2023
        )
        for country, value in comparison.items():
            print(f"{country}: ${value}")

        # Get country profile
        profile = await client.get_country_profile("IN")
        print(f"{profile.name} — {profile.income_group}")

        # Search for indicators
        indicators = await client.search_indicators("renewable energy")
    """

    BASE_URL = "https://api.worldbank.org/v2"

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 2
    RATE_LIMIT_DELAY = 0.1  # 100ms = 10 req/sec recommended

    # Pre-defined indicator codes for common queries
    INDICATORS: dict[str, str] = {
        "gdp": "NY.GDP.MKTP.CD",
        "gdp_per_capita": "NY.GDP.PCAP.CD",
        "gdp_growth": "NY.GDP.MKTP.KD.ZG",
        "inflation": "FP.CPI.TOTL.ZG",
        "trade_pct_gdp": "NE.TRD.GNFS.ZS",
        "population": "SP.POP.TOTL",
        "population_growth": "SP.POP.GROW",
        "health_spending": "SH.XPD.CHEX.GD.ZS",
        "education_spending": "SE.XPD.TOTL.GD.ZS",
        "unemployment": "SL.UEM.TOTL.ZS",
        "fdi": "BX.KLT.DINV.WD.GD.ZS",
        "co2_emissions": "EN.ATM.CO2E.KT",
        "co2_per_capita": "EN.ATM.CO2E.PC",
        "renewable_energy": "EG.FEC.RNEW.ZS",
        "electricity_access": "EG.ELC.ACCS.ZS",
        "internet_users": "IT.NET.USER.ZS",
        "mobile_subscriptions": "IT.CEL.SETS.P2",
        "life_expectancy": "SP.DYN.LE00.IN",
        "literacy_rate": "SE.ADT.LITR.ZS",
        "tax_revenue": "GC.TAX.TOTL.GD.ZS",
        "debt_to_gdp": "GC.DOD.TOTL.GD.ZS",
        "exports": "NE.EXP.GNFS.CD",
        "imports": "NE.IMP.GNFS.CD",
    }

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_request_time: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip, deflate",
                },
            )
        return self._client

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
        """Enforce 10 req/sec rate limit (100ms between calls)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < self.RATE_LIMIT_DELAY:
            await asyncio.sleep(self.RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    async def _make_request(
        self,
        endpoint: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make a rate-limited, cached request to the World Bank API."""
        cache_key = self._cache_key(endpoint, *sorted((params or {}).items()))
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()
        client = await self._get_client()

        # World Bank API requires format=json
        params = params or {}
        params["format"] = "json"

        url = f"{self.BASE_URL}/{endpoint}"

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

                self._set_cached(cache_key, data)
                return data

            except (httpx.HTTPError, httpx.RequestError, ValueError) as e:
                logger.warning("World Bank request failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    # ─────────────────────────────────────────────────────────────────────
    # Indicators
    # ─────────────────────────────────────────────────────────────────────

    async def get_indicator(
        self,
        indicator_code: str,
        country: str = "all",
        date_range: str = "",
        frequency: str = "",
    ) -> WorldBankIndicatorData:
        """Get time series data for a specific indicator.

        Args:
            indicator_code: World Bank indicator code (e.g., "NY.GDP.MKTP.CD")
                           or a shorthand key like "gdp", "inflation", etc.
            country: Country code (ISO2 like "US", "CN", "IN") or "all"
            date_range: Date range (e.g., "2020:2024", "2020:", ":2024")
            frequency: Data frequency ("Y" annual, "M" monthly, "Q" quarterly)

        Returns:
            WorldBankIndicatorData with time series data points.
        """
        # Resolve shorthand indicator codes
        code = self.INDICATORS.get(indicator_code, indicator_code)

        endpoint = f"country/{country}/indicator/{code}"
        params: dict[str, str] = {"per_page": "1000"}
        if date_range:
            params["date"] = date_range
        if frequency:
            params["frequency"] = frequency

        data = await self._make_request(endpoint, params=params)

        if "error" in data or not isinstance(data, list) or len(data) < 2:
            return WorldBankIndicatorData(
                indicator_code=code,
                country_code=country,
            )

        # World Bank returns [metadata, data_points] for list format
        metadata = data[0] if len(data) > 0 else {}
        raw_points = data[1] if len(data) > 1 else []

        if not isinstance(raw_points, list):
            raw_points = []

        data_points: list[dict[str, Any]] = []
        indicator_name = ""
        country_name = ""

        for point in raw_points:
            value = point.get("value")
            if value is not None:
                try:
                    value = float(value)
                except (ValueError, TypeError):
                    value = None

            indicator_info = point.get("indicator", {})
            indicator_name = indicator_info.get("value", indicator_name)
            country_info = point.get("country", {})
            country_name = country_info.get("value", country_name)

            data_points.append({
                "year": int(point.get("date", 0) or 0),
                "value": value,
                "country": country_info.get("value", ""),
                "indicator": indicator_info.get("value", ""),
            })

        # Sort by year ascending
        data_points.sort(key=lambda x: x["year"])

        return WorldBankIndicatorData(
            indicator_code=code,
            indicator_name=indicator_name,
            country_code=country,
            country_name=country_name,
            data_points=data_points,
        )

    async def search_indicators(
        self,
        query: str,
        limit: int = 20,
    ) -> list[dict[str, str]]:
        """Search for World Bank indicators by keyword.

        Args:
            query: Search query (e.g., "renewable energy", "GDP per capita")
            limit: Maximum number of results

        Returns:
            List of dicts with indicator code, name, and source.
        """
        if not query or not query.strip():
            return []

        params: dict[str, str] = {"per_page": str(min(limit, 1000))}

        data = await self._make_request("indicator", params=params)

        if "error" in data or not isinstance(data, list) or len(data) < 2:
            return []

        raw_indicators = data[1] if len(data) > 1 else []
        if not isinstance(raw_indicators, list):
            return []

        results: list[dict[str, str]] = []
        query_lower = query.lower()

        for ind in raw_indicators:
            name = ind.get("name", "")
            if query_lower in name.lower():
                results.append({
                    "code": ind.get("id", ""),
                    "name": name,
                    "source": ind.get("sourceNote", "")[:200],
                })
                if len(results) >= limit:
                    break

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Country Profiles
    # ─────────────────────────────────────────────────────────────────────

    async def get_country_profile(self, country_code: str) -> WorldBankCountryProfile | None:
        """Get a country profile from the World Bank.

        Args:
            country_code: ISO2 country code (e.g., "US", "CN", "IN")

        Returns:
            WorldBankCountryProfile object, or None if not found.
        """
        if not country_code:
            return None

        data = await self._make_request(f"country/{country_code}")

        if "error" in data or not isinstance(data, list) or len(data) < 2:
            return None

        raw_countries = data[1] if len(data) > 1 else []
        if not isinstance(raw_countries, list) or not raw_countries:
            return None

        country = raw_countries[0]

        region = country.get("region", {})
        income_level = country.get("incomeLevel", {})

        return WorldBankCountryProfile(
            country_code=country.get("id", ""),
            name=country.get("name", ""),
            region=region.get("value", "") if isinstance(region, dict) else "",
            income_group=income_level.get("value", "") if isinstance(income_level, dict) else "",
            capital_city=country.get("capitalCity", "") or "",
            latitude=float(country["latitude"]) if country.get("latitude") else None,
            longitude=float(country["longitude"]) if country.get("longitude") else None,
            iso2=country.get("iso2", "") or "",
            iso3=country.get("iso3", "") or "",
        )

    # ─────────────────────────────────────────────────────────────────────
    # Country Comparison
    # ─────────────────────────────────────────────────────────────────────

    async def compare_countries(
        self,
        indicator_code: str,
        countries: list[str],
        year: int = 0,
    ) -> dict[str, float | None]:
        """Compare an indicator across multiple countries.

        Args:
            indicator_code: World Bank indicator code or shorthand
            countries: List of ISO2 country codes (e.g., ["US", "CN", "IN"])
            year: Specific year to compare. 0 = latest available year.

        Returns:
            Dict mapping country code to indicator value.
        """
        if not countries:
            return {}

        results: dict[str, float | None] = {}

        for country in countries:
            if year > 0:
                date_range = str(year)
            else:
                date_range = "2000:"  # Get recent data

            indicator_data = await self.get_indicator(
                indicator_code,
                country=country,
                date_range=date_range,
            )

            if year > 0:
                # Find the specific year
                for point in indicator_data.data_points:
                    if point["year"] == year:
                        results[country] = point["value"]
                        break
                else:
                    results[country] = None
            else:
                # Get the latest available value
                if indicator_data.data_points:
                    latest = indicator_data.data_points[-1]
                    results[country] = latest["value"]
                else:
                    results[country] = None

        return results

    # ─────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────

    async def get_gdp(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get nominal GDP data. Used for international market sizing (TAM)."""
        return await self.get_indicator(self.INDICATORS["gdp"], country=country, date_range=date_range)

    async def get_gdp_per_capita(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get GDP per capita. Used for purchasing power analysis."""
        return await self.get_indicator(self.INDICATORS["gdp_per_capita"], country=country, date_range=date_range)

    async def get_inflation(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get inflation rate. Used for country risk premiums in DCF."""
        return await self.get_indicator(self.INDICATORS["inflation"], country=country, date_range=date_range)

    async def get_population(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get population data. Used for market sizing."""
        return await self.get_indicator(self.INDICATORS["population"], country=country, date_range=date_range)

    async def get_co2_emissions(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get CO2 emissions. Used for ESG/sustainability assessment."""
        return await self.get_indicator(self.INDICATORS["co2_emissions"], country=country, date_range=date_range)

    async def get_renewable_energy(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get renewable energy consumption %. Used for sustainability analysis."""
        return await self.get_indicator(self.INDICATORS["renewable_energy"], country=country, date_range=date_range)

    async def get_fdi(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get Foreign Direct Investment as % of GDP. Used for investment climate."""
        return await self.get_indicator(self.INDICATORS["fdi"], country=country, date_range=date_range)

    async def get_internet_users(self, country: str = "all", date_range: str = "") -> WorldBankIndicatorData:
        """Get internet users %. Used for digital market sizing."""
        return await self.get_indicator(self.INDICATORS["internet_users"], country=country, date_range=date_range)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> WorldBankClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
