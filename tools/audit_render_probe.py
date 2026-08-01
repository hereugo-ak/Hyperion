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


def _probe_sources(i: int) -> list:
    """Real `Source` models for chapter `i`.

    Fix 4.5. Note the third entry: every chapter repeats the SAME url. The
    endnote builder de-duplicates by url *within* a chapter, so this shared url
    must appear once per chapter but never twice in one — which is only
    checkable if the fixture actually contains a duplicate.
    """
    from hyperion.schemas.models import Source, SourceCredibility

    return [
        Source(
            id=f"src-{i}-1",
            title=f"Grid-Scale Storage Cost Curve Review, Chapter {i}",
            url=f"https://example.gov/storage/cost-curve/{i}",
            credibility=SourceCredibility.GOVERNMENT,
        ),
        Source(
            id=f"src-{i}-2",
            # Ampersand and angle brackets on purpose: this is the escaping
            # regression that `_build_appendix_sources_html` had before 4.5.
            title=f"Rate Design & Interconnection <Queue> Analysis {i}",
            url=f"https://example.org/rate-design?zone={i}&mode=full",
            credibility=SourceCredibility.INDUSTRY_REPORT,
        ),
        Source(
            id=f"src-{i}-dup",
            title="Shared Methodology Note (cited by every chapter)",
            url="https://example.org/shared-methodology",
            credibility=SourceCredibility.PEER_REVIEWED,
        ),
        Source(
            id=f"src-{i}-dup2",
            title="Shared Methodology Note (cited by every chapter)",
            url="https://example.org/shared-methodology",
            credibility=SourceCredibility.PEER_REVIEWED,
        ),
    ]


def _probe_quality_score():
    """A real `QualityScore` — `dimensions` is a LIST, which is the point.

    The first draft of `_build_technical_appendix_html` guarded the dimension
    table with `isinstance(dimensions, dict)`. That guard is false for the real
    model, so the table would have rendered on no report ever. A fixture that
    omits `quality_score` entirely cannot catch that; this one can.
    """
    from hyperion.schemas.models import QualityDimension, QualityScore

    # `dimension_id` is a QualityDimensionName ENUM and `score` is an int
    # constrained to 1..5 — both discovered by the model rejecting a first
    # draft that used a free-string id and fractional scores. Worth recording:
    # the fixture was wrong in a way that only the real validator could catch,
    # which is the same reason the probe feeds real models instead of stubs.
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id="evidence_sufficiency",
                name="Evidence sufficiency",
                score=4,
                weight=0.25,
                feedback="Adequate source coverage per claim.",
                critical=True,
            ),
            QualityDimension(
                dimension_id="analytical_depth",
                name="Analytical depth",
                score=3,
                weight=0.30,
                feedback="Sensitivity analysis is thin on the downside case.",
            ),
            QualityDimension(
                dimension_id="contradiction_resolution",
                name="Contradiction resolution",
                score=4,
                weight=0.25,
                feedback="One of two contradictions remains unreconciled.",
            ),
        ],
        total_score=4.1,
        threshold=4.0,
        approved=True,
        iteration=2,
        gaps=[
            "Hourly zone-level dispatch data unavailable below quarterly grain.",
            "No primary interviews with interconnection queue operators.",
        ],
    )


def _probe_contradictions() -> list:
    """Real `Contradiction` models.

    Fields are `finding_a`/`finding_b`, NOT `description`/`topic`. The first
    draft of the appendix builder read the latter and fell back to
    `str(item)` — which would have printed a raw pydantic repr into a
    client-facing PDF. The probe now measures that this cannot happen.
    """
    from hyperion.schemas.models import Contradiction, ContradictionType

    return [
        Contradiction(
            id="contra-1",
            agent_a="market_analyst",
            agent_b="financial_analyst",
            finding_a="Four zones clear the arbitrage spread at $78/kWh.",
            finding_b="Only two zones clear once curtailment risk is priced.",
            contradiction_type=ContradictionType.DATA_CONFLICT,
            resolved=False,
        ),
        Contradiction(
            id="contra-2",
            agent_a="regulatory_analyst",
            agent_b="market_analyst",
            finding_a="Interconnection queue clears within 30 months.",
            finding_b="Queue precedent implies 42 months in the two target zones.",
            contradiction_type=ContradictionType.INTERPRETATION_CONFLICT,
            resolution="Adopted the 42-month figure; the 30-month base case "
            "relied on a pre-2023 queue reform assumption.",
            resolved=True,
        ),
    ]


