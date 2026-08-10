"""A1-A4 / B1-B2 (2026-08-10): entity-aware competitor discovery + self-healing.

- A2: discovery queries are shaped by the competitive arena's ENTITY CLASS
  (a country's players, a company's rivals, a technology's vendors).
- A3 Stage A: a STRONG-tier (Mistral) model-knowledge call names candidates
  even when the live web pool returns ZERO results.
- A3 Stage B: search validation binds names to URLs; model-knowledge names
  survive as fallback when search yields no citable rows.
- B1/B2: a PROVIDER_FAILURE sub-agent self-heals once via a STRONG-tier retry
  instead of immediately typing terminal.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from hyperion.agents.base import BaseAgent
from hyperion.agents.specialists.competitive_intel import CompetitiveIntel
from hyperion.config import ModelTier
from hyperion.schemas.agents import AgentName, SubAgentSpec
from hyperion.schemas.models import (
    ConfidenceLevel,
    KeyFinding,
)


def _compete(**context) -> CompetitiveIntel:
    agent = object.__new__(CompetitiveIntel)
    agent._context = context
    agent._question = context.get("_question", "Should India build space startups?")
    agent._competitor_names = []
    agent._llm_competitor_candidates = []
    agent._sources = []
    agent._findings = []
    agent.state = SimpleNamespace(findings_count=0)
    agent.bus = MagicMock()
    agent.bus.publish_finding = AsyncMock()
    agent.spec = SimpleNamespace(name=AgentName.COMPETITIVE_INTEL)
    agent._log = MagicMock()
    return agent


# ── A2 · entity-class-shaped queries ─────────────────────────────────────────


def test_discovery_queries_for_nation_arena() -> None:
    """A country's players, NOT 'competitors of a country'."""
    agent = _compete(geography="India", subject_class="nation_or_region")
    queries = agent._build_discovery_queries("space startups")
    joined = " ".join(queries).lower()
    assert "competitors" not in joined
    assert "india" in joined
    assert any("players" in q for q in queries)


def test_discovery_queries_for_company_arena() -> None:
    agent = _compete(subject_class="company")
    queries = agent._build_discovery_queries("Skyroot")
    joined = " ".join(queries).lower()
    assert "competitors" in joined or "rivals" in joined


def test_discovery_queries_for_technology_arena() -> None:
    agent = _compete(subject_class="technology")
    queries = agent._build_discovery_queries("solid state batteries")
    joined = " ".join(queries).lower()
    assert "vendors" in joined or "providers" in joined


def test_arena_class_prefers_competitive_override() -> None:
    agent = _compete(subject_class="nation_or_region", competitive_arena_class="company")
    assert agent._arena_class() == "company"


# ── A3 Stage A · model-knowledge discovery ───────────────────────────────────


def test_discover_competitors_llm_parses_names() -> None:
    agent = _compete(geography="India", subject_class="nation_or_region")
    payload = {
        "competitors": [
            {"name": "Skyroot Aerospace", "arena_role": "launch"},
            {"name": "Agnikul Cosmos", "arena_role": "launch"},
            {"name": "Pixxel", "arena_role": "EO"},
        ]
    }

    class _Resp:
        success = True
        content = __import__("json").dumps(payload)

    async def _stub(**kwargs):
        assert kwargs["tier"] == ModelTier.STRONG
        return _Resp()

    agent._llm_complete = _stub
    names = asyncio_run(agent._discover_competitors_llm("space startups"))
    assert [n["name"] for n in names] == ["Skyroot Aerospace", "Agnikul Cosmos", "Pixxel"]


def test_discover_competitors_llm_empty_on_failure() -> None:
    agent = _compete(subject_class="company")

    async def _stub(**kwargs):
        return SimpleNamespace(success=False, content="")

    agent._llm_complete = _stub
    assert asyncio_run(agent._discover_competitors_llm("x")) == []


def test_discover_competitors_llm_dedups_and_caps() -> None:
    agent = _compete(subject_class="company")
    payload = {"competitors": [
        {"name": "A", "arena_role": "r"},
        {"name": "a", "arena_role": "r2"},
        {"name": "B", "arena_role": "r"},
        {"name": "C", "arena_role": "r"},
        {"name": "D", "arena_role": "r"},
        {"name": "E", "arena_role": "r"},
        {"name": "F", "arena_role": "r"},
        {"name": "G", "arena_role": "r"},
    ]}

    class _Resp:
        success = True
        content = __import__("json").dumps(payload)

    async def _stub(**kwargs):
        return _Resp()

    agent._llm_complete = _stub
    names = asyncio_run(agent._discover_competitors_llm("x"))
    assert len(names) <= 6
    assert len({n["name"].casefold() for n in names}) == len(names)


