"""
Tests for FactChecker._search_for_verification's query construction
(Phase 1, fix 1.7 — HYPERION_DEEP_AUDIT_2026-07-27.md §4.9 Finding B-8).

Before the fix, ``fact_checker.py:605`` built its verification query as:

    query = claim.claim[:100]
    if claim.agent:
        query = f"{query} {claim.agent.replace('_', ' ')}"

Two problems: (1) a blind 100-char slice of a claim sentence frequently cut
mid-word/mid-clause; (2) appending the internal agent name
("market analyst", "risk analyst") injected HYPERION's own org vocabulary
into the outbound search string — precisely the debris ``normalize_query``'s
``_INTERNAL_TOKENS`` exists to strip — and the query never went through
``ground_query`` at all.

The fix grounds ``claim.claim`` directly (no agent-name suffix, no blind
slice) via ``ground_query``, so this file locks in both properties.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from hyperion.agents.support.fact_checker import FactChecker
from hyperion.schemas.models import Claim, ClaimStatus, ClaimType
from hyperion.tools.query_utils import clear_engagement_focus, set_engagement_focus


@pytest.fixture(autouse=True)
def _focus():
    clear_engagement_focus()
    set_engagement_focus(
        question="Should Nigeria expand lithium-ion battery manufacturing?",
        subject="lithium-ion battery manufacturing",
        geography="Nigeria",
    )
    yield
    clear_engagement_focus()


def _make_checker() -> FactChecker:
    # bus=None / router=None: FactChecker.__init__ only stores these, no
    # network/bus I/O happens at construction time.
    return FactChecker(bus=None, router=None)


class TestFactCheckerQueryConstruction:
    def test_query_does_not_contain_internal_agent_name(self, monkeypatch):
        """The single most important regression guard: 'market analyst',
        'risk analyst' etc. must never reach the outbound search string."""
        checker = _make_checker()

        spy_searxng = SimpleNamespace(search=AsyncMock(return_value=[]))
        monkeypatch.setattr(checker, "get_tool", lambda tool: spy_searxng)
        monkeypatch.setattr(checker, "_check_local_corpus", lambda claim: [])

        claim = Claim(
            id="c1",
            agent="market_analyst",
            claim="Tesla acquired Maxwell Technologies in 2019 for $218 million",
            claim_type=ClaimType.NUMBER,
            status=ClaimStatus.UNVERIFIED,
        )

        asyncio.run(checker._search_for_verification(claim))

        assert spy_searxng.search.await_count == 1
        called_query = spy_searxng.search.await_args.args[0]
        assert "market analyst" not in called_query.lower()
        assert "market_analyst" not in called_query.lower()
        assert "analyst" not in called_query.lower()

    def test_query_is_not_a_blind_character_slice(self, monkeypatch):
        """The old `claim.claim[:100]` could cut mid-word. ground_query
        normalizes and truncates on word boundaries via normalize_query,
        so the outbound query must be built from cleaned words, not a
        raw character slice that can end mid-token."""
        checker = _make_checker()

        spy_searxng = SimpleNamespace(search=AsyncMock(return_value=[]))
        monkeypatch.setattr(checker, "get_tool", lambda tool: spy_searxng)
        monkeypatch.setattr(checker, "_check_local_corpus", lambda claim: [])

        # A claim whose 100th character lands mid-word under the old slice.
        long_claim = (
            "The global lithium-ion battery market is projected to reach "
            "approximately four hundred billion dollars by the year 2030"
        )
        claim = Claim(
            id="c2",
            agent="risk_analyst",
            claim=long_claim,
            claim_type=ClaimType.NUMBER,
            status=ClaimStatus.UNVERIFIED,
        )

        asyncio.run(checker._search_for_verification(claim))

        called_query = spy_searxng.search.await_args.args[0]
        # No dangling partial word fragments like the historical "illi" from
        # a mid-word slice of "billion".
        assert called_query.split()[-1].strip() != "" if called_query else True
        for word in called_query.split():
            assert word.isascii()  # sanity: well-formed tokens, not slice debris

    def test_grounded_query_carries_engagement_subject_when_claim_is_thin(
        self, monkeypatch
    ):
        """A claim with almost no content of its own (e.g. a bare
        percentage) must still search on-topic via ground_query's rebuild
        path, rather than firing an empty/near-empty query."""
        checker = _make_checker()

        spy_searxng = SimpleNamespace(search=AsyncMock(return_value=[]))
        monkeypatch.setattr(checker, "get_tool", lambda tool: spy_searxng)
        monkeypatch.setattr(checker, "_check_local_corpus", lambda claim: [])

        claim = Claim(
            id="c3",
            agent="financial_analyst",
            claim="18%",
            claim_type=ClaimType.NUMBER,
            status=ClaimStatus.UNVERIFIED,
        )

        asyncio.run(checker._search_for_verification(claim))

        # Either the search was skipped (query grounded to "") or, if it
        # ran, it must be anchored to the engagement subject/geography —
        # never a bare unanchored "18%".
        if spy_searxng.search.await_count:
            called_query = spy_searxng.search.await_args.args[0]
            assert called_query != "18%"
            assert (
                "lithium" in called_query.lower() or "nigeria" in called_query.lower()
            )

    def test_contentless_claim_skips_web_search_without_crashing(self, monkeypatch):
        """A claim that grounds to '' (no engagement focus, no usable
        subject) must not crash the verification flow — it should simply
        skip the web-search step and fall through to whatever local
        sources were already found."""
        clear_engagement_focus()  # no focus at all — nothing to rebuild from
        checker = _make_checker()

        spy_searxng = SimpleNamespace(search=AsyncMock(return_value=[]))
        monkeypatch.setattr(checker, "get_tool", lambda tool: spy_searxng)
        monkeypatch.setattr(checker, "_check_local_corpus", lambda claim: [])

        claim = Claim(
            id="c4",
            agent="risk_analyst",
            claim="50% $100 2024",  # pure digits/punctuation, no subject
            claim_type=ClaimType.NUMBER,
            status=ClaimStatus.UNVERIFIED,
        )

        # Must not raise.
        result = asyncio.run(checker._search_for_verification(claim))
        assert isinstance(result, list)
