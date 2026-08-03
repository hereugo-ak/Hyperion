"""W-09: the narrative boundary — telemetry cannot reach client prose.

WHAT THIS ITEM FIXES
--------------------
RC-9: ``FinalReport`` carried client prose and operator telemetry in one
object, and the client template read both. The shipped artifact showed dict
reprs, agent names, ``Fact Checker``, ``hallucinat`` and em dashes — every
prior defence was a string filter applied AFTER the leak, and the filter is a
backstop, not a boundary. W-09 builds the boundary:

* ``ClientProse`` — a validated narrative type. Its factory raises on all six
  telemetry categories; it never sanitises.
* ``ClientReport`` — the client-facing view of a report, carrying no
  telemetry attributes at all, so a client template cannot resolve one.
* ``EngagementTelemetry`` — telemetry's own destination: an operator JSON +
  HTML artifact under ``reports/diagnostics/``.
* ``BANNED_SUBSTRINGS`` stays exactly as it is — the render-time backstop
  that should never fire after this item.

These tests defend the boundary itself. If a future change lets telemetry
back into the client path, one of these fails loudly.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from jinja2 import BaseLoader, Environment

from hyperion.schemas.models import (
    AgentName,
    ClaimStatus,
    ConfidenceLevel,
)
from hyperion.schemas.narrative import (
    ClientProse,
    ClientReport,
    EngagementTelemetry,
    write_telemetry_artifact,
)

# ── ClientProse: the six rejection categories (audit verification block) ─────


class TestClientProseRejectsTelemetry:
    """The audit's own verification list, plus the registry-derived forms."""

    @pytest.mark.parametrize(
        "bad",
        [
            "{'a': 1}",                    # 1. dict repr
            '{"a": 1}',                    # 1b. double-quoted dict repr
            "em\u2014dash",                # 2. U+2014
            "en\u2013dash",                # 2b. U+2013
            "Fact Checker",                # 3. agent display name
            "fact_checker",                # 3b. agent wire name
            "market_analyst",              # 3c. specialist wire name
            "Confidence: low",             # 4. confidence literal
            "confidence is low",           # 4b. confidence rendered as prose
            "hallucinated",                # 5. verification state
            "UNVERIFIABLE",                # 5b. case-insensitive
            "unverified claim",            # 5c. claim-level telemetry
            "gap_12",                      # 6. gap identifier
            "GAP_market_analyst_1719",     # 6b. sub-agent gap id shape
        ],
    )
    def test_of_raises(self, bad: str) -> None:
        with pytest.raises(ValueError):
            ClientProse.of(bad)

    def test_direct_construction_bypasses_validation_is_impossible(self) -> None:
        with pytest.raises(TypeError):
            ClientProse("{'a': 1}")

    @pytest.mark.parametrize(
        "good",
        [
            "The market is large and growing.",
            "Confidence remains strong across all dimensions.",
            "Results were verified against two independent sources.",
            "Demand is plausible given current trends.",
            "Costs are low and margins are high.",
            "Of 48 claims checked, 85% were corroborated by an independent source.",
            "India should reduce its dependence on imports.",
        ],
    )
    def test_legitimate_prose_is_accepted(self, good: str) -> None:
        assert str(ClientProse.of(good)) == good

    def test_rejection_sets_are_derived_from_live_enums(self) -> None:
        """A new agent name or claim status is banned without editing the list."""
        for agent in AgentName:
            display = agent.value.replace("_", " ").title()
            with pytest.raises(ValueError):
                ClientProse.of(display)
            with pytest.raises(ValueError):
                ClientProse.of(agent.value)
        # The unambiguous verification states all raise; 'verified' and
        # 'plausible' are ordinary English and deliberately do not.
        for status in ClaimStatus:
            if status.value in {"verified", "plausible"}:
                continue
            with pytest.raises(ValueError):
                ClientProse.of(status.value.upper())

    def test_it_never_sanitises(self) -> None:
        """Sanitising hides the upstream bug; the factory must raise, not strip."""
        for bad in ("{'a': 1}", "Fact Checker", "em\u2014dash"):
            try:
                ClientProse.of(bad)
            except ValueError:
                continue
            raise AssertionError(f"ClientProse accepted or stripped {bad!r}")


