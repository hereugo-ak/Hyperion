"""Tests for fix 3.2 — brand font embedding in the shipped CSS_TEMPLATE.

The audit (§3.2, Phase 3 item 3.2) requires:
    Inject @font-face with base64 data-URIs into the shipped CSS_TEMPLATE —
    assert in a test that the output PDF embeds InstrumentSerif/SourceSans3
    and **not** DejaVu.

These tests cover:
  1. Structure: the shipped CSS_TEMPLATE carries 7 @font-face blocks, one per
     vendored font, with data-URI sources (self-contained, no cwd dependence).
  2. Binding: @font-face family names exactly match the family names used in
     the CSS font-family declarations (a mismatch silently falls back).
  3. Never-raises (§0.3): a missing fonts directory degrades to a loud
     warning + empty CSS, never an exception.
  4. End-to-end: a real WeasyPrint render of HTML styled by the shipped
     CSS_TEMPLATE embeds InstrumentSerif / SourceSans3 / JetBrainsMono and
     does NOT fall back to DejaVu.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from hyperion.agents.delivery.presentation_designer import (
    _FONTS_DIR,
    _VENDORED_FONTS,
    CSS_TEMPLATE,
    _build_font_face_css,
)

FONT_FACE_RE = re.compile(r"@font-face \{[^}]+\}")
FAMILY_RE = re.compile(r'font-family: "([^"]+)"')
WEIGHT_RE = re.compile(r"font-weight: (\d+)")
STYLE_RE = re.compile(r"font-style: (\w+)")
DATA_URI_RE = re.compile(r'src: url\("data:font/ttf;base64,([A-Za-z0-9+/=]+)"\) format\("truetype"\)')


def _font_face_blocks(css: str) -> list[str]:
    return FONT_FACE_RE.findall(css)


class TestFontFaceStructure:
    """The shipped CSS_TEMPLATE must carry one @font-face per vendored font."""

    def test_seven_font_face_blocks(self) -> None:
        blocks = _font_face_blocks(CSS_TEMPLATE)
        assert len(blocks) == len(_VENDORED_FONTS) == 7

    def test_every_block_has_data_uri_source(self) -> None:
        for block in _font_face_blocks(CSS_TEMPLATE):
            m = DATA_URI_RE.search(block)
            assert m is not None, f"@font-face block lacks data-URI src:\n{block[:200]}"
            # The base64 payload must be non-trivial (a real font, not a stub)
            assert len(m.group(1)) > 50_000

    def test_no_relative_or_remote_url_sources(self) -> None:
        """Data-URIs only — relative url() would resolve against the caller's
        cwd (inline <style> + base_url=cwd) and break outside the repo root."""
        bad = re.findall(r'src: url\("(?!data:)[^"]+"\)', CSS_TEMPLATE)
        assert bad == [], f"non-data-URI font sources present: {bad}"

    def test_block_faces_match_vendored_manifest(self) -> None:
        expected = {(fam, wt, st) for fam, wt, st, _ in _VENDORED_FONTS}
        actual = set()
        for block in _font_face_blocks(CSS_TEMPLATE):
            fam = FAMILY_RE.search(block)
            wt = WEIGHT_RE.search(block)
            st = STYLE_RE.search(block)
            assert fam and wt and st
            actual.add((fam.group(1), int(wt.group(1)), st.group(1)))
        assert actual == expected

    def test_font_face_families_match_declared_families(self) -> None:
        """A family-name mismatch between @font-face and font-family is the
        classic silent-fallback bug: the block exists but never binds."""
        embedded = {FAMILY_RE.search(b).group(1) for b in _font_face_blocks(CSS_TEMPLATE)}
        declared = set()
        for m in re.finditer(r"font-family: ([^;]+);", CSS_TEMPLATE):
            for part in m.group(1).split(","):
                name = part.strip().strip('"')
                if name in {"Instrument Serif", "Source Sans 3", "JetBrains Mono"}:
                    declared.add(name)
        assert declared == {"Instrument Serif", "Source Sans 3", "JetBrains Mono"}
        assert declared <= embedded

    def test_font_faces_come_after_format(self) -> None:
        """Palette placeholders must be resolved AND font blocks present —
        proves injection happened after str.format(**PDF_PALETTE)."""
        assert "{warm_gray}" not in CSS_TEMPLATE
        assert "{cream}" not in CSS_TEMPLATE
        assert CSS_TEMPLATE.rindex("@font-face") > CSS_TEMPLATE.index("#F5F4EE")


class TestNeverRaises:
    """§0.3 discipline: a missing font degrades loudly, never fatally."""

    def test_missing_dir_returns_empty_and_logs(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING):
            css = _build_font_face_css(Path("/nonexistent/fonts/dir"))
        assert css == ""
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 7  # one loud warning per vendored font
        assert any("InstrumentSerif-Regular.ttf" in r.getMessage() for r in warnings)
        assert any(r.exc_info for r in warnings)  # fail-loud: exc_info=True

    def test_partial_dir_skips_only_missing(self, tmp_path: Path) -> None:
        # Provide exactly one real font file; the other six must be skipped.
        real = _FONTS_DIR / _VENDORED_FONTS[0][3]
        (tmp_path / _VENDORED_FONTS[0][3]).write_bytes(real.read_bytes())
        css = _build_font_face_css(tmp_path)
        blocks = _font_face_blocks(css)
        assert len(blocks) == 1
        assert _VENDORED_FONTS[0][0] in blocks[0]

    def test_all_vendored_fonts_on_disk(self) -> None:
        for _, _, _, filename in _VENDORED_FONTS:
            path = _FONTS_DIR / filename
            assert path.exists(), f"vendored font missing: {path}"
            assert path.stat().st_size > 20_000
            # TTF magic number: 0x00010000
            assert path.read_bytes()[:4] == b"\x00\x01\x00\x00"


class TestRenderedPdfEmbedsBrandFonts:
    """The audit's acceptance test: render a real PDF through the shipped
    CSS_TEMPLATE and prove the embedded fonts are the brand fonts, not the
    DejaVu system fallback."""

    @pytest.fixture(scope="class")
    def rendered_pdf(self, tmp_path_factory: pytest.TempPathFactory) -> Path:
        weasyprint = pytest.importorskip("weasyprint")
        fitz = pytest.importorskip("fitz")
        del fitz  # used in the assertions below, imported separately

        html = f"""<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><style>{CSS_TEMPLATE}</style></head>
