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

NOTE (fix 3.3 — dead-template fork collapsed): There is exactly ONE report
template system — the inline `HTML_TEMPLATE` / `CSS_TEMPLATE` in
`presentation_designer.py`. The former parallel system
(`templates/report.html.j2`, `cover.html.j2`, `styles/hyperion.css`) was
never shipped: `render_pdf` secretly layered the dead `hyperion.css` over
the shipped inline CSS, and any fix applied to the `.j2` files had zero
effect on output. Those files, the `FileSystemLoader` that served them,
`_embed_fonts_in_css`, and the sync/async-broken `render_from_template`
have been removed. Font embedding now lives where the shipped CSS lives:
base64 `@font-face` data-URIs injected into `CSS_TEMPLATE` (fix 3.2).
"""

from __future__ import annotations

import contextlib
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
    """Jinja2 template-string renderer for HYPERION reports.

    Renders the shipped inline `HTML_TEMPLATE` (from
    `presentation_designer.py`) with report context data. This is a
    STRING renderer only — fix 3.3 removed the FileSystemLoader and the
    dead `templates/*.j2` files so there can be no second, diverging
    template system. `env.get_template` deliberately does not exist here:
    the Environment has no loader, so any attempt to render by file name
    fails loudly (TemplateNotFound is unreachable; there are no files).

    Usage:
        renderer = TemplateRenderer(settings=settings)
        result = await renderer.render_template(
            template_string=HTML_TEMPLATE, context={...}
        )
        if result.success:
            print(f"Rendered {len(result.html)} chars of HTML")
    """

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._env: Any | None = None

    def _get_env(self) -> Any:
        """Get or create the Jinja2 environment (no loader — strings only)."""
        if self._env is None:
            from jinja2 import Environment, select_autoescape

            self._env = Environment(
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
            self._env.filters["clean_dict_repr"] = self._clean_dict_repr

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

    def _clean_dict_repr(self, value: Any) -> str:
        """Clean up raw dict/list reprs that leak into report text.

        When the synthesis lead or specialist agents put a Pydantic model's
        repr() or a dict's str() into a text field, it shows up in the report
        as ``{'recommendation': 'BUY', 'time_to_market_build': 'Unknown', ...}``.
        This filter extracts readable key-value pairs from such strings and
        formats them as ``Key: Value`` lines. If the value is already clean
        text, it passes through unchanged.
        """
        import re as _re
        if value is None:
            return ""
        text = str(value)
        # Detect dict repr pattern: starts with { and contains 'key': 'value'
        if text.strip().startswith("{") and "'" in text:
            # Try to parse as JSON-like dict string
            try:
                # Replace single quotes with double quotes for JSON parsing
                json_str = text.replace("'", '"')
                import json as _json
                data = _json.loads(json_str)
                lines = []
                for k, v in data.items():
                    # Make key readable: replace underscores with spaces, title case
                    readable_key = k.replace("_", " ").title()
                    lines.append(f"{readable_key}: {v}")
                return " · ".join(lines)
            except (ValueError, TypeError):
                pass
            # Fallback: regex extract key-value pairs
            pairs = _re.findall(r"'([\w_]+)':\s*'([^']*)'", text)
            if pairs:
                lines = []
                for k, v in pairs:
                    readable_key = k.replace("_", " ").title()
                    lines.append(f"{readable_key}: {v}")
                return " · ".join(lines)
            # If we can't extract pairs, just truncate the raw repr
            if len(text) > 200:
                return text[:197] + "..."
        return text

    def _markdown_to_html(self, value: str) -> str:
        """Convert basic markdown to HTML for report rendering.

        Handles: **bold**, *italic*, ## headings, ### sub-headings,
        - bullet lists, and paragraph breaks. Lightweight — no external deps.

        Returns a markupsafe.Markup object so Jinja2 does NOT re-escape the output.
        """
        if not value:
            return ""

        try:
            from markupsafe import Markup
        except ImportError:
            try:
                from jinja2 import Markup  # deprecated fallback for old jinja2
            except ImportError:
                Markup = str  # noqa: N806 — fallback; str will be auto-escaped by Jinja2

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
        in_paragraph = False
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("- "):
                if in_paragraph:
                    result.append("</p>")
                    in_paragraph = False
                if not in_list:
                    result.append("<ul>")
                    in_list = True
                result.append(f"<li>{stripped[2:]}</li>")
            else:
                if in_list:
                    result.append("</ul>")
                    in_list = False
                if stripped and not stripped.startswith("<h"):
                    # Empty line breaks the current paragraph
                    if not in_paragraph:
                        result.append(f"<p>{stripped}")
                        in_paragraph = True
                    else:
                        result.append(stripped)
                elif stripped.startswith("<h"):
                    if in_paragraph:
                        result.append("</p>")
                        in_paragraph = False
                    result.append(stripped)
                else:
                    # Empty line — close current paragraph
                    if in_paragraph:
                        result.append("</p>")
                        in_paragraph = False
        if in_list:
            result.append("</ul>")
        if in_paragraph:
            result.append("</p>")

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
        """Render a Jinja2 template string with context data.

        Args:
            template_name: Label for diagnostics only (e.g. "<inline>").
                Kept for caller compatibility; no file is ever loaded.
            context: Dictionary of data to pass to the template.
            template_string: Raw Jinja2 template string — the only source
                of templates (fix 3.3: the dead templates/*.j2 fork is gone).

        Returns:
            TemplateRenderResult with the rendered HTML.
        """
        env = self._get_env()
        context = context or {}

        if not template_string:
            # Loud, explicit failure — the old code silently fell through to
            # env.get_template() against a FileSystemLoader of dead files.
            return TemplateRenderResult(
                template_name=template_name or "<inline>",
                error="render_template requires template_string; file-based "
                "templates were removed in fix 3.3 (dead-template fork)",
            )

        try:
            template = env.from_string(template_string)
            html = template.render(**context)
            return TemplateRenderResult(
                html=html,
                template_name=template_name or "<inline>",
                success=True,
            )
        except (OSError, ValueError, RuntimeError, KeyError) as e:
            return TemplateRenderResult(
                template_name=template_name or "<inline>",
                error=str(e),
            )


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

    NOTE (fix 3.3): this renderer carries NO brand CSS of its own. The
    former `CSS_PATH -> templates/styles/hyperion.css` was the dead fork —
    render_pdf() silently layered it over the shipped inline CSS so a fix
    to that file could fight the real stylesheet. CSS now arrives exactly
    two ways: inline <style> in the HTML (the shipped CSS_TEMPLATE, which
    embeds the brand fonts via base64 @font-face — fix 3.2), or the
    `additional_css` escape hatch.
    """

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
        from weasyprint import CSS, HTML

        return HTML, CSS

    def _render_pdf_playwright(self, html: str, output_path: str, css_content: str) -> bool:
        """Fallback: render HTML to PDF using Playwright Chromium.

        Used when WeasyPrint can't load native GTK libraries (Windows).
        Produces a print-quality PDF with A4 page size and proper margins.
        """
        temp_html: str | None = None
        try:
            from playwright.sync_api import sync_playwright

            # Write HTML to a temp file so Playwright can load it. This is a
            # throwaway scratch file — it is ALWAYS deleted in `finally` so the
            # intermediate `_playwright.html` can never be mistaken for (or
            # shipped as) the deliverable. The deliverable is the .pdf only.
            temp_html = output_path.replace(".pdf", "_playwright.tmp.html")
            full_html = html
            # Always inline the CSS (never an absolute machine path <link>) so
            # the render is self-contained and portable across machines.
            if css_content and "<style>" not in html[:500]:
                full_html = f"<style>{css_content}</style>" + html
            with open(temp_html, "w", encoding="utf-8") as f:
                f.write(full_html)

            # Build proper file:// URL for Windows (C:\path → file:///C:/path)
            file_url = f"file:///{temp_html.replace(os.sep, '/').lstrip('/')}"

            with sync_playwright() as p:
                browser = p.chromium.launch()
                page = browser.new_page()
                page.goto(file_url, wait_until="networkidle")
                page.pdf(
                    path=output_path,
                    format="A4",
                    print_background=True,
                    margin={
                        "top": "25mm",
                        "bottom": "25mm",
                        "left": "40mm",
                        "right": "25mm",
                    },
                    prefer_css_page_size=True,
                )
                browser.close()

            success = os.path.exists(output_path) and os.path.getsize(output_path) > 0
            if not success:
                print("[RENDER] Playwright: PDF file missing or empty after render")
            return success

        except ImportError:
            print("[RENDER] Playwright not installed — cannot use PDF fallback")
            return False
        except Exception as exc:  # noqa: BLE001 - best-effort, returns a safe default
            print(f"[RENDER] Playwright PDF fallback failed: {type(exc).__name__}: {exc!s:.200}")
            return False
        finally:
            # Never leave the scratch HTML on disk — it must not be delivered.
            if temp_html and os.path.exists(temp_html):
                with contextlib.suppress(OSError):
                    os.remove(temp_html)

    def _embed_images_as_data_uris(self, html: str) -> str:
        """Convert img src file paths to base64 data URIs (D17 fix).

        WeasyPrint and Playwright can't reliably load images from absolute
        Windows paths (C:\\...) or relative paths. Embedding as data URIs
        makes the HTML self-contained — no external file dependencies.

        Handles:
        - <img src="C:\\path\\to\\image.png">  → <img src="data:image/png;base64,...">
        - <img src="path/to/image.png">       → resolved relative to cwd
        - <img src="data:image/...">          → already embedded, skip
        - <img src="https://...">             → remote URL, skip
        """
        import base64
        import re

        # Match img src attributes
        img_pattern = re.compile(r'<img\s+[^>]*src="([^"]+)"', re.IGNORECASE)

        def replace_src(match: re.Match[str]) -> str:
            src = match.group(1)

            # Skip already-embedded data URIs
            if src.startswith("data:"):
                return match.group(0)

            # Skip remote URLs
            if src.startswith("http://") or src.startswith("https://"):
                return match.group(0)

            # Resolve to absolute path
            img_path = Path(src)
            if not img_path.is_absolute():
                img_path = Path.cwd() / img_path

            if not img_path.exists():
                return match.group(0)  # Leave as-is if file doesn't exist

            # Determine MIME type
            ext = img_path.suffix.lower()
            mime_map = {
                ".png": "image/png",
                ".jpg": "image/jpeg",
                ".jpeg": "image/jpeg",
                ".gif": "image/gif",
                ".svg": "image/svg+xml",
                ".webp": "image/webp",
                ".bmp": "image/bmp",
            }
            mime_type = mime_map.get(ext, "application/octet-stream")

            # Read and encode
            try:
                img_data = img_path.read_bytes()
                b64 = base64.b64encode(img_data).decode("ascii")
                new_src = f"data:{mime_type};base64,{b64}"
                return match.group(0).replace(src, new_src)
            except (OSError, ValueError):
                return match.group(0)

        return img_pattern.sub(replace_src, html)

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
            additional_css: Optional extra CSS appended as a stylesheet. The
                shipped brand CSS is NOT loaded from disk (fix 3.3 removed the
                dead templates/styles/hyperion.css fork) — it arrives inline
                in `html`, already embedding the brand fonts (fix 3.2).

        Returns:
            PDFRenderResult with the PDF path and metadata.
        """
        result = PDFRenderResult()

        # Generate output path if not provided
        if not output_path:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            output_path = str(self._reports_dir / f"hyperion_report_{timestamp}.pdf")

        # Fix 3.3: no brand CSS is loaded from disk. The only stylesheet this
        # renderer adds beyond the inline <style> in `html` is an explicit
        # caller-provided `additional_css` escape hatch.
        css_embedded = additional_css or ""

        # Combine cover + body if cover is provided
        full_html = html
        if cover_html:
            full_html = cover_html + '<div class="page-break"></div>' + html

        # D17: Embed images as base64 data URIs so HTML is self-contained
        full_html = self._embed_images_as_data_uris(full_html)

        # Save HTML for debugging
        html_path = output_path.replace(".pdf", ".html")
        with open(html_path, "w", encoding="utf-8") as f:
            f.write(full_html)
        result.html_path = html_path

        # ── Attempt 1: WeasyPrint ──
        weasy_error: Exception | None = None
        try:
            weasy_html, weasy_css = self._get_weasyprint()

            # Create WeasyPrint HTML object
            html_obj = weasy_html(string=full_html, base_url=str(Path.cwd()))

            # Extra stylesheet only when the caller explicitly passed one;
            # the shipped brand CSS is inline in `full_html` already.
            css_obj = weasy_css(string=css_embedded) if css_embedded else None

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
        if self._render_pdf_playwright(full_html, output_path, css_embedded):
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

        # ── Both PDF engines failed: emit a real HTML deliverable ──
        #
        # HISTORY — this is the code path that produced the user-visible
        # disaster. Previously it DELETED the scratch HTML and returned
        # html_path="", so a 34-minute engagement finished with nothing in
        # output/ except a stray report.css. The reasoning was sound (an
        # *unstyled* scratch file is not a deliverable and must not masquerade
        # as one) but the conclusion was wrong: deleting the only surviving
        # artifact turned a degraded result into a total loss.
        #
        # The correct behaviour is to promote the scratch file into a genuine
        # fallback: inline the brand CSS so it is self-contained and styled,
        # name it unmistakably (…_FALLBACK.html), and mark the result as
        # degraded-but-delivered. The user gets something they can read, print
        # to PDF from a browser, and inspect — never an empty folder.
        result.success = False
        result.pdf_path = ""

        fallback_path = output_path.replace(".pdf", "_FALLBACK.html")
        fallback_written = False
        try:
            # Inline the (font-embedded) CSS so the file stands alone. The
            # debug HTML written earlier links no stylesheet at all, which is
            # precisely why it looked unusable.
            standalone = full_html
            if css_embedded:
                style_block = f"<style>\n{css_embedded}\n</style>"
                if "</head>" in standalone:
                    standalone = standalone.replace("</head>", style_block + "\n</head>", 1)
                elif "<body" in standalone:
                    standalone = style_block + standalone
                else:
                    standalone = (
                        "<!DOCTYPE html><html><head><meta charset='utf-8'>"
                        f"{style_block}</head><body>{standalone}</body></html>"
                    )
            banner = (
                "<div style=\"background:#7A1F1F;color:#fff;padding:14px 18px;"
                "font-family:Georgia,serif;font-size:13px;line-height:1.5;\">"
                "<strong>DEGRADED OUTPUT — HTML fallback.</strong> PDF rendering "
                "was unavailable on this machine, so the full report is provided "
                "as self-contained HTML. Use your browser's "
                "<em>Print &rarr; Save as PDF</em> (A4, margins on) to obtain a "
                "print-ready file. Content and analysis are complete and unaltered."
                "</div>"
            )
            if "<body" in standalone:
                idx = standalone.find(">", standalone.find("<body"))
                if idx != -1:
                    standalone = standalone[: idx + 1] + banner + standalone[idx + 1 :]
            else:
                standalone = banner + standalone

            with open(fallback_path, "w", encoding="utf-8") as f:
                f.write(standalone)
            fallback_written = os.path.getsize(fallback_path) > 0
        except Exception as e:  # noqa: BLE001 - fallback must never raise
            result.warnings.append(
                f"Could not write HTML fallback: {type(e).__name__}: {e!s:.100}"
            )

        # Remove the unstyled scratch files so only the real fallback remains.
        for scratch in (output_path.replace(".pdf", "_playwright.html"), html_path):
            try:
                if scratch != fallback_path and os.path.exists(scratch):
                    os.remove(scratch)
            except OSError:
                pass

        if fallback_written:
            result.html_path = fallback_path
            result.file_size_bytes = os.path.getsize(fallback_path)
            result.warnings.append(
                "PDF engines unavailable — delivered self-contained HTML fallback"
            )
            result.error = (
                f"PDF generation failed (WeasyPrint: {weasy_error!s:.80}; "
                f"Playwright fallback also failed). Delivered styled HTML "
                f"fallback instead: {fallback_path}"
            )
        else:
            result.html_path = ""
            result.error = (
                f"PDF generation FAILED — WeasyPrint: {weasy_error!s:.80}; "
                f"Playwright fallback also failed; HTML fallback could not be "
                f"written. No deliverable produced."
            )
        return result

    def verify_pdf(
        self,
        pdf_path: str,
        budget: Any | None = None,
    ) -> dict[str, Any]:
        """Verify a PDF meets HYPERION quality standards.

        Checks (§6.5):
        - No blank pages
        - All fonts embedded
        - Page count honours the delivery contract (fix 4.2)
        - File size is reasonable

        NOTE (fix 4.2 — the page-count check used to be decorative): this method
        previously recorded ``page_count_reasonable: 15 <= page_count <= 40`` and
        then computed ``passed`` from blank pages and embedded fonts *only*. The
        page count therefore could not fail a verification no matter what it was,
        and the 25-page-wide window would not have distinguished a compliant
        report from a 39-page one anyway. That is how the audit's §3.1 "36 pages
        against a 15-20 target" row survived: the number was measured, written
        down, and structurally ignored.

        The band now comes from `page_budget`, so it moves with the contract
        instead of being retyped here, and it participates in `passed`.

        Args:
            pdf_path: PDF to verify.
            budget: The `PageBudget` the report was generated under, when the
                caller knows it. Passing it lets the verdict distinguish a report
                that is short because the word ceiling bound it from one that is
                short because it is thin — see `page_count_verdict`.
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

            from hyperion.output.page_budget import page_count_verdict

            verdict = page_count_verdict(page_count, budget)

            return {
                "path": pdf_path,
                "page_count": page_count,
                "blank_pages": blank_pages,
                "has_blank_pages": len(blank_pages) > 0,
                "fonts_embedded": list(fonts),
                "all_fonts_embedded": len(fonts) > 0,
                "file_size_bytes": file_size,
                "page_count_reasonable": verdict.passed,
                "page_count_expected_min": verdict.expected_min,
                "page_count_expected_max": verdict.expected_max,
                "page_count_reason": verdict.reason,
                # Page count is now load-bearing in the verdict. Before 4.2 it
                # was computed and discarded.
                "passed": (
                    len(blank_pages) == 0 and len(fonts) > 0 and verdict.passed
                ),
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
