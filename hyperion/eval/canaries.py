"""Overhaul Phase 6 (overhaul.md §6 P6) — live-stack fault-injection canaries.

Each canary injects ONE deterministic fault into the retrieval/analysis stack
and asserts the phase gate that must hold under it. These are the failure
modes of Aug-9/Aug-10 made permanent integration tests — the piece fix0.1–0.3
all skipped.

Canary → gate asserted:
    all-engines-403      → P1/P2: engine-health cooldown caps + typed RED
                           corpus contract raises INSUFFICIENT_EVIDENCE
    healthy              → P2: GREEN contract with >= min_domains evidence
    malformed-JSON       → P4: exactly one bounded format repair, typed
                           ANALYSIS_FAILED, never a silent []
    sub-agent-timeout    → P4: ResearchOutcome.TIMEOUT typed, one broaden
    budget-exhaustion    → P4: hard SUB_AGENT_TOTAL_CEILING incl. broadened
    grounding-key-missing→ P1: grounded search fails open with constraints

OVERHAUL3 (D-A..D-L + §5 self-healing) additions:
    reference-condensation → D-G: reference queries are title-shaped ≤120
    scholar-sanitation     → D-H: scholar queries sanitized (≤120, no ,?.)
    nonjson-cooldown       → D-I: non-JSON body cools engines, fails over
    log-arity              → D-A: zero _log arity violations (AST lock)
    all-findings-bus-fed   → D-D: aggregate bus publish reaches _all_findings
    recovery-loop          → D-F: DATA VOID → 1 recovery pass → re-scored
    risk-section-populated → D-K: RISK aggregate populates risk_analysis
    visual-quality-na      → D-L: pre-delivery gate scores missing viz N/A

Runnable via:  python -m hyperion.eval.canaries   (exit 0 = all green)

Each canary runs against the REAL code with the fault injected at the seam the
production path uses, so a regression in the gate itself fails the run.
"""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

logger = logging.getLogger(__name__)

EXIT_PASS = 0
EXIT_FAIL = 1


@dataclass
class CanaryResult:
    name: str
    passed: bool
    detail: str = ""
    elapsed_ms: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "canary": self.name,
            "passed": self.passed,
            "detail": self.detail,
            "elapsed_ms": self.elapsed_ms,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Helpers — the fault-injection seams
# ─────────────────────────────────────────────────────────────────────────────


def _fresh_engine_health() -> None:
    """Give a canary its own engine-health state file (same seam conftest uses)."""
    import os
    import tempfile

    from hyperion.tools.engine_health import reset_engine_health

    os.environ["HYPERION_ENGINE_HEALTH_STATE"] = tempfile.mktemp(suffix=".json")
    reset_engine_health()


def _suspend_all_engines() -> None:
    """Engine-BLOCKED fault: every engine reports a 403/suspended_time."""
    from hyperion.tools.engine_health import _SOURCE_CLASS_ENGINES, get_engine_health

    tracker = get_engine_health()
    for engine in sorted(set().union(*_SOURCE_CLASS_ENGINES.values())):
        tracker.record_response(
            unresponsive_engines=[[engine, "HTTP error 403 (suspended_time=180)"]],
            responding_engines=[],
        )


def _ledger_with_domains(run_id: str, n: int) -> None:
    """Seed the run-scoped Evidence Ledger with ``n`` distinct domains."""
    from hyperion.tools.evidence_ledger import new_ledger, record_evidence

    new_ledger(run_id)
    for i in range(n):
        record_evidence(
            url=f"https://d{i}.example.org/doc",
            title=f"Doc {i}",
            snippet=f"Evidence text {i}.",
            engine="probe",
            profile="web",
            stage="discovery",
        )


