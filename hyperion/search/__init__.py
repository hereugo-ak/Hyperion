"""HYPERION multi-provider search layer (OVERHAUL4 P8 — search-only).

Canonical deterministic fallback chain:

    SearXNG -> You.com -> Exa   (loop the top-3 once)   -> Tavily -> Yep

- Search-only: every adapter returns title+url+snippet. NO contents / answer
  / summarize flags — extraction stays in Hyperion's own ladder.
- Strict fixed order, per-run budget buckets, per-run suspension.
- SearchResult shape is the ground truth for the evidence ledger, dedupe,
  preflight and citation formatting.
"""

from hyperion.search.orchestrator import (
    MAX_RESULTS,
    MIN_RESULTS,
    SearchOrchestrator,
)
from hyperion.search.types import SearchResult

__all__ = [
    "MAX_RESULTS",
    "MIN_RESULTS",
    "SearchOrchestrator",
    "SearchResult",
]
