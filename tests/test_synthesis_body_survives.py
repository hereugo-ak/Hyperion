"""T-02 · D-01 · sections survive a mid-synthesis crash — the invariant that
was missing on 07-30.

The audit (§2 D-01, §4 1.2): the report body used to be assembled at step 8
of ``_run_synthesis``, so any earlier raise discarded 12 specialists' work and
``_minimal_report()`` hardcoded ``sections=[]``. The deliverable was a
contentless report with no signal that the body had ever existed.

The class fix: ``_build_analysis_sections()`` runs on findings BEFORE the
recommendation call, the sections are parked on ``self._partial_sections``
the moment they exist, and ``_minimal_report()`` carries them into the
degraded report with ``is_degraded=True``.

These tests assert on the DELIVERABLE — the FinalReport a downstream stage
would render — not on the internal reordering.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.schemas.models import (
    ConfidenceLevel,
    FinalReport,
    KeyFinding,
    Recommendation,
    Source,
    SourceCredibility,
)

QUESTION = "should india import less ?"


class _StubLLMResponse:
    """RouterResponse stand-in. success=False makes the section builder take
    its deterministic findings-concatenation fallback — the sandbox suite
    must never place a live LLM call."""

    success = False
    content = ""
    model = "stub"
    provider = "stub"


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    async def _stub_complete(self, *args, **kwargs):
        return _StubLLMResponse()

    monkeypatch.setattr(SynthesisLead, "_llm_complete", _stub_complete)


def _finding(agent: str, i: int) -> KeyFinding:
    return KeyFinding(
        id=f"f_{agent}_{i}",
        agent=agent,
        finding_type="market_size",
        title=f"{agent} finding {i}: measurable evidence",
        content=(
            f"Evidence block {i} from {agent}: imports fell 14% year-on-year "
            f"while domestic capacity utilisation rose to 78%, per ministry data."
        ),
        sources=[
            Source(
                id=f"src_{agent}_{i}",
                title=f"{agent} source {i}",
                url=f"https://example.com/{agent}/{i}",
                credibility=SourceCredibility.GOVERNMENT,
            )
        ],
        confidence=ConfidenceLevel.HIGH,
        implications="Import substitution is already underway; policy amplifies it.",
    )


@pytest.fixture
def findings_fixture() -> list[KeyFinding]:
    """Three specialists' worth of findings — enough for >= 3 sections."""
    agents = ("market_analyst", "financial_analyst", "risk_analyst")
    return [f for a in agents for f in (_finding(a, 0), _finding(a, 1))]


def _seed_lead(lead: SynthesisLead, findings: list[KeyFinding]) -> None:
    """Seed the lead as if the bus had delivered the findings."""
    lead._collected_findings = list(findings)
    for f in findings:
        lead._findings_by_agent.setdefault(f.agent, []).append(f)