# ── A3 Stage B · search validation + model-knowledge fallback ───────────────


def test_identify_competitors_falls_back_to_stage_a_when_search_empty() -> None:
    agent = _compete(geography="India", subject_class="nation_or_region")

    async def _stub_llm(**kwargs):
        return SimpleNamespace(
            success=True,
            content='{"competitors": [{"name": "Skyroot", "arena_role": "launch"}, '
                    '{"name": "Agnikul", "arena_role": "launch"}]}',
        )

    class _FakeSearch:
        async def search(self, pattern, max_results=10):  # noqa: ARG002
            return []  # dead pool

    agent._llm_complete = _stub_llm
    agent.get_tool = lambda name: _FakeSearch()
    results = asyncio_run(agent._identify_competitors("space startups"))
    assert agent._competitor_names == ["Skyroot", "Agnikul"]
    assert results == []


def test_identify_competitors_binds_urls_when_search_succeeds() -> None:
    agent = _compete(geography="India", subject_class="company")

    async def _stub_llm(**kwargs):
        # Judge confirms with a citation to result [0].
        return SimpleNamespace(
            success=True,
            content='{"competitors": [{"name": "Skyroot", "evidence_result_ids": [0], '
                    '"relevance": "launch"}]}',
        )

    class _FakeSearch:
        async def search(self, pattern, max_results=10):  # noqa: ARG002
            return [{
                "title": "Skyroot launches", "url": "https://skyroot.example",
                "content": "Skyroot Aerospace India launch",
            }]

    agent._llm_complete = _stub_llm
    agent.get_tool = lambda name: _FakeSearch()
    results = asyncio_run(agent._identify_competitors("Skyroot"))
    assert agent._competitor_names == ["Skyroot"]
    assert any(r["url"] == "https://skyroot.example" for r in results)
    assert any(s.url == "https://skyroot.example" for s in agent._sources)


# ── B1/B2 · provider self-heal ───────────────────────────────────────────────


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


def _spec(
    tier: ModelTier = ModelTier.STANDARD,
    question: str = "Find ops benchmarks",
) -> SubAgentSpec:
    return SubAgentSpec(
        question=question,
        parent_agent=AgentName.OPERATIONS_ANALYST,
        model_tier=tier,
        tools=[],
        findings_model="KeyFinding",
        timeout_seconds=600,
    )


def test_provider_failure_escalates_to_strong_once() -> None:
    """B2: PROVIDER_FAILURE triggers ONE STRONG-tier retry, then terminal."""
    from hyperion.agents.sub_agent import SubAgentRunner

    parent = _parent()
    parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
    parent._should_respawn_broadened = (
        BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
    )

    # First run: provider failure. Strong retry: returns one substantive finding.
    calls = {"n": 0}

    async def _fake_run(self):
        calls["n"] += 1
        if calls["n"] == 1:
            self.outcome = __import__(
                "hyperion.schemas.models", fromlist=["ResearchOutcome"]
            ).ResearchOutcome.ANALYSIS_FAILED
            self.recovery_hint = "PROVIDER_FAILURE"
            self.counters.provider_failures = 1
            return []
        # Strong-tier retry succeeds.
        self.outcome = __import__(
            "hyperion.schemas.models", fromlist=["ResearchOutcome"]
        ).ResearchOutcome.SUCCESS
        self.recovery_hint = "SUCCESS"
        return [KeyFinding(
            id="healed", agent="ops", finding_type="benchmark", title="t",
            content="cycle time 42h", confidence=ConfidenceLevel.MEDIUM,
        )]

    with patch.object(SubAgentRunner, "run", new=_fake_run):
        findings = asyncio_run(parent._spawn_sub_agent(_spec()))

    assert len(findings) == 1
    assert findings[0].finding_type == "benchmark"
    log_lines = [c.args[0] for c in parent._log.call_args_list]
    assert any("PROVIDER SELF-HEAL" in line for line in log_lines)


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
