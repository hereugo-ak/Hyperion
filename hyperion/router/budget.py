"""
HYPERION Daily Budget Planner — tracks RPD across all models per provider.

This is NOT a generic budget tracker. This is the system that ensures we
never exhaust a provider's daily quota before the engagement is complete.
It allocates requests based on urgency, preserves a 20% reserve on every
provider for critical end-of-engagement tasks, and tracks daily consumption
in real-time. (§3.5)

Provider daily budgets (total RPD across all models):
- Google: ~29,460 RPD (Gemma 14.4K + Gemma 14.4K + Gemini 500 + reserves)
- Groq: ~18,400 RPD (6 models x ~1K-14.4K each)
- NVIDIA: ~1,000 credits/month -> ~33/day (scarce — reserve for STRONG/DEEP)
- Cerebras: 1M TPD per model -> effectively unlimited by tokens, but 5 RPM

Allocation strategy:
- High urgency (quality gate, synthesis): use high-RPD providers first
  (Google Gemma, Groq Llama 3.1 8B) to preserve NVIDIA credits
- Normal (research, analysis): balanced selection across all providers
- Low (background tasks): use NVIDIA sparingly, prefer Google/Groq
- 20% reserve: preserved on every provider for critical end-of-engagement
  tasks (Quality Gate scoring, Synthesis Lead reconciliation, final render)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum

from hyperion.config import ModelSpec, ModelTier, ProviderType


class TaskUrgency(str, Enum):
    """Urgency level for budget allocation (§3.5).

    Determines which providers are preferred for the request:
    - HIGH: quality gate, synthesis, final render — use high-RPD providers
      first to preserve scarce NVIDIA credits
    - NORMAL: research, analysis — balanced selection
    - LOW: background tasks, keyword expansion — use NVIDIA sparingly
    """

    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


# Provider daily budget estimates (§3.5)
# These are approximate total RPD across all models per provider
_PROVIDER_DAILY_BUDGETS: dict[ProviderType, int] = {
    ProviderType.GOOGLE: 29_460,
    ProviderType.GROQ: 18_400,
    ProviderType.NVIDIA: 33,  # ~1,000 credits/month -> ~33/day — scarce
    ProviderType.CEREBRAS: 10_000,  # Effectively unlimited by RPD (TPD-limited)
    ProviderType.MISTRAL: 86_400,  # ~60 RPM * 1440 min — most abundant (~1B tokens/month)
}

# Scarcity ranking — most scarce providers should be preserved for high-urgency
_PROVIDER_SCARCITY: dict[ProviderType, int] = {
    ProviderType.NVIDIA: 0,     # Most scarce — 33/day
    ProviderType.CEREBRAS: 1,   # 5 RPM limit, but high TPD
    ProviderType.GROQ: 2,       # 18.4K RPD
    ProviderType.GOOGLE: 3,     # 29.4K RPD — most abundant
    ProviderType.MISTRAL: 4,    # ~86.4K RPD — most abundant, ~1B tokens/month
}


@dataclass
class ProviderBudget:
    """Daily budget tracking for a single provider.

    Tracks total RPD consumed across all models on this provider and
    enforces the 20% reserve for critical end-of-engagement tasks.
    """

    provider: ProviderType
    total_budget: int
    consumed: int = 0
    reserve_fraction: float = 0.20

    # Per-model consumption
    model_consumption: dict[str, int] = field(default_factory=dict)

    _reset_day: int = 0

    def __post_init__(self) -> None:
        self._reset_day = time.gmtime().tm_yday

    def _check_reset(self) -> None:
        """Reset daily counters at UTC midnight."""
        current_day = time.gmtime().tm_yday
        if current_day != self._reset_day:
            self.consumed = 0
            self.model_consumption.clear()
            self._reset_day = current_day

    @property
    def available(self) -> int:
        """Available RPD (total minus consumed, minus reserve)."""
        self._check_reset()
        reserved = int(self.total_budget * self.reserve_fraction)
        return max(0, self.total_budget - self.consumed - reserved)

    @property
    def available_with_reserve(self) -> int:
        """Available RPD including reserve — only for critical tasks."""
        self._check_reset()
        return max(0, self.total_budget - self.consumed)

    @property
    def usage_percentage(self) -> float:
        """Current usage as a fraction of total budget (0.0 to 1.0)."""
        self._check_reset()
        if self.total_budget == 0:
            return 0.0
        return self.consumed / self.total_budget

    @property
    def is_reserve_available(self) -> bool:
        """Check if we're into the reserve zone."""
        self._check_reset()
        reserved = int(self.total_budget * self.reserve_fraction)
        return (self.total_budget - self.consumed) > reserved

    def can_consume(self, urgency: TaskUrgency = TaskUrgency.NORMAL) -> bool:
        """Check if this provider has budget for another request.

        HIGH urgency tasks can dip into the reserve.
        NORMAL and LOW urgency tasks cannot.
        """
        self._check_reset()
        if urgency == TaskUrgency.HIGH:
            return self.available_with_reserve > 0
        return self.available > 0

    def consume(self, model_name: str, count: int = 1) -> None:
        """Record consumption of count requests on a model."""
        self._check_reset()
        self.consumed += count
        self.model_consumption[model_name] = (
            self.model_consumption.get(model_name, 0) + count
        )

    def remaining_for_model(self, model: ModelSpec) -> int | None:
        """Estimate remaining RPD for a specific model.

        If the model has its own RPD limit, use that. Otherwise, use
        the provider's total budget.
        """
        self._check_reset()
        if model.rpd is not None:
            consumed_for_model = self.model_consumption.get(model.name, 0)
            return max(0, model.rpd - consumed_for_model)
        return self.available


