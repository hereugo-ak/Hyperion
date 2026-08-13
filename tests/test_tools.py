"""
Tests for HYPERION Tools — tool client initialization and basic operations.

Tests:
- Tool client instantiation
- Tool registry completeness
- Unified search fallback chain
- Unified extract fallback chain

Architecture reference: §5 Tool Registry
"""


from hyperion.schemas.agents import ToolName


class TestToolRegistry:
    """Test that all 23 tools are properly defined in the registry."""

    def test_all_tools_defined(self):
        """All 23 tools should be in the ToolName enum."""
        expected_tools = [
            "searxng", "jina", "obscura", "scrapling", "crawl4ai",
            "flaresolverr", "wayback", "alpha_vantage", "fred",
            "unsplash", "second_brain", "deep_search",
            "sec_edgar", "semantic_scholar", "open_alex", "world_bank",
            "google_trends", "hackernews", "reddit",
            "plotly", "weasyprint", "jinja2", "pillow",
        ]
        for tool_name in expected_tools:
            assert any(t.value == tool_name for t in ToolName), f"Tool {tool_name} not in ToolName enum"

    def test_tool_count(self):
        """Should have exactly 24 tools."""
        # OVERHAUL5 W5: +1 for ToolName.UNIFIED_EXTRACT (the single extraction
        # ladder granted to every specialist).
        assert len(list(ToolName)) == 24


class TestToolInstantiation:
    """Test that tool clients can be instantiated with settings."""

    def test_searxng_client_init(self):
        """SearxNGClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.searxng import SearxNGClient

        settings = get_settings()
        client = SearxNGClient(settings=settings)
        assert client is not None
        assert client.base_url == settings.searxng_url

    def test_jina_client_init(self):
        """JinaClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.jina import JinaClient

        settings = get_settings()
        client = JinaClient(settings=settings)
        assert client is not None

    def test_unified_search_init(self):
        """UnifiedSearch should initialize."""
        from hyperion.config import get_settings
        from hyperion.tools.unified_search import UnifiedSearch

        settings = get_settings()
        search = UnifiedSearch(settings=settings)
        assert search is not None

    def test_unified_extract_init(self):
        """UnifiedExtract should initialize."""
        from hyperion.config import get_settings
        from hyperion.tools.unified_extract import UnifiedExtract

        settings = get_settings()
        extract = UnifiedExtract(settings=settings)
        assert extract is not None

    def test_second_brain_init(self):
        """SecondBrainClient should initialize."""
        from hyperion.config import get_settings
        from hyperion.tools.second_brain import SecondBrainClient

        settings = get_settings()
        client = SecondBrainClient(settings=settings)
        assert client is not None

    def test_deep_search_init(self):
        """DeepSearchClient should initialize."""
        from hyperion.config import get_settings
        from hyperion.tools.deep_search import DeepSearchClient

        settings = get_settings()
        client = DeepSearchClient(settings=settings)
        assert client is not None

    def test_evidence_scorer_init(self):
        """EvidenceScorer should initialize without settings."""
        from hyperion.tools.evidence_scorer import EvidenceScorer

        scorer = EvidenceScorer()
        assert scorer is not None

    # ── Phase 2 Data Sources ──

    def test_sec_edgar_init(self):
        """SECEdgarClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.sec_edgar import SECEdgarClient

        settings = get_settings()
        client = SECEdgarClient(settings=settings)
        assert client is not None

    def test_semantic_scholar_init(self):
        """SemanticScholarClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.semantic_scholar import SemanticScholarClient

        settings = get_settings()
        client = SemanticScholarClient(settings=settings)
        assert client is not None

    def test_openalex_init(self):
        """OpenAlexClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.openalex import OpenAlexClient

        settings = get_settings()
        client = OpenAlexClient(settings=settings)
        assert client is not None

    def test_world_bank_init(self):
        """WorldBankClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.world_bank import WorldBankClient

        settings = get_settings()
        client = WorldBankClient(settings=settings)
        assert client is not None

    def test_google_trends_init(self):
        """GoogleTrendsClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.google_trends import GoogleTrendsClient

        settings = get_settings()
        client = GoogleTrendsClient(settings=settings)
        assert client is not None

    def test_hackernews_init(self):
        """HackerNewsClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.hackernews import HackerNewsClient

        settings = get_settings()
        client = HackerNewsClient(settings=settings)
        assert client is not None

    def test_reddit_init(self):
        """RedditClient should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.tools.reddit import RedditClient

        settings = get_settings()
        client = RedditClient(settings=settings)
        assert client is not None


class TestOutputTools:
    """Test output tool instantiation."""

    def test_chart_generator_init(self):
        """ChartGenerator should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.output.charts import ChartGenerator

        settings = get_settings()
        gen = ChartGenerator(settings=settings)
        assert gen is not None

    def test_image_processor_init(self):
        """ImageProcessor should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.output.images import ImageProcessor

        settings = get_settings()
        proc = ImageProcessor(settings=settings)
        assert proc is not None

    def test_pdf_renderer_init(self):
        """PDFRenderer should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.output.render import PDFRenderer

        settings = get_settings()
        renderer = PDFRenderer(settings=settings)
        assert renderer is not None

    def test_template_renderer_init(self):
        """TemplateRenderer should initialize with settings."""
        from hyperion.config import get_settings
        from hyperion.output.render import TemplateRenderer

        settings = get_settings()
        renderer = TemplateRenderer(settings=settings)
        assert renderer is not None

    def test_markdown_exporter_init(self):
        """MarkdownExporter should initialize."""
        from hyperion.output.markdown import MarkdownExporter

        exporter = MarkdownExporter()
        assert exporter is not None
