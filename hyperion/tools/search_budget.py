"""HYPERION — Per-engagement search budget singleton.

Enforces a hard cap on total search requests per engagement so the
fact-checker and discovery layer can't DoS the search stack.

D3 fix: SearchBudget.current().allow("searxng") gates every search call.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SearchBudget:
    """Per-engagement hard cap on search requests.

    Call SearchBudget.start(cap=60) at the top of each engagement.
    Every search call checks SearchBudget.current().allow(engine).
    """

    cap: int = 60
    used: dict[str, int] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    _instance: SearchBudget | None = None

    @classmethod
    def start(cls, cap: int = 60) -> SearchBudget:
        cls._instance = SearchBudget(cap=cap)
        logger.info("SearchBudget started: cap=%d", cap)
        return cls._instance

    @classmethod
    def current(cls) -> SearchBudget:
        if cls._instance is None:
            cls._instance = SearchBudget()
        return cls._instance

    def allow(self, engine: str) -> bool:
        with self._lock:
            total = sum(self.used.values())
            if total >= self.cap:
                logger.warning(
                    "SearchBudget exhausted: %d/%d (by_engine=%s)",
                    total, self.cap, dict(self.used),
                )
                return False
            self.used[engine] = self.used.get(engine, 0) + 1
            return True

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "used": sum(self.used.values()),
                "cap": self.cap,
                "by_engine": dict(self.used),
            }

    def reset(self) -> None:
        with self._lock:
            self.used.clear()


# Type annotation helper
from typing import Any  # noqa: E402
