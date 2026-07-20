"""HYPERION — processing indicators. Spec §8.1 / §8.3.

Three tiers, all rendered as Rich Text so they drop straight into Textual:

  Tier 1  braille-dot spinner          (short work < 5s)
  Tier 2  gradient determinate bar      (bounded work, per-cell cyan->violet)
  Tier 3  aurora indeterminate bar      (long, unknown-duration work)

No ASCII slashes. No "Loading...". No percentage-inside-the-bar. Ever.
"""

from __future__ import annotations

import math

from rich.text import Text

from hyperion.tui.motion.color import ramp
from hyperion.tui.theme import BORDER_SUBTLE, BRAND_CYAN, BRAND_VIOLET, SIG_WARN, TEXT_DIM

# §8.1 Tier 1 — braille-dot sequence (10 frames, 90 ms interval)
BRAILLE_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]

# Reduced-motion fallback (§13): a single dot toggling.
REDUCED_FRAMES = ["●", " "]

# Gradient used by both bars.
_BAR_STOPS = [BRAND_CYAN, BRAND_VIOLET]


def spinner_frame(tick: int, reduced: bool = False) -> Text:
    """Return the braille glyph for a given tick, coloured brand.cyan."""
    if reduced:
        return Text(REDUCED_FRAMES[tick % len(REDUCED_FRAMES)], style=BRAND_CYAN)
    glyph = BRAILLE_FRAMES[tick % len(BRAILLE_FRAMES)]
    return Text(glyph, style=f"bold {BRAND_CYAN}")


def progress_bar(fraction: float, width: int = 20) -> Text:
    """Tier 2 — determinate bar with a per-cell cyan→violet gradient fill.

    Filled cells sample the gradient across the FULL width (so the ramp reads
    as one continuous sweep, not a per-segment rainbow). Empty cells are the
    subtle border colour.
    """
    fraction = 0.0 if fraction < 0 else 1.0 if fraction > 1 else fraction
    filled = int(round(fraction * width))
    out = Text()
    for i in range(width):
        if i < filled:
            color = ramp(_BAR_STOPS, i / max(1, width - 1))
            out.append("█", style=color)
        else:
            out.append("░", style=BORDER_SUBTLE)
    return out


def progress_line(label: str, done: int, total: int, width: int = 20) -> Text:
    """Full determinate row: 'label  ████░░  68%   (14 / 22)'. §8.1 Tier 2."""
    total = max(1, total)
    frac = done / total
    line = Text()
    line.append(label + "  ", style=TEXT_DIM)
    line.append_text(progress_bar(frac, width))
    line.append(f"  {int(round(frac * 100))}%", style=f"bold {SIG_WARN}")
    line.append(f"   ({done} / {total})", style=TEXT_DIM)
    return line


# §8.1 Tier 3 — aurora indeterminate bar
_AURORA_HEIGHTS = "▁▂▃▄▅▆▇█"


def aurora_bar(tick: int, track: int = 28, sigma: float = 3.2) -> Text:
    """A soft Gaussian pulse sliding across `track` cells on a loop.

    Cell height is modulated by a Gaussian centred on the pulse position;
    colour follows the cyan→violet ramp with a brighter leading edge.
    """
    # Pulse travels 0..track over a 1.6s loop; caller ticks ~30fps.
    period = 48  # frames for a full sweep
    phase = (tick % period) / period
    center = phase * (track + 8) - 4  # let it enter/exit off-screen

    out = Text()
    for i in range(track):
        d = i - center
        g = math.exp(-(d * d) / (2 * sigma * sigma))  # 0..1
        h_idx = int(round(g * (len(_AURORA_HEIGHTS) - 1)))
        glyph = _AURORA_HEIGHTS[h_idx]
        if g < 0.06:
            out.append(" ", style=BORDER_SUBTLE)
            continue
        color = ramp(_BAR_STOPS, min(1.0, i / max(1, track - 1)))
        # leading edge (just ahead of centre) glows a touch brighter
        style = f"bold {color}" if d >= -0.5 and g > 0.5 else color
        out.append(glyph, style=style)
    return out
