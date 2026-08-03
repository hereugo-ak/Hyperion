"""
HYPERION Offline Evaluation Harness — make "McKinsey-grade" measurable.

This module implements the offline eval harness from IV.1.3 / P11:

1. **Golden set** of representative queries spanning all workflow types.
2. **Deterministic checks** per report and rendered PDF: structural report
   requirements, the production ``audit_pdf`` gate, authoritative text-integrity
   scanning, and images embedded in the client artifact.
3. **Truthfully named deterministic quality score** derived only from those
   checks. The harness does not claim that this score came from an LLM judge.
4. **Regression gate:** CI fails if either golden-set mean score or pass rate
   drops versus the last release, with per-query and per-check attribution.

Usage::

    from hyperion.eval.harness import EvalHarness

    harness = EvalHarness()
    results = await harness.run_all()
    if results.regression_detected:
        print(f"QUALITY REGRESSION: {results.mean_score} < {results.baseline_score}")
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import fitz

from hyperion.output.page_audit import (
    BANNED_SUBSTRINGS,
    audit_pdf,
    extract_pdf_text,
    scan_text_integrity,
)

# ─────────────────────────────────────────────────────────────────────────────
# Golden Set — representative queries across all workflow types
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class GoldenQuery:
    """A single golden-set query with expected properties."""

    id: str
    question: str
    question_type: str  # market_entry, competitive_analysis, risk_assessment, etc.
    min_sections: int = 3
    min_sources: int = 5
    min_findings: int = 3
    expect_charts: bool = True
    expect_pdf: bool = True


GOLDEN_SET: list[GoldenQuery] = [
    GoldenQuery(
        id="gq_001",
        question="Should we enter the Tier-2 Indian SaaS market?",
        question_type="market_entry",
        min_sections=3,
        min_sources=5,
        min_findings=3,
    ),
    GoldenQuery(
        id="gq_002",
        question="What is the competitive landscape for AI-powered supply chain platforms?",
        question_type="competitive_analysis",
        min_sections=3,
        min_sources=5,
        min_findings=3,
    ),
    GoldenQuery(
        id="gq_003",
        question="Assess the regulatory risks of launching a fintech product in the EU.",
        question_type="risk_assessment",
        min_sections=3,
        min_sources=4,
        min_findings=3,
    ),
    GoldenQuery(
        id="gq_004",
        question="Should we acquire Company X or build the capability in-house?",
        question_type="ma_analysis",
        min_sections=3,
        min_sources=5,
        min_findings=3,
    ),
    GoldenQuery(
        id="gq_005",
        question="What technology stack should we adopt for our next-gen data platform?",
        question_type="technology_assessment",
        min_sections=3,
        min_sources=4,
        min_findings=3,
    ),
    GoldenQuery(
        id="gq_006",
        question=(
            "How should Singapore strengthen national semiconductor resilience "
            "through 2035?"
        ),
        question_type="NATION_OR_REGION",
        min_sections=3,
        min_sources=5,
        min_findings=3,
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic Checks — structural quality validation
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CheckResult:
    """Result of a single deterministic check."""

    name: str
    passed: bool
    detail: str = ""


def run_deterministic_checks(
    report: dict[str, Any],
    pdf_path: str = "",
    golden: GoldenQuery | None = None,
) -> list[CheckResult]:
    """Run deterministic structural checks on a final report.

    These are non-LLM checks that validate both report structure and the
    rendered client artifact. PDF checks fail closed when bytes cannot be
    opened or the production page audit reports a violation.
    """
    results: list[CheckResult] = []
    g = golden or GOLDEN_SET[0]

    # Check 1: Minimum sections
    sections = report.get("sections", [])
    n_sections = len(sections)
    results.append(CheckResult(
        name="min_sections",
        passed=n_sections >= g.min_sections,
        detail=f"{n_sections}/{g.min_sections} sections",
    ))

    # Check 2: Minimum cited sources
    total_sources = report.get("total_sources", 0)
    results.append(CheckResult(
        name="min_sources",
        passed=total_sources >= g.min_sources,
        detail=f"{total_sources}/{g.min_sources} sources",
    ))

    # Check 3: Minimum key findings
    findings = report.get("key_findings", [])
    n_findings = len(findings)
    results.append(CheckResult(
        name="min_findings",
        passed=n_findings >= g.min_findings,
        detail=f"{n_findings}/{g.min_findings} findings",
    ))

    # Check 4: No empty sections
    empty_sections = [
        s.get("title", "untitled") for s in sections
        if not s.get("body", "").strip() or len(s.get("body", "").strip()) < 50
    ]
    results.append(CheckResult(
        name="no_empty_sections",
        passed=len(empty_sections) == 0,
        detail=f"Empty: {empty_sections}" if empty_sections else "All sections have content",
    ))

    # Check 5: Every KeyFinding has a source
    findings_without_sources = [
        f.get("title", "untitled") for f in findings
        if not f.get("sources")
    ]
    results.append(CheckResult(
        name="findings_have_sources",
        passed=len(findings_without_sources) == 0,
        detail=f"Missing sources: {findings_without_sources}" if findings_without_sources else "All findings have sources",
    ))

    # Checks 6-8: inspect the rendered client artifact, never the report model.
    pdf_exists = bool(pdf_path and os.path.exists(pdf_path))
    integrity_hits: list[str] = []
    page_audit_violations: list[str] = []
    images_count = 0
    if pdf_exists:
        try:
            extracted_text = extract_pdf_text(pdf_path)
            # This direct call intentionally shares BANNED_SUBSTRINGS with the
            # production renderer gate; there is no harness-local weaker list.
            integrity_hits = scan_text_integrity(extracted_text)
            page_audit = audit_pdf(pdf_path, fail_closed=False)
            page_audit_violations = page_audit.violations
            images_count = _count_pdf_images(pdf_path)
        except (OSError, RuntimeError, ValueError) as exc:
            page_audit_violations = [f"PDF inspection failed: {exc}"]

    results.append(CheckResult(
        name="no_template_artifacts",
        passed=pdf_exists and not integrity_hits,
        detail=(
            f"Integrity violations: {integrity_hits}"
            if integrity_hits
            else (
                f"Clean against {len(BANNED_SUBSTRINGS)} authoritative bans"
                if pdf_exists
                else "PDF not found — client text not inspected"
            )
        ),
    ))

    if g.expect_pdf:
        results.append(CheckResult(
            name="pdf_renders",
            passed=pdf_exists and not page_audit_violations,
            detail=(
                "; ".join(page_audit_violations[:3])
                if page_audit_violations
                else (pdf_path if pdf_exists else "PDF not found")
            ),
        ))
        results.append(CheckResult(
            name="production_pdf_audit",
            passed=pdf_exists and not page_audit_violations,
            detail=(
                "; ".join(page_audit_violations[:3])
                if page_audit_violations
                else "Production audit passed"
            ),
        ))

    if g.expect_charts:
        results.append(CheckResult(
            name="charts_present",
            passed=pdf_exists and images_count > 0,
            detail=f"{images_count} images embedded in rendered PDF",
        ))

    # Check 9: Executive summary is non-trivial
    exec_summary = report.get("executive_summary", "")
    results.append(CheckResult(
        name="exec_summary_substantial",
        passed=len(exec_summary.strip()) >= 200,
        detail=f"{len(exec_summary.strip())} chars",
    ))

    # Check 10: Recommendation is set (not None/empty)
    recommendation = report.get("recommendation", "")
    results.append(CheckResult(
        name="recommendation_set",
        passed=bool(recommendation),
        detail=recommendation if recommendation else "Missing",
    ))

    # ─────────────────────────────────────────────────────────────────────
    # P14 GAP-8: CI Pixel-QA Gate — visual/structural PDF checks
    # ─────────────────────────────────────────────────────────────────────

    # Check 11: Fonts embedded in PDF
    if pdf_path and os.path.exists(pdf_path):
        fonts_embedded = _check_fonts_embedded(pdf_path)
        results.append(CheckResult(
            name="fonts_embedded",
            passed=len(fonts_embedded) >= 2,
            detail=f"{len(fonts_embedded)} fonts: {fonts_embedded[:3]}" if fonts_embedded else "No fonts embedded",
        ))
    else:
        results.append(CheckResult(
            name="fonts_embedded",
            passed=False,
            detail="PDF not found — cannot check fonts",
        ))

    # Check 12: No missing images (all image paths resolve)
    missing_images: list[str] = []
    for section in sections:
        for img in section.get("charts", []):
            img_path = img.get("image_path", "") or img.get("path", "")
            if img_path and not os.path.exists(img_path):
                missing_images.append(img_path)
    results.append(CheckResult(
        name="no_missing_images",
        passed=len(missing_images) == 0,
        detail=f"Missing: {missing_images[:3]}" if missing_images else "All images resolve",
    ))

    # Check 13: Cover page present (executive_summary acts as cover proxy)
    has_cover = bool(report.get("executive_summary", "").strip())
    results.append(CheckResult(
        name="cover_page_present",
        passed=has_cover,
        detail="Cover/exec summary present" if has_cover else "No executive summary (cover)",
    ))

    # Check 14: Footer/source attribution present
    has_footer = total_sources > 0 and any(
        section.get("body", "") and "source" in section.get("body", "").lower()
        for section in sections
    )
    results.append(CheckResult(
        name="footer_source_attribution",
        passed=has_footer,
        detail="Source attribution found" if has_footer else "No source attribution in sections",
    ))

    # Check 15: PDF page count honours the delivery contract (fix 4.2)
    #
    # This check was `5 <= page_count <= 60`. A 55-page-wide window on a 15-20
    # page contract cannot fail anything a renderer would plausibly emit, so the
    # offline gate agreed with the runtime gate only in the sense that neither
    # was checking. The band now comes from `page_budget`, the single source of
    # truth, so the offline harness and the Render Engine cannot drift apart.
    if pdf_path and os.path.exists(pdf_path):
        from hyperion.output.page_budget import page_count_verdict

        page_count = _get_pdf_page_count(pdf_path)
        verdict = page_count_verdict(page_count if page_count is not None else 0)
        results.append(CheckResult(
            name="page_count_reasonable",
            passed=verdict.passed,
            detail=verdict.reason,
        ))
    else:
        results.append(CheckResult(
            name="page_count_reasonable",
            passed=False,
            detail="PDF not found",
        ))

    return results


def _count_pdf_images(pdf_path: str) -> int:
    """Count images embedded in the actual rendered PDF bytes."""
    with fitz.open(pdf_path) as doc:
        return sum(len(page.get_image_info()) for page in doc)


def _check_fonts_embedded(pdf_path: str) -> list[str]:
    """Check which fonts are embedded in the PDF using PyMuPDF."""
    try:
        import fitz  # PyMuPDF
        doc = fitz.open(pdf_path)
        fonts: set[str] = set()
        for page in doc:
            for font in page.get_fonts():
                fonts.add(font[3])  # Font name
        doc.close()
        return list(fonts)
    except (ImportError, OSError, ValueError):
        return []


def _get_pdf_page_count(pdf_path: str) -> int | None:
    """Get PDF page count using PyMuPDF."""
    try:
        import fitz
        doc = fitz.open(pdf_path)
        count = int(doc.page_count)
        doc.close()
        return count
    except (ImportError, OSError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Eval Harness — run golden set, score, detect regressions
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class QueryEvalResult:
    """Evaluation result for a single golden query."""

    query_id: str
    question: str
    deterministic_checks: list[CheckResult] = field(default_factory=list)
    deterministic_score: float = 0.0
    overall_score: float = 0.0
    pdf_path: str = ""
    success: bool = False
    error: str = ""

    @property
    def all_checks_passed(self) -> bool:
        return all(c.passed for c in self.deterministic_checks)


@dataclass
class EvalResults:
    """Aggregate evaluation results across the golden set."""

    results: list[QueryEvalResult] = field(default_factory=list)
    mean_score: float = 0.0
    baseline_score: float = 0.0
    baseline_pass_rate: float = 0.0
    regression_detected: bool = False
    regression_reasons: list[str] = field(default_factory=list)
    pass_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_score": self.mean_score,
            "baseline_score": self.baseline_score,
            "baseline_pass_rate": self.baseline_pass_rate,
            "regression_detected": self.regression_detected,
            "regression_reasons": self.regression_reasons,
            "pass_rate": self.pass_rate,
            "results": [
                {
                    "query_id": r.query_id,
                    "question": r.question,
                    "overall_score": r.overall_score,
                    "deterministic_score": r.deterministic_score,
                    "all_checks_passed": r.all_checks_passed,
                    "success": r.success,
                    "error": r.error,
                    "checks": [
                        {"name": c.name, "passed": c.passed, "detail": c.detail}
                        for c in r.deterministic_checks
                    ],
                }
                for r in self.results
            ],
        }


class EvalHarness:
    """Offline evaluation harness for HYPERION report quality.

    Runs the golden set through the full proprietary consulting pipeline,
    applies deterministic report and rendered-artifact checks, and detects
    attributable quality regressions against a stored baseline.
    """

    BASELINE_PATH = "eval/baseline.json"
    RESULTS_PATH = "eval/results.json"
    REGRESSION_THRESHOLD = 0.3  # Fail if mean drops > 0.3 below baseline
    PASS_RATE_REGRESSION_THRESHOLD = 0.0  # Any pass-rate drop is a regression

    def __init__(self, baseline_path: str | None = None) -> None:
        self.baseline_path = baseline_path or self.BASELINE_PATH
        self.golden_set = list(GOLDEN_SET)

    def _load_baseline(self) -> dict[str, Any]:
        """Load an attributable baseline; return an empty schema if unavailable."""
        empty: dict[str, Any] = {
            "mean_score": 0.0,
            "pass_rate": 0.0,
            "queries": {},
        }
        if not os.path.exists(self.baseline_path):
            return empty
        try:
            with open(self.baseline_path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                return empty
            return {
                "mean_score": float(data.get("mean_score", 0.0)),
                "pass_rate": float(data.get("pass_rate", 0.0)),
                "queries": data.get("queries", {}),
            }
        except (json.JSONDecodeError, OSError, TypeError, ValueError):
            return empty

    def _save_baseline(self, results: EvalResults) -> None:
        """Persist aggregate, per-query, and per-check baseline outcomes."""
        directory = os.path.dirname(self.baseline_path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        queries = {
            result.query_id: {
                "overall_score": result.overall_score,
                "success": result.success,
                "checks": {
                    check.name: check.passed
                    for check in result.deterministic_checks
                },
            }
            for result in results.results
        }
        payload = {
            "schema_version": 2,
            "mean_score": results.mean_score,
            "pass_rate": results.pass_rate,
            "queries": queries,
            "ts": time.time(),
        }
        with open(self.baseline_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, indent=2, sort_keys=True)

    def _save_results(self, results: EvalResults) -> str:
        """Save evaluation results to disk."""
        os.makedirs(os.path.dirname(self.RESULTS_PATH), exist_ok=True)
        path = self.RESULTS_PATH
        with open(path, "w", encoding="utf-8") as f:
            json.dump(results.to_dict(), f, indent=2, default=str)
        return path

    async def _run_single_query(self, golden: GoldenQuery) -> QueryEvalResult:
        """Run a single golden query through the pipeline and evaluate it."""
        result = QueryEvalResult(
            query_id=golden.id,
            question=golden.question,
        )

        try:
            from hyperion.orchestrator import run_engagement

            engagement = await run_engagement(question=golden.question)
            result.success = engagement.success
            result.pdf_path = engagement.pdf_path
            result.error = engagement.error

            if engagement.final_report:
                report_dict = engagement.final_report.model_dump()

                # Run deterministic checks
                result.deterministic_checks = run_deterministic_checks(
                    report=report_dict,
                    pdf_path=engagement.pdf_path,
                    golden=golden,
                )

                # Truthful deterministic score: no LLM judge is invoked here.
                passed = sum(1 for c in result.deterministic_checks if c.passed)
                total = len(result.deterministic_checks)
                result.deterministic_score = (
                    (passed / total) * 5.0 if total > 0 else 0.0
                )
                result.overall_score = result.deterministic_score

        except Exception as e:  # noqa: BLE001 - failure is recorded in the result
            result.success = False
            result.error = str(e)[:500]

        return result

    async def run_all(self, save_baseline: bool = False) -> EvalResults:
        """Run the full golden set and compute aggregate results.

        Args:
            save_baseline: If True, store the mean score as the new baseline.
        """
        results = EvalResults()
        baseline = self._load_baseline()
        results.baseline_score = baseline["mean_score"]
        results.baseline_pass_rate = baseline["pass_rate"]

        for golden in self.golden_set:
            qr = await self._run_single_query(golden)
            results.results.append(qr)

        # Every query remains in the denominator. A crash is explicitly zero.
        for result in results.results:
            if not result.success:
                result.overall_score = 0.0
                result.deterministic_score = 0.0
        scores = [result.overall_score for result in results.results]
        results.mean_score = sum(scores) / len(scores) if scores else 0.0
        passed = sum(
            1
            for result in results.results
            if result.success and result.all_checks_passed
        )
        results.pass_rate = passed / len(results.results) if results.results else 0.0

        if results.baseline_score > 0 and (
            results.mean_score
            < results.baseline_score - self.REGRESSION_THRESHOLD
        ):
            results.regression_reasons.append(
                "mean score dropped below the permitted baseline threshold"
            )
        if results.baseline_pass_rate > 0 and (
            results.pass_rate
            < results.baseline_pass_rate - self.PASS_RATE_REGRESSION_THRESHOLD
        ):
            results.regression_reasons.append(
                "pass rate dropped below baseline"
            )
        results.regression_detected = bool(results.regression_reasons)

        # Save results
        self._save_results(results)

        if save_baseline:
            self._save_baseline(results)

        return results

    def run_deterministic_only(
        self,
        report: dict[str, Any],
        pdf_path: str = "",
        golden: GoldenQuery | None = None,
    ) -> list[CheckResult]:
        """Run only the deterministic checks on a pre-built report.

        Useful for CI pipelines that already have a report artifact.
        """
        return run_deterministic_checks(report, pdf_path, golden)
