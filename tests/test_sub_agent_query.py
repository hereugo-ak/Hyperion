"""
Tests for SubAgentRunner._condense_query — the sole query builder used by
all 12 specialist sub-agents (§4.7).

This file exists specifically because a single malformed regex character
class in this method (`[\\u2014\\u2013--]`, parsed as an invalid character
RANGE under Python 3.13) raised `re.PatternError` UNCONDITIONALLY on every
call. Every one of the 13 call sites wraps the call in a bare
`except Exception: pass`, so the crash was swallowed silently and every
sub-agent search leg returned empty results — a 100% research outage that
345 previously-green tests never caught (`_condense_query` had zero test
coverage). See HYPERION_DEEP_AUDIT_2026-07-27.md §0 / Finding B-1 / fix 0.1.

This test suite locks in the fix (0.1) and prevents regression (0.2).
"""

from __future__ import annotations

import re

import pytest

from hyperion.agents.sub_agent import SubAgentRunner

# A deliberately adversarial corpus: em-dash, en-dash, plain hyphen (both
# standalone and doubled), parentheticals, unicode, mixed punctuation, and
# realistic full-paragraph sub-agent questions of the kind the audit
# reproduced the crash with.
ADVERSARIAL_INPUTS = [
    "Find market size in Nigeria — 2024 data",
    "plain question about market size in Nigeria",
    "Find lithium battery cost data",
    "Should we enter now-or wait? (Bitcoin, Ethereum)",
    "multi -- hyphen -- test",
    "en\u2013dash test",
    "em\u2014dash test",
    "Find TAM data for: Should India enter the blockchain market?",
    "double--hyphen--sequence",
    "trailing hyphen-",
    "-leading hyphen",
    "a-b-c-d-e chained hyphens",
    "unicode em\u2014dash mixed with en\u2013dash and a-hyphen all at once",
    "Should we enter now or wait? (Bitcoin, Ethereum) — decide fast",
    "",
    "   ",
    "a" * 500,  # far past max_len, no separators at all
    "word " * 100,  # long, needs truncation at word boundary
    "Investigate (Scope 3) emissions — ISO 14001 compliance for Q3-2024",
    "Research the U.S.-China trade war's effect on semiconductors—now",
    "Analyze non-existent-word combos: pre-2020, post-2020, mid-cycle",
    "Discover — — — — nothing but dashes — — — —",
    "----------",
    "\u2013\u2014\u2013\u2014",
    "Gather data for: what-if scenario A vs scenario B (2025-2030)",
    "Collect statistics regarding e-commerce growth — YoY — in APAC",
    "Look up ISO-9001 and ISO 14001 certifications for suppliers",
    "Identify key-value pairs in the regulatory filing — Section 301",
    "Explore multi-agent — sub-agent — research pipelines",
    "Should India's 5G rollout — delayed twice — proceed in 2025?",
]


