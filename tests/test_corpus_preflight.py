"""Overhaul Phase 2 (overhaul.md §6 P2) — Corpus Contract preflight tests.

The system must decide whether it CAN research before spending a token on
research. These tests pin the contract classification (GREEN/AMBER/RED),
the typed RED terminal state, per-source-class attribution, and the
diagnostic snapshot.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from hyperion.agents.support.corpus_preflight import (
    CorpusContract,
    CorpusPreflightError,
    CorpusStatus,
    _evaluate_contract,
    run_corpus_preflight,
)
from hyperion.tools.evidence_ledger import (
    new_ledger,
    record_evidence,
    reset_active_ledger,
)


def _settings() -> SimpleNamespace:
    return SimpleNamespace()


def _record(url: str, profile: str) -> None:
    record_evidence(url=url, engine="probe", profile=profile, stage="discovery")


def _stub_canaries(monkeypatch) -> None:
    async def _noop(question, settings):
        return None

    monkeypatch.setattr(
        "hyperion.agents.support.corpus_preflight._fire_canaries", _noop
    )


@pytest.mark.asyncio
async def test_red_zero_evidence_raises_typed_terminal(monkeypatch) -> None:
    """KPI-4: a dead fleet terminates with the typed diagnostic, not a run."""
    reset_active_ledger()
    try:
        new_ledger("eng_red")
        _stub_canaries(monkeypatch)
        with pytest.raises(CorpusPreflightError, match="INSUFFICIENT_EVIDENCE"):
            await run_corpus_preflight(
                "should india build space startups?", settings=_settings()
            )
    finally:
        reset_active_ledger()


@pytest.mark.asyncio
async def test_green_when_domain_floor_met(monkeypatch) -> None:
    reset_active_ledger()
    try:
        new_ledger("eng_green")
        _stub_canaries(monkeypatch)
        for i in range(9):  # 9 distinct domains across all three classes
            profile = "web" if i < 3 else ("scholar" if i < 6 else "reference")
            _record(f"https://d{i}.example/page", profile)
        contract = await run_corpus_preflight(
            "q", settings=_settings(), min_domains=8
        )
        assert contract.status is CorpusStatus.GREEN
        assert contract.distinct_domains == 9
        assert contract.evidence_items == 9
    finally:
        reset_active_ledger()


@pytest.mark.asyncio
async def test_green_at_exact_floor(monkeypatch) -> None:
    reset_active_ledger()
    try:
        new_ledger("eng_floor")
        _stub_canaries(monkeypatch)
        # OVERHAUL2 S6: GREEN at the exact total floor AND every source class
        # alive. Records below each per-class floor would degrade to AMBER —
        # the old test recorded 8 web-only domains and called it GREEN (that
        # is the D4 bug: a 2/3-dead fleet fanned out a full DAG).
        for i in range(8):  # 8 distinct domains across all three classes
            profile = "web" if i < 3 else ("scholar" if i < 6 else "reference")
            _record(f"https://f{i}.example/x", profile)
        contract = await run_corpus_preflight("q", settings=_settings())
        assert contract.status is CorpusStatus.GREEN
    finally:
        reset_active_ledger()


@pytest.mark.asyncio
async def test_dead_source_class_degrades_to_amber(monkeypatch) -> None:
    """OVERHAUL2 S6: a fleet with a dead source class is AMBER, never GREEN.

    The 17:13 run went GREEN on scholar alone (web=0d, reference=0d) and
    fanned out a full 16-task DAG over two dead classes. GREEN now requires
    the total floor AND a per-class pulse; any dead class degrades to AMBER.
    """
    reset_active_ledger()
    try:
        new_ledger("eng_dead_class")
        _stub_canaries(monkeypatch)
        for i in range(8):  # 8 distinct domains but ONLY from scholar
            _record(f"https://s{i}.example/x", "scholar")
        contract = await run_corpus_preflight("q", settings=_settings())
        assert contract.status is CorpusStatus.AMBER
        assert "dead/thin classes: ['web', 'reference']" in contract.detail
    finally:
        reset_active_ledger()


@pytest.mark.asyncio
async def test_amber_when_partial_evidence(monkeypatch) -> None:
    """Some evidence but below the floor → AMBER (reduced DAG), never RED."""
    reset_active_ledger()
    try:
        new_ledger("eng_amber")
        _stub_canaries(monkeypatch)
        for i in range(3):
            _record(f"https://a{i}.example/x", "web")
        contract = await run_corpus_preflight("q", settings=_settings())
        assert contract.status is CorpusStatus.AMBER
        assert contract.distinct_domains == 3
        assert "partial corpus" in contract.detail
    finally:
        reset_active_ledger()


def test_evaluate_contract_attributes_per_source_class() -> None:
    class _E:
        def __init__(self, url: str, profile: str):
            self.url = url
            self.profile = profile
            self.domain = url.split("/")[2]

    records = [
        _E("https://a.com/1", "web"),
        _E("https://a.com/2", "web"),
        _E("https://b.org/3", "scholar"),
        _E("https://c.net/4", "reference"),
    ]
    contract = _evaluate_contract(records, min_domains=8, elapsed_seconds=0.1)
    by_class = {p.source_class: p for p in contract.per_class}
    assert by_class["web"].evidence_items == 2
    assert by_class["web"].distinct_domains == 1
    assert by_class["scholar"].distinct_domains == 1
    assert by_class["reference"].distinct_domains == 1
    assert contract.status is CorpusStatus.AMBER
    assert contract.distinct_domains == 3


def test_contract_snapshot_writes_the_typed_diagnostic(tmp_path) -> None:
    contract = CorpusContract(
        status=CorpusStatus.RED,
        min_domains=8,
        distinct_domains=0,
        evidence_items=0,
        detail="zero evidence from every source class",
    )
    path = tmp_path / "nested" / "corpus_preflight.json"
    written = contract.snapshot(path)
    assert written == str(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["status"] == "red"
    assert data["min_domains"] == 8
    assert "INSUFFICIENT_EVIDENCE" in data["detail"] or "zero evidence" in data["detail"]