# ── ClientReport: the client view carries no telemetry ───────────────────────


def _report_stub(**overrides):
    from types import SimpleNamespace

    base = dict(
        engagement_id="ENG-W09",
        question="Should Acme enter the storage market?",
        recommendation=SimpleNamespace(value="conditional"),
        recommendation_rationale="Two zones clear with margin.",
        executive_summary="Summary.",
        critical_assumptions=["Pack prices fall."],
        key_findings=[SimpleNamespace(title="Finding", content="Content.")],
        sections=[
            SimpleNamespace(
                id="market",
                title="Market",
                key_insight="Insight.",
                body="Body.",
                implications="Implication.",
                confidence=ConfidenceLevel.HIGH,
            )
        ],
        risk_analysis=None,
        limitations=["Pricing data is quarterly."],
        total_sources=34,
        total_data_points=112,
        generated_at=datetime(2026, 7, 31, tzinfo=UTC),
        agents_used=["market_analyst", "fact_checker"],
        quality_score=SimpleNamespace(total_score=4.1),
        fact_check_report=SimpleNamespace(total_claims_checked=48),
        confidence_breakdown={"market": ConfidenceLevel.HIGH},
        contradictions=[],
        is_degraded=False,
        chart_specifications=[{"title": "t"}],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


class TestClientReportIsTelemetryFree:
    def test_from_report_builds_a_valid_view(self) -> None:
        view = ClientReport.from_report(_report_stub())
        assert view.recommendation == "conditional"
        assert view.total_sources == 34
        assert len(view.sections) == 1

    @pytest.mark.parametrize(
        "attr",
        [
            "agents_used",
            "quality_score",
            "fact_check_report",
            "confidence_breakdown",
            "contradictions",
            "is_degraded",
            "chart_specifications",
            "confidence",
        ],
    )
    def test_telemetry_attributes_do_not_exist(self, attr: str) -> None:
        view = ClientReport.from_report(_report_stub())
        assert not hasattr(view, attr), (
            f"ClientReport exposes {attr}; a client template can resolve it"
        )

    def test_finding_view_carries_no_agent_or_confidence(self) -> None:
        view = ClientReport.from_report(_report_stub())
        finding = view.key_findings[0]
        assert not hasattr(finding, "agent")
        assert not hasattr(finding, "confidence")

    def test_section_view_carries_no_agent_or_confidence(self) -> None:
        view = ClientReport.from_report(_report_stub())
        section = view.sections[0]
        assert not hasattr(section, "agent")
        assert not hasattr(section, "confidence")

    def test_a_telemetry_field_in_narrative_fails_at_construction(self) -> None:
        """If upstream leaks an agent name into a section title, the leak
        fails HERE — at the named transformation — not on the printed page."""
        from types import SimpleNamespace

        stub = _report_stub(
            sections=[
                SimpleNamespace(
                    id="s",
                    title="Findings by Fact Checker",
                    key_insight="i",
                    body="b",
                    implications=None,
                    confidence=ConfidenceLevel.HIGH,
                )
            ]
        )
        with pytest.raises(ValueError):
            ClientReport.from_report(stub)


# ── client_template_isolation: the template cannot reach telemetry ───────────


class TestClientTemplateIsolation:
    """The enforcement test the audit asks for: render the production template
    against a ClientReport and prove no telemetry attribute resolves."""

    def _render_with_client_report(self) -> str:
        from hyperion.agents.delivery.presentation_designer import HTML_TEMPLATE

        view = ClientReport.from_report(_report_stub())
        env = Environment(loader=BaseLoader(), autoescape=True)
        env.filters["md_to_html"] = lambda v: v or ""
        env.filters["clean_dict_repr"] = lambda v: v or ""
        template = env.from_string(HTML_TEMPLATE)
        from types import SimpleNamespace

        return template.render(
            report=view,
            cover_image=None,
            section_images={},
            section_charts={},
            palette=SimpleNamespace(
                cream="#F5F4EE", warm_gray="#8A8580", terracotta="#C4573A"
            ),
            css_content="",
            risk_analysis_html="",
            appendix_sources_html="",
            endnotes_html="",
        )

    def test_template_renders_against_the_client_view(self) -> None:
        html = self._render_with_client_report()
        assert "Should Acme enter the storage market?" in html

    def test_no_telemetry_token_reaches_the_rendered_page(self) -> None:
        html = self._render_with_client_report()
        for token in (
            "Fact Checker",
            "fact_checker",
            "market_analyst",
            "agents_used",
            "Technical Appendix",
            "technical-appendix",
            "hallucinat",
            "Confidence: low",
        ):
            assert token not in html, f"telemetry token in client HTML: {token!r}"

    def test_template_source_never_reads_a_telemetry_attribute(self) -> None:
        from hyperion.agents.delivery.presentation_designer import HTML_TEMPLATE

        for forbidden in (
            "report.agents_used",
            "report.confidence",
            "report.quality_score",
            "report.fact_check_report",
            "report.confidence_breakdown",
            "report.contradictions",
            "finding.confidence",
            "section.confidence",
            "recommendation.value",
        ):
            assert forbidden not in HTML_TEMPLATE, (
                f"template still reads {forbidden} — telemetry is resolvable"
            )


# ── EngagementTelemetry: the operator destination ────────────────────────────


class TestTelemetryHasItsOwnDestination:
    def test_from_report_captures_the_scorecard(self) -> None:
        tel = EngagementTelemetry.from_report(_report_stub())
        assert tel.agents_used == ["market_analyst", "fact_checker"]
        assert tel.section_confidence == {"market": "high"}
        assert tel.is_degraded is False

    def test_write_artifact_lands_under_reports_diagnostics(self, tmp_path, monkeypatch) -> None:
        import hyperion.schemas.narrative as narrative

        monkeypatch.setattr(narrative, "_reports_dir", lambda: tmp_path)
        path = write_telemetry_artifact(_report_stub())
        assert path.parent == tmp_path / "diagnostics"
        assert path.name == "telemetry_ENG-W09.json"
        data = json.loads(path.read_text())
        assert data["agents_used"] == ["market_analyst", "fact_checker"]
        html = (tmp_path / "diagnostics" / "telemetry_ENG-W09.html").read_text()
        assert "Engagement telemetry" in html
        assert "market_analyst" in html  # operator copy — agent names belong here

    def test_artifact_is_never_the_deliverable_path(self, tmp_path, monkeypatch) -> None:
        import hyperion.schemas.narrative as narrative

        monkeypatch.setattr(narrative, "_reports_dir", lambda: tmp_path)
        path = write_telemetry_artifact(_report_stub())
        assert "output" not in path.parts
        assert path.suffix == ".json"


# ── The backstop stays armed ─────────────────────────────────────────────────


class TestBackstopStaysArmed:
    def test_banned_substrings_unchanged(self) -> None:
        from hyperion.output.page_audit import BANNED_SUBSTRINGS

        for token in ("—", "–", "hallucinat", "Fact Checker", "unverified claim", "{'"):
            assert token in BANNED_SUBSTRINGS

    def test_scan_still_fires_on_raw_telemetry_text(self) -> None:
        """If telemetry somehow reaches a rendered page, the audit must still
        see it. W-09 makes that impossible upstream; this stays as the alarm."""
        from hyperion.output.page_audit import scan_text_integrity

        hits = scan_text_integrity("This section was written by the Fact Checker.")
        assert hits, "backstop no longer fires on an agent name"

    def test_scan_is_clean_on_client_prose(self) -> None:
        from hyperion.output.page_audit import scan_text_integrity

        assert scan_text_integrity("The market is large and growing.") == []
