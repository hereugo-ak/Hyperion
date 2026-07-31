"""Confidence is derived, not asserted, from one function (P2-15).

Report A's cover said HIGH while At a Glance said "4 sources" and the WHY
block said CONDITIONAL because "critical sections lack verified sources".
The fix: one derive_confidence(report) read by every surface. HIGH requires
>= 12 sources, >= 3 independent domains, void_ratio == 0, and no critical
dimension below 4. Otherwise clamp to MEDIUM or LOW.
"""

from __future__ import annotations

from hyperion.output.confidence import derive_confidence
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    FinalReport,
    Recommendation,
    Source,
    SourceCredibility,
)


def _source(n: int, domain: str) -> Source:
    return Source(
        id=f"src_{n}",
        title=f"Source {n}",
        url=f"https://{domain}/article-{n}",
        credibility=SourceCredibility.INDUSTRY_REPORT,
    )


def _section(agent: str, sources: list[Source]) -> AnalysisSection:
    return AnalysisSection(
        id=f"section_{agent}",
        title=agent.replace("_", " ").title(),
        agent=agent,
        key_insight="k",
        body="b" * 120,
        implications="i",
        sources=sources,
        confidence=ConfidenceLevel.HIGH,
    )


def _report(sections: list[AnalysisSection], total_sources: int) -> FinalReport:
    return FinalReport(
        engagement_id="e1",
        question="Q",
        recommendation=Recommendation.ENTER,
        recommendation_rationale="rationale",
        critical_assumptions=["a"],
        confidence=ConfidenceLevel.HIGH,
        confidence_breakdown={},
        executive_summary="summary",
        sections=sections,
        total_sources=total_sources,
    )


class TestDeriveConfidence:
    def test_high_requires_12_sources_3_domains_no_voids(self):
        domains = ["a.com", "b.com", "c.com", "d.com"]
        sections = [
            _section("market_analyst", [_source(1, domains[0]), _source(2, domains[1])]),
            _section("financial_analyst", [_source(3, domains[2]), _source(4, domains[3])]),
            _section("risk_analyst", [_source(5, domains[0])]),
        ]
        report = _report(sections, total_sources=13)
        assert derive_confidence(report) == ConfidenceLevel.HIGH

    def test_under_12_sources_clamps_below_high(self):
        domains = ["a.com", "b.com", "c.com"]
        sections = [
            _section("market_analyst", [_source(1, domains[0])]),
            _section("risk_analyst", [_source(2, domains[1]), _source(3, domains[2])]),
        ]
        # 4 sources, 3 domains, no voids: passes domains+voids, fails source floor.
        report = _report(sections, total_sources=4)
        assert derive_confidence(report) != ConfidenceLevel.HIGH

    def test_under_3_domains_clamps_below_high(self):
        sections = [
            _section("market_analyst", [_source(i, "only.com") for i in range(15)]),
        ]
        report = _report(sections, total_sources=15)
        assert derive_confidence(report) != ConfidenceLevel.HIGH

    def test_any_unsourced_section_clamps_below_high(self):
        domains = ["a.com", "b.com", "c.com"]
        sections = [
            _section("market_analyst", [_source(1, domains[0])]),
            _section("financial_analyst", [_source(2, domains[1])]),
            _section("risk_analyst", [_source(3, domains[2])]),
            _section("operations_analyst", []),  # void
        ]
        report = _report(sections, total_sources=13)
        assert derive_confidence(report) != ConfidenceLevel.HIGH

    def test_very_thin_evidence_is_low(self):
        report = _report([_section("market_analyst", [_source(1, "a.com")])], total_sources=1)
        assert derive_confidence(report) == ConfidenceLevel.LOW

    def test_deterministic_same_input_same_output(self):
        domains = ["a.com", "b.com", "c.com"]
        sections = [
            _section("market_analyst", [_source(1, domains[0])]),
            _section("risk_analyst", [_source(2, domains[1]), _source(3, domains[2])]),
        ]
        report = _report(sections, total_sources=4)
        assert derive_confidence(report) == derive_confidence(report)
