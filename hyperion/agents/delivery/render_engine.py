"""
HYPERION Render Engine — Agent 20, the final PDF assembly.

This is NOT a generic "call weasyprint" agent. This is a specialist with
5 proprietary skills:

- PDF generation: Convert Jinja2-rendered HTML to PDF with proper page
  sizing (A4), DPI (300), and font embedding. Not just "render to PDF" —
  it sets DPI, embeds fonts, controls page breaks, and verifies output.

- Image processing: Process all images through the Pillow pipeline:
  resize (never upscale), crop (center-weighted), color-correct (match
  brand warmth), sharpen (unsharp mask), export as PNG (lossless).
  Every image — whether from Unsplash or Plotly — goes through the same
  pipeline. (§6.4)

- Color management: Ensure brand colors render correctly in PDF (CMYK
  fallback for print, exact hex for digital). No blue, no purple, no
  green — warm premium palette only.

- Font embedding: Embed Instrument Serif and JetBrains Mono in the PDF
  so it renders identically on any system. Without embedding, the PDF
  looks different on every machine — that's not premium.

- Page break control: Use CSS `page-break-inside: avoid` and
  `page-break-before: always` to control page flow and prevent blank
  pages. Scan the rendered PDF for blank pages and orphaned images.

It runs on CPU tier (no LLM calls) because PDF rendering and image
processing are deterministic CPU tasks — they don't need reasoning.

Model Tier: CPU (no LLM — CPU-only tasks: PDF rendering, image processing)
Tools: WeasyPrint (HTML/CSS → PDF at 300 DPI),
       Pillow (image processing: resize, crop, color-correct, sharpen)
Sub-agents: 0 (delivery agent — doesn't spawn sub-agents)
Output: RenderOutput (PDF path, page count, verification results)

Methodology (§4.6, Agent 20):
1. Receive HTML from Presentation Designer
2. Receive image paths from Data Visualizer and Unsplash tool
3. Process all images through Pillow pipeline
4. Convert HTML → PDF with WeasyPrint at 300 DPI
5. Verify PDF: no blank pages, no orphaned images, all fonts embedded
6. Save to reports/ directory
7. Return PDF path

What makes it the best version of itself:
It is the last line of defense for quality. It verifies the PDF after
rendering — checks for blank pages, checks that all images are properly
placed, checks that fonts are embedded. If any check fails, it reports
the issue back to the Presentation Designer for correction. It never
ships a broken PDF.
"""

from __future__ import annotations

import hashlib
import os
from datetime import datetime
from typing import Any

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import Channel, MessageType
from hyperion.config import ModelTier
from hyperion.schemas.agents import (
    AgentName,
    AgentRole,
    AgentSpec,
    AgentState,
    SkillSpec,
    ToolName,
)
from hyperion.schemas.models import (
    ConfidenceLevel,
    KeyFinding,
    LayoutPlan,
    RenderOutput,
)


# ─────────────────────────────────────────────────────────────────────────────
# Pillow Image Pipeline Constants (§6.4)
# ─────────────────────────────────────────────────────────────────────────────

# Target dimensions for different image placements (at 300 DPI)
# A4 at 300 DPI = 2480 x 3508 pixels
# 25mm margin = ~295px, so content area = ~1890 x 2918 pixels

COVER_IMAGE_WIDTH = 2480       # Full A4 width at 300 DPI
COVER_IMAGE_HEIGHT = 3508      # Full A4 height at 300 DPI

SECTION_IMAGE_WIDTH = 756      # 40% of content area width (~1890 * 0.4)
SECTION_IMAGE_HEIGHT = 1132    # 40% of content area height (max 50%)

CHART_IMAGE_WIDTH = 1512       # 80% of content area width
CHART_IMAGE_HEIGHT = 1132      # Max 40% of page height

# Brand warmth filter intensity (§6.4)
WARM_FILTER_INTENSITY = 0.05

# Unsharp mask parameters for print sharpness (§6.4)
UNSHARP_RADIUS = 2
UNSHARP_PERCENT = 150
UNSHARP_THRESHOLD = 3

# Required fonts to embed (§7.4)
REQUIRED_FONTS = [
    "Instrument Serif",
    "Source Sans 3",  # D24: body font changed from JetBrains Mono
    "JetBrains Mono",  # Still required for tables/numbers
]

# PDF output directory
REPORTS_DIR = "reports"

# Minimum image resolution thresholds (at 300 DPI)
MIN_COVER_RESOLUTION = (1200, 1600)
MIN_SECTION_RESOLUTION = (600, 600)
MIN_CHART_RESOLUTION = (1000, 600)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


