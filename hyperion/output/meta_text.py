"""Meta-text blocklist for quality-iteration output (P2-14).

The quality-iteration prompt hands the LLM the Quality Gate's fix
instructions; the LLM sometimes narrates the instruction instead of
executing it, and the narration was stored as report content in both
fixtures: "the section previously lacked a key insight", "$XB",
"[verified citation]", "[new source for TAM]", and the ⟨...⟩ shape
placeholders from the synthesis prompt's own convention.

Two rules (audit P2-14):
1. Post-validate every iteration output against this blocklist. On match,
   discard the output (the caller retries at a higher tier; on second
   failure it escalates as a gap).
2. Never let an iteration REDUCE information: if the new text is shorter
   AND contains a blocklist token, keep the old text.
"""

from __future__ import annotations

import re

__all__ = ["contains_meta_text", "reject_meta_text", "META_TEXT_PATTERNS"]

# Compiled once. All case-insensitive. Order does not matter.
META_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(p, re.IGNORECASE)
    for p in (
        r"\bpreviously\b",
        r"\bthe section\b",
        r"\bthis section requires\b",
        r"\[[^\]]*citation[^\]]*\]",
        r"\[new source",
        r"\$[XYZ]B\b",
        r"\u27e8",                      # ⟨ shape placeholder
        r"\u27e9",                      # ⟩ shape placeholder
        r"\bparse error\b",
        r"\bplaceholder\b",
    )
)


def contains_meta_text(text: str | None) -> bool:
    """True if the text contains any QA-machinery narration token."""
    if not text:
        return False
    return any(p.search(text) for p in META_TEXT_PATTERNS)


def reject_meta_text(text: str | None, old_text: str | None = None) -> str | None:
    """Return the text if it is clean, else None (or the old text).

    * Clean text passes through unchanged.
    * Meta-text with no ``old_text`` returns None: the caller discards the
      iteration output and retries/escalates.
    * Meta-text that is ALSO shorter than ``old_text`` returns ``old_text``:
      an iteration must never reduce information (P2-14 rule 2).
    """
    if not text:
        return None
    if not contains_meta_text(text):
        return text
    if old_text is not None and len(text) < len(old_text):
        return old_text
    return None
