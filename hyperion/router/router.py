"""
HYPERION LLMRouter — the async, TPM-aware, singleton routing layer.

This is NOT a generic LLM client wrapper. This is the system that:
1. Receives a tier request from an agent (agents don't know providers)
2. Estimates token consumption via the TokenEstimator
3. Consults the DailyBudgetPlanner for provider availability
4. Consults the WaitGate for the best provider+model candidate
5. Dispatches the request via the provider's async OpenAI client
6. Records actual token usage for calibration
7. Handles failover per §3.6

The router is a singleton — one instance per process. It holds all
provider instances, the wait gate, the budget planner, and the estimator.
Agents call router.complete() with a tier and messages; the router handles
everything else. (§3.1–§3.6)

Architecture (§3.2):
    Agent → Router.complete(tier, messages)
              → Estimator.estimate_tokens()
              → BudgetPlanner.filter_available_providers()
              → WaitGate.select_provider()
              → Provider.complete()
              → Record actual usage
              → Return RouterResponse

If the selected provider fails, the router failovers to the next candidate
in the same tier. If all providers in the tier fail, it tries the adjacent
tier (up or down based on task urgency).
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from hyperion.config import (
    ModelSpec,
    ModelTier,
    ProviderConfig,
    ProviderType,
    Settings,
    get_settings,
)
from hyperion.router.budget import DailyBudgetPlanner, TaskUrgency
from hyperion.router.estimator import TokenEstimator
from hyperion.router.providers.base import BaseProvider, RouterResponse
from hyperion.router.providers.cerebras import CerebrasProvider
from hyperion.router.providers.google import GoogleProvider
from hyperion.router.providers.groq import GroqProvider
from hyperion.router.providers.mistral import MistralProvider
from hyperion.router.providers.nvidia import NvidiaProvider
from hyperion.router.semantic_cache import ResponseCache
from hyperion.router.speculative_racer import SpeculativeRacer
from hyperion.router.structured_validator import StructuredValidator
from hyperion.router.wait_gate import ProviderCandidate, SlidingWindowTracker, WaitGate
from hyperion.obs import trace


# Tier adjacency for fallback (§3.3: "> 30s wait: try adjacent tier")
# When a tier is exhausted, try the next tier up (more capable) or down (less capable)
_TIER_ADJACENCY: dict[ModelTier, list[ModelTier]] = {
    ModelTier.MICRO: [ModelTier.FAST, ModelTier.STANDARD],
    ModelTier.FAST: [ModelTier.MICRO, ModelTier.STANDARD],
    ModelTier.STANDARD: [ModelTier.STRONG, ModelTier.FAST],
    ModelTier.STRONG: [ModelTier.DEEP, ModelTier.STANDARD],
    ModelTier.DEEP: [ModelTier.STRONG],
}

# D9: Explicit tier downgrade — when ALL providers in a tier fail, degrade
# to the next lower tier rather than giving up.
_TIER_DOWNGRADE: dict[ModelTier, ModelTier] = {
    ModelTier.DEEP: ModelTier.STRONG,
    ModelTier.STRONG: ModelTier.STANDARD,
}

# D19/D20/D21: Provider priority per tier — controls which provider is tried
# first within a tier. Providers not listed are appended in arbitrary order.
# Rationale:
#   FAST: Mistral primary (rpm60), Groq secondary (rpm30), Cerebras overflow (rpm5)
#   STANDARD: NVIDIA/Mistral primary (high TPM), Groq burst-only (tpm8k)
#   DEEP: Google flash-lite, Mistral devstral, NVIDIA ultra-550b (round-robin, ≥2 non-Google)
#   STRONG: NVIDIA nemotron, Mistral large
_TIER_PROVIDER_PRIORITY: dict[ModelTier, list[ProviderType]] = {
    ModelTier.FAST: [ProviderType.MISTRAL, ProviderType.GROQ, ProviderType.CEREBRAS],
    ModelTier.STANDARD: [ProviderType.NVIDIA, ProviderType.MISTRAL, ProviderType.GROQ],
    ModelTier.DEEP: [ProviderType.MISTRAL, ProviderType.NVIDIA, ProviderType.GOOGLE],
    ModelTier.STRONG: [ProviderType.NVIDIA, ProviderType.MISTRAL],
}


class LLMRouter:
    """The LLMRouter singleton — the central routing brain.

    Agents call router.complete() with a tier and messages. The router:
    1. Estimates token consumption
    2. Filters available providers via the budget planner
    3. Selects the best provider+model via the wait gate
    4. Dispatches the request
    5. Records actual usage for calibration
    6. Handles failover on errors

    The router is async and can handle multiple concurrent requests.
    The wait gate ensures we never hit a 429 by tracking capacity in
    real-time sliding windows.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

        # Initialize providers
        provider_configs = self.settings.providers
        self._providers: dict[ProviderType, BaseProvider] = {
            ProviderType.GOOGLE: GoogleProvider(provider_configs[ProviderType.GOOGLE]),
            ProviderType.NVIDIA: NvidiaProvider(provider_configs[ProviderType.NVIDIA]),
            ProviderType.CEREBRAS: CerebrasProvider(provider_configs[ProviderType.CEREBRAS]),
            ProviderType.GROQ: GroqProvider(provider_configs[ProviderType.GROQ]),
            ProviderType.MISTRAL: MistralProvider(provider_configs[ProviderType.MISTRAL]),
        }

        # Initialize sliding window trackers — one per provider+model pair
        trackers: dict[tuple[ProviderType, str], SlidingWindowTracker] = {}
        for provider_type, provider_config in provider_configs.items():
            for model in provider_config.models:
                if model.deprecated:
                    continue
                trackers[(provider_type, model.name)] = SlidingWindowTracker(
                    model=model,
                    window_seconds=self.settings.wait_gate.window_seconds,
                )
        self._trackers = trackers

        # Initialize subsystems
        self.wait_gate = WaitGate(
            config=self.settings.wait_gate,
            trackers=trackers,
        )
        self.budget_planner = DailyBudgetPlanner(
            reserve_fraction=self.settings.wait_gate.budget_reserve,
        )
        self.estimator = TokenEstimator()

        # P13: Response cache + speculative racer + structured validator
        self._response_cache = ResponseCache(ttl_seconds=3600)
        self._speculative_racer = SpeculativeRacer(router=self)
        self._structured_validator = StructuredValidator(router=self)

        # Token tracking: per-provider cumulative token usage for end-of-run summary
        self._token_usage_by_provider: dict[ProviderType, dict[str, int]] = {
            pt: {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "calls": 0}
            for pt in ProviderType
        }

        # Model lookup: tier → list of (provider_type, model_spec)
        self._tier_models: dict[ModelTier, list[tuple[ProviderType, ModelSpec]]] = {}
        for provider_type, provider_config in provider_configs.items():
            for model in provider_config.models:
                if model.deprecated:
                    continue
                self._tier_models.setdefault(model.tier, []).append(
                    (provider_type, model)
                )

    def get_provider(self, provider_type: ProviderType) -> BaseProvider:
        """Get a provider instance by type."""
        return self._providers[provider_type]

    def get_token_summary(self) -> dict[str, Any]:
        """Get per-provider token usage breakdown for end-of-run summary.

        Returns a dict with:
        - total_tokens: sum across all providers
        - total_input_tokens, total_output_tokens, total_calls
        - by_provider: {provider_name: {input, output, total, calls}}
        """
        by_provider: dict[str, dict[str, int]] = {}
        grand_total = 0
        grand_input = 0
        grand_output = 0
        grand_calls = 0
        for pt, stats in self._token_usage_by_provider.items():
            if stats["total_tokens"] == 0 and stats["calls"] == 0:
                continue
            name = pt.value if hasattr(pt, "value") else str(pt)
            by_provider[name] = {
                "input_tokens": stats["input_tokens"],
                "output_tokens": stats["output_tokens"],
                "total_tokens": stats["total_tokens"],
                "calls": stats["calls"],
            }
            grand_total += stats["total_tokens"]
            grand_input += stats["input_tokens"]
            grand_output += stats["output_tokens"]
            grand_calls += stats["calls"]
        return {
            "total_tokens": grand_total,
            "total_input_tokens": grand_input,
            "total_output_tokens": grand_output,
            "total_calls": grand_calls,
            "by_provider": by_provider,
        }

    def _predicted_rate_limited(self, provider_type: ProviderType) -> bool:
        """D9: Check if a provider is predicted to be rate-limited right now.

        Uses the wait gate's sliding window tracker to see if the provider
        is near its RPM/TPM limit. If so, skip it and try the next provider.
        """
        if provider_type not in self._providers:
            return True
        provider = self._providers[provider_type]
        if not provider.health.is_available():
            return True
        # Check if any tracker for this provider shows near-limit usage
        for (pt, _model_name), tracker in self._trackers.items():
            if pt != provider_type:
                continue
            # If RPM or TPM is >85% utilized, predict rate-limited
            if tracker.model.rpm > 0:
                rpm_pct = tracker.current_rpm() / tracker.model.rpm
                if rpm_pct > 0.85:
                    return True
            if tracker.model.tpm > 0:
                tpm_pct = tracker.current_tpm() / tracker.model.tpm
                if tpm_pct > 0.85:
                    return True
        return False

    def _sort_providers_by_priority(
        self,
        tier: ModelTier,
        providers: set[ProviderType],
    ) -> list[ProviderType]:
        """D19/D20/D21: Sort providers by tier-specific priority order.

        Providers in _TIER_PROVIDER_PRIORITY come first (in listed order),
        remaining providers are appended in arbitrary order.
        """
        priority = _TIER_PROVIDER_PRIORITY.get(tier, [])
        ordered = [p for p in priority if p in providers]
        remaining = [p for p in providers if p not in priority]
        return ordered + remaining

    def get_available_providers(
        self,
        tier: ModelTier,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
    ) -> set[ProviderType]:
        """Get the set of providers available for a tier and urgency level.

        Combines health checks and budget checks:
        1. Provider must be healthy (not in cooldown/circuit breaker)
        2. Provider must have budget remaining (respecting reserve for non-HIGH urgency)
        3. Provider must have at least one non-deprecated model for the tier
        """
        available: set[ProviderType] = set()

        # Get models by provider for this tier
        models_by_provider: dict[ProviderType, list[ModelSpec]] = {}
        for provider_type, provider in self._providers.items():
            if not provider.health.is_available():
                continue
            tier_models = provider.get_models_for_tier(tier)
            if tier_models:
                models_by_provider[provider_type] = tier_models

        # Filter by budget
        budget_available = self.budget_planner.filter_available_providers(
            tier=tier,
            models_by_provider=models_by_provider,
            urgency=urgency,
        )

        return budget_available

    async def complete(
        self,
        tier: ModelTier,
        messages: list[dict[str, str]],
        agent_name: str = "",
        urgency: TaskUrgency = TaskUrgency.NORMAL,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
        _skip_speculative: bool = False,
    ) -> RouterResponse:
        """Execute a completion request at the given tier.

        This is the main entry point for agents. They specify:
        - tier: what intelligence level they need
        - messages: the conversation
        - agent_name: for calibration tracking
        - urgency: for budget allocation

        The router handles everything else — provider selection, wait gate,
        failover, calibration. Agents don't know which provider they're using.
        (§9: "Agents don't know which provider they're using — they request
        a tier and the router decides.")
        """
        # Extract system and user prompts for token estimation
        system_prompt = ""
        user_prompt = ""
        for msg in messages:
            if msg.get("role") == "system":
                system_prompt += msg.get("content", "")
            elif msg.get("role") == "user":
                user_prompt += msg.get("content", "")

        estimated_tokens = self.estimator.estimate_tokens(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            tier=tier,
            agent_name=agent_name,
        )

        # P13: Check response cache before hitting any provider
        cached = self._response_cache.get(
            tier=tier,
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            use_semantic=(urgency != TaskUrgency.HIGH),
        )
        if cached is not None:
            trace("cache", tier=tier.value, agent=agent_name, status="hit")
            return cached

        # P13: Speculative racing for DEEP tier (critical-path latency reduction)
        # _skip_speculative prevents infinite recursion when the racer falls back
        if tier == ModelTier.DEEP and urgency == TaskUrgency.HIGH and not _skip_speculative:
            response = await self._speculative_racer.race(
                tier=tier,
                messages=messages,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            if response.success:
                self._response_cache.set(
                    tier=tier,
                    messages=messages,
                    response=response,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            return response

        # Try the requested tier first
        response = await self._try_tier(
            tier=tier,
            messages=messages,
            estimated_tokens=estimated_tokens,
            agent_name=agent_name,
            urgency=urgency,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        if response is not None and response.success:
            self._response_cache.set(tier, messages, response, temperature, max_tokens)
            return response

        # If the requested tier failed, try adjacent tiers (§3.3)
        for adjacent_tier in _TIER_ADJACENCY.get(tier, []):
            # Re-estimate for the adjacent tier (different output budget)
            adjacent_estimated = self.estimator.estimate_tokens(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tier=adjacent_tier,
                agent_name=agent_name,
            )

            response = await self._try_tier(
                tier=adjacent_tier,
                messages=messages,
                estimated_tokens=adjacent_estimated,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )

            if response is not None and response.success:
                self._response_cache.set(tier, messages, response, temperature, max_tokens)
                return response

        # D9: If all adjacent tiers exhausted, try explicit downgrade
        downgrade_tier = _TIER_DOWNGRADE.get(tier)
        if downgrade_tier is not None:
            downgrade_estimated = self.estimator.estimate_tokens(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                tier=downgrade_tier,
                agent_name=agent_name,
            )
            response = await self._try_tier(
                tier=downgrade_tier,
                messages=messages,
                estimated_tokens=downgrade_estimated,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            if response is not None and response.success:
                self._response_cache.set(tier, messages, response, temperature, max_tokens)
                return response

        # All tiers exhausted — return the last error response
        if response is not None:
            return response

        return RouterResponse(
            content="",
            model="none",
            provider=ProviderType.GOOGLE,  # Placeholder
            tier=tier,
            success=False,
            error="All providers exhausted across all adjacent tiers",
        )

    async def _try_tier(
        self,
        tier: ModelTier,
        messages: list[dict[str, str]],
        estimated_tokens: int,
        agent_name: str,
        urgency: TaskUrgency,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, str] | None,
    ) -> RouterResponse | None:
        """Try to execute a request at a specific tier.

        Handles the wait gate selection, wait-for-capacity, dispatch,
        and failover within the tier. Returns None if no candidates exist.

        D19/D20/D21: Providers are tried in priority order per tier.
        """
        available_providers = self.get_available_providers(tier, urgency)

        if not available_providers:
            return None

        # D19/D20/D21: Try providers in priority order
        ordered_providers = self._sort_providers_by_priority(tier, available_providers)

        for provider_type in ordered_providers:
            # Skip predicted-rate-limited providers
            if self._predicted_rate_limited(provider_type):
                continue

            # Select the best model on this provider via the wait gate
            candidate, wait_seconds = self.wait_gate.select_with_wait(
                tier=tier,
                estimated_tokens=estimated_tokens,
                available_providers={provider_type},
            )

            if candidate is None:
                continue

            # If we need to wait, do so
            if wait_seconds > 0:
                if wait_seconds > self.settings.wait_gate.medium_wait_threshold:
                    # > 30s — skip this provider, try next
                    continue
                waited = await self.wait_gate.wait_for_capacity(candidate, estimated_tokens)
                if not waited:
                    # Capacity didn't open up — try next provider
                    continue

            # Dispatch the request
            response = await self._dispatch(
                candidate=candidate,
                messages=messages,
                estimated_tokens=estimated_tokens,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )

            if response is not None and response.success:
                return response

        # All providers in this tier exhausted — try remaining without priority filter
        return await self._try_next_candidate(
            tier=tier,
            messages=messages,
            estimated_tokens=estimated_tokens,
            agent_name=agent_name,
            urgency=urgency,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            exclude_provider=None,  # Don't exclude any — we already tried all above
        )

    async def _try_next_candidate(
        self,
        tier: ModelTier,
        messages: list[dict[str, str]],
        estimated_tokens: int,
        agent_name: str,
        urgency: TaskUrgency,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, str] | None,
        exclude_provider: ProviderType | None = None,
    ) -> RouterResponse | None:
        """Try ALL remaining candidates in the tier, excluding the failed provider.

        D9 fix: loops through every available provider in the tier, skipping
        predicted-rate-limited ones, until one succeeds or all are exhausted.
        D19/D20/D21: providers are sorted by tier priority.
        """
        available_providers = self.get_available_providers(tier, urgency)
        if exclude_provider is not None:
            available_providers.discard(exclude_provider)

        if not available_providers:
            return None

        # D19/D20/D21: Try providers in priority order
        ordered_providers = self._sort_providers_by_priority(tier, available_providers)

        for provider_type in ordered_providers:
            # Skip predicted-rate-limited providers
            if self._predicted_rate_limited(provider_type):
                continue

            candidate, wait_seconds = self.wait_gate.select_with_wait(
                tier=tier,
                estimated_tokens=estimated_tokens,
                available_providers={provider_type},
            )

            if candidate is None:
                continue

            if wait_seconds > 0 and wait_seconds <= self.settings.wait_gate.medium_wait_threshold:
                await self.wait_gate.wait_for_capacity(candidate, estimated_tokens)

            response = await self._dispatch(
                candidate=candidate,
                messages=messages,
                estimated_tokens=estimated_tokens,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )

            if response is not None and response.success:
                return response

        return None

    async def _dispatch(
        self,
        candidate: ProviderCandidate,
        messages: list[dict[str, str]],
        estimated_tokens: int,
        agent_name: str,
        urgency: TaskUrgency,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, str] | None,
    ) -> RouterResponse:
        """Dispatch a request to a provider and record usage.

        This is where the actual API call happens. After the response:
        1. Record dispatch in the wait gate (RPM/TPM/RPD tracking)
        2. Record consumption in the budget planner
        3. Record actual token usage for calibration
        4. If the request failed, attempt failover to the next candidate
        """
        provider = self._providers[candidate.provider_type]

        # Record dispatch BEFORE the call (so concurrent requests see the capacity)
        self.wait_gate.record_dispatch(
            provider=candidate.provider_type,
            model_name=candidate.model.name,
            estimated_tokens=estimated_tokens,
        )
        self.budget_planner.consume(
            provider=candidate.provider_type,
            model_name=candidate.model.name,
            urgency=urgency,
        )

        # Execute the request
        response = await provider.complete(
            model=candidate.model.name,
            messages=messages,
            tier=candidate.model.tier,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )

        if response.success:
            # Record actual usage for calibration
            self.wait_gate.record_actual_usage(
                provider=candidate.provider_type,
                model_name=candidate.model.name,
                estimated_tokens=estimated_tokens,
                actual_tokens=response.total_tokens,
                latency_ms=response.latency_ms,
            )
            self.estimator.record_actual(
                agent_name=agent_name,
                model_name=candidate.model.name,
                tier=candidate.model.tier,
                estimated_tokens=estimated_tokens,
                actual_tokens=response.total_tokens,
            )
            # Track per-provider token usage for end-of-run summary
            stats = self._token_usage_by_provider.get(candidate.provider_type)
            if stats is not None:
                stats["input_tokens"] += response.input_tokens
                stats["output_tokens"] += response.output_tokens
                stats["total_tokens"] += response.total_tokens
                stats["calls"] += 1
            return response

        # Request failed — attempt failover within the tier
        # Don't failover on 429 (wait gate should prevent these, but if one
        # slips through, the provider is in cooldown and we try the next)
        return await self._try_next_candidate(
            tier=candidate.model.tier,
            messages=messages,
            estimated_tokens=estimated_tokens,
            agent_name=agent_name,
            urgency=urgency,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            exclude_provider=candidate.provider_type,
        ) or response

    def get_tpm_status(self) -> dict[ProviderType, dict[str, float]]:
        """Get TPM usage percentages for all providers — for TUI display (§8.6)."""
        result: dict[ProviderType, dict[str, float]] = {}
        for provider_type in ProviderType:
            result[provider_type] = self.wait_gate.get_tpm_usage_percentage(provider_type)
        return result

    def get_budget_status(self) -> dict[ProviderType, dict[str, float]]:
        """Get budget usage for all providers — for TUI display."""
        return self.budget_planner.get_usage_summary()

    def get_provider_health(self) -> dict[ProviderType, dict[str, Any]]:
        """Get health status for all providers — for TUI splash screen (§8.2)."""
        result: dict[ProviderType, dict[str, Any]] = {}
        for provider_type, provider in self._providers.items():
            result[provider_type] = {
                "status": provider.health.status.value,
                "available": provider.health.is_available(),
                "uptime_pct": provider.health.uptime_percentage(),
                "last_error": provider.health.last_error,
                "total_requests": provider.health.total_requests,
                "total_errors": provider.health.total_errors,
            }
        return result

    async def health_check_all(self) -> dict[ProviderType, bool]:
        """Run health checks on all providers — used at startup (§8.2 splash)."""
        results: dict[ProviderType, bool] = {}
        tasks = [
            (pt, provider.health_check()) for pt, provider in self._providers.items()
        ]
        for provider_type, task in tasks:
            results[provider_type] = await task
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Singleton access
# ─────────────────────────────────────────────────────────────────────────────

_router: LLMRouter | None = None


def get_router() -> LLMRouter:
    """Get the singleton LLMRouter instance."""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router


def reset_router() -> None:
    """Reset the singleton — useful for testing."""
    global _router
    _router = None
