"""Base adapter — shared HTTP plumbing + error classification (§8, §10)."""

from __future__ import annotations

import logging
from typing import Any

import httpx

from hyperion.search.types import SearchResult

logger = logging.getLogger(__name__)


class TransientProviderError(RuntimeError):
    """A provider failure the orchestrator should react to (429/403/5xx/timeout)."""

    def __init__(self, signal: str, message: str = "") -> None:
        self.signal = signal
        super().__init__(f"{signal}: {message}")


class BaseAdapter:
    """Common adapter behavior: key lookup, one client, error-safe search."""

    name: str = ""
    endpoint: str = ""
    timeout_s: float = 15.0

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None

    # ── key ────────────────────────────────────────────────────────────────

    def _api_key(self, attr: str) -> str:
        if self.settings is None:
            return ""
        return str(getattr(self.settings, attr, "") or "").strip()

    # ── client ─────────────────────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.timeout_s),
                headers=self._headers(),
            )
        return self._client

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ),
        }

    # ── error classification (§10) ─────────────────────────────────────────

    def _classify(self, exc: Exception, response: httpx.Response | None = None) -> str:
        """Map a failure to a §10 signal: 429 / 403 / 5xx / timeout / None."""
        if response is not None:
            if response.status_code == 429:
                return "429"
            if response.status_code == 403:
                return "403"
            if 500 <= response.status_code < 600:
                return "5xx"
            return ""
        if isinstance(exc, httpx.TimeoutException):
            return "timeout"
        if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
            return "timeout"
        return ""

    def _raise_if_error(self, response: httpx.Response) -> None:
        signal = self._classify(None, response)
        if signal:
            raise TransientProviderError(
                signal, f"HTTP {response.status_code} from {self.name}"
            )

    # ── contract ───────────────────────────────────────────────────────────

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[SearchResult]:
        """Return search-only results. Never raises; empty on failure."""
        raise NotImplementedError

    async def close(self) -> None:
        if self._client is not None and not self._client.is_closed:
            await self._client.aclose()
        self._client = None

    async def __aenter__(self) -> "BaseAdapter":
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
