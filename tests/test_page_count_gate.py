"""Tests for the rendered-page-count quality gate (fix 4.2).

WHAT THIS FIX WAS FOR
---------------------
Fix 4.1 made the page contract govern the *request*: `plan_budget` turns a target
page count into a per-section word allocation, and the Synthesis Lead prompts with
it. But nothing verified the *result*. Three separate checks existed, and all
three were inert:

1. ``render.py:757`` recorded ``page_count_reasonable: 15 <= page_count <= 40``
   and then computed ``passed`` from blank pages and fonts **only**. The page
   count could not fail a verification no matter what it was.
2. ``render_engine._verify_pdf`` — the method whose own docstring calls the agent
   "the last line of defense for quality" — did not check page count at all. It
   was measured two steps later, in ``run()``, to fill in a status line.
3. ``harness.py`` used ``5 <= page_count <= 60``, a 55-page window on a 15-20
   page contract.

So the audit's measured "36 pages against a 15-20 target" was not a check that
failed. It was a number that three separate mechanisms wrote down and ignored.

WHAT THESE TESTS DEFEND
-----------------------
The property is not "the band is 15-22". It is that a page count outside the
contract **causes a failure** — that the verdict is load-bearing in `passed` and
reaches `verification_issues`. Tests that only asserted on band arithmetic would
pass against the original inert code, since it computed the band correctly too.

`TestTheGateIsLoadBearing` is therefore the centre of this file: each of its
tests fails against the pre-4.2 implementation.

A DEFECT IN 4.1 THAT THIS WORK EXPOSED
--------------------------------------
`TestAdvertisedRangeFitsTheProjection` pins a bug found while writing the gate,
not while writing the budget. 4.1 allocated the *most* words that fit a section's
sheet allotment, then advertised "acceptable range 0.9N-1.1N" on top of it. The
upper end of that range did not fit: at 4 sections the allocation was 1,342 words
(exactly 3 sheets) and the clause invited 1,476, which needs 4. A model that
obeyed its instructions pushed the report from 20 pages to 24 — at 4 of the 6
realistic section counts.

While nothing verified the output, this was invisible. The moment page count
became a gate it would have failed compliant reports, and the obvious-looking
fix would have been to widen the gate — entrenching the actual defect. So the
allocation now reserves the tolerance it advertises.
"""

from __future__ import annotations

import pytest

from hyperion.output.page_budget import (
    MAX_SECTION_WORDS,
    PAGE_COUNT_MAX,
    PAGE_COUNT_MIN,
    RENDER_SLACK_PAGES,
    SECTION_WORD_TOLERANCE,
    TARGET_PAGES_MAX,
    TARGET_PAGES_MIN,
    PageCountVerdict,
    _section_pages,
    page_count_verdict,
    plan_budget,
)

#: Section counts representing real engagements — see test_page_budget.REALISTIC.
REALISTIC = (2, 3, 4, 5, 6)


class TestTheBandIsDerivedNotRetyped:
    """The contract must have exactly one definition.

    The pre-4.2 codebase stated the page contract in five places — `render.py`,
    `harness.py`, the `LayoutPlan` schema, the Presentation Designer's prompt and
    ARCHITECTURE.md — with three different values (15-40, 5-60, 15-40). Anything
    restated is something that can drift, and these had already drifted.
    """

    def test_floor_is_the_contract_floor(self):
        assert PAGE_COUNT_MIN == TARGET_PAGES_MIN

    def test_ceiling_is_the_target_plus_named_slack(self):
        """The ceiling must be derived, so moving the contract moves the gate."""
        assert PAGE_COUNT_MAX == TARGET_PAGES_MAX + RENDER_SLACK_PAGES

    def test_matches_the_audits_stated_contract(self):
        """The audit specified `15..22`. Derivation must reproduce it."""
        assert (PAGE_COUNT_MIN, PAGE_COUNT_MAX) == (15, 22)

    def test_slack_is_global_not_per_section(self):
        """Slack absorbs renderer line-breaking, not systematic over-length.

        A per-section allowance would scale with section count — at 6 sections it
        would admit +6 pages, which is a third of the contract and would let a
        genuinely over-long report through.
        """
        assert RENDER_SLACK_PAGES <= 3

    def test_band_is_narrower_than_the_window_it_replaced(self):
        """The old 15-40 window could not distinguish 16 pages from 39.

        A gate whose verdict no achievable change can alter is not a gate.
        """
        assert (PAGE_COUNT_MAX - PAGE_COUNT_MIN) < (40 - 15)


