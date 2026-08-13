"""OVERHAUL5 W8 (D-13 / D-14) — visibility + typed recovery.

- W8.2: the run-end summary carries the search-layer status (per-provider
  calls/results/errors/state + cost report) — pre-W8 the operator could not
  see why the paid providers sat unused (08-12 run: 0 paid records).
- W8.4: the corpus-floor recovery directive names the paid web backbone as
  the input change.
- W8.5: one recovery pass per blocker class.

Fail-first: the recovery-directive test fails on pre-W8 code (no paid-backbone
reference); the summary test fails on pre-W8 code (no search-layer block).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from hyperion.orchestrator import WorkflowEngine


def _orch() -> WorkflowEngine:
    return WorkflowEngine.__new__(WorkflowEngine)


def _stub_result() -> SimpleNamespace:
    return SimpleNamespace(
        success=True,
        error="",
        pdf_path="reports/out.pdf",
        markdown_path="reports/out.md",
        duration_seconds=3600.0,
        quality_score=SimpleNamespace(total_score=3.4),
        quality_iterations=2,
        dag=SimpleNamespace(agents_selected=[SimpleNamespace(value="market_analyst")]),
        adaptation_count=1,
        escalation_count=2,
        estimated_llm_cost_usd=0.0123,
    )


def test_recovery_thin_directive_names_paid_backbone() -> None:
    """[FF] The corpus-floor recovery remedy must change the INPUT — the paid
    web backbone (You/Exa/Tavily/Yep), not a re-run of the dead free pool."""
    orch = _orch()
    action = orch._remediation_for("CORPUS FLOOR: only 3 distinct domain(s)", None)
    assert action is not None
    assert action["recovery_class"] == orch._RECOVERY_CLASS_THIN
    directive = action["directive"].lower()
    assert "you" in directive and "exa" in directive, (
        "the remedy must name the paid web backbone (pre-W8: 'living source "
        "classes only' — which is what re-ran into the same dead pool)"
    )


def test_remediation_verdict_class_maps_to_synthesis() -> None:
    orch = _orch()
    action = orch._remediation_for(
        "VERDICT CONTRADICTION: recommendation is 'CONDITIONAL' but the "
        "narrative contains conflicting language ('no-go').",
        None,
    )
    assert action is not None
    assert action["recovery_class"] == orch._RECOVERY_CLASS_VERDICT
    assert action["agent"].value == "synthesis_lead"


def test_run_summary_includes_search_layer_block() -> None:
    """[FF] The run-end summary must print per-provider search status + cost."""
    orch = _orch()
    orch.router = None

    class _FakeSearchOrch:
        def metrics_snapshot(self) -> dict:
            return {
                "You": {"calls_total": 3, "results_total": 12, "errors_total": 0},
                "Exa": {"calls_total": 5, "results_total": 30, "errors_total": 1},
            }

        def _cooldown_label(self, name: str) -> str:
            return "ok"

    import contextlib
    import io

    out = io.StringIO()
    with (
        patch(
            "hyperion.search.orchestrator.get_search_orchestrator",
            return_value=_FakeSearchOrch(),
        ),
        contextlib.redirect_stdout(out),
    ):
        orch._print_run_summary(_stub_result())

    text = out.getvalue()
    assert "Search Layer" in text, "the summary must surface the search layer"
    assert "You" in text and "Exa" in text
    assert "results=" in text and "calls=" in text
