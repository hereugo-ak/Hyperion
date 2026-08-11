"""
HYPERION Corpus Contract preflight — overhaul Phase 2 (overhaul.md §6 P2).

The system must decide *whether it can research* before it spends a token on
research. The Aug-10 run burned 39 minutes and 775.7k tokens discovering that
the retrieval fleet was dead — the corpus floor, the one component that
counts evidence, was the FIRST to notice, at the very end.

This module fixes the ordering: at engagement start it fires a small fixed
battery of canary probes (one per source class: web, scholar, reference),
reads the run-scoped Evidence Ledger (P0), and computes a ``CorpusContract``:

    GREEN  full DAG          — >= ``min_domains`` distinct domains retrieved
    AMBER  reduced DAG       — some evidence, below the domain floor
    RED    terminal          — zero evidence anywhere (INSUFFICIENT_EVIDENCE)

A RED contract raises ``CorpusPreflightError``: the orchestrator terminates
the engagement in seconds with a typed diagnostic instead of running a DAG
over the string "No raw data available from tools." (overhaul §2 A-4/A-9).

The canary probes are the corpus probe that P1.5 deliberately removed from
boot — readiness is local, corpus truth is measured here.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)

#: The source classes probed by the canary battery, mapped to the SearXNG
#: category that routes to the owning replica.
SOURCE_CLASSES: tuple[str, ...] = ("web", "scholar", "reference")
_CANARY_CATEGORIES: dict[str, str] = {
    "web": "general",
    "scholar": "science",
    "reference": "reference",
}
#: Wall-clock bound for the whole battery (overhaul KPI-4: a degraded run
#: terminates < 60s). A battery that stalls on a sick-but-alive stack degrades
#: to RED instead of hanging engagement start.
_CANARY_TIMEOUT_SECONDS = 40.0


class CorpusStatus(str, Enum):
    """The three engagement postures the contract can license."""

    GREEN = "green"   # full DAG
    AMBER = "amber"   # reduced DAG (retrieval-first, smaller budget)
    RED = "red"       # terminal — INSUFFICIENT_EVIDENCE


class CorpusPreflightError(RuntimeError):
    """Raised when the corpus contract is RED — the engagement cannot be grounded."""


@dataclass
class SourceClassProbe:
    """One canary probe's outcome for one source class."""

    source_class: str
    ok: bool
    evidence_items: int = 0
    distinct_domains: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_class": self.source_class,
            "ok": self.ok,
            "evidence_items": self.evidence_items,
            "distinct_domains": self.distinct_domains,
            "detail": self.detail,
        }


