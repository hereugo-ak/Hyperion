"""Validate that README branding remains mechanically aligned with the TUI."""

from __future__ import annotations

import re
from pathlib import Path

from PIL import Image

from hyperion.tui.banner import WORDMARK
from hyperion.tui.motion.color import hex_to_rgb, ramp
from hyperion.tui.theme import BG_CANVAS, LOGO_STOPS

README = Path("README.md")
SVG = Path("assets/brand/hyperion-tui-wordmark.svg")
PNG = Path("assets/brand/hyperion-tui-wordmark.png")


def main() -> None:
    """Fail clearly if the README or visual asset drifts from the TUI source."""
    readme = README.read_text(encoding="utf-8")
    if 'src="assets/brand/hyperion-tui-wordmark.png"' not in readme:
        raise SystemExit("README does not embed the cross-compatible PNG wordmark")
    if 'src="assets/brand/hyperion-logo.png"' not in readme:
        raise SystemExit("README does not embed the supplied Hyperion logo")

    svg = SVG.read_text(encoding="utf-8")
    if f'fill="{BG_CANVAS}"' not in svg:
        raise SystemExit("SVG background differs from the TUI canvas")

    width = max(len(line) for line in WORDMARK)
    actual = {
        (int(x), int(y)): fill
        for x, y, fill in re.findall(r'<text x="(\d+)" y="(\d+)" fill="(#[0-9A-Fa-f]{6})">', svg)
    }
    expected: dict[tuple[int, int], str] = {}
    for row, line in enumerate(WORDMARK):
        y = 60 + 38 + row * 47
        for column, character in enumerate(line):
            if character != " ":
                expected[(60 + column * 23, y)] = ramp(LOGO_STOPS, column / max(1, width - 1))
    if actual != expected:
        missing = sorted(expected.items() - actual.items())[:3]
        unexpected = sorted(actual.items() - expected.items())[:3]
        raise SystemExit(
            "SVG glyph positions or colors differ from the TUI OKLab ramp; "
            f"missing={missing}; unexpected={unexpected}"
        )

    image = Image.open(PNG).convert("RGB")
    if image.getpixel((0, 0)) != hex_to_rgb(BG_CANVAS):
        raise SystemExit("PNG canvas differs from the TUI canvas")
    if image.size[0] < 1000 or image.size[1] < 250:
        raise SystemExit("PNG does not have sufficient raster resolution for the README heading")

    print("README embeds both required brand assets")
    print("SVG wordmark glyphs exactly match hyperion.tui.banner.WORDMARK")
    print("SVG colors and glyph coordinates exactly match the TUI OKLab ramp")
    print(f"PNG decoded successfully at {image.size[0]}×{image.size[1]} on TUI canvas {BG_CANVAS}")


if __name__ == "__main__":
    main()
