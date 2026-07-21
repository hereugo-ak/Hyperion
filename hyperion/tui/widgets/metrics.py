"""HYPERION metrics rail — always-visible engagement telemetry (Content-native).

A slim right-hand column that never scrolls away. Shows, live:

    ◆ ENGAGEMENT
      status      running
      elapsed     00:12
      phase       execute

    ◆ AGENTS      3 / 5 active
      ▸ MARKET    working  ⠹
      ▸ FINANCE   done     ✓
      …

    ◆ RESOURCES
      tools       14 calls
      tokens      128.4k
      providers   groq · cerebras

Built from :class:`textual.content.Content`, so every character is
selectable/copyable and layout height measures correctly.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from textual.widgets import Static

from hyperion.tui.content import build, line, span
from hyperion.tui.motion.indicators import spinner_span
from hyperion.tui.theme import (
    CLAY,
    CLAY_DEEP,
    SIG_ERROR,
    SIG_SUCCESS,
    SIG_WARN,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    badge_color,
)

_STATE_STYLE = {
    "working": (CLAY, "working"),
    "waiting": (SIG_WARN, "waiting"),
    "done": (SIG_SUCCESS, "done"),
    "blocked": (SIG_ERROR, "blocked"),
    "queued": (TEXT_DIM, "queued"),
}

_FPS = 12


@dataclass
class AgentState:
    label: str
    state: str = "queued"
    order: int = 0


@dataclass
class Telemetry:
    status: str = "idle"  # idle / running / done / error
    phase: str = "—"
    started: float | None = None
    ended: float | None = None
    tool_calls: int = 0
    tokens: int = 0
    providers: set[str] = field(default_factory=set)
    agents: dict[str, AgentState] = field(default_factory=dict)

    def elapsed(self) -> float:
        if self.started is None:
            return 0.0
        end = self.ended if self.ended is not None else time.monotonic()
        return end - self.started


class MetricsRail(Static):
    """Live, always-on engagement telemetry."""

    DEFAULT_CSS = """
    MetricsRail {
        width: 36;
        min-width: 30;
        padding: 1 2;
        background: #1A1918;
        border-left: solid #2A2926;
    }
    """

    def __init__(self, **kwargs) -> None:
        self.tel = Telemetry()
        self._frame = 0
        self._timer = None
        super().__init__(self._build(), **kwargs)

    # ── state mutation ─────────────────────────────────────────────────────────

    def start(self, phase: str = "decompose") -> None:
        self.tel = Telemetry(status="running", phase=phase, started=time.monotonic())
        self._ensure_timer()
        self._repaint()

    def finish(self, ok: bool = True) -> None:
        self.tel.status = "done" if ok else "error"
        self.tel.ended = time.monotonic()
        self._repaint()

    def reset(self) -> None:
        self.tel = Telemetry()
        self._repaint()

    def set_phase(self, phase: str) -> None:
        self.tel.phase = phase
        self._repaint()

    def touch_provider(self, name: str) -> None:
        if name:
            self.tel.providers.add(name)
            self._repaint()

    def add_tool_call(self, n: int = 1) -> None:
        self.tel.tool_calls += n
        self._repaint()

    def add_tokens(self, n: int) -> None:
        self.tel.tokens += max(0, n)
        self._repaint()

    def set_agent(self, key: str, label: str, state: str) -> None:
        a = self.tel.agents.get(key)
        if a is None:
            a = AgentState(label=label, state=state, order=len(self.tel.agents))
            self.tel.agents[key] = a
        else:
            a.state = state
            if label:
                a.label = label
        self._ensure_timer()
        self._repaint()

    # ── animation (only while something is working) ───────────────────────────

    def _ensure_timer(self) -> None:
        if self._timer is None:
            self._timer = self.set_interval(1 / _FPS, self._on_frame)

    def _on_frame(self) -> None:
        self._frame += 1
        busy = self.tel.status == "running" or any(
            a.state == "working" for a in self.tel.agents.values()
        )
        if busy:
            self._repaint()
        elif self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _repaint(self) -> None:
        try:
            self.update(self._build())
        except Exception:
            pass

    # ── build ─────────────────────────────────────────────────────────────────

    def _section(self, title: str, right: str = "") -> list:
        spans = [span("▍ ", CLAY), span(title, f"bold {TEXT_SECONDARY}")]
        if right:
            spans.append(span("   ", ""))
            spans.append(span(right, TEXT_DIM))
        return spans

    def _kv(self, key: str, value: str, vstyle: str = TEXT_PRIMARY) -> list:
        return [span("  " + key.ljust(11), TEXT_DIM), span(value, vstyle)]

    def _build(self):
        t = self.tel
        lines: list = []

        status_style = {
            "idle": TEXT_DIM,
            "running": CLAY,
            "done": SIG_SUCCESS,
            "error": SIG_ERROR,
        }.get(t.status, TEXT_PRIMARY)

        lines.append(self._section("ENGAGEMENT"))
        lines.append(self._kv("status", t.status, status_style))
        el = int(t.elapsed())
        lines.append(self._kv("elapsed", f"{el//60:02d}:{el%60:02d}"))
        lines.append(self._kv("phase", t.phase, CLAY_DEEP))
        lines.append(line(""))

        active = sum(1 for a in t.agents.values() if a.state == "working")
        total = len(t.agents)
        lines.append(self._section("AGENTS", f"{active} / {total} active" if total else ""))
        if not t.agents:
            lines.append([span("  no agents dispatched yet", TEXT_GHOST)])
        else:
            for a in sorted(t.agents.values(), key=lambda a: a.order)[:12]:
                color, word = _STATE_STYLE.get(a.state, (TEXT_DIM, a.state))
                row = [
                    span("  ▸ ", badge_color(a.label)),
                    span(a.label[:9].ljust(10), TEXT_SECONDARY),
                    span(word.ljust(8), color),
                ]
                if a.state == "working":
                    row.append(span(*spinner_span(self._frame)))
                elif a.state == "done":
                    row.append(span("✓", SIG_SUCCESS))
                elif a.state == "blocked":
                    row.append(span("✗", SIG_ERROR))
                lines.append(row)
        lines.append(line(""))

        lines.append(self._section("RESOURCES"))
        lines.append(self._kv("tools", f"{t.tool_calls} calls", SIG_WARN))
        tok = f"{t.tokens/1000:.1f}k" if t.tokens >= 1000 else str(t.tokens)
        lines.append(self._kv("tokens", tok))
        provs = sorted(t.providers)
        if not provs:
            lines.append(self._kv("providers", "—", CLAY))
        else:
            joined = " · ".join(provs)
            # ~19 usable cols for the value column in a width-36 rail; if the
            # joined list fits, keep it inline — otherwise stack one per line
            # so it never wraps mid-token ("· groq" orphaned on the next line).
            if len(joined) <= 19:
                lines.append(self._kv("providers", joined, CLAY))
            else:
                lines.append(self._kv("providers", f"{len(provs)} active", CLAY))
                for name in provs:
                    lines.append([span("    · ", TEXT_DIM), span(name, CLAY)])

        return build(lines)
