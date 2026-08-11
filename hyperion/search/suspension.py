"""Per-run provider suspension (§10).

Any signal (429 / 403 / 5xx×3 / timeout×2 / bucket-exhausted / empty×3)
demotes a provider for the REST of the current run, with cooldowns for
transient conditions. Per-run only — the next engagement starts clean.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

#: cooldown seconds per signal; None = do not retry within the run.
COOLDOWNS: dict[str, float | None] = {
    "429": 90.0,
    "5xx": 120.0,
    "timeout": 60.0,
    "403": None,
    "bucket_exhausted": None,   # resumes next run by construction
}

#: how many consecutive empty results before a provider is down-weighted
#: (continues in chain but logged).
CONSEC_EMPTY_THRESHOLD = 3


@dataclass
class ProviderState:
    suspended_until: float = 0.0      # monotonic; 0 = not suspended
    permanent: bool = False            # 403 / bucket-exhausted — never retry
    consec_empties: int = 0
    error_counts: dict[str, int] = field(default_factory=dict)  # 5xx / timeout


class SuspensionRegistry:
    """Per-run provider suspension + empty-result bookkeeping."""

    def __init__(self) -> None:
        self.states: dict[str, ProviderState] = {}

    def _state(self, name: str) -> ProviderState:
        return self.states.setdefault(name, ProviderState())

    def record_error(self, name: str, signal: str, now: float | None = None) -> None:
        """Apply §10 signals. ``signal`` in {429, 403, 5xx, timeout}."""
        now = now or time.monotonic()
        st = self._state(name)
        if signal in ("403", "bucket_exhausted"):
            st.permanent = True
            st.suspended_until = now  # never retry within the run
            return
        if signal == "429":
            st.suspended_until = max(st.suspended_until, now + COOLDOWNS["429"])
            return
        if signal in ("5xx", "timeout"):
            st.error_counts[signal] = st.error_counts.get(signal, 0) + 1
            threshold = 3 if signal == "5xx" else 2
            if st.error_counts[signal] >= threshold:
                st.suspended_until = max(
                    st.suspended_until, now + COOLDOWNS[signal]
                )
                st.error_counts[signal] = 0

    def record_empty(self, name: str) -> bool:
        """Increment consecutive-empty counter; True when down-weighted."""
        st = self._state(name)
        st.consec_empties += 1
        return st.consec_empties >= CONSEC_EMPTY_THRESHOLD

    def record_success(self, name: str) -> None:
        st = self._state(name)
        st.consec_empties = 0
        st.error_counts.clear()

    def is_available(self, name: str, now: float | None = None) -> bool:
        now = now or time.monotonic()
        st = self.states.get(name)
        if st is None:
            return True
        if st.permanent:
            return False
        return st.suspended_until <= now

    def reset(self) -> None:
        self.states.clear()

    def suspended(self) -> list[str]:
        return [n for n in self.states if not self.is_available(n)]
