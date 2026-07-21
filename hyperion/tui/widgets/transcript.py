"""HYPERION transcript — the copyable event log (Content-native RichLog).

The single most important fix over the old build: this is a *selectable*
widget. The previous log painted its own cells to a custom ``ScrollView`` and
exposed no text to Textual's selection engine, so nothing could ever be
copied. Here every line is a :class:`textual.content.Content`, written into a
:class:`RichLog` — so mouse click-drag highlights it and ``Ctrl+Shift+C``
copies it (OSC-52), and ``--no-mouse`` hands selection to the terminal itself.

Each event is one header line, optionally followed by nested detail lines:

    [HH:MM:SS]  BADGE      content ......
                           ├─ detail line
                           └─ detail line

Live rows (spinner / progress / aurora) are rewritten in place each animation
frame by splicing the RichLog line buffer, so the rest of the scrollback — and
the whole copy surface — stays intact.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from textual.content import Content
from textual.strip import Strip
from textual.widgets import RichLog

from hyperion.tui.content import build, line, span
from hyperion.tui.motion.indicators import aurora_spans, progress_line_spans, spinner_span
from hyperion.tui.theme import (
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    badge_color,
)

_AURORA_FPS = 30
_BADGE_CELL = 10  # fixed-width badge column


@dataclass
class LogRow:
    """A logical row. Returned to callers so they can mutate it live."""

    badge: str
    content: str
    detail: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    spinner: bool = False
    progress: tuple[int, int] | None = None
    aurora: bool = False
    icon: str = ""
    _line_index: int = -1  # header-line index inside the RichLog

    def animating(self) -> bool:
        return self.spinner or self.progress is not None or self.aurora


class Transcript(RichLog):
    """Scrollable, selectable, copyable badge-tagged event log."""

    DEFAULT_CSS = """
    Transcript {
        scrollbar-size: 1 1;
        scrollbar-color: #4A4640;
        scrollbar-color-hover: #d97757;
        scrollbar-background: #141413;
        background: #141413;
        padding: 0 2;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(
            markup=False,
            highlight=False,
            wrap=True,
            auto_scroll=True,
            **kwargs,
        )
        # _blocks preserves document order for copy: each item is either a
        # LogRow or a ("content", Content) tuple (logo / roster / raw block).
        self._blocks: list = []
        self._rows: list[LogRow] = []
        self._live: list[LogRow] = []
        self._frame = 0
        self._timer = None

    # ── public API ──────────────────────────────────────────────────────────

    def write_block(self, content: Content, *, blank_after: int = 0) -> None:
        """Write a raw Content block (logo, roster, separators) into the scroll.

        Kept in the copy surface via ``_blocks`` so select-all still captures it.
        """
        self._blocks.append(("content", content))
        self.write(content, scroll_end=True)
        for _ in range(blank_after):
            blank = Content("")
            self._blocks.append(("content", blank))
            self.write(blank, scroll_end=True)

    def add_row(self, row: LogRow) -> LogRow:
        self._rows.append(row)
        self._blocks.append(row)
        self._write_row(row)
        if row.animating():
            self._live.append(row)
            self._ensure_timer()
        return row

    def add_entry(
        self,
        badge: str,
        content: str,
        detail: list[str] | None = None,
        *,
        spinner: bool = False,
        progress: tuple[int, int] | None = None,
        aurora: bool = False,
        icon: str = "",
    ) -> LogRow:
        return self.add_row(
            LogRow(
                badge=badge,
                content=content,
                detail=detail or [],
                spinner=spinner,
                progress=progress,
                aurora=aurora,
                icon=icon,
            )
        )

    def update_row(
        self,
        row: LogRow,
        *,
        badge: str | None = None,
        content: str | None = None,
        spinner: bool | None = None,
        progress: tuple[int, int] | None = -1,  # type: ignore[assignment]
        aurora: bool | None = None,
        icon: str | None = None,
    ) -> None:
        if badge is not None:
            row.badge = badge
        if content is not None:
            row.content = content
        if spinner is not None:
            row.spinner = spinner
        if progress != -1:
            row.progress = progress  # type: ignore[assignment]
        if aurora is not None:
            row.aurora = aurora
        if icon is not None:
            row.icon = icon

        now_live = row.animating()
        if now_live and row not in self._live:
            self._live.append(row)
            self._ensure_timer()
        if not now_live and row in self._live:
            self._live.remove(row)
        self._rewrite_row(row)

    def clear(self) -> "Transcript":  # type: ignore[override]
        self._rows.clear()
        self._blocks.clear()
        self._live.clear()
        super().clear()
        return self

    def select_all(self) -> None:
        try:
            self.text_select_all()
        except Exception:
            pass

    # ── selection extraction (RichLog can't extract its own text) ─────────────

    def _plain_lines(self) -> list[str]:
        """Reconstruct the transcript as plain-text physical lines.

        RichLog only keeps rendered *strips*, so its built-in ``get_selection``
        returns nothing. We keep the logical ``_rows`` and rebuild the exact
        plain text here so drag-select / select-all / copy all work.
        """
        out: list[str] = []
        for block in self._blocks:
            if isinstance(block, tuple) and block and block[0] == "content":
                out.extend(str(block[1]).split("\n"))
            else:  # LogRow
                content = self._row_content(block)
                out.extend(str(content).split("\n"))
        return out

    def get_selection(self, selection):  # type: ignore[override]
        """Return (text, ending) for the selected region, extracted from rows."""
        try:
            text = "\n".join(self._plain_lines())
            return selection.extract(text), "\n"
        except Exception:
            return None

    def selected_text(self, selection) -> str:
        result = self.get_selection(selection)
        return result[0] if result else ""

    # ── content building ──────────────────────────────────────────────────────

    def _header_spans(self, row: LogRow) -> list:
        ts = time.strftime("[%H:%M:%S]", time.localtime(row.ts))
        spans = [span(ts + "  ", TEXT_DIM)]

        bcolor = badge_color(row.badge)
        if row.spinner:
            spans.append(span(*spinner_span(self._frame)))
            label = row.badge.upper()[: _BADGE_CELL - 2]
            spans.append(span(" " + label, f"bold {bcolor}"))
            pad = _BADGE_CELL - (2 + len(label))
        else:
            label = row.badge.upper()[:_BADGE_CELL]
            spans.append(span(label, f"bold {bcolor}"))
            pad = _BADGE_CELL - len(label)
        if pad > 0:
            spans.append(span(" " * pad, ""))
        spans.append(span("  ", ""))

        if row.progress is not None:
            done, total = row.progress
            spans.extend(progress_line_spans(row.content, done, total))
        elif row.aurora:
            spans.extend(aurora_spans(self._frame))
            spans.append(span("  " + row.content, TEXT_PRIMARY))
        else:
            if row.icon:
                spans.append(span(row.icon + " ", f"bold {bcolor}"))
            spans.append(span(row.content, TEXT_PRIMARY))
        return spans

    def _row_lines(self, row: LogRow) -> list:
        lines = [self._header_spans(row)]
        for i, d in enumerate(row.detail):
            glyph = "└─" if i == len(row.detail) - 1 else "├─"
            lines.append(
                [span("              " + glyph + " ", TEXT_GHOST), span(d, TEXT_DIM)]
            )
        return lines

    def _row_content(self, row: LogRow) -> Content:
        return build(self._row_lines(row))

    # ── write / rewrite ────────────────────────────────────────────────────────

    def _write_row(self, row: LogRow) -> None:
        row._line_index = len(self.lines)
        self.write(self._row_content(row), scroll_end=True)

    def _rewrite_row(self, row: LogRow) -> None:
        """Rewrite the physical lines belonging to ``row`` in place.

        Mirrors RichLog.write's own render path (console.render → split_lines →
        Strip.from_lines) so the spliced strips are byte-identical in shape to
        freshly-written ones — keeping the copy surface consistent.
        """
        try:
            from rich.segment import Segment

            start = row._line_index
            if start < 0:
                return
            console = self.app.console
            render_options = console.options
            width = max(
                1,
                self.scrollable_content_region.width or self.size.width or 80,
            )
            render_options = render_options.update_width(width)

            content = self._row_content(row)
            segments = console.render(content, render_options)
            new_lines = list(Segment.split_lines(segments))
            strips = Strip.from_lines(new_lines)
            for offset, strip in enumerate(strips):
                idx = start + offset
                if 0 <= idx < len(self.lines):
                    self.lines[idx] = strip.adjust_cell_length(width)
            self.refresh()
        except Exception:
            self.refresh()

    # ── animation loop (runs only while a row animates) ───────────────────────

    def _ensure_timer(self) -> None:
        if self._timer is None:
            self._timer = self.set_interval(1 / _AURORA_FPS, self._on_frame)

    def _on_frame(self) -> None:
        self._frame += 1
        if not self._live:
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            return
        for row in list(self._live):
            self._rewrite_row(row)
