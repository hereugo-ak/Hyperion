"""
HYPERION SearxNG Client — self-hosted meta-search, free, unlimited.

SearxNG is the primary search tool for ALL specialists. It aggregates
70+ search engines, has no API key, no rate limit, and no tracking.
It runs in Docker at the URL configured in settings.searxng_url.

This is NOT a generic "search the web" wrapper. It:
- Uses the SearxNG JSON API (/search?q=...&format=json)
- Supports category filtering (general, images, news, files, it, science)
- Supports language and time range filtering
- Returns structured results: title, url, snippet, engine, score
- Caches results to minimize redundant queries
- Handles network errors gracefully with retries
- Deduplicates results by URL

Architecture reference: §5.1 — "Self-hosted meta-search, free, unlimited.
Docker-based. Aggregates 70+ search engines. No API key, no rate limit,
no tracking."

Tool selection logic (§5.2):
  Search task:
    1. SearxNG (free, unlimited, fast) — always try first
    2. Jina search (if SearxNG returns poor results)
    3. Obscura (if the data is behind JS rendering)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import random
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any, cast

import httpx

from hyperion.tools.engine_health import get_engine_health
from hyperion.tools.evidence_ledger import record_evidence
from hyperion.tools.jina import JinaClient
from hyperion.tools.query_utils import grounded_search_or_empty
from hyperion.tools.valkey import get_valkey_store

logger = logging.getLogger(__name__)

# W-11: one code registry, built only from API-backed sources and independent
# crawlers. Tier C engines that CAPTCHA, IP-ban, or proxy a blocking upstream
# are forbidden everywhere, not merely omitted from the default route.
# P2-G23: the general pool. openalex is a Tier-A/B documented API (owned by
# the scholar replica) that does not ban datacenter IPs the way crawlers do;
# `engines_for` intersects it out of web-replica requests, so W-11 isolation
# and W-12 disjoint profiles are preserved.
#
# P1.2 (overhaul §6 P1, 2026-08-10): **mojeek and yep are removed from every
# active engine set.** Both categorically 403 from a datacenter/VPS egress
# after a week of daily runs (the Aug-10 autopsy: mojeek 403 x5+, yep 403
# x4+ for the entire 40-minute run). They are also disabled in
# `searxng_settings.yml` so no replica ever receives traffic for them again.
# The web corpus is now served by `mwmbl` + `brave` behind the engine-health
# circuit, with the scholar/reference API engines as the fan-out rescue path.
RELIABLE_ENGINES = "wikipedia,mwmbl,brave,crossref,openalex"
STANDBY_ENGINES = ""
CATEGORY_ENGINES = {
    "science": "arxiv,crossref,openalex,semantic scholar",
    "medical": "pubmed,openalex",
    "it": "github,stackexchange,hackernews",
    "geo": "openstreetmap",
    "news": "mwmbl",
}
# Corpus-safe defaults when a request crosses profiles after an empty/error
# response. These engines accept broad natural-language research queries;
# profile-specific code/search/geo engines do not.
PROFILE_FALLBACK_ENGINES = {
    # P1.2: mojeek/yep removed — they 403 categorically from this egress.
    "web": frozenset({"mwmbl", "brave"}),
    "reference": frozenset({"wikipedia"}),
    "scholar": frozenset({"crossref", "openalex", "semantic scholar"}),
}
TIER_C_ENGINES = frozenset({
    "bing",
    "bing news",
    "duckduckgo",
    "duckduckgo news",
    "ecosia",
    "google",
    "google scholar",
    "qwant",
    "startpage",
    "stackoverflow",
    "swisscows",
})
HEALTHY_ENGINE_FLOOR = 4


def referenced_engines() -> set[str]:
    """All engine identities that code may send to SearXNG."""
    blobs = (RELIABLE_ENGINES, STANDBY_ENGINES, *CATEGORY_ENGINES.values())
    return {
        engine.strip()
        for blob in blobs
        for engine in blob.split(",")
        if engine.strip()
    }


class EngineRegistryMismatch(RuntimeError):  # noqa: N818 - public W-11 contract
    """The running instance does not implement the code's engine contract."""


@dataclass(frozen=True)
class EngineRegistryReport:
    base_url: str
    enabled: frozenset[str]
    missing: frozenset[str]
    forbidden: frozenset[str]

    @property
    def ok(self) -> bool:
        return not self.missing and not self.forbidden


def profile_enabled_engines(profile: str) -> set[str]:
    """P1.2-fix: the ENABLED engines for a SearXNG replica profile.

    Reads the generated per-profile settings file (``searxng_settings.<profile>.yml``)
    and returns only the engines that are NOT ``disabled: true``. This exists
    because the W-12 replica registry deliberately keeps DISABLED engines
    declared (mojeek/yep stay in ``SEARXNG_REPLICAS`` so profile disjointness
    and the P1.2 decision record hold), but a boot reconcile that expects the
    full declared tuple reports a spurious mismatch against the running config,
    which only serves the enabled set. The reconcile must expect exactly the
    enabled engines.
    """
    try:
        import yaml

        from hyperion.infra.paths import project_root

        path = project_root() / f"searxng_settings.{profile}.yml"
        if not path.exists():
            return set()
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        enabled = {
            str(e.get("name", "")).strip()
            for e in data.get("engines", [])
            if str(e.get("name", "")).strip() and not e.get("disabled", False)
        }
        return enabled
    except Exception as exc:  # noqa: BLE001 - a read failure must not break boot
        logger.warning("profile_enabled_engines(%r) failed: %s", profile, exc)
        return set()


