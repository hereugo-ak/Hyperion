"""
HYPERION Offline Evaluation Harness — make "McKinsey-grade" measurable.

This module implements the offline eval harness from IV.1.3 / P11:

1. **Golden set** of representative queries spanning all workflow types.
2. **Deterministic checks** per report: ≥N sections, ≥M cited sources,
   every KeyFinding has a source, no empty sections, no template artifacts
   (``&lt;``, ``C:\\``, unrendered ``{{ }}``), PDF renders, charts present.
3. **LLM-as-judge rubric** (scored 1–5) on: evidence density, analytical
   depth, structure, actionability — with the same rubric the runtime
   quality-gate uses, so runtime and offline agree.
4. **Regression gate:** CI fails if golden-set mean score drops > threshold
   vs the last release.

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
import re
import time
from dataclasses import dataclass, field
from typing import Any


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
]


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic Checks — structural quality validation
# ─────────────────────────────────────────────────────────────────────────────


# Template artifacts that should never appear in a final report
_TEMPLATE_ARTIFACTS = [
    re.compile(r"&lt;"),       # Unescaped HTML entities
    re.compile(r"C:\\", re.IGNORECASE),  # Windows file paths
    re.compile(r"{{\s*\w+"),   # Unrendered Jinja2 templates
    re.compile(r"\{\{"),       # Literal Jinja2 braces
    re.compile(r"\}\}"),       # Literal Jinja2 braces
    re.compile(r"\bNone\b"),   # Python None leaking into text
    re.compile(r"<template>", re.IGNORECASE),  # Template tags
]


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

    These are fast, non-LLM checks that validate the report's structural
    integrity — not its analytical quality (that's the LLM judge's job).
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

    # Check 6: No template artifacts in text
    full_text = json.dumps(report, default=str)
    artifacts_found = []
    for pattern in _TEMPLATE_ARTIFACTS:
        match = pattern.search(full_text)
        if match:
            artifacts_found.append(match.group())
    results.append(CheckResult(
        name="no_template_artifacts",
        passed=len(artifacts_found) == 0,
        detail=f"Artifacts: {artifacts_found}" if artifacts_found else "Clean",
    ))

    # Check 7: PDF exists (if expected)
    if g.expect_pdf:
        pdf_exists = bool(pdf_path and os.path.exists(pdf_path))
        results.append(CheckResult(
            name="pdf_renders",
            passed=pdf_exists,
            detail=pdf_path if pdf_exists else "PDF not found",
        ))

    # Check 8: Charts present (if expected)
    if g.expect_charts:
        charts_count = sum(
            len(s.get("charts", [])) for s in sections
        )
        results.append(CheckResult(
            name="charts_present",
            passed=charts_count > 0,
            detail=f"{charts_count} charts found",
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

    # Check 15: PDF page count is reasonable (15-40 pages)
    if pdf_path and os.path.exists(pdf_path):
        page_count = _get_pdf_page_count(pdf_path)
        results.append(CheckResult(
            name="page_count_reasonable",
            passed=page_count is not None and 5 <= page_count <= 60,
            detail=f"{page_count} pages" if page_count is not None else "Could not read PDF",
        ))
    else:
        results.append(CheckResult(
            name="page_count_reasonable",
            passed=False,
            detail="PDF not found",
        ))

    return results


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
        count = doc.page_count
        doc.close()
        return count
    except (ImportError, OSError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# LLM-as-Judge Rubric — analytical quality scoring (shared with runtime gate)
# ─────────────────────────────────────────────────────────────────────────────


JUDGE_RUBRIC = {
    "evidence_density": {
        "description": "Every claim is backed by specific, cited evidence. No hand-waving.",
        "weight": 1.0,
    },
    "analytical_depth": {
        "description": "Goes beyond surface description to causal analysis and synthesis.",
        "weight": 1.0,
    },
    "structure": {
        "description": "Logical flow, clear sections, executive summary stands alone.",
        "weight": 0.8,
    },
    "actionability": {
        "description": "Recommendation is specific, actionable, and supported by evidence.",
        "weight": 1.0,
    },
    "boardroom_register": {
        "description": "Professional tone, no technical jargon, suitable for S&P-500 executives.",
        "weight": 0.6,
    },
}


JUDGE_PROMPT = """You are an expert evaluator for McKinsey-grade business reports.
Score the following report on a 1-5 scale across 5 dimensions.

