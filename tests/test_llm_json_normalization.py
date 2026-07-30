"""
Phase 5.1e — two defects that made specialist agents report success while
delivering nothing.

═══════════════════════════════════════════════════════════════════════════
DEFECT 1 — the systemic `json.loads(response.content)` outage
═══════════════════════════════════════════════════════════════════════════

72 call sites across 19 files (every specialist agent) do:

    try:
        data = json.loads(response.content)
    except (json.JSONDecodeError, ValueError, TypeError):
        return SomeFramework()          # <- structurally valid, EMPTY

`BaseAgent._llm_complete` had a mitigation for this, but it was gated twice
over and both gates were wrong:

    if response.success and response.content and response_format \\
            and response_format.get("type") == "json_object":
        content = response.content.strip()
        if content.startswith("```"):
            ...

Gate A (`response_format`) keys the repair off what we *asked for* rather than
what we *got*. An AST scan of the tree found 5 of 78 `_llm_complete` call sites
omit `response_format` entirely and still `json.loads` the result — and several
providers ignore the field even when it is sent.

Gate B (`startswith("```")`) only catches JSON whose very first characters are
a fence. Measured against the shapes real providers return, it missed 4 of 6:

    fenced ```json {...} ```        -> handled
    bare fence ``` {...} ```        -> handled
    prose THEN fence               -> MISSED  "Sure!\\n```json\\n{...}\\n```"
    prose prefix, no fence         -> MISSED  "Here is the analysis:\\n{...}"
    prose suffix                   -> MISSED  "{...}\\nHope that helps!"
    trailing commentary            -> MISSED  "{...} - note the caveat."

Every miss lands on the `except` branch above. The user is handed a Porter's
Five Forces with no forces, a VRIO with no resources, a claim list with no
claims — and the run is reported as a success. That is the §0.3 silent-failure
anti-pattern replicated across the entire specialist layer.

═══════════════════════════════════════════════════════════════════════════
DEFECT 2 — three advertised strategy frameworks were never implemented
═══════════════════════════════════════════════════════════════════════════

`StrategyAnalyst._select_frameworks` offers the LLM 8 frameworks and asks it
to pick 3-5. Only 5 had implementations. `run()` hardcoded:

    bcg_matrix=None,       # Only applied if portfolio question
    blue_ocean=None,       # Only applied if market creation question
    core_competence=None,  # Derived from VRIO if needed

...and no such conditional path existed anywhere in the file. So for exactly
the questions the selector was designed to route (a portfolio question picks
BCG), `frameworks_selected` advertised analysis that the agent was
structurally incapable of performing. A false claim in a paid deliverable.

═══════════════════════════════════════════════════════════════════════════
What must hold after the fix
═══════════════════════════════════════════════════════════════════════════
  1. Every wrapper shape above normalizes to parseable JSON.
  2. Non-JSON completions (prose, drafted prose sections, markdown) pass
     through byte-identical — normalization must never damage them.
  3. Normalization is unconditional: not gated on `response_format`, and not
     gated on `startswith("```")`. (Structural AST guard.)
  4. Normalization is conservative: a candidate that does not itself parse is
     discarded and the original returned, so a caller's error path sees the
     true response rather than a fragment we invented.
  5. The three frameworks run when selected, are absent when not selected,
     and BCG categorisation is derived from the axes rather than taken on the
     model's word.
  6. All 8 frameworks advertised by `_select_frameworks` have an implementation
     that `run()` can actually reach. (Structural guard against the *next*
     framework being advertised and never wired up.)

Negative controls (must fail when the defect is reintroduced):
  NC1  restore the `response_format` gate            -> TestNormalizationIsUnconditional
  NC2  restore the `startswith("```")` gate          -> TestWrappedJsonShapes + guard
  NC3  restore `bcg_matrix=None` in run()            -> TestSelectedFrameworksActuallyRun
  NC4  let `_classify_bcg` trust the model's label    -> TestBcgAxesAreAuthoritative
  NC5  drop the blue-ocean empty-space downgrade      -> TestBlueOceanEmptyClaimIsDowngraded
"""

from __future__ import annotations

import ast
import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import AgentBus
from hyperion.config import ModelTier, ProviderType
from hyperion.router.providers.base import RouterResponse
from hyperion.schemas.models import (
    BCGCategory,
    BlueOceanStrategy,
    CoreCompetence,
    VRIOAssessment,
    VRIOResult,
)

_REPO = Path(__file__).resolve().parents[1]
BASE_PY = _REPO / "hyperion" / "agents" / "base.py"
STRATEGY_PY = _REPO / "hyperion" / "agents" / "specialists" / "strategy_analyst.py"

# The normalizer is a staticmethod, so it is directly addressable without
# building an agent — which is the point: it must be independently testable.
normalize = BaseAgent._normalize_json_content


# ─────────────────────────────────────────────────────────────────────────
# Corpus: the shapes providers actually return
# ─────────────────────────────────────────────────────────────────────────

_PAYLOAD = {"forces": [{"force": "Buyer power", "intensity": "high"}], "score": 3.5}
_BODY = json.dumps(_PAYLOAD)

WRAPPED_JSON_SHAPES: list[tuple[str, str]] = [
    ("clean", _BODY),
    ("clean_with_whitespace", f"\n\n  {_BODY}  \n\n"),
    ("fenced_json", f"```json\n{_BODY}\n```"),
    ("fenced_bare", f"```\n{_BODY}\n```"),
    ("fenced_uppercase_lang", f"```JSON\n{_BODY}\n```"),
    # The four the old gate missed:
    ("prose_then_fence", f"Sure! Here you go:\n\n```json\n{_BODY}\n```"),
    ("prose_prefix_no_fence", f"Here is the analysis:\n{_BODY}"),
    ("prose_suffix", f"{_BODY}\nHope that helps!"),
    ("trailing_commentary", f"{_BODY} - note the caveat about buyer power."),
    ("prose_both_sides", f"Analysis follows.\n{_BODY}\nLet me know if you need more."),
    ("fence_no_trailing_newline", f"```json{_BODY}```"),
]

# Completions that are genuinely not JSON. These must survive untouched —
# ~half of `_llm_complete` calls draft prose (executive summaries, section
# bodies, action titles) and a normalizer that mangles them would be a worse
# defect than the one being fixed.
NON_JSON_SHAPES: list[tuple[str, str]] = [
    ("plain_prose", "The market is consolidating around three platform vendors."),
    (
        "prose_with_braces_in_text",
        "Revenue grew {sic} 12% — the figure in the filing is transcribed as {12}.",
    ),
    ("markdown_heading", "## Executive summary\n\nThree things matter here."),
    ("bulleted_list", "- Buyer power is high\n- Supplier power is low"),
    ("empty", ""),
    ("whitespace_only", "   \n\t  "),
    ("code_fence_python", "```python\ndef f():\n    return 1\n```"),
]


