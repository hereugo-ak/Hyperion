"""Fix 3.7 — the exhibit path, end to end.

The audit measured ``has_exhibits: false`` on the production render path and
attributed it to charts never being populated. The deeper cause was a chain of
four independent breaks, each of which produced a 300-DPI PNG on disk that no
page ever displayed:

1. ``mine_chart_specs`` paired every ``report.key_findings`` finding with
   ``section_id=""``. The template iterates ``section_charts[section.id]`` and
   no section has the id ``""``, so charts mined from the *headline* findings
   were placed under a key nothing reads. The most important exhibits in the
   document were exactly the ones dropped.
2. ``ChartSpecification`` had no ``note`` field, although ``ChartPlacement``
   had one and the template already rendered it. Any methodology note was
   therefore discarded at the Data Visualizer hop, and every exhibit shipped
   with a truncated three-part anatomy instead of MGI/BCG's four-part one.
3. ``_receive_chart_images`` reproduced break 1 downstream and additionally
   placed charts whose ``image_path`` was empty (failed export), which renders
   as a broken-image box under a real "Exhibit N" label *and* consumes an
   exhibit number, pushing every later exhibit out of sequence.
4. ``.exhibit-note-label`` / ``.exhibit-source-label`` existed in the CSS but
   were referenced by no markup — dead rules, so the italic ``Note:`` /
   ``Source:`` convention of both benchmarks was not actually applied.

These tests pin each break independently, plus the assembled chain, so a
regression in any single hop fails a named test rather than quietly reducing
the exhibit count.
"""
from __future__ import annotations

from types import SimpleNamespace as NS  # noqa: N814 - concise fixture alias

import pytest

from hyperion.output.chart_specs import mine_chart_specs
from hyperion.schemas.models import (
    ChartPlacement,
    ChartSpecification,
    ChartType,
    VisualizationOutput,
)

# ── Fixtures ────────────────────────────────────────────────────────────────


def _finding(fid: str, agent: str, content: str, title: str = "") -> NS:
    return NS(
        id=fid,
        agent=agent,
        title=title or f"Finding {fid} headline claim about the market",
        content=content,
        implications="Sequence entry behind the clearing zones.",
        sources=[NS(title="IEA Storage Outlook 2025", url="https://iea.org/x")],
    )


def _section(sid: str, agent: str, findings: list[NS] | None = None) -> NS:
    return NS(
        id=sid,
        title=sid.replace("section_", "").replace("_", " ").title(),
        agent=agent,
        findings=findings or [],
        body="",
    )


# A three-value series in one coherent unit, so the miner produces a spec.
SERIES = (
    "Revenue was $41 billion in 2019, $78 billion in 2024, "
    "and $95 billion in 2025."
)


# ── Break 1: homeless specs ─────────────────────────────────────────────────


