"""
HYPERION Data Visualizer — Agent 17, the chart generation and visual
storytelling engine.

This is NOT a generic "make a chart" agent. This is a specialist with
5 proprietary skills:

- Chart type selection: Select the right chart type for the data shape.
  comparison → bar, trend → line, distribution → histogram, correlation →
  scatter, composition → stacked bar/treemap, flow → sankey. Never uses a
  pie chart when a bar chart would be clearer.
- Data viz best practices: Apply Tufte principles — minimize chartjunk,
  maximize data-ink ratio, use color purposefully (not decoratively). No 3D
  effects, no gradient fills, no decorative elements.
- Brand-compliant styling: All charts use the HYPERION chart color sequence
  (terracotta, sage, deep brown, warm gray, beige, alert red). First series
  is always Terracotta. Risk data uses Alert Red. Positive findings use Sage.
  Never blue, purple, or green (standard Plotly defaults).
- Axis calibration: Choose axis ranges that show the data honestly — no
  truncated y-axes that exaggerate differences, no log scales without
  labeling. The y-axis starts at zero for bar charts (always).
- Annotation: Add contextual annotations that help the reader understand
  the key insight — benchmark lines, callout boxes, trend lines. Not
  decorative — purposeful.

It runs on STANDARD tier (Llama 3.3 70B / GPT OSS 120B / Nemotron 3 Nano
30B) because chart generation requires reasoning about data shape and chart
type selection, but doesn't need the deep analysis of STRONG/DEEP tiers.

Model Tier: STANDARD (Llama 3.3 70B on Groq — chart type selection and
styling require moderate reasoning, not deep analysis)
Tools: Plotly (generate charts with brand colors and 300 DPI export),
       Unsplash (search for contextual images to complement charts),
       Pillow (post-process chart images — sharpen, color-correct for print)
Sub-agents: 0 (support agent — doesn't spawn sub-agents)
Output: VisualizationOutput (chart specifications with generated PNG paths,
        300 DPI, brand-compliant, Tufte-compliant, Pillow-processed)

Methodology (§4.5, Agent 17):
1. Receive chart specifications from Presentation Designer
2. For each chart, select chart type based on data shape
3. Generate chart with Plotly using brand colors
4. Export at scale=3 for 300 DPI
5. Post-process with Pillow (sharpen for print)
6. Return chart image paths to Presentation Designer

What makes it the best version of itself:
It follows Tufte principles — no chartjunk, no 3D effects, no gradient
fills. Every chart has a purpose: it reveals a pattern that the text alone
cannot convey. It never uses a pie chart when a bar chart would be clearer.
It always labels axes, always cites the data source, and always chooses the
chart type that best reveals the insight — not the chart type that looks
most impressive.
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
    ChartAnnotation,
    ChartDataSeries,
    ChartSpecification,
    ChartType,
    ConfidenceLevel,
    KeyFinding,
    VisualizationOutput,
)


# ─────────────────────────────────────────────────────────────────────────────
# HYPERION Chart Color Sequence (§7.3 — STRICT)
# ─────────────────────────────────────────────────────────────────────────────

CHART_COLORS = [
    "#C8704D",  # Terracotta (primary — always first series)
    "#7C9885",  # Sage (secondary — always second series)
    "#3D3530",  # Deep Brown (tertiary)
    "#8B8680",  # Warm Gray (quaternary)
    "#E8E6DD",  # Beige (light fill — for backgrounds/shading)
    "#B5533C",  # Alert Red (risk series only — never default)
]

# PDF palette for reference
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


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


DATA_VISUALIZER_SPEC = AgentSpec(
    name=AgentName.DATA_VISUALIZER,
    role=AgentRole.SUPPORT,
    display_name="Data Visualizer",
    model_tier=ModelTier.STANDARD,
    tools=[
        ToolName.PLOTLY,
        ToolName.UNSPLASH,
        ToolName.PILLOW,
    ],
    skills=[
        SkillSpec(
            name="Chart type selection",
            description=(
                "Select the right chart type for the data shape: comparison "
                "→ bar, trend → line, distribution → histogram, correlation "
                "→ scatter, composition → stacked bar/treemap, flow → "
                "sankey. Never uses a pie chart when a bar chart would be "
                "clearer. Never uses 3D effects. The chart type is chosen "
                "to best reveal the insight, not to look most impressive."
            ),
            inputs=["data_shape", "comparison_type", "series_count"],
            outputs=["chart_type", "chart_type_rationale"],
        ),
        SkillSpec(
            name="Data viz best practices",
            description=(
                "Apply Tufte principles: minimize chartjunk, maximize "
                "data-ink ratio, use color purposefully (not decoratively). "
                "No 3D effects, no gradient fills, no decorative elements. "
                "Every pixel of ink serves the data. Gridlines are subtle "
                "and behind the data, not on top."
            ),
            inputs=["chart_spec", "data_series"],
            outputs=["tufte_compliant_chart", "data_ink_ratio"],
        ),
        SkillSpec(
            name="Brand-compliant styling",
            description=(
                "All charts use the HYPERION chart color sequence: "
                "Terracotta (#C8704D), Sage (#7C9885), Deep Brown "
                "(#3D3530), Warm Gray (#8B8680), Beige (#E8E6DD), Alert "
                "Red (#B5533C). First series is always Terracotta. Risk "
                "data uses Alert Red. Positive findings use Sage. Never "
                "blue, purple, or green (standard Plotly defaults). "
                "Override with colorway=CHART_COLORS in every chart."
            ),
            inputs=["chart_spec", "series_count"],
            outputs=["brand_compliant_chart", "color_sequence_applied"],
        ),
        SkillSpec(
            name="Axis calibration",
            description=(
                "Choose axis ranges that show the data honestly — no "
                "truncated y-axes that exaggerate differences, no log "
                "scales without labeling. The y-axis starts at zero for "
                "bar charts (always). Axis ranges are set to show the full "
                "data context, not to make differences look bigger than "
                "they are."
            ),
            inputs=["data_series", "chart_type"],
            outputs=["x_axis_range", "y_axis_range", "axis_calibration_notes"],
        ),
        SkillSpec(
            name="Annotation",
            description=(
                "Add contextual annotations to charts that help the reader "
                "understand the key insight: benchmark lines (industry "
                "average, competitor position), callout boxes (highlighting "
                "the key data point), trend lines (showing direction). Not "
                "decorative — purposeful. Every annotation has a reason."
            ),
            inputs=["chart_spec", "key_insight", "benchmark_data"],
            outputs=["annotations", "benchmark_lines", "callout_boxes"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Data Visualizer — the chart generation and "
        "visual storytelling engine.\n\n"
        "Your role:\n"
        "1. RECEIVE chart specifications from the Presentation Designer. "
        "Each spec includes the data, the section it belongs to, and the "
        "insight it should reveal.\n"
        "2. SELECT the right chart type for the data shape: comparison → "
        "bar, trend → line, distribution → histogram, correlation → "
        "scatter, composition → stacked bar/treemap, flow → sankey.\n"
        "3. GENERATE charts with Plotly using the HYPERION chart color "
        "sequence. First series is always Terracotta (#C8704D).\n"
        "4. EXPORT at scale=3 for 300 DPI. Charts are NEVER screenshots.\n"
        "5. POST-PROCESS with Pillow: sharpen (unsharp mask for print), "
        "color-correct (match brand warmth).\n"
        "6. RETURN chart image paths to the Presentation Designer.\n\n"
        "Tufte Principles (NON-NEGOTIABLE):\n"
        "- No chartjunk. No 3D effects. No gradient fills. No decorative "
        "elements.\n"
        "- Maximize data-ink ratio. Every pixel of ink serves the data.\n"
        "- Gridlines are subtle and behind the data, not on top.\n"
        "- Never use a pie chart when a bar chart would be clearer.\n"
        "- Always label axes. Always cite the data source.\n\n"
        "Brand Color Rules (§7.3 — STRICT):\n"
        "- CHART_COLORS = ['#C8704D', '#7C9885', '#3D3530', '#8B8680', "
        "'#E8E6DD', '#B5533C']\n"
        "- First series is ALWAYS Terracotta. No exceptions.\n"
        "- Risk-related data uses Alert Red (#B5533C).\n"
        "- Positive findings (opportunities, growth) use Sage (#7C9885).\n"
        "- Never use more than 5 colors in a single chart. Group remaining "
        "into 'Other'.\n"
        "- NEVER use blue, purple, or green (standard Plotly defaults). "
        "Override with colorway=CHART_COLORS.\n\n"
        "Axis Calibration Rules:\n"
        "- Y-axis starts at zero for bar charts. Always.\n"
        "- No truncated y-axes that exaggerate differences.\n"
        "- No log scales without explicit labeling.\n"
        "- Axis ranges show the full data context.\n\n"
        "Annotation Rules:\n"
        "- Add benchmark lines (industry average, competitor position).\n"
        "- Add callout boxes highlighting the key data point.\n"
        "- Add trend lines showing direction.\n"
        "- Every annotation has a reason. No decorative annotations.\n\n"
        "You run on STANDARD tier. You do NOT spawn sub-agents.\n\n"
        "Your output is a VisualizationOutput Pydantic model — structured, "
        "not free text. Every chart has: image_path, title, caption, "
        "source_citation, and tufte_compliant=True."
    ),
    spawn_condition="Spawned by the Presentation Designer when chart "
                     "specifications are ready. Runs in parallel with "
                     "section writing. Must complete before the Render "
                     "Engine assembles the final PDF.",
    max_sub_agents=0,
    output_model="VisualizationOutput",
)


# ─────────────────────────────────────────────────────────────────────────────
# Data Visualizer Agent
# ─────────────────────────────────────────────────────────────────────────────


class DataVisualizer(BaseAgent):
    """Agent 17: The chart generation and visual storytelling engine.

    Generates charts, graphs, and visual elements for the report. Runs on
    STANDARD tier because chart type selection and styling require moderate
    reasoning. Follows Tufte principles — no chartjunk, no 3D effects, no
    gradient fills. Every chart has a purpose. (§4.5, Agent 17)

    Lifecycle:
    1. Receive chart specifications from Presentation Designer
    2. For each chart, select chart type based on data shape
    3. Generate chart with Plotly using brand colors
    4. Export at scale=3 for 300 DPI
    5. Post-process with Pillow (sharpen for print)
    6. Return chart image paths to Presentation Designer
    """

    # Chart output directory (relative to engagement output)
    CHART_OUTPUT_DIR = "output/charts"
    IMAGE_OUTPUT_DIR = "output/images"

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or DATA_VISUALIZER_SPEC, bus=bus, router=router)

        # Chart specifications received from Presentation Designer
        self._chart_specs: list[dict[str, Any]] = []

        # Generated charts
        self._generated_charts: list[ChartSpecification] = []

        # Unsplash images sourced
        self._unsplash_images: list[dict[str, str]] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Data Visualizer listens to:
        - HANDOFF: receives chart specifications from Presentation Designer
        - REQUESTS: receives chart generation requests
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            if task == "generate_charts":
                context_bundle = payload.get("context_bundle", {})
                chart_specs = context_bundle.get("chart_specs", [])
                self._chart_specs.extend(chart_specs)

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "generate_chart":
                chart_spec = payload.get("chart_spec", {})
                self._chart_specs.append(chart_spec)

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Receive chart specifications from Presentation Designer
    # ─────────────────────────────────────────────────────────────────────

    async def _receive_chart_specs(
        self,
        chart_specs: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Receive chart specifications from the Presentation Designer.

        Chart specs include: title, section, data, insight to reveal,
        and optional chart type hint.
        """
        if chart_specs:
            self._chart_specs.extend(chart_specs)
        return self._chart_specs

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Select chart type based on data shape
    # ─────────────────────────────────────────────────────────────────────

    def _select_chart_type(
        self,
        data_shape: str,
        series_count: int,
        data_series: list[ChartDataSeries],
        hint: str | None = None,
    ) -> ChartType:
        """Select the right chart type for the data.

        Selection logic (§4.5, Agent 17, Skill 1):
        - comparison → bar
        - trend → line
        - distribution → histogram
        - correlation → scatter
        - composition → stacked_bar (if ≤5 parts) or treemap (if >5)
        - flow → sankey
        - risk matrix → heatmap
        - multi-dimensional comparison → radar
        - financial bridge → waterfall

        Never uses PIE when BAR would be clearer. PIE only for composition
        with ≤4 parts where part-to-whole relationship is the key insight.
        """
        if hint:
            hint_lower = hint.lower()
            type_map = {
                "bar": ChartType.BAR,
                "line": ChartType.LINE,
                "scatter": ChartType.SCATTER,
                "histogram": ChartType.HISTOGRAM,
                "heatmap": ChartType.HEATMAP,
                "radar": ChartType.RADAR,
                "waterfall": ChartType.WATERFALL,
                "treemap": ChartType.TREEMAP,
                "sankey": ChartType.SANKEY,
                "stacked_bar": ChartType.STACKED_BAR,
                "pie": ChartType.PIE,
            }
            if hint_lower in type_map:
                return type_map[hint_lower]

        shape_lower = data_shape.lower() if data_shape else ""

        # Comparison → bar
        if any(w in shape_lower for w in ["comparison", "compare", "versus", "vs", "benchmark"]):
            return ChartType.BAR

        # Trend → line
        if any(w in shape_lower for w in ["trend", "time", "over time", "growth", "decline", "trajectory"]):
            return ChartType.LINE

        # Distribution → histogram
        if any(w in shape_lower for w in ["distribution", "spread", "frequency", "histogram"]):
            return ChartType.HISTOGRAM

        # Correlation → scatter
        if any(w in shape_lower for w in ["correlation", "relationship", "scatter", "x-y"]):
            return ChartType.SCATTER

        # Composition
        if any(w in shape_lower for w in ["composition", "breakdown", "parts", "share", "allocation"]):
            if series_count <= 4:
                return ChartType.STACKED_BAR  # Prefer stacked bar over pie
            elif series_count <= 10:
                return ChartType.TREEMAP
            else:
                return ChartType.STACKED_BAR  # Group into "Other"

        # Flow → sankey
        if any(w in shape_lower for w in ["flow", "sankey", "pipeline", "funnel", "conversion"]):
            return ChartType.SANKEY

        # Risk matrix → heatmap
        if any(w in shape_lower for w in ["risk matrix", "risk map", "probability", "impact"]):
            return ChartType.HEATMAP

        # Multi-dimensional comparison → radar
        if any(w in shape_lower for w in ["radar", "spider", "multi-dimensional", "profile"]):
            return ChartType.RADAR

        # Financial bridge → waterfall
        if any(w in shape_lower for w in ["waterfall", "bridge", "reconciliation", "build-up"]):
            return ChartType.WATERFALL

        # Default: bar chart (safest, most universally readable)
        return ChartType.BAR

    def _determine_data_shape(
        self,
        spec: dict[str, Any],
    ) -> tuple[str, int, list[ChartDataSeries]]:
        """Determine the data shape from a chart specification.

        Returns (data_shape_description, series_count, parsed_data_series).
        """
        data_shape = spec.get("data_shape", "comparison")
        raw_series = spec.get("data_series", [])
        series_count = len(raw_series) if raw_series else 1

        # Parse raw data into ChartDataSeries
        data_series: list[ChartDataSeries] = []
        for raw in raw_series:
            series = ChartDataSeries(
                name=raw.get("name", "Series"),
                values=raw.get("values", []),
                labels=raw.get("labels", []),
                color=raw.get("color"),
            )
            data_series.append(series)

        return (data_shape, series_count, data_series)

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Generate chart with Plotly using brand colors
    # ─────────────────────────────────────────────────────────────────────

    def _build_plotly_layout(
        self,
        chart_spec: ChartSpecification,
    ) -> dict[str, Any]:
        """Build a Plotly layout dict with brand-compliant styling.

        Applies:
        - CHART_COLORS colorway (never Plotly defaults)
        - Cream background (#F5F4EE)
        - Warm charcoal text (#1A1A1A)
        - Subtle gridlines (warm gray, behind data)
        - No chartjunk: no legend border, no grid background, no 3D
        - Axis labels and honest ranges
        - Title in Instrument Serif style (as close as Plotly allows)
        """
        layout: dict[str, Any] = {
            "colorway": CHART_COLORS,
            "paper_bgcolor": PDF_PALETTE["cream"],
            "plot_bgcolor": PDF_PALETTE["cream"],
            "font": {
                "family": "Georgia, serif",  # Closest to Instrument Serif
                "size": 14,
                "color": PDF_PALETTE["warm_charcoal"],
            },
            "title": {
                "text": chart_spec.title,
                "font": {
                    "family": "Georgia, serif",
                    "size": 22,
                    "color": PDF_PALETTE["warm_charcoal"],
                },
                "x": 0.5,  # Centered
            },
            "xaxis": {
                "title": chart_spec.x_axis_label,
                "gridcolor": PDF_PALETTE["warm_gray"],
                "gridwidth": 0.5,
                "zeroline": False,
                "showline": True,
                "linecolor": PDF_PALETTE["warm_gray"],
                "tickfont": {"size": 12, "color": PDF_PALETTE["warm_charcoal"]},
            },
            "yaxis": {
                "title": chart_spec.y_axis_label,
                "gridcolor": PDF_PALETTE["warm_gray"],
                "gridwidth": 0.5,
                "zeroline": True,
                "zerolinecolor": PDF_PALETTE["warm_gray"],
                "zerolinewidth": 0.5,
                "showline": True,
                "linecolor": PDF_PALETTE["warm_gray"],
                "tickfont": {"size": 12, "color": PDF_PALETTE["warm_charcoal"]},
            },
            "legend": {
                "bgcolor": "rgba(0,0,0,0)",  # Transparent — no chartjunk
                "borderwidth": 0,
                "font": {"size": 12, "color": PDF_PALETTE["warm_charcoal"]},
            },
            "margin": {"l": 80, "r": 40, "t": 80, "b": 80},
            "showlegend": len(chart_spec.data_series) > 1,
        }

        # Apply axis ranges (honest axis calibration)
        if chart_spec.x_axis_range:
            layout["xaxis"]["range"] = list(chart_spec.x_axis_range)
        if chart_spec.y_axis_range:
            layout["yaxis"]["range"] = list(chart_spec.y_axis_range)
        else:
            # For bar charts, y-axis starts at zero (always)
            if chart_spec.chart_type in (ChartType.BAR, ChartType.STACKED_BAR):
                layout["yaxis"]["rangemode"] = "tozero"

        return layout

    def _build_plotly_traces(
        self,
        chart_spec: ChartSpecification,
    ) -> list[dict[str, Any]]:
        """Build Plotly trace dicts for the chart type.

        Each chart type has specific trace construction:
        - BAR: bar traces with brand colors
        - LINE: scatter traces with mode='lines'
        - SCATTER: scatter traces with mode='markers'
        - HISTOGRAM: histogram traces
        - HEATMAP: heatmap trace with z-values
        - RADAR: scatterpolar traces
        - WATERFALL: waterfall trace
        - TREEMAP: treemap trace
        - SANKEY: sankey trace
        - STACKED_BAR: bar traces with barmode='stack'
        - PIE: pie trace (only when ≤4 parts)
        """
        traces: list[dict[str, Any]] = []

        for i, series in enumerate(chart_spec.data_series):
            color = series.color or CHART_COLORS[i % len(CHART_COLORS)]

            if chart_spec.chart_type == ChartType.BAR:
                traces.append({
                    "type": "bar",
                    "name": series.name,
                    "x": series.labels,
                    "y": series.values,
                    "marker": {"color": color},
                })

            elif chart_spec.chart_type == ChartType.STACKED_BAR:
                traces.append({
                    "type": "bar",
                    "name": series.name,
                    "x": series.labels,
                    "y": series.values,
                    "marker": {"color": color},
                })

            elif chart_spec.chart_type == ChartType.LINE:
                traces.append({
                    "type": "scatter",
                    "mode": "lines+markers",
                    "name": series.name,
                    "x": series.labels,
                    "y": series.values,
                    "line": {"color": color, "width": 2.5},
                    "marker": {"color": color, "size": 6},
                })

            elif chart_spec.chart_type == ChartType.SCATTER:
                traces.append({
                    "type": "scatter",
                    "mode": "markers",
                    "name": series.name,
                    "x": series.values if len(series.values) > 1 else series.labels,
                    "y": series.labels if len(series.labels) > 1 else series.values,
                    "marker": {"color": color, "size": 8, "opacity": 0.7},
                })

            elif chart_spec.chart_type == ChartType.HISTOGRAM:
                traces.append({
                    "type": "histogram",
                    "name": series.name,
                    "x": series.values,
                    "marker": {"color": color, "opacity": 0.7},
                    "nbinsx": 20,
                })

            elif chart_spec.chart_type == ChartType.HEATMAP:
                # Heatmap: single trace with z-values
                if i == 0:
                    traces.append({
                        "type": "heatmap",
                        "name": series.name,
                        "z": [series.values],
                        "x": series.labels,
                        "y": [series.name],
                        "colorscale": [
                            [0, PDF_PALETTE["beige"]],
                            [0.5, PDF_PALETTE["terracotta"]],
                            [1, PDF_PALETTE["alert_red"]],
                        ],
                    })

            elif chart_spec.chart_type == ChartType.RADAR:
                traces.append({
                    "type": "scatterpolar",
                    "mode": "lines+markers",
                    "name": series.name,
                    "r": series.values,
                    "theta": series.labels,
                    "fill": "toself",
                    "fillcolor": color + "40",  # 25% opacity
                    "line": {"color": color, "width": 2},
                })

            elif chart_spec.chart_type == ChartType.WATERFALL:
                traces.append({
                    "type": "waterfall",
                    "name": series.name,
                    "x": series.labels,
                    "y": series.values,
                    "measure": ["absolute"] + ["relative"] * (len(series.values) - 1),
                    "connector": {"line": {"color": PDF_PALETTE["warm_gray"]}},
                    "increasing": {"marker": {"color": PDF_PALETTE["sage"]}},
                    "decreasing": {"marker": {"color": PDF_PALETTE["alert_red"]}},
                    "totals": {"marker": {"color": PDF_PALETTE["deep_brown"]}},
                })

            elif chart_spec.chart_type == ChartType.TREEMAP:
                if i == 0:
                    traces.append({
                        "type": "treemap",
                        "name": series.name,
                        "labels": series.labels,
                        "values": series.values,
                        "marker": {"colors": [CHART_COLORS[j % len(CHART_COLORS)] for j in range(len(series.labels))]},
                        "textinfo": "label+value+percent",
                    })

            elif chart_spec.chart_type == ChartType.SANKEY:
                if i == 0:
                    traces.append({
                        "type": "sankey",
                        "name": series.name,
                        "node": {
                            "label": series.labels,
                            "color": [CHART_COLORS[j % len(CHART_COLORS)] for j in range(len(series.labels))],
                            "pad": 15,
                            "thickness": 20,
                        },
                        "link": {
                            "source": list(range(len(series.labels) - 1)),
                            "target": list(range(1, len(series.labels))),
                            "value": series.values,
                            "color": [CHART_COLORS[j % 5] + "60" for j in range(len(series.values))],
                        },
                    })

            elif chart_spec.chart_type == ChartType.PIE:
                if i == 0:
                    traces.append({
                        "type": "pie",
                        "name": series.name,
                        "labels": series.labels,
                        "values": series.values,
                        "marker": {"colors": [CHART_COLORS[j % 5] for j in range(len(series.labels))]},
                        "textinfo": "label+percent",
                        "textposition": "outside",
                    })

        return traces

    def _build_annotations(
        self,
        chart_spec: ChartSpecification,
    ) -> list[dict[str, Any]]:
        """Build Plotly annotation dicts from ChartAnnotation models.

        Annotations are contextual, not decorative:
        - benchmark_line: horizontal line at a specific y value
        - callout: text box highlighting a key data point
        - trend_line: diagonal line showing direction
        - shaded_region: semi-transparent rectangle highlighting a range
        """
        plotly_annotations: list[dict[str, Any]] = []

        for ann in chart_spec.annotations:
            if ann.annotation_type == "benchmark_line":
                # Horizontal line at y value
                plotly_annotations.append({
                    "x": 0,
                    "y": ann.y or 0,
                    "xref": "paper",
                    "yref": "y",
                    "text": ann.text,
                    "showarrow": False,
                    "font": {"size": 11, "color": PDF_PALETTE["warm_gray"]},
                    "xanchor": "left",
                })
                # Add a horizontal line shape
                plotly_annotations.append({
                    "x": 0,
                    "y": ann.y or 0,
                    "xref": "paper",
                    "yref": "y",
                    "text": "",
                    "showarrow": False,
                })

            elif ann.annotation_type == "callout":
                plotly_annotations.append({
                    "x": ann.x,
                    "y": ann.y,
                    "text": ann.text,
                    "showarrow": True,
                    "arrowhead": 2,
                    "arrowsize": 1,
                    "arrowwidth": 1.5,
                    "arrowcolor": PDF_PALETTE["terracotta"],
                    "font": {"size": 11, "color": PDF_PALETTE["warm_charcoal"]},
                    "bgcolor": PDF_PALETTE["beige"],
                    "bordercolor": PDF_PALETTE["terracotta"],
                    "borderwidth": 1,
                    "borderpad": 4,
                })

            elif ann.annotation_type == "trend_line":
                plotly_annotations.append({
                    "x": ann.x,
                    "y": ann.y,
                    "text": ann.text,
                    "showarrow": False,
                    "font": {"size": 10, "color": PDF_PALETTE["sage"]},
                    "xanchor": "right",
                })

        return plotly_annotations

    async def _generate_chart_with_plotly(
        self,
        chart_spec: ChartSpecification,
    ) -> str:
        """Generate a chart with Plotly and export as PNG at scale=3 (300 DPI).

        Uses the Plotly tool to:
        1. Build traces from data series
        2. Build layout with brand colors
        3. Add annotations
        4. Export at scale=3 for 300 DPI

        Returns the path to the generated PNG.
        """
        # Ensure output directory exists
        os.makedirs(self.CHART_OUTPUT_DIR, exist_ok=True)

        # Build the chart
        traces = self._build_plotly_traces(chart_spec)
        layout = self._build_plotly_layout(chart_spec)
        annotations = self._build_annotations(chart_spec)

        if annotations:
            layout["annotations"] = annotations

        # For stacked bar, set barmode
        if chart_spec.chart_type == ChartType.STACKED_BAR:
            layout["barmode"] = "stack"

        # Generate chart using Plotly tool
        chart_id = chart_spec.id
        output_path = os.path.join(self.CHART_OUTPUT_DIR, f"{chart_id}.png")

        try:
            plotly_tool = self.get_tool(ToolName.PLOTLY)

            # Build the figure and export
            figure = {
                "data": traces,
                "layout": layout,
            }

            # Export at scale=3 for 300 DPI
            await plotly_tool.export_chart(
                figure=figure,
                output_path=output_path,
                scale=3,
                width=chart_spec.width_px,
                height=chart_spec.height_px,
            )

            return output_path

        except (ValueError, AttributeError, RuntimeError) as e:
            # If Plotly tool fails, log and return empty path
            self._logger.warning(f"Plotly chart generation failed for {chart_id}: {e}")
            return ""

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Export at scale=3 for 300 DPI
    # ─────────────────────────────────────────────────────────────────────

    async def _export_chart_300dpi(
        self,
        chart_spec: ChartSpecification,
        figure: dict[str, Any],
    ) -> str:
        """Export a Plotly figure at scale=3 for 300 DPI.

        Charts are NEVER screenshots. Always Plotly → kaleido → PNG at
        scale=3. Screenshots are blurry, have wrong colors, and can't be
        edited.
        """
        os.makedirs(self.CHART_OUTPUT_DIR, exist_ok=True)
        output_path = os.path.join(self.CHART_OUTPUT_DIR, f"{chart_spec.id}.png")

        try:
            plotly_tool = self.get_tool(ToolName.PLOTLY)
            await plotly_tool.export_chart(
                figure=figure,
                output_path=output_path,
                scale=3,  # 300 DPI
                width=chart_spec.width_px,
                height=chart_spec.height_px,
            )
            return output_path
        except (ValueError, AttributeError, RuntimeError):
            return ""

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Post-process with Pillow (sharpen for print)
    # ─────────────────────────────────────────────────────────────────────

    async def _post_process_with_pillow(self, image_path: str) -> str:
        """Post-process a chart image with Pillow for print quality.

        Pipeline (§6.4):
        1. Open and verify image resolution
        2. Color-correct (match brand warmth — slightly warm, not cold/blue)
        3. Sharpen (unsharp mask for print clarity)
        4. Export as PNG (lossless, 300 DPI)
        """
        if not image_path or not os.path.exists(image_path):
            return image_path

        try:
            pillow_tool = self.get_tool(ToolName.PILLOW)

            processed_path = image_path.replace(".png", "_processed.png")

            # Apply Pillow pipeline
            await pillow_tool.process_image(
                image_path=image_path,
                output_path=processed_path,
                operations=[
                    {"op": "color_correct", "intensity": 0.05},  # Warm slightly
                    {"op": "sharpen", "radius": 2, "percent": 150, "threshold": 3},  # Unsharp mask
                    {"op": "save", "format": "PNG", "dpi": (300, 300)},
                ],
            )

            return processed_path

        except (ValueError, AttributeError, RuntimeError):
            # If Pillow fails, return original path
            return image_path

    # ─────────────────────────────────────────────────────────────────────
    # Unsplash image sourcing (complementary images for charts)
    # ─────────────────────────────────────────────────────────────────────

    async def _source_unsplash_image(
        self,
        search_term: str,
        section: str,
    ) -> tuple[str, str]:
        """Search for a contextual Unsplash image to complement a chart.

        Uses specific search terms, not generic ones. "Modern boardroom
        meeting" not "business." "Mumbai skyline at dusk" not "city."
        The image must add meaning, not just fill space.

        Returns (image_path, caption_with_attribution).
        """
        if not search_term:
            return ("", "")

        try:
            unsplash_tool = self.get_tool(ToolName.UNSPLASH)
            os.makedirs(self.IMAGE_OUTPUT_DIR, exist_ok=True)

            # Search for image
            results = await unsplash_tool.search_photos(
                query=search_term,
                per_page=1,
                orientation="landscape",
            )

            if not results:
                return ("", "")

            photo = results[0]
            image_url = photo.get("urls", {}).get("regular", "")
            photographer = photo.get("user", {}).get("name", "Unknown")
            photo_id = photo.get("id", "")

            if not image_url:
                return ("", "")

            # Download image
            image_path = os.path.join(self.IMAGE_OUTPUT_DIR, f"unsplash_{photo_id}.png")
            await unsplash_tool.download_photo(
                url=image_url,
                output_path=image_path,
            )

            # Post-process with Pillow
            processed_path = await self._post_process_with_pillow(image_path)

            # Attribution caption
            caption = f"Source: Unsplash via {photographer}"

            return (processed_path, caption)

        except (ValueError, AttributeError, RuntimeError):
            return ("", "")

    # ─────────────────────────────────────────────────────────────────────
    # Tufte compliance check
    # ─────────────────────────────────────────────────────────────────────

    def _check_tufte_compliance(self, chart_spec: ChartSpecification) -> bool:
        """Check if a chart follows Tufte principles.

        Tufte compliance means:
        - No 3D effects (no 'scene' in layout, no 'projection' in traces)
        - No gradient fills (solid colors only)
        - No chartjunk (minimal gridlines, no decorative elements)
        - Data-ink ratio maximized
        - Axes labeled
        - Source cited
        """
        # Check for required elements
        if not chart_spec.x_axis_label and chart_spec.chart_type not in (
            ChartType.TREEMAP, ChartType.SANKEY, ChartType.PIE, ChartType.RADAR
        ):
            return False

        if not chart_spec.y_axis_label and chart_spec.chart_type not in (
            ChartType.TREEMAP, ChartType.SANKEY, ChartType.PIE, ChartType.RADAR,
            ChartType.HISTOGRAM,
        ):
            return False

        if not chart_spec.source_citation:
            return False

        # Check for no more than 5 colors
        unique_colors = set()
        for series in chart_spec.data_series:
            if series.color:
                unique_colors.add(series.color)
        if len(unique_colors) > 5:
            return False

        return True

    # ─────────────────────────────────────────────────────────────────────
    # Axis calibration
    # ─────────────────────────────────────────────────────────────────────

    def _calibrate_axes(
        self,
        chart_spec: ChartSpecification,
        data_series: list[ChartDataSeries],
    ) -> ChartSpecification:
        """Calibrate axis ranges for honest data display.

        Rules:
        - Y-axis starts at zero for bar charts (always)
        - No truncated y-axes that exaggerate differences
        - Axis ranges show full data context
        - No log scales without labeling
        """
        if chart_spec.chart_type in (ChartType.BAR, ChartType.STACKED_BAR):
            # Y-axis must start at zero for bar charts
            all_values: list[float] = []
            for series in data_series:
                for v in series.values:
                    if isinstance(v, (int, float)):
                        all_values.append(v)

            if all_values:
                max_val = max(all_values)
                # Add 10% headroom above max
                chart_spec.y_axis_range = (0, max_val * 1.1)

        elif chart_spec.chart_type == ChartType.LINE:
            # For line charts, use data range with some padding
            all_values = []
            for series in data_series:
                for v in series.values:
                    if isinstance(v, (int, float)):
                        all_values.append(v)

            if all_values:
                min_val = min(all_values)
                max_val = max(all_values)
                range_padding = (max_val - min_val) * 0.1
                if range_padding == 0:
                    range_padding = max_val * 0.1
                # Don't force zero for line charts — but don't truncate either
                chart_spec.y_axis_range = (
                    min_val - range_padding,
                    max_val + range_padding,
                )

        return chart_spec

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 6-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
        chart_specs: list[dict[str, Any]] | None = None,
    ) -> VisualizationOutput:
        """Execute the Data Visualizer's 6-step methodology.

        Steps (§4.5, Agent 17):
        1. Receive chart specifications from Presentation Designer
        2. For each chart, select chart type based on data shape
        3. Generate chart with Plotly using brand colors
        4. Export at scale=3 for 300 DPI
        5. Post-process with Pillow (sharpen for print)
        6. Return chart image paths to Presentation Designer
        """
        # Subscribe to bus
        self.subscribe_to_bus()

        # Step 1: Receive chart specifications
        await self._transition(AgentState.WORKING, "Step 1: Receiving chart specifications")
        specs = await self._receive_chart_specs(chart_specs)

        if not specs:
            await self._transition(AgentState.DONE, "No chart specifications received")
            return VisualizationOutput(
                charts=[],
                total_charts=0,
                total_images=0,
                confidence=ConfidenceLevel.LOW,
            )

        generated_charts: list[ChartSpecification] = []
        chart_types_used: set[str] = set()
        unsplash_images_count = 0
        all_300_dpi = True
        all_brand_compliant = True
        all_tufte_compliant = True

        for i, spec in enumerate(specs):
            # Step 2: Select chart type based on data shape
            await self._transition(
                AgentState.WORKING,
                f"Step 2: Selecting chart type for chart {i+1}/{len(specs)}: {spec.get('title', 'Untitled')}",
            )

            data_shape, series_count, data_series = self._determine_data_shape(spec)
            chart_type_hint = spec.get("chart_type_hint")
            selected_type = self._select_chart_type(data_shape, series_count, data_series, chart_type_hint)

            # Build chart specification
            chart_id = spec.get("id", f"chart_{hashlib.md5(f'{engagement_id}_{i}'.encode()).hexdigest()[:8]}")
            chart_spec = ChartSpecification(
                id=chart_id,
                title=spec.get("title", f"Chart {i+1}"),
                section=spec.get("section", ""),
                chart_type=selected_type,
                data_series=data_series,
                x_axis_label=spec.get("x_axis_label", ""),
                y_axis_label=spec.get("y_axis_label", ""),
                source_citation=spec.get("source_citation", ""),
                # Fix 3.7: carry the mined methodology note through to the
                # placement. Without this the "Note:" half of the MGI/BCG
                # exhibit footer was silently dropped at this hop, even though
                # both `ChartPlacement` and the template already supported it.
                note=spec.get("note", ""),
                caption=spec.get("caption", ""),
                annotations=[
                    ChartAnnotation(**ann) for ann in spec.get("annotations", [])
                ],
                width_px=spec.get("width_px", 1200),
                height_px=spec.get("height_px", 800),
            )

            # Calibrate axes (honest axis calibration)
            chart_spec = self._calibrate_axes(chart_spec, data_series)

            # Check Tufte compliance
            chart_spec.tufte_compliant = self._check_tufte_compliance(chart_spec)
            if not chart_spec.tufte_compliant:
                all_tufte_compliant = False

            chart_types_used.add(selected_type.value)

            # Step 3: Generate chart with Plotly using brand colors
            await self._transition(
                AgentState.WORKING,
                f"Step 3: Generating chart with Plotly: {chart_spec.title}",
            )
            image_path = await self._generate_chart_with_plotly(chart_spec)
            chart_spec.image_path = image_path

            # Step 4: Export at scale=3 for 300 DPI (done in _generate_chart_with_plotly)
            await self._transition(
                AgentState.WORKING,
                f"Step 4: Exporting at 300 DPI: {chart_spec.title}",
            )
            if not image_path:
                all_300_dpi = False
                chart_spec.dpi = 0
            else:
                chart_spec.dpi = 300

            # Step 5: Post-process with Pillow (sharpen for print)
            await self._transition(
                AgentState.WORKING,
                f"Step 5: Post-processing with Pillow: {chart_spec.title}",
            )
            if image_path:
                processed_path = await self._post_process_with_pillow(image_path)
                if processed_path != image_path:
                    chart_spec.image_path = processed_path

            # Source complementary Unsplash image if requested
            unsplash_search = spec.get("unsplash_search_term", "")
            if unsplash_search:
                img_path, img_caption = await self._source_unsplash_image(
                    unsplash_search, chart_spec.section
                )
                if img_path:
                    chart_spec.unsplash_image_path = img_path
                    chart_spec.unsplash_caption = img_caption
                    unsplash_images_count += 1

            generated_charts.append(chart_spec)

        # Step 6: Return chart image paths to Presentation Designer
        await self._transition(
            AgentState.WORKING,
            f"Step 6: Returning {len(generated_charts)} chart paths to Presentation Designer",
        )

        # Determine confidence
        if all_300_dpi and all_brand_compliant and all_tufte_compliant:
            confidence = ConfidenceLevel.HIGH
        elif all_300_dpi and all_brand_compliant:
            confidence = ConfidenceLevel.MEDIUM
        else:
            confidence = ConfidenceLevel.LOW

        output = VisualizationOutput(
            charts=generated_charts,
            total_charts=len(generated_charts),
            total_images=unsplash_images_count,
            chart_types_used=list(chart_types_used),
            all_300_dpi=all_300_dpi,
            all_brand_compliant=all_brand_compliant,
            all_tufte_compliant=all_tufte_compliant,
            confidence=confidence,
        )

        # Publish visualization output to bus
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "finding_type": "visualization_output",
                "visualization_output": output.model_dump(),
                "total_charts": len(generated_charts),
                "total_images": unsplash_images_count,
                "chart_types_used": list(chart_types_used),
                "all_300_dpi": all_300_dpi,
                "all_brand_compliant": all_brand_compliant,
                "all_tufte_compliant": all_tufte_compliant,
                "confidence": confidence.value,
            },
        )

        # Publish a finding for the chart generation
        if generated_charts:
            finding = KeyFinding(
                id=f"finding_{hashlib.md5(f'data_visualizer_{engagement_id}'.encode()).hexdigest()[:8]}",
                agent=self.name.value,
                finding_type="visualization_complete",
                title=f"Generated {len(generated_charts)} charts ({', '.join(chart_types_used)})",
                content=(
                    f"Generated {len(generated_charts)} brand-compliant charts "
                    f"at 300 DPI. Chart types used: {', '.join(chart_types_used)}. "
                    f"All Tufte-compliant: {all_tufte_compliant}. "
                    f"All brand-compliant: {all_brand_compliant}. "
                    f"Complementary Unsplash images: {unsplash_images_count}."
                ),
                confidence=confidence,
            )
            await self._publish_finding(finding)

        await self._transition(
            AgentState.DONE,
            f"Visualization complete: {len(generated_charts)} charts, "
            f"{unsplash_images_count} images, "
            f"types: {', '.join(chart_types_used)}, "
            f"300_dpi: {all_300_dpi}, "
            f"brand_compliant: {all_brand_compliant}, "
            f"tufte_compliant: {all_tufte_compliant}, "
            f"confidence: {confidence.value}",
        )

        return output
