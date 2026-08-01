"""Persistent, auditable quota accounting for scarce grounded retrieval.

The ledger is deliberately independent of the LLM request budget.  Google
Search grounding has a separate billing unit (issued search queries for Gemini
3, grounded calls for older families), and routine breadth retrieval must not
consume the final reserve needed for evidence-critical escalation.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

try:  # pragma: no cover - Windows path is covered through the thread lock
    import fcntl
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore[assignment]


class QuotaExhausted(RuntimeError):  # noqa: N818 - public W-14 contract
    """The requested grounded-search reservation would violate quota policy."""


@dataclass(frozen=True)
class QuotaReservation:
    id: str
    family: str
    units: int
    query: str
    engagement_id: str
    high_value: bool


class GroundingQuotaLedger:
    """Atomic day/month quota ledger with an append-only audit trail."""

    _thread_lock = threading.RLock()

    def __init__(
        self,
        path: Path,
        *,
        daily_limit: int,
        monthly_limit: int,
        reserve_fraction: float = 0.10,
        now: Any | None = None,
    ) -> None:
        if daily_limit < 0 or monthly_limit < 0:
            raise ValueError("quota limits must be non-negative")
        if not 0.0 <= reserve_fraction < 1.0:
            raise ValueError("reserve_fraction must be in [0, 1)")
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.daily_limit = daily_limit
        self.monthly_limit = monthly_limit
        self.reserve_fraction = reserve_fraction
        self._now = now or (lambda: datetime.now(UTC))

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, self.lock_path.open("a+", encoding="utf-8") as lock:
            if fcntl is not None:
                fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                if fcntl is not None:
                    fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def _periods(self) -> tuple[str, str, str]:
        current = self._now()
        if current.tzinfo is None:
            current = current.replace(tzinfo=UTC)
        current = current.astimezone(UTC)
        return current.date().isoformat(), current.strftime("%Y-%m"), current.isoformat()

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {"version": 1, "days": {}, "months": {}, "reservations": {}, "events": []}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"grounding quota ledger is unreadable: {self.path}") from exc
        if payload.get("version") != 1:
            raise RuntimeError("unsupported grounding quota ledger version")
        for key, default in (("days", {}), ("months", {}), ("reservations", {}), ("events", [])):
            payload.setdefault(key, default)
        return cast("dict[str, Any]", payload)

    def _write(self, payload: dict[str, Any]) -> None:
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, sort_keys=True, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            if os.path.exists(tmp_name):
                os.unlink(tmp_name)

    @staticmethod
    def model_family(model: str) -> str:
        lowered = model.casefold()
        if "gemini-3" in lowered:
            return "gemini-3"
        if "gemini-2.5" in lowered:
            return "gemini-2.5"
        return lowered.split("/", 1)[-1].split("-preview", 1)[0]

    def remaining(self, family: str, *, high_value: bool = False) -> dict[str, int]:
        with self._locked():
            payload = self._read()
            day, month, _ = self._periods()
            day_used = int(payload["days"].get(day, {}).get(family, 0))
            month_used = int(payload["months"].get(month, {}).get(family, 0))
            daily = max(0, self.daily_limit - day_used)
            monthly = max(0, self.monthly_limit - month_used)
            available = min(daily, monthly)
            if not high_value:
                reserve = max(
                    int(self.daily_limit * self.reserve_fraction + 0.999999),
                    int(self.monthly_limit * self.reserve_fraction + 0.999999),
                )
                available = max(0, available - reserve)
            return {"daily": daily, "monthly": monthly, "available": available}

    def reserve(
        self,
        units: int,
        *,
        model: str,
        query: str,
        engagement_id: str,
        high_value: bool = False,
    ) -> QuotaReservation:
        if units <= 0:
            raise ValueError("reservation units must be positive")
        family = self.model_family(model)
        with self._locked():
            payload = self._read()
            day, month, timestamp = self._periods()
            day_used = int(payload["days"].get(day, {}).get(family, 0))
            month_used = int(payload["months"].get(month, {}).get(family, 0))
            daily_after = day_used + units
            monthly_after = month_used + units
            daily_cap = self.daily_limit
            monthly_cap = self.monthly_limit
            if not high_value:
                daily_cap -= int(self.daily_limit * self.reserve_fraction + 0.999999)
                monthly_cap -= int(self.monthly_limit * self.reserve_fraction + 0.999999)
            if daily_after > daily_cap or monthly_after > monthly_cap:
                raise QuotaExhausted(
                    f"grounding quota unavailable for {family}: requested={units}, "
                    f"daily={day_used}/{self.daily_limit}, "
                    f"monthly={month_used}/{self.monthly_limit}, "
                    f"reserve={self.reserve_fraction:.0%}, high_value={high_value}"
                )
            payload["days"].setdefault(day, {})[family] = daily_after
            payload["months"].setdefault(month, {})[family] = monthly_after
            reservation_id = uuid.uuid4().hex
            record = {
                "id": reservation_id,
                "family": family,
                "model": model,
                "reserved_units": units,
                "query": query,
                "engagement_id": engagement_id,
                "high_value": high_value,
                "day": day,
                "month": month,
                "timestamp": timestamp,
            }
            payload["reservations"][reservation_id] = record
            payload["events"].append({"type": "reserved", **record})
            self._write(payload)
        return QuotaReservation(reservation_id, family, units, query, engagement_id, high_value)

    def settle(self, reservation: QuotaReservation, actual_units: int, *, outcome: str) -> None:
        """Reconcile a conservative reservation to provider-reported actual spend."""
        if actual_units < 0:
            raise ValueError("actual units must be non-negative")
        with self._locked():
            payload = self._read()
            record = payload["reservations"].pop(reservation.id, None)
            if record is None:
                raise KeyError(f"unknown or already settled reservation {reservation.id}")
            delta = actual_units - int(record["reserved_units"])
            day = str(record["day"])
            month = str(record["month"])
            family = str(record["family"])
            payload["days"].setdefault(day, {})[family] = max(
                0, int(payload["days"].get(day, {}).get(family, 0)) + delta
            )
            payload["months"].setdefault(month, {})[family] = max(
                0, int(payload["months"].get(month, {}).get(family, 0)) + delta
            )
            _, _, timestamp = self._periods()
            payload["events"].append({
                "type": "settled",
                "reservation_id": reservation.id,
                "family": family,
                "model": record["model"],
                "query": record["query"],
                "engagement_id": record["engagement_id"],
                "high_value": record["high_value"],
                "reserved_units": record["reserved_units"],
                "actual_units": actual_units,
                "outcome": outcome,
                "timestamp": timestamp,
            })
            self._write(payload)
