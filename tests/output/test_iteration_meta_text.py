"""Quality-iteration output must not write QA feedback into the deliverable (P2-14).

The iteration prompt hands the LLM the Quality Gate's fix instructions; the
LLM narrates the instruction instead of executing it ("the section
previously lacked a key insight", "$XB", "[verified citation]"), and the
result was stored as content in both fixtures.

After the fix (gate P2-G34):
1. Iteration output containing a meta-text token is discarded, not stored.
2. An iteration that reduces information (shorter AND contains a blocklist
   token) keeps the old text.
3. The tokens live in one blocklist shared with the render path.
"""

from __future__ import annotations

import pytest

from hyperion.output.meta_text import contains_meta_text, reject_meta_text


class TestContainsMetaText:
    @pytest.mark.parametrize(
        "bad",
        [
            "the section previously lacked a key insight",
            "TAM triangulation previously resulted in a parse error",
            "The addressable market is $XB by 2030",
            "a range of $YB-$ZB",
            "Source: [verified citation]",
            "[new source for TAM]",
            "Market size \u27e8TAM_FIGURE\u27e9 pending",
            "This section requires additional research",
            "placeholder value",
        ],
    )
    def test_blocklist_tokens_detected(self, bad):
        assert contains_meta_text(bad), f"should flag: {bad!r}"

    @pytest.mark.parametrize(
        "good",
        [
            "The market is viable at high penetration.",
            "Revenue reached $12B in the prior fiscal year.",
            "An earlier study estimated 4% growth; our estimate is 7%.",
        ],
    )
    def test_clean_prose_passes(self, good):
        assert not contains_meta_text(good), f"false positive: {good!r}"


class TestRejectMetaText:
    def test_meta_output_returns_none(self):
        assert reject_meta_text("the section previously lacked sources") is None

    def test_clean_output_returned(self):
        text = "Updated analytical body with real content."
        assert reject_meta_text(text) == text

    def test_shorter_and_meta_keeps_old(self):
        """An iteration that is shorter AND contains meta-text must not
        replace the longer original (P2-14 rule 2)."""
        old = "A substantive original body with real analysis. " * 10
        new = "previously lacked detail"
        assert reject_meta_text(new, old_text=old) == old

    def test_empty_input_returns_none(self):
        assert reject_meta_text("") is None
        assert reject_meta_text(None) is None
