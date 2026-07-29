"""Work that is performed must reach an output. Ruff F841 found where it didn't.

Why this file exists
--------------------
Standing up ruff for 5.1 surfaced 25 `F841 local variable assigned but never
used` findings. Triaged individually, most were not lint noise — they were the
signature of a distinct and expensive defect class: **a computation that runs,
costs time or tokens, and is then discarded before it can affect anything.**

The worst four, all locked in below:

1. `risk_analyst` computed `_build_risk_matrix()` (its own declared headline
   output) and `await _run_monte_carlo()` — an **LLM call** — into locals that
   nothing read. Every engagement paid for a Monte Carlo simulation whose result
   was garbage-collected unread, and the 5x5 matrix never reached the report.
2. `presentation_designer._build_risk_analysis_html` read
   `getattr(risk, "name", "Unknown")`, but `Risk` has **no `name` field**. So the
   Risk column of the top-risks table printed the literal string `Unknown` on
   every row of every report ever produced — and `Unknown` is one of the tokens
   `tools/audit_render_probe.py` counts as a **template leak**, which §11 exit
   criterion 11 requires to be zero.
3. `synthesis_lead` awaited `_query_second_brain_for_patterns()`, announced it to
   the user as a pipeline step, and dropped the result. §12.8's "this pattern
   matching makes the system smarter over time" could not happen: the patterns
   never reached a prompt.
4. `unsplash` and `wayback` retry loops ended in an **unconditional `return`**
   after the backoff sleep, so `MAX_RETRIES`, the `attempt` guard and the
   trailing "All retries exhausted" line were all decorative — one transient
   error was a permanent failure, silently, because the cause was dropped too.

These are asserted here rather than left to the lint config because a lint rule
is a control only while someone runs it. The same reasoning as
`tests/test_module_importability.py`.
"""

from __future__ import annotations

import ast
import inspect
import re
from pathlib import Path

from hyperion.schemas.models import ConfidenceLevel, Risk, RiskAnalysis, RiskCategory

REPO_ROOT = Path(__file__).resolve().parent.parent


def _risk(idx: int, prob: int, impact: int, *, desc: str = "") -> Risk:
    return Risk(
        id=f"risk_{idx}",
        category=RiskCategory.MARKET,
        description=desc or f"Demand in the target segment softens ({idx})",
        probability=prob,
        impact=impact,
        risk_score=prob * impact,
        mitigation=f"Stage the rollout and re-price at gate {idx}",
        owner="Market Analyst",
    )


class TestRiskAnalysisCarriesItsComputedAnalytics:
    """Defect 1: matrix + Monte Carlo computed, then dropped."""

    def test_schema_has_somewhere_to_put_the_matrix_and_simulation(self) -> None:
        assert "risk_matrix" in RiskAnalysis.model_fields
        assert "monte_carlo" in RiskAnalysis.model_fields

    def test_both_fields_default_to_empty_not_none(self) -> None:
        """A consumer must be able to `.get()` without a None check."""
        analysis = RiskAnalysis(
            risks=[],
            residual_risk_summary="none",
            confidence=ConfidenceLevel.LOW,
        )
        assert analysis.risk_matrix == {}
        assert analysis.monte_carlo == {}

    def test_the_agent_passes_both_into_the_model(self) -> None:
        """Source-level: the two locals must reach the constructor.

        An output assertion cannot check this without running a full engagement
        (two LLM calls). The defect was purely one of wiring, so wiring is what
        is asserted — the same tactic 4.5 used for its builders.
        """
        src = (
            REPO_ROOT / "hyperion" / "agents" / "specialists" / "risk_analyst.py"
        ).read_text(encoding="utf-8")
        tree = ast.parse(src)

        constructor_kwargs: set[str] = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "RiskAnalysis"
            ):
                constructor_kwargs.update(kw.arg for kw in node.keywords if kw.arg)

        assert "risk_matrix" in constructor_kwargs, (
            "_build_risk_matrix() is computed but never passed to RiskAnalysis — "
            "the 5x5 matrix is the Risk Analyst's declared headline output"
        )
        assert "monte_carlo" in constructor_kwargs, (
            "_run_monte_carlo() is an awaited LLM call; if its result is not "
            "passed to RiskAnalysis the engagement pays for it and discards it"
        )

    def test_monte_carlo_is_still_an_llm_call_worth_wiring(self) -> None:
        """Guards the premise: if it ever stops being awaited, revisit the above."""
        from hyperion.agents.specialists.risk_analyst import RiskAnalyst

        src = inspect.getsource(RiskAnalyst._run_monte_carlo)
        assert "_llm_complete" in src
        assert src.lstrip().startswith("async def")


