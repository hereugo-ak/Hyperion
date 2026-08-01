"""Unit tests for WaybackClient.get_snapshot / get_snapshots (W-15 wayback repair).

These methods were called by three specialists (competitive_intel,
innovation_analyst, regulatory_analyst) but did not exist on WaybackClient,
so every historical-snapshot call raised AttributeError and was swallowed by
the broad except. The methods are now implemented over the Availability and
CDX APIs; these tests exercise both call shapes with the HTTP layer mocked.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from hyperion.tools.wayback import (
    WaybackAvailabilityResult,
    WaybackClient,
    WaybackSnapshot,
    WaybackTimelineResult,
)


def _client() -> WaybackClient:
    return WaybackClient(settings=None)


def _snapshot(ts: str) -> WaybackSnapshot:
    return WaybackSnapshot(
        url="https://example.com/pricing",
        snapshot_url=f"https://web.archive.org/web/{ts}/https://example.com/pricing",
        timestamp=ts,
        status_code=200,
    )


class TestGetSnapshot:
    async def test_returns_closest_snapshot_for_target_date(self) -> None:
        client = _client()
        availability = WaybackAvailabilityResult(
            url="https://example.com/pricing",
            available=True,
            closest_snapshot=_snapshot("20230715000000"),
            closest_timestamp="20230715000000",
        )
        client.check_availability = AsyncMock(return_value=availability)
        snap = await client.get_snapshot("https://example.com/pricing", years_ago=2)
        assert snap is not None
        assert snap.timestamp == "20230715000000"
        # the target timestamp passed to the API lands ~2 years back
        args, kwargs = client.check_availability.call_args
        assert kwargs["timestamp"].startswith("202")

    async def test_returns_none_when_no_archive(self) -> None:
        client = _client()
        client.check_availability = AsyncMock(
            return_value=WaybackAvailabilityResult(url="u", available=False)
        )
        assert await client.get_snapshot("https://nope.example", years_ago=1) is None

    async def test_negative_years_rejected(self) -> None:
        client = _client()
        with pytest.raises(ValueError):
            await client.get_snapshot("https://example.com", years_ago=-1)


class TestGetSnapshotsIntervals:
    async def test_intervals_shape_deduplicated(self) -> None:
        client = _client()

        async def fake_get_snapshot(url: str, years_ago: int = 1):
            # 1y and 2y resolve to the SAME capture (common for sparse archives)
            return _snapshot("20240101000000") if years_ago <= 2 else _snapshot("20190101000000")

        client.get_snapshot = fake_get_snapshot
        snaps = await client.get_snapshots(
            "https://example.com/pricing", intervals=["1y", "2y", "5y"]
        )
        assert len(snaps) == 2
        assert {s.timestamp for s in snaps} == {"20240101000000", "20190101000000"}

    async def test_unparseable_interval_skipped(self) -> None:
        client = _client()
        client.get_snapshot = AsyncMock(return_value=_snapshot("20240101000000"))
        snaps = await client.get_snapshots("https://example.com", intervals=["bogus", "1y"])
        assert len(snaps) == 1

    async def test_empty_intervals_and_no_years_back_returns_empty(self) -> None:
        client = _client()
        assert await client.get_snapshots("https://example.com") == []


class TestGetSnapshotsYearsBack:
    async def test_years_back_shape_one_per_year_latest_capture(self) -> None:
        client = _client()
        timeline = WaybackTimelineResult(
            url="https://sec.gov/regulations",
            snapshots=[
                _snapshot("20220305000000"),
                _snapshot("20221120000000"),  # later 2022 capture wins
                _snapshot("20230601000000"),
                _snapshot("20240101000000"),
            ],
            total=4,
        )
        client.get_timeline = AsyncMock(return_value=timeline)
        snaps = await client.get_snapshots("https://sec.gov/regulations", years_back=3)
        assert [s.timestamp for s in snaps] == [
            "20221120000000",
            "20230601000000",
            "20240101000000",
        ]
        # one CDX query, not per-interval availability lookups
        client.get_timeline.assert_awaited_once()
        _, kwargs = client.get_timeline.call_args
        assert kwargs["filter_status"] == [200]

    async def test_zero_years_back_rejected(self) -> None:
        client = _client()
        with pytest.raises(ValueError):
            await client.get_snapshots("https://example.com", years_back=0)

    async def test_empty_timeline_returns_empty(self) -> None:
        client = _client()
        client.get_timeline = AsyncMock(
            return_value=WaybackTimelineResult(url="u", snapshots=[], total=0)
        )
        assert await client.get_snapshots("https://example.com", years_back=3) == []
