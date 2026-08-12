"""OVERHAUL5 W4 (D-06) — paywall pre-classifier + firecrawl load guard.

The 08-12 run burned playwright+fetch on Elsevier/DOI paywalls 20+ times
(SCRAPE_ALL_ENGINES_FAILED) and hammered the single firecrawl worker with
7,244 "Can't accept connection due to RAM/CPU load" rejections. W4: paywall
hosts fail fast with a typed PAYWALL reason (zero tier attempts); the
firecrawl tier processes URLs in waves of <= 2.

Fail-first: the paywall tests fail on pre-W4 code (the ladder climbed all 11
tiers against a paywall host / returned non-PAYWALL errors); the wave-cap
test fails on pre-W4 code (all 4 URLs fired concurrently).
"""

from __future__ import annotations

import asyncio

import pytest

from hyperion.tools.unified_extract import UnifiedExtract, UnifiedExtractResult


@pytest.mark.asyncio
async def test_single_paywall_url_fails_fast() -> None:
    """[FF] doi.org URL -> typed PAYWALL failure with ZERO tier attempts."""
    ex = UnifiedExtract(settings=None)
    result = await ex.extract("https://doi.org/10.1016/j.apenergy.2019.114074")
    assert not result.success
    assert "PAYWALL" in result.error
    assert result.tools_tried == [], "no tier may be attempted against a paywall"


@pytest.mark.asyncio
async def test_ladder_paywall_urls_fail_fast() -> None:
    """[FF] A batch of paywall URLs climbs nothing — typed failures, empty
    tools_tried."""
    ex = UnifiedExtract(settings=None)
    urls = [
        "https://linkinghub.elsevier.com/retrieve/pii/S0957178721001314",
        "https://doi.org/10.1016/s0048-7333(02)00062-8",
        "https://www.mdpi.com/2227-9091/10/12/224",
    ]
    outcome = await ex.extract_ladder(urls)
    assert len(outcome.results) == 3
    assert all("PAYWALL" in r.error for r in outcome.results)
    assert all(r.tools_tried == [] for r in outcome.results)
    assert outcome.tools_tried == [], "the ladder must not climb for a paywall batch"


@pytest.mark.asyncio
async def test_paywall_check_wins_over_js_heavy() -> None:
    """A paywalled JS-heavy host is still a paywall — the paywall profile
    must be decided before the js_heavy hint."""
    ex = UnifiedExtract(settings=None)
    assert ex._classify_url("https://www.taylorfrancis.com/books/9781351433730") == "paywall"


@pytest.mark.asyncio
async def test_firecrawl_tier_waves_at_two() -> None:
    """[FF] The firecrawl tier never fires more than 2 concurrent scrapes —
    the single-worker stack's load guard."""
    ex = UnifiedExtract(settings=None)
    active = 0
    peak = 0
    calls = 0

    async def fake_extract(url: str) -> UnifiedExtractResult:
        nonlocal active, peak, calls
        active += 1
        peak = max(peak, active)
        calls += 1
        await asyncio.sleep(0.02)
        active -= 1
        return UnifiedExtractResult(
            url=url, content="content " * 40, success=True, tool_used="firecrawl"
        )

    def resolver(tier, semaphore, *, extract_tables=True, extract_links=True):
        return fake_extract

    urls = [f"https://site{i}.example.com/page" for i in range(4)]
    outcome = await ex.extract_ladder(
        urls,
        tiers=["firecrawl"],
        tier_resolver=resolver,
        tier_available=lambda t: True,
    )
    assert peak <= 2, f"firecrawl wave must cap at 2 concurrent (peak was {peak})"
    assert calls == 4
    assert len(outcome.results) == 4