async def reconcile_engine_registry(
    base_url: str,
    *,
    client: httpx.AsyncClient | None = None,
    expected_engines: set[str] | frozenset[str] | None = None,
) -> EngineRegistryReport:
    """Fail closed when /config and the code registry disagree.

    This runs after readiness at shell boot. Drift is a boot failure because a
    dead category route wastes W-07's retry budget and silently narrows the
    research corpus.
    """
    owns_client = client is None
    http = client or httpx.AsyncClient(timeout=10.0)
    try:
        response = await http.get(
            f"{base_url.rstrip('/')}/config",
            headers={
                "User-Agent": "HYPERION/1.0 (local readiness probe)",
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
            },
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await http.aclose()
    enabled = frozenset(
        str(item.get("name", "")).strip()
        for item in payload.get("engines", [])
        if item.get("enabled", not item.get("disabled", False))
        and str(item.get("name", "")).strip()
    )
    expected = set(expected_engines) if expected_engines is not None else referenced_engines()
    report = EngineRegistryReport(
        base_url=base_url,
        enabled=enabled,
        missing=frozenset(expected - enabled),
        forbidden=frozenset(enabled & TIER_C_ENGINES),
    )
    if not report.ok:
        raise EngineRegistryMismatch(
            "SearXNG engine registry mismatch at "
            f"{base_url}: missing={sorted(report.missing)}, "
            f"forbidden={sorted(report.forbidden)}. "
            "Edit searxng_settings.yml and restart the container."
        )
    return report


class EngineTokenBucket:
    """Process-wide per-engine outbound limiter with jitter.

    SearXNG's limiter protects its inbound endpoint. This limiter protects the
    upstream APIs and crawlers from aggregate specialist concurrency. W-12 can
    move the same state to Valkey when multiple HYPERION processes are used.
    """

    _lock: asyncio.Lock | None = None
    _next_allowed: dict[str, float] = {}
    interval_seconds = 2.0

    @classmethod
    async def acquire(cls, engines: set[str]) -> None:
        if not engines:
            return

        shared_wait = await get_valkey_store().reserve_engine_window(
            engines,
            interval_ms=max(1, int(cls.interval_seconds * 1000)),
            jitter_ms=random.randint(0, 200),
        )
        if shared_wait is not None:
            if shared_wait:
                await asyncio.sleep(shared_wait)
            return

        # Retrieval remains available when Docker/Valkey is down, but one
        # HYPERION process still honours the same per-engine safety interval.
        if cls._lock is None:
            cls._lock = asyncio.Lock()
        loop = asyncio.get_running_loop()
        async with cls._lock:
            now = loop.time()
            wait = max(0.0, max(cls._next_allowed.get(e, now) for e in engines) - now)
            reservation = now + wait + cls.interval_seconds + random.uniform(0.0, 0.2)
            for engine in engines:
                cls._next_allowed[engine] = reservation
        if wait:
            await asyncio.sleep(wait)


@dataclass
class SearchResult:
    """A single search result from SearxNG."""

    title: str
    url: str
    snippet: str = ""
    engine: str = ""
    score: float = 0.0
    category: str = "general"
    published_date: str = ""
    backend: str = "searxng"

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "engine": self.engine,
            "score": self.score,
            "category": self.category,
            "published_date": self.published_date,
            "backend": self.backend,
        }

    def get(self, key: str, default: Any = "") -> Any:
        """Dict-like access for compatibility with agents that use .get()."""
        mapping = {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.snippet,
            "engine": self.engine,
            "score": self.score,
            "category": self.category,
            "published_date": self.published_date,
            "backend": self.backend,
        }
        return mapping.get(key, default)


@dataclass
class SearchResponse:
    """A complete search response from SearxNG."""

    query: str
    results: list[SearchResult] = field(default_factory=list)
    total: int = 0
    took_ms: int = 0
    engines_used: list[str] = field(default_factory=list)
    cached: bool = False
    retrieval_degraded: bool = False
    degradation_events: list[dict[str, object]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "took_ms": self.took_ms,
            "engines_used": self.engines_used,
            "cached": self.cached,
            "retrieval_degraded": self.retrieval_degraded,
            "degradation_events": self.degradation_events,
        }

    def __iter__(self) -> Iterator[SearchResult]:
        """Iterate over results, yielding SearchResult items."""
        return iter(self.results)

    def __len__(self) -> int:
        return len(self.results)

    def __getitem__(self, key: int | slice) -> SearchResult | list[SearchResult]:
        """Support indexing and slicing: response[0], response[:5]."""
        return self.results[key]

    def __bool__(self) -> bool:
        return bool(self.results)


@dataclass
class SearxngEndpoint:
    base_url: str
    profile: str
    port: int
    engines: frozenset[str] = field(default_factory=frozenset)
    outstanding: int = 0
    consecutive_failures: int = 0
    circuit_open: bool = False
    retry_after: float = 0.0


class SearxngPool:
    """Profile-aware endpoint pool with circuit breakers and fallback."""

    CATEGORY_PROFILE = {
        "science": "scholar", "medical": "scholar",
        "it": "reference", "geo": "reference", "reference": "reference",
        "general": "web", "news": "web",
    }
    FALLBACKS = {
        "web": ("reference", "scholar"),
        "reference": ("scholar", "web"),
        "scholar": ("reference", "web"),
    }

    def __init__(self, endpoints: list[SearxngEndpoint]) -> None:
        self.endpoints = endpoints

    @classmethod
    def from_config(cls) -> SearxngPool:
        from hyperion.infra.services import SEARXNG_REPLICAS

        return cls([
            SearxngEndpoint(
                f"http://127.0.0.1:{item.port}",
                item.profile,
                item.port,
                frozenset(item.engines),
            )
            for item in SEARXNG_REPLICAS
        ])

    def preferred_profile(self, category: str) -> str:
        """Return the primary profile for a SearXNG category."""
        return self.CATEGORY_PROFILE.get(category.lower(), "web")

    def endpoint_for(
        self,
        *,
        category: str = "general",
        requested_engines: set[str] | None = None,
        excluded_ports: set[int] | None = None,
    ) -> SearxngEndpoint:
        preferred = self.preferred_profile(category)
        profiles: tuple[str, ...] = (preferred, *self.FALLBACKS[preferred])
        if requested_engines:
            owners = tuple(
                profile
                for profile in profiles
                if any(
                    requested_engines <= endpoint.engines
                    for endpoint in self.endpoints
                    if endpoint.profile == profile
                )
            )
            if not owners:
                raise EngineRegistryMismatch(
                    "Explicit engine request crosses isolated SearXNG profiles: "
                    f"{sorted(requested_engines)}"
                )
            # Explicit requests are a caller contract, not a hint. Never silently
            # replace them with a different profile's corpus during failover.
            profiles = owners

        excluded = excluded_ports or set()
        now = time.monotonic()
        for endpoint in self.endpoints:
            if endpoint.circuit_open and endpoint.retry_after <= now:
                endpoint.circuit_open = False
                endpoint.consecutive_failures = 0
        for profile in profiles:
            candidates = [
                endpoint for endpoint in self.endpoints
                if endpoint.profile == profile
                and endpoint.port not in excluded
                and not endpoint.circuit_open
            ]
            if candidates:
                return min(candidates, key=lambda endpoint: endpoint.outstanding)
        raise RuntimeError("No healthy SearXNG endpoint is available")

    def engines_for(
        self,
        endpoint: SearxngEndpoint,
        *,
        category: str,
        requested_engines: set[str],
        explicit: bool,
    ) -> set[str]:
        """Return only engines that the selected isolated replica actually serves."""
        if explicit and requested_engines <= endpoint.engines:
            return set(requested_engines)

        category_engines = {
            engine.strip()
            for engine in CATEGORY_ENGINES.get(category.lower(), "").split(",")
            if engine.strip()
        }
        compatible = category_engines & endpoint.engines
        if compatible:
            return compatible
        requested_compatible = requested_engines & endpoint.engines
        if requested_compatible:
            # An implicit default engine registry is still a corpus policy.
            # Respect its per-profile intersection so a general business query
            # falling back to the reference replica does not hit StackExchange,
            # GitHub and OpenStreetMap with an invalid paragraph-sized query.
            return requested_compatible
        # Custom implicit engine sets may contain only engines from the failed
        # profile. Never respond by enabling every engine on the fallback
        # replica; choose only broad-query-safe corpus engines.
        return set(PROFILE_FALLBACK_ENGINES.get(endpoint.profile, frozenset())) & set(
            endpoint.engines
        )

    def mark_unhealthy(self, port: int) -> None:
        endpoint = next(item for item in self.endpoints if item.port == port)
        endpoint.consecutive_failures += 1
        endpoint.circuit_open = True
        endpoint.retry_after = time.monotonic() + min(
            300.0,
            30.0 * (2 ** max(0, endpoint.consecutive_failures - 1)),
        )

    def mark_success(self, port: int) -> None:
        endpoint = next(item for item in self.endpoints if item.port == port)
        endpoint.consecutive_failures = 0
        endpoint.circuit_open = False
        endpoint.retry_after = 0.0

    def healthy_engines(self) -> dict[str, set[str]]:
        """Per-profile sets of engines that are currently healthy (F-03).

        Respects engine-health cooldowns (``vault/engine_health.json``), so
        cooled/suspended engines are never included and never receive traffic.
        A profile whose engines are all cooling is absent from the result —
        the full-pool fan-out must not resurrect a dead profile.

        Returns ``{profile: {engine, ...}}`` over the whole fleet, so the
        scholar/reference API engines participate in general queries instead
        of sitting idle while the web replica's crawlers are banned.
        """
        from hyperion.tools.engine_health import get_engine_health

        health = get_engine_health()
        result: dict[str, set[str]] = {}
        for endpoint in self.endpoints:
            healthy = set(health.filter_available(sorted(endpoint.engines)))
            if healthy:
                result[endpoint.profile] = healthy
        return result


