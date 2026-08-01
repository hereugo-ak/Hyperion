"""W-05: contradiction detection is rebuilt around opposition, not inequality.

A contradiction is two claims about the SAME subject with incompatible
values or polarity. Nothing else may be called a contradiction. These tests
pin the five audit spec cases plus the structural invariants:

- ``finding_a``/``finding_b`` are never assigned from a title field.
- The rendered Position text is exactly the compared text (single string).
- Telemetry ("Confidence: low") is ineligible — never a Position.
- Zero-opposition runs produce zero contradictions.
"""

from __future__ import annotations

import inspect
import re

import pytest

from hyperion.agents.claim_triples import (
    MAX_MATERIAL_CONTRADICTIONS,
    extract_measurements,
    extract_triple,
    is_telemetry,
    numeric_opposition,
    opposition,
    subjects_match,
)
from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.schemas.models import (
    ConfidenceLevel,
    ContradictionType,
    KeyFinding,
    Source,
    SourceCredibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


def _finding(
    agent: str,
    ftype: str,
    content: str,
    title: str = "Finding",
    n_sources: int = 2,
    confidence: ConfidenceLevel = ConfidenceLevel.MEDIUM,
) -> KeyFinding:
    return KeyFinding(
        id=f"{agent}-{abs(hash(content)) % 10**6}",
        agent=agent,
        finding_type=ftype,
        title=title,
        content=content,
        confidence=confidence,
        sources=[
            Source(
                id=f"src_{agent}_{i}",
                url=f"https://example.org/{agent}/{i}",
                title=f"Source {i}",
                credibility=SourceCredibility.INDUSTRY_REPORT,
            )
            for i in range(n_sources)
        ],
    )


def _lead_with(findings: list[KeyFinding]) -> SynthesisLead:
    lead = SynthesisLead()
    lead._collected_findings = list(findings)
    return lead


def _triple(agent, pred, text, sc=2, conf="medium"):
    return extract_triple(
        agent=agent, predicate=pred, claim_text=text, source_count=sc, confidence=conf
    )


# ─────────────────────────────────────────────────────────────────────────────
# The five audit spec cases
# ─────────────────────────────────────────────────────────────────────────────


class TestSpecCases:
    def test_case1_identical_titles_different_bodies_no_shared_subject(self):
        """Identical titles, different bodies, no shared subject -> 0."""
        lead = _lead_with(
            [
                _finding(
                    "market_analyst",
                    "market_data",
                    "Key insight: cloud adoption rose 12% in 2024 across Europe",
                    title="Key insight",
                ),
                _finding(
                    "competitor_analyst",
                    "market_data",
                    "Key insight: textile exports to ASEAN reached $4B in 2024",
                    title="Key insight",
                ),
            ]
        )
        matrix = lead._build_finding_matrix()
        assert lead._identify_contradictions(matrix) == []

    def test_case2_temporal_guard(self):
        """Same quantity, different periods -> 0 contradictions."""
        a = _triple("a1", "market_data", "India manufacturing share is 17% in 2023")
        b = _triple("a2", "market_data", "India manufacturing share is 14% in 2015")
        assert a is not None and b is not None
        assert opposition(a, b) is None

        lead = _lead_with(
            [
                _finding("a1", "market_data", "India manufacturing share is 17% in 2023"),
                _finding("a2", "market_data", "India manufacturing share is 14% in 2015"),
            ]
        )
        assert lead._identify_contradictions(lead._build_finding_matrix()) == []

    def test_case3_unit_normalisation(self):
        """$1.2B vs $1200M is the same number -> 0 contradictions."""
        a = _triple("a1", "financials", "The deal is valued at $1.2B")
        b = _triple("a2", "financials", "The deal is valued at $1200M")
        assert a is not None and b is not None
        assert a.measurement.value == pytest.approx(b.measurement.value)
        assert opposition(a, b) is None

    def test_case4_supports_vs_opposes_same_proposition(self):
        """Polarity opposition on the same proposition -> 1 contradiction."""
        lead = _lead_with(
            [
                _finding("a1", "policy", "The ministry supports tariff on imported steel"),
                _finding("a2", "policy", "The ministry opposes tariff on imported steel"),
            ]
        )
        contradictions = lead._identify_contradictions(lead._build_finding_matrix())
        assert len(contradictions) == 1
        c = contradictions[0]
        assert c.contradiction_type == ContradictionType.INTERPRETATION_CONFLICT
        assert {c.agent_a, c.agent_b} == {"a1", "a2"}

    def test_case5_telemetry_finding_is_ineligible(self):
        """A finding whose body is 'Confidence: low' is never a Position."""
        assert is_telemetry("Confidence: low")
        assert _triple("a1", "policy", "Confidence: low") is None

        lead = _lead_with(
            [
                _finding("a1", "policy", "Confidence: low"),
                _finding("a2", "policy", "The ministry supports tariff on imported steel"),
                _finding("a3", "policy", "The ministry opposes tariff on imported steel"),
            ]
        )
        contradictions = lead._identify_contradictions(lead._build_finding_matrix())
        assert len(contradictions) == 1
        c = contradictions[0]
        assert "Confidence" not in c.finding_a
        assert "Confidence" not in c.finding_b
        assert "a1" not in (c.agent_a, c.agent_b)


# ─────────────────────────────────────────────────────────────────────────────
# Detector semantics
# ─────────────────────────────────────────────────────────────────────────────


class TestOppositionSemantics:
    def test_numeric_conflict_same_period_is_data_conflict(self):
        a = _triple("a1", "market_data", "India manufacturing share is 17% in 2023")
        b = _triple("a2", "market_data", "India manufacturing share is 8% in 2023")
        assert opposition(a, b) == "data_conflict"

    def test_within_relative_tolerance_is_not_a_conflict(self):
        a = _triple("a1", "market_data", "Share is 17% in 2023")
        b = _triple("a2", "market_data", "Share is 18% in 2023")
        assert opposition(a, b) is None

    def test_predicate_mismatch_never_pairs(self):
        a = _triple("a1", "market_data", "India manufacturing share is 17% in 2023")
        b = _triple("a2", "risk", "India manufacturing share is 8% in 2023")
        assert opposition(a, b) is None

    def test_same_agent_never_pairs(self):
        lead = _lead_with(
            [
                _finding("a1", "market_data", "India manufacturing share is 17% in 2023"),
                _finding("a1", "market_data", "India manufacturing share is 8% in 2023"),
            ]
        )
        assert lead._identify_contradictions(lead._build_finding_matrix()) == []

    def test_substring_is_not_subject_match(self):
        """'India' must not pair with 'Indian textiles' by containment."""
        a = _triple("a1", "market_data", "India GDP grew 7% in 2023")
        b = _triple("a2", "market_data", "Indian textiles export volume fell 40% in 2023")
        assert a is not None and b is not None
        assert not subjects_match(a, b)
        assert opposition(a, b) is None

    def test_mixed_numeric_categorical_never_contradicts(self):
        a = _triple("a1", "policy", "Tariff share is 25% under the current regime")
        b = _triple("a2", "policy", "The ministry opposes tariff under the current regime")
        assert a is not None and b is not None
        assert a.measurement is not None and b.polarity is not None
        assert opposition(a, b) is None

    def test_year_tokens_are_not_measurements(self):
        ms = extract_measurements("Growth continued through 2023")
        assert ms == []

    def test_unit_normalisation_examples(self):
        (m_b,) = extract_measurements("$1.2B")
        (m_m,) = extract_measurements("$1200M")
        assert m_b.value == pytest.approx(m_m.value)
        assert not numeric_opposition(m_b, m_m)

    def test_temporal_guard_none_year_does_not_shield(self):
        """A measurement without a period still conflicts with a dated one."""
        a = _triple("a1", "market_data", "Share is 17% in 2023")
        b = _triple("a2", "market_data", "Share is 8%")
        assert opposition(a, b) == "data_conflict"


# ─────────────────────────────────────────────────────────────────────────────
# Structural invariants
# ─────────────────────────────────────────────────────────────────────────────


class TestInvariants:
    def test_finding_fields_never_come_from_title(self):
        """finding_a/finding_b are the compared claim text, never titles."""
        lead = _lead_with(
            [
                _finding(
                    "a1", "market_data",
                    "India manufacturing share is 17% in 2023",
                    title="Manufacturing update",
                ),
                _finding(
                    "a2", "market_data",
                    "India manufacturing share is 8% in 2023",
                    title="Manufacturing update",
                ),
            ]
        )
        contradictions = lead._identify_contradictions(lead._build_finding_matrix())
        assert len(contradictions) == 1
        c = contradictions[0]
        assert c.finding_a != "Manufacturing update"
        assert c.finding_b != "Manufacturing update"
        assert c.finding_a in {
            "India manufacturing share is 17% in 2023",
            "India manufacturing share is 8% in 2023",
        }
        assert c.finding_b in {
            "India manufacturing share is 17% in 2023",
            "India manufacturing share is 8% in 2023",
        }
        assert c.finding_a != c.finding_b

    def test_compared_text_equals_rendered_text(self):
        """The detector compares claim_text and the appendix renders
        finding_a/finding_b — both are the same single string."""
        a = _triple("a1", "policy", "The ministry supports tariff on imported steel")
        b = _triple("a2", "policy", "The ministry opposes tariff on imported steel")
        assert opposition(a, b) is not None

        lead = _lead_with(
            [
                _finding("a1", "policy", "The ministry supports tariff on imported steel"),
                _finding("a2", "policy", "The ministry opposes tariff on imported steel"),
            ]
        )
        (c,) = lead._identify_contradictions(lead._build_finding_matrix())
        compared = {a.claim_text, b.claim_text}
        assert {c.finding_a, c.finding_b} == compared

    def test_source_has_no_title_assignment(self):
        """The detector source never assigns finding_a/b from a title field."""
        import hyperion.agents.synthesis_lead as sl

        src = inspect.getsource(sl.SynthesisLead._identify_contradictions)
        assert not re.search(r'finding_a\s*=\s*\w+\["title"\]', src)
        assert not re.search(r'finding_b\s*=\s*\w+\["title"\]', src)
        assert '["title"]' not in src

    def test_old_inequality_detector_gone(self):
        """The old inequality predicate and classifier are deleted, not kept
        behind a flag."""
        assert not hasattr(SynthesisLead, "_classify_contradiction")
        import hyperion.agents.synthesis_lead as sl

        src = inspect.getsource(sl.SynthesisLead._identify_contradictions)
        assert "content_a" not in src

    def test_budget_cap(self):
        """Opposing pairs beyond the cap are dropped by materiality."""
        findings = []
        for i in range(MAX_MATERIAL_CONTRADICTIONS + 3):
            findings.append(
                _finding("a1", "market_data", f"Metric{i} share is 17% in 2023")
            )
            findings.append(
                _finding("a2", "market_data", f"Metric{i} share is 8% in 2023")
            )
        lead = _lead_with(findings)
        contradictions = lead._identify_contradictions(lead._build_finding_matrix())
        assert len(contradictions) == MAX_MATERIAL_CONTRADICTIONS

    def test_zero_opposition_run_produces_no_contradictions(self):
        """No fact-check report, no opposing triples -> empty list."""
        lead = _lead_with(
            [
                _finding("a1", "market_data", "Share is 17% in 2023"),
                _finding("a2", "market_data", "Share is 18% in 2023"),
            ]
        )
        assert lead._fact_check_report is None
        assert lead._identify_contradictions(lead._build_finding_matrix()) == []


# ─────────────────────────────────────────────────────────────────────────────
# Resolution lookup (claim text, not title)
# ─────────────────────────────────────────────────────────────────────────────


class TestResolutionLookup:
    @pytest.mark.asyncio
    async def test_resolve_finds_findings_by_claim_text(self):
        f1 = _finding(
            "a1", "market_data",
            "India manufacturing share is 17% in 2023",
            title="T1", n_sources=4, confidence=ConfidenceLevel.HIGH,
        )
        f2 = _finding(
            "a2", "market_data",
            "India manufacturing share is 8% in 2023",
            title="T2", n_sources=1, confidence=ConfidenceLevel.LOW,
        )
        lead = _lead_with([f1, f2])
        contradictions = lead._identify_contradictions(lead._build_finding_matrix())
        assert len(contradictions) == 1

        resolved = await lead._resolve_contradictions(
            contradictions, lead._build_finding_matrix()
        )
        assert len(resolved) == 1
        assert resolved[0].resolved is True
        assert "Could not locate" not in (resolved[0].resolution or "")
        # The better-evidenced side wins.
        assert resolved[0].evidence_weighted_winner == "a1"

    def test_title_fallback_for_fact_check_contradictions(self):
        """Fact-check-origin contradictions (title strings) still resolve."""
        f1 = _finding("a1", "risk", "Some risk body", title="Risk title")
        lead = _lead_with([f1])
        assert lead._find_finding_by_agent_and_title("a1", "Risk title") is f1
        assert lead._find_finding_by_agent_and_title("a1", "Some risk body") is f1
        assert lead._find_finding_by_agent_and_title("nobody", "Risk title") is None
