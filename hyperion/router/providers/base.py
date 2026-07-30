"""
HYPERION BaseProvider — the common interface for all 4 LLM providers.

This is NOT a generic API client. Each provider implementation tracks:
- Health (last response, consecutive failures, circuit breaker state)
- Rate limit capacity (delegated to the wait gate's SlidingWindowTracker)
- Model availability (which models on this provider serve which tiers)

All providers expose OpenAI-compatible APIs, so we use the openai client
with different base_urls. The BaseProvider abstracts the common patterns:
health checking, error classification, and response normalization.
(§3.2, §3.6)
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from openai import AsyncOpenAI

from hyperion.config import ModelSpec, ModelTier, ProviderConfig, ProviderType


@dataclass
class RouterFailure:
    """Structured diagnosis for a total routing failure (P2-29).

    Before this existed, every "no candidate anywhere" outcome was
    returned as ``provider=ProviderType.GOOGLE, model="none"`` with a
    quota-exhaustion message, so a hard credential failure on one
    provider was reported to the operator as rate limiting on a provider
    it had never successfully contacted. The operator needs to know
    which providers were considered, which tiers were walked, and why
    each provider was skipped.

    skip_reasons vocabulary:
      "health_open"             circuit breaker / cooldown / unavailable
      "unauthenticated"         401/403 credential failure (NOT quota)
      "budget_exhausted"        daily budget planner filtered it out
      "predicted_rate_limited"  sliding window tracker > 85% utilized
      "no_model_for_tier"       provider has no non-deprecated model for the tier
      "wait_exceeded_threshold" candidate wait beyond the medium threshold
      "no_candidate"            wait gate returned no candidate
      "auth_error"              provider call failed with an auth error
    """

    tiers_attempted: list[ModelTier] = field(default_factory=list)
    providers_considered: dict[str, list[str]] = field(default_factory=dict)
    skip_reasons: dict[ProviderType, str] = field(default_factory=dict)

    def render(self) -> str:
        """One-line human-readable diagnosis for logs and operators."""
        reasons = ", ".join(
            f"{pt.value}={reason}" for pt, reason in sorted(
                self.skip_reasons.items(), key=lambda kv: kv[0].value
            )
        ) or "no providers considered"
        tiers = " -> ".join(t.value for t in self.tiers_attempted) or "none"
        return f"tiers attempted: {tiers}; skip reasons: {reasons}"


def _coerce_content(raw: Any) -> str:
    """Flatten any OpenAI-compatible `message.content` shape into a str.

    Handles, in order of how often we actually see them:

    - ``None`` / falsy            -> ``""``
    - ``str``                     -> unchanged
    - ``list`` of content blocks  -> the text parts joined
      (Mistral & some NVIDIA models return this; blocks may be dicts with
      ``text``/``content``/``value`` keys, or pydantic objects with a
      ``.text`` attribute, or bare strings)
    - anything else               -> ``str(raw)``

    Never raises: a malformed block is skipped rather than allowed to take
    down the calling agent.
    """
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        parts: list[str] = []
        for block in raw:
            if not block:
                continue
            if isinstance(block, str):
                parts.append(block)
                continue
            if isinstance(block, dict):
                for key in ("text", "content", "value"):
                    val = block.get(key)
                    if isinstance(val, str) and val:
                        parts.append(val)
                        break
                    # Nested blocks, e.g. {"content": [{"text": "..."}]}
                    if isinstance(val, (list, tuple)) and val:
                        nested = _coerce_content(val)
                        if nested:
                            parts.append(nested)
                        break
                continue
            # Pydantic / SDK objects exposing .text or .content
            for attr in ("text", "content", "value"):
                val = getattr(block, attr, None)
                if isinstance(val, str) and val:
                    parts.append(val)
                    break
        return "".join(parts)
    return str(raw)


class ProviderStatus(str, Enum):
    """Health status of a provider (§3.6 Failover Handler)."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    COOLDOWN = "cooldown"
    CIRCUIT_OPEN = "circuit_open"
    UNAVAILABLE = "unavailable"
    # P2-29: a 401/403 credential failure is categorically different from
    # rate limiting and must never be aggregated into quota reporting.
    UNAUTHENTICATED = "unauthenticated"


