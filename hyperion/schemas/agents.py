"""
HYPERION Agent Schemas — the typed specification for every agent in the system.

This is not a generic agent config. Every agent in HYPERION has:
- Name and role (what they are)
- Model tier (what intelligence level they operate at)
- Tools (what they can actually use — not decorative)
- Skills (proprietary analytical methods they apply)
- System prompt (their expertise, voice, methodology)
- Spawn condition (when the Engagement Director activates them)

No agent is generic. No tool is idle. No skill is decorative.
(ARCHITECTURE.md §4.1)
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from hyperion.config import ModelTier


# ─────────────────────────────────────────────────────────────────────────────
# Agent Identity (ARCHITECTURE.md §4.2)
# ─────────────────────────────────────────────────────────────────────────────


class AgentRole(str, Enum):
    """The 4 organizational tiers of HYPERION agents (§4.2).

    - CORE: Always active (Engagement Director, Synthesis Lead)
    - SPECIALIST: Dynamic spawn — 12 domain experts
    - SUPPORT: Fact-checking, visualization, quality gating
    - DELIVERY: PDF composition and rendering
    """

    CORE = "core"
    SPECIALIST = "specialist"
    SUPPORT = "support"
    DELIVERY = "delivery"


class AgentState(str, Enum):
    """Live state of an agent during an engagement (§4.8 AgentBus channels).

    Published to the 'status' channel for the TUI agent grid to display.
    """

    IDLE = "idle"          # Not yet activated or finished and waiting
    WORKING = "working"    # Actively researching/analyzing
    WAITING = "waiting"    # Waiting for a dependency or rate limit
    DONE = "done"          # Completed all tasks
    BLOCKED = "blocked"    # Hit an error or cannot proceed
    SUB_AGENT_SPAWNED = "sub_agent_spawned"  # Has active sub-agents running


class AgentName(str, Enum):
    """The 20 named agents in HYPERION (§4.2).

    Each name maps to a specific specialist with proprietary skills.
    No two agents have the same skill set.
    """

    # Core (§4.3)
    ENGAGEMENT_DIRECTOR = "engagement_director"
    SYNTHESIS_LEAD = "synthesis_lead"

    # Specialists (§4.4)
    MARKET_ANALYST = "market_analyst"
    COMPETITIVE_INTEL = "competitive_intel"
    FINANCIAL_ANALYST = "financial_analyst"
    RISK_ANALYST = "risk_analyst"
    TECHNOLOGY_ANALYST = "technology_analyst"
    OPERATIONS_ANALYST = "operations_analyst"
    REGULATORY_ANALYST = "regulatory_analyst"
    SUSTAINABILITY_ANALYST = "sustainability_analyst"
    CONSUMER_INSIGHTS = "consumer_insights"
    MA_ANALYST = "ma_analyst"
    INNOVATION_ANALYST = "innovation_analyst"
    STRATEGY_ANALYST = "strategy_analyst"

    # Support (§4.5)
    RESEARCH_LIBRARIAN = "research_librarian"
    FACT_CHECKER = "fact_checker"
    DATA_VISUALIZER = "data_visualizer"
    QUALITY_GATE = "quality_gate"

    # Delivery (§4.6)
    PRESENTATION_DESIGNER = "presentation_designer"
    RENDER_ENGINE = "render_engine"


# ─────────────────────────────────────────────────────────────────────────────
# Tool and Skill Specifications
# ─────────────────────────────────────────────────────────────────────────────


class ToolName(str, Enum):
    """The 23 tools in HYPERION's registry (§5.1).

    Every tool is assigned to agents who actually use it.
    No decorative tools. No tool is assigned to an agent that doesn't need it.
    """

    SEARXNG = "searxng"
    JINA = "jina"
    OBSCURA = "obscura"
    SCRAPLING = "scrapling"
    CRAWL4AI = "crawl4ai"
    FLARESOLVERR = "flaresolverr"
    WAYBACK = "wayback"
    ALPHA_VANTAGE = "alpha_vantage"
    FRED = "fred"
    UNSPLASH = "unsplash"
    SECOND_BRAIN = "second_brain"
    DEEP_SEARCH = "deep_search"       # unified search orchestration (VIGIL Layer 5)

    # ── Data Sources (Phase 2) ──
    SEC_EDGAR = "sec_edgar"
    SEMANTIC_SCHOLAR = "semantic_scholar"
    OPEN_ALEX = "open_alex"
    WORLD_BANK = "world_bank"
    GOOGLE_TRENDS = "google_trends"
    HACKERNEWS = "hackernews"
    REDDIT = "reddit"

    PLOTLY = "plotly"
    WEASYPRINT = "weasyprint"
    JINJA2 = "jinja2"
    PILLOW = "pillow"


class SkillSpec(BaseModel):
    """A proprietary analytical framework that an agent applies.

    This is NOT a generic "research and write" instruction. Each skill is a
    named, structured methodology with defined inputs, outputs, and quality
    criteria (§0.1).

    Example: The Financial Analyst's DCF skill has:
    - name: "DCF (Discounted Cash Flow)"
    - description: explicit forecast period, terminal value, WACC, sensitivity
    - inputs: ["cash_flow_projections", "discount_rate", "terminal_growth_rate"]
    - outputs: ["dcf_valuation", "sensitivity_table", "key_value_drivers"]
    """

    name: str = Field(description="The framework name (e.g., 'Porter's Five Forces')")
    description: str = Field(description="What the framework does and how it's applied")
    inputs: list[str] = Field(default_factory=list, description="What data the framework needs")
    outputs: list[str] = Field(default_factory=list, description="What the framework produces")


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification (ARCHITECTURE.md §4.1)
# ─────────────────────────────────────────────────────────────────────────────


class AgentSpec(BaseModel):
    """Complete specification of a HYPERION agent.

    This is the contract that defines what an agent IS, what it CAN DO,
    and what it PRODUCES. The Engagement Director uses this to decide which
    agents to spawn for each engagement (§4.9).

    No agent is generic. Every field is deliberate.
    """

    name: AgentName = Field(description="The agent's identity")
    role: AgentRole = Field(description="Organizational tier (core/specialist/support/delivery)")
    display_name: str = Field(description="Human-readable name for TUI display")
    model_tier: ModelTier = Field(description="Intelligence level this agent operates at")
    tools: list[ToolName] = Field(description="Tools this agent can use — not decorative, all used")
    skills: list[SkillSpec] = Field(description="Proprietary analytical frameworks this agent applies")
    system_prompt: str = Field(description="Expertise, voice, methodology — not a generic prompt")
    spawn_condition: str = Field(description="When the Engagement Director activates this agent")
    max_sub_agents: int = Field(default=0, description="Max sub-agents this agent can spawn (0 = none)")
    output_model: str = Field(description="Name of the Pydantic model this agent produces")

    def has_tool(self, tool: ToolName) -> bool:
        """Check if this agent has access to a specific tool."""
        return tool in self.tools

    def has_skill(self, skill_name: str) -> bool:
        """Check if this agent has a specific skill by name."""
        return any(s.name == skill_name for s in self.skills)


# ─────────────────────────────────────────────────────────────────────────────
# Sub-Agent Specification (ARCHITECTURE.md §4.7)
# ─────────────────────────────────────────────────────────────────────────────


class SubAgentSpec(BaseModel):
    """Specification for a junior sub-agent spawned by a specialist.

    Sub-agents handle context isolation — a specialist sends a focused
    sub-question to a junior agent, gets structured findings back, and
    synthesizes them. This is how we handle context window limits without
    truncating or compressing (§4.7).

    Rules (§4.7):
    - Max 3 sub-agents per specialist per engagement
    - Sub-agents use MICRO or FAST tier only
    - Sub-agent findings are structured (Pydantic model), not free text
    - Sub-agents have 5-minute timeout
    - Sub-agents have access to a subset of parent's tools
    - Sub-agents cannot spawn their own sub-agents (no recursive spawning)
    """

    question: str = Field(description="The focused sub-question to research")
    parent_agent: AgentName = Field(description="Which specialist spawned this sub-agent")
    model_tier: ModelTier = Field(
        description="Must be MICRO or FAST — don't burn STRONG/DEEP quota"
    )
    tools: list[ToolName] = Field(
        description="Subset of parent's tools needed for this sub-question"
    )
    findings_model: str = Field(
        description="Name of the Pydantic model the sub-agent must produce"
    )
    timeout_seconds: int = Field(default=600, description="10-minute timeout per §4.7")
    context: dict[str, Any] = Field(
        default_factory=dict,
        description="Context bundle from parent (prior findings, search terms, etc.)"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Agent Runtime State
# ─────────────────────────────────────────────────────────────────────────────


class AgentRuntimeState(BaseModel):
    """Live runtime state of an agent during an engagement.

    Published to the AgentBus 'status' channel for TUI display.
    Updated in real-time as the agent works.

    The TUI agent grid (§8.5) displays:
    - Agent name
    - Model tier (color-coded)
    - State (IDLE/WORKING/WAITING/DONE/BLOCKED with icon)
    - Tools active
    - Findings count
    - Sub-agents spawned
    """

    agent_name: AgentName
    state: AgentState = AgentState.IDLE
    model_tier: ModelTier
    active_tools: list[ToolName] = Field(default_factory=list)
    findings_count: int = 0
    sub_agents_spawned: int = 0
    sub_agents_active: int = 0
    last_state_change: float = Field(description="Unix timestamp of last state change")
    detail: str = Field(default="", description="Human-readable detail for TUI display")

    def transition_to(self, new_state: AgentState, detail: str = "") -> None:
        """Transition to a new state with a detail message."""
        self.state = new_state
        self.detail = detail
        import time
        self.last_state_change = time.time()
