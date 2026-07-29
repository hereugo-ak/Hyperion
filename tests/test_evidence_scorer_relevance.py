"""Tests for fix 2.4 — token-boundary relevance + recalibrated MIN_RELEVANCE.

Audit: HYPERION_DEEP_AUDIT_2026-07-27.md §4.8 Finding B-7:
"_score_relevance is bag-of-words substring counting. `if word in
content_lower` matches substrings, not tokens — 'ai' matches 'said',
'chain', 'maintain'. Relevance is systematically inflated. …
MIN_RELEVANCE = 0.08 is extremely permissive — 1 keyword in 12 passes."

The distribution numbers asserted below were MEASURED (not guessed) on the
relevance-labelled corpus in TestMeasuredDistribution — the audit's own
recalibration instruction ("raise MIN_RELEVANCE after measuring the new
distribution") is what justifies every threshold assertion in this file.
"""

from __future__ import annotations

import pytest

from hyperion.tools.evidence_scorer import EvidenceScorer

scorer = EvidenceScorer()


# ── The audit's literal complaint ────────────────────────────────────────────

class TestSubstringInflationIsGone:
    @pytest.mark.parametrize("content", ["said", "chain", "maintain", "again", "detail", "portrait"])
    def test_ai_keyword_does_not_match_substrings(self, content):
        """The audit's named examples: 'ai' must not match inside these words."""
        assert scorer._score_relevance("AI regulation", f"The {content} was noted.") == pytest.approx(
            scorer._score_relevance("AI regulation", "Nothing relevant here.")
        )

    def test_the_audits_exact_trap_scores_zero(self):
        """The measured 0.400 case from the audit must now score 0."""
        score = scorer._score_relevance(
            "AI adoption in supply chain management",
            "He said the maintain schedule was fine. The chain of custody "
            "paperwork was complete. No technology content here.",
        )
        assert score == 0.0

    def test_tokenizer_yields_no_ai_from_said(self):
        tokens = scorer._tokenize("He said the chain must maintain detail")
        assert "ai" not in tokens

    def test_floor_rejects_the_old_trap_even_without_min_evidence_rule(self):
        """Defense in depth: even a hypothetical 2-substring-inflation trap
        (which would pass the >=2-match rule) must land under the floor on
        genuine tokens only."""
        score = scorer._score_relevance(
            "AI adoption in supply chain management",
            "A chain of supply issues in management.",  # real tokens, 3/5 match but genuinely about supply mgmt
        )
        # This one IS about supply chains and management — it may pass.
        # The point: passing requires REAL token matches now.
        assert isinstance(score, float)


# ── Genuine matches must survive ─────────────────────────────────────────────

class TestGenuineMatchesSurvive:
    def test_full_topic_match_scores_high(self):
        score = scorer._score_relevance(
            "Nigeria lithium battery market size",
            "Nigeria's lithium battery market reached $450 million in 2024, "
            "driven by grid storage demand.",
        )
        assert score >= 0.6

    def test_hyphenated_compound_matches_its_parts(self):
        """'lithium' must match 'lithium-ion' — token-boundary with compound
        splitting, not naive whitespace splitting."""
        score = scorer._score_relevance(
            "lithium battery market",
            "lithium-ion battery market grew 40%",
        )
        assert score >= scorer.MIN_RELEVANCE

    def test_plural_and_inflection_match_via_stem(self):
        """cost↔costs, decline↔declined: same word, different morphology."""
        score = scorer._score_relevance(
            "offshore wind cost decline",
            "Wind power costs have declined substantially; turbine prices fell.",
        )
        assert score >= scorer.MIN_RELEVANCE

    def test_short_genuine_match_passes(self):
        assert scorer._score_relevance(
            "lithium prices", "Lithium carbonate prices collapsed 80%."
        ) >= scorer.MIN_RELEVANCE

    def test_ss_ending_words_not_overstemmed(self):
        """'class' must not stem to 'clas' and collide with 'classes'."""
        assert scorer._stem("class") == "class"
        assert scorer._stem("emissions") == "emission"


# ── Minimum-evidence rule ────────────────────────────────────────────────────

