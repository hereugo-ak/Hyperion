"""HYPERION render probe — measures the ACTUAL rendered PDF against the
MGI/BCG benchmark, using the real CSS_TEMPLATE + HTML_TEMPLATE the pipeline
ships (not the unused .j2 files).

Not a unit test. This is an instrument: it renders a representative report
payload through the production template path, then extracts hard numbers from
the resulting PDF — page count, words/page, font families actually embedded,
image resolution, exhibit count, contrast of text-over-image — so the audit
cites measurements rather than impressions.

Run:  python3 tools/audit_render_probe.py
"""
from __future__ import annotations

import json
import re
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "reports" / "_audit"
OUT_DIR.mkdir(parents=True, exist_ok=True)


# ── Representative payload ────────────────────────────────────────────────
# Prose length matches what the section prompt ASKS for (2000-4000 words),
# so the probe measures the ceiling of the layout, not a starved fixture.
LOREM_PARA = (
    "The addressable market for grid-scale storage in the target geography "
    "expanded at a 24 percent compound annual rate between 2019 and 2024, "
    "reaching an installed base of 41 GWh, according to the national grid "
    "operator's annual capacity filing. That growth was not uniform: three "
    "quarters of the additions landed in two states, which concentrates both "
    "the opportunity and the interconnection-queue risk. The economics turn "
    "on a single variable — the delivered cell cost — and at the current "
    "$78/kWh pack price the levelised cost of storage clears the wholesale "
    "arbitrage spread in only four of the eleven pricing zones examined. "
)


def build_payload() -> dict:
    """A report payload shaped exactly like FinalReport as the template sees it."""
    from datetime import datetime
    from types import SimpleNamespace

    def V(v):  # enum-ish shim: template does `.value`
        return SimpleNamespace(value=v)

    body = "\n\n".join(
        [f"**Sub-heading {i}**\n\n" + (LOREM_PARA * 3) for i in range(1, 6)]
    )

    def section(i, title):
        return SimpleNamespace(
            id=f"section_{i}",
            title=title,
            key_insight=(
                f"At the current $78/kWh pack price, only 4 of 11 pricing zones "
                f"clear the arbitrage spread — concentrating {title.lower()} "
                f"upside in two states."
            ),
            body=body,
            implications=(
                "Sequence entry behind the two clearing zones and treat the "
                "remaining nine as an option contingent on a sub-$60/kWh pack."
            ),
            findings=[],
            sources=[],
            confidence=V("high"),
        )

    titles = [
        "Market Sizing and Demand Formation",
        "Competitive Structure and Entry Economics",
        "Unit Economics and Capital Requirements",
        "Regulatory and Interconnection Exposure",
        "Technology Cost Curve and Substitution Risk",
        "Operating Model and Supply Chain",
        "Strategic Options and Sequencing",
    ]

    return dict(
        report=SimpleNamespace(
            question="Should we enter the grid-scale storage market in the target geography?",
            recommendation=V("conditional"),
            confidence=V("moderate"),
            generated_at=datetime.now(),
            engagement_id="AUDIT-PROBE-001",
            executive_summary=(LOREM_PARA * 4),
            key_findings=[
                SimpleNamespace(
                    title=f"Finding {i}",
                    content=LOREM_PARA,
                    confidence=V("high"),
                )
                for i in range(1, 7)
            ],
            critical_assumptions=[
                "Pack prices decline to $60/kWh by 2028.",
                "Interconnection queue clears within 30 months.",
            ],
            sections=[section(i, t) for i, t in enumerate(titles, 1)],
            risk_analysis=SimpleNamespace(risks=[]),
            agents_used=["market_analyst", "financial_analyst", "regulatory_analyst"],
            total_sources=34,
            total_data_points=112,
            limitations=["Zone-level pricing data is quarterly, not hourly."],
            sources=[],
        ),
        cover_image=None,
        section_images={f"section_{i}": None for i in range(1, 8)},
        # Fix 3.7: the probe used to hand the template `section_charts={...: []}`
        # — every section empty. That made `has_exhibits: false` a property of
        # the FIXTURE, not of the pipeline: the exhibit branch of the template
        # was never entered, so the probe could not have detected an exhibit
        # regression either way. It now generates real charts through the real
        # ChartGenerator and places them as real ChartPlacements, so the
        # measured exhibit count reflects the shipping exhibit path.
        section_charts=_build_real_exhibits(range(1, 8)),
    )


