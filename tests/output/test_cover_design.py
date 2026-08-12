"""OVERHAUL4 COVER — front-page design audit regression lock.

The audit (design brief, 2026-08-12) demanded, for the report cover:
  1. a hero visual in the top ~65% that dissolves into the charcoal plate
     the type sits on (never dead-black empty space),
  2. zero margin — the cover bleeds edge-to-edge on every render path,
  3. the terracotta accent rule directly ABOVE the title, not floating in
     empty space,
  4. a clean metadata line ("August 2026 · MBB Engagement Report") instead
     of the raw engagement UUID, plus an inline confidence badge
     ("HIGH ●") right-aligned on the same row as the recommendation,
  5. real title weight (Source Sans 3 Bold is vendored; Instrument Serif
     has no bold face so a 600-weight there would be a smeared synthetic).

Plus one latent blocker discovered while reproducing the design state:
the render-time page audit expected CREAM corners on every page, but a
full-bleed cover is charcoal (or photo) at the trim edge — so NO cover
could ever pass the audit and reports were withheld (reports/ had only
.md files, zero PDFs). The audit now exempts the declared cover plate.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime

import fitz
import pytest
from jinja2 import BaseLoader, Environment

from hyperion.agents.delivery.presentation_designer import (
    CSS_TEMPLATE,
    HTML_TEMPLATE,
    PDF_PALETTE,
)
from hyperion.output.page_audit import PageAuditError, audit_pdf
from hyperion.output.render import PDFRenderer, TemplateRenderer
from hyperion.schemas.models import (
    ConfidenceLevel,
    FinalReport,
    Recommendation,
)

CREAM_RGB = (0xF5, 0xF4, 0xEE)
# PyMuPDF draw_rect wants 0..1 floats; 26/255 = #1A1A1A warm_charcoal.
CHARCOAL_FILL = (26 / 255, 26 / 255, 26 / 255)

_WORDS = [
    "lorem", "ipsum", "dolor", "sit", "amet", "consectetur", "adipiscing",
    "elit", "sed", "do", "eiusmod", "tempor", "incididunt", "ut", "labore",
    "et", "dolore", "magna", "aliqua", "ut", "enim", "ad", "minim", "veniam",
    "quis", "nostrud", "exercitation", "ullamco", "laboris", "nisi", "aliquip",
    "ex", "ea", "commodo", "consequat", "duis", "aute", "irure", "dolor", "in",
    "reprehenderit", "in", "voluptate", "velit", "esse", "cillum", "dolore",
    "eu", "fugiat", "nulla", "pariatur", "excepteur", "sint", "occaecat",
    "cupidatat", "non", "proident",
]


def _make_report() -> FinalReport:
    return FinalReport(
        engagement_id="eng.b4f10006c3fae",
        question="should india invest more in africa?",
        recommendation=Recommendation.CONDITIONAL,
        recommendation_rationale="Evidence is mixed.",
        critical_assumptions=["Stable trade policy."],
        confidence=ConfidenceLevel.HIGH,
        confidence_breakdown={},
        executive_summary="Mixed evidence.",
        key_findings=[],
        sections=[],
        total_sources=0,
        total_data_points=0,
        generated_at=datetime(2026, 8, 1, tzinfo=UTC),
    )


def _render_cover_html(report: FinalReport | None = None) -> str:
    """Render the designer template the way the production path does."""
    env = Environment(loader=BaseLoader())
    tr = TemplateRenderer()
    env.filters["md_to_html"] = tr._markdown_to_html
    env.filters["clean_dict_repr"] = tr._clean_dict_repr
    return env.from_string(HTML_TEMPLATE).render(
        report=report or _make_report(),
        css_content=CSS_TEMPLATE,
        palette=PDF_PALETTE,
        cover_image=None,
        section_images=[],
        charts=[],
        toc_entries=[],
        appendix_sources_html="",
        endnotes_html="",
        risk_analysis_html="",
    )


def _cover_block(html: str) -> str:
    start = html.find('<div class="cover')
    end = html.find('<div class="page-break at-a-glance"')
    assert start >= 0, "cover block not found in rendered HTML"
    return html[start:end]


def _css_rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{\{([^}]*)\}\}", css)
    if m:
        return m.group(1)
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


# ---------------------------------------------------------------------------
# 1. Metadata — no raw engagement UUID, professional sub-line, badge
# ---------------------------------------------------------------------------


def test_cover_meta_line_has_no_raw_engagement_id():
    html = _render_cover_html()
    cover = _cover_block(html)
    assert "b4f10006c3fae" not in cover, "raw engagement UUID leaked onto the cover"
    assert "Engagement {{" not in cover
    assert "MBB Engagement Report" in cover, (
        "professional sub-line 'August 2026 · MBB Engagement Report' missing"
    )


def test_cover_has_confidence_badge_inline():
    html = _render_cover_html()
    cover = _cover_block(html)
    assert 'class="cover-meta-row"' in cover
    assert 'class="cover-confidence"' in cover
    assert "HIGH" in cover and "\u25cf" in cover, (
        "confidence badge 'HIGH ●' missing from the recommendation row"
    )
    # Same row as the recommendation (flex row in the CSS).
    meta_rule = _css_rule(CSS_TEMPLATE, ".cover-meta-row")
    assert "display: flex" in meta_rule and "justify-content: space-between" in meta_rule


def test_cover_confidence_dot_is_terracotta():
    dot_rule = _css_rule(CSS_TEMPLATE, ".cover-confidence-dot")
    assert PDF_PALETTE["terracotta"] in dot_rule


# ---------------------------------------------------------------------------
# 2. Accent rule sits directly above the title
# ---------------------------------------------------------------------------


def test_cover_accent_rule_is_in_title_flow():
    html = _render_cover_html()
    cover = _cover_block(html)
    rule_idx = cover.find('class="cover-accent-rule"')
    h1_idx = cover.find("<h1>")
    assert rule_idx >= 0 and h1_idx >= 0
    assert rule_idx < h1_idx, "accent rule must come BEFORE the title"


def test_cover_accent_rule_no_longer_floats():
    rule = _css_rule(CSS_TEMPLATE, ".cover-accent-rule")
    assert "top: 120mm" not in rule, "the orphaned floating rule is back"
    assert "position: absolute" not in rule, "rule must be in-flow, not absolute"


# ---------------------------------------------------------------------------
# 3. Hero visual — photo overlay or designed gradient, never dead black
# ---------------------------------------------------------------------------


def test_cover_overlay_is_full_height_topdown_fade():
    overlay = _css_rule(CSS_TEMPLATE, ".cover-overlay")
    assert "height: 100%" in overlay, "overlay must span the full cover height"
    assert "to bottom" in overlay, "fade must run top-down (hero dissolves into plate)"
    assert "0%" in overlay and "100%" in overlay


def test_cover_typographic_has_designed_hero_gradient():
    hero = _css_rule(CSS_TEMPLATE, ".cover-hero")
    assert "radial-gradient" in hero, "no-image cover must carry a designed hero composition"
    assert "linear-gradient" in hero
    # The hero is only emitted when no photo made it through.
    html = _render_cover_html()
    assert 'class="cover-hero"' in _cover_block(html)
    assert 'class="cover-overlay"' not in _cover_block(html)


# ---------------------------------------------------------------------------
# 4. Title weight — real Source Sans 3 Bold, not a synthetic smear
# ---------------------------------------------------------------------------


def test_cover_title_uses_vendored_bold_face():
    h1_rule = _css_rule(CSS_TEMPLATE, ".cover-title h1")
    assert "Source Sans 3" in h1_rule
    assert "font-weight: 700" in h1_rule


# ---------------------------------------------------------------------------
# 5. Full bleed + audit — the cover plate is exempt from body-page checks
# ---------------------------------------------------------------------------


def test_corners_exempt_declared_cover_pages(tmp_path):
    """A full-bleed charcoal cover page fails the corner check UNLESS it is
    declared as a cover plate — this is the latent blocker that withheld
    every PDF since the corner check landed."""
    pdf = tmp_path / "charcoal_cover.pdf"
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(0, 0, 595, 842), fill=CHARCOAL_FILL, color=None)
    _insert_body_words(page)
    doc.save(str(pdf))
    doc.close()

    # Without the cover declaration: corner violation (the old behaviour).
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    assert "corner" in str(exc.value).lower()

    # With the cover declaration: the plate passes.
    result = audit_pdf(pdf, background_rgb=CREAM_RGB, cover_pages=frozenset({0}))
    assert result.passed


def test_cover_pages_still_audit_body_pages(tmp_path):
    """Exempting page 0 must not exempt the pages after it."""
    pdf = tmp_path / "cover_and_blank.pdf"
    doc = fitz.open()
    cover = doc.new_page(width=595, height=842)
    cover.draw_rect(fitz.Rect(0, 0, 595, 842), fill=CHARCOAL_FILL, color=None)
    doc.new_page(width=595, height=842)  # blank body page
    doc.save(str(pdf))
    doc.close()

    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB, cover_pages=frozenset({0}))
    msg = str(exc.value).lower()
    assert "page 2" in msg, "body pages must still be audited after the cover"


# ---------------------------------------------------------------------------
# 6. Playwright split — cover renders separately, zero-margin
# ---------------------------------------------------------------------------


def test_split_cover_detects_cover_and_body():
    renderer = PDFRenderer()
    html = _render_cover_html()
    split = renderer._split_cover(html)
    assert split is not None, "cover block must be detected"
    cover_html, body_html = split
    assert 'class="cover' in cover_html
    assert '<div class="page-break at-a-glance"' in body_html
    assert body_html.startswith('<div class="page-break at-a-glance"')


def test_split_cover_returns_none_without_cover():
    renderer = PDFRenderer()
    assert renderer._split_cover("<html><body><p>no cover here</p></body></html>") is None


def test_merge_pdfs_prepends_cover(tmp_path):
    """The Playwright two-pass merge must lay the cover ahead of the body
    and preserve both documents' pages."""
    cover_pdf = tmp_path / "cover.pdf"
    body_pdf = tmp_path / "body.pdf"
    merged_pdf = tmp_path / "merged.pdf"
    for path, label in ((cover_pdf, "COVER"), (body_pdf, "BODY")):
        doc = fitz.open()
        page = doc.new_page(width=200, height=200)
        page.insert_text((20, 20), label)
        doc.save(str(path))
        doc.close()

    assert PDFRenderer._merge_pdfs(str(cover_pdf), str(body_pdf), str(merged_pdf))
    doc = fitz.open(merged_pdf)
    assert len(doc) == 2
    assert doc[0].get_text().strip() == "COVER"
    assert doc[1].get_text().strip() == "BODY"
    doc.close()


def _insert_body_words(page: fitz.Page) -> None:
    """Insert enough words that word/fill checks would otherwise pass."""
    text = " ".join(_WORDS * 3)
    page.insert_textbox(
        fitz.Rect(40, 40, 555, 800), text, fontsize=10, fontname="helv"
    )