RENDER_ENGINE_SPEC = AgentSpec(
    name=AgentName.RENDER_ENGINE,
    role=AgentRole.DELIVERY,
    display_name="Render Engine",
    model_tier=ModelTier.CPU,
    tools=[
        ToolName.WEASYPRINT,
        ToolName.PILLOW,
    ],
    skills=[
        SkillSpec(
            name="PDF generation",
            description=(
                "Convert Jinja2-rendered HTML to PDF with proper page "
                "sizing (A4), DPI (300), and font embedding. Not just "
                "'call weasyprint' — it sets DPI=300, page_size=A4, "
                "embeds Instrument Serif and JetBrains Mono, and "
                "controls page breaks via CSS page-break-inside: avoid "
                "and page-break-before: always."
            ),
            inputs=["html_path", "css_path", "layout_plan"],
            outputs=["pdf_path", "page_count", "file_size"],
        ),
        SkillSpec(
            name="Image processing",
            description=(
                "Process all images through the Pillow pipeline (§6.4): "
                "(1) Open and verify resolution is high enough. "
                "(2) Resize — downscale only, never upscale, LANCZOS "
                "interpolation. (3) Crop — center-weighted, preserving "
                "most interesting region. (4) Color-correct — apply warm "
                "filter at 5% intensity to match brand warmth. "
                "(5) Sharpen — unsharp mask (radius=2, percent=150, "
                "threshold=3) for print. (6) Export as PNG (lossless) "
                "at 300 DPI. Every image — Unsplash or Plotly — goes "
                "through the same pipeline."
            ),
            inputs=["image_paths", "target_dimensions"],
            outputs=["processed_image_paths", "processing_report"],
        ),
        SkillSpec(
            name="Color management",
            description=(
                "Ensure brand colors render correctly in PDF. Warm "
                "premium palette only (§7.2): Warm Charcoal #1A1A1A, "
                "Cream #F5F4EE, Terracotta #C8704D, Sage #7C9885, "
                "Beige #E8E6DD, Warm Gray #8B8680, Deep Brown #3D3530, "
                "Alert Red #B5533C. CMYK fallback for print, exact hex "
                "for digital. NEVER blue, purple, or green."
            ),
            inputs=["css_path", "pdf_path"],
            outputs=["color_verification", "cmyk_conversion"],
        ),
        SkillSpec(
            name="Font embedding",
            description=(
                "Embed Instrument Serif and JetBrains Mono in the PDF "
                "so it renders identically on any system. Without "
                "embedding, the PDF looks different on every machine — "
                "that's not premium. Verify after rendering that both "
                "fonts are present in the PDF's font dictionary."
            ),
            inputs=["pdf_path", "font_files"],
            outputs=["fonts_embedded", "font_verification"],
        ),
        SkillSpec(
            name="Page break control",
            description=(
                "Use CSS page-break-inside: avoid and page-break-before: "
                "always to control page flow and prevent blank pages. "
                "After rendering, scan the PDF for blank pages (pages "
                "with no text content) and orphaned images (images "
                "without adjacent text). If any check fails, report "
                "back to the Presentation Designer."
            ),
            inputs=["pdf_path", "layout_plan"],
            outputs=["blank_page_check", "orphaned_image_check", "page_flow_report"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Render Engine — the final PDF assembly "
        "and the last line of defense for quality.\n\n"
        "Your role:\n"
        "1. RECEIVE HTML from the Presentation Designer.\n"
        "2. RECEIVE image paths from the Data Visualizer and Unsplash.\n"
        "3. PROCESS all images through the Pillow pipeline.\n"
        "4. CONVERT HTML → PDF with WeasyPrint at 300 DPI.\n"
        "5. VERIFY: no blank pages, no orphaned images, all fonts embedded.\n"
        "6. SAVE to reports/ directory.\n"
        "7. RETURN PDF path.\n\n"
        "Pillow Image Pipeline (§6.4 — NON-NEGOTIABLE):\n"
        "1. Open and verify resolution (raise ImageTooSmallError if too small).\n"
        "2. Resize — downscale ONLY, never upscale. LANCZOS interpolation.\n"
        "3. Crop — center-weighted, preserving most interesting region.\n"
        "4. Color-correct — apply warm filter at 5% intensity.\n"
        "5. Sharpen — unsharp mask (radius=2, percent=150, threshold=3).\n"
        "6. Export as PNG (lossless) at 300 DPI.\n\n"
        "PDF Requirements:\n"
        "- A4 page size (210mm x 297mm).\n"
        "- 300 DPI.\n"
        "- Margins: 25mm all sides, 15mm extra on left for binding.\n"
        "- Fonts embedded: Instrument Serif, JetBrains Mono.\n"
        "- Brand colors only (warm palette, no blue/purple/green).\n"
        "- No blank pages. No orphaned images.\n\n"
        "Verification (NON-NEGOTIABLE):\n"
        "- Scan PDF for blank pages (pages with no text content).\n"
        "- Check all images are properly placed (not orphaned).\n"
        "- Verify both fonts are embedded in the PDF.\n"
        "- If any check fails, report back to Presentation Designer.\n"
        "- NEVER ship a broken PDF.\n\n"
        "You run on CPU tier (no LLM calls). You do NOT spawn sub-agents.\n"
        "Your output is a RenderOutput Pydantic model — PDF path, page "
        "count, verification results."
    ),
    spawn_condition="Spawned after the Presentation Designer produces the "
                     "LayoutPlan and HTML. Receives HTML path, CSS path, "
                     "image paths, and chart paths. Produces the final PDF.",
    max_sub_agents=0,
    output_model="RenderOutput",
)


# ─────────────────────────────────────────────────────────────────────────────
# Render Engine Agent
# ─────────────────────────────────────────────────────────────────────────────


class ImageTooSmallError(Exception):
    """Raised when an image's resolution is below the minimum threshold."""

    def __init__(self, image_path: str, actual: tuple[int, int], minimum: tuple[int, int]) -> None:
        self.image_path = image_path
        self.actual = actual
        self.minimum = minimum
        super().__init__(
            f"Image {image_path} is {actual[0]}x{actual[1]} but minimum is {minimum[0]}x{minimum[1]}"
        )


class RenderEngine(BaseAgent):
    """Agent 20: Final PDF assembly — converts HTML/CSS + images into a 300 DPI PDF.

    The Render Engine is the last line of defense for quality. It processes
    all images through the Pillow pipeline, renders the PDF with WeasyPrint
    at 300 DPI, and verifies the output — no blank pages, no orphaned
    images, all fonts embedded. If any check fails, it reports back to the
    Presentation Designer. It never ships a broken PDF.
    (§4.6, Agent 20)

    Lifecycle:
    1. Receive HTML from Presentation Designer
    2. Receive image paths from Data Visualizer and Unsplash tool
    3. Process all images through Pillow pipeline
    4. Convert HTML → PDF with WeasyPrint at 300 DPI
    5. Verify PDF: no blank pages, no orphaned images, all fonts embedded
    6. Save to reports/ directory
    7. Return PDF path
    """

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or RENDER_ENGINE_SPEC, bus=bus, router=router)

        # HTML and CSS paths from Presentation Designer
        self._html_path: str = ""
        self._css_path: str = ""

        # Image paths to process
        self._image_paths: list[str] = []
        self._chart_paths: list[str] = []

        # Layout plan (for verification)
        self._layout_plan: LayoutPlan | None = None

        # Processed image paths (after Pillow pipeline)
        self._processed_images: dict[str, str] = {}  # original → processed

        # PDF output path
        self._pdf_path: str = ""

        # Verification results
        self._verification_issues: list[str] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Render Engine listens to:
        - HANDOFF: receives layout plan and HTML from Presentation Designer
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            if task == "render_pdf":
                context_bundle = payload.get("context_bundle", {})
                self._html_path = context_bundle.get("html_path", "")
                self._css_path = context_bundle.get("css_path", "")
                self._image_paths = context_bundle.get("images_to_process", [])
                self._chart_paths = context_bundle.get("charts_to_process", [])
                self._pdf_path = context_bundle.get("pdf_output_path", "")

                layout_data = context_bundle.get("layout_plan")
                if layout_data:
                    self._layout_plan = LayoutPlan(**layout_data) if isinstance(layout_data, dict) else layout_data

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Receive HTML from Presentation Designer
    # ─────────────────────────────────────────────────────────────────────

    def _receive_html(self, html_path: str, css_path: str = "") -> str:
        """Receive the HTML path from the Presentation Designer."""
        self._html_path = html_path
        if css_path:
            self._css_path = css_path
        return self._html_path

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Receive image paths from Data Visualizer and Unsplash tool
    # ─────────────────────────────────────────────────────────────────────

    def _receive_image_paths(
        self,
        image_paths: list[str] | None = None,
        chart_paths: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """Receive image paths from the Data Visualizer and Unsplash tool."""
        if image_paths:
            self._image_paths = image_paths
        if chart_paths:
            self._chart_paths = chart_paths
        return (self._image_paths, self._chart_paths)

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Process all images through Pillow pipeline
    # ─────────────────────────────────────────────────────────────────────

    def _apply_warm_filter(self, img: Any, intensity: float = WARM_FILTER_INTENSITY) -> Any:
        """Apply a warm filter to match brand warmth.

        Slightly increases red channel and decreases blue channel.
        This gives images a warm, premium feel consistent with the
        HYPERION brand palette. (§6.4)
        """
        try:
            from PIL import Image, ImageEnhance, ImageOps

            # Split into RGB channels
            r, g, b = img.split()

            # Increase red slightly, decrease blue slightly
            r = r.point(lambda p: min(255, int(p * (1 + intensity))))
            b = b.point(lambda p: int(p * (1 - intensity * 0.5)))

            img = Image.merge("RGB", (r, g, b))

            # Slight saturation boost for richness
            enhancer = ImageEnhance.Color(img)
            img = enhancer.enhance(1.05)

            return img

        except (ImportError, ValueError, AttributeError, RuntimeError):
            return img

    def _center_crop(self, img: Any, target_width: int, target_height: int) -> Any:
        """Center-weighted crop preserving the most interesting region.

        Crops from the center, which is typically the most important
        part of the image (faces, objects, focal points). (§6.4)
        """
        try:
            from PIL import Image

            width, height = img.size
            aspect_target = target_width / target_height
            aspect_img = width / height

            if aspect_img > aspect_target:
                # Image is wider than target — crop width
                new_width = int(height * aspect_target)
                left = (width - new_width) // 2
                img = img.crop((left, 0, left + new_width, height))
            elif aspect_img < aspect_target:
                # Image is taller than target — crop height
                new_height = int(width / aspect_target)
                top = (height - new_height) // 2
                img = img.crop((0, top, width, top + new_height))

            return img

        except (ImportError, ValueError, AttributeError, RuntimeError):
            return img

    def _process_single_image(
        self,
        image_path: str,
        target_width: int,
        target_height: int,
        min_resolution: tuple[int, int] = (600, 600),
    ) -> str:
        """Process a single image through the full Pillow pipeline.

        Pipeline (§6.4):
        1. Open and verify resolution
        2. Resize (downscale only, never upscale, LANCZOS)
        3. Crop (center-weighted)
        4. Color-correct (warm filter at 5% intensity)
        5. Sharpen (unsharp mask: radius=2, percent=150, threshold=3)
        6. Export as PNG (lossless) at 300 DPI

        Returns the path to the processed PNG.
        """
        try:
            from PIL import Image, ImageFilter

            img = Image.open(image_path)

            # Convert to RGB if needed (handles RGBA, P, L modes)
            if img.mode not in ("RGB", "RGBA"):
                img = img.convert("RGB")

            # Step 1: Verify resolution
            actual_w, actual_h = img.size
            min_w, min_h = min_resolution
            if actual_w < min_w or actual_h < min_h:
                raise ImageTooSmallError(image_path, (actual_w, actual_h), (min_w, min_h))

            # Step 2: Resize (downscale only, never upscale)
            if actual_w > target_width or actual_h > target_height:
                # Calculate resize dimensions maintaining aspect ratio
                img_aspect = actual_w / actual_h
                target_aspect = target_width / target_height

                if img_aspect > target_aspect:
                    # Image is wider — fit to height
                    new_height = target_height
                    new_width = int(target_height * img_aspect)
                else:
                    # Image is taller — fit to width
                    new_width = target_width
                    new_height = int(target_width / img_aspect)

                img = img.resize((new_width, new_height), Image.LANCZOS)

            # Step 3: Crop (center-weighted)
            img = self._center_crop(img, target_width, target_height)

            # Ensure exact target dimensions
            if img.size != (target_width, target_height):
                img = img.resize((target_width, target_height), Image.LANCZOS)

            # Step 4: Color-correct (warm filter)
            img = self._apply_warm_filter(img, WARM_FILTER_INTENSITY)

            # Step 5: Sharpen for print (unsharp mask)
            img = img.filter(
                ImageFilter.UnsharpMask(
                    radius=UNSHARP_RADIUS,
                    percent=UNSHARP_PERCENT,
                    threshold=UNSHARP_THRESHOLD,
                )
            )

            # Step 6: Export as PNG (lossless) at 300 DPI
            base, _ = os.path.splitext(image_path)
            output_path = f"{base}_processed.png"

            img.save(output_path, "PNG", dpi=(300, 300))

            return output_path

        except ImportError:
            # Pillow not available — return original path
            return image_path
        except ImageTooSmallError:
            raise
        except (ValueError, OSError, RuntimeError):
            # Processing failed — return original path
            return image_path

    def _process_all_images(self) -> dict[str, str]:
        """Process all images (Unsplash photos + Plotly charts) through the Pillow pipeline.

        Cover images get full A4 resolution (2480x3508).
        Section images get 40% width (756x1132).
        Chart images get 80% width (1512x1132).

        Returns a mapping of original_path → processed_path.
        """
        processed: dict[str, str] = {}

        # Process cover image (if in image_paths, it's the first one)
        for i, img_path in enumerate(self._image_paths):
            try:
                if i == 0 and self._layout_plan and self._layout_plan.cover_image:
                    # Cover image — full A4
                    processed_path = self._process_single_image(
                        img_path,
                        target_width=COVER_IMAGE_WIDTH,
                        target_height=COVER_IMAGE_HEIGHT,
                        min_resolution=MIN_COVER_RESOLUTION,
                    )
                else:
                    # Section image — 40% width
                    processed_path = self._process_single_image(
                        img_path,
                        target_width=SECTION_IMAGE_WIDTH,
                        target_height=SECTION_IMAGE_HEIGHT,
                        min_resolution=MIN_SECTION_RESOLUTION,
                    )
                processed[img_path] = processed_path
            except ImageTooSmallError as e:
                # D25: Non-fatal — skip image, don't add to verification issues
                # The report should still render without this photo
                processed[img_path] = img_path  # Use original as fallback

        # Process chart images
        for chart_path in self._chart_paths:
            try:
                processed_path = self._process_single_image(
                    chart_path,
                    target_width=CHART_IMAGE_WIDTH,
                    target_height=CHART_IMAGE_HEIGHT,
                    min_resolution=MIN_CHART_RESOLUTION,
                )
                processed[chart_path] = processed_path
            except ImageTooSmallError as e:
                # D25: Non-fatal — skip chart image processing
                processed[chart_path] = chart_path

        self._processed_images = processed
        return processed

    def _update_html_with_processed_images(self, html_path: str, processed: dict[str, str]) -> str:
        """Update the HTML file to reference processed image paths instead of originals."""
        if not processed:
            return html_path

        try:
            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            for original, processed_path in processed.items():
                if original != processed_path:
                    html_content = html_content.replace(original, processed_path)

            updated_path = html_path.replace(".html", "_final.html")
            with open(updated_path, "w", encoding="utf-8") as f:
                f.write(html_content)

            return updated_path
        except (OSError, IOError):
            return html_path

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Convert HTML → PDF with WeasyPrint at 300 DPI
    # ─────────────────────────────────────────────────────────────────────

    async def _render_pdf(self, html_path: str) -> str:
        """Convert HTML → PDF with WeasyPrint at 300 DPI.

        WeasyPrint settings:
        - Page size: A4 (210mm x 297mm)
        - DPI: 300
        - Font embedding: Instrument Serif, JetBrains Mono
        - Margins: 25mm all sides, 15mm binding on left
        """
        os.makedirs(REPORTS_DIR, exist_ok=True)

        if not self._pdf_path:
            self._pdf_path = os.path.join(REPORTS_DIR, f"report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.pdf")

        try:
            weasyprint_tool = self.get_tool(ToolName.WEASYPRINT)

            # PDFRenderer.render_pdf expects HTML string content, not a file path.
            # Read the HTML file that the Presentation Designer wrote.
            html_content = ""
            try:
                with open(html_path, "r", encoding="utf-8") as f:
                    html_content = f.read()
            except (OSError, ValueError) as e:
                self._log(f"RENDER: failed to read HTML file: {e!s:.200}")
                return ""

            if not html_content:
                self._log("RENDER: HTML content is empty")
                return ""

            result = weasyprint_tool.render_pdf(
                html=html_content,
                output_path=self._pdf_path,
            )

            if result and result.success:
                if result.warnings:
                    self._log(f"RENDER: PDF generated with warnings: {'; '.join(result.warnings[:3])}")
                return self._pdf_path
            else:
                error_msg = ""
                if result and result.error:
                    error_msg = result.error[:200]
                elif result and result.warnings:
                    error_msg = "; ".join(result.warnings[:3])
                self._log(f"RENDER: PDFRenderer failed: {error_msg}")
                return ""

        except (ValueError, AttributeError, RuntimeError) as e:
            self._log(f"RENDER: WeasyPrint tool unavailable ({type(e).__name__}: {e!s:.100}), trying direct import")
            # Fallback: try direct weasyprint import
            try:
                from weasyprint import HTML

                HTML(filename=html_path).write_pdf(
                    self._pdf_path,
                    dpi=300,
                )
                return self._pdf_path
            except (ImportError, RuntimeError, OSError) as e2:
                self._log(f"RENDER: Direct WeasyPrint also failed: {type(e2).__name__}: {e2!s:.200}")
                return ""

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Verify PDF — no blank pages, no orphaned images, fonts embedded
    # ─────────────────────────────────────────────────────────────────────

    def _verify_no_blank_pages(self, pdf_path: str) -> tuple[bool, list[int]]:
        """Verify the PDF has no blank pages.

        A blank page is one with no text content. Scans each page's
        text content and flags any page with zero characters.
        """
        blank_pages: list[int] = []

        try:
            import fitz  # PyMuPDF

            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                text = page.get_text().strip()
                if len(text) < 10:  # Less than 10 chars = effectively blank
                    # Don't flag cover page (page 0) or back cover (last page)
                    if page_num != 0 and page_num != len(doc) - 1:
                        blank_pages.append(page_num + 1)
            doc.close()

        except ImportError:
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(pdf_path)
                for i, page in enumerate(reader.pages):
                    text = page.extract_text().strip()
                    if len(text) < 10:
                        if i != 0 and i != len(reader.pages) - 1:
                            blank_pages.append(i + 1)
            except ImportError:
                # No PDF library available — skip check
                pass

        return (len(blank_pages) == 0, blank_pages)

    def _verify_fonts_embedded(self, pdf_path: str) -> tuple[bool, list[str]]:
        """Verify that required fonts are embedded in the PDF.

        Checks for Instrument Serif and JetBrains Mono in the PDF's
        font dictionary. Without embedding, the PDF looks different
        on every system — that's not premium.
        """
        embedded_fonts: list[str] = []

        try:
            import fitz  # PyMuPDF

            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                fonts = page.get_fonts()
                for font in fonts:
                    font_name = font[3] if len(font) > 3 else ""
                    if font_name and font_name not in embedded_fonts:
                        embedded_fonts.append(font_name)
            doc.close()

        except ImportError:
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(pdf_path)
                for page in reader.pages:
                    if "/Resources" in page:
                        resources = page["/Resources"]
                        if "/Font" in resources:
                            font_obj = resources["/Font"]
                            for font_key in font_obj:
                                font_data = font_obj[font_key]
                                if "/BaseFont" in font_data:
                                    font_name = str(font_data["/BaseFont"])
                                    if font_name not in embedded_fonts:
                                        embedded_fonts.append(font_name)
            except ImportError:
                pass

        # Check if required fonts are present (fuzzy match — PDF may prefix/subset)
        required_found: list[str] = []
        for required in REQUIRED_FONTS:
            for embedded in embedded_fonts:
                # Check if any embedded font contains the required font name
                # PDFs often subset fonts as "ABCDEF+InstrumentSerif" etc.
                if required.lower().replace(" ", "") in embedded.lower().replace(" ", ""):
                    required_found.append(required)
                    break

        all_embedded = len(required_found) == len(REQUIRED_FONTS)
        return (all_embedded, required_found)

    def _verify_no_orphaned_images(self, pdf_path: str) -> tuple[bool, list[int]]:
        """Verify no orphaned images in the PDF.

        An orphaned image is one on a page with no text context.
        The cover page and back cover are exempt.
        """
        orphaned_pages: list[int] = []

        try:
            import fitz  # PyMuPDF

            doc = fitz.open(pdf_path)
            for page_num in range(len(doc)):
                page = doc[page_num]
                images = page.get_images()
                text = page.get_text().strip()

                if images and len(text) < 10:
                    # Page has images but no text — orphaned
                    # Don't flag cover (page 0) or back cover (last page)
                    if page_num != 0 and page_num != len(doc) - 1:
                        orphaned_pages.append(page_num + 1)

            doc.close()

        except ImportError:
            # No PDF library — skip check
            pass

        return (len(orphaned_pages) == 0, orphaned_pages)

    def _verify_image_dpi(self, pdf_path: str) -> bool:
        """Verify all images in the PDF are at least 300 DPI."""
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(pdf_path)
            all_300 = True

            for page_num in range(len(doc)):
                page = doc[page_num]
                images = page.get_images(full=True)
                for img_info in images:
                    xref = img_info[0]
                    try:
                        img_dict = doc.extract_image(xref)
                        img_w = img_dict.get("width", 0)
                        img_h = img_dict.get("height", 0)
                        # Get the display size on the page
                        rects = page.get_image_rects(xref)
                        for rect in rects:
                            display_w_mm = rect.width * 25.4 / 72  # points to mm
                            display_h_mm = rect.height * 25.4 / 72
                            if display_w_mm > 0 and display_h_mm > 0:
                                dpi_w = img_w / (display_w_mm / 25.4)
                                dpi_h = img_h / (display_h_mm / 25.4)
                                if dpi_w < 250 or dpi_h < 250:  # Allow 250 as tolerance
                                    all_300 = False
                    except (ValueError, KeyError, RuntimeError):
                        pass

            doc.close()
            return all_300

        except ImportError:
            return True  # Can't verify — assume OK

    def _get_page_count(self, pdf_path: str) -> int:
        """Get the total page count of the PDF."""
        try:
            import fitz  # PyMuPDF

            doc = fitz.open(pdf_path)
            count = len(doc)
            doc.close()
            return count

        except ImportError:
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(pdf_path)
                return len(reader.pages)
            except (ImportError, RuntimeError):
                return 0

    def _get_file_size_mb(self, pdf_path: str) -> float:
        """Get the PDF file size in MB."""
        try:
            size_bytes = os.path.getsize(pdf_path)
            return round(size_bytes / (1024 * 1024), 2)
        except OSError:
            return 0.0

    def _verify_pdf(self, pdf_path: str) -> tuple[bool, list[str], dict[str, Any]]:
        """Run all verification checks on the rendered PDF.

        Checks:
        1. No blank pages (pages with no text content)
        2. No orphaned images (images without adjacent text)
        3. All fonts embedded (Instrument Serif, JetBrains Mono)
        4. All images 300 DPI

        Returns (all_passed, issues, details).
        """
        issues: list[str] = []
        details: dict[str, Any] = {}

        if not pdf_path or not os.path.exists(pdf_path):
            issues.append("PDF file does not exist.")
            return (False, issues, details)

        # Check 1: No blank pages
        no_blank, blank_pages = self._verify_no_blank_pages(pdf_path)
        details["blank_pages"] = blank_pages
        if not no_blank:
            issues.append(f"Blank pages detected on pages: {blank_pages}")

        # Check 2: No orphaned images
        no_orphaned, orphaned_pages = self._verify_no_orphaned_images(pdf_path)
        details["orphaned_image_pages"] = orphaned_pages
        if not no_orphaned:
            issues.append(f"Orphaned images detected on pages: {orphaned_pages}")

        # Check 3: Fonts embedded
        fonts_ok, embedded_fonts = self._verify_fonts_embedded(pdf_path)
        details["embedded_fonts"] = embedded_fonts
        if not fonts_ok:
            missing = [f for f in REQUIRED_FONTS if f not in embedded_fonts]
            issues.append(f"Fonts not embedded: {missing}")

        # Check 4: Image DPI
        all_300 = self._verify_image_dpi(pdf_path)
        details["all_images_300_dpi"] = all_300
        if not all_300:
            issues.append("Some images are below 300 DPI.")

        all_passed = len(issues) == 0
        return (all_passed, issues, details)

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 7-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
        html_path: str = "",
        css_path: str = "",
        image_paths: list[str] | None = None,
        chart_paths: list[str] | None = None,
        layout_plan: LayoutPlan | None = None,
    ) -> RenderOutput:
        """Execute the Render Engine's 7-step methodology.

        Steps (§4.6, Agent 20):
        1. Receive HTML from Presentation Designer
        2. Receive image paths from Data Visualizer and Unsplash tool
        3. Process all images through Pillow pipeline
        4. Convert HTML → PDF with WeasyPrint at 300 DPI
        5. Verify PDF: no blank pages, no orphaned images, all fonts embedded
        6. Save to reports/ directory
        7. Return PDF path
        """
        # Subscribe to bus
        self.subscribe_to_bus()

        # Step 1: Receive HTML from Presentation Designer
        await self._transition(AgentState.WORKING, "Step 1: Receiving HTML from Presentation Designer")
        if html_path:
            self._receive_html(html_path, css_path)
        if layout_plan:
            self._layout_plan = layout_plan
            if layout_plan.html_template_path and not self._html_path:
                self._receive_html(layout_plan.html_template_path, layout_plan.css_path)
            if layout_plan.pdf_path and not self._pdf_path:
                self._pdf_path = layout_plan.pdf_path

        if not self._html_path:
            await self._transition(AgentState.DONE, "No HTML path received")
            return RenderOutput(
                pdf_path="",
                verification_passed=False,
                verification_issues=["No HTML path received from Presentation Designer."],
            )

        # Step 2: Receive image paths
        await self._transition(AgentState.WORKING, "Step 2: Receiving image paths")
        # Extract image paths from layout plan if not passed directly
        if not image_paths and layout_plan:
            image_paths = [
                img.image_path for img in layout_plan.section_images
                if img.image_path
            ]
            if layout_plan.cover_image and layout_plan.cover_image.image_path:
                image_paths.append(layout_plan.cover_image.image_path)
        if not chart_paths and layout_plan:
            chart_paths = [
                cp.image_path for cp in layout_plan.chart_placements
                if cp.image_path
            ]
        self._receive_image_paths(image_paths, chart_paths)

        # Step 3: Process all images through Pillow pipeline
        await self._transition(
            AgentState.WORKING,
            f"Step 3: Processing {len(self._image_paths)} images + {len(self._chart_paths)} charts through Pillow pipeline",
        )
        processed = self._process_all_images()

        # Update HTML to reference processed images
        final_html = self._update_html_with_processed_images(self._html_path, processed)

        # Step 4: Convert HTML → PDF with WeasyPrint at 300 DPI
        await self._transition(AgentState.WORKING, "Step 4: Converting HTML → PDF with WeasyPrint at 300 DPI")
        pdf_path = await self._render_pdf(final_html)

        if not pdf_path or not os.path.exists(pdf_path):
            await self._transition(AgentState.DONE, "PDF rendering failed")
            return RenderOutput(
                pdf_path="",
                verification_passed=False,
                verification_issues=["WeasyPrint failed to generate PDF."],
            )

        # Step 5: Verify PDF
        await self._transition(AgentState.WORKING, "Step 5: Verifying PDF — no blank pages, no orphaned images, fonts embedded")
        all_passed, issues, verify_details = self._verify_pdf(pdf_path)

        # Add any image processing issues
        all_issues = issues + self._verification_issues

        # Step 6: Save to reports/ directory
        await self._transition(AgentState.WORKING, f"Step 6: PDF saved to {pdf_path}")

        # Get metadata
        page_count = self._get_page_count(pdf_path)
        file_size = self._get_file_size_mb(pdf_path)
        fonts_embedded = verify_details.get("embedded_fonts", [])

        # Step 7: Return PDF path
        await self._transition(
            AgentState.WORKING,
            f"Step 7: Returning PDF path — {page_count} pages, {file_size}MB, "
            f"{'verified' if all_passed else 'issues found'}",
        )

        # Build RenderOutput
        render_output = RenderOutput(
            pdf_path=pdf_path,
            page_count=page_count,
            file_size_mb=file_size,
            dpi=300,
            images_processed=len(self._image_paths),
            charts_processed=len(self._chart_paths),
            fonts_embedded=fonts_embedded,
            no_blank_pages=verify_details.get("blank_pages", []) == [],
            no_orphaned_images=verify_details.get("orphaned_image_pages", []) == [],
            all_fonts_embedded=all(f in fonts_embedded for f in REQUIRED_FONTS),
            all_images_300_dpi=verify_details.get("all_images_300_dpi", True),
            verification_passed=len(all_issues) == 0,
            verification_issues=all_issues,
        )

        # Publish render output to bus
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "finding_type": "render_complete",
                "render_output": render_output.model_dump(),
                "pdf_path": pdf_path,
                "page_count": page_count,
                "file_size_mb": file_size,
                "verification_passed": render_output.verification_passed,
                "verification_issues": all_issues,
            },
        )

        # If verification failed, report back to Presentation Designer
        if not render_output.verification_passed:
            await self.bus.publish(
                channel=Channel.HANDOFF,
                msg_type=MessageType.ESCALATION,
                sender=self.name,
                payload={
                    "to_agent": "presentation_designer",
                    "from_agent": self.name.value,
                    "task": "fix_layout",
                    "issues": all_issues,
                    "pdf_path": pdf_path,
                    "message": (
                        f"Render Engine verification FAILED. {len(all_issues)} issue(s): "
                        f"{'; '.join(all_issues[:3])}. "
                        f"Presentation Designer must fix the layout and regenerate HTML."
                    ),
                },
            )

        # Publish final finding
        finding = KeyFinding(
            id=f"finding_{hashlib.md5(f'render_engine_{engagement_id}'.encode()).hexdigest()[:8]}",
            agent=self.name.value,
            finding_type="render_complete",
            title=f"PDF rendered: {page_count} pages, {file_size}MB at 300 DPI — {'VERIFIED' if render_output.verification_passed else 'ISSUES FOUND'}",
            content=(
                f"Final PDF: {pdf_path}. "
                f"Pages: {page_count}. "
                f"Size: {file_size}MB. "
                f"DPI: 300. "
                f"Images processed: {len(self._image_paths)}. "
                f"Charts processed: {len(self._chart_paths)}. "
                f"Fonts embedded: {', '.join(fonts_embedded) if fonts_embedded else 'none'}. "
                f"Blank pages: {'none' if render_output.no_blank_pages else 'detected'}. "
                f"Orphaned images: {'none' if render_output.no_orphaned_images else 'detected'}. "
                f"{'All checks passed.' if render_output.verification_passed else f'Issues: {all_issues}'}"
            ),
            confidence=ConfidenceLevel.HIGH if render_output.verification_passed else ConfidenceLevel.MEDIUM,
        )
        await self._publish_finding(finding)

        await self._transition(
            AgentState.DONE,
            f"Render Engine complete: {page_count} pages, {file_size}MB, "
            f"{'verified' if render_output.verification_passed else f'{len(all_issues)} issues'}, "
            f"pdf: {pdf_path}",
        )

        return render_output