class TestVerdictArithmetic:
    """The flat band, with no budget supplied."""

    @pytest.mark.parametrize("pages", [PAGE_COUNT_MIN, 18, PAGE_COUNT_MAX])
    def test_inside_the_band_passes(self, pages):
        assert page_count_verdict(pages).passed is True

    @pytest.mark.parametrize("pages", [PAGE_COUNT_MIN - 1, 5, 1])
    def test_below_the_floor_fails(self, pages):
        assert page_count_verdict(pages).passed is False

    @pytest.mark.parametrize("pages", [PAGE_COUNT_MAX + 1, 36, 120])
    def test_above_the_ceiling_fails(self, pages):
        assert page_count_verdict(pages).passed is False

    def test_the_audits_measured_36_pages_now_fails(self):
        """The regression this fix exists to catch.

        36 pages was the audit's measurement, and under `15 <= n <= 40` it
        passed. If this test ever passes-as-True again, the gate has been
        widened back to uselessness.
        """
        verdict = page_count_verdict(36)
        assert verdict.passed is False
        assert "36" in verdict.reason

    def test_boundaries_are_inclusive(self):
        """Off-by-one at the boundary would reject a compliant report."""
        assert page_count_verdict(PAGE_COUNT_MIN).passed
        assert page_count_verdict(PAGE_COUNT_MAX).passed
        assert not page_count_verdict(PAGE_COUNT_MIN - 1).passed
        assert not page_count_verdict(PAGE_COUNT_MAX + 1).passed


class TestUnreadablePageCountIsAFailure:
    """A check that cannot run must not report success.

    `_get_page_count` returns 0 when no PDF library is available or the file is
    corrupt. Treating 0 as "nothing to complain about" is the exact shape of the
    silent-failure class this audit was opened over: a broken artefact would ship
    with `verification_passed: True` because the verifier could not read it.
    """

    @pytest.mark.parametrize("pages", [0, -1, -100])
    def test_non_positive_counts_fail(self, pages):
        verdict = page_count_verdict(pages)
        assert verdict.passed is False
        assert "could not be read" in verdict.reason

    def test_unreadable_is_distinguishable_from_too_short(self):
        """The operator must be able to tell "no PDF" from "thin PDF".

        Both fail, but they need different responses: one is a renderer problem,
        the other a content problem.
        """
        unreadable = page_count_verdict(0).reason
        too_short = page_count_verdict(3).reason
        assert unreadable != too_short
        assert "could not be read" not in too_short


class TestVerdictIsActionable:
    """A bare False is not a usable failure.

    The old check emitted `page_count_reasonable: False` with no band attached —
    unactionable, because 36 pages fails a 20-page contract and passes a 40-page
    one and the record did not say which was in force.
    """

    def test_carries_the_expected_band(self):
        verdict = page_count_verdict(30)
        assert verdict.expected_min == PAGE_COUNT_MIN
        assert verdict.expected_max == PAGE_COUNT_MAX

    def test_reason_names_both_the_actual_and_the_limit(self):
        verdict = page_count_verdict(30)
        assert "30" in verdict.reason
        assert str(PAGE_COUNT_MAX) in verdict.reason

    def test_issue_is_empty_when_passing(self):
        """So callers can append unconditionally without inventing wording."""
        assert page_count_verdict(18).issue == ""

    def test_issue_is_populated_when_failing(self):
        issue = page_count_verdict(36).issue
        assert issue
        assert "36" in issue

    def test_verdict_is_immutable(self):
        """A verdict a caller could edit is not a verdict."""
        verdict = page_count_verdict(18)
        with pytest.raises((AttributeError, TypeError)):
            verdict.passed = False  # type: ignore[misc]

    def test_is_a_page_count_verdict(self):
        assert isinstance(page_count_verdict(18), PageCountVerdict)