class TestWrappedJsonShapes:
    """Every wrapper a provider might apply must reduce to parseable JSON."""

    @pytest.mark.parametrize(
        "name,raw", WRAPPED_JSON_SHAPES, ids=[n for n, _ in WRAPPED_JSON_SHAPES]
    )
    def test_shape_becomes_parseable(self, name: str, raw: str) -> None:
        out = normalize(raw)
        try:
            parsed = json.loads(out)
        except json.JSONDecodeError as exc:  # pragma: no cover - failure path
            pytest.fail(
                f"shape {name!r} did not normalize to parseable JSON: {exc}\n"
                f"  input:  {raw!r}\n  output: {out!r}"
            )
        assert parsed == _PAYLOAD, f"shape {name!r} normalized to the wrong payload: {parsed!r}"

    def test_the_four_shapes_the_old_gate_missed(self) -> None:
        """Pins the specific regression: `startswith('```')` was not enough."""
        missed = [
            f"Sure! Here you go:\n\n```json\n{_BODY}\n```",
            f"Here is the analysis:\n{_BODY}",
            f"{_BODY}\nHope that helps!",
            f"{_BODY} - note the caveat.",
        ]
        for raw in missed:
            assert not raw.strip().startswith("```") or "Sure!" not in raw
            assert json.loads(normalize(raw)) == _PAYLOAD, (
                f"shape the old gate missed is still broken: {raw[:60]!r}"
            )

    def test_json_array_payload_survives(self) -> None:
        """fact_checker asks for a LIST of up to 30 claims, not an object."""
        claims = [{"claim": "A"}, {"claim": "B"}, {"claim": "C"}]
        raw = f"Here are the claims:\n```json\n{json.dumps(claims)}\n```"
        assert json.loads(normalize(raw)) == claims

    def test_nested_object_is_not_collapsed_to_an_inner_object(self) -> None:
        """The 5.1d `extract_json` fix must still hold through this path."""
        nested = {"a": {"b": 1}, "c": 2}
        raw = f"Result: {json.dumps(nested)}"
        assert json.loads(normalize(raw)) == nested

    def test_json_containing_braces_inside_strings(self) -> None:
        payload = {"note": "the template is {placeholder} and }} is literal"}
        raw = f"```json\n{json.dumps(payload)}\n```"
        assert json.loads(normalize(raw)) == payload


class TestNonJsonPassesThroughUnharmed:
    """Normalization must be a strict no-op on non-JSON completions."""

    @pytest.mark.parametrize("name,raw", NON_JSON_SHAPES, ids=[n for n, _ in NON_JSON_SHAPES])
    def test_returned_byte_identical(self, name: str, raw: str) -> None:
        out = normalize(raw)
        assert out == raw, (
            f"non-JSON shape {name!r} was modified by the normalizer.\n"
            f"  in:  {raw!r}\n  out: {out!r}"
        )

    def test_prose_that_merely_mentions_a_brace_is_untouched(self) -> None:
        raw = "Set the flag to {true} in config; see appendix."
        assert normalize(raw) == raw

    def test_partial_json_is_not_salvaged(self) -> None:
        """A truncated payload must surface as-is so the caller sees the truth.

        Returning a salvaged fragment would be worse than failing: the agent
        would proceed on half a framework believing it had a whole one.
        """
        raw = '{"forces": [{"force": "Buyer power",'
        assert normalize(raw) == raw

    def test_candidate_that_does_not_parse_is_discarded(self) -> None:
        """Extraction that yields garbage must not replace the original."""
        raw = "Here: {this is not json at all, really}"
        assert normalize(raw) == raw


class TestNormalizationIsConservative:
    """The candidate must itself parse before it is allowed to win."""

    def test_clean_json_takes_the_fast_path_unchanged(self) -> None:
        assert normalize(_BODY) == _BODY

    def test_result_is_always_a_str(self) -> None:
        for _, raw in WRAPPED_JSON_SHAPES + NON_JSON_SHAPES:
            assert isinstance(normalize(raw), str)

    def test_is_idempotent(self) -> None:
        """Normalizing twice must equal normalizing once."""
        for name, raw in WRAPPED_JSON_SHAPES:
            once = normalize(raw)
            assert normalize(once) == once, f"not idempotent for {name!r}"

    def test_is_pure(self) -> None:
        """Same input, same output, no hidden state."""
        for _, raw in WRAPPED_JSON_SHAPES:
            assert normalize(raw) == normalize(raw)


# ─────────────────────────────────────────────────────────────────────────
# Behavioural: through the real `_llm_complete`
# ─────────────────────────────────────────────────────────────────────────


class _ScriptedRouter:
    """Router that returns a fixed body, recording the request."""

    def __init__(self, content: str, success: bool = True) -> None:
        self._content = content
        self._success = success
        self.calls: list[dict[str, Any]] = []

    async def complete(self, **kwargs: Any) -> RouterResponse:
        self.calls.append(kwargs)
        return RouterResponse(
            content=self._content,
            model="stub-model",
            provider=ProviderType.GOOGLE,
            tier=ModelTier.STANDARD,
            success=self._success,
            error=None if self._success else "stubbed failure",
        )


def _agent_with_router(router: Any) -> BaseAgent:
    from hyperion.agents.engagement_director import ENGAGEMENT_DIRECTOR_SPEC

    class _Agent(BaseAgent):
        async def run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            return None

    return _Agent(spec=ENGAGEMENT_DIRECTOR_SPEC, bus=AgentBus(), router=router)


