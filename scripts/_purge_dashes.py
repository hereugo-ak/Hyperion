"""In-place purge of U+2014/U+2013 from string literals in render-path files.

Edits ONLY the character spans of STRING / FSTRING_MIDDLE tokens; every other
byte of the file stays identical (comments untouched, code formatting intact).

Cleaning rules mirror hyperion.output.typography.sanitize_typography, but the
punctuation collapse is SENTINEL-based: only punctuation adjacency created by
a dash replacement is collapsed, so pre-existing sequences like
``replace("Note:", "")`` inside template strings are never touched.
"""

from __future__ import annotations

import io
import re
import sys
import tokenize

EM = "—"
EN = "–"
SENTINEL = "\x00"

_FSTRING_MIDDLE = getattr(tokenize, "FSTRING_MIDDLE", None)
_TARGET_TYPES = {tokenize.STRING} | ({_FSTRING_MIDDLE} if _FSTRING_MIDDLE else set())

_RANGE_RE = re.compile(r"[—–](?=[-]?\d)")
_PROSE_DASH_RE = re.compile(r"\s*[—–]\s*")
_SENT_BEFORE_PUNCT_RE = re.compile(re.escape(SENTINEL) + r"\s*([,.;:!?])")
_PUNCT_BEFORE_SENT_RE = re.compile(r"([,.;:!?])\s*" + re.escape(SENTINEL))
_SENT_BEFORE_CLOSE_RE = re.compile(re.escape(SENTINEL) + r"\s*(?=[\"')\]])")


def _clean_token(text: str) -> str:
    out = _RANGE_RE.sub("-", text)
    out = _PROSE_DASH_RE.sub(SENTINEL, out)
    out = _SENT_BEFORE_CLOSE_RE.sub("", out)
    out = _SENT_BEFORE_PUNCT_RE.sub(r"\1", out)
    out = _PUNCT_BEFORE_SENT_RE.sub(r"\1", out)
    out = out.replace(SENTINEL, ", ")
    # A comma stranded at the very start of the literal reads as a typo.
    out = re.sub(r"^([\"']{1,3}), ", r"\1", out)
    return out


def purge(path: str) -> int:
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    line_off = [0]
    for m in re.finditer("\n", src):
        line_off.append(m.end())

    def abspos(pos: tuple[int, int]) -> int:
        row, col = pos
        return line_off[row - 1] + col

    spans: list[tuple[int, int, str]] = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type in _TARGET_TYPES and (EM in tok.string or EN in tok.string):
            new = _clean_token(tok.string)
            if new != tok.string:
                spans.append((abspos(tok.start), abspos(tok.end), new))

    for start, end, new in sorted(spans, reverse=True):
        src = src[:start] + new + src[end:]

    with open(path, "w", encoding="utf-8") as fh:
        fh.write(src)
    return len(spans)


if __name__ == "__main__":
    for path in sys.argv[1:]:
        n = purge(path)
        print(f"{path}: {n} token spans cleaned")
