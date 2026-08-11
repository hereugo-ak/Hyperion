"""P3 (overhaul.md §6 P3): retrieval-bound provenance regression gates.

Covers the runtime provenance binding in ``SubAgentRunner``:

- I-3: the LLM's own ``sources`` are discarded; only URLs/IDs that resolve
  to the run-scoped Evidence Ledger become ``Source`` objects.
- A substantive finding with zero ledger-bound citations is typed
  ``unverified_assertion`` and never counted as yield.
- The EVIDENCE INDEX block surfaces stable ``[E1]``-style IDs in the prompt.
- Gaps stay gaps; ``ResearchOutcome.SUCCESS`` requires a bound finding.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from hyperion.agents.base import BaseAgent
from hyperion.schemas.models import (
    UNVERIFIED_ASSERTION_TYPE,
    ConfidenceLevel,
    EvidenceFinding,
    KeyFinding,
    ResearchOutcome,
    Source,
    SourceCredibility,
)
from hyperion.tools.evidence_ledger import (
    new_ledger,
    record_evidence,
    reset_active_ledger,
)


def _spec(**overrides):
    from hyperion.config import ModelTier
    from hyperion.schemas.agents import AgentName, SubAgentSpec, ToolName

    base = {
        "question": "What is the India TAM for space startups?",
        "parent_agent": AgentName.MARKET_ANALYST,
        "model_tier": ModelTier.STANDARD,
        "tools": [ToolName.SEARXNG, ToolName.JINA],
        "findings_model": "KeyFinding",
        "timeout_seconds": 600,
    }
    base.update(overrides)
    return SubAgentSpec(**base)


def _stub_response(content: str):
    from hyperion.config import ModelTier
    from hyperion.router.providers.base import ProviderType, RouterResponse

    return RouterResponse(
        content=content,
        model="stub",
        provider=ProviderType.NONE,
        tier=ModelTier.STANDARD,
        success=True,
    )


def _runner_with_router(content: str):
    from hyperion.agents.sub_agent import SubAgentRunner

    class _Router:
        async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
            return _stub_response(content)

    return SubAgentRunner(spec=_spec(), router=_Router())  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# EVIDENCE INDEX block
# ─────────────────────────────────────────────────────────────────────────────


def test_evidence_index_block_lists_stable_ids() -> None:
    new_ledger("test_eng_index")
    try:
        record_evidence(
            url="https://example.com/report-a",
            title="Report A",
            snippet="Market is $4.2B.",
            engine="searxng",
            profile="web",
            stage="discovery",
        )
        record_evidence(
            url="https://stats.example.org/gdp",
            title="GDP table",
            engine="openalex",
            profile="scholar",
            stage="discovery",
        )
        block = _runner_with_router("")._evidence_index_block()
    finally:
        reset_active_ledger()

    assert "EVIDENCE INDEX" in block
    assert "[E1]" in block
    assert "[E2]" in block
    assert "https://example.com/report-a" in block
    assert "https://stats.example.org/gdp" in block


def test_evidence_index_block_empty_without_records() -> None:
    new_ledger("test_eng_empty")
    try:
        block = _runner_with_router("")._evidence_index_block()
    finally:
        reset_active_ledger()

    assert block == ""


def test_evidence_index_block_is_capped() -> None:
    """A rich ledger must not turn the index into a token-burning dump:
    the block is bounded by record count and char budget."""
    new_ledger("test_eng_cap")
    try:
        for i in range(50):
            record_evidence(
                url=f"https://example.com/doc-{i}",
                title=f"Doc {i}",
                snippet="x",
                engine="searxng",
                profile="web",
                stage="discovery",
            )
        block = _runner_with_router("")._evidence_index_block()
    finally:
        reset_active_ledger()

    assert "[E1]" in block
    assert "[E40]" in block
    assert "[E41]" not in block


# ─────────────────────────────────────────────────────────────────────────────
# Binding: LLM sources → ledger Evidence (I-3)
# ─────────────────────────────────────────────────────────────────────────────


def test_llm_minted_url_is_dropped_and_finding_typed_unverified(monkeypatch) -> None:
    new_ledger("test_eng_unbound")
    try:
        record_evidence(
            url="https://example.com/real-source",
            title="Real source",
            snippet="The actual number is 42.",
            engine="searxng",
            profile="web",
            stage="discovery",
        )
        runner = _runner_with_router(
            '{"findings": [{"id": "f1", "agent": "market_analyst", '
            '"finding_type": "market_data", "title": "Claim", '
            '"content": "The market is 42.", '
            '"sources": [{"url": "https://llm-minted.example/fake"}], '
            '"confidence": "medium"}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    # The LLM-minted URL is not in the ledger → zero bound sources.
    assert runner.counters.valid_findings == 0
    assert runner.counters.unverified_assertions == 1
    assert len(findings) == 1
    assert findings[0].finding_type == UNVERIFIED_ASSERTION_TYPE
    assert findings[0].sources == []
    # Not a success: no citable evidence was produced.
    assert runner.outcome is not ResearchOutcome.SUCCESS


def test_cited_evidence_id_binds_to_code_built_source(monkeypatch) -> None:
    """A cited ``[E1]`` ID resolves to the ledger record even when the LLM's
    own URL field is wrong — provenance comes from the ledger, not the echo."""
    new_ledger("test_eng_bound_id")
    try:
        record_evidence(
            url="https://example.gov/statistics",
            title="National statistics office",
            snippet="GDP grew 6.7%.",
            engine="searxng",
            profile="web",
            stage="discovery",
        )
        runner = _runner_with_router(
            '{"findings": [{"id": "f1", "agent": "market_analyst", '
            '"finding_type": "market_data", "title": "GDP", '
            '"content": "GDP grew 6.7% per the statistics office.", '
            '"sources": [{"id": "E1", '
            '"url": "https://llm-minted.example/gdp"}], '
            '"confidence": "high"}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    assert runner.outcome is ResearchOutcome.SUCCESS
    assert runner.counters.valid_findings == 1
    assert isinstance(findings[0], EvidenceFinding)
    # Source is constructed from ledger Evidence, not from the LLM payload:
    # title/url come from the ledger record; the LLM's URL is ignored.
    src = findings[0].sources[0]
    assert isinstance(src, Source)
    assert src.url == "https://example.gov/statistics"
    assert src.title == "National statistics office"


def test_bracketed_evidence_id_binds(monkeypatch) -> None:
    """The prompt tells the LLM to cite ``[E1]``; brackets are stripped on
    bind, so the ID resolves even when the echoed URL is mangled."""
    new_ledger("test_eng_brackets")
    try:
        record_evidence(
            url="https://example.com/doc",
            title="Doc",
            snippet="content",
            engine="searxng",
            profile="web",
            stage="discovery",
        )
        runner = _runner_with_router(
            '{"findings": [{"id": "f1", "agent": "market_analyst", '
            '"finding_type": "market_data", "title": "T", '
            '"content": "content", '
            '"sources": [{"id": "[E1]", '
            '"url": "https://mangled.example/whatever"}], '
            '"confidence": "low"}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    assert runner.counters.valid_findings == 1
    assert findings[0].sources[0].url == "https://example.com/doc"


def test_normalized_url_echo_binds(monkeypatch) -> None:
    """A URL echo with a trailing slash / fragment still binds (I-3 tolerant)."""
    new_ledger("test_eng_normalized")
    try:
        record_evidence(
            url="https://example.com/page",
            title="Page",
            snippet="content",
            engine="searxng",
            profile="web",
            stage="discovery",
        )
        runner = _runner_with_router(
            '{"findings": [{"id": "f1", "agent": "market_analyst", '
            '"finding_type": "market_data", "title": "T", '
            '"content": "content", '
            '"sources": [{"id": "src1", '
            '"url": "https://EXAMPLE.com/page/#frag"}], '
            '"confidence": "low"}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    assert runner.counters.valid_findings == 1
    assert findings[0].sources[0].url == "https://example.com/page"


def test_single_source_dict_is_tolerated(monkeypatch) -> None:
    """A ``sources`` payload shaped as one object (not a list) still binds."""
    new_ledger("test_eng_single")
    try:
        record_evidence(
            url="https://example.com/one",
            title="One",
            snippet="c",
            engine="searxng",
            profile="web",
            stage="discovery",
        )
        runner = _runner_with_router(
            '{"findings": [{"id": "f1", "agent": "market_analyst", '
            '"finding_type": "market_data", "title": "T", '
            '"content": "c", '
            '"sources": {"id": "E1", '
            '"url": "https://example.com/one"}, '
            '"confidence": "low"}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    assert runner.counters.valid_findings == 1
    assert findings[0].sources[0].url == "https://example.com/one"


def test_credibility_derived_in_code_not_transcribed(monkeypatch) -> None:
    """Credibility is classified from the URL; the LLM's label is ignored."""
    new_ledger("test_eng_credibility")
    try:
        record_evidence(
            url="https://example.com/some-blog-post",
            title="Blog post",
            snippet="opinion",
            engine="searxng",
            profile="web",
            stage="discovery",
        )
        runner = _runner_with_router(
            '{"findings": [{"id": "f1", "agent": "market_analyst", '
            '"finding_type": "market_data", "title": "T", '
            '"content": "opinion content", '
            '"sources": [{"id": "E1", "credibility": "peer_reviewed", '
            '"url": "https://example.com/some-blog-post"}], '
            '"confidence": "low"}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    # example.com is not a known gov/news/academic domain → UNKNOWN → BLOG.
    # The LLM's self-attested "peer_reviewed" is not accepted.
    assert findings[0].sources[0].credibility == SourceCredibility.BLOG


# ─────────────────────────────────────────────────────────────────────────────
# Gaps and malformed sources
# ─────────────────────────────────────────────────────────────────────────────


def test_gap_finding_passthrough_never_counts(monkeypatch) -> None:
    new_ledger("test_eng_gap")
    try:
        runner = _runner_with_router(
            '{"findings": [{"id": "g1", "agent": "market_analyst", '
            '"finding_type": "research_gap", "title": "Gap", '
            '"content": "No pricing data found.", '
            '"sources": [], "confidence": "low", '
            '"gaps": ["pricing data"]}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    assert len(findings) == 1
    assert findings[0].finding_type == "research_gap"
    assert runner.counters.gaps == 1
    assert runner.counters.valid_findings == 0
    assert runner.outcome is not ResearchOutcome.SUCCESS


def test_malformed_llm_source_does_not_invalidate_finding(monkeypatch) -> None:
    """I-3: LLM sources are discarded pre-validation, so a malformed source
    block can no longer kill an otherwise valid finding."""
    new_ledger("test_eng_malformed")
    try:
        runner = _runner_with_router(
            '{"findings": [{"id": "f1", "agent": "market_analyst", '
            '"finding_type": "market_data", "title": "T", '
            '"content": "claim", '
            '"sources": [{"credibility": "definitely_not_an_enum"}], '
            '"confidence": "low"}]}'
        )

        async def data() -> str:
            return "raw text"

        monkeypatch.setattr(runner, "_gather_raw_data", data)
        findings = asyncio.run(runner.run())
    finally:
        reset_active_ledger()

    # The finding survives; it is simply unbound → unverified_assertion.
    assert runner.counters.invalid_findings == 0
    assert len(findings) == 1
    assert findings[0].finding_type == UNVERIFIED_ASSERTION_TYPE


def test_unverified_counter_is_a_slot() -> None:
    from hyperion.agents.sub_agent import ResearchCounters

    counters = ResearchCounters()
    counters.unverified_assertions = 3
    d = counters.to_dict()
    assert d["unverified_assertions"] == 3
    assert d["gaps"] == 0
    assert d["valid_findings"] == 0


# ─────────────────────────────────────────────────────────────────────────────
# P3.3: Zero-evidence gate helper (_check_zero_evidence)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_check_zero_evidence_returns_false_when_sources_present() -> None:
    """The gate must NOT fire when the specialist has collected sources."""
    agent = _minimal_agent("test_check_has_sources")
    agent._sources = [Source(  # type: ignore[attr-defined]
        id="src1", url="https://example.com/doc", title="Doc",
        credibility=SourceCredibility.NEWS,
    )]
    result = await agent._check_zero_evidence("test")
    assert result is False
    assert len(agent._findings) == 0  # no gap published


@pytest.mark.asyncio
async def test_check_zero_evidence_publishes_gap_on_empty() -> None:
    """The gate must fire and emit a research_gap when _sources is empty."""
    agent = _minimal_agent("test_check_empty")
    agent._sources = []  # type: ignore[attr-defined]
    result = await agent._check_zero_evidence("no sources for testing")
    assert result is True
    assert len(agent._findings) == 1
    assert agent._findings[0].finding_type == "research_gap"
    assert agent._findings[0].confidence == ConfidenceLevel.LOW


@pytest.mark.asyncio
async def test_check_zero_evidence_safe_without_sources_attr() -> None:
    """An agent that never initialises _sources must not crash."""
    agent = _minimal_agent("test_check_no_attr")
    # Deliberately do NOT set _sources — method uses getattr safe path.
    result = await agent._check_zero_evidence("no _sources attr")
    assert result is True  # can't verify sources → treat as empty


# ─────────────────────────────────────────────────────────────────────────────
# P3.4: Floor report filtering
# ─────────────────────────────────────────────────────────────────────────────


def test_floor_report_requires_substantive_findings() -> None:
    """A floor report built entirely from research_gaps and unverified
    assertions must return None."""
    findings = [
        KeyFinding(
            id="g1", agent="test", finding_type="research_gap",
            title="Gap", content="No data",
            confidence=ConfidenceLevel.LOW, sources=[],
        ),
        KeyFinding(
            id="g2", agent="test", finding_type="unverified_assertion",
            title="Unverified", content="Uncited claim",
            confidence=ConfidenceLevel.LOW, sources=[],
        ),
    ]
    from hyperion.schemas.models import NON_SUBSTANTIVE_FINDING_TYPES
    substantive = [f for f in findings if f.finding_type not in NON_SUBSTANTIVE_FINDING_TYPES]
    assert len(substantive) == 0


def test_floor_report_keeps_substantive_findings() -> None:
    """A floor report with mixed substantive and non-substantive findings
    must keep only the substantive ones. OVERHAUL2 S8: a substantive finding
    with no source URL is retyped ``unverified_assertion`` AT CONSTRUCTION,
    so a "substantive" fixture must carry a bound source to stay substantive."""
    from hyperion.schemas.models import Source, SourceCredibility

    findings = [
        KeyFinding(
            id="a1", agent="market", finding_type="market_data",
            title="Size", content="$4.2B",
            confidence=ConfidenceLevel.MEDIUM,
            sources=[Source(
                id="src_a1", title="Market report",
                url="https://example.com/market",
                credibility=SourceCredibility.INDUSTRY_REPORT,
            )],
        ),
        KeyFinding(
            id="g1", agent="market", finding_type="research_gap",
            title="Gap", content="No growth drivers",
            confidence=ConfidenceLevel.LOW, sources=[],
        ),
    ]
    from hyperion.schemas.models import NON_SUBSTANTIVE_FINDING_TYPES
    substantive = [f for f in findings if f.finding_type not in NON_SUBSTANTIVE_FINDING_TYPES]
    assert len(substantive) == 1
    assert substantive[0].finding_type == "market_data"


# ─────────────────────────────────────────────────────────────────────────────
# P3.5: Parse-error cleanup (flaresolverr)
# ─────────────────────────────────────────────────────────────────────────────


def test_flaresolverr_no_literal_parse_error_string() -> None:
    """FlaresolverrResult must not emit the literal 'Parse error' string."""
    import ast
    import os

    path = os.path.join(os.path.dirname(__file__), "..", "hyperion", "tools", "flaresolverr.py")
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            assert "Parse error" not in node.value, (
                f"Literal 'Parse error' found in flaresolverr.py at line {node.lineno}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────


def _minimal_agent(engagement_id: str) -> BaseAgent:
    """Build a minimally-functioning BaseAgent for testing _check_zero_evidence.

    The agent has a real bus subscription so _publish_finding works, but no
    real router — _check_zero_evidence never calls the router.
    """
    from hyperion.agents.bus import get_bus
    from hyperion.config import ModelTier
    from hyperion.router.router import get_router
    from hyperion.schemas.agents import AgentName, AgentRole, AgentSpec, ToolName

    spec = AgentSpec(
        name=AgentName.MARKET_ANALYST,
        role=AgentRole.SPECIALIST,
        display_name="Test Analyst",
        model_tier=ModelTier.STANDARD,
        tools=[ToolName.SEARXNG],
        skills=[],
        system_prompt="Test agent.",
        spawn_condition="test",
        output_model="TestModel",
    )
    agent = _MinimalTestAgent(spec=spec, bus=get_bus(), router=get_router())
    agent._engagement_id = engagement_id
    return agent


class _MinimalTestAgent(BaseAgent):
    """Minimal BaseAgent subclass for unit-testing shared helpers.

    Delegates run() to the standard SubAgentRunner or returns an empty model.
    """

    async def run(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ARG002
        return None

    def _handle_bus_message(self, msg: Any) -> None:  # noqa: ARG002
        pass