class TestNormalizationIsUnconditional:
    """NC1: this class fails if the `response_format` gate is restored."""

    @pytest.mark.parametrize(
        "response_format",
        [None, {"type": "json_object"}, {"type": "text"}],
        ids=["no_response_format", "json_object", "text"],
    )
    def test_applies_regardless_of_response_format(
        self, response_format: dict[str, str] | None
    ) -> None:
        router = _ScriptedRouter(f"Sure!\n```json\n{_BODY}\n```")
        agent = _agent_with_router(router)
        resp = asyncio.run(
            agent._llm_complete(user_prompt="x", response_format=response_format)
        )
        assert json.loads(resp.content) == _PAYLOAD, (
            "normalization did not run — it is still gated on response_format "
            f"(response_format={response_format!r}, content={resp.content!r})"
        )

    def test_a_caller_omitting_response_format_still_gets_clean_json(self) -> None:
        """5 of 78 `_llm_complete` sites omit response_format and json.loads anyway."""
        router = _ScriptedRouter(f"Here is the analysis:\n{_BODY}")
        agent = _agent_with_router(router)
        resp = asyncio.run(agent._llm_complete(user_prompt="x"))
        assert json.loads(resp.content) == _PAYLOAD

    def test_failed_response_is_not_normalized(self) -> None:
        """A failure body is diagnostic text — leave it exactly as-is."""
        router = _ScriptedRouter("upstream 503: {partial", success=False)
        agent = _agent_with_router(router)
        resp = asyncio.run(agent._llm_complete(user_prompt="x"))
        assert resp.content == "upstream 503: {partial"

    def test_prose_completion_through_llm_complete_is_unharmed(self) -> None:
        prose = "## Executive summary\n\nThe market is consolidating."
        router = _ScriptedRouter(prose)
        agent = _agent_with_router(router)
        resp = asyncio.run(agent._llm_complete(user_prompt="x"))
        assert resp.content == prose


class TestNoConditionalGateRemainsInSource:
    """Structural guard (NC2): the gates must not come back.

    A behavioural test can be satisfied by a *different* gate that happens to
    admit the tested shapes. This asserts on the shape of the code itself.
    """

    def _normalizer_call_node(self) -> ast.Call:
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "_normalize_json_content"
        ]
        assert calls, "no call to _normalize_json_content found in base.py"
        assert len(calls) == 1, (
            f"expected exactly one normalization call site, found {len(calls)} — "
            "duplicated normalization means one of them can drift"
        )
        return calls[0]

    def test_normalizer_is_called_exactly_once(self) -> None:
        self._normalizer_call_node()

    def test_guarding_condition_does_not_mention_response_format(self) -> None:
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.If):
                continue
            body_src = ast.dump(ast.Module(body=node.body, type_ignores=[]))
            if "_normalize_json_content" not in body_src:
                continue
            test_src = ast.dump(node.test)
            assert "response_format" not in test_src, (
                "the `response_format` gate has been reintroduced around "
                "_normalize_json_content — normalization must key off the "
                "RESPONSE, not the request"
            )
            assert "startswith" not in test_src, (
                "the `startswith(\"```\")` gate has been reintroduced — it "
                "misses 4 of 6 real provider shapes"
            )

    def test_no_startswith_fence_check_anywhere_in_llm_complete(self) -> None:
        """The specific expression that caused the outage must be gone."""
        src = BASE_PY.read_text(encoding="utf-8")
        offenders = [
            line.strip()
            for line in src.splitlines()
            if "startswith(" in line and "```" in line and not line.strip().startswith("#")
        ]
        assert not offenders, (
            "a live `startswith('```')` fence check is back in base.py: " f"{offenders!r}"
        )

    def test_normalizer_exists_as_a_static_method(self) -> None:
        """It must be independently testable, i.e. not buried inline."""
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        found = [
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.FunctionDef) and n.name == "_normalize_json_content"
        ]
        assert found, "_normalize_json_content is not defined as a named function"
        decorators = {
            d.id for d in found[0].decorator_list if isinstance(d, ast.Name)
        }
        assert "staticmethod" in decorators, (
            "_normalize_json_content should be a staticmethod — it has no "
            "reason to touch instance state, and being one is what lets these "
            "tests call it without constructing an agent"
        )


# ─────────────────────────────────────────────────────────────────────────
# Defect 2 — the three missing strategy frameworks
# ─────────────────────────────────────────────────────────────────────────


def _strategy_analyst(selected: list[str] | None = None) -> Any:
    from hyperion.agents.specialists.strategy_analyst import StrategyAnalyst

    agent = StrategyAnalyst(bus=AgentBus(), router=_ScriptedRouter("{}"))
    agent._frameworks_selected = list(selected or [])
    return agent


def _stub_llm(agent: Any, body: str, success: bool = True) -> list[str]:
    """Replace `_llm_complete` with a scripted response; return prompt log."""
    prompts: list[str] = []

    async def _fake(user_prompt: str, **kwargs: Any) -> RouterResponse:
        prompts.append(user_prompt)
        return RouterResponse(
            content=body,
            model="stub",
            provider=ProviderType.GOOGLE,
            tier=ModelTier.STANDARD,
            success=success,
        )

    agent._llm_complete = _fake  # type: ignore[method-assign]
    return prompts


class TestFrameworkSelectionMatching:
    """`_framework_selected` reads the selector's own output format."""

    @pytest.mark.parametrize(
        "label",
        [
            "BCG growth-share matrix: the question is about portfolio mix",
            "BCG matrix: portfolio allocation",
            "bcg growth share matrix: portfolio",
            "Growth-share matrix: portfolio",
        ],
    )
    def test_bcg_labels_match(self, label: str) -> None:
        agent = _strategy_analyst([label])
        assert agent._framework_selected(agent._BCG_TOKENS) is True

    @pytest.mark.parametrize(
        "label",
        [
            "Blue Ocean strategy: the client wants a new category",
            "blue ocean: market creation",
        ],
    )
    def test_blue_ocean_labels_match(self, label: str) -> None:
        agent = _strategy_analyst([label])
        assert agent._framework_selected(agent._BLUE_OCEAN_TOKENS) is True

    @pytest.mark.parametrize(
        "label",
        [
            "Core competence analysis: capability assessment",
            "Core competency analysis: capabilities",
            "core competencies: what can they actually do",
        ],
    )
    def test_core_competence_labels_match(self, label: str) -> None:
        agent = _strategy_analyst([label])
        assert agent._framework_selected(agent._CORE_COMPETENCE_TOKENS) is True

    def test_unselected_frameworks_do_not_match(self) -> None:
        agent = _strategy_analyst(
            ["Porter's Five Forces: industry attractiveness", "SWOT/TOWS: positioning"]
        )
        assert agent._framework_selected(agent._BCG_TOKENS) is False
        assert agent._framework_selected(agent._BLUE_OCEAN_TOKENS) is False
        assert agent._framework_selected(agent._CORE_COMPETENCE_TOKENS) is False

    def test_empty_selection_matches_nothing(self) -> None:
        agent = _strategy_analyst([])
        assert agent._framework_selected(agent._BCG_TOKENS) is False


