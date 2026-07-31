"""P2-16: a placeholder string is never constructed; every gap raises an
``AnalysisGap``, and an unresolvable gap omits the field and declares the
unanswered question in ``limitations``.

Before the fix, two sites in ``synthesis_lead`` emitted hardcoded placeholder
implications (``Insufficient evidence to state implications ...`` shipped 4
times in report A and 8 times in report B), and the empty-findings path built
a filler section. The string is on the Quality Gate's own banned-filler list,
so the system wrote text it knew was forbidden and failed to act.

After the fix:
  * ``AnalysisGap`` is a first-class schema object.
  * No placeholder string is constructible: a construction-time validator on
    ``AnalysisSection`` and ``KeyFinding`` rejects every banned filler phrase.
  * Every placeholder site raises a gap; an omitted section records its
    question in ``section_gaps`` (surfaced to ``FinalReport.limitations``).
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import (
    AnalysisGap,
    AnalysisSection,
    ConfidenceLevel,
    KeyFinding,
)


# ---------------------------------------------------------------------------
# AnalysisGap schema shape
# ---------------------------------------------------------------------------


class TestAnalysisGapSchema:
    def test_analysis_gap_exists_and_constructs(self):
        gap = AnalysisGap(
            id="gap_1",
            section_id="section_market_analyst",
            agent=AgentName.MARKET_ANALYST,
            field="implications",
            question="What are the 'so what' implications of the market-size finding?",
        )
        assert gap.id == "gap_1"
        assert gap.field == "implications"
        assert gap.attempts == 0
        assert gap.resolved is False
        assert gap.resolution is None

    def test_analysis_gap_field_is_literal_constrained(self):
        with pytest.raises(ValidationError):
            AnalysisGap(
                id="gap_2",
                section_id="section_x",
                agent=AgentName.RISK_ANALYST,
                field="nonsense_field",
                question="q",
            )

    def test_gap_marks_resolved_with_resolution(self):
        gap = AnalysisGap(
            id="gap_3",
            section_id="s",
            agent=AgentName.FINANCIAL_ANALYST,
            field="body",
            question="q",
        )
        gap.resolved = True
        gap.resolution = "answered"
        gap.attempts = 2
        assert gap.resolved and gap.attempts == 2


# ---------------------------------------------------------------------------
# No placeholder string is constructible (construction-time validator)
# ---------------------------------------------------------------------------

_BANNED_PHRASES = (
    "Insufficient evidence to state implications",
    "no specific implications stated",
    "no specific implications could be derived",
    "so what? no specific",
    "no competitors identified",
)


def _section_kwargs(**overrides):
    base = dict(
        id="s1",
        title="Market Landscape",
        agent="market_analyst",
        key_insight="A real insight.",
        body="A substantive body of analysis with real content.",
        confidence=ConfidenceLevel.MEDIUM,
    )
    base.update(overrides)
    return base


class TestBannedFillerUnconstructible:
    @pytest.mark.parametrize("phrase", _BANNED_PHRASES)
    def test_section_implications_rejects_filler(self, phrase):
        with pytest.raises(ValidationError):
            AnalysisSection(**_section_kwargs(implications=phrase))

    @pytest.mark.parametrize("phrase", _BANNED_PHRASES)
    def test_keyfinding_implications_rejects_filler(self, phrase):
        with pytest.raises(ValidationError):
            KeyFinding(
                id="f1",
                agent="market_analyst",
                finding_type="risk",
                title="T",
                content="Real content.",
                confidence=ConfidenceLevel.MEDIUM,
                implications=phrase,
            )

    def test_section_body_rejects_filler(self):
        with pytest.raises(ValidationError):
            AnalysisSection(
                **_section_kwargs(body="no competitors identified in this market")
            )

    def test_real_implications_pass(self):
        s = AnalysisSection(
            **_section_kwargs(
                implications="The market is viable; entry within 18 months is recommended."
            )
        )
        assert "viable" in s.implications

    def test_omitted_implications_allowed(self):
        """An honest omission (None) must be constructible; only the
        placeholder is banned."""
        s = AnalysisSection(**_section_kwargs(implications=None))
        assert s.implications is None


# ---------------------------------------------------------------------------
# Placeholder sites raise a gap instead of shipping filler
# ---------------------------------------------------------------------------


class TestPlaceholderSitesRaiseGaps:
    def _lead(self):
        from hyperion.agents.synthesis_lead import SynthesisLead

        lead = SynthesisLead.__new__(SynthesisLead)
        lead._question = "Should we enter the market?"
        lead.section_gaps = []
        return lead

    def test_empty_findings_omits_section_and_declares_gap(self):
        import asyncio

        lead = self._lead()
        lead._findings_by_agent = {"market_analyst": []}
        sections = asyncio.run(lead._build_analysis_sections())

        assert sections == []
        assert lead.section_gaps, "expected the gap question to be declared"

    def test_missing_implications_omits_field_no_placeholder(self):
        """A section built from findings with no implications must ship with
        implications omitted (None), never the banned placeholder string."""
        import asyncio

        lead = self._lead()
        lead._findings_by_agent = {
            "market_analyst": [
                KeyFinding(
                    id="f1",
                    agent="market_analyst",
                    finding_type="market_size",
                    title="Market size finding",
                    content="Substantive content.",
                    confidence=ConfidenceLevel.MEDIUM,
                    implications=None,  # no implications available
                )
            ]
        }

        async def _narrative(**kwargs):
            from types import SimpleNamespace

            # min_body_chars is max(800, words_per_section * 3) -- 7800 for a
            # single-section budget; exceed it or the section is (correctly)
            # omitted as an unsynthesizable gap.
            body = "A synthesized analytical narrative with substantive depth. " * 200
            return SimpleNamespace(success=True, content=body)

        lead._llm_complete = _narrative  # type: ignore[method-assign]
        sections = asyncio.run(lead._build_analysis_sections())

        assert sections, "expected the section to be built"
        for s in sections:
            for phrase in _BANNED_PHRASES:
                assert phrase.lower() not in (s.implications or "").lower()