<body>
  <h1>Instrument Serif Heading</h1>
  <h2>Second Level Heading</h2>
  <h3>Source Sans Subsection</h3>
  <p>Body text in Source Sans 3 with <strong>bold emphasis</strong>
     and <em>italic emphasis</em> to force every face to embed.</p>
  <p><em>Pure italic sentence in the body face.</em></p>
  <p><strong>Pure bold sentence in the body face.</strong></p>
  <p class="kpi-value">42.7%</p>
  <p class="kpi-label">MARKET SHARE</p>
  <p class="confidence-pill">CONFIDENCE 0.87</p>
</body>
</html>"""
        out = tmp_path_factory.mktemp("pdf") / "font_embed_test.pdf"
        weasyprint.HTML(string=html, base_url=".").write_pdf(str(out))
        assert out.exists() and out.stat().st_size > 10_000
        return out

    @staticmethod
    def _embedded_basefont_names(pdf_path: Path) -> set[str]:
        """Return embedded font names normalised for comparison.

        PDF BaseFont names carry a 6-letter subset prefix (``AQHRXV+``) and
        WeasyPrint hyphenates PostScript names (``Source-Sans-3-Bold``), so
        comparisons normalise by stripping the prefix, dashes and case.
        """
        import fitz

        names: set[str] = set()
        with fitz.open(str(pdf_path)) as doc:
            for page in doc:
                for font in page.get_fonts():
                    base = font[3]
                    base = base.split("+", 1)[-1]  # strip subset prefix
                    names.add(base.replace("-", "").replace(",", "").lower())
        return names

    def test_pdf_embeds_instrument_serif(self, rendered_pdf: Path) -> None:
        names = self._embedded_basefont_names(rendered_pdf)
        assert any("instrumentserif" in n for n in names), f"fonts: {sorted(names)}"

    def test_pdf_embeds_source_sans_3(self, rendered_pdf: Path) -> None:
        names = self._embedded_basefont_names(rendered_pdf)
        assert any("sourcesans3" in n for n in names), f"fonts: {sorted(names)}"

    def test_pdf_embeds_jetbrains_mono(self, rendered_pdf: Path) -> None:
        names = self._embedded_basefont_names(rendered_pdf)
        assert any("jetbrainsmono" in n for n in names), f"fonts: {sorted(names)}"

    def test_pdf_does_not_fall_back_to_dejavu(self, rendered_pdf: Path) -> None:
        """The audit's core assertion: no DejaVu anywhere in the output."""
        names = self._embedded_basefont_names(rendered_pdf)
        assert not any("dejavu" in n for n in names), f"fonts: {sorted(names)}"

    def test_pdf_embeds_bold_and_italic_faces(self, rendered_pdf: Path) -> None:
        """Bold/italic must be REAL embedded faces, not synthesized — the
        test HTML forces <strong>/<em> in the body face for this reason."""
        names = self._embedded_basefont_names(rendered_pdf)
        assert any("sourcesans3bold" in n for n in names), f"fonts: {sorted(names)}"
        assert any("sourcesans3italic" in n for n in names), f"fonts: {sorted(names)}"

    def test_pdf_no_synthesized_bold_serif(self, rendered_pdf: Path) -> None:
        """Instrument Serif ships Regular/Italic only (no bold weight). If a
        heading is rendered bold, WeasyPrint synthesizes a smeared fake-bold
        (``Instrument-Serif-Bold``) — a premium-report defect. Headings must
        use the real Regular face, so no synthesized serif-bold may appear."""
        names = self._embedded_basefont_names(rendered_pdf)
        assert not any("instrumentserifbold" in n for n in names), (
            f"synthesized bold serif present: {sorted(names)}"
        )
