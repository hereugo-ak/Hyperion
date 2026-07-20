"""HYPERION horizontal rules + phase transitions. Spec §2.4 + §8.5.

- Rule: a single-line U+2500 divider in border.subtle. Optionally draws
  itself left→right over 320 ms on an expo-out curve (§8.5).
- PhaseRule: a phase announcement — thin rule with a centred, letter-spaced
  label:  ───────  P H A S E   2 · E X E C U T E  ───────
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widget import Widget

from hyperion.tui.motion.color import mix
from hyperion.tui.motion.easing import expo_out
from hyperion.tui.theme import BG_CANVAS, BORDER_SUBTLE, TEXT_SECONDARY

HR_WIDTH = 69
_DRAW_MS = 320.0
_FPS = 30


def hr(width: int = HR_WIDTH) -> Text:
    """A static rule as Rich Text."""
    return Text("─" * width, style=BORDER_SUBTLE)


class Rule(Widget):
    """A horizontal rule that can animate its draw-in (§8.5)."""

    DEFAULT_CSS = """
    Rule { height: 1; width: 100%; }
    """

    def __init__(self, animate: bool = False, **kwargs) -> None:
        super().__init__(**kwargs)
        self._animate = animate
        self._t0 = time.monotonic()
        self._timer = None

    def on_mount(self) -> None:
        if self._animate:
            self._timer = self.set_interval(1 / _FPS, self._frame)

    def _frame(self) -> None:
        if (time.monotonic() - self._t0) * 1000.0 >= _DRAW_MS:
            if self._timer:
                self._timer.stop()
                self._timer = None
        self.refresh()

    def render(self) -> Text:
        width = self.size.width or HR_WIDTH
        if not self._animate:
            return Text("─" * width, style=BORDER_SUBTLE)
        p = min(1.0, (time.monotonic() - self._t0) * 1000.0 / _DRAW_MS)
        drawn = int(round(expo_out(p) * width))
        out = Text()
        out.append("─" * drawn, style=BORDER_SUBTLE)
        if drawn < width:
            # faint leading char, then empty
            out.append("─", style=mix(BG_CANVAS, BORDER_SUBTLE, 0.5))
            out.append(" " * max(0, width - drawn - 1))
        return out


class PhaseRule(Widget):
    """A phase-transition announcement rule (§8.5)."""

    DEFAULT_CSS = """
    PhaseRule { height: 1; width: 100%; }
    """

    def __init__(self, label: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self._label = " ".join(label.upper())  # letter-spaced by one

    def render(self) -> Text:
        width = self.size.width or HR_WIDTH
        mid = f"  {self._label}  "
        side = max(2, (width - len(mid)) // 2)
        out = Text()
        out.append("─" * side, style=BORDER_SUBTLE)
        out.append(mid, style=f"bold {TEXT_SECONDARY}")
        out.append("─" * max(0, width - side - len(mid)), style=BORDER_SUBTLE)
        return out
