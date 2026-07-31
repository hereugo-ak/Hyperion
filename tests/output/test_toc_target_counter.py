"""P2-05/P2-06: the TOC must use real cross references, not arithmetic.

P2-05: hardcoded ints and `+ N` expressions assumed one page per section
(measured error up to 9 pages). Fix: <a href="#sec-N"> with
content: target-counter(attr(href), page), id="sec-N" on every section
wrapper, and every fixed block gets an anchor id.

P2-06: rows must be emitted from the same conditions as the chapters -
Risk Analysis was listed unconditionally while the chapter is guarded by
{% if report.risk_analysis %}.
"""

from __future__ import annotations

import re

from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE, HTML_TEMPLATE

TOC_START = "{# ── Table of Contents ──"


def _toc_block() -> str:
    start = HTML_TEMPLATE.index(TOC_START)
    end = HTML_TEMPLATE.index("</table>", start)
    return HTML_TEMPLATE[start:end]


def test_toc_has_no_hardcoded_page_numbers():
    toc = _toc_block()
    assert not re.search(r"<td>\s*\d+\s*</td>", toc), (
        "hardcoded page integers are arithmetic fiction (P2-05)"
    )
    assert not re.search(r"loop\.index\s*\+|\|\s*length\s*\+", toc), (
        "no +N arithmetic page numbers (P2-05)"
    )


def test_toc_uses_target_counter():
    assert re.search(
        r"target-counter\(\s*attr\(\s*href\s*\)\s*,\s*page\s*\)", CSS_TEMPLATE
    ), "toc-page cells must resolve via target-counter(attr(href), page)"


def test_section_rows_link_to_section_anchors():
    toc = _toc_block()
    assert 'href="#sec-{{ loop.index }}"' in toc
    # And the section wrapper must carry the matching id.
    assert 'id="sec-{{ loop.index }}"' in HTML_TEMPLATE


def test_fixed_blocks_have_anchor_ids():
    for anchor in ("at-a-glance", "exec-summary", "risk-analysis",
                   "methodology", "endnotes", "technical-appendix",
                   "appendix-sources"):
        assert f'id="{anchor}"' in HTML_TEMPLATE, f"missing chapter anchor id={anchor}"
        assert f'href="#{anchor}"' in HTML_TEMPLATE, f"missing TOC link to #{anchor}"


def test_toc_risk_row_shares_chapter_condition():
    toc = _toc_block()
    risk_row = re.search(r"\{%\s*if report\.risk_analysis\s*%\}.*?Risk Analysis", toc, re.DOTALL)
    assert risk_row is not None, (
        "the Risk Analysis TOC row must be guarded by the same condition "
        "that emits the chapter (P2-06)"
    )
