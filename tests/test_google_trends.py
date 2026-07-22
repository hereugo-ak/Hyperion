"""
Tests for Google Trends client — search interest and related queries.

Tests:
- Client initialization
- Interest over time (mocked)
- Error handling for API failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.tools.google_trends import GoogleTrendsClient, TrendResult, RelatedQuery, RelatedTopic


class TestGoogleTrendsClient:
    """Test GoogleTrendsClient initialization and operations."""

    def test_init(self):
        """Client initializes with settings."""
        from hyperion.config import get_settings

        settings = get_settings()
        client = GoogleTrendsClient(settings=settings)
        assert client is not None

    def test_init_without_settings(self):
        """Client initializes without settings (defaults)."""
        client = GoogleTrendsClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_get_interest_over_time(self):
        """Get interest over time returns TrendResult (mocked)."""
        client = GoogleTrendsClient()

        mock_pytrends = MagicMock()
        mock_df = MagicMock()
        mock_df.empty = False

        mock_row = MagicMock()
        mock_row.index = ["AI", "isPartial"]
        mock_row.__getitem__ = MagicMock(side_effect=lambda key: 75 if key == "AI" else False)

        mock_date = MagicMock()
        mock_date.date.return_value = "2024-01-01"

        mock_df.iterrows.return_value = [(mock_date, mock_row)]
        mock_pytrends.interest_over_time.return_value = mock_df

        with patch.object(client, "_get_pytrends", return_value=mock_pytrends):
            result = await client.get_interest_over_time(["AI"], timeframe="today 12-m")

        assert result is not None
        assert isinstance(result, TrendResult)
        assert result.keywords == ["AI"]

    @pytest.mark.asyncio
    async def test_empty_keywords(self):
        """Empty keywords returns empty TrendResult."""
        client = GoogleTrendsClient()
        result = await client.get_interest_over_time([])
        assert isinstance(result, TrendResult)
        assert result.keywords == []

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        client = GoogleTrendsClient()

        with patch.object(client, "_get_pytrends", side_effect=Exception("API error")):
            result = await client.get_interest_over_time(["test"])
            assert isinstance(result, TrendResult)

    @pytest.mark.asyncio
    async def test_close(self):
        """Client closes cleanly."""
        client = GoogleTrendsClient()
        await client.close()
