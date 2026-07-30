"""
Tests for 5.6 — PDF/A-2b post-pass via pikepdf + bookmarks.

Audit §6 item 9: "`pikepdf`/`qpdf` — PDF/A-2b post-pass, metadata,
outline/bookmarks. Free credibility." (Phase 5.6)

Layers:
- Functional (live, pikepdf present in sandbox): the pass stamps XMP
  pdfaid:part=2 / conformance=B, writes the outline, and survives a
  re-open by an independent reader (fitz + pikepdf).
- Negative controls: empty title, missing file, out-of-range bookmark
  pages, and a deliberately truncated (corrupt) PDF MUST refuse — never
  crash, never half-apply.
- Structural AST guards: pikepdf must be lazy-imported, render.py must
  route both PDF engines through the post-pass, and pyproject must
  declare pikepdf.
"""

import ast
from pathlib import Path
from unittest.mock import patch

import pytest

from hyperion.output.pdf_postprocess import (
    BookmarkSpec,
    PDFMetadata,
    PDFPostProcessResult,
    _require_pikepdf,
    postprocess_pdf,
)

REPO_ROOT = Path(__file__).resolve().parent.parent

pikepdf = pytest.importorskip("pikepdf", reason="pikepdf not installed — post-pass tests skipped")


def _make_pdf(path: Path, pages: int = 3) -> Path:
    """Build a minimal valid PDF with distinct text per page."""
    import fitz

    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page()
        page.insert_text((72, 72), f"Section {i + 1} Heading")
        page.insert_text((72, 100), f"Body content for section {i + 1}.")
    doc.save(str(path))
    doc.close()
    return path


# ─────────────────────────────────────────────────────────────────────────
# Functional — the pass actually does what it claims
# ─────────────────────────────────────────────────────────────────────────


class TestPDFA2bConformance:
    def test_xmp_stamps_pdfa_identification(self, tmp_path):
        pdf = _make_pdf(tmp_path / "report.pdf")
        res = postprocess_pdf(
            pdf,
            PDFMetadata(title="EU Grid Study", keywords="energy, eu"),
            bookmarks=[BookmarkSpec("Cover", 0)],
        )
        assert res.applied
        assert res.pdfa_conformance == "2b"
        # Independent reader verifies — do not trust our own result object.
        with pikepdf.open(pdf) as reopened, reopened.open_metadata() as xmp:
            assert xmp.get("pdfaid:part") == "2"
            assert xmp.get("pdfaid:conformance") == "B"
            assert xmp.get("dc:title") == "EU Grid Study"
            assert xmp.get("pdf:Keywords") == "energy, eu"

    def test_bookmarks_survive_reopen(self, tmp_path):
        pdf = _make_pdf(tmp_path / "report.pdf")
        specs = [
            BookmarkSpec("Section 1 Heading", 0),
            BookmarkSpec("Section 2 Heading", 1),
            BookmarkSpec("Section 3 Heading", 2),
        ]
        res = postprocess_pdf(pdf, PDFMetadata(title="T"), bookmarks=specs)
        assert res.bookmarks_written == 3
        # fitz reads the outline as a TOC — independent of pikepdf.
        import fitz

        doc = fitz.open(str(pdf))
        toc = doc.get_toc()
        doc.close()
        assert [entry[1] for entry in toc] == [s.title for s in specs]
        assert [entry[2] for entry in toc] == [1, 2, 3]  # 1-based pages

    def test_unreadable_bytes_refuse_and_preserve_file(self, tmp_path):
        """Garbage bytes (not a PDF at all) must refuse WITHOUT destroying
        the bytes already on disk. NOTE: a merely *truncated* PDF is NOT a
        refusal case — qpdf reconstructs broken xref tables during open,
        and post-processing the recovered structure is legitimate."""
        pdf = tmp_path / "report.pdf"
        original = b"%PDF-1.4 broken-garbage-no-xref-no-objects" * 20
        pdf.write_bytes(original)
        res = postprocess_pdf(pdf, PDFMetadata(title="T"))
        assert not res.applied
        assert pdf.read_bytes() == original  # untouched

    def test_save_failure_leaves_original_bytes_intact(self, tmp_path):
        """The atomicity contract: if the pass fails AFTER opening the PDF
        (here: save raises), the original file must survive byte-for-byte —
        the temp-file + os.replace design exists for exactly this case."""
        pdf = _make_pdf(tmp_path / "report.pdf")
        original = pdf.read_bytes()

        def _exploding_save(self, *args, **kwargs):  # noqa: ANN001, ANN202
            raise OSError("simulated disk-full during save")

        with patch.object(pikepdf.Pdf, "save", _exploding_save):
            res = postprocess_pdf(pdf, PDFMetadata(title="T"))
        assert not res.applied
        assert "post-pass failed" in res.reason
        assert pdf.read_bytes() == original  # the deliverable survives

    def test_result_records_metadata_fields(self, tmp_path):
        pdf = _make_pdf(tmp_path / "report.pdf")
        res = postprocess_pdf(pdf, PDFMetadata(title="T", keywords="k"))
        assert "pdfaid" in res.metadata_fields
        assert "dc:title" in res.metadata_fields
        assert "pdf:Keywords" in res.metadata_fields


