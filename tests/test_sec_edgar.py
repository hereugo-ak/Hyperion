"""
Tests for SEC EDGAR client — financial filings search and retrieval.

Tests:
- Client initialization
- Full-text search (mocked)
- Error handling for API failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.tools.sec_edgar import SECEdgarClient, SECFiling, SECFilingContent, SECCompanyInfo


class TestSECEdgarClient:
    """Test SECEdgarClient initialization and operations."""

    def test_init(self):
        """Client initializes with settings."""
        from hyperion.config import get_settings

        settings = get_settings()
        client = SECEdgarClient(settings=settings)
        assert client is not None

    def test_init_without_settings(self):
        """Client initializes without settings (defaults)."""
        client = SECEdgarClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_search_full_text_returns_results(self):
        """Full-text search returns SECFiling objects (mocked)."""
        client = SECEdgarClient()

        mock_data = {
            "hits": {
                "total": {"value": 2},
                "hits": [
                    {
                        "_id": "f1",
                        "_source": {
                            "accession_no": "0000000001-24-000001",
                            "entity_id": "0000000001",
                            "form": "10-K",
                            "entity_name": "Test Company Inc.",
                            "file_date": "2024-01-15",
                            "period_ending": "2023-12-31",
                            "file_description": "Annual report",
                        },
                    },
                    {
                        "_id": "f2",
                        "_source": {
                            "accession_no": "0000000002-24-000002",
                            "entity_id": "0000000002",
                            "form": "10-Q",
                            "entity_name": "Another Corp",
                            "file_date": "2024-02-20",
                            "period_ending": "2023-12-31",
                            "file_description": "Quarterly report",
                        },
                    },
                ],
            }
        }

        with patch.object(client, "_make_request", new=AsyncMock(return_value=mock_data)):
            results = await client.search_full_text("artificial intelligence", limit=10)

        assert len(results) == 2
        assert isinstance(results[0], SECFiling)
        assert results[0].filing_type == "10-K"
        assert results[0].company_name == "Test Company Inc."

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        """Empty query returns empty list."""
        client = SECEdgarClient()
        results = await client.search_full_text("")
        assert results == []

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        client = SECEdgarClient()

        with patch.object(client, "_make_request", new=AsyncMock(return_value={"error": "API error"})):
            results = await client.search_full_text("test query")
            assert results == []

    @pytest.mark.asyncio
    async def test_close(self):
        """Client closes cleanly."""
        client = SECEdgarClient()
        await client.close()
