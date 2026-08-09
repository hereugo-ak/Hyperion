"""Phase 0 (overhaul.md §6 P0): Evidence Ledger tests.

Covers the ledger contract — record/dedup/enrich, distinct_domains, by_stage,
by_engine, first_fetched_at, snapshot — plus the run-scoped context binding
and the SearXNG wiring that turns every retrieved URL into an Evidence record
before any LLM sees it.
"""

from __future__ import annotations

import asyncio
import contextvars
import json

import pytest

from hyperion.tools.evidence_ledger import (
    Evidence,
    EvidenceLedger,
    content_hash_of,
    domain_of,
    get_evidence_ledger,
    new_ledger,
    record_evidence,
    reset_active_ledger,
    set_active_ledger,
)


def test_domain_of_normalizes_hosts() -> None:
    assert domain_of("https://www.Example.com/path?q=1") == "example.com"
    assert domain_of("https://example.com") == "example.com"
    assert domain_of("http://sub.domain.org/x") == "sub.domain.org"
    assert domain_of("not a url") == ""
    assert domain_of("") == ""


def test_record_dedups_by_url_and_first_sighting_wins() -> None:
    ledger = EvidenceLedger(run_id="eng_1")
    first = ledger.record(
        url="https://example.com/a",
        title="First title",
        engine="mojeek",
        profile="web",
        stage="discovery",
    )
    second = ledger.record(
        url="https://example.com/a",
        title="Second title",
        engine="wikipedia",
        profile="reference",
        stage="discovery",
    )
    assert ledger.count() == 1
    assert first is not None and second is first
    assert ledger.all()[0].engine == "mojeek"
    assert ledger.all()[0].title == "First title"


def test_record_skips_empty_and_invalid_urls() -> None:
    ledger = EvidenceLedger(run_id="eng_1")
    assert ledger.record(url="") is None
    assert ledger.record(url="   ") is None
    assert ledger.record(url=123) is None  # type: ignore[arg-type]
    assert ledger.count() == 0


def test_second_record_enriches_content_hash_without_duplicating() -> None:
    """An extraction visit to an already-discovered URL attaches the hash."""
    ledger = EvidenceLedger(run_id="eng_1")
    ledger.record(url="https://example.com/a", engine="mojeek", stage="discovery")
    enriched = ledger.record(
        url="https://example.com/a",
        engine="unified_extract",
        stage="extraction",
        content_hash="deadbeef",
    )
    assert ledger.count() == 1
    assert enriched is not None
    assert enriched.content_hash == "deadbeef"
    # First-sighting metadata wins; the fingerprint is the only addition.
    assert enriched.engine == "mojeek"
    assert enriched.stage == "discovery"
    # A second fingerprint is a no-op once one exists.
    again = ledger.record(
        url="https://example.com/a", content_hash="cafebabe"
    )
    assert again is not None and again.content_hash == "deadbeef"


def test_distinct_domains_and_by_engine() -> None:
    ledger = EvidenceLedger(run_id="eng_1")
    ledger.record(url="https://a.com/1", engine="mojeek", profile="web")
    ledger.record(url="https://a.com/2", engine="mojeek", profile="web")
    ledger.record(url="https://www.b.org/3", engine="wikipedia", profile="reference")
    ledger.record(url="https://c.net/4", engine="jina", profile="s.jina.ai")
    assert ledger.distinct_domains() == {"a.com", "b.org", "c.net"}
    assert ledger.by_engine() == {"mojeek": 2, "wikipedia": 1, "jina": 1}


def test_by_stage_and_first_fetched_at() -> None:
    ledger = EvidenceLedger(run_id="eng_1")
    assert ledger.first_fetched_at() is None
    ledger.record(url="https://a.com/1", stage="discovery")
    ledger.record(url="https://a.com/2", stage="extraction")
    assert len(ledger.by_stage("discovery")) == 1
    assert len(ledger.by_stage("extraction")) == 1
    assert ledger.first_fetched_at() is not None


def test_summary_reports_kpi_shape() -> None:
    ledger = EvidenceLedger(run_id="eng_1")
    ledger.record(url="https://a.com/1", engine="mojeek", stage="discovery")
    ledger.record(url="https://b.com/2", engine="mojeek", stage="discovery")
    summary = ledger.summary()
    assert summary["run_id"] == "eng_1"
    assert summary["evidence_items"] == 2
    assert summary["distinct_domains"] == 2
    assert summary["by_engine"] == {"mojeek": 2}
    assert summary["domains"] == ["a.com", "b.com"]


