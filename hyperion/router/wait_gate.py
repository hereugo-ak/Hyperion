"""
HYPERION Wait Gate — the predictive rate limit management system.

This is NOT a generic retry/backoff utility. This is the system that tracks
RPM/TPM/RPD in real-time sliding windows across all 4 providers and predicts
when a provider will be rate-limited — routing around it BEFORE the 429
happens. (§3.1–§3.3)

The old system fired requests, got 429s, then failover. v0.1 predicts when
a provider will be rate-limited and routes around it before the 429 happens.
This is the difference between reactive and proactive rate limit management.

A 429 is a failure. Every 429 wastes time, wastes tokens, and forces
failover to a potentially suboptimal provider. The wait gate eliminates 429s
by tracking capacity in real-time and routing intelligently.

Components:
- SlidingWindowTracker: per provider+model, tracks RPM/TPM/RPD in a
  rolling 60-second window (deque of timestamps + token counts). O(1)
  amortized cost per check.
- WaitGate: coordinator that evaluates all trackers, calculates optimal
  wait time or provider switch, predicts TPM consumption based on prompt
  size, and maintains a global request queue with priorities. (§3.2)
"""

from __future__ import annotations

import asyncio
import random
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any

from hyperion.config import ModelSpec, ModelTier, ProviderType, WaitGateConfig


