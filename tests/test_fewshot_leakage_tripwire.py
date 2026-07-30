"""T-04 · D-02 · few-shot leakage tripwire — *the single test that would
have caught the deliverable received on 07-30*.

The audit (§5, T-04): assert the canonical example tokens never appear in a
generated report. The 07-30 PDF contained the prompt's fabricated numbers
("$2B TAM", "12% penetration") and placeholder citations ("(Source A, 2023)")
as if they were evidence — the quality loop had regurgitated the few-shot
example over the degradation notice.

Two layers of tripwire:

1. DELIVERABLE layer (the audit's spec): a report assembled from findings
   that happen to contain the forbidden tokens is flagged — no matter how
   the tokens got there, they must never reach a reader as evidence.
2. CLASS layer: the tokens must not exist ANYWHERE in the hyperion package
   source — not in prompts, not in docstrings, not in skill descriptions.
   A token that cannot be transcribed cannot leak. This is the fix-the-
   class-not-the-instance guard: it makes the leak impossible rather than
   detecting it after the fact.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    FinalReport,
    KeyFinding,
    Recommendation,
    Source,
    SourceCredibility,
)

FORBIDDEN = [
    "$2B TAM",
    "12% penetration",
    "5% penetration",
    "8% penetration",
    "three dominant players",
    "70% market share",
    "(Source A,",
    "(Source B,",
    "(Source C,",
    "(Source D,",
    "(Source E,",
]

PACKAGE_ROOT = Path(__file__).resolve().parent.parent / "hyperion"


def _report_text(report: FinalReport) -> str:
    """Every human-readable string in the deliverable, flattened."""
    parts: list[str] = [
        report.executive_summary,
        report.recommendation_rationale,
        " ".join(report.critical_assumptions),
        " ".join(report.limitations),
        " ".join(f.title + " " + f.content for f in report.key_findings),
    ]
    for section in report.sections:
        parts.append(section.title)
        parts.append(section.key_insight)
        parts.append(section.body)
        parts.append(section.implications or "")
        parts.extend(f.title + " " + f.content for f in section.findings)
    return "\n".join(parts)


def _leaks(text: str) -> list[str]:
    return [t for t in FORBIDDEN if t.lower() in text.lower()]


class TestPackageSourceCarriesNoExampleTokens:
    """The class fix: if the tokens cannot be transcribed, they cannot leak.
    Scans every .py file in the package — prompts, docstrings, comments."""

    @pytest.mark.parametrize("token", FORBIDDEN)
    def test_token_absent_from_package_source(self, token: str):
        offenders: list[str] = []
        for path in PACKAGE_ROOT.rglob("*.py"):
            if token.lower() in path.read_text(encoding="utf-8").lower():
                offenders.append(str(path.relative_to(PACKAGE_ROOT)))
        assert not offenders, (
            f"few-shot token {token!r} present in package source: {offenders} — "
            "a token that exists in a prompt can be regurgitated into a "
            "deliverable (07-30). Use ⟨…⟩ shape placeholders instead."
        )


class TestDeliverableNeverEchoesExampleTokens:
    def _clean_report(self) -> FinalReport:
        finding = KeyFinding(
            id="f1",
            agent="market_analyst",
            finding_type="market_size",
            title="Imports declined year-on-year",
            content="Merchandise imports fell 14% year-on-year per ministry data.",
            sources=[
                Source(
                    id="s1",
                    title="Ministry of Commerce trade statistics",
                    url="https://example.com/moc",
                    credibility=SourceCredibility.GOVERNMENT,
                )
            ],
            confidence=ConfidenceLevel.HIGH,
            implications="Domestic substitution is already underway.",
        )
        section = AnalysisSection(
            id="section_market_analyst",
            title="Market Landscape",
            agent="market_analyst",
            key_insight="Imports declined year-on-year",
            body=(
                "Merchandise imports fell 14% year-on-year while domestic "
                "capacity utilisation rose, per ministry statistics. "
                * 8  # a real, evidence-carrying body
            ),
            findings=[finding],
            implications="Substitution is underway; policy amplifies it.",
            confidence=ConfidenceLevel.HIGH,
        )
        return FinalReport(
            engagement_id="t",
            question="should india import less ?",
            recommendation=Recommendation.INVESTIGATE,
            recommendation_rationale="Evidence is mixed; further research required.",
            critical_assumptions=["Demand holds"],
            confidence=ConfidenceLevel.MEDIUM,
            confidence_breakdown={},
            executive_summary="Imports are declining; the picture is incomplete.",
            key_findings=[finding],
            sections=[section],
        )

    def test_clean_report_passes_tripwire(self):
        report = self._clean_report()
        assert _leaks(_report_text(report)) == []

    @pytest.mark.parametrize("token", FORBIDDEN)
    def test_contaminated_report_is_flagged(self, token: str):
        """Every forbidden token, injected anywhere in the deliverable, is
        detected. This is the assertion that would have caught the 07-30
        PDF before it shipped."""
        report = self._clean_report()
        report.sections[0].body += f" Analysts note {token} as context."
        hits = _leaks(_report_text(report))
        assert hits == [token]

    def test_placeholder_citation_shape_is_flagged(self):
        """The 07-30 report's '(Source A, 2023)' citations: fabricated
        sources that look like evidence."""
        report = self._clean_report()
        report.executive_summary += " The market is attractive (Source A, 2023)."
        hits = _leaks(_report_text(report))
        assert hits == ["(Source A,"]

    def test_real_fabricated_number_pattern_would_be_flagged(self):
        """Regression for the exact 07-30 sentence shape: the fabricated TAM
        claim as it appeared in the delivered PDF."""
        report = self._clean_report()
        report.executive_summary = (
            "The Indian market presents a $2B TAM opportunity; at 12% "
            "penetration the entry is viable."
        )
        hits = _leaks(_report_text(report))
        assert "$2B TAM" in hits
        assert "12% penetration" in hits

    def test_tripwire_is_case_insensitive(self):
        report = self._clean_report()
        report.sections[0].body += " THE $2B TAM FIGURE "
        assert _leaks(_report_text(report)) == ["$2B TAM"]


class TestTripwireIntegrity:
    def test_forbidden_list_matches_audit(self):
        """Guard against the tripwire itself rotting: the token set is the
        audit's §5 T-04 list, verbatim."""
        assert FORBIDDEN == [
            "$2B TAM",
            "12% penetration",
            "5% penetration",
            "8% penetration",
            "three dominant players",
            "70% market share",
            "(Source A,",
            "(Source B,",
            "(Source C,",
            "(Source D,",
            "(Source E,",
        ]

    def test_tokens_are_not_placeholders(self):
        """The forbidden list must contain the ORIGINAL fabricated values,
        never the ⟨…⟩ placeholders that replaced them."""
        for token in FORBIDDEN:
            assert not re.search(r"[⟨⟩]", token)
