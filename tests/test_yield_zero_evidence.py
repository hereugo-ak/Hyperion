"""T-13 · D-12 · zero-evidence engagements are counted and flagged.

The 07-30 run issued dozens of real SearXNG queries — every one returned zero
URLs because both engines were dead — and the engagement yield metric read
``0/0 URLs (0%) extracted ... 0 search calls``. The counters were incremented
only at the end of the SUCCESS path, so the one state the metric existed to
catch was the one state it couldn't see. These tests lock the fix:

1. A deep-search call whose discovery finds ZERO urls still records a search
   call (the early-return path that previously skipped the recorder).
2. A deep-search call that RAISES mid-discovery still records a search call.
3. The orchestrator's zero-evidence gate turns a 0-call/0-char yield report
   into an engagement failure, never a tidy "0%" success line.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hyperion.orchestrator import zero_evidence_failure
from hyperion.tools.deep_search import (
    DeepSearchClient,
    engagement_yield_report,
    reset_engagement_yield,
)


@pytest.fixture(autouse=True)
def clean_yield():
    reset_engagement_yield()
    yield
    reset_engagement_yield()


class TestEverySearchExitIsCounted:
    @pytest.mark.asyncio
    async def test_zero_discovery_still_counts_as_a_search_call(self):
        """The exact 07-30 path: discovery returns [], search() early-returns,
        and the engagement accumulator MUST still see the call."""
        client = DeepSearchClient()
        with patch.object(
            client, "_discover", new=AsyncMock(return_value=([], [], {"searxng": "HTTP 403"}))
        ):
            result = await client.search("india import tariff", depth="quick")

        assert "discovery found no URLs" in result.error
        report = engagement_yield_report()
        assert report["search_calls"] == 1, (
            "a search that found nothing was not counted — the 07-30 defect"
        )
        assert report["urls_discovered"] == 0
        assert report["chars_retained"] == 0

    @pytest.mark.asyncio
    async def test_raising_discovery_still_counts_as_a_search_call(self):
        """An exception mid-discovery must not erase the attempt from the
        engagement metric either — partial evidence of death is still evidence."""
        client = DeepSearchClient()
        with patch.object(
            client, "_discover", new=AsyncMock(side_effect=OSError("connection refused"))
        ), pytest.raises(OSError):
            await client.search("india import tariff", depth="quick")

        report = engagement_yield_report()
        assert report["search_calls"] == 1

    @pytest.mark.asyncio
    async def test_successful_search_counts_exactly_once(self):
        """The success path must not double-record now that recording is
        guaranteed at every exit."""
        from hyperion.tools.deep_search import ExtractedContent

        client = DeepSearchClient()
        relevant = (
            "India import tariff policy shapes trade deficit outcomes. "
            "Tariff rates on Indian imports fell this year. "
        )
        body = (relevant * (600 // len(relevant) + 1))[:600]
        extracted = [
            ExtractedContent(
                url="https://example.com/0", title="t", content=body, tool_used="jina"
            )
        ]
        with patch.object(
            client, "_discover", new=AsyncMock(return_value=(["https://example.com/0"], ["searxng"], {}))
        ), patch.object(
            client, "_extract_batch", new=AsyncMock(return_value=(extracted, ["jina"], ["jina"], {}))
        ):
            await client.search("india import tariff trade deficit", depth="quick")

        report = engagement_yield_report()
        assert report["search_calls"] == 1
        assert report["urls_discovered"] == 1


class TestZeroEvidenceGate:
    def test_zero_calls_zero_chars_is_a_failure(self):
        msg = zero_evidence_failure({"search_calls": 0, "chars_retained": 0})
        assert msg is not None
        assert "Zero evidence" in msg
        assert "ungrounded" in msg

    def test_zero_calls_with_evidence_is_not_a_failure(self):
        """Evidence retained from a prior path (e.g. cache) must not trip it."""
        assert zero_evidence_failure({"search_calls": 0, "chars_retained": 4000}) is None

    def test_calls_with_zero_chars_is_allowed_to_stand(self):
        """Searches ran and found nothing: recorded honestly; the deliverable
        contract (T-14) owns whether that is shippable — this gate only owns
        the instrumented-zero state."""
        assert zero_evidence_failure({"search_calls": 7, "chars_retained": 0}) is None

    def test_missing_keys_are_safe(self):
        assert zero_evidence_failure({}) is not None
