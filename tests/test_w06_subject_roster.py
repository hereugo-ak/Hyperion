"""W-06: subject ontology gates the engagement roster.

Pins the fix for RC-6 (a DCF aimed at a nation state):

  1. The roster is a function of (question_type, subject_class) — a
     country-scoped question dispatches ZERO agents whose only methods are
     firm-level; a company-scoped question still gets the financial
     analyst WITH DCF.
  2. Method eligibility, not agent exclusion — FINANCIAL_ANALYST is never
     "dropped"; for NATION_OR_REGION it runs fiscal-cost/public-investment
     methods, for COMPANY it runs DCF/EV-EBITDA/unit economics.
  3. Every considered agent is recorded (dispatched with eligible methods,
     or excluded with a reason) — the audit trail W-10 quotes.
  4. Low subject-class confidence abstains: interactive runs clarify,
     scripted runs raise SubjectClassAbstain — never guess.
  5. The DAG builder asserts the roster invariant at planning time, before
     any tokens are spent.
  6. The single-axis QUESTION_TYPE_AGENTS table is deleted — subject class
     must not be decorative.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hyperion.agents import engagement_director as ed
from hyperion.agents.engagement_director import (
    AGENT_METHODS,
    SUBJECT_CLASS_CONFIDENCE_THRESHOLD,
    EngagementDirector,
    SubjectClassAbstain,
    eligible_methods,
)
from hyperion.schemas.agents import AgentName
from hyperion.schemas.workflow import QuestionType, RosterDecision, SubjectClass

INDIA_Q = "Should India increase manufacturing?"
ACME_Q = "Should Acme Corp increase manufacturing?"


def _director() -> EngagementDirector:
    d = EngagementDirector.__new__(EngagementDirector)
    d._escalation_count = 0
    d._current_dag = None
    return d


def _fake_llm_payload(**overrides) -> SimpleNamespace:
    payload = {
        "question_types": ["go_no_go"],
        "selected_agents": [
            "market_analyst", "competitive_intel", "financial_analyst",
            "risk_analyst", "consumer_insights", "strategy_analyst",
        ],
        "key_question": "k",
        "geographies": [],
        "subject": "manufacturing",
        "subject_class": "company",
        "subject_class_confidence": 0.95,
        "research_domains": [],
        "critical_path": "",
    }
    payload.update(overrides)
    return SimpleNamespace(success=True, content=json.dumps(payload))


def _stub_llm(d: EngagementDirector, response: SimpleNamespace) -> None:
    async def _complete(**kwargs):
        return response
    d._llm_complete = _complete  # type: ignore[attr-defined]


# ─────────────────────────────────────────────────────────────────────────
# 1. The spec's verification snippet, made executable
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_india_question_gates_out_firm_level_only_agents():
    """"Should India increase manufacturing?" → NATION_OR_REGION roster.

    Asserts per spec: subject_class == NATION_OR_REGION, CONSUMER_INSIGHTS
    and COMPETITIVE_INTEL not in the roster (their firm-level methods do
    not apply), and no roster entry whose eligible methods include DCF or
    EV/EBITDA.
    """
    d = _director()
    _stub_llm(d, _fake_llm_payload(
        subject_class="nation_or_region",
        subject_class_confidence=0.97,
        geographies=["India"],
    ))
    question_types, subject_class, roster, key_question = await d._classify_question_llm(INDIA_Q)

    assert subject_class == SubjectClass.NATION_OR_REGION
    assert AgentName.CONSUMER_INSIGHTS not in roster
    assert AgentName.COMPETITIVE_INTEL not in roster

    # No roster entry whose method is DCF or EV/EBITDA
    for agent in roster:
        methods = eligible_methods(agent, subject_class)
        assert "dcf valuation" not in methods
        assert "ev/ebitda comparables" not in methods
        assert "unit economics" not in methods

    # The financial analyst may still serve — but only with nation-level methods
    if AgentName.FINANCIAL_ANALYST in roster:
        methods = eligible_methods(AgentName.FINANCIAL_ANALYST, subject_class)
        assert "fiscal-cost analysis" in methods
        assert "dcf valuation" not in methods


@pytest.mark.asyncio
async def test_acme_question_keeps_financial_analyst_with_dcf():
    """"Should Acme Corp increase manufacturing?" → COMPANY roster with DCF."""
    d = _director()
    _stub_llm(d, _fake_llm_payload(
        subject_class="company",
        subject_class_confidence=0.97,
    ))
    question_types, subject_class, roster, key_question = await d._classify_question_llm(ACME_Q)

    assert subject_class == SubjectClass.COMPANY
    assert AgentName.FINANCIAL_ANALYST in roster
    assert "dcf valuation" in eligible_methods(AgentName.FINANCIAL_ANALYST, subject_class)
    # Firm-level agents ARE eligible for a firm
    assert AgentName.CONSUMER_INSIGHTS in roster
    assert AgentName.COMPETITIVE_INTEL in roster


# ─────────────────────────────────────────────────────────────────────────
# 2. Method eligibility is the mechanism, not an exclusion table
# ─────────────────────────────────────────────────────────────────────────


def test_financial_analyst_is_never_dropped_only_its_methods_vary():
    """FINANCIAL_ANALYST has eligible methods for COMPANY, POLICY and
    NATION_OR_REGION — it is gated by method, not excluded by name."""
    company = eligible_methods(AgentName.FINANCIAL_ANALYST, SubjectClass.COMPANY)
    nation = eligible_methods(AgentName.FINANCIAL_ANALYST, SubjectClass.NATION_OR_REGION)
    policy = eligible_methods(AgentName.FINANCIAL_ANALYST, SubjectClass.POLICY)

    assert "dcf valuation" in company
    assert "dcf valuation" not in nation
    assert "fiscal-cost analysis" in nation
    assert "public-investment analysis" in nation
    assert "fiscal-cost analysis" in policy


def test_method_matrix_covers_every_specialist_and_subject():
    """Every specialist declares ≥1 method; every method lists ≥1 subject
    class; every subject class has ≥3 eligible specialists (a subject with
    zero eligible specialists could never be staffed)."""
    specialists = [a for a in AgentName if a in AGENT_METHODS]
    assert len(specialists) == 12
    for agent, methods in AGENT_METHODS.items():
        assert methods, f"{agent} declares no methods"
        for method, classes in methods.items():
            assert classes, f"{agent}.{method} applies to nothing"
    for sc in SubjectClass:
        eligible_agents = [a for a in AGENT_METHODS if eligible_methods(a, sc)]
        assert len(eligible_agents) >= 3, (
            f"{sc} has only {len(eligible_agents)} eligible specialists"
        )


# ─────────────────────────────────────────────────────────────────────────
# 3. Every roster decision is recorded with a reason
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_exclusions_are_recorded_with_reasons():
    d = _director()
    _stub_llm(d, _fake_llm_payload(
        subject_class="nation_or_region", subject_class_confidence=0.95,
    ))
    await d._classify_question_llm(INDIA_Q)

    decisions = d._roster_decisions
    assert decisions, "no roster decisions recorded"
    assert all(isinstance(r, RosterDecision) for r in decisions)

    excluded = {r.agent: r for r in decisions if not r.dispatched}
    assert AgentName.CONSUMER_INSIGHTS in excluded
    assert excluded[AgentName.CONSUMER_INSIGHTS].reason, "exclusion has no reason"
    assert excluded[AgentName.CONSUMER_INSIGHTS].subject_class == SubjectClass.NATION_OR_REGION

    dispatched = {r.agent: r for r in decisions if r.dispatched}
    for agent, record in dispatched.items():
        assert record.eligible_methods, f"{agent} dispatched with no recorded method"
        assert record.reason, f"{agent} dispatched with no recorded reason"


# ─────────────────────────────────────────────────────────────────────────
# 4. Low confidence abstains — clarify interactively, fail when scripted
# ─────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_low_confidence_abstains_in_scripted_run(monkeypatch):
    """stdin is not a TTY under pytest → abstain-and-fail, never guess."""
    d = _director()
    _stub_llm(d, _fake_llm_payload(
        subject_class="company", subject_class_confidence=0.2,
    ))
    with pytest.raises(SubjectClassAbstain):
        await d._classify_question_llm("Should they expand the thing?")


@pytest.mark.asyncio
async def test_unknown_subject_class_value_abstains(monkeypatch):
    """An unparseable subject_class value IS low confidence."""
    d = _director()
    _stub_llm(d, _fake_llm_payload(
        subject_class="galactic_empire", subject_class_confidence=0.99,
    ))
    with pytest.raises(SubjectClassAbstain):
        await d._classify_question_llm(INDIA_Q)


@pytest.mark.asyncio
async def test_failed_decomposition_abstains_not_defaults_to_company():
    """The old fallback silently staffed from QUESTION_TYPE_AGENTS. W-06:
    no LLM ⇒ no subject class ⇒ abstain-and-fail at planning time."""
    d = _director()
    _stub_llm(d, SimpleNamespace(success=False, content=""))
    with pytest.raises(SubjectClassAbstain):
        await d._classify_question_llm(INDIA_Q)


@pytest.mark.asyncio
async def test_interactive_clarification_resolves_abstain(monkeypatch):
    """When a user IS present, one clarifying question decides the class."""
    import sys
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": "2")
    d = _director()
    _stub_llm(d, _fake_llm_payload(
        subject_class="company", subject_class_confidence=0.1,
    ))
    _, subject_class, roster, _ = await d._classify_question_llm(INDIA_Q)
    assert subject_class == SubjectClass.NATION_OR_REGION
    assert AgentName.CONSUMER_INSIGHTS not in roster


def test_threshold_is_documented_and_sane():
    assert 0.0 < SUBJECT_CLASS_CONFIDENCE_THRESHOLD <= 1.0


# ─────────────────────────────────────────────────────────────────────────
# 5. The DAG builder enforces the invariant at planning time
# ─────────────────────────────────────────────────────────────────────────


def test_build_dag_asserts_every_dispatched_agent_has_eligible_method():
    d = _director()
    d._llm_research_domains = []
    d._llm_subject_class = SubjectClass.NATION_OR_REGION
    d._llm_geographies = []
    d._llm_subject = ""
    d._roster_decisions = []
    with pytest.raises(AssertionError, match="W-06 roster invariant"):
        d._build_dag(
            engagement_id="eng_test",
            question=INDIA_Q,
            question_types=[QuestionType.GO_NO_GO],
            selected_agents=[AgentName.CONSUMER_INSIGHTS],  # firm-level only
            key_question=INDIA_Q,
            second_brain_context="",
        )


def test_build_dag_publishes_subject_class_and_roster_decisions():
    d = _director()
    d._llm_research_domains = []
    d._llm_subject_class = SubjectClass.NATION_OR_REGION
    d._llm_geographies = ["India"]
    d._llm_subject = "manufacturing"
    d._roster_decisions = [
        RosterDecision(
            agent=AgentName.MARKET_ANALYST,
            subject_class=SubjectClass.NATION_OR_REGION,
            eligible_methods=["market sizing"],
            dispatched=True,
            reason="5 declared method(s) apply to nation_or_region",
        )
    ]
    dag = d._build_dag(
        engagement_id="eng_test",
        question=INDIA_Q,
        question_types=[QuestionType.GO_NO_GO],
        selected_agents=[AgentName.MARKET_ANALYST],
        key_question=INDIA_Q,
        second_brain_context="",
    )
    assert dag.subject_class == "nation_or_region"
    assert len(dag.roster_decisions) == 1
    assert dag.roster_decisions[0].agent == AgentName.MARKET_ANALYST


# ─────────────────────────────────────────────────────────────────────────
# 6. The single-axis table is gone; subject class is not decorative
# ─────────────────────────────────────────────────────────────────────────


def test_single_axis_table_is_deleted():
    assert not hasattr(ed, "QUESTION_TYPE_AGENTS"), (
        "QUESTION_TYPE_AGENTS must be deleted — leaving it as the roster "
        "source makes subject class decorative (W-06 failure mode)"
    )
    # co_names excludes docstrings/comments: it proves the name is not
    # REFERENCED as code in either classification path.
    for fn_name in ("_classify_question_llm", "_classification_fallback"):
        code = getattr(EngagementDirector, fn_name).__code__
        assert "QUESTION_TYPE_AGENTS" not in code.co_names


def test_empty_roster_after_gate_fails_loudly(monkeypatch):
    """A proposal the gate fully rejects must not silently dispatch nothing.

    The real matrix guarantees ≥3 eligible specialists per subject class, so
    the only way to reach the empty-roster path is a matrix that declares
    nothing — which is exactly the broken-configuration case the assertion
    exists to catch loudly instead of dispatching an empty engagement.
    """
    monkeypatch.setattr(ed, "AGENT_METHODS", {})
    d = _director()
    with pytest.raises(SubjectClassAbstain, match="[Nn]o specialist"):
        d._gate_roster_by_subject(
            [AgentName.MA_ANALYST],
            SubjectClass.TECHNOLOGY,
            "Should solid-state batteries scale?",
        )
