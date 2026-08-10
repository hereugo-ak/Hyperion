from __future__ import annotations

import ast
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hyperion.config import ProviderConfig, ProviderType
from hyperion.infra.quota import GroundingQuotaLedger, QuotaExhausted
from hyperion.output.methodology import build_methodology
from hyperion.router.providers.google import GoogleGroundingResponse, GoogleProvider
from hyperion.tools.grounded_search import (
    GroundedSearchClient,
    GroundedSearchOutcome,
    GroundingReason,
)
from hyperion.tools.searxng import SearchResult, SearxNGClient


def fixed_now() -> datetime:
    return datetime(2026, 8, 1, 12, 0, tzinfo=UTC)


def _ledger(path: Path, *, daily: int = 10, monthly: int = 100) -> GroundingQuotaLedger:
    return GroundingQuotaLedger(
        path,
        daily_limit=daily,
        monthly_limit=monthly,
        reserve_fraction=0.10,
        now=fixed_now,
    )


def test_quota_reserve_floor_reconciliation_and_restart(tmp_path: Path) -> None:
    path = tmp_path / "quota.json"
    ledger = _ledger(path)
    reservation = ledger.reserve(
        9,
        model="gemini-3.1-flash-lite",
        query="official inflation release",
        engagement_id="eng_123",
    )
    assert ledger.remaining("gemini-3")["available"] == 0
    with pytest.raises(QuotaExhausted):
        ledger.reserve(
            1,
            model="gemini-3.1-flash-lite",
            query="routine breadth",
            engagement_id="eng_123",
        )

    ledger.settle(reservation, 2, outcome="success")
    restarted = _ledger(path)
    assert restarted.remaining("gemini-3")["available"] == 7
    assert restarted.remaining("gemini-3", high_value=True)["daily"] == 8
    audit = json.loads(path.read_text(encoding="utf-8"))
    assert [event["type"] for event in audit["events"]] == ["reserved", "settled"]
    assert audit["events"][1]["actual_units"] == 2
    assert audit["events"][1]["query"] == "official inflation release"
    assert audit["events"][1]["engagement_id"] == "eng_123"


def test_high_value_may_use_reserve_but_never_exceed_limit(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path / "quota.json")
    routine = ledger.reserve(
        9,
        model="gemini-3.1-flash-lite",
        query="routine",
        engagement_id="eng",
    )
    ledger.settle(routine, 9, outcome="success")
    critical = ledger.reserve(
        1,
        model="gemini-3.1-flash-lite",
        query="attributed claim",
        engagement_id="eng",
        high_value=True,
    )
    ledger.settle(critical, 1, outcome="success")
    with pytest.raises(QuotaExhausted):
        ledger.reserve(
            1,
            model="gemini-3.1-flash-lite",
            query="beyond quota",
            engagement_id="eng",
            high_value=True,
        )


def test_provider_preserves_generate_content_grounding_metadata() -> None:
    payload = {
        "candidates": [{
            "finishReason": "STOP",
            "content": {"parts": [{"text": "Answer with citations."}]},
            "groundingMetadata": {
                "webSearchQueries": ["one", "", "two"],
                "groundingChunks": [
                    {"web": {"uri": "https://example.gov/data", "title": "Official data"}}
                ],
                "groundingSupports": [{
                    "groundingChunkIndices": [0],
                    "segment": {"text": "supported sentence"},
                }],
            },
        }]
    }
    parsed = GoogleProvider.parse_grounding_response(
        payload, model="gemini-3.1-flash-lite"
    )
    assert parsed.text == "Answer with citations."
    assert parsed.web_search_queries == ["one", "two"]
    assert parsed.billable_units == 2
    assert parsed.grounding_chunks[0]["web"]["uri"] == "https://example.gov/data"
    assert parsed.grounding_supports[0]["segment"]["text"] == "supported sentence"


def test_provider_supports_interactions_citations_and_safety_refusal() -> None:
    interactions = {
        "outputs": [
            {"type": "google_search_call", "arguments": {"queries": ["official source"]}},
            {"type": "model_output", "content": [{
                "text": "cited",
                "annotations": [{"url_citation": {
                    "url": "https://authority.example/report",
                    "title": "Authority report",
                }}],
            }]},
        ]
    }
    parsed = GoogleProvider.parse_grounding_response(interactions, model="gemini-3-flash")
    assert parsed.billable_units == 1
    assert parsed.grounding_chunks == [{"web": {
        "uri": "https://authority.example/report",
        "title": "Authority report",
    }}]
    refused = GoogleProvider.parse_grounding_response(
        {"promptFeedback": {"blockReason": "SAFETY"}}, model="gemini-2.5-flash"
    )
    assert refused.safety_refused
    assert refused.billable_units == 0


class _Provider:
    def __init__(self, response: GoogleGroundingResponse) -> None:
        self.response = response
        self.config = ProviderConfig(api_key="secret", base_url="")

    async def grounded_generate(self, *, model: str, query: str) -> GoogleGroundingResponse:
        assert model == "gemini-3.1-flash-lite"
        assert query
        return self.response


def _settings(tmp_path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        providers={ProviderType.GOOGLE: ProviderConfig(api_key="secret", base_url="")},
        google_grounding_enabled=True,
        google_grounding_model="gemini-3.1-flash-lite",
        google_grounding_daily_limit=10,
        google_grounding_monthly_limit=100,
        google_grounding_reserve_fraction=0.10,
        google_grounding_max_queries_per_call=4,
        google_grounding_ledger_path=tmp_path / "quota.json",
    )


