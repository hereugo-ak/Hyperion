"""
HYPERION Semantic Scholar Client — academic paper search and citation graphs.

Semantic Scholar is the premier academic research API. It provides:
- Paper search across 200M+ academic papers
- Citation graphs (papers that cite this, papers this cites)
- AI-generated TLDR summaries for papers
- Open access PDF links
- Fields of study classification
- Author information and affiliations
- Paper recommendations based on similarity

This is NOT a generic "search papers" wrapper. It:
- Uses Semantic Scholar's Graph API (api.semanticscholar.org/graph/v1)
- 100 req/5min without API key (3s delay); 1 req/sec with key
- Returns structured AcademicPaper data for TRL assessment and
  peer-reviewed evidence gathering
- Provides citation graph traversal for literature reviews
- Caches responses to minimize API calls
- Handles rate limit errors gracefully (429 responses)

Architecture reference: §5.1 — "Academic papers, citation graphs, TLDR
summaries. Free, 100 req/5min. Used for TRL assessment and peer-reviewed
evidence."

Tool selection logic (§5.2):
  Academic research task:
    1. Semantic Scholar (always — primary academic source) ← THIS
    2. OpenAlex (complementary — institution data, broader coverage)

Used by: Innovation Analyst (TRL assessment), Technology Analyst
(peer-reviewed tech evaluations), Market Analyst (market sizing
academic studies) (§5.1)
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
CACHE_TTL_SECONDS = 3600  # 1 hour — paper metadata doesn't change


@dataclass
class AcademicPaper:
    """A single academic paper from Semantic Scholar."""

    paper_id: str
    title: str = ""
    abstract: str = ""
    authors: list[dict[str, str]] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    citation_count: int = 0
    reference_count: int = 0
    influential_citation_count: int = 0
    tldr: str = ""
    open_access_pdf_url: str = ""
    fields_of_study: list[str] = field(default_factory=list)
    publication_types: list[str] = field(default_factory=list)
    publication_date: str = ""
    journal: dict[str, Any] = field(default_factory=dict)
    external_ids: dict[str, str] = field(default_factory=dict)
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "year": self.year,
            "venue": self.venue,
            "citation_count": self.citation_count,
            "reference_count": self.reference_count,
            "influential_citation_count": self.influential_citation_count,
            "tldr": self.tldr,
            "open_access_pdf_url": self.open_access_pdf_url,
            "fields_of_study": self.fields_of_study,
            "publication_types": self.publication_types,
            "publication_date": self.publication_date,
            "journal": self.journal,
            "external_ids": self.external_ids,
            "url": self.url,
        }


@dataclass
class CitationGraph:
    """Citation graph result — papers citing or cited by a target paper."""

    paper_id: str
    direction: str = ""  # "citations" or "references"
    papers: list[AcademicPaper] = field(default_factory=list)
    total: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "direction": self.direction,
            "papers": [p.to_dict() for p in self.papers],
            "total": self.total,
        }


class SemanticScholarClient:
    """Semantic Scholar academic research client.

    Provides access to 200M+ academic papers, citation graphs, TLDR
    summaries, and paper recommendations. Free, 100 req/5min without
    API key. (§5.1)

    Usage:
        client = SemanticScholarClient(settings=settings)

        # Search for papers
        papers = await client.search("transformer architecture", limit=10)
        for p in papers:
            print(f"{p.year}: {p.title} ({p.citation_count} citations)")

        # Get a specific paper
        paper = await client.get_paper("10.1038/nature14539")
        print(f"TLDR: {paper.tldr}")

        # Get citations (papers that cite this one)
        citations = await client.get_citations("10.1038/nature14539", limit=5)

        # Get references (papers this one cites)
        refs = await client.get_references("10.1038/nature14539", limit=5)

        # Get AI-generated TLDR
        tldr = await client.get_tldr("10.1038/nature14539")

        # Get recommendations
        recs = await client.recommend("10.1038/nature14539", limit=5)
    """

    BASE_URL = "https://api.semanticscholar.org/graph/v1"
    RECOMMEND_URL = "https://api.semanticscholar.org/recommendations/v1"

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 5  # Longer delay for rate limit recovery
    RATE_LIMIT_DELAY_NO_KEY = 3.0  # 100 req/5min = ~3s between calls
    RATE_LIMIT_DELAY_WITH_KEY = 1.0  # 1 req/sec with key

    # Default fields to request from the API
    DEFAULT_FIELDS = (
        "paperId,title,abstract,authors,year,venue,citationCount,"
        "referenceCount,influentialCitationCount,tldr,openAccessPdf,"
        "fieldsOfStudy,publicationTypes,publicationDate,journal,"
        "externalIds,url"
    )

    SEARCH_FIELDS = (
        "paperId,title,abstract,authors,year,venue,citationCount,"
        "referenceCount,influentialCitationCount,tldr,openAccessPdf,"
        "fieldsOfStudy,publicationTypes,publicationDate,journal,"
        "externalIds,url"
    )

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._api_key = ""
        if settings:
            self._api_key = getattr(settings, "semantic_scholar_api_key", "")
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_request_time: float = 0.0

    @property
    def _rate_limit_delay(self) -> float:
        """Rate limit delay based on whether we have an API key."""
        return self.RATE_LIMIT_DELAY_WITH_KEY if self._api_key else self.RATE_LIMIT_DELAY_NO_KEY

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            headers = {
                "Accept": "application/json",
                "Accept-Encoding": "gzip, deflate",
            }
            if self._api_key:
                headers["x-api-key"] = self._api_key
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers=headers,
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
        """Enforce rate limit (3s without key, 1s with key)."""
        elapsed = time.time() - self._last_request_time
        delay = self._rate_limit_delay
        if elapsed < delay:
            await asyncio.sleep(delay - elapsed)
        self._last_request_time = time.time()

    async def _make_request(
        self,
        endpoint: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make a rate-limited, cached request to Semantic Scholar."""
        cache_key = self._cache_key(endpoint, *sorted((params or {}).items()))
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()
        client = await self._get_client()

        url = f"{self.BASE_URL}/{endpoint}"

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(url, params=params)
                response.raise_for_status()
                data = response.json()

                self._set_cached(cache_key, data)
                return data

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    # Rate limited — wait longer and retry
                    logger.warning("Semantic Scholar rate limited (429). Waiting...")
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.RETRY_DELAY * 3)
                    return {"error": "Rate limited (429). Try again later."}
                logger.warning("Semantic Scholar HTTP error (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

            except (httpx.HTTPError, httpx.RequestError, ValueError) as e:
                logger.warning("Semantic Scholar request failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    async def _make_request_raw(
        self,
        url: str,
        params: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Make a request to a full URL (for recommendation endpoint)."""
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

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    logger.warning("Semantic Scholar rate limited (429). Waiting...")
                    if attempt < self.MAX_RETRIES - 1:
                        await asyncio.sleep(self.RETRY_DELAY * 3)
                    return {"error": "Rate limited (429). Try again later."}
                logger.warning("Semantic Scholar HTTP error (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

            except (httpx.HTTPError, httpx.RequestError, ValueError) as e:
                logger.warning("Semantic Scholar request failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    def _parse_paper(self, raw: dict[str, Any]) -> AcademicPaper:
        """Parse a raw paper dict from the API into an AcademicPaper."""
        # Parse authors
        authors: list[dict[str, str]] = []
        for author in raw.get("authors", []):
            authors.append({
                "name": author.get("name", ""),
                "authorId": author.get("authorId", ""),
            })

        # Parse TLDR
        tldr_text = ""
        tldr_data = raw.get("tldr")
        if tldr_data and isinstance(tldr_data, dict):
            tldr_text = tldr_data.get("text", "")

        # Parse open access PDF
        oa_pdf_url = ""
        oa_data = raw.get("openAccessPdf")
        if oa_data and isinstance(oa_data, dict):
            oa_pdf_url = oa_data.get("url", "")

        # Parse external IDs
        external_ids = raw.get("externalIds", {}) or {}

        # Parse journal
        journal = raw.get("journal", {}) or {}

        return AcademicPaper(
            paper_id=raw.get("paperId", ""),
            title=raw.get("title", ""),
            abstract=raw.get("abstract", "") or "",
            authors=authors,
            year=raw.get("year"),
            venue=raw.get("venue", "") or "",
            citation_count=raw.get("citationCount", 0) or 0,
            reference_count=raw.get("referenceCount", 0) or 0,
            influential_citation_count=raw.get("influentialCitationCount", 0) or 0,
            tldr=tldr_text,
            open_access_pdf_url=oa_pdf_url,
            fields_of_study=raw.get("fieldsOfStudy", []) or [],
            publication_types=raw.get("publicationTypes", []) or [],
            publication_date=raw.get("publicationDate", "") or "",
            journal=journal,
            external_ids=external_ids,
            url=raw.get("url", "") or "",
        )

    # ─────────────────────────────────────────────────────────────────────
    # Search
    # ─────────────────────────────────────────────────────────────────────

    async def search(
        self,
        query: str,
        limit: int = 10,
        fields: str = "",
        year_range: str = "",
        publication_types: str = "",
        open_access_only: bool = False,
    ) -> list[AcademicPaper]:
        """Search for academic papers by keyword query.

        Args:
            query: Search query (e.g., "transformer architecture attention mechanism")
            limit: Maximum number of results (max 100)
            fields: Comma-separated list of fields to return. Empty = default.
            year_range: Year filter (e.g., "2020-2024" or "2020-")
            publication_types: Filter by type (e.g., "JournalArticle,Conference")
            open_access_only: If True, only return papers with open access PDFs

        Returns:
            List of AcademicPaper objects matching the query.
        """
        if not query or not query.strip():
            return []

        params: dict[str, str] = {
            "query": query,
            "limit": str(min(limit, 100)),
            "fields": fields or self.SEARCH_FIELDS,
        }
        if year_range:
            params["year"] = year_range
        if publication_types:
            params["publicationTypes"] = publication_types
        if open_access_only:
            params["openAccessPdf"] = ""

        data = await self._make_request("paper/search", params=params)

        if "error" in data:
            return []

        papers: list[AcademicPaper] = []
        for raw_paper in data.get("data", []):
            papers.append(self._parse_paper(raw_paper))

        return papers

    async def search_bulk(
        self,
        query: str,
        limit: int = 100,
        fields: str = "",
        year_range: str = "",
    ) -> list[AcademicPaper]:
        """Bulk search — returns up to 1000 papers (uses search/bulk endpoint).

        Args:
            query: Search query
            limit: Maximum number of results (max 1000)
            fields: Comma-separated fields. Empty = default.
            year_range: Year filter (e.g., "2020-2024")

        Returns:
            List of AcademicPaper objects.
        """
        if not query or not query.strip():
            return []

        params: dict[str, str] = {
            "query": query,
            "limit": str(min(limit, 1000)),
            "fields": fields or self.SEARCH_FIELDS,
        }
        if year_range:
            params["year"] = year_range

        data = await self._make_request("paper/search/bulk", params=params)

        if "error" in data:
            return []

        papers: list[AcademicPaper] = []
        for raw_paper in data.get("data", []):
            papers.append(self._parse_paper(raw_paper))

        return papers

    # ─────────────────────────────────────────────────────────────────────
    # Paper Details
    # ─────────────────────────────────────────────────────────────────────

    async def get_paper(self, paper_id: str) -> AcademicPaper | None:
        """Get details for a specific paper.

        Args:
            paper_id: Semantic Scholar paper ID, DOI (prefix with "DOI:"),
                      ArXiv ID (prefix with "ARXIV:"), or PubMed ID (prefix with "PMID:")

        Returns:
            AcademicPaper object, or None if not found.
        """
        if not paper_id:
            return None

        params = {"fields": self.DEFAULT_FIELDS}
        data = await self._make_request(f"paper/{paper_id}", params=params)

        if "error" in data:
            return None

        return self._parse_paper(data)

    async def get_tldr(self, paper_id: str) -> str:
        """Get the AI-generated TLDR summary for a paper.

        Args:
            paper_id: Semantic Scholar paper ID or DOI

        Returns:
            TLDR text, or empty string if not available.
        """
        if not paper_id:
            return ""

        params = {"fields": "tldr"}
        data = await self._make_request(f"paper/{paper_id}", params=params)

        if "error" in data:
            return ""

        tldr_data = data.get("tldr")
        if tldr_data and isinstance(tldr_data, dict):
            return tldr_data.get("text", "")

        return ""

    # ─────────────────────────────────────────────────────────────────────
    # Citation Graph
    # ─────────────────────────────────────────────────────────────────────

    async def get_citations(
        self,
        paper_id: str,
        limit: int = 20,
        fields: str = "",
    ) -> CitationGraph:
        """Get papers that cite a given paper (incoming citations).

        Args:
            paper_id: Semantic Scholar paper ID or DOI
            limit: Maximum number of citations to return
            fields: Comma-separated fields to return. Empty = default.

        Returns:
            CitationGraph with citing papers.
        """
        if not paper_id:
            return CitationGraph(paper_id=paper_id, direction="citations")

        params = {
            "limit": str(min(limit, 100)),
            "fields": fields or self.SEARCH_FIELDS,
        }
        data = await self._make_request(f"paper/{paper_id}/citations", params=params)

        if "error" in data:
            return CitationGraph(paper_id=paper_id, direction="citations")

        papers: list[AcademicPaper] = []
        for entry in data.get("data", []):
            # Each entry has a "citingPaper" key
            raw_paper = entry.get("citingPaper", {})
            if raw_paper:
                papers.append(self._parse_paper(raw_paper))

        return CitationGraph(
            paper_id=paper_id,
            direction="citations",
            papers=papers,
            total=len(papers),
        )

    async def get_references(
        self,
        paper_id: str,
        limit: int = 20,
        fields: str = "",
    ) -> CitationGraph:
        """Get papers that a given paper cites (outgoing references).

        Args:
            paper_id: Semantic Scholar paper ID or DOI
            limit: Maximum number of references to return
            fields: Comma-separated fields to return. Empty = default.

        Returns:
            CitationGraph with referenced papers.
        """
        if not paper_id:
            return CitationGraph(paper_id=paper_id, direction="references")

        params = {
            "limit": str(min(limit, 100)),
            "fields": fields or self.SEARCH_FIELDS,
        }
        data = await self._make_request(f"paper/{paper_id}/references", params=params)

        if "error" in data:
            return CitationGraph(paper_id=paper_id, direction="references")

        papers: list[AcademicPaper] = []
        for entry in data.get("data", []):
            # Each entry has a "citedPaper" key
            raw_paper = entry.get("citedPaper", {})
            if raw_paper:
                papers.append(self._parse_paper(raw_paper))

        return CitationGraph(
            paper_id=paper_id,
            direction="references",
            papers=papers,
            total=len(papers),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Recommendations
    # ─────────────────────────────────────────────────────────────────────

    async def recommend(
        self,
        paper_id: str,
        limit: int = 10,
        fields: str = "",
    ) -> list[AcademicPaper]:
        """Get paper recommendations based on a single paper.

        Uses Semantic Scholar's recommendation engine to find similar papers.

        Args:
            paper_id: Semantic Scholar paper ID or DOI
            limit: Maximum number of recommendations
            fields: Comma-separated fields to return. Empty = default.

        Returns:
            List of recommended AcademicPaper objects.
        """
        if not paper_id:
            return []

        params = {
            "limit": str(min(limit, 100)),
            "fields": fields or self.SEARCH_FIELDS,
        }

        url = f"{self.RECOMMEND_URL}/papers/forpaper/{paper_id}"
        data = await self._make_request_raw(url, params=params)

        if "error" in data:
            return []

        papers: list[AcademicPaper] = []
        for raw_paper in data.get("recommendedPapers", []):
            papers.append(self._parse_paper(raw_paper))

        return papers

    async def recommend_multiple(
        self,
        paper_ids: list[str],
        limit: int = 10,
        fields: str = "",
    ) -> list[AcademicPaper]:
        """Get recommendations based on multiple papers.

        Args:
            paper_ids: List of Semantic Scholar paper IDs
            limit: Maximum number of recommendations
            fields: Comma-separated fields to return. Empty = default.

        Returns:
            List of recommended AcademicPaper objects.
        """
        if not paper_ids:
            return []

        params = {
            "limit": str(min(limit, 100)),
            "fields": fields or self.SEARCH_FIELDS,
        }

        url = f"{self.RECOMMEND_URL}/papers/"
        data = await self._make_request_raw(url, params=params)

        if "error" in data:
            return []

        papers: list[AcademicPaper] = []
        for raw_paper in data.get("recommendedPapers", []):
            papers.append(self._parse_paper(raw_paper))

        return papers

    # ─────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────

    async def search_recent(
        self,
        query: str,
        limit: int = 10,
        years_back: int = 3,
    ) -> list[AcademicPaper]:
        """Search for recent papers within the last N years.

        Convenience method for finding state-of-the-art research.

        Args:
            query: Search query
            limit: Maximum number of results
            years_back: How many years back to search (default: 3)

        Returns:
            List of recent AcademicPaper objects.
        """
        import datetime
        current_year = datetime.datetime.now().year
        year_range = f"{current_year - years_back}-{current_year}"
        return await self.search(query, limit=limit, year_range=year_range)

    async def search_highly_cited(
        self,
        query: str,
        limit: int = 10,
        min_citations: int = 50,
    ) -> list[AcademicPaper]:
        """Search for highly-cited papers on a topic.

        Convenience method for finding influential research.

        Args:
            query: Search query
            limit: Maximum number of results
            min_citations: Minimum citation count threshold

        Returns:
            List of highly-cited AcademicPaper objects.
        """
        papers = await self.search(query, limit=limit * 3)
        return [p for p in papers if p.citation_count >= min_citations][:limit]

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> SemanticScholarClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
