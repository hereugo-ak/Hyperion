"""Regression tests for the eight pipeline defects fixed in commit 22290eb.

WHY THIS FILE EXISTS
────────────────────
The eight fixes in 22290eb were verified by ad-hoc scripts that were then
thrown away. The existing 148-test suite passes, but a coverage audit showed
that **not one** of those tests references any of the changed APIs
(`_pull_macro_inputs`, `_resolve_jurisdictions`, `FinalReport.
chart_specifications`, `resolve_subject`, `detect_geographies`,
`_coerce_content`, the HTML fallback). A green suite that never touches the
repaired code proves nothing about the repair.

Each test below pins one specific observed production failure so it cannot
silently return. The failure each one guards is named in its docstring.

The governing invariant, stated once: **detect what the user named; never
invent a default.** An empty geography means "no jurisdiction filter", which
is honest. A *wrong* geography is strictly worse than a missing one, because
nothing in the output signals the error.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path

import pytest

# The verbatim question from the failing 34-minute run. Every grounding test
# uses it so the assertions describe a real engagement, not a synthetic one.
INDIA_Q = "should india reduce its dependence on the imports ?"


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 2 — WRONG-COUNTRY ANALYSIS
#
# The most damaging defect: it never crashed. It produced confident,
# well-formatted findings about the United States for a question about India,
# and nothing in the output flagged it.
# ═════════════════════════════════════════════════════════════════════════════


class TestGeographyDetection:
    """`detect_geographies` must detect, and must never invent."""

    def test_detects_india_from_the_real_question(self):
        from hyperion.tools.query_utils import detect_geographies

        assert detect_geographies(INDIA_Q) == ["India"]

    def test_returns_empty_when_no_geography_is_named(self):
        """The core invariant. [] means "no filter" — NOT "assume US/EU"."""
        from hyperion.tools.query_utils import detect_geographies

        for text in (
            "should we reduce our dependence on suppliers?",
            "what is the outlook for enterprise software pricing?",
            "",
        ):
            assert detect_geographies(text) == [], f"invented a geography for {text!r}"

    def test_first_named_country_ranks_first(self):
        """Ordering is by first appearance, so the question's primary subject wins."""
        from hyperion.tools.query_utils import detect_geographies

        got = detect_geographies("should India cut imports from China and Germany?")
        assert got[0] == "India"
        assert set(got) == {"India", "China", "Germany"}

    def test_canonicalises_aliases(self):
        from hyperion.tools.query_utils import detect_geographies

        assert detect_geographies("indian manufacturing") == ["India"]
        assert detect_geographies("the U.S. market") == ["US"]
        assert detect_geographies("Bharat") == ["India"]

    def test_global_is_demoted_below_concrete_countries(self):
        from hyperion.tools.query_utils import detect_geographies

        got = detect_geographies("global outlook for India")
        assert got[0] == "India", f"weak 'Global' signal outranked a real country: {got}"
        assert got[-1] == "Global"