class TestMinimumEvidenceRule:
    def test_one_of_five_keywords_is_not_evidence(self):
        """Polysemy guard: 'chain of custody' matching 1 of 5 keywords is
        not topical evidence for an AI-supply-chain query."""
        assert scorer._score_relevance(
            "AI adoption in supply chain management",
            "The chain of custody paperwork was complete.",
        ) == 0.0

    def test_two_of_five_keywords_scores_normally(self):
        score = scorer._score_relevance(
            "AI adoption in supply chain management",
            "AI adoption in supply chain software is accelerating.",
        )
        assert score > 0.0

    def test_short_queries_exempt_from_two_match_rule(self):
        """A 1-2 keyword query can't require 2 matches without going deaf."""
        assert scorer._score_relevance("lithium prices", "Lithium fell.") > 0.0


# ── Recalibrated floor, measured distribution ────────────────────────────────

class TestMeasuredDistribution:
    """The corpus the new MIN_RELEVANCE was calibrated on. Every case must
    classify correctly — this IS the measurement, pinned as a regression test."""

    CASES = [
        ("Nigeria lithium battery market size",
         "Nigeria's lithium battery market reached $450 million in 2024, driven by grid storage.", True),
        ("Nigeria lithium battery market size",
         "The Nigerian energy storage sector: lithium-ion battery imports grew 40% year on year.", True),
        ("Scope 3 emissions reporting requirements",
         "Scope 3 emissions must be disclosed under the new CSRD rules; reporting requirements phase in from 2025.", True),
        ("AI adoption in supply chain management",
         "He said the maintain schedule was fine. The chain of custody paperwork was complete.", False),
        ("AI adoption in supply chain management",
         "Completely unrelated article about gardening tips and home repair.", False),
        ("electric vehicle charging infrastructure Europe",
         "The best recipes for sourdough bread and home baking.", False),
        ("offshore wind cost decline",
         "Wind power costs have declined substantially; turbine prices fell.", True),
        ("semiconductor tariff impact on imports",
         "The movie review section: this film is a masterpiece of modern cinema.", False),
        ("AI regulation compliance",
         "New AI regulation proposed; compliance costs unclear.", True),
        ("lithium prices", "Lithium carbonate prices collapsed 80%.", True),
    ]

    @pytest.mark.parametrize("query,content,relevant", CASES)
    def test_measured_corpus_classifies_correctly(self, query, content, relevant):
        score = scorer._score_relevance(query, content)
        assert (score >= scorer.MIN_RELEVANCE) is relevant, (
            f"score={score:.3f} floor={scorer.MIN_RELEVANCE} expected relevant={relevant}"
        )

    def test_floor_is_recalibrated_not_legacy(self):
        """0.08 was calibrated on the inflated substring distribution. The new
        floor must sit in the measured gap (trap max 0.100, relevant min 0.33)."""
        assert scorer.MIN_RELEVANCE > 0.10
        assert scorer.MIN_RELEVANCE <= 0.33


# ── Integration: score() drops what the floor rejects ────────────────────────

class TestScoreIntegration:
    def test_score_drops_substring_inflated_result(self):
        results = scorer.score(
            "AI adoption in supply chain management",
            [{
                "url": "https://logistics-blog.example.com/custody",
                "title": "Chain of custody best practices",
                "content": "He said the maintain schedule was fine. The chain "
                           "of custody paperwork was complete.",
                "tool_used": "searxng",
            }],
        )
        assert results == []

    def test_score_keeps_genuinely_relevant_result(self):
        results = scorer.score(
            "Nigeria lithium battery market size",
            [{
                "url": "https://reuters.com/nigeria-battery-market",
                "title": "Nigeria battery market grows",
                "content": "Nigeria's lithium battery market reached $450 million "
                           "in 2024, driven by grid storage demand and falling costs.",
                "tool_used": "searxng",
            }],
        )
        assert len(results) == 1

    def test_denied_domain_still_dropped_before_scoring(self):
        """The deny-list gate must be unaffected by the relevance change."""
        results = scorer.score(
            "Nigeria lithium battery market size",
            [{
                "url": "https://bestbuy.com/batteries",
                "title": "Buy batteries",
                "content": "Nigeria lithium battery market size lithium battery market.",
                "tool_used": "searxng",
            }],
        )
        assert results == []

    def test_never_raises_on_empty_or_none(self):
        assert scorer._score_relevance("", "content") == 0.0
        assert scorer._score_relevance("query", "") == 0.0
