"""
HYPERION SpeculativeRacer — race two providers in parallel for DEEP calls.

For critical-path DEEP calls (synthesis, quality gate), the router can
speculatively dispatch the same request to two providers simultaneously
and use whichever response arrives first. This cuts p99 latency on the
slowest tier without wasting tokens — the loser's response is discarded.

This is the proportionate adoption of speculative execution (IV.1.5):
only used for DEEP tier (where latency variance is highest), and only
when two+ providers are available and not rate-limited.

Usage (inside the router)::

    from hyperion.router.speculative_racer import SpeculativeRacer

    racer = SpeculativeRacer(router=self)
    response = await racer.race(
        tier=ModelTier.DEEP,
        messages=messages,
        agent_name="synthesis_lead",
        ...
    )
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from hyperion.config import ModelTier, ProviderType
from hyperion.obs import trace
from hyperion.router.budget import TaskUrgency
from hyperion.router.providers.base import RouterResponse

logger = logging.getLogger(__name__)


class SpeculativeRacer:
    """Race two providers in parallel for critical-path DEEP calls.

    Dispatches the same request to two providers simultaneously and
    returns whichever response arrives first. The loser is cancelled.

    Only used for DEEP tier (highest latency variance). For lower tiers,
    the overhead of dispatching twice outweighs the latency benefit.
    """

    def __init__(self, router: Any) -> None:
        self.router = router

    async def race(
        self,
        tier: ModelTier,
        messages: list[dict[str, str]],
        agent_name: str = "",
        urgency: TaskUrgency = TaskUrgency.NORMAL,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
    ) -> RouterResponse:
        """Race two providers in parallel, return the first successful response.

        Falls back to sequential single-provider dispatch if fewer than
        two providers are available.
        """
        # Get available providers for this tier
        available = self.router.get_available_providers(tier, urgency)
        if len(available) < 2:
            # Not enough providers to race — fall back to normal dispatch
            trace("speculative", tier=tier.value, status="fallback",
                  reason="insufficient_providers", count=len(available))
            return await self.router.complete(
                tier=tier,
                messages=messages,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                _skip_speculative=True,
            )

        # Pick top 2 providers by priority
        ordered = self.router._sort_providers_by_priority(tier, available)
        candidates = [
            p for p in ordered
            if not self.router._predicted_rate_limited(p)
        ][:2]

        if len(candidates) < 2:
            trace("speculative", tier=tier.value, status="fallback",
                  reason="rate_limited", available=len(candidates))
            return await self.router.complete(
                tier=tier,
                messages=messages,
                agent_name=agent_name,
                urgency=urgency,
                temperature=temperature,
                max_tokens=max_tokens,
                response_format=response_format,
                _skip_speculative=True,
            )

        trace("speculative", tier=tier.value, status="racing",
              providers=[p.value for p in candidates], agent=agent_name)

        # Dispatch to both providers in parallel
        tasks: list[asyncio.Task] = []
        for provider_type in candidates:
            task = asyncio.create_task(
                self._dispatch_single(
                    tier=tier,
                    messages=messages,
                    provider_type=provider_type,
                    agent_name=agent_name,
                    urgency=urgency,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    response_format=response_format,
                ),
                name=f"speculative_{provider_type.value}",
            )
            tasks.append(task)

        # Wait for the first successful response
        done, pending = await asyncio.wait(
            tasks,
            return_when=asyncio.FIRST_COMPLETED,
        )

        # Cancel any still-running tasks
        for task in pending:
            task.cancel()

        # Find the successful response from completed tasks
        for task in done:
            try:
                result = task.result()
                if result and result.success:
                    trace("speculative", tier=tier.value, status="won",
                          provider=result.provider.value,
                          latency_ms=result.latency_ms)
                    return result
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(f"Speculative race task error: {e}")

        # If no task succeeded, try the normal path as fallback
        trace("speculative", tier=tier.value, status="all_failed")
        return await self.router.complete(
            tier=tier,
            messages=messages,
            agent_name=agent_name,
            urgency=urgency,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
            _skip_speculative=True,
        )

    async def _dispatch_single(
        self,
        tier: ModelTier,
        messages: list[dict[str, str]],
        provider_type: ProviderType,
        agent_name: str,
        urgency: TaskUrgency,
        temperature: float,
        max_tokens: int | None,
        response_format: dict[str, str] | None,
    ) -> RouterResponse:
        """Dispatch to a single provider via the router's _try_tier method."""
        return await self.router._try_tier(
            tier=tier,
            messages=messages,
            estimated_tokens=self.router.estimator.estimate_tokens(
                system_prompt=next(
                    (m["content"] for m in messages if m.get("role") == "system"), ""
                ),
                user_prompt=next(
                    (m["content"] for m in messages if m.get("role") == "user"), ""
                ),
                tier=tier,
                agent_name=agent_name,
            ),
            agent_name=agent_name,
            urgency=urgency,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format=response_format,
        )
