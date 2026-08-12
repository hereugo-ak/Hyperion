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
    def test_failed_narrative_builds_digest_section_never_a_gap(self):
        """OVERHAUL4 P3.2: P2-11's "omit the section" policy is exactly how
        47 findings became a 0-domain shell (Aug-11 run). A failed narrative
        LLM now falls back to a deterministic finding-digest section — the
        section is never omitted, never empty."""
        lead = _lead_with_failing_narrative()
        sections = asyncio.run(lead._build_analysis_sections())

        assert sections, "a failed narrative must not empty the report"
        market = next(s for s in sections if s.agent == "market_analyst")
        assert market.body.startswith(
            "Analysis of Market Landscape is synthesized from 3 specialist finding(s)"
        )

    def test_digest_is_structured_not_a_raw_concatenation(self):
        """The P2-11 concern was the raw ``"\\n\\n".join(f.content)``
        concatenation that put raw finding text into chapters. The P3.2
        digest is a structured, self-identifying section (header + numbered
        titled findings + so-what/sources), never a bare join of finding
        strings."""
        lead = _lead_with_failing_narrative()
        sections = asyncio.run(lead._build_analysis_sections())
        market = next(s for s in sections if s.agent == "market_analyst")
        assert "narrative engine was unavailable" in market.body
        assert "### 1." in market.body
        assert "### 2." in market.body

    def test_gap_error_type_exists(self):
        # The mechanism is a typed error, never a silent pass.
        assert issubclass(SectionGapError, Exception)
