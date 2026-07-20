"""HYPERION log stream. Spec §6 (log stream) + §7 (badges) + §8 (motion).

Each event is one row:  [HH:MM:SS]  BADGE   content ......
Rows support:
  - 220 ms fade-in (§8, expo-out) on arrival
  - a live braille spinner in the badge column while a row is "active" (§8.1)
  - a determinate gradient progress bar (§8.1 Tier 2)
  - an indeterminate aurora bar (§8.1 Tier 3)
  - nested tree detail lines (├─ / └─) in text.ghost (§10)

Rendering is a single Rich Text rebuilt each animation frame, but we ONLY run
the frame timer while at least one row is animating (fade / spinner / bar) —
idle cost is zero repaints (§12 performance budget).
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from rich.text import Text
from textual.scroll_view import ScrollView
from textual.geometry import Size

from hyperion.tui.motion.easing import expo_out
from hyperion.tui.motion.indicators import aurora_bar, progress_line, spinner_frame
from hyperion.tui.theme import (
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    badge_color,
)

_FADE_MS = 220.0
_SPIN_MS = 90.0
_AURORA_FPS = 30
_BADGE_CELL = 10  # fixed-width badge column (§7.3)


@dataclass
class LogRow:
    badge: str
    content: str
    detail: list[str] = field(default_factory=list)
    ts: float = field(default_factory=time.time)
    born: float = field(default_factory=time.monotonic)
    # live indicators
    spinner: bool = False
    progress: tuple[int, int] | None = None  # (done, total)
    aurora: bool = False
    icon: str = ""  # optional leading glyph in content (✓ ✗ ▸ …)

    def animating(self) -> bool:
        faded = (time.monotonic() - self.born) * 1000.0 >= _FADE_MS
        return (not faded) or self.spinner or self.progress is not None or self.aurora


class LogStream(ScrollView):
    """Scrollable badge-tagged event log."""

    DEFAULT_CSS = """
    LogStream {
        scrollbar-size: 1 1;
        scrollbar-color: #2A3350;
        scrollbar-background: #0A0E1A;
    }
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._rows: list[LogRow] = []
        self._tick = 0
        self._timer = None

    def on_mount(self) -> None:
        self._ensure_timer()

    # ── public API ───────────────────────────────────────────────────────────

    def add_row(self, row: LogRow) -> LogRow:
        self._rows.append(row)
        self._recompute_size()
        self._ensure_timer()
        self.scroll_end(animate=False)
        self.refresh()
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
        progress: tuple[int, int] | None = -1,  # sentinel; None means clear
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
            row.progress = progress
        if aurora is not None:
            row.aurora = aurora
        if icon is not None:
            row.icon = icon
        self._ensure_timer()
        self.refresh()

    def clear(self) -> None:
        self._rows.clear()
        self._recompute_size()
        self.refresh()

    # ── animation loop (only runs while something is animating) ───────────────

    def _ensure_timer(self) -> None:
        if self._timer is None:
            self._timer = self.set_interval(1 / _AURORA_FPS, self._frame)

    def _frame(self) -> None:
        self._tick += 1
        if any(r.animating() for r in self._rows):
            self.refresh()
        else:
            # go idle — no repaints until next event (§12)
            if self._timer is not None:
                self._timer.stop()
                self._timer = None

    # ── size / rendering ──────────────────────────────────────────────────────

    def _recompute_size(self) -> None:
        lines = sum(1 + len(r.detail) for r in self._rows)
        width = self.size.width or 72
        self.virtual_size = Size(width, max(lines, 1))

    def _render_row(self, row: LogRow, out: Text) -> None:
        now_ms = (time.monotonic() - row.born) * 1000.0
        alpha = expo_out(min(1.0, now_ms / _FADE_MS)) if now_ms < _FADE_MS else 1.0

        # Timestamp (10 chars, text.dim). §7.3
        ts = time.strftime("[%H:%M:%S]", time.localtime(row.ts))
        out.append(ts + "  ", style=self._fade(TEXT_DIM, alpha))

        # Badge column — spinner replaces the label glyph position (§8.1/§9.7).
        bcolor = badge_color(row.badge)
        if row.spinner:
            spin = spinner_frame(self._tick // 1)
            out.append_text(spin)
            label = row.badge.upper()[: _BADGE_CELL - 2]
            out.append(" " + label, style=f"bold {self._fade(bcolor, alpha)}")
            pad = _BADGE_CELL - (1 + 1 + len(label))
        else:
            label = row.badge.upper()[:_BADGE_CELL]
            out.append(label, style=f"bold {self._fade(bcolor, alpha)}")
            pad = _BADGE_CELL - len(label)
        if pad > 0:
            out.append(" " * pad)
        out.append("  ")

        # Content
        if row.progress is not None:
            done, total = row.progress
            out.append_text(progress_line(row.content, done, total))
        elif row.aurora:
            out.append_text(aurora_bar(self._tick))
            out.append("  " + row.content, style=self._fade(TEXT_PRIMARY, alpha))
        else:
            if row.icon:
                out.append(row.icon + " ", style=f"bold {self._fade(bcolor, alpha)}")
            out.append(row.content, style=self._fade(TEXT_PRIMARY, alpha))
        out.append("\n")

        # Nested tree detail (§10)
        for i, d in enumerate(row.detail):
            glyph = "└─" if i == len(row.detail) - 1 else "├─"
            out.append("              " + glyph + " ", style=self._fade(TEXT_GHOST, alpha))
            out.append(d, style=self._fade(TEXT_DIM, alpha))
            out.append("\n")

    @staticmethod
    def _fade(hex_color: str, alpha: float):
        """Blend a colour toward the canvas bg to emulate fade-in opacity."""
        if alpha >= 1.0:
            return hex_color
        from hyperion.tui.motion.color import mix
        from hyperion.tui.theme import BG_CANVAS

        return mix(BG_CANVAS, hex_color, max(0.0, min(1.0, alpha)))

    def render_lines(self, crop):  # type: ignore[override]
        self._recompute_size()
        return super().render_lines(crop)

    def render(self) -> Text:
        out = Text()
        for row in self._rows:
            self._render_row(row, out)
        return out
