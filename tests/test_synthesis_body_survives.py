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

import re
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
    """RouterResponse stand-in — the sandbox suite must never place a live
    LLM call.

    STALE-FIXTURE NOTE (P2-11). This stub used to be ``success = False`` with
    a comment saying that made "the section builder take its deterministic
    findings-concatenation fallback". P2-11 DELETED that fallback: a section
    with no synthesized narrative is now a ``SectionGapError`` and the section
    is omitted (``synthesis_lead.py:1318-1324``). A failing stub therefore
    produced zero sections and this file's invariant ("sections survive a
    mid-synthesis crash") became untestable rather than false.

    The stub is now SUCCESSFUL and prompt-aware: it returns a narrative long
    enough to clear ``min_body_chars`` for a section-narrative prompt, and a
    JSON object for the structured prompts. That is what the post-P2-11
    design requires of a real provider, so the fixture now models the
    provider contract instead of a deleted fallback.
    """

    def __init__(self, content: str) -> None:
        self.success = True
        self.content = content
        self.model = "stub"
        self.provider = "stub"
        self.error = None


_MIN_WORDS_RE = re.compile(r"not write fewer than\s+(\d+)\s+words")

# One paragraph, repeated as many times as the prompt's own stated minimum
# requires. Deriving the length from the prompt (rather than hardcoding a
# character count) is what keeps this fixture correct when plan_budget()'s
# per-section word allocation changes: min_body_chars is
# ``max(800, words_per_section * 6 * 0.5)`` and the prompt states
# ``words_per_section * 0.9``, so scaling off the stated minimum can never
# fall under the acceptance threshold.
_STUB_PARAGRAPH = (
    "Import volumes fell 14 percent year on year while domestic capacity "
    "utilisation rose to 78 percent, and the two movements are the same "
    "story told from opposite ends of the supply chain. The ministry series "
    "shows the decline concentrated in intermediate goods rather than "
    "finished products, which means the substitution is happening upstream "
    "where it compounds through the value chain rather than downstream "
    "where it would be a one-off. "
)
def stub_narrative(min_words: int = 2000) -> str:
    """A narrative body of at least ``min_words`` words."""
    per = len(_STUB_PARAGRAPH.split())
    reps = max(4, (min_words * 2) // max(1, per))
    return ("**Framing**\n\n" + _STUB_PARAGRAPH * reps).strip()


STUB_NARRATIVE = stub_narrative()


def _stub_content_for(prompt: str) -> str:
    """Return the shape a real provider would return for this prompt."""
    text = prompt or ""
    lowered = text.lower()
    is_narrative = (
        "write a deep, analytical narrative section" in lowered
        or "write a comprehensive section body" in lowered
        or "consulting prose" in lowered
    )
    if is_narrative:
        match = _MIN_WORDS_RE.search(lowered)
        return stub_narrative(int(match.group(1)) if match else 2000)
    return "{}"


@pytest.fixture(autouse=True)
def _no_live_llm(monkeypatch):
    async def _stub_complete(self, *args, **kwargs):
        prompt = kwargs.get("user_prompt") or (args[0] if args else "")
        return _StubLLMResponse(_stub_content_for(str(prompt)))

    monkeypatch.setattr(SynthesisLead, "_llm_complete", _stub_complete)

    # W-05 rebuilt contradiction resolution; two findings of equal evidence
    # weight now dispatch ``_deep_dive_contradiction`` -> ``_spawn_sub_agent``,
    # which performs LIVE web retrieval. The fixture's findings are
    # deliberately symmetric, so that path fires on every run and the test
    # hangs on the network rather than failing. Stubbed here: the sandbox
    # suite must never place a live call, and the sub-agent's verdict is not
    # what this file asserts on.
    async def _no_sub_agent(self, *args, **kwargs):
        return []

    monkeypatch.setattr(SynthesisLead, "_spawn_sub_agent", _no_sub_agent)


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
            # `confidence` is REQUIRED on AnalysisSection: P2-16 removed its
            # default so a section can never silently inherit a confidence
            # nobody derived. The fixture states it explicitly.
            __import__("hyperion.schemas.models", fromlist=["AnalysisSection"]).AnalysisSection(
                id="section_market_analyst",
                title="Market Landscape",
                agent="market_analyst",
                key_insight="k",
                body="b" * 200,
                confidence=ConfidenceLevel.MEDIUM,
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