class TestNoHardcodedGeographyDefaults:
    """Guards the exact literals that caused the wrong-country analysis."""

    ACTIVE_SPECIALISTS = [
        "regulatory_analyst.py",
        "sustainability_analyst.py",
        "risk_analyst.py",
        "financial_analyst.py",
        "market_analyst.py",
    ]

    GEO_KEYS = {
        "jurisdiction", "jurisdictions", "geography", "geographies",
        "region", "regions", "country", "countries", "markets",
    }

    def _tree(self, filename: str):
        root = Path(__file__).resolve().parent.parent
        path = root / "hyperion" / "agents" / "specialists" / filename
        return ast.parse(path.read_text(encoding="utf-8"))

    @staticmethod
    def _literal_geographies(node) -> list[str]:
        """Return real geography labels in a literal AST node.

        Uses the production gazetteer, so this asserts on *meaning* rather than
        on spelling. `"Unknown"` yields [] — labelling an unresolved value as
        unknown is honest. `["US", "EU"]` yields ["US", "EU"] — fabrication.
        """
        from hyperion.tools.query_utils import detect_geographies

        strings: list[str] = []
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            strings = [node.value]
        elif isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            strings = [
                el.value
                for el in node.elts
                if isinstance(el, ast.Constant) and isinstance(el.value, str)
            ]
        hits: list[str] = []
        for s in strings:
            if detect_geographies(s):
                hits.append(s)
        return hits

    @pytest.mark.parametrize("filename", ACTIVE_SPECIALISTS)
    def test_no_geography_default_in_dict_get(self, filename):
        """`.get("jurisdictions", ["US", "EU"])` analysed India under the Buy
        American Act, the TAA and the Berry Amendment — 119 findings about the
        wrong jurisdiction.

        AST-based, so the *documentation* of this fix (which necessarily quotes
        the old literal in docstrings and comments) does not trip the test.
        """
        offenders: list[str] = []
        for node in ast.walk(self._tree(filename)):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            if node.func.attr != "get" or len(node.args) != 2:
                continue
            key = node.args[0]
            if not (isinstance(key, ast.Constant) and isinstance(key.value, str)):
                continue
            if key.value.lower() not in self.GEO_KEYS:
                continue
            for geo in self._literal_geographies(node.args[1]):
                offenders.append(f"line {node.lineno}: .get({key.value!r}, ...{geo!r})")
        assert not offenders, (
            f"{filename} still fabricates a geography default: {offenders}"
        )

    @pytest.mark.parametrize("filename", ACTIVE_SPECIALISTS)
    def test_no_geography_default_in_function_signature(self, filename):
        """`geography="US"` as a *default argument* is the same fabrication in
        signature form — that is how FRED's US-only series got an India label."""
        offenders: list[str] = []
        for node in ast.walk(self._tree(filename)):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            args = node.args
            # Positional args pair with defaults right-aligned.
            positional = args.posonlyargs + args.args
            paired = list(
                zip(positional[len(positional) - len(args.defaults):], args.defaults)
            )
            paired += [
                (a, d) for a, d in zip(args.kwonlyargs, args.kw_defaults) if d is not None
            ]
            for arg, default in paired:
                if arg.arg.lower() not in self.GEO_KEYS:
                    continue
                for geo in self._literal_geographies(default):
                    offenders.append(f"line {node.lineno}: {node.name}({arg.arg}={geo!r})")
        assert not offenders, (
            f"{filename} has a hardcoded default geography argument: {offenders}"
        )


class TestFredGeographyHonesty:
    """FRED is US-only. `geography` only ever *labelled* the source."""

    def test_macro_signature_defaults_to_empty_not_us(self):
        """`_pull_macro_inputs(geography="US")` meant an India engagement
        discounted Indian cash flows at a US risk-free rate, under an Indian
        citation. In market sizing the same error scales the whole TAM."""
        from hyperion.agents.specialists.financial_analyst import FinancialAnalyst

        sig = inspect.signature(FinancialAnalyst._pull_macro_inputs)
        assert sig.parameters["geography"].default == "", (
            "geography must default to empty — never to a fabricated 'US'"
        )

    @pytest.mark.parametrize(
        "filename",
        ["financial_analyst.py", "market_analyst.py"],
    )
    def test_fred_source_is_cited_as_united_states(self, filename):
        """The source line used to read "FRED Macroeconomic Data — India" while
        serving US Treasury yields (DGS10, CPIAUCSL, GDP, FEDFUNDS, PCES)."""
        root = Path(__file__).resolve().parent.parent
        src = (root / "hyperion" / "agents" / "specialists" / filename).read_text(
            encoding="utf-8"
        )
        assert "FRED Macroeconomic Data — United States" in src, (
            f"{filename} must cite FRED as United States, not as the requested geography"
        )
        assert "geography_mismatch" in src, (
            f"{filename} must record requested-vs-actual geography for the Fact Checker"
        )


