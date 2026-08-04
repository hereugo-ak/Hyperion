"""W-12 regression gates for isolated SearXNG replicas and lifecycle."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
import yaml

from hyperion.infra import services
from hyperion.infra.searxng_profiles import build_profiles
from hyperion.tools.searxng import (
    EngineRegistryMismatch,
    EngineTokenBucket,
    SearchResponse,
    SearchResult,
    SearxNGClient,
    SearxngEndpoint,
    SearxngPool,
    referenced_engines,
)
from hyperion.tools.valkey import ValkeyStore, get_valkey_store

ROOT = Path(__file__).resolve().parents[1]


def test_replica_registry_is_complete_and_disjoint() -> None:
    seen: set[str] = set()
    for replica in services.SEARXNG_REPLICAS:
        engines = set(replica.engines)
        assert not seen.intersection(engines)
        seen.update(engines)
    assert seen == referenced_engines()
    assert [replica.port for replica in services.SEARXNG_REPLICAS] == [8888, 8889, 8890]


def test_generated_profiles_match_registry_and_have_unique_identity() -> None:
    base = yaml.safe_load((ROOT / "searxng_settings.yml").read_text(encoding="utf-8"))
    profiles = build_profiles(base)
    valkey_urls: set[str] = set()
    for replica in services.SEARXNG_REPLICAS:
        profile = profiles[replica.profile]
        assert {engine["name"] for engine in profile["engines"]} == set(replica.engines)
        assert set(
            profile["use_default_settings"]["engines"]["keep_only"]
        ) == set(replica.engines)
        assert profile["server"]["secret_key"] == "${SEARXNG_SECRET}"
        assert profile["default_doi_resolver"] == "doi.org"
        assert "default_doi_resolver" not in profile["search"]
        valkey_urls.add(profile["valkey"]["url"])
    assert len(valkey_urls) == 3
    assert all(url.startswith("valkey://valkey:6379/") for url in valkey_urls)


def test_container_specs_are_isolated_loopback_only_and_flare_is_opt_in() -> None:
    specs = services.searxng_specs()
    assert len(specs) == 3
    assert len({spec.name for spec in specs}) == 3
    assert len({spec.host_port for spec in specs}) == 3
    assert len({spec.named_volumes[0][0] for spec in specs}) == 3
    assert len({spec.env["SEARXNG_SECRET"] for spec in specs}) == 3
    for spec in specs:
        argv = services._docker_run_argv(spec, spec.image)
        assert f"127.0.0.1:{spec.host_port}:8080" in argv
        assert argv[argv.index("--network") + 1] == services.RETRIEVAL_NETWORK
        assert spec.health_path == "/config"
        assert spec.health_headers["X-Forwarded-For"] == "127.0.0.1"
    valkey_argv = services._docker_run_argv(
        services.valkey_spec(), services.VALKEY_IMAGE
    )
    assert valkey_argv[valkey_argv.index("--network-alias") + 1] == "valkey"
    assert "flaresolverr" not in {spec.name for spec in services.all_specs()}
    assert "flaresolverr" in {
        spec.name for spec in services.all_specs(include_flaresolverr=True)
    }


def test_profile_routing_least_outstanding_and_cross_profile_fallback() -> None:
    pool = SearxngPool.from_config()
    assert pool.endpoint_for(category="science").profile == "scholar"
    assert pool.endpoint_for(category="medical").profile == "scholar"
    assert pool.endpoint_for(category="it").profile == "reference"
    assert pool.endpoint_for(category="geo").profile == "reference"
    assert pool.endpoint_for(category="general").profile == "web"
    assert pool.endpoint_for(category="news").profile == "web"

    first_web = pool.endpoint_for(category="general")
    duplicate = SearxngEndpoint(
        "http://127.0.0.1:9890",
        "web",
        9890,
        first_web.engines,
    )
    pool.endpoints.append(duplicate)
    first_web.outstanding = 2
    assert pool.endpoint_for(category="general") is duplicate

    pool.mark_unhealthy(first_web.port)
    pool.mark_unhealthy(duplicate.port)
    fallback = pool.endpoint_for(category="general")
    assert fallback.profile == "reference"
    assert pool.engines_for(
        fallback,
        category="general",
        requested_engines={"mojeek", "brave"},
        explicit=False,
    ) == {"wikipedia"}


def test_explicit_engines_are_bound_to_exactly_one_profile() -> None:
    pool = SearxngPool.from_config()
    reference = pool.endpoint_for(category="general", requested_engines={"wikipedia"})
    assert reference.profile == "reference"
    assert pool.engines_for(
        reference,
        category="general",
        requested_engines={"wikipedia"},
        explicit=True,
    ) == {"wikipedia"}

    with pytest.raises(
        EngineRegistryMismatch,
        match="crosses isolated SearXNG profiles",
    ):
        pool.endpoint_for(
            category="general",
            requested_engines={"wikipedia", "crossref"},
        )

    pool.mark_unhealthy(reference.port)
    with pytest.raises(RuntimeError, match="No healthy SearXNG endpoint"):
        pool.endpoint_for(category="general", requested_engines={"wikipedia"})


@pytest.mark.asyncio
async def test_valkey_store_uses_internal_container_and_atomic_engine_script(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    async def fake_run(command: list[str], timeout: float = 30.0):
        commands.append(command)
        return 0, "125", ""

    monkeypatch.setattr("hyperion.tools.valkey.run_command", fake_run)
    wait = await ValkeyStore().reserve_engine_window(
        {"brave", "mojeek"},
        interval_ms=2000,
        jitter_ms=100,
    )

    assert wait == 0.125
    command = commands[0]
    assert command[:4] == ["docker", "exec", "hyperion-valkey", "valkey-cli"]
    assert "EVAL" in command
    assert "hyperion:retrieval:engine:brave" in command
    assert "hyperion:retrieval:engine:mojeek" in command


@pytest.mark.asyncio
async def test_token_bucket_prefers_cross_process_valkey_reservation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    waits: list[float] = []

    async def reserve(*_args, **_kwargs):
        return 0.25

    async def fake_sleep(delay: float) -> None:
        waits.append(delay)

    monkeypatch.setattr(get_valkey_store(), "reserve_engine_window", reserve)
    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    EngineTokenBucket._next_allowed = {}
    await EngineTokenBucket.acquire({"brave"})
    assert waits == [0.25]
    assert EngineTokenBucket._next_allowed == {}


@pytest.mark.asyncio
async def test_dead_web_profile_fails_over_to_reference_engines(monkeypatch) -> None:
    """Blocked web upstreams must not leave the evidence corpus empty."""
    client = SearxNGClient()
    client._pool = SearxngPool([
        SearxngEndpoint(
            "http://web", "web", 8890, frozenset({"brave", "mojeek"})
        ),
        SearxngEndpoint(
            "http://reference",
            "reference",
            8889,
            frozenset({"wikipedia", "github", "stackexchange"}),
        ),
        SearxngEndpoint(
            "http://scholar", "scholar", 8888, frozenset({"crossref"})
        ),
    ])

    class Health:
        def __init__(self) -> None:
            self.dead: set[str] = set()

        def filter_available(self, engines):
            return [engine for engine in engines if engine not in self.dead]

        def record_response(self, unresponsive_engines, responding_engines):
            self.dead.update(str(entry[0]) for entry in unresponsive_engines)
            self.dead.difference_update(responding_engines)

        def record_degradation_if_needed(self, engines, *, floor=4):
            return None

    health = Health()
    monkeypatch.setattr("hyperion.tools.searxng.get_engine_health", lambda: health)
    monkeypatch.setattr(EngineTokenBucket, "acquire", staticmethod(lambda engines: _done()))

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    requested: list[tuple[str, str]] = []

    class Http:
        def __init__(self, base_url):
            self.base_url = base_url

        async def get(self, path, params=None):
            requested.append((self.base_url, params["engines"]))
            if self.base_url == "http://web":
                return Response({
                    "results": [],
                    "unresponsive_engines": [
                        ["brave", "HTTP 429 suspended_time=180"],
                        ["mojeek", "HTTP 403 suspended_time=180"],
                    ],
                })
            return Response({
                "results": [{
                    "title": "Grounding source",
                    "url": "https://en.wikipedia.org/wiki/Market_research",
                    "content": "Independent reference evidence",
                    "engine": "wikipedia",
                    "score": 1.0,
                }],
                "unresponsive_engines": [],
            })

    async def _get_client(base_url=None):
        return Http(base_url)

    async def _done():
        return None

    monkeypatch.setattr(client, "_get_client", _get_client)
    response = await client._search_searxng_json(
        query="market evidence",
        num_results=5,
        categories="general",
        language="en",
        time_range="",
        engines="brave,mojeek",
        safesearch=0,
    )

    assert response is not None
    assert response.results[0].engine == "wikipedia"
    assert requested == [
        ("http://web", "brave,mojeek"),
        ("http://reference", "wikipedia"),
    ]
    assert client._pool.endpoints[0].circuit_open is True
    await client.close()


@pytest.mark.asyncio
async def test_zero_results_walk_each_profile_once(monkeypatch) -> None:
    """An empty healthy profile must not be mistaken for an empty corpus."""
    client = SearxNGClient()
    client._pool = SearxngPool([
        SearxngEndpoint("http://web", "web", 8890, frozenset({"brave"})),
        SearxngEndpoint(
            "http://reference",
            "reference",
            8889,
            frozenset({"wikipedia", "github", "stackexchange"}),
        ),
        SearxngEndpoint("http://scholar", "scholar", 8888, frozenset({"crossref"})),
    ])
    requested: list[tuple[str, str]] = []

    class Health:
        def filter_available(self, engines):
            return list(engines)

        def record_response(self, unresponsive_engines, responding_engines):
            return None

        def record_degradation_if_needed(self, engines, *, floor=4):
            return None

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self.payload

    class Http:
        def __init__(self, base_url):
            self.base_url = base_url

        async def get(self, path, params=None):
            requested.append((self.base_url, params["engines"]))
            if self.base_url != "http://scholar":
                return Response({"results": [], "unresponsive_engines": []})
            return Response({
                "results": [{
                    "title": "Independent study",
                    "url": "https://doi.org/10.1000/example",
                    "content": "Cross-profile evidence",
                    "engine": "crossref",
                }],
                "unresponsive_engines": [],
            })

    async def _get_client(base_url=None):
        return Http(base_url)

    async def _done():
        return None

    monkeypatch.setattr("hyperion.tools.searxng.get_engine_health", lambda: Health())
    monkeypatch.setattr(EngineTokenBucket, "acquire", staticmethod(lambda engines: _done()))
    monkeypatch.setattr(client, "_get_client", _get_client)

    response = await client._search_searxng_json(
        query="market evidence",
        num_results=5,
        categories="general",
        language="en",
        time_range="",
        engines="brave",
        safesearch=0,
    )

    assert response is not None
    assert response.results[0].engine == "crossref"
    assert requested == [
        ("http://web", "brave"),
        ("http://reference", "wikipedia"),
        ("http://scholar", "crossref"),
    ]
    assert len({base_url for base_url, _engines in requested}) == 3
    await client.close()


@pytest.mark.asyncio
async def test_result_cache_is_normalized_and_shared(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored: dict[str, object] = {}

    async def set_json(key: str, value: dict[str, object], ttl_seconds: int) -> bool:
        stored.update(key=key, value=value, ttl=ttl_seconds)
        return True

    async def get_json(key: str):
        if key == stored.get("key"):
            return stored.get("value")
        return None

    store = get_valkey_store()
    monkeypatch.setattr(store, "set_json", set_json)
    monkeypatch.setattr(store, "get_json", get_json)
    client = SearxNGClient()
    first_key = client._cache_key("  Market   SIZE ", engines="brave,mojeek")
    second_key = client._cache_key("market size", engines="mojeek, brave")
    assert first_key == second_key

    response = SearchResponse(
        query="market size",
        results=[SearchResult(title="Authority", url="https://example.test", engine="brave")],
        total=1,
        engines_used=["brave"],
    )
    await client._set_cached(first_key, response)
    client._cache.clear()
    cached = await client._get_cached(first_key)
    assert cached is not None
    assert cached.cached is True
    assert cached.results[0].url == "https://example.test"
    assert stored["ttl"] == client.CACHE_TTL_SECONDS
    await client.close()


@pytest.mark.asyncio
async def test_startup_removes_legacy_single_instance_and_inactive_flare(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []
    legacy_removed = asyncio.Event()

    async def fake_run_command(command: list[str], timeout: float = 30.0):
        commands.append(command)
        if command[:4] == ["docker", "rm", "-f", "flaresolverr"]:
            legacy_removed.set()
        return 0, "network-ready", ""

    async def fake_ensure(spec, *, on_progress=None):
        # No desired replica may attempt to bind 8888 before migration cleanup.
        assert legacy_removed.is_set()
        return services.ServiceStatus(spec.name, state=services.OK, ready=True)

    monkeypatch.setattr(services, "docker_available", lambda: True)
    monkeypatch.setattr(services, "run_command", fake_run_command)
    monkeypatch.setattr(services, "ensure_container", fake_ensure)

    await services.start_services()

    assert ["docker", "rm", "-f", "searxng"] in commands
    assert ["docker", "rm", "-f", "flaresolverr"] in commands


@pytest.mark.asyncio
async def test_valkey_is_ready_before_replicas_start_concurrently(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    peak = 0
    valkey_ready = False

    async def fake_run_command(command: list[str], timeout: float = 30.0):
        return 0, "network-ready", ""

    async def fake_ensure(spec, *, on_progress=None):
        nonlocal active, peak, valkey_ready
        if spec.name == "hyperion-valkey":
            assert active == 0
            await asyncio.sleep(0)
            valkey_ready = True
        else:
            assert valkey_ready
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0)
            active -= 1
        return services.ServiceStatus(spec.name, state=services.OK, ready=True)

    monkeypatch.setattr(services, "docker_available", lambda: True)
    monkeypatch.setattr(services, "run_command", fake_run_command)
    monkeypatch.setattr(services, "ensure_container", fake_ensure)

    statuses = await services.start_services()
    assert set(statuses) == {spec.name for spec in services.all_specs()}
    assert peak == len(services.searxng_specs())


@pytest.mark.asyncio
async def test_shared_startup_deadline_returns_explicit_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_run_command(command: list[str], timeout: float = 30.0):
        return 0, "network-ready", ""

    async def never_ready(spec, *, on_progress=None):
        await asyncio.Event().wait()

    async def immediate_timeout(tasks, *, timeout):
        task_set = set(tasks)
        return set(), task_set

    monkeypatch.setattr(services, "docker_available", lambda: True)
    monkeypatch.setattr(services, "run_command", fake_run_command)
    monkeypatch.setattr(services, "ensure_container", never_ready)
    monkeypatch.setattr(services.asyncio, "wait", immediate_timeout)

    statuses = await services.start_services()
    assert set(statuses) == {spec.name for spec in services.all_specs()}
    assert all(status.state == services.FAIL for status in statuses.values())
    assert "shared" in statuses["hyperion-valkey"].detail
    for replica in services.SEARXNG_REPLICAS:
        assert "dependency" in statuses[replica.name].detail


@pytest.mark.asyncio
async def test_shutdown_is_concurrent_and_removes_owned_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    active = 0
    peak = 0
    commands: list[list[str]] = []

    async def fake_remove(name: str) -> None:
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1

    async def fake_run(command: list[str], timeout: float = 30.0):
        commands.append(command)
        return 0, "", ""

    monkeypatch.setattr(services, "docker_available", lambda: True)
    monkeypatch.setattr(services, "remove_container", fake_remove)
    monkeypatch.setattr(services, "run_command", fake_run)

    removed = await services.stop_services()
    assert all(removed.values())
    assert peak == len(services.MANAGED_CONTAINERS)
    assert ["docker", "network", "rm", services.RETRIEVAL_NETWORK] in commands


def test_compose_matches_pins_resources_and_network_exposure() -> None:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    definitions = compose["services"]
    for replica in services.SEARXNG_REPLICAS:
        item = definitions[f"searxng-{replica.profile}"]
        assert item["image"] == services.SEARXNG_IMAGE
        assert item["ports"] == [f"127.0.0.1:{replica.port}:8080"]
        assert item["mem_limit"] == "512m"
        assert item["cpus"] == 2.0
        assert item["read_only"] is True
        assert item["cap_drop"] == ["ALL"]
        assert item["security_opt"] == ["no-new-privileges:true"]
        healthcheck = item["healthcheck"]["test"]
        assert healthcheck[-1] == "http://127.0.0.1:8080/config"
        assert "--header=X-Forwarded-For: 127.0.0.1" in healthcheck
    assert definitions["valkey"]["image"] == services.VALKEY_IMAGE
    assert "ports" not in definitions["valkey"]
    assert definitions["flaresolverr"]["profiles"] == ["investigation"]
