"""Tests for fix 2.3 — PDF table extraction feeding chart_specs.

Audit: HYPERION_DEEP_AUDIT_2026-07-27.md §5.2 item 6, §6 Phase 2 item 2.3:
"Add pdfplumber/camelot table extraction for PDF sources; feed tables to
chart_specs. Right now a 60-page PDF yields prose only; the tables are where
the exhibits live."

PDFs are built in-memory with reportlab-free, dependency-light PyMuPDF
(``fitz``) — already a declared project dependency — so these tests need no
network, no fixture files, and no mocks of the pdfplumber boundary.
"""

from __future__ import annotations

import fitz
import pytest

from hyperion.tools.pdf_tables import (
    MAX_CELL_CHARS,
    PDFTable,
    extract_tables_from_bytes,
    tables_to_prose,
)

# ── In-memory PDF builders ───────────────────────────────────────────────────

def _pdf_with_text_table() -> bytes:
    """A one-page PDF with a bordered table drawn as lines + text cells.

    pdfplumber's default line-strategy detection picks up drawn rectangles,
    so we draw a real 3-row x 3-col grid and place text in the cells.
    """
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    # Grid: 3 rows, 3 cols
    xs = [72, 250, 400, 528]
    ys = [100, 140, 180, 220]
    for y in ys:
        page.draw_line(fitz.Point(xs[0], y), fitz.Point(xs[-1], y))
    for x in xs:
        page.draw_line(fitz.Point(x, ys[0]), fitz.Point(x, ys[-1]))
    rows = [
        ["Year", "Production GWh", "Cost USD/kWh"],
        ["2023", "450", "139"],
        ["2024", "620", "115"],
    ]
    for r, row in enumerate(rows):
        for c, cell in enumerate(row):
            page.insert_text(
                fitz.Point(xs[c] + 6, (ys[r] + ys[r + 1]) / 2 + 4),
                cell,
                fontsize=10,
            )
    data = doc.tobytes()
    doc.close()
    return data


def _pdf_prose_only() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text(fitz.Point(72, 100), "This report contains no tables, only "
        "prose.", fontsize=11)
    data = doc.tobytes()
    doc.close()
    return data


# ── Core extraction ──────────────────────────────────────────────────────────