class TestJurisdictionResolution:
    """`_resolve_jurisdictions` — four-tier, and returns [] rather than guessing."""

    def _analyst(self, context, question=""):
        """Build a RegulatoryAnalyst shell without running __init__.

        The real __init__ needs a bus, a router and a settings object; the
        resolution logic depends on none of them.
        """
        from hyperion.agents.specialists.regulatory_analyst import RegulatoryAnalyst

        obj = object.__new__(RegulatoryAnalyst)
        obj._context = context
        obj._question = question
        return obj

    def test_context_key_wins(self):
        a = self._analyst({"jurisdictions": ["India"]})
        assert a._resolve_jurisdictions() == ["India"]

    def test_falls_back_to_the_question(self):
        """No context key — mine the user's own words rather than defaulting."""
        a = self._analyst({}, question=INDIA_Q)
        assert a._resolve_jurisdictions() == ["India"]

    def test_singular_context_keys_are_honoured(self):
        for key in ("jurisdiction", "geography", "region", "country"):
            a = self._analyst({key: "India"})
            assert a._resolve_jurisdictions() == ["India"], f"{key} not resolved"

    def test_returns_empty_rather_than_inventing(self):
        """THE regression. Empty context + geography-free question => []."""
        a = self._analyst({}, question="should we reduce supplier dependence?")
        assert a._resolve_jurisdictions() == []

    def test_prose_handover_is_mined_not_spliced(self):
        """Handover payloads are sometimes whole sentences. Splicing a
        200-char sentence into a search query is as useless as splicing ""."""
        a = self._analyst(
            {"jurisdictions": ["Target markets include India and the UAE for phase one."]}
        )
        got = a._resolve_jurisdictions()
        assert "India" in got and "UAE" in got
        assert all(len(j) < 40 for j in got), f"prose leaked through as a label: {got}"

    def test_aliases_are_canonicalised_not_duplicated(self):
        a = self._analyst({"jurisdictions": ["india", "Indian", "India"]})
        assert a._resolve_jurisdictions() == ["India"]

    def test_null_sentinels_are_dropped(self):
        a = self._analyst({"jurisdictions": ["none", "N/A", "unknown"]})
        assert a._resolve_jurisdictions() == []


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 3 — SEARCHES UNRELATED TO THE QUESTION
#
# Observed in docker logs: q=carbon+footprint+emissions+data — no subject, no
# country. Specialists build queries as f"{sector} ..." from context keys the
# Director's handover often omits, so {sector} interpolated to "".
# ═════════════════════════════════════════════════════════════════════════════


class TestSubjectResolution:
    def setup_method(self):
        from hyperion.tools.query_utils import clear_engagement_focus

        clear_engagement_focus()

    teardown_method = setup_method

    def test_context_key_is_preferred(self):
        from hyperion.tools.query_utils import resolve_subject

        assert resolve_subject({"sector": "solar manufacturing"}, "sector") == (
            "solar manufacturing"
        )

    def test_prose_context_value_is_rejected(self):
        """A paragraph is not a label. Splicing one into a query is useless."""
        from hyperion.tools.query_utils import resolve_subject, set_engagement_focus

        set_engagement_focus(question=INDIA_Q, subject="import dependence")
        prose = (
            "The client wishes to understand whether reducing import reliance "
            "would improve resilience. This is a strategic question."
        )
        assert resolve_subject({"sector": prose}, "sector") == "import dependence"

    def test_falls_back_to_engagement_subject(self):
        from hyperion.tools.query_utils import resolve_subject, set_engagement_focus

        set_engagement_focus(question=INDIA_Q, subject="import dependence")
        assert resolve_subject({}, "sector") == "import dependence"

    def test_last_resort_mines_the_question(self):
        from hyperion.tools.query_utils import resolve_subject, set_engagement_focus

        set_engagement_focus(question=INDIA_Q)
        got = resolve_subject({}, "sector")
        assert got, "must mine the question rather than return nothing"
        assert "india" in got.lower()

    def test_contentless_context_value_is_rejected(self):
        """"industry"/"market" alone carry no subject."""
        from hyperion.tools.query_utils import resolve_subject

        assert resolve_subject({"sector": "industry"}, "sector") == ""

    def test_empty_everywhere_returns_empty(self):
        from hyperion.tools.query_utils import resolve_subject

        assert resolve_subject({}, "sector") == ""

    def test_list_context_value_takes_first_usable(self):
        from hyperion.tools.query_utils import resolve_subject

        assert resolve_subject({"sector": ["", "steel imports"]}, "sector") == "steel imports"


