"""
Tests for HackerNews client — story search via Algolia API.

Tests:
- Client initialization
- Story search (mocked)
- Error handling for API failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.tools.hackernews import HackerNewsClient, HNStory, HNComment


class TestHackerNewsClient:
    """Test HackerNewsClient initialization and operations."""

    def test_init(self):
        """Client initializes with settings."""
        from hyperion.config import get_settings

        settings = get_settings()
        client = HackerNewsClient(settings=settings)
        assert client is not None

    def test_init_without_settings(self):
        """Client initializes without settings (defaults)."""
        client = HackerNewsClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_search_stories_returns_results(self):
        """Search stories returns HNStory objects (mocked)."""
        client = HackerNewsClient()

        mock_data = {
            "hits": [
                {
                    "objectID": "12345",
                    "title": "Show HN: A new Rust web framework",
                    "url": "https://example.com/rust-framework",
                    "points": 250,
                    "num_comments": 80,
                    "author": "rustdev",
                    "created_at": "2024-01-15T10:00:00Z",
                    "created_at_i": 1705312800,
                    "story_text": "",
                    "_tags": ["story"],
                },
                {
                    "objectID": "67890",
                    "title": "Rust 1.75 released",
                    "url": "https://blog.rust-lang.org/2024/01/75.html",
                    "points": 500,
                    "num_comments": 200,
                    "author": "rustteam",
                    "created_at": "2024-02-01T12:00:00Z",
                    "created_at_i": 1706798400,
                    "story_text": "",
                    "_tags": ["story"],
                },
            ],
            "nbHits": 2,
        }

        with patch.object(client, "_make_request", new=AsyncMock(return_value=mock_data)):
            results = await client.search_stories("rust programming", hits=10)

        assert len(results) == 2
        assert isinstance(results[0], HNStory)
        assert results[0].title == "Show HN: A new Rust web framework"
        assert results[0].points == 250

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        """Empty query returns empty list."""
        client = HackerNewsClient()
        results = await client.search_stories("")
        assert results == []

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        client = HackerNewsClient()

        with patch.object(client, "_make_request", new=AsyncMock(return_value={"error": "API error"})):
            results = await client.search_stories("test query")
            assert results == []

    @pytest.mark.asyncio
    async def test_close(self):
        """Client closes cleanly."""
        client = HackerNewsClient()
        await client.close()
