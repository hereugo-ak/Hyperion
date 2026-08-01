"""W-15 verification: fact checker corpus and measurement validity.

Covers the five acceptance criteria that can be exercised without live
network or provider calls:

1. A claim whose only sources carry key_data=None resolves to
   UNVERIFIABLE, never HALLUCINATED, never VERIFIED.
2. _verify_claim and _validate_evidence_chains share one matching
   algorithm (_source_supports_claim); the naive substring block is gone.
3. A network failure during URL liveness resolves to UNVERIFIABLE
   (UNKNOWN), never silently to alive/verified.
4. ROUND_NUMBER_SUSPECTS matches equivalent magnitudes across notation
   ($1B == $1000M == $1,000,000,000) and deduplicates repeated flags.
5. The hallucination/statistical findings carry derived confidence, not a
   hardcoded HIGH.

Live provider calls and real URL egress cannot run in this sandbox; the
liveness test mocks httpx at the import site, which is the same seam the
production code uses.
"""

from __future__ import annotations

import inspect
import re
from unittest.mock import AsyncMock, patch

import pytest

from hyperion.agents.support.fact_checker import FactChecker
from hyperion.agents.support.fact_checker import FACT_CHECKER_SPEC
from hyperion.schemas.models import (
    Claim,
    ClaimStatus,
    ClaimType,
    ConfidenceLevel,
    Source,
    SourceCredibility,
)


def _checker() -> FactChecker:
    return FactChecker(FACT_CHECKER_SPEC, bus=None, router=None)  # type: ignore[arg-type]


def _claim(text: str, sources: list[Source]) -> Claim:
    return Claim(
        id="c1",
        claim=text,
        claim_type=ClaimType.NUMBER,
        agent="market_analyst",
        status=ClaimStatus.UNVERIFIED,
        verification_sources=sources,
    )


def _source(key_data: str | None, url: str = "https://example.gov/data") -> Source:
    return Source(
        id="s1",
        title="Example",
        url=url,
        credibility=SourceCredibility.GOVERNMENT,
        key_data=key_data,
    )


class TestNoneKeyDataIsUnverifiable:
    async def test_none_key_data_never_hallucinated(self) -> None:
        checker = _checker()
        claim = _claim("India GST collection was 1.7 lakh crore", [_source(None)])
        breaks, hallucinated = await checker._validate_evidence_chains([claim])
        assert claim.status == ClaimStatus.UNVERIFIABLE
        assert claim not in hallucinated
        assert claim.status not in (ClaimStatus.HALLUCINATED, ClaimStatus.VERIFIED)

    async def test_empty_key_data_never_hallucinated(self) -> None:
        checker = _checker()
        claim = _claim("Market size is $50B", [_source("   ")])
        _, hallucinated = await checker._validate_evidence_chains([claim])
        assert claim.status == ClaimStatus.UNVERIFIABLE
        assert not hallucinated


class TestSingleMatchingAlgorithm:
    def test_verify_claim_uses_shared_matcher(self) -> None:
        """The naive substring/word-overlap block must be gone; _verify_claim
        must call the same _source_supports_claim the chains path uses."""
        src = inspect.getsource(FactChecker._verify_claim)
        assert "_source_supports_claim" in src
        assert "claim_lower" not in src
        assert ".split()" not in src

    def test_only_one_matcher_definition(self) -> None:
        import hyperion.agents.support.fact_checker as fc

        tree = inspect.getsource(fc)
        assert tree.count("def _source_supports_claim") == 1


