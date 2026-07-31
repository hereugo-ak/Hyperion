"""T-09 (P2-29 / P2-G28): a total routing failure must never be attributed
to an innocent provider.

Before the fix, ``router.py`` returned ``provider=ProviderType.GOOGLE  #
Placeholder`` with ``model="none"`` for every "no candidate anywhere"
outcome, so a hard credential failure on one provider was reported to the
operator as quota exhaustion on a provider it had never successfully
contacted. The fix:

1. ``ProviderType.NONE`` exists and is used for unattributed failures.
2. The failure response carries a structured ``RouterFailure`` with
   ``tiers_attempted`` and per-provider ``skip_reasons``.
"""

from __future__ import annotations

import pytest

from hyperion.config import ModelTier, ProviderType
from hyperion.router.budget import TaskUrgency
from hyperion.router.router import reset_router

MESSAGES = [
    {"role": "system", "content": "You are a test agent."},
    {"role": "user", "content": "Reply with the word ok."},
]


@pytest.fixture()
def dead_router(monkeypatch):
    """A router whose every provider is unavailable at every layer."""
    reset_router()
    from hyperion.router.router import LLMRouter

    router = LLMRouter()

    # Every provider unhealthy: health.is_available() -> False
    for provider in router._providers.values():
        provider.health.trip_circuit_breaker(cooldown_seconds=3600)

    # And belt-and-braces: no model candidates anywhere
    monkeypatch.setattr(router, "get_available_providers", lambda tier, urgency=TaskUrgency.NORMAL: set())
    yield router
    reset_router()


@pytest.mark.asyncio
async def test_provider_type_none_exists():
    """ProviderType.NONE must exist so a failure can name nobody."""
    assert ProviderType.NONE.value == "none"


@pytest.mark.asyncio
async def test_router_failure_names_no_provider(dead_router):
    """With all providers down, the failure must not name a real provider."""
    r = await dead_router.complete(ModelTier.STRONG, MESSAGES)
    assert not r.success
    assert r.provider is ProviderType.NONE
    assert r.failure is not None
    assert r.failure.skip_reasons  # per-provider, non-empty
    assert r.failure.tiers_attempted  # at least the requested tier was walked
    assert ModelTier.STRONG in r.failure.tiers_attempted


@pytest.mark.asyncio
async def test_router_failure_records_health_skip_reasons(monkeypatch):
    """When providers are circuit-open, skip_reasons must say so."""
    reset_router()
    from hyperion.router.router import LLMRouter

    router = LLMRouter()
    for provider in router._providers.values():
        provider.health.trip_circuit_breaker(cooldown_seconds=3600)

    r = await router.complete(ModelTier.FAST, MESSAGES)
    assert not r.success
    assert r.provider is ProviderType.NONE
    assert r.failure is not None
    # Every configured provider must have a reason recorded
    for pt in (ProviderType.GOOGLE, ProviderType.NVIDIA, ProviderType.CEREBRAS,
               ProviderType.GROQ, ProviderType.MISTRAL):
        assert pt in r.failure.skip_reasons, f"missing skip reason for {pt}"
        assert r.failure.skip_reasons[pt] == "health_open"
    reset_router()


@pytest.mark.asyncio
async def test_router_failure_no_model_for_tier(monkeypatch):
    """A healthy provider with no model for the tier is reported as such."""
    reset_router()
    from hyperion.router.router import LLMRouter

    router = LLMRouter()
    monkeypatch.setattr(
        router, "get_available_providers", lambda tier, urgency=TaskUrgency.NORMAL: set()
    )
    r = await router.complete(ModelTier.DEEP, MESSAGES)
    assert not r.success
    assert r.provider is ProviderType.NONE
    assert r.failure is not None and r.failure.skip_reasons
    reset_router()
