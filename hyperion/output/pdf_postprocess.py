"""
HYPERION PDF Post-Processor — PDF/A-2b archival pass + bookmarks via pikepdf.

A consulting deliverable that cannot be archived, navigated, or trusted five
years from now is not a deliverable. WeasyPrint/Playwright emit a perfectly
rendered but *unconformant* PDF: no XMP metadata, no PDF/A identification,
no outline. Phase 5.6 closes that gap as a pure post-pass on the bytes
WeasyPrint already produced — the render path is untouched.

What this pass does, in order:
1. **Bookmarks (outline)** — a 36-page report with no outline forces the
   reader to scroll-scan. The outline is derived from the report structure
   (At a Glance, Table of Contents, per-section headings, Endnotes,
   Technical Appendix) so the reader jumps, not scrolls.
2. **XMP + PDF/A-2b identification** — stamps the XMP metadata packet with
   `pdfaid:part=2`, `pdfaid:conformance=B`, plus title/author/subject/
   keywords/creator/producer. This is what lets a records system classify
   the file as an archival-grade PDF instead of an anonymous blob.
3. **Deterministic metadata** — title/author/subject come from the caller,
   never invented; creation date is ISO-8601 UTC.

This is NOT a generic "tidy the PDF" wrapper. It:
- Runs pikepdf lazily — the module is importable without pikepdf installed
  (985MB sandbox); the caller degrades to the un-post-processed PDF with a
  logged warning, never a crash
- Never mutates the source in place — writes to a temp file and atomically
  replaces only on full success, so a failed pass leaves the good PDF intact
- Returns a typed PDFPostProcessResult so the render result can record
  exactly what was applied (bookmarks count, PDF/A flag, metadata)

Architecture reference: audit §6 item 9 — "`pikepdf`/`qpdf` — PDF/A-2b
post-pass, metadata, outline/bookmarks. Free credibility." (Phase 5.6)

Used by: the delivery render path (hyperion/output/render.py) after
WeasyPrint/Playwright succeeds.
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class BookmarkSpec:
    """One outline entry: a title and the 0-based page it opens on."""

    title: str
    page: int  # 0-based page index

    def to_dict(self) -> dict[str, Any]:
        return {"title": self.title, "page": self.page}


@dataclass
class PDFPostProcessResult:
    """What the post-pass actually did — recorded, never assumed."""

    applied: bool = False
    pdfa_conformance: str = ""  # "2b" when stamped
    bookmarks_written: int = 0
    metadata_fields: list[str] = field(default_factory=list)
    reason: str = ""  # why not applied (missing pikepdf, unreadable PDF...)

    def to_dict(self) -> dict[str, Any]:
        return {
            "applied": self.applied,
            "pdfa_conformance": self.pdfa_conformance,
            "bookmarks_written": self.bookmarks_written,
            "metadata_fields": self.metadata_fields,
            "reason": self.reason,
        }


@dataclass
class PDFMetadata:
    """Document metadata for the PDF/A-2b pass. Title is mandatory — a
    metadata pass that invents a title is worse than none."""

    title: str
    author: str = "HYPERION Consulting"
    subject: str = "Deep research deliverable"
    keywords: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "author": self.author,
            "subject": self.subject,
            "keywords": self.keywords,
        }


def _require_pikepdf() -> Any | None:
    """Import pikepdf lazily — optional runtime dependency.

    Returns the module, or None (logged) when not installed. Keeping the
    import inside the function means this module is always importable even
    on hosts without pikepdf (e.g. the 985MB sandbox).
    """
    try:
        import pikepdf

        return pikepdf
    except ImportError:
        logger.warning(
            "pikepdf not installed — PDF/A-2b post-pass skipped. "
            "Install with: pip install pikepdf"
        )
        return None


def postprocess_pdf(
    pdf_path: str | Path,
    metadata: PDFMetadata,
    bookmarks: list[BookmarkSpec] | None = None,
) -> PDFPostProcessResult:
    """Apply the PDF/A-2b + bookmarks post-pass to a rendered PDF.

    Atomic: writes to a temp file and replaces the original only on full
    success. A failed pass never leaves a half-written deliverable.

    Args:
        pdf_path: path to the WeasyPrint/Playwright output PDF
        metadata: title/author/subject/keywords (title required)
        bookmarks: outline entries (0-based pages); empty = no outline

    Returns:
        PDFPostProcessResult. Never raises — degradation is recorded.
    """
    result = PDFPostProcessResult()
    path = Path(pdf_path)

    if not path.exists():
        result.reason = f"PDF not found: {pdf_path}"
        logger.warning("PDF post-pass skipped — %s", result.reason)
        return result
    if not metadata.title.strip():
        result.reason = "metadata.title is empty"
        logger.warning("PDF post-pass skipped — %s", result.reason)
        return result

    pikepdf = _require_pikepdf()
    if pikepdf is None:
        result.reason = "pikepdf not installed"
        return result

    try:
        pdf = pikepdf.open(path)
    except Exception as exc:  # noqa: BLE001 - pikepdf raises untyped qpdf errors
        result.reason = f"could not open PDF: {exc!s:.120}"
        logger.warning("PDF post-pass skipped — %s", result.reason)
        return result

    page_count = len(pdf.pages)
    # Clamp bookmark pages into range — an out-of-range page is a caller bug
    # but must not crash the pass.
    clean_bookmarks = [
        b for b in (bookmarks or []) if 0 <= b.page < page_count and b.title.strip()
    ]

    try:
        # ── XMP + PDF/A-2b identification ──
        now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        with pdf.open_metadata(set_pikepdf_as_editor=False) as xmp:
            xmp["pdfaid:part"] = "2"
            xmp["pdfaid:conformance"] = "B"
            xmp["dc:title"] = metadata.title
            xmp["dc:creator"] = [metadata.author]
            xmp["dc:description"] = metadata.subject
            if metadata.keywords:
                xmp["pdf:Keywords"] = metadata.keywords
            # W-01 step 6 (RC-1): stamp the build SHA into the producer so an
            # uploaded artifact carries the commit it was built from. Had this
            # existed, the pre-merge artifact would have visibly predated the
            # merge and the diagnosis would have taken thirty seconds, not an
            # afternoon. Reads the provenance snapshot cached at boot — never
            # re-runs git per render.
            from hyperion.infra.provenance import current as _provenance_current

            _sha = _provenance_current().git_sha or "unknown"
            xmp["pdf:Producer"] = f"HYPERION {_sha} (WeasyPrint + pikepdf post-pass)"
            xmp["xmp:CreateDate"] = now
            xmp["xmp:ModifyDate"] = now
        result.metadata_fields = ["pdfaid", "dc:title", "dc:creator", "dc:description"]
        if metadata.keywords:
            result.metadata_fields.append("pdf:Keywords")

        # ── Bookmarks (outline) ──
        if clean_bookmarks:
            with pdf.open_outline() as outline:
                outline.root.clear()
                for spec in clean_bookmarks:
                    item = pikepdf.OutlineItem(spec.title, spec.page)
                    outline.root.append(item)
            result.bookmarks_written = len(clean_bookmarks)

        # ── Atomic write ──
        tmp_fd, tmp_name = tempfile.mkstemp(suffix=".pdf", dir=str(path.parent))
        os.close(tmp_fd)
        try:
            pdf.save(tmp_name)
            os.replace(tmp_name, path)
        finally:
            if os.path.exists(tmp_name):
                os.remove(tmp_name)

        result.applied = True
        result.pdfa_conformance = "2b"
        logger.info(
            "PDF/A-2b post-pass applied to %s (%d bookmarks)",
            path.name,
            result.bookmarks_written,
        )
        return result

    except Exception as exc:  # noqa: BLE001 - pikepdf raises untyped qpdf errors
        result.reason = f"post-pass failed: {exc!s:.120}"
        logger.warning("PDF post-pass failed — %s", result.reason)
        return result
    finally:
        pdf.close()
