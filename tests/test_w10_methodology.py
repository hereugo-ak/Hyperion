"""W-10: the methodology section describes METHOD, not staffing.

Anchors verified against HEAD before these tests were written:

- ``hyperion/agents/delivery/presentation_designer.py`` methodology template
  block. NOTE THE DISCREPANCY: the audit anchors this at :1448-1477 emitting
  "Agents Used, Sources Accessed count, Data Points count, Limitations". W-09
  (commit fb445f9) had already deleted the roster row, so at the time W-10 was
  executed the block sat at :1433-1459 and emitted only an "Evidence base"
  count pair plus "Limitations" — and its own comment said the six-subsection
  methodology was W-10's job. The remediation is the same either way: the
  counts are replaced by six subsections.
- ``hyperion/schemas/models.py`` ``FinalReport`` — had no ``methodology`` field.
- ``hyperion/schemas/narrative.py`` ``ClientReport`` / ``from_report`` — had no
  ``methodology`` field.
- ``hyperion/schemas/workflow.py:69`` ``RosterDecision`` (W-06 input).
- ``hyperion/agents/insufficiency.py:148`` ``InsufficiencyResolution`` (W-07).
- ``hyperion/agents/engagement_director.py:277`` ``AGENT_METHODS`` — the
  method/subject-class eligibility table subsection 2 is derived from.

The acceptance criteria under test, one test each:

1. all six subsections present, in order
2. zero agent names in the methodology section
3. every excluded method has a stated reason
4. retrieval coverage cites distinct query count AND distinct domain count
5. verification is stated as a rate, not as a warning
"""

from __future__ import annotations

import re

import pytest

