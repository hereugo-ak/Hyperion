"""
HYPERION OpenAlex Client — open scholarly metadata catalog.

OpenAlex is the largest open catalog of scholarly works, institutions,
and concepts. It provides:
- 250M+ works (papers, books, datasets) with metadata
- Institution data (R&D spending, faculty counts, country)
- Concept/topic classification and search
- Citation counts and referenced works
- Abstracts stored as inverted indices (reconstructed on fetch)
- Author affiliations and ORCID IDs
- Open data — no API key, polite pool with email in User-Agent

This is NOT a generic "search academic papers" wrapper. It:
- Uses OpenAlex's REST API (api.openalex.org) with polite User-Agent
- 10 req/sec recommended rate limit (100ms delay)
- Reconstructs abstracts from inverted indices
- Returns structured OpenAlexWork data for innovation assessment
- Complements Semantic Scholar with institution-level data
- Caches responses to minimize API calls
- Handles missing abstracts gracefully (not all works have them)

Architecture reference: §5.1 — "Open scholarly metadata, institution
data, concept search. Free, unlimited. Complements Semantic Scholar
with broader coverage and institution-level R&D data."

Tool selection logic (§5.2):
  Academic research task:
    1. Semantic Scholar (primary — citation graphs, TLDR)
    2. OpenAlex (complementary — institution data, broader coverage) ← THIS

Used by: Innovation Analyst (TRL + institution R&D), Research Librarian
(source credibility scoring for academic sources) (§5.1)
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
CACHE_TTL_SECONDS = 3600  # 1 hour — scholarly metadata rarely changes


@dataclass
class OpenAlexWork:
    """A scholarly work from OpenAlex."""

    work_id: str
    title: str = ""
    abstract: str = ""
    authors: list[dict[str, str]] = field(default_factory=list)
    institutions: list[dict[str, str]] = field(default_factory=list)
    year: int | None = None
    venue: str = ""
    cited_by_count: int = 0
    referenced_works_count: int = 0
    doi: str = ""
    concepts: list[dict[str, Any]] = field(default_factory=list)
    publication_date: str = ""
    type: str = ""  # article, book-chapter, dataset, etc.
    open_access: dict[str, Any] = field(default_factory=dict)
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "title": self.title,
            "abstract": self.abstract,
            "authors": self.authors,
            "institutions": self.institutions,
            "year": self.year,
            "venue": self.venue,
            "cited_by_count": self.cited_by_count,
            "referenced_works_count": self.referenced_works_count,
            "doi": self.doi,
            "concepts": self.concepts,
            "publication_date": self.publication_date,
            "type": self.type,
            "open_access": self.open_access,
            "url": self.url,
        }


@dataclass
class OpenAlexInstitution:
    """An academic/research institution from OpenAlex."""

    institution_id: str
    name: str = ""
    country: str = ""
    country_code: str = ""
    ror_id: str = ""
    type: str = ""  # education, facility, company, etc.
    works_count: int = 0
    cited_by_count: int = 0
    homepage_url: str = ""
    latitude: float | None = None
    longitude: float | None = None
    display_name_alternatives: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "institution_id": self.institution_id,
            "name": self.name,
            "country": self.country,
            "country_code": self.country_code,
            "ror_id": self.ror_id,
            "type": self.type,
            "works_count": self.works_count,
            "cited_by_count": self.cited_by_count,
            "homepage_url": self.homepage_url,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "display_name_alternatives": self.display_name_alternatives,
        }


class OpenAlexClient:
    """OpenAlex scholarly metadata client.

    Provides access to 250M+ scholarly works, institutions, and concepts.
    Free, unlimited — email in User-Agent for polite pool. (§5.1)

    Usage:
        client = OpenAlexClient(settings=settings)

        # Search for works
        works = await client.search_works("large language models", limit=10)
        for w in works:
            print(f"{w.year}: {w.title} ({w.cited_by_count} citations)")

        # Get a specific work
        work = await client.get_work("W2741809807")
        print(f"Abstract: {work.abstract[:200]}")

        # Search by concept
        papers = await client.search_by_concept("artificial intelligence", limit=5)

        # Get institution data
        inst = await client.get_institution("I136199984")  # MIT
        print(f"{inst.name} ({inst.country}) — {inst.works_count} works")

        # Get citing works
        citations = await client.get_citations("W2741809807", limit=5)
    """

    BASE_URL = "https://api.openalex.org"

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 2
    RATE_LIMIT_DELAY = 0.1  # 100ms = 10 req/sec recommended

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._email = "hyperion-research@example.com"
        if settings:
            email = getattr(settings, "openalex_email", "")
            if email:
                self._email = email
        self._client: httpx.AsyncClient | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_request_time: float = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers={
                    "User-Agent": f"HYPERION Research mailto:{self._email}",
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
        """Make a rate-limited, cached request to OpenAlex."""
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

            except (httpx.HTTPError, httpx.RequestError, ValueError) as e:
                logger.warning("OpenAlex request failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    def _reconstruct_abstract(self, inverted_index: dict[str, list[int]] | None) -> str:
        """Reconstruct abstract from OpenAlex inverted index format.

        OpenAlex stores abstracts as inverted indices: a dict mapping
        each word to a list of positions. This method reconstructs the
        original text by placing words at their positions.

        Args:
            inverted_index: Dict like {"word": [0, 5], "another": [1], ...}

        Returns:
            Reconstructed abstract string, or empty string if no abstract.
        """
        if not inverted_index or not isinstance(inverted_index, dict):
            return ""

        # Build position → word mapping
        positions: dict[int, str] = {}
        for word, pos_list in inverted_index.items():
            for pos in pos_list:
                positions[pos] = word

        if not positions:
            return ""

        # Reconstruct by sorting positions
        max_pos = max(positions.keys())
        words: list[str] = []
        for i in range(max_pos + 1):
            words.append(positions.get(i, ""))

        abstract = " ".join(words)
        # Clean up multiple spaces from missing positions
        import re
        abstract = re.sub(r"\s+", " ", abstract).strip()

        return abstract[:MAX_CONTENT_CHARS]

    def _parse_work(self, raw: dict[str, Any]) -> OpenAlexWork:
        """Parse a raw work dict from the API into an OpenAlexWork."""
        # Parse authors
        authors: list[dict[str, str]] = []
        for authorship in raw.get("authorships", []):
            author = authorship.get("author", {})
            authors.append({
                "name": author.get("display_name", ""),
                "orcid": author.get("orcid", ""),
                "id": author.get("id", ""),
            })

        # Parse institutions from authorships
        institutions: list[dict[str, str]] = []
        for authorship in raw.get("authorships", []):
            for inst in authorship.get("institutions", []):
                institutions.append({
                    "name": inst.get("display_name", ""),
                    "country": inst.get("country_code", ""),
                    "type": inst.get("type", ""),
                    "id": inst.get("id", ""),
                })

        # Parse concepts
        concepts: list[dict[str, Any]] = []
        for concept in raw.get("concepts", []):
            concepts.append({
                "name": concept.get("display_name", ""),
                "level": concept.get("level", 0),
                "score": concept.get("score", 0.0),
                "id": concept.get("id", ""),
            })

        # Reconstruct abstract from inverted index
        abstract = self._reconstruct_abstract(raw.get("abstract_inverted_index"))

        # Parse venue
        venue = ""
        host_venue = raw.get("primary_location", {})
        if host_venue:
            source = host_venue.get("source", {})
            if source:
                venue = source.get("display_name", "")

        # Parse open access
        open_access = raw.get("open_access", {}) or {}

        # Parse DOI
        doi = raw.get("doi", "") or ""

        return OpenAlexWork(
            work_id=raw.get("id", "").replace("https://openalex.org/", ""),
            title=raw.get("title", "") or raw.get("display_name", ""),
            abstract=abstract,
            authors=authors,
            institutions=institutions,
            year=raw.get("publication_year"),
            venue=venue,
            cited_by_count=raw.get("cited_by_count", 0) or 0,
            referenced_works_count=len(raw.get("referenced_works", []) or []),
            doi=doi,
            concepts=concepts,
            publication_date=raw.get("publication_date", "") or "",
            type=raw.get("type", "") or "",
            open_access=open_access,
            url=raw.get("id", ""),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Works Search
    # ─────────────────────────────────────────────────────────────────────

    async def search_works(
        self,
        query: str,
        limit: int = 10,
        sort: str = "relevance_score:desc",
        filter_str: str = "",
    ) -> list[OpenAlexWork]:
        """Search for scholarly works by keyword query.

        Args:
            query: Search query (e.g., "large language models reasoning")
            limit: Maximum number of results (max 200 per page)
            sort: Sort order (e.g., "relevance_score:desc", "cited_by_count:desc")
            filter_str: OpenAlex filter string (e.g., "publication_year:2023-2024")

        Returns:
            List of OpenAlexWork objects matching the query.
        """
        if not query or not query.strip():
            return []

        params: dict[str, str] = {
            "search": query,
            "per-page": str(min(limit, 200)),
            "sort": sort,
        }
        if filter_str:
            params["filter"] = filter_str

        data = await self._make_request("works", params=params)

        if "error" in data:
            return []

        works: list[OpenAlexWork] = []
        for raw_work in data.get("results", []):
            works.append(self._parse_work(raw_work))

        return works

    async def search_by_concept(
        self,
        concept: str,
        limit: int = 10,
    ) -> list[OpenAlexWork]:
        """Search for works by concept/topic.

        Args:
            concept: Concept name (e.g., "artificial intelligence", "machine learning")
            limit: Maximum number of results

        Returns:
            List of OpenAlexWork objects tagged with the concept.
        """
        if not concept or not concept.strip():
            return []

        params: dict[str, str] = {
            "search": concept,
            "per-page": str(min(limit, 200)),
        }

        # Use concept search endpoint first, then get works for top concept
        concept_data = await self._make_request("concepts", params=params)

        if "error" in concept_data or not concept_data.get("results"):
            # Fall back to regular work search
            return await self.search_works(concept, limit=limit)

        # Get the top concept ID
        top_concept = concept_data["results"][0]
        concept_id = top_concept.get("id", "").replace("https://openalex.org/", "")

        if not concept_id:
            return await self.search_works(concept, limit=limit)

        # Search works filtered by this concept
        works_params: dict[str, str] = {
            "filter": f"concepts.id:{concept_id}",
            "per-page": str(min(limit, 200)),
            "sort": "cited_by_count:desc",
        }

        works_data = await self._make_request("works", params=works_params)

        if "error" in works_data:
            return []

        works: list[OpenAlexWork] = []
        for raw_work in works_data.get("results", []):
            works.append(self._parse_work(raw_work))

        return works

    async def get_work(self, work_id: str) -> OpenAlexWork | None:
        """Get details for a specific work.

        Args:
            work_id: OpenAlex work ID (e.g., "W2741809807") or DOI (prefix with "doi:")

        Returns:
            OpenAlexWork object, or None if not found.
        """
        if not work_id:
            return None

        data = await self._make_request(f"works/{work_id}")

        if "error" in data:
            return None

        return self._parse_work(data)

    # ─────────────────────────────────────────────────────────────────────
    # Citations
    # ─────────────────────────────────────────────────────────────────────

    async def get_citations(
        self,
        work_id: str,
        limit: int = 20,
    ) -> list[OpenAlexWork]:
        """Get works that cite a given work.

        Args:
            work_id: OpenAlex work ID
            limit: Maximum number of citing works to return

        Returns:
            List of OpenAlexWork objects citing the target work.
        """
        if not work_id:
            return []

        params: dict[str, str] = {
            "filter": f"cites:{work_id}",
            "per-page": str(min(limit, 200)),
            "sort": "cited_by_count:desc",
        }

        data = await self._make_request("works", params=params)

        if "error" in data:
            return []

        works: list[OpenAlexWork] = []
        for raw_work in data.get("results", []):
            works.append(self._parse_work(raw_work))

        return works

    async def get_references(
        self,
        work_id: str,
        limit: int = 20,
    ) -> list[OpenAlexWork]:
        """Get works that a given work references.

        Args:
            work_id: OpenAlex work ID
            limit: Maximum number of referenced works to return

        Returns:
            List of OpenAlexWork objects referenced by the target work.
        """
        if not work_id:
            return []

        # OpenAlex stores referenced_works as a list of IDs
        work_data = await self._make_request(f"works/{work_id}")

        if "error" in work_data:
            return []

        referenced_ids = work_data.get("referenced_works", [])
        if not referenced_ids:
            return []

        # Fetch each referenced work (limited to avoid rate limits)
        references: list[OpenAlexWork] = []
        for ref_id in referenced_ids[:limit]:
            ref_id_clean = ref_id.replace("https://openalex.org/", "")
            work = await self.get_work(ref_id_clean)
            if work:
                references.append(work)

        return references

    # ─────────────────────────────────────────────────────────────────────
    # Institutions
    # ─────────────────────────────────────────────────────────────────────

    async def get_institution(self, institution_id: str) -> OpenAlexInstitution | None:
        """Get details for a specific institution.

        Args:
            institution_id: OpenAlex institution ID (e.g., "I136199984" for MIT)

        Returns:
            OpenAlexInstitution object, or None if not found.
        """
        if not institution_id:
            return None

        data = await self._make_request(f"institutions/{institution_id}")

        if "error" in data:
            return None

        return OpenAlexInstitution(
            institution_id=data.get("id", "").replace("https://openalex.org/", ""),
            name=data.get("display_name", ""),
            country=data.get("country", ""),
            country_code=data.get("country_code", ""),
            ror_id=data.get("ror", ""),
            type=data.get("type", ""),
            works_count=data.get("works_count", 0) or 0,
            cited_by_count=data.get("cited_by_count", 0) or 0,
            homepage_url=data.get("homepage_url", "") or "",
            latitude=data.get("geo", {}).get("latitude") if data.get("geo") else None,
            longitude=data.get("geo", {}).get("longitude") if data.get("geo") else None,
            display_name_alternatives=data.get("display_name_alternatives", []) or [],
        )

    async def search_institutions(
        self,
        query: str,
        limit: int = 10,
    ) -> list[OpenAlexInstitution]:
        """Search for institutions by name.

        Args:
            query: Institution name (e.g., "Massachusetts Institute")
            limit: Maximum number of results

        Returns:
            List of OpenAlexInstitution objects matching the query.
        """
        if not query or not query.strip():
            return []

        params: dict[str, str] = {
            "search": query,
            "per-page": str(min(limit, 200)),
        }

        data = await self._make_request("institutions", params=params)

        if "error" in data:
            return []

        institutions: list[OpenAlexInstitution] = []
        for raw_inst in data.get("results", []):
            institutions.append(OpenAlexInstitution(
                institution_id=raw_inst.get("id", "").replace("https://openalex.org/", ""),
                name=raw_inst.get("display_name", ""),
                country=raw_inst.get("country", ""),
                country_code=raw_inst.get("country_code", ""),
                ror_id=raw_inst.get("ror", ""),
                type=raw_inst.get("type", ""),
                works_count=raw_inst.get("works_count", 0) or 0,
                cited_by_count=raw_inst.get("cited_by_count", 0) or 0,
                homepage_url=raw_inst.get("homepage_url", "") or "",
                display_name_alternatives=raw_inst.get("display_name_alternatives", []) or [],
            ))

        return institutions

    # ─────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────

    async def search_recent(
        self,
        query: str,
        limit: int = 10,
        years_back: int = 3,
    ) -> list[OpenAlexWork]:
        """Search for recent works within the last N years.

        Args:
            query: Search query
            limit: Maximum number of results
            years_back: How many years back to search

        Returns:
            List of recent OpenAlexWork objects.
        """
        import datetime
        current_year = datetime.datetime.now().year
        filter_str = f"publication_year:{current_year - years_back}-{current_year}"
        return await self.search_works(query, limit=limit, filter_str=filter_str)

    async def search_highly_cited(
        self,
        query: str,
        limit: int = 10,
        min_citations: int = 50,
    ) -> list[OpenAlexWork]:
        """Search for highly-cited works on a topic.

        Args:
            query: Search query
            limit: Maximum number of results
            min_citations: Minimum citation count threshold

        Returns:
            List of highly-cited OpenAlexWork objects.
        """
        works = await self.search_works(
            query,
            limit=limit * 3,
            sort="cited_by_count:desc",
        )
        return [w for w in works if w.cited_by_count >= min_citations][:limit]

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> OpenAlexClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
