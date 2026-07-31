"""P2-19 / P2-20 / P2-21 (gates P2-G20 / P2-G21 / P2-G22): the Fact
Checker's three verification defects and their fixes.

P2-19 — the hallucination detector was a false-positive factory. A claim
whose sources all carried an empty ``key_data`` (the normal case for a
SERP-snippet corpus) had every word checked against ``""``, matched
nothing, and was labelled a hallucinated citation. The "17 hallucinated
citations" in report A measured missing snippets, not model invention.
After the fix there is a distinct ``UNVERIFIABLE`` state: chain validation
only runs on non-empty fetched content, matching is token-boundary (the
``evidence_scorer`` matcher, not substring), a hallucination needs TWO
independent signals (no overlap AND a dead URL), and ``UNVERIFIABLE`` is
never aggregated into the hallucination count.

P2-20 — the Fact Checker ran FAST-only and could not be escalated. After
the fix a stage-2 STRONG-tier re-adjudication re-judges only the claims
stage 1 flagged, and its verdict is the ONLY authority for
``HALLUCINATED`` / ``CONTRADICTED``.

P2-21 — contradiction detection compared metadata strings ("Confidence:
low" vs "Confidence: low") and resolved ties at weight 0.00. After the
fix a contradiction requires a shared subject AND a numeric conflict,
0.00-weight resolutions are suppressed, and the appendix table caps at 10.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hyperion.agents.support.fact_checker import FactChecker
from hyperion.schemas.models import (
    Claim,
    ClaimStatus,
    ClaimType,
    Source,
    SourceCredibility,
)


def _source(key_data: str | None = None, url: str = "https://example.com/a") -> Source:
    return Source(
        id="src_1",
        title="Example",
        url=url,
        credibility=SourceCredibility.INDUSTRY_REPORT,
        key_data=key_data,
    )


def _claim(
    text: str,
    sources: list[Source],
    status: ClaimStatus = ClaimStatus.PLAUSIBLE,
    agent: str = "market_analyst",
) -> Claim:
    return Claim(
        id="c1",
        agent=agent,
        claim=text,
        claim_type=ClaimType.NUMBER,
        status=status,
        verification_sources=sources,
    )


def _make_checker() -> FactChecker:
    # bus=None / router=None: FactChecker.__init__ stores these; no I/O at
    # construction time (same pattern as tests/test_fact_checker_query.py).
    return FactChecker(bus=None, router=None)


# ─────────────────────────────────────────────────────────────────────────────
# P2-19 — UNVERIFIABLE as a distinct state, never HALLUCINATED on empty data
# ─────────────────────────────────────────────────────────────────────────────


class TestUnverifiableStatus:
    def test_unverifiable_status_exists(self):
        """ClaimStatus gains a third state distinct from HALLUCINATED."""
        assert ClaimStatus.UNVERIFIABLE.value == "unverifiable"
        assert ClaimStatus.HALLUCINATED.value == "hallucinated"

    def test_empty_key_data_is_unverifiable_never_hallucinated(self):
        """P2-G20: all sources have empty key_data → UNVERIFIABLE, and the
        claim is excluded from the hallucinated list entirely."""
        checker = _make_checker()
        claim = _claim("The market is worth $5.2 billion", [_source(key_data=None)])
        _, hallucinated = asyncio.run(checker._validate_evidence_chains([claim]))
        assert claim.is_hallucinated_citation is False
        assert hallucinated == []

    def test_all_empty_string_key_data_is_unverifiable(self):
        """The Bing-only corpus case: key_data is an empty string, not None."""
        checker = _make_checker()
        claim = _claim(
            "Revenue grew 14 percent", [_source(key_data=""), _source(key_data="")]
        )
        _, hallucinated = asyncio.run(checker._validate_evidence_chains([claim]))
        assert claim.is_hallucinated_citation is False
        assert hallucinated == []

    def test_no_sources_is_unverifiable_not_hallucinated(self):
        """'We did not look' (zero sources) is not 'the model invented a
        citation'. Must not be flagged hallucinated."""
        checker = _make_checker()
        claim = _claim("The sector employs 12000 people", [])
        _, hallucinated = asyncio.run(checker._validate_evidence_chains([claim]))
        assert claim.is_hallucinated_citation is False
        assert hallucinated == []


