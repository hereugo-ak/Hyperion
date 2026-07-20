"""HYPERION prompt bar. Spec §9 (prompt) + §8.2 (cursor).

    ◈ hyperion@orchestrator ~ ❯ █

- ◈ session glyph (violet), hyperion (cyan), @ (dim), agent context (violet),
  ~ scope (dim), ❯ caret (magenta), then a CYAN BLOCK cursor.
- Cursor is a solid block █ (never underscore/bar). Blinks 500 ms on / 500 ms
  off, step-end (no fade). During active generation it stops blinking and
  shows a steady, faintly-glowing block; blink resumes when idle (§8.2).
- Enter submits; ↑/↓ history (instant, no animation); Ctrl+C cancel,
  Ctrl+L clear, Ctrl+D exit, F1 help.
"""

from __future__ import annotations

from rich.text import Text
from textual import events
from textual.message import Message
from textual.widget import Widget

from hyperion.tui.motion.color import mix
from hyperion.tui.theme import (
    BG_CANVAS,
    BRAND_CYAN,
    BRAND_MAGENTA,
    BRAND_VIOLET,
    TEXT_DIM,
    TEXT_PRIMARY,
)

_BLINK_MS = 500


class PromptSubmitted(Message):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__()


class ClearScrollback(Message):
    pass


class CancelTurn(Message):
    pass


class PromptBar(Widget, can_focus=True):
    """Persistent bottom prompt with a blinking cyan block cursor."""

    DEFAULT_CSS = """
    PromptBar {
        height: 1;
        width: 100%;
    }
    """

    def __init__(self, agent_context: str = "orchestrator", scope: str = "~", **kwargs) -> None:
        super().__init__(**kwargs)
        self._buffer = ""
        self._agent = agent_context
        self._scope = scope
        self._cursor_on = True
        self._busy = False  # steady glow while an engagement runs
        self._history: list[str] = []
        self._hidx = 0
        self._blink_timer = None

    def on_mount(self) -> None:
        self._blink_timer = self.set_interval(_BLINK_MS / 1000, self._blink)

    def _blink(self) -> None:
        if self._busy:
            # steady during generation — no toggle, keep it visible
            if not self._cursor_on:
                self._cursor_on = True
                self.refresh()
            return
        self._cursor_on = not self._cursor_on
        self.refresh()

    # ── public API ────────────────────────────────────────────────────────────

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._cursor_on = True
        self.refresh()

    def set_agent_context(self, agent: str) -> None:
        self._agent = agent
        self.refresh()

    # ── input handling ─────────────────────────────────────────────────────────

    def on_key(self, event: events.Key) -> None:
        key = event.key
        if key == "enter":
            value = self._buffer.strip()
            if value:
                self._history.append(value)
                self._hidx = len(self._history)
                self._buffer = ""
                self.refresh()
                self.post_message(PromptSubmitted(value))
            event.stop()
        elif key == "backspace":
            self._buffer = self._buffer[:-1]
            self.refresh()
            event.stop()
        elif key == "up":
            if self._history:
                self._hidx = max(0, self._hidx - 1)
                self._buffer = self._history[self._hidx]
                self.refresh()
            event.stop()
        elif key == "down":
            if self._history:
                self._hidx = min(len(self._history), self._hidx + 1)
                self._buffer = self._history[self._hidx] if self._hidx < len(self._history) else ""
                self.refresh()
            event.stop()
        elif key == "ctrl+l":
            self.post_message(ClearScrollback())
            event.stop()
        elif key == "ctrl+c":
            self.post_message(CancelTurn())
            event.stop()
        elif key == "ctrl+d":
            self.app.exit()
            event.stop()
        elif event.is_printable and event.character:
            self._buffer += event.character
            self.refresh()
            event.stop()

    # ── render ──────────────────────────────────────────────────────────────────

    def render(self) -> Text:
        out = Text()
        out.append("  ◈ ", style=BRAND_VIOLET)
        out.append("hyperion", style=f"bold {BRAND_CYAN}")
        out.append("@", style=TEXT_DIM)
        out.append(self._agent, style=BRAND_VIOLET)
        out.append(f" {self._scope} ", style=TEXT_DIM)
        out.append("❯ ", style=f"bold {BRAND_MAGENTA}")
        out.append(self._buffer, style=TEXT_PRIMARY)

        # Block cursor — solid █, teleports (no easing). §8.2
        if self._busy:
            glow = mix(BG_CANVAS, BRAND_CYAN, 0.8)
            out.append("█", style=glow)
        elif self._cursor_on:
            out.append("█", style=BRAND_CYAN)
        else:
            out.append(" ")
        return out
