"""Repro: render the CURRENT cover HTML to PDF and measure page-1 geometry.

Run inside the WSL deployment (weasyprint available):
    .venv/bin/python scripts/repro_cover.py <outdir>
Prints:
  - renderer used (weasyprint/playwright)
  - page 1 content bbox vs the 595.28x841.89pt A4 trim box
  - whether the cover is full-bleed (bbox == trim) or inset (white ring)
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import datetime, timezone  # noqa: E402

from hyperion.agents.delivery.presentation_designer import (  # noqa: E402
    CSS_TEMPLATE,
    HTML_TEMPLATE,
    PDF_PALETTE,
)
from hyperion.schemas.models import (  # noqa: E402
    ConfidenceLevel,
    FinalReport,
    Recommendation,
)


def build_report() -> FinalReport:
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
        generated_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
    )


def main() -> None:
    outdir = Path(sys.argv[1] if len(sys.argv) > 1 else ".")
    outdir.mkdir(parents=True, exist_ok=True)
    report = build_report()

    # Render the Jinja template exactly like the Presentation Designer does.
    from hyperion.output.render import TemplateRenderer
    from jinja2 import Environment, BaseLoader

    env = Environment(loader=BaseLoader())
    # NOTE: CSS_TEMPLATE is already str.format(**PDF_PALETTE)-expanded at
    # module import (presentation_designer.py:1082). Do NOT format again.
    css = CSS_TEMPLATE
    _tr = TemplateRenderer()
    env.filters["md_to_html"] = _tr._markdown_to_html
    env.filters["clean_dict_repr"] = _tr._clean_dict_repr
    html = env.from_string(HTML_TEMPLATE).render(
        report=report,
        css_content=css,
        palette=PDF_PALETTE,
        cover_image=None,
        section_images=[],
        charts=[],
        toc_entries=[],
        appendix_sources_html="",
        endnotes_html="",
        risk_analysis_html="",
    )
    html_path = outdir / "cover_current.html"
    html_path.write_text(html, encoding="utf-8")

    from hyperion.output.render import PDFRenderer

    pdf_path = outdir / "cover_current.pdf"
    renderer = PDFRenderer()
    result = renderer.render_pdf(
        html=html_path.read_text(encoding="utf-8"),
        output_path=str(pdf_path),
    )
    print("success:", result.success)
    print("error:", getattr(result, "error", None))
    print("warnings:", result.warnings)

    import fitz

    doc = fitz.open(pdf_path)
    print("pages:", len(doc))
    page = doc[0]
    rect = page.rect
    print("page rect:", rect)
    # Content bbox: union of text + image blocks that touch the page.
    blocks = page.get_text("blocks") + page.get_images(full=True)
    if page.get_images(full=True):
        for img in page.get_images(full=True):
            try:
                r = page.get_image_rects(img[0])
                for rr in r:
                    print("image rect:", rr)
            except Exception:
                pass
    words = page.get_text("words")
    if words:
        x0 = min(w[0] for w in words)
        y0 = min(w[1] for w in words)
        x1 = max(w[2] for w in words)
        y1 = max(w[3] for w in words)
        print(f"text bbox: ({x0:.1f},{y0:.1f})-({x1:.1f},{y1:.1f})")
        print(f"  left gap: {x0:.1f}pt  right gap: {rect.width - x1:.1f}pt  top gap: {y0:.1f}pt  bottom gap: {rect.height - y1:.1f}pt")
    doc.close()


if __name__ == "__main__":
    main()
