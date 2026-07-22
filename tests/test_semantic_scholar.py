"""
Tests for Semantic Scholar client — academic paper search.

Tests:
- Client initialization
- Paper search (mocked)
- Error handling for API failures
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.tools.semantic_scholar import SemanticScholarClient, AcademicPaper, CitationGraph


class TestSemanticScholarClient:
    """Test SemanticScholarClient initialization and operations."""

    def test_init(self):
        """Client initializes with settings."""
        from hyperion.config import get_settings

        settings = get_settings()
        client = SemanticScholarClient(settings=settings)
        assert client is not None

    def test_init_without_settings(self):
        """Client initializes without settings (defaults)."""
        client = SemanticScholarClient()
        assert client is not None

    @pytest.mark.asyncio
    async def test_search_returns_results(self):
        """Search returns AcademicPaper objects (mocked)."""
        client = SemanticScholarClient()

        mock_data = {
            "total": 2,
            "data": [
                {
                    "paperId": "abc123",
                    "title": "Attention Is All You Need",
                    "abstract": "We propose a new architecture...",
                    "authors": [{"name": "A. Vaswani", "authorId": "1"}],
                    "year": 2017,
                    "venue": "NeurIPS",
                    "citationCount": 50000,
                    "referenceCount": 15,
                    "influentialCitationCount": 5000,
                    "tldr": {"text": "Transformer architecture"},
                    "fieldsOfStudy": ["Computer Science"],
                    "url": "https://www.semanticscholar.org/paper/abc123",
                },
                {
                    "paperId": "def456",
                    "title": "BERT: Pre-training of Deep Bidirectional Transformers",
                    "abstract": "We introduce a new language representation model...",
                    "authors": [{"name": "J. Devlin", "authorId": "2"}],
                    "year": 2019,
                    "venue": "NAACL",
                    "citationCount": 30000,
                    "referenceCount": 20,
                    "influentialCitationCount": 3000,
                    "tldr": {"text": "BERT model"},
                    "fieldsOfStudy": ["Computer Science"],
                    "url": "https://www.semanticscholar.org/paper/def456",
                },
            ],
        }

        with patch.object(client, "_make_request", new=AsyncMock(return_value=mock_data)):
            results = await client.search("transformer architecture", limit=10)

        assert len(results) == 2
        assert isinstance(results[0], AcademicPaper)
        assert results[0].title == "Attention Is All You Need"
        assert results[0].year == 2017
        assert results[0].citation_count == 50000

    @pytest.mark.asyncio
    async def test_search_empty_query(self):
        """Empty query returns empty list."""
        client = SemanticScholarClient()
        results = await client.search("")
        assert results == []

    @pytest.mark.asyncio
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        client = SemanticScholarClient()

        with patch.object(client, "_make_request", new=AsyncMock(return_value={"error": "API error"})):
            results = await client.search("test query")
            assert results == []

    @pytest.mark.asyncio
    async def test_close(self):
        """Client closes cleanly."""
        client = SemanticScholarClient()
        await client.close()
