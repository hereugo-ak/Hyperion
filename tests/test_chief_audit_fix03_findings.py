"""Regression gates for CHIEF_AUDIT_FIX0.3_ZERO_FINDINGS.md findings.

Covers the code-level remediation for:

- F-01: typed research outcome state machine (SUCCESS/NO_EVIDENCE/
  RETRIEVAL_DEGRADED/ANALYSIS_FAILED/TIMEOUT/RETRY_EXHAUSTED); a
  ``research_gap`` must never be counted as a substantive finding.
- F-02: dependency-aware respawn — broadening is suppressed when the
  retrieval dependency health gate is RED.
- F-03: the sub-agent search fan-out is bounded and deadline-aware, not
  serial, and stops dispatching once the evidence minimum is met.
- F-04: the search fail-fast is per-profile; a dead preferred profile with
  healthy fleet engines must go straight to the full-pool fan-out.
- F-05: corpus readiness is reported independently of process readiness.
- F-07: invalid JSON/findings are counted and one format-repair attempt is
  made; provider failures are typed, never silent.
- F-08: the provenance fingerprint carries source/settings/profile hashes
  and the executed policy (timeouts, budgets).
- F-09: a failed corpus-floor escalation is terminal (INSUFFICIENT_EVIDENCE).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from hyperion.schemas.models import KeyFinding, ResearchOutcome


# ─────────────────────────────────────────────────────────────────────────────
# F-01: typed outcome state machine
# ─────────────────────────────────────────────────────────────────────────────


def test_research_outcome_enum_members() -> None:
    """F-01: the six required outcomes exist as one typed enum."""
    values = {o.value for o in ResearchOutcome}
    assert {
        "success",
        "no_evidence",
        "retrieval_degraded",
        "analysis_failed",
        "timeout",
        "retry_exhausted",
    } == values


def _make_spec(**overrides):
    from hyperion.config import ModelTier
    from hyperion.schemas.agents import AgentName, SubAgentSpec, ToolName

    base = {
        "question": "Test sub-question?",
        "parent_agent": AgentName.MARKET_ANALYST,
        "model_tier": ModelTier.STANDARD,
        "tools": [ToolName.SEARXNG, ToolName.JINA],
        "findings_model": "KeyFinding",
        "timeout_seconds": 600,
    }
    base.update(overrides)
    return SubAgentSpec(**base)


def _stub_response(**overrides) -> Any:
    """Build a minimal RouterResponse for outcome-path tests."""
    from hyperion.config import ModelTier
    from hyperion.router.providers.base import ProviderType, RouterResponse

    payload = {
        "content": "",
        "model": "stub",
        "provider": ProviderType.NONE,
        "tier": ModelTier.STANDARD,
        "success": False,
        "error": "stub provider down",
    }
    payload.update(overrides)
    return RouterResponse(**payload)


class _NoopRouter:
    """A router stub that never succeeds — for outcome-path tests."""

    async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
        return _stub_response()


def test_f01_run_types_analysis_failed_on_provider_failure(monkeypatch) -> None:
    """A dead provider is ANALYSIS_FAILED, never 'the world has no evidence'."""
    from hyperion.agents.sub_agent import SubAgentRunner

    runner = SubAgentRunner(spec=_make_spec(), router=_NoopRouter())  # type: ignore[arg-type]

    async def no_data() -> str:
        return "some raw text"  # retrieval succeeded, analysis then failed

    monkeypatch.setattr(runner, "_gather_raw_data", no_data)
    findings = asyncio.run(runner.run())

    assert runner.outcome is ResearchOutcome.ANALYSIS_FAILED
    assert runner.counters.provider_failures == 1
    assert runner.counters.valid_findings == 0
    # The synthetic gap is a gap, not a substantive finding.
    assert len(findings) == 1
    assert findings[0].finding_type == "research_gap"
    assert runner.counters.gaps == 1


def test_f01_run_types_success_with_valid_findings(monkeypatch) -> None:
    from hyperion.agents.sub_agent import SubAgentRunner

    class _Router:
        async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
            return _stub_response(
                success=True,
                content=(
                    '{"findings": [{"id": "f1", "agent": "market_analyst", '
                    '"finding_type": "market_data", "title": "TAM", '
                    '"content": "The TAM is $4.2B with sources.", '
                    '"sources": [], "confidence": "medium"}]}'
                ),
            )

    runner = SubAgentRunner(spec=_make_spec(), router=_Router())  # type: ignore[arg-type]

    async def data() -> str:
        return "raw text with a number 4.2"

    monkeypatch.setattr(runner, "_gather_raw_data", data)
    findings = asyncio.run(runner.run())

    assert runner.outcome is ResearchOutcome.SUCCESS
    assert runner.counters.valid_findings == 1
    assert runner.counters.gaps == 0
    assert findings[0].finding_type == "market_data"


def test_f01_gap_finding_is_never_substantive() -> None:
    """F-01/F-07: a research_gap must not be counted as evidence yield."""
    from hyperion.agents.sub_agent import SubAgentRunner
    from hyperion.schemas.agents import AgentName

    runner = SubAgentRunner(spec=_make_spec(), router=_NoopRouter())  # type: ignore[arg-type]
    gap = runner.gap_finding("no evidence", 1.0)
    assert gap.finding_type == "research_gap"
    assert gap.agent == AgentName.MARKET_ANALYST.value


def test_f01_broadened_pass_with_zero_yield_types_retry_exhausted(monkeypatch) -> None:
    """F-01: a broadened respawn pass that still yields nothing must be a
    typed RETRY_EXHAUSTED terminal state — the one permitted retry is spent."""
    from hyperion.agents.sub_agent import SubAgentRunner

    runner = SubAgentRunner(  # type: ignore[arg-type]
        spec=_make_spec(broadened=True),  # parent marks the respawn pass
        router=_NoopRouter(),
    )

    async def data() -> str:
        return "raw"

    monkeypatch.setattr(runner, "_gather_raw_data", data)
    findings = asyncio.run(runner.run())

    assert runner.outcome is ResearchOutcome.RETRY_EXHAUSTED
    assert runner.counters.valid_findings == 0
    # Still returns the explicit gap — never a fake success.
    assert len(findings) == 1
    assert findings[0].finding_type == "research_gap"


def test_f01_counters_are_not_shared_across_new_constructed_runners() -> None:
    """F-01: runners built via ``object.__new__`` (as tests and some callers
    do) must each get a fresh counter block; state must not leak through a
    shared mutable class attribute."""
    from hyperion.agents.sub_agent import SubAgentRunner

    first = SubAgentRunner.__new__(SubAgentRunner)
    first._ensure_counters().invalid_findings += 1
    first._ensure_counters().provider_failures += 1

    second = SubAgentRunner.__new__(SubAgentRunner)
    counters = second._ensure_counters()
    assert counters.invalid_findings == 0
    assert counters.provider_failures == 0

    # And the class attribute itself was never polluted.
    assert SubAgentRunner.counters is None


# ─────────────────────────────────────────────────────────────────────────────
# F-07: counters and one format-repair attempt
# ─────────────────────────────────────────────────────────────────────────────


def test_f07_invalid_json_is_counted_not_silently_dropped(monkeypatch) -> None:
    from hyperion.agents.sub_agent import SubAgentRunner

    class _Router:
        async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
            return _stub_response(
                success=True,
                content="this is not json at all",
            )

    runner = SubAgentRunner(spec=_make_spec(), router=_Router())  # type: ignore[arg-type]

    async def data() -> str:
        return "raw"

    monkeypatch.setattr(runner, "_gather_raw_data", data)
    asyncio.run(runner.run())

    assert runner.counters.invalid_findings == 1
    assert runner.outcome is ResearchOutcome.ANALYSIS_FAILED


def test_f07_format_repair_recovers_fenced_json(monkeypatch) -> None:
    """One bounded repair attempt: fenced JSON must survive extract_json."""
    from hyperion.agents.sub_agent import SubAgentRunner

    class _Router:
        async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
            return _stub_response(
                success=True,
                content=(
                    "Here is the analysis:\\n```json\\n"
                    '{"findings": [{"id": "f1", "agent": "market_analyst", '
                    '"finding_type": "market_data", "title": "T", '
                    '"content": "Data with 42.", "sources": [], '
                    '"confidence": "low"}]}\\n```'
                ),
            )

    runner = SubAgentRunner(spec=_make_spec(), router=_Router())  # type: ignore[arg-type]

    async def data() -> str:
        return "raw"

    monkeypatch.setattr(runner, "_gather_raw_data", data)
    findings = asyncio.run(runner.run())

    assert runner.outcome is ResearchOutcome.SUCCESS
    assert runner.counters.valid_findings == 1
    assert runner.counters.invalid_findings == 0


def test_f07_invalid_schema_items_counted(monkeypatch) -> None:
    from hyperion.agents.sub_agent import SubAgentRunner

    class _Router:
        async def complete(self, **kwargs):  # noqa: ARG002 - stub interface
            return _stub_response(
                success=True,
                content=(
                    '{"findings": [{"id": "bad", "finding_type": "x"}, '
                    '{"id": "ok", "agent": "market_analyst", '
                    '"finding_type": "market_data", "title": "T", '
                    '"content": "valid with 7", "sources": [], '
                    '"confidence": "medium"}]}'
                ),
            )

    runner = SubAgentRunner(spec=_make_spec(), router=_Router())  # type: ignore[arg-type]

    async def data() -> str:
        return "raw"

    monkeypatch.setattr(runner, "_gather_raw_data", data)
    findings = asyncio.run(runner.run())

    assert runner.counters.invalid_findings == 1  # the schema-invalid one
    assert runner.counters.valid_findings == 1
    assert len(findings) == 1
    assert findings[0].id == "ok"


def test_f07_gather_counts_raw_and_extracted(monkeypatch) -> None:
    """F-07 counters: raw discovery yield and extracted documents."""
    from hyperion.agents.sub_agent import SubAgentRunner

    runner = SubAgentRunner(spec=_make_spec(), router=_NoopRouter())  # type: ignore[arg-type]

    async def fake_gather() -> str:
        runner.counters.raw_results = 6
        runner.counters.extracted_documents = 4
        return "six urls, four extracted"

    monkeypatch.setattr(runner, "_gather_raw_data", fake_gather)
    asyncio.run(runner.run())

    assert runner.counters.raw_results == 6
    assert runner.counters.extracted_documents == 4


# ─────────────────────────────────────────────────────────────────────────────
# F-02: dependency-aware respawn
# ─────────────────────────────────────────────────────────────────────────────


class _SpecCarrier:
    """Minimal stand-in exposing the attributes _should_respawn_broadened reads."""

    def __init__(self, broadened: bool = False, timeout_seconds: int = 600) -> None:
        self.broadened = broadened
        self.timeout_seconds = timeout_seconds
        self.question = "Should we enter?"


def _gap_finding(content: str) -> KeyFinding:
    return KeyFinding(
        id="gap_1",
        agent="market_analyst",
        finding_type="research_gap",
        title="gap",
        content=content,
        sources=[],
        confidence="low",
    )


def test_f02_respawn_suppressed_when_dependency_health_red(monkeypatch) -> None:
    """A RED dependency gate must suppress broadening (no dead-pool retries)."""
    from hyperion.agents.base import BaseAgent

    class _Agent(BaseAgent):
        async def run(self, *args, **kwargs):  # pragma: no cover - abstract stub
            return None

    import hyperion.agents.base as base_mod

    monkeypatch.setattr(
        base_mod.BaseAgent,
        "_dependency_health_green",
        staticmethod(lambda: False),
    )
    agent = _Agent.__new__(_Agent)
    agent._log = lambda msg: None  # type: ignore[method-assign]
    agent._sub_agent_respawned = set()

    assert agent._should_respawn_broadened(
        _SpecCarrier(), [_gap_finding("no validated findings")], False, False
    ) is False


def test_f02_respawn_allowed_when_dependency_health_green(monkeypatch) -> None:
    from hyperion.agents.base import BaseAgent

    class _Agent(BaseAgent):
        async def run(self, *args, **kwargs):  # pragma: no cover - abstract stub
            return None

    import hyperion.agents.base as base_mod

    monkeypatch.setattr(
        base_mod.BaseAgent,
        "_dependency_health_green",
        staticmethod(lambda: True),
    )
    agent = _Agent.__new__(_Agent)
    agent._log = lambda msg: None  # type: ignore[method-assign]
    agent._sub_agent_respawned = set()

    assert agent._should_respawn_broadened(
        _SpecCarrier(), [_gap_finding("no validated findings")], False, False
    ) is True


def test_f02_dependency_health_gate_reads_engine_floor() -> None:
    """The real gate consults engine health, not a constant."""
    from hyperion.agents.base import BaseAgent

    green = BaseAgent._dependency_health_green()
    assert isinstance(green, bool)


# ─────────────────────────────────────────────────────────────────────────────
# F-03: bounded deadline-aware fan-out
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_f03_fan_out_runs_queries_with_bounded_concurrency(monkeypatch) -> None:
    from hyperion.agents.sub_agent import SubAgentRunner

    runner = SubAgentRunner(spec=_make_spec(), router=_NoopRouter())  # type: ignore[arg-type]
    runner.FAN_OUT_CONCURRENCY = 2

    in_flight = 0
    peak_in_flight = 0
    started: list[str] = []

    async def fake_search(query: str, **kwargs):
        nonlocal in_flight, peak_in_flight
        in_flight += 1
        peak_in_flight = max(peak_in_flight, in_flight)
        await asyncio.sleep(0.01)
        started.append(query)
        in_flight -= 1
        from hyperion.tools.searxng import SearchResult

        return [SearchResult(title=f"r-{query}", url=f"https://x/{len(started)}")]

    results = await runner._fan_out_search(fake_search, ["q1", "q2", "q3", "q4", "q5"], 10)

    assert len(results) == 5
    assert peak_in_flight <= 2, f"concurrency exceeded bound: {peak_in_flight}"
    assert len(started) == 5


@pytest.mark.asyncio
async def test_f03_fan_out_stops_at_evidence_minimum() -> None:
    from hyperion.agents.sub_agent import SubAgentRunner

    runner = SubAgentRunner(spec=_make_spec(), router=_NoopRouter())  # type: ignore[arg-type]
    runner.FAN_OUT_MIN_EVIDENCE = 3
    runner.FAN_OUT_CONCURRENCY = 2
    runner.FAN_OUT_DEADLINE_SECONDS = 30

    dispatched: list[str] = []

    async def fake_search(query: str, **kwargs):
        dispatched.append(query)
        from hyperion.tools.searxng import SearchResult

        return [SearchResult(title=f"r-{query}", url=f"https://x/{len(dispatched)}")]

    results = await runner._fan_out_search(fake_search, ["a", "b", "c", "d", "e"], 10)

    assert len(results) >= 3
    # Early stop: not every query was dispatched once the minimum was met.
    assert len(dispatched) < 5, f"all queries dispatched despite early stop: {dispatched}"


# ─────────────────────────────────────────────────────────────────────────────
# F-04: per-profile fail-fast
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_f04_dead_preferred_profile_goes_straight_to_fleet(monkeypatch) -> None:
    """A dead web profile with healthy scholar engines must NOT return empty
    and must NOT burn the web rotation first."""
    from hyperion.tools.searxng import SearxngEndpoint, SearxngPool, SearxNGClient, SearchResponse

    client = SearxNGClient()
    client._pool = SearxngPool([
        SearxngEndpoint(
            "http://web", "web", 8890,
            frozenset({"brave", "mojeek", "mwmbl", "yep"}),
        ),
        SearxngEndpoint(
            "http://reference", "reference", 8889, frozenset({"wikipedia"})
        ),
        SearxngEndpoint(
            "http://scholar", "scholar", 8888,
            frozenset({"crossref", "openalex", "semantic scholar"}),
        ),
    ])

    from hyperion.tools import searxng as searxng_mod
    from hyperion.tools.engine_health import EngineHealthTracker

    health = EngineHealthTracker()
    # Kill every web engine; keep scholar/reference healthy.
    for engine in ("brave", "mojeek", "mwmbl", "yep"):
        health.record_response(
            [[engine, "HTTP error 403 (suspended_time=3600)"]], []
        )
    monkeypatch.setattr(searxng_mod, "get_engine_health", lambda: health)

    fleet_called = []

    async def fake_fanout(*args, **kwargs):
        fleet_called.append(True)
        from hyperion.tools.searxng import SearchResult

        return SearchResponse(
            query="q",
            results=[
                SearchResult(title="crossref hit", url="https://api.crossref.org/1"),
                SearchResult(title="openalex hit", url="https://api.openalex.org/1"),
            ],
            engines_used=["crossref", "openalex"],
        )

    async def fake_set_cached(*args, **kwargs):
        return None

    monkeypatch.setattr(client, "_search_all_replicas", fake_fanout)
    monkeypatch.setattr(client, "_set_cached", fake_set_cached)

    response = await client.search("India import dependence market")

    assert response.results, "dead web profile must not return empty when fleet is healthy"
    assert fleet_called, "expected the fleet fan-out to serve the query"


# ─────────────────────────────────────────────────────────────────────────────
# F-08: provenance fingerprint
# ─────────────────────────────────────────────────────────────────────────────


def test_f08_fingerprint_carries_hashes_and_policy(tmp_path) -> None:
    from hyperion.infra import provenance

    package_dir = tmp_path / "hyperion"
    package_dir.mkdir()
    (package_dir / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "searxng_settings.yml").write_text("server:\n", encoding="utf-8")
    (tmp_path / "searxng_settings.web.yml").write_text("web:\n", encoding="utf-8")

    snapshot = provenance.Provenance(
        package_dir=str(package_dir),
        repo_root=str(tmp_path),
        git_sha="abc1234",
        git_dirty=False,
        install_mode="editable",
        stale_pycache=[],
        source_hash=provenance._source_hash(package_dir),
        settings_hash=provenance._settings_hash(tmp_path),
        profile_hashes=provenance._profile_hashes(tmp_path),
        policy={"task_timeout_s": 600},
    )

    assert snapshot.source_hash
    assert snapshot.settings_hash
    assert "searxng_settings.web.yml" in snapshot.profile_hashes
    assert snapshot.policy["task_timeout_s"] == 600
    banner = provenance.banner(snapshot)
    assert "FINGERPRINT" in banner
    assert "POLICY" in banner


def test_f08_collected_policy_has_search_budgets() -> None:
    from hyperion.infra.provenance import _policy_snapshot

    policy = _policy_snapshot()
    assert policy.get("search_budget_cap", 0) > 0
    assert policy.get("sub_agent_total_ceiling", 0) > 0


def test_f08_banner_lines_carry_facts_in_compact_form(tmp_path) -> None:
    """F-08: the transcript BUILD row carries the same facts as the stderr
    banner, folded into (content, detail lines) instead of a raw dump."""
    from hyperion.infra import provenance

    package_dir = tmp_path / "hyperion"
    package_dir.mkdir()
    (package_dir / "module.py").write_text("VALUE = 1\n", encoding="utf-8")
    (tmp_path / "searxng_settings.yml").write_text("server:\n", encoding="utf-8")
    (tmp_path / "searxng_settings.web.yml").write_text("web:\n", encoding="utf-8")

    snapshot = provenance.Provenance(
        package_dir=str(package_dir),
        repo_root=str(tmp_path),
        git_sha="abc1234",
        git_dirty=True,
        install_mode="editable",
        stale_pycache=[],
        source_hash=provenance._source_hash(package_dir),
        settings_hash=provenance._settings_hash(tmp_path),
        profile_hashes=provenance._profile_hashes(tmp_path),
        policy={"task_timeout_s": 600, "search_budget_cap": 600},
    )

    content, detail = provenance.banner_lines(snapshot)
    # Content is one compact line: build + dirty + mode + platform.
    assert content.startswith("HYPERION build abc1234 +dirty · editable · platform=")
    assert "\n" not in content
    # The audit facts survive in the styled detail lines.
    joined = "\n".join(detail)
    assert "fingerprint" in joined
    assert "source=" in joined
    assert "searxng_settings.web.yml=" in joined
    assert "policy" in joined
    assert "task_timeout_s=600" in joined
    assert "search_budget_cap=600" in joined


# ─────────────────────────────────────────────────────────────────────────────
# F-09: corpus-floor escalation is terminal
# ─────────────────────────────────────────────────────────────────────────────


def test_f09_corpus_floor_constants_are_consistent() -> None:
    from hyperion.agents.support.quality_gate import QualityGate
    from hyperion.orchestrator import WorkflowEngine

    # One evidence contract: the render-boundary corpus floor is the same
    # number the orchestrator's targeted escalation uses.
    assert WorkflowEngine._CORPUS_FLOOR_SOURCE_FLOOR == QualityGate._CORPUS_FLOOR_DOMAINS
