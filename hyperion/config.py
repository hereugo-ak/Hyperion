"""
HYPERION Configuration — Pydantic Settings with HYPERION_ prefix.

This is not a generic config file. It encodes the entire provider matrix
(§2), model tier assignments (§2.5), wait gate parameters (§3), quality
gate thresholds (§4.5), and sub-agent rules (§4.7) as typed, validated
Pydantic models. Every value maps to an architectural decision.

The provider rate limits are not suggestions — they are the constraints
the wait gate operates within. Changing them without updating the wait
gate logic will cause 429s or underutilization.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


# ─────────────────────────────────────────────────────────────────────────────
# Model Tiers — the 5 intelligence levels (ARCHITECTURE.md §2.5)
# ─────────────────────────────────────────────────────────────────────────────


class ModelTier(str, Enum):
    """The 5 model intelligence tiers. Each agent operates at exactly one tier.

    The tier determines which providers/models are eligible, the output token
    budget for estimation, and the priority for daily budget allocation.
    """

    MICRO = "micro"      # High RPD workhorse — query gen, fact-check snippets, sub-agent quick tasks
    FAST = "fast"        # Speed-critical — real-time extraction validation, inline fact verification
    STANDARD = "standard"  # Research & analysis — specialist analysis, structured Pydantic output
    STRONG = "strong"    # Planning & writing — engagement planning, synthesis, quality gate
    DEEP = "deep"        # Ultra-long context — multi-source reconciliation, full-document synthesis
    CPU = "cpu"          # No LLM — CPU-only tasks (PDF rendering, image processing)


# Output token budgets per tier (ARCHITECTURE.md §3.4)
TIER_OUTPUT_BUDGET: dict[ModelTier, int] = {
    ModelTier.MICRO: 500,
    ModelTier.FAST: 2000,
    ModelTier.STANDARD: 4000,
    ModelTier.STRONG: 8000,
    ModelTier.DEEP: 16000,
    ModelTier.CPU: 0,
}


# ─────────────────────────────────────────────────────────────────────────────
# Provider Model Definitions (ARCHITECTURE.md §2.1–§2.4)
# ─────────────────────────────────────────────────────────────────────────────


class ProviderType(str, Enum):
    """The 5 LLM providers. All expose OpenAI-compatible APIs."""

    GOOGLE = "google"
    NVIDIA = "nvidia"
    CEREBRAS = "cerebras"
    GROQ = "groq"
    MISTRAL = "mistral"


class ModelSpec(BaseModel):
    """Specification for a single model on a single provider.

    Encodes the exact rate limits from the provider matrix (§2).
    The wait gate uses these to track capacity in real-time.
    """

    name: str
    provider: ProviderType
    context_window: int = Field(description="Max context window in tokens")
    rpm: int = Field(description="Requests per minute limit")
    tpm: int = Field(description="Tokens per minute limit")
    rpd: int | None = Field(default=None, description="Requests per day limit (None if unlimited)")
    tpd: int | None = Field(default=None, description="Tokens per day limit (None if unlimited)")
    speed_tps: float | None = Field(default=None, description="Tokens per second (for speed-aware routing)")
    tier: ModelTier = Field(description="Primary tier this model serves")
    roles: list[str] = Field(default_factory=list, description="What this model is used for")
    deprecated: bool = Field(default=False, description="If true, never route to this model")


# ─────────────────────────────────────────────────────────────────────────────
# Provider Configurations
# ─────────────────────────────────────────────────────────────────────────────


class ProviderConfig(BaseModel):
    """Configuration for a single LLM provider.

    Contains the API key, base URL, and all models available on this provider.
    The wait gate tracks capacity per model within each provider.
    """

    api_key: str = ""
    base_url: str = ""
    models: list[ModelSpec] = Field(default_factory=list)

    def get_models_for_tier(self, tier: ModelTier) -> list[ModelSpec]:
        """Return all non-deprecated models on this provider that serve the given tier."""
        return [m for m in self.models if m.tier == tier and not m.deprecated]


# ── Google AI Studio (§2.1) ──────────────────────────────────────────────────

GOOGLE_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="gemma-4-31b",
        provider=ProviderType.GOOGLE,
        context_window=16_000,
        rpm=30,
        tpm=16_000,
        rpd=14_400,
        tier=ModelTier.MICRO,
        roles=["query generation", "fact-check snippets", "simple extraction", "sub-agent quick tasks", "keyword expansion", "tag generation"],
    ),
    ModelSpec(
        name="gemma-4-26b",
        provider=ProviderType.GOOGLE,
        context_window=16_000,
        rpm=30,
        tpm=16_000,
        rpd=14_400,
        tier=ModelTier.MICRO,
        roles=["backup workhorse"],
    ),
    ModelSpec(
        name="gemini-3.1-flash-lite",
        provider=ProviderType.GOOGLE,
        context_window=250_000,
        rpm=15,
        tpm=250_000,
        rpd=500,
        tier=ModelTier.DEEP,
        roles=["deep context", "long doc synthesis"],
    ),
    ModelSpec(
        name="gemini-3.5-flash",
        provider=ProviderType.GOOGLE,
        context_window=250_000,
        rpm=5,
        tpm=250_000,
        rpd=20,
        tier=ModelTier.DEEP,
        roles=["reserve"],
        deprecated=True,  # D19: rpd=20 is too low for useful DEEP capacity
    ),
    ModelSpec(
        name="gemini-3-flash",
        provider=ProviderType.GOOGLE,
        context_window=250_000,
        rpm=5,
        tpm=250_000,
        rpd=20,
        tier=ModelTier.DEEP,
        roles=["reserve"],
        deprecated=True,  # D19: rpd=20 is too low for useful DEEP capacity
    ),
]

# ── NVIDIA NIM (§2.2) ────────────────────────────────────────────────────────

NVIDIA_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="nvidia/nemotron-3-super-120b-a12b",
        provider=ProviderType.NVIDIA,
        context_window=262_000,
        rpm=40,
        tpm=262_000,
        rpd=None,
        tier=ModelTier.STRONG,
        roles=["planning", "writing", "design"],
    ),
    ModelSpec(
        name="nvidia/nemotron-3-ultra-550b-a55b",
        provider=ProviderType.NVIDIA,
        context_window=1_000_000,
        rpm=40,
        tpm=1_000_000,
        rpd=None,
        tier=ModelTier.DEEP,
        roles=["deep reserve", "ultra-long context"],
    ),
    ModelSpec(
        name="nvidia/nemotron-3-nano-30b-a3b",
        provider=ProviderType.NVIDIA,
        context_window=262_000,
        rpm=40,
        tpm=262_000,
        rpd=None,
        tier=ModelTier.STANDARD,
        roles=["research", "sub-agents"],
    ),
    ModelSpec(
        name="nvidia/llama-3.3-nemotron-super-49b-v1.5",
        provider=ProviderType.NVIDIA,
        context_window=131_000,
        rpm=40,
        tpm=131_000,
        rpd=None,
        tier=ModelTier.STANDARD,
        roles=["backup standard"],
    ),
]

# ── Cerebras (§2.3) ──────────────────────────────────────────────────────────

CEREBRAS_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="gpt-oss-120b",
        provider=ProviderType.CEREBRAS,
        context_window=131_000,
        rpm=5,
        tpm=30_000,
        tpd=1_000_000,
        speed_tps=3000.0,
        tier=ModelTier.FAST,
        roles=["fast", "real-time extraction"],
    ),
    ModelSpec(
        name="gemma-4-31b",
        provider=ProviderType.CEREBRAS,
        context_window=131_000,
        rpm=5,
        tpm=30_000,
        tpd=1_000_000,
        speed_tps=1850.0,
        tier=ModelTier.FAST,
        roles=["backup fast"],
    ),
]

# ── Groq (§2.4) ──────────────────────────────────────────────────────────────

GROQ_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="gpt-oss-120b",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=30,
        tpm=8_000,
        rpd=1_000,
        tpd=200_000,
        tier=ModelTier.STANDARD,
        roles=["standard", "research", "analysis"],
    ),
    ModelSpec(
        name="llama-3.3-70b-versatile",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=30,
        tpm=12_000,
        rpd=1_000,
        tpd=100_000,
        tier=ModelTier.STANDARD,
        roles=["standard alt", "higher TPM"],
    ),
    ModelSpec(
        name="llama-3.1-8b-instant",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=30,
        tpm=6_000,
        rpd=14_400,
        tpd=500_000,
        tier=ModelTier.MICRO,
        roles=["micro backup", "14.4K RPD"],
    ),
    # D20: Add a Groq FAST model for secondary FAST capacity
    ModelSpec(
        name="llama-3.1-8b-instant",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=30,
        tpm=30_000,
        rpd=14_400,
        tpd=500_000,
        tier=ModelTier.FAST,
        roles=["fast secondary", "sub-agent research", "keyword matching"],
    ),
    ModelSpec(
        name="llama-4-scout-17b",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=30,
        tpm=30_000,
        rpd=1_000,
        tpd=500_000,
        tier=ModelTier.STANDARD,
        roles=["high TPM tasks"],
    ),
    ModelSpec(
        name="qwen-3-32b",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=60,
        tpm=6_000,
        rpd=1_000,
        tpd=500_000,
        tier=ModelTier.STANDARD,
        roles=["high RPM tasks"],
    ),
    ModelSpec(
        name="gpt-oss-20b",
        provider=ProviderType.GROQ,
        context_window=128_000,
        rpm=30,
        tpm=8_000,
        rpd=1_000,
        tpd=200_000,
        tier=ModelTier.STANDARD,
        roles=["lightweight reasoning"],
    ),
]

# ── Mistral AI (§2.5 — 5th provider, free Experiment tier) ───────────────────

MISTRAL_MODELS: list[ModelSpec] = [
    ModelSpec(
        name="mistral-large-latest",
        provider=ProviderType.MISTRAL,
        context_window=128_000,
        rpm=60,
        tpm=500_000,
        rpd=None,
        tier=ModelTier.STRONG,
        roles=["planning", "writing", "synthesis", "quality gate"],
    ),
    ModelSpec(
        name="mistral-medium-latest",
        provider=ProviderType.MISTRAL,
        context_window=128_000,
        rpm=60,
        tpm=500_000,
        rpd=None,
        tier=ModelTier.STANDARD,
        roles=["research", "analysis", "structured output"],
    ),
    ModelSpec(
        name="magistral-medium-latest",
        provider=ProviderType.MISTRAL,
        context_window=40_000,
        rpm=60,
        tpm=500_000,
        rpd=None,
        tier=ModelTier.STRONG,
        roles=["reasoning", "DCF", "risk analysis", "game theory", "strategic options"],
    ),
    ModelSpec(
        name="magistral-small-latest",
        provider=ProviderType.MISTRAL,
        context_window=40_000,
        rpm=60,
        tpm=500_000,
        rpd=None,
        tier=ModelTier.STANDARD,
        roles=["reasoning", "fact-check logic", "quality scoring"],
    ),
    ModelSpec(
        name="mistral-small-latest",
        provider=ProviderType.MISTRAL,
        context_window=32_000,
        rpm=60,
        tpm=500_000,
        rpd=None,
        tier=ModelTier.FAST,
        roles=["fast extraction", "sub-agent research", "keyword matching"],
    ),
    ModelSpec(
        name="devstral-latest",
        provider=ProviderType.MISTRAL,
        context_window=256_000,
        rpm=60,
        tpm=500_000,
        rpd=None,
        tier=ModelTier.DEEP,
        roles=["long context", "tool orchestration", "multi-file reasoning"],
    ),
    ModelSpec(
        name="ministral-3b-latest",
        provider=ProviderType.MISTRAL,
        context_window=128_000,
        rpm=60,
        tpm=500_000,
        rpd=None,
        tier=ModelTier.MICRO,
        roles=["micro tasks", "quick lookups", "simple classification", "sub-agent"],
    ),
]


# ─────────────────────────────────────────────────────────────────────────────
# Wait Gate Configuration (ARCHITECTURE.md §3)
# ─────────────────────────────────────────────────────────────────────────────


class WaitGateConfig(BaseModel):
    """Configuration for the predictive wait gate system.

    The wait gate tracks RPM/TPM/RPD in real-time sliding windows across
    all 4 providers and routes requests to avoid 429s before they happen.
    These parameters control the routing, failover, and budget behavior.
    """

    # Sliding window size in seconds (ARCHITECTURE.md §3.3)
    window_seconds: int = 60

    # Short wait threshold — below this, sleep and retry (§3.3)
    short_wait_threshold: float = 5.0

    # Medium wait threshold — queue and yield to async scheduler (§3.3)
    medium_wait_threshold: float = 30.0

    # Daily budget reserve percentage — preserved on every provider for
    # critical end-of-engagement tasks (§3.5)
    budget_reserve: float = Field(default=0.20, description="Fraction of daily budget reserved (0.20 = 20%)")

    # Cooldown after a 429 in seconds (§3.6)
    rate_limit_cooldown: int = 60

    # Circuit breaker — consecutive failures before cooldown (§3.6)
    circuit_breaker_threshold: int = 3

    # Circuit breaker cooldown period in seconds (§3.6)
    circuit_breaker_cooldown: int = 300

    # Max retries for timeout with exponential backoff (§3.6)
    max_timeout_retries: int = 3

    # Base backoff for timeout retries (§3.6: 1s, 2s, 4s)
    timeout_backoff_base: float = 1.0

    # Scoring weights for provider selection (§3.3)
    score_weight_capacity: float = 0.5
    score_weight_latency: float = 0.3
    score_weight_context_fit: float = 0.2


# ─────────────────────────────────────────────────────────────────────────────
# Quality Gate Configuration (ARCHITECTURE.md §4.5, Agent 18)
# ─────────────────────────────────────────────────────────────────────────────


class QualityGateConfig(BaseModel):
    """Configuration for the 10-dimension quality gate.

    Reports scoring below the threshold go back for iteration.
    Max iterations before escalation to the Engagement Director.
    """

    # Minimum score to approve (1-5 scale, §4.5)
    threshold: float = 4.0

    # Max iterations before escalation (§4.5) — capped at 2 (P7 content-aware gate)
    max_iterations: int = 2

    # Source-count floor — if the report has fewer sources than this, stop
    # iterating because more passes won't fix thin evidence (P7 content-aware gate)
    source_count_floor: int = 3

    # Minimum per-dimension score — if any dimension scores below this,
    # the report goes back regardless of total score (§6.5)
    min_dimension_score: int = 3

    # The 10 quality dimensions (§4.5, Agent 18)
    dimensions: list[str] = Field(default_factory=lambda: [
        "completeness",
        "evidence_sufficiency",
        "analytical_depth",
        "logical_consistency",
        "contradiction_resolution",
        "tone_and_voice",
        "structural_quality",
        "risk_coverage",
        "data_accuracy",
        "visual_quality",
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Sub-Agent Configuration (ARCHITECTURE.md §4.7)
# ─────────────────────────────────────────────────────────────────────────────


class SubAgentConfig(BaseModel):
    """Configuration for junior sub-agent spawning.

    Sub-agents handle context isolation — a specialist sends a focused
    sub-question to a junior agent, gets structured findings back, and
    synthesizes them. This is how we handle context window limits without
    truncating or compressing.
    """

    # Max sub-agents per specialist per engagement (§4.7)
    max_per_specialist: int = 3

    # Timeout in seconds — if a sub-agent doesn't return, the parent
    # proceeds with available findings and flags the gap (§4.7)
    timeout_seconds: int = 300

    # Sub-agents use MICRO/FAST/STANDARD — STANDARD for deeper findings (§4.7)
    allowed_tiers: list[ModelTier] = Field(default_factory=lambda: [ModelTier.MICRO, ModelTier.FAST, ModelTier.STANDARD])

    # Sub-agents cannot spawn their own sub-agents (§4.7)
    allow_recursive: bool = False


# ─────────────────────────────────────────────────────────────────────────────
# Engagement Configuration (ARCHITECTURE.md §10)
# ─────────────────────────────────────────────────────────────────────────────


class EngagementConfig(BaseModel):
    """Configuration for engagement lifecycle.

    An engagement is the full cycle from question to PDF (1-15 min).
    These parameters control the orchestration boundaries.
    """

    # Maximum engagement duration in seconds (§0: 1-15 min)
    max_duration_seconds: int = 900

    # Estimated LLM calls for a standard engagement (§10.1)
    estimated_llm_calls: int = 45

    # Estimated token consumption for a standard engagement (§10.1)
    estimated_tokens: int = 120_000


# ─────────────────────────────────────────────────────────────────────────────
# Tool Paths Configuration (ARCHITECTURE.md §5)
# ─────────────────────────────────────────────────────────────────────────────


class ToolPathsConfig(BaseModel):
    """Paths to external tools and infrastructure.

    SearxNG runs in Docker, Obscura is a binary, the vault is a directory.
    These paths tell the system where to find them.
    """

    # SearxNG — self-hosted meta-search in Docker (§5.1)
    searxng_url: str = "http://localhost:8888"

    # Jina — search + reader API (§5.1)
    jina_api_key: str = ""

    # Obscura — Rust headless browser binary (§5.1)
    # Empty string means "look in PATH"
    obscura_path: str = ""

    # FlareSolverr — CAPTCHA-solving proxy in Docker (§5.1)
    # Solves Cloudflare/DDoS-GUARD challenges via headless Chromium
    flaresolverr_url: str = "http://localhost:8191/v1"

    # Alpha Vantage — financial data API (§5.1)
    alpha_vantage_api_key: str = ""

    # FRED — economic data API (§5.1)
    fred_api_key: str = ""

    # ── Phase 2 Data Sources ──
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "hyperion_research/1.0"

    # Semantic Scholar — academic paper search API (§5.1)
    semantic_scholar_api_key: str = ""

    # Unsplash — image search API (§5.1)
    unsplash_access_key: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# Color System (ARCHITECTURE.md §7)
# ─────────────────────────────────────────────────────────────────────────────


class ColorSystem(BaseModel):
    """HYPERION's two color systems: TUI (terminal) and PDF (print).

    Both are warm, earthy, and premium. Neither uses blue.
    Blue is the color of AI slop — every generic AI product uses
    blue-to-purple gradients. HYPERION uses warm tones inspired by
    aged instrument metals and Claude's paper-like aesthetic.
    """

    # TUI Palette (§7.1) — inspired by aged instrument metals
    tui_obsidian: str = "#0C0A08"        # Base surface — warm black
    tui_parchment: str = "#EDE4D3"       # Primary text — warm off-white
    tui_burnished_bronze: str = "#C89550"  # Primary accent — needle, actions, focus
    tui_verdigris: str = "#4B8F7E"       # Status accent — agent active, success
    tui_umber: str = "#362E22"           # Structure — borders, dim chrome
    tui_oxide: str = "#B5533C"           # Alert — errors only

    # PDF Report Palette (§7.2) — Claude-inspired warm, not blue AI slop
    pdf_warm_charcoal: str = "#1A1A1A"   # Primary text, headings
    pdf_cream: str = "#F5F4EE"           # Page background — warm paper
    pdf_terracotta: str = "#C8704D"      # Primary accent — headers, key boxes, chart primary
    pdf_sage: str = "#7C9885"            # Secondary accent — positive findings
    pdf_beige: str = "#E8E6DD"           # Section backgrounds, callout boxes
    pdf_warm_gray: str = "#8B8680"       # Captions, footnotes, secondary text
    pdf_deep_brown: str = "#3D3530"      # Footer, methodology section
    pdf_alert_red: str = "#B5533C"       # Risk indicators only — never decorative

    # Chart color sequence (§7.3) — always in this order
    chart_colors: list[str] = Field(default_factory=lambda: [
        "#C8704D",  # Terracotta — always first series
        "#7C9885",  # Sage — always second series
        "#3D3530",  # Deep Brown — tertiary
        "#8B8680",  # Warm Gray — quaternary
        "#E8E6DD",  # Beige — light fill
        "#B5533C",  # Alert Red — risk series only
    ])


# ─────────────────────────────────────────────────────────────────────────────
# Typography (ARCHITECTURE.md §7.4)
# ─────────────────────────────────────────────────────────────────────────────


class TypographyConfig(BaseModel):
    """HYPERION's two-font system. Only two. One for headers, one for body.

    This is a design constraint, not a limitation — it creates visual
    consistency. Instrument Serif conveys authority. JetBrains Mono
    is technical, precise, and aligns numbers perfectly in tables.
    """

    header_font: str = "Instrument Serif"
    body_font: str = "JetBrains Mono"

    # Sizes (§7.4)
    cover_title_size: int = 36
    section_header_size: int = 22
    subsection_header_size: int = 14
    body_text_size: int = 10
    caption_size: int = 8
    key_insight_size: int = 11
    data_table_size: int = 9


# ─────────────────────────────────────────────────────────────────────────────
# Main Settings — loads from .env with HYPERION_ prefix
# ─────────────────────────────────────────────────────────────────────────────


class Settings(BaseSettings):
    """HYPERION main settings. Loads from .env with HYPERION_ prefix.

    This is the single source of truth for all runtime configuration.
    Every value maps to an architectural decision documented in ARCHITECTURE.md.
    """

    model_config = SettingsConfigDict(
        env_prefix="HYPERION_",
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Provider API Keys ──
    google_api_key: str = ""
    nvidia_api_key: str = ""
    cerebras_api_key: str = ""
    groq_api_key: str = ""
    mistral_api_key: str = ""

    # ── Provider Base URLs ──
    google_base_url: str = "https://generativelanguage.googleapis.com/v1beta/openai"
    nvidia_base_url: str = "https://integrate.api.nvidia.com/v1"
    cerebras_base_url: str = "https://api.cerebras.ai/v1"
    groq_base_url: str = "https://api.groq.com/openai/v1"
    mistral_base_url: str = "https://api.mistral.ai/v1"

    # ── Paths ──
    vault_path: Path = Path("./vault")
    reports_dir: Path = Path("./reports")
    assets_dir: Path = Path("./assets")

    # ── Quality Gate ──
    quality_threshold: float = 4.0
    max_quality_iterations: int = 2  # P7: capped at ≤2 (was 3)
    quality_source_floor: int = 3   # P7: stop iterating if sources < floor

    # ── Sub-Agent ──
    sub_agent_timeout: int = 300
    max_sub_agents: int = 3

    # ── Wait Gate ──
    budget_reserve: float = 0.20
    rate_limit_cooldown: int = 60
    circuit_breaker_threshold: int = 3
    circuit_breaker_cooldown: int = 300

    # ── Engagement ──
    max_engagement_duration: int = 900

    # ── Stealth Layer 3 (P8 GAP-3): proxy/UA rotation, off by default ──
    stealth_proxy_enabled: bool = False
    stealth_proxy_url: str = ""  # e.g. "http://user:pass@proxy:8080"
    stealth_ua_rotation: bool = False  # rotate UA per request when True

    # ── Tool Paths ──
    searxng_url: str = "http://localhost:8888"
    jina_api_key: str = ""
    obscura_path: str = ""
    flaresolverr_url: str = "http://localhost:8191/v1"
    alpha_vantage_api_key: str = ""
    fred_api_key: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "hyperion_research/1.0"
    unsplash_access_key: str = ""
    semantic_scholar_api_key: str = ""

    # ── Computed Configurations ──
    # These are not loaded from env — they are derived from the architecture

    @property
    def providers(self) -> dict[ProviderType, ProviderConfig]:
        """Return all provider configurations with their model matrices."""
        return {
            ProviderType.GOOGLE: ProviderConfig(
                api_key=self.google_api_key,
                base_url=self.google_base_url,
                models=GOOGLE_MODELS,
            ),
            ProviderType.NVIDIA: ProviderConfig(
                api_key=self.nvidia_api_key,
                base_url=self.nvidia_base_url,
                models=NVIDIA_MODELS,
            ),
            ProviderType.CEREBRAS: ProviderConfig(
                api_key=self.cerebras_api_key,
                base_url=self.cerebras_base_url,
                models=CEREBRAS_MODELS,
            ),
            ProviderType.GROQ: ProviderConfig(
                api_key=self.groq_api_key,
                base_url=self.groq_base_url,
                models=GROQ_MODELS,
            ),
            ProviderType.MISTRAL: ProviderConfig(
                api_key=self.mistral_api_key,
                base_url=self.mistral_base_url,
                models=MISTRAL_MODELS,
            ),
        }

    @property
    def wait_gate(self) -> WaitGateConfig:
        return WaitGateConfig(
            budget_reserve=self.budget_reserve,
            rate_limit_cooldown=self.rate_limit_cooldown,
            circuit_breaker_threshold=self.circuit_breaker_threshold,
            circuit_breaker_cooldown=self.circuit_breaker_cooldown,
        )

    @property
    def quality_gate(self) -> QualityGateConfig:
        return QualityGateConfig(
            threshold=self.quality_threshold,
            max_iterations=self.max_quality_iterations,
        )

    @property
    def sub_agent(self) -> SubAgentConfig:
        return SubAgentConfig(
            max_per_specialist=self.max_sub_agents,
            timeout_seconds=self.sub_agent_timeout,
        )

    @property
    def engagement(self) -> EngagementConfig:
        return EngagementConfig(
            max_duration_seconds=self.max_engagement_duration,
        )

    @property
    def tool_paths(self) -> ToolPathsConfig:
        return ToolPathsConfig(
            searxng_url=self.searxng_url,
            jina_api_key=self.jina_api_key,
            obscura_path=self.obscura_path,
            flaresolverr_url=self.flaresolverr_url,
            alpha_vantage_api_key=self.alpha_vantage_api_key,
            fred_api_key=self.fred_api_key,
            reddit_client_id=self.reddit_client_id,
            reddit_client_secret=self.reddit_client_secret,
            reddit_user_agent=self.reddit_user_agent,
            unsplash_access_key=self.unsplash_access_key,
            semantic_scholar_api_key=self.semantic_scholar_api_key,
        )

    @property
    def brand(self) -> ColorSystem:
        return ColorSystem()

    @property
    def colors(self) -> ColorSystem:
        return ColorSystem()

    @property
    def typography(self) -> TypographyConfig:
        return TypographyConfig()

    @property
    def all_models(self) -> list[ModelSpec]:
        """Return all model specs across all providers (non-deprecated)."""
        models: list[ModelSpec] = []
        models.extend(GOOGLE_MODELS)
        models.extend(NVIDIA_MODELS)
        models.extend(CEREBRAS_MODELS)
        models.extend(GROQ_MODELS)
        models.extend(MISTRAL_MODELS)
        return [m for m in models if not m.deprecated]

    def get_models_for_tier(self, tier: ModelTier) -> list[ModelSpec]:
        """Return all non-deprecated models across all providers for a given tier."""
        return [m for m in self.all_models if m.tier == tier]

    @field_validator("vault_path", "reports_dir", "assets_dir", mode="before")
    @classmethod
    def validate_paths(cls, v: Any) -> Path:
        """Ensure paths are Path objects."""
        if isinstance(v, str):
            return Path(v)
        return v


# ─────────────────────────────────────────────────────────────────────────────
# Singleton access
# ─────────────────────────────────────────────────────────────────────────────


_settings: Settings | None = None


def get_settings() -> Settings:
    """Get the singleton Settings instance. Loads from .env on first access."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings() -> None:
    """Reset the singleton — useful for testing."""
    global _settings
    _settings = None
