"""
HYPERION structured event emitter — the observability spine.

Every significant pipeline event (LLM call, search, extraction, agent
transition, escalation) emits a structured event via ``trace()``.
Sinks receive these events in real-time:

- ``file_sink`` writes JSONL to ``reports/<engagement_id>/trace.jsonl``
- TUI sink renders a compact rolling view + status strip
- Future sinks: metrics aggregator, distributed trace exporter

This is NOT logging. Logging is for debugging. Tracing is for
observability — structured, typed events that feed dashboards,
regression gates, and cost/latency ledgers. (§II.9, D10)

Usage::

    from hyperion.obs import trace, add_sink, file_sink

    # At engagement start:
    add_sink(file_sink(engagement_id))

    # Anywhere in the pipeline:
    trace("llm", agent="synthesis_lead", tier="deep", provider="google",
          model="gemini-3.1-flash-lite", status="OK", took_ms=4200,
          prompt_tokens=12000, completion_tokens=3000)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

_LOCK = threading.Lock()
_SINKS: list[Callable[[dict[str, Any]], None]] = []


def add_sink(fn: Callable[[dict[str, Any]], None]) -> None:
    """Register a sink callable that receives every trace event."""
    with _LOCK:
        _SINKS.append(fn)


def remove_sink(fn: Callable[[dict[str, Any]], None]) -> None:
    """Remove a previously registered sink."""
    with _LOCK:
        if fn in _SINKS:
            _SINKS.remove(fn)


def clear_sinks() -> None:
    """Remove all sinks — used in tests."""
    with _LOCK:
        _SINKS.clear()


def trace(stage: str, **fields: Any) -> None:
    """Emit a structured trace event.

    Args:
        stage: Event stage/category (e.g. "llm", "search", "extract",
               "agent", "escalation", "render").
        **fields: Arbitrary structured fields — provider, model, agent,
                  status, took_ms, tokens, url, etc.

    The event is a flat dict with a timestamp, stage, and all fields.
    Each sink receives the event dict; sink errors are swallowed so
    a broken sink never crashes the pipeline.
    """
    ev: dict[str, Any] = {
        "t": round(time.time(), 3),
        "stage": stage,
        **fields,
    }
    with _LOCK:
        sinks = list(_SINKS)
    for fn in sinks:
        try:
            fn(ev)
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            # A dead trace sink means telemetry is silently incomplete —
            # degraded observability, not lost engagement data. Debug level.
            logger.debug(
                "trace sink %r raised and event was dropped: %s: %s",
                getattr(fn, "__name__", fn), type(exc).__name__, exc,
            )


def file_sink(engagement_id: str) -> Callable[[dict[str, Any]], None]:
    """Create a file-based sink that writes JSONL to reports/<id>/trace.jsonl.

    Returns a callable suitable for ``add_sink()``. The file is opened
    in append mode on each event (safe across crashes, no file handle leak).
    """
    path = os.path.join("reports", engagement_id, "trace.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)

    def _write(ev: dict[str, Any]) -> None:
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, default=str) + "\n")

    return _write
