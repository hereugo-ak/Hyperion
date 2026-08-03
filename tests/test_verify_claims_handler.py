"""P2-17 / P2-G18: every specialist handles request_type="verify_claims"
via a shared base-class handler.

Before the fix, fact_checker published request_type="verify_claims" to all
11 specialists and every specialist's _handle_bus_message matched
request_type against its own literals (tam_number, peer_benchmarks, ...),
so the gap-fill request vanished silently. The designed Step 6 ("Flag
unverified claims to originating specialist") never did anything.

After the fix: BaseAgent._handle_verify_claims is the shared handler —
it records the request for the agent's next run() and acknowledges it on
the bus — and the default _handle_bus_message routes verify_claims
messages addressed to the agent to it. Specialists that override
_handle_bus_message for their own request types must still route
verify_claims to the shared handler.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from hyperion.agents.bus import BusMessage, Channel, MessageType
from hyperion.schemas.agents import AgentName


def _make_agent():
    from hyperion.agents.base import BaseAgent

    class _Concrete(BaseAgent):
        async def run(self, *args, **kwargs):  # pragma: no cover - unused
            return None

    agent = _Concrete.__new__(_Concrete)
    agent.spec = SimpleNamespace(
        system_prompt="spec",
        name=AgentName.MARKET_ANALYST,
        model_tier=SimpleNamespace(value="standard"),
    )
    agent.bus = SimpleNamespace(publish=AsyncMock())
    agent._pending_verify_requests = []
    return agent


def _verify_msg(to_agent: str = "market_analyst") -> BusMessage:
    return BusMessage(
        channel=Channel.REQUESTS,
        msg_type=MessageType.ESCALATION,
        sender=AgentName.FACT_CHECKER,
        payload={
            "to_agent": to_agent,
            "from_agent": "fact_checker",
            "request_type": "verify_claims",
            "unverified_claims": [{"claim": "TAM is $12B", "id": "c1"}],
            "message": "Fact Checker could not verify 1 claim(s).",
        },
    )


class TestHandleVerifyClaims:
    def test_handler_exists_on_base(self):
        from hyperion.agents.base import BaseAgent

        assert hasattr(BaseAgent, "_handle_verify_claims")
        assert callable(BaseAgent._handle_verify_claims)

    def test_verify_claims_recorded_for_next_run(self):
        agent = _make_agent()
        asyncio.run(agent._handle_verify_claims(_verify_msg().payload))
        assert agent._pending_verify_requests, "request must be recorded"
        assert agent._pending_verify_requests[0]["unverified_claims"]

    def test_base_dispatch_routes_verify_claims(self):
        agent = _make_agent()
        asyncio.run(agent._handle_bus_message(_verify_msg()))
        assert agent._pending_verify_requests, (
            "base _handle_bus_message must route verify_claims to the handler"
        )

    def test_message_addressed_to_another_agent_is_ignored(self):
        agent = _make_agent()
        asyncio.run(agent._handle_bus_message(_verify_msg(to_agent="risk_analyst")))
        assert agent._pending_verify_requests == []

    def test_all_11_specialists_route_verify_claims(self):
        """P2-G18: no specialist override may drop a verify_claims request."""
        import importlib

        specialists = {
            "market_analyst": ("market_analyst", "MarketAnalyst"),
            "competitive_intel": ("competitive_intel", "CompetitiveIntel"),
            "financial_analyst": ("financial_analyst", "FinancialAnalyst"),
            "risk_analyst": ("risk_analyst", "RiskAnalyst"),
            "technology_analyst": ("technology_analyst", "TechnologyAnalyst"),
            "operations_analyst": ("operations_analyst", "OperationsAnalyst"),
            "regulatory_analyst": ("regulatory_analyst", "RegulatoryAnalyst"),
            "sustainability_analyst": ("sustainability_analyst", "SustainabilityAnalyst"),
            "consumer_insights": ("consumer_insights", "ConsumerInsightsAnalyst"),
            "ma_analyst": ("ma_analyst", "MAAnalyst"),
            "innovation_analyst": ("innovation_analyst", "InnovationAnalyst"),
        }
        for module_name, (agent_value, class_name) in specialists.items():
            module = importlib.import_module(
                f"hyperion.agents.specialists.{module_name}"
            )
            cls = getattr(module, class_name)
            agent = cls.__new__(cls)
            agent.spec = SimpleNamespace(
                system_prompt="spec",
                name=AgentName(agent_value),
                model_tier=SimpleNamespace(value="standard"),
            )
            agent.bus = SimpleNamespace(publish=AsyncMock())
            agent._pending_verify_requests = []
            # Initialise any attributes overrides may touch on this path.
            agent._engagement_id = ""
            agent._question = ""
            agent._context = {}

            asyncio.run(agent._handle_bus_message(_verify_msg(to_agent=agent_value)))
            assert agent._pending_verify_requests, (
                f"{class_name}._handle_bus_message dropped a verify_claims "
                f"request — it must route to the shared base handler (P2-17)"
            )
