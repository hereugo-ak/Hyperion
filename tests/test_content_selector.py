"""Tests for fix 2.2 — chunk → rerank → top-k content selection.

HYPERION_DEEP_AUDIT_2026-07-27.md §4.7 Finding B-6 / §6 Phase 2 item 2.2:
"Replace blind ``content[:15000]`` with **chunk → rerank → top-k-by-relevance**
assembly."

The audit's specific complaint is not that truncation exists — a context window
is finite — but that it was *positional* and therefore *adversely biased*: the
front of an institutional PDF is a title page, a copyright notice and a table of
contents, while the tables and conclusions are in the back half. So the headline
test in this file (:class:`TestTheAuditsActualComplaint`) is not "does chunking
work" but "does the retained text now contain the numbers a head-slice threw
away" — measured on a document shaped like the ones the audit named (IEA/IMF/
World Bank outlooks, 10-Ks).

Everything else here defends a property that, if it silently broke, would turn
this fix into a *different* form of the same bug:

  * lossless chunking      — a chunker that drops text is a head-slice in disguise
  * never-raises           — §0's P0 was a silent query-layer crash; a silent
                             selector crash would zero retention the same way
  * budget respected       — an over-budget return blows the context window
  * document order out     — a relevance-sorted jumble is worse LLM input
  * lead chunk kept        — context-free numbers are worse than fewer numbers
  * token boundary         — §4.8's substring defect must not be reintroduced
  * determinism            — fix 5.2's golden-PDF test is unpinnable without it
"""

from __future__ import annotations

import logging
import re

import pytest