class TestBcgAxesAreAuthoritative:
    """NC4: the quadrant comes from the axes, not the model's adjective.

    BCG's four categories are *defined* by market growth and relative share.
    A model that labels a 2%-growth unit a "star" is contradicting the
    framework it was asked to apply, and publishing that label would put a
    factually wrong exhibit in the deck.
    """

    @pytest.mark.parametrize(
        "growth,share,expected",
        [
            ("25%", "1.8x", BCGCategory.STAR),
            ("12%", "1.0x", BCGCategory.STAR),
            ("3%", "2.4x", BCGCategory.CASH_COW),
            ("0%", "1.1x", BCGCategory.CASH_COW),
            ("30%", "0.4x", BCGCategory.QUESTION_MARK),
            ("18%", "0.99x", BCGCategory.QUESTION_MARK),
            ("2%", "0.3x", BCGCategory.DOG),
            ("-4%", "0.2x", BCGCategory.DOG),
        ],
    )
    def test_quadrant_derived_from_axes(
        self, growth: str, share: str, expected: BCGCategory
    ) -> None:
        agent = _strategy_analyst()
        assert agent._classify_bcg(growth, share) is expected

    @pytest.mark.parametrize(
        "growth,share,wrong_label,expected",
        [
            ("2%", "0.3x", "star", BCGCategory.DOG),
            ("40%", "0.1x", "cash_cow", BCGCategory.QUESTION_MARK),
            ("1%", "3.0x", "dog", BCGCategory.CASH_COW),
            ("35%", "2.0x", "question_mark", BCGCategory.STAR),
        ],
    )
    def test_model_label_is_overruled_by_the_axes(
        self, growth: str, share: str, wrong_label: str, expected: BCGCategory
    ) -> None:
        agent = _strategy_analyst()
        got = agent._classify_bcg(growth, share, wrong_label)
        assert got is expected, (
            f"model said {wrong_label!r} for growth={growth} share={share}; "
            f"the axes say {expected.value!r} but classifier returned {got.value!r}"
        )

    def test_model_label_is_used_when_an_axis_is_missing(self) -> None:
        agent = _strategy_analyst()
        assert agent._classify_bcg("", "1.4x", "cash_cow") is BCGCategory.CASH_COW
        assert agent._classify_bcg("20%", "", "star") is BCGCategory.STAR

    @pytest.mark.parametrize(
        "label,expected",
        [
            ("cash cow", BCGCategory.CASH_COW),
            ("cash-cow", BCGCategory.CASH_COW),
            ("Question Mark", BCGCategory.QUESTION_MARK),
            ("STAR", BCGCategory.STAR),
        ],
    )
    def test_model_label_normalisation(self, label: str, expected: BCGCategory) -> None:
        agent = _strategy_analyst()
        assert agent._classify_bcg("", "", label) is expected

    def test_unknown_everything_falls_back_to_question_mark(self) -> None:
        """QUESTION_MARK is the genuine "unproven" quadrant, not a silent default."""
        agent = _strategy_analyst()
        assert agent._classify_bcg("", "", None) is BCGCategory.QUESTION_MARK
        assert agent._classify_bcg("n/a", "unknown", "nonsense") is BCGCategory.QUESTION_MARK

    @pytest.mark.parametrize(
        "text,expected",
        [
            ("12%", 12.0),
            ("1.4x", 1.4),
            ("~8.5 %", 8.5),
            ("-3%", -3.0),
            ("approx 0.62x of leader", 0.62),
            ("", None),
            ("n/a", None),
            ("high", None),
        ],
    )
    def test_leading_number_parser(self, text: str, expected: float | None) -> None:
        agent = _strategy_analyst()
        assert agent._parse_leading_number(text) == expected

    def test_thresholds_are_named_constants_not_magic_numbers(self) -> None:
        agent = _strategy_analyst()
        assert agent.BCG_HIGH_GROWTH_PCT == 10.0
        assert agent.BCG_HIGH_RELATIVE_SHARE == 1.0


def _unit(name: str, growth: str, share: str, **extra: Any) -> dict[str, Any]:
    """A BCG unit as the LLM would emit it."""
    return {
        "unit_name": name,
        "market_growth_rate": growth,
        "relative_market_share": share,
        **extra,
    }


class TestBcgMatrixConstruction:
    """The matrix must be internally consistent and refuse unnamed units."""

    def _run(self, body: str) -> Any:
        agent = _strategy_analyst(["BCG growth-share matrix: portfolio"])
        _stub_llm(agent, body)
        return asyncio.run(agent._run_bcg_matrix("q", [], {}))

    def test_units_are_bucketed_consistently(self) -> None:
        body = json.dumps(
            {
                "units": [
                    _unit("Cloud", "28%", "1.5x"),
                    _unit("Legacy", "1%", "2.2x"),
                    _unit("Edge", "35%", "0.3x"),
                    _unit("Print", "-2%", "0.4x"),
                ],
                "portfolio_balance": "Cloud funds Edge.",
            }
        )
        m = self._run(body)
        assert m.stars == ["Cloud"]
        assert m.cash_cows == ["Legacy"]
        assert m.question_marks == ["Edge"]
        assert m.dogs == ["Print"]
        assert len(m.units) == 4
        # Every unit must appear in exactly one bucket.
        bucketed = m.stars + m.cash_cows + m.question_marks + m.dogs
        assert sorted(bucketed) == sorted(u.unit_name for u in m.units)
        assert len(bucketed) == len(set(bucketed))

    def test_unnamed_units_are_dropped_not_placeholdered(self) -> None:
        body = json.dumps(
            {
                "units": [
                    _unit("", "20%", "2x"),
                    {"market_growth_rate": "20%", "relative_market_share": "2x"},
                    _unit("Real", "20%", "2x"),
                ]
            }
        )
        m = self._run(body)
        assert [u.unit_name for u in m.units] == ["Real"], (
            "an unnamed unit reached the matrix — it cannot be plotted or cited"
        )

    def test_missing_recommendation_falls_back_to_framework_prescription(self) -> None:
        body = json.dumps({"units": [_unit("Legacy", "1%", "2.2x")]})
        m = self._run(body)
        assert m.units[0].recommendation == "harvest"

    def test_explicit_recommendation_is_preserved(self) -> None:
        body = json.dumps(
            {
                "units": [
                    {
                        "unit_name": "Legacy",
                        "market_growth_rate": "1%",
                        "relative_market_share": "2.2x",
                        "recommendation": "harvest then divest in FY27",
                    }
                ]
            }
        )
        m = self._run(body)
        assert m.units[0].recommendation == "harvest then divest in FY27"

    def test_non_dict_entries_are_skipped(self) -> None:
        body = json.dumps({"units": ["Cloud", None, 42, {"unit_name": "Real"}]})
        m = self._run(body)
        assert [u.unit_name for u in m.units] == ["Real"]

    def test_empty_units_yields_empty_matrix_not_a_crash(self) -> None:
        m = self._run(json.dumps({"units": [], "portfolio_balance": ""}))
        assert m.units == []
        assert m.portfolio_balance == ""

    def test_unparseable_json_yields_empty_matrix(self) -> None:
        m = self._run("not json at all")
        assert m.units == []

    def test_llm_failure_yields_empty_matrix(self) -> None:
        agent = _strategy_analyst(["BCG matrix: portfolio"])
        _stub_llm(agent, "", success=False)
        m = asyncio.run(agent._run_bcg_matrix("q", [], {}))
        assert m.units == []

    def test_wrapped_json_from_the_model_still_works_end_to_end(self) -> None:
        """Ties defect 1 to defect 2: a fenced BCG response must not be lost.

        This is the composite failure the two fixes together eliminate — a
        framework that exists, is selected, runs, and would still have come
        back empty because the provider fenced its JSON.
        """
        payload = json.dumps({"units": [_unit("Cloud", "28%", "1.5x")]})
        raw = f"Certainly:\n```json\n{payload}\n```"
        agent = _strategy_analyst(["BCG matrix: portfolio"])

        async def _fake(user_prompt: str, **kwargs: Any) -> RouterResponse:
            # Route through the real normalizer, as `_llm_complete` does.
            return RouterResponse(
                content=BaseAgent._normalize_json_content(raw),
                model="stub",
                provider=ProviderType.GOOGLE,
                tier=ModelTier.STANDARD,
                success=True,
            )

        agent._llm_complete = _fake  # type: ignore[method-assign]
        m = asyncio.run(agent._run_bcg_matrix("q", [], {}))
        assert [u.unit_name for u in m.units] == ["Cloud"]

    def test_prompt_states_the_axis_definitions(self) -> None:
        """The model must be told the axes decide, not its intuition."""
        agent = _strategy_analyst(["BCG matrix: portfolio"])
        prompts = _stub_llm(agent, json.dumps({"units": []}))
        asyncio.run(agent._run_bcg_matrix("q", [], {}))
        p = prompts[0].lower()
        for token in ("market_growth_rate", "relative_market_share", "cash cow", "question mark"):
            assert token in p, f"BCG prompt omits {token!r}"


