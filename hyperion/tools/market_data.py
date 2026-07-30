"""
HYPERION Market Data Client — yfinance wrapper for global public equities.

FRED + the SDMX trio (5.5) cover official macro statistics. They do NOT
cover live equity fundamentals, price history, or company financials for
non-US listings. yfinance (Yahoo Finance) closes that gap:

- Global ticker coverage — LSE (.L), Euronext (.PA/.AS), Xetra (.DE),
  Tokyo (.T), Toronto (.TO), ASX (.AX), NSE (.NS) — not just US exchanges
- Price history (OHLCV) for any listed instrument
- Fundamentals (market cap, P/E, revenue, margins) for company profiling
- Financial statements (income statement, balance sheet, cash flow)

This is NOT a generic "fetch stock data" wrapper. It:
- Runs yfinance in a thread executor — the library is synchronous, so
  direct calls would block the asyncio event loop the AgentBus depends on
- Returns typed dataclasses with the same .to_dict() contract as every
  other HYPERION data source
- Degrades gracefully: missing yfinance or a network failure yields
  available=False / empty results with a logged warning, never a crash
- Caches quotes (markets tick fast, but research runs don't need tick
  precision — 15-minute TTL matches free-tier data lag anyway)

Architecture reference: audit §6 item 5 — "`yfinance` + OECD SDMX +
Eurostat + IMF SDMX — kills the FRED US-only ceiling honestly instead of
flagging it." (Phase 5.5)

Tool selection logic (§5.2, extended by 5.5):
  Company/equity data task:
    1. SEC EDGAR (US-listed filings, authoritative)
    2. Alpha Vantage (US quotes + fundamentals, key-limited)
    3. MarketDataClient (global listings, no key) ← THIS

Used by: Market Analyst (comparable companies, global peers), Financial
Analyst (non-US comps, beta, price history for returns) (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
from dataclasses import dataclass, field
from typing import Any, cast

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 900  # 15 min — matches free Yahoo data lag


@dataclass
class MarketQuote:
    """A real-time quote snapshot for a ticker."""

    ticker: str
    available: bool = False
    currency: str = ""
    exchange: str = ""
    short_name: str = ""
    regular_market_price: float | None = None
    previous_close: float | None = None
    market_cap: float | None = None
    trailing_pe: float | None = None
    fifty_two_week_high: float | None = None
    fifty_two_week_low: float | None = None
    sector: str = ""
    industry: str = ""
    country: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "available": self.available,
            "currency": self.currency,
            "exchange": self.exchange,
            "short_name": self.short_name,
            "regular_market_price": self.regular_market_price,
            "previous_close": self.previous_close,
            "market_cap": self.market_cap,
            "trailing_pe": self.trailing_pe,
            "fifty_two_week_high": self.fifty_two_week_high,
            "fifty_two_week_low": self.fifty_two_week_low,
            "sector": self.sector,
            "industry": self.industry,
            "country": self.country,
        }


@dataclass
class PriceBar:
    """A single OHLCV bar."""

    date: str
    open: float | None = None
    high: float | None = None
    low: float | None = None
    close: float | None = None
    volume: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "open": self.open,
            "high": self.high,
            "low": self.low,
            "close": self.close,
            "volume": self.volume,
        }


@dataclass
class PriceHistory:
    """OHLCV history for a ticker."""

    ticker: str
    period: str = ""
    available: bool = False
    bars: list[PriceBar] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "period": self.period,
            "available": self.available,
            "bars": [b.to_dict() for b in self.bars],
        }


class MarketDataClient:
    """yfinance-backed global equity client.

    All yfinance calls run in the default executor because the library is
    synchronous — awaiting a blocking call inside the AgentBus event loop
    would stall every concurrent agent.

    Usage:
        client = MarketDataClient()
        quote = await client.get_quote("ASML.AS")  # Euronext Amsterdam
        hist = await client.get_history("7203.T", period="1y")  # Toyota, Tokyo
    """

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._cache: dict[str, tuple[float, Any]] = {}

    def _cache_key(self, *args: Any) -> str:
        return hashlib.md5(":".join(str(a) for a in args).encode()).hexdigest()

    def _get_cached(self, key: str) -> Any | None:
        if key in self._cache:
            timestamp, data = self._cache[key]
            if time.time() - timestamp < CACHE_TTL_SECONDS:
                return data
            del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = (time.time(), data)

    @staticmethod
    def _require_yfinance() -> Any | None:
        """Import yfinance lazily — it is an optional runtime dependency.

        Returns the module, or None (logged) when not installed. Keeping the
        import inside the function means the module itself is always
        importable even on hosts without yfinance (e.g. the 985MB sandbox).
        """
        try:
            import yfinance as yf

            return yf
        except ImportError:
            logger.warning(
                "yfinance not installed — MarketDataClient degrades to unavailable. "
                "Install with: pip install yfinance"
            )
            return None

    @staticmethod
    def _quote_from_info(ticker: str, info: dict[str, Any]) -> MarketQuote:
        """Map a yfinance .info dict onto the typed quote contract."""
        if not info:
            return MarketQuote(ticker=ticker, available=False)

        def _num(key: str) -> float | None:
            value = info.get(key)
            try:
                return float(value) if value is not None else None
            except (ValueError, TypeError):
                return None

        return MarketQuote(
            ticker=ticker,
            available=True,
            currency=str(info.get("currency") or ""),
            exchange=str(info.get("exchange") or ""),
            short_name=str(info.get("shortName") or ""),
            regular_market_price=_num("regularMarketPrice"),
            previous_close=_num("regularMarketPreviousClose"),
            market_cap=_num("marketCap"),
            trailing_pe=_num("trailingPE"),
            fifty_two_week_high=_num("fiftyTwoWeekHigh"),
            fifty_two_week_low=_num("fiftyTwoWeekLow"),
            sector=str(info.get("sector") or ""),
            industry=str(info.get("industry") or ""),
            country=str(info.get("country") or ""),
        )

    async def get_quote(self, ticker: str) -> MarketQuote:
        """Get a quote snapshot for a global ticker (e.g. "ASML.AS", "7203.T").

        Returns MarketQuote(available=False) when yfinance is missing, the
        ticker is unknown, or the network fails — never raises.
        """
        if not ticker:
            return MarketQuote(ticker=ticker, available=False)

        cache_key = self._cache_key("quote", ticker)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cast("MarketQuote", cached)

        yf = self._require_yfinance()
        if yf is None:
            return MarketQuote(ticker=ticker, available=False)

        loop = asyncio.get_running_loop()

        def _fetch() -> dict[str, Any]:
            try:
                return dict(yf.Ticker(ticker).info or {})
            except Exception as e:  # noqa: BLE001 - yfinance raises untyped
                # network/parse errors; quote is optional enrichment
                logger.warning("yfinance quote fetch failed for %s: %s", ticker, e)
                return {}

        info = await loop.run_in_executor(None, _fetch)
        quote = self._quote_from_info(ticker, info)
        if quote.available:
            self._set_cached(cache_key, quote)
        return quote

    async def get_history(self, ticker: str, period: str = "1y") -> PriceHistory:
        """Get OHLCV price history for a global ticker.

        Args:
            ticker: e.g. "NESN.SW" (Nestlé, SIX), "RELIANCE.NS" (NSE India)
            period: yfinance period string — "1mo", "6mo", "1y", "5y", "max"

        Returns PriceHistory(available=False) on any failure — never raises.
        """
        if not ticker:
            return PriceHistory(ticker=ticker, period=period, available=False)

        cache_key = self._cache_key("history", ticker, period)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cast("PriceHistory", cached)

        yf = self._require_yfinance()
        if yf is None:
            return PriceHistory(ticker=ticker, period=period, available=False)

        loop = asyncio.get_running_loop()

        def _fetch() -> list[PriceBar]:
            try:
                df = yf.Ticker(ticker).history(period=period)
            except Exception as e:  # noqa: BLE001 - yfinance raises untyped
                # network/parse errors; history is optional enrichment
                logger.warning("yfinance history fetch failed for %s: %s", ticker, e)
                return []
            bars: list[PriceBar] = []
            if df is None or getattr(df, "empty", True):
                return bars
            for idx, row in df.iterrows():
                bars.append(
                    PriceBar(
                        date=str(idx.date()) if hasattr(idx, "date") else str(idx),
                        open=float(row["Open"]) if row["Open"] is not None else None,
                        high=float(row["High"]) if row["High"] is not None else None,
                        low=float(row["Low"]) if row["Low"] is not None else None,
                        close=float(row["Close"]) if row["Close"] is not None else None,
                        volume=int(row["Volume"]) if row["Volume"] is not None else 0,
                    )
                )
            return bars

        bars = await loop.run_in_executor(None, _fetch)
        history = PriceHistory(ticker=ticker, period=period, available=bool(bars), bars=bars)
        if history.available:
            self._set_cached(cache_key, history)
        return history

    async def compare_peers(self, tickers: list[str]) -> dict[str, MarketQuote]:
        """Fetch quotes for a peer group in one pass.

        Args:
            tickers: e.g. ["ASML.AS", "AMAT", "LRCX"] — mixing exchanges is
                     the whole point (global comps, not US-only).
        """
        results: dict[str, MarketQuote] = {}
        for ticker in tickers:
            results[ticker] = await self.get_quote(ticker)
        return results

    async def close(self) -> None:
        """No persistent connection — yfinance manages its own sessions."""

    async def __aenter__(self) -> MarketDataClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()

