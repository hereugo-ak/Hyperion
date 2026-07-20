"""HYPERION header bar. Spec §6 (header) + §2.5 (locked composition).

Left: three decorative traffic-light dots (● ● ●) — NOT clickable, never
react to hover (§16). Right: HYPERION · v{ver} · SESSION 0x{HEX}.

Also provides the collapsed identity line shown after the first turn (§6):
    ▸ HYPERION v1.0.0   ◆ orchestration · reasoning · synthesis   0x7F3A
"""

from __future__ import annotations

from rich.text import Text
from textual.widget import Widget

from hyperion.tui.theme import (
    BRAND_CYAN,
    BRAND_MAGENTA,
    BRAND_VIOLET,
    SIG_SUCCESS,
    SIG_WARN,
    TEXT_DIM,
    TEXT_SECONDARY,
)

# Muted traffic-light dots (decorative). Not the loud macOS red/amber/green.
_DOT_COLORS = ["#3A4670", "#3A4670", "#3A4670"]


class HeaderBar(Widget):
    """Pinned top header — traffic-light dots + product/version/session."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        width: 100%;
    }
    """

    def __init__(self, version: str = "v1.0.0", session_id: str = "0x000000", **kwargs) -> None:
        super().__init__(**kwargs)
        self._version = version
        self._session_id = session_id

    def render(self) -> Text:
        width = self.size.width or 72
        left = Text()
        left.append("  ")
        for c in _DOT_COLORS:
            left.append("● ", style=c)

        right = Text()
        right.append("HYPERION", style=f"bold {BRAND_CYAN}")
        right.append(" · ", style=TEXT_DIM)
        right.append(self._version, style=TEXT_SECONDARY)
        right.append(" · ", style=TEXT_DIM)
        right.append(f"SESSION {self._session_id}", style=BRAND_VIOLET)
        right.append("  ")

        pad = max(1, width - left.cell_len - right.cell_len)
        out = Text()
        out.append_text(left)
        out.append(" " * pad)
        out.append_text(right)
        return out


class CollapsedIdentity(Widget):
    """Single-line identity shown after the first turn (§6)."""

    DEFAULT_CSS = """
    CollapsedIdentity {
        height: 1;
        width: 100%;
    }
    """

    def __init__(self, version: str = "v1.0.0", session_id: str = "0x000000", **kwargs) -> None:
        super().__init__(**kwargs)
        self._version = version
        self._session_id = session_id

    def render(self) -> Text:
        out = Text()
        out.append("  ▸ ", style=BRAND_CYAN)
        out.append("HYPERION ", style=f"bold {TEXT_SECONDARY}")
        out.append(self._version, style=TEXT_DIM)
        out.append("   ◆ ", style=BRAND_VIOLET)
        out.append("orchestration · reasoning · synthesis", style=TEXT_DIM)
        out.append("   ", style=TEXT_DIM)
        out.append(self._session_id, style=BRAND_VIOLET)
        return out
