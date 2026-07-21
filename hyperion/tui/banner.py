"""HYPERION opening banner + roster, rendered as Content lines.

These are written as the FIRST rows inside the transcript scroll surface, so
the logo, tagline and full agent roster live *inside* the single scrollable
document. Scroll to the top at any time and the wordmark is right there — it is
never docked away, never collapsed, never blanked. Everything is Content, so it
is fully selectable and copyable.

The wordmark carries a static clay gradient (soft-clay → clay → deep-clay). We
keep it static rather than per-frame animated so it doesn't shimmer/flicker
while you scroll — calm and premium, not a light show.
"""

from __future__ import annotations

from textual.content import Content

from hyperion.tui.content import build, line, span
from hyperion.tui.motion.color import ramp
from hyperion.tui.roster import GROUP_ORDER, by_group
from hyperion.tui.theme import (
    CLAY,
    LOGO_STOPS,
    SAGE,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    badge_color,
)

# LOCKED wordmark (ANSI Shadow figlet).
WORDMARK = [
    "  ██╗  ██╗██╗   ██╗██████╗ ███████╗██████╗ ██╗ ██████╗ ███╗   ██╗",
    "  ██║  ██║╚██╗ ██╔╝██╔══██╗██╔════╝██╔══██╗██║██╔═══██╗████╗  ██║",
    "  ███████║ ╚████╔╝ ██████╔╝█████╗  ██████╔╝██║██║   ██║██╔██╗ ██║",
    "  ██╔══██║  ╚██╔╝  ██╔═══╝ ██╔══╝  ██╔══██╗██║██║   ██║██║╚██╗██║",
    "  ██║  ██║   ██║   ██║     ███████╗██║  ██║██║╚██████╔╝██║ ╚████║",
    "  ╚═╝  ╚═╝   ╚═╝   ╚═╝     ╚══════╝╚═╝  ╚═╝╚═╝ ╚═════╝ ╚═╝  ╚═══╝",
]
_LOGO_WIDTH = max(len(s) for s in WORDMARK)

TAGLINE = "Multi-Agent Consulting Intelligence"
SUBTAGLINE = "orchestration · reasoning · synthesis"


def _wordmark_lines() -> list[list]:
    """The gradient wordmark as styled spans (static clay ramp)."""
    lines: list[list] = []
    for s in WORDMARK:
        row = []
        for x, ch in enumerate(s):
            if ch == " ":
                row.append(span(" ", ""))
            else:
                color = ramp(LOGO_STOPS, x / max(1, _LOGO_WIDTH - 1))
                row.append(span(ch, color))
        lines.append(row)
    return lines


def logo_content() -> Content:
    """Wordmark + tagline block for the top of the scroll document."""
    lines = _wordmark_lines()
    lines.append(line(""))
    lines.append(line(span("  " + TAGLINE, f"bold {TEXT_SECONDARY}")))
    lines.append(line(span("  " + SUBTAGLINE, TEXT_DIM)))
    return build(lines)


def roster_content(online: int | None = None) -> Content:
    """The full agent roster grouped by function, with each agent's ability.

    This is the 'show all information about all the agents and its ability'
    panel — always present at the top of the session so you can see the whole
    cast before and during an engagement.
    """
    groups = by_group()
    total = sum(len(v) for v in groups.values())
    head = f"  ROSTER · {total} specialist agents"
    if online is not None:
        head += f" · {online} online"

    lines: list[list] = []
    lines.append(line(span(head, f"bold {TEXT_PRIMARY}")))
    lines.append(line(""))

    for grp in GROUP_ORDER:
        members = groups.get(grp) or []
        if not members:
            continue
        lines.append(line(span("  " + grp, f"bold {TEXT_DIM}")))
        for a in members:
            bc = badge_color(a.badge)
            # badge column is 12 wide (longest badge "REGULATORY" = 10 + 2 gap);
            # name column 22; ability follows and is allowed to wrap naturally.
            lines.append(
                [
                    span("    ", ""),
                    span(a.badge.ljust(12)[:12], f"bold {bc}"),
                    span(a.name.ljust(22)[:22], TEXT_SECONDARY),
                    span(a.ability, TEXT_DIM),
                ]
            )
        lines.append(line(""))
    # trim trailing blank
    if lines and lines[-1] == line(""):
        lines.pop()
    return build(lines)


def hint_content() -> Content:
    """A one-liner nudging the user how to start."""
    return build(
        [
            [
                span("  ask a question to begin  ", TEXT_SECONDARY),
                span("· ", TEXT_GHOST),
                span("try  ", TEXT_DIM),
                span("should India enter the EV market?", f"italic {CLAY}"),
            ],
            [
                span("  commands  ", TEXT_DIM),
                span("/agents /providers /demo /clear /help", TEXT_GHOST),
                span("   copy  ", TEXT_DIM),
                span("drag + Ctrl+Shift+C", TEXT_GHOST),
            ],
        ]
    )
