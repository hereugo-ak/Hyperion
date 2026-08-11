"""Overhaul Phase 5 (overhaul.md §6 P5) — verification repositioned.

The Quality Gate stops being the first detector of an empty corpus:

- P5.1 corpus floor is measured at the pre-factcheck boundary (the
  orchestrator calls ``_recheck_corpus_midrun`` before the Fact Checker runs,
  exactly as it does before synthesis).
- P5.2 the score scale is consistently /5.0 everywhere (A-12's 3.2/4.0 vs
  2.95/5.0 split) and the boot POLICY line prints BOTH evidence floors.
- P5.4 the Fact Checker verifies claims against the run-scoped Evidence
  Ledger, not just the live web pool.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hyperion.agents.support.fact_checker import FACT_CHECKER_SPEC, FactChecker
from hyperion.orchestrator import WorkflowEngine
from hyperion.schemas.models import (
    Claim,
    ClaimStatus,
    ClaimType,
)
from hyperion.tools.evidence_ledger import (
    new_ledger,
    record_evidence,
    reset_active_ledger,
)


def _claim(text: str = "India GST collection was 1.7 lakh crore") -> Claim:
    return Claim(
        id="c_p5",
        claim=text,
        claim_type=ClaimType.NUMBER,
        agent="market_analyst",
        status=ClaimStatus.UNVERIFIED,
        verification_sources=[],
    )


# ── P5.1 · corpus floor at the pre-factcheck boundary ──────────────────────


def test_orchestrator_rechecks_corpus_before_fact_check() -> None:
    """P5.1: the FACT_CHECKER task branch must run the mid-run corpus re-probe
    before dispatching — a collapsed corpus must not be fact-checked as if it
    were healthy."""
    import inspect

    src = inspect.getsource(WorkflowEngine._execute_task)
    # Slice on the branch markers (unique in the method) so the S4 partial-
    # context block that mentions both agent names cannot skew the slice.
    # The FACT_CHECKER branch contains the same recheck the SYNTHESIS branch has.
    fact_check_slice = src.split("task.agent == AgentName.FACT_CHECKER:")[1] \
        .split("task.agent == AgentName.SYNTHESIS_LEAD:")[0]
    assert "_recheck_corpus_midrun" in fact_check_slice


def test_recheck_corpus_midrun_degrades_on_collapse(monkeypatch) -> None:
    """P5.1/P4.4: when the ledger falls below the contract floor, the recheck
    degrades to AMBER (halved sub-agent ceiling) — measured at the boundary,
    not discovered at the end."""
    from hyperion.agents.base import BaseAgent

    orch = WorkflowEngine.__new__(WorkflowEngine)
    orch._corpus_contract = SimpleNamespace(min_domains=8)
    orch._evidence_reduced_budget = False
    orch._evidence_budget_default = None
    orch._log = MagicMock()

    default_ceiling = BaseAgent.SUB_AGENT_TOTAL_CEILING
    try:
        # Patch the ledger read at the import site the method uses.
        monkeypatch.setattr(
            "hyperion.tools.evidence_ledger.get_evidence_ledger",
            lambda: SimpleNamespace(
                # S7: the mid-run recheck reads ENGAGEMENT evidence only via
                # ledger.all(), so the mock must expose it.
                all=lambda: [
                    SimpleNamespace(domain="a.example", stage="discovery"),
                    SimpleNamespace(domain="b.example", stage="discovery"),
                ],
                distinct_domains=lambda: {"a.example", "b.example"},
            ),
        )
        import asyncio

        asyncio.run(orch._recheck_corpus_midrun(MagicMock()))
        assert orch._evidence_reduced_budget is True
        assert max(1, default_ceiling // 2) == BaseAgent.SUB_AGENT_TOTAL_CEILING
    finally:
        BaseAgent.SUB_AGENT_TOTAL_CEILING = default_ceiling


def test_recheck_corpus_midrun_noop_when_at_floor(monkeypatch) -> None:
    """P5.1: a corpus still at/above the contract triggers no AMBER degrade."""
    from hyperion.agents.base import BaseAgent

    orch = WorkflowEngine.__new__(WorkflowEngine)
    orch._corpus_contract = SimpleNamespace(min_domains=8)
    orch._evidence_reduced_budget = False
    orch._evidence_budget_default = None
    orch._log = MagicMock()

    default_ceiling = BaseAgent.SUB_AGENT_TOTAL_CEILING
    try:
        monkeypatch.setattr(
            "hyperion.tools.evidence_ledger.get_evidence_ledger",
            lambda: SimpleNamespace(  # >= floor: 12 distinct domains
                all=lambda: [
                    SimpleNamespace(domain=f"d{i}.example", stage="discovery")
                    for i in range(12)
                ],
                distinct_domains=lambda: {f"d{i}.example" for i in range(12)},
            ),
        )
        import asyncio

        asyncio.run(orch._recheck_corpus_midrun(MagicMock()))
        assert orch._evidence_reduced_budget is False
        assert default_ceiling == BaseAgent.SUB_AGENT_TOTAL_CEILING
    finally:
        BaseAgent.SUB_AGENT_TOTAL_CEILING = default_ceiling


# ── P5.2 · score scale + two-floor POLICY line ──────────────────────────────


def test_score_scale_is_always_5() -> None:
    """P5.2 (A-12): the quality score is displayed on the 1-5 rubric scale,
    never as 'score/4.0' (the threshold is a comparison line, not a scale)."""
    from hyperion.obs.health import _format_quality_line

    line = _format_quality_line(3.2, threshold=4.0, iterations=3)
    assert "/5.0" in line
    assert "approve ≥ 4.0" in line
    assert "/4.0" not in line


def test_boot_policy_line_prints_both_floors() -> None:
    """P5.2: the boot POLICY line carries BOTH the iteration source floor and
    the corpus deliverability floor, so the operator sees one contract."""
    from hyperion.infra.provenance import _CORPUS_FLOOR_DOMAINS, build_policy

    policy = build_policy()
    assert policy.get("quality_source_floor") == 3
    assert policy.get("corpus_floor_domains") == 8
    assert _CORPUS_FLOOR_DOMAINS == 8


# ── P5.4 · fact-check consumes the Evidence Ledger ──────────────────────────


def _ledger_source(url: str, snippet: str) -> None:
    record_evidence(
        url=url,
        title="Ledger doc",
        snippet=snippet,
        engine="searxng",
        profile="web",
        stage="discovery",
    )


def test_fact_checker_verifies_against_ledger_evidence() -> None:
    """P5.4: a claim whose numbers appear in ledger evidence is verified from
    the ledger WITHOUT a live web search."""
    new_ledger("eng_p5_fc")
    try:
        _ledger_source("https://stats.gov.in/gst", "GST collection was 1.7 lakh crore in FY24")
        checker = FactChecker(FACT_CHECKER_SPEC, bus=None, router=None)  # type: ignore[arg-type]
        sources = checker._check_ledger_corpus(_claim())
        assert any("stats.gov.in" in s.url for s in sources)
    finally:
        reset_active_ledger()


def test_fact_checker_ledger_requires_shared_content() -> None:
    """P5.4: ledger evidence that does NOT support the claim is excluded."""
    new_ledger("eng_p5_fc_no")
    try:
        _ledger_source("https://other.gov/x", "completely unrelated economic policy text")
        checker = FactChecker(FACT_CHECKER_SPEC, bus=None, router=None)  # type: ignore[arg-type]
        sources = checker._check_ledger_corpus(_claim())
        assert sources == []
    finally:
        reset_active_ledger()


def test_fact_checker_merges_ledger_into_search_path(monkeypatch) -> None:
    """P5.4: _search_for_verification consults the ledger as part of the local
    corpus before deciding to hit the web."""
    new_ledger("eng_p5_merge")
    try:
        _ledger_source("https://stats.gov.in/gst", "GST collection was 1.7 lakh crore in FY24")
        checker = FactChecker(FACT_CHECKER_SPEC, bus=None, router=None)  # type: ignore[arg-type]
        # Two independent ledger/local sources should skip the web search.
        checker._all_findings = []
        with patch.object(checker, "_check_independence", return_value=True):
            sources = asyncio_run(checker._search_for_verification(_claim()))
        assert any("stats.gov.in" in s.url for s in sources)
    finally:
        reset_active_ledger()


def asyncio_run(coro):
    import asyncio

    return asyncio.run(coro)
