"""The Fact Checker must never become a client-facing chapter (P2-12).

``_findings_by_agent`` is keyed by ANY sender on Channel.FINDINGS. Sections
were built one per key, so "Fact Checker" became a chapter, appeared in both
reports' TOCs, and its QA-log content ("17 hallucinated citations") was
quoted into At a Glance and the Executive Summary.

After the fix:
1. ``SECTION_PRODUCING_AGENTS`` holds the 12 specialists only; the section
   builder iterates the allowlist, not every bus sender. OVERHAUL4 P3.1
   added ``STRATEGY_ANALYST`` (11 -> 12) — a strategy-only engagement
   produced zero chapters before that.
2. The Layer 4 gate rejects client-visible meta-text: hallucinat*,
   unverified claim, fact checker, quality gate, iteration, parse error,
   data sparse.
"""

from __future__ import annotations

import asyncio

from hyperion.agents.support.quality_gate import QualityGate
from hyperion.agents.synthesis_lead import (
    SECTION_PRODUCING_AGENTS,
    SynthesisLead,
)
from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import (
    ConfidenceLevel,
    FinalReport,
    KeyFinding,
    Recommendation,
)


def _finding(agent: str, i: int) -> KeyFinding:
    return KeyFinding(
        id=f"f{i}",
        agent=agent,
        finding_type="market_size",
        title=f"Finding {i}",
        content=f"Substantive content {i}.",
        confidence=ConfidenceLevel.MEDIUM,
    )


class TestSectionProducingAgents:
    def test_allowlist_is_twelve_specialists_only(self):
        # OVERHAUL4 P3.1: strategy_analyst joined the allowlist (11 -> 12) so
        # a strategy-only engagement produces chapters; the assertion tracks
        # the live set rather than a stale count.
        assert len(SECTION_PRODUCING_AGENTS) == 12
        assert AgentName.FACT_CHECKER not in SECTION_PRODUCING_AGENTS
        assert AgentName.QUALITY_GATE not in SECTION_PRODUCING_AGENTS
        assert AgentName.SYNTHESIS_LEAD not in SECTION_PRODUCING_AGENTS
        assert AgentName.MARKET_ANALYST in SECTION_PRODUCING_AGENTS

    def test_fact_checker_findings_never_become_a_section(self):
        lead = SynthesisLead.__new__(SynthesisLead)
        lead._question = "Q"
        lead._findings_by_agent = {
            "market_analyst": [_finding("market_analyst", i) for i in range(3)],
            # The Fact Checker publishes findings; they must not become a chapter.
            "fact_checker": [
                _finding("fact_checker", 90),
            ],
        }
        lead.section_gaps = []

        async def _narrative(**kwargs):
            class R:
                success = True
                content = "A long analytical narrative body. " * 60

            return R()

        lead._llm_complete = _narrative  # type: ignore[method-assign]

        sections = asyncio.run(lead._build_analysis_sections())
        agents = {s.agent for s in sections}
        assert "fact_checker" not in agents
        assert all("Fact Checker" not in s.title for s in sections)


class TestMetaTextBlocklist:
    def _gate(self) -> QualityGate:
        return QualityGate.__new__(QualityGate)

    def _report(self, exec_summary: str) -> FinalReport:
        return FinalReport(
            engagement_id="e1",
            question="Q",
            recommendation=Recommendation.ENTER,
            recommendation_rationale="rationale",
            critical_assumptions=["a"],
            confidence=ConfidenceLevel.MEDIUM,
            confidence_breakdown={},
            executive_summary=exec_summary,
        )

    def test_hallucination_meta_text_is_a_blocker(self):
        gate = self._gate()
        report = self._report("17 hallucinated citations break evidence chains.")
        blockers = gate._detect_hard_blockers(report)
        assert blockers, "expected a hard blocker for QA-log meta-text"

    def test_fact_checker_meta_text_is_a_blocker(self):
        gate = self._gate()
        report = self._report("The Fact Checker verified 40% of claims.")
        blockers = gate._detect_hard_blockers(report)
        assert blockers

    def test_clean_exec_summary_has_no_meta_blocker(self):
        gate = self._gate()
        report = self._report(
            "The market supports entry at high penetration with a viable TAM."
        )
        blockers = gate._detect_hard_blockers(report)
        assert not any("META" in b for b in blockers)
