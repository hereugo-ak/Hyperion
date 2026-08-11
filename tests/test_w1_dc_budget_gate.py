"""D-C (overhaul3_audit.md W1/S3): membership-aware sub-agent budget gate.

The 2026-08-11 run: AMBER halved the sub-agent total ceiling (6→3,
orchestrator.py:3453). Once the set of distinct questions was full, the
SIZE-ONLY gate (base.py:1377 ``len(distinct_questions) >= CEILING``) refused
EVERY spawn — including STRONG-tier self-heal retries of an ALREADY-COUNTED
question. So ``PROVIDER_FAILURE`` → self-heal → ``[]`` at the gate → the
parent logged "still failed on STRONG tier" when STRONG never ran. A log
asserted an action a gate refused: the D-C lie.

Fix (overhaul3_audit.md D-C + §5.4 P1/P2):

- The gate is membership-aware: a retry of a counted question is budget-free;
  only genuinely NEW work items consume the ceiling.
- The budget-refusal stamps ``_last_spawn_refused`` so the self-heal can log
  "REFUSED BY BUDGET — typed BUDGET_REFUSED" instead of claiming STRONG ran
  and failed. Logs must never lie (§4.5).

Tests reproduce the real failure, not happy-path mocks: before the fix, the
"retry of counted question executes" test fails (the gate refuses it) and the
honesty test fails (the log still claims "still failed on STRONG tier").
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


def _parent() -> MagicMock:
    parent = MagicMock()
    parent.max_sub_agents = 3
    parent.SUB_AGENT_TOTAL_CEILING = 6
    parent.SUB_AGENT_CONCURRENT_MAX = 5
    parent.spec = SimpleNamespace(max_sub_agents=3)
    parent._sub_agent_specs = []
    parent._sub_agent_respawned = set()
    parent._sub_agent_questions = set()
    # Pin attributes the spawn loop reads/writes so MagicMock does not
    # auto-create a mock for a missing one (a mock is truthy and would send
    # the deferred-drain / refusal checks down the wrong branch).
    parent._deferred_specs = []
    parent._last_spawn_refused = False
    parent.state = SimpleNamespace(sub_agents_spawned=0, sub_agents_active=0)
    parent._transition = AsyncMock()
    parent._log = MagicMock()
    parent._dependency_health_green = lambda: True
    # Bind the REAL respawn guard: MagicMock would auto-create a truthy mock
    # for a missing method and the respawn branch would fire every time
    # (infinite recursion). The real method makes the budget behaviour what
    # we are actually testing.
    parent._should_respawn_broadened = (
        BaseAgent._should_respawn_broadened.__get__(parent, type(parent))
    )
    return parent


def _gap_finding() -> KeyFinding:
    return KeyFinding(
        id="f", agent="sub", finding_type=RESEARCH_GAP_TYPE, title="t",
        content="no validated findings here", confidence=ConfidenceLevel.LOW,
    )


# ── the core D-C behaviour: retry of a counted question executes ─────────────


@pytest.mark.asyncio
async def test_retry_of_counted_question_executes_when_ceiling_full() -> None:
    """D-C reproduction — a STRONG self-heal / broadened respawn re-enters
    with an ALREADY-COUNTED question while the ceiling is full.

    Before the fix the size-only gate refused it (``[]`` returned before the
    runner was even constructed) — the exact 2026-08-11 "budget reached (3/3)"
    refusal on every retry. After the fix the spawn executes.
    """
    parent = _parent()
    parent._sub_agent_questions = {f"q_{i}" for i in range(6)}  # ceiling full
    parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))

    # The spec retries a question the budget has ALREADY counted.
    spec = _spec(question="q_0")

    run_mock = AsyncMock(return_value=[])
    with patch.object(SubAgentRunner, "run", new=run_mock):
        findings = await parent._spawn_sub_agent(spec)

    log_lines = [c.args[0] for c in parent._log.call_args_list]
    assert not any("distinct work items" in line for line in log_lines), (
        "a retry of an already-counted question must NOT be refused by the "
        "total budget gate"
    )
    assert getattr(parent, "_last_spawn_refused", False) is False
    assert parent.state.sub_agents_spawned >= 1, (
        "the retry must actually spawn (the runner slot must be consumed)"
    )
    assert run_mock.call_count >= 1, (
        "the retried sub-agent must actually run — the budget gate refused "
        "it before the runner was even constructed"
    )


# ── the strict side survives: a genuinely NEW work item is still refused ─────


@pytest.mark.asyncio
async def test_new_question_still_refused_when_ceiling_full() -> None:
    """A NEW work item at a full ceiling remains refused — membership-aware
    changes who is exempt, not the ceiling itself."""
    parent = _parent()
    parent._sub_agent_questions = {f"q_{i}" for i in range(6)}  # ceiling full
    parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))

    with patch.object(SubAgentRunner, "run", new=AsyncMock(return_value=[])):
        findings = await parent._spawn_sub_agent(_spec())  # fresh question

    assert findings == []
    log_lines = [c.args[0] for c in parent._log.call_args_list]
    assert any("distinct work items" in line for line in log_lines)
    assert getattr(parent, "_last_spawn_refused", False) is True, (
        "the gate must stamp the refusal so callers can log the truth"
    )
    assert parent.state.sub_agents_spawned == 0


# ── P2 honesty: a budget-refused self-heal never claims STRONG ran ───────────


@pytest.mark.asyncio
async def test_budget_refused_self_heal_logs_truthfully() -> None:
    """D-C log-honesty reproduction — the 2026-08-11 lie.

    Fault injection (audit §5.7): the STRONG-tier heal is REFUSED at the
    budget gate. The self-heal must log "REFUSED BY BUDGET" and NEVER
    "still failed on STRONG tier" — the old line asserted an action the gate
    refused.
    """
    parent = _parent()
    parent._spawn_sub_agent = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))

    async def _fake_run(self):  # noqa: ANN001
        self.recovery_hint = "PROVIDER_FAILURE"
        return [_gap_finding()]

    # Intercept the STRONG-tier respawn: simulate a gate refusal (returns []
    # with the refusal stamp), exactly as the budget gate does.
    async def _refusing_spawn(spec: SubAgentSpec) -> list:
        if spec.model_tier == ModelTier.STRONG:
            parent._last_spawn_refused = True
            return []
        return await real_spawn(spec)

    real_spawn = BaseAgent._spawn_sub_agent.__get__(parent, type(parent))
    parent._spawn_sub_agent = _refusing_spawn  # type: ignore[method-assign]

    with patch.object(SubAgentRunner, "run", new=_fake_run):
        findings = await real_spawn(_spec())

    log_lines = [c.args[0] for c in parent._log.call_args_list]
    assert any("REFUSED BY BUDGET" in line for line in log_lines), (
        "a budget-refused heal must be logged as refused by budget"
    )
    assert not any(
        "still failed on STRONG tier" in line for line in log_lines
    ), "logs must NEVER claim STRONG ran and failed when the gate refused it"
    # Terminal outcome: the heal produced no substantive evidence.
    assert not any(f.finding_type != RESEARCH_GAP_TYPE for f in findings)