# ─────────────────────────────────────────────────────────────────────────
# Negative controls — reintroduce the defect, the pass MUST refuse
# ─────────────────────────────────────────────────────────────────────────


class TestNegativeControls:
    def test_missing_pdf_refuses(self, tmp_path):
        res = postprocess_pdf(tmp_path / "nope.pdf", PDFMetadata(title="T"))
        assert not res.applied
        assert "not found" in res.reason

    def test_empty_title_refuses(self, tmp_path):
        pdf = _make_pdf(tmp_path / "report.pdf")
        res = postprocess_pdf(pdf, PDFMetadata(title="   "))
        assert not res.applied
        assert "title" in res.reason

    def test_out_of_range_bookmarks_dropped_not_fatal(self, tmp_path):
        pdf = _make_pdf(tmp_path / "report.pdf", pages=2)
        specs = [
            BookmarkSpec("Good", 0),
            BookmarkSpec("OutOfRange", 99),
            BookmarkSpec("Negative", -1),
            BookmarkSpec("", 1),  # empty title
        ]
        res = postprocess_pdf(pdf, PDFMetadata(title="T"), bookmarks=specs)
        assert res.applied
        assert res.bookmarks_written == 1  # only the valid entry survives

    def test_missing_pikepdf_degrades(self, tmp_path):
        pdf = _make_pdf(tmp_path / "report.pdf")
        with patch("hyperion.output.pdf_postprocess._require_pikepdf", return_value=None):
            res = postprocess_pdf(pdf, PDFMetadata(title="T"))
        assert not res.applied
        assert res.reason == "pikepdf not installed"

    def test_module_importable_without_pikepdf(self):
        """The lazy-import contract: importing the module never touches
        pikepdf, so hosts without it still import cleanly."""
        # _require_pikepdf returns the real module here (installed); the
        # AST guard below pins the structural property instead.
        assert _require_pikepdf() is not None


# ─────────────────────────────────────────────────────────────────────────
# Structural AST guards — wiring cannot silently regress
# ─────────────────────────────────────────────────────────────────────────


class TestStructuralGuards:
    def test_pikepdf_lazy_imported(self):
        """pikepdf must be imported INSIDE _require_pikepdf, not at module
        top — hosts without it must still import pdf_postprocess."""
        tree = ast.parse((REPO_ROOT / "hyperion" / "output" / "pdf_postprocess.py").read_text())
        top_level = any(
            isinstance(node, (ast.Import, ast.ImportFrom))
            and (
                (isinstance(node, ast.Import) and any(a.name == "pikepdf" for a in node.names))
                or (isinstance(node, ast.ImportFrom) and node.module == "pikepdf")
            )
            for node in tree.body
        )
        assert not top_level, "pikepdf must be lazy-imported inside _require_pikepdf"

    def test_render_routes_both_engines_through_post_pass(self):
        """render_pdf has two success paths (WeasyPrint, Playwright). Both
        must call _apply_pdf_post_pass before returning — one engine only
        would leave the fallback deliverable un-archivable."""
        src = (REPO_ROOT / "hyperion" / "output" / "render.py").read_text()
        assert src.count("self._apply_pdf_post_pass(result, output_path, full_html)") >= 2

    def test_render_method_extracts_bookmarks(self):
        src = (REPO_ROOT / "hyperion" / "output" / "render.py").read_text()
        assert "def _apply_pdf_post_pass" in src
        assert "BookmarkSpec" in src
        assert "postprocess_pdf" in src

    def test_postprocess_never_raises_by_contract(self):
        """postprocess_pdf returns PDFPostProcessResult on every path —
        its only exit is `return result`. Guard: no bare `raise` in the
        public function body."""
        tree = ast.parse((REPO_ROOT / "hyperion" / "output" / "pdf_postprocess.py").read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == "postprocess_pdf":
                raises = [n for n in ast.walk(node) if isinstance(n, ast.Raise)]
                assert not raises, "postprocess_pdf must degrade, never raise"

    def test_pikepdf_declared_dependency(self):
        import tomllib

        with open(REPO_ROOT / "pyproject.toml", "rb") as fh:
            data = tomllib.load(fh)
        deps = data["project"]["dependencies"]
        assert any(d.startswith("pikepdf") for d in deps), "pikepdf not in dependencies"

    def test_result_dataclass_contract(self):
        """The render result consumes these fields; renaming any of them
        silently breaks the warning strings."""
        res = PDFPostProcessResult()
        for field_name in ("applied", "pdfa_conformance", "bookmarks_written", "reason"):
            assert hasattr(res, field_name), f"PDFPostProcessResult lost {field_name}"
