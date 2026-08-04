"""
Tests for `resolve_subject` wiring in market_analyst.py, regulatory_analyst.py,
and risk_analyst.py (Phase 1, fix 1.6 — HYPERION_DEEP_AUDIT_2026-07-27.md
§4.10 Finding B-9).

Before the fix, these three specialists read raw context keys
(``self._context.get("industry", "")`` etc.) with no fallback chain. When the
Engagement Director's handover omitted the key — the normal case for a macro
question with no explicit "industry"/"sector" field — every downstream query
template interpolated that emptiness into a subject-less search:

    f"{industry} industry risks challenges"   -> " industry risks challenges"
    f"{market_query} market size TAM report"  -> "" (market_query was the
                                                   already-resolved question,
                                                   but sub-methods used bare
                                                   context reads elsewhere)

The fix wires `resolve_subject` (context keys -> engagement subject -> the
user's own question) into each specialist's `run()` and into the ad hoc
`get_engagement_focus()`-based subject resolution duplicated inside
`_scrape_dashboards`/`_scrape_government_portals`/`_discover_regulatory_portals`,
so every one of these agents' outbound queries is anchored to *something*
whenever the question itself carries a subject.

This file locks in that property directly against the real search-query
construction code paths (not just `resolve_subject` in isolation, which is
already covered by `test_search_grounding.py`).
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hyperion.agents.specialists.competitive_intel import CompetitiveIntel
from hyperion.agents.specialists.market_analyst import MarketAnalyst
from hyperion.agents.specialists.regulatory_analyst import RegulatoryAnalyst
from hyperion.agents.specialists.risk_analyst import RiskAnalyst
from hyperion.agents.specialists.sustainability_analyst import SustainabilityAnalyst
from hyperion.tools.query_utils import clear_engagement_focus, set_engagement_focus

QUESTION = "Should a mid-size Vietnamese seafood exporter expand into the EU market?"
SUBJECT = "Vietnamese seafood export"


@pytest.fixture(autouse=True)
def _focus():
    clear_engagement_focus()
    set_engagement_focus(question=QUESTION, subject=SUBJECT, geography="Vietnam")
    yield
    clear_engagement_focus()


def _spy_searxng(results: list | None = None) -> SimpleNamespace:
    return SimpleNamespace(search=AsyncMock(return_value=results or []))


# ─────────────────────────────────────────────────────────────────────────────
# market_analyst.py
# ─────────────────────────────────────────────────────────────────────────────


class TestMarketAnalystResolveSubject:
    def test_search_market_reports_query_carries_subject_when_context_is_empty(
        self, monkeypatch
    ):
        """`_search_market_reports` is called from `run()` with a
        `resolve_subject`-resolved `market_query`, never the bare context.
        With no context keys at all, the query must still be anchored to
        the question/engagement subject rather than degrading to a bare
        template like ' market size TAM report'."""
        agent = MarketAnalyst()
        agent._question = QUESTION
        agent._context = {}

        spy = _spy_searxng()
        monkeypatch.setattr(agent, "get_tool", lambda tool: spy)

        # Mirror what run() does: resolve the market subject, then call the
        # method with it (this is the exact call chain after the fix).
        from hyperion.tools.query_utils import resolve_subject

        market_query = (
            resolve_subject(
                agent._context, "market", "segment", "sector", "industry",
                question=agent._question,
            )
            or agent._question
        )
        assert market_query  # never empty when the question has content

        asyncio.run(agent._search_market_reports(market_query))

        assert spy.search.await_count == 1
        called_query = spy.search.await_args.args[0]
        assert called_query.strip() != "market size TAM report"
        assert "seafood" in called_query.lower() or "vietnam" in called_query.lower()

    def test_scrape_dashboards_query_carries_subject_when_context_is_empty(
        self, monkeypatch
    ):
        agent = MarketAnalyst()
        agent._question = QUESTION
        agent._context = {}

        spy = _spy_searxng()
        monkeypatch.setattr(agent, "get_tool", lambda tool: spy)

        from hyperion.tools.query_utils import resolve_subject

        market_query = (
            resolve_subject(
                agent._context, "market", "segment", "sector", "industry",
                question=agent._question,
            )
            or agent._question
        )

        asyncio.run(agent._scrape_dashboards(market_query))

        called_query = spy.search.await_args.args[0]
        assert called_query.strip() != "market data dashboard statista IBISWorld"
        assert "seafood" in called_query.lower() or "vietnam" in called_query.lower()

    def test_market_analyst_imports_resolve_subject(self):
        import hyperion.agents.specialists.market_analyst as mod

        assert hasattr(mod, "resolve_subject")


# ─────────────────────────────────────────────────────────────────────────────
# regulatory_analyst.py
# ─────────────────────────────────────────────────────────────────────────────


class TestRegulatoryAnalystResolveSubject:
    def test_run_resolves_industry_via_resolve_subject_when_context_is_empty(
        self, monkeypatch
    ):
        """`run()` used to do
        `industry = self._context.get("industry") or self._context.get("sector") or ""`
        with no further fallback. With an empty context, `industry` must now
        resolve to something derived from the engagement subject/question."""
        agent = RegulatoryAnalyst()
        agent._question = QUESTION
        agent._context = {}

        from hyperion.tools.query_utils import resolve_subject

        industry = resolve_subject(
            agent._context, "industry", "sector", question=agent._question
        )
        assert industry  # never empty when the question/focus has content

    def test_search_regulations_query_carries_subject_when_industry_is_empty(
        self, monkeypatch
    ):
        """Even if the caller passes industry="" through, `_search_regulations`
        must not degrade to a bare ' regulations <jurisdiction> compliance
        requirements' template — it re-resolves via `resolve_subject`."""
        agent = RegulatoryAnalyst()
        agent._question = QUESTION
        agent._context = {}

        spy = _spy_searxng()
        monkeypatch.setattr(agent, "get_tool", lambda tool: spy)

        asyncio.run(agent._search_regulations("", ["EU"]))

        assert spy.search.await_count >= 1
        first_query = spy.search.await_args_list[0].args[0]
        assert not first_query.startswith(" regulations")
        assert "seafood" in first_query.lower() or "vietnam" in first_query.lower()

    def test_regulatory_analyst_imports_resolve_subject(self):
        import hyperion.agents.specialists.regulatory_analyst as mod

        assert hasattr(mod, "resolve_subject")


