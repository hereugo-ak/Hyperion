"""HYPERION animated logo. Spec §2.1 (locked wordmark) + §3 (animation).

Three layered behaviours:
  1. Intro chroma-sweep (§3.2): dim monochrome → a vertical light-bar sweeps
     left→right over 900 ms on an expo-out curve, revealing the gradient in
     its wake with a 2-char trailing glow. Plays once per session.
  2. Steady shimmer (§3.1): a cyan→violet→magenta gradient flows left→right
     on a 4 s loop, OKLCH-interpolated (never a muddy grey mid-band).
  3. Idle breathing (§3.3, optional/off): subtle opacity pulse on long idle.

Locked wordmark string is embedded verbatim so it never regenerates.
Banner + sub-banner strings are byte-identical to §2.2 / §2.3.
"""

from __future__ import annotations

import time

from rich.text import Text
from textual.widget import Widget

from hyperion.tui.motion.color import mix, ramp
from hyperion.tui.motion.easing import expo_out
from hyperion.tui.theme import (
    BRAND_MAGENTA,
    LOGO_DIM,
    LOGO_STOPS,
    TEXT_DIM,
    TEXT_SECONDARY,
)

# §2.1 — LOCKED wordmark (ANSI Shadow figlet). Do not regenerate.
WORDMARK = [
    "  ██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗ ██╗ ██████╗ ███╗   ██╗",
    "  ██║  ██║╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██║██╔═══██╗████╗  ██║",
    "  ███████║ ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║██║   ██║██╔██╗ ██║",
    "  ██╔══██║  ╚██╔╝  ██╔═══╝ ██╔══╝  ██╔══██╗██║██║   ██║██║╚██╗██║",
    "  ██║  ██║   ██║   ██║     ███████╗██║  ██║██║╚██████╔╝██║ ╚████║",
    "  ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝",
]

# §2.2 / §2.3 — locked banner strings (byte-identical, verbatim).
BANNER_LINE = "◆ MULTI-AGENT CONSULTING SYSTEM ◆"
SUBBANNER_LINE = "orchestration · reasoning · synthesis"

_LOGO_WIDTH = max(len(line) for line in WORDMARK)

# Timing (§8 / §3.2)
_INTRO_DIM_MS = 250
_INTRO_SWEEP_MS = 900
_SHIMMER_LOOP_S = 4.0
_FPS = 30
_GLOW = 2  # trailing-glow falloff in chars


class HyperionLogo(Widget):
    """The animated wordmark + banner block."""

    DEFAULT_CSS = """
    HyperionLogo {
        height: auto;
        content-align: center middle;
        text-align: center;
    }
    """

    def __init__(
        self,
        animated: bool = True,
        reduced_motion: bool = False,
        show_intro: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(**kwargs)
        self._animated = animated
        self._reduced = reduced_motion
        self._show_intro = show_intro and not reduced_motion
        self._t0 = time.monotonic()
        self._banner_reveal = 0.0  # 0..1 fade-in progress for banner
        self._timer = None

    def on_mount(self) -> None:
        if self._animated and not self._reduced:
            self._timer = self.set_interval(1 / _FPS, self.refresh)
        else:
            self.refresh()

    # ── per-character colour for the wordmark ────────────────────────────────

    def _char_color(self, x: int, elapsed_ms: float) -> str:
        """Colour for glyph column x at time `elapsed_ms`."""
        if self._reduced or not self._animated:
            # Static gradient (still colourful, no motion). §13 reduced-motion.
            return ramp(LOGO_STOPS, x / max(1, _LOGO_WIDTH - 1))

        intro_total = _INTRO_DIM_MS + _INTRO_SWEEP_MS
        if self._show_intro and elapsed_ms < intro_total:
            return self._intro_color(x, elapsed_ms)

        # Steady shimmer (§3.1): gradient phase scrolls left→right, 4 s loop.
        phase = ((elapsed_ms / 1000.0) / _SHIMMER_LOOP_S) % 1.0
        pos = ((x / max(1, _LOGO_WIDTH - 1)) - phase) % 1.0
        return ramp(LOGO_STOPS, pos)

    def _intro_color(self, x: int, elapsed_ms: float) -> str:
        """Chroma-sweep reveal colour for column x. §3.2."""
        if elapsed_ms < _INTRO_DIM_MS:
            return LOGO_DIM  # 250 ms dim monochrome hold

        # Sweep progress on expo-out over 900 ms.
        p = (elapsed_ms - _INTRO_DIM_MS) / _INTRO_SWEEP_MS
        eased = expo_out(p)
        bar = eased * (_LOGO_WIDTH + _GLOW)  # light-bar leading position (cols)
        col_norm = x / max(1, _LOGO_WIDTH - 1)

        if x <= bar - _GLOW:
            # fully revealed: steady gradient sample
            return ramp(LOGO_STOPS, col_norm)
        if x <= bar:
            # trailing glow: blend from dim → gradient over the 2-char falloff
            reveal = 1.0 - (bar - x) / _GLOW
            grad = ramp(LOGO_STOPS, col_norm)
            # leading edge burns slightly toward magenta for a light-bar feel
            edge = mix(grad, BRAND_MAGENTA, 0.35)
            return mix(LOGO_DIM, edge, reveal)
        return LOGO_DIM  # not yet reached

    # ── render ───────────────────────────────────────────────────────────────

    def render(self) -> Text:
        elapsed_ms = (time.monotonic() - self._t0) * 1000.0
        out = Text(justify="center")

        for line in WORDMARK:
            for x, ch in enumerate(line):
                if ch == " ":
                    out.append(" ")
                else:
                    out.append(ch, style=self._char_color(x, elapsed_ms))
            out.append("\n")

        out.append("\n")

        # Banner + sub-banner fade in after the sweep (staggered), §19.4.
        intro_total = _INTRO_DIM_MS + _INTRO_SWEEP_MS
        show_banner = (not self._show_intro) or elapsed_ms >= intro_total
        show_sub = (not self._show_intro) or elapsed_ms >= intro_total + 80

        if show_banner:
            out.append(BANNER_LINE, style=f"bold {TEXT_SECONDARY}")
        out.append("\n")
        if show_sub:
            out.append(SUBBANNER_LINE, style=TEXT_DIM)

        return out

    def stop(self) -> None:
        if self._timer is not None:
            self._timer.stop()
            self._timer = None
