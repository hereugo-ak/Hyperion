"""
HYPERION Findings Models — the structured data contract between agents.

Every agent produces structured output (Pydantic models with typed fields),
not free text. This means the Synthesis Lead can programmatically reconcile
findings, the Quality Gate can programmatically score them, and the
Presentation Designer can programmatically lay them out. Free text is the
enemy of quality at scale. (ARCHITECTURE.md §0.1)

This file contains:
- Source: A cited source with credibility scoring
- KeyFinding: A single finding from any agent
- ConfidenceLevel: Confidence enumeration (HIGH/MEDIUM/LOW)
- Risk: A risk identified by the Risk Analyst
- FinancialMetric: A typed financial metric
- AnalysisSection: A section of analysis for the report
- Contradiction: A contradiction between agents (for Synthesis Lead)
- Specialist output models: MarketAnalysis, CompetitiveLandscape, etc.
- FinalReport: The single most important data structure in the system
- QualityScore: The Quality Gate's 10-dimension rubric output
- FactCheckReport: The Fact Checker's claim verification output
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Confidence Levels (used by all agents)
# ─────────────────────────────────────────────────────────────────────────────


class ConfidenceLevel(str, Enum):
    """Confidence level for findings and recommendations.

    The Synthesis Lead aggregates individual agent confidence scores into
    a system-level confidence with domain-weighted breakdown. If Market is
    HIGH confidence but Regulatory is LOW confidence, the system confidence
    reflects the weakest critical link. (§4.3, Agent 2)
    """

    HIGH = "high"        # Multiple independent sources agree, data is solid
    MEDIUM = "medium"    # Some sources agree, data has gaps
    LOW = "low"          # Sparse data, single source, or contradictory sources


# ─────────────────────────────────────────────────────────────────────────────
# Source — a cited source with credibility scoring (§5.5, Agent 15)
# ─────────────────────────────────────────────────────────────────────────────


class SourceCredibility(str, Enum):
    """Source credibility hierarchy (§4.5, Agent 15).

    Peer-reviewed > government > industry report > news > blog > social media.
    The Fact Checker weights verification accordingly — a claim verified by
    a peer-reviewed paper is more credible than one verified by a blog post.
    """

    PEER_REVIEWED = "peer_reviewed"
    GOVERNMENT = "government"
    INDUSTRY_REPORT = "industry_report"
    VENDOR = "vendor"          # Vendor pricing/feature pages — factual but biased
    NEWS = "news"
    BLOG = "blog"
    SOCIAL_MEDIA = "social_media"


class Source(BaseModel):
    """A cited source with credibility scoring.

    Every claim in the final report must have a traceable source.
    The Research Librarian formats citations and deduplicates sources.
    The Fact Checker verifies that sources actually contain the data
    agents claim they do. (§5.5, §4.5 Agent 16)
    """

    id: str = Field(description="Unique source identifier (e.g., 'src_001')")
    title: str = Field(description="Title of the source document/page")
    url: str = Field(description="URL to the source")
    credibility: SourceCredibility = Field(description="Credibility tier")
    accessed_at: datetime = Field(default_factory=datetime.now)
    author: str | None = Field(default=None, description="Author or organization")
    publication_date: str | None = Field(default=None, description="Publication date if available")
    key_data: str | None = Field(default=None, description="The specific data point extracted from this source")


# ─────────────────────────────────────────────────────────────────────────────
# KeyFinding — the universal finding unit (ARCHITECTURE.md:88)
# ─────────────────────────────────────────────────────────────────────────────


class KeyFinding(BaseModel):
    """A single finding from any agent.

    This is the universal unit of knowledge in HYPERION. Every agent
    produces KeyFinding objects. The Synthesis Lead collects all findings,
    builds a finding matrix (agent × finding × evidence × confidence),
    and reconciles them into a FinalReport. (§4.3, Agent 2)

    A finding is NOT free text. It has:
    - A typed finding_type (what kind of finding)
    - Structured content (the actual data)
    - Sources (traceable evidence)
    - Confidence (how sure we are)
    - Gaps (what we couldn't find)
    """

    id: str = Field(description="Unique finding identifier")
    agent: str = Field(description="Which agent produced this finding")
    finding_type: str = Field(description="Type of finding (e.g., 'market_size', 'risk', 'competitor_profile')")
    title: str = Field(description="Short title for TUI display")
    content: str = Field(description="The finding content — specific, evidence-based, not generic")
    sources: list[Source] = Field(default_factory=list, description="Evidence backing this finding")
    confidence: ConfidenceLevel = Field(description="How confident the agent is")
    gaps: list[str] = Field(default_factory=list, description="What the agent couldn't find")
    implications: str | None = Field(default=None, description="'So what?' — what does this mean for the recommendation?")
    timestamp: datetime = Field(default_factory=datetime.now)


# ─────────────────────────────────────────────────────────────────────────────
# Risk — the Risk Analyst's output unit (§4.4, Agent 6)
# ─────────────────────────────────────────────────────────────────────────────


class RiskCategory(str, Enum):
    """The 6 risk categories (§4.4, Agent 6)."""

    MARKET = "market"
    FINANCIAL = "financial"
    OPERATIONAL = "operational"
    REGULATORY = "regulatory"
    TECHNOLOGY = "technology"
    STRATEGIC = "strategic"


class Risk(BaseModel):
    """A single risk identified by the Risk Analyst.

    Scored on probability (1-5) × impact (1-5) on a 5×5 risk matrix.
    Each risk has a mitigation action, an owner (which agent monitors it),
    and a residual risk score (risk after mitigation). (§4.4, Agent 6)
    """

    id: str = Field(description="Unique risk identifier")
    category: RiskCategory = Field(description="Which of the 6 risk categories")
    description: str = Field(description="What the risk is — specific, not generic")
    probability: int = Field(ge=1, le=5, description="Probability score 1-5")
    impact: int = Field(ge=1, le=5, description="Impact score 1-5")
    risk_score: int = Field(ge=1, le=25, description="probability × impact")
    mitigation: str = Field(description="Specific mitigation action — actionable, not vague")
    residual_probability: int | None = Field(default=None, ge=1, le=5, description="Probability after mitigation")
    residual_impact: int | None = Field(default=None, ge=1, le=5, description="Impact after mitigation")
    owner: str = Field(description="Which agent monitors this risk")
    is_black_swan: bool = Field(default=False, description="Low-probability, high-impact event")
    trigger_conditions: str | None = Field(default=None, description="What would cause this risk to materialize")
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Financial Metric — the Financial Analyst's output unit (§4.4, Agent 5)
# ─────────────────────────────────────────────────────────────────────────────


class FinancialMetric(BaseModel):
    """A single typed financial metric.

    The Financial Analyst never reports a single valuation number. It always
    reports a range with sensitivity tables showing how the valuation changes
    under different assumptions. It always identifies the key value drivers —
    the 2-3 assumptions that account for 80% of the valuation variance.
    (§4.4, Agent 5)
    """

    name: str = Field(description="Metric name (e.g., 'DCF Valuation', 'LTV/CAC Ratio')")
    value: float | str = Field(description="The metric value (float for numbers, str for ranges like '$1.8B-$2.3B')")
    unit: str = Field(default="", description="Unit (e.g., '$', '%', 'x', 'months')")
    low_estimate: float | None = Field(default=None, description="Low end of range")
    high_estimate: float | None = Field(default=None, description="High end of range")
    base_case: float | None = Field(default=None, description="Base case estimate")
    assumptions: list[str] = Field(default_factory=list, description="Key assumptions driving this metric")
    sensitivity: dict[str, dict[str, float]] | None = Field(
        default=None,
        description="Sensitivity table: {variable: {low/medium/high: value}}"
    )
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Analysis Section — for the FinalReport structure (§6.1)
# ─────────────────────────────────────────────────────────────────────────────


class AnalysisSection(BaseModel):
    """A section of the final report.

    Each section is self-contained — a reader can jump to any section and
    understand it without reading prior sections. Each section has:
    - A key insight box (highlighted, beige background, terracotta border)
    - Body text with inline data
    - Charts/graphs (Plotly, brand colors, 300 DPI)
    - Source citations (footnote style)
    - A "So what?" implication box at the end (sage background)
    (§6.1)
    """

    id: str = Field(description="Section identifier (e.g., 'market_analysis')")
    title: str = Field(description="Section title (Instrument Serif, 22pt)")
    agent: str = Field(description="Which agent authored this section")
    key_insight: str = Field(description="The key insight for the highlighted box")
    body: str = Field(description="The section body — evidence-based, not generic")
    findings: list[KeyFinding] = Field(default_factory=list, description="Findings backing this section")
    charts: list[str] = Field(default_factory=list, description="Chart image paths (300 DPI PNG)")
    images: list[str] = Field(default_factory=list, description="Unsplash image paths for this section")
    implications: str = Field(description="'So what?' — what does this mean for the recommendation?")
    sources: list[Source] = Field(default_factory=list, description="All sources cited in this section")
    confidence: ConfidenceLevel = Field(description="Confidence level for this section's analysis")


# ─────────────────────────────────────────────────────────────────────────────
# Contradiction — for the Synthesis Lead's reconciliation (§4.3, Agent 2)
# ─────────────────────────────────────────────────────────────────────────────


class ContradictionType(str, Enum):
    """The 3 types of contradictions (§4.3, Agent 2)."""

    DATA_CONFLICT = "data_conflict"          # Different numbers for the same metric
    INTERPRETATION_CONFLICT = "interpretation"  # Same data, different conclusions
    SCOPE_CONFLICT = "scope_conflict"         # Agents analyzed different scopes


class Contradiction(BaseModel):
    """A contradiction between two agents' findings.

    The Synthesis Lead builds a contradiction matrix, classifies each
    contradiction, and resolves it evidence-weighted (not averaging).
    (§4.3, Agent 2)
    """

    id: str = Field(description="Contradiction identifier")
    agent_a: str = Field(description="First agent")
    agent_b: str = Field(description="Second agent")
    finding_a: str = Field(description="What agent A claims")
    finding_b: str = Field(description="What agent B claims")
    contradiction_type: ContradictionType = Field(description="Type of contradiction")
    resolution: str | None = Field(default=None, description="How the Synthesis Lead resolved it")
    resolved: bool = Field(default=False, description="Whether this contradiction has been resolved")
    evidence_weighted_winner: str | None = Field(default=None, description="Which finding was better supported")


# ─────────────────────────────────────────────────────────────────────────────
# Specialist Output Models — typed outputs per agent (§4.4)
# ─────────────────────────────────────────────────────────────────────────────


class MarketAnalysis(BaseModel):
    """Output from the Market Analyst (Agent 3).

    Never reports a single market size number. Always reports a range with
    a top-down estimate, a bottom-up estimate, and a triangulated best
    estimate. Always cites the source for each number. Always flags when
    market data is sparse. Always segments before sizing. (§4.4, Agent 3)
    """

    tam_top_down: FinancialMetric = Field(description="TAM via top-down sizing")
    tam_bottom_up: FinancialMetric = Field(description="TAM via bottom-up sizing")
    tam_triangulated: FinancialMetric = Field(description="Triangulated best estimate TAM")
    sam: FinancialMetric = Field(description="Serviceable Addressable Market")
    som: FinancialMetric = Field(description="Serviceable Obtainable Market")
    cagr: FinancialMetric = Field(description="Compound Annual Growth Rate")
    segments: list[KeyFinding] = Field(default_factory=list, description="Market segments")
    growth_drivers: list[KeyFinding] = Field(default_factory=list, description="Growth driver decomposition")
    market_maturity: str = Field(description="emerging/growing/mature/declining")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


class CompetitiveLandscape(BaseModel):
    """Output from Competitive Intelligence (Agent 4).

    Doesn't just list competitors — maps their moats and identifies which
    are defensible vs. eroding. Always identifies white space. Cross-references
    current pricing with Wayback historical pricing. (§4.4, Agent 4)
    """

    competitors: list[KeyFinding] = Field(description="Competitor profiles")
    competitor_matrix: dict[str, dict[str, str]] = Field(description="Competitor × dimension comparison")
    moat_assessments: list[KeyFinding] = Field(default_factory=list, description="Hamilton Helmer moat framework")
    strategic_groups: list[str] = Field(default_factory=list, description="Strategic group clusters")
    positioning_map: dict[str, Any] | None = Field(default=None, description="2D positioning map data")
    white_space: list[str] = Field(default_factory=list, description="White space opportunities")
    pricing_trends: list[KeyFinding] = Field(default_factory=list, description="Historical pricing via Wayback")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


class FinancialAnalysis(BaseModel):
    """Output from the Financial Analyst (Agent 5).

    Never reports a single valuation number. Always reports a range with
    sensitivity tables. Always identifies key value drivers. Always
    cross-validates DCF with comparable company analysis. (§4.4, Agent 5)
    """

    dcf_valuation: FinancialMetric | None = Field(default=None, description="DCF model output")
    comparable_analysis: FinancialMetric | None = Field(default=None, description="Comparable company multiples")
    unit_economics: list[FinancialMetric] = Field(default_factory=list, description="LTV, CAC, payback, margins")
    sensitivity_tables: list[FinancialMetric] = Field(default_factory=list, description="Two-variable sensitivity")
    scenarios: dict[str, FinancialMetric] = Field(default_factory=dict, description="best/base/worst case")
    break_even: FinancialMetric | None = Field(default=None, description="Break-even analysis")
    key_value_drivers: list[str] = Field(default_factory=list, description="2-3 assumptions driving 80% of variance")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


class RiskAnalysis(BaseModel):
    """Output from the Risk Analyst (Agent 6).

    Thinks in scenarios, not in lists. Identifies the 5 risks that actually
    matter, explains why the other 15 are noise. Always asks "what would
    kill this?" before "what could help this?" (§4.4, Agent 6)
    """

    risks: list[Risk] = Field(description="All identified risks")
    top_risks: list[Risk] = Field(default_factory=list, description="Top 10 risks with mitigations")
    black_swan_scenarios: list[Risk] = Field(default_factory=list, description="Low-probability, high-impact events")
    residual_risk_summary: str = Field(description="Risk profile after mitigations")
    scenario_plan: dict[str, Any] = Field(default_factory=dict, description="best/base/worst with triggers")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Technology Assessment — the Technology Analyst's output (§4.4, Agent 7)
# ─────────────────────────────────────────────────────────────────────────────


class VendorComparison(BaseModel):
    """A single vendor in the comparison matrix (§4.4, Agent 7).

    Scored across 7 dimensions: feature fit, pricing, scalability, support
    quality, ecosystem, lock-in risk, and roadmap alignment. Each dimension
    is scored 1-5. The overall score is a weighted average.
    """

    vendor_name: str = Field(description="Vendor or technology name")
    feature_fit: int = Field(ge=1, le=5, description="How well features match requirements (1-5)")
    pricing: int = Field(ge=1, le=5, description="Pricing competitiveness (1=expensive, 5=affordable)")
    scalability: int = Field(ge=1, le=5, description="Ability to scale with the business (1-5)")
    support_quality: int = Field(ge=1, le=5, description="Support and documentation quality (1-5)")
    ecosystem: int = Field(ge=1, le=5, description="Integration ecosystem and community (1-5)")
    lock_in_risk: int = Field(ge=1, le=5, description="Lock-in risk (1=high lock-in, 5=easy to switch)")
    roadmap_alignment: int = Field(ge=1, le=5, description="Roadmap alignment with business needs (1-5)")
    overall_score: float = Field(ge=0, le=5, description="Weighted overall score")
    notes: str = Field(default="", description="Qualitative notes on this vendor")
    pricing_details: str | None = Field(default=None, description="Pricing tier details")
    sources: list[Source] = Field(default_factory=list)


class BuildVsBuyAnalysis(BaseModel):
    """Structured build-vs-buy comparison (§4.4, Agent 7).

    Compares build vs. buy on: time to market, total cost of ownership
    (5-year), strategic differentiation, maintenance burden, team capability,
    and opportunity cost. The recommendation is not 'build is better' or
    'buy is better' — it's the one that best fits the business context.
    """

    recommendation: str = Field(description="BUILD or BUY or HYBRID")
    time_to_market_build: str = Field(description="Estimated time if building")
    time_to_market_buy: str = Field(description="Estimated time if buying")
    tco_5yr_build: float = Field(description="5-year TCO if building ($)")
    tco_5yr_buy: float = Field(description="5-year TCO if buying ($)")
    strategic_differentiation_build: str = Field(description="How much strategic differentiation if building")
    strategic_differentiation_buy: str = Field(description="How much strategic differentiation if buying")
    maintenance_burden_build: str = Field(description="Maintenance burden if building")
    maintenance_burden_buy: str = Field(description="Maintenance burden if buying")
    team_capability_assessment: str = Field(description="Can the team build and maintain this?")
    opportunity_cost: str = Field(description="What are we NOT doing if we build this?")
    rationale: str = Field(description="Why this recommendation — specific to the business context")


class TCOAnalysis(BaseModel):
    """5-year Total Cost of Ownership breakdown (§4.4, Agent 7).

    Includes licensing, infrastructure, maintenance, integration, and
    switching costs. Not just licensing cost — the full picture.
    A vendor that's 20% cheaper but impossible to leave is more expensive
    than one that's 20% pricier but easy to switch.
    """

    option_name: str = Field(description="Name of the option being analyzed")
    year_1: float = Field(description="Year 1 cost ($)")
    year_2: float = Field(description="Year 2 cost ($)")
    year_3: float = Field(description="Year 3 cost ($)")
    year_4: float = Field(description="Year 4 cost ($)")
    year_5: float = Field(description="Year 5 cost ($)")
    total_5yr: float = Field(description="Total 5-year cost ($)")
    licensing: float = Field(description="Licensing costs over 5 years ($)")
    infrastructure: float = Field(description="Infrastructure costs over 5 years ($)")
    maintenance: float = Field(description="Maintenance costs over 5 years ($)")
    integration: float = Field(description="Integration costs over 5 years ($)")
    switching_cost: float = Field(description="Cost to switch away from this option ($)")
    notes: str = Field(default="", description="Key cost drivers and assumptions")


class ArchitectureReview(BaseModel):
    """Architecture review against business requirements (§4.4, Agent 7).

    Evaluates a technology architecture against scalability, reliability,
    maintainability, and cost requirements. Identifies anti-patterns and
    single points of failure.
    """

    architecture_description: str = Field(description="What architecture is being reviewed")
    scalability_assessment: str = Field(description="Can it scale to 10x current load?")
    reliability_assessment: str = Field(description="Single points of failure, redundancy")
    maintainability_assessment: str = Field(description="Code quality, documentation, team familiarity")
    cost_assessment: str = Field(description="Infrastructure cost at scale")
    anti_patterns: list[str] = Field(default_factory=list, description="Architectural anti-patterns identified")
    single_points_of_failure: list[str] = Field(default_factory=list, description="SPOFs in the architecture")
    recommendations: list[str] = Field(default_factory=list, description="Specific architectural recommendations")


class TechnologyAssessment(BaseModel):
    """Output from the Technology Analyst (Agent 7).

    Evaluates tech against business requirements, not engineering preferences.
    Doesn't recommend Kubernetes because it's "modern" — recommends the
    simplest technology that meets the scalability/reliability requirements.
    Always calculates 5-year TCO, not just licensing cost. Always assesses
    lock-in risk. (§4.4, Agent 7)
    """

    vendor_matrix: list[VendorComparison] = Field(default_factory=list, description="Vendor comparison matrix")
    build_vs_buy: BuildVsBuyAnalysis | None = Field(default=None, description="Build-vs-buy recommendation")
    tco_analysis: list[TCOAnalysis] = Field(default_factory=list, description="5-year TCO for each option")
    architecture_review: ArchitectureReview | None = Field(default=None, description="Architecture review if applicable")
    platform_assessment: str = Field(default="", description="Platform play vs. point solution assessment")
    lock_in_risk_summary: str = Field(default="", description="Lock-in risk across all recommended vendors")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Operations Analysis — the Operations Analyst's output (§4.4, Agent 8)
# ─────────────────────────────────────────────────────────────────────────────


class ProcessStep(BaseModel):
    """A single step in an end-to-end process map (§4.4, Agent 8).

    Mapped using SIPOC (Supplier-Input-Process-Output-Customer) methodology.
    Each step has a throughput rate, cycle time, and value-adding classification.
    """

    step_number: int = Field(description="Step number in the process sequence")
    step_name: str = Field(description="Name of the process step")
    supplier: str = Field(description="Who/what provides the input")
    input: str = Field(description="What comes into this step")
    process: str = Field(description="What happens in this step")
    output: str = Field(description="What comes out of this step")
    customer: str = Field(description="Who receives the output")
    cycle_time: str = Field(description="Time to complete this step")
    throughput: str = Field(description="Units per time period")
    is_value_adding: bool = Field(description="Does this step add value the customer pays for?")
    is_bottleneck: bool = Field(default=False, description="Is this the binding constraint?")


class Bottleneck(BaseModel):
    """A process bottleneck identified via theory of constraints (§4.4, Agent 8).

    The HYPERION Operations Analyst doesn't just say 'the process has bottlenecks.'
    It identifies the binding constraint, quantifies the improvement potential in
    dollars, and calculates the ROI of resolving it.
    """

    step_name: str = Field(description="Which step is the bottleneck")
    current_throughput: str = Field(description="Current throughput at this step")
    max_downstream_throughput: str = Field(description="Max throughput of downstream steps")
    constraint_type: str = Field(description="Type: capacity, policy, market, or material")
    improvement_action: str = Field(description="Specific action to resolve the bottleneck")
    improvement_cost: str = Field(description="Estimated cost to resolve ($)")
    improvement_potential: str = Field(description="Estimated throughput increase (%)")
    annual_value: str = Field(description="Annual $ value of resolving this bottleneck")
    roi: str = Field(description="ROI of the improvement action")


class OperationalKPI(BaseModel):
    """A single KPI in the operational dashboard (§4.4, Agent 8).

    Not generic metrics — the 5-7 metrics that actually drive performance
    for this specific operational model. Each KPI has a target, current value,
    and the levers that move it.
    """

    name: str = Field(description="KPI name")
    category: str = Field(description="Category: efficiency, quality, throughput, cost, customer")
    formula: str = Field(description="How to calculate this KPI")
    target: str = Field(description="Target value")
    current: str = Field(description="Current value (if known)")
    unit: str = Field(description="Unit of measurement")
    frequency: str = Field(description="Measurement frequency: hourly, daily, weekly, monthly")
    levers: list[str] = Field(default_factory=list, description="What moves this KPI")
    benchmark: str = Field(default="", description="Industry benchmark for this KPI")


class BenchmarkComparison(BaseModel):
    """Operational benchmark against industry leaders (§4.4, Agent 8).

    Identifies the gap between current performance and industry leaders,
    and estimates the improvement potential.
    """

    metric: str = Field(description="Operational metric being benchmarked")
    current_value: str = Field(description="Current performance")
    industry_average: str = Field(description="Industry average")
    industry_leader: str = Field(description="Best-in-class performance")
    gap_to_leader: str = Field(description="Gap between current and leader")
    improvement_potential: str = Field(description="Estimated improvement if gap closed")
    annual_value: str = Field(default="", description="Annual $ value of closing the gap")


class OperationsAnalysis(BaseModel):
    """Output from the Operations Analyst (Agent 8).

    Doesn't just map processes — identifies the binding constraint and
    estimates the improvement potential in dollars. A generic ops analyst
    says "the process has bottlenecks." The HYPERION Operations Analyst
    says "Step 3 is the bottleneck at 40 units/hour vs. 60 units/hour for
    the rest of the process. Adding one worker to Step 3 costs $50K/year
    but increases throughput by 50%, generating $200K/year in additional
    contribution margin. ROI = 300%." (§4.4, Agent 8)
    """

    process_map: list[ProcessStep] = Field(default_factory=list, description="End-to-end SIPOC process map")
    bottlenecks: list[Bottleneck] = Field(default_factory=list, description="Identified bottlenecks with $ improvement potential")
    capacity_utilization: str = Field(default="", description="Current capacity utilization and constraints")
    benchmark_comparison: list[BenchmarkComparison] = Field(default_factory=list, description="Benchmark vs. industry leaders")
    kpi_dashboard: list[OperationalKPI] = Field(default_factory=list, description="5-7 KPIs that drive performance")
    improvement_opportunities: list[str] = Field(default_factory=list, description="Lean/Six Sigma improvement opportunities")
    total_improvement_value: str = Field(default="", description="Total annual $ value of all improvement opportunities")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Regulatory Analysis — the Regulatory Analyst's output (§4.4, Agent 9)
# ─────────────────────────────────────────────────────────────────────────────


class RegulationType(str, Enum):
    """Categories of regulations (§4.4, Agent 9)."""

    DATA_PROTECTION = "data_protection"      # GDPR, CCPA, DPDP
    FINANCIAL = "financial"                  # SEC, RBI, MiFID II
    INDUSTRY_SPECIFIC = "industry_specific"  # FDA, FAA, FERC
    LABOR = "labor"                          # Wage, safety, discrimination
    ENVIRONMENTAL = "environmental"          # EPA, emissions, waste
    TAX = "tax"                              # Corporate tax, VAT/GST
    CONSUMER_PROTECTION = "consumer_protection"  # FTC, consumer rights
    ANTITRUST = "antitrust"                  # Competition law


class Regulation(BaseModel):
    """A single regulation applicable to the business (§4.4, Agent 9).

    Mapped across jurisdictions with type classification, requirements,
    and compliance status. Each regulation has a risk level and estimated
    compliance cost.
    """

    name: str = Field(description="Regulation name (e.g., 'GDPR', 'CCPA', 'DPDP Act 2023')")
    jurisdiction: str = Field(description="Jurisdiction (e.g., 'EU', 'US-California', 'India')")
    regulation_type: RegulationType = Field(description="Category of regulation")
    description: str = Field(description="What the regulation requires")
    key_requirements: list[str] = Field(default_factory=list, description="Specific compliance requirements")
    penalty_range: str = Field(default="", description="Range of penalties for non-compliance")
    compliance_cost: str = Field(default="", description="Estimated compliance cost ($)")
    risk_level: str = Field(default="medium", description="Risk level: low, medium, high, critical")
    effective_date: str = Field(default="", description="When the regulation takes effect")
    sources: list[Source] = Field(default_factory=list)


class ComplianceItem(BaseModel):
    """A single item in the compliance checklist (§4.4, Agent 9).

    Structured compliance checklist with specific requirements, documentation
    needed, and estimated compliance cost. Each item is actionable — not
    'comply with GDPR' but 'appoint a Data Protection Officer within 30 days'."
    """

    requirement: str = Field(description="Specific compliance requirement")
    regulation: str = Field(description="Which regulation this satisfies")
    documentation_needed: list[str] = Field(default_factory=list, description="Documents required for compliance")
    estimated_cost: str = Field(default="", description="Estimated compliance cost ($)")
    estimated_timeline: str = Field(default="", description="Time to achieve compliance")
    priority: str = Field(default="medium", description="Priority: low, medium, high, critical")
    status: str = Field(default="not_started", description="Status: not_started, in_progress, compliant")


class HorizonScanItem(BaseModel):
    """A pending or proposed regulation on the horizon (§4.4, Agent 9).

    Identifies pending regulations, proposed rules, and regulatory trends
    that could impact the business in 1-3 years. Each item has a probability
    assessment and potential impact.
    """

    regulation_name: str = Field(description="Name of pending/proposed regulation")
    jurisdiction: str = Field(description="Jurisdiction")
    status: str = Field(description="Status: proposed, draft, consultation, pending_vote")
    timeline: str = Field(description="Expected timeline (e.g., 'Q3 2025', '2026-2027')")
    probability: str = Field(description="Probability of enactment: low, medium, high")
    potential_impact: str = Field(description="Potential impact on the business")
    recommended_action: str = Field(description="What to do now to prepare")
    sources: list[Source] = Field(default_factory=list)


class EnforcementPrecedent(BaseModel):
    """A regulatory enforcement action against a similar company (§4.4, Agent 9).

    Analyzes enforcement actions to understand regulatory priorities and
    penalties. Not just 'company X was fined $Y' — what did they do, what
    was the penalty, and what does it tell us about regulatory priorities?
    """

    company: str = Field(description="Company that was penalized")
    regulation: str = Field(description="Which regulation was violated")
    violation: str = Field(description="What the company did wrong")
    penalty: str = Field(description="Penalty amount and type")
    date: str = Field(description="When the enforcement action occurred")
    lesson: str = Field(description="What this tells us about regulatory priorities")
    relevance: str = Field(description="How relevant this is to our situation")
    sources: list[Source] = Field(default_factory=list)


class JurisdictionComparison(BaseModel):
    """Regulatory comparison across jurisdictions (§4.4, Agent 9).

    Compares regulatory requirements across jurisdictions to identify the
    most favorable regulatory environment and the most restrictive. The
    jurisdiction with the lightest regulatory touch can be a strategic
    advantage.
    """

    jurisdiction: str = Field(description="Jurisdiction name")
    regulation_count: int = Field(description="Number of applicable regulations")
    compliance_burden: str = Field(description="Overall compliance burden: low, medium, high, very high")
    estimated_annual_cost: str = Field(default="", description="Estimated annual compliance cost ($)")
    key_advantages: list[str] = Field(default_factory=list, description="Regulatory advantages of this jurisdiction")
    key_disadvantages: list[str] = Field(default_factory=list, description="Regulatory disadvantages")
    strategic_assessment: str = Field(default="", description="Strategic implications for this jurisdiction")


class RegulatoryAnalysis(BaseModel):
    """Output from the Regulatory Analyst (Agent 9).

    It knows it is not a lawyer. It maps the landscape, identifies risks,
    and recommends legal counsel for definitive opinions. It doesn't give
    legal advice — it gives regulatory intelligence. It tracks regulatory
    evolution using Wayback Machine, not just current state. It always
    identifies the jurisdiction with the lightest regulatory touch as a
    potential strategic advantage. (§4.4, Agent 9)
    """

    regulatory_map: list[Regulation] = Field(default_factory=list, description="All applicable regulations by jurisdiction")
    jurisdiction_comparison: list[JurisdictionComparison] = Field(default_factory=list, description="Regulatory comparison across jurisdictions")
    compliance_checklist: list[ComplianceItem] = Field(default_factory=list, description="Structured compliance checklist")
    horizon_scan: list[HorizonScanItem] = Field(default_factory=list, description="Pending/proposed regulations (1-3 year horizon)")
    enforcement_precedents: list[EnforcementPrecedent] = Field(default_factory=list, description="Enforcement actions against similar companies")
    lightest_jurisdiction: str = Field(default="", description="Jurisdiction with the lightest regulatory touch (strategic advantage)")
    regulatory_evolution: str = Field(default="", description="How regulations have evolved over time (Wayback Machine data)")
    legal_disclaimer: str = Field(default="This is regulatory intelligence, not legal advice. Consult qualified legal counsel for definitive opinions.", description="Disclaimer that this is not legal advice")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Sustainability Analysis — the Sustainability Analyst's output (§4.4, Agent 10)
# ─────────────────────────────────────────────────────────────────────────────


class ESGFramework(str, Enum):
    """ESG assessment frameworks (§4.4, Agent 10)."""

    MSCI_ESG = "msci_esg"            # MSCI ESG Ratings
    SASB = "sasb"                    # SASB standards
    TCFD = "tcfd"                    # TCFD recommendations
    GRI = "gri"                      # GRI standards
    CSRD = "csrd"                    # EU CSRD
    CDP = "cdp"                      # CDP disclosure


class ESGScore(BaseModel):
    """ESG score on a specific framework (§4.4, Agent 10).

    Not just a letter grade — the specific score, the key strengths and
    weaknesses, and which stakeholder audience this framework matters for.
    Investors want TCFD, regulators want CSRD, customers want GRI.
    """

    framework: ESGFramework = Field(description="ESG framework used")
    score: str = Field(description="Score or rating (e.g., 'AA', '8.2/10', 'A-')")
    key_strengths: list[str] = Field(default_factory=list, description="Key ESG strengths")
    key_weaknesses: list[str] = Field(default_factory=list, description="Key ESG weaknesses")
    stakeholder_audience: str = Field(default="", description="Who cares about this framework (investors, regulators, customers)")
    is_mandatory: bool = Field(default=False, description="Is this framework mandatory for the business?")


class EmissionSource(BaseModel):
    """A single emission source in the carbon footprint (§4.4, Agent 10).

    Identifies the specific emission sources that account for 80% of the
    footprint and calculates the abatement cost for each. Not just 'energy
    use' — 'electricity from coal-fired grid power, 5000 MWh/yr, 2500 tCO2e,
    abatement: switch to renewable PPA at $20/tCO2e, total cost $50K/yr'."
    """

    source_name: str = Field(description="Specific emission source")
    scope: str = Field(description="Scope 1 (direct), Scope 2 (purchased electricity), or Scope 3 (value chain)")
    emissions_tco2e: str = Field(description="Annual emissions in tonnes CO2e")
    percentage_of_total: str = Field(default="", description="% of total footprint")
    abatement_action: str = Field(default="", description="Specific action to reduce emissions")
    abatement_cost_per_tco2e: str = Field(default="", description="Cost per tonne CO2e to abate ($)")
    total_abatement_cost: str = Field(default="", description="Total annual cost of abatement ($)")
    is_top_80: bool = Field(default=False, description="Is this in the top 80% of emission sources?")


class CarbonFootprint(BaseModel):
    """Carbon footprint broken down by scope (§4.4, Agent 10).

    Scope 1: Direct emissions (owned/controlled sources — combustion, fleet).
    Scope 2: Indirect emissions from purchased electricity, heat, steam.
    Scope 3: Value chain emissions (suppliers, product use, end-of-life).
    The HYPERION Sustainability Analyst identifies the specific sources that
    account for 80% of the footprint and calculates abatement cost for each.
    """

    scope1_tco2e: str = Field(default="", description="Scope 1 emissions (direct) in tCO2e")
    scope2_tco2e: str = Field(default="", description="Scope 2 emissions (purchased electricity) in tCO2e")
    scope3_tco2e: str = Field(default="", description="Scope 3 emissions (value chain) in tCO2e")
    total_tco2e: str = Field(default="", description="Total emissions in tCO2e")
    emission_sources: list[EmissionSource] = Field(default_factory=list, description="Top emission sources with abatement costs")
    top_80_sources: list[EmissionSource] = Field(default_factory=list, description="Sources accounting for 80% of footprint")
    total_abatement_cost: str = Field(default="", description="Total annual cost to abate all identified sources ($)")


class ReportingRequirement(BaseModel):
    """An ESG/sustainability reporting requirement (§4.4, Agent 10).

    Maps reporting requirements (CSRD, SEC climate, TCFD, CDP). Identifies
    which reports are mandatory vs. voluntary and the penalty for non-
    compliance.
    """

    report_name: str = Field(description="Name of the report (e.g., 'CSRD', 'SEC Climate Disclosure', 'TCFD', 'CDP')")
    jurisdiction: str = Field(default="", description="Jurisdiction (e.g., 'EU', 'US', 'Global')")
    is_mandatory: bool = Field(default=False, description="Is this report mandatory?")
    deadline: str = Field(default="", description="Reporting deadline/frequency")
    key_disclosures: list[str] = Field(default_factory=list, description="Key disclosure requirements")
    penalty_for_non_compliance: str = Field(default="", description="Penalty for not reporting")
    estimated_compliance_cost: str = Field(default="", description="Estimated cost of compliance ($)")


class GreenFinancingOpportunity(BaseModel):
    """A green financing opportunity (§4.4, Agent 10).

    Evaluates green bonds, sustainability-linked loans, and carbon credit
    opportunities. Calculates potential financing cost savings — not just
    'green bonds exist' but 'a $50M green bond at 3.5% vs. 4.5% conventional
    saves $500K/yr in interest.'"
    """

    instrument: str = Field(description="Financing instrument (green bond, sustainability-linked loan, carbon credits)")
    description: str = Field(description="Description of the opportunity")
    estimated_amount: str = Field(default="", description="Estimated financing amount ($)")
    conventional_rate: str = Field(default="", description="Conventional financing rate (%)")
    green_rate: str = Field(default="", description="Green financing rate (%)")
    annual_savings: str = Field(default="", description="Annual savings from green financing ($)")
    eligibility_criteria: list[str] = Field(default_factory=list, description="Criteria to qualify")
    sources: list[Source] = Field(default_factory=list)


class CircularEconomyAssessment(BaseModel):
    """Circular economy assessment (§4.4, Agent 10).

    Assesses opportunities for circular economy models (reduce, reuse,
    recycle, refurbish) in the business model. Each opportunity has a
    $ value and implementation cost.
    """

    opportunity: str = Field(description="Circular economy opportunity (reduce, reuse, recycle, refurbish)")
    description: str = Field(default="", description="How to implement this opportunity")
    current_waste: str = Field(default="", description="Current waste this addresses")
    implementation_cost: str = Field(default="", description="Cost to implement ($)")
    annual_value: str = Field(default="", description="Annual $ value (cost savings + new revenue)")
    roi: str = Field(default="", description="ROI of implementing this opportunity")
    feasibility: str = Field(default="medium", description="Feasibility: low, medium, high")


class SustainabilityAnalysis(BaseModel):
    """Output from the Sustainability Analyst (Agent 10).

    It doesn't just calculate a carbon number — it identifies the specific
    emission sources that account for 80% of the footprint and calculates
    the abatement cost for each. It maps ESG to financial impact (green
    financing savings, regulatory penalty avoidance, investor access) not
    just to compliance. It always identifies which ESG framework matters
    for the specific stakeholder (investors want TCFD, regulators want
    CSRD, customers want GRI). (§4.4, Agent 10)
    """

    esg_scores: list[ESGScore] = Field(default_factory=list, description="ESG scores across frameworks")
    most_relevant_framework: str = Field(default="", description="Most relevant ESG framework for the stakeholder")
    carbon_footprint: CarbonFootprint | None = Field(default=None, description="Carbon footprint with abatement costs")
    reporting_requirements: list[ReportingRequirement] = Field(default_factory=list, description="Mandatory and voluntary reporting requirements")
    green_financing_opportunities: list[GreenFinancingOpportunity] = Field(default_factory=list, description="Green financing opportunities with $ savings")
    circular_economy: list[CircularEconomyAssessment] = Field(default_factory=list, description="Circular economy opportunities")
    total_green_financing_savings: str = Field(default="", description="Total annual savings from green financing ($)")
    total_abatement_cost: str = Field(default="", description="Total annual cost to abate all emission sources ($)")
    financial_impact_summary: str = Field(default="", description="ESG mapped to financial impact (savings, penalty avoidance, investor access)")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Consumer Insights — the Consumer Insights Analyst's output (§4.4, Agent 11)
# ─────────────────────────────────────────────────────────────────────────────


class Persona(BaseModel):
    """A data-driven customer persona (§4.4, Agent 11).

    NOT generic — grounded in scraped review data and survey responses.
    Not "Tech-Savvy Tom, age 25-35." It says "Based on 847 G2 reviews and
    234 Reddit threads, the primary persona is a mid-market IT manager
    (35-45, $80K-$120K budget) whose top frustration is 'integration
    complexity' (mentioned in 34% of negative reviews) and whose primary
    buying trigger is 'peer recommendation from a similar company'
    (mentioned in 41% of positive reviews)."
    """

    name: str = Field(description="Persona name (descriptive, not generic)")
    demographics: str = Field(description="Age, income, geography, company size, role")
    behaviors: list[str] = Field(default_factory=list, description="Observed behaviors — usage patterns, purchase frequency, feature adoption")
    motivations: list[str] = Field(default_factory=list, description="What drives this persona to buy/use")
    frustrations: list[str] = Field(default_factory=list, description="Top frustrations with specific frequency from review data")
    preferred_channels: list[str] = Field(default_factory=list, description="Where this persona gets information and makes purchases")
    buying_triggers: list[str] = Field(default_factory=list, description="Specific triggers that drive purchase (with % from review data)")
    data_basis: str = Field(default="", description="What data this persona is based on (e.g., '847 G2 reviews, 234 Reddit threads')")
    is_primary: bool = Field(default=False, description="Is this the primary persona?")


class JourneyStage(BaseModel):
    """A stage in the customer journey (§4.4, Agent 11).

    Maps the end-to-end customer journey from awareness to advocacy.
    Identifies friction points, drop-off points, and moments of truth.
    """

    stage: str = Field(description="Journey stage (awareness, consideration, purchase, onboarding, usage, advocacy, etc.)")
    description: str = Field(default="", description="What happens at this stage")
    touchpoints: list[str] = Field(default_factory=list, description="Customer touchpoints at this stage")
    friction_points: list[str] = Field(default_factory=list, description="Friction points at this stage")
    drop_off_rate: str = Field(default="", description="Drop-off rate at this stage (if known)")
    is_moment_of_truth: bool = Field(default=False, description="Is this a 'moment of truth' that determines customer outcome?")
    improvement_opportunity: str = Field(default="", description="Specific improvement opportunity at this stage")


class NPSAnalysis(BaseModel):
    """Net Promoter Score analysis (§4.4, Agent 11).

    Analyzes NPS data and qualitative feedback to identify the drivers
    of promotion and detraction. Not just a number — the specific reasons
    behind it with frequency data.
    """

    nps_score: str = Field(default="", description="Net Promoter Score (e.g., '+42', '-15')")
    promoter_percentage: str = Field(default="", description="% of promoters (9-10)")
    passive_percentage: str = Field(default="", description="% of passives (7-8)")
    detractor_percentage: str = Field(default="", description="% of detractors (0-6)")
    promotion_drivers: list[str] = Field(default_factory=list, description="Specific drivers of promotion (with frequency from feedback)")
    detraction_drivers: list[str] = Field(default_factory=list, description="Specific drivers of detraction (with frequency from feedback)")
    sample_size: str = Field(default="", description="Sample size of NPS data")
    key_quotes: list[str] = Field(default_factory=list, description="Representative quotes from promoters and detractors")


class SegmentationApproach(str, Enum):
    """Customer segmentation approaches (§4.4, Agent 11)."""

    DEMOGRAPHIC = "demographic"        # Age, income, geography, company size
    BEHAVIORAL = "behavioral"          # Usage patterns, purchase frequency, feature adoption
    PSYCHOGRAPHIC = "psychographic"    # Values, motivations, attitudes


class CustomerSegment(BaseModel):
    """A customer segment (§4.4, Agent 11).

    Three approaches: demographic, behavioral, psychographic. Identifies
    which segmentation approach is most predictive of purchase behavior.
    """

    approach: SegmentationApproach = Field(description="Segmentation approach used")
    segment_name: str = Field(description="Name of the segment")
    size_percentage: str = Field(default="", description="% of total market in this segment")
    characteristics: list[str] = Field(default_factory=list, description="Key characteristics of this segment")
    purchase_probability: str = Field(default="", description="Purchase probability/propensity for this segment")
    value: str = Field(default="", description="Customer lifetime value or annual value ($)")
    is_most_predictive: bool = Field(default=False, description="Is this the most predictive segment for purchase behavior?")


class WillingnessToPay(BaseModel):
    """Willingness-to-pay analysis using Van Westendorp methodology (§4.4, Agent 11).

    Estimates the price point that maximizes revenue using the Van Westendorp
    price sensitivity meter methodology. Not just 'charge $50' — identifies
    the optimal price point, too-cheap price, too-expensive price, and the
    range of acceptable prices.
    """

    optimal_price_point: str = Field(default="", description="Price point that maximizes revenue ($)")
    too_cheap_price: str = Field(default="", description="Price below which customers question quality ($)")
    too_expensive_price: str = Field(default="", description="Price above which customers won't buy ($)")
    acceptable_range: str = Field(default="", description="Range of acceptable prices ($- $)")
    revenue_at_optimal: str = Field(default="", description="Estimated revenue at optimal price point ($)")
    methodology: str = Field(default="Van Westendorp Price Sensitivity Meter", description="Methodology used")
    data_basis: str = Field(default="", description="What data this analysis is based on")


class DemandEstimate(BaseModel):
    """Demand estimation (§4.4, Agent 11).

    Estimates demand using willingness-to-pay analysis, conjoint analysis
    proxies, and price elasticity estimation from market data.
    """

    total_addressable_market: str = Field(default="", description="TAM ($)")
    serviceable_addressable_market: str = Field(default="", description="SAM ($)")
    serviceable_obtainable_market: str = Field(default="", description="SOM / market share achievable (%)")
    price_elasticity: str = Field(default="", description="Price elasticity of demand (e.g., '-1.5')")
    demand_at_current_price: str = Field(default="", description="Estimated demand at current price")
    demand_at_optimal_price: str = Field(default="", description="Estimated demand at optimal price")
    revenue_forecast: str = Field(default="", description="Revenue forecast at optimal price ($/yr)")
    methodology: str = Field(default="Conjoint analysis proxy + price elasticity from market data", description="Methodology used")


class ConsumerInsights(BaseModel):
    """Output from the Consumer Insights Analyst (Agent 11).

    It builds personas from real scraped data, not from imagination. It
    doesn't say "Tech-Savvy Tom, age 25-35." It says "Based on 847 G2
    reviews and 234 Reddit threads, the primary persona is a mid-market
    IT manager (35-45, $80K-$120K budget) whose top frustration is
    'integration complexity' (mentioned in 34% of negative reviews) and
    whose primary buying trigger is 'peer recommendation from a similar
    company' (mentioned in 41% of positive reviews)." (§4.4, Agent 11)
    """

    personas: list[Persona] = Field(default_factory=list, description="Data-driven customer personas grounded in scraped review data")
    journey_map: list[JourneyStage] = Field(default_factory=list, description="End-to-end customer journey with friction points and moments of truth")
    nps_analysis: NPSAnalysis | None = Field(default=None, description="NPS analysis with promotion/detraction drivers")
    segments: list[CustomerSegment] = Field(default_factory=list, description="Customer segments across demographic, behavioral, psychographic approaches")
    most_predictive_segmentation: str = Field(default="", description="Which segmentation approach is most predictive of purchase behavior")
    demand_estimate: DemandEstimate | None = Field(default=None, description="Demand estimation with price elasticity")
    willingness_to_pay: WillingnessToPay | None = Field(default=None, description="Willingness-to-pay analysis using Van Westendorp methodology")
    total_reviews_analyzed: str = Field(default="", description="Total number of reviews/data points analyzed")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# M&A Analysis — the M&A Analyst's output (§4.4, Agent 12)
# ─────────────────────────────────────────────────────────────────────────────


class AcquisitionTarget(BaseModel):
    """An acquisition target (§4.4, Agent 12).

    Screened using criteria: strategic fit, size, geography, technology,
    talent, customer base. Part of a long list (20-50) or short list (5-10)
    with rationale for each.
    """

    company_name: str = Field(description="Target company name")
    ticker: str = Field(default="", description="Stock ticker if public")
    description: str = Field(default="", description="Brief company description")
    headquarters: str = Field(default="", description="HQ location")
    employees: str = Field(default="", description="Employee count")
    revenue: str = Field(default="", description="Annual revenue ($)")
    strategic_fit: str = Field(default="", description="Strategic fit rationale")
    acquisition_rationale: str = Field(default="", description="Why acquire this company")
    list_stage: str = Field(default="long", description="long or short")
    risks: list[str] = Field(default_factory=list, description="Key risks associated with this target")


class SynergyAnalysis(BaseModel):
    """Synergy analysis with reality discount (§4.4, Agent 12).

    Quantifies revenue synergies (cross-sell, upsell, new markets) and cost
    synergies (headcount reduction, facility consolidation, procurement
    savings). ALWAYS with a reality discount — synergies rarely materialize
    at 100% of the estimate. 50-70% of estimated synergies typically
    materialize.
    """

    revenue_synergies: list[str] = Field(default_factory=list, description="Revenue synergies (cross-sell, upsell, new markets)")
    revenue_synergy_value: str = Field(default="", description="Estimated annual revenue synergy value ($)")
    cost_synergies: list[str] = Field(default_factory=list, description="Cost synergies (headcount, facilities, procurement)")
    cost_synergy_value: str = Field(default="", description="Estimated annual cost synergy value ($)")
    total_estimated_synergies: str = Field(default="", description="Total estimated annual synergies ($)")
    reality_discount_percentage: str = Field(default="40%", description="Reality discount applied (synergies rarely materialize at 100%)")
    realizable_synergies: str = Field(default="", description="Synergies after reality discount ($)")
    synergy_timeline: str = Field(default="", description="Timeline for synergy realization")


class IntegrationWorkstream(BaseModel):
    """A workstream in the 100-day integration plan (§4.4, Agent 12)."""

    workstream: str = Field(description="Workstream name (e.g., 'Sales integration', 'IT systems migration')")
    owner: str = Field(default="", description="Workstream owner")
    key_milestones: list[str] = Field(default_factory=list, description="Key milestones")
    day_1_actions: list[str] = Field(default_factory=list, description="Day 1 actions")
    day_30_milestones: list[str] = Field(default_factory=list, description="Day 30 milestones")
    day_100_milestones: list[str] = Field(default_factory=list, description="Day 100 milestones")
    risk_flags: list[str] = Field(default_factory=list, description="Risk flags for this workstream")


class IntegrationPlan(BaseModel):
    """100-day integration plan (§4.4, Agent 12).

    Builds a 100-day integration plan with workstreams, milestones, owners,
    and risk flags. Identifies the top 3 integration risks. The deal is the
    easy part — integration is the hard part.
    """

    workstreams: list[IntegrationWorkstream] = Field(default_factory=list, description="Integration workstreams")
    top_3_integration_risks: list[str] = Field(default_factory=list, description="Top 3 integration risks")
    day_1_priorities: list[str] = Field(default_factory=list, description="Day 1 priorities")
    success_metrics: list[str] = Field(default_factory=list, description="Success metrics for integration")


class ValuationGap(BaseModel):
    """Valuation gap analysis (§4.4, Agent 12).

    Compares the acquirer's maximum acceptable price to the target's minimum
    acceptable price. Identifies the zone of possible agreement.
    """

    acquirer_max_price: str = Field(default="", description="Acquirer's maximum acceptable price ($)")
    target_min_price: str = Field(default="", description="Target's minimum acceptable price ($)")
    zone_of_possible_agreement: str = Field(default="", description="Zone of possible agreement ($ - $)")
    likely_transaction_price: str = Field(default="", description="Likely transaction price ($)")
    premium_to_market: str = Field(default="", description="Premium to current market price (%)")
    is_deal_feasible: bool = Field(default=False, description="Is there a zone of possible agreement?")


class AccretionDilution(BaseModel):
    """Accretion/dilution analysis (§4.4, Agent 12).

    Models the impact of the acquisition on the acquirer's EPS over 1-3
    years. Identifies whether the deal is accretive or dilutive and under
    what conditions.
    """

    year_1_eps_impact: str = Field(default="", description="Year 1 EPS impact (% accretive/dilutive)")
    year_2_eps_impact: str = Field(default="", description="Year 2 EPS impact (%)")
    year_3_eps_impact: str = Field(default="", description="Year 3 EPS impact (%)")
    is_accretive: bool = Field(default=False, description="Is the deal accretive in year 1?")
    accretive_conditions: list[str] = Field(default_factory=list, description="Conditions under which the deal becomes accretive")
    pro_forma_revenue: str = Field(default="", description="Pro forma combined revenue ($)")
    pro_forma_ebitda: str = Field(default="", description="Pro forma combined EBITDA ($)")
    deal_financing: str = Field(default="", description="Deal financing structure (cash, stock, debt)")


class CulturalFit(BaseModel):
    """Cultural fit assessment (§4.4, Agent 12).

    Evaluates cultural compatibility using public data (Glassdoor reviews,
    LinkedIn company pages, employee sentiment). Cultural mismatch is the
    #1 reason M&A deals fail to deliver synergies.
    """

    acquirer_culture_summary: str = Field(default="", description="Acquirer culture summary from public data")
    target_culture_summary: str = Field(default="", description="Target culture summary from public data")
    compatibility_score: str = Field(default="", description="Cultural compatibility score (e.g., '7/10')")
    alignment_areas: list[str] = Field(default_factory=list, description="Areas of cultural alignment")
    misalignment_areas: list[str] = Field(default_factory=list, description="Areas of cultural misalignment")
    glassdoor_ratings: dict[str, str] = Field(default_factory=dict, description="Glassdoor ratings for acquirer and target")
    integration_risk: str = Field(default="", description="Cultural integration risk level (low, medium, high)")
    data_basis: str = Field(default="", description="What data this assessment is based on (e.g., 'Glassdoor reviews, LinkedIn pages')")


class MAAnalysis(BaseModel):
    """Output from the M&A Analyst (Agent 12).

    It always applies a reality discount to synergies — 50-70% of estimated
    synergies typically materialize. It always assesses cultural fit because
    that's the #1 failure cause. It always builds an integration plan, not
    just a deal rationale — because the deal is the easy part, integration
    is the hard part. (§4.4, Agent 12)
    """

    acquisition_criteria: str = Field(default="", description="Acquisition criteria defined with Engagement Director")
    long_list: list[AcquisitionTarget] = Field(default_factory=list, description="Long list of targets (20-50)")
    short_list: list[AcquisitionTarget] = Field(default_factory=list, description="Short list of targets (5-10) with rationale")
    synergy_analysis: SynergyAnalysis | None = Field(default=None, description="Synergy analysis with reality discount")
    valuation_gap: ValuationGap | None = Field(default=None, description="Valuation gap analysis with zone of possible agreement")
    accretion_dilution: AccretionDilution | None = Field(default=None, description="Accretion/dilution analysis (EPS impact)")
    cultural_fit: CulturalFit | None = Field(default=None, description="Cultural fit assessment")
    integration_plan: IntegrationPlan | None = Field(default=None, description="100-day integration plan")
    top_integration_risks: list[str] = Field(default_factory=list, description="Top integration risks")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Innovation Analysis — the Innovation Analyst's output (§4.4, Agent 13)
# ─────────────────────────────────────────────────────────────────────────────


class HypeCyclePhase(str, Enum):
    """Gartner hype cycle phases (§4.4, Agent 13)."""

    INNOVATION_TRIGGER = "innovation_trigger"
    PEAK_OF_INFLATED_EXPECTATIONS = "peak_of_inflated_expectations"
    TROUGH_OF_DISILLUSIONMENT = "trough_of_disillusionment"
    SLOPE_OF_ENLIGHTENMENT = "slope_of_enlightenment"
    PLATEAU_OF_PRODUCTIVITY = "plateau_of_productivity"


class TechnologyTRL(BaseModel):
    """Technology Readiness Level assessment (§4.4, Agent 13).

    Assesses technologies on the NASA TRL scale (1-9) from basic research to
    deployed. Identifies which emerging techs are ready for production use vs.
    still experimental. Not just 'AI is transformative' — 'LLM-based customer
    support is at TRL 8 (ready for production) while autonomous agents are at
    TRL 4 (2-3 years from production readiness).'
    """

    technology: str = Field(description="Technology name")
    trl_level: int = Field(description="TRL level 1-9 (1=basic research, 9=deployed)")
    trl_description: str = Field(default="", description="What this TRL level means for this technology")
    is_production_ready: bool = Field(default=False, description="Is this technology ready for production use (TRL 7+)?")
    time_to_production: str = Field(default="", description="Estimated time to production readiness")
    key_bottlenecks: list[str] = Field(default_factory=list, description="Key bottlenecks preventing higher TRL")
    evidence: str = Field(default="", description="Evidence supporting this TRL assessment")


class HypeCyclePosition(BaseModel):
    """Gartner hype cycle positioning (§4.4, Agent 13).

    Plots technologies on the hype cycle: innovation trigger → peak of
    inflated expectations → trough of disillusionment → slope of enlightenment
    → plateau of productivity. Identifies where each tech currently sits.
    """

    technology: str = Field(description="Technology name")
    phase: HypeCyclePhase = Field(description="Current hype cycle phase")
    phase_description: str = Field(default="", description="What this phase means for this technology")
    years_to_plateau: str = Field(default="", description="Estimated years to plateau of productivity")
    is_overhyped: bool = Field(default=False, description="Is this technology overhyped relative to its TRL?")
    hype_vs_reality_gap: str = Field(default="", description="Gap between hype and reality")


class HorizonScanItem(BaseModel):
    """A horizon scan signal (§4.4, Agent 13).

    Systematically scans for signals of change across 3 horizons:
    H1 (current, 0-12 months), H2 (emerging, 1-3 years), H3 (future, 3-10 years).
    """

    horizon: str = Field(description="H1 (0-12 months), H2 (1-3 years), or H3 (3-10 years)")
    signal: str = Field(description="Signal of change")
    description: str = Field(default="", description="Description of the signal")
    impact: str = Field(default="", description="Potential impact: low, medium, high, transformative")
    probability: str = Field(default="", description="Probability of materializing")
    time_horizon: str = Field(default="", description="When this signal is expected to materialize")
    recommended_action: str = Field(default="", description="Recommended action for this signal")


class DisruptionPattern(str, Enum):
    """Disruption patterns (§4.4, Agent 13)."""

    LOW_END = "low_end"              # Cheaper, simpler — serves least demanding customers
    NEW_MARKET = "new_market"        # Serving non-consumers — creates new market
    ARCHITECTURAL = "architectural"  # Reconfiguring the value chain


class DisruptionAnalysis(BaseModel):
    """Disruption pattern analysis (§4.4, Agent 13).

    Identifies which disruption pattern applies: low-end disruption (cheaper,
    simpler), new-market disruption (serving non-consumers), or architectural
    disruption (reconfiguring the value chain).
    """

    pattern: DisruptionPattern = Field(description="Disruption pattern identified")
    description: str = Field(default="", description="How this disruption pattern applies")
    disrupted_companies: list[str] = Field(default_factory=list, description="Companies likely to be disrupted")
    disrupting_companies: list[str] = Field(default_factory=list, description="Companies driving the disruption")
    disruption_timeline: str = Field(default="", description="Timeline for disruption to play out")
    defensibility: str = Field(default="", description="Can incumbents defend against this disruption?")


class FirstMoverAnalysis(BaseModel):
    """First-mover vs. fast-follower analysis (§4.4, Agent 13).

    Analyzes whether first-mover advantage applies in this market or whether
    fast-follower is the better strategy. Considers: network effects, switching
    costs, learning curve, patent protection, and brand. Not just 'be first' —
    'first-mover advantage is weak here because switching costs are low and
    network effects are absent. Fast-follower is the better strategy — let
    others bear the R&D cost and learn from their mistakes.'
    """

    recommendation: str = Field(default="", description="first_mover, fast_follower, or fast_second")
    rationale: str = Field(default="", description="Why this strategy is recommended")
    network_effects: str = Field(default="", description="Strength of network effects: strong, moderate, weak, none")
    switching_costs: str = Field(default="", description="Switching costs for customers: high, medium, low")
    learning_curve: str = Field(default="", description="Learning curve advantage: steep, moderate, flat")
    patent_protection: str = Field(default="", description="Patent protection strength: strong, moderate, weak, none")
    brand_advantage: str = Field(default="", description="Brand advantage for first mover: strong, moderate, weak")
    first_mover_examples: list[str] = Field(default_factory=list, description="Examples of first movers in this space and their outcomes")
    fast_follower_examples: list[str] = Field(default_factory=list, description="Examples of fast followers and their outcomes")


class InnovationPortfolioItem(BaseModel):
    """An item in the innovation portfolio (§4.4, Agent 13)."""

    initiative: str = Field(description="Innovation initiative name")
    horizon: str = Field(description="H1 (current), H2 (emerging), or H3 (future)")
    investment_level: str = Field(default="", description="Investment level: high, medium, low")
    expected_roi: str = Field(default="", description="Expected ROI")
    status: str = Field(default="", description="Status: exploring, piloting, scaling, deployed")
    risk_level: str = Field(default="", description="Risk level: high, medium, low")


class InnovationPortfolio(BaseModel):
    """Innovation portfolio assessment (§4.4, Agent 13).

    Maps the company's innovation initiatives on the 3-horizon portfolio.
    Identifies if the portfolio is balanced or over-invested in one horizon.
    """

    items: list[InnovationPortfolioItem] = Field(default_factory=list, description="Innovation initiatives")
    h1_count: int = Field(default=0, description="Number of H1 (current) initiatives")
    h2_count: int = Field(default=0, description="Number of H2 (emerging) initiatives")
    h3_count: int = Field(default=0, description="Number of H3 (future) initiatives")
    is_balanced: bool = Field(default=False, description="Is the portfolio balanced across horizons?")
    imbalance_description: str = Field(default="", description="If unbalanced, what's the imbalance?")
    recommendation: str = Field(default="", description="Portfolio rebalancing recommendation")


class InnovationAnalysis(BaseModel):
    """Output from the Innovation Analyst (Agent 13).

    It separates hype from reality using the Gartner hype cycle and TRL scale.
    It doesn't say 'AI is transformative' — it says 'LLM-based customer support
    is at the slope of enlightenment (TRL 8) and ready for production, while
    autonomous agents are at the peak of inflated expectations (TRL 4) and
    2-3 years from production readiness.' It always assesses first-mover
    advantage — because in some markets, being first is a disadvantage.
    (§4.4, Agent 13)
    """

    trl_assessments: list[TechnologyTRL] = Field(default_factory=list, description="TRL assessments for each technology")
    hype_cycle_positions: list[HypeCyclePosition] = Field(default_factory=list, description="Gartner hype cycle positioning for each technology")
    horizon_scan: list[HorizonScanItem] = Field(default_factory=list, description="Horizon scan signals across H1/H2/H3")
    disruption_analysis: DisruptionAnalysis | None = Field(default=None, description="Disruption pattern analysis")
    first_mover_analysis: FirstMoverAnalysis | None = Field(default=None, description="First-mover vs. fast-follower analysis")
    innovation_portfolio: InnovationPortfolio | None = Field(default=None, description="Innovation portfolio assessment")
    key_emerging_technologies: list[str] = Field(default_factory=list, description="Key emerging technologies identified")
    technologies_ready_for_production: list[str] = Field(default_factory=list, description="Technologies at TRL 7+ ready for production")
    technologies_overhyped: list[str] = Field(default_factory=list, description="Technologies that are overhyped relative to TRL")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Strategy Analysis — the Strategy Analyst's output (§4.4, Agent 14)
# ─────────────────────────────────────────────────────────────────────────────


class ForceStrength(str, Enum):
    """Strength rating for Porter's Five Forces (§4.4, Agent 14)."""

    STRONG = "strong"
    MODERATE = "moderate"
    WEAK = "weak"


class PorterFiveForces(BaseModel):
    """Porter's Five Forces analysis (§4.4, Agent 14).

    Analyzes industry attractiveness through five forces. Each force is scored
    as strong/moderate/weak with a specific rationale. Not just 'rivalry is
    high' — 'rivalry is STRONG because 3 well-funded competitors compete on
    price in a mature market with 70% combined market share.'
    """

    threat_of_new_entrants: ForceStrength = Field(default=ForceStrength.MODERATE, description="Barriers to entry assessment")
    new_entrants_rationale: str = Field(default="", description="Why this strength — specific barriers or lack thereof")
    bargaining_power_suppliers: ForceStrength = Field(default=ForceStrength.MODERATE, description="Supplier power assessment")
    suppliers_rationale: str = Field(default="", description="Why this strength — supplier concentration, switching costs")
    bargaining_power_buyers: ForceStrength = Field(default=ForceStrength.MODERATE, description="Buyer power assessment")
    buyers_rationale: str = Field(default="", description="Why this strength — buyer concentration, price sensitivity")
    threat_of_substitutes: ForceStrength = Field(default=ForceStrength.MODERATE, description="Substitute threat assessment")
    substitutes_rationale: str = Field(default="", description="Why this strength — alternative products/services")
    competitive_rivalry: ForceStrength = Field(default=ForceStrength.MODERATE, description="Rivalry intensity assessment")
    rivalry_rationale: str = Field(default="", description="Why this strength — competitor count, growth rate, differentiation")
    overall_attractiveness: str = Field(default="", description="Overall industry attractiveness summary")
    frameworks_used: list[str] = Field(default_factory=list, description="Which frameworks were selected and why")


class BCGCategory(str, Enum):
    """BCG growth-share matrix categories (§4.4, Agent 14)."""

    STAR = "star"                    # High growth, high market share
    CASH_COW = "cash_cow"            # Low growth, high market share
    QUESTION_MARK = "question_mark"  # High growth, low market share
    DOG = "dog"                      # Low growth, low market share


class BCGUnit(BaseModel):
    """A business unit in the BCG growth-share matrix (§4.4, Agent 14)."""

    unit_name: str = Field(description="Business unit or product name")
    category: BCGCategory = Field(description="BCG category: star, cash_cow, question_mark, or dog")
    market_growth_rate: str = Field(default="", description="Market growth rate (%)")
    relative_market_share: str = Field(default="", description="Relative market share vs. leading competitor")
    recommendation: str = Field(default="", description="Resource allocation recommendation: invest, harvest, divest, hold")


class BCGMatrix(BaseModel):
    """BCG growth-share matrix (§4.4, Agent 14).

    Plots the company's products/business units on the growth-share matrix.
    Identifies resource allocation recommendations for each unit.
    """

    units: list[BCGUnit] = Field(default_factory=list, description="Business units plotted on the matrix")
    stars: list[str] = Field(default_factory=list, description="Star units (invest)")
    cash_cows: list[str] = Field(default_factory=list, description="Cash cow units (harvest)")
    question_marks: list[str] = Field(default_factory=list, description="Question mark units (invest selectively)")
    dogs: list[str] = Field(default_factory=list, description="Dog units (divest)")
    portfolio_balance: str = Field(default="", description="Is the portfolio balanced across categories?")


class SWOTItem(BaseModel):
    """A single SWOT factor (§4.4, Agent 14)."""

    factor: str = Field(description="The factor")
    description: str = Field(default="", description="Description of the factor")
    evidence: str = Field(default="", description="Evidence supporting this factor")


class TOWSStrategy(BaseModel):
    """A TOWS matrix strategy (§4.4, Agent 14).

    TOWS converts SWOT from a snapshot into strategic options:
    - SO: Use strengths to maximize opportunities
    - WO: Overcome weaknesses to pursue opportunities
    - ST: Use strengths to minimize threats
    - WT: Minimize weaknesses and avoid threats
    """

    strategy_type: str = Field(description="SO, WO, ST, or WT")
    strategy: str = Field(description="The strategic option")
    description: str = Field(default="", description="How this strategy works")


class SWOTTOWS(BaseModel):
    """SWOT analysis with TOWS matrix conversion (§4.4, Agent 14).

    SWOT is a snapshot, not a strategy. The critical distinction: convert SWOT
    into a TOWS matrix to generate strategic options (SO, WO, ST, WT strategies).
    """

    strengths: list[SWOTItem] = Field(default_factory=list, description="Internal strengths")
    weaknesses: list[SWOTItem] = Field(default_factory=list, description="Internal weaknesses")
    opportunities: list[SWOTItem] = Field(default_factory=list, description="External opportunities")
    threats: list[SWOTItem] = Field(default_factory=list, description="External threats")
    tows_strategies: list[TOWSStrategy] = Field(default_factory=list, description="TOWS matrix strategies (SO, WO, ST, WT)")
    note: str = Field(default="SWOT is a snapshot, not a strategy. TOWS converts it to strategic options.", description="Critical distinction note")


class BlueOceanStrategy(BaseModel):
    """Blue Ocean strategy analysis (§4.4, Agent 14).

    Identifies whether the company can create uncontested market space using
    the eliminate-reduce-raise-create framework. Builds a strategy canvas
    comparing the company to competitors.
    """

    eliminate: list[str] = Field(default_factory=list, description="What factors should be eliminated that the industry takes for granted?")
    reduce: list[str] = Field(default_factory=list, description="What factors should be reduced well below the industry standard?")
    raise_factors: list[str] = Field(default_factory=list, validation_alias="raise", description="What factors should be raised well above the industry standard?")
    create: list[str] = Field(default_factory=list, description="What factors should be created that the industry has never offered?")
    strategy_canvas: list[dict[str, str]] = Field(default_factory=list, description="Strategy canvas comparing company to competitors on key factors")
    is_blue_ocean_feasible: bool = Field(default=False, description="Is Blue Ocean strategy feasible for this company?")
    new_market_space: str = Field(default="", description="Description of the uncontested market space")


class VRIOResult(BaseModel):
    """VRIO assessment of a single resource/capability (§4.4, Agent 14).

    Evaluates resources on Value, Rarity, Imitability, and Organization.
    Identifies which resources provide sustainable competitive advantage.
    """

    resource: str = Field(description="Resource or capability name")
    is_valuable: bool = Field(default=False, description="Does it enable the firm to exploit opportunities or neutralize threats?")
    is_rare: bool = Field(default=False, description="Is it controlled by only a few firms?")
    is_inimitable: bool = Field(default=False, description="Is it costly for other firms to imitate?")
    is_organized: bool = Field(default=False, description="Is the firm organized to exploit this resource?")
    competitive_implication: str = Field(default="", description="Competitive disadvantage, parity, temporary advantage, or sustained advantage")
    description: str = Field(default="", description="Description of the resource and its strategic value")


class VRIOAssessment(BaseModel):
    """VRIO framework assessment (§4.4, Agent 14).

    Evaluates resources/capabilities on Value, Rarity, Imitability, and
    Organization. Identifies which resources provide sustainable competitive
    advantage.
    """

    resources: list[VRIOResult] = Field(default_factory=list, description="VRIO assessment for each resource/capability")
    sustained_advantages: list[str] = Field(default_factory=list, description="Resources providing sustained competitive advantage")
    temporary_advantages: list[str] = Field(default_factory=list, description="Resources providing temporary competitive advantage")
    competitive_parity: list[str] = Field(default_factory=list, description="Resources at competitive parity")
    competitive_disadvantage: list[str] = Field(default_factory=list, description="Resources at competitive disadvantage")


class CoreCompetence(BaseModel):
    """Core competence analysis (§4.4, Agent 14).

    Identifies the 2-3 core competencies that give the company its competitive
    advantage. Assesses whether these competencies are defensible and
    transferable.
    """

    competencies: list[str] = Field(default_factory=list, description="2-3 core competencies identified")
    competency_descriptions: list[str] = Field(default_factory=list, description="Description of each competency")
    is_defensible: bool = Field(default=False, description="Are these competencies defensible against competitors?")
    is_transferable: bool = Field(default=False, description="Are these competencies transferable to new markets/products?")
    defensibility_assessment: str = Field(default="", description="How defensible are these competencies?")
    transferability_assessment: str = Field(default="", description="How transferable are these competencies?")


class StrategicOption(BaseModel):
    """A strategic option in the strategic option grid (§4.4, Agent 14).

    Each option is scored on: feasibility, impact, risk, time to value, and
    resource requirements.
    """

    option_name: str = Field(description="Strategic option name")
    description: str = Field(default="", description="What this option entails")
    feasibility: str = Field(default="", description="Feasibility score: high, medium, low")
    impact: str = Field(default="", description="Impact score: high, medium, low")
    risk: str = Field(default="", description="Risk level: high, medium, low")
    time_to_value: str = Field(default="", description="Time to value: 0-6mo, 6-12mo, 1-2yr, 2-5yr")
    resource_requirements: str = Field(default="", description="Resource requirements: high, medium, low")
    overall_score: str = Field(default="", description="Overall score or ranking")
    recommendation: str = Field(default="", description="Recommendation: pursue, explore, reject")


class StrategicOptionGrid(BaseModel):
    """Strategic option grid (§4.4, Agent 14).

    Builds a grid of 3-5 strategic options, each scored on feasibility, impact,
    risk, time to value, and resource requirements.
    """

    options: list[StrategicOption] = Field(default_factory=list, description="3-5 strategic options")
    recommended_option: str = Field(default="", description="The recommended option")
    rationale: str = Field(default="", description="Why this option is recommended")


class GameTheoryAnalysis(BaseModel):
    """Game theory analysis of competitive dynamics (§4.4, Agent 14).

    Analyzes competitive interactions using game theory (prisoner's dilemma,
    sequential games, signaling). Identifies dominant strategies and Nash
    equilibria. Not just 'competition is intense' — 'This is a prisoner's
    dilemma: if both competitors cut prices, both lose. The Nash equilibrium
    is to maintain prices, but the temptation to defect is high.'
    """

    game_type: str = Field(default="", description="Game type: prisoner's_dilemma, sequential, signaling, chicken, stag_hunt")
    players: list[str] = Field(default_factory=list, description="Key players in the game")
    strategies: list[str] = Field(default_factory=list, description="Available strategies for each player")
    payoff_matrix: list[dict[str, str]] = Field(default_factory=list, description="Payoff matrix for the game")
    dominant_strategy: str = Field(default="", description="Dominant strategy if one exists")
    nash_equilibrium: str = Field(default="", description="Nash equilibrium description")
    implications: str = Field(default="", description="Strategic implications for the company")


class StrategyAnalysis(BaseModel):
    """Output from the Strategy Analyst (Agent 14).

    It doesn't apply every framework to every question. It selects the right
    framework for the specific question — Porter's for industry attractiveness,
    VRIO for resource-based strategy, Blue Ocean for market creation, game
    theory for competitive dynamics. A generic strategist applies SWOT to
    everything. The HYPERION Strategy Analyst applies the framework that
    actually illuminates the specific question, and explicitly says why it
    chose that framework over the alternatives. (§4.4, Agent 14)
    """

    frameworks_selected: list[str] = Field(default_factory=list, description="Frameworks selected for this question and why")
    frameworks_not_selected: list[str] = Field(default_factory=list, description="Frameworks not selected and why not")
    porter_five_forces: PorterFiveForces | None = Field(default=None, description="Porter's Five Forces analysis")
    bcg_matrix: BCGMatrix | None = Field(default=None, description="BCG growth-share matrix")
    swot_tows: SWOTTOWS | None = Field(default=None, description="SWOT with TOWS matrix conversion")
    blue_ocean: BlueOceanStrategy | None = Field(default=None, description="Blue Ocean strategy analysis")
    vrio_assessment: VRIOAssessment | None = Field(default=None, description="VRIO framework assessment")
    core_competence: CoreCompetence | None = Field(default=None, description="Core competence analysis")
    strategic_option_grid: StrategicOptionGrid | None = Field(default=None, description="Strategic option grid with 3-5 scored options")
    game_theory: GameTheoryAnalysis | None = Field(default=None, description="Game theory analysis of competitive dynamics")
    recommended_strategy: str = Field(default="", description="The recommended strategy based on the analysis")
    confidence: ConfidenceLevel
    sources: list[Source] = Field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Source Collection — the Research Librarian's output (§4.5, Agent 15)
# ─────────────────────────────────────────────────────────────────────────────


class PriorResearchLink(BaseModel):
    """A link to prior research from a previous engagement (§4.5, Agent 15).

    When a new engagement touches a topic researched in a prior engagement,
    the Research Librarian links the prior research for the Synthesis Lead
    to reference. This makes the system smarter over time.
    """

    engagement_id: str = Field(description="Prior engagement ID")
    topic: str = Field(description="Topic that was researched")
    note_path: str = Field(description="Path to the vault note")
    relevance_score: float = Field(default=0.0, description="Relevance score (0-1) from keyword matching")
    summary: str = Field(default="", description="Summary of the prior research")
    agents_used: list[str] = Field(default_factory=list, description="Which agents were used in the prior engagement")


class SourceCollection(BaseModel):
    """Output from the Research Librarian (Agent 15).

    Contains deduplicated sources with credibility scores, and prior research
    links. The Research Librarian manages the Obsidian vault (Second Brain),
    retrieves prior research, organizes sources, and links findings across
    engagements. (§4.5, Agent 15)

    It runs on MICRO tier because it doesn't need strong reasoning — it needs
    fast, high-throughput keyword matching. It makes the system smarter over
    time by accumulating knowledge in the vault.
    """

    sources: list[Source] = Field(default_factory=list, description="Deduplicated sources with credibility scores")
    total_sources_before_dedup: int = Field(default=0, description="Total sources before deduplication")
    total_sources_after_dedup: int = Field(default=0, description="Total unique sources after deduplication")
    duplicates_removed: int = Field(default=0, description="Number of duplicate sources removed")
    low_credibility_sources: list[Source] = Field(default_factory=list, description="Sources flagged as low credibility (blog, social media)")
    prior_research_links: list[PriorResearchLink] = Field(default_factory=list, description="Links to prior engagement research from the vault")
    citations_formatted: list[str] = Field(default_factory=list, description="Footnote-style formatted citations for the final report")
    vault_note_saved: bool = Field(default=False, description="Whether engagement findings were saved to vault")
    vault_note_path: str = Field(default="", description="Path to the saved vault note")
    confidence: ConfidenceLevel = Field(default=ConfidenceLevel.MEDIUM)
    sources_by_credibility: dict[str, int] = Field(default_factory=dict, description="Count of sources per credibility tier")


# ─────────────────────────────────────────────────────────────────────────────
# Fact Check Report — the Fact Checker's output (§4.5, Agent 16)
# ─────────────────────────────────────────────────────────────────────────────


class ClaimStatus(str, Enum):
    """Verification status for a factual claim (§4.5, Agent 16)."""

    VERIFIED = "verified"          # 2+ independent sources agree
    PLAUSIBLE = "plausible"        # 1 source supports, no contradiction
    UNVERIFIED = "unverified"      # No independent source found
    CONTRADICTED = "contradicted"  # Sources disagree


class ClaimType(str, Enum):
    """Type of factual claim for targeted verification (§4.5, Agent 16)."""

    NUMBER = "number"        # Statistical figure, market size, revenue
    DATE = "date"            # Event date, founding date, announcement date
    NAME = "name"            # Person, company, product name
    EVENT = "event"          # Acquisition, launch, bankruptcy, regulatory action
    RELATIONSHIP = "relationship"  # Market position, competitive relationship
    QUOTE = "quote"          # Direct quotation attributed to someone


class Claim(BaseModel):
    """A single factual claim extracted from specialist findings.

    The Fact Checker extracts claims from specialist findings and verifies
    each against independent sources. A claim is VERIFIED if 2+ independent
    sources agree. The evidence chain (claim → source → original data) is
    validated — if the chain breaks (source doesn't contain the data, or
    data doesn't support the claim), it's flagged.
    """

    id: str = Field(description="Claim identifier")
    agent: str = Field(description="Which agent made the claim")
    claim: str = Field(description="The factual claim text")
    claim_type: ClaimType = Field(default=ClaimType.NUMBER, description="Type of claim for targeted verification")
    status: ClaimStatus = Field(description="Verification status")
    verification_sources: list[Source] = Field(default_factory=list, description="Sources used to verify")
    contradiction_with: str | None = Field(default=None, description="ID of contradicting claim if any")
    evidence_chain_valid: bool = Field(default=True, description="Whether claim → source → original data chain is intact")
    evidence_chain_break: str | None = Field(default=None, description="Where the chain breaks if invalid")
    credibility_weighted_score: float = Field(default=0.0, description="Verification score weighted by source credibility (0-1)")
    is_hallucinated_citation: bool = Field(default=False, description="True if the cited source doesn't exist or doesn't contain the data")
    verification_notes: str = Field(default="", description="Notes on the verification process")


class FactCheckReport(BaseModel):
    """Output from the Fact Checker (Agent 16).

    Doesn't just check if a source exists — checks if the source actually
    contains the data the specialist claims it does. Catches hallucinated
    citations, which is the #1 quality risk in LLM-generated reports.
    (§4.5, Agent 16)
    """

    claims: list[Claim] = Field(description="All extracted claims with verification status")
    verified_count: int = Field(default=0, description="Number of VERIFIED claims")
    plausible_count: int = Field(default=0, description="Number of PLAUSIBLE claims")
    unverified_count: int = Field(default=0, description="Number of UNVERIFIED claims")
    contradicted_count: int = Field(default=0, description="Number of CONTRADICTED claims")
    contradictions: list[Contradiction] = Field(default_factory=list, description="Inter-agent contradictions")
    hallucinated_citations: list[Claim] = Field(default_factory=list, description="Claims with fake sources")
    hallucinated_citation_count: int = Field(default=0, description="Total hallucinated citations found")
    statistical_red_flags: list[str] = Field(default_factory=list, description="Statistical sanity check issues: too round numbers, implausible growth rates, market sizes that don't reconcile")
    evidence_chain_breaks: list[Claim] = Field(default_factory=list, description="Claims where the evidence chain (claim → source → original data) is broken")
    evidence_chain_break_count: int = Field(default=0, description="Total evidence chain breaks")
    total_claims_checked: int = Field(default=0, description="Total claims extracted and checked")
    verification_rate: float = Field(default=0.0, description="Percentage of claims verified (verified + plausible / total)")
    confidence: ConfidenceLevel = Field(default=ConfidenceLevel.MEDIUM, description="Confidence in the fact-check process")


# ─────────────────────────────────────────────────────────────────────────────
# Visualization Output — the Data Visualizer's output (§4.5, Agent 17)
# ─────────────────────────────────────────────────────────────────────────────


class ChartType(str, Enum):
    """Chart types supported by the Data Visualizer (§4.5, Agent 17).

    Selection is based on data shape:
    comparison → bar, trend → line, distribution → histogram,
    correlation → scatter, composition → stacked bar/treemap, flow → sankey.
    """

    BAR = "bar"
    LINE = "line"
    SCATTER = "scatter"
    HISTOGRAM = "histogram"
    HEATMAP = "heatmap"
    RADAR = "radar"
    WATERFALL = "waterfall"
    TREEMAP = "treemap"
    SANKEY = "sankey"
    STACKED_BAR = "stacked_bar"
    PIE = "pie"  # Discouraged — only used when composition has ≤4 parts


class ChartDataSeries(BaseModel):
    """A single data series for a chart."""

    name: str = Field(description="Series name (e.g., 'Revenue', 'Market Share')")
    values: list[float | str] = Field(default_factory=list, description="Data values")
    labels: list[str] = Field(default_factory=list, description="Category labels for the values")
    color: str | None = Field(default=None, description="Hex color override (otherwise uses CHART_COLORS sequence)")


class ChartAnnotation(BaseModel):
    """A contextual annotation on a chart (§4.5, Agent 17, Skill 5).

    Annotations help the reader understand the key insight:
    benchmark lines, callout boxes, trend lines.
    """

    annotation_type: str = Field(description="Type: 'benchmark_line', 'callout', 'trend_line', 'shaded_region'")
    text: str = Field(description="Annotation text")
    x: float | None = Field(default=None, description="X position (if applicable)")
    y: float | None = Field(default=None, description="Y position (if applicable)")
    x0: float | None = Field(default=None, description="X start for shaded regions")
    x1: float | None = Field(default=None, description="X end for shaded regions")
    y0: float | None = Field(default=None, description="Y start for shaded regions")
    y1: float | None = Field(default=None, description="Y end for shaded regions")


class ChartSpecification(BaseModel):
    """Specification for a single chart (§4.5, Agent 17).

    Received from the Presentation Designer, the Data Visualizer selects
    the chart type, generates the chart with Plotly using brand colors,
    exports at scale=3 for 300 DPI, and post-processes with Pillow.
    """

    id: str = Field(description="Chart identifier (e.g., 'chart_market_size_001')")
    title: str = Field(description="Chart title")
    section: str = Field(default="", description="Which report section this chart belongs to")
    chart_type: ChartType = Field(description="Selected chart type based on data shape")
    data_series: list[ChartDataSeries] = Field(default_factory=list, description="Data series to plot")
    x_axis_label: str = Field(default="", description="X-axis label")
    y_axis_label: str = Field(default="", description="Y-axis label")
    x_axis_range: tuple[float, float] | None = Field(default=None, description="X-axis range (for honest axis calibration)")
    y_axis_range: tuple[float, float] | None = Field(default=None, description="Y-axis range (no truncated axes)")
    annotations: list[ChartAnnotation] = Field(default_factory=list, description="Contextual annotations")
    source_citation: str = Field(default="", description="Data source citation for the chart")
    caption: str = Field(default="", description="Chart caption (below chart)")
    image_path: str = Field(default="", description="Path to the generated PNG (300 DPI)")
    thumbnail_path: str = Field(default="", description="Path to a thumbnail version for TUI display")
    dpi: int = Field(default=300, description="Export DPI (always 300)")
    width_px: int = Field(default=1200, description="Chart width in pixels")
    height_px: int = Field(default=800, description="Chart height in pixels")
    tufte_compliant: bool = Field(default=True, description="Whether chart follows Tufte principles (no chartjunk, no 3D, no gradients)")
    unsplash_image_path: str = Field(default="", description="Path to complementary Unsplash image (if requested)")
    unsplash_caption: str = Field(default="", description="Caption for the Unsplash image")


class VisualizationOutput(BaseModel):
    """Output from the Data Visualizer (Agent 17).

    Contains all chart specifications with generated image paths and
    metadata. Charts are 300 DPI PNGs with brand-compliant styling,
    Tufte principles applied, and Pillow post-processing for print quality.
    (§4.5, Agent 17)

    It follows Tufte principles — no chartjunk, no 3D effects, no gradient
    fills. Every chart has a purpose: it reveals a pattern that the text
    alone cannot convey. It never uses a pie chart when a bar chart would
    be clearer. It always labels axes, always cites the data source, and
    always chooses the chart type that best reveals the insight.
    """

    charts: list[ChartSpecification] = Field(default_factory=list, description="All generated charts with image paths")
    total_charts: int = Field(default=0, description="Total charts generated")
    total_images: int = Field(default=0, description="Total Unsplash images sourced")
    chart_types_used: list[str] = Field(default_factory=list, description="Chart types used in this engagement")
    all_300_dpi: bool = Field(default=True, description="Whether all charts are 300 DPI")
    all_brand_compliant: bool = Field(default=True, description="Whether all charts use brand color sequence")
    all_tufte_compliant: bool = Field(default=True, description="Whether all charts follow Tufte principles")
    confidence: ConfidenceLevel = Field(default=ConfidenceLevel.MEDIUM)


# ─────────────────────────────────────────────────────────────────────────────
# Quality Score — the Quality Gate's output (§4.5, Agent 18)
# ─────────────────────────────────────────────────────────────────────────────


class QualityDimensionName(str, Enum):
    """The 10 quality dimensions scored by the Quality Gate (§4.5, Agent 18).

    Each dimension is scored 1-5. The weighted total must be ≥ 4.0 for
    approval. If any dimension scores below 3, the report goes back for
    iteration regardless of the total.
    """

    COMPLETENESS = "completeness"
    EVIDENCE_SUFFICIENCY = "evidence_sufficiency"
    ANALYTICAL_DEPTH = "analytical_depth"
    LOGICAL_CONSISTENCY = "logical_consistency"
    CONTRADICTION_RESOLUTION = "contradiction_resolution"
    TONE_AND_VOICE = "tone_and_voice"
    STRUCTURAL_QUALITY = "structural_quality"
    RISK_COVERAGE = "risk_coverage"
    DATA_ACCURACY = "data_accuracy"
    VISUAL_QUALITY = "visual_quality"


# Dimension weights for weighted total score (§4.5, Agent 18)
# Higher weight = more critical to report quality
DIMENSION_WEIGHTS: dict[QualityDimensionName, float] = {
    QualityDimensionName.COMPLETENESS: 0.15,
    QualityDimensionName.EVIDENCE_SUFFICIENCY: 0.15,
    QualityDimensionName.ANALYTICAL_DEPTH: 0.15,
    QualityDimensionName.LOGICAL_CONSISTENCY: 0.10,
    QualityDimensionName.CONTRADICTION_RESOLUTION: 0.10,
    QualityDimensionName.TONE_AND_VOICE: 0.05,
    QualityDimensionName.STRUCTURAL_QUALITY: 0.05,
    QualityDimensionName.RISK_COVERAGE: 0.10,
    QualityDimensionName.DATA_ACCURACY: 0.10,
    QualityDimensionName.VISUAL_QUALITY: 0.05,
}


class QualityDimension(BaseModel):
    """A single dimension of the 10-dimension quality rubric (§4.5, Agent 18).

    Doesn't just say "good" or "bad." Produces specific, actionable feedback.
    "Dimension 3 (analytical depth) scored 2/5: the Market Analysis section
    presents data but doesn't interpret it. Fix: add 'so what?' implications
    to each finding."
    """

    dimension_id: QualityDimensionName = Field(description="Which of the 10 dimensions")
    name: str = Field(description="Human-readable dimension name")
    score: int = Field(ge=1, le=5, description="Score 1-5")
    weight: float = Field(description="Weight in total score (from DIMENSION_WEIGHTS)")
    feedback: str = Field(description="Specific, actionable feedback — not just 'good' or 'bad'")
    fix_instructions: str | None = Field(default=None, description="What to fix if score < 4")
    critical: bool = Field(default=False, description="True if score < 3 — forces iteration regardless of total")


class QualityScore(BaseModel):
    """Output from the Quality Gate (Agent 18).

    Doesn't just say "good" or "bad." Produces a specific, actionable score
    report that tells the Synthesis Lead exactly what to fix. "Dimension 3
    (analytical depth) scored 2/5: the Market Analysis section presents data
    but doesn't interpret it. Fix: add 'so what?' implications to each finding.
    Dimension 6 (tone) scored 3/5: 4 instances of hedgy language in the executive
    summary. Fix: replace 'might possibly' with 'is likely to'."
    (§4.5, Agent 18)
    """

    dimensions: list[QualityDimension] = Field(description="All 10 dimension scores with feedback")
    total_score: float = Field(ge=0, le=5, description="Weighted total score across all dimensions")
    threshold: float = Field(default=4.0, description="Approval threshold (default 4.0/5.0)")
    approved: bool = Field(description="True if total_score >= threshold AND no critical dimensions")
    iteration: int = Field(ge=1, description="Which iteration this is (max 3)")
    gaps: list[str] = Field(default_factory=list, description="Specific gaps identified — questions unanswered, data missing")
    critical_dimensions: list[QualityDimensionName] = Field(default_factory=list, description="Dimensions scoring < 3 — forces iteration")
    max_iterations_reached: bool = Field(default=False, description="True if 3 iterations done without pass")
    escalation_report: str | None = Field(default=None, description="Detailed escalation report if max iterations reached without pass")
    fix_priority: list[str] = Field(default_factory=list, description="Ordered list of fixes to apply, highest impact first")


# ─────────────────────────────────────────────────────────────────────────────
# Final Report — THE single most important data structure (§4.3, Agent 2)
# ─────────────────────────────────────────────────────────────────────────────


class Recommendation(str, Enum):
    """The final recommendation type."""

    ENTER = "enter"            # Go ahead with the initiative
    NO_GO = "no_go"            # Do not proceed
    CONDITIONAL = "conditional"  # Proceed only if certain conditions are met
    INVESTIGATE = "investigate"  # Need more research before deciding
    ACQUIRE = "acquire"        # Proceed with acquisition
    DO_NOT_ACQUIRE = "do_not_acquire"  # Do not acquire
    HOLD = "hold"              # Maintain current position


class FinalReport(BaseModel):
    """The FinalReport — the single most important data structure in HYPERION.

    Produced by the Synthesis Lead after reconciling all specialist findings.
    This is not a summary — it is a synthesis. A summarizer lists what each
    agent found. A synthesizer says "Market says $2B TAM, Financial says too
    small, but Financial's model assumes 5% penetration while Market's data
    supports 12% — at 12% penetration the market is viable." (§4.3, Agent 2)

    This structure drives the PDF generation pipeline:
    - Presentation Designer uses it to design the layout
    - Data Visualizer uses it to generate charts
    - Render Engine uses it to assemble the final PDF
    - Quality Gate uses it to score the report
    """

    engagement_id: str = Field(description="Unique engagement identifier")
    question: str = Field(description="The original business question")
    recommendation: Recommendation = Field(description="The final recommendation")
    recommendation_rationale: str = Field(description="Why — the evidence chain supporting the recommendation")
    critical_assumptions: list[str] = Field(description="Assumptions that would flip the recommendation if wrong")
    confidence: ConfidenceLevel = Field(description="System-level confidence")
    confidence_breakdown: dict[str, ConfidenceLevel] = Field(
        description="Per-domain confidence (market=HIGH, regulatory=LOW, etc.)"
    )

    # Executive summary (§6.1) — the page the CEO reads, must stand alone
    executive_summary: str = Field(description="The recommendation + key findings + critical risks, standalone")
    key_findings: list[KeyFinding] = Field(default_factory=list, description="3-5 key findings for exec summary")

    # Analysis sections (§6.1) — each 3-8 pages, self-contained
    sections: list[AnalysisSection] = Field(default_factory=list, description="Specialist analysis sections")

    # Risk analysis (§6.1) — 2-3 pages
    risk_analysis: RiskAnalysis | None = Field(default=None, description="Risk section")

    # Reconciliation artifacts
    contradictions: list[Contradiction] = Field(default_factory=list, description="All contradictions and resolutions")

    # Quality and fact-check
    quality_score: QualityScore | None = Field(default=None, description="Quality Gate score")
    fact_check_report: FactCheckReport | None = Field(default=None, description="Fact Checker report")

    # Metadata for methodology page (§6.1)
    agents_used: list[str] = Field(default_factory=list, description="Which agents were spawned")
    total_sources: int = Field(default=0, description="Total unique sources cited")
    total_data_points: int = Field(default=0, description="Total data points collected")
    limitations: list[str] = Field(default_factory=list, description="What we couldn't research")
    generated_at: datetime = Field(default_factory=datetime.now)


# ─────────────────────────────────────────────────────────────────────────────
# Layout Plan — the Presentation Designer's output (§4.6, Agent 19)
# ─────────────────────────────────────────────────────────────────────────────


class PageType(str, Enum):
    """Page types in the premium report structure (§6.1)."""

    COVER = "cover"
    TABLE_OF_CONTENTS = "table_of_contents"
    EXECUTIVE_SUMMARY = "executive_summary"
    SECTION = "section"
    RISK_ANALYSIS = "risk_analysis"
    METHODOLOGY = "methodology"
    APPENDIX = "appendix"
    BACK_COVER = "back_cover"


class ImageSelection(BaseModel):
    """An Unsplash image selected for the report (§4.6, Agent 19).

    The Presentation Designer specifies exact search terms per section.
    "Modern boardroom meeting" not "business." "Mumbai skyline at dusk"
    not "city." The image must add meaning, not just fill space.
    """

    id: str = Field(description="Image identifier (e.g., 'img_cover_001')")
    page_type: PageType = Field(description="Which page type this image is for")
    section_id: str = Field(default="", description="Which section (if applicable)")
    search_term: str = Field(description="Specific Unsplash search term used")
    image_path: str = Field(default="", description="Path to the downloaded/processed image")
    photographer: str = Field(default="", description="Photographer name for attribution")
    unsplash_id: str = Field(default="", description="Unsplash photo ID")
    caption: str = Field(default="", description="Caption with source attribution")
    placement: str = Field(default="right", description="Placement: 'full_bleed' (cover), 'right' (section), 'inline'")
    width_percent: int = Field(default=40, description="Width as percentage of page (40% for sections, 100% for cover)")
    page_number: int = Field(default=0, description="Which page this image appears on")


class ChartPlacement(BaseModel):
    """A chart placement in the layout plan (§4.6, Agent 19).

    Charts are placed adjacent to their context text on the SAME page.
    No orphaned images. No blank pages.
    """

    chart_id: str = Field(description="Chart identifier from Data Visualizer")
    section_id: str = Field(default="", description="Which section this chart belongs to")
    page_number: int = Field(default=0, description="Which page this chart appears on")
    image_path: str = Field(default="", description="Path to the chart PNG (300 DPI)")
    caption: str = Field(default="", description="Chart caption")
    source_citation: str = Field(default="", description="Data source citation")
    width_percent: int = Field(default=80, description="Width as percentage of page")
    placement: str = Field(default="center", description="Placement on page: 'center', 'left', 'right'")


class PageLayout(BaseModel):
    """A single page in the layout plan (§4.6, Agent 19).

    Each page has a clear visual hierarchy:
    header → key insight → body → chart/image → implication.
    """

    page_number: int = Field(description="Page number (1-indexed)")
    page_type: PageType = Field(description="What type of page this is")
    section_id: str = Field(default="", description="Which section (if applicable)")
    title: str = Field(default="", description="Page title (Instrument Serif)")
    content_blocks: list[str] = Field(default_factory=list, description="Ordered content blocks on this page")
    images: list[ImageSelection] = Field(default_factory=list, description="Images on this page")
    charts: list[ChartPlacement] = Field(default_factory=list, description="Charts on this page")
    has_key_insight_box: bool = Field(default=False, description="Whether this page has a key insight box")
    has_implication_box: bool = Field(default=False, description="Whether this page has a 'so what?' implication box")
    is_full_bleed: bool = Field(default=False, description="True for cover page (full-bleed image)")
    page_break_before: bool = Field(default=False, description="Whether to force a page break before this page")


class LayoutPlan(BaseModel):
    """Output from the Presentation Designer (Agent 19).

    Contains page-by-page layout, image selections, and chart placements.
    The Presentation Designer treats layout as design, not as formatting.
    It makes deliberate decisions about what goes on each page, how to
    balance text and visuals, and how to guide the reader through the
    narrative. It always ensures images are adjacent to their context text.
    It never produces a blank page or an orphaned image.
    (§4.6, Agent 19)

    The final PDF is produced by the Render Engine using this layout plan.
    """

    engagement_id: str = Field(description="Engagement identifier")
    pages: list[PageLayout] = Field(default_factory=list, description="Page-by-page layout")
    total_pages: int = Field(default=0, description="Total page count (15-40 for standard engagement)")
    cover_image: ImageSelection | None = Field(default=None, description="Cover page image")
    section_images: list[ImageSelection] = Field(default_factory=list, description="All section header images")
    chart_placements: list[ChartPlacement] = Field(default_factory=list, description="All chart placements")
    html_template_path: str = Field(default="", description="Path to the Jinja2-rendered HTML")
    css_path: str = Field(default="", description="Path to the CSS file (brand colors, typography)")
    pdf_path: str = Field(default="", description="Path to the generated PDF (empty until Render Engine runs)")
    typography: dict[str, str] = Field(
        default_factory=lambda: {
            "header_font": "Instrument Serif",
            "body_font": "Source Sans 3",  # D24: professional sans
            "cover_title_size": "36pt",
            "section_header_size": "22pt",
            "subsection_header_size": "14pt",
            "body_size": "10pt",
            "caption_size": "8pt",
            "key_insight_size": "11pt",
            "data_table_size": "9pt",
        },
        description="Typography system (§7.4)",
    )
    color_palette: dict[str, str] = Field(
        default_factory=lambda: {
            "warm_charcoal": "#1A1A1A",
            "cream": "#F5F4EE",
            "terracotta": "#C8704D",
            "sage": "#7C9885",
            "beige": "#E8E6DD",
            "warm_gray": "#8B8680",
            "deep_brown": "#3D3530",
            "alert_red": "#B5533C",
        },
        description="PDF color palette (§7.2)",
    )
    no_blank_pages: bool = Field(default=True, description="Whether the layout has no blank pages")
    no_orphaned_images: bool = Field(default=True, description="Whether all images have adjacent text context")
    all_images_300_dpi: bool = Field(default=True, description="Whether all images are 300 DPI")
    confidence: ConfidenceLevel = Field(default=ConfidenceLevel.MEDIUM)


# ─────────────────────────────────────────────────────────────────────────────
# Render Output — the Render Engine's output (§4.6, Agent 20)
# ─────────────────────────────────────────────────────────────────────────────


class RenderOutput(BaseModel):
    """Output from the Render Engine (Agent 20).

    The final PDF file path with verification results. The Render Engine
    is the last line of defense for quality — it verifies the PDF after
    rendering: no blank pages, no orphaned images, all fonts embedded.
    If any check fails, it reports the issue back to the Presentation
    Designer for correction. It never ships a broken PDF.
    (§4.6, Agent 20)
    """

    pdf_path: str = Field(description="Path to the final 300 DPI PDF file")
    page_count: int = Field(default=0, description="Total pages in the PDF")
    file_size_mb: float = Field(default=0.0, description="File size in MB")
    dpi: int = Field(default=300, description="PDF DPI (always 300)")
    images_processed: int = Field(default=0, description="Number of images processed through Pillow pipeline")
    charts_processed: int = Field(default=0, description="Number of charts processed through Pillow pipeline")
    fonts_embedded: list[str] = Field(default_factory=list, description="Fonts embedded in the PDF")
    no_blank_pages: bool = Field(default=True, description="Verified: no blank pages in PDF")
    no_orphaned_images: bool = Field(default=True, description="Verified: no orphaned images in PDF")
    all_fonts_embedded: bool = Field(default=True, description="Verified: all fonts embedded")
    all_images_300_dpi: bool = Field(default=True, description="Verified: all images are 300 DPI")
    verification_passed: bool = Field(default=True, description="True if all verification checks passed")
    verification_issues: list[str] = Field(default_factory=list, description="Issues found during verification (empty if all passed)")
    rendered_at: datetime = Field(default_factory=datetime.now)
