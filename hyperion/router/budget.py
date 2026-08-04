"""Persistent request, token, and cost controls for HYPERION's LLM router.

Daily limits are shared across engagements and process restarts. Usage is stored
by provider, model, and UTC date in SQLite; candidate reservation is atomic so
concurrent agents cannot all pass a stale check before dispatch.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path

from hyperion.config import ModelSpec, ModelTier, ProviderType
from hyperion.infra.paths import project_root


class TaskUrgency(str, Enum):
    HIGH = "high"
    NORMAL = "normal"
    LOW = "low"


_PROVIDER_DAILY_BUDGETS: dict[ProviderType, int] = {
    ProviderType.GOOGLE: 29_460,
    ProviderType.GROQ: 18_400,
    # NVIDIA publishes per-minute model limits, not a 33-request daily cap.
    # The old synthetic credit-derived ceiling disabled NIM early in every
    # engagement and surfaced as `nvidia=budget_exhausted` despite available
    # RPM. Keep only a high defensive process ceiling; WaitGate enforces RPM.
    ProviderType.NVIDIA: 1_000_000,
    # RPD is only a defensive ceiling; BudgetStore.reserve() enforces each
    # Cerebras model's configured TPD limit as the binding constraint.
    ProviderType.CEREBRAS: 10_000,
    ProviderType.MISTRAL: 86_400,
}

_PROVIDER_SCARCITY: dict[ProviderType, int] = {
    ProviderType.NVIDIA: 0,
    ProviderType.CEREBRAS: 1,
    ProviderType.GROQ: 2,
    ProviderType.GOOGLE: 3,
    ProviderType.MISTRAL: 4,
}

# USD per million input/output tokens. Pricing snapshot: 2026-08-01.
# Sources (official provider pricing pages, retrieved 2026-08-01):
# https://ai.google.dev/gemini-api/docs/pricing
# https://build.nvidia.com/pricing ; https://www.cerebras.ai/pricing
# https://groq.com/pricing ; https://mistral.ai/pricing
# These are planning estimates, not invoices. Exact model rows are preferred;
# provider defaults keep newly configured models visible rather than cost-free.
_MODEL_PRICES_PER_MILLION: dict[tuple[ProviderType, str], tuple[float, float]] = {
    (ProviderType.GOOGLE, "gemma-4-31b"): (0.10, 0.20),
    (ProviderType.GOOGLE, "gemma-4-26b"): (0.10, 0.20),
    (ProviderType.GOOGLE, "gemini-3.5-flash-lite"): (0.10, 0.40),
    (ProviderType.NVIDIA, "nvidia/nemotron-3-super-120b-a12b"): (0.60, 0.60),
    (ProviderType.NVIDIA, "nvidia/nemotron-3-ultra-550b-a55b"): (1.20, 1.20),
    (ProviderType.NVIDIA, "nvidia/nemotron-3-nano-30b-a3b"): (0.20, 0.20),
    (ProviderType.CEREBRAS, "gpt-oss-120b"): (0.85, 0.85),
    (ProviderType.CEREBRAS, "gemma-4-31b"): (0.60, 0.60),
    (ProviderType.GROQ, "gpt-oss-120b"): (0.15, 0.60),
    (ProviderType.GROQ, "llama-3.3-70b-versatile"): (0.59, 0.79),
    (ProviderType.GROQ, "llama-3.1-8b-instant"): (0.05, 0.08),
    (ProviderType.GROQ, "llama-4-scout-17b"): (0.11, 0.34),
    (ProviderType.GROQ, "qwen-3-32b"): (0.29, 0.59),
    (ProviderType.GROQ, "gpt-oss-20b"): (0.10, 0.50),
    (ProviderType.MISTRAL, "mistral-large-2512"): (2.00, 6.00),
    (ProviderType.MISTRAL, "mistral-medium-2605"): (0.40, 2.00),
    (ProviderType.MISTRAL, "mistral-medium-2508"): (0.40, 2.00),
    (ProviderType.MISTRAL, "ministral-14b-2512"): (0.15, 0.15),
    (ProviderType.MISTRAL, "mistral-small-2603"): (0.10, 0.30),
    (ProviderType.MISTRAL, "devstral-2512"): (0.40, 2.00),
    (ProviderType.MISTRAL, "ministral-3b-2512"): (0.04, 0.04),
}
_PROVIDER_DEFAULT_PRICES: dict[ProviderType, tuple[float, float]] = {
    ProviderType.GOOGLE: (0.10, 0.40),
    ProviderType.NVIDIA: (0.60, 0.60),
    ProviderType.CEREBRAS: (0.85, 0.85),
    ProviderType.GROQ: (0.20, 0.60),
    ProviderType.MISTRAL: (0.40, 2.00),
}


def _utc_date() -> str:
    return datetime.now(UTC).date().isoformat()


def _default_db_path() -> Path:
    configured = os.environ.get("HYPERION_BUDGET_DB", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (project_root() / "artifacts" / "shared" / "llm_budget.sqlite").resolve()


class BudgetStore:
    """SQLite-backed shared daily ledger with atomic reservations."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path is not None else _default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
            conn.execute(
                """CREATE TABLE IF NOT EXISTS daily_usage (
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    utc_date TEXT NOT NULL,
                    requests INTEGER NOT NULL DEFAULT 0,
                    reserved_tokens INTEGER NOT NULL DEFAULT 0,
                    actual_tokens INTEGER NOT NULL DEFAULT 0,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    cost_usd REAL NOT NULL DEFAULT 0,
                    PRIMARY KEY (provider, model, utc_date)
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10, isolation_level=None)

    def provider_requests(self, provider: ProviderType) -> int:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(requests), 0) FROM daily_usage "
                "WHERE provider=? AND utc_date=?",
                (provider.value, _utc_date()),
            ).fetchone()
        return int(row[0] if row else 0)

    def model_usage(self, provider: ProviderType, model: str) -> tuple[int, int]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT requests, reserved_tokens + actual_tokens FROM daily_usage "
                "WHERE provider=? AND model=? AND utc_date=?",
                (provider.value, model, _utc_date()),
            ).fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    def reserve(
        self,
        provider: ProviderType,
        model: ModelSpec,
        estimated_tokens: int,
        total_budget: int,
        reserve_fraction: float,
        urgency: TaskUrgency,
    ) -> bool:
        """Atomically check RPD/TPD and reserve one dispatch."""
        estimated_tokens = max(0, int(estimated_tokens))
        date = _utc_date()
        with self._lock, self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            provider_requests = int(
                conn.execute(
                    "SELECT COALESCE(SUM(requests), 0) FROM daily_usage "
                    "WHERE provider=? AND utc_date=?",
                    (provider.value, date),
                ).fetchone()[0]
            )
            ceiling = total_budget
            if urgency != TaskUrgency.HIGH:
                ceiling -= int(total_budget * reserve_fraction)
            model_requests, model_tokens = self._model_usage_in_transaction(
                conn, provider, model.name, date
            )
            allowed = provider_requests < ceiling
            if model.rpd is not None:
                allowed = allowed and model_requests < model.rpd
            if model.tpd is not None:
                allowed = allowed and model_tokens + estimated_tokens <= model.tpd
            if not allowed:
                conn.rollback()
                return False
            conn.execute(
                """INSERT INTO daily_usage
                   (provider, model, utc_date, requests, reserved_tokens)
                   VALUES (?, ?, ?, 1, ?)
                   ON CONFLICT(provider, model, utc_date) DO UPDATE SET
                     requests=requests+1,
                     reserved_tokens=reserved_tokens+excluded.reserved_tokens""",
                (provider.value, model.name, date, estimated_tokens),
            )
            conn.commit()
            return True

    @staticmethod
    def _model_usage_in_transaction(
        conn: sqlite3.Connection,
        provider: ProviderType,
        model: str,
        date: str,
    ) -> tuple[int, int]:
        row = conn.execute(
            "SELECT requests, reserved_tokens + actual_tokens FROM daily_usage "
            "WHERE provider=? AND model=? AND utc_date=?",
            (provider.value, model, date),
        ).fetchone()
        return (int(row[0]), int(row[1])) if row else (0, 0)

    def consume_legacy(self, provider: ProviderType, model: str, count: int = 1) -> None:
        """Persist the historical request-only API used by external callers."""
        with self._lock, self._connect() as conn:
            conn.execute(
                """INSERT INTO daily_usage(provider, model, utc_date, requests)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(provider, model, utc_date) DO UPDATE SET
                     requests=requests+excluded.requests""",
                (provider.value, model, _utc_date(), max(0, count)),
            )

    def reconcile(
        self,
        provider: ProviderType,
        model: str,
        estimated_tokens: int,
        actual_tokens: int,
        input_tokens: int,
        output_tokens: int,
        cost_usd: float,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE daily_usage SET
                     reserved_tokens=MAX(0, reserved_tokens-?),
                     actual_tokens=actual_tokens+?,
                     input_tokens=input_tokens+?,
                     output_tokens=output_tokens+?,
                     cost_usd=cost_usd+?
                   WHERE provider=? AND model=? AND utc_date=?""",
                (
                    max(0, estimated_tokens),
                    max(0, actual_tokens),
                    max(0, input_tokens),
                    max(0, output_tokens),
                    max(0.0, cost_usd),
                    provider.value,
                    model,
                    _utc_date(),
                ),
            )

    def release_reservation(
        self,
        provider: ProviderType,
        model: str,
        estimated_tokens: int,
    ) -> None:
        """Release token capacity after a failed, non-billable completion.

        The request remains counted because it reached the provider and used an
        RPM slot. Only the speculative token reservation is released.
        """
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE daily_usage SET
                     reserved_tokens=MAX(0, reserved_tokens-?)
                   WHERE provider=? AND model=? AND utc_date=?""",
                (max(0, estimated_tokens), provider.value, model, _utc_date()),
            )

    def refund(
        self,
        provider: ProviderType,
        model: str,
        count: int = 1,
        estimated_tokens: int = 0,
    ) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                """UPDATE daily_usage SET
                     requests=MAX(0, requests-?),
                     reserved_tokens=MAX(0, reserved_tokens-?)
                   WHERE provider=? AND model=? AND utc_date=?""",
                (
                    max(0, count),
                    max(0, estimated_tokens),
                    provider.value,
                    model,
                    _utc_date(),
                ),
            )

    def daily_cost(self) -> float:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(cost_usd), 0) FROM daily_usage WHERE utc_date=?",
                (_utc_date(),),
            ).fetchone()
        return float(row[0] if row else 0.0)


@dataclass
class ProviderBudget:
    provider: ProviderType
    total_budget: int
    store: BudgetStore
    reserve_fraction: float = 0.20

    @property
    def consumed(self) -> int:
        return self.store.provider_requests(self.provider)

    @property
    def available(self) -> int:
        reserved = int(self.total_budget * self.reserve_fraction)
        return max(0, self.total_budget - self.consumed - reserved)

    @property
    def available_with_reserve(self) -> int:
        return max(0, self.total_budget - self.consumed)

    @property
    def usage_percentage(self) -> float:
        return self.consumed / self.total_budget if self.total_budget else 0.0

    @property
    def is_reserve_available(self) -> bool:
        return self.available > 0

    def can_consume(self, urgency: TaskUrgency = TaskUrgency.NORMAL) -> bool:
        return (
            self.available_with_reserve > 0 if urgency == TaskUrgency.HIGH else self.available > 0
        )

    def consume(self, model_name: str, count: int = 1) -> None:
        self.store.consume_legacy(self.provider, model_name, count)

    def refund(self, model_name: str, count: int = 1, estimated_tokens: int = 0) -> None:
        self.store.refund(self.provider, model_name, count, estimated_tokens)

    def remaining_for_model(self, model: ModelSpec) -> int | None:
        requests, _tokens = self.store.model_usage(self.provider, model.name)
        if model.rpd is not None:
            return max(0, model.rpd - requests)
        return self.available

    def remaining_tokens_for_model(self, model: ModelSpec) -> int | None:
        if model.tpd is None:
            return None
        _requests, tokens = self.store.model_usage(self.provider, model.name)
        return max(0, model.tpd - tokens)


class DailyBudgetPlanner:
    """Persistent daily planner for request, token, and engagement cost limits."""

    def __init__(
        self,
        reserve_fraction: float = 0.20,
        db_path: str | Path | None = None,
    ) -> None:
        self.store = BudgetStore(db_path)
        self._lock = threading.RLock()
        self._engagement_cost_usd = 0.0
        self._budgets = {
            provider: ProviderBudget(provider, budget, self.store, reserve_fraction)
            for provider, budget in _PROVIDER_DAILY_BUDGETS.items()
        }

    def get_budget(self, provider: ProviderType) -> ProviderBudget:
        return self._budgets[provider]

    def can_serve(
        self,
        provider: ProviderType,
        model: ModelSpec,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
        estimated_tokens: int = 0,
    ) -> bool:
        budget = self._budgets[provider]
        if not budget.can_consume(urgency):
            return False
        remaining_requests = budget.remaining_for_model(model)
        if remaining_requests is not None and remaining_requests < 1:
            return False
        remaining_tokens = budget.remaining_tokens_for_model(model)
        return remaining_tokens is None or remaining_tokens >= max(0, estimated_tokens)

    def reserve(
        self,
        provider: ProviderType,
        model: ModelSpec,
        estimated_tokens: int,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
    ) -> bool:
        budget = self._budgets[provider]
        return self.store.reserve(
            provider,
            model,
            estimated_tokens,
            budget.total_budget,
            budget.reserve_fraction,
            urgency,
        )

    def consume(
        self,
        provider: ProviderType,
        model_name: str,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
    ) -> None:
        del urgency
        self._budgets[provider].consume(model_name)

    def reconcile_actual(
        self,
        provider: ProviderType,
        model_name: str,
        estimated_tokens: int,
        input_tokens: int,
        output_tokens: int,
        actual_tokens: int,
    ) -> float:
        actual = max(0, actual_tokens) or max(0, estimated_tokens)
        input_count = max(0, input_tokens)
        output_count = max(0, output_tokens)
        if input_count + output_count == 0:
            input_count = actual
        input_price, output_price = _MODEL_PRICES_PER_MILLION.get(
            (provider, model_name), _PROVIDER_DEFAULT_PRICES[provider]
        )
        cost = (input_count * input_price + output_count * output_price) / 1_000_000
        self.store.reconcile(
            provider,
            model_name,
            estimated_tokens,
            actual,
            input_count,
            output_count,
            cost,
        )
        with self._lock:
            self._engagement_cost_usd += cost
        return cost

    def release_reservation(
        self,
        provider: ProviderType,
        model_name: str,
        estimated_tokens: int,
    ) -> None:
        self.store.release_reservation(provider, model_name, estimated_tokens)

    def refund(
        self,
        provider: ProviderType,
        model_name: str,
        estimated_tokens: int = 0,
    ) -> None:
        self._budgets[provider].refund(model_name, estimated_tokens=estimated_tokens)

    def filter_available_providers(
        self,
        tier: ModelTier,
        models_by_provider: dict[ProviderType, list[ModelSpec]],
        urgency: TaskUrgency = TaskUrgency.NORMAL,
        estimated_tokens: int = 0,
    ) -> set[ProviderType]:
        available: set[ProviderType] = set()
        for provider, models in models_by_provider.items():
            for model in models:
                if (
                    model.tier == tier
                    and not model.deprecated
                    and self.can_serve(provider, model, urgency, estimated_tokens)
                ):
                    available.add(provider)
                    break
        return available

    def reset_engagement_cost(self) -> None:
        with self._lock:
            self._engagement_cost_usd = 0.0

    @property
    def engagement_cost_usd(self) -> float:
        with self._lock:
            return self._engagement_cost_usd

    def get_usage_summary(self) -> dict[ProviderType, dict[str, float]]:
        summary: dict[ProviderType, dict[str, float]] = {}
        daily_cost = self.store.daily_cost()
        for provider, budget in self._budgets.items():
            summary[provider] = {
                "usage_pct": budget.usage_percentage,
                "percentage": budget.usage_percentage * 100.0,
                "available": float(budget.available),
                "total": float(budget.total_budget),
                "in_reserve": float(not budget.is_reserve_available),
                "engagement_cost_usd": self.engagement_cost_usd,
                "daily_cost_usd": daily_cost,
            }
        return summary

    def get_priority_order(
        self,
        urgency: TaskUrgency,
        available: set[ProviderType],
    ) -> list[ProviderType]:
        if urgency in (TaskUrgency.HIGH, TaskUrgency.LOW):
            return sorted(
                available,
                key=lambda p: _PROVIDER_SCARCITY.get(p, 99),
                reverse=True,
            )
        return sorted(available, key=lambda p: self._budgets[p].available, reverse=True)
