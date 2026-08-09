"""F-11 — retry-and-omit parse helper shared by the financial specialists.

The Aug 9 session's META-TEXT blocker fired because the literal string
``"Parse error"`` reached the client deliverable. The blocklists in
``quality_gate.py``, ``meta_text.py`` and ``page_audit.py`` only detect it
AFTER the fact; this helper kills it at source.

Contract:
- **Retry once**: ``json.loads`` first, then a lenient salvage pass (strip
  code fences, extract the first balanced JSON object) for models that wrap
  their output in prose or markdown.
- **Omit on failure**: return ``None`` so the caller can omit the metric
  (empty/missing value) instead of persisting a placeholder string.
- **Never raises**: a parse failure is data absence, not an exception.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)


def _strip_fences(content: str) -> str:
    """Drop markdown code fences some models wrap JSON in."""
    stripped = re.sub(r"^```(?:json)?\s*|\s*```$", "", content, flags=re.MULTILINE)
    return stripped.strip()


def _first_balanced_object(content: str) -> str | None:
    """Salvage the first balanced ``{...}`` block from noisy prose."""
    start = content.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for i in range(start, len(content)):
        ch = content[i]
        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : i + 1]
    return None


def parse_or_none(content: str | None) -> dict[str, Any] | None:
    """Parse an LLM JSON response, retrying once leniently; None on failure.

    Args:
        content: The raw LLM response body, possibly wrapped in fences or
            prose.

    Returns:
        A dict when parseable (after the retry), else ``None`` — the caller
        then OMITS the metric rather than emitting a placeholder string.
    """
    if not content or not content.strip():
        return None

    candidates: list[str] = []
    primary = content.strip()
    candidates.append(primary)

    # Retry tier 1: drop code fences.
    fenced = _strip_fences(primary)
    if fenced != primary:
        candidates.append(fenced)

    # Retry tier 2: salvage the first balanced JSON object.
    salvaged = _first_balanced_object(primary)
    if salvaged and salvaged != primary:
        candidates.append(salvaged)

    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
        if isinstance(data, dict):
            return data
    logger.warning(
        "parse_or_none: could not parse LLM JSON after retries (%.80r...)",
        primary[:200],
    )
    return None
