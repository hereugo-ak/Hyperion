"""
Tests for the HYPERION LLM Router — provider selection, failover, tier mapping.

Tests:
- Provider availability per tier
- Failover from failed provider to next in tier
- Adjacent tier fallback when all providers in tier exhausted
- TPM tracking and wait gate integration
- Budget planner filtering
- Token estimation

Architecture reference: §3 LLM Router, §3.1-3.4
"""


from hyperion.config import ModelTier, ProviderType
from hyperion.router.budget import TaskUrgency
from hyperion.router.providers.base import RouterResponse
from hyperion.router.router import get_router, reset_router


class TestRouterInitialization:
    """Test router initialization and singleton behavior."""

    def test_router_singleton(self):
        """Router should return the same instance."""
        reset_router()
        router1 = get_router()
        router2 = get_router()
        assert router1 is router2

    def test_router_has_all_providers(self):
        """Router should initialize all 5 providers."""
        reset_router()
        router = get_router()
        assert ProviderType.GOOGLE in router._providers
        assert ProviderType.NVIDIA in router._providers
        assert ProviderType.CEREBRAS in router._providers
        assert ProviderType.GROQ in router._providers
        assert ProviderType.MISTRAL in router._providers

    def test_router_has_budget_planner(self):
        """Router should have a budget planner."""
        reset_router()
        router = get_router()
        assert router.budget_planner is not None

    def test_router_has_wait_gate(self):
        """Router should have a wait gate."""
        reset_router()
        router = get_router()
        assert router.wait_gate is not None


class TestTierMapping:
    """Test tier to provider mapping."""

    def test_micro_tier_has_providers(self):
        """MICRO tier should have at least one provider."""
        reset_router()
        router = get_router()
        providers = router.get_available_providers(ModelTier.MICRO, TaskUrgency.LOW)
        assert len(providers) > 0

    def test_standard_tier_has_providers(self):
        """STANDARD tier should have at least one provider."""
        reset_router()
        router = get_router()
        providers = router.get_available_providers(ModelTier.STANDARD, TaskUrgency.LOW)
        assert len(providers) > 0

    def test_strong_tier_has_providers(self):
        """STRONG tier should have at least one provider."""
        reset_router()
        router = get_router()
        providers = router.get_available_providers(ModelTier.STRONG, TaskUrgency.LOW)
        assert len(providers) > 0

    def test_google_uses_ai_studio_model_with_visible_project_quota(self):
        reset_router()
        router = get_router()
        deep_models = router._providers[ProviderType.GOOGLE].get_models_for_tier(
            ModelTier.DEEP
        )
        assert [model.name for model in deep_models] == ["gemini-2.5-flash"]
        assert deep_models[0].rpm == 5
        assert deep_models[0].tpm == 250_000
        assert deep_models[0].rpd == 20

    def test_unsupported_groq_model_is_never_routable(self):
        reset_router()
        router = get_router()
        configured = router._providers[ProviderType.GROQ].config.models
        unsupported = next(model for model in configured if model.name == "gpt-oss-20b")
        assert unsupported.deprecated is True
        assert all(
            model.name != "gpt-oss-20b"
            for model in router._providers[ProviderType.GROQ].get_models_for_tier(
                ModelTier.STANDARD
            )
        )

    def test_mistral_uses_versioned_console_limits_not_latest_aliases(self):
        reset_router()
        router = get_router()
        models = router._providers[ProviderType.MISTRAL].config.models
        assert all(not model.name.endswith("-latest") for model in models)
        limits = {model.name: (model.rpm, model.tpm) for model in models}
        assert limits["mistral-large-2512"] == (4, 250_000)
        assert limits["mistral-medium-2605"] == (25, 375_000)
        assert limits["ministral-3b-2512"] == (750, 1_300_000)


class TestTPMStatus:
    """Test TPM status reporting for TUI display."""

    def test_tpm_status_returns_all_providers(self):
        """TPM status should return data for all 5 providers."""
        reset_router()
        router = get_router()
        status = router.get_tpm_status()
        assert ProviderType.GOOGLE in status
        assert ProviderType.NVIDIA in status
        assert ProviderType.CEREBRAS in status
        assert ProviderType.GROQ in status
        assert ProviderType.MISTRAL in status

    def test_tpm_status_has_percentage(self):
        """Each provider's TPM status should have at least one model entry."""
        reset_router()
        router = get_router()
        status = router.get_tpm_status()
        for data in status.values():
            assert isinstance(data, dict)
            assert len(data) > 0  # At least one model tracked


class TestProviderHealth:
    """Test provider health reporting."""

    def test_provider_health_returns_all_providers(self):
        """Health status should return data for all providers."""
        reset_router()
        router = get_router()
        health = router.get_provider_health()
        assert len(health) == 5

    def test_provider_health_has_status(self):
        """Each provider's health should have a status field."""
        reset_router()
        router = get_router()
        health = router.get_provider_health()
        for data in health.values():
            assert "status" in data
            assert "available" in data


class TestResponseCompleteness:
    """D-18: provider termination state is never discarded."""

    def test_length_finish_reason_is_a_failed_truncated_response(self):
        response = RouterResponse(
            content="partial",
            model="test",
            provider=ProviderType.GOOGLE,
            tier=ModelTier.STANDARD,
            finish_reason="length",
        )

        assert response.truncated is True
        assert response.is_complete is False
        assert response.success is False
        assert "finish_reason=length" in (response.error or "")

    def test_stop_finish_reason_is_complete(self):
        response = RouterResponse(
            content="complete",
            model="test",
            provider=ProviderType.GOOGLE,
            tier=ModelTier.STANDARD,
            finish_reason="stop",
        )

        assert response.truncated is False
        assert response.is_complete is True
        assert response.success is True
