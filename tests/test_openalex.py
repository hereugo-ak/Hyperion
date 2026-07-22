"""
Tests for OpenAlex client — scholarly works search.

Tests:
- Client initialization
- Works search (mocked)
- Error handling for API failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.tools.openalex import OpenAlexClient, OpenAlexWork, OpenAlexInstitution


class TestOpenAlexClient:
    """Test OpenAlexClient initialization and operations."""

    def test_init(self):
        """Client initializes with settings."""
        from hyperion.config import get_settings

        settings = get_settings()
        client = OpenAlexClient(settings=settings)
        assert client is not None

    def test_init_without_settings(self):
        """Client initializes without settings (defaults)."""
        client = OpenAlexClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_search_works_returns_results(self):
        """Search works returns OpenAlexWork objects (mocked)."""
        client = OpenAlexClient()

        mock_data = {
            "results": [
                {
                    "id": "https://openalex.org/W123",
                    "title": "Large Language Models: A Survey",
                    "abstract_inverted_index": {
                        "Large": [0],
                        "Language": [1],
                        "Models": [2],
                    },
                    "authorships": [
                        {
                            "author": {"display_name": "J. Smith", "orcid": "", "id": "A1"},
                            "institutions": [],
                        }
                    ],
                    "publication_year": 2024,
                    "primary_location": {"source": {"display_name": "Nature"}},
                    "cited_by_count": 150,
                    "doi": "https://doi.org/10.1234/test",
                    "type": "article",
                    "concepts": [{"display_name": "AI", "level": 1, "score": 0.9, "id": "C1"}],
                    "open_access": {"is_oa": True},
                },
            ],
            "meta": {"count": 1},
        }

        with patch.object(client, "_make_request", new=AsyncMock(return_value=mock_data)):
            results = await client.search_works("large language models", limit=10)

        assert len(results) == 1
        assert isinstance(results[0], OpenAlexWork)
        assert results[0].title == "Large Language Models: A Survey"
        assert results[0].year == 2024
        assert results[0].cited_by_count == 150
        assert results[0].work_id == "W123"

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        """Empty query returns empty list."""
        client = OpenAlexClient()
        results = await client.search_works("")
        assert results == []

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        client = OpenAlexClient()

        with patch.object(client, "_make_request", new=AsyncMock(return_value={"error": "API error"})):
            results = await client.search_works("test query")
            assert results == []

    @pytest.mark.asyncio
    async def test_close(self):
        """Client closes cleanly."""
        client = OpenAlexClient()
        await client.close()
