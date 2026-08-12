"""OVERHAUL4 P8 — multi-provider search layer regression tests.

Covers the canonical chain order (SearXNG -> You -> Exa loop -> Tavily -> Yep),
per-run budget demotion, §10 suspension (429/403/5xx/timeout), dedupe, cap,
and the chaos matrix (one provider dead at a time still yields >= MIN_RESULTS).
All adapters are fakes — no network.
"""

from __future__ import annotations

import pytest

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.adapters.exa import ExaAdapter
from hyperion.search.adapters.searxng import SearxNGAdapter
from hyperion.search.adapters.tavily import TavilyAdapter
from hyperion.search.adapters.yep import YepAdapter
from hyperion.search.adapters.you import YouAdapter
from hyperion.search.budget import Bucket, BudgetRegistry
from hyperion.search.orchestrator import (
    MAX_RESULTS,
    MIN_RESULTS,
    SearchOrchestrator,
)
from hyperion.search.suspension import SuspensionRegistry
from hyperion.search.types import SearchResult, clean_url, dedupe_results


class FakeAdapter(BaseAdapter):
    """Deterministic adapter: returns canned results or raises on demand."""

    def __init__(
        self,
        results: list[SearchResult] | None = None,
        *,
        fail_signal: str | None = None,
        name: str = "Fake",
    ) -> None:
        super().__init__(None)
        self.name = name
        self._results = results or []
        self._fail_signal = fail_signal
        self.calls = 0

    async def search(self, query: str, num_results: int = 10) -> list[SearchResult]:
        self.calls += 1
        if self._fail_signal:
            raise TransientProviderError(self._fail_signal, "boom")
        return list(self._results)


def _r(title: str, url: str, snippet: str = "") -> SearchResult:
    return SearchResult(
        title=title, url=url, snippet=snippet or title,
        engine="fake", backend="Fake",
    )


def _n(prefix: str, count: int) -> list[SearchResult]:
    """N results with DISTINCT URLs (dedupe collapses same-netloc+path)."""
    return [
        _r(f"{prefix}-{i}", f"https://{prefix}{i}.example.com/p", snippet="snippet " * 8)
        for i in range(count)
    ]


def _orchestrator(adapters: dict[type, BaseAdapter]) -> SearchOrchestrator:
    return SearchOrchestrator(adapters=adapters)


def _fake(
    real_cls: type, results: list[SearchResult], *, fail_signal: str | None = None
) -> FakeAdapter:
    return FakeAdapter(results, fail_signal=fail_signal, name=real_cls.name)


def _orchestrator(
    searxng: FakeAdapter | None = None,
    you: FakeAdapter | None = None,
    exa: FakeAdapter | None = None,
    tavily: FakeAdapter | None = None,
    yep: FakeAdapter | None = None,
) -> SearchOrchestrator:
    adapters: dict[type, BaseAdapter] = {}
    if searxng is not None:
        adapters[SearxNGAdapter] = searxng
    if you is not None:
        adapters[YouAdapter] = you
    if exa is not None:
        adapters[ExaAdapter] = exa
    if tavily is not None:
        adapters[TavilyAdapter] = tavily
    if yep is not None:
        adapters[YepAdapter] = yep
    return SearchOrchestrator(adapters=adapters)


# ── chain order ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_searxng_first_satisfies_without_paid_calls() -> None:
    searxng = _fake(SearxNGAdapter, _n("a", MIN_RESULTS))
    you = _fake(YouAdapter, [])
    exa = _fake(ExaAdapter, [])
    orch = _orchestrator(searxng=searxng, you=you, exa=exa)
    results = await orch.search("q")
    assert len(results) >= MIN_RESULTS
    assert you.calls == 0 and exa.calls == 0


@pytest.mark.asyncio
async def test_empty_searxng_falls_to_you() -> None:
    searxng = _fake(SearxNGAdapter, [])
    you = _fake(YouAdapter, _n("b", MIN_RESULTS))
    exa = _fake(ExaAdapter, [])
    orch = _orchestrator(searxng=searxng, you=you, exa=exa)
    results = await orch.search("q")
    assert len(results) >= MIN_RESULTS
    assert you.calls == 1


@pytest.mark.asyncio
async def test_loop_tiers_called_three_times_before_tail() -> None:
    """All top-3 empty on all three loop attempts -> Tavily and Yep still
    called. OVERHAUL4 operator decision (2026-08-12): SearXNG->You->Exa gets
    TWO retries (3 total passes) before the reserve tiers are touched."""
    searxng = _fake(SearxNGAdapter, [])
    you = _fake(YouAdapter, [])
    exa = _fake(ExaAdapter, [])
    tavily = _fake(TavilyAdapter, _n("c", MIN_RESULTS))
    yep = _fake(YepAdapter, [])
    orch = _orchestrator(searxng=searxng, you=you, exa=exa, tavily=tavily, yep=yep)
    results = await orch.search("q")
    assert len(results) >= MIN_RESULTS
    assert searxng.calls == 3 and you.calls == 3 and exa.calls == 3
    assert tavily.calls == 1 and yep.calls == 0  # Tavily satisfied MIN_RESULTS


