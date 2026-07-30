"""
HYPERION Synthesis Lead — Agent 2, the senior consultant.

This is NOT a summarizer. This is the most intellectually demanding role
in the system. The Synthesis Lead:

- Holds 4-6 specialists' findings in mind simultaneously (DEEP tier,
  250K context window)
- Identifies contradictions between agents (data conflict, interpretation
  conflict, scope conflict)
- Resolves contradictions evidence-weighted, NOT by averaging
- Calibrates system-level confidence (weakest critical link dominates)
- Produces a coherent narrative synthesis with a clear recommendation

A summarizer lists what each agent found. A synthesizer says "Market says
⟨TAM_FIGURE⟩, Financial says too small, but Financial's model assumes
⟨LOW_PENETRATION⟩ while Market's data supports ⟨HIGH_PENETRATION⟩ — at
⟨HIGH_PENETRATION⟩ the market is viable. The recommendation is ⟨VERDICT⟩,
with the critical assumption being ⟨PIVOT_ASSUMPTION⟩. If
⟨PIVOT_ASSUMPTION⟩ falls below ⟨FLIP_THRESHOLD⟩, the recommendation flips
to ⟨OPPOSITE_VERDICT⟩." That is synthesis. (§4.3, Agent 2)

D-02: the concrete numbers that used to illustrate this paragraph leaked
into delivered reports verbatim (the quality loop regurgitated them over
the degradation notice). The ⟨…⟩ placeholders show SHAPE, never values —
they are not transcriber-bait because they are obviously not data.

Model Tier: DEEP (Gemini 3.1 Flash Lite — 250K context window for
holding all findings simultaneously)
Tools: Second Brain (retrieve prior engagements for pattern matching),
       all specialist findings (read-only via AgentBus)
Sub-agents: Max 1 — for contradiction resolution deep dives
Output: FinalReport (the single most important data structure in HYPERION)

Methodology (§4.3, Agent 2):
1. Collect all specialist findings from AgentBus
2. Build a finding matrix (agent × finding × evidence × confidence)
3. Identify contradictions and classify them
4. Resolve contradictions (evidence-weighted, not averaging)
5. Identify the critical path to the recommendation
6. Draft the recommendation with supporting evidence chain
7. Calibrate system confidence level
8. Produce FinalReport model

Quality Gate Loop:
After producing FinalReport, the Quality Gate scores it on a 10-dimension
rubric. If score < 4.0, the Synthesis Lead receives specific gap feedback
and iterates — up to 3 times max. Each iteration targets the specific
dimensions that scored below 4. This is NOT a generic "try again" — it
is targeted refinement based on actionable feedback. (§4.5, Agent 18)
(§4.3, §0.1)
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import Channel, MessageType
from hyperion.config import ModelTier
from hyperion.output.page_budget import plan_budget
from hyperion.router.budget import TaskUrgency
from hyperion.schemas.agents import (
    AgentName,
    AgentRole,
    AgentSpec,
    AgentState,
    SkillSpec,
    SubAgentSpec,
    ToolName,
)
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    Contradiction,
    ContradictionType,
    FactCheckReport,
    FinalReport,
    KeyFinding,
    QualityScore,
    Recommendation,
    Source,
    SourceCredibility,
)
from hyperion.schemas.workflow import WorkflowDAG

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


SYNTHESIS_LEAD_SPEC = AgentSpec(
    name=AgentName.SYNTHESIS_LEAD,
    role=AgentRole.CORE,
    display_name="Synthesis Lead",
    model_tier=ModelTier.DEEP,
    tools=[
        ToolName.SECOND_BRAIN,
    ],
    skills=[
        SkillSpec(
            name="Cross-source reconciliation",
            description=(
                "When Market Analyst reports ⟨TAM_FIGURE⟩ and Financial Analyst says "
                "'the market is too small to justify entry,' the Synthesis Lead "
                "identifies the contradiction, determines which finding is better "
                "supported by evidence, and resolves it in the final recommendation. "
                "This is NOT averaging — it is evidence-weighted resolution. "
            ),
            inputs=["all_specialist_findings", "fact_check_report"],
            outputs=["contradiction_matrix", "resolved_contradictions"],
        ),
        SkillSpec(
            name="Contradiction resolution",
            description=(
                "Explicitly maps contradictions between agents on a contradiction "
                "matrix. Each contradiction is classified as: data conflict (different "
                "numbers for the same metric), interpretation conflict (same data, "
                "different conclusions), or scope conflict (agents analyzed different "
                "scopes). Each is resolved evidence-weighted, not by averaging."
            ),
            inputs=["specialist_findings", "fact_check_report"],
            outputs=["typed_contradictions", "resolutions", "evidence_weighted_winners"],
        ),
        SkillSpec(
            name="Confidence calibration",
            description=(
                "Aggregates individual agent confidence scores into a system-level "
                "confidence with domain-weighted breakdown. If Market is HIGH "
                "confidence but Regulatory is LOW confidence, the system confidence "
                "reflects the weakest critical link — not an average."
            ),
            inputs=["per_agent_confidence_scores", "contradiction_count"],
            outputs=["system_confidence", "per_domain_confidence_breakdown"],
        ),
        SkillSpec(
            name="Narrative synthesis",
            description=(
                "Produces a coherent narrative that weaves all findings into a single "
                "story with a clear recommendation, supporting evidence, and acknowledged "
                "limitations. Not a summary — a synthesis. A summarizer lists what each "
                "agent found. A synthesizer identifies the through-line that connects "
                "all findings into one recommendation."
            ),
            inputs=["resolved_findings", "critical_path", "system_confidence"],
            outputs=["executive_summary", "analysis_sections", "recommendation"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Synthesis Lead — the senior consultant who reconciles "
        "all specialist findings into a single, coherent recommendation.\n\n"
        "This is the most intellectually demanding role in the system. You hold "
        "4-6 specialists' findings simultaneously, identify contradictions, resolve "
        "them evidence-weighted (NOT by averaging), and produce one answer.\n\n"
        "You are NOT a summarizer. A summarizer lists what each agent found. "
        "You synthesize. You say: 'Market says ⟨TAM_FIGURE⟩, Financial says too small, "
        "but Financial's model assumes ⟨LOW_PENETRATION⟩ while Market's data supports "
        "⟨HIGH_PENETRATION⟩ — at ⟨HIGH_PENETRATION⟩ the market is viable. The "
        "recommendation is ⟨VERDICT⟩, with the critical assumption being "
        "⟨PIVOT_ASSUMPTION⟩. If ⟨PIVOT_ASSUMPTION⟩ falls below ⟨FLIP_THRESHOLD⟩, the "
        "recommendation flips to ⟨OPPOSITE_VERDICT⟩.'\n\n"
        "HARD RULE: ⟨…⟩ are placeholders showing SHAPE, never values. Every number "
        "you emit must appear verbatim in the findings above. If the findings "
        "contain no numbers, write the analysis without numbers and say the "
        "evidence is qualitative — do NOT invent figures to fit the shape.\n\n"
        "Your methodology:\n"
        "1. Build a finding matrix (agent × finding × evidence × confidence)\n"
        "2. Identify contradictions and classify them (data/interpretation/scope)\n"
        "3. Resolve contradictions evidence-weighted — the finding with more credible "
        "   sources and higher confidence wins. Document WHY.\n"
        "4. Identify the critical path — the 2-3 findings that determine the recommendation\n"
        "5. Draft the recommendation with a clear evidence chain\n"
        "6. Calibrate system confidence — the weakest critical link dominates\n"
        "7. Produce the FinalReport with executive summary, sections, and limitations\n\n"
        "Rules:\n"
        "- Every claim in the report must trace to a specialist finding with a source\n"
        "- Every contradiction must be explicitly resolved, not glossed over\n"
        "- Critical assumptions are assumptions that would FLIP the recommendation if wrong\n"
        "- The executive summary must stand alone — a CEO reads only that page\n"
        "- Limitations are what you couldn't research, not what you chose to skip\n"
        "- The recommendation must be actionable: ENTER, NO-GO, CONDITIONAL, etc.\n"
        "- CONDITIONAL means: proceed IF these specific conditions are met\n"
        "- Never hedge. 'Might possibly perhaps' is banned. Be confident or be specific "
        "about what's uncertain.\n\n"
        "You can spawn 1 sub-agent for contradiction resolution — if two agents' "
        "findings are deeply contradictory, a sub-agent does a focused deep dive "
        "on the specific point of conflict.\n\n"
        "You receive a QualityScore from the Quality Gate. If score < 4.0, you "
        "iterate — up to 3 times. Each iteration targets the specific dimensions "
        "that scored below 4. This is targeted refinement, not 'try again.'"
    ),
    spawn_condition="Always active (core agent) — activated after all specialists complete",
    max_sub_agents=1,
    output_model="FinalReport",
)


# ─────────────────────────────────────────────────────────────────────────────
# Synthesis Lead Agent
# ─────────────────────────────────────────────────────────────────────────────


class SynthesisLead(BaseAgent):
    """Agent 2: The senior consultant who synthesizes all findings.

    The Synthesis Lead is NOT a summarizer. It reconciles contradictions,
    calibrates confidence, and produces a single coherent recommendation.
    It runs at DEEP tier (250K context window) because it must hold all
    specialist findings simultaneously. (§4.3, Agent 2)

    Lifecycle:
    1. Subscribes to FINDINGS channel — collects all specialist findings
    2. When all specialists complete (signaled by Engagement Director),
       begins synthesis
    3. Builds finding matrix, identifies contradictions, resolves them
    4. Produces FinalReport
    5. Receives QualityScore from Quality Gate
    6. If score < 4.0, iterates (max 3 times) with targeted fixes
    7. If score >= 4.0 or max iterations reached, delivers FinalReport
    """

    #: How many vault notes are rendered into the precedent block. The block is
    #: advisory context, not evidence; an unbounded splice would crowd the real
    #: findings out of the DEEP-tier context window.
    PRIOR_PATTERN_LIMIT: int = 5

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or SYNTHESIS_LEAD_SPEC, bus=bus, router=router)

        # Collected findings from all specialists (via AgentBus)
        self._collected_findings: list[KeyFinding] = []
        self._findings_by_agent: dict[str, list[KeyFinding]] = {}

        # Fact check report (received from Fact Checker)
        self._fact_check_report: FactCheckReport | None = None

        # Quality gate score (received from Quality Gate)
        self._quality_score: QualityScore | None = None
        self._quality_iteration: int = 0
        self._max_quality_iterations: int = 2  # P7: capped at ≤2 (was 3)

        # The current FinalReport (iteratively refined)
        self._current_report: FinalReport | None = None

        # D-01: analysis sections are built from findings BEFORE the
        # recommendation call and parked here immediately. If any later step
        # raises, _minimal_report() carries them into the degraded report —
        # a synthesis failure costs the recommendation, never the analysis.
        self._partial_sections: list[AnalysisSection] = []

        # D-02: structural integrity violations observed by the quality loop
        # (e.g. section_updates for a report whose body was never built).
        # Recorded, never swallowed — the audit's lesson is that silent
        # degradation is how a crash became a confident PDF.
        self._recorded_failures: list[str] = []

        # The workflow DAG (for knowing which agents participated)
        self._dag: WorkflowDAG | None = None

        # Contradiction count (set during synthesis)
        self._contradiction_count: int = 0

        # Engagement metadata
        self._engagement_id: str = ""
        self._question: str = ""

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling — collect findings, fact check, quality score
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Synthesis Lead listens to:
        - FINDINGS: collects all specialist findings for synthesis
        - HANDOFF: receives FactCheckReport from Fact Checker, QualityScore
          from Quality Gate, and the engagement DAG from Engagement Director
        """
        if msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                agent_name = msg.sender.value
                self._collected_findings.append(finding)
                if agent_name not in self._findings_by_agent:
                    self._findings_by_agent[agent_name] = []
                self._findings_by_agent[agent_name].append(finding)
            else:
                # Specialists publish full analysis objects via bus.publish
                # without a "finding" key — extract summary data as a synthetic
                # KeyFinding so the synthesis lead has access to quantitative
                # results (TAM, DCF, WACC, etc.) for narrative generation.
                payload = msg.payload
                agent_name = msg.sender.value

                # Map analysis payload keys to readable content
                analysis_keys = [
                    "market_analysis", "financial_analysis", "risk_analysis",
                    "technology_assessment", "operations_analysis",
                    "regulatory_analysis", "sustainability_analysis",
                    "consumer_insights", "ma_analysis", "innovation_analysis",
                    "strategy_analysis", "competitive_landscape",
                ]
                for key in analysis_keys:
                    if key in payload:
                        try:
                            summary = json.dumps(payload[key], default=str)[:3000]
                        except (TypeError, ValueError):
                            summary = str(payload[key])[:3000]

                        # Extract headline metrics from common fields
                        headlines = []
                        headline_title = ""
                        analysis_data = payload.get(key, {})
                        if isinstance(analysis_data, dict):
                            # Try to build a meaningful title from key metrics
                            for title_key, label in [
                                ("tam_triangulated", "TAM"),
                                ("dcf_valuation", "DCF Valuation"),
                                ("comp_valuation", "Comp Valuation"),
                                ("market_maturity", "Market Maturity"),
                                ("residual_risk_summary", "Residual Risk"),
                                ("build_vs_buy", "Build vs Buy"),
                            ]:
                                val = analysis_data.get(title_key)
                                if val is not None:
                                    val_str = str(val)
                                    if len(val_str) > 120:
                                        val_str = val_str[:117] + "..."
                                    headlines.append(f"{label}: {val_str}")
                                    if not headline_title:
                                        headline_title = f"{label}: {val_str}"

                            # Extract key value drivers as headlines
                            kvd = analysis_data.get("key_value_drivers", [])
                            if isinstance(kvd, list):
                                for vd in kvd[:3]:
                                    headlines.append(f"Key Value Driver — {vd}")
                                    if not headline_title:
                                        headline_title = f"Key Value Driver — {vd}"

                        if not headline_title:
                            # Fallback: use confidence or risk count
                            for fb_key in ("confidence", "risk_count", "competitor_count", "white_space_count"):
                                fb_val = payload.get(fb_key)
                                if fb_val is not None:
                                    headline_title = f"{fb_key.replace('_', ' ').title()}: {fb_val}"
                                    break

                        if not headline_title:
                            headline_title = f"{agent_name.replace('_', ' ').title()} Analysis"

                        content = summary
                        if headlines:
                            content = "\n".join(headlines) + "\n\n" + summary

                        synthetic = KeyFinding(
                            id=f"summary_{agent_name}_{uuid.uuid4().hex[:8]}",
                            agent=agent_name,
                            finding_type="analysis_summary",
                            title=headline_title[:200],
                            content=content,
                            sources=[],
                            confidence=ConfidenceLevel.MEDIUM,
                            implications=headlines[0] if headlines else "",
                        )
                        self._collected_findings.append(synthetic)
                        if agent_name not in self._findings_by_agent:
                            self._findings_by_agent[agent_name] = []
                        self._findings_by_agent[agent_name].append(synthetic)
                        break

        elif msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            task_type = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            # Only process handoffs directed at the Synthesis Lead
            if to_agent != self.name.value:
                return

            if task_type == "fact_check_report":
                report_data = context_bundle.get("report")
                if report_data:
                    try:
                        self._fact_check_report = FactCheckReport.model_validate(report_data)
                    except (ValueError, TypeError) as exc:
                        # A fact-check report that fails validation leaves the
                        # Synthesis Lead blind to hallucinated citations — the
                        # #1 quality risk per the FactCheckReport schema.
                        logger.warning(
                            "fact_check_report failed validation and was discarded: %s: %s",
                            type(exc).__name__, exc,
                        )

            elif task_type == "quality_score":
                score_data = context_bundle.get("score")
                if score_data:
                    try:
                        self._quality_score = QualityScore.model_validate(score_data)
                        self._quality_iteration = self._quality_score.iteration
                    except (ValueError, TypeError) as exc:
                        # A dropped quality score leaves the revision loop on
                        # stale iteration state — record it.
                        logger.warning(
                            "quality_score failed validation and was discarded: %s: %s",
                            type(exc).__name__, exc,
                        )

            elif task_type == "engagement_dag":
                dag_data = context_bundle.get("dag")
                if dag_data:
                    try:
                        self._dag = WorkflowDAG.model_validate(dag_data)
                    except (ValueError, TypeError) as exc:
                        logger.warning(
                            "engagement_dag failed validation and was discarded: %s: %s",
                            type(exc).__name__, exc,
                        )

            elif task_type == "start_synthesis":
                # Engagement Director signals all specialists are done
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._question = context_bundle.get("question", "")

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Collect findings (already done via bus subscription)
    # ─────────────────────────────────────────────────────────────────────

    def _get_all_findings(self) -> list[KeyFinding]:
        """Get all collected findings sorted by confidence (highest first)."""
        confidence_order = {
            ConfidenceLevel.HIGH: 0,
            ConfidenceLevel.MEDIUM: 1,
            ConfidenceLevel.LOW: 2,
        }
        return sorted(
            self._collected_findings,
            key=lambda f: confidence_order.get(f.confidence, 3),
        )

    def _get_findings_for_agent(self, agent_name: str) -> list[KeyFinding]:
        """Get all findings from a specific agent."""
        return self._findings_by_agent.get(agent_name, [])

    def _get_participating_agents(self) -> list[str]:
        """Get list of agents that produced findings."""
        return list(self._findings_by_agent.keys())

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Build finding matrix
    # ─────────────────────────────────────────────────────────────────────

    def _build_finding_matrix(self) -> dict[str, Any]:
        """Build a finding matrix: agent × finding × evidence × confidence.

        This is the structured representation of all findings that the
        Synthesis Lead uses to identify contradictions and the critical
        path. It is NOT a flat list — it is a cross-referenced matrix.

        The matrix is a dict keyed by finding_type, containing all findings
        of that type from different agents. This makes contradictions
        visible: if two agents have findings of type 'market_size' with
        different values, that's a data conflict.
        """
        matrix: dict[str, list[dict[str, Any]]] = {}

        for finding in self._collected_findings:
            entry = {
                "agent": finding.agent,
                "title": finding.title,
                "content": finding.content,
                "confidence": finding.confidence.value,
                "sources": [
                    {"url": s.url, "credibility": s.credibility.value}
                    for s in finding.sources
                ],
                "source_count": len(finding.sources),
                "gaps": finding.gaps,
                "implications": finding.implications,
            }

            ftype = finding.finding_type
            if ftype not in matrix:
                matrix[ftype] = []
            matrix[ftype].append(entry)

        return {
            "matrix": matrix,
            "total_findings": len(self._collected_findings),
            "participating_agents": self._get_participating_agents(),
            "finding_types": list(matrix.keys()),
        }

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Identify contradictions
    # ─────────────────────────────────────────────────────────────────────

    def _identify_contradictions(self, matrix: dict[str, Any]) -> list[Contradiction]:
        """Identify contradictions between agents' findings.

        Uses the finding matrix to detect:
        - Data conflicts: same finding_type, different values from different agents
        - Interpretation conflicts: same data, different conclusions
        - Scope conflicts: agents analyzed different scopes

        Also incorporates contradictions from the FactCheckReport if available.
        """
        contradictions: list[Contradiction] = []
        contradiction_id = 0

        # From fact check report
        if self._fact_check_report:
            for fc_contradiction in self._fact_check_report.contradictions:
                contradictions.append(fc_contradiction)

        # From finding matrix — look for same finding_type from different agents
        m = matrix.get("matrix", {})
        for ftype, entries in m.items():
            if len(entries) < 2:
                continue

            # Group by agent
            agents_present = {e["agent"] for e in entries}
            if len(agents_present) < 2:
                continue

            # Compare pairs from different agents
            for i, entry_a in enumerate(entries):
                for j, entry_b in enumerate(entries):
                    if i >= j:
                        continue
                    if entry_a["agent"] == entry_b["agent"]:
                        continue

                    # Check if contents diverge (simple heuristic)
                    content_a = entry_a["content"].lower().strip()
                    content_b = entry_b["content"].lower().strip()
                    if content_a == content_b:
                        continue

                    # Classify contradiction type
                    ctype = self._classify_contradiction(entry_a, entry_b, ftype)

                    contradiction_id += 1
                    contradiction = Contradiction(
                        id=f"contradiction_{contradiction_id}",
                        agent_a=entry_a["agent"],
                        agent_b=entry_b["agent"],
                        finding_a=entry_a["title"],
                        finding_b=entry_b["title"],
                        contradiction_type=ctype,
                    )
                    contradictions.append(contradiction)

        return contradictions

    def _classify_contradiction(
        self,
        entry_a: dict[str, Any],
        entry_b: dict[str, Any],
        finding_type: str,
    ) -> ContradictionType:
        """Classify a contradiction as data, interpretation, or scope conflict.

        - Data conflict: different numbers for the same metric (e.g., market_size)
        - Interpretation conflict: same data, different conclusions
        - Scope conflict: agents analyzed different scopes (geography, segment, etc.)
        """
        # Finding types that are inherently numeric → data conflicts
        numeric_types = {
            "market_size", "tam", "sam", "som", "cagr", "valuation",
            "dcf", "revenue", "margin", "ltv", "cac", "price",
            "cost", "spend", "growth_rate", "penetration_rate",
        }

        if finding_type.lower() in numeric_types:
            return ContradictionType.DATA_CONFLICT

        # Check if agents mention different geographies or segments
        scope_indicators = ["region", "country", "geography", "segment", "market segment"]
        content_a = entry_a["content"].lower()
        content_b = entry_b["content"].lower()
        for indicator in scope_indicators:
            if indicator in content_a or indicator in content_b:
                return ContradictionType.SCOPE_CONFLICT

        # Default: interpretation conflict (same data, different conclusions)
        return ContradictionType.INTERPRETATION_CONFLICT

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Resolve contradictions (evidence-weighted, not averaging)
    # ─────────────────────────────────────────────────────────────────────

    async def _resolve_contradictions(
        self,
        contradictions: list[Contradiction],
        matrix: dict[str, Any],
    ) -> list[Contradiction]:
        """Resolve all contradictions evidence-weighted.

        For each contradiction:
        1. Count sources for each side
        2. Weight by source credibility
        3. Weight by agent confidence
        4. The finding with more credible sources and higher confidence wins
        5. Document the resolution

        If a contradiction is deeply entrenched (both sides have equal
        evidence weight), spawn a sub-agent for a focused deep dive.
        """
        if not contradictions:
            return []

        resolved: list[Contradiction] = []

        for contradiction in contradictions:
            # Find the findings in the matrix
            finding_a = self._find_finding_by_agent_and_title(
                contradiction.agent_a, contradiction.finding_a
            )
            finding_b = self._find_finding_by_agent_and_title(
                contradiction.agent_b, contradiction.finding_b
            )

            if finding_a is None or finding_b is None:
                # Can't resolve what we can't find — mark unresolved
                contradiction.resolution = "Could not locate original findings for resolution"
                resolved.append(contradiction)
                continue

            # Calculate evidence weight for each side
            weight_a = self._calculate_evidence_weight(finding_a)
            weight_b = self._calculate_evidence_weight(finding_b)

            if weight_a > weight_b:
                winner = contradiction.agent_a
                resolution = (
                    f"{contradiction.agent_a}'s finding is better supported "
                    f"(evidence weight: {weight_a:.2f} vs {weight_b:.2f}). "
                    f"{finding_a.implications or 'No implications stated.'}"
                )
            elif weight_b > weight_a:
                winner = contradiction.agent_b
                resolution = (
                    f"{contradiction.agent_b}'s finding is better supported "
                    f"(evidence weight: {weight_b:.2f} vs {weight_a:.2f}). "
                    f"{finding_b.implications or 'No implications stated.'}"
                )
            else:
                # Equal weight — this is a deeply entrenched contradiction
                # Spawn a sub-agent for a focused deep dive (§4.3, Agent 2)
                # Record whether a deep dive actually ran, so the resolution
                # text cannot claim an investigation that never happened —
                # the sub-agent budget may already be spent.
                _specs_before = len(self._sub_agent_specs)
                winner = await self._deep_dive_contradiction(contradiction, finding_a, finding_b)
                if len(self._sub_agent_specs) > _specs_before:
                    resolution = (
                        f"Contradiction was deeply entrenched (equal evidence weight: "
                        f"{weight_a:.2f}). Sub-agent deep dive resolved in favor of {winner}."
                    )
                else:
                    resolution = (
                        f"Contradiction was deeply entrenched (equal evidence weight: "
                        f"{weight_a:.2f}) and the sub-agent research budget was already "
                        f"spent. Resolved in favor of {winner} on source count; treat "
                        f"this resolution as provisional."
                    )

            contradiction.resolution = resolution
            contradiction.evidence_weighted_winner = winner
            contradiction.resolved = True
            resolved.append(contradiction)

        return resolved

    def _find_finding_by_agent_and_title(
        self,
        agent: str,
        title: str,
    ) -> KeyFinding | None:
        """Find a specific finding by agent name and title."""
        for finding in self._collected_findings:
            if finding.agent == agent and finding.title == title:
                return finding
        return None

    def _calculate_evidence_weight(self, finding: KeyFinding) -> float:
        """Calculate evidence weight for a finding.

        Weight = source_count × avg_credibility × confidence_multiplier

        Source credibility hierarchy:
        peer_reviewed=5, government=4, industry_report=3, news=2, blog=1, social_media=0.5

        Confidence multiplier: HIGH=1.5, MEDIUM=1.0, LOW=0.5
        """
        credibility_weights = {
            SourceCredibility.PEER_REVIEWED: 5.0,
            SourceCredibility.GOVERNMENT: 4.0,
            SourceCredibility.INDUSTRY_REPORT: 3.0,
            SourceCredibility.NEWS: 2.0,
            SourceCredibility.BLOG: 1.0,
            SourceCredibility.SOCIAL_MEDIA: 0.5,
        }

        confidence_multipliers = {
            ConfidenceLevel.HIGH: 1.5,
            ConfidenceLevel.MEDIUM: 1.0,
            ConfidenceLevel.LOW: 0.5,
        }

        source_weight = sum(
            credibility_weights.get(s.credibility, 1.0) for s in finding.sources
        )
        confidence_mult = confidence_multipliers.get(finding.confidence, 1.0)

        return source_weight * confidence_mult

    async def _deep_dive_contradiction(
        self,
        contradiction: Contradiction,
        finding_a: KeyFinding,
        finding_b: KeyFinding,
    ) -> str:
        """Spawn a sub-agent for a focused contradiction deep dive.

        Per §4.3: "Can spawn 1 sub-agent for contradiction resolution —
        if two agents' findings are deeply contradictory, a sub-agent
        does a focused deep dive on the specific point of conflict."

        The sub-agent uses FAST tier (not MICRO — contradiction resolution
        requires reasoning) and SearxNG + Jina to independently verify
        the conflicting claims.
        """
        # Short-circuit once the sub-agent budget is spent.
        #
        # This method is called once per evidence-tied contradiction, and a
        # real run can produce many ties. With max_sub_agents=1 only the first
        # call can ever succeed, so every later call did a full round trip just
        # to be refused. Detecting exhaustion up front and going straight to
        # the deterministic source-count tiebreak keeps resolution honest and
        # removes the busywork that fed the escalation storm.
        if len(self._sub_agent_specs) >= self.max_sub_agents:
            self._log(
                "SYNTHESIS: sub-agent budget spent; resolving contradiction "
                f"'{contradiction.finding_a[:60]}' by source count instead of deep dive"
            )
            if len(finding_a.sources) >= len(finding_b.sources):
                return contradiction.agent_a
            return contradiction.agent_b

        sub_question = (
            f"Two agents disagree on '{contradiction.finding_a}':\n"
            f"Agent {contradiction.agent_a} claims: {finding_a.content[:200]}\n"
            f"Agent {contradiction.agent_b} claims: {finding_b.content[:200]}\n"
            f"Independently verify which claim is better supported by evidence."
        )

        sub_spec = SubAgentSpec(
            question=sub_question,
            parent_agent=AgentName.SYNTHESIS_LEAD,
            model_tier=ModelTier.FAST,
            tools=[ToolName.SEARXNG, ToolName.JINA],
            findings_model="KeyFinding",
            timeout_seconds=300,
            context={
                "contradiction_type": contradiction.contradiction_type.value,
                "finding_a_sources": [s.url for s in finding_a.sources],
                "finding_b_sources": [s.url for s in finding_b.sources],
            },
        )

        sub_findings = await self._spawn_sub_agent(sub_spec)

        if not sub_findings:
            # Sub-agent couldn't resolve — default to the finding with more sources
            if len(finding_a.sources) >= len(finding_b.sources):
                return contradiction.agent_a
            return contradiction.agent_b

        # Use the sub-agent's findings to determine the winner
        # The sub-agent's finding should support one side or the other
        sub_content = sub_findings[0].content.lower()
        if contradiction.agent_a.lower() in sub_content or (
            finding_a.content[:50].lower() in sub_content
        ):
            return contradiction.agent_a
        return contradiction.agent_b

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Identify critical path to recommendation
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_critical_path(
        self,
        matrix: dict[str, Any],
        contradictions: list[Contradiction],
    ) -> list[str]:
        """Identify the critical path — the 2-3 findings that determine the recommendation.

        This is NOT all findings. It is the specific findings that, if they changed,
        would flip the recommendation. The Synthesis Lead uses LLM reasoning to
        identify these, because it requires understanding the causal chain from
        findings to recommendation.

        Example: "Market sizing is the critical path because the recommendation
        depends on TAM > $1B. If TAM < $1B, the recommendation flips to NO-GO."
        """
        # Build a summary of all findings for the LLM
        findings_summary = self._format_findings_for_llm()

        prompt = (
            "You are the Synthesis Lead identifying the critical path to the "
            "recommendation.\n\n"
            f"Question: {self._question}\n\n"
            f"All findings:\n{findings_summary}\n\n"
            f"Contradictions found: {len(contradictions)}\n\n"
            "Identify the 2-3 CRITICAL findings — the ones that, if they changed, "
            "would flip the recommendation. These are the findings on the critical "
            "path. Most findings are supporting evidence; only a few are decision-"
            "determinative.\n\n"
            "Return JSON: {\"critical_findings\": [\"finding_title_1\", ...], "
            "\"reasoning\": \"why these are critical\"}"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            # Fallback: use highest-confidence findings as critical path
            all_findings = self._get_all_findings()
            return [f.title for f in all_findings[:3]]

        try:
            data = json.loads(response.content)
            critical = data.get("critical_findings", [])
            if isinstance(critical, list) and critical:
                return [str(c) for c in critical[:3]]
        except (json.JSONDecodeError, ValueError) as exc:
            # P2-11: never a silent pass. The fallback below still runs, but
            # the parse failure is recorded with the output that caused it.
            logger.error(
                "critical-findings JSON parse failed, falling back to "
                "highest-confidence titles: %s: %s (output head: %.120r)",
                type(exc).__name__, exc, response.content,
            )

        # Fallback
        all_findings = self._get_all_findings()
        return [f.title for f in all_findings[:3]]

    def _format_findings_for_llm(self) -> str:
        """Format all findings into a readable summary for LLM prompts."""
        lines: list[str] = []
        for agent, findings in self._findings_by_agent.items():
            lines.append(f"\n=== {agent} ===")
            for f in findings:
                lines.append(
                    f"  [{f.confidence.value.upper()}] {f.title}: {f.content[:200]}"
                )
                if f.implications:
                    lines.append(f"    Implication: {f.implications[:150]}")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Draft recommendation with evidence chain
    # ─────────────────────────────────────────────────────────────────────

    async def _draft_recommendation(
        self,
        critical_path: list[str],
        contradictions: list[Contradiction],
    ) -> dict[str, Any]:
        """Draft the recommendation with supporting evidence chain.

        The recommendation is NOT a guess. It is a structured output with:
        - recommendation type (ENTER, NO_GO, CONDITIONAL, etc.)
        - rationale (the evidence chain)
        - critical assumptions (what would flip it)
        - executive summary (standalone, for the CEO)
        """
        findings_summary = self._format_findings_for_llm()

        contradictions_summary = "\n".join(
            f"- {c.agent_a} vs {c.agent_b}: {c.finding_a} vs {c.finding_b} "
            f"→ Resolved: {c.resolution or 'unresolved'}"
            for c in contradictions
        ) if contradictions else "No contradictions found."

        prompt = (
            "You are the Synthesis Lead drafting the final recommendation.\n\n"
            f"Question: {self._question}\n\n"
            f"All findings:\n{findings_summary}\n\n"
            f"Contradictions:\n{contradictions_summary}\n\n"
            f"Critical path findings: {', '.join(critical_path)}\n\n"
            "Produce the recommendation as JSON with these fields:\n"
            "{\n"
            '  "recommendation": "enter|no_go|conditional|investigate|acquire|do_not_acquire|hold",\n'
            '  "recommendation_rationale": "The evidence chain supporting this recommendation — specific, not generic",\n'
            '  "critical_assumptions": ["assumption1", "assumption2"],\n'
            '  "executive_summary": "Standalone summary for the CEO — recommendation + key findings + critical risks",\n'
            '  "key_findings_titles": ["3-5 finding titles that support the recommendation"]\n'
            "}\n\n"
            "Rules:\n"
            "- The recommendation must follow from the findings, not from generic reasoning\n"
            "- Critical assumptions are assumptions that would FLIP the recommendation if wrong\n"
            "- The executive summary must stand alone — a CEO reads only that page\n"
            "- Be confident. No hedging. If uncertain, use CONDITIONAL with specific conditions\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            return {
                "recommendation": "investigate",
                "recommendation_rationale": "Insufficient data for a definitive recommendation.",
                "critical_assumptions": [],
                "executive_summary": "Further research is needed before a recommendation can be made.",
                "key_findings_titles": [],
            }

        try:
            return json.loads(response.content)
        except (json.JSONDecodeError, ValueError):
            return {
                "recommendation": "investigate",
                "recommendation_rationale": "LLM output parsing failed.",
                "critical_assumptions": [],
                "executive_summary": "Further research is needed.",
                "key_findings_titles": [],
            }

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Calibrate system confidence
    # ─────────────────────────────────────────────────────────────────────

    def _calibrate_confidence(
        self,
        contradictions: list[Contradiction],
    ) -> tuple[ConfidenceLevel, dict[str, ConfidenceLevel]]:
        """Calibrate system-level confidence.

        The system confidence reflects the WEAKEST CRITICAL LINK, not an
        average. If Market is HIGH confidence but Regulatory is LOW
        confidence, and Regulatory is on the critical path, the system
        confidence is LOW. (§4.3, Agent 2)

        Factors that reduce confidence:
        - Unresolved contradictions
        - Low-confidence findings on the critical path
        - Gaps in research
        - Hallucinated citations (from FactCheckReport)
        """
        per_domain: dict[str, ConfidenceLevel] = {}

        # Per-agent confidence (domain = agent)
        for agent, findings in self._findings_by_agent.items():
            if not findings:
                continue
            # Domain confidence = lowest confidence among that agent's findings
            confidence_levels = [f.confidence for f in findings]
            lowest = min(confidence_levels, key=lambda c: {
                ConfidenceLevel.HIGH: 0,
                ConfidenceLevel.MEDIUM: 1,
                ConfidenceLevel.LOW: 2,
            }.get(c, 3))
            per_domain[agent] = lowest

        # System confidence = weakest critical link
        if not per_domain:
            return ConfidenceLevel.LOW, per_domain

        # Count unresolved contradictions — they reduce system confidence
        unresolved = sum(1 for c in contradictions if not c.resolved)
        if unresolved > 0:
            return ConfidenceLevel.LOW, per_domain

        # Check for hallucinated citations from fact check
        if self._fact_check_report and self._fact_check_report.hallucinated_citations:
            return ConfidenceLevel.LOW, per_domain

        # System confidence = lowest domain confidence
        system_confidence = min(
            per_domain.values(),
            key=lambda c: {
                ConfidenceLevel.HIGH: 0,
                ConfidenceLevel.MEDIUM: 1,
                ConfidenceLevel.LOW: 2,
            }.get(c, 3),
        )

        return system_confidence, per_domain

    # ─────────────────────────────────────────────────────────────────────
    # Step 8: Build analysis sections for FinalReport
    # ─────────────────────────────────────────────────────────────────────

    async def _build_analysis_sections(
        self,
        recommendation_data: dict[str, Any] | None = None,
    ) -> list[AnalysisSection]:
        """Build the analysis sections for the FinalReport.

        Each section corresponds to a specialist's analysis, self-contained
        so a reader can jump to any section without reading prior sections.
        Each section has: key insight, body, findings, implications, sources.
        (§6.1)

        D-01: sections depend ONLY on the collected findings, never on the
        recommendation — the ``recommendation_data`` parameter is vestigial
        (kept optional for call-site compatibility) and is not read anywhere
        in this method. That independence is what allows the body to be
        built before the recommendation call and to survive its failure.

        The section body is NOT a concatenation of finding strings. It is
        a deep analytical narrative written by the Synthesis Lead LLM,
        synthesizing all findings into McKinsey/BCG-quality prose with:
        - Context setting and framing
        - Data presentation with specific numbers
        - Interpretation and "so what?" analysis
        - Cross-references to other sections where relevant
        - Clear implications for the recommendation
        """

        # Consulting-style section titles — never use raw agent names as headings
        agent_section_titles = {
            "market_analyst": "Market Landscape",
            "competitive_intel": "Competitive Landscape",
            "financial_analyst": "Financial Viability",
            "risk_analyst": "Risk Assessment",
            "technology_analyst": "Technology Architecture",
            "operations_analyst": "Operational Feasibility",
            "regulatory_analyst": "Regulatory Environment",
            "sustainability_analyst": "Sustainability Assessment",
            "consumer_insights": "Consumer Insights",
            "ma_analyst": "M&A Assessment",
            "innovation_analyst": "Innovation Outlook",
            "strategy_analyst": "Strategic Options",
        }

        def _section_title(agent: str) -> str:
            return agent_section_titles.get(agent, agent.replace("_", " ").title())

        # Fix 4.1: derive the per-section word allocation from the page contract
        # instead of hardcoding it.
        #
        # This function previously restated "2000-4000 words" in four separate
        # prompt strings, with no relationship to the 15-20 page deliverable
        # target. Because the section count is `len(self._findings_by_agent)` —
        # anywhere from 1 to 12 depending on which agents reported — the page
        # count was an emergent accident: the audit measured 36 pages against a
        # stated 20-page ceiling and nothing in the codebase noticed.
        #
        # `plan_budget` inverts that: the target page count is the input, and
        # the words-per-section allocation is what falls out of it. A 3-agent
        # engagement is now asked for long sections and a 10-agent engagement
        # for short ones, so both land near the same page count.
        budget = plan_budget(len(self._findings_by_agent))
        word_clause = budget.prompt_clause()
        # Rejection threshold, in characters, derived from the allocation rather
        # than the old fixed `> 800`. 800 characters is ~130 words, so under the
        # previous rule a section that answered a 2,000-word request with 130
        # words was accepted silently. Half the allocation at ~6 chars/word is a
        # threshold that actually tracks what was asked for; floored at 800 so
        # this can only ever be stricter than the behaviour it replaces.
        min_body_chars = max(800, int(budget.words_per_section * 6 * 0.5))

        if budget.sections_over_capacity:
            # Not silently absorbed: with this many sections the target page
            # count is unreachable even at the minimum viable section length,
            # so the operator should know the deliverable will run long.
            logger.warning(
                "Page budget over capacity: %d sections cannot fit %d pages; "
                "each section pinned to %d words, projecting %d pages",
                budget.section_count,
                budget.target_pages,
                budget.words_per_section,
                budget.projected_pages,
            )
        else:
            logger.info(
                "Page budget: %d sections x %d words -> ~%d pages (target %d)",
                budget.section_count,
                budget.words_per_section,
                budget.projected_pages,
                budget.target_pages,
            )

        async def _build_one_section(
            agent: str,
            findings: list[KeyFinding],
        ) -> AnalysisSection:
            if not findings:
                return AnalysisSection(
                    id=f"section_{agent}",
                    title=_section_title(agent),
                    agent=agent,
                    key_insight="No findings available for this section",
                    body=(
                        f"The {_section_title(agent)} analysis did not produce "
                        f"specific findings for this engagement. This is a data-"
                        f"availability gap, not an absence of analytical relevance."
                    ),
                    findings=[],
                    charts=[],
                    images=[],
                    implications="No specific implications could be derived — data gap.",
                    sources=[],
                    confidence=ConfidenceLevel.LOW,
                )

            # Select the best finding for the key insight box.
            # Prefer findings with real implications and non-generic titles.
            # Avoid "analysis_summary" type findings whose titles may be raw data labels.
            def _insight_score(f: KeyFinding) -> tuple:
                has_implications = bool(f.implications and f.implications.strip())
                is_specific = f.finding_type != "analysis_summary"
                has_sources = len(f.sources) > 0
                return (has_implications and is_specific, has_implications, has_sources, len(f.sources))

            key_finding = max(findings, key=_insight_score)
            all_sources: list[Source] = []
            for f in findings:
                all_sources.extend(f.sources)

            findings_digest = "\n\n".join(
                f"Finding: {f.title}\nContent: {f.content}\nConfidence: {f.confidence.value}\nImplications: {f.implications or 'N/A'}"
                for f in findings
            )
            sources_digest = "\n".join(
                f"- {s.title}: {s.url}" for s in all_sources[:10]
            )

            narrative_prompt = (
                "You are a senior consultant at a top-tier strategy firm (McKinsey/BCG).\n"
                "Write a deep, analytical narrative section for a client report.\n\n"
                f"Section topic: {_section_title(agent)}\n"
                f"Engagement question: {self._question}\n\n"
                f"Findings from the {agent} analyst:\n{findings_digest}\n\n"
                f"Sources:\n{sources_digest}\n\n"
                f"{word_clause}\n\n"
                "Write a comprehensive section body that:\n"
                "1. Opens with context — why this dimension matters for the question\n"
                "2. Presents key data points with specific numbers and sources cited inline\n"
                "3. Interprets the data — what does it mean? What's the 'so what'?\n"
                "4. Identifies patterns, tensions, or counterarguments within the findings\n"
                "5. Draws out implications for the overall recommendation\n"
                "6. Uses clear structure with sub-headings (marked with **bold**)\n"
                "7. Writes in professional consulting prose — authoritative, precise, no fluff\n"
                "8. Cites sources naturally (e.g., 'According to [Source]...')\n"
                "9. Includes at least 4-6 substantial paragraphs of 150+ words each\n"
                "10. Synthesizes across findings — don't just summarize each finding\n"
                "11. Ends with a clear 'so what' paragraph that connects to the engagement\n\n"
                "Do NOT write bullet points. Write flowing analytical paragraphs.\n"
                "Do NOT repeat the section title. Start directly with the narrative.\n"
                f"Depth is critical: do not write fewer than "
                f"{int(budget.words_per_section * 0.9)} words.\n"
            )

            section_body = "\n\n".join(f.content for f in findings)  # fallback

            try:
                response = await self._llm_complete(
                    user_prompt=narrative_prompt,
                    urgency=TaskUrgency.NORMAL,
                    temperature=0.3,
                    system_prompt_override=(
                        "You are a senior consultant writing a report section. "
                        "Write analytical prose, not bullet points. "
                        f"This section must be at least "
                        f"{int(budget.words_per_section * 0.9)} words of deep analysis."
                    ),
                )
                if response.success and response.content and len(response.content) > min_body_chars:
                    section_body = response.content
                elif response.success and response.content:
                    # Response too short — retry with stronger instruction
                    retry_prompt = (
                        f"The previous attempt was only {len(response.content)} characters — "
                        f"far too short for a consulting report section.\n\n"
                        f"{narrative_prompt}\n\n"
                        f"Previous attempt (DO NOT REPEAT — write something better):\n"
                        f"{response.content[:500]}\n\n"
                        f"Write the FULL section now. It must be at least "
                        f"{int(budget.words_per_section * 0.9)} words. "
                        "Do not stop early. Do not summarize. Write complete paragraphs."
                    )
                    retry_response = await self._llm_complete(
                        user_prompt=retry_prompt,
                        urgency=TaskUrgency.NORMAL,
                        temperature=0.4,
                        system_prompt_override=(
                            "You are a senior consultant. Your previous attempt was too short. "
                            "Write a thorough, deep analytical section of at least "
                            f"{int(budget.words_per_section * 0.9)} words."
                        ),
                    )
                    if (
                        retry_response.success
                        and retry_response.content
                        and len(retry_response.content) > min_body_chars
                    ):
                        section_body = retry_response.content
            except (ValueError, AttributeError, RuntimeError) as exc:
                # P2-11: never a silent pass. The concatenation fallback
                # still runs for this engagement, but the synthesis failure
                # is recorded with the agent and section identity instead
                # of vanishing. (Phase 4/P2-11 deletes the fallback itself
                # and raises a structured gap.)
                logger.error(
                    "narrative synthesis failed for agent=%s section=%s, "
                    "using fallback concatenation: %s: %s",
                    agent, _section_title(agent), type(exc).__name__, exc,
                )

            return AnalysisSection(
                id=f"section_{agent}",
                title=_section_title(agent),
                agent=agent,
                key_insight=key_finding.title,
                body=section_body,
                findings=findings,
                charts=[],  # Charts are added by Data Visualizer later
                images=[],  # Images are added by Presentation Designer later
                implications=(
                    key_finding.implications
                    or "Insufficient evidence to state implications — this section requires additional research."
                ),
                sources=list({s.url: s for s in all_sources}.values()),  # Dedupe by URL
                confidence=findings[0].confidence,
            )

        # Build all sections in parallel — each section's LLM call is independent
        # D5: include agents with no findings so sections are never missing
        tasks = [
            _build_one_section(agent, findings)
            for agent, findings in self._findings_by_agent.items()
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        sections: list[AnalysisSection] = []
        for result in results:
            if isinstance(result, AnalysisSection):
                sections.append(result)
            elif isinstance(result, Exception):
                logger.warning("Section build failed: %s", result)

        return sections

    # ─────────────────────────────────────────────────────────────────────
    # Quality Gate iteration — targeted refinement
    # ─────────────────────────────────────────────────────────────────────

    async def _apply_quality_feedback(
        self,
        report: FinalReport,
        quality_score: QualityScore,
    ) -> FinalReport:
        """Apply Quality Gate feedback to iteratively improve the report.

        This is NOT a generic 'try again.' Each iteration targets the
        specific dimensions that scored below 4. The Quality Gate provides
        actionable feedback like: 'Dimension 3 (analytical depth) scored
        2/5: the Market Analysis section presents data but doesn't interpret
        it. Fix: add 'so what?' implications to each finding.' (§4.5, Agent 18)

        Implementation: Instead of asking the LLM to reproduce the entire
        FinalReport JSON (which is too large for reliable JSON generation),
        we send a condensed summary and ask for targeted fixes only. The
        fixes are then applied programmatically to the existing report.
        """
        # Identify dimensions that need fixing
        failing_dims = [d for d in quality_score.dimensions if d.score < 4]

        if not failing_dims:
            return report

        # Build targeted fix instructions
        fix_instructions = "\n".join(
            f"- {d.name} (scored {d.score}/5): {d.feedback}"
            + (f" Fix: {d.fix_instructions}" if d.fix_instructions else "")
            for d in failing_dims
        )

        # Build a CONDENSED summary of the report — not the full JSON.
        # The LLM only needs enough context to make targeted fixes.
        section_summaries = "\n".join(
            f"  Section '{s.title}' (agent={s.agent}): "
            f"key_insight='{s.key_insight[:100]}', "
            f"body_length={len(s.body)} chars, "
            f"findings={len(s.findings)}, "
            f"sources={len(s.sources)}, "
            f"implications='{(s.implications or '')[:80]}'"
            for s in report.sections
        )

        report_summary = (
            f"Recommendation: {report.recommendation.value}\n"
            f"Confidence: {report.confidence.value}\n"
            f"Executive summary length: {len(report.executive_summary)} chars\n"
            f"Sections ({len(report.sections)}):\n{section_summaries}\n"
            f"Key findings: {len(report.key_findings)}\n"
            f"Total sources: {report.total_sources}\n"
            f"Risk analysis present: {report.risk_analysis is not None}\n"
            f"Limitations: {len(report.limitations)}"
        )

        prompt = (
            "You are the Synthesis Lead iterating on the FinalReport based on "
            "Quality Gate feedback.\n\n"
            f"Current report summary:\n{report_summary}\n\n"
            f"Quality Gate feedback (dimensions scoring below 4):\n{fix_instructions}\n\n"
            f"Iteration: {self._quality_iteration + 1} of {self._max_quality_iterations}\n\n"
            "Based on the feedback, return TARGETED FIXES as JSON.\n"
            "Only include fields you want to update. Omit fields that don't need changes.\n\n"
            "Return format:\n"
            "{\n"
            '  "executive_summary": "updated executive summary (if needed)",\n'
            '  "recommendation_rationale": "updated rationale (if needed)",\n'
            '  "section_updates": {\n'
            '    "section_<agent_name>": {\n'
            '      "body": "updated section body (if needed)",\n'
            '      "implications": "updated implications (if needed)"\n'
            '    }\n'
            '  },\n'
            '  "new_limitations": ["limitation1", "limitation2"]\n'
            "}\n\n"
            "Rules:\n"
            "1. Do NOT wrap the JSON in markdown code fences.\n"
            "2. Do NOT add text before or after the JSON.\n"
            "3. Only include fields that need fixing.\n"
            "4. Section bodies must be detailed, analytical, consulting-grade prose.\n"
            "5. Each section body should be 1500-5000 chars of deep analytical prose.\n"
            "6. Write flowing paragraphs, NOT bullet points. Cite sources inline.\n"
            "7. Include specific data points, interpretations, and 'so what' analysis.\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.3,
            response_format={"type": "json_object"},
        )

        if not response.success or not response.content:
            await self._transition(
                AgentState.WORKING,
                f"Quality iteration LLM call failed: {response.error if not response.success else 'empty response'}",
            )
            return report

        try:
            # Strip markdown code fences if present
            content = response.content.strip()
            if content.startswith("```"):
                lines = content.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].strip() == "```":
                    lines = lines[:-1]
                content = "\n".join(lines)
            if not content.startswith("{"):
                start = content.find("{")
                end = content.rfind("}")
                if start != -1 and end != -1 and end > start:
                    content = content[start:end + 1]

            data = json.loads(content)

            # Apply targeted fixes to a deep copy of the report
            updated = report.model_copy(deep=True)

            if "executive_summary" in data and data["executive_summary"]:
                updated.executive_summary = data["executive_summary"]

            if "recommendation_rationale" in data and data["recommendation_rationale"]:
                updated.recommendation_rationale = data["recommendation_rationale"]

            if "new_limitations" in data and isinstance(data["new_limitations"], list):
                existing = set(updated.limitations)
                for lim in data["new_limitations"]:
                    if isinstance(lim, str) and lim not in existing:
                        updated.limitations.append(lim)

            # Apply section-level updates
            section_updates = data.get("section_updates", {})
            if isinstance(section_updates, dict):
                for section in updated.sections:
                    key = section.id
                    if key in section_updates:
                        update = section_updates[key]
                        if isinstance(update, dict):
                            if "body" in update and update["body"]:
                                section.body = update["body"]
                            if "implications" in update and update["implications"]:
                                section.implications = update["implications"]

            # D-02: a degraded report may gain STRUCTURE, never CONFIDENCE.
            # The quality loop has write access to conclusions and no access
            # to evidence; without this guard it launders a crash into a
            # confident recommendation — the exact mechanism by which the
            # few-shot example's fabricated TAM figure overwrote the 07-30
            # degradation notice (see T-04's FORBIDDEN list for the tokens).
            # Conclusion fields are restored verbatim; section structure
            # updates above are allowed to stand.
            if report.is_degraded:
                updated.executive_summary = report.executive_summary
                updated.recommendation_rationale = report.recommendation_rationale
                updated.limitations = report.limitations

            # D-02 honesty: the loop cannot create sections out of nothing.
            # If it returned section_updates for a report with 0 sections,
            # the body was never built (see D-01) — that is a structural
            # failure, and it must be recorded, not silently dropped by the
            # no-match loop above.
            if not updated.sections and section_updates:
                self._record_failure(
                    "quality loop returned section_updates for a report with "
                    "0 sections — body was never built; see D-01"
                )

            await self._transition(
                AgentState.WORKING,
                f"Quality iteration {self._quality_iteration + 1}: applied targeted fixes "
                f"({len(data)} fields updated)",
            )
            return updated

        except (json.JSONDecodeError, ValueError, TypeError) as e:
            await self._transition(
                AgentState.WORKING,
                f"Quality iteration {self._quality_iteration + 1} JSON parse failed: {e!s:.80} — "
                f"keeping current report",
            )
            return report

    def _record_failure(self, message: str) -> None:
        """Record a structural integrity violation (D-02).

        Not an escalation (those go to the Director and cost a STRONG call)
        and not a silent log line: the message lands in
        ``self._recorded_failures`` where the run journal / tests can assert
        on it, and is logged at error level. The rule from the audit: a
        synthesis failure may degrade the deliverable, it may never do so
        invisibly.
        """
        logger.error("Synthesis integrity failure: %s", message)
        self._recorded_failures.append(message)

    # ─────────────────────────────────────────────────────────────────────
    # Second Brain — query for prior engagement patterns
    # ─────────────────────────────────────────────────────────────────────

    async def _query_second_brain_for_patterns(self, question: str) -> str:
        """Query Second Brain for prior engagement patterns.

        The Synthesis Lead checks the vault for prior engagements on similar
        topics — not for raw data, but for patterns. 'Last time we analyzed
        a Tier-2 SaaS market entry, the critical assumption was penetration
        rate and it flipped the recommendation.' This pattern matching makes
        the system smarter over time. (§12.8)

        BUG HISTORY (report-killer). This method is annotated ``-> str`` but
        returned ``results`` — the raw ``VaultSearchResult`` dataclass from
        ``SecondBrainClient.search()``. The single consumer,
        ``_identify_and_draft()``, does ``prior_patterns.strip()`` to decide
        whether to include the precedent block. A dataclass has no ``.strip``,
        so every engagement with a non-empty vault raised
        ``AttributeError: 'VaultSearchResult' object has no attribute 'strip'``
        out of step 5+6 of ``run()``.

        The blast radius was the whole deliverable, not one prompt block:
        ``run()`` aborted BEFORE step 8 (``_build_analysis_sections()``), so
        ``sections`` was never built. The PDF shipped with a cover, an
        At-a-Glance, an Executive Summary and an appendix — and **zero analysis
        chapters** ("0 chapters · 0 Analysis Sections" on the deliverable).
        The `except` clause here could not save it: the raise happens at the
        *call site*, not inside this method.

        Fixed by rendering the search result to the string the annotation
        always promised. Coercion is also applied defensively at the call site
        so a future tool-contract change degrades to a missing precedent block
        instead of a contentless report.
        """
        try:
            brain = self.get_tool(ToolName.SECOND_BRAIN)
            results = await brain.search(f"synthesis patterns: {question}")
        except (ValueError, AttributeError, RuntimeError):
            return ""

        notes = getattr(results, "notes", None)
        if not notes:
            return ""

        lines: list[str] = []
        for note, score in notes[: self.PRIOR_PATTERN_LIMIT]:
            title = getattr(note, "title", "") or "(untitled note)"
            body = (getattr(note, "content", "") or "").strip().replace("\n", " ")
            lines.append(f"[relevance {score:.2f}] {title}: {body[:400]}")
        return "\n".join(lines)

    # ─────────────────────────────────────────────────────────────────────
    # D5: Combined critical-path + recommendation (single DEEP call)
    # ─────────────────────────────────────────────────────────────────────

    async def _identify_and_draft(
        self,
        matrix: dict[str, Any],
        contradictions: list[Contradiction],
        prior_patterns: str = "",
    ) -> tuple[list[str], dict[str, Any]]:
        """Identify critical path AND draft recommendation in a single DEEP call.

        D5 fix: collapses former steps 5+6 into one LLM call to keep
        total DEEP calls ≤ 3 (combined + sections + quality-iteration).

        D5.1: `prior_patterns` was previously fetched by `run()` — an awaited
        Second Brain query, announced to the user as a pipeline step ("Querying
        Second Brain for prior patterns") — and then assigned to a local nothing
        read (ruff F841). The vault lookup ran, cost time, and its result was
        garbage-collected before it could influence anything. That silently
        voided the §12.8 design intent: "this pattern matching makes the system
        smarter over time." It could not, because the patterns never reached a
        prompt. They are now threaded into the one call that drafts the
        recommendation, which is the only place they could change an outcome.
        """
        findings_summary = self._format_findings_for_llm()

        contradictions_summary = "\n".join(
            f"- {c.agent_a} vs {c.agent_b}: {c.finding_a} vs {c.finding_b} "
            f"→ Resolved: {c.resolution or 'unresolved'}"
            for c in contradictions
        ) if contradictions else "No contradictions found."

        # D5.1: prior-engagement patterns from the Second Brain vault. Presented
        # as precedent, explicitly NOT as evidence — a pattern from a previous
        # engagement must not be cited as a finding about this one, or the report
        # would inherit conclusions it did not research.
        #
        # Coerced with str() rather than trusted: `prior_patterns` used to arrive
        # here as a VaultSearchResult (see _query_second_brain_for_patterns) and
        # the bare `.strip()` turned a cosmetic prompt-block decision into an
        # AttributeError that destroyed every analysis chapter in the report. A
        # decorative input must never be able to abort synthesis, so the type is
        # normalised at the boundary instead of assumed.
        patterns_text = prior_patterns if isinstance(prior_patterns, str) else str(prior_patterns or "")
        patterns_block = (
            f"Prior-engagement patterns from the vault (precedent, NOT evidence "
            f"for this question — use to sharpen which assumptions to stress-test):\n"
            f"{patterns_text}\n\n"
            if patterns_text.strip()
            else ""
        )

        prompt = (
            "You are the Synthesis Lead. Do TWO things in one response:\n\n"
            f"Question: {self._question}\n\n"
            f"All findings:\n{findings_summary}\n\n"
            f"Contradictions:\n{contradictions_summary}\n\n"
            f"{patterns_block}"
            "FIRST: Identify the 2-3 CRITICAL findings — the ones that, if they "
            "changed, would flip the recommendation.\n\n"
            "SECOND: Draft the recommendation as JSON with these fields:\n"
            "{\n"
            '  "critical_findings": ["finding_title_1", ...],\n'
            '  "reasoning": "why these are critical",\n'
            '  "recommendation": "enter|no_go|conditional|investigate|acquire|do_not_acquire|hold",\n'
            '  "recommendation_rationale": "The evidence chain — specific, not generic",\n'
            '  "critical_assumptions": ["assumption1", "assumption2"],\n'
            '  "executive_summary": "Standalone summary for the CEO — recommendation + key findings + critical risks",\n'
            '  "key_findings_titles": ["3-5 finding titles that support the recommendation"]\n'
            "}\n\n"
            "Rules:\n"
            "- The recommendation must follow from the findings, not from generic reasoning\n"
            "- Critical assumptions are assumptions that would FLIP the recommendation if wrong\n"
            "- The executive summary must stand alone — a CEO reads only that page\n"
            "- Be confident. No hedging. If uncertain, use CONDITIONAL with specific conditions\n"
        )

        response = await self._llm_complete(
            user_prompt=prompt,
            urgency=TaskUrgency.HIGH,
            temperature=0.4,
            response_format={"type": "json_object"},
        )

        fallback_critical: list[str] = []
        all_findings = self._get_all_findings()
        fallback_critical = [f.title for f in all_findings[:3]]

        fallback_rec: dict[str, Any] = {
            "recommendation": "investigate",
            "recommendation_rationale": "Insufficient data for a definitive recommendation.",
            "critical_assumptions": [],
            "executive_summary": "Further research is needed before a recommendation can be made.",
            "key_findings_titles": [],
        }

        if not response.success or not response.content:
            return fallback_critical, fallback_rec

        try:
            data = json.loads(response.content)
            critical = data.get("critical_findings", [])
            if not isinstance(critical, list) or not critical:
                critical = fallback_critical
            else:
                critical = [str(c) for c in critical[:3]]

            rec = {
                "recommendation": data.get("recommendation", "investigate"),
                "recommendation_rationale": data.get("recommendation_rationale", ""),
                "critical_assumptions": data.get("critical_assumptions", []),
                "executive_summary": data.get("executive_summary", ""),
                "key_findings_titles": data.get("key_findings_titles", []),
            }
            return critical, rec
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            logger.warning("Combined identify+draft JSON parse failed: %s", e)
            return fallback_critical, fallback_rec

    def _minimal_report(
        self,
        reason: str = "",
        sections: list[AnalysisSection] | None = None,
    ) -> FinalReport:
        """D5: Always return a valid FinalReport, even on total failure.

        D-01: whatever analysis was already built survives the crash. The
        sections arrive explicitly or fall back to ``self._partial_sections``
        (populated as soon as ``_build_analysis_sections()`` completes, before
        the recommendation call). A synthesis failure costs the
        *recommendation*, never the *analysis*.
        """
        carried = sections if sections is not None else self._partial_sections
        return FinalReport(
            engagement_id=self._engagement_id or f"eng_{uuid.uuid4().hex[:12]}",
            question=self._question or "",
            recommendation=Recommendation.INVESTIGATE,
            recommendation_rationale=(
                f"Synthesis was unable to complete normally: {reason}. "
                f"This is a degraded report — further research is required."
            ),
            critical_assumptions=["Further research is needed to validate any assumptions"],
            confidence=ConfidenceLevel.LOW,
            confidence_breakdown={},
            executive_summary=(
                f"This is a degraded report. Synthesis could not complete fully: {reason}. "
                f"The recommendation is INVESTIGATE pending additional research."
            ),
            key_findings=self._get_all_findings()[:5],
            sections=list(carried),
            contradictions=[],
            agents_used=self._get_participating_agents(),
            total_sources=len({s.url for f in self._get_all_findings() for s in f.sources}),
            total_data_points=len(self._get_all_findings()),
            limitations=[f"Synthesis incomplete: {reason}"],
            is_degraded=True,
        )

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 8-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        engagement_id: str = "",
        question: str = "",
        dag: WorkflowDAG | None = None,
    ) -> FinalReport:
        """Execute the Synthesis Lead's 8-step methodology.

        This is the most intellectually demanding method in HYPERION.
        It takes all specialist findings, reconciles them, and produces
        a single coherent recommendation. (§4.3, Agent 2)

        Steps:
        1. Collect all specialist findings from AgentBus
        2. Build a finding matrix (agent × finding × evidence × confidence)
        3. Identify contradictions and classify them
        4. Resolve contradictions (evidence-weighted, not averaging)
        5. Identify the critical path to the recommendation
        6. Draft the recommendation with supporting evidence chain
        7. Calibrate system confidence level
        8. Produce FinalReport model

        After step 8, the Quality Gate scores the report. If score < 4.0,
        the Synthesis Lead iterates with targeted fixes (max 3 iterations).
        """
        self._engagement_id = engagement_id or f"eng_{uuid.uuid4().hex[:12]}"
        self._question = question
        self._dag = dag

        try:
            return await self._run_synthesis(engagement_id, question, dag)
        except Exception as e:
            logger.error("Synthesis run() failed: %s — returning degraded report", e, exc_info=True)
            await self._escalate(
                issue=f"Synthesis failed: {e!s:.200}",
                suggested_action="Use degraded report; check logs for root cause",
            )
            degraded = self._minimal_report(reason=str(e)[:200])
            self._current_report = degraded
            return degraded

    async def _run_synthesis(
        self,
        engagement_id: str,
        question: str,
        dag: WorkflowDAG | None,
    ) -> FinalReport:
        """Internal synthesis logic — called by run() with try/except guard."""

        # Subscribe to bus channels — CORE role, but specifically needs
        # FINDINGS (to collect specialist output) and HANDOFF (for fact
        # check report, quality score, and start signal)
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Synthesizing {len(self._collected_findings)} findings from "
            f"{len(self._findings_by_agent)} specialists",
        )

        # Step 1: Collect findings (already collected via bus subscription)
        all_findings = self._get_all_findings()
        if not all_findings:
            await self._escalate(
                issue="No specialist findings collected — cannot synthesize",
                suggested_action="Check that specialists completed and published findings",
            )
            # Return a minimal report — INVESTIGATE here is a placeholder,
            # not a synthesis, so the report is degraded by definition.
            return FinalReport(
                engagement_id=self._engagement_id,
                question=self._question,
                recommendation=Recommendation.INVESTIGATE,
                recommendation_rationale="No specialist findings were available for synthesis.",
                critical_assumptions=[],
                confidence=ConfidenceLevel.LOW,
                confidence_breakdown={},
                executive_summary="Insufficient data for a recommendation.",
                is_degraded=True,
            )

        # Query Second Brain for prior patterns
        await self._transition(AgentState.WORKING, "Querying Second Brain for prior patterns")
        prior_patterns = await self._query_second_brain_for_patterns(self._question)

        # Step 2: Build finding matrix
        await self._transition(AgentState.WORKING, "Building finding matrix")
        matrix = self._build_finding_matrix()

        # Step 3: Identify contradictions
        await self._transition(AgentState.WORKING, "Identifying contradictions")
        contradictions = self._identify_contradictions(matrix)
        self._contradiction_count = len(contradictions)

        # Step 4: Resolve contradictions
        await self._transition(
            AgentState.WORKING,
            f"Resolving {len(contradictions)} contradictions (evidence-weighted)",
        )
        resolved_contradictions = await self._resolve_contradictions(contradictions, matrix)

        # D-01 structural fix: build the analysis body BEFORE the
        # recommendation call. Sections are a pure function of the collected
        # findings (``_build_analysis_sections`` never reads
        # ``recommendation_data`` — the parameter is vestigial), so there is
        # no dependency forcing them after the recommendation. Any raise in
        # step 5+6 below previously discarded every specialist's work because
        # the body was only assembled at the old step 8; now it already
        # exists and ``_minimal_report()`` carries it into the degraded
        # report.
        await self._transition(AgentState.WORKING, "Building analysis sections from findings")
        sections = await self._build_analysis_sections()
        self._partial_sections = sections

        # Step 5+6: Identify critical path AND draft recommendation (single DEEP call)
        await self._transition(AgentState.WORKING, "Identifying critical path + drafting "
            "recommendation")
        critical_path, recommendation_data = await self._identify_and_draft(
            matrix, resolved_contradictions, prior_patterns,
        )

        # Step 7: Calibrate confidence
        await self._transition(AgentState.WORKING, "Calibrating system confidence")
        system_confidence, confidence_breakdown = self._calibrate_confidence(resolved_contradictions)

        # Step 8: Assemble the FinalReport (the body already exists)
        await self._transition(AgentState.WORKING, "Producing FinalReport")

        # Parse recommendation
        try:
            recommendation = Recommendation(recommendation_data.get("recommendation", "investigate"))
        except ValueError:
            recommendation = Recommendation.INVESTIGATE

        # Select key findings for exec summary
        key_findings_titles = recommendation_data.get("key_findings_titles", [])
        key_findings: list[KeyFinding] = []
        for title in key_findings_titles:
            for finding in all_findings:
                if finding.title == title:
                    key_findings.append(finding)
                    break
        # Fallback: top 3-5 highest-confidence findings
        if not key_findings:
            key_findings = all_findings[:5]

        # Collect all sources
        all_sources: list[Source] = []
        for finding in all_findings:
            all_sources.extend(finding.sources)
        unique_sources = list({s.url: s for s in all_sources}.values())

        # Collect all gaps as limitations
        limitations: list[str] = []
        for finding in all_findings:
            limitations.extend(finding.gaps)

        report = FinalReport(
            engagement_id=self._engagement_id,
            question=self._question,
            recommendation=recommendation,
            recommendation_rationale=recommendation_data.get("recommendation_rationale", ""),
            critical_assumptions=recommendation_data.get("critical_assumptions", []),
            confidence=system_confidence,
            confidence_breakdown=confidence_breakdown,
            executive_summary=recommendation_data.get("executive_summary", ""),
            key_findings=key_findings,
            sections=sections,
            contradictions=resolved_contradictions,
            fact_check_report=self._fact_check_report,
            agents_used=self._get_participating_agents(),
            total_sources=len(unique_sources),
            total_data_points=len(all_findings),
            limitations=list(set(limitations)),
        )

        # Store the report
        self._current_report = report

        # Publish the FinalReport to the bus
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "synthesis_complete": True,
                "report": report.model_dump(),
                "recommendation": report.recommendation.value,
                "confidence": report.confidence.value,
            },
        )

        await self._transition(
            AgentState.DONE,
            f"Synthesis complete: {report.recommendation.value} "
            f"({report.confidence.value} confidence)",
        )

        return report

    # ─────────────────────────────────────────────────────────────────────
    # Quality Gate iteration loop
    # ─────────────────────────────────────────────────────────────────────

    async def iterate_on_quality(self, quality_score: QualityScore) -> FinalReport:
        """Iterate on the FinalReport based on Quality Gate feedback.

        Called by the orchestrator when the Quality Gate returns a score < 4.0.
        The Synthesis Lead applies targeted fixes to the specific dimensions
        that scored below 4, then returns the updated report.

        Max 3 iterations. If max is reached without passing, the report is
        delivered with the best score achieved and the Quality Gate's
        max_iterations_reached flag is set. (§4.5, Agent 18)
        """
        if self._current_report is None:
            return FinalReport(
                engagement_id=self._engagement_id,
                question=self._question,
                recommendation=Recommendation.INVESTIGATE,
                recommendation_rationale="No report to iterate on.",
                critical_assumptions=[],
                confidence=ConfidenceLevel.LOW,
                confidence_breakdown={},
                executive_summary="No report available.",
            )

        self._quality_score = quality_score
        self._quality_iteration = quality_score.iteration

        if self._quality_iteration >= self._max_quality_iterations:
            # Max iterations reached — deliver with current report
            await self._transition(
                AgentState.DONE,
                f"Max quality iterations ({self._max_quality_iterations}) reached — delivering best version",
            )
            return self._current_report

        await self._transition(
            AgentState.WORKING,
            f"Quality iteration {self._quality_iteration + 1}: "
            f"fixing {sum(1 for d in quality_score.dimensions if d.score < 4)} dimensions",
        )

        improved = await self._apply_quality_feedback(self._current_report, quality_score)
        self._current_report = improved

        await self._transition(
            AgentState.DONE,
            f"Quality iteration {self._quality_iteration + 1} complete",
        )

        return improved

    def get_current_report(self) -> FinalReport | None:
        """Get the current FinalReport (for the orchestrator)."""
        return self._current_report

    def get_findings_count(self) -> int:
        """Get the number of findings collected so far."""
        return len(self._collected_findings)

    def get_contradiction_count(self) -> int:
        """Get the number of contradictions identified during synthesis."""
        return self._contradiction_count