def _probe_fact_check():
    """A real `FactCheckReport`.

    Correct field names are `total_claims_checked` / `verified_count`, not
    `claims_checked` / `claims_verified`. The draft read the wrong pair, got
    `None` for both, and skipped the entire block silently.
    """
    from hyperion.schemas.models import FactCheckReport

    return FactCheckReport(
        claims=[],
        total_claims_checked=48,
        verified_count=41,
        unverified_count=5,
        contradicted_count=2,
        verification_rate=0.854,
        hallucinated_citation_count=0,
        evidence_chain_break_count=1,
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
            # Fix 4.5: real `Source` models, not `[]`. With an empty list the
            # endnote apparatus renders its honest-emptiness fallback, and the
            # probe would report "Endnotes present" for a page containing only
            # an apology. Two sources per chapter, and one URL deliberately
            # duplicated across chapters so the per-chapter de-duplication is
            # measured rather than assumed.
            sources=_probe_sources(i),
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
            # ── Fix 4.5 ──
            # These four fields existed on FinalReport from the start and none
            # of them reached the PDF: the system graded itself and threw the
            # scorecard away. They are populated here with REAL models so the
            # technical appendix is measured against the real schema. Three
            # wrong field names were caught exactly this way.
            recommendation_rationale=(
                "Two of eleven zones clear on current pack pricing, and both "
                "clear with margin. That is enough to justify a staged entry "
                "but not a platform commitment."
            ),
            quality_score=_probe_quality_score(),
            confidence_breakdown={
                "market_sizing": V("high"),
                "unit_economics": V("medium"),
                "regulatory_exposure": V("low"),
            },
            contradictions=_probe_contradictions(),
            fact_check_report=_probe_fact_check(),
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
    from jinja2 import BaseLoader, Environment

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

    # Fix 4.5: drive the REAL builders, not literal stubs. A probe that feeds
    # "<p>No sources.</p>" cannot detect a broken endnote apparatus, which is
    # precisely the kind of green-measurement-over-broken-output this audit
    # exists to prevent. `report` is the same object the template renders.
    from hyperion.agents.delivery.presentation_designer import PresentationDesigner

    designer = PresentationDesigner.__new__(PresentationDesigner)
    report = payload["report"]

    html = tpl.render(
        css_content=CSS_TEMPLATE,
        palette=PDF_PALETTE,
        risk_analysis_html="<p>No risk analysis available.</p>",
        appendix_sources_html=designer._build_appendix_sources_html(report),
        endnotes_html=designer._build_endnotes_html(report),
        technical_appendix_html=designer._build_technical_appendix_html(report),
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

    page_texts = [doc[i].get_text() for i in range(doc.page_count)]
    full_text = "\n".join(page_texts)

    # ── Fix 4.5 instrumentation ──
    # These are computed PER PAGE, not over `full_text`, because the first
    # version of this metric block was measured over the whole document and was
    # wrong twice — in both cases reading as healthier than reality:
    #
    #  * `"Recommendation" in full_text` scored 1/4 labels. The glance labels are
    #    uppercased by CSS `text-transform`, so the literal never matched; the
    #    one hit was the word "Confidence" on the *Technical Appendix* page. The
    #    metric would therefore have reported a non-zero score with the entire
    #    At-a-glance grid deleted.
    #  * the endnote regex run over `full_text` counted the At-a-glance
    #    "what we found" <ol> as endnotes, inflating 21 to 24.
    #
    # A measurement that cannot distinguish the thing it names from an unrelated
    # page elsewhere in the document is not a measurement. Scoping fixes both.
    def _page_index(marker: str) -> int:
        """First page whose text contains `marker`, else -1.

        The TOC also lists every one of these headings, so the search skips any
        page that looks like the contents page — otherwise every section would
        be "found" on page 3 and the metric would pass with all real sections
        missing.
        """
        for idx, text in enumerate(page_texts):
            if "Table of Contents" in text:
                continue
            if marker in text:
                return idx
        return -1

    glance_idx = _page_index("At a Glance")
    endnotes_idx = _page_index("Endnotes")
    tech_idx = _page_index("Technical Appendix")

    # W-09: the at-a-glance Confidence cell was internal telemetry and is
    # removed from the client page; the remaining three labels are the
    # contract. The old 4-label expectation is preserved in the golden file
    # history, not here.
    glance_labels = ("RECOMMENDATION", "EVIDENCE BASE", "ANALYSIS DEPTH")
    glance_text = page_texts[glance_idx] if glance_idx >= 0 else ""
    # Labels are uppercased by CSS, so compare uppercase.
    glance_upper = glance_text.upper()

    # Endnote pages are contiguous from the "Endnotes" heading up to the
    # Technical Appendix, so only that span is scanned for numbered entries.
    if endnotes_idx >= 0:
        end_stop = tech_idx if tech_idx > endnotes_idx else len(page_texts)
        endnote_text = "\n".join(page_texts[endnotes_idx:end_stop])
    else:
        endnote_text = ""

    front_back_matter = {
        "at_a_glance_page": glance_idx + 1 if glance_idx >= 0 else 0,
        "endnotes_page": endnotes_idx + 1 if endnotes_idx >= 0 else 0,
        "technical_appendix_page": tech_idx + 1 if tech_idx >= 0 else 0,
        # Presence of a heading is necessary but not sufficient — a heading over
        # an empty page is the failure mode — so each section also reports how
        # much content it actually carried.
        "glance_labels_present": sum(1 for lab in glance_labels if lab in glance_upper),
        "glance_words": len(glance_text.split()),
        "endnote_entries": len(re.findall(r"(?m)^\s*\d{1,3}\.\s+\S", endnote_text)),
        # W-09: the technical appendix is no longer a client page; its
        # content moved to the operator telemetry artifact. A technical
        # appendix page in the client PDF is now a DEFECT, so the metric
        # counts down (0 = clean).
        "technical_appendix_sections": (
            0
            if tech_idx < 0
            else sum(
                1
                for heading in (
                    "QUALITY ASSESSMENT",
                    "CONFIDENCE BY DIMENSION",
                    "CONTRADICTIONS",
                    "FACT CHECK",
                    "LIMITATIONS",
                )
                if heading in "\n".join(page_texts[tech_idx : tech_idx + 3]).upper()
            )
        ),
        # At-a-glance must precede the table of contents, as in MGI. Ordering is
        # part of the fix, so it is measured rather than eyeballed.
        "glance_precedes_toc": (
            glance_idx >= 0
            and any("Table of Contents" in t for t in page_texts)
            and glance_idx
            < next(i for i, t in enumerate(page_texts) if "Table of Contents" in t)
        ),
    }

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
        # ── Fix 4.5 exit criteria: MBB front/back matter ──
        # Presence of the heading is necessary but not sufficient — a heading
        # over an empty page is the failure mode. So each section is measured by
        # whether it carried real content through to the PDF, not by whether the
        # template mentions it.
        "front_back_matter": front_back_matter,
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