class TestRiskTableDoesNotPrintTheWordUnknown:
    """Defect 2: a wrong field name that could only ever produce a leak token."""

    def test_risk_schema_has_no_name_field(self) -> None:
        """The premise of the bug — asserted so the fix cannot be misread.

        If a `name` field is ever added, `getattr(risk, "name", "Unknown")`
        becomes correct and this whole test class needs rethinking. Better to
        fail loudly here than to silently over-constrain the renderer.
        """
        assert "name" not in Risk.model_fields, (
            "Risk gained a `name` field — revisit _build_risk_analysis_html, "
            "which now deliberately renders `description`"
        )
        assert "description" in Risk.model_fields

    def _html(self) -> str:
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        analysis = RiskAnalysis(
            risks=[_risk(1, 5, 5), _risk(2, 1, 2)],
            top_risks=[_risk(1, 5, 5), _risk(2, 1, 2)],
            residual_risk_summary="Elevated but bounded",
            confidence=ConfidenceLevel.MEDIUM,
            risk_matrix={"zone_counts": {"red": 1, "yellow": 0, "green": 1}},
        )

        class _Report:
            risk_analysis = analysis
            sections: list[object] = []

        designer = PresentationDesigner.__new__(PresentationDesigner)
        return designer._build_risk_analysis_html(_Report())  # type: ignore[arg-type]

    def test_the_word_unknown_does_not_appear(self) -> None:
        """The exact template-leak token, in the exact table that emitted it."""
        assert "Unknown" not in self._html(), (
            "`Unknown` is counted as a template leak by tools/audit_render_probe.py "
            "and §11 exit criterion 11 requires zero"
        )

    def test_the_real_description_is_rendered_instead(self) -> None:
        html = self._html()
        assert "Demand in the target segment softens (1)" in html

    def test_the_score_is_shown_not_just_its_factors(self) -> None:
        """Rows are ranked by risk_score; the table must show the number."""
        html = self._html()
        assert "<td>25</td>" in html, "risk_score (5x5=25) must be rendered"

    def test_zone_counts_from_the_matrix_reach_the_page(self) -> None:
        """The matrix was computed and discarded; now it must be visible."""
        html = self._html()
        assert "red zone" in html
        assert "<strong>1</strong>" in html

    def test_llm_authored_prose_is_escaped(self) -> None:
        """Risk text is LLM prose; `&`/`<` in it must not corrupt the table."""
        from hyperion.agents.delivery.presentation_designer import PresentationDesigner

        nasty = _risk(3, 4, 4, desc="Gross margin < 10% & churn > 5%")
        analysis = RiskAnalysis(
            risks=[nasty],
            top_risks=[nasty],
            residual_risk_summary="x",
            confidence=ConfidenceLevel.LOW,
        )

        class _Report:
            risk_analysis = analysis
            sections: list[object] = []

        designer = PresentationDesigner.__new__(PresentationDesigner)
        html = designer._build_risk_analysis_html(_Report())  # type: ignore[arg-type]
        assert "&lt; 10% &amp; churn &gt; 5%" in html
        assert "< 10% & churn" not in html

    def test_no_defensive_getattr_defaults_remain_in_the_builder(self) -> None:
        """The construct that hid the bug, banned in the function it hid in.

        Mirrors 4.5's ban on the front/back-matter builders. A wrong field name
        with a `getattr` default produces *plausible* output, which no output
        assertion can catch — only banning the construct bites before the
        mistake is made.
        """
        src = (
            REPO_ROOT / "hyperion" / "agents" / "delivery" / "presentation_designer.py"
        ).read_text(encoding="utf-8")
        start = src.index("def _build_risk_analysis_html")
        end = src.index("def _build_appendix_sources_html")
        body = re.sub(r'""".*?"""', "", src[start:end], flags=re.S)
        offenders = re.findall(r"getattr\([^)]*,\s*[^)]*,\s*[^)]+\)", body)
        assert not offenders, (
            "_build_risk_analysis_html must use direct attribute access so a "
            f"schema mismatch raises instead of printing a placeholder: {offenders}"
        )


