"""D5.1c — a chart must draw every series it was given.

THE DEFECT THESE TESTS PIN
--------------------------
Twelve chart creators in ``hyperion/output/charts.py`` (plus both matplotlib
fallback branches and the HTML data-table fallback) iterated::

    for i, (y_values, name) in enumerate(zip(spec.y_data, spec.series_names)):

``zip`` stops at the shorter argument and ``ChartSpec.series_names`` defaults to
``[]``. So any spec constructed with data but without names produced a figure
with **zero traces**, and any spec with fewer names than series silently lost
the surplus series. Measured live on the pre-fix code::

    ChartSpec(chart_type="bar", x_data=["a","b"], y_data=[[1,2],[3,4]])
        -> _create_bar -> 0 traces          (2 series of real data in)
    ChartSpec(chart_type="line", y_data=[3 rows], series_names=["only"])
        -> _create_line -> 1 trace          (2 series dropped)

Three things kept it invisible, and each gets its own test class below:

1. ``generate()`` returned ``success=True`` for a trace-less figure — kaleido
   renders one to a perfectly valid PNG of an empty axis frame, so nothing
   raised and nothing logged. A blank exhibit shipped under a real title with a
   real ``Note:`` and ``Source:`` beneath it.
2. Tier 2 (matplotlib) contained the same ``zip``, so degrading did not recover
   the data.
3. Tier 3 (the "never blank" HTML data table) drove both its header cells and
   its value columns off ``series_names``, so it emitted a table of category
   labels with every number missing.

WHY THE INVARIANT IS TESTED STRUCTURALLY TOO
--------------------------------------------
``TestNoCreatorZipsNamesAgainstData`` walks the AST of every ``_create_*``
method rather than only checking rendered output. Twelve sites had this bug;
fixing twelve call sites without banning the thirteenth leaves the door open,
and a new chart type is added to this module regularly (fix 4.3 added five at
once). The structural test is what makes the fix hold.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

from hyperion.output import charts as charts_mod
from hyperion.output.charts import ChartGenerator, ChartSpec

CHARTS_SRC = Path(charts_mod.__file__)


def _go():
    """Import plotly.graph_objects or skip — the creators take `go` as a param."""
    return pytest.importorskip("plotly.graph_objects")


def _trace_count(fig) -> int:
    return len(fig.to_plotly_json().get("data", []))


# Chart types whose creators consume `y_data` as one-row-per-series and are
# therefore expected to emit exactly `len(y_data)` traces. The MBB types added
# by fix 4.3 (tornado, marimekko, football_field, growth_share, bubble) read
# `y_data` positionally — `y_data[0]` is widths, `y_data[1:]` are segments — so
# their trace count is deliberately NOT len(y_data) and they are covered
# separately in TestPositionalCreatorsStillDrawSomething.
ONE_TRACE_PER_SERIES = ["bar", "line", "scatter", "histogram", "stacked_bar", "radar"]


class TestEverySeriesIsDrawn:
    """The count of traces is driven by `y_data`, never by `series_names`."""

    @pytest.mark.parametrize("chart_type", ONE_TRACE_PER_SERIES)
    def test_two_series_no_names_draws_two_traces(self, chart_type):
        """THE ORIGINAL BUG. No names supplied -> pre-fix this drew nothing."""
        go = _go()
        spec = ChartSpec(
            chart_type=chart_type,
            title="Unnamed series",
            x_data=["2023", "2024"],
            y_data=[[10, 20], [30, 40]],
        )
        fig = ChartGenerator()._get_chart_creator(chart_type)(spec, go)
        assert _trace_count(fig) == 2, (
            f"{chart_type}: 2 series of data in, {_trace_count(fig)} traces out. "
            "series_names is a label, not a gate on whether the data exists."
        )

    @pytest.mark.parametrize("chart_type", ONE_TRACE_PER_SERIES)
    def test_three_series_one_name_draws_three_traces(self, chart_type):
        """Partial names must not truncate. Pre-fix this drew exactly 1."""
        go = _go()
        spec = ChartSpec(
            chart_type=chart_type,
            title="Partially named",
            x_data=["2023", "2024"],
            y_data=[[1, 2], [3, 4], [5, 6]],
            series_names=["Only the first"],
        )
        fig = ChartGenerator()._get_chart_creator(chart_type)(spec, go)
        assert _trace_count(fig) == 3

    @pytest.mark.parametrize("chart_type", ONE_TRACE_PER_SERIES)
    def test_surplus_names_do_not_invent_series(self, chart_type):
        """The converse. Three names, one series -> one trace, not three.

        Without this, `series_pairs` could have been written as a zip-longest
        over names, which would fabricate empty series — an invented exhibit
        row is worse than a missing one because it looks like a measurement.
        """
        go = _go()
        spec = ChartSpec(
            chart_type=chart_type,
            title="Over-named",
            x_data=["2023", "2024"],
            y_data=[[1, 2]],
            series_names=["a", "b", "c"],
        )
        fig = ChartGenerator()._get_chart_creator(chart_type)(spec, go)
        assert _trace_count(fig) == 1

    def test_no_data_draws_no_traces(self):
        """Empty `y_data` must stay empty — no placeholder series."""
        go = _go()
        spec = ChartSpec(chart_type="bar", title="Nothing", x_data=["a"], y_data=[])
        fig = ChartGenerator()._get_chart_creator("bar")(spec, go)
        assert _trace_count(fig) == 0


class TestSeriesPairsContract:
    """`ChartSpec.series_pairs()` is the single pairing authority."""

    def test_one_pair_per_y_data_row(self):
        spec = ChartSpec(y_data=[[1], [2], [3]])
        assert len(spec.series_pairs()) == 3

    def test_supplied_names_are_preserved_in_order(self):
        spec = ChartSpec(y_data=[[1], [2]], series_names=["Revenue", "Cost"])
        assert [n for _, n in spec.series_pairs()] == ["Revenue", "Cost"]

    def test_missing_names_are_generated_not_skipped(self):
        spec = ChartSpec(y_data=[[1], [2], [3]], series_names=["Revenue"])
        assert [n for _, n in spec.series_pairs()] == ["Revenue", "Series 2", "Series 3"]

    def test_blank_and_whitespace_names_are_replaced(self):
        """An empty string is not a usable legend entry; Plotly renders it as
        `trace 0`. It must be treated as absent, not passed through."""
        spec = ChartSpec(y_data=[[1], [2]], series_names=["", "   "])
        assert [n for _, n in spec.series_pairs()] == ["Series 1", "Series 2"]

    def test_values_are_the_original_rows(self):
        rows = [[1, 2], [3, 4]]
        spec = ChartSpec(y_data=rows)
        assert [v for v, _ in spec.series_pairs()] == rows

    def test_series_count_ignores_names(self):
        assert ChartSpec(y_data=[[1], [2], [3]], series_names=["x"]).series_count() == 3
        assert ChartSpec(y_data=[], series_names=["x", "y"]).series_count() == 0


class TestColorsAndLegendFollowTheData:
    """Both were keyed off `len(series_names)` and so disagreed with reality."""

    def test_colors_cover_every_series_when_unnamed(self):
        """Pre-fix returned ONE colour for three unnamed series, so series 2
        and 3 were drawn in series 1's colour — or raised IndexError."""
        colors = ChartGenerator()._get_colors(ChartSpec(y_data=[[1], [2], [3]]))
        assert len(colors) == 3
        assert len(set(colors)) == 3, "each series needs a distinguishable colour"

    def test_colors_never_empty_for_a_dataless_spec(self):
        assert len(ChartGenerator()._get_colors(ChartSpec())) >= 1

    def test_legend_shows_for_multi_series_without_names(self):
        go = _go()
        spec = ChartSpec(chart_type="bar", x_data=["a"], y_data=[[1], [2]])
        gen = ChartGenerator()
        fig = gen._apply_brand_styling(gen._get_chart_creator("bar")(spec, go), spec)
        assert fig.to_plotly_json()["layout"]["showlegend"] is True

    def test_legend_hidden_for_single_series(self):
        """Tufte: a legend naming one thing is chartjunk."""
        go = _go()
        spec = ChartSpec(chart_type="bar", x_data=["a"], y_data=[[1]])
        gen = ChartGenerator()
        fig = gen._apply_brand_styling(gen._get_chart_creator("bar")(spec, go), spec)
        assert fig.to_plotly_json()["layout"]["showlegend"] is False


