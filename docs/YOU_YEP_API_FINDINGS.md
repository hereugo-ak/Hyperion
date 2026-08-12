# You.com & Yep.com Search API: Findings, Root Cause, Fix

**Date:** 2026-08-12
**Status:** Both APIs verified WORKING with the keys in `.env`. Hyperion's adapters are stale (old API specs) and were hitting dead endpoints/auth shapes.

---

## TL;DR

| Provider | Key in `.env` | Hyperion adapter state | Verdict |
|---|---|---|---|
| You.com | `HYPERION_YOU_API_KEY` (`ydc-sk-...`) | **STALE** (old endpoint, old body param, old response shape) | API + key work fine |
| Yep.com | `HYPERION_YEP_API_KEY` (`yep_...`) | **STALE** (dead endpoint, wrong HTTP method, wrong response shape) | API + key work fine (paid $0.004, balance $10.00 -> $9.996) |

The keys are valid. The failure in Hyperion is caused by adapters written against API specs that have since changed. Fix = update the two adapters (code below).

---

## Verification evidence (live, 2026-08-12)

Standalone script `scripts/check_you_yep_search.py` (pure stdlib, reads keys straight from `.env`, search only, no extraction), query: `top 10 fastest blockchain with highest tps`

### You.com: HTTP 200, 10 web results in 1.94s
1. 10 Fastest Blockchains by TPS 2026 | Webopedia
   https://www.webopedia.com/crypto/learn/fastest-blockchains-tps/
2. Top 10 Fastest Cryptocurrency With Highest TPS in 2026 | NowPayments
   https://nowpayments.io/blog/top-10-cryptos-with-fastest-transactions
3. Fastest Crypto Network Blockchains by TPS: Top 10 in 2026 | DirectionsMag
   https://www.directionsmag.com/crypto/fastest-crypto-network-blockchains-tps
4. Fastest Blockchains by TPS (Transactions Per Second) | Chainspect
   https://chainspect.app/dashboard
5. The Fastest Blockchain Processed 91M Transactions in a Day | CoinGecko
   https://www.coingecko.com/research/publications/fastest-blockchains
6. Understanding TPS: Which Blockchains Are the Fastest? | Hashlock
   https://hashlock.com/blog/understanding-tps-which-blockchains-are-the-fastest
7. Top 10 Fastest Blockchains by Maximum Daily Average TPS | CryptoRank
   https://cryptorank.io/news/feed/17ef7-top-10-fastest-blockchains-tps
8. Top 10 Cryptos with the Fastest Transactions | Fuze Finance
   https://fuze.finance/blog/cryptocurrencies-transaction-speeds
9. Top-10 Fastest Crypto to Send | Cryptomus
   https://cryptomus.com/blog/top-10-blockchains-by-transaction-speed
10. Fastest Blockchains Ranked by TPS | Gate Wiki
    https://www.gate.com/crypto-wiki/article/fastest-blockchains-ranked-by-transactions-per-second-20251220

### Yep.com: HTTP 200, 10 results in 1.62s, cost $0.004, balance $10.00 -> $9.996
1. Top 10 Fastest Blockchains by Maximum Daily Average TPS | CryptoRank
   https://cryptorank.io/ru/news/feed/17ef7-top-10-fastest-blockchains-tps
2. Coingeck: Top 25 Fastest Blockchains by Max Daily Average TPS | Bitget News
   https://www.bitget.com/news/detail/12560604005779
3. Solana leads as the fastest among large-scale blockchains | Bitget News
   https://www.bitget.com/news/detail/12560604005364
4. ICP and TON Dominate April Blockchain Speed Rankings | BlockchainReporter
   https://www.binance.com/en/square/post/23631760423818
5. #Top 5 Fastest Blockchains Revolutionizing | Binance Square
   https://www.binance.com/en/square/post/8394507500897
6. Solana Proves to Be the Fastest Blockchain with 1,504 TPS | Bitget News
   https://www.bitget.com/news/detail/12560604008297
7. The Fastest Blockchain Processed 91M Transactions in a Day | CoinGecko
   https://www.coingecko.com/research/publications/fastest-blockchains
8. Top 10 Fastest Blockchains by Maximum Daily A... | CoinStats
   https://coinstats.app/news/713d897429c2e1801a0528d6fa6eb6d9c238018ebe6fd85e5d6b97fc95afb8b7_Top-10-Fastest-Blockchains-by-Maximum-Daily-Average-TPS/
9. ICP and TON Dominate April Blockchain Speed R... | CoinStats
   https://coinstats.app/news/1c730cd64ae25182d2e19a35234fb2710197148f00a64132c6a9a197691b173a_ICP-and-TON-Dominate-April-Blockchain-Speed-Rankings-with-Highest-TPS/
10. Solana Ranked The World's Fastest Blockchain, Outshining Ethereum, Polygon | CryptoRank
    https://cryptorank.io/ru/news/feed/ed04c-solana-ranked-the-worlds-fastest-blockchain-outshining-ethereum-polygon

---

## What was wrong

### 1. You.com adapter: `hyperion/search/adapters/you.py`

Error seen when calling the old endpoint: `403 {"message":"Missing Authentication Token"}`.

| Aspect | Old (in Hyperion) | Current (verified on you.com/docs, 2026-08-12) |
|---|---|---|
| Endpoint | `POST https://api.ydc-index.io/search` | `POST https://ydc-index.io/v1/search` |
| Auth header | `X-API-Key: <key>` | `X-API-Key: <key>` (unchanged, still correct) |
| Request body | `{"query": ..., "num_web_results": N}` | `{"query": ..., "count": N}` |
| Response shape | `data["hits"][].{title,url,snippet}` | `data["results"]["web"][].{url,title,description,snippets:[...]}` |

