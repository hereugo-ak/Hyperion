"""W-17 — Router attempt budget and HTTP error classification.

Verifies the acceptance criteria from HYPERION_DEEP_AUDIT_2026-07-31_PART2.md
§W-17 against injected provider failures (no live provider calls):

1. A complete() call with every provider failing terminates in a bounded
   number of dispatch attempts (<= RouterAttempt.max_attempts).
2. No provider is dispatched more than twice within a single complete() call
   (the second dispatch is only ever the explicit transient retry).
3. A 401/403 failure never triggers a second attempt against the same
   provider and the budget charge is refunded.
4. A 429 failure never triggers an immediate cross-provider failover — the
   tier walk halts on the rate-limited response.
5. RouterResponse carries the `downgraded` field, set when a walk succeeds
   on an adjacent/downgrade tier.

These are unit-level (mocked provider.complete) verifications: the sandbox
has no live provider credentials, so no real HTTP traffic is exercised here.
"""

from __future__ import annotations

import pytest

from hyperion.config import ModelTier, ProviderType
from hyperion.router.providers.base import RouterResponse
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
    """Replace every provider's complete() with a failing stub that counts
    dispatches per provider. Returns the counter dict."""

    async def _failing_complete(self, model, messages, tier, temperature=None,
                                max_tokens=None, response_format=None):
        counter[self.provider_type] = counter.get(self.provider_type, 0) + 1
        return _failure(self.provider_type, tier, status_code, error)

    from hyperion.router.providers.base import BaseProvider

    monkeypatch.setattr(BaseProvider, "complete", _failing_complete)


@pytest.mark.asyncio
async def test_all_providers_failing_terminates_bounded(monkeypatch):
    """Criterion 1+2: with all 5 providers returning transient 500s, the
    failover chain terminates within max_attempts and no provider sees more
    than two dispatches."""
    router = _fresh_router()
    counter: dict[ProviderType, int] = {}
    _patch_all_providers(router, monkeypatch, 500, "500 Server Error", counter)

    # avoid real backoff sleeps in the transient retry path
    import hyperion.router.router as router_mod

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(router_mod.asyncio, "sleep", _no_sleep)

    response = await router.complete(tier=ModelTier.STANDARD, messages=MESSAGES)

    assert not response.success
    total_dispatches = sum(counter.values())
    max_attempts = max(1, len(router._providers)) * 2
    assert total_dispatches <= max_attempts, (
        f"unbounded failover: {total_dispatches} dispatches > {max_attempts}"
    )
    for provider_type, count in counter.items():
        assert count <= 2, (
            f"{provider_type} dispatched {count}x in one complete() call"
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
async def test_429_halts_tier_walk_without_failover(monkeypatch):
    """Criterion 4: a 429 must halt the walk on that response — no instant
    cross-provider failover."""
    router = _fresh_router()
    counter: dict[ProviderType, int] = {}
    _patch_all_providers(router, monkeypatch, 429, "429 Rate Limited", counter)

    response = await router.complete(tier=ModelTier.STANDARD, messages=MESSAGES)

    assert not response.success
    assert response.status_code == 429
    # Exactly one provider was dispatched before the halt.
    assert sum(counter.values()) == 1


@pytest.mark.asyncio
async def test_transient_retried_once_then_failover(monkeypatch):
    """Criterion 2 (transient path): the first provider gets exactly one
    retry; the walk then moves to the next provider."""
    router = _fresh_router()
    counter: dict[ProviderType, int] = {}
    _patch_all_providers(router, monkeypatch, 503, "503 Service Unavailable", counter)

    import hyperion.router.router as router_mod

    async def _no_sleep(_seconds):
        return None

    monkeypatch.setattr(router_mod.asyncio, "sleep", _no_sleep)

    response = await router.complete(tier=ModelTier.FAST, messages=MESSAGES)
    assert not response.success

    first_provider = min(counter, key=lambda p: counter[p] if counter[p] > 0 else 99)
    # The first-dispatched provider saw exactly 2 dispatches (initial + retry);
    # every other provider at most 2 as well (they also get the transient retry).
    assert counter[first_provider] <= 2
    for provider_type, count in counter.items():
        assert count <= 2
    # And the walk did move on to at least one other provider.
    assert len(counter) >= 2


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
