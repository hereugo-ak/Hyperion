"""Tests for the explicit page budget (fix 4.1).

WHAT THESE TESTS ARE DEFENDING
------------------------------
The audit's finding B-10 was not "the report is too long" — a too-long report is
a symptom. The finding was that **no mechanism related page count to anything**:
the section count came from however many agents happened to report, the word
count came from a literal ``2000`` typed into four separate prompt strings, and
the only check (``15 <= page_count <= 40``) was wide enough to pass both a
16-page and a 39-page document.

So the property under test is not "the number 767 is correct". It is the
*inversion*: page count is now an input and words-per-section is the output. Most
tests below therefore assert on relationships that must hold for any sane
constants — allocation falls as sections rise, the projection respects the
ceiling, the floor and ceiling clamps bind, degenerate inputs do not raise —
rather than on the constants themselves, which are measurements and will
legitimately change if the layout changes.

`TestModelIsValidatedAgainstTheRealPdf` is the deliberate exception: it pins the
constants to the artefact they were measured from, and SHOULD fail if someone
changes the template's type size or column count without re-measuring.

TWO BUGS THESE TESTS ALREADY CAUGHT
-----------------------------------
Written before the module was finished, and both fired:

1. **Continuous division.** The first model divided available pages by section
   count as though page space were a fluid. But the production CSS sets
   ``page-break-before: always`` on sections, so a section needing 2.1 pages
   burns 3 sheets. The continuous model under-projected — the same class of
   error as the bug being fixed. Hence `TestPagesQuantize`.
2. **Aiming at the midpoint.** The first model targeted the middle of the 15-20
   contract, and so gave 4 sections 575 words each (16 pages) when 1,342 words
   each (20 pages) fit the same contract. Undershooting by 4 pages is not the
   safe choice, it is a thinner deliverable bought for nothing. Hence
   `TestBudgetFillsItsAllowance`.
"""

from __future__ import annotations

import pytest

from hyperion.output.page_budget import (
    BODY_WORDS_PER_FULL_PAGE,
    FIXED_OVERHEAD_PAGES,
    MAX_SECTION_WORDS,
    MIN_SECTION_WORDS,
    SECTION_CHROME_PAGES,
    TARGET_PAGES_MAX,
    TARGET_PAGES_MIN,
    PageBudget,
    _max_words_in_pages,
    _projected_pages,
    _section_pages,
    plan_budget,
)

#: Section counts that represent real engagements. Fewer than 2 specialist
#: sections is not a consulting report; beyond 6 the audit's own §3.1 page target
#: is physically unreachable at readable section lengths, which the budget
#: reports as over-capacity rather than pretending otherwise.
REALISTIC = (2, 3, 4, 5, 6)


class TestTheInversionHolds:
    """Page count is the input; words-per-section is what falls out of it."""

    def test_more_sections_means_fewer_words_each(self):
        """The core behaviour the old code lacked entirely.

        Under the hardcoded prompt, 3 agents and 10 agents both got "2000-4000
        words", so 10 agents produced a report roughly three times longer. The
        allocation must move in the opposite direction to the section count.
        """
        allocations = [plan_budget(n).words_per_section for n in range(1, 13)]
        for smaller, larger in zip(allocations, allocations[1:], strict=False):
            assert larger <= smaller, (
                f"allocation rose when section count rose: {allocations}"
            )

    def test_allocation_actually_varies_not_just_clamps(self):
        """Guard against a degenerate implementation that returns a constant.

        A `plan_budget` that always returned MIN_SECTION_WORDS would satisfy
        "non-increasing" trivially while reintroducing the original bug in a new
        costume. There must be real variation across the realistic range.
        """
        allocations = {plan_budget(n).words_per_section for n in REALISTIC}
        assert len(allocations) > 1, (
            "allocation is constant across realistic section counts — the "
            "budget is not actually responding to section count"
        )

    def test_raising_the_target_raises_the_allocation(self):
        """More pages allowed => more words per section, same section count."""
        tight = plan_budget(4, target_pages=TARGET_PAGES_MIN)
        loose = plan_budget(4, target_pages=TARGET_PAGES_MAX)
        assert loose.words_per_section > tight.words_per_section


