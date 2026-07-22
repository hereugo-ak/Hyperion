"""HYPERION animated Mark widget — brand symbol with 6 states.

The Mark is HYPERION's brand symbol, rendered as a text-based animation that
works in any terminal. It is NOT an image. It uses Rich/Textual Content spans
with ANSI colour codes.

States (per ARCHITECTURE.md §8.3):
    Dormant      — slow pulse (1 cycle/3s)    System idle, no engagement
    Listening    — quick pulse (1 cycle/1s)   User typing a question
    Orchestrating — rotating segments          Engagement Director building DAG
    Synthesizing  — converging segments        Synthesis Lead reconciling
    Delivered    — solid glow                  PDF generated, report ready
    Blocked      — red flash                   Error, rate limit, quality gate rejection
"""

from __future__ import annotations

import math
from enum import Enum

from textual.widgets import Static

from hyperion.tui.content import build_line, span
from hyperion.tui.theme import CLAY, CLAY_DEEP, CLAY_SOFT, ROSE, SIG_SUCCESS, TEXT_DIM

_FPS = 10

# Mark glyphs — 8-segment ring that rotates/pulses
_SEGMENTS = "▖▗▘▝▀▄▌▐"


class MarkState(str, Enum):
    DORMANT = "dormant"
    LISTENING = "listening"
    ORCHESTRATING = "orchestrating"
    SYNTHESIZING = "synthesizing"
    DELIVERED = "delivered"
    BLOCKED = "blocked"


# Per-state animation parameters
_STATE_CONFIG = {
    MarkState.DORMANT: {
        "cycle_s": 3.0,
        "color": CLAY_SOFT,
        "mode": "pulse",
    },
    MarkState.LISTENING: {
        "cycle_s": 1.0,
        "color": CLAY,
        "mode": "pulse",
    },
    MarkState.ORCHESTRATING: {
        "cycle_s": 1.5,
        "color": CLAY,
        "mode": "rotate",
    },
    MarkState.SYNTHESIZING: {
        "cycle_s": 2.0,
        "color": CLAY_DEEP,
        "mode": "converge",
    },
    MarkState.DELIVERED: {
        "cycle_s": 0,
        "color": SIG_SUCCESS,
        "mode": "solid",
    },
    MarkState.BLOCKED: {
        "cycle_s": 0.5,
        "color": ROSE,
        "mode": "flash",
    },
}


class Mark(Static):
    """Animated HYPERION brand Mark — 6 states, text-based, no images."""

    DEFAULT_CSS = """
    Mark {
        height: 1;
        width: 6;
        content-align: center middle;
    }
    """

    def __init__(self, **kwargs) -> None:
        self._state: MarkState = MarkState.DORMANT
        self._frame = 0
        self._timer = None
        super().__init__(self._render(), **kwargs)

    def on_mount(self) -> None:
        self._ensure_timer()

    def set_state(self, state: MarkState) -> None:
        if state == self._state:
            return
        self._state = state
        self._ensure_timer()
        self._repaint()

    @property
    def state(self) -> MarkState:
        return self._state

    # ── animation ────────────────────────────────────────────────────────────

    def _ensure_timer(self) -> None:
        cfg = _STATE_CONFIG.get(self._state, _STATE_CONFIG[MarkState.DORMANT])
        if cfg["mode"] == "solid":
            if self._timer is not None:
                self._timer.stop()
                self._timer = None
            self._repaint()
            return
        if self._timer is None:
            self._timer = self.set_interval(1 / _FPS, self._on_frame)

    def _on_frame(self) -> None:
        self._frame += 1
        self._repaint()

    def _repaint(self) -> None:
        try:
            self.update(self._render())
        except Exception:
            pass

    # ── render ────────────────────────────────────────────────────────────────

    def _render(self):
        cfg = _STATE_CONFIG.get(self._state, _STATE_CONFIG[MarkState.DORMANT])
        color = cfg["color"]
        mode = cfg["mode"]
        cycle = cfg["cycle_s"]
        t = self._frame / _FPS  # seconds elapsed

        if mode == "solid":
            return build_line(span("◆", f"bold {color}"))

        if mode == "pulse":
            phase = (t % cycle) / cycle  # 0..1
            intensity = 0.4 + 0.6 * (0.5 + 0.5 * math.sin(phase * 2 * math.pi))
            if intensity > 0.7:
                style = f"bold {color}"
            elif intensity > 0.4:
                style = color
            else:
                style = TEXT_DIM
            return build_line(span("◆", style))

        if mode == "rotate":
            idx = int(self._frame / 2) % len(_SEGMENTS)
            glyph = _SEGMENTS[idx]
            return build_line(
                span(_SEGMENTS[(idx - 1) % len(_SEGMENTS)], TEXT_DIM),
                span(glyph, f"bold {color}"),
                span(_SEGMENTS[(idx + 1) % len(_SEGMENTS)], TEXT_DIM),
            )

        if mode == "converge":
            cycle_frames = int(cycle * _FPS)
            phase = (self._frame % cycle_frames) / cycle_frames  # 0..1
            # Segments converge from outside to center
            if phase < 0.5:
                spread = 1.0 - phase * 2  # 1 → 0
                left = " " * int(spread * 2)
                right = " " * int(spread * 2)
                return build_line(
                    span(left, ""),
                    span("◆", color),
                    span(right, ""),
                )
            else:
                # Pulsing center
                return build_line(span("◆", f"bold {color}"))

        if mode == "flash":
            on = (self._frame % 5) < 3
            color = ROSE if on else TEXT_DIM
            return build_line(span("◆", f"bold {color}"))

        return build_line(span("◆", TEXT_DIM))