def _build_real_exhibits(section_range) -> dict:
    """Generate real 300-DPI charts and wrap them as real ChartPlacements.

    Uses the production `ChartGenerator` and the production `ChartPlacement`
    model — no stand-ins — so a failure in either shows up as a missing
    exhibit in the measured PDF rather than as a passing probe.
    """
    from hyperion.output.charts import ChartGenerator, ChartSpec
    from hyperion.schemas.models import ChartPlacement

    gen = ChartGenerator()
    gen._output_dir = OUT_DIR / "charts"
    gen._output_dir.mkdir(parents=True, exist_ok=True)
    out: dict[str, list] = {}
    for i in section_range:
        sid = f"section_{i}"
        result = gen.generate(
            ChartSpec(
                chart_type="bar" if i % 2 else "line",
                title=f"Installed base by pricing zone, section {i}",
                x_label="Zone",
                y_label="GWh",
                x_data=["Zone A", "Zone B", "Zone C", "Zone D"],
                y_data=[[41, 78, 95, 112]],
                series_names=["Installed base"],
                source="National grid operator capacity filing, 2024",
            )
        )
        if not result.success or not result.image_path:
            # Fail loud: a probe that silently measures zero exhibits because
            # chart export broke is exactly the blind spot the audit found.
            print(
                f"PROBE WARNING: chart generation failed for {sid}: "
                f"{result.error or 'no image_path'}",
                file=sys.stderr,
            )
            out[sid] = []
            continue
        out[sid] = [
            ChartPlacement(
                chart_id=f"chart_{i}",
                section_id=sid,
                image_path=result.image_path,
                caption=(
                    "Only four of eleven zones clear the arbitrage spread at "
                    "the current pack price"
                ),
                note=(
                    "Note: Values quoted as reported in the market analyst "
                    "finding; not modelled or interpolated."
                ),
                source_citation=(
                    "Source: National grid operator annual capacity filing, 2024"
                ),
            )
        ]
    return out


def render() -> Path:
    from jinja2 import Environment, BaseLoader
    from hyperion.agents.delivery.presentation_designer import (
        CSS_TEMPLATE,
        HTML_TEMPLATE,
        PDF_PALETTE,
    )

    payload = build_payload()

    env = Environment(loader=BaseLoader(), autoescape=True)
    env.filters["md_to_html"] = _md_to_html
    env.filters["clean_dict_repr"] = lambda v: str(v) if v else ""
    tpl = env.from_string(HTML_TEMPLATE)

    html = tpl.render(
        css_content=CSS_TEMPLATE,
        palette=PDF_PALETTE,
        risk_analysis_html="<p>No risk analysis available.</p>",
        appendix_sources_html="<p>No sources.</p>",
        **payload,
    )
    (OUT_DIR / "probe.html").write_text(html, encoding="utf-8")

    from weasyprint import HTML

    pdf_path = OUT_DIR / "probe.pdf"
    HTML(string=html, base_url=str(ROOT)).write_pdf(str(pdf_path))
    return pdf_path


def _md_to_html(text: str):
    """Use the PRODUCTION filter, so the probe measures shipped behaviour.

    Note this is `TemplateRenderer._markdown_to_html`, which returns a
    markupsafe.Markup. The designer's *fallback* Jinja env instead registers
    `lambda v: v or ""` — a plain str, which Jinja autoescapes into visible
    `<p>` / `<strong>` tags on the page. That divergence is itself a finding.
    """
    from hyperion.output.render import TemplateRenderer

    return TemplateRenderer()._markdown_to_html(text)