class TestTriStateLiveness:
    async def test_network_failure_is_unknown_not_alive(self) -> None:
        checker = _checker()
        with patch("httpx.AsyncClient") as mock_client:
            mock_client.return_value.__aenter__ = AsyncMock(
                side_effect=ConnectionError("egress down")
            )
            mock_client.return_value.__aexit__ = AsyncMock(return_value=False)
            result = await checker._url_alive("https://example.gov/data")
        assert result is None

    async def test_4xx_is_confirmed_dead(self) -> None:
        checker = _checker()
        response = type("R", (), {"status_code": 404})()
        client = AsyncMock()
        client.head = AsyncMock(return_value=response)
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("httpx.AsyncClient", return_value=client):
            result = await checker._url_alive("https://example.gov/gone")
        assert result is False

    async def test_unknown_liveness_routes_to_unverifiable(self) -> None:
        checker = _checker()
        checker._url_alive = AsyncMock(return_value=None)  # egress failed
        # Source has content but does NOT contain the claim's data, so the
        # chain path reaches the liveness signal.
        src = _source("Unrelated regulatory text with no matching tokens 12345")
        claim = _claim("Market size is $50B with growth accelerating", [src])
        _, hallucinated = await checker._validate_evidence_chains([claim])
        assert not hallucinated
        assert claim.status == ClaimStatus.UNVERIFIABLE

    async def test_dead_url_plus_no_overlap_is_hallucinated(self) -> None:
        checker = _checker()
        checker._url_alive = AsyncMock(return_value=False)  # confirmed dead
        src = _source("Unrelated regulatory text with no matching tokens 12345")
        claim = _claim("Market size is $50B with growth accelerating", [src])
        _, hallucinated = await checker._validate_evidence_chains([claim])
        assert claim in hallucinated
        assert claim.status == ClaimStatus.HALLUCINATED


class TestRoundNumberNormalisation:
    def test_equivalent_magnitudes_flagged_regardless_of_notation(self) -> None:
        checker = _checker()
        for text in (
            "The market is worth $1B annually",
            "The market is worth $1000M annually",
            "The market is worth $1,000,000,000 annually",
            "The market is worth 1 billion dollars",
        ):
            claim = _claim(text, [])
            flags = checker._run_statistical_sanity_checks([claim])
            assert any("round number" in f.lower() for f in flags), text

    def test_duplicate_flags_deduplicated(self) -> None:
        checker = _checker()
        claim = _claim("Revenue was $1B and profit also $1000M", [])
        flags = checker._run_statistical_sanity_checks([claim])
        round_flags = [f for f in flags if "round number" in f.lower()]
        assert len(round_flags) == 1

    def test_non_suspect_magnitudes_not_flagged(self) -> None:
        checker = _checker()
        claim = _claim("Revenue was $1.37B in 2024", [])
        flags = checker._run_statistical_sanity_checks([claim])
        assert not [f for f in flags if "round number" in f.lower()]


class TestDerivedTelemetryConfidence:
    def test_helper_thresholds(self) -> None:
        assert FactChecker._telemetry_confidence(1, 400) == ConfidenceLevel.LOW
        assert FactChecker._telemetry_confidence(2, 400) == ConfidenceLevel.MEDIUM
        assert FactChecker._telemetry_confidence(40, 50) == ConfidenceLevel.HIGH
        assert FactChecker._telemetry_confidence(3, 12) == ConfidenceLevel.HIGH

    def test_no_hardcoded_high_on_telemetry_findings(self) -> None:
        import hyperion.agents.support.fact_checker as fc

        tree = inspect.getsource(fc)
        hallucinated_block = tree.split('finding_type="hallucinated_citations"')[1]
        hallucinated_block = hallucinated_block.split("await self._publish_finding")[0]
        assert "ConfidenceLevel.HIGH" not in hallucinated_block
        assert "_telemetry_confidence" in hallucinated_block


class TestSpecialistCorpusHygiene:
    def test_no_provenance_sentences_remain(self) -> None:
        """Zero key_data=f"<prose description>" assignments in specialists;
        every remaining f-string assignment carries real data (numbers or
        snapshot metadata), never a where-it-came-from sentence."""
        import pathlib

        bad = []
        pattern = re.compile(r'key_data=f"([^"]*)"')
        provenance_markers = (
            "data from", "data for", "reviews from", "snapshots for",
            "snapshot from", "content from", "evolution for",
        )
        for path in pathlib.Path("hyperion/agents/specialists").glob("*.py"):
            for match in pattern.finditer(path.read_text()):
                body = match.group(1)
                if any(marker in body.lower() for marker in provenance_markers):
                    bad.append(f"{path}: {body}")
        assert not bad, "provenance sentences remain: " + "; ".join(bad)
