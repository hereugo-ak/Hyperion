"""OVERHAUL4 fix: section images must NEVER occlude the 2-column text.

The audited defect: ``.section-plate img`` used ``object-fit: cover`` with a
``max-height``. WeasyPrint paints the cover-cropped content at its NATURAL
size, centred in the box, WITHOUT clipping — so a portrait source (the render
pipeline cropped section images 40%-portrait) bled ~91pt above and below the
box and smothered the column text beneath it (the render-time audit's
occlusion check fired on 9+ text blocks per imaged page).

The fix has two halves:
1. CSS: ``object-fit`` dropped; the image scale-to-fits within the band
   (``max-width: 100%; max-height: 62mm; width/height: auto``) — any aspect
   ratio fits inside the column-span band, never crossing into the gutter.
2. Render engine: section images are now pre-cropped to the band's landscape
   ratio (1890x732, full content width x 62mm at 300 DPI) instead of the old
   40%-portrait target.

This test renders a real section body (2 columns + a TALL portrait image —
the exact failure shape) through the REAL CSS_TEMPLATE and asserts the
render-time occlusion check is clean.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

try:
    pytest.importorskip("weasyprint", reason="weasyprint required")
except OSError:
    # Windows: weasyprint imports but fails to load its GTK natives
    # (libgobject) — same reason the render path falls back to Chromium.
    pytest.skip("weasyprint native libs unavailable", allow_module_level=True)


@pytest.fixture(scope="module")
def occluded_metrics() -> dict[str, object]:
    """Render the failure shape (tall portrait image in a 2-col section)."""
    script = REPO / "scripts" / "repro_section_image.py"
    assert script.is_file(), f"missing script: {script}"
    out = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        cwd=str(REPO),
        timeout=300,
        check=False,
    )
    assert out.returncode == 0, f"repro failed:\n{out.stderr[-2000:]}"
    return {"stdout": out.stdout, "stderr": out.stderr}


def test_section_image_never_occludes_text(occluded_metrics: dict[str, object]) -> None:
    """The audited occlusion must be zero — the image may not overlap ANY
    text block, in either column."""
    stdout = str(occluded_metrics["stdout"])
    assert "OCCLUSION:" not in stdout, (
        "section image still occludes column text:\n"
        + "\n".join(line for line in stdout.splitlines() if "OCCLUSION" in line)
    )


def test_section_image_stays_in_band(occluded_metrics: dict[str, object]) -> None:
    """The image box must be exactly the 62mm band (175.7pt) tall and never
    have a negative top (the pre-fix render started at y=-90.7, off the page)."""
    stdout = str(occluded_metrics["stdout"])
    img_lines = [line for line in stdout.splitlines() if "image bbox" in line]
    assert img_lines, "no image box measured"
    for line in img_lines:
        bbox = line.split("image bbox:", 1)[1].strip(" []")
        vals = [float(v) for v in bbox.split(",") if v]
        x0, y0, x1, y1 = vals
        assert y0 >= 0, f"image starts above the page top: y0={y0}"
        height = y1 - y0
        assert height <= 176.5, (  # 62mm = 175.7pt, +tolerance
            f"image taller than the 62mm band: {height:.1f}pt"
        )
