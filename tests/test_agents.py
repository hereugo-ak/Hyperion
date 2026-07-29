"""
Tests for the HYPERION Agent System — agent specs, bus, state management.

Tests:
- Agent spec compliance (tools, tier, skills)
- AgentBus pub/sub functionality
- Agent state transitions
- Escalation handling
- Finding publication

Architecture reference: §4 Agent System
"""

import asyncio

from hyperion.agents.bus import Channel, MessageType, get_bus, reset_bus
from hyperion.agents.engagement_director import (
    ENGAGEMENT_DIRECTOR_SPEC,
    QUESTION_TYPE_AGENTS,
    EngagementDirector,
)
from hyperion.agents.synthesis_lead import SYNTHESIS_LEAD_SPEC
from hyperion.schemas.agents import AgentName, ModelTier
from hyperion.schemas.workflow import QuestionType


class TestAgentSpecs:
    """Test that all agent specs comply with architecture requirements."""

    def test_engagement_director_spec(self):
        """Engagement Director spec should have correct attributes."""
        spec = ENGAGEMENT_DIRECTOR_SPEC
        assert spec.name == AgentName.ENGAGEMENT_DIRECTOR
        assert spec.model_tier == ModelTier.STRONG
        assert len(spec.skills) >= 5  # At least 5 skills per §4.3
        assert len(spec.tools) > 0  # Has tool access

    def test_synthesis_lead_spec(self):
        """Synthesis Lead spec should have correct attributes."""
        spec = SYNTHESIS_LEAD_SPEC
        assert spec.name == AgentName.SYNTHESIS_LEAD
        assert spec.model_tier == ModelTier.DEEP
        assert len(spec.skills) >= 3

    def test_all_specialist_specs_have_tools(self):
        """Every specialist should have at least 2 tools."""
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
        )

        specs = [
            MARKET_ANALYST_SPEC, COMPETITIVE_INTEL_SPEC, FINANCIAL_ANALYST_SPEC,
            RISK_ANALYST_SPEC, TECHNOLOGY_ANALYST_SPEC, OPERATIONS_ANALYST_SPEC,
            REGULATORY_ANALYST_SPEC, SUSTAINABILITY_ANALYST_SPEC, CONSUMER_INSIGHTS_SPEC,
            MA_ANALYST_SPEC, INNOVATION_ANALYST_SPEC, STRATEGY_ANALYST_SPEC,
        ]

        for spec in specs:
            assert len(spec.tools) >= 2, f"{spec.name.value} has fewer than 2 tools"
            assert len(spec.skills) >= 3, f"{spec.name.value} has fewer than 3 skills"


class TestAgentBus:
    """Test the AgentBus pub/sub system."""

    def test_bus_singleton(self):
        """Bus should return the same instance."""
        reset_bus()
        bus1 = get_bus()
        bus2 = get_bus()
        assert bus1 is bus2

    def test_bus_subscribe_and_publish(self):
        """Messages should be delivered to subscribers."""
        reset_bus()
        bus = get_bus()

        received = []

        async def callback(msg):
            received.append(msg)

        bus.subscribe(
            subscriber_id="test-1",
            agent=AgentName.MARKET_ANALYST,
            channels={Channel.STATUS},
            callback=callback,
        )

        async def run_test():
            await bus.start()
            await bus.publish(
                channel=Channel.STATUS,
                msg_type=MessageType.STATUS,
                sender=AgentName.MARKET_ANALYST,
                payload={"state": "working"},
            )
            # Give the bus time to deliver
            await asyncio.sleep(0.1)
            await bus.stop()

        asyncio.run(run_test())
        assert len(received) == 1
        assert received[0].payload["state"] == "working"

    def test_bus_channel_filtering(self):
        """Subscribers should only receive messages on their channels."""
        reset_bus()
        bus = get_bus()

        status_received = []
        findings_received = []

        async def status_callback(msg):
            status_received.append(msg)

        async def findings_callback(msg):
            findings_received.append(msg)

        bus.subscribe(
            subscriber_id="status-sub",
            agent=AgentName.MARKET_ANALYST,
            channels={Channel.STATUS},
            callback=status_callback,
        )
        bus.subscribe(
            subscriber_id="findings-sub",
            agent=AgentName.FINANCIAL_ANALYST,
            channels={Channel.FINDINGS},
            callback=findings_callback,
        )

        async def run_test():
            await bus.start()
            await bus.publish(
                channel=Channel.STATUS,
                msg_type=MessageType.STATUS,
                sender=AgentName.MARKET_ANALYST,
                payload={"state": "working"},
            )
            await bus.publish(
                channel=Channel.FINDINGS,
                msg_type=MessageType.FINDING,
                sender=AgentName.MARKET_ANALYST,
                payload={"content": "TAM estimate"},
            )
            await asyncio.sleep(0.1)
            await bus.stop()

        asyncio.run(run_test())
        assert len(status_received) == 1
        assert len(findings_received) == 1


class TestQuestionClassification:
    """Test the Engagement Director's question classification."""

    def test_go_no_go_heuristic(self):
        """Go/No-Go patterns should be detected heuristically."""
        director = EngagementDirector.__new__(EngagementDirector)
        director._current_dag = None
        director._escalation_count = 0

        result = director._classify_question_heuristic("Should we enter the Indian market?")
        assert QuestionType.GO_NO_GO in result

    def test_comparison_heuristic(self):
        """Comparison patterns should be detected."""
        director = EngagementDirector.__new__(EngagementDirector)
        director._current_dag = None
        director._escalation_count = 0

        result = director._classify_question_heuristic("Compare AWS vs Azure vs GCP")
        assert QuestionType.COMPARISON in result

    def test_question_type_agent_map_covers_all_types(self):
        """Every question type should have at least one agent mapping."""
        for qt in QuestionType:
            assert qt in QUESTION_TYPE_AGENTS, f"{qt} missing from agent map"
            assert len(QUESTION_TYPE_AGENTS[qt]) > 0, f"{qt} has no agents"
