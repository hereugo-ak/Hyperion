"""HYPERION agent roster — the full cast and what each one actually does.

Kept as a static, dependency-light table (no pydantic import) so the TUI layer
stays importable in isolation. Order follows the engagement lifecycle:
orchestration → specialists → support → delivery.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentInfo:
    key: str        # internal AgentName.value
    badge: str      # short uppercase tag shown in the log
    name: str       # human display name
    group: str      # ORCHESTRATION / SPECIALISTS / SUPPORT / DELIVERY
    ability: str    # one-line description of what it can do


ROSTER: list[AgentInfo] = [
    # ── Orchestration ────────────────────────────────────────────────────────
    AgentInfo("engagement_director", "DIRECTOR", "Engagement Director", "ORCHESTRATION",
              "decomposes the objective & routes specialists"),
    AgentInfo("synthesis_lead", "SYNTHESIS", "Synthesis Lead", "ORCHESTRATION",
              "reconciles findings into one recommendation"),
    # ── Specialists ──────────────────────────────────────────────────────────
    AgentInfo("market_analyst", "MARKET", "Market Analyst", "SPECIALISTS",
              "TAM/SAM/SOM sizing & CAGR triangulation"),
    AgentInfo("competitive_intel", "COMPETE", "Competitive Intel", "SPECIALISTS",
              "incumbents, entrants, moats & share maps"),
    AgentInfo("financial_analyst", "FINANCE", "Financial Analyst", "SPECIALISTS",
              "unit economics, capex, margins & ROI"),
    AgentInfo("risk_analyst", "RISK", "Risk Analyst", "SPECIALISTS",
              "policy, supply-chain & FX risk + mitigations"),
    AgentInfo("technology_analyst", "TECH", "Technology Analyst", "SPECIALISTS",
              "tech readiness, architecture & build-vs-buy"),
    AgentInfo("operations_analyst", "OPS", "Operations Analyst", "SPECIALISTS",
              "footprint, logistics & operating model"),
    AgentInfo("regulatory_analyst", "REGULATORY", "Regulatory Analyst", "SPECIALISTS",
              "licensing, compliance & incentives"),
    AgentInfo("sustainability_analyst", "ESG", "Sustainability Analyst", "SPECIALISTS",
              "ESG exposure, emissions & reporting"),
    AgentInfo("consumer_insights", "CONSUMER", "Consumer Insights", "SPECIALISTS",
              "segments, willingness-to-pay & sentiment"),
    AgentInfo("ma_analyst", "M&A", "M&A Analyst", "SPECIALISTS",
              "targets, comparables & synergy assessment"),
    AgentInfo("innovation_analyst", "INNOVATE", "Innovation Analyst", "SPECIALISTS",
              "emerging tech, patents & disruption vectors"),
    AgentInfo("strategy_analyst", "STRATEGY", "Strategy Analyst", "SPECIALISTS",
              "entry modes, sequencing & optionality"),
    # ── Support ──────────────────────────────────────────────────────────────
    AgentInfo("research_librarian", "LIBRARY", "Research Librarian", "SUPPORT",
              "sources & curates evidence from the web"),
    AgentInfo("fact_checker", "FACTCHECK", "Fact Checker", "SUPPORT",
              "verifies claims & flags weak citations"),
    AgentInfo("data_visualizer", "VISUAL", "Data Visualizer", "SUPPORT",
              "builds charts, tables & comparison matrices"),
    AgentInfo("quality_gate", "QUALITY", "Quality Gate", "SUPPORT",
              "scores rigor & coverage; rejects thin work"),
    # ── Delivery ─────────────────────────────────────────────────────────────
    AgentInfo("presentation_designer", "DESIGNER", "Presentation Designer", "DELIVERY",
              "structures the deck, storyline & summary"),
    AgentInfo("render_engine", "RENDER", "Render Engine", "DELIVERY",
              "typesets the final PDF with charts"),
]

GROUP_ORDER = ["ORCHESTRATION", "SPECIALISTS", "SUPPORT", "DELIVERY"]


def by_group() -> dict[str, list[AgentInfo]]:
    out: dict[str, list[AgentInfo]] = {g: [] for g in GROUP_ORDER}
    for a in ROSTER:
        out.setdefault(a.group, []).append(a)
    return out


def get(key: str) -> AgentInfo | None:
    for a in ROSTER:
        if a.key == key:
            return a
    return None
