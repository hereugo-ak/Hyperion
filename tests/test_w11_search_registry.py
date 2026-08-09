"""W-11 regression gates for the no-block SearXNG engine contract."""

from __future__ import annotations

import asyncio
import collections
from pathlib import Path
from typing import Any

import pytest
import yaml

from hyperion.infra.services import searxng_spec
from hyperion.tools.engine_health import EngineHealthTracker, EngineState
from hyperion.tools.searxng import (
    TIER_C_ENGINES,
    EngineRegistryMismatch,
    EngineTokenBucket,
    reconcile_engine_registry,
    referenced_engines,
)

ROOT = Path(__file__).resolve().parents[1]
SETTINGS = ROOT / "searxng_settings.yml"


class NoDuplicateKeysLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects silent mapping-key replacement."""


def _construct_unique_mapping(
    loader: yaml.SafeLoader,
    node: yaml.MappingNode,
    deep: bool = False,
) -> dict[Any, Any]:
    keys = [loader.construct_object(key, deep=deep) for key, _ in node.value]
    duplicates = sorted(
        key for key, count in collections.Counter(keys).items() if count > 1
    )
    if duplicates:
        raise AssertionError(f"duplicate YAML keys: {duplicates}")
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


NoDuplicateKeysLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


def _settings() -> dict[str, Any]:
    with SETTINGS.open(encoding="utf-8") as handle:
        return yaml.load(handle, Loader=NoDuplicateKeysLoader)


def test_settings_registry_is_exact_and_contains_no_tier_c_engines() -> None:
    data = _settings()
    enabled = {
        str(engine["name"])
        for engine in data["engines"]
        if not engine.get("disabled", False)
    }
    assert enabled == referenced_engines()
    assert enabled.isdisjoint(TIER_C_ENGINES)
    # P1.2 (overhaul §6 P1, 2026-08-10): ``keep_only`` lists every DECLARED
    # engine (incl. disabled mojeek/yep); the non-disabled ``enabled`` set is
    # exactly the active referenced set.
    declared = {str(engine["name"]) for engine in data["engines"]}
    assert set(data["use_default_settings"]["engines"]["keep_only"]) == declared
    assert data["default_doi_resolver"] in data["doi_resolvers"]
    assert "default_doi_resolver" not in data["search"]
    assert data["server"]["secret_key"] == "${SEARXNG_SECRET}"


def test_runtime_secret_is_generated_or_operator_supplied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SEARXNG_SCHOLAR_SECRET", raising=False)
    first = searxng_spec().env["SEARXNG_SECRET"]
    second = searxng_spec().env["SEARXNG_SECRET"]
    assert len(first) >= 48
    assert first != second

    monkeypatch.setenv("SEARXNG_SCHOLAR_SECRET", "operator-controlled-secret")
    assert searxng_spec().env["SEARXNG_SECRET"] == "operator-controlled-secret"


class _Response:
    def __init__(self, engines: list[dict[str, Any]]) -> None:
        self._payload = {"engines": engines}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._payload


class _Client:
    def __init__(self, engines: list[dict[str, Any]]) -> None:
        self.engines = engines
        self.requested_url = ""

    async def get(self, url: str, **kwargs: Any) -> _Response:
        self.requested_url = url
        assert kwargs["headers"]["X-Forwarded-For"] == "127.0.0.1"
        return _Response(self.engines)


@pytest.mark.asyncio
async def test_reconciliation_passes_for_exact_running_registry() -> None:
    client = _Client([{"name": name, "enabled": True} for name in referenced_engines()])
    report = await reconcile_engine_registry("http://127.0.0.1:8888", client=client)  # type: ignore[arg-type]
    assert report.ok
    assert report.enabled == frozenset(referenced_engines())
    assert client.requested_url.endswith("/config")


@pytest.mark.asyncio
async def test_reconciliation_fails_closed_with_named_drift() -> None:
    enabled = referenced_engines() - {"crossref"}
    client = _Client(
        [{"name": name, "enabled": True} for name in enabled]
        + [{"name": "google", "enabled": True}]
    )
    with pytest.raises(EngineRegistryMismatch) as caught:
        await reconcile_engine_registry("http://127.0.0.1:8888", client=client)  # type: ignore[arg-type]
    message = str(caught.value)
    assert "crossref" in message
    assert "google" in message
    assert "searxng_settings.yml" in message


@pytest.fixture()
def tracker(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> EngineHealthTracker:
    monkeypatch.setenv(
        "HYPERION_ENGINE_HEALTH_STATE", str(tmp_path / "engine-health.json")
    )
    instance = EngineHealthTracker()
    instance.reset()
    return instance


def test_health_states_captcha_eviction_and_degradation(
    tracker: EngineHealthTracker,
) -> None:
    assert tracker.state("brave") is EngineState.HEALTHY
    tracker.record_response([["brave", "timeout"]], [])
    assert tracker.state("brave") is EngineState.COOLING

    tracker.record_response(
        [["crossref", "HTTP error 403 (suspended_time=3600)"]], []
    )
    assert tracker.state("crossref") is EngineState.SUSPENDED

    tracker.record_response([["mojeek", "SearxEngineCaptchaException"]], [])
    assert tracker.state("mojeek") is EngineState.SUSPENDED
    tracker.record_response([], ["mojeek"])
    assert tracker.state("mojeek") is EngineState.SUSPENDED

    event = tracker.record_degradation_if_needed(
        {"brave", "crossref", "mojeek", "wikipedia"}, floor=4
    )
    assert event is not None
    assert event["type"] == "retrieval_engine_pool_degraded"
    assert event["healthy"] == 1
    assert tracker.degradation_events() == [event]


@pytest.mark.asyncio
async def test_token_bucket_is_process_wide_per_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    # The local process-wide fallback must be exercised deterministically.
    # When a real Valkey container is running, the shared reservation path
    # answers first and the fallback never runs — the test then flips between
    # environments (and between test orderings, since the shared store keeps
    # pre-warmed keys). Simulate "shared store unavailable" so this test
    # always covers the path it is named after.
    async def shared_store_unavailable(*_args: Any, **_kwargs: Any) -> None:
        return None

    monkeypatch.setattr(
        "hyperion.tools.searxng.get_valkey_store",
        lambda: type("NoValkey", (), {
            "reserve_engine_window": shared_store_unavailable,
        })(),
    )
    EngineTokenBucket._lock = None
    EngineTokenBucket._next_allowed = {}
    monkeypatch.setattr(EngineTokenBucket, "interval_seconds", 0.05)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    monkeypatch.setattr("hyperion.tools.searxng.random.uniform", lambda _a, _b: 0.0)

    await EngineTokenBucket.acquire({"brave"})
    await EngineTokenBucket.acquire({"brave"})

    assert len(waits) == 1
    assert waits[0] > 0.0

    EngineTokenBucket._lock = None
    EngineTokenBucket._next_allowed = {}
