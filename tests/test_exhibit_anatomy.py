"""Tests for MGI exhibit anatomy enforcement (fix 4.4).

WHAT THIS FIX WAS FOR
---------------------
The audit (§3.9, fix-plan row 4.4) requires the template to enforce the MGI
four-part exhibit anatomy:

    number → action title → figure → ``Note:`` → ``Source:``

Fix 3.7 had already made the template *emit* all five parts, and it was easy to
read that as done. It was not, because **every part was independently optional**
in the Jinja source:

* ``{% if chart.caption %}`` around the action title
* ``{% if chart.source_citation or chart.note %}`` around the *whole footer*

So an exhibit arriving with no caption rendered as a numbered, sourced exhibit
with **no takeaway title**, and one arriving with neither note nor source
rendered with **no footer at all** — no hairline, no provenance. Both were
silent: no exception, no log line, and the resulting PDF still looked
deliberate. Measured before the fix, all five degenerate combinations rendered
clean:

    complete         title=1 figure=1 Note=1 Source=1 footer=1
    NO action title  title=0 figure=1 Note=1 Source=1 footer=1
    NO note          title=1 figure=1 Note=0 Source=1 footer=1
    NO source        title=1 figure=1 Note=1 Source=0 footer=1
    NO note+source   title=1 figure=1 Note=0 Source=0 footer=0

Only ``image_path`` was ever guarded (a figure-less chart is dropped upstream),
which is exactly why the other four parts needed a gate.

WHAT THESE TESTS DEFEND
-----------------------
The property is **not** "the template contains the string ``Note:``" — the
pre-4.4 template contained it too, inside an ``{% if %}`` that could skip it.
The property is that a placement missing a part **cannot reach the PDF missing
that part**. So the tests drive `_enforce_exhibit_anatomy` with degenerate
input and assert on the repaired object and on rendered HTML, not on template
text.

`TestTheAnatomyCannotBeIncomplete` is the centre of this file: each of its tests
fails if either `{% if %}` guard is restored, or if the enforcement call is
removed from `_assemble_chart_placements`.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from jinja2 import BaseLoader, Environment

from hyperion.agents.delivery.presentation_designer import (
    HTML_TEMPLATE,
    PresentationDesigner,
)
from hyperion.schemas.models import ChartPlacement

ROOT = Path(__file__).resolve().parents[1]
SRC = (
    ROOT / "hyperion" / "agents" / "delivery" / "presentation_designer.py"
).read_text()


def _placement(**kw: object) -> ChartPlacement:
    """A ChartPlacement with every anatomy part present unless overridden."""
    base: dict[str, object] = {
        "chart_id": "revenue_growth_2024",
        "section_id": "s1",
        "image_path": "/tmp/exhibit.png",
        "caption": "Revenue grew 4x while margin held",
        "note": "Note: n=42 firms",
        "source_citation": "Source: FRED series GDPC1",
    }
    base.update(kw)
    return ChartPlacement(**base)  # type: ignore[arg-type]


def _enforce(*charts: ChartPlacement) -> tuple[list[str], list[ChartPlacement]]:
    """Run the production gate over placements, returning (defects, repaired)."""
    designer = PresentationDesigner.__new__(PresentationDesigner)
    designer._log = lambda *a, **k: None  # type: ignore[method-assign]
    placements = {"s1": list(charts)}
    defects = designer._enforce_exhibit_anatomy(placements)
    return defects, placements["s1"]


def _render_exhibit(chart: ChartPlacement) -> str:
    """Render just the exhibit loop from the real HTML_TEMPLATE."""
    # The loop header carries a Jinja filter expression (P2-34 added
    # ``if chart`` so a falsy placement cannot render an empty <figure>), and
    # more filters may be added. Match the header up to its closing ``%}``
    # rather than pinning the exact expression, so this locator survives a
    # legitimate change to the guard without silently matching nothing.
    m = re.search(
        r"(\{% for chart in section_charts\[section\.id\][^%]*%\}.*?\{% endfor %\})",
        HTML_TEMPLATE,
        re.S,
    )
    assert m, "exhibit loop not found in HTML_TEMPLATE — did the markup move?"
    env = Environment(loader=BaseLoader(), autoescape=True)

    class _Section:
        id = "s1"

    return env.from_string(m.group(1)).render(
        section=_Section(), section_charts={"s1": [chart]}
    )


class TestTheAnatomyCannotBeIncomplete:
    """Each test here fails if the pre-4.4 `{% if %}` guards come back."""

    def test_missing_action_title_is_repaired_not_shipped_blank(self) -> None:
        defects, (chart,) = _enforce(_placement(caption=""))
        assert chart.caption, "a numbered exhibit shipped with no action title"
        assert any("action title" in d for d in defects), (
            f"the defect was repaired but not reported: {defects}"
        )

    def test_missing_source_is_repaired_because_benchmarks_always_carry_one(
        self,
    ) -> None:
        defects, (chart,) = _enforce(_placement(source_citation=""))
        assert chart.source_citation.strip(), "exhibit shipped with no provenance"
        assert "Source:" in chart.source_citation
        assert any("Source:" in d for d in defects), defects

    def test_missing_note_is_reported_but_never_invented(self) -> None:
        """A note says HOW a figure was built. Inventing one is a data defect."""
        defects, (chart,) = _enforce(_placement(note=""))
        assert chart.note == "", (
            f"a methodology note was fabricated: {chart.note!r} — an invented "
            f"note is the same class of defect as an invented geography"
        )
        assert any("Note:" in d for d in defects), defects

    def test_footer_renders_even_when_note_and_source_are_both_absent(self) -> None:
        """The hairline is what visually closes the exhibit in the benchmarks.

        Pre-4.4 the whole <figcaption> was wrapped in
        `{% if chart.source_citation or chart.note %}`, so this exact input
        produced an exhibit with no footer at all.
        """
        _, (chart,) = _enforce(_placement(note="", source_citation=""))
        html = _render_exhibit(chart)
        assert "exhibit-footer" in html, (
            "no footer element — the exhibit has no hairline and no provenance"
        )
        assert "Source:" in html

    def test_footer_is_unconditional_in_the_template(self) -> None:
        """Structural, and it took a failed negative control to get right.

        The behavioural test above passes even with the old
        `{% if chart.source_citation or chart.note %}` guard restored, because
        enforcement defaults the source line and so the condition is always
        true. That makes the behavioural test unable to detect the guard's
        return — it only proves the two fixes *together* work.

        Defence in depth is the point: if someone later relaxes enforcement to
        stop defaulting the source (a reasonable-looking change, since
        inventing provenance is itself a defect), the guard would silently start
        dropping footers again. Pinning the template shape means that
        combination cannot regress unnoticed.
        """
        m = re.search(r'<figcaption class="exhibit-footer">', HTML_TEMPLATE)
        assert m, "exhibit-footer element missing"
        window = HTML_TEMPLATE[max(0, m.start() - 400) : m.start()]
        assert "{% if chart.source_citation or chart.note %}" not in window, (
            "the footer is conditional again — an exhibit with neither note nor "
            "source will render with no hairline and no provenance"
        )

    def test_all_five_parts_survive_a_fully_degenerate_placement(self) -> None:
        """Number, title, figure, Note:, Source: — the whole contract at once."""
        _, (chart,) = _enforce(_placement(caption="", note="", source_citation=""))
        html = _render_exhibit(chart)
        # The number is a CSS counter, so the element carrying it is the check.
        for part, needle in (
            ("number", "exhibit-number"),
            ("action title", "exhibit-title"),
            ("figure", "exhibit-figure"),
            ("footer/hairline", "exhibit-footer"),
            ("Source:", "Source:"),
        ):
            assert needle in html, f"anatomy part missing from render: {part}"

    def test_title_element_is_not_conditional_in_the_template(self) -> None:
        """Pinned structurally: the guard's return would not fail any render
        test that happens to supply a caption, so the absence of the `{% if %}`
        is asserted directly."""
        m = re.search(r'<div class="exhibit-title">', HTML_TEMPLATE)
        assert m, "exhibit-title element missing"
        window = HTML_TEMPLATE[max(0, m.start() - 260) : m.start()]
        assert "{% if chart.caption %}" not in window, (
            "the action title is conditional again — a caption-less chart will "
            "render as a numbered exhibit with no takeaway"
        )


class TestEnforcementIsWiredIntoProduction:
    """A gate nobody calls is the audit's central failure mode."""

    def test_assemble_chart_placements_calls_the_gate(self) -> None:
        assert "_enforce_exhibit_anatomy(self._chart_placements)" in SRC, (
            "the anatomy gate exists but is never called from "
            "_assemble_chart_placements, so it defends nothing in production"
        )

    def test_gate_runs_before_placements_are_returned(self) -> None:
        call = SRC.index("_enforce_exhibit_anatomy(self._chart_placements)")
        ret = SRC.index("return self._chart_placements", call - 400)
        assert call < ret, "the gate runs after the placements are handed out"


