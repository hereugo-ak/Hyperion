"""P2-25 / P2-26 / P2-27 / P2-28: search corpus collapse fixes.

P2-26 (P2-G23/24/25): the general-web corpus was a two-engine duopoly
(bing, duckduckgo), and when DuckDuckGo ate a 24h CAPTCHA ban, one engine
carried the engagement. The fix widens the pool to 6+ engines, rotates to
standby engines on a zero-result response (one retry before falling
through), and makes a corpus with < 8 distinct domains an
integrity_blocker.

P2-27 (P2-G26): off-topic reference sites (dictionaries, consumer-health)
were not filtered and were mislabelled as credible. The fix denies
reference-work domains, drops definitional results for business queries,
and implements a real classify_source_type(url) that never defaults to a
credible label (no source labelled government without a .gov-class host).

P2-28 (P2-G27): query planning could not detect a no-corpus subject.
Subject recall below 0.15 after round 1 switches strategy; still below
0.15 escalates no_corpus.

P2-25: the content-aware stop in the quality loop fired before any fix
attempt. Thin evidence must trigger retrieval escalation, not a stop.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from hyperion.tools.searxng import SearxNGClient

# ─────────────────────────────────────────────────────────────────────────────
# P2-26 fix 1: the engine pool is 6+ general-web engines
# ─────────────────────────────────────────────────────────────────────────────


class TestEnginePool:
    def test_reliable_engines_has_at_least_six(self):
        engines = [e.strip() for e in SearxNGClient.RELIABLE_ENGINES.split(",") if e.strip()]
        assert len(engines) >= 6, (
            f"P2-G23: >= 6 general-web engines required, got {engines}"
        )

    def test_standby_pool_exists_and_is_disjoint(self):
        standby = [
            e.strip()
            for e in getattr(SearxNGClient, "STANDBY_ENGINES", "").split(",")
            if e.strip()
        ]
        primary = {e.strip() for e in SearxNGClient.RELIABLE_ENGINES.split(",") if e.strip()}
        assert standby, "a standby engine pool must exist for rotation"
        assert not (set(standby) & primary), (
            "standby engines must be disjoint from the primary pool, "
            "otherwise rotation adds nothing"
        )


# ─────────────────────────────────────────────────────────────────────────────
# P2-26 fix 3: zero-result response rotates engines and retries once
# ─────────────────────────────────────────────────────────────────────────────


class TestEngineRotationOnZero:
    def _client(self) -> SearxNGClient:
        client = SearxNGClient.__new__(SearxNGClient)
        client.settings = None
        return client

    def test_zero_results_rotates_to_standby_once(self, monkeypatch):
        """P2-G24: a zero-result response drops cooled/unresponsive engines,
        adds standby engines, and retries ONCE before falling through."""
        import hyperion.tools.searxng as sx

        monkeypatch.setattr(sx, "get_engine_health", lambda: SimpleNamespace(
            record_response=lambda **kw: None,
            is_available=lambda e: True,
        ))

        calls: list[str] = []

        async def fake_json(self, **kwargs):
            calls.append(kwargs.get("engines", ""))
            if len(calls) == 1:
                return None  # zero results on primary pool
            from hyperion.tools.searxng import SearchResponse, SearchResult
            return SearchResponse(
                query="q",
                results=[SearchResult(
                    title="t", url="https://example.com", snippet="s",
                    engine="mojeek", score=1.0, category="general",
                )],
                total=1, took_ms=10, engines_used=["mojeek"],
            )

        monkeypatch.setattr(SearxNGClient, "_search_searxng_json", fake_json)

        client = self._client()
        resp = asyncio.run(client._search_with_rotation(
            query="lithium market size",
            num_results=5,
            categories="general",
            language="en",
            time_range="",
            engines="bing,duckduckgo",
            safesearch=1,
        ))
        assert resp is not None and resp.total == 1
        assert len(calls) == 2, "exactly one rotation retry"
        rotated = calls[1]
        assert any(
            e.strip() in rotated
            for e in SearxNGClient.STANDBY_ENGINES.split(",")
        ), "the retry must include standby engines"

    def test_rotation_exhausts_then_falls_through(self, monkeypatch):
        """When the standby retry also returns zero, fall through (None)."""
        import hyperion.tools.searxng as sx

        monkeypatch.setattr(sx, "get_engine_health", lambda: SimpleNamespace(
            record_response=lambda **kw: None,
            is_available=lambda e: True,
        ))

        async def fake_json(self, **kwargs):
            return None

        monkeypatch.setattr(SearxNGClient, "_search_searxng_json", fake_json)

        client = self._client()
        resp = asyncio.run(client._search_with_rotation(
            query="nothing anywhere",
            num_results=5,
            categories="general",
            language="en",
            time_range="",
            engines="bing,duckduckgo",
            safesearch=1,
        ))
        assert resp is None


# ─────────────────────────────────────────────────────────────────────────────
# P2-26 fix 5: corpus floor as integrity blocker
# ─────────────────────────────────────────────────────────────────────────────


class TestCorpusFloorIntegrityBlocker:
    def test_under_8_distinct_domains_is_integrity_blocker(self):
        """P2-G25: an engagement finishing with < 8 distinct source domains
        is an integrity_blocker, not a footnote."""
        from hyperion.agents.support.quality_gate import QualityGate

        gate = QualityGate.__new__(QualityGate)
        urls = [f"https://site{i}.example.com/page" for i in range(4)]
        blockers = gate._corpus_floor_blocker(urls)
        assert blockers, "4 distinct domains must trigger a blocker"
        assert any("domain" in b.lower() for b in blockers)

    def test_8_or_more_domains_no_blocker(self):
        from hyperion.agents.support.quality_gate import QualityGate

        gate = QualityGate.__new__(QualityGate)
        urls = [f"https://site{i}.example.com/page" for i in range(9)]
        assert gate._corpus_floor_blocker(urls) == []


# ─────────────────────────────────────────────────────────────────────────────
# P2-27: reference-work denial, definitional detector, classify_source_type
# ─────────────────────────────────────────────────────────────────────────────


class TestReferenceWorkDenial:
    def test_dictionary_domains_denied(self):
        from hyperion.tools.evidence_scorer import EvidenceScorer

        scorer = EvidenceScorer()
        for domain in (
            "https://www.merriam-webster.com/dictionary/emerging",
            "https://dictionary.cambridge.org/dictionary/english/mobility",
            "https://www.dictionary.com/browse/emerging",
            "https://www.iciba.com/word?w=emerging",
            "https://en.wiktionary.org/wiki/emerging",
            "https://health.harvard.edu/topics/mobility",
        ):
            assert scorer._is_denied_domain(domain), f"{domain} must be denied"

    def test_definitional_title_dropped(self):
        """A result whose title is definitional is dropped for a business
        query regardless of domain."""
        from hyperion.tools.evidence_scorer import EvidenceScorer

        scorer = EvidenceScorer()
        assert scorer._is_definitional_result(
            "Emerging - Definition, Meaning & Synonyms", "https://example.com/x"
        )
        assert scorer._is_definitional_result(
            "Mobility pronunciation guide", "https://example.com/y"
        )
        assert scorer._is_definitional_result(
            "Market analysis", "https://example.com/dictionary/mobility"
        )
        assert not scorer._is_definitional_result(
            "Nigeria lithium market outlook 2026", "https://example.com/z"
        )


class TestClassifySourceType:
    def test_classifier_exists_and_never_defaults_credible(self):
        from hyperion.schemas.models import SourceType
        from hyperion.tools.source_classifier import classify_source_type

        # Unclassifiable host -> UNKNOWN, never a credible default.
        assert (
            classify_source_type("https://random-blog-xyz.example.com/post")
            == SourceType.UNKNOWN
        )

    def test_government_requires_gov_host(self):
        from hyperion.schemas.models import SourceType
        from hyperion.tools.source_classifier import classify_source_type

        assert classify_source_type("https://www.energy.gov/article") == SourceType.GOVERNMENT
        assert classify_source_type("https://www.gov.uk/guidance") == SourceType.GOVERNMENT
        # A health/edu host is NOT government.
        assert classify_source_type("https://health.harvard.edu/x") != SourceType.GOVERNMENT

    def test_reference_works_classified_as_reference(self):
        from hyperion.schemas.models import SourceType
        from hyperion.tools.source_classifier import classify_source_type

        assert classify_source_type(
            "https://www.merriam-webster.com/dictionary/x"
        ) == SourceType.REFERENCE

    def test_news_host_classified_news(self):
        from hyperion.schemas.models import SourceType
        from hyperion.tools.source_classifier import classify_source_type

        assert classify_source_type("https://www.reuters.com/markets/x") == SourceType.NEWS


# ─────────────────────────────────────────────────────────────────────────────
# P2-28: subject recall -> strategy switch -> no_corpus
# ─────────────────────────────────────────────────────────────────────────────


class TestSubjectRecall:
    def test_subject_recall_computation(self):
        from hyperion.tools.query_planner import subject_recall

        results = [
            {"title": "Acme Lithium expands in Nigeria", "snippet": "Acme Lithium said..."},
            {"title": "Dictionary: emerging", "snippet": "the meaning of emerging"},
            {"title": "Mobility definition", "snippet": "what mobility means"},
        ]
        recall = subject_recall("Acme Lithium", results)
        assert 0.0 < recall < 1.0
        assert abs(recall - (1 / 3)) < 1e-6

    def test_low_recall_triggers_strategy_switch(self):
        """Below 0.15 after round 1: alternative queries target the entity's
        own domain, registries, and news archives."""
        from hyperion.tools.query_planner import no_corpus_fallback_queries

        queries = no_corpus_fallback_queries("Acme Lithium Ltd", geography="Nigeria")
        assert queries, "fallback must produce reformulated queries"
        joined = " ".join(queries).lower()
        assert "acme lithium" in joined
        assert any(
            any(tok in q for tok in ("site:", "registry", "corporate affairs", "news"))
            for q in (q.lower() for q in queries)
        )

    def test_still_low_recall_escalates_no_corpus(self):
        from hyperion.tools.query_planner import NoCorpusError, assess_subject_recall

        try:
            assess_subject_recall("Ghost Entity XYZ", round1=0.05, round2=0.10)
            raised = False
        except NoCorpusError as exc:
            raised = True
            assert "no_corpus" in str(exc)
        assert raised, "recall < 0.15 after both rounds must raise NoCorpusError"

    def test_recovered_recall_no_escalation(self):
        from hyperion.tools.query_planner import assess_subject_recall

        # Round 2 recovered above threshold: no escalation.
        assess_subject_recall("Acme Lithium", round1=0.05, round2=0.40)


# ─────────────────────────────────────────────────────────────────────────────
# P2-25: thin evidence triggers retrieval escalation, not a stop
# ─────────────────────────────────────────────────────────────────────────────


class TestRetrievalEscalation:
    def test_thin_evidence_dispatches_retrieval_before_stopping(self):
        """When total_sources is below the floor, the orchestrator escalates
        retrieval (targeted search round) instead of breaking immediately."""
        from hyperion.orchestrator import WorkflowEngine

        orch = WorkflowEngine.__new__(WorkflowEngine)
        called: list[dict] = []

        async def fake_escalate(report, needed):
            called.append({"needed": needed})
            return 14  # escalation recovered enough sources

        orch._escalate_retrieval = fake_escalate
        report = SimpleNamespace(total_sources=2)
        proceed = asyncio.run(orch._handle_thin_evidence(report, source_floor=3))
        assert called, "retrieval escalation must be attempted before any stop"
        assert proceed is True

    def test_failed_escalation_marks_limitation_not_silent(self):
        from hyperion.orchestrator import WorkflowEngine

        orch = WorkflowEngine.__new__(WorkflowEngine)

        async def fake_escalate(report, needed):
            return 0  # nothing found

        orch._escalate_retrieval = fake_escalate
        report = SimpleNamespace(total_sources=2, limitations=[])
        proceed = asyncio.run(orch._handle_thin_evidence(report, source_floor=3))
        assert proceed is False
        assert report.limitations, (
            "a failed escalation must leave a stated evidence limitation"
        )

    def test_escalation_persists_recovered_urls_on_report(self, monkeypatch):
        """Recovered counts must correspond to real report provenance."""
        from hyperion.orchestrator import WorkflowEngine
        from hyperion.tools.searxng import SearxNGClient

        monkeypatch.setattr(
            "hyperion.tools.query_utils.get_engagement_focus",
            lambda: ("question", "Acme", "Singapore"),
        )

        async def fake_search(self, query, num_results=5, **kwargs):
            slug = query.lower().replace(" ", "-")
            result = SimpleNamespace(
                url=f"https://example.com/{slug}",
                title=f"Evidence for {query}",
                snippet="Retrieved evidence",
                published_date="2026-08-01",
            )
            return SimpleNamespace(results=[result])

        monkeypatch.setattr(SearxNGClient, "search", fake_search)
        section = SimpleNamespace(sources=[])
        report = SimpleNamespace(
            sections=[section],
            key_findings=[],
            total_sources=0,
        )
        orch = WorkflowEngine.__new__(WorkflowEngine)

        recovered = asyncio.run(orch._escalate_retrieval(report, needed=8))

        assert recovered == 3
        assert report.total_sources == 3
        assert len(section.sources) == 3
        assert {source.url for source in section.sources} == {
            "https://example.com/acme-singapore-market-analysis",
            "https://example.com/acme-singapore-industry-report-2025",
            "https://example.com/acme-singapore-news",
        }
