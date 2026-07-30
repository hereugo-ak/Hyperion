"""T-01..T-06, T-08: render-time page audit (P2-08, P2-24).

The module under test (``hyperion/output/page_audit.py``) does not exist yet;
these tests define the contract from audit §2 P2-08's assertion table and §5.
The two audit PDFs are regression fixtures in ``tests/fixtures/pdf/`` — when
they are present the fixture tests assert the audit RAISES on them (they fail
20+ gates today); when absent, fixture tests skip and synthetic PDFs drive the
same assertions.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF
import pytest

from hyperion.output.page_audit import (
    PageAuditError,
    PageAuditResult,
    audit_pdf,
    extract_pdf_text,
    scan_text_integrity,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "pdf"
FIXTURES = [
    FIXTURE_DIR / "should_tesla_enter_in_india.pdf",
    FIXTURE_DIR / "should_six_sense_mobilityhexense_lab_starts_manufacturing_ra.pdf",
]

CREAM_RGB = (0xF5, 0xF4, 0xEE)  # designer :106 "cream": "#F5F4EE"
_CREAM_FILL = tuple(c / 255 for c in CREAM_RGB)  # PyMuPDF fill wants 0-1 floats


# ---------------------------------------------------------------------------
# Synthetic PDF builders (PyMuPDF hand-constructed pages)
# ---------------------------------------------------------------------------


def _body_words(n: int) -> str:
    """n words of printable prose without banned substrings."""
    return " ".join(f"prose{i}" for i in range(n))


def _insert_text_line(page, rect, text, fontsize=10):
    spare = page.insert_textbox(rect, text, fontsize=fontsize, align=0)
    if spare < 0:  # PyMuPDF inserts NOTHING on overflow; fail loudly instead
        raise ValueError(f"textbox overflow: spare={spare:.1f} for {len(text.split())} words")


def make_clean_body_pdf(path: Path, pages: int = 4) -> Path:
    """A PDF that should PASS the audit: cream canvas, balanced two-column
    body pages with >= 90 words, content inside trim, no banned text."""
    doc = fitz.open()
    w, h = 595, 842  # A4-ish points
    for page_idx in range(pages):
        page = doc.new_page(width=w, height=h)
        page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
        words_per_col = 250
        col_w = (w - 90) / 2
        _insert_text_line(
            page,
            fitz.Rect(40, 40, 40 + col_w, h - 30),
            " ".join(f"p{page_idx}a{i}" for i in range(words_per_col)),
        )
        _insert_text_line(
            page,
            fitz.Rect(50 + col_w, 40, 50 + 2 * col_w, h - 30),
            " ".join(f"p{page_idx}b{i}" for i in range(words_per_col)),
        )
    doc.save(str(path))
    doc.close()
    return path


def make_occluded_pdf(path: Path) -> Path:
    """T-01: an image bbox overlapping a text block -> audit must raise."""
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    _insert_text_line(page, fitz.Rect(40, 40, 300, h - 30), _body_words(180))
    # 1x1 pixmap stretched into a rect overlapping the text block
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 1, 1))
    pix.set_rect(fitz.IRect(0, 0, 1, 1), (200, 100, 60))
    page.insert_image(fitz.Rect(50, 50, 280, 300), pixmap=pix)
    doc.save(str(path))
    doc.close()
    return path


def make_orphan_page_pdf(path: Path) -> Path:
    """T-02: a body page with 25 words and near-zero ink fill -> must raise."""
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    _insert_text_line(page, fitz.Rect(40, 40, 300, 120), _body_words(25))
    doc.save(str(path))
    doc.close()
    return path


def make_column_imbalance_pdf(path: Path) -> Path:
    """T-02b: 300 words left column, 0 right column -> must raise."""
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    _insert_text_line(page, fitz.Rect(40, 40, 280, h - 30), _body_words(280))
    doc.save(str(path))
    doc.close()
    return path


def make_off_canvas_pdf(path: Path) -> Path:
    """T-03: white canvas instead of theme background -> must raise."""
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    _insert_text_line(page, fitz.Rect(40, 40, 300, h - 30), _body_words(150))
    doc.save(str(path))
    doc.close()
    return path


def make_outside_trim_pdf(path: Path) -> Path:
    """T-04: text drawn at negative x (outside the trim box) -> must raise."""
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    page.insert_text(fitz.Point(500, 100), "right edge overflow test string here", fontsize=12)
    _insert_text_line(page, fitz.Rect(40, 140, 300, h - 30), _body_words(150))
    doc.save(str(path))
    doc.close()
    return path


def make_banned_text_pdf(path: Path) -> Path:
    """T-06/P2-24: leaked object repr + em dash in extracted text -> raise."""
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    page.insert_text(
        fitz.Point(40, 100),
        "TAM: {'name': 'TAM (Triangulated)', 'value': 'Parse error'}",
        fontsize=11,
    )
    page.insert_text(fitz.Point(40, 140), "Revenue will grow \u2014 substantially", fontsize=11)
    _insert_text_line(page, fitz.Rect(40, 180, 300, h - 30), _body_words(150))
    doc.save(str(path))
    doc.close()
    return path


def make_duplicate_paragraph_pdf(path: Path) -> Path:
    """T-08: the same >= 12-word paragraph appears on two pages -> raise."""
    para = " ".join(f"duplicate{i}" for i in range(20))
    doc = fitz.open()
    w, h = 595, 842
    for _ in range(2):
        page = doc.new_page(width=w, height=h)
        page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
        _insert_text_line(page, fitz.Rect(40, 40, 300, h - 30), para + "\n" + _body_words(120))
    doc.save(str(path))
    doc.close()
    return path


# ---------------------------------------------------------------------------
# T-01 image/text occlusion
# ---------------------------------------------------------------------------


def test_clean_body_pdf_passes(tmp_path):
    pdf = make_clean_body_pdf(tmp_path / "clean.pdf")
    result = audit_pdf(pdf, background_rgb=CREAM_RGB)
    assert isinstance(result, PageAuditResult)
    assert result.passed


def test_image_text_occlusion_raises(tmp_path):
    pdf = make_occluded_pdf(tmp_path / "occluded.pdf")
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    assert "occlu" in str(exc.value).lower() or "image" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# T-02 fill / orphan pages
# ---------------------------------------------------------------------------


def test_orphan_page_raises(tmp_path):
    pdf = make_orphan_page_pdf(tmp_path / "orphan.pdf")
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    msg = str(exc.value).lower()
    assert "words" in msg or "fill" in msg


# ---------------------------------------------------------------------------
# T-02b column balance
# ---------------------------------------------------------------------------


def test_column_imbalance_raises(tmp_path):
    pdf = make_column_imbalance_pdf(tmp_path / "imbalance.pdf")
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    assert "column" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# T-03 canvas colour
# ---------------------------------------------------------------------------


def test_wrong_canvas_colour_raises(tmp_path):
    pdf = make_off_canvas_pdf(tmp_path / "white.pdf")
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    msg = str(exc.value).lower()
    assert "corner" in msg or "canvas" in msg or "background" in msg


# ---------------------------------------------------------------------------
# T-04 trim box containment
# ---------------------------------------------------------------------------


def test_content_outside_trim_raises(tmp_path):
    pdf = make_outside_trim_pdf(tmp_path / "trim.pdf")
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    assert "trim" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# T-06 text hygiene + P2-24 integrity scan on extracted text
# ---------------------------------------------------------------------------


def test_banned_text_in_pdf_raises(tmp_path):
    pdf = make_banned_text_pdf(tmp_path / "banned.pdf")
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    msg = str(exc.value)
    assert "{'name'" in msg or "\u2014" in msg or "banned" in msg.lower()


def test_scan_text_integrity_reports_leaks():
    leaked = "DCF Valuation: {'name': 'DCF Valuation', 'value': '$12.5B'}"
    hits = scan_text_integrity(leaked)
    assert any("{'" in h for h in hits)


def test_scan_text_integrity_reports_dashes_and_placeholders():
    hits = scan_text_integrity(
        "Growth \u2014 strong \u2013 really. Insufficient evidence to state implications."
    )
    joined = " ".join(hits)
    assert "\u2014" in joined and "\u2013" in joined
    assert "Insufficient evidence to state implications" in joined


def test_scan_text_integrity_clean_text_passes():
    assert scan_text_integrity("A plain sentence about market growth and revenue.") == []


def test_extract_pdf_text(tmp_path):
    pdf = make_clean_body_pdf(tmp_path / "clean.pdf")
    text = extract_pdf_text(pdf)
    assert "p0a0" in text


# ---------------------------------------------------------------------------
# T-08 duplicate paragraph detection
# ---------------------------------------------------------------------------


def test_duplicate_paragraph_raises(tmp_path):
    pdf = make_duplicate_paragraph_pdf(tmp_path / "dup.pdf")
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    assert "duplicate" in str(exc.value).lower()


# ---------------------------------------------------------------------------
# Regression fixtures: the two audit PDFs (skip-if-missing in this sandbox).
# They currently fail 20+ gates — the audit MUST raise on each of them.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("fixture", FIXTURES, ids=[p.name for p in FIXTURES])
def test_audit_raises_on_audit_fixtures(fixture):
    if not fixture.exists():
        pytest.skip(f"regression fixture not present in this sandbox: {fixture.name}")
    with pytest.raises(PageAuditError):
        audit_pdf(fixture, background_rgb=CREAM_RGB)


# ---------------------------------------------------------------------------
# T-05 TOC fidelity: stated page numbers must match the page the heading is
# drawn on; phantom entries (no heading anywhere) are flagged.
# ---------------------------------------------------------------------------


def make_toc_pdf(path, *, wrong_number: bool) -> Path:
    doc = fitz.open()
    w, h = 595, 842
    toc = doc.new_page(width=w, height=h)
    toc.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    stated = 4 if wrong_number else 2
    toc.insert_text(fitz.Point(40, 60), "Contents", fontsize=16)
    toc.insert_text(fitz.Point(40, 120), f"Market Landscape ........ {stated}", fontsize=11)
    body = doc.new_page(width=w, height=h)
    body.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    body.insert_text(fitz.Point(40, 60), "Market Landscape", fontsize=16)
    _insert_text_line(body, fitz.Rect(40, 100, 300, h - 30), _body_words(140))
    doc.save(str(path))
    doc.close()
    return path


def test_toc_wrong_page_number_raises(tmp_path):
    pdf = make_toc_pdf(tmp_path / "toc_wrong.pdf", wrong_number=True)
    with pytest.raises(PageAuditError) as exc:
        audit_pdf(pdf, background_rgb=CREAM_RGB)
    assert "toc" in str(exc.value).lower() or "stated page" in str(exc.value).lower()


def test_toc_correct_page_number_no_toc_violation(tmp_path):
    pdf = make_toc_pdf(tmp_path / "toc_right.pdf", wrong_number=False)
    result = audit_pdf(pdf, background_rgb=CREAM_RGB, fail_closed=False)
    assert not any("TOC entry" in v for v in result.violations)
