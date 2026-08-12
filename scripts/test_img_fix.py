"""Verify the section-image occlusion fix candidates."""
import fitz

IMG = "/tmp/occl/section_img.png"
BODY = (
    "The question of whether India should deepen its investments in Africa "
    "cannot be answered without rigorous examination of the trade and "
    "capital dynamics that govern the corridor. "
    * 40
)


def render(tag: str, figure_html: str) -> None:
    html = f"""<!DOCTYPE html><html><head><style>
      body {{ font-family: sans-serif; }}
      .section-body {{ column-count: 2; column-gap: 7mm; }}
      .section-plate {{ column-span: all; width: 100%; margin: 0 0 10px 0; }}
      .section-plate img {{ width: 100%; max-height: 62mm; object-fit: cover; display: block; }}
    </style></head><body><div class="section-body">
      {figure_html}
      <p>{BODY}</p>
    </div></body></html>"""
    from weasyprint import HTML

    HTML(string=html, base_url="/tmp").write_pdf(f"/tmp/occl/{tag}.pdf")
    doc = fitz.open(f"/tmp/occl/{tag}.pdf")
    page = doc[0]
    imgs = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
    blocks = [fitz.Rect(b[:4]) for b in page.get_text("blocks") if b[4].strip()]
    occ = 0.0
    for ir in imgs:
        for blk in blocks:
            inter = ir & blk
            if not inter.is_empty and inter.get_area() >= 1.0:
                occ += inter.get_area()
    r = [round(v, 1) for v in imgs[0]] if imgs else "NONE"
    print(f"{tag:12s} | img rect: {r} | occlusion pt2: {occ:.1f}")
    doc.close()


base = f'<figure class="section-plate"><img src="{IMG}"><figcaption>Source: X</figcaption></figure>'
wrapped = (
    f'<figure class="section-plate">'
    f'<div style="height:62mm; overflow:hidden;"><img src="{IMG}"></div>'
    f"<figcaption>Source: X</figcaption></figure>"
)
render("base", base)
render("wrapped", wrapped)
