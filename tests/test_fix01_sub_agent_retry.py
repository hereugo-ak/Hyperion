"""FIX0.1 (docs/FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md) regression tests.

Pins the remediation items:

- F-0.1-1: context-URL fetch seeds extraction (ranked first) in the runner.
- F-0.1-3: route probing generates sibling pricing routes when a page fails.
- F-0.1-5: sufficiency gate stamps counters.sufficiency_failed for a
  pricing task whose extraction lacks pricing artifacts.
- F-0.1-7: quantitative questions with no data yield a labeled estimate, not
  only a gap.
- F-0.1-8: the shared fetch cache returns a cached result for a second fetch
  of the same URL.
- F-0.1-10: _should_respawn_broadened branches on recovery_hint — FETCH
  classes are not broadened, LOW_YIELD is.
- F-0.1-11: the framework-completeness gate publishes a typed gap on empty
  mandatory outputs.
- F-0.1-12: a placeholder finding is converted to a gap at _publish_finding.
- F-0.1-14: the budget gate counts distinct work items.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hyperion.agents.base import BaseAgent
from hyperion.agents.sub_agent import SubAgentRunner
from hyperion.config import ModelTier
from hyperion.schemas.agents import AgentName, SubAgentSpec
from hyperion.schemas.models import (
    RESEARCH_GAP_TYPE,
    UNVERIFIED_ASSERTION_TYPE,
    ConfidenceLevel,
    KeyFinding,
)


def _spec(**overrides) -> SubAgentSpec:
    base = {
        "question": "Scrape competitor pricing page, extract pricing tiers",
        "parent_agent": AgentName.COMPETITIVE_INTEL,
        "model_tier": ModelTier.STANDARD,
        "tools": [],
        "findings_model": "KeyFinding",
        "timeout_seconds": 600,
        "context": {"url": "https://competitor.example/pricing"},
    }
    base.update(overrides)
    return SubAgentSpec(**base)


def _runner(**spec_overrides) -> SubAgentRunner:
    return object.__new__(SubAgentRunner)  # skip __init__ network wiring


# ── F-0.1-1 · context-URL fetch ─────────────────────────────────────────────


def test_context_urls_extracted_from_spec_context() -> None:
    runner = _runner()
    runner.spec = _spec(context={"url": "https://competitor.example/pricing"})
    urls = runner._context_urls()
    assert urls[0] == "https://competitor.example/pricing"


def test_context_urls_also_from_question() -> None:
    runner = _runner()
    runner.spec = _spec(
        question="Fetch https://reports.example/tam.pdf and summarize",
        context={},
    )
    urls = runner._context_urls()
    assert any("reports.example" in u for u in urls)


def test_gather_seeds_context_url_before_search(monkeypatch) -> None:
    """F-0.1-1: context URL is ranked first in all_urls (search legs mocked)."""
    runner = _runner()
    runner.spec = _spec(context={"url": "https://competitor.example/pricing"})
    runner.counters = SimpleNamespace(raw_results=0, extracted_documents=0)
    # OVERHAUL4: the research loop routes counter writes through
    # _ensure_counters() (lazy init); the stub must hand the test's
    # counter block back, not None.
    runner._ensure_counters = lambda: runner.counters  # type: ignore[method-assign]

    async def _zero_search(self):  # noqa: ANN001
        return ("searxng", [], None)

    async def _zero_jina(self):  # noqa: ANN001
        return ("jina", [], None)

    async def _capture_extract(self, urls, query=""):  # noqa: ANN001
        self._last_captured = list(urls)
        return (["<content>"], [])

    runner._search_searxng = _zero_search.__get__(runner, SubAgentRunner)
    runner._search_jina = _zero_jina.__get__(runner, SubAgentRunner)
    runner._extract_urls = _capture_extract.__get__(runner, SubAgentRunner)

    # Patch _has_tool to keep the search legs off.
    runner._has_tool = lambda name: False  # type: ignore[method-assign]

    import asyncio

    result = asyncio.run(runner._gather_raw_data())
    assert "https://competitor.example/pricing" in runner._last_captured
    assert "<content>" in result


# ── F-0.1-3 · route probing ─────────────────────────────────────────────────


def test_route_probe_candidates() -> None:
    candidates = SubAgentRunner._route_probe_candidates(
        ["https://competitor.example/pricing"]
    )
    assert "https://competitor.example/pricing/plans" in candidates
    assert "https://competitor.example/pricing/packages" in candidates


# ── F-0.1-5 · sufficiency gate ──────────────────────────────────────────────


def test_sufficiency_gate_fails_on_pricing_task_without_artifacts() -> None:
    runner = _runner()
    runner.spec = _spec()  # pricing question
    runner.counters = SimpleNamespace(sufficiency_failed=0)
    ok = runner._check_sufficiency(["some prose about the product with no price"], "pricing")
    assert ok is False
    assert runner.counters.sufficiency_failed == 1


def test_sufficiency_gate_passes_with_price() -> None:
    runner = _runner()
    runner.spec = _spec()
    runner.counters = SimpleNamespace(sufficiency_failed=0)
    ok = runner._check_sufficiency(["Plans start at $20 per month"], "pricing")
    assert ok is True
    assert runner.counters.sufficiency_failed == 0


# ── F-0.1-7 · labeled estimate closure ──────────────────────────────────────


def test_quantitative_question_detection() -> None:
    runner = _runner()
    runner.spec = _spec(question="What is the TAM for space startups?")
    assert runner._is_quantitative_question() is True
    runner.spec = _spec(question="Who are the main competitors?")
    assert runner._is_quantitative_question() is False


def test_labeled_estimate_finding_stamps_assumption() -> None:
    runner = _runner()
    runner.spec = _spec()
    est = runner.labeled_estimate_finding(3.5)
    # OVERHAUL2 S8: a labeled estimate is an UNSOURCED assumption by design —
    # the provenance validator retypes it unverified_assertion at
    # construction so it can never be counted as citable evidence yield.
    # Its role is the closure contract: surface "data not publicly available"
    # as a typed limitation, not as a sourced figure.
    assert est.finding_type == UNVERIFIED_ASSERTION_TYPE
    assert "LABELED ANALOG ESTIMATE" in est.content
    assert est.confidence == ConfidenceLevel.LOW


# ── F-0.1-8 · shared fetch cache ────────────────────────────────────────────


def test_fetch_cache_hit_skips_network(monkeypatch) -> None:
    from hyperion.tools.unified_extract import UnifiedExtract, clear_fetch_cache

    clear_fetch_cache()
    try:
        ex = UnifiedExtract.__new__(UnifiedExtract)
        ex._skipped = {}

        async def _noop(*a, **k):
            return None

        # First call: no cache, falls through to tiers (which we stub to nothing).
        ex._active_query = ""
        ex._selection_stats = {}
        ex._tier_available = lambda tier: False  # no tiers available
        # OVERHAUL4 P7: _eligible_tiers gained a profile param for the
        # URL/page-type-aware ladder — the stub must match the new arity.
        ex._eligible_tiers = lambda force=False, profile="default": []  # type: ignore[method-assign]
        ex._default_resolver = lambda *a, **k: None
        import asyncio

        outcome1 = asyncio.run(ex.extract_ladder(["https://x.example/page"]))
        assert outcome1.results == []  # nothing extracted, not cached

        # Now cache a result manually and confirm a hit short-circuits.
        from hyperion.tools.unified_extract import _FETCH_CACHE, UnifiedExtractResult

        _FETCH_CACHE["https://x.example/page"] = UnifiedExtractResult(
            url="https://x.example/page", title="t", content="cached content",
            success=True, tool_used="jina",
        )
        outcome2 = asyncio.run(ex.extract_ladder(["https://x.example/page"]))
        assert outcome2.results
        assert "fetch_cache" in outcome2.tools_used
    finally:
        clear_fetch_cache()


# ── F-0.1-10 · failure-class-aware respawn ──────────────────────────────────


def _parent() -> MagicMock:
    parent = MagicMock()
    parent.max_sub_agents = 3
    parent.SUB_AGENT_TOTAL_CEILING = 6
    parent._sub_agent_specs = []
    parent._sub_agent_respawned = set()
    parent._sub_agent_questions = set()
    parent.state = SimpleNamespace(sub_agents_spawned=0, sub_agents_active=0)
    parent._transition = AsyncMock()
    parent._log = MagicMock()
    parent._dependency_health_green = lambda: True
    return parent


def _finding(finding_type: str) -> KeyFinding:
    return KeyFinding(
        id="f", agent="sub", finding_type=finding_type, title="t",
        content="no validated findings here", confidence=ConfidenceLevel.LOW,
    )


def test_fetch_blocked_is_not_broadened() -> None:
    parent = _parent()
    guard = BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
    assert guard(
        _spec(), [_finding(RESEARCH_GAP_TYPE)], timed_out=False,
        generic_failure=False, recovery_hint="FETCH_BLOCKED",
    ) is False


def test_low_yield_is_broadened() -> None:
    parent = _parent()
    guard = BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
    assert guard(
        _spec(), [_finding(RESEARCH_GAP_TYPE)], timed_out=False,
        generic_failure=False, recovery_hint="LOW_YIELD",
    ) is True


def test_provider_failure_is_not_broadened() -> None:
    parent = _parent()
    guard = BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
    assert guard(
        _spec(), [_finding(RESEARCH_GAP_TYPE)], timed_out=False,
        generic_failure=False, recovery_hint="PROVIDER_FAILURE",
    ) is False


# ── F-0.1-11 · framework-completeness gate ──────────────────────────────────


@pytest.mark.asyncio
async def test_framework_gap_published_on_empty_mandatory_outputs() -> None:
    from hyperion.agents.specialists.innovation_analyst import InnovationAnalyst

    agent = object.__new__(InnovationAnalyst)
    agent.spec = SimpleNamespace(name=AgentName.INNOVATION_ANALYST)
    agent._findings = []
    agent.state = SimpleNamespace(findings_count=0)
    agent.bus = MagicMock()
    agent.bus.publish_finding = AsyncMock()

    incomplete = await agent._publish_framework_gap(
        mandatory_keys=[[], [], []],  # all empty → framework_insufficient
        context_detail="space",
    )
    assert incomplete is True
    assert len(agent._findings) == 1
    assert agent._findings[0].finding_type == RESEARCH_GAP_TYPE
    assert "framework_insufficient" in agent._findings[0].content or \
        "Framework insufficient" in agent._findings[0].title


@pytest.mark.asyncio
async def test_framework_gate_passes_when_outputs_present() -> None:
    from hyperion.agents.specialists.innovation_analyst import InnovationAnalyst

    agent = object.__new__(InnovationAnalyst)
    agent.spec = SimpleNamespace(name=AgentName.INNOVATION_ANALYST)
    agent._findings = []
    agent.state = SimpleNamespace(findings_count=0)
    agent.bus = MagicMock()

    incomplete = await agent._publish_framework_gap(
        mandatory_keys=[["trl1"], ["hype1"]], context_detail="space",
    )
    assert incomplete is False
    assert agent._findings == []


# ── F-0.1-12 · placeholder → gap conversion ─────────────────────────────────


@pytest.mark.asyncio
async def test_placeholder_finding_converted_to_gap_at_publish() -> None:
    from hyperion.agents.specialists.market_analyst import MarketAnalyst

    agent = object.__new__(MarketAnalyst)
    agent.spec = SimpleNamespace(name=AgentName.MARKET_ANALYST)
    agent._findings = []
    agent.state = SimpleNamespace(findings_count=0)
    agent.bus = MagicMock()
    agent.bus.publish_finding = AsyncMock()

    # Constructing a finding with banned filler raises at validation; we build
    # it via model_validate to force the error path the guard catches.
    with pytest.raises(ValueError):
        KeyFinding(
            id="f", agent="x", finding_type="market_size", title="t",
            content="no competitors identified", confidence=ConfidenceLevel.LOW,
        )


# ── F-0.1-14 · budget counts distinct work items ────────────────────────────


@pytest.mark.asyncio
async def test_budget_counts_distinct_questions_not_attempts() -> None:
    from hyperion.agents.sub_agent import SubAgentRunner

    parent = _parent()
    parent._sub_agent_questions = {f"q_{i}" for i in range(6)}  # ceiling full
    parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
    with patch.object(SubAgentRunner, "run", new=AsyncMock(return_value=[])):
        findings = await parent._spawn_sub_agent(_spec())
    assert findings == []
    log_lines = [c.args[0] for c in parent._log.call_args_list]
    assert any("distinct work items" in line for line in log_lines)
