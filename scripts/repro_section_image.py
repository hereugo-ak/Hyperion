"""Repro: section image placement inside the 2-column body.

Renders a section with an image (section-plate) + prose using the REAL
CSS_TEMPLATE, then measures whether the image occludes any text block
(the render-time audit's occlusion check) and where the image box sits
relative to the two column boxes.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


from hyperion.agents.delivery.presentation_designer import (  # noqa: E402
    CSS_TEMPLATE,
)


def main() -> None:
    outdir = Path("/tmp/occl")
    outdir.mkdir(parents=True, exist_ok=True)

    # 1) a real image (fitz-generated, wide so it stresses the column box)
    import fitz

    img = fitz.open()
    page = img.new_page(width=1000, height=600)
    page.draw_rect(fitz.Rect(0, 0, 1000, 600), fill=(0.3, 0.6, 0.85), color=None)
    page.insert_text((350, 300), "SECTION IMAGE", fontsize=40)
    # pixmap.save writes a REAL PNG (page.save writes a PDF regardless of
    # extension — the earlier repro embedded PDF bytes as image/png).
    page.get_pixmap(dpi=72).save(str(outdir / "section_img.png"))
    img.close()

    body_paras = "\n\n".join(
        f"Paragraph {i}: " + ("The question of whether India should deepen its "
        "investments in Africa cannot be answered without a rigorous "
        "examination of the underlying trade, capital, and political "
        "dynamics that govern the corridor. " * 8)
        for i in range(6)
    )

    html = f"""<!DOCTYPE html><html lang="en"><head><meta charset="UTF-8">
    <style>{CSS_TEMPLATE}</style></head><body>
    <div class="section-body">
        <figure class="section-plate">
            <img src="{outdir}/section_img.png" alt="Circuit board">
            <figcaption>Source: Unsplash via Adi Goldstein</figcaption>
        </figure>
        <h3>Vendor Landscape: A Place</h3>
        {body_paras}
    </div>
    </body></html>"""

    (outdir / "occl.html").write_text(html, encoding="utf-8")

    from hyperion.output.render import PDFRenderer

    renderer = PDFRenderer()
    result = renderer.render_pdf(
        html=html, output_path=str(outdir / "occl.pdf")
    )
    print("success:", result.success)
    print("violations:", getattr(result, "audit_violations", None))

    # 2) measure: image rects vs text blocks per page
    import glob

    pdf = str(outdir / "occl.pdf")
    if not Path(pdf).exists():
        rej = sorted(glob.glob(str(outdir / "_rejected" / "*.rejected.pdf")))
        if rej:
            pdf = rej[-1]
    doc = fitz.open(pdf)
    print("pages:", len(doc))
    for pno in range(len(doc)):
        page = doc[pno]
        imgs = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
        if not imgs:
            continue
        blocks = [fitz.Rect(b[:4]) for b in page.get_text("blocks") if b[4].strip()]
        print(f"--- page {pno+1}: {len(imgs)} image(s), {len(blocks)} text blocks")
        for ir in imgs:
            print("  image bbox:", [round(v, 1) for v in ir])
        for blk in blocks:
            for ir in imgs:
                inter = ir & blk
                if not inter.is_empty and inter.get_area() >= 1.0:
                    print(f"  OCCLUSION: text block {[round(v,1) for v in blk]} "
                          f"overlaps image by {inter.get_area():.1f} pt2")
    doc.close()


if __name__ == "__main__":
    main()
