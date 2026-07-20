"""HYPERION TUI application. Spec §6 / §11 (Textual stack) / §19 (first-run).

A single-screen command bridge. No splash detour, no box-in-box nesting
(max 2 levels, §16). The premium feel comes from the motion layer
(`hyperion.tui.motion`) and the animated logo — not from decoration.
"""

from __future__ import annotations

from typing import Any

from textual.app import App

from hyperion.tui.screens.session import SessionScreen
from hyperion.tui.theme import (
    BG_CANVAS,
    BG_SURFACE,
    BRAND_CYAN,
    BRAND_MAGENTA,
    BRAND_VIOLET,
    SIG_ERROR,
    SIG_SUCCESS,
    SIG_WARN,
    TEXT_PRIMARY,
)


class HyperionApp(App):
    """The HYPERION terminal interface."""

    TITLE = "HYPERION"
    SUB_TITLE = "multi-agent consulting system"

    CSS = f"""
    Screen {{
        background: {BG_CANVAS};
        color: {TEXT_PRIMARY};
    }}
    * {{
        scrollbar-background: {BG_CANVAS};
        scrollbar-color: {BG_SURFACE};
        scrollbar-color-hover: {BRAND_VIOLET};
    }}
    """

    def __init__(self, reduced_motion: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reduced_motion = reduced_motion

    def on_mount(self) -> None:
        # Apply brand accents to Textual's theme variables where possible.
        try:
            self.theme_variables.update(
                {
                    "primary": BRAND_VIOLET,
                    "secondary": BRAND_CYAN,
                    "accent": BRAND_MAGENTA,
                    "success": SIG_SUCCESS,
                    "warning": SIG_WARN,
                    "error": SIG_ERROR,
                }
            )
        except Exception:
            pass
        self.push_screen(SessionScreen(reduced_motion=self._reduced_motion))


def run(reduced_motion: bool = False) -> None:
    """Entry point used by the CLI `shell` command."""
    HyperionApp(reduced_motion=reduced_motion).run()
