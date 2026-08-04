"""W-17 — Router attempt budget and HTTP error classification.

Verifies the acceptance criteria from HYPERION_DEEP_AUDIT_2026-07-31_PART2.md
§W-17 against injected provider failures (no live provider calls):

1. A complete() call with every provider failing terminates in a bounded
   number of dispatch attempts (<= RouterAttempt.max_attempts).
2. No provider/model candidate is dispatched more than twice within one
   complete() call (the second dispatch is the explicit transient retry).
3. A 401/403 failure never triggers a second attempt against the same
   provider and the budget charge is refunded.
4. A 429 cools the exact model and fails over to an independent provider
   instead of disabling the whole provider fleet.
5. RouterResponse carries the `downgraded` field, set when a walk succeeds
   on an adjacent/downgrade tier.

These are unit-level (mocked provider.complete) verifications: the sandbox
has no live provider credentials, so no real HTTP traffic is exercised here.
"""

from __future__ import annotations

import pytest

from hyperion.config import ModelTier, ProviderType
from hyperion.router.budget import TaskUrgency
from hyperion.router.providers.base import (
    ProviderHealth,
    ProviderStatus,
    RouterResponse,
    is_transient_connection_error,
)
from hyperion.router.router import RouterAttempt, reset_router

MESSAGES = [
    {"role": "system", "content": "You are a test agent."},
    {"role": "user", "content": "Reply with the word ok."},
]


def _fresh_router():
    reset_router()
    from hyperion.router.router import LLMRouter

    return LLMRouter()


def _failure(provider: ProviderType, tier: ModelTier, status_code: int | None, error: str):
    return RouterResponse(
        content="",
        model="mock-model",
        provider=provider,
        tier=tier,
        success=False,
        error=error,
        status_code=status_code,
    )


def _patch_all_providers(router, monkeypatch, status_code, error, counter):
    """Replace provider completion with a candidate-scoped failing stub."""

    async def _failing_complete(self, model, messages, tier, temperature=None,
                                max_tokens=None, response_format=None):
        key = (self.provider_type, model)
        counter[key] = counter.get(key, 0) + 1
        return _failure(self.provider_type, tier, status_code, error)

    from hyperion.router.providers.base import BaseProvider

    monkeypatch.setattr(BaseProvider, "complete", _failing_complete)


@pytest.mark.asyncio
async def test_all_providers_failing_terminates_bounded(monkeypatch):
    """Criterion 1+2: with all 5 providers returning transient 500s, the
    failover chain terminates within max_attempts and no provider sees more
    than two dispatches."""
    router = _fresh_router()
    counter: dict[tuple[ProviderType, str], int] = {}
    _patch_all_providers(router, monkeypatch, 500, "500 Server Error", counter)

    # avoid real backoff sleeps in the transient retry path
    import hyperion.router.router as router_mod

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(router_mod.asyncio, "sleep", _no_sleep)

    response = await router.complete(tier=ModelTier.STANDARD, messages=MESSAGES)

    assert not response.success
    total_dispatches = sum(counter.values())
    max_attempts = max(1, len(router._trackers)) * 2
    assert total_dispatches <= max_attempts, (
        f"unbounded failover: {total_dispatches} dispatches > {max_attempts}"
    )
    for candidate, count in counter.items():
        assert count <= 2, (
            f"{candidate} dispatched {count}x in one complete() call"
        )