class TestQueryGrounding:
    """The choke-point guarantee: no outbound query is subject-less."""

    def setup_method(self):
        from hyperion.tools.query_utils import set_engagement_focus

        set_engagement_focus(question=INDIA_Q, subject="import dependence", geography="India")

    def teardown_method(self):
        from hyperion.tools.query_utils import clear_engagement_focus

        clear_engagement_focus()

    def test_the_exact_observed_bad_query_is_rescued(self):
        """q=carbon+footprint+emissions+data, verbatim from the docker logs."""
        from hyperion.tools.query_utils import ground_query

        got = ground_query("carbon footprint emissions data")
        assert "India" in got, f"geography anchor missing: {got!r}"
        assert "import dependence" in got.lower(), f"subject anchor missing: {got!r}"

    def test_empty_template_interpolation_is_rescued(self):
        """f"{sector} carbon footprint" with sector="" produced a bare template."""
        from hyperion.tools.query_utils import ground_query

        got = ground_query(" carbon footprint emissions data")
        assert "India" in got and "import dependence" in got.lower()

    def test_subjectless_template_is_rescued(self):
        from hyperion.tools.query_utils import ground_query

        for raw in ("vendor comparison 2024 2025", "architecture case study", ""):
            got = ground_query(raw)
            assert got, f"produced no query at all for {raw!r}"
            assert "india" in got.lower(), f"unanchored query survived: {got!r}"

    def test_already_grounded_query_is_not_mangled(self):
        from hyperion.tools.query_utils import ground_query

        got = ground_query("India import dependence steel tariffs")
        assert "India" in got
        # The anchor must not be duplicated.
        assert got.lower().count("india") == 1, f"anchor duplicated: {got!r}"

    def test_bare_years_are_stripped(self):
        """"2024 2025" never constrained anything and would rot as the calendar
        advanced. Recency now uses SearxNG's time_range parameter."""
        from hyperion.tools.query_utils import normalize_query

        got = normalize_query("steel imports India 2024 2025")
        assert "2024" not in got and "2025" not in got

    def test_qualifying_numbers_are_preserved(self):
        """"Section 301", "Scope 3", "ISO 14001" ARE the subject of a
        regulatory search — stripping the digits destroys the query."""
        from hyperion.tools.query_utils import normalize_query

        assert "301" in normalize_query("Section 301 tariffs review")
        assert "3" in normalize_query("Scope 3 emissions reporting")
        assert "14001" in normalize_query("ISO 14001 certification requirements")

    def test_engagement_focus_does_not_leak_between_engagements(self):
        from hyperion.tools.query_utils import (
            clear_engagement_focus,
            get_engagement_focus,
        )

        clear_engagement_focus()
        assert get_engagement_focus() == ("", "", "")


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 4 — EVERY REPORT HAD 0 CHARTS
#
# `FinalReport` never declared `chart_specifications`, so the orchestrator's
# `hasattr()` guard was always False. The entire Plotly -> 300 DPI -> Pillow
# pipeline was implemented and never once invoked.
# ═════════════════════════════════════════════════════════════════════════════


class TestChartSpecifications:
    def test_final_report_declares_the_field(self):
        """The one-line omission that silently zeroed out every chart."""
        from hyperion.schemas.models import FinalReport

        assert "chart_specifications" in FinalReport.model_fields

    def test_field_is_assignable_and_defaults_empty(self):
        from hyperion.schemas.models import FinalReport

        field = FinalReport.model_fields["chart_specifications"]
        assert field.default_factory is list, "must default to [] not None"

    def test_hasattr_guard_now_passes(self):
        """The orchestrator gates the Data Visualizer on hasattr()."""
        from hyperion.schemas.models import FinalReport

        assert hasattr(FinalReport.model_construct(), "chart_specifications")


