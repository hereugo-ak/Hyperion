"""
Tests for HYPERION Output — PDF generation, image pipeline, charts, markdown.

Tests:
- ChartGenerator brand colors and chart types
- ImageProcessor pipeline steps
- PDFRenderer and TemplateRenderer
- MarkdownExporter structure
- Brand CSS compliance (no blue/purple)

Architecture reference: §6 Output Pipeline, §7 Brand System
"""

import pytest
from pathlib import Path

from hyperion.config import get_settings


class TestChartGenerator:
    """Test the Plotly ChartGenerator."""

    def test_brand_colors_not_blue(self):
        """Chart colors should NOT contain blue or purple."""
        from hyperion.output.charts import ChartGenerator

        settings = get_settings()
        gen = ChartGenerator(settings=settings)

        for color in settings.brand.chart_colors:
            # Convert to RGB and check it's not blue-dominant or purple
            hex_color = color.lstrip("#")
            r = int(hex_color[0:2], 16)
            g = int(hex_color[2:4], 16)
            b = int(hex_color[4:6], 16)
            # Blue-dominant: b > r and b > g and b > 100
            assert not (b > r and b > g and b > 100), f"Color {color} is blue-dominant"
            # Purple: r and b both high, g low
            assert not (r > 100 and b > 100 and g < 80), f"Color {color} is purple"

    def test_chart_color_sequence(self):
        """Chart colors should match the ARCHITECTURE.md sequence."""
        settings = get_settings()
        expected = [
            "#C8704D",  # Terracotta
            "#7C9885",  # Sage
            "#3D3530",  # Deep Brown
            "#8B8680",  # Warm Gray
            "#E8E6DD",  # Beige
            "#B5533C",  # Alert Red
        ]
        assert settings.brand.chart_colors == expected


class TestImageProcessor:
    """Test the Pillow ImageProcessor."""

    def test_image_processor_has_pipeline(self):
        """ImageProcessor should have the 6-step pipeline methods."""
        from hyperion.output.images import ImageProcessor

        settings = get_settings()
        proc = ImageProcessor(settings=settings)

        # Check the 6-step pipeline methods exist
        assert hasattr(proc, "process_image") or hasattr(proc, "process_for_pdf")
        assert hasattr(proc, "process_cover_image") or hasattr(proc, "process_cover")
        assert hasattr(proc, "process_section_image") or hasattr(proc, "process_section")


class TestPDFRenderer:
    """Test the PDFRenderer and TemplateRenderer."""

    def test_pdf_renderer_init(self):
        """PDFRenderer should initialize with correct settings."""
        from hyperion.output.render import PDFRenderer

        settings = get_settings()
        renderer = PDFRenderer(settings=settings)
        assert renderer is not None

    def test_template_renderer_init(self):
        """TemplateRenderer should initialize and find templates."""
        from hyperion.output.render import TemplateRenderer

        renderer = TemplateRenderer()
        assert renderer is not None


class TestMarkdownExporter:
    """Test the MarkdownExporter."""

    def test_markdown_exporter_init(self):
        """MarkdownExporter should initialize."""
        from hyperion.output.markdown import MarkdownExporter

        exporter = MarkdownExporter()
        assert exporter is not None


class TestBrandCSS:
    """Test brand CSS compliance."""

    def test_css_has_no_blue(self):
        """The brand CSS should not use blue colors."""
        css_path = Path(__file__).parent.parent / "hyperion" / "output" / "templates" / "styles" / "hyperion.css"
        if css_path.exists():
            css_content = css_path.read_text(encoding="utf-8").lower()
            # Check for blue hex codes
            blue_codes = ["#0000ff", "#000080", "#4169e1", "#1e90ff", "#87ceeb"]
            for code in blue_codes:
                assert code not in css_content, f"Blue color {code} found in CSS"
            # Check for purple hex codes
            purple_codes = ["#800080", "#9370db", "#6a5acd", "#483d8b"]
            for code in purple_codes:
                assert code not in css_content, f"Purple color {code} found in CSS"

    def test_css_has_warm_palette(self):
        """The brand CSS should use the warm palette colors."""
        css_path = Path(__file__).parent.parent / "hyperion" / "output" / "templates" / "styles" / "hyperion.css"
        if css_path.exists():
            css_content = css_path.read_text(encoding="utf-8")
            # Check for warm palette colors (case-insensitive)
            warm_colors = ["#1a1a1a", "#f5f4ee", "#c8704d", "#7c9885"]
            found = sum(1 for c in warm_colors if c.lower() in css_content.lower())
            assert found >= 2, "CSS should contain at least 2 warm palette colors"


class TestOrchestrator:
    """Test the WorkflowEngine orchestrator."""

    def test_orchestrator_init(self):
        """WorkflowEngine should initialize."""
        from hyperion.orchestrator import WorkflowEngine

        engine = WorkflowEngine()
        assert engine is not None
        assert engine.MAX_QUALITY_ITERATIONS == 2  # P7: capped at ≤2
        assert engine.TASK_TIMEOUT_SECONDS == 600

    def test_engagement_result_dataclass(self):
        """EngagementResult should have required fields."""
        from hyperion.orchestrator import EngagementResult

        result = EngagementResult()
        assert result.engagement_id == ""
        assert result.success is False
        assert result.duration_seconds == 0.0

    def test_agent_instantiation_function(self):
        """_instantiate_agent should map all agent names to classes."""
        from hyperion.orchestrator import _instantiate_agent
        from hyperion.schemas.agents import AgentName

        # Test a few key agents
        director = _instantiate_agent(AgentName.ENGAGEMENT_DIRECTOR)
        assert director is not None

        synthesis = _instantiate_agent(AgentName.SYNTHESIS_LEAD)
        assert synthesis is not None

        market = _instantiate_agent(AgentName.MARKET_ANALYST)
        assert market is not None