class TestPagesQuantize:
    """Sections never share a sheet — `page-break-before: always`.

    This is the bug the first version of the module had: it divided page space
    continuously, so it projected fewer pages than the renderer produces.
    """

    def test_section_pages_is_a_whole_number_of_sheets(self):
        for words in (450, 800, 1510, 2600):
            assert isinstance(_section_pages(words), int)

    def test_partial_page_still_costs_a_whole_sheet(self):
        """2.1 pages of prose occupies 3 sheets, not 2.1.

        Rounding down here would let the budget claim 15 pages for something
        that physically occupies 18 — under-reporting by design.
        """
        exact = SECTION_CHROME_PAGES + 1510 / BODY_WORDS_PER_FULL_PAGE
        assert _section_pages(1510) >= exact
        assert _section_pages(1510) - exact < 1.0

    def test_projection_is_overhead_plus_whole_sections(self):
        """No fractional pages may survive into the projection."""
        for n in REALISTIC:
            budget = plan_budget(n)
            assert budget.projected_pages == (
                FIXED_OVERHEAD_PAGES + n * budget.pages_per_section
            )

    def test_inverse_is_exact(self):
        """`_max_words_in_pages` must be the true inverse of `_section_pages`.

        If these two drift apart, the allocation stops describing the projection
        it was derived from and every guarantee here becomes decorative. The
        boundary is what matters: the returned word count must still fit, and
        one more word must not.
        """
        for pages in range(2, 8):
            words = _max_words_in_pages(pages)
            assert _section_pages(words) == pages, (
                f"{words} words should fit exactly {pages} pages"
            )
            assert _section_pages(words + 1) == pages + 1, (
                f"{words + 1} words should spill to {pages + 1} pages"
            )


class TestBudgetFillsItsAllowance:
    """`target_pages` is a ceiling to fill, not a bullseye to hit.

    The first version aimed at the contract midpoint and therefore chose 16
    pages when 20 were permitted, asking for 575-word sections when 1,342-word
    sections fit the same contract.
    """

    def test_default_target_is_the_top_of_the_contract(self):
        assert plan_budget(4).target_pages == TARGET_PAGES_MAX

    def test_never_exceeds_the_ceiling_when_a_fit_exists(self):
        for n in REALISTIC:
            budget = plan_budget(n)
            if not budget.sections_over_capacity:
                assert budget.projected_pages <= TARGET_PAGES_MAX

    def test_no_larger_allotment_would_have_fitted(self):
        """The chosen allocation must be maximal, not merely legal.

        This is the property that the midpoint-aiming version violated: its
        answer was inside the contract, so a naive "within_contract" assertion
        passed while pages were being thrown away.
        """
        for n in REALISTIC:
            budget = plan_budget(n)
            if budget.sections_over_capacity or budget.words_per_section >= MAX_SECTION_WORDS:
                continue  # floor or ceiling clamp binds, not the page fit
            bigger = budget.pages_per_section + 1
            assert FIXED_OVERHEAD_PAGES + n * bigger > TARGET_PAGES_MAX, (
                f"{n} sections could have had {bigger} pages each "
                f"({FIXED_OVERHEAD_PAGES + n * bigger} total) but got "
                f"{budget.pages_per_section}"
            )

    @pytest.mark.parametrize("section_count", REALISTIC)
    def test_realistic_engagements_satisfy_the_contract(self, section_count):
        budget = plan_budget(section_count)
        assert budget.within_contract, (
            f"{section_count} sections projected {budget.projected_pages} pages, "
            f"outside {TARGET_PAGES_MIN}-{TARGET_PAGES_MAX}"
        )


