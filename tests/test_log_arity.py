"""D-A (overhaul3_audit.md W0/S1): zero `_log()` call sites with >1 positional arg.

The 2026-08-11 AST sweep proved the D-A bug class has EXACTLY four sites:

- hyperion/agents/specialists/competitive_intel.py:529  (fatal — Stage-A success path)
- hyperion/agents/specialists/competitive_intel.py:568  (fatal — Stage-B fallback path)
- hyperion/orchestrator.py:2015                        (fatal + latent — corpus-progress
                                                        signal, fires under a starved fleet)
- hyperion/orchestrator.py:3341                        (silent — KPI-regression telemetry,
                                                        swallowed by the enclosing except)

`BaseAgent._log(self, message)` (base.py:565) and `WorkflowEngine._log(self, message)`
(orchestrator.py:394) accept exactly ONE positional argument. Passing more raises
`TypeError: _log() takes 2 positional arguments but 3 were given` at the CALL SITE —
before the method body's exception-swallowing shim runs. That is the exact crash that
killed COMPETE at 06:31:36 on 2026-08-11.

Why the bug survived Overhaul-1/2: tests mocked discovery to return EMPTY, so the
success path (the branch where the 2-arg `_log` actually fires) was never exercised.

These tests make the "exactly 4, verified complete" claim a permanent regression lock:
an AST walk of every hyperion/**/*.py asserts ZERO offending sites, and a behavioural
test drives the real Stage-A + Stage-B success path against the REAL `_log` to prove
the crash is gone — the real failure, not a happy-path mock.
"""

from __future__ import annotations

import ast
import pathlib
from unittest.mock import AsyncMock, MagicMock

import pytest

from hyperion.agents.base import BaseAgent
from hyperion.agents.specialists.competitive_intel import CompetitiveIntel
from hyperion.orchestrator import WorkflowEngine


class _ConcreteBase(BaseAgent):
    """Minimal concrete BaseAgent so the crash-signature test can bind
    ``BaseAgent._log`` without tripping ABCMeta's abstract-method guard."""

    async def run(self, *args: object, **kwargs: object) -> None:
        raise NotImplementedError

HYPERION_ROOT = pathlib.Path(__file__).resolve().parents[1] / "hyperion"

# Single-message log methods (the D-A bug class). `_log(self, message)`.
_LOG_MAX_POSITIONAL_ARGS = 1
# AgentBus.publish_status(self, agent, state, detail="", **extra) legitimately
# takes up to THREE positional args; 4+ is the same arity bug class on a
# different method and is guarded here too.
_PUBLISH_STATUS_MAX_POSITIONAL_ARGS = 3


def _arity_violations() -> list[tuple[str, int, str]]:
    """Return (path, lineno, label) for every arity-violating call site.

    Walks every ``*.py`` under ``hyperion/``. A call to a method named ``_log``
    with >1 positional arg, or to ``publish_status`` with >3 positional args,
    is a violation. Starred args (``*args``) are excluded from the count.
    """
    violations: list[tuple[str, int, str]] = []
    for path in sorted(HYPERION_ROOT.rglob("*.py")):
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:  # pragma: no cover - never gate on a broken parse
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            attr = getattr(node.func, "attr", None)
            if attr == "_log":
                n_positional = len([
                    a for a in node.args if not isinstance(a, ast.Starred)
                ])
                if n_positional > _LOG_MAX_POSITIONAL_ARGS:
                    violations.append((
                        str(path), node.lineno,
                        f"_log(...) with {n_positional} positional args",
                    ))
            elif attr == "publish_status":
                n_positional = len([
                    a for a in node.args if not isinstance(a, ast.Starred)
                ])
                if n_positional > _PUBLISH_STATUS_MAX_POSITIONAL_ARGS:
                    violations.append((
                        str(path), node.lineno,
                        f"publish_status(...) with {n_positional} positional args",
                    ))
    return violations


# ── The regression lock ──────────────────────────────────────────────────────


def test_zero_log_call_sites_with_extra_positional_args() -> None:
    """D-A: the whole package must have ZERO `_log` calls with >1 positional arg.

    Before the 2026-08-11 fix this failed with exactly 4 sites (the table in
    the module docstring). The audit's "exactly 4, verified complete" claim is
    now a permanent AST regression lock instead of a one-time grep.
    """
    violations = _arity_violations()
    assert violations == [], (
        "D-A arity violations (was exactly 4 before the fix):\n"
        + "\n".join(
            f"  {path}:{lineno}  {label}" for path, lineno, label in violations
        )
    )


# ── Behavioural reproduction of the REAL crash ──────────────────────────────


def test_log_methods_accept_exactly_one_positional_argument() -> None:
    """The exact crash signature.

    Calling either single-message `_log` with a second positional arg raises
    `TypeError: _log() takes 2 positional arguments but 3 were given` — this is
    what killed COMPETE at 06:31:36 on 2026-08-11 (the TypeError binds before
    the exception-swallowing body runs).
    """
    for obj in (object.__new__(_ConcreteBase), object.__new__(WorkflowEngine)):
        with pytest.raises(TypeError):
            obj._log("message", "extra")  # type: ignore[call-arg]


async def test_competitor_discovery_success_path_does_not_crash_on_log() -> None:
    """D-A reproduction — the REAL failure, not a happy-path mock.

    Before the fix, `_identify_competitors` crashed with a 2-arg `_log` the
    moment Stage-A discovery returned candidates (the observed crash). Tests
    missed it because they mocked `_log` away; here the REAL `BaseAgent._log`
    is used, so a regression raises exactly as it did in production.
    """
    agent = object.__new__(CompetitiveIntel)
    agent._context = {"geography": "India", "subject_class": "nation_or_region"}
    agent._question = "Should India invest more in home-grown space tech?"
    agent._competitor_names = []
    agent._llm_competitor_candidates = []
    agent._sources = []
    agent.bus = MagicMock()

    # Stage A returns candidates → fires the Stage-A "%d candidate(s)" log.
    agent._discover_competitors_llm = AsyncMock(return_value=[
        {"name": "ISRO", "arena_role": "national space agency"},
        {"name": "Skyroot Aerospace", "arena_role": "launch startup"},
    ])
    # Search yields no citable rows → fires the Stage-B fallback "%d candidate(s)" log.
    searxng = MagicMock()
    searxng.search = AsyncMock(return_value=[])
    agent.get_tool = MagicMock(return_value=searxng)
    agent._extract_competitor_names = AsyncMock(return_value=([], set()))

    # Both log sites run against the real _log. A 2-arg _log raises TypeError
    # here and the await below fails — exactly the production crash.
    await agent._identify_competitors("space sector")

    # The model-knowledge fallback still produced the competitor list.
    assert agent._competitor_names == ["ISRO", "Skyroot Aerospace"]