class TestDataTableFallbackKeepsTheNumbers:
    """Tier 3 is the "never blank" tier. It was blanking the numbers."""

    def test_every_series_gets_a_column(self):
        spec = ChartSpec(
            chart_type="bar", title="Tier3 columns", x_label="Segment",
            x_data=["a", "b"], y_data=[[1, 2], [3, 4]],
        )
        result = ChartGenerator()._generate_data_table(spec)
        assert result.success
        html = Path(result.image_path).read_text(encoding="utf-8")
        # 1 category header + 2 series headers.
        assert html.count("<th") == 3, "pre-fix emitted 1 header and no value columns"
        # 2 rows x (1 label + 2 values).
        assert html.count("<td") == 6

    def test_values_actually_appear(self):
        spec = ChartSpec(
            chart_type="bar", title="Tier3 values",
            x_data=["a", "b"], y_data=[[11, 22], [33, 44]],
        )
        result = ChartGenerator()._generate_data_table(spec)
        html = Path(result.image_path).read_text(encoding="utf-8")
        for v in ("11", "22", "33", "44"):
            assert f">{v}</td>" in html, f"value {v} missing from the data table"

    def test_html_is_escaped(self):
        """LLM prose and mined finding text routinely contain `&` and `<`.
        Unescaped, a stray `<` swallows the rest of the row once this fragment
        is embedded in the report HTML."""
        spec = ChartSpec(
            chart_type="bar", title="M&A at <5% growth", x_label="A & B",
            x_data=["<script>"], y_data=[[1]], series_names=["Cost & Fees"],
            source="Reuters & FT",
        )
        result = ChartGenerator()._generate_data_table(spec)
        html = Path(result.image_path).read_text(encoding="utf-8")
        assert "M&amp;A at &lt;5% growth" in html
        assert "A &amp; B" in html
        assert "Cost &amp; Fees" in html
        assert "Reuters &amp; FT" in html
        assert "<script>" not in html, "raw tag from data reached the output"


