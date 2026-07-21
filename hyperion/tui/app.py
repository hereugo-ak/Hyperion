"""HYPERION TUI application.

A single-screen command bridge. The premium feel comes from the motion layer
(`hyperion.tui.motion`) and the animated logo — not from decoration.

Copy support
------------
Every visible surface is built on *selectable* Textual widgets (`Static`,
`RichLog`), and `App.ALLOW_SELECT` is on, so a mouse click-drag highlights
text and ``ctrl+shift+c`` copies the current selection to the system clipboard
via OSC-52 (works in Windows Terminal, iTerm2, kitty, WezTerm, …).

For terminals where Textual's mouse capture prevents the *terminal's own*
click-drag selection (classic conhost / some PowerShell setups), launch with
``hyperion shell --no-mouse``: Textual then never grabs the mouse, so the
terminal handles selection & copy natively.
"""

from __future__ import annotations

from typing import Any

from textual.app import App
from textual.binding import Binding

from hyperion.tui.screens.session import SessionScreen
from hyperion.tui.theme import (
    BG_CANVAS,
    BG_SURFACE,
    CLAY,
    CLAY_DEEP,
    CLAY_SOFT,
    SIG_ERROR,
    SIG_SUCCESS,
    SIG_WARN,
    TEXT_PRIMARY,
)


class HyperionApp(App):
    """The HYPERION terminal interface."""

    TITLE = "HYPERION"
    SUB_TITLE = "multi-agent consulting system"

    # Native drag-to-select is on everywhere. Custom widgets that paint their
    # own cells are avoided in favour of Static/RichLog so selection works.
    ALLOW_SELECT = True

    # Global copy bindings. ctrl+shift+c never collides with the prompt's
    # printable input, and works while the prompt has focus.
    BINDINGS = [
        Binding("ctrl+shift+c", "copy_selection", "Copy", show=True),
        Binding("ctrl+shift+a", "select_all", "Select all", show=True),
        Binding("ctrl+q", "quit", "Quit", show=True),
    ]

    CSS = f"""
    Screen {{
        background: {BG_CANVAS};
        color: {TEXT_PRIMARY};
    }}
    * {{
        scrollbar-background: {BG_CANVAS};
        scrollbar-color: #4A4640;
        scrollbar-color-hover: {CLAY};
    }}
    /* Selection highlight: clay wash so highlighted text is obvious. */
    Screen {{
        link-color: {CLAY};
    }}
    /* Textual paints drag-selection with the 'text-selection' theme colour
       (set in on_mount) — make it a clay wash with cream text. */
    RichLog {{
        text-wrap: wrap;
    }}
    """

    def __init__(
        self,
        reduced_motion: bool = False,
        demo: bool = False,
        mouse: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._reduced_motion = reduced_motion
        self._demo = demo
        self._want_mouse = mouse

    def on_mount(self) -> None:
        # Apply brand accents to Textual's theme variables where possible.
        try:
            self.theme_variables.update(
                {
                    "primary": CLAY,
                    "secondary": CLAY_SOFT,
                    "accent": CLAY_DEEP,
                    "success": SIG_SUCCESS,
                    "warning": SIG_WARN,
                    "error": SIG_ERROR,
                }
            )
        except Exception:
            pass
        self.push_screen(
            SessionScreen(reduced_motion=self._reduced_motion, demo=self._demo)
        )

    # ── copy actions ─────────────────────────────────────────────────────────

    def action_copy_selection(self) -> None:
        """Copy the current text selection to the clipboard (OSC-52)."""
        text = self._gather_selection()
        if not text:
            self._toast("nothing selected — drag to highlight, then Ctrl+Shift+C")
            return
        try:
            self.copy_to_clipboard(text)
            n = len(text.splitlines()) or 1
            self._toast(f"copied {len(text)} chars · {n} line(s)")
        except Exception as exc:  # pragma: no cover - clipboard is best-effort
            self._toast(f"copy failed: {exc}")

    def action_select_all(self) -> None:
        """Select the whole transcript so it can be copied at once."""
        try:
            screen = self.screen
            if isinstance(screen, SessionScreen):
                screen.select_all_transcript()
                self._toast("transcript selected — Ctrl+Shift+C to copy")
        except Exception:
            pass

    def _gather_selection(self) -> str:
        """Return the currently selected text, if the Textual version exposes it."""
        # Textual >= 3 keeps selections per-screen; try the documented helper.
        try:
            get_sel = getattr(self.screen, "get_selected_text", None)
            if callable(get_sel):
                sel = get_sel()
                if sel:
                    return sel
        except Exception:
            pass
        # Fallback: ask the session screen for its transcript selection.
        try:
            screen = self.screen
            if isinstance(screen, SessionScreen):
                return screen.selected_transcript_text()
        except Exception:
            pass
        return ""

    def _toast(self, msg: str) -> None:
        try:
            self.notify(msg, timeout=3)
        except Exception:
            pass


def run(reduced_motion: bool = False, demo: bool = False, mouse: bool = True) -> None:
    """Entry point used by the CLI `shell` command."""
    HyperionApp(reduced_motion=reduced_motion, demo=demo, mouse=mouse).run(mouse=mouse)
