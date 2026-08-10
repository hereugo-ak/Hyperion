"""P1.5 · overhaul §6 P1 · boot smoke is LOCAL-ONLY.

The Aug-9/10 boot smoke fired a live search query at every profile one minute
before the engagement began — the exact traffic that tripped the fleet into
403/429. The Aug-10 boot line ``SEARCH ✓ scholar:ok · reference:ok · web:ok``
was a process check, not a corpus check: every engine behind the open sockets
was already banned.

P1.5 replaces the smoke with local-only readiness: TCP port + ``/config``
(local, zero upstream) + persisted engine health. These tests pin the
contract — most importantly that the probe NEVER issues a search query.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from hyperion.obs.health import _check_searxng


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        searxng_url="http://localhost:8888",
        searxng_host="localhost",
        searxng_port=8888,
    )


@pytest.fixture(autouse=True)
def _fresh_engine_health():
    """Isolate persisted engine health from other tests' state."""
    from hyperion.tools.engine_health import get_engine_health, reset_engine_health

    reset_engine_health()
    get_engine_health().reset()
    yield
    reset_engine_health()


@pytest.fixture
def port_open(monkeypatch):
    monkeypatch.setattr("hyperion.obs.health._check_port", lambda *a, **k: True)


@pytest.fixture
def port_closed(monkeypatch):
    monkeypatch.setattr("hyperion.obs.health._check_port", lambda *a, **k: False)


class _MockResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


def test_health_offline_when_port_closed(port_closed):
    """No replica reachable → OFFLINE, never a silent OK."""
    h = _check_searxng(_settings())
    assert h.status == "OFFLINE"
    assert "unreachable" in h.detail


def test_health_offline_when_config_unreachable(port_open, monkeypatch):
    """The instance is up but /config fails → OFFLINE (was OK via port)."""
    def _boom(url, timeout=None):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "get", _boom)
    h = _check_searxng(_settings())
    assert h.status == "OFFLINE"
    assert "ReadTimeout" in h.detail


def test_config_probe_never_issues_a_search_query(port_open, monkeypatch):
    """THE P1.5 contract: boot hits /config only — no /search, no q param.

    A regression to a live smoke would send ``q=...&format=json`` to
    ``/search``; this stub fails the test the moment that happens.
    """
    calls: list[dict] = []

    def _get(url, timeout=None):
        calls.append({"url": url, "timeout": timeout})
        return _MockResponse({"engines": []})

    monkeypatch.setattr(httpx, "get", _get)
    h = _check_searxng(_settings())
    assert calls, "boot must probe each replica's /config"
    for call in calls:
        assert call["url"].endswith("/config"), f"boot probe hit a search path: {call['url']}"
    assert h.status == "OK"


def test_health_ok_when_all_replicas_serve_locally(port_open, monkeypatch):
    def _get(url, timeout=None):
        return _MockResponse({"engines": []})

    monkeypatch.setattr(httpx, "get", _get)
    h = _check_searxng(_settings())
    assert h.status == "OK"
    assert "3/3 replicas local" in h.detail


def test_health_degraded_when_replica_has_no_capacity(port_open, monkeypatch):
    """A reachable replica whose engines are ALL suspended (persisted bans)
    must report DEGRADED, not OK — readiness includes capacity."""
    from hyperion.tools.engine_health import get_engine_health

    health = get_engine_health()
    # Suspend the web replica's whole engine set, as an earlier session's
    # bans would leave behind.
    health.record_response(
        [
            ["mojeek", "HTTP 403 suspended_time=180"],
            ["mwmbl", "HTTP 429 suspended_time=180"],
            ["brave", "HTTP 403 suspended_time=180"],
            ["yep", "HTTP 403 suspended_time=180"],
        ],
        [],
    )

    def _get(url, timeout=None):
        return _MockResponse({"engines": []})

    monkeypatch.setattr(httpx, "get", _get)
    h = _check_searxng(_settings())
    assert h.status == "DEGRADED"
    assert "no capacity" in h.detail
    assert "mwmbl" in h.detail


def test_health_ok_lists_partially_cooling_engines(port_open, monkeypatch):
    """A partially-cooling replica still has capacity → OK, with the cooling
    engines named so the operator sees the persisted state."""
    from hyperion.tools.engine_health import get_engine_health

    get_engine_health().record_response([["mwmbl", "HTTP 429 suspended_time=180"]], [])

    def _get(url, timeout=None):
        return _MockResponse({"engines": []})

    monkeypatch.setattr(httpx, "get", _get)
    h = _check_searxng(_settings())
    assert h.status == "OK"
    assert "cooling: mwmbl" in h.detail
