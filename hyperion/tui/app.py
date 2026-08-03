"""HYPERION TUI application.

A single-screen command bridge. The premium feel comes from the motion layer
(`hyperion.tui.motion`) and the animated logo — not from decoration.

Copy support
------------
Every visible surface is built on *selectable* Textual widgets (`Static`, and
the `ScrollView`-based `Transcript`), and `App.ALLOW_SELECT` is on, so a mouse
click-drag highlights text and ``ctrl+shift+c`` copies the current selection to
the system clipboard via OSC-52 (works in Windows Terminal, iTerm2, kitty,
WezTerm, …).

Because `Transcript.render_line` stamps precise per-cell content offsets and
`ENABLE_SELECT_AUTO_SCROLL` is on, a drag that reaches the top or bottom edge
keeps scrolling *and* keeps extending the selection — you are not limited to
what is currently on screen. ``ctrl+shift+a`` selects the entire scrollback.

For terminals where Textual's mouse capture prevents the *terminal's own*
click-drag selection (classic conhost / some PowerShell setups), launch with
``hyperion shell --no-mouse``: Textual then never grabs the mouse, so the
terminal handles selection & copy natively.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from textual.app import App
from textual.binding import Binding

from hyperion.tui.screens.deliverable import DeliverableScreen
from hyperion.tui.screens.engagement import EngagementScreen
from hyperion.tui.screens.session import SessionScreen
from hyperion.tui.screens.splash import SplashScreen
from hyperion.tui.theme import (
    BG_CANVAS,
    CLAY,
    CLAY_DEEP,
    CLAY_SOFT,
    SIG_ERROR,
    SIG_SUCCESS,
    SIG_WARN,
    TEXT_PRIMARY,
)

logger = logging.getLogger(__name__)


class HyperionApp(App[None]):
    """The HYPERION terminal interface."""

    TITLE = "HYPERION"
    SUB_TITLE = "multi-agent consulting system"

    # Native drag-to-select is on everywhere. Custom widgets that paint their
    # own cells still stamp per-cell offsets (see Transcript.render_line) so a
    # drag resolves to real character positions instead of collapsing to
    # "select all".
    ALLOW_SELECT = True

    # Let a drag that runs off the top/bottom edge keep scrolling *and* keep
    # growing the selection, so users can highlight far more than one screenful.
    ENABLE_SELECT_AUTO_SCROLL = True

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
       (set in on_mount) — make it a clay wash with cream text.
       Both scrollback widgets must be listed: Transcript is a ScrollView now,
       not a RichLog, so a bare `RichLog` rule would silently stop matching it
       and long lines would clip instead of wrapping. */
    Transcript, RichLog {{
        text-wrap: wrap;
    }}
    """

    def __init__(
        self,
        reduced_motion: bool = False,
        demo: bool = False,
        mouse: bool = True,
        use_splash: bool = False,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._reduced_motion = reduced_motion
        self._demo = demo
        self._want_mouse = mouse
        self._use_splash = use_splash

    def on_mount(self) -> None:
        # Apply brand accents to Textual's theme variables where possible.
        with contextlib.suppress(Exception):
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
        self.push_screen(
            SplashScreen(reduced_motion=self._reduced_motion)
            if self._use_splash
            else SessionScreen(reduced_motion=self._reduced_motion, demo=self._demo)
        )

    def show_engagement(self) -> None:
        """Transition from splash to the engagement room."""
        self.pop_screen()
        self.push_screen(
            EngagementScreen(reduced_motion=self._reduced_motion, demo=self._demo)
        )

    def show_deliverable(self, result: Any) -> None:
        """Transition from engagement to the deliverable view."""
        self.push_screen(DeliverableScreen(engagement_result=result))

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
        # Clipboard write is best-effort; a failure must never propagate.
        except Exception as exc:  # noqa: BLE001 - best-effort, must not propagate  # pragma: no cover
            self._toast(f"copy failed: {exc}")

    def action_select_all(self) -> None:
        """Select the whole transcript so it can be copied at once."""
        try:
            screen = self.screen
            if isinstance(screen, SessionScreen):
                screen.select_all_transcript()
                self._toast("transcript selected — Ctrl+Shift+C to copy")
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.debug("%s: %s", "action_select_all", exc)

    def _gather_selection(self) -> str:
        """Return the currently selected text, if the Textual version exposes it."""
        # Textual >= 3 keeps selections per-screen; try the documented helper.
        try:
            get_sel = getattr(self.screen, "get_selected_text", None)
            if callable(get_sel):
                sel = get_sel()
                if isinstance(sel, str) and sel:
                    return sel
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.debug("%s: %s", "_gather_selection", exc)
        # Fallback: ask the session screen for its transcript selection.
        try:
            screen = self.screen
            if isinstance(screen, SessionScreen):
                return screen.selected_transcript_text()
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.debug("%s: %s", "_gather_selection", exc)
        return ""

    def _toast(self, msg: str) -> None:
        with contextlib.suppress(Exception):
            self.notify(msg, timeout=3)

    async def action_quit(self) -> None:
        """Tear down every service BEFORE the app exits.

        Teardown must happen here rather than in `on_unmount`. `on_unmount` was
        a *sync* handler that did:

            loop = asyncio.get_running_loop()
            loop.run_until_complete(stop_services())

        Textual dispatches unmount from inside its own running event loop, so
        `run_until_complete` on that same loop raises
        `RuntimeError: This event loop is already running`. The bare
        `except Exception: pass` swallowed it, so teardown reported no error and
        did nothing: `stop_services` ran ZERO times. Every `hyperion shell`
        session leaked its SearxNG and FlareSolverr containers, and the next
        boot inherited warm containers holding stale cached SERPs.

        `action_quit` is awaited by Textual, so the teardown genuinely
        completes before the process ends. Ctrl+Q, the quit binding and
        `App.exit()`-by-action all route through here.
        """
        await self._shutdown_services()
        self.exit()

    async def _shutdown_services(self) -> None:
        """Run service teardown exactly once, even if quit is triggered twice.

        Ctrl+Q during an already-running quit would otherwise start a second
        `docker stop`, and the two racing removals make the failure look
        intermittent.
        """
        if getattr(self, "_services_stopped", False):
            return
        self._services_stopped = True
        try:
            from hyperion.tui.boot import stop_services

            await stop_services()
        except Exception as exc:  # noqa: BLE001 - shutdown must never block exit
            # Surfaced, not silently swallowed: a failed teardown leaves real
            # containers running and the user needs to know.
            with contextlib.suppress(Exception):
                self.log(f"service teardown failed: {type(exc).__name__}: {exc}")

    async def on_unmount(self) -> None:
        """Safety net for exits that do not pass through `action_quit`.

        Async so it is awaited by Textual. Idempotent via `_shutdown_services`,
        so the normal Ctrl+Q path does not tear down twice.
        """
        await self._shutdown_services()


def run(
    reduced_motion: bool = False,
    demo: bool = False,
    mouse: bool = True,
    use_splash: bool = False,
) -> None:
    """Entry point used by the CLI `shell` command."""
    HyperionApp(
        reduced_motion=reduced_motion,
        demo=demo,
        mouse=mouse,
        use_splash=use_splash,
    ).run(mouse=mouse)
