"""T-12 · D-02/D-06 · zero evidence must abort, never fabricate.

The 07-30 engagement ran 1,216 s and made 95 LLM calls against a search stack
whose engines were all dead, then shipped a fabricated report. It should never
have been allowed to begin. These tests assert the preflight refusal:

- a dead stack raises EngagementPreflightError with an actionable message
- the refusal fires before any DAG or journal is built (fast abort)
- DEGRADED (some engines answering) is allowed through
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import pytest

from hyperion.infra.preflight import (
    EngagementPreflightError,
    assert_research_stack_usable,
)
from hyperion.obs.health import ToolHealth


def _settings() -> SimpleNamespace:
    return SimpleNamespace(
        searxng_url="http://localhost:8888",
        searxng_host="localhost",
        searxng_port=8888,
    )


def test_offline_stack_raises_with_actionable_message(monkeypatch):
    monkeypatch.setattr(
        "hyperion.obs.health._check_searxng",
        lambda s: ToolHealth(
            name="searxng",
            status="OFFLINE",
            detail="reachable but returned 0 results for 'india import tariff'; "
            "unresponsive: ['duckduckgo', 'bing']",
        ),
    )
    with pytest.raises(EngagementPreflightError, match="Research stack is offline"):
        assert_research_stack_usable(_settings())


def test_offline_detail_is_included(monkeypatch):
    monkeypatch.setattr(
        "hyperion.obs.health._check_searxng",
        lambda s: ToolHealth(name="searxng", status="OFFLINE", detail="0 results; DDG banned"),
    )
    with pytest.raises(EngagementPreflightError, match="DDG banned"):
        assert_research_stack_usable(_settings())


def test_degraded_stack_is_allowed_through(monkeypatch):
    """Some engines dead, some answering → breadth reduced, engagement groundable."""
    degraded = ToolHealth(
        name="searxng", status="DEGRADED", detail="4 results; DEAD: ['duckduckgo']"
    )
    monkeypatch.setattr("hyperion.obs.health._check_searxng", lambda s: degraded)
    assert assert_research_stack_usable(_settings()) is degraded


def test_ok_stack_is_allowed_through(monkeypatch):
    ok = ToolHealth(name="searxng", status="OK", detail="8 results")
    monkeypatch.setattr("hyperion.obs.health._check_searxng", lambda s: ok)
    assert assert_research_stack_usable(_settings()) is ok


def test_passed_health_result_is_used_without_requery(monkeypatch):
    """A caller that already ran the health table must not pay for a second query."""
    monkeypatch.setattr(
        "hyperion.obs.health._check_searxng",
        lambda s: pytest.fail("smoke query re-issued despite a provided result"),
    )
    offline = ToolHealth(name="searxng", status="OFFLINE", detail="port closed")
    with pytest.raises(EngagementPreflightError):
        assert_research_stack_usable(_settings(), health_result=offline)


@pytest.mark.asyncio
async def test_run_engagement_aborts_on_dead_stack(monkeypatch):
    """End-to-end refusal: run_engagement raises fast, before any DAG/journal."""
    from hyperion.orchestrator import WorkflowEngine

    monkeypatch.setattr("hyperion.obs.health.check_startup_health", lambda s: None)
    monkeypatch.setattr(
        "hyperion.obs.health._check_searxng",
        lambda s: ToolHealth(
            name="searxng",
            status="OFFLINE",
            detail="reachable but returned 0 results; unresponsive: ['duckduckgo']",
        ),
    )
    engine = WorkflowEngine()
    started = time.monotonic()
    with pytest.raises(EngagementPreflightError, match="Research stack is offline"):
        await engine.run_engagement(question="should india import less ?")
    assert time.monotonic() - started < 10.0, "preflight refusal must abort in <10 s"
    assert engine._journal is None, "journal was opened despite the refusal"
    assert engine._director is None, "a DAG was built despite the refusal"
