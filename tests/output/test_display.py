"""Tests for hyperion/output/display.py (P2-09, P2-10).

The two leaks that shipped to clients:
  - ``DCF Valuation: {'name': 'DCF Valuation', 'value': '$12.5B - $38.9B', ...``
    (str() on a model, truncated at 117 chars)
  - whole ``sources`` arrays json.dumps'd into chapter prose.

Both must be unrepresentable after this module.
"""

from __future__ import annotations

import pytest

from hyperion.output.display import DisplayError, display_value, humanize
from hyperion.schemas.models import FinancialMetric


class TestDisplayValue:
    def test_scalar_passthrough(self):
        assert display_value("$12.5B") == "$12.5B"
        assert display_value(42) == "42"
        assert display_value(None) == ""

    def test_float_trims_trailing_zero(self):
        assert display_value(12.0) == "12"
        assert display_value(12.5) == "12.5"

    def test_financial_metric_uses_its_presenter(self):
        m = FinancialMetric(
            name="DCF Valuation",
            value="insufficient data",
            unit="$",
            low_estimate=12_500_000_000,
            high_estimate=38_900_000_000,
        )
        out = display_value(m)
        assert "{" not in out and "'" not in out
        assert "12,500,000,000" in out or "$" in out

    def test_financial_metric_missing_value_is_honest(self):
        m = FinancialMetric(name="TAM (Triangulated)", value="Parse error", unit="$")
        out = display_value(m)
        # Never the raw repr, never "Parse error" passthrough as a dict dump
        assert "{" not in out

    def test_dict_becomes_prose(self):
        out = display_value({"tam_triangulated": "$12B", "growth_rate": 7})
        assert out == "Tam Triangulated: $12B · Growth Rate: 7"

    def test_dict_skips_empty_fields(self):
        out = display_value({"name": "TAM", "low_estimate": None, "value": "$12B"})
        assert "Low Estimate" not in out
        assert "Name: TAM" in out

    def test_list_of_scalars(self):
        assert display_value(["alpha", "beta"]) == "alpha, beta"

    def test_never_emits_repr(self):
        out = display_value({"name": "DCF Valuation", "value": "$12.5B"})
        assert not out.startswith("{")
        assert "'name':" not in out

    def test_opaque_object_raises_not_reprs(self):
        class Opaque:
            __slots__ = ()

        with pytest.raises(DisplayError):
            display_value(Opaque())


class TestHumanize:
    def test_plain_prose_unchanged(self):
        s = "The market is viable at high penetration."
        assert humanize(s) == s

    def test_none_becomes_empty(self):
        assert humanize(None) == ""

    def test_label_prefixed_dict_repr_is_parsed(self):
        # The exact shape that leaked: does NOT start with '{'
        s = "TAM: {'name': 'TAM (Triangulated)', 'value': '$12B'}"
        out = humanize(s)
        assert "{" not in out
        assert "'name'" not in out
        assert "TAM" in out
        assert "$12B" in out

    def test_dict_repr_with_none_and_true_parses(self):
        # json.loads would choke on None/True; ast.literal_eval must not.
        s = "DCF Valuation: {'value': None, 'verified': True}"
        out = humanize(s)
        assert "{" not in out
        assert "Verified: yes" in out

    def test_unparseable_repr_raises_not_truncates(self):
        s = "Build vs Buy: {'recommendation': BUY, !!!"
        with pytest.raises(DisplayError):
            humanize(s)

    def test_non_string_model_routed_to_display_value(self):
        m = FinancialMetric(name="WACC", value=0.09, unit="%")
        out = humanize(m)
        assert "{" not in out
