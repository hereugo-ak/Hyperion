"""D-G / D-H (overhaul3_audit.md W3/S6-7): profile-aware query shaping.

D-G — reference-profile queries must be condensed to a wikipedia
``/page/summary/{title}``-safe shape. The docker log proved it:

    12:31:05 wikipedia 400 Bad Request
    https://en.wikipedia.org/api/rest_v1/page/summary/competitor%20strategic%20moves%20space%2C%20recent%20announcements%2C...

wikipedia treats the whole query as an article title; a full-sentence
specialist query always 400s. The fix routes reference-profile queries
through the same ``SubAgentRunner._condense_query`` the sub-agents already
use (≤120 chars).

D-H — scholar-profile queries must be sanitized. openalex 400'd on a
145-char comma/``?`` sentence that sat UNDER the old 200-char clamp:

    12:02:35 openalex 400 Bad Request
    search=historical+failures+space+sector%2C+startups+failed%2C...What+caused+failure%3F+India

The old S10 clamp (``>200``) ran BEFORE the endpoint was known and only
measured length — punctuation was the real rejection. The fix strips
``,`` ``?`` ``.`` then clamps to 120 at a word boundary.

Each test drives the REAL ``_search_searxng_json`` against a single-profile
pool and asserts on the ``q`` parameter actually dispatched to the HTTP
client. Before the fix both queries go out unshaped.
"""

from __future__ import annotations

import asyncio

import pytest

from hyperion.tools.searxng import (
    EngineTokenBucket,
    SearxNGClient,
    SearxngEndpoint,
    SearxngPool,
)

# D-G: the audited wikipedia 400 — a >120-char sentence query.
REFERENCE_QUERY = (
    "Find competitor strategic moves in the Indian space sector, recent "
    "announcements, funding rounds and market positioning of startups"
)

# D-H: the audited openalex 400 — ~145 chars, comma- and ?-heavy.
SCHOLAR_QUERY = (
    "historical failures space sector, startups failed, What caused failure? "
    "India's space industry collapse, lessons from failed launches and "
    "bankrupt rocket companies"
)


class _Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {
            "results": [
                {
                    "title": "A result",
                    "url": "https://example.org/result",
                    "content": "Snippet",
                    "engine": "wikipedia",
                    "score": 1.0,
                }
            ],
            "unresponsive_engines": [],
        }


class _Http:
    """Captures the ``q`` parameter the client would have sent."""

    def __init__(self, base_url: str) -> None:
        self.base_url = base_url
        self.last_q: str | None = None

    async def get(self, path: str, params: dict | None = None) -> _Response:
        self.last_q = str((params or {}).get("q", ""))
        return _Response()


class _Health:
    def filter_available(self, engines):
        return list(engines)

    def record_response(self, unresponsive_engines, responding_engines):
        return None

    def record_degradation_if_needed(self, engines, *, floor=4):
        return None


@pytest.mark.asyncio
async def _dispatch(client: SearxNGClient, query: str, engines: str) -> str:
    """Run one real ``_search_searxng_json`` and return the dispatched ``q``."""
    http = _Http("http://ref")

    async def _get_client(base_url=None):
        return http

    client._get_client = _get_client  # type: ignore[method-assign]

    response = await client._search_searxng_json(
        query=query,
        num_results=5,
        categories="general",
        language="en",
        time_range="",
        engines=engines,
        safesearch=0,
    )
    assert response is not None, "the stub should always return one result"
    assert http.last_q is not None, "the HTTP stub must have been called"
    return http.last_q


@pytest.fixture(autouse=True)
def _no_token_bucket(monkeypatch):
    monkeypatch.setattr(
        EngineTokenBucket,
        "acquire",
        staticmethod(lambda engines: asyncio.sleep(0)),
    )
    monkeypatch.setattr(
        "hyperion.tools.searxng.get_engine_health",
        lambda: _Health(),
    )


# ── D-G · reference profile condenses the query ─────────────────────────────


@pytest.mark.asyncio
async def test_reference_profile_query_is_condensed_title_shape() -> None:
    """A sentence query routed to the reference replica must arrive ≤120
    chars, condensed — not the raw paragraph that 400'd wikipedia."""
    client = SearxNGClient()
    client._pool = SearxngPool([
        SearxngEndpoint("http://ref", "reference", 8890, frozenset({"wikipedia"})),
    ])

    dispatched = await _dispatch(client, REFERENCE_QUERY, "wikipedia")

    assert len(dispatched) <= 120, (
        f"reference query must be condensed to ≤120 chars, got {len(dispatched)}: "
        f"{dispatched!r}"
    )
    assert dispatched != REFERENCE_QUERY, (
        "the raw sentence must not reach a reference endpoint — that is the "
        "wikipedia /page/summary 400"
    )
    assert not dispatched.startswith("Find "), (
        "instruction prefixes are not title-shaped"
    )
    await client.close()


# ── D-H · scholar profile sanitizes the query ───────────────────────────────


@pytest.mark.asyncio
async def test_scholar_profile_query_is_sanitized() -> None:
    """A 145-char comma/? sentence routed to the scholar replica must arrive
    ≤120 chars with hard punctuation stripped — the openalex 400."""
    client = SearxNGClient()
    client._pool = SearxngPool([
        SearxngEndpoint(
            "http://scholar", "scholar", 8891, frozenset({"crossref", "openalex"})
        ),
    ])

    dispatched = await _dispatch(client, SCHOLAR_QUERY, "crossref,openalex")

    assert len(dispatched) <= 120, (
        f"scholar query must be clamped to ≤120 chars, got {len(dispatched)}"
    )
    for ch in (",", "?", "."):
        assert ch not in dispatched, (
            f"scholar query must have {ch!r} stripped — openalex 400s on "
            f"punctuation-heavy search= expressions; got: {dispatched!r}"
        )
    assert dispatched != SCHOLAR_QUERY, "the raw sentence must not reach scholar APIs"
    await client.close()


# ── web profile keeps its generic clamp (regression pin) ────────────────────


@pytest.mark.asyncio
async def test_web_profile_unchanged_for_short_queries() -> None:
    """Web-profile queries under 200 chars must pass through untouched — the
    S10 clamp is the only web transformation."""
    client = SearxNGClient()
    client._pool = SearxngPool([
        SearxngEndpoint("http://web", "web", 8892, frozenset({"mwmbl", "brave"})),
    ])

    dispatched = await _dispatch(
        client, "Indian space startup market size 2024", "mwmbl,brave"
    )

    assert dispatched == "Indian space startup market size 2024"
    await client.close()