from hyperion.tools.content_selector import (
    DEFAULT_BUDGET_CHARS,
    Chunk,
    SelectionResult,
    chunk_content,
    rerank_chunks,
    select_content,
    select_relevant_content,
    tokenize,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — a document shaped like the ones the audit named
# ─────────────────────────────────────────────────────────────────────────────

QUERY = "Nigeria lithium-ion battery market size CAGR 2030"

# Front matter: exactly what a positional head-slice retains.
_FRONT = "\n\n".join([
    "# Global Lithium-Ion Battery Outlook 2025",
    "Copyright (c) 2025 International Energy Institute. All rights reserved. "
    "No part of this publication may be reproduced, stored in a retrieval "
    "system or transmitted without prior written permission. " * 8,
    "## Foreword\nThis publication continues our long tradition of annual "
    "outlooks. We thank the many colleagues and external reviewers who "
    "generously contributed their time and expertise. " * 12,
    "## Table of Contents\nForeword ... 3\nAbbreviations ... 5\n"
    "Chapter 1 ... 9\nChapter 2 ... 41\nChapter 3 ... 77\nAnnex A ... 120\n" * 6,
    "## Acknowledgements\nThe team is grateful for the sustained support of "
    "its steering committee and of the funders who made this work possible. " * 20,
])

# Middle: on-topic-ish but not quantitative.
_MIDDLE = (
    "## Chapter 1 — Methodological Approach\nOur modelling framework follows "
    "the standard bottom-up convention described in prior editions of this "
    "outlook. " * 30
)

# Back matter: the evidence. Exactly what a head-slice discards.
_BACK = "\n\n".join([
    "## Chapter 3 — Nigeria Market Sizing\n"
    "The Nigerian lithium-ion battery market reached USD 412 million in 2024 "
    "and is forecast to grow at a 21.4% CAGR to USD 1.32 billion by 2030. "
    "Installed storage capacity rose from 0.9 GWh to 2.7 GWh over the period.",
    "### Exhibit 3.4 — Nigeria battery demand by segment, 2024-2030 (GWh)\n"
    "| Segment | 2024 | 2027 | 2030 |\n|---|---|---|---|\n"
    "| Grid storage | 0.4 | 1.1 | 2.9 |\n"
    "| Two/three-wheeler | 0.3 | 0.9 | 2.1 |\n"
    "| Passenger BEV | 0.2 | 0.7 | 1.8 |\n"
    "Note: Nigeria only. Source: IEI modelling.",
    "## Conclusions\nNigeria's battery demand is concentrated in grid storage, "
    "which accounts for 48% of 2030 volumes at a levelised cost of USD 94/kWh.",
])

LONG_DOC = f"{_FRONT}\n\n{_MIDDLE}\n\n{_BACK}"

# The four facts a consulting report would need from LONG_DOC. Each is in the
# back half, i.e. beyond any plausible head-slice boundary.
EVIDENCE_MARKERS = ("412 million", "21.4% CAGR", "| Grid storage", "USD 94/kWh")

# Adversarial corpus. Mirrors the shape of the Phase-0 corpus in
# tests/test_sub_agent_query.py — the fix that taught this repo that a query/
# content pipeline needs a never-raises proof, not a happy-path one.
ADVERSARIAL_CONTENT: tuple[str, ...] = (
    "",
    "   ",
    "\n\n\n",
    "\t\t",
    "a",
    "#",
    "###### ",
    "# heading with no body",
    "\x00\x01\x02",
    "é" * 5000,
    "—" * 3000,
    "🎯" * 2000,
    "|" * 9000,
    "." * 20000,
    "\t" * 5000,
    "word " * 6000,
    "x" * 40000,
    "| a | b |\n|---|---|\n| 1 | 2 |\n\n" * 300,
    "line one\r\n\r\nline two\r\n\r\n" * 300,
    "## h\n\n" * 2000,
)

ADVERSARIAL_QUERIES: tuple[str, ...] = (
    "",
    "   ",
    "market size",
    "—",
    "\x00",
    "2024 2025 $100 50%",
    "Scope 3 emissions Section 301",
    "a" * 500,
    "🎯 emoji query",
)


def _fits(result: SelectionResult, budget: int) -> bool:
    """A result is in budget when it did not exceed it. ``verbatim`` is exempt
    only because it means the whole document was already under budget."""
    if result.strategy == "verbatim":
        return result.chars_out <= result.chars_in
    return result.chars_out <= budget


# ─────────────────────────────────────────────────────────────────────────────
# The headline test — does the fix actually fix the audit's complaint?
# ─────────────────────────────────────────────────────────────────────────────


class TestTheAuditsActualComplaint:
    """§4.7: a blind head-slice retains front matter and discards the evidence.

    These are the tests that would fail if fix 2.2 were reverted. Everything
    else in this file protects them from regressing for a subtler reason.
    """

    BUDGET = 4000

    def test_head_slice_retains_none_of_the_evidence(self):
        """Establish the baseline the audit measured, so the delta is real.

        This test asserts the *old* behaviour is bad. It exists so that the
        next test's pass is meaningful rather than vacuous — without it, a
        selector that simply returned the whole document would also "pass".
        """
        head = LONG_DOC[: self.BUDGET]
        assert len(LONG_DOC) > self.BUDGET, "fixture must exceed the budget"
        for marker in EVIDENCE_MARKERS:
            assert marker not in head, (
                f"fixture is wrong: {marker!r} must live beyond the head-slice "
                "boundary for this test to model the audit's finding"
            )
        assert "Copyright" in head, "head-slice should retain the boilerplate"

    def test_reranked_selection_retains_all_of_the_evidence(self):
        """The fix: same budget, evidence retained instead of boilerplate."""
        result = select_relevant_content(LONG_DOC, QUERY, budget_chars=self.BUDGET)
        assert result.strategy == "reranked"
        assert not result.degraded
        for marker in EVIDENCE_MARKERS:
            assert marker in result.content, f"lost evidence marker {marker!r}"

    def test_the_table_survives_intact(self):
        """A table split from its header row is not a table.

        §4.7 calls tables out specifically ("the tables and conclusions … are
        discarded"), and §3.5/§12 note that ``chart_specs.mine_chart_specs``
        mines numbers out of retained content and correctly returns ``[]``
        rather than inventing them. A half-table yields nothing minable, so
        "the table survived" has to mean every row, not just the caption.
        """
        result = select_relevant_content(LONG_DOC, QUERY, budget_chars=self.BUDGET)
        for row in ("| Grid storage | 0.4 | 1.1 | 2.9 |",
                    "| Two/three-wheeler | 0.3 | 0.9 | 2.1 |",
                    "| Passenger BEV | 0.2 | 0.7 | 1.8 |"):
            assert row in result.content, f"table row lost: {row!r}"
        assert "Source: IEI modelling." in result.content

    def test_boilerplate_loses_to_evidence_under_a_tight_budget(self):
        """With room for only a couple of chunks, evidence must win.

        The 4000-char budget above is roomy enough that a merely *adequate*
        ranker passes. This one leaves room for ~2 chunks out of 15, so it
        fails unless the ranking genuinely prefers the quantitative passages.
        """
        result = select_relevant_content(LONG_DOC, QUERY, budget_chars=900)
        assert result.strategy == "reranked"
        assert "412 million" in result.content or "| Grid storage" in result.content
        assert "Acknowledgements" not in result.content
        assert "Table of Contents" not in result.content

    def test_a_ten_k_style_document_surfaces_its_segment_tables(self):
        """The 10-K case (§4.7 + sub_agent's SEC path).

        A 10-K's opening is a cover page and a cross-reference table; the
        segment revenue table is tens of thousands of characters in. This is a
        second, independently-shaped document so the fix is not tuned to the
        one fixture above.
        """
        ten_k = "\n\n".join([
            "UNITED STATES SECURITIES AND EXCHANGE COMMISSION\nWashington, D.C. 20549\n"
            "FORM 10-K\nAnnual report pursuant to Section 13 or 15(d).",
            "Indicate by check mark whether the registrant is a large accelerated filer, "
            "an accelerated filer, a non-accelerated filer, or a smaller reporting "
            "company. " * 40,
            "## Item 1A. Risk Factors\nOur business is subject to numerous risks and "
            "uncertainties that could materially affect our results. " * 60,
            "## Item 7. Management's Discussion and Analysis\n"
            "Segment revenue for the year: Energy Storage USD 6.04 billion, up 54% "
            "year over year; Automotive USD 82.4 billion, up 15%.",
            "| Segment | FY2023 | FY2024 | Change |\n|---|---|---|---|\n"
            "| Energy Storage | 3.91 | 6.04 | +54% |\n"
            "| Automotive | 71.6 | 82.4 | +15% |",
        ])
        result = select_relevant_content(
            ten_k, "energy storage segment revenue growth", budget_chars=2200
        )
        assert "6.04" in result.content
        assert "| Energy Storage | 3.91 | 6.04 | +54% |" in result.content


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────


class TestChunking:
    """A chunker that loses text is a head-slice wearing a costume."""

    SHAPES: dict[str, str] = {
        "markdown headings": "# A\n\npara one. " * 60 + "\n\n## B\n\npara two. " * 60,
        "blank lines only": ("Some paragraph text here. " * 20 + "\n\n") * 30,
        "no boundaries at all": "word " * 6000,
        "one enormous token": "x" * 40000,
        "tables and tabs": "| a | b |\n|---|---|\n| 1 | 2 |\n\n" * 200,
        "crlf line endings": "line one\r\n\r\nline two\r\n\r\n" * 300,
    }

    @pytest.mark.parametrize("name", sorted(SHAPES))
    def test_chunking_is_lossless(self, name):
        """Every non-whitespace character of the input survives, in order.

        Whitespace-insensitive because chunking normalises separators; the
        point is that no *content* is dropped. If it were, this module would be
        reintroducing the audit's finding rather than fixing it — just less
        visibly, because the loss would no longer be a clean prefix.
        """
        doc = self.SHAPES[name]
        rejoined = "".join(c.text for c in chunk_content(doc))
        assert re.sub(r"\s+", "", rejoined) == re.sub(r"\s+", "", doc)

    @pytest.mark.parametrize("name", sorted(SHAPES))
    def test_chunk_offsets_are_monotonic(self, name):
        chunks = chunk_content(self.SHAPES[name])
        assert [c.index for c in chunks] == sorted(c.index for c in chunks)
        assert all(a.start <= b.start for a, b in zip(chunks, chunks[1:], strict=False))

    def test_empty_input_yields_no_chunks(self):
        assert chunk_content("") == []
        assert chunk_content("   \n\n  ") == []

    def test_headings_are_captured_as_labels(self):
        chunks = chunk_content(LONG_DOC)
        headings = {c.heading for c in chunks if c.heading}
        assert "Conclusions" in headings
        assert any("Nigeria Market Sizing" in h for h in headings)

    def test_no_runt_chunks(self):
        """A bare heading must not compete for budget as if it were a passage."""
        doc = "# H1\n\n## H2\n\n### H3\n\n" + ("Real body content here. " * 200)
        chunks = chunk_content(doc)
        # The only chunk permitted below the merge floor is a lone final one.
        assert sum(1 for c in chunks if c.length < 200) <= 1

    def test_oversized_block_is_split_on_sentence_boundaries(self):
        doc = ("This is a complete sentence about the market. " * 400)
        chunks = chunk_content(doc, target_chars=1000)
        assert len(chunks) > 1
        # No chunk should start mid-sentence (i.e. with a lowercase letter)
        # when sentence boundaries were available to split on.
        starts_lower = [c for c in chunks[1:] if c.text[:1].islower()]
        assert not starts_lower, f"{len(starts_lower)} chunks start mid-sentence"

    def test_a_single_unsplittable_sentence_is_hard_cut_not_dropped(self):
        doc = "y" * 30000
        chunks = chunk_content(doc, target_chars=1000)
        assert len(chunks) > 1
        assert sum(c.length for c in chunks) == len(doc)


# ─────────────────────────────────────────────────────────────────────────────
# Reranking
# ─────────────────────────────────────────────────────────────────────────────


class TestReranking:
    def test_on_topic_quantitative_chunk_outranks_boilerplate(self):
        chunks = chunk_content(LONG_DOC)
        ranked = rerank_chunks(QUERY, chunks)
        best = ranked[0]
        assert best.score > 0
        assert "Acknowledgements" not in best.text
        assert "Copyright" not in best.text

    def test_term_saturation_stops_keyword_spam_from_winning(self):
        """BM25's ``k1``: 40 repetitions must not beat one real statement.

        A navigation sidebar or tag cloud repeating the subject is the common
        real-world case here, and linear term-frequency scoring — what a naive
        ``content.count(word)`` gives — ranks it top.
        """
        spam = "lithium " * 300
        real = (
            "The Nigerian lithium-ion battery market reached USD 412 million "
            "in 2024, growing at a 21.4% CAGR through 2030."
        ) + (" Additional context follows. " * 20)
        chunks = chunk_content(f"{spam}\n\n{real}")
        ranked = rerank_chunks("Nigeria lithium battery market size CAGR", chunks)
        assert "412 million" in ranked[0].text, (
            "keyword spam outranked a real quantitative statement — term "
            "saturation is not being applied"
        )

    def test_length_normalisation_stops_volume_from_winning(self):
        """BM25's ``b``: without it every long chunk wins and the reranker
        degenerates into a slower head-slice."""
        bulky = "The market is considered to be growing steadily. " * 200
        tight = "Nigeria battery market: USD 412 million, 21.4% CAGR to 2030."
        chunks = chunk_content(f"{bulky}\n\n{tight}\n\n{bulky}")
        ranked = rerank_chunks("Nigeria battery market CAGR", chunks)
        assert "412 million" in ranked[0].text

    def test_evidence_boost_breaks_ties_toward_numbers(self):
        """Two equally on-topic chunks: the citable one should win."""
        vague = "The Nigeria battery market has grown considerably in recent years. " * 6
        exact = "The Nigeria battery market reached USD 412 million, a 21.4% CAGR. " * 6
        chunks = chunk_content(f"{vague}\n\n{exact}")
        ranked = rerank_chunks("Nigeria battery market", chunks)
        assert "412 million" in ranked[0].text

    def test_evidence_boost_cannot_override_topical_relevance(self):
        """A chunk of unrelated numbers must not beat an on-topic passage.

        The boost is additive and capped precisely so this cannot happen — an
        uncapped boost would turn the selector into a numeral detector and
        happily retain a stock ticker table from a page's sidebar.
        """
        off_topic_numbers = (
            "| Ticker | Price | Change |\n|---|---|---|\n"
            "| AAPL | 231.40 | +1.2% |\n| MSFT | 419.20 | -0.4% |\n"
            "| NVDA | 132.65 | +3.8% |\n"
        ) * 4
        on_topic = (
            "Nigeria's lithium-ion battery market size and its CAGR to 2030 are "
            "the subject of this section of the Nigeria battery outlook. " * 6
        )
        chunks = chunk_content(f"{off_topic_numbers}\n\n{on_topic}")
        ranked = rerank_chunks(QUERY, chunks)
        assert "Nigeria" in ranked[0].text
        assert "AAPL" not in ranked[0].text

    def test_ranking_is_deterministic(self):
        chunks = chunk_content(LONG_DOC)
        first = [c.index for c in rerank_chunks(QUERY, chunks)]
        for _ in range(4):
            again = [c.index for c in rerank_chunks(QUERY, chunk_content(LONG_DOC))]
            assert again == first

    def test_ties_break_on_document_order(self):
        """Determinism requires a total order; without this, fix 5.2's
        golden-PDF regression test cannot be pinned."""
        identical = "Neutral filler sentence with no query terms at all. " * 30
        chunks = chunk_content("\n\n".join([identical] * 4))
        ranked = rerank_chunks("completely unrelated query terms", chunks)
        scores = [c.score for c in ranked]
        if len(set(scores)) == 1:
            assert [c.index for c in ranked] == sorted(c.index for c in ranked)

    def test_empty_inputs(self):
        assert rerank_chunks(QUERY, []) == []
        chunks = chunk_content(LONG_DOC)
        assert len(rerank_chunks("", chunks)) == len(chunks)


# ─────────────────────────────────────────────────────────────────────────────
# Tokenisation — the §4.8 defect must not be reintroduced
# ─────────────────────────────────────────────────────────────────────────────


class TestTokenization:
    @pytest.mark.parametrize(
        ("needle", "haystack"),
        [
            ("ai", "said chain maintain"),
            ("ai", "the campaign remained available"),
            ("eu", "queue euphemism"),
            ("us", "focus bonus census"),
        ],
    )
    def test_token_boundary_not_substring(self, needle, haystack):
        """§4.8: ``if word in content_lower`` makes ``"ai"`` match ``"said"``.

        That defect systematically inflates relevance. Reintroducing it *here*
        would be worse than leaving it in the scorer, because it would misrank
        chunk selection first and then inflate the score of whatever survived.
        """
        assert needle not in tokenize(haystack)

    def test_qualifying_digits_survive(self):
        """``query_utils.normalize_query`` goes to some trouble to preserve
        these (§4.1); a tokenizer that drops them undoes that upstream work."""
        tokens = tokenize("Scope 3 emissions under Section 301 by 2030")
        for expected in ("scope", "3", "section", "301", "2030"):
            assert expected in tokens

    def test_stop_words_removed(self):
        tokens = tokenize("the market is in the region of a billion")
        assert "the" not in tokens
        assert "billion" in tokens

    def test_decimals_survive_as_single_tokens(self):
        assert "21.4" in tokenize("a 21.4% CAGR")

    @pytest.mark.parametrize("text", ADVERSARIAL_CONTENT)
    def test_tokenize_never_raises(self, text):
        assert isinstance(tokenize(text), list)


# ─────────────────────────────────────────────────────────────────────────────
# Assembly contract
# ─────────────────────────────────────────────────────────────────────────────


class TestAssemblyContract:
    @pytest.mark.parametrize("budget", [200, 300, 1000, 5000, 15000])
    def test_budget_is_never_exceeded(self, budget):
        """Including separators. A "15000-char" budget that ships
        15000 + n_chunks * 2 silently overruns the context window it exists to
        protect."""
        result = select_relevant_content(LONG_DOC, QUERY, budget_chars=budget)
        assert _fits(result, budget)
        assert len(result.content) <= budget

    def test_output_is_in_document_order(self):
        """Selection is by score; *output* is by position.

        A relevance-sorted jumble contains the identical characters but is
        measurably worse input for the LLM that consumes it — sentences arrive
        without their antecedents.
        """
        result = select_relevant_content(LONG_DOC, QUERY, budget_chars=4000)
        assert result.kept_indices == sorted(result.kept_indices)
        # And the text really is ordered, not merely the index list.
        pos_chapter3 = result.content.find("Chapter 3")
        pos_conclusions = result.content.find("Conclusions")
        assert -1 < pos_chapter3 < pos_conclusions

    def test_lead_chunk_is_always_kept(self):
        """The opening carries the thesis and the entity naming that makes the
        rest interpretable. Retained numbers with no stated subject are not
        evidence, they are trivia."""
        result = select_relevant_content(LONG_DOC, QUERY, budget_chars=4000)
        assert 0 in result.kept_indices

    def test_lead_chunk_kept_even_when_it_scores_zero(self):
        lead = "An entirely unrelated opening paragraph about nothing at all. " * 12
        body = "Nigeria battery market USD 412 million 21.4% CAGR 2030. " * 60
        result = select_relevant_content(f"{lead}\n\n{body}", QUERY, budget_chars=1500)
        assert 0 in result.kept_indices

    def test_lead_chunk_can_be_disabled(self):
        result = select_relevant_content(
            LONG_DOC, QUERY, budget_chars=900, always_keep_lead=False
        )
        assert result.strategy == "reranked"

    def test_content_within_budget_is_returned_verbatim(self):
        """No selection is needed, and reporting it as ``degraded`` would make
        the fix-2.6 yield metric read as if something went wrong."""
        short = "A short document about the Nigeria battery market."
        result = select_relevant_content(short, QUERY, budget_chars=DEFAULT_BUDGET_CHARS)
        assert result.content == short
        assert result.strategy == "verbatim"
        assert not result.degraded
        assert result.retention == 1.0

    def test_no_query_degrades_to_head_slice_and_says_so(self):
        """Honest degradation. With no query there is nothing to rank against;
        silently "reranking" against nothing would be the §0 failure mode —
        a broken path that looks identical to a working one."""
        result = select_relevant_content(LONG_DOC, "", budget_chars=4000)
        assert result.strategy == "head_slice"
        assert result.degraded
        assert result.reason
        assert result.content == LONG_DOC[:4000]

    def test_retention_is_reported(self):
        result = select_relevant_content(LONG_DOC, QUERY, budget_chars=4000)
        assert 0.0 < result.retention < 1.0
        assert result.chars_in == len(LONG_DOC)
        assert result.chars_out == len(result.content)

    def test_to_dict_is_serialisable_provenance(self):
        d = select_relevant_content(LONG_DOC, QUERY, budget_chars=4000).to_dict()
        for key in ("strategy", "degraded", "chunks_total", "chunks_kept",
                    "chars_in", "chars_out", "retention", "kept_indices"):
            assert key in d

    def test_selection_is_deterministic(self):
        outputs = {
            select_relevant_content(LONG_DOC, QUERY, budget_chars=4000).content
            for _ in range(5)
        }
        assert len(outputs) == 1

    def test_string_wrapper_matches_the_full_api(self):
        assert select_content(LONG_DOC, QUERY, budget_chars=4000) == (
            select_relevant_content(LONG_DOC, QUERY, budget_chars=4000).content
        )

    def test_single_chunk_document_degrades_rather_than_returning_nothing(self):
        """One paragraph larger than the whole budget: head-slice is genuinely
        the only option, and it must be taken rather than returning ``""``."""
        doc = "z" * 5000
        result = select_relevant_content(doc, QUERY, budget_chars=1000)
        assert len(result.content) == 1000
        assert result.degraded


# ─────────────────────────────────────────────────────────────────────────────
# Never-raises / never-empty — the §0 lesson
# ─────────────────────────────────────────────────────────────────────────────


class TestNeverRaisesNeverSilentlyEmpty:
    """§0: a single regex character zeroed all research for months because the
    failure was silent. This module's failures must be loud and bounded."""

    @pytest.mark.parametrize("content", ADVERSARIAL_CONTENT)
    @pytest.mark.parametrize("query", ADVERSARIAL_QUERIES)
    def test_never_raises(self, content, query):
        result = select_relevant_content(content, query, budget_chars=500)
        assert isinstance(result, SelectionResult)
        assert isinstance(result.content, str)
        assert _fits(result, 500)

    @pytest.mark.parametrize("content", ADVERSARIAL_CONTENT)
    def test_non_empty_input_yields_non_empty_output(self, content):
        """Whatever else happens, content in ⇒ content out. Returning ``""``
        for a page that extracted fine is the shape of the P0 outage."""
        if not content.strip():
            pytest.skip("empty input legitimately yields empty output")
        result = select_relevant_content(content, "market size data", budget_chars=500)
        assert result.content.strip()

    def test_internal_failure_degrades_to_head_slice_and_logs_loudly(self, monkeypatch, caplog):
        """Fix 0.3 discipline: no bare ``except: pass``.

        A bug inside the selector must cost retrieval *quality*, never
        retrieval *entirely*, and it must be visible in the logs and in
        ``degraded`` so it cannot hide the way the P0 did.
        """
        import hyperion.tools.content_selector as cs

        def boom(*_a, **_k):
            raise RuntimeError("synthetic rerank failure")

        monkeypatch.setattr(cs, "rerank_chunks", boom)
        with caplog.at_level(logging.WARNING, logger="hyperion.tools.content_selector"):
            result = cs.select_relevant_content(LONG_DOC, QUERY, budget_chars=4000)

        assert result.degraded
        assert result.strategy == "head_slice"
        assert result.content == LONG_DOC[:4000]
        assert "synthetic rerank failure" in result.reason
        assert any(r.levelno >= logging.WARNING for r in caplog.records)

    def test_zero_and_negative_budgets_do_not_crash(self):
        for budget in (0, -1, -10000):
            result = select_relevant_content(LONG_DOC, QUERY, budget_chars=budget)
            assert isinstance(result.content, str)

    def test_chunk_dataclass_is_well_formed(self):
        c = Chunk(text="abc", index=0, start=0, end=3)
        assert c.length == 3
        assert c.to_dict()["index"] == 0
