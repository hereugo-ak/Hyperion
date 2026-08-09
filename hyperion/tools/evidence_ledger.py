"""
HYPERION Evidence Ledger — Phase 0 of the overhaul (overhaul.md §6 P0).

The single architectural defect the Aug-10 audit identified: **evidence is not
a first-class object anywhere in the system.** Search results were flattened
into prompt text, the LLM was asked to re-mint sources from that text, and the
corpus floor — the one gate that counts evidence — only fired at the very end
of a 39-minute, 775k-token run.

This module makes evidence a first-class object:

* ``Evidence`` — one record per retrieved URL (domain, title, snippet,
  content_hash, engine/tool, profile, stage, fetched_at). It exists BEFORE any
  LLM sees anything.
* ``EvidenceLedger`` — the run-scoped, thread-safe ledger: ``record()``,
  ``distinct_domains()``, ``by_stage()``, ``by_engine()``, ``summary()`` and a
  ``snapshot()`` into ``reports/diagnostics/`` so the run autopsy is
  reproducible from telemetry alone (P0 exit gate).
* ContextVar-scoped access — ``new_ledger(run_id)`` opens a ledger for the
  engagement and every child asyncio task inherits it via ``get_evidence_ledger()``.
  Outside an engagement (standalone tools, tests) a module-level fallback
  ledger is used so wiring can never crash on a missing context.

KPI-1 (time-to-first-evidence) and KPI-2 (distinct domains before synthesis)
are read straight off this ledger in later phases.

Notes for later phases:

* **First-sighting wins.** A URL discovered via search and later extracted
  keeps its discovery ``stage``/``engine``; the extraction visit only attaches
  the ``content_hash``. ``by_stage("extraction")`` therefore counts URLs the
  ladder saw *before* any search did, while corpus depth is measured by
  ``content_hash`` presence. Don't read the stage split as a URL census.
* **The default ledger is a scratch buffer.** Outside an engagement (boot
  smoke, standalone tool calls) records land in the module-level
  ``_DEFAULT_LEDGER`` and are never cleared — it exists so wiring can never
  crash, not as a store. Only the per-run ledger is snapshotted.
* **ContextVar propagation is asyncio-scoped.** ``record_evidence`` inside a
  thread (``asyncio.to_thread``, ``run_in_executor``) would silently fall
  back to the default ledger — keep evidence wiring on the async path.
"""

from __future__ import annotations

import contextvars
import hashlib
import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


