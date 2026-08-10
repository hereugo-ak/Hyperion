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
from enum import StrEnum
from pathlib import Path

from hyperion.infra.paths import project_root

logger = logging.getLogger(__name__)

# Base cooldown doubles on each consecutive failure, capped at 4h.
#
# F-04 (FIX0.3_RUNBOOK_2026-08-09): the cap was 24h, which let a single bad
# session (a CAPTCHA storm, a suspended_time=86400 ban) waste a whole day of
# capacity and poison the NEXT engagement's boot. 4h is enough to absorb a
# real outage while guaranteeing the fleet recovers on its own.
_BASE_COOLDOWN_SECONDS = 300  # 5 minutes
_MAX_COOLDOWN_SECONDS = 14400  # 4 hours

# P1.4 (overhaul §6 P1, 2026-08-10): per-source-class membership. The Aug-10
# autopsy proved engine-level cooldowns are necessary but not sufficient — the
# web SCRAPER class (mwmbl/brave) can be wholesale 403/429ing while the scholar
# and reference API classes are healthy, and the old gate only asked "how many
# engines are healthy fleet-wide". Class-level health lets the search layer
# reroute to a living source class instead of re-probing a dead one. These are
# the ACTIVE engines only (P1.2: mojeek/yep are disabled and excluded).
_SOURCE_CLASS_ENGINES: dict[str, frozenset[str]] = {
    "web": frozenset({"mwmbl", "brave"}),
    "scholar": frozenset({"crossref", "openalex", "arxiv", "pubmed", "semantic scholar"}),
    "reference": frozenset({"wikipedia", "github", "stackexchange", "hackernews", "openstreetmap"}),
}

_SUSPENDED_TIME_RE = re.compile(r"suspended_time=(\d+)")
_CAPTCHA_MARKERS = ("captcha", "accessdenied", "access denied")


class EngineState(StrEnum):
    """The three operational states required by W-11."""

    HEALTHY = "healthy"
    COOLING = "cooling"
    SUSPENDED = "suspended"