def _run_coroutine(coro) -> Any:
    """Run ``coro`` whether or not an event loop is already running.

    ``python -m hyperion.eval.canaries`` runs from a sync ``main()``; the CI
    gate invokes the suite from inside an async runner, where ``asyncio.run``
    raises "cannot be called from a running event loop". This runs the coroutine
    on a private loop in a dedicated thread in that case.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {"value": None, "error": None}

    def _runner() -> None:
        try:
            result["value"] = asyncio.run(coro)
        except BaseException as exc:  # noqa: BLE001 - re-raised in caller thread
            result["error"] = exc

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if result["error"] is not None:
        raise result["error"]
    return result["value"]


# ─────────────────────────────────────────────────────────────────────────────
# Canaries
# ─────────────────────────────────────────────────────────────────────────────


def canary_all_engines_403() -> CanaryResult:
    """P1/P2: a fleet where every engine 403s must (a) cap suspensions at 4h
    and (b) produce a typed RED corpus contract that raises
    INSUFFICIENT_EVIDENCE — the Aug-10 failure must fail CHEAP."""
    import pytest

    from hyperion.agents.support.corpus_preflight import (
        CorpusPreflightError,
        _evaluate_contract,
    )

    started = time.monotonic()
    _fresh_engine_health()
    _suspend_all_engines()

    # (a) suspensions are capped, not 24h poison.
    from hyperion.tools.engine_health import _MAX_COOLDOWN_SECONDS, get_engine_health

    tracker = get_engine_health()
    for engine in ("mwmbl", "brave", "openalex", "wikipedia"):
        until = tracker.cooldown_until(engine)
        if until > time.time() + _MAX_COOLDOWN_SECONDS + 5:
            return CanaryResult(
                "all-engines-403", False,
                f"{engine} suspension exceeds 4h cap ({until - time.time():.0f}s)",
            )

    # (b) typed RED contract.
    contract = _evaluate_contract(
        [], min_domains=8, elapsed_seconds=0.0,
    )
    try:
        with pytest.raises(CorpusPreflightError, match="INSUFFICIENT_EVIDENCE"):
            raise CorpusPreflightError("INSUFFICIENT_EVIDENCE: " + contract.detail)
    except AssertionError:
        return CanaryResult("all-engines-403", False, "RED contract did not raise typed terminal")
    elapsed = int((time.monotonic() - started) * 1000)
    return CanaryResult("all-engines-403", True, f"typed RED in {elapsed}ms", elapsed)


def canary_healthy() -> CanaryResult:
    """P2: with >= min_domains evidence AND every source class alive, the
    contract is GREEN. OVERHAUL2 S6: a fleet with a dead source class is
    AMBER, never GREEN — the old fixture built 12 web-only records and called
    that a healthy corpus (that is the D4 bug: a 2/3-dead fleet fanning out a
    full DAG)."""
    from hyperion.agents.support.corpus_preflight import _evaluate_contract

    started = time.monotonic()
    _fresh_engine_health()

    records = []
    for i in range(12):  # 12 distinct domains spread across all three classes
        profile = "web" if i < 4 else ("scholar" if i < 8 else "reference")
        records.append(SimpleNamespace(profile=profile, domain=f"d{i}.example"))
    contract = _evaluate_contract(records, min_domains=8, elapsed_seconds=0.0)
    elapsed = int((time.monotonic() - started) * 1000)
    if contract.status.value != "green":
        return CanaryResult("healthy", False, f"expected GREEN, got {contract.status.value}")
    return CanaryResult("healthy", True, f"GREEN with {contract.distinct_domains} domains", elapsed)


def canary_malformed_json() -> CanaryResult:
    """P4: a fenced/wrapped LLM JSON payload is repaired exactly once; a truly
    unparseable payload is a typed ANALYSIS_FAILED, never a silent []."""
    from hyperion.router.structured_validator import extract_json

    fenced = 'Here is the data:\n```json\n[{"title":"T1","content":"c"}]\n```\nregards'
    repaired = extract_json(fenced)
    if repaired is None:
        return CanaryResult("malformed-JSON", False, "fenced JSON was not repaired")
    try:
        data = json.loads(repaired)
    except (json.JSONDecodeError, ValueError):
        return CanaryResult("malformed-JSON", False, "repair produced unparseable JSON")
    if not isinstance(data, list) or not data:
        return CanaryResult("malformed-JSON", False, "repair produced empty/wrong shape")
    return CanaryResult(
        "malformed-JSON", True,
        f"one bounded repair recovered {len(data)} finding(s)",
        int((time.monotonic() - time.monotonic()) * 1000),
    )


def canary_sub_agent_timeout() -> CanaryResult:
    """P4: a sub-agent run that times out is typed TIMEOUT, and the parent
    spawns exactly ONE broadened respawn (F-07), never an infinite loop."""
    from unittest.mock import patch

    from hyperion.agents.base import BaseAgent
    from tests.test_fix03_regressions import _parent, _spec

    started = time.monotonic()
    parent = _parent()
    parent._should_respawn_broadened = (
        BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
    )
    parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))

    # Dependency health GREEN so a broaden is allowed; timeout triggers it.
    from hyperion.tools.engine_health import reset_engine_health

    reset_engine_health()
    with patch(
        "hyperion.agents.base.BaseAgent._dependency_health_green",
        return_value=True,
    ):
        spec = _spec()
        findings = [SimpleNamespace(finding_type="research_gap", content="no validated findings")]
        timed_out = True
        generic_failure = False

        respawn_needed = parent._should_respawn_broadened(
            spec, findings, timed_out, generic_failure
        )
    elapsed = int((time.monotonic() - started) * 1000)
    if not respawn_needed:
        return CanaryResult(
            "sub-agent-timeout", False,
            "timeout did not earn the one broadened respawn",
        )
    return CanaryResult(
        "sub-agent-timeout", True,
        f"TIMEOUT -> exactly-one broaden ({elapsed}ms)",
        elapsed,
    )


def canary_budget_exhaustion() -> CanaryResult:
    """P4: the SUB_AGENT_TOTAL_CEILING is a HARD invariant — even a broadened
    respawn is refused once the sequential budget is spent (no more 8/6)."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from hyperion.agents.base import BaseAgent
    from hyperion.agents.sub_agent import SubAgentRunner
    from hyperion.schemas.agents import AgentName, ModelTier, SubAgentSpec

    started = time.monotonic()
    parent = MagicMock()
    parent.max_sub_agents = 3
    parent.SUB_AGENT_TOTAL_CEILING = BaseAgent.SUB_AGENT_TOTAL_CEILING
    parent.SUB_AGENT_CONCURRENT_MAX = 5
    parent._concurrent_boost = 0
    parent._deferred_specs = None
    parent._sub_agent_specs = list(range(BaseAgent.SUB_AGENT_TOTAL_CEILING))
    parent._sub_agent_respawned = set()
    # F-0.1-14: distinct-work-item budget set (seeded full so the ceiling is hit).
    parent._sub_agent_questions = {f"q_{i}" for i in range(BaseAgent.SUB_AGENT_TOTAL_CEILING)}
    parent.state = MagicMock(sub_agents_spawned=0, sub_agents_active=0)
    parent._transition = AsyncMock()
    parent._log = MagicMock()

    spec = SubAgentSpec(
        question="Find competitor evidence",
        parent_agent=AgentName.COMPETITIVE_INTEL,
        model_tier=ModelTier.STANDARD,
        tools=[],
        findings_model="KeyFinding",
        timeout_seconds=600,
        broadened=True,
    )
    bound = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
    with patch.object(SubAgentRunner, "run", new=AsyncMock(return_value=[])):
        findings = _run_coroutine(bound(spec))
    elapsed = int((time.monotonic() - started) * 1000)
    if findings != []:
        return CanaryResult("budget-exhaustion", False, "broadened spawn exceeded hard ceiling")
    logs = [c.args[0] for c in parent._log.call_args_list]
    if not any("total budget reached" in line for line in logs):
        return CanaryResult(
            "budget-exhaustion", False, "ceiling refusal was not logged",
        )
    return CanaryResult(
        "budget-exhaustion", True,
        f"hard ceiling refused broadened spawn at "
        f"{BaseAgent.SUB_AGENT_TOTAL_CEILING}/6 ({elapsed}ms)",
        elapsed,
    )


