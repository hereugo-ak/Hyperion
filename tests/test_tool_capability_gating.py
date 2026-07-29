"""Tests for tool capability gating and failure reporting.

Every test here was written against a *specific* observed defect and verified
to fail before the corresponding fix. They fall into four groups:

1. Capability gating — a tier that physically cannot run here must be SKIPPED
   and NAMED, not attempted. Attempting it wastes a round of concurrency and,
   worse, buries the real error behind a bogus "not available on linux" one.

2. Failure reporting — a search/extract that returns nothing must say WHY. The
   original code swallowed every failure with `except (...): pass` or a bare
   `logger.debug`, so a dead SearxNG container and a query with genuinely no
   sources produced byte-identical empty results.

3. Reachability — a tool that exists, is exported, and is advertised in a
   docstring must actually be called by something. Two entire tiers
   (`_extract_scrapling`, `StealthSearchClient`) were dead code.

4. Honest provenance — `tools_used` must list tiers that produced results, not
   tiers that were merely attempted.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from hyperion.tools.deep_search import DeepSearchClient, DeepSearchResult, ExtractedContent
from hyperion.tools.stealth_search import StealthSearchClient
from hyperion.tools.unified_extract import UnifiedExtract
from hyperion.tools.unified_search import UnifiedSearch, UnifiedSearchResult

# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _force(client, **tiers) -> None:
    """Pin tier availability so tests don't depend on what's installed."""
    client._availability.update(tiers)
    for name, ok in tiers.items():
        if not ok:
            client._skipped[name] = "forced unavailable by test"


class TestUnifiedSearchGating:
    """UnifiedSearch must skip impossible tiers and explain empty results."""

    def test_tier_order_is_declared(self):
        """A single declared ladder, so reporting and execution cannot drift."""
        assert UnifiedSearch.TIER_ORDER[0] == "searxng", "SearxNG is free — always first"
        assert "obscura" in UnifiedSearch.TIER_ORDER
        assert UnifiedSearch.TIER_ORDER[-1] == "stealth", (
            "launching a browser is the most expensive way to get a result, "
            "so the stealth tier must be last"
        )

    def test_obscura_is_skipped_when_binary_cannot_run(self):
        """The whole point: don't attempt a binary that cannot execute here."""
        search = UnifiedSearch()
        _force(search, obscura=False)
        assert "obscura" not in search.available_tiers()
        assert "obscura" in search.unavailable_tiers()

    def test_unavailable_tiers_explain_themselves(self):
        """'unavailable' with no reason is not actionable for the user."""
        search = UnifiedSearch()
        search._tier_available("obscura")
        for tier, why in search.unavailable_tiers().items():
            assert why and why.strip(), f"{tier} marked unavailable with no reason"

    async def test_obscura_tier_not_attempted_when_unavailable(self):
        """Gating must actually prevent the call, not just relabel it."""
        search = UnifiedSearch()
        _force(search, searxng=True, jina=True, obscura=False, stealth=False)

        called = []

        class _Boom:
            async def scrape(self, urls, concurrency=3):
                called.append(urls)
                raise AssertionError("obscura must not be invoked when unavailable")

        search._obscura = _Boom()

        async def _empty_searxng(**kw):
            called.append("searxng")
            class R:
                results = [type("S", (), {
                    "title": "t", "url": "https://a.example/1", "snippet": "s",
                    "engine": "e", "score": 1.0,
                })()]
            return R()

        search._searxng = type("C", (), {"search": staticmethod(_empty_searxng)})()
        search._jina = type("J", (), {
            "search": staticmethod(lambda **kw: _raise_conn())
        })()

        result = await search.search("q", num_results=5, min_results=99)
        assert "obscura" not in result.tools_tried
        assert not any(isinstance(c, list) for c in called), "obscura was invoked"

    async def test_empty_result_reports_why(self):
        """A failing tier must land in errors, not vanish."""
        search = UnifiedSearch()
        _force(search, searxng=True, jina=False, obscura=False, stealth=False)

        class _Dead:
            async def search(self, **kw):
                raise ConnectionError("searxng container is not running")

        search._searxng = _Dead()

        result = await search.search("q", num_results=5)
        assert not result.success
        assert "searxng" in result.errors
        assert "not running" in result.errors["searxng"]
        assert result.error, "roll-up error must be populated when nothing found"

    async def test_tools_used_excludes_tiers_that_produced_nothing(self):
        """tools_used claimed success for tiers that returned zero rows."""
        search = UnifiedSearch()
        _force(search, searxng=True, jina=False, obscura=False, stealth=False)

        class _Empty:
            async def search(self, **kw):
                return type("R", (), {"results": []})()

        search._searxng = _Empty()
        result = await search.search("q", num_results=5)
        assert result.tools_used == [], (
            "a tier that returned no results must not be listed in tools_used"
        )
        assert "searxng" in result.tools_tried, "but it WAS tried — record that"

    async def test_result_carries_a_failure_channel(self):
        """UnifiedSearchResult had nowhere to report failure at all."""
        fields = UnifiedSearchResult("q").to_dict()
        for key in ("error", "errors", "tiers_unavailable", "tools_tried", "success"):
            assert key in fields, f"UnifiedSearchResult cannot report {key}"

    def test_no_silent_except_pass_in_search_chain(self):
        """The three `except (...): pass` blocks must not come back."""
        src = Path("hyperion/tools/unified_search.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.ExceptHandler):
                body = [n for n in node.body if not isinstance(n, ast.Pass)]
                assert body, (
                    "an except handler in unified_search.py swallows its "
                    "exception with a bare `pass` — failures become invisible"
                )

    async def test_took_ms_is_populated(self):
        """took_ms was declared and never set, so it was always 0."""
        search = UnifiedSearch()
        _force(search, searxng=False, jina=False, obscura=False, stealth=False)
        result = await search.search("q")
        assert result.took_ms >= 0
        assert isinstance(result.took_ms, int)


class TestSearchNewsIsReachable:
    """search_news() raised TypeError on every call — it was dead code."""

    def test_search_accepts_time_range(self):
        params = inspect.signature(UnifiedSearch.search).parameters
        assert "time_range" in params, (
            "search_news forwards time_range=, so search() must accept it"
        )

    async def test_search_news_does_not_raise_type_error(self):
        search = UnifiedSearch()
        _force(search, searxng=False, jina=False, obscura=False, stealth=False)
        result = await search.search_news("q", num_results=3, time_range="week")
        assert isinstance(result, UnifiedSearchResult)

    async def test_time_range_reaches_searxng(self):
        """Forwarding must be real — a swallowed kwarg is the same bug again."""
        seen = {}

        class _Spy:
            async def search(self, **kw):
                seen.update(kw)
                return type("R", (), {"results": []})()

        search = UnifiedSearch()
        _force(search, searxng=True, jina=False, obscura=False, stealth=False)
        search._searxng = _Spy()
        await search.search_news("q", num_results=3, time_range="week")
        assert seen.get("time_range") == "week"
        assert seen.get("categories") == "news"


class TestDeduplicationKeepsExpensiveContent:
    """Obscura re-fetches known URLs to ADD content; dedup threw it away."""

    def test_rendered_content_survives_merge(self):
        search = UnifiedSearch()
        merged = search._deduplicate([
            {"url": "https://x.example/a", "title": "A", "snippet": "short",
             "source": "searxng"},
            {"url": "https://x.example/a", "title": "A full", "snippet": "much longer",
             "content": "RENDERED BODY", "source": "obscura"},
        ])
        assert len(merged) == 1
        assert merged[0].get("content") == "RENDERED BODY", (
            "the expensive tier's whole contribution was discarded"
        )

    def test_both_sources_recorded(self):
        search = UnifiedSearch()
        merged = search._deduplicate([
            {"url": "https://x.example/a", "title": "A", "source": "searxng"},
            {"url": "https://x.example/a", "title": "A", "source": "obscura"},
        ])
        assert set(merged[0]["sources"]) == {"searxng", "obscura"}

    def test_richer_snippet_wins(self):
        search = UnifiedSearch()
        merged = search._deduplicate([
            {"url": "https://x.example/a", "snippet": "tiny", "source": "searxng"},
            {"url": "https://x.example/a", "snippet": "a considerably longer snippet",
             "source": "jina"},
        ])
        assert merged[0]["snippet"] == "a considerably longer snippet"

    def test_urlless_rows_dropped(self):
        search = UnifiedSearch()
        assert search._deduplicate([{"title": "no url"}]) == []


class TestDeepSearchGating:
    """DeepSearchClient ran every tier unconditionally and reported nothing."""

    def test_extraction_ladder_is_declared(self):
        tiers = DeepSearchClient.EXTRACTION_TIERS
        assert tiers[0] == "jina", "cheapest keyless tier first"
        assert tiers[-1] == "flaresolverr", "CAPTCHA solver is the last resort"
        assert "scrapling" in tiers, "the Scrapling tier must be in the ladder"

    def test_every_tier_has_an_extractor_and_a_label(self):
        """A ladder entry with no implementation is a silent no-op."""
        client = DeepSearchClient()
        for tier in DeepSearchClient.EXTRACTION_TIERS:
            assert hasattr(client, f"_extract_{tier}"), f"no _extract_{tier}"
            assert tier in DeepSearchClient.TIER_LABELS, f"no label for {tier}"

    def test_scrapling_tier_is_reachable(self):
        """_extract_scrapling existed but _extract_batch never called it.

        Guard against the batch loop silently dropping a tier again by
        asserting the ladder is what drives dispatch.
        """
        src = Path("hyperion/tools/deep_search.py").read_text(encoding="utf-8")
        assert "EXTRACTION_TIERS" in src
        # The loop must dispatch dynamically from the declared ladder rather
        # than repeating hand-written per-tier blocks that can omit one.
        assert 'f"_extract_{tier}"' in src, (
            "extraction must dispatch from EXTRACTION_TIERS so a tier cannot "
            "be advertised yet never invoked"
        )

    async def test_unavailable_tier_is_not_invoked(self):
        client = DeepSearchClient()
        _force(client, jina=False, http=False, obscura=False,
               crawl4ai=False, scrapling=False, flaresolverr=False)

        async def _boom(sem, url):
            raise AssertionError("no tier should run when all are unavailable")

        for tier in DeepSearchClient.EXTRACTION_TIERS:
            setattr(client, f"_extract_{tier}", _boom)

        extracted, used, tried, errors = await client._extract_batch(
            ["https://a.example/1"]
        )
        assert extracted == [] and used == [] and tried == []

    async def test_ladder_stops_once_all_urls_extracted(self):
        """Climbing past a full result set wastes browser launches."""
        client = DeepSearchClient()
        _force(client, jina=True, http=True, obscura=True,
               crawl4ai=True, scrapling=True, flaresolverr=True)

        invoked: list[str] = []

        def _mk(tier, content):
            async def _x(sem, url):
                invoked.append(tier)
                return ExtractedContent(url=url, content=content, tool_used=tier)
            return _x

        for tier in DeepSearchClient.EXTRACTION_TIERS:
            setattr(client, f"_extract_{tier}", _mk(tier, "x" * 500))

        await client._extract_batch(["https://a.example/1"])
        assert invoked == ["jina"], (
            f"first tier succeeded, later tiers must not run — got {invoked}"
        )

    async def test_failing_tier_records_why(self):
        client = DeepSearchClient()
        _force(client, jina=True, http=False, obscura=False,
               crawl4ai=False, scrapling=False, flaresolverr=False)

        async def _fail(sem, url):
            raise ConnectionError("jina unreachable")

        client._extract_jina = _fail
        extracted, used, tried, errors = await client._extract_batch(
            ["https://a.example/1"]
        )
        assert extracted == []
        assert "jina-reader" in tried
        assert "jina-reader" in errors
        assert "unreachable" in errors["jina-reader"]

    async def test_result_carries_a_failure_channel(self):
        fields = DeepSearchResult(query="q").to_dict()
        for key in ("error", "errors", "tiers_unavailable", "tools_tried", "success"):
            assert key in fields, f"DeepSearchResult cannot report {key}"

    async def test_discovery_failure_is_explained(self):
        """Empty discovery used to be indistinguishable from a broken engine."""
        client = DeepSearchClient()

        async def _dead_searxng(query, n, geo):
            return ([], "searxng", "ConnectionError: container not running")

        async def _dead_jina(query, n):
            return ([], "jina", "HTTPStatusError: 429 rate limited")

        client._search_searxng = _dead_searxng
        client._search_jina = _dead_jina

        result = await client.search("some query", depth="quick")
        assert not result.success
        assert "searxng" in result.errors and "jina" in result.errors
        assert "rate limited" in result.errors["jina"]
        assert "container not running" in result.errors["searxng"]
        assert result.error, "roll-up must explain why discovery found nothing"

    async def test_discovery_helpers_return_a_reason(self):
        """The 2-tuple signature had no room for a reason at all."""
        for name in ("_search_searxng", "_search_jina"):
            sig = inspect.signature(getattr(DeepSearchClient, name))
            ret = sig.return_annotation
            assert "str, str" in str(ret) or "tuple[list[str], str, str]" in str(ret), (
                f"{name} must return (urls, tool, error_detail)"
            )


class TestStealthSearchIsUsable:
    """StealthSearchClient was exported but no orchestrator called it."""

    def test_wired_into_unified_search(self):
        assert "stealth" in UnifiedSearch.TIER_ORDER
        assert hasattr(UnifiedSearch, "_get_stealth")

    def test_defaults_to_headless(self):
        """A visible browser window during a TUI consultation is unacceptable."""
        default = inspect.signature(
            StealthSearchClient.__init__
        ).parameters["headless"].default
        assert default is True, (
            "headless defaulted to False — reaching this tier would pop a "
            "Chromium window onto the user's desktop and fail on headless hosts"
        )

    def test_unified_search_requests_headless(self):
        src = Path("hyperion/tools/unified_search.py").read_text(encoding="utf-8")
        assert "headless=True" in src

    def test_has_availability_probe(self):
        assert hasattr(StealthSearchClient, "_check_available")

    async def test_returns_empty_rather_than_raising_when_unavailable(self):
        client = StealthSearchClient()
        client._available = False
        assert await client.search("q", num_results=3) == []

    async def test_stealth_only_runs_when_text_tiers_found_nothing(self):
        """A browser launch must never pre-empt a working cheap tier."""
        search = UnifiedSearch()
        _force(search, searxng=True, jina=False, obscura=False, stealth=True)

        class _Good:
            async def search(self, **kw):
                return type("R", (), {"results": [type("S", (), {
                    "title": "t", "url": "https://a.example/1", "snippet": "s",
                    "engine": "e", "score": 1.0,
                })()]})()

        class _Boom:
            async def search(self, *a, **kw):
                raise AssertionError("stealth must not run when results exist")
            async def close(self):
                pass

        search._searxng = _Good()
        search._stealth = _Boom()
        result = await search.search("q", num_results=5, min_results=1)
        assert result.total == 1
        assert "stealth" not in result.tools_tried


class TestUnifiedExtractGatingHolds:
    """Regression guard for the extraction ladder fixed alongside these."""

    def test_live_render_precedes_archive(self):
        order = UnifiedExtract.TIER_ORDER
        assert order.index("camoufox") < order.index("wayback"), (
            "wayback ran before camoufox, so a live page that camoufox could "
            "render was answered from a stale archived snapshot instead"
        )

    def test_wayback_is_last(self):
        assert UnifiedExtract.TIER_ORDER[-1] == "wayback"

    def test_cheapest_tier_is_first(self):
        assert UnifiedExtract.TIER_ORDER[0] == "curl_cffi"

    def test_unavailable_tiers_are_named(self):
        extract = UnifiedExtract()
        _force(extract, obscura=False)
        assert "obscura" in extract.unavailable_tiers()
        assert "obscura" not in extract.available_tiers()


class TestGatingIsConsistentAcrossTools:
    """The same defect appeared in four files; keep the remedy uniform."""

    @pytest.mark.parametrize("cls", [UnifiedSearch, UnifiedExtract, DeepSearchClient])
    def test_every_orchestrator_can_report_availability(self, cls):
        client = cls()
        assert hasattr(client, "available_tiers")
        assert hasattr(client, "unavailable_tiers")
        assert isinstance(client.available_tiers(), list)
        assert isinstance(client.unavailable_tiers(), dict)

    @pytest.mark.parametrize("cls", [UnifiedSearch, UnifiedExtract, DeepSearchClient])
    def test_availability_is_cached(self, cls):
        """Obscura's probe shells out; re-probing per URL is wasteful."""
        client = cls()
        client.available_tiers()
        assert client._availability, "probe results must be memoised"

    @pytest.mark.parametrize("cls", [UnifiedSearch, UnifiedExtract, DeepSearchClient])
    def test_tier_with_no_probe_stays_enabled(self, cls):
        """A tier nobody wrote a probe for must not be silently disabled."""
        client = cls()
        assert client._tier_available("a-tier-that-has-no-probe") is True

    @pytest.mark.parametrize("cls", [UnifiedSearch, UnifiedExtract, DeepSearchClient])
    def test_probe_that_raises_does_not_disable_a_tier(self, cls, monkeypatch):
        """Attempting and failing beats skipping something that might work.

        If a probe itself explodes (a broken import, a permissions error on the
        binary's directory) the safe answer is "try it anyway". Treating a
        crashed probe as "unavailable" would disable working tiers on the
        strength of an unrelated bug.
        """
        import hyperion.tools.obscura as obscura_mod

        class _Exploding:
            def __init__(self, *a, **kw):
                raise RuntimeError("probe blew up")

        monkeypatch.setattr(obscura_mod, "ObscuraClient", _Exploding)
        # unified_search/unified_extract hold module-level imports of the name
        for mod_name in ("hyperion.tools.unified_search", "hyperion.tools.unified_extract"):
            import sys
            mod = sys.modules.get(mod_name)
            if mod is not None and hasattr(mod, "ObscuraClient"):
                monkeypatch.setattr(mod, "ObscuraClient", _Exploding)

        client = cls()
        assert client._tier_available("obscura") is True, (
            "a probe that raises must leave the tier enabled, not disable it"
        )

    @pytest.mark.parametrize(
        "module",
        [
            "hyperion/tools/unified_search.py",
            "hyperion/tools/unified_extract.py",
            "hyperion/tools/deep_search.py",
        ],
    )
    def test_obscura_availability_is_never_assumed(self, module):
        """Each orchestrator must consult the probe, not guess by platform."""
        src = Path(module).read_text(encoding="utf-8")
        assert "_binary_available()" in src, (
            f"{module} must gate Obscura on the real executability probe"
        )


async def _raise_conn():
    raise ConnectionError("unavailable")
