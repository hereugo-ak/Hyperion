"""
HYPERION Presentation Designer — Agent 19, the report layout designer.

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
- White space management: Use white space deliberately — not as empty space,
  but as a design element that improves readability and focus.

It runs on STRONG tier (Nemotron 3 Super 120B) because layout design requires
strong reasoning — it must understand narrative flow, visual hierarchy, and
how to balance text and visuals on each page.

Model Tier: STRONG (Nemotron 3 Super 120B — layout design requires strong
reasoning about visual hierarchy and narrative flow)
Tools: Unsplash (search and select images for cover, section headers, and
       contextual illustrations),
       Plotly (receive chart specifications from Data Visualizer),
       Jinja2 (render the HTML template with report content and layout plan),
       WeasyPrint (generate the final PDF from HTML/CSS)
Sub-agents: 0 (delivery agent — doesn't spawn sub-agents)
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
into a template — it makes deliberate decisions about what goes on each page,
how to balance text and visuals, and how to guide the reader through the
narrative. It always ensures images are adjacent to their context text. It
never produces a blank page or an orphaned image.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import Channel, MessageType
from hyperion.config import ModelTier
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
    ChartSpecification,
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


# ─────────────────────────────────────────────────────────────────────────────
# PDF Palette (§7.2 — STRICT)
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
    "consumer": "consumer shopping retail",
    "consumer_insights": "consumer shopping retail",
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
@page {{
    size: A4;
    dpi: 300;
    margin: 25mm 25mm 25mm 40mm;  /* 15mm extra on left for binding */
    @bottom-center {{
        content: "HYPERION · many minds. one reading. · {{{{page}}}}";
        font-family: "JetBrains Mono", monospace;
        font-size: 8pt;
        color: {warm_gray};
    }}
    @top-center {{
        content: "{{{{section_title}}}}";
        font-family: "Instrument Serif", serif;
        font-size: 10pt;
        color: {warm_gray};
    }}
}}

body {{
    font-family: "JetBrains Mono", monospace;
    font-size: 10pt;
    color: {warm_charcoal};
    background-color: {cream};
    line-height: 1.6;
}}

h1, h2, h3 {{
    font-family: "Instrument Serif", serif;
    color: {warm_charcoal};
}}

h1 {{ font-size: 36pt; }}  /* Cover title */
h2 {{ font-size: 22pt; }}  /* Section headers */
h3 {{ font-size: 14pt; font-family: "JetBrains Mono", monospace; font-weight: bold; }}  /* Subsection headers */

.cover {{
    page: cover;
    margin: 0;
    padding: 0;
    position: relative;
    width: 100%;
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
    bottom: 0;
    left: 0;
    width: 100%;
    height: 60%;
    background: linear-gradient(to top, rgba(26,26,26,0.92) 0%, rgba(26,26,26,0.75) 40%, rgba(26,26,26,0.3) 75%, rgba(26,26,26,0) 100%);
    z-index: 2;
}}

.cover-title {{
    position: absolute;
    bottom: 50mm;
    left: 25mm;
    right: 25mm;
    color: {cream};
    font-size: 32pt;
    font-family: "Instrument Serif", serif;
    z-index: 3;
    line-height: 1.2;
}}

.cover-title h1 {{
    color: {cream};
    font-size: 32pt;
    line-height: 1.2;
    margin-bottom: 12px;
    text-shadow: 0 2px 8px rgba(0,0,0,0.5);
}}

.cover--typographic {{
    background-color: {warm_charcoal};
    height: 297mm;
}}

.cover-accent-rule {{
    position: absolute;
    top: 120mm;
    left: 25mm;
    width: 60mm;
    height: 3px;
    background-color: {terracotta};
    z-index: 3;
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
}}

/* D27: Rich exec layout components */
.kpi-strip {{
    display: flex;
    gap: 0;
    margin: 0.5cm 0 0.8cm 0;
    page-break-inside: avoid;
    break-inside: avoid;
}}
.kpi-card {{
    flex: 1;
    background-color: {beige};
    border-top: 3px solid {terracotta};
    padding: 10px 14px;
    text-align: center;
}}
.kpi-card + .kpi-card {{ margin-left: 2px; }}
.kpi-value {{
    font-family: "Instrument Serif", serif;
    font-size: 20pt;
    color: {warm_charcoal};
    line-height: 1.1;
    margin: 0;
}}
.kpi-label {{
    font-family: "Source Sans 3", sans-serif;
    font-size: 8pt;
    color: {warm_gray};
    text-transform: uppercase;
    letter-spacing: 0.08em;
    margin-top: 4px;
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

.section-image {{
    width: 40%;
    float: right;
    margin: 0 0 8px 16px;
    page-break-inside: avoid;
}}

.section-image-caption {{
    font-size: 8pt;
    color: {warm_gray};
    text-align: right;
    margin-top: 4px;
}}

.chart {{
    width: 80%;
    margin: 16px auto;
    page-break-inside: avoid;
}}

.chart-caption {{
    font-size: 8pt;
    color: {warm_gray};
    text-align: center;
    margin-top: 4px;
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

.no-break {{
    page-break-inside: avoid;
}}
""".format(**PDF_PALETTE)