class TestBlankExhibitIsNotASuccess:
    """`generate()` must refuse to report success for a trace-less figure.

    This is the check whose absence let the whole defect ship: a valid PNG of
    an empty axis frame is indistinguishable from a working chart to every
    caller downstream.

    🔴 THESE TESTS ARE WRITTEN THE WAY THEY ARE BECAUSE MY FIRST VERSION WAS
    A FALSE PASS. It called the real `generate()` and asserted the result did
    not come from Tier 1. That passed with the guard deliberately deleted —
    not because the code was correct, but because **kaleido is version-broken
    in this environment** (plotly 6.0.1 vs kaleido 1.0.0), so `write_image`
    raises regardless and Tier 1 can never succeed here anyway. The test was
    measuring the environment, not the guard.

    So Tier 1's export is stubbed to *succeed* below. That is the only way to
    put the guard on the only path where it matters: a host where kaleido
    works, which is every host the user actually renders reports on.
    """

    class _EmptyFigureGenerator(ChartGenerator):
        """Tier 1 produces a figure with no traces despite data being present.

        `_import_plotly` is overridden to return a `pio` whose `write_image`
        silently succeeds, simulating a WORKING kaleido. Without this the
        guard is untestable in this sandbox (see the class docstring).
        """

        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            self.exported: list[str] = []

        def _get_chart_creator(self, chart_type):
            return lambda spec, go: go.Figure()

        def _import_plotly(self):
            go, _real_pio = super()._import_plotly()

            outer = self

            class _FakePio:
                @staticmethod
                def write_image(fig, path, **kwargs):
                    # A working kaleido writes a real file and returns None.
                    outer.exported.append(str(path))
                    Path(path).parent.mkdir(parents=True, exist_ok=True)
                    Path(path).write_bytes(b"\x89PNG\r\n\x1a\n")  # PNG magic
                    return None

            return go, _FakePio()

    def test_tier1_refuses_to_export_an_empty_figure(self):
        """With a WORKING exporter, a trace-less figure must not be exported.

        The three tiers are distinguishable by filename — Tier 1 writes
        `<title>.png`, Tier 2 `<title>_mpl.png`, Tier 3 `<title>_table.html` —
        so "which tier answered" is checkable rather than inferred.
        """
        _go()
        gen = self._EmptyFigureGenerator()
        spec = ChartSpec(chart_type="bar", title="D51c blank guard",
                         x_data=["a"], y_data=[[1]])
        result = gen.generate(spec)

        tier1_path = str(gen._output_dir / "d51c_blank_guard.png")
        assert tier1_path not in gen.exported, (
            "Tier 1 exported a trace-less figure even though its exporter worked — "
            "this is exactly how a blank exhibit shipped under a real title, "
            "Note: and Source:"
        )
        assert result.image_path != tier1_path

    def test_a_lower_tier_recovers_the_data(self):
        """The guard must not merely refuse — the numbers must still reach the
        report. A guard that turns a blank chart into no chart at all would
        trade a silent defect for a visible regression."""
        _go()
        gen = self._EmptyFigureGenerator()
        spec = ChartSpec(chart_type="bar", title="D51c blank recovery",
                         x_data=["a", "b"], y_data=[[7, 8]])
        result = gen.generate(spec)
        assert result.success, f"no tier recovered the exhibit: {result.error}"
        assert Path(result.image_path).exists()

    def test_a_figure_with_traces_is_exported_by_tier1(self):
        """The positive control for the stub itself.

        Without this, `test_tier1_refuses_to_export_an_empty_figure` would
        also pass if the stub simply never worked — which is precisely the
        false-pass mode described in the class docstring, one level down.
        """
        go = _go()

        class _RealFigureGenerator(self._EmptyFigureGenerator):
            def _get_chart_creator(self, chart_type):
                return ChartGenerator._get_chart_creator(self, chart_type)

        gen = _RealFigureGenerator()
        spec = ChartSpec(chart_type="bar", title="D51c positive control",
                         x_data=["a", "b"], y_data=[[1, 2]])
        result = gen.generate(spec)
        assert result.success
        assert gen.exported, "the stubbed exporter was never called at all"
        assert result.image_path == gen.exported[0]
        assert result.image_path.endswith("d51c_positive_control.png")

    def test_guard_does_not_fire_when_there_is_no_data_to_draw(self):
        """A spec with no `y_data` legitimately has nothing to plot. The guard
        must not turn "nothing was asked for" into an error — a fix that
        shouts on every empty input is noise, and noise is how the next real
        signal gets ignored."""
        _go()
        gen = self._EmptyFigureGenerator()
        result = gen.generate(ChartSpec(chart_type="bar", title="No data at all", y_data=[]))
        assert "0 traces" not in (result.error or "")
        assert result.success, "an intentionally empty spec is not an error"


