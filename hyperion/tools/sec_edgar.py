"""
HYPERION SEC EDGAR Client — US public company regulatory filings.

SEC EDGAR is the authoritative source for all US public company filings.
It provides structured access to:
- 10-K (annual reports), 10-Q (quarterly reports), 8-K (current events)
- S-1 (IPO/registration), DEF 14A (proxy statements), 20-F (foreign filers)
- XBRL financial statement data (structured, machine-readable)
- Full-text search across all filings
- CIK lookup (company name / ticker → CIK number)

This is NOT a generic "fetch SEC data" wrapper. It:
- Uses SEC EDGAR's REST API (data.sec.gov + efts.sec.gov) with proper
  User-Agent header (required by SEC fair access policy)
- 10 req/sec rate limit (100ms delay between calls)
- Returns structured data for financial analysis and due diligence
- Provides XBRL structured financials (more granular than Alpha Vantage)
- Caches responses to minimize API calls
- Handles rate limit errors gracefully

Architecture reference: §5.1 — "SEC filings, 10-K/10-Q/8-K, CIK lookup,
full-text search. Free, unlimited. Used for financial analysis and
due diligence."

Tool selection logic (§5.2):
  Financial filings task:
    1. SEC EDGAR (always — it's the only SEC filings source) ← THIS

Used by: Financial Analyst (10-K/10-Q for DCF), M&A Analyst (due diligence),
Competitive Intel (competitor financials from filings) (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 15000
CACHE_TTL_SECONDS = 3600  # 1 hour — filings don't change after submission


@dataclass
class SECFiling:
    """A single SEC filing entry."""

    accession_number: str
    filing_type: str
    company_name: str
    cik: str
    filing_date: str
    report_date: str
    url: str
    document_url: str
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession_number": self.accession_number,
            "filing_type": self.filing_type,
            "company_name": self.company_name,
            "cik": self.cik,
            "filing_date": self.filing_date,
            "report_date": self.report_date,
            "url": self.url,
            "document_url": self.document_url,
            "description": self.description,
        }


@dataclass
class SECFilingContent:
    """Full text content of a filing, truncated to MAX_CONTENT_CHARS."""

    accession_number: str
    filing_type: str
    company_name: str
    cik: str
    content: str = ""
    url: str = ""
    truncated: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "accession_number": self.accession_number,
            "filing_type": self.filing_type,
            "company_name": self.company_name,
            "cik": self.cik,
            "content": self.content,
            "url": self.url,
            "truncated": self.truncated,
        }


@dataclass
class SECCompanyInfo:
    """Company metadata from SEC EDGAR submissions API."""

    cik: str
    name: str = ""
    ticker: str = ""
    sic: str = ""  # Standard Industrial Classification code
    sic_description: str = ""
    fiscal_year_end: str = ""
    state_of_incorporation: str = ""
    addresses: dict[str, Any] = field(default_factory=dict)
    phone: str = ""
    website: str = ""
    former_names: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "cik": self.cik,
            "name": self.name,
            "ticker": self.ticker,
            "sic": self.sic,
            "sic_description": self.sic_description,
            "fiscal_year_end": self.fiscal_year_end,
            "state_of_incorporation": self.state_of_incorporation,
            "addresses": self.addresses,
            "phone": self.phone,
            "website": self.website,
            "former_names": self.former_names,
        }


class SECEdgarClient:
    """SEC EDGAR regulatory filings client.

    Provides access to all US public company filings (10-K, 10-Q, 8-K, etc.),
    XBRL structured financial data, CIK lookup, and full-text search.
    Free, unlimited — requires User-Agent header with contact email.
    (§5.1)

    Usage:
        client = SECEdgarClient(settings=settings)

        # Look up a company's CIK
        cik = await client.get_cik("Apple")
        # Returns "0000320193"

        # Get company info
        info = await client.get_company_info("0000320193")
        print(f"{info.name} ({info.ticker}) — SIC: {info.sic_description}")

        # Get recent 10-K filings
        filings = await client.get_filings("0000320193", filing_type="10-K", limit=5)
        for f in filings:
            print(f"{f.filing_date}: {f.filing_type} — {f.accession_number}")

        # Get full text of most recent 10-K
        if filings:
            content = await client.get_filing_content(filings[0])
            print(content.content[:500])

        # Full-text search across all filings
        results = await client.search_full_text("artificial intelligence", filing_type="10-K")
        for r in results:
            print(f"{r.company_name}: {r.filing_type} ({r.filing_date})")

        # Get XBRL financial statements
        financials = await client.get_financial_statements("0000320193", "Revenues")
        # Returns structured dict with XBRL data
    """

    BASE_URL = "https://data.sec.gov"
    SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
    ARCHIVES_URL = "https://www.sec.gov/Archives/edgar/data"
    TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
    SUBMISSIONS_URL = "https://data.sec.gov/submissions"
    COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts"
    COMPANYCONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept"

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 2
    RATE_LIMIT_DELAY = 0.1  # 100ms = 10 req/sec

    # Common filing types for quick access
    FILING_10K = "10-K"
    FILING_10Q = "10-Q"
    FILING_8K = "8-K"
    FILING_S1 = "S-1"
    FILING_DEF14A = "DEF 14A"
    FILING_20F = "20-F"

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._user_agent = "HYPERION Research hyperion-research@example.com"
        if settings:
            email = getattr(settings, "sec_contact_email", "")
            if email:
                self._user_agent = f"HYPERION Research {email}"
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_request_time: float = 0.0
        self._tickers_cache: dict[str, dict[str, Any]] | None = None
        self._tickers_cache_time: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers={
                    "User-Agent": self._user_agent,
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
        url: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make a rate-limited, cached request to SEC EDGAR."""
        cache_key = self._cache_key(url, *sorted((params or {}).items()))
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()
        client = await self._get_client()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

                self._set_cached(cache_key, data)
                return data

            except (httpx.HTTPError, httpx.RequestError, ValueError) as e:
                logger.warning("SEC EDGAR request failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    async def _make_text_request(
        self,
        url: str,
    ) -> str:
        """Make a rate-limited request expecting text/HTML content."""
        cache_key = self._cache_key(url)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()
        client = await self._get_client()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(url)
                response.raise_for_status()
                text = response.text

                self._set_cached(cache_key, text)
                return text

            except (httpx.HTTPError, httpx.RequestError) as e:
                logger.warning("SEC EDGAR text request failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return ""

        return ""

    # ─────────────────────────────────────────────────────────────────────
    # CIK Lookup
    # ─────────────────────────────────────────────────────────────────────

    async def _load_tickers(self) -> dict[str, dict[str, Any]]:
        """Load the SEC tickers JSON (maps ticker → CIK + company name).

        Cached for 24 hours — this file rarely changes.
        """
        if self._tickers_cache is not None and time.time() - self._tickers_cache_time < 86400:
            return self._tickers_cache

        data = await self._make_request(self.TICKERS_URL)
        if "error" in data:
            return {}

        # SEC returns {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
        tickers: dict[str, dict[str, Any]] = {}
        for entry in data.values():
            if isinstance(entry, dict):
                ticker = entry.get("ticker", "").upper()
                cik_str = str(entry.get("cik_str", "")).zfill(10)
                title = entry.get("title", "")
                if ticker:
                    tickers[ticker] = {
                        "cik": cik_str,
                        "ticker": ticker,
                        "title": title,
                    }

        self._tickers_cache = tickers
        self._tickers_cache_time = time.time()
        return tickers

    async def get_cik(self, company_name_or_ticker: str) -> str | None:
        """Look up a company's CIK number by name or ticker.

        Args:
            company_name_or_ticker: Company name (e.g., "Apple") or ticker (e.g., "AAPL")

        Returns:
            CIK number as a zero-padded 10-digit string, or None if not found.
        """
        query = company_name_or_ticker.strip()
        if not query:
            return None

        # Try ticker match first (exact, case-insensitive)
        tickers = await self._load_tickers()
        query_upper = query.upper()
        if query_upper in tickers:
            return tickers[query_upper]["cik"]

        # Try name match (case-insensitive substring)
        query_lower = query.lower()
        for entry in tickers.values():
            if query_lower in entry.get("title", "").lower():
                return entry["cik"]

        return None

    # ─────────────────────────────────────────────────────────────────────
    # Company Info
    # ─────────────────────────────────────────────────────────────────────

    async def get_company_info(self, cik: str) -> SECCompanyInfo:
        """Get company metadata from SEC submissions API.

        Args:
            cik: CIK number (zero-padded, e.g., "0000320193")

        Returns:
            SECCompanyInfo with name, ticker, SIC, fiscal year, etc.
        """
        cik_padded = cik.zfill(10)
        url = f"{self.SUBMISSIONS_URL}/CIK{cik_padded}.json"
        data = await self._make_request(url)

        if "error" in data:
            return SECCompanyInfo(cik=cik_padded)

        # Parse the submissions response
        entity = data.get("entity", {})
        name = entity.get("name", "")
        tickers_list = entity.get("tickers", [])
        ticker = tickers_list[0] if tickers_list else ""
        sic = entity.get("sic", "")
        sic_description = entity.get("sicDescription", "")
        fiscal_year_end = entity.get("fiscalYearEnd", "")
        state = entity.get("stateOfIncorporation", "")
        addresses = entity.get("addresses", {})
        phone = entity.get("phone", "")
        former_names = entity.get("formerNames", [])

        # Extract website from addresses
        website = ""
        if isinstance(addresses, dict):
            business_addr = addresses.get("business", {})
            if isinstance(business_addr, dict):
                website = business_addr.get("website", "")

        return SECCompanyInfo(
            cik=cik_padded,
            name=name,
            ticker=ticker,
            sic=sic,
            sic_description=sic_description,
            fiscal_year_end=fiscal_year_end,
            state_of_incorporation=state,
            addresses=addresses,
            phone=phone,
            website=website,
            former_names=former_names,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Filings Listing
    # ─────────────────────────────────────────────────────────────────────

    async def get_filings(
        self,
        cik: str,
        filing_type: str = "",
        start_date: str = "",
        end_date: str = "",
        limit: int = 20,
    ) -> list[SECFiling]:
        """Get filings for a company, optionally filtered by type and date.

        Args:
            cik: CIK number (zero-padded, e.g., "0000320193")
            filing_type: Filing type filter (e.g., "10-K", "10-Q", "8-K").
                         Empty = all types.
            start_date: Start date filter (YYYY-MM-DD). Empty = no filter.
            end_date: End date filter (YYYY-MM-DD). Empty = no filter.
            limit: Maximum number of filings to return.

        Returns:
            List of SECFiling objects, most recent first.
        """
        cik_padded = cik.zfill(10)
        url = f"{self.SUBMISSIONS_URL}/CIK{cik_padded}.json"
        data = await self._make_request(url)

        if "error" in data:
            return []

        # The submissions API returns recent filings in 'recent' and
        # older filings in 'files' (which require separate fetches)
        recent = data.get("recent", {})
        all_filings: list[SECFiling] = []

        # Parse recent filings
        form_types = recent.get("form", [])
        accession_numbers = recent.get("accessionNumber", [])
        filing_dates = recent.get("filingDate", [])
        report_dates = recent.get("reportDate", [])
        primary_docs = recent.get("primaryDocument", [])
        primary_descs = recent.get("primaryDocDescription", [])

        for i in range(len(form_types)):
            # Apply filters
            if filing_type and form_types[i] != filing_type:
                continue
            if start_date and filing_dates[i] < start_date:
                continue
            if end_date and filing_dates[i] > end_date:
                continue

            accession = accession_numbers[i] if i < len(accession_numbers) else ""
            accession_no_dashes = accession.replace("-", "")

            # Construct filing URL
            filing_url = (
                f"{self.ARCHIVES_URL}/{int(cik_padded)}/{accession_no_dashes}/"
                f"{primary_docs[i] if i < len(primary_docs) else 'index.htm'}"
            )

            # Filing index page URL
            index_url = (
                f"https://www.sec.gov/cgi-bin/browseEdgar?"
                f"action=getcompany&CIK={cik_padded}&type={form_types[i]}"
                f"&dateb=&owner=include&count={limit}"
            )

            all_filings.append(SECFiling(
                accession_number=accession,
                filing_type=form_types[i],
                company_name=data.get("entity", {}).get("name", ""),
                cik=cik_padded,
                filing_date=filing_dates[i] if i < len(filing_dates) else "",
                report_date=report_dates[i] if i < len(report_dates) else "",
                url=index_url,
                document_url=filing_url,
                description=primary_descs[i] if i < len(primary_descs) else "",
            ))

            if len(all_filings) >= limit:
                break

        return all_filings

    # ─────────────────────────────────────────────────────────────────────
    # Filing Content
    # ─────────────────────────────────────────────────────────────────────

    async def get_filing_content(self, filing: SECFiling) -> SECFilingContent:
        """Fetch the full text content of a filing, truncated to 15K chars.

        Args:
            filing: SECFiling object (from get_filings or search_full_text)

        Returns:
            SECFilingContent with the filing's text content.
        """
        if not filing.document_url:
            return SECFilingContent(
                accession_number=filing.accession_number,
                filing_type=filing.filing_type,
                company_name=filing.company_name,
                cik=filing.cik,
            )

        # Fetch the filing document
        text = await self._make_text_request(filing.document_url)

        if not text:
            return SECFilingContent(
                accession_number=filing.accession_number,
                filing_type=filing.filing_type,
                company_name=filing.company_name,
                cik=filing.cik,
                url=filing.document_url,
            )

        # Strip HTML tags for plain text
        clean_text = re.sub(r"<[^>]+>", " ", text)
        clean_text = re.sub(r"\s+", " ", clean_text).strip()

        truncated = len(clean_text) > MAX_CONTENT_CHARS
        content = clean_text[:MAX_CONTENT_CHARS]

        return SECFilingContent(
            accession_number=filing.accession_number,
            filing_type=filing.filing_type,
            company_name=filing.company_name,
            cik=filing.cik,
            content=content,
            url=filing.document_url,
            truncated=truncated,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Full-Text Search
    # ─────────────────────────────────────────────────────────────────────

    async def search_full_text(
        self,
        query: str,
        filing_type: str = "",
        limit: int = 20,
    ) -> list[SECFiling]:
        """Search across all SEC filings using full-text search.

        Uses the EDGAR full-text search system (efts.sec.gov).

        Args:
            query: Search query (e.g., "artificial intelligence revenue")
            filing_type: Filter by filing type (e.g., "10-K"). Empty = all.
            limit: Maximum number of results.

        Returns:
            List of SECFiling objects matching the query.
        """
        if not query or not query.strip():
            return []

        params: dict[str, str] = {
            "q": query,
            "dateRange": "custom",
            "startdt": "2020-01-01",
            "enddt": "2025-12-31",
        }
        if filing_type:
            params["forms"] = filing_type

        # Use the EDGAR full-text search API
        search_url = "https://efts.sec.gov/LATEST/search-index"
        data = await self._make_request(search_url, params=params)

        if "error" in data:
            # Try alternate search endpoint
            search_url = "https://efts.sec.gov/LATEST/search-index?"
            data = await self._make_request(
                "https://efts.sec.gov/LATEST/search-index",
                params=params,
            )
            if "error" in data:
                return []

        # Parse search results
        hits = data.get("hits", {}).get("hits", [])
        filings: list[SECFiling] = []

        for hit in hits[:limit]:
            source = hit.get("_source", {})
            accession = source.get("accession_no", "").replace("-", "")
            cik = str(source.get("entity_id", "")).zfill(10)

            # Construct document URL
            primary_doc = source.get("file_type", "")
            doc_url = (
                f"{self.ARCHIVES_URL}/{int(cik)}/{accession}/"
                f"{primary_doc}" if accession and cik else ""
            )

            filings.append(SECFiling(
                accession_number=source.get("accession_no", ""),
                filing_type=source.get("form", ""),
                company_name=source.get("entity_name", ""),
                cik=cik,
                filing_date=source.get("file_date", ""),
                report_date=source.get("period_ending", ""),
                url=f"https://www.sec.gov/cgi-bin/browseEdgar?CIK={cik}",
                document_url=doc_url,
                description=source.get("file_description", ""),
            ))

        return filings

    # ─────────────────────────────────────────────────────────────────────
    # XBRL Financial Statements
    # ─────────────────────────────────────────────────────────────────────

    async def get_financial_statements(
        self,
        cik: str,
        concept: str = "Revenues",
    ) -> dict[str, Any]:
        """Get XBRL structured financial data for a company.

        Uses the SEC's XBRL API to retrieve structured financial data
        by concept (e.g., Revenues, NetIncomeLoss, Assets, Liabilities).

        Args:
            cik: CIK number (zero-padded, e.g., "0000320193")
            concept: XBRL concept name (e.g., "Revenues", "NetIncomeLoss",
                     "Assets", "Liabilities", "CashAndCashEquivalentsAtCarryingValue")

        Returns:
            Dict with XBRL data units, labels, and descriptions.
        """
        cik_padded = cik.zfill(10)
        url = (
            f"{self.COMPANYCONCEPT_URL}/CIK{cik_padded}"
            f"/us-gaap/{concept}.json"
        )
        data = await self._make_request(url)

        if "error" in data:
            return {"error": data["error"]}

        # The XBRL API returns data organized by units
        units = data.get("units", {})
        label = data.get("label", "")
        description = data.get("description", "")
        entity_name = data.get("entityName", "")
        taxonomy = data.get("taxonomy", "")

        # Extract the most recent data points
        data_points: list[dict[str, Any]] = []
        for unit_type, entries in units.items():
            if isinstance(entries, list):
                for entry in entries:
                    data_points.append({
                        "unit": unit_type,
                        "end_date": entry.get("end", ""),
                        "start_date": entry.get("start", ""),
                        "value": entry.get("val"),
                        "form": entry.get("form", ""),
                        "filed": entry.get("filed", ""),
                        "frame": entry.get("frame", ""),
                    })

        # Sort by end date descending
        data_points.sort(key=lambda x: x.get("end_date", ""), reverse=True)

        return {
            "cik": cik_padded,
            "concept": concept,
            "label": label,
            "description": description,
            "entity_name": entity_name,
            "taxonomy": taxonomy,
            "data_points": data_points[:50],  # Limit to 50 most recent
            "total_data_points": len(data_points),
        }

    async def get_company_facts(self, cik: str) -> dict[str, Any]:
        """Get all XBRL facts for a company (complete financial profile).

        This is a large response — use get_financial_statements for specific concepts.

        Args:
            cik: CIK number (zero-padded)

        Returns:
            Dict with all XBRL facts organized by taxonomy and concept.
        """
        cik_padded = cik.zfill(10)
        url = f"{self.COMPANYFACTS_URL}/CIK{cik_padded}.json"
        data = await self._make_request(url)

        if "error" in data:
            return {"error": data["error"]}

        return {
            "cik": cik_padded,
            "entity_name": data.get("entityName", ""),
            "facts": data.get("facts", {}),
        }

    # ─────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────

    async def get_latest_10k(self, cik: str) -> SECFiling | None:
        """Get the most recent 10-K (annual report) for a company.

        Args:
            cik: CIK number

        Returns:
            SECFiling for the latest 10-K, or None if not found.
        """
        filings = await self.get_filings(cik, filing_type="10-K", limit=1)
        return filings[0] if filings else None

    async def get_latest_10q(self, cik: str) -> SECFiling | None:
        """Get the most recent 10-Q (quarterly report) for a company.

        Args:
            cik: CIK number

        Returns:
            SECFiling for the latest 10-Q, or None if not found.
        """
        filings = await self.get_filings(cik, filing_type="10-Q", limit=1)
        return filings[0] if filings else None

    async def get_revenue_data(self, cik: str) -> dict[str, Any]:
        """Get revenue (Revenues) XBRL data for a company.

        Convenience method for DCF modeling — revenue is the primary
        input for financial projections.

        Args:
            cik: CIK number

        Returns:
            Dict with revenue data points from XBRL.
        """
        return await self.get_financial_statements(cik, "Revenues")

    async def get_net_income(self, cik: str) -> dict[str, Any]:
        """Get net income (NetIncomeLoss) XBRL data for a company.

        Args:
            cik: CIK number

        Returns:
            Dict with net income data points from XBRL.
        """
        return await self.get_financial_statements(cik, "NetIncomeLoss")

    async def get_assets(self, cik: str) -> dict[str, Any]:
        """Get total assets (Assets) XBRL data for a company.

        Args:
            cik: CIK number

        Returns:
            Dict with asset data points from XBRL.
        """
        return await self.get_financial_statements(cik, "Assets")

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> SECEdgarClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