@pytest.mark.asyncio
async def test_auth_failure_never_retried_and_budget_refunded(monkeypatch):
    """Criterion 3: a 401 on the first provider must not re-dispatch that
    provider, and its budget charge must be refunded."""
    router = _fresh_router()
    counter: dict[ProviderType, int] = {}

    async def _auth_then_fail(self, model, messages, tier, temperature=None,
                              max_tokens=None, response_format=None):
        counter[self.provider_type] = counter.get(self.provider_type, 0) + 1
        if self.provider_type == ProviderType.NVIDIA:
            return _failure(self.provider_type, tier, 401, "401 Invalid API Key")
        return _failure(self.provider_type, tier, 500, "500 Server Error")

    from hyperion.router.providers.base import BaseProvider

    monkeypatch.setattr(BaseProvider, "complete", _auth_then_fail)

    import hyperion.router.router as router_mod

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(router_mod.asyncio, "sleep", _no_sleep)

    nvidia_budget = router.budget_planner._budgets[ProviderType.NVIDIA]
    before = nvidia_budget.consumed

    response = await router.complete(tier=ModelTier.STRONG, messages=MESSAGES)
    assert not response.success

    # NVIDIA failed auth on its first dispatch — it must never be retried.
    assert counter.get(ProviderType.NVIDIA, 0) <= 1
    # The 401 charge must have been refunded (net zero for NVIDIA auth dispatches).
    assert nvidia_budget.consumed == before


@pytest.mark.asyncio
async def test_429_cools_model_and_fails_over_to_independent_provider(monkeypatch):
    """Criterion 4: one model's 429 must not strand other providers."""
    router = _fresh_router()
    counter: dict[ProviderType, int] = {}
    first_provider: list[ProviderType] = []

    async def _first_rate_limited_then_success(
        self, model, messages, tier, temperature=None, max_tokens=None,
        response_format=None,
    ):
        counter[self.provider_type] = counter.get(self.provider_type, 0) + 1
        if not first_provider:
            first_provider.append(self.provider_type)
            return _failure(self.provider_type, tier, 429, "429 Rate Limited")
        return RouterResponse(
            content="ok",
            model=model,
            provider=self.provider_type,
            tier=tier,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )

    from hyperion.router.providers.base import BaseProvider

    monkeypatch.setattr(BaseProvider, "complete", _first_rate_limited_then_success)
    response = await router.complete(tier=ModelTier.STANDARD, messages=MESSAGES)

    assert response.success
    assert len(counter) == 2
    assert counter[first_provider[0]] == 1
    cooled = [
        tracker
        for (provider, _), tracker in router._trackers.items()
        if provider == first_provider[0] and tracker.in_cooldown()
    ]
    assert len(cooled) == 1


@pytest.mark.asyncio
async def test_transient_retried_once_then_failover(monkeypatch):
    """Criterion 2 (transient path): the first provider gets exactly one
    retry; the walk then moves to the next provider."""
    router = _fresh_router()
    counter: dict[tuple[ProviderType, str], int] = {}
    _patch_all_providers(router, monkeypatch, 503, "503 Service Unavailable", counter)

    import hyperion.router.router as router_mod

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(router_mod.asyncio, "sleep", _no_sleep)

    response = await router.complete(tier=ModelTier.FAST, messages=MESSAGES)
    assert not response.success

    # Every model sees at most its initial dispatch plus one transient retry.
    assert counter
    for count in counter.values():
        assert count <= 2
    # The walk moved beyond the first failed candidate.
    assert len(counter) >= 2


@pytest.mark.asyncio
async def test_connection_error_retried_once_then_recovers(monkeypatch):
    """A status-less SDK connection failure is transient, not provider death."""
    router = _fresh_router()
    calls: list[ProviderType] = []

    async def _connection_then_success(
        self, model, messages, tier, temperature=None, max_tokens=None,
        response_format=None,
    ):
        calls.append(self.provider_type)
        if len(calls) == 1:
            return _failure(self.provider_type, tier, None, "Connection error.")
        return RouterResponse(
            content="ok",
            model=model,
            provider=self.provider_type,
            tier=tier,
            input_tokens=4,
            output_tokens=1,
            total_tokens=5,
        )

    import hyperion.router.router as router_mod
    from hyperion.router.providers.base import BaseProvider

    monkeypatch.setattr(BaseProvider, "complete", _connection_then_success)

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(router_mod.asyncio, "sleep", _no_sleep)
    response = await router.complete(tier=ModelTier.FAST, messages=MESSAGES)

    assert response.success
    assert len(calls) == 2
    assert calls[0] == calls[1]
    assert is_transient_connection_error("Temporary failure in name resolution")
    assert is_transient_connection_error("Name or service not known")


