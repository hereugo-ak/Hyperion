"""P2-07: the cover composition bled 328 pt off the left edge of page 1
(report A content bbox x0=-328.0 .. x1=934.0 on a 595.28 pt page).
Negative-x content is a PDF/A hazard and a print hazard, and the pikepdf
PDF/A-2b post-pass does not clip it.

Fix per audit: clip the cover to the trim box. If full bleed is intended,
use a dedicated `@page :first { margin: 0 }` and size the cover art to
exactly 210mm x 297mm - never an overflowing transform or negative offset.
"""

from __future__ import annotations

import re

from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE


def _css_rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{\{([^}]*)\}\}", css)
    if m:
        return m.group(1)
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_first_page_rule_zeroes_margins():
    """Full bleed requires @page :first { margin: 0 } - otherwise the
    210mm-wide cover box inside a (210 - 19 - 15)mm content frame is
    pushed out of the trim box on both sides."""
    m = re.search(r"@page\s+:first\s*\{\{?(.*?)\}\}?\s*\n", CSS_TEMPLATE, re.DOTALL)
    assert m is not None, "@page :first rule is missing"
    block = m.group(1)
    assert re.search(r"margin\s*:\s*0", block), (
        "@page :first must zero the page margins so the 210mm x 297mm "
        "cover exactly fills the trim box instead of overflowing it"
    )


def test_cover_sized_to_exact_trim_box():
    """The cover art must be exactly the A4 trim size, not larger."""
    rule = _css_rule(CSS_TEMPLATE, ".cover")
    assert "width: 210mm" in rule, (
        ".cover width must be exactly 210mm (the A4 trim width); "
        "'width: 100%' inside a margined content frame is what bled "
        "off the left edge"
    )
    assert "height: 297mm" in rule, ".cover height must be exactly 297mm"


def test_no_negative_offsets_anywhere():
    """No negative margin / left / top / right / bottom or translate in
    the whole stylesheet: those are the mechanisms that produce negative-x
    content, and the PDF/A post-pass does not clip them."""
    assert not re.search(
        r"(?:margin(?:-(?:left|right|top|bottom))?|left|right|top|bottom)"
        r"\s*:\s*-\d",
        CSS_TEMPLATE,
    ), "negative box offset found - content would start outside the trim box"
    assert "translate(-" not in CSS_TEMPLATE and "translateX(-" not in CSS_TEMPLATE, (
        "negative translate found - use @page :first margin: 0 plus an "
        "exactly-sized cover instead of an overflowing transform"
    )


def test_cover_named_page_rule_zeroes_margins():
    """.cover declares `page: cover`, so the named page rule @page cover
    must exist and zero the margins too (WeasyPrint honours named pages;
    without the rule the cover page keeps the body margins)."""
    m = re.search(r"@page\s+cover\s*\{\{?(.*?)\}\}?\s*\n", CSS_TEMPLATE, re.DOTALL)
    assert m is not None, (
        ".cover sets `page: cover` but no @page cover rule exists"
    )
    assert re.search(r"margin\s*:\s*0", m.group(1)), (
        "@page cover must zero the page margins for the same reason "
        "@page :first must"
    )


def test_cover_overflow_clipped():
    """Belt and braces: even if an absolutely-positioned child misbehaves,
    the cover box must clip it to the trim rectangle."""
    rule = _css_rule(CSS_TEMPLATE, ".cover")
    assert "overflow: hidden" in rule, (
        ".cover must keep overflow: hidden so nothing inside it can "
        "paint outside the trim box"
    )
