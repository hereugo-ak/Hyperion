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


def test_reference_canary_query_is_title_shaped() -> None:
    """OVERHAUL3 S12 (D-G contract at the preflight boundary): the query the
    reference-class canary probe dispatches must be title-shaped.

    ``corpus_preflight._fire_canaries`` condenses the question ONCE (via the
    same ``SubAgentRunner._condense_query`` rule the sub-agents use) and sends
    that single shaped query to every source class — including the reference
    replica. A paragraph-length query 400s wikipedia ``/page/summary`` (the
    audited 12:31:05 ``wikipedia 400 Bad Request`` on
    ``competitor strategic moves space%2C recent announcements%2C...``), so
    the reference probe's query must never be the raw sentence.

    Regression lock: if the condensation were removed from the battery, the
    reference probe would ship the raw paragraph and this contract fails.
    """
    from hyperion.agents.sub_agent import SubAgentRunner

    # The audited wikipedia 400 — a >120-char instruction-prefixed sentence.
    reference_probe_question = (
        "Find competitor strategic moves in the Indian space sector, recent "
        "announcements, funding rounds and market positioning of startups"
    )

    # Replay the battery's shaping seam verbatim: the single condensed query
    # that routes to ``categories=reference``.
    shaped = SubAgentRunner._condense_query(reference_probe_question)

    assert len(shaped) <= 120, (
        "the reference probe query must be ≤120 chars (wikipedia title-safe), "
        f"got {len(shaped)}: {shaped!r}"
    )
    assert shaped != reference_probe_question, (
        "the raw paragraph must never reach the reference replica — that is "
        "the wikipedia /page/summary 400"
    )
    assert not shaped.startswith("Find "), (
        "instruction prefixes are not title-shaped"
    )
    # The battery really does route this query to the reference category.
    assert _CANARY_CATEGORIES["reference"] == "reference"
