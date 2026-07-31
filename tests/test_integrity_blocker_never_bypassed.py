"""P2-23 / P2-G15 — `max_iterations_reached` must never bypass an
integrity blocker.

Part 2 audit finding: `presentation_designer.py:3038-3059` had a single
`max_iterations_reached` escape hatch that converted ANY non-approval
(cosmetic gap or hard integrity blocker alike) into
"proceeding with best report (escalation)". Since the orchestrator sets
`max_iterations_reached=True` on every non-approved run that reaches the
iteration cap, that override was the *normal* path — it is how both audited
PDFs, which contained leaked Python dicts and banned filler, still shipped.

The fix partitions `QualityScore` into `gaps` (cosmetic/thin-evidence,
bypassable) and `integrity_blockers` (leaked object, banned filler, verdict
contradiction, dishonest confidence, broken URL, meta-text — never
bypassable). This test builds a QualityScore with
`max_iterations_reached=True` AND a non-empty `integrity_blockers`, and
asserts the Presentation Designer refuses to produce a real layout
(returns a LOW-confidence empty LayoutPlan) instead of proceeding.
"""

from __future__ import annotations

import pytest

from hyperion.agents.delivery.presentation_designer import PresentationDesigner
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    FinalReport,
    QualityDimension,
    QualityDimensionName,
    QualityScore,
    Recommendation,
)


def _minimal_report() -> FinalReport:
    return FinalReport(
        engagement_id="ENG-TEST",
        question="Should Acme enter the market?",
        recommendation=Recommendation.ENTER,
        recommendation_rationale="Evidence supports entry.",
        critical_assumptions=["Prices stay flat."],
        confidence=ConfidenceLevel.MEDIUM,
        confidence_breakdown={"market": ConfidenceLevel.MEDIUM},
        executive_summary="Enter the market given favorable conditions.",
        sections=[
            AnalysisSection(
                id="market_analysis",
                title="Market Sizing",
                agent="market_analyst",
                key_insight="Demand is growing.",
                body="b" * 200,
                implications="Enter now.",
                confidence=ConfidenceLevel.MEDIUM,
            )
        ],
    )


def _score_with_integrity_blocker_at_max_iterations() -> QualityScore:
    """The exact pathological case from P2-23: max iterations reached AND
    a non-negotiable integrity blocker is present. This must never ship.
    """
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id=QualityDimensionName.TONE_AND_VOICE,
                name="Tone and Voice",
                score=5,
                weight=0.1,
                feedback="Fine.",
            )
        ],
        total_score=4.5,
        approved=False,
        iteration=2,
        gaps=["LEAK: a raw Python object/dict ({'...) reached the report body"],
        integrity_blockers=["LEAK: a raw Python object/dict ({'...) reached the report body"],
        max_iterations_reached=True,  # the escalation escape hatch fired
    )


def _score_with_only_gaps_at_max_iterations() -> QualityScore:
    """Cosmetic/thin-evidence gaps with no integrity blocker MAY proceed
    past max_iterations_reached (this is the legitimate escalation path).
    """
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id=QualityDimensionName.TONE_AND_VOICE,
                name="Tone and Voice",
                score=3,
                weight=0.1,
                feedback="A bit thin.",
            )
        ],
        total_score=3.6,
        approved=False,
        iteration=2,
        gaps=["Only 4 sources found; evidence is thin."],
        integrity_blockers=[],
        max_iterations_reached=True,
    )


class TestIntegrityBlockerNeverBypassed:
    @pytest.mark.asyncio
    async def test_integrity_blocker_refuses_even_at_max_iterations(self):
        designer = PresentationDesigner(bus=None)
        layout = await designer.run(
            question="Should Acme enter?",
            engagement_id="ENG-TEST",
            final_report=_minimal_report(),
            quality_score=_score_with_integrity_blocker_at_max_iterations(),
        )
        # Must NOT have designed a real layout — no pages, LOW confidence,
        # blank-page/orphaned-image flags left at their refusal defaults.
        assert layout.confidence == ConfidenceLevel.LOW
        assert not layout.pages
        assert layout.no_blank_pages is False
        assert layout.no_orphaned_images is False

    @pytest.mark.asyncio
    async def test_cosmetic_gap_may_proceed_at_max_iterations(self):
        designer = PresentationDesigner(bus=None)
        layout = await designer.run(
            question="Should Acme enter?",
            engagement_id="ENG-TEST",
            final_report=_minimal_report(),
            quality_score=_score_with_only_gaps_at_max_iterations(),
        )
        # A cosmetic/thin-evidence gap with NO integrity blocker legitimately
        # proceeds through the escalation path and produces real pages.
        assert layout.pages