@pytest.mark.asyncio
async def test_grounded_client_normalizes_to_single_search_result_type(tmp_path: Path) -> None:
    response = GoogleGroundingResponse(
        model="gemini-3.1-flash-lite",
        web_search_queries=["query a", "query b"],
        grounding_chunks=[
            {"web": {"uri": "https://example.gov/a", "title": "A"}},
            {"web": {"uri": "https://example.gov/a", "title": "duplicate"}},
        ],
        grounding_supports=[{
            "groundingChunkIndices": [0],
            "segment": {"text": "Official support."},
        }],
    )
    client = GroundedSearchClient(
        settings=_settings(tmp_path),
        provider=_Provider(response),  # type: ignore[arg-type]
        ledger=_ledger(tmp_path / "quota.json"),
    )
    outcome = await client.search(
        "official output data",
        engagement_id="eng_audit",
        reason=GroundingReason.ATTRIBUTION_VERIFICATION,
    )
    assert outcome.actual_units == 2
    assert len(outcome.results) == 1
    assert isinstance(outcome.results[0], SearchResult)
    assert outcome.results[0].backend == "gemini"
    assert outcome.results[0].snippet == "Official support."
    assert SearchResult(title="x", url="https://x").backend == "searxng"


@pytest.mark.asyncio
async def test_search_choke_point_escalates_to_grounding_after_local_exhaustion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, GroundingReason]] = []

    class FakeGroundedSearchClient:
        def __init__(self, *, settings=None) -> None:
            assert settings is sentinel

        async def search(self, query: str, *, reason: GroundingReason):
            calls.append((query, reason))
            return GroundedSearchOutcome(
                query=query,
                reason=reason,
                results=[SearchResult(
                    title="Official source",
                    url="https://authority.example/report",
                    backend="gemini",
                    engine="google-search-grounding",
                )],
                search_queries=["official source query"],
                actual_units=1,
            )

    sentinel = object()
    monkeypatch.setattr(
        "hyperion.tools.grounded_search.GroundedSearchClient",
        FakeGroundedSearchClient,
    )
    client = SearxNGClient(settings=sentinel)
    response = await client._search_grounded_fallback(
        "official market data",
        num_results=5,
        categories="general",
    )

    assert response is not None
    assert calls == [("official market data", GroundingReason.RETRY_EXHAUSTED)]
    assert response.results[0].backend == "gemini"
    assert response.degradation_events[0]["billable_queries"] == 1
    await client.close()


def test_methodology_reports_recorded_backend_mix_without_engine_names() -> None:
    report = SimpleNamespace(
        sections=[], key_findings=[], fact_check_report=None, limitations=[]
    )
    record = build_methodology(
        report,
        queries_issued=7,
        backend_query_counts={"searxng": 4, "jina": 2, "gemini": 3},
        retrieval_constraints=["quota unavailable"],
    )
    subsection = record.by_key("retrieval_strategy_and_coverage")
    joined = " ".join([subsection.narrative, *subsection.facts]).lower()
    assert "6 independent index queries" in joined
    assert "3 grounded model search queries" in joined
    assert "grounded retrieval constraints recorded: 1" in joined
    assert "searxng" not in joined
    assert "gemini" not in joined


def test_agents_do_not_branch_on_grounding_backend_identity() -> None:
    agents = Path(__file__).parents[1] / "hyperion" / "agents"
    violations: list[str] = []
    for path in agents.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Compare):
                continue
            source = ast.get_source_segment(path.read_text(encoding="utf-8"), node) or ""
            if "backend" in source.casefold() and "gemini" in source.casefold():
                violations.append(f"{path.relative_to(agents)}:{node.lineno}: {source}")
    assert violations == []


def test_grounding_default_quota_is_1500_per_day() -> None:
    """P1.4 (overhaul §6 P1, 2026-08-10): the Gemini 2.5 Flash grounding tier
    ships 1500 requests/day free. The shipped default must match — the old
    20/day was a conservative guess that strangled the last-resort web class."""
    from hyperion.config import Settings

    settings = Settings(_env_file=None)
    assert settings.google_grounding_daily_limit == 1500
    assert settings.google_grounding_monthly_limit == 45000
    assert settings.google_grounding_enabled is True


def test_grounding_quota_is_no_longer_a_daily_drip(tmp_path: Path) -> None:
    """P1.4: at 1500/day a routine grounded call reserves real budget, proving
    the quota ledger was the bottleneck, not the engine."""
    ledger = GroundingQuotaLedger(
        tmp_path / "quota.json",
        daily_limit=1500,
        monthly_limit=45000,
        reserve_fraction=0.10,
    )
    reservation = ledger.reserve(
        4,
        model="gemini-2.5-flash",
        query="should india build more space startups",
        engagement_id="eng_p14",
    )
    remaining = ledger.remaining("gemini-2.5", high_value=False)
    # 1500/day minus 10% reserve (150) = 1350 routine budget; one 4-unit call
    # leaves ~1346. The old 20/day cap would have left ~14 — the class is no
    # longer a drip.
    assert remaining["available"] >= 1300
    assert reservation.units == 4
