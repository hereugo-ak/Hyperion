"""HYPERION horizontal rules (Content-native).

- ``hr(width)`` — a static rule as Content (for use inside a Static).
- Rule — a full-width divider widget that can animate its draw-in.
- PhaseRule — a phase announcement: thin rule with a centred, letter-spaced
  label:  ─────  P H A S E   2 · E X E C U T E  ─────
"""

from __future__ import annotations

import time

from textual.widgets import Static

from hyperion.tui.content import build_line, span
from hyperion.tui.motion.color import mix
from hyperion.tui.motion.easing import expo_out
from hyperion.tui.theme import BG_CANVAS, BORDER_SUBTLE, TEXT_SECONDARY

HR_WIDTH = 69
_DRAW_MS = 320.0
_FPS = 30


def hr(width: int = HR_WIDTH):
    """A static rule as Content."""
    return build_line(span("─" * width, BORDER_SUBTLE))


class Rule(Static):
    """A horizontal rule that can animate its draw-in."""

    DEFAULT_CSS = """
    Rule { height: 1; width: 100%; }
    """

    def __init__(self, animate: bool = False, **kwargs) -> None:
        self._animate = animate
        self._t0 = time.monotonic()
        self._timer = None
        super().__init__(self._build(HR_WIDTH), **kwargs)

    def on_mount(self) -> None:
        if self._animate:
            self._timer = self.set_interval(1 / _FPS, self._frame)
        else:
            self.update(self._build(self.size.width or HR_WIDTH))

    def _frame(self) -> None:
        if (time.monotonic() - self._t0) * 1000.0 >= _DRAW_MS:
            if self._timer:
                self._timer.stop()
                self._timer = None
        self.update(self._build(self.size.width or HR_WIDTH))

    def _build(self, width: int):
        if not self._animate:
            return build_line(span("─" * width, BORDER_SUBTLE))
        p = min(1.0, (time.monotonic() - self._t0) * 1000.0 / _DRAW_MS)
        drawn = int(round(expo_out(p) * width))
        spans = [span("─" * drawn, BORDER_SUBTLE)]
        if drawn < width:
            spans.append(span("─", mix(BG_CANVAS, BORDER_SUBTLE, 0.5)))
            spans.append(span(" " * max(0, width - drawn - 1), ""))
        return build_line(*spans)


class PhaseRule(Static):
    """A phase-transition announcement rule."""

    DEFAULT_CSS = """
    PhaseRule { height: 1; width: 100%; }
    """

    def __init__(self, label: str, **kwargs) -> None:
        self._label = " ".join(label.upper())
        super().__init__(self._build(HR_WIDTH), **kwargs)

    def on_mount(self) -> None:
        self.update(self._build(self.size.width or HR_WIDTH))

    def _build(self, width: int):
        mid = f"  {self._label}  "
        side = max(2, (width - len(mid)) // 2)
        return build_line(
            span("─" * side, BORDER_SUBTLE),
            span(mid, f"bold {TEXT_SECONDARY}"),
            span("─" * max(0, width - side - len(mid)), BORDER_SUBTLE),
        )
