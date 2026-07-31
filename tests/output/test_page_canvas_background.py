"""P2-03: the page canvas is white because the cream background sits on
body, inset by the page margin. CSS Backgrounds propagates to the canvas
only from the ROOT element, and @page background covers the margin boxes.
Fix: background on html AND @page, body transparent.
"""

from __future__ import annotations

import re

from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE


def _css_rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_html_root_carries_theme_background():
    rule = _css_rule(CSS_TEMPLATE, "html")
    assert "background-color: #F5F4EE" in rule or "background-color: {cream}" in rule, (
        "the root element must carry the theme background - only root "
        "backgrounds propagate to the page canvas"
    )


def test_page_rule_carries_theme_background():
    m = re.search(r"@page\s*\{([^@]*)", CSS_TEMPLATE)
    assert m is not None
    block = m.group(1)
    assert "background" in block and ("#F5F4EE" in block or "{cream}" in block), (
        "@page must paint the margin boxes with the theme background"
    )


def test_body_background_transparent():
    rule = _css_rule(CSS_TEMPLATE, "body")
    assert "background-color: {cream}" not in rule and "background-color: #F5F4EE" not in rule, (
        "body background is what produced the white-corner defect - the "
        "body box is inset by the page margin"
    )
