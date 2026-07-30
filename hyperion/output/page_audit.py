"""Render-time page audit (P2-08, P2-24). Fail closed.

The page-count verdict checks *how many* pages exist. This module checks
*what is on them*, on the produced PDF bytes via PyMuPDF, and RAISES on any
violation (DoD gate P2-G1: a failing audit must raise, not warn).

Assertions (audit §2 P2-08 table):

| Assertion                                   | Threshold                        |
|---------------------------------------------|----------------------------------|
| image / text bbox intersection              | < 1.0 pt2, excl. cover plates    |
| words per body page                         | >= 90                            |
| ink fill per body page                      | >= 0.30, median >= 0.45          |
| column balance per two-column body page     | min(col) >= 0.35 * max(col)      |
| content bbox within trim box                | -0.5 <= x0, x1 <= page_w + 0.5   |
| corner pixel colour                         | theme background, all 4 corners  |
| TOC stated page vs actual heading page      | exact match for every entry      |
| ``{'`` / ``{"`` in extracted text           | == 0                             |
| U+2014 / U+2013 in extracted text           | == 0                             |
| banned filler / meta-text (T-06, P2-24)     | == 0                             |
| duplicate paragraph (>= 12 words, T-08)     | no normalized hash twice         |

The integrity text scan (P2-24) runs here on the *extracted text of the
produced PDF* — the artifact the client receives — complementing the
pre-render model scan in the Quality Gate.
"""

from __future__ import annotations

import hashlib
import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF

# Theme canvas colour. Kept in sync with PDF_PALETTE["cream"] in
# agents/delivery/presentation_designer.py (:106). Callers that render under a
# different palette must pass background_rgb explicitly.
DEFAULT_BACKGROUND_RGB: tuple[int, int, int] = (0xF5, 0xF4, 0xEE)

WORDS_PER_PAGE_MIN = 90
INK_FILL_MIN = 0.30
INK_FILL_MEDIAN_MIN = 0.45
COLUMN_BALANCE_MIN = 0.35
COLUMN_PROSE_MIN_WORDS = 60
OCCLUSION_TOLERANCE_PT2 = 1.0
TRIM_TOLERANCE_PT = 0.5
CORNER_CHANNEL_TOLERANCE = 3
DUP_PARAGRAPH_MIN_WORDS = 12

# T-06 zero-tolerance client-text hygiene list (audit §5). "hallucinat" and
# the internal-agent names are banned in client-visible text; the Technical
# Appendix must phrase internal metrics without these tokens.
BANNED_SUBSTRINGS: tuple[str, ...] = (
    "{'",
    '{"',
    "—",
    "–",
    "Insufficient evidence to state implications",
    "no specific implications stated",
    "no specific implications could be derived",
    "so what? no specific",
    "no competitors identified",
    "accessed_at",
    "\\u20",
    "$XB",
    "$YB",
    "[verified citation]",
    "[new source",
    "previously lacked",
    "parse error",
    "Data Sparse",
    "hallucinat",
    "unverified claim",
    "Fact Checker",
    "Quality Gate",
)

_TOC_ENTRY_RE = re.compile(r"^(?P<title>.{3,80}?)\s*(?:\.{2,}\s*|\s{2,})(?P<page>\d{1,3})\s*$")
_TOC_HEADING_RE = re.compile(r"^(table of )?contents$", re.IGNORECASE)


class PageAuditError(RuntimeError):
    """Raised when the produced PDF fails the render-time page audit."""

    def __init__(self, violations: list[str]) -> None:
        self.violations = list(violations)
        joined = "\n".join(f"  - {v}" for v in self.violations)
        super().__init__(
            f"Page audit FAILED ({len(self.violations)} violation(s)):\n{joined}"
        )


@dataclass
class PageAuditResult:
    passed: bool
    violations: list[str] = field(default_factory=list)
    metrics: dict[str, object] = field(default_factory=dict)


def extract_pdf_text(pdf_path: str | Path) -> str:
    """All extracted text of the PDF, one page per line."""
    with fitz.open(str(pdf_path)) as doc:
        return "\n".join(page.get_text() for page in doc)


