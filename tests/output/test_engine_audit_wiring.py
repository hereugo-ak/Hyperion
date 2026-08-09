"""P2-08 wiring, second call site: RenderEngine._verify_pdf runs the page
audit and refuses the PDF when it fails (audit section 2 P2-08 names both
render.py:845-850 and render_engine.py:971-975).
"""

from __future__ import annotations

import fitz

from hyperion.agents.delivery.render_engine import RenderEngine

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


def _make_pdf(path, *, em_dash: bool) -> None:
    doc = fitz.open()
    w, h = 595, 842
    page = doc.new_page(width=w, height=h)
    page.draw_rect(fitz.Rect(0, 0, w, h), fill=_CREAM_FILL, color=None)
    page.insert_font(fontname="dvs", fontfile=_DEJAVU)
    # 640 filler words per column keeps median ink fill above the page audit's
    # 45% floor with DejaVu (Linux) OR Arial (Windows) — font metrics differ,
    # and the audit contract must not depend on which platform font resolved.
    col1 = " ".join(f"w{i}" for i in range(640))
    page.insert_textbox(fitz.Rect(40, 40, 290, h - 30), col1, fontsize=9, fontname="dvs")
    col2 = " ".join(f"c{i}" for i in range(640))
    if em_dash:
        col2 += " grows — fast"
    page.insert_textbox(fitz.Rect(305, 40, 555, h - 30), col2, fontsize=9, fontname="dvs")
    doc.save(str(path))
    doc.close()


def test_verify_pdf_refuses_when_page_audit_fails(tmp_path, monkeypatch):
    engine = RenderEngine()
    # Other legacy checks must not mask the audit verdict.
    monkeypatch.setattr(engine, "_verify_no_blank_pages", lambda p: (True, []))
    monkeypatch.setattr(engine, "_verify_no_orphaned_images", lambda p: (True, []))
    monkeypatch.setattr(engine, "_verify_fonts_embedded", lambda p: (True, ["Instrument Serif"]))
    monkeypatch.setattr(engine, "_verify_image_dpi", lambda p: True)
    monkeypatch.setattr(engine, "_get_page_count", lambda p: 1)

    pdf = tmp_path / "dash.pdf"
    _make_pdf(pdf, em_dash=True)
    all_passed, issues, details = engine._verify_pdf(str(pdf))
    assert not all_passed
    assert any("page audit" in i.lower() for i in issues)
    assert details.get("page_audit_passed") is False


def test_verify_pdf_passes_clean_pdf(tmp_path, monkeypatch):
    engine = RenderEngine()
    # Check 5's contract band (15-40 pages) is covered by its own tests; a
    # 1-page stub would mask the audit verdict behind it.
    import hyperion.output.page_budget as pb

    monkeypatch.setattr(
        pb, "page_count_verdict",
        lambda count, budget=None: pb.PageCountVerdict(
            page_count=count, passed=True, expected_min=1, expected_max=40,
            reason="stubbed within contract",
        ),
    )
    monkeypatch.setattr(engine, "_verify_no_blank_pages", lambda p: (True, []))
    monkeypatch.setattr(engine, "_verify_no_orphaned_images", lambda p: (True, []))
    monkeypatch.setattr(engine, "_verify_fonts_embedded", lambda p: (True, ["Instrument Serif"]))
    monkeypatch.setattr(engine, "_verify_image_dpi", lambda p: True)
    monkeypatch.setattr(engine, "_get_page_count", lambda p: 1)

    pdf = tmp_path / "clean.pdf"
    _make_pdf(pdf, em_dash=False)
    all_passed, issues, details = engine._verify_pdf(str(pdf))
    assert all_passed, f"clean PDF must pass: {issues}"
    assert details.get("page_audit_passed") is True
