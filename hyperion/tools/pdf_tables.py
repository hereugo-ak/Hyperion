"""HYPERION — PDF table extraction (pdfplumber), feeding ``chart_specs``.

Fix 2.3 (HYPERION_DEEP_AUDIT_2026-07-27.md §5.2 item 6, §6 Phase 2 item 2.3):
"Add ``pdfplumber``/``camelot`` table extraction for PDF sources; feed tables
to ``chart_specs``."

THE DEFECT THIS MODULE FIXES
----------------------------
The audit's benchmark observation: "Right now a 60-page PDF yields prose only;
the tables are where the exhibits live." Concretely:

  * ``unified_extract.extract_pdf`` delegated PDF text extraction to
    Crawl4AI's PDF path and stopped there. The output was a flat prose stream.
    IEA/IMF/World Bank/BCG reports carry their quantitative evidence in
    *tables* — production volumes by year, cost curves, market shares by
    region — and a prose-only extract keeps the narrative sentences around
    those tables while discarding the numbers themselves.
  * ``chart_specs.mine_chart_specs`` is honest by design (§4: "Never invent
    data"), so with no table numbers in the evidence stream it had nothing to
    mine and returned ``[]`` — compounding the ``has_exhibits: false`` output
    failure.

WHY pdfplumber AND NOT camelot
------------------------------
``camelot`` requires Ghostscript (a system-level binary dependency) and
OpenCV; it cannot be pip-installed into this repo's declared dependency set
without adding an OS-package installation step to every environment
(developer laptops, CI, Docker). ``pdfplumber`` is pure-Python on top of
``pdfminer.six`` — pip-installable, and per the audit benchmark it handles the
bordered-and-borderless table styles the benchmark reports use. The API below
returns plain structured rows, so a future camelot backend can be added as a
second provider behind the same ``PDFTable`` shape without touching consumers.

DESIGN CONTRACTS (learned from the audit's P0 and Phase 0-2 fixes)
------------------------------------------------------------------
1. **Never raises.** Any failure — missing pdfplumber, corrupt PDF, encrypted
   PDF, zero tables — returns an empty list with a WARNING/DEBUG log, never an
   exception. A table-extraction bug must cost *exhibit quality*, never
   retrieval itself (the exact failure isolation fix 2.2 applied to
   ``content_selector``).
2. **Offline-capable core.** ``extract_tables_from_bytes`` operates on raw PDF
   bytes; the network fetch lives in the caller (``unified_extract`` already
   has 10 fetch tiers). This keeps the module unit-testable with no network
   and no mocks.
3. **Two output shapes, one extraction.** ``PDFTable`` carries both the
   structured ``rows`` (for ``UnifiedExtractResult.tables``, which already
   exists and was always empty for PDFs) and a ``to_prose_lines()`` rendering
   (for appending into the extracted text stream, which is how the numbers
   reach agent findings and, from there, ``mine_chart_specs``'s existing
   prose-number parser — no change to the honest mining contract).
4. **Junk filtering.** A real consulting-report table has ≥2 columns and ≥2
   data rows and at least one numeric cell; pdfplumber's line-detection also
   fires on layout rules, page furniture, and border decoration. Those are
   dropped, because feeding "table-shaped noise" into ``mine_chart_specs``
   would produce charts from page numbers — a fabrication-adjacent failure the
   module docstring of ``chart_specs`` explicitly forbids.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass, field
from typing import Any

__all__ = [
    "PDFTable",
    "extract_tables_from_bytes",
    "tables_to_prose",
    "PDF_TABLES_AVAILABLE",
]

logger = logging.getLogger(__name__)

try:  # Declared dependency since fix 2.3; guard anyway — never-raises contract.
    import pdfplumber

    PDF_TABLES_AVAILABLE = True
except ImportError:  # pragma: no cover - exercised only in a broken env
    pdfplumber = None  # type: ignore[assignment]
    PDF_TABLES_AVAILABLE = False
    logger.warning(
        "pdfplumber is not installed — PDF table extraction tier disabled. "
        "Run: pip install pdfplumber"
    )


# ── Tunables ─────────────────────────────────────────────────────────────────

#: Don't scan a 600-page IMF book end-to-end for tables. Exhibits in the
#: benchmark reports cluster in the front half + appendix; 80 pages of scan
#: budget is the point where marginal yield stops paying for parse time.
MAX_SCAN_PAGES = 80

#: Per-document cap on extracted tables. A data-heavy report can carry
#: hundreds; the exhibit pipeline needs the best dozens.
MAX_TABLES = 40

#: A table must clear all three to count as evidence rather than furniture.
MIN_COLUMNS = 2
MIN_DATA_ROWS = 2  # rows beyond the header
MAX_CELL_CHARS = 200  # a "cell" this long is a text box, not a table cell

#: Numeric evidence test — a table with no numbers is a checklist, and
#: checklists don't mine into charts.
_NUMERIC_CELL_RE = re.compile(r"\d")


@dataclass
class PDFTable:
    """One extracted table, in both structured and prose-renderable form."""

    page: int  # 1-based page number — provenance, matches how PDFs are cited
    headers: list[str] = field(default_factory=list)
    rows: list[list[str]] = field(default_factory=list)

    @property
    def n_numeric_cells(self) -> int:
        return sum(
            1
            for row in self.rows
            for cell in row
            if _NUMERIC_CELL_RE.search(cell)
        )

    def to_dict(self) -> dict[str, Any]:
        """The shape ``UnifiedExtractResult.tables`` already carries."""
        return {"page": self.page, "headers": list(self.headers), "rows": [list(r) for r in self.rows]}

    def to_prose_lines(self) -> list[str]:
        """Render rows as 'label: value' lines parseable by ``_extract_numbers``.

        This is the bridge to ``chart_specs``: the miner parses numbers out of
        prose, so each data row becomes one line whose cells are joined —
        ``"Battery cell cost 2023: 139 USD/kWh"`` — preserving the row's own
        label next to its own number (the exact association a flat prose
        extract destroys). Markdown-table syntax is deliberately NOT used:
        ``_NUM_RE`` would parse the ``|``-separated cells identically, but the
        pipes add noise tokens to the LLM-facing content stream.
        """
        lines: list[str] = []
        header = " / ".join(h for h in self.headers if h)
        if header:
            lines.append(f"[Table p.{self.page}] {header}")
        for row in self.rows:
            cells = [c for c in row if c]
            if cells:
                lines.append(": ".join([cells[0], " | ".join(cells[1:])]) if len(cells) > 1 else cells[0])
        return lines


def _clean_cell(cell: Any) -> str:
    """Normalise a pdfplumber cell: None → "", collapse whitespace/newlines."""
    if cell is None:
        return ""
    text = re.sub(r"\s+", " ", str(cell)).strip()
    return text[:MAX_CELL_CHARS]


def _is_evidence_table(rows: list[list[str]]) -> bool:
    """Reject layout furniture; keep tables that carry chartable numbers."""
    if len(rows) < MIN_DATA_ROWS + 1:  # header + ≥MIN_DATA_ROWS data rows
        return False
    width = max(len(r) for r in rows)
    if width < MIN_COLUMNS:
        return False
    data = rows[1:]
    numeric = sum(1 for r in data for c in r if _NUMERIC_CELL_RE.search(c))
    # Require numeric content in at least two distinct data rows — a lone
    # total row under a text block is not a data series.
    numeric_rows = sum(1 for r in data if any(_NUMERIC_CELL_RE.search(c) for c in r))
    return numeric >= 2 and numeric_rows >= 2


def extract_tables_from_bytes(
    pdf_bytes: bytes,
    *,
    max_pages: int = MAX_SCAN_PAGES,
    max_tables: int = MAX_TABLES,
) -> list[PDFTable]:
    """Extract evidence-grade tables from raw PDF bytes. Never raises.

    Returns tables in document order, junk-filtered (see
    :func:`_is_evidence_table`). Empty list on any failure mode, each logged.
    """
    if not PDF_TABLES_AVAILABLE:
        return []
    if not pdf_bytes:
        logger.debug("extract_tables_from_bytes called with empty payload")
        return []

    tables: list[PDFTable] = []
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_idx, page in enumerate(pdf.pages[:max_pages], start=1):
                if len(tables) >= max_tables:
                    break
                try:
                    raw_tables = page.extract_tables()
                except Exception as e:  # per-page isolation: one bad page ≠ abort
                    logger.debug("table extraction failed on page %d: %s", page_idx, e)
                    continue
                for raw in raw_tables or []:
                    if len(tables) >= max_tables:
                        break
                    cleaned = [[_clean_cell(c) for c in row] for row in raw if row]
                    cleaned = [row for row in cleaned if any(row)]
                    if not _is_evidence_table(cleaned):
                        continue
                    headers, data = cleaned[0], cleaned[1:]
                    tables.append(PDFTable(page=page_idx, headers=headers, rows=data))
    except Exception as e:
        # Corrupt/encrypted/not-actually-a-PDF: WARNING with traceback per the
        # fail-loud discipline (fix 0.3), but the caller still gets [] and
        # continues with the prose extract it already has.
        logger.warning("pdfplumber failed on document: %s", e, exc_info=True)
        return []

    logger.debug("pdfplumber extracted %d evidence table(s)", len(tables))
    return tables


def tables_to_prose(tables: list[PDFTable], *, max_tables: int = 20) -> str:
    """Render extracted tables as a prose block for the content stream.

    Appended to a PDF's extracted text so the table numbers participate in
    agent reasoning and ``mine_chart_specs`` on equal footing with the
    narrative prose — that is the literal "feed tables to chart_specs" half
    of fix 2.3.
    """
    lines: list[str] = []
    for table in tables[:max_tables]:
        lines.extend(table.to_prose_lines())
    return "\n".join(lines)
