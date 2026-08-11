"""D-I (overhaul3_audit.md W3/S8): a non-JSON SearXNG body cools the engines.

The docker log showed semantic scholar being re-queried repeatedly after
serving a non-JSON body (a proxy error page / throttling HTML is an
ENGINE-level health signal, not a generic client error). The old path only
opened the endpoint circuit — ``engine_health`` was never told, so the next
request asked the same dead engines again.

Fix: when ``response.json()`` raises a decode error, feed
``health.record_response(unresponsive=endpoint.engines, ...)`` BEFORE the
retry, so ``filter_available`` drops the cooled engines on the next attempt
and the profile fails over to a healthy replica.

This test reproduces the real flow: a web replica answering with a non-JSON
body while the scholar replica is healthy. Before the fix the web engines
are never cooled and the second attempt re-sends ``mwmbl,brave``; after the
fix they are marked unresponsive and the second attempt ships
``crossref,openalex``.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from hyperion.tools.searxng import (
    EngineTokenBucket,
    SearxNGClient,
    SearxngEndpoint,
    SearxngPool,
)


class _Health:
    """Tracks cooled engines the way the real tracker does."""

    def __init__(self) -> None:
        self.dead: set[str] = set()

    def filter_available(self, engines):
        return [engine for engine in engines if engine not in self.dead]

    def record_response(self, unresponsive_engines, responding_engines):
        self.dead.update(str(entry[0]) for entry in unresponsive_engines)
        self.dead.difference_update(str(engine) for engine in responding_engines)

    def record_degradation_if_needed(self, engines, *, floor=4):
        return None


class _WebResponse:
    """The dead replica: HTTP 200 but a non-JSON body (proxy error page)."""

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        raise json.JSONDecodeError("Expecting value", "line 1 column 1 (char 0)", 0)


class _ScholarResponse:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "results": [
                {
                    "title": "A paper",
                    "url": "https://paper.example/1",
                    "content": "Snippet",
                    "engine": "crossref",
                    "score": 1.0,
                }
            ],
            "unresponsive_engines": [],
        }


class _Http:
    """Serves non-JSON on the web replica and real JSON on the scholar one."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.calls: list[dict] = []

    async def get(self, path: str, params: dict | None = None) -> object:
        self.calls.append(dict(params or {}))
        if "scholar" in self.base_url:
            return _ScholarResponse()
        return _WebResponse()


@pytest.mark.asyncio
async def test_non_json_body_cools_engines_and_fails_over(monkeypatch) -> None:
    health = _Health()
    monkeypatch.setattr("hyperion.tools.searxng.get_engine_health", lambda: health)
    monkeypatch.setattr(
        EngineTokenBucket,
        "acquire",
        staticmethod(lambda engines: asyncio.sleep(0)),
    )

    client = SearxNGClient()
    client._pool = SearxngPool([
        SearxngEndpoint("http://web", "web", 8890, frozenset({"mwmbl", "brave"})),
        SearxngEndpoint(
            "http://scholar", "scholar", 8891, frozenset({"crossref", "openalex"})
        ),
    ])

    clients: dict[str, _Http] = {}

    async def _get_client(base_url=None):
        url = (base_url or "http://web").rstrip("/")
        if url not in clients:
            clients[url] = _Http(url)
        return clients[url]

    client._get_client = _get_client  # type: ignore[method-assign]

    response = await client._search_searxng_json(
        query="non-json probe query",
        num_results=5,
        categories="general",
        language="en",
        time_range="",
        engines="mwmbl,brave",
        safesearch=0,
    )

    # The non-JSON body must have been recorded as an engine-level failure.
    assert {"mwmbl", "brave"} <= health.dead, (
        "a non-JSON body must cool the profile's engines — the old path only "
        "opened the endpoint circuit and re-asked the same dead engines"
    )
    # The next attempt must fail over to the healthy replica, skipping the
    # cooled web engines entirely (audit: 'non-JSON marks engines cooling;
    # next request skips them').
    assert response is not None, (
        "after cooling the dead profile, the request must be served by a "
        "healthy replica instead of dying"
    )
    engines_sent = [
        c.get("engines", "") for http in clients.values() for c in http.calls
    ]
    assert "crossref,openalex" in engines_sent, (
        "the retry must ship the scholar replica's engines, not re-ask "
        "mwmbl/brave — semantic scholar kept being re-queried after serving "
        "garbage on 2026-07-30"
    )
    await client.close()