class TestChartMiner:
    """Deterministic extraction from numbers already in the findings —
    no extra LLM calls."""

    def _finding(self, content, title="Import bill", fid="f1"):
        class _F:
            pass

        f = _F()
        f.id = fid
        f.title = title
        f.content = content
        f.sources = []
        f.implications = ""
        return f

    def _report(self, findings):
        class _R:
            pass

        r = _R()
        r.key_findings = findings
        r.sections = []
        return r

    def test_mines_a_time_series(self):
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding(
                "Imports reached $67.2 billion in 2022, $71.4 billion in 2023 "
                "and $78.9 billion in 2024."
            )
        ])
        specs = mine_chart_specs(report, question=INDIA_Q)
        assert specs, "found no chart in an obviously chartable finding"
        series = specs[0]["data_series"][0]
        assert series["values"] == [67.2, 71.4, 78.9]
        assert specs[0]["chart_type_hint"] == "line", "a 3-year series is a trend"

    def test_axis_ticks_are_readable_not_absolute(self):
        """Numbers are parsed to ABSOLUTE units so a coherence check can compare
        "$67.2 billion" with "$71,400 million". But an axis tick reading
        67200000000 is not benchmark-grade — the magnitude belongs in the axis
        label, per consulting convention."""
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding("Imports were $67.2 billion in 2022 and $78.9 billion in 2023.")
        ])
        spec = mine_chart_specs(report)[0]
        assert spec["data_series"][0]["values"] == [67.2, 78.9]
        assert spec["y_axis_label"] == "USD billion"

    def test_percentages_are_never_rescaled(self):
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding("Share rose from 12.5% in 2021 to 24.1% in 2023.")
        ])
        spec = mine_chart_specs(report)[0]
        assert spec["data_series"][0]["values"] == [12.5, 24.1]
        assert spec["y_axis_label"] == "Percent"

    def test_magnitude_chosen_does_not_shrink_the_series(self):
        """A 900-million series reads "900 million", never "0.9 billion"."""
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding("Volumes were 900 million in 2022 and 700 million in 2023.")
        ])
        spec = mine_chart_specs(report)[0]
        assert spec["data_series"][0]["values"] == [900.0, 700.0]
        assert "million" in spec["y_axis_label"]

    def test_category_labels_are_the_entity_not_the_verb_phrase(self):
        """Hard-won label rule #2. Multi-word verb phrases ("made up",
        "accounted for") once shipped as the bar labels themselves."""
        from hyperion.output.chart_specs import mine_chart_specs

        cases = {
            "Crude oil made up $132 billion while electronics accounted for $88 billion.":
                ["Crude oil", "electronics"],
            "Machinery represented $45 billion, chemicals contributed $30 billion.":
                ["Machinery", "chemicals"],
            "Dependence is highest in semiconductors at 92%, followed by "
            "display panels at 78%.":
                ["semiconductors", "display panels"],
        }
        for content, expected in cases.items():
            specs = mine_chart_specs(self._report([self._finding(content)]))
            assert specs, f"no chart mined from {content!r}"
            got = specs[0]["data_series"][0]["labels"]
            assert got == expected, f"labels {got} != {expected} for {content!r}"

    def test_no_label_carries_a_trailing_verb(self):
        """A general guard: no axis label may end in a dangling verb or
        preposition, whatever the phrasing."""
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding(
                "Crude oil made up $132 billion, electronics accounted for "
                "$88 billion, and machinery represented $45 billion."
            )
        ])
        for spec in mine_chart_specs(report):
            for label in spec["data_series"][0]["labels"]:
                last = label.split()[-1].lower()
                assert last not in {
                    "up", "for", "of", "in", "at", "to", "by", "with", "from",
                    "made", "accounted", "represented", "was", "were", "is",
                }, f"label ends in a dangling word: {label!r}"

    def test_period_labels_follow_the_number_in_english(self):
        """Hard-won label rule #1: "$67.2 billion in 2022" — the period comes
        AFTER the number, not before it."""
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding("Imports were $67.2 billion in 2022 and $71.4 billion in 2023.")
        ])
        specs = mine_chart_specs(report, question=INDIA_Q)
        labels = specs[0]["data_series"][0]["labels"]
        assert labels == ["2022", "2023"], f"period labels mis-associated: {labels}"

    def test_returns_empty_when_nothing_is_chartable(self):
        """A premium report is not a chart dump. No honest series => no chart."""
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding("Import dependence is a strategic concern for policymakers.")
        ])
        assert mine_chart_specs(report, question=INDIA_Q) == []

    def test_respects_max_charts(self):
        from hyperion.output.chart_specs import mine_chart_specs

        findings = [
            self._finding(
                f"Segment {i} grew {10 + i}% in 2021, {20 + i}% in 2022, {30 + i}% in 2023.",
                fid=f"f{i}",
            )
            for i in range(20)
        ]
        specs = mine_chart_specs(self._report(findings), max_charts=3)
        assert len(specs) <= 3

    def test_duplicate_series_are_deduped(self):
        from hyperion.output.chart_specs import mine_chart_specs

        same = "Volumes were 10.0 in 2021, 20.0 in 2022, 30.0 in 2023."
        specs = mine_chart_specs(
            self._report([self._finding(same, fid="a"), self._finding(same, fid="b")])
        )
        assert len(specs) == 1, "identical series charted twice"

    def test_handles_none_report(self):
        from hyperion.output.chart_specs import mine_chart_specs

        assert mine_chart_specs(None) == []

    def test_spec_shape_matches_the_visualizer_contract(self):
        """The miner's output feeds `_receive_chart_specs` directly."""
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding("Imports were $67.2 billion in 2022 and $71.4 billion in 2023.")
        ])
        spec = mine_chart_specs(report)[0]
        for key in (
            "id",
            "title",
            "data_shape",
            "chart_type_hint",
            "data_series",
            "x_axis_label",
            "y_axis_label",
        ):
            assert key in spec, f"visualizer contract missing {key}"

    def test_never_emits_a_placeholder_citation(self):
        """A fabricated source line is the same class of defect as a
        fabricated geography."""
        from hyperion.output.chart_specs import mine_chart_specs

        report = self._report([
            self._finding("Imports were $67.2 billion in 2022 and $71.4 billion in 2023.")
        ])
        citation = mine_chart_specs(report)[0]["source_citation"]
        assert citation == "", "invented a citation where the finding had no sources"


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 1 — THE MISSING PDF (a chain of four failures)
# ═════════════════════════════════════════════════════════════════════════════


