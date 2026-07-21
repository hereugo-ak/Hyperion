"""HYPERION — processing indicators (Content-native span builders).

Three tiers, each returned as a list of ``(text, style)`` spans so they drop
straight into :func:`hyperion.tui.content.build` (Textual ``Content``):

  Tier 1  braille-dot spinner          (short work < 5s)
  Tier 2  gradient determinate bar      (bounded work, per-cell cyan->violet)
  Tier 3  aurora indeterminate bar      (long, unknown-duration work)

No ASCII slashes. No "Loading...". No percentage-inside-the-bar. Ever.
"""

from __future__ import annotations

import math

from hyperion.tui.motion.color import ramp
from hyperion.tui.theme import BORDER_SUBTLE, CLAY, CLAY_DEEP, CLAY_SOFT, SIG_WARN, TEXT_DIM

Span = tuple[str, str]

# Tier 1 — braille-dot sequence (10 frames, ~90 ms interval)
BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Reduced-motion fallback: a single dot pulsing.
REDUCED_FRAMES = ["●", "○"]

# Gradient used by both bars.
_BAR_STOPS = [CLAY_SOFT, CLAY, CLAY_DEEP]


def spinner_span(tick: int, reduced: bool = False) -> Span:
    """Return a single (glyph, style) span for the spinner at ``tick``."""
    if reduced:
        return (REDUCED_FRAMES[tick % len(REDUCED_FRAMES)], CLAY)
    glyph = BRAILLE_FRAMES[tick % len(BRAILLE_FRAMES)]
    return (glyph, f"bold {CLAY}")


def progress_bar_spans(fraction: float, width: int = 20) -> list[Span]:
    """Tier 2 — determinate bar with a per-cell cyan→violet gradient fill."""
    fraction = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
    filled = int(round(fraction * width))
    spans: list[Span] = []
    for i in range(width):
        if i < filled:
            spans.append(("█", ramp(_BAR_STOPS, i / max(1, width - 1))))
        else:
            spans.append(("░", BORDER_SUBTLE))
    return spans


def progress_line_spans(label: str, done: int, total: int, width: int = 20) -> list[Span]:
    """Full determinate row: 'label  ████░░  68%   (14 / 22)'. Tier 2."""
    total = max(1, total)
    frac = done / total
    spans: list[Span] = [(label + "  ", TEXT_DIM)]
    spans.extend(progress_bar_spans(frac, width))
    spans.append((f"  {int(round(frac * 100))}%", f"bold {SIG_WARN}"))
    spans.append((f"   ({done} / {total})", TEXT_DIM))
    return spans


# Tier 3 — aurora indeterminate bar
_AURORA_HEIGHTS = "▁▂▃▄▅▆▇█"


def aurora_spans(tick: int, track: int = 28, sigma: float = 3.2) -> list[Span]:
    """A soft Gaussian pulse sliding across ``track`` cells on a loop."""
    period = 48  # frames for a full sweep
    phase = (tick % period) / period
    center = phase * (track + 8) - 4  # let it enter/exit off-screen

    spans: list[Span] = []
    for i in range(track):
        d = i - center
        g = math.exp(-(d * d) / (2 * sigma * sigma))  # 0..1
        if g < 0.06:
            spans.append((" ", BORDER_SUBTLE))
            continue
        h_idx = int(round(g * (len(_AURORA_HEIGHTS) - 1)))
        glyph = _AURORA_HEIGHTS[h_idx]
        color = ramp(_BAR_STOPS, min(1.0, i / max(1, track - 1)))
        style = f"bold {color}" if d >= -0.5 and g > 0.5 else color
        spans.append((glyph, style))
    return spans


# ── legacy rich.Text shims (kept so nothing else breaks) ─────────────────────
# These wrap the span builders in a Rich Text for any old caller; new code uses
# the *_span(s) helpers above with Content.

def spinner_frame(tick: int, reduced: bool = False):
    from rich.text import Text

    text, style = spinner_span(tick, reduced)
    return Text(text, style=style)
