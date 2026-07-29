"""HYPERION findings stream — live feed of agent findings.

Shows findings as agents publish them to the AgentBus:
    - Agent name (color-coded by tier)
    - Finding summary (first 2-3 lines)
    - Source count and confidence level
    - Updates in real-time
    - Scrollable — user can scroll back to earlier findings
    - Clickable — user can expand a finding to see full content

Per ARCHITECTURE.md §8.7.

Implementation: a specialized Transcript that only accepts finding entries,
keeping them separate from the main event log for focused review.

Selection
---------
This widget subclasses :class:`~hyperion.tui.widgets.transcript.Transcript`
rather than Textual's ``RichLog``, and that choice is load-bearing rather than
cosmetic. ``RichLog.render_line`` never calls ``Strip.apply_offsets``, so the
compositor cannot tell which *character* the pointer is over; the screen then
falls through to its ``SELECT_ALL`` branch and every drag — however small —
selects the entire widget, with no anchor to extend from when the pointer runs
past an edge. Inheriting from ``Transcript`` gives the findings feed the same
precise, scroll-past-the-edge selection as the main event log for free.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from hyperion.tui.content import build, span
from hyperion.tui.theme import (
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    badge_color,
)
from hyperion.tui.widgets.transcript import Transcript


@dataclass
class FindingEntry:
    """One finding in the stream."""
    agent: str
    badge: str
    title: str
    snippet: str
    sources: int = 0
    confidence: str = ""
    ts: float = field(default_factory=time.time)
    expanded: bool = False


class FindingsStream(Transcript):
    """Live, scrollable findings feed — separate from the main event log.

    Each finding is displayed as:
        [HH:MM:SS]  BADGE   Finding title — snippet...
                           sources: 4 · confidence: HIGH
    """

    DEFAULT_CSS = """
    FindingsStream {
        scrollbar-size: 1 1;
        scrollbar-color: #4A4640;
        scrollbar-color-hover: #d97757;
        scrollbar-background: #141413;
        background: #141413;
        padding: 0 2;
        overflow-y: scroll;
    }
    """

    def __init__(self, **kwargs) -> None:
        # Transcript owns its own line buffer and wrapping, so the RichLog-only
        # knobs (markup / highlight / wrap) no longer mean anything and must not
        # be forwarded — passing them through would raise TypeError. They are
        # dropped rather than rejected so existing call sites keep working.
        kwargs.pop("markup", None)
        kwargs.pop("highlight", None)
        kwargs.pop("wrap", None)
        kwargs.setdefault("auto_scroll", True)
        super().__init__(**kwargs)
        self._findings: list[FindingEntry] = []

    def add_finding(
        self,
        agent: str,
        badge: str,
        title: str,
        snippet: str = "",
        sources: int = 0,
        confidence: str = "",
    ) -> None:
        """Add a finding to the stream."""
        entry = FindingEntry(
            agent=agent,
            badge=badge,
            title=title[:120],
            snippet=snippet[:200],
            sources=sources,
            confidence=confidence,
        )
        self._findings.append(entry)
        self._write_finding(entry)

    def clear(self) -> FindingsStream:  # type: ignore[override]
        self._findings.clear()
        super().clear()
        return self

    def get_findings(self) -> list[FindingEntry]:
        return list(self._findings)

    def _write_finding(self, entry: FindingEntry) -> None:
        ts = time.strftime("[%H:%M:%S]", time.localtime(entry.ts))
        bc = badge_color(entry.badge)

        spans = [
            span(ts + "  ", TEXT_DIM),
            span(entry.badge[:10].ljust(10), f"bold {bc}"),
            span(entry.title, TEXT_PRIMARY),
        ]

        if entry.snippet:
            spans.append(span(f" — {entry.snippet}", TEXT_DIM))

        # Detail line
        detail_parts = []
        if entry.sources > 0:
            detail_parts.append(f"sources: {entry.sources}")
        if entry.confidence:
            detail_parts.append(f"confidence: {entry.confidence}")

        lines = [spans]
        if detail_parts:
            detail = " · ".join(detail_parts)
            lines.append([
                span("              ", TEXT_GHOST),
                span(detail, TEXT_DIM),
            ])

        self.write_block(build(lines))
        if self.auto_scroll:
            self.scroll_end(animate=False)