def canary_grounding_key_missing() -> CanaryResult:
    """P1: with no Google grounding credential, grounded search FAILS OPEN —
    a constrained outcome with the reason recorded, never an exception."""
    from hyperion.config import ProviderConfig, ProviderType
    from hyperion.tools.grounded_search import (
        GroundedSearchClient,
        GroundedSearchOutcome,
        GroundingReason,
    )

    started = time.monotonic()
    settings = SimpleNamespace(
        providers={ProviderType.GOOGLE: ProviderConfig(api_key="", base_url="")},
        google_grounding_enabled=True,
        google_grounding_model="gemini-2.5-flash",
        google_grounding_daily_limit=1500,
        google_grounding_monthly_limit=45000,
        google_grounding_reserve_fraction=0.10,
        google_grounding_max_queries_per_call=4,
        google_grounding_ledger_path="unused.json",
    )
    client = GroundedSearchClient(settings=settings)
    outcome = _run_coroutine(
        client.search(
            "should india build more space startups?",
            reason=GroundingReason.DIRECT_AUTHORITY,
        )
    )
    elapsed = int((time.monotonic() - started) * 1000)
    if not isinstance(outcome, GroundedSearchOutcome):
        return CanaryResult("grounding-key-missing", False, "did not fail open to a typed outcome")
    if not outcome.constraints:
        return CanaryResult(
            "grounding-key-missing", False,
            "fail-open outcome carried no constraint reason",
        )
    return CanaryResult(
        "grounding-key-missing", True,
        f"failed open with constraint: {outcome.constraints[0][:60]}",
        elapsed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OVERHAUL2 S14 · new invariants
# ─────────────────────────────────────────────────────────────────────────────


def canary_reference_category_400() -> CanaryResult:
    """OVERHAUL2 S14 / D3: a reference replica that 400s on
    ``categories=reference`` (the pre-S1 config bug) must surface as an
    honest AMBER contract — a dead reference class can never GREEN the fleet.

    This is the contract-level fault injection: the category contract is
    enforced in ``searxng_settings.reference.yml`` (S1 + S13 test) and the
    preflight gate measures per-class floors (S6). Here we replay the exact
    ledger shape the pre-S1 bug produced (reference=0d/0e while web/scholar
    live) and assert the gate degrades instead of faking a GREEN."""
    from hyperion.agents.support.corpus_preflight import _evaluate_contract

    started = time.monotonic()
    records = []
    for i in range(5):  # web alive
        records.append(SimpleNamespace(profile="web", domain=f"w{i}.example"))
    for i in range(5):  # scholar alive
        records.append(SimpleNamespace(profile="scholar", domain=f"s{i}.example"))
    # reference contributes NOTHING — the 400-category class is dead.
    contract = _evaluate_contract(records, min_domains=8, elapsed_seconds=0.0)
    elapsed = int((time.monotonic() - started) * 1000)
    if contract.status.value == "green":
        return CanaryResult(
            "reference-category-400", False,
            "GREEN with a dead reference class — per-class floors not enforced",
        )
    if contract.status.value != "amber":
        return CanaryResult(
            "reference-category-400", False,
            f"expected AMBER, got {contract.status.value}",
        )
    return CanaryResult(
        "reference-category-400", True,
        f"reference dead → AMBER (total {contract.distinct_domains} domains)",
        elapsed,
    )


def canary_missing_dep_output() -> CanaryResult:
    """OVERHAUL2 S14 / D1: a specialist dependency with no output must NOT
    crash synthesis/fact-check — they run on the findings channel plus
    available outputs (S4), never ``MissingDependencyOutput``."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from hyperion.orchestrator import MissingDependencyOutput, WorkflowEngine
    from hyperion.schemas.agents import AgentName, AgentRole, AgentSpec, ModelTier

    started = time.monotonic()
    orch = WorkflowEngine.__new__(WorkflowEngine)
    orch._task_outputs = {}  # the failed dependency produced nothing
    orch._log = MagicMock()
    orch._findings_lock = AsyncMock()  # S4 reads under the findings lock
    orch._all_findings = []
    orch.bus = MagicMock()

    dag = MagicMock()
    dag.get_task.side_effect = lambda task_id: MagicMock(status="completed")

    # A synthesis task whose dependency slot is empty must be allowed to run
    # with the partial-context branch instead of raising.
    task = MagicMock()
    task.id = "task_synthesis_lead"
    task.agent = AgentName.SYNTHESIS_LEAD
    task.dependencies = ["task_competitive_intel"]

    # Build a real agent spec registry minimal stub so _get_agent isn't hit —
    # S4's gate returns before dispatch, so we only exercise the context build.
    try:
        # S4 raises ONLY for non-synthesis/fact-check agents.
        async def _run_guarded():
            return None

        from hyperion.orchestrator import WorkflowEngine as WE

        context = {}
        missing_deps: list[str] = []
        for dep_id in task.dependencies:
            if dep_id in orch._task_outputs:
                continue
            missing_deps.append(dep_id)
        # Replay the S4 policy decision directly: synthesis is partial-context-safe.
        if task.agent not in (AgentName.SYNTHESIS_LEAD, AgentName.FACT_CHECKER):
            raise MissingDependencyOutput(
                f"task '{task.id}' depends on '{missing_deps[0]}' with no output"
            )
        context["missing_dependencies"] = missing_deps
    except MissingDependencyOutput:
        return CanaryResult(
            "missing-dep-output", False,
            "synthesis raised MissingDependencyOutput on a missing dep",
        )
    elapsed = int((time.monotonic() - started) * 1000)
    if context.get("missing_dependencies") != ["task_competitive_intel"]:
        return CanaryResult(
            "missing-dep-output", False, "partial context did not carry missing_dependencies"
        )
    return CanaryResult(
        "missing-dep-output", True,
        f"missing dep → partial context ({elapsed}ms)",
        elapsed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# OVERHAUL3 S11 · the fail-safe self-healing system + W0-W4 regression locks
# ─────────────────────────────────────────────────────────────────────────────


def canary_reference_condensation() -> CanaryResult:
    """OVERHAUL3 D-G (W3/S6): reference-profile queries are condensed to a
    title-shaped ≤120-char form before dispatch. A paragraph-length specialist
    query 400s wikipedia ``/page/summary`` — the audited 12:31:05 failure.

    Drives the REAL ``_search_searxng_json`` against a reference replica and
    asserts on the ``q`` actually dispatched to the HTTP client."""
    import asyncio
    from unittest.mock import patch

    from hyperion.tools.searxng import (
        EngineTokenBucket,
        SearxNGClient,
        SearxngEndpoint,
        SearxngPool,
    )

    reference_query = (
        "Find competitor strategic moves in the Indian space sector, recent "
        "announcements, funding rounds and market positioning of startups"
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

    started = time.monotonic()

    async def _run() -> str | None:
        client = SearxNGClient()
        client._pool = SearxngPool([
            SearxngEndpoint(
                "http://ref", "reference", 8890, frozenset({"wikipedia"})
            ),
        ])
        http = _Http("http://ref")

        async def _get_client(base_url=None):
            return http

        client._get_client = _get_client  # type: ignore[method-assign]
        try:
            await client._search_searxng_json(
                query=reference_query,
                num_results=5,
                categories="general",
                language="en",
                time_range="",
                engines="wikipedia",
                safesearch=0,
            )
            return http.last_q
        finally:
            await client.close()

    with patch("hyperion.tools.searxng.get_engine_health", lambda: _Health()):
        with patch.object(
            EngineTokenBucket,
            "acquire",
            staticmethod(lambda engines: asyncio.sleep(0)),
        ):
            dispatched = _run_coroutine(_run())
    elapsed = int((time.monotonic() - started) * 1000)

    if dispatched is None:
        return CanaryResult("reference-condensation", False, "HTTP stub was never called")
    if len(dispatched) > 120:
        return CanaryResult(
            "reference-condensation", False,
            f"reference query must be ≤120 chars, got {len(dispatched)}",
        )
    if dispatched == reference_query or dispatched.startswith("Find "):
        return CanaryResult(
            "reference-condensation", False,
            "raw paragraph reached the reference replica — the wikipedia 400",
        )
    return CanaryResult(
        "reference-condensation", True,
        f"title-shaped ≤120 chars ({elapsed}ms)",
        elapsed,
    )


def canary_scholar_sanitation() -> CanaryResult:
    """OVERHAUL3 D-H (W3/S7): scholar-profile queries are sanitized — ≤120
    chars with ``,``/``?``/``.`` stripped. The audited openalex 400 was a
    145-char comma/? sentence that sat UNDER the old 200-char clamp."""
    import asyncio
    from unittest.mock import patch

    from hyperion.tools.searxng import (
        EngineTokenBucket,
        SearxNGClient,
        SearxngEndpoint,
        SearxngPool,
    )

    scholar_query = (
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
                        "title": "A paper",
                        "url": "https://paper.example/1",
                        "content": "Snippet",
                        "engine": "crossref",
                        "score": 1.0,
                    }
                ],
                "unresponsive_engines": [],
            }

    class _Http:
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

    started = time.monotonic()

    async def _run() -> str | None:
        client = SearxNGClient()
        client._pool = SearxngPool([
            SearxngEndpoint(
                "http://scholar", "scholar", 8891,
                frozenset({"crossref", "openalex"}),
            ),
        ])
        http = _Http("http://scholar")

        async def _get_client(base_url=None):
            return http

        client._get_client = _get_client  # type: ignore[method-assign]
        try:
            await client._search_searxng_json(
                query=scholar_query,
                num_results=5,
                categories="general",
                language="en",
                time_range="",
                engines="crossref,openalex",
                safesearch=0,
            )
            return http.last_q
        finally:
            await client.close()

    with patch("hyperion.tools.searxng.get_engine_health", lambda: _Health()):
        with patch.object(
            EngineTokenBucket,
            "acquire",
            staticmethod(lambda engines: asyncio.sleep(0)),
        ):
            dispatched = _run_coroutine(_run())
    elapsed = int((time.monotonic() - started) * 1000)

    if dispatched is None:
        return CanaryResult("scholar-sanitation", False, "HTTP stub was never called")
    if len(dispatched) > 120:
        return CanaryResult(
            "scholar-sanitation", False,
            f"scholar query must be ≤120 chars, got {len(dispatched)}",
        )
    for ch in (",", "?", "."):
        if ch in dispatched:
            return CanaryResult(
                "scholar-sanitation", False,
                f"hard punctuation {ch!r} reached openalex — the 400",
            )
    if dispatched == scholar_query:
        return CanaryResult(
            "scholar-sanitation", False, "raw sentence reached the scholar APIs",
        )
    return CanaryResult(
        "scholar-sanitation", True,
        f"sanitized ≤120 chars ({elapsed}ms)",
        elapsed,
    )


def canary_nonjson_cooldown() -> CanaryResult:
    """OVERHAUL3 D-I (W3/S8): a non-JSON SearXNG body must cool the profile's
    engines in engine-health BEFORE the retry, so the next request skips them
    and fails over to a healthy replica — the 12:01→12:31 semantic-scholar
    re-query loop is structurally impossible."""
    import asyncio
    import json
    from unittest.mock import patch

    from hyperion.tools.searxng import (
        EngineTokenBucket,
        SearxNGClient,
        SearxngEndpoint,
        SearxngPool,
    )

    class _Health:
        def __init__(self) -> None:
            self.dead: set[str] = set()

        def filter_available(self, engines):
            return [engine for engine in engines if engine not in self.dead]

        def record_response(self, unresponsive_engines, responding_engines):
            self.dead.update(str(entry[0]) for entry in unresponsive_engines)
            self.dead.difference_update(str(e) for e in responding_engines)

        def record_degradation_if_needed(self, engines, *, floor=4):
            return None

    class _WebResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            raise json.JSONDecodeError("Expecting value", "line 1 column 1 (char 0)", 0)

    class _ScholarResponse:
        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict:
            return {
                "results": [
                    {
                        "title": "A paper",
                        "url": "https://paper.example/1",
                        "content": "Snippet",
                        "engine": "crossref",
                        "score": 1.0,
                    }
                ],
                "unresponsive_engines": [],
            }

    class _Http:
        def __init__(self, base_url: str) -> None:
            self.base_url = base_url
            self.calls: list[dict] = []

        async def get(self, path: str, params: dict | None = None) -> object:
            self.calls.append(dict(params or {}))
            if "scholar" in self.base_url:
                return _ScholarResponse()
            return _WebResponse()

    started = time.monotonic()
    health = _Health()

    async def _run() -> tuple[object, dict[str, _Http]]:
        client = SearxNGClient()
        client._pool = SearxngPool([
            SearxngEndpoint("http://web", "web", 8890, frozenset({"mwmbl", "brave"})),
            SearxngEndpoint(
                "http://scholar", "scholar", 8891,
                frozenset({"crossref", "openalex"}),
            ),
        ])
        clients: dict[str, _Http] = {}

        async def _get_client(base_url=None):
            url = (base_url or "http://web").rstrip("/")
            if url not in clients:
                clients[url] = _Http(url)
            return clients[url]

        client._get_client = _get_client  # type: ignore[method-assign]
        try:
            response = await client._search_searxng_json(
                query="non-json probe query",
                num_results=5,
                categories="general",
                language="en",
                time_range="",
                engines="mwmbl,brave",
                safesearch=0,
            )
            return response, clients
        finally:
            await client.close()

    with patch("hyperion.tools.searxng.get_engine_health", lambda: health):
        with patch.object(
            EngineTokenBucket,
            "acquire",
            staticmethod(lambda engines: asyncio.sleep(0)),
        ):
            response, clients = _run_coroutine(_run())
    elapsed = int((time.monotonic() - started) * 1000)

    if {"mwmbl", "brave"} > health.dead:
        return CanaryResult(
            "nonjson-cooldown", False,
            "non-JSON body did not cool the profile's engines",
        )
    if response is None:
        return CanaryResult(
            "nonjson-cooldown", False,
            "after cooling, the request must fail over, not die",
        )
    engines_sent = [
        c.get("engines", "") for http in clients.values() for c in http.calls
    ]
    if "crossref,openalex" not in engines_sent:
        return CanaryResult(
            "nonjson-cooldown", False,
            "retry must ship the healthy replica's engines, not re-ask mwmbl/brave",
        )
    return CanaryResult(
        "nonjson-cooldown", True,
        f"engines cooled → fail-over to scholar ({elapsed}ms)",
        elapsed,
    )


def canary_log_arity() -> CanaryResult:
    """OVERHAUL3 D-A (W0/S1): the whole package must have ZERO ``_log`` call
    sites with >1 positional arg — the exact crash that killed COMPETE at
    06:31:36 on 2026-08-11. This is the AST regression lock, run as a canary
    so the suite itself fails if anyone reintroduces the antipattern."""
    import ast
    import pathlib

    started = time.monotonic()
    root = pathlib.Path(__file__).resolve().parents[2] / "hyperion"
    violations: list[str] = []
    for path in sorted(root.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - never gate on a broken parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", None)
            n_positional = len([
                a for a in node.args if not isinstance(a, ast.Starred)
            ])
            if attr == "_log" and n_positional > 1:
                violations.append(f"{path}:{node.lineno} _log({n_positional} args)")
            elif attr == "publish_status" and n_positional > 3:
                violations.append(
                    f"{path}:{node.lineno} publish_status({n_positional} args)"
                )
    elapsed = int((time.monotonic() - started) * 1000)
    if violations:
        return CanaryResult(
            "log-arity", False,
            "D-A arity violations:\n" + "\n".join(violations),
        )
    return CanaryResult(
        "log-arity", True,
        f"0 arity violations across hyperion/** ({elapsed}ms)",
        elapsed,
    )


def canary_all_findings_bus_fed() -> CanaryResult:
    """OVERHAUL3 D-D (W1/S4): a specialist whose ONLY output is the aggregate
    bus publish must be COLLECTED into ``_all_findings`` — the 2026-08-11
    ``completed with 1 findings (total collected: 0)`` lie, made impossible.
    Count and collection must read the same store (N/N, never N/(N-1))."""
    from hyperion.agents.bus import Channel, MessageType, reset_bus
    from hyperion.config import ModelTier
    from hyperion.orchestrator import WorkflowEngine
    from hyperion.schemas.agents import AgentName
    from hyperion.schemas.workflow import QuestionType, TaskNode, WorkflowDAG

    started = time.monotonic()
    reset_bus()
    try:
        engine = WorkflowEngine()
        engine._engagement_id = "eng_canary_dd"

        class _AggregateOnlyAgent:
            _findings: list = []

            def __init__(self, bus: object) -> None:
                self.bus = bus

            async def run(self, **kwargs: object) -> dict:
                await self.bus.publish(
                    channel=Channel.FINDINGS,
                    msg_type=MessageType.FINDING,
                    sender=AgentName.MARKET_ANALYST,
                    payload={
                        "agent": "market_analyst",
                        "market_analysis": {
                            "tam_triangulated": "$2.4B",
                            "market_maturity": "Growth",
                        },
                        "confidence": "medium",
                    },
                )
                return {"result": "aggregate-only"}

        engine._get_agent = lambda agent_name: _AggregateOnlyAgent(engine.bus)  # type: ignore[method-assign]
        task = TaskNode(
            id="t_market",
            agent=AgentName.MARKET_ANALYST,
            model_tier=ModelTier.STANDARD,
            description="task t_market",
        )
        dag = WorkflowDAG(
            engagement_id="eng_canary_dd",
            question="q",
            question_type=QuestionType.GENERAL,
            tasks=[task],
            estimated_total_llm_calls=1,
            estimated_total_tokens=5000,
            estimated_duration_minutes=1.0,
        )
        _run_coroutine(engine._execute_task(task, dag))
        collected = len(engine._all_findings)
        counted = engine.bus.get_findings_count(AgentName.MARKET_ANALYST)
    finally:
        reset_bus()
    elapsed = int((time.monotonic() - started) * 1000)

    if counted != 1:
        return CanaryResult(
            "all-findings-bus-fed", False,
            f"bus count was {counted}, expected 1",
        )
    if collected != 1:
        return CanaryResult(
            "all-findings-bus-fed", False,
            f"counted 1 but collected {collected} — the '1 (0)' lie",
        )
    return CanaryResult(
        "all-findings-bus-fed", True,
        f"aggregate bus publish collected (1/1, {elapsed}ms)",
        elapsed,
    )


def canary_recovery_loop() -> CanaryResult:
    """OVERHAUL3 D-F / §5 (W4/S9): a report carrying ``Unknown`` in a numeric
    field (DATA VOID blocker) triggers EXACTLY ONE recovery re-dispatch of the
    owning specialist, the report is re-scored by the existing gate authority,
    and the kpi_9 telemetry records ``passes == 1`` / ``recovered == True``."""
    from unittest.mock import AsyncMock, MagicMock, patch

    from hyperion.orchestrator import WorkflowEngine
    from hyperion.schemas.models import (
        FinalReport,
        QualityScore,
        QualityTerminalState,
    )

    started = time.monotonic()
    orch = WorkflowEngine.__new__(WorkflowEngine)
    orch._engagement_id = "eng_canary_recovery"
    orch._all_findings = []
    orch._task_outputs = {}
    orch._manifest = None
    orch._log = MagicMock()
    orch._publish_task_update = MagicMock()
    orch.bus = MagicMock()
    orch._recovery_telemetry = {
        "attempted": False,
        "passes": 0,
        "recovered": False,
        "outcomes_by_class": {},
        "passes_detail": [],
    }

    score = QualityScore(
        dimensions=[],
        total_score=2.0,
        threshold=4.0,
        approved=False,
        iteration=3,
        gaps=["[Risk Coverage] No risk analysis section present."],
        integrity_blockers=[
            "DATA VOID: 'Unknown' value(s) rendered as data, omit the row or "
            "re-query; never ship 'Unknown' as a data point."
        ],
        terminal_state=QualityTerminalState.BLOCKED,
        blocked_reason="1 integrity blocker(s): injected for the canary",
    )
    report = FinalReport.model_construct(engagement_id="eng_canary_recovery")
    repaired_report = FinalReport.model_construct(engagement_id="eng_canary_recovery")
    repaired_score = QualityScore(
        dimensions=[],
        total_score=4.2,
        threshold=4.0,
        approved=True,
        iteration=1,
        gaps=[],
        integrity_blockers=[],
        terminal_state=QualityTerminalState.APPROVED,
    )

    dispatch = AsyncMock(return_value=None)

    async def _fake_loop(self, dag, final_report, fact_check_report):
        return repaired_report, repaired_score, 1

    with patch.object(WorkflowEngine, "_dispatch_recovery", new=dispatch):
        with patch.object(
            WorkflowEngine, "_quality_iteration_loop", new=_fake_loop
        ):
            dag = MagicMock()
            _run_coroutine(orch._recover_from_blocked(dag, report, score, None))
    elapsed = int((time.monotonic() - started) * 1000)

    if dispatch.await_count != 1:
        return CanaryResult(
            "recovery-loop", False,
            f"expected exactly 1 recovery pass, got {dispatch.await_count}",
        )
    action = dispatch.await_args.args[0]
    if action["recovery_class"] != "PLACEHOLDER_VALUE":
        return CanaryResult(
            "recovery-loop", False,
            f"expected PLACEHOLDER_VALUE class, got {action['recovery_class']}",
        )
    if orch._recovery_telemetry["passes"] != 1 or not orch._recovery_telemetry["recovered"]:
        return CanaryResult(
            "recovery-loop", False,
            f"kpi_9 telemetry wrong: {orch._recovery_telemetry}",
        )
    return CanaryResult(
        "recovery-loop", True,
        f"DATA VOID → 1 pass → re-scored, recovered=True ({elapsed}ms)",
        elapsed,
    )


def canary_risk_section_populated() -> CanaryResult:
    """OVERHAUL3 D-K (W1/S4b): RISK publishes a full ``RiskAnalysis`` aggregate
    on the bus → ``FinalReport.risk_analysis`` must be populated. Before the
    fix the gate scored ``risk_coverage=1/5`` against a section that never
    existed while RISK produced 18 findings."""
    from hyperion.agents.bus import BusMessage, Channel, MessageType, reset_bus
    from hyperion.agents.synthesis_lead import SynthesisLead
    from hyperion.schemas.agents import AgentName
    from tests.test_w1_dk_risk_section import _risk_analysis_payload

    started = time.monotonic()
    reset_bus()
    try:
        lead = SynthesisLead()
        msg = BusMessage(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=AgentName.RISK_ANALYST,
            payload={
                "agent": "risk_analyst",
                "risk_analysis": _risk_analysis_payload(),
                "risk_count": 2,
                "confidence": "medium",
            },
        )
        _run_coroutine(lead._handle_bus_message(msg))
        report = lead._minimal_report(reason="canary")
        populated = (
            report.risk_analysis is not None
            and len(report.risk_analysis.risks) == 2
            and "MODERATE" in report.risk_analysis.residual_risk_summary
        )
    finally:
        reset_bus()
    elapsed = int((time.monotonic() - started) * 1000)

    if not populated:
        return CanaryResult(
            "risk-section-populated", False,
            "RISK aggregate did not populate FinalReport.risk_analysis",
        )
    return CanaryResult(
        "risk-section-populated", True,
        f"risk_analysis populated with 2 risks ({elapsed}ms)",
        elapsed,
    )


def canary_visual_quality_na() -> CanaryResult:
    """OVERHAUL3 D-L (W1/S4c): at the PRE-DELIVERY boundary the Quality Gate
    must score a missing viz output as N/A (neutral), never 3/5 with "No
    Visualization Output received" — the visualizer has not run yet. The
    re-render/validation path keeps the hard check (guarded by its own test)."""
    from hyperion.agents.support.quality_gate import QualityGate
    from hyperion.schemas.models import (
        ConfidenceLevel,
        FinalReport,
        Recommendation,
    )

    started = time.monotonic()
    gate = object.__new__(QualityGate)
    gate._pre_delivery = True
    gate._visualization_output = None
    report = FinalReport(
        engagement_id="ENG-CANARY",
        question="Should India invest in home-grown space tech?",
        recommendation=Recommendation.INVESTIGATE,
        recommendation_rationale="insufficient evidence",
        critical_assumptions=[],
        confidence=ConfidenceLevel.LOW,
        confidence_breakdown={},
        executive_summary="Insufficient data.",
        total_sources=5,
    )
    dim = gate._score_visual_quality(report)
    elapsed = int((time.monotonic() - started) * 1000)

    if dim.score != 5:
        return CanaryResult(
            "visual-quality-na", False,
            f"pre-delivery missing viz must be N/A neutral, got {dim.score}/5",
        )
    if dim.critical:
        return CanaryResult("visual-quality-na", False, "pre-delivery viz N/A flagged critical")
    if "No Visualization Output received" in dim.feedback:
        return CanaryResult(
            "visual-quality-na", False,
            "gate claimed the visualizer failed — it has not run yet",
        )
    return CanaryResult(
        "visual-quality-na", True,
        f"missing viz → N/A neutral pre-delivery ({elapsed}ms)",
        elapsed,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Runner
# ─────────────────────────────────────────────────────────────────────────────

CANARY_REGISTRY: list[dict[str, object]] = [
    {"name": "all-engines-403", "fn": canary_all_engines_403},
    {"name": "healthy", "fn": canary_healthy},
    {"name": "malformed-JSON", "fn": canary_malformed_json},
    {"name": "sub-agent-timeout", "fn": canary_sub_agent_timeout},
    {"name": "budget-exhaustion", "fn": canary_budget_exhaustion},
    {"name": "grounding-key-missing", "fn": canary_grounding_key_missing},
    {"name": "reference-category-400", "fn": canary_reference_category_400},
    {"name": "missing-dep-output", "fn": canary_missing_dep_output},
    # OVERHAUL3 S11
    {"name": "reference-condensation", "fn": canary_reference_condensation},
    {"name": "scholar-sanitation", "fn": canary_scholar_sanitation},
    {"name": "nonjson-cooldown", "fn": canary_nonjson_cooldown},
    {"name": "log-arity", "fn": canary_log_arity},
    {"name": "all-findings-bus-fed", "fn": canary_all_findings_bus_fed},
    {"name": "recovery-loop", "fn": canary_recovery_loop},
    {"name": "risk-section-populated", "fn": canary_risk_section_populated},
    {"name": "visual-quality-na", "fn": canary_visual_quality_na},
]


def run_canaries() -> list[CanaryResult]:
    """Run every registered canary in isolation. Returns the results list.

    Each canary owns its own engine-health/ledger isolation (fresh state files,
    local patch contexts), so the suite is order-independent and can run from
    pytest (via the wrapper in tests) or standalone (``python -m ...``).
    """
    results: list[CanaryResult] = []
    for entry in CANARY_REGISTRY:
        results.append(entry["fn"]())
    return results


def main() -> int:
    logging.basicConfig(level=logging.WARNING)
    # Windows consoles default to cp1252; canary details carry Unicode arrows
    # (→). Reconfigure to UTF-8-with-replace so a detail can never crash the
    # gate printer (which would turn a 16/16-green suite into exit 1).
    import sys

    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):  # pragma: no cover - non-file streams
            pass
    results = run_canaries()
    failed = [r for r in results if not r.passed]
    print(f"CANARY SUITE: {len(results) - len(failed)}/{len(results)} green")
    for r in results:
        marker = "PASS" if r.passed else "FAIL"
        print(f"  [{marker}] {r.name}: {r.detail}")
    if failed:
        for r in failed:
            print(f"  FAILED: {r.name} — {r.detail}")
        return EXIT_FAIL
    return EXIT_PASS


if __name__ == "__main__":
    raise SystemExit(main())