class EngineHealthTracker:
    """Tracks per-engine health with exponential cooldowns, persisted to disk."""

    def __init__(self) -> None:
        self._state_path = self._resolve_state_path()
        self._failures: dict[str, int] = {}
        self._cooldowns: dict[str, float] = {}
        self._suspended: dict[str, float] = {}
        # A CAPTCHA is a W-11 policy violation, not a routine transient. Keep
        # the engine evicted for this process even if a later response happens
        # to include it among responding engines.
        self._policy_violations: set[str] = set()
        self._degradation_events: list[dict[str, object]] = []
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
                self._suspended = {
                    str(k): float(v) for k, v in data.get("suspended", {}).items()
                }
        except (OSError, ValueError, TypeError) as exc:
            # A corrupt state file is not fatal: start fresh and say so.
            logger.warning("engine health state unreadable (%s); starting fresh", exc)
            self._failures = {}
            self._cooldowns = {}
            self._suspended = {}
        # F-04: boot-time TTL sweep. Cooldowns whose ``until`` has already
        # passed were written by an earlier session and must not be allowed to
        # poison this one — the Aug 4 session's 24h bans were still active on
        # Aug 9 purely because nothing aged them out at boot. ``state()``
        # pops expired entries lazily per-engine; this sweeps every persisted
        # engine once so a fresh process starts from a genuinely fresh state.
        self.sweep_expired()

    def sweep_expired(self) -> int:
        """Drop every expired cooldown/suspension; return how many were dropped.

        F-04: called at load time so a restart never inherits a stale ban, and
        callable any time an operator wants to age out finished suspensions
        without resetting healthy state.
        """
        now = time.time()
        dropped = 0
        for bucket in (self._cooldowns, self._suspended):
            expired = [name for name, until in bucket.items() if until <= now]
            for name in expired:
                del bucket[name]
                self._failures.pop(name, None)
            dropped += len(expired)
        if dropped:
            self._save()
        return dropped

    def _save(self) -> None:
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._state_path.with_suffix(".tmp")
            tmp.write_text(
                json.dumps({
                    "failures": self._failures,
                    "cooldowns": self._cooldowns,
                    "suspended": self._suspended,
                }),
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
            reason_lower = reason.lower()
            explicit_suspension = _SUSPENDED_TIME_RE.search(reason) is not None
            captcha = any(marker in reason_lower for marker in _CAPTCHA_MARKERS)
            if captcha:
                # The no-block pool contract says this must never happen. A
                # CAPTCHA means an engine changed behaviour or a Tier C engine
                # was reintroduced, so evict it for the process and make the
                # policy breach operator-visible.
                self._policy_violations.add(name)
                until = time.time() + _MAX_COOLDOWN_SECONDS
                self._suspended[name] = until
                logger.error(
                    "ENGINE POLICY VIOLATION: %s issued a CAPTCHA/access denial; "
                    "evicted for this process (reason=%r)",
                    name,
                    reason,
                )
            elif explicit_suspension or ("403" in reason and "suspended" in reason_lower):
                cooldown = self._cooldown_for(reason, self._failures[name])
                until = time.time() + cooldown
                self._suspended[name] = max(until, self._suspended.get(name, 0.0))
                logger.error(
                    "ENGINE SUSPENDED: %s (reason=%r, until=%.0f)", name, reason, until
                )
            else:
                cooldown = self._cooldown_for(reason, self._failures[name])
                until = time.time() + cooldown
                # Never shorten an existing longer cooldown.
                if until > self._cooldowns.get(name, 0.0):
                    self._cooldowns[name] = until
                logger.warning(
                    "ENGINE COOLING: %s (reason=%r, failures=%d, cooled %.0fs)",
                    name, reason, self._failures[name], cooldown,
                )
            changed = True

        for name in responding_engines or []:
            if name in self._policy_violations:
                continue
            if name in self._failures or name in self._cooldowns or name in self._suspended:
                self._failures.pop(name, None)
                self._cooldowns.pop(name, None)
                self._suspended.pop(name, None)
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

    def state(self, engine: str) -> EngineState:
        """Return HEALTHY, COOLING, or SUSPENDED for one engine."""
        if engine in self._policy_violations:
            return EngineState.SUSPENDED
        now = time.time()
        suspended_until = self._suspended.get(engine, 0.0)
        if suspended_until > now:
            return EngineState.SUSPENDED
        if suspended_until:
            self._suspended.pop(engine, None)
            self._failures.pop(engine, None)
            self._save()
        cooldown_until = self._cooldowns.get(engine, 0.0)
        if cooldown_until > now:
            return EngineState.COOLING
        if cooldown_until:
            self._cooldowns.pop(engine, None)
            self._failures.pop(engine, None)
            self._save()
        return EngineState.HEALTHY

    def is_available(self, engine: str) -> bool:
        return self.state(engine) is EngineState.HEALTHY

    def cooldown_until(self, engine: str) -> float:
        """Absolute epoch an engine cools/suspends until (0.0 if healthy)."""
        return max(self._cooldowns.get(engine, 0.0), self._suspended.get(engine, 0.0))

    def healthy_count(self, engines: list[str] | set[str]) -> int:
        return sum(1 for engine in engines if self.is_available(engine))

    # ── P1.4: source-class health ──────────────────────────────────────

    def class_state(self, source_class: str) -> tuple[int, int, dict[str, EngineState]]:
        """Per-class health: (healthy, total, per-engine states).

        ``source_class`` is one of ``_SOURCE_CLASS_ENGINES`` keys (``web`` /
        ``scholar`` / ``reference``). The engine-health circuit classifies the
        fleet so the search layer can reroute to a living class instead of
        re-probing a dead one (the Aug-10 "web pool 403ing all run" failure).
        """
        engines = sorted(_SOURCE_CLASS_ENGINES.get(source_class, frozenset()))
        states = {engine: self.state(engine) for engine in engines}
        healthy = sum(1 for state in states.values() if state is EngineState.HEALTHY)
        return healthy, len(engines), states

    def class_healthy(self, source_class: str) -> bool:
        """True when at least one engine in the class is currently HEALTHY."""
        healthy, total, _ = self.class_state(source_class)
        return total > 0 and healthy > 0

    def living_classes(self) -> list[str]:
        """Source classes with >= 1 healthy engine, in registry order."""
        return [cls for cls in _SOURCE_CLASS_ENGINES if self.class_healthy(cls)]

    def record_degradation_if_needed(
        self,
        engines: list[str] | set[str],
        *,
        floor: int = 4,
    ) -> dict[str, object] | None:
        """Record and log a first-class retrieval degradation event."""
        healthy = self.healthy_count(engines)
        if healthy >= floor:
            return None
        event: dict[str, object] = {
            "type": "retrieval_engine_pool_degraded",
            "healthy": healthy,
            "required": floor,
            "states": {engine: self.state(engine).value for engine in sorted(engines)},
            "timestamp": time.time(),
        }
        self._degradation_events.append(event)
        logger.error(
            "RETRIEVAL DEGRADED: only %d healthy engines; floor is %d", healthy, floor
        )
        return event

    def degradation_events(self) -> list[dict[str, object]]:
        return list(self._degradation_events)

    def filter_available(self, engines: list[str]) -> list[str]:
        """Drop cooled engines from a candidate list for the next request."""
        return [e for e in engines if self.is_available(e)]

    def reset(self) -> None:
        """Clear all state (used by tests)."""
        self._failures = {}
        self._cooldowns = {}
        self._suspended = {}
        self._policy_violations = set()
        self._degradation_events = []
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