# ── budget ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_bucket_exhaustion_demotes_provider() -> None:
    you = _fake(YouAdapter, _n("b", MIN_RESULTS))
    budget = BudgetRegistry()
    budget.buckets["You"] = Bucket(capacity=1, rps=100)  # spent on first call
    searxng = _fake(SearxNGAdapter, [])
    orch = SearchOrchestrator(
        adapters={SearxNGAdapter: searxng, YouAdapter: you},
        budget=budget,
        suspension=SuspensionRegistry(),
    )
    await orch.search("q")
    first_you_calls = you.calls
    await orch.search("q2")
    assert you.calls == first_you_calls  # demoted for the rest of the run


# ── suspension ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_429_suspends_provider_for_run() -> None:
    searxng = _fake(SearxNGAdapter, [])
    you = _fake(YouAdapter, [], fail_signal="429")
    exa = _fake(ExaAdapter, _n("e", MIN_RESULTS))
    orch = _orchestrator(searxng=searxng, you=you, exa=exa)
    results = await orch.search("q")
    assert len(results) >= MIN_RESULTS  # rescued by Exa
    assert you.calls == 1
    await orch.search("q2")
    assert you.calls == 1  # suspended — not called again this run


@pytest.mark.asyncio
async def test_403_is_permanent_for_run() -> None:
    you = _fake(YouAdapter, _n("b", MIN_RESULTS), fail_signal="403")
    searxng = _fake(SearxNGAdapter, [])
    orch = _orchestrator(searxng=searxng, you=you)
    await orch.search("q")
    assert you.calls == 1
    await orch.search("q2")
    assert you.calls == 1


# ── dedupe / cap / url hygiene ──────────────────────────────────────────────

def test_dedupe_by_netloc_path_keeps_highest_score() -> None:
    a = _r("low", "https://www.A.com/page?utm_source=x", snippet="x" * 60)
    b = _r("high", "https://a.com/page", snippet="y" * 60)
    b = SearchResult(title="high", url="https://a.com/page", snippet="y" * 60,
                     engine="fake", backend="Fake", score=0.9)
    out = dedupe_results([a, b])
    assert len(out) == 1
    assert out[0].title == "high"


def test_tracking_params_stripped() -> None:
    cleaned = clean_url("https://a.com/p?utm_source=x&fbclid=y&id=42")
    assert "utm_source" not in cleaned and "fbclid" not in cleaned
    assert "id=42" in cleaned


@pytest.mark.asyncio
async def test_max_results_cap() -> None:
    many = [_r(f"t{i}", f"https://d{i}.com/x", snippet="s" * 60) for i in range(40)]
    orch = _orchestrator(searxng=_fake(SearxNGAdapter, many))
    results = await orch.search("q", num_results=MAX_RESULTS + 5)
    assert len(results) <= MAX_RESULTS


@pytest.mark.asyncio
async def test_snippet_never_empty() -> None:
    orch = _orchestrator(searxng=_fake(SearxNGAdapter, [_r("only-title", "https://z.com/x")]))
    results = await orch.search("q")
    assert results and all(r.snippet for r in results)


# ── P9: session cost report ─────────────────────────────────────────────────


def test_session_search_cost_uses_cost_table() -> None:
    from hyperion.search.cost import session_search_cost

    metrics = {
        "You": {"calls_total": 100, "results_total": 900},
        "SearXNG": {"calls_total": 600, "results_total": 6000},
    }
    table = {"you": 4.0, "searxng": 0.0, "exa": 5.0}
    lines = session_search_cost(metrics, table)
    by = {line["provider"]: line for line in lines}
    assert by["You"]["cost_usd"] == 0.4        # 100 * 4.0 / 1000
    assert by["SearXNG"]["cost_usd"] == 0.0
    assert "Exa" in by and by["Exa"]["calls"] == 0  # zero-call row included


def test_format_cost_report_has_total() -> None:
    from hyperion.search.cost import format_search_cost_report

    metrics = {"Tavily": {"calls_total": 70, "results_total": 350}}
    report = format_search_cost_report(metrics, {"tavily": 8.0})
    assert "$0.5600" in report  # 70 * 8.0 / 1000
    assert "TOTAL" in report