@dataclass
class CorpusContract:
    """The preflight verdict: can the engagement be grounded, and at what depth."""

    status: CorpusStatus
    min_domains: int
    distinct_domains: int = 0
    evidence_items: int = 0
    per_class: list[SourceClassProbe] = field(default_factory=list)
    detail: str = ""
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "min_domains": self.min_domains,
            "distinct_domains": self.distinct_domains,
            "evidence_items": self.evidence_items,
            "per_class": [p.to_dict() for p in self.per_class],
            "detail": self.detail,
            "elapsed_seconds": self.elapsed_seconds,
        }

    def snapshot(self, path: str | object) -> str:
        """Persist the contract as the typed diagnostic; never raises."""
        try:
            directory = os.path.dirname(str(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(str(path), "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, default=str)
            return str(path)
        except OSError as exc:  # pragma: no cover - filesystem edge
            logger.warning("corpus contract snapshot failed to %s: %s", path, exc)
            return ""


async def _fire_canaries(question: str, settings: Any) -> None:
    """One small search per source class; results land in the Evidence Ledger.

    ``num_results=5`` keeps the battery cheap; the ledger (P0 wiring in
    ``searxng.py``) records every retrieved URL with its replica profile, so
    the contract is computed from structured evidence, never from log lines.

    The query is CONDENSED (same rule the sub-agents use): a raw paragraph-
    length user question frequently returns zero keyword results even on a
    healthy fleet, which would false-RED a perfectly good stack.
    """
    from hyperion.agents.sub_agent import SubAgentRunner
    from hyperion.tools.searxng import SearxNGClient

    query = SubAgentRunner._condense_query(question)
    client = SearxNGClient(settings=settings)
    # OVERHAUL2 S7: tag canary evidence so mid-run gates can exclude it.
    from hyperion.tools.evidence_ledger import get_evidence_ledger
    _ledger = get_evidence_ledger()
    _before = {e.url for e in _ledger.all()}
    try:
        for source_class in SOURCE_CLASSES:
            try:
                await client.search(
                    query=query,
                    num_results=5,
                    categories=_CANARY_CATEGORIES[source_class],
                )
            except Exception as exc:  # noqa: BLE001 - one class must not kill the battery
                logger.warning(
                    "CORPUS PREFLIGHT: %s canary probe failed: %s",
                    source_class,
                    exc,
                )
    finally:
        try:
            await client.close()
        except Exception as exc:  # noqa: BLE001 - closing must not mask a verdict
            logger.debug("corpus preflight: search client close failed: %s", exc)
        _new_urls = {e.url for e in _ledger.all()} - _before
        if _new_urls:
            _ledger.retag_stage(urls=_new_urls, stage="preflight")


def _evaluate_contract(
    records: list[Any],
    *,
    min_domains: int,
    elapsed_seconds: float,
) -> CorpusContract:
    """Compute the contract from ledger evidence records (pure function).

    ``records`` are ``Evidence`` objects; per-class attribution uses the
    replica ``profile`` recorded at retrieval time (P0 wiring).
    """
    per_class: list[SourceClassProbe] = []
    all_domains: set[str] = set()
    for source_class in SOURCE_CLASSES:
        class_records = [e for e in records if e.profile == source_class]
        class_domains = {e.domain for e in class_records if e.domain}
        all_domains.update(class_domains)
        per_class.append(
            SourceClassProbe(
                source_class=source_class,
                ok=bool(class_records),
                evidence_items=len(class_records),
                distinct_domains=len(class_domains),
            )
        )

    evidence_items = len(records)
    distinct_domains = len(all_domains)

    # OVERHAUL2 S6: a fleet with a dead source class is NOT green. The
    # 17:13 run went GREEN on scholar alone (web=0d, reference=0d) and
    # fanned out a full 16-task DAG over two dead classes. GREEN requires
    # the total floor AND a per-class pulse; any dead class degrades to
    # AMBER (reduced DAG + that class's queries rerouted to living classes).
    _PER_CLASS_MIN_DOMAINS = {"web": 1, "scholar": 2, "reference": 1}
    dead_classes = [
        p.source_class
        for p in per_class
        if p.distinct_domains < _PER_CLASS_MIN_DOMAINS.get(p.source_class, 1)
    ]
    if distinct_domains == 0:
        status = CorpusStatus.RED
    elif distinct_domains >= min_domains and not dead_classes:
        status = CorpusStatus.GREEN
    else:
        status = CorpusStatus.AMBER

    summary = "; ".join(
        f"{p.source_class}={p.distinct_domains}d/{p.evidence_items}e" for p in per_class
    )
    if status is CorpusStatus.RED:
        detail = (
            f"zero evidence from every source class ({summary}) — the retrieval "
            "fleet produced no domains; an engagement now can only produce "
            "ungrounded output."
        )
    elif status is CorpusStatus.AMBER:
        _dead_note = f"dead/thin classes: {dead_classes}" if dead_classes else ""
        detail = (
            f"partial corpus ({distinct_domains}/{min_domains} domains, {evidence_items} "
            f"items; {summary}) — running a reduced DAG. {_dead_note}".rstrip()
        )
    else:
        detail = (
            f"corpus contract met ({distinct_domains}/{min_domains} domains, "
            f"{evidence_items} items; {summary})."
        )

    return CorpusContract(
        status=status,
        min_domains=min_domains,
        distinct_domains=distinct_domains,
        evidence_items=evidence_items,
        per_class=per_class,
        detail=detail,
        elapsed_seconds=elapsed_seconds,
    )


async def run_corpus_preflight(
    question: str,
    settings: Any | None = None,
    *,
    run_id: str = "",
    min_domains: int = 8,
) -> CorpusContract:
    """Fire the canary battery, read the ledger, and return the contract.

    Raises ``CorpusPreflightError`` when RED so the orchestrator's existing
    failure path records the typed ``INSUFFICIENT_EVIDENCE`` terminal state.
    Snapshot + trace are emitted here (when ``run_id`` is given) so the
    diagnostic exists even if the orchestrator crashes between preflight and
    manifest save.
    """
    import asyncio

    from hyperion.config import get_settings
    from hyperion.tools.evidence_ledger import get_evidence_ledger

    settings = settings or get_settings()
    ledger = get_evidence_ledger()
    started = time.time()

    # The battery is wall-clock bounded (KPI-4): a half-dead fleet that makes
    # upstreams hang degrades to RED fast instead of stalling engagement start.
    try:
        await asyncio.wait_for(
            _fire_canaries(question, settings),
            timeout=_CANARY_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.error(
            "CORPUS PREFLIGHT: canary battery exceeded %.0fs — treating the "
            "corpus as empty (conservative RED)",
            _CANARY_TIMEOUT_SECONDS,
        )

    contract = _evaluate_contract(
        ledger.all(),
        min_domains=min_domains,
        elapsed_seconds=time.time() - started,
    )

    try:
        from hyperion.obs import trace

        trace(
            "corpus_preflight",
            run_id=run_id,
            status=contract.status.value,
            domains=contract.distinct_domains,
            evidence_items=contract.evidence_items,
            min_domains=contract.min_domains,
            detail=contract.detail,
        )
    except Exception as exc:  # noqa: BLE001 - telemetry must not break the verdict
        logger.debug("corpus preflight trace emission failed: %s", exc)

    if run_id:
        from hyperion.infra.paths import project_file

        contract.snapshot(
            project_file("reports", "diagnostics", run_id, "corpus_preflight.json")
        )

    if contract.status is CorpusStatus.RED:
        raise CorpusPreflightError(
            "INSUFFICIENT_EVIDENCE: " + contract.detail
        )
    return contract
