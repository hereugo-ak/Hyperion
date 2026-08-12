"""Overhaul Phase 6 (overhaul.md §6 P6) — fault-injection canaries + KPI diff.

The canary suite makes the Aug-9/Aug-10 failure modes permanent integration
tests. This module runs the real ``hyperion.eval.canaries`` suite and the
``hyperion.eval.kpi`` recorder/differ, and pins the CI wiring.
"""

from __future__ import annotations

from hyperion.eval.canaries import CANARY_REGISTRY, run_canaries
from hyperion.eval.kpi import (
    KPI_OWNER_PHASE,
    RunKPIs,
    diff_kpis,
    record_run_kpis,
    regressed_phase,
)

# ── P6.1 · fault-injection canary suite ────────────────────────────────────


def test_all_canaries_are_registered() -> None:
    """P6.1: the canary registry carries every named failure mode from the
    overhaul §6 P6 list, plus the OVERHAUL2 S14 fault-injection canaries."""
    names = {entry["name"] for entry in CANARY_REGISTRY}
    required = {
        "all-engines-403",
        "healthy",
        "malformed-JSON",
        "sub-agent-timeout",
        "budget-exhaustion",
        "grounding-key-missing",
        # OVERHAUL2 S14
        "reference-category-400",
        "missing-dep-output",
    }
    assert required <= names


def test_canary_suite_all_green() -> None:
    """P6.1: every canary asserts its phase gate. A regression in any gate
    fails the suite — the fix stays fixed."""
    results = run_canaries()
    failed = [r for r in results if not r.passed]
    assert not failed, f"canary failures: {[(r.name, r.detail) for r in failed]}"
    assert len(results) == len(CANARY_REGISTRY)


def test_429_storm_canary_caps_suspensions() -> None:
    """P6.1 (429-storm): a suspended_time=86400 report must be capped at the
    4h ceiling, not poison the next engagement for 24h."""
    import os
    import tempfile
    import time as _t

    from hyperion.tools.engine_health import (
        _MAX_COOLDOWN_SECONDS,
        EngineHealthTracker,
    )

    os.environ["HYPERION_ENGINE_HEALTH_STATE"] = tempfile.mktemp(suffix=".json")
    tracker = EngineHealthTracker()
    tracker.reset()
    tracker.record_response(
        unresponsive_engines=[["mwmbl", "HTTP error 429 (suspended_time=86400)"]],
        responding_engines=[],
    )
    until = tracker.cooldown_until("mwmbl")
    assert until <= _t.time() + _MAX_COOLDOWN_SECONDS + 1


# ── P6.2 · KPI recording + run-over-run diff ───────────────────────────────


def test_kpi_gates_classify_green_run() -> None:
    good = RunKPIs(
        run_id="eng_g",
        kpi_1_time_to_first_evidence_s=30,
        kpi_2_distinct_domains_pre_synthesis=12,
        kpi_3_provenance_binding_pct=100.0,
        kpi_4_duration_s=240,
        kpi_4_tokens=30_000,
        kpi_4_typed_terminal=True,
        kpi_5_blockers=0,
        kpi_5_verdict_consistent=True,
        # OVERHAUL2 S15: the Output Contract gates must be green on a healthy run.
        kpi_6_pct_tasks_completed_with_output=100.0,
        kpi_7_synthesis_produced_final_report=True,
        kpi_8_off_topic_dropped=0,
    )
    assert all(good.gates().values())


def test_kpi_gates_classify_degraded_run() -> None:
    bad = RunKPIs(
        run_id="eng_b",
        kpi_1_time_to_first_evidence_s=120,
        kpi_2_distinct_domains_pre_synthesis=2,
        kpi_3_provenance_binding_pct=10.0,
        kpi_4_duration_s=2400,
        kpi_4_tokens=800_000,
        kpi_4_typed_terminal=False,
        kpi_5_blockers=1,
        kpi_5_verdict_consistent=False,
        # OVERHAUL2 S15: a run with completed-but-no-output tasks fails kpi_6,
        # a synthesis that never produced a report fails kpi_7, and a
        # non-negative off-topic counter always passes the tracking gate.
        kpi_6_pct_tasks_completed_with_output=87.5,
        kpi_7_synthesis_produced_final_report=False,
        kpi_8_off_topic_dropped=7,
    )
    gates = bad.gates()
    assert not gates["kpi_6"]
    assert not gates["kpi_7"]
    assert gates["kpi_8"]


