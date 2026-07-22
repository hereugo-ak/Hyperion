"""
HYPERION HackerNews Client — tech community sentiment and discussion signals.

HackerNews provides:
- Story search via Algolia-powered API (full-text search)
- Story metadata (points, comments, author, date)
- Comment extraction for individual stories
- Date-filtered search for recent discussions
- Tags for filtering (story, comment, poll, etc.)

This is NOT a generic "search HN" wrapper. It:
- Uses the Algolia HN Search API (hn.algolia.com/api/v1)
- No auth, no rate limit (100ms delay recommended)
- Returns structured HNStory data for developer sentiment analysis
- Provides comment extraction for deeper sentiment mining
- Caches responses to minimize API calls
- Handles missing stories and API errors gracefully

Architecture reference: §5.1 — "Tech community discussions, developer
sentiment. Algolia search API. Free, unlimited. Used for tech adoption
signals and developer sentiment analysis."

Tool selection logic (§5.2):
  Community sentiment task (tech/developer):
    1. HackerNews (primary — developer/tech community) ← THIS
    2. Reddit (complementary — broader community sentiment)

Used by: Consumer Insights (developer sentiment for B2B), Technology
Analyst (tech adoption signals), Innovation Analyst (emerging tech
discussions, hype signals) (§5.1)
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
CACHE_TTL_SECONDS = 1800  # 30 min — HN discussions evolve


@dataclass
class HNStory:
    """A HackerNews story from the Algolia search API."""

    object_id: str
    title: str = ""
    url: str = ""
    points: int = 0
    num_comments: int = 0
    author: str = ""
    created_at: str = ""
    created_at_i: int = 0
    story_text: str = ""
    tags: list[str] = field(default_factory=list)
    hn_url: str = ""  # Direct HN discussion URL

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "title": self.title,
            "url": self.url,
            "points": self.points,
            "num_comments": self.num_comments,
            "author": self.author,
            "created_at": self.created_at,
            "created_at_i": self.created_at_i,
            "story_text": self.story_text,
            "tags": self.tags,
            "hn_url": self.hn_url,
        }


@dataclass
class HNComment:
    """A HackerNews comment from the Algolia search API."""

    object_id: str
    text: str = ""
    author: str = ""
    points: int = 0
    created_at: str = ""
    created_at_i: int = 0
    parent_id: str = ""
    story_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "object_id": self.object_id,
            "text": self.text,
            "author": self.author,
            "points": self.points,
            "created_at": self.created_at,
            "created_at_i": self.created_at_i,
            "parent_id": self.parent_id,
            "story_id": self.story_id,
        }


class HackerNewsClient:
    """HackerNews search and sentiment client.

    Provides access to HN stories and comments via the Algolia search
    API. Free, unlimited. (§5.1)

    Usage:
        client = HackerNewsClient(settings=settings)

        # Search for stories
        stories = await client.search_stories("rust programming", hits=10)
        for s in stories:
            print(f"{s.title} ({s.points} points, {s.num_comments} comments)")

        # Get a specific story
        story = await client.get_story("12345")

        # Get comments for a story
        comments = await client.get_comments("12345", limit=20)
        for c in comments:
            print(f"{c.author}: {c.text[:100]}")

        # Search recent stories (last 7 days)
        recent = await client.search_by_date("openai", days=7)
    """

    BASE_URL = "https://hn.algolia.com/api/v1"

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 2
    RATE_LIMIT_DELAY = 0.1  # 100ms = 10 req/sec recommended

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
        """Make a rate-limited, cached request to the HN Algolia API."""
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
                logger.warning("HackerNews request failed (attempt %d): %s", attempt + 1, e)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return {"error": str(e)}

        return {"error": "All retries exhausted"}

    def _parse_story(self, raw: dict[str, Any]) -> HNStory:
        """Parse a raw story dict from the Algolia API into an HNStory."""
        object_id = str(raw.get("objectID", ""))
        return HNStory(
            object_id=object_id,
            title=raw.get("title", "") or raw.get("story_title", "") or "",
            url=raw.get("url", "") or raw.get("story_url", "") or "",
            points=raw.get("points", 0) or 0,
            num_comments=raw.get("num_comments", 0) or raw.get("children", []) and len(raw.get("children", [])) or 0,
            author=raw.get("author", "") or "",
            created_at=raw.get("created_at", "") or "",
            created_at_i=raw.get("created_at_i", 0) or 0,
            story_text=raw.get("story_text", "") or "",
            tags=raw.get("_tags", []) or [],
            hn_url=f"https://news.ycombinator.com/item?id={object_id}" if object_id else "",
        )

    def _parse_comment(self, raw: dict[str, Any]) -> HNComment:
        """Parse a raw comment dict from the Algolia API into an HNComment."""
        return HNComment(
            object_id=str(raw.get("objectID", "")),
            text=raw.get("comment_text", "") or raw.get("text", "") or "",
            author=raw.get("author", "") or "",
            points=raw.get("points", 0) or 0,
            created_at=raw.get("created_at", "") or "",
            created_at_i=raw.get("created_at_i", 0) or 0,
            parent_id=str(raw.get("parent_id", "")) or "",
            story_id=str(raw.get("story_id", "")) or "",
        )

    # ─────────────────────────────────────────────────────────────────────
    # Search
    # ─────────────────────────────────────────────────────────────────────

    async def search_stories(
        self,
        query: str,
        tags: str = "story",
        hits: int = 20,
    ) -> list[HNStory]:
        """Search for HackerNews stories by keyword query.

        Args:
            query: Search query (e.g., "rust programming language")
            tags: Filter by tag ("story", "comment", "poll", "pollopt", "front_page")
            hits: Maximum number of results (max 1000)

        Returns:
            List of HNStory objects matching the query.
        """
        if not query or not query.strip():
            return []

        params: dict[str, str] = {
            "query": query,
            "tags": tags,
            "hitsPerPage": str(min(hits, 1000)),
        }

        data = await self._make_request("search", params=params)

        if "error" in data:
            return []

        stories: list[HNStory] = []
        for hit in data.get("hits", []):
            stories.append(self._parse_story(hit))

        return stories

    async def search_by_date(
        self,
        query: str,
        days: int = 7,
        tags: str = "story",
        hits: int = 20,
    ) -> list[HNStory]:
        """Search for recent HackerNews stories within the last N days.

        Args:
            query: Search query
            days: Number of days to look back (e.g., 7 = last week)
            tags: Filter by tag
            hits: Maximum number of results

        Returns:
            List of recent HNStory objects.
        """
        if not query or not query.strip():
            return []

        # Calculate timestamp range
        time_range = int(time.time()) - (days * 86400)

        params: dict[str, str] = {
            "query": query,
            "tags": tags,
            "numericFilters": f"created_at_i>{time_range}",
            "hitsPerPage": str(min(hits, 1000)),
        }

        data = await self._make_request("search_by_date", params=params)

        if "error" in data:
            return []

        stories: list[HNStory] = []
        for hit in data.get("hits", []):
            stories.append(self._parse_story(hit))

        return stories

    # ─────────────────────────────────────────────────────────────────────
    # Story Details
    # ─────────────────────────────────────────────────────────────────────

    async def get_story(self, story_id: str) -> HNStory | None:
        """Get details for a specific HackerNews story.

        Args:
            story_id: HN story ID (e.g., "12345")

        Returns:
            HNStory object, or None if not found.
        """
        if not story_id:
            return None

        # Use search with tags=story and numericFilters on objectID
        params: dict[str, str] = {
            "tags": "story",
            "numericFilters": f"objectID={story_id}",
        }

        data = await self._make_request("search", params=params)

        if "error" in data:
            return None

        hits = data.get("hits", [])
        if not hits:
            return None

        return self._parse_story(hits[0])

    # ─────────────────────────────────────────────────────────────────────
    # Comments
    # ─────────────────────────────────────────────────────────────────────

    async def get_comments(
        self,
        story_id: str,
        limit: int = 20,
    ) -> list[HNComment]:
        """Get comments for a specific HackerNews story.

        Uses the Algolia API to fetch comments associated with a story.

        Args:
            story_id: HN story ID
            limit: Maximum number of comments to return

        Returns:
            List of HNComment objects.
        """
        if not story_id:
            return []

        # Search for comments with this story_id
        params: dict[str, str] = {
            "tags": "comment",
            "numericFilters": f"story_id={story_id}",
            "hitsPerPage": str(min(limit, 1000)),
        }

        data = await self._make_request("search", params=params)

        if "error" in data:
            return []

        comments: list[HNComment] = []
        for hit in data.get("hits", []):
            comments.append(self._parse_comment(hit))

        return comments

    # ─────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────

    async def get_top_stories_by_points(
        self,
        query: str,
        limit: int = 10,
    ) -> list[HNStory]:
        """Search for stories sorted by points (most upvoted first).

        Args:
            query: Search query
            limit: Maximum number of results

        Returns:
            List of HNStory objects sorted by points.
        """
        stories = await self.search_stories(query, hits=limit * 3)
        # Sort by points descending
        stories.sort(key=lambda s: s.points, reverse=True)
        return stories[:limit]

    async def get_discussion_summary(
        self,
        query: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Get a summary of HN discussions for a topic.

        Combines top stories and their comment counts into a summary.

        Args:
            query: Search query
            limit: Number of top stories to include

        Returns:
            Dict with stories, total_points, total_comments, and top_authors.
        """
        stories = await self.search_stories(query, hits=limit * 2)
        stories.sort(key=lambda s: s.points, reverse=True)
        top_stories = stories[:limit]

        total_points = sum(s.points for s in top_stories)
        total_comments = sum(s.num_comments for s in top_stories)

        # Count author frequency
        author_counts: dict[str, int] = {}
        for s in top_stories:
            if s.author:
                author_counts[s.author] = author_counts.get(s.author, 0) + 1

        top_authors = sorted(author_counts.items(), key=lambda x: x[1], reverse=True)[:5]

        return {
            "query": query,
            "stories": [s.to_dict() for s in top_stories],
            "total_points": total_points,
            "total_comments": total_comments,
            "top_authors": top_authors,
        }

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> HackerNewsClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
