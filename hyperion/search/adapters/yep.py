"""Yep adapter — the last resort (§4/§8).

Yep is denied when proxied through SearXNG, so we call its direct API here.
Cheapest, weakest relevance. Key optional (public endpoint).
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.types import SearchResult, clean_url, ensure_snippet

logger = logging.getLogger(__name__)


class YepAdapter(BaseAdapter):
    name = "Yep"
    endpoint = "https://api.yep.com/fs/2/search"
    timeout_s = 15.0
    category = "web"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._key = self._api_key("yep_api_key")

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"
        return headers

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[SearchResult]:
        try:
            client = await self._get_client()
            response = await client.get(
                self.endpoint,
                params={
                    "q": query,
                    "gl": "US",
                    "max_results": min(num_results, 20),
                    "safe_search": "false",
                },
            )
            self._raise_if_error(response)
            data = response.json()
        except TransientProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            signal = self._classify(exc)
            raise TransientProviderError(signal or "5xx", str(exc)) from exc

        # Yep's /fs/2/search returns {web: {results: [{title, url, snippet}]}}
        web = data.get("web") or {}
        raw_items = web.get("results") or data.get("results") or []
        results: list[SearchResult] = []
        for item in raw_items:
            url = clean_url(str(item.get("url", "") or ""))
            if not url:
                continue
            title = str(item.get("title", "") or "").strip()
            snippet = str(item.get("snippet", "") or title).strip()
            results.append(SearchResult(
                title=title or url,
                url=url,
                snippet=snippet or title,
                engine="yep",
                backend=self.name,
                score=0.6,
                category=self.category,
                raw=item,
            ))
        return [ensure_snippet(r) for r in results[:num_results]]
