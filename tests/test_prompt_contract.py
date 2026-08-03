"""Registry-level test for the shared agent prompt contract (W-16).

The contract only matters if it reaches the string actually sent to the LLM.
A static grep of ``prompt_contract.py`` proves nothing about composition, so
these tests exercise the real dispatch path in ``BaseAgent._llm_complete``
with a stub router and assert on the composed system prompt, for every
registered AgentSpec.

A future agent whose spec never routes through the base composition point
fails here, in CI, rather than silently shipping without the contract.
"""

from __future__ import annotations

import asyncio
from typing import Any

import hyperion.agents as agents_pkg
import pytest
from hyperion.agents.base import BaseAgent
from hyperion.agents.prompt_contract import (
    AGENT_CONTRACT,
    AGENT_CONTRACT_MARKER,
    AGENT_CONTRACT_VERSION,
)
from hyperion.schemas.agents import AgentSpec

# Every module-level *Spec object exported by the agents package: this is the
# registry the orchestrator dispatches from. Iterating the package namespace
# (rather than a hardcoded list) is what makes a newly added agent appear in
# this test automatically.
REGISTERED_SPECS: list[AgentSpec] = [
    obj
    for name, obj in vars(agents_pkg).items()
    if name.endswith("_SPEC") and isinstance(obj, AgentSpec)
]

CLAUSE_KEYWORDS = (
    "SUBJECT FIT",
    "ABSTAIN",
    "NO FABRICATION",
    "EVIDENCE BINDING",
    "UNITS AND DENOMINATION",
    "UNCERTAINTY",
    "CONFLICT",
    "TYPOGRAPHY",
    "DEPTH AND LENGTH",
)


class _StubResponse:
    """Minimal stand-in for RouterResponse; _llm_complete only reads attrs."""

    def __init__(self) -> None:
        self.model = "stub-model"
        self.provider = "stub-provider"
        self.content = "{}"
        self.input_tokens = 0
        self.output_tokens = 0
        self.latency_ms = 0.0
        self.success = True
        self.error = None


class _RecordingRouter:
    """Captures the messages list of every complete() call."""

    def __init__(self) -> None:
        self.calls: list[list[dict[str, Any]]] = []

    async def complete(self, *, messages, **kwargs):  # noqa: ANN001
        self.calls.append(messages)
        return _StubResponse()


class _ProbeAgent(BaseAgent):
    """Smallest concrete BaseAgent: run() is never called."""

    async def run(self, task):  # noqa: ANN001, ANN201
        return None


def _composed_prompt(spec: AgentSpec, override: str | None = None) -> str:
    router = _RecordingRouter()
    agent = _ProbeAgent(spec, bus=None, router=router)  # type: ignore[arg-type]
    asyncio.run(agent._llm_complete("ping", system_prompt_override=override))
    assert router.calls, "stub router never received a dispatch"
    return router.calls[-1][0]["content"]


def test_registry_is_complete() -> None:
    """The audit counted 20 system_prompt= sites; the registry must match."""
    assert len(REGISTERED_SPECS) == 20
    assert all(spec.system_prompt.strip() for spec in REGISTERED_SPECS)


def test_contract_text_is_dash_free_and_versioned() -> None:
    """The contract obeys its own clause 8 and carries a version marker."""
    assert "—" not in AGENT_CONTRACT
    assert "–" not in AGENT_CONTRACT
    assert AGENT_CONTRACT_VERSION >= 1
    assert f"v{AGENT_CONTRACT_VERSION}" in AGENT_CONTRACT_MARKER
    assert AGENT_CONTRACT_MARKER in AGENT_CONTRACT
    for keyword in CLAUSE_KEYWORDS:
        assert keyword in AGENT_CONTRACT, f"missing clause: {keyword}"


@pytest.mark.parametrize(
    "spec",
    REGISTERED_SPECS,
    ids=lambda spec: spec.name.value,
)
def test_contract_reaches_composed_prompt(spec: AgentSpec) -> None:
    """Marker and every clause header reach the dispatched prompt."""
    composed = _composed_prompt(spec)
    assert AGENT_CONTRACT_MARKER in composed
    for keyword in CLAUSE_KEYWORDS:
        assert keyword in composed, f"{spec.name.value}: missing {keyword}"
    # The agent's own prompt is still present: the contract is additive,
    # never a replacement for role-specific instruction.
    assert spec.system_prompt.splitlines()[0] in composed


@pytest.mark.parametrize(
    "spec",
    REGISTERED_SPECS,
    ids=lambda spec: spec.name.value,
)
def test_contract_reaches_prompt_overrides(spec: AgentSpec) -> None:
    """Overrides replace the role prompt, never the contract."""
    composed = _composed_prompt(spec, override="OVERRIDE ROLE PROMPT")
    assert "OVERRIDE ROLE PROMPT" in composed
    assert AGENT_CONTRACT_MARKER in composed


def test_depth_clause_sets_a_real_word_floor() -> None:
    """D-20: the live shared dispatch contract carries the depth budget."""
    assert "at least 450 words" in AGENT_CONTRACT
    for spec in REGISTERED_SPECS:
        composed = _composed_prompt(spec)
        assert "at least 450 words" in composed


def test_contract_is_prepended_once() -> None:
    """Guard against the double-prepend failure mode: the old typography
    rule must not be prepended separately alongside the contract."""
    spec = REGISTERED_SPECS[0]
    composed = _composed_prompt(spec)
    assert composed.count(AGENT_CONTRACT_MARKER) == 1
    assert composed.startswith(AGENT_CONTRACT_MARKER)