class TestBudgetAwareWidening:
    """Two self-declared cases where the flat band would punish honesty."""

    def test_ceiling_clamped_short_report_is_not_failed_for_being_short(self):
        """A 1-section engagement projects 13 pages and cannot reach 15.

        `MAX_SECTION_WORDS` caps it deliberately. Failing it would push the fix
        toward padding prose to hit a page number, which inverts the intent of
        the whole budget.
        """
        budget = plan_budget(1)
        assert budget.projected_pages < PAGE_COUNT_MIN  # premise of the test
        assert page_count_verdict(budget.projected_pages, budget).passed is True

    def test_over_capacity_report_is_judged_against_its_declared_projection(self):
        """9 sections already declared over-capacity and logged a warning.

        Failing it again here reports nothing the operator was not already told,
        and a gate that fires on known-and-accepted conditions is a gate people
        learn to ignore.
        """
        budget = plan_budget(9)
        assert budget.sections_over_capacity is True  # premise
        assert page_count_verdict(budget.projected_pages, budget).passed is True

    def test_over_capacity_still_fails_if_it_overruns_its_own_admission(self):
        """The widening is bounded, not a blank cheque.

        This is what separates "fair" from "toothless": an over-capacity report
        that blows past even its own declared projection is still caught.
        """
        budget = plan_budget(9)
        way_over = budget.projected_pages + RENDER_SLACK_PAGES + 5
        assert page_count_verdict(way_over, budget).passed is False

    def test_widening_never_lowers_the_ceiling(self):
        """Supplying a budget must never make the gate *stricter* than the band.

        If it could, the same PDF would pass or fail depending on whether the
        caller happened to know the budget — a non-deterministic gate.
        """
        for n in range(1, 13):
            budget = plan_budget(n)
            verdict = page_count_verdict(18, budget)
            assert verdict.expected_max >= PAGE_COUNT_MAX

    def test_a_healthy_budget_does_not_widen_the_band_at_all(self):
        """No widening for the normal case — the flat contract applies."""
        for n in REALISTIC:
            budget = plan_budget(n)
            if budget.sections_over_capacity or budget.projected_pages < PAGE_COUNT_MIN:
                continue
            verdict = page_count_verdict(18, budget)
            assert verdict.expected_min == PAGE_COUNT_MIN
            assert verdict.expected_max == PAGE_COUNT_MAX

    def test_widening_is_explained_in_the_reason(self):
        """An operator seeing a non-standard band must be told why."""
        crowded = plan_budget(9)
        assert "over capacity" in page_count_verdict(30, crowded).reason
        thin = plan_budget(1)
        assert "ceiling" in page_count_verdict(13, thin).reason

    def test_zero_section_budget_does_not_widen(self):
        """A degenerate budget must not disable the gate.

        `plan_budget(0)` projects 8 pages with `sections_over_capacity=False`. If
        that were allowed to set the floor, any report would pass.
        """
        budget = plan_budget(0)
        assert page_count_verdict(9, budget).passed is False


class TestBudgetProjectionsSatisfyTheirOwnGate:
    """The two halves of the contract must agree.

    If `plan_budget` could produce a plan whose own projection the gate rejects,
    then the system would be specified to fail: the request and the verification
    would be enforcing different contracts. That was literally the pre-4.2 state
    (`plan_budget` targeted 20, `render.py` accepted up to 40).
    """

    @pytest.mark.parametrize("section_count", range(1, 13))
    def test_every_plan_passes_the_gate_it_will_be_judged_by(self, section_count):
        budget = plan_budget(section_count)
        verdict = page_count_verdict(budget.projected_pages, budget)
        assert verdict.passed is True, (
            f"{section_count} sections: budget plans {budget.projected_pages} "
            f"pages but the gate rejects it — {verdict.reason}"
        )

    @pytest.mark.parametrize("section_count", REALISTIC)
    def test_realistic_plans_pass_the_flat_band_without_help(self, section_count):
        """No budget-aware widening needed for a normal engagement.

        If a realistic section count needed the widening to pass, the widening
        would be load-bearing for the common case rather than an exception, and
        the flat band would be fiction.
        """
        budget = plan_budget(section_count)
        assert page_count_verdict(budget.projected_pages).passed is True


