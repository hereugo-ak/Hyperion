"""P2-23 / P2-G15 — `max_iterations_reached` must never bypass an
integrity blocker.

ORIGINAL FINDING (P2-23). `presentation_designer.py:3038-3059` had a single
`max_iterations_reached` escape hatch that converted ANY non-approval
(cosmetic gap or hard integrity blocker alike) into "proceeding with best
report (escalation)". Since the orchestrator sets
`max_iterations_reached=True` on every non-approved run that reaches the
iteration cap, that override was the *normal* path — it is how both audited
PDFs, which contained leaked Python dicts and banned filler, still shipped.

WHY THIS FILE WAS REWRITTEN (W-08 step 4). The original version of this test
asserted the *Presentation Designer* refuses. W-08 step 4 explicitly deletes
the designer's quality evaluation:

    "Delivery must not evaluate quality at all. The orchestrator decides;
     the designer either receives a report to lay out or is never invoked.
     Remove the check rather than repairing it, because a second quality
     decision point is a second escape hatch."

So asserting a designer-side refusal now asserts the presence of the exact
second decision point W-08 removed. The invariant is unchanged and is still
the point of this file; only its enforcement site moved. This file now pins
the invariant where W-08 put it:

1. `WorkflowEngine._compute_quality_terminal_state` returns BLOCKED whenever
   `integrity_blockers` is non-empty, regardless of `total_score`,
   `approved`, or `max_iterations_reached`.
2. `max_iterations_reached` appears in no ship condition.
3. The designer source contains no quality-approval branch at all.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from hyperion.agents.delivery.presentation_designer import PresentationDesigner
from hyperion.orchestrator import WorkflowEngine
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    FinalReport,
    QualityDimension,
    QualityDimensionName,
    QualityScore,
    QualityTerminalState,
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
    """The exact pathological case from P2-23: max iterations reached, the
    score comfortably ABOVE the ship floor and the approval threshold, and a
    non-negotiable integrity blocker present. This must never ship.
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


def _score_approved_at_max_iterations() -> QualityScore:
    """Approved, above threshold, no integrity blocker — but the iteration cap
    was also reached. `max_iterations_reached` must not change the outcome.
    """
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id=QualityDimensionName.TONE_AND_VOICE,
                name="Tone and Voice",
                score=5,
                weight=0.1,
                feedback="Good.",
            )
        ],
        total_score=4.4,
        approved=True,
        iteration=4,
        gaps=["Only 4 sources found; evidence is thin."],
        integrity_blockers=[],
        max_iterations_reached=True,
    )


class TestIntegrityBlockerNeverBypassed:
    def test_integrity_blocker_forces_blocked_even_above_threshold(self):
        engine = WorkflowEngine()
        score = _score_with_integrity_blocker_at_max_iterations()
        engine._compute_quality_terminal_state(score)
        assert score.terminal_state == QualityTerminalState.BLOCKED
        assert "integrity blocker" in (score.blocked_reason or "").lower()

    def test_max_iterations_alone_does_not_block_an_approved_run(self):
        """The blocker, not the iteration count, is what refuses to ship."""
        engine = WorkflowEngine()
        score = _score_approved_at_max_iterations()
        engine._compute_quality_terminal_state(score)
        assert score.terminal_state == QualityTerminalState.APPROVED

    def test_max_iterations_reached_is_read_by_no_ship_condition(self):
        """W-08 step 2: iteration exhaustion sets a diagnostic field only.

        Every read of ``max_iterations_reached`` in the orchestrator must be a
        write (``x.max_iterations_reached = True``) or a diagnostic/report
        serialization — never a branch condition.
        """
        src = Path(WorkflowEngine.__module__.replace(".", "/") + ".py")
        text = src.read_text(encoding="utf-8")
        tree = ast.parse(text)

        branch_reads: list[int] = []

        class _Visitor(ast.NodeVisitor):
            def _scan_test(self, node: ast.AST) -> None:
                for sub in ast.walk(node):
                    if (
                        isinstance(sub, ast.Attribute)
                        and sub.attr == "max_iterations_reached"
                    ):
                        branch_reads.append(sub.lineno)

            def visit_If(self, node: ast.If) -> None:
                self._scan_test(node.test)
                self.generic_visit(node)

            def visit_While(self, node: ast.While) -> None:
                self._scan_test(node.test)
                self.generic_visit(node)

            def visit_IfExp(self, node: ast.IfExp) -> None:
                self._scan_test(node.test)
                self.generic_visit(node)

        _Visitor().visit(tree)
        assert not branch_reads, (
            "max_iterations_reached is read in a branch condition at "
            f"{src}:{branch_reads} — W-08 step 2 forbids it in any ship decision"
        )

    def test_terminal_state_computation_ignores_max_iterations_reached(self):
        """The decision function must not reference the field at all."""
        source = inspect.getsource(WorkflowEngine._compute_quality_terminal_state)
        code_lines = [
            ln for ln in source.splitlines()
            if "max_iterations_reached" in ln and not ln.strip().startswith("#")
        ]
        # The docstring mentions it deliberately ("deliberately NOT read here").
        code_lines = [ln for ln in code_lines if "deliberately" not in ln]
        assert not code_lines, code_lines

    def test_designer_holds_no_quality_approval_branch(self):
        """W-08 step 4: delivery evaluates no quality at all."""
        src = Path(inspect.getsourcefile(PresentationDesigner) or "")
        text = src.read_text(encoding="utf-8")
        tree = ast.parse(text)
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not isinstance(node, (ast.If, ast.IfExp)):
                continue
            for sub in ast.walk(node.test):
                if isinstance(sub, ast.Attribute) and sub.attr in {
                    "approved",
                    "max_iterations_reached",
                    "integrity_blockers",
                    "terminal_state",
                }:
                    offenders.append(f"{src.name}:{sub.lineno} -> .{sub.attr}")
        assert not offenders, (
            "the Presentation Designer branches on a quality verdict; W-08 "
            f"step 4 removes the second decision point: {offenders}"
        )

    @pytest.mark.asyncio
    async def test_designer_lays_out_whatever_it_is_given(self):
        """Having no verdict of its own, the designer produces pages.

        The refusal is upstream (the orchestrator never invokes delivery on a
        BLOCKED run, asserted by tests/test_w08_quality_gate_refusal.py); the
        designer's contract is to lay out the report it receives.
        """
        designer = PresentationDesigner(bus=None)
        layout = await designer.run(
            question="Should Acme enter?",
            engagement_id="ENG-TEST",
            final_report=_minimal_report(),
            quality_score=_score_approved_at_max_iterations(),
        )
        assert layout.pages