class TestTokenBoundaryMatching:
    """P2-19 fix 2: substring matching with a 4-char floor matched "market"
    inside "supermarket" and "rate" inside "corporate". The fix reuses
    evidence_scorer's token-boundary matcher, so those stop matching."""

    def test_substring_words_do_not_support_a_claim(self):
        checker = _make_checker()
        # "market" is a substring of "supermarket"; "rate" of "corporate".
        # Under substring matching these matched; token-boundary they do not.
        claim = _claim(
            "The market growth rate reached 9 percent",
            [_source(key_data="The supermarket reported corporate earnings.")],
        )
        checker._url_alive = AsyncMock(return_value=False)
        _, hallucinated = asyncio.run(checker._validate_evidence_chains([claim]))
        assert claim.is_hallucinated_citation is True

    def test_genuine_token_overlap_supports_claim(self):
        checker = _make_checker()
        claim = _claim(
            "The lithium market grew 20 percent",
            [_source(key_data="Lithium market growth was 20 percent in 2024.")],
        )
        _, hallucinated = asyncio.run(checker._validate_evidence_chains([claim]))
        assert claim.is_hallucinated_citation is False
        assert hallucinated == []


class TestTwoSignalHallucination:
    """P2-19 fix 3: a hallucinated citation needs TWO independent signals —
    no numeric/named-entity overlap AND a dead URL. Either alone is
    insufficient, so a live source with sparse text is UNVERIFIABLE."""

    def test_no_overlap_but_live_url_is_unverifiable(self):
        checker = _make_checker()
        claim = _claim(
            "The lithium market grew 20 percent",
            [_source(key_data="An unrelated article about cooking recipes.")],
        )
        checker._url_alive = AsyncMock(return_value=True)  # URL is alive
        _, hallucinated = asyncio.run(checker._validate_evidence_chains([claim]))
        assert claim.is_hallucinated_citation is False
        assert hallucinated == []

    def test_no_overlap_and_dead_url_is_hallucinated(self):
        checker = _make_checker()
        claim = _claim(
            "The lithium market grew 20 percent",
            [_source(key_data="An unrelated article about cooking recipes.")],
        )
        checker._url_alive = AsyncMock(return_value=False)  # dead URL
        _, hallucinated = asyncio.run(checker._validate_evidence_chains([claim]))
        assert claim.is_hallucinated_citation is True
        assert claim in hallucinated


# ─────────────────────────────────────────────────────────────────────────────
# P2-20 — stage-2 STRONG re-adjudication is the only authority
# ─────────────────────────────────────────────────────────────────────────────


class TestStageTwoReAdjudication:
    def test_stage2_re_adjudicates_only_flagged_claims(self):
        """Stage 2 runs on STRONG tier over ONLY the claims stage 1 flagged
        (HALLUCINATED / CONTRADICTED), not the full set."""
        checker = _make_checker()
        flagged = _claim(
            "The market is worth $5.2 billion",
            [_source(key_data="unrelated text")],
        )
        flagged.is_hallucinated_citation = True
        clean = _claim("Revenue grew 14 percent", [_source(key_data="grew 14%")])
        clean.is_hallucinated_citation = False

        captured: dict = {}
        response = SimpleNamespace(
            content='{"verdict": "verified", "reason": "source confirms it"}'
        )

        async def fake_complete(**kwargs):
            captured.update(kwargs)
            return response

        checker.router = SimpleNamespace(complete=fake_complete)

        asyncio.run(checker._stage2_readjudicate([flagged, clean]))

        tier = captured.get("tier")
        assert tier is not None, "stage 2 must escalate through the router"
        assert tier.value == "strong", f"stage 2 must run STRONG, got {tier}"
        assert captured.get("urgency") is not None

    def test_stage2_verdict_overrides_stage1_flag(self):
        """A STRONG-tier verdict of 'verified' clears the stage-1
        hallucination flag: stage 2 is authoritative."""
        checker = _make_checker()
        flagged = _claim(
            "The market is worth $5.2 billion",
            [_source(key_data="unrelated text")],
        )
        flagged.is_hallucinated_citation = True

        response = SimpleNamespace(
            content='{"verdict": "verified", "reason": "confirmed"}'
        )
        checker.router = SimpleNamespace(complete=AsyncMock(return_value=response))

        asyncio.run(checker._stage2_readjudicate([flagged]))

        assert flagged.is_hallucinated_citation is False
        assert flagged.status == ClaimStatus.VERIFIED

    def test_stage2_confirms_hallucination_when_upheld(self):
        """When STRONG tier confirms the invention, the flag stands."""
        checker = _make_checker()
        flagged = _claim(
            "The market is worth $5.2 billion",
            [_source(key_data="unrelated text")],
        )
        flagged.is_hallucinated_citation = True

        response = SimpleNamespace(
            content='{"verdict": "hallucinated", "reason": "invented figure"}'
        )
        checker.router = SimpleNamespace(complete=AsyncMock(return_value=response))

        asyncio.run(checker._stage2_readjudicate([flagged]))

        assert flagged.is_hallucinated_citation is True
        assert flagged.status == ClaimStatus.HALLUCINATED


