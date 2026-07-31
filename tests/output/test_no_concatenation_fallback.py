"""A section with no synthesized narrative is a gap, never a concatenation (P2-11).

Before the fix, when the narrative LLM call failed, the section body became
``"\\n\\n".join(f.content for f in findings)`` -- the concatenation that put
raw finding content (and, via P2-09, JSON dumps) into seven chapters of
report B. The ``except (...): pass`` is why no operator was told.

After the fix: a section whose narrative cannot be synthesized raises a
``SectionGapError``, the section is omitted from the report, and the specific
unanswered question is declared in ``section_gaps`` (surfaced to
``FinalReport.limitations``).
"""

from __future__ import annotations

import asyncio

from hyperion.agents.synthesis_lead import SectionGapError, SynthesisLead
from hyperion.schemas.models import ConfidenceLevel, KeyFinding


def _finding(agent: str, i: int) -> KeyFinding:
    return KeyFinding(
        id=f"f{i}",
        agent=agent,
        finding_type="market_size",
        title=f"Finding {i}",
        content=f"Content of finding {i} with some substance.",
        confidence=ConfidenceLevel.MEDIUM,
    )


def _lead_with_failing_narrative() -> SynthesisLead:
    lead = SynthesisLead.__new__(SynthesisLead)
    lead._question = "Should we enter the market?"
    lead._findings_by_agent = {
        "market_analyst": [_finding("market_analyst", i) for i in range(3)],
    }
    lead.section_gaps = []

    async def _boom(**kwargs):
        raise RuntimeError("provider down")

    lead._llm_complete = _boom  # type: ignore[method-assign]
    return lead


class TestNoConcatenationFallback:
    def test_failed_narrative_omits_section_and_declares_gap(self):
        lead = _lead_with_failing_narrative()
        sections = asyncio.run(lead._build_analysis_sections())

        # The section must be omitted, not shipped with concatenated content.
        assert sections == []
        # The gap must be recorded with the specific unanswered question.
        assert lead.section_gaps, "expected the gap to be declared"
        assert any("market" in g.lower() for g in lead.section_gaps)

    def test_no_concatenated_content_leaks(self):
        lead = _lead_with_failing_narrative()
        sections = asyncio.run(lead._build_analysis_sections())
        for s in sections:
            assert "Content of finding" not in s.body

    def test_gap_error_type_exists(self):
        # The mechanism is a typed error, never a silent pass.
        assert issubclass(SectionGapError, Exception)
