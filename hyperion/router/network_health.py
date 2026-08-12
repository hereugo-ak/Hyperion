"""Global network-reachability probe for the router (L1 fix).

Circuit breakers are per-provider. But all HYPERION providers (NVIDIA,
Mistral, Groq, Google) share the same egress network path: DNS + TCP +
TLS out of the same host. A single network blip therefore trips them
independently and in parallel, which manifested as the "one blip took
nvidia+mistral+groq down together" failure mode this module fixes.

This module owns ONE cheap, shared probe of external connectivity and
publishes a boolean ``environment_degraded`` flag. Preflight (and the
provider circuit-breaker path) consult the flag before opening more
individual circuits: if the network itself is down, opening five parallel
5-minute cooldowns solves nothing — a single probe → global degraded flag
is enough.

The module is deliberately small and side-effect-free at import time.
The probe is:
  - a TCP-connect (no HTTP body) to a well-known highly-available host,
  - bounded by a 2s timeout,
  - throttled so at most one probe runs every ``PROBE_INTERVAL_S`` seconds
    across all callers (a single asyncio lock guards this).

Never raises. On any probe failure the flag is flipped True; on the next
success it flips False again. Callers may consult ``is_degraded()`` from
sync code cheaply.
"""

from __future__ import annotations

import asyncio
import logging
import socket
import time

logger = logging.getLogger(__name__)


# Public-DNS anycast endpoints; one primary + one fallback so the probe
# does not itself become a single point of failure. Cloudflare 1.1.1.1
# and Google Public DNS 8.8.8.8 are the two highest-availability endpoints
# outside our provider set — a *joint* failure of both means the network
# is genuinely unreachable, not that one CDN happened to be flapping.
_PROBE_TARGETS: tuple[tuple[str, int], ...] = (
    ("1.1.1.1", 443),
    ("8.8.8.8", 443),
)

# Minimum seconds between real probes. Cheap enough (~<200ms) that we could
# probe on every provider dispatch, but throttling matches the operator's
# expectation that "network is down" is a slowly-changing state.
PROBE_INTERVAL_S: float = 15.0

# TCP connect timeout for one probe attempt.
_PROBE_TIMEOUT_S: float = 2.0


class _State:
    """Module-local mutable state (probing history + degraded flag).

    Encapsulated in a class rather than free module vars purely so that
    :func:`reset` can zero it out cleanly for tests without touching
    ``globals()``.
    """

    def __init__(self) -> None:
        self.degraded: bool = False
        self.last_probe_at: float = 0.0
        self.last_success_at: float = 0.0
        self.probe_lock: asyncio.Lock | None = None  # constructed lazily
        self.consecutive_failures: int = 0


_state = _State()


def _get_lock() -> asyncio.Lock:
    """Construct the probe lock lazily so import does not require a loop."""
    if _state.probe_lock is None:
        _state.probe_lock = asyncio.Lock()
    return _state.probe_lock


def is_degraded() -> bool:
    """Cheap sync check for callers who cannot ``await`` a probe.

    Returns the current cached flag. The wait gate / router uses this to
    short-circuit its own circuit-breaker escalation ("network is down —
    do not open individual circuits, they are all going to fail").
    """
    return _state.degraded


def _mark_degraded(reason: str) -> None:
    if not _state.degraded:
        logger.warning("Network probe: environment marked DEGRADED (%s)", reason)
    _state.degraded = True
    _state.consecutive_failures += 1


def _mark_healthy() -> None:
    if _state.degraded:
        logger.info("Network probe: environment recovered")
    _state.degraded = False
    _state.consecutive_failures = 0
    _state.last_success_at = time.time()


async def _tcp_connect(host: str, port: int, timeout: float) -> bool:
    """Attempt a bounded TCP connect. Never raises."""
    loop = asyncio.get_running_loop()
    try:
        conn = loop.run_in_executor(
            None, lambda: socket.create_connection((host, port), timeout=timeout)
        )
        sock = await asyncio.wait_for(conn, timeout=timeout + 0.5)
    except (TimeoutError, OSError):
        return False
    except Exception as exc:  # noqa: BLE001 - defensive: never raise from a probe
        logger.debug("Network probe: unexpected error connecting to %s:%d: %s",
                     host, port, exc)
        return False
    try:
        sock.close()
    except Exception as exc:  # noqa: BLE001 - close best effort
        logger.debug("socket close failed: %s", exc)
    return True


async def probe(force: bool = False) -> bool:
    """Run a bounded external-network probe if it is time to do so.

    Returns True when the environment is reachable (or the probe was
    skipped because the last one was recent enough), False when the
    probe attempted and failed.

    ``force=True`` bypasses the ``PROBE_INTERVAL_S`` throttle: preflight
    uses this to establish a fresh baseline before it decides whether to
    trip provider circuits.

    Never raises. On any error the flag flips to degraded and the
    function returns False.
    """
    now = time.time()
    if not force and (now - _state.last_probe_at) < PROBE_INTERVAL_S:
        return not _state.degraded

    lock = _get_lock()
    async with lock:
        # Re-check under the lock: another awaiter may have just probed.
        now = time.time()
        if not force and (now - _state.last_probe_at) < PROBE_INTERVAL_S:
            return not _state.degraded

        _state.last_probe_at = now
        for host, port in _PROBE_TARGETS:
            ok = await _tcp_connect(host, port, _PROBE_TIMEOUT_S)
            if ok:
                _mark_healthy()
                return True

        # All targets failed → the egress network path is unreachable.
        _mark_degraded(f"probes failed to {len(_PROBE_TARGETS)} anycast endpoints")
        return False


def reset() -> None:
    """Reset module state. Used by tests only."""
    _state.degraded = False
    _state.last_probe_at = 0.0
    _state.last_success_at = 0.0
    _state.consecutive_failures = 0
    # Do NOT reset probe_lock — asyncio locks are bound to a loop and a
    # test that just called reset() may still be running in that loop.


__all__ = [
    "PROBE_INTERVAL_S",
    "is_degraded",
    "probe",
    "reset",
]
