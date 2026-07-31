"""Typography sanitizer (P2-32): the em dash and en dash are banned characters
across the entire client-facing product surface.

Three enforcement layers, per audit §2 P2-32:
  1. Generation: a shared system-prompt rule (PROMPT_TYPOGRAPHY_RULE below)
     prepended to every agent prompt at dispatch.
  2. Sanitization: ``sanitize_typography`` runs inside the Jinja finalize
     hook, so model output is cleaned regardless of prompt compliance.
  3. Source hygiene + enforcement: string literals in the render path are
     purged, and page_audit asserts zero U+2014 / U+2013 in extracted text.

Replacement policy: a dash used as a prose separator becomes ", " (a dash
between words is a parenthetical aside, which a comma carries); a dash glued
directly to a following digit is a range, which becomes "-". Doubled
punctuation left by the replacement is collapsed.
"""

from __future__ import annotations

import re

EM_DASH = "—"
EN_DASH = "–"

# The single generation-layer rule, prepended once to every agent prompt.
PROMPT_TYPOGRAPHY_RULE = (
    "Never use the em dash character (U+2014) or the en dash (U+2013). "
    "Use a comma, a colon, or a full stop."
)

# Dash immediately followed by a digit or minus: a numeric range ("2020-2025"
# style). Word-boundary dashes in prose never abut a digit in this corpus.
_RANGE_RE = re.compile(r"[—–](?=[-]?\d)")
# Any remaining dash, with surrounding whitespace absorbed: a prose separator.
# Absorbing the spaces is what prevents "word , next" artifacts.
_PROSE_DASH_RE = re.compile(r"\s*[—–]\s*")
# Punctuation doubled (or comma-before-stop) by the replacement above.
_DOUBLE_PUNCT_RE = re.compile(r"([,.!?:;])\s*([,.!?:;])")


def sanitize_typography(text: str) -> str:
    """Remove every em/en dash from ``text`` per the policy above.

    Idempotent: ``sanitize_typography(sanitize_typography(t)) == sanitize_typography(t)``.
    """
    if not text:
        return text
    out = _RANGE_RE.sub("-", text)
    out = _PROSE_DASH_RE.sub(", ", out)
    # Collapse doubled punctuation introduced when a dash sat next to an
    # existing comma/stop ("word, - next" -> "word, , next"). Keep the first
    # mark; a stop after a comma would read as a typo.
    prev = None
    while prev != out:
        prev = out
        out = _DOUBLE_PUNCT_RE.sub(r"\1", out)
    # A comma stranded before a closing quote/paren reads as a typo too.
    out = re.sub(r",\s*([)\]”\"'])", r"\1", out)
    # Collapse runs of spaces the replacement may leave at line starts.
    out = re.sub(r"[ \t]{2,}", " ", out)
    return out