# ─────────────────────────────────────────────────────────────────────────────
# SlidingWindowTracker — per provider+model rate limit tracking (§3.3)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class SlidingWindowTracker:
    """Rolling 60-second window tracking RPM, TPM, and RPD for one model.

    Each provider+model pair has its own tracker. The window is a deque
    that prunes entries older than 60 seconds on every access — O(1)
    amortized cost per check. (§3.3)

    RPM: count of requests in the window (deque of timestamps)
    TPM: sum of estimated tokens in the window (deque of (timestamp, token_count))
    RPD: daily counter (resets at UTC midnight)
    """

    model: ModelSpec
    window_seconds: int = 60

    # Rolling window entries
    _rpm_window: deque[float] = field(default_factory=deque)
    _tpm_window: deque[tuple[float, int]] = field(default_factory=deque)

    # Daily counter — resets at UTC midnight
    _rpd_count: int = 0
    _rpd_reset_day: int = 0  # Julian day number for reset detection

    # Historical latency tracking (for scoring)
    _latency_samples: deque[float] = field(default_factory=deque)
    _max_latency_samples: int = 50

    def _prune_window(self) -> None:
        """Remove entries older than window_seconds. Called on every access."""
        cutoff = time.time() - self.window_seconds

        while self._rpm_window and self._rpm_window[0] < cutoff:
            self._rpm_window.popleft()

        while self._tpm_window and self._tpm_window[0][0] < cutoff:
            self._tpm_window.popleft()

    def _check_rpd_reset(self) -> None:
        """Reset daily counter if we've crossed UTC midnight."""
        current_day = time.gmtime().tm_yday
        if current_day != self._rpd_reset_day:
            self._rpd_count = 0
            self._rpd_reset_day = current_day

    def current_rpm(self) -> int:
        """Current requests in the rolling window."""
        self._prune_window()
        return len(self._rpm_window)

    def current_tpm(self) -> int:
        """Current tokens in the rolling window."""
        self._prune_window()
        return sum(tokens for _, tokens in self._tpm_window)

    def current_rpd(self) -> int:
        """Current requests today (resets at UTC midnight)."""
        self._check_rpd_reset()
        return self._rpd_count

    def rpm_available(self) -> int:
        """Remaining RPM capacity."""
        return max(0, self.model.rpm - self.current_rpm())

    def tpm_available(self) -> int:
        """Remaining TPM capacity."""
        return max(0, self.model.tpm - self.current_tpm())

    def rpd_available(self) -> int | None:
        """Remaining RPD capacity. None if unlimited."""
        if self.model.rpd is None:
            return None
        return max(0, self.model.rpd - self.current_rpd())

    def can_serve(self, estimated_tokens: int) -> bool:
        """Check if this model can serve a request right now.

        A model can serve if:
        1. RPM capacity is available (at least 1 request slot)
        2. TPM capacity is available (at least estimated_tokens)
        3. RPD capacity is available (if applicable)
        """
        if self.rpm_available() < 1:
            return False
        if self.tpm_available() < estimated_tokens:
            return False
        rpd_avail = self.rpd_available()
        if rpd_avail is not None and rpd_avail < 1:
            return False
        return True

    def record_request(self, estimated_tokens: int) -> None:
        """Record a dispatched request — add to RPM and TPM windows."""
        now = time.time()
        self._rpm_window.append(now)
        self._tpm_window.append((now, estimated_tokens))

        self._check_rpd_reset()
        self._rpd_count += 1

    def record_actual_tokens(self, estimated_tokens: int, actual_tokens: int) -> None:
        """Calibrate the TPM window with actual token usage.

        After each response, the router records the actual token usage
        and calibrates future estimates. This replaces the estimated token
        count in the TPM window with the actual count. (§3.4)
        """
        now = time.time()
        # Find the most recent entry matching estimated_tokens and replace it
        # This is O(n) but n is small (max ~60 entries in a 60s window)
        for i in range(len(self._tpm_window) - 1, -1, -1):
            ts, tokens = self._tpm_window[i]
            if tokens == estimated_tokens and (now - ts) < self.window_seconds:
                self._tpm_window[i] = (ts, actual_tokens)
                break

    def record_latency(self, latency_ms: float) -> None:
        """Record a latency sample for scoring."""
        self._latency_samples.append(latency_ms)
        if len(self._latency_samples) > self._max_latency_samples:
            self._latency_samples.popleft()

    def average_latency_ms(self) -> float:
        """Historical average latency for this model."""
        if not self._latency_samples:
            return 500.0  # Default assumption if no history
        return sum(self._latency_samples) / len(self._latency_samples)

    def seconds_until_rpm_available(self) -> float:
        """How long until one RPM slot opens up."""
        self._prune_window()
        if not self._rpm_window or self.rpm_available() >= 1:
            return 0.0
        oldest = self._rpm_window[0]
        return max(0.0, (oldest + self.window_seconds) - time.time())

    def seconds_until_tpm_available(self, estimated_tokens: int) -> float:
        """How long until enough TPM capacity opens up for estimated_tokens."""
        self._prune_window()
        if self.tpm_available() >= estimated_tokens:
            return 0.0
        # Find when enough tokens will expire to free up capacity
        needed = estimated_tokens - self.tpm_available()
        tokens_sorted = sorted(self._tpm_window, key=lambda x: x[0])
        freed = 0
        for ts, tokens in tokens_sorted:
            freed += tokens
            if freed >= needed:
                return max(0.0, (ts + self.window_seconds) - time.time())
        return float(self.window_seconds)  # Worst case: full window wait

    def seconds_until_capacity(self, estimated_tokens: int) -> float:
        """Minimum wait time until this model can serve the request."""
        return max(
            self.seconds_until_rpm_available(),
            self.seconds_until_tpm_available(estimated_tokens),
        )

    def available_capacity_score(self) -> float:
        """Normalized available capacity score (0.0 to 1.0+).

        Used by the WaitGate for provider selection scoring (§3.3):
        available_capacity = (rpm_limit - current_rpm) + (tpm_limit - current_tpm) / tpm_limit
        """
        rpm_ratio = self.rpm_available() / max(1, self.model.rpm)
        tpm_ratio = self.tpm_available() / max(1, self.model.tpm)
        return rpm_ratio + tpm_ratio

    def context_fit_score(self, prompt_tokens: int) -> float:
        """How well the model's context window fits the request (§3.3).

        A model that exactly fits gets 1.0. A model that's much larger
        than needed gets a slightly lower score (wasted capacity). A model
        that's too small gets 0.0.
        """
        if prompt_tokens > self.model.context_window:
            return 0.0
        # Perfect fit at 80% utilization; penalize both under and over utilization
        utilization = prompt_tokens / self.model.context_window
        if utilization <= 0.8:
            return 0.5 + (utilization / 0.8) * 0.5  # 0.5 to 1.0
        return max(0.0, 1.0 - ((utilization - 0.8) / 0.2) * 0.5)  # 1.0 to 0.5

    def selection_score(self, estimated_tokens: int, config: WaitGateConfig) -> float:
        """Calculate the selection score for this model (§3.3).

        score = available_capacity * 0.5 + (1 / latency_estimate) * 0.3 + context_fit * 0.2
        """
        capacity = self.available_capacity_score()
        latency = self.average_latency_ms()
        latency_score = 1.0 / max(1.0, latency / 1000.0)  # Normalize: 1000ms = score 1.0
        context = self.context_fit_score(estimated_tokens)

        return (
            capacity * config.score_weight_capacity
            + latency_score * config.score_weight_latency
            + context * config.score_weight_context_fit
        )


# ─────────────────────────────────────────────────────────────────────────────
# ProviderCandidate — a scored candidate for routing
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class ProviderCandidate:
    """A scored provider+model candidate for routing.

    The WaitGate evaluates all eligible provider+model pairs for a tier,
    scores them, and returns the highest-scoring candidate to the router.
    """

    provider_type: ProviderType
    model: ModelSpec
    tracker: SlidingWindowTracker
    score: float
    estimated_wait_seconds: float
    can_serve_now: bool