class TestAdvertisedRangeFitsTheProjection:
    """The 4.1 defect this work uncovered — see the module docstring.

    The budget tells the model "approximately N words (acceptable range LO-HI)".
    HI must fit inside the sheets the projection allotted, or a fully compliant
    model breaks the page contract and the gate blames it for obedience.
    """

    @pytest.mark.parametrize("section_count", range(1, 13))
    def test_top_of_the_advertised_range_still_fits(self, section_count):
        budget = plan_budget(section_count)
        assert _section_pages(budget.max_acceptable_words) <= budget.pages_per_section, (
            f"{section_count} sections: prompt invites up to "
            f"{budget.max_acceptable_words} words, which needs "
            f"{_section_pages(budget.max_acceptable_words)} sheets, but the "
            f"projection allotted only {budget.pages_per_section}"
        )

    @pytest.mark.parametrize("section_count", range(1, 13))
    def test_a_maximally_compliant_report_passes_the_gate(self, section_count):
        """End-to-end statement of the same property, in gate terms.

        This is the test that would have caught the defect: a model writing the
        largest number of words it was told was acceptable must not fail.
        """
        budget = plan_budget(section_count)
        from hyperion.output.page_budget import FIXED_OVERHEAD_PAGES

        worst_case = FIXED_OVERHEAD_PAGES + section_count * _section_pages(
            budget.max_acceptable_words
        )
        verdict = page_count_verdict(worst_case, budget)
        assert verdict.passed is True, (
            f"{section_count} sections: a model writing "
            f"{budget.max_acceptable_words} words/section (inside the range it "
            f"was given) renders {worst_case} pages — {verdict.reason}"
        )

    def test_prompt_clause_range_matches_the_reserved_tolerance(self):
        """The clause and the allocation must use one constant, not two.

        They previously used a hardcoded 1.1 in the clause and no allowance in
        the allocation, which is how they came to disagree.
        """
        budget = plan_budget(4)
        clause = budget.prompt_clause()
        low = int(budget.words_per_section * (1 - SECTION_WORD_TOLERANCE))
        assert f"{low}-{budget.max_acceptable_words}" in clause

    def test_reserving_tolerance_did_not_collapse_the_allocation(self):
        """The fix costs prose; it must not cost *all* the prose.

        Reserving headroom by shrinking sections to the floor would satisfy the
        property above while gutting the deliverable.
        """
        for n in REALISTIC:
            budget = plan_budget(n)
            assert budget.words_per_section >= 500

    def test_sections_still_fill_most_of_their_allotment(self):
        """Headroom should be ~the tolerance, not a wasted sheet.

        If the reservation over-shrank sections, each would leave most of a sheet
        blank and the report would be needlessly thin inside a passing gate.
        """
        for n in REALISTIC:
            budget = plan_budget(n)
            if budget.words_per_section >= MAX_SECTION_WORDS:
                continue  # ceiling clamp binds, not the page fit
            assert _section_pages(budget.words_per_section) == budget.pages_per_section


