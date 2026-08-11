"""Overhaul Phase 1 (2026-08-10) — capacity-preservation regression gates.

The operator chose to harden the existing stack rather than add a third-party
keyed search API. These gates pin the changes that make the current stack
survive the Aug-10 failure mode (a pre-banned fleet):

- P1.2: the banned scrapers (mojeek/yep) are out of every ACTIVE code path
  and disabled in the SearXNG configuration. They stay *declared* in the
  W-12 replica registry so profile disjointness holds, but no replica may
  ever receive traffic for them again.
- P1.3: the polite pool carries a real contact (HYPERION_CONTACT_EMAIL),
  which raises OpenAlex-class rate ceilings vs. the anonymous bucket.
- P1.6: cooldowns are capped at 4h and the boot-time TTL sweep drops stale
  suspensions from earlier sessions.
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
import yaml

from hyperion.tools.engine_health import EngineHealthTracker
from hyperion.tools.openalex import OpenAlexClient
from hyperion.tools.searxng import (
    PROFILE_FALLBACK_ENGINES,
    RELIABLE_ENGINES,
    STANDBY_ENGINES,
    referenced_engines,
)

ROOT = Path(__file__).resolve().parents[1]
BANNED = ("mojeek", "yep")
# OVERHAUL4 P6.2: marginalia/wiby added to the web class — keyless,
# IP-tolerant engines (unlike the crawler class) to end web=0d/0e.
ACTIVE_WEB = ("mwmbl", "brave", "marginalia", "wiby")


def _load_yml(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_code_never_requests_banned_scrapers() -> None:
    """P1.2: no active engine set may contain mojeek/yep."""
    assert not referenced_engines().intersection(BANNED)
    active_blob = " ".join([
        RELIABLE_ENGINES,
        STANDBY_ENGINES,
        *PROFILE_FALLBACK_ENGINES["web"],
    ])
    assert BANNED[0] not in active_blob
    assert BANNED[1] not in active_blob


def test_web_fallback_is_mwmbl_brave_only() -> None:
    """P1.2: the web corpus is mwmbl + brave behind the health circuit."""
    assert PROFILE_FALLBACK_ENGINES["web"] == set(ACTIVE_WEB)


def test_base_settings_disable_banned_scrapers() -> None:
    """P1.2: base settings ship mojeek/yep disabled, web engines live."""
    base = _load_yml(ROOT / "searxng_settings.yml")
    engines = {e["name"]: e for e in base["engines"]}
    for name in BANNED:
        assert name in engines
        assert engines[name]["disabled"] is True
    for name in ACTIVE_WEB:
        assert engines[name]["disabled"] is False


def test_generated_web_profile_matches_the_pruning_contract() -> None:
    """P1.2 + OVERHAUL4 P6.2: the web replica never feeds a banned scraper
    and ships the tolerant keyless web engines."""
    web = _load_yml(ROOT / "searxng_settings.web.yml")
    engines = {e["name"]: e for e in web["engines"]}
    assert set(engines) == {"mojeek", "mwmbl", "brave", "yep", "marginalia", "wiby"}
    for name in BANNED:
        assert engines[name]["disabled"] is True
    for name in ACTIVE_WEB:
        assert engines[name]["disabled"] is False


def test_upstream_suspensions_are_capped_at_4h(tmp_path, monkeypatch) -> None:
    """P1.6: a 24h upstream ban must not poison the next engagement."""
    # The tracker persists to disk — keep the real vault state untouched.
    monkeypatch.setenv(
        "HYPERION_ENGINE_HEALTH_STATE", str(tmp_path / "engine-health.json")
    )
    tracker = EngineHealthTracker()
    tracker.reset()
    # 86400s (24h) from upstream is clamped to the 4h ceiling.
    tracker.record_response([["mojeek", "HTTP 429 suspended_time=86400"]], [])
    until = tracker._suspended["mojeek"]
    assert until <= time.time() + 4 * 3600 + 1
    # A short suspension is honoured as-is.
    tracker.record_response([["crossref", "HTTP 403 suspended_time=180"]], [])
    assert tracker._suspended["crossref"] <= time.time() + 180 + 1


def test_boot_sweep_drops_stale_cooldowns(tmp_path, monkeypatch) -> None:
    """P1.6: expired persisted state never survives into a fresh process."""
    monkeypatch.setenv(
        "HYPERION_ENGINE_HEALTH_STATE", str(tmp_path / "engine-health.json")
    )
    tracker = EngineHealthTracker()
    tracker.reset()
    # Simulate an earlier session's state that has already aged out.
    tracker._suspended["wikipedia"] = time.time() - 10
    tracker._cooldowns["brave"] = time.time() - 5
    assert tracker.sweep_expired() == 2
    assert "wikipedia" not in tracker._suspended
    assert "brave" not in tracker._cooldowns
    # A live suspension survives the sweep untouched.
    tracker._suspended["crossref"] = time.time() + 60
    assert tracker.sweep_expired() == 0
    assert "crossref" in tracker._suspended


def test_openalex_polite_pool_carries_the_real_contact(monkeypatch) -> None:
    """P1.3: the mailto falls back to HYPERION_CONTACT_EMAIL."""
    monkeypatch.delenv("HYPERION_OPENALEX_EMAIL", raising=False)
    monkeypatch.setenv("HYPERION_CONTACT_EMAIL", "real@example.org")
    client = OpenAlexClient(settings=None)
    assert client._email == "real@example.org"

    class FakeSettings:
        openalex_email = "explicit@example.org"

    assert OpenAlexClient(settings=FakeSettings())._email == "explicit@example.org"