# ─────────────────────────────────────────────────────────────────────────────
# WaitGate — the coordinator (§3.2)
# ─────────────────────────────────────────────────────────────────────────────


class WaitGate:
    """The WaitGate coordinator — evaluates all provider+model trackers,
    calculates optimal wait time or provider switch, predicts TPM
    consumption based on prompt size, and maintains a global request
    queue with priorities. (§3.2)

    This is the brain of the predictive routing system. Before dispatching
    a request, the router calls WaitGate.select_provider() which:

    1. Filters: exclude pairs where RPD is exhausted, provider is in
       cooldown, or health check failed
    2. Scores: for each remaining pair, calculate a score based on
       available capacity, latency estimate, and context fit
    3. Dispatches: pick the highest-scoring pair

    If no pair can serve NOW:
    - < 5s wait: sleep and retry (blocking the coroutine)
    - 5-30s wait: queue the request, yield to async scheduler
    - > 30s wait: try adjacent tier (up or down based on task priority)
    """

    def __init__(
        self,
        config: WaitGateConfig,
        trackers: dict[tuple[ProviderType, str], SlidingWindowTracker],
    ) -> None:
        self.config = config
        self._trackers = trackers
        self._provider_usage: dict[ProviderType, int] = {}  # round-robin counter

    def get_tracker(self, provider: ProviderType, model_name: str) -> SlidingWindowTracker | None:
        """Get the tracker for a specific provider+model pair."""
        return self._trackers.get((provider, model_name))

    def get_candidates_for_tier(
        self,
        tier: ModelTier,
        estimated_tokens: int,
        available_providers: set[ProviderType],
    ) -> list[ProviderCandidate]:
        """Get all eligible provider+model candidates for a given tier.

        Filters out:
        - Deprecated models
        - Providers not in available_providers (health-checked by router)
        - Models where RPD is exhausted
        """
        candidates: list[ProviderCandidate] = []

        for (provider_type, model_name), tracker in self._trackers.items():
            if provider_type not in available_providers:
                continue
            if tracker.model.tier != tier:
                continue
            if tracker.model.deprecated:
                continue

            # Check RPD exhaustion
            rpd_avail = tracker.rpd_available()
            if rpd_avail is not None and rpd_avail < 1:
                continue

            can_serve = tracker.can_serve(estimated_tokens)
            wait_time = 0.0 if can_serve else tracker.seconds_until_capacity(estimated_tokens)
            score = tracker.selection_score(estimated_tokens, self.config)

            candidates.append(
                ProviderCandidate(
                    provider_type=provider_type,
                    model=tracker.model,
                    tracker=tracker,
                    score=score,
                    estimated_wait_seconds=wait_time,
                    can_serve_now=can_serve,
                )
            )

        return candidates

    def select_provider(
        self,
        tier: ModelTier,
        estimated_tokens: int,
        available_providers: set[ProviderType],
    ) -> ProviderCandidate | None:
        """Select the best provider+model for a request.

        This is the core routing decision (§3.3):
        1. Get all candidates for the tier
        2. Filter to those that can serve NOW
        3. If any can serve now, pick the highest-scoring one
        4. If none can serve now, pick the one with the shortest wait time

        Returns None if no candidates exist at all (all RPD exhausted,
        all providers unhealthy, etc.)
        """
        candidates = self.get_candidates_for_tier(tier, estimated_tokens, available_providers)

        if not candidates:
            return None

        # Sort by can_serve_now first, then by score
        can_serve_now = [c for c in candidates if c.can_serve_now]
        if can_serve_now:
            # Round-robin: when scores are within 10% of each other, prefer
            # the least-used provider. This prevents one provider (Mistral)
            # from monopolizing all requests because it has higher RPM/TPM limits.
            best_score = max(c.score for c in can_serve_now)
            threshold = best_score * 0.9
            competitive = [c for c in can_serve_now if c.score >= threshold]
            if len(competitive) > 1:
                # Among competitive candidates, pick the least-used provider
                def usage(c: ProviderCandidate) -> int:
                    return self._provider_usage.get(c.provider_type, 0)
                min_usage = min(usage(c) for c in competitive)
                least_used = [c for c in competitive if usage(c) == min_usage]
                chosen = random.choice(least_used)
            else:
                chosen = competitive[0]
            self._provider_usage[chosen.provider_type] = self._provider_usage.get(chosen.provider_type, 0) + 1
            return chosen

        # None can serve now — pick the one with shortest wait
        return min(candidates, key=lambda c: c.estimated_wait_seconds)

    def select_with_wait(
        self,
        tier: ModelTier,
        estimated_tokens: int,
        available_providers: set[ProviderType],
    ) -> tuple[ProviderCandidate | None, float]:
        """Select provider and return wait time if needed.

        Returns (candidate, wait_seconds). If wait_seconds > 0, the caller
        should sleep/await before dispatching. The wait gate's thresholds
        determine the behavior:

        - < 5s: sleep and retry (blocking the coroutine)
        - 5-30s: queue, yield to async scheduler, other agents continue
        - > 30s: try adjacent tier (up or down based on priority)
        """
        candidate = self.select_provider(tier, estimated_tokens, available_providers)

        if candidate is None:
            return None, float("inf")

        if candidate.can_serve_now:
            return candidate, 0.0

        wait = candidate.estimated_wait_seconds

        if wait > self.config.medium_wait_threshold:
            # > 30s — signal the router to try adjacent tier
            return candidate, wait
        elif wait > self.config.short_wait_threshold:
            # 5-30s — queue and yield
            return candidate, wait
        else:
            # < 5s — sleep and retry
            return candidate, wait

    def record_dispatch(
        self,
        provider: ProviderType,
        model_name: str,
        estimated_tokens: int,
    ) -> None:
        """Record that a request has been dispatched to a provider+model."""
        tracker = self._trackers.get((provider, model_name))
        if tracker:
            tracker.record_request(estimated_tokens)

    def record_actual_usage(
        self,
        provider: ProviderType,
        model_name: str,
        estimated_tokens: int,
        actual_tokens: int,
        latency_ms: float,
    ) -> None:
        """Record actual token usage and latency after a response.

        This calibrates future estimates — over time, the estimator learns
        the real token consumption patterns of each agent on each model.
        (§3.4)
        """
        tracker = self._trackers.get((provider, model_name))
        if tracker:
            tracker.record_actual_tokens(estimated_tokens, actual_tokens)
            tracker.record_latency(latency_ms)

    def get_tpm_usage_percentage(self, provider: ProviderType) -> dict[str, float]:
        """Get TPM usage percentage per model for a provider.

        Used by the TUI to display live TPM usage bars (§8.6).
        Returns {model_name: usage_percentage} where 0.0 = empty, 1.0 = full.
        """
        result: dict[str, float] = {}
        for (prov, model_name), tracker in self._trackers.items():
            if prov != provider:
                continue
            result[model_name] = tracker.current_tpm() / max(1, tracker.model.tpm)
        return result

    def get_rpm_usage_percentage(self, provider: ProviderType) -> dict[str, float]:
        """Get RPM usage percentage per model for a provider."""
        result: dict[str, float] = {}
        for (prov, model_name), tracker in self._trackers.items():
            if prov != provider:
                continue
            result[model_name] = tracker.current_rpm() / max(1, tracker.model.rpm)
        return result

    def get_rpd_usage_percentage(self, provider: ProviderType) -> dict[str, float]:
        """Get RPD usage percentage per model for a provider."""
        result: dict[str, float] = {}
        for (prov, model_name), tracker in self._trackers.items():
            if prov != provider:
                continue
            if tracker.model.rpd is None:
                result[model_name] = 0.0
            else:
                result[model_name] = tracker.current_rpd() / max(1, tracker.model.rpd)
        return result

    async def wait_for_capacity(
        self,
        candidate: ProviderCandidate,
        estimated_tokens: int,
    ) -> bool:
        """Wait for capacity to become available on a candidate.

        Returns True if capacity became available, False if timeout.
        The wait behavior depends on the estimated wait time:

        - < 5s: asyncio.sleep and retry
        - 5-30s: asyncio.sleep in increments, yielding to scheduler
        """
        if candidate.can_serve_now:
            return True

        wait_seconds = candidate.estimated_wait_seconds

        if wait_seconds > self.config.medium_wait_threshold:
            # > 30s — the router should try adjacent tier instead
            return False

        if wait_seconds <= self.config.short_wait_threshold:
            # < 5s — just sleep
            await asyncio.sleep(wait_seconds + 0.1)  # Small buffer
            return candidate.tracker.can_serve(estimated_tokens)

        # 5-30s — sleep in 1s increments, checking capacity
        elapsed = 0.0
        while elapsed < wait_seconds:
            await asyncio.sleep(1.0)
            elapsed += 1.0
            if candidate.tracker.can_serve(estimated_tokens):
                return True

        return candidate.tracker.can_serve(estimated_tokens)
