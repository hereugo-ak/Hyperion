"""
HYPERION AgentBus — in-memory async pub/sub system for inter-agent communication.

This is NOT a generic message queue. This is the nervous system of the
multi-agent consulting model. Built on asyncio.Queue — lightweight, fast,
zero dependencies. No external broker (no Redis, no RabbitMQ). (§4.8)

Channels:
- status: all agents publish state changes (IDLE/WORKING/WAITING/DONE/BLOCKED)
- findings: agents publish completed findings for other agents to consume
- requests: agents request data/context from other agents
- escalation: agents flag issues to Engagement Director
- handoff: agents pass tasks to other agents
- tui: status updates for the TUI to display

Message types:
- FINDING: {agent, finding_type, content, sources, confidence, timestamp}
- REQUEST: {from_agent, to_agent, request_type, context, timestamp}
- STATUS: {agent, state, detail, timestamp}
- HANDOFF: {from_agent, to_agent, task, context_bundle, timestamp}
- ESCALATION: {agent, issue, suggested_action, timestamp}

Subscription pattern (§4.8):
- Engagement Director subscribes to ALL channels (omniscient)
- Specialists subscribe to findings and requests (need-aware)
- Support agents subscribe to findings (Fact Checker needs all findings)
- TUI subscribes to status and findings (display only)
- Delivery agents subscribe to findings (need final report content)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Coroutine

from hyperion.schemas.agents import AgentName, AgentState
from hyperion.schemas.models import KeyFinding


# ─────────────────────────────────────────────────────────────────────────────
# Channel and Message Type Definitions (§4.8)
# ─────────────────────────────────────────────────────────────────────────────


class Channel(str, Enum):
    """The 6 AgentBus channels (§4.8). Each is a separate async queue."""

    STATUS = "status"
    FINDINGS = "findings"
    REQUESTS = "requests"
    ESCALATION = "escalation"
    HANDOFF = "handoff"
    TUI = "tui"


class MessageType(str, Enum):
    """The 5 message types that flow through the AgentBus (§4.8)."""

    FINDING = "finding"
    REQUEST = "request"
    STATUS = "status"
    HANDOFF = "handoff"
    ESCALATION = "escalation"


@dataclass
class BusMessage:
    """A single message on the AgentBus.

    All messages share this structure — the channel determines routing,
    the type determines semantics, and the payload carries the data.

    The timestamp is set at creation time and used by the TUI to display
    a chronological findings stream (§8.7).
    """

    channel: Channel
    msg_type: MessageType
    sender: AgentName
    payload: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    # Convenience accessors for common payload fields

    @property
    def agent(self) -> str:
        return self.payload.get("agent", self.sender.value)

    @property
    def state(self) -> str:
        return self.payload.get("state", "")

    @property
    def detail(self) -> str:
        return self.payload.get("detail", "")

    @property
    def finding(self) -> KeyFinding | None:
        return self.payload.get("finding")

    @property
    def to_agent(self) -> str:
        return self.payload.get("to_agent", "")

    @property
    def from_agent(self) -> str:
        return self.payload.get("from_agent", self.sender.value)

    @property
    def issue(self) -> str:
        return self.payload.get("issue", "")

    @property
    def suggested_action(self) -> str:
        return self.payload.get("suggested_action", "")


# ─────────────────────────────────────────────────────────────────────────────
# Subscription — a typed subscriber callback
# ─────────────────────────────────────────────────────────────────────────────

# A subscriber is an async callable that receives a BusMessage
Subscriber = Callable[[BusMessage], Coroutine[Any, Any, None]]


@dataclass
class Subscription:
    """A single subscription to a channel.

    Tracks the subscriber callback, which agent it belongs to, and
    whether it's a durable subscription (survives agent restart).
    """

    subscriber_id: str
    agent: AgentName | None  # None = system subscriber (TUI, etc.)
    channels: set[Channel]
    callback: Subscriber
    durable: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# AgentBus — the in-memory async pub/sub system
# ─────────────────────────────────────────────────────────────────────────────


class AgentBus:
    """In-memory async pub/sub system for inter-agent communication.

    No external broker. Built on asyncio.Queue — one queue per channel.
    Agents publish messages; subscribers consume them asynchronously.

    The bus is the ONLY communication mechanism between agents. Agents
    never call each other directly — they publish to the bus and let
    the subscription pattern handle routing. This decouples agents and
    makes the system extensible (new agents just subscribe to channels).

    Subscription patterns (§4.8):
    - Engagement Director: ALL channels (omniscient)
    - Specialists: findings + requests (need-aware)
    - Support agents: findings (Fact Checker needs all findings)
    - TUI: status + findings (display only)
    - Delivery agents: findings (need final report content)
    """

    def __init__(self) -> None:
        # Per-channel queues — each channel is an independent async queue
        self._queues: dict[Channel, asyncio.Queue[BusMessage]] = {
            ch: asyncio.Queue() for ch in Channel
        }

        # Subscriptions registry
        self._subscriptions: dict[str, Subscription] = {}

        # Dispatch tasks — one per channel, running concurrently
        self._dispatch_tasks: dict[Channel, asyncio.Task[None]] = {}
        self._running = False

        # Message history for debugging and TUI scrollback
        self._history: list[BusMessage] = []
        self._max_history = 1000

        # Per-agent state cache (for TUI agent grid display)
        self._agent_states: dict[AgentName, AgentState] = {}

        # Retained findings — survives until explicitly cleared.
        # Late subscribers (Synthesis Lead, Fact Checker) are instantiated
        # after specialists have already published. Without retention,
        # those findings are lost forever (pub/sub is fire-and-forget).
        self._retained_findings: list[BusMessage] = []

    async def start(self) -> None:
        """Start the dispatch tasks — one per channel."""
        if self._running:
            return
        self._running = True
        for channel in Channel:
            self._dispatch_tasks[channel] = asyncio.create_task(
                self._dispatch_loop(channel)
            )

    async def stop(self) -> None:
        """Stop all dispatch tasks."""
        self._running = False
        for task in self._dispatch_tasks.values():
            task.cancel()
        self._dispatch_tasks.clear()

    async def _dispatch_loop(self, channel: Channel) -> None:
        """Dispatch loop for a single channel.

        Pulls messages from the channel queue and delivers them to all
        subscribers of that channel. Runs concurrently across all channels.
        """
        queue = self._queues[channel]
        while self._running:
            try:
                msg = await asyncio.wait_for(queue.get(), timeout=0.5)
            except asyncio.TimeoutError:
                continue

            # Deliver to all subscribers of this channel
            for sub in list(self._subscriptions.values()):
                if channel in sub.channels:
                    try:
                        await sub.callback(msg)
                    except Exception:
                        pass  # Subscriber errors don't crash the bus

            queue.task_done()

    def subscribe(
        self,
        subscriber_id: str,
        agent: AgentName | None,
        channels: set[Channel],
        callback: Subscriber,
        durable: bool = False,
    ) -> None:
        """Subscribe to one or more channels.

        The Engagement Director subscribes to ALL channels (omniscient).
        Specialists subscribe to findings + requests (need-aware).
        Support agents subscribe to findings.
        TUI subscribes to status + findings.

        If Channel.FINDINGS is in the subscription, all retained findings
        are replayed to the new subscriber immediately. This ensures late
        subscribers (Synthesis Lead, Fact Checker) don't miss findings
        published before they were instantiated.
        """
        self._subscriptions[subscriber_id] = Subscription(
            subscriber_id=subscriber_id,
            agent=agent,
            channels=channels,
            callback=callback,
            durable=durable,
        )

        # Replay retained findings to late subscribers
        if Channel.FINDINGS in channels and self._retained_findings:
            import asyncio as _asyncio

            loop = _asyncio.get_event_loop()
            for msg in self._retained_findings:
                try:
                    if loop.is_running():
                        loop.create_task(callback(msg))
                    else:
                        _asyncio.run(callback(msg))
                except Exception:
                    pass  # Replay errors don't crash the bus

    def unsubscribe(self, subscriber_id: str) -> None:
        """Remove a subscription."""
        self._subscriptions.pop(subscriber_id, None)

    async def publish(
        self,
        channel: Channel,
        msg_type: MessageType,
        sender: AgentName,
        payload: dict[str, Any],
    ) -> None:
        """Publish a message to a channel.

        This is how agents communicate. The message is enqueued on the
        channel's queue and dispatched to all subscribers asynchronously.
        """
        msg = BusMessage(
            channel=channel,
            msg_type=msg_type,
            sender=sender,
            payload=payload,
        )

        # Store in history for scrollback
        self._history.append(msg)
        if len(self._history) > self._max_history:
            self._history = self._history[-self._max_history:]

        # Retain FINDING messages for late subscribers (D4 fix)
        if channel == Channel.FINDINGS and msg_type == MessageType.FINDING:
            self._retained_findings.append(msg)

        # Update agent state cache if this is a status message
        if channel == Channel.STATUS and "state" in payload:
            state_str = payload.get("state", "")
            try:
                self._agent_states[sender] = AgentState(state_str)
            except ValueError:
                pass

        await self._queues[channel].put(msg)

    # ── Convenience publish methods for each message type ──

    async def publish_status(
        self,
        agent: AgentName,
        state: AgentState,
        detail: str = "",
        **extra: Any,
    ) -> None:
        """Publish a status change — broadcast to all subscribers of STATUS."""
        payload: dict[str, Any] = {
            "agent": agent.value,
            "state": state.value,
            "detail": detail,
        }
        payload.update(extra)
        await self.publish(Channel.STATUS, MessageType.STATUS, agent, payload)

    async def publish_finding(
        self,
        agent: AgentName,
        finding: KeyFinding,
    ) -> None:
        """Publish a completed finding — consumed by Synthesis Lead, Fact Checker, TUI."""
        await self.publish(
            Channel.FINDINGS,
            MessageType.FINDING,
            agent,
            {"agent": agent.value, "finding": finding},
        )

    async def publish_request(
        self,
        from_agent: AgentName,
        to_agent: AgentName,
        request_type: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Publish a direct request from one agent to another.

        Example: Financial Analyst requests Market Analyst's TAM number
        before building the DCF model. (§4.8)
        """
        await self.publish(
            Channel.REQUESTS,
            MessageType.REQUEST,
            from_agent,
            {
                "from_agent": from_agent.value,
                "to_agent": to_agent.value,
                "request_type": request_type,
                "context": context or {},
            },
        )

    async def publish_handoff(
        self,
        from_agent: AgentName,
        to_agent: AgentName,
        task: str,
        context_bundle: dict[str, Any] | None = None,
    ) -> None:
        """Publish a task handoff from one agent to another.

        Example: Engagement Director hands off a sub-task to a specialist.
        (§4.8)
        """
        await self.publish(
            Channel.HANDOFF,
            MessageType.HANDOFF,
            from_agent,
            {
                "from_agent": from_agent.value,
                "to_agent": to_agent.value,
                "task": task,
                "context_bundle": context_bundle or {},
            },
        )

    async def publish_escalation(
        self,
        agent: AgentName,
        issue: str,
        suggested_action: str = "",
    ) -> None:
        """Publish an escalation to the Engagement Director.

        Example: Regulatory Analyst finds an unexpected compliance barrier
        and escalates so the Director can reroute the DAG. (§4.8, §10.2)
        """
        await self.publish(
            Channel.ESCALATION,
            MessageType.ESCALATION,
            agent,
            {
                "agent": agent.value,
                "issue": issue,
                "suggested_action": suggested_action,
            },
        )

    # ── Query methods for TUI and debugging ──

    def get_agent_states(self) -> dict[AgentName, AgentState]:
        """Get current state of all agents — for TUI agent grid (§8.5)."""
        return dict(self._agent_states)

    def get_history(
        self,
        channel: Channel | None = None,
        limit: int = 100,
    ) -> list[BusMessage]:
        """Get message history — for TUI scrollback and debugging."""
        msgs = self._history
        if channel is not None:
            msgs = [m for m in msgs if m.channel == channel]
        return msgs[-limit:]

    def get_findings_count(self, agent: AgentName | None = None) -> int:
        """Count published findings — for TUI findings count display (§8.5)."""
        count = 0
        for msg in self._history:
            if msg.channel != Channel.FINDINGS:
                continue
            if agent is not None and msg.sender != agent:
                continue
            count += 1
        return count

    def get_retained_findings(self) -> list[BusMessage]:
        """Get all retained FINDING messages — for explicit handoff.

        Used by the orchestrator to inject findings into agents that
        were instantiated after specialists published (D4 fix).
        """
        return list(self._retained_findings)

    def clear_retained_findings(self) -> None:
        """Clear retained findings — called at the start of a new engagement."""
        self._retained_findings.clear()


# ─────────────────────────────────────────────────────────────────────────────
# Singleton access
# ─────────────────────────────────────────────────────────────────────────────

_bus: AgentBus | None = None


def get_bus() -> AgentBus:
    """Get the singleton AgentBus instance."""
    global _bus
    if _bus is None:
        _bus = AgentBus()
    return _bus


def reset_bus() -> None:
    """Reset the singleton — useful for testing."""
    global _bus
    _bus = None
