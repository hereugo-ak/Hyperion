"""Scarce, policy-gated Google Search grounding for retrieval escalation.

Agents consume the existing :class:`SearchResult` shape.  This module owns the
backend policy, quota reservation, provider metadata normalization and fail-open
outcome; callers never branch on a result's backend identity.
"""

from __future__ import annotations

import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from hyperion.config import ProviderType, get_settings
from hyperion.infra.quota import GroundingQuotaLedger, QuotaExhausted
from hyperion.router.providers.google import GoogleGroundingResponse, GoogleProvider
from hyperion.tools.query_utils import ground_query_or_raise
from hyperion.tools.searxng import HEALTHY_ENGINE_FLOOR, SearchResult, referenced_engines

logger = logging.getLogger(__name__)
_current_engagement_id: ContextVar[str] = ContextVar(
    "grounding_engagement_id", default="unknown"
)


def set_grounding_engagement_id(engagement_id: str) -> None:
    """Bind grounded-search audit entries to the current orchestration run."""
    _current_engagement_id.set(engagement_id or "unknown")


def get_grounding_engagement_id() -> str:
    return _current_engagement_id.get()


class GroundingReason(StrEnum):
    SEARXNG_DEGRADED = "searxng_degraded"
    RETRY_EXHAUSTED = "retry_exhausted"
    ATTRIBUTION_VERIFICATION = "attribution_verification"
    DIRECT_AUTHORITY = "direct_authority"

    @property
    def high_value(self) -> bool:
        return self is not GroundingReason.SEARXNG_DEGRADED


@dataclass
class GroundedSearchOutcome:
    query: str
    results: list[SearchResult] = field(default_factory=list)
    reason: GroundingReason = GroundingReason.SEARXNG_DEGRADED
    search_queries: list[str] = field(default_factory=list)
    supports: list[dict[str, Any]] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    actual_units: int = 0

    @property
    def succeeded(self) -> bool:
        return bool(self.results)


class GroundedSearchClient:
    """Quota-backed grounded retrieval. Any failure returns a constrained outcome."""

    def __init__(
        self,
        *,
        settings: Any | None = None,
        provider: GoogleProvider | None = None,
        ledger: GroundingQuotaLedger | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        config = self.settings.providers[ProviderType.GOOGLE]
        self.provider = provider or GoogleProvider(config)
        self.ledger = ledger or GroundingQuotaLedger(
            Path(self.settings.google_grounding_ledger_path),
            daily_limit=int(self.settings.google_grounding_daily_limit),
            monthly_limit=int(self.settings.google_grounding_monthly_limit),
            reserve_fraction=float(self.settings.google_grounding_reserve_fraction),
        )

    @staticmethod
    def searxng_is_degraded() -> bool:
        from hyperion.tools.engine_health import get_engine_health

        healthy = int(get_engine_health().healthy_count(referenced_engines()))
        return bool(healthy < int(HEALTHY_ENGINE_FLOOR))

    async def search(
        self,
        query: str,
        *,
        engagement_id: str | None = None,
        reason: GroundingReason,
        subject: str = "",
        geography: str = "",
    ) -> GroundedSearchOutcome:
        grounded = ground_query_or_raise(query, subject=subject, geography=geography)
        engagement_id = engagement_id or get_grounding_engagement_id()
        outcome = GroundedSearchOutcome(query=grounded, reason=reason)
        if not bool(self.settings.google_grounding_enabled):
            outcome.constraints.append("grounded retrieval disabled by configuration")
            return outcome
        if reason is GroundingReason.SEARXNG_DEGRADED and not self.searxng_is_degraded():
            outcome.constraints.append("routine escalation refused while SearXNG is healthy")
            return outcome
        if not self.provider.config.api_key:
            outcome.constraints.append("Google grounding credential unavailable")
            return outcome

        model = str(self.settings.google_grounding_model)
        expected = max(1, int(self.settings.google_grounding_max_queries_per_call))
        try:
            reservation = self.ledger.reserve(
                expected,
                model=model,
                query=grounded,
                engagement_id=engagement_id,
                high_value=reason.high_value,
            )
        except QuotaExhausted as exc:
            outcome.constraints.append(str(exc))
            return outcome

        response: GoogleGroundingResponse | None = None
        settlement = "provider_error"
        actual_units = 0
        try:
            response = await self.provider.grounded_generate(model=model, query=grounded)
            actual_units = response.billable_units
            settlement = "safety_refusal" if response.safety_refused else "success"
        except Exception as exc:  # noqa: BLE001 - fail-open retrieval contract
            outcome.constraints.append(f"Google grounding failed: {type(exc).__name__}: {exc}")
            logger.warning("grounded search failed open to SearXNG: %s", exc)
        finally:
            self.ledger.settle(reservation, actual_units, outcome=settlement)

        if response is None:
            return outcome
        outcome.actual_units = actual_units
        outcome.search_queries = response.web_search_queries
        outcome.supports = response.grounding_supports
        if response.safety_refused:
            outcome.constraints.append("Google grounding safety refusal")
            return outcome
        outcome.results = self._normalize_results(response)
        if not outcome.results:
            outcome.constraints.append("Google grounding returned no attributable sources")
        return outcome

    @staticmethod
    def _normalize_results(response: GoogleGroundingResponse) -> list[SearchResult]:
        results: list[SearchResult] = []
        seen: set[str] = set()
        for index, chunk in enumerate(response.grounding_chunks):
            web = chunk.get("web", chunk)
            if not isinstance(web, dict):
                continue
            url = str(web.get("uri") or web.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            title = str(web.get("title") or url)
            snippets = [
                str(support.get("segment", {}).get("text", ""))
                for support in response.grounding_supports
                if index in support.get("groundingChunkIndices", [])
                and isinstance(support.get("segment"), dict)
            ]
            results.append(SearchResult(
                title=title,
                url=url,
                snippet=" ".join(part for part in snippets if part),
                engine="google-search-grounding",
                backend="gemini",
            ))
        return results