class TestPortfolioBalanceNarrative:
    """When the model omits it, state the funding logic rather than nothing."""

    def _balance(self, **counts: list[str]) -> str:
        agent = _strategy_analyst()
        buckets = {c: [] for c in BCGCategory}
        buckets[BCGCategory.STAR] = counts.get("stars", [])
        buckets[BCGCategory.CASH_COW] = counts.get("cows", [])
        buckets[BCGCategory.QUESTION_MARK] = counts.get("marks", [])
        buckets[BCGCategory.DOG] = counts.get("dogs", [])
        return agent._describe_portfolio_balance(buckets)

    def test_empty_portfolio_says_nothing(self) -> None:
        assert self._balance() == ""

    def test_growth_units_with_no_cash_cow_is_flagged_unbalanced(self) -> None:
        out = self._balance(marks=["A", "B"], stars=["C"])
        assert "Unbalanced" in out and "cash cow" in out

    def test_cash_cows_with_no_growth_engine_is_flagged_unbalanced(self) -> None:
        out = self._balance(cows=["A", "B"])
        assert "Unbalanced" in out and "no future growth engine" in out

    def test_dog_dominated_portfolio_is_flagged(self) -> None:
        out = self._balance(dogs=["A", "B", "C"], cows=["D"], marks=["E"])
        assert "Unbalanced" in out
        assert "dog(s) dominate" in out

    def test_every_imbalance_is_reported_not_just_the_first(self) -> None:
        """Three dogs + one question mark is unbalanced in TWO ways.

        An earlier version returned on the first match, so this portfolio was
        described only as "no cash cow to fund the growth unit" — omitting
        that three quarters of it should be divested. A partial diagnosis
        misleads more than none, because the reader believes it is complete.
        """
        out = self._balance(dogs=["A", "B", "C"], marks=["D"])
        assert "no cash cow" in out, "the funding gap was not reported"
        assert "dog(s) dominate" in out, (
            "the dog-dominated finding was dropped — only the first imbalance "
            f"was reported: {out!r}"
        )

    def test_census_is_always_included_so_the_claim_is_checkable(self) -> None:
        out = self._balance(stars=["A"], cows=["B"], marks=["C"], dogs=["D"])
        for token in ("1 star(s)", "1 cash cow(s)", "1 question mark(s)", "1 dog(s)"):
            assert token in out, f"balance narrative omits the {token!r} count"

    def test_healthy_portfolio_is_described_as_balanced(self) -> None:
        out = self._balance(stars=["A"], cows=["B"], marks=["C"], dogs=["D"])
        assert out.startswith("Balanced")
        assert "Unbalanced" not in out

    def test_model_supplied_balance_wins_over_the_derived_one(self) -> None:
        agent = _strategy_analyst(["BCG matrix: portfolio"])
        _stub_llm(
            agent,
            json.dumps(
                {
                    "units": [_unit("X", "1%", "3x")],
                    "portfolio_balance": "Model's own reading of the portfolio.",
                }
            ),
        )
        m = asyncio.run(agent._run_bcg_matrix("q", [], {}))
        assert m.portfolio_balance == "Model's own reading of the portfolio."


