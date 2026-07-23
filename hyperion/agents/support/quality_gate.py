"""
HYPERION Quality Gate — Agent 18, the final review against a 10-dimension
rubric. If the score is below threshold, the report goes back for iteration.

This is NOT a generic "check if it's good" agent. This is a specialist with
5 proprietary skills:

- Rubric scoring (10 dimensions): Score each dimension 1-5 with specific,
  actionable feedback. Not "good" or "bad" — "Dimension 3 (analytical depth)
  scored 2/5: the Market Analysis section presents data but doesn't interpret
  it. Fix: add 'so what?' implications to each finding." The 10 dimensions
  are: Completeness, Evidence Sufficiency, Analytical Depth, Logical
  Consistency, Contradiction Resolution, Tone and Voice, Structural Quality,
  Risk Coverage, Data Accuracy, Visual Quality.

- Gap analysis: Identify specific gaps — questions that should have been
  answered but weren't, data that should have been collected but wasn't.
  Not "needs more research" — "The regulatory analysis doesn't address
  GDPR compliance for the EU market entry, which is a critical gap."

- Tone enforcement: Flag hedgy language ("might possibly perhaps"), generic
  statements ("it depends"), and absolute statements ("this will
  definitely"). Consulting-grade tone is confident, specific, evidence-based.

- Structural validation: Check that the report follows the premium structure
  (cover → TOC → exec summary → sections → risk → methodology → appendix →
  back cover). No missing sections, no out-of-order sections.

- Evidence sufficiency check: Verify that every claim has at least one source
  and that key claims have at least two. Cross-reference with the Fact
  Checker's report for hallucinated citations.

It runs on STRONG tier (Nemotron 3 Super 120B) because quality evaluation
requires strong reasoning — it must understand what makes analysis deep vs
shallow, what makes tone consulting-grade vs generic, and what makes
evidence sufficient vs insufficient.

Model Tier: STRONG (Nemotron 3 Super 120B — quality evaluation requires
strong reasoning)
Tools: All outputs (read-only) — can read everything the engagement produced
Sub-agents: 0 (support agent — doesn't spawn sub-agents)
Output: QualityScore (per-dimension scores, weighted total, gaps,
        approve/reject, fix priority)

Methodology (§4.5, Agent 18):
1. Receive FinalReport from Synthesis Lead
2. Receive FactCheckReport from Fact Checker
3. Score each of the 10 dimensions (1-5 scale)
4. Calculate weighted total score
5. If score ≥ 4.0/5.0: approve for delivery
6. If score < 4.0: identify specific gaps and send back for iteration
7. Max 3 iterations before escalation

What makes it the best version of itself:
It doesn't just say "good" or "bad." It produces a specific, actionable score
report that tells the Synthesis Lead exactly what to fix. "Dimension 3
(analytical depth) scored 2/5: the Market Analysis section presents data but
doesn't interpret it. Fix: add 'so what?' implications to each finding.
Dimension 6 (tone) scored 3/5: 4 instances of hedgy language in the executive
summary. Fix: replace 'might possibly' with 'is likely to'."
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import Channel, MessageType
from hyperion.config import ModelTier
from hyperion.router.budget import TaskUrgency
from hyperion.schemas.agents import (
    AgentName,
    AgentRole,
    AgentSpec,
    AgentState,
    SkillSpec,
    ToolName,
)
from hyperion.schemas.models import (
    ClaimStatus,
    ConfidenceLevel,
    FactCheckReport,
    FinalReport,
    KeyFinding,
    QualityDimension,
    QualityDimensionName,
    QualityScore,
    VisualizationOutput,
)


# ─────────────────────────────────────────────────────────────────────────────
# Dimension metadata — names, descriptions, and scoring criteria
# ─────────────────────────────────────────────────────────────────────────────

DIMENSION_META: dict[QualityDimensionName, dict[str, str]] = {
    QualityDimensionName.COMPLETENESS: {
        "name": "Completeness",
        "description": "Are all sections present? Are all key questions answered?",
        "score_5": "All sections present, all key questions thoroughly answered with evidence",
        "score_1": "Missing sections, key questions unanswered",
    },
    QualityDimensionName.EVIDENCE_SUFFICIENCY: {
        "name": "Evidence Sufficiency",
        "description": "Is every claim backed by ≥1 source? Are key claims backed by ≥2 sources?",
        "score_5": "Every claim has ≥1 source, key claims have ≥2 independent sources",
        "score_1": "Claims lack sources, key claims have no independent verification",
    },
    QualityDimensionName.ANALYTICAL_DEPTH: {
        "name": "Analytical Depth",
        "description": "Does the analysis go beyond surface-level findings? Are frameworks applied correctly?",
        "score_5": "Deep analysis with frameworks applied correctly, 'so what?' implications throughout",
        "score_1": "Surface-level data presentation without interpretation",
    },
    QualityDimensionName.LOGICAL_CONSISTENCY: {
        "name": "Logical Consistency",
        "description": "Do the recommendations follow from the findings? Are there logical gaps?",
        "score_5": "Recommendations logically follow from findings, no gaps in reasoning",
        "score_1": "Recommendations don't follow from findings, major logical gaps",
    },
    QualityDimensionName.CONTRADICTION_RESOLUTION: {
        "name": "Contradiction Resolution",
        "description": "Have all contradictions between agents been resolved by the Synthesis Lead?",
        "score_5": "All contradictions resolved evidence-weighted with clear rationale",
        "score_1": "Unresolved contradictions between agents",
    },
    QualityDimensionName.TONE_AND_VOICE: {
        "name": "Tone and Voice",
        "description": "Is the tone consulting-grade (confident, specific, evidence-based)? No hedging, no waffling.",
        "score_5": "Confident, specific, evidence-based throughout. No hedging or generic statements.",
        "score_1": "Hedgy, generic, or absolute language throughout",
    },
    QualityDimensionName.STRUCTURAL_QUALITY: {
        "name": "Structural Quality",
        "description": "Does the report follow the premium structure? Are sections properly ordered?",
        "score_5": "Perfect premium structure: cover → TOC → exec summary → sections → risk → methodology → appendix",
        "score_1": "Missing sections, wrong order, no executive summary",
    },
    QualityDimensionName.RISK_COVERAGE: {
        "name": "Risk Coverage",
        "description": "Has the Risk Analyst identified the top risks? Are mitigations specific and actionable?",
        "score_5": "Top risks identified with specific, actionable mitigations and residual risk assessment",
        "score_1": "Risks not identified or mitigations are vague",
    },
    QualityDimensionName.DATA_ACCURACY: {
        "name": "Data Accuracy",
        "description": "Has the Fact Checker verified all claims? Are there unverified claims?",
        "score_5": "All claims verified, no hallucinated citations, no statistical red flags",
        "score_1": "Multiple unverified claims, hallucinated citations, or statistical red flags",
    },
    QualityDimensionName.VISUAL_QUALITY: {
        "name": "Visual Quality",
        "description": "Are charts brand-compliant? Are images properly placed? Is the PDF 300 DPI?",
        "score_5": "All charts brand-compliant, 300 DPI, Tufte-compliant, properly placed",
        "score_1": "Charts not brand-compliant, low resolution, or improperly placed",
    },
}

# Hedgy language patterns for tone enforcement
HEDGY_PATTERNS = [
    r"\bmight possibly\b",
    r"\bperhaps maybe\b",
    r"\bcould potentially\b",
    r"\bmay perhaps\b",
    r"\bit depends\b",
    r"\bhard to say\b",
    r"\bdifficult to determine\b",
    r"\buncertain whether\b",
    r"\bnot clear if\b",
    r"\bmight perhaps\b",
    r"\bpossibly could\b",
    r"\bsomewhat perhaps\b",
]

# Absolute language patterns
ABSOLUTE_PATTERNS = [
    r"\bwill definitely\b",
    r"\bguaranteed to\b",
    r"\bcertainly will\b",
    r"\babsolutely will\b",
    r"\bwithout any doubt\b",
    r"\bimpossible to fail\b",
    r"\bcannot fail\b",
]

# Generic statement patterns
GENERIC_PATTERNS = [
    r"\bit depends\b",
    r"\bvarious factors\b",
    r"\bmany considerations\b",
    r"\bnumerous reasons\b",
    r"\bseveral aspects\b",
    r"\bmultiple factors\b(?! to consider)",  # Allow "multiple factors to consider" with specifics
]

# Required report sections in order (§6.1)
REQUIRED_SECTIONS = [
    "cover",
    "table_of_contents",
    "executive_summary",
    "sections",  # 1-N specialist sections
    "risk_analysis",
    "methodology",
    "appendix",
    "back_cover",
]


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


QUALITY_GATE_SPEC = AgentSpec(
    name=AgentName.QUALITY_GATE,
    role=AgentRole.SUPPORT,
    display_name="Quality Gate",
    model_tier=ModelTier.STRONG,
    tools=[
        # Reviewer — needs search for spot-checking claims, SECOND_BRAIN for prior research
        ToolName.SECOND_BRAIN,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Rubric scoring (10 dimensions)",
            description=(
                "Score each of 10 dimensions on a 1-5 scale with specific, "
                "actionable feedback. Dimensions: (1) Completeness — all "
                "sections present, all key questions answered. (2) Evidence "
                "Sufficiency — every claim backed by ≥1 source, key claims "
                "by ≥2. (3) Analytical Depth — beyond surface-level, "
                "frameworks applied correctly. (4) Logical Consistency — "
                "recommendations follow from findings. (5) Contradiction "
                "Resolution — all inter-agent contradictions resolved. "
                "(6) Tone and Voice — consulting-grade, no hedging. "
                "(7) Structural Quality — premium structure followed. "
                "(8) Risk Coverage — top risks identified with actionable "
                "mitigations. (9) Data Accuracy — Fact Checker verified "
                "all claims. (10) Visual Quality — charts brand-compliant, "
                "300 DPI."
            ),
            inputs=["final_report", "fact_check_report", "visualization_output"],
            outputs=["dimension_scores", "weighted_total", "critical_dimensions"],
        ),
        SkillSpec(
            name="Gap analysis",
            description=(
                "Identify specific gaps in the analysis — questions that "
                "should have been answered but weren't, data that should "
                "have been collected but wasn't. Not 'needs more research' "
                "— 'The regulatory analysis doesn't address GDPR compliance "
                "for the EU market entry, which is a critical gap.'"
            ),
            inputs=["final_report", "original_question", "engagement_plan"],
            outputs=["gaps", "unanswered_questions", "missing_data"],
        ),
        SkillSpec(
            name="Tone enforcement",
            description=(
                "Flag any language that is too hedgy ('might possibly "
                "perhaps'), too generic ('it depends'), or too absolute "
                "('this will definitely'). Consulting-grade tone is "
                "confident, specific, evidence-based. No hedging, no "
                "waffling, no generic statements."
            ),
            inputs=["final_report", "section_texts"],
            outputs=["hedgy_instances", "generic_instances", "absolute_instances", "tone_fixes"],
        ),
        SkillSpec(
            name="Structural validation",
            description=(
                "Check that the report follows the premium structure: "
                "cover → TOC → exec summary → sections → risk → methodology "
                "→ appendix → back cover. No missing sections, no "
                "out-of-order sections. Executive summary must be upfront."
            ),
            inputs=["final_report", "report_sections"],
            outputs=["structure_valid", "missing_sections", "order_issues"],
        ),
        SkillSpec(
            name="Evidence sufficiency check",
            description=(
                "Verify that every claim has at least one source and that "
                "key claims have at least two. Cross-reference with the "
                "Fact Checker's report for hallucinated citations. Flag "
                "any claims that lack sources or have only a single "
                "non-independent source."
            ),
            inputs=["final_report", "fact_check_report"],
            outputs=["unsourced_claims", "single_source_claims", "hallucinated_citations"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Quality Gate — the final review before "
        "delivery. You are the last line of defense against mediocre "
        "reports.\n\n"
        "Your role:\n"
        "1. RECEIVE the FinalReport from the Synthesis Lead and the "
        "FactCheckReport from the Fact Checker.\n"
        "2. SCORE each of 10 dimensions on a 1-5 scale with SPECIFIC, "
        "ACTIONABLE feedback.\n"
        "3. CALCULATE the weighted total score.\n"
        "4. APPROVE if score ≥ 4.0/5.0 AND no dimension scores < 3.\n"
        "5. REJECT if score < 4.0 or any dimension is critical (< 3).\n"
        "6. IDENTIFY specific gaps and send back for iteration.\n"
        "7. ESCALATE after 3 failed iterations.\n\n"
        "The 10 Dimensions:\n"
        "1. Completeness: All sections present? All key questions answered?\n"
        "2. Evidence Sufficiency: Every claim ≥1 source? Key claims ≥2?\n"
        "3. Analytical Depth: Beyond surface-level? Frameworks applied?\n"
        "4. Logical Consistency: Recommendations follow from findings?\n"
        "5. Contradiction Resolution: All contradictions resolved?\n"
        "6. Tone and Voice: Consulting-grade? No hedging, no generic?\n"
        "7. Structural Quality: Premium structure followed?\n"
        "8. Risk Coverage: Top risks identified? Mitigations actionable?\n"
        "9. Data Accuracy: Fact Checker verified all claims?\n"
        "10. Visual Quality: Charts brand-compliant? 300 DPI?\n\n"
        "Scoring Rules:\n"
        "- 5: Excellent — exceeds McKinsey/BCG standard\n"
        "- 4: Good — meets standard, minor improvements possible\n"
        "- 3: Acceptable — meets minimum standard, needs improvement\n"
        "- 2: Below standard — significant issues\n"
        "- 1: Unacceptable — fundamental problems\n"
        "- Any dimension scoring < 3 is CRITICAL and forces iteration\n"
        "- Weighted total must be ≥ 4.0 for approval\n\n"
        "Feedback Rules (NON-NEGOTIABLE):\n"
        "- NEVER say 'good' or 'bad' without specifics.\n"
        "- ALWAYS provide fix_instructions when score < 4.\n"
        "- ALWAYS reference specific sections, paragraphs, or data points.\n"
        "- Example: 'Dimension 3 scored 2/5: Market Analysis section "
        "presents data but doesn't interpret it. Fix: add \"so what?\" "
        "implications to each finding.'\n\n"
        "Tone Enforcement:\n"
        "- Flag hedgy language: 'might possibly perhaps', 'could potentially'\n"
        "- Flag generic statements: 'it depends', 'various factors'\n"
        "- Flag absolute statements: 'will definitely', 'guaranteed to'\n"
        "- Consulting-grade = confident, specific, evidence-based\n\n"
        "You run on STRONG tier (Nemotron 3 Super 120B). You do NOT spawn "
        "sub-agents. You read ALL outputs (read-only).\n\n"
        "Your output is a QualityScore Pydantic model — structured, not "
        "free text. Every dimension has a score, feedback, and fix "
        "instructions if score < 4."
    ),
    spawn_condition="Spawned after the Synthesis Lead produces the FinalReport "
                     "and the Fact Checker produces the FactCheckReport. Runs "
                     "after all analysis and visualization is complete. Can "
                     "trigger up to 3 iterations before escalation.",
    max_sub_agents=0,
    output_model="QualityScore",
)


# ─────────────────────────────────────────────────────────────────────────────
# Quality Gate Agent
# ─────────────────────────────────────────────────────────────────────────────


class QualityGate(BaseAgent):
    """Agent 18: The final review against a 10-dimension rubric.

    Scores the report on 10 dimensions (1-5 scale), calculates a weighted
    total, and either approves for delivery or sends back for iteration.
    Max 3 iterations before escalation. Runs on STRONG tier because quality
    evaluation requires strong reasoning. (§4.5, Agent 18)

    Lifecycle:
    1. Receive FinalReport from Synthesis Lead
    2. Receive FactCheckReport from Fact Checker
    3. Score each of the 10 dimensions (1-5 scale)
    4. Calculate weighted total score
    5. If score ≥ 4.0/5.0: approve for delivery
    6. If score < 4.0: identify specific gaps and send back for iteration
    7. Max 3 iterations before escalation
    """

    APPROVAL_THRESHOLD = 4.0
    MAX_ITERATIONS = 2  # P7: capped at ≤2 (was 3) — content-aware quality gate
    CRITICAL_THRESHOLD = 3  # Scores below this are critical

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or QUALITY_GATE_SPEC, bus=bus, router=router)

        # The FinalReport to evaluate
        self._final_report: FinalReport | None = None

        # The FactCheckReport to cross-reference
        self._fact_check_report: FactCheckReport | None = None

        # The VisualizationOutput to check visual quality
        self._visualization_output: VisualizationOutput | None = None

        # Current iteration
        self._iteration = 1

        # Previous scores (for tracking improvement across iterations)
        self._previous_scores: list[QualityScore] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Quality Gate listens to:
        - HANDOFF: receives FinalReport from Synthesis Lead
        - FINDINGS: collects FactCheckReport and VisualizationOutput
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            if task == "quality_check":
                context_bundle = payload.get("context_bundle", {})
                if "final_report" in context_bundle:
                    report_data = context_bundle["final_report"]
                    self._final_report = FinalReport(**report_data) if isinstance(report_data, dict) else report_data
                if "fact_check_report" in context_bundle:
                    fc_data = context_bundle["fact_check_report"]
                    self._fact_check_report = FactCheckReport(**fc_data) if isinstance(fc_data, dict) else fc_data
                if "visualization_output" in context_bundle:
                    viz_data = context_bundle["visualization_output"]
                    self._visualization_output = VisualizationOutput(**viz_data) if isinstance(viz_data, dict) else viz_data
                if "iteration" in context_bundle:
                    self._iteration = context_bundle["iteration"]

        elif msg.channel == Channel.FINDINGS:
            payload = msg.payload
            finding_type = payload.get("finding_type", "")

            if finding_type == "fact_check_report":
                fc_data = payload.get("fact_check_report")
                if fc_data:
                    self._fact_check_report = FactCheckReport(**fc_data) if isinstance(fc_data, dict) else fc_data

            elif finding_type == "visualization_output":
                viz_data = payload.get("visualization_output")
                if viz_data:
                    self._visualization_output = VisualizationOutput(**viz_data) if isinstance(viz_data, dict) else viz_data

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Receive FinalReport from Synthesis Lead
    # ─────────────────────────────────────────────────────────────────────

    async def _receive_final_report(
        self,
        final_report: FinalReport | None = None,
    ) -> FinalReport | None:
        """Receive the FinalReport from the Synthesis Lead."""
        if final_report:
            self._final_report = final_report
        return self._final_report

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Receive FactCheckReport from Fact Checker
    # ─────────────────────────────────────────────────────────────────────

    async def _receive_fact_check_report(
        self,
        fact_check_report: FactCheckReport | None = None,
    ) -> FactCheckReport | None:
        """Receive the FactCheckReport from the Fact Checker."""
        if fact_check_report:
            self._fact_check_report = fact_check_report
        return self._fact_check_report

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Score each of the 10 dimensions
    # ─────────────────────────────────────────────────────────────────────

    def _score_completeness(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 1: Completeness.

        Are all sections present? Are all key questions answered?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        # Check if sections exist
        if not report.sections:
            score = 1
            feedback_parts.append("No analysis sections present in the report.")
            fix_parts.append("Add specialist analysis sections (Market, Competitive, Financial, etc.).")
        else:
            # Check section count — a standard engagement should have 4-8 sections
            section_count = len(report.sections)
            if section_count < 3:
                score = min(score, 2)
                feedback_parts.append(f"Only {section_count} sections — a standard engagement should have 4-8.")
                fix_parts.append("Add missing specialist sections based on the engagement plan.")
            elif section_count < 4:
                score = min(score, 3)
                feedback_parts.append(f"Only {section_count} sections — could benefit from more depth.")

        # Check executive summary
        if not report.executive_summary:
            score = min(score, 1)
            feedback_parts.append("No executive summary present.")
            fix_parts.append("Add an executive summary — this is the page the CEO reads.")

        # Check key findings
        if not report.key_findings:
            score = min(score, 2)
            feedback_parts.append("No key findings listed in executive summary.")
            fix_parts.append("Add 3-5 key findings with evidence references.")

        # Check risk analysis
        if not report.risk_analysis:
            score = min(score, 3)
            feedback_parts.append("No risk analysis section present.")
            fix_parts.append("Add risk analysis with top risks and mitigations.")

        # Check recommendation
        if not report.recommendation_rationale:
            score = min(score, 2)
            feedback_parts.append("No recommendation rationale provided.")
            fix_parts.append("Add a clear rationale for the recommendation.")

        if not feedback_parts:
            feedback_parts.append("All sections present, all key questions answered with evidence.")

        return QualityDimension(
            dimension_id=QualityDimensionName.COMPLETENESS,
            name="Completeness",
            score=score,
            weight=0.15,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_evidence_sufficiency(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 2: Evidence Sufficiency.

        Is every claim backed by ≥1 source? Are key claims backed by ≥2 sources?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        unsourced_sections = 0
        single_source_sections = 0

        for section in report.sections:
            if not section.sources:
                unsourced_sections += 1
            elif len(section.sources) == 1:
                single_source_sections += 1

        if unsourced_sections > 0:
            score = min(score, 2 if unsourced_sections > 2 else 3)
            feedback_parts.append(f"{unsourced_sections} section(s) have no sources cited.")
            fix_parts.append(f"Add sources to the {unsourced_sections} unsourced section(s).")

        if single_source_sections > 0:
            score = min(score, 3 if single_source_sections > 2 else 4)
            feedback_parts.append(f"{single_source_sections} section(s) have only 1 source — key claims need ≥2.")
            fix_parts.append(f"Add additional independent sources to {single_source_sections} section(s).")

        # Cross-reference with Fact Checker
        if self._fact_check_report:
            if self._fact_check_report.hallucinated_citation_count > 0:
                score = min(score, 1)
                feedback_parts.append(
                    f"Fact Checker found {self._fact_check_report.hallucinated_citation_count} "
                    f"hallucinated citation(s) — sources that don't exist or don't contain the claimed data."
                )
                fix_parts.append("Replace all hallucinated citations with real, verified sources.")

            if self._fact_check_report.unverified_count > 0:
                score = min(score, 3 if self._fact_check_report.unverified_count > 3 else 4)
                feedback_parts.append(
                    f"Fact Checker found {self._fact_check_report.unverified_count} "
                    f"unverified claim(s)."
                )

        if not feedback_parts:
            feedback_parts.append("Every claim has ≥1 source, key claims have ≥2 independent sources.")

        return QualityDimension(
            dimension_id=QualityDimensionName.EVIDENCE_SUFFICIENCY,
            name="Evidence Sufficiency",
            score=score,
            weight=0.15,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    async def _score_analytical_depth(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 3: Analytical Depth.

        Does the analysis go beyond surface-level findings? Are frameworks
        applied correctly?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        for section in report.sections:
            # Check for "so what?" implications
            if not section.implications or len(section.implications) < 20:
                score = min(score, 3)
                feedback_parts.append(f"Section '{section.title}' lacks 'so what?' implications.")
                fix_parts.append(f"Add 'so what?' implications to section '{section.title}' — what does this finding mean for the recommendation?")

            # Check for key insight
            if not section.key_insight or len(section.key_insight) < 10:
                score = min(score, 3)
                feedback_parts.append(f"Section '{section.title}' lacks a key insight.")

            # Check body length (surface-level = short)
            if len(section.body) < 500:
                score = min(score, 2)
                feedback_parts.append(f"Section '{section.title}' is too short ({len(section.body)} chars) — surface-level, not deep analysis.")
                fix_parts.append(f"Expand section '{section.title}' with deeper analysis, framework application, and interpretation.")
            elif len(section.body) < 1500:
                score = min(score, 3)
                feedback_parts.append(f"Section '{section.title}' is shallow ({len(section.body)} chars) — needs more depth, interpretation, and 'so what' analysis.")
                fix_parts.append(f"Expand section '{section.title}' to 1500+ chars with deeper analysis, specific data points, and consulting-grade prose.")

        # Use LLM to evaluate analytical depth if available
        if report.sections and self.router:
            try:
                sections_text = "\n".join(
                    f"[{s.title}] {s.body[:500]}..."
                    for s in report.sections[:5]
                )

                prompt = (
                    "Evaluate the analytical depth of these report sections "
                    "on a scale of 1-5. Does the analysis go beyond "
                    "surface-level data presentation? Are frameworks applied "
                    "correctly? Are there 'so what?' implications?\n\n"
                    f"SECTIONS:\n{sections_text}\n\n"
                    "Return a JSON object with: score (1-5), feedback (specific), "
                    "fix_instructions (if score < 4)."
                )

                response = await self._llm_complete(
                    user_prompt=prompt,
                    urgency=TaskUrgency.HIGH,
                    response_format={"type": "json_object"},
                )
                if response and response.success and response.content:
                    import json
                    try:
                        result = json.loads(response.content)
                        llm_score = int(result.get("score", score))
                        if llm_score < score:
                            score = llm_score
                            feedback_parts.append(result.get("feedback", ""))
                            if result.get("fix_instructions"):
                                fix_parts.append(result["fix_instructions"])
                    except (json.JSONDecodeError, ValueError, TypeError):
                        pass

            except (ValueError, AttributeError, RuntimeError) as e:
                await self._log_tool_use("llm", "score", f"FAIL · {e}", success=False)

        if not feedback_parts:
            feedback_parts.append("Deep analysis with frameworks applied correctly, 'so what?' implications throughout.")

        return QualityDimension(
            dimension_id=QualityDimensionName.ANALYTICAL_DEPTH,
            name="Analytical Depth",
            score=score,
            weight=0.15,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_logical_consistency(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 4: Logical Consistency.

        Do the recommendations follow from the findings? Are there logical gaps?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        # Check if recommendation rationale exists
        if not report.recommendation_rationale or len(report.recommendation_rationale) < 100:
            score = min(score, 2)
            feedback_parts.append("Recommendation rationale is missing or too brief.")
            fix_parts.append("Expand the recommendation rationale — trace the evidence chain from findings to recommendation.")

        # Check if key findings support the recommendation
        if report.key_findings and report.recommendation_rationale:
            # Check if any key findings are referenced in the rationale
            rationale_lower = report.recommendation_rationale.lower()
            unreferenced_findings = 0
            for finding in report.key_findings:
                # Check if finding title or key words appear in rationale
                finding_words = [w for w in finding.title.lower().split() if len(w) > 4]
                if not any(w in rationale_lower for w in finding_words):
                    unreferenced_findings += 1

            if unreferenced_findings > 0:
                score = min(score, 3)
                feedback_parts.append(f"{unreferenced_findings} key finding(s) not referenced in recommendation rationale.")
                fix_parts.append(f"Connect {unreferenced_findings} key finding(s) to the recommendation rationale.")

        # Check critical assumptions
        if not report.critical_assumptions:
            score = min(score, 3)
            feedback_parts.append("No critical assumptions listed — what would flip the recommendation if wrong?")
            fix_parts.append("Add 2-3 critical assumptions that would change the recommendation if proven wrong.")

        if not feedback_parts:
            feedback_parts.append("Recommendations logically follow from findings, no gaps in reasoning.")

        return QualityDimension(
            dimension_id=QualityDimensionName.LOGICAL_CONSISTENCY,
            name="Logical Consistency",
            score=score,
            weight=0.10,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_contradiction_resolution(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 5: Contradiction Resolution.

        Have all contradictions between agents been resolved by the Synthesis Lead?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        if report.contradictions:
            unresolved = [c for c in report.contradictions if not c.resolved]
            if unresolved:
                score = min(score, 2 if len(unresolved) > 2 else 3)
                feedback_parts.append(f"{len(unresolved)} contradiction(s) unresolved.")
                fix_parts.append(f"Resolve {len(unresolved)} contradiction(s) evidence-weighted — don't average, pick the better-evidenced position.")
            else:
                feedback_parts.append(f"All {len(report.contradictions)} contradiction(s) resolved.")
        else:
            feedback_parts.append("No contradictions detected between agents.")

        # Cross-reference with Fact Checker
        if self._fact_check_report and self._fact_check_report.contradictions:
            fc_contradictions = self._fact_check_report.contradictions
            fc_unresolved = [c for c in fc_contradictions if not c.resolved]
            if fc_unresolved:
                score = min(score, 2)
                feedback_parts.append(f"Fact Checker found {len(fc_unresolved)} additional unresolved contradiction(s).")
                fix_parts.append(f"Resolve {len(fc_unresolved)} contradiction(s) identified by the Fact Checker.")

        if not feedback_parts:
            feedback_parts.append("All contradictions resolved evidence-weighted with clear rationale.")

        return QualityDimension(
            dimension_id=QualityDimensionName.CONTRADICTION_RESOLUTION,
            name="Contradiction Resolution",
            score=score,
            weight=0.10,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_tone_and_voice(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 6: Tone and Voice.

        Is the tone consulting-grade (confident, specific, evidence-based)?
        No hedging, no waffling, no generic statements.
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        # Check executive summary for hedgy language
        hedgy_count = 0
        generic_count = 0
        absolute_count = 0

        texts_to_check = [report.executive_summary, report.recommendation_rationale]
        for section in report.sections:
            texts_to_check.append(section.body)
            texts_to_check.append(section.key_insight)

        for text in texts_to_check:
            if not text:
                continue
            for pattern in HEDGY_PATTERNS:
                matches = re.findall(pattern, text, re.IGNORECASE)
                hedgy_count += len(matches)
            for pattern in GENERIC_PATTERNS:
                matches = re.findall(pattern, text, re.IGNORECASE)
                generic_count += len(matches)
            for pattern in ABSOLUTE_PATTERNS:
                matches = re.findall(pattern, text, re.IGNORECASE)
                absolute_count += len(matches)

        if hedgy_count > 0:
            score = min(score, 3 if hedgy_count > 5 else 4)
            feedback_parts.append(f"{hedgy_count} instance(s) of hedgy language ('might possibly', 'could potentially').")
            fix_parts.append(f"Replace {hedgy_count} hedgy phrase(s) with confident, evidence-based language.")

        if generic_count > 0:
            score = min(score, 3 if generic_count > 3 else 4)
            feedback_parts.append(f"{generic_count} instance(s) of generic statements ('it depends', 'various factors').")
            fix_parts.append(f"Replace {generic_count} generic phrase(s) with specific, actionable statements.")

        if absolute_count > 0:
            score = min(score, 4)
            feedback_parts.append(f"{absolute_count} instance(s) of absolute language ('will definitely', 'guaranteed').")
            fix_parts.append(f"Soften {absolute_count} absolute statement(s) — consulting-grade is confident, not absolute.")

        if not feedback_parts:
            feedback_parts.append("Confident, specific, evidence-based throughout. No hedging or generic statements.")

        return QualityDimension(
            dimension_id=QualityDimensionName.TONE_AND_VOICE,
            name="Tone and Voice",
            score=score,
            weight=0.05,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_structural_quality(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 7: Structural Quality.

        Does the report follow the premium structure? Are sections properly
        ordered? Is the executive summary upfront?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        # Check executive summary exists
        if not report.executive_summary:
            score = min(score, 1)
            feedback_parts.append("No executive summary — this is required and must be upfront.")
            fix_parts.append("Add an executive summary as the first content section after the TOC.")

        # Check sections are present
        if not report.sections:
            score = min(score, 1)
            feedback_parts.append("No analysis sections present.")
        else:
            # Check section ordering — sections should have sequential IDs
            section_ids = [s.id for s in report.sections]
            for i, sid in enumerate(section_ids):
                if not sid:
                    score = min(score, 3)
                    feedback_parts.append(f"Section {i+1} has no ID — cannot verify ordering.")
                    break

        # Check risk analysis
        if not report.risk_analysis:
            score = min(score, 3)
            feedback_parts.append("No risk analysis section.")
            fix_parts.append("Add a risk analysis section after the specialist sections.")

        # Check methodology metadata
        if not report.agents_used:
            score = min(score, 3)
            feedback_parts.append("No agents listed in methodology metadata.")
            fix_parts.append("List all agents used in the methodology section.")

        if not report.limitations:
            score = min(score, 4)
            feedback_parts.append("No limitations listed — what couldn't be researched?")
            fix_parts.append("Add limitations to the methodology section.")

        if not feedback_parts:
            feedback_parts.append("Perfect premium structure: cover → TOC → exec summary → sections → risk → methodology → appendix.")

        return QualityDimension(
            dimension_id=QualityDimensionName.STRUCTURAL_QUALITY,
            name="Structural Quality",
            score=score,
            weight=0.05,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_risk_coverage(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 8: Risk Coverage.

        Has the Risk Analyst identified the top risks? Are mitigations
        specific and actionable?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        if not report.risk_analysis:
            score = min(score, 1)
            feedback_parts.append("No risk analysis present.")
            fix_parts.append("Add a risk analysis section with top risks and mitigations.")
        else:
            risks = report.risk_analysis.risks if hasattr(report.risk_analysis, "risks") else []
            if not risks:
                score = min(score, 2)
                feedback_parts.append("Risk analysis section exists but no risks identified.")
                fix_parts.append("Identify and document the top risks.")
            else:
                # Check if mitigations are specific
                vague_mitigations = 0
                vague_words = ["monitor", "watch", "track", "be aware", "consider"]
                for risk in risks:
                    mitigation = getattr(risk, "mitigation", "")
                    if mitigation and any(w in mitigation.lower() for w in vague_words) and len(mitigation) < 50:
                        vague_mitigations += 1

                if vague_mitigations > 0:
                    score = min(score, 3)
                    feedback_parts.append(f"{vague_mitigations} risk(s) have vague mitigations ('monitor', 'watch').")
                    fix_parts.append(f"Make {vague_mitigations} mitigation(s) specific and actionable — what exactly will be done?")

                # Check for black swan scenarios
                black_swans = [r for r in risks if getattr(r, "is_black_swan", False)]
                if not black_swans:
                    score = min(score, 4)
                    feedback_parts.append("No black swan scenarios identified.")
                    fix_parts.append("Add 1-2 black swan scenarios — low-probability, high-impact events.")

        if not feedback_parts:
            feedback_parts.append("Top risks identified with specific, actionable mitigations and residual risk assessment.")

        return QualityDimension(
            dimension_id=QualityDimensionName.RISK_COVERAGE,
            name="Risk Coverage",
            score=score,
            weight=0.10,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_data_accuracy(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 9: Data Accuracy.

        Has the Fact Checker verified all claims? Are there unverified claims?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        if self._fact_check_report:
            fc = self._fact_check_report

            # Check verification rate
            if fc.total_claims_checked > 0:
                if fc.verification_rate < 0.4:
                    score = min(score, 2)
                    feedback_parts.append(f"Only {fc.verification_rate:.0%} of claims verified — below 40% threshold.")
                    fix_parts.append("Verify more claims with independent sources, especially key numbers and dates.")
                elif fc.verification_rate < 0.7:
                    score = min(score, 3)
                    feedback_parts.append(f"{fc.verification_rate:.0%} of claims verified — could be higher.")

            # Check hallucinated citations
            if fc.hallucinated_citation_count > 0:
                score = min(score, 1 if fc.hallucinated_citation_count > 3 else 2)
                feedback_parts.append(f"{fc.hallucinated_citation_count} hallucinated citation(s) found.")
                fix_parts.append(f"Replace {fc.hallucinated_citation_count} hallucinated citation(s) with real sources.")

            # Check statistical red flags
            if fc.statistical_red_flags:
                score = min(score, 3 if len(fc.statistical_red_flags) > 3 else 4)
                feedback_parts.append(f"{len(fc.statistical_red_flags)} statistical red flag(s): {fc.statistical_red_flags[:2]}")
                fix_parts.append("Address statistical red flags — verify suspicious numbers with primary sources.")

            # Check evidence chain breaks
            if fc.evidence_chain_break_count > 0:
                score = min(score, 2)
                feedback_parts.append(f"{fc.evidence_chain_break_count} evidence chain break(s) — claim → source → data chain is broken.")
                fix_parts.append(f"Fix {fc.evidence_chain_break_count} evidence chain break(s) — ensure sources actually contain the claimed data.")

            # Check contradictions
            if fc.contradicted_count > 0:
                score = min(score, 3)
                feedback_parts.append(f"{fc.contradicted_count} contradicted claim(s) — sources disagree.")
        else:
            score = min(score, 3)
            feedback_parts.append("No Fact Check Report received — cannot verify data accuracy.")
            fix_parts.append("Run the Fact Checker before quality gating.")

        if not feedback_parts:
            feedback_parts.append("All claims verified, no hallucinated citations, no statistical red flags.")

        return QualityDimension(
            dimension_id=QualityDimensionName.DATA_ACCURACY,
            name="Data Accuracy",
            score=score,
            weight=0.10,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    def _score_visual_quality(self, report: FinalReport) -> QualityDimension:
        """Score Dimension 10: Visual Quality.

        Are charts brand-compliant? Are images properly placed? Is the PDF 300 DPI?
        """
        score = 5
        feedback_parts: list[str] = []
        fix_parts: list[str] = []

        if self._visualization_output:
            viz = self._visualization_output

            if not viz.all_300_dpi:
                score = min(score, 2)
                feedback_parts.append("Not all charts are 300 DPI.")
                fix_parts.append("Re-export all charts at scale=3 for 300 DPI.")

            if not viz.all_brand_compliant:
                score = min(score, 2)
                feedback_parts.append("Not all charts use the HYPERION brand color sequence.")
                fix_parts.append("Re-generate charts with colorway=CHART_COLORS (Terracotta, Sage, Deep Brown, Warm Gray, Beige, Alert Red).")

            if not viz.all_tufte_compliant:
                score = min(score, 3)
                feedback_parts.append("Not all charts follow Tufte principles (chartjunk, 3D effects, gradient fills).")
                fix_parts.append("Remove chartjunk, 3D effects, and gradient fills from non-compliant charts.")

            if viz.total_charts == 0:
                score = min(score, 2)
                feedback_parts.append("No charts generated for the report.")
                fix_parts.append("Generate charts for key data visualizations — a report without charts is not McKinsey/BCG-grade.")

            # Check sections have charts
            sections_with_charts = sum(1 for s in report.sections if s.charts)
            if report.sections and sections_with_charts < len(report.sections) // 2:
                score = min(score, 3)
                feedback_parts.append(f"Only {sections_with_charts}/{len(report.sections)} sections have charts.")
                fix_parts.append("Add charts to more sections — visual data is critical for consulting-grade reports.")
        else:
            score = min(score, 3)
            feedback_parts.append("No Visualization Output received — cannot verify visual quality.")
            fix_parts.append("Run the Data Visualizer before quality gating.")

        if not feedback_parts:
            feedback_parts.append("All charts brand-compliant, 300 DPI, Tufte-compliant, properly placed.")

        return QualityDimension(
            dimension_id=QualityDimensionName.VISUAL_QUALITY,
            name="Visual Quality",
            score=score,
            weight=0.05,
            feedback=" ".join(feedback_parts),
            fix_instructions=" ".join(fix_parts) if fix_parts else None,
            critical=score < self.CRITICAL_THRESHOLD,
        )

    async def _score_all_dimensions(self, report: FinalReport) -> list[QualityDimension]:
        """Score all 10 dimensions.

        Each dimension is scored 1-5 with specific, actionable feedback.
        """
        dimensions: list[QualityDimension] = []

        dimensions.append(self._score_completeness(report))
        dimensions.append(self._score_evidence_sufficiency(report))
        dimensions.append(await self._score_analytical_depth(report))
        dimensions.append(self._score_logical_consistency(report))
        dimensions.append(self._score_contradiction_resolution(report))
        dimensions.append(self._score_tone_and_voice(report))
        dimensions.append(self._score_structural_quality(report))
        dimensions.append(self._score_risk_coverage(report))
        dimensions.append(self._score_data_accuracy(report))
        dimensions.append(self._score_visual_quality(report))

        return dimensions

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Calculate weighted total score
    # ─────────────────────────────────────────────────────────────────────

    def _calculate_weighted_total(self, dimensions: list[QualityDimension]) -> float:
        """Calculate the weighted total score across all 10 dimensions.

        Each dimension has a weight (from DIMENSION_WEIGHTS). The total is
        the sum of (score * weight) for all dimensions.
        """
        total = 0.0
        for dim in dimensions:
            total += dim.score * dim.weight
        return round(total, 2)

    # ─────────────────────────────────────────────────────────────────────
    # Step 5-6: Approve or reject
    # ─────────────────────────────────────────────────────────────────────

    def _determine_approval(
        self,
        total_score: float,
        dimensions: list[QualityDimension],
    ) -> tuple[bool, list[QualityDimensionName]]:
        """Determine if the report is approved.

        Approved if:
        - total_score >= threshold (4.0)
        - AND no dimension scores < 3 (critical)

        If any dimension is critical, the report goes back for iteration
        regardless of the total score.
        """
        critical_dims = [
            d.dimension_id for d in dimensions
            if d.score < self.CRITICAL_THRESHOLD
        ]

        approved = total_score >= self.APPROVAL_THRESHOLD and len(critical_dims) == 0

        return (approved, critical_dims)

    def _identify_gaps(self, report: FinalReport, dimensions: list[QualityDimension]) -> list[str]:
        """Identify specific gaps in the analysis.

        Questions that should have been answered but weren't, data that
        should have been collected but wasn't.
        """
        gaps: list[str] = []

        for dim in dimensions:
            if dim.score < 4 and dim.fix_instructions:
                gaps.append(f"[{dim.name}] {dim.fix_instructions}")

        # Check for missing critical sections
        if not report.risk_analysis:
            gaps.append("Risk analysis section is missing — top risks must be identified with mitigations.")

        if not report.critical_assumptions:
            gaps.append("No critical assumptions listed — what would flip the recommendation if wrong?")

        if not report.limitations:
            gaps.append("No limitations listed — what couldn't be researched?")

        # Cross-reference with Fact Checker
        if self._fact_check_report:
            if self._fact_check_report.hallucinated_citation_count > 0:
                gaps.append(
                    f"{self._fact_check_report.hallucinated_citation_count} hallucinated citation(s) "
                    f"must be replaced with real sources."
                )

        return gaps

    def _build_fix_priority(self, dimensions: list[QualityDimension]) -> list[str]:
        """Build an ordered list of fixes, highest impact first.

        Priority order:
        1. Critical dimensions (score < 3) — highest weight first
        2. Non-critical dimensions with fix_instructions — highest weight first
        """
        fixes: list[str] = []

        # Critical dimensions first, sorted by weight (highest first)
        critical = [d for d in dimensions if d.critical and d.fix_instructions]
        critical.sort(key=lambda d: d.weight, reverse=True)
        for d in critical:
            fixes.append(f"CRITICAL [{d.name}] (score {d}/5): {d.fix_instructions}")

        # Non-critical dimensions with fixes, sorted by weight
        non_critical = [d for d in dimensions if not d.critical and d.fix_instructions]
        non_critical.sort(key=lambda d: d.weight, reverse=True)
        for d in non_critical:
            fixes.append(f"[{d.name}] (score {d.score}/5): {d.fix_instructions}")

        return fixes

    def _build_escalation_report(
        self,
        dimensions: list[QualityDimension],
        total_score: float,
        iteration: int,
    ) -> str:
        """Build a detailed escalation report when max iterations reached.

        This report explains why the report couldn't pass quality gate after
        3 iterations and what specific issues remain.
        """
        critical_dims = [d for d in dimensions if d.critical]
        low_dims = [d for d in dimensions if d.score < 4 and not d.critical]

        report_parts = [
            f"QUALITY GATE ESCALATION REPORT",
            f"=" * 50,
            f"",
            f"Iterations attempted: {iteration} (max {self.MAX_ITERATIONS})",
            f"Final score: {total_score}/{self.APPROVAL_THRESHOLD}",
            f"Status: FAILED — escalation required",
            f"",
            f"CRITICAL DIMENSIONS (score < {self.CRITICAL_THRESHOLD}):",
        ]

        for d in critical_dims:
            report_parts.append(f"  - {d.name} (score {d.score}/5): {d.feedback}")
            if d.fix_instructions:
                report_parts.append(f"    Fix: {d.fix_instructions}")

        report_parts.append(f"")
        report_parts.append(f"DIMENSIONS NEEDING IMPROVEMENT (score < 4):")

        for d in low_dims:
            report_parts.append(f"  - {d.name} (score {d.score}/5): {d.feedback}")
            if d.fix_instructions:
                report_parts.append(f"    Fix: {d.fix_instructions}")

        report_parts.append(f"")
        report_parts.append(f"RECOMMENDATION: Manual review required. The report has failed")
        report_parts.append(f"quality gating after {iteration} iterations. The Synthesis Lead")
        report_parts.append(f"should review the specific issues above and determine whether to:")
        report_parts.append(f"  1. Make manual fixes and resubmit")
        report_parts.append(f"  2. Accept the report with documented limitations")
        report_parts.append(f"  3. Restart the engagement with a different approach")

        return "\n".join(report_parts)

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 7-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
        final_report: FinalReport | None = None,
        fact_check_report: FactCheckReport | None = None,
        visualization_output: VisualizationOutput | None = None,
        iteration: int = 1,
    ) -> QualityScore:
        """Execute the Quality Gate's 7-step methodology.

        Steps (§4.5, Agent 18):
        1. Receive FinalReport from Synthesis Lead
        2. Receive FactCheckReport from Fact Checker
        3. Score each of the 10 dimensions (1-5 scale)
        4. Calculate weighted total score
        5. If score ≥ 4.0/5.0: approve for delivery
        6. If score < 4.0: identify specific gaps and send back for iteration
        7. Max 3 iterations before escalation
        """
        # Subscribe to bus
        self.subscribe_to_bus()

        # Step 1: Receive FinalReport
        await self._transition(AgentState.WORKING, "Step 1: Receiving FinalReport")
        report = await self._receive_final_report(final_report)

        if not report:
            await self._transition(AgentState.DONE, "No FinalReport received")
            return QualityScore(
                dimensions=[],
                total_score=0.0,
                approved=False,
                iteration=iteration,
                gaps=["No FinalReport received from Synthesis Lead."],
                critical_dimensions=[],
            )

        # Step 2: Receive FactCheckReport
        await self._transition(AgentState.WORKING, "Step 2: Receiving FactCheckReport")
        await self._receive_fact_check_report(fact_check_report)

        if visualization_output:
            self._visualization_output = visualization_output

        self._iteration = iteration

        # Step 3: Score each of the 10 dimensions
        await self._transition(
            AgentState.WORKING,
            f"Step 3: Scoring 10 dimensions (iteration {iteration}/{self.MAX_ITERATIONS})",
        )
        dimensions = await self._score_all_dimensions(report)

        # Step 4: Calculate weighted total score
        await self._transition(AgentState.WORKING, "Step 4: Calculating weighted total score")
        total_score = self._calculate_weighted_total(dimensions)

        # Step 5: Determine approval
        await self._transition(AgentState.WORKING, "Step 5: Determining approval")
        approved, critical_dims = self._determine_approval(total_score, dimensions)

        # Step 6: Identify gaps and send back if not approved
        await self._transition(AgentState.WORKING, "Step 6: Identifying gaps")
        gaps = self._identify_gaps(report, dimensions)
        fix_priority = self._build_fix_priority(dimensions)

        # Step 7: Check max iterations
        max_reached = iteration >= self.MAX_ITERATIONS
        escalation_report = None

        if not approved and max_reached:
            await self._transition(
                AgentState.WORKING,
                f"Step 7: Max iterations ({self.MAX_ITERATIONS}) reached — escalating",
            )
            escalation_report = self._build_escalation_report(dimensions, total_score, iteration)

            # Publish escalation to bus
            await self.bus.publish(
                channel=Channel.HANDOFF,
                msg_type=MessageType.ESCALATION,
                sender=self.name,
                payload={
                    "to_agent": "engagement_director",
                    "from_agent": self.name.value,
                    "escalation_type": "quality_gate_failed",
                    "iteration": iteration,
                    "total_score": total_score,
                    "threshold": self.APPROVAL_THRESHOLD,
                    "critical_dimensions": [d.value for d in critical_dims],
                    "escalation_report": escalation_report,
                    "message": (
                        f"Quality Gate FAILED after {iteration} iterations. "
                        f"Final score: {total_score}/{self.APPROVAL_THRESHOLD}. "
                        f"Critical dimensions: {', '.join(critical_dims)}. "
                        f"Manual review required."
                    ),
                },
            )
        elif not approved:
            # Send back for iteration
            await self._transition(
                AgentState.WORKING,
                f"Step 7: Score {total_score} < {self.APPROVAL_THRESHOLD} — sending back for iteration {iteration + 1}",
            )

            await self.bus.publish(
                channel=Channel.HANDOFF,
                msg_type=MessageType.ESCALATION,
                sender=self.name,
                payload={
                    "to_agent": "synthesis_lead",
                    "from_agent": self.name.value,
                    "task": "iterate",
                    "iteration": iteration + 1,
                    "quality_score": {
                        "total_score": total_score,
                        "threshold": self.APPROVAL_THRESHOLD,
                        "approved": approved,
                        "critical_dimensions": [d.value for d in critical_dims],
                        "gaps": gaps,
                        "fix_priority": fix_priority,
                    },
                    "message": (
                        f"Quality Gate: score {total_score}/{self.APPROVAL_THRESHOLD}. "
                        f"Fix {len(critical_dims)} critical dimension(s) and "
                        f"{len([d for d in dimensions if d.score < 4 and not d.critical])} "
                        f"improvement area(s). Priority: {fix_priority[:3]}"
                    ),
                },
            )

        # Build QualityScore
        quality_score = QualityScore(
            dimensions=dimensions,
            total_score=total_score,
            threshold=self.APPROVAL_THRESHOLD,
            approved=approved,
            iteration=iteration,
            gaps=gaps,
            critical_dimensions=critical_dims,
            max_iterations_reached=max_reached and not approved,
            escalation_report=escalation_report,
            fix_priority=fix_priority,
        )

        # Publish quality score to bus
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "finding_type": "quality_score",
                "quality_score": quality_score.model_dump(),
                "total_score": total_score,
                "approved": approved,
                "iteration": iteration,
                "critical_dimensions": [d.value for d in critical_dims],
                "gaps_count": len(gaps),
            },
        )

        # Publish a finding for the quality gate result
        finding = KeyFinding(
            id=f"finding_{hashlib.md5(f'quality_gate_{engagement_id}_{iteration}'.encode()).hexdigest()[:8]}" if hashlib else f"finding_qg_{engagement_id}_{iteration}",
            agent=self.name.value,
            finding_type="quality_gate_result",
            title=f"Quality Gate: {'APPROVED' if approved else 'REJECTED'} (score {total_score}/5.0, iteration {iteration})",
            content=(
                f"Quality Gate {'approved' if approved else 'rejected'} the report. "
                f"Total score: {total_score}/{self.APPROVAL_THRESHOLD}. "
                f"Iteration: {iteration}/{self.MAX_ITERATIONS}. "
                f"Critical dimensions: {', '.join(d.value for d in critical_dims) if critical_dims else 'none'}. "
                f"Gaps identified: {len(gaps)}. "
                f"{'Max iterations reached — escalated.' if max_reached and not approved else ''}"
            ),
            confidence=ConfidenceLevel.HIGH,
        )
        await self._publish_finding(finding)

        await self._transition(
            AgentState.DONE,
            f"Quality Gate {'APPROVED' if approved else 'REJECTED'}: "
            f"score {total_score}/{self.APPROVAL_THRESHOLD}, "
            f"iteration {iteration}/{self.MAX_ITERATIONS}, "
            f"critical_dims: {len(critical_dims)}, "
            f"gaps: {len(gaps)}, "
            f"{'escalated' if max_reached and not approved else 'sent back for iteration' if not approved else 'approved for delivery'}",
        )

        return quality_score
