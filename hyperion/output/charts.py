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

    chart_type: str = "bar"  # bar, line, scatter, histogram, stacked_bar, treemap, sankey, heatmap, radar, waterfall
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
        if spec.chart_type in ("bar", "stacked_bar"):
            fig.update_yaxis(rangemode="tozero")

        # No 3D effects, no gradient fills (Tufte)
        fig.update_traces(
            marker_line_width=0,  # No bar outlines
            opacity=0.95,
        )

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
        colors = self._get_colors(spec)

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

    def _get_chart_creator(self, chart_type: str) -> Any:
        """Get the chart creation method for a chart type."""
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
            else:
                # Default to bar for unsupported types in matplotlib fallback
                for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):
                    ax.bar(spec.x_data, y_values, color=colors[i % len(colors)], label=name)

            # Brand styling
            ax.set_title(spec.title, fontsize=16, color=text_color, fontweight="normal", pad=15)
            ax.set_xlabel(spec.x_label, fontsize=11, color=text_color)
            ax.set_ylabel(spec.y_label, fontsize=11, color=text_color)
            ax.tick_params(colors=text_color, labelsize=9)
            ax.grid(True, color=grid_color, linewidth=0.5, alpha=0.7)
            ax.spines["top"].set_visible(False)
            ax.spines["right"].set_visible(False)
            ax.spines["left"].set_color(grid_color)
            ax.spines["bottom"].set_color(grid_color)

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

        except (ValueError, RuntimeError, OSError, ImportError) as plotly_err:
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
        """
        return [self.generate(spec) for spec in specs]

    async def close(self) -> None:
        """Close any open resources."""
        pass

    async def __aenter__(self) -> ChartGenerator:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
