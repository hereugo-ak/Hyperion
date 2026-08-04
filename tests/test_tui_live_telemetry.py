"""Regression tests for engagement telemetry on the compact TUI status bar."""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from enum import Enum
from types import ModuleType
from typing import Any

import pytest

from hyperion.tui.screens.session import SessionScreen
from hyperion.tui.widgets.metrics import Telemetry


class _Channel(str, Enum):
    STATUS = "status"
    FINDINGS = "findings"
    HANDOFF = "handoff"
    ESCALATION = "escalation"
    TUI = "tui"


@dataclass
class _Message:
    channel: _Channel
    payload: dict[str, Any]


@pytest.fixture(autouse=True)
def _stub_agent_bus(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep these TUI unit tests independent from optional agent dependencies."""
    agents_package = ModuleType("hyperion.agents")
    agents_package.__path__ = []  # type: ignore[attr-defined]
    bus_module = ModuleType("hyperion.agents.bus")
    bus_module.Channel = _Channel  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "hyperion.agents", agents_package)
    monkeypatch.setitem(sys.modules, "hyperion.agents.bus", bus_module)


class _FakeLog:
    """Small transcript stand-in for SessionScreen telemetry tests."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []
        self.updates: list[tuple[Any, dict[str, Any]]] = []

    def add_entry(self, badge: str, content: str, **_kwargs: Any) -> object:
        self.entries.append((badge, content))
        return object()

    def update_row(self, row: Any, **kwargs: Any) -> None:
        self.updates.append((row, kwargs))


@dataclass
class _FakeMetrics:
    tel: Telemetry = field(default_factory=lambda: Telemetry(status="running"))

    def set_agent(self, key: str, label: str, state: str) -> None:
        from hyperion.tui.widgets.metrics import AgentState

        current = self.tel.agents.get(key)
        if current is None:
            self.tel.agents[key] = AgentState(
                label=label,
                state=state,
                order=len(self.tel.agents),
            )
        else:
            current.label = label or current.label
            current.state = state

    def set_phase(self, phase: str) -> None:
        self.tel.phase = phase

    def add_tool_call(self, n: int = 1) -> None:
        self.tel.tool_calls += n

    def set_tokens(self, total: int) -> None:
        self.tel.tokens = max(0, total)

    def touch_provider(self, name: str) -> None:
        if name:
            self.tel.providers.add(name)


def _screen() -> tuple[SessionScreen, _FakeMetrics, _FakeLog]:
    screen = SessionScreen(reduced_motion=True)
    metrics = _FakeMetrics()
    log = _FakeLog()
    screen._metrics = lambda: metrics  # type: ignore[method-assign]
    screen._log = lambda: log  # type: ignore[method-assign]
    return screen, metrics, log


def _tui_message(payload: dict[str, Any]) -> _Message:
    return _Message(channel=_Channel.TUI, payload=payload)


def test_dag_initializes_unique_agent_total_and_states() -> None:
    screen, metrics, _log = _screen()

    screen._render_task_list(
        [
            {
                "id": "market-1",
                "agent": "market_analyst",
                "tier": "standard",
                "status": "running",
                "description": "size market",
            },
            {
                "id": "market-2",
                "agent": "market_analyst",
                "tier": "standard",
                "status": "pending",
                "description": "segment demand",
            },
            {
                "id": "risk-1",
                "agent": "risk_analyst",
                "tier": "strong",
                "status": "pending",
                "description": "map risks",
            },
        ]
    )

    assert set(metrics.tel.agents) == {"market_analyst", "risk_analyst"}
    assert metrics.tel.agents["market_analyst"].state == "queued"
    assert metrics.tel.agents["risk_analyst"].state == "queued"


@pytest.mark.parametrize(
    ("agent", "expected_phase"),
    [
        ("market_analyst", "execute"),
        ("synthesis_lead", "synthesize"),
        ("quality_gate", "quality"),
        ("presentation_designer", "deliver"),
        ("data_visualizer", "deliver"),
        ("render_engine", "deliver"),
    ],
)
def test_running_task_updates_agent_and_pipeline_phase(
    agent: str,
    expected_phase: str,
) -> None:
    screen, metrics, _log = _screen()

    screen._update_task_metrics(agent, "running")

    assert metrics.tel.agents[agent].state == "working"
    assert metrics.tel.phase == expected_phase


async def test_only_marked_tool_events_increment_tool_count() -> None:
    screen, metrics, log = _screen()

    await screen._on_bus_message(
        _tui_message(
            {
                "agent": "market_analyst",
                "tool": "searxng",
                "action": "access",
                "telemetry_kind": "tool_call",
                "display": False,
            }
        )
    )
    await screen._on_bus_message(
        _tui_message(
            {
                "agent": "ORCHESTRATOR",
                "tool": "system",
                "action": "status",
                "detail": "not a tool call",
            }
        )
    )

    assert metrics.tel.tool_calls == 1
    assert len(log.entries) == 1, "hidden tool telemetry must not flood the transcript"


async def test_llm_event_updates_tokens_and_provider_immediately() -> None:
    screen, metrics, _log = _screen()
    screen._live_router_telemetry = True
    screen._router_token_baseline = 100
    screen._router_token_summary = lambda: {  # type: ignore[method-assign]
        "total_tokens": 100,
        "by_provider": {},
    }

    await screen._on_bus_message(
        _tui_message(
            {
                "agent": "market_analyst",
                "tool": "llm",
                "action": "groq/model-x",
                "detail": "standard tier · OK · 120 chars",
                "provider": "groq",
                "total_tokens": 37,
            }
        )
    )

    assert metrics.tel.tokens == 37
    assert metrics.tel.providers == {"groq"}
    assert metrics.tel.tool_calls == 0, "an LLM display event is not an external tool call"


def test_router_polling_is_engagement_relative() -> None:
    screen, metrics, _log = _screen()
    screen._live_router_telemetry = True
    screen._router_token_baseline = 1_000
    screen._router_provider_call_baseline = {"groq": 2, "mistral": 1}
    screen._router_token_summary = lambda: {  # type: ignore[method-assign]
        "total_tokens": 1_480,
        "by_provider": {
            "groq": {"calls": 2, "total_tokens": 800},
            "mistral": {"calls": 3, "total_tokens": 680},
        },
    }

    screen._refresh_live_metrics()

    assert metrics.tel.tokens == 480
    assert metrics.tel.providers == {"mistral"}


def test_router_polling_does_not_overwrite_demo_counters() -> None:
    screen, metrics, _log = _screen()
    metrics.tel.tokens = 2_500
    screen._live_router_telemetry = False
    screen._router_token_summary = lambda: {  # type: ignore[method-assign]
        "total_tokens": 0,
        "by_provider": {},
    }

    screen._refresh_live_metrics()

    assert metrics.tel.tokens == 2_500


def test_telemetry_elapsed_advances_and_freezes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("hyperion.tui.widgets.metrics.time.monotonic", lambda: 12.75)
    telemetry = Telemetry(status="running", started=10.0)

    assert telemetry.elapsed() == 2.75
    telemetry.ended = 13.0
    assert telemetry.elapsed() == 3.0
