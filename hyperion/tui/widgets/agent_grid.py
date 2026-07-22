"""HYPERION live agent status grid — real-time agent roster display.

Shows all active agents with:
    - Agent name (color-coded by tier)
    - Model tier: MICRO, FAST, STANDARD, STRONG, DEEP
    - State: IDLE, WORKING, WAITING, DONE, BLOCKED
    - Tools active
    - Findings count
    - Sub-agents spawned

Updates in real-time via the AgentBus ``status`` channel (within 100ms).

Per ARCHITECTURE.md §8.5.
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
    CLAY_SOFT,
    GOLD,
    ROSE,
    SAGE,
    SIG_ERROR,
    SIG_SUCCESS,
    SIG_WARN,
    SKY,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    badge_color,
)

_FPS = 12

_TIER_COLOR = {
    "MICRO": TEXT_DIM,
    "FAST": SKY,
    "STANDARD": CLAY,
    "STANDARD+": CLAY,
    "STRONG": CLAY_DEEP,
    "DEEP": GOLD,
}

_STATE_ICON = {
    "idle": "○",
    "working": "●",
    "waiting": "◐",
    "done": "✓",
    "blocked": "✗",
    "queued": "○",
}

_STATE_COLOR = {
    "idle": TEXT_DIM,
    "working": CLAY,
    "waiting": SIG_WARN,
    "done": SIG_SUCCESS,
    "blocked": SIG_ERROR,
    "queued": TEXT_DIM,
}


@dataclass
class GridAgent:
    """One agent's display state in the grid."""
    key: str
    name: str
    badge: str
    tier: str = "STANDARD"
    state: str = "idle"
    tools_active: str = ""
    findings: int = 0
    sub_agents: int = 0
    order: int = 0


class AgentGrid(Static):
    """Live agent status grid — updates in real-time via bus messages.

    Shows all active agents with name, tier, state, tools, findings count,
    and sub-agent count. Sorted by insertion order.
    """

    DEFAULT_CSS = """
    AgentGrid {
        width: 100%;
        height: auto;
        min-height: 3;
        padding: 0 1;
        background: #1A1918;
    }
    """

    def __init__(self, **kwargs) -> None:
        self._agents: dict[str, GridAgent] = {}
        self._frame = 0
        self._timer = None
        super().__init__(self._render_grid(), **kwargs)

    # ── public API ────────────────────────────────────────────────────────────

    def set_agent(
        self,
        key: str,
        name: str,
        badge: str,
        tier: str = "STANDARD",
        state: str = "idle",
        tools: str = "",
        findings: int = 0,
        sub_agents: int = 0,
    ) -> None:
        existing = self._agents.get(key)
        if existing is None:
            existing = GridAgent(
                key=key, name=name, badge=badge, tier=tier, state=state,
                tools_active=tools, findings=findings, sub_agents=sub_agents,
                order=len(self._agents),
            )
            self._agents[key] = existing
        else:
            existing.state = state
            if tier:
                existing.tier = tier
            if tools:
                existing.tools_active = tools
            if findings:
                existing.findings = findings
            if sub_agents:
                existing.sub_agents = sub_agents
        self._ensure_timer()
        self._repaint()

    def update_state(self, key: str, state: str) -> None:
        a = self._agents.get(key)
        if a is not None:
            a.state = state
            self._repaint()

    def add_finding(self, key: str) -> None:
        a = self._agents.get(key)
        if a is not None:
            a.findings += 1
            self._repaint()

    def add_sub_agent(self, key: str) -> None:
        a = self._agents.get(key)
        if a is not None:
            a.sub_agents += 1
            self._repaint()

    def clear(self) -> None:
        self._agents.clear()
        self._repaint()

    def get_active_count(self) -> tuple[int, int]:
        active = sum(1 for a in self._agents.values() if a.state == "working")
        return active, len(self._agents)

    # ── animation ────────────────────────────────────────────────────────────

    def _ensure_timer(self) -> None:
        if self._timer is None:
            self._timer = self.set_interval(1 / _FPS, self._on_frame)

    def _on_frame(self) -> None:
        self._frame += 1
        busy = any(a.state == "working" for a in self._agents.values())
        if busy:
            self._repaint()
        elif self._timer is not None:
            self._timer.stop()
            self._timer = None

    def _repaint(self) -> None:
        try:
            self.update(self._render_grid())
        except Exception:
            pass

    # ── render ────────────────────────────────────────────────────────────────

    def _render_grid(self):
        lines: list = []

        active, total = self.get_active_count()
        lines.append([
            span("  AGENTS", f"bold {TEXT_SECONDARY}"),
            span(f"  {active}/{total} active", TEXT_DIM),
        ])

        if not self._agents:
            lines.append([span("  no agents dispatched yet", TEXT_GHOST)])
            return build(lines)

        for a in sorted(self._agents.values(), key=lambda x: x.order):
            bc = badge_color(a.badge)
            tc = _TIER_COLOR.get(a.tier.upper(), TEXT_DIM)
            sc = _STATE_COLOR.get(a.state.lower(), TEXT_DIM)
            si = _STATE_ICON.get(a.state.lower(), "○")

            row = [
                span("  ", ""),
                span(si + " ", f"bold {sc}"),
                span(a.badge[:10].ljust(10), f"bold {bc}"),
                span(a.tier.upper()[:10].ljust(10), tc),
            ]

            if a.state == "working":
                row.append(span(*spinner_span(self._frame)))
                row.append(span(" ", ""))
            else:
                row.append(span("  ", ""))

            row.append(span(a.state[:8].ljust(8), sc))

            if a.findings > 0:
                row.append(span(f"  {a.findings}f", TEXT_DIM))
            if a.sub_agents > 0:
                row.append(span(f"  {a.sub_agents}sub", TEXT_DIM))

            lines.append(row)

        return build(lines)