class TestCondenseQueryNeverRaises:
    """Fix 0.2: _condense_query must never raise, across a hostile corpus."""

    @pytest.mark.parametrize("question", ADVERSARIAL_INPUTS)
    def test_never_raises(self, question: str) -> None:
        # Must not raise re.PatternError (or anything else).
        result = SubAgentRunner._condense_query(question)
        assert isinstance(result, str)

    def test_em_dash_is_stripped(self) -> None:
        result = SubAgentRunner._condense_query("Nigeria market size — 2024 report")
        assert "\u2014" not in result

    def test_en_dash_is_stripped(self) -> None:
        result = SubAgentRunner._condense_query("Nigeria market size \u2013 2024 report")
        assert "\u2013" not in result

    def test_plain_hyphen_separator_is_stripped(self) -> None:
        result = SubAgentRunner._condense_query("multi -- hyphen -- test")
        assert "--" not in result

    def test_empty_input_returns_string(self) -> None:
        result = SubAgentRunner._condense_query("")
        assert isinstance(result, str)

    def test_result_respects_max_len(self) -> None:
        long_question = "word " * 200
        result = SubAgentRunner._condense_query(long_question, max_len=120)
        assert len(result) <= 120

    def test_result_is_never_empty_for_nonempty_input(self) -> None:
        # Falls back to question[:max_len] if condensation empties the string.
        result = SubAgentRunner._condense_query("the a an of to in on at by")
        assert result != ""

    def test_hyphenated_compound_terms_survive_reasonably(self) -> None:
        # Regression guard: we don't need the exact output, just no crash
        # and no leftover raw '--' artifacts from doubled separators.
        result = SubAgentRunner._condense_query("ISO-9001 certification data")
        assert "--" not in result

    def test_parametrized_regex_pattern_itself_compiles(self) -> None:
        # Direct unit check on the specific pattern that caused the P0:
        # hyphen must be the LAST literal char in the class (or escaped),
        # never adjacent to a preceding \uXXXX escape, or Python parses it
        # as a character range.
        pattern = r'\s*[\u2013\u2014-]+\s*'
        compiled = re.compile(pattern)  # must not raise re.PatternError
        assert compiled.sub(" ", "a\u2014b\u2013c-d") == "a b c d"


class TestCondenseQueryBehavior:
    """Sanity checks on the intended condensation behavior."""

    def test_strips_find_prefix(self) -> None:
        result = SubAgentRunner._condense_query("Find lithium battery cost data")
        assert not result.lower().startswith("find ")

    def test_strips_data_for_prefix(self) -> None:
        result = SubAgentRunner._condense_query(
            "Find TAM data for: Should India enter the blockchain market?"
        )
        assert "data for" not in result.lower()

    def test_truncates_at_word_boundary(self) -> None:
        result = SubAgentRunner._condense_query("word " * 100, max_len=50)
        assert len(result) <= 50
        # Should not end mid-word-cut artifact from a hard slice beyond
        # what rsplit(' ', 1) would leave — no trailing partial fragment
        # longer than the longest token ("word").
        assert not result.endswith(" wor")


class TestCondenseQueryIntentPreservation:
    """fix 1.4 (audit §4.4 Finding B-3): the pre-fix `filler` set deleted
    'not', 'should', 'how', 'why', 'what', 'which', 'most', 'more' — the
    exact words that carry a consulting question's analytical intent.
    These tests lock in that those 8 words now survive condensation.
    """

    @pytest.mark.parametrize(
        "word",
        ["not", "should", "how", "why", "what", "which", "most", "more"],
    )
    def test_intent_word_survives_condensation(self, word: str) -> None:
        # Each word embedded in a sentence with otherwise-fillerable
        # padding must still appear in the condensed output.
        question = f"Analyze {word} lithium battery cost drivers globally"
        result = SubAgentRunner._condense_query(question)
        assert word in result.lower().split(), (
            f"'{word}' was stripped from {question!r} -> {result!r}"
        )

    def test_negation_does_not_invert_question_meaning(self) -> None:
        """The single most important regression guard: 'not' must survive,
        or a negative-framed question is silently rewritten into its
        opposite."""
        result = SubAgentRunner._condense_query(
            "Should we NOT enter this market given the regulatory risk?"
        )
        assert "not" in result.lower().split()

    def test_superlative_most_survives(self) -> None:
        result = SubAgentRunner._condense_query(
            "What is the most effective market entry strategy?"
        )
        assert "most" in result.lower().split()

    def test_comparative_more_survives(self) -> None:
        result = SubAgentRunner._condense_query(
            "Is India or Vietnam a more attractive manufacturing base?"
        )
        assert "more" in result.lower().split()

    def test_interrogative_how_why_survive(self) -> None:
        result = SubAgentRunner._condense_query(
            "How and why did the 2021 chip shortage happen?"
        )
        words = result.lower().split()
        assert "how" in words
        assert "why" in words

    def test_grammatical_filler_is_still_stripped(self) -> None:
        # Sanity guard: this fix narrows the filler set, it doesn't empty
        # it. Pure grammatical connective tissue in a sentence that also
        # carries real content words must still be removed.
        result = SubAgentRunner._condense_query(
            "Find the market size of the semiconductor industry in India"
        )
        words = result.lower().split()
        for w in ("the", "of", "in"):
            assert w not in words, f"grammatical filler {w!r} survived: {result!r}"
        # But the real content words must remain.
        assert "market" in words
        assert "semiconductor" in words
        assert "india" in words


