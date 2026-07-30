"""
MBB exhibit vocabulary — fix 4.3 (HYPERION_DEEP_AUDIT_2026-07-27.md §3.9, §6 PHASE 4).

The audit's finding was short and specific:

    **Missing the MBB exhibit vocabulary:** tornado/sensitivity, marimekko
    (mekko), football-field valuation range, growth-share matrix,
    bubble-with-size-encoding.

HYPERION could draw the generic business-graphics set — bar, line, scatter,
histogram, stacked bar, treemap, sankey, heatmap, radar, waterfall — but none
of the exhibit forms that actually distinguish MBB work product from a
spreadsheet's default chart menu. A deck of bar charts is not a McKinsey deck.

WHAT MAKES THIS FIX HARDER THAN "ADD FIVE FUNCTIONS"
────────────────────────────────────────────────────
The chart type list was written out in THREE independent places:

  1. `hyperion.schemas.models.ChartType`               — the enum
  2. `hyperion.output.charts._get_chart_creator`       — the geometry dispatch
  3. `hyperion.agents.support.data_visualizer`         — `type_map`,
     `_select_chart_type`, and the `_build_plotly_traces` branch chain

and drift between them is SILENT AND DIRECTIONAL, which is why no existing
test caught it:

  - A type in (1) but missing from (2) renders as a BAR CHART, because
    `_get_chart_creator` ends in `.get(chart_type, self._create_bar)`. Right
    data, wrong geometry, no exception, no log line.
  - A type in (1) but missing from (3)'s `if/elif` chain renders as an EMPTY
    chart, because the chain simply never appends a trace.
  - A type in (1) and (2) but unreachable from `_select_chart_type` is dead
    code that every unit test can still exercise directly and pass.

`ChartType.PIE` was in exactly the first state before this fix — enumerated,
selectable, and rendered as a bar by `charts.py` for want of a `"pie"` key.
That defect was found by writing the parity test in this file, not by reading
the code, which is the whole argument for asserting the invariant rather than
maintaining it by hand.

So these tests do four things that a naive "does it return a figure" test
would not:

  - assert THREE-WAY PARITY between the registries (`TestTheThreeRegistriesAgree`),
    so adding an enum member without wiring the other two fails the suite;
  - assert each new type is REACHABLE from realistic natural-language data
    shapes, including the phrases that collide with the pre-existing generic
    rules (`TestSpecificShapesBeatGenericOnes`);
  - assert each new type EXPORTS A REAL PNG through the production
    `ChartGenerator.generate()` path via Tier 1 — not a mock, not a figure
    object, an actual file with actual bytes — and separately through Tier 2,
    because a silent tier demotion is the audit's signature failure;
  - assert the GEOMETRY IS ACTUALLY DISTINCT — that a tornado is sorted and
    signed, that a marimekko's columns really do have unequal widths on a
    continuous axis, that a football field's bars really do float. Without
    these, all five could be quietly reduced to bar charts and every other
    test in this file would still pass.
"""

from __future__ import annotations

import inspect
import subprocess
import time
from pathlib import Path
from typing import Any

import pytest

from hyperion.agents.support.data_visualizer import (
    _MBB_CHART_TYPES,
    _NO_CARTESIAN_AXES,
    DataVisualizer,
)
from hyperion.output.charts import ChartGenerator, ChartSpec
from hyperion.schemas.models import ChartDataSeries, ChartSpecification, ChartType

# The five types the audit named. Written out literally rather than derived
# from `_MBB_CHART_TYPES`, so that deleting a member from that frozenset
# fails this suite instead of silently shrinking what it checks.
MBB_TYPES = ("tornado", "marimekko", "football_field", "growth_share", "bubble")


# Realistic data per MBB type, following each creator's documented `y_data`
# row layout. Shared by the export and geometry tests so they cannot drift
# apart on what "valid input" means.
MBB_FIXTURES: dict[str, dict[str, Any]] = {
    "tornado": dict(
        x_data=["Price realization", "Volume", "Input costs", "FX"],
        y_data=[[-12.0, -8.0, -5.0, -1.5], [14.0, 9.0, 4.0, 2.0]],
        series_names=["Downside", "Upside"],
        x_label="Impact on EBITDA ($M)",
    ),
    "marimekko": dict(
        x_data=["SMB", "Mid-Market", "Enterprise"],
        y_data=[[30.0, 45.0, 25.0], [60.0, 50.0, 40.0], [40.0, 50.0, 60.0]],
        series_names=["Revenue pool ($B)", "Our share", "Competitor share"],
    ),
    "football_field": dict(
        x_data=["DCF", "Trading comps", "Precedent transactions", "52-week range"],
        y_data=[[40.0, 45.0, 50.0, 38.0], [70.0, 65.0, 80.0, 62.0], [58.0]],
        series_names=["Implied value per share ($)"],
        x_label="Value per share ($)",
    ),
    "growth_share": dict(
        x_data=["Cloud", "On-prem", "Services", "Hardware"],
        y_data=[[2.1, 0.4, 1.5, 0.3], [18.0, 4.0, 12.0, -2.0], [500.0, 300.0, 220.0, 90.0]],
        series_names=["Business units"],
        x_label="Relative market share (x)",
        y_label="Market growth (%)",
    ),
    "bubble": dict(
        x_data=["Alpha", "Beta", "Gamma"],
        y_data=[[1.0, 2.0, 3.0], [10.0, 20.0, 15.0], [100.0, 400.0, 250.0]],
        series_names=["Segments"],
        x_label="Penetration (%)",
        y_label="Margin (%)",
    ),
}


