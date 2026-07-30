"""T-05 · D-02 · degraded reports cannot be re-prosed into confidence.

The 07-30 mechanism: synthesis crashed, `_minimal_report()` shipped an honest
"This is a degraded report" notice — then the quality loop fed the report
back through the LLM with the few-shot example in context, and the example's
fabricated numbers ("$2B TAM", "12% penetration") overwrote the notice. The
deliverable read as a confident ENTER recommendation built on evidence that
never existed.

The class fix (Phase 1.3): `_apply_quality_feedback()` restores the degraded
report's conclusion fields verbatim — a degraded report may gain STRUCTURE,
never CONFIDENCE — and records a failure when the loop returns
section_updates for a report whose body was never built.

These tests assert on the DELIVERABLE (the FinalReport that would go to the
PDF), not on the prompt.
"""

from __future__ import annotations

import json

import pytest

from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.schemas.models import (
    ConfidenceLevel,
    KeyFinding,
    QualityDimension,
    QualityDimensionName,
    QualityScore,
    Source,
    SourceCredibility,
)

QUESTION = "should india import less ?"

# The exact laundering payload from the 07-30 failure: the few-shot
# example's fabricated numbers re-emerging as "quality improvements".
LAUNDERING_PAYLOAD = {
    "executive_summary": "Market's $2B TAM at 12% penetration is attractive. ENTER.",
    "recommendation_rationale": "ENTER: the $2B TAM supports entry at 12% penetration.",
    "new_limitations": ["None — the analysis is complete."],
}


class _StubLLMResponse:
    def __init__(self, payload: dict):
        self.success = True
        self.content = json.dumps(payload)
        self.model = "stub"
        self.provider = "stub"
        self.error = None


def _failing_quality_score() -> QualityScore:
    """A Quality Gate verdict that forces iteration (dimension < 4)."""
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id=QualityDimensionName.TONE_AND_VOICE,
                name="Tone and Voice",
                score=2,
                weight=0.1,
                feedback="Executive summary reads as hedgy.",
                fix_instructions="Make it confident.",
            )
        ],
        total_score=3.4,
        approved=False,
        iteration=1,
    )


def _seed_lead_with_findings(lead: SynthesisLead) -> None:
    for a in ("market_analyst", "financial_analyst", "risk_analyst"):
        f = KeyFinding(
            id=f"f_{a}",
            agent=a,
            finding_type="market_size",
            title=f"{a} finding",
            content="Real evidence: imports fell 14% year-on-year per ministry data.",
            sources=[
                Source(
                    id=f"src_{a}",
                    title="t",
                    url=f"https://example.com/{a}",
                    credibility=SourceCredibility.GOVERNMENT,
                )
            ],
            confidence=ConfidenceLevel.HIGH,
            implications="Real implication from evidence.",
        )
        lead._collected_findings.append(f)
        lead._findings_by_agent.setdefault(a, []).append(f)


class TestDegradedReportCannotBeReProsed:
    @pytest.mark.asyncio
    async def test_quality_loop_cannot_overwrite_degradation_notice(self, monkeypatch):
        """The audit's T-05, verbatim in intent: the laundering payload must
        not survive into the deliverable."""
        lead = SynthesisLead()
        degraded = lead._minimal_report(
            reason="VaultSearchResult has no attribute 'strip'"
        )

        async def _stub(*a, **k):
            return _StubLLMResponse(LAUNDERING_PAYLOAD)

        monkeypatch.setattr(lead, "_llm_complete", _stub)

        out = await lead._apply_quality_feedback(degraded, _failing_quality_score())

        assert "degraded" in out.executive_summary.lower()
        assert "$2B" not in out.executive_summary
        assert "12% penetration" not in out.executive_summary

    @pytest.mark.asyncio
    async def test_rationale_and_limitations_also_survive(self, monkeypatch):
        """The guard is not just the exec summary: rationale and limitations
        are conclusion fields too."""
        lead = SynthesisLead()
        degraded = lead._minimal_report(reason="crash mid-synthesis")
        original_rationale = degraded.recommendation_rationale
        original_limitations = list(degraded.limitations)

        async def _stub(*a, **k):
            return _StubLLMResponse(LAUNDERING_PAYLOAD)

        monkeypatch.setattr(lead, "_llm_complete", _stub)

        out = await lead._apply_quality_feedback(degraded, _failing_quality_score())

        assert out.recommendation_rationale == original_rationale
        assert "$2B" not in out.recommendation_rationale
        assert out.limitations == original_limitations
        assert out.is_degraded  # flag itself must survive the copy

    @pytest.mark.asyncio
    async def test_degraded_report_may_still_gain_structure(self, monkeypatch):
        """The guard is surgical: section body updates (structure, backed by
        findings) are NOT blocked for a degraded report that has sections."""
        lead = SynthesisLead()
        _seed_lead_with_findings(lead)

        async def _stub(*a, **k):
            return _StubLLMResponse(
                {
                    "executive_summary": LAUNDERING_PAYLOAD["executive_summary"],
                    "section_updates": {
                        "section_market_analyst": {
                            "body": "Deeper structural analysis of the 14% import decline.",
                        }
                    },
                }
            )

        monkeypatch.setattr(lead, "_llm_complete", _stub)

        # Sections come from the D-01 path: built from findings, parked on
        # _partial_sections, carried into the degraded report.
        lead._partial_sections = await lead._build_analysis_sections()
        degraded = lead._minimal_report(reason="crash")
        assert degraded.sections, "fixture: degraded report carries the body"

        out = await lead._apply_quality_feedback(degraded, _failing_quality_score())

        assert "deeper structural analysis" in out.sections[0].body.lower()
        # ...but the conclusion fields stayed honest
        assert "degraded" in out.executive_summary.lower()

    @pytest.mark.asyncio
    async def test_non_degraded_report_updates_normally(self, monkeypatch):
        """Guard against over-blocking: a healthy report takes the LLM's
        conclusion updates exactly as before."""
        lead = SynthesisLead()
        _seed_lead_with_findings(lead)
        healthy = lead._minimal_report(reason="x")
        healthy = healthy.model_copy(update={"is_degraded": False})

        async def _stub(*a, **k):
            return _StubLLMResponse(LAUNDERING_PAYLOAD)

        monkeypatch.setattr(lead, "_llm_complete", _stub)

        out = await lead._apply_quality_feedback(healthy, _failing_quality_score())

        assert out.executive_summary == LAUNDERING_PAYLOAD["executive_summary"]

    @pytest.mark.asyncio
    async def test_section_updates_for_bodiless_report_are_recorded(self, monkeypatch):
        """Honesty clause: section_updates aimed at a 0-section report mean
        the body was never built. The loop can't create sections, so the
        update silently no-ops — that silence is exactly the failure class
        the audit bans. It must be recorded."""
        lead = SynthesisLead()
        bodiless = lead._minimal_report(reason="early crash", sections=[])
        assert bodiless.sections == []

        async def _stub(*a, **k):
            return _StubLLMResponse(
                {"section_updates": {"section_market_analyst": {"body": "ghost"}}}
            )

        monkeypatch.setattr(lead, "_llm_complete", _stub)

        out = await lead._apply_quality_feedback(bodiless, _failing_quality_score())

        assert out.sections == []  # loop cannot conjure sections
        assert any("0 sections" in f for f in lead._recorded_failures), (
            "a section update that matched nothing must not vanish silently"
        )
