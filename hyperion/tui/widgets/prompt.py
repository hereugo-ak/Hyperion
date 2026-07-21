"""HYPERION prompt bar — the command line (Content-native, premium cursor).

    ◈ hyperion@orchestrator ~ ❯ █

- ◈ session glyph (violet), hyperion (cyan), @ (dim), agent context (violet),
  ~ scope (dim), ❯ caret (magenta), then a CYAN BLOCK cursor.
- Cursor is a solid block █ that blinks 530 ms on / 530 ms off (step-end, no
  fade). During active generation it stops blinking and shows a steady,
  faintly-glowing block; blink resumes when idle.
- Enter submits; ↑/↓ history; Ctrl+C cancel, Ctrl+L clear, F1 help.

Typed text is transient (it moves into the transcript on submit), so the
prompt is a light custom widget — the copyable surfaces are the transcript
and the metrics rail.
"""

from __future__ import annotations

from textual import events
from textual.message import Message
from textual.widget import Widget

from hyperion.tui.content import build_line, span
from hyperion.tui.motion.color import mix
from hyperion.tui.theme import (
    BG_CANVAS,
    CLAY,
    CLAY_DEEP,
    TEXT_DIM,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_BLINK_MS = 530


class PromptSubmitted(Message):
    def __init__(self, value: str) -> None:
        self.value = value
        super().__init__()


class ClearScrollback(Message):
    pass


class CancelTurn(Message):
    pass


class PromptBar(Widget, can_focus=True):
    """Persistent bottom prompt with a blinking clay block cursor."""

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
        self._busy = False
        self._history: list[str] = []
        self._hidx = 0
        self._blink_timer = None

    def on_mount(self) -> None:
        self._blink_timer = self.set_interval(_BLINK_MS / 1000, self._blink)

    def _blink(self) -> None:
        if self._busy:
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
        elif event.is_printable and event.character:
            self._buffer += event.character
            self.refresh()
            event.stop()

    # ── render ──────────────────────────────────────────────────────────────────

    def render(self):
        spans = [
            span("  ", ""),
            span("hyperion", f"bold {CLAY}"),
            span("@", TEXT_DIM),
            span(self._agent, TEXT_SECONDARY),
            span(f" {self._scope} ", TEXT_DIM),
            span("❯ ", f"bold {CLAY}"),
            span(self._buffer, TEXT_PRIMARY),
        ]
        if self._busy:
            spans.append(span("▊", mix(BG_CANVAS, CLAY, 0.7)))
        elif self._cursor_on:
            spans.append(span("▊", CLAY))
        else:
            spans.append(span(" ", ""))
        return build_line(*spans)