def test_snapshot_writes_json(tmp_path) -> None:
    ledger = EvidenceLedger(run_id="eng_1")
    ledger.record(
        url="https://a.com/1",
        title="T",
        snippet="S",
        content_hash=content_hash_of("body text"),
        engine="mojeek",
        profile="web",
    )
    path = tmp_path / "nested" / "evidence_ledger.json"
    written = ledger.snapshot(path)
    assert written == str(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["run_id"] == "eng_1"
    assert data["summary"]["distinct_domains"] == 1
    assert data["records"][0]["domain"] == "a.com"


def test_run_scoped_context_binding() -> None:
    reset_active_ledger()
    try:
        assert get_evidence_ledger().run_id == "__default__"
        outer = contextvars.copy_context()

        def inside_engagement() -> None:
            new_ledger("eng_alpha")
            record_evidence(url="https://a.com/1", engine="mojeek")
            assert get_evidence_ledger().run_id == "eng_alpha"
            assert get_evidence_ledger().count() == 1

        outer.run(inside_engagement)
        # The binding must not leak into the caller's context.
        assert get_evidence_ledger().run_id == "__default__"
        assert get_evidence_ledger().count() == 0
    finally:
        reset_active_ledger()


def test_record_evidence_never_raises_without_an_active_run() -> None:
    reset_active_ledger()
    try:
        assert record_evidence(url="https://a.com/1") is not None
        assert get_evidence_ledger().run_id == "__default__"
    finally:
        reset_active_ledger()


# ───────────────────────────────────
# Wiring: SearXNG must turn every retrieved URL into an Evidence record.
# ───────────────────────────────────

from hyperion.tools.searxng import (  # noqa: E402
    EngineTokenBucket,
    SearxNGClient,
    SearxngEndpoint,
    SearxngPool,
)


@pytest.mark.asyncio
async def test_searxng_search_records_evidence_into_active_ledger(
    monkeypatch,
) -> None:
    """Every URL returned by the SearXNG JSON path lands in the ledger."""
    reset_active_ledger()
    try:
        new_ledger("eng_wiring")
        client = SearxNGClient()
        client._pool = SearxngPool([
            SearxngEndpoint(
                "http://web", "web", 8890, frozenset({"mojeek", "mwmbl"})
            ),
        ])

        class Health:
            def filter_available(self, engines):
                return list(engines)

            def record_response(self, unresponsive_engines, responding_engines):
                return None

            def record_degradation_if_needed(self, engines, *, floor=4):
                return None

        monkeypatch.setattr(
            "hyperion.tools.searxng.get_engine_health", lambda: Health()
        )
        monkeypatch.setattr(
            EngineTokenBucket, "acquire", staticmethod(lambda engines: asyncio.sleep(0))
        )

        class Response:
            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "results": [
                        {
                            "title": "Alpha report",
                            "url": "https://alpha.example/report",
                            "content": "Snippet one",
                            "engine": "mojeek",
                            "score": 1.0,
                        },
                        {
                            "title": "Beta paper",
                            "url": "https://beta.example/paper",
                            "content": "Snippet two",
                            "engine": "mwmbl",
                            "score": 1.0,
                        },
                    ],
                    "unresponsive_engines": [],
                }

        class Http:
            def __init__(self, base_url):
                self.base_url = base_url

            async def get(self, path, params=None):
                return Response()

        async def _get_client(base_url=None):
            return Http(base_url)

        monkeypatch.setattr(client, "_get_client", _get_client)

        response = await client._search_searxng_json(
            query="evidence wiring",
            num_results=5,
            categories="general",
            language="en",
            time_range="",
            engines="mojeek,mwmbl",
            safesearch=0,
        )
        assert response is not None and len(response.results) == 2

        ledger = get_evidence_ledger()
        assert ledger.count() == 2
        assert ledger.distinct_domains() == {"alpha.example", "beta.example"}
        by_engine = ledger.by_engine()
        assert by_engine.get("mojeek") == 1
        assert by_engine.get("mwmbl") == 1
        for record in ledger.all():
            assert record.stage == "discovery"
            assert record.profile == "web"
        await client.close()
    finally:
        reset_active_ledger()
