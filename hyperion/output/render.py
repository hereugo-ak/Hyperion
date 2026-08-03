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

from markupsafe import Markup

# Chromium does not implement CSS paged-media margin boxes. These templates
# provide the running furniture for the Playwright fallback; the first-page
# cover stays clean because Chromium suppresses header/footer templates when
# the page margins are removed by the named cover page.
PLAYWRIGHT_HEADER_TEMPLATE = """
<div style="width:100%;font-family:serif;font-size:10px;color:#6f675f;text-align:center;">
  HYPERION
</div>
"""
PLAYWRIGHT_FOOTER_TEMPLATE = """
<div style="width:100%;font-family:monospace;font-size:8px;color:#6f675f;text-align:center;">
  HYPERION · many minds. one reading. ·
  <span class="pageNumber"></span> / <span class="totalPages"></span>
</div>
"""


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
    # W-02 (RC-2): when the page audit rejects the render, pdf_path is ""
    # and these record where the rejected bytes went and why. A withheld
    # PDF must be physically observable — not a silent success=False with
    # the deliverable name still on disk (RC-2: a 277-violation PDF named
    # exactly what the user expected the deliverable to be named).
    rejected_path: str = ""
    audit_violations: list[str] = field(default_factory=list)

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
            "rejected_path": self.rejected_path,
            "audit_violations": self.audit_violations,
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

            from hyperion.output.display import humanize
            from hyperion.output.typography import sanitize_typography

            # P2-10: humanize is the environment FINALIZER, not an opt-in
            # filter. The old clean_dict_repr filter was applied to exactly 1
            # of ~40 renderable fields and its startswith('{') guard could not
            # fire on the LABEL: {'...'} strings that actually leaked. As the
            # finalize hook, humanize runs on every interpolated value, so no
            # field can be forgotten. On an unparseable repr it raises rather
            # than truncating and shipping.
            #
            # P2-32: the finalizer ALSO sanitizes typography. humanize runs
            # first (repr -> prose), then sanitize_typography removes every
            # em/en dash model output or a leaked string literal carries. This
            # catches dashes regardless of prompt compliance.
            def _finalize(value: Any) -> str:
                return sanitize_typography(humanize(value))

            self._env = Environment(
                autoescape=select_autoescape(["html", "xml"]),
                trim_blocks=True,
                lstrip_blocks=True,
                finalize=_finalize,
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
        return Markup(output)

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
                    display_header_footer=True,
                    header_template=PLAYWRIGHT_HEADER_TEMPLATE,
                    footer_template=PLAYWRIGHT_FOOTER_TEMPLATE,
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
        """Embed local images while enforcing per-asset and total byte budgets.

        Self-contained HTML is portable, but blindly base64-encoding archival
        PNGs can add tens of megabytes to one report. Oversized photographs are
        recompressed before embedding, charts retain lossless PNG where the
        budget permits, and assets that cannot fit are omitted. Machine-local
        paths are never left behind as a false promise to another renderer.
        """
        import base64
        import re

        from hyperion.output.images import (
            MAX_CHART_IMAGE_BYTES,
            MAX_COVER_BYTES,
            MAX_EMBEDDED_IMAGE_BYTES,
            MAX_SECTION_IMAGE_BYTES,
            compress_image_for_embedding,
        )

        img_pattern = re.compile(
            r'<img\b[^>]*\bsrc="([^"]+)"[^>]*>',
            re.IGNORECASE,
        )
        embedded_bytes = 0

        def replace_src(match: re.Match[str]) -> str:
            nonlocal embedded_bytes
            tag = match.group(0)
            src = match.group(1)
            tag_lower = tag.lower()

            is_cover = "cover-image" in tag_lower
            is_chart = any(
                marker in tag_lower or marker in Path(src).stem.lower()
                for marker in ("chart", "exhibit", "plot")
            )
            per_asset_budget = (
                MAX_COVER_BYTES
                if is_cover
                else MAX_CHART_IMAGE_BYTES
                if is_chart
                else MAX_SECTION_IMAGE_BYTES
            )
            remaining = MAX_EMBEDDED_IMAGE_BYTES - embedded_bytes
            allowed_bytes = min(per_asset_budget, remaining)
            if allowed_bytes <= 0:
                return ""

            # Existing data URIs still count against both limits. They cannot
            # safely be recompressed without MIME-specific decoding, so omit an
            # over-budget payload rather than bypassing the guard.
            if src.startswith("data:"):
                try:
                    payload = src.split(",", 1)[1]
                    size = len(base64.b64decode(payload, validate=True))
                except (IndexError, ValueError):
                    return ""
                if size > allowed_bytes:
                    return ""
                embedded_bytes += size
                return tag

            # Remote URLs are deliberately left alone: this method owns local
            # asset embedding, and callers may intentionally allow network
            # resources in non-deliverable preview HTML.
            if src.startswith(("http://", "https://")):
                return tag

            img_path = Path(src)
            if not img_path.is_absolute():
                img_path = Path.cwd() / img_path
            if not img_path.is_file():
                return ""

            compressed = compress_image_for_embedding(
                img_path,
                allowed_bytes,
                preserve_lossless=is_chart,
            )
            if compressed is None:
                return ""

            img_data, mime_type = compressed
            embedded_bytes += len(img_data)
            b64 = base64.b64encode(img_data).decode("ascii")
            new_src = f"data:{mime_type};base64,{b64}"
            return tag.replace(src, new_src, 1)

        return img_pattern.sub(replace_src, html)

    def _apply_pdf_post_pass(
        self,
        result: PDFRenderResult,
        output_path: str,
        full_html: str,
    ) -> None:
        """5.6: PDF/A-2b + bookmarks post-pass via pikepdf.

        Runs after either PDF engine succeeds. Degrades silently-but-recorded:
        missing pikepdf or a failed pass adds a warning and leaves the
        un-post-processed (still valid) PDF in place — never a crash, never a
        half-written deliverable (the pass writes atomically).
        """
        import re

        from hyperion.output.pdf_postprocess import (
            BookmarkSpec,
            PDFMetadata,
            postprocess_pdf,
        )

        # Title: first <title> tag, else first <h1>, else a dated fallback.
        title = ""
        for pattern in (r"<title[^>]*>([^<]+)</title>", r"<h1[^>]*>([^<]+)</h1>"):
            match = re.search(pattern, full_html, re.IGNORECASE)
            if match and match.group(1).strip():
                title = match.group(1).strip()
                break
        if not title:
            title = f"HYPERION Report {datetime.now().strftime('%Y-%m-%d')}"

        # Outline from <h1>/<h2> headings mapped onto pages by content order.
        # Page mapping needs the rendered layout, so we locate each heading's
        # text in the produced PDF via fitz; headings not found are skipped
        # rather than guessed.
        bookmarks: list[BookmarkSpec] = []
        try:
            import fitz

            doc = fitz.open(output_path)
            headings = [
                (m.group(1).strip(), 1 if m.group(0).lower().startswith("<h1") else 2)
                for m in re.finditer(r"<h[12][^>]*>([^<]+)</h[12]>", full_html, re.IGNORECASE)
            ]
            seen_pages: set[int] = set()
            for heading_text, _level in headings:
                clean = re.sub(r"\s+", " ", heading_text)[:80]
                if not clean:
                    continue
                for page_index in range(len(doc)):
                    if page_index in seen_pages:
                        continue
                    if doc[page_index].search_for(clean):
                        bookmarks.append(BookmarkSpec(title=clean, page=page_index))
                        seen_pages.add(page_index)
                        break
            doc.close()
        except (ImportError, OSError, ValueError) as exc:
            result.warnings.append(f"bookmark extraction skipped: {exc!s:.100}")

        post = postprocess_pdf(
            output_path,
            PDFMetadata(title=title, keywords="deep research, consulting"),
            bookmarks=bookmarks,
        )
        if post.applied:
            result.warnings.append(
                f"PDF/A-2b post-pass applied ({post.bookmarks_written} bookmarks)"
            )
        else:
            result.warnings.append(f"PDF/A-2b post-pass skipped: {post.reason}")

    def _finalize_or_reject(
        self,
        result: PDFRenderResult,
        staging_path: str,
        output_path: str,
    ) -> PDFRenderResult:
        """W-02 (RC-2): the single finalisation point for BOTH PDF engines.

        The staging file has been rendered and post-processed; audit it,
        then either promote it to the deliverable name or quarantine it.

        On pass: ``os.replace(staging, output)`` — atomic on the same
        filesystem, so the deliverable path either does not exist or is a
        clean, audited PDF. There is no window in which a partial or
        unaudited file occupies the deliverable name.

        On fail: move the staging file to
        ``<output_dir>/_rejected/<slug>.<timestamp>.rejected.pdf`` with a
        sibling ``.violations.txt`` listing every violation in full, and
        guarantee the deliverable path does not exist. The rejected bytes
        are preserved for debugging; the ``_rejected/`` location and the
        ``.rejected.pdf`` suffix mean the user can never mistake one for a
        deliverable.
        """
        from hyperion.output.page_audit import PageAuditError, audit_pdf

        try:
            audit_pdf(staging_path)
        except PageAuditError as exc:
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            slug = Path(output_path).stem
            rejected_dir = Path(output_path).parent / "_rejected"
            rejected_dir.mkdir(parents=True, exist_ok=True)
            rejected_path = rejected_dir / f"{slug}.{timestamp}.rejected.pdf"
            violations_path = rejected_dir / f"{slug}.{timestamp}.violations.txt"
            try:
                # Same directory tree as the deliverable, so os.replace is
                # atomic here too (never shutil.move across filesystems).
                os.replace(staging_path, rejected_path)
            except OSError as move_exc:
                result.warnings.append(
                    f"rejected artifact could not be quarantined: {move_exc!s:.100}"
                )
            violations_path.write_text(
                f"Page audit rejected: {output_path}\n"
                f"Rejected at: {datetime.now().isoformat()}\n"
                f"Violations ({len(exc.violations)}):\n"
                + "\n".join(f"- {v}" for v in exc.violations)
                + "\n",
                encoding="utf-8",
            )
            # The deliverable name must not exist after a failed audit.
            Path(output_path).unlink(missing_ok=True)
            result.success = False
            result.pdf_path = ""
            result.rejected_path = str(rejected_path)
            result.audit_violations = list(exc.violations)
            result.error = (
                f"page audit failed ({len(exc.violations)} violation(s)); "
                f"rejected artifact quarantined at {rejected_path}; "
                f"full violation list at {violations_path}"
            )
            result.warnings.append(
                "PDF withheld: render-time page audit failed (see error)"
            )
            return result

        os.replace(staging_path, output_path)
        result.pdf_path = output_path
        result.success = True
        result.file_size_bytes = os.path.getsize(output_path)
        return result

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

        # W-02 (RC-2): render to a staging path in the SAME directory, never
        # directly to the deliverable name. The deliverable path is only ever
        # populated by _finalize_or_reject's atomic os.replace AFTER the page
        # audit passes; a failed audit quarantines the bytes under _rejected/
        # and guarantees the deliverable name does not exist.
        staging_path = output_path + ".staging.pdf"

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

            # Render PDF to the staging path (W-02) — the deliverable name
            # stays empty until _finalize_or_reject promotes audited bytes.
            if css_obj:
                html_obj.write_pdf(staging_path, stylesheets=[css_obj])
            else:
                html_obj.write_pdf(staging_path)

            # Try to get page count
            try:
                import fitz

                doc = fitz.open(staging_path)
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

            self._apply_pdf_post_pass(result, staging_path, full_html)

            # P2-08/P2-G1 + W-02: render-time page audit, fail closed, via
            # the shared finaliser — a pass atomically promotes the staging
            # file to the deliverable name; a failure quarantines it under
            # _rejected/ and guarantees the deliverable name does not exist.
            return self._finalize_or_reject(result, staging_path, output_path)

        except (OSError, ImportError, ValueError, RuntimeError) as exc:
            weasy_error = exc
            result.warnings.append(f"WeasyPrint failed: {weasy_error!s:.120}")

        # ── Attempt 2: Playwright Chromium fallback ──
        if self._render_pdf_playwright(full_html, staging_path, css_embedded):
            result.warnings.append("PDF rendered via Playwright (WeasyPrint unavailable)")

            # Try to get page count
            try:
                import fitz

                doc = fitz.open(staging_path)
                result.page_count = len(doc)
                doc.close()
            except (ImportError, OSError, ValueError):
                pass

            self._apply_pdf_post_pass(result, staging_path, full_html)

            # P2-08/P2-G1 + W-02: same shared finaliser as the WeasyPrint
            # path — the audit applies to whichever engine produced the
            # bytes, and a failure is quarantined identically.
            return self._finalize_or_reject(result, staging_path, output_path)

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
        # W-02: no staging or deliverable bytes survive a total engine
        # failure — only the self-contained HTML fallback remains.
        Path(output_path).unlink(missing_ok=True)
        Path(staging_path).unlink(missing_ok=True)

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
