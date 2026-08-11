"""OVERHAUL4 P2/P3/P4 regression guards.

- P2.1/P2.3: query dispatch strips instruction debris — the "Scrape X
  pricing page extract tiers" and trailing-``OR`` ``site:`` queries that
  produced crossref/openalex 429s and the arxiv HTTP 400 (docker 17:09,
  2026-08-11) must never reach a scholar API verbatim.
- P3.1: every specialist is a section-producing agent — the Aug-11 empty
  report was built by a chapter allowlist that silently dropped
  ``strategy_analyst``.
- P4.1: the corpus floor must consult the evidence ledger so a synthesis
  defect (empty report over a 542-domain ledger) is not misreported as a
  retrieval outage.
"""

from __future__ import annotations

import pytest

from hyperion.tools.searxng import SearxNGClient

# ── P2.1/P2.3: instruction debris sanitizer ───────────────────────────────


@pytest.mark.parametrize(
    "raw,strict,expected",
    [
        (
            "fintech Scrape Circle Internet Financial Inc pricing page "
            "extract pricing tiers features per tier discounts India",
            True,
            "fintech Circle Internet Financial Inc pricing page pricing tiers "
            "features per tier discounts India",
        ),
        (
            "fintech Binance funding rounds total raised investors headcount "
            "site:crunchbase.com OR site:techcrunch.com OR",
            True,
            "fintech Binance funding rounds total raised investors headcount",
        ),
        (
            "fintech Scrape reviews G2 Capterra Trustpilot extract sentiment "
            "pain points buying triggers frequency data India",
            True,
            "fintech reviews G2 Capterra Trustpilot sentiment pain points "
            "buying triggers frequency data India",
        ),
        # Legitimate keyword queries must pass through untouched.
        ("india central bank stablecoin regulation compliance", True,
         "india central bank stablecoin regulation compliance"),
        # Non-strict keeps boolean operators (web crawlers can use them).
        ("crypto risk OR opportunity India", False,
         "crypto risk OR opportunity India"),
    ],
)
def test_strip_instruction_debris(raw: str, strict: bool, expected: str) -> None:
    got = SearxNGClient._strip_instruction_debris(raw, strict=strict)
    assert got == expected


def test_scholar_shape_applies_strict_sanitizer() -> None:
    """A planner leak ('Scrape ... site:... OR') is clean before scholar APIs."""
    raw = (
        "fintech Scrape MakerDAO pricing page extract pricing tiers features "
        "per tier discounts DAI India"
    )
    shaped = SearxNGClient._shape_query_for_profile(raw, "scholar")
    assert "scrape" not in shaped.lower()
    assert "extract" not in shaped.lower()
    assert "site:" not in shaped.lower()
    assert " or " not in f" {shaped} ".lower()


# ── P3.1: every specialist is a section-producing agent ───────────────────


def test_strategy_analyst_is_section_producing() -> None:
    from hyperion.agents.synthesis_lead import SECTION_PRODUCING_AGENTS
    from hyperion.schemas.agents import AgentName

    specialists = {
        AgentName.MARKET_ANALYST,
        AgentName.COMPETITIVE_INTEL,
        AgentName.FINANCIAL_ANALYST,
        AgentName.RISK_ANALYST,
        AgentName.TECHNOLOGY_ANALYST,
        AgentName.OPERATIONS_ANALYST,
        AgentName.REGULATORY_ANALYST,
        AgentName.SUSTAINABILITY_ANALYST,
        AgentName.CONSUMER_INSIGHTS,
        AgentName.MA_ANALYST,
        AgentName.INNOVATION_ANALYST,
        AgentName.STRATEGY_ANALYST,
    }
    missing = specialists - set(SECTION_PRODUCING_AGENTS)
    assert not missing, f"specialists missing from SECTION_PRODUCING_AGENTS: {missing}"


# ── P3.4: deterministic digest fallback is a real, citable section ──────


def test_deterministic_section_fallback_never_empty() -> None:
    """OVERHAUL4 P3.2/P3.4: when the narrative LLM cannot produce a body,
    the section is a deterministic finding digest — a report built from it
    still has sections AND cited source domains (never a 0-domain shell)."""
    from hyperion.agents.synthesis_lead import SynthesisLead
    from hyperion.schemas.models import (
        ConfidenceLevel,
        KeyFinding,
        Source,
        SourceCredibility,
    )

    lead = SynthesisLead()
    findings = [
        KeyFinding(
            id="kf_1",
            agent="market_analyst",
            finding_type="market_size",
            title="Indian fintech TAM",
            content="The Indian fintech market is projected to grow at 18% CAGR.",
            implications="Entry is viable but crowded.",
            confidence=ConfidenceLevel.MEDIUM,
            sources=[
                Source(
                    id="src_1",
                    url="https://example1.com/a",
                    title="Example One",
                    credibility=SourceCredibility.INDUSTRY_REPORT,
                ),
                Source(
                    id="src_2",
                    url="https://example2.com/b",
                    title="Example Two",
                    credibility=SourceCredibility.NEWS,
                ),
            ],
        )
    ]
    body = lead._deterministic_section_body("market_analyst", findings, "Market Landscape")
    assert "Market Landscape" in body
    assert "Indian fintech TAM" in body
    assert "Entry is viable but crowded." in body  # implication survives
    assert len(body) > 200


# ── P4.1: corpus floor is ledger-aware ────────────────────────────────────


def test_corpus_floor_no_block_when_ledger_rich() -> None:
    """Report cites <8 domains but the evidence ledger holds >= 8: no hard
    CORPUS FLOOR integrity blocker — the deficiency is synthesis citation,
    which the evidence_sufficiency dimension already penalizes."""
    from hyperion.agents.support.quality_gate import QualityGate
    from hyperion.tools.evidence_ledger import new_ledger, record_evidence

    ledger = new_ledger("test_floor_rich")
    for i in range(8):
        record_evidence(
            url=f"https://domain{i}.example.com/paper/{i}",
            title=f"evidence {i}",
            engine="crossref",
            profile="scholar",
            stage="discovery",
        )
    assert len(ledger.distinct_domains()) >= 8

    gate = QualityGate()
    urls = [
        "https://a.example.com/x",
        "https://b.example.org/y",
        "https://c.example.net/z",
    ]
    blockers = gate._corpus_floor_blocker(urls)
    assert all("CORPUS FLOOR" not in b for b in blockers)


def test_corpus_floor_blocks_when_ledger_thin() -> None:
    """A genuinely thin base (report < 8 AND ledger < 8) still hard-blocks."""
    from hyperion.agents.support.quality_gate import QualityGate
    from hyperion.tools.evidence_ledger import new_ledger

    new_ledger("test_floor_thin")  # empty ledger
    gate = QualityGate()
    urls = ["https://a.example.com/x", "https://b.example.org/y"]
    blockers = gate._corpus_floor_blocker(urls)
    assert any("CORPUS FLOOR" in b for b in blockers)
