"""D-F (overhaul3_audit.md W4/S9 + §5): the Recovery Supervisor.

A BLOCKED verdict is a diagnostic input, not an exit. ``_recover_from_blocked``
classifies each integrity blocker into a RecoveryClass, re-dispatches ONLY the
responsible agent with a blocker-specific directive (idempotent task ids),
re-scores via the existing Quality Gate authority, and commits only a strictly
improved score (monotonicity). Bounded by ``quality_recovery_max_passes``.

Fault-injection per §5.7: a report carrying ``Unknown`` in a numeric field
(→ ``DATA VOID`` blocker) must trigger exactly one recovery re-dispatch of
the owning specialist, the report is re-scored, and ``recovery_passes == 1``.

Before the fix there is no supervisor at all: ``_recover_from_blocked`` does
not exist and the BLOCKED branch terminates with a discarded diagnosis.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from hyperion.orchestrator import WorkflowEngine
from hyperion.schemas.models import (
    FinalReport,
    QualityScore,
    QualityTerminalState,
)


def _orch() -> WorkflowEngine:
    orch = WorkflowEngine.__new__(WorkflowEngine)
    orch._engagement_id = "eng_recovery_test"
    orch._all_findings = []
    orch._task_outputs = {}
    orch._manifest = None
    orch._log = MagicMock()
    orch._publish_task_update = MagicMock()
    orch.bus = MagicMock()
    orch._recovery_telemetry = {
        "attempted": False,
        "passes": 0,
        "recovered": False,
        "outcomes_by_class": {},
        "passes_detail": [],
    }
    return orch


def _blocked_score(*, blockers=None, gaps=None, total=2.0) -> QualityScore:
    return QualityScore(
        dimensions=[],
        total_score=total,
        threshold=4.0,
        approved=False,
        iteration=3,
        gaps=gaps or [],
        integrity_blockers=blockers or [],
        terminal_state=QualityTerminalState.BLOCKED,
        blocked_reason="1 integrity blocker(s): injected for the test",
    )


def _report() -> FinalReport:
    # The recovery loop only carries the report; the scorer is injected, so a
    # construct suffices — no need to materialize the full model.
    return FinalReport.model_construct(engagement_id="eng_recovery_test")


# ── §5.7 fault injection: DATA VOID → one recovery re-dispatch → re-scored ──


@pytest.mark.asyncio
async def test_data_void_triggers_one_recovery_pass_that_rescues(monkeypatch) -> None:
    """A report carrying 'Unknown' (DATA VOID blocker) triggers exactly one
    recovery re-dispatch of the owning specialist, the report is re-scored by
    the existing gate, and ``kpi_9_recovery_passes == 1`` / ``recovered``."""
    orch = _orch()
    report = _report()
    score = _blocked_score(
        blockers=[
            "DATA VOID: 'Unknown' value(s) rendered as data, omit the row or "
            "re-query; never ship 'Unknown' as a data point."
        ],
        gaps=["[Risk Coverage] No risk analysis section present."],
    )

    repaired_report = _report()
    repaired_score = _blocked_score(total=4.2)
    repaired_score.terminal_state = QualityTerminalState.APPROVED
    repaired_score.approved = True

    dispatch = AsyncMock(return_value=None)  # specialist re-dispatch outcome unused
    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._dispatch_recovery", dispatch
    )
    async def _fake_loop(self, dag, final_report, fact_check_report):
        return repaired_report, repaired_score, 1

    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._quality_iteration_loop", _fake_loop
    )

    dag = MagicMock()
    final_report, final_score = await orch._recover_from_blocked(
        dag, report, score, None,
    )

    # Exactly one recovery pass, re-dispatch of the owning specialist.
    assert dispatch.await_count == 1
    action = dispatch.await_args.args[0]  # (action, dag, pass_no)
    assert action["recovery_class"] == "PLACEHOLDER_VALUE"
    assert action["agent"].value == "risk_analyst", (
        "the owning specialist (from the critical-dimension/gap signal) must "
        "be re-dispatched — not a generic blanket re-run"
    )
    # Re-scored by the existing gate authority; the improved score is returned.
    assert final_score is repaired_score
    assert final_score.total_score == 4.2
    assert final_report is repaired_report
    # Telemetry: kpi_9 contract.
    assert orch._recovery_telemetry["attempted"] is True
    assert orch._recovery_telemetry["passes"] == 1
    assert orch._recovery_telemetry["recovered"] is True


# ── §5.3 monotonicity: a worse re-score is discarded, `best` is kept ────────


@pytest.mark.asyncio
async def test_recovery_pass_that_lowers_score_is_discarded(monkeypatch) -> None:
    """Recovery can never regress the deliverable: a re-score below `best` is
    discarded and the original BLOCKED report/score are returned."""
    orch = _orch()
    report = _report()
    score = _blocked_score(
        blockers=["VERDICT CONTRADICTION: recommendation is 'CONDITIONAL' but "
                  "the narrative contains 'no-go'. Reconcile to a single verdict."],
    )

    worse_report = _report()
    worse_score = _blocked_score(total=1.4)  # worse AND still BLOCKED

    dispatch = AsyncMock(return_value=worse_report)
    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._dispatch_recovery", dispatch
    )
    async def _fake_loop(self, dag, final_report, fact_check_report):
        return worse_report, worse_score, 1

    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._quality_iteration_loop", _fake_loop
    )

    dag = MagicMock()
    final_report, final_score = await orch._recover_from_blocked(
        dag, report, score, None,
    )

    # `best` (the original) is kept — the worse re-score never ships.
    assert final_score is score
    assert final_report is report
    assert orch._recovery_telemetry["passes"] == 1
    assert orch._recovery_telemetry["recovered"] is False
    assert orch._recovery_telemetry["outcomes_by_class"][
        "VERDICT_CONFLICT"
    ] == {"committed": 0, "discarded": 1, "skipped": 0}


# ── §5.3 bounded: max_passes = 0 disables the supervisor (old behaviour) ────


@pytest.mark.asyncio
async def test_supervisor_disabled_when_max_passes_zero(monkeypatch) -> None:
    orch = _orch()
    monkeypatch.setattr(
        "hyperion.config.get_settings",
        lambda: type("S", (), {
            "quality_recovery_max_passes": 0,
            "quality_recovery_min_score_gain": 0.05,
            "recovery_wall_clock_seconds": 300,
        })(),
    )
    report = _report()
    score = _blocked_score(blockers=["CORPUS FLOOR: only 2 distinct source domain(s)."])
    monkeypatch.setattr(
        "hyperion.orchestrator.WorkflowEngine._dispatch_recovery",
        AsyncMock(),
    )

    dag = MagicMock()
    final_report, final_score = await orch._recover_from_blocked(
        dag, report, score, None,
    )

    assert final_report is report
    assert final_score is score
    assert orch._recovery_telemetry["attempted"] is False
    assert orch._recovery_telemetry["passes"] == 0


# ── §5.2 routing table: the four blocker → remediation mappings ─────────────


def test_remediation_routing_table() -> None:
    orch = _orch()
    score = _blocked_score()

    assert orch._remediation_for(
        "CORPUS FLOOR: only 2 distinct source domain(s) (minimum 8)", score,
    )["recovery_class"] == "THIN_EVIDENCE"

    verdict = orch._remediation_for(
        "VERDICT CONTRADICTION: recommendation is 'CONDITIONAL' but the "
        "narrative contains conflicting language ('no-go').", score,
    )
    assert verdict["recovery_class"] == "VERDICT_CONFLICT"
    assert verdict["agent"].value == "synthesis_lead"

    missing = orch._remediation_for(
        "MISSING RISK SECTION: risk_coverage failing with risk_analysis absent",
        score,
    )
    assert missing["recovery_class"] == "MISSING_SECTION"
    assert missing["agent"].value == "synthesis_lead"

    placeholder = orch._remediation_for(
        "DATA VOID: 'Unknown' value(s) rendered as data", score,
    )
    assert placeholder["recovery_class"] == "PLACEHOLDER_VALUE"

    presentation = orch._remediation_for(
        "BROKEN URL: source URL has an empty param, https://x.com?id=None",
        score,
    )
    assert presentation["recovery_class"] == "PRESENTATION_DEFECT"
