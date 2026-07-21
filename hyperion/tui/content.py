"""HYPERION — Content builders.

Textual 8.x renders text through its own :class:`textual.content.Content`
type, not Rich :class:`~rich.text.Text`. Passing Rich ``Text`` into a
``Static`` forces a lossy conversion that (a) needs an active app just to
measure height and (b) breaks layout for fixed-width / no-wrap widgets.

Every visible surface in this TUI is therefore built from ``Content`` — which
is *natively selectable and copyable* — using the tiny helpers below. A
``Line`` is an ordered list of ``(text, style)`` spans; ``build()`` assembles
one or more lines into a single ``Content`` with newlines between them.

``style`` is a Textual style string: ``"bold #00D9FF"``, ``"#6B7A99"``,
``"italic #F0ABFC on #111629"`` — colours, ``bold``, ``italic``, ``dim``,
``underline``, ``reverse`` all compose.
"""

from __future__ import annotations

from typing import Iterable

from textual.content import Content

# A span is (text, style-string). A line is a list of spans.
Span = tuple[str, str]
Line = list[Span]


def span(text: str, style: str = "") -> Span:
    return (text, style)


def line(*spans: Span | str) -> Line:
    """Build a line from spans; bare strings are unstyled."""
    out: Line = []
    for s in spans:
        if isinstance(s, str):
            out.append((s, ""))
        else:
            out.append(s)
    return out


def build(lines: Iterable[Line]) -> Content:
    """Assemble lines (each a list of (text, style) spans) into one Content."""
    parts: list[tuple[str, str]] = []
    first = True
    for ln in lines:
        if not first:
            parts.append(("\n", ""))
        first = False
        for text, style in ln:
            parts.append((text, style))
    if not parts:
        return Content("")
    # Content.assemble accepts (text, style) tuples and plain strings.
    return Content.assemble(*parts)


def build_line(*spans: Span | str) -> Content:
    """Convenience: build a single-line Content."""
    return build([line(*spans)])


def pad_between(left_len: int, right_len: int, width: int, minimum: int = 1) -> str:
    """Return a run of spaces that right-justifies a right segment."""
    return " " * max(minimum, width - left_len - right_len)
