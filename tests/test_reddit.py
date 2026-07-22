"""
Tests for Reddit client — post search and sentiment via PRAW.

Tests:
- Client initialization
- Post search (mocked)
- Error handling for API failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.tools.reddit import RedditClient, RedditPost, RedditComment, Subreddit, SentimentSummary


class TestRedditClient:
    """Test RedditClient initialization and operations."""

    def test_init(self):
        """Client initializes with settings."""
        from hyperion.config import get_settings

        settings = get_settings()
        client = RedditClient(settings=settings)
        assert client is not None

    def test_init_without_settings(self):
        """Client initializes without settings (defaults)."""
        client = RedditClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_search_posts_returns_results(self):
        """Search posts returns RedditPost objects (mocked)."""
        client = RedditClient()

        mock_subreddit = MagicMock()
        mock_subreddit.display_name = "technology"
        mock_subreddit.__str__ = MagicMock(return_value="technology")

        mock_author = MagicMock()
        mock_author.name = "user1"

        mock_submission1 = MagicMock()
        mock_submission1.id = "abc123"
        mock_submission1.title = "What do you think about AI in 2024?"
        mock_submission1.subreddit = mock_subreddit
        mock_submission1.author = mock_author
        mock_submission1.score = 1500
        mock_submission1.upvote_ratio = 0.92
        mock_submission1.num_comments = 350
        mock_submission1.created_utc = 1705312800.0
        mock_submission1.url = "https://reddit.com/r/technology/comments/abc123"
        mock_submission1.selftext = ""
        mock_submission1.permalink = "/r/technology/comments/abc123/what_do_you_think/"
        mock_submission1.link_flair_text = "Discussion"
        mock_submission1.is_self = True
        mock_submission1.over_18 = False

        mock_subreddit2 = MagicMock()
        mock_subreddit2.display_name = "artificial"
        mock_subreddit2.__str__ = MagicMock(return_value="artificial")

        mock_author2 = MagicMock()
        mock_author2.name = "user2"

        mock_submission2 = MagicMock()
        mock_submission2.id = "def456"
        mock_submission2.title = "AI breakthrough announced"
        mock_submission2.subreddit = mock_subreddit2
        mock_submission2.author = mock_author2
        mock_submission2.score = 2000
        mock_submission2.upvote_ratio = 0.95
        mock_submission2.num_comments = 500
        mock_submission2.created_utc = 1706798400.0
        mock_submission2.url = "https://example.com/ai-breakthrough"
        mock_submission2.selftext = ""
        mock_submission2.permalink = "/r/artificial/comments/def456/ai_breakthrough/"
        mock_submission2.link_flair_text = "News"
        mock_submission2.is_self = False
        mock_submission2.over_18 = False

        mock_reddit = MagicMock()
        mock_reddit.search.return_value = [mock_submission1, mock_submission2]

        with patch.object(client, "_get_reddit", return_value=mock_reddit):
            results = await client.search_posts("AI 2024", sort="relevance", time_filter="year", limit=15)

        assert len(results) == 2
        assert isinstance(results[0], RedditPost)
        assert results[0].title == "What do you think about AI in 2024?"
        assert results[0].subreddit == "technology"

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        """Empty query returns empty list."""
        client = RedditClient()
        results = await client.search_posts("")
        assert results == []

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        client = RedditClient()

        mock_reddit = MagicMock()
        mock_reddit.search.side_effect = Exception("API error")

        with patch.object(client, "_get_reddit", return_value=mock_reddit):
            results = await client.search_posts("test query")
            assert results == []

    @pytest.mark.asyncio
    async def test_close(self):
        """Client closes cleanly."""
        client = RedditClient()
        await client.close()