def scan_text_integrity(text: str) -> list[str]:
    """P2-24 / T-06 integrity scan over arbitrary text.

    Returns one human-readable hit per banned substring found (the hit string
    embeds the matched token). Empty list means clean.
    """
    lowered = text.lower()
    hits: list[str] = []
    for token in BANNED_SUBSTRINGS:
        if token.lower() in lowered:
            hits.append(f"banned text present in PDF: {token}")
    return hits


def _normalized_paragraphs(chunks: list[str], min_words: int) -> list[str]:
    paragraphs: list[str] = []
    for chunk in chunks:
        normalized = " ".join(chunk.split()).lower()
        if len(normalized.split()) >= min_words:
            paragraphs.append(normalized)
    return paragraphs


def _check_occlusion(doc: fitz.Document, cover_pages: frozenset[int]) -> list[str]:
    violations: list[str] = []
    for page in doc:
        if page.number in cover_pages:
            continue  # declared full-bleed plates legitimately carry text on art
        images = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
        blocks = [fitz.Rect(b[:4]) for b in page.get_text("blocks")]
        for img in images:
            for blk in blocks:
                inter = img & blk
                if not inter.is_empty and inter.get_area() >= OCCLUSION_TOLERANCE_PT2:
                    violations.append(
                        f"page {page.number + 1}: image {img} occludes text "
                        f"{blk} ({inter.get_area():.0f} pt2)"
                    )
    return violations


def _check_words_and_fill(
    doc: fitz.Document, cover_pages: frozenset[int]
) -> tuple[list[str], dict[str, object]]:
    violations: list[str] = []
    fills: list[float] = []
    words_per_page: list[int] = []
    for page in doc:
        if page.number in cover_pages:
            continue
        words = len(page.get_text().split())
        ink = sum(fitz.Rect(b[:4]).get_area() for b in page.get_text("blocks"))
        fill = ink / page.rect.get_area() if page.rect.get_area() else 0.0
        words_per_page.append(words)
        fills.append(fill)
        if words < WORDS_PER_PAGE_MIN:
            violations.append(f"page {page.number + 1}: {words} words (< {WORDS_PER_PAGE_MIN})")
        if fill < INK_FILL_MIN:
            violations.append(
                f"page {page.number + 1}: {fill:.1%} ink fill (< {INK_FILL_MIN:.0%})"
            )
    metrics: dict[str, object] = {"fills": fills, "words_per_page": words_per_page}
    if fills:
        median_fill = statistics.median(fills)
        metrics["median_fill"] = median_fill
        if median_fill < INK_FILL_MEDIAN_MIN:
            violations.append(
                f"median ink fill {median_fill:.1%} (< {INK_FILL_MEDIAN_MIN:.0%})"
            )
    return violations, metrics


def _check_column_balance(doc: fitz.Document, cover_pages: frozenset[int]) -> list[str]:
    violations: list[str] = []
    for page in doc:
        if page.number in cover_pages:
            continue
        mid = page.rect.width / 2
        blocks = [b for b in page.get_text("blocks") if b[4].strip()]
        c1 = sum(len(b[4].split()) for b in blocks if b[2] <= mid + 6)
        c2 = sum(len(b[4].split()) for b in blocks if b[0] >= mid - 6)
        if max(c1, c2) < COLUMN_PROSE_MIN_WORDS:
            continue  # not a prose page
        if min(c1, c2) < COLUMN_BALANCE_MIN * max(c1, c2):
            violations.append(
                f"page {page.number + 1}: column imbalance col1={c1}w col2={c2}w "
                f"(min(col) < {COLUMN_BALANCE_MIN} * max(col))"
            )
    return violations


def _check_trim(doc: fitz.Document) -> list[str]:
    violations: list[str] = []
    for page in doc:
        for b in page.get_text("blocks"):
            if b[0] < -TRIM_TOLERANCE_PT or b[2] > page.rect.width + TRIM_TOLERANCE_PT:
                violations.append(
                    f"page {page.number + 1}: content outside trim box "
                    f"(x0={b[0]:.1f}, x1={b[2]:.1f}, width={page.rect.width:.1f})"
                )
    return violations


