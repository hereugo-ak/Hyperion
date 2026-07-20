"""HYPERION TUI theme — spec §4 colour system.

24-bit truecolor. No pure white (#FFFFFF), no pure black (#000000) — both read
as cheap on truecolor terminals (§4.2). Every colour that appears on screen is
declared here; nothing else is allowed (§16 acceptance checklist).
"""

from __future__ import annotations

# NOTE: theme.py deliberately does NOT import hyperion.schemas / hyperion.config.
# Rendering colours must never drag in pydantic / the settings stack. Agent names
# are referenced as plain strings (matching AgentName.value) so the motion + theme
# layer stays dependency-light and importable in isolation.

# ── §4.1 Palette ────────────────────────────────────────────────────────────

BG_CANVAS = "#0A0E1A"
BG_SURFACE = "#111629"
BORDER_SUBTLE = "#2A3350"
BORDER_BRAND = "#8B5CF6"  # used at 30% via blending

TEXT_PRIMARY = "#E4E9F2"
TEXT_SECONDARY = "#A8B3CF"
TEXT_DIM = "#6B7A99"
TEXT_GHOST = "#4A5878"

BRAND_CYAN = "#00D9FF"
BRAND_VIOLET = "#8B5CF6"
BRAND_MAGENTA = "#F0ABFC"

SIG_SUCCESS = "#10D9A0"
SIG_WARN = "#FFB627"
SIG_ERROR = "#FF5C7A"
SIG_INFO = "#7EE5FF"

# ── Logo gradient stops (§3.1): cyan → violet → soft magenta ────────────────

LOGO_STOPS = [BRAND_CYAN, BRAND_VIOLET, BRAND_MAGENTA]
LOGO_DIM = "#3A4670"  # dim monochrome pre-sweep state (§3.2)

# ── Badge vocabulary (§7.1) + agent-specific tags (§7.2) ────────────────────

BADGE_COLORS: dict[str, str] = {
    # Status vocabulary
    "READY": SIG_SUCCESS,
    "THINKING": BRAND_VIOLET,
    "PLAN": BRAND_VIOLET,
    "AGENT": BRAND_CYAN,
    "TOOL": SIG_WARN,
    "STREAM": SIG_INFO,
    "DONE": SIG_SUCCESS,
    "WARN": SIG_WARN,
    "ERROR": SIG_ERROR,
    "HANDOFF": BRAND_MAGENTA,
    "SYSTEM": TEXT_DIM,
    "USER": BRAND_MAGENTA,
    # Roster (deterministic colours from the accent ramp)
    "ORCHESTRATOR": BRAND_VIOLET,
    "DIRECTOR": BRAND_VIOLET,
    "ANALYST": BRAND_CYAN,
    "RESEARCHER": SIG_INFO,
    "STRATEGIST": BRAND_VIOLET,
    "CRITIC": SIG_ERROR,
    "SYNTHESIZER": BRAND_MAGENTA,
    "SYNTHESIS": BRAND_MAGENTA,
    "QUALITY": SIG_WARN,
    "FACTCHECK": SIG_INFO,
    "DESIGNER": BRAND_MAGENTA,
    "VISUAL": BRAND_CYAN,
    "RENDER": TEXT_SECONDARY,
}

# Map internal agent name (AgentName.value strings) → short badge label (§7.2).
AGENT_BADGE: dict[str, str] = {
    "engagement_director": "DIRECTOR",
    "synthesis_lead": "SYNTHESIS",
    "market_analyst": "MARKET",
    "competitive_intel": "COMPETE",
    "financial_analyst": "FINANCE",
    "risk_analyst": "RISK",
    "technology_analyst": "TECH",
    "operations_analyst": "OPS",
    "regulatory_analyst": "REGULATORY",
    "sustainability_analyst": "ESG",
    "consumer_insights": "CONSUMER",
    "ma_analyst": "M&A",
    "innovation_analyst": "INNOVATE",
    "strategy_analyst": "STRATEGY",
    "research_librarian": "LIBRARY",
    "fact_checker": "FACTCHECK",
    "data_visualizer": "VISUAL",
    "quality_gate": "QUALITY",
    "presentation_designer": "DESIGNER",
    "render_engine": "RENDER",
}

# Deterministic accent per specialist from the 3-accent ramp (§7.2 / §4.2:
# never more than 3 accents visible — we cycle the brand trio + info).
_ACCENT_RAMP = [BRAND_CYAN, BRAND_VIOLET, BRAND_MAGENTA, SIG_INFO]


def badge_color(label: str) -> str:
    """Resolve a badge label → colour, with a deterministic fallback."""
    up = label.upper()
    if up in BADGE_COLORS:
        return BADGE_COLORS[up]
    # deterministic hash into the accent ramp
    idx = sum(ord(c) for c in up) % len(_ACCENT_RAMP)
    return _ACCENT_RAMP[idx]


def agent_badge(agent_value: str) -> str:
    """Internal agent name → its uppercase badge label."""
    return AGENT_BADGE.get(agent_value, agent_value.upper().replace("_", " ")[:10])
