"""HYPERION — easing curves. Spec §8 timing invariants.

Hand-tuned Bezier / expo curves. NEVER linear for reveal/transition motion
(linear + ease-in-out are the AI-slop defaults). The signature curve is
cubic-bezier(0.16, 1, 0.3, 1) — a snappy expo-out used for the logo sweep,
log-line fade-in, and section transitions.
"""

from __future__ import annotations


def _cubic_bezier(p1x: float, p1y: float, p2x: float, p2y: float):
    """Return a callable f(t)->y approximating a CSS cubic-bezier.

    P0=(0,0), P3=(1,1). Solve x(t)=target via a few Newton iterations, then
    evaluate y. Accurate enough for animation, cheap enough for per-frame.
    """

    def _bez(a: float, b: float, t: float) -> float:
        # cubic with fixed endpoints 0 and 1
        mt = 1 - t
        return 3 * mt * mt * t * a + 3 * mt * t * t * b + t * t * t

    def _bez_dt(a: float, b: float, t: float) -> float:
        mt = 1 - t
        return 3 * mt * mt * a + 6 * mt * t * (b - a) + 3 * t * t * (1 - b)

    def curve(x: float) -> float:
        x = 0.0 if x < 0 else 1.0 if x > 1 else x
        t = x
        for _ in range(6):
            xe = _bez(p1x, p2x, t) - x
            d = _bez_dt(p1x, p2x, t)
            if abs(d) < 1e-6:
                break
            t -= xe / d
            t = 0.0 if t < 0 else 1.0 if t > 1 else t
        return _bez(p1y, p2y, t)

    return curve


# The HYPERION signature curve (expo-out). §8 / §3.2.
expo_out = _cubic_bezier(0.16, 1.0, 0.3, 1.0)

# Standard material-style ease for progress steps. §8.
standard = _cubic_bezier(0.4, 0.0, 0.2, 1.0)


def linear(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x


def clamp01(x: float) -> float:
    return 0.0 if x < 0 else 1.0 if x > 1 else x