class TestCondenseQueryVariants:
    """fix 1.4: `_condense_query_variants` recovers parenthetical entity
    lists (e.g. "(Bitcoin, Ethereum)") as a second query variant instead
    of the primary query silently losing them."""

    def test_no_parenthetical_returns_single_variant(self) -> None:
        variants = SubAgentRunner._condense_query_variants(
            "Find lithium battery cost data"
        )
        assert len(variants) == 1

    def test_never_returns_empty_list(self) -> None:
        for q in ["", "   ", "(only a parenthetical)", "a" * 500]:
            variants = SubAgentRunner._condense_query_variants(q)
            assert isinstance(variants, list)
            assert len(variants) >= 1

    def test_entity_parenthetical_produces_second_variant(self) -> None:
        variants = SubAgentRunner._condense_query_variants(
            "Should we enter now or wait? (Bitcoin, Ethereum)"
        )
        assert len(variants) == 2
        primary, entity_variant = variants
        # The entities must appear in the second variant but the primary
        # stays entity-free (matches _condense_query's existing contract).
        assert "bitcoin" not in primary.lower()
        assert "ethereum" not in primary.lower()
        assert "bitcoin" in entity_variant.lower()
        assert "ethereum" in entity_variant.lower()

    def test_eg_prefixed_entity_parenthetical_strips_the_eg_label(self) -> None:
        """fix 1.4 polish: "(e.g. Salesforce, HubSpot)" is NOT purely
        trivial (it names real entities) so it must still produce a
        second variant — but the literal "e.g." abbreviation must not
        ride along into the search query."""
        for q in [
            "Compare vendor pricing (e.g. Salesforce, HubSpot)",
            "Compare vendor pricing (e.g: Salesforce, HubSpot)",
            "Compare vendor pricing (i.e. Salesforce, HubSpot)",
            "Compare vendor pricing (etc. Salesforce, HubSpot)",
        ]:
            variants = SubAgentRunner._condense_query_variants(q)
            assert len(variants) == 2, f"{q!r} did not produce a second variant: {variants}"
            _, entity_variant = variants
            assert "salesforce" in entity_variant.lower()
            assert "hubspot" in entity_variant.lower()
            assert "e.g" not in entity_variant.lower()
            assert "i.e" not in entity_variant.lower()
            assert not entity_variant.lower().strip().startswith("etc")

    def test_trivial_parenthetical_does_not_produce_second_variant(self) -> None:
        for q in [
            "Analyze market trends (see above)",
            "Research growth rates (e.g.)",
            "Explore data (etc.)",
        ]:
            variants = SubAgentRunner._condense_query_variants(q)
            assert len(variants) == 1, f"{q!r} produced spurious variant: {variants}"

    def test_variant_respects_max_len(self) -> None:
        long_question = (
            "Should we invest heavily in this particular emerging market "
            "segment right now given current macroeconomic headwinds "
            "(Argentina, Brazil, Chile, Colombia, Peru, Uruguay, Paraguay)?"
        )
        variants = SubAgentRunner._condense_query_variants(long_question, max_len=60)
        for v in variants:
            assert len(v) <= 60

    def test_variants_never_raise_on_adversarial_corpus(self) -> None:
        for q in ADVERSARIAL_INPUTS:
            variants = SubAgentRunner._condense_query_variants(q)
            assert isinstance(variants, list)
            assert len(variants) >= 1
            for v in variants:
                assert isinstance(v, str)