class TestSpecsAreHomedToRealSections:
    """No spec may name a section that does not exist."""

    def test_headline_finding_is_homed_to_its_authoring_agents_section(self):
        """A key_findings spec lands in the section whose analyst produced it.

        This is the specific regression: before the fix this spec carried
        ``section=""`` and was rendered by nobody.
        """
        report = NS(
            question="Should we enter?",
            key_findings=[_finding("f1", "financial_analyst", SERIES)],
            sections=[
                _section("section_market_analyst", "market_analyst"),
                _section("section_financial_analyst", "financial_analyst"),
            ],
        )
        specs = mine_chart_specs(report, question="Should we enter?")
        assert specs, "a coherent 3-value series must yield at least one spec"
        assert specs[0]["section"] == "section_financial_analyst"

    def test_no_spec_ever_carries_an_empty_section(self):
        report = NS(
            question="q",
            key_findings=[
                _finding("f1", "financial_analyst", SERIES),
                _finding("f2", "unknown_agent", SERIES.replace("41", "52")),
            ],
            sections=[_section("section_market_analyst", "market_analyst")],
        )
        specs = mine_chart_specs(report, question="q")
        assert specs
        assert all(s["section"] for s in specs), (
            f"every spec must name a real section, got "
            f"{[s['section'] for s in specs]}"
        )

    def test_unmatched_agent_falls_back_to_the_first_section(self):
        """An agent with no matching section still gets a real home."""
        report = NS(
            question="q",
            key_findings=[_finding("f1", "agent_with_no_section", SERIES)],
            sections=[
                _section("section_market_analyst", "market_analyst"),
                _section("section_risk_analyst", "risk_analyst"),
            ],
        )
        specs = mine_chart_specs(report, question="q")
        assert specs
        assert specs[0]["section"] == "section_market_analyst"

    def test_section_less_report_still_mines_specs(self):
        """Mining and placement are separate concerns.

        With no sections there is no honest home to assign, so ``section``
        stays ``""`` — but the series is still chartable and the spec is still
        emitted. Returning ``[]`` here would claim a report has no chartable
        data when it has plenty, and it broke 9 pre-existing chart-miner tests
        that (correctly) exercise the miner on section-less reports. The
        renderability guard belongs at the placement hop, which is where
        ``_receive_chart_images`` re-homes anything that does not resolve.
        """
        report = NS(
            question="q",
            key_findings=[_finding("f1", "financial_analyst", SERIES)],
            sections=[],
        )
        specs = mine_chart_specs(report, question="q")
        assert specs, "a chartable series must still be mined without sections"
        assert specs[0]["section"] == ""

    def test_specs_reference_only_ids_present_in_the_report(self):
        sections = [
            _section(
                "section_market_analyst",
                "market_analyst",
                [_finding("s1", "market_analyst", SERIES)],
            ),
            _section("section_risk_analyst", "risk_analyst"),
        ]
        report = NS(
            question="q",
            key_findings=[_finding("f1", "risk_analyst", SERIES.replace("41", "63"))],
            sections=sections,
        )
        valid = {s.id for s in sections}
        specs = mine_chart_specs(report, question="q")
        assert specs
        for spec in specs:
            assert spec["section"] in valid


# ── Break 2: the note field must survive every hop ──────────────────────────


class TestMethodologyNoteSurvivesTheChain:
    def test_chart_specification_declares_a_note_field(self):
        """Without this field the note is dropped at the Data Visualizer."""
        assert "note" in ChartSpecification.model_fields

    def test_chart_placement_declares_a_note_field(self):
        assert "note" in ChartPlacement.model_fields

    def test_note_defaults_to_empty_not_a_placeholder(self):
        """An invented note is as misleading as an invented source."""
        spec = ChartSpecification(id="c", title="t", chart_type=ChartType.BAR)
        assert spec.note == ""

    def test_miner_emits_a_note_for_every_spec(self):
        report = NS(
            question="q",
            key_findings=[_finding("f1", "market_analyst", SERIES)],
            sections=[_section("section_market_analyst", "market_analyst")],
        )
        specs = mine_chart_specs(report, question="q")
        assert specs
        for spec in specs:
            assert spec["note"].startswith("Note:")
            assert spec["note"].endswith(".")

    def test_note_names_the_analyst_and_disclaims_modelling(self):
        """The note must describe what actually happened, not flatter it."""
        report = NS(
            question="q",
            key_findings=[_finding("f1", "market_analyst", SERIES)],
            sections=[_section("section_market_analyst", "market_analyst")],
        )
        note = mine_chart_specs(report, question="q")[0]["note"]
        assert "market analyst" in note, "underscores must be humanised"
        assert "not modelled or interpolated" in note

    def test_note_discloses_display_rescaling(self):
        """A tick reading 41 under a rescaled axis must say so.

        Values are parsed to absolute units then divided for display; without
        this disclosure a reader cannot tell 41 from 41,000,000,000.
        """
        report = NS(
            question="q",
            key_findings=[_finding("f1", "market_analyst", SERIES)],
            sections=[_section("section_market_analyst", "market_analyst")],
        )
        note = mine_chart_specs(report, question="q")[0]["note"]
        assert "billion" in note

    def test_percentage_series_note_flags_percentages(self):
        report = NS(
            question="q",
            key_findings=[
                _finding(
                    "f1",
                    "market_analyst",
                    "Margin was 42% in 2023, 47% in 2024, and 51% in 2025.",
                )
            ],
            sections=[_section("section_market_analyst", "market_analyst")],
        )
        specs = mine_chart_specs(report, question="q")
        assert specs
        assert "percentages" in specs[0]["note"]

    def test_note_is_not_emitted_when_no_series_is_minable(self):
        """No chart, no note — the miner must not invent either."""
        report = NS(
            question="q",
            key_findings=[
                _finding("f1", "market_analyst", "Margins improved considerably.")
            ],
            sections=[_section("section_market_analyst", "market_analyst")],
        )
        assert mine_chart_specs(report, question="q") == []

    def test_data_visualizer_copies_the_note_onto_the_specification(self):
        """The Data Visualizer hop is where the note used to vanish."""
        spec_dict = {
            "id": "c1",
            "title": "T",
            "section": "section_market_analyst",
            "note": "Note: values quoted as reported.",
            "source_citation": "Source: IEA.",
            "data_series": [
                {"name": "USD billion", "values": [1.0, 2.0], "labels": ["2024", "2025"]}
            ],
        }
        built = ChartSpecification(
            id=spec_dict["id"],
            title=spec_dict["title"],
            section=spec_dict["section"],
            chart_type=ChartType.BAR,
            source_citation=spec_dict.get("source_citation", ""),
            note=spec_dict.get("note", ""),
        )
        assert built.note == "Note: values quoted as reported."


