"""
HYPERION Unified Extract — THE single extraction ladder.

Fix 2.1 (HYPERION_DEEP_AUDIT_2026-07-27.md §4.5 Finding B-4, §6 Phase 2 item
2.1): "Delete two of the three extraction ladders. Make ``UnifiedExtract`` the
single implementation and wire ``sub_agent`` + ``deep_search`` to it."

THE DEFECT THIS MODULE NOW FIXES
--------------------------------
Before this fix HYPERION contained **three** independent, divergent
implementations of "climb a ladder of extraction tools until one returns
usable content":

  1. ``UnifiedExtract`` (this module) — 7 tiers, cheap-first, capability-gated,
     the best-engineered of the three … and, per the audit's own consumer grep,
     **it had zero callers**:

         grep -rn "unified_extract|UnifiedExtract" --include=*.py .
           → hyperion/tools/__init__.py:27   (import)
           → hyperion/tools/__init__.py:108  (__all__)
           → hyperion/tools/__init__.py:109  (__all__)

  2. ``deep_search._extract_batch`` — a *second* 6-tier ladder
     (jina → http → obscura → crawl4ai → scrapling → flaresolverr), with its
     own availability probes, its own quality gate, its own truncation.
  3. ``sub_agent._gather_raw_data`` — a *third* ladder, 5 tiers hand-unrolled
     inline (Obscura → Scrapling → Jina → Crawl4AI → FlareSolverr), each tier a
     copy-pasted ``for url in all_urls[:N]`` loop with its own slice budget.

Three implementations is not merely untidy; each one of the following was a
real, observable consequence:

  * **Tier coverage diverged.** ``UnifiedExtract`` had ``curl_cffi``,
    ``nodriver``, ``camoufox`` and ``wayback`` tiers that neither consumer
    could reach. ``deep_search`` had ``http`` (httpx + trafilatura — the
    keyless, browserless workhorse) and ``flaresolverr`` tiers that
    ``UnifiedExtract`` did not have. So *whichever* ladder a given call used,
    it was missing tools that the codebase already shipped and paid to
    maintain. Retrieval yield depended on which of the three code paths you
    happened to enter.
  * **Fixes did not propagate.** The archive-before-live ordering bug
    (wayback ahead of camoufox) was fixed here, in this module — the one with
    no callers. Neither live consumer benefited.
  * **Capability gating existed in two of three.** ``sub_agent``'s inline
    ladder had none at all: it attempted Obscura on Linux, per URL, for every
    sub-agent of every specialist, forever.
  * **Provenance was incomparable.** Three different naming schemes for the
    same tool meant ``tool_used`` could not be aggregated across the system.

THE LADDER
----------
One declared order, cheapest-first, live-fetch before archive:

  Tier 0  curl_cffi     TLS-fingerprint spoof — no browser, cheapest
  Tier 1  jina          Jina Reader — keyless, fast, clean markdown
  Tier 2  http          httpx + trafilatura — keyless, browserless workhorse
  Tier 3  obscura       stealth local binary, JS rendering
  Tier 4  nodriver      undetected Chrome — JS-heavy anti-bot
  Tier 5  crawl4ai      heavy extraction, PDFs
  Tier 6  scrapling     adaptive anti-bot (Playwright) + httpx fallback
  Tier 7  camoufox      stealth Firefox — nuclear option for anti-bot
  Tier 8  flaresolverr  CAPTCHA solving via external container
  Tier 9  wayback       ARCHIVED copy — genuine last resort

``wayback`` is last on purpose. It was previously ordered *ahead* of camoufox,
so an anti-bot page that camoufox could render live was answered from a stale
archived snapshot instead — archive-before-live is exactly backwards for a
system whose output is dated market analysis. ``flaresolverr`` likewise sits
before ``wayback``: solving a CAPTCHA gets you today's page, an archive does
not.

TWO DRIVERS, ONE TIER TABLE
---------------------------
The ladder body lives in exactly one place — the ``_extract_<tier>`` table
below, dispatched dynamically from :attr:`UnifiedExtract.tier_order`. Two
drivers walk that one table:

  * :meth:`extract` — single URL, climbs until a tier produces quality content.
  * :meth:`extract_ladder` — batch, **tier-major**: every pending URL is tried
    at tier N before *any* URL is tried at tier N+1. This is what
    ``deep_search._extract_batch`` used to do by hand, and it is the correct
    batching strategy: it never launches a browser for URL A while URL B is
    still un-attempted at the free tier.

Consumers restrict the ladder by passing ``tiers=`` — that is how
``deep_search`` keeps its declared 6-tier subset and how ``sub_agent``
restricts extraction to the tools its spec actually granted it (§4.7 tool
subset rule), without either of them re-implementing a ladder.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any, cast

from hyperion.tools._content_quality import is_quality_content
from hyperion.tools.camoufox_client import CamoufoxClient
from hyperion.tools.content_selector import DEFAULT_BUDGET_CHARS, select_relevant_content
from hyperion.tools.crawl4ai import Crawl4AIClient
from hyperion.tools.curl_cffi_client import CurlCffiClient
from hyperion.tools.flaresolverr import FlareBreaker, FlareSolverrClient
from hyperion.tools.http_extract import HttpExtractClient
from hyperion.tools.jina import JinaClient
from hyperion.tools.nodriver_client import NodriverClient
from hyperion.tools.obscura import ObscuraClient
from hyperion.tools.scrapling import ScraplingClient
from hyperion.tools.source_classifier import is_paywall_host
from hyperion.tools.wayback import WaybackClient

logger = logging.getLogger(__name__)

# F-0.1-8 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): shared per-engagement fetch
# cache. Module-level so every UnifiedExtract instance (each specialist spawns
# one) shares it — the same competitor page is fetched ONCE per engagement
# instead of once per concurrently-running specialist. Engagement-scoped: call
# clear_fetch_cache() at engagement start. Keys are URLs; values are shallow
# copies of the first successful extraction result.
_FETCH_CACHE: dict[str, UnifiedExtractResult] = {}


def clear_fetch_cache() -> None:
    """F-0.1-8: reset the shared fetch cache (call at engagement start)."""
    _FETCH_CACHE.clear()


def _batch_result_ok(result: Any) -> bool:
    """OVERHAUL4 P7: True when a batch-tier result is a successful extraction
    (an exception is never OK)."""
    return not isinstance(result, BaseException) and bool(
        getattr(result, "success", False)
    )


def _batch_result_transient(result: Any) -> bool:
    """OVERHAUL4 P7: True when a batch-tier failure looks transient
    (timeout/connection/5xx/429) and is worth a same-tier retry."""
    if isinstance(result, BaseException):
        msg = f"{type(result).__name__}: {result}"
    else:
        msg = getattr(result, "error", "") or ""
    return UnifiedExtract._is_transient_error(msg)


@dataclass
class UnifiedExtractResult:
    """A unified extraction result from the fallback chain."""

    url: str
    title: str = ""
    content: str = ""
    markdown: str = ""
    html: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    # OVERHAUL4 P7: media URLs found ON the page (img/media sub-resources)
    # — populated by tiers that can enumerate rendered assets (obscura
    # --dump assets). Pure media FILE urls still fail fast as "media".
    images: list[str] = field(default_factory=list)
    tool_used: str = ""
    tools_tried: list[str] = field(default_factory=list)
    success: bool = False
    error: str = ""
    took_ms: int = 0
    cached: bool = False

    # A consumer that adapts its own extractors into the shared driver (see
    # ``UnifiedExtract.extract_ladder``'s ``tier_resolver``) parks its native
    # result object here, so delegating through the common driver is lossless
    # and the consumer does not have to reconstruct fields this dataclass has
    # no slot for (``deep_search`` needs ``published_date``). Deliberately
    # excluded from ``to_dict()`` — it is an internal hand-back, not payload.
    raw: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "html": self.html,
            "links": self.links,
            "tables": self.tables,
            "images": self.images,
            "tool_used": self.tool_used,
            "tools_tried": self.tools_tried,
            "success": self.success,
            "error": self.error,
            "took_ms": self.took_ms,
            "cached": self.cached,
        }


@dataclass
class LadderOutcome:
    """Result of a tier-major batch climb (:meth:`UnifiedExtract.extract_ladder`).

    ``tools_used`` vs ``tools_tried`` is the distinction that makes provenance
    honest: a tier that ran and produced nothing must not be reported as the
    source of anything, but it must still be visible as *attempted* so that
    "everything failed" is distinguishable from "nothing needed extracting".
    ``errors`` maps a tried-but-fruitless tier to why, so a thin engagement can
    be traced to a cause rather than to a shrug.
    """

    results: list[UnifiedExtractResult] = field(default_factory=list)
    tools_used: list[str] = field(default_factory=list)
    tools_tried: list[str] = field(default_factory=list)
    errors: dict[str, str] = field(default_factory=dict)
    tiers_unavailable: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "tools_used": self.tools_used,
            "tools_tried": self.tools_tried,
            "errors": self.errors,
            "tiers_unavailable": self.tiers_unavailable,
        }


class UnifiedExtract:
    """The single extraction ladder. Cheap-first, capability-gated, fail-loud.

    Usage:
        # full ladder, one URL
        async with UnifiedExtract(settings=settings) as ex:
            result = await ex.extract("https://competitor.com/pricing")
            if result.success:
                print(f"via {result.tool_used}: {result.content[:200]}")

        # restricted ladder, batch, tier-major
        ex = UnifiedExtract(settings=settings, tiers=("jina", "http"))
        outcome = await ex.extract_ladder(urls, concurrency=5)
    """

    MIN_CONTENT_LENGTH = 100  # Minimum content length to consider extraction successful
    JINA_TIMEOUT = 30
    OBSCURA_TIMEOUT = 60
    CRAWL4AI_TIMEOUT = 120
    WAYBACK_TIMEOUT = 30
    CURL_CFFI_TIMEOUT = 20
    NODRIVER_TIMEOUT = 30
    CAMOUFOX_TIMEOUT = 30

    # Default concurrency for batch climbs.
    EXTRACTION_CONCURRENCY = 5

    # Ladder order, cheapest first, live-fetch before archive. Declared once so
    # the ladder, the module docstring, and availability reporting cannot drift.
    #
    # NOTE the position of `wayback`: it is the LAST RESORT, after every
    # live-fetch tier. The chain previously ran wayback BEFORE camoufox,
    # contradicting this class's own docstring and returning stale archived
    # snapshots of pages that were live and fetchable at that moment.
    TIER_ORDER: tuple[str, ...] = (
        "curl_cffi",
        "jina",
        "http",
        # OVERHAUL4 P7: firecrawl self-host — one HTTP call with server-side
        # JS rendering + parallel batch, cheaper per page than any local
        # browser tier, so it sits right after the no-JS tiers and before
        # obscura/nodriver/crawl4ai/camoufox.
        "firecrawl",
        "obscura",
        "nodriver",
        "crawl4ai",
        "scrapling",
        "camoufox",
        "flaresolverr",
        "wayback",
    )

    # Tiers that cannot execute JavaScript. Skipped when the caller knows the
    # page needs rendering (pricing calculators, interactive dashboards), since
    # attempting them can only return a shell of the document.
    NON_JS_TIERS: tuple[str, ...] = ("curl_cffi", "jina", "http")

    # OVERHAUL4 P7 (URL-aware ladder): how many attempts each tier gets before
    # the ladder moves on. Retries only fire on TRANSIENT failures (timeout /
    # connection / 5xx / 429) — a deterministic "not installed" or
    # "no usable content" is not retried.
    TIER_MAX_ATTEMPTS = 3

    # OVERHAUL4 P7 (capability-based assignment): for js_heavy pages, the
    # tier order is by CAPACITY, not raw cheapness — obscura leads (instant
    # Rust browser, stealth TLS impersonation, JS rendering, cheaper than
    # firecrawl's node/playwright stack), then the HTTP-based firecrawl, then
    # the anti-bot fetchers in increasing stealth/heaviness, cloudflare
    # solver, and the archive as the true last resort.
    JS_HEAVY_TIER_ORDER: tuple[str, ...] = (
        "obscura",
        "firecrawl",
        "scrapling",
        "nodriver",
        "crawl4ai",
        "camoufox",
        "flaresolverr",
        "wayback",
    )

    # Known JS-rendered / anti-bot hostname fragments. Pages from these hosts
    # skip the non-JS tiers entirely (their HTML is an empty shell).
    JS_HEAVY_HOST_HINTS: frozenset[str] = frozenset({
        "twitter.com", "x.com", "linkedin.com", "facebook.com",
        "instagram.com", "tiktok.com", "glassdoor.com", "indeed.com",
        "producthunt.com", "statista.com", "crunchbase.com",
        "g2.com", "capterra.com", "trustpilot.com", "etmoney.com",
        "investing.com", "tradingview.com", "app.", "dashboards.",
    })
    # URL path fragments that indicate a JS-heavy interactive page.
    JS_HEAVY_PATH_HINTS: frozenset[str] = frozenset({
        "/pricing", "/features", "/compare", "/dashboard", "/calculator",
        "/explore", "/products", "/app", "/chart", "/interactive",
    })

    def __init__(
        self,
        settings: Any | None = None,
        tiers: tuple[str, ...] | list[str] | None = None,
        content_budget_chars: int = DEFAULT_BUDGET_CHARS,
    ) -> None:
        """
        Args:
            settings: HYPERION settings object, forwarded to every leaf client.
            tiers: Optional restriction of :attr:`TIER_ORDER`. Entries not in
                ``TIER_ORDER`` are dropped with a warning rather than silently
                accepted — a typo'd tier name must not quietly shrink the
                ladder. Order is normalised back to ``TIER_ORDER`` order so a
                caller cannot accidentally put a browser tier ahead of a free
                one (the whole point of the ladder is that it is cheap-first).
            content_budget_chars: Fix 2.2 — the retained-content budget applied
                when (and only when) a query is supplied to :meth:`extract` /
                :meth:`extract_ladder`. Content over budget is fitted by
                relevance rather than head-sliced.
        """
        self.settings = settings
        self.tier_order: tuple[str, ...] = self._normalize_tiers(tiers)
        self.content_budget_chars = int(content_budget_chars) or DEFAULT_BUDGET_CHARS

        # Fix 2.2: the query the in-flight extraction is being performed for.
        # Instance state rather than a parameter on all ten `_extract_<tier>`
        # methods: the tier contract is `(url, *, extract_tables,
        # extract_links)` and `deep_search` adapts to it through
        # `tier_resolver`, so widening it would break the seam that fix 2.1
        # deliberately preserved. An extraction batch is single-query by
        # construction, so there is nothing to lose by holding it here.
        self._active_query: str = ""
        # URL → SelectionResult.to_dict(), for fix 2.6's yield metric.
        self._selection_stats: dict[str, dict[str, Any]] = {}

        self._jina: JinaClient | None = None
        self._http_extract: HttpExtractClient | None = None
        self._obscura: ObscuraClient | None = None
        self._crawl4ai: Crawl4AIClient | None = None
        self._scrapling: ScraplingClient | None = None
        self._flaresolverr: FlareSolverrClient | None = None
        self._wayback: WaybackClient | None = None
        self._curl_cffi: CurlCffiClient | None = None
        self._nodriver: NodriverClient | None = None
        self._camoufox: CamoufoxClient | None = None

        # Cached per-tier availability. The probes are cheap but not free
        # (Obscura's shells out), and the answer cannot change mid-run.
        self._availability: dict[str, bool] = {}
        self._skipped: dict[str, str] = {}

    @classmethod
    def _normalize_tiers(cls, tiers: tuple[str, ...] | list[str] | None) -> tuple[str, ...]:
        """Restrict the ladder to ``tiers``, preserving cheap-first order."""
        if not tiers:
            return cls.TIER_ORDER
        requested = set(tiers)
        unknown = sorted(requested - set(cls.TIER_ORDER))
        if unknown:
            logger.warning(
                "UnifiedExtract: ignoring unknown extraction tier(s) %s "
                "(known tiers: %s)",
                ", ".join(unknown),
                ", ".join(cls.TIER_ORDER),
            )
        ordered = tuple(t for t in cls.TIER_ORDER if t in requested)
        if not ordered:
            logger.warning(
                "UnifiedExtract: tiers=%r selected nothing usable — falling "
                "back to the full ladder rather than extracting nothing",
                tiers,
            )
            return cls.TIER_ORDER
        return ordered

    # ── Lazy leaf clients ───────────────────────────────────────────────────

    async def _get_jina(self) -> JinaClient:
        if self._jina is None:
            self._jina = JinaClient(settings=self.settings)
        return self._jina

    async def _get_http_extract(self) -> HttpExtractClient:
        if self._http_extract is None:
            self._http_extract = HttpExtractClient(settings=self.settings)
        return self._http_extract

    async def _get_obscura(self) -> ObscuraClient:
        if self._obscura is None:
            self._obscura = ObscuraClient(settings=self.settings)
        return self._obscura

    async def _get_crawl4ai(self) -> Crawl4AIClient:
        if self._crawl4ai is None:
            self._crawl4ai = Crawl4AIClient(settings=self.settings)
        return self._crawl4ai

    async def _get_scrapling(self) -> ScraplingClient:
        if self._scrapling is None:
            self._scrapling = ScraplingClient(settings=self.settings)
        return self._scrapling

    async def _get_flaresolverr(self) -> FlareSolverrClient:
        if self._flaresolverr is None:
            solver_url = (
                getattr(self.settings, "flaresolverr_url", "") if self.settings else ""
            )
            self._flaresolverr = FlareSolverrClient(solver_url=solver_url or "")
        return self._flaresolverr

    async def _get_wayback(self) -> WaybackClient:
        if self._wayback is None:
            self._wayback = WaybackClient(settings=self.settings)
        return self._wayback

    async def _get_curl_cffi(self) -> CurlCffiClient:
        if self._curl_cffi is None:
            self._curl_cffi = CurlCffiClient(settings=self.settings)
        return self._curl_cffi

    async def _get_nodriver(self) -> NodriverClient:
        if self._nodriver is None:
            self._nodriver = NodriverClient(settings=self.settings)
        return self._nodriver

    async def _get_camoufox(self) -> CamoufoxClient:
        if self._camoufox is None:
            self._camoufox = CamoufoxClient(settings=self.settings)
        return self._camoufox

    # ── Capability gating ────────────────────────────────────────────────────
    #
    # THE DEFECT THIS FIXES
    # ---------------------
    # The ladder attempted every tier unconditionally, including tiers that
    # *cannot* run in the current environment. Measured on a checkout where the
    # optional stealth extras are not installed, a single `extract()` call
    # produced:
    #
    #   tools_tried : [curl_cffi, jina, obscura, nodriver, crawl4ai, wayback, camoufox]
    #   error       : curl_cffi: curl_cffi not installed; Obscura: Obscura binary
    #                 not available on linux; nodriver: nodriver not installed;
    #                 ...; Camoufox: camoufox not installed
    #
    # Four of the seven attempts were structurally impossible. Three costs, all
    # real:
    #
    #   1. Every extraction pays the construct-call-fail round trip for tools
    #      that will never work — repeated for every URL of every agent, of
    #      which an engagement makes hundreds.
    #   2. `tools_tried` is provenance. Listing a tool that never ran makes the
    #      audit trail wrong, and a genuine Obscura failure is indistinguishable
    #      from Obscura not being installed.
    #   3. The real error is buried. "no content extracted" arrives with four
    #      not-installed messages in front of the one cause that matters.
    #
    # Every client already exposed an availability probe (`_check_available()`,
    # or `_binary_available()` for Obscura) — the ladder simply never consulted
    # them. Availability is cached per instance because the answer cannot change
    # during a run, and the Obscura probe in particular shells out.

    def _tier_available(self, tool: str) -> bool:
        """True when ``tool`` can actually run here.

        Unknown tools default to True: a tier with no probe is assumed usable
        (Jina and Wayback are plain HTTP, Crawl4AI has an httpx fallback, and
        Scrapling degrades to its own httpx path when Playwright is absent), so
        adding a tier without a probe degrades to attempt-and-report rather
        than silently disabling it.

        ``flaresolverr`` is deliberately NOT probed here. Its usability is
        governed by :class:`FlareBreaker`, a *time-varying* circuit breaker —
        caching that decision for the life of the instance would either pin the
        breaker open long after the container recovered, or pin it closed while
        it is flooding. The breaker is consulted inside the tier instead.
        """
        cached = self._availability.get(tool)
        if cached is not None:
            return cached

        available = True
        detail = ""
        try:
            if tool == "curl_cffi":
                available = CurlCffiClient(settings=self.settings)._check_available()
                detail = "" if available else "curl_cffi not installed"
            elif tool == "firecrawl":
                # OVERHAUL4 P7: self-host reachability probe (short timeout —
                # a dead instance must be skipped, not waited on for 30s).
                try:
                    import httpx as _httpx

                    from hyperion.tools.firecrawl_client import FIRECRAWL_DEFAULT_URL

                    base = (
                        str(getattr(self.settings, "firecrawl_url", "") or "")
                        or FIRECRAWL_DEFAULT_URL
                    ).rstrip("/")
                    with _httpx.Client(timeout=3.0) as _c:
                        _r = _c.get(f"{base}/test")
                    available = _r.status_code < 500
                    detail = "" if available else "firecrawl not reachable"
                except Exception as _exc:  # noqa: BLE001 - probe must not raise
                    available = False
                    detail = f"firecrawl not reachable ({type(_exc).__name__})"
            elif tool == "nodriver":
                available = NodriverClient(settings=self.settings)._check_available()
                detail = "" if available else "nodriver not installed"
            elif tool == "camoufox":
                available = CamoufoxClient(settings=self.settings)._check_available()
                detail = "" if available else "camoufox not installed"
            elif tool == "obscura":
                client = ObscuraClient(settings=self.settings)
                available = client._binary_available()
                if not available:
                    binary = client._find_obscura()
                    detail = (
                        f"binary present but not executable here ({binary})"
                        if binary
                        else "Obscura binary not found"
                    )
            elif tool == "http":
                # http_extract IS the keyless/browserless workhorse, but it is
                # trafilatura or nothing: without it, `extract()` returns
                # error="trafilatura not installed" for every URL (§4.6
                # Finding B-5). Probing it here turns a guaranteed-failed
                # attempt per URL into one named skip per run.
                import importlib.util

                available = importlib.util.find_spec("trafilatura") is not None
                detail = "" if available else "trafilatura not installed"
        except Exception:  # noqa: BLE001 - best-effort, returns a safe default
            # A probe that raises must not disable a tier outright — attempting
            # it and failing is strictly better than skipping something usable.
            available = True

        self._availability[tool] = available
        if not available:
            self._skipped[tool] = detail or "not installed/available in this environment"
            logger.debug("extraction tier %s unavailable — skipping", tool)
        return available

    def available_tiers(self) -> list[str]:
        """Tiers that can run here, in ladder order. Useful for health output."""
        return [t for t in self.tier_order if self._tier_available(t)]

    def unavailable_tiers(self) -> dict[str, str]:
        """Tiers that cannot run here, mapped to why."""
        for tool in self.tier_order:
            self._tier_available(tool)
        return dict(self._skipped)

    def _is_quality_content(self, content: str) -> bool:
        """Check if extracted content meets quality thresholds.

        Phase 5.1d: the inline substring counter this replaced accepted a
        stock ``"404 Not Found. The requested URL was not found on this
        server."`` body as quality content (only 2 of its 5 indicators
        matched, and the gate required *more than* 2). Because a "successful"
        rung stops the ladder descending, that error text became the final
        extraction result and flowed downstream as evidence. Detection now
        lives in :mod:`hyperion.tools._content_quality`, shared with
        `deep_search` so the two ladders cannot drift apart again.
        """
        return is_quality_content(content, self.MIN_CONTENT_LENGTH)

    def _finish(
        self,
        url: str,
        tier: str,
        *,
        primary: str,
        title: str = "",
        content: str = "",
        markdown: str = "",
        html: str = "",
        links: list[dict[str, str]] | None = None,
        tables: list[dict[str, Any]] | None = None,
        error: str = "",
    ) -> UnifiedExtractResult:
        """Build a tier result, applying the shared quality gate exactly once.

        Every tier used to inline its own ``status_code == 200 and
        _is_quality_content(...)`` check against a *different* field (markdown
        here, content there), which is precisely how the three ladders drifted.
        One helper, one gate, one field-precedence rule.

        Fix 2.2 (§4.7 Finding B-6) hooks in *here*, for the same reason the
        quality gate does: this is the one place every tier's output passes
        through. When a query is in flight (:attr:`_active_query`, set by
        :meth:`extract` / :meth:`extract_ladder`), oversized content is fitted
        to :attr:`content_budget_chars` by relevance — chunk → rerank → top-k —
        instead of being handed to the caller whole and head-sliced downstream.

        The gate runs on the **pre-selection** text on purpose. ``primary`` is
        what the tier actually retrieved; judging retrieval success on a
        post-selection excerpt would let a selection bug read as an extraction
        failure and send the ladder climbing to a browser tier for a page that
        was fetched perfectly well.
        """
        ok = bool(primary) and self._is_quality_content(primary)
        resolved_content = content or primary
        resolved_markdown = markdown or primary
        if ok:
            resolved_content = self._fit(resolved_content, url)
            resolved_markdown = self._fit(resolved_markdown, url)
        return UnifiedExtractResult(
            url=url,
            title=title,
            content=resolved_content,
            markdown=resolved_markdown,
            html=html,
            links=list(links or []),
            tables=list(tables or []),
            tool_used=tier if ok else "",
            success=ok,
            error="" if ok else (error or "no quality content returned"),
        )

    def _fit(self, text: str, url: str = "") -> str:
        """Fit ``text`` into the content budget by relevance to the active query.

        No-ops when no query is in flight. That is deliberate rather than
        defensive: without a query there is nothing to rank against, and
        silently head-slicing here would make ``UnifiedExtract`` lossy for
        callers that never asked for a budget — the audit's §4.7 complaint was
        about *unweighted* truncation, and unweighted truncation applied by
        default would simply relocate the defect into this module.
        """
        if not text or not self._active_query:
            return text
        if len(text) <= self.content_budget_chars:
            return text
        selection = select_relevant_content(
            text,
            self._active_query,
            budget_chars=self.content_budget_chars,
        )
        if url:
            self._selection_stats[url] = selection.to_dict()
        return selection.content

    # ── The tier table ──────────────────────────────────────────────────────
    #
    # One method per tier, named `_extract_<tier>` so :attr:`tier_order` can
    # dispatch dynamically. A tier that is advertised in the ladder but has no
    # method here is reported as an error, not silently skipped — that is
    # exactly how `deep_search._extract_scrapling` stayed dead code for months
    # while being listed in the docstring.
    #
    # Contract for every tier method:
    #   * takes (url, *, extract_tables, extract_links)
    #   * returns an UnifiedExtractResult; success=True only when the shared
    #     quality gate passed
    #   * may raise — the driver catches and records; it must never raise a
    #     *bare* `except: pass`

    async def _extract_curl_cffi(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 0 — TLS fingerprint spoof. No browser, cheapest possible fetch."""
        cffi = await self._get_curl_cffi()
        r = await cffi.fetch(url, timeout=self.CURL_CFFI_TIMEOUT)
        if not getattr(r, "success", False):
            return self._finish(url, "curl_cffi", primary="", error=getattr(r, "error", ""))
        return self._finish(
            url, "curl_cffi", primary=r.markdown, markdown=r.markdown, error=r.error
        )

    async def _extract_jina(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 1 — Jina Reader. Keyless, fast, clean markdown."""
        jina = await self._get_jina()
        r = await jina.read(url)
        if getattr(r, "status_code", 0) != 200:
            return self._finish(
                url,
                "jina",
                primary="",
                error=getattr(r, "error", "") or f"HTTP {getattr(r, 'status_code', 0)}",
            )
        primary = r.markdown or r.content
        return self._finish(
            url,
            "jina",
            primary=primary,
            title=r.title,
            content=r.content,
            markdown=r.markdown,
            error=r.error,
        )

    async def _extract_http(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 2 — httpx + trafilatura. Keyless, browserless workhorse.

        Imported into this ladder from ``deep_search``, where it was the only
        place it existed. ``UnifiedExtract`` previously jumped straight from
        Jina to a browser tier, skipping the cheapest *parsing* tier the
        codebase ships.
        """
        client = await self._get_http_extract()
        r = await client.extract(url)
        if not getattr(r, "success", False):
            return self._finish(url, "http", primary="", error=getattr(r, "error", ""))
        primary = r.content or r.markdown
        return self._finish(
            url,
            "http",
            primary=primary,
            title=r.title,
            content=r.content,
            markdown=r.markdown,
            error=r.error,
        )

    async def _extract_firecrawl(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """OVERHAUL4 P7 — firecrawl tier. Self-hosted crawl/scrape engine with
        server-side JS rendering; one HTTP call, cheaper per page than any
        local browser tier. ``extract_tables`` is intentionally ignored here:
        the tier requests ``onlyMainContent`` markdown (tables arrive inline);
        local browser tiers remain the table-structured fallback."""
        from hyperion.tools.firecrawl_client import FirecrawlClient

        client = FirecrawlClient(settings=self.settings)
        try:
            r = await client.scrape(url)
        finally:
            await client.close()
        if not r.success:
            return self._finish(url, "firecrawl", primary="", error=r.error or "no content")
        links = [dict(link) for link in r.links] if extract_links else []
        return self._finish(
            url,
            "firecrawl",
            primary=r.markdown or r.html,
            title=r.title,
            markdown=r.markdown,
            html=r.html,
            links=links,
        )

    async def _extract_obscura(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier — Obscura. Stealth local Rust browser with JS rendering.

        OVERHAUL4 P7: obscura is the cheap-and-capable browser tier for
        js_heavy pages — instant cold start, 30MB RAM, ``--stealth`` TLS
        impersonation. After the text fetch it also enumerates the rendered
        page's media assets (``--dump assets``) into ``result.images``, so a
        page CONTAINING media contributes those URLs downstream.
        """
        obscura = await self._get_obscura()
        r = await obscura.fetch(url, output_format="markdown")
        if getattr(r, "status_code", 0) != 200:
            return self._finish(
                url,
                "obscura",
                primary="",
                error=getattr(r, "error", "") or f"HTTP {getattr(r, 'status_code', 0)}",
            )
        primary = r.markdown or r.content
        result = self._finish(
            url,
            "obscura",
            primary=primary,
            title=r.title,
            content=r.content,
            markdown=r.markdown,
            error=r.error,
        )
        if result.success and extract_links:
            # Media-on-page: the rendered asset graph (img/media URLs).
            try:
                result.images = await obscura.fetch_assets(url)
            except Exception as exc:  # noqa: BLE001 - assets are best-effort
                logger.debug("obscura asset enumeration failed for %s: %s", url, exc)
        return result

    async def _extract_nodriver(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 4 — nodriver. Undetected Chrome for JS-heavy anti-bot sites."""
        nodriver = await self._get_nodriver()
        r = await nodriver.extract(url, timeout=self.NODRIVER_TIMEOUT)
        if not getattr(r, "success", False):
            return self._finish(url, "nodriver", primary="", error=getattr(r, "error", ""))
        return self._finish(
            url,
            "nodriver",
            primary=r.content,
            title=r.title,
            content=r.content,
            markdown=r.markdown,
            html=r.html,
            error=r.error,
        )

    async def _extract_crawl4ai(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 5 — Crawl4AI. Heavy extraction; the only tier that yields tables."""
        crawl4ai = await self._get_crawl4ai()
        r = await crawl4ai.crawl(
            url=url, extract_tables=extract_tables, extract_links=extract_links
        )
        if getattr(r, "status_code", 0) != 200:
            return self._finish(
                url,
                "crawl4ai",
                primary="",
                error=getattr(r, "error", "") or f"HTTP {getattr(r, 'status_code', 0)}",
            )
        primary = r.markdown or r.content
        return self._finish(
            url,
            "crawl4ai",
            primary=primary,
            title=r.title,
            content=r.content,
            markdown=r.markdown,
            html=r.html,
            links=r.links,
            tables=r.tables,
            error=r.error,
        )

    async def _extract_scrapling(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 6 — Scrapling. Adaptive anti-bot (Playwright) + httpx fallback.

        Imported into this ladder from ``deep_search``, where — despite being
        advertised in the class docstring and having a full implementation —
        ``_extract_batch`` never actually called it. An entire anti-bot tier was
        dead code.
        """
        scrapling = await self._get_scrapling()
        r = await scrapling.fetch(url, stealth=True)
        primary = r.content or r.markdown
        return self._finish(
            url,
            "scrapling",
            primary=primary,
            title=r.title,
            content=r.content,
            markdown=r.markdown,
            html=r.html,
            error=r.error,
        )

    async def _extract_camoufox(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 7 — Camoufox. Stealth Firefox; the nuclear option for anti-bot."""
        camoufox = await self._get_camoufox()
        r = await camoufox.extract(url, timeout=self.CAMOUFOX_TIMEOUT)
        if not getattr(r, "success", False):
            return self._finish(url, "camoufox", primary="", error=getattr(r, "error", ""))
        return self._finish(
            url,
            "camoufox",
            primary=r.content,
            title=r.title,
            content=r.content,
            markdown=r.markdown,
            html=r.html,
            error=r.error,
        )

    async def _extract_flaresolverr(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 8 — FlareSolverr. CAPTCHA-protected pages, via a container.

        Gated on :class:`FlareBreaker` rather than on ``_tier_available``: the
        breaker is time-varying, and ``_tier_available`` memoises for the life
        of the instance. ``deep_search``'s copy of this tier consulted the
        breaker *not at all*, so a dead container was hammered once per URL per
        agent for the whole engagement.
        """
        if not FlareBreaker.closed():
            return self._finish(
                url,
                "flaresolverr",
                primary="",
                error="FlareBreaker open — recent consecutive failures, in cooldown",
            )
        flare = await self._get_flaresolverr()
        r = await flare.get(url)
        if not getattr(r, "success", False) or not r.html:
            FlareBreaker.record_error()
            return self._finish(
                url, "flaresolverr", primary="", error=getattr(r, "error", "") or "no HTML"
            )
        FlareBreaker.record_ok()
        text = re.sub(r"<[^>]+>", " ", r.html)
        text = re.sub(r"\s+", " ", text).strip()
        return self._finish(
            url, "flaresolverr", primary=text, content=text, markdown=r.markdown or text
        )

    async def _extract_wayback(
        self, url: str, *, extract_tables: bool = True, extract_links: bool = True
    ) -> UnifiedExtractResult:
        """Tier 9 — Wayback Machine. An ARCHIVED copy: genuine last resort.

        Only reached once every live-fetch tier has failed, so a snapshot can
        no longer displace a page that is actually reachable now.
        """
        wayback = await self._get_wayback()
        r = await wayback.fetch_snapshot(url)
        if getattr(r, "status_code", 0) != 200:
            return self._finish(
                url,
                "wayback",
                primary="",
                error=getattr(r, "error", "") or f"HTTP {getattr(r, 'status_code', 0)}",
            )
        return self._finish(
            url,
            "wayback",
            primary=r.content,
            title=r.title,
            content=r.content,
            markdown=r.content,
            error=r.error,
        )

    # ── Driver 1: single URL ────────────────────────────────────────────────

    def _classify_url(self, url: str) -> str:
        """OVERHAUL4 P7 / OVERHAUL5 W4: URL/page-type profile for tier selection.

        Returns one of ``pdf`` / ``media`` / ``paywall`` / ``js_heavy`` / ``default``:

        - ``pdf``      -> dedicated PDF text+table extraction first
        - ``media``    -> an image/video file: nothing to extract, fail fast
        - ``paywall``  -> known paywall/DOI/academic-publisher host (D-06):
          headless extraction is rejected upstream (the 08-12 run burned
          playwright+fetch on Elsevier DOIs); fail fast with a typed reason
        - ``js_heavy`` -> known JS/anti-bot host or interactive path: skip the
          non-JS tiers (their HTML is an empty shell) and start at the first
          rendering tier (firecrawl/obscura)
        - ``default``  -> cheap-first ladder as before
        """
        raw = (url or "").strip()
        lowered = raw.lower()
        path = lowered.split("?", 1)[0].split("#", 1)[0]
        host = lowered.split("//")[-1].split("/")[0]
        host = host[4:] if host.startswith("www.") else host

        if path.endswith(".pdf") or ".pdf?" in lowered:
            return "pdf"
        if path.endswith((
            ".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp",
            ".mp4", ".mp3", ".avi", ".mov", ".webm", ".zip", ".gz",
        )):
            return "media"
        # OVERHAUL5 W4 (D-06): a paywall host is a paywall regardless of JS.
        if is_paywall_host(url):
            return "paywall"
        if any(h in host for h in self.JS_HEAVY_HOST_HINTS) or any(
            h in path for h in self.JS_HEAVY_PATH_HINTS
        ):
            return "js_heavy"
        return "default"

    @staticmethod
    def _is_transient_error(error: str) -> bool:
        """OVERHAUL4 P7: whether a tier failure is worth a same-tier retry.

        Transient = timeout / connection / 5xx / 429 / rate limit. A
        deterministic failure ("not installed", "no usable content",
        "HTTP 404") is not retried — it will fail identically.
        """
        msg = (error or "").lower()
        return any(k in msg for k in (
            "timeout", "timed out", "time out", "connection", "refused",
            "reset", "temporarily", "too many requests", "rate limit",
            "429", "500", "502", "503", "504", "5xx", "connect error",
            "unreachable", "unavailable", "read error", "broken pipe",
        ))

    def _tier_order_for(self, profile: str) -> tuple[str, ...]:
        """OVERHAUL4 P7: capability-based tier order for a URL profile.

        - ``js_heavy`` -> :attr:`JS_HEAVY_TIER_ORDER` (obscura leads: instant
          Rust browser with stealth TLS impersonation, then firecrawl, then
          the anti-bot fetchers by increasing stealth/heaviness)
        - ``pdf`` / ``media`` / ``default`` -> :attr:`TIER_ORDER` (cheap-first
          ladder; pdf/media are handled by their dedicated paths before the
          ladder runs anyway)
        """
        if profile == "js_heavy":
            return self.JS_HEAVY_TIER_ORDER
        return self.tier_order

    def _eligible_tiers(self, force_js_render: bool, profile: str = "default") -> list[str]:
        tiers = list(self._tier_order_for(profile))
        if force_js_render or profile == "js_heavy":
            tiers = [t for t in tiers if t not in self.NON_JS_TIERS]
        return tiers

    def _failure(
        self, url: str, tools_tried: list[str], errors: list[str]
    ) -> UnifiedExtractResult:
        """Build the all-tiers-failed result, distinguishing failed from skipped.

        "no content extracted" with four "not installed" messages in front of
        it hides the one cause that matters, so skipped tiers are named
        separately and a thin engagement can be traced to a missing optional
        dependency rather than to the web.
        """
        detail = "; ".join(errors) if errors else "no extraction tier produced content"
        if self._skipped:
            skipped = ", ".join(sorted(self._skipped))
            detail += f" [tiers unavailable here: {skipped}]"
        return UnifiedExtractResult(
            url=url, tools_tried=tools_tried, success=False, error=detail
        )

    async def extract(
        self,
        url: str,
        extract_tables: bool = True,
        extract_links: bool = True,
        force_js_render: bool = False,
        query: str = "",
    ) -> UnifiedExtractResult:
        """Extract content from a single URL, climbing the ladder cheapest-first.

        Args:
            url: URL to extract content from
            extract_tables: Whether to extract tables as structured data
            extract_links: Whether to extract all links
            force_js_render: Skip the non-JS tiers (curl_cffi/jina/http) and go
                straight to a rendering tier, for pages whose content only
                exists after script execution.
            query: Fix 2.2 — the question this URL was retrieved to answer.
                Supplying it caps the result at
                :attr:`content_budget_chars` **by relevance**; omitting it
                returns the full extraction untruncated.

        Returns:
            UnifiedExtractResult with the best extraction available. Never
            raises: a total failure is reported as ``success=False`` with an
            ``error`` that names each tier that was tried and each that was
            skipped.
        """
        tools_tried: list[str] = []
        errors: list[str] = []
        self._active_query = query or ""

        # OVERHAUL4 P7: URL/page-type aware entry. Media files fail fast;
        # PDFs get the dedicated PDF path first (text + tables), then fall
        # through to the general ladder if it fails; known JS-heavy hosts skip
        # the non-JS tiers so the first rendering tier carries them.
        profile = self._classify_url(url)
        if profile == "media":
            return self._failure(url, [], ["media file — no text to extract"])
        # OVERHAUL5 W4 (D-06): paywall/DOI hosts reject headless extraction;
        # fail fast with a typed reason instead of an 11-tier death march.
        if profile == "paywall":
            return self._failure(
                url,
                [],
                ["PAYWALL host — extraction rejected upstream; use wayback/abstract"],
            )
        if profile == "pdf":
            try:
                pdf_result = await self.extract_pdf(url)
                if pdf_result.success:
                    pdf_result.tool_used = pdf_result.tool_used or "pdf"
                    pdf_result.tools_tried = ["pdf"]
                    return pdf_result
                errors.append(f"pdf: {pdf_result.error or 'no content'}")
            except Exception as exc:  # noqa: BLE001 - PDF path must not kill the ladder
                logger.debug("pdf extraction failed for %s: %s", url, exc)
                errors.append(f"pdf: {type(exc).__name__}: {exc}")

        for tier in self._eligible_tiers(force_js_render, profile):
            if not self._tier_available(tier):
                continue
            extractor = getattr(self, f"_extract_{tier}", None)
            if extractor is None:  # pragma: no cover — guards a typo in TIER_ORDER
                errors.append(f"{tier}: no extractor implemented")
                logger.warning(
                    "extraction tier %r is declared in the ladder but has no "
                    "_extract_%s method — it can never run",
                    tier,
                    tier,
                )
                continue

            tools_tried.append(tier)
            result: UnifiedExtractResult | None = None
            # OVERHAUL4 P7: per-tier retries (TIER_MAX_ATTEMPTS). Only
            # transient failures (timeout/connection/5xx/429) are retried
            # with a short backoff; deterministic failures move on at once.
            for attempt in range(1, self.TIER_MAX_ATTEMPTS + 1):
                try:
                    result = cast(
                        "UnifiedExtractResult",
                        await extractor(
                            url,
                            extract_tables=extract_tables,
                            extract_links=extract_links,
                        ),
                    )
                except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                    logger.debug(
                        "extraction tier %s failed for %s (attempt %d/%d): %s",
                        tier, url, attempt, self.TIER_MAX_ATTEMPTS, exc,
                    )
                    if attempt < self.TIER_MAX_ATTEMPTS and self._is_transient_error(
                        f"{type(exc).__name__}: {exc}"
                    ):
                        await asyncio.sleep(0.4 * attempt)
                        continue
                    # OVERHAUL4 P7 FIX: a RAISING tier used to set
                    # ``result = None`` and fall through — the reason was
                    # silently dropped from the aggregated error because the
                    # append below only fires for ``result.error``. A raising
                    # tier must be reported, never shrugged off.
                    errors.append(f"{tier}: {type(exc).__name__}: {exc}")
                    result = None
                    break
                if result.success:
                    break
                if (
                    attempt < self.TIER_MAX_ATTEMPTS
                    and result.error
                    and self._is_transient_error(result.error)
                ):
                    logger.debug(
                        "extraction tier %s transient failure (%s) — retrying "
                        "%d/%d", tier, result.error[:60], attempt + 1,
                        self.TIER_MAX_ATTEMPTS,
                    )
                    await asyncio.sleep(0.5 * attempt)
                    continue
                break

            if result is not None and result.success:
                result.tools_tried = tools_tried
                result.tool_used = tier
                # OVERHAUL4 P7: media enrichment — a cheap tier (curl_cffi/
                # jina/http/firecrawl) returns TEXT but no media. When the
                # caller wants links, ask obscura for the rendered asset
                # graph (~1 fast local call; skipped when obscura is
                # unavailable or the tier already produced images). This is
                # what makes "a page containing media and infographics"
                # contribute its image URLs on EVERY profile, not just
                # js_heavy.
                if extract_links and not result.images and tier != "obscura":
                    try:
                        if self._tier_available("obscura"):
                            obscura = await self._get_obscura()
                            result.images = await obscura.fetch_assets(url)
                    except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
                        logger.debug("media enrichment failed for %s: %s", url, exc)
                return result
            if result is not None and result.error:
                errors.append(f"{tier}: {result.error}")

        return self._failure(url, tools_tried, errors)

    # ── Driver 2: batch, tier-major ─────────────────────────────────────────

    def _default_resolver(
        self,
        tier: str,
        semaphore: asyncio.Semaphore,
        *,
        extract_tables: bool,
        extract_links: bool,
    ) -> Any:
        """Resolve ``tier`` to a concurrency-bounded ``(url) -> result`` callable.

        The resolved callable owns its own bounding — see
        :meth:`extract_ladder`'s ``tier_resolver`` contract for why that
        ownership sits here rather than in the driver.
        """
        extractor = getattr(self, f"_extract_{tier}", None)
        if extractor is None:
            return None

        async def _call(url: str) -> UnifiedExtractResult:
            async with semaphore:
                return cast(
                    "UnifiedExtractResult",
                    await extractor(
                        url,
                        extract_tables=extract_tables,
                        extract_links=extract_links,
                    ),
                )

        return _call

    async def extract_ladder(
        self,
        urls: list[str],
        *,
        concurrency: int | None = None,
        extract_tables: bool = True,
        extract_links: bool = True,
        force_js_render: bool = False,
        tiers: tuple[str, ...] | list[str] | None = None,
        tier_resolver: Any | None = None,
        tier_available: Any | None = None,
        query: str = "",
    ) -> LadderOutcome:
        """Extract a batch of URLs **tier-major**: all URLs at tier N, then N+1.

        This is the batching strategy ``deep_search._extract_batch`` used to
        hand-roll, and it is the right one: climbing per-URL would launch a
        browser for URL A while URL B had not yet been attempted at the free
        tier. Once every URL has been extracted the climb stops, so a
        successful cheap tier never pays for an expensive one.

        Never raises. Per-URL exceptions are collected into ``errors`` keyed by
        tier, so an empty outcome always says why.

        Args:
            tiers: Per-call restriction of the ladder, on top of the
                instance-level one. Lets one shared extractor serve callers
                with different granted tool subsets (§4.7) without each of them
                constructing — and separately warming up — its own instance.
            tier_resolver: ``(tier, semaphore, *, extract_tables,
                extract_links) -> ((url) -> Awaitable[UnifiedExtractResult]) |
                None``. Overrides how a tier name becomes a callable. This is
                the seam that lets ``DeepSearchClient`` delegate to this one
                driver while keeping its own ``_extract_<tier>(semaphore, url)``
                methods as the substitution point its capability-gating tests
                monkeypatch — so there is a single ladder implementation
                *without* deleting the override point that makes tier behaviour
                testable without a network.

                A resolver's callable is responsible for its own concurrency
                bounding using the supplied semaphore. The driver deliberately
                does NOT wrap it: an `asyncio.Semaphore` is not reentrant, so a
                driver-side acquire around a resolver that also acquires would
                deadlock as soon as ``len(urls) > concurrency``.
            tier_available: ``(tier) -> bool`` override for the availability
                probe, so a delegating caller's own cached probe results (and
                its own tests' forced values) remain authoritative.
            query: Fix 2.2 — the question this batch was discovered for. One
                query per batch, because a batch *is* the URL set discovered
                for one question. Applies only to tiers resolved by this
                class's own tier table: a caller supplying ``tier_resolver``
                (i.e. ``DeepSearchClient``) fits its own budget inside its own
                extractors, and double-fitting would select from an
                already-selected excerpt.
        """
        outcome = LadderOutcome()
        self._active_query = query or ""
        self._selection_stats = {}
        if not urls:
            return outcome

        pending_order = list(dict.fromkeys(u for u in urls if u))
        if not pending_order:
            return outcome

        # F-0.1-8 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): shared per-engagement
        # fetch cache. Two concurrently running specialists scraping the same
        # competitor page used to fetch it twice (one network call each). The
        # cache is module-level (shared across UnifiedExtract instances) and
        # engagement-scoped (cleared per engagement via clear_fetch_cache), so
        # the same URL is fetched once per engagement. A cache hit skips the
        # whole tier climb and marks the result cached.
        cached_results: list[UnifiedExtractResult] = []
        remaining: list[str] = []
        for url in pending_order:
            hit = _FETCH_CACHE.get(url)
            if hit is not None:
                cached_results.append(hit)
            else:
                remaining.append(url)
        if cached_results:
            outcome.results.extend(cached_results)
            for _cached in cached_results:
                outcome.tools_used.append("fetch_cache")
            if not remaining:
                outcome.tools_tried.append("fetch_cache")
                outcome.tiers_unavailable = dict(self._skipped)
                return outcome
        pending_order = remaining

        extracted_urls: set[str] = set()
        semaphore = asyncio.Semaphore(concurrency or self.EXTRACTION_CONCURRENCY)
        is_available = tier_available or self._tier_available
        resolve = tier_resolver or self._default_resolver
        # OVERHAUL4 P7: all-js_heavy batches climb the capability-ordered
        # js_heavy ladder (obscura first); mixed/default batches keep the
        # cheap-first order (per-URL filtering still skips non-JS on
        # js_heavy URLs).
        _batch_profile = (
            "js_heavy"
            if pending_order and all(
                self._classify_url(u) == "js_heavy" for u in pending_order
            )
            else "default"
        )
        ladder = self._eligible_tiers(force_js_render, _batch_profile)
        if tiers:
            allowed = set(tiers)
            ladder = [t for t in ladder if t in allowed]

        # OVERHAUL4 P7: URL-aware pre-pass — media files fail fast (no text
        # to extract), paywall/DOI hosts fail fast with a typed reason
        # (D-06), and PDFs use the dedicated PDF path (text + tables); none
        # should waste a general tier on a binary blob or a paywall. PDF
        # failures stay in the ladder as a fallback.
        for url in list(pending_order):
            profile = self._classify_url(url)
            if profile == "media":
                outcome.results.append(UnifiedExtractResult(
                    url=url, success=False, error="media file — no text to extract"
                ))
                extracted_urls.add(url)
            elif profile == "paywall":
                outcome.results.append(UnifiedExtractResult(
                    url=url, success=False,
                    error="PAYWALL host — extraction rejected upstream; use wayback/abstract",
                ))
                extracted_urls.add(url)
            elif profile == "pdf":
                try:
                    pdf_result = await self.extract_pdf(url)
                    if pdf_result.success:
                        pdf_result.tool_used = pdf_result.tool_used or "pdf"
                        pdf_result.tools_tried = ["pdf"]
                        outcome.results.append(pdf_result)
                        extracted_urls.add(url)
                        if url and (pdf_result.markdown or pdf_result.content):
                            _FETCH_CACHE.setdefault(url, pdf_result)
                except Exception as exc:  # noqa: BLE001 - PDF path must not kill the batch
                    logger.debug("pdf extraction failed for %s: %s", url, exc)

        for tier in ladder:
            pending = [u for u in pending_order if u not in extracted_urls]
            if not pending:
                break  # everything already extracted — stop climbing the ladder
            if not is_available(tier):
                continue

            # OVERHAUL4 P7: URL-aware tier filtering — non-JS tiers never
            # waste a request on a known js_heavy URL (its HTML is an empty
            # shell); the first rendering tier carries it instead.
            if tier in self.NON_JS_TIERS:
                pending = [u for u in pending if self._classify_url(u) != "js_heavy"]
                if not pending:
                    continue

            extractor = resolve(
                tier,
                semaphore,
                extract_tables=extract_tables,
                extract_links=extract_links,
            )
            if extractor is None:  # pragma: no cover — guards a typo in TIER_ORDER
                outcome.errors[tier] = "no extractor implemented"
                continue

            outcome.tools_tried.append(tier)
            # OVERHAUL5 W4 (D-06): per-tier concurrency caps. The self-hosted
            # firecrawl stack runs ONE worker — firing 5 parallel scrapes at it
            # produced the run's 7,244x 'Can't accept connection due to
            # RAM/CPU load' rejections. Cap its wave so the worker is never
            # hammered; other tiers keep the batch semaphore.
            _tier_caps = {"firecrawl": 2}
            _cap = _tier_caps.get(tier)
            if _cap and len(pending) > _cap:
                _per_url: dict[str, Any] = {}
                for _start in range(0, len(pending), _cap):
                    _wave = pending[_start:_start + _cap]
                    _wave_out = await asyncio.gather(
                        *(extractor(u) for u in _wave), return_exceptions=True
                    )
                    _per_url.update(dict(zip(_wave, _wave_out, strict=False)))
                results = [_per_url[u] for u in pending]
            else:
                results = await asyncio.gather(
                    *(extractor(u) for u in pending), return_exceptions=True
                )
            by_url: dict[str, Any] = dict(zip(pending, results, strict=False))

            # OVERHAUL4 P7: per-tier transient retry rounds — timeout/
            # connection/5xx/429 failures on the SAME tier are retried
            # (TIER_MAX_ATTEMPTS total attempts) with a short backoff before
            # the ladder moves on; deterministic failures do not retry.
            for retry_round in range(1, self.TIER_MAX_ATTEMPTS):
                retryable = [
                    u for u in pending
                    if not _batch_result_ok(by_url[u])
                    and _batch_result_transient(by_url[u])
                ]
                if not retryable:
                    break
                logger.debug(
                    "extraction tier %s retrying %d transient URL(s) "
                    "(round %d/%d)", tier, len(retryable),
                    retry_round + 1, self.TIER_MAX_ATTEMPTS,
                )
                await asyncio.sleep(0.5 * retry_round)
                retried = await asyncio.gather(
                    *(extractor(u) for u in retryable), return_exceptions=True
                )
                for u, res in zip(retryable, retried, strict=False):
                    if _batch_result_ok(res):
                        by_url[u] = res

            produced = 0
            failures: list[str] = []
            for _url, result in by_url.items():
                if isinstance(result, BaseException):
                    logger.debug("extraction tier %s raised: %s", tier, result)
                    failures.append(f"{type(result).__name__}: {result}")
                    continue
                if result.success:
                    result.tool_used = tier
                    result.tools_tried = [tier]
                    outcome.results.append(result)
                    extracted_urls.add(result.url)
                    produced += 1
                    # F-0.1-8: cache the first successful fetch of this URL so a
                    # concurrent specialist scraping the same page skips the
                    # network call. Cache only successful, content-bearing
                    # results (a 404 shell must not be cached as success).
                    if result.url and (result.markdown or result.content):
                        _FETCH_CACHE.setdefault(result.url, result)
                elif result.error:
                    failures.append(result.error)

            if produced:
                outcome.tools_used.append(tier)
            elif failures:
                outcome.errors[tier] = failures[0]
            else:
                outcome.errors[tier] = f"no usable content from {len(pending)} URL(s)"

        outcome.tiers_unavailable = dict(self._skipped)
        return outcome

    async def extract_batch(
        self,
        urls: list[str],
        concurrency: int = 5,
        query: str = "",
    ) -> list[UnifiedExtractResult]:
        """Extract content from multiple URLs, one result per input URL.

        Delegates to :meth:`extract_ladder` (so there is exactly one batch
        implementation) and re-keys the outcome back to the caller's input
        order, filling misses with an explanatory failure result.
        """
        outcome = await self.extract_ladder(urls, concurrency=concurrency, query=query)
        by_url = {r.url: r for r in outcome.results}
        detail = "; ".join(f"{k}: {v}" for k, v in outcome.errors.items())
        if outcome.tiers_unavailable:
            detail += f" [tiers unavailable here: {', '.join(sorted(outcome.tiers_unavailable))}]"
        return [
            by_url.get(
                url,
                UnifiedExtractResult(
                    url=url,
                    tools_tried=list(outcome.tools_tried),
                    success=False,
                    error=detail or "no extraction tier produced content",
                ),
            )
            for url in urls
        ]

    async def extract_pdf(self, url: str) -> UnifiedExtractResult:
        """Extract text AND tables from a PDF file.

        Uses Crawl4AI's PDF extraction capability (PyMuPDF or PyPDF2) for the
        prose stream, then runs pdfplumber table extraction on the same
        document (fix 2.3, audit §6 Phase 2): the benchmark's quantitative
        evidence lives in tables, and a prose-only extract discards exactly
        the numbers ``chart_specs`` mines for exhibits.

        The table pass is strictly additive and failure-isolated: it can add
        ``tables`` + a rendered prose block to the result, it can never turn
        a successful text extraction into a failure.
        """
        tools_tried: list[str] = []

        try:
            crawl4ai = await self._get_crawl4ai()
            tools_tried.append("crawl4ai")
            pdf_result = await crawl4ai.crawl_pdf(url)

            if pdf_result.status_code == 200 and self._is_quality_content(pdf_result.content):
                tables: list[dict[str, Any]] = []
                content = pdf_result.content
                try:
                    from hyperion.tools.pdf_tables import (
                        extract_tables_from_bytes,
                        tables_to_prose,
                    )

                    # crawl_pdf already downloaded the document; reuse those
                    # bytes rather than fetching the PDF a second time.
                    pdf_bytes = pdf_result.pdf_bytes
                    if pdf_bytes:
                        extracted = extract_tables_from_bytes(pdf_bytes)
                        if extracted:
                            tables = [t.to_dict() for t in extracted]
                            prose = tables_to_prose(extracted)
                            if prose:
                                content = f"{content}\n\n{prose}"
                                tools_tried.append("pdfplumber")
                                logger.info(
                                    "pdfplumber recovered %d evidence table(s) from %s",
                                    len(extracted),
                                    url,
                                )
                except Exception as e:
                    # Never let the additive table pass sink the text extraction
                    # (fix 0.3 discipline: loud, never fatal).
                    logger.warning("PDF table extraction failed for %s: %s", url, e, exc_info=True)

                return UnifiedExtractResult(
                    url=url,
                    title=pdf_result.title,
                    content=content,
                    markdown=content,
                    tables=tables,
                    tool_used="crawl4ai-pdf",
                    tools_tried=tools_tried,
                    success=True,
                )
            elif pdf_result.error:
                return UnifiedExtractResult(
                    url=url,
                    tools_tried=tools_tried,
                    success=False,
                    error=pdf_result.error,
                )

        except (ConnectionError, RuntimeError, OSError) as e:
            return UnifiedExtractResult(
                url=url,
                tools_tried=tools_tried,
                success=False,
                error=str(e),
            )

        return UnifiedExtractResult(
            url=url,
            tools_tried=tools_tried,
            success=False,
            error="PDF extraction failed",
        )

    async def close(self) -> None:
        """Close all underlying clients."""
        for client in (
            self._jina,
            self._http_extract,
            self._obscura,
            self._crawl4ai,
            self._scrapling,
            self._flaresolverr,
            self._wayback,
            self._curl_cffi,
            self._nodriver,
            self._camoufox,
        ):
            if client is None:
                continue
            try:
                await client.close()
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
                # Fail loud but never let cleanup abort the caller: a leaf
                # client that cannot close must not prevent the other nine.
                logger.debug("closing %s failed: %s", type(client).__name__, e)

    async def __aenter__(self) -> UnifiedExtract:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
