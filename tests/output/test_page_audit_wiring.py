"""P2-G1 wiring: the page audit runs inside the render path and FAILS CLOSED.

Audit section 4 Phase 2.2: wire page_audit into render.py immediately after
PDF bytes exist; a failing audit must raise (mark the render failed), never
warn. WeasyPrint is not installed in this sandbox, so the PDF bytes are
produced by a stubbed writer and the audit verdict comes from the real
audit_pdf on a real (synthetic) PDF.
"""

from __future__ import annotations

import fitz
import pytest

from hyperion.output.page_audit import PageAuditError, audit_pdf
from hyperion.output.render import PDFRenderer

CREAM = (0xF5, 0xF4, 0xEE)
_CREAM_FILL = tuple(c / 255 for c in CREAM)


def _dejavu_or_platform_font() -> str:
    """DejaVu on Linux; fall back to a Windows font (Arial carries U+2014)."""
    from pathlib import Path

    candidates = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for candidate in candidates:
        if Path(candidate).is_file():
            return candidate
    raise RuntimeError("no usable TTF font for synthetic PDF tests")


_DEJAVU = _dejavu_or_platform_font()


def _make_pdf(path, *, with_em_dash: bool) -> None:
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    # DejaVu carries the U+2014 glyph; the built-in Helvetica substitutes '?'
    # so a dash test would silently pass without it.
    page.insert_font(fontname="dvs", fontfile=_DEJAVU)
    # 640 filler words per column keeps median ink fill above the page audit's
    # 45% floor with DejaVu (Linux) OR Arial (Windows) — font metrics differ,
    # and the audit contract must not depend on which platform font resolved.
    words = " ".join(f"w{i}" for i in range(640))
    page.insert_textbox(fitz.Rect(40, 40, 290, h - 30), words, fontsize=9, fontname="dvs")
    col2 = " ".join(f"c{i}" for i in range(640))
    if with_em_dash:
        col2 += " grows — fast"
    page.insert_textbox(fitz.Rect(305, 40, 555, h - 30), col2, fontsize=9, fontname="dvs")
    doc.save(str(path))
    doc.close()


@pytest.fixture
def renderer(tmp_path, monkeypatch):
    r = PDFRenderer()
    monkeypatch.setattr(r, "_reports_dir", tmp_path)
    return r


def test_render_pdf_fails_closed_when_audit_raises(renderer, tmp_path, monkeypatch):
    """A rendered PDF containing a client-visible em dash must NOT be
    delivered as a successful render: render_pdf marks the result failed."""
    clean_pdf = tmp_path / "audit_target.pdf"
    _make_pdf(clean_pdf, with_em_dash=True)

    class _FakeHTML:
        def __init__(self, string, base_url=None):
            pass

        def write_pdf(self, path, stylesheets=None):
            _make_pdf(path, with_em_dash=True)

    class _FakeCSS:
        def __init__(self, string):
            pass

    monkeypatch.setattr(renderer, "_get_weasyprint", lambda: (_FakeHTML, _FakeCSS))
    monkeypatch.setattr(renderer, "_render_pdf_playwright", lambda *a, **k: False)
    monkeypatch.setattr(renderer, "_apply_pdf_post_pass", lambda *a, **k: None)

    result = renderer.render_pdf("<html><body>report</body></html>",
                                 output_path=str(tmp_path / "out.pdf"))
    assert not result.success, "audit violation must fail the render, not warn"
    assert any("page audit" in w.lower() for w in result.warnings + [result.error or ""])


def test_render_pdf_succeeds_when_audit_passes(renderer, tmp_path, monkeypatch):
    class _FakeHTML:
        def __init__(self, string, base_url=None):
            pass

        def write_pdf(self, path, stylesheets=None):
            _make_pdf(path, with_em_dash=False)

    class _FakeCSS:
        def __init__(self, string):
            pass

    monkeypatch.setattr(renderer, "_get_weasyprint", lambda: (_FakeHTML, _FakeCSS))
    monkeypatch.setattr(renderer, "_render_pdf_playwright", lambda *a, **k: False)
    monkeypatch.setattr(renderer, "_apply_pdf_post_pass", lambda *a, **k: None)

    result = renderer.render_pdf("<html><body>report</body></html>",
                                 output_path=str(tmp_path / "out.pdf"))
    assert result.success
    assert not any("page audit" in w.lower() for w in result.warnings)


def test_audit_pdf_itself_raises_on_em_dash(tmp_path):
    pdf = tmp_path / "dash.pdf"
    _make_pdf(pdf, with_em_dash=True)
    with pytest.raises(PageAuditError):
        audit_pdf(pdf, background_rgb=CREAM)
