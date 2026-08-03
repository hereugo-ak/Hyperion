"""D-22 regression tests for escalation evaluation without an active DAG."""

from __future__ import annotations

import inspect

import pytest

from hyperion.agents.engagement_director import EngagementDirector


@pytest.mark.asyncio
async def test_evaluate_escalation_without_dag_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    director = object.__new__(EngagementDirector)
    director._current_dag = None

    async def unexpected_completion(**_kwargs):
        pytest.fail("LLM must not be called when no engagement DAG exists")

    monkeypatch.setattr(director, "_llm_complete", unexpected_completion)

    assert await director._evaluate_escalation("issue", "action") is None


def test_nullable_guard_lives_in_evaluator_itself() -> None:
    source = inspect.getsource(EngagementDirector._evaluate_escalation)
    assert "if self._current_dag is None:" in source
