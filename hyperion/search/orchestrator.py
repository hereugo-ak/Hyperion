"""SearchOrchestrator — the canonical deterministic fallback chain (§5).

    SearXNG -> You.com -> Exa   (loop the top-3 twice)   -> Tavily -> Yep

- Strict fixed order — no dynamic routing.
- Per-run budget buckets (§9) and suspension (§10); both reset at engagement
  start via ``SearchOrchestrator.reset_run()`` (hooked into the orchestrator's
  existing budget reset).
- Dedupe by (netloc, path) with tracking params stripped; cap at MAX_RESULTS.
- Metrics counters per provider for the run log.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.adapters.exa import ExaAdapter
from hyperion.search.adapters.searxng import SearxNGAdapter
from hyperion.search.adapters.tavily import TavilyAdapter
from hyperion.search.adapters.yep import YepAdapter
from hyperion.search.adapters.you import YouAdapter
from hyperion.search.budget import BudgetRegistry
from hyperion.search.suspension import SuspensionRegistry
from hyperion.search.types import SearchResult, dedupe_results

logger = logging.getLogger(__name__)

MIN_RESULTS = 5
MAX_RESULTS = 15
LOOP_RETRIES = 1          # SearXNG->You->Exa is tried a second time

#: Adapter classes in canonical order.
TIERS_LOOP = (SearxNGAdapter, YouAdapter, ExaAdapter)
TIERS_TAIL = (TavilyAdapter, YepAdapter)


@dataclass
class ProviderMetrics:
    calls_total: int = 0
    calls_success: int = 0
    calls_429: int = 0
    calls_403: int = 0
    calls_5xx: int = 0
    calls_timeout: int = 0
    results_total: int = 0
    latency_ms: list[float] = field(default_factory=list)

    @property
    def latency_p50_ms(self) -> float:
        if not self.latency_ms:
            return 0.0
        return sorted(self.latency_ms)[len(self.latency_ms) // 2]

    @property
    def latency_p95_ms(self) -> float:
        if not self.latency_ms:
            return 0.0
        s = sorted(self.latency_ms)
        return s[min(len(s) - 1, int(len(s) * 0.95))]

    def snapshot(self) -> dict[str, Any]:
        return {
            "calls_total": self.calls_total,
            "calls_success": self.calls_success,
            "calls_429": self.calls_429,
            "calls_403": self.calls_403,
            "calls_5xx": self.calls_5xx,
            "calls_timeout": self.calls_timeout,
            "results_total": self.results_total,
            "latency_p50_ms": round(self.latency_p50_ms, 1),
            "latency_p95_ms": round(self.latency_p95_ms, 1),
        }


class SearchOrchestrator:
    """Per-run multi-provider search. Not shared across engagements."""

    def __init__(
        self,
        adapters: dict[type, BaseAdapter] | None = None,
        budget: BudgetRegistry | None = None,
        suspension: SuspensionRegistry | None = None,
    ) -> None:
        self.adapters: dict[type, BaseAdapter] = adapters or {}
        self.budget = budget or BudgetRegistry()
        self.suspension = suspension or SuspensionRegistry()
        self.metrics: dict[str, ProviderMetrics] = {}
        self._loop_task: asyncio.Task[Any] | None = None

    # ── factory ────────────────────────────────────────────────────────────

    @classmethod
    def from_settings(
        cls,
        settings: Any | None = None,
        *,
        budget: BudgetRegistry | None = None,
        suspension: SuspensionRegistry | None = None,
    ) -> "SearchOrchestrator":
        adapters = {
            SearxNGAdapter: SearxNGAdapter(settings),
            YouAdapter: YouAdapter(settings),
            ExaAdapter: ExaAdapter(settings),
            TavilyAdapter: TavilyAdapter(settings),
            YepAdapter: YepAdapter(settings),
        }
        return cls(adapters, budget=budget, suspension=suspension)

    # ── per-run lifecycle ──────────────────────────────────────────────────

    def reset_run(self) -> None:
        """Reset per-run budget + suspension + metrics (call at engagement start)."""
        self.budget.reset()
        self.suspension.reset()
        self.metrics.clear()

    # ── the canonical chain (§5.1) ─────────────────────────────────────────

    async def search(
        self,
        query: str,
        num_results: int = MAX_RESULTS,
    ) -> list[SearchResult]:
        """Run the deterministic chain and return deduped results (≤ MAX_RESULTS)."""
        pool: list[SearchResult] = []
        for _attempt in range(LOOP_RETRIES + 1):
            for adapter_cls in TIERS_LOOP:
                got = await self._call(adapter_cls, query, num_results)
                pool.extend(got)
                if len(dedupe_results(pool)) >= MIN_RESULTS:
                    return self._finish(pool, num_results)
        for adapter_cls in TIERS_TAIL:
            got = await self._call(adapter_cls, query, num_results)
            pool.extend(got)
            if len(dedupe_results(pool)) >= MIN_RESULTS:
                return self._finish(pool, num_results)
        return self._finish(pool, num_results)

    def _finish(self, pool: list[SearchResult], num_results: int) -> list[SearchResult]:
        deduped = dedupe_results(pool)
        return deduped[: max(1, min(num_results, MAX_RESULTS))]

    # ── one provider call ──────────────────────────────────────────────────

    async def _call(
        self, adapter_cls: type, query: str, num_results: int
    ) -> list[SearchResult]:
        name = adapter_cls.name
        adapter = self.adapters.get(adapter_cls)
        if adapter is None:
            return []
        if not self.suspension.is_available(name):
            return []
        if not self.budget.can_spend(name):
            self.suspension.record_error(name, "bucket_exhausted")
            logger.info("search provider %s: bucket exhausted — demoted for run", name)
            return []
        if not await self.budget.spend(name):
            self.suspension.record_error(name, "bucket_exhausted")
            return []

        metrics = self.metrics.setdefault(name, ProviderMetrics())
        metrics.calls_total += 1
        started = time.monotonic()
        try:
            results = await adapter.search(query, num_results)
        except TransientProviderError as exc:
            metrics.calls_total -= 1  # not a successful call; count the signal
            signal = exc.signal
            self._count_signal(metrics, signal)
            self.suspension.record_error(name, signal)
            logger.warning(
                "search provider %s %s — suspended for run (query=%r)",
                name, signal, query[:60],
            )
            return []
        except Exception as exc:  # noqa: BLE001 - one provider must not kill the chain
            metrics.calls_total -= 1
            logger.warning("search provider %s failed: %s", name, exc)
            self.suspension.record_error(name, "5xx")
            return []
        finally:
            metrics.latency_ms.append((time.monotonic() - started) * 1000)

        metrics.calls_success += 1
        metrics.results_total += len(results)
        if results:
            self.suspension.record_success(name)
        else:
            down_weighted = self.suspension.record_empty(name)
            if down_weighted:
                logger.info(
                    "search provider %s: %d consecutive empty results — "
                    "down-weighted (still in chain)", name,
                    self.suspension._state(name).consec_empties,
                )
        return results

    @staticmethod
    def _count_signal(metrics: ProviderMetrics, signal: str) -> None:
        if signal == "429":
            metrics.calls_429 += 1
        elif signal == "403":
            metrics.calls_403 += 1
        elif signal == "5xx":
            metrics.calls_5xx += 1
        elif signal == "timeout":
            metrics.calls_timeout += 1

    # ── telemetry ──────────────────────────────────────────────────────────

    def metrics_snapshot(self) -> dict[str, dict[str, Any]]:
        return {name: m.snapshot() for name, m in self.metrics.items()}

    async def close(self) -> None:
        for adapter in self.adapters.values():
            try:
                await adapter.close()
            except Exception:  # noqa: BLE001 - close is best-effort
                pass


# ── module singleton: one per run, reset at engagement start ────────────────

_singleton: SearchOrchestrator | None = None


def get_search_orchestrator(settings: Any | None = None) -> SearchOrchestrator:
    """Return the process-wide orchestrator, creating it on first use."""
    global _singleton
    if _singleton is None:
        _singleton = SearchOrchestrator.from_settings(settings)
    return _singleton


def reset_search_run() -> None:
    """Reset per-run state — called alongside SearxNGClient.reset_budget()."""
    if _singleton is not None:
        _singleton.reset_run()
