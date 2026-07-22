"""HYPERION deliverable screen — report view + export + quality scores.

Per ARCHITECTURE.md §8.2 (Deliverable View):

    Deliverable View:
    - Rendered markdown of the final report (Rich Markdown widget)
    - Export button (save as PDF, save as markdown)
    - Quality score display (10-dimension rubric scores)
    - Engagement metadata (duration, agents used, sources accessed)
    - "Open PDF" button (opens the generated PDF in the system viewer)
"""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from hyperion.tui.content import build, build_line, line, span
from hyperion.tui.theme import (
    CLAY,
    SAGE,
    SIG_SUCCESS,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from hyperion.tui.widgets.deliverable import (
    DeliverableView,
    ExportRequested,
    OpenPDFRequested,
)
from hyperion.tui.widgets.mark import Mark, MarkState
from hyperion.tui.widgets.rule import hr


class DeliverableScreen(Screen):
    """Report deliverable view — markdown, quality scores, export, open PDF."""

    DEFAULT_CSS = """
    DeliverableScreen {
        layout: vertical;
        background: #141413;
        color: #F4F3EE;
    }
    #del-header {
        dock: top;
        height: 2;
        width: 100%;
    }
    #del-body {
        height: 1fr;
        width: 100%;
    }
    #del-footer {
        dock: bottom;
        height: 1;
        width: 100%;
    }
    """

    BINDINGS = [
        Binding("escape", "back", "Back", show=True),
        Binding("ctrl+e", "export_pdf", "Export PDF", show=False),
    ]

    def __init__(self, engagement_result: Any | None = None, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._result = engagement_result

    def compose(self) -> ComposeResult:
        with Vertical(id="del-header"):
            yield Static(self._build_header(), id="del-title")
            yield Static(hr(), id="del-rule")
        yield DeliverableView(id="del-body")
        yield Static(
            build_line(
                span("  esc", f"bold {CLAY}"),
                span(" back to engagement  ·  ", TEXT_DIM),
                span("ctrl+e", f"bold {CLAY}"),
                span(" export PDF", TEXT_DIM),
            ),
            id="del-footer",
        )

    def on_mount(self) -> None:
        dv = self.query_one("#del-body", DeliverableView)
        if self._result:
            self._populate(dv)

    def _build_header(self):
        return build_line(
            span("  DELIVERABLE", f"bold {CLAY}"),
            span("  ·  ", TEXT_GHOST),
            span("engagement complete", SAGE),
        )

    def _populate(self, dv: DeliverableView) -> None:
        """Extract report data from EngagementResult and populate the view."""
        result = self._result
        md_text = ""
        quality_scores: dict[str, float] = {}
        quality_total = 0.0
        quality_iterations = 0
        metadata: dict[str, Any] = {}
        pdf_path = ""

        try:
            fr = result.final_report
            # Build markdown from report
            if fr:
                rec = getattr(getattr(fr, "recommendation", None), "value", "see report")
                conf = getattr(getattr(fr, "confidence", None), "value", "")
                md_text = f"# Executive Summary\n\nRecommendation: {rec}"
                if conf:
                    md_text += f" ({conf} confidence)"
                md_text += "\n\n"
                # Add sections if available
                sections = getattr(fr, "sections", []) or []
                for sec in sections:
                    title = getattr(sec, "title", "Section")
                    content = getattr(sec, "content", "")
                    md_text += f"## {title}\n\n{content}\n\n"

            # Quality scores
            if result.quality_score:
                qs = result.quality_score
                quality_total = getattr(qs, "total_score", 0.0)
                quality_iterations = result.quality_iterations
                dims = getattr(qs, "dimensions", {}) or {}
                for dim_name, dim_obj in dims.items():
                    score = getattr(dim_obj, "score", 0.0)
                    quality_scores[dim_name] = score

            # Metadata
            metadata = {
                "duration": f"{result.duration_seconds:.0f}s",
                "agents": len(getattr(result, "agent_outputs", {}) or {}),
                "pdf": result.pdf_path or "",
            }
            pdf_path = result.pdf_path or ""
        except Exception:
            pass

        dv.set_report(
            markdown=md_text,
            quality_scores=quality_scores,
            quality_total=quality_total,
            quality_iterations=quality_iterations,
            metadata=metadata,
            pdf_path=pdf_path,
        )

    # ── event handlers ──────────────────────────────────────────────────────────

    def on_export_requested(self, event: ExportRequested) -> None:
        """Handle export button clicks."""
        try:
            self.app.notify(f"Exporting {event.fmt}…", timeout=2)
        except Exception:
            pass

    def on_open_pdf_requested(self, event: OpenPDFRequested) -> None:
        """Open the PDF in the system viewer."""
        pdf_path = ""
        try:
            dv = self.query_one("#del-body", DeliverableView)
            pdf_path = dv._pdf_path
        except Exception:
            pass

        if not pdf_path:
            try:
                self.app.notify("No PDF available to open", timeout=2)
            except Exception:
                pass
            return

        try:
            if sys.platform == "win32":
                os.startfile(pdf_path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", pdf_path])
            else:
                subprocess.Popen(["xdg-open", pdf_path])
        except Exception as e:
            try:
                self.app.notify(f"Failed to open PDF: {e}", timeout=3)
            except Exception:
                pass

    # ── actions ──────────────────────────────────────────────────────────────────

    def action_back(self) -> None:
        """Return to the engagement screen."""
        self.app.pop_screen()

    def action_export_pdf(self) -> None:
        """Shortcut to export PDF."""
        dv = self.query_one("#del-body", DeliverableView)
        dv.post_message(ExportRequested("pdf"))
