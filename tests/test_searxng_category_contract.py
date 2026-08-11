"""OVERHAUL2 S13: every profile-named category sent by the client/preflight
must be declared by at least one engine in that profile's settings file.

Regression guard for D3 (B-2): the reference replica's settings declared no
engine in the ``reference`` category, so SearXNG rejected every
``categories=reference`` request with HTTP 400 before any engine ran — the
whole reference source class was dead by config, not by network, and the
preflight's ``reference=0d/0e`` was the honest symptom.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from hyperion.agents.support.corpus_preflight import _CANARY_CATEGORIES

ROOT = Path(__file__).resolve().parents[1]

_PROFILE_FILES = {
    "web": "searxng_settings.web.yml",
    "scholar": "searxng_settings.scholar.yml",
    "reference": "searxng_settings.reference.yml",
}
# Categories the client can send to each profile: the SearxngPool
# CATEGORY_PROFILE mapping plus the preflight canary categories and the
# "general" fallback always sent on cross-profile fan-out.
_PROFILE_CATEGORIES = {
    "web": {"general", "news"},
    "scholar": {"science", "medical", "general"},
    "reference": {"reference", "it", "geo", "general"},
}


def _declared_categories(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    declared: set[str] = set()
    for engine in cfg.get("engines", []):
        cats = engine.get("categories")
        if cats is None:
            declared.add("general")
        elif isinstance(cats, str):
            declared.add(cats)
        else:
            declared.update(cats)
    return declared


def test_every_profile_accepts_the_categories_we_send() -> None:
    for profile, path in _PROFILE_FILES.items():
        declared = _declared_categories(str(ROOT / path))
        for cat in _PROFILE_CATEGORIES[profile]:
            assert cat in declared, (
                f"{path}: no engine declares category {cat!r} "
                f"(declared={sorted(declared)})"
            )


def test_canary_categories_match_profiles() -> None:
    assert set(_CANARY_CATEGORIES) == {"web", "scholar", "reference"}
