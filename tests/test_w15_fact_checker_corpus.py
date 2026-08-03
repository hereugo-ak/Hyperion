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
5. Hallucination/statistical measurements stay out of client findings and
   render through the operator-only EngagementTelemetry artifact.

Live provider calls and real URL egress cannot run in this sandbox; the
liveness test mocks httpx at the import site, which is the same seam the
production code uses.
"""

from __future__ import annotations

import ast
import inspect
from unittest.mock import AsyncMock, patch

from hyperion.agents.support.fact_checker import FACT_CHECKER_SPEC, FactChecker
from hyperion.schemas.models import (
    Claim,
    ClaimStatus,
    ClaimType,
    ConfidenceLevel,
    KeyFinding,
    Source,
    SourceCredibility,
)
from hyperion.schemas.narrative import EngagementTelemetry


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


class TestLocalCorpusIndependence:
    @staticmethod
    def _finding(content: str, source: Source) -> KeyFinding:
        return KeyFinding(
            id="f1",
            agent="market_analyst",
            finding_type="market_size",
            title="Market size",
            content=content,
            sources=[source],
            confidence=ConfidenceLevel.MEDIUM,
        )

    def test_finding_prose_cannot_verify_its_own_claim(self) -> None:
        checker = _checker()
        claim = _claim("India GST collection was 1.7 lakh crore", [])
        source = _source("Unrelated source data about exports in 2024")
        checker._all_findings = [self._finding(claim.claim, source)]

        assert checker._check_local_corpus(claim) == []

    def test_underlying_source_data_can_verify_claim(self) -> None:
        checker = _checker()
        claim = _claim("India GST collection was 1.7 lakh crore", [])
        source = _source("Official data: India GST collection was 1.7 lakh crore")
        checker._all_findings = [self._finding("Agent-authored summary", source)]

        local = checker._check_local_corpus(claim)

        assert len(local) == 1
        assert local[0].key_data == source.key_data
        assert local[0].key_data != checker._all_findings[0].content

    def test_empty_source_data_cannot_borrow_finding_prose(self) -> None:
        checker = _checker()
        claim = _claim("Market size is $50B", [])
        checker._all_findings = [self._finding(claim.claim, _source(None))]

        assert checker._check_local_corpus(claim) == []


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


class TestTelemetryRouting:
    def test_measurements_are_not_published_as_client_findings(self) -> None:
        run_source = inspect.getsource(FactChecker.run)
        assert 'finding_type="hallucinated_citations"' not in run_source
        assert 'finding_type="statistical_red_flags"' not in run_source
        assert "await self._publish_finding" not in run_source

    def test_measurements_render_in_operator_telemetry(self) -> None:
        telemetry = EngagementTelemetry(
            engagement_id="eng_test",
            fact_check_report={
                "hallucinated_citation_count": 2,
                "statistical_red_flags": ["Growth rate is implausibly high"],
            },
        )
        html = telemetry.render_html()
        assert "Hallucinated citations</td><td>2" in html
        assert "Statistical red flags</td><td>1: Growth rate is implausibly high" in html


class TestSpecialistCorpusHygiene:
    def test_key_data_assignments_are_not_static_provenance(self) -> None:
        """Inspect every ``key_data=`` assignment, not only f-strings.

        A static sentence describes a source but cannot contain the retrieved
        value that the Fact Checker must match. Dynamic expressions are allowed
        because they bind key_data to fetched content, observations, or values.
        """
        import pathlib

        static_assignments = []
        assignments_seen = 0
        for path in pathlib.Path("hyperion/agents/specialists").glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.keyword) or node.arg != "key_data":
                    continue
                assignments_seen += 1
                try:
                    ast.literal_eval(node.value)
                except (ValueError, TypeError):
                    continue
                static_assignments.append(f"{path}:{node.lineno}")

        assert assignments_seen >= 21, "acceptance scan did not cover every key_data= site"
        assert not static_assignments, (
            "key_data must contain retrieved values, not static provenance: "
            + "; ".join(static_assignments)
        )

    def test_macro_sources_serialize_retrieved_values(self) -> None:
        """The four regressed macro sources bind their fetched observations."""
        import pathlib

        for filename in ("financial_analyst.py", "market_analyst.py"):
            source = pathlib.Path("hyperion/agents/specialists", filename).read_text()
            assert "key_data=json.dumps(" in source
            assert ".to_dict()" in source
            assert "Country-specific GDP growth" not in source
            assert '"US GDP growth, inflation' not in source
            assert '"US risk-free rate, inflation' not in source