class TestRepairPreservesGoodInput:
    """Enforcement must be a no-op on already-correct exhibits."""

    def test_complete_exhibit_is_left_untouched(self) -> None:
        original = _placement()
        defects, (chart,) = _enforce(_placement())
        assert defects == [], f"a complete exhibit was reported defective: {defects}"
        assert chart.caption == original.caption
        assert chart.note == original.note
        assert chart.source_citation == original.source_citation

    def test_existing_labels_are_not_doubled(self) -> None:
        """The template strips one leading label; enforcement must not add another."""
        _, (chart,) = _enforce(_placement())
        html = _render_exhibit(chart)
        assert html.count("Source:") == 1, f"Source: label duplicated:\n{html}"
        assert html.count("Note:") == 1, f"Note: label duplicated:\n{html}"

    @pytest.mark.parametrize("blank", ["", "   ", "\n\t "])
    def test_whitespace_only_fields_count_as_missing(self, blank: str) -> None:
        """`"  "` is not a title. A truthiness check would let it through."""
        _, (chart,) = _enforce(_placement(caption=blank, source_citation=blank))
        assert chart.caption.strip(), "whitespace-only caption survived as a title"
        assert chart.source_citation.strip()


class TestHumanisedFallbackTitle:
    """The placeholder must read as provisional, not as a real MBB takeaway."""

    @pytest.mark.parametrize(
        ("chart_id", "expected"),
        [
            ("revenue_growth_2024", "Revenue growth 2024"),
            ("market-share-by-region", "Market share by region"),
            ("tornado__sensitivity", "Tornado sensitivity"),
            ("", "Exhibit"),
        ],
    )
    def test_chart_id_is_humanised(self, chart_id: str, expected: str) -> None:
        assert PresentationDesigner._humanise_chart_id(chart_id) == expected

    def test_fallback_is_used_verbatim_as_the_caption(self) -> None:
        _, (chart,) = _enforce(_placement(chart_id="cost_curve_shift", caption=""))
        assert chart.caption == "Cost curve shift"


class TestDefectsAreReportedForEveryExhibit:
    def test_multiple_exhibits_each_report_their_own_defects(self) -> None:
        defects, charts = _enforce(
            _placement(chart_id="a", caption=""),
            _placement(chart_id="b", source_citation=""),
        )
        assert all(c.caption.strip() for c in charts)
        assert all(c.source_citation.strip() for c in charts)
        assert any("a" in d and "action title" in d for d in defects), defects
        assert any("b" in d and "Source:" in d for d in defects), defects

    def test_defect_strings_name_the_section_and_chart(self) -> None:
        """A defect you cannot locate is a log line, not a diagnostic."""
        defects, _ = _enforce(_placement(chart_id="mekko_1", caption=""))
        assert any("s1/mekko_1" in d for d in defects), defects
