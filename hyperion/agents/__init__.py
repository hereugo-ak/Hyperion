"""
HYPERION Agent System, the dynamic consulting team.

This package contains the 20-agent system that makes HYPERION a
proprietary consulting model, not a generic LLM wrapper. Every agent
has proprietary skills, assigned tools, a specific model tier, and
produces structured Pydantic output, not free text.

Architecture (§4):
    agents/
        base.py, BaseAgent: bus, router, tools, state, sub-agents
        bus.py, AgentBus: in-memory async pub/sub (§4.8)
        sub_agent.py, SubAgentRunner: junior agent for context isolation (§4.7)
        engagement_director.py, Agent 1: decompose, orchestrate, adapt
        synthesis_lead.py, Agent 2: reconcile, synthesize, recommend
        specialists/, 12 specialist agents (§4.4)
        support/, 4 support agents (§4.5)
        delivery/, 2 delivery agents (§4.6)

No agent is generic. No tool is idle. No skill is decorative. (§4.1)
"""

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import AgentBus, BusMessage, Channel, MessageType, get_bus, reset_bus
from hyperion.agents.delivery import (
    PRESENTATION_DESIGNER_SPEC,
    RENDER_ENGINE_SPEC,
    PresentationDesigner,
    RenderEngine,
)
from hyperion.agents.engagement_director import ENGAGEMENT_DIRECTOR_SPEC, EngagementDirector
from hyperion.agents.specialists import (
    COMPETITIVE_INTEL_SPEC,
    CONSUMER_INSIGHTS_SPEC,
    FINANCIAL_ANALYST_SPEC,
    INNOVATION_ANALYST_SPEC,
    MA_ANALYST_SPEC,
    MARKET_ANALYST_SPEC,
    OPERATIONS_ANALYST_SPEC,
    REGULATORY_ANALYST_SPEC,
    RISK_ANALYST_SPEC,
    STRATEGY_ANALYST_SPEC,
    SUSTAINABILITY_ANALYST_SPEC,
    TECHNOLOGY_ANALYST_SPEC,
    CompetitiveIntel,
    ConsumerInsightsAnalyst,
    FinancialAnalyst,
    InnovationAnalyst,
    MAAnalyst,
    MarketAnalyst,
    OperationsAnalyst,
    RegulatoryAnalyst,
    RiskAnalyst,
    StrategyAnalyst,
    SustainabilityAnalyst,
    TechnologyAnalyst,
)
from hyperion.agents.sub_agent import SubAgentRunner
from hyperion.agents.support import (
    DATA_VISUALIZER_SPEC,
    FACT_CHECKER_SPEC,
    QUALITY_GATE_SPEC,
    RESEARCH_LIBRARIAN_SPEC,
    DataVisualizer,
    FactChecker,
    QualityGate,
    ResearchLibrarian,
)
from hyperion.agents.synthesis_lead import SYNTHESIS_LEAD_SPEC, SynthesisLead

__all__ = [
    "BaseAgent",
    "AgentBus",
    "BusMessage",
    "Channel",
    "MessageType",
    "get_bus",
    "reset_bus",
    "SubAgentRunner",
    "EngagementDirector",
    "ENGAGEMENT_DIRECTOR_SPEC",
    "SynthesisLead",
    "SYNTHESIS_LEAD_SPEC",
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
    "ResearchLibrarian",
    "RESEARCH_LIBRARIAN_SPEC",
    "FactChecker",
    "FACT_CHECKER_SPEC",
    "DataVisualizer",
    "DATA_VISUALIZER_SPEC",
    "QualityGate",
    "QUALITY_GATE_SPEC",
    "PresentationDesigner",
    "PRESENTATION_DESIGNER_SPEC",
    "RenderEngine",
    "RENDER_ENGINE_SPEC",
]
