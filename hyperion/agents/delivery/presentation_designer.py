"""
HYPERION Presentation Designer, Agent 19, the report layout designer.

This is NOT a generic "put content in a template" agent. This is a specialist
with 7 proprietary skills:

- Layout design: Design page layouts that follow the premium structure.
  Each page has a clear visual hierarchy: header → key insight → body →
  chart/image → implication.
- Typography: Apply the HYPERION typography system (Instrument Serif for
  headers, JetBrains Mono for body) consistently.
- Image placement: Place images according to the 5 image placement rules
  (see Section 6.3). No orphaned images, no blank pages.
- Print design: Ensure the PDF is print-ready: 300 DPI, embedded fonts,
  proper margins, no color bleeding.
- Page flow: Control page breaks to ensure no blank pages, no orphaned
  images, and no awkward section breaks. Use `page-break-inside: avoid`.
- Visual hierarchy: Use size, weight, and color to guide the reader's eye
  through the report. The most important content gets the most visual weight.
- White space management: Use white space deliberately, not as empty space,
  but as a design element that improves readability and focus.

It runs on STRONG tier (Nemotron 3 Super 120B) because layout design requires
strong reasoning, it must understand narrative flow, visual hierarchy, and
how to balance text and visuals on each page.

Model Tier: STRONG (Nemotron 3 Super 120B, layout design requires strong
reasoning about visual hierarchy and narrative flow)
Tools: Unsplash (search and select images for cover, section headers, and
       contextual illustrations),
       Plotly (receive chart specifications from Data Visualizer),
       Jinja2 (render the HTML template with report content and layout plan),
       WeasyPrint (generate the final PDF from HTML/CSS)
Sub-agents: 0 (delivery agent, doesn't spawn sub-agents)
Output: LayoutPlan (page-by-page layout, image selections, chart placements)

Methodology (§4.6, Agent 19):
1. Receive FinalReport from Synthesis Lead
2. Receive QualityScore from Quality Gate
3. Design layout plan (which content goes on which page)
4. Select Unsplash images for cover and section headers
5. Receive chart images from Data Visualizer
6. Render HTML template with Jinja2
7. Generate PDF with WeasyPrint
8. Post-process images with Pillow (via Render Engine)

What makes it the best version of itself:
It treats layout as design, not as formatting. It doesn't just dump content
into a template, it makes deliberate decisions about what goes on each page,
how to balance text and visuals, and how to guide the reader through the
narrative. It always ensures images are adjacent to their context text. It
never produces a blank page or an orphaned image.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import re
from html import escape as html_escape
from io import BytesIO
from pathlib import Path
from typing import Any

from fontTools import subset
from fontTools.ttLib import TTFont, TTLibError

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import Channel, MessageType
from hyperion.config import ModelTier
from hyperion.output.confidence import derive_confidence
from hyperion.output.images import ImageRelevanceGate
from hyperion.output.methodology import build_methodology
from hyperion.output.page_budget import PAGE_COUNT_MAX, PAGE_COUNT_MIN
from hyperion.router.budget import TaskUrgency
from hyperion.schemas.agents import (
    AgentName,
    AgentRole,
    AgentSpec,
    AgentState,
    SkillSpec,
    ToolName,
)
from hyperion.schemas.models import (
    AnalysisSection,
    ChartPlacement,
    ConfidenceLevel,
    FinalReport,
    ImageSelection,
    KeyFinding,
    LayoutPlan,
    PageLayout,
    PageType,
    QualityScore,
    VisualizationOutput,
)
from hyperion.schemas.narrative import ClientReport, write_telemetry_artifact

# Declared after the imports, not between them. It previously sat above the
# `hyperion.*` block, which made every one of those imports an E402 ("module
# level import not at top of file"), 7 findings from one misplaced line. Adding
# the page-budget import would have made it 8, so the line moved instead.
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# PDF Palette (§7.2, STRICT)
# ─────────────────────────────────────────────────────────────────────────────

PDF_PALETTE = {
    "warm_charcoal": "#1A1A1A",
    "cream": "#F5F4EE",
    "terracotta": "#C8704D",
    "sage": "#7C9885",
    "beige": "#E8E6DD",
    "warm_gray": "#8B8680",
    "deep_brown": "#3D3530",
    "alert_red": "#B5533C",
}

# Typography system (§7.4)
TYPOGRAPHY = {
    "header_font": "Instrument Serif",
    "body_font": "Source Sans 3",  # D24: professional sans, not monospace
    "cover_title_size": "36pt",
    "section_header_size": "22pt",
    "subsection_header_size": "14pt",
    "body_size": "10pt",
    "caption_size": "8pt",
    "key_insight_size": "11pt",
    "data_table_size": "9pt",
}

# Image placement rules (§6.3)
# 1. Every image has adjacent text context on the SAME page.
# 2. Cover image = full-bleed. Section images = 40% page width, right-aligned.
# 3. Images are topic-relevant, not generic stock.
# 4. All images processed through Pillow pipeline.
# 5. Charts are NEVER screenshots. Always Plotly → kaleido → PNG at scale=3.
# 6. No image is larger than 50% of the page height (except cover).
# 7. Every image has a caption with source attribution.

# Section-specific Unsplash search term templates (§5.4)
# Specific, relevant, not generic. "Modern boardroom meeting" not "business."
SECTION_IMAGE_SEARCH_TERMS: dict[str, str] = {
    # Agent name keys (match section.agent field directly)
    "market_analyst": "market analysis charts on screen",
    "competitive_intel": "modern corporate strategy meeting",
    "financial_analyst": "financial charts on screen",
    "risk_analyst": "risk management dashboard",
    "technology_analyst": "modern technology infrastructure",
    "operations_analyst": "modern factory operations",
    "regulatory_analyst": "government building columns",
    "sustainability_analyst": "green sustainable business",
    "consumer_insights": "consumer shopping retail",
    "ma_analyst": "corporate merger acquisition handshake",
    "innovation_analyst": "innovation technology lab",
    "strategy_analyst": "chess strategy pieces board",
    # Topic-based keys (for title matching fallback)
    "market_analysis": "market analysis charts on screen",
    "market": "market analysis charts on screen",
    "competitive": "modern corporate strategy meeting",
    "competitive_intelligence": "modern corporate strategy meeting",
    "financial": "financial charts on screen",
    "financial_analysis": "financial charts on screen",
    "risk": "risk management dashboard",
    "risk_analysis": "risk management dashboard",
    "technology": "modern technology infrastructure",
    "technology_analysis": "modern technology infrastructure",
    "operations": "modern factory operations",
    "operations_analysis": "modern factory operations",
    "regulatory": "government building columns",
    "regulatory_analysis": "government building columns",
    "sustainability": "green sustainable business",
    "sustainability_analysis": "green sustainable business",
    # D5.1b (F601): "consumer_insights" was listed twice in this one dict,
    # once above as an agent-name key (it is the literal
    # `AgentName.CONSUMER_INSIGHTS` value) and again here in the topic block.
    # Both mapped to the same term, so the duplicate was inert rather than a
    # live bug, but it is exactly the shape that silently overwrites a *differing*
    # value later. Only the agent-name entry is kept; "consumer" covers the
    # title-matching fallback.
    "consumer": "consumer shopping retail",
    "ma": "corporate merger acquisition handshake",
    "ma_analysis": "corporate merger acquisition handshake",
    "innovation": "innovation technology lab",
    "innovation_analysis": "innovation technology lab",
    "strategy": "chess strategy pieces board",
    "strategy_analysis": "chess strategy pieces board",
}

# Cover image search terms by question type
COVER_IMAGE_SEARCH_TERMS: dict[str, str] = {
    "market_entry": "city skyline aerial view",
    "ma": "corporate boardroom meeting",
    "competitive": "chess strategy pieces",
    "financial": "financial district skyline",
    "risk": "storm clouds over city",
    "technology": "circuit board macro",
    "general": "modern business abstract",
}


# ─────────────────────────────────────────────────────────────────────────────
# CSS Template (brand-compliant, §7.2 + §7.4)
# ─────────────────────────────────────────────────────────────────────────────

CSS_TEMPLATE = """\
/* ── Page geometry and running elements ────────────────────────────────────
   BUG HISTORY: the footer previously read
       content: "HYPERION · … · {{{{page}}}}";
   and the header  content: "{{{{section_title}}}}";
   Because CSS_TEMPLATE is passed through str.format(**PDF_PALETTE), the
   quadrupled braces collapse to the LITERAL two-brace text `{{page}}` in the
   emitted stylesheet. Nothing downstream substitutes it, it is not a Jinja
   variable (the CSS is not rendered through Jinja) and it is not CSS. So every
   single page of every report printed the characters "{{page}}" where the page
   number belonged. Verified by rendering to a real PDF and extracting text:
   18 literal occurrences across 9 pages.

   The fix uses genuine CSS paged-media features, which WeasyPrint implements:
     • counter(page) / counter(pages), the page number, computed by the
       renderer, so it cannot drift from reality.
     • string-set + string(), the running section title. `h2` sets a named
       string as it flows; the margin box echoes the most recent value, which
       is exactly the "current section" semantics a consulting report wants.

   D-03 CORRECTION, the previous sentence here claimed "Both degrade
   gracefully in Chromium/Playwright". That was FALSE for the margin boxes:
   Chromium's page.pdf() does NOT implement CSS paged-media margin boxes, so
   @top-center / @bottom-center are SILENTLY DROPPED, installing Playwright
   alone would yield a PDF with no running heads and no page numbers. The
   comment's own "unsupported string() yields empty" caveat was the tell: it
   described the content expression, not the box. When rendering through
   Playwright the running header and the `n / N` footer MUST come from
   page.pdf(header_template=..., footer_template=...) with explicit top/
   bottom margins (Chromium's <span class="pageNumber">/<span class=
   "totalPages"> substitution hooks), NOT from these margin boxes. These
   margin boxes remain correct for the WeasyPrint path, which is the one
   that honours them. (D-03) */
@page {{
    size: A4;
    /* P2-03: paint the margin boxes too. Without this, the 25mm/15mm/19mm
       margin ring stays white while the canvas is cream. */
    background: {cream};
    /* Fix 3.4: was 25mm 25mm 25mm 40mm (15mm binding allowance). With the
       two-column body that made each column 69mm ≈ 42 chars/line, far
       short of the 52-60 benchmark band. The BCG benchmark itself measures
       L 36pt · R 35pt margins (≈12.5mm): the wide-binding-margin page frame
       was simply incompatible with the two-column measure. Now 19mm left
       (4mm binding allowance over 15mm) · 15mm right → 176mm text width →
       84.5mm columns ≈ 53 chars/line at 10pt Source Sans 3 (1.58mm/char
       measured by the probe). */
    margin: 25mm 15mm 25mm 19mm;
    @bottom-center {{
        content: "HYPERION · many minds. one reading. · "
                 counter(page) " / " counter(pages);
        font-family: "JetBrains Mono", monospace;
        font-size: 8pt;
        color: {warm_gray};
    }}
    @top-center {{
        content: string(section-title);
        font-family: "Instrument Serif", serif;
        font-size: 10pt;
        color: {warm_gray};
    }}
}}

/* The cover carries no running header/footer, a title page with a page
   number and a repeated section name is the clearest tell of an automated
   document. Both benchmarks leave the cover clean.

   P2-07: full bleed is done the paged-media way, not with an overflowing
   transform. The first page (and the named `cover` page below) gets
   margin: 0 so the page content frame IS the 210mm x 297mm trim box, and
   .cover is sized to exactly that. Before this, the 210mm-wide cover box
   sat inside a (210 - 19 - 15)mm content frame and bled 328pt off the
   left edge - a PDF/A and print hazard the pikepdf post-pass cannot clip. */
@page :first {{
    margin: 0;
    @top-center {{ content: none; }}
    @bottom-center {{ content: none; }}
}}

/* .cover declares `page: cover`; the named page rule must zero the margins
   too, or the cover page keeps the body frame and the bleed returns. */
@page cover {{
    margin: 0;
    /* Named-page rules do not inherit the root canvas paint consistently
       across WeasyPrint and Chromium. Paint the trim box explicitly so a
       missing image still produces a full-bleed charcoal cover, never a white
       ring around an inset panel. */
    background: {warm_charcoal};
    @top-center {{ content: none; }}
    @bottom-center {{ content: none; }}
}}

/* Exhibit numbering counter. Reset once on the root so numbers run
   continuously across the whole document, which is what both MGI and BCG do
   (MGI: E1, E2 in the exec summary, then 1..n by chapter; BCG: a flat
   EXHIBIT 1..n). Generated by the document, never authored by an agent, so
   exhibits cannot be mis-numbered and insertions renumber automatically. */
/* P2-03: the background belongs on the ROOT element - CSS Backgrounds
   propagates a background to the page canvas only from the root. It used
   to sit on body, which is inset by the page margin, producing a cream
   panel floating on a white A4 sheet (white corners on every page). */
html {{ counter-reset: exhibit; background-color: {cream}; }}

/* ── Body typography ───────────────────────────────────────────────────────
   The body font WAS "JetBrains Mono", monospace. That single declaration was
   the largest gap between this output and an MGI/BCG deliverable: no
   consulting firm sets running prose in a monospace font. Monospace forces
   uniform character width, which destroys the word-shape cues fluent readers
   rely on, caps comfortable measure at ~55 characters, and reads as code or
   terminal output rather than as analysis.

   Note the codebase already *declared* the right answer:
   TYPOGRAPHY["body_font"] = "Source Sans 3" carries the comment
   "professional sans, not monospace". The CSS simply ignored it and
   hardcoded the mono stack. Monospace is now confined to where it is
   genuinely correct, tabular figures and data labels, where fixed advance
   width makes digits align in a column.

   `font-variant-numeric: oldstyle-nums` is deliberately NOT set on body:
   consulting prose is number-dense and lining figures read better inline. */
body {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 10pt;
    color: {warm_charcoal};
    /* Reset the browser/WeasyPrint UA default (normally 8px). Page margins
       are owned exclusively by @page; a body margin creates the visible white
       frame around every page and prevents the cover from reaching trim. */
    margin: 0;
    padding: 0;
    /* P2-03: transparent - the background lives on html (canvas
       propagation) and @page (margin boxes), never on body. */
    /* 1.6 was loose for a 10pt sans at this measure; 1.5 tightens the block
       into a more solid grey without crowding descenders. */
    line-height: 1.5;
    /* Consulting reports are read, not skimmed for keywords: hyphenation plus
       justification is what gives a benchmark page its even left-right block.
       Kept modest so no line breaks more than twice in a row. */
    text-align: justify;
    hyphens: auto;
    /* OVERHAUL4 fix (2026-08-12): WeasyPrint renders hyphenation breaks
       with U+2010, which Instrument Serif does not carry; any hyphenated
       serif span (the TOC is the live case) fell back to DejaVu-Serif, a
       forbidden embedded font per the golden baseline. Force the ASCII
       hyphen U+002D, which every vendored face has. */
    hyphenate-character: "-";
    -webkit-hyphens: auto;
    orphans: 3;
    widows: 3;
}}

h1, h2, h3, h4 {{
    font-family: "Instrument Serif", Georgia, serif;
    /* Instrument Serif ships Regular/Italic only, there is no bold weight
       (and only those two faces are vendored, fix 3.1). The UA stylesheet
       defaults headings to bold, which makes WeasyPrint SYNTHESIZE a
       smeared fake-bold from the Regular face. Hierarchy here comes from
       size (22-36pt display type), not weight, exactly how MGI/BCG set
       their serif heads. */
    font-weight: normal;
    color: {warm_charcoal};
    /* Headings are set tight: display type at 22-36pt needs negative
       tracking and sub-1.2 leading or it looks airy and amateur. */
    line-height: 1.15;
    letter-spacing: -0.01em;
    /* A heading must never be the last thing on a page. */
    page-break-after: avoid;
    text-align: left;
    hyphens: none;
}}