def domain_of(url: str) -> str:
    """Extract a normalized registrable-ish host from a URL.

    Lower-cased, leading ``www.`` stripped, empty for unparseable input.
    """
    if not url or not isinstance(url, str):
        return ""
    try:
        host = urlparse(url.strip()).netloc.lower()
    except ValueError:
        return ""
    if not host:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def content_hash_of(text: str) -> str:
    """Stable short SHA-256 fingerprint of a content blob."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _url_hash(url: str) -> str:
    """Stable fingerprint used to deduplicate evidence by URL."""
    return hashlib.sha256(url.strip().encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class Evidence:
    """One retrieved URL, recorded before any LLM sees it (invariant I-1)."""

    url: str
    domain: str
    title: str = ""
    snippet: str = ""
    content_hash: str = ""
    #: engine/tool that produced this record, e.g. ``searxng``, ``mojeek``,
    #: ``jina``, ``unified_extract``, ``grounding``.
    engine: str = ""
    #: SearXNG replica profile (web/scholar/reference) or tool class.
    profile: str = ""
    #: Pipeline stage: ``discovery`` (search), ``extraction`` (ladder),
    #: ``grounding`` (Gemini Search grounding), ...
    stage: str = "discovery"
    fetched_at: float = field(default_factory=time.time)
    run_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "domain": self.domain,
            "title": self.title,
            "snippet": self.snippet,
            "content_hash": self.content_hash,
            "engine": self.engine,
            "profile": self.profile,
            "stage": self.stage,
            "fetched_at": self.fetched_at,
            "run_id": self.run_id,
        }


class EvidenceLedger:
    """Run-scoped ledger of every retrieved URL.

    One ``Evidence`` record per unique URL: a second ``record()`` of the same
    URL enriches the first (fills an empty ``content_hash``) instead of
    duplicating it, so ``distinct_domains()`` and per-engine counters stay
    honest no matter how many pipeline stages touch the same URL.
    """

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self._lock = threading.Lock()
        self._items: dict[str, Evidence] = {}
        self._created_at = time.time()

    # ── writes ─────────────

    def record(
        self,
        *,
        url: str,
        title: str = "",
        snippet: str = "",
        content_hash: str = "",
        engine: str = "",
        profile: str = "",
        stage: str = "discovery",
    ) -> Evidence | None:
        """Record one retrieved URL. Idempotent per URL; never raises."""
        if not url or not isinstance(url, str):
            return None
        url = url.strip()
        if not url:
            return None
        key = _url_hash(url)
        now = time.time()
        with self._lock:
            existing = self._items.get(key)
            if existing is not None:
                # First sighting wins for engine/profile/stage; only enrich a
                # missing content fingerprint (extraction visiting a URL the
                # discovery layer already recorded).
                if existing.content_hash or not content_hash:
                    return existing
                merged = Evidence(
                    url=existing.url,
                    domain=existing.domain,
                    title=existing.title or title,
                    snippet=existing.snippet or snippet,
                    content_hash=content_hash,
                    engine=existing.engine,
                    profile=existing.profile,
                    stage=existing.stage,
                    fetched_at=existing.fetched_at,
                    run_id=existing.run_id,
                )
                self._items[key] = merged
                return merged
            evidence = Evidence(
                url=url,
                domain=domain_of(url),
                title=title,
                snippet=snippet,
                content_hash=content_hash,
                engine=engine,
                profile=profile,
                stage=stage,
                fetched_at=now,
                run_id=self.run_id,
            )
            self._items[key] = evidence
            return evidence

    # ── reads ───────────────

    def all(self) -> list[Evidence]:
        """All evidence records (insertion order preserved)."""
        with self._lock:
            return list(self._items.values())

    def count(self) -> int:
        """Number of unique evidence records (unique URLs)."""
        with self._lock:
            return len(self._items)

    def distinct_domains(self) -> set[str]:
        """Unique non-empty domains across the ledger (KPI-2 metric)."""
        with self._lock:
            return {e.domain for e in self._items.values() if e.domain}

    def by_stage(self, stage: str) -> list[Evidence]:
        """Evidence recorded at a given pipeline stage."""
        with self._lock:
            return [e for e in self._items.values() if e.stage == stage]

    def by_engine(self) -> dict[str, int]:
        """Per-engine/tool record counts (first-sighting attribution)."""
        counts: dict[str, int] = {}
        with self._lock:
            for e in self._items.values():
                engine = e.engine or "unknown"
                counts[engine] = counts.get(engine, 0) + 1
        return counts

    def first_fetched_at(self) -> float | None:
        """Timestamp of the first evidence record — KPI-1 (time-to-first-evidence)."""
        with self._lock:
            if not self._items:
                return None
            return min(e.fetched_at for e in self._items.values())

    def summary(self) -> dict[str, Any]:
        """Compact KPI-oriented snapshot for manifests, TUI and telemetry."""
        records = self.all()
        stages = sorted({e.stage for e in records})
        domains = sorted(self.distinct_domains())
        return {
            "run_id": self.run_id,
            "evidence_items": len(records),
            "distinct_domains": len(domains),
            "domains": domains,
            "by_engine": self.by_engine(),
            "by_stage": {
                stage: sum(1 for e in records if e.stage == stage) for stage in stages
            },
            "first_fetched_at": self.first_fetched_at(),
        }

    def to_dict(self) -> dict[str, Any]:
        """Full serializable snapshot (all records + summary)."""
        return {
            "run_id": self.run_id,
            "created_at": self._created_at,
            "summary": self.summary(),
            "records": [e.to_dict() for e in self.all()],
        }

    def snapshot(self, path: str | object) -> str:
        """Persist the ledger to a JSON file; returns the path written.

        Creates parent directories as needed. Never raises — a snapshot
        failure must not break the run it is describing.
        """
        try:
            directory = os.path.dirname(str(path))
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(str(path), "w", encoding="utf-8") as f:
                json.dump(self.to_dict(), f, indent=2, default=str)
            return str(path)
        except OSError as exc:  # pragma: no cover - filesystem edge
            logger.warning("evidence ledger snapshot failed to %s: %s", path, exc)
            return ""


# ───────────────────────────────────
# Run-scoped access. The active ledger lives in a ContextVar so every child
# asyncio task spawned inside an engagement inherits it without threading an
# explicit parameter through 40 search call sites.
# ───────────────────────────────────

_active: contextvars.ContextVar[EvidenceLedger | None] = contextvars.ContextVar(
    "hyperion_evidence_ledger", default=None
)

#: Fallback ledger used outside an engagement (standalone tools, tests). Wiring
#: must never crash on a missing run context.
_DEFAULT_LEDGER = EvidenceLedger(run_id="__default__")


def get_evidence_ledger() -> EvidenceLedger:
    """The active engagement ledger, or the module-level fallback."""
    return _active.get() or _DEFAULT_LEDGER


def set_active_ledger(ledger: EvidenceLedger | None) -> None:
    """Bind a ledger to the current context (used by tests)."""
    _active.set(ledger)


def reset_active_ledger() -> None:
    """Unbind the active ledger — next engagement opens its own."""
    _active.set(None)


def new_ledger(run_id: str) -> EvidenceLedger:
    """Open a fresh run-scoped ledger and make it active for this context."""
    ledger = EvidenceLedger(run_id=run_id)
    set_active_ledger(ledger)
    return ledger


def record_evidence(**fields: Any) -> Evidence | None:
    """Record one Evidence record against the active ledger (never raises).

    Mirrors ``hyperion.obs.trace()`` in shape so search/extraction call sites
    can emit evidence with a single line, e.g.::

        record_evidence(
            url=result.url,
            title=result.title,
            snippet=result.snippet,
            engine=result.engine,
            profile=endpoint.profile,
            stage="discovery",
        )
    """
    try:
        return get_evidence_ledger().record(**fields)
    except Exception as exc:  # noqa: BLE001 - evidence must not break retrieval
        logger.debug("evidence record failed: %s", exc)
        return None
