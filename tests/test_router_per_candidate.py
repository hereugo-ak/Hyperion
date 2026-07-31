"""T-10 (P2-30 / P2-G30) and T-10b (P2-31 / P2-G-part): router correctness.

P2-30 — one hot model must not disable an entire provider.

    Before the fix, ``_predicted_rate_limited(provider_type)`` answered "is
    *any* model on this provider hot" and was used to answer "should I skip
    this provider". Google runs Gemma at 14,400 RPD and Gemini at 500 RPD;
    saturating the small model marked the large one unavailable, and vice
    versa. The fix evaluates the prediction for the *specific candidate* the
    wait gate selects, and skips the candidate, not the provider.

P2-31 — MICRO tier has no provider priority, and the STRONG walk duplicates
    STANDARD.

    ``_TIER_PROVIDER_PRIORITY`` had no MICRO entry, so the highest-volume
    tier (every specialist sub-agent runs on MICRO) fell back to
    ``[] + list(set)`` — non-deterministic ordering. And
    ``_TIER_ADJACENCY[STRONG]`` already walks STANDARD, so the
    ``_TIER_DOWNGRADE[STRONG] -> STANDARD`` step attempted STANDARD a second
    time on every STRONG failure.
"""

from __future__ import annotations

import pytest

from hyperion.config import ModelTier, ProviderType
from hyperion.router.router import reset_router

MESSAGES = [
    {"role": "system", "content": "You are a test agent."},
    {"role": "user", "content": "Reply with the word ok."},
]


def _fresh_router():
    reset_router()
    from hyperion.router.router import LLMRouter

    return LLMRouter()


def _tracker(router, provider: ProviderType, tier: ModelTier, prefer: str | None = None):
    """Return the tracker for a provider's model serving ``tier``.

    When the provider has several models for the tier, ``prefer`` selects the
    one whose name contains the given substring (else the first).
    """
    matches = [
        (name, tr)
        for (pt, name), tr in router._trackers.items()
        if pt == provider and tr.model.tier == tier
    ]
    assert matches, f"no tracker for {provider} tier {tier}"
    if prefer is not None:
        matches = [m for m in matches if prefer in m[0]] or matches
    return matches[0][1]


def _saturate_rpm(tracker) -> None:
    """Drive a tracker's rolling RPM window to 100% of its limit."""
    assert tracker.model.rpm > 0
    for _ in range(tracker.model.rpm):
        tracker.record_request(100)
    assert tracker.rpm_available() < 1


class TestPerCandidateRateLimit:
    """P2-30: saturate one model; a *different* model on the same provider
    must still be served."""

    def test_candidate_level_predictor_exists(self):
        """The per-candidate predictor must exist (candidate, not provider)."""
        router = _fresh_router()
        assert hasattr(router, "_candidate_rate_limited"), (
            "router must expose a per-candidate _candidate_rate_limited() that "
            "checks the specific selected candidate, not the whole provider"
        )

    def test_saturated_model_skipped_not_provider(self):
        """Saturate Google's MICRO model; DEEP-tier Google must still serve.

        The unit-of-decision test: with the Gemma window at 100% RPM, the
        candidate the wait gate selects for DEEP must be Google's Gemini
        model (i.e. the provider was not disabled by its hot MICRO model).
        """
        router = _fresh_router()
        # Saturate the Google MICRO (Gemma) model.
        micro = _tracker(router, ProviderType.GOOGLE, ModelTier.MICRO)
        _saturate_rpm(micro)

        # Sanity: the provider-level aggregate predictor would flag Google here
        # (that is exactly the P2-30 defect we are removing). The candidate the
        # wait gate picks for DEEP must be a fresh model.
        candidate, _wait = router.wait_gate.select_with_wait(
            tier=ModelTier.DEEP,
            estimated_tokens=100,
            available_providers={ProviderType.GOOGLE},
        )
        assert candidate is not None, "Google must still have a DEEP candidate"
        assert candidate.provider_type is ProviderType.GOOGLE
        assert candidate.model.tier is ModelTier.DEEP
        assert candidate.can_serve_now, (
            "DEEP candidate (Gemini) must be servable even though MICRO "
            "(Gemma) on the same provider is saturated"
        )
        # The per-candidate predictor must not flag this fresh candidate.
        assert not router._candidate_rate_limited(candidate)


class TestTierProviderPriority:
    """P2-31: MICRO must have an explicit priority entry; ordering deterministic."""

    def test_micro_has_explicit_priority(self):
        from hyperion.router.router import _TIER_PROVIDER_PRIORITY

        assert ModelTier.MICRO in _TIER_PROVIDER_PRIORITY, (
            "MICRO (the highest-volume tier) must have an explicit provider "
            "priority entry; otherwise ordering falls back to set iteration"
        )
        assert _TIER_PROVIDER_PRIORITY[ModelTier.MICRO], (
            "MICRO priority list must be non-empty"
        )

    def test_micro_provider_ordering_deterministic(self):
        """Sorting MICRO providers twice must give the same deterministic order."""
        router = _fresh_router()
        providers = {ProviderType.GOOGLE, ProviderType.GROQ, ProviderType.MISTRAL}
        first = router._sort_providers_by_priority(ModelTier.MICRO, set(providers))
        second = router._sort_providers_by_priority(ModelTier.MICRO, set(providers))
        assert first == second
        # All priority-listed MICRO providers come before any remainder.
        assert len(first) == len(providers)


class TestStrongWalkDeduplication:
    """P2-31: STANDARD must not be attempted twice on a STRONG failure."""

    @pytest.mark.asyncio
    async def test_standard_tier_attempted_once(self, monkeypatch):
        router = _fresh_router()
        # Force every provider/tier to fail so the full walk executes.
        monkeypatch.setattr(
            router, "get_available_providers", lambda tier, urgency=None: set()
        )

        tried: list[ModelTier] = []
        original = router._try_tier

        async def recording_try_tier(tier, *args, **kwargs):
            tried.append(tier)
            return await original(tier, *args, **kwargs)

        monkeypatch.setattr(router, "_try_tier", recording_try_tier)

        response = await router.complete(ModelTier.STRONG, MESSAGES)
        assert not response.success
        assert tried.count(ModelTier.STANDARD) <= 1, (
            f"STANDARD attempted {tried.count(ModelTier.STANDARD)} times; the "
            "STRONG walk (adjacency + downgrade) must be deduplicated"
        )
        reset_router()
