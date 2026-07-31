"""Schema boundary: serialized object reprs are unrepresentable (P2-09).

``KeyFinding.content``, ``AnalysisSection.body`` and ``AnalysisSection.implications``
must reject a string matching ``{['"]\\w+['"]\\s*:`` at construction. Fail at
the Pydantic layer, not at render.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hyperion.schemas.models import AnalysisSection, ConfidenceLevel, KeyFinding


def _finding(**over) -> KeyFinding:
    base = dict(
        id="f1",
        agent="market_analyst",
        finding_type="market_size",
        title="T",
        content="b" * 100,
        confidence=ConfidenceLevel.MEDIUM,
    )
    base.update(over)
    return KeyFinding(**base)


def _section(**over) -> AnalysisSection:
    base = dict(
        id="s1",
        title="T",
        agent="market_analyst",
        key_insight="k",
        body="b" * 100,
        implications="i",
        confidence=ConfidenceLevel.MEDIUM,
    )
    base.update(over)
    return AnalysisSection(**base)


class TestKeyFindingContentRejectsReprs:
    @pytest.mark.parametrize(
        "bad",
        [
            "{'name': 'TAM', 'value': '$12B'}",
            'TAM: {"name": "TAM", "value": "$12B"}',
            "DCF Valuation: {'name': 'DCF Valuation', 'value': '$12.5B'}",
        ],
    )
    def test_repr_content_raises(self, bad):
        with pytest.raises(ValidationError):
            _finding(content=bad)

    def test_clean_content_accepted(self):
        f = _finding(content="The market is viable at high penetration. " * 3)
        assert f.content.startswith("The market")


class TestAnalysisSectionRejectsReprs:
    def test_repr_body_raises(self):
        with pytest.raises(ValidationError):
            _section(body="Build vs Buy: {'recommendation': 'buy'} " * 5)

    def test_repr_implications_raises(self):
        with pytest.raises(ValidationError):
            _section(implications="TAM: {'name': 'TAM'}")

    def test_repr_key_insight_raises(self):
        with pytest.raises(ValidationError):
            _section(key_insight="{'name': 'DCF Valuation'}")

    def test_clean_section_accepted(self):
        s = _section()
        assert s.body == "b" * 100