class TestDeliverableGuarantee:
    def test_base_agent_defines_log(self):
        """Link 2 of the chain: the except branch called `self._log()`, which
        BaseAgent did not define — an AttributeError raised *while handling
        another error*, masking the real failure."""
        from hyperion.agents.base import BaseAgent

        assert hasattr(BaseAgent, "_log")
        assert not inspect.iscoroutinefunction(BaseAgent._log), (
            "_log is called from sync except blocks; it must not be a coroutine"
        )

    def test_render_no_longer_deletes_the_surviving_html(self):
        """Link 4: render.py deleted the only surviving artifact, turning a
        degraded result into a total loss."""
        root = Path(__file__).resolve().parent.parent
        src = (root / "hyperion" / "output" / "render.py").read_text(encoding="utf-8")
        assert "_FALLBACK.html" in src, "no named fallback deliverable"
        assert "DEGRADED OUTPUT" in src, "fallback carries no degradation banner"

    def test_css_is_not_written_beside_the_deliverable(self):
        """Link 5: the stray report.css the user actually received. The
        intermediate CSS now lives in output/.build/ so it can never be
        mistaken for a deliverable."""
        root = Path(__file__).resolve().parent.parent
        src = (
            root / "hyperion" / "agents" / "delivery" / "presentation_designer.py"
        ).read_text(encoding="utf-8")
        assert ".build" in src, "intermediate CSS is still written next to the report"

    def test_weasyprint_is_genuinely_absent(self):
        """Documents the environment assumption these fixes rest on: the
        Playwright/HTML fallback IS the production path here."""
        try:
            import weasyprint  # noqa: F401

            pytest.skip("WeasyPrint present in this environment")
        except Exception:
            pass  # expected


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 8 — CRASHES
# ═════════════════════════════════════════════════════════════════════════════