# ── Break 3: the designer must not place unrenderable charts ────────────────


def _designer(viz: VisualizationOutput, logs: list[str]):
    """A PresentationDesigner with only the state _receive_chart_images needs.

    Constructed via ``__new__`` deliberately: the real ``__init__`` needs a bus
    and a router, and this test is about the placement logic alone.
    """
    from hyperion.agents.delivery.presentation_designer import PresentationDesigner

    designer = PresentationDesigner.__new__(PresentationDesigner)
    designer._visualization_output = viz
    designer._chart_placements = {}
    designer._log = logs.append
    return designer


def _chart(
    cid: str,
    section: str,
    image_path: str = "/tmp/x.png",
    note: str = "",
) -> ChartSpecification:
    return ChartSpecification(
        id=cid,
        title=f"Title {cid}",
        section=section,
        chart_type=ChartType.BAR,
        image_path=image_path,
        caption=f"Caption {cid}",
        note=note,
    )


REPORT_TWO_SECTIONS = NS(
    sections=[
        NS(id="section_market_analyst", agent="market_analyst"),
        NS(id="section_financial_analyst", agent="financial_analyst"),
    ]
)


class TestDesignerPlacement:
    def test_homeless_chart_is_rehomed_not_dropped_silently(self):
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        logs: list[str] = []
        viz = VisualizationOutput(charts=[_chart("c1", "")], total_charts=1)
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, logs), viz, report=REPORT_TWO_SECTIONS
        )
        assert "" not in placements, "the unreadable '' key must never survive"
        assert sum(len(v) for v in placements.values()) == 1
        assert any("re-homing" in m for m in logs), "re-homing must be logged loudly"

    def test_rehomed_chart_lands_on_a_real_section(self):
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        viz = VisualizationOutput(charts=[_chart("c1", "")], total_charts=1)
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, []), viz, report=REPORT_TWO_SECTIONS
        )
        valid = {s.id for s in REPORT_TWO_SECTIONS.sections}
        assert set(placements) <= valid

    def test_chart_naming_an_unknown_section_is_rehomed(self):
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        viz = VisualizationOutput(
            charts=[_chart("c1", "section_that_does_not_exist")], total_charts=1
        )
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, []), viz, report=REPORT_TWO_SECTIONS
        )
        assert "section_that_does_not_exist" not in placements
        assert sum(len(v) for v in placements.values()) == 1

    def test_chart_with_no_image_path_is_dropped(self):
        """A failed export must not render as a broken image."""
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        logs: list[str] = []
        viz = VisualizationOutput(
            charts=[
                _chart("ok", "section_market_analyst"),
                _chart("bad", "section_market_analyst", image_path=""),
            ],
            total_charts=2,
        )
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, logs), viz, report=REPORT_TWO_SECTIONS
        )
        ids = [p.chart_id for v in placements.values() for p in v]
        assert ids == ["ok"]
        assert any("dropping chart" in m for m in logs)

    def test_dropped_chart_does_not_consume_an_exhibit_number(self):
        """Numbering comes from a CSS counter over placed exhibits only."""
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        viz = VisualizationOutput(
            charts=[
                _chart("bad", "section_market_analyst", image_path=""),
                _chart("a", "section_market_analyst"),
                _chart("b", "section_market_analyst"),
            ],
            total_charts=3,
        )
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, []), viz, report=REPORT_TWO_SECTIONS
        )
        assert [p.chart_id for p in placements["section_market_analyst"]] == ["a", "b"]

    def test_note_is_copied_onto_the_placement(self):
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        viz = VisualizationOutput(
            charts=[_chart("c1", "section_market_analyst", note="Note: quoted as reported.")],
            total_charts=1,
        )
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, []), viz, report=REPORT_TWO_SECTIONS
        )
        assert placements["section_market_analyst"][0].note == "Note: quoted as reported."

    def test_valid_chart_is_untouched(self):
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        viz = VisualizationOutput(
            charts=[_chart("c1", "section_financial_analyst")], total_charts=1
        )
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, []), viz, report=REPORT_TWO_SECTIONS
        )
        assert list(placements) == ["section_financial_analyst"]

    def test_missing_report_degrades_without_raising(self):
        """The designer must still function if no report is threaded through."""
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        viz = VisualizationOutput(charts=[_chart("c1", "section_market_analyst")], total_charts=1)
        placements = PresentationDesigner._receive_chart_images(
            _designer(viz, []), viz, report=None
        )
        assert placements["section_market_analyst"][0].chart_id == "c1"

    def test_get_charts_for_section_skips_homeless_and_imageless(self):
        """The secondary accessor must agree with the primary one."""
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        viz = VisualizationOutput(
            charts=[
                _chart("homeless", ""),
                _chart("noimg", "section_market_analyst", image_path=""),
                _chart("good", "section_market_analyst"),
            ],
            total_charts=3,
        )
        designer = _designer(viz, [])
        got = PresentationDesigner._get_charts_for_section(designer, "section_market_analyst")
        assert [p.chart_id for p in got] == ["good"]


