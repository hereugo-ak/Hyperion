"""Tests for the MBB front/back matter — At-a-glance, Endnotes, Technical appendix (fix 4.5).

WHAT THIS FIX WAS FOR
---------------------
The audit (fix-plan row 4.5) records that the shipped PDF was missing three
sections that every MGI/BCG report carries:

* **At a Glance** — the whole argument on one page, *before* the table of
  contents, so a partner who reads exactly one page still gets the answer.
* **Endnotes** — numbered continuously across the document and grouped by the
  chapter that cited them, so a claim can be walked back to its evidence.
* **Technical appendix** — how well the work was done, and what it failed to
  achieve.

The third was the worst of the three, and not because it was merely absent.
``FinalReport`` already carried ``quality_score``, ``confidence_breakdown``,
``contradictions``, ``fact_check_report`` and ``limitations``. The pipeline
computed all of them and **none reached the PDF**: the system graded itself and
then threw the scorecard away. The data was there; only the page was missing.

WHAT THESE TESTS DEFEND
-----------------------
The weak version of this test would assert ``"Endnotes" in HTML_TEMPLATE``. That
passes over a heading with an empty page under it, which is precisely the
failure mode — and it is a failure mode this fix hit for real. Three field names
in the first draft of ``_build_technical_appendix_html`` were wrong:

* ``QualityScore.dimensions`` is a ``list[QualityDimension]``, not a ``dict``;
  the draft guarded with ``isinstance(dimensions, dict)`` and so would have
  rendered the dimension table on **no report ever**.
* ``FactCheckReport`` exposes ``total_claims_checked`` / ``verified_count``, not
  ``claims_checked`` / ``claims_verified``; both reads returned ``None`` and the
  entire fact-check block was skipped.
* ``Contradiction`` carries ``finding_a`` / ``finding_b``, not ``description`` /
  ``topic``; the draft's ``or str(item)`` fallback would have printed a **raw
  pydantic repr** into a client-facing PDF.

Every one of those was invisible because the code used
``getattr(obj, "field", default)``. A defensive default converts a schema
mismatch into a plausible-looking empty section. So these tests:

1. build the builders' input from **real pydantic models**, never stubs or
   ``SimpleNamespace`` — a stub cannot reject a wrong field name, and a test
   whose fixture is wrong in the same direction as the code is worthless;
2. assert on **content pulled out of the returned HTML**, not on the presence of
   headings;
3. include ``TestTheSchemaContractIsReal``, which asserts the field names
   directly against ``model_fields``. If the schema is renamed, that class fails
   loudly instead of the appendix silently emptying.

`TestTheAppendixCannotBeSilentlyEmpty` is the centre of this file: each of its
tests fails if the corresponding block is removed from the builder or if its
field reads regress to the wrong names.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from hyperion.agents.delivery.presentation_designer import (
    HTML_TEMPLATE,
    PresentationDesigner,
)
from hyperion.schemas.models import (
    AnalysisSection,
    ConfidenceLevel,
    Contradiction,
    ContradictionType,
    FactCheckReport,
    FinalReport,
    QualityDimension,
    QualityScore,
    Recommendation,
    Source,
    SourceCredibility,
)

ROOT = Path(__file__).resolve().parents[1]
SRC = (
    ROOT / "hyperion" / "agents" / "delivery" / "presentation_designer.py"
).read_text()


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures — real models only.
# ─────────────────────────────────────────────────────────────────────────────


def _designer() -> PresentationDesigner:
    """A designer instance without running __init__ (needs no agent wiring)."""
    return PresentationDesigner.__new__(PresentationDesigner)


def _sources(chapter: int) -> list[Source]:
    """Four sources of which two share a URL, so de-duplication is testable."""
    return [
        Source(
            id=f"s{chapter}-1",
            title=f"Cost Curve Review {chapter}",
            url=f"https://example.gov/cost/{chapter}",
            credibility=SourceCredibility.GOVERNMENT,
        ),
        Source(
            id=f"s{chapter}-2",
            # Ampersand + angle brackets: the escaping regression fixed in 4.5.
            title=f"Rate Design & <Queue> Analysis {chapter}",
            url=f"https://example.org/rate?zone={chapter}&mode=full",
            credibility=SourceCredibility.INDUSTRY_REPORT,
        ),
        Source(
            id=f"s{chapter}-3",
            title="Shared Methodology Note",
            url="https://example.org/shared",
            credibility=SourceCredibility.PEER_REVIEWED,
        ),
        Source(
            id=f"s{chapter}-4",
            title="Shared Methodology Note",
            url="https://example.org/shared",  # duplicate of the previous entry
            credibility=SourceCredibility.PEER_REVIEWED,
        ),
    ]


def _section(chapter: int, *, sources: list[Source] | None = None) -> AnalysisSection:
    return AnalysisSection(
        id=f"section_{chapter}",
        title=f"Chapter {chapter} Title",
        agent="market_analyst",
        key_insight="Insight.",
        body="Body.",
        implications="Implications.",
        findings=[],
        sources=_sources(chapter) if sources is None else sources,
        confidence=ConfidenceLevel.HIGH,
    )


def _quality_score() -> QualityScore:
    """`dimensions` is a LIST — the wrong-guard bug lived exactly here."""
    return QualityScore(
        dimensions=[
            QualityDimension(
                dimension_id="evidence_sufficiency",
                name="Evidence sufficiency",
                score=4,
                weight=0.25,
                feedback="Adequate.",
                critical=True,
            ),
            QualityDimension(
                dimension_id="analytical_depth",
                name="Analytical depth",
                score=3,
                weight=0.30,
                feedback="Thin downside case.",
            ),
        ],
        total_score=4.1,
        threshold=4.0,
        approved=True,
        iteration=2,
        gaps=["Hourly dispatch data unavailable."],
    )


def _contradictions() -> list[Contradiction]:
    """Fields are `finding_a`/`finding_b`, NOT `description`/`topic`."""
    return [
        Contradiction(
            id="c1",
            agent_a="market_analyst",
            agent_b="financial_analyst",
            finding_a="Four zones clear the spread.",
            finding_b="Only two clear once curtailment is priced.",
            contradiction_type=ContradictionType.DATA_CONFLICT,
            resolved=False,
        ),
        Contradiction(
            id="c2",
            agent_a="regulatory_analyst",
            agent_b="market_analyst",
            finding_a="Queue clears in 30 months.",
            finding_b="Precedent implies 42 months.",
            contradiction_type=ContradictionType.INTERPRETATION_CONFLICT,
            resolution="Adopted the 42-month figure.",
            resolved=True,
        ),
    ]


def _fact_check() -> FactCheckReport:
    """`total_claims_checked`/`verified_count` — not `claims_checked`."""
    return FactCheckReport(
        claims=[],
        total_claims_checked=48,
        verified_count=41,
        unverified_count=5,
        contradicted_count=2,
        verification_rate=0.854,
        hallucinated_citation_count=0,
        evidence_chain_break_count=1,
    )


def _report(**overrides: object) -> FinalReport:
    """A FinalReport with all 4.5 inputs populated unless overridden."""
    base: dict[str, object] = {
        "engagement_id": "TEST-001",
        "question": "Should we enter the grid-scale storage market?",
        "recommendation": Recommendation.CONDITIONAL,
        "confidence": ConfidenceLevel.MEDIUM,
        "executive_summary": "Summary.",
        "sections": [_section(1), _section(2)],
        "key_findings": [],
        "critical_assumptions": ["Pack prices reach $60/kWh."],
        "limitations": ["Pricing data is quarterly."],
        "agents_used": ["market_analyst"],
        "total_sources": 34,
        "total_data_points": 112,
        "recommendation_rationale": "Two zones clear with margin.",
        "quality_score": _quality_score(),
        "confidence_breakdown": {
            "market_sizing": ConfidenceLevel.HIGH,
            "unit_economics": ConfidenceLevel.MEDIUM,
        },
        "contradictions": _contradictions(),
        "fact_check_report": _fact_check(),
    }
    base.update(overrides)
    return FinalReport(**base)  # type: ignore[arg-type]


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", " ", html)


# ─────────────────────────────────────────────────────────────────────────────


class TestTheSchemaContractIsReal:
    """Pin the field names the builders read.

    These are the cheapest tests in the file and they are the ones that would
    have caught all three of the wrong-field-name bugs in the first draft. If a
    schema field is renamed, this class fails with the name in the message —
    rather than the technical appendix quietly losing a section.
    """

    def test_quality_score_dimensions_is_a_list_not_a_dict(self) -> None:
        anno = str(QualityScore.model_fields["dimensions"].annotation)
        assert "list" in anno, (
            "QualityScore.dimensions must be a list. The first draft of "
            "_build_technical_appendix_html guarded it with isinstance(dict), "
            f"which would render the table never. Got: {anno}"
        )

    def test_quality_dimension_score_is_an_int_between_1_and_5(self) -> None:
        field = QualityDimension.model_fields["score"]
        assert field.annotation is int
        # The builder prints "N/5"; that is only honest if 5 is the ceiling.
        bounds = {
            type(m).__name__: getattr(m, "ge", getattr(m, "le", None))
            for m in field.metadata
        }
        assert bounds.get("Ge") == 1 and bounds.get("Le") == 5, (
            f"score bounds changed; the builder renders 'N/5'. Got {field.metadata}"
        )

    @pytest.mark.parametrize(
        "field",
        [
            "total_claims_checked",
            "verified_count",
            "unverified_count",
            "contradicted_count",
            "verification_rate",
            "hallucinated_citation_count",
            "evidence_chain_break_count",
        ],
    )
    def test_fact_check_report_field_names(self, field: str) -> None:
        assert field in FactCheckReport.model_fields, (
            f"FactCheckReport.{field} is read by _build_technical_appendix_html. "
            "The first draft read 'claims_checked'/'claims_verified', which do "
            "not exist, and silently skipped the whole block."
        )

    @pytest.mark.parametrize("field", ["finding_a", "finding_b", "agent_a", "agent_b"])
    def test_contradiction_field_names(self, field: str) -> None:
        assert field in Contradiction.model_fields, (
            f"Contradiction.{field} is read by the appendix builder. The first "
            "draft read 'description'/'topic' and fell back to str(item), which "
            "would print a raw pydantic repr into the PDF."
        )

    def test_the_builders_do_not_use_defensive_getattr_defaults(self) -> None:
        """`getattr(x, "f", default)` is what hid all three bugs.

        This is a source-level assertion on purpose. The bug class is not "wrong
        output" — it is "a wrong field name produces *plausible* output", which
        no output assertion can catch. Banning the construct in these two
        builders is the only check that bites before the mistake is made.

        Docstrings are stripped before scanning: the builders' own docstrings
        discuss the banned construct by name, and the first version of this test
        matched those prose mentions and failed on correct code. Recorded rather
        than quietly patched — a test that fires on its own documentation is a
        false positive, and false positives get suppressed, which is how a gate
        stops gating.
        """
        start = SRC.index("def _build_endnotes_html")
        end = SRC.index("# Step 7: Generate PDF with WeasyPrint")
        body = SRC[start:end]
        code_only = re.sub(r'""".*?"""', "", body, flags=re.S)
        offenders = re.findall(r"getattr\([^)]*,\s*[^)]*,\s*[^)]+\)", code_only)
        assert not offenders, (
            "The 4.5 builders must use direct attribute access so a schema "
            "mismatch raises instead of rendering an empty section. Found: "
            f"{offenders}"
        )


class TestEndnotesAreAnApparatusNotAList:
    """A flat source list already existed; it was not an endnote apparatus."""

    def test_numbering_is_continuous_across_chapters(self) -> None:
        html = _designer()._build_endnotes_html(_report())
        numbers = [int(n) for n in re.findall(r"<li value='(\d+)'>", html)]
        assert numbers == list(range(1, len(numbers) + 1)), (
            "Endnote numbers must run 1..N across the whole document. Jinja's "
            f"loop.index resets per section, which is why this is built "
            f"server-side. Got: {numbers}"
        )

    def test_numbering_does_not_restart_in_the_second_chapter(self) -> None:
        """The specific regression a per-section Jinja loop would reintroduce."""
        html = _designer()._build_endnotes_html(_report())
        assert html.count("<li value='1'>") == 1, (
            "'1' appeared more than once: numbering restarted per chapter."
        )

    def test_each_chapter_heading_is_present_so_notes_are_traceable(self) -> None:
        html = _designer()._build_endnotes_html(_report())
        for chapter in (1, 2):
            assert f"Chapter {chapter} Title" in html, (
                "Each note group must name its chapter — that is what lets a "
                "reader walk from a claim to its evidence."
            )

    def test_duplicate_urls_are_collapsed_within_a_chapter(self) -> None:
        html = _designer()._build_endnotes_html(_report())
        # The fixture gives every chapter 4 sources, two sharing one URL.
        # 2 chapters x 3 distinct = 6.
        entries = re.findall(r"<li value='\d+'>", html)
        assert len(entries) == 6, (
            f"Expected 6 de-duplicated entries from 8 raw sources, got "
            f"{len(entries)}. The same URL supporting several findings must be "
            "printed once per chapter, or the apparatus looks padded."
        )

    def test_the_shared_url_still_appears_once_per_chapter(self) -> None:
        """De-duplication is per chapter, not global: each chapter cites it."""
        html = _designer()._build_endnotes_html(_report())
        assert html.count("https://example.org/shared") == 2

    def test_titles_and_urls_are_html_escaped(self) -> None:
        html = _designer()._build_endnotes_html(_report())
        assert "&amp;" in html and "&lt;Queue&gt;" in html, (
            "A source title containing & or < is ordinary in real headlines and "
            "must not be able to corrupt the markup."
        )
        assert "<Queue>" not in html

    def test_a_source_with_no_title_falls_back_to_its_url_not_to_unknown(self) -> None:
        """`Unknown` is counted as a template leak by the audit probe."""
        untitled = Source(
            id="u1",
            title="",
            url="https://example.org/untitled",
            credibility=SourceCredibility.NEWS,
        )
        html = _designer()._build_endnotes_html(
            _report(sections=[_section(1, sources=[untitled])])
        )
        assert "Unknown" not in html
        assert "https://example.org/untitled" in html

    def test_a_report_with_no_sources_says_so_rather_than_implying_evidence(self) -> None:
        html = _designer()._build_endnotes_html(
            _report(sections=[_section(1, sources=[])])
        )
        assert "endnote-empty" in html
        text = _strip_tags(html).lower()
        assert "no per-chapter sources" in text, (
            "An endnotes page implying evidence that does not exist is worse "
            "than one that admits the shortfall."
        )
        assert "<li" not in html


class TestTheAppendixCannotBeSilentlyEmpty:
    """The centre of this file: each test fails if its block regresses.

    Every assertion here targets a value that could ONLY appear if the correct
    schema field was read. Asserting on the heading would pass with the field
    reads broken — which is how the first draft looked healthy.
    """

    def test_quality_dimensions_render_as_rows(self) -> None:
        """Fails if the `isinstance(dimensions, dict)` guard comes back."""
        html = _designer()._build_technical_appendix_html(_report())
        assert "Evidence sufficiency" in html and "Analytical depth" in html, (
            "QualityScore.dimensions is a list; a dict guard renders nothing."
        )
        assert "4/5" in html and "3/5" in html

    def test_the_critical_dimension_is_marked_as_critical(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "(critical)" in html

    def test_total_score_is_shown_against_its_threshold(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "4.1" in html and "4.0" in html, (
            "A score without its threshold is not interpretable."
        )

    def test_residual_gaps_are_published(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "Hourly dispatch data unavailable." in html

    def test_confidence_breakdown_renders_each_dimension(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        text = _strip_tags(html)
        assert "Market sizing" in text and "Unit economics" in text
        assert "High" in text and "Medium" in text

    def test_contradictions_render_both_opposed_findings(self) -> None:
        """A contradiction printed as one sentence is not a contradiction."""
        html = _designer()._build_technical_appendix_html(_report())
        assert "Four zones clear the spread." in html
        assert "Only two clear once curtailment is priced." in html

    def test_contradictions_name_the_disagreeing_agents(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "market_analyst" in html and "financial_analyst" in html

    def test_an_unresolved_contradiction_is_labelled_unresolved(self) -> None:
        """Published rather than resolved-away: a visible conflict is rigour."""
        html = _designer()._build_technical_appendix_html(_report())
        assert "Unresolved" in html

    def test_a_resolved_contradiction_shows_its_resolution(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "Adopted the 42-month figure." in html

    def test_no_raw_pydantic_repr_leaks_into_the_appendix(self) -> None:
        """The `or str(item)` fallback in the first draft would do exactly this.

        THIS TEST WAS WRONG FIRST, and the negative control is what caught it.
        The original version asserted ``"Contradiction(" not in html`` and
        ``"id='c1'" not in html``. Both passed while the raw-repr bug was
        deliberately reinstated, because:

        * pydantic v2's ``str()`` is **not** ``repr()`` — it emits
          ``id='c1' agent_a='...'`` with no ``ClassName(`` prefix; and
        * ``html_escape`` rewrites ``'`` to ``&#x27;``, so the literal
          ``id='c1'`` could never appear even when the leak was present.

        So the assertion tested nothing. It now looks for the actual signature
        of a leaked model dump — ``field=`` pairs and escaped quotes around a
        field's value — which does fail when the fallback returns.
        """
        html = _designer()._build_technical_appendix_html(_report())
        # A model dump names its own fields; correctly rendered HTML never does.
        for marker in ("agent_a=", "finding_a=", "contradiction_type=", "resolved="):
            assert marker not in html, (
                f"{marker!r} is pydantic field-dump syntax — a raw model leaked "
                "into the appendix instead of being formatted into cells."
            )
        # And the escaped-quote form the first version of this test missed.
        assert "id=&#x27;c1&#x27;" not in html
        assert "Contradiction(" not in html

    def test_each_contradiction_occupies_its_own_structured_cells(self) -> None:
        """Structural companion to the leak test above.

        ``test_contradictions_render_both_opposed_findings`` also passed under
        the reinstated bug, because a leaked ``str(item)`` happens to *contain*
        the finding text — so a substring assertion cannot tell a formatted
        table from a dumped object. This asserts the shape instead: four cells
        per contradiction, which a single ``colspan`` dump cannot satisfy.
        """
        html = _designer()._build_technical_appendix_html(_report())
        block = html[html.index("<h3>Contradictions</h3>") :]
        block = block[: block.index("</table>")]
        body_rows = [r for r in block.split("<tr>") if "<td>" in r]
        assert len(body_rows) == 2, f"expected 2 contradiction rows, got {len(body_rows)}"
        for row in body_rows:
            assert row.count("<td>") == 4, (
                "Each contradiction needs four cells (type, position A, "
                "position B, resolution). A single wide cell means the model was "
                f"dumped rather than formatted. Got: {row.count('<td>')}"
            )
        assert "colspan" not in block

    def test_fact_check_counts_render(self) -> None:
        """Fails if the field names regress to claims_checked/claims_verified."""
        html = _designer()._build_technical_appendix_html(_report())
        text = _strip_tags(html)
        assert "48" in text, "total_claims_checked did not render"
        assert "41" in text, "verified_count did not render"

    def test_verification_rate_renders_as_a_percentage(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "85%" in html

    def test_zero_hallucinated_citations_is_stated_not_omitted(self) -> None:
        """"0" must be an affirmative claim, not an absent section."""
        html = _designer()._build_technical_appendix_html(_report())
        text = _strip_tags(html)
        assert "Hallucinated citations" in text, (
            "A zero count must still be printed: an absent row could mean "
            "'none found' or 'never checked', and the reader cannot tell."
        )

    def test_evidence_chain_breaks_are_reported(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "Evidence-chain breaks" in _strip_tags(html)

    def test_limitations_are_published(self) -> None:
        html = _designer()._build_technical_appendix_html(_report())
        assert "Pricing data is quarterly." in html

    def test_a_report_with_no_assessment_data_says_so(self) -> None:
        html = _designer()._build_technical_appendix_html(
            _report(
                quality_score=None,
                confidence_breakdown={},
                contradictions=[],
                fact_check_report=None,
                limitations=[],
            )
        )
        assert "appendix-empty" in html
        assert "No quality assessment data" in _strip_tags(html)


class TestAtAGlanceIsWiredIntoTheTemplate:
    """At-a-glance is authored in Jinja, so it is asserted structurally.

    Its *content* is verified end-to-end by `tools/audit_render_probe.py`, which
    measures the rendered PDF (4/4 labels, precedes the TOC). Here we defend the
    two properties that can be checked without a 300MB WeasyPrint render.
    """

    def test_the_glance_block_precedes_the_table_of_contents(self) -> None:
        """MGI puts it before the TOC; after the TOC it is just a summary."""
        glance = HTML_TEMPLATE.index("at-a-glance")
        toc = HTML_TEMPLATE.index("Table of Contents")
        assert glance < toc, (
            "At a Glance must come before the TOC — that ordering is what makes "
            "it the page a partner reads first."
        )

    @pytest.mark.parametrize(
        "field",
        [
            "report.question",
            "report.recommendation.value",
            "report.confidence.value",
            "report.total_sources",
            "report.total_data_points",
            "report.recommendation_rationale",
            "report.key_findings",
            "report.critical_assumptions",
        ],
    )
    def test_every_glance_value_comes_from_a_real_report_field(self, field: str) -> None:
        assert field in HTML_TEMPLATE, (
            f"{field} is not read by the template; the glance page would show a "
            "hardcoded or missing value."
        )

    def test_the_findings_list_is_capped(self) -> None:
        """An at-a-glance page that runs to two pages is not one."""
        assert "key_findings[:5]" in HTML_TEMPLATE

    def test_the_assumptions_list_is_capped(self) -> None:
        assert "critical_assumptions[:4]" in HTML_TEMPLATE


class TestBothRenderPathsAreFed:
    """Fix 3.5 was a filter registered in one Jinja env and not the other.

    The same shape of bug is available here: two call sites build the render
    context, and feeding only one leaves the fallback path rendering empty
    back matter. So both are asserted, by count.
    """

    @pytest.mark.parametrize("name", ["endnotes_html", "technical_appendix_html"])
    def test_the_variable_reaches_both_render_call_sites(self, name: str) -> None:
        # One `"name": ...` dict entry + one `name=...` kwarg = 2 producers,
        # plus the template's own `{{ name | safe }}` consumer.
        producers = len(re.findall(rf'["\s]{name}["\s]*[:=]', SRC))
        assert producers >= 2, (
            f"{name} is produced at {producers} call site(s); both the primary "
            "context dict and the fallback env must be fed, or one render path "
            "silently emits an empty section (this is the fix-3.5 bug class)."
        )

    @pytest.mark.parametrize("name", ["endnotes_html", "technical_appendix_html"])
    def test_the_template_actually_consumes_it(self, name: str) -> None:
        assert f"{{{{ {name} | safe }}}}" in HTML_TEMPLATE


class TestTheSourcesAppendixLeakIsFixed:
    """Two defects fixed in `_build_appendix_sources_html` while adding 4.5."""

    def test_the_literal_unknown_placeholder_is_gone(self) -> None:
        """The audit probe counts "Unknown" as a template leak (must be 0)."""
        untitled = Source(
            id="u1",
            title="",
            url="https://example.org/x",
            credibility=SourceCredibility.NEWS,
        )
        html = _designer()._build_appendix_sources_html(
            _report(sections=[_section(1, sources=[untitled])])
        )
        assert "Unknown" not in html

    def test_titles_are_escaped(self) -> None:
        html = _designer()._build_appendix_sources_html(_report())
        assert "&amp;" in html
        assert "<Queue>" not in html