class TestBlueOcean:
    """eliminate-reduce-raise-create, with an honesty guard."""

    def _run(self, body: str) -> BlueOceanStrategy:
        agent = _strategy_analyst(["Blue Ocean strategy: market creation"])
        _stub_llm(agent, body)
        return asyncio.run(agent._run_blue_ocean("q", [], {}))

    def test_all_four_grid_axes_are_populated(self) -> None:
        body = json.dumps(
            {
                "eliminate": ["Showroom footprint"],
                "reduce": ["SKU count"],
                "raise": ["Delivery speed"],
                "create": ["Subscription tier"],
                "is_blue_ocean_feasible": True,
                "new_market_space": "Urban renters who never owned a car.",
            }
        )
        bo = self._run(body)
        assert bo.eliminate == ["Showroom footprint"]
        assert bo.reduce == ["SKU count"]
        assert bo.raise_factors == ["Delivery speed"], (
            "the `raise` JSON key did not reach `raise_factors` — the "
            'validation_alias="raise" does not apply to keyword construction'
        )
        assert bo.create == ["Subscription tier"]
        assert bo.is_blue_ocean_feasible is True

    def test_raise_factors_key_is_also_accepted(self) -> None:
        bo = self._run(json.dumps({"raise_factors": ["Speed"]}))
        assert bo.raise_factors == ["Speed"]

    def test_blank_and_null_entries_are_dropped(self) -> None:
        """`str(None)` is the truthy 4-char string "None" — filter before coercing.

        A model emitting `["Showroom footprint", null]` would otherwise put the
        literal word "None" into the eliminate axis of a client-facing exhibit.
        """
        bo = self._run(json.dumps({"eliminate": ["Real", "", "   ", None]}))
        assert bo.eliminate == ["Real"], (
            f"junk survived into the eliminate axis: {bo.eliminate!r}"
        )
        assert "None" not in bo.eliminate

    @pytest.mark.parametrize(
        "junk",
        [None, True, False, {"a": 1}, ["nested"]],
        ids=["null", "true", "false", "dict", "list"],
    )
    def test_non_phrase_entries_are_rejected(self, junk: Any) -> None:
        """A competing factor is a phrase; anything else is model noise."""
        bo = self._run(json.dumps({"reduce": ["Genuine factor", junk]}))
        assert bo.reduce == ["Genuine factor"], (
            f"non-phrase entry {junk!r} reached the reduce axis as {bo.reduce!r}"
        )

    def test_numeric_entries_are_kept_as_strings(self) -> None:
        """Numbers are legitimate ("30" day SLA) — only structural junk is dropped."""
        bo = self._run(json.dumps({"create": [30, 2.5]}))
        assert bo.create == ["30", "2.5"]

    def test_non_list_grid_value_yields_empty_list(self) -> None:
        bo = self._run(json.dumps({"eliminate": "not a list"}))
        assert bo.eliminate == []

    def test_strategy_canvas_rows_are_coerced_to_str_maps(self) -> None:
        bo = self._run(
            json.dumps(
                {"strategy_canvas": [{"factor": "Price", "industry": 5, "proposed": 2}, "junk"]}
            )
        )
        assert bo.strategy_canvas == [{"factor": "Price", "industry": "5", "proposed": "2"}]

    def test_explicitly_infeasible_is_respected(self) -> None:
        bo = self._run(
            json.dumps(
                {
                    "is_blue_ocean_feasible": False,
                    "new_market_space": "This is a red ocean; compete on cost.",
                }
            )
        )
        assert bo.is_blue_ocean_feasible is False
        assert "red ocean" in bo.new_market_space

    def test_missing_feasibility_defaults_to_false(self) -> None:
        """Blue Ocean must be opt-in. Defaulting to True invents a market."""
        bo = self._run(json.dumps({"eliminate": ["X"]}))
        assert bo.is_blue_ocean_feasible is False


class TestRaiseAliasDoesNotSwallowTheAxis:
    """A latent schema defect found *by* the 5.1e tests, in 5.1e's own code.

    `raise_factors` carries `validation_alias="raise"` because `raise` is a
    Python keyword. In pydantic v2 a `validation_alias` **replaces** the field
    name for validation unless `populate_by_name=True`, so

        BlueOceanStrategy(raise_factors=["Delivery speed"])

    validated cleanly and returned `raise_factors == []`. No error. No warning.
    One entire quarter of the eliminate-reduce-raise-create grid — the axis
    that says what the company should be *best in the world at* — silently
    blank in the deliverable, forever.

    This is the §0.3 anti-pattern expressed in a schema rather than an
    `except` clause: a data path that fails without saying so. It was
    invisible until `_run_blue_ocean` existed to exercise it, which is
    precisely why the framework had to be implemented before it could be
    trusted.

    NC6: remove `populate_by_name=True` from BlueOceanStrategy -> this fails.
    """

    def test_field_name_construction_populates_the_axis(self) -> None:
        bo = BlueOceanStrategy(raise_factors=["Delivery speed"])
        assert bo.raise_factors == ["Delivery speed"], (
            "keyword construction by field name was silently discarded — "
            "populate_by_name is missing from BlueOceanStrategy"
        )

    def test_json_alias_still_works(self) -> None:
        """The alias must keep working: the LLM is told to emit "raise"."""
        bo = BlueOceanStrategy.model_validate({"raise": ["Delivery speed"]})
        assert bo.raise_factors == ["Delivery speed"]

    def test_both_spellings_accepted_via_model_validate(self) -> None:
        assert BlueOceanStrategy.model_validate(
            {"raise_factors": ["A"]}
        ).raise_factors == ["A"]
        assert BlueOceanStrategy.model_validate({"raise": ["B"]}).raise_factors == ["B"]

    def test_default_is_still_an_empty_list(self) -> None:
        assert BlueOceanStrategy().raise_factors == []

    def test_no_other_field_in_the_model_is_dropped_by_keyword_construction(self) -> None:
        """Generalise the check across the whole model.

        The alias trap applies to any field with a validation_alias. Rather
        than trusting that `raise_factors` is the only one, round-trip every
        field by name and assert nothing vanishes.
        """
        probe: dict[str, Any] = {
            "eliminate": ["e"],
            "reduce": ["r"],
            "raise_factors": ["ra"],
            "create": ["c"],
            "strategy_canvas": [{"factor": "f"}],
            "is_blue_ocean_feasible": True,
            "new_market_space": "space",
        }
        bo = BlueOceanStrategy(**probe)
        for field, expected in probe.items():
            assert getattr(bo, field) == expected, (
                f"field {field!r} was silently dropped by keyword construction "
                "— it likely has a validation_alias without populate_by_name"
            )

    def test_populate_by_name_is_set_on_the_model(self) -> None:
        """Structural guard so the config cannot be quietly removed."""
        assert BlueOceanStrategy.model_config.get("populate_by_name") is True, (
            "BlueOceanStrategy.model_config.populate_by_name is not True — "
            "any field with a validation_alias will silently swallow "
            "keyword construction by field name"
        )

    def test_every_aliased_field_in_the_schema_module_is_reachable_by_name(self) -> None:
        """Repo-wide guard against the same trap in a future model.

        Any model that adds a `validation_alias` without `populate_by_name`
        reintroduces this defect class. This walks every BaseModel in the
        schema module and fails on the combination.
        """
        import inspect

        from pydantic import BaseModel as _BaseModel

        from hyperion.schemas import models as models_mod

        offenders: list[str] = []
        for name, obj in inspect.getmembers(models_mod, inspect.isclass):
            if not issubclass(obj, _BaseModel) or obj is _BaseModel:
                continue
            if obj.__module__ != models_mod.__name__:
                continue
            aliased = [
                fname
                for fname, finfo in obj.model_fields.items()
                if finfo.validation_alias is not None
            ]
            if aliased and obj.model_config.get("populate_by_name") is not True:
                offenders.append(f"{name}(fields={aliased})")

        assert not offenders, (
            "model(s) declare a validation_alias without populate_by_name=True, "
            "so keyword construction by field name is silently discarded: "
            f"{offenders}"
        )


