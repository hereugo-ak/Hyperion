"""
HYPERION Chart Generator — Plotly charts with brand colors and Tufte principles.

This is NOT a generic "make a chart" wrapper. It implements the exact
specifications from ARCHITECTURE.md §4.5 (Agent 17) and §7.3 (chart colors):

- All charts use the HYPERION chart color sequence (terracotta, sage, deep
  brown, warm gray, beige, alert red). Never blue, purple, or green.
- First series is always Terracotta. No exceptions.
- Risk-related data uses Alert Red.
- Positive findings use Sage.
- Never more than 5 colors in a single chart.
- Export at scale=3 for 300 DPI via kaleido.
- Apply Tufte principles: no chartjunk, no 3D effects, no gradient fills.
- Every chart has a title, axis labels, and data source citation.
- Y-axis starts at zero for bar charts (always).

Chart types supported (§4.5 Agent 17):
- Bar (comparison)
- Line (trend)
- Scatter (correlation)
- Histogram (distribution)
- Stacked bar / Treemap (composition)
- Sankey (flow)
- Heatmap
- Radar
- Waterfall
- Pie (composition, ≤4 parts — discouraged)

MBB exhibit vocabulary (fix 4.3, audit §3.9):
- Tornado (sensitivity — which driver moves the answer most)
- Marimekko / mekko (two-dimensional composition: width × height)
- Football field (valuation range by methodology)
- Growth-share matrix (BCG portfolio: growth × relative share × size)
- Bubble (three variables — x, y, and area-encoded magnitude)

The chart type list is NOT authoritative here. `hyperion.schemas.models.ChartType`
is the canonical registry; this module's `_get_chart_creator` dispatch and the
`data_visualizer` trace builder are both required to cover it, and
`tests/test_mbb_chart_vocabulary.py` enforces that three-way parity. See the
`ChartType` docstring for why: an unrecognised type silently renders as a bar
chart in both dispatchers, so drift is lossy but never raises.

Architecture reference: §4.5 Agent 17, §7.3 Chart Color Sequence

Methodology (§4.5):
1. Receive chart specifications from Presentation Designer
2. For each chart, select chart type based on data shape
3. Generate chart with Plotly using brand colors
4. Export at scale=3 for 300 DPI
5. Post-process with Pillow (sharpen for print)
6. Return chart image paths to Presentation Designer

Used by: Data Visualizer (PLOTLY tool), Presentation Designer (PLOTLY tool) (§5.1)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


# Chart color sequence (§7.3) — always in this order
CHART_COLORS = [
    "#C8704D",  # Terracotta — always first series
    "#7C9885",  # Sage — always second series
    "#3D3530",  # Deep Brown — tertiary
    "#8B8680",  # Warm Gray — quaternary
    "#E8E6DD",  # Beige — light fill
    "#B5533C",  # Alert Red — risk series only
]

# PDF palette for chart backgrounds and text
CHART_BG_COLOR = "#F5F4EE"      # Cream — page background
CHART_TEXT_COLOR = "#1A1A1A"    # Warm Charcoal — text
CHART_GRID_COLOR = "#E8E6DD"    # Beige — grid lines
CHART_PAPER_COLOR = "#F5F4EE"   # Cream — plot paper


@dataclass
class ChartSpec:
    """Specification for a chart to be generated.

    Passed from the Presentation Designer to the Data Visualizer.
    """

    # Valid values are the `.value`s of `hyperion.schemas.models.ChartType`
    # (the canonical registry). Deliberately typed `str` rather than the enum
    # so this dataclass stays importable without pulling in pydantic, but the
    # allowed set is not this module's to define. See the module docstring.
    chart_type: str = "bar"
    title: str = ""
    x_label: str = ""
    y_label: str = ""
    x_data: list[Any] = field(default_factory=list)
    y_data: list[list[Any]] = field(default_factory=list)  # Multiple series
    series_names: list[str] = field(default_factory=list)
    source: str = ""  # Data source citation
    caption: str = ""
    width: int = 1200
    height: int = 800
    orientation: str = "v"  # v=vertical, h=horizontal
    is_risk: bool = False  # If True, use Alert Red for primary series
    annotations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chart_type": self.chart_type,
            "title": self.title,
            "x_label": self.x_label,
            "y_label": self.y_label,
            "x_data": self.x_data,
            "y_data": self.y_data,
            "series_names": self.series_names,
            "source": self.source,
            "caption": self.caption,
            "width": self.width,
            "height": self.height,
            "orientation": self.orientation,
            "is_risk": self.is_risk,
            "annotations": self.annotations,
        }


@dataclass
class ChartResult:
    """Result of generating a chart."""

    spec: ChartSpec
    image_path: str = ""
    success: bool = False
    error: str = ""
    width: int = 0
    height: int = 0
    dpi: int = 300

    def to_dict(self) -> dict[str, Any]:
        return {
            "spec": self.spec.to_dict(),
            "image_path": self.image_path,
            "success": self.success,
            "error": self.error,
            "width": self.width,
            "height": self.height,
            "dpi": self.dpi,
        }


class ChartGenerator:
    """Plotly chart generator with brand colors and Tufte principles.

    Generates charts using the HYPERION chart color sequence, exports
    at scale=3 for 300 DPI, and applies Tufte principles (no chartjunk,
    no 3D effects, no gradient fills).

    Usage:
        generator = ChartGenerator(settings=settings)

        spec = ChartSpec(
            chart_type="bar",
            title="Market Size by Segment (2024)",
            x_data=["SMB", "Mid-Market", "Enterprise"],
            y_data=[[120, 340, 580]],
            series_names=["Revenue ($M)"],
            source="Alpha Vantage, FRED",
            x_label="Segment",
            y_label="Revenue ($M)",
        )

        result = generator.generate(spec)
        if result.success:
            print(f"Chart saved to: {result.image_path}")
    """

    EXPORT_SCALE = 3  # scale=3 for 300 DPI (§4.5 methodology step 4)
    EXPORT_FORMAT = "png"
    DEFAULT_WIDTH = 1200
    DEFAULT_HEIGHT = 800

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._output_dir = Path("assets/images/charts")
        if settings:
            self._output_dir = Path(getattr(settings, "assets_dir", "assets")) / "images" / "charts"
        self._output_dir.mkdir(parents=True, exist_ok=True)

    def _get_colors(self, spec: ChartSpec) -> list[str]:
        """Get the color sequence for a chart.

        Risk charts use Alert Red as the primary color.
        All other charts use Terracotta as the primary color.
        """
        if spec.is_risk:
            # Risk data uses Alert Red as primary
            colors = [CHART_COLORS[5]]  # Alert Red
            colors.extend(CHART_COLORS[0:4])  # Then standard sequence
        else:
            colors = CHART_COLORS[:5]  # Max 5 colors (§7.3)

        return colors[:max(len(spec.series_names), 1)]

    def _apply_brand_styling(self, fig: Any, spec: ChartSpec) -> Any:
        """Apply HYPERION brand styling to a Plotly figure.

        This is NOT optional. Every chart must use brand colors, brand
        fonts, and Tufte-compliant layout. No exceptions.
        """
        colors = self._get_colors(spec)

        # Apply colorway (brand color sequence)
        fig.update_layout(
            colorway=colors,
            paper_bgcolor=CHART_PAPER_COLOR,
            plot_bgcolor=CHART_BG_COLOR,
            font=dict(
                family="Source Sans 3, sans-serif",  # D24: body font
                size=12,
                color=CHART_TEXT_COLOR,
            ),
            title=dict(
                text=spec.title,
                font=dict(
                    family="Instrument Serif, serif",
                    size=22,
                    color=CHART_TEXT_COLOR,
                ),
                x=0.5,  # Center title
                xanchor="center",
            ),
            xaxis=dict(
                title=spec.x_label,
                gridcolor=CHART_GRID_COLOR,
                zerolinecolor=CHART_GRID_COLOR,
                tickfont=dict(family="JetBrains Mono, monospace", size=10),  # D24: mono for numbers
            ),
            yaxis=dict(
                title=spec.y_label,
                gridcolor=CHART_GRID_COLOR,
                zerolinecolor=CHART_GRID_COLOR,
                tickfont=dict(family="JetBrains Mono, monospace", size=10),  # D24: mono for numbers
            ),
            legend=dict(
                font=dict(family="Source Sans 3, sans-serif", size=10),
                bgcolor=CHART_BG_COLOR,
                bordercolor=CHART_GRID_COLOR,
                borderwidth=1,
            ),
            # Tufte principles: no chartjunk
            showlegend=True if len(spec.series_names) > 1 else False,
            margin=dict(l=60, r=40, t=80, b=60),
        )

        # Bar charts: y-axis starts at zero (always — §4.5 Agent 17 skill)
        # Fix (audit follow-up to 0.4): `update_yaxis` (singular) is not a
        # Plotly Figure method — it has always been `update_yaxes` (plural).
        # This raised AttributeError on every bar/stacked_bar chart, and
        # AttributeError is NOT in the `except (ValueError, RuntimeError,
        # OSError, ImportError)` tuple in generate() below, so it propagated
        # past all three fallback tiers (Plotly -> matplotlib -> data table)
        # instead of degrading gracefully — a second, independent cause of
        # `has_exhibits: false` for the most common chart type.
        if spec.chart_type in ("bar", "stacked_bar"):
            fig.update_yaxes(rangemode="tozero")

        # No 3D effects, no gradient fills (Tufte).
        # Fix (audit follow-up to 0.4): `marker_line_width` and `opacity` are
        # not valid properties on every trace type — Sankey, Heatmap, and
        # Waterfall traces reject `opacity`/`marker` at the trace level (only
        # `Heatmap`/`Waterfall` support `opacity`; `Sankey` supports neither).
        # This unconditional `update_traces()` raised `ValueError` for those
        # three chart types on every single call, which (like the
        # `update_yaxis` typo above) is NOT caught by the pre-fix exception
        # tuple in `generate()` for AttributeError-class bugs but WAS already
        # a `ValueError` here — meaning sankey/heatmap/waterfall have been
        # silently falling through to the Tier 2 matplotlib fallback on
        # every single call, never rendering via Plotly. Scope the update to
        # trace types that actually support these properties.
        try:
            fig.update_traces(
                selector=dict(type="bar"),
                marker_line_width=0,
                opacity=0.95,
            )
            fig.update_traces(
                selector=lambda t: t.type in ("scatter", "scatterpolar", "histogram", "treemap"),
                opacity=0.95,
            )
            fig.update_traces(
                selector=dict(type="heatmap"),
                opacity=0.95,
            )
            fig.update_traces(
                selector=dict(type="waterfall"),
                opacity=0.95,
            )
            # Sankey supports neither `marker` nor `opacity` at the trace
            # level — deliberately not touched here.
        except (ValueError, TypeError):
            # Never let cosmetic styling break chart generation — the chart
            # itself (data + colors) is already correct without this step.
            pass

        # Add source citation as annotation at bottom
        if spec.source:
            fig.add_annotation(
                text=f"Source: {spec.source}",
                xref="paper",
                yref="paper",
                x=0.5,
                y=-0.15,
                showarrow=False,
                font=dict(family="Source Sans 3, sans-serif", size=8, color="#8B8680"),
            )

        # Add custom annotations
        for ann in spec.annotations:
            fig.add_annotation(**ann)

        return fig

    def _import_plotly(self) -> tuple[Any, Any]:
        """Import Plotly components. Returns (go, pio)."""
        import plotly.graph_objects as go
        import plotly.io as pio

        return go, pio

    def _create_bar(self, spec: ChartSpec, go: Any) -> Any:
        """Create a bar chart."""
        colors = self._get_colors(spec)

        fig = go.Figure()
        for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
            fig.add_trace(go.Bar(
                x=spec.x_data,
                y=y_values,
                name=name,
                marker_color=colors[i % len(colors)],
                orientation=spec.orientation,
            ))

        return fig

    def _create_line(self, spec: ChartSpec, go: Any) -> Any:
        """Create a line chart."""
        colors = self._get_colors(spec)

        fig = go.Figure()
        for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
            fig.add_trace(go.Scatter(
                x=spec.x_data,
                y=y_values,
                mode="lines+markers",
                name=name,
                line=dict(color=colors[i % len(colors)], width=2),
                marker=dict(size=6, color=colors[i % len(colors)]),
            ))

        return fig

    def _create_scatter(self, spec: ChartSpec, go: Any) -> Any:
        """Create a scatter chart."""
        colors = self._get_colors(spec)

        fig = go.Figure()
        for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
            fig.add_trace(go.Scatter(
                x=spec.x_data,
                y=y_values,
                mode="markers",
                name=name,
                marker=dict(size=8, color=colors[i % len(colors)], opacity=0.7),
            ))

        return fig

    def _create_histogram(self, spec: ChartSpec, go: Any) -> Any:
        """Create a histogram."""
        colors = self._get_colors(spec)

        fig = go.Figure()
        for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
            fig.add_trace(go.Histogram(
                x=y_values,
                name=name,
                marker_color=colors[i % len(colors)],
                opacity=0.7,
            ))

        return fig

    def _create_stacked_bar(self, spec: ChartSpec, go: Any) -> Any:
        """Create a stacked bar chart."""
        colors = self._get_colors(spec)

        fig = go.Figure()
        for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
            fig.add_trace(go.Bar(
                x=spec.x_data,
                y=y_values,
                name=name,
                marker_color=colors[i % len(colors)],
            ))

        fig.update_layout(barmode="stack")
        return fig

    def _create_treemap(self, spec: ChartSpec, go: Any) -> Any:
        """Create a treemap chart."""
        colors = self._get_colors(spec)

        # For treemap, x_data = labels, y_data[0] = values
        labels = spec.x_data
        values = spec.y_data[0] if spec.y_data else []
        parents = [""] * len(labels)

        fig = go.Figure(go.Treemap(
            labels=labels,
            values=values,
            parents=parents,
            marker=dict(colors=colors[:len(labels)]),
            textfont=dict(family="JetBrains Mono, monospace"),
        ))

        return fig

    def _create_sankey(self, spec: ChartSpec, go: Any) -> Any:
        """Create a Sankey diagram.

        For Sankey, x_data = source labels, y_data = [target labels, values].
        """
        sources = spec.x_data
        targets = spec.y_data[0] if len(spec.y_data) > 0 else []
        values = spec.y_data[1] if len(spec.y_data) > 1 else []

        # Create node labels
        all_labels = list(set(sources + targets))
        source_indices = [all_labels.index(s) for s in sources]
        target_indices = [all_labels.index(t) for t in targets]

        fig = go.Figure(go.Sankey(
            node=dict(
                pad=15,
                thickness=20,
                line=dict(color=CHART_GRID_COLOR, width=0.5),
                label=all_labels,
                color=CHART_COLORS[:len(all_labels)],
            ),
            link=dict(
                source=source_indices,
                target=target_indices,
                value=values,
                color=CHART_COLORS[0],
            ),
        ))

        return fig

    def _create_heatmap(self, spec: ChartSpec, go: Any) -> Any:
        """Create a heatmap.

        For heatmap, x_data = x labels, y_data[0] = y labels, y_data[1] = z values.
        """
        x_labels = spec.x_data
        y_labels = spec.y_data[0] if len(spec.y_data) > 0 else []
        z_values = spec.y_data[1] if len(spec.y_data) > 1 else []

        fig = go.Figure(go.Heatmap(
            x=x_labels,
            y=y_labels,
            z=z_values,
            colorscale=[[0, CHART_COLORS[4]], [0.5, CHART_COLORS[0]], [1, CHART_COLORS[5]]],
        ))

        return fig

    def _create_radar(self, spec: ChartSpec, go: Any) -> Any:
        """Create a radar chart."""
        colors = self._get_colors(spec)

        fig = go.Figure()
        for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
            fig.add_trace(go.Scatterpolar(
                r=y_values,
                theta=spec.x_data,
                fill="toself",
                name=name,
                line=dict(color=colors[i % len(colors)]),
                fillcolor=colors[i % len(colors)].replace(")", ", 0.2)").replace("rgb", "rgba") if "rgb" in colors[i % len(colors)] else colors[i % len(colors)],
            ))

        return fig

    def _create_waterfall(self, spec: ChartSpec, go: Any) -> Any:
        """Create a waterfall chart."""
        # D5.1: a `colors = self._get_colors(spec)` local sat here unread (ruff
        # F841). Waterfall traces colour themselves via `increasing`/`decreasing`/
        # `totals` markers rather than a per-point colour list, so the palette
        # genuinely does not apply — removed rather than wired in, which would
        # have overridden the semantic up/down colouring with brand hues.

        # For waterfall, y_data[0] = values (positive/negative)
        values = spec.y_data[0] if spec.y_data else []

        # Calculate cumulative for waterfall
        measures = []
        for v in values:
            if v >= 0:
                measures.append("relative")
            else:
                measures.append("relative")

        fig = go.Figure(go.Waterfall(
            x=spec.x_data,
            y=values,
            measure=measures,
            increasing=dict(marker=dict(color=CHART_COLORS[1])),  # Sage for increase
            decreasing=dict(marker=dict(color=CHART_COLORS[5])),  # Alert Red for decrease
            totals=dict(marker=dict(color=CHART_COLORS[2])),  # Deep Brown for totals
            connector=dict(line=dict(color=CHART_GRID_COLOR, width=1)),
        ))

        return fig

    # ─────────────────────────────────────────────────────────────────────
    # MBB exhibit vocabulary (fix 4.3, audit §3.9)
    #
    # The audit found HYPERION could draw the generic business-graphics set
    # (bar/line/scatter/histogram/stacked/treemap/sankey/heatmap/radar/
    # waterfall) but none of the exhibit forms that actually distinguish MBB
    # work product. The five below are the named gap.
    #
    # Each of these needs more than one numeric dimension per category, which
    # `ChartSpec` carries as extra rows in `y_data`. That per-type convention
    # is pre-existing house style (treemap reads `x_data` as labels; sankey
    # reads `y_data[0]` as targets and `y_data[1]` as values; heatmap reads
    # `y_data[0]` as y-labels and `y_data[1]` as the z-matrix), so these
    # follow it rather than inventing a parallel spec type. Each creator
    # documents its own layout in its docstring, and every one of them
    # tolerates missing rows by degrading to something drawable rather than
    # raising — an exhibit that is merely less informative beats an exhibit
    # that falls through to Tier 2 and loses its brand geometry entirely.
    # ─────────────────────────────────────────────────────────────────────

    @staticmethod
    def _nums(values: list[Any]) -> list[float]:
        """Coerce a data row to floats, mapping anything non-numeric to 0.0.

        Chart data arrives from an LLM by way of pydantic models that permit
        `float | str`, so a row can legitimately contain `"n/a"`, `"12%"`, or
        `None`. The MBB chart types below do arithmetic on their rows (sorting
        by swing, computing cumulative widths, deriving a size reference), and
        a `TypeError` there would be caught by `generate()` and silently
        demote the exhibit to the matplotlib tier. Coercing is the lesser
        evil: a zero is visible and obviously wrong to a reviewer, whereas a
        silent tier demotion is invisible.
        """
        out: list[float] = []
        for v in values:
            try:
                out.append(float(str(v).strip().rstrip("%").replace(",", "")))
            except (TypeError, ValueError):
                out.append(0.0)
        return out

    def _color_cycle(self, spec: ChartSpec, count: int) -> list[str]:
        """Return `count` brand colors, cycling within the §7.3 five-color cap.

        `_get_colors` truncates its return to `len(spec.series_names)`, which
        is correct for one-color-per-series charts but wrong for the MBB types
        below, where the number of drawn colors is driven by the number of
        *categories* (marimekko segments, growth-share business units) rather
        than the number of series names. Calling `_get_colors` for those would
        raise `IndexError` on the second category of a single-series spec.
        """
        base = self._get_colors(spec) or CHART_COLORS[:1]
        if len(base) < min(count, 5):
            # Extend from the standard sequence, preserving the primary color
            # already chosen by `_get_colors` (Alert Red for risk charts).
            for c in CHART_COLORS[:5]:
                if c not in base:
                    base = [*base, c]
        return [base[i % len(base)] for i in range(max(count, 1))]

    def _create_tornado(self, spec: ChartSpec, go: Any) -> Any:
        """Create a tornado (sensitivity) chart.

        Answers "which driver moves the answer most" — the standard MBB
        sensitivity exhibit. Drivers are sorted by total swing so the widest
        bar sits at the top, which is what makes the shape a tornado and not
        just a diverging bar chart.

        Data layout:
            x_data     = driver names
            y_data[0]  = downside deltas (conventionally negative)
            y_data[1]  = upside deltas (optional; mirrors downside if absent)
        """
        labels = [str(x) for x in spec.x_data]
        low = self._nums(spec.y_data[0]) if spec.y_data else []
        high = self._nums(spec.y_data[1]) if len(spec.y_data) > 1 else [-v for v in low]

        # Pad so a short row cannot silently drop drivers off the chart.
        low = (low + [0.0] * len(labels))[: len(labels)]
        high = (high + [0.0] * len(labels))[: len(labels)]

        # Widest total swing at the top. Plotly draws the first category at
        # the bottom of a horizontal axis, so ascending order here renders
        # descending on screen.
        order = sorted(range(len(labels)), key=lambda i: abs(high[i] - low[i]))
        labels = [labels[i] for i in order]
        low = [low[i] for i in order]
        high = [high[i] for i in order]

        down_name = spec.series_names[0] if spec.series_names else "Downside"
        up_name = spec.series_names[1] if len(spec.series_names) > 1 else "Upside"

        fig = go.Figure()
        # Alert Red for the downside, Sage for the upside — the same semantic
        # coloring §7.3 already mandates for waterfall increases/decreases.
        # This deliberately overrides "first series is Terracotta": on a
        # sensitivity chart the sign of the bar IS the information, and
        # coloring it by series index would hide it.
        fig.add_trace(go.Bar(
            y=labels,
            x=low,
            name=down_name,
            orientation="h",
            base=0,
            marker_color=CHART_COLORS[5],
        ))
        fig.add_trace(go.Bar(
            y=labels,
            x=high,
            name=up_name,
            orientation="h",
            base=0,
            marker_color=CHART_COLORS[1],
        ))

        fig.update_layout(barmode="overlay", bargap=0.35)
        # An explicit shape rather than relying on `xaxis.zeroline`: on a
        # tornado the baseline is the reference case, so it should read as a
        # deliberate annotation at full plot height and in the text color,
        # not as the faint beige grid line `_apply_brand_styling` sets via
        # `zerolinecolor`. (That styling would survive — `update_layout`
        # merges into the existing axis rather than replacing it — it is
        # simply too quiet for a line that carries meaning here.)
        fig.add_shape(
            type="line",
            x0=0, x1=0, y0=0, y1=1,
            yref="paper",
            line=dict(color=CHART_TEXT_COLOR, width=1.5),
        )
        return fig

    def _create_marimekko(self, spec: ChartSpec, go: Any) -> Any:
        """Create a marimekko (mekko) chart — two-dimensional composition.

        Column *width* encodes one magnitude (segment size, revenue pool) and
        column *height* encodes composition within it (share by player). Both
        dimensions are read at once, which is the whole point: a stacked bar
        shows mix, a mekko shows mix weighted by how much each column matters.

        Data layout:
            x_data       = column labels
            y_data[0]    = column widths (the first magnitude)
            y_data[1:]   = one row per stacked segment, values within columns
            series_names = [width dimension name, segment names...]
        """
        labels = [str(x) for x in spec.x_data]
        widths = self._nums(spec.y_data[0]) if spec.y_data else []
        widths = (widths + [1.0] * len(labels))[: len(labels)]
        # A zero or negative width would collapse the column to invisibility
        # and shift every subsequent column's center.
        widths = [w if w > 0 else 1.0 for w in widths]

        segments = [self._nums(row) for row in spec.y_data[1:]]
        if not segments:
            # Degenerate but drawable: one segment at full height, so the
            # chart still communicates the width dimension.
            segments = [[100.0] * len(labels)]

        seg_names = spec.series_names[1:] if len(spec.series_names) > 1 else []
        if len(seg_names) < len(segments):
            seg_names = [*seg_names] + [
                f"Segment {i + 1}" for i in range(len(seg_names), len(segments))
            ]

        # Column centers on a continuous axis — this is what makes widths
        # meaningful. Bars on a categorical axis are always equally spaced.
        centers: list[float] = []
        running = 0.0
        for w in widths:
            centers.append(running + w / 2)
            running += w

        colors = self._color_cycle(spec, len(segments))

        fig = go.Figure()
        for i, row in enumerate(segments):
            row = (row + [0.0] * len(labels))[: len(labels)]
            fig.add_trace(go.Bar(
                x=centers,
                y=row,
                width=widths,
                name=seg_names[i],
                marker_color=colors[i % len(colors)],
                marker_line=dict(color=CHART_BG_COLOR, width=1),
            ))

        fig.update_layout(barmode="stack", bargap=0)
        # Label the columns at their true centers, and show the width value
        # so the second dimension is readable and not merely suggestive.
        fig.update_xaxes(
            tickmode="array",
            tickvals=centers,
            ticktext=[f"{lab}<br>{w:g}" for lab, w in zip(labels, widths, strict=True)],
        )
        return fig

    def _create_football_field(self, spec: ChartSpec, go: Any) -> Any:
        """Create a football-field chart — valuation range by methodology.

        One floating horizontal bar per methodology (DCF, trading comps,
        precedent transactions, 52-week range), spanning that method's low to
        high. The reader's takeaway is where the ranges *overlap*, so the bars
        are drawn as floating spans rather than bars from zero — a bar from
        zero would compress every range into the right-hand margin.

        Data layout:
            x_data     = methodology names
            y_data[0]  = range low per methodology
            y_data[1]  = range high per methodology
            y_data[2]  = optional single reference value (e.g. current price);
                         first element is used, drawn as a vertical line
        """
        labels = [str(x) for x in spec.x_data]
        lows = self._nums(spec.y_data[0]) if spec.y_data else []
        highs = self._nums(spec.y_data[1]) if len(spec.y_data) > 1 else []
        lows = (lows + [0.0] * len(labels))[: len(labels)]
        highs = (highs + [0.0] * len(labels))[: len(labels)]

        # Tolerate low/high supplied the wrong way round rather than drawing
        # a negative-width bar (which Plotly renders as nothing at all).
        pairs = [(min(lo, hi), max(lo, hi)) for lo, hi in zip(lows, highs, strict=True)]
        spans = [hi - lo for lo, hi in pairs]
        bases = [lo for lo, _ in pairs]

        fig = go.Figure()
        fig.add_trace(go.Bar(
            y=labels,
            x=spans,
            base=bases,
            orientation="h",
            name=spec.series_names[0] if spec.series_names else "Valuation range",
            marker_color=self._get_colors(spec)[0],
            text=[f"{lo:g} – {hi:g}" for lo, hi in pairs],
            textposition="outside",
            textfont=dict(family="JetBrains Mono, monospace", size=10),
        ))

        # Reference line (current price / offer). A shape rather than a trace
        # so it stays out of the legend and off the category axis.
        if len(spec.y_data) > 2:
            ref_row = self._nums(spec.y_data[2])
            if ref_row:
                fig.add_shape(
                    type="line",
                    x0=ref_row[0], x1=ref_row[0], y0=0, y1=1,
                    yref="paper",
                    line=dict(color=CHART_COLORS[2], width=1.5, dash="dash"),
                )

        # Reserve room on the right for the outside value labels. Plotly sizes
        # the axis to the DATA, not to the annotations drawn beyond it, so the
        # "50 – 80" label on the widest range was rendering clipped against
        # the figure edge — found by rendering the exhibit and looking at it,
        # not by any assertion, which is why the visual check is part of this
        # fix's verification and not an afterthought.
        finite = [v for pair in pairs for v in pair]
        if finite:
            lo, hi = min(finite), max(finite)
            pad = (hi - lo) * 0.18 or abs(hi) * 0.18 or 1.0
            # Left pad is small (the bars start at the low, so there is no
            # label there); right pad carries the text.
            fig.update_xaxes(range=[lo - pad * 0.15, hi + pad])

        fig.update_layout(bargap=0.45)
        return fig

    def _create_bubble(self, spec: ChartSpec, go: Any) -> Any:
        """Create a bubble chart — three variables, area-encoded magnitude.

        Data layout:
            x_data     = point labels
            y_data[0]  = x values
            y_data[1]  = y values
            y_data[2]  = magnitude, encoded as bubble AREA (not diameter)
        """
        return self._bubble_figure(spec, go)

    def _create_growth_share(self, spec: ChartSpec, go: Any) -> Any:
        """Create a growth-share (BCG portfolio) matrix.

        A bubble chart with fixed semantics: relative market share on a
        reversed x-axis (high share left, per BCG convention), market growth
        on y, revenue as area, and quadrant dividers at share = 1.0x and at
        the growth midpoint — which is what turns four scattered points into
        stars / cash cows / question marks / dogs.

        Data layout:
            x_data     = business unit names
            y_data[0]  = relative market share (1.0 = parity with leader)
            y_data[1]  = market growth rate (%)
            y_data[2]  = revenue or another size magnitude
        """
        fig = self._bubble_figure(spec, go)

        growth = self._nums(spec.y_data[1]) if len(spec.y_data) > 1 else []

        # Vertical divider at relative share = 1.0x (parity with the market
        # leader) — the BCG convention, not the data median. A median would
        # guarantee two units land on each side regardless of whether any of
        # them actually leads its market.
        fig.add_shape(
            type="line",
            x0=1.0, x1=1.0, y0=0, y1=1,
            yref="paper",
            line=dict(color=CHART_COLORS[3], width=1, dash="dot"),
        )
        if growth:
            midpoint = (max(growth) + min(growth)) / 2
            fig.add_shape(
                type="line",
                x0=0, x1=1, y0=midpoint, y1=midpoint,
                xref="paper",
                line=dict(color=CHART_COLORS[3], width=1, dash="dot"),
            )

        # High relative share on the LEFT (BCG convention). Reversing the
        # axis rather than negating the data keeps the tick labels honest.
        fig.update_xaxes(autorange="reversed")
        return fig

    def _bubble_figure(self, spec: ChartSpec, go: Any) -> Any:
        """Shared bubble construction for `bubble` and `growth_share`.

        Area encoding, not diameter: `sizemode="area"` with an explicit
        `sizeref`. Plotly's default is diameter, which overstates large
        values quadratically — precisely the distortion Tufte's lie factor
        measures, and a common way real consulting decks mislead.
        """
        labels = [str(x) for x in spec.x_data]
        xs = self._nums(spec.y_data[0]) if spec.y_data else []
        ys = self._nums(spec.y_data[1]) if len(spec.y_data) > 1 else []
        sizes = self._nums(spec.y_data[2]) if len(spec.y_data) > 2 else []

        n = len(labels) or max(len(xs), len(ys))
        xs = (xs + [0.0] * n)[:n]
        ys = (ys + [0.0] * n)[:n]
        if not sizes:
            sizes = [1.0] * n
        sizes = (sizes + [0.0] * n)[:n]
        if not labels:
            labels = [f"Item {i + 1}" for i in range(n)]

        # sizeref maps the largest magnitude to MAX_BUBBLE_PX of diameter.
        max_size = max([abs(s) for s in sizes] + [1.0])
        max_px = 64.0
        sizeref = 2.0 * max_size / (max_px**2)

        colors = self._color_cycle(spec, n)

        fig = go.Figure()
        fig.add_trace(go.Scatter(
            x=xs,
            y=ys,
            mode="markers+text",
            name=spec.series_names[0] if spec.series_names else "Portfolio",
            text=labels,
            textposition="top center",
            textfont=dict(family="Source Sans 3, sans-serif", size=10),
            marker=dict(
                size=[abs(s) for s in sizes],
                sizemode="area",
                sizeref=sizeref,
                sizemin=6,
                color=colors[:n],
                opacity=0.75,
                line=dict(color=CHART_TEXT_COLOR, width=0.5),
            ),
        ))
        return fig

    def _create_pie(self, spec: ChartSpec, go: Any) -> Any:
        """Create a pie chart. Discouraged (§7.3) — kept for composition ≤4 parts.

        Fix 4.3 note: `ChartType.PIE` has existed in the canonical registry,
        and `_select_chart_type` could return it, but `_get_chart_creator`
        had no `"pie"` key — so every pie request fell through the dispatch
        dict's `.get(chart_type, self._create_bar)` default and rendered a
        BAR chart. No exception, no warning, correct data, wrong geometry.
        That is the exact silent-drift failure mode the `ChartType` docstring
        now describes, found by writing the three-way parity test rather than
        by reading the code.

        Data layout:
            x_data    = slice labels
            y_data[0] = slice values
        """
        labels = [str(x) for x in spec.x_data]
        values = self._nums(spec.y_data[0]) if spec.y_data else []

        fig = go.Figure(go.Pie(
            labels=labels,
            values=values,
            marker=dict(colors=self._color_cycle(spec, len(labels))),
            textinfo="label+percent",
            textposition="outside",
            textfont=dict(family="Source Sans 3, sans-serif", size=11),
            sort=False,
        ))
        return fig

    def _get_chart_creator(self, chart_type: str) -> Any:
        """Get the chart creation method for a chart type.

        Every `.value` of `hyperion.schemas.models.ChartType` must appear as a
        key here. The `.get(..., self._create_bar)` default means a missing key
        is not an error — it is a silently mis-drawn exhibit (see `_create_pie`
        for the case where that actually happened), so the coverage invariant
        is enforced by `tests/test_mbb_chart_vocabulary.py` instead of by
        anything at runtime.
        """
        creators = {
            "bar": self._create_bar,
            "line": self._create_line,
            "scatter": self._create_scatter,
            "histogram": self._create_histogram,
            "stacked_bar": self._create_stacked_bar,
            "treemap": self._create_treemap,
            "sankey": self._create_sankey,
            "heatmap": self._create_heatmap,
            "radar": self._create_radar,
            "waterfall": self._create_waterfall,
            "pie": self._create_pie,
            # MBB exhibit vocabulary (fix 4.3)
            "tornado": self._create_tornado,
            "marimekko": self._create_marimekko,
            "football_field": self._create_football_field,
            "growth_share": self._create_growth_share,
            "bubble": self._create_bubble,
        }
        return creators.get(chart_type, self._create_bar)

    def _generate_matplotlib(self, spec: ChartSpec) -> ChartResult:
        """D26: Generate a chart using matplotlib as a fallback when kaleido/Plotly fails.

        Uses the same brand colors and Tufte principles as the Plotly path.
        Exports at 300 DPI via matplotlib's savefig.
        """
        result = ChartResult(spec=spec)

        try:
            import matplotlib
            matplotlib.use("Agg")  # Headless backend
            import matplotlib.pyplot as plt

            colors = self._get_colors(spec)
            bg_color = "#F5F4EE"
            text_color = "#1A1A1A"
            grid_color = "#E8E6DD"

            fig, ax = plt.subplots(figsize=(8, 5), facecolor=bg_color)
            ax.set_facecolor(bg_color)

            chart_type = spec.chart_type

            if chart_type == "bar":
                x = spec.x_data
                for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
                    ax.bar(x, y_values, color=colors[i % len(colors)], label=name, alpha=0.95)
                if spec.orientation == "h":
                    ax.invert_yaxis()
            elif chart_type == "line":
                for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
                    ax.plot(spec.x_data, y_values, color=colors[i % len(colors)], marker="o", markersize=4, linewidth=2, label=name)
            elif chart_type == "scatter":
                for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
                    ax.scatter(spec.x_data, y_values, color=colors[i % len(colors)], alpha=0.7, s=40, label=name)
            elif chart_type == "stacked_bar":
                bottom = [0] * len(spec.x_data)
                for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
                    ax.bar(spec.x_data, y_values, bottom=bottom, color=colors[i % len(colors)], label=name)
                    bottom = [b + v for b, v in zip(bottom, y_values)]

            # ── MBB exhibit vocabulary (fix 4.3) ──────────────────────────
            # These four are handled explicitly rather than left to the
            # generic `else` below. The `else` draws a vertical bar from
            # zero, which for a tornado, a football field, or a bubble chart
            # is not a degraded rendering of the exhibit — it is a different
            # and actively misleading exhibit. A tornado's downside row would
            # become a set of bars hanging below zero with the upside row
            # drawn over the top of it; a football field's `y_data[0]` (range
            # lows) would be drawn as if the lows were the values. Tier 2 is
            # supposed to preserve the *meaning* at lower fidelity.
            elif chart_type == "tornado":
                labels = [str(x) for x in spec.x_data]
                low = self._nums(spec.y_data[0]) if spec.y_data else []
                high = self._nums(spec.y_data[1]) if len(spec.y_data) > 1 else [-v for v in low]
                low = (low + [0.0] * len(labels))[: len(labels)]
                high = (high + [0.0] * len(labels))[: len(labels)]
                order = sorted(range(len(labels)), key=lambda i: abs(high[i] - low[i]))
                ax.barh([labels[i] for i in order], [low[i] for i in order],
                        color=CHART_COLORS[5], label=spec.series_names[0] if spec.series_names else "Downside")
                ax.barh([labels[i] for i in order], [high[i] for i in order],
                        color=CHART_COLORS[1],
                        label=spec.series_names[1] if len(spec.series_names) > 1 else "Upside")
                ax.axvline(0, color=text_color, linewidth=1.2)

            elif chart_type == "football_field":
                labels = [str(x) for x in spec.x_data]
                lows = self._nums(spec.y_data[0]) if spec.y_data else []
                highs = self._nums(spec.y_data[1]) if len(spec.y_data) > 1 else []
                lows = (lows + [0.0] * len(labels))[: len(labels)]
                highs = (highs + [0.0] * len(labels))[: len(labels)]
                pairs = [(min(lo, hi), max(lo, hi)) for lo, hi in zip(lows, highs, strict=True)]
                ax.barh(labels, [hi - lo for lo, hi in pairs],
                        left=[lo for lo, _ in pairs], color=colors[0])
                if len(spec.y_data) > 2:
                    ref = self._nums(spec.y_data[2])
                    if ref:
                        ax.axvline(ref[0], color=CHART_COLORS[2], linewidth=1.2, linestyle="--")

            elif chart_type in ("bubble", "growth_share"):
                xs = self._nums(spec.y_data[0]) if spec.y_data else []
                ys = self._nums(spec.y_data[1]) if len(spec.y_data) > 1 else []
                sizes = self._nums(spec.y_data[2]) if len(spec.y_data) > 2 else []
                n = len(spec.x_data) or max(len(xs), len(ys))
                xs = (xs + [0.0] * n)[:n]
                ys = (ys + [0.0] * n)[:n]
                sizes = (sizes + [0.0] * n)[:n] if sizes else [1.0] * n
                # Scale to point-AREA, matching the Plotly path's
                # `sizemode="area"`. matplotlib's `s` is already an area in
                # points squared, so this is a linear scale — not a square.
                peak = max([abs(s) for s in sizes] + [1.0])
                areas = [80 + 2600 * (abs(s) / peak) for s in sizes]
                point_colors = colors[: len(xs)] if len(colors) >= len(xs) else colors[0]
                ax.scatter(xs, ys, s=areas, c=point_colors, alpha=0.75,
                           edgecolors=text_color, linewidths=0.5)
                # strict=False here, unlike the padded rows elsewhere in this
                # fix: `xs`/`ys` are padded to `n`, which is derived from
                # `len(spec.x_data) or max(len(xs), len(ys))`. When x_data is
                # empty the labels are shorter by construction and annotating
                # only the points that have names is the intended behaviour.
                for label, x_val, y_val in zip(spec.x_data, xs, ys, strict=False):
                    ax.annotate(str(label), (x_val, y_val), fontsize=8, color=text_color,
                                ha="center", va="bottom")
                if chart_type == "growth_share":
                    ax.axvline(1.0, color=CHART_COLORS[3], linewidth=1, linestyle=":")
                    if ys:
                        ax.axhline((max(ys) + min(ys)) / 2, color=CHART_COLORS[3],
                                   linewidth=1, linestyle=":")
                    ax.invert_xaxis()  # High relative share on the left

            elif chart_type == "marimekko":
                # Width-weighted columns on a continuous axis — the defining
                # property. Falling back to a plain stacked bar would silently
                # discard the width dimension, i.e. half the exhibit.
                labels = [str(x) for x in spec.x_data]
                widths = self._nums(spec.y_data[0]) if spec.y_data else []
                widths = (widths + [1.0] * len(labels))[: len(labels)]
                widths = [w if w > 0 else 1.0 for w in widths]
                segments = [self._nums(r) for r in spec.y_data[1:]] or [[100.0] * len(labels)]
                centers, running = [], 0.0
                for w in widths:
                    centers.append(running + w / 2)
                    running += w
                bottoms = [0.0] * len(labels)
                seg_names = spec.series_names[1:] if len(spec.series_names) > 1 else []
                for i, row in enumerate(segments):
                    row = (row + [0.0] * len(labels))[: len(labels)]
                    ax.bar(centers, row, width=widths, bottom=bottoms,
                           color=colors[i % len(colors)],
                           label=seg_names[i] if i < len(seg_names) else f"Segment {i + 1}",
                           edgecolor=bg_color, linewidth=1)
                    bottoms = [b + v for b, v in zip(bottoms, row, strict=True)]
                ax.set_xticks(centers)
                ax.set_xticklabels([f"{lab}\n{w:g}" for lab, w in zip(labels, widths, strict=True)])

            elif chart_type == "pie":
                values = self._nums(spec.y_data[0]) if spec.y_data else []
                labels = [str(x) for x in spec.x_data]
                if any(v > 0 for v in values):
                    ax.pie(values, labels=labels,
                           colors=colors[: len(values)] if len(colors) >= len(values) else None,
                           autopct="%1.0f%%", textprops=dict(color=text_color, fontsize=9))
                    ax.set_aspect("equal")

            else:
                # Default to bar for unsupported types in matplotlib fallback
                for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
                    ax.bar(spec.x_data, y_values, color=colors[i % len(colors)], label=name)

            # Brand styling
            ax.set_title(spec.title, fontsize=16, color=text_color, fontweight="normal", pad=15)
            ax.set_xlabel(spec.x_label, fontsize=11, color=text_color)
            ax.set_ylabel(spec.y_label, fontsize=11, color=text_color)
            ax.tick_params(colors=text_color, labelsize=9)
            # A pie has no axes to grid or to frame; drawing them produces a
            # box of gridlines around the circle, which is chartjunk.
            if chart_type != "pie":
                ax.grid(True, color=grid_color, linewidth=0.5, alpha=0.7)
                ax.spines["top"].set_visible(False)
                ax.spines["right"].set_visible(False)
                ax.spines["left"].set_color(grid_color)
                ax.spines["bottom"].set_color(grid_color)
            else:
                ax.axis("off")

            if len(spec.series_names) > 1:
                ax.legend(fontsize=9, facecolor=bg_color, edgecolor=grid_color)

            if spec.source:
                fig.text(0.5, 0.01, f"Source: {spec.source}", ha="center", fontsize=7, color="#8B8680")

            fig.tight_layout()

            safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in spec.title.lower())[:50]
            output_path = str(self._output_dir / f"{safe_title}_mpl.png")
            fig.savefig(output_path, dpi=300, facecolor=bg_color, bbox_inches="tight")
            plt.close(fig)

            result.image_path = output_path
            result.success = True
            result.width = spec.width or self.DEFAULT_WIDTH
            result.height = spec.height or self.DEFAULT_HEIGHT
            result.dpi = 300
            return result

        except (ImportError, ValueError, RuntimeError, OSError) as e:
            result.error = f"matplotlib fallback failed: {e}"
            return result

    def _generate_data_table(self, spec: ChartSpec) -> ChartResult:
        """D26: Generate a styled HTML data table as the final fallback.

        When both Plotly/kaleido and matplotlib fail, render the chart data
        as a clean HTML table with brand styling. Never blank.
        """
        result = ChartResult(spec=spec)

        try:
            safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in spec.title.lower())[:50]
            output_path = str(self._output_dir / f"{safe_title}_table.html")

            # Build HTML table with brand styling
            html_parts = [
                '<div class="chart-data-table" style="font-family: Source Sans 3, sans-serif; background: #F5F4EE; padding: 1cm; border: 1px solid #E8E6DD;">',
                f'<h3 style="font-family: Instrument Serif, serif; color: #1A1A1A; margin: 0 0 0.5cm 0;">{spec.title}</h3>',
                '<table style="width: 100%; border-collapse: collapse; font-family: JetBrains Mono, monospace; font-size: 9pt;">',
            ]

            # Header row
            header_cells = [f'<th style="background: #3D3530; color: #F5F4EE; padding: 6px 10px; text-align: left;">{spec.x_label or "Category"}</th>']
            for name in spec.series_names:
                header_cells.append(f'<th style="background: #3D3530; color: #F5F4EE; padding: 6px 10px; text-align: right;">{name}</th>')
            html_parts.append("<tr>" + "".join(header_cells) + "</tr>")

            # Data rows
            for row_idx, x_val in enumerate(spec.x_data):
                row_cells = [f'<td style="padding: 6px 10px; border-bottom: 1px solid #E8E6DD; color: #1A1A1A;">{x_val}</td>']
                for series_idx in range(len(spec.series_names)):
                    y_values = spec.y_data[series_idx] if series_idx < len(spec.y_data) else []
                    val = y_values[row_idx] if row_idx < len(y_values) else ""
                    row_cells.append(f'<td style="padding: 6px 10px; border-bottom: 1px solid #E8E6DD; text-align: right; color: #1A1A1A;">{val}</td>')
                bg = ' style="background: #F5F4EE;"' if row_idx % 2 == 0 else ""
                html_parts.append(f"<tr{bg}>" + "".join(row_cells) + "</tr>")

            html_parts.append("</table>")

            if spec.source:
                html_parts.append(f'<p style="font-family: Source Sans 3, sans-serif; font-size: 8pt; color: #8B8680; margin-top: 0.3cm;">Source: {spec.source}</p>')

            html_parts.append("</div>")

            with open(output_path, "w", encoding="utf-8") as f:
                f.write("\n".join(html_parts))

            result.image_path = output_path
            result.success = True
            result.width = spec.width or self.DEFAULT_WIDTH
            result.height = spec.height or self.DEFAULT_HEIGHT
            result.dpi = 300
            return result

        except (OSError, ValueError, RuntimeError) as e:
            result.error = f"Data table fallback failed: {e}"
            return result

    def generate(self, spec: ChartSpec) -> ChartResult:
        """Generate a chart from a specification.

        D26: Three-tier fallback strategy:
        1. Plotly + kaleido (preferred — interactive quality, scale=3 for 300 DPI)
        2. matplotlib (headless fallback — same brand colors, Agg backend)
        3. Styled HTML data table (final fallback — never blank)

        Args:
            spec: Chart specification with data, labels, and styling info.

        Returns:
            ChartResult with the generated chart image path.
        """
        # Tier 1: Plotly + kaleido
        try:
            go, pio = self._import_plotly()
            result = ChartResult(spec=spec)

            creator = self._get_chart_creator(spec.chart_type)
            fig = creator(spec, go)
            fig = self._apply_brand_styling(fig, spec)
            fig.update_layout(
                width=spec.width or self.DEFAULT_WIDTH,
                height=spec.height or self.DEFAULT_HEIGHT,
            )

            safe_title = "".join(c if c.isalnum() or c in "-_" else "_" for c in spec.title.lower())[:50]
            output_path = str(self._output_dir / f"{safe_title}.png")

            pio.write_image(
                fig,
                output_path,
                format=self.EXPORT_FORMAT,
                scale=self.EXPORT_SCALE,
                width=spec.width or self.DEFAULT_WIDTH,
                height=spec.height or self.DEFAULT_HEIGHT,
            )

            result.image_path = output_path
            result.success = True
            result.width = spec.width or self.DEFAULT_WIDTH
            result.height = spec.height or self.DEFAULT_HEIGHT
            result.dpi = 300
            return result

        except (ValueError, RuntimeError, OSError, ImportError, AttributeError, TypeError, KeyError) as plotly_err:
            # Broadened from (ValueError, RuntimeError, OSError, ImportError):
            # a plain `fig.update_yaxis()` typo (AttributeError) previously
            # propagated straight past this handler and crashed the whole
            # chart pipeline instead of degrading to Tier 2/3. Defense in
            # depth — Tier 1 should degrade on any of its own coding errors,
            # not just the environment-level errors originally anticipated.
            # Tier 2: matplotlib fallback
            mpl_result = self._generate_matplotlib(spec)
            if mpl_result.success:
                return mpl_result

            # Tier 3: styled HTML data table — never blank
            table_result = self._generate_data_table(spec)
            if table_result.success:
                return table_result

            # All tiers failed — return error result
            return ChartResult(
                spec=spec,
                error=f"All chart tiers failed. Plotly: {plotly_err}. matplotlib: {mpl_result.error}. Table: {table_result.error}",
            )

    def generate_batch(self, specs: list[ChartSpec]) -> list[ChartResult]:
        """Generate multiple charts.

        Args:
            specs: List of chart specifications.

        Returns:
            List of ChartResult objects, one per spec (in same order).

        Releases the renderer once the batch is done. Kaleido reserves ~311 MB
        for its Chromium tree and never gives it back on its own, so holding it
        past the last chart is what starves the PDF render that follows.
        Deliberately released *after* the loop, not per chart: respawning costs
        ~1.5 s versus ~0.15 s for a warm export, so per-chart shutdown would
        make a 10-exhibit report ~15 s slower for no benefit.
        """
        try:
            return [self.generate(spec) for spec in specs]
        finally:
            self.release_renderer()

    @staticmethod
    def release_renderer() -> bool:
        """Terminate the kaleido/Chromium subprocess tree. Returns True if released.

        Kaleido starts a Chromium process tree on the first `to_image` call and
        keeps it alive for the life of the interpreter. Measured cost on this
        box: **35 MB / 6 procs → 311 MB / 13 procs** after a single export, and
        it does *not* shrink afterwards (11 exports → 353 MB). Export latency
        stays flat at ~0.15 s for 60 consecutive exports, so this is a
        steady-state memory reservation, not a leak that grows per chart.

        That reservation is the whole problem on a memory-constrained host. A
        985 MB container with swap already full has ~370 MB available; kaleido
        claims 311 MB of it and holds it. The next allocation — another test
        module, a PDF render, an LLM client — pushes the box into swap thrash,
        and kaleido's own pipe read is what appears to hang. The symptom
        (`kaleido/scopes/base.py:308` blocking forever) points at kaleido, but
        the cause is whatever else needed the memory kaleido was still holding.

        `_shutdown_kaleido` is private in kaleido 0.2.x — there is no public
        equivalent, which is why it is called defensively here and why the
        version pin in `test_chart_export_smoke.py` matters. Verified: after
        shutdown the tree returns to 35 MB / 6 procs, and the *next* export
        transparently respawns it and succeeds (86,786 B PNG). So this is safe
        to call at any point, including between charts.
        """
        try:
            import plotly.io as pio

            scope = getattr(pio, "kaleido", None)
            scope = getattr(scope, "scope", None)
            shutdown = getattr(scope, "_shutdown_kaleido", None)
            if shutdown is None:
                return False
            shutdown()
            return True
        except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
            # Releasing memory must never be the reason a report fails.
            return False

    async def close(self) -> None:
        """Release the renderer subprocess tree.

        Previously `pass`, which is why `async with ChartGenerator()` and the
        `finally: await close()` call sites freed nothing: the 311 MB Chromium
        tree outlived every generator that started it.
        """
        self.release_renderer()

    async def __aenter__(self) -> ChartGenerator:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
