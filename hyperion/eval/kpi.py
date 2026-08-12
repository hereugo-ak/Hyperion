"""Overhaul Phase 6 (overhaul.md §6 P6) — the 5 KPIs recorded per run.

KPI-1..KPI-5 are the master loop's iteration condition (overhaul §5). This
module records them per engagement into ``reports/diagnostics/kpis.json``
(keyed by run_id) and diffs a new run against the previous one, so a KPI
regression auto-opens the owning phase node instead of hiding in logs.

KPI contract (overhaul §5 / §8):
    KPI-1 time-to-first-evidence      < 60s from question
    KPI-2 distinct domains pre-synth  >= 8
    KPI-3 provenance binding          100% substantive findings carry a URL
    KPI-4 failure cost                degraded run < 5min, < 50k tokens, typed
    KPI-5 report integrity            0 blockers, consistent verdict

Usage:
    record_run_kpis(run_id, ...)   # at engagement end
    diff_kpis(run_id)              # vs the previous recorded run
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class RunKPIs:
    """One run's five KPIs plus identity/context fields."""

    run_id: str
    question: str = ""
    timestamp: float = field(default_factory=time.time)

    kpi_1_time_to_first_evidence_s: float = -1.0
    kpi_2_distinct_domains_pre_synthesis: int = -1
    kpi_3_provenance_binding_pct: float = -1.0
    kpi_4_duration_s: float = -1.0
    kpi_4_tokens: int = -1
    kpi_4_typed_terminal: bool = False
    kpi_5_blockers: int = -1
    kpi_5_verdict_consistent: bool = False

    # OVERHAUL2 S15: gates for the Output Contract invariants.
    #   kpi_6_pct_tasks_completed_with_output — OC-1/OC-2: every COMPLETED task
    #     must have a real output object; a "completed but no output" task is
    #     the status-writer bug that crashed synthesis (B-5). Must be 100.
    #   kpi_7_synthesis_produced_final_report — S4: any run that reaches the
    #     synthesis boundary must produce a FinalReport from the findings
    #     channel, never die on MissingDependencyOutput.
    #   kpi_8_off_topic_dropped — S11: counter of topicality-guard drops,
    #     visible in telemetry (B-9 "money laundering in a space report").
    kpi_6_pct_tasks_completed_with_output: float = -1.0
    kpi_7_synthesis_produced_final_report: bool = False
    kpi_8_off_topic_dropped: int = -1

    # OVERHAUL3 D-F (overhaul3_audit.md §5.5): the Recovery Supervisor
    # telemetry. ``kpi_9_recovery_attempted`` — did a BLOCKED run enter the
    # supervisor at all? ``kpi_9_recovery_passes`` — how many bounded passes
    # ran. ``kpi_9_recovered`` — did the run ship (or strictly improve) after
    # recovery instead of terminating with a discarded diagnosis?
    kpi_9_recovery_attempted: bool = False
    kpi_9_recovery_passes: int = 0
    kpi_9_recovered: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def gates(self) -> dict[str, bool]:
        """Which KPI thresholds are met (green) for this run."""
        return {
            "kpi_1": 0 <= self.kpi_1_time_to_first_evidence_s < 60,
            "kpi_2": self.kpi_2_distinct_domains_pre_synthesis >= 8,
            "kpi_3": self.kpi_3_provenance_binding_pct >= 100.0,
            "kpi_4": (
                self.kpi_4_duration_s < 5 * 60
                and self.kpi_4_tokens < 50_000
                and self.kpi_4_typed_terminal
            ),
            "kpi_5": self.kpi_5_blockers == 0 and self.kpi_5_verdict_consistent,
            # OVERHAUL2 S15 (OC-1): 100% of completed tasks carry an output.
            "kpi_6": self.kpi_6_pct_tasks_completed_with_output >= 100.0,
            # OVERHAUL2 S15 (OC-2): synthesis at the boundary always yields a
            # FinalReport — either directly or via the floor fallback, never a
            # MissingDependencyOutput crash.
            "kpi_7": self.kpi_7_synthesis_produced_final_report,
            # OVERHAUL2 S15 (S11): off-topic drops are tracked, not hidden.
            "kpi_8": self.kpi_8_off_topic_dropped >= 0,
            # OVERHAUL3 D-F (§5.5): recovery telemetry is present and counted.
            "kpi_9": self.kpi_9_recovery_passes >= 0,
        }


_KPI_FILE = "reports/diagnostics/kpis.json"


def _kpi_path(base: str | Path = "") -> Path:
    root = Path(base or ".")
    return root / _KPI_FILE