class TestExtractTablesFromBytes:
    def test_extracts_drawn_table_with_numbers(self):
        tables = extract_tables_from_bytes(_pdf_with_text_table())
        assert tables, "expected at least one evidence table from a drawn grid"
        flat = [cell for t in tables for row in t.rows for cell in row]
        assert any("450" in c for c in flat)
        assert any("139" in c for c in flat)

    def test_tables_carry_page_provenance(self):
        tables = extract_tables_from_bytes(_pdf_with_text_table())
        assert all(t.page >= 1 for t in tables)

    def test_tables_have_headers_and_data_rows(self):
        tables = extract_tables_from_bytes(_pdf_with_text_table())
        assert all(len(t.rows) >= 2 for t in tables)

    def test_prose_only_pdf_yields_no_tables(self):
        assert extract_tables_from_bytes(_pdf_prose_only()) == []

    def test_never_raises_on_empty_payload(self):
        assert extract_tables_from_bytes(b"") == []

    def test_never_raises_on_garbage_bytes(self):
        assert extract_tables_from_bytes(b"this is not a pdf at all \x00\x01\x02") == []

    def test_never_raises_on_truncated_pdf(self):
        real = _pdf_with_text_table()
        assert extract_tables_from_bytes(real[: len(real) // 3]) == [] or True
        # The contract is *never raises*; result may be [] or partial tables.

    def test_respects_max_tables_cap(self):
        tables = extract_tables_from_bytes(_pdf_with_text_table(), max_tables=1)
        assert len(tables) <= 1


# ── Junk filtering ───────────────────────────────────────────────────────────

class TestEvidenceFilter:
    def test_single_row_table_is_furniture(self):
        from hyperion.tools.pdf_tables import _is_evidence_table
        assert _is_evidence_table([["a", "1"]]) is False

    def test_no_numeric_content_is_checklist_not_table(self):
        from hyperion.tools.pdf_tables import _is_evidence_table
        assert _is_evidence_table(
            [["Item", "Status"], ["Alpha", "Done"], ["Beta", "Pending"]]
        ) is False

    def test_single_numeric_row_is_not_a_series(self):
        from hyperion.tools.pdf_tables import _is_evidence_table
        assert _is_evidence_table(
            [["Metric", "Value"], ["Region A", "n/a"], ["Total", "42"]]
        ) is False

    def test_two_numeric_rows_is_evidence(self):
        from hyperion.tools.pdf_tables import _is_evidence_table
        assert _is_evidence_table(
            [["Year", "GWh"], ["2023", "450"], ["2024", "620"]]
        ) is True


# ── Cell normalisation ───────────────────────────────────────────────────────

class TestCellCleaning:
    def test_none_cell_becomes_empty(self):
        from hyperion.tools.pdf_tables import _clean_cell
        assert _clean_cell(None) == ""

    def test_whitespace_collapsed(self):
        from hyperion.tools.pdf_tables import _clean_cell
        assert _clean_cell("multi\nline   cell") == "multi line cell"

    def test_overlong_cell_truncated(self):
        from hyperion.tools.pdf_tables import _clean_cell
        assert len(_clean_cell("x" * (MAX_CELL_CHARS + 50))) == MAX_CELL_CHARS


# ── Prose bridge to chart_specs ─────────────────────────────────────────────

class TestProseBridge:
    def test_to_prose_lines_keeps_label_next_to_number(self):
        t = PDFTable(page=3, headers=["Year", "GWh"], rows=[["2023", "450"], ["2024", "620"]])
        lines = t.to_prose_lines()
        joined = "\n".join(lines)
        assert "450" in joined and "620" in joined

    def test_tables_to_prose_renders_block(self):
        t = PDFTable(page=1, headers=["Year", "GWh"], rows=[["2023", "450"], ["2024", "620"]])
        block = tables_to_prose([t])
        assert "450" in block

    def test_prose_is_minable_by_chart_specs_number_parser(self):
        """The literal 'feed tables to chart_specs' contract: numbers rendered
        by the prose bridge must be parseable by the miner's own regex."""
        from hyperion.output.chart_specs import _extract_numbers

        t = PDFTable(
            page=1,
            headers=["Region", "Market size"],
            rows=[["Europe", "$2.4 billion"], ["Asia", "$5.1 billion"]],
        )
        numbers = _extract_numbers(tables_to_prose([t]))
        values = sorted(v for v, _u, _l in numbers)
        assert 2.4e9 in values and 5.1e9 in values

    def test_to_dict_shape_matches_unified_result_tables(self):
        t = PDFTable(page=2, headers=["a"], rows=[["b"]])
        d = t.to_dict()
        assert set(d) == {"page", "headers", "rows"}
        assert d["page"] == 2


# ── Wired into unified_extract.extract_pdf ───────────────────────────────────

class TestUnifiedExtractPdfWiring:
    @pytest.mark.asyncio
    async def test_extract_pdf_appends_tables_and_prose(self):
        """extract_pdf must enrich a successful text extraction with tables —
        and the table pass must never sink the text result."""
        from unittest.mock import AsyncMock, patch

        from hyperion.tools.crawl4ai import CrawlResult
        from hyperion.tools.unified_extract import UnifiedExtract

        fake = CrawlResult(
            url="https://example.com/r.pdf",
            title="r.pdf",
            content="Narrative prose. " * 40,  # passes the quality gate
            status_code=200,
            pdf_bytes=_pdf_with_text_table(),
        )
        extractor = UnifiedExtract()
        with patch.object(
            extractor, "_get_crawl4ai", new=AsyncMock()
        ) as get_client:
            client = AsyncMock()
            client.crawl_pdf = AsyncMock(return_value=fake)
            get_client.return_value = client

            result = await extractor.extract_pdf("https://example.com/r.pdf")

        assert result.success is True
        assert result.tables, "expected structured tables on the result"
        assert "450" in result.content, "expected table numbers in the content stream"
        assert "pdfplumber" in result.tools_tried

    @pytest.mark.asyncio
    async def test_table_pass_failure_never_sinks_text_extraction(self):
        from unittest.mock import AsyncMock, patch

        from hyperion.tools.crawl4ai import CrawlResult
        from hyperion.tools.unified_extract import UnifiedExtract

        fake = CrawlResult(
            url="https://example.com/r.pdf",
            title="r.pdf",
            content="Narrative prose. " * 40,
            status_code=200,
            pdf_bytes=b"\x00garbage-not-a-pdf",
        )
        extractor = UnifiedExtract()
        with patch.object(extractor, "_get_crawl4ai", new=AsyncMock()) as get_client:
            client = AsyncMock()
            client.crawl_pdf = AsyncMock(return_value=fake)
            get_client.return_value = client
            result = await extractor.extract_pdf("https://example.com/r.pdf")

        assert result.success is True  # text extraction survives
        assert result.tables == []

    @pytest.mark.asyncio
    async def test_crawl_result_carries_pdf_bytes(self):
        """CrawlResult must expose the downloaded payload for the table pass."""
        from hyperion.tools.crawl4ai import CrawlResult

        r = CrawlResult(url="u", pdf_bytes=b"%PDF-1.4 fake")
        assert r.pdf_bytes == b"%PDF-1.4 fake"
        # Internal hand-back: must NOT leak into serialised output.
        assert "pdf_bytes" not in r.to_dict()


# ── Dependency declared ─────────────────────────────────────────────────────

class TestDependencyDeclared:
    def test_pdfplumber_in_pyproject(self):
        from pathlib import Path

        text = Path("pyproject.toml").read_text()
        assert "pdfplumber" in text

    def test_pdfplumber_importable(self):
        import pdfplumber  # noqa: F401
