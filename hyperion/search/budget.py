"""Per-run budget buckets for the paid search adapters (§9).

When a bucket is exhausted, the orchestrator treats the provider as
suspended for the remainder of the run (resumes next run — the registry is
reset at engagement start via ``SearchOrchestrator.reset_run()``).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class Bucket:
    capacity: int        # per-run cap
    rps: float           # requests-per-second ceiling
    spent: int = 0
    _last: float = 0.0

    async def try_spend(self, n: int = 1) -> bool:
        """Pace to ``rps`` and consume from the per-run cap. False = exhausted."""
        if self.capacity <= 0:
            return False
        now = time.monotonic()
        interval = 1.0 / max(self.rps, 0.1)
        wait = self._last + interval - now
        if wait > 0:
            await asyncio.sleep(wait)
        if self.spent + n > self.capacity:
            return False
        self.spent += n
        self._last = time.monotonic()
        return True

    @property
    def remaining(self) -> int:
        return max(0, self.capacity - self.spent)


# §9.2 defaults. The config file (config/search_providers.yaml) is the
# runtime source of truth; these are the shipped fallbacks.
DEFAULT_BUCKETS: dict[str, Bucket] = {
    "SearXNG": Bucket(capacity=10_000, rps=6.0),
    "You": Bucket(capacity=300, rps=8.0),
    "Exa": Bucket(capacity=180, rps=4.0),
    "Tavily": Bucket(capacity=70, rps=4.0),
    "Yep": Bucket(capacity=150, rps=4.0),
}


@dataclass
class BudgetRegistry:
    """Per-run buckets keyed by adapter name; reset at engagement start."""

    buckets: dict[str, Bucket] = field(default_factory=lambda: {
        name: Bucket(capacity=b.capacity, rps=b.rps)
        for name, b in DEFAULT_BUCKETS.items()
    })

    def can_spend(self, name: str) -> bool:
        bucket = self.buckets.get(name)
        if bucket is None:
            return False
        return bucket.remaining > 0

    async def spend(self, name: str, n: int = 1) -> bool:
        bucket = self.buckets.get(name)
        if bucket is None:
            return False
        return await bucket.try_spend(n)

    def reset(self) -> None:
        for name, b in DEFAULT_BUCKETS.items():
            self.buckets[name] = Bucket(capacity=b.capacity, rps=b.rps)

    def snapshot(self) -> dict[str, dict[str, int]]:
        return {name: {"capacity": b.capacity, "spent": b.spent,
                       "remaining": b.remaining}
                for name, b in self.buckets.items()}