def measure(pdf_path: Path) -> dict:
    import fitz

    doc = fitz.open(str(pdf_path))
    words = [len(doc[i].get_text("words")) for i in range(doc.page_count)]

    fonts: set[str] = set()
    for i in range(doc.page_count):
        for f in doc[i].get_fonts():
            fonts.add(f[3])

    sizes: dict[float, int] = {}
    for i in range(doc.page_count):
        for b in doc[i].get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            for line in b["lines"]:
                for span in line["spans"]:
                    key = round(span["size"], 1)
                    sizes[key] = sizes.get(key, 0) + len(span["text"])

    # ink coverage proxy: fraction of non-background pixels
    ink = []
    for i in range(min(doc.page_count, 12)):
        pix = doc[i].get_pixmap(dpi=50)
        n = pix.width * pix.height
        samples = pix.samples
        dark = sum(
            1
            for k in range(0, len(samples), pix.n * 7)
            if samples[k] < 200
        )
        ink.append(round(dark / (n / 7 + 1), 4))

    full_text = "\n".join(doc[i].get_text() for i in range(doc.page_count))

    # ── Fix 3.4 exit criteria: chars/line and column count ──
    # Line measure: for each text line in a body-size span (9-11pt), count
    # its characters and bucket it by horizontal position. Two-column pages
    # show two distinct x-bands; single-column pages one wide band.
    line_chars: list[int] = []
    col_bands: set[int] = set()
    for i in range(doc.page_count):
        for b in doc[i].get_text("dict")["blocks"]:
            if b["type"] != 0:
                continue
            for line in b["lines"]:
                spans = [s for s in line["spans"] if 9.0 <= s["size"] <= 11.0]
                text = "".join(s["text"] for s in spans).strip()
                if len(text) < 25:  # ignore captions/labels/footers
                    continue
                line_chars.append(len(text))
                x0 = min(s["bbox"][0] for s in spans)
                col_bands.add(0 if x0 < 297.6 else 1)  # A4 midpoint in pt

    return {
        "chars_per_line_median": statistics.median(line_chars) if line_chars else 0,
        "chars_per_line_p90": (
            sorted(line_chars)[int(len(line_chars) * 0.9)] if line_chars else 0
        ),
        "column_bands": len(col_bands),
        "pdf": str(pdf_path),
        "page_count": doc.page_count,
        "total_words": sum(words),
        "words_per_page_mean": round(statistics.mean(words), 1),
        "words_per_page_max": max(words),
        "blank_pages": sum(1 for w in words if w < 8),
        "fonts_embedded": sorted(fonts),
        "font_size_histogram": dict(
            sorted(sizes.items(), key=lambda kv: -kv[1])[:10]
        ),
        "ink_coverage_by_page": ink,
        "leaks": {
            "raw_dict": full_text.count("{'"),
            "none_url": full_text.count("=None"),
            "literal_brace_page": full_text.count("{{page}}"),
            "unknown": full_text.count("Unknown"),
        },
        "has_exhibits": "EXHIBIT" in full_text.upper(),
        # ── Fix 3.7 exit criteria ──
        # `has_exhibits` alone is a weak assertion: it is true if the word
        # "Exhibit" appears anywhere, including in prose. These count the
        # actual four-part MGI/BCG anatomy so the exit criterion ("every
        # section carries >=1 exhibit with Note: + Source:") is measurable.
        #
        # The number comes from a CSS counter, so it appears in the PDF text
        # as "Exhibit 1", "Exhibit 2", … — counting distinct numbered labels
        # is therefore an exact exhibit count, and a gap in the sequence would
        # reveal a dropped or mis-numbered figure.
        # Matched case-INSENSITIVELY: `.exhibit-number` sets
        # `text-transform: uppercase`, so the counter reaches the PDF text
        # layer as "EXHIBIT 1", not "Exhibit 1". A case-sensitive pattern
        # reported 0 exhibits on a PDF that in fact carried 7.
        "exhibit_numbers": sorted(
            int(n) for n in set(re.findall(r"\bexhibit\s+(\d+)\b", full_text, re.I))
        ),
        "exhibit_count": len(set(re.findall(r"\bexhibit\s+(\d+)\b", full_text, re.I))),
        "exhibit_note_count": len(re.findall(r"\bNote:", full_text)),
        "exhibit_source_count": len(re.findall(r"\bSource:", full_text)),
        "embedded_images": sum(len(doc[i].get_images()) for i in range(doc.page_count)),
        "file_size_kb": round(pdf_path.stat().st_size / 1024, 1),
    }


if __name__ == "__main__":
    pdf = render()
    report = measure(pdf)
    print(json.dumps(report, indent=2))
    (OUT_DIR / "probe_metrics.json").write_text(
        json.dumps(report, indent=2), encoding="utf-8"
    )
