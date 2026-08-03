"""No verbatim paragraph duplication inside a chapter (P2-13).

Six chapters of report B repeat paragraphs verbatim (twice on the same page
in Market Landscape). Cause: multiple KeyFinding objects carried the same
generated content, and assembly had no deduplication.

After the fix: normalized-hash dedup at the point of assembly removes a
repeated paragraph of >= 12 words, keeping the first occurrence.
"""

from __future__ import annotations

from hyperion.output.dedup import dedup_paragraphs, normalized_paragraph_hash

DUP = (
    "The addressable market remains difficult to size because the entity has "
    "no public footprint and no disclosed revenue figures for the period."
)


class TestNormalizedHash:
    def test_whitespace_and_case_insensitive(self):
        a = normalized_paragraph_hash("The Market  is viable.\nNext line.")
        b = normalized_paragraph_hash("the market is viable. next line.")
        assert a == b

    def test_different_text_different_hash(self):
        assert normalized_paragraph_hash("alpha beta gamma delta") != normalized_paragraph_hash(
            "alpha beta gamma epsilon"
        )


class TestDedupParagraphs:
    def test_duplicate_long_paragraph_removed(self):
        body = f"{DUP}\n\nSome intervening analysis paragraph with distinct content.\n\n{DUP}"
        out = dedup_paragraphs(body)
        assert out.count("no public footprint") == 1

    def test_short_paragraphs_left_alone(self):
        # Under the 12-word floor, repetition is a stylistic choice, not a defect.
        body = "See above.\n\nAnalysis continues here with more detail added.\n\nSee above."
        out = dedup_paragraphs(body)
        assert out.count("See above.") == 2

    def test_first_occurrence_kept_order_preserved(self):
        p1 = "First unique paragraph with enough words to pass the twelve word floor easily."
        p2 = "Second unique paragraph also with enough words to pass the twelve word floor."
        body = f"{p1}\n\n{p2}\n\n{p1}"
        out = dedup_paragraphs(body)
        paragraphs = [p for p in out.split("\n\n") if p.strip()]
        assert paragraphs[0] == p1
        assert paragraphs[1] == p2
        assert len(paragraphs) == 2
