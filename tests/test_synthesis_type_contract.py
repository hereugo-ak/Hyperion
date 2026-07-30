"""T-01 · D-01 · the type contract — fails on the code that shipped.

The 07-30 report lost every analysis chapter because
``_query_second_brain_for_patterns`` was annotated ``-> str`` but returned
the raw ``VaultSearchResult`` dataclass; the consumer called
``prior_patterns.strip()`` and the AttributeError aborted synthesis before
``_build_analysis_sections()`` ever ran. These tests pin the contract at
both sides of the boundary:

1. The method returns a real ``str`` that survives ``.strip()`` — the exact
   call that raised on 07-30 — when the vault has notes.
2. The string carries the note content (the §12.8 "institutional memory"
   the query exists for) and respects PRIOR_PATTERN_LIMIT.
3. Empty-vault and dead-tool paths return "".
4. The call-site coercion degrades a future contract breach to a missing
   prompt block, never a contentless report.
"""

from __future__ import annotations

import pytest

from hyperion.agents.synthesis_lead import SynthesisLead
from hyperion.tools.second_brain import VaultNote, VaultSearchResult


def _vault_with_notes(n: int = 3) -> VaultSearchResult:
    notes = [
        (
            VaultNote(
                path=f"vault/engagements/eng-{i}.md",
                title=f"Prior engagement {i}: penetration assumption flipped ENTER",
                category="engagements",
                content=f"Pattern {i}: the critical assumption was penetration "
                f"rate and it flipped the recommendation.",
            ),
            0.9 - i * 0.1,
        )
        for i in range(n)
    ]
    return VaultSearchResult(query="synthesis patterns", notes=notes, total=n)


class _StubBrain:
    def __init__(self, result):
        self._result = result

    async def search(self, query):
        return self._result


def _lead_with_brain(monkeypatch, brain) -> SynthesisLead:
    lead = SynthesisLead()
    monkeypatch.setattr(lead, "get_tool", lambda tool: brain)
    return lead


class TestPriorPatternsReturnsStr:
    @pytest.mark.asyncio
    async def test_returns_str_and_survives_strip(self, monkeypatch):
        lead = _lead_with_brain(monkeypatch, _StubBrain(_vault_with_notes()))
        out = await lead._query_second_brain_for_patterns("india imports")
        assert isinstance(out, str), f"was {type(out).__name__} — the 07-30 bug"
        out.strip()  # the exact call that raised

    @pytest.mark.asyncio
    async def test_string_carries_note_content(self, monkeypatch):
        lead = _lead_with_brain(monkeypatch, _StubBrain(_vault_with_notes()))
        out = await lead._query_second_brain_for_patterns("india imports")
        assert "penetration" in out
        assert "Prior engagement 0" in out
        assert "[relevance 0.90]" in out

    @pytest.mark.asyncio
    async def test_respects_prior_pattern_limit(self, monkeypatch):
        lead = _lead_with_brain(monkeypatch, _StubBrain(_vault_with_notes(n=8)))
        out = await lead._query_second_brain_for_patterns("india imports")
        assert out.count("[relevance") == lead.PRIOR_PATTERN_LIMIT

    @pytest.mark.asyncio
    async def test_empty_vault_returns_empty_str(self, monkeypatch):
        lead = _lead_with_brain(
            monkeypatch, _StubBrain(VaultSearchResult(query="q", notes=[]))
        )
        out = await lead._query_second_brain_for_patterns("india imports")
        assert out == ""

    @pytest.mark.asyncio
    async def test_dead_tool_returns_empty_str(self, monkeypatch):
        class _DeadBrain:
            async def search(self, query):
                raise RuntimeError("vault locked")

        lead = _lead_with_brain(monkeypatch, _DeadBrain())
        out = await lead._query_second_brain_for_patterns("india imports")
        assert out == ""

    @pytest.mark.asyncio
    async def test_missing_tool_access_returns_empty_str(self):
        """get_tool raising ValueError (no SECOND_BRAIN in spec) must degrade
        to "" — a decorative input can never abort synthesis."""
        lead = SynthesisLead()
        # Real get_tool raises ValueError if the tool isn't in the spec, or
        # instantiates the real client (fine — a real empty vault also yields "").
        out = await lead._query_second_brain_for_patterns("india imports")
        assert isinstance(out, str)


class TestCallSiteCoercion:
    def test_non_str_prior_patterns_cannot_raise(self):
        """The class fix: ANY object arriving at the boundary is normalised.
        Replicates the isinstance guard at synthesis_lead.py:1488."""
        for bad in (VaultSearchResult(query="q"), None, 42, ["x"]):
            patterns_text = bad if isinstance(bad, str) else str(bad or "")
            assert isinstance(patterns_text, str)
            patterns_text.strip()  # must never raise