class TestFloorAndCeiling:
    """Clamps exist so the budget degrades into something writable, not absurd."""

    def test_never_below_the_floor(self):
        """Even at 12 agents, no section is allocated stub length.

        A 200-word "section" is not a shorter section; it is a missing section
        with a heading on top.
        """
        for n in range(1, 15):
            assert plan_budget(n).words_per_section >= MIN_SECTION_WORDS

    def test_never_above_the_ceiling(self):
        """A single-section report must not be asked for 5,900 words.

        Past a few thousand words models pad rather than deepen, so an
        unbounded allocation buys repetition, not analysis.
        """
        for n in range(1, 15):
            assert plan_budget(n).words_per_section <= MAX_SECTION_WORDS

    def test_ceiling_binds_for_very_few_sections(self):
        assert plan_budget(1).words_per_section == MAX_SECTION_WORDS

    def test_over_capacity_is_reported_not_hidden(self):
        """The honest signal when the contract is unreachable.

        With enough sections the per-section chrome alone overruns the ceiling.
        The old code would have silently shipped a 32-page "20-page" report; the
        budget must instead say it cannot be done.
        """
        crowded = plan_budget(12)
        assert crowded.sections_over_capacity is True
        assert crowded.words_per_section == MIN_SECTION_WORDS
        assert crowded.within_contract is False

    def test_ceiling_clamp_is_not_reported_as_over_capacity(self):
        """Applying the word ceiling means the report is SHORT, not over capacity.

        Conflating the two would have a 1-section engagement warn the operator
        that the page target is unreachable, when in fact it will simply come in
        under it — a false alarm that trains operators to ignore the real one.
        """
        assert plan_budget(1).sections_over_capacity is False

    def test_over_capacity_reports_a_longer_report_not_a_shorter_one(self):
        """Over capacity must mean overrun, never undershoot.

        A budget that flagged over-capacity while projecting 12 pages would be
        describing the opposite problem, and the Synthesis Lead's warning would
        send the operator looking in the wrong direction.
        """
        crowded = plan_budget(12)
        assert crowded.projected_pages > TARGET_PAGES_MAX


class TestPromptClause:
    """What actually reaches the LLM."""

    def test_states_a_number_and_a_range(self):
        budget = plan_budget(5)
        clause = budget.prompt_clause()
        assert str(budget.words_per_section) in clause
        assert "-" in clause, "clause states no acceptable range"

    def test_range_brackets_the_target(self):
        budget = plan_budget(5)
        low = int(budget.words_per_section * 0.9)
        high = int(budget.words_per_section * 1.1)
        assert f"{low}-{high}" in budget.prompt_clause()
        assert low < budget.words_per_section < high

    def test_is_a_complete_sentence(self):
        """Returned as prose, not a bare integer.

        The four drifted copies of "2000" existed because the number was raw and
        each call site had to re-supply its own wording. Handing back a finished
        sentence removes that opportunity.
        """
        clause = plan_budget(5).prompt_clause()
        assert clause.endswith(".")
        assert clause[0].isupper()

    def test_no_stale_hardcoded_figure_survives(self):
        """The literal the audit flagged must be gone from the clause."""
        for n in range(1, 13):
            assert "2000-4000" not in plan_budget(n).prompt_clause()


class TestSynthesisLeadUsesTheBudget:
    """The module is worthless if the prompt still carries the old literal.

    Fix 4.1 is only real once the four hardcoded copies are gone from the agent.
    Asserting on the module alone would let someone delete the wiring and still
    see green.
    """

    def test_no_hardcoded_word_counts_remain_in_the_prompts(self):
        from pathlib import Path

        source = Path("hyperion/agents/synthesis_lead.py").read_text(encoding="utf-8")
        # Strip comments so the explanatory notes about the old values (which
        # legitimately name them) do not trip the check.
        code = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )
        for literal in ("2000-4000 words", "at least 2000 words", "fewer than 2000 words"):
            assert literal not in code, f"stale hardcoded budget still present: {literal!r}"

    def test_agent_imports_the_budget(self):
        from pathlib import Path

        source = Path("hyperion/agents/synthesis_lead.py").read_text(encoding="utf-8")
        assert "plan_budget" in source


