"""SearXNG adapter — the free primary. Wraps the existing SearxNGClient pool
(full-pool fan-out + engine-health circuits) and maps its results into the
canonical SearchResult shape. Always available, never costs a credit."""

from __future__ import annotations

import contextlib
import logging
from typing import Any

from hyperion.search.adapters.base import BaseAdapter
from hyperion.search.types import SearchResult, clean_url, ensure_snippet

logger = logging.getLogger(__name__)


class SearxNGAdapter(BaseAdapter):
    name = "SearXNG"
    category = "web"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._client_holder: Any | None = None

    async def _searxng(self) -> Any:
        if self._client_holder is None:
            from hyperion.tools.searxng import SearxNGClient

            self._client_holder = SearxNGClient(settings=self.settings)
        return self._client_holder

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[SearchResult]:
        try:
            client = await self._searxng()
            response = await client.search(
                query=query, num_results=max(num_results, 10)
            )
        except Exception as exc:  # noqa: BLE001 - adapter is fail-open
            logger.warning("SearXNG adapter failed: %s", exc)
            return []
        results: list[SearchResult] = []
        for r in response.results:
            snippet = (r.snippet or "").strip() or (r.title or "").strip()
            results.append(SearchResult(
                title=(r.title or "").strip(),
                url=clean_url(r.url),
                snippet=snippet,
                engine=(r.engine or "searxng").strip(),
                backend=self.name,
                score=float(getattr(r, "score", 0.0) or 0.0),
                category=self.category,
                published_date=getattr(r, "published_date", "") or None,
            ))
        return [ensure_snippet(r) for r in results]

    async def close(self) -> None:
        if self._client_holder is not None:
            with contextlib.suppress(Exception):  # close is best-effort
                await self._client_holder.close()
            self._client_holder = None