class TestTheGateIsLoadBearing:
    """The heart of 4.2: the verdict must actually cause failures.

    Every test in this class fails against the pre-4.2 code, which computed the
    page-count band correctly and then discarded the result. Band arithmetic
    alone cannot distinguish the fixed code from the broken code — only wiring
    can.
    """

    def _pdf_with_pages(self, tmp_path, pages: int) -> str:
        """Render a real multi-page PDF, so the wiring is tested end to end.

        A mocked page count would test that the code calls a function; this tests
        that a genuinely over-long PDF is genuinely rejected.
        """
        import fitz

        doc = fitz.open()
        for i in range(pages):
            page = doc.new_page()
            page.insert_text((72, 72), f"Page {i + 1} of {pages} — body text here.")
        path = str(tmp_path / f"probe_{pages}p.pdf")
        doc.save(path)
        doc.close()
        return path

    def test_render_verify_pdf_fails_an_over_long_pdf(self, tmp_path):
        """`render.py`'s `passed` must now depend on page count.

        Pre-4.2 this returned `passed: True` for any page count whatsoever,
        because `passed` was `no blank pages and fonts embedded`.
        """
        from hyperion.output.render import PDFRenderer

        result = PDFRenderer().verify_pdf(self._pdf_with_pages(tmp_path, 36))
        assert result["page_count"] == 36
        assert result["page_count_reasonable"] is False
        assert result["passed"] is False, (
            "verify_pdf still reports passed=True for a 36-page report against a "
            "15-22 page contract — the page count is not load-bearing"
        )

    def test_render_verify_pdf_passes_a_compliant_pdf(self, tmp_path):
        """The gate must not simply fail everything."""
        from hyperion.output.render import PDFRenderer

        result = PDFRenderer().verify_pdf(self._pdf_with_pages(tmp_path, 18))
        assert result["page_count_reasonable"] is True
        assert result["page_count_expected_min"] == PAGE_COUNT_MIN
        assert result["page_count_expected_max"] == PAGE_COUNT_MAX

    def test_render_verify_pdf_reports_the_band_it_used(self, tmp_path):
        from hyperion.output.render import PDFRenderer

        result = PDFRenderer().verify_pdf(self._pdf_with_pages(tmp_path, 36))
        assert "page_count_reason" in result
        assert str(PAGE_COUNT_MAX) in result["page_count_reason"]

    def test_render_verify_pdf_accepts_a_budget(self, tmp_path):
        """A ceiling-clamped 13-page report must pass when the budget explains it."""
        from hyperion.output.render import PDFRenderer

        pdf = self._pdf_with_pages(tmp_path, 13)
        renderer = PDFRenderer()
        assert renderer.verify_pdf(pdf)["page_count_reasonable"] is False
        assert renderer.verify_pdf(pdf, plan_budget(1))["page_count_reasonable"] is True

    def test_render_engine_verify_pdf_reports_page_count_as_an_issue(self, tmp_path):
        """The Render Engine must refuse to sign off an over-long PDF.

        Pre-4.2 `_verify_pdf` did not look at page count at all, so this is the
        single most important assertion in the file: the agent documented as
        "the last line of defense" now actually defends the page contract.
        """
        from hyperion.agents.delivery.render_engine import RenderEngine

        engine = RenderEngine()
        all_passed, issues, details = engine._verify_pdf(
            self._pdf_with_pages(tmp_path, 36)
        )
        assert details["page_count"] == 36
        assert details["page_count_within_contract"] is False
        assert all_passed is False
        assert any("Page count" in i for i in issues), (
            f"page count absent from verification issues: {issues}"
        )

    def test_render_engine_uses_the_budget_when_given_one(self, tmp_path):
        """A 13-page ceiling-clamped report must not be failed for its length."""
        from hyperion.agents.delivery.render_engine import RenderEngine

        pdf = self._pdf_with_pages(tmp_path, 13)

        strict = RenderEngine()
        assert strict._verify_pdf(pdf)[2]["page_count_within_contract"] is False

        informed = RenderEngine()
        informed._page_budget = plan_budget(1)
        assert informed._verify_pdf(pdf)[2]["page_count_within_contract"] is True

    def test_render_engine_does_not_flag_a_compliant_page_count(self, tmp_path):
        """No page-count issue for a report inside the contract."""
        from hyperion.agents.delivery.render_engine import RenderEngine

        _, issues, details = RenderEngine()._verify_pdf(
            self._pdf_with_pages(tmp_path, 18)
        )
        assert details["page_count_within_contract"] is True
        assert not any("Page count" in i for i in issues)

    def test_eval_harness_fails_an_over_long_pdf(self, tmp_path):
        """The offline gate must agree with the runtime gate.

        Its window was `5 <= n <= 60`, which no plausible render could fail, so
        the offline harness could not have caught the 36-page regression either.
        """
        from hyperion.eval.harness import GOLDEN_SET, run_deterministic_checks

        report = {
            "sections": [{"title": "S", "body": "x" * 200, "charts": []}] * 3,
            "total_sources": 8,
            "key_findings": [{"title": "F", "sources": [{"url": "http://e.com"}]}] * 3,
            "executive_summary": "y" * 300,
            "recommendation": "PROCEED",
        }
        checks = run_deterministic_checks(
            report,
            pdf_path=self._pdf_with_pages(tmp_path, 36),
            golden=GOLDEN_SET[0],
        )
        check = next(c for c in checks if c.name == "page_count_reasonable")
        assert check.passed is False
        assert "36" in check.detail

    def test_eval_harness_passes_a_compliant_pdf(self, tmp_path):
        from hyperion.eval.harness import GOLDEN_SET, run_deterministic_checks

        report = {
            "sections": [{"title": "S", "body": "x" * 200, "charts": []}] * 3,
            "total_sources": 8,
            "key_findings": [{"title": "F", "sources": [{"url": "http://e.com"}]}] * 3,
            "executive_summary": "y" * 300,
            "recommendation": "PROCEED",
        }
        checks = run_deterministic_checks(
            report,
            pdf_path=self._pdf_with_pages(tmp_path, 18),
            golden=GOLDEN_SET[0],
        )
        assert next(c for c in checks if c.name == "page_count_reasonable").passed


