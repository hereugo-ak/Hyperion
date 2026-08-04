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
from dataclasses import dataclass, field
from typing import Any

from hyperion.config import (
    ModelSpec,
    ModelTier,
    ProviderType,
    Settings,
    get_settings,
)
from hyperion.obs import trace
from hyperion.router.budget import DailyBudgetPlanner, TaskUrgency
from hyperion.router.estimator import TokenEstimator
from hyperion.router.providers.base import (
    BaseProvider,
    ProviderStatus,
    RouterFailure,
    RouterResponse,
    is_transient_connection_error,
)
from hyperion.router.providers.cerebras import CerebrasProvider
from hyperion.router.providers.google import GoogleProvider
from hyperion.router.providers.groq import GroqProvider
from hyperion.router.providers.mistral import MistralProvider
from hyperion.router.providers.nvidia import NvidiaProvider
from hyperion.router.semantic_cache import ResponseCache
from hyperion.router.speculative_racer import SpeculativeRacer
from hyperion.router.structured_validator import StructuredValidator
from hyperion.router.wait_gate import ProviderCandidate, SlidingWindowTracker, WaitGate

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

# W-17: failure classification driving the failover policy.
# - auth (401/403): dead credential. The provider opens its circuit, the
#   budget charge is refunded (no real quota was consumed), and the provider
#   is never retried within the same complete() call.
# - rate_limit (429): the wait gate mispredicted capacity. Cool the exact
#   provider+model pair and fail over to a different provider. Provider quotas
#   are independent; stopping the whole call here strands healthy capacity.
# - transient (5xx/timeout/connect/DNS): retry the SAME provider once with a short
#   backoff, then fail over.
# - other: fail over to the next unvisited candidate.
_TRANSIENT_STATUS_CODES = frozenset({408, 425, 500, 502, 503, 504})
_TRANSIENT_BACKOFF_SECONDS = 1.0


@dataclass
class RouterAttempt:
    """W-17: shared failover context for one complete() invocation.

    Before this existed, failover state was a single ``exclude_provider``
    argument threaded through a mutual recursion between ``_dispatch`` and
    ``_try_next_candidate``: no visited set, no depth counter, no total
    attempt budget. Five failing providers could recurse without bound,
    re-dispatching already-failed providers and consuming daily budget per
    frame. One RouterAttempt is threaded through _try_tier,
    _try_tier and _dispatch for the whole call chain, so every
    provider is dispatched at most once (twice counting the explicit
    transient retry) and the chain terminates at a hard ceiling.
    """

    max_attempts: int
    visited: set[ProviderType] = field(default_factory=set)
    attempts: int = 0

    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts

# D19/D20/D21: Provider priority per tier — controls which provider is tried
# first within a tier. Providers not listed are appended in arbitrary order.
# Rationale:
#   FAST: Mistral primary (rpm60), Groq secondary (rpm30), Cerebras overflow (rpm5)
#   STANDARD: NVIDIA/Mistral primary (high TPM), Groq burst-only (tpm8k)
#   DEEP: Google flash-lite, Mistral devstral, NVIDIA ultra-550b (round-robin, ≥2 non-Google)
#   STRONG: NVIDIA nemotron, Mistral large
_TIER_PROVIDER_PRIORITY: dict[ModelTier, list[ProviderType]] = {
    # P2-31: MICRO is the highest-volume tier (every specialist sub-agent
    # runs on it), so it must have a deterministic priority order. Mistral
    # ministral-3b leads (rpm60, no daily cap), then Google gemma (14.4K
    # RPD), then Groq llama-instant (low tpm). Without an entry the sort
    # fell back to `[] + list(set)`, i.e. non-deterministic set order.
    ModelTier.MICRO: [ProviderType.MISTRAL, ProviderType.GOOGLE, ProviderType.GROQ],
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
            if pt is not ProviderType.NONE  # P2-29: NONE never serves traffic
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

    def _tracker_hot(self, tracker: SlidingWindowTracker) -> bool:
        """P2-30: is a single model's sliding window near its RPM/TPM limit?

        This is the per-model predicate. A model is "hot" when either its RPM
        or TPM rolling window is >85% utilised.
        """
        if tracker.model.rpm > 0 and tracker.current_rpm() / tracker.model.rpm > 0.85:
            return True
        return tracker.model.tpm > 0 and tracker.current_tpm() / tracker.model.tpm > 0.85

    def _candidate_rate_limited(self, candidate: ProviderCandidate) -> bool:
        """P2-30/P2-G30: evaluate the rate-limit prediction for the SPECIFIC
        candidate the wait gate selected, not for the provider.

        One hot model must not disable an entire provider. Google runs Gemma
        at 14,400 RPD and Gemini at 500 RPD; saturating the small model used
        to mark the large one unavailable. The router skips the hot candidate
        and the wait gate can offer the next candidate on the same provider.
        """
        return self._tracker_hot(candidate.tracker)

    def _predicted_rate_limited(self, provider_type: ProviderType) -> bool:
        """D9: True only when the provider genuinely cannot serve right now.

        P2-30: this is the provider-LEVEL aggregate retained for diagnostics
        (``_diagnose_skip_reasons``) and the speculative racer. It is True
        when the provider is unhealthy or EVERY non-deprecated model on it is
        hot (no cold model exists). A provider with one hot model and one
        cold model is NOT rate-limited; per-request gating uses
        ``_candidate_rate_limited`` instead.
        """
        if provider_type not in self._providers:
            return True
        provider = self._providers[provider_type]
        if not provider.health.is_available():
            return True
        trackers = [
            tracker
            for (pt, _model_name), tracker in self._trackers.items()
            if pt == provider_type and not tracker.model.deprecated
        ]
        if not trackers:
            return True
        return all(self._tracker_hot(tracker) for tracker in trackers)

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
        estimated_tokens: int = 0,
    ) -> set[ProviderType]:
        """Get the set of providers available for a tier and urgency level.

        Combines health checks and budget checks:
        1. Provider must be healthy (not in cooldown/circuit breaker)
        2. Provider must have budget remaining (respecting reserve for non-HIGH urgency)
        3. Provider must have at least one non-deprecated model for the tier
        """
        # D5.1: an `available: set[ProviderType] = set()` accumulator sat here
        # and was never written to or read (ruff F841) — the function builds
        # `models_by_provider` and delegates the actual filtering to the budget
        # planner. Removed: an empty accumulator named `available` in a function
        # named `_get_available_providers` reads like the return value, which
        # makes the real `return budget_available` look like a bug.

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
            estimated_tokens=estimated_tokens,
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
            speculative_response = await self._speculative_racer.race(
                tier=tier,
                messages=messages,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            if speculative_response.success:
                self._response_cache.set(
                    tier=tier,
                    messages=messages,
                    response=speculative_response,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
            return speculative_response

        # P2-29: track every tier walked so a total failure can report it
        tiers_attempted: list[ModelTier] = [tier]

        # W-17: ONE attempt context for the entire failover chain — the
        # requested tier, the adjacency walk, and the explicit downgrade
        # share one visited set and one attempt ceiling. Sized to the real
        # provider count: every provider may be tried once plus one retry
        # each, never more.
        attempt = RouterAttempt(max_attempts=max(1, len(self._providers)) * 2)

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
            attempt=attempt,
        )

        if response is not None and response.success:
            self._response_cache.set(tier, messages, response, temperature, max_tokens)
            return response

        # A model-scoped 429 is handled inside _try_tier by trying an
        # independent provider. If every provider fails, continue the normal
        # adjacent-tier walk rather than aborting the engagement.

        # If the requested tier failed, try adjacent tiers (§3.3)
        for adjacent_tier in _TIER_ADJACENCY.get(tier, []):
            if adjacent_tier not in tiers_attempted:
                tiers_attempted.append(adjacent_tier)
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
                attempt=attempt,
            )

            if response is not None and response.success:
                # W-17: mark the tier downgrade so downstream reporting can
                # state that a weaker model than requested produced this.
                response.downgraded = True
                self._response_cache.set(tier, messages, response, temperature, max_tokens)
                return response

        # D9: If all adjacent tiers exhausted, try explicit downgrade.
        # P2-31: skip when the adjacency walk already attempted this tier —
        # _TIER_ADJACENCY[STRONG] already reaches STANDARD, so the downgrade
        # step was attempting STANDARD a second time on every STRONG failure.
        downgrade_tier = _TIER_DOWNGRADE.get(tier)
        if downgrade_tier is not None and downgrade_tier not in tiers_attempted:
            tiers_attempted.append(downgrade_tier)
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
                attempt=attempt,
            )
            if response is not None and response.success:
                response.downgraded = True
                self._response_cache.set(tier, messages, response, temperature, max_tokens)
                return response

        # P2-29: a total failure is attributed to NO provider. Naming one
        # (the old ProviderType.GOOGLE "Placeholder") misreported a deleted
        # API key as quota exhaustion for two entire engagements.
        failure = RouterFailure(
            tiers_attempted=tiers_attempted,
            providers_considered={
                t.value: [pt.value for pt in self._providers] for t in tiers_attempted
            },
            skip_reasons=self._diagnose_skip_reasons(tiers_attempted),
        )
        trace(
            "router",
            tier=tier.value,
            agent=agent_name,
            status="total_failure",
            detail=failure.render(),
        )
        last_error = response.error if response is not None else None
        detail = f"; last provider error: {last_error}" if last_error else ""
        return RouterResponse(
            content="",
            model="none",
            provider=ProviderType.NONE,
            tier=tier,
            success=False,
            error=(
                "All providers exhausted across all adjacent tiers "
                f"({failure.render()}){detail}"
            ),
            failure=failure,
            status_code=response.status_code if response is not None else None,
        )

    def _diagnose_skip_reasons(
        self, tiers_attempted: list[ModelTier]
    ) -> dict[ProviderType, str]:
        """Per-provider reason why no candidate could serve the request (P2-29).

        Recomputed from live state at failure time so the operator sees the
        actual cause (auth, circuit, budget, no model for tier) rather than
        a quota-exhaustion guess.
        """
        reasons: dict[ProviderType, str] = {}
        for pt, provider in self._providers.items():
            if provider.health.status == ProviderStatus.UNAUTHENTICATED:
                reasons[pt] = "unauthenticated"
            elif not provider.health.is_available():
                reasons[pt] = "health_open"
            elif all(
                not provider.get_models_for_tier(t) for t in tiers_attempted
            ):
                reasons[pt] = "no_model_for_tier"
            elif self._predicted_rate_limited(pt):
                reasons[pt] = "predicted_rate_limited"
            else:
                reasons[pt] = "budget_exhausted"
        return reasons

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
        attempt: RouterAttempt | None = None,
    ) -> RouterResponse | None:
        """Try to execute a request at a specific tier.

        W-17: this is now ONE explicit loop over the priority-sorted,
        ``attempt.visited``-filtered providers of the tier — one call frame
        per attempt, with ``max_attempts`` checked at the single dispatch
        point. The old second pass (``exclude_provider=None``) re-dispatched
        every provider the first pass had just failed, and the mutual
        recursion with ``_try_next_candidate`` had no depth bound; both are
        deleted. Failover decisions are classification-driven (see
        ``_dispatch``); a 429 halts the walk immediately.

        Standalone callers (the speculative racer) may omit ``attempt`` —
        a fresh bounded context is created so their failover is capped too.

        D19/D20/D21: Providers are tried in priority order per tier.
        Returns the last failure response when every candidate fails, or
        None when no candidate could even be selected.
        """
        if attempt is None:
            attempt = RouterAttempt(
                max_attempts=max(1, len(self._providers)) * 2
            )

        last_failure: RouterResponse | None = None

        while not attempt.exhausted():
            available_providers = self.get_available_providers(
                tier, urgency, estimated_tokens
            )

            if not available_providers:
                break

            # D19/D20/D21: priority order, minus every provider already
            # dispatched during this complete() call (W-17 step 2/3).
            ordered_providers = [
                p
                for p in self._sort_providers_by_priority(tier, available_providers)
                if p not in attempt.visited
            ]
            if not ordered_providers:
                break

            dispatched = False
            for provider_type in ordered_providers:
                if attempt.exhausted():
                    break

                # P2-30: select the candidate FIRST, then skip the hot candidate
                # rather than the whole provider. A saturated Gemma window must
                # not disable the Gemini model on the same provider.
                candidate, wait_seconds = self.wait_gate.select_with_wait(
                    tier=tier,
                    estimated_tokens=estimated_tokens,
                    available_providers={provider_type},
                )

                if candidate is None:
                    continue

                # Skip a predicted-hot candidate; this provider's other models
                # and the remaining providers stay eligible.
                if self._candidate_rate_limited(candidate):
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

                dispatched = True
                response = await self._dispatch(
                    candidate=candidate,
                    messages=messages,
                    estimated_tokens=estimated_tokens,
                    agent_name=agent_name,
                    urgency=urgency,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                    attempt=attempt,
                )

                if response is None:
                    # Attempt budget exhausted at the dispatch gate.
                    break
                if response.success:
                    return response

                last_failure = response
                # A 429 cools only this exact model in _dispatch. Continue to
                # another provider whose quota is independent. The visited set
                # prevents retrying the same provider in this completion call.
                # auth/transient/other failures: the provider is now in
                # attempt.visited (and possibly circuit-open); the loop
                # moves to the next unvisited provider.

            if not dispatched:
                # Every remaining provider was unselectable (no candidate,
                # hot, or too long a wait) — re-querying availability will
                # not change that within this call.
                break

        return last_failure

    def _record_served(
        self,
        candidate: ProviderCandidate,
        response: RouterResponse,
        estimated_tokens: int,
        agent_name: str,
    ) -> None:
        """Record a served response: wait-gate actuals, estimator
        calibration, and per-provider token totals. Shared by the primary
        dispatch and the W-17 transient retry."""
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
        self.budget_planner.reconcile_actual(
            provider=candidate.provider_type,
            model_name=candidate.model.name,
            estimated_tokens=estimated_tokens,
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            actual_tokens=response.total_tokens,
        )
        stats = self._token_usage_by_provider.get(candidate.provider_type)
        if stats is not None:
            stats["input_tokens"] += response.input_tokens
            stats["output_tokens"] += response.output_tokens
            stats["total_tokens"] += response.total_tokens
            stats["calls"] += 1

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
        attempt: RouterAttempt,
    ) -> RouterResponse | None:
        """Dispatch ONE attempt to a provider and record usage.

        W-17: one call frame per attempt — the recursive failover into
        ``_try_next_candidate`` is gone; ``_try_tier``'s loop owns failover.
        Failure handling is classification-driven from the HTTP status:

        - 401/403: refund the budget charge (an auth rejection never
          consumed real provider quota) and return. The provider already
          opened its circuit and is in ``attempt.visited``, so it is never
          retried within this complete() call.
        - 429: the provider's health tracker already recorded the cooldown;
          return the response so ``_try_tier`` halts the walk instead of
          spreading the rate limit across providers.
        - transient (408/425/5xx/timeout): ONE retry on the same provider
          after a short backoff, then fail over.
        - anything else: fail over to the next unvisited candidate.

        Returns None when the attempt budget is already exhausted.
        """
        if attempt.exhausted():
            return None

        # W-18: atomically reserve request and estimated-token capacity at the
        # dispatch boundary. The earlier filter is advisory; this reservation
        # closes the concurrent check-then-consume gap and enforces model TPD.
        if not self.budget_planner.reserve(
            provider=candidate.provider_type,
            model=candidate.model,
            estimated_tokens=estimated_tokens,
            urgency=urgency,
        ):
            return None

        provider = self._providers[candidate.provider_type]
        attempt.attempts += 1
        attempt.visited.add(candidate.provider_type)

        # Record dispatch AFTER the durable reservation and before the call.
        self.wait_gate.record_dispatch(
            provider=candidate.provider_type,
            model_name=candidate.model.name,
            estimated_tokens=estimated_tokens,
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
            self._record_served(candidate, response, estimated_tokens, agent_name)
            return response

        status = response.status_code

        if status in (401, 403):
            # A dead credential consumed neither a useful request budget nor
            # token capacity, so refund both parts of the reservation.
            self.budget_planner.refund(
                provider=candidate.provider_type,
                model_name=candidate.model.name,
                estimated_tokens=estimated_tokens,
            )
            return response

        # Every other failed completion consumed an RPM slot but produced no
        # token usage. Release estimated token capacity so failed calls cannot
        # manufacture `budget_exhausted` later in the same engagement.
        self.budget_planner.release_reservation(
            provider=candidate.provider_type,
            model_name=candidate.model.name,
            estimated_tokens=estimated_tokens,
        )

        if status == 429:
            self.wait_gate.record_rate_limit(
                provider=candidate.provider_type,
                model_name=candidate.model.name,
            )
            return response

        is_transient = status in _TRANSIENT_STATUS_CODES or (
            status is None and is_transient_connection_error(response.error)
        )
        if is_transient and not attempt.exhausted():
            # W-17 step 5: exactly ONE same-provider retry with backoff.
            await asyncio.sleep(_TRANSIENT_BACKOFF_SECONDS)
            attempt.attempts += 1
            if not self.budget_planner.reserve(
                provider=candidate.provider_type,
                model=candidate.model,
                estimated_tokens=estimated_tokens,
                urgency=urgency,
            ):
                return response
            self.wait_gate.record_dispatch(
                provider=candidate.provider_type,
                model_name=candidate.model.name,
                estimated_tokens=estimated_tokens,
            )
            retry = await provider.complete(
                model=candidate.model.name,
                messages=messages,
                tier=candidate.model.tier,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
            )
            if retry.success:
                self._record_served(candidate, retry, estimated_tokens, agent_name)
                return retry
            if retry.status_code in (401, 403):
                self.budget_planner.refund(
                    provider=candidate.provider_type,
                    model_name=candidate.model.name,
                    estimated_tokens=estimated_tokens,
                )
            else:
                self.budget_planner.release_reservation(
                    provider=candidate.provider_type,
                    model_name=candidate.model.name,
                    estimated_tokens=estimated_tokens,
                )
                if retry.status_code == 429:
                    self.wait_gate.record_rate_limit(
                        provider=candidate.provider_type,
                        model_name=candidate.model.name,
                    )
            return retry

        return response

    def get_tpm_status(self) -> dict[ProviderType, dict[str, float]]:
        """Get TPM usage percentages for all providers — for TUI display (§8.6)."""
        result: dict[ProviderType, dict[str, float]] = {}
        for provider_type in self._providers:
            result[provider_type] = self.wait_gate.get_tpm_usage_percentage(provider_type)
        return result

    def get_budget_status(self) -> dict[ProviderType, dict[str, float]]:
        """Get persisted request and cost usage for the TUI display."""
        return self.budget_planner.get_usage_summary()

    def get_engagement_cost_usd(self) -> float:
        """Return the current engagement's estimated LLM cost."""
        return self.budget_planner.engagement_cost_usd

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
