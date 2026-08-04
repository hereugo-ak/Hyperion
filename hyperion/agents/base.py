"""
HYPERION BaseAgent, the foundation class for all 20 agents.

This is NOT a generic agent base class. It is the contract that every
HYPERION agent, from the Engagement Director to the Render Engine, must fulfill. Every agent has:

- Identity: AgentName + AgentRole (who they are)
- Intelligence: ModelTier (what level they operate at)
- Tools: ToolName list (what they can actually use, not decorative)
- Skills: SkillSpec list (proprietary analytical frameworks)
- System prompt: their expertise, voice, methodology (not generic)
- AgentBus subscription: for inter-agent communication (§4.8)
- Runtime state: AgentRuntimeState for TUI display (§8.5)
- Structured output: produces Pydantic models, not free text (§0.1)

The BaseAgent provides:
1. Bus integration, publish findings, status, escalations, handoffs
2. Router integration, request LLM completions by tier (agents don't know providers)
3. Tool access, lazy-initialized tool instances, only available if in spec
4. Sub-agent spawning, delegates to SubAgentRunner with 5-min timeout
5. State management, transitions published to bus for TUI display
6. Error handling, BLOCKED state on failure, escalation to Director

Agents override `run()` with their proprietary methodology.
The `run()` method is where the agent's skills are applied.
(§4.1, §0.1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

from hyperion.agents.bus import AgentBus, Channel, MessageType, get_bus
from hyperion.agents.prompt_contract import compose_agent_prompt
from hyperion.config import TIER_OUTPUT_BUDGET, ModelTier, get_settings
from hyperion.router.budget import TaskUrgency
from hyperion.router.providers.base import RouterResponse
from hyperion.router.router import LLMRouter, get_router
from hyperion.router.structured_validator import extract_json
from hyperion.schemas.agents import (
    AgentName,
    AgentRole,
    AgentRuntimeState,
    AgentSpec,
    AgentState,
    SkillSpec,
    SubAgentSpec,
    ToolName,
)
from hyperion.schemas.models import KeyFinding

logger = logging.getLogger(__name__)

# Type variable for structured output models
T = TypeVar("T", bound=BaseModel)

# Retry budget for a single sub-agent that times out or returns only
# research_gap findings (see _spawn_sub_agent). Bounded so a persistently
# empty question cannot loop forever; the orchestrator reframer (L3) is the
# backstop above this. The tier bump on retry gives a larger context window
# and a stronger analyzer without touching the parent specialist's own
# synthesis depth.
SUB_AGENT_MAX_RETRIES = 2


class BaseAgent(ABC):
    """The foundation class for all HYPERION agents.

    Every agent in HYPERION extends this class. The base provides:
    - Bus integration (publish/subscribe per §4.8)
    - Router integration (tier-based LLM calls per §3.1)
    - Tool access (only tools in the agent's spec)
    - Sub-agent spawning (with 5-min timeout per §4.7)
    - State management (published to bus for TUI per §8.5)
    - Structured output (Pydantic models, not free text per §0.1)

    Agents override `run()` with their proprietary methodology.
    The system prompt is loaded from the AgentSpec, it is the agent's
    expertise, voice, and methodology, not a generic instruction.

    This class is NOT instantiable directly, it is abstract.
    Each of the 20 agents has its own class with a specific spec
    and a run() method that applies that agent's proprietary skills.
    """

    def __init__(
        self,
        spec: AgentSpec,
        bus: AgentBus | None = None,
        router: LLMRouter | None = None,
    ) -> None:
        self.spec = spec
        self.bus = bus or get_bus()
        self.router = router or get_router()
        self.settings = get_settings()

        # Runtime state, published to bus for TUI agent grid (§8.5)
        self.state = AgentRuntimeState(
            agent_name=spec.name,
            state=AgentState.IDLE,
            model_tier=spec.model_tier,
            last_state_change=time.time(),
        )

        # Tool instances, lazy initialized, only for tools in spec
        self._tools: dict[ToolName, Any] = {}

        # Findings collected by this agent
        self._findings: list[KeyFinding] = []

        # P2-17: verify_claims requests from the Fact Checker, recorded for
        # the agent's next run() (or the GAP_CLOSURE re-dispatch) to act on.
        self._pending_verify_requests: list[dict[str, Any]] = []

        # Sub-agent specs spawned by this agent
        self._sub_agent_specs: list[SubAgentSpec] = []

        # Issue texts already escalated, for deduplication. See _escalate():
        # each escalation costs the Director a STRONG-tier LLM call, so a loop
        # emitting the same issue repeatedly must not be allowed to storm it.
        self._escalated_issues: set[str] = set()

        # Subscription ID for bus
        self._sub_id = f"agent_{spec.name.value}"

    # ─────────────────────────────────────────────────────────────────────
    # Identity
    # ─────────────────────────────────────────────────────────────────────

    @property
    def name(self) -> AgentName:
        return self.spec.name

    @property
    def role(self) -> AgentRole:
        return self.spec.role

    @property
    def display_name(self) -> str:
        return self.spec.display_name

    @property
    def model_tier(self) -> ModelTier:
        return self.spec.model_tier

    @property
    def skills(self) -> list[SkillSpec]:
        return self.spec.skills

    @property
    def tools(self) -> list[ToolName]:
        return self.spec.tools

    @property
    def max_sub_agents(self) -> int:
        return self.spec.max_sub_agents

    @property
    def system_prompt(self) -> str:
        return self.spec.system_prompt

    # ─────────────────────────────────────────────────────────────────────
    # Context Enrichment, MICRO LLM classifier (P7 GAP-2)
    # Replaces brittle regex keyword matching with a fast LLM classification
    # call that returns structured intent: industry, geography, sector,
    # technology, company, etc.  Falls back to regex if the LLM call fails.
    # ─────────────────────────────────────────────────────────────────────

    _ENRICH_CLASSIFIER_PROMPT = (
        "You are a fast entity classifier. Extract structured intent from the "
        "user's business research question. Return ONLY a JSON object with these "
        "keys (omit any that don't apply):\n"
        "  \"geography\": country/region mentioned (e.g. \"US\", \"EU\", \"India\")\n"
        "  \"jurisdiction\": primary regulatory jurisdiction\n"
        "  \"industry\": the industry or sector (e.g. \"fintech\", \"healthcare\")\n"
        "  \"sector\": same as industry if applicable\n"
        "  \"space\": the market space or domain\n"
        "  \"technology\": specific technology if mentioned (e.g. \"kubernetes\", \"AI\")\n"
        "  \"company\": named company if mentioned\n"
        "  \"segment\": market segment if mentioned\n"
        "  \"business_model\": business model if mentioned\n"
        "  \"stakeholder_audience\": primary audience if mentioned\n"
        "  \"acquirer\": acquiring company if M&A question\n"
        "  \"size_range\": deal/company size range if mentioned\n"
        "  \"tickers\": list of stock tickers if mentioned\n"
        "  \"value_drivers\": list of key value drivers if mentioned\n"
        "  \"vendors\": list of vendor names if mentioned\n"
        "  \"process_type\": operational process type if mentioned\n"
        "  \"architecture_description\": architecture if described\n"
        "  \"use_case\": specific use case if mentioned\n\n"
        "Question: {question}\n\n"
        "Return JSON:"
    )

    async def _enrich_context(self, question: str) -> dict[str, Any]:
        """Extract industry, geography, sector, etc. from the question string.

        P7 GAP-2: Uses a MICRO tier LLM call to classify the question into
        structured intent fields. Falls back to regex keyword matching if
        the LLM call fails or the router is unavailable.

        Specialists expect context keys like 'industry', 'geography', 'space',
        'technology', 'company', but the orchestrator only passes prior agent
        outputs keyed by agent name. This method populates those keys so
        search queries are never empty.
        """
        # Try MICRO LLM classifier first
        try:
            ctx = await self._enrich_context_llm(question)
            if ctx:
                return ctx
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_enrich_context", exc)

        # Fallback: regex keyword matching (original implementation)
        return self._enrich_context_regex(question)

    async def _enrich_context_llm(self, question: str) -> dict[str, Any]:
        """MICRO LLM classifier for context enrichment (P7 GAP-2)."""
        import json

        prompt = self._ENRICH_CLASSIFIER_PROMPT.format(question=question[:500])
        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.LOW,
            temperature=0.1,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return {}

        parsed = json.loads(response.content)
        if not isinstance(parsed, dict):
            logger.warning("Context classifier returned non-object JSON")
            return {}
        data: dict[str, Any] = {str(key): value for key, value in parsed.items()}
        # Normalize: ensure geography-derived fields are consistent
        geo = data.get("geography")
        if geo and "jurisdiction" not in data:
            data["jurisdiction"] = geo
        if geo and "jurisdictions" not in data:
            data["jurisdictions"] = [geo]
        # Ensure sector/space/industry consistency
        industry = data.get("industry")
        if industry:
            data.setdefault("sector", industry)
            data.setdefault("space", industry)
        return data

    @staticmethod
    def _enrich_context_regex(question: str) -> dict[str, Any]:
        """Regex fallback for context enrichment (original implementation)."""
        import re

        ctx: dict[str, Any] = {}
        q_lower = question.lower()

        # Geography detection
        geos = ["us", "usa", "united states", "eu", "europe", "uk", "india", "china",
                "japan", "germany", "france", "brazil", "canada", "australia",
                "singapore", "middle east", "africa", "asia pacific", "latam"]
        found_geos = [g for g in geos if g in q_lower]
        if found_geos:
            ctx["geography"] = found_geos[0].upper() if found_geos[0] in ("us", "eu", "uk") else found_geos[0].title()
            ctx["jurisdiction"] = ctx["geography"]
            ctx["jurisdictions"] = [ctx["geography"]]

        # Industry/sector detection, common industries
        industries = [
            "saas", "fintech", "healthcare", "biotech", "pharmaceutical",
            "automotive", "retail", "e-commerce", "ecommerce", "logistics",
            "education", "edtech", "real estate", "proptech", "agriculture",
            "energy", "manufacturing", "telecommunications", "media",
            "entertainment", "gaming", "travel", "hospitality", "food",
            "construction", "aerospace", "defense", "banking", "insurance",
            "cybersecurity", "ai", "artificial intelligence", "blockchain",
            "cryptocurrency", "cloud computing", "semiconductor", "robotics",
        ]
        found_industries = [ind for ind in industries if ind in q_lower]
        if found_industries:
            ctx["industry"] = found_industries[0]
            ctx["sector"] = found_industries[0]
            ctx["space"] = found_industries[0]

        # Technology detection
        techs = ["kotlin", "rust", "python", "react", "kubernetes", "docker",
                 "aws", "azure", "gcp", "mongodb", "postgresql", "redis"]
        found_techs = [t for t in techs if t in q_lower]
        if found_techs:
            ctx["technology"] = found_techs[0]
            ctx["technology_category"] = found_techs[0]

        # Company detection, look for capitalized words near "company" or "startup"
        company_match = re.search(r'(?:company|startup|firm|corporation|inc|ltd)\s+([A-Z][a-zA-Z]+)', question)
        if company_match:
            ctx["company"] = company_match.group(1)

        return ctx

    # ─────────────────────────────────────────────────────────────────────
    # State Management, published to bus for TUI (§8.5)
    # ─────────────────────────────────────────────────────────────────────

    async def _transition(self, new_state: AgentState, detail: str = "") -> None:
        """Transition to a new state and publish to bus.

        The TUI agent grid (§8.5) updates within 100ms of this publish.
        States: IDLE → WORKING → WAITING → DONE / BLOCKED
        """
        self.state.transition_to(new_state, detail)
        await self.bus.publish_status(
            agent=self.name,
            state=new_state,
            detail=detail,
            tools=[t.value for t in self.state.active_tools],
            findings_count=self.state.findings_count,
            sub_agents=self.state.sub_agents_active,
        )

    async def _set_active_tools(self, tools: list[ToolName]) -> None:
        """Update which tools are currently active, for TUI display."""
        self.state.active_tools = tools
        await self.bus.publish_status(
            agent=self.name,
            state=self.state.state,
            detail=self.state.detail,
            tools=[t.value for t in tools],
            findings_count=self.state.findings_count,
            sub_agents=self.state.sub_agents_active,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Bus Integration (§4.8)
    # ─────────────────────────────────────────────────────────────────────

    def subscribe_to_bus(self) -> None:
        """Subscribe to appropriate bus channels based on agent role.

        Subscription patterns (§4.8):
        - Core (Engagement Director): ALL channels (omniscient)
        - Specialists: findings + requests (need-aware)
        - Support: findings (Fact Checker needs all findings)
        - Delivery: findings (need final report content)
        """
        if self.role == AgentRole.CORE:
            channels = {Channel.STATUS, Channel.FINDINGS, Channel.REQUESTS,
                       Channel.ESCALATION, Channel.HANDOFF, Channel.TUI}
        elif self.role == AgentRole.SPECIALIST:
            channels = {Channel.FINDINGS, Channel.REQUESTS}
        elif self.role == AgentRole.SUPPORT:
            channels = {Channel.FINDINGS}
        elif self.role == AgentRole.DELIVERY:
            # D4-rest: Delivery needs HANDOFF (receives FinalReport from Synthesis)
            # and FINDINGS (receives viz output, layout plan from other delivery agents)
            channels = {Channel.FINDINGS, Channel.HANDOFF}
        else:
            channels = {Channel.FINDINGS}

        self.bus.subscribe(
            subscriber_id=self._sub_id,
            agent=self.name,
            channels=channels,
            callback=self._handle_bus_message,
        )

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        Override in subclasses for agent-specific message handling.
        The base implementation is a deliberate, documented no-op: most
        agents drain the bus inside their own ``run()`` loop and do not
        need a push callback. It is NOT abstract (ruff B027 would
        otherwise flag the empty body) because forcing all 20 agents to
        implement a method they do not use would be pure ceremony.

        Subclasses that DO care about push delivery override this.
        """
        # P2-17: a verify_claims request addressed to this agent is handled
        # by EVERY agent via the shared base handler, it is the Fact
        # Checker's Step 6 feedback path, and it must never be dropped.
        payload = getattr(msg, "payload", None) or {}
        if (
            getattr(msg, "channel", None) == Channel.REQUESTS
            and payload.get("request_type") == "verify_claims"
            and payload.get("to_agent", "") == self.name.value
        ):
            await self._handle_verify_claims(payload)
            return

        # Traced rather than silently dropped: a message arriving here means
        # the subscribing agent declared interest in a channel but has no
        # handler, which is nearly always a wiring bug (§4.8).
        logger.debug(
            "%s received bus message with no handler override (msg=%r), dropping",
            self.name,
            getattr(msg, "message_type", msg),
        )

    async def _handle_verify_claims(self, payload: dict[str, Any]) -> None:
        """Shared handler for the Fact Checker's verify_claims requests (P2-17).

        The request is recorded for the agent's next run(), or the
        GAP_CLOSURE re-dispatch (P2-18), to act on, and acknowledged on the
        bus so the Fact Checker knows a live specialist received it. Before
        this handler existed, every specialist matched request_type against
        its own literals (tam_number, moat_assessment, ...) and the
        verify_claims request vanished silently.
        """
        claims = payload.get("unverified_claims", [])
        self._pending_verify_requests.append(payload)
        logger.info(
            "%s: verify_claims request from %s recorded (%d claim(s) flagged)",
            self.name.value, payload.get("from_agent", "fact_checker"), len(claims),
        )
        await self.bus.publish(
            channel=Channel.REQUESTS,
            msg_type=MessageType.STATUS,
            sender=self.name,
            payload={
                "to_agent": payload.get("from_agent", "fact_checker"),
                "from_agent": self.name.value,
                "request_type": "verify_claims_ack",
                "acknowledged_claims": len(claims),
            },
        )

    async def _publish_finding(self, finding: KeyFinding) -> None:
        """Publish a completed finding to the bus.

        Other agents consume findings via their subscriptions.
        The Fact Checker verifies all findings.
        The Synthesis Lead collects all findings for reconciliation.
        The TUI displays findings in the findings stream (§8.7).
        """
        self._findings.append(finding)
        self.state.findings_count = len(self._findings)
        await self.bus.publish_finding(self.name, finding)

    async def _log_tool_use(
        self,
        tool: str,
        action: str,
        detail: str = "",
        success: bool | None = None,
    ) -> None:
        """Publish a tool-use event to the TUI channel for live display.

        This is NOT a bus channel for agent communication, it's a one-way
        telemetry feed the TUI subscribes to so the user can see exactly
        what each agent is doing in real time (§8.7 findings stream).

        Args:
            tool: Tool name (e.g. "searxng", "jina", "fred")
            action: What the tool is doing (e.g. "search", "extract", "pull_series")
            detail: Human-readable detail (e.g. "12 results for 'EV market size'")
            success: True=success, False=failure, None=in-progress (default)
        """
        await self.bus.publish(
            channel=Channel.TUI,
            msg_type=MessageType.STATUS,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "tool": tool,
                "action": action,
                "detail": detail,
                "success": success,
            },
        )

    def _log(self, message: str) -> None:
        """Publish a diagnostic log line to the TUI. Synchronous, never raises.

        CRITICAL: several agents (notably RenderEngine) call `self._log()` from
        inside `except` blocks. Before this method existed, that call raised
        AttributeError *while handling another error*, masking the real failure
        and aborting the delivery stage, which is exactly how an engagement
        could finish with a stray `report.css` and no PDF. This is therefore a
        deliberately bulletproof, non-async, exception-swallowing shim: logging
        must NEVER be able to break the pipeline.
        """
        try:
            import asyncio

            coro = self.bus.publish(
                channel=Channel.TUI,
                msg_type=MessageType.STATUS,
                sender=self.name,
                payload={
                    "agent": self.name.value,
                    "tool": "system.log",
                    "action": message[:400],
                    "detail": "",
                    "success": None,
                },
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                # No running loop, nothing is listening; drop the message.
                coro.close()
                return
            task = loop.create_task(coro)
            # Prevent "Task exception was never retrieved" noise.
            task.add_done_callback(lambda t: t.exception() if not t.cancelled() else None)
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_log", exc)

    async def _publish_findings(self, findings: list[KeyFinding]) -> None:
        """Publish multiple findings."""
        for finding in findings:
            await self._publish_finding(finding)

    async def _escalate(self, issue: str, suggested_action: str = "") -> None:
        """Escalate an issue to the Engagement Director.

        The Director receives escalations and can:
        - Spawn new agents mid-engagement (adaptive replanning, §10.2)
        - Reroute tasks if an agent fails
        - Reallocate model tiers if budget is running low

        DEDUPLICATED. Every escalation costs the Director a STRONG-tier LLM
        call to evaluate. An agent in a loop (e.g. Synthesis Lead iterating
        over N contradictions, each hitting the same sub-agent cap) used to
        emit the identical escalation N times, and each one triggered a fresh
        Director evaluation, an escalation storm that burned STRONG quota and
        wall-clock time while producing zero new information. The same issue
        text from the same agent is therefore only escalated once per
        engagement; repeats are logged and dropped.
        """
        key = issue.strip().lower()[:160]
        if key in self._escalated_issues:
            self._log(
                f"ESCALATION suppressed (duplicate, already reported): {issue[:120]}"
            )
            return
        self._escalated_issues.add(key)

        await self.bus.publish_escalation(
            agent=self.name,
            issue=issue,
            suggested_action=suggested_action,
        )
        await self._transition(AgentState.BLOCKED, f"Escalated: {issue}")

    async def _request_from_agent(
        self,
        to_agent: AgentName,
        request_type: str,
        context: dict[str, Any] | None = None,
    ) -> None:
        """Request data or context from another agent.

        Example: Financial Analyst requests Market Analyst's TAM number
        before building the DCF model. (§4.8)
        """
        await self.bus.publish_request(
            from_agent=self.name,
            to_agent=to_agent,
            request_type=request_type,
            context=context,
        )

    async def _handoff_to_agent(
        self,
        to_agent: AgentName,
        task: str,
        context_bundle: dict[str, Any] | None = None,
    ) -> None:
        """Hand off a task to another agent.

        Example: Engagement Director hands off a sub-task to a specialist.
        (§4.8)
        """
        await self.bus.publish_handoff(
            from_agent=self.name,
            to_agent=to_agent,
            task=task,
            context_bundle=context_bundle,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Router Integration (§3.1), agents don't know providers
    # ─────────────────────────────────────────────────────────────────────

    async def _llm_complete(
        self,
        user_prompt: str,
        system_prompt_override: str | None = None,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        response_format: dict[str, str] | None = None,
        conversation_history: list[dict[str, str]] | None = None,
        tier: ModelTier | None = None,
    ) -> RouterResponse:
        """Request an LLM completion at this agent's model tier.

        Agents don't know which provider they're using, they request a
        tier and the router decides. This is the core abstraction that
        decouples agent intelligence from infrastructure. (§9)

        The agent's system prompt is always prepended. If
        system_prompt_override is provided, it replaces the default.
        """
        # W-16 (extends P2-32): the shared, versioned agent contract is
        # prepended to EVERY dispatched prompt (base prompt and overrides
        # alike). Clause 8 subsumes the old PROMPT_TYPOGRAPHY_RULE, so all
        # eight quality clauses (subject fit / abstain / no fabrication /
        # evidence binding / units / uncertainty / conflict / typography)
        # are stated once here, not 20 times across specs.
        base_prompt = system_prompt_override or self.system_prompt
        system = compose_agent_prompt(base_prompt)

        # Allow a single call to borrow a higher (or lower) tier than the
        # agent's own — e.g. the Competitive Intelligence agent runs on
        # STANDARD but borrows STRONG once for the competitor-naming decision.
        resolved_tier = tier or self.model_tier

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_prompt})

        await self._transition(
            AgentState.WAITING,
            f"Requesting {resolved_tier.value} tier completion",
        )

        # D-17: every agent call owns an explicit output ceiling. Leaving this
        # as None delegates length to provider defaults, which are often only a
        # few hundred tokens and silently cap substantive analysis.
        resolved_max_tokens = max_tokens or TIER_OUTPUT_BUDGET.get(resolved_tier, 4_000)
        if resolved_max_tokens <= 0:
            resolved_max_tokens = 4_000

        response = await self.router.complete(
            tier=resolved_tier,
            messages=messages,
            agent_name=self.name.value,
            urgency=urgency,
            temperature=temperature,
            max_tokens=resolved_max_tokens,
            response_format=response_format,
        )

        # Publish LLM call telemetry to TUI
        try:
            model_name = getattr(response, "model", "unknown")
            provider_val = getattr(response, "provider", "unknown")
            provider_name = provider_val.value if hasattr(provider_val, "value") else str(provider_val)
            await self.bus.publish(
                channel=Channel.TUI,
                msg_type=MessageType.STATUS,
                sender=self.name,
                payload={
                    "agent": self.name.value,
                    "tool": "llm",
                    "action": f"{provider_name}/{model_name}",
                    "detail": f"{self.model_tier.value} tier · {'OK' if response.success else 'FAIL'} · {len(response.content or '')} chars",
                    "success": response.success,
                    "provider": provider_name,
                    "input_tokens": max(0, int(getattr(response, "input_tokens", 0) or 0)),
                    "output_tokens": max(0, int(getattr(response, "output_tokens", 0) or 0)),
                    "total_tokens": max(0, int(getattr(response, "total_tokens", 0) or 0)),
                },
            )
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.warning("%s: %s", "_llm_complete", exc)

        if not response.success:
            await self._transition(
                AgentState.BLOCKED,
                f"LLM completion failed: {response.error}",
            )
            # Escalate so the Director can reroute
            await self._escalate(
                issue=f"LLM completion failed at {self.model_tier.value} tier: {response.error}",
                suggested_action="Reroute to adjacent tier or retry with different provider",
            )

        # Normalize JSON responses so `json.loads(response.content)` at the
        # ~72 downstream call sites cannot fail on a wrapper.
        #
        # Phase 5.1e: the previous version of this block only acted when
        # `content.strip().startswith("```")`. Measured against the shapes real
        # providers return, that gate missed 4 of 6:
        #
        #   fenced                    -> handled
        #   bare fence (no language)  -> handled
        #   prose THEN fence          -> MISSED   "Sure!\n```json\n{...}\n```"
        #   prose prefix, no fence    -> MISSED   "Here is the analysis:\n{...}"
        #   prose suffix              -> MISSED   "{...}\nHope that helps!"
        #   trailing commentary       -> MISSED   "{...} - note the caveat."
        #
        # Every miss lands on `except (json.JSONDecodeError, ...): return
        # SomeModel()`, so the agent returns a structurally-valid but EMPTY
        # framework and reports success. That is the §0.3 anti-pattern at the
        # scale of every specialist: a Porter's Five Forces with no forces, a
        # VRIO with no resources, a claim list with no claims.
        #
        # It is also not conditional on `response_format` any more. 5 of the 78
        # `_llm_complete` call sites omit that kwarg yet still json.loads the
        # result, and several providers ignore the field entirely, so keying
        # the repair off the *request* rather than the *response* was wrong.
        # Normalization is now attempted whenever the body looks like it
        # contains JSON, and is a strict no-op otherwise.
        if response.success and response.content:
            response.content = self._normalize_json_content(response.content)

        return response

    @staticmethod
    def _normalize_json_content(content: str) -> str:
        """Return `content` reduced to its JSON payload, when it has one.

        Conservative by construction: the extracted candidate must itself
        parse as JSON before it replaces the original. If extraction finds
        nothing, or finds something that does not parse, the original string
        is returned untouched so a non-JSON completion (prose, markdown,
        a drafted section) passes through unharmed.
        """
        stripped = content.strip()
        if not stripped:
            return content

        # Fast path: already clean JSON. Avoids doing any work for the
        # overwhelmingly common case.
        if stripped[:1] in ("{", "["):
            try:
                json.loads(stripped)
                return stripped
            except (json.JSONDecodeError, TypeError):
                pass  # falls through to extraction; may be fenced-and-nested

        # Only bother if there is plausibly a JSON payload in there. This keeps
        # free-text completions (which are the majority of non-JSON calls) from
        # being scanned at all.
        if "{" not in stripped and "[" not in stripped:
            return content

        candidate = extract_json(stripped)
        if candidate is None:
            return content

        try:
            json.loads(candidate)
        except (json.JSONDecodeError, TypeError):
            # Extraction produced something unparseable, return the original
            # so the caller's own error path sees the true response, not a
            # fragment we invented.
            return content

        if candidate != stripped:
            logger.debug(
                "normalized wrapped JSON response: %d chars -> %d chars",
                len(stripped),
                len(candidate),
            )
        return candidate

    async def _llm_complete_structured(
        self,
        user_prompt: str,
        output_model: type[T],
        system_prompt_override: str | None = None,
        urgency: TaskUrgency = TaskUrgency.NORMAL,
        temperature: float = 0.3,
        conversation_history: list[dict[str, str]] | None = None,
    ) -> T | None:
        """Request a structured LLM completion that returns a Pydantic model.

        Every agent produces structured output, not free text (§0.1).
        This method uses the router's response and parses it into the
        specified Pydantic model. If parsing fails, returns None and
        escalates.

        The temperature is lower (0.3) for structured output to reduce
        randomness, we want deterministic, typed results.
        """
        import json

        response = await self._llm_complete(
            user_prompt=user_prompt,
            system_prompt_override=system_prompt_override,
            urgency=urgency,
            temperature=temperature,
            response_format={"type": "json_object"},
            conversation_history=conversation_history,
        )

        if not response.success or not response.content:
            return None

        try:
            from hyperion.router.structured_validator import extract_json
            json_str = extract_json(response.content)
            if json_str is None:
                raise json.JSONDecodeError("No JSON found in response", response.content, 0)
            data = json.loads(json_str)
            return output_model.model_validate(data)
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            await self._escalate(
                issue=f"Structured output parsing failed: {e}",
                suggested_action="Retry with explicit JSON instruction in prompt",
            )
            return None

    # ─────────────────────────────────────────────────────────────────────
    # Tool Access (§5.1), only tools in the agent's spec
    # ─────────────────────────────────────────────────────────────────────

    def get_tool(self, tool: ToolName) -> Any:
        """Get a tool instance, but only if it's in this agent's spec.

        No decorative tools. No agent has a tool it doesn't use.
        If the tool is not in the agent's spec, raises ValueError.
        (§5.1, §12.3)
        """
        if not self.spec.has_tool(tool):
            raise ValueError(
                f"Agent {self.name.value} does not have access to tool {tool.value}. "
                f"Tools available: {[t.value for t in self.spec.tools]}"
            )

        if tool not in self._tools:
            self._tools[tool] = self._instantiate_tool(tool)

        # Emit a hidden, countable event at the moment an agent begins using a
        # tool. Most tool clients do not know about AgentBus, so relying on
        # hand-written success logs left the live TUI counter at zero for the
        # majority of real retrieval calls.
        self._publish_tool_access(tool)
        return self._tools[tool]

    def _publish_tool_access(self, tool: ToolName) -> None:
        """Publish one non-blocking tool-access event for live telemetry."""
        try:
            coro = self.bus.publish(
                channel=Channel.TUI,
                msg_type=MessageType.STATUS,
                sender=self.name,
                payload={
                    "agent": self.name.value,
                    "tool": tool.value,
                    "action": "access",
                    "detail": "",
                    "success": None,
                    "telemetry_kind": "tool_call",
                    "display": False,
                },
            )
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                coro.close()
                return
            task = loop.create_task(coro)
            task.add_done_callback(
                lambda done: done.exception() if not done.cancelled() else None
            )
        except Exception as exc:  # noqa: BLE001 - telemetry cannot break tool access
            logger.debug("tool telemetry failed for %s: %s", tool.value, exc)

    async def get_tool_or_escalate(self, tool: ToolName) -> Any | None:
        """D4-rest: Get a tool, escalating on failure instead of raising.

        If the tool is unavailable (not in spec, or instantiation fails),
        publishes an ESCALATION so the director can adapt. Returns None
        on failure, callers must check and degrade gracefully.
        """
        try:
            return self.get_tool(tool)
        except (ValueError, RuntimeError, ImportError) as e:
            await self._escalate(
                issue=f"Tool {tool.value} unavailable: {e!s:.200}",
                suggested_action="Degrade gracefully or reroute to alternative tool",
            )
            return None

    def _instantiate_tool(self, tool: ToolName) -> Any:
        """Instantiate a tool by name.

        Tools are imported lazily to avoid circular imports and to keep
        startup fast. Each tool is a singleton within the agent.
        """
        # Tool imports are deferred to avoid circular dependencies
        # The tools/ layer will be built next, but the agent layer
        # must be structured to accept them.
        if tool == ToolName.SEARXNG:
            from hyperion.tools.searxng import SearxNGClient
            return SearxNGClient(settings=self.settings)
        elif tool == ToolName.JINA:
            from hyperion.tools.jina import JinaClient
            return JinaClient(settings=self.settings)
        elif tool == ToolName.OBSCURA:
            from hyperion.tools.obscura import ObscuraClient
            return ObscuraClient(settings=self.settings)
        elif tool == ToolName.SCRAPLING:
            from hyperion.tools.scrapling import ScraplingClient
            return ScraplingClient(settings=self.settings)
        elif tool == ToolName.CRAWL4AI:
            from hyperion.tools.crawl4ai import Crawl4AIClient
            return Crawl4AIClient(settings=self.settings)
        elif tool == ToolName.FLARESOLVERR:
            from hyperion.tools.flaresolverr import FlareSolverrClient
            solver_url = getattr(self.settings, "flaresolverr_url", "http://localhost:8191/v1") if self.settings else "http://localhost:8191/v1"
            return FlareSolverrClient(solver_url=solver_url)
        elif tool == ToolName.WAYBACK:
            from hyperion.tools.wayback import WaybackClient
            return WaybackClient(settings=self.settings)
        elif tool == ToolName.ALPHA_VANTAGE:
            from hyperion.tools.alpha_vantage import AlphaVantageClient
            return AlphaVantageClient(settings=self.settings)
        elif tool == ToolName.FRED:
            from hyperion.tools.fred import FredClient
            return FredClient(settings=self.settings)
        elif tool == ToolName.UNSPLASH:
            from hyperion.tools.unsplash import UnsplashClient
            return UnsplashClient(settings=self.settings)
        elif tool == ToolName.SECOND_BRAIN:
            from hyperion.tools.second_brain import SecondBrainClient
            return SecondBrainClient(settings=self.settings)
        elif tool == ToolName.DEEP_SEARCH:
            from hyperion.tools.deep_search import DeepSearchClient
            return DeepSearchClient(settings=self.settings)
        elif tool == ToolName.SEC_EDGAR:
            from hyperion.tools.sec_edgar import SECEdgarClient
            return SECEdgarClient(settings=self.settings)
        elif tool == ToolName.SEMANTIC_SCHOLAR:
            from hyperion.tools.semantic_scholar import SemanticScholarClient
            return SemanticScholarClient(settings=self.settings)
        elif tool == ToolName.OPEN_ALEX:
            from hyperion.tools.openalex import OpenAlexClient
            return OpenAlexClient(settings=self.settings)
        elif tool == ToolName.WORLD_BANK:
            from hyperion.tools.world_bank import WorldBankClient
            return WorldBankClient(settings=self.settings)
        elif tool == ToolName.GOOGLE_TRENDS:
            from hyperion.tools.google_trends import GoogleTrendsClient
            return GoogleTrendsClient(settings=self.settings)
        elif tool == ToolName.HACKERNEWS:
            from hyperion.tools.hackernews import HackerNewsClient
            return HackerNewsClient(settings=self.settings)
        elif tool == ToolName.REDDIT:
            from hyperion.tools.reddit import RedditClient
            return RedditClient(settings=self.settings)
        elif tool == ToolName.PLOTLY:
            from hyperion.output.charts import ChartGenerator
            return ChartGenerator(settings=self.settings)
        elif tool == ToolName.WEASYPRINT:
            from hyperion.output.render import PDFRenderer
            return PDFRenderer(settings=self.settings)
        elif tool == ToolName.JINJA2:
            from hyperion.output.render import TemplateRenderer
            return TemplateRenderer(settings=self.settings)
        elif tool == ToolName.PILLOW:
            from hyperion.output.images import ImageProcessor
            return ImageProcessor(settings=self.settings)
        else:
            raise ValueError(f"Unknown tool: {tool}")

    # ─────────────────────────────────────────────────────────────────────
    # Sub-Agent Spawning (§4.7)
    # ─────────────────────────────────────────────────────────────────────

    async def _spawn_sub_agent(self, spec: SubAgentSpec) -> list[Any]:
        """Spawn a junior sub-agent for a focused sub-question.

        Sub-agents handle context isolation (§4.7):
        - Max 3 per specialist per engagement
        - STANDARD or higher tier (research needs a large context window)
        - bounded timeout (see spec.timeout_seconds; retried on gap/timeout)
        - Returns structured KeyFinding objects, not free text
        - Cannot spawn their own sub-agents (no recursive spawning)

        The parent specialist receives the sub-agent's findings and
        synthesizes them into its own analysis. The parent's context
        window is used for synthesis, not for raw research.
        """
        from hyperion.agents.sub_agent import SubAgentRunner

        if len(self._sub_agent_specs) >= self.max_sub_agents:
            # Budget exhaustion is a NORMAL, expected outcome of a bounded
            # resource, not an anomaly the Director needs to reason about.
            # This used to call _escalate(), so an agent looping over N items
            # (e.g. Synthesis Lead resolving N contradictions with
            # max_sub_agents=1) fired N escalations, each costing the Director
            # a STRONG-tier LLM evaluation that could only ever conclude
            # "proceed with available findings". That was the escalation storm.
            # The correct behaviour is to log the gap and carry on.
            self._log(
                f"SUB-AGENT budget reached ({len(self._sub_agent_specs)}/"
                f"{self.max_sub_agents}); proceeding without spawning: "
                f"{spec.question[:80]}"
            )
            return []

        # Research sub-agents need the context capacity of STANDARD or higher.
        if spec.model_tier not in (
            ModelTier.STANDARD,
            ModelTier.STRONG,
            ModelTier.DEEP,
        ):
            raise ValueError(
                f"Sub-agent tier must be STANDARD or higher, got {spec.model_tier.value}"
            )

        self._sub_agent_specs.append(spec)
        self.state.sub_agents_spawned += 1
        self.state.sub_agents_active += 1

        await self._transition(
            AgentState.SUB_AGENT_SPAWNED,
            f"Spawned sub-agent: {spec.question[:80]}",
        )

        # Spawn-time observability: confirm the active timeout budget. A
        # deploy that predates the 300->600s bump silently runs at 300s and
        # reproduces the "Sub-agent timed out" field failures, so surface the
        # effective value on every spawn.
        logger.info(
            "Spawning sub-agent (parent=%s, tier=%s, timeout=%ss, tools=%s): %s",
            self.spec.agent.value,
            spec.model_tier.value,
            spec.timeout_seconds,
            ",".join(t.value for t in spec.tools),
            spec.question[:80],
        )

        findings: list[KeyFinding] = []
        attempt_spec = spec
        try:
            for attempt in range(1 + SUB_AGENT_MAX_RETRIES):
                runner = SubAgentRunner(
                    spec=attempt_spec, bus=self.bus, router=self.router
                )
                started = time.monotonic()
                try:
                    result = await asyncio.wait_for(
                        runner.run(),
                        timeout=attempt_spec.timeout_seconds,
                    )
                except TimeoutError:
                    # Timeout is a bounded-resource outcome, not a reason to
                    # spend a STRONG Director call. Preserve one explicit gap
                    # and (below) retry with an escalated strategy.
                    result = [
                        runner.gap_finding(
                            f"timed out after {attempt_spec.timeout_seconds}s",
                            time.monotonic() - started,
                        )
                    ]
                    self._log(f"Sub-agent timed out: {spec.question[:80]}")
                except Exception as exc:  # noqa: BLE001 - isolate junior-agent failure
                    # Parallel specialist gather() calls use return_exceptions.
                    # Letting this escape silently discarded the entire result
                    # and the TUI reported '0 findings'. Convert every failure
                    # into auditable evidence insufficiency at this boundary.
                    result = [
                        runner.gap_finding(
                            f"failed with {type(exc).__name__}: {str(exc)[:160]}",
                            time.monotonic() - started,
                        )
                    ]
                    logger.exception("Sub-agent failed for %r", spec.question[:120])

                # A run that returns only research_gap findings is a
                # non-answer: keep it as the gap but retry with an escalated
                # tier before shipping an empty gap as if it were content.
                real = [
                    f for f in result
                    if getattr(f, "finding_type", None) != "research_gap"
                ]
                if real:
                    findings = result
                    break

                if attempt < SUB_AGENT_MAX_RETRIES:
                    attempt_spec = self._escalate_sub_agent_spec(attempt_spec)
                    self._log(
                        f"Sub-agent retry {attempt + 1}/{SUB_AGENT_MAX_RETRIES} "
                        f"(bump tier -> {attempt_spec.model_tier.value}): "
                        f"{spec.question[:80]}"
                    )
                else:
                    findings = result  # give up with the gap finding
        finally:
            self.state.sub_agents_active = max(0, self.state.sub_agents_active - 1)

        await self._transition(
            AgentState.WORKING,
            f"Sub-agent returned {len(findings)} findings"
            + (
                f" after {SUB_AGENT_MAX_RETRIES} retries"
                if attempt_spec is not spec
                else ""
            ),
        )

        return findings

    def _escalate_sub_agent_spec(self, spec: SubAgentSpec) -> SubAgentSpec:
        """Escalate a sub-agent spec for a retry attempt.

        Bumps the model tier one level (STANDARD -> STRONG -> DEEP) so a
        retry that previously returned only research_gap findings gets a
        larger context window and a stronger analyzer. The question itself is
        left intact: the sub-agent already performs a drop_geography
        low-yield retry internally (sub_agent._search_*), so this reinforces
        that rather than rewriting the parent's carefully-framed question.
        """
        tier_order = (ModelTier.STANDARD, ModelTier.STRONG, ModelTier.DEEP)
        idx = tier_order.index(spec.model_tier) if spec.model_tier in tier_order else 0
        new_tier = tier_order[min(idx + 1, len(tier_order) - 1)]
        return spec.model_copy(update={"model_tier": new_tier})

    # ─────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Initialize the agent, subscribe to bus, set state to IDLE."""
        self.subscribe_to_bus()
        await self._transition(AgentState.IDLE, "Initialized")

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the agent's run() method with proper lifecycle management.

        This wraps run() with:
        1. State transition to WORKING
        2. Error handling, BLOCKED on failure
        3. State transition to DONE on success
        4. Bus status updates throughout

        Agents should NOT override this method, override run() instead.
        """
        await self._transition(AgentState.WORKING, "Starting execution")

        try:
            result = await self.run(*args, **kwargs)
            await self._transition(AgentState.DONE, "Execution complete")
            return result
        except Exception as e:
            await self._transition(
                AgentState.BLOCKED,
                f"Execution failed: {e}",
            )
            await self._escalate(
                issue=f"Agent execution failed: {e}",
                suggested_action="Reroute task or retry with different approach",
            )
            raise

    @abstractmethod
    async def run(self, *args: Any, **kwargs: Any) -> Any:
        """The agent's proprietary methodology.

        Every agent overrides this with its specific analytical framework.
        This is where the agent's skills are applied, tools are wielded,
        and structured output is produced.

        NOT a generic "research and write" method. Each agent's run()
        applies specific frameworks in a specific order with specific
        tools to produce specific structured output. (§0.1, §4.1)
        """
        ...

    async def cleanup(self) -> None:
        """Cleanup after execution, unsubscribe from bus."""
        self.bus.unsubscribe(self._sub_id)

    async def close(self) -> None:
        """Close all tool instances and clean up resources.

        Called by the orchestrator on shutdown. Closes every instantiated
        tool's HTTP client / browser / connection pool, then delegates to
        cleanup() to unsubscribe from the bus.

        Failures are logged, never swallowed: a tool whose close() raises has
        leaked an HTTP client, a browser process, or a connection pool, and a
        silent ``except Exception: pass`` here is exactly the anti-pattern
        that produced the original P0 (§0.3). We narrow the catch to the
        errors a real teardown can legitimately raise, log every one of them
        with the offending tool named, and keep closing the rest so one bad
        tool cannot strand the others.
        """
        for tool_name, tool in self._tools.items():
            close_method = getattr(tool, "close", None)
            if not callable(close_method):
                continue
            try:
                result = close_method()
                if asyncio.iscoroutine(result):
                    await result
            except asyncio.CancelledError:
                # Cancellation is control flow, not an error, never absorb it.
                raise
            except (RuntimeError, OSError, AttributeError, TypeError, ValueError):
                logger.warning(
                    "%s: tool %r failed to close, resource may be leaked",
                    self.name,
                    tool_name,
                    exc_info=True,
                )
        self._tools.clear()
        await self.cleanup()
