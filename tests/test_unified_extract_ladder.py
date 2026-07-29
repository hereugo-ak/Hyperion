"""Tests for fix 2.1 — the single extraction ladder.

HYPERION_DEEP_AUDIT_2026-07-27.md §4.5 Finding B-4 / §6 Phase 2 item 2.1:
"Delete two of the three extraction ladders. Make ``UnifiedExtract`` the single
implementation and wire ``sub_agent`` + ``deep_search`` to it."

Before this fix the codebase held **three** independent implementations of the
same climb, and the best-engineered one had zero callers. These tests are
organised around the four properties that collapsing them was supposed to buy,
because "the code now lives in one file" is not by itself a fix — what matters
is that behaviour is now uniform, that the consumers actually reach it, and that
the tiers each ladder used to own exclusively are now reachable from all of them.

  1. There is ONE ladder, and it is a table, not unrolled code.
  2. It covers the UNION of the three former ladders' tiers.
  3. Both consumers DELEGATE to it rather than reimplementing it.
  4. Delegating did not weaken any pre-existing guarantee (capability gating,
     honest provenance, tool-subset restriction, cheap-first ordering).

Doubles are installed at each tier's own boundary — the ``_extract_<tier>``
method — because that is where a real implementation would call the network or
launch a browser. No test here touches either.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from hyperion.agents.sub_agent import SubAgentRunner
from hyperion.config import ModelTier
from hyperion.schemas.agents import AgentName, SubAgentSpec, ToolName
from hyperion.tools.deep_search import DeepSearchClient, ExtractedContent
from hyperion.tools.unified_extract import (
    LadderOutcome,
    UnifiedExtract,
    UnifiedExtractResult,
)

QUALITY = "x" * 500  # comfortably over MIN_CONTENT_LENGTH


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _all_available(client) -> None:
    """Pin every tier available so tests don't depend on what's installed."""
    for tier in client.tier_order:
        client._availability[tier] = True


def _stub_tiers(ex: UnifiedExtract, *, succeed: set[str] | None = None, log: list | None = None):
    """Replace every tier method with a double, recording invocation order."""
    succeed = succeed if succeed is not None else set()

    def mk(tier: str):
        async def _f(url, *, extract_tables=True, extract_links=True):
            if log is not None:
                log.append((tier, url))
            ok = tier in succeed
            return ex._finish(
                url,
                tier,
                primary=QUALITY if ok else "",
                title=f"{tier} title",
                error="" if ok else f"{tier} produced nothing",
            )

        return _f

    for tier in UnifiedExtract.TIER_ORDER:
        setattr(ex, f"_extract_{tier}", mk(tier))


def _sub_agent(tools: list[ToolName], question: str = "Nigeria battery market size "
    "2025") -> SubAgentRunner:
    spec = SubAgentSpec(
        question=question,
        parent_agent=AgentName.MARKET_ANALYST,
        model_tier=ModelTier.MICRO,
        tools=tools,
        findings_model="KeyFinding",
    )
    return SubAgentRunner(spec=spec, bus=MagicMock(), router=MagicMock())


# ─────────────────────────────────────────────────────────────────────
# 1. There is ONE ladder, and it is a table
# ─────────────────────────────────────────────────────────────────────


class TestLadderIsSingleAndTableDriven:
    def test_every_declared_tier_has_an_implementation(self):
        """A tier advertised in the ladder but not implemented is a silent no-op.

        This is exactly how ``deep_search._extract_scrapling`` stayed dead code
        while being named in the class docstring: the ladder listed it, nothing
        dispatched to it, and no test noticed.
        """
        ex = UnifiedExtract()
        for tier in UnifiedExtract.TIER_ORDER:
            assert hasattr(ex, f"_extract_{tier}"), f"no _extract_{tier}"

    def test_no_orphan_tier_implementations(self):
        """The reverse gap: an implemented tier absent from the ladder never runs."""
        implemented = {
            name[len("_extract_") :]
            for name, _ in inspect.getmembers(UnifiedExtract, inspect.isfunction)
            if name.startswith("_extract_")
        }
        orphans = implemented - set(UnifiedExtract.TIER_ORDER)
        assert not orphans, (
            f"_extract_{orphans} implemented but not in TIER_ORDER — unreachable, "
            "which is precisely the defect that left _extract_scrapling dead"
        )

    def test_dispatch_is_dynamic_not_unrolled(self):
        """The climb must read the ladder, not repeat hand-written tier blocks.

        ``sub_agent`` used to unroll five tiers as five copy-pasted loops. Each
        copy then drifted — different URL budgets, different quality checks, no
        capability gating in one of them. A dynamic dispatch makes drift
        impossible by construction.
        """
        src = Path("hyperion/tools/unified_extract.py").read_text(encoding="utf-8")
        assert 'f"_extract_{tier}"' in src, (
            "extraction must dispatch from the declared ladder so a tier cannot "
            "be advertised yet never invoked"
        )

    def test_quality_gate_is_applied_in_exactly_one_place(self):
        """Each of the three ladders had its own gate, against different fields.

        One of them checked ``markdown``, another ``content``, a third the
        stripped HTML — so identical pages passed in one path and failed in
        another. ``_finish`` is now the sole gate.
        """
        src = Path("hyperion/tools/unified_extract.py").read_text(encoding="utf-8")
        tree = ast.parse(src)
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_is_quality_content"
        ]
        # `_finish` plus `extract_pdf` (a separate entry point with its own
        # non-ladder contract). Anything beyond that means a tier re-gated.
        assert len(calls) <= 2, (
            f"{len(calls)} call sites for the quality gate — tiers must not "
            "re-implement it, that is how the three ladders diverged"
        )


# ─────────────────────────────────────────────────────────────────────
# 2. The ladder covers the UNION of the three former ladders
# ─────────────────────────────────────────────────────────────────────


class TestLadderCoversTheUnion:
    @pytest.mark.parametrize(
        "tier,formerly_only_in",
        [
            ("curl_cffi", "UnifiedExtract"),
            ("nodriver", "UnifiedExtract"),
            ("camoufox", "UnifiedExtract"),
            ("wayback", "UnifiedExtract"),
            ("http", "deep_search"),
            ("flaresolverr", "deep_search + sub_agent"),
            ("scrapling", "deep_search (dead) + sub_agent"),
            ("jina", "all three"),
            ("obscura", "all three"),
            ("crawl4ai", "all three"),
        ],
    )
    def test_tier_is_present(self, tier, formerly_only_in):
        """Every tier any of the three ladders had must survive the collapse.

        Collapsing three ladders into their *intersection* would be a
        regression dressed as a cleanup: it would silently delete working
        retrieval capability. The union is the only defensible target.
        """
        assert tier in UnifiedExtract.TIER_ORDER, (
            f"{tier} existed in {formerly_only_in} and would be lost"
        )

    def test_cheapest_tier_is_first(self):
        assert UnifiedExtract.TIER_ORDER[0] == "curl_cffi"

    def test_keyless_browserless_tiers_precede_every_browser_tier(self):
        """The free tiers must all run before anything launches a browser."""
        order = UnifiedExtract.TIER_ORDER
        cheapest = max(order.index(t) for t in ("curl_cffi", "jina", "http"))
        browsers = min(order.index(t) for t in ("obscura", "nodriver", "crawl4ai", "camoufox"))
        assert cheapest < browsers, (
            "a browser tier is ordered ahead of a keyless HTTP tier — the whole "
            "point of the ladder is that it is cheap-first"
        )

    def test_wayback_is_last(self):
        assert UnifiedExtract.TIER_ORDER[-1] == "wayback"

    def test_live_render_precedes_archive(self):
        order = UnifiedExtract.TIER_ORDER
        assert order.index("camoufox") < order.index("wayback"), (
            "wayback ran before camoufox, so a live page that camoufox could "
            "render was answered from a stale archived snapshot instead"
        )

    def test_captcha_solving_precedes_archive(self):
        """Solving a CAPTCHA gets today's page; an archive does not.

        Same class of error as the camoufox/wayback inversion: for a system
        whose output is dated market analysis, preferring an archived snapshot
        over a live fetch that would have worked is a data-freshness bug.
        """
        order = UnifiedExtract.TIER_ORDER
        assert order.index("flaresolverr") < order.index("wayback")


# ─────────────────────────────────────────────────────────────────────
# 3. Ladder behaviour
# ─────────────────────────────────────────────────────────────────────


class TestSingleUrlClimb:
    async def test_stops_at_first_success(self):
        ex = UnifiedExtract()
        _all_available(ex)
        log: list = []
        _stub_tiers(ex, succeed={"jina", "obscura"}, log=log)

        result = await ex.extract("https://a.example/1")
        assert result.success
        assert result.tool_used == "jina"
        assert [t for t, _ in log] == ["curl_cffi", "jina"], (
            "climbing past a success wastes the more expensive tiers"
        )

    async def test_unavailable_tier_is_not_invoked(self):
        ex = UnifiedExtract()
        _all_available(ex)
        ex._availability["curl_cffi"] = False
        ex._skipped["curl_cffi"] = "forced unavailable by test"
        log: list = []
        _stub_tiers(ex, succeed={"jina"}, log=log)

        await ex.extract("https://a.example/1")
        assert "curl_cffi" not in [t for t, _ in log]

    async def test_total_failure_names_tried_and_skipped_separately(self):
        """"no content extracted" behind four "not installed" hides the cause."""
        ex = UnifiedExtract()
        _all_available(ex)
        ex._availability["camoufox"] = False
        ex._skipped["camoufox"] = "camoufox not installed"
        _stub_tiers(ex, succeed=set())

        result = await ex.extract("https://a.example/1")
        assert not result.success
        assert "jina" in result.tools_tried
        assert "camoufox" not in result.tools_tried, "a skipped tier was never tried"
        assert "tiers unavailable here" in result.error
        assert "camoufox" in result.error

    async def test_a_raising_tier_does_not_abort_the_climb(self):
        """One broken tier must not cost the whole extraction."""
        ex = UnifiedExtract()
        _all_available(ex)
        _stub_tiers(ex, succeed={"obscura"})

        async def _boom(url, *, extract_tables=True, extract_links=True):
            raise ConnectionError("jina unreachable")

        ex._extract_jina = _boom
        result = await ex.extract("https://a.example/1")
        assert result.success and result.tool_used == "obscura"

    async def test_raising_tier_reason_is_reported_when_all_fail(self):
        ex = UnifiedExtract()
        _all_available(ex)
        _stub_tiers(ex, succeed=set())

        async def _boom(url, *, extract_tables=True, extract_links=True):
            raise ConnectionError("jina unreachable")

        ex._extract_jina = _boom
        result = await ex.extract("https://a.example/1")
        assert "unreachable" in result.error

    async def test_force_js_render_skips_the_non_js_tiers(self):
        """A page whose content only exists after script execution.

        Attempting curl_cffi/jina/http there can only return a shell of the
        document — and worse, a *quality-passing* shell, which is silently wrong
        rather than loudly empty.
        """
        ex = UnifiedExtract()
        _all_available(ex)
        log: list = []
        _stub_tiers(ex, succeed=set(UnifiedExtract.TIER_ORDER), log=log)

        result = await ex.extract("https://a.example/1", force_js_render=True)
        tried = [t for t, _ in log]
        assert not (set(tried) & set(UnifiedExtract.NON_JS_TIERS))
        assert result.tool_used == "obscura"

    async def test_never_raises_when_every_tier_explodes(self):
        """The audit's P0 was a silent total outage in the query layer.

        The extraction layer must not be able to reproduce it by propagating.
        """
        ex = UnifiedExtract()
        _all_available(ex)

        async def _boom(url, *, extract_tables=True, extract_links=True):
            raise RuntimeError("everything is broken")

        for tier in UnifiedExtract.TIER_ORDER:
            setattr(ex, f"_extract_{tier}", _boom)

        result = await ex.extract("https://a.example/1")
        assert not result.success and result.error


class TestTierMajorBatchClimb:
    async def test_all_urls_at_tier_n_before_any_at_tier_n_plus_1(self):
        """The property that makes batching correct.

        URL-major climbing would launch a browser for URL A while URL B had not
        yet been attempted at the free tier — paying the most expensive tier's
        cost before exhausting the cheapest one.
        """
        ex = UnifiedExtract()
        _all_available(ex)
        log: list = []
        _stub_tiers(ex, succeed={"obscura"}, log=log)

        await ex.extract_ladder(["u1", "u2", "u3"])
        tiers_in_order = [t for t, _ in log]
        # Each tier's three URLs must form one contiguous run.
        runs = [t for i, t in enumerate(tiers_in_order) if i == 0 or tiers_in_order[i - 1] != t]
        assert len(runs) == len(set(runs)), (
            f"a tier was revisited — climb was not tier-major: {tiers_in_order}"
        )

    async def test_climb_stops_once_every_url_is_extracted(self):
        """Costly, and — less obviously — it corrupts provenance if it doesn't.

        Checking only that no *extractor* ran is not enough: with the early
        break removed, ``pending`` is empty so nothing is invoked, yet the loop
        still appends every remaining tier to ``tools_tried`` and records
        ``"no usable content from 0 URL(s)"`` against each. A caller then sees
        all ten tiers as attempted and nine spurious failures for a batch that
        actually succeeded outright at the first tier. Verified by mutation: with
        ``if not pending: break`` disabled, the invocation-log assertion alone
        still passed.
        """
        ex = UnifiedExtract()
        _all_available(ex)
        log: list = []
        _stub_tiers(ex, succeed=set(UnifiedExtract.TIER_ORDER), log=log)

        outcome = await ex.extract_ladder(["u1", "u2"])
        assert len(outcome.results) == 2
        assert [t for t, _ in log] == ["curl_cffi", "curl_cffi"]
        assert outcome.tools_tried == ["curl_cffi"], (
            f"tiers that never ran are reported as tried: {outcome.tools_tried}"
        )
        assert outcome.errors == {}, (
            f"tiers that never ran recorded failures: {outcome.errors}"
        )

    async def test_no_tier_is_recorded_against_an_empty_pending_set(self):
        """Belt-and-braces on the same defect, phrased as an invariant.

        No tier may ever report a result — success or failure — for zero URLs.
        """
        ex = UnifiedExtract()
        _all_available(ex)
        _stub_tiers(ex, succeed={"curl_cffi"})

        outcome = await ex.extract_ladder(["u1"])
        assert "0 URL(s)" not in " ".join(outcome.errors.values())

    async def test_partial_success_only_retries_the_misses(self):
        """A URL already extracted must not be re-fetched by a costlier tier."""
        ex = UnifiedExtract()
        _all_available(ex)
        log: list = []

        def mk(tier, ok_urls):
            async def _f(url, *, extract_tables=True, extract_links=True):
                log.append((tier, url))
                ok = url in ok_urls
                return ex._finish(url, tier, primary=QUALITY if ok else "", error="" if ok else "miss")

            return _f

        ex._extract_curl_cffi = mk("curl_cffi", {"u1"})
        ex._extract_jina = mk("jina", {"u1", "u2"})
        for tier in UnifiedExtract.TIER_ORDER[2:]:
            setattr(ex, f"_extract_{tier}", mk(tier, set()))

        await ex.extract_ladder(["u1", "u2"])
        assert ("jina", "u1") not in log, "u1 succeeded at curl_cffi and was re-fetched"
        assert ("jina", "u2") in log

    async def test_tools_used_excludes_tiers_that_produced_nothing(self):
        """``tools_used`` is provenance, not a log of attempts."""
        ex = UnifiedExtract()
        _all_available(ex)
        _stub_tiers(ex, succeed={"obscura"})

        outcome = await ex.extract_ladder(["u1"])
        assert outcome.tools_used == ["obscura"]
        assert "curl_cffi" in outcome.tools_tried
        assert "curl_cffi" not in outcome.tools_used

    async def test_fruitless_tier_records_why(self):
        ex = UnifiedExtract()
        _all_available(ex)
        _stub_tiers(ex, succeed={"obscura"})

        outcome = await ex.extract_ladder(["u1"])
        assert "curl_cffi" in outcome.errors
        assert "produced nothing" in outcome.errors["curl_cffi"]

    @pytest.mark.parametrize("concurrency", [1, 2, 5])
    async def test_batch_larger_than_concurrency_does_not_deadlock(self, concurrency):
        """Guards the semaphore-reentrancy hazard in the resolver seam.

        ``asyncio.Semaphore`` is not reentrant. The resolved per-tier callable
        acquires it, so if the driver *also* acquired it around that callable,
        each URL would need two permits and the batch would hang.

        ``concurrency=1`` is the case that matters and is why this is
        parametrised. Verified by mutation: with a double-acquire introduced in
        the driver, ``concurrency=2`` and ``concurrency=5`` both still PASSED —
        with two or more permits a single task can hold both and make progress,
        so the batch merely serialises instead of hanging. Only a one-permit
        semaphore turns the bug into a deterministic deadlock. A test that
        picked a comfortable concurrency would have been silently vacuous.
        """
        ex = UnifiedExtract()
        _all_available(ex)
        _stub_tiers(ex, succeed={"curl_cffi"})

        urls = [f"u{i}" for i in range(12)]
        outcome = await asyncio.wait_for(
            ex.extract_ladder(urls, concurrency=concurrency), timeout=10
        )
        assert len(outcome.results) == 12

    async def test_resolved_callable_owns_its_own_bounding(self):
        """The contract that makes the no-double-acquire rule checkable.

        Stated explicitly because it is the kind of invariant a future editor
        would "tidy up" by hoisting the acquire into the driver — which is
        precisely the deadlock above.
        """
        ex = UnifiedExtract()
        sem = asyncio.Semaphore(1)
        _stub_tiers(ex, succeed={"jina"})
        call = ex._default_resolver("jina", sem, extract_tables=True, extract_links=True)

        assert sem.locked() is False
        result = await asyncio.wait_for(call("u1"), timeout=5)
        assert result.success
        assert sem.locked() is False, "the resolver leaked a permit"

    async def test_empty_and_blank_urls_are_a_no_op(self):
        ex = UnifiedExtract()
        _stub_tiers(ex, succeed=set(UnifiedExtract.TIER_ORDER))
        assert (await ex.extract_ladder([])).results == []
        assert (await ex.extract_ladder(["", ""])).results == []

    async def test_duplicate_urls_are_fetched_once(self):
        ex = UnifiedExtract()
        _all_available(ex)
        log: list = []
        _stub_tiers(ex, succeed={"curl_cffi"}, log=log)

        await ex.extract_ladder(["u1", "u1", "u1"])
        assert len(log) == 1

    async def test_extract_batch_returns_one_result_per_input_url(self):
        """Including misses — the caller indexes by position."""
        ex = UnifiedExtract()
        _all_available(ex)

        async def _only_u1(url, *, extract_tables=True, extract_links=True):
            ok = url == "u1"
            return ex._finish(url, "curl_cffi", primary=QUALITY if ok else "", error="" if ok else "miss")

        _stub_tiers(ex, succeed=set())
        ex._extract_curl_cffi = _only_u1

        results = await ex.extract_batch(["u1", "u2"])
        assert [r.url for r in results] == ["u1", "u2"]
        assert results[0].success and not results[1].success
        assert results[1].error, "a miss must explain itself"


class TestTierRestriction:
    def test_unknown_tier_is_rejected_not_silently_accepted(self):
        ex = UnifiedExtract(tiers=("jina", "typoo"))
        assert ex.tier_order == ("jina",)

    def test_restriction_preserves_cheap_first_order(self):
        """A caller must not be able to promote a browser tier ahead of a free one."""
        ex = UnifiedExtract(tiers=("camoufox", "jina", "obscura"))
        assert ex.tier_order == ("jina", "obscura", "camoufox")

    def test_all_unknown_tiers_falls_back_to_full_ladder(self):
        """Extracting nothing at all is the worse failure mode."""
        ex = UnifiedExtract(tiers=("nope", "alsonope"))
        assert ex.tier_order == UnifiedExtract.TIER_ORDER

    def test_none_means_full_ladder(self):
        assert UnifiedExtract(tiers=None).tier_order == UnifiedExtract.TIER_ORDER

    async def test_per_call_restriction_narrows_the_climb(self):
        ex = UnifiedExtract()
        _all_available(ex)
        log: list = []
        _stub_tiers(ex, succeed=set(), log=log)

        await ex.extract_ladder(["u1"], tiers=("jina", "http"))
        assert sorted({t for t, _ in log}) == ["http", "jina"]

    def test_availability_reporting_follows_the_restricted_ladder(self):
        ex = UnifiedExtract(tiers=("jina", "http"))
        assert set(ex.available_tiers()) <= {"jina", "http"}


class TestCapabilityGating:
    def test_tier_with_no_probe_stays_enabled(self):
        """A tier nobody wrote a probe for must not be silently disabled."""
        assert UnifiedExtract()._tier_available("a-tier-that-has-no-probe") is True

    def test_availability_is_cached(self):
        ex = UnifiedExtract()
        ex.available_tiers()
        assert ex._availability, "probe results must be memoised"

    def test_http_tier_is_probed_on_trafilatura(self, monkeypatch):
        """§4.6 Finding B-5: without trafilatura this tier fails for EVERY URL.

        Probing turns a guaranteed-failed attempt per URL into one named skip
        per run — and, more importantly, stops it from burying the real error.
        """
        import importlib.util

        real = importlib.util.find_spec

        def _no_trafilatura(name, *a, **kw):
            return None if name == "trafilatura" else real(name, *a, **kw)

        monkeypatch.setattr(importlib.util, "find_spec", _no_trafilatura)
        ex = UnifiedExtract()
        assert ex._tier_available("http") is False
        assert "trafilatura" in ex.unavailable_tiers()["http"]

    def test_flaresolverr_is_not_memoised(self):
        """Its usability is time-varying, so caching it is wrong in both directions.

        Memoising would either pin the breaker open long after the container
        recovered, or pin it closed while it was flooding a dead endpoint.
        """
        src = inspect.getsource(UnifiedExtract._tier_available)
        assert "flaresolverr" not in src.replace("``flaresolverr``", ""), (
            "flaresolverr must be gated by the FlareBreaker inside the tier, "
            "not by the memoised availability probe"
        )

    async def test_flaresolverr_respects_the_open_breaker(self):
        from hyperion.tools.flaresolverr import FlareBreaker

        ex = UnifiedExtract()
        try:
            FlareBreaker.record_error()
            FlareBreaker.record_error()
            assert not FlareBreaker.closed(), "precondition: breaker is open"

            async def _boom():
                raise AssertionError("must not reach the solver while open")

            ex._get_flaresolverr = _boom  # type: ignore[method-assign]
            result = await ex._extract_flaresolverr("https://a.example/1")
            assert not result.success
            assert "FlareBreaker open" in result.error
        finally:
            FlareBreaker.reset()

    def test_obscura_availability_is_never_assumed(self):
        src = Path("hyperion/tools/unified_extract.py").read_text(encoding="utf-8")
        assert "_binary_available()" in src

    def test_probe_that_raises_does_not_disable_a_tier(self, monkeypatch):
        """Attempting and failing beats skipping something that might work."""
        import hyperion.tools.unified_extract as mod

        class _Exploding:
            def __init__(self, *a, **kw):
                raise RuntimeError("probe blew up")

        monkeypatch.setattr(mod, "ObscuraClient", _Exploding)
        assert UnifiedExtract()._tier_available("obscura") is True

    async def test_close_survives_a_client_that_cannot_close(self):
        """One broken leaf client must not prevent the other nine from closing."""
        ex = UnifiedExtract()
        closed: list[str] = []

        class _Bad:
            async def close(self):
                raise RuntimeError("nope")

        class _Good:
            async def close(self):
                closed.append("good")

        ex._jina = _Bad()  # type: ignore[assignment]
        ex._wayback = _Good()  # type: ignore[assignment]
        await ex.close()
        assert closed == ["good"]


# ─────────────────────────────────────────────────────────────────────
# 4. Consumers delegate rather than reimplement
# ─────────────────────────────────────────────────────────────────────


class TestDeepSearchDelegates:
    def test_extract_batch_no_longer_implements_a_climb(self):
        """The point of 2.1. ``_extract_batch`` must call the shared driver.

        Asserted structurally rather than behaviourally: the delegation could
        be reverted to a hand-rolled loop that still passed every behavioural
        test in this file, and the codebase would be back to two ladders.
        """
        src = inspect.getsource(DeepSearchClient._extract_batch)
        assert "extract_ladder" in src, "deep_search must delegate the climb"
        assert "asyncio.gather" not in src, (
            "deep_search is running its own fan-out again — the climb belongs "
            "to UnifiedExtract"
        )

    def test_tier_labels_survive_delegation(self):
        """Its public provenance vocabulary must not change under the refactor."""
        # D5.1: `client = DeepSearchClient()` was unread (ruff F841) — this test
        # asserts on class attributes only. Instantiation retained as a smoke
        # check that the class still constructs, but not bound.
        DeepSearchClient()
        for tier in DeepSearchClient.EXTRACTION_TIERS:
            assert tier in DeepSearchClient.TIER_LABELS

    def test_declared_tiers_are_all_in_the_shared_ladder(self):
        """A tier deep_search claims but the ladder lacks can never run."""
        missing = set(DeepSearchClient.EXTRACTION_TIERS) - set(UnifiedExtract.TIER_ORDER)
        assert not missing, f"{missing} declared by deep_search but absent from the ladder"

    async def test_labels_are_applied_to_used_tried_and_errors(self):
        client = DeepSearchClient()
        for tier in DeepSearchClient.EXTRACTION_TIERS:
            client._availability[tier] = True

        def mk(tier, ok):
            async def _f(sem, url):
                async with sem:
                    return ExtractedContent(
                        url=url, content=QUALITY if ok else "", tool_used=tier
                    )

            return _f

        for tier in DeepSearchClient.EXTRACTION_TIERS:
            setattr(client, f"_extract_{tier}", mk(tier, tier == "obscura"))

        extracted, used, tried, errors = await client._extract_batch(["https://a.example/1"])
        assert len(extracted) == 1
        assert used == ["obscura"]
        assert "jina-reader" in tried and "http-extract" in tried
        assert "jina-reader" in errors, "labels must be applied to errors too"

    async def test_published_date_survives_the_round_trip(self):
        """``UnifiedExtractResult`` has no such field; a lossy adapter would drop it.

        Freshness feeds the 0.15 weight in the evidence composite, so losing the
        date silently changes every ranking.
        """
        client = DeepSearchClient()
        for tier in DeepSearchClient.EXTRACTION_TIERS:
            client._availability[tier] = True

        async def _jina(sem, url):
            async with sem:
                return ExtractedContent(
                    url=url,
                    content=QUALITY,
                    tool_used="jina-reader",
                    published_date="2025-03-14",
                )

        client._extract_jina = _jina
        extracted, _, _, _ = await client._extract_batch(["https://a.example/1"])
        assert extracted[0].published_date == "2025-03-14"
        assert extracted[0].tool_used == "jina-reader"

    async def test_unavailable_tier_is_not_invoked(self):
        client = DeepSearchClient()
        for tier in DeepSearchClient.EXTRACTION_TIERS:
            client._availability[tier] = False

        async def _boom(sem, url):
            raise AssertionError("no tier should run when all are unavailable")

        for tier in DeepSearchClient.EXTRACTION_TIERS:
            setattr(client, f"_extract_{tier}", _boom)

        extracted, used, tried, _ = await client._extract_batch(["https://a.example/1"])
        assert extracted == [] and used == [] and tried == []

    async def test_own_extract_methods_remain_the_substitution_point(self):
        """Delegation must not cost the ability to mock a tier.

        If ``_extract_batch`` bypassed these methods and called the ladder's own
        tiers, every capability-gating test would silently start exercising real
        HTTP clients instead of doubles.
        """
        client = DeepSearchClient()
        client._availability.update({t: True for t in DeepSearchClient.EXTRACTION_TIERS})
        called: list[str] = []

        async def _mine(sem, url):
            async with sem:
                called.append(url)
                return ExtractedContent(url=url, content=QUALITY, tool_used="jina-reader")

        client._extract_jina = _mine
        await client._extract_batch(["https://a.example/1"])
        assert called == ["https://a.example/1"]

    async def test_batch_larger_than_concurrency_does_not_deadlock(self, monkeypatch):
        """The reentrancy hazard, exercised through the real consumer.

        Forced to one permit for the same reason as the driver-level test: at
        ``EXTRACTION_CONCURRENCY = 5`` a double-acquire merely serialises the
        batch rather than hanging it, so the default value cannot detect the bug.
        ``deep_search``'s own ``_extract_<tier>`` methods acquire the semaphore,
        which is exactly why the shared driver must not.
        """
        monkeypatch.setattr(DeepSearchClient, "EXTRACTION_CONCURRENCY", 1)
        client = DeepSearchClient()
        client._availability.update({t: True for t in DeepSearchClient.EXTRACTION_TIERS})

        async def _jina(sem, url):
            async with sem:
                return ExtractedContent(url=url, content=QUALITY, tool_used="jina-reader")

        client._extract_jina = _jina
        urls = [f"https://a.example/{i}" for i in range(8)]
        extracted, _, _, _ = await asyncio.wait_for(client._extract_batch(urls), timeout=15)
        assert len(extracted) == len(urls)

    async def test_empty_url_list_is_a_no_op(self):
        assert await DeepSearchClient()._extract_batch([]) == ([], [], [], {})

    async def test_ladder_is_closed_with_the_client(self):
        client = DeepSearchClient()
        ladder = client._get_unified_extract()
        assert ladder is not None
        await client.close()
        assert client._unified_extract is None, "the shared ladder leaked past close()"


class TestSubAgentDelegates:
    def test_gather_raw_data_no_longer_unrolls_tiers(self):
        """It used to be five copy-pasted ``for url in all_urls[:N]`` blocks."""
        src = inspect.getsource(SubAgentRunner._gather_raw_data)
        for tool in ("obscura.fetch", "scrapling.fetch", "crawl4ai.crawl"):
            assert tool not in src, f"{tool} is still called inline — ladder not collapsed"
        assert "_extract_urls" in src

    def test_extraction_delegates_to_the_shared_ladder(self):
        src = inspect.getsource(SubAgentRunner._extract_urls)
        assert "extract_ladder" in src
        assert "UnifiedExtract" in src

    def test_one_url_budget_not_five_different_ones(self):
        """The inline ladder used [:6], [:6], [:8], [:4], [:3].

        A URL's chance of being extracted therefore depended on its rank in a
        merged search list: URLs 7–8 were reachable only by the third tier, and
        URLs past 8 by no tier at all. That is not a retrieval policy anyone
        chose — it is an artefact of five independently-written loops.
        """
        assert isinstance(SubAgentRunner.MAX_EXTRACT_URLS, int)
        src = inspect.getsource(SubAgentRunner._extract_urls)
        assert "MAX_EXTRACT_URLS" in src

    @pytest.mark.parametrize(
        "granted,expected_present,expected_absent",
        [
            ([ToolName.JINA], {"jina"}, {"obscura", "scrapling", "crawl4ai", "flaresolverr"}),
            ([ToolName.OBSCURA], {"obscura"}, {"jina", "scrapling"}),
            ([ToolName.SCRAPLING, ToolName.CRAWL4AI], {"scrapling", "crawl4ai"}, {"jina", "obscura"}),
            ([ToolName.SEARXNG], set(), {"jina", "obscura", "scrapling", "crawl4ai"}),
        ],
    )
    def test_only_granted_tools_back_an_offered_tier(self, granted, expected_present, expected_absent):
        """§4.7: a sub-agent may only use the tool subset it was handed.

        The inline ladder honoured this via ``_has_tool`` guards; the delegation
        must not quietly widen a junior agent's reach to the full ladder.
        """
        tiers = set(_sub_agent(granted)._extraction_tiers())
        assert expected_present <= tiers
        assert not (expected_absent & tiers)

    def test_keyless_tiers_are_always_offered(self):
        """curl_cffi and http are plain HTTP with no ToolName to grant.

        Gating them behind a grant nobody can express would make the cheapest
        parsing tier the codebase ships permanently unreachable from sub-agents
        — which is exactly how it came to be missing from this path.
        """
        tiers = _sub_agent([ToolName.SEARXNG])._extraction_tiers()
        assert "curl_cffi" in tiers and "http" in tiers

    def test_browser_tiers_are_never_auto_granted(self):
        """nodriver/camoufox launch real browsers and have no ToolName.

        Unlike the keyless tiers they are expensive, so §4.7's quota discipline
        says a junior agent must not reach for them unilaterally.
        """
        tiers = _sub_agent(list(ToolName))._extraction_tiers()
        assert "nodriver" not in tiers and "camoufox" not in tiers

    def test_offered_tiers_are_all_real(self):
        tiers = _sub_agent(list(ToolName))._extraction_tiers()
        unknown = set(tiers) - set(UnifiedExtract.TIER_ORDER)
        assert not unknown, f"{unknown} would be dropped by the ladder"

    async def test_extracted_content_is_labelled_with_its_url_and_tool(self):
        """Provenance has to reach the LLM prompt, not just the log."""
        runner = _sub_agent([ToolName.JINA])

        async def _fake_ladder(urls, **kw):
            return LadderOutcome(
                results=[
                    UnifiedExtractResult(
                        url="https://a.example/1",
                        content=QUALITY,
                        markdown=QUALITY,
                        tool_used="jina",
                        success=True,
                    )
                ],
                tools_used=["jina"],
                tools_tried=["curl_cffi", "jina"],
            )

        import hyperion.tools.unified_extract as mod

        class _Fake:
            def __init__(self, *a, **kw):
                pass

            extract_ladder = staticmethod(_fake_ladder)

            async def close(self):
                pass

        original = mod.UnifiedExtract
        mod.UnifiedExtract = _Fake  # type: ignore[misc]
        try:
            blocks, errors = await runner._extract_urls(["https://a.example/1"])
        finally:
            mod.UnifiedExtract = original  # type: ignore[misc]

        assert len(blocks) == 1
        assert "https://a.example/1" in blocks[0]
        assert blocks[0].startswith("jina content from")
        assert errors == []

    async def test_ladder_failure_is_reported_not_swallowed(self):
        """A sub-agent losing its whole research phase must say so.

        This is the audit's P0 failure mode (§4.2): a silent nothing is
        indistinguishable from a genuine absence of sources.
        """
        runner = _sub_agent([ToolName.JINA])
        import hyperion.tools.unified_extract as mod

        class _Exploding:
            def __init__(self, *a, **kw):
                pass

            async def extract_ladder(self, urls, **kw):
                raise RuntimeError("ladder exploded")

            async def close(self):
                pass

        original = mod.UnifiedExtract
        mod.UnifiedExtract = _Exploding  # type: ignore[misc]
        try:
            blocks, errors = await runner._extract_urls(["https://a.example/1"])
        finally:
            mod.UnifiedExtract = original  # type: ignore[misc]

        assert blocks == []
        assert errors and "exploded" in errors[0]

    async def test_empty_outcome_explains_every_tier(self):
        runner = _sub_agent([ToolName.JINA])
        import hyperion.tools.unified_extract as mod

        class _Empty:
            def __init__(self, *a, **kw):
                pass

            async def extract_ladder(self, urls, **kw):
                return LadderOutcome(
                    tools_tried=["curl_cffi", "jina"],
                    errors={"curl_cffi": "no usable content", "jina": "HTTP 429"},
                    tiers_unavailable={"http": "trafilatura not installed"},
                )

            async def close(self):
                pass

        original = mod.UnifiedExtract
        mod.UnifiedExtract = _Empty  # type: ignore[misc]
        try:
            blocks, errors = await runner._extract_urls(["https://a.example/1"])
        finally:
            mod.UnifiedExtract = original  # type: ignore[misc]

        assert blocks == []
        joined = " ".join(errors)
        assert "429" in joined
        assert "trafilatura not installed" in joined, (
            "a missing optional dependency must be traceable, not shrugged off"
        )
