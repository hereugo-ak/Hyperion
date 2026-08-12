"""OVERHAUL5 W2 (D-04) — paid-chain no-re-entry + recursion guard tests.

The in-orchestrator ``SearxNGAdapter`` wrapped the SAME ``SearxNGClient.search``
the caller had just exhausted — a re-entry with no recursion guard, bounded
only by the 600-search budget. W2: (a) the paid chain excludes the
SearxNGAdapter, (b) a ContextVar guard makes any re-entrant ``search`` return
empty immediately.

Fail-first: on the pre-W2 code these tests fail — the orchestrator re-entered
``SearxNGClient.search`` (test 1), the guard flag did not exist (tests 2-3).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hyperion.search.adapters.searxng import SearxNGAdapter
from hyperion.search.orchestrator import SearchOrchestrator
from hyperion.tools.searxng import _IN_PAID_CHAIN, SearchResponse, SearxNGClient


class _NoValkey:
    async def get_json(self, key: str):
        return None

    async def set_json(self, key: str, payload: dict, ttl: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None


class _FakePaid:
    """Returns enough results to satisfy MIN_RESULTS — asserts it was called."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls = 0
        self._resp = None

    async def search(self, query: str, num_results: int = 10) -> list:
        self.calls += 1
        if self._resp is not None:
            return list(self._resp)
        return []


@pytest.mark.asyncio
async def test_orchestrator_exclude_skips_searxng_adapter() -> None:
    """[FF] With exclude={SearxNGAdapter}, the real SearxNG adapter is never
    consulted — the re-entry vector is closed and paid tiers are reached."""
    real_searxng = SearxNGAdapter(None)
    you = _FakePaid("You")
    exa = _FakePaid("Exa")
    tavily = _FakePaid("Tavily")
    yep = _FakePaid("Yep")

    from hyperion.search.adapters.exa import ExaAdapter
    from hyperion.search.adapters.tavily import TavilyAdapter
    from hyperion.search.adapters.yep import YepAdapter
    from hyperion.search.adapters.you import YouAdapter

    empty_resp = SearchResponse(query="q", results=[])
    with patch.object(
        SearxNGClient, "search", new=AsyncMock(return_value=empty_resp)
    ) as client_search:
        orch = SearchOrchestrator(adapters={
            SearxNGAdapter: real_searxng,
            YouAdapter: you,
            ExaAdapter: exa,
            TavilyAdapter: tavily,
            YepAdapter: yep,
        })
        results = await orch.search("india manufacturing", num_results=10, exclude={SearxNGAdapter})

    assert client_search.await_count == 0, (
        "SearxNGClient.search must never be re-entered from the paid chain"
    )
    assert you.calls == 3, "You is the first paid tier — must be called every loop attempt"
    assert exa.calls == 3
    assert tavily.calls >= 1
    assert yep.calls >= 1
    assert results == []


@pytest.mark.asyncio
async def test_recursion_guard_returns_empty_without_rotation() -> None:
    """[FF] Inside the paid chain, a re-entrant search returns empty BEFORE
    the rotation machinery — no budget burn, no recursion."""
    client = SearxNGClient()
    rotation = AsyncMock(return_value=None)
    client._search_with_rotation = rotation  # type: ignore[method-assign]

    token = _IN_PAID_CHAIN.set(True)
    try:
        resp = await client.search("india manufacturing", num_results=5)
    finally:
        _IN_PAID_CHAIN.reset(token)

    assert resp.results == []
    rotation.assert_not_awaited()


@pytest.mark.asyncio
async def test_paid_chain_passes_exclude_and_clears_guard() -> None:
    """[FF] The searxng.py caller excludes SearxNGAdapter AND clears the guard
    after the paid chain — no state leaks into later queries."""
    client = SearxNGClient()

    async def fake_rotation(
        query, num_results, categories, language, time_range, engines,
        safesearch, explicit_engines,
    ):
        return None

    async def fake_fanout(query, num_results, language, time_range, safesearch):
        return None

    client._search_with_rotation = fake_rotation  # type: ignore[method-assign]
    client._search_all_replicas = fake_fanout  # type: ignore[method-assign]

    seen: dict = {}

    class _FakeOrch:
        async def search(self, query: str, num_results: int, exclude=None):
            seen["exclude"] = exclude
            return []

        def metrics_snapshot(self) -> dict:
            return {}

    with (
        patch("hyperion.search.orchestrator.get_search_orchestrator", return_value=_FakeOrch()),
        patch("hyperion.tools.searxng.get_valkey_store", return_value=_NoValkey()),
        patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)),
        patch.object(client, "_search_grounded_fallback", new=AsyncMock(return_value=None)),
    ):
        await client.search("india manufacturing", num_results=5)

    assert seen.get("exclude") == {SearxNGAdapter}, (
        "the paid chain must skip the SearxNGAdapter — the caller exhausted it"
    )
    assert _IN_PAID_CHAIN.get() is False, "guard must be cleared after the paid chain"
