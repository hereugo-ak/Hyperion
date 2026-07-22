"""
HYPERION RunJournal — durable execution via append-only SQLite event history.

Every DAG step (agent execution) is recorded as a journal entry:
    {step_id, run_id, inputs_hash, status, output_ref, ts}

On restart with the same ``run_id``, the orchestrator replays the journal
and skips steps that already succeeded with the same inputs — resuming
from the frontier of completed work instead of restarting from zero.

This is the proportionate, zero-cost adoption of Temporal-style durable
execution (IV.1.1): no external cluster, just a local SQLite file under
``artifacts/<run_id>/journal.sqlite``.

Usage::

    journal = RunJournal(run_id="eng_abc123")
    journal.open()

    # Before executing a step:
    cached = journal.get_cached(step_id, inputs_hash)
    if cached:
        return load_artifact(cached.output_ref)

    # After successful execution:
    journal.record_success(step_id, inputs_hash, output_ref)

    # After failure:
    journal.record_failure(step_id, inputs_hash, error)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from dataclasses import dataclass
from typing import Any


# ─────────────────────────────────────────────────────────────────────────────
# Data models
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class JournalEntry:
    """A single journal record for one DAG step."""

    step_id: str
    run_id: str
    inputs_hash: str
    status: str  # "success" | "failed" | "running"
    output_ref: str  # artifact path or ""
    error: str  # error message or ""
    ts: float


# ─────────────────────────────────────────────────────────────────────────────
# RunJournal — SQLite append-only event history
# ─────────────────────────────────────────────────────────────────────────────


class RunJournal:
    """Append-only SQLite journal for durable execution.

    The journal lives at ``artifacts/<run_id>/journal.sqlite``. It is
    opened once, kept across the engagement, and closed on shutdown.
    All writes are committed immediately (WAL mode) so a crash never
    loses a completed step.
    """

    def __init__(self, run_id: str, base_dir: str = "artifacts") -> None:
        self.run_id = run_id
        self._dir = os.path.join(base_dir, run_id)
        self._path = os.path.join(self._dir, "journal.sqlite")
        self._conn: sqlite3.Connection | None = None

    def open(self) -> None:
        """Open the journal database, creating tables if needed."""
        os.makedirs(self._dir, exist_ok=True)
        self._conn = sqlite3.connect(self._path, isolation_level=None)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS journal (
                step_id     TEXT NOT NULL,
                run_id      TEXT NOT NULL,
                inputs_hash TEXT NOT NULL,
                status      TEXT NOT NULL,
                output_ref  TEXT NOT NULL DEFAULT '',
                error       TEXT NOT NULL DEFAULT '',
                ts          REAL NOT NULL,
                PRIMARY KEY (step_id, inputs_hash)
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_journal_run
            ON journal(run_id)
        """)

    def close(self) -> None:
        """Close the journal database."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def _ensure_open(self) -> sqlite3.Connection:
        if self._conn is None:
            self.open()
        assert self._conn is not None
        return self._conn

    def compute_inputs_hash(self, inputs: dict[str, Any]) -> str:
        """Compute a deterministic SHA-256 hash of the step inputs."""
        payload = json.dumps(inputs, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def get_cached(self, step_id: str, inputs_hash: str) -> JournalEntry | None:
        """Check if a step already succeeded with the same inputs.

        Returns the journal entry if found and status == "success",
        otherwise None. This is the cache-hit path that lets the
        orchestrator skip re-execution.
        """
        conn = self._ensure_open()
        row = conn.execute(
            "SELECT step_id, run_id, inputs_hash, status, output_ref, error, ts "
            "FROM journal WHERE step_id = ? AND inputs_hash = ? AND status = 'success'",
            (step_id, inputs_hash),
        ).fetchone()
        if row is None:
            return None
        return JournalEntry(*row)

    def record_success(self, step_id: str, inputs_hash: str, output_ref: str) -> None:
        """Record a successful step execution."""
        conn = self._ensure_open()
        conn.execute(
            "INSERT OR REPLACE INTO journal "
            "(step_id, run_id, inputs_hash, status, output_ref, error, ts) "
            "VALUES (?, ?, ?, 'success', ?, '', ?)",
            (step_id, self.run_id, inputs_hash, output_ref, time.time()),
        )

    def record_failure(self, step_id: str, inputs_hash: str, error: str) -> None:
        """Record a failed step execution."""
        conn = self._ensure_open()
        conn.execute(
            "INSERT OR REPLACE INTO journal "
            "(step_id, run_id, inputs_hash, status, output_ref, error, ts) "
            "VALUES (?, ?, ?, 'failed', '', ?, ?)",
            (step_id, self.run_id, inputs_hash, error[:500], time.time()),
        )

    def record_running(self, step_id: str, inputs_hash: str) -> None:
        """Mark a step as running (in-progress)."""
        conn = self._ensure_open()
        conn.execute(
            "INSERT OR REPLACE INTO journal "
            "(step_id, run_id, inputs_hash, status, output_ref, error, ts) "
            "VALUES (?, ?, ?, 'running', '', '', ?)",
            (step_id, self.run_id, inputs_hash, time.time()),
        )

    def get_completed_steps(self) -> list[JournalEntry]:
        """Return all successfully completed steps for this run.

        Used on replay to reconstruct which steps are already done.
        """
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT step_id, run_id, inputs_hash, status, output_ref, error, ts "
            "FROM journal WHERE run_id = ? AND status = 'success' "
            "ORDER BY ts",
            (self.run_id,),
        ).fetchall()
        return [JournalEntry(*row) for row in rows]

    def get_failed_steps(self) -> list[JournalEntry]:
        """Return all failed steps for this run."""
        conn = self._ensure_open()
        rows = conn.execute(
            "SELECT step_id, run_id, inputs_hash, status, output_ref, error, ts "
            "FROM journal WHERE run_id = ? AND status = 'failed' "
            "ORDER BY ts",
            (self.run_id,),
        ).fetchall()
        return [JournalEntry(*row) for row in rows]
