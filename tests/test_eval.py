"""Tests for the offline evaluation harness (P11)."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import fitz
import pytest

import hyperion.eval.harness as harness_module
from hyperion.eval.harness import (
    GOLDEN_SET,
    CheckResult,
    EvalHarness,
    EvalResults,
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
        assert "NATION_OR_REGION" in types

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
                           "exec_summary_substantial", "recommendation_set"}
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

    def test_pdf_integrity_uses_authoritative_scan(self, tmp_path, monkeypatch):
        report = self._make_good_report()
        pdf_path = tmp_path / "artifact.pdf"
        doc = fitz.open()
        doc.new_page().insert_text((72, 72), "Insufficient evidence")
        doc.save(pdf_path)
        doc.close()

        calls = {"extract": 0, "scan": 0, "audit": 0}
        real_extract = harness_module.extract_pdf_text
        real_scan = harness_module.scan_text_integrity

        def tracked_extract(path):
            calls["extract"] += 1
            return real_extract(path)

        def tracked_scan(text):
            calls["scan"] += 1
            return real_scan(text)

        def tracked_audit(path, *, fail_closed):
            calls["audit"] += 1
            assert fail_closed is False
            return SimpleNamespace(violations=[])

        monkeypatch.setattr(harness_module, "extract_pdf_text", tracked_extract)
        monkeypatch.setattr(harness_module, "scan_text_integrity", tracked_scan)
        monkeypatch.setattr(harness_module, "audit_pdf", tracked_audit)

        checks = run_deterministic_checks(
            report, pdf_path=str(pdf_path), golden=GOLDEN_SET[0]
        )
        artifacts_check = next(c for c in checks if c.name == "no_template_artifacts")
        assert not artifacts_check.passed
        assert calls == {"extract": 1, "scan": 1, "audit": 1}
        assert not hasattr(harness_module, "_TEMPLATE_ARTIFACTS")

    def test_charts_present_counts_pdf_images_not_report_specs(
        self, tmp_path, monkeypatch
    ):
        report = self._make_good_report()
        report["sections"][0]["charts"] = []
        pdf_path = tmp_path / "image.pdf"
        png = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR4nGNgYAAAAAMAASsJTYQAAAAASUVORK5CYII="
        )
        doc = fitz.open()
        page = doc.new_page()
        page.insert_image(fitz.Rect(10, 10, 30, 30), stream=png)
        doc.save(pdf_path)
        doc.close()
        monkeypatch.setattr(
            harness_module,
            "audit_pdf",
            lambda *args, **kwargs: SimpleNamespace(violations=[]),
        )

        checks = run_deterministic_checks(
            report, pdf_path=str(pdf_path), golden=GOLDEN_SET[0]
        )
        charts = next(c for c in checks if c.name == "charts_present")
        assert charts.passed
        assert "1 images embedded" in charts.detail

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
        assert harness._load_baseline() == {
            "mean_score": 0.0,
            "pass_rate": 0.0,
            "queries": {},
        }

    def test_save_and_load_attributable_baseline(self, tmp_path):
        baseline_path = str(tmp_path / "baseline.json")
        harness = EvalHarness(baseline_path=baseline_path)
        query = QueryEvalResult(
            query_id="gq_test",
            question="test",
            overall_score=4.2,
            success=True,
            deterministic_checks=[
                CheckResult(name="pdf_renders", passed=True, detail="ok")
            ],
        )
        results = EvalResults(
            results=[query],
            mean_score=4.2,
            pass_rate=1.0,
        )
        harness._save_baseline(results)

        loaded = harness._load_baseline()
        assert loaded["mean_score"] == 4.2
        assert loaded["pass_rate"] == 1.0
        assert loaded["queries"]["gq_test"]["overall_score"] == 4.2
        assert loaded["queries"]["gq_test"]["checks"] == {"pdf_renders": True}
        on_disk = json.loads((tmp_path / "baseline.json").read_text())
        assert on_disk["schema_version"] == 2

    @pytest.mark.asyncio
    async def test_crashed_query_counts_as_zero_in_mean(self, tmp_path, monkeypatch):
        harness = EvalHarness(baseline_path=str(tmp_path / "missing.json"))
        harness.golden_set = GOLDEN_SET[:2]
        outcomes = iter(
            [
                QueryEvalResult(
                    query_id="ok",
                    question="ok",
                    overall_score=4.0,
                    deterministic_score=4.0,
                    success=True,
                    deterministic_checks=[CheckResult("gate", True)],
                ),
                QueryEvalResult(
                    query_id="crash",
                    question="crash",
                    overall_score=5.0,
                    deterministic_score=5.0,
                    success=False,
                    error="boom",
                ),
            ]
        )

        async def fake_run(_golden):
            return next(outcomes)

        monkeypatch.setattr(harness, "_run_single_query", fake_run)
        monkeypatch.setattr(harness, "_save_results", lambda _results: "unused")
        results = await harness.run_all()
        assert results.results[1].overall_score == 0.0
        assert results.mean_score == 2.0

    @pytest.mark.asyncio
    async def test_pass_rate_drop_is_a_regression(self, tmp_path, monkeypatch):
        baseline_path = tmp_path / "baseline.json"
        baseline_path.write_text(
            json.dumps({"mean_score": 4.0, "pass_rate": 1.0, "queries": {}})
        )
        harness = EvalHarness(baseline_path=str(baseline_path))
        harness.golden_set = GOLDEN_SET[:1]

        async def fake_run(_golden):
            return QueryEvalResult(
                query_id="failed-check",
                question="test",
                overall_score=4.0,
                deterministic_score=4.0,
                success=True,
                deterministic_checks=[CheckResult("gate", False)],
            )

        monkeypatch.setattr(harness, "_run_single_query", fake_run)
        monkeypatch.setattr(harness, "_save_results", lambda _results: "unused")
        results = await harness.run_all()
        assert results.mean_score == 4.0
        assert results.pass_rate == 0.0
        assert results.regression_detected
        assert "pass rate dropped below baseline" in results.regression_reasons

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