class DailyBudgetPlanner:
    """Daily budget planner — tracks RPD across all providers and ensures
    the 20% reserve is preserved for critical end-of-engagement tasks.

    The planner is consulted by the router before dispatching a request.
    It filters out providers that have exhausted their daily budget (or
    are in the reserve zone for non-critical tasks).

    Allocation strategy (§3.5):
    - HIGH urgency: prefer abundant providers (Google, Groq) to preserve
      scarce NVIDIA credits for the most critical tasks
    - NORMAL: balanced — all providers with available budget are eligible
    - LOW: prefer abundant providers, use NVIDIA sparingly
    """

    def __init__(self, reserve_fraction: float = 0.20) -> None:
        self._budgets: dict[ProviderType, ProviderBudget] = {}
        for provider, budget in _PROVIDER_DAILY_BUDGETS.items():
            self._budgets[provider] = ProviderBudget(
                provider=provider,
                total_budget=budget,
                reserve_fraction=reserve_fraction,
            )

    def get_budget(self, provider: ProviderType) -> ProviderBudget:
        """Get the budget tracker for a provider."""
        return self._budgets[provider]

    def can_serve(
        self,
        provider: ProviderType,
        model: ModelSpec,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
    ) -> bool:
        """Check if a provider has budget for a request on a specific model.

        Checks both the provider-level budget and the model-level RPD limit.
        """
        budget = self._budgets[provider]

        # Check provider-level budget
        if not budget.can_consume(urgency):
            return False

        # Check model-level RPD limit
        model_remaining = budget.remaining_for_model(model)
        return model_remaining is None or model_remaining >= 1

    def consume(
        self,
        provider: ProviderType,
        model_name: str,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
    ) -> None:
        """Record that a request has been dispatched to a provider+model."""
        self._budgets[provider].consume(model_name)

    def filter_available_providers(
        self,
        tier: ModelTier,
        models_by_provider: dict[ProviderType, list[ModelSpec]],
        urgency: TaskUrgency = TaskUrgency.NORMAL,
    ) -> set[ProviderType]:
        """Filter to providers that have budget for the given tier and urgency.

        Returns a set of provider types that:
        1. Have at least one non-deprecated model for the tier
        2. Have remaining daily budget (respecting reserve for non-HIGH urgency)
        3. The specific model has remaining RPD (if applicable)
        """
        available: set[ProviderType] = set()

        for provider, models in models_by_provider.items():
            tier_models = [m for m in models if m.tier == tier and not m.deprecated]
            if not tier_models:
                continue

            for model in tier_models:
                if self.can_serve(provider, model, urgency):
                    available.add(provider)
                    break

        return available

    def get_usage_summary(self) -> dict[ProviderType, dict[str, float]]:
        """Get a usage summary for TUI display (§8.6).

        Returns {provider: {usage_pct, available, total, in_reserve}}.
        """
        summary: dict[ProviderType, dict[str, float]] = {}
        for provider, budget in self._budgets.items():
            summary[provider] = {
                "usage_pct": budget.usage_percentage,
                "available": float(budget.available),
                "total": float(budget.total_budget),
                "in_reserve": not budget.is_reserve_available,
            }
        return summary

    def get_priority_order(
        self,
        urgency: TaskUrgency,
        available: set[ProviderType],
    ) -> list[ProviderType]:
        """Get the priority order for provider selection based on urgency.

        HIGH urgency: prefer abundant providers (Google, Groq) to preserve
          scarce NVIDIA credits for the most critical tasks.
        NORMAL: balanced — order by remaining capacity.
        LOW: prefer abundant providers, use NVIDIA sparingly.
        """
        if urgency == TaskUrgency.HIGH:
            # Prefer abundant providers — reverse scarcity (most abundant first)
            return sorted(
                available,
                key=lambda p: _PROVIDER_SCARCITY.get(p, 99),
                reverse=True,
            )
        elif urgency == TaskUrgency.LOW:
            # Same as HIGH — preserve scarce providers
            return sorted(
                available,
                key=lambda p: _PROVIDER_SCARCITY.get(p, 99),
                reverse=True,
            )
        else:
            # NORMAL — order by remaining capacity (most available first)
            return sorted(
                available,
                key=lambda p: self._budgets[p].available,
                reverse=True,
            )
