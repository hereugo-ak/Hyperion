"""
HYPERION BaseAgent — the foundation class for all 20 agents.

This is NOT a generic agent base class. It is the contract that every
HYPERION agent — from the Engagement Director to the Render Engine —
must fulfill. Every agent has:

- Identity: AgentName + AgentRole (who they are)
- Intelligence: ModelTier (what level they operate at)
- Tools: ToolName list (what they can actually use — not decorative)
- Skills: SkillSpec list (proprietary analytical frameworks)
- System prompt: their expertise, voice, methodology (not generic)
- AgentBus subscription: for inter-agent communication (§4.8)
- Runtime state: AgentRuntimeState for TUI display (§8.5)
- Structured output: produces Pydantic models, not free text (§0.1)

The BaseAgent provides:
1. Bus integration — publish findings, status, escalations, handoffs
2. Router integration — request LLM completions by tier (agents don't know providers)
3. Tool access — lazy-initialized tool instances, only available if in spec
4. Sub-agent spawning — delegates to SubAgentRunner with 5-min timeout
5. State management — transitions published to bus for TUI display
6. Error handling — BLOCKED state on failure, escalation to Director

Agents override `run()` with their proprietary methodology.
The `run()` method is where the agent's skills are applied.
(§4.1, §0.1)
"""

from __future__ import annotations

import asyncio
import time
from abc import ABC, abstractmethod
from typing import Any, TypeVar

from pydantic import BaseModel

