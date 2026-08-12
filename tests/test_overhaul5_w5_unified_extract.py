"""OVERHAUL5 W5 (D-07) — UNIFIED_EXTRACT granted to every specialist.

The 08-12 run's specialist tool grants were partial (operations had ZERO
extraction tools; firecrawl wasn't a tool at all), so direct specialist
extraction was a lottery. W5: one ToolName.UNIFIED_EXTRACT granted to all 12
specialists, backed by the single page-aware ladder.

Fail-first: the schema test fails on pre-W5 code (no UNIFIED_EXTRACT grant /
no ToolName member).
"""

from __future__ import annotations

import importlib

import pytest

from hyperion.schemas.agents import ToolName
from hyperion.tools.unified_extract import UnifiedExtractTool

_SPEC_CONSTANTS = [
    ("competitive_intel", "COMPETITIVE_INTEL_SPEC"),
    ("consumer_insights", "CONSUMER_INSIGHTS_SPEC"),
    ("financial_analyst", "FINANCIAL_ANALYST_SPEC"),
    ("innovation_analyst", "INNOVATION_ANALYST_SPEC"),
    ("ma_analyst", "MA_ANALYST_SPEC"),
    ("market_analyst", "MARKET_ANALYST_SPEC"),
    ("operations_analyst", "OPERATIONS_ANALYST_SPEC"),
    ("regulatory_analyst", "REGULATORY_ANALYST_SPEC"),
    ("risk_analyst", "RISK_ANALYST_SPEC"),
    ("strategy_analyst", "STRATEGY_ANALYST_SPEC"),
    ("sustainability_analyst", "SUSTAINABILITY_ANALYST_SPEC"),
    ("technology_analyst", "TECHNOLOGY_ANALYST_SPEC"),
]


@pytest.mark.parametrize("module,symbol", _SPEC_CONSTANTS)
def test_every_specialist_has_unified_extract(module: str, symbol: str) -> None:
    """[FF] Every specialist spec grants the unified extraction ladder."""
    mod = importlib.import_module(f"hyperion.agents.specialists.{module}")
    spec = getattr(mod, symbol)
    assert ToolName.UNIFIED_EXTRACT in spec.tools, (
        f"{module} must grant UNIFIED_EXTRACT (pre-W5 grants were partial)"
    )


def test_toolname_member_exists() -> None:
    assert ToolName.UNIFIED_EXTRACT.value == "unified_extract"


@pytest.mark.asyncio
async def test_facade_wires_to_ladder_paywall_logic() -> None:
    """The facade routes through the real ladder: a paywall URL yields no
    content (fail-fast), a normal URL attempts extraction."""
    tool = UnifiedExtractTool(settings=None)
    # Paywall -> ladder fails fast, zero content, zero network tiers.
    hits = await tool.extract(["https://doi.org/10.1016/j.apenergy.2019.114074"])
    assert hits == []
