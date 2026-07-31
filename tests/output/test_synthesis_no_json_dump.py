"""Regression: the synthesis bus handler must never serialize payloads (P2-09).

Before the fix, a specialist publishing an analysis payload without a
``finding`` key produced a synthetic KeyFinding whose ``content`` was up to
3,000 characters of ``json.dumps(...)`` -- whole sources arrays, accessed_at
keys and \\uXXXX escapes printed as chapter prose in seven chapters of
report B.
"""

from __future__ import annotations

import asyncio

from hyperion.agents.bus import BusMessage, Channel, MessageType
from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.schemas.agents import AgentName


def _make_msg(payload: dict) -> BusMessage:
    return BusMessage(
        channel=Channel.FINDINGS,
        msg_type=MessageType.FINDING,
        sender=AgentName.MARKET_ANALYST,
        payload=payload,
    )


def _fresh_agent() -> SynthesisLead:
    agent = SynthesisLead.__new__(SynthesisLead)
    agent._collected_findings = []
    agent._findings_by_agent = {}
    return agent


class TestNoJsonDumpPath:
    def test_payload_with_metrics_becomes_prose_not_json(self):
        agent = _fresh_agent()
        payload = {
            "market_analysis": {
                "tam_triangulated": {
                    "name": "TAM (Triangulated)",
                    "value": "$12.5B-$38.9B",
                    "unit": "$",
                },
                "sources": [{"id": "src_000", "accessed_at": "2026-07-01"}],
            }
        }
        asyncio.run(agent._handle_bus_message(_make_msg(payload)))

        assert agent._collected_findings, "expected a synthetic finding from metrics"
        finding = agent._collected_findings[0]
        assert "{" not in finding.content
        assert "accessed_at" not in finding.content
        assert '"id": "src_000"' not in finding.content
        assert "\\u" not in finding.content

    def test_payload_without_presentable_metrics_yields_no_json_dump(self):
        """A payload with no presentable metrics is a gap, not a JSON dump."""
        agent = _fresh_agent()
        payload = {
            "market_analysis": {
                "sources": [{"id": "src_000", "accessed_at": "2026-07-01"}],
            }
        }
        asyncio.run(agent._handle_bus_message(_make_msg(payload)))

        for finding in agent._collected_findings:
            assert "{" not in finding.content
            assert "accessed_at" not in finding.content
