"""HYPERION deliverable widget — rendered markdown report view.

Displays the final report as rendered markdown (Rich Markdown widget) with:
    - Export button (save as PDF, save as markdown)
    - Quality score display (10-dimension rubric scores)
    - Engagement metadata (duration, agents used, sources accessed)
    - "Open PDF" button (opens the generated PDF in the system viewer)

Per ARCHITECTURE.md §8.2 (Deliverable View).
"""

from __future__ import annotations

from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static
from textual.widget import Widget
from textual.message import Message

from hyperion.tui.content import build, build_line, line, span
from hyperion.tui.theme import (
    CLAY,
    CLAY_DEEP,
    SAGE,
    SIG_ERROR,
    SIG_SUCCESS,
    SIG_WARN,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)


class ExportRequested(Message):
    """User clicked export."""
    def __init__(self, fmt: str) -> None:
        self.fmt = fmt
        super().__init__()


class OpenPDFRequested(Message):
    """User clicked open PDF."""
    pass


class DeliverableView(Widget):
    """Report deliverable view — markdown render + export + quality scores.

    Shows:
        - Rendered markdown of the final report
        - Quality score display (10-dimension rubric)
        - Engagement metadata
        - Export buttons (PDF, markdown, JSON)
        - Open PDF button
    """

    DEFAULT_CSS = """
    DeliverableView {
        layout: vertical;
        width: 100%;
        height: 1fr;
        padding: 1 2;
    }
    DeliverableView > #dv-header {
        height: 2;
        width: 100%;
    }
    DeliverableView > #dv-report {
        height: 1fr;
        width: 100%;
        overflow-y: auto;
        scrollbar-size: 1 1;
    }
    DeliverableView > #dv-quality {
        height: auto;
        min-height: 3;
        width: 100%;
        padding: 1 0;
    }
    DeliverableView > #dv-actions {
        height: 3;
        width: 100%;
        padding: 1 0;
    }
    DeliverableView > #dv-actions > Horizontal {
        height: 1;
        width: 100%;
    }
    DeliverableView Button {
        margin: 0 1;
        min-width: 16;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._report_md: str = ""
        self._quality_scores: dict[str, float] = {}
        self._quality_total: float = 0.0
        self._quality_iterations: int = 0
        self._metadata: dict[str, Any] = {}
        self._pdf_path: str = ""

    def compose(self) -> ComposeResult:
        yield Static(self._build_header(), id="dv-header")
        yield Static(self._render_markdown(), id="dv-report")
        yield Static(self._build_quality(), id="dv-quality")
        with Vertical(id="dv-actions"):
            with Horizontal():
                yield Button("Export PDF", id="btn-pdf", variant="primary")
                yield Button("Export Markdown", id="btn-md", variant="default")
                yield Button("Export JSON", id="btn-json", variant="default")
                yield Button("Open PDF", id="btn-open", variant="success")

    # ── public API ────────────────────────────────────────────────────────────

    def set_report(
        self,
        markdown: str,
        quality_scores: dict[str, float] | None = None,
        quality_total: float = 0.0,
        quality_iterations: int = 0,
        metadata: dict[str, Any] | None = None,
        pdf_path: str = "",
    ) -> None:
        self._report_md = markdown
        self._quality_scores = quality_scores or {}
        self._quality_total = quality_total
        self._quality_iterations = quality_iterations
        self._metadata = metadata or {}
        self._pdf_path = pdf_path
        self._refresh_all()

    def _refresh_all(self) -> None:
        try:
            self.query_one("#dv-header", Static).update(self._build_header())
            self.query_one("#dv-report", Static).update(self._render_markdown())
            self.query_one("#dv-quality", Static).update(self._build_quality())
        except Exception:
            pass

    # ── builders ──────────────────────────────────────────────────────────────

    def _build_header(self):
        lines: list = []
        lines.append([
            span("  DELIVERABLE", f"bold {CLAY}"),
            span("  ·  ", TEXT_GHOST),
        ])
        if self._pdf_path:
            lines[0].append(span(f"pdf → {self._pdf_path}", TEXT_DIM))
        else:
            lines[0].append(span("no report generated yet", TEXT_GHOST))
        return build(lines)

    def _render_markdown(self):
        """Render the report markdown as Content.

        For now, we render it as plain text with basic styling.
        A full Rich Markdown widget could be substituted here.
        """
        if not self._report_md:
            return build([line(span("  No report content yet.", TEXT_GHOST))])

        lines: list = []
        for md_line in self._report_md.split("\n"):
            if md_line.startswith("# "):
                lines.append([span(md_line[2:], f"bold {CLAY}")])
            elif md_line.startswith("## "):
                lines.append([span(md_line[3:], f"bold {TEXT_SECONDARY}")])
            elif md_line.startswith("### "):
                lines.append([span(md_line[4:], f"bold {TEXT_DIM}")])
            elif md_line.startswith("> "):
                lines.append([span("  " + md_line[2:], TEXT_DIM)])
            elif md_line.startswith("- ") or md_line.startswith("* "):
                lines.append([span("  • ", CLAY), span(md_line[2:], TEXT_PRIMARY)])
            else:
                lines.append([span("  " + md_line, TEXT_PRIMARY)])
        return build(lines)

    def _build_quality(self):
        lines: list = []

        if not self._quality_scores:
            lines.append([span("  QUALITY  no scores yet", TEXT_GHOST)])
            return build(lines)

        verdict = "PASS" if self._quality_total >= 4.0 else "FAIL"
        vc = SIG_SUCCESS if verdict == "PASS" else SIG_ERROR

        lines.append([
            span("  QUALITY  ", f"bold {CLAY}"),
            span(f"{self._quality_total:.1f}/5.0", f"bold {TEXT_PRIMARY}"),
            span(f"  {verdict}", f"bold {vc}"),
            span(f"  ·  {self._quality_iterations} iteration(s)", TEXT_DIM),
        ])

        for dim, score in self._quality_scores.items():
            sc = SIG_SUCCESS if score >= 4 else SIG_WARN if score >= 3 else SIG_ERROR
            bar_filled = int(round(score / 5 * 8))
            bar = "█" * bar_filled + "░" * (8 - bar_filled)
            lines.append([
                span(f"  {dim[:20].ljust(20)}  ", TEXT_DIM),
                span(bar, sc),
                span(f"  {score:.0f}/5", sc),
            ])

        return build(lines)

    # ── button handlers ───────────────────────────────────────────────────────

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id or ""
        if btn_id == "btn-pdf":
            self.post_message(ExportRequested("pdf"))
        elif btn_id == "btn-md":
            self.post_message(ExportRequested("markdown"))
        elif btn_id == "btn-json":
            self.post_message(ExportRequested("json"))
        elif btn_id == "btn-open":
            self.post_message(OpenPDFRequested())