class TestTheBudgetReachesTheGateInProduction:
    """The gate must be budget-aware on the real delivery path, not just in tests.

    Every assertion in `TestBudgetAwareWidening` constructs the budget by hand.
    That proves the verdict logic is fair, but not that production ever supplies
    it — and a `page_budget` parameter that nothing passes is indistinguishable
    from no parameter at all. Without these tests, a ceiling-clamped 1-section
    engagement would still be failed for its length in a real run.
    """

    def _dag_with_sections(self, section_count: int):
        """A minimal DAG whose Synthesis Lead output carries `section_count`."""
        from types import SimpleNamespace

        from hyperion.schemas.agents import AgentName

        task = SimpleNamespace(id="t_synth", agent=AgentName.SYNTHESIS_LEAD)
        dag = SimpleNamespace(tasks=[task], question="q")
        report = SimpleNamespace(sections=[object()] * section_count)
        return dag, {"t_synth": report}

    def _engine(self):
        from hyperion.orchestrator import WorkflowEngine

        engine = WorkflowEngine.__new__(WorkflowEngine)  # no live LLM/bus needed
        return engine

    def test_orchestrator_reconstructs_the_budget_from_the_report(self):
        engine = self._engine()
        dag, outputs = self._dag_with_sections(4)
        engine._task_outputs = outputs

        budget = engine._page_budget_for(dag)
        assert budget is not None
        assert budget.section_count == 4

    def test_reconstructed_budget_equals_the_planned_one(self):
        """`plan_budget` is pure, so recomputation must be exact.

        If it were not, the gate would be judging the report against a budget
        subtly different from the one it was written under — worse than no budget
        at all, because the discrepancy would be invisible.
        """
        engine = self._engine()
        for n in REALISTIC:
            dag, outputs = self._dag_with_sections(n)
            engine._task_outputs = outputs
            assert engine._page_budget_for(dag) == plan_budget(n)

    def test_returns_none_when_there_is_no_report(self):
        """No report to measure => flat contract band, not a skipped check."""
        engine = self._engine()
        dag, _ = self._dag_with_sections(0)
        engine._task_outputs = {}
        assert engine._page_budget_for(dag) is None

    def test_returns_none_for_a_report_with_no_sections(self):
        engine = self._engine()
        dag, outputs = self._dag_with_sections(0)
        engine._task_outputs = outputs
        assert engine._page_budget_for(dag) is None

    def test_render_engine_run_accepts_the_budget_parameter(self):
        """The parameter the orchestrator passes must exist on `run`.

        A keyword mismatch here would raise TypeError at the very end of a long
        engagement — after all the research and LLM spend, at the last step.
        """
        import inspect

        from hyperion.agents.delivery.render_engine import RenderEngine

        assert "page_budget" in inspect.signature(RenderEngine.run).parameters

    def test_orchestrator_passes_the_budget_to_the_render_engine(self):
        """Greps the call site: the wiring, not just the parameter, must exist."""
        from pathlib import Path

        source = Path("hyperion/orchestrator.py").read_text(encoding="utf-8")
        assert "page_budget=self._page_budget_for(dag)" in source

    async def test_run_stores_the_budget_it_is_given(self):
        """`run` must actually retain the budget rather than ignore it."""
        from hyperion.agents.delivery.render_engine import RenderEngine

        engine = RenderEngine()
        budget = plan_budget(3)
        # No HTML path => `run` returns early, which is precisely the point:
        # the budget must be recorded before any of the render work happens,
        # otherwise a failure path could discard it.
        await engine.run(page_budget=budget)
        assert engine._page_budget == budget