class TestNoCreatorZipsNamesAgainstData:
    """The structural invariant that stops the NEXT chart type reintroducing it.

    Twelve call sites had this bug. Fixing twelve without banning the
    thirteenth is not a fix, and this module gains chart types regularly.
    """

    def test_no_zip_of_y_data_against_series_names_anywhere(self):
        tree = ast.parse(CHARTS_SRC.read_text(encoding="utf-8"))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and getattr(node.func, "id", "") == "zip"):
                continue
            args = {ast.unparse(a) for a in node.args}
            if "spec.y_data" in args and "spec.series_names" in args:
                offenders.append(f"line {node.lineno}: {ast.unparse(node)}")
        assert not offenders, (
            "zip(y_data, series_names) truncates the data to the number of "
            f"labels. Use spec.series_pairs(). Offenders: {offenders}"
        )

    def test_no_creator_iterates_series_names_directly(self):
        """`for name in spec.series_names` has the same effect as the zip: it
        makes the label list decide how many series exist."""
        tree = ast.parse(CHARTS_SRC.read_text(encoding="utf-8"))
        offenders: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.For) and ast.unparse(node.iter) == "spec.series_names":
                offenders.append(f"line {node.lineno}")
        assert not offenders, (
            f"iterate spec.series_pairs(), not spec.series_names: {offenders}"
        )

    def test_no_range_len_series_names_loop(self):
        """`for i in range(len(spec.series_names))` — the data-table variant."""
        src = CHARTS_SRC.read_text(encoding="utf-8")
        assert "range(len(spec.series_names))" not in src

    def test_every_creator_is_reachable_and_callable(self):
        """Guards against a creator being registered under a name that
        `_get_chart_creator` never returns — the failure mode fix 5.1b found
        in the schema layer, in this module's dispatch table."""
        gen = ChartGenerator()
        registered = [
            n.removeprefix("_create_")
            for n, _ in inspect.getmembers(gen, inspect.ismethod)
            if n.startswith("_create_")
        ]
        assert registered, "no chart creators found — dispatch table moved?"
        for chart_type in ONE_TRACE_PER_SERIES:
            assert callable(gen._get_chart_creator(chart_type))


class TestPositionalCreatorsStillDrawSomething:
    """The MBB types (fix 4.3) read `y_data` positionally, not per-series.

    Their trace count is deliberately not `len(y_data)`, so they are excluded
    from the per-series assertions above — but they must still draw, and they
    must still not depend on `series_names` being populated. `_create_tornado`
    and `_create_marimekko` index `series_names[0]` / `[1:]`, which is safe
    only because both guard the length; these tests pin that.
    """

    @pytest.mark.parametrize("chart_type", ["tornado", "marimekko", "football_field",
                                            "growth_share", "bubble"])
    def test_draws_without_any_series_names(self, chart_type):
        go = _go()
        spec = ChartSpec(
            chart_type=chart_type,
            title=f"{chart_type} unnamed",
            x_data=["Driver A", "Driver B", "Driver C"],
            y_data=[[-10, -5, -2], [10, 5, 2], [3, 4, 5]],
        )
        gen = ChartGenerator()
        creator = gen._get_chart_creator(chart_type)
        fig = creator(spec, go)
        assert _trace_count(fig) >= 1, f"{chart_type} drew nothing without names"
