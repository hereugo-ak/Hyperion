"""HYPERION TUI theme — Claude / Anthropic brand palette.

Warm, editorial, calm. Clay-orange accent on a soft ink canvas with cream text.
No neon. No pure white / pure black. This mirrors the Claude product surface
(dark: #141413 canvas, #faf9f5 text, #d97757 clay accent).

Reference palette (Anthropic brand):
    Dark      #141413        Light/cream   #faf9f5
    Surface   #1F1E1D        Mid gray      #b0aea5
    Hairline  #2A2926        Light gray    #e8e6dc
    Clay      #d97757  ← signature accent   Crail #c15f3c (deep clay)
    Blue      #6a9bcc        Green         #7d9367        Gold #ca9a5a
"""

from __future__ import annotations

# ── Canvas & surfaces ───────────────────────────────────────────────────────

BG_CANVAS = "#141413"      # Anthropic Dark — the whole terminal canvas
BG_SURFACE = "#1F1E1D"     # slightly raised panels (roster / metrics)
BG_SUNKEN = "#100F0E"      # code / quote wells
BORDER_SUBTLE = "#2A2926"  # hairline dividers
BORDER_BRAND = "#d97757"   # clay border when we want to draw the eye

# ── Text ramp (warm neutrals) ────────────────────────────────────────────────

TEXT_PRIMARY = "#F4F3EE"   # Pampas — primary reading text
TEXT_SECONDARY = "#C9C6BC"  # secondary
TEXT_DIM = "#B1ADA1"       # Cloudy — labels / meta
TEXT_GHOST = "#6E6B63"     # faint scaffolding

# ── Accents (Claude signature clay + a small supporting set) ─────────────────

CLAY = "#d97757"           # THE accent — Claude orange
CLAY_DEEP = "#c15f3c"      # Crail — deeper clay for gradient tail
CLAY_SOFT = "#e0a08a"      # light clay for gradient head
SKY = "#6a9bcc"            # calm blue (info / links)
SAGE = "#7d9367"           # muted green (success)
GOLD = "#ca9a5a"           # muted gold (warn / tools)
ROSE = "#c96a6a"           # muted rose (error)

# Back-compat aliases (old code referenced BRAND_* / SIG_*). All warm now.
BRAND_CYAN = CLAY
BRAND_VIOLET = CLAY_DEEP
BRAND_MAGENTA = CLAY_SOFT
SIG_SUCCESS = SAGE
SIG_WARN = GOLD
SIG_ERROR = ROSE
SIG_INFO = SKY

# ── Logo gradient: soft clay → clay → deep clay (monochromatic, premium) ─────

LOGO_STOPS = [CLAY_SOFT, CLAY, CLAY_DEEP]
LOGO_DIM = "#4A4640"       # pre-sweep dim state

# ── Badge vocabulary + agent-specific tags ───────────────────────────────────
# Restrained: clay for agent/thinking, sage/gold/rose/sky for signals, cloudy
# for system. We intentionally do NOT rainbow every agent — calm > carnival.

BADGE_COLORS: dict[str, str] = {
    "READY": SAGE,
    "THINKING": CLAY,
    "PLAN": CLAY,
    "AGENT": CLAY,
    "TOOL": GOLD,
    "STREAM": SKY,
    "DONE": SAGE,
    "WARN": GOLD,
    "ERROR": ROSE,
    "HANDOFF": SKY,
    "SYSTEM": TEXT_DIM,
    "USER": CLAY,
    "ORCHESTRATOR": CLAY,
    "DIRECTOR": CLAY,
    "ANALYST": CLAY,
    "RESEARCHER": SKY,
    "STRATEGIST": CLAY,
    "CRITIC": ROSE,
    "SYNTHESIZER": CLAY_DEEP,
    "SYNTHESIS": CLAY_DEEP,
    "QUALITY": GOLD,
    "FACTCHECK": SKY,
    "DESIGNER": CLAY_SOFT,
    "VISUAL": SKY,
    "RENDER": TEXT_SECONDARY,
    "MARKET": CLAY,
    "COMPETE": SKY,
    "FINANCE": SAGE,
    "RISK": ROSE,
    "REGULATORY": GOLD,
    "TECH": SKY,
    "OPS": TEXT_SECONDARY,
    "ESG": SAGE,
    "CONSUMER": CLAY_SOFT,
    "M&A": GOLD,
    "INNOVATE": CLAY,
    "STRATEGY": CLAY,
    "LIBRARY": SKY,
}

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

_ACCENT_RAMP = [CLAY, SKY, SAGE, GOLD]


def badge_color(label: str) -> str:
    """Resolve a badge label → colour, with a deterministic fallback."""
    up = label.upper()
    if up in BADGE_COLORS:
        return BADGE_COLORS[up]
    idx = sum(ord(c) for c in up) % len(_ACCENT_RAMP)
    return _ACCENT_RAMP[idx]


def agent_badge(agent_value: str) -> str:
    """Internal agent name → its uppercase badge label."""
    return AGENT_BADGE.get(agent_value, agent_value.upper().replace("_", " ")[:10])