class TestBlueOceanEmptyClaimIsDowngraded:
    """NC5: "feasible" with no described market space is an empty claim.

    This is the single most seductive failure mode of the framework — it is
    trivially easy for a model to assert a blue ocean exists and impossible
    for a reader to act on the assertion without knowing which non-customer
    is being converted. Publishing it would be the deliverable equivalent of
    a chart with no axis labels.
    """

    def _run(self, body: str) -> BlueOceanStrategy:
        agent = _strategy_analyst(["Blue Ocean strategy: market creation"])
        _stub_llm(agent, body)
        return asyncio.run(agent._run_blue_ocean("q", [], {}))

    @pytest.mark.parametrize("space", ["", "   ", "\n\t"])
    def test_feasible_with_no_space_is_downgraded(self, space: str) -> None:
        bo = self._run(json.dumps({"is_blue_ocean_feasible": True, "new_market_space": space}))
        assert bo.is_blue_ocean_feasible is False, (
            "a blue ocean was reported feasible with no market space described"
        )
        assert bo.new_market_space, "the downgrade must be explained, not silent"
        assert "not feasible" in bo.new_market_space.lower()

    def test_feasible_with_missing_space_key_is_downgraded(self) -> None:
        bo = self._run(json.dumps({"is_blue_ocean_feasible": True}))
        assert bo.is_blue_ocean_feasible is False

    def test_feasible_with_a_real_space_is_not_downgraded(self) -> None:
        bo = self._run(
            json.dumps(
                {
                    "is_blue_ocean_feasible": True,
                    "new_market_space": "Rural clinics that today buy nothing.",
                }
            )
        )
        assert bo.is_blue_ocean_feasible is True
        assert bo.new_market_space == "Rural clinics that today buy nothing."

    def test_downgrade_is_auditable_in_the_output(self) -> None:
        """The reader must be able to see that a downgrade happened."""
        bo = self._run(json.dumps({"is_blue_ocean_feasible": True, "new_market_space": ""}))
        assert "claimed feasible" in bo.new_market_space.lower()


class TestCoreCompetence:
    """Grounded in VRIO, with the parallel lists kept aligned."""

    def _vrio(self) -> VRIOAssessment:
        return VRIOAssessment(
            resources=[
                VRIOResult(
                    resource="Proprietary battery chemistry",
                    is_valuable=True,
                    is_rare=True,
                    is_inimitable=True,
                    is_organized=True,
                    competitive_implication="sustained advantage",
                ),
                VRIOResult(
                    resource="Retail footprint",
                    is_valuable=True,
                    competitive_implication="competitive parity",
                ),
            ],
            sustained_advantages=["Proprietary battery chemistry"],
        )

    def _run(self, body: str, success: bool = True) -> CoreCompetence:
        agent = _strategy_analyst(["Core competence analysis: capability assessment"])
        _stub_llm(agent, body, success=success)
        return asyncio.run(agent._run_core_competence("q", self._vrio(), {}))

    def test_competencies_and_descriptions_are_populated(self) -> None:
        cc = self._run(
            json.dumps(
                {
                    "competencies": ["Cell chemistry R&D", "Vertical manufacturing"],
                    "competency_descriptions": ["20 years of patents", "Owns the whole line"],
                    "is_defensible": True,
                    "is_transferable": False,
                    "defensibility_assessment": "Patent thicket to 2038.",
                    "transferability_assessment": "Chemistry does not transfer to software.",
                }
            )
        )
        assert cc.competencies == ["Cell chemistry R&D", "Vertical manufacturing"]
        assert cc.competency_descriptions == ["20 years of patents", "Owns the whole line"]
        assert cc.is_defensible is True
        assert cc.is_transferable is False

    def test_short_description_list_is_padded_not_misaligned(self) -> None:
        """A length mismatch silently mis-attributes descriptions."""
        cc = self._run(
            json.dumps(
                {"competencies": ["A", "B", "C"], "competency_descriptions": ["desc for A"]}
            )
        )
        assert len(cc.competency_descriptions) == len(cc.competencies)
        assert cc.competency_descriptions[0] == "desc for A"
        assert cc.competency_descriptions[1:] == ["", ""]

    def test_long_description_list_is_truncated(self) -> None:
        cc = self._run(
            json.dumps({"competencies": ["A"], "competency_descriptions": ["a", "b", "c"]})
        )
        assert len(cc.competency_descriptions) == 1
        assert cc.competency_descriptions == ["a"]

    def test_no_descriptions_stays_empty_rather_than_padding_noise(self) -> None:
        cc = self._run(json.dumps({"competencies": ["A", "B"]}))
        assert cc.competency_descriptions == []

    def test_vrio_evidence_is_passed_into_the_prompt(self) -> None:
        """"Derived from VRIO if needed" must actually derive from VRIO."""
        agent = _strategy_analyst(["Core competence analysis: capability"])
        prompts = _stub_llm(agent, json.dumps({"competencies": []}))
        asyncio.run(agent._run_core_competence("q", self._vrio(), {}))
        assert "Proprietary battery chemistry" in prompts[0], (
            "the VRIO assessment was not passed to the core-competence prompt — "
            "the model is being asked to start over rather than build on it"
        )
        assert "sustained advantage" in prompts[0].lower()

    def test_prompt_states_all_three_prahalad_hamel_tests(self) -> None:
        agent = _strategy_analyst(["Core competence analysis: capability"])
        prompts = _stub_llm(agent, json.dumps({"competencies": []}))
        asyncio.run(agent._run_core_competence("q", self._vrio(), {}))
        p = prompts[0].lower()
        assert "customer value" in p
        assert "imitate" in p
        assert "leveraged across" in p or "multiple products" in p

    def test_empty_vrio_does_not_crash(self) -> None:
        agent = _strategy_analyst(["Core competence analysis: capability"])
        prompts = _stub_llm(agent, json.dumps({"competencies": []}))
        cc = asyncio.run(agent._run_core_competence("q", VRIOAssessment(), {}))
        assert cc.competencies == []
        assert "no VRIO resources" in prompts[0]

    def test_llm_failure_yields_empty_competence(self) -> None:
        cc = self._run("", success=False)
        assert cc.competencies == []

    def test_unparseable_json_yields_empty_competence(self) -> None:
        cc = self._run("I could not determine any competencies.")
        assert cc.competencies == []