class SearxNGClient:
    """SearxNG meta-search client.

    Self-hosted meta-search that aggregates 70+ search engines.
    No API key, no rate limit, no tracking. Docker-based.
    (§5.1)

    Usage:
        client = SearxNGClient(settings=settings)
        response = await client.search("Indian SaaS market size 2024", num_results=10)
        for result in response.results:
            print(f"{result.title} — {result.url}")
    """

    CACHE_DIR = "output/.searxng_cache"
    CACHE_TTL_SECONDS = 3600  # 1 hour
    REQUEST_TIMEOUT = 45  # seconds — must match SearxNG max_request_timeout
    MAX_RETRIES = 3
    RETRY_DELAY = 2  # seconds
    MAX_CONCURRENT = 10  # allow more parallel searches across 12 specialists

    # Search budget cap — 600 discovery searches per engagement (F-05).
    # The old 200 was a guess from an era with 1 query per sub-agent; the
    # query planner now emits up to 7 per leg across 12 specialists × 3
    # sub-agents, so 200 was exhausted long before M&A / STRATEGY / SYNTHESIS
    # ran, silently zeroing every later search.
    SEARCH_BUDGET_CAP = 600
    # Per-owner cap (F-05c): one heavy specialist must not be able to starve
    # the rest of the engagement. The owner label is set at client
    # construction from the spawning agent's name.
    PER_OWNER_BUDGET_CAP = 200

    # Class-level semaphore shared across all instances
    _semaphore: asyncio.Semaphore | None = None
    _search_count: int = 0
    _budget_exceeded: bool = False
    _owner_counts: dict[str, int] = {}
    _owners_exhausted: set[str] = set()

    def __init__(self, settings: Any | None = None, *, owner: str = "") -> None:
        self.settings = settings
        # F-05c: per-owner budget accounting. Set from the spawning agent's
        # name (base.py / sub_agent.py) so one heavy specialist cannot
        # silently consume the whole engagement budget.
        self._owner = owner
        # The default is derived from the port the launcher actually publishes,
        # not a literal. Two hardcoded copies of "8888" (here and in the
        # container spec) meant a port change moved the container without moving
        # the client, and every search then failed with a connection error that
        # surfaced only as "search returned no results".
        self._pool = SearxngPool.from_config()
        default_url = self._pool.endpoint_for(category="science").base_url
        self._base_url = default_url
        if settings:
            self._base_url = getattr(settings, "searxng_url", "") or default_url
        self._base_url = self._base_url.rstrip("/")
        self._clients: dict[str, httpx.AsyncClient] = {}
        self._cache: dict[str, tuple[float, SearchResponse]] = {}
        os.makedirs(self.CACHE_DIR, exist_ok=True)
        if SearxNGClient._semaphore is None:
            SearxNGClient._semaphore = asyncio.Semaphore(self.MAX_CONCURRENT)

    @property
    def base_url(self) -> str:
        """Public accessor for the SearxNG base URL."""
        return self._base_url

    async def _get_client(self, base_url: str | None = None) -> httpx.AsyncClient:
        url = (base_url or self._base_url).rstrip("/")
        client = self._clients.get(url)
        if client is None or client.is_closed:
            client = httpx.AsyncClient(
                base_url=url,
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                headers={
                    "Accept": "application/json",
                    # Identify as a normal browser client. SearxNG's bot
                    # detection inspects User-Agent and will reject or throttle
                    # a bare httpx UA even when the limiter is disabled.
                    "User-Agent": (
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/124.0.0.0 Safari/537.36"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                    # searxng-limiter.toml lists 172.17.0.0/16 (the docker
                    # bridge) in trusted_proxies, but requests originating on
                    # the host arrive with no X-Forwarded-For at all, so the
                    # limiter cannot resolve a trusted client IP and buckets
                    # every query into the same aggressive rate limit — the
                    # 429s visible in the docker logs. Presenting a loopback
                    # forwarded-for marks the request as local and trusted.
                    "X-Forwarded-For": "127.0.0.1",
                    "X-Real-IP": "127.0.0.1",
                },
            )
            self._clients[url] = client
        return client

    def _cache_key(self, query: str, **kwargs: Any) -> str:
        """Generate a stable key from a normalized query and engine set."""
        normalized = " ".join(query.casefold().split())
        values = dict(kwargs)
        raw_engines = str(values.get("engines", ""))
        values["engines"] = sorted(
            engine.strip().casefold()
            for engine in raw_engines.split(",")
            if engine.strip()
        )
        payload = json.dumps(
            {"query": normalized, "parameters": values},
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        )
        digest = hashlib.sha256(payload.encode()).hexdigest()
        return f"hyperion:retrieval:cache:{digest}"

    @staticmethod
    def _response_from_dict(payload: dict[str, Any]) -> SearchResponse:
        return SearchResponse(
            query=str(payload.get("query", "")),
            results=[
                SearchResult(
                    title=str(item.get("title", "")),
                    url=str(item.get("url", "")),
                    snippet=str(item.get("snippet", "")),
                    engine=str(item.get("engine", "")),
                    score=float(item.get("score", 0.0)),
                    category=str(item.get("category", "general")),
                    published_date=str(item.get("published_date", "")),
                    backend=str(item.get("backend", "searxng")),
                )
                for item in payload.get("results", [])
                if isinstance(item, dict)
            ],
            total=int(payload.get("total", 0)),
            took_ms=int(payload.get("took_ms", 0)),
            engines_used=[str(item) for item in payload.get("engines_used", [])],
            cached=True,
            retrieval_degraded=bool(payload.get("retrieval_degraded", False)),
            degradation_events=list(payload.get("degradation_events", [])),
        )

    async def _get_cached(self, key: str) -> SearchResponse | None:
        """Read through the shared cache, falling back to local memory."""
        payload = await get_valkey_store().get_json(key)
        if payload is not None:
            return self._response_from_dict(payload)

        if key in self._cache:
            timestamp, response = self._cache[key]
            if time.time() - timestamp < self.CACHE_TTL_SECONDS:
                response.cached = True
                return response
            del self._cache[key]
        return None

    async def _set_cached(self, key: str, response: SearchResponse) -> None:
        """Write a normalized response to both local and shared caches."""
        self._cache[key] = (time.time(), response)
        await get_valkey_store().set_json(
            key,
            response.to_dict(),
            self.CACHE_TTL_SECONDS,
        )

    def _deduplicate(self, results: list[SearchResult]) -> list[SearchResult]:
        """Deduplicate results by URL, keeping the highest-scored version."""
        seen: dict[str, SearchResult] = {}
        for result in results:
            if result.url in seen:
                if result.score > seen[result.url].score:
                    seen[result.url] = result
            else:
                seen[result.url] = result
        return list(seen.values())

    @classmethod
    def reset_budget(cls) -> None:
        """Reset the search budget counter — called at the start of each engagement."""
        cls._search_count = 0
        cls._budget_exceeded = False
        cls._owner_counts = {}
        cls._owners_exhausted = set()

    @classmethod
    def get_search_count(cls) -> int:
        """Return the current search count for this engagement."""
        return cls._search_count

    @classmethod
    def get_owner_count(cls, owner: str) -> int:
        """Return the search count attributed to one owner (F-05c)."""
        return cls._owner_counts.get(owner, 0)

    @classmethod
    def budget_snapshot(cls) -> dict[str, object]:
        """Machine-readable budget status for the completion health table."""
        return {
            "used": cls._search_count,
            "cap": cls.SEARCH_BUDGET_CAP,
            "exhausted": cls._budget_exceeded,
            "owners": dict(cls._owner_counts),
            "owners_exhausted": sorted(cls._owners_exhausted),
        }

    @staticmethod
    def _sanitize_scholar_query(query: str, max_len: int = 120) -> str:
        """OVERHAUL3 D-H (overhaul3_audit.md W3/S7): scholar APIs reject
        punctuation-heavy ``search=`` expressions.

        openalex 400'd on a 145-char comma/``?`` sentence (docker 12:02:35)
        that sat UNDER the old 200-char clamp — the rejection was
        punctuation, not length. Collapse ``,`` ``?`` ``.`` to spaces, then
        clamp to ``max_len`` at a word boundary.
        """
        q = (query or "").strip()
        q = q.replace(",", " ").replace("?", " ").replace(".", " ")
        q = " ".join(q.split())
        if len(q) > max_len:
            q = q[:max_len].rsplit(" ", 1)[0]
        return q.strip() or (query or "").strip()[:max_len]

    @staticmethod
    def _shape_query_for_profile(query: str, profile: str) -> str:
        """OVERHAUL3 D-G/D-H: shape a query for the profile actually serving it.

        - ``reference`` (wikipedia): ``/page/summary/{title}`` treats the
          whole query as an article title and 400s on full-sentence specialist
          queries (docker 12:31:05). Route through the same
          ``SubAgentRunner._condense_query`` the sub-agents already use, so
          reference searches arrive title-shaped (≤120 chars).
        - ``scholar`` (openalex/crossref/arxiv/pubmed): APIs reject
          punctuation-heavy paragraph queries; sanitize via
          ``_sanitize_scholar_query``.
        - everything else: the legacy S10 200-char clamp stays as the final
          net so no profile ever dispatches a runaway paragraph.
        """
        if profile == "reference":
            try:
                from hyperion.agents.sub_agent import SubAgentRunner

                return SubAgentRunner._condense_query(query, max_len=120)
            except Exception as exc:  # noqa: BLE001 - degrade to the generic clamp
                logger.debug("reference query condensation unavailable (%s)", exc)
        elif profile == "scholar":
            return SearxNGClient._sanitize_scholar_query(query)
        if len(query) > 200:
            return query[:200].rsplit(" ", 1)[0]
        return query

    async def _search_searxng_json(
        self,
        query: str,
        num_results: int,
        categories: str,
        language: str,
        time_range: str,
        engines: str,
        safesearch: int,
        *,
        explicit_engines: bool = False,
    ) -> SearchResponse | None:
        """Query one profile and fail over without sending it foreign engines."""
        category = categories or "general"
        # OVERHAUL3 D-G/D-H (overhaul3_audit.md W3/S6-7): query shaping is now
        # profile-aware and happens INSIDE the retry loop
        # (``_shape_query_for_profile``), because the endpoint profile is only
        # known after ``endpoint_for`` resolves. The old S10 blanket 200-char
        # clamp ran before the endpoint was known, so punctuation-heavy
        # paragraphs still reached wikipedia (400 on ``/page/summary/{title}``)
        # and openalex (400 on ``search=``).
        requested_engines = {
            engine.strip() for engine in engines.split(",") if engine.strip()
        }
        forbidden = requested_engines & TIER_C_ENGINES
        if forbidden:
            raise EngineRegistryMismatch(
                f"Tier C engines are forbidden by W-11 policy: {sorted(forbidden)}"
            )

        endpoint: SearxngEndpoint | None = None
        attempted_ports: set[int] = set()
        for attempt in range(self.MAX_RETRIES):
            try:
                endpoint = self._pool.endpoint_for(
                    category=category,
                    requested_engines=requested_engines if explicit_engines else None,
                    excluded_ports=attempted_ports,
                )
                attempted_ports.add(endpoint.port)
                selected_engines = self._pool.engines_for(
                    endpoint,
                    category=category,
                    requested_engines=requested_engines,
                    explicit=explicit_engines,
                )
                health = get_engine_health()
                selected_engines = set(
                    health.filter_available(sorted(selected_engines))
                )
                if not selected_engines:
                    if explicit_engines:
                        return None
                    self._pool.mark_unhealthy(endpoint.port)
                    logger.warning(
                        "SearXNG profile %s has no healthy engines; failing over",
                        endpoint.profile,
                    )
                    continue
                dispatch_query = self._shape_query_for_profile(query, endpoint.profile)
                params: dict[str, Any] = {
                    "q": dispatch_query,
                    "format": "json",
                    "categories": (
                        category
                        if endpoint.profile == self._pool.preferred_profile(category)
                        else "general"
                    ),
                    "language": language,
                    "safesearch": safesearch,
                    "engines": ",".join(sorted(selected_engines)),
                }
                if time_range:
                    params["time_range"] = time_range

                endpoint.outstanding += 1
                client = await self._get_client(endpoint.base_url)
                try:
                    await EngineTokenBucket.acquire(selected_engines)
                    response = await client.get("/search", params=params)
                    response.raise_for_status()
                finally:
                    endpoint.outstanding = max(0, endpoint.outstanding - 1)
                self._pool.mark_success(endpoint.port)

                # OVERHAUL3 D-I (overhaul3_audit.md W3/S8): a non-JSON body
                # (proxy error page, throttling HTML, an upstream 502 page) is
                # an ENGINE-LEVEL health signal, not a generic client error.
                # The old path only opened the endpoint circuit — engine_health
                # was never told, so the next request re-asked the same dead
                # engines (the 2026-07-30 semantic scholar repeat-queries).
                # Feed the failure to ``record_response`` BEFORE the retry so
                # ``filter_available`` drops the cooled engines and the
                # profile fails over to a healthy replica.
                try:
                    data = response.json()
                except json.JSONDecodeError as exc:
                    logger.warning(
                        "SearXNG non-JSON body for '%s' on %s profile: %s — "
                        "cooling engines %s",
                        query[:80],
                        endpoint.profile,
                        exc,
                        ", ".join(sorted(endpoint.engines)),
                    )
                    health.record_response(
                        unresponsive_engines=[
                            [engine, "non-json response body"]
                            for engine in sorted(endpoint.engines)
                        ],
                        responding_engines=[],
                    )
                    raise
                raw_results = data.get("results", [])
                results: list[SearchResult] = []
                engines_used_set: set[str] = set()
                for item in raw_results:
                    url = item.get("url", "")
                    if not url:
                        continue
                    engine_name = item.get("engine", "unknown")
                    engines_used_set.add(engine_name)
                    results.append(SearchResult(
                        title=item.get("title", ""),
                        url=url,
                        snippet=item.get("content", ""),
                        engine=engine_name,
                        score=float(item.get("score", 1.0)),
                        category=item.get("category", category),
                        published_date=item.get("publishedDate", ""),
                    ))
                    # P0 (overhaul §6 P0.2): every retrieved URL becomes an
                    # Evidence record in the run-scoped ledger BEFORE any LLM
                    # sees it. Provenance starts here, in code.
                    record_evidence(
                        url=url,
                        title=item.get("title", ""),
                        snippet=item.get("content", ""),
                        engine=engine_name,
                        profile=endpoint.profile,
                        stage="discovery",
                    )

                unresponsive = data.get("unresponsive_engines", [])
                degradation_events: list[dict[str, object]] = []
                if unresponsive or engines_used_set:
                    health.record_response(
                        unresponsive_engines=unresponsive,
                        responding_engines=engines_used_set,
                    )
                degradation = health.record_degradation_if_needed(
                    referenced_engines(), floor=HEALTHY_ENGINE_FLOOR
                )
                if degradation is not None:
                    degradation_events.append(degradation)

                if results:
                    results = self._deduplicate(results)[:num_results]
                    return SearchResponse(
                        query=query,
                        results=results,
                        total=len(results),
                        took_ms=int(data.get("number_of_results", 0)),
                        engines_used=sorted(engines_used_set),
                        retrieval_degraded=bool(degradation_events),
                        degradation_events=degradation_events,
                    )

                if unresponsive:
                    logger.warning(
                        "SearXNG unresponsive engines for '%s': %s",
                        query[:80], unresponsive,
                    )
                if unresponsive and not health.filter_available(
                    sorted(endpoint.engines)
                ):
                    # All engines in this isolated profile are cooling or
                    # suspended. Open the endpoint circuit and use the next
                    # profile instead of repeatedly hammering dead upstreams.
                    self._pool.mark_unhealthy(endpoint.port)
                if not explicit_engines and attempt < self.MAX_RETRIES - 1:
                    # A zero-result profile is not proof that the corpus is
                    # empty. Walk each isolated profile once in this request;
                    # the previous implementation retried the same preferred
                    # profile and never reached reference/scholar fallbacks
                    # unless every engine had already opened a global circuit.
                    continue
                logger.debug(
                    "SearXNG returned 0 results for '%s' (attempt %d)",
                    query,
                    attempt + 1,
                )
                break

            except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError) as exc:
                if endpoint is not None:
                    self._pool.mark_unhealthy(endpoint.port)
                logger.warning("SearXNG JSON API error (attempt %d): %s", attempt + 1, exc)
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))
                    continue
                return None

        return None

    async def _search_with_rotation(
        self,
        query: str,
        num_results: int,
        categories: str,
        language: str,
        time_range: str,
        engines: str,
        safesearch: int,
        *,
        explicit_engines: bool = False,
    ) -> SearchResponse | None:
        """Search, and on a zero-result response rotate engines once.

        P2-26 fix 3 (P2-G24): the old code broke out of the loop on zero
        results ("engines are likely blocked"). Correct reasoning, wrong
        action: if engines are blocked, use DIFFERENT engines. The first
        attempt uses the given pool; a zero-result response drops the
        cooled/unresponsive engines, promotes the standby pool, and retries
        exactly once. Only then does it fall through (None) so the caller
        can try the Jina fallback.
        """
        health = get_engine_health()
        response = await self._search_searxng_json(
            query=query,
            num_results=num_results,
            categories=categories,
            language=language,
            time_range=time_range,
            engines=engines,
            safesearch=safesearch,
            explicit_engines=explicit_engines,
        )
        if response is not None and response.results:
            return response

        # Rotation: keep only healthy engines from the original pool, then
        # add standby engines not already in it.
        primary = [e.strip() for e in engines.split(",") if e.strip()]
        healthy = [e for e in primary if health.is_available(e)]
        standby = [
            e.strip()
            for e in self.STANDBY_ENGINES.split(",")
            if e.strip() and e.strip() not in primary and health.is_available(e.strip())
        ]
        rotated = healthy + standby
        if not rotated or not standby:
            return None
        logger.info(
            "ENGINE ROTATION: zero results on %s; retrying once with %s",
            engines, ",".join(rotated),
        )
        retry = await self._search_searxng_json(
            query=query,
            num_results=num_results,
            categories=categories,
            language=language,
            time_range=time_range,
            engines=",".join(rotated),
            safesearch=safesearch,
            explicit_engines=explicit_engines,
        )
        if retry is not None and retry.results:
            return retry
        return None

    async def _search_all_replicas(
        self,
        query: str,
        num_results: int,
        language: str,
        time_range: str,
        safesearch: int,
    ) -> SearchResponse | None:
        """FULL-POOL FAN-OUT (F-03): one parallel request per replica with
        that replica's FULL set of currently-healthy engines, merge + dedup.

        WHY THIS EXISTS. The Aug 9 session's web replica had 4/4 engines
        403/429/timed-out while the scholar replica's documented API engines
        (crossref/openalex/semantic scholar) were never asked for the general
        query — they do not ban datacenter IPs the way crawlers do. The
        sequential zero-result fallback only reached them with a NARROW
        fallback set (``{wikipedia}`` / ``{crossref, openalex, semantic
        scholar}``) AFTER the preferred profile had already failed.

        This method flips the ordering: when the preferred profile + standby
        rotation yield zero, fire ONE parallel request per replica using that
        replica's FULL healthy engine set and merge the results. W-11
        isolation is preserved because explicit engines are bound to the
        replica that owns them — each request only ever contains engines
        from a single replica's corpus.

        Per-replica failures are logged, never fatal: a dead scholar replica
        must not abort the reference replica's contribution.
        """
        per_profile = self._pool.healthy_engines()
        if not per_profile:
            return None
        # P1.2: never name an engine the code does not reference. The web
        # replica still DECLARES (disabled) mojeek/yep so W-12 disjointness
        # holds, but the fan-out must not carry their names either — SearXNG
        # ignores disabled engines in a request, and keeping them out of the
        # engine list makes the no-traffic guarantee explicit.
        per_profile = {
            profile: engines & referenced_engines()
            for profile, engines in per_profile.items()
        }
        per_profile = {p: e for p, e in per_profile.items() if e}

        async def _fan_one(profile: str, engines: set[str]) -> SearchResponse | None:
            try:
                return await self._search_searxng_json(
                    query=query,
                    num_results=num_results,
                    categories="general",
                    language=language,
                    time_range=time_range,
                    engines=",".join(sorted(engines)),
                    safesearch=safesearch,
                    explicit_engines=True,
                )
            except Exception as exc:  # noqa: BLE001 - one replica must not kill the fleet
                logger.warning(
                    "FULL-POOL FAN-OUT: %s replica failed for '%s': %s",
                    profile, query[:80], exc,
                )
                return None

        responses = await asyncio.gather(
            *(_fan_one(profile, engines) for profile, engines in per_profile.items()),
            return_exceptions=True,
        )

        merged: list[SearchResult] = []
        engines_used: set[str] = set()
        degradation_events: list[dict[str, object]] = []
        for response in responses:
            if isinstance(response, Exception) or response is None:
                continue
            merged.extend(response.results)
            engines_used.update(response.engines_used)
            degradation_events.extend(response.degradation_events)

        if not merged:
            return None
        merged = self._deduplicate(merged)[:num_results]
        return SearchResponse(
            query=query,
            results=merged,
            total=len(merged),
            engines_used=sorted(engines_used),
            retrieval_degraded=bool(degradation_events),
            degradation_events=degradation_events,
        )

    async def _search_jina_fallback(
        self,
        query: str,
        num_results: int,
        categories: str,
    ) -> SearchResponse | None:
        """Fallback: search via Jina (s.jina.ai).

        Jina is keyless and reliable but returns fewer results.
        Only used when SearXNG is unavailable or returns nothing.
        """
        if not self.settings:
            return None

        try:
            jina = JinaClient(settings=self.settings)
            jina_resp = await jina.search(query=query, num_results=num_results)
            await jina.close()

            if jina_resp.results:
                results: list[SearchResult] = []
                for jr in jina_resp.results:
                    results.append(SearchResult(
                        title=jr.title,
                        url=jr.url,
                        snippet=jr.snippet,
                        engine="jina",
                        score=1.0,
                        category=categories,
                    ))
                    record_evidence(
                        url=jr.url,
                        title=jr.title,
                        snippet=jr.snippet,
                        engine="jina",
                        profile="jina_fallback",
                        stage="discovery",
                    )

                if results:
                    results = self._deduplicate(results)[:num_results]
                    return SearchResponse(
                        query=query,
                        results=results,
                        total=len(results),
                        took_ms=jina_resp.took_ms,
                        engines_used=["jina"],
                    )
        except (httpx.HTTPError, httpx.RequestError, RuntimeError, OSError) as e:
            logger.warning("Jina fallback search failed: %s", e)

        return None

    async def _search_grounded_fallback(
        self,
        query: str,
        num_results: int,
        categories: str,
    ) -> SearchResponse | None:
        """Final discovery fallback through metered Gemini Search grounding.

        This is deliberately *not* part of the routine fan-out. It runs only
        after the profile-aware SearXNG pool and Jina have both produced no
        usable result, matching W-14's ``RETRY_STRATEGY exhausted`` policy.
        Provider failures, safety refusals, disabled grounding and exhausted
        quota all fail open and are retained as degradation events.
        """
        try:
            from hyperion.tools.grounded_search import (
                GroundedSearchClient,
                GroundingReason,
            )

            outcome = await GroundedSearchClient(settings=self.settings).search(
                query,
                reason=GroundingReason.RETRY_EXHAUSTED,
            )
        except Exception as exc:  # noqa: BLE001 - final fallback must never abort search
            logger.warning("Gemini grounded fallback failed open: %s", exc)
            # F-09: same visibility rule as the constraint path above.
            try:
                from hyperion.obs import trace

                trace(
                    "search",
                    event="grounding_unavailable",
                    tool="google_search_grounding",
                    query=query[:120],
                    reason=f"{type(exc).__name__}: {exc}",
                    category=categories,
                )
            except Exception as exc:  # noqa: BLE001 - telemetry must not break search
                logger.debug("search trace emission failed: %s", exc)
            return None

        if not outcome.results:
            if outcome.constraints:
                reason = "; ".join(outcome.constraints)
                logger.warning(
                    "Gemini grounded fallback unavailable for '%s': %s",
                    query[:80],
                    reason,
                )
                # F-09: a grounding failure must be VISIBLE, not just a log
                # line. Silent empty results are how this bug hid (no
                # GOOGLE_API_KEY -> fallback is a no-op -> search returns
                # empty with no trace).
                try:
                    from hyperion.obs import trace

                    trace(
                        "search",
                        event="grounding_unavailable",
                        tool="google_search_grounding",
                        query=query[:120],
                        reason=reason,
                        category=categories,
                    )
                except Exception as exc:  # noqa: BLE001 - telemetry must not break search
                    logger.debug("search trace emission failed: %s", exc)
            return None

        results = self._deduplicate(outcome.results)[:num_results]
        for r in results:
            record_evidence(
                url=r.url,
                title=r.title,
                snippet=r.snippet,
                engine="google-search-grounding",
                profile="grounding",
                stage="grounding",
            )
        return SearchResponse(
            query=query,
            results=results,
            total=len(results),
            engines_used=["google-search-grounding"],
            retrieval_degraded=True,
            degradation_events=[{
                "type": "grounded_search_escalation",
                "reason": outcome.reason.value,
                "billable_queries": outcome.actual_units,
                "constraints": list(outcome.constraints),
                "category": categories,
            }],
        )

    # General-web engines used for business/strategy research.
    #
    # WHY THIS LIST CHANGED. It was
    #     "bing,wikipedia,arxiv,github,hackernews"
    # and the docker logs showed arxiv/github/wikipedia producing the timeouts,
    # 403s and 429s that starved every search. Two distinct problems:
    #
    #  1. WRONG CORPUS. arxiv (physics/CS preprints), github (source code) and
    #     hackernews (tech forum) cannot answer "should India reduce its
    #     dependence on imports". They contribute ~nothing on a business query
    #     while consuming the per-request timeout budget, so a single slow
    #     engine delays the whole aggregated response.
    #  2. RATE LIMITING. wikipedia/arxiv aggressively 429 a datacenter IP
    #     issuing dozens of queries per engagement.
    #
    # P2-26 (P2-G23): the pool is widened to six general-web engines. The
    # 07-30 engagement collapsed to 3 sources because DuckDuckGo ate a 24h
    # CAPTCHA ban and Bing alone could not carry the corpus from a
    # datacenter IP. Six engines plus a disjoint standby pool means a single
    # engine ban no longer starves an engagement. Specialist corpora remain
    # reachable on demand via the `engines=` argument and the dedicated
    # science/code tools (Semantic Scholar, OpenAlex), which are the right
    # instruments for that job.
    RELIABLE_ENGINES = RELIABLE_ENGINES

    # Standby pool, disjoint from RELIABLE_ENGINES, promoted by
    # _search_with_rotation when the primary pool returns zero results
    # (P2-26 fix 3 / P2-G24). Wikipedia is definitional grounding only; it
    # is NOT in the general pool because it 429s a datacenter IP and its
    # corpus cannot answer a business question.
    STANDBY_ENGINES = STANDBY_ENGINES

    # Engines appropriate to non-general categories, so a caller asking for
    # `categories="science"` still reaches the right corpus.
    CATEGORY_ENGINES = CATEGORY_ENGINES

    async def search(
        self,
        query: str,
        num_results: int = 10,
        categories: str = "general",
        language: str = "en",
        time_range: str = "",
        engines: str = "",
        safesearch: int = 0,
        max_results: int | None = None,
        drop_geography: bool = False,
    ) -> SearchResponse:
        """Search via SearXNG JSON API — the primary discovery engine.

        SearXNG aggregates 70+ search engines in a single request.
        No API key, no rate limit, no browser, no CAPTCHA.
        If SearXNG is unavailable or returns no results, falls back to Jina.

        Search budget cap: 60 discovery searches per engagement (§5.2).
        Cached results do not count against the budget.

        Args:
            query: Search query string
            num_results: Maximum number of results to return
            categories: Search category (general, images, news, it, science)
            language: Language code (en, fr, de, etc.)
            time_range: Time filter (day, week, month, year, or empty)
            engines: Comma-separated list of specific engines to use
            safesearch: Safe search level (0=off, 1=moderate, 2=strict)
            drop_geography: fix 1.5 — skip the geography anchor entirely
                (neither the explicit engagement geography nor any inferred
                one is appended). Used by a caller's own low-yield
                reformulation retry to broaden a query that returned too few
                results anchored to a narrow jurisdiction.

        Returns:
            SearchResponse with deduplicated, scored results.
        """
        if max_results is not None:
            num_results = max_results

        # ── Query grounding (single choke point for all 45 specialist call
        # sites) ──
        # Specialists build queries from hardcoded f-string templates such as
        # f"{sector} carbon footprint emissions data". When the interpolated
        # variable is empty, the outbound query becomes subject-less and cannot
        # possibly answer the user's question. Rather than trust 11 specialist
        # modules to each get this right, every query is grounded HERE, against
        # the engagement's actual question/subject/geography.
        #
        # A query that still has no subject after grounding is dropped rather
        # than sent — a useless search costs 20s of timeout and pollutes the
        # findings with irrelevant sources.
        #
        # Fix 1.2 (HYPERION_DEEP_AUDIT_2026-07-27.md item 1.2): this now goes
        # through the shared `grounded_search_or_empty` choke point in
        # query_utils.py instead of re-implementing ground/log/drop inline,
        # so every search client applies the identical rule.
        original_query = query
        grounded, empty = grounded_search_or_empty(
            query,
            lambda: SearchResponse(query=original_query, results=[], total=0, engines_used=[]),
            logger=logger,
            tool_name="SearxNG",
            drop_geography=drop_geography,
        )
        if empty is not None:
            return cast("SearchResponse", empty)
        query = grounded

        # Pick the engine set. An explicit `engines=` argument always wins.
        # Otherwise route by category so a science/it/news query reaches the
        # right corpus instead of being forced through general-web engines
        # (or, as before, sending business questions to arxiv and github).
        if engines:
            effective_engines = engines
        else:
            effective_engines = self.CATEGORY_ENGINES.get(
                (categories or "general").lower(), self.RELIABLE_ENGINES
            )

        # P2-26: exclude engines under an active cooldown so a banned engine
        # (e.g. DuckDuckGo under a 24h 403) stops receiving traffic and the
        # standby pool carries the load instead.
        cooled_out = [
            e.strip()
            for e in effective_engines.split(",")
            if e.strip() and not get_engine_health().is_available(e.strip())
        ]
        if cooled_out:
            kept = [
                e.strip()
                for e in effective_engines.split(",")
                if e.strip() and get_engine_health().is_available(e.strip())
            ]
            if kept:
                logger.info("ENGINE HEALTH: skipping cooled engines %s", cooled_out)
                effective_engines = ",".join(kept)

        cache_key = self._cache_key(query, num_results=num_results, categories=categories,
                                     language=language, time_range=time_range, engines=effective_engines)
        cached = await self._get_cached(cache_key)
        if cached:
            return cached

        # Enforce search budget caps (cached results don't count). F-05:
        # per-owner first (one heavy specialist must not starve the rest),
        # then the global cap. Exhaustion is LOUD: a trace event + error
        # log, not a single quiet warning — silent empties are exactly how
        # the Aug 9 session hid the plateau.
        owner = self._owner or "ungrouped"
        owner_used = SearxNGClient._owner_counts.get(owner, 0)
        if owner_used >= SearxNGClient.PER_OWNER_BUDGET_CAP:
            if owner not in SearxNGClient._owners_exhausted:
                SearxNGClient._owners_exhausted.add(owner)
                logger.error(
                    "SEARCH BUDGET EXHAUSTED for owner %s (%d/%d) — returning empty",
                    owner, owner_used, SearxNGClient.PER_OWNER_BUDGET_CAP,
                )
                try:
                    from hyperion.obs import trace

                    trace(
                        "search",
                        event="search_budget_owner_exhausted",
                        owner=owner,
                        used=owner_used,
                        cap=SearxNGClient.PER_OWNER_BUDGET_CAP,
                    )
                except Exception as exc:  # noqa: BLE001 - telemetry must not break search
                    logger.debug("search trace emission failed: %s", exc)
            return SearchResponse(query=query, results=[], total=0, engines_used=[])

        if SearxNGClient._search_count >= SearxNGClient.SEARCH_BUDGET_CAP:
            if not SearxNGClient._budget_exceeded:
                SearxNGClient._budget_exceeded = True
                logger.error(
                    "SEARCH BUDGET EXHAUSTED (%d/%d) — every later search returns empty",
                    SearxNGClient._search_count,
                    SearxNGClient.SEARCH_BUDGET_CAP,
                )
                try:
                    from hyperion.obs import trace

                    trace(
                        "search",
                        event="search_budget_exhausted",
                        used=SearxNGClient._search_count,
                        cap=SearxNGClient.SEARCH_BUDGET_CAP,
                    )
                except Exception as exc:  # noqa: BLE001 - telemetry must not break search
                    logger.debug("search trace emission failed: %s", exc)
            return SearchResponse(query=query, results=[], total=0, engines_used=[])

        SearxNGClient._search_count += 1
        SearxNGClient._owner_counts[owner] = owner_used + 1

        # F-04/F-06b: fail fast ONLY when the PREFERRED PROFILE for this
        # category is dead. The old gate checked the global referenced set, so
        # a web profile with 4/4 crawlers 403/429ing still passed whenever two
        # scholar API engines happened to be healthy — and then general
        # queries burned the full web rotation budget proving the web pool
        # was dead before the fleet fan-out ever ran (the Aug 9 failure).
        #
        # The per-profile gate has the opposite behaviour: when the preferred
        # profile is dead but OTHER source classes are healthy, the query is
        # NOT returned empty — it falls through to the full-pool fan-out,
        # which serves it from the healthy profiles. The dead-pool guard now
        # only fires when the fleet has fewer than two healthy engines
        # anywhere, which is the true total-outage case.
        health = get_engine_health()
        preferred_profile = self._pool.preferred_profile(categories or "general")
        preferred_engines = set()
        for endpoint in self._pool.endpoints:
            if endpoint.profile == preferred_profile:
                preferred_engines.update(endpoint.engines)
        preferred_engines &= referenced_engines()

        preferred_healthy = health.healthy_count(preferred_engines) if preferred_engines else 0
        fleet_healthy = health.healthy_count(referenced_engines())

        if preferred_engines and preferred_healthy == 0 and fleet_healthy >= 2:
            logger.warning(
                "SEARCH PROFILE FAIL-FAST: preferred profile '%s' has 0/%d "
                "healthy engines but %d fleet engines are healthy — skipping "
                "profile rotation and going straight to the full-pool fan-out",
                preferred_profile,
                len(preferred_engines),
                fleet_healthy,
            )
            fanout_response = await self._search_all_replicas(
                query=query,
                num_results=num_results,
                language=language,
                time_range=time_range,
                safesearch=safesearch,
            )
            if fanout_response and fanout_response.results:
                await self._set_cached(cache_key, fanout_response)
                return fanout_response
            # The fleet fan-out also produced nothing — fall through to the
            # normal degraded path below instead of returning a bare empty.

        if fleet_healthy < 2:
            logger.error(
                "SEARCH FAIL-FAST: only %d/%d referenced engines healthy — "
                "returning empty without issuing queries",
                fleet_healthy,
                len(referenced_engines()),
            )
            return SearchResponse(
                query=query,
                results=[],
                total=0,
                engines_used=[],
                retrieval_degraded=True,
                degradation_events=[{
                    "type": "search_fail_fast_dead_pool",
                    "healthy": fleet_healthy,
                    "required": 2,
                }],
            )

        assert SearxNGClient._semaphore is not None
        async with SearxNGClient._semaphore:
            # ── PRIMARY: SearXNG JSON API, with engine rotation on zero ──
            # (P2-26 fix 3 / P2-G24: a zero-result response rotates to the
            # standby pool and retries once before falling through).
            searxng_response = await self._search_with_rotation(
                query=query,
                num_results=num_results,
                categories=categories,
                language=language,
                time_range=time_range,
                engines=effective_engines,
                safesearch=safesearch,
                explicit_engines=bool(engines),
            )

            if searxng_response and searxng_response.results:
                await self._set_cached(cache_key, searxng_response)
                return searxng_response

            # ── F-03 FULL-POOL FAN-OUT ──
            # The preferred profile AND its standby rotation produced nothing.
            # Before leaving the local stack for Jina, ask the WHOLE fleet:
            # one parallel request per replica with that replica's full set of
            # currently-healthy engines. The scholar/reference API engines do
            # not ban datacenter IPs the way crawlers do, so a dead web pool
            # can still be rescued by crossref/openalex/wikipedia. W-11 stays
            # intact: each request carries only engines owned by its replica.
            # Explicit-engine callers are excluded — a caller that demanded a
            # specific corpus must not have it silently replaced.
            if not engines:
                logger.info(
                    "SearXNG rotation exhausted for '%s' — FULL-POOL FAN-OUT across replicas",
                    query,
                )
                fanout_response = await self._search_all_replicas(
                    query=query,
                    num_results=num_results,
                    language=language,
                    time_range=time_range,
                    safesearch=safesearch,
                )
                if fanout_response and fanout_response.results:
                    await self._set_cached(cache_key, fanout_response)
                    return fanout_response

            # ── FALLBACK: Jina Search (s.jina.ai) ──
            logger.info("SearXNG returned no results for '%s' — falling back to Jina", query)
            jina_response = await self._search_jina_fallback(
                query=query,
                num_results=num_results,
                categories=categories,
            )

            if jina_response and jina_response.results:
                await self._set_cached(cache_key, jina_response)
                return jina_response

            # ── FINAL ESCALATION: metered Gemini Search grounding ──
            # W-14 reserves this independent server-side index for a genuine
            # local retrieval failure. It must not become the routine default,
            # but it must also be reachable from the search choke point used by
            # every specialist rather than only from a few bespoke call sites.
            logger.info(
                "SearXNG and Jina exhausted for '%s' — escalating to grounded search",
                query,
            )
            grounded_response = await self._search_grounded_fallback(
                query=query,
                num_results=num_results,
                categories=categories,
            )
            if grounded_response and grounded_response.results:
                await self._set_cached(cache_key, grounded_response)
                return grounded_response

        # All search paths exhausted
        logger.warning("All search paths exhausted for query: '%s'", query)
        return SearchResponse(
            query=query,
            results=[],
            total=0,
            engines_used=[],
        )

    async def search_images(
        self,
        query: str,
        num_results: int = 5,
        safesearch: int = 1,
    ) -> SearchResponse:
        """Search for images via SearxNG.

        Uses the 'images' category to find image results.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="images",
            safesearch=safesearch,
        )

    async def search_news(
        self,
        query: str,
        num_results: int = 10,
        time_range: str = "",
        language: str = "en",
    ) -> SearchResponse:
        """Search for news articles via SearxNG.

        Uses the 'news' category with optional time range filtering.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="news",
            time_range=time_range,
            language=language,
        )

    async def search_science(
        self,
        query: str,
        num_results: int = 10,
    ) -> SearchResponse:
        """Search for scientific/academic content via SearxNG.

        Uses the 'science' category which targets academic databases.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="science",
        )

    async def search_it(
        self,
        query: str,
        num_results: int = 10,
    ) -> SearchResponse:
        """Search for IT/technology content via SearxNG.

        Uses the 'it' category which targets tech-specific engines.
        """
        return await self.search(
            query=query,
            num_results=num_results,
            categories="it",
        )

    async def close(self) -> None:
        """Close every pooled endpoint client."""
        for client in self._clients.values():
            if not client.is_closed:
                await client.aclose()
        self._clients.clear()

    async def __aenter__(self) -> SearxNGClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
