"""D-L (overhaul3_audit.md W1/S4c): the Quality Gate must not penalize
``visual_quality`` before delivery has run.

The 2026-08-11 blocked diagnostic scored ``visual_quality=3/5`` with rationale
"No Visualization Output received, cannot verify visual quality" and the open
gap "Run the Data Visualizer before quality gating." But DATA_VISUALIZER runs
only in Stage 5 — which is skipped entirely on a BLOCKED run — so the gate
scored a viz output that BY CONSTRUCTION does not exist at the pre-delivery
boundary. It punished the run for a stage that never ran.

Fix (D-L): the gate distinguishes its two boundaries:

- ``pre_delivery=True`` (the orchestrator's Stage-4 quality loop) — a missing
  viz output is scored as **N/A (neutral)**: the gate verifies what exists, it
  never penalizes a stage scheduled for later.
- ``pre_delivery=False`` (the re-render/validation path where delivery output
  IS expected) — the hard check survives: missing viz output is still a 3/5
  with the "No Visualization Output received" feedback.

This test reproduces the real defect: before the fix, the pre-delivery gate
returned 3/5 with the misleading "No Visualization Output received" feedback.
"""

from __future__ import annotations

from hyperion.agents.support.quality_gate import QualityGate
from hyperion.schemas.models import (
    ConfidenceLevel,
    FinalReport,
    Recommendation,
)


def _gate(pre_delivery: bool) -> QualityGate:
    gate = object.__new__(QualityGate)
    gate._pre_delivery = pre_delivery
    gate._visualization_output = None
    return gate


def _minimal_report() -> FinalReport:
    return FinalReport(
        engagement_id="ENG-TEST",
        question="Should India invest in home-grown space tech?",
        recommendation=Recommendation.INVESTIGATE,
        recommendation_rationale="insufficient evidence",
        critical_assumptions=[],
        confidence=ConfidenceLevel.LOW,
        confidence_breakdown={},
        executive_summary="Insufficient data.",
        total_sources=5,
    )


def test_pre_delivery_boundary_does_not_penalize_missing_viz() -> None:
    """D-L reproduction — the 06:47-blocked diagnostic: at the pre-delivery
    boundary a missing viz output must be N/A (neutral), never 3/5."""
    dim = _gate(pre_delivery=True)._score_visual_quality(_minimal_report())

    assert dim.score == 5, (
        "at the pre-delivery boundary a missing viz output must be N/A "
        f"(neutral), not penalized; got {dim.score}/5"
    )
    assert dim.critical is False
    assert "No Visualization Output received" not in dim.feedback, (
        "the gate must not claim the visualizer failed — it has not run yet"
    )
    assert "pre-delivery" in dim.feedback.lower() or "not scored" in dim.feedback.lower()


def test_validation_path_still_hard_checks_missing_viz() -> None:
    """The re-render/validation path (pre_delivery=False) keeps the hard
    check: there, missing viz output IS a failure."""
    dim = _gate(pre_delivery=False)._score_visual_quality(_minimal_report())

    assert dim.score == 3
    assert "No Visualization Output received" in dim.feedback
