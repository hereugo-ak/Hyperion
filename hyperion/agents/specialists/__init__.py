"""Specialist agents — 12 domain experts with proprietary analytical skills."""

from hyperion.agents.specialists.competitive_intel import COMPETITIVE_INTEL_SPEC, CompetitiveIntel
from hyperion.agents.specialists.consumer_insights import (
    CONSUMER_INSIGHTS_SPEC,
    ConsumerInsightsAnalyst,
)
from hyperion.agents.specialists.financial_analyst import FINANCIAL_ANALYST_SPEC, FinancialAnalyst
from hyperion.agents.specialists.innovation_analyst import (
    INNOVATION_ANALYST_SPEC,
    InnovationAnalyst,
)
from hyperion.agents.specialists.ma_analyst import MA_ANALYST_SPEC, MAAnalyst
from hyperion.agents.specialists.market_analyst import MARKET_ANALYST_SPEC, MarketAnalyst
from hyperion.agents.specialists.operations_analyst import (
    OPERATIONS_ANALYST_SPEC,
    OperationsAnalyst,
)
from hyperion.agents.specialists.regulatory_analyst import (
    REGULATORY_ANALYST_SPEC,
    RegulatoryAnalyst,
)
from hyperion.agents.specialists.risk_analyst import RISK_ANALYST_SPEC, RiskAnalyst
from hyperion.agents.specialists.strategy_analyst import STRATEGY_ANALYST_SPEC, StrategyAnalyst
from hyperion.agents.specialists.sustainability_analyst import (
    SUSTAINABILITY_ANALYST_SPEC,
    SustainabilityAnalyst,
)
from hyperion.agents.specialists.technology_analyst import (
    TECHNOLOGY_ANALYST_SPEC,
    TechnologyAnalyst,
)

__all__ = [
    "MarketAnalyst",
    "MARKET_ANALYST_SPEC",
    "CompetitiveIntel",
    "COMPETITIVE_INTEL_SPEC",
    "FinancialAnalyst",
    "FINANCIAL_ANALYST_SPEC",
    "RiskAnalyst",
    "RISK_ANALYST_SPEC",
    "TechnologyAnalyst",
    "TECHNOLOGY_ANALYST_SPEC",
    "OperationsAnalyst",
    "OPERATIONS_ANALYST_SPEC",
    "RegulatoryAnalyst",
    "REGULATORY_ANALYST_SPEC",
    "SustainabilityAnalyst",
    "SUSTAINABILITY_ANALYST_SPEC",
    "ConsumerInsightsAnalyst",
    "CONSUMER_INSIGHTS_SPEC",
    "MAAnalyst",
    "MA_ANALYST_SPEC",
    "InnovationAnalyst",
    "INNOVATION_ANALYST_SPEC",
    "StrategyAnalyst",
    "STRATEGY_ANALYST_SPEC",
]
