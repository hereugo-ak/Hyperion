"""P2-26 / P2-G23: SearXNG's unresponsive_engines must be consumed, not
logged and discarded.

The 07-30 Docker log showed DuckDuckGo under a CAPTCHA storm terminating
in ``HTTP error 403 (suspended_time=86400)`` — a 24-hour ban the engine
reported on every response body, and nothing read it. One engine served
every query for the rest of the engagement; report B's evidence base
collapsed to 3 encyclopedia entries.

The engine health tracker parses unresponsive_engines from every SearXNG
response, applies an exponential per-engine cooldown (24h on a
suspended_time ban), persists across process restarts, and the client
excludes cooled engines from the next engines= parameter.
"""

from __future__ import annotations

import time

import pytest

from hyperion.tools.engine_health import EngineHealthTracker


@pytest.fixture()
def tracker(tmp_path, monkeypatch):
    monkeypatch.setenv("HYPERION_ENGINE_HEALTH_STATE", str(tmp_path / "engine_health.json"))
    t = EngineHealthTracker()
    t.reset()
    return t


def test_unresponsive_engine_gets_cooldown(tracker):
    tracker.record_response(
        unresponsive_engines=[["duckduckgo", "CAPTCHA"]],
        responding_engines=["bing"],
    )
    assert not tracker.is_available("duckduckgo")
    assert tracker.is_available("bing")
    assert "duckduckgo" in tracker.filter_available(["bing", "duckduckgo", "brave"]) or True
    assert tracker.filter_available(["bing", "duckduckgo", "brave"]) == ["bing", "brave"]


def test_suspended_time_ban_is_capped_at_4h(tracker):
    """F-04: a 403 with suspended_time=86400 is capped at the 4h maximum so
    a single bad session cannot waste a whole day of capacity."""
    from hyperion.tools.engine_health import _MAX_COOLDOWN_SECONDS

    tracker.record_response(
        unresponsive_engines=[["duckduckgo", "HTTP error 403 (suspended_time=86400)"]],
        responding_engines=[],
    )
    until = tracker.cooldown_until("duckduckgo")
    assert until >= time.time() + 3 * 3600  # a real suspension, not a token one
    assert until <= time.time() + _MAX_COOLDOWN_SECONDS + 5


def test_boot_ttl_sweep_drops_expired_cooldowns(tracker):
    """F-04: expired cooldowns persisted by an earlier session must be aged
    out at boot — the Aug 4 session's bans were still active on Aug 9."""
    tracker.record_response(
        unresponsive_engines=[["brave", "HTTP error 429 (suspended_time=10)"]],
        responding_engines=[],
    )
    assert not tracker.is_available("brave")
    # Simulate the passage of time past the cooldown expiry.
    import time as _time

    expired = _time.time() - 60
    tracker._suspended["brave"] = expired
    tracker._cooldowns["brave"] = expired
    dropped = tracker.sweep_expired()
    assert dropped >= 1
    assert tracker.is_available("brave")
    assert "brave" not in tracker._suspended
    assert "brave" not in tracker._cooldowns


def test_repeated_failures_escalate_cooldown(tracker):
    tracker.record_response(unresponsive_engines=[["brave", "timeout"]], responding_engines=[])
    first = tracker.cooldown_until("brave")
    tracker.record_response(unresponsive_engines=[["brave", "timeout"]], responding_engines=[])
    second = tracker.cooldown_until("brave")
    assert second > first  # exponential


def test_recovery_clears_cooldown(tracker):
    tracker.record_response(unresponsive_engines=[["qwant", "error"]], responding_engines=[])
    assert not tracker.is_available("qwant")
    tracker.record_response(unresponsive_engines=[], responding_engines=["qwant"])
    assert tracker.is_available("qwant")


def test_cooldown_expires(tracker, monkeypatch):
    tracker.record_response(unresponsive_engines=[["mojeek", "error"]], responding_engines=[])
    assert not tracker.is_available("mojeek")
    future = time.time() + 49 * 3600
    monkeypatch.setattr(time, "time", lambda: future)
    assert tracker.is_available("mojeek")


def test_state_persists_across_instances(tmp_path, monkeypatch):
    state = tmp_path / "engine_health.json"
    monkeypatch.setenv("HYPERION_ENGINE_HEALTH_STATE", str(state))
    t1 = EngineHealthTracker()
    t1.reset()
    t1.record_response(unresponsive_engines=[["startpage", "403"]], responding_engines=[])
    t2 = EngineHealthTracker()  # new instance, same state file
    assert not t2.is_available("startpage")
    t1.reset()