class TestDegenerateInputsDoNotRaise:
    """A budget that raises turns a cosmetic issue into a failed engagement."""

    @pytest.mark.parametrize("section_count", [0, -1, -100])
    def test_non_positive_section_counts(self, section_count):
        budget = plan_budget(section_count)
        assert budget.section_count == 0
        assert budget.words_per_section == 0
        assert budget.total_body_words == 0
        assert budget.projected_pages == FIXED_OVERHEAD_PAGES

    @pytest.mark.parametrize("target", [0, -5, 1, 3, 500, 100_000])
    def test_absurd_targets_are_clamped_into_the_contract(self, target):
        """A caller asking for a 3-page or 300-page report gets the contract.

        This template cannot produce either, so honouring the request would
        produce a budget that provably cannot be met.
        """
        budget = plan_budget(4, target_pages=target)
        assert TARGET_PAGES_MIN <= budget.target_pages <= TARGET_PAGES_MAX

    def test_prompt_clause_is_safe_on_a_zero_section_budget(self):
        """Must not raise — early pipeline stages can legitimately see zero."""
        assert isinstance(plan_budget(0).prompt_clause(), str)


class TestBudgetIsImmutable:
    """Computed once, read by several agents."""

    def test_cannot_be_mutated(self):
        """A mutable budget one agent could adjust mid-run reintroduces the bug.

        The original failure mode was that no single place owned the length
        decision. If the Synthesis Lead could bump `words_per_section` after the
        fact, ownership would be diffuse again.
        """
        budget = plan_budget(5)
        with pytest.raises((AttributeError, TypeError)):
            budget.words_per_section = 9999  # type: ignore[misc]

    def test_total_is_consistent_with_the_parts(self):
        budget = plan_budget(6)
        assert budget.total_body_words == budget.words_per_section * budget.section_count

    def test_is_a_page_budget(self):
        assert isinstance(plan_budget(3), PageBudget)


class TestModelIsValidatedAgainstTheRealPdf:
    """Pins the constants to the artefact they were measured from.

    These are the only tests here that assert on magic numbers, deliberately.
    If the template's type size, column count or front/back matter changes,
    these fail — which is correct, because the constants are then stale and
    every projection above becomes fiction. The fix when they fail is to
    re-measure `tools/audit_render_probe.py`'s output, not to edit the numbers
    until the test passes.
    """

    def test_reproduces_the_audits_measured_36_pages(self):
        """The audit measured 36 pages. The model must reproduce it exactly.

        The probe fixture builds 5 paragraphs x 3 repeats of LOREM_PARA = 1,510
        body words per section, across 7 sections. PyMuPDF measured 4 sheets per
        section plus 8 pages of front/back matter = 36. If this arithmetic does
        not reproduce that number, the model is not describing this renderer.
        """
        assert _section_pages(1510) == 4
        assert _projected_pages(7, 1510) == 36

    def test_fixture_word_count_is_still_1510(self):
        """Guards the calibration input itself.

        The test above is only meaningful while the probe fixture really does
        emit 1,510 words. If someone edits LOREM_PARA, the calibration silently
        stops referring to the measured PDF — so re-derive it from the probe
        rather than trusting the comment.
        """
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "probe", "tools/audit_render_probe.py"
        )
        assert spec is not None and spec.loader is not None
        probe = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(probe)
        body = "\n\n".join(
            [f"**Sub-heading {i}**\n\n" + (probe.LOREM_PARA * 3) for i in range(1, 6)]
        )
        assert len(body.split()) == 1510, (
            "probe fixture no longer emits 1,510 body words — the 36-page "
            "calibration above must be re-measured"
        )

    def test_full_page_word_count_is_plausible_for_two_column_10pt(self):
        """Sanity band, not a re-measurement.

        Two columns of 10 pt at ~54 chars/line cannot hold 200 words and cannot
        hold 2,000. A constant outside this band means someone guessed.
        """
        assert 500 <= BODY_WORDS_PER_FULL_PAGE <= 1100

    def test_fixed_overhead_matches_the_templates_front_and_back_matter(self):
        """Cover, TOC, exec summary x2, risk, methodology, appendix, back cover."""
        assert FIXED_OVERHEAD_PAGES == 8

    def test_chrome_is_less_than_the_measured_section(self):
        """Chrome must leave room for prose.

        If chrome ever exceeded the per-section page count, every section would
        be pure furniture and the word allocation would collapse to the floor
        for all inputs — silently.
        """
        assert 0 < SECTION_CHROME_PAGES < 4
