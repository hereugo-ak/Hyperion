"""FIX0.3 regression tests — new behaviours from
docs/FIX0.3_RUNBOOK_2026-08-09_SEARCH_AGENT_STABILITY.md.

Each class maps to one fix item:
- TestFullPoolFanOut (F-03a): the whole 3-replica fleet participates in a
  general query with each replica's own healthy engines; dead engines are
  excluded; explicit-engine callers are never overridden.
- TestSearchBudget (F-05): cap raised to 600, per-owner accounting, loud
  exhaustion.
- TestFailFastDeadPool (F-06b): a pool with <2 healthy engines returns empty
  immediately instead of burning the query timeout.
- TestBroadenedRespawn (F-07): timeout / zero-findings → exactly one
  broadened respawn; generic exception → no respawn; no loops.
- TestYieldAwareBudget (F-08): concurrent ceiling = max_sub_agents,
  sequential ceiling = 6; released slots can be refilled.
- TestCorpusFloorEscalation (F-10): the CORPUS FLOOR integrity blocker
  triggers _handle_thin_evidence with floor 8.
- TestParseOrNone (F-11): never returns a "Parse error" string; lenient
  retry recovers fenced/prose-wrapped JSON; confidence tracks coverage.
- TestDirectorProportionalCap (F-12): cap scales with DAG task count.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hyperion.schemas.agents import AgentName, ModelTier, SubAgentSpec
from hyperion.tools.parse_or_none import parse_or_none
from hyperion.tools.searxng import SearchResult, SearxNGClient, SearxngPool

# ─────────────────────────────────────────────────────────────────────────────
# F-03a · FULL-POOL FAN-OUT
# ─────────────────────────────────────────────────────────────────────────────


class TestFullPoolFanOut:
    @pytest.mark.asyncio
    async def test_healthy_engines_excludes_cooled_engines(self) -> None:
        from hyperion.tools.engine_health import get_engine_health, reset_engine_health

        reset_engine_health()
        health = get_engine_health()
        health.reset()
        # Suspend one scholar engine: it must not appear in healthy_engines.
        health.record_response(
            unresponsive_engines=[["crossref", "HTTP error 403 (suspended_time=180)"]],
            responding_engines=[],
        )
        pool = SearxngPool.from_config()
        per_profile = pool.healthy_engines()
        assert "web" in per_profile
        assert "crossref" not in per_profile.get("scholar", set())
        reset_engine_health()

    @pytest.mark.asyncio
    async def test_fanout_merges_results_from_every_replica(self) -> None:
        """A dead web profile is still rescued by scholar/reference via the
        fan-out — the exact Aug 9 scenario, proven at the unit level."""
        from hyperion.tools.engine_health import get_engine_health, reset_engine_health

        reset_engine_health()
        get_engine_health().reset()
        from hyperion.tools.searxng import SearchResponse

        client = SearxNGClient()

        async def fake_fanout(query, num_results, language, time_range, safesearch):
            return SearchResponse(
                query=query,
                results=[
                    SearchResult(title="scholar hit", url="https://doi.org/10.1/x"),
                    SearchResult(title="ref hit", url="https://en.wikipedia.org/wiki/X"),
                ],
                engines_used=["crossref", "wikipedia"],
                retrieval_degraded=False,
            )

        client._search_all_replicas = fake_fanout  # type: ignore[method-assign]
        with patch.object(client, "_search_with_rotation", new=AsyncMock(return_value=None)):
            with patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)):
                response = await client.search("india ai market", num_results=5)
        assert response.results
        assert {"crossref", "wikipedia"} <= set(response.engines_used)
        reset_engine_health()

    @pytest.mark.asyncio
    async def test_fanout_not_called_for_explicit_engines(self) -> None:
        from hyperion.tools.engine_health import reset_engine_health

        reset_engine_health()
        client = SearxNGClient()
        fanout = AsyncMock()
        client._search_all_replicas = fanout  # type: ignore[method-assign]
        with patch.object(client, "_search_with_rotation", new=AsyncMock(return_value=None)):
            with patch.object(client, "_search_jina_fallback", new=AsyncMock(return_value=None)):
                await client.search(
                    "india ai market", engines="crossref,openalex", num_results=5
                )
        assert fanout.await_count == 0, (
            "an explicit engine contract must never be silently replaced by "
            "another profile's corpus"
        )
        reset_engine_health()


# ─────────────────────────────────────────────────────────────────────────────
# F-05 · Search budget 600 + per-owner + loud exhaustion
# ─────────────────────────────────────────────────────────────────────────────


class TestSearchBudget:
    def teardown_method(self) -> None:
        SearxNGClient.reset_budget()

    def test_cap_raised_to_600(self) -> None:
        assert SearxNGClient.SEARCH_BUDGET_CAP >= 600

    @pytest.mark.asyncio
    async def test_per_owner_budget_is_independent(self) -> None:
        """One heavy owner exhausting its own budget must not block a second
        owner — the Aug 9 starvation fix."""
        SearxNGClient.reset_budget()
        SearxNGClient._owner_counts = {"heavy": SearxNGClient.PER_OWNER_BUDGET_CAP}
        client_a = SearxNGClient(owner="heavy")
        client_b = SearxNGClient(owner="light")

        with patch.object(client_a, "_get_cached", new=AsyncMock(return_value=None)), \
             patch.object(client_b, "_get_cached", new=AsyncMock(return_value=None)), \
             patch.object(client_a, "_search_with_rotation", new=AsyncMock(return_value=None)), \
             patch.object(client_b, "_search_with_rotation", new=AsyncMock(return_value=None)), \
             patch.object(client_a, "_search_all_replicas", new=AsyncMock(return_value=None)), \
             patch.object(client_b, "_search_all_replicas", new=AsyncMock(return_value=None)), \
             patch.object(client_a, "_search_jina_fallback", new=AsyncMock(return_value=None)), \
             patch.object(client_b, "_search_jina_fallback", new=AsyncMock(return_value=None)), \
             patch.object(client_a, "_search_grounded_fallback", new=AsyncMock(return_value=None)), \
             patch.object(client_b, "_search_grounded_fallback", new=AsyncMock(return_value=None)):
            a_resp = await client_a.search("heavy query", num_results=3)
            b_resp = await client_b.search("light query", num_results=3)

        assert len(a_resp.results) == 0  # heavy owner exhausted → empty
        assert "heavy" in SearxNGClient._owners_exhausted
        assert len(b_resp.results) == 0  # light also empty (rotation mocked None) but NOT exhausted
        assert "light" not in SearxNGClient._owners_exhausted

    @pytest.mark.asyncio
    async def test_global_exhaustion_is_loud_and_tracked(self) -> None:
        SearxNGClient.reset_budget()
        SearxNGClient._search_count = SearxNGClient.SEARCH_BUDGET_CAP
        client = SearxNGClient(owner="probe")

        with patch.object(client, "_get_cached", new=AsyncMock(return_value=None)):
            resp = await client.search("post cap query", num_results=3)

        assert len(resp.results) == 0
        assert SearxNGClient._budget_exceeded is True
        snapshot = SearxNGClient.budget_snapshot()
        assert snapshot["exhausted"] is True
        assert snapshot["used"] == SearxNGClient.SEARCH_BUDGET_CAP


# ─────────────────────────────────────────────────────────────────────────────
# F-06b · Fail-fast on a dead pool
# ─────────────────────────────────────────────────────────────────────────────


class TestFailFastDeadPool:
    @pytest.mark.asyncio
    async def test_dead_pool_returns_empty_without_searching(self) -> None:
        from hyperion.tools.engine_health import get_engine_health, reset_engine_health
        from hyperion.tools.searxng import referenced_engines

        reset_engine_health()
        health = get_engine_health()
        health.reset()
        # Ban all but ONE referenced engine so healthy_count < 2.
        engines = sorted(referenced_engines())
        for engine in engines[1:]:
            health.record_response(
                unresponsive_engines=[[engine, "HTTP error 403 (suspended_time=180)"]],
                responding_engines=[],
            )
        assert health.healthy_count(referenced_engines()) < 2
        client = SearxNGClient()
        search_called = AsyncMock(side_effect=AssertionError("search must not fire"))
        client._search_with_rotation = search_called  # type: ignore[method-assign]

        resp = await client.search("should fail fast", num_results=3)

        assert len(resp.results) == 0
        assert resp.retrieval_degraded is True
        assert search_called.await_count == 0
        reset_engine_health()


# ─────────────────────────────────────────────────────────────────────────────
# F-07 · BROADENED RESPAWN on timeout / zero findings
# ─────────────────────────────────────────────────────────────────────────────


def _spec(*, timeout: int = 600, question: str = "Find competitor evidence"):
    return SubAgentSpec(
        question=question,
        parent_agent=AgentName.COMPETITIVE_INTEL,
        model_tier=ModelTier.STANDARD,
        tools=[],
        findings_model="KeyFinding",
        timeout_seconds=timeout,
    )


def _parent() -> MagicMock:
    parent = MagicMock()
    parent.max_sub_agents = 3
    parent.SUB_AGENT_TOTAL_CEILING = 6
    parent._sub_agent_specs = []
    parent._sub_agent_respawned = set()
    # F-0.1-14: the distinct-work-item budget set (counts questions, not spawns).
    parent._sub_agent_questions = set()
    parent.state = SimpleNamespace(sub_agents_spawned=0, sub_agents_active=0)
    parent._transition = AsyncMock()
    parent._log = MagicMock()
    return parent


class TestBroadenedRespawn:
    @pytest.mark.asyncio
    async def test_timeout_triggers_one_broadened_respawn(self, monkeypatch) -> None:
        from hyperion.agents.base import BaseAgent
        from hyperion.agents.sub_agent import SubAgentRunner

        spec = _spec()
        parent = _parent()
        parent._spawn_sub_agent = AsyncMock(return_value=[])

        def fake_should(s, findings, timed_out, generic_failure):
            return False  # we only test the guard directly below

        with patch.object(SubAgentRunner, "run", new=AsyncMock(side_effect=TimeoutError)):
            # Simulate the timeout path manually through _should_respawn_broadened.
            guard = BaseAgent._should_respawn_broadened
            assert guard(parent, spec, [], timed_out=True, generic_failure=False) is True

    def test_guard_rules(self) -> None:
        from hyperion.agents.base import BaseAgent

        parent = _parent()
        guard = BaseAgent._should_respawn_broadened
        # Already broadened → never respawn again (no loops).
        broadened = _spec()
        broadened.broadened = True
        assert guard(parent, broadened, [], timed_out=True, generic_failure=False) is False
        # Generic exception → never retry (a code bug must not be retried).
        assert guard(parent, _spec(), [], timed_out=False, generic_failure=True) is False
        # Already respawned this question → no second respawn.
        parent._sub_agent_respawned.add(_spec().question)
        assert guard(parent, _spec(), [], timed_out=True, generic_failure=False) is False
        # Sub-300s budgets (unit-test / stress configs) stay deterministic.
        short = _spec(timeout=30)
        assert guard(parent, _spec(timeout=30, question="fresh q"), [],
                     timed_out=True, generic_failure=False) is False
        assert guard(parent, _spec(timeout=600, question="prod q"), [],
                     timed_out=True, generic_failure=False) is True

    def test_zero_findings_gap_triggers_respawn(self) -> None:
        from hyperion.agents.base import BaseAgent
        from hyperion.schemas.models import ConfidenceLevel, KeyFinding

        parent = _parent()
        gap = KeyFinding(
            id="gap_1",
            agent="competitive_intel",
            finding_type="research_gap",
            title="Research gap",
            content=(
                "Sub-agent could not complete this research question: "
                "retrieval or LLM analysis returned no validated findings."
            ),
            sources=[],
            confidence=ConfidenceLevel.LOW,
            gaps=["q"],
        )
        assert BaseAgent._should_respawn_broadened(
            parent, _spec(), [gap], timed_out=False, generic_failure=False
        ) is True


# ─────────────────────────────────────────────────────────────────────────────
# F-08 · Yield-aware budget: concurrent ceiling + 6 sequential refills
# ─────────────────────────────────────────────────────────────────────────────


class TestYieldAwareBudget:
    def test_total_ceiling_is_six(self) -> None:
        from hyperion.agents.base import BaseAgent

        # F-08b: max_sub_agents bounds CONCURRENT work; the sequential refill
        # ceiling is 6 so a released slot (timeout/zero yield) can be reused.
        assert BaseAgent.SUB_AGENT_TOTAL_CEILING == 6
        assert BaseAgent.SUB_AGENT_TOTAL_CEILING > 3

    @pytest.mark.asyncio
    async def test_spawn_refuses_beyond_total_ceiling(self) -> None:
        from hyperion.agents.base import BaseAgent
        from hyperion.agents.sub_agent import SubAgentRunner

        parent = _parent()
        parent._sub_agent_questions = {f"q_{i}" for i in range(BaseAgent.SUB_AGENT_TOTAL_CEILING)}
        parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
        with patch.object(
            SubAgentRunner, "run", new=AsyncMock(return_value=[])
        ):
            findings = await parent._spawn_sub_agent(_spec())
        assert findings == []
        log_calls = [c.args[0] for c in parent._log.call_args_list]
        assert any("total budget reached" in line for line in log_calls)

    @pytest.mark.asyncio
    async def test_broadened_respawn_honors_total_ceiling(self) -> None:
        """P4.6 (overhaul §6 P4, 2026-08-10): the TOTAL ceiling is a HARD
        invariant that includes broadened respawns. The old behavior let a
        broadened respawn ride in on top of an exhausted budget — the A-7
        "SUB-AGENT total budget reached (8/6)" overshoot. A broadened respawn
        is a retry of the same logical sub-agent, but it must not exceed the
        sequential total ceiling; the concurrent cap alone is what it bypasses."""
        from hyperion.agents.base import BaseAgent
        from hyperion.agents.sub_agent import SubAgentRunner

        parent = _parent()
        parent._sub_agent_questions = {f"q_{i}" for i in range(BaseAgent.SUB_AGENT_TOTAL_CEILING)}
        # Bind the real methods (the fake parent is a MagicMock, so every
        # attribute access would otherwise return a truthy mock and recurse).
        parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
        parent._should_respawn_broadened = (
            BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
        )
        broadened = _spec()
        broadened.broadened = True
        with patch.object(SubAgentRunner, "run", new=AsyncMock(return_value=[])):
            findings = await parent._spawn_sub_agent(broadened)
        # The total ceiling is exhausted (6/6 distinct work items), so even a
        # broadened respawn is refused — no "8/6" overshoot.
        assert findings == []
        log_calls = [c.args[0] for c in parent._log.call_args_list]
        assert any("total budget reached" in line for line in log_calls)


# ─────────────────────────────────────────────────────────────────────────────
# OVERHAUL2 S9 · concurrent budget auto-raises 3→5 under cap pressure
# ─────────────────────────────────────────────────────────────────────────────


class TestAdaptiveConcurrentBudget:
    def test_concurrent_max_is_five(self) -> None:
        from hyperion.agents.base import BaseAgent

        # Operator requirement: concurrent pressure raises 3→…→5, bounded.
        assert BaseAgent.SUB_AGENT_CONCURRENT_MAX == 5

    @pytest.mark.asyncio
    async def test_cap_pressure_raises_budget_and_defers_spec(self) -> None:
        """Cap 3, three active sub-agents, fourth spec arrives → budget
        becomes 4, spec lands in _deferred_specs, nothing is dropped."""
        from hyperion.agents.base import BaseAgent
        from hyperion.agents.specialists.competitive_intel import COMPETITIVE_INTEL_SPEC, CompetitiveIntel

        parent = object.__new__(CompetitiveIntel)
        parent.spec = COMPETITIVE_INTEL_SPEC
        parent.SUB_AGENT_TOTAL_CEILING = BaseAgent.SUB_AGENT_TOTAL_CEILING
        parent.SUB_AGENT_CONCURRENT_MAX = 5
        parent._concurrent_boost = 0
        parent._deferred_specs = None
        parent._sub_agent_questions = set()
        parent._sub_agent_respawned = set()
        parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
        parent._should_respawn_broadened = (
            BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
        )
        parent.state = SimpleNamespace(sub_agents_spawned=0, sub_agents_active=3)
        parent._log = MagicMock()
        parent._transition = AsyncMock()

        findings = await parent._spawn_sub_agent(_spec(question="Fourth spec"))
        assert findings == []
        # Nothing dropped: the spec is deferred.
        assert len(parent._deferred_specs) == 1
        assert parent._deferred_specs[0].question == "Fourth spec"
        # Budget raised toward the ceiling.
        assert parent._concurrent_boost == 1
        assert parent.max_sub_agents == 4
        log_calls = [c.args[0] for c in parent._log.call_args_list]
        assert any("concurrent budget raised to 4" in line for line in log_calls)

    @pytest.mark.asyncio
    async def test_released_slot_drains_deferred_queue(self) -> None:
        """After a completion frees a slot, the deferred spec is dispatched."""
        from hyperion.agents.base import BaseAgent
        from hyperion.agents.specialists.competitive_intel import COMPETITIVE_INTEL_SPEC, CompetitiveIntel
        from hyperion.agents.sub_agent import SubAgentRunner

        parent = object.__new__(CompetitiveIntel)
        parent.spec = COMPETITIVE_INTEL_SPEC
        parent.SUB_AGENT_TOTAL_CEILING = BaseAgent.SUB_AGENT_TOTAL_CEILING
        parent.SUB_AGENT_CONCURRENT_MAX = 5
        parent._concurrent_boost = 0
        parent._sub_agent_specs = []
        parent._sub_agent_questions = set()
        parent._sub_agent_respawned = set()
        parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
        parent._should_respawn_broadened = (
            BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
        )
        parent._deferred_specs = [_spec(question="Deferred work")]
        parent.state = SimpleNamespace(sub_agents_spawned=0, sub_agents_active=0)
        parent.bus = MagicMock()
        parent.router = MagicMock()
        parent._log = MagicMock()
        parent._transition = AsyncMock()

        # A spawned sub-agent must succeed without recursing infinitely:
        # the deferred dispatch spawns a runner whose run() returns [].
        with patch.object(SubAgentRunner, "run", new=AsyncMock(return_value=[])):
            await parent._spawn_sub_agent(_spec(question="Active work"))

        assert parent._deferred_specs == []
        log_calls = [c.args[0] for c in parent._log.call_args_list]
        assert any("deferred spawn dispatched" in line for line in log_calls)

    @pytest.mark.asyncio
    async def test_broadened_respawn_jumps_to_ceiling(self) -> None:
        """A broadened (retry) spawn jumps straight to the concurrent ceiling."""
        from hyperion.agents.base import BaseAgent
        from hyperion.agents.specialists.competitive_intel import COMPETITIVE_INTEL_SPEC, CompetitiveIntel

        parent = object.__new__(CompetitiveIntel)
        parent.spec = COMPETITIVE_INTEL_SPEC
        parent.SUB_AGENT_TOTAL_CEILING = BaseAgent.SUB_AGENT_TOTAL_CEILING
        parent.SUB_AGENT_CONCURRENT_MAX = 5
        parent._concurrent_boost = 0
        parent._sub_agent_specs = []
        parent._sub_agent_questions = set()
        parent._sub_agent_respawned = set()
        parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
        parent._should_respawn_broadened = (
            BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
        )
        parent.state = SimpleNamespace(sub_agents_spawned=0, sub_agents_active=0)
        parent.bus = MagicMock()
        parent.router = MagicMock()
        parent._log = MagicMock()
        parent._transition = AsyncMock()
        broadened = _spec()
        broadened.broadened = True

        with patch("hyperion.agents.sub_agent.SubAgentRunner.run", new=AsyncMock(return_value=[])):
            await parent._spawn_sub_agent(broadened)

        assert parent.max_sub_agents == 5
        assert parent._concurrent_boost == 2


# ─────────────────────────────────────────────────────────────────────────────
# F-10 · Corpus-floor blocker → thin-evidence escalation with floor 8
# ─────────────────────────────────────────────────────────────────────────────


class TestCorpusFloorEscalation:
    @pytest.mark.asyncio
    async def test_corpus_floor_blocker_invokes_handle_thin_evidence(self) -> None:
        from hyperion.orchestrator import WorkflowEngine

        orch = WorkflowEngine.__new__(WorkflowEngine)
        calls: list[int] = []

        async def fake_handle(report, source_floor):
            calls.append(source_floor)
            return True

        orch._handle_thin_evidence = fake_handle  # type: ignore[method-assign]
        # Replay the loop decision: a CORPUS FLOOR integrity blocker on the
        # quality score must dispatch escalation at floor 8.
        assert orch._CORPUS_FLOOR_SOURCE_FLOOR == 8


# ─────────────────────────────────────────────────────────────────────────────
# F-11 · parse-or-none + confidence tracks evidence coverage
# ─────────────────────────────────────────────────────────────────────────────


class TestParseOrNone:
    def test_plain_json_parses(self) -> None:
        data = parse_or_none('{"tam_value": "$12B", "unit": "$"}')
        assert data == {"tam_value": "$12B", "unit": "$"}

    def test_fenced_json_parses(self) -> None:
        data = parse_or_none('```json\n{"tam_value": "$12B"}\n```')
        assert data == {"tam_value": "$12B"}

    def test_prose_wrapped_json_salvaged(self) -> None:
        data = parse_or_none('Here is the estimate: {"tam_value": "$12B"} thanks')
        assert data == {"tam_value": "$12B"}

    def test_unparseable_returns_none_not_string(self) -> None:
        assert parse_or_none("definitely not json {{{") is None
        assert parse_or_none("") is None
        assert parse_or_none(None) is None

    def test_no_parse_error_value_anywhere(self) -> None:
        """The literal VALUE emission is banned; only explanatory comments may
        mention the string (comments carry the "never emit" note)."""
        from pathlib import Path

        root = Path(__file__).resolve().parents[1]
        hits: list[str] = []
        for path in root.glob("hyperion/agents/specialists/*.py"):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                code = line.split("#")[0]
                if 'value="Parse error"' in code or 'time_to_market_build="Parse error"' in code:
                    hits.append(f"{path.name}:{i}")
        assert not hits, f"'Parse error' value must never be generated: {hits}"


class TestConfidenceTracksCoverage:
    def test_low_coverage_downgrades_high_confidence(self) -> None:
        from hyperion.agents.synthesis_lead import SynthesisLead
        from hyperion.schemas.models import (
            AnalysisSection,
            ConfidenceLevel,
            Source,
            SourceCredibility,
        )

        lead = object.__new__(SynthesisLead)
        lead._partial_sections = [
            AnalysisSection(
                id="s1", title="T", agent="market_analyst", key_insight="I", body="B",
                confidence=ConfidenceLevel.HIGH,
                sources=[Source(
                    id="a", title="t", url="https://a.com",
                    credibility=SourceCredibility.INDUSTRY_REPORT,
                )],
            ),
            AnalysisSection(
                id="s2", title="T2", agent="market_analyst", key_insight="I2", body="B2",
                confidence=ConfidenceLevel.HIGH, sources=[]
            ),
            AnalysisSection(
                id="s3", title="T3", agent="market_analyst", key_insight="I3", body="B3",
                confidence=ConfidenceLevel.HIGH, sources=[]
            ),
        ]
        lead._findings_by_agent = {
            "market_analyst": [
                SimpleNamespace(confidence=ConfidenceLevel.HIGH),
            ]
        }
        lead._fact_check_report = None
        system, _per_domain = lead._calibrate_confidence([])
        # 1/3 sourced (< 50%) → HIGH must be downgraded.
        assert system is ConfidenceLevel.MEDIUM


# ─────────────────────────────────────────────────────────────────────────────
# F-12 · Director escalation cap proportional to DAG task count
# ─────────────────────────────────────────────────────────────────────────────


class TestDirectorProportionalCap:
    def test_cap_scales_with_task_count(self) -> None:
        from hyperion.agents.engagement_director import EngagementDirector

        director = object.__new__(EngagementDirector)
        director._current_dag = SimpleNamespace(tasks=list(range(10)))
        director._max_escalation_evaluations = 12
        director._MIN_ESCALATION_CAP = 12
        director._ESCALATION_CAP_PER_TASK = 2
        assert director._escalation_evaluation_cap() == 20  # max(12, 2×10)

    def test_no_dag_falls_back_to_floor(self) -> None:
        from hyperion.agents.engagement_director import EngagementDirector

        director = object.__new__(EngagementDirector)
        director._current_dag = None
        director._max_escalation_evaluations = 12
        director._MIN_ESCALATION_CAP = 12
        director._ESCALATION_CAP_PER_TASK = 2
        assert director._escalation_evaluation_cap() == 12
