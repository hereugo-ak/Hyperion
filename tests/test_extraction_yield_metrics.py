"""Tests for fix 2.6 — per-engagement extraction-yield metrics.

Audit: HYPERION_DEEP_AUDIT_2026-07-27.md §6 Phase 2 item 2.6:
"Log an extraction-yield metric per engagement (``urls_discovered``,
``urls_extracted``, ``chars_retained``, ``sources_cited``) and surface it in
the run report."

Exit criterion these metrics make measurable (§6 Phase 2):
"extraction success >=60% of discovered URLs; every cited source has >=500
chars of retained, reranked content."
"""

from __future__ import annotations

import pytest

from hyperion.tools.deep_search import (
    DeepSearchResult,
    YieldMetrics,
    engagement_yield_report,
    reset_engagement_yield,
)


class TestYieldMetricsShape:
    def test_the_audits_four_named_fields_exist(self):
        ym = YieldMetrics(urls_discovered=10, urls_extracted=7,
                          chars_retained=9000, sources_cited=6)
        d = ym.to_dict()
        for key in ("urls_discovered", "urls_extracted", "chars_retained", "sources_cited"):
            assert key in d
        assert d["urls_discovered"] == 10
        assert d["urls_extracted"] == 7
        assert d["chars_retained"] == 9000
        assert d["sources_cited"] == 6

    def test_extraction_yield_ratio(self):
        ym = YieldMetrics(urls_discovered=10, urls_extracted=6)
        assert ym.extraction_yield == pytest.approx(0.6)

    def test_yield_ratio_zero_discovered_is_zero_not_crash(self):
        assert YieldMetrics().extraction_yield == 0.0
        assert YieldMetrics().avg_chars_per_source == 0.0

    def test_avg_chars_per_source_supports_the_500_char_criterion(self):
        ym = YieldMetrics(chars_retained=3000, sources_cited=6)
        assert ym.avg_chars_per_source == pytest.approx(500.0)


class TestResultCarriesMetrics:
    def test_deep_search_result_has_yield_metrics_field(self):
        r = DeepSearchResult(query="q")
        assert isinstance(r.yield_metrics, YieldMetrics)

    def test_yield_metrics_in_to_dict(self):
        r = DeepSearchResult(
            query="q",
            yield_metrics=YieldMetrics(urls_discovered=4, urls_extracted=3,
                                       chars_retained=1500, sources_cited=3),
        )
        assert r.to_dict()["yield_metrics"]["extraction_yield"] == pytest.approx(0.75)

    def test_yield_metrics_in_markdown(self):
        r = DeepSearchResult(
            query="q",
            yield_metrics=YieldMetrics(urls_discovered=4, urls_extracted=3,
                                       chars_retained=1500, sources_cited=3),
        )
        md = r.to_markdown()
        assert "Yield" in md
        assert "75%" in md