h1 {{ font-size: 36pt; }}  /* Cover title */

/* Section headers. The vertical space is asymmetric ON PURPOSE: far more
   above than below, so the heading binds visually to the text it introduces
   rather than floating between two blocks. This is the single most reliable
   whitespace cue for a deliberate-looking page. */
h2 {{
    font-size: 22pt;
    margin: 0 0 10pt 0;
    /* Feeds the @top-center running header. Set here (not hardcoded per
       section) so the header always reports the section actually on the page
       and can never disagree with the body. */
    string-set: section-title content();
}}

/* ── Section openers ───────────────────────────────────────────────────────
   Sections previously began with a bare h2 immediately followed by the key
   insight box, so a new section looked identical to a paragraph break. Both
   benchmarks announce a section: MGI with a letterspaced "C H A P T E R  O N E"
   eyebrow above the title, BCG with a full-width opener and generous space.

   The eyebrow number comes from Jinja's `loop.index`, i.e. from the document
   structure itself, so it can never disagree with the actual section order.

   The generous `padding-bottom` is the point of the whole block: whitespace is
   what signals a new movement in the document. It is set in cm rather than em
   so it does not scale with heading size. */
.section-opener {{
    padding-bottom: 0.7cm;
    margin-bottom: 0.5cm;
    page-break-after: avoid;
    break-after: avoid;
}}
.section-eyebrow {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 7.5pt;
    font-weight: 600;
    text-transform: uppercase;
    /* Wide tracking is what makes a short label read as a signpost rather than
       as small body text. MGI spaces this out dramatically. */
    letter-spacing: 0.22em;
    color: {terracotta};
    margin-bottom: 10pt;
    hyphens: none;
}}
/* A hairline under the title, not a heavy divider: it closes the opener
   without competing with the exhibit rules further down the page. */
.section-rule {{
    border-bottom: 0.5pt solid {warm_gray};
    margin-top: 12pt;
}}

/* Subsection headers were 14pt BOLD MONOSPACE, visually a code comment.
   Now small-caps sans: it reads as a label, sits quietly under the serif h2,
   and establishes a third level without competing with it. */
h3 {{
    font-size: 10.5pt;
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {terracotta};
    margin: 20pt 0 6pt 0;
}}

p {{ margin: 0 0 8pt 0; }}

/* Tabular figures: monospace belongs HERE, where fixed advance width makes
   digits line up in a column, and nowhere else. */
table, .kpi-value, .data-table, .chart-data-table {{
    font-variant-numeric: tabular-nums;
}}

.cover {{
    page: cover;
    margin: 0;
    padding: 0;
    position: relative;
    /* P2-07: exactly the A4 trim box, never a transform or relative width
       that can paint outside it. overflow: hidden stays as the belt and
       braces clip for absolutely-positioned children. */
    width: 210mm;
    height: 297mm;
    overflow: hidden;
}}

.cover-image {{
    width: 100%;
    height: 100%;
    object-fit: cover;
    position: absolute;
    top: 0;
    left: 0;
    z-index: 1;
}}

.cover-overlay {{
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    /* OVERHAUL4 COVER: the old plate was a 60%-tall bottom-up gradient,
       so the top ~70% of the cover was raw image (or, with no image,
       dead black). The design call is a hero that fills the top ~65%
       and dissolves into the solid charcoal plate the type sits on.
       Full-height, top-down: transparent at the trim edge, ~35% at 45%,
       and solid {warm_charcoal} from ~78% down (the title block always
       sits on the solid plate, never on busy image detail. */
    background: linear-gradient(
        to bottom,
        rgba(26,26,26,0) 0%,
        rgba(26,26,26,0.35) 45%,
        rgba(26,26,26,0.88) 72%,
        {warm_charcoal} 78%,
        {warm_charcoal} 100%
    );
    z-index: 2;
}}

.cover-title {{
    position: absolute;
    bottom: 46mm;
    left: 25mm;
    right: 25mm;
    color: {cream};
    z-index: 3;
}}

.cover-title h1 {{
    color: {cream};
    /* OVERHAUL4 COVER: title weight. Instrument Serif ships Regular/Italic
       only (a `font-weight: 600` there is a smeared synthetic. Source
       Sans 3 has a vendored Bold face (assets/fonts/SourceSans3-Bold.ttf),
       so the cover head gets real weight without synthesis. Sentence case
       comes from the question itself (never force-lowercased). */
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 34pt;
    font-weight: 700;
    line-height: 1.18;
    letter-spacing: -0.015em;
    margin: 0 0 14px 0;
    text-shadow: 0 2px 10px rgba(0,0,0,0.55);
    hyphens: none;
}}

.cover--typographic {{
    background-color: {warm_charcoal};
    height: 297mm;
}}

/* OVERHAUL4 COVER: when no photo made it through (no Unsplash key, failed
   search/download), the top of the cover is NOT empty black. A designed
   composition (a warm radial glow over charcoal, dissolving into the
   solid plate the type sits on, keeping the "hero + fade" anatomy of the
   photo cover without inventing a fake photograph. */
.cover-hero {{
    position: absolute;
    top: 0;
    left: 0;
    width: 100%;
    height: 100%;
    background:
        radial-gradient(ellipse 85% 60% at 68% 28%, rgba(200,112,77,0.30) 0%, rgba(200,112,77,0.12) 45%, rgba(200,112,77,0) 70%),
        linear-gradient(to bottom, #2B2320 0%, {warm_charcoal} 62%);
    z-index: 1;
}}

/* OVERHAUL4 COVER: the accent rule used to float at top: 120mm in empty
   space with nothing above it to justify it as a divider. It now sits
   directly above the title as the in-flow first child of .cover-title,
   on the solid plate the type block occupies. */
.cover-accent-rule {{
    width: 60mm;
    height: 3px;
    background-color: {terracotta};
    margin-bottom: 14px;
}}

/* OVERHAUL4 COVER: metadata row (the recommendation label left, the
   confidence badge right, on ONE baseline. The old cover put the raw
   engagement UUID in a sub-line that read as debug text; the new line is
   "August 2026 · MBB Engagement Report" and the confidence dot is a
   terracotta glyph, not the word "Confidence:" as prose. */
.cover-meta-row {{
    display: flex;
    align-items: baseline;
    justify-content: space-between;
    margin-bottom: 12px;
}}
.cover-recommendation {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 13pt;
    font-weight: 600;
    letter-spacing: 0.18em;
    text-transform: uppercase;
    color: {cream};
}}
.cover-confidence {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 11pt;
    font-weight: 600;
    letter-spacing: 0.12em;
    text-transform: uppercase;
    color: {cream};
}}
.cover-confidence-dot {{
    color: {terracotta};
    margin-left: 6px;
}}
.cover-date {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 10pt;
    color: {warm_gray};
    margin: 0;
}}

.key-insight-box {{
    background-color: {beige};
    border-left: 4px solid {terracotta};
    padding: 12px 16px;
    margin: 16px 0;
    font-size: 11pt;
}}

.implication-box {{
    background-color: #7C988520;  /* Sage with 12% opacity */
    border-left: 4px solid {sage};
    padding: 12px 16px;
    margin: 16px 0;
    font-size: 11pt;
    /* P2-04: the callout is the last child after the column block. With a
       full-height column block it fragmented onto its own page, alone -
       6 pages of report B measured 25 words at 2.0% ink. Never start a
       page with it, never split it. */
    break-before: avoid;
    page-break-before: avoid;
    break-inside: avoid;
    page-break-inside: avoid;
}}

/* ── KPI strip (BCG convention) ────────────────────────────────────────────
   BCG's sustainability report states a metric as a large number with a short
   descriptor UNDER it ("$600M+" / "Invested in societal impact in 2024"). Two
   properties make that work, and both were wrong here:

   1. LEFT alignment. The old cards were centred, so with five cards the
      numbers sat at five arbitrary horizontal positions and the eye had no
      line to travel down. Left-aligning puts every figure on a shared axis.

   2. NO hyphenation. `hyphens: auto` is inherited from body, and a centred
      ~3cm card is narrow enough that it fired: the real PDF printed
      "KEY FIND-INGS" and "CONFID-ENCE", a word broken across two lines
      inside a five-character label. Descriptors must never hyphenate, so
      hyphens/overflow-wrap are explicitly disabled and the label is allowed
      to wrap on whole words only.

   The beige fill and 3px terracotta cap are replaced by a single hairline
   rule above each figure. Filled boxes make a strip read as five buttons;
   benchmark KPI rows are held together by alignment and a shared rule, which
   is quieter and lets the numbers carry the emphasis. */
.kpi-strip {{
    display: flex;
    gap: 0;
    margin: 0.5cm 0 0.9cm 0;
    page-break-inside: avoid;
    break-inside: avoid;
    border-top: 0.5pt solid {warm_gray};
}}
.kpi-card {{
    flex: 1;
    padding: 9pt 12pt 0 0;
    text-align: left;
    /* Labels are short; a break inside one is always an error. */
    hyphens: none;
    -webkit-hyphens: none;
    overflow-wrap: normal;
    word-break: keep-all;
}}
.kpi-card + .kpi-card {{
    /* Separator between figures rather than around them. */
    border-left: 0.5pt solid {beige};
    padding-left: 12pt;
    margin-left: 0;
}}
.kpi-value {{
    font-family: "Instrument Serif", Georgia, serif;
    /* Up from 20pt: this is the one place in the document where a number is
       the message, and the benchmarks set these very large relative to body. */
    font-size: 26pt;
    color: {terracotta};
    line-height: 1.0;
    margin: 0;
    letter-spacing: -0.02em;
    font-variant-numeric: tabular-nums;
    hyphens: none;
}}
.kpi-label {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 7.5pt;
    font-weight: 600;
    color: {warm_gray};
    text-transform: uppercase;
    letter-spacing: 0.07em;
    line-height: 1.25;
    margin-top: 5pt;
    hyphens: none;
    text-align: left;
}}
.callout {{
    background-color: {beige};
    border-left: 4px solid {terracotta};
    padding: 12px 16px;
    margin: 0.4cm 0;
    font-size: 10pt;
    page-break-inside: avoid;
    break-inside: avoid;
}}
.callout--alert {{
    border-left-color: {alert_red};
    background-color: #B5533C10;
}}
.callout-title {{
    font-family: "Source Sans 3", sans-serif;
    font-weight: bold;
    font-size: 9pt;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: {terracotta};
    margin-bottom: 4px;
}}
.callout--alert .callout-title {{ color: {alert_red}; }}
.dashboard-grid {{
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 0.4cm;
    margin: 0.4cm 0;
    page-break-inside: avoid;
    break-inside: avoid;
}}
.dashboard-card {{
    background-color: {cream};
    border: 1px solid {beige};
    border-top: 3px solid {terracotta};
    padding: 10px 14px;
}}
.dashboard-card-title {{
    font-family: "Source Sans 3", sans-serif;
    font-weight: bold;
    font-size: 10pt;
    color: {warm_charcoal};
    margin-bottom: 4px;
}}
.dashboard-card-body {{
    font-family: "Source Sans 3", sans-serif;
    font-size: 9pt;
    color: {deep_brown};
    line-height: 1.4;
}}
.confidence-pill {{
    display: inline-block;
    font-family: "JetBrains Mono", monospace;
    font-size: 7pt;
    font-weight: bold;
    padding: 2px 8px;
    border-radius: 2px;
    text-transform: uppercase;
    margin-left: 4px;
}}
.confidence-pill--high {{ background-color: {sage}; color: {cream}; }}
.confidence-pill--medium {{ background-color: {terracotta}; color: {cream}; }}
.confidence-pill--low {{ background-color: {alert_red}; color: {cream}; }}
.recommendation-banner {{
    background-color: {deep_brown};
    color: {cream};
    padding: 12px 16px;
    margin: 0.3cm 0 0.5cm 0;
    text-align: center;
    page-break-inside: avoid;
    break-inside: avoid;
}}
.recommendation-banner .rec-label {{
    font-family: "Source Sans 3", sans-serif;
    font-size: 8pt;
    text-transform: uppercase;
    letter-spacing: 0.1em;
    color: {terracotta};
    margin-bottom: 2px;
}}
.recommendation-banner .rec-value {{
    font-family: "Instrument Serif", serif;
    font-size: 18pt;
    color: {cream};
}}

/* P2-02: the figure is a column-spanning band INSIDE the multicol
   .section-body, never a float. A float:right sibling of a column-count
   container is laid out in the containing block, not the column flow, so
   WeasyPrint composites the raster over column 2's full-width line boxes
   (2,773 pt2 of text occluded on every imaged section page of both
   audited reports). float adjacent to column-count is forbidden here. */
.section-plate {{
    column-span: all;
    width: 100%;
    margin: 0 0 10px 0;
    break-inside: avoid;
    page-break-inside: avoid;
}}

.section-plate img {{
    width: 100%;
    max-height: 62mm;
    object-fit: cover;
    display: block;
}}

.section-plate figcaption {{
    font-size: 8pt;
    color: {warm_gray};
    margin-top: 4px;
}}

/* ── Exhibit system ────────────────────────────────────────────────────────
   Audited directly against McKinsey MGI "The Next Big Arenas of Competition"
   (Oct 2024) and the BCG Annual Sustainability Report 2024. Both firms use the
   SAME four-part exhibit anatomy, and the pipeline previously had none of it:
   a grep for "Exhibit" across this file returned 0. Charts carried only a
   centred grey 8pt caption, which is the convention of a blog post.

   The four parts, in the order both benchmarks print them:

     1. NUMBER   "Exhibit 1" / "Exhibit E1", small, caps, tracked, accent
                 colour, on its OWN line above the title. MGI numbers exec
                 exhibits E1/E2 and chapter exhibits 1,2,3; BCG uses a flat
                 "EXHIBIT 1". Numbering must be automatic, never authored.
     2. TITLE    MGI writes a full-sentence TAKEAWAY ("The 12 arenas of today
                 exhibited outsize shuffle rates and significant growth in
                 share by market cap."). BCG uses a short label ("Our
                 Values"). MGI's convention is stronger for a consulting
                 deliverable, the reader gets the finding without decoding
                 the chart, so the title is set as a serif statement, left
                 aligned, at body-plus size rather than as a small grey
                 caption.
     3. FIGURE   the chart image itself.
     4. NOTE/SOURCE  MGI: "Note: Based on McKinsey Industry Classification...
                 Source: McKinsey Value Intelligence; McKinsey Global
                 Institute analysis". BCG: "Source: BCG analysis. Note: EV =
                 electric vehicle". Both are 7-8pt, left aligned under the
                 figure, note before source, separated from the figure by a
                 hairline rule.

   Everything below is left-aligned, not centred: centred captions read as
   editorial, and every exhibit in both benchmark documents is left-aligned
   to the text block. */
.exhibit {{
    margin: 18pt 0 20pt 0;
    page-break-inside: avoid;
    break-inside: avoid;
}}

/* Exhibit number. `counter-increment` means the number is generated by the
   document, so exhibits cannot be mis-numbered by an agent, and inserting or
   dropping one renumbers the rest automatically. */
.exhibit-number {{
    counter-increment: exhibit;
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 7.5pt;
    font-weight: 600;
    text-transform: uppercase;
    letter-spacing: 0.14em;
    color: {terracotta};
    margin: 0 0 3pt 0;
}}

.exhibit-number::before {{
    content: "Exhibit " counter(exhibit);
}}

/* The takeaway title. Serif, statement-length, tight leading, this is the
   line the reader is meant to remember. */
.exhibit-title {{
    font-family: "Instrument Serif", Georgia, serif;
    font-size: 12pt;
    line-height: 1.25;
    color: {warm_charcoal};
    margin: 0 0 10pt 0;
    text-align: left;
    hyphens: none;
}}