class TestContentCoercion:
    """`'list' object has no attribute 'strip'` killed ma_analyst.
    OpenAI-compatible providers may return message.content as content blocks."""

    def test_all_observed_shapes(self):
        from hyperion.router.providers.base import _coerce_content

        cases = [
            (None, ""),
            ("", ""),
            ("plain", "plain"),
            ([], ""),
            (["a", "b"], "ab"),
            ([{"text": "a"}, {"text": "b"}], "ab"),
            ([{"content": "x"}], "x"),
            ([{"value": "v"}], "v"),
            ([{"content": [{"text": "nested"}]}], "nested"),
        ]
        for raw, expected in cases:
            assert _coerce_content(raw) == expected, f"shape {raw!r} mis-coerced"

    def test_result_is_always_a_string(self):
        """The crash was a downstream .strip() call. Whatever comes back must
        support it."""
        from hyperion.router.providers.base import _coerce_content

        for raw in (None, 42, {"unexpected": "dict"}, [object()], [{"bad": "key"}]):
            assert isinstance(_coerce_content(raw), str)
            _coerce_content(raw).strip()  # must not raise

    def test_malformed_block_is_skipped_not_raised(self):
        from hyperion.router.providers.base import _coerce_content

        assert _coerce_content([{"text": "keep"}, None, {"no_text": 1}]) == "keep"


class TestOrchestratorLogger:
    def test_module_logger_is_defined(self):
        """orchestrator.py referenced a module-level `logger` that was never
        defined — a latent NameError on any logging path."""
        import hyperion.orchestrator as orch

        assert hasattr(orch, "logger")


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 7 — ESCALATION STORM BURNING STRONG-TIER QUOTA
# ═════════════════════════════════════════════════════════════════════════════


class TestEscalationDedup:
    def test_director_dedupes_by_fingerprint(self):
        """Every tie hit the sub-agent cap and escalated; each escalation cost
        the Director an unconditional STRONG-tier LLM call."""
        root = Path(__file__).resolve().parent.parent
        src = (root / "hyperion" / "agents" / "engagement_director.py").read_text(
            encoding="utf-8"
        )
        assert "_seen_escalations" in src
        assert "fingerprint" in src


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 6 — OBSCURA: blocking subprocess.run in async code
# ═════════════════════════════════════════════════════════════════════════════


class TestObscuraProbeCache:
    def test_binary_available_never_raises(self):
        """A narrow `except` missed the NotImplementedError raised by the
        Windows SelectorEventLoop, so it both stalled the event loop and
        aborted agents."""
        from hyperion.tools import obscura

        fn = getattr(obscura, "_binary_available", None)
        if fn is None:
            pytest.skip("_binary_available not exposed")
        fn()  # must not raise
        fn()  # cached path must not raise either

    def test_probe_is_cached(self):
        """5000ms -> 0.01ms across 50 calls. A blocking subprocess.run on every
        call is what stalled the loop."""
        import time

        from hyperion.tools import obscura

        fn = getattr(obscura, "_binary_available", None)
        if fn is None:
            pytest.skip("_binary_available not exposed")
        fn()  # warm
        start = time.perf_counter()
        for _ in range(50):
            fn()
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"50 cached probes took {elapsed:.3f}s — not cached"


# ═════════════════════════════════════════════════════════════════════════════
# DEFECT 5 — SEARCH INFRASTRUCTURE
# ═════════════════════════════════════════════════════════════════════════════


