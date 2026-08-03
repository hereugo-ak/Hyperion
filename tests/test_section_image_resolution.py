"""Tests for fix 3.6 — section image target >=2000px wide (no-upscale kept).

The audit (§3.7, §6 Phase 3 item 3.6) requires:
    Raise section image target to >=2000 px wide; keep the no-upscale rule.

Rationale: 800x400 was far below print grade — 300 DPI across the ~170mm
text measure needs ~2000px, and the BCG benchmark's largest embedded image
is 1660x2346. The no-upscale rule (§6.3 rule 4) is preserved: sources
smaller than target are skipped, never enlarged. Sources are now fetched
at full resolution (quality="high") so the gate can actually pass, and the
Unsplash cache is quality-aware so a stale 1080px "regular" download can
never satisfy a full-res request.

These tests cover:
  1. Constants: the section target is 2000px wide (2:1 aspect preserved).
  2. No-upscale: a too-small source still returns a non-fatal error result.
  3. End-to-end: a 2400x1200 source processes to exactly 2000x1000.
  4. Cache: download_image keys its cache by quality — a cached "regular"
     file does NOT satisfy a "high" request.
  5. Wiring: the designer requests full resolution for cover AND sections.
"""

from __future__ import annotations

import inspect
from pathlib import Path

import pytest

from hyperion.output.images import ImageProcessor, ImageProcessResult


class TestSectionHeaderConstants:
    def test_section_target_is_2000px_wide(self) -> None:
        assert ImageProcessor.SECTION_HEADER_WIDTH == 2000
        assert ImageProcessor.SECTION_HEADER_WIDTH >= 2000  # audit floor

    def test_section_aspect_preserved(self) -> None:
        assert ImageProcessor.SECTION_HEADER_HEIGHT == 1000
        assert (
            ImageProcessor.SECTION_HEADER_WIDTH / ImageProcessor.SECTION_HEADER_HEIGHT
            == 2.0
        )


class TestNoUpscalePreserved:
    """The audit's constraint: keep the no-upscale rule. A small source must
    be skipped (non-fatal error result), never enlarged."""

    @pytest.fixture()
    def small_image(self, tmp_path) -> str:
        pil_image, _, _ = ImageProcessor()._import_pillow()
        path = tmp_path / "small.jpg"
        pil_image.new("RGB", (1600, 800), (200, 180, 160)).save(path, "JPEG")
        return str(path)

    def test_small_source_returns_error_result(self, small_image: str) -> None:
        result = ImageProcessor().process_section_image(small_image)
        assert isinstance(result, ImageProcessResult)
        assert result.error, "1600px source must not pass the 2000px gate"
        assert "too small" in result.error.lower()
        assert result.output_path == ""  # nothing written

    def test_no_upscale_anywhere_in_pipeline(self, small_image: str) -> None:
        result = ImageProcessor().process_image(
            small_image, target_width=2000, target_height=1000
        )
        assert result.error
        # The result must report the ORIGINAL size — never a resized-up one
        assert result.original_width == 1600
        assert result.final_width == 0


class TestPrintGradeProcessing:
    """A full-resolution source passes the gate and lands at exactly the
    print-grade target."""

    @pytest.fixture()
    def hires_image(self, tmp_path) -> str:
        pil_image, _, _ = ImageProcessor()._import_pillow()
        path = tmp_path / "hires.jpg"
        pil_image.new("RGB", (2400, 1200), (120, 100, 90)).save(path, "JPEG")
        return str(path)

    def test_full_res_source_processes_to_2000x1000(
        self, hires_image: str, tmp_path
    ) -> None:
        out = tmp_path / "processed.png"
        result = ImageProcessor().process_section_image(
            hires_image, output_path=str(out)
        )
        assert result.error == ""
        assert result.final_width == 2000
        assert result.final_height == 1000
        assert out.exists()

        pil_image, _, _ = ImageProcessor()._import_pillow()
        with pil_image.open(out) as img:
            assert img.size == (2000, 1000)
            # PNG stores DPI as pixels-per-meter, so 300 DPI round-trips
            # as ~299.9994 — assert within tolerance, not exact equality.
            dpi = img.info.get("dpi", (0, 0))[0]
            assert abs(dpi - 300) < 0.01, f"DPI {dpi} != 300"


