"""Single source of truth for report confidence (P2-15).

Report A's cover said HIGH, At a Glance said "4 sources", and the WHY block
on the same page said the recommendation was CONDITIONAL because "critical
sections lack verified sources". Three surfaces, three answers, one report.

The fix: confidence is DERIVED from the evidence, never asserted, and every
surface (cover, At a Glance, Executive Summary, Technical Appendix) reads
this one function.

Rules (gate P2-G31):
  HIGH   requires >= 12 total sources, >= 3 independent source domains,
         void_ratio == 0 (no unsourced section), and no critical quality
         dimension below 4.
  MEDIUM requires >= 6 total sources and >= 2 independent domains, with
         void_ratio < 0.5.
  LOW    everything else.
"""

from __future__ import annotations

from urllib.parse import urlparse

from hyperion.schemas.models import ConfidenceLevel, FinalReport

__all__ = [
    "derive_confidence",
    "HIGH_MIN_SOURCES",
    "HIGH_MIN_DOMAINS",
    "MEDIUM_MIN_SOURCES",
    "MEDIUM_MIN_DOMAINS",
]

HIGH_MIN_SOURCES = 12
HIGH_MIN_DOMAINS = 3
MEDIUM_MIN_SOURCES = 6
MEDIUM_MIN_DOMAINS = 2


def _domain(url: str) -> str:
    """Registered-domain-ish key for independence counting."""
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def _report_domains(report: FinalReport) -> set[str]:
    domains: set[str] = set()
    for sec in report.sections:
        for src in sec.sources:
            d = _domain(src.url or "")
            if d:
                domains.add(d)
    for kf in report.key_findings:
        for src in kf.sources:
            d = _domain(src.url or "")
            if d:
                domains.add(d)
    return domains


def derive_confidence(report: FinalReport) -> ConfidenceLevel:
    """Derive the single confidence level every surface must display."""
    total_sources = report.total_sources
    domains = _report_domains(report)
    total_sections = len(report.sections) or 1
    unsourced = sum(1 for sec in report.sections if not sec.sources)
    void_ratio = unsourced / total_sections

    critical_below_4 = False
    if report.quality_score is not None:
        critical_below_4 = bool(report.quality_score.critical_dimensions)

    if (
        total_sources >= HIGH_MIN_SOURCES
        and len(domains) >= HIGH_MIN_DOMAINS
        and void_ratio == 0
        and not critical_below_4
    ):
        return ConfidenceLevel.HIGH

    if (
        total_sources >= MEDIUM_MIN_SOURCES
        and len(domains) >= MEDIUM_MIN_DOMAINS
        and void_ratio < 0.5
    ):
        return ConfidenceLevel.MEDIUM

    return ConfidenceLevel.LOW
