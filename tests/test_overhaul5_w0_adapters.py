"""OVERHAUL5 W0 (D-01) — paid-adapter regression tests.

The 2026-08-12 run proved the You.com and Yep adapters were written against
dead API specs (403 on every call, 0 paid records in the ledger). Both were
repointed to their verified live endpoints (docs/YOU_YEP_API_FINDINGS.md).

Fail-first contract: each parsing test feeds the NEW live response shape and
asserts >= 1 result. On the pre-W0 adapters these fail (old code read
``hits`` / ``web.results`` and got nothing); on the fixed adapters they pass.
Live tests are skipped unless the real keys are present (WSL .env).
"""

from __future__ import annotations

import types

import pytest

from hyperion.search.adapters.yep import YepAdapter
from hyperion.search.adapters.you import YouAdapter


class _FakeResponse:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise AssertionError(f"unexpected status {self.status_code}")


class _FakeClient:
    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._resp = _FakeResponse(payload, status_code)
        self.last_url: str | None = None
        self.last_json: dict | None = None

    async def post(self, url: str, json: dict | None = None, **_: object):
        self.last_url = url
        self.last_json = json
        return self._resp

    async def get(self, url: str, **_: object):
        self.last_url = url
        return self._resp

    async def aclose(self) -> None:
        return None


def _settings(**kw: str) -> types.SimpleNamespace:
    base = {
        "you_api_key": "test-you-key",
        "yep_api_key": "test-yep-key",
    }
    base.update(kw)
    return types.SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_you_adapter_parses_live_response_shape() -> None:
    """New shape: {results: {web: [{url, title, snippets, description}]}}."""
    adapter = YouAdapter(settings=_settings())
    fake = _FakeClient(
        {
            "results": {
                "web": [
                    {
                        "url": "https://example.com/a",
                        "title": "India Manufacturing Tracker 2026",
                        "snippets": ["India's factory output rose 7.2%."],
                    },
                    {
                        "url": "https://example.com/b",
                        "title": "Second Result",
                        "description": "A plain-text fallback snippet.",
                    },
                ]
            }
        }
    )
    async def _fake_get_client() -> _FakeClient:
        return fake

    adapter._get_client = _fake_get_client  # type: ignore[method-assign]
    results = await adapter.search("india manufacturing", num_results=5)
    assert len(results) == 2
    assert results[0].engine == "you.com"
    assert results[0].snippet == "India's factory output rose 7.2%."
    assert results[1].snippet == "A plain-text fallback snippet."
    assert fake.last_json == {"query": "india manufacturing", "count": 5}
    assert "v1/search" in fake.last_url


@pytest.mark.asyncio
async def test_yep_adapter_parses_live_response_shape() -> None:
    """New shape: {success, results: [{title, url, description}], api_cost}."""
    adapter = YepAdapter(settings=_settings())
    fake = _FakeClient(
        {
            "success": True,
            "api_cost": {"cost": 0.004},
            "balance": {"before": "9.9960", "after": 9.992},
            "results": [
                {
                    "url": "https://cryptorank.io/news/feed/17ef7",
                    "title": "Top 10 Fastest Blockchains",
                    "description": "Ranked by max daily average TPS.",
                },
                {
                    "url": "https://bitget.com/news/detail/12560604005779",
                    "title": "Coingecko: Top 25 Fastest Blockchains",
                },
            ],
        }
    )
    async def _fake_get_client() -> _FakeClient:
        return fake

    adapter._get_client = _fake_get_client  # type: ignore[method-assign]
    results = await adapter.search("fastest blockchain tps", num_results=5)
    assert len(results) == 2
    assert results[0].engine == "yep"
    assert results[0].snippet == "Ranked by max daily average TPS."
    assert results[1].snippet == "Coingecko: Top 25 Fastest Blockchains"  # title fallback
    assert fake.last_json == {
        "query": "fastest blockchain tps",
        "type": "basic",
        "limit": 5,
        "language": ["en"],
        "location": "US",
    }
    assert "platform.yep.com" in fake.last_url


def _has_key(attr: str) -> bool:
    try:
        from hyperion.config import get_settings

        return bool(getattr(get_settings(), attr, ""))
    except Exception:  # noqa: BLE001 - no settings in CI
        return False


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_key("you_api_key"), reason="HYPERION_YOU_API_KEY not set (WSL .env)")
async def test_you_live_endpoint() -> None:
    """Real endpoint, real key — the outcome test O4 never had."""
    from hyperion.config import get_settings

    adapter = YouAdapter(settings=None)
    adapter._key = get_settings().you_api_key
    results = await adapter.search("india manufacturing competitiveness 2026", num_results=5)
    assert len(results) >= 1, "You.com live search must return results (adapter stale -> 0)"
    assert all(r.url and r.title for r in results)


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_key("yep_api_key"), reason="HYPERION_YEP_API_KEY not set (WSL .env)")
async def test_yep_live_endpoint() -> None:
    """Real endpoint, real key."""
    from hyperion.config import get_settings

    adapter = YepAdapter(settings=None)
    adapter._key = get_settings().yep_api_key
    results = await adapter.search("india manufacturing competitiveness 2026", num_results=5)
    assert len(results) >= 1, "Yep live search must return results (adapter stale -> 0)"
    assert all(r.url and r.title for r in results)
