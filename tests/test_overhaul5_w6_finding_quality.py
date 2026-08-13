"""OVERHAUL5 W6 (D-08 / D-09 / D-10) — finding quality at birth.

- W6.3: bus content-hash dedupe — recovery re-runs can't double-add findings
  (the 08-12 report repeated the same 5 sources twice).
- W6.1+W6.2: section assembly drops gap-placeholders and off-topic findings
  (the report cited "Risk analysis gap…" and Apple/iPhone papers for an
  India-vs-China manufacturing question).
- W6.4: 'Unknown' placeholder defaults are gone from specialist models (they
  rendered as data and tripped the DATA VOID blocker).
- W6.5: verdict/narrative consistency enforced at construction (CONDITIONAL
  vs 'no-go' contradiction killed before the gate).

Fail-first: bus dedupe, section filter, verdict reconcile, and the
source-scan all fail on pre-W6 code.
"""

from __future__ import annotations

import glob
import re
from unittest.mock import AsyncMock

import pytest

from hyperion.agents.bus import get_bus, reset_bus
from hyperion.agents.synthesis_lead import (
    SynthesisLead,
    _verdict_conflict,
)
from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import ConfidenceLevel, KeyFinding


def _finding(title: str, content: str, *, ftype: str = "market_size") -> KeyFinding:
    return KeyFinding(
        id=f"f_{abs(hash(title))}", agent="market_analyst",
        finding_type=ftype, title=title, content=content,
        confidence=ConfidenceLevel.MEDIUM,
    )


# ── W6.3 · bus content-hash dedupe ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_bus_dedupes_identical_findings() -> None:
    """[FF] Publishing the same finding twice yields ONE retained finding."""
    reset_bus()
    bus = get_bus()
    finding = _finding("India EV market size", "The market is projected at $40B by 2030.")
    await bus.publish_finding(AgentName.MARKET_ANALYST, finding)
    await bus.publish_finding(AgentName.MARKET_ANALYST, finding)
    retained = bus.get_retained_findings()
    assert len(retained) == 1, "identical finding from a recovery re-run is a duplicate"


@pytest.mark.asyncio
async def test_bus_keeps_distinct_findings() -> None:
    reset_bus()
    bus = get_bus()
    await bus.publish_finding(
        AgentName.MARKET_ANALYST,
        _finding("TAM top-down", "Top-down sizing gives $20B."),
    )
    await bus.publish_finding(
        AgentName.MARKET_ANALYST,
        _finding("TAM bottom-up", "Bottom-up sizing gives $15B."),
    )
    assert len(bus.get_retained_findings()) == 2
    reset_bus()


# ── W6.1 + W6.2 · section assembly filter ──────────────────────────────────

def _synthesis() -> SynthesisLead:
    obj = SynthesisLead.__new__(SynthesisLead)
    obj._question = "how india can beat china in manufacturing ?"
    obj.section_gaps = []
    return obj


def test_section_filter_drops_gap_placeholders() -> None:
    """[FF] A research_gap placeholder is not section evidence."""
    synth = _synthesis()
    gap = _finding("Risk analysis gap", "No specific risks could be identified",
                   ftype="research_gap")
    real = _finding("China manufacturing cost advantage",
                    "China's labor cost advantage narrows as wages rise.")
    evidence, dropped = synth._filter_section_findings([gap, real])
    assert real in evidence
    assert gap not in evidence
    assert dropped == 1


def test_section_filter_drops_off_topic_findings() -> None:
    """[FF] An Italian-fashion finding (zero keyword overlap) is off-topic
    for an India-vs-China manufacturing question."""
    synth = _synthesis()
    off_topic = _finding(
        "Italian Fashion Industry's Digitalization",
        "The COVID-19 pandemic exposed vulnerabilities in global fashion supply chains.",
    )
    on_topic = _finding(
        "India manufacturing capex",
        "India's PLI scheme boosts manufacturing capex and export capacity.",
    )
    evidence, dropped = synth._filter_section_findings([off_topic, on_topic])
    assert on_topic in evidence
    assert off_topic not in evidence
    assert dropped == 1


def test_section_filter_noop_without_question() -> None:
    synth = _synthesis()
    synth._question = ""
    f = _finding("anything at all", "content without a question to score against")
    evidence, dropped = synth._filter_section_findings([f])
    assert evidence == [f]
    assert dropped == 0


# ── W6.4 · 'Unknown' placeholders gone ─────────────────────────────────────

def test_no_unknown_placeholder_defaults_in_specialists() -> None:
    """[FF] The 'Unknown' data-placeholder pattern is banned from specialist
    code — it rendered as data and tripped the DATA VOID integrity blocker."""
    offenders: list[str] = []
    for path in glob.glob("hyperion/agents/specialists/*.py"):
        with open(path, encoding="utf-8") as fh:
            src = fh.read()
        if re.search(r',\s*"Unknown"\)', src):
            offenders.append(path)
    assert not offenders, f"'Unknown' placeholder defaults remain in: {offenders}"


# ── W6.5 · verdict/narrative consistency ───────────────────────────────────

def test_verdict_conflict_detects_contradiction() -> None:
    assert _verdict_conflict("conditional", "The market is a no-go at this stage.") == "no-go"
    assert _verdict_conflict("conditional", "Proceed only if conditions are met.") is None
    assert _verdict_conflict("no_go", "We recommend entering the market now.") == "enter"
    assert _verdict_conflict("enter", "Do not enter — reject the opportunity.") == "do not"


@pytest.mark.asyncio
async def test_reconcile_verdict_regenerates_conflicting_summary() -> None:
    """[FF] A CONDITIONAL verdict with 'no-go' in the summary is regenerated
    with the verdict as a hard constraint — the contradiction dies at
    construction, not at the gate."""
    synth = _synthesis()
    calls: list[str] = []

    async def _fake_llm_complete(**kwargs: object):
        calls.append(str(kwargs.get("user_prompt", "")))
        resp = AsyncMock()
        resp.success = True
        resp.content = (
            "India can proceed — provided PLI disbursement accelerates "
            "and export logistics improve."
        )
        return resp

    synth._llm_complete = _fake_llm_complete  # type: ignore[method-assign]
    draft = await synth._reconcile_verdict({
        "recommendation": "conditional",
        "executive_summary": (
            "This is a no-go for India unless conditions change."
        ),
    })
    assert "no-go" not in draft["executive_summary"].lower()
    assert calls, "the conflicting summary must be regenerated"