class TestQualityAwareCache:
    """Fix 3.6 cache correctness: a cached 1080px 'regular' download must
    never satisfy a later full-res 'high' request."""

    @pytest.fixture()
    def client(self, tmp_path):
        from hyperion.tools.unsplash import UnsplashClient

        c = UnsplashClient()
        c.IMAGE_DIR = str(tmp_path)
        return c

    @staticmethod
    def _image():
        from hyperion.tools.unsplash import UnsplashImage

        return UnsplashImage(
            id="photo123",
            image_url="https://images.unsplash.com/photo-123?fm=full",
            thumb_url="https://images.unsplash.com/photo-123?fm=thumb",
        )

    @staticmethod
    def _mock_client(monkeypatch, client, payload: bytes = b"img-bytes"):
        class _Resp:
            content = payload

            def raise_for_status(self) -> None:
                return None

        class _Client:
            async def get(self, url, follow_redirects=True):
                return _Resp()

        async def _get():
            return _Client()

        monkeypatch.setattr(client, "_get_client", _get)

    @pytest.mark.asyncio
    async def test_cache_filename_includes_quality(self, client, monkeypatch) -> None:
        import os

        self._mock_client(monkeypatch, client)
        path = await client.download_image(self._image(), quality="high")
        assert os.path.basename(path) == "photo123_high.jpg"
        assert os.path.exists(path)

    @pytest.mark.asyncio
    async def test_regular_cache_does_not_satisfy_high_request(
        self, client, monkeypatch, tmp_path
    ) -> None:
        import os

        # Stale 1080px regular download already on disk
        stale = tmp_path / "photo123_regular.jpg"
        stale.write_bytes(b"old-1080px")
        fetched: list[str] = []

        class _Resp:
            content = b"fresh-fullres"

            def raise_for_status(self) -> None:
                return None

        class _Client:
            async def get(self, url, follow_redirects=True):
                fetched.append(url)
                return _Resp()

        async def _get():
            return _Client()

        monkeypatch.setattr(client, "_get_client", _get)

        path = await client.download_image(self._image(), quality="high")
        assert os.path.basename(path) == "photo123_high.jpg"
        assert fetched, "high request must download, not serve the regular cache"
        assert Path(path).read_bytes() == b"fresh-fullres"

    @pytest.mark.asyncio
    async def test_same_quality_cache_hit_skips_download(
        self, client, monkeypatch, tmp_path
    ) -> None:
        cached = tmp_path / "photo123_high.jpg"
        cached.write_bytes(b"cached-fullres")

        async def _get():  # pragma: no cover - must never be called
            raise AssertionError("cache hit should not touch the network")

        monkeypatch.setattr(client, "_get_client", _get)
        path = await client.download_image(self._image(), quality="high")
        assert Path(path).read_bytes() == b"cached-fullres"


class TestDesignerRequestsFullResolution:
    """Wiring guard: the presentation designer must fetch full-res sources
    for BOTH the cover (1920px gate) and sections (2000px gate) — a 1080px
    'regular' download can never pass either under the no-upscale rule."""

    def test_no_regular_quality_downloads(self) -> None:
        import hyperion.agents.delivery.presentation_designer as pd

        src = inspect.getsource(pd)
        assert 'quality="regular"' not in src

    def test_high_quality_downloads_present(self) -> None:
        import hyperion.agents.delivery.presentation_designer as pd

        src = inspect.getsource(pd)
        assert src.count('quality="high"') >= 2  # cover + section call sites


class TestCoverFallbackAndFullBleed:
    """Regression guards for the missing-photo and white-frame cover defects."""

    def test_section_photo_is_promoted_when_dedicated_cover_is_missing(self) -> None:
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner
        from hyperion.schemas.models import ImageSelection, PageType

        designer = PresentationDesigner.__new__(PresentationDesigner)
        section_images = {
            "section_1": ImageSelection(
                id="img_section_1",
                page_type=PageType.SECTION,
                section_id="section_1",
                search_term="Africa renewable energy",
                image_path="/tmp/section.jpg",
                photographer="Example Photographer",
                unsplash_id="photo-1",
                caption="Section illustration.",
            )
        }

        cover = designer._promote_section_image_to_cover(section_images)

        assert cover is not None
        assert cover.page_type == PageType.COVER
        assert cover.placement == "full_bleed"
        assert cover.width_percent == 100
        assert cover.page_number == 1
        assert cover.section_id == ""
        assert cover.image_path == "/tmp/section.jpg"
        assert cover.caption == "Source: Unsplash via Example Photographer"
        assert section_images == {}, "promoted image must not repeat in the body"

    def test_empty_section_image_map_keeps_typographic_fallback(self) -> None:
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        designer = PresentationDesigner.__new__(PresentationDesigner)
        assert designer._promote_section_image_to_cover({}) is None

    def test_css_resets_ua_body_margin_and_paints_named_cover_page(self) -> None:
        from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE

        assert "margin: 0;\n    padding: 0;" in CSS_TEMPLATE
        cover_rule = CSS_TEMPLATE.split("@page cover", 1)[1].split("}", 1)[0]
        assert "margin: 0" in cover_rule
        assert "background: #1A1A1A" in cover_rule
