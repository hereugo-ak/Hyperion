"""Secure host-to-Valkey coordination without publishing a network port.

HYPERION runs on the host while Valkey is deliberately internal to Docker.  The
Docker CLI is therefore the transport: every process reaches the same Valkey
container through ``docker exec`` without exposing an unauthenticated Redis port.
Operations fail open to the caller so retrieval can use its process-local safety
fallback when Docker is unavailable.
"""

from __future__ import annotations

import json
import os
from typing import Any

from hyperion.infra.services import run_command


class ValkeyStore:
    """Small async Valkey facade for shared retrieval state."""

    database = 3
    command_timeout = 5.0

    def __init__(self, container: str | None = None) -> None:
        self.container = container or os.environ.get(
            "HYPERION_VALKEY_CONTAINER",
            "hyperion-valkey",
        )

    async def _execute(self, *arguments: str) -> tuple[bool, str]:
        command = [
            "docker",
            "exec",
            self.container,
            "valkey-cli",
            "--raw",
            "-n",
            str(self.database),
            *arguments,
        ]
        rc, stdout, _ = await run_command(command, timeout=self.command_timeout)
        return rc == 0, stdout

    async def get_json(self, key: str) -> dict[str, Any] | None:
        """Return a JSON object, or ``None`` on a miss or unavailable Valkey."""
        ok, value = await self._execute("GET", key)
        if not ok or not value:
            return None
        try:
            decoded = json.loads(value)
        except (TypeError, ValueError):
            return None
        return decoded if isinstance(decoded, dict) else None

    async def set_json(self, key: str, value: dict[str, Any], ttl_seconds: int) -> bool:
        """Store one JSON object with a mandatory expiry."""
        payload = json.dumps(value, sort_keys=True, separators=(",", ":"))
        ok, response = await self._execute(
            "SETEX",
            key,
            str(ttl_seconds),
            payload,
        )
        return ok and response == "OK"

    async def reserve_engine_window(
        self,
        engines: set[str],
        *,
        interval_ms: int,
        jitter_ms: int,
    ) -> float | None:
        """Atomically reserve one shared time window for every requested engine.

        The Lua script uses Valkey's clock, avoiding host clock skew across
        HYPERION processes.  It returns the wait in seconds before this caller's
        reservation begins. ``None`` means the shared store was unavailable.
        """
        if not engines:
            return 0.0
        script = """
local stamp = redis.call('TIME')
local now = (stamp[1] * 1000) + math.floor(stamp[2] / 1000)
local next_allowed = now
for _, key in ipairs(KEYS) do
  local value = tonumber(redis.call('GET', key) or '0')
  if value > next_allowed then next_allowed = value end
end
local reservation = next_allowed + tonumber(ARGV[1]) + tonumber(ARGV[2])
local ttl = math.max(tonumber(ARGV[1]) * 10, 60000)
for _, key in ipairs(KEYS) do
  redis.call('SET', key, reservation, 'PX', ttl)
end
return math.max(0, next_allowed - now)
""".strip()
        keys = [f"hyperion:retrieval:engine:{engine}" for engine in sorted(engines)]
        ok, value = await self._execute(
            "EVAL",
            script,
            str(len(keys)),
            *keys,
            str(interval_ms),
            str(jitter_ms),
        )
        if not ok:
            return None
        try:
            return max(0.0, int(value) / 1000.0)
        except (TypeError, ValueError):
            return None


_STORE = ValkeyStore()


def get_valkey_store() -> ValkeyStore:
    """Return the process singleton; Valkey itself remains cross-process."""
    return _STORE