@dataclass
class ProviderHealth:
    """Real-time health tracking for a provider (§3.2, §3.6).

    The failover handler uses this to decide when to skip a provider:
    - 429 -> mark cooldown (60s)
    - 500/503 -> health check, circuit breaker (3 failures -> 5-min cooldown)
    - Timeout -> exponential backoff (1s, 2s, 4s, max 3 retries)
    - Network error -> immediate failover, no retry
    """

    status: ProviderStatus = ProviderStatus.HEALTHY
    last_response_at: float = 0.0
    last_error: str | None = None
    consecutive_failures: int = 0
    cooldown_until: float = 0.0
    total_requests: int = 0
    total_errors: int = 0
    total_429s: int = 0
    total_timeouts: int = 0

    def record_success(self) -> None:
        self.status = ProviderStatus.HEALTHY
        self.last_response_at = time.time()
        self.consecutive_failures = 0
        self.total_requests += 1

    def record_429(self, cooldown_seconds: int = 60) -> None:
        self.status = ProviderStatus.COOLDOWN
        self.cooldown_until = time.time() + cooldown_seconds
        self.last_error = "429 Rate Limited"
        self.total_429s += 1
        self.total_errors += 1
        self.total_requests += 1

    def record_500(self) -> None:
        self.consecutive_failures += 1
        self.last_error = "500/503 Server Error"
        self.total_errors += 1
        self.total_requests += 1
        if self.consecutive_failures >= 3:
            self.trip_circuit_breaker()

    def record_timeout(self) -> None:
        self.consecutive_failures += 1
        self.last_error = "Timeout"
        self.total_timeouts += 1
        self.total_errors += 1
        self.total_requests += 1
        if self.consecutive_failures >= 3:
            self.trip_circuit_breaker()

    def record_network_error(self) -> None:
        self.status = ProviderStatus.UNAVAILABLE
        self.last_error = "Network Error"
        self.total_errors += 1
        self.total_requests += 1

    def record_auth_error(self) -> None:
        """401/403 from the provider: the credential is dead (P2-29).

        This is categorically NOT rate limiting. It must surface as
        UNAUTHENTICATED so the operator replaces the key instead of
        waiting out an imaginary quota window.
        """
        self.status = ProviderStatus.UNAUTHENTICATED
        self.last_error = "401/403 Authentication Error"
        self.total_errors += 1
        self.total_requests += 1

    def trip_circuit_breaker(self, cooldown_seconds: int = 300) -> None:
        self.status = ProviderStatus.CIRCUIT_OPEN
        self.cooldown_until = time.time() + cooldown_seconds
        self.last_error = f"Circuit breaker tripped ({self.consecutive_failures} consecutive failures)"

    def is_available(self) -> bool:
        now = time.time()
        if self.status in (ProviderStatus.COOLDOWN, ProviderStatus.CIRCUIT_OPEN):
            if now >= self.cooldown_until:
                self.status = ProviderStatus.HEALTHY
                self.consecutive_failures = 0
                return True
            return False
        return self.status != ProviderStatus.UNAVAILABLE

    def uptime_percentage(self) -> float:
        if self.total_requests == 0:
            return 100.0
        return ((self.total_requests - self.total_errors) / self.total_requests) * 100.0


@dataclass
class RouterResponse:
    """Normalized response from any provider.

    Agents don't know which provider they're using — they just get content
    and token usage. (§9: "Agents don't know which provider they're using")
    """

    content: str
    model: str
    provider: ProviderType
    tier: ModelTier
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    latency_ms: float = 0.0
    success: bool = True
    error: str | None = None
    raw_response: Any | None = None
    failure: RouterFailure | None = None

    def __post_init__(self) -> None:
        """Enforce the `content: str` contract at the type boundary.

        Defense in depth. `_coerce_content` already normalizes the provider
        path, but this dataclass is the single type through which every LLM
        result reaches every agent, and ~every agent calls
        `response.content.strip()`. Guaranteeing str here means no future
        provider quirk or new construction site can reintroduce the
        `'list' object has no attribute 'strip'` class of crash.
        """
        if not isinstance(self.content, str):
            self.content = _coerce_content(self.content)