from hyperion.agents.engagement_director import AGENT_METHODS, eligible_methods
from hyperion.agents.insufficiency import (
    EngineSet,
    InsufficiencyOutcome,
    InsufficiencyResolution,
    QueryForm,
    StrategyTriple,
    TimeWindow,
)
from hyperion.config import ModelTier
from hyperion.output.methodology import (
    CorpusStats,
    RetrievalStats,
    build_methodology,
    collect_sources,
)
from hyperion.schemas.agents import AgentName
from hyperion.schemas.methodology import (
    REQUIRED_SUBSECTION_KEYS,
    MethodologyRecord,
    MethodologySubsection,
)
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    FactCheckReport,
    FinalReport,
    KeyFinding,
    Recommendation,
    Source,
    SourceCredibility,
)
from hyperion.schemas.narrative import ClientProse, ClientReport
from hyperion.schemas.workflow import (
    QuestionType,
    RosterDecision,
    SubjectClass,
    TaskNode,
    WorkflowDAG,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: a nation-state engagement, which is the case the audit calls out
# ("the report will state that firm-level valuation was excluded because the
# subject is a nation state").
# ─────────────────────────────────────────────────────────────────────────────


def _source(sid: str, host: str, year: str, cred: SourceCredibility) -> Source:
    return Source(
        id=sid,
        title=f"Official release {sid}",
        url=f"https://{host}/reports/{sid}",
        credibility=cred,
        publication_date=year,
        key_data=f"Figure from {sid}",
    )


@pytest.fixture
def sources() -> list[Source]:
    return [
        _source("src_001", "commerce.gov.in", "2024-03-01", SourceCredibility.GOVERNMENT),
        _source("src_002", "www.rbi.org.in", "2023-11-12", SourceCredibility.GOVERNMENT),
        _source("src_003", "nature.com", "2022-06-01", SourceCredibility.PEER_REVIEWED),
        _source("src_004", "mckinsey.com", "2025-01-09", SourceCredibility.INDUSTRY_REPORT),
        _source("src_005", "reuters.com", "2025-02-02", SourceCredibility.NEWS),
        _source("src_006", "someblog.example", "2021-01-01", SourceCredibility.BLOG),
    ]


@pytest.fixture
def report(sources: list[Source]) -> FinalReport:
    finding = KeyFinding(
        id="f1",
        agent="market_analyst",
        finding_type="market_size",
        title="Import dependence concentrated in two categories",
        content=(
            "Import volumes fell 14 percent year on year while domestic "
            "capacity additions covered only a third of the shortfall."
        ),
        sources=sources[:3],
        confidence=ConfidenceLevel.HIGH,
    )
    section = AnalysisSection(
        id="market_analysis",
        title="Trade exposure and domestic capacity",
        agent="market_analyst",
        key_insight="Domestic capacity covers a third of the shortfall.",
        body="Body text that is long enough to be a real section body. " * 20,
        findings=[finding],
        sources=sources,
        confidence=ConfidenceLevel.HIGH,
    )
    return FinalReport(
        engagement_id="eng_w10",
        question="Should India reduce its dependence on these imports?",
        recommendation=Recommendation.CONDITIONAL,
        recommendation_rationale="Capacity additions are necessary but not sufficient.",
        critical_assumptions=["Tariff schedule holds through the period."],
        confidence=ConfidenceLevel.MEDIUM,
        confidence_breakdown={},
        executive_summary="Domestic capacity covers a third of the shortfall.",
        key_findings=[finding],
        sections=[section],
        total_sources=len(sources),
        total_data_points=42,
        limitations=["Sub national data was not available for two states."],
        fact_check_report=FactCheckReport(
            claims=[],
            verified_count=31,
            plausible_count=7,
            unverified_count=2,
            contradicted_count=0,
            total_claims_checked=40,
            verification_rate=0.95,
        ),
    )


@pytest.fixture
def dag() -> WorkflowDAG:
    """A NATION_OR_REGION roster: financial_analyst dispatched on its fiscal
    methods, competitive_intel excluded outright, ma_analyst excluded outright.
    """
    subject = SubjectClass.NATION_OR_REGION
    dispatched = (
        AgentName.MARKET_ANALYST,
        AgentName.FINANCIAL_ANALYST,
        AgentName.REGULATORY_ANALYST,
        AgentName.RISK_ANALYST,
    )
    excluded = (AgentName.COMPETITIVE_INTEL, AgentName.MA_ANALYST, AgentName.CONSUMER_INSIGHTS)
    decisions = [
        RosterDecision(
            agent=agent,
            subject_class=subject,
            eligible_methods=eligible_methods(agent, subject),
            dispatched=True,
            reason="At least one declared method applies to the subject class.",
        )
        for agent in dispatched
    ] + [
        RosterDecision(
            agent=agent,
            subject_class=subject,
            eligible_methods=[],
            dispatched=False,
            reason="No declared method applies to the subject class.",
        )
        for agent in excluded
    ]
    return WorkflowDAG(
        engagement_id="eng_w10",
        question="Should India reduce its dependence on these imports?",
        question_type=QuestionType.GO_NO_GO,
        tasks=[
            TaskNode(
                id=f"t_{agent.value}",
                agent=agent,
                model_tier=ModelTier.DEEP,
                description="Do the work.",
            )
            for agent in dispatched
        ],
        subject="import dependence",
        subject_class=subject.value,
        roster_decisions=decisions,
        agents_selected=list(dispatched),
        estimated_total_llm_calls=40,
        estimated_total_tokens=200_000,
        estimated_duration_minutes=25.0,
    )


@pytest.fixture
def resolutions() -> list[InsufficiencyResolution]:
    return [
        InsufficiencyResolution(
            gap_id="gap_1",
            question="What was the state level capacity addition in 2024?",
            section_id="market_analysis",
            outcome=InsufficiencyOutcome.DECLARED_GAP,
            tried_triples=[
                StrategyTriple(
                    query_form=QueryForm.KEYWORD_CONJUNCTION,
                    engine_set=EngineSet.RELIABLE,
                    window=TimeWindow.UNBOUNDED,
                ),
                StrategyTriple(
                    query_form=QueryForm.ENTITY_METRIC,
                    engine_set=EngineSet.RELIABLE,
                    window=TimeWindow.LAST_3_YEARS,
                ),
                StrategyTriple(
                    query_form=QueryForm.SITE_SCOPED,
                    engine_set=EngineSet.CATEGORY_SCIENCE,
                    window=TimeWindow.UNBOUNDED,
                ),
            ],
        ),
        InsufficiencyResolution(
            gap_id="gap_2",
            question="What is the discounted cash flow value of the country?",
            section_id="market_analysis",
            outcome=InsufficiencyOutcome.OUT_OF_SCOPE,
            justification="The unit of analysis is a firm, not a jurisdiction.",
        ),
    ]


@pytest.fixture
def record(report, dag, resolutions) -> MethodologyRecord:
    return build_methodology(
        report, dag=dag, resolutions=resolutions, queries_issued=37
    )


def _all_text(rec: MethodologyRecord) -> str:
    parts: list[str] = []
    for sub in rec.subsections:
        parts.append(sub.heading)
        parts.append(sub.narrative)
        parts.extend(sub.facts)
    return "\n".join(parts)


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance criterion 1: all six subsections present
# ─────────────────────────────────────────────────────────────────────────────


def test_all_six_subsections_present_in_order(record):
    assert tuple(s.key for s in record.subsections) == REQUIRED_SUBSECTION_KEYS
    assert len(record.subsections) == 6


def test_headings_match_the_audit_verification_greps(record):
    """The audit's verification snippet greps the PDF for these strings."""
    text = _all_text(record).lower()
    for required in (
        "question decomposition",
        "scope and method selection",
        "retrieval strategy",
        "inclusion",
        "verification",
        "limitations",
    ):
        assert required in text, required


def test_a_five_subsection_record_is_unconstructible(record):
    with pytest.raises(ValueError, match="exactly the six"):
        MethodologyRecord(subsections=record.subsections[:5])


def test_subsections_out_of_order_are_unconstructible(record):
    shuffled = [record.subsections[1], record.subsections[0]] + list(
        record.subsections[2:]
    )
    with pytest.raises(ValueError, match="in order"):
        MethodologyRecord(subsections=shuffled)


def test_a_seventh_agent_roster_subsection_is_unconstructible():
    """W-10 failure mode 1: keeping 'Agents Used' as a seventh subsection."""
    with pytest.raises(ValueError, match="Unknown methodology subsection key"):
        MethodologySubsection(
            key="agents_used",
            heading="Agents Used",
            narrative="The following specialists ran.",
        )


def test_every_subsection_has_a_narrative_sentence_not_only_counts(record):
    """W-10 failure mode 2: filling the six subsections with counts only."""
    for sub in record.subsections:
        # A narrative, not a number: at least 12 words and a verb-bearing
        # sentence ending in a full stop.
        assert len(sub.narrative.split()) >= 12, sub.key
        assert sub.narrative.rstrip().endswith("."), sub.key


def test_a_subsection_with_no_narrative_is_unconstructible():
    with pytest.raises(ValueError):
        MethodologySubsection(
            key="verification_procedure",
            heading="Verification procedure",
            narrative="",
            facts=["Claims checked: 40."],
        )


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance criterion 2: zero agent names
# ─────────────────────────────────────────────────────────────────────────────


def test_zero_agent_names_anywhere_in_the_methodology(record):
    text = _all_text(record)
    lowered = text.lower()
    for agent in AgentName:
        assert agent.value not in lowered, agent.value
        display = agent.value.replace("_", " ").title()
        assert display not in text, display
        assert display.lower() not in lowered, display


def test_the_audit_named_agents_are_absent(record):
    """The exact strings the audit's verification snippet checks for."""
    text = _all_text(record)
    for agent in ("Financial Analyst", "Fact Checker", "Data Visualizer"):
        assert agent not in text, agent


def test_agent_names_are_structurally_unconstructible_not_merely_absent():
    """Zero agent names is an invariant of the type, not a property of one run."""
    with pytest.raises(ValueError, match="agent name"):
        MethodologySubsection(
            key="scope_and_method_selection",
            heading="Scope and method selection",
            narrative="The financial_analyst applied a valuation method here.",
        )


def test_no_em_or_en_dashes_and_no_dict_reprs(record):
    text = _all_text(record)
    assert "\u2014" not in text
    assert "\u2013" not in text
    assert not re.search(r"\{['\"]", text)


def test_no_subject_class_enum_literal_leaks_as_prose(record):
    """'a nation or region', never 'nation_or_region'.

    Only the compound wire values are checked. 'company', 'market', 'policy'
    and 'technology' are ordinary English words that legitimately appear in a
    methodology sentence ("it is defined for a company"); banning them would be
    the same mistake ClientProse deliberately avoids with 'low' and 'high'.
    The leak shape is the underscored compound.
    """
    text = _all_text(record)
    compounds = [c.value for c in SubjectClass if "_" in c.value]
    assert compounds, "fixture assumption: SubjectClass has compound values"
    for value in compounds:
        assert value not in text, value
    assert "a nation or region" in text


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance criterion 3: every excluded method has a stated reason
# ─────────────────────────────────────────────────────────────────────────────


def test_every_excluded_method_line_states_a_reason(record):
    sub = record.by_key("scope_and_method_selection")
    excluded = [f for f in sub.facts if f.startswith("Excluded: ")]
    assert excluded, "the nation-state roster must record exclusions"
    for line in excluded:
        assert " because " in line, line
        # a reason, not a restatement of the exclusion
        assert len(line.split(" because ", 1)[1].split()) >= 4, line


def test_the_dcf_on_a_country_question_is_answered_permanently(record):
    """The audit's named case: firm-level valuation excluded because the
    subject is a nation state."""
    sub = record.by_key("scope_and_method_selection")
    joined = "\n".join(sub.facts) + "\n" + sub.narrative
    assert "discounted cash flow valuation" in joined
    excluded_dcf = [
        f
        for f in sub.facts
        if f.startswith("Excluded: ") and "discounted cash flow" in f
    ]
    assert excluded_dcf, "DCF must appear as a stated exclusion"
    assert "nation or region" in excluded_dcf[0]


def test_applied_methods_are_listed_and_are_the_eligible_ones(record, dag):
    sub = record.by_key("scope_and_method_selection")
    applied = [f for f in sub.facts if f.startswith("Applied: ")]
    assert applied
    # 'fiscal cost analysis' is what a financial specialist does to a nation;
    # it must be present, and it must be presented as a method.
    joined = "\n".join(applied)
    assert "fiscal cost analysis" in joined


def test_exclusions_are_derived_from_the_real_method_table(dag):
    """Guards against a hand-written exclusion list drifting from AGENT_METHODS."""
    subject = SubjectClass.NATION_OR_REGION
    declared = set(AGENT_METHODS[AgentName.COMPETITIVE_INTEL])
    eligible = set(eligible_methods(AgentName.COMPETITIVE_INTEL, subject))
    assert declared - eligible, (
        "fixture assumption broken: competitive_intel must have methods that "
        "do not apply to a nation or region"
    )


def test_no_subject_class_means_no_invented_exclusions(report):
    """A legacy DAG with no subject class must not fabricate reasons."""
    rec = build_methodology(report, dag=None, resolutions=[], queries_issued=5)
    sub = rec.by_key("scope_and_method_selection")
    assert "not established" in "\n".join(sub.facts)
    assert not [f for f in sub.facts if f.startswith("Excluded: ")]


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance criterion 4: distinct query count AND distinct domain count
# ─────────────────────────────────────────────────────────────────────────────


def test_retrieval_cites_distinct_query_count_and_distinct_domain_count(record):
    sub = record.by_key("retrieval_strategy_and_coverage")
    joined = sub.narrative + "\n" + "\n".join(sub.facts)
    assert "Distinct queries issued: 37." in sub.facts
    assert "37 distinct queries" in sub.narrative
    # 6 sources on 6 distinct hosts, www. stripped
    assert "Distinct source domains: 6." in sub.facts
    assert "6 distinct source domains" in joined


def test_distinct_domains_strip_www_and_deduplicate():
    srcs = [
        _source("a", "www.example.com", "2024", SourceCredibility.NEWS),
        _source("b", "example.com", "2024", SourceCredibility.NEWS),
        _source("c", "other.org", "2024", SourceCredibility.NEWS),
    ]
    stats = CorpusStats(srcs)
    assert stats.domains == {"example.com", "other.org"}
    assert stats.n_domains == 2


def test_retrieval_reports_the_date_range_and_the_escalations(record):
    sub = record.by_key("retrieval_strategy_and_coverage")
    assert "Source date range: 2021 to 2025." in sub.facts
    # gap_1 tried three triples => two escalations
    assert "Retrieval escalations triggered: 2." in sub.facts
    assert "Distinct retrieval strategies attempted: 3." in sub.facts


def test_retrieval_names_pools_not_engines(record):
    sub = record.by_key("retrieval_strategy_and_coverage")
    joined = sub.narrative + "\n".join(sub.facts)
    for engine in ("duckduckgo", "startpage", "searxng", "brave", "mojeek", "qwant"):
        assert engine not in joined.lower(), engine
    assert "index pool" in joined


def test_query_count_is_never_invented_when_unrecorded(report):
    stats = RetrievalStats.from_resolutions([], queries_issued=0)
    assert not stats.queries_issued
    rec = build_methodology(report, dag=None, resolutions=[], queries_issued=0)
    sub = rec.by_key("retrieval_strategy_and_coverage")
    assert "Query count not recorded for this run." in sub.facts
    assert "distinct quer" not in sub.narrative


def test_corpus_stats_are_derived_from_sources_not_from_total_sources(sources):
    """total_sources is a number somebody set; these are computed."""
    stats = CorpusStats(sources)
    assert stats.n_sources == 6
    assert stats.date_range == (2021, 2025)
    assert stats.with_extracted_data == 6


def test_collect_sources_reaches_section_and_finding_level(report):
    collected = collect_sources(report)
    # section.sources (6) + section.findings[0].sources (3) + key_findings (3)
    assert len(collected) == 12
    assert CorpusStats(collected).n_sources == 6  # deduplicated by url


# ─────────────────────────────────────────────────────────────────────────────
# Acceptance criterion 5: verification stated as a rate, not a warning
# ─────────────────────────────────────────────────────────────────────────────


def test_verification_is_stated_as_a_rate(record):
    sub = record.by_key("verification_procedure")
    assert "95 percent" in sub.narrative
    assert "Pass rate: 95 percent." in sub.facts
    assert "Claims extracted and checked: 40." in sub.facts


def test_verification_is_not_an_alarm(record):
    sub = record.by_key("verification_procedure")
    joined = (sub.narrative + "\n".join(sub.facts)).lower()
    for alarm in (
        "unverified",
        "contradicted",
        "hallucinat",
        "red flag",
        "warning",
        "failed",
        "error",
    ):
        assert alarm not in joined, alarm


def test_verification_literals_are_structurally_unconstructible():
    with pytest.raises(ValueError, match="verification state"):
        MethodologySubsection(
            key="verification_procedure",
            heading="Verification procedure",
            narrative="Two claims were left unverified after checking.",
        )


def test_a_percentage_rate_and_a_fraction_rate_both_render_as_percent(report):
    """verification_rate is 0.95 on some paths and 95.0 on others."""
    report.fact_check_report.verification_rate = 95.0
    rec = build_methodology(report, dag=None, resolutions=[])
    assert "95 percent" in rec.by_key("verification_procedure").narrative


def test_no_fact_check_report_states_the_absence_without_alarm(report):
    report.fact_check_report = None
    rec = build_methodology(report, dag=None, resolutions=[])
    sub = rec.by_key("verification_procedure")
    assert "Claims extracted and checked: 0." in sub.facts
    assert "no pass rate is reported" in sub.narrative
    assert "Pass rate" not in "\n".join(sub.facts)


# ─────────────────────────────────────────────────────────────────────────────
# Subsections 1, 4 and 6 substance
# ─────────────────────────────────────────────────────────────────────────────


def test_question_decomposition_reports_answered_gaps_and_out_of_scope(record):
    sub = record.by_key("question_decomposition")
    facts = "\n".join(sub.facts)
    assert "Lines of enquiry planned: 4." in sub.facts
    assert "Closed with sufficient evidence: 1." in sub.facts
    assert "Carried forward as stated evidence gaps: 1." in sub.facts
    assert "Ruled outside scope: 1." in sub.facts
    assert "Answered: Trade exposure and domestic capacity." in facts


def test_gap_identifiers_never_reach_the_page(record):
    text = _all_text(record)
    assert not re.search(r"\bgap_[A-Za-z0-9_]+\b", text, re.IGNORECASE)


def test_inclusion_states_a_rule_then_the_counts(record):
    sub = record.by_key("source_inclusion_and_exclusion")
    assert "General reference works were refused as primary evidence." in sub.facts
    assert "Sources retained after deduplication: 6." in sub.facts
    # 4 accepted tiers present (government x2, peer_reviewed, industry, news)
    assert "Retained in preferred credibility tiers: 5." in sub.facts
    assert "peer reviewed literature" in sub.narrative


def test_design_limitations_are_structural_and_four_in_number(record):
    sub = record.by_key("design_limitations")
    lowered = sub.narrative.lower()
    assert "no primary research" in lowered
    assert "paywall" in lowered
    assert "english" in lowered
    assert "recency cut off" in lowered
    assert "Effective recency cut off: 2025." in sub.facts


def test_design_limitations_are_distinct_from_the_evidence_gaps(record):
    """Item 6 must not RESTATE item 1's gaps.

    A cross-reference is required, not forbidden: the audit asks for item 6 to
    be "distinct from the evidence gaps in item 1", and the clearest way to be
    distinct is to say so. What must not happen is the specific gap question
    reappearing here as though it were a structural limit of the design.
    """
    design = record.by_key("design_limitations")
    blob = (design.narrative + "\n".join(design.facts)).lower()
    assert "state level capacity" not in blob
    assert "discounted cash flow" not in blob
    # it must, however, point the reader at where the gaps actually are
    assert "evidence gaps" in design.narrative.lower()
    decomposition = record.by_key("question_decomposition")
    gap_lines = [f for f in decomposition.facts if f.startswith("Stated gap: ")]
    for line in gap_lines:
        assert line not in design.facts


# ─────────────────────────────────────────────────────────────────────────────
# Wiring: FinalReport -> ClientReport -> template
# ─────────────────────────────────────────────────────────────────────────────


def test_final_report_carries_the_methodology_and_round_trips(report, record):
    report.methodology = record
    dumped = report.model_dump()
    assert dumped["methodology"]["subsections"][0]["key"] == "question_decomposition"
    revived = FinalReport.model_validate(dumped)
    assert revived.methodology is not None
    assert tuple(s.key for s in revived.methodology.subsections) == (
        REQUIRED_SUBSECTION_KEYS
    )


def test_round_trip_revalidates_every_string_through_client_prose(report, record):
    """The record crosses the bus; the far side must re-check, not trust."""
    dumped = report.model_dump()
    dumped["methodology"] = record.model_dump()
    dumped["methodology"]["subsections"][1]["narrative"] = (
        "The market_analyst applied a sizing method."
    )
    with pytest.raises(ValueError, match="agent name"):
        FinalReport.model_validate(dumped)


def test_client_report_carries_the_methodology(report, record):
    report.methodology = record
    client = ClientReport.from_report(report)
    assert client.methodology is not None
    assert len(client.methodology.subsections) == 6


def test_client_report_has_no_methodology_when_none_was_built(report):
    client = ClientReport.from_report(report)
    assert client.methodology is None


def _template_markup() -> str:
    """HTML_TEMPLATE with Jinja comments stripped.

    The W-10 comment block legitimately NAMES the four bullets it removed
    ("Agents Used, Sources Accessed, Data Points, Limitations") so a future
    reader knows what changed and why. A comment is not markup: Jinja never
    emits it, so the assertions below run against what actually renders.
    """
    from hyperion.agents.delivery.presentation_designer import HTML_TEMPLATE

    return re.sub(r"\{#.*?#\}", "", HTML_TEMPLATE, flags=re.DOTALL)


def test_template_renders_six_headings_and_no_agents_used_row():
    markup = _template_markup()

    assert "report.methodology.subsections" in markup
    assert "sub.heading" in markup
    assert "sub.narrative" in markup
    # the four bullets W-10 removed
    assert "Agents Used" not in markup
    assert "agents_used" not in markup
    # the old bare count sentence is gone from the primary path
    assert "collected data points" not in markup


def test_template_cannot_reach_any_agent_name(record):
    """W-09 boundary check applied to the new block: the methodology loop reads
    only ClientReport attributes, and every string in the record has been
    through ClientProse."""
    markup = _template_markup()
    for agent in AgentName:
        assert agent.value not in markup, agent.value
    assert "report.quality_score" not in markup
    assert "report.fact_check_report" not in markup


def _render_methodology_block(client_report) -> str:
    """Render HTML_TEMPLATE and return only the methodology block.

    This is the sandbox-executable form of the audit's W-10 verification
    snippet. The audit greps a rendered PDF; a live engagement needs provider
    credentials that this environment does not have, so instead we drive the
    REAL ``HTML_TEMPLATE`` through a Jinja env carrying the SAME two filters
    the designer's fallback path registers (``md_to_html``, ``clean_dict_repr``
    from ``hyperion.output.render.TemplateRenderer``). What is verified is
    therefore the actual markup that WeasyPrint would turn into the page, not a
    description of it. What remains unverified by this route: PDF glyph
    shaping, page breaks and font embedding.
    """
    import jinja2

    from hyperion.agents.delivery.presentation_designer import (
        HTML_TEMPLATE,
        PDF_PALETTE,
    )
    from hyperion.output.render import TemplateRenderer

    renderer = TemplateRenderer()
    env = jinja2.Environment(autoescape=True)
    env.filters["md_to_html"] = renderer._markdown_to_html
    env.filters["clean_dict_repr"] = renderer._clean_dict_repr
    html = env.from_string(HTML_TEMPLATE).render(
        report=client_report,
        cover_image=None,
        section_images={},
        section_charts={},
        palette=PDF_PALETTE,
        css_content="",
        risk_analysis_html="",
        appendix_sources_html="",
        endnotes_html="",
    )
    start = html.find('id="methodology"')
    assert start != -1, "the methodology block did not render at all"
    end = html.find('id="endnotes"', start)
    return html[start : end if end > start else start + 20000]


@pytest.fixture
def rendered(report, record) -> str:
    report.methodology = record
    return _render_methodology_block(ClientReport.from_report(report))


def test_rendered_page_contains_all_six_subsection_headings(rendered):
    """The audit's own verification list, run against real rendered markup."""
    lowered = rendered.lower()
    for required in (
        "question decomposition",
        "scope and method selection",
        "retrieval strategy",
        "inclusion",
        "verification",
        "limitations",
    ):
        assert required in lowered, required
    assert rendered.count("<h3>") >= 6


def test_rendered_page_contains_no_agent_names(rendered):
    for agent in ("Financial Analyst", "Fact Checker", "Data Visualizer"):
        assert agent not in rendered, agent
    lowered = rendered.lower()
    for agent in AgentName:
        assert agent.value not in lowered, agent.value
    assert "Agents Used" not in rendered


def test_rendered_page_carries_the_dcf_exclusion_with_its_reason(rendered):
    assert "discounted cash flow valuation" in rendered
    match = re.search(
        r"<li>Excluded: discounted cash flow valuation, because ([^<]*)</li>",
        rendered,
    )
    assert match, "the DCF exclusion must render with a stated reason"
    assert "nation or region" in match.group(1)


def test_rendered_page_cites_query_and_domain_counts(rendered):
    assert "Distinct queries issued: 37." in rendered
    assert "Distinct source domains: 6." in rendered


def test_rendered_page_states_verification_as_a_rate(rendered):
    assert "Pass rate: 95 percent." in rendered
    assert "95 percent" in rendered


def test_rendered_page_has_no_banned_typography(rendered):
    assert "\u2014" not in rendered
    assert "\u2013" not in rendered
    assert not re.search(r"\{['\"]", rendered)


def test_rendered_page_has_no_empty_list_items(rendered):
    """P2-34 render-level invariant applied to the new block."""
    assert not re.search(r"<li>\s*</li>", rendered)
    assert not re.search(r"<ul[^>]*>\s*</ul>", rendered)


def test_rendered_page_keeps_evidence_gaps_separate_from_design_limits(rendered):
    assert "Evidence gaps specific to this question" in rendered
    assert "Limitations of the design" in rendered
    assert rendered.index("Limitations of the design") < rendered.index(
        "Evidence gaps specific to this question"
    )


def test_rendered_page_falls_back_honestly_when_no_record_was_built(report):
    """The template's defensive branch: state that the account is missing rather
    than print a bare count as though it were a methodology."""
    report.methodology = None
    block = _render_methodology_block(ClientReport.from_report(report))
    assert "could not be assembled" in block
    assert "Question decomposition" not in block


def test_declared_gap_statement_survives_the_client_prose_boundary():
    """W-10 side fix: the em dash at insufficiency.py:183 made every engagement
    with a declared gap raise inside ClientReport.from_report."""
    res = InsufficiencyResolution(
        gap_id="gap_9",
        question="What was the 2024 figure?",
        section_id="market_analysis",
        outcome=InsufficiencyOutcome.DECLARED_GAP,
    )
    statement = res.declared_gap_statement()
    assert "\u2014" not in statement
    assert "\u2013" not in statement
    # the whole point: this must not raise
    assert ClientProse.of(statement)


# ─────────────────────────────────────────────────────────────────────────────
# Determinism and robustness
# ─────────────────────────────────────────────────────────────────────────────


def test_build_is_deterministic(report, dag, resolutions):
    a = build_methodology(report, dag=dag, resolutions=resolutions, queries_issued=9)
    b = build_methodology(report, dag=dag, resolutions=resolutions, queries_issued=9)
    assert a.model_dump() == b.model_dump()


def test_build_never_calls_an_llm():
    """W-10 failure mode 3: a free-form prompt describes research that did not
    happen. Enforced by source inspection, so a future edit that introduces a
    completion call fails here."""
    import inspect

    import hyperion.output.methodology as mod

    src = inspect.getsource(mod)
    for forbidden in (
        "_llm_complete",
        "llm_complete",
        "router.complete",
        "acompletion",
        "chat.completions",
        "PROMPT",
    ):
        assert forbidden not in src, forbidden


def test_build_survives_a_report_with_no_sources_and_no_dag():
    minimal = FinalReport(
        engagement_id="eng_min",
        question="What now?",
        recommendation=Recommendation.INVESTIGATE,
        recommendation_rationale="Not enough was recoverable.",
        critical_assumptions=[],
        confidence=ConfidenceLevel.LOW,
        confidence_breakdown={},
        executive_summary="Not enough was recoverable.",
    )
    rec = build_methodology(minimal)
    assert len(rec.subsections) == 6
    retrieval = rec.by_key("retrieval_strategy_and_coverage")
    assert "Distinct source domains: 0." in retrieval.facts
    design = rec.by_key("design_limitations")
    assert "Recency cut off could not be established from the corpus." in design.facts


def test_a_dirty_recorded_string_is_omitted_not_crashed(report, dag):
    """A gap question carrying banned typography must not take the build down."""
    dirty = InsufficiencyResolution(
        gap_id="gap_x",
        question="What is the 2024 figure \u2014 by state \u2014 for capacity?",
        section_id="market_analysis",
        outcome=InsufficiencyOutcome.DECLARED_GAP,
    )
    rec = build_methodology(report, dag=dag, resolutions=[dirty], queries_issued=3)
    assert len(rec.subsections) == 6
    text = _all_text(rec)
    assert "\u2014" not in text
    # the count is still reported even if the restatement was normalised
    assert "Carried forward as stated evidence gaps: 1." in rec.by_key(
        "question_decomposition"
    ).facts