class TestEngagementAccumulator:
    def setup_method(self):
        reset_engagement_yield()

    def teardown_method(self):
        reset_engagement_yield()

    def test_report_starts_empty(self):
        report = engagement_yield_report()
        assert report["urls_discovered"] == 0
        assert report["search_calls"] == 0

    def test_aggregates_across_calls(self):
        from hyperion.tools.deep_search import _engagement_yield
        _engagement_yield.record(YieldMetrics(urls_discovered=10, urls_extracted=6,
                                              chars_retained=3000, sources_cited=6))
        _engagement_yield.record(YieldMetrics(urls_discovered=5, urls_extracted=4,
                                              chars_retained=2500, sources_cited=4))
        report = engagement_yield_report()
        assert report["urls_discovered"] == 15
        assert report["urls_extracted"] == 10
        assert report["chars_retained"] == 5500
        assert report["sources_cited"] == 10
        assert report["search_calls"] == 2
        assert report["extraction_yield"] == pytest.approx(10 / 15, abs=1e-3)

    def test_reset_clears(self):
        from hyperion.tools.deep_search import _engagement_yield
        _engagement_yield.record(YieldMetrics(urls_discovered=10, urls_extracted=6))
        reset_engagement_yield()
        assert engagement_yield_report()["urls_discovered"] == 0

    def test_thread_safe_concurrent_recording(self):
        import threading

        from hyperion.tools.deep_search import _engagement_yield

        def record_many():
            for _ in range(100):
                _engagement_yield.record(YieldMetrics(urls_discovered=1, urls_extracted=1))

        threads = [threading.Thread(target=record_many) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        report = engagement_yield_report()
        assert report["urls_discovered"] == 400
        assert report["search_calls"] == 400

    def test_exported_from_tools_package(self):
        import hyperion.tools as tools
        assert "YieldMetrics" in tools.__all__
        assert "engagement_yield_report" in tools.__all__
        assert "reset_engagement_yield" in tools.__all__
        assert callable(tools.engagement_yield_report)


class TestSearchWiresMetrics:
    @pytest.mark.asyncio
    async def test_search_populates_yield_metrics(self):
        """A deep_search.search() call must populate all four metrics from
        real discovery/extraction outcomes (mocked at the client boundary)."""
        from unittest.mock import AsyncMock, patch

        from hyperion.tools.deep_search import DeepSearchClient, ExtractedContent

        # NOTE: the mock content must be genuinely relevant to the query —
        # search() runs the REAL EvidenceScorer over extracted content, and
        # fix 2.4's token-boundary scoring correctly drops contentless filler
        # below MIN_RELEVANCE. 700 chars per source: exactly 3 sources x 700.
        relevant = (
            "Nigeria lithium battery market size reached $450 million. "
            "The Nigeria lithium battery market is growing. "
        )
        body = (relevant * (700 // len(relevant) + 1))[:700]
        client = DeepSearchClient()
        extracted = [
            ExtractedContent(url=f"https://example.com/{i}", title=f"t{i}",
                             content=body, tool_used="jina")
            for i in range(3)
        ]
        with patch.object(client, "_discover", new=AsyncMock(
            return_value=([f"https://example.com/{i}" for i in range(5)], ["searxng"], {})
        )), patch.object(client, "_extract_batch", new=AsyncMock(
            return_value=(extracted, ["jina"], ["jina"], {})
        )):
            result = await client.search("Nigeria lithium battery market size", depth="quick")

        ym = result.yield_metrics
        assert ym.urls_discovered == 5
        assert ym.urls_extracted == 3
        assert ym.sources_cited == 3
        assert ym.chars_retained == 2100  # 3 cited sources x 700 chars

    @pytest.mark.asyncio
    async def test_search_records_into_engagement_accumulator(self):
        from unittest.mock import AsyncMock, patch

        from hyperion.tools.deep_search import DeepSearchClient, ExtractedContent

        reset_engagement_yield()
        client = DeepSearchClient()
        relevant = (
            "offshore wind cost decline in Europe: turbine prices fell. "
            "Wind costs declined sharply across Europe. "
        )
        body = (relevant * (600 // len(relevant) + 1))[:600]
        extracted = [ExtractedContent(url="https://example.com/0", title="t",
                                      content=body, tool_used="jina")]
        with patch.object(client, "_discover", new=AsyncMock(
            return_value=(["https://example.com/0"], ["searxng"], {})
        )), patch.object(client, "_extract_batch", new=AsyncMock(
            return_value=(extracted, ["jina"], ["jina"], {})
        )):
            await client.search("offshore wind cost decline Europe", depth="quick")

        report = engagement_yield_report()
        assert report["search_calls"] == 1
        assert report["urls_discovered"] == 1
        assert report["urls_extracted"] == 1
        reset_engagement_yield()


class TestOrchestratorSurfaces:
    def test_engagement_result_has_extraction_yield_field(self):
        from hyperion.orchestrator import EngagementResult
        r = EngagementResult(engagement_id="e", question="q")
        assert r.extraction_yield == {}
        assert "extraction_yield" in r.to_dict()
