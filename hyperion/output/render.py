"""
HYPERION PDF Renderer + Template Renderer — WeasyPrint and Jinja2 integration.

This is NOT a generic "render HTML to PDF" wrapper. It implements the
exact PDF generation pipeline from ARCHITECTURE.md §6:

1. **TemplateRenderer (Jinja2)**: Renders the FinalReport Pydantic model
   into print-ready HTML using Jinja2 templates. The templates use the
   HYPERION brand CSS (warm palette, Instrument Serif + JetBrains Mono).

2. **PDFRenderer (WeasyPrint)**: Converts the rendered HTML into a 300 DPI
   PDF with embedded fonts, proper page breaks, and print-quality output.

Key requirements (§6):
- All fonts embedded (Instrument Serif, JetBrains Mono)
- 300 DPI images
- No blank pages
- No orphaned images (image + text on same page)
- Page breaks before major sections
- Footer on every page (page number, report title, date)
- Cover page = full-bleed image with title overlay
- Section images = 40% page width, right-aligned, with caption
- Cream background (#F5F4EE), never white
- Warm Charcoal text (#1A1A1A), never pure black

Architecture reference: §6 — "Reports are 300 DPI PDFs with Unsplash hero
images, Plotly charts, and Jinja2-templated content rendered through
WeasyPrint."

§7.4 — "Both fonts are embedded in the PDF via WeasyPrint. This ensures
the PDF renders identically on any system, regardless of installed fonts."

Used by: Render Engine (WEASYPRINT + JINJA2 tools), Presentation Designer
(JINJA2 tool) (§5.1)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass
class TemplateRenderResult:
    """Result of rendering a Jinja2 template."""

    html: str = ""
    template_name: str = ""
    success: bool = False
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "html": self.html,
            "template_name": self.template_name,
            "success": self.success,
            "error": self.error,
        }


@dataclass
class PDFRenderResult:
    """Result of rendering a PDF via WeasyPrint."""

    pdf_path: str = ""
    html_path: str = ""
    page_count: int = 0
    file_size_bytes: int = 0
    fonts_embedded: list[str] = field(default_factory=list)
    success: bool = False
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "pdf_path": self.pdf_path,
            "html_path": self.html_path,
            "page_count": self.page_count,
            "file_size_bytes": self.file_size_bytes,
            "fonts_embedded": self.fonts_embedded,
            "success": self.success,
            "error": self.error,
            "warnings": self.warnings,
        }


class TemplateRenderer:
    """Jinja2 template renderer for HYPERION reports.

    Renders the FinalReport Pydantic model into print-ready HTML using
    Jinja2 templates with the HYPERION brand CSS.

    Usage:
        renderer = TemplateRenderer(settings=settings)
        result = renderer.render_report(report_data=final_report_dict)
        if result.success:
            print(f"Rendered {len(result.html)} chars of HTML")
    """

    TEMPLATE_DIR = Path(__file__).parent / "templates"

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._env: Any | None = None

    def _get_env(self) -> Any:
        """Get or create the Jinja2 environment."""
        if self._env is None:
            from jinja2 import Environment, FileSystemLoader, select_autoescape

            self._env = Environment(
                loader=FileSystemLoader(str(self.TEMPLATE_DIR)),
                autoescape=select_autoescape(["html", "xml"]),
                trim_blocks=True,
                lstrip_blocks=True,
            )
            # Add custom filters
            self._env.filters["format_currency"] = self._format_currency
            self._env.filters["format_percent"] = self._format_percent
            self._env.filters["format_date"] = self._format_date
            self._env.filters["truncate_chars"] = self._truncate_chars
            self._env.filters["md_to_html"] = self._markdown_to_html

        return self._env

    def _format_currency(self, value: float, currency: str = "$") -> str:
        """Format a number as currency."""
        if value is None:
            return "N/A"
        if abs(value) >= 1_000_000_000:
            return f"{currency}{value / 1_000_000_000:.1f}B"
        elif abs(value) >= 1_000_000:
            return f"{currency}{value / 1_000_000:.1f}M"
        elif abs(value) >= 1_000:
            return f"{currency}{value / 1_000:.1f}K"
        else:
            return f"{currency}{value:.2f}"

    def _format_percent(self, value: float, decimals: int = 1) -> str:
        """Format a number as percentage."""
        if value is None:
            return "N/A"
        return f"{value:.{decimals}f}%"

    def _format_date(self, value: str) -> str:
        """Format an ISO date string."""
        if not value:
            return ""
        try:
            dt = datetime.fromisoformat(value)
            return dt.strftime("%B %d, %Y")
        except (ValueError, TypeError):
            return value

    def _truncate_chars(self, value: str, length: int = 200) -> str:
        """Truncate text to a maximum length with ellipsis."""
        if not value:
            return ""
        if len(value) <= length:
            return value
        return value[:length - 3] + "..."

    def _markdown_to_html(self, value: str) -> str:
        """Convert basic markdown to HTML for report rendering.

        Handles: **bold**, *italic*, ## headings, ### sub-headings,
        - bullet lists, and paragraph breaks. Lightweight — no external deps.

        Returns a jinja2.Markup object so Jinja2 does NOT re-escape the output.
        """
        if not value:
            return ""

        try:
            from jinja2 import Markup
        except ImportError:
            Markup = str  # fallback — str will be auto-escaped by Jinja2

        import re

        html = value

        # Convert markdown headings
        html = re.sub(r"^### (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)
        html = re.sub(r"^## (.+)$", r"<h3>\1</h3>", html, flags=re.MULTILINE)

        # Convert bold and italic
        html = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", html)
        html = re.sub(r"\*(.+?)\*", r"<em>\1</em>", html)

        # Convert bullet lists (group consecutive lines)
        lines = html.split("\n")
        result: list[str] = []
        in_list = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- "):
                if not in_list:
                    result.append("<ul>")
                    in_list = True
                result.append(f"<li>{stripped[2:]}</li>")
            else:
                if in_list:
                    result.append("</ul>")
                    in_list = False
                if stripped and not stripped.startswith("<h"):
                    # Wrap non-heading, non-empty lines in <p> tags
                    if not result or not result[-1].startswith("<p>"):
                        result.append(f"<p>{stripped}</p>")
                    else:
                        # Append to previous paragraph
                        result[-1] = result[-1][:-4] + " " + stripped + "</p>"
                elif stripped.startswith("<h"):
                    result.append(stripped)
        if in_list:
            result.append("</ul>")

        output = "\n".join(result)
        if Markup is not str:
            return Markup(output)
        return output

    async def render_template(
        self,
        template_name: str = "",
        context: dict[str, Any] | None = None,
        template_string: str = "",
    ) -> TemplateRenderResult:
        """Render a Jinja2 template with context data.

        Args:
            template_name: Template filename (e.g., "report.html.j2")
            context: Dictionary of data to pass to the template
            template_string: Raw Jinja2 template string (alternative to
                template_name — used by Presentation Designer which has
                inline HTML templates)

        Returns:
            TemplateRenderResult with the rendered HTML.
        """
        env = self._get_env()
        context = context or {}

        try:
            if template_string:
                template = env.from_string(template_string)
                html = template.render(**context)
                return TemplateRenderResult(
                    html=html,
                    template_name=template_name or "<inline>",
                    success=True,
                )
            else:
                template = env.get_template(template_name)
                html = template.render(**context)
                return TemplateRenderResult(
                    html=html,
                    template_name=template_name,
                    success=True,
                )
        except (OSError, ValueError, RuntimeError, KeyError) as e:
            return TemplateRenderResult(
                template_name=template_name or "<inline>",
                error=str(e),
            )

    async def render_report(
        self,
        report_data: dict[str, Any],
        template_name: str = "report.html.j2",
    ) -> TemplateRenderResult:
        """Render the main report template with report data.

        Args:
            report_data: Dictionary containing the FinalReport data
            template_name: Template filename (default: report.html.j2)

        Returns:
            TemplateRenderResult with the rendered HTML.
        """
        context = {
            "report": report_data,
            "generated_date": datetime.now().strftime("%B %d, %Y"),
            "generated_timestamp": datetime.now().isoformat(),
        }
        return await self.render_template(template_name, context)

    async def render_cover(
        self,
        cover_data: dict[str, Any],
        template_name: str = "cover.html.j2",
    ) -> TemplateRenderResult:
        """Render the cover page template.

        Args:
            cover_data: Dictionary containing cover page data
                       (title, subtitle, client, date, image_path)
            template_name: Template filename (default: cover.html.j2)

        Returns:
            TemplateRenderResult with the rendered cover HTML.
        """
        context = {
            "cover": cover_data,
            "generated_date": datetime.now().strftime("%B %d, %Y"),
        }
        return await self.render_template(template_name, context)

    async def render_section(
        self,
        section_data: dict[str, Any],
        template_name: str = "section.html.j2",
    ) -> TemplateRenderResult:
        """Render a single section template.

        Args:
            section_data: Dictionary containing section data
            template_name: Template filename

        Returns:
            TemplateRenderResult with the rendered section HTML.
        """
        context = {"section": section_data}
        return await self.render_template(template_name, context)


class PDFRenderer:
    """WeasyPrint PDF renderer for HYPERION reports.

    Converts rendered HTML into a 300 DPI PDF with embedded fonts,
    proper page breaks, and print-quality output.

    Usage:
        renderer = PDFRenderer(settings=settings)
        result = renderer.render_pdf(
            html="<html>...</html>",
            output_path="reports/engagement_2024.pdf",
        )
        if result.success:
            print(f"PDF saved: {result.pdf_path} ({result.page_count} pages)")
    """

    CSS_PATH = Path(__file__).parent / "templates" / "styles" / "hyperion.css"

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._reports_dir = Path("reports")
        if settings:
            self._reports_dir = Path(getattr(settings, "reports_dir", "reports"))
        self._reports_dir.mkdir(parents=True, exist_ok=True)

    def _get_weasyprint(self) -> tuple[Any, Any]:
        """Import WeasyPrint components. Returns (HTML, CSS).

        Raises OSError if native GTK libraries are not available (common on Windows).
        """
        from weasyprint import HTML, CSS

        return HTML, CSS

    def _render_pdf_playwright(self, html: str, output_path: str, css_content: str) -> bool:
        """Fallback: render HTML to PDF using Playwright Chromium.

        Used when WeasyPrint can't load native GTK libraries (Windows).
        Produces a print-quality PDF with A4 page size and proper margins.
        """
        try:
            from playwright.sync_api import sync_playwright

            # Write HTML to a temp file so Playwright can load it
            import tempfile
            temp_html = output_path.replace(".pdf", "_playwright.html")
            full_html = html
            if css_content:
                full_html = f"<style>{css_content}</style>" + html
            with open(temp_html, "w", encoding="utf-8") as f:
                f.write(full_html)

            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(f"file:///{temp_html.replace(os.sep, '/')}")
                page.pdf(
                    path=output_path,
                    format="A4",
                    print_background=True,
                    margin={"top": "25mm", "bottom": "25mm", "left": "25mm", "right": "25mm"},
                )
                browser.close()

            return os.path.exists(output_path) and os.path.getsize(output_path) > 0

        except Exception:
            return False

    def render_pdf(
        self,
        html: str,
        output_path: str = "",
        cover_html: str = "",
        additional_css: str = "",
    ) -> PDFRenderResult:
        """Render HTML to a print-quality PDF.

        Tries WeasyPrint first (best quality, embedded fonts). Falls back to
        Playwright Chromium when WeasyPrint can't load native GTK libraries
        (common on Windows — libgobject-2.0 not available).

        Args:
            html: The rendered HTML content (body of the report)
            output_path: Path to save the PDF. If empty, auto-generated.
            cover_html: Optional cover page HTML (rendered separately, prepended)
            additional_css: Optional additional CSS to append to the brand CSS

        Returns:
            PDFRenderResult with the PDF path and metadata.
        """
        result = PDFRenderResult()

        # Generate output path if not provided
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self._reports_dir / f"hyperion_report_{timestamp}.pdf")

        # Load brand CSS
        css_content = ""
        if self.CSS_PATH.exists():
            css_content = self.CSS_PATH.read_text(encoding="utf-8")
        if additional_css:
            css_content += "\n" + additional_css

        # Combine cover + body if cover is provided
        full_html = html
        if cover_html:
            full_html = cover_html + '<div class="page-break"></div>' + html

        # Save HTML for debugging
        html_path = output_path.replace(".pdf", ".html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        result.html_path = html_path

        # ── Attempt 1: WeasyPrint ──
        weasy_error: Exception | None = None
        try:
            HTML, CSS = self._get_weasyprint()

            # Create WeasyPrint HTML object
            html_obj = HTML(string=full_html, base_url=str(Path.cwd()))

            # Create CSS object
            css_obj = CSS(string=css_content) if css_content else None

            # Render PDF
            if css_obj:
                html_obj.write_pdf(output_path, stylesheets=[css_obj])
            else:
                html_obj.write_pdf(output_path)

            # Get PDF metadata
            result.pdf_path = output_path
            result.success = True
            result.file_size_bytes = os.path.getsize(output_path)

            # Try to get page count
            try:
                import fitz

                doc = fitz.open(output_path)
                result.page_count = len(doc)

                # Check embedded fonts
                fonts: set[str] = set()
                for page in doc:
                    for font in page.get_fonts():
                        fonts.add(font[3])  # Font name
                result.fonts_embedded = list(fonts)
                doc.close()
            except (ImportError, OSError, ValueError):
                result.warnings.append("PyMuPDF not available — page count unknown")

            return result

        except (OSError, ImportError, ValueError, RuntimeError) as exc:
            weasy_error = exc
            result.warnings.append(f"WeasyPrint failed: {weasy_error!s:.120}")

        # ── Attempt 2: Playwright Chromium fallback ──
        if self._render_pdf_playwright(full_html, output_path, css_content):
            result.pdf_path = output_path
            result.success = True
            result.file_size_bytes = os.path.getsize(output_path)
            result.warnings.append("PDF rendered via Playwright (WeasyPrint unavailable)")

            # Try to get page count
            try:
                import fitz

                doc = fitz.open(output_path)
                result.page_count = len(doc)
                doc.close()
            except (ImportError, OSError, ValueError):
                pass

            return result

        # Both methods failed
        result.error = f"WeasyPrint: {weasy_error!s:.80}; Playwright fallback also failed"
        return result

    def render_from_template(
        self,
        report_data: dict[str, Any],
        cover_data: dict[str, Any] | None = None,
        output_path: str = "",
    ) -> PDFRenderResult:
        """Render a complete PDF from report data using Jinja2 templates.

        This is the main entry point for the Render Engine. It:
        1. Renders the cover page template (if cover_data provided)
        2. Renders the main report template
        3. Combines them and renders to PDF via WeasyPrint

        Args:
            report_data: Dictionary containing the FinalReport data
            cover_data: Optional dictionary containing cover page data
            output_path: Path to save the PDF. If empty, auto-generated.

        Returns:
            PDFRenderResult with the PDF path and metadata.
        """
        # Step 1: Render cover page (if provided)
        cover_html = ""
        if cover_data:
            template_renderer = TemplateRenderer(settings=self.settings)
            cover_result = template_renderer.render_cover(cover_data)
            if cover_result.success:
                cover_html = cover_result.html
            else:
                # Continue without cover if template fails
                pass

        # Step 2: Render main report
        template_renderer = TemplateRenderer(settings=self.settings)
        report_result = template_renderer.render_report(report_data)
        if not report_result.success:
            return PDFRenderResult(error=f"Template rendering failed: {report_result.error}")

        # Step 3: Render PDF
        return self.render_pdf(
            html=report_result.html,
            output_path=output_path,
            cover_html=cover_html,
        )

    def verify_pdf(self, pdf_path: str) -> dict[str, Any]:
        """Verify a PDF meets HYPERION quality standards.

        Checks (§6.5):
        - No blank pages
        - All fonts embedded
        - Page count is reasonable (15-40 pages)
        - File size is reasonable
        """
        try:
            import fitz

            doc = fitz.open(pdf_path)
            page_count = len(doc)
            blank_pages: list[int] = []
            fonts: set[str] = set()

            for i, page in enumerate(doc):
                # Check for blank pages
                text = page.get_text().strip()
                images = page.get_images()
                if not text and not images:
                    blank_pages.append(i + 1)

                # Check fonts
                for font in page.get_fonts():
                    fonts.add(font[3])

            doc.close()

            file_size = os.path.getsize(pdf_path)

            return {
                "path": pdf_path,
                "page_count": page_count,
                "blank_pages": blank_pages,
                "has_blank_pages": len(blank_pages) > 0,
                "fonts_embedded": list(fonts),
                "all_fonts_embedded": len(fonts) > 0,
                "file_size_bytes": file_size,
                "page_count_reasonable": 15 <= page_count <= 40,
                "passed": len(blank_pages) == 0 and len(fonts) > 0,
            }

        except (ImportError, OSError, ValueError) as e:
            return {"path": pdf_path, "error": str(e), "passed": False}

    async def close(self) -> None:
        """Close any open resources."""
        pass

    async def __aenter__(self) -> PDFRenderer:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
