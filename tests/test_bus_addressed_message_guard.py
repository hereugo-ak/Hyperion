"""P2-17 / P2-G18 (bus half): an addressed escalation with no matching
subscriber must log at ERROR at publish time.

fact_checker.py publishes Channel.REQUESTS / MessageType.ESCALATION with
request_type="verify_claims" addressed to each specialist, and for five
months every specialist matched request_type against its own literal
("tam_number", "personas", ...) and none matched this one. The message
vanished silently. A message with no possible recipient is a bug, and
the bus is the only place that can see it — so the bus must say so.
"""

from __future__ import annotations

import logging

import pytest

from hyperion.agents.bus import AgentBus, Channel, MessageType
from hyperion.schemas.agents import AgentName


@pytest.fixture()
def bus():
    b = AgentBus()
    yield b


@pytest.mark.asyncio
async def test_addressed_escalation_with_no_subscriber_logs_error(bus, caplog):
    """An addressed ESCALATION (to_agent + request_type) with zero live
    subscribers on the channel is a bug and must be logged at ERROR."""
    with caplog.at_level(logging.ERROR):
        await bus.publish(
            Channel.REQUESTS,
            MessageType.ESCALATION,
            sender=AgentName.FACT_CHECKER,
            payload={
                "to_agent": AgentName.MARKET_ANALYST.value,
                "request_type": "verify_claims",
                "unverified_claims": [],
            },
        )
    assert any(
        "no matching subscriber" in r.message.lower() and "verify_claims" in r.message
        for r in caplog.records
        if r.levelno >= logging.ERROR
    ), (
        "expected an ERROR about the undeliverable message, got: "
        f"{[r.message for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_addressed_escalation_with_subscriber_is_silent(bus, caplog):
    """When a live subscriber exists for the channel, no ERROR is logged."""
    received = []

    async def handler(msg):
        received.append(msg)

    bus.subscribe(
        subscriber_id="test-sub",
        agent=AgentName.MARKET_ANALYST,
        channels={Channel.REQUESTS},
        callback=handler,
    )
    with caplog.at_level(logging.ERROR):
        await bus.publish(
            Channel.REQUESTS,
            MessageType.ESCALATION,
            sender=AgentName.FACT_CHECKER,
            payload={
                "to_agent": AgentName.MARKET_ANALYST.value,
                "request_type": "verify_claims",
                "unverified_claims": [],
            },
        )
    assert not any(
        "no matching subscriber" in r.message.lower() for r in caplog.records
    )


@pytest.mark.asyncio
async def test_broadcast_status_without_subscribers_is_fine(bus, caplog):
    """STATUS messages are broadcasts; absence of subscribers is normal
    (no TUI attached) and must NOT log an error."""
    with caplog.at_level(logging.ERROR):
        await bus.publish(
            Channel.STATUS,
            MessageType.STATUS,
            sender=AgentName.MARKET_ANALYST,
            payload={"state": "working"},
        )
    assert not any(
        "no matching subscriber" in r.message.lower() for r in caplog.records
    )
