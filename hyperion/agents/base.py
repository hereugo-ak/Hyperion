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
from hyperion.schemas.models import (
    NON_SUBSTANTIVE_FINDING_TYPES,
    UNVERIFIED_ASSERTION_TYPE,
    ConfidenceLevel,
    KeyFinding,
    Source,
)

logger = logging.getLogger(__name__)

# Type variable for structured output models
T = TypeVar("T", bound=BaseModel)


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

        # F-07: questions that already got their ONE broadened respawn, so a
        # timeout/zero-yield can never loop. Keyed by the sub-question text.
        self._sub_agent_respawned: set[str] = set()

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
        # OVERHAUL2 S9: concurrent-cap pressure raises the concurrent budget
        # (3→…→5, bounded by SUB_AGENT_CONCURRENT_MAX). The sequential TOTAL
        # ceiling is untouched.
        boost = getattr(self, "_concurrent_boost", 0)
        return min(self.spec.max_sub_agents + boost, self.SUB_AGENT_CONCURRENT_MAX)

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

        F-0.1-12 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): a placeholder-text
        rejection is converted to a typed gap at this boundary, never surfaced
        as a raw validation ERROR. If a specialist tried to publish a finding
        whose title/content trips the P2-16 banned-filler guard, that finding is
        an AnalysisGap by definition — record it as one and continue.
        """
        try:
            # Force validation up-front so a banned-filler ValueError is caught
            # HERE, at the single publish boundary, instead of propagating as a
            # raw ERROR row in the TUI.
            finding.model_validate(finding)
        except (ValueError, TypeError) as exc:
            message = str(exc)
            if "placeholder text is unrepresentable" in message:
                logger.warning(
                    "F-0.1-12: finding rejected as placeholder filler — "
                    "converting to typed gap: %s", message[:120],
                )
                gap = KeyFinding(
                    id=f"placeholder_gap_{len(self._findings)}_{int(time.time())}",
                    agent=self.name.value,
                    finding_type="research_gap",
                    title="Placeholder rejected — converted to gap",
                    content=(
                        f"The {self.name.value} emitted a placeholder/unsupported "
                        f"finding text that the banned-filler guard rejected. This "
                        f"is an AnalysisGap, not evidence: {message[:160]}"
                    ),
                    confidence=ConfidenceLevel.LOW,
                    sources=[],
                )
                self._findings.append(gap)
                self.state.findings_count = len(self._findings)
                await self.bus.publish_finding(self.name, gap)
                return
            logger.warning("finding validation failed at publish (dropped): %s", message)
            return
        self._findings.append(finding)
        self.state.findings_count = len(self._findings)
        await self.bus.publish_finding(self.name, finding)

    async def _publish_framework_gap(
        self,
        *,
        mandatory_keys: list[Any],
        context_detail: str = "",
    ) -> bool:
        """F-0.1-11: framework-completeness gate (specialist tier).

        A specialist that returns a structurally-valid but content-empty model
        must NOT report "✓ complete" — that is the §0.3 fake-success class the
        audit fought. When mandatory output keys are empty, this publishes a
        typed ``research_gap`` finding carrying ``framework_insufficient:
        <key>=0`` and returns True so the caller can skip the "complete"
        transition and let the gap-closure loop re-dispatch. Returns False
        when every mandatory key is non-empty (gate passed).
        """
        empty = [key for key in mandatory_keys if not key]
        if not empty:
            return False
        reason = ", ".join(f"{k}=0" for k in empty)
        try:
            import time as _time

            gap = KeyFinding(
                id=f"framework_gap_{len(self._findings)}_{int(_time.time())}",
                agent=self.name.value,
                finding_type="research_gap",
                title=f"Framework insufficient: {reason}",
                content=(
                    f"The {self.name.value} returned a structurally-valid but "
                    f"content-empty analysis (mandatory output(s) missing: "
                    f"{reason}). This is not a success; the analysis is "
                    f"ungrounded and the gap-closure loop should re-dispatch. "
                    f"context={context_detail}"
                ),
                confidence=ConfidenceLevel.LOW,
                sources=[],
            )
            await self._publish_finding(gap)
        except Exception as exc:  # noqa: BLE001 - a gap must never break the specialist
            logger.warning("_publish_framework_gap failed: %s", exc)
        return True

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

    # ─────────────────────────────────────────────────────────────────────
    # P3.3: Zero-evidence gate — return True if the specialist collected
    # no sources during its search phase, and publish a research_gap
    # finding. Callers return a degraded model when True.
    # ─────────────────────────────────────────────────────────────────────

    async def _check_zero_evidence(self, context_detail: str = "") -> bool:
        """P3.3: Check if the specialist collected zero evidence.

        Call this after all search steps complete but before any analysis
        LLM call. Returns True when:
        - ``self._sources`` is empty (no URLs collected)
        - AND no search results were gathered

        When True, a ``research_gap`` finding is published so the gap-
        closure loop can re-dispatch. The caller should return a degraded
        model with LOW confidence and empty sources rather than running
        expensive LLM pipelines over an empty corpus.
        """
        # Safe access: specialists init _sources in __init__; support agents
        # that don't collect raw data never hit this code path.
        if getattr(self, "_sources", None):
            return False

        import time

        gap = KeyFinding(
            id=f"zero_evidence_gap_{len(self._findings)}_{int(time.time())}",
            agent=self.name.value,
            finding_type="research_gap",
            title=f"Zero evidence: {context_detail[:80]}" if context_detail else "Zero evidence "
                "collected",
            content=(
                f"The {self.name.value} collected zero source documents during its search "
                f"phase. All downstream analysis would be ungrounded, so the pipeline "
                f"returned early to avoid wasting tokens on LLM calls over an empty "
                f"corpus. context={context_detail}"
            ),
            confidence=ConfidenceLevel.LOW,
            sources=[],
        )
        await self._publish_finding(gap)
        return True

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

        ``tier`` optionally overrides the calling agent's own tier for a
        single call — e.g. Stage-A competitor discovery escalates to STRONG
        (Mistral Large) even though the specialist runs at STANDARD. The
        agent's system prompt is always prepended. If
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

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_prompt})

        effective_tier = tier or self.model_tier
        await self._transition(
            AgentState.WAITING,
            f"Requesting {effective_tier.value} tier completion",
        )

        # D-17: every agent call owns an explicit output ceiling. Leaving this
        # as None delegates length to provider defaults, which are often only a
        # few hundred tokens and silently cap substantive analysis.
        resolved_max_tokens = max_tokens or TIER_OUTPUT_BUDGET.get(effective_tier, 4_000)
        if resolved_max_tokens <= 0:
            resolved_max_tokens = 4_000

        response = await self.router.complete(
            tier=effective_tier,
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
                    "detail": f"{effective_tier.value} tier · {'OK' if response.success else 'FAIL'} · {len(response.content or '')} chars",
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
                issue=f"LLM completion failed at {effective_tier.value} tier: {response.error}",
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
            # F-05c: label every client with its owning agent so the search
            # budget can be tracked per-specialist, not just globally.
            return SearxNGClient(settings=self.settings, owner=self.name.value)
        elif tool == ToolName.JINA:
            from hyperion.tools.jina import JinaClient
            return JinaClient(settings=self.settings)
        elif tool == ToolName.UNIFIED_EXTRACT:
            # OVERHAUL5 W5 (D-07): the single extraction ladder as one facade.
            from hyperion.tools.unified_extract import UnifiedExtractTool

            return UnifiedExtractTool(settings=self.settings)
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

    # F-08b: the absolute sequential ceiling per specialist. The old fixed
    # 3-slot budget (max_sub_agents) was spent on sub-agents that timed out on
    # a dead pool ("SUB-AGENT budget reached (3/3)"). max_sub_agents now
    # bounds CONCURRENT sub-agents only; sequential re-fills are allowed up
    # to this ceiling so a released slot (timeout/zero yield) can be reused.
    SUB_AGENT_TOTAL_CEILING = 6

    #: OVERHAUL2 S9: concurrent-cap pressure raises the concurrent budget
    #: toward this bound; the sequential TOTAL ceiling above is unaffected.
    SUB_AGENT_CONCURRENT_MAX = 5

    # ─────────────────────────────────────────────────────────────────────
    # P-CORE (overhaul, 2026-08-10): evidence reconciliation — the proprietary
    # core. Sub-agent findings MUST reach the parent's analysis; a wrapper
    # drops them, a MBB-grade system funnels them. These two methods are the
    # deterministic reconciliation every specialist inherits.
    # ─────────────────────────────────────────────────────────────────────

    def _merge_evidence(
        self,
        sub_findings: list[KeyFinding],
        own_sources: list[Source],
    ) -> list[Source]:
        """Merge sub-agent evidence into the parent's source set (MBB funnel).

        Every sub-agent finding's ledger-bound sources are folded into the
        parent's ``_sources``, deduplicated by URL. This is what makes KPI-2
        (domains before synthesis) and KPI-3 (provenance binding) move
        together BY CONSTRUCTION: sub-agent evidence now counts toward the
        parent's corroboration, so the report's depth grows with the research
        actually performed instead of being discarded at the storage line.

        Returns the merged source list (the parent should assign it back to
        ``self._sources``). Never raises — a merge failure returns the parent's
        own sources unchanged so a sub-agent edge case cannot break analysis.
        """
        own_sources = list(own_sources or [])
        seen: set[str] = {s.url for s in own_sources if s.url}
        merged: list[Source] = list(own_sources)
        try:
            for finding in sub_findings or []:
                if getattr(finding, "finding_type", "") in NON_SUBSTANTIVE_FINDING_TYPES:
                    continue
                for src in getattr(finding, "sources", []) or []:
                    url = getattr(src, "url", "") or ""
                    if not url or url in seen:
                        continue
                    seen.add(url)
                    merged.append(src)
        except Exception as exc:  # noqa: BLE001 - merge must never break analysis
            logger.warning("_merge_evidence failed (returning own sources): %s", exc)
        return merged

    def _reconcile_findings(
        self,
        sub_findings: list[KeyFinding],
    ) -> list[KeyFinding]:
        """Reconcile sub-agent findings into parent-visible substantive findings.

        Filters out gaps/unverified assertions (they are typed limitations,
        never yield), so the parent's published finding set includes every
        sub-agent's corroborated evidence — not just the parent's own angle.
        This closes the "sub-agents returned findings but parent reported 0"
        gap: the parent's output is definitionally a superset of its
        sub-agents' evidence.

        Returns the reconciled list (the parent merges it into the findings it
        publishes). Never raises — a reconciliation failure returns [].
        """
        try:
            from hyperion.schemas.models import NON_SUBSTANTIVE_FINDING_TYPES

            return [
                f for f in sub_findings or []
                if getattr(f, "finding_type", "") not in NON_SUBSTANTIVE_FINDING_TYPES
            ]
        except Exception as exc:  # noqa: BLE001 - reconcile must never break analysis
            logger.warning("_reconcile_findings failed (returning []): %s", exc)
            return []

    def _detect_sub_agent_contradictions(
        self,
        sub_findings: list[KeyFinding],
    ) -> list[str]:
        """P-CORE: surface numeric contradictions BETWEEN sub-agent findings.

        MBB-grade depth shows the disagreement and resolves it — it never
        hides conflicting numbers. This extracts numeric claims from each
        substantive sub-agent finding and flags pairs that disagree on the
        same magnitude (e.g. one sub-agent cites a two-billion-dollar TAM,
        another cites twenty billion), so the parent's synthesis can either
        reconcile them evidence-weighted or carry them as a typed limitation
        instead of silently averaging.

        Returns a list of human-readable contradiction strings. Never raises —
        a detection failure yields [] so a sub-agent edge case cannot break
        the parent's analysis.
        """
        try:
            import re

            from hyperion.schemas.models import NON_SUBSTANTIVE_FINDING_TYPES

            findings = [
                f for f in sub_findings or []
                if getattr(f, "finding_type", "") not in NON_SUBSTANTIVE_FINDING_TYPES
            ]
            if len(findings) < 2:
                return []

            def _numbers(f: KeyFinding) -> list[float]:
                out: list[float] = []
                for token in re.findall(r"\$?\d[\d,]*\.?\d*[bBmMkK]?", getattr(f, "content", "") or ""):
                    try:
                        cleaned = token.replace(",", "").replace("$", "")
                        mult = 1.0
                        if cleaned[-1] in "bB":
                            mult, cleaned = 1e9, cleaned[:-1]
                        elif cleaned[-1] in "mM":
                            mult, cleaned = 1e6, cleaned[:-1]
                        elif cleaned[-1] in "kK":
                            mult, cleaned = 1e3, cleaned[:-1]
                        out.append(float(cleaned) * mult)
                    except ValueError:
                        continue
                return out

            contradictions: list[str] = []
            for i in range(len(findings)):
                for j in range(i + 1, len(findings)):
                    fa, fb = findings[i], findings[j]
                    nums_a, nums_b = _numbers(fa), _numbers(fb)
                    if not nums_a or not nums_b:
                        continue
                    for na in nums_a:
                        for nb in nums_b:
                            if na <= 0 or nb <= 0:
                                continue
                            ratio = max(na, nb) / min(na, nb)
                            if ratio >= 2.0:
                                contradictions.append(
                                    f"SUB-AGENT NUMERIC CONTRADICTION: "
                                    f"{getattr(fa, 'title', '')[:40]!r} cites {na:g} "
                                    f"but {getattr(fb, 'title', '')[:40]!r} cites {nb:g} "
                                    f"({ratio:.1f}x apart) — resolve evidence-weighted"
                                )
            # De-duplicate and cap for prompt budget.
            seen: set[str] = set()
            unique: list[str] = []
            for c in contradictions:
                if c not in seen:
                    seen.add(c)
                    unique.append(c)
            return unique[:8]
        except Exception as exc:  # noqa: BLE001 - contradiction detection must never break analysis
            logger.warning("_detect_sub_agent_contradictions failed: %s", exc)
            return []

    async def _ingest_sub_findings(self, sub_findings: list[KeyFinding] | None) -> None:
        """OVERHAUL2 S2/S11: the single sub-agent ingestion path.

        Replaces the 10 hand-wired assign/merge/reconcile/contradiction
        blocks whose ``sub_findings`` was assigned inside an ``if`` guard and
        consumed outside it (UnboundLocalError on empty collections), and
        whose publish loop was in one file gated on contradictions existing.
        Also runs the topicality guard (S11): off-topic sub-agent yield is
        dropped BEFORE merge/reconcile so a broadened query that drifted
        ("money laundering in real estate" inside a space-sector run) cannot
        be summarized and counted as evidence. Never raises; always
        publishes reconciled findings.
        """
        sub_findings = list(sub_findings or [])
        # OVERHAUL2 S11: drop off-topic sub-agent yield BEFORE merge/reconcile.
        # Broadened queries drift until *something* returns; without a guard
        # that something is summarized and counted (B-9: real-estate money
        # laundering inside a space-sector risk analysis). v1 is a blunt,
        # deterministic lexical overlap check against the engagement focus.
        try:
            from hyperion.tools.query_utils import get_engagement_focus
            _fq, _subject, _geo = get_engagement_focus()
            focus_tokens = {
                t.lower() for t in f"{_subject} {_geo}".split() if len(t) >= 4
            }
            if focus_tokens:
                kept, dropped = [], 0
                for f in sub_findings:
                    hay = f"{getattr(f, 'title', '')} {getattr(f, 'content', '')}".lower()
                    if any(tok in hay for tok in focus_tokens):
                        kept.append(f)
                    else:
                        dropped += 1
                if dropped:
                    self._log(f"TOPICALITY: dropped {dropped} off-topic sub-agent finding(s)")
                    # OVERHAUL2 S15: the drop is telemetry, not just a log line.
                    self._off_topic_dropped = getattr(self, "_off_topic_dropped", 0) + dropped
                sub_findings = kept
        except Exception as exc:  # noqa: BLE001 - guard must never break ingestion
            logger.debug("topicality guard skipped: %s", exc)
        self._sub_agent_findings = sub_findings
        try:
            self._sources = self._merge_evidence(sub_findings, getattr(self, "_sources", []))
            self._sub_agent_reconciled = self._reconcile_findings(sub_findings)
            self._sub_agent_contradictions = self._detect_sub_agent_contradictions(sub_findings)
        except Exception as exc:  # noqa: BLE001 - ingestion must never break analysis
            logger.warning("_ingest_sub_findings failed: %s", exc)
            self._sub_agent_reconciled = []
            self._sub_agent_contradictions = []
        if self._sub_agent_contradictions:
            self._log(
                "SUB-AGENT RECONCILIATION: {} contradiction(s) surfaced: {}".format(
                    len(self._sub_agent_contradictions),
                    "; ".join(self._sub_agent_contradictions[:3]),
                )
            )
        for _reconciled in self._sub_agent_reconciled:
            await self._publish_finding(_reconciled)

    async def _spawn_sub_agent(self, spec: SubAgentSpec) -> list[KeyFinding]:
        """Spawn a junior sub-agent for a focused sub-question.

        Sub-agents handle context isolation (§4.7):
        - max_sub_agents CONCURRENT, SUB_AGENT_TOTAL_CEILING sequential
        - STANDARD or higher tier (research needs a large context window)
        - Timeout: a timeout/zero-yield result releases its slot and triggers
          ONE broadened respawn (F-07) instead of a terminal gap
        - Returns structured KeyFinding objects, not free text
        - Cannot spawn their own sub-agents (no recursive spawning)

        The parent specialist receives the sub-agent's findings and
        synthesizes them into its own analysis. The parent's context
        window is used for synthesis, not for raw research.
        """
        from hyperion.agents.sub_agent import SubAgentRunner

        # OVERHAUL3 D-C (overhaul3_audit.md W1/S3): reset the budget-refusal
        # stamp at the top of EVERY spawn so a refusal recorded by an earlier
        # spawn can never leak into a later caller's self-heal decision. The
        # gate sets it to True only when IT refuses this specific spawn.
        self._last_spawn_refused = False

        # F-08: yield-aware budget. A slot is only consumed by a sub-agent
        # that produced >=1 non-gap finding; timeouts and zero-findings
        # RELEASE the slot (sequential refills up to SUB_AGENT_TOTAL_CEILING).
        # max_sub_agents is the CONCURRENT (resource) bound.
        # P4.6 (overhaul §6 P4, 2026-08-10): the TOTAL ceiling is a HARD
        # invariant that includes broadened respawns. The old code skipped BOTH
        # budget checks for `broadened=True` — the A-7 "SUB-AGENT total budget
        # reached (8/6)" overshoot, where broadened respawns rode in on top of
        # an already-full normal budget. Broadened spawns still bypass the
        # CONCURRENT cap (they reuse a released slot sequentially), but the
        # sequential total ceiling applies to every spawn.
        if not spec.broadened and self.state.sub_agents_active >= self.max_sub_agents:
            # OVERHAUL2 S9: pressure raises the budget (3→…→5) and DEFERS
            # the spec — the old branch silently discarded the work item
            # (B-8: "SUB-AGENT concurrent budget reached (3/3); proceeding
            # without spawning" ×3 on RISK, lost work, thinner parent).
            if self.max_sub_agents < self.SUB_AGENT_CONCURRENT_MAX:
                self._concurrent_boost = getattr(self, "_concurrent_boost", 0) + 1
                self._log(
                    f"SUB-AGENT concurrent budget raised to {self.max_sub_agents} "
                    f"(cap pressure; deferred: {spec.question[:60]})"
                )
            deferred = getattr(self, "_deferred_specs", None)
            if deferred is None:
                deferred = []
                self._deferred_specs = deferred
            deferred.append(spec)
            return []
        # F-0.1-14 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): budget counts DISTINCT
        # work items, not spawn attempts. A broadened respawn is a retry of the
        # SAME logical question — it must not double-dip the budget (the 8/6
        # starve: failed questions consumed the ceiling via original+respawn and
        # late legitimate tasks never spawned). The gate checks a set of distinct
        # questions; max_sub_agents remains the true concurrency bound.
        distinct_questions = getattr(self, "_sub_agent_questions", None)
        if distinct_questions is None:
            distinct_questions = set()
            self._sub_agent_questions = distinct_questions
        # OVERHAUL3 D-C (overhaul3_audit.md W1/S3): the gate is MEMBERSHIP-aware,
        # not size-only. A STRONG self-heal or a broadened respawn re-enters
        # with the SAME question (already counted); the old `len(...) >= CEILING`
        # check refused every retry once the set filled (3/3 under AMBER), so
        # PROVIDER_FAILURE heals logged "still failed on STRONG tier" when
        # STRONG never ran (the 2026-08-11 lie). Only genuinely NEW work items
        # consume the ceiling; a retry of a counted question is budget-free.
        if (
            spec.question not in distinct_questions
            and len(distinct_questions) >= self.SUB_AGENT_TOTAL_CEILING
        ):
            # P2 honesty (overhaul3 §5.4): stamp the refusal so the caller's
            # self-heal can log "refused by budget" instead of claiming the
            # STRONG tier ran and failed.
            self._last_spawn_refused = True
            self._log(
                f"SUB-AGENT total budget reached "
                f"({len(distinct_questions)}/{self.SUB_AGENT_TOTAL_CEILING} "
                f"distinct work items); "
                f"proceeding without spawning: {spec.question[:80]}"
            )
            return []
        distinct_questions.add(spec.question)

        # OVERHAUL2 S9 step 4: a BROADENED (retry) spawn jumps straight to the
        # concurrent ceiling — operator requirement: "increase to 5 in the next
        # attempt or retry". A retry reuses a released slot sequentially, so
        # raising the cap costs no additional concurrency; it just stops the
        # next legitimate task from being dropped.
        if spec.broadened and self.max_sub_agents < self.SUB_AGENT_CONCURRENT_MAX:
            self._concurrent_boost = self.SUB_AGENT_CONCURRENT_MAX - self.spec.max_sub_agents

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

        runner = SubAgentRunner(spec=spec, bus=self.bus, router=self.router)

        started = time.monotonic()
        timed_out = False
        generic_failure = False
        try:
            findings = await asyncio.wait_for(
                runner.run(),
                timeout=spec.timeout_seconds,
            )
        except TimeoutError:
            timed_out = True
            # Timeout is a bounded-resource outcome, not a reason to spend a
            # STRONG Director call. Preserve one explicit gap and continue.
            findings = [
                runner.gap_finding(
                    f"timed out after {spec.timeout_seconds}s",
                    time.monotonic() - started,
                )
            ]
            self._log(f"Sub-agent timed out: {spec.question[:80]}")
        except Exception as exc:  # noqa: BLE001 - isolate junior-agent failure
            generic_failure = True
            # Parallel specialist gather() calls use return_exceptions=True.
            # Letting this escape silently discarded the entire result and the
            # TUI reported '0 findings'. Convert every failure into auditable
            # evidence insufficiency at this boundary.
            findings = [
                runner.gap_finding(
                    f"failed with {type(exc).__name__}: {str(exc)[:160]}",
                    time.monotonic() - started,
                )
            ]
            logger.exception("Sub-agent failed for %r", spec.question[:120])
        finally:
            self.state.sub_agents_active = max(0, self.state.sub_agents_active - 1)

        # OVERHAUL2 S9: a released slot drains the deferred queue first.
        # The old code silently discarded specs that hit the concurrent cap;
        # the queue is drained here (the single slot-release site) so deferred
        # work is eventually dispatched instead of lost. Recursion depth is
        # naturally bounded by the queue.
        deferred = getattr(self, "_deferred_specs", None)
        if deferred and self.state.sub_agents_active < self.max_sub_agents:
            next_spec = deferred.pop(0)
            self._log(f"SUB-AGENT deferred spawn dispatched: {next_spec.question[:60]}")
            await self._spawn_sub_agent(next_spec)

        # P0 (overhaul §6 P0.3) + P3 (I-3): never count a research gap or an
        # unverified_assertion as a finding at the display layer. "Sub-agent
        # returned 1 findings" was the Aug-10 lie — a synthetic gap
        # placeholder masquerading as evidence yield, and an uncited claim is
        # equally not evidence.
        n_substantive = sum(
            1
            for f in findings
            if f.finding_type not in NON_SUBSTANTIVE_FINDING_TYPES
        )
        n_gaps = sum(1 for f in findings if f.finding_type == "research_gap")
        n_unverified = sum(
            1 for f in findings if f.finding_type == UNVERIFIED_ASSERTION_TYPE
        )
        gap_note = f" ({n_gaps} gap)" if n_gaps else ""
        unverified_note = f" ({n_unverified} unverified)" if n_unverified else ""
        await self._transition(
            AgentState.WORKING,
            f"Sub-agent returned {n_substantive} findings{gap_note}{unverified_note}",
        )

        # F-07 + F-0.1-10: exactly ONE respawn per question, typed by failure
        # class (recovery_hint). NOT on a generic exception (a code bug must
        # not be retried) and never on an already-broadened spec (no loops).
        hint = getattr(runner, "recovery_hint", "") or ""
        # B1/B2 (self-healing): a PROVIDER_FAILURE means the router's whole
        # tier chain failed. F-0.1-10 correctly refuses to REWORD a provider
        # bug — but the system must still SELF-HEAL: escalate once to a
        # STRONG-tier (Mistral Large) spec with a fresh router state. Only if
        # that also fails is the run typed terminal. This converts a transient
        # STANDARD-tier outage into a working result instead of a gap.
        if (
            hint == "PROVIDER_FAILURE"
            and not spec.broadened
            and spec.question not in getattr(self, "_sub_agent_provider_retried", set())
        ):
            self._log(
                f"SUB-AGENT PROVIDER SELF-HEAL: STANDARD-tier provider chain "
                f"failed for {spec.question[:80]} — escalating once to STRONG "
                f"(Mistral Large) with fresh router state"
            )
            if not hasattr(self, "_sub_agent_provider_retried"):
                self._sub_agent_provider_retried = set()
            self._sub_agent_provider_retried.add(spec.question)
            strong = spec.model_copy(update={
                "model_tier": ModelTier.STRONG,
                "broadened": False,
                "timeout_seconds": spec.timeout_seconds,
            })
            healed = await self._spawn_sub_agent(strong)
            if healed and not any(
                f.finding_type == "research_gap" for f in healed
            ):
                self._log(
                    f"SUB-AGENT PROVIDER SELF-HEAL SUCCEEDED: {spec.question[:80]} "
                    f"recovered {len(healed)} finding(s) on STRONG tier"
                )
                return healed
            # OVERHAUL3 D-C (overhaul3_audit.md W1/S3): logs must NEVER lie.
            # If the STRONG spawn was REFUSED at the total budget gate, it
            # never ran — stamp BUDGET_REFUSED instead of the old
            # "still failed on STRONG tier" line (which asserted an action the
            # gate refused). ``_last_spawn_refused`` is set by the gate's
            # refusal branch and reset at the top of every spawn.
            if getattr(self, "_last_spawn_refused", False):
                # OVERHAUL3 S10 (overhaul3_audit.md W4/S10, §5.4 P2): stamp the
                # RUNNER's typed outcome too. The runner typed
                # ANALYSIS_FAILED/RETRY_EXHAUSTED when PROVIDER_FAILURE was
                # recorded; leaving that would tell telemetry a STRONG-tier
                # run failed when the gate never let it run. Failure-class
                # accuracy is a typed property, not a log line.
                try:
                    from hyperion.schemas.models import ResearchOutcome

                    runner.outcome = ResearchOutcome.BUDGET_REFUSED
                    runner.recovery_hint = "BUDGET_REFUSED"
                except Exception as exc:  # noqa: BLE001 - stamping is best-effort
                    logger.debug("runner BUDGET_REFUSED stamp failed: %s", exc)
                self._log(
                    f"SUB-AGENT PROVIDER SELF-HEAL REFUSED BY BUDGET: "
                    f"{spec.question[:80]} — STRONG tier never ran (total "
                    f"budget gate); typed BUDGET_REFUSED, not a STRONG failure"
                )
            else:
                self._log(
                    f"SUB-AGENT PROVIDER SELF-HEAL EXHAUSTED: {spec.question[:80]} "
                    f"still failed on STRONG tier — typed terminal"
                )
        if self._should_respawn_broadened(
            spec, findings, timed_out, generic_failure, recovery_hint=hint
        ):
            self._log(
                f"SUB-AGENT RESPAWN (broadened, reason="
                f"{'timeout' if timed_out else (hint or 'zero_findings')}): "
                f"{spec.question[:80]}"
            )
            broadened = spec.model_copy(update={
                "broadened": True,
                "timeout_seconds": max(60, spec.timeout_seconds // 2),
            })
            self._sub_agent_respawned.add(spec.question)
            respawned = await self._spawn_sub_agent(broadened)
            if respawned and not any(
                f.finding_type == "research_gap" for f in respawned
            ):
                return respawned
            # F-01/F-02: the one permitted retry is spent and produced no
            # substantive evidence — this is a typed RETRY_EXHAUSTED
            # terminal state, never a fake successful finding. The runner's
            # outcome is stamped so the parent (and TUI) can observe the
            # exhausted state instead of re-deriving it from list lengths.
            from hyperion.schemas.models import ResearchOutcome

            runner.outcome = ResearchOutcome.RETRY_EXHAUSTED
            # F-0.1-9 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): surface the raw
            # discovery yield vs the extracted count on the terminal line so
            # "raw=0 (no discovery)" reads differently from "raw=14,
            # extracted=0 (fetched but blocked)". A fetch-blocked task is not
            # cured by a wider query; the parent must see which one happened.
            counters = getattr(runner, "counters", None)
            raw_n = getattr(counters, "raw_results", 0) if counters else 0
            extracted_n = getattr(counters, "extracted_documents", 0) if counters else 0
            self._log(
                f"SUB-AGENT RETRY EXHAUSTED: {spec.question[:80]} — "
                f"{len(findings)} finding(s), "
                f"{sum(1 for f in findings if f.finding_type == 'research_gap')} "
                f"gap(s); raw={raw_n}, extracted={extracted_n}, "
                f"recovery_hint={getattr(runner, 'recovery_hint', '')}; "
                "ending with explicit insufficient evidence"
            )

        return findings

    def _should_respawn_broadened(
        self,
        spec: SubAgentSpec,
        findings: list[KeyFinding],
        timed_out: bool,
        generic_failure: bool,
        recovery_hint: str = "",
    ) -> bool:
        """F-07/F-02 + F-0.1-10: decide whether this sub-agent earns a respawn.

        True when ALL of:
        - not already a broadened respawn (bounded: exactly one per question)
        - a production budget (unit-test / stress configs stay deterministic)
        - the question was not already respawned
        - the trigger is a TIMEOUT or a recovery-hint that broadening can cure
        - NOT a generic exception (a code bug must not be retried)
        - the retrieval dependency health gate is GREEN (F-02: never broaden
          into the same dead dependency; a 403/429/dead-pool outage is not
          cured by wider query text)

        F-0.1-10 (FIX0.1_SUB_AGENT_RETRY_EXHAUSTED.md): the recovery action is
        typed by FAILURE CLASS, not by attempt count. Broadening (rewording)
        cures LOW_YIELD (search ran, nothing found). It does NOT cure
        FETCH_BLOCKED / FETCH_INSUFFICIENT (the page was fetched but blocked or
        pricing-thin — those need the fallback data routes, not a wider query)
        and never retries PROVIDER_FAILURE (a code/provider bug). TIMEOUT keeps
        the halved-scope broaden.
        """
        if spec.broadened:
            return False
        if spec.timeout_seconds < 300:
            return False
        if spec.question in self._sub_agent_respawned:
            return False
        if generic_failure:
            return False
        if not self._dependency_health_green():
            self._log(
                "SUB-AGENT RESPAWN suppressed (F-02): retrieval dependency "
                "health gate is RED — broadening would retry the same dead "
                f"path: {spec.question[:80]}"
            )
            return False
        # F-0.1-10: failure-class routing. FETCH_BLOCKED / FETCH_INSUFFICIENT
        # are NOT cured by rewording — they need the fallback data routes
        # (F-0.1-6) or a labeled estimate (F-0.1-7), so no broaden. A
        # PROVIDER_FAILURE is a code/provider bug and is never retried.
        if recovery_hint in ("FETCH_BLOCKED", "FETCH_INSUFFICIENT", "PROVIDER_FAILURE"):
            self._log(
                "SUB-AGENT RESPAWN suppressed (F-0.1-10): failure class "
                f"{recovery_hint} is not cured by broadening (rewording); "
                f"routing to fallback/closure: {spec.question[:80]}"
            )
            return False
        if timed_out:
            return True
        # LOW_YIELD (search ran, nothing found) is the broaden-curable class.
        if recovery_hint in ("LOW_YIELD", "ENGINE_BLOCKED"):
            return True
        if len(findings) == 1 and findings[0].finding_type == "research_gap":
            return "no validated findings" in findings[0].content
        return False

    @staticmethod
    def _dependency_health_green() -> bool:
        """F-02: is the local retrieval dependency healthy enough to broaden?

        Uses the engine-health telemetry: when fewer than the healthy-engine
        floor are available, the pool is degraded and a broadened respawn
        would only re-prove the outage with wider query text. Query breadth
        is a recovery edge only after the dependency health gate is green.
        """
        try:
            from hyperion.tools.engine_health import get_engine_health
            from hyperion.tools.searxng import HEALTHY_ENGINE_FLOOR, referenced_engines

            health = get_engine_health()
            return health.healthy_count(referenced_engines()) >= HEALTHY_ENGINE_FLOOR
        except Exception:  # noqa: BLE001 - a telemetry outage must not crash respawn
            # Fail open: if we cannot read dependency health, the audit's
            # invariant ("classify before retry") cannot be proven, so do not
            # silently broaden. The sub-agent's typed outcome already carries
            # RETRIEVAL_DEGRADED for the parent to escalate.
            return False

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
