"""Verify the OVERHAUL4 COVER fix on the Playwright path.

Forces PDFRenderer onto the Playwright/Chromium fallback (the path Windows
uses, where WeasyPrint GTK natives are unavailable) and checks:
  - the cover is rendered as its OWN zero-margin, furniture-free page,
  - body pages keep the margins + running header/footer,
  - pikepdf merges cover + body (page count == cover + body pages),
  - the merged artifact's cover page is full-bleed (no white ring).
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from datetime import UTC, datetime  # noqa: E402

from jinja2 import BaseLoader, Environment  # noqa: E402

from hyperion.agents.delivery.presentation_designer import (  # noqa: E402
    CSS_TEMPLATE,
    HTML_TEMPLATE,
    PDF_PALETTE,
)
from hyperion.output.render import PDFRenderer  # noqa: E402
from hyperion.schemas.models import (  # noqa: E402
    ConfidenceLevel,
    FinalReport,
    Recommendation,
)


def main() -> None:
    outdir = Path("/tmp/pw_cover")
    outdir.mkdir(parents=True, exist_ok=True)

    report = FinalReport(
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
    env = Environment(loader=BaseLoader())
    from hyperion.output.render import TemplateRenderer

    tr = TemplateRenderer()
    env.filters["md_to_html"] = tr._markdown_to_html
    env.filters["clean_dict_repr"] = tr._clean_dict_repr
    html = env.from_string(HTML_TEMPLATE).render(
        report=report,
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

    renderer = PDFRenderer()

    # Force the Playwright fallback exactly like Windows (WeasyPrint fails).
    def _boom():
        raise OSError("GTK natives unavailable (simulated Windows)")

    renderer._get_weasyprint = _boom  # type: ignore[method-assign]

    pdf_path = outdir / "pw_cover.pdf"
    result = renderer.render_pdf(html=html, output_path=str(pdf_path))
    print("success:", result.success)
    print("warnings:", [w for w in result.warnings if "Playwright" in w or "audit" in w])

    import fitz

    doc = fitz.open(pdf_path)
    print("pages:", len(doc))
    page = doc[0]
    words = page.get_text("words")
    print("page1 words:", len(words))
    print("page1 text sample:", " | ".join(w[4] for w in words[:8]))
    pix = page.get_pixmap(dpi=30)
    w, h = pix.width, pix.height
    corners = [
        ("TL", pix.pixel(1, 1)),
        ("TR", pix.pixel(w - 2, 1)),
        ("BL", pix.pixel(1, h - 2)),
        ("BR", pix.pixel(w - 2, h - 2)),
    ]
    print("page1 corners:", corners)
    white_corners = sum(1 for _, c in corners if c[0] > 240 and c[1] > 240 and c[2] > 240)
    print("FULL BLEED:", "YES" if white_corners == 0 else f"NO ({white_corners} white corners)")

    if len(doc) > 1:
        p2 = doc[1]
        p2words = p2.get_text("words")
        x0 = min((x[0] for x in p2words), default=0)
        print("page2 left edge (pt):", round(x0, 1), "(body margin ~113pt = 40mm)")
    doc.close()


if __name__ == "__main__":
    main()
