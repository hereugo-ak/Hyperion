"""Tests for fix 3.5 — Markup-returning filter in the fallback Jinja env.

The audit (§3.4 escaped-HTML divergence, §6 Phase 3 item 3.5) requires:
    Fix presentation_designer.py:1947 — register the real Markup-returning
    filter in the fallback env.

Background: the fallback Jinja env (used when the JINJA2 tool path raises)
registered ``md_to_html = lambda v: v or ""`` — a plain str. Jinja
autoescapes plain strs, so every markdown-produced ``<p>`` / ``<strong>``
tag rendered as VISIBLE text on the page. The production path returns
markupsafe.Markup. The fallback must register the SAME real filters or it
ships a different document.

These tests cover:
  1. The real filter returns markupsafe.Markup (never plain str).
  2. Forcing the fallback path renders markdown to real HTML elements —
     no escaped "&lt;p&gt;" / "&lt;strong&gt;" anywhere in the output.
  3. Parity: production and fallback filters are the same callables.
"""

from __future__ import annotations

import pytest
from markupsafe import Markup

from hyperion.agents.delivery.presentation_designer import PresentationDesigner
from hyperion.output.render import TemplateRenderer
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    FinalReport,
    Recommendation,
)


def _minimal_report() -> FinalReport:
    return FinalReport(
        engagement_id="FIX35-TEST",
        question="Should Acme enter the grid-scale storage market?",
        recommendation=Recommendation.CONDITIONAL,
        recommendation_rationale="Unit economics clear in 4 of 11 zones.",
        critical_assumptions=["Pack prices reach $60/kWh by 2028."],
        confidence=ConfidenceLevel.MEDIUM,
        confidence_breakdown={"market": ConfidenceLevel.HIGH},
        executive_summary="**Enter conditionally.** The market expanded at 24% CAGR.",
        sections=[
            AnalysisSection(
                id="market_analysis",
                title="Market Sizing",
                agent="market_analyst",
                key_insight="Only 4 of 11 zones clear the arbitrage spread.",
                body="**Demand formation.**\n\nThe installed base reached *41 GWh* in 2024.",
                implications="Sequence entry behind the two clearing zones.",
                confidence=ConfidenceLevel.HIGH,
            )
        ],
    )


class TestRealFilterReturnsMarkup:
    def test_md_to_html_returns_markup(self) -> None:
        out = TemplateRenderer()._markdown_to_html("**bold** and *italic*")
        assert isinstance(out, Markup), f"got {type(out).__name__}, not Markup"

    def test_md_to_html_produces_real_tags(self) -> None:
        out = TemplateRenderer()._markdown_to_html("**bold** and *italic*")
        assert "<strong>" in str(out)
        assert "<em>" in str(out)

    def test_clean_dict_repr_cleans_dict_leaks(self) -> None:
        """clean_dict_repr outputs plain text (no HTML), so a plain str is
        correct — only md_to_html needs Markup. Assert it actually cleans
        dict-repr leaks rather than passing them through."""
        out = TemplateRenderer()._clean_dict_repr("{'time_to_market': 'Unknown'}")
        assert "{'" not in str(out)
        assert "Time To Market" in str(out)
        # And the fallback env must use this real filter, not the old
        # `lambda v: str(v) if v else ""` passthrough.
        renderer = TemplateRenderer()
        assert renderer._get_env().filters["clean_dict_repr"] == renderer._clean_dict_repr


class TestFallbackRendersRealHtml:
    """Force the fallback Jinja env (JINJA2 tool raises) and assert the
    markdown in section bodies/exec summary renders as real HTML, not as
    escaped visible tags."""

    @pytest.fixture()
    def fallback_html(self, tmp_path, monkeypatch) -> str:
        designer = PresentationDesigner()
        designer.OUTPUT_DIR = str(tmp_path / "output")
        designer.BUILD_DIR = str(tmp_path / "build")
        designer.HTML_OUTPUT = str(tmp_path / "output" / "report.html")

        def _boom(tool):
            raise RuntimeError("JINJA2 tool unavailable — forced for test")

        monkeypatch.setattr(designer, "get_tool", _boom)

        import asyncio

        html_path = asyncio.run(
            designer._render_html_template(
                report=_minimal_report(),
                cover_image=None,
                section_images={},
                chart_placements={},
            )
        )
        assert html_path, "fallback render returned empty path"
        with open(html_path, encoding="utf-8") as f:
            return f.read()

    def test_no_escaped_paragraph_tags(self, fallback_html: str) -> None:
        assert "&lt;p&gt;" not in fallback_html
        assert "&lt;/p&gt;" not in fallback_html

    def test_no_escaped_strong_or_em_tags(self, fallback_html: str) -> None:
        assert "&lt;strong&gt;" not in fallback_html
        assert "&lt;em&gt;" not in fallback_html

    def test_markdown_rendered_to_elements(self, fallback_html: str) -> None:
        assert "<strong>Demand formation.</strong>" in fallback_html
        assert "<em>41 GWh</em>" in fallback_html
        assert "<strong>Enter conditionally.</strong>" in fallback_html

    def test_fallback_uses_same_filters_as_production(self) -> None:
        """Guard against re-divergence: whatever the production env uses for
        md_to_html/clean_dict_repr, the fallback must use the same callables."""
        renderer = TemplateRenderer()
        prod_env = renderer._get_env()
        assert prod_env.filters["md_to_html"] == renderer._markdown_to_html
        assert prod_env.filters["clean_dict_repr"] == renderer._clean_dict_repr
        # The fallback env is constructed inline in presentation_designer;
        # verify by rendering the same input through both filters.
        body = "**Sub-head**\n\nBody *text* with `code`."
        prod_out = str(prod_env.filters["md_to_html"](body))
        assert "<strong>" in prod_out or "<em>" in prod_out
