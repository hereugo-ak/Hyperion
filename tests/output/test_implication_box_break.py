"""P2-04: the implication callout is the last child after a full-height
column block, so it fragments onto its own page alone (6 pages of report B:
25 words, 2.0% ink). Fix per audit: break-before: avoid on the callout, and
orphans/widows on the column block so prose accompanies it.
"""

from __future__ import annotations

import re

from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE


def _css_rule(css: str, selector: str) -> str:
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


def test_implication_box_avoids_break_before():
    rule = _css_rule(CSS_TEMPLATE, ".implication-box")
    assert "break-before: avoid" in rule or "page-break-before: avoid" in rule, (
        "the callout must not be allowed to start a fresh page alone"
    )


def test_implication_box_keeps_together():
    rule = _css_rule(CSS_TEMPLATE, ".implication-box")
    assert "break-inside: avoid" in rule or "page-break-inside: avoid" in rule


def test_section_body_sets_orphans_and_widows():
    rule = _css_rule(CSS_TEMPLATE, ".section-body")
    assert re.search(r"orphans:\s*[4-9]", rule), (
        "at least 4 lines of prose must accompany a fragment boundary"
    )
    assert re.search(r"widows:\s*[4-9]", rule)