class _FakeResult:
    """Minimal stand-in for a SearxNG/Jina result object — just enough
    attributes for `_search_searxng`/`_search_jina`'s formatting code."""

    def __init__(self, title: str, url: str, snippet: str = "") -> None:
        self.title = title
        self.url = url
        self.snippet = snippet


def _n_results(n: int, prefix: str = "r") -> list[_FakeResult]:
    """`n` distinct fake results — enough to clear `LOW_YIELD_THRESHOLD`
    (3) on its own, so tests that aren't specifically about the fix-1.5
    retry path don't accidentally trigger it."""
    return [_FakeResult(f"{prefix}{i}", f"https://example.com/{prefix}{i}") for i in range(n)]


def _disable_planner(runner: SubAgentRunner) -> None:
    """Pin `runner._plan_queries` to the pure fix-1.4 deterministic variants.

    **Why this exists (fix 1.3).** `_search_searxng`/`_search_jina` now build
    their query set with `_plan_queries()`, which fans the sub-question out
    through the LLM query planner (audit §7 item 1.3) on top of the fix-1.4
    `_condense_query_variants` baseline. That is the correct production
    behaviour and is tested directly in `tests/test_query_planner.py`.

    But the two test classes below exist to pin *fix 1.4's variant fanout*
    and *fix 1.5's low-yield retry* in isolation — both of which assert exact
    `await_count` values on the search double. Leaving the planner engaged
    would make those counts a function of how many angles the planner
    happened to emit, i.e. it would silently convert "the fix-1.4 variant
    fired" into "some number of queries fired", losing the property the
    tests were written to protect (the same reasoning fix 1.5 applied when
    it updated fix 1.4's mocks to return >= LOW_YIELD_THRESHOLD results).

    So these tests hold the planner constant instead, exactly as they hold
    the tool clients constant. The planner's own contribution is not
    untested — it is covered end-to-end in `tests/test_query_planner.py`'s
    `TestSubAgentUsesThePlanner`, including that both legs dispatch planner
    queries and that the union across legs meets the audit's ">=8 distinct
    grounded queries per sub-question" exit criterion.
    """

    async def _variants_only(leg: str = "") -> list[str]:
        return SubAgentRunner._condense_query_variants(runner.spec.question)

    runner._plan_queries = _variants_only  # type: ignore[method-assign]