@pytest.mark.asyncio
async def test_model_rejection_fails_over_to_sibling_model(monkeypatch):
    """A stale model ID must not exclude valid siblings on one provider."""
    router = _fresh_router()
    calls: list[str] = []

    monkeypatch.setattr(
        router,
        "get_available_providers",
        lambda *_args, **_kwargs: {ProviderType.GROQ},
    )
    monkeypatch.setattr(router.budget_planner, "reserve", lambda **_kwargs: True)
    monkeypatch.setattr(
        router.budget_planner,
        "release_reservation",
        lambda **_kwargs: None,
    )

    async def _reject_then_succeed(
        self, model, messages, tier, temperature=None, max_tokens=None,
        response_format=None,
    ):
        calls.append(model)
        if len(calls) == 1:
            return _failure(self.provider_type, tier, 404, "model does not exist")
        return RouterResponse(
            content="ok",
            model=model,
            provider=self.provider_type,
            tier=tier,
            input_tokens=4,
            output_tokens=1,
            total_tokens=5,
        )

    from hyperion.router.providers.base import BaseProvider

    monkeypatch.setattr(BaseProvider, "complete", _reject_then_succeed)
    response = await router._try_tier(
        tier=ModelTier.STANDARD,
        messages=MESSAGES,
        estimated_tokens=10,
        agent_name="test",
        urgency=TaskUrgency.NORMAL,
        temperature=0.0,
        max_tokens=None,
        response_format=None,
    )

    assert response is not None and response.success
    assert len(calls) == 2
    assert calls[0] != calls[1]


def test_request_error_does_not_poison_provider_health():
    health = ProviderHealth()

    for _ in range(5):
        health.record_request_error(404)

    assert health.status == ProviderStatus.HEALTHY
    assert health.consecutive_failures == 0
    assert health.is_available()
    assert health.total_errors == 5


def test_single_network_error_is_recoverable_and_circuit_is_timed():
    health = ProviderHealth()

    health.record_network_error()
    assert health.status == ProviderStatus.DEGRADED
    assert health.is_available()

    health.record_network_error()
    health.record_network_error()
    assert health.status == ProviderStatus.CIRCUIT_OPEN
    assert not health.is_available()

    health.cooldown_until = 0
    assert health.is_available()
    assert health.status == ProviderStatus.HEALTHY


@pytest.mark.asyncio
async def test_downgraded_field_present_and_set(monkeypatch):
    """Criterion 5: RouterResponse.downgraded exists and is set when the walk
    succeeds on a tier other than the requested one."""
    router = _fresh_router()

    async def _fail_standard_only(self, model, messages, tier, temperature=None,
                                  max_tokens=None, response_format=None):
        if tier == ModelTier.STANDARD:
            return _failure(self.provider_type, tier, 500, "500 Server Error")
        return RouterResponse(
            content="ok",
            model=model,
            provider=self.provider_type,
            tier=tier,
            input_tokens=10,
            output_tokens=5,
            total_tokens=15,
        )

    from hyperion.router.providers.base import BaseProvider

    monkeypatch.setattr(BaseProvider, "complete", _fail_standard_only)

    import hyperion.router.router as router_mod

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(router_mod.asyncio, "sleep", _no_sleep)

    response = await router.complete(tier=ModelTier.STANDARD, messages=MESSAGES)

    assert response.success
    assert hasattr(response, "downgraded")
    assert response.downgraded is True


def test_router_attempt_exhaustion_ceiling():
    """RouterAttempt.exhausted() enforces the hard ceiling."""
    attempt = RouterAttempt(max_attempts=3)
    assert not attempt.exhausted()
    attempt.attempts = 3
    assert attempt.exhausted()
    attempt.attempts = 4
    assert attempt.exhausted()