class TestTheContractIsStatedOnceInTheCodebase:
    """Greps the source for the stale windows the fix removed.

    Without these, someone can reintroduce a second, looser statement of the
    contract in a prompt or a schema and nothing will notice — which is exactly
    how `15-40`, `5-60` and `15-20` came to coexist.
    """

    def _code_of(self, rel: str) -> str:
        """Executable code only — comments and docstrings removed.

        Both must go. Every one of these fixes documents the old value in prose
        ("this previously asserted 15 <= page_count <= 40, which..."), and that
        documentation is worth keeping: it is how the next reader learns why the
        band is narrow. A naive line filter that dropped only `#` lines left the
        docstrings in and failed this test on its own explanatory note — caught
        on first run.

        Tokenising is the right instrument rather than a regex: it knows the
        difference between a string that is a statement (a docstring) and a
        string that is a value.
        """
        import io
        import tokenize
        from pathlib import Path

        source = Path(rel).read_text(encoding="utf-8")
        kept: list[str] = []
        prev_type: int | None = None
        for tok in tokenize.generate_tokens(io.StringIO(source).readline):
            if tok.type == tokenize.COMMENT:
                continue
            # A STRING that opens a logical line is a docstring / bare string
            # expression, never a value being assigned or passed.
            if tok.type == tokenize.STRING and prev_type in (
                None,
                tokenize.INDENT,
                tokenize.DEDENT,
                tokenize.NEWLINE,
                tokenize.NL,
            ):
                prev_type = tok.type
                continue
            if tok.type not in (tokenize.NL, tokenize.NEWLINE):
                kept.append(tok.string)
            prev_type = tok.type
        return " ".join(kept)

    def test_render_no_longer_hardcodes_the_wide_window(self):
        assert "15 <= page_count <= 40" not in self._code_of("hyperion/output/render.py")

    def test_harness_no_longer_hardcodes_its_own_window(self):
        assert "5 <= page_count <= 60" not in self._code_of("hyperion/eval/harness.py")

    def test_designer_prompt_no_longer_states_15_40_pages(self):
        """The layout agent and the verifying agent must share one contract."""
        code = self._code_of("hyperion/agents/delivery/presentation_designer.py")
        assert "15-40 pages" not in code

    def test_layout_plan_schema_no_longer_states_15_40(self):
        assert "15-40 for standard" not in self._code_of("hyperion/schemas/models.py")

    def test_all_three_call_sites_use_the_shared_verdict(self):
        """Derivation, not coincidence, is what keeps them aligned."""
        for path in (
            "hyperion/output/render.py",
            "hyperion/eval/harness.py",
            "hyperion/agents/delivery/render_engine.py",
        ):
            assert "page_count_verdict" in self._code_of(path), (
                f"{path} does not use the shared page-count verdict"
            )
