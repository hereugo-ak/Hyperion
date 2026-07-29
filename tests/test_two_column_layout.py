"""Tests for fix 3.4 — two-column body targeting 56 chars/line.

The audit (§3.4/§6 Phase 3 item 3.4) requires:
    Two-column body via column-count: 2; column-gap: 7mm targeting
    **56 chars/line**; keep exhibits/KPI strips full-bleed with
    column-span: all.

Exit criterion (§6 Phase 3): probe reports 52–60 chars/line, 2 columns.

These tests cover:
  1. Structure: the shipped CSS_TEMPLATE columns the prose body and
     column-spans the visual anchors; the HTML_TEMPLATE wraps body prose
     in .section-body (not the old impossible .no-break).
  2. Page frame: margins are benchmark-like (BCG measures L 36pt · R 35pt)
     so the columns actually resolve to the 52–60 char band.
  3. End-to-end: a real WeasyPrint render of the production template path
     measures 2 column bands and a median line measure inside 52–60 chars.
"""

from __future__ import annotations

import re
import statistics
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from hyperion.agents.delivery.presentation_designer import (  # noqa: E402
    CSS_TEMPLATE,
    HTML_TEMPLATE,
)


def _css_rule(css: str, selector: str) -> str:
    """Extract the declaration block for a selector (best-effort)."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


class TestTwoColumnStructure:
    def test_section_body_is_two_column(self) -> None:
        rule = _css_rule(CSS_TEMPLATE, ".section-body")
        assert "column-count: 2" in rule
        assert "column-gap: 7mm" in rule

    def test_column_fill_balances_last_page(self) -> None:
        rule = _css_rule(CSS_TEMPLATE, ".section-body")
        assert "column-fill: auto" in rule

    def test_visual_anchors_span_all_columns(self) -> None:
        """Exhibits/KPI strips/insight/implication boxes must be full-bleed —
        squeezed into a 68mm column they would read as sidebars."""
        for selector in (
            ".exhibit",
            ".kpi-strip",
            ".key-insight-box",
            ".implication-box",
            ".callout",
        ):
            m = re.search(
                re.escape(selector) + r"[^}]*column-span: all;", CSS_TEMPLATE
            )
            assert m is not None, f"{selector} missing column-span: all"

    def test_headings_not_orphaned_in_columns(self) -> None:
        assert re.search(
            r"\.section-body h3, \.section-body h4 \{[^}]*break-after: avoid",
            CSS_TEMPLATE,
        )

    def test_html_wraps_body_in_section_body(self) -> None:
        assert '<div class="section-body">' in HTML_TEMPLATE

    def test_body_no_longer_wrapped_in_no_break(self) -> None:
        """The old .no-break wrapper tried to keep a whole 2000-word body on
        one page — impossible, silently ignored, and incompatible with
        columns."""
        assert re.search(
            r'<div class="no-break">\s*\{\{ section\.body', HTML_TEMPLATE
        ) is None

    def test_page_margins_benchmark_like(self) -> None:
        """The BCG benchmark measures L 36pt · R 35pt. A 40mm binding margin
        (the old frame) cannot resolve a two-column measure to 52-60 chars;
        assert the frame never drifts back."""
        m = re.search(r"@page \{.*?size: A4;.*?margin: ([0-9.mm ]+);", CSS_TEMPLATE, re.DOTALL)
        assert m is not None
        top, right, bottom, left = (
            float(v.replace("mm", "")) for v in m.group(1).split()
        )
        assert left <= 20, f"left margin {left}mm too wide for two-column measure"
        assert right <= 20, f"right margin {right}mm too wide for two-column measure"
        assert left >= right, "binding allowance must be on the left, not the right"


class TestRenderedTwoColumnMeasure:
    """The audit's exit criterion measured on a real render of the production
    template path (same payload as tools/audit_render_probe.py)."""

    @pytest.fixture(scope="class")
    def rendered_pdf(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        pytest.importorskip("weasyprint")
        pytest.importorskip("fitz")
        import audit_render_probe

        from jinja2 import BaseLoader, Environment
        from weasyprint import HTML

        from hyperion.agents.delivery.presentation_designer import PDF_PALETTE
        from hyperion.output.render import TemplateRenderer

        payload = audit_render_probe.build_payload()
        env = Environment(loader=BaseLoader(), autoescape=True)
        env.filters["md_to_html"] = TemplateRenderer()._markdown_to_html
        env.filters["clean_dict_repr"] = lambda v: str(v) if v else ""
        html = env.from_string(HTML_TEMPLATE).render(
            css_content=CSS_TEMPLATE,
            palette=PDF_PALETTE,
            risk_analysis_html="<p>No risk analysis available.</p>",
            appendix_sources_html="<p>No sources.</p>",
            **payload,
        )
        out = tmp_path_factory.mktemp("pdf") / "two_column_test.pdf"
        HTML(string=html, base_url=str(ROOT)).write_pdf(str(out))
        return out

    @staticmethod
    def _measure(pdf_path: Path) -> tuple[list[int], int]:
        import fitz

        line_chars: list[int] = []
        bands: set[int] = set()
        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                for b in page.get_text("dict")["blocks"]:
                    if b["type"] != 0:
                        continue
                    for line in b["lines"]:
                        spans = [s for s in line["spans"] if 9.0 <= s["size"] <= 11.0]
                        text = "".join(s["text"] for s in spans).strip()
                        if len(text) < 25:
                            continue
                        line_chars.append(len(text))
                        x0 = min(s["bbox"][0] for s in spans)
                        bands.add(0 if x0 < 297.6 else 1)
        return line_chars, len(bands)

    def test_two_column_bands(self, rendered_pdf: Path) -> None:
        _, bands = self._measure(rendered_pdf)
        assert bands == 2

    def test_median_chars_per_line_in_benchmark_band(self, rendered_pdf: Path) -> None:
        line_chars, _ = self._measure(rendered_pdf)
        assert line_chars, "no body-measure lines found"
        median = statistics.median(line_chars)
        assert 52 <= median <= 60, f"median {median} chars/line outside 52-60"

    def test_no_wall_of_text_lines(self, rendered_pdf: Path) -> None:
        """p90 must stay inside the band too — a handful of 87-char lines is
        the single-column wall of text sneaking back."""
        line_chars, _ = self._measure(rendered_pdf)
        p90 = sorted(line_chars)[int(len(line_chars) * 0.9)]
        assert p90 <= 64, f"p90 {p90} chars/line — wide single-column lines remain"
