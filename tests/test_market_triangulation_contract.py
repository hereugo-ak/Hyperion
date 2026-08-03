"""D-21 regression tests for the market triangulation return contract."""

from __future__ import annotations

import inspect
from types import SimpleNamespace

import pytest

from hyperion.agents.specialists.market_analyst import MarketAnalyst
from hyperion.schemas.models import FinancialMetric


@pytest.mark.asyncio
async def test_provider_failure_still_returns_three_values(monkeypatch: pytest.MonkeyPatch) -> None:
    analyst = object.__new__(MarketAnalyst)
    analyst._sources = []

    async def failed_completion(**_kwargs):
        return SimpleNamespace(success=False, content="")

    monkeypatch.setattr(analyst, "_llm_complete", failed_completion)
    metric = FinancialMetric(name="TAM", value="Unknown", unit="$")

    tam, cagr, contradictions = await analyst._cagr_triangulation(metric, metric, [])

    assert tam.name == "TAM (Triangulated)"
    assert cagr.name == "CAGR"
    assert cagr.unit == "%"
    assert contradictions == []


def test_run_does_not_runtime_sniff_tuple_length() -> None:
    source = inspect.getsource(MarketAnalyst.run)
    assert "len(triangulated_result)" not in source
    assert "Handle both 2-tuple" not in source
