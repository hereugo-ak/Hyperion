"""W-18 acceptance tests for persistent request/token/cost accounting."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from hyperion.config import ModelSpec, ModelTier, ProviderType
from hyperion.orchestrator import EngagementResult
from hyperion.router.budget import DailyBudgetPlanner, TaskUrgency


def _model(*, rpd: int | None = 100, tpd: int | None = 100) -> ModelSpec:
    return ModelSpec(
        name="gpt-oss-120b",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=30,
        tpm=30_000,
        rpd=rpd,
        tpd=tpd,
        tier=ModelTier.STANDARD,
    )


def test_daily_request_consumption_survives_planner_restart(tmp_path):
    database = tmp_path / "shared-budget.sqlite"
    first = DailyBudgetPlanner(db_path=database)
    first.consume(ProviderType.GROQ, "gpt-oss-120b")

    second = DailyBudgetPlanner(db_path=database)
    budget = second.get_budget(ProviderType.GROQ)
    assert budget.consumed == 1
    assert budget.remaining_for_model(_model()) == 99


def test_tpd_is_enforced_during_candidate_filter_and_reservation(tmp_path):
    planner = DailyBudgetPlanner(db_path=tmp_path / "tokens.sqlite")
    model = _model(tpd=100)

    assert planner.reserve(ProviderType.GROQ, model, estimated_tokens=90, urgency=TaskUrgency.HIGH)
    assert not planner.can_serve(
        ProviderType.GROQ,
        model,
        urgency=TaskUrgency.HIGH,
        estimated_tokens=11,
    )
    assert not planner.reserve(
        ProviderType.GROQ, model, estimated_tokens=11, urgency=TaskUrgency.HIGH
    )


def test_actual_tokens_replace_reservation_and_generate_cost(tmp_path):
    planner = DailyBudgetPlanner(db_path=tmp_path / "cost.sqlite")
    model = _model(tpd=100)
    assert planner.reserve(ProviderType.GROQ, model, estimated_tokens=70, urgency=TaskUrgency.HIGH)

    cost = planner.reconcile_actual(
        ProviderType.GROQ,
        model.name,
        estimated_tokens=70,
        input_tokens=20,
        output_tokens=20,
        actual_tokens=40,
    )

    assert cost > 0
    assert planner.engagement_cost_usd == cost
    assert planner.get_budget(ProviderType.GROQ).remaining_tokens_for_model(model) == 60


def test_atomic_reservation_closes_concurrent_check_consume_gap(tmp_path):
    database = tmp_path / "atomic.sqlite"
    planner = DailyBudgetPlanner(db_path=database)
    model = _model(rpd=5, tpd=10_000)

    def reserve_once(_index: int) -> bool:
        return planner.reserve(
            ProviderType.GROQ,
            model,
            estimated_tokens=1,
            urgency=TaskUrgency.HIGH,
        )

    with ThreadPoolExecutor(max_workers=12) as executor:
        results = list(executor.map(reserve_once, range(20)))
    assert sum(results) == 5


def test_auth_refund_releases_request_and_reserved_tokens(tmp_path):
    planner = DailyBudgetPlanner(db_path=tmp_path / "refund.sqlite")
    model = _model(tpd=100)
    assert planner.reserve(ProviderType.GROQ, model, estimated_tokens=80, urgency=TaskUrgency.HIGH)
    planner.refund(ProviderType.GROQ, model.name, estimated_tokens=80)

    assert planner.get_budget(ProviderType.GROQ).consumed == 0
    assert planner.get_budget(ProviderType.GROQ).remaining_tokens_for_model(model) == 100


def test_engagement_result_exposes_estimated_llm_cost():
    result = EngagementResult(estimated_llm_cost_usd=0.012345)
    assert result.to_dict()["estimated_llm_cost_usd"] == 0.012345