class BaseProvider:
    """Base class for all LLM providers.

    Each provider (Google, NVIDIA, Cerebras, Groq) extends this class.
    The common patterns are:
    - Async OpenAI client with provider-specific base_url
    - Health tracking (ProviderHealth)
    - Model registry (which models serve which tiers)
    - Request execution with error classification

    The wait gate calls can_serve() before dispatching to check capacity.
    The router calls complete() to execute a request.
    """

    def __init__(self, config: ProviderConfig) -> None:
        self.config = config
        self.health = ProviderHealth()
        self._client: AsyncOpenAI | None = None

    @property
    def provider_type(self) -> ProviderType:
        raise NotImplementedError

    @property
    def client(self) -> AsyncOpenAI:
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.config.api_key,
                base_url=self.config.base_url,
                timeout=60.0,
                max_retries=2,
            )
        return self._client

    def get_models_for_tier(self, tier: ModelTier) -> list[ModelSpec]:
        return self.config.get_models_for_tier(tier)

    def can_serve(self, tier: ModelTier) -> bool:
        return self.health.is_available() and len(self.get_models_for_tier(tier)) > 0

    async def complete(
        self,
        model: str,
        messages: list[dict[str, str]],
        tier: ModelTier,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> RouterResponse:
        """Execute a completion request against this provider.

        Error handling follows §3.6:
        - 429 -> record_429, raise for failover
        - 500/503 -> record_500, raise for failover
        - Timeout -> record_timeout, raise for failover
        - Network error -> record_network_error, raise for failover
        """
        start = time.time()

        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if max_tokens is not None:
            kwargs["max_tokens"] = max_tokens
        if response_format is not None:
            kwargs["response_format"] = response_format

        try:
            response = await self.client.chat.completions.create(**kwargs)

            latency = (time.time() - start) * 1000
            self.health.record_success()

            input_tokens = 0
            output_tokens = 0
            if response.usage:
                input_tokens = response.usage.prompt_tokens or 0
                output_tokens = response.usage.completion_tokens or 0

            # Normalize the message content to a plain str.
            #
            # `RouterResponse.content` is typed `str` and ~every caller does
            # `response.content.strip()`. But the OpenAI-compatible wire format
            # allows `message.content` to be a LIST of content blocks
            # (e.g. [{"type":"text","text":"..."}]), and Mistral in particular
            # returns that shape. Passing the list straight through produced
            # the production crash `'list' object has no attribute 'strip'`
            # in ma_analyst (and would eventually hit every other agent).
            # Normalizing here fixes it once for all 5 providers and all
            # call sites, rather than sprinkling isinstance checks around.
            content = _coerce_content(
                response.choices[0].message.content if response.choices else None
            )

            return RouterResponse(
                content=content,
                model=model,
                provider=self.provider_type,
                tier=tier,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=input_tokens + output_tokens,
                latency_ms=latency,
                raw_response=response,
            )

        except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
            error_str = str(e)
            latency = (time.time() - start) * 1000

            if "429" in error_str or "rate_limit" in error_str.lower():
                self.health.record_429()
            elif "500" in error_str or "503" in error_str or "server_error" in error_str.lower():
                self.health.record_500()
            elif "timeout" in error_str.lower() or "timed out" in error_str.lower():
                self.health.record_timeout()
            else:
                self.health.record_network_error()

            return RouterResponse(
                content="",
                model=model,
                provider=self.provider_type,
                tier=tier,
                latency_ms=latency,
                success=False,
                error=error_str,
            )

    async def health_check(self) -> bool:
        """Check if the provider is reachable by listing models.

        Used by the failover handler after a 500/503 to determine if the
        provider has recovered before routing requests back to it. (§3.6)
        """
        try:
            await self.client.models.list()
            if self.health.status in (ProviderStatus.UNAVAILABLE, ProviderStatus.CIRCUIT_OPEN):
                self.health.status = ProviderStatus.HEALTHY
                self.health.consecutive_failures = 0
            return True
        except Exception:  # noqa: BLE001 - best-effort, returns a safe default
            return False
