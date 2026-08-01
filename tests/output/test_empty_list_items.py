"""P2-34: methodology and appendix loops render empty bullets.

Report B's Methodology page printed 11 empty bullet glyphs because
``report.agents_used`` held 11 empty strings and the template loop was
unguarded::

    {% for agent in report.agents_used %}
    <li>{{ agent }}</li>
    {% endfor %}

Fix per audit: filter falsy entries in every template loop
(``{% for x in list if x %}``), suppress the enclosing <h3>/<ul> when the
filtered list is empty, and add a page_audit assertion that no page
contains a list item with no text content.

W-09 update: the ``agents_used`` roster loop is deleted outright — the roster
is internal telemetry and the client template can no longer resolve
``report.agents_used`` at all (ClientReport does not carry it). The
falsy-filter contract still applies to every remaining loop, and the
methodology assertions below now defend the limitations list and the
absence of the roster, which is the stronger guarantee.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from html.parser import HTMLParser
from types import SimpleNamespace

import fitz  # PyMuPDF
import pytest
from jinja2 import BaseLoader, Environment

from hyperion.agents.delivery.presentation_designer import HTML_TEMPLATE
from hyperion.output.page_audit import _check_empty_list_items

_DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"

_FOR_TAG_RE = re.compile(r"\{%\s*for\s+([^%]+?)\s*%\}")


# ── page_audit assertion ─────────────────────────────────────────────────────


def _make_bullet_pdf(path, *, empty: bool) -> None:
    """One page carrying a bullet list; ``empty`` reproduces report B's
    marker-only glyphs, otherwise each marker carries text."""
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.insert_font(fontname="dvs", fontfile=_DEJAVU)
    if empty:
        text = "\u2022\n\u2022\n\u2022\n\u2022\n\u2022"
    else:
        text = (
            "\u2022 Engagement Director orchestrated the pipeline\n"
            "\u2022 Fact Checker verified every claim\n"
            "\u2022 Quality Gate enforced the style contract"
        )
    spare = page.insert_textbox(
        fitz.Rect(50, 50, 545, 800), text, fontname="dvs", fontsize=10
    )
    if spare < 0:
        raise ValueError("textbox overflow: synthetic PDF lost its text")
    doc.save(str(path))
    doc.close()


def test_page_audit_flags_empty_list_items(tmp_path):
    pdf = tmp_path / "empty_bullets.pdf"
    _make_bullet_pdf(pdf, empty=True)
    with fitz.open(str(pdf)) as doc:
        violations = _check_empty_list_items(doc)
    assert violations, "marker-only lines must be flagged as empty list items"
    assert "empty list item" in violations[0]


def test_page_audit_accepts_bullets_with_text(tmp_path):
    pdf = tmp_path / "real_bullets.pdf"
    _make_bullet_pdf(pdf, empty=False)
    with fitz.open(str(pdf)) as doc:
        violations = _check_empty_list_items(doc)
    assert violations == [], f"bullets with text flagged: {violations}"


# ── template-level invariants ────────────────────────────────────────────────


def test_every_for_loop_filters_falsy_entries():
    tags = _FOR_TAG_RE.findall(HTML_TEMPLATE)
    assert tags, "expected at least one {% for %} loop in the template"
    unguarded = [t for t in tags if not re.search(r"\sif\s", t)]
    assert not unguarded, (
        f"template loops without a falsy-entry filter (P2-34): {unguarded}"
    )


def test_methodology_lists_prefilter_trim_and_suppress_heading():
    """Whitespace-only entries must die too (map('trim') | select), and the
    enclosing <h3> + <ul> must be suppressed when nothing survives."""
    for raw, clean in (
        ("limitations", "limitations_clean"),
    ):
        expected_set = (
            "{% set " + clean + " = report." + raw + " | map('trim') | select | list %}"
        )
        assert expected_set in HTML_TEMPLATE, (
            f"methodology must pre-filter report.{raw} with map('trim') | select"
        )
        assert ("{% if " + clean + " %}") in HTML_TEMPLATE, (
            f"the <h3>/<ul> for {raw} must be suppressed when the filtered "
            "list is empty"
        )


def test_the_roster_loop_is_deleted_not_just_guarded():
    """W-09: filtering empty agent names was the P2-34 fix; the W-09 fix is
    that the template cannot reference the roster at all."""
    assert "report.agents_used" not in HTML_TEMPLATE, (
        "the client template must not read report.agents_used — the roster "
        "is operator telemetry and lives in the EngagementTelemetry artifact"
    )
    # Markup-level check: the roster HEADING element is gone. (The literal
    # phrase may still appear inside a Jinja comment, which never renders.)
    assert "<h3>Agents Used</h3>" not in HTML_TEMPLATE


# ── behavioural render ───────────────────────────────────────────────────────


def _render(limitations, key_findings=None) -> str:
    env = Environment(loader=BaseLoader(), autoescape=True)
    env.filters["md_to_html"] = lambda v: v or ""
    env.filters["clean_dict_repr"] = lambda v: v or ""
    # Mirrors the W-09 ClientReport view: recommendation is a plain wire
    # string, no confidence, no agents_used — those attributes no longer
    # exist for the template to resolve.
    report = SimpleNamespace(
        question="Should Acme enter the market?",
        recommendation="enter",
        generated_at=datetime(2026, 7, 30, tzinfo=timezone.utc),
        engagement_id="ENG-TEST",
        recommendation_rationale="",
        executive_summary="",
        key_findings=key_findings or [],
        critical_assumptions=[],
        sections=[],
        risk_analysis=None,
        limitations=limitations,
        total_sources=0,
        total_data_points=0,
    )
    palette = SimpleNamespace(cream="#F5F4EE", warm_gray="#8A8580", terracotta="#C4573A")
    template = env.from_string(HTML_TEMPLATE)
    return template.render(
        report=report,
        cover_image=None,
        section_images={},
        section_charts={},
        palette=palette,
        css_content="",
        risk_analysis_html="",
        appendix_sources_html="",
        endnotes_html="",
    )


def _methodology_region(html: str) -> str:
    start = html.find('<h2>Methodology</h2>')
    assert start != -1, "methodology chapter missing from rendered HTML"
    end = html.find('<h2>Endnotes</h2>', start)
    assert end != -1, "endnotes chapter missing from rendered HTML"
    return html[start:end]


class _EmptyLiDetector(HTMLParser):
    """Collects every <li> whose text content is empty or whitespace."""

    def __init__(self) -> None:
        super().__init__()
        self.empty_items = 0
        self._in_li = False
        self._li_text: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag == "li":
            self._in_li = True
            self._li_text = []

    def handle_endtag(self, tag):
        if tag == "li" and self._in_li:
            if not "".join(self._li_text).strip():
                self.empty_items += 1
            self._in_li = False

    def handle_data(self, data):
        if self._in_li:
            self._li_text.append(data)


def test_rendered_html_contains_no_empty_list_items():
    """The report B scenario (11 empty roster strings) is now impossible at
    the type level; the same falsy-filter contract is defended on the
    remaining list, limitations."""
    html = _render(limitations=[""] * 3)
    detector = _EmptyLiDetector()
    detector.feed(html)
    assert detector.empty_items == 0, (
        f"{detector.empty_items} empty <li> element(s) rendered (P2-34)"
    )


def test_methodology_suppresses_headings_when_lists_empty():
    region = _methodology_region(_render(limitations=[]))
    assert "<li" not in region
    assert "Agents Used" not in region
    assert "Limitations" not in region


def test_methodology_keeps_real_entries_and_drops_blanks():
    region = _methodology_region(
        _render(limitations=["Single geography in scope", " "])
    )
    items = re.findall(r"<li>(.*?)</li>", region, re.DOTALL)
    assert items == ["Single geography in scope"], (
        f"unexpected methodology list items: {items}"
    )


def test_key_findings_heading_suppressed_when_no_findings():
    html = _render(limitations=[], key_findings=[])
    assert "<h3>Key Findings</h3>" not in html, (
        "an empty Key Findings heading is the same defect class as an "
        "empty bullet: the heading must be suppressed with the list"
    )
