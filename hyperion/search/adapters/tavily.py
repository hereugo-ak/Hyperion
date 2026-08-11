"""Tavily adapter — the reserve tier (§4/§8).

Search-only: search_depth basic, include_answer false, include_raw_content
false. Mid-quality, modest $25 budget, held in reserve.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.types import SearchResult, clean_url, ensure_snippet

logger = logging.getLogger(__name__)


class TavilyAdapter(BaseAdapter):
    name = "Tavily"
    endpoint = "https://api.tavily.com/search"
    timeout_s = 20.0
    category = "web"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._key = self._api_key("tavily_api_key")

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[SearchResult]:
        if not self._key:
            logger.debug("tavily: no HYPERION_TAVILY_API_KEY — skipping")
            return []
        try:
            client = await self._get_client()
            response = await client.post(
                self.endpoint,
                json={
                    "api_key": self._key,
                    "query": query,
                    "search_depth": "basic",
                    "include_answer": False,
                    "include_raw_content": False,
                    "max_results": min(num_results, 20),
                },
            )
            self._raise_if_error(response)
            data = response.json()
        except TransientProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            signal = self._classify(exc)
            raise TransientProviderError(signal or "5xx", str(exc)) from exc

        results: list[SearchResult] = []
        for item in (data.get("results") or []) or []:
            url = clean_url(str(item.get("url", "") or ""))
            if not url:
                continue
            title = str(item.get("title", "") or "").strip()
            snippet = str(item.get("content", "") or title).strip()
            try:
                score = float(item.get("score", 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            results.append(SearchResult(
                title=title or url,
                url=url,
                snippet=snippet or title,
                engine="tavily",
                backend=self.name,
                score=max(0.0, min(1.0, score)),
                category=self.category,
                published_date=str(item.get("published_date", "") or "") or None,
                raw=item,
            ))
        return [ensure_snippet(r) for r in results[:num_results]]