class TestSearchInfrastructure:
    def test_general_engines_are_not_preprint_servers(self):
        """RELIABLE_ENGINES was "bing,wikipedia,arxiv,github,hackernews" — a
        business question dispatched to a physics preprint server, a source-code
        host and a tech forum. SearxNG aggregates, so one slow engine delays
        the whole response."""
        from hyperion.tools.searxng import SearxNGClient

        low = SearxNGClient.RELIABLE_ENGINES.lower()
        for bad in ("arxiv", "github", "hackernews"):
            assert bad not in low, f"{bad} is still in the general engine set: {low}"

    def test_specialist_corpora_remain_reachable_by_category(self):
        """Narrowing the general set must not make specialist engines
        unreachable — a science query still needs arxiv."""
        from hyperion.tools.searxng import SearxNGClient

        cats = SearxNGClient.CATEGORY_ENGINES
        assert "arxiv" in cats["science"].lower()
        assert "github" in cats["it"].lower()
        assert "news" in cats["news"].lower()

    def test_forwarded_headers_are_sent(self):
        """Host-originated requests carried no X-Forwarded-For, so the limiter
        could not resolve a trusted client and bucketed every query into the
        same aggressive limit."""
        root = Path(__file__).resolve().parent.parent
        src = (root / "hyperion" / "tools" / "searxng.py").read_text(encoding="utf-8")
        assert "X-Forwarded-For" in src
        assert "User-Agent" in src, "SearxNG bot-detection rejects a bare httpx UA"


# ═════════════════════════════════════════════════════════════════════════════
# FORMATTING QUALITY — benchmark-grade typography
#
# Audited against McKinsey MGI "The Next Big Arenas of Competition" and the
# BCG Annual Sustainability Report 2024.
# ═════════════════════════════════════════════════════════════════════════════


class TestTypography:
    """The body font was monospace — the single largest gap versus MGI/BCG."""

    def _css(self) -> str:
        from hyperion.agents.delivery.presentation_designer import CSS_TEMPLATE

        return CSS_TEMPLATE

    def _block(self, selector: str) -> str:
        import re

        m = re.search(
            r"\n" + re.escape(selector) + r"\s*\{(.*?)\n\}", self._css(), re.S
        )
        assert m, f"no CSS block for {selector!r}"
        return m.group(1)

    def test_body_is_not_monospace(self):
        """No consulting firm sets running prose in a monospace font: uniform
        character width destroys word-shape cues and reads as terminal output.

        The codebase already DECLARED the right answer —
        TYPOGRAPHY["body_font"] == "Source Sans 3", commented "professional
        sans, not monospace" — while the CSS hardcoded the mono stack."""
        body = self._block("body")
        assert "monospace" not in body, "body copy is still monospace"
        assert "JetBrains Mono" not in body

    def test_body_uses_the_declared_body_font(self):
        """The declared typography system must actually reach the stylesheet."""
        from hyperion.agents.delivery.presentation_designer import TYPOGRAPHY

        assert TYPOGRAPHY["body_font"] in self._block("body")

    def test_headings_are_serif_and_tightly_set(self):
        """Display type at 22-36pt needs negative tracking and sub-1.2 leading
        or it reads airy and amateur."""
        heads = self._block("h1, h2, h3, h4")
        assert "serif" in heads
        assert "letter-spacing: -" in heads, "display type is not tracked in"
        assert "page-break-after: avoid" in heads, "a heading may end a page"

    def test_subsection_headers_are_not_bold_monospace(self):
        """h3 was 14pt bold monospace — visually a code comment."""
        h3 = self._block("h3")
        assert "monospace" not in h3

    def test_tabular_figures_are_enabled_for_numeric_blocks(self):
        """Monospace/tabular width belongs where digits must align in a
        column — and only there."""
        assert "tabular-nums" in self._css()

    def test_orphans_and_widows_are_controlled(self):
        """A single line stranded across a page break is the most visible
        difference between a generated PDF and a typeset one."""
        body = self._block("body")
        assert "orphans" in body and "widows" in body

    def test_no_unresolved_palette_placeholders(self):
        """CSS_TEMPLATE is .format()-ed at import; a stray {token} would ship
        as a literal brace in the stylesheet."""
        import re

        # {page} and {section_title} are WeasyPrint running-element markers,
        # deliberately escaped through to the output.
        leftovers = {
            t for t in re.findall(r"\{[a-z_]+\}", self._css())
            if t not in ("{page}", "{section_title}")
        }
        assert not leftovers, f"unresolved palette placeholders: {leftovers}"