# ─────────────────────────────────────────────────────────────────────────────
# P2-21 — contradiction detection requires shared subject + numeric conflict
# ─────────────────────────────────────────────────────────────────────────────


class TestContradictionDetection:
    def test_no_numeric_conflict_no_contradiction(self):
        """Two findings with no numeric/categorical conflict never produce a
        contradiction row, even if they share keywords."""
        checker = _make_checker()
        a = _claim("The lithium market outlook is positive", [], agent="market_analyst")
        b = _claim("The lithium market faces headwinds", [], agent="risk_analyst")
        a.claim_type = ClaimType.RELATIONSHIP
        b.claim_type = ClaimType.RELATIONSHIP
        assert checker._claims_conflict(a, b) is False

    def test_confidence_metadata_strings_never_conflict(self):
        """P2-G22: two 'Confidence: low' metadata strings are NOT a
        contradiction. No shared quantity + numeric conflict → no row."""
        checker = _make_checker()
        a = _claim("Confidence: low", [], agent="market_analyst")
        b = _claim("Confidence: low", [], agent="risk_analyst")
        assert checker._claims_conflict(a, b) is False

    def test_shared_subject_and_numeric_conflict_is_contradiction(self):
        """Same metric, non-overlapping values → a genuine contradiction."""
        checker = _make_checker()
        a = _claim("Global lithium demand reached 1200000 tonnes", [], agent="market_analyst")
        b = _claim("Global lithium demand reached 450000 tonnes", [], agent="supply_analyst")
        assert checker._claims_conflict(a, b) is True

    def test_different_metrics_no_contradiction(self):
        """Different quantities (demand vs price) with different numbers are
        NOT a contradiction — no shared subject."""
        checker = _make_checker()
        a = _claim("Lithium demand reached 1200000 tonnes", [], agent="market_analyst")
        b = _claim("Lithium carbonate price hit 80000 dollars", [], agent="supply_analyst")
        assert checker._claims_conflict(a, b) is False

    def test_same_value_no_contradiction(self):
        """Two claims citing the SAME number for the same metric agree."""
        checker = _make_checker()
        a = _claim("Global lithium demand reached 1200000 tonnes", [], agent="market_analyst")
        b = _claim("Global lithium demand reached 1200000 tonnes", [], agent="supply_analyst")
        assert checker._claims_conflict(a, b) is False


class TestContradictionResolutionSuppression:
    """P2-21 fixes 3-4: 0.00-weight resolutions are suppressed and the
    appendix table caps at the 10 highest-weight contradictions."""

    def test_zero_weight_resolution_suppressed(self):
        checker = _make_checker()
        rows = [
            {"weight": 0.0, "finding_a": "A", "finding_b": "B"},
            {"weight": 0.62, "finding_a": "C", "finding_b": "D"},
        ]
        kept = checker._filter_contradiction_rows(rows)
        assert all(r["weight"] > 0.0 for r in kept)
        assert len(kept) == 1

    def test_appendix_table_capped_at_ten(self):
        checker = _make_checker()
        rows = [
            {"weight": float(20 - i), "finding_a": f"A{i}", "finding_b": f"B{i}"}
            for i in range(25)
        ]
        kept = checker._filter_contradiction_rows(rows)
        assert len(kept) <= 10
        # Highest-weight first.
        assert kept[0]["weight"] >= kept[-1]["weight"]