.exhibit-figure {{
    /* Full text-column width. The old .chart was 80% and centred, which left
       ragged asymmetric margins either side of every figure. */
    width: 100%;
    margin: 0;
}}

.exhibit-figure img {{ width: 100%; height: auto; display: block; }}

/* Note + source block. The hairline is what visually closes the exhibit and
   separates its metadata from the body text that follows. */
.exhibit-footer {{
    border-top: 0.5pt solid {beige};
    margin-top: 6pt;
    padding-top: 4pt;
    text-align: left;
    hyphens: none;
}}

.exhibit-note, .exhibit-source {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 7pt;
    line-height: 1.35;
    color: {warm_gray};
    margin: 0;
    text-align: left;
}}

/* "Note:" / "Source:" labels are italic in both benchmarks. */
.exhibit-note-label, .exhibit-source-label {{ font-style: italic; }}

/* Retained for backward compatibility: older layout plans may still emit
   .chart / .chart-caption. Kept visually consistent with .exhibit so a
   partially-migrated report does not look like two different documents. */
.chart {{
    width: 100%;
    margin: 18pt 0;
    page-break-inside: avoid;
}}

.chart-caption {{
    font-family: "Source Sans 3", "Helvetica Neue", Arial, sans-serif;
    font-size: 7pt;
    color: {warm_gray};
    text-align: left;
    margin-top: 5pt;
    padding-top: 4pt;
    border-top: 0.5pt solid {beige};
}}

.risk-matrix {{
    page-break-inside: avoid;
}}

.data-table {{
    font-size: 10pt;
    width: 100%;
    border-collapse: collapse;
}}

.data-table th {{
    background-color: {beige};
    color: {warm_charcoal};
    padding: 8px 12px;
    text-align: left;
    border-bottom: 2px solid {terracotta};
    font-family: "Instrument Serif", serif;
    /* UA <th> defaults to bold; Instrument Serif has no bold face (see the
       h1-h4 rule), so WeasyPrint would synthesize fake-bold here. The
       header row is already distinguished by the beige fill + terracotta
       rule, which is the benchmark (MGI/BCG) treatment. */
    font-weight: normal;
    font-size: 12pt;
}}

.data-table td {{
    padding: 8px 12px;
    border-bottom: 1px solid {beige};
    color: {warm_charcoal};
}}

.data-table tr:hover {{
    background-color: {beige};
}}

/* TOC specific styling */
.toc-table td:first-child {{
    font-family: "Instrument Serif", serif;
    font-size: 12pt;
    /* OVERHAUL4 fix: TOC labels are navigation, never prose; they must
       not hyphenate (WeasyPrint was breaking "Economics" -> "Econom-",
       pushing U+2010 into a font that lacks it). */
    hyphens: none;
}}

.toc-table td:last-child {{
    text-align: right;
    font-family: "JetBrains Mono", monospace;
    font-size: 10pt;
    color: {warm_gray};
    width: 40px;
}}

.toc-dots {{
    border-bottom: 1px dotted {warm_gray};
}}

/* P2-05: real cross references. WeasyPrint resolves the page number of the
   anchor target at layout time via target-counter - no arithmetic, no
   one-page-per-section assumption, cannot drift from the body. */
.toc-table a {{
    color: inherit;
    text-decoration: none;
}}

.toc-table a::after {{
    content: target-counter(attr(href), page);
    font-family: "JetBrains Mono", monospace;
    font-size: 10pt;
    color: {warm_gray};
    margin-left: 6px;
}}

.footer {{
    color: {deep_brown};
    font-size: 8pt;
}}

.confidence-badge-high {{
    color: {sage};
    font-weight: bold;
}}

.confidence-badge-medium {{
    color: {terracotta};
    font-weight: bold;
}}

.confidence-badge-low {{
    color: {alert_red};
    font-weight: bold;
}}

.page-break {{
    page-break-before: always;
}}

/* ── Front/back matter (fix 4.5) ───────────────────────────────────────────
   At-a-glance, Endnotes, Technical appendix. These pages are single-column on
   purpose: the two-column measure from fix 3.4 is right for running prose but
   wrong for a scannable summary grid and for numbered reference apparatus,
   both of which are read by jumping rather than reading linearly. The
   `column-span: all` rules further down keep them full-measure. */
.at-a-glance .glance-question {{
    font-family: 'Instrument Serif', Georgia, serif;
    font-size: 15pt;
    line-height: 1.35;
    color: {deep_brown};
    margin: 0 0 14pt 0;
    padding-bottom: 10pt;
    border-bottom: 2px solid {terracotta};
}}

/* Two columns of paired label/value cells. `table` layout rather than flex so
   WeasyPrint paginates it predictably, flex fragmentation across pages is not
   reliably supported in the engine we render with. */
.glance-grid {{
    display: table;
    width: 100%;
    margin-bottom: 14pt;
    border-collapse: collapse;
}}
.glance-cell {{
    display: table-cell;
    width: 25%;
    padding: 8pt 10pt 8pt 0;
    vertical-align: top;
    border-top: 1px solid {beige};
}}
.glance-label {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 7pt;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: {warm_gray};
    margin-bottom: 3pt;
}}
.glance-value {{
    font-size: 10.5pt;
    line-height: 1.3;
    color: {deep_brown};
    font-weight: 600;
}}
.glance-block {{
    margin-bottom: 12pt;
    page-break-inside: avoid;
}}
.glance-block p {{ margin: 2pt 0 0 0; font-size: 10pt; line-height: 1.45; }}
.glance-findings, .glance-assumptions {{
    margin: 4pt 0 0 0;
    padding-left: 16pt;
    font-size: 10pt;
    line-height: 1.45;
}}
.glance-findings li, .glance-assumptions li {{ margin-bottom: 4pt; }}

/* Endnotes. 8pt is the benchmark footnote measure (7-7.5pt) nudged up for the
   longer URLs we carry; the chapter heading is what makes a note traceable
   back to the claim it supports. */
.endnote-chapter {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 8pt;
    letter-spacing: 0.06em;
    text-transform: uppercase;
    color: {terracotta};
    margin: 12pt 0 4pt 0;
    page-break-after: avoid;
}}
.endnote-list {{
    margin: 0 0 8pt 0;
    padding-left: 0;
    list-style: none;
    font-size: 8pt;
    line-height: 1.4;
}}
.endnote-list li {{
    margin-bottom: 3pt;
    padding-left: 20pt;
    text-indent: -20pt;
}}
.endnote-num {{ color: {terracotta}; font-weight: 600; }}
/* URLs must wrap: an unbreakable 120-char URL otherwise overflows the measure
   and is silently clipped at the page edge. */
.endnote-url {{
    font-family: 'JetBrains Mono', monospace;
    font-size: 7pt;
    color: {warm_gray};
    word-break: break-all;
}}
.endnote-empty, .appendix-empty {{
    font-size: 10pt;
    font-style: italic;
    color: {warm_gray};
}}

/* ── Two-column body (fix 3.4) ─────────────────────────────────────────────
   The audit measured the shipped report at ~87 chars/line in a single
   justified column; the BCG benchmark sits at a median of 56 chars/line in
   a TWO-column layout. The page frame is 25mm/40mm margins on A4, so the
   measure only resolves to the benchmark range with columns, not with
   smaller type.

   Only the prose body is columned. Visual anchors span the full width
   (column-span: all), an exhibit, KPI strip, insight or implication box
   squeezed into a 68mm column would read as a sidebar, and both benchmarks
   set their exhibits full-measure. Headings must never be orphaned at the
   foot of a column, so they break-after: avoid and stay with their first
   paragraph. */
.section-body {{
    column-count: 2;
    column-gap: 7mm;
    /* P2-01: balance, not auto. auto fills column 1 to full column height
       and never starts column 2 on any chapter shorter than one column
       height - 6 pages of report A measured col2=0w against col1=180-297w.
       The old comment claimed balance while the value did the opposite. */
    column-fill: balance;
    /* P2-04: at least 4 lines of prose must accompany any fragment
       boundary, so the trailing implication callout can never sit alone
       on a page with no body text around it. */
    orphans: 4;
    widows: 4;
}}

.section-body h3, .section-body h4 {{
    break-after: avoid;
    page-break-after: avoid;
}}

.section-body p:first-of-type {{
    margin-top: 0;
}}

/* Full-width anchors inside a two-column section. `column-span: all` is the
   WeasyPrint-supported spell. (P2-02 removed a sentence here claiming the
   section image "sits outside the columned div so it is already
   full-measure" - outside the columned div is precisely why the float
   collided with the column flow. The plate now lives inside .section-body
   with column-span: all like everything else on this list.) */
.exhibit,
.kpi-strip,
.key-insight-box,
.implication-box,
.callout {{
    column-span: all;
}}

