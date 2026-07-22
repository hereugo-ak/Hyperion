"""HYPERION TPM usage bars — live token-per-minute consumption per provider.

Shows real-time TPM usage as a percentage of limit for each provider:
    Google   ████████░░  80%
    NVIDIA   ███░░░░░░░  30%
    Cerebras ████████░░  85%
    Groq     ██████░░░░  60%

Color coding: green (<70%), yellow (70-90%), red (>90%).

Updates in real-time as the wait gate tracks token consumption.

Per ARCHITECTURE.md §8.6.
"""

from __future__ import annotations

from textual.widgets import Static

from hyperion.tui.content import build, line, span
from hyperion.tui.theme import (
    CLAY,
    ROSE,
    SAGE,
    SIG_WARN,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_FPS = 4
_BAR_WIDTH = 10


def _bar_color(pct: float) -> str:
    if pct >= 0.9:
        return ROSE
    if pct >= 0.7:
        return SIG_WARN
    return SAGE


def _bar_spans(pct: float, width: int = _BAR_WIDTH) -> list:
    filled = int(round(pct * width))
    color = _bar_color(pct)
    spans = []
    for i in range(width):
        if i < filled:
            spans.append(("█", color))
        else:
            spans.append(("░", TEXT_GHOST))
    return spans


class TPMBar(Static):
    """Live TPM usage bars for all LLM providers.

    Queries the router for per-provider TPM usage and renders a bar chart.
    Updates at 4 FPS to stay current without flooding the terminal.
    """

    DEFAULT_CSS = """
    TPMBar {
        width: 100%;
        height: auto;
        min-height: 2;
        padding: 0 1;
        background: #1A1918;
    }
    """

    def __init__(self, **kwargs) -> None:
        self._providers: dict[str, float] = {}  # name → usage fraction 0..1
        self._timer = None
        self._frame = 0
        super().__init__(self._render_bars(), **kwargs)

    def on_mount(self) -> None:
        self._timer = self.set_interval(1 / _FPS, self._on_frame)

    def _on_frame(self) -> None:
        self._frame += 1
        self._poll_providers()
        self._repaint()

    def _poll_providers(self) -> None:
        """Query the router for current TPM usage per provider."""
        try:
            from hyperion.router.router import get_router
            router = get_router()
            usage = router.get_tpm_usage()  # dict[provider_name, fraction]
            if usage:
                self._providers = dict(usage)
        except Exception:
            pass

    def _repaint(self) -> None:
        try:
            self.update(self._render_bars())
        except Exception:
            pass

    def set_usage(self, provider: str, fraction: float) -> None:
        """Manually set a provider's TPM usage (for demo/testing)."""
        self._providers[provider] = max(0.0, min(1.0, fraction))
        self._repaint()

    def touch_provider(self, name: str) -> None:
        """Mark a provider as active (0% usage, but visible in the bar)."""
        if name not in self._providers:
            self._providers[name] = 0.0
        self._repaint()

    def _render_bars(self):
        lines: list = []

        lines.append([
            span("  TPM USAGE", f"bold {TEXT_SECONDARY}"),
        ])

        if not self._providers:
            lines.append([span("  no providers connected", TEXT_GHOST)])
            return build(lines)

        for name in sorted(self._providers.keys()):
            pct = self._providers[name]
            pct_str = f"{int(round(pct * 100)):3d}%"
            color = _bar_color(pct)

            row = [
                span(f"  {name[:8].ljust(8)}  ", TEXT_DIM),
            ]
            row.extend(_bar_spans(pct))
            row.append(span(f"  {pct_str}", f"bold {color}"))
            lines.append(row)

        return build(lines)
