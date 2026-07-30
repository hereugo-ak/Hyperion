"""Output — PDF render, charts, image processing, markdown export, templates.

This module contains the complete output pipeline for HYPERION reports:
- ImageProcessor: Pillow pipeline (resize, crop, warm filter, sharpen, PNG 300 DPI)
- ChartGenerator: Plotly charts with brand colors, Tufte principles, 300 DPI export
- PDFRenderer: WeasyPrint PDF generation with font embedding and verification
- TemplateRenderer: Jinja2 template rendering for report and cover pages
- MarkdownExporter: Structured markdown export for TUI display

All output uses the HYPERION brand palette (warm, earthy, premium) and
typography (Instrument Serif for headers, JetBrains Mono for body).
No blue. No purple. No AI slop. (§7)
"""

from hyperion.output.charts import CHART_COLORS, ChartGenerator, ChartResult, ChartSpec
from hyperion.output.images import ImageProcessor, ImageProcessResult, ImageTooSmallError
from hyperion.output.markdown import MarkdownExporter, MarkdownExportResult
from hyperion.output.render import (
    PDFRenderer,
    PDFRenderResult,
    TemplateRenderer,
    TemplateRenderResult,
)

__all__ = [
    # Image processing
    "ImageProcessor",
    "ImageProcessResult",
    "ImageTooSmallError",
    # Charts
    "ChartGenerator",
    "ChartSpec",
    "ChartResult",
    "CHART_COLORS",
    # PDF rendering
    "PDFRenderer",
    "PDFRenderResult",
    # Template rendering
    "TemplateRenderer",
    "TemplateRenderResult",
    # Markdown export
    "MarkdownExporter",
    "MarkdownExportResult",
]