from hyperion.agents.bus import AgentBus, Channel, MessageType, get_bus
from hyperion.config import ModelTier, get_settings
from hyperion.router.budget import TaskUrgency
from hyperion.router.providers.base import RouterResponse
from hyperion.router.router import LLMRouter, get_router
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
    The system prompt is loaded from the AgentSpec — it is the agent's
    expertise, voice, and methodology, not a generic instruction.

    This class is NOT instantiable directly — it is abstract.
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

        # Runtime state — published to bus for TUI agent grid (§8.5)
        self.state = AgentRuntimeState(
            agent_name=spec.name,
            state=AgentState.IDLE,
            model_tier=spec.model_tier,
            last_state_change=time.time(),
        )

        # Tool instances — lazy initialized, only for tools in spec
        self._tools: dict[ToolName, Any] = {}

        # Findings collected by this agent
        self._findings: list[KeyFinding] = []

        # Sub-agent specs spawned by this agent
        self._sub_agent_specs: list[SubAgentSpec] = []

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
    # Context Enrichment — MICRO LLM classifier (P7 GAP-2)
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
        'technology', 'company' — but the orchestrator only passes prior agent
        outputs keyed by agent name. This method populates those keys so
        search queries are never empty.
        """
        # Try MICRO LLM classifier first
        try:
            ctx = await self._enrich_context_llm(question)
            if ctx:
                return ctx
        except Exception:
            pass

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

        data = json.loads(response.content)
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

        # Industry/sector detection — common industries
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

        # Company detection — look for capitalized words near "company" or "startup"
        company_match = re.search(r'(?:company|startup|firm|corporation|inc|ltd)\s+([A-Z][a-zA-Z]+)', question)
        if company_match:
            ctx["company"] = company_match.group(1)

        return ctx

    # ─────────────────────────────────────────────────────────────────────
    # State Management — published to bus for TUI (§8.5)
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
        """Update which tools are currently active — for TUI display."""
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
        Default: ignore (agent processes messages in its run() loop).
        """
        pass

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

        This is NOT a bus channel for agent communication — it's a one-way
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
        """
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
    # Router Integration (§3.1) — agents don't know providers
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
    ) -> RouterResponse:
        """Request an LLM completion at this agent's model tier.

        Agents don't know which provider they're using — they request a
        tier and the router decides. This is the core abstraction that
        decouples agent intelligence from infrastructure. (§9)

        The agent's system prompt is always prepended. If
        system_prompt_override is provided, it replaces the default.
        """
        system = system_prompt_override or self.system_prompt

        messages: list[dict[str, str]] = [{"role": "system", "content": system}]
        if conversation_history:
            messages.extend(conversation_history)
        messages.append({"role": "user", "content": user_prompt})

        await self._transition(
            AgentState.WAITING,
            f"Requesting {self.model_tier.value} tier completion",
        )

        response = await self.router.complete(
            tier=self.model_tier,
            messages=messages,
            agent_name=self.name.value,
            urgency=urgency,
            temperature=temperature,
            max_tokens=max_tokens,
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
                },
            )
        except Exception:
            pass

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

        # Strip markdown code fences from JSON responses — many LLMs wrap
        # JSON in ```json blocks despite response_format=json_object.
        # This fixes all downstream json.loads(response.content) calls.
        if response.success and response.content and response_format and response_format.get("type") == "json_object":
            content = response.content.strip()
            if content.startswith("```"):
                from hyperion.router.structured_validator import extract_json
                cleaned = extract_json(content)
                if cleaned:
                    response.content = cleaned

        return response

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
        randomness — we want deterministic, typed results.
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
    # Tool Access (§5.1) — only tools in the agent's spec
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

        return self._tools[tool]

    async def get_tool_or_escalate(self, tool: ToolName) -> Any | None:
        """D4-rest: Get a tool, escalating on failure instead of raising.

        If the tool is unavailable (not in spec, or instantiation fails),
        publishes an ESCALATION so the director can adapt. Returns None
        on failure — callers must check and degrade gracefully.
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

    async def _spawn_sub_agent(self, spec: SubAgentSpec) -> list[KeyFinding]:
        """Spawn a junior sub-agent for a focused sub-question.

        Sub-agents handle context isolation (§4.7):
        - Max 3 per specialist per engagement
        - MICRO or FAST tier only (don't burn STRONG/DEEP quota)
        - 5-minute timeout
        - Returns structured KeyFinding objects, not free text
        - Cannot spawn their own sub-agents (no recursive spawning)

        The parent specialist receives the sub-agent's findings and
        synthesizes them into its own analysis. The parent's context
        window is used for synthesis, not for raw research.
        """
        from hyperion.agents.sub_agent import SubAgentRunner

        if len(self._sub_agent_specs) >= self.max_sub_agents:
            await self._escalate(
                issue=f"Max sub-agents ({self.max_sub_agents}) already spawned",
                suggested_action="Proceed with available findings and flag the gap",
            )
            return []

        # Validate tier — sub-agents must use MICRO or FAST (§4.7)
        if spec.model_tier not in (ModelTier.MICRO, ModelTier.FAST):
            raise ValueError(
                f"Sub-agent tier must be MICRO or FAST, got {spec.model_tier.value}"
            )

        self._sub_agent_specs.append(spec)
        self.state.sub_agents_spawned += 1
        self.state.sub_agents_active += 1

        await self._transition(
            AgentState.SUB_AGENT_SPAWNED,
            f"Spawned sub-agent: {spec.question[:80]}",
        )

        runner = SubAgentRunner(spec=spec, bus=self.bus, router=self.router)

        try:
            findings = await asyncio.wait_for(
                runner.run(),
                timeout=spec.timeout_seconds,
            )
        except asyncio.TimeoutError:
            # §4.7: "if a sub-agent doesn't return in 5 min, the parent
            # proceeds with available findings and flags the gap"
            findings = []
            await self._escalate(
                issue=f"Sub-agent timed out: {spec.question[:80]}",
                suggested_action="Proceed with available findings and flag the gap",
            )

        self.state.sub_agents_active -= 1
        await self._transition(
            AgentState.WORKING,
            f"Sub-agent returned {len(findings)} findings",
        )

        return findings

    # ─────────────────────────────────────────────────────────────────────
    # Lifecycle
    # ─────────────────────────────────────────────────────────────────────

    async def initialize(self) -> None:
        """Initialize the agent — subscribe to bus, set state to IDLE."""
        self.subscribe_to_bus()
        await self._transition(AgentState.IDLE, "Initialized")

    async def execute(self, *args: Any, **kwargs: Any) -> Any:
        """Execute the agent's run() method with proper lifecycle management.

        This wraps run() with:
        1. State transition to WORKING
        2. Error handling — BLOCKED on failure
        3. State transition to DONE on success
        4. Bus status updates throughout

        Agents should NOT override this method — override run() instead.
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
        """Cleanup after execution — unsubscribe from bus."""
        self.bus.unsubscribe(self._sub_id)

    async def close(self) -> None:
        """Close all tool instances and clean up resources.

        Called by the orchestrator on shutdown. Closes every instantiated
        tool's HTTP client / browser / connection pool, then delegates to
        cleanup() to unsubscribe from the bus.
        """
        for tool_name, tool in self._tools.items():
            close_method = getattr(tool, "close", None)
            if callable(close_method):
                try:
                    result = close_method()
                    if asyncio.iscoroutine(result):
                        await result
                except (RuntimeError, OSError, Exception):
                    pass
        self._tools.clear()
        await self.cleanup()
