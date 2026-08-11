"""D-K (overhaul3_audit.md W1/S4b): ``FinalReport.risk_analysis`` must be
assigned from the RISK aggregate payload.

The 2026-08-11 blocked diagnostic: the Quality Gate scored
``risk_coverage=1/5`` ("No risk analysis present") while RISK produced 18
findings and published a full ``RiskAnalysis`` (risk_analyst.py:1323-1339).
Nothing in ``synthesis_lead.py`` ever assigned ``FinalReport.risk_analysis``
(models.py:2654 defaults it to None) — the report had no risk section, and the
gate blocked it on risk coverage INDEPENDENTLY of retrieval. Even a healthy
run with strong RISK findings would fail the gate.

Fix (P1 — the report is a view over the store): the Synthesis Lead captures
the structured ``risk_analysis`` model from the RISK aggregate payload on the
bus and assigns it to every ``FinalReport`` it constructs.

This test reproduces the real wiring gap: a full RiskAnalysis on the bus and a
report that still has ``risk_analysis is None`` before the fix.
"""

from __future__ import annotations

import pytest

from hyperion.agents.bus import BusMessage, Channel, MessageType, reset_bus
from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.schemas.agents import AgentName


def _risk_analysis_payload() -> dict:
    return {
        "risks": [
            {
                "id": "r1",
                "category": "regulatory",
                "description": "Export-control restrictions tighten for Indian launch providers",
                "probability": 3,
                "impact": 4,
                "risk_score": 12,
                "mitigation": "Diversify component sourcing to non-restricted jurisdictions",
                "owner": "risk_analyst",
            },
            {
                "id": "r2",
                "category": "financial",
                "description": "Cost overruns on domestic launcher development",
                "probability": 2,
                "impact": 3,
                "risk_score": 6,
                "mitigation": "Fixed-price milestone contracts",
                "owner": "risk_analyst",
            },
        ],
        "top_risks": [],
        "black_swan_scenarios": [],
        "residual_risk_summary": "Residual risk is MODERATE after mitigations",
        "scenario_plan": {"best": "x", "base": "y", "worst": "z"},
        "risk_matrix": {},
        "monte_carlo": {},
        "confidence": "medium",
        "sources": [],
    }


@pytest.fixture(autouse=True)
def _clean_bus() -> None:
    reset_bus()
    yield
    reset_bus()


@pytest.mark.asyncio
async def test_risk_aggregate_payload_assigns_report_risk_analysis() -> None:
    """D-K reproduction — RISK publishes a full RiskAnalysis; the report must
    carry it. Before the fix ``FinalReport.risk_analysis`` stayed None and the
    Quality Gate blocked risk_coverage=1/5."""
    lead = SynthesisLead()

    msg = BusMessage(
        channel=Channel.FINDINGS,
        msg_type=MessageType.FINDING,
        sender=AgentName.RISK_ANALYST,
        payload={
            "agent": "risk_analyst",
            "risk_analysis": _risk_analysis_payload(),
            "risk_count": 2,
            "confidence": "medium",
        },
    )
    await lead._handle_bus_message(msg)

    report = lead._minimal_report(reason="test")

    assert report.risk_analysis is not None, (
        "a RISK aggregate with a full RiskAnalysis must populate "
        "FinalReport.risk_analysis (pre-fix: the gate scored risk_coverage "
        "against a nonexistent section)"
    )
    assert len(report.risk_analysis.risks) == 2
    assert "MODERATE" in report.risk_analysis.residual_risk_summary


@pytest.mark.asyncio
async def test_invalid_risk_payload_is_discarded_not_crash() -> None:
    """A malformed risk_analysis payload must be discarded gracefully — a
    capture failure may never crash synthesis or fabricate a risk section."""
    lead = SynthesisLead()

    msg = BusMessage(
        channel=Channel.FINDINGS,
        msg_type=MessageType.FINDING,
        sender=AgentName.RISK_ANALYST,
        payload={
            "agent": "risk_analyst",
            "risk_analysis": {"risks": "not-a-list", "confidence": 42},
            "risk_count": 0,
            "confidence": "medium",
        },
    )
    # Must not raise.
    await lead._handle_bus_message(msg)

    report = lead._minimal_report(reason="test")
    assert report.risk_analysis is None
    assert report is not None
