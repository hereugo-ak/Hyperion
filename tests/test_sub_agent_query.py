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
