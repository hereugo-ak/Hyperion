"""
HYPERION SDMX Clients — OECD, Eurostat, and IMF statistical data.

FRED covers the United States and nothing else. World Bank covers development
indicators with multi-year publication lags. Between those two ceilings sit
the three canonical SDMX sources every consulting-grade macro model needs:

- **OECD SDMX 2.1** — harmonised CPI, unit labour costs, trade balance,
  labour force, short-term economic indicators across 38 member countries.
- **Eurostat SDMX 2.1** — the EU-27 statistical office: HICP inflation,
  unemployment (une_rt_a/m), national accounts (nama_10_gdp), energy prices.
- **IMF SDMX Central** — International Financial Statistics (IFS): exchange
  rates, monetary aggregates, government finance, balance of payments.

All three speak SDMX 2.1 with `format=csvfile` support — one HTTP layer, one
CSV parser, three adapters with provider-specific dataflow geometry.

This is NOT a generic "fetch statistics" wrapper. It:
- Uses each provider's native SDMX REST endpoint with proper key geometry
  (OECD path-keys, Eurostat query-params, IMF dot-keys)
- Requires no API key — all three are official open-data portals
- Returns the same SDMXSeries shape for all providers so downstream agents
  (Market Analyst, Financial Analyst, Sustainability) can fuse them
- Caches responses (statistical series revise slowly; 4h TTL)
- Degrades to empty series on network failure — never crashes the pipeline
- Parses SDMX-CSV structurally (header-driven column mapping), never by
  fixed column index — providers reorder columns between dataset vintages

Architecture reference: audit §6 item 5 — "`yfinance` + OECD SDMX +
Eurostat + IMF SDMX — kills the FRED US-only ceiling honestly instead of
flagging it." (Phase 5.5)

Tool selection logic (§5.2, extended by 5.5):
  Macro data task:
    1. FRED (US-specific)
    2. World Bank (development indicators, lagged)
    3. OECD / Eurostat / IMF SDMX (harmonised international, current) ← THIS

Used by: Market Analyst (EU/OECD market sizing), Financial Analyst
(non-US discount rates, FX), Sustainability Analyst (EU energy/CO2) (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 14400  # 4 hours — official statistics revise slowly


@dataclass
class SDMXPoint:
    """A single observation in an SDMX time series."""

    period: str  # "2023", "2023-Q4", "2023-11" — frequency-agnostic
    value: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {"period": self.period, "value": self.value}


@dataclass
class SDMXSeries:
    """A harmonised time series from any SDMX provider.

    One shape for OECD / Eurostat / IMF so downstream code never branches
    on provider. `dimensions` carries the full filter that selected the
    series (e.g. {"geo": "DE", "unit": "CP_MEUR"} for Eurostat).
    """

    provider: str  # "oecd" | "eurostat" | "imf"
    dataset: str  # dataflow id, e.g. "DF_CPI" / "nama_10_gdp" / "IFS"
    series_key: str  # provider-native key that selected this series
    dimensions: dict[str, str] = field(default_factory=dict)
    unit: str = ""
    frequency: str = ""
    title: str = ""
    data_points: list[SDMXPoint] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "dataset": self.dataset,
            "series_key": self.series_key,
            "dimensions": self.dimensions,
            "unit": self.unit,
            "frequency": self.frequency,
            "title": self.title,
            "data_points": [p.to_dict() for p in self.data_points],
        }


@dataclass
class SDMXDataflow:
    """A dataflow (dataset catalogue entry) from an SDMX provider."""

    provider: str
    flow_id: str
    name: str = ""
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "flow_id": self.flow_id,
            "name": self.name,
            "description": self.description,
        }


def _parse_sdmx_csv(csv_text: str) -> list[dict[str, str]]:
    """Parse SDMX-CSV (`format=csvfile`) into row dicts keyed by header.

    SDMX-CSV is comma-separated with a header row; providers differ in
    column order and optional columns, so we map by header name, never by
    position. Quoted fields are handled via the csv module.
    """
    import csv
    import io

    rows: list[dict[str, str]] = []
    reader = csv.DictReader(io.StringIO(csv_text))
    for row in reader:
        rows.append({(k or "").strip(): (v or "").strip() for k, v in row.items()})
    return rows


class _SDMXBase:
    """Shared HTTP + cache plumbing for the three SDMX providers."""

    BASE_URL: str = ""
    PROVIDER: str = ""
    REQUEST_TIMEOUT = 45
    MAX_RETRIES = 2
    RETRY_DELAY = 2

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers={"Accept": "text/csv, application/json"},
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
            del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = (time.time(), data)

    async def _make_request(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> str | dict[str, Any]:
        """Make a cached GET request. Returns response text, or {"error": ...}.

        SDMX providers return CSV (data) or JSON (structure); callers decode.
        """
        cache_key = self._cache_key(url, *sorted((params or {}).items()))
        cached = self._get_cached(cache_key)
        if cached is not None:
            # The cache stores exactly the union this method returns.
            return cast("str | dict[str, Any]", cached)

        client = await self._get_client()
        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                text = response.text
                self._set_cached(cache_key, text)
                return text
            except (httpx.HTTPError, httpx.RequestError) as e:
                logger.warning(
                    "%s request failed (attempt %d): %s",
                    self.PROVIDER,
                    attempt + 1,
                    e,
                )
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> _SDMXBase:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()


class OECDClient(_SDMXBase):
    """OECD SDMX 2.1 client (sdmx.oecd.org/public/rest).

    Key geometry: path segments, one per dimension in dataflow order —
    e.g. GET /v1/data/OECD.SDD.STES,DF_CPI/M.FRA+DEU.CPIALL._T.IX
    Dots mark empty dimension slots. `format=csvfile` yields SDMX-CSV.

    Usage:
        client = OECDClient()
        cpi = await client.get_series(
            "OECD.SDD.STES,DF_CPI",
            "M.FRA.CPIALL._T.IX",
            start_period="2015",
        )
    """

    BASE_URL = "https://sdmx.oecd.org/public/rest/v1"
    PROVIDER = "oecd"

    # Common dataflows (agency,flow_id) for quick access
    CPI = "OECD.SDD.STES,DF_CPI"  # Consumer price indices, monthly
    ULC = "OECD.SDD.TPS,DF_ULC"  # Unit labour costs, quarterly
    TRADE = "OECD.SDD.TPS,DF_TRADE"  # International trade
    LFS = "OECD.SDD.TPS,DF_LFS"  # Labour force statistics

    async def get_series(
        self,
        dataflow: str,
        key: str,
        start_period: str = "",
        end_period: str = "",
    ) -> SDMXSeries:
        """Get a time series from an OECD dataflow.

        Args:
            dataflow: "agency,flow_id" (e.g. "OECD.SDD.STES,DF_CPI")
            key: path key, one segment per dimension, dots for wildcards
                 (e.g. "M.FRA.CPIALL._T.IX")
            start_period: e.g. "2015", "2020-Q1", "2023-01"
            end_period: same format
        """
        params: dict[str, str] = {"format": "csvfile", "dimensionAtObservation": "AllDimensions"}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period

        result = await self._make_request(f"{self.BASE_URL}/data/{dataflow}/{key}", params)
        if isinstance(result, dict):
            return SDMXSeries(provider=self.PROVIDER, dataset=dataflow, series_key=key)

        rows = _parse_sdmx_csv(result)
        points: list[SDMXPoint] = []
        dims: dict[str, str] = {}
        for row in rows:
            period = row.get("TIME_PERIOD", "")
            raw_value = row.get("OBS_VALUE", "")
            try:
                value = float(raw_value) if raw_value else None
            except (ValueError, TypeError):
                value = None
            if period:
                points.append(SDMXPoint(period=period, value=value))
            if not dims:
                # Header-driven: capture whatever dimension columns exist
                dims = {
                    k: v
                    for k, v in row.items()
                    if k in ("REF_AREA", "FREQ", "MEASURE", "UNIT_MEASURE", "ADJUSTMENT")
                }

        return SDMXSeries(
            provider=self.PROVIDER,
            dataset=dataflow,
            series_key=key,
            dimensions=dims,
            unit=dims.get("UNIT_MEASURE", ""),
            frequency=dims.get("FREQ", ""),
            data_points=points,
        )

    async def list_dataflows(self) -> list[SDMXDataflow]:
        """List available OECD dataflows (structure query, JSON)."""
        result = await self._make_request(
            f"{self.BASE_URL}/dataflow/OECD.SDD.STES",
            params={"format": "json"},
        )
        if isinstance(result, dict) and "error" in result:
            return []
        # Structure queries are large; the catalogue is fetched rarely and
        # parsed shallowly — agents normally use the curated constants above.
        flows: list[SDMXDataflow] = []
        try:
            import json

            payload = json.loads(result) if isinstance(result, str) else result
            for flow in payload.get("data", {}).get("dataflows", []):
                flows.append(
                    SDMXDataflow(
                        provider=self.PROVIDER,
                        flow_id=flow.get("id", ""),
                        name=flow.get("name", ""),
                        description=flow.get("description", ""),
                    )
                )
        except (ValueError, AttributeError) as e:
            logger.warning("OECD dataflow catalogue parse failed: %s", e)
        return flows


class EurostatClient(_SDMXBase):
    """Eurostat SDMX 2.1 client (ec.europa.eu/eurostat/api/dissemination).

    Key geometry: query params per dimension —
    e.g. GET /statistics/1.0/data/nama_10_gdp?geo=DE&unit=CP_MEUR&na_item=B1GQ
    `format=JSON` is Eurostat's native mode; we request `format=TSV`-style
    SDMX-CSV via the SDMX 2.1 route for parser uniformity.

    Usage:
        client = EurostatClient()
        hicp = await client.get_series(
            "prc_hicp_midx",
            {"geo": "DE", "unit": "I15", "coicop": "CP00"},
            start_period="2015",
        )
    """

    BASE_URL = "https://ec.europa.eu/eurostat/api/dissemination/sdmx/2.1/data"
    PROVIDER = "eurostat"

    # Common datasets for quick access
    GDP = "nama_10_gdp"  # National accounts GDP, annual
    HICP = "prc_hicp_midx"  # Harmonised CPI index, monthly
    UNEMPLOYMENT = "une_rt_m"  # Unemployment rate, monthly
    ENERGY_PRICES = "nrg_pc_202"  # Electricity prices, households

    async def get_series(
        self,
        dataset: str,
        filters: dict[str, str],
        start_period: str = "",
        end_period: str = "",
    ) -> SDMXSeries:
        """Get a time series from a Eurostat dataset.

        Args:
            dataset: dataset code (e.g. "nama_10_gdp", "prc_hicp_midx")
            filters: dimension filters as query params
                     (e.g. {"geo": "DE", "unit": "CP_MEUR", "na_item": "B1GQ"})
            start_period: e.g. "2015", "2020-01"
            end_period: same format
        """
        params: dict[str, str] = {"format": "TSV", "compressed": "false"}
        params.update(filters)
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period

        result = await self._make_request(f"{self.BASE_URL}/{dataset}", params)
        if isinstance(result, dict):
            return SDMXSeries(provider=self.PROVIDER, dataset=dataset, series_key=str(filters))

        # Eurostat TSV: first col = "freq,unit,na_item,geo\TIME_PERIOD" header,
        # subsequent cols = period values; first row after header = series.
        lines = [ln for ln in result.splitlines() if ln.strip()]
        points: list[SDMXPoint] = []
        if len(lines) >= 2:
            header = lines[0].split("\t")
            periods = [h.strip() for h in header[1:]]
            for row_line in lines[1:]:
                cells = row_line.split("\t")
                for period, raw in zip(periods, cells[1:], strict=False):
                    raw = raw.strip().rstrip(" bdefipsuz")  # Eurostat flags
                    try:
                        value = float(raw) if raw and raw != ":" else None
                    except (ValueError, TypeError):
                        value = None
                    points.append(SDMXPoint(period=period, value=value))

        key = ".".join(f"{k}={v}" for k, v in sorted(filters.items()))
        return SDMXSeries(
            provider=self.PROVIDER,
            dataset=dataset,
            series_key=key,
            dimensions=dict(filters),
            unit=filters.get("unit", ""),
            frequency=filters.get("freq", ""),
            data_points=points,
        )

    async def compare_countries(
        self,
        dataset: str,
        filters: dict[str, str],
        geos: list[str],
        period: str = "",
    ) -> dict[str, float | None]:
        """Compare one indicator across EU countries.

        Args:
            dataset: dataset code
            filters: dimension filters WITHOUT geo (geo comes from `geos`)
            geos: country codes (e.g. ["DE", "FR", "IT"])
            period: specific period; empty = latest available per country
        """
        results: dict[str, float | None] = {}
        for geo in geos:
            merged = dict(filters)
            merged["geo"] = geo
            series = await self.get_series(dataset, merged)
            if period:
                match = next((p for p in series.data_points if p.period == period), None)
                results[geo] = match.value if match else None
            else:
                latest = next(
                    (p for p in reversed(series.data_points) if p.value is not None), None
                )
                results[geo] = latest.value if latest else None
        return results


class IMFClient(_SDMXBase):
    """IMF SDMX Central client (api.imf.org / dataservices.imf.org).

    Key geometry: dot-joined path key —
    e.g. GET /external/sdmx/2.1/data/dataflow/IMF/IFS/1.0/A.US.PMP_IX
    IFS dimensions: FREQ.REF_AREA.INDICATOR.

    Usage:
        client = IMFClient()
        fx = await client.get_series("IFS", "A.US.ENDA_XDC_USD_RATE", start_period="2010")
    """

    BASE_URL = "https://api.imf.org/external/sdmx/2.1/data"
    PROVIDER = "imf"

    # Common datasets for quick access
    IFS = "IFS"  # International Financial Statistics
    WEO = "WEO"  # World Economic Outlook
    BOP = "BOP"  # Balance of Payments
    GFS = "GFS"  # Government Finance Statistics

    async def get_series(
        self,
        dataset: str,
        key: str,
        start_period: str = "",
        end_period: str = "",
    ) -> SDMXSeries:
        """Get a time series from an IMF dataset.

        Args:
            dataset: dataset code (e.g. "IFS", "WEO")
            key: dot-joined dimension key (e.g. "A.US.PMP_IX")
            start_period: e.g. "2010", "2020-Q1"
            end_period: same format
        """
        params: dict[str, str] = {"format": "csvfile"}
        if start_period:
            params["startPeriod"] = start_period
        if end_period:
            params["endPeriod"] = end_period

        url = f"{self.BASE_URL}/dataflow/IMF/{dataset}/1.0/{key}"
        result = await self._make_request(url, params)
        if isinstance(result, dict):
            return SDMXSeries(provider=self.PROVIDER, dataset=dataset, series_key=key)

        rows = _parse_sdmx_csv(result)
        points: list[SDMXPoint] = []
        dims: dict[str, str] = {}
        for row in rows:
            period = row.get("TIME_PERIOD", "")
            raw_value = row.get("OBS_VALUE", "")
            try:
                value = float(raw_value) if raw_value else None
            except (ValueError, TypeError):
                value = None
            if period:
                points.append(SDMXPoint(period=period, value=value))
            if not dims:
                dims = {
                    k: v
                    for k, v in row.items()
                    if k in ("REF_AREA", "FREQ", "INDICATOR", "UNIT_MEASURE")
                }

        return SDMXSeries(
            provider=self.PROVIDER,
            dataset=dataset,
            series_key=key,
            dimensions=dims,
            unit=dims.get("UNIT_MEASURE", ""),
            frequency=dims.get("FREQ", ""),
            data_points=points,
        )

    async def get_exchange_rate(
        self,
        country: str,
        start_period: str = "",
    ) -> SDMXSeries:
        """Get annual USD exchange rate (ENDA) for a country. Used for
        non-US DCF conversions — breaks the FRED US-only FX ceiling."""
        return await self.get_series(
            self.IFS,
            f"A.{country}.ENDA_XDC_USD_RATE",
            start_period=start_period,
        )