def _kaleido_proc_count() -> int:
    """Count live kaleido/Chromium processes owned by this user.

    Deliberately measures the OS, not a Python attribute: the failure being
    guarded against is orphaned subprocesses holding ~311 MB of RAM, and an
    in-process flag saying "shut down" would happily pass while the tree is
    still resident. Returns 0 if `ps` is unavailable so the assertion degrades
    to trivially-true rather than erroring on an exotic platform.
    """
    try:
        out = subprocess.run(
            ["ps", "-eo", "args", "--no-headers"],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    return sum(
        1
        for line in out.splitlines()
        if ("kaleido" in line or "chrom" in line.lower()) and "ps -eo" not in line
    )


def _spec(chart_type: str, **overrides: Any) -> ChartSpec:
    """Build a realistic ChartSpec for an MBB type."""
    kwargs: dict[str, Any] = {
        "chart_type": chart_type,
        "title": f"Exhibit — {chart_type}",
        "source": "HYPERION test fixture",
        "x_label": "X",
        "y_label": "Y",
    }
    kwargs.update(MBB_FIXTURES.get(chart_type, {}))
    kwargs.update(overrides)
    return ChartSpec(**kwargs)


@pytest.fixture
def gen(tmp_path: Path) -> ChartGenerator:
    """A ChartGenerator writing to tmp_path instead of the real assets dir."""
    g = ChartGenerator()
    g._output_dir = tmp_path
    return g


# ─────────────────────────────────────────────────────────────────────────────


class TestTheThreeRegistriesAgree:
    """The enum, the geometry dispatch, and the agent must cover the same set.

    This is the structural heart of the fix. Each assertion below names the
    specific silent failure that divergence causes, because the failure modes
    are different in each direction and a reader hitting a red test needs to
    know which one they have.
    """

    def test_every_enum_member_has_a_geometry_creator(self) -> None:
        """A member of ChartType with no creator renders as a BAR CHART.

        `_get_chart_creator` ends in `.get(chart_type, self._create_bar)`, so
        a missing key is not an error — it is an exhibit drawn with the right
        data and the wrong geometry, which no runtime check can notice. This
        is the assertion that caught `ChartType.PIE` having no `"pie"` key.
        """
        g = ChartGenerator()
        source = inspect.getsource(g._get_chart_creator)

        missing = [ct.value for ct in ChartType if f'"{ct.value}"' not in source]
        assert not missing, (
            f"ChartType members with no entry in _get_chart_creator: {missing}. "
            "These do NOT raise — they silently render as bar charts via the "
            "dict's default. Add an explicit creator for each."
        )

    def test_every_enum_member_resolves_to_a_distinct_callable(self) -> None:
        """Registered creators must not all collapse onto `_create_bar`.

        Weaker-looking than the source grep above but catches a different
        mistake: a key that is present but points at the wrong method (e.g.
        copy-pasting `"tornado": self._create_bar`). Only the types that are
        genuinely bar-shaped may map to a bar creator.
        """
        g = ChartGenerator()
        bar_creator = g._create_bar

        legitimately_bar = {"bar"}
        wrong = [
            ct.value
            for ct in ChartType
            if ct.value not in legitimately_bar
            and g._get_chart_creator(ct.value) == bar_creator
        ]
        assert not wrong, (
            f"These chart types resolve to _create_bar: {wrong}. Either the "
            "dispatch key is missing (falling through to the default) or it "
            "was wired to the wrong method."
        )

    def test_every_mbb_type_is_in_the_canonical_enum(self) -> None:
        """The five audited types must exist as ChartType members."""
        values = {ct.value for ct in ChartType}
        missing = [t for t in MBB_TYPES if t not in values]
        assert not missing, (
            f"MBB exhibit types absent from ChartType: {missing} "
            "(audit §3.9 names all five)."
        )

    def test_mbb_frozenset_matches_the_audited_list(self) -> None:
        """`_MBB_CHART_TYPES` must be exactly the five audited types.

        The agent delegates trace-building for members of this frozenset. If
        a type is dropped from it, the agent falls back to its own `if/elif`
        chain, which does not name that type, and the chart exports EMPTY.
        """
        assert {ct.value for ct in _MBB_CHART_TYPES} == set(MBB_TYPES)

    def test_agent_trace_builder_covers_every_enum_member(self) -> None:
        """No ChartType may produce an empty trace list.

        Unlike the geometry dispatch (which defaults to a bar), the agent's
        `_build_plotly_traces` is a bare `if/elif` chain: an unnamed type
        appends nothing and exports a blank chart. That is the
        `has_exhibits: false` family of failure the audit opened with.
        """
        dv = DataVisualizer.__new__(DataVisualizer)

        empty: list[str] = []
        for ct in ChartType:
            spec = ChartSpecification(
                id=f"chart_{ct.value}",
                title="Coverage probe",
                chart_type=ct,
                data_series=[
                    ChartDataSeries(name="A", values=[3.0, 1.0, 2.0], labels=["p", "q", "r"]),
                    ChartDataSeries(name="B", values=[6.0, 4.0, 5.0], labels=["p", "q", "r"]),
                    ChartDataSeries(name="C", values=[9.0, 7.0, 8.0], labels=["p", "q", "r"]),
                ],
                source_citation="Src",
                x_axis_label="X",
                y_axis_label="Y",
            )
            if not dv._build_plotly_traces(spec):
                empty.append(ct.value)

        assert not empty, (
            f"These chart types produce ZERO traces in the agent: {empty}. "
            "They will export as blank charts, not as fallback bars."
        )

    def test_every_mbb_type_is_selectable_by_hint(self) -> None:
        """An explicit hint must reach every MBB type.

        The Presentation Designer passes a chart-type hint. If `type_map`
        lacks the key, the hint is ignored and selection silently falls
        through to data-shape heuristics — so a deliberate request for a
        tornado quietly becomes a bar chart.
        """
        dv = DataVisualizer.__new__(DataVisualizer)
        for t in MBB_TYPES:
            assert dv._select_chart_type("", 1, [], t).value == t, (
                f"Hint {t!r} did not resolve to ChartType.{t.upper()} — "
                "it is probably missing from `type_map`."
            )


class TestSpecificShapesBeatGenericOnes:
    """Natural phrasings must not be swallowed by the pre-existing rules.

    This class exists because of a near-miss during the fix. The generic
    families match broad substrings, and the MBB phrasings collide with them:

        "growth-share matrix"  contains "growth" -> trend    -> LINE
        "market share mekko"   contains "share"  -> composition -> STACKED_BAR
        "valuation by comps"   contains "compar" -> comparison  -> BAR
        "bubble sized by growth" contains "growth" -> trend  -> LINE

    Appending the new rules to the END of the chain — the obvious, purely
    additive change — would have left every one of these unreachable for its
    most natural description while every direct unit test still passed. The
    fix orders specific-before-generic; these cases pin that ordering.
    """

    @pytest.mark.parametrize(
        ("shape", "expected"),
        [
            ("growth-share matrix of the portfolio", ChartType.GROWTH_SHARE),
            ("BCG portfolio: stars and cash cows", ChartType.GROWTH_SHARE),
            ("market share mekko by segment", ChartType.MARIMEKKO),
            ("weighted composition across the revenue pool", ChartType.MARIMEKKO),
            ("valuation range from comparable transactions", ChartType.FOOTBALL_FIELD),
            ("football field of price targets", ChartType.FOOTBALL_FIELD),
            ("sensitivity of NPV to each driver", ChartType.TORNADO),
            ("swing analysis: what-if on input costs", ChartType.TORNADO),
            ("bubble: revenue vs growth sized by margin", ChartType.BUBBLE),
        ],
    )
    def test_mbb_phrasing_wins(self, shape: str, expected: ChartType) -> None:
        dv = DataVisualizer.__new__(DataVisualizer)
        got = dv._select_chart_type(shape, 3, [], None)
        assert got == expected, (
            f"{shape!r} selected {got.value!r}, expected {expected.value!r}. "
            "A generic rule earlier in the chain is probably shadowing the "
            "specific one — the ordering is load-bearing."
        )

    @pytest.mark.parametrize(
        ("shape", "expected"),
        [
            ("trend over time", ChartType.LINE),
            ("comparison of competitors", ChartType.BAR),
            ("composition breakdown by product", ChartType.STACKED_BAR),
            ("flow through the conversion funnel", ChartType.SANKEY),
            ("distribution of observed prices", ChartType.HISTOGRAM),
            ("correlation between the two x-y measures", ChartType.SCATTER),
            ("risk matrix of probability and impact", ChartType.HEATMAP),
            ("multi-dimensional capability profile", ChartType.RADAR),
            ("waterfall bridge of the reconciliation", ChartType.WATERFALL),
        ],
    )
    def test_generic_families_still_resolve(self, shape: str, expected: ChartType) -> None:
        """The new rules must not steal the generic families' shapes.

        Inserting rules ahead of existing ones is the risky half of the
        ordering change: it fixes reachability for the new types and can break
        it for the old ones. These are the pre-fix behaviours, pinned.
        """
        dv = DataVisualizer.__new__(DataVisualizer)
        assert dv._select_chart_type(shape, 3, [], None) == expected

    def test_unrecognised_shape_still_defaults_to_bar(self) -> None:
        """The safest-default behaviour is unchanged."""
        dv = DataVisualizer.__new__(DataVisualizer)
        assert dv._select_chart_type("something entirely novel", 2, [], None) == ChartType.BAR


class TestEveryMbbTypeExportsARealImage:
    """Tier 1 must produce an actual PNG on disk for every new type.

    The audit's central complaint is that green tests coexisted with a broken
    system, so these assert bytes on disk from the production `generate()`
    entry point rather than inspecting a figure object. A figure that builds
    but cannot be exported by kaleido is worth nothing to a PDF.
    """

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    def test_tier1_plotly_writes_a_real_png(self, chart_type: str, gen: ChartGenerator) -> None:
        result = gen.generate(_spec(chart_type))

        assert result.success, f"{chart_type} failed all tiers: {result.error}"

        # `_mpl.png` / `_table.html` are the Tier 2 / Tier 3 filename markers.
        assert "_mpl" not in Path(result.image_path).name, (
            f"{chart_type} silently degraded to the matplotlib tier. The "
            "Plotly creator raised and generate() swallowed it — check the "
            "exception tuple in generate() for what was hidden."
        )
        assert result.image_path.endswith(".png"), (
            f"{chart_type} degraded to the HTML data-table tier."
        )

        written = Path(result.image_path)
        assert written.exists(), f"{chart_type}: no file written"
        assert written.stat().st_size > 10_000, (
            f"{chart_type}: {written.stat().st_size} bytes is too small for a "
            "300-DPI exhibit — kaleido may have exported a blank canvas."
        )

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    def test_tier2_matplotlib_also_renders(self, chart_type: str, gen: ChartGenerator) -> None:
        """The matplotlib fallback must handle each new type explicitly.

        `_generate_matplotlib` ends in an `else` that draws a vertical bar
        from zero. For these five that is not a lower-fidelity rendering of
        the exhibit, it is a DIFFERENT and misleading exhibit — a tornado's
        negative downside row drawn as bars below zero with the upside row
        painted over it, or a football field's range-lows drawn as if the
        lows were the values. Tier 2 must preserve meaning, not just produce
        a file.

        Called directly because Tier 2 is unreachable while kaleido works.
        """
        result = gen._generate_matplotlib(_spec(chart_type))

        assert result.success, f"{chart_type} matplotlib fallback failed: {result.error}"
        written = Path(result.image_path)
        assert written.exists() and written.stat().st_size > 5_000

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    def test_matplotlib_handles_the_type_by_name(self, chart_type: str) -> None:
        """Each new type must be named in the Tier 2 branch chain.

        Guards the generic `else` from quietly reclaiming a type: without
        this, deleting an `elif` would still pass the render test above (a bar
        chart is a valid PNG of the right size) while destroying the meaning.
        """
        source = inspect.getsource(ChartGenerator._generate_matplotlib)
        assert f'"{chart_type}"' in source, (
            f"{chart_type} is not named in _generate_matplotlib — it will fall "
            "through to the generic bar-chart `else`, which for this exhibit "
            "type is actively misleading rather than merely plainer."
        )


class TestTheGeometryIsActuallyDistinct:
    """Each exhibit must have the property that makes it that exhibit.

    Without this class, all five creators could be replaced by `_create_bar`
    and every other test here would still pass: the parity tests check the
    dispatch, the export tests check that bytes exist. These check the shape.
    """

    @staticmethod
    def _figure(chart_type: str) -> Any:
        import plotly.graph_objects as go

        g = ChartGenerator()
        return g._get_chart_creator(chart_type)(_spec(chart_type), go)

    def test_tornado_sorts_drivers_by_swing(self) -> None:
        """The widest swing must be outermost — that is the tornado shape.

        The fixture is supplied in already-descending order, so a creator
        that did no sorting would coincidentally look right. It is checked by
        asserting the RENDERED order is ascending (Plotly draws the first
        horizontal category at the bottom), which is the reverse of the input.
        """
        fig = self._figure("tornado")
        labels = list(fig.data[0].y)

        assert labels[0] == "FX", (
            f"Smallest-swing driver should render at the bottom, got {labels}. "
            "The creator is not sorting by swing."
        )
        assert labels[-1] == "Price realization", (
            f"Widest-swing driver should render at the top, got {labels}."
        )

    def test_tornado_is_signed_and_symmetric_about_zero(self) -> None:
        """Downside bars go left, upside right, from a common baseline."""
        fig = self._figure("tornado")
        assert len(fig.data) == 2, "A tornado needs a downside and an upside series"
        assert all(v <= 0 for v in fig.data[0].x), "Downside bars must be negative"
        assert all(v >= 0 for v in fig.data[1].x), "Upside bars must be positive"
        assert fig.layout.barmode == "overlay", (
            "Grouped/stacked bars would offset the two series onto separate "
            "rows instead of opposing them across a shared baseline."
        )

    def test_tornado_colors_by_sign_not_by_series_index(self) -> None:
        """On a sensitivity chart the sign IS the information (§7.3).

        Deliberately overrides "first series is Terracotta": coloring by
        series index would make downside and upside indistinguishable at a
        glance, which defeats the exhibit.
        """
        fig = self._figure("tornado")
        assert fig.data[0].marker.color == "#B5533C", "Downside must be Alert Red"
        assert fig.data[1].marker.color == "#7C9885", "Upside must be Sage"

    def test_marimekko_columns_have_unequal_widths(self) -> None:
        """Unequal widths on a continuous axis are the whole point.

        A marimekko encodes one magnitude as column WIDTH and composition as
        height. If the widths are uniform, or if the x values are categorical
        labels, it has silently become a plain stacked bar and half the
        exhibit's information is gone.
        """
        fig = self._figure("marimekko")

        widths = list(fig.data[0].width)
        assert len(set(widths)) > 1, (
            f"All columns have the same width ({widths}) — the width "
            "dimension has been discarded and this is now a stacked bar."
        )
        assert widths == [30.0, 45.0, 25.0], (
            f"Widths {widths} do not match y_data[0], the width dimension."
        )

        # Continuous positions, not category strings — categorical axes are
        # always equally spaced, so widths would not compose.
        assert all(isinstance(x, (int, float)) for x in fig.data[0].x), (
            f"x positions {list(fig.data[0].x)} are not numeric; column "
            "widths cannot be meaningful on a categorical axis."
        )

    def test_marimekko_columns_abut_and_stack(self) -> None:
        """Zero gap and stacked segments — adjacent columns must touch."""
        fig = self._figure("marimekko")
        assert fig.layout.barmode == "stack"
        assert fig.layout.bargap == 0, (
            "A non-zero bargap leaves whitespace between columns, which "
            "breaks the read of total width as a share of the whole."
        )

    def test_marimekko_centers_are_cumulative(self) -> None:
        """Each column sits at the center of its own width, in sequence.

        `expected` is DERIVED from the widths rather than written out as
        literals. The first version of this test hardcoded the three centers
        and left `widths` unused — ruff's F841 caught it. That is not a
        cosmetic lint: a hand-computed expectation is a second, unverified
        implementation of the thing under test, and if the fixture widths ever
        change the literals silently become an assertion about nothing.
        """
        fig = self._figure("marimekko")
        widths = MBB_FIXTURES["marimekko"]["y_data"][0]

        expected: list[float] = []
        running = 0.0
        for w in widths:
            expected.append(running + w / 2)
            running += w

        assert list(fig.data[0].x) == pytest.approx(expected), (
            f"Column centers {list(fig.data[0].x)} != cumulative centers "
            f"{expected}. Columns will overlap or leave gaps."
        )
        # Guard the derivation itself: centers must strictly increase and the
        # last one must sit inside the total width.
        assert expected == sorted(expected)
        assert expected[-1] < sum(widths)

    def test_football_field_bars_float(self) -> None:
        """Bars must start at each method's low, not at zero.

        A bar from zero compresses every valuation range into the right-hand
        margin and destroys the overlap read, which is the only reason the
        exhibit exists.
        """
        fig = self._figure("football_field")
        trace = fig.data[0]

        bases = list(trace.base)
        assert all(b > 0 for b in bases), (
            f"Bar bases {bases} include zero — the bars are not floating."
        )
        assert bases == [40.0, 45.0, 50.0, 38.0], "Bases must be the range lows"
        assert list(trace.x) == [30.0, 20.0, 30.0, 24.0], (
            "Bar lengths must be (high - low), i.e. the width of each range."
        )
        assert trace.orientation == "h", "Methodologies run down the y-axis"

    def test_football_field_reference_line_is_drawn(self) -> None:
        """The optional third row becomes a vertical reference line."""
        fig = self._figure("football_field")
        shapes = [s for s in fig.layout.shapes if s.type == "line"]
        assert shapes, "No reference line drawn for y_data[2]"
        assert shapes[0].x0 == 58.0 and shapes[0].x1 == 58.0, (
            "Reference line must sit at the supplied value, vertically."
        )

    def test_football_field_reserves_room_for_outside_labels(self) -> None:
        """The x-range must extend past the widest range's high value.

        Plotly sizes an axis to the DATA, not to annotations drawn beyond it,
        so `textposition="outside"` labels on the widest bar rendered clipped
        against the figure edge. Found by rendering the exhibit and looking at
        it — no assertion in this file would have caught it, which is why the
        visual check is part of this fix's verification rather than an
        afterthought. Pinned here so it cannot silently return.
        """
        fig = self._figure("football_field")
        x_range = fig.layout.xaxis.range

        assert x_range is not None, "No explicit range — labels will clip"
        assert x_range[1] > 80.0, (
            f"Upper bound {x_range[1]} does not clear the widest high (80); "
            "the outside value label will be clipped."
        )
        # But not so much headroom that the bars are squeezed into a corner.
        assert x_range[1] < 80.0 + (80.0 - 38.0), "Excessive right padding"

    def test_football_field_tolerates_inverted_low_high(self) -> None:
        """low/high supplied backwards must still draw a visible bar.

        A negative bar length renders as nothing at all in Plotly — a blank
        exhibit from data that was merely in the wrong order.
        """
        import plotly.graph_objects as go

        g = ChartGenerator()
        spec = ChartSpec(
            chart_type="football_field",
            title="Inverted",
            x_data=["DCF"],
            y_data=[[80.0], [40.0]],  # low > high
            series_names=["Range"],
        )
        fig = g._create_football_field(spec, go)
        assert list(fig.data[0].x) == [40.0], "Span must be positive"
        assert list(fig.data[0].base) == [40.0], "Base must be the true minimum"

    def test_bubble_encodes_magnitude_as_area_not_diameter(self) -> None:
        """Area encoding is honest; diameter overstates large values.

        Plotly's default `sizemode` is diameter, which scales the perceived
        magnitude quadratically — exactly the distortion Tufte's lie factor
        measures, and a routine way real decks mislead.
        """
        for chart_type in ("bubble", "growth_share"):
            fig = self._figure(chart_type)
            marker = fig.data[0].marker
            assert marker.sizemode == "area", (
                f"{chart_type}: sizemode is {marker.sizemode!r}, not 'area' — "
                "magnitudes are being overstated quadratically."
            )
            assert marker.sizeref is not None and marker.sizeref > 0, (
                f"{chart_type}: area encoding needs an explicit sizeref."
            )
            assert marker.sizemin is not None, (
                f"{chart_type}: without sizemin the smallest bubble can "
                "vanish entirely."
            )

    def test_bubble_sizes_come_from_the_third_row(self) -> None:
        fig = self._figure("bubble")
        assert list(fig.data[0].marker.size) == [100.0, 400.0, 250.0]
        assert list(fig.data[0].x) == [1.0, 2.0, 3.0]
        assert list(fig.data[0].y) == [10.0, 20.0, 15.0]

    def test_growth_share_reverses_the_share_axis(self) -> None:
        """High relative share on the LEFT, per BCG convention.

        Reversing the axis rather than negating the data keeps the tick
        labels readable as true share multiples.
        """
        fig = self._figure("growth_share")
        assert fig.layout.xaxis.autorange == "reversed", (
            "Growth-share matrices put high relative share on the left; "
            "without this the quadrants are mirrored and 'stars' land in the "
            "wrong corner."
        )

    def test_growth_share_divides_share_at_parity_not_at_the_median(self) -> None:
        """The vertical divider belongs at relative share = 1.0x.

        A median split would guarantee two units on each side regardless of
        whether any of them actually leads its market — it would report the
        shape of the sample rather than a fact about the portfolio.
        """
        fig = self._figure("growth_share")
        verticals = [s for s in fig.layout.shapes if s.x0 == s.x1]
        assert verticals, "No vertical share divider drawn"
        assert verticals[0].x0 == 1.0, (
            f"Share divider at {verticals[0].x0}, expected parity (1.0x). "
            "A data-derived split would not mean 'leads its market'."
        )

    def test_growth_share_draws_a_growth_midpoint(self) -> None:
        """Both dividers are needed to produce four quadrants."""
        fig = self._figure("growth_share")
        horizontals = [s for s in fig.layout.shapes if s.y0 == s.y1]
        assert horizontals, "No horizontal growth divider — no quadrants"
        # Fixture growth spans -2 .. 18, midpoint 8.
        assert horizontals[0].y0 == pytest.approx(8.0)

    def test_growth_share_and_bubble_do_not_share_a_figure(self) -> None:
        """growth_share is a bubble chart PLUS fixed semantics.

        Both delegate to `_bubble_figure`; the matrix must add its quadrant
        dividers and axis reversal on top. If they were identical, the
        distinction would be cosmetic.
        """
        plain = self._figure("bubble")
        matrix = self._figure("growth_share")
        assert not plain.layout.shapes, "A plain bubble chart has no quadrants"
        assert len(matrix.layout.shapes) >= 2, "The matrix needs both dividers"
        assert plain.layout.xaxis.autorange != "reversed"


class TestBrandComplianceHolds:
    """§7.3 is not suspended for new chart types."""

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    def test_only_brand_colors_are_used(self, chart_type: str) -> None:
        """No Plotly default palette may leak in (never blue/purple/green)."""
        import plotly.graph_objects as go

        from hyperion.output.charts import CHART_COLORS

        allowed = {c.lower() for c in CHART_COLORS}
        allowed |= {"#f5f4ee", "#1a1a1a", "#e8e6dd"}  # bg / text / grid

        g = ChartGenerator()
        fig = g._get_chart_creator(chart_type)(_spec(chart_type), go)

        found: list[str] = []
        for trace in fig.data:
            marker = getattr(trace, "marker", None)
            color = getattr(marker, "color", None) if marker else None
            for c in ([color] if isinstance(color, str) else list(color or [])):
                if isinstance(c, str) and c.startswith("#"):
                    found.append(c.lower())

        leaked = sorted({c for c in found if c not in allowed})
        assert not leaked, (
            f"{chart_type} uses non-brand colors: {leaked}. §7.3 permits only "
            f"the six-color sequence."
        )

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    def test_no_more_than_five_colors_per_chart(self, chart_type: str) -> None:
        """§7.3: never more than 5 colors in a single chart.

        The MBB types color by CATEGORY rather than by series, so they are the
        types most likely to breach the cap — a marimekko with eight segments
        or a portfolio with eight business units. `_color_cycle` must wrap
        rather than extend.
        """
        import plotly.graph_objects as go

        many = {
            "marimekko": dict(
                x_data=["A", "B", "C"],
                y_data=[[10.0, 20.0, 30.0]] + [[5.0, 5.0, 5.0]] * 8,
                series_names=["Width"] + [f"S{i}" for i in range(8)],
            ),
            "growth_share": dict(
                x_data=[f"BU{i}" for i in range(8)],
                y_data=[[1.0] * 8, [5.0] * 8, [100.0] * 8],
                series_names=["BUs"],
            ),
            "bubble": dict(
                x_data=[f"P{i}" for i in range(8)],
                y_data=[[1.0] * 8, [5.0] * 8, [100.0] * 8],
                series_names=["Points"],
            ),
            "tornado": dict(
                x_data=[f"D{i}" for i in range(8)],
                y_data=[[-1.0] * 8, [1.0] * 8],
                series_names=["Down", "Up"],
            ),
            "football_field": dict(
                x_data=[f"M{i}" for i in range(8)],
                y_data=[[1.0] * 8, [9.0] * 8],
                series_names=["Range"],
            ),
        }[chart_type]

        g = ChartGenerator()
        spec = ChartSpec(chart_type=chart_type, title="Wide", **many)
        fig = g._get_chart_creator(chart_type)(spec, go)

        used: set[str] = set()
        for trace in fig.data:
            marker = getattr(trace, "marker", None)
            color = getattr(marker, "color", None) if marker else None
            for c in ([color] if isinstance(color, str) else list(color or [])):
                if isinstance(c, str) and c.startswith("#"):
                    used.add(c.lower())

        assert len(used) <= 5, (
            f"{chart_type} used {len(used)} colors ({sorted(used)}); §7.3 "
            "caps a single chart at 5."
        )

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    def test_brand_styling_applies_without_raising(self, chart_type: str) -> None:
        """`_apply_brand_styling` must tolerate every new trace type.

        This is a re-run of a known historical failure: an unconditional
        `update_traces()` raised `ValueError` for sankey/heatmap/waterfall on
        every call, silently demoting them to Tier 2 forever. New trace types
        are the same hazard.
        """
        import plotly.graph_objects as go

        g = ChartGenerator()
        spec = _spec(chart_type)
        fig = g._get_chart_creator(chart_type)(spec, go)

        styled = g._apply_brand_styling(fig, spec)

        assert styled.layout.paper_bgcolor == "#F5F4EE"
        assert styled.layout.font.family == "Source Sans 3, sans-serif"

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    def test_source_citation_survives_styling(self, chart_type: str) -> None:
        """Every exhibit carries its source (§4.5) — including new ones.

        The creators add `shapes`; the styler adds the source `annotation`.
        These are separate layout collections, but a creator that returned a
        figure with `annotations` already set could clobber it.
        """
        import plotly.graph_objects as go

        g = ChartGenerator()
        spec = _spec(chart_type, source="FRED, World Bank")
        fig = g._apply_brand_styling(g._get_chart_creator(chart_type)(spec, go), spec)

        texts = [a.text for a in fig.layout.annotations if a.text]
        assert any("FRED, World Bank" in t for t in texts), (
            f"{chart_type} lost its source citation during styling."
        )


class TestDegenerateInputCannotDemoteATier:
    """Malformed data must degrade the exhibit, never the rendering tier.

    Every one of these creators does arithmetic on LLM-supplied rows that
    pydantic types as `float | str`, so `"n/a"`, `"12%"`, `None`, and short
    rows all arrive in practice. A `TypeError` there is caught by
    `generate()` and silently demotes the chart to matplotlib — invisible in
    logs, visible only as a subtly worse PDF. Coercion to zero is the lesser
    evil: a zero is wrong in a way a reviewer can see.
    """

    CASES = {
        "empty": dict(x_data=[], y_data=[], series_names=[]),
        "non_numeric": dict(
            x_data=["A", "B"],
            y_data=[["n/a", "12%"], [None, "3"]],
            series_names=["S1", "S2"],
        ),
        "short_rows": dict(x_data=["A", "B", "C"], y_data=[[1.0]], series_names=["S"]),
        "zero_and_negative": dict(
            x_data=["A", "B"], y_data=[[0.0, -5.0], [1.0, 2.0]], series_names=["W", "S"]
        ),
        "single_point": dict(x_data=["Only"], y_data=[[1.0], [2.0], [3.0]], series_names=["S"]),
    }

    @pytest.mark.parametrize("chart_type", MBB_TYPES)
    @pytest.mark.parametrize("case", sorted(CASES))
    def test_tier1_still_wins(self, chart_type: str, case: str, gen: ChartGenerator) -> None:
        spec = ChartSpec(chart_type=chart_type, title=f"{chart_type}-{case}", **self.CASES[case])
        result = gen.generate(spec)

        assert result.success, (
            f"{chart_type} with {case!r} input failed all three tiers: {result.error}"
        )
        assert "_mpl" not in Path(result.image_path).name, (
            f"{chart_type} with {case!r} input demoted to matplotlib — the "
            "Plotly creator raised on degenerate data instead of coercing it."
        )

    def test_nums_coerces_rather_than_raising(self) -> None:
        """The shared coercion helper is total over messy input."""
        assert ChartGenerator._nums(["1", 2, 3.5]) == [1.0, 2.0, 3.5]
        assert ChartGenerator._nums(["12%", "1,200"]) == [12.0, 1200.0]
        assert ChartGenerator._nums(["n/a", None, "", "abc"]) == [0.0, 0.0, 0.0, 0.0]

    def test_marimekko_never_collapses_a_column_to_zero_width(self) -> None:
        """A zero width would hide the column AND shift every later center."""
        import plotly.graph_objects as go

        g = ChartGenerator()
        spec = ChartSpec(
            chart_type="marimekko",
            title="Zero width",
            x_data=["A", "B", "C"],
            y_data=[[0.0, -3.0, 20.0], [1.0, 2.0, 3.0]],
            series_names=["W", "S"],
        )
        fig = g._create_marimekko(spec, go)
        assert all(w > 0 for w in fig.data[0].width), (
            f"Non-positive widths survived: {list(fig.data[0].width)}"
        )

    def test_color_cycle_does_not_index_past_a_single_series(self) -> None:
        """`_get_colors` truncates to `len(series_names)`; `_color_cycle` must not.

        The MBB types color by category, so a single-series spec with four
        categories would raise `IndexError` off `_get_colors` — caught by
        `generate()`, demoting the tier. This is that regression, pinned.
        """
        g = ChartGenerator()
        spec = ChartSpec(chart_type="bubble", title="One series", series_names=["Only"])
        colors = g._color_cycle(spec, 8)
        assert len(colors) == 8
        assert len({c for c in colors}) <= 5, "Must cycle within the 5-color cap"


class TestTufteChecksStayHonest:
    """Axis-label requirements must be right, not merely strict.

    Two failure directions matter equally here. Exempting a type that really
    does have a labelled value axis makes the check useless for that type;
    demanding a label from a type that has no such axis is a false negative
    that marks a correct exhibit non-compliant.
    """

    @staticmethod
    def _spec_for(chart_type: ChartType, **kw: Any) -> ChartSpecification:
        base: dict[str, Any] = dict(
            id="c1",
            title="T",
            chart_type=chart_type,
            data_series=[ChartDataSeries(name="A", values=[1.0], labels=["x"])],
            source_citation="Src",
            x_axis_label="X label",
            y_axis_label="Y label",
        )
        base.update(kw)
        return ChartSpecification(**base)

    def test_value_axes_are_still_required(self) -> None:
        """A football field's x-axis is a currency amount; it needs a label.

        Blanket-exempting the new types would have been the easy change and
        would have silently lowered the bar for the exhibits that most need
        a readable axis.
        """
        dv = DataVisualizer.__new__(DataVisualizer)
        for ct in (ChartType.TORNADO, ChartType.FOOTBALL_FIELD,
                   ChartType.GROWTH_SHARE, ChartType.BUBBLE):
            assert not dv._check_tufte_compliance(self._spec_for(ct, x_axis_label="")), (
                f"{ct.value} passed Tufte compliance with NO x-axis label — "
                "its x-axis carries units and must be labelled."
            )

    def test_categorical_y_axes_are_exempt(self) -> None:
        """Labelling a y-axis of driver names would restate the ticks."""
        dv = DataVisualizer.__new__(DataVisualizer)
        for ct in (ChartType.TORNADO, ChartType.FOOTBALL_FIELD):
            assert dv._check_tufte_compliance(self._spec_for(ct, y_axis_label="")), (
                f"{ct.value} was failed for lacking a y-axis label, but its "
                "y-axis is self-labelling categories."
            )

    def test_growth_share_needs_both_axes(self) -> None:
        """Relative share and growth rate are both quantitative."""
        dv = DataVisualizer.__new__(DataVisualizer)
        assert not dv._check_tufte_compliance(
            self._spec_for(ChartType.GROWTH_SHARE, y_axis_label="")
        )

    def test_axis_free_types_remain_exempt(self) -> None:
        """Pre-fix behaviour for treemap/sankey/pie/radar is unchanged."""
        dv = DataVisualizer.__new__(DataVisualizer)
        for ct in (ChartType.TREEMAP, ChartType.SANKEY, ChartType.PIE, ChartType.RADAR):
            assert dv._check_tufte_compliance(
                self._spec_for(ct, x_axis_label="", y_axis_label="")
            ), f"{ct.value} should not require cartesian axis labels"

    def test_exempt_set_is_declared_once(self) -> None:
        """`_NO_CARTESIAN_AXES` replaced two hand-synced inline tuples."""
        assert ChartType.TREEMAP in _NO_CARTESIAN_AXES
        assert ChartType.MARIMEKKO in _NO_CARTESIAN_AXES
        assert ChartType.BAR not in _NO_CARTESIAN_AXES

    def test_source_citation_is_still_mandatory(self) -> None:
        dv = DataVisualizer.__new__(DataVisualizer)
        assert not dv._check_tufte_compliance(
            self._spec_for(ChartType.TORNADO, source_citation="")
        )


class TestTheAgentPreservesMbbGeometry:
    """The agent's own export path must not flatten the delegated geometry.

    `_build_plotly_layout` builds a layout from the spec alone and knows
    nothing about barmode, bargap, quadrant shapes, or a reversed axis. Left
    unmerged, a marimekko would export with default gaps — i.e. a plain
    stacked bar, silently discarding the width dimension it exists to show.
    """

    @staticmethod
    def _agent_spec(ct: ChartType) -> ChartSpecification:
        return ChartSpecification(
            id=f"c_{ct.value}",
            title="Exhibit",
            chart_type=ct,
            data_series=[
                ChartDataSeries(name="W", values=[30.0, 45.0, 25.0], labels=["a", "b", "c"]),
                ChartDataSeries(name="S1", values=[60.0, 50.0, 40.0], labels=["a", "b", "c"]),
                ChartDataSeries(name="S2", values=[40.0, 50.0, 60.0], labels=["a", "b", "c"]),
            ],
            source_citation="Src",
            x_axis_label="X",
            y_axis_label="Y",
        )

    def test_marimekko_keeps_zero_bargap_through_the_agent(self) -> None:
        dv = DataVisualizer.__new__(DataVisualizer)
        overrides = dv._mbb_layout_overrides(self._agent_spec(ChartType.MARIMEKKO))
        assert overrides.get("barmode") == "stack"
        assert overrides.get("bargap") == 0, (
            "Without the bargap override the agent exports a gapped stacked "
            "bar, discarding the width dimension."
        )

    def test_tornado_keeps_overlay_barmode_through_the_agent(self) -> None:
        dv = DataVisualizer.__new__(DataVisualizer)
        overrides = dv._mbb_layout_overrides(self._agent_spec(ChartType.TORNADO))
        assert overrides.get("barmode") == "overlay"

    def test_growth_share_keeps_reversed_axis_and_quadrants(self) -> None:
        dv = DataVisualizer.__new__(DataVisualizer)
        overrides = dv._mbb_layout_overrides(self._agent_spec(ChartType.GROWTH_SHARE))
        assert overrides.get("xaxis", {}).get("autorange") == "reversed"
        assert overrides.get("shapes"), "Quadrant dividers were dropped"

    def test_overrides_do_not_carry_brand_styling(self) -> None:
        """Only geometry is merged; colors/fonts stay the agent's job.

        Copying the whole layout across would let `charts.py` overwrite the
        agent's `_build_plotly_layout` brand styling, creating a second place
        that decides what an exhibit looks like.
        """
        dv = DataVisualizer.__new__(DataVisualizer)
        overrides = dv._mbb_layout_overrides(self._agent_spec(ChartType.TORNADO))
        for banned in ("paper_bgcolor", "plot_bgcolor", "font", "title", "colorway"):
            assert banned not in overrides, (
                f"{banned!r} leaked into the layout overrides — brand styling "
                "must stay with _build_plotly_layout."
            )

    def test_non_mbb_types_get_no_overrides_path(self) -> None:
        """Existing types must keep using the hand-built branch chain."""
        dv = DataVisualizer.__new__(DataVisualizer)
        traces = dv._build_plotly_traces(self._agent_spec(ChartType.BAR))
        assert traces and all(t["type"] == "bar" for t in traces)

    def test_malformed_agent_spec_does_not_raise(self) -> None:
        """One bad exhibit must not abort the whole visualization run.

        This test originally asserted an empty trace list, on the assumption
        that a spec with no data series would drive the creator into the
        `except` branch of `_build_mbb_traces`. It does not: the creators pad
        and coerce their rows, so an empty spec yields two structurally valid
        traces that happen to carry no points. That is a better outcome than
        the assumption — an empty-but-well-formed figure exports cleanly,
        whereas the `except` path returns `[]` and produces a blank chart —
        so the assertion was corrected to pin the property that actually
        matters and is actually true: no exception escapes to the caller, and
        whatever comes back is JSON-serialisable for the export step.
        """
        dv = DataVisualizer.__new__(DataVisualizer)
        spec = ChartSpecification(
            id="c_bad",
            title="Bad",
            chart_type=ChartType.TORNADO,
            data_series=[],
            source_citation="Src",
        )

        traces = dv._build_plotly_traces(spec)  # must not raise

        assert isinstance(traces, list)
        for trace in traces:
            assert isinstance(trace, dict), "Traces must be plain dicts for export"
            assert "type" in trace


class TestTheVocabularyIsDocumented:
    """A chart type nobody knows about is a chart type nobody requests.

    `_select_chart_type` is driven by a data-shape string that upstream
    agents produce from their prompts. If the vocabulary is not documented
    where those prompts are written, the creators are reachable in tests and
    unreachable in production.
    """

    def test_charts_module_lists_the_mbb_types(self) -> None:
        source = Path("hyperion/output/charts.py").read_text(encoding="utf-8")
        header = source.split("from __future__")[0]
        for t in ("Tornado", "Marimekko", "Football field", "Growth-share", "Bubble"):
            assert t in header, f"{t} missing from the charts.py module docstring"

    def test_enum_docstring_names_the_canonical_registry_invariant(self) -> None:
        """The enum must say it is canonical, or the invariant will drift again."""
        doc = ChartType.__doc__ or ""
        assert "CANONICAL" in doc.upper()
        assert "_get_chart_creator" in doc, (
            "The enum docstring must name the dispatch it has to stay in sync "
            "with, so the next person adding a member knows what else to touch."
        )


class TestTheRendererDoesNotHoardMemory:
    """The renderer must give its ~311 MB back, or it starves everything after it.

    Found while making this suite pass, and it is a production defect rather
    than a test-harness one. Kaleido spawns a Chromium tree on the first
    export and holds it for the life of the interpreter: measured **35 MB / 6
    procs → 311 MB / 13 procs** after one export, still 353 MB after eleven.
    Export latency is flat (~0.15 s across 60 consecutive exports), so nothing
    degrades *within* charting — the damage lands on whatever allocates next.

    On the 985 MB CI/dev container with swap already exhausted that reservation
    is most of the free memory, and the observable symptom was a hang inside
    `kaleido/scopes/base.py:308` that looked like a kaleido bug. It was not:
    the same test combination that timed out at >45 s completed in 12 s once
    the orphaned Chromium tree was reaped. `ChartGenerator.close()` was
    `pass`, so `async with` and every `finally: await close()` freed nothing.
    """

    def test_close_is_not_a_no_op(self) -> None:
        """`close()` was literally `pass`; that is the whole bug, pinned."""
        import inspect

        src = inspect.getsource(ChartGenerator.close)
        body = src.split('"""')[-1]
        assert "release_renderer" in body, (
            "ChartGenerator.close() must release the renderer. If this reverts "
            "to `pass`, the 311 MB Chromium tree outlives the generator again."
        )

    async def test_close_releases_the_subprocess_tree(self, gen: ChartGenerator) -> None:
        """End-to-end: export, close, confirm the tree this test started is gone.

        Asserts a DELTA against the count taken before exporting, not an
        absolute count. Earlier drafts asserted `== 0` and failed against six
        surviving processes that turned out to be orphans from previous runs
        (`ppid=1`) — the fix was working and the assertion was lying. A clean
        interpreter measures 0 procs → 7 procs / 277 MB on export → 0 after
        shutdown, so the delta is the honest signal here.
        """
        # Establish a cold baseline first. Without this the test is
        # order-dependent: any earlier test in the same process leaves the tree
        # warm (7 procs), so the export cannot raise the count and the
        # `peak > before` guard fires on a working fix. That is exactly what
        # happened — the test passed alone and failed inside the full suite.
        ChartGenerator.release_renderer()
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and _kaleido_proc_count() > 0:
            time.sleep(0.25)
        before = _kaleido_proc_count()

        result = gen.generate(_spec("bubble"))
        assert result.success, f"precondition failed: {result.error}"
        peak = _kaleido_proc_count()
        assert peak > before, (
            f"Renderer process count did not rise on a successful Tier-1 "
            f"export ({before} → {peak}): this test can no longer observe what "
            "it is meant to observe, so its passing would mean nothing."
        )

        await gen.close()
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline and _kaleido_proc_count() > before:
            time.sleep(0.25)

        after = _kaleido_proc_count()
        assert after <= before, (
            f"close() left {after - before} extra renderer process(es) behind "
            f"({before} before, {peak} at peak, {after} after). Each tree holds "
            "~277 MB; on a memory-constrained host that is what makes the "
            "*next* kaleido call appear to hang forever."
        )

    def test_renderer_respawns_after_release(self, gen: ChartGenerator) -> None:
        """Releasing must be safe mid-run, not a one-way door.

        `generate_batch` releases after every batch, so a second batch in the
        same process has to work. Verified respawn cost ~1.5 s vs ~0.15 s warm,
        which is why release is per-batch and not per-chart.
        """
        assert ChartGenerator.release_renderer() in (True, False)
        again = gen.generate(_spec("tornado"))
        assert again.success, (
            f"export after release_renderer() failed: {again.error} — "
            "releasing the renderer must not poison the process."
        )
        assert "_mpl" not in Path(again.image_path).name, (
            "Post-release export silently fell back to matplotlib, so the "
            "release is not transparent and reports would quietly lose quality."
        )

    def test_generate_batch_releases_even_when_a_chart_fails(self) -> None:
        """The release is in a `finally`, because the starving case is the failing one."""
        import inspect

        src = inspect.getsource(ChartGenerator.generate_batch)
        assert "finally" in src and "release_renderer" in src, (
            "generate_batch must release the renderer in a finally block: a "
            "raising batch is exactly when memory is already tight."
        )