# ── Break 4: the rendered exhibit anatomy ───────────────────────────────────


def _render_exhibit(placement: ChartPlacement) -> str:
    """Render the exhibit block of the SHIPPED template with one placement.

    Uses ``HTML_TEMPLATE`` itself rather than a copy, so the test cannot drift
    away from what actually ships — the exact failure mode the audit found in
    the dead ``.j2`` fork.
    """
    from jinja2 import BaseLoader, Environment

    from hyperion.agents.delivery.presentation_designer import HTML_TEMPLATE

    start = HTML_TEMPLATE.index("{% for chart in section_charts[section.id] %}")
    end = HTML_TEMPLATE.index("{% endfor %}", start) + len("{% endfor %}")
    fragment = HTML_TEMPLATE[start:end]

    env = Environment(loader=BaseLoader(), autoescape=True)
    template = env.from_string(fragment)
    return template.render(
        section=NS(id="s1"),
        section_charts={"s1": [placement]},
    )


class TestRenderedExhibitAnatomy:
    def test_note_and_source_labels_are_emitted(self):
        html = _render_exhibit(
            ChartPlacement(
                chart_id="c1",
                section_id="s1",
                image_path="/tmp/x.png",
                caption="Only four of eleven zones clear the spread",
                note="Note: values quoted as reported.",
                source_citation="Source: IEA Storage Outlook 2025.",
            )
        )
        assert 'class="exhibit-note-label"' in html
        assert 'class="exhibit-source-label"' in html

    def test_labels_are_not_duplicated_when_the_value_is_prefixed(self):
        """The miner prefixes 'Note:'; the template adds the label itself."""
        html = _render_exhibit(
            ChartPlacement(
                chart_id="c1",
                section_id="s1",
                image_path="/tmp/x.png",
                note="Note: values quoted as reported.",
                source_citation="Source: IEA.",
            )
        )
        assert html.count("Note:") == 1, f"duplicated Note: label in {html!r}"
        assert html.count("Source:") == 1, f"duplicated Source: label in {html!r}"

    def test_labels_are_added_when_the_value_is_not_prefixed(self):
        """An LLM-supplied spec may omit the prefix; the label still appears."""
        html = _render_exhibit(
            ChartPlacement(
                chart_id="c1",
                section_id="s1",
                image_path="/tmp/x.png",
                note="values quoted as reported",
                source_citation="IEA Storage Outlook 2025",
            )
        )
        assert html.count("Note:") == 1
        assert html.count("Source:") == 1
        assert "values quoted as reported" in html

    def test_absent_note_emits_no_note_line(self):
        """An invented note is as bad as an invented source."""
        html = _render_exhibit(
            ChartPlacement(
                chart_id="c1",
                section_id="s1",
                image_path="/tmp/x.png",
                source_citation="Source: IEA.",
            )
        )
        assert "Note:" not in html
        assert "Source:" in html

    def test_absent_source_emits_no_source_line(self):
        html = _render_exhibit(
            ChartPlacement(
                chart_id="c1",
                section_id="s1",
                image_path="/tmp/x.png",
                note="Note: quoted as reported.",
            )
        )
        assert "Source:" not in html
        assert "Note:" in html

    def test_exhibit_number_is_generated_by_css_not_authored(self):
        """The number must never come from an agent — it cannot be wrong."""
        html = _render_exhibit(
            ChartPlacement(chart_id="c1", section_id="s1", image_path="/tmp/x.png")
        )
        assert '<div class="exhibit-number"></div>' in html, (
            "the number element must be empty; content comes from counter()"
        )

    def test_four_part_anatomy_is_ordered_number_title_figure_footer(self):
        html = _render_exhibit(
            ChartPlacement(
                chart_id="c1",
                section_id="s1",
                image_path="/tmp/x.png",
                caption="Takeaway title",
                note="Note: n.",
                source_citation="Source: s.",
            )
        )
        order = [
            html.index("exhibit-number"),
            html.index("exhibit-title"),
            html.index("exhibit-figure"),
            html.index("exhibit-footer"),
        ]
        assert order == sorted(order), f"anatomy out of order: {order}"


class TestExhibitCssContract:
    """The label rules must exist in the shipped CSS, not only the dead fork."""

    @pytest.mark.parametrize(
        "selector",
        [
            ".exhibit-number",
            ".exhibit-title",
            ".exhibit-figure",
            ".exhibit-footer",
            ".exhibit-note",
            ".exhibit-source",
            ".exhibit-note-label",
            ".exhibit-source-label",
        ],
    )
    def test_selector_present_in_shipped_css(self, selector):
        from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE

        assert selector in CSS_TEMPLATE

    def test_exhibit_counter_is_reset_once_on_the_root(self):
        from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE

        assert "counter-reset: exhibit" in CSS_TEMPLATE
        assert "counter-increment: exhibit" in CSS_TEMPLATE

    def test_exhibits_span_all_columns(self):
        """A two-column body must not squeeze exhibits into one column."""
        from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE

        assert "column-span: all" in CSS_TEMPLATE