def test_kpi_record_and_diff(tmp_path) -> None:
    """P6.2: recording two runs lets the differ name regressions and their
    owning phase nodes. OVERHAUL2 S15 adds the Output Contract gates."""
    good = RunKPIs(
        run_id="eng_good",
        kpi_1_time_to_first_evidence_s=30,
        kpi_2_distinct_domains_pre_synthesis=12,
        kpi_3_provenance_binding_pct=100.0,
        kpi_4_duration_s=240,
        kpi_4_tokens=30_000,
        kpi_4_typed_terminal=True,
        kpi_5_blockers=0,
        kpi_5_verdict_consistent=True,
        kpi_6_pct_tasks_completed_with_output=100.0,
        kpi_7_synthesis_produced_final_report=True,
        kpi_8_off_topic_dropped=0,
    )
    bad = RunKPIs(
        run_id="eng_bad",
        kpi_1_time_to_first_evidence_s=90,
        kpi_2_distinct_domains_pre_synthesis=3,
        kpi_3_provenance_binding_pct=40.0,
        kpi_4_duration_s=1200,
        kpi_4_tokens=90_000,
        kpi_4_typed_terminal=False,
        kpi_5_blockers=2,
        kpi_5_verdict_consistent=False,
        kpi_6_pct_tasks_completed_with_output=87.5,
        kpi_7_synthesis_produced_final_report=False,
        kpi_8_off_topic_dropped=5,
    )
    record_run_kpis(good, base=str(tmp_path))
    record_run_kpis(bad, base=str(tmp_path))

    diff = diff_kpis("eng_bad", base=str(tmp_path))
    assert diff["prev_id"] == "eng_good"
    assert set(diff["regressions"]) == {
        "kpi_1", "kpi_2", "kpi_3", "kpi_4", "kpi_5", "kpi_6", "kpi_7",
    }
    # A regression auto-opens the owning phase node.
    assert regressed_phase(diff) == ["P1", "P2", "P3", "P5"]


def test_kpi_owner_phase_mapping_covered() -> None:
    """Every KPI maps to an owning phase node; the map is complete.

    OVERHAUL3 D-F (§5.5): ``kpi_9_*`` (recovery supervisor telemetry) is part
    of the map, so a recovery regression opens the P5 quality node.
    """
    assert set(KPI_OWNER_PHASE) == {
        "kpi_1", "kpi_2", "kpi_3", "kpi_4", "kpi_5",
        "kpi_6", "kpi_7", "kpi_8", "kpi_9",
    }


# ── P6.3 · weekly healthy-engagement gate ──────────────────────────────────


def test_weekly_healthy_gate_constants() -> None:
    """P6.3: the weekly gate's thresholds are pinned so they cannot drift."""
    from hyperion.agents.support.quality_gate import QualityGate

    assert QualityGate._CORPUS_FLOOR_DOMAINS == 8  # ≥8 domains pre-synthesis
    from hyperion.eval.kpi import RunKPIs

    healthy = RunKPIs(
        run_id="weekly",
        kpi_1_time_to_first_evidence_s=30,
        kpi_2_distinct_domains_pre_synthesis=8,
        kpi_3_provenance_binding_pct=100.0,
        kpi_4_duration_s=200,
        kpi_4_tokens=40_000,
        kpi_4_typed_terminal=True,
        kpi_5_blockers=0,
        kpi_5_verdict_consistent=True,
    )
    assert healthy.gates()["kpi_2"]  # ≥8 domains
    assert healthy.gates()["kpi_5"]  # 0 blockers, consistent verdict