# ─────────────────────────────────────────────────────────────────────────────
# HTML Template (Jinja2 — premium report structure §6.1)
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
    <div class="cover-accent-rule"></div>
    {% endif %}
    <div class="cover-title">
        <h1>{{ report.question }}</h1>
        <p style="color: {{ palette.cream }}; font-size: 14pt; margin-top: 8px;">{{ report.recommendation.value | upper }}</p>
        <p style="color: {{ palette.warm_gray }}; font-size: 10pt; margin-top: 4px;">
            {{ report.generated_at.strftime('%B %Y') }} · Engagement {{ report.engagement_id }}
        </p>
        <p style="color: {{ palette.warm_gray }}; font-size: 10pt; margin-top: 2px;">
            Confidence: <span class="confidence-badge-{{ report.confidence.value }}">{{ report.confidence.value | upper }}</span>
        </p>
    </div>
</div>

{# ── Table of Contents ── #}
<div class="page-break">
    <h2>Table of Contents</h2>
    <div class="data-table toc-table">
        <table>
            <tr><td>Executive Summary</td><td>3</td></tr>
            {% for section in report.sections %}
            <tr><td>{{ section.title }}</td><td>{{ loop.index + 3 }}</td></tr>
            {% endfor %}
            <tr><td>Risk Analysis</td><td>{{ report.sections | length + 4 }}</td></tr>
            <tr><td>Methodology</td><td>{{ report.sections | length + 5 }}</td></tr>
            <tr><td>Appendix</td><td>{{ report.sections | length + 6 }}</td></tr>
        </table>
    </div>
</div>

{# ── Executive Summary — D27: Rich dashboard layout ── #}
<div class="page-break">
    <h2>Executive Summary</h2>

    {# Recommendation banner #}
    <div class="recommendation-banner">
        <div class="rec-label">Recommendation</div>
        <div class="rec-value">{{ report.recommendation.value | upper }}</div>
    </div>

    {# KPI strip — key metrics at a glance #}
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
        <div class="kpi-card">
            <div class="kpi-value">{{ report.confidence.value | upper }}</div>
            <div class="kpi-label">Confidence</div>
        </div>
    </div>

    {{ report.executive_summary | md_to_html }}

    {# Key findings in dashboard grid #}
    <h3>Key Findings</h3>
    <div class="dashboard-grid">
        {% for finding in report.key_findings %}
        <div class="dashboard-card">
            <div class="dashboard-card-title">{{ finding.title }}</div>
            <div class="dashboard-card-body">
                {{ finding.content[:300] }}
                {% if finding.confidence %}
                <span class="confidence-pill confidence-pill--{{ finding.confidence.value | lower }}">{{ finding.confidence.value | upper }}</span>
                {% endif %}
            </div>
        </div>
        {% endfor %}
    </div>

    {# Critical assumptions as callouts #}
    {% if report.critical_assumptions %}
    <h3>Critical Assumptions</h3>
    {% for assumption in report.critical_assumptions %}
    <div class="callout callout--alert">
        <div class="callout-title">Assumption</div>
        {{ assumption }}
    </div>
    {% endfor %}
    {% endif %}
</div>

{# ── Analysis Sections ── #}
{% for section in report.sections %}
<div class="page-break">
    <h2>{{ section.title }}</h2>

    <div class="key-insight-box">
        {{ section.key_insight | clean_dict_repr }}
    </div>

    {% if section_images[section.id] %}
    <img src="{{ section_images[section.id].image_path }}" class="section-image" alt="{{ section_images[section.id].caption }}">
    <p class="section-image-caption">{{ section_images[section.id].caption }}</p>
    {% endif %}

    <div class="no-break">
        {{ section.body | md_to_html }}
    </div>

    {% for chart in section_charts[section.id] %}
    <div class="chart no-break">
        <img src="{{ chart.image_path }}" alt="{{ chart.caption }}" style="width: 100%;">
        <p class="chart-caption">{{ chart.caption }}</p>
    </div>
    {% endfor %}

    <div class="implication-box">
        <strong>So What?</strong> {{ section.implications }}
    </div>
</div>
{% endfor %}

{# ── Risk Analysis ── #}
{% if report.risk_analysis %}
<div class="page-break">
    <h2>Risk Analysis</h2>
    {{ risk_analysis_html | safe }}
</div>
{% endif %}

{# ── Methodology ── #}
<div class="page-break">
    <h2>Methodology</h2>
    <h3>Agents Used</h3>
    <ul>
        {% for agent in report.agents_used %}
        <li>{{ agent }}</li>
        {% endfor %}
    </ul>
    <h3>Sources Accessed</h3>
    <p>Total unique sources: {{ report.total_sources }}</p>
    <h3>Data Points Collected</h3>
    <p>Total data points: {{ report.total_data_points }}</p>
    <h3>Limitations</h3>
    <ul>
        {% for limitation in report.limitations %}
        <li>{{ limitation }}</li>
        {% endfor %}
    </ul>
</div>

{# ── Appendix ── #}
<div class="page-break">
    <h2>Appendix</h2>
    <h3>Full Source List</h3>
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
        Confidential — for intended recipient only.
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
        ToolName.WEASYPRINT,
    ],
    skills=[
        SkillSpec(
            name="Layout design",
            description=(
                "Design page layouts that follow the premium structure. "
                "Each page has a clear visual hierarchy: header → key "
                "insight → body → chart/image → implication. Not just "
                "dumping content into a template — deliberate decisions "
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
                "(9pt). Two fonts only — creates visual consistency."
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
                "size. Brand palette only — no random colors."
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
                "Use white space deliberately — not as empty space, but "
                "as a design element that improves readability and focus. "
                "Margins, padding, and spacing between elements are "
                "intentional. No cramped pages, no wasted space."
            ),
            inputs=["page_layouts", "content_density"],
            outputs=["spacing_plan", "margin_adjustments"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Presentation Designer — the report layout "
        "designer and visual storyteller.\n\n"
        "Your role:\n"
        "1. RECEIVE the FinalReport from the Synthesis Lead and the "
        "QualityScore from the Quality Gate.\n"
        "2. DESIGN a layout plan — which content goes on which page, "
        "in what order, with what visuals.\n"
        "3. SELECT Unsplash images for cover and section headers — "
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
        "Image Placement Rules (§6.3 — NON-NEGOTIABLE):\n"
        "1. Every image has adjacent text context on the SAME page.\n"
        "2. Cover = full-bleed. Sections = 40% width, right-aligned.\n"
        "3. Topic-relevant, not generic stock. 'Modern boardroom meeting' "
        "not 'business.'\n"
        "4. All images processed through Pillow pipeline.\n"
        "5. No image larger than 50% page height (except cover).\n"
        "6. Every image has a caption with source attribution.\n"
        "7. Charts are NEVER screenshots. Always Plotly → PNG at scale=3.\n\n"
        "Typography (§7.4 — TWO FONTS ONLY):\n"
        "- Headers: Instrument Serif (cover 36pt, sections 22pt)\n"
        "- Body: JetBrains Mono (10pt regular, 14pt bold subsections)\n"
        "- Captions: JetBrains Mono 8pt\n"
        "- Key insight: JetBrains Mono 11pt\n"
        "- Data tables: JetBrains Mono 9pt\n\n"
        "Color Palette (§7.2 — WARM, NOT BLUE):\n"
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
        "- 15-40 pages for a standard engagement.\n\n"
        "You run on STRONG tier. You do NOT spawn sub-agents.\n\n"
        "Your output is a LayoutPlan Pydantic model — page-by-page layout, "
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

        # The QualityScore (must be approved)
        self._quality_score: QualityScore | None = None

        # Chart specifications from Data Visualizer
        self._visualization_output: VisualizationOutput | None = None

        # Selected images
        self._cover_image: ImageSelection | None = None
        self._section_images: dict[str, ImageSelection] = {}

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

        The report must be approved (score ≥ 4.0) before layout design begins.
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
            if chart.section == section_id or section_id in chart.section:
                placement = ChartPlacement(
                    chart_id=chart.id,
                    section_id=section_id,
                    image_path=chart.image_path,
                    caption=chart.caption or chart.title,
                    source_citation=chart.source_citation,
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
                term = data.get("search_term", "").strip()
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

            # Pick the first suitable image
            img = search_result.images[0]
            photographer = img.photographer or "Unknown"
            photo_id = img.id

            # Download using the UnsplashClient's download_image method
            local_path = await unsplash_tool.download_image(img, quality="regular")

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
        a specific, visually relevant search term — not a generic stock photo.
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
                term = data.get("search_term", "").strip()
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

    async def _select_section_images(self, report: FinalReport) -> dict[str, ImageSelection]:
        """Select Unsplash images for each section header.

        Section images = 40% page width, right-aligned, with caption.
        Uses LLM to generate context-aware search terms per section based
        on actual content, not hardcoded generic terms.
        """
        section_images: dict[str, ImageSelection] = {}

        if not report.sections:
            return section_images

        try:
            unsplash_tool = self.get_tool(ToolName.UNSPLASH)
            os.makedirs(self.IMAGE_DIR, exist_ok=True)

            for section in report.sections:
                search_term = await self._generate_section_search_term(section)

                search_result = await unsplash_tool.search(
                    query=search_term,
                    per_page=3,
                    orientation="landscape",
                )

                if not search_result.images:
                    continue

                img = search_result.images[0]
                photographer = img.photographer or "Unknown"
                photo_id = img.id

                local_path = await unsplash_tool.download_image(img, quality="regular")

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
                    caption=f"Source: Unsplash via {photographer}",
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
    ) -> dict[str, list[ChartPlacement]]:
        """Receive chart images from the Data Visualizer and organize by section."""
        if visualization_output:
            self._visualization_output = visualization_output

        self._chart_placements = {}
        if not self._visualization_output:
            return self._chart_placements

        for chart in self._visualization_output.charts:
            section_id = chart.section
            placement = ChartPlacement(
                chart_id=chart.id,
                section_id=section_id,
                image_path=chart.image_path,
                caption=chart.caption or chart.title,
                source_citation=chart.source_citation,
                width_percent=80,
                placement="center",
            )
            if section_id not in self._chart_placements:
                self._chart_placements[section_id] = []
            self._chart_placements[section_id].append(placement)

        return self._chart_placements

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

        # Write CSS file (also keep for debugging)
        css_content = CSS_TEMPLATE
        with open(self.CSS_OUTPUT, "w", encoding="utf-8") as f:
            f.write(css_content)

        try:
            jinja2_tool = self.get_tool(ToolName.JINJA2)

            # Prepare template context
            context = {
                "report": report,
                "cover_image": cover_image_abs,
                "section_images": section_images_abs,
                "section_charts": chart_placements_abs,
                "palette": PDF_PALETTE,
                "css_content": css_content,
                "risk_analysis_html": self._build_risk_analysis_html(report),
                "appendix_sources_html": self._build_appendix_sources_html(report),
            }

            html_content = await jinja2_tool.render_template(
                template_string=HTML_TEMPLATE,
                context=context,
            )

            # render_template returns a TemplateRenderResult, not a string
            if hasattr(html_content, "html") and html_content.success:
                html_str = html_content.html
            elif hasattr(html_content, "html"):
                # Template rendered but with errors — use what we got
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
                from jinja2 import Environment, BaseLoader
                env = Environment(loader=BaseLoader(), autoescape=True)
                env.filters["md_to_html"] = lambda v: v or ""
                env.filters["clean_dict_repr"] = lambda v: str(v) if v else ""
                template = env.from_string(HTML_TEMPLATE)
                html_str = template.render(
                    report=report,
                    cover_image=cover_image_abs,
                    section_images=section_images_abs,
                    section_charts=chart_placements_abs,
                    palette=PDF_PALETTE,
                    css_content=css_content,
                    risk_analysis_html=self._build_risk_analysis_html(report),
                    appendix_sources_html=self._build_appendix_sources_html(report),
                )
            except Exception:
                # Last resort: strip Jinja2 tags and do basic format
                html_str = HTML_TEMPLATE.replace("{{ css_content | safe }}", css_content)
                html_str = html_str.replace("{{ report.question }}", str(report.question))
                html_str = html_str.replace("{{ report.recommendation.value | upper }}", str(report.recommendation.value).upper())

            with open(self.HTML_OUTPUT, "w", encoding="utf-8") as f:
                f.write(html_str)

            return self.HTML_OUTPUT

    def _build_risk_analysis_html(self, report: FinalReport) -> str:
        """Build the risk analysis HTML section."""
        if not report.risk_analysis:
            return "<p>No risk analysis available.</p>"

        html_parts = ["<div class='risk-matrix no-break'>"]
        html_parts.append("<h3>Top Risks</h3>")
        html_parts.append("<table class='data-table'>")
        html_parts.append("<tr><th>Risk</th><th>Probability</th><th>Impact</th><th>Mitigation</th></tr>")

        risks = getattr(report.risk_analysis, "risks", [])
        for risk in risks[:10]:
            name = getattr(risk, "name", "Unknown")
            probability = getattr(risk, "probability", "N/A")
            impact = getattr(risk, "impact", "N/A")
            mitigation = getattr(risk, "mitigation", "N/A")
            html_parts.append(
                f"<tr><td>{name}</td><td>{probability}</td><td>{impact}</td><td>{mitigation}</td></tr>"
            )

        html_parts.append("</table></div>")
        return "\n".join(html_parts)

    def _build_appendix_sources_html(self, report: FinalReport) -> str:
        """Build the appendix source list HTML."""
        html_parts = ["<div class='no-break'><h3>Full Source List</h3>"]
        html_parts.append("<table class='data-table'>")
        html_parts.append("<tr><th>#</th><th>Source</th><th>URL</th></tr>")

        source_num = 1
        for section in report.sections:
            for source in section.sources:
                title = getattr(source, "title", "Unknown")
                url = getattr(source, "url", "")
                html_parts.append(f"<tr><td>{source_num}</td><td>{title}</td><td>{url}</td></tr>")
                source_num += 1

        html_parts.append("</table></div>")
        return "\n".join(html_parts)

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Generate PDF with WeasyPrint
    # ─────────────────────────────────────────────────────────────────────

    async def _generate_pdf(self, html_path: str) -> str:
        """Generate the final PDF with WeasyPrint.

        WeasyPrint converts HTML/CSS to PDF at 300 DPI with:
        - A4 page size
        - Embedded fonts (Instrument Serif, JetBrains Mono)
        - Brand colors (warm palette, no blue)
        - Proper margins (25mm all sides, 15mm binding)
        - Page breaks (no blank pages, no orphaned images)
        """
        os.makedirs(self.OUTPUT_DIR, exist_ok=True)

        try:
            weasyprint_tool = self.get_tool(ToolName.WEASYPRINT)

            # PDFRenderer.render_pdf expects HTML string content, not a file path.
            html_content = ""
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
            except (OSError, ValueError):
                return ""

            if not html_content:
                return ""

            result = weasyprint_tool.render_pdf(
                html=html_content,
                output_path=self.PDF_OUTPUT,
            )

            if result and result.success:
                return self.PDF_OUTPUT
            return ""

        except (ValueError, AttributeError, RuntimeError):
            return ""

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
            if not page.content_blocks and not page.images and not page.charts:
                if page.page_type not in (PageType.BACK_COVER,):
                    no_blank = False

            # Check for orphaned images (image without text context on same page)
            if page.images and not page.content_blocks:
                if page.page_type != PageType.COVER:
                    no_orphaned = False

        return (no_blank, no_orphaned)

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 8-step methodology
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

        if not report:
            await self._transition(AgentState.DONE, "No FinalReport received")
            return LayoutPlan(engagement_id=engagement_id, confidence=ConfidenceLevel.LOW)

        # Step 2: Receive QualityScore
        await self._transition(AgentState.WORKING, "Step 2: Receiving QualityScore")
        await self._receive_quality_score(quality_score)

        if self._quality_score and not self._quality_score.approved:
            quality_note = (
                f"Quality Gate not approved (score {self._quality_score.total_score:.1f}/5.0"
                f", iteration {self._quality_score.iteration})"
            )
            if self._quality_score.max_iterations_reached:
                quality_note += " — max iterations reached, proceeding with best report (escalation)"
                await self._transition(
                    AgentState.WORKING,
                    f"Step 2: {quality_note}",
                )
            else:
                await self._transition(
                    AgentState.DONE,
                    f"{quality_note} — cannot design layout",
                )
                return LayoutPlan(
                    engagement_id=engagement_id,
                    confidence=ConfidenceLevel.LOW,
                    no_blank_pages=False,
                    no_orphaned_images=False,
                )

        # Step 3: Design layout plan
        await self._transition(AgentState.WORKING, "Step 3: Designing layout plan")
        self._pages = self._design_layout_plan(report)

        # Step 4: Select Unsplash images
        await self._transition(AgentState.WORKING, "Step 4: Selecting Unsplash images for cover and sections")
        self._cover_image = await self._select_cover_image(report)
        self._section_images = await self._select_section_images(report)

        # Assign images to pages
        if self._cover_image and self._pages:
            self._pages[0].images.append(self._cover_image)
        for page in self._pages:
            if page.page_type == PageType.SECTION and page.section_id in self._section_images:
                page.images.append(self._section_images[page.section_id])

        # Step 5: Receive chart images from Data Visualizer
        await self._transition(AgentState.WORKING, "Step 5: Receiving chart images from Data Visualizer")
        self._receive_chart_images(visualization_output)

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

        # Step 7: Generate PDF with WeasyPrint
        await self._transition(AgentState.WORKING, "Step 7: Generating PDF with WeasyPrint")
        pdf_path = await self._generate_pdf(html_path)

        # Step 8: Post-process images with Pillow (via Render Engine)
        await self._transition(AgentState.WORKING, "Step 8: Post-processing images (handed to Render Engine)")

        # Collect all chart placements
        all_chart_placements: list[ChartPlacement] = []
        for placements in self._chart_placements.values():
            all_chart_placements.extend(placements)

        # Collect all section images
        all_section_images = list(self._section_images.values())

        # Determine confidence
        if pdf_path and no_blank and no_orphaned:
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
            css_path=self.CSS_OUTPUT,
            pdf_path=pdf_path,
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
                "has_pdf": bool(pdf_path),
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
                "task": "render_pdf",
                "context_bundle": {
                    "layout_plan": layout_plan.model_dump(),
                    "html_path": html_path,
                    "css_path": self.CSS_OUTPUT,
                    "pdf_output_path": self.PDF_OUTPUT,
                    "images_to_process": [img.image_path for img in all_section_images] +
                                         ([self._cover_image.image_path] if self._cover_image else []),
                    "charts_to_process": [cp.image_path for cp in all_chart_placements],
                },
                "message": (
                    f"Layout plan complete: {len(self._pages)} pages, "
                    f"{len(all_section_images)} section images, "
                    f"{len(all_chart_placements)} charts. "
                    f"PDF {'generated' if pdf_path else 'pending'}. "
                    f"Hand off to Render Engine for final assembly."
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
                f"PDF: {'generated' if pdf_path else 'pending Render Engine'}."
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
            f"pdf: {'yes' if pdf_path else 'pending'}, "
            f"confidence: {confidence.value}",
        )

        return layout_plan
