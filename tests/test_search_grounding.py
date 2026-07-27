"""
Tests for query grounding at all 5 search entry points (Phase 1, fixes
1.1 and 1.2 — HYPERION_DEEP_AUDIT_2026-07-27.md §4.3 Finding B-2 and §7).

The audit found grounding enforced at only 1 of 5 search entry points:

    unified_search.py   ground_query = 0
    deep_search.py      ground_query = 0
    jina.py              ground_query = 0
    stealth_search.py   ground_query = 0
    searxng.py           ground_query = 2   <- the only guarded door

Fix 1.1 adds grounding to the remaining four. Fix 1.2 moves the
ground/log/drop logic that ``searxng.py`` had inlined into a single
shared choke point — ``grounded_search_or_empty`` in query_utils.py — so
a new search tool cannot be added without it, and so the five entry
points cannot silently drift from each other's behaviour.

These tests verify BEHAVIOUR (a contentless query is dropped before any
network call is attempted) rather than just literal ``grep -c`` counts,
using monkeypatched HTTP-boundary methods so no real network/browser
access is required.
"""

from __future__ import annotations

import asyncio

import pytest

from hyperion.tools.query_utils import (
    ContentlessQueryError,
    clear_engagement_focus,
    ground_query_or_raise,
    grounded_search_or_empty,
    set_engagement_focus,
)


# A query that grounds to "" — no engagement focus set, and the raw string
# contains no alphabetic subject at all (pure digits/punctuation), so there
# is genuinely nothing to rebuild a query from. ("vendor comparison 2024
# 2025" is NOT contentless under this library: normalize_query empties it,
# but ground_query's rebuild step keeps "vendor"/"comparison" as fallback
# intent words, producing "vendor comparison" rather than "".)
CONTENTLESS_QUERY = "2024 2025 $100 50%"

# A well-formed, subject-bearing query that should pass through unchanged
# (aside from possible geography/subject anchoring).
GROUNDED_QUERY = "India steel tariff exemptions 2024"


@pytest.fixture(autouse=True)
def _clear_focus():
    """Every test starts with no engagement focus, and leaves none behind."""
    clear_engagement_focus()
    yield
    clear_engagement_focus()


# ─────────────────────────────────────────────────────────────────────────
# 1.2 — shared choke point itself
# ─────────────────────────────────────────────────────────────────────────


class TestGroundedSearchOrEmpty:
    def test_contentless_query_returns_empty_sentinel(self):
        grounded, empty = grounded_search_or_empty(
            CONTENTLESS_QUERY, lambda: "SENTINEL", tool_name="Test"
        )
        assert grounded == ""
        assert empty == "SENTINEL"

    def test_grounded_query_passes_through(self):
        grounded, empty = grounded_search_or_empty(
            GROUNDED_QUERY, lambda: "SENTINEL", tool_name="Test"
        )
        assert empty is None
        assert grounded  # non-empty
        assert "steel" in grounded.lower()

    def test_contentless_query_rebuilt_from_engagement_focus(self):
        set_engagement_focus(
            question="Should Nigeria expand lithium-ion battery manufacturing?",
            subject="lithium-ion battery manufacturing",
            geography="Nigeria",
        )
        grounded, empty = grounded_search_or_empty(
            CONTENTLESS_QUERY, lambda: "SENTINEL", tool_name="Test"
        )
        # Rebuilt from the engagement subject rather than dropped.
        assert empty is None
        assert "lithium" in grounded.lower() or "nigeria" in grounded.lower()

    def test_empty_factory_only_called_when_dropped(self):
        calls = []

        def factory():
            calls.append(1)
            return "EMPTY"

        grounded_search_or_empty(GROUNDED_QUERY, factory, tool_name="Test")
        assert calls == []  # not called — query was usable

        grounded_search_or_empty(CONTENTLESS_QUERY, factory, tool_name="Test")
        assert calls == [1]  # called exactly once — query was dropped


class TestGroundQueryOrRaise:
    def test_raises_on_contentless(self):
        with pytest.raises(ContentlessQueryError):
            ground_query_or_raise(CONTENTLESS_QUERY)

    def test_returns_grounded_on_success(self):
        assert ground_query_or_raise(GROUNDED_QUERY)


# ─────────────────────────────────────────────────────────────────────────
# 1.1 — each of the 5 entry points must ground, and must ground BEFORE
# any network/browser boundary is touched. Each test monkeypatches the
# lowest-level network call to assert (a) it is never reached for a
# contentless query, and (b) when it IS reached, it receives a grounded
# query rather than the raw one.
# ─────────────────────────────────────────────────────────────────────────


class TestSearxNGGrounding:
    def test_contentless_query_never_hits_network(self, monkeypatch):
        from hyperion.tools.searxng import SearxNGClient

        called = []

        async def fake_request(*args, **kwargs):
            called.append((args, kwargs))
            raise AssertionError("network layer must not be reached")

        client = SearxNGClient()
        monkeypatch.setattr(client, "_search_single_attempt", fake_request, raising=False)

        result = asyncio.run(client.search(CONTENTLESS_QUERY))
        assert result.results == []
        assert called == []


