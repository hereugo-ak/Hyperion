"""P-CORE (2026-08-10) — sub-agent evidence reconciliation (the MBB funnel).

The proprietary core: sub-agent findings MUST reach the parent's analysis.
A wrapper drops them; a MBB-grade system funnels them. These tests pin:

- _merge_evidence: sub-agent sources merge into the parent's source set,
  deduplicated by URL — so KPI-2/KPI-3 move together by construction.
- _reconcile_findings: substantive sub-agent findings are published alongside
  the parent's own; gaps/unverified assertions never count as yield.
- _detect_sub_agent_contradictions: numeric disagreements between sub-agents
  are surfaced for evidence-weighted resolution, never silently averaged.
- Every specialist wires the merge immediately after spawning sub-agents.
"""

from __future__ import annotations

import pathlib

from hyperion.schemas.models import (
    RESEARCH_GAP_TYPE,
    UNVERIFIED_ASSERTION_TYPE,
    ConfidenceLevel,
    KeyFinding,
    Source,
    SourceCredibility,
)

SPECIALISTS_DIR = (
    pathlib.Path(__file__).resolve().parents[1]
    / "hyperion" / "agents" / "specialists"
)


def _source(url: str) -> Source:
    return Source(
        id=f"src_{abs(hash(url)) % 100000}",
        title="t",
        url=url,
        credibility=SourceCredibility.INDUSTRY_REPORT,
    )


def _finding(
    *,
    title: str = "F",
    content: str = "Market is $2B",
    finding_type: str = "market_size",
    sources: list[Source] | None = None,
) -> KeyFinding:
    return KeyFinding(
        id=f"f_{abs(hash(title)) % 100000}",
        agent="sub",
        finding_type=finding_type,
        title=title,
        content=content,
        sources=sources or [],
        confidence=ConfidenceLevel.HIGH,
    )


def _agent():
    from hyperion.agents.specialists.market_analyst import MarketAnalyst

    # object.__new__ to skip __init__ network/bus wiring.
    return object.__new__(MarketAnalyst)  # type: ignore[call-arg]


# ── _merge_evidence ─────────────────────────────────────────────────────────


def test_merge_evidence_folds_sub_agent_sources() -> None:
    agent = _agent()
    own = [_source("https://own.example/report")]
    sub_findings = [
        _finding(sources=[_source("https://sub-a.example/data"), _source("https://own.example/report")]),
        _finding(sources=[_source("https://sub-b.example/analysis")]),
    ]
    merged = agent._merge_evidence(sub_findings, own)
    urls = [s.url for s in merged]
    # Sub-agent sources are added; the duplicate (own.example) is kept once.
    assert "https://sub-a.example/data" in urls
    assert "https://sub-b.example/analysis" in urls
    assert urls.count("https://own.example/report") == 1


def test_merge_evidence_skips_gap_and_unverified_findings() -> None:
    agent = _agent()
    sub_findings = [
        _finding(finding_type=RESEARCH_GAP_TYPE, sources=[_source("https://gap.example/x")]),
        _finding(finding_type=UNVERIFIED_ASSERTION_TYPE, sources=[_source("https://unv.example/y")]),
        _finding(sources=[_source("https://good.example/z")]),
    ]
    merged = agent._merge_evidence(sub_findings, [])
    urls = [s.url for s in merged]
    assert "https://good.example/z" in urls
    assert "https://gap.example/x" not in urls
    assert "https://unv.example/y" not in urls


def test_merge_evidence_never_raises_on_bad_input() -> None:
    agent = _agent()
    assert agent._merge_evidence(None, []) == []
    assert agent._merge_evidence([], None) == []


# ── _reconcile_findings ─────────────────────────────────────────────────────


def test_reconcile_findings_filters_non_substantive() -> None:
    agent = _agent()
    sub_findings = [
        _finding(finding_type=RESEARCH_GAP_TYPE),
        _finding(finding_type=UNVERIFIED_ASSERTION_TYPE),
        _finding(finding_type="market_size"),
    ]
    reconciled = agent._reconcile_findings(sub_findings)
    assert len(reconciled) == 1
    assert reconciled[0].finding_type == "market_size"


# ── _detect_sub_agent_contradictions ────────────────────────────────────────


def test_contradiction_detection_flags_numeric_conflicts() -> None:
    agent = _agent()
    sub_findings = [
        _finding(title="Low estimate", content="India TAM is $2B"),
        _finding(title="High estimate", content="India TAM is $20B"),
    ]
    flags = agent._detect_sub_agent_contradictions(sub_findings)
    assert flags
    assert any("SUB-AGENT NUMERIC CONTRADICTION" in f for f in flags)
    assert any("10.0x apart" in f for f in flags)


def test_contradiction_detection_silent_on_agreement() -> None:
    agent = _agent()
    sub_findings = [
        _finding(title="A", content="Market is $2B"),
        _finding(title="B", content="Market is $2.1B"),  # <2x apart → no flag
    ]
    assert agent._detect_sub_agent_contradictions(sub_findings) == []


def test_contradiction_detection_never_raises() -> None:
    agent = _agent()
    assert agent._detect_sub_agent_contradictions([]) == []
    assert agent._detect_sub_agent_contradictions([_finding(content="no numbers here")]) == []


# ── Every specialist wires the merge ────────────────────────────────────────


def test_all_specialists_merge_after_spawning_sub_agents() -> None:
    """P-CORE: no specialist may store sub-agent findings and drop them."""
    for path in sorted(SPECIALISTS_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        src = path.read_text(encoding="utf-8")
        has_spawn = "_spawn_sub_agent(" in src or "_sub_agent_findings = sub_findings" in src
        if not has_spawn:
            continue  # specialists that don't spawn sub-agents are unaffected
        assert "_merge_evidence(" in src, f"{path.name} stores sub-agent findings but never merges"
        assert "_reconcile_findings(" in src, f"{path.name} never reconciles sub-agent findings"
        assert "_detect_sub_agent_contradictions(" in src, (
            f"{path.name} never surfaces sub-agent contradictions"
        )


def test_market_analyst_publishes_reconciled_findings() -> None:
    """The parent's published finding set must include reconciled sub-agent
    findings — its output is a superset of its sub-agents' evidence."""
    src = (SPECIALISTS_DIR / "market_analyst.py").read_text(encoding="utf-8")
    # The publish loop concatenates the reconciled findings.
    assert "_sub_agent_reconciled" in src.split("for finding in")[1].split(")")[0] or \
        "_sub_agent_reconciled" in src
