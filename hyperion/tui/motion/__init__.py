"""HYPERION motion toolkit — spinners, easing, gradient helpers (reusable).

Premium, hand-tuned motion language. Indicators are Content-native span
builders (see :mod:`hyperion.tui.motion.indicators`).
"""

from hyperion.tui.motion.color import dim, mix, ramp, rgb_to_hex
from hyperion.tui.motion.easing import clamp01, expo_out, linear, standard
from hyperion.tui.motion.indicators import (
    BRAILLE_FRAMES,
    aurora_spans,
    progress_bar_spans,
    progress_line_spans,
    spinner_frame,
    spinner_span,
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
    "spinner_span",
    "spinner_frame",
    "progress_bar_spans",
    "progress_line_spans",
    "aurora_spans",
    "BRAILLE_FRAMES",
]