class TestSearchMethodsUseVariants:
    """fix 1.4: `_search_searxng`/`_search_jina` must actually fire the
    second (entity-preserving) query variant when the question contains a
    usable parenthetical, not just compute it and discard it.

    Each mock here returns >= LOW_YIELD_THRESHOLD results so the fix-1.5
    low-yield retry (tested separately in `TestLowYieldReformulation`)
    never engages and cannot change these tests' call counts."""

    @staticmethod
    def _make_runner(question: str, tools: list) -> SubAgentRunner:
        from unittest.mock import MagicMock

        from hyperion.schemas.agents import AgentName, ModelTier, SubAgentSpec

        spec = SubAgentSpec(
            question=question,
            parent_agent=AgentName.MARKET_ANALYST,
            model_tier=ModelTier.MICRO,
            tools=tools,
            findings_model="KeyFinding",
        )
        runner = SubAgentRunner(spec, bus=MagicMock(), router=MagicMock())
        # fix 1.3: hold the LLM query planner constant so these tests keep
        # measuring fix 1.4/1.5 behaviour rather than planner output. See
        # `_disable_planner`'s docstring.
        _disable_planner(runner)
        return runner

    def test_search_searxng_fires_both_variants_when_entity_parenthetical_present(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner(
            "Should we enter now or wait? (Bitcoin, Ethereum)", [ToolName.SEARXNG]
        )
        spy_search = AsyncMock(return_value=_n_results(3))
        runner._tools["searxng"] = type(
            "FakeSearxNG", (), {"search": spy_search}
        )()

        asyncio.run(runner._search_searxng())

        assert spy_search.await_count == 2
        queries = [c.args[0] for c in spy_search.await_args_list]
        assert "bitcoin" in queries[1].lower() or "bitcoin" in queries[0].lower()

    def test_search_searxng_fires_single_query_when_no_parenthetical(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner(
            "Find lithium battery cost data", [ToolName.SEARXNG]
        )
        spy_search = AsyncMock(return_value=_n_results(3))
        runner._tools["searxng"] = type(
            "FakeSearxNG", (), {"search": spy_search}
        )()

        asyncio.run(runner._search_searxng())

        assert spy_search.await_count == 1

    def test_search_jina_fires_both_variants_when_entity_parenthetical_present(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner(
            "Compare vendor pricing (Salesforce, HubSpot, Pipedrive)",
            [ToolName.JINA],
        )
        spy_search = AsyncMock(return_value=_n_results(3))
        runner._tools["jina"] = type("FakeJina", (), {"search": spy_search})()

        asyncio.run(runner._search_jina())

        assert spy_search.await_count == 2
        queries = [c.args[0] for c in spy_search.await_args_list]
        assert any("salesforce" in q.lower() for q in queries)

    def test_search_searxng_dedups_urls_across_variants(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        shared = _FakeResult("Shared", "https://example.com/shared")
        only_in_second = _FakeResult("Second", "https://example.com/second")
        # A third distinct result keeps the merged primary-pass count at
        # LOW_YIELD_THRESHOLD (3) so the fix-1.5 retry does not engage —
        # this test is about dedup, not the retry (covered separately).
        third = _FakeResult("Third", "https://example.com/third")

        spy_search = AsyncMock(side_effect=[[shared], [shared, only_in_second, third]])

        runner = self._make_runner(
            "Should we enter now or wait? (Bitcoin, Ethereum)", [ToolName.SEARXNG]
        )
        runner._tools["searxng"] = type(
            "FakeSearxNG", (), {"search": spy_search}
        )()

        label, urls, formatted = asyncio.run(runner._search_searxng())

        assert label == "searxng"
        assert spy_search.await_count == 2  # confirms the retry did NOT fire
        # The shared URL must appear exactly once despite being returned by
        # both variant searches.
        assert urls.count("https://example.com/shared") == 1
        assert "https://example.com/second" in urls

    def test_search_searxng_never_raises_when_tool_fails(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner(
            "Should we enter now or wait? (Bitcoin, Ethereum)", [ToolName.SEARXNG]
        )
        spy_search = AsyncMock(side_effect=RuntimeError("boom"))
        runner._tools["searxng"] = type(
            "FakeSearxNG", (), {"search": spy_search}
        )()

        label, urls, formatted = asyncio.run(runner._search_searxng())
        assert label == "searxng"
        assert urls == []
        assert formatted is None


class TestLowYieldReformulation:
    """fix 1.5 (audit §7 item 1.5): a query returning fewer than
    `LOW_YIELD_THRESHOLD` (3) scored results gets one broadened retry with
    the geography anchor dropped, instead of being accepted as final."""

    @staticmethod
    def _make_runner(question: str, tools: list) -> SubAgentRunner:
        from unittest.mock import MagicMock

        from hyperion.schemas.agents import AgentName, ModelTier, SubAgentSpec

        spec = SubAgentSpec(
            question=question,
            parent_agent=AgentName.MARKET_ANALYST,
            model_tier=ModelTier.MICRO,
            tools=tools,
            findings_model="KeyFinding",
        )
        runner = SubAgentRunner(spec, bus=MagicMock(), router=MagicMock())
        # fix 1.3: hold the LLM query planner constant so these tests keep
        # measuring fix 1.4/1.5 behaviour rather than planner output. See
        # `_disable_planner`'s docstring.
        _disable_planner(runner)
        return runner

    def test_zero_results_triggers_one_broadened_retry(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner("Find lithium battery cost data", [ToolName.SEARXNG])
        # Primary pass (1 query, no parenthetical) returns nothing; the
        # broadened retry (1 more query) returns 2 results.
        spy_search = AsyncMock(side_effect=[[], _n_results(2)])
        runner._tools["searxng"] = type("FakeSearxNG", (), {"search": spy_search})()

        label, urls, formatted = asyncio.run(runner._search_searxng())

        assert spy_search.await_count == 2
        # The retry call must have asked for drop_geography=True.
        assert spy_search.await_args_list[1].kwargs.get("drop_geography") is True
        # And the primary call must NOT have (it should use the normal,
        # geography-anchored path).
        assert not spy_search.await_args_list[0].kwargs.get("drop_geography")
        assert label == "searxng"
        assert len(urls) == 2

    def test_below_threshold_yield_triggers_retry_and_merges_results(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner("Find lithium battery cost data", [ToolName.SEARXNG])
        # Primary pass returns 2 results (< 3 threshold) -> retry fires and
        # contributes 2 more distinct results -> merged total is 4.
        spy_search = AsyncMock(
            side_effect=[_n_results(2, prefix="primary"), _n_results(2, prefix="broad")]
        )
        runner._tools["searxng"] = type("FakeSearxNG", (), {"search": spy_search})()

        label, urls, formatted = asyncio.run(runner._search_searxng())

        assert spy_search.await_count == 2
        assert len(urls) == 4  # capped at [:8], well under the cap here

    def test_at_or_above_threshold_yield_does_not_retry(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner("Find lithium battery cost data", [ToolName.SEARXNG])
        spy_search = AsyncMock(return_value=_n_results(3))
        runner._tools["searxng"] = type("FakeSearxNG", (), {"search": spy_search})()

        asyncio.run(runner._search_searxng())

        # Exactly 1 call — the primary query, no parenthetical, and no
        # retry since 3 already meets the threshold.
        assert spy_search.await_count == 1

    def test_retry_deduplicates_against_primary_results(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner("Find lithium battery cost data", [ToolName.SEARXNG])
        shared = _FakeResult("Shared", "https://example.com/shared")
        new_one = _FakeResult("New", "https://example.com/new")
        # Primary returns just `shared` (1 result, below threshold); the
        # broadened retry returns `shared` again plus one genuinely new URL.
        spy_search = AsyncMock(side_effect=[[shared], [shared, new_one]])
        runner._tools["searxng"] = type("FakeSearxNG", (), {"search": spy_search})()

        label, urls, formatted = asyncio.run(runner._search_searxng())

        assert urls.count("https://example.com/shared") == 1
        assert "https://example.com/new" in urls

    def test_jina_low_yield_also_retries_with_drop_geography(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner("Find lithium battery cost data", [ToolName.JINA])
        spy_search = AsyncMock(side_effect=[[], _n_results(3)])
        runner._tools["jina"] = type("FakeJina", (), {"search": spy_search})()

        label, urls, formatted = asyncio.run(runner._search_jina())

        assert spy_search.await_count == 2
        assert spy_search.await_args_list[1].kwargs.get("drop_geography") is True
        assert label == "jina"
        assert len(urls) == 3

    def test_retry_never_raises_when_broadened_call_also_fails(self):
        import asyncio
        from unittest.mock import AsyncMock

        from hyperion.schemas.agents import ToolName

        runner = self._make_runner("Find lithium battery cost data", [ToolName.SEARXNG])
        spy_search = AsyncMock(side_effect=[[], RuntimeError("boom")])
        runner._tools["searxng"] = type("FakeSearxNG", (), {"search": spy_search})()

        label, urls, formatted = asyncio.run(runner._search_searxng())

        # The whole method's try/except still catches the retry's failure —
        # fail loud (logged), not silently propagate.
        assert label == "searxng"
        assert urls == []
        assert formatted is None
