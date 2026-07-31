"""Paragraph-level deduplication for assembled report text (P2-13).

Report B repeated whole paragraphs verbatim inside six chapters — the
concatenation path (P2-11, now deleted) joined one KeyFinding per market
segment whose generated content was identical, and nothing downstream
noticed. This module is the assembly-time guarantee: a repeated normalized
paragraph of >= 12 words is dropped, keeping the first occurrence and
preserving order.

The render-time backstop is a ``page_audit`` assertion that no normalized
paragraph of >= 12 words appears twice in the document.
"""

from __future__ import annotations

import hashlib
import re

__all__ = ["dedup_paragraphs", "normalized_paragraph_hash", "MIN_DEDUP_WORDS"]

# A paragraph shorter than this is a heading, caption, or connective tissue;
# repeating one is a stylistic choice, not a duplication defect.
MIN_DEDUP_WORDS = 12

_WS_RE = re.compile(r"\s+")


def _normalize(text: str) -> str:
    """Collapse whitespace and case so 'the same sentence, reformatted' matches."""
    return _WS_RE.sub(" ", text.strip().lower())


def normalized_paragraph_hash(text: str) -> str:
    """Stable hash of a paragraph's whitespace/case-normalized form."""
    return hashlib.sha1(_normalize(text).encode("utf-8")).hexdigest()


def _word_count(text: str) -> int:
    return len(_normalize(text).split())


def dedup_paragraphs(body: str, min_words: int = MIN_DEDUP_WORDS) -> str:
    """Drop duplicate paragraphs of >= ``min_words`` words, keeping the first.

    Paragraphs are split on blank lines. A paragraph under the word floor is
    never dropped (headings, captions and short connectives repeat
    legitimately). Order is preserved; the body is otherwise unchanged.
    """
    if not body or not body.strip():
        return body

    paragraphs = re.split(r"\n\s*\n", body)
    seen: set[str] = set()
    kept: list[str] = []
    for para in paragraphs:
        if _word_count(para) >= min_words:
            h = normalized_paragraph_hash(para)
            if h in seen:
                continue
            seen.add(h)
        kept.append(para)
    return "\n\n".join(kept)
