"""Exa neural search adapter (§4/§8).

Search-only: POST /search with type auto. NO ``contents`` key — that flag
triggers extraction billing and Hyperion already extracts locally.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.types import SearchResult, clean_url, ensure_snippet

logger = logging.getLogger(__name__)


class ExaAdapter(BaseAdapter):
    name = "Exa"
    endpoint = "https://api.exa.ai/search"
    timeout_s = 20.0
    category = "web"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._key = self._api_key("exa_api_key")

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self._key:
            headers["x-api-key"] = self._key
        return headers

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[SearchResult]:
        if not self._key:
            logger.debug("exa: no HYPERION_EXA_API_KEY — skipping")
            return []
        try:
            client = await self._get_client()
            # type auto = neural + keyword; NO contents key (search-only §12).
            response = await client.post(
                self.endpoint,
                json={
                    "query": query,
                    "type": "auto",
                    "numResults": min(num_results, 20),
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
            # snippet: use the short field when present, else the title.
            snippet = str(
                item.get("snippet", "") or item.get("text", "") or title
            ).strip()
            try:
                score = float(item.get("score", 0.0) or 0.0)
            except (TypeError, ValueError):
                score = 0.0
            results.append(SearchResult(
                title=title or url,
                url=url,
                snippet=snippet or title,
                engine="exa",
                backend=self.name,
                score=max(0.0, min(1.0, score)),
                category=self.category,
                published_date=str(item.get("publishedDate", "") or "") or None,
                raw=item,
            ))
        return [ensure_snippet(r) for r in results[:num_results]]
