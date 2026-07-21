"""HYPERION header bar — pinned top strip (selectable/copyable, Content-native).

Left: three muted status dots + a copy hint.
Right: HYPERION · v{ver} · SESSION 0x{HEX}.
"""

from __future__ import annotations

from textual.widgets import Static

from hyperion.tui.content import build, span
from hyperion.tui.theme import (
    CLAY,
    SAGE,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_SECONDARY,
)

_DOT = SAGE


class HeaderBar(Static):
    """Pinned top header — status dots + product/version/session + copy hint."""

    DEFAULT_CSS = """
    HeaderBar {
        height: 1;
        width: 100%;
        background: #1F1E1D;
    }
    """

    def __init__(self, version: str = "v1.0.0", session_id: str = "0x000000", **kwargs) -> None:
        self._version = version
        self._session_id = session_id
        super().__init__(self._build(80), **kwargs)

    def on_mount(self) -> None:
        self.update(self._build(self.size.width or 80))
        self.set_interval(1.0, self._refresh)

    def _refresh(self) -> None:
        self.update(self._build(self.size.width or 80))

    def _build(self, width: int):
        left_spans = [
            span("  ", ""),
            span("● ", _DOT),
            span("HYPERION", f"bold {CLAY}"),
            span("  ·  ", TEXT_DIM),
            span("copy: drag + Ctrl+Shift+C", TEXT_GHOST),
        ]
        left_len = 2 + 2 + len("HYPERION") + 5 + len("copy: drag + Ctrl+Shift+C")

        right_text = f"HYPERION · {self._version} · SESSION {self._session_id}  "
        right_len = len(right_text)

        pad = max(1, width - left_len - right_len)

        spans = list(left_spans)
        spans.append(span(" " * pad, ""))
        spans.append(span(self._version, TEXT_SECONDARY))
        spans.append(span(" · ", TEXT_DIM))
        spans.append(span(f"session {self._session_id}", TEXT_DIM))
        spans.append(span("  ", ""))
        return build([spans])
