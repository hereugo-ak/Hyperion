"""
Tests for World Bank client — macro economic indicators.

Tests:
- Client initialization
- Indicator data retrieval (mocked)
- Error handling for API failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.tools.world_bank import WorldBankClient, WorldBankIndicator, WorldBankIndicatorData, WorldBankCountryProfile


class TestWorldBankClient:
    """Test WorldBankClient initialization and operations."""

    def test_init(self):
        """Client initializes with settings."""
        from hyperion.config import get_settings

        settings = get_settings()
        client = WorldBankClient(settings=settings)
        assert client is not None

    def test_init_without_settings(self):
        """Client initializes without settings (defaults)."""
        client = WorldBankClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_get_indicator_returns_data(self):
        """Get indicator returns WorldBankIndicatorData (mocked)."""
        client = WorldBankClient()

        mock_data = [
            {"page": 1, "pages": 1, "per_page": 50, "total": 2},
            [
                {
                    "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                    "country": {"id": "US", "value": "United States"},
                    "countryiso3code": "USA",
                    "date": "2023",
                    "value": 27360900000000,
                    "unit": "",
                    "obs_status": "",
                },
                {
                    "indicator": {"id": "NY.GDP.MKTP.CD", "value": "GDP (current US$)"},
                    "country": {"id": "CN", "value": "China"},
                    "countryiso3code": "CHN",
                    "date": "2023",
                    "value": 17794780000000,
                    "unit": "",
                    "obs_status": "",
                },
            ],
        ]

        with patch.object(client, "_make_request", new=AsyncMock(return_value=mock_data)):
            result = await client.get_indicator("gdp", country="all", date_range="2023:2023")

        assert result is not None
        assert isinstance(result, WorldBankIndicatorData)
        assert result.indicator_code == "NY.GDP.MKTP.CD"
        assert len(result.data_points) == 2
        assert result.data_points[0]["country"] == "United States"

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        client = WorldBankClient()

        with patch.object(client, "_make_request", new=AsyncMock(return_value={"error": "API error"})):
            result = await client.get_indicator("gdp", country="US")
            # Should return empty data, not crash
            assert result is not None
            assert result.data_points == []

    @pytest.mark.asyncio
    async def test_close(self):
        """Client closes cleanly."""
        client = WorldBankClient()
        await client.close()