def _load_all(base: str | Path = "") -> dict[str, dict[str, Any]]:
    path = _kpi_path(base)
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def record_run_kpis(run: RunKPIs, base: str | Path = "") -> Path:
    """Persist one run's KPIs, keyed by run_id. Never raises."""
    path = _kpi_path(base)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        all_runs = _load_all(base)
        all_runs[run.run_id] = run.to_dict()
        tmp = path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(all_runs, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
        tmp.replace(path)
    except OSError as exc:
        print(f"WARN: KPI record failed for {run.run_id}: {exc}")
    return path


def diff_kpis(run_id: str, base: str | Path = "") -> dict[str, Any]:
    """Compare ``run_id`` against the most recent OTHER recorded run.

    Returns ``{run_id, prev_id, regressions: [kpi...], improved: [kpi...]}``.
    A regression names the owning phase (mapping below) so the master loop can
    auto-open the failed phase node.
    """
    all_runs = _load_all(base)
    if run_id not in all_runs:
        return {"run_id": run_id, "prev_id": None, "regressions": [], "improved": []}
    current = all_runs[run_id]

    others = {
        rid: data
        for rid, data in all_runs.items()
        if rid != run_id
    }
    if not others:
        return {"run_id": run_id, "prev_id": None, "regressions": [], "improved": []}
    prev_id = max(others, key=lambda rid: others[rid].get("timestamp", 0.0))
    prev = others[prev_id]

    def _green(data: dict[str, Any]) -> dict[str, bool]:
        gates = {}
        gates["kpi_1"] = 0 <= float(data.get("kpi_1_time_to_first_evidence_s", -1)) < 60
        gates["kpi_2"] = int(data.get("kpi_2_distinct_domains_pre_synthesis", -1)) >= 8
        gates["kpi_3"] = float(data.get("kpi_3_provenance_binding_pct", -1)) >= 100.0
        gates["kpi_4"] = (
            float(data.get("kpi_4_duration_s", -1)) < 300
            and int(data.get("kpi_4_tokens", -1)) < 50_000
            and bool(data.get("kpi_4_typed_terminal", False))
        )
        gates["kpi_5"] = (
            int(data.get("kpi_5_blockers", -1)) == 0
            and bool(data.get("kpi_5_verdict_consistent", False))
        )
        # OVERHAUL2 S15
        gates["kpi_6"] = (
            float(data.get("kpi_6_pct_tasks_completed_with_output", -1)) >= 100.0
        )
        gates["kpi_7"] = bool(data.get("kpi_7_synthesis_produced_final_report", False))
        gates["kpi_8"] = int(data.get("kpi_8_off_topic_dropped", -1)) >= 0
        gates["kpi_9"] = int(data.get("kpi_9_recovery_passes", 0)) >= 0
        return gates

    cur_g = _green(current)
    prev_g = _green(prev)

    regressions = [k for k in sorted(cur_g) if prev_g[k] and not cur_g[k]]
    improved = [k for k in sorted(cur_g) if not prev_g[k] and cur_g[k]]

    return {
        "run_id": run_id,
        "prev_id": prev_id,
        "regressions": regressions,
        "improved": improved,
    }


KPI_OWNER_PHASE: dict[str, str] = {
    "kpi_1": "P1",  # time-to-first-evidence → capacity (P1)
    "kpi_2": "P1",  # domains before synthesis → capacity (P1)
    "kpi_3": "P3",  # provenance binding → retrieval-bound provenance (P3)
    "kpi_4": "P2",  # cheap degraded terminal → corpus preflight (P2) + loops (P4)
    "kpi_5": "P5",  # report integrity → verification repositioned (P5)
    # OVERHAUL2 S15: the Output Contract invariants.
    "kpi_6": "P2",  # tasks-completed-with-output → output contract (OC-1/OC-2)
    "kpi_7": "P2",  # synthesis-produced-final-report → partial-context (S4)
    "kpi_8": "P3",  # off-topic drops → funnel hygiene (S11)
    # OVERHAUL3 D-F: recovery supervisor telemetry → self-healing loop (W4/S9)
    "kpi_9": "P5",  # recovery attempted/counted → quality supervision (P5)
}


def regressed_phase(diff: dict[str, Any]) -> list[str]:
    """Map a KPI diff's regressions to owning phase nodes."""
    return sorted({KPI_OWNER_PHASE[k] for k in diff.get("regressions", [])})


# Export for __init__ and tests.
__all__ = [
    "RunKPIs",
    "diff_kpis",
    "record_run_kpis",
    "regressed_phase",
    "KPI_OWNER_PHASE",
]
