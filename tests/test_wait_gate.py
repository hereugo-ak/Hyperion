"""
Tests for the HYPERION Wait Gate — sliding window, token estimation, budget.

Tests:
- SlidingWindowTracker token tracking
- WaitGate coordinator
- BudgetPlanner with 20% reserve
- Token estimation and calibration

Architecture reference: §3.3 Wait Gate
"""

import time

from hyperion.config import ModelSpec, ModelTier, ProviderType, WaitGateConfig


def _make_model(name: str = "test-model", tpm: int = 10000, rpm: int = 30, rpd: int | None = 1000) -> ModelSpec:
    """Create a minimal ModelSpec for testing."""
    return ModelSpec(
        name=name,
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=rpm,
        tpm=tpm,
        rpd=rpd,
        tier=ModelTier.STANDARD,
    )


class TestSlidingWindowTracker:
    """Test the sliding window token tracker."""

    def test_tracker_init(self):
        """SlidingWindowTracker should initialize with a model and window size."""
        from hyperion.router.wait_gate import SlidingWindowTracker

        model = _make_model()
        tracker = SlidingWindowTracker(model=model, window_seconds=60)
        assert tracker is not None
        assert tracker.window_seconds == 60

    def test_tracker_add_tokens(self):
        """Adding tokens should increase the current count."""
        from hyperion.router.wait_gate import SlidingWindowTracker

        model = _make_model()
        tracker = SlidingWindowTracker(model=model, window_seconds=60)
        tracker.record_request(estimated_tokens=1000)
        assert tracker.current_tpm() == 1000

    def test_tracker_multiple_additions(self):
        """Multiple additions should accumulate."""
        from hyperion.router.wait_gate import SlidingWindowTracker

        model = _make_model()
        tracker = SlidingWindowTracker(model=model, window_seconds=60)
        tracker.record_request(estimated_tokens=500)
        tracker.record_request(estimated_tokens=300)
        tracker.record_request(estimated_tokens=200)
        assert tracker.current_tpm() == 1000

    def test_tracker_window_expiry(self):
        """Tokens outside the window should expire."""
        from hyperion.router.wait_gate import SlidingWindowTracker

        model = _make_model()
        tracker = SlidingWindowTracker(model=model, window_seconds=0.1)  # 100ms window
        tracker.record_request(estimated_tokens=1000)
        time.sleep(0.15)  # Wait for window to expire
        assert tracker.current_tpm() == 0


class TestBudgetPlanner:
    """Test the budget planner with 20% reserve."""

    def test_budget_planner_init(self):
        """DailyBudgetPlanner should initialize."""
        from hyperion.router.budget import DailyBudgetPlanner

        planner = DailyBudgetPlanner(reserve_fraction=0.20)
        assert planner is not None

    def test_budget_reserve(self):
        """Budget planner should maintain 20% reserve."""
        from hyperion.config import get_settings

        settings = get_settings()
        # The reserve should be 0.20 (20%)
        assert settings.budget_reserve == 0.20


class TestWaitGate:
    """Test the WaitGate coordinator."""

    def test_wait_gate_init(self):
        """WaitGate should initialize with config and trackers."""
        from hyperion.router.wait_gate import WaitGate

        config = WaitGateConfig()
        gate = WaitGate(config=config, trackers={})
        assert gate is not None

    def test_wait_gate_tpm_percentage(self):
        """WaitGate should return TPM usage percentage for each provider."""
        from hyperion.router.wait_gate import WaitGate

        config = WaitGateConfig()
        gate = WaitGate(config=config, trackers={})

        for provider in ProviderType:
            pct = gate.get_tpm_usage_percentage(provider)
            assert isinstance(pct, dict)

