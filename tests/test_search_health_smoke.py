"""T-11 · D-06 · health reports OFFLINE when engines are dead.

The 07-30 run booted ``✓ SearXNG ready · 13 data sources ready`` because the
health check was a TCP port probe while every engine behind the open socket
was dead (DuckDuckGo 24h 403 ban, Bing silent-zero). These tests replay the
exact 07-30 state against a stubbed HTTP layer and assert the only honest
answers: DEGRADED/OFFLINE, never OK.
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from hyperion.obs.health import (
    MIN_SMOKE_RESULTS,
    _check_searxng,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        searxng_url="http://localhost:8888",
        searxng_host="localhost",
        searxng_port=8888,
    )


@pytest.fixture(autouse=True)
def _fresh_engine_health():
    """F-02: the smoke probe is now gated behind engine health. A leftover
    cooldown from another test must not make the probe defer."""
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

    def raise_for_status(self) -> None:  # always 200 in these fixtures
        return None

    def json(self) -> dict:
        return self._payload


def _stub_get(payload: dict):
    def _get(url, params=None, headers=None, timeout=None):
        assert params["q"]
        assert params["format"] == "json"
        assert headers["X-Forwarded-For"] == "127.0.0.1"
        return _MockResponse(payload)

    return _get


def test_health_offline_when_all_engines_banned(port_open, monkeypatch):
    """Replays the exact 07-30 state: port open, 0 results, engines suspended."""
    monkeypatch.setattr(
        httpx,
        "get",
        _stub_get(
            {
                "results": [],
                "unresponsive_engines": [
                    ["duckduckgo", "CAPTCHA"],
                    ["bing", "no results"],
                ],
            }
        ),
    )
    h = _check_searxng(_settings())
    assert h.status == "OFFLINE"  # was "OK" — the port was open
    assert "duckduckgo" in h.detail
    assert "bing" in h.detail


def test_health_ok_when_engines_answer(port_open, monkeypatch):
    results = [
        {"title": f"r{i}", "engine": "mojeek" if i % 2 else "brave"}
        for i in range(MIN_SMOKE_RESULTS + 2)
    ]
    monkeypatch.setattr(httpx, "get", _stub_get({"results": results}))
    h = _check_searxng(_settings())
    assert h.status == "OK"
    assert "mojeek" in h.detail and "brave" in h.detail


def test_health_degraded_when_some_engines_dead(port_open, monkeypatch):
    results = [{"title": f"r{i}", "engine": "mojeek"} for i in range(MIN_SMOKE_RESULTS)]
    monkeypatch.setattr(
        httpx,
        "get",
        _stub_get(
            {"results": results, "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}
        ),
    )
    h = _check_searxng(_settings())
    assert h.status == "DEGRADED"
    assert "DEAD" in h.detail and "duckduckgo" in h.detail


def test_health_offline_when_port_closed(port_closed):
    h = _check_searxng(_settings())
    assert h.status == "OFFLINE"
    assert "unreachable" in h.detail


def test_health_offline_when_smoke_query_raises(port_open, monkeypatch):
    def _boom(url, params=None, headers=None, timeout=None):
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(httpx, "get", _boom)
    h = _check_searxng(_settings())
    assert h.status == "OFFLINE"
    assert "ReadTimeout" in h.detail


def test_health_degraded_when_few_results(port_open, monkeypatch):
    """A thin response proves evidence exists but must not report full health."""
    results = [{"title": "only one", "engine": "mojeek"}]
    monkeypatch.setattr(httpx, "get", _stub_get({"results": results}))
    h = _check_searxng(_settings())
    assert h.status == "DEGRADED"
    assert "1 results" in h.detail