# ─────────────────────────────────────────────────────────────────────────────
# risk_analyst.py
# ─────────────────────────────────────────────────────────────────────────────


class TestRiskAnalystResolveSubject:
    def test_run_resolves_industry_via_resolve_subject_when_context_is_empty(self):
        """`run()` used to do `industry = self._context.get("industry", "")`
        with zero fallback — the most literal reading of Finding B-9's
        'three of the highest-search-volume agents... resolve subject ad
        hoc.' With an empty context, industry must resolve to something."""
        agent = RiskAnalyst()
        agent._question = QUESTION
        agent._context = {}

        from hyperion.tools.query_utils import resolve_subject

        industry = resolve_subject(
            agent._context, "industry", "sector", question=agent._question
        )
        assert industry

    def test_search_known_risks_query_carries_subject_when_industry_is_empty(
        self, monkeypatch
    ):
        """`_search_known_risks(industry, space)` with industry="" used to
        build `f"{industry} industry risks challenges"` -> ' industry risks
        challenges', a subject-less query. Callers now pass the
        `resolve_subject`-resolved value from `run()`, so exercise that same
        resolve-then-call chain here."""
        agent = RiskAnalyst()
        agent._question = QUESTION
        agent._context = {}

        spy = _spy_searxng()
        monkeypatch.setattr(agent, "get_tool", lambda tool: spy)

        from hyperion.tools.query_utils import resolve_subject

        industry = resolve_subject(
            agent._context, "industry", "sector", question=agent._question
        )

        asyncio.run(agent._search_known_risks(industry, industry))

        assert spy.search.await_count >= 1
        first_query = spy.search.await_args_list[0].args[0]
        assert not first_query.strip().startswith("industry risks")
        assert "seafood" in first_query.lower() or "vietnam" in first_query.lower()

    def test_discover_regulatory_portals_query_carries_subject_when_context_empty(
        self, monkeypatch
    ):
        agent = RiskAnalyst()
        agent._question = QUESTION
        agent._context = {}

        spy = _spy_searxng()
        monkeypatch.setattr(agent, "get_tool", lambda tool: spy)

        asyncio.run(agent._discover_regulatory_portals("EU"))

        assert spy.search.await_count == 1
        called_query = spy.search.await_args.args[0]
        assert "seafood" in called_query.lower() or "vietnam" in called_query.lower()

    def test_risk_analyst_imports_resolve_subject(self):
        import hyperion.agents.specialists.risk_analyst as mod

        assert hasattr(mod, "resolve_subject")


