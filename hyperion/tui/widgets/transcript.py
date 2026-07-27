"""HYPERION transcript — the copyable event log.

Selection contract
------------------
This widget owns a *physical line* model: ``self.lines`` (rendered strips) and
``self._plain`` (the plain text of each strip) are kept index-for-index in step.
Everything about copy/selection depends on that invariant.

Three defects made "select and keep scrolling" impossible before, all fixed here:

1. ``render_line`` was inherited from ``RichLog``, which — unlike Textual's own
   ``Log`` widget — never calls :meth:`Strip.apply_offsets`. Without offsets the
   compositor cannot map a screen coordinate back to a document offset, so
   ``Screen.get_widget_and_offset_at`` returned ``content_offset=None`` and
   ``Screen._watch__select_state`` fell through to its ``SELECT_ALL`` branch.
   Every drag, however small, collapsed to "select the whole widget", and
   dragging past the edge could not extend a selection because there was no
   anchor to extend *from*. We now render offsets and paint the
   ``screen--selection`` component style, exactly as ``Log`` does.

2. ``LogRow._line_index`` was captured as ``len(self.lines)`` at write time. But
   ``RichLog.write`` *defers* rendering until the widget's size is known, so
   every row written during ``on_mount`` (the whole intro banner) recorded index
   0. Rewriting any of them spliced over line 0. Indices are now assigned when
   the lines physically materialise, and the write path no longer depends on
   Textual's deferral behaviour.

3. Rewriting a live row spliced ``len(new_strips)`` entries over the old ones.
   When a row grew — a spinner line whose content got longer, which is the
   normal case — the extra physical lines were dropped on the floor and the
   scrollback silently lost content. Rewrites now splice by replacing the row's
   old span with its new span, growing or shrinking the buffer and shifting the
   indices of every following row.

Each event is one header line, optionally followed by nested detail lines:

    [HH:MM:SS]  BADGE      content ......
                           ├─ detail line
                           └─ detail line
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich.style import Style
from rich.text import Text
from textual.content import Content
from textual.geometry import Size
from textual.scroll_view import ScrollView
from textual.strip import Strip
from textual.visual import Visual

from hyperion.tui.content import build, span
from hyperion.tui.motion.indicators import aurora_spans, progress_line_spans, spinner_span
from hyperion.tui.theme import (
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    badge_color,
)

_AURORA_FPS = 30
_BADGE_CELL = 10  # fixed-width badge column
_MIN_WIDTH = 20


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
    content_spans: list[tuple[str, str]] | None = None  # colored spans for content
    # Physical span occupied by this row: [start, start + span_len).
    # Assigned when the row's lines actually materialise, never guessed.
    _line_index: int = -1
    _span_len: int = 0

    def animating(self) -> bool:
        return self.spinner or self.progress is not None or self.aurora


class Transcript(ScrollView):
    """Scrollable, selectable, copyable badge-tagged event log.

    Subclasses :class:`ScrollView` directly rather than ``RichLog`` because the
    selection fixes above require owning the line buffer and the render path.
    The public surface (``write_block`` / ``add_row`` / ``add_entry`` /
    ``update_row`` / ``clear`` / ``select_all`` / ``get_selection``) is
    unchanged, and ``lines`` is still exposed for callers that inspect it.
    """

    ALLOW_SELECT = True

    DEFAULT_CSS = """
    Transcript {
        scrollbar-size: 1 1;
        scrollbar-color: #4A4640;
        scrollbar-color-hover: #d97757;
        scrollbar-background: #141413;
        background: #141413;
        padding: 0 2;
        overflow-y: scroll;
    }
    """

    def __init__(self, *, auto_scroll: bool = True, **kwargs) -> None:
        super().__init__(**kwargs)
        self.auto_scroll = auto_scroll
        # Physical model. These two lists are always the same length.
        self.lines: list[Strip] = []
        self._plain: list[str] = []
        # _blocks preserves document order for copy: each item is either a
        # LogRow or a ("content", Content) tuple (logo / roster / raw block).
        self._blocks: list = []
        self._rows: list[LogRow] = []
        self._live: list[LogRow] = []
        self._frame = 0
        self._timer = None
        self._widest = 0
        self._render_cache: dict[int, Strip] = {}
        # Pending writes, replayed once a width is known. We manage this
        # ourselves so row indices are correct rather than all-zero.
        self._pending: list = []
        self._width_known = False

    # ── geometry ────────────────────────────────────────────────────────────

    def _render_width(self) -> int:
        return max(_MIN_WIDTH, self.scrollable_content_region.width or self.size.width)

    def on_resize(self) -> None:
        first = not self._width_known
        self._width_known = True
        if first and self._pending:
            pending, self._pending = self._pending, []
            for item in pending:
                self._emit(item)
            self._refresh_virtual_size()
        elif not first:
            # Width changed: wrapping changed, so every physical line is stale.
            self._reflow()

    # ── public API ──────────────────────────────────────────────────────────

    def write_block(self, content: Content, *, blank_after: int = 0) -> None:
        """Write a raw Content block (logo, roster, separators) into the scroll."""
        self._append(("content", content))
        for _ in range(blank_after):
            self._append(("content", Content("")))

    def write(self, content, **_kwargs) -> "Transcript":
        """RichLog-compatible write, so existing callers keep working."""
        if isinstance(content, str):
            content = Content(content)
        self.write_block(content)
        return self

    def add_row(self, row: LogRow) -> LogRow:
        self._rows.append(row)
        self._append(row)
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
        content_spans: list[tuple[str, str]] | None = None,
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
                content_spans=content_spans,
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

    def clear(self) -> "Transcript":
        self._rows.clear()
        self._blocks.clear()
        self._live.clear()
        self.lines.clear()
        self._plain.clear()
        self._pending.clear()
        self._render_cache.clear()
        self._widest = 0
        self.virtual_size = Size(0, 0)
        self.refresh()
        return self

    def select_all(self) -> None:
        """Select the whole transcript, including scrolled-off history.

        ``SELECT_ALL`` is ``Selection(None, None)`` — an unbounded selection,
        which :meth:`Selection.extract` widens to the full text. Using explicit
        line numbers here would cap the selection at whatever was rendered.
        """
        from textual.selection import SELECT_ALL

        try:
            self.screen.selections = {self: SELECT_ALL}
            self._render_cache.clear()
            self.refresh()
        except Exception:
            try:
                self.text_select_all()
            except Exception:
                pass

    # ── selection ──────────────────────────────────────────────────────────

    @property
    def text_selection(self):
        """The active selection for this widget, if any."""
        try:
            return self.screen.selections.get(self)
        except Exception:
            return None

    def _plain_lines(self) -> list[str]:
        """The transcript as plain-text physical lines.

        This is the copy surface, and it is index-aligned with ``self.lines``,
        so a selection expressed in screen/document rows maps to exactly the
        text the user highlighted — including rows scrolled out of view.
        """
        return list(self._plain)

    def get_selection(self, selection):  # type: ignore[override]
        """Return ``(text, ending)`` for the selected region."""
        try:
            text = "\n".join(self._plain)
            return selection.extract(text), "\n"
        except Exception:
            return None

    def selected_text(self, selection) -> str:
        result = self.get_selection(selection)
        return result[0] if result else ""

    # ── content building ───────────────────────────────────────────────────

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
            if row.content_spans:
                for s_text, s_style in row.content_spans:
                    spans.append(span(s_text, s_style))
            else:
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

    # ── physical line production ───────────────────────────────────────────

    def _block_content(self, block) -> Content:
        return block[1] if isinstance(block, tuple) else self._row_content(block)

    def _render_block(self, block) -> tuple[list[Strip], list[str]]:
        """Render one block to ``(strips, plain_lines)`` at the current width.

        Uses ``Visual.to_strips``, which is the path Textual itself uses: it
        honours the widget's ``text_wrap`` rule, so a long line wraps into
        several physical lines instead of overflowing. Wrapping has to happen
        here — the plain-text mirror must be one entry per *physical* line, or
        selection offsets drift away from what is on screen.
        """
        content = self._block_content(block)
        width = self._render_width()
        strips = Visual.to_strips(
            self,
            content,
            width,
            None,
            self.visual_style,
            apply_selection=False,
            pad=True,
        )
        if not strips:
            return ([Strip.blank(width)], [""])
        plain = [strip.text for strip in strips]
        return (strips, plain)

    def _append(self, block) -> None:
        """Append a block, deferring only if no width is known yet."""
        self._blocks.append(block)
        if not self._width_known:
            self._pending.append(block)
            return
        self._emit(block)
        self._refresh_virtual_size()

    def _emit(self, block) -> None:
        """Materialise a block's physical lines and record its span."""
        strips, plain = self._render_block(block)
        start = len(self.lines)
        self.lines.extend(strips)
        self._plain.extend(plain)
        if not isinstance(block, tuple):
            # Indices assigned HERE — after the lines exist — so rows written
            # before the widget had a size get real indices, not 0.
            block._line_index = start
            block._span_len = len(strips)
        self._widest = max(self._widest, *(len(p) for p in plain)) if plain else self._widest

    def _refresh_virtual_size(self) -> None:
        self.virtual_size = Size(self._widest, len(self.lines))
        self._render_cache.clear()
        if self.auto_scroll:
            self.scroll_end(animate=False, immediate=False, x_axis=False)
        self.refresh()

    def _reflow(self) -> None:
        """Re-render every block after a width change."""
        self.lines.clear()
        self._plain.clear()
        self._widest = 0
        for block in self._blocks:
            self._emit(block)
        self._refresh_virtual_size()

    def _rewrite_row(self, row: LogRow) -> None:
        """Replace a row's physical span in place, growing/shrinking as needed."""
        if row._line_index < 0:
            return  # not materialised yet; the pending replay will render it
        try:
            strips, plain = self._render_block(row)
            start = row._line_index
            old_len = row._span_len
            end = start + old_len
            if start > len(self.lines):
                return

            self.lines[start:end] = strips
            self._plain[start:end] = plain

            delta = len(strips) - old_len
            row._span_len = len(strips)
            if delta:
                # Every later row moved. Without this the buffer and the row
                # indices disagree and subsequent rewrites corrupt the log.
                for other in self._rows:
                    if other is not row and other._line_index > start:
                        other._line_index += delta
                self._refresh_virtual_size()
            else:
                self._render_cache.clear()
                self.refresh_lines(start, len(strips))
        except Exception:
            self.refresh()

    # ── rendering ──────────────────────────────────────────────────────────

    def _render_line_strip(self, y: int, rich_style: Style) -> Strip:
        """Render physical line ``y``, painting the selection highlight.

        Mirrors ``textual.widgets.Log._render_line_strip``. Painting matters as
        much as extraction: without it the user drags across the log and sees
        nothing highlighted, so there is no feedback that a selection larger
        than the viewport is being built.
        """
        if y >= len(self.lines):
            return Strip.blank(self._render_width(), rich_style)

        selection = self.text_selection
        if selection is None:
            return self.lines[y]

        if y in self._render_cache:
            return self._render_cache[y]

        select_span = selection.get_span(y)
        if select_span is None:
            return self.lines[y]

        start, end = select_span
        plain = self._plain[y]
        if end == -1:
            end = len(plain)
        try:
            selection_style = self.screen.get_component_rich_style("screen--selection")
        except Exception:
            selection_style = Style(reverse=True)

        text = Text(plain, no_wrap=True, end="")
        text.stylize(rich_style)
        text.stylize(selection_style, start, end)
        strip = Strip(text.render(self.app.console), len(plain))
        self._render_cache[y] = strip
        return strip

    def render_line(self, y: int) -> Strip:
        """Render a visible line, tagged with document offsets.

        ``apply_offsets`` is the whole fix for "can only copy what's on screen":
        it stamps each segment with its (x, y) document position, which is what
        lets the compositor answer ``get_widget_and_offset_at`` with a real
        offset. With a real offset, ``Screen`` builds a precise anchored
        ``Selection`` and extends it as the user drags and auto-scrolls, instead
        of collapsing to ``SELECT_ALL``.
        """
        scroll_x, scroll_y = self.scroll_offset
        y_doc = scroll_y + y
        rich_style = self.rich_style
        strip = self._render_line_strip(y_doc, rich_style)
        strip = strip.crop_extend(scroll_x, scroll_x + self.size.width, rich_style)
        strip = strip.apply_offsets(scroll_x, y_doc)
        return strip

    def refresh_lines(self, y_start: int, line_count: int = 1) -> None:
        for y in range(y_start, y_start + line_count):
            self._render_cache.pop(y, None)
        super().refresh_lines(y_start, line_count=line_count)

    def _watch_text_selection(self) -> None:
        self._render_cache.clear()
        self.refresh()

    # ── animation loop (runs only while a row animates) ────────────────────

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
