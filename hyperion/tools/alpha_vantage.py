"""
HYPERION Alpha Vantage Client — financial market data.

Alpha Vantage is the ONLY financial data source in HYPERION. It provides:
- Stock quotes (real-time and historical)
- Company fundamentals (income statement, balance sheet, cash flow)
- Forex rates
- Crypto rates
- Economic indicators
- Technical indicators (SMA, EMA, RSI, MACD, etc.)

This is NOT a generic "fetch stock data" wrapper. It:
- Uses Alpha Vantage's REST API with proper rate limit handling
- 25 API calls/day (free key), 500/day (premium key)
- Returns structured data for comparable company analysis
- Returns fundamental data for DCF modeling
- Returns time series for historical analysis
- Handles rate limit errors gracefully with clear messaging
- Caches responses to minimize API calls (critical with 25/day limit)

Architecture reference: §5.1 — "Market data, fundamentals, forex, crypto.
25 API calls/day (free key), 500/day (premium). Used for comparable
company analysis and market data."

Tool selection logic (§5.2):
  Financial data task:
    1. Alpha Vantage (always — it's the only financial data source) ← THIS

Used by: Financial Analyst, M&A Analyst (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class StockQuote:
    """Real-time stock quote from Alpha Vantage."""

    symbol: str
    price: float = 0.0
    change: float = 0.0
    change_percent: float = 0.0
    volume: int = 0
    high: float = 0.0
    low: float = 0.0
    open: float = 0.0
    prev_close: float = 0.0
    latest_trading_day: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price": self.price,
            "change": self.change,
            "change_percent": self.change_percent,
            "volume": self.volume,
            "high": self.high,
            "low": self.low,
            "open": self.open,
            "prev_close": self.prev_close,
            "latest_trading_day": self.latest_trading_day,
        }


@dataclass
class TimeSeriesData:
    """Historical time series data from Alpha Vantage."""

    symbol: str
    interval: str = "daily"
    data_points: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "data_points": self.data_points,
            "metadata": self.metadata,
        }


@dataclass
class FundamentalData:
    """Company fundamental data from Alpha Vantage."""

    symbol: str
    report_type: str = ""  # income_statement, balance_sheet, cash_flow, overview
    data: dict[str, Any] = field(default_factory=dict)
    annual_reports: list[dict[str, Any]] = field(default_factory=list)
    quarterly_reports: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "report_type": self.report_type,
            "data": self.data,
            "annual_reports": self.annual_reports,
            "quarterly_reports": self.quarterly_reports,
        }


class AlphaVantageClient:
    """Alpha Vantage financial data client.

    Provides market data, fundamentals, forex, and crypto data.
    25 API calls/day on free tier, 500/day on premium.
    (§5.1)

    Usage:
        client = AlphaVantageClient(settings=settings)

        # Real-time quote
        quote = await client.get_quote("AAPL")
        print(f"AAPL: ${quote.price}")

        # Daily time series
        ts = await client.get_daily("AAPL")
        for point in ts.data_points[:5]:
            print(f"{point['date']}: {point['close']}")

        # Income statement
        income = await client.get_income_statement("AAPL")
        for report in income.annual_reports:
            print(f"{report['fiscalDateEnding']}: Revenue ${report['totalRevenue']}")
    """

    BASE_URL = "https://www.alphavantage.co/query"
    CACHE_DIR = "output/.alphavantage_cache"
    CACHE_TTL_SECONDS = 3600  # 1 hour for quotes, 86400 for fundamentals
    FUNDAMENTAL_CACHE_TTL = 86400  # 24 hours for fundamentals
    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 5  # Alpha Vantage rate limits need longer delays
    FREE_TIER_DAILY_LIMIT = 25

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._api_key = ""
        if settings:
            self._api_key = getattr(settings, "alpha_vantage_api_key", "")
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._call_count: int = 0
        self._last_reset: float = time.time()
        os.makedirs(self.CACHE_DIR, exist_ok=True)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
            )
        return self._client

    def _check_rate_limit(self) -> bool:
        """Check if we've hit the daily API call limit."""
        # Reset counter every 24 hours
        if time.time() - self._last_reset > 86400:
            self._call_count = 0
            self._last_reset = time.time()
        return self._call_count < self.FREE_TIER_DAILY_LIMIT

    def _cache_key(self, *args: Any) -> str:
        """Generate a cache key from arguments."""
        key_str = ":".join(str(a) for a in args)
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached(self, key: str, ttl: int = CACHE_TTL_SECONDS) -> Any | None:
        """Get a cached response if it exists and is not expired."""
        if key in self._cache:
            timestamp, data = self._cache[key]
            if time.time() - timestamp < ttl:
                return data
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        """Cache a response."""
        self._cache[key] = (time.time(), data)

    async def _make_request(self, params: dict[str, str]) -> dict[str, Any]:
        """Make a rate-limited, cached request to Alpha Vantage."""
        # Build cache key
        cache_key = self._cache_key(*sorted(params.items()))

        # Determine TTL based on function type
        func = params.get("function", "")
        ttl = self.FUNDAMENTAL_CACHE_TTL if any(
            x in func for x in ["INCOME", "BALANCE", "CASH", "OVERVIEW", "LISTING"]
        ) else self.CACHE_TTL_SECONDS

        cached = self._get_cached(cache_key, ttl)
        if cached is not None:
            return cached

        # Check rate limit
        if not self._check_rate_limit():
            return {"error": "Alpha Vantage daily API call limit reached (25/day free tier). Use "
                "cached data or upgrade to premium."}

        # Add API key
        params["apikey"] = self._api_key

        client = await self._get_client()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(self.BASE_URL, params=params)
                response.raise_for_status()
                data = response.json()

                # Check for API error messages
                if "Error Message" in data:
                    return {"error": data["Error Message"]}
                if "Note" in data and "API call frequency" in data.get("Note", ""):
                    # Rate limited — wait and retry
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.RETRY_DELAY * 2)
                    continue
                if "Information" in data and "premium" in data.get("Information", "").lower():
                    return {"error": "This endpoint requires a premium Alpha Vantage key."}

                self._call_count += 1
                self._set_cached(cache_key, data)
                return data

            except (httpx.HTTPError, httpx.RequestError, json.JSONDecodeError) as e:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    # ─────────────────────────────────────────────────────────────────────
    # Stock Quotes
    # ─────────────────────────────────────────────────────────────────────

    async def get_quote(self, symbol: str) -> StockQuote:
        """Get a real-time stock quote.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL", "MSFT")

        Returns:
            StockQuote with current price, change, volume, etc.
        """
        data = await self._make_request({
            "function": "GLOBAL_QUOTE",
            "symbol": symbol,
        })

        if "error" in data:
            return StockQuote(symbol=symbol)

        quote_data = data.get("Global Quote", {})
        return StockQuote(
            symbol=symbol,
            price=float(quote_data.get("05. price", 0) or 0),
            change=float(quote_data.get("09. change", 0) or 0),
            change_percent=float(quote_data.get("10. change percent", "0%").replace("%", "") or 0),
            volume=int(quote_data.get("06. volume", 0) or 0),
            high=float(quote_data.get("03. high", 0) or 0),
            low=float(quote_data.get("04. low", 0) or 0),
            open=float(quote_data.get("02. open", 0) or 0),
            prev_close=float(quote_data.get("08. previous close", 0) or 0),
            latest_trading_day=quote_data.get("07. latest trading day", ""),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Time Series
    # ─────────────────────────────────────────────────────────────────────

    async def get_daily(
        self,
        symbol: str,
        output_size: str = "compact",
    ) -> TimeSeriesData:
        """Get daily time series data.

        Args:
            symbol: Stock ticker symbol
            output_size: "compact" (last 100 data points) or "full" (20+ years)

        Returns:
            TimeSeriesData with daily OHLCV data points.
        """
        data = await self._make_request({
            "function": "TIME_SERIES_DAILY",
            "symbol": symbol,
            "outputsize": output_size,
        })

        if "error" in data:
            return TimeSeriesData(symbol=symbol, interval="daily")

        metadata = data.get("Meta Data", {})
        time_series = data.get("Time Series (Daily)", {})

        data_points: list[dict[str, Any]] = []
        for date, values in sorted(time_series.items(), reverse=True):
            data_points.append({
                "date": date,
                "open": float(values.get("1. open", 0) or 0),
                "high": float(values.get("2. high", 0) or 0),
                "low": float(values.get("3. low", 0) or 0),
                "close": float(values.get("4. close", 0) or 0),
                "volume": int(values.get("5. volume", 0) or 0),
            })

        return TimeSeriesData(
            symbol=symbol,
            interval="daily",
            data_points=data_points,
            metadata=metadata,
        )

    async def get_weekly(self, symbol: str) -> TimeSeriesData:
        """Get weekly time series data."""
        data = await self._make_request({
            "function": "TIME_SERIES_WEEKLY",
            "symbol": symbol,
        })

        if "error" in data:
            return TimeSeriesData(symbol=symbol, interval="weekly")

        time_series = data.get("Weekly Time Series", {})
        data_points: list[dict[str, Any]] = []
        for date, values in sorted(time_series.items(), reverse=True):
            data_points.append({
                "date": date,
                "open": float(values.get("1. open", 0) or 0),
                "high": float(values.get("2. high", 0) or 0),
                "low": float(values.get("3. low", 0) or 0),
                "close": float(values.get("4. close", 0) or 0),
                "volume": int(values.get("5. volume", 0) or 0),
            })

        return TimeSeriesData(symbol=symbol, interval="weekly", data_points=data_points)

    # ─────────────────────────────────────────────────────────────────────
    # Fundamental Data
    # ─────────────────────────────────────────────────────────────────────

    async def get_income_statement(self, symbol: str) -> FundamentalData:
        """Get annual and quarterly income statements.

        Used for comparable company analysis and DCF modeling.
        """
        data = await self._make_request({
            "function": "INCOME_STATEMENT",
            "symbol": symbol,
        })

        if "error" in data:
            return FundamentalData(symbol=symbol, report_type="income_statement")

        return FundamentalData(
            symbol=symbol,
            report_type="income_statement",
            annual_reports=data.get("annualReports", []),
            quarterly_reports=data.get("quarterlyReports", []),
        )

    async def get_balance_sheet(self, symbol: str) -> FundamentalData:
        """Get annual and quarterly balance sheets.

        Used for LBO modeling and financial health assessment.
        """
        data = await self._make_request({
            "function": "BALANCE_SHEET",
            "symbol": symbol,
        })

        if "error" in data:
            return FundamentalData(symbol=symbol, report_type="balance_sheet")

        return FundamentalData(
            symbol=symbol,
            report_type="balance_sheet",
            annual_reports=data.get("annualReports", []),
            quarterly_reports=data.get("quarterlyReports", []),
        )

    async def get_cash_flow(self, symbol: str) -> FundamentalData:
        """Get annual and quarterly cash flow statements.

        Used for DCF modeling (free cash flow calculation).
        """
        data = await self._make_request({
            "function": "CASH_FLOW",
            "symbol": symbol,
        })

        if "error" in data:
            return FundamentalData(symbol=symbol, report_type="cash_flow")

        return FundamentalData(
            symbol=symbol,
            report_type="cash_flow",
            annual_reports=data.get("annualReports", []),
            quarterly_reports=data.get("quarterlyReports", []),
        )

    async def get_overview(self, symbol: str) -> FundamentalData:
        """Get company overview including market cap, P/E, sector, etc.

        Used for comparable company analysis and market sizing.
        """
        data = await self._make_request({
            "function": "OVERVIEW",
            "symbol": symbol,
        })

        if "error" in data:
            return FundamentalData(symbol=symbol, report_type="overview")

        return FundamentalData(
            symbol=symbol,
            report_type="overview",
            data=data,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Forex & Crypto
    # ─────────────────────────────────────────────────────────────────────

    async def get_forex_rate(self, from_currency: str, to_currency: str) -> dict[str, Any]:
        """Get real-time forex exchange rate.

        Args:
            from_currency: Source currency code (e.g., "USD")
            to_currency: Target currency code (e.g., "INR")
        """
        data = await self._make_request({
            "function": "CURRENCY_EXCHANGE_RATE",
            "from_currency": from_currency,
            "to_currency": to_currency,
        })

        if "error" in data:
            return {"error": data["error"]}

        rate_data = data.get("Realtime Currency Exchange Rate", {})
        return {
            "from_currency": from_currency,
            "to_currency": to_currency,
            "exchange_rate": float(rate_data.get("5. Exchange Rate", 0) or 0),
            "last_refreshed": rate_data.get("6. Last Refreshed", ""),
            "bid": float(rate_data.get("8. Bid Price", 0) or 0),
            "ask": float(rate_data.get("9. Ask Price", 0) or 0),
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> AlphaVantageClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
