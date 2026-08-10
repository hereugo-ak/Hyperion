"""P1.4 (overhaul.md §6 P1, 2026-08-10): rescue discovery tier regression gates.

When SearXNG + Jina return ZERO URLs, the sub-agent must NOT reword-and-retry
the same dead source class (anti-pattern 5). Instead it reroutes to the free
scholarly/reference API classes (OpenAlex, Semantic Scholar, HackerNews), which
do not ban datacenter IPs the way web crawlers do, and feeds their candidate
URLs through the SAME extraction ladder so rescued evidence is ledger-bound
like any other.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from hyperion.config import ModelTier
from hyperion.schemas.agents import AgentName, SubAgentSpec, ToolName
from hyperion.tools.engine_health import get_engine_health, reset_engine_health


def _spec(**overrides) -> SubAgentSpec:
    base = {
        "question": "What is the India TAM for space startups?",
        "parent_agent": AgentName.MARKET_ANALYST,
        "model_tier": ModelTier.STANDARD,
        "tools": [
            ToolName.SEARXNG,
            ToolName.JINA,
            ToolName.OPEN_ALEX,
            ToolName.SEMANTIC_SCHOLAR,
            ToolName.HACKERNEWS,
        ],
        "findings_model": "KeyFinding",
        "timeout_seconds": 600,
    }
    base.update(overrides)
    return SubAgentSpec(**base)


def _runner(monkeypatch, *, tool_tools=None):
    from hyperion.agents.sub_agent import SubAgentRunner

    tool_tools = tool_tools or ["open_alex", "semantic_scholar", "hackernews"]

    class _Router:
        async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
            return SimpleNamespace(content="[]", usage=None)

    monkeypatch.setattr(
        "hyperion.agents.sub_agent.SubAgentRunner._has_tool",
        lambda self, name: name in tool_tools,
    )

    class _FakeOA:
        async def search_works(self, query, limit=10):
            return [SimpleNamespace(url="https://openalex.example.org/w1")]

    class _FakeSS:
        async def search(self, query, limit=10, year_range=""):
            return [SimpleNamespace(url="https://paper.example.org/p1")]

    class _FakeHN:
        async def search_stories(self, query, hits=15):
            return [SimpleNamespace(url="https://news.example.org/story1")]

    runner = SubAgentRunner(spec=_spec(), router=_Router())  # type: ignore[arg-type]
    runner._tools = {
        "open_alex": _FakeOA(),
        "semantic_scholar": _FakeSS(),
        "hackernews": _FakeHN(),
    }
    return runner


@pytest.fixture(autouse=True)
def _fresh_health(monkeypatch):
    reset_engine_health()
    tracker = get_engine_health()
    tracker.reset()
    # Suspended the entire web scraper class so the fleet-wide gate in
    # _rescue_discovery passes (no living class → rescue is allowed to run).
    for engine in ("mwmbl", "brave"):
        tracker.record_response(
            unresponsive_engines=[[engine, "HTTP error 403 (suspended_time=180)"]],
            responding_engines=[],
        )
    yield
    reset_engine_health()


@pytest.mark.asyncio
async def test_rescue_discovery_returns_candidate_urls(monkeypatch) -> None:
    runner = _runner(monkeypatch)
    urls = await runner._rescue_discovery()
    assert "https://openalex.example.org/w1" in urls
    assert "https://paper.example.org/p1" in urls
    assert "https://news.example.org/story1" in urls


@pytest.mark.asyncio
async def test_rescue_discovery_skips_when_class_is_living(monkeypatch) -> None:
    """P1.4: the rescue is for a fleet-wide outage, not one slow leg. When the
    web class is healthy the rescue must not run (SearXNG is the preferred path)."""
    reset_engine_health()
    tracker = get_engine_health()
    tracker.reset()  # no engine suspended → web class is healthy
    runner = _runner(monkeypatch)
    urls = await runner._rescue_discovery()
    assert urls == []


@pytest.mark.asyncio
async def test_rescue_discovery_never_raises_on_tool_failure(monkeypatch) -> None:
    from hyperion.agents.sub_agent import SubAgentRunner

    class _Router:
        async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
            return SimpleNamespace(content="[]", usage=None)

    monkeypatch.setattr(
        "hyperion.agents.sub_agent.SubAgentRunner._has_tool",
        lambda self, name: True,
    )

    class _Boom:
        async def search_works(self, query, limit=10):  # noqa: ARG002
            raise RuntimeError("boom")

    class _BoomSS:
        async def search(self, query, limit=10, year_range=""):  # noqa: ARG002
            raise RuntimeError("boom ss")

    class _BoomHN:
        async def search_stories(self, query, hits=15):  # noqa: ARG002
            raise RuntimeError("boom hn")

    runner = SubAgentRunner(spec=_spec(), router=_Router())  # type: ignore[arg-type]
    runner._tools = {
        "open_alex": _Boom(),
        "semantic_scholar": _BoomSS(),
        "hackernews": _BoomHN(),
    }
    urls = await runner._rescue_discovery()
    assert urls == []


@pytest.mark.asyncio
async def test_zero_url_discovery_arms_rescue_in_gather(monkeypatch) -> None:
    """P1.4 integration seam: when both search legs return zero URLs, the
    rescue tier runs and its URLs feed the extraction ladder (deduped)."""
    from hyperion.agents.sub_agent import SubAgentRunner

    runner = _runner(monkeypatch)

    async def _zero_search(self):  # noqa: ANN001
        return ("searxng", [], None)

    async def _zero_jina(self):  # noqa: ANN001
        return ("jina", [], None)

    captured: list[str] = []

    async def _capture_extract(self, urls, query=""):  # noqa: ANN001
        captured.extend(urls)
        return (["<extracted>"], [])

    monkeypatch.setattr(SubAgentRunner, "_search_searxng", _zero_search)
    monkeypatch.setattr(SubAgentRunner, "_search_jina", _zero_jina)
    monkeypatch.setattr(SubAgentRunner, "_extract_urls", _capture_extract)

    new_ledger, _, reset_active_ledger = _ledger_helpers()
    new_ledger("test_eng_rescue")
    try:
        raw = await runner._gather_raw_data()
    finally:
        reset_active_ledger()

    # The rescued scholarly URLs fed the SAME extraction ladder.
    assert "https://openalex.example.org/w1" in captured
    assert "https://paper.example.org/p1" in captured
    assert "https://news.example.org/story1" in captured
    assert "<extracted>" in raw


def _ledger_helpers():
    from hyperion.tools.evidence_ledger import (  # noqa: F401
        new_ledger,
        record_evidence,
        reset_active_ledger,
    )

    return new_ledger, record_evidence, reset_active_ledger