The `api.ydc-index.io/search` path is dead; the API gateway returns "Missing Authentication Token" / "Forbidden" for it. `num_web_results` is ignored. The response no longer has `hits`; snippets are an array under `snippets[]` with a `description` fallback.

### 2. Yep.com adapter: `hyperion/search/adapters/yep.py`

Error seen: `403` with an HTML block page on `api.yep.com`.

Yep pivoted: the old consumer search engine API is gone. It is now the **YEP Search API by Ahrefs** at `platform.yep.com` (official docs: https://platform.yep.com/api-documentation).

| Aspect | Old (in Hyperion) | Current (verified on platform.yep.com/api-documentation, 2026-08-12) |
|---|---|---|
| Endpoint | `GET https://api.yep.com/fs/2/search` | `POST https://platform.yep.com/api/search` |
| Auth header | `Authorization: Bearer <key>` | `Authorization: Bearer <key>` (unchanged, still correct; `yep_` prefix optional) |
| Request | query params `q`, `gl`, `max_results`, `safe_search` | JSON body `{"query", "type": "basic", "limit", "language": ["en"], "location": "US", "safe_search"}` |
| Response shape | `data["web"]["results"][]` | `data["results"][]` (plus `api_cost`, `balance`, `request_id`) |
| Limits/pricing | n/a | 60 req/min, 3,600/hr, 86,400/day; $4 per 1,000 basic requests (first 20 results) |

---

## How to fix

Both adapters keep their class structure, `BaseAdapter` inheritance, `_api_key("you_api_key" / "yep_api_key")` settings lookup (env var names unchanged), and error handling. Only endpoint, HTTP method/body, and response parsing change.

### Fixed `hyperion/search/adapters/you.py`

```python
"""You.com Web Search API adapter — the largest-wallet secondary (§4/§8).

Search-only: plain /v1/search, no smart/rag/answer.
Docs (2026-08-12): https://you.com/docs/quickstart
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
    endpoint = "https://ydc-index.io/v1/search"  # FIXED: was api.ydc-index.io/search (dead)
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
                json={"query": query, "count": min(num_results, 20)},  # FIXED: num_web_results -> count
            )
            self._raise_if_error(response)
            data = response.json()
        except TransientProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            signal = self._classify(exc)
            raise TransientProviderError(signal or "5xx", str(exc)) from exc

        # FIXED: new shape {results: {web: [{url, title, snippets: [...], description}]}}
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
```

### Fixed `hyperion/search/adapters/yep.py`

```python
"""Yep adapter — the last resort (§4/§8).

Yep pivoted to the Ahrefs "YEP Search API" (platform.yep.com).
Docs (2026-08-12): https://platform.yep.com/api-documentation
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
    endpoint = "https://platform.yep.com/api/search"  # FIXED: was api.yep.com/fs/2/search (dead)
    timeout_s = 15.0
    category = "web"

    def __init__(self, settings: Any | None = None) -> None:
        super().__init__(settings)
        self._key = self._api_key("yep_api_key")

    def _headers(self) -> dict[str, str]:
        headers = super()._headers()
        if self._key:
            headers["Authorization"] = f"Bearer {self._key}"  # unchanged, still correct
        return headers

    async def search(
        self, query: str, num_results: int = 10
    ) -> list[SearchResult]:
        try:
            client = await self._get_client()
            response = await client.post(  # FIXED: GET -> POST
                self.endpoint,
                json={
                    "query": query,
                    "type": "basic",
                    "limit": min(num_results, 100),
                    "language": ["en"],
                    "location": "US",
                },
            )
            self._raise_if_error(response)
            data = response.json()
        except TransientProviderError:
            raise
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            signal = self._classify(exc)
            raise TransientProviderError(signal or "5xx", str(exc)) from exc

        # FIXED: new shape {success, results: [...], api_cost, balance}
        results: list[SearchResult] = []
        for item in data.get("results") or []:
            url = clean_url(str(item.get("url", "") or ""))
            if not url:
                continue
            title = str(item.get("title", "") or "").strip()
            snippet = str(item.get("description") or item.get("snippet") or title).strip()
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
```

---

## Verification script (already in repo)

`scripts/check_you_yep_search.py` is standalone and dependency-free (stdlib only). It loads both keys from `.env`, fires the real query at both live APIs, and prints HTTP status, timing, and the top 10 results.

```bash
cd /mnt/wsl/.../Hyperion   # or the WSL mount path you use
python scripts/check_you_yep_search.py "top 10 fastest blockchain with highest tps"
```

Expected output: `HTTP 200` from both providers with 10 results each. Note: each Yep call costs $0.004 against the $10 balance.

---

## Notes / gotchas

- **Keys unchanged.** No need to rotate or regenerate anything in `.env`. The `yep_` prefix is optional for auth (docs state both prefixed and unprefixed keys work).
- **You.com response field names:** snippets come as an array (`snippets: ["..."]`); `description` is the plain-text fallback. Old `hits` field no longer exists.
- **Yep result fields:** verified live that each result has `title` and `url`. The snippet/highlight field name may vary by `type` (`basic` vs `highlights`); use `description` with `snippet` fallback, or switch `type` to `"highlights"` if you want content excerpts.
- **Yep pricing:** $4 per 1,000 basic requests, first 20 results included, each extra result $0.001. Balance credited: $10.00.
- **Rate limits:** Yep caps at 60 req/min, 3,600/hr, 86,400/day per key.
- The old `api.yep.com/fs/2/search` and `api.ydc-index.io/search` endpoints return 403; do not use them.
