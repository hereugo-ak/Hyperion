"""HYPERION motion toolkit — spinners, easing, gradient helpers (reusable).

Spec §8 motion language. Everything premium and hand-tuned lives here.
"""

from hyperion.tui.motion.color import dim, mix, ramp, rgb_to_hex
from hyperion.tui.motion.easing import clamp01, expo_out, linear, standard
from hyperion.tui.motion.indicators import (
    BRAILLE_FRAMES,
    aurora_bar,
    progress_bar,
    progress_line,
    spinner_frame,
)

__all__ = [
    "ramp",
    "mix",
    "dim",
    "rgb_to_hex",
    "expo_out",
    "standard",
    "linear",
    "clamp01",
    "spinner_frame",
    "progress_bar",
    "progress_line",
    "aurora_bar",
    "BRAILLE_FRAMES",
]
