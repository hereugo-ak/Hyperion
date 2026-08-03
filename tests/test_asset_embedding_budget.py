"""D-15 regression tests for bounded self-contained report assets."""

from __future__ import annotations

import base64
import re
from pathlib import Path

from PIL import Image

from hyperion.output.images import compress_image_for_embedding
from hyperion.output.render import PDFRenderer

DATA_URI_RE = re.compile(r'data:([^;]+);base64,([A-Za-z0-9+/=]+)')


def _write_noisy_png(path: Path, width: int = 1600, height: int = 900) -> None:
    image = Image.effect_noise((width, height), 100).convert("RGB")
    image.save(path, format="PNG")


def test_oversized_photo_is_recompressed_below_budget(tmp_path: Path) -> None:
    source = tmp_path / "cover.png"
    _write_noisy_png(source)
    assert source.stat().st_size > 120_000

    result = compress_image_for_embedding(source, 120_000)

    assert result is not None
    payload, mime_type = result
    assert mime_type == "image/jpeg"
    assert 0 < len(payload) <= 120_000
    with Image.open(Path(source)) as original:
        assert original.size == (1600, 900)
    with Image.open(__import__("io").BytesIO(payload)) as compressed:
        assert min(compressed.size) >= 320


def test_renderer_never_embeds_more_than_total_budget(
    tmp_path: Path,
    monkeypatch,
) -> None:
    import hyperion.output.images as image_module

    monkeypatch.setattr(image_module, "MAX_EMBEDDED_IMAGE_BYTES", 180_000)
    monkeypatch.setattr(image_module, "MAX_SECTION_IMAGE_BYTES", 100_000)

    sources = []
    for index in range(3):
        source = tmp_path / f"section_{index}.png"
        _write_noisy_png(source, 900, 600)
        sources.append(source)
    html = "".join(f'<img src="{source}">' for source in sources)

    embedded = PDFRenderer()._embed_images_as_data_uris(html)
    payloads = [base64.b64decode(match.group(2)) for match in DATA_URI_RE.finditer(embedded)]

    assert payloads
    assert sum(map(len, payloads)) <= 180_000
    assert all(len(payload) <= 100_000 for payload in payloads)
    assert str(tmp_path) not in embedded
    assert embedded.count("<img") < len(sources)


def test_unreadable_local_image_tag_is_removed(tmp_path: Path) -> None:
    missing = tmp_path / "missing.png"
    html = f'<p>before</p><img src="{missing}" alt="missing"><p>after</p>'

    embedded = PDFRenderer()._embed_images_as_data_uris(html)

    assert "<img" not in embedded
    assert str(missing) not in embedded
    assert "before" in embedded and "after" in embedded