def _check_corners(
    doc: fitz.Document, background_rgb: tuple[int, int, int]
) -> list[str]:
    violations: list[str] = []
    for page in doc:
        pix = page.get_pixmap(dpi=72)
        w, h = pix.width, pix.height
        for x, y in ((1, 1), (w - 2, 1), (1, h - 2), (w - 2, h - 2)):
            pixel = pix.pixel(x, y)
            if any(
                abs(pixel[ch] - background_rgb[ch]) > CORNER_CHANNEL_TOLERANCE
                for ch in range(3)
            ):
                violations.append(
                    f"page {page.number + 1}: corner ({x},{y}) is {tuple(pixel[:3])}, "
                    f"expected canvas {background_rgb}"
                )
    return violations


def _check_toc(doc: fitz.Document) -> list[str]:
    """T-05: every TOC entry's stated page must equal the page its heading is
    drawn on. Runs only when a Contents page with numbered entries is found."""
    violations: list[str] = []
    toc_page_index: int | None = None
    for page in doc:
        lines = [ln.strip() for ln in page.get_text().splitlines() if ln.strip()]
        if any(_TOC_HEADING_RE.match(ln) for ln in lines):
            toc_page_index = page.number
            break
    if toc_page_index is None:
        return violations

    entries: list[tuple[str, int]] = []
    for ln in doc[toc_page_index].get_text().splitlines():
        m = _TOC_ENTRY_RE.match(ln.strip())
        if m:
            entries.append((m.group("title").strip(), int(m.group("page"))))
    if not entries:
        return violations

    page_texts = [
        " ".join(p.get_text().split()).lower() for p in doc
    ]
    for title, stated in entries:
        normalized = " ".join(title.split()).lower()
        actual = next(
            (i + 1 for i, t in enumerate(page_texts) if normalized in t and i != toc_page_index),
            None,
        )
        if actual is None:
            violations.append(f"TOC entry {title!r}: no page carries this heading (phantom entry)")
        elif actual != stated:
            violations.append(
                f"TOC entry {title!r}: stated page {stated}, heading is on page {actual}"
            )
    return violations


def _check_duplicates(text_chunks: list[str]) -> list[str]:
    violations: list[str] = []
    seen: dict[str, int] = {}
    for para in _normalized_paragraphs(text_chunks, DUP_PARAGRAPH_MIN_WORDS):
        digest = hashlib.sha256(para.encode("utf-8")).hexdigest()
        seen[digest] = seen.get(digest, 0) + 1
    for digest, count in seen.items():
        if count > 1:
            violations.append(
                f"duplicate paragraph (>= {DUP_PARAGRAPH_MIN_WORDS} words) appears "
                f"{count} times (sha256:{digest[:12]})"
            )
    return violations


def audit_pdf(
    pdf_path: str | Path,
    *,
    background_rgb: tuple[int, int, int] = DEFAULT_BACKGROUND_RGB,
    cover_pages: frozenset[int] | set[int] = frozenset(),
    fail_closed: bool = True,
) -> PageAuditResult:
    """Run the full P2-08 assertion table against produced PDF bytes.

    Args:
        pdf_path: path to the rendered PDF.
        background_rgb: theme canvas colour the page corners must carry.
        cover_pages: 0-based indices of declared full-bleed plates, excluded
            from occlusion / word / fill / column checks.
        fail_closed: when True (default), raise PageAuditError on any
            violation. When False, return the result with passed=False.

    Returns:
        PageAuditResult with per-page metrics. Raises PageAuditError when
        fail_closed and any assertion fails.
    """
    violations: list[str] = []
    metrics: dict[str, object] = {}

    with fitz.open(str(pdf_path)) as doc:
        cover = frozenset(cover_pages)
        violations.extend(_check_occlusion(doc, cover))
        fill_violations, fill_metrics = _check_words_and_fill(doc, cover)
        violations.extend(fill_violations)
        metrics.update(fill_metrics)
        violations.extend(_check_column_balance(doc, cover))
        violations.extend(_check_trim(doc))
        violations.extend(_check_corners(doc, background_rgb))
        violations.extend(_check_toc(doc))
        full_text = "\n".join(page.get_text() for page in doc)
        text_chunks = [b[4] for page in doc for b in page.get_text("blocks")]
        metrics["page_count"] = len(doc)

    # P2-24: authoritative integrity scan on the extracted text of the
    # produced PDF (the artifact the client receives).
    violations.extend(scan_text_integrity(full_text))
    violations.extend(_check_duplicates(text_chunks))

    result = PageAuditResult(passed=not violations, violations=violations, metrics=metrics)
    if violations and fail_closed:
        raise PageAuditError(violations)
    return result