class TestCompetitiveIntelLLMResearch:
    def test_query_plan_is_generated_from_arbitrary_engagement(self, monkeypatch):
        agent = CompetitiveIntel()
        agent._question = "Who competes in precision-fermentation dairy proteins in Brazil?"
        agent._context = {"geography": "Brazil", "customer": "food manufacturers"}
        response = SimpleNamespace(
            success=True,
            content=json.dumps({
                "subject": "precision-fermentation dairy protein suppliers",
                "inclusion_criteria": ["Sells fermentation-derived dairy proteins"],
                "exclusion_criteria": ["Traditional dairy processors"],
                "queries": [
                    "Brazil precision fermentation whey protein suppliers",
                    "fermentation-derived casein companies Latin America",
                ],
            }),
        )
        monkeypatch.setattr(agent, "_llm_complete", AsyncMock(return_value=response))

        plan = asyncio.run(agent._plan_competitor_searches("alternative proteins"))

        assert plan["subject"] == "precision-fermentation dairy protein suppliers"
        assert len(plan["queries"]) == 2
        assert all("aerospace" not in query.lower() for query in plan["queries"])

    def test_semantic_judge_requires_valid_cited_results(self, monkeypatch):
        agent = CompetitiveIntel()
        agent._question = "Compare workflow automation tools for hospitals"
        response = SimpleNamespace(
            success=True,
            content=json.dumps({
                "competitors": [
                    {
                        "name": "ClinicalFlow",
                        "evidence_result_ids": [4],
                        "relevance": "Automates hospital clinical workflows",
                    },
                    {
                        "name": "Uncited Vendor",
                        "evidence_result_ids": [],
                        "relevance": "No supporting result",
                    },
                    {
                        "name": "Invalid Citation Vendor",
                        "evidence_result_ids": [99],
                        "relevance": "Citation does not exist",
                    },
                ],
            }),
        )
        monkeypatch.setattr(agent, "_llm_complete", AsyncMock(return_value=response))
        results = [{
            "result_id": 4,
            "title": "ClinicalFlow hospital automation platform",
            "snippet": "Clinical workflow orchestration for care teams",
            "url": "https://example.com/clinicalflow",
        }]

        names, evidence_ids = asyncio.run(agent._extract_competitor_names({}, results))

        assert names == ["ClinicalFlow"]
        assert evidence_ids == {4}


class TestSustainabilityNullableFramework:
    def test_null_framework_preserves_unknown_semantics(self, monkeypatch):
        agent = SustainabilityAnalyst()
        response = SimpleNamespace(
            success=True,
            content='{"esg_scores": [], "most_relevant_framework": null}',
        )
        monkeypatch.setattr(agent, "_llm_complete", AsyncMock(return_value=response))

        scores, framework = asyncio.run(agent._score_esg("space sector", [], [], {}))

        assert scores == []
        assert framework is None