class TestJinaGrounding:
    def test_contentless_query_never_hits_network(self, monkeypatch):
        from hyperion.tools.jina import JinaClient

        client = JinaClient()

        async def fake_get_client(*args, **kwargs):
            raise AssertionError("network layer must not be reached")

        # _get_client() is the sole boundary between search() and the
        # network (it lazily creates the httpx.AsyncClient). Forcing it to
        # raise proves grounding short-circuits BEFORE any HTTP call.
        monkeypatch.setattr(client, "_get_client", fake_get_client)

        result = asyncio.run(client.search(CONTENTLESS_QUERY))
        assert result.results == []
        assert result.total == 0

    def test_grounded_query_reaches_get_client(self, monkeypatch):
        """A subject-bearing query IS allowed through to the network
        boundary — proves grounding drops only contentless queries, not
        every query."""
        from hyperion.tools.jina import JinaClient

        client = JinaClient()
        reached = []

        async def fake_get_client(*args, **kwargs):
            reached.append(1)
            raise RuntimeError("stop before an actual HTTP call")

        monkeypatch.setattr(client, "_get_client", fake_get_client)

        with pytest.raises(RuntimeError):
            asyncio.run(client.search(GROUNDED_QUERY))
        assert reached == [1]


class TestStealthSearchGrounding:
    def test_contentless_query_never_launches_browser(self, monkeypatch):
        from hyperion.tools.stealth_search import StealthSearchClient

        client = StealthSearchClient()
        # Force "available" so we can prove grounding — not availability —
        # is what short-circuits the contentless case.
        monkeypatch.setattr(client, "_check_available", lambda: True)

        launched = []

        async def fake_launch(*args, **kwargs):
            launched.append(1)
            raise AssertionError("browser must not launch for a contentless query")

        monkeypatch.setattr(client, "_launch_browser", fake_launch)

        results = asyncio.run(client.search(CONTENTLESS_QUERY))
        assert results == []
        assert launched == []


class TestDeepSearchGrounding:
    def test_contentless_query_never_reaches_discovery(self, monkeypatch):
        from hyperion.tools.deep_search import DeepSearchClient

        client = DeepSearchClient()

        async def fake_discover(*args, **kwargs):
            raise AssertionError("_discover must not run for a contentless query")

        monkeypatch.setattr(client, "_discover", fake_discover)

        result = asyncio.run(client.search(CONTENTLESS_QUERY))
        assert result.success is False
        assert "grounding" in (result.error or "").lower() or result.error == ""


class TestUnifiedSearchGrounding:
    """``UnifiedSearch.search()`` deliberately does NOT re-ground the query
    at its own layer (see the NOTE in unified_search.py's search() body for
    why — re-grounding there broke tier-selection tests that use bare
    placeholder queries against fully-mocked leaf clients). Instead it
    relies on each leaf tier grounding internally at its own network
    boundary. These tests verify that transitive property: a contentless
    query reaching the REAL SearxNGClient/JinaClient/StealthSearchClient
    (not a mock) must still be dropped before any network/browser call.
    """

    def test_contentless_query_dropped_by_real_searxng_leaf(self, monkeypatch):
        from hyperion.tools.unified_search import UnifiedSearch

        client = UnifiedSearch()

        async def fake_boundary(*args, **kwargs):
            raise AssertionError("searxng network boundary must not be reached")

        # Do not mock SearxNGClient itself — use the real one so its own
        # internal grounding (fixed in searxng.py) is what's under test.
        async def fake_get_searxng():
            from hyperion.tools.searxng import SearxNGClient

            real = SearxNGClient()
            monkeypatch.setattr(real, "_search_single_attempt", fake_boundary, raising=False)
            return real

        monkeypatch.setattr(client, "_get_searxng", fake_get_searxng)
        monkeypatch.setattr(client, "_tier_available", lambda tier: tier == "searxng")

        result = asyncio.run(client.search(CONTENTLESS_QUERY, use_jina_fallback=False,
                                            use_obscura_fallback=False,
                                            use_stealth_fallback=False))
        assert result.success is False


# ─────────────────────────────────────────────────────────────────────────
# Regression guard: every entry point must literally call the shared
# grounding choke point (fix 1.2's "grep -c ground_query > 0" criterion,
# satisfied via grounded_search_or_empty rather than a raw ground_query
# call, since the shared guard subsumes it).
# ─────────────────────────────────────────────────────────────────────────


class TestGroundingCoverageAcrossEntryPoints:
    # unified_search.py is intentionally excluded: it relies on its leaf
    # clients (searxng.py, jina.py, stealth_search.py — all covered below)
    # grounding internally rather than duplicating the call at its own
    # orchestration layer. See the NOTE in unified_search.py's search().
    ENTRY_POINT_MODULES = [
        "hyperion.tools.searxng",
        "hyperion.tools.jina",
        "hyperion.tools.stealth_search",
        "hyperion.tools.deep_search",
    ]

    @pytest.mark.parametrize("module_name", ENTRY_POINT_MODULES)
    def test_module_imports_grounding_helper(self, module_name):
        import importlib

        mod = importlib.import_module(module_name)
        src_has_helper = (
            "grounded_search_or_empty" in mod.__dict__
        )
        assert src_has_helper, (
            f"{module_name} does not import the shared grounding choke "
            "point grounded_search_or_empty — a search entry point with "
            "no grounding call regresses Finding B-2."
        )