class TestSecondBrainPatternsReachAPrompt:
    """Defect 3: an awaited vault query whose result was dropped."""

    def test_identify_and_draft_accepts_prior_patterns(self) -> None:
        from hyperion.agents.synthesis_lead import SynthesisLead

        params = inspect.signature(SynthesisLead._identify_and_draft).parameters
        assert "prior_patterns" in params, (
            "_query_second_brain_for_patterns() is awaited in run(); its result "
            "must reach the call that drafts the recommendation or the vault "
            "lookup is pure cost (§12.8)"
        )

    def test_the_patterns_are_interpolated_into_the_prompt(self) -> None:
        from hyperion.agents.synthesis_lead import SynthesisLead

        src = inspect.getsource(SynthesisLead._identify_and_draft)
        assert "prior_patterns" in src
        assert "patterns_block" in src, "the argument must reach the prompt string"

    def test_run_passes_the_fetched_patterns_through(self) -> None:
        """The wiring at the call site, which is where it was severed.

        🔴 The first version of this test was worthless and a negative control
        proved it. It ran `re.search(r"_identify_and_draft\\(\\s*([^)]*)\\)")` over
        the whole class, and the FIRST match is the `async def
        _identify_and_draft(self, matrix, contradictions, prior_patterns="")`
        signature — whose parameter list of course contains `prior_patterns`. So
        it passed with the call site deliberately severed back to
        `_identify_and_draft(matrix, resolved_contradictions)`.

        A test that reads the definition when it means to read the *call* cannot
        detect an unwired call. It now parses the AST and inspects `ast.Call`
        nodes only, so a definition can never satisfy it.
        """
        from hyperion.agents.synthesis_lead import SynthesisLead

        tree = ast.parse(inspect.getsource(SynthesisLead))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_identify_and_draft"
        ]
        assert calls, "no call to _identify_and_draft found — has it been renamed?"

        for call in calls:
            passed = {ast.unparse(a) for a in call.args} | {
                kw.arg for kw in call.keywords if kw.arg
            }
            assert "prior_patterns" in passed, (
                "run() awaits _query_second_brain_for_patterns() and announces it "
                "as a pipeline step, but does not pass the result to "
                f"_identify_and_draft (line {call.lineno}) — so the vault lookup "
                "is pure cost and §12.8's 'smarter over time' cannot happen. "
                f"Passed: {sorted(passed)}"
            )

    def test_patterns_are_labelled_as_precedent_not_evidence(self) -> None:
        """A prior engagement's pattern must not be citable as a finding here."""
        from hyperion.agents.synthesis_lead import SynthesisLead

        src = inspect.getsource(SynthesisLead._identify_and_draft)
        assert "NOT evidence" in src


class TestRetryLoopsActuallyRetry:
    """Defect 4: `return` below the backoff made every retry loop decorative."""

    RETRY_MODULES = (
        ("hyperion/tools/unsplash.py", 1),
        ("hyperion/tools/wayback.py", 3),
        ("hyperion/tools/jina.py", 2),
    )

    def _retry_loops(self, rel: str) -> list[ast.For]:
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        loops: list[ast.For] = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.For):
                continue
            # `for attempt in range(self.MAX_RETRIES):`
            if isinstance(node.target, ast.Name) and node.target.id == "attempt":
                loops.append(node)
        return loops

    def test_the_retry_loops_are_where_we_think_they_are(self) -> None:
        for rel, expected in self.RETRY_MODULES:
            found = len(self._retry_loops(rel))
            assert found == expected, f"{rel}: expected {expected} retry loops, found {found}"

    def test_every_retry_loop_can_reach_a_second_attempt(self) -> None:
        """The defect precisely: a handler that sleeps must be able to loop again.

        Without a reachable `continue`, control falls through to the `return`
        beneath and the loop exits after one attempt — sleeping first, which makes
        it *slower* than no retry at all while providing none of the benefit.

        The first draft of this test asserted `continue` appeared **inside the
        `if attempt < MAX_RETRIES - 1:` body**, and it produced two false
        positives on `jina.py`, whose (correct) shape is

            if attempt < self.MAX_RETRIES - 1:
                await asyncio.sleep(...)
            continue

        — `continue` after the guard is equivalent, and on the final attempt it
        re-enters the loop only to have `range()` end it. Recorded rather than
        quietly patched: I nearly "fixed" two already-correct call sites to
        satisfy a bad assertion, which is how a lint-driven cleanup injects
        defects. The check is now about *reachability from the handler*, which is
        the property that actually matters.
        """
        offenders: list[str] = []
        for rel, _ in self.RETRY_MODULES:
            for loop in self._retry_loops(rel):
                for handler in [n for n in ast.walk(loop) if isinstance(n, ast.ExceptHandler)]:
                    handler_src = " ".join(ast.dump(s) for s in handler.body)
                    if "sleep" not in handler_src:
                        continue  # not a backoff handler
                    if "Continue" not in handler_src:
                        offenders.append(f"{rel}:{handler.lineno}")
        assert not offenders, (
            "an except handler that sleeps for backoff but has no reachable "
            "`continue` falls through to the return below it — the loop sleeps, "
            "then gives up:\n  " + "\n  ".join(offenders)
        )

    def test_exhaustion_is_logged_in_the_discovery_and_imagery_paths(self) -> None:
        """Giving up must never be silent (§0.3)."""
        for rel in ("hyperion/tools/unsplash.py", "hyperion/tools/jina.py"):
            src = (REPO_ROOT / rel).read_text(encoding="utf-8")
            assert "logger = logging.getLogger(__name__)" in src, f"{rel} has no logger"
            assert "exhausted" in src, f"{rel} does not log retry exhaustion"


class TestWorldBankTruncationIsNotSilent:
    """A discarded `metadata` local was hiding the API's pagination envelope."""

    def test_the_pagination_envelope_is_inspected(self) -> None:
        from hyperion.tools.world_bank import WorldBankClient

        src = inspect.getsource(WorldBankClient.get_indicator)
        assert '"pages"' in src or "'pages'" in src, (
            "World Bank returns [metadata, points] where metadata carries "
            "{page, pages, per_page, total}. Ignoring it silently truncates a "
            "multi-page result to page 1 and reports it as complete."
        )
        assert "TRUNCATED" in src, "a silent truncation must at least be a loud one"
