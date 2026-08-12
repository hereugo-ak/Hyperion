"""OVERHAUL5 W1 (D-02 / D-03) — web-class quality trigger regression tests.

The 08-12 run proved the paid chain is structurally unreachable: the scholar
fan-out rescues every web query with crossref DOIs (non-zero), so the
"zero results" trigger never fired. W1 replaces the binary trigger with a
web-class quality gate: a general-web query is "proper" only when it returns
>= MIN_WEB_RESULTS WEB-CLASS results.

Fail-first contract: tests marked [FF] fail on the pre-W1 code (which
returned any non-zero rotation response immediately) and pass on the W1 code.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hyperion.tools.searxng import MIN_WEB_RESULTS, SearchResponse, SearchResult, SearxNGClient


class _NoValkey:
    """A valkey stand-in that never returns cached payloads.

    The real store is shared + persistent (TTL 1h) — without this, a previous
    test's satisfied response is served from cache and the web-class gate is
    never exercised.
    """

    async def get_json(self, key: str):
        return None

    async def set_json(self, key: str, payload: dict, ttl: int) -> None:
        return None

    async def delete(self, key: str) -> None:
        return None


def _patch_valkey():
    return patch("hyperion.tools.searxng.get_valkey_store", return_value=_NoValkey())


def _result(url: str, engine: str, *, web_class: bool = True) -> SearchResult:
    return SearchResult(
        title=f"title {url}", url=url, snippet="snippet content " * 4,
        engine=engine, web_class=web_class,
    )


def _doi(engine: str = "crossref") -> SearchResult:
    return _result("https://doi.org/10.1016/j.apenergy.2019.114074", engine, web_class=False)


class _FakeOrchestrator:
    """Stand-in for the paid chain: records calls, returns canned results."""

    def __init__(self, results: list) -> None:
        self.results = results
        self.calls: list[str] = []

    async def search(self, query: str, num_results: int, exclude=None) -> list:
        self.calls.append(query)
        return list(self.results)

    def metrics_snapshot(self) -> dict:
        return {"calls": len(self.calls)}


def _client_with(
    rotation_results: list | None = None,
    fanout_results: list | None = None,
) -> tuple[SearxNGClient, _FakeOrchestrator]:
    client = SearxNGClient()

    async def fake_rotation(
        query, num_results, categories, language, time_range, engines,
        safesearch, explicit_engines,
    ):
        if rotation_results is None:
            return None
        return SearchResponse(
            query=query, results=list(rotation_results),
            engines_used=[r.engine for r in rotation_results],
        )

    async def fake_fanout(query, num_results, language, time_range, safesearch):
        if fanout_results is None:
            return None
        return SearchResponse(
            query=query, results=list(fanout_results),
            engines_used=[r.engine for r in fanout_results],
        )

    client._search_with_rotation = fake_rotation  # type: ignore[method-assign]
    client._search_all_replicas = fake_fanout  # type: ignore[method-assign]
    return client, _FakeOrchestrator([])


@pytest.mark.asyncio
async def test_thin_web_rotation_escalates_to_paid_chain() -> None:
    """[FF] 3 web-class results (< MIN_WEB_RESULTS) must NOT count as proper:
    the pre-W1 code returned them immediately; W1 escalates to the paid chain."""
    client, fake_orch = _client_with(
        rotation_results=[_result("https://a.com/1", "mwmbl") for _ in range(3)]
    )
    with (
        _patch_valkey(),
        patch("hyperion.search.orchestrator.get_search_orchestrator", return_value=fake_orch),
        patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)),
        patch.object(client, "_search_grounded_fallback", new=AsyncMock(return_value=None)),
    ):
        await client.search("india manufacturing competitiveness", num_results=5)
    assert fake_orch.calls, "paid chain must fire when the web class is thin"


@pytest.mark.asyncio
async def test_scholar_rescue_does_not_satisfy_web_trigger() -> None:
    """[FF] 3 crossref DOIs (web_class=False) from rotation + fan-out must
    escalate to the paid chain — scholar metadata is NOT a proper web answer."""
    client, fake_orch = _client_with(
        rotation_results=[_doi() for _ in range(3)],
        fanout_results=[_doi() for _ in range(3)],
    )
    with (
        _patch_valkey(),
        patch("hyperion.search.orchestrator.get_search_orchestrator", return_value=fake_orch),
        patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)),
        patch.object(client, "_search_grounded_fallback", new=AsyncMock(return_value=None)),
    ):
        await client.search("india manufacturing competitiveness", num_results=5)
    assert fake_orch.calls, "paid chain must fire when only scholar DOIs come back"


@pytest.mark.asyncio
async def test_full_web_rotation_returns_without_paid() -> None:
    """5+ web-class results = proper → rotation response returned, paid chain
    never called."""
    client, fake_orch = _client_with(
        rotation_results=[_result(f"https://a{i}.com/1", "mwmbl") for i in range(MIN_WEB_RESULTS)]
    )
    with (
        _patch_valkey(),
        patch("hyperion.search.orchestrator.get_search_orchestrator", return_value=fake_orch),
        patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)),
    ):
        response = await client.search("india manufacturing competitiveness", num_results=5)
    assert not fake_orch.calls, "paid chain must not fire when web class is proper"
    assert len(response.results) == MIN_WEB_RESULTS


@pytest.mark.asyncio
async def test_non_web_query_never_gated() -> None:
    """A scholar-class query is not gated on web-class results — any result
    set is proper."""
    client, fake_orch = _client_with(
        rotation_results=[_doi() for _ in range(3)]
    )
    with (
        _patch_valkey(),
        patch("hyperion.search.orchestrator.get_search_orchestrator", return_value=fake_orch),
        patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)),
    ):
        response = await client.search(
            "capacity utilization manufacturing", categories="scholar", num_results=5
        )
    assert not fake_orch.calls
    assert len(response.results) == 3


@pytest.mark.asyncio
async def test_paid_chain_results_flow_through_retrieval_degraded() -> None:
    """[FF] When SearXNG web class is dead and the paid chain answers, the
    response is marked retrieval_degraded and carries the paid engines."""
    client, fake_orch = _client_with(
        rotation_results=[_doi() for _ in range(2)],
        fanout_results=[_doi() for _ in range(2)],
    )
    from hyperion.search.types import SearchResult as PaidResult

    fake_orch.results = [
        PaidResult(
            title="exa hit", url="https://exa.example.com/p",
            snippet="snippet " * 6, engine="exa", backend="Exa",
        )
        for _ in range(6)
    ]
    with (
        _patch_valkey(),
        patch("hyperion.search.orchestrator.get_search_orchestrator", return_value=fake_orch),
        patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)),
    ):
        response = await client.search("india manufacturing competitiveness", num_results=5)
    assert response.retrieval_degraded
    assert "exa" in response.engines_used
    assert any(e["type"] == "multi_provider_paid_chain" for e in response.degradation_events)
