"""P2-29 / P2-G29: a dead API key must surface as UNAUTHENTICATED at
startup, distinct from rate limiting, proven by one real completion per
configured provider, not a TCP probe or a key-presence check.

Context: the Google key that triggered Part 2 leaked, Google deleted it,
and for two entire engagements the operator saw quota-exhaustion language
while the actual failure was a hard 401. A TCP connect to the provider
host succeeds whether or not the key works; only a completion can detect
a dead key.
"""

from __future__ import annotations

import pytest

from hyperion.config import ModelTier, ProviderType
from hyperion.router.providers.base import ProviderStatus


@pytest.mark.asyncio
async def test_401_classified_as_auth_error_not_quota():
    """A 401/403 from a provider must record UNAUTHENTICATED, never a
    rate-limit cooldown. Auth and quota are different operator actions."""
    from hyperion.config import get_settings
    from hyperion.router.providers.google import GoogleProvider

    settings = get_settings()
    provider = GoogleProvider(settings.providers[ProviderType.GOOGLE])

    class FakeAuthError(Exception):
        pass

    async def fake_create(**kwargs):
        raise FakeAuthError("Error code: 401 - {'error': {'message': 'API key not valid'}}")

    completions = type("Comp", (), {"create": staticmethod(fake_create)})()
    chat = type("Chat", (), {"completions": completions})()
    provider._client = type("C", (), {"chat": chat})()

    resp = await provider.complete(
        model="gemini-2.5-flash",
        messages=[{"role": "user", "content": "ping"}],
        tier=ModelTier.FAST,
    )
    assert not resp.success
    assert provider.health.status == ProviderStatus.UNAUTHENTICATED
    # Crucially NOT treated as rate limiting
    assert provider.health.total_429s == 0
    assert provider.health.cooldown_until == 0.0


@pytest.mark.asyncio
async def test_preflight_issues_one_completion_per_provider(monkeypatch):
    """credential_preflight() must call every configured provider once and
    return a per-provider result distinguishing AUTH from QUOTA failures."""
    from hyperion.obs.health import credential_preflight

    calls: list[str] = []

    class FakeProvider:
        def __init__(self, name: str, behavior: str) -> None:
            from hyperion.router.providers.base import ProviderHealth

            self._name = name
            self._behavior = behavior
            self.health = ProviderHealth()

        async def complete(self, model, messages, tier, **kwargs):
            from hyperion.router.providers.base import RouterResponse

            calls.append(self._name)
            if self._behavior == "auth":
                return RouterResponse(
                    content="", model=model, provider=ProviderType.GOOGLE,
                    tier=tier, success=False,
                    error="Error code: 401 - API key not valid",
                )
            if self._behavior == "quota":
                return RouterResponse(
                    content="", model=model, provider=ProviderType.GROQ,
                    tier=tier, success=False,
                    error="Error code: 429 - rate_limit_exceeded",
                )
            return RouterResponse(
                content="ok", model=model, provider=ProviderType.MISTRAL,
                tier=tier, success=True,
            )

        def get_models_for_tier(self, tier):
            return [type("M", (), {"name": "m", "deprecated": False})()]

    fake = {
        ProviderType.GOOGLE: FakeProvider("google", "auth"),
        ProviderType.GROQ: FakeProvider("groq", "quota"),
        ProviderType.MISTRAL: FakeProvider("mistral", "ok"),
    }

    from hyperion.router.router import reset_router
    reset_router()
    from hyperion.router.router import LLMRouter

    router = LLMRouter()
    router._providers = fake

    results = await credential_preflight(router)

    assert set(calls) == {"google", "groq", "mistral"}
    assert results[ProviderType.GOOGLE] == "UNAUTHENTICATED"
    assert results[ProviderType.GROQ] == "QUOTA"
    assert results[ProviderType.MISTRAL] == "OK"
    # The dead key must also be stamped onto the provider health so the
    # router diagnoses it as unauthenticated for the rest of the process.
    assert router._providers[ProviderType.GOOGLE].health.status == ProviderStatus.UNAUTHENTICATED
    reset_router()