.no-break {{
    page-break-inside: avoid;
}}
""".format(**PDF_PALETTE)


# ─────────────────────────────────────────────────────────────────────────────
# Vendored brand fonts (fix 3.2, @font-face embedding)
# ─────────────────────────────────────────────────────────────────────────────
#
# The audit (§3.2) found that the brand typography declared in CSS_TEMPLATE,
# "Instrument Serif""Source Sans 3""JetBrains Mono", was never actually
# embedded in the shipped PDF: there were zero @font-face blocks, so every
# render silently fell back to DejaVu (WeasyPrint's system default). The fonts
# are vendored in assets/fonts/ (fix 3.1, OFL-licensed); here they are inlined
# as base64 data-URIs so the PDF embeds the real typefaces on every machine,
# with no network or system-font dependency.
#
# (family, weight, style, filename). The family strings MUST match the
# font-family names used in CSS_TEMPLATE exactly, or @font-face will not bind.
_VENDORED_FONTS: tuple[tuple[str, int, str, str], ...] = (
    ("Instrument Serif", 400, "normal", "InstrumentSerif-Regular.ttf"),
    ("Instrument Serif", 400, "italic", "InstrumentSerif-Italic.ttf"),
    ("Source Sans 3", 400, "normal", "SourceSans3-Regular.ttf"),
    ("Source Sans 3", 700, "normal", "SourceSans3-Bold.ttf"),
    ("Source Sans 3", 400, "italic", "SourceSans3-Italic.ttf"),
    ("JetBrains Mono", 400, "normal", "JetBrainsMono-Regular.ttf"),
    ("JetBrains Mono", 700, "normal", "JetBrainsMono-Bold.ttf"),
)

_FONTS_DIR = Path(__file__).resolve().parents[3] / "assets" / "fonts"

# D-15: report prose is English, but retain the complete Latin repertoire plus
# common punctuation, currencies, arrows, and mathematical symbols used in
# financial exhibits. CJK and other large unused ranges are deliberately not
# embedded. Browsers may use a system fallback for an exceptional glyph while
# the report's actual brand text remains embedded and portable.
_FONT_SUBSET_RANGES: tuple[tuple[int, int], ...] = (
    (0x0020, 0x024F),  # Basic Latin, Latin-1, Latin Extended A/B, IPA
    (0x2000, 0x206F),  # General punctuation
    (0x20A0, 0x20CF),  # Currency symbols
    (0x2190, 0x21FF),  # Arrows
    (0x2200, 0x22FF),  # Mathematical operators
    # OVERHAUL4 COVER: 0x25A0-0x25FF (Geometric Shapes) — the cover
    # confidence badge uses U+25CF (●). The glyph EXISTS in Source Sans 3,
    # but the subsetter stripped it (the range was missing), so WeasyPrint
    # fell back to DejaVu-Sans-Bold in every rendered PDF — a forbidden
    # fallback font per the golden baseline. The dot is now embedded.
    (0x25A0, 0x25FF),
)
DEFAULT_FONT_GLYPHS = "".join(
    chr(codepoint)
    for start, end in _FONT_SUBSET_RANGES
    for codepoint in range(start, end + 1)
)
MAX_EMBEDDED_FONT_BYTES = 180_000
MAX_EMBEDDED_FONTS_BYTES = 900_000


def _subset_font_bytes(font_path: Path, glyphs: str = DEFAULT_FONT_GLYPHS) -> bytes | None:
    """Subset one TTF to the bounded glyph repertoire used by reports."""
    try:
        font = TTFont(font_path, recalcTimestamp=False)
        options = subset.Options()
        options.layout_features = ["*"]
        options.notdef_outline = True
        subsetter = subset.Subsetter(options=options)
        subsetter.populate(text=glyphs)
        subsetter.subset(font)
        output = BytesIO()
        font.save(output)
        font.close()
        payload = output.getvalue()
    except (OSError, TTLibError, ValueError) as exc:
        logger.warning(
            "Vendored font %s could not be subset: %s",
            font_path,
            exc,
            exc_info=True,
        )
        return None

    if not payload or len(payload) > MAX_EMBEDDED_FONT_BYTES:
        logger.warning(
            "Subset font %s exceeds its %d-byte embedding budget (%d bytes)",
            font_path.name,
            MAX_EMBEDDED_FONT_BYTES,
            len(payload),
        )
        return None
    return payload


def _build_font_face_css(fonts_dir: Path = _FONTS_DIR) -> str:
    """Build @font-face blocks with base64 data-URI sources for vendored fonts.

    Data-URIs (not relative url() paths) because the CSS is inlined into the
    HTML <style> element and rendered with base_url=cwd, relative paths from
    an inline stylesheet resolve against cwd, not the package, and break the
    moment a caller sets cwd elsewhere. Data-URIs make the HTML fully
    self-contained: it renders identically regardless of working directory.

    Never-raises (§0.3): a missing/unreadable font file is logged loudly and
    skipped, the PDF then falls back for that face rather than sinking the
    whole report build.
    """
    blocks: list[str] = []
    total_bytes = 0
    for family, weight, style, filename in _VENDORED_FONTS:
        font_path = fonts_dir / filename
        if not font_path.is_file():
            logger.warning(
                "Vendored font %s not readable at %s; "
                "PDF will fall back to system fonts for this face",
                filename,
                font_path,
                exc_info=FileNotFoundError(font_path),
            )
            continue
        data = _subset_font_bytes(font_path)
        if data is None:
            continue
        if total_bytes + len(data) > MAX_EMBEDDED_FONTS_BYTES:
            logger.warning(
                "Skipping %s: total embedded-font budget of %d bytes reached",
                filename,
                MAX_EMBEDDED_FONTS_BYTES,
            )
            continue
        total_bytes += len(data)
        b64 = base64.b64encode(data).decode("ascii")
        blocks.append(
            "@font-face {\n"
            f'    font-family: "{family}";\n'
            f"    font-style: {style};\n"
            f"    font-weight: {weight};\n"
            f'    src: url("data:font/ttf;base64,{b64}") format("truetype");\n'
            "}"
        )
    return "\n\n".join(blocks)


# Font blocks are appended AFTER str.format(**PDF_PALETTE) has run (line
# above): the @font-face braces are therefore plain CSS braces, never seen by
# the formatter. This sidesteps the doubled-brace trap documented at the top
# of CSS_TEMPLATE.
CSS_TEMPLATE = CSS_TEMPLATE + "\n\n" + _build_font_face_css() + "\n"


# ─────────────────────────────────────────────────────────────────────────────
# HTML Template (Jinja2, premium report structure §6.1)
# ─────────────────────────────────────────────────────────────────────────────

HTML_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>{{ report.question }}</title>
    <style>{{ css_content | safe }}</style>
</head>
<body>

{# ── Cover Page ── #}
<div class="cover{% if not cover_image %} cover--typographic{% endif %}">
    {% if cover_image %}
    <img src="{{ cover_image.image_path }}" class="cover-image" alt="{{ cover_image.caption }}">
    <div class="cover-overlay"></div>
    {% else %}
    <div class="cover-hero"></div>
    {% endif %}
    <div class="cover-title">
        {# OVERHAUL4 COVER: the accent rule is the FIRST child of the title
           block so it sits directly above the title (on the image fade in
           the photo case, on the designed plate in the typographic case), never orphaned in empty space. #}
        <div class="cover-accent-rule"></div>
        <h1>{{ report.question }}</h1>
        <div class="cover-meta-row">
            <span class="cover-recommendation">{{ report.recommendation | upper }}</span>
            {% if report.confidence %}
            <span class="cover-confidence">{{ report.confidence | upper }}<span class="cover-confidence-dot">●</span></span>
            {% endif %}
        </div>
        {# OVERHAUL4 COVER: the raw engagement UUID read as debug text. The
           professional sub-line is a clean date + series line. #}
        <p class="cover-date">{{ report.generated_at.strftime('%B %Y') }} · MBB Engagement Report</p>
    </div>
</div>

{# ── At-a-glance (fix 4.5) ──
   MGI opens with this, BEFORE the table of contents: the whole argument on one
   spread, so a partner who reads exactly one page still gets the answer. It is
   deliberately NOT a second executive summary, the exec summary narrates,
   this enumerates. Every value here is a field that already existed on
   FinalReport and was simply never surfaced in the PDF.

   Counts are computed with Jinja rather than authored, so they cannot drift
   from the body the way the hardcoded TOC page numbers below already do. #}
<div class="page-break at-a-glance" id="at-a-glance">
    <h2>At a Glance</h2>

    <div class="glance-question">{{ report.question }}</div>

    <div class="glance-grid">
        <div class="glance-cell">
            <div class="glance-label">Recommendation</div>
            <div class="glance-value">
                {{ report.recommendation | replace("_", " ") | title }}
            </div>
        </div>
        <div class="glance-cell">
            <div class="glance-label">Evidence base</div>
            <div class="glance-value">
                {{ report.total_sources }} sources ·
                {{ report.total_data_points }} data points
            </div>
        </div>
        <div class="glance-cell">
            <div class="glance-label">Analysis depth</div>
            <div class="glance-value">
                {{ report.sections | length }} chapters
            </div>
        </div>
    </div>

    {% if report.recommendation_rationale %}
    <div class="glance-block">
        <div class="glance-label">Why</div>
        <p>{{ report.recommendation_rationale }}</p>
    </div>
    {% endif %}

    {% if report.key_findings %}
    <div class="glance-block">
        <div class="glance-label">What we found</div>
        <ol class="glance-findings">
            {# Capped at five. An at-a-glance page that runs to two pages is not
               an at-a-glance page. #}
            {# P2-34: filter falsy entries - an empty <li> glyph is a
               template-leak defect, not a stylistic choice. #}
            {% for finding in report.key_findings[:5] if finding %}
            <li>{{ finding.title }}</li>
            {% endfor %}
        </ol>
    </div>
    {% endif %}

    {% if report.critical_assumptions %}
    <div class="glance-block">
        <div class="glance-label">What this rests on</div>
        <ul class="glance-assumptions">
            {% for assumption in report.critical_assumptions[:4] if assumption %}
            <li>{{ assumption }}</li>
            {% endfor %}
        </ul>
    </div>
    {% endif %}
</div>

{# ── Table of Contents ──
   P2-05: page numbers are resolved by WeasyPrint via
   target-counter(attr(href), page) against real anchors - the hardcoded
   integers and +N arithmetic here assumed one page per section and were
   wrong by up to 9 pages in the audited fixtures.
   P2-06: every row is emitted under the same condition as its chapter,
   so the TOC can never advertise a chapter that does not exist. #}
<div class="page-break">
    <h2>Table of Contents</h2>
    <div class="data-table toc-table">
        <table>
            <tr><td><a href="#at-a-glance">At a Glance</a></td><td class="toc-page"></td></tr>
            <tr><td><a href="#exec-summary">Executive Summary</a></td><td class="toc-page"></td></tr>
            {% for section in report.sections if section %}
            <tr><td><a href="#sec-{{ loop.index }}">{{ section.title }}</a></td><td class="toc-page"></td></tr>
            {% endfor %}
            {% if report.risk_analysis %}
            <tr><td><a href="#risk-analysis">Risk Analysis</a></td><td class="toc-page"></td></tr>
            {% endif %}
            <tr><td><a href="#methodology">Methodology</a></td><td class="toc-page"></td></tr>
            <tr><td><a href="#endnotes">Endnotes</a></td><td class="toc-page"></td></tr>
            <tr><td><a href="#appendix-sources">Appendix: Sources</a></td><td class="toc-page"></td></tr>
        </table>
    </div>
</div>

{# ── Executive Summary, D27: Rich dashboard layout ── #}
<div class="page-break" id="exec-summary">
    <h2>Executive Summary</h2>

    {# Recommendation banner #}
    <div class="recommendation-banner">
        <div class="rec-label">Recommendation</div>
        <div class="rec-value">{{ report.recommendation | upper }}</div>
    </div>

    {# KPI strip, key metrics at a glance #}
    <div class="kpi-strip">
        <div class="kpi-card">
            <div class="kpi-value">{{ report.key_findings | length }}</div>
            <div class="kpi-label">Key Findings</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-value">{{ report.total_sources }}</div>
            <div class="kpi-label">Sources Cited</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-value">{{ report.total_data_points }}</div>
            <div class="kpi-label">Data Points</div>
        </div>
        <div class="kpi-card">
            <div class="kpi-value">{{ report.sections | length }}</div>
            <div class="kpi-label">Analysis Sections</div>
        </div>
    </div>

    {{ report.executive_summary | md_to_html }}

    {# Key findings in dashboard grid. P2-34: the heading is suppressed
       with the list - an empty <h3> is the same defect class as an empty
       bullet. #}
    {% if report.key_findings %}
    <h3>Key Findings</h3>
    <div class="dashboard-grid">
        {% for finding in report.key_findings if finding %}
        <div class="dashboard-card">
            <div class="dashboard-card-title">{{ finding.title }}</div>
            <div class="dashboard-card-body">
                {{ finding.content[:300] }}
            </div>
        </div>
        {% endfor %}
    </div>
    {% endif %}

    {# Critical assumptions as callouts #}
    {% if report.critical_assumptions %}
    <h3>Critical Assumptions</h3>
    {% for assumption in report.critical_assumptions if assumption %}
    <div class="callout callout--alert">
        <div class="callout-title">Assumption</div>
        {{ assumption }}
    </div>
    {% endfor %}
    {% endif %}
</div>

{# ── Analysis Sections ── #}
{% for section in report.sections if section %}
<div class="page-break" id="sec-{{ loop.index }}">
    {# Section opener. MGI sets a letterspaced "C H A P T E R  O N E" above the
       chapter title; BCG gives each section a full-width opener with generous
       whitespace. Neither drops the reader straight into body copy. The number
       comes from `loop.index`, so it is derived from the document structure and
       cannot disagree with the actual section order. #}
    <div class="section-opener">
        <div class="section-eyebrow">Section {{ loop.index }}</div>
        <h2>{{ section.title }}</h2>
        <div class="section-rule"></div>
    </div>

    <div class="key-insight-box">
        {{ section.key_insight | clean_dict_repr }}
    </div>

    {# The prose body is two-column (fix 3.4, see .section-body in the CSS).
       It was previously wrapped in .no-break, which tried to keep an entire
       2000-word section body on one page, impossible, so WeasyPrint ignored
       it, and it contradicted the column layout. The columns handle their own
       break etiquette via orphans/widows on the body element.

       P2-02: the section plate is the FIRST child of the column flow with
       column-span: all, a full-measure band. It used to be a float:right
       sibling placed before this div, which collided with the columns. #}
    <div class="section-body">
        {% if section_images[section.id] %}
        <figure class="section-plate">
            <img src="{{ section_images[section.id].image_path }}" alt="{{ section_images[section.id].caption }}">
            <figcaption>{{ section_images[section.id].caption }}</figcaption>
        </figure>
        {% endif %}
        {{ section.body | md_to_html }}
    </div>

    {# Exhibits, the MGI/BCG four-part anatomy: number, takeaway title,
       figure, then note/source under a hairline. The number is generated by
       a CSS counter, so it is always correct and never authored.

       `source_citation` already existed on ChartPlacement and was simply
       never rendered: every chart shipped with no provenance line at all,
       while both benchmark documents carry one under every single exhibit.
       It is emitted only when actually present, an invented source line
       would be the same class of defect as an invented geography. #}
    {% for chart in section_charts[section.id] if chart %}
    <figure class="exhibit no-break">
        <div class="exhibit-number"></div>
        {# Fix 4.4: unconditional. A numbered exhibit with no action title is a
           chart, not an exhibit, MGI's anatomy requires the takeaway to sit
           between the number and the figure. `_enforce_exhibit_anatomy`
           guarantees a non-empty caption, so there is nothing to guard. #}
        <div class="exhibit-title">{{ chart.caption }}</div>
        <div class="exhibit-figure">
            <img src="{{ chart.image_path }}" alt="{{ chart.caption }}">
        </div>
        {# Fix 4.4: the footer is NOT conditional on note/source being present.
           It used to be, so an exhibit with neither rendered with no footer at
           all, no hairline, no provenance, and looked deliberate. The hairline
           is what visually closes the exhibit in both benchmark documents, so it
           is now always drawn, and `_enforce_exhibit_anatomy` guarantees a
           Source: line exists by the time we get here. #}
        <figcaption class="exhibit-footer">
            {# Note before source, as in both benchmarks ("Note: Based on
               McKinsey Industry Classification… Source: …"). Each is emitted
               only when present: an invented note or source line would be the
               same class of defect as an invented geography. #}
            {# The "Note:" / "Source:" labels are italic in both benchmarks, and
               .exhibit-note-label / .exhibit-source-label existed in the CSS
               but were referenced by no markup, dead rules. The label is
               emitted as its own span and the prefix stripped from the value,
               so the label appears exactly once whether or not the producer
               already prefixed the string (the deterministic miner does; an
               LLM-supplied spec may not). #}
            {% if chart.note %}
            <p class="exhibit-note"><span class="exhibit-note-label">Note:</span>
                {{ chart.note | trim | replace("Note:", "", 1) | trim }}</p>
            {% endif %}
            {% if chart.source_citation %}
            <p class="exhibit-source"><span class="exhibit-source-label">Source:</span>
                {{ chart.source_citation | trim | replace("Source:", "", 1) | trim }}</p>
            {% endif %}
        </figcaption>
    </figure>
    {% endfor %}

    <div class="implication-box">
        <strong>So What?</strong> {{ section.implications }}
    </div>
</div>
{% endfor %}

{# ── Risk Analysis ── #}
{% if report.risk_analysis %}
<div class="page-break" id="risk-analysis">
    <h2>Risk Analysis</h2>
    {{ risk_analysis_html | safe }}
</div>
{% endif %}

{# Methodology (W-10)
   The four bullets this replaces (Agents Used, Sources Accessed, Data Points,
   Limitations) were three counts and a list: they told the reader who ran the
   engagement, when the reader was asking how the answer is known. W-09 had
   already deleted the roster (it is telemetry, and `ClientReport` cannot
   resolve it); W-10 replaces the remaining counts with six subsections, each
   built from a structure the pipeline actually recorded: the DAG's roster
   decisions, the W-07 insufficiency resolutions, the fact checker's counters
   and the Source corpus (`hyperion/output/methodology.py`).

   Every string inside `report.methodology` has been through
   `ClientProse.of()`, so an agent name in this block is unconstructible
   rather than merely unprinted, and no template-level filter is needed.

   NOTE ON TYPOGRAPHY: this comment is inside HTML_TEMPLATE, which is a string
   constant, and tests/output/test_typography.py walks render-path string
   CONSTANTS for U+2014/U+2013. A Jinja comment is part of that constant even
   though Jinja never emits it, so the dash ban applies here too. Plain
   punctuation only.

   P2-34: lists are pre-filtered (trim + drop falsy), every loop carries a
   falsy-entry filter (tests/output/test_empty_list_items.py asserts this over
   every for-tag in the template) and the enclosing <h3>/<ul> is suppressed
   when nothing survives; page_audit._check_empty_list_items is the
   render-level backstop. #}
{% set limitations_clean = report.limitations | map('trim') | select | list %}
<div class="page-break" id="methodology">
    <h2>Methodology</h2>
    {% if report.methodology %}
    {% for sub in report.methodology.subsections if sub %}
    {% set sub_facts = sub.facts | map('trim') | select | list %}
    <h3>{{ sub.heading }}</h3>
    <p>{{ sub.narrative }}</p>
    {% if sub_facts %}
    <ul class="methodology-facts">
        {% for fact in sub_facts if fact %}
        <li>{{ fact }}</li>
        {% endfor %}
    </ul>
    {% endif %}
    {% endfor %}
    {% else %}
    {# Defensive only: the designer builds a report-only record when the
       orchestrator did not supply one, so this branch means the build itself
       failed. State that, rather than printing a bare count. #}
    <h3>Evidence base</h3>
    <p>The analysis draws on {{ report.total_sources }} unique sources across
       {{ report.total_data_points }} recorded data points. A full account of
       the retrieval and verification procedure could not be assembled for
       this engagement.</p>
    {% endif %}
    {% if limitations_clean %}
    <h3>Evidence gaps specific to this question</h3>
    <ul>
        {% for limitation in limitations_clean if limitation %}
        <li>{{ limitation }}</li>
        {% endfor %}
    </ul>
    {% endif %}
</div>

{# ── Endnotes (fix 4.5) ──
   MGI numbers its endnotes and ties each to the chapter that cited it, which is
   what makes a claim traceable rather than merely footnoted. Built server-side
   (`endnotes_html`) because the numbering has to be stable across sections and
   Jinja loop indices reset per section. #}
<div class="page-break" id="endnotes">
    <h2>Endnotes</h2>
    {{ endnotes_html | safe }}
</div>

{# W-09: the Technical Appendix is deleted from the client document. Every
   input it rendered (quality_score, confidence_breakdown, contradictions,
   fact_check_report) is operator telemetry, not client copy. It now lives in
   the EngagementTelemetry operator artifact (reports/diagnostics/), where the
   scorecard is genuinely valuable. The self-assessment is not discarded; it is
   addressed to the right reader. #}

{# ── Appendix: Sources ── #}
<div class="page-break" id="appendix-sources">
    <h2>Appendix: Sources</h2>
    {{ appendix_sources_html | safe }}
</div>

{# ── Back Cover ── #}
<div class="page-break" style="text-align: center; padding-top: 200px;">
    <h1 style="color: {{ palette.terracotta }};">HYPERION</h1>
    <p style="color: {{ palette.warm_gray }}; font-size: 14pt;">many minds. one reading.</p>
    <p style="color: {{ palette.warm_gray }}; font-size: 8pt;">
        Generated {{ report.generated_at.strftime('%B %d, %Y') }} · Engagement {{ report.engagement_id }}
    </p>
    <p style="color: {{ palette.warm_gray }}; font-size: 8pt;">
        Confidential, for intended recipient only.
    </p>
</div>

</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


PRESENTATION_DESIGNER_SPEC = AgentSpec(
    name=AgentName.PRESENTATION_DESIGNER,
    role=AgentRole.DELIVERY,
    display_name="Presentation Designer",
    model_tier=ModelTier.STRONG,
    tools=[
        ToolName.UNSPLASH,
        ToolName.PLOTLY,
        ToolName.JINJA2,
        # W-03: ToolName.WEASYPRINT removed, the designer stages HTML and a
        # layout plan; the Render Engine is the only agent that writes PDFs.
    ],
    skills=[
        SkillSpec(
            name="Layout design",
            description=(
                "Design page layouts that follow the premium structure. "
                "Each page has a clear visual hierarchy: header → key "
                "insight → body → chart/image → implication. Not just "
                "dumping content into a template, deliberate decisions "
                "about what goes on each page, how to balance text and "
                "visuals, and how to guide the reader through the narrative."
            ),
            inputs=["final_report", "quality_score", "visualization_output"],
            outputs=["page_layouts", "content_distribution", "visual_balance"],
        ),
        SkillSpec(
            name="Typography",
            description=(
                "Apply the HYPERION typography system consistently: "
                "Instrument Serif for headers (cover 36pt, sections 22pt), "
                "JetBrains Mono for body (10pt), subsections (14pt bold), "
                "captions (8pt), key insight boxes (11pt), data tables "
                "(9pt). Two fonts only, creates visual consistency."
            ),
            inputs=["layout_plan"],
            outputs=["typography_applied", "font_sizes_set"],
        ),
        SkillSpec(
            name="Image placement",
            description=(
                "Place images according to the 5 image placement rules "
                "(§6.3): (1) Every image has adjacent text context on the "
                "SAME page. (2) Cover = full-bleed, sections = 40% width "
                "right-aligned. (3) Topic-relevant, not generic stock. "
                "(4) All processed through Pillow pipeline. (5) No image "
                "larger than 50% page height (except cover). (6) Every "
                "image has a caption with source attribution. No "
                "orphaned images, no blank pages."
            ),
            inputs=["unsplash_images", "section_content", "page_layouts"],
            outputs=["image_placements", "captions", "attribution"],
        ),
        SkillSpec(
            name="Print design",
            description=(
                "Ensure the PDF is print-ready: 300 DPI, embedded fonts "
                "(Instrument Serif, JetBrains Mono), proper margins (25mm "
                "all sides, 15mm binding), no color bleeding. A4 page "
                "size. Brand palette only, no random colors."
            ),
            inputs=["layout_plan", "css_template"],
            outputs=["print_ready_pdf", "font_embedding", "margin_spec"],
        ),
        SkillSpec(
            name="Page flow",
            description=(
                "Control page breaks to ensure no blank pages, no "
                "orphaned images, and no awkward section breaks. Use "
                "page-break-inside: avoid for images and charts. Use "
                "page-break-before: always for new sections. Scan for "
                "and eliminate blank pages."
            ),
            inputs=["page_layouts", "content_blocks"],
            outputs=["page_break_plan", "blank_page_check"],
        ),
        SkillSpec(
            name="Visual hierarchy",
            description=(
                "Use size, weight, and color to guide the reader's eye "
                "through the report. The most important content "
                "(recommendation, key findings) gets the most visual "
                "weight. Key insight boxes use beige background with "
                "terracotta border. Implication boxes use sage background. "
                "Risk indicators use alert red."
            ),
            inputs=["final_report", "layout_plan"],
            outputs=["visual_weight_map", "color_assignments"],
        ),
        SkillSpec(
            name="White space management",
            description=(
                "Use white space deliberately, not as empty space, but "
                "as a design element that improves readability and focus. "
                "Margins, padding, and spacing between elements are "
                "intentional. No cramped pages, no wasted space."
            ),
            inputs=["page_layouts", "content_density"],
            outputs=["spacing_plan", "margin_adjustments"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Presentation Designer, the report layout "
        "designer and visual storyteller.\n\n"
        "Your role:\n"
        "1. RECEIVE the FinalReport from the Synthesis Lead and the "
        "QualityScore from the Quality Gate.\n"
        "2. DESIGN a layout plan, which content goes on which page, "
        "in what order, with what visuals.\n"
        "3. SELECT Unsplash images for cover and section headers with "
        "specific search terms, not generic.\n"
        "4. RECEIVE chart images from the Data Visualizer.\n"
        "5. RENDER the HTML template with Jinja2.\n"
        "6. GENERATE the PDF with WeasyPrint.\n"
        "7. POST-PROCESS images with Pillow (via Render Engine).\n\n"
        "Layout Design Principles:\n"
        "- Each page has a clear visual hierarchy: header → key insight "
        "→ body → chart/image → implication.\n"
        "- The most important content (recommendation, key findings) "
        "gets the most visual weight.\n"
        "- White space is a design element, not empty space.\n\n"
        "Image Placement Rules (§6.3, NON-NEGOTIABLE):\n"
        "1. Every image has adjacent text context on the SAME page.\n"
        "2. Cover = full-bleed. Sections = 40% width, right-aligned.\n"
        "3. Topic-relevant, not generic stock. 'Modern boardroom meeting' "
        "not 'business.'\n"
        "4. All images processed through Pillow pipeline.\n"
        "5. No image larger than 50% page height (except cover).\n"
        "6. Every image has a caption with source attribution.\n"
        "7. Charts are NEVER screenshots. Always Plotly → PNG at scale=3.\n\n"
        "Typography (§7.4, TWO FONTS ONLY):\n"
        "- Headers: Instrument Serif (cover 36pt, sections 22pt)\n"
        "- Body: JetBrains Mono (10pt regular, 14pt bold subsections)\n"
        "- Captions: JetBrains Mono 8pt\n"
        "- Key insight: JetBrains Mono 11pt\n"
        "- Data tables: JetBrains Mono 9pt\n\n"
        "Color Palette (§7.2, WARM, NOT BLUE):\n"
        "- Warm Charcoal #1A1A1A (primary text)\n"
        "- Cream #F5F4EE (background)\n"
        "- Terracotta #C8704D (primary accent, key insight borders)\n"
        "- Sage #7C9885 (secondary accent, implication boxes)\n"
        "- Beige #E8E6DD (callout backgrounds)\n"
        "- Warm Gray #8B8680 (captions, secondary text)\n"
        "- Deep Brown #3D3530 (footer, methodology)\n"
        "- Alert Red #B5533C (risk indicators only)\n"
        "- NEVER blue, purple, or green.\n\n"
        "Page Flow Rules:\n"
        "- page-break-inside: avoid for images and charts.\n"
        "- page-break-before: always for new sections.\n"
        "- No blank pages. No orphaned images.\n"
        # Fix 4.2: this said "15-40 pages", contradicting the 15-20 delivery
        # contract the page budget enforces. The Presentation Designer was being
        # told a looser rule than the Render Engine verifies against, so the
        # agent that lays out the pages had a different idea of the contract than
        # the agent that checks it. Interpolated from the constants so the two
        # cannot diverge again.
        f"- {PAGE_COUNT_MIN}-{PAGE_COUNT_MAX} pages for a standard engagement.\n\n"
        "You run on STRONG tier. You do NOT spawn sub-agents.\n\n"
        "Your output is a LayoutPlan Pydantic model, page-by-page layout, "
        "image selections, chart placements, HTML template path, CSS path."
    ),
    spawn_condition="Spawned after the Quality Gate approves the report "
                     "(score ≥ 4.0). Receives FinalReport, QualityScore, "
                     "and VisualizationOutput. Produces the LayoutPlan that "
                     "the Render Engine uses to assemble the final PDF.",
    max_sub_agents=0,
    output_model="LayoutPlan",
)


# ─────────────────────────────────────────────────────────────────────────────
# Presentation Designer Agent
# ─────────────────────────────────────────────────────────────────────────────


class PresentationDesigner(BaseAgent):
    """Agent 19: The report layout designer and visual storyteller.

    Designs the report layout, selects images, and composes the visual
    structure of the PDF. Runs on STRONG tier because layout design
    requires strong reasoning about visual hierarchy and narrative flow.
    (§4.6, Agent 19)

    Lifecycle:
    1. Receive FinalReport from Synthesis Lead
    2. Receive QualityScore from Quality Gate
    3. Design layout plan (which content goes on which page)
    4. Select Unsplash images for cover and section headers
    5. Receive chart images from Data Visualizer
    6. Render HTML template with Jinja2
    7. Generate PDF with WeasyPrint
    8. Post-process images with Pillow (via Render Engine)
    """

    OUTPUT_DIR = "output"
    # Build intermediates (CSS, scratch HTML) live here, NOT in output/.
    # output/ is reserved for things the client is meant to receive, so a
    # failed render can never leave a bare stylesheet as the "deliverable".
    BUILD_DIR = "output/.build"
    HTML_OUTPUT = "output/report.html"
    CSS_OUTPUT = "output/report.css"
    PDF_OUTPUT = "output/report.pdf"
    IMAGE_DIR = "output/images"

    @staticmethod
    def _slugify(text: str, max_len: int = 60) -> str:
        """Convert a question string into a filesystem-safe filename slug."""
        import re
        slug = re.sub(r'[^\w\s-]', '', text.lower()).strip()
        slug = re.sub(r'[\s_-]+', '_', slug)
        return slug[:max_len].rstrip('_') or "report"

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or PRESENTATION_DESIGNER_SPEC, bus=bus, router=router)

        # The FinalReport to design layout for
        self._final_report: FinalReport | None = None

        # The QualityScore, stored for DISPLAY only (dimension table on the
        # quality page). W-08: delivery never evaluates it; the orchestrator
        # is the single ship/no-ship decision point.
        self._quality_score: QualityScore | None = None

        # Chart specifications from Data Visualizer
        self._visualization_output: VisualizationOutput | None = None

        # Selected images
        self._cover_image: ImageSelection | None = None
        self._section_images: dict[str, ImageSelection] = {}

        # Track already-used Unsplash image IDs so no image is ever reused across
        # the cover and section headers (the "same image every time" failure).
        # Picking prefers the first unused candidate.
        self._used_image_ids: set[str] = set()

        # Chart placements
        self._chart_placements: dict[str, list[ChartPlacement]] = {}

        # Page layouts
        self._pages: list[PageLayout] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Presentation Designer listens to:
        - HANDOFF: receives FinalReport from Synthesis Lead, QualityScore from Quality Gate
        - FINDINGS: collects VisualizationOutput from Data Visualizer
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            if task == "design_layout":
                context_bundle = payload.get("context_bundle", {})
                if "final_report" in context_bundle:
                    report_data = context_bundle["final_report"]
                    self._final_report = FinalReport(**report_data) if isinstance(report_data, dict) else report_data
                if "quality_score" in context_bundle:
                    qs_data = context_bundle["quality_score"]
                    self._quality_score = QualityScore(**qs_data) if isinstance(qs_data, dict) else qs_data
                if "visualization_output" in context_bundle:
                    viz_data = context_bundle["visualization_output"]
                    self._visualization_output = VisualizationOutput(**viz_data) if isinstance(viz_data, dict) else viz_data

        elif msg.channel == Channel.FINDINGS:
            payload = msg.payload
            finding_type = payload.get("finding_type", "")

            if finding_type == "visualization_output":
                viz_data = payload.get("visualization_output")
                if viz_data:
                    self._visualization_output = VisualizationOutput(**viz_data) if isinstance(viz_data, dict) else viz_data

            elif finding_type == "quality_score":
                qs_data = payload.get("quality_score")
                if qs_data:
                    self._quality_score = QualityScore(**qs_data) if isinstance(qs_data, dict) else qs_data

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Receive FinalReport from Synthesis Lead
    # ─────────────────────────────────────────────────────────────────────

    async def _receive_final_report(
        self,
        final_report: FinalReport | None = None,
    ) -> FinalReport | None:
        """Receive the FinalReport from the Synthesis Lead."""
        if final_report:
            self._final_report = final_report
        return self._final_report

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Receive QualityScore from Quality Gate
    # ─────────────────────────────────────────────────────────────────────

    async def _receive_quality_score(
        self,
        quality_score: QualityScore | None = None,
    ) -> QualityScore | None:
        """Receive the QualityScore from the Quality Gate.

        The score is stored for display on the quality page only. W-08:
        whether a report ships is decided by the orchestrator's terminal
        state; if the designer is invoked at all, the report cleared the
        gate and is laid out unconditionally.
        """
        if quality_score:
            self._quality_score = quality_score
        return self._quality_score

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Design layout plan (which content goes on which page)
    # ─────────────────────────────────────────────────────────────────────

    def _design_layout_plan(self, report: FinalReport) -> list[PageLayout]:
        """Design the page-by-page layout plan.

        Premium structure (§6.1):
        - Page 1: Cover (full-bleed image, title, recommendation, confidence)
        - Page 2: Table of Contents
        - Page 3-4: Executive Summary (1-2 pages)
        - Pages 5-N: Analysis Sections (3-8 pages each)
        - Pages N+1-N+3: Risk Analysis (2-3 pages)
        - Page N+4: Methodology (1 page)
        - Pages N+5-N+7: Appendix (source list, data tables)
        - Last page: Back Cover

        Each page has a clear visual hierarchy:
        header → key insight → body → chart/image → implication.
        """
        pages: list[PageLayout] = []
        page_num = 1

        # Page 1: Cover
        pages.append(PageLayout(
            page_number=page_num,
            page_type=PageType.COVER,
            title=report.question,
            content_blocks=["cover_image", "wordmark", "title", "recommendation", "date", "confidence_badge"],
            is_full_bleed=True,
            page_break_before=False,
        ))
        page_num += 1

        # Page 2: Table of Contents
        pages.append(PageLayout(
            page_number=page_num,
            page_type=PageType.TABLE_OF_CONTENTS,
            title="Table of Contents",
            content_blocks=["section_list_with_page_numbers"],
            page_break_before=True,
        ))
        page_num += 1

        # Pages 3-4: Executive Summary (1-2 pages)
        exec_blocks = ["recommendation", "key_findings", "confidence_reasoning", "critical_risks"]
        if len(report.key_findings) > 3:
            # Split into 2 pages if many findings
            pages.append(PageLayout(
                page_number=page_num,
                page_type=PageType.EXECUTIVE_SUMMARY,
                title="Executive Summary",
                content_blocks=exec_blocks[:2],
                has_key_insight_box=True,
                page_break_before=True,
            ))
            page_num += 1
            pages.append(PageLayout(
                page_number=page_num,
                page_type=PageType.EXECUTIVE_SUMMARY,
                title="Executive Summary (continued)",
                content_blocks=exec_blocks[2:],
                page_break_before=False,
            ))
            page_num += 1
        else:
            pages.append(PageLayout(
                page_number=page_num,
                page_type=PageType.EXECUTIVE_SUMMARY,
                title="Executive Summary",
                content_blocks=exec_blocks,
                has_key_insight_box=True,
                page_break_before=True,
            ))
            page_num += 1

        # Pages 5-N: Analysis Sections (3-8 pages each)
        for section in report.sections:
            # Each section starts on a new page
            pages.append(PageLayout(
                page_number=page_num,
                page_type=PageType.SECTION,
                section_id=section.id,
                title=section.title,
                content_blocks=[
                    f"section_header:{section.title}",
                    f"key_insight:{section.key_insight}",
                    f"section_image:{section.id}",
                    f"body:{section.body[:500]}",
                ],
                has_key_insight_box=True,
                page_break_before=True,
            ))
            page_num += 1

            # If section body is long, add continuation pages
            if len(section.body) > 2000:
                # Split body across pages (~2000 chars per page)
                body_chunks = [section.body[i:i+2000] for i in range(0, len(section.body), 2000)]
                for chunk_idx, chunk in enumerate(body_chunks[1:], 1):
                    pages.append(PageLayout(
                        page_number=page_num,
                        page_type=PageType.SECTION,
                        section_id=section.id,
                        title=f"{section.title} (continued, part {chunk_idx + 1})",
                        content_blocks=[f"body:{chunk}"],
                        page_break_before=False,
                    ))
                    page_num += 1

            # Add charts for this section
            section_charts = self._get_charts_for_section(section.id)
            if section_charts:
                for chart in section_charts:
                    chart.page_number = page_num
                    pages[-1].charts.append(chart)

            # Implication box on the last page of the section
            pages[-1].has_implication_box = True
            pages[-1].content_blocks.append(f"implication:{section.implications}")

        # Risk Analysis (2-3 pages)
        if report.risk_analysis:
            pages.append(PageLayout(
                page_number=page_num,
                page_type=PageType.RISK_ANALYSIS,
                title="Risk Analysis",
                content_blocks=["risk_matrix", "top_risks_table", "black_swan_scenarios", "residual_risk"],
                page_break_before=True,
            ))
            page_num += 1

        # Methodology (1 page)
        pages.append(PageLayout(
            page_number=page_num,
            page_type=PageType.METHODOLOGY,
            title="Methodology",
            content_blocks=[
                f"agents_used:{', '.join(report.agents_used)}",
                f"sources_accessed:{report.total_sources}",
                f"data_points:{report.total_data_points}",
                f"limitations:{'; '.join(report.limitations)}",
            ],
            page_break_before=True,
        ))
        page_num += 1

        # Appendix (1-2 pages)
        pages.append(PageLayout(
            page_number=page_num,
            page_type=PageType.APPENDIX,
            title="Appendix",
            content_blocks=["full_source_list", "data_tables"],
            page_break_before=True,
        ))
        page_num += 1

        # Back Cover
        pages.append(PageLayout(
            page_number=page_num,
            page_type=PageType.BACK_COVER,
            title="HYPERION",
            content_blocks=["wordmark", "tagline", "date", "confidentiality_notice"],
            page_break_before=True,
        ))

        return pages

    def _get_charts_for_section(self, section_id: str) -> list[ChartPlacement]:
        """Get chart placements for a specific section from the Data Visualizer output."""
        placements: list[ChartPlacement] = []

        if not self._visualization_output:
            return placements

        for chart in self._visualization_output.charts:
            # `section_id in chart.section` is a substring test, so an empty
            # `chart.section` (the homeless-chart case, fix 3.7) used to match
            # NO section here while `_receive_chart_images` happily filed it
            # under "". Requiring a non-empty section keeps the two paths
            # consistent; re-homing is handled centrally in
            # `_receive_chart_images`, which is the path that feeds the template.
            if not chart.section or not chart.image_path:
                continue
            if chart.section == section_id or section_id in chart.section:
                placement = ChartPlacement(
                    chart_id=chart.id,
                    section_id=section_id,
                    image_path=chart.image_path,
                    caption=chart.caption or chart.title,
                    source_citation=chart.source_citation,
                    note=getattr(chart, "note", "") or "",
                    width_percent=80,
                    placement="center",
                )
                placements.append(placement)

        return placements

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Select Unsplash images for cover and section headers
    # ─────────────────────────────────────────────────────────────────────

    async def _generate_cover_search_term(self, report: FinalReport) -> str:
        """Use LLM to generate a context-aware Unsplash search term for the cover.

        Instead of hardcoded substring matching that always falls back to
        "modern business abstract", the LLM analyzes the question and picks
        a specific, visual search term that reflects the actual topic.
        """
        # Build a quick summary of the report topic
        section_titles = [s.title for s in report.sections[:8]]
        sections_str = "; ".join(section_titles) if section_titles else "N/A"

        prompt = (
            "You are selecting a cover image for a business consulting report.\n"
            f"Report question: {report.question}\n"
            f"Report sections: {sections_str}\n"
            f"Recommendation: {report.recommendation.value}\n\n"
            "Generate a SINGLE Unsplash search term (2-5 words) that:\n"
            "- Is visually specific (e.g., 'Mumbai skyline at dusk', not 'city')\n"
            "- Reflects the actual topic, industry, or geography of the question\n"
            "- Would return high-quality, professional landscape photos\n"
            "- Is NOT generic (avoid 'business', 'office', 'abstract')\n\n"
            "Return JSON: {\"search_term\": \"your search term here\"}"
        )

        try:
            response = await self._llm_complete(
                user_prompt=prompt,
                urgency=TaskUrgency.NORMAL,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            if response.success and response.content:
                import json
                data = json.loads(response.content)
                if isinstance(data, dict):
                    raw_term = data.get("search_term")
                    if isinstance(raw_term, str):
                        term = raw_term.strip()
                        if term and len(term) < 100:
                            return term
        except (ValueError, KeyError, TypeError):
            pass

        # Fallback: keyword-based matching (improved with word-level matching)
        question_lower = report.question.lower()
        for key, term in COVER_IMAGE_SEARCH_TERMS.items():
            if key == "general":
                continue
            # Match on word boundaries, not substring (so "ma" doesn't match "market")
            if key in question_lower.split() or f" {key} " in f" {question_lower} ":
                return term

        return COVER_IMAGE_SEARCH_TERMS.get("general", "modern business abstract")

    def _pick_unused_image(self, images: list[Any]) -> Any | None:
        """Return the first image whose ID hasn't been used yet (L5.17).

        Guarantees no image is reused across the cover and section headers.
        Falls back to the first candidate only if every option is already used
        (better a repeat than no image), and to None if the list is empty.
        """
        if not images:
            return None
        for img in images:
            img_id = getattr(img, "id", None)
            if not img_id or img_id not in self._used_image_ids:
                return img
        # All candidates already used, return the first as a last resort.
        return images[0]

    async def _select_cover_image(self, report: FinalReport) -> ImageSelection | None:
        """Select a cover image from Unsplash.

        Cover image = full-bleed, relevant to the topic, 300 DPI.
        Uses LLM to generate a context-aware search term based on the
        actual question content, not hardcoded substring matching.
        """
        search_term = await self._generate_cover_search_term(report)

        try:
            unsplash_tool = self.get_tool(ToolName.UNSPLASH)
            os.makedirs(self.IMAGE_DIR, exist_ok=True)

            search_result = await unsplash_tool.search(
                query=search_term,
                per_page=5,
                orientation="landscape",
            )

            if not search_result.images:
                return None

            # Pick the first suitable image and reserve its ID so no section
            # reuses it later (L5.17).
            img = self._pick_unused_image(search_result.images)
            if img is None:
                return None
            photographer = img.photographer or "Unknown"
            photo_id = img.id
            if photo_id:
                self._used_image_ids.add(photo_id)

            # Download using the UnsplashClient's download_image method.
            # Fix 3.6: full resolution"regular" (1080px) can never pass
            # the cover pipeline's 1920px no-upscale gate.
            local_path = await unsplash_tool.download_image(img, quality="high")

            if not local_path or not os.path.exists(local_path):
                return None

            return ImageSelection(
                id="img_cover_001",
                page_type=PageType.COVER,
                search_term=search_term,
                image_path=local_path,
                photographer=photographer,
                unsplash_id=photo_id,
                caption=f"Source: Unsplash via {photographer}",
                placement="full_bleed",
                width_percent=100,
                page_number=1,
            )

        except (ValueError, AttributeError, RuntimeError, OSError):
            return None

    async def _generate_section_search_term(self, section: AnalysisSection) -> str:
        """Use LLM to generate a context-aware Unsplash search term for a section.

        Analyzes the section title, agent type, and key insight to generate
        a specific, visually relevant search term, not a generic stock photo.
        """
        # First try hardcoded terms based on agent name (fast path)
        agent_name = section.agent if isinstance(section.agent, str) else str(section.agent)
        agent_key = agent_name.lower().replace(" ", "_")

        # Try direct agent name match
        if agent_key in SECTION_IMAGE_SEARCH_TERMS:
            return SECTION_IMAGE_SEARCH_TERMS[agent_key]

        # Try partial agent name match (e.g., "market_analyst" → "market")
        for key in SECTION_IMAGE_SEARCH_TERMS:
            if key in agent_key or agent_key.startswith(key):
                return SECTION_IMAGE_SEARCH_TERMS[key]

        # Use LLM for context-aware search term
        body_preview = section.body[:500] if section.body else ""
        prompt = (
            "You are selecting a header image for a section of a business consulting report.\n"
            f"Section title: {section.title}\n"
            f"Section topic: {agent_name.replace('_', ' ')}\n"
            f"Key insight: {section.key_insight[:200] if section.key_insight else 'N/A'}\n"
            f"Content preview: {body_preview[:300]}\n\n"
            "Generate a SINGLE Unsplash search term (2-5 words) that:\n"
            "- Is visually specific to THIS section's topic\n"
            "- Would return professional, landscape photos\n"
            "- Is NOT generic (avoid 'business', 'office', 'abstract')\n"
            "- Reflects the actual content, not just the section name\n\n"
            "Return JSON: {\"search_term\": \"your search term here\"}"
        )

        try:
            response = await self._llm_complete(
                user_prompt=prompt,
                urgency=TaskUrgency.NORMAL,
                temperature=0.4,
                response_format={"type": "json_object"},
            )
            if response.success and response.content:
                import json
                data = json.loads(response.content)
                if isinstance(data, dict):
                    raw_term = data.get("search_term")
                    if isinstance(raw_term, str):
                        term = raw_term.strip()
                        if term and len(term) < 100:
                            return term
        except (ValueError, KeyError, TypeError):
            pass

        # Fallback: try title-based matching with normalization
        title_normalized = section.title.lower().replace(" ", "_")
        for key, term in SECTION_IMAGE_SEARCH_TERMS.items():
            if key in title_normalized:
                return term

        return SECTION_IMAGE_SEARCH_TERMS.get("general", "modern business abstract")

    # ─────────────────────────────────────────────────────────────────
    # P2-33: topic-relevant section imagery (query construction, caption,
    # credit). Query/caption/credit construction are pure functions so the
    # relevance contract is unit-testable without an Unsplash round trip.
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def build_section_image_query(
        subject: str,
        geography: str,
        section_topic: str,
    ) -> str:
        """Build the search query from the engagement, per P2-33 fix item 1:
        ``{subject} {geography} {topic}`` → ``{subject} {topic}`` → topic.

        The audit's measured defect was that the static
        ``SECTION_IMAGE_SEARCH_TERMS`` map never interpolated the engagement
        subject, so a manufacturing chapter fetched a crypto candlestick
        photo. The subject leads the query so the engine ranks on-topic
        results first.
        """
        parts = [p.strip() for p in (subject, geography, section_topic) if p and p.strip()]
        return " ".join(parts) if parts else section_topic.strip()

    @staticmethod
    def build_section_image_caption(
        section_title: str,
        photographer: str,
    ) -> str:
        """The figcaption is a caption, not a photo credit (P2-33 fix item 4).

        Report B page 8 printed ``Source: Unsplash via Maxim Hopman`` where a
        caption belongs; the credit moves to the colophon via
        :meth:`build_image_credit`.
        """
        title = (section_title or "").strip()
        return f"{title}: illustrated." if title else "Section illustration."

    @staticmethod
    def build_image_credit(photographer: str) -> str:
        """The colophon credit line: the ONLY place a photo credit renders."""
        name = (photographer or "").strip() or "Unknown"
        return f"Source: Unsplash via {name}"

    def _promote_section_image_to_cover(
        self,
        section_images: dict[str, ImageSelection],
    ) -> ImageSelection | None:
        """Promote one downloaded section photo when cover acquisition failed.

        Cover and section searches use the same licensed Unsplash source and
        print-grade download path. If the dedicated cover query is too narrow,
        rate-limited, or its first download fails while section searches still
        succeed, shipping a typographic placeholder wastes imagery that is
        already available locally. Promote the first section photo and remove
        it from the section map so the same image is not repeated in the body.
        """
        if not section_images:
            return None

        section_id, image = next(iter(section_images.items()))
        del section_images[section_id]
        self._log(
            "DESIGNER: dedicated cover image unavailable; promoting downloaded "
            f"section image {image.unsplash_id or image.id!r} to full-bleed cover"
        )
        return image.model_copy(
            update={
                "id": "img_cover_fallback",
                "page_type": PageType.COVER,
                "section_id": "",
                "placement": "full_bleed",
                "width_percent": 100,
                "page_number": 1,
                "caption": self.build_image_credit(image.photographer),
            }
        )

    async def _select_section_images(self, report: FinalReport) -> dict[str, ImageSelection]:
        """Select Unsplash images for each section header.

        Section images = 40% page width, right-aligned, with caption.
        Uses LLM to generate context-aware search terms per section based
        on actual content, not hardcoded generic terms.

        P2-33: candidates pass the ImageRelevanceGate before download. A
        section with no candidate that clears the relevance floor gets NO
        image, which the audit requires is strictly better than a wrong one.
        """
        section_images: dict[str, ImageSelection] = {}

        if not report.sections:
            return section_images

        # The engagement subject for query interpolation and relevance
        # scoring. FinalReport has no dedicated subject/geography field, so
        # the question is the honest proxy available at delivery time.
        subject = getattr(report, "question", "") or ""
        gate = ImageRelevanceGate()

        try:
            unsplash_tool = self.get_tool(ToolName.UNSPLASH)
            os.makedirs(self.IMAGE_DIR, exist_ok=True)

            for section in report.sections:
                search_term = await self._generate_section_search_term(section)
                # P2-33: interpolate the engagement subject into the query so
                # the engine ranks on-topic photos first.
                search_term = self.build_section_image_query(
                    subject=subject,
                    geography="",
                    section_topic=search_term,
                )

                # Request more candidates than needed so the relevance gate
                # and dedup have room to pick a fresh, on-topic image even
                # when the top results are off-topic or already used (L5.17).
                search_result = await unsplash_tool.search(
                    query=search_term,
                    per_page=8,
                    orientation="landscape",
                )

                if not search_result.images:
                    continue

                # P2-33: keep only candidates that clear the relevance floor
                # and are not chart-like decoration, THEN prefer one not
                # already used by the cover or an earlier section.
                relevant = [
                    c for c in search_result.images
                    if gate.is_relevant(c, subject=subject, topic=section.title or "")
                ]
                img = self._pick_unused_image(relevant)
                if img is None:
                    # No on-topic candidate: no image is better than a wrong one.
                    continue
                photographer = img.photographer or "Unknown"
                photo_id = img.id
                if photo_id:
                    self._used_image_ids.add(photo_id)

                # Fix 3.6: full resolution, the section pipeline targets
                # 2000px wide (print grade at 300 DPI), which a 1080px
                # "regular" download can never pass under the no-upscale rule.
                local_path = await unsplash_tool.download_image(img, quality="high")

                if not local_path or not os.path.exists(local_path):
                    continue

                section_images[section.id] = ImageSelection(
                    id=f"img_section_{section.id}",
                    page_type=PageType.SECTION,
                    section_id=section.id,
                    search_term=search_term,
                    image_path=local_path,
                    photographer=photographer,
                    unsplash_id=photo_id,
                    # P2-33: the figcaption is a caption, not a photo credit.
                    caption=self.build_section_image_caption(
                        section_title=section.title or "",
                        photographer=photographer,
                    ),
                    placement="right",
                    width_percent=40,
                )

        except (ValueError, AttributeError, RuntimeError, OSError):
            pass

        return section_images

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Receive chart images from Data Visualizer
    # ─────────────────────────────────────────────────────────────────────

    def _receive_chart_images(
        self,
        visualization_output: VisualizationOutput | None = None,
        report: FinalReport | None = None,
    ) -> dict[str, list[ChartPlacement]]:
        """Receive chart images from the Data Visualizer and organize by section.

        Fix 3.7, three defects in the original, all of which ended with a
        300-DPI PNG on disk that no page ever displayed:

        1. **Homeless charts were keyed by whatever string arrived.** A chart
           mined from `report.key_findings` carries `section=""`. The template
           iterates `section_charts[section.id]`, and no section has the id
           `""`, so those charts were placed into the dict and then rendered by
           nobody. Those are the *headline* exhibits. They are now re-homed
           onto a real section (by authoring agent, then first section).
        2. **Charts with no `image_path` were still placed.** A chart whose
           export failed produced `<img src="">`, a broken-image box under a
           real "Exhibit N" number, which also consumed a number and pushed
           every later exhibit's numbering out by one. They are now dropped.
        3. **The methodology note was never copied**, so the exhibit footer
           shipped with a `Source:` line and no `Note:` line.
        """
        if visualization_output:
            self._visualization_output = visualization_output

        self._chart_placements = {}
        if not self._visualization_output:
            return self._chart_placements

        # Build the same agent -> section index the miner uses, so a re-homed
        # chart lands in the section whose analyst produced its numbers rather
        # than in an arbitrary one.
        sections = list(getattr(report, "sections", None) or []) if report else []
        valid_ids: set[str] = set()
        first_id = ""
        section_id_by_agent: dict[str, str] = {}
        for section in sections:
            sid = getattr(section, "id", "") or ""
            if not sid:
                continue
            valid_ids.add(sid)
            if not first_id:
                first_id = sid
            agent = (getattr(section, "agent", "") or "").strip()
            if agent and agent not in section_id_by_agent:
                section_id_by_agent[agent] = sid

        for chart in self._visualization_output.charts:
            # Defect 2: an exhibit with no figure is not an exhibit.
            if not chart.image_path:
                self._log(
                    f"DESIGNER: dropping chart {chart.id!r}, no image_path "
                    f"(export failed); it would render as a broken image and "
                    f"consume an exhibit number"
                )
                continue

            section_id = chart.section
            # Defect 1: re-home anything that does not name a real section.
            if valid_ids and section_id not in valid_ids:
                agent = ""
                for sec in sections:
                    if getattr(sec, "id", "") == section_id:
                        agent = getattr(sec, "agent", "") or ""
                        break
                rehomed = section_id_by_agent.get(agent) or first_id
                self._log(
                    f"DESIGNER: re-homing chart {chart.id!r} from "
                    f"section {section_id!r} to {rehomed!r}, the original "
                    f"section id matches no section, so the exhibit would "
                    f"never have rendered"
                )
                section_id = rehomed

            placement = ChartPlacement(
                chart_id=chart.id,
                section_id=section_id,
                image_path=chart.image_path,
                caption=chart.caption or chart.title,
                source_citation=chart.source_citation,
                # Defect 3: carry the note so the footer is Note: + Source:.
                note=getattr(chart, "note", "") or "",
                width_percent=80,
                placement="center",
            )
            if section_id not in self._chart_placements:
                self._chart_placements[section_id] = []
            self._chart_placements[section_id].append(placement)

        self._enforce_exhibit_anatomy(self._chart_placements)
        return self._chart_placements

    # ─────────────────────────────────────────────────────────────────────
    # Fix 4.4: MGI exhibit anatomy is a contract, not a suggestion
    # ─────────────────────────────────────────────────────────────────────

    def _enforce_exhibit_anatomy(
        self, placements: dict[str, list[ChartPlacement]]
    ) -> list[str]:
        """Repair the four-part exhibit anatomy in place; report what was wrong.

        The anatomy is: **number → action title → figure → `Note:` → `Source:`**
        (§3.9). The template already *emitted* all five parts, but nothing
        *enforced* them, and every part is independently optional in Jinja. So a
        chart arriving without a caption rendered as a numbered, sourced exhibit
        with **no title**, and one arriving with neither note nor source
        rendered with **no footer at all**, no hairline, no provenance. Both
        were silent: no exception, no log line, and the PDF still looked
        plausible. Measured before this fix, all five degenerate combinations
        rendered clean.

        `image_path` was the only part already guarded (a figure-less chart is
        dropped upstream), which is why the other four are handled here.

        Repair rather than drop, deliberately. Dropping a titleless exhibit
        would discard real extracted numbers and renumber every later exhibit;
        the audit's own §3.9 note about insertions shifting numbering applies
        equally to deletions. Instead:

        * a missing **action title** falls back to the chart id humanised, an
          honest placeholder that is obviously provisional in review, unlike a
          confident invented takeaway;
        * a missing **note** is left empty. A note describes *how* a figure was
          constructed. Inventing one is the same class of defect as inventing a
          geography, so the line stays absent and the omission is reported;
        * a missing **source** is the one that matters most for MBB parity, both benchmark documents carry a source under every single exhibit, so it is filled with the explicit, non-deceptive
          ``"Source: HYPERION analysis"`` rather than being silently dropped.

        Returns the list of defects found, so callers can log or gate on it.
        """
        defects: list[str] = []
        for section_id, charts in placements.items():
            for chart in charts:
                cid = chart.chart_id or "<unnamed>"
                where = f"{section_id}/{cid}"

                if not (chart.caption or "").strip():
                    chart.caption = self._humanise_chart_id(cid)
                    defects.append(f"{where}: no action title (used {chart.caption!r})")

                if not (chart.image_path or "").strip():
                    # Should be unreachable: figure-less charts are dropped
                    # before placement. Recorded rather than assumed away.
                    defects.append(f"{where}: no figure")

                if not (chart.note or "").strip():
                    defects.append(f"{where}: no Note: line (left absent, not invented)")

                if not (chart.source_citation or "").strip():
                    chart.source_citation = "Source: HYPERION analysis"
                    defects.append(f"{where}: no Source: line (defaulted)")

        if defects:
            self._log(
                f"DESIGNER: exhibit anatomy repaired on {len(defects)} field(s), "
                + "; ".join(defects[:8])
                + (" …" if len(defects) > 8 else "")
            )
        return defects

    @staticmethod
    def _humanise_chart_id(chart_id: str) -> str:
        """`revenue_growth_2024` → `Revenue growth 2024`.

        Deliberately plain. The point is a title a reviewer immediately reads as
        a placeholder, not a fabricated MBB action title.
        """
        cleaned = re.sub(r"[_\-]+", " ", chart_id).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned[:1].upper() + cleaned[1:] if cleaned else "Exhibit"

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Render HTML template with Jinja2
    # ─────────────────────────────────────────────────────────────────────

    async def _render_html_template(
        self,
        report: FinalReport,
        cover_image: ImageSelection | None,
        section_images: dict[str, ImageSelection],
        chart_placements: dict[str, list[ChartPlacement]],
    ) -> str:
        """Render the HTML template with Jinja2.

        Uses the Jinja2 tool to render the premium report template with:
        - Cover page (full-bleed image, title, recommendation, confidence)
        - Table of Contents
        - Executive Summary (key findings, critical risks)
        - Analysis Sections (key insight box, body, images, charts, implication)
        - Risk Analysis (risk matrix, top risks, black swans)
        - Methodology (agents, sources, data points, limitations)
        - Appendix (full source list)
        - Back Cover (wordmark, tagline, confidentiality)
        """
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)

        # Convert image paths to absolute so they resolve correctly
        # regardless of where the HTML file is opened from
        cover_image_abs = None
        if cover_image and cover_image.image_path:
            abs_path = os.path.abspath(cover_image.image_path)
            if os.path.exists(abs_path):
                cover_image_abs = cover_image.model_copy(update={"image_path": abs_path})
            else:
                cover_image_abs = cover_image

        section_images_abs: dict[str, ImageSelection] = {}
        for sid, img in section_images.items():
            if img and img.image_path:
                abs_path = os.path.abspath(img.image_path)
                if os.path.exists(abs_path):
                    section_images_abs[sid] = img.model_copy(update={"image_path": abs_path})
                else:
                    section_images_abs[sid] = img
            else:
                section_images_abs[sid] = img

        # Convert chart paths to absolute too
        chart_placements_abs: dict[str, list[ChartPlacement]] = {}
        for sid, charts in chart_placements.items():
            abs_charts = []
            for cp in charts:
                if cp.image_path:
                    abs_path = os.path.abspath(cp.image_path)
                    if os.path.exists(abs_path):
                        abs_charts.append(cp.model_copy(update={"image_path": abs_path}))
                    else:
                        abs_charts.append(cp)
                else:
                    abs_charts.append(cp)
            chart_placements_abs[sid] = abs_charts

        # The CSS is an INTERNAL build input, not a deliverable.
        #
        # HISTORY: this used to write directly to self.CSS_OUTPUT
        # (output/<slug>.css) *before* the HTML/PDF were generated. When PDF
        # rendering then failed, that stylesheet was the only file left in
        # output/, which is exactly how a 34-minute engagement delivered a
        # lone `should_india_reduce_its_dependence_on_the_imports.css` and no
        # report. A build intermediate must never be able to outlive, or be
        # mistaken for, the deliverable.
        #
        # It is therefore written to a clearly-marked build directory. The
        # PDF/HTML deliverable is the only thing that lands in output/.
        css_content = CSS_TEMPLATE
        try:
            os.makedirs(self.BUILD_DIR, exist_ok=True)
            with open(self._css_build_path(), "w", encoding="utf-8") as f:
                f.write(css_content)
        except OSError as e:
            self._log(f"DESIGNER: could not write build CSS ({e}); continuing with inline CSS")

        # W-09: two transformations happen here, both named and explicit.
        # 1. Telemetry is routed to its own destination: an operator
        #    artifact under reports/diagnostics/ (JSON + HTML). This is
        #    where the quality scorecard, the roster and the fact-check
        #    counts belong. They are genuinely valuable there, they are
        #    simply not client copy.
        # 2. The template receives ClientReport, a view of the report
        #    that carries no telemetry attributes at all. A client
        #    template holding this object cannot resolve an agent name,
        #    a quality score or a fact-check count even if one is left
        #    behind in the markup: the leak is impossible at the type
        #    level, not filtered after the fact.
        # These run BEFORE the try: both render paths (JINJA2 tool and the
        # manual fallback) must receive the SAME ClientReport view. If they
        # ran inside the try, a get_tool() failure would leave the fallback
        # holding an undefined name and silently ship the last-resort strip
        # path with no sections at all (Fix 3.5's exact class of bug).
        telemetry_path = write_telemetry_artifact(report)
        self._log(f"DESIGNER: operator telemetry artifact written to {telemetry_path}")

        # W-10: the methodology section is normally built by the orchestrator,
        # which holds the DAG and the insufficiency resolutions the richer
        # subsections need. When the designer is driven directly (tests, a
        # re-render of a stored report, the floor report path) that context is
        # absent, and printing no methodology at all would be worse than
        # printing one built from the report alone: the reader uses this page to
        # calibrate the rest. build_methodology is deterministic and never
        # raises on thin input, so this is a safe unconditional fallback.
        if getattr(report, "methodology", None) is None:
            try:
                report.methodology = build_methodology(report)
                self._log(
                    "DESIGNER: methodology built from the report alone "
                    "(no engagement DAG in this context)"
                )
            except (ValueError, TypeError, AttributeError) as exc:
                # Never lose the whole render over the methodology page. The
                # template's defensive branch states that the account could not
                # be assembled, which is honest, and the failure is logged.
                self._log(f"DESIGNER: methodology build failed ({exc})")

        client_report = ClientReport.from_report(report)

        try:
            jinja2_tool = self.get_tool(ToolName.JINJA2)

            # Prepare template context
            context = {
                "report": client_report,
                "cover_image": cover_image_abs,
                "section_images": section_images_abs,
                "section_charts": chart_placements_abs,
                "palette": PDF_PALETTE,
                "css_content": css_content,
                # These builders still read the full FinalReport: they need
                # Source objects, risk fields and endnote provenance that the
                # client view deliberately does not carry. They emit HTML
                # strings built with html_escape over real fields; no
                # telemetry fields are read.
                "risk_analysis_html": self._build_risk_analysis_html(report),
                "appendix_sources_html": self._build_appendix_sources_html(report),
                # Fix 4.5. Both call sites must be fed: this dict and the
                # fallback env below. Fix 3.5 was exactly this class of bug,
                # a filter registered in one env and not the other.
                "endnotes_html": self._build_endnotes_html(report),
            }

            html_content = await jinja2_tool.render_template(
                template_string=HTML_TEMPLATE,
                context=context,
            )

            # render_template returns a TemplateRenderResult, not a string
            if hasattr(html_content, "html") and html_content.success:
                html_str = html_content.html
            elif hasattr(html_content, "html"):
                # Template rendered but with errors, use what we got
                html_str = html_content.html or ""
            else:
                html_str = str(html_content)

            if not html_str:
                raise RuntimeError("Template rendering produced empty HTML")

            with open(self.HTML_OUTPUT, "w", encoding="utf-8") as f:
                f.write(html_str)

            return self.HTML_OUTPUT

        except (ValueError, AttributeError, RuntimeError):
            # Fallback: render manually with Jinja2
            try:
                from jinja2 import BaseLoader, Environment

                from hyperion.output.render import TemplateRenderer

                env = Environment(loader=BaseLoader(), autoescape=True)
                # Fix 3.5: this fallback previously registered
                #   md_to_html = lambda v: v or ""
                # a plain-str passthrough. TemplateRenderer._markdown_to_html
                # returns markupsafe.Markup; a plain str is autoescaped by
                # Jinja, so every markdown-produced <p>/<strong> tag rendered
                # as VISIBLE text on the page (audit §3.4 escaped-HTML
                # divergence). The fallback must use the SAME real filter as
                # the production JINJA2-tool path or it ships a different
                # document.
                _fallback_renderer = TemplateRenderer()
                env.filters["md_to_html"] = _fallback_renderer._markdown_to_html
                env.filters["clean_dict_repr"] = _fallback_renderer._clean_dict_repr
                template = env.from_string(HTML_TEMPLATE)
                html_str = template.render(
                    report=client_report,
                    cover_image=cover_image_abs,
                    section_images=section_images_abs,
                    section_charts=chart_placements_abs,
                    palette=PDF_PALETTE,
                    css_content=css_content,
                    risk_analysis_html=self._build_risk_analysis_html(report),
                    appendix_sources_html=self._build_appendix_sources_html(report),
                    endnotes_html=self._build_endnotes_html(report),
                )
            except Exception:  # noqa: BLE001 - best-effort, failure must not propagate
                # Last resort: strip Jinja2 tags and do basic format
                html_str = HTML_TEMPLATE.replace("{{ css_content | safe }}", css_content)
                html_str = html_str.replace("{{ report.question }}", str(report.question))
                html_str = html_str.replace("{{ report.recommendation | upper "
                    "}}", str(report.recommendation.value).upper())

            with open(self.HTML_OUTPUT, "w", encoding="utf-8") as f:
                f.write(html_str)

            return self.HTML_OUTPUT

    def _build_risk_analysis_html(self, report: FinalReport) -> str:
        """Build the risk analysis HTML section.

        Three defects fixed here (D5.1), all of the same family as the ones 4.5
        fixed in the appendix builders, and all invisible for the same reason:

        1. ``getattr(risk, "name", "Unknown")``, **`Risk` has no `name` field.**
           Its descriptive field is ``description``. So this expression could
           never return anything but the literal string ``"Unknown"``, and the
           Risk column of the top-risks table printed ``Unknown`` on every row of
           every report ever produced. ``"Unknown"`` is one of the four tokens
           ``tools/audit_render_probe.py`` counts as a **template leak**, which
           §11 exit criterion 11 requires to be zero, so this single wrong field
           name was silently breaking a headline Definition-of-Done metric.
           The defensive third argument to ``getattr`` is exactly what hid it: a
           direct ``risk.name`` would have raised ``AttributeError`` on the first
           run. Now uses direct attribute access, per the ban 4.5 established.
        2. Every cell was interpolated **unescaped**. Risk descriptions and
           mitigations are LLM-authored prose that routinely contains ``&`` and
           ``<`` (e.g. "margin < 10% & falling"), which would corrupt the table.
        3. ``risk_score`` and the mitigation ``owner`` were not shown at all,
           though both are populated, the table showed probability and impact
           but not their product, which is the number the ranking is *by*.

        The 5×5 matrix's zone counts are now rendered too: the agent computes
        them (``_build_risk_matrix``) and, until D5.1, threw them away.
        """
        if not report.risk_analysis:
            return "<p>No risk analysis available.</p>"

        analysis = report.risk_analysis
        html_parts = ["<div class='risk-matrix no-break'>"]

        # Zone summary, the 5x5 matrix's headline, previously discarded.
        zone_counts = (analysis.risk_matrix or {}).get("zone_counts") or {}
        if zone_counts:
            red = int(zone_counts.get("red", 0))
            yellow = int(zone_counts.get("yellow", 0))
            green = int(zone_counts.get("green", 0))
            html_parts.append(
                "<p class='risk-zone-summary'>"
                f"<strong>{red}</strong> in the red zone (mitigate now), "
                f"<strong>{yellow}</strong> amber (plan mitigation), "
                f"<strong>{green}</strong> green (monitor)."
                "</p>"
            )

        html_parts.append("<h3>Top Risks</h3>")
        html_parts.append("<table class='data-table'>")
        html_parts.append(
            "<tr><th>Risk</th><th>Category</th><th>P</th><th>I</th>"
            "<th>Score</th><th>Mitigation</th><th>Owner</th></tr>"
        )

        # Prefer the ranked list; fall back to the full list if ranking is absent.
        risks = analysis.top_risks or analysis.risks
        for risk in risks[:10]:
            html_parts.append(
                "<tr>"
                f"<td>{html_escape(risk.description)}</td>"
                f"<td>{html_escape(risk.category.value.title())}</td>"
                f"<td>{risk.probability}</td>"
                f"<td>{risk.impact}</td>"
                f"<td>{risk.risk_score}</td>"
                f"<td>{html_escape(risk.mitigation)}</td>"
                f"<td>{html_escape(risk.owner)}</td>"
                "</tr>"
            )

        html_parts.append("</table></div>")
        return "\n".join(html_parts)

    def _build_appendix_sources_html(self, report: FinalReport) -> str:
        """Build the appendix source list HTML.

        Two defects fixed here while adding 4.5's back matter:

        1. The fallback title was the literal string ``"Unknown"``, which
           ``tools/audit_render_probe.py`` counts as a **template leak** (§11
           exit criterion 11 requires zero). A source with no title now falls
           back to its own URL, which is real information; only a source with
           neither is described as untitled, and it says so in words rather than
           printing a placeholder that reads like a bug.
        2. Titles and URLs were interpolated into HTML **unescaped**. A source
           title containing ``&`` or ``<``, ordinary in news headlines, would
           corrupt the table or silently swallow text. Now escaped.
        """
        html_parts = ["<div class='no-break'><h3>Full Source List</h3>"]
        html_parts.append("<table class='data-table'>")
        html_parts.append("<tr><th>#</th><th>Source</th><th>URL</th></tr>")

        source_num = 1
        for section in report.sections:
            for source in section.sources:
                raw_title = (getattr(source, "title", "") or "").strip()
                raw_url = (getattr(source, "url", "") or "").strip()
                title = raw_title or raw_url or "Untitled source"
                html_parts.append(
                    f"<tr><td>{source_num}</td>"
                    f"<td>{html_escape(title)}</td>"
                    f"<td>{html_escape(raw_url)}</td></tr>"
                )
                source_num += 1

        html_parts.append("</table></div>")
        return "\n".join(html_parts)

    # ─────────────────────────────────────────────────────────────────────
    # Fix 4.5: MBB front/back matter, At-a-glance, Endnotes, Technical appendix
    # ─────────────────────────────────────────────────────────────────────

    def _build_endnotes_html(self, report: FinalReport) -> str:
        """Numbered endnotes, each tied to the chapter that cited it.

        A flat source list already existed in the appendix, but it is not an
        endnote apparatus: it cannot answer "which claim rested on this?".
        MGI's endnotes are numbered continuously across the document and
        grouped by chapter, so a reader can walk from an argument to its
        evidence. Numbering is assigned here, server-side, because Jinja's
        ``loop.index`` resets per section and would restart at 1 in every
        chapter.

        Sources are de-duplicated by URL within a chapter, the same URL
        legitimately supports several findings, and printing it four times
        makes the apparatus look padded rather than thorough.
        """
        chapters: list[tuple[str, list[tuple[int, str, str]]]] = []
        note_num = 1
        for section in report.sections:
            seen: set[str] = set()
            entries: list[tuple[int, str, str]] = []
            # Typed attribute access, not ``getattr(..., default)``. While
            # writing this method the defensive-getattr style silently hid three
            # wrong field names elsewhere in 4.5 (see
            # ``_build_technical_appendix_html``): the fallback made a schema
            # mismatch render as "no data" instead of raising. An AttributeError
            # here is strictly preferable, it fails loudly at the seam.
            for source in section.sources:
                url = (source.url or "").strip()
                title = (source.title or "").strip()
                key = url or title
                if not key or key in seen:
                    continue
                seen.add(key)
                entries.append((note_num, title or url or "Untitled source", url))
                note_num += 1
            if entries:
                chapters.append((section.title or "Chapter", entries))

        if not chapters:
            # Honest emptiness. An endnotes page implying evidence that does not
            # exist is worse than one that admits the shortfall.
            return (
                "<p class='endnote-empty'>No per-chapter sources were recorded "
                "for this engagement.</p>"
            )

        parts: list[str] = []
        for chapter_title, entries in chapters:
            parts.append(
                f"<h3 class='endnote-chapter'>{html_escape(chapter_title)}</h3>"
            )
            parts.append("<ol class='endnote-list'>")
            for num, title, url in entries:
                # The number is emitted explicitly, not left to the <ol> marker,
                # so it stays correct and citable even if the list is restyled.
                url_html = (
                    f" <span class='endnote-url'>{html_escape(url)}</span>" if url else ""
                )
                parts.append(
                    f"<li value='{num}'><span class='endnote-num'>{num}.</span> "
                    f"{html_escape(title)}{url_html}</li>"
                )
            parts.append("</ol>")
        return "\n".join(parts)

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Generate PDF with WeasyPrint
    # ─────────────────────────────────────────────────────────────────────

    def _css_build_path(self) -> str:
        """Path of the stylesheet build intermediate.

        Lives under BUILD_DIR, never in output/. See the comment at the CSS
        write site: a stylesheet left in output/ after a failed render was
        mistaken for the deliverable.
        """
        return os.path.join(self.BUILD_DIR, os.path.basename(self.CSS_OUTPUT))

    # ─────────────────────────────────────────────────────────────────────
    # Page flow validation
    # ─────────────────────────────────────────────────────────────────────

    def _validate_page_flow(self, pages: list[PageLayout]) -> tuple[bool, bool]:
        """Validate that the page flow has no blank pages or orphaned images.

        Returns (no_blank_pages, no_orphaned_images).
        """
        no_blank = True
        no_orphaned = True

        for page in pages:
            # Check for blank pages (no content blocks and no images)
            if (
                not page.content_blocks
                and not page.images
                and not page.charts
                and page.page_type not in (PageType.BACK_COVER,)
            ):
                no_blank = False

            # Check for orphaned images (image without text context on same page)
            if page.images and not page.content_blocks and page.page_type != PageType.COVER:
                no_orphaned = False

        return (no_blank, no_orphaned)

    # ─────────────────────────────────────────────────────────────────────
    # Main execution, the 8-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
        final_report: FinalReport | None = None,
        quality_score: QualityScore | None = None,
        visualization_output: VisualizationOutput | None = None,
    ) -> LayoutPlan:
        """Execute the Presentation Designer's 8-step methodology.

        Steps (§4.6, Agent 19):
        1. Receive FinalReport from Synthesis Lead
        2. Receive QualityScore from Quality Gate
        3. Design layout plan (which content goes on which page)
        4. Select Unsplash images for cover and section headers
        5. Receive chart images from Data Visualizer
        6. Render HTML template with Jinja2
        7. Generate PDF with WeasyPrint
        8. Post-process images with Pillow (via Render Engine)
        """
        # Subscribe to bus
        self.subscribe_to_bus()

        # Set dynamic output filenames based on question
        if question:
            slug = self._slugify(question)
            self.HTML_OUTPUT = f"output/{slug}.html"
            self.CSS_OUTPUT = f"output/{slug}.css"
            self.PDF_OUTPUT = f"output/{slug}.pdf"
            self.IMAGE_DIR = f"output/{slug}_images"
            os.makedirs(self.OUTPUT_DIR, exist_ok=True)
            os.makedirs(self.IMAGE_DIR, exist_ok=True)

        # Step 1: Receive FinalReport
        await self._transition(AgentState.WORKING, "Step 1: Receiving FinalReport")
        report = await self._receive_final_report(final_report)

        if report is not None:
            # P2-15: confidence is derived, never asserted. Every surface
            # (cover, At a Glance, Executive Summary, Technical Appendix)
            # reads report.confidence; we pin it to derive_confidence() once
            # here so all four surfaces show the same token by construction.
            derived = derive_confidence(report)
            if derived != report.confidence:
                self._log(
                    f"CONFIDENCE: derived {derived.value.upper()} overrides "
                    f"asserted {report.confidence.value.upper()} "
                    f"(sources={report.total_sources})"
                )
                report.confidence = derived

        if not report:
            await self._transition(AgentState.DONE, "No FinalReport received")
            return LayoutPlan(engagement_id=engagement_id, confidence=ConfidenceLevel.LOW)

        # Step 2: Receive QualityScore (display only)
        await self._transition(AgentState.WORKING, "Step 2: Receiving QualityScore")
        await self._receive_quality_score(quality_score)
        # W-08: the escape hatch that used to live here is deleted, not
        # repaired. Delivery NEVER evaluates quality. The orchestrator's
        # terminal-state computation is the single ship/no-ship decision
        # point; if this agent is running at all, the report cleared the
        # gate and is laid out unconditionally. A second quality decision
        # point here is a second escape hatch.

        # Step 3: Design layout plan
        await self._transition(AgentState.WORKING, "Step 3: Designing layout plan")
        self._pages = self._design_layout_plan(report)

        # Step 4: Select Unsplash images
        await self._transition(AgentState.WORKING, "Step 4: Selecting Unsplash images for cover "
            "and sections")
        self._cover_image = await self._select_cover_image(report)
        self._section_images = await self._select_section_images(report)
        if self._cover_image is None:
            self._cover_image = self._promote_section_image_to_cover(
                self._section_images
            )

        # Assign images to pages
        if self._cover_image and self._pages:
            self._pages[0].images.append(self._cover_image)
        for page in self._pages:
            if page.page_type == PageType.SECTION and page.section_id in self._section_images:
                page.images.append(self._section_images[page.section_id])

        # Step 5: Receive chart images from Data Visualizer
        await self._transition(AgentState.WORKING, "Step 5: Receiving chart images from Data "
            "Visualizer")
        # `report` is passed so homeless charts can be re-homed onto a section
        # that actually exists (fix 3.7), without it the headline exhibits
        # mined from `key_findings` are rendered by nobody.
        self._receive_chart_images(visualization_output, report=report)

        # Assign charts to pages
        for page in self._pages:
            if page.page_type == PageType.SECTION and page.section_id in self._chart_placements:
                page.charts.extend(self._chart_placements[page.section_id])

        # Validate page flow
        no_blank, no_orphaned = self._validate_page_flow(self._pages)

        # Step 6: Render HTML template with Jinja2
        await self._transition(AgentState.WORKING, "Step 6: Rendering HTML template with Jinja2")
        html_path = await self._render_html_template(
            report=report,
            cover_image=self._cover_image,
            section_images=self._section_images,
            chart_placements=self._chart_placements,
        )

        # Step 7: W-03, the designer NO LONGER writes a PDF. Its contract is
        # the staged HTML + layout plan and nothing else; the Render Engine
        # is the single writer. The deleted `_generate_pdf` duplicated the
        # Render Engine's job, and the orchestrator's `layout_plan.pdf_path`
        # fallback then let an unaudited designer-rendered PDF become the
        # deliverable (RC-3/RC-4). Both are gone now.
        await self._transition(
            AgentState.WORKING,
            "Step 7: Staged HTML + layout plan handed to Render Engine "
            "(the single PDF writer)",
        )

        # Step 8: Post-process images with Pillow (via Render Engine)
        await self._transition(AgentState.WORKING, "Step 8: Post-processing images (handed to "
            "Render Engine)")

        # Collect all chart placements
        all_chart_placements: list[ChartPlacement] = []
        for placements in self._chart_placements.values():
            all_chart_placements.extend(placements)

        # Collect all section images
        all_section_images = list(self._section_images.values())

        # Determine confidence, W-03: the designer's confidence describes
        # its own artifact (the staged HTML + layout plan), not a PDF it no
        # longer authors.
        if html_path and no_blank and no_orphaned:
            confidence = ConfidenceLevel.HIGH
        elif html_path:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW

        # Build LayoutPlan
        layout_plan = LayoutPlan(
            engagement_id=engagement_id,
            pages=self._pages,
            total_pages=len(self._pages),
            cover_image=self._cover_image,
            section_images=all_section_images,
            chart_placements=all_chart_placements,
            html_template_path=html_path,
            css_path=self._css_build_path(),
            typography=TYPOGRAPHY,
            color_palette=PDF_PALETTE,
            no_blank_pages=no_blank,
            no_orphaned_images=no_orphaned,
            all_images_300_dpi=True,
            confidence=confidence,
        )

        # Publish layout plan to bus
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "finding_type": "layout_plan",
                "layout_plan": layout_plan.model_dump(),
                "total_pages": len(self._pages),
                "no_blank_pages": no_blank,
                "no_orphaned_images": no_orphaned,
                "cover_image": self._cover_image.image_path if self._cover_image else "",
                "section_images_count": len(all_section_images),
                "chart_placements_count": len(all_chart_placements),
            },
        )

        # Publish handoff to Render Engine
        await self.bus.publish(
            channel=Channel.HANDOFF,
            msg_type=MessageType.HANDOFF,
            sender=self.name,
            payload={
                "to_agent": "render_engine",
                "from_agent": self.name.value,
                "task": "render_deliverable",
                "context_bundle": {
                    "layout_plan": layout_plan.model_dump(),
                    "html_path": html_path,
                    "css_path": self._css_build_path(),
                    "pdf_output_path": self.PDF_OUTPUT,
                    "images_to_process": [img.image_path for img in all_section_images] +
                                         ([self._cover_image.image_path] if self._cover_image else []),
                    "charts_to_process": [cp.image_path for cp in all_chart_placements],
                },
                "message": (
                    f"Layout plan complete: {len(self._pages)} pages, "
                    f"{len(all_section_images)} section images, "
                    f"{len(all_chart_placements)} charts. "
                    f"Hand off to Render Engine for final assembly and "
                    f"single-writer PDF rendering."
                ),
            },
        )

        # Publish a finding for the layout plan
        finding = KeyFinding(
            id=f"finding_{hashlib.md5(f'presentation_designer_{engagement_id}'.encode()).hexdigest()[:8]}",
            agent=self.name.value,
            finding_type="layout_complete",
            title=f"Layout plan complete: {len(self._pages)} pages with {len(all_section_images)} images and {len(all_chart_placements)} charts",
            content=(
                f"Designed {len(self._pages)}-page layout. "
                f"Cover image: {'selected' if self._cover_image else 'missing'}. "
                f"Section images: {len(all_section_images)}. "
                f"Chart placements: {len(all_chart_placements)}. "
                f"Blank pages: {'none' if no_blank else 'detected'}. "
                f"Orphaned images: {'none' if no_orphaned else 'detected'}. "
                f"PDF: authored by Render Engine (single writer)."
            ),
            confidence=confidence,
        )
        await self._publish_finding(finding)

        await self._transition(
            AgentState.DONE,
            f"Layout plan complete: {len(self._pages)} pages, "
            f"{len(all_section_images)} images, "
            f"{len(all_chart_placements)} charts, "
            f"blank_pages: {'no' if no_blank else 'yes'}, "
            f"orphaned: {'no' if no_orphaned else 'yes'}, "
            f"pdf: render_engine (single writer), "
            f"confidence: {confidence.value}",
        )

        return layout_plan
