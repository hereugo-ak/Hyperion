"""OVERHAUL5 W3 (D-05) — paid-provider suspension semantics + TUI visibility.

Pre-W3: a single 403 exiled a paid provider for the whole run (permanent),
and every provider failure hid at logger.warning — the 08-12 run's paid
providers sat unused with zero visible trace. W3: 403 = 120s cooldown,
permanent only after 3 consecutive failures; every provider attempt emits a
TUI system.log line.

Fail-first: these tests fail on the pre-W3 code (403 -> permanent on the
first error; no TUI emission).
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.adapters.exa import ExaAdapter
from hyperion.search.adapters.you import YouAdapter
from hyperion.search.orchestrator import SearchOrchestrator
from hyperion.search.suspension import SuspensionRegistry


def test_403_cooldown_then_retry_then_permanent_after_three() -> None:
    """[FF] 403 -> 120s cooldown (retryable); 3 consecutive -> permanent;
    a success resets the counter."""
    reg = SuspensionRegistry()
    t0 = 1000.0

    reg.record_error("You", "403", now=t0)
    st = reg.states["You"]
    assert not st.permanent, "first 403 must NOT be permanent (pre-W3 exiled here)"
    assert st.suspended_until == t0 + 120.0
    assert not reg.is_available("You", now=t0 + 119.0)
    assert reg.is_available("You", now=t0 + 121.0), "cooldown lapses -> retried"

    # 3rd consecutive 403 -> permanent
    reg.record_error("You", "403", now=t0 + 121.0)
    reg.record_error("You", "403", now=t0 + 122.0)
    assert reg.states["You"].permanent, "3 consecutive 403s -> permanent for run"
    assert not reg.is_available("You", now=t0 + 9999.0)


def test_success_resets_403_counter() -> None:
    """A successful call between 403s must clear the counter — one 403 after
    a success is a fresh incident, not the 3rd strike."""
    reg = SuspensionRegistry()
    reg.record_error("You", "403", now=1000.0)
    reg.record_error("You", "403", now=1100.0)
    reg.record_success("You")
    assert reg.states["You"].error_counts.get("403", 0) == 0
    reg.record_error("You", "403", now=1200.0)
    reg.record_error("You", "403", now=1300.0)
    assert not reg.states["You"].permanent, "counter was reset by the success"


class _FailingAdapter(BaseAdapter):
    name = "You"

    def __init__(self) -> None:
        super().__init__(None)

    async def search(self, query: str, num_results: int = 10) -> list:
        raise TransientProviderError("403", "access denied")


class _Bus:
    def __init__(self) -> None:
        self.published: list[tuple] = []

    async def publish(self, channel, msg_type, sender, payload):
        self.published.append((sender, payload))


@pytest.mark.asyncio
async def test_provider_failure_emits_tui_line() -> None:
    """[FF] A provider failure surfaces in the TUI system.log with the
    provider name and the resulting suspension state."""
    bus = _Bus()
    orch = SearchOrchestrator(adapters={YouAdapter: _FailingAdapter()})
    with patch("hyperion.agents.bus.get_bus", return_value=bus):
        results = await orch.search("india manufacturing", num_results=10)
        await asyncio.sleep(0.01)  # let the scheduled TUI emit task run
    assert results == []
    assert any(
        payload.get("tool") == "system.log"
        and "You" in payload.get("action", "")
        and "403" in payload.get("action", "")
        for _, payload in bus.published
    ), "a TUI-visible 'paid You: 403 … cooldown' line must be emitted"


@pytest.mark.asyncio
async def test_provider_success_emits_tui_line() -> None:
    """A successful provider call is visible too (result count)."""
    bus = _Bus()

    class _OkAdapter(BaseAdapter):
        name = "Exa"

        def __init__(self) -> None:
            super().__init__(None)

        async def search(self, query: str, num_results: int = 10) -> list:
            from hyperion.search.types import SearchResult

            return [
                SearchResult(title="t", url=f"https://e{i}.example.com/p",
                             snippet="snippet " * 6, engine="exa", backend="Exa")
                for i in range(6)
            ]

    orch = SearchOrchestrator(adapters={ExaAdapter: _OkAdapter()})
    with patch("hyperion.agents.bus.get_bus", return_value=bus):
        results = await orch.search("india manufacturing", num_results=10)
        await asyncio.sleep(0.01)  # let the scheduled TUI emit task run
    assert len(results) >= 5
    assert any(
        "Exa" in payload.get("action", "") and "result(s)" in payload.get("action", "")
        for _, payload in bus.published
    )
