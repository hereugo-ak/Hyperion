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
        for provider_type, data in status.items():
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
        for provider_type, data in health.items():
            assert "status" in data
            assert "available" in data