class TestSectionsSurviveMidSynthesisCrash:
    @pytest.mark.asyncio
    async def test_synthesis_failure_preserves_analysis_body(
        self, monkeypatch, findings_fixture
    ):
        """The audit's T-02, verbatim in intent: a recommendation failure
        must not delete the analysis."""
        lead = SynthesisLead()
        _seed_lead(lead, findings_fixture)
        monkeypatch.setattr(
            lead,
            "_identify_and_draft",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        report = await lead.run(engagement_id="t", question=QUESTION)

        assert report.is_degraded
        assert len(report.sections) >= 3, (
            "a recommendation failure must not delete the analysis"
        )

    @pytest.mark.asyncio
    async def test_crash_report_marks_placeholder_recommendation(
        self, monkeypatch, findings_fixture
    ):
        """The degraded report must not masquerade as a synthesis: the
        recommendation is the INVESTIGATE placeholder, confidence LOW, and
        the limitations name the failure."""
        lead = SynthesisLead()
        _seed_lead(lead, findings_fixture)
        monkeypatch.setattr(
            lead,
            "_identify_and_draft",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        report = await lead.run(engagement_id="t", question=QUESTION)

        assert report.recommendation == Recommendation.INVESTIGATE
        assert report.confidence == ConfidenceLevel.LOW
        assert any("boom" in lim for lim in report.limitations)
        assert "degraded" in report.executive_summary.lower()

    @pytest.mark.asyncio
    async def test_surviving_sections_carry_real_content(
        self, monkeypatch, findings_fixture
    ):
        """The surviving body is the actual specialist analysis — titled
        sections with non-trivial bodies, findings, and sources — not a
        placeholder shell."""
        lead = SynthesisLead()
        _seed_lead(lead, findings_fixture)
        monkeypatch.setattr(
            lead,
            "_identify_and_draft",
            AsyncMock(side_effect=RuntimeError("boom")),
        )

        report = await lead.run(engagement_id="t", question=QUESTION)

        titles = {s.title for s in report.sections}
        assert "Market Landscape" in titles
        assert "Financial Viability" in titles
        assert "Risk Assessment" in titles
        for section in report.sections:
            assert len(section.body) > 100, f"{section.title} body is a stub"
            assert section.findings, f"{section.title} lost its findings"
            assert section.sources, f"{section.title} lost its sources"
        # Evidence made it into the degraded deliverable
        assert report.total_sources >= 3
        assert report.total_data_points == len(findings_fixture)

    @pytest.mark.asyncio
    async def test_failure_after_sections_still_preserves_body(
        self, monkeypatch, findings_fixture
    ):
        """A crash anywhere after section assembly (e.g. confidence
        calibration) must also preserve the body — the invariant is about
        ordering, not about one specific raise point."""
        lead = SynthesisLead()
        _seed_lead(lead, findings_fixture)
        monkeypatch.setattr(
            lead,
            "_calibrate_confidence",
            lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("calibration exploded")),
        )

        report = await lead.run(engagement_id="t", question=QUESTION)

        assert report.is_degraded
        assert len(report.sections) >= 3


class TestDegradedFlag:
    def test_normal_report_is_not_degraded(self):
        report = FinalReport(
            engagement_id="t",
            question=QUESTION,
            recommendation=Recommendation.ENTER,
            recommendation_rationale="evidence chain",
            critical_assumptions=[],
            confidence=ConfidenceLevel.HIGH,
            confidence_breakdown={},
            executive_summary="summary",
        )
        assert report.is_degraded is False

    @pytest.mark.asyncio
    async def test_no_findings_report_is_degraded(self):
        """The empty-findings early return ships an INVESTIGATE placeholder
        with no synthesis behind it — degraded by definition."""
        lead = SynthesisLead()
        report = await lead.run(engagement_id="t", question=QUESTION)
        assert report.is_degraded
        assert report.sections == []

    def test_minimal_report_uses_partial_sections_when_no_explicit_arg(
        self, findings_fixture
    ):
        """_minimal_report falls back to self._partial_sections — the
        mechanism that lets run()'s except-block carry the body without
        knowing it exists."""
        lead = SynthesisLead()
        _seed_lead(lead, findings_fixture)
        # Simulate: sections were built, then something downstream raised.
        lead._partial_sections = [
            # Minimal stand-in; _minimal_report must carry the list object.
            __import__("hyperion.schemas.models", fromlist=["AnalysisSection"]).AnalysisSection(
                id="section_market_analyst",
                title="Market Landscape",
                agent="market_analyst",
                key_insight="k",
                body="b" * 200,
            )
        ]
        report = lead._minimal_report(reason="late crash")
        assert report.is_degraded
        assert len(report.sections) == 1
        assert report.sections[0].title == "Market Landscape"

    @pytest.mark.asyncio
    async def test_successful_synthesis_is_not_marked_degraded(
        self, monkeypatch, findings_fixture
    ):
        """Guard against over-flagging: a clean run produces
        is_degraded=False with the recommendation from the LLM call."""
        lead = SynthesisLead()
        _seed_lead(lead, findings_fixture)
        monkeypatch.setattr(
            lead,
            "_identify_and_draft",
            AsyncMock(
                return_value=(
                    ["market_analyst"],
                    {
                        "recommendation": "enter",
                        "recommendation_rationale": "the evidence chain",
                        "critical_assumptions": ["demand holds"],
                        "executive_summary": "Enter, carefully.",
                        "key_findings_titles": [],
                    },
                )
            ),
        )

        report = await lead.run(engagement_id="t", question=QUESTION)

        assert report.is_degraded is False
        assert report.recommendation == Recommendation.ENTER
        assert len(report.sections) >= 3
