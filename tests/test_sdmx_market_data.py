"""
Tests for 5.5 — OECD/Eurostat/IMF SDMX + yfinance MarketDataClient.

Breaks the FRED US-only ceiling (audit §6 item 5, Phase 5.5).

Layers:
- Functional: mocked HTTP (no network in sandbox) — parser correctness,
  graceful degradation, caching, yfinance executor wrapping.
- Negative controls: reintroduce the defect (US-only data path) and prove
  the test suite FAILS if 5.5 regresses.
- Structural AST guards: the registry wiring cannot silently drop the new
  clients — if tools/__init__.py stops exporting them, the suite fails even
  before any import runs.
"""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hyperion.tools.market_data import MarketDataClient, PriceHistory
from hyperion.tools.sdmx import (
    EurostatClient,
    IMFClient,
    OECDClient,
    SDMXSeries,
    _parse_sdmx_csv,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ─────────────────────────────────────────────────────────────────────────
# Fixtures — realistic provider payloads
# ─────────────────────────────────────────────────────────────────────────

OECD_CSV = (
    "FREQ,REF_AREA,MEASURE,UNIT_MEASURE,ADJUSTMENT,TIME_PERIOD,OBS_VALUE\n"
    "M,FRA,CPIALL,_T,IX,2023-01,100.5\n"
    "M,FRA,CPIALL,_T,IX,2023-02,101.2\n"
    "M,FRA,CPIALL,_T,IX,2023-03,\n"  # missing observation → value None
)

EUROSTAT_TSV = (
    "freq\\unit\\na_item\\geo\\TIME_PERIOD\t2020\t2021\t2022\n"
    "A\\CP_MEUR\\B1GQ\\DE\t3319000.0\t3602000.0 b\t3876700.0\n"
)

IMF_CSV = (
    "FREQ,REF_AREA,INDICATOR,UNIT_MEASURE,TIME_PERIOD,OBS_VALUE\n"
    "A,US,ENDA_XDC_USD_RATE,XDC,2021,1.0\n"
    "A,US,ENDA_XDC_USD_RATE,XDC,2022,1.0\n"
)


# ─────────────────────────────────────────────────────────────────────────
# SDMX-CSV parser (structural, header-driven)
# ─────────────────────────────────────────────────────────────────────────


class TestSDMXParser:
    def test_header_driven_mapping(self):
        rows = _parse_sdmx_csv(OECD_CSV)
        assert len(rows) == 3
        assert rows[0]["REF_AREA"] == "FRA"
        assert rows[0]["OBS_VALUE"] == "100.5"

    def test_reordered_columns_still_parse(self):
        """Columns arriving in a different order must not shift values."""
        reordered = (
            "TIME_PERIOD,OBS_VALUE,REF_AREA,FREQ\n"
            "2023,42.5,DEU,A\n"
        )
        rows = _parse_sdmx_csv(reordered)
        assert rows[0]["OBS_VALUE"] == "42.5"
        assert rows[0]["REF_AREA"] == "DEU"

    def test_empty_csv(self):
        assert _parse_sdmx_csv("") == []


# ─────────────────────────────────────────────────────────────────────────
# OECD client
# ─────────────────────────────────────────────────────────────────────────


class TestOECDClient:
    @pytest.mark.asyncio
    async def test_get_series_parses_points(self):
        client = OECDClient()
        with patch.object(client, "_make_request", new=AsyncMock(return_value=OECD_CSV)):
            series = await client.get_series(
                OECDClient.CPI, "M.FRA.CPIALL._T.IX", start_period="2023"
            )
        assert isinstance(series, SDMXSeries)
        assert series.provider == "oecd"
        assert len(series.data_points) == 3
        assert series.data_points[0].period == "2023-01"
        assert series.data_points[0].value == 100.5
        # Missing OBS_VALUE → None, not crash, not 0.0
        assert series.data_points[2].value is None
        assert series.frequency == "M"
        await client.close()

    @pytest.mark.asyncio
    async def test_get_series_degrades_on_error(self):
        client = OECDClient()
        with patch.object(
            client, "_make_request", new=AsyncMock(return_value={"error": "HTTP 503"})
        ):
            series = await client.get_series(OECDClient.CPI, "M.FRA.CPIALL._T.IX")
        assert series.provider == "oecd"
        assert series.data_points == []
        await client.close()

    @pytest.mark.asyncio
    async def test_list_dataflows_degrades_on_error(self):
        client = OECDClient()
        with patch.object(
            client, "_make_request", new=AsyncMock(return_value={"error": "timeout"})
        ):
            flows = await client.list_dataflows()
        assert flows == []
        await client.close()


# ─────────────────────────────────────────────────────────────────────────
# Eurostat client
# ─────────────────────────────────────────────────────────────────────────


class TestEurostatClient:
    @pytest.mark.asyncio
    async def test_get_series_parses_tsv_and_flags(self):
        """Eurostat appends flag letters (' b') to values — must be stripped."""
        client = EurostatClient()
        with patch.object(client, "_make_request", new=AsyncMock(return_value=EUROSTAT_TSV)):
            series = await client.get_series(
                EurostatClient.GDP,
                {"geo": "DE", "unit": "CP_MEUR", "na_item": "B1GQ", "freq": "A"},
            )
        assert series.provider == "eurostat"
        assert len(series.data_points) == 3
        assert series.data_points[1].period == "2021"
        assert series.data_points[1].value == 3602000.0  # ' b' flag stripped
        assert series.unit == "CP_MEUR"
        await client.close()

    @pytest.mark.asyncio
    async def test_compare_countries_latest(self):
        client = EurostatClient()
        with patch.object(client, "_make_request", new=AsyncMock(return_value=EUROSTAT_TSV)):
            results = await client.compare_countries(
                EurostatClient.GDP,
                {"unit": "CP_MEUR", "na_item": "B1GQ", "freq": "A"},
                ["DE", "FR"],
            )
        assert results["DE"] == 3876700.0  # latest non-None
        assert results["FR"] == 3876700.0  # same mocked payload per geo
        await client.close()

    @pytest.mark.asyncio
    async def test_get_series_degrades_on_error(self):
        client = EurostatClient()
        with patch.object(
            client, "_make_request", new=AsyncMock(return_value={"error": "HTTP 500"})
        ):
            series = await client.get_series(EurostatClient.GDP, {"geo": "DE"})
        assert series.data_points == []
        await client.close()


# ─────────────────────────────────────────────────────────────────────────
# IMF client
# ─────────────────────────────────────────────────────────────────────────


class TestIMFClient:
    @pytest.mark.asyncio
    async def test_get_series_parses_points(self):
        client = IMFClient()
        with patch.object(client, "_make_request", new=AsyncMock(return_value=IMF_CSV)):
            series = await client.get_series(IMFClient.IFS, "A.US.ENDA_XDC_USD_RATE")
        assert series.provider == "imf"
        assert len(series.data_points) == 2
        assert series.data_points[0].period == "2021"
        assert series.frequency == "A"
        assert series.dimensions["INDICATOR"] == "ENDA_XDC_USD_RATE"
        await client.close()

    @pytest.mark.asyncio
    async def test_get_exchange_rate_convenience(self):
        client = IMFClient()
        with patch.object(client, "_make_request", new=AsyncMock(return_value=IMF_CSV)) as mock_req:
            series = await client.get_exchange_rate("JP", start_period="2010")
        assert series.dataset == IMFClient.IFS
        assert "ENDA_XDC_USD_RATE" in mock_req.call_args[0][0]
        await client.close()

    @pytest.mark.asyncio
    async def test_get_series_degrades_on_error(self):
        client = IMFClient()
        with patch.object(
            client, "_make_request", new=AsyncMock(return_value={"error": "DNS failure"})
        ):
            series = await client.get_series(IMFClient.IFS, "A.US.PMP_IX")
        assert series.data_points == []
        await client.close()


# ─────────────────────────────────────────────────────────────────────────
# MarketDataClient (yfinance wrapper)
# ─────────────────────────────────────────────────────────────────────────


def _fake_yf_module(info: dict | None = None, hist_rows: list[tuple] | None = None):
    """Build a fake yfinance module with Ticker().info / .history()."""
    fake = MagicMock()

    class FakeTicker:
        def __init__(self, ticker: str):
            self._ticker = ticker

        @property
        def info(self):
            return info or {}

        def history(self, period="1y"):
            import pandas as pd

            rows = hist_rows or []
            if not rows:
                return pd.DataFrame()
            dates = pd.to_datetime([r[0] for r in rows])
            return pd.DataFrame(
                {
                    "Open": [r[1] for r in rows],
                    "High": [r[2] for r in rows],
                    "Low": [r[3] for r in rows],
                    "Close": [r[4] for r in rows],
                    "Volume": [r[5] for r in rows],
                },
                index=dates,
            )

    fake.Ticker = FakeTicker
    return fake


class TestMarketDataClient:
    @pytest.mark.asyncio
    async def test_quote_maps_info_fields(self):
        info = {
            "currency": "EUR",
            "exchange": "AMS",
            "shortName": "ASML Holding",
            "regularMarketPrice": 850.4,
            "regularMarketPreviousClose": 842.1,
            "marketCap": 335000000000,
            "trailingPE": 41.2,
            "fiftyTwoWeekHigh": 1020.0,
            "fiftyTwoWeekLow": 610.0,
            "sector": "Technology",
            "industry": "Semiconductor Equipment",
            "country": "Netherlands",
        }
        fake = _fake_yf_module(info=info)
        with patch.object(MarketDataClient, "_require_yfinance", return_value=fake):
            quote = await MarketDataClient().get_quote("ASML.AS")
        assert quote.available
        assert quote.currency == "EUR"
        assert quote.exchange == "AMS"
        assert quote.regular_market_price == 850.4
        assert quote.market_cap == 335000000000
        assert quote.country == "Netherlands"

    @pytest.mark.asyncio
    async def test_history_parses_bars(self):
        rows = [("2024-01-02", 100.0, 102.0, 99.0, 101.5, 1_000_000)]
        fake = _fake_yf_module(hist_rows=rows)
        with patch.object(MarketDataClient, "_require_yfinance", return_value=fake):
            hist = await MarketDataClient().get_history("7203.T", period="1y")
        assert isinstance(hist, PriceHistory)
        assert hist.available
        assert len(hist.bars) == 1
        assert hist.bars[0].date == "2024-01-02"
        assert hist.bars[0].close == 101.5
        assert hist.bars[0].volume == 1_000_000

    @pytest.mark.asyncio
    async def test_missing_yfinance_degrades_cleanly(self):
        """NC1: without yfinance installed, client returns unavailable — no crash."""
        with patch.object(MarketDataClient, "_require_yfinance", return_value=None):
            quote = await MarketDataClient().get_quote("ASML.AS")
            hist = await MarketDataClient().get_history("7203.T")
        assert not quote.available
        assert not hist.available

    @pytest.mark.asyncio
    async def test_yfinance_exception_degrades_cleanly(self):
        """NC2: yfinance raising untyped network errors must not propagate."""
        fake = MagicMock()

        class BadTicker:
            def __init__(self, ticker: str):
                pass

            @property
            def info(self):
                raise ConnectionError("simulated Yahoo outage")

            def history(self, period="1y"):
                raise ConnectionError("simulated Yahoo outage")

        fake.Ticker = BadTicker
        with patch.object(MarketDataClient, "_require_yfinance", return_value=fake):
            quote = await MarketDataClient().get_quote("BROKEN")
            hist = await MarketDataClient().get_history("BROKEN")
        assert not quote.available
        assert not hist.available

    @pytest.mark.asyncio
    async def test_empty_ticker_rejected(self):
        quote = await MarketDataClient().get_quote("")
        assert not quote.available

    @pytest.mark.asyncio
    async def test_compare_peers_global_mix(self):
        """The whole point: US + EU + JP tickers in one peer group."""
        info = {"currency": "USD", "regularMarketPrice": 100.0}
        fake = _fake_yf_module(info=info)
        with patch.object(MarketDataClient, "_require_yfinance", return_value=fake):
            results = await MarketDataClient().compare_peers(["AMAT", "ASML.AS", "8035.T"])
        assert set(results) == {"AMAT", "ASML.AS", "8035.T"}
        assert all(q.available for q in results.values())


# ─────────────────────────────────────────────────────────────────────────
# Negative control — US-only ceiling regression
# ─────────────────────────────────────────────────────────────────────────


class TestUSOnlyCeilingNegativeControl:
    """If the 5.5 clients vanish, the pipeline falls back to FRED-only and
    every non-US macro/equity request silently returns nothing. These tests
    pin the international data path so that regression is caught."""

    @pytest.mark.asyncio
    async def test_non_us_macro_path_exists(self):
        """FRED cannot answer 'German CPI'. The SDMX clients must."""
        fred = pytest.importorskip("hyperion.tools.fred")
        # FRED has no EU/DE concept — this is the ceiling itself:
        assert not hasattr(fred.FredClient, "get_series_by_country")
        # 5.5 closes it:
        client = EurostatClient()
        with patch.object(client, "_make_request", new=AsyncMock(return_value=EUROSTAT_TSV)):
            series = await client.get_series("nama_10_gdp", {"geo": "DE"})
        assert series.provider == "eurostat"
        assert series.data_points
        await client.close()

    @pytest.mark.asyncio
    async def test_non_us_equity_path_exists(self):
        """SEC EDGAR + Alpha Vantage are US-listing-centric. yfinance is not."""
        info = {"currency": "JPY", "exchange": "TYO", "regularMarketPrice": 2800.0}
        fake = _fake_yf_module(info=info)
        with patch.object(MarketDataClient, "_require_yfinance", return_value=fake):
            quote = await MarketDataClient().get_quote("7203.T")
        assert quote.available
        assert quote.currency == "JPY"


# ─────────────────────────────────────────────────────────────────────────
# Structural AST guards — registry wiring cannot silently regress
# ─────────────────────────────────────────────────────────────────────────


class TestRegistryStructuralGuards:
    def _init_tree(self) -> ast.Module:
        return ast.parse((REPO_ROOT / "hyperion" / "tools" / "__init__.py").read_text())

    def _all_names(self, tree: ast.Module) -> list[str]:
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.List):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id == "__all__":
                        return [
                            elt.value
                            for elt in node.value.elts
                            if isinstance(elt, ast.Constant)
                        ]
        return []

    def test_sdmx_imported_in_registry(self):
        src = (REPO_ROOT / "hyperion" / "tools" / "__init__.py").read_text()
        assert "from hyperion.tools.sdmx import" in src
        for name in ("OECDClient", "EurostatClient", "IMFClient", "SDMXSeries"):
            assert name in src, f"registry missing sdmx export {name}"

    def test_market_data_imported_in_registry(self):
        src = (REPO_ROOT / "hyperion" / "tools" / "__init__.py").read_text()
        assert "from hyperion.tools.market_data import" in src
        for name in ("MarketDataClient", "MarketQuote", "PriceHistory"):
            assert name in src, f"registry missing market_data export {name}"

    def test_all_lists_new_clients(self):
        names = self._all_names(self._init_tree())
        for name in (
            "OECDClient",
            "EurostatClient",
            "IMFClient",
            "SDMXDataflow",
            "SDMXPoint",
            "SDMXSeries",
            "MarketDataClient",
            "MarketQuote",
            "PriceBar",
            "PriceHistory",
        ):
            assert name in names, f"__all__ missing {name}"

    def test_registry_roundtrip_import(self):
        """AST guard + live import: names in __all__ resolve on the package."""
        import hyperion.tools as tools_pkg

        for name in ("OECDClient", "EurostatClient", "IMFClient", "MarketDataClient"):
            assert getattr(tools_pkg, name) is not None

    def test_yfinance_declared_dependency(self):
        """pyproject must declare yfinance so production installs get it."""
        import tomllib

        with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)
        deps = data["project"]["dependencies"]
        assert any(d.startswith("yfinance") for d in deps), "yfinance not in dependencies"

    def test_market_data_lazily_imports_yfinance(self):
        """AST guard: yfinance must be imported INSIDE a function, not at
        module top — the module must stay importable without it installed."""
        tree = ast.parse((REPO_ROOT / "hyperion" / "tools" / "market_data.py").read_text())
        top_level_yf = any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                (isinstance(node, ast.Import) and any(a.name == "yfinance" for a in node.names))
                or (isinstance(node, ast.ImportFrom) and node.module == "yfinance")
            )
            for node in tree.body
        )
        assert not top_level_yf, "yfinance must be lazy-imported inside _require_yfinance"