# ─────────────────────────────────────────────────────────────────────────
# Structural: no framework may be advertised without an implementation
# ─────────────────────────────────────────────────────────────────────────


class TestEveryAdvertisedFrameworkIsImplemented:
    """NC3: the guard against this defect class recurring.

    The original bug was not "BCG is missing" — it was that nothing connected
    the *menu* shown to the LLM to the *methods* that exist. A ninth framework
    added to the prompt tomorrow would reproduce the defect exactly. This
    asserts the connection.
    """

    #: Framework label as advertised in `_select_frameworks` -> the attribute
    #: on StrategyAnalyst that implements it.
    ADVERTISED: dict[str, str] = {
        "Porter's Five Forces": "_run_porter_five_forces",
        "BCG growth-share matrix": "_run_bcg_matrix",
        "SWOT/TOWS": "_build_swot_tows",
        "Blue Ocean strategy": "_run_blue_ocean",
        "VRIO framework": "_run_vrio",
        "Core competence analysis": "_run_core_competence",
        "Strategic option grid": "_generate_and_score_options",
        "Game theory": "_run_game_theory",
    }

    def test_selector_advertises_exactly_eight_frameworks(self) -> None:
        src = STRATEGY_PY.read_text(encoding="utf-8")
        start = src.index("Available frameworks:")
        end = src.index("SELECT 3-5 frameworks", start)
        block = src[start:end]
        numbered = [f"{i}." for i in range(1, 10)]
        present = [n for n in numbered if n in block]
        assert present == [f"{i}." for i in range(1, 9)], (
            "the framework menu changed. If a framework was added, add its "
            "implementation to ADVERTISED in this test and wire it into run(). "
            f"Found entries: {present}"
        )

    @pytest.mark.parametrize("label,method", sorted(ADVERTISED.items()))
    def test_each_has_an_implementation(self, label: str, method: str) -> None:
        from hyperion.agents.specialists.strategy_analyst import StrategyAnalyst

        impl = getattr(StrategyAnalyst, method, None)
        assert callable(impl), (
            f"framework {label!r} is advertised to the LLM but "
            f"{method}() does not exist — the selector can promise analysis "
            "the agent cannot perform"
        )

    @pytest.mark.parametrize("label,method", sorted(ADVERTISED.items()))
    def test_each_is_called_from_run(self, label: str, method: str) -> None:
        """Existing is not enough — `run()` must actually reach it."""
        tree = ast.parse(STRATEGY_PY.read_text(encoding="utf-8"))
        run_fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
        )
        called = {
            n.func.attr
            for n in ast.walk(run_fn)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        }
        assert method in called, (
            f"framework {label!r} has an implementation ({method}) but run() "
            "never calls it — this is exactly the original defect"
        )

    def test_no_framework_field_is_hardcoded_none_in_the_result(self) -> None:
        """NC3: `bcg_matrix=None` must not come back as a literal."""
        tree = ast.parse(STRATEGY_PY.read_text(encoding="utf-8"))
        run_fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
        )
        framework_fields = {
            "porter_five_forces",
            "bcg_matrix",
            "swot_tows",
            "blue_ocean",
            "vrio_assessment",
            "core_competence",
            "strategic_option_grid",
            "game_theory",
        }
        offenders: list[str] = []
        for node in ast.walk(run_fn):
            if not isinstance(node, ast.Call):
                continue
            for kw in node.keywords:
                if (
                    kw.arg in framework_fields
                    and isinstance(kw.value, ast.Constant)
                    and kw.value.value is None
                ):
                    offenders.append(str(kw.arg))
        assert not offenders, (
            "framework field(s) hardcoded to None in run(): "
            f"{offenders} — StrategyAnalysis would advertise a framework in "
            "frameworks_selected while carrying a permanently-null field"
        )


class TestSelectedFrameworksActuallyRun:
    """Selection must gate execution in both directions.

    Running them unconditionally would be the opposite error: §4.4 is explicit
    that the Strategy Analyst "selects the framework that actually illuminates
    the specific question" rather than applying all eight. A deck containing a
    BCG matrix for a non-portfolio question is padding.
    """

    def _dispatch_calls(self, selected: list[str]) -> set[str]:
        """Record which conditional frameworks would run for a selection."""
        agent = _strategy_analyst(selected)
        ran: set[str] = set()

        for name, tokens in (
            ("bcg", agent._BCG_TOKENS),
            ("blue_ocean", agent._BLUE_OCEAN_TOKENS),
            ("core_competence", agent._CORE_COMPETENCE_TOKENS),
        ):
            if agent._framework_selected(tokens):
                ran.add(name)
        return ran

    def test_portfolio_question_runs_bcg_only(self) -> None:
        assert self._dispatch_calls(
            ["BCG growth-share matrix: portfolio mix", "Porter's Five Forces: attractiveness"]
        ) == {"bcg"}

    def test_market_creation_question_runs_blue_ocean_only(self) -> None:
        assert self._dispatch_calls(["Blue Ocean strategy: new category"]) == {"blue_ocean"}

    def test_capability_question_runs_core_competence_only(self) -> None:
        assert self._dispatch_calls(
            ["Core competence analysis: capabilities", "VRIO framework: resources"]
        ) == {"core_competence"}

    def test_none_selected_runs_none(self) -> None:
        assert self._dispatch_calls(["Porter's Five Forces: x", "SWOT/TOWS: y"]) == set()

    def test_all_three_selected_runs_all_three(self) -> None:
        assert self._dispatch_calls(
            [
                "BCG matrix: portfolio",
                "Blue Ocean strategy: creation",
                "Core competence analysis: capability",
            ]
        ) == {"bcg", "blue_ocean", "core_competence"}

    def test_run_guards_each_conditional_framework_with_a_selection_check(self) -> None:
        """Structural: the dispatch must be inside an `if _framework_selected`."""
        tree = ast.parse(STRATEGY_PY.read_text(encoding="utf-8"))
        run_fn = next(
            n
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "run"
        )
        for method in ("_run_bcg_matrix", "_run_blue_ocean", "_run_core_competence"):
            guarded = False
            for node in ast.walk(run_fn):
                if not isinstance(node, ast.If):
                    continue
                if "_framework_selected" not in ast.dump(node.test):
                    continue
                body = ast.dump(ast.Module(body=node.body, type_ignores=[]))
                if method in body:
                    guarded = True
                    break
            assert guarded, (
                f"{method} is called from run() but not gated on "
                "_framework_selected — it would run for every question, "
                "padding the deck with a framework nobody selected"
            )
