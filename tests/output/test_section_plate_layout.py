"""P2-02: a float:right sibling of the multicol .section-body is laid out in
the containing block, not the column flow - WeasyPrint composites the raster
over column 2's full-width line boxes (2,773 pt2 occlusion on every section
page carrying an image). The fix moves the figure INSIDE .section-body with
column-span: all as a full-measure band, and deletes float entirely.
"""

from __future__ import annotations

import re

from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE, HTML_TEMPLATE


def _css_rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_no_float_anywhere_near_section_image():
    assert not re.search(r"\.section-image[^{]*\{[^}]*float", CSS_TEMPLATE), (
        ".section-image must not float - float adjacent to column-count is "
        "the P2-02 occlusion defect"
    )
    assert ".section-image" not in CSS_TEMPLATE, (
        "the .section-image class is retired; the plate is .section-plate"
    )


def test_section_plate_spans_all_columns():
    rule = _css_rule(CSS_TEMPLATE, ".section-plate")
    assert "column-span: all" in rule
    assert "float" not in rule


def test_figure_markup_lives_inside_section_body():
    """The figure must be a child of the multicol div, not a preceding
    sibling - preceding siblings are what collided with the column flow."""
    body_open = HTML_TEMPLATE.index('<div class="section-body">')
    assert re.search(
        r'<div class="section-body">\s*\{%\s*if section_images', HTML_TEMPLATE
    ), "section figure must render inside .section-body"
    assert "<figure" in HTML_TEMPLATE[body_open:]


def test_figure_uses_figure_and_figcaption():
    assert '<figure class="section-plate"' in HTML_TEMPLATE
    assert "<figcaption" in HTML_TEMPLATE
    assert "section-image-caption" not in HTML_TEMPLATE


def test_wrong_full_measure_comment_deleted():
    assert "already full-measure" not in CSS_TEMPLATE, (
        "the comment asserting the floated image is already full-measure "
        "asserted the opposite of the defect (audit P2-02)"
    )
