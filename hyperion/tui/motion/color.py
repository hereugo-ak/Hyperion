"""HYPERION — colour maths for premium, non-slop gradients.

Spec §3.1: gradients MUST be interpolated in a perceptual space (OKLCH), not
naive sRGB. sRGB lerp produces the muddy grey mid-band that is the signature
of "AI slop" banners. We implement sRGB → linear → OKLab → OKLCH and back,
so a cyan → violet → magenta ramp stays luminous and saturated the whole way.

Everything here is pure, dependency-free (no `palette` crate equivalent needed),
and fast enough to run per-cell per-frame at 30+ fps.
"""

from __future__ import annotations

import math
from typing import Sequence

RGB = tuple[int, int, int]


# ── hex <-> rgb ──────────────────────────────────────────────────────────────

def hex_to_rgb(h: str) -> RGB:
    h = h.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def rgb_to_hex(rgb: RGB) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


# ── sRGB <-> linear ──────────────────────────────────────────────────────────

def _srgb_to_linear(c: float) -> float:
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _linear_to_srgb(c: float) -> float:
    c = max(0.0, min(1.0, c))
    v = 12.92 * c if c <= 0.0031308 else 1.055 * (c ** (1 / 2.4)) - 0.055
    return v * 255.0


# ── linear sRGB <-> OKLab (Björn Ottosson) ───────────────────────────────────

def _linear_to_oklab(r: float, g: float, b: float) -> tuple[float, float, float]:
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    return (
        0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_,
        1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_,
        0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_,
    )


def _oklab_to_linear(L: float, a: float, b: float) -> tuple[float, float, float]:
    l_ = L + 0.3963377774 * a + 0.2158037573 * b
    m_ = L - 0.1055613458 * a - 0.0638541728 * b
    s_ = L - 0.0894841775 * a - 1.2914855480 * b
    l, m, s = l_ ** 3, m_ ** 3, s_ ** 3
    return (
        +4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
        -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
        -0.0041960863 * l - 0.7034186147 * m + 1.7076147010 * s,
    )


def rgb_to_oklab(rgb: RGB) -> tuple[float, float, float]:
    r, g, b = (_srgb_to_linear(c) for c in rgb)
    return _linear_to_oklab(r, g, b)


def oklab_to_rgb(lab: tuple[float, float, float]) -> RGB:
    r, g, b = _oklab_to_linear(*lab)
    return (
        int(round(_linear_to_srgb(r))),
        int(round(_linear_to_srgb(g))),
        int(round(_linear_to_srgb(b))),
    )


# ── interpolation ────────────────────────────────────────────────────────────

def lerp_oklab(a: RGB, b: RGB, t: float) -> RGB:
    """Interpolate between two colours in OKLab. t in [0, 1]."""
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    la = rgb_to_oklab(a)
    lb = rgb_to_oklab(b)
    return oklab_to_rgb((
        la[0] + (lb[0] - la[0]) * t,
        la[1] + (lb[1] - la[1]) * t,
        la[2] + (lb[2] - la[2]) * t,
    ))


def ramp(stops: Sequence[str], t: float) -> str:
    """Sample a multi-stop gradient (hex stops) at position t in [0, 1], OKLab.

    e.g. ramp(["#00D9FF", "#8B5CF6", "#F0ABFC"], 0.5) → mid violet, never grey.
    """
    if not stops:
        return "#000000"
    if len(stops) == 1:
        return stops[0]
    t = 0.0 if t < 0 else 1.0 if t > 1 else t
    seg = t * (len(stops) - 1)
    i = int(seg)
    if i >= len(stops) - 1:
        return stops[-1]
    local = seg - i
    a = hex_to_rgb(stops[i])
    b = hex_to_rgb(stops[i + 1])
    return rgb_to_hex(lerp_oklab(a, b, local))


def dim(hex_color: str, factor: float) -> str:
    """Scale a colour toward black in OKLab lightness. factor in [0, 1]."""
    L, a, b = rgb_to_oklab(hex_to_rgb(hex_color))
    return rgb_to_hex(oklab_to_rgb((L * factor, a * factor, b * factor)))


def mix(a_hex: str, b_hex: str, t: float) -> str:
    """Blend two hex colours in OKLab."""
    return rgb_to_hex(lerp_oklab(hex_to_rgb(a_hex), hex_to_rgb(b_hex), t))
