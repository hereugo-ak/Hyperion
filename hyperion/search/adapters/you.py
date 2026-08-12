"""You.com Web Search API adapter — the largest-wallet secondary (§4/§8).

Search-only: plain /v1/search, no smart/rag/answer. Largest wallet ($200 credit)
so it absorbs the most paid volume.

OVERHAUL5 W0 (D-01, 2026-08-13): the old endpoint ``api.ydc-index.io/search``
returned 403 "Missing Authentication Token" — the API moved to
``ydc-index.io/v1/search`` with a ``count`` body param and a
``results.web[]`` response shape. Verified live (scripts/check_you_yep_search.py):
HTTP 200, 10 results. Docs: docs/YOU_YEP_API_FINDINGS.md
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from hyperion.search.adapters.base import BaseAdapter, TransientProviderError
from hyperion.search.types import SearchResult, clean_url, ensure_snippet

logger = logging.getLogger(__name__)


class YouAdapter(BaseAdapter):
    name = "You"
    endpoint = "https://ydc-index.io/v1/search"  # OVERHAUL5 W0: was api.ydc-index.io/search (dead)
    timeout_s = 15.0
    category = "web"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._key = self._api_key("you_api_key")

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self._key:
            headers["X-API-Key"] = self._key
        return headers

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[SearchResult]:
        if not self._key:
            logger.debug("you.com: no HYPERION_YOU_API_KEY — skipping")
            return []
        try:
            client = await self._get_client()
            response = await client.post(
                self.endpoint,
                json={"query": query, "count": min(num_results, 20)},
            )
            self._raise_if_error(response)
            data = response.json()
        except TransientProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            signal = self._classify(exc)
            raise TransientProviderError(signal or "5xx", str(exc)) from exc

        results: list[SearchResult] = []
        for hit in (data.get("results") or {}).get("web") or []:
            url = clean_url(str(hit.get("url", "") or ""))
            if not url:
                continue
            title = str(hit.get("title", "") or "").strip()
            snips = hit.get("snippets") or []
            snippet = str(snips[0]) if snips else str(hit.get("description", "") or "").strip()
            results.append(SearchResult(
                title=title or url,
                url=url,
                snippet=snippet or title,
                engine="you.com",
                backend=self.name,
                score=1.0,
                category=self.category,
                raw=hit,
            ))
        return [ensure_snippet(r) for r in results[:num_results]]
