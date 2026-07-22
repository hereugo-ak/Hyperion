"""Tests for the offline evaluation harness (P11)."""

from __future__ import annotations

import json
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from hyperion.eval.harness import (
    CheckResult,
    EvalHarness,
    EvalResults,
    GoldenQuery,
    GOLDEN_SET,
    QueryEvalResult,
    run_deterministic_checks,
)


# ─────────────────────────────────────────────────────────────────────────────
# Golden Set Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestGoldenSet:
    def test_golden_set_has_queries(self):
        assert len(GOLDEN_SET) >= 3, "Golden set should have at least 3 queries"

    def test_golden_set_covers_types(self):
        types = {gq.question_type for gq in GOLDEN_SET}
        assert "market_entry" in types
        assert "competitive_analysis" in types
        assert "risk_assessment" in types

    def test_each_query_has_minimums(self):
        for gq in GOLDEN_SET:
            assert gq.min_sections >= 1
            assert gq.min_sources >= 1
            assert gq.min_findings >= 1
            assert gq.question  # non-empty


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic Checks Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestDeterministicChecks:
    def _make_good_report(self) -> dict:
        return {
            "sections": [
                {"title": "Market Analysis", "body": "A" * 200, "charts": [{"type": "bar"}]},
                {"title": "Competitive Landscape", "body": "B" * 200, "charts": []},
                {"title": "Financial Assessment", "body": "C" * 200, "charts": []},
            ],
            "total_sources": 8,
            "key_findings": [
                {"title": "Finding 1", "sources": [{"url": "http://example.com"}]},
                {"title": "Finding 2", "sources": [{"url": "http://example.com"}]},
                {"title": "Finding 3", "sources": [{"url": "http://example.com"}]},
            ],
            "executive_summary": "X" * 300,
            "recommendation": "PROCEED",
        }

    def test_good_report_passes_all_checks(self):
        report = self._make_good_report()
        golden = GOLDEN_SET[0]
        checks = run_deterministic_checks(report, pdf_path="", golden=golden)
        failed = [c for c in checks if not c.passed]
        # PDF and charts checks may fail without a real PDF, but structural checks should pass
        structural_names = {"min_sections", "min_sources", "min_findings",
                           "no_empty_sections", "findings_have_sources",
                           "no_template_artifacts", "exec_summary_substantial",
                           "recommendation_set"}
        structural_failed = [c for c in failed if c.name in structural_names]
        assert len(structural_failed) == 0, \
            f"Structural checks failed: {[c.name for c in structural_failed]}"

    def test_empty_sections_detected(self):
        report = self._make_good_report()
        report["sections"][0]["body"] = ""  # Empty section
        golden = GOLDEN_SET[0]
        checks = run_deterministic_checks(report, pdf_path="", golden=golden)
        empty_check = next(c for c in checks if c.name == "no_empty_sections")
        assert not empty_check.passed

    def test_missing_sources_detected(self):
        report = self._make_good_report()
        report["key_findings"][0]["sources"] = []  # No sources
        golden = GOLDEN_SET[0]
        checks = run_deterministic_checks(report, pdf_path="", golden=golden)
        sources_check = next(c for c in checks if c.name == "findings_have_sources")
        assert not sources_check.passed

    def test_template_artifacts_detected(self):
        report = self._make_good_report()
        report["executive_summary"] = "This has {{unrendered}} template tags"
        golden = GOLDEN_SET[0]
        checks = run_deterministic_checks(report, pdf_path="", golden=golden)
        artifacts_check = next(c for c in checks if c.name == "no_template_artifacts")
        assert not artifacts_check.passed

    def test_short_exec_summary_detected(self):
        report = self._make_good_report()
        report["executive_summary"] = "Too short"
        golden = GOLDEN_SET[0]
        checks = run_deterministic_checks(report, pdf_path="", golden=golden)
        summary_check = next(c for c in checks if c.name == "exec_summary_substantial")
        assert not summary_check.passed

    def test_missing_recommendation_detected(self):
        report = self._make_good_report()
        report["recommendation"] = ""
        golden = GOLDEN_SET[0]
        checks = run_deterministic_checks(report, pdf_path="", golden=golden)
        rec_check = next(c for c in checks if c.name == "recommendation_set")
        assert not rec_check.passed


# ─────────────────────────────────────────────────────────────────────────────
# EvalHarness Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestEvalHarness:
    def test_load_baseline_missing_file(self, tmp_path):
        harness = EvalHarness(baseline_path=str(tmp_path / "nonexistent.json"))
        assert harness._load_baseline() == 0.0

    def test_save_and_load_baseline(self, tmp_path):
        baseline_path = str(tmp_path / "baseline.json")
        harness = EvalHarness(baseline_path=baseline_path)
        harness._save_baseline(4.2)
        assert harness._load_baseline() == 4.2

    def test_regression_detection(self, tmp_path):
        baseline_path = str(tmp_path / "baseline.json")
        harness = EvalHarness(baseline_path=baseline_path)
        harness._save_baseline(4.0)

        results = EvalResults()
        results.baseline_score = 4.0
        results.mean_score = 3.5  # Drop of 0.5 > threshold 0.3
        results.regression_detected = (
            results.mean_score < results.baseline_score - harness.REGRESSION_THRESHOLD
        )
        assert results.regression_detected

    def test_no_regression_when_score_stable(self, tmp_path):
        baseline_path = str(tmp_path / "baseline.json")
        harness = EvalHarness(baseline_path=baseline_path)
        harness._save_baseline(4.0)

        results = EvalResults()
        results.baseline_score = 4.0
        results.mean_score = 3.8  # Drop of 0.2 < threshold 0.3
        results.regression_detected = (
            results.mean_score < results.baseline_score - harness.REGRESSION_THRESHOLD
        )
        assert not results.regression_detected

    def test_results_serialization(self):
        results = EvalResults()
        results.mean_score = 4.5
        results.baseline_score = 4.0
        results.pass_rate = 0.8
        r = QueryEvalResult(query_id="test", question="test question")
        r.overall_score = 4.5
        r.success = True
        r.deterministic_checks = [CheckResult(name="test", passed=True, detail="ok")]
        results.results.append(r)

        d = results.to_dict()
        assert d["mean_score"] == 4.5
        assert d["pass_rate"] == 0.8
        assert len(d["results"]) == 1
        assert d["results"][0]["query_id"] == "test"
