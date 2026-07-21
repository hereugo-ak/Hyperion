"""HYPERION animated wordmark — the premium brand identity.

Built entirely on Textual :class:`Content`, so every glyph is selectable and
copyable. Three behaviours:

  1. Intro chroma-sweep — dim monochrome → a light-bar sweeps left→right over
     ~900 ms (expo-out), revealing the cyan→violet→magenta gradient in its wake
     with a short trailing glow. Plays once per session.
  2. Steady shimmer — the gradient phase scrolls left→right on a 4 s loop,
     OKLab-interpolated so the mid-band never turns muddy grey.
  3. Compact strip — a one-line shimmering "HYPERION · multi-agent consulting
     system" that stays pinned once an engagement starts, so the identity is
     ALWAYS on screen (never a blank page).
"""

from __future__ import annotations

import time

from textual.widgets import Static

from hyperion.tui.content import build, line, span
from hyperion.tui.motion.color import mix, ramp
from hyperion.tui.motion.easing import expo_out
from hyperion.tui.theme import (
    BRAND_MAGENTA,
    LOGO_DIM,
    LOGO_STOPS,
    TEXT_DIM,
    TEXT_SECONDARY,
)

# LOCKED wordmark (ANSI Shadow figlet). Do not regenerate.
WORDMARK = [
    "  ██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗ ██╗ ██████╗ ███╗   ██╗",
    "  ██║  ██║╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██║██╔═══██╗████╗  ██║",
    "  ███████║ ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║██║   ██║██╔██╗ ██║",
    "  ██╔══██║  ╚██╔╝  ██╔═══╝ ██╔══╝  ██╔══██╗██║██║   ██║██║╚██╗██║",
    "  ██║  ██║   ██║   ██║     ███████╗██║  ██║██║╚██████╔╝██║ ╚████║",
    "  ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝",
]

BANNER_LINE = "◆ MULTI-AGENT CONSULTING SYSTEM ◆"
SUBBANNER_LINE = "orchestration · reasoning · synthesis"

_LOGO_WIDTH = max(len(s) for s in WORDMARK)

_INTRO_DIM_MS = 250
_INTRO_SWEEP_MS = 900
_SHIMMER_LOOP_S = 4.0
_FPS = 30
_GLOW = 2


class HyperionLogo(Static):
    """Animated wordmark + banner (selectable/copyable)."""

    DEFAULT_CSS = """
    HyperionLogo {
        height: auto;
        content-align: center middle;
        text-align: center;
        width: 1fr;
    }
    HyperionLogo.compact { height: 1; }
    """

    def __init__(
        self,
        animated: bool = True,
        reduced_motion: bool = False,
        show_intro: bool = True,
        compact: bool = False,
        **kwargs,
    ) -> None:
        self._animated = animated
        self._reduced = reduced_motion
        self._show_intro = show_intro and not reduced_motion
        self._compact = compact
        self._t0 = time.monotonic()
        self._timer = None
        super().__init__(self._build(), **kwargs)

    def on_mount(self) -> None:
        if self._animated and not self._reduced:
            self._timer = self.set_interval(1 / _FPS, self._tick)

    def _tick(self) -> None:
        self.update(self._build())

    def set_compact(self, compact: bool) -> None:
        self._compact = compact
        self.set_class(compact, "compact")
        self.update(self._build())

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None

    # ── per-character colour ─────────────────────────────────────────────────

    def _char_color(self, x: int, elapsed_ms: float) -> str:
        if self._reduced or not self._animated:
            return ramp(LOGO_STOPS, x / max(1, _LOGO_WIDTH - 1))
        intro_total = _INTRO_DIM_MS + _INTRO_SWEEP_MS
        if self._show_intro and elapsed_ms < intro_total:
            return self._intro_color(x, elapsed_ms)
        phase = ((elapsed_ms / 1000.0) / _SHIMMER_LOOP_S) % 1.0
        pos = ((x / max(1, _LOGO_WIDTH - 1)) - phase) % 1.0
        return ramp(LOGO_STOPS, pos)

    def _intro_color(self, x: int, elapsed_ms: float) -> str:
        if elapsed_ms < _INTRO_DIM_MS:
            return LOGO_DIM
        p = (elapsed_ms - _INTRO_DIM_MS) / _INTRO_SWEEP_MS
        eased = expo_out(p)
        bar = eased * (_LOGO_WIDTH + _GLOW)
        col_norm = x / max(1, _LOGO_WIDTH - 1)
        if x <= bar - _GLOW:
            return ramp(LOGO_STOPS, col_norm)
        if x <= bar:
            reveal = 1.0 - (bar - x) / _GLOW
            grad = ramp(LOGO_STOPS, col_norm)
            edge = mix(grad, BRAND_MAGENTA, 0.35)
            return mix(LOGO_DIM, edge, reveal)
        return LOGO_DIM

    # ── build ─────────────────────────────────────────────────────────────────

    def _compact_content(self, elapsed_ms: float):
        word = "HYPERION"
        spans = [span("  ", "")]
        phase = ((elapsed_ms / 1000.0) / _SHIMMER_LOOP_S) % 1.0
        for i, ch in enumerate(word):
            if self._reduced or not self._animated:
                color = ramp(LOGO_STOPS, i / max(1, len(word) - 1))
            else:
                pos = ((i / max(1, len(word) - 1)) - phase) % 1.0
                color = ramp(LOGO_STOPS, pos)
            spans.append(span(ch, f"bold {color}"))
        spans.append(span("  ·  ", TEXT_DIM))
        spans.append(span("multi-agent consulting system", TEXT_SECONDARY))
        return build([spans])

    def _build(self):
        elapsed_ms = (time.monotonic() - self._t0) * 1000.0
        if self._compact:
            return self._compact_content(elapsed_ms)

        lines = []
        for s in WORDMARK:
            row = []
            for x, ch in enumerate(s):
                if ch == " ":
                    row.append(span(" ", ""))
                else:
                    row.append(span(ch, self._char_color(x, elapsed_ms)))
            lines.append(row)
        lines.append(line(""))  # blank spacer

        intro_total = _INTRO_DIM_MS + _INTRO_SWEEP_MS
        show_banner = (not self._show_intro) or elapsed_ms >= intro_total
        show_sub = (not self._show_intro) or elapsed_ms >= intro_total + 80

        lines.append(line(span(BANNER_LINE, f"bold {TEXT_SECONDARY}")) if show_banner else line(""))
        lines.append(line(span(SUBBANNER_LINE, TEXT_DIM)) if show_sub else line(""))
        return build(lines)
