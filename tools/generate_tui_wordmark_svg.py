"""Generate README wordmark assets from the locked TUI characters and palette.

Both generated assets reuse the TUI's `WORDMARK`, `LOGO_STOPS`, and `ramp()`
implementation. The SVG provides an inspectable vector source, while the PNG
is the cross-compatible README embed used by GitHub and other Markdown viewers.
"""

from __future__ import annotations

from html import escape
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

from hyperion.tui.banner import WORDMARK
from hyperion.tui.motion.color import hex_to_rgb, ramp
from hyperion.tui.theme import BG_CANVAS, LOGO_STOPS

ASSET_DIR = Path("assets/brand")
SVG_OUTPUT = ASSET_DIR / "hyperion-tui-wordmark.svg"
PNG_OUTPUT = ASSET_DIR / "hyperion-tui-wordmark.png"
FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf")
SVG_FONT_SIZE = 38
SVG_CELL_WIDTH = 23
SVG_LINE_HEIGHT = 47
SVG_PADDING_X = 60
SVG_PADDING_Y = 60
PNG_FONT_SIZE = 52
PNG_LINE_GAP = 14
PNG_PADDING_X = 96
PNG_PADDING_Y = 72


def _dimensions() -> tuple[int, int]:
    """Return the locked wordmark width and height in terminal cells."""
    return max(len(line) for line in WORDMARK), len(WORDMARK)


def _glyph_color(column: int, width: int) -> tuple[int, int, int]:
    """Sample the exact TUI static OKLab ramp for one terminal column."""
    return hex_to_rgb(ramp(LOGO_STOPS, column / max(1, width - 1)))


def generate_svg() -> None:
    """Write the exact TUI characters as a vector wordmark with per-cell color."""
    width_cells, height_cells = _dimensions()
    width = SVG_PADDING_X * 2 + width_cells * SVG_CELL_WIDTH
    height = SVG_PADDING_Y * 2 + height_cells * SVG_LINE_HEIGHT
    glyphs: list[str] = []

    for row, line in enumerate(WORDMARK):
        y = SVG_PADDING_Y + SVG_FONT_SIZE + row * SVG_LINE_HEIGHT
        for column, character in enumerate(line):
            if character == " ":
                continue
            color = ramp(LOGO_STOPS, column / max(1, width_cells - 1))
            x = SVG_PADDING_X + column * SVG_CELL_WIDTH
            glyphs.append(
                f'    <text x="{x}" y="{y}" fill="{color}">{escape(character)}</text>'
            )

    svg = "\n".join(
        [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<svg xmlns="http://www.w3.org/2000/svg" '
            f'viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
            '  <title id="title">HYPERION terminal wordmark</title>',
            '  <desc id="desc">The exact six-line ANSI Shadow Hyperion wordmark, '
            'colored with the terminal interface’s soft-clay to deep-clay gradient.</desc>',
            f'  <rect width="{width}" height="{height}" rx="18" fill="{BG_CANVAS}"/>',
            '  <g font-family="ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, '
            'Liberation Mono, monospace" font-size="38" font-weight="700" '
            'dominant-baseline="alphabetic" text-rendering="geometricPrecision">',
            *glyphs,
            '  </g>',
            '</svg>',
            '',
        ]
    )
    SVG_OUTPUT.write_text(svg, encoding="utf-8")


def generate_png() -> None:
    """Write a PNG fallback with the exact glyphs and TUI-derived colors."""
    width_cells, height_cells = _dimensions()
    font = ImageFont.truetype(str(FONT_PATH), PNG_FONT_SIZE)
    cell_width = round(font.getlength("M"))
    ascent, descent = font.getmetrics()
    line_height = ascent + descent + PNG_LINE_GAP
    width = PNG_PADDING_X * 2 + width_cells * cell_width
    height = PNG_PADDING_Y * 2 + height_cells * line_height - PNG_LINE_GAP

    image = Image.new("RGB", (width, height), hex_to_rgb(BG_CANVAS))
    draw = ImageDraw.Draw(image)
    for row, line in enumerate(WORDMARK):
        y = PNG_PADDING_Y + row * line_height
        for column, character in enumerate(line):
            if character != " ":
                draw.text(
                    (PNG_PADDING_X + column * cell_width, y),
                    character,
                    font=font,
                    fill=_glyph_color(column, width_cells),
                    stroke_width=0,
                )
    image.save(PNG_OUTPUT, format="PNG", optimize=True)


def main() -> None:
    """Generate both committed README assets from the runtime brand source."""
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    generate_svg()
    generate_png()


if __name__ == "__main__":
    main()