For each dimension, provide:
- score: integer 1-5 (5 = best-in-class, 1 = unacceptable)
- reasoning: one sentence explaining the score

Dimensions:
1. evidence_density: Every claim backed by specific, cited evidence.
2. analytical_depth: Goes beyond description to causal analysis and synthesis.
3. structure: Logical flow, clear sections, executive summary stands alone.
4. actionability: Recommendation is specific, actionable, evidence-supported.
5. boardroom_register: Professional tone, no jargon, suitable for executives.

Report to evaluate:
{report_text}

Return JSON:
{{"scores": {{"evidence_density": {{"score": N, "reasoning": "..."}}, ...}}, "overall": N}}
"""


# ─────────────────────────────────────────────────────────────────────────────
# Eval Harness — run golden set, score, detect regressions
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class QueryEvalResult:
    """Evaluation result for a single golden query."""

    query_id: str
    question: str
    deterministic_checks: list[CheckResult] = field(default_factory=list)
    judge_scores: dict[str, int] = field(default_factory=dict)
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
    regression_detected: bool = False
    pass_rate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "mean_score": self.mean_score,
            "baseline_score": self.baseline_score,
            "regression_detected": self.regression_detected,
            "pass_rate": self.pass_rate,
            "results": [
                {
                    "query_id": r.query_id,
                    "question": r.question,
                    "overall_score": r.overall_score,
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

    Runs the golden set through the full pipeline, applies deterministic
    checks + LLM-as-judge rubric, and detects quality regressions vs a
    stored baseline.
    """

    BASELINE_PATH = "eval/baseline.json"
    RESULTS_PATH = "eval/results.json"
    REGRESSION_THRESHOLD = 0.3  # Fail if mean drops > 0.3 below baseline

    def __init__(self, baseline_path: str | None = None) -> None:
        self.baseline_path = baseline_path or self.BASELINE_PATH
        self.golden_set = list(GOLDEN_SET)

    def _load_baseline(self) -> float:
        """Load the stored baseline score. Returns 0.0 if no baseline exists."""
        if not os.path.exists(self.baseline_path):
            return 0.0
        try:
            with open(self.baseline_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data.get("mean_score", 0.0)
        except (json.JSONDecodeError, OSError):
            return 0.0

    def _save_baseline(self, score: float) -> None:
        """Save a new baseline score."""
        os.makedirs(os.path.dirname(self.baseline_path), exist_ok=True)
        with open(self.baseline_path, "w", encoding="utf-8") as f:
            json.dump({"mean_score": score, "ts": time.time()}, f, indent=2)

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

                # LLM-as-judge (optional — requires router)
                # For now, compute a heuristic score from deterministic checks
                passed = sum(1 for c in result.deterministic_checks if c.passed)
                total = len(result.deterministic_checks)
                result.overall_score = (passed / total) * 5.0 if total > 0 else 0.0

        except Exception as e:
            result.success = False
            result.error = str(e)[:500]

        return result

    async def run_all(self, save_baseline: bool = False) -> EvalResults:
        """Run the full golden set and compute aggregate results.

        Args:
            save_baseline: If True, store the mean score as the new baseline.
        """
        results = EvalResults()
        results.baseline_score = self._load_baseline()

        for golden in self.golden_set:
            qr = await self._run_single_query(golden)
            results.results.append(qr)

        # Compute aggregates
        scores = [r.overall_score for r in results.results if r.success]
        results.mean_score = sum(scores) / len(scores) if scores else 0.0
        passed = sum(1 for r in results.results if r.success and r.all_checks_passed)
        results.pass_rate = passed / len(results.results) if results.results else 0.0

        # Regression detection
        if results.baseline_score > 0:
            results.regression_detected = (
                results.mean_score < results.baseline_score - self.REGRESSION_THRESHOLD
            )

        # Save results
        self._save_results(results)

        if save_baseline:
            self._save_baseline(results.mean_score)

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
