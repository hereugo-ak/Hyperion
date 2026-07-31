"""Canonical client-facing presenters (P2-09, P2-10).

Nothing reachable by the renderer may ever emit ``str()``, ``repr()`` or
``json.dumps`` of a structured object. Two functions cover both directions:

* ``display_value(obj)`` -- structured object in, prose out. Accepts a
  ``FinancialMetric``/``DataPoint``-style model, any Pydantic model, a dict,
  a list, or a scalar, and returns a human-readable string. Never raises for
  ordinary missing data; a genuinely unrepresentable object raises
  ``DisplayError`` so the caller can route it to the gap-closure loop
  (P2-16) instead of shipping a placeholder.

* ``humanize(text)`` -- text in, text out. Registered as the Jinja
  ``finalize`` hook (render.py, P2-10) so no template field can forget it.
  Detects a Python dict/list repr *anywhere* in the string (not only at the
  start -- the leaks that shipped were of the form ``LABEL: {'name': ...}``),
  parses it with ``ast.literal_eval`` (the correct tool for a Python repr;
  ``json.loads`` after a ``.replace("'", '"')`` corrupts apostrophes and
  cannot parse ``None``/``True``), and renders it as ``Key: Value`` prose.
  On a string that *looks* like a repr but cannot be parsed, it raises --
  it never truncates and ships the leak (the old ``text[:197] + "..."``
  fallback is exactly what the client saw).
"""

from __future__ import annotations

import ast
import re
from typing import Any

__all__ = ["DisplayError", "display_value", "humanize", "OBJECT_REPR_RE"]


class DisplayError(ValueError):
    """Raised when a value cannot be represented as honest client prose.

    Callers should treat this as an analysis gap (P2-16): re-dispatch the
    producing specialist, and if the gap cannot be closed, omit the field and
    declare the omission in ``FinalReport.limitations``. Never catch this and
    substitute filler.
    """


# Matches a dict/list repr *anywhere* in a string: ``{'key': ...}`` or
# ``{"key": ...}`` with at least one quoted key followed by a colon. The old
# ``clean_dict_repr`` guard (``text.strip().startswith("{")``) could not fire
# on the ``LABEL: {'...'}`` strings that actually leaked -- this one can.
OBJECT_REPR_RE = re.compile(r"\{['\"]\w+['\"]\s*:")

# A full dict/list literal, for extracting the repr substring to parse.
_DICT_SPAN_RE = re.compile(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}")


def _readable_key(key: Any) -> str:
    """Turn a dict key into a readable label: ``tam_triangulated`` -> ``Tam Triangulated``."""
    return str(key).replace("_", " ").strip().title()


def _present_scalar(value: Any) -> str:
    """Render one scalar without ever producing a repr."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, float):
        # Trim trailing .0 for whole numbers.
        s = f"{value:,.2f}".rstrip("0").rstrip(".")
        return s
    return str(value)


def display_value(obj: Any) -> str:
    """Present a structured value as client-readable prose.

    Order of preference:
      1. Objects that already know how to present themselves
         (``FinancialMetric.display_value()`` returns a clean unit-annotated
         string and never a repr).
      2. Pydantic models -> their most informative fields as ``Key: Value``.
      3. dict -> ``Key: Value`` pairs joined with a middle dot.
      4. list/tuple/set -> comma-joined presented items.
      5. scalars -> plain string.

    Raises ``DisplayError`` only when the value is an opaque object with no
    presentable surface (so the leak is caught, not shipped).
    """
    if obj is None:
        return ""

    # 1. Self-presenting models (FinancialMetric has display_value()).
    presenter = getattr(obj, "display_value", None)
    if callable(presenter) and not isinstance(obj, type):
        try:
            rendered = presenter()
        except Exception as exc:  # a broken presenter is a gap, not a repr
            raise DisplayError(
                f"{type(obj).__name__}.display_value() failed: {exc}"
            ) from exc
        if isinstance(rendered, str) and rendered:
            return rendered

    # 2. Pydantic models: present informative public fields.
    model_dump = getattr(obj, "model_dump", None)
    if callable(model_dump):
        try:
            data = model_dump()
        except Exception as exc:
            raise DisplayError(
                f"could not dump {type(obj).__name__}: {exc}"
            ) from exc
        if isinstance(data, dict):
            return _present_mapping(data, skip_empty=True)

    # 3. Mappings.
    if isinstance(obj, dict):
        return _present_mapping(obj, skip_empty=True)

    # 4. Sequences (but not strings/bytes).
    if isinstance(obj, (list, tuple, set, frozenset)):
        items = [display_value(v) for v in obj]
        items = [i for i in items if i]
        return ", ".join(items)

    # 5. Scalars.
    if isinstance(obj, (str, int, float, bool)):
        return _present_scalar(obj)

    # Opaque object with no presentable surface: refuse rather than repr().
    raise DisplayError(
        f"value of type {type(obj).__name__} has no client-facing representation"
    )


def _present_mapping(data: dict, skip_empty: bool) -> str:
    """Render a mapping as ``Key: Value · Key: Value`` prose."""
    parts: list[str] = []
    for key, value in data.items():
        if skip_empty and value in (None, "", [], {}):
            continue
        if isinstance(value, dict):
            rendered = _present_mapping(value, skip_empty=True)
        elif isinstance(value, (list, tuple, set, frozenset)):
            rendered = ", ".join(
                filter(None, (display_value(v) for v in value))
            )
        else:
            rendered = _present_scalar(value)
        if not rendered:
            continue
        parts.append(f"{_readable_key(key)}: {rendered}")
    return " · ".join(parts)


def humanize(text: Any) -> str:
    """Sanitize a renderable string: no Python object reprs reach the page.

    Registered as the Jinja ``finalize`` hook so *every* interpolated field
    passes through it. Behaviour:

    * Non-string, non-None values (a model slipped into a template) are
      routed through ``display_value``.
    * A string containing a dict/list repr anywhere has the repr parsed with
      ``ast.literal_eval`` and replaced with ``Key: Value`` prose.
    * A string that matches the repr pattern but cannot be parsed raises
      ``DisplayError`` -- it is never truncated and shipped.
    * Ordinary prose passes through unchanged.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        return display_value(text)
    if not OBJECT_REPR_RE.search(text):
        return text

    # Find the repr span and replace it with prose. Try progressively: the
    # first balanced-brace span that literal_eval can parse.
    for match in _DICT_SPAN_RE.finditer(text):
        span = match.group(0)
        try:
            parsed = ast.literal_eval(span)
        except (ValueError, SyntaxError, MemoryError, RecursionError):
            continue
        if isinstance(parsed, dict):
            prose = _present_mapping(parsed, skip_empty=True)
        elif isinstance(parsed, (list, tuple)):
            prose = ", ".join(filter(None, (display_value(v) for v in parsed)))
        else:
            prose = display_value(parsed)
        if prose:
            return (text[: match.start()] + prose + text[match.end():]).strip()

    # The string looked like it contained a repr but no span parsed. The old
    # code shipped ``text[:197] + "..."`` -- the exact leak. Refuse instead.
    raise DisplayError(
        "string matches an object-repr pattern but could not be parsed: "
        f"{text[:80]!r}"
    )
