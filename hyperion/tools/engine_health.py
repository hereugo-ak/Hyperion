"""HYPERION Engine Health Tracker — per-engine cooldowns from SearXNG's
own failure reporting (P2-26 / P2-G23).

WHY THIS MODULE EXISTS
----------------------
SearXNG tells us exactly which engine failed, in every response body, via
``unresponsive_engines``. Until now that field was logged at
``searxng.py`` and discarded: no cooldown, no blacklist, no promotion of
a standby engine. The 07-30 Docker log showed DuckDuckGo under a CAPTCHA
storm terminating in ``HTTP error 403 (suspended_time=86400)`` — a
24-hour ban reported on every response — while the client kept sending
it traffic and letting one surviving engine carry the whole engagement.
Report B's evidence base collapsed to 3 encyclopedia entries as a direct
result.

Rules:
1. **Consume, don't discard.** Every SearXNG response feeds
   :meth:`record_response`. Engines named in ``unresponsive_engines``
   get an exponential cooldown; engines seen producing results recover
   immediately.
2. **A suspended_time ban is 24 hours for that engine specifically.**
   It is never a reason to abandon search entirely.
3. **Persist across the process.** Cooldowns survive restarts so a
   re-launched engagement does not rediscover the same bans one query
   at a time.
4. **The client asks before every request.** ``filter_available``
   removes cooled engines from the next ``engines=`` parameter so the
   standby pool is used instead.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

from hyperion.infra.paths import project_root

logger = logging.getLogger(__name__)

# Base cooldown doubles on each consecutive failure, capped at 24h.
_BASE_COOLDOWN_SECONDS = 300  # 5 minutes
_MAX_COOLDOWN_SECONDS = 86400  # 24 hours

_SUSPENDED_TIME_RE = re.compile(r"suspended_time=(\d+)")


class EngineHealthTracker:
    """Tracks per-engine health with exponential cooldowns, persisted to disk."""

    def __init__(self) -> None:
        self._state_path = self._resolve_state_path()
        self._failures: dict[str, int] = {}
        self._cooldowns: dict[str, float] = {}
        self._load()

    @staticmethod
    def _resolve_state_path() -> Path:
        override = os.environ.get("HYPERION_ENGINE_HEALTH_STATE", "")
        if override:
            return Path(override)
        return project_root() / "vault" / "engine_health.json"

    # ── Persistence ────────────────────────────────────────────────────

    def _load(self) -> None:
        try:
            if self._state_path.is_file():
                data = json.loads(self._state_path.read_text(encoding="utf-8"))
                self._failures = {str(k): int(v) for k, v in data.get("failures", {}).items()}
                self._cooldowns = {
                    str(k): float(v) for k, v in data.get("cooldowns", {}).items()
                }
        except (OSError, ValueError, TypeError) as exc:
            # A corrupt state file is not fatal: start fresh and say so.
            logger.warning("engine health state unreadable (%s); starting fresh", exc)
            self._failures = {}
            self._cooldowns = {}

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({"failures": self._failures, "cooldowns": self._cooldowns}),
                encoding="utf-8",
            )
            tmp.replace(self._state_path)
        except OSError as exc:
            logger.warning("engine health state could not be persisted: %s", exc)

    # ── Recording ──────────────────────────────────────────────────────

    def record_response(
        self,
        unresponsive_engines: list[list[str]] | list[tuple[str, str]],
        responding_engines: list[str] | set[str],
    ) -> None:
        """Consume one SearXNG response body's engine health signals.

        Args:
            unresponsive_engines: the raw ``unresponsive_engines`` field,
                a list of ``[engine_name, reason]`` pairs.
            responding_engines: engines observed producing results in the
                same response (they recover immediately).
        """
        changed = False
        for entry in unresponsive_engines or []:
            if not entry:
                continue
            name = str(entry[0])
            reason = str(entry[1]) if len(entry) > 1 else ""
            self._failures[name] = self._failures.get(name, 0) + 1
            cooldown = self._cooldown_for(reason, self._failures[name])
            until = time.time() + cooldown
            # Never shorten an existing longer ban (e.g. a 24h suspended_time
            # ban must not be overwritten by a 5-minute generic failure).
            if until > self._cooldowns.get(name, 0.0):
                self._cooldowns[name] = until
            logger.warning(
                "ENGINE COOLDOWN: %s (reason=%r, failures=%d, cooled %.0fs)",
                name, reason, self._failures[name], cooldown,
            )
            changed = True

        for name in responding_engines or []:
            if name in self._failures or name in self._cooldowns:
                self._failures.pop(name, None)
                self._cooldowns.pop(name, None)
                logger.info("ENGINE RECOVERED: %s (produced results)", name)
                changed = True

        if changed:
            self._save()

    @staticmethod
    def _cooldown_for(reason: str, consecutive_failures: int) -> float:
        """Exponential cooldown; an explicit suspended_time ban wins."""
        match = _SUSPENDED_TIME_RE.search(reason)
        if match:
            return min(float(match.group(1)), _MAX_COOLDOWN_SECONDS)
        if "403" in reason and "suspended" in reason:
            return _MAX_COOLDOWN_SECONDS
        backoff = _BASE_COOLDOWN_SECONDS * (2 ** max(0, consecutive_failures - 1))
        return float(min(backoff, _MAX_COOLDOWN_SECONDS))

    # ── Queries ────────────────────────────────────────────────────────

    def is_available(self, engine: str) -> bool:
        until = self._cooldowns.get(engine)
        if until is None:
            return True
        if time.time() >= until:
            self._cooldowns.pop(engine, None)
            self._failures.pop(engine, None)
            self._save()
            return True
        return False

    def cooldown_until(self, engine: str) -> float:
        """Absolute epoch the engine cools until (0.0 if not cooled)."""
        return self._cooldowns.get(engine, 0.0)

    def filter_available(self, engines: list[str]) -> list[str]:
        """Drop cooled engines from a candidate list for the next request."""
        return [e for e in engines if self.is_available(e)]

    def reset(self) -> None:
        """Clear all state (used by tests)."""
        self._failures = {}
        self._cooldowns = {}
        self._save()


_tracker: EngineHealthTracker | None = None


def get_engine_health() -> EngineHealthTracker:
    """Process-wide singleton; state itself persists across processes."""
    global _tracker
    if _tracker is None:
        _tracker = EngineHealthTracker()
    return _tracker


def reset_engine_health() -> None:
    """Drop the singleton (used by tests)."""
    global _tracker
    _tracker = None
