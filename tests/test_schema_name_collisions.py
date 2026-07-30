"""
Tests for schema-name uniqueness in `hyperion/schemas/models.py`, and for the
one live outage that a duplicate name actually caused (Phase 5, fix 5.1b —
HYPERION_DEEP_AUDIT_2026-07-27.md §10, ruff rule F811).

WHY THIS FILE EXISTS
────────────────────
`models.py` is ~2,300 lines and defines the entire output contract for all 20
agents. Two *entirely different* pydantic models in it were both named
`HorizonScanItem`:

  * line ~702 — Agent 9 (Regulatory Analyst) pending-regulation item, keyed on
    ``regulation_name`` / ``jurisdiction`` / ``status`` / ``timeline``.
  * line ~1267 — Agent 13 (Innovation Scout) horizon signal, keyed on
    ``horizon`` / ``signal`` / ``description`` / ``impact``.

Python has no notion of "overloaded class"; the second `class` statement simply
rebinds the module-global name. So the Agent 9 model was **unreachable** — the
name `HorizonScanItem` resolved to Agent 13's model everywhere, including inside
`regulatory_analyst.py`, which imported it by that name and constructed it with
Agent 9's keyword arguments.

Every such construction raised::

    ValidationError: 2 validation errors for HorizonScanItem
    horizon   Field required
    signal    Field required

and it was invisible, because the parse block ended in::

    except (json.JSONDecodeError, ValueError, TypeError):
        pass

`pydantic.ValidationError` subclasses `ValueError`. So *every* horizon item was
rejected, *every* rejection was swallowed, and nothing was logged anywhere.
`RegulatoryAnalysis.horizon_scan` was therefore structurally guaranteed to be
`[]` on every engagement HYPERION has ever run — while the LLM call that
produced the items was still paid for in full. This is the audit's signature
defect class: the "regulatory horizon scanning" capability was advertised in the
agent's docstring, billed on every run, and shipped as an empty list.

This is a two-layer test:

  1. **The general invariant** — no two top-level classes in `models.py` may
     share a name. This is what stops the *next* collision, which is the point;
     a fix that only renames one class leaves the door open.
  2. **The specific outage** — `RegulatoryHorizonItem` constructs from Agent 9's
     field names, `RegulatoryAnalysis.horizon_scan` is typed to it, and
     `_scan_horizon` no longer swallows failures silently.

NEGATIVE CONTROL (performed, see audit §10 5.1b):
Re-adding a second `class RegulatoryHorizonItem` to `models.py` fails
`test_no_duplicate_class_names_in_models`; renaming the field type back to
Agent 13's model fails `test_regulatory_analysis_horizon_scan_is_typed_to_the_regulatory_item`;
restoring the bare `except ... : pass` fails both
`test_scan_horizon_logs_when_every_item_fails_construction` and
`test_scan_horizon_has_no_silent_except_pass`.
"""

from __future__ import annotations

import ast
import asyncio
import collections
import inspect
import logging
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from pydantic import BaseModel, ValidationError

import hyperion.schemas.models as models_mod
from hyperion.agents.specialists.regulatory_analyst import RegulatoryAnalyst
from hyperion.config import ModelTier
from hyperion.router.providers.base import ProviderType, RouterResponse
from hyperion.schemas.models import (
    ConfidenceLevel,
    HorizonScanItem,
    RegulatoryAnalysis,
    RegulatoryHorizonItem,
)

MODELS_PATH = Path(models_mod.__file__)


def _models_ast() -> ast.Module:
    return ast.parse(MODELS_PATH.read_text(encoding="utf-8"))


def _router_response(content: str) -> RouterResponse:
    """A minimal successful RouterResponse carrying `content`."""
    return RouterResponse(
        content=content,
        model="test-model",
        provider=ProviderType.GOOGLE,
        tier=ModelTier.STANDARD,
        success=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Layer 1: the general invariant — names in models.py must be unique
# ─────────────────────────────────────────────────────────────────────────────


class TestNoDuplicateClassNames:
    def test_no_duplicate_class_names_in_models(self):
        """No two top-level classes in models.py may share a name.

        This is the invariant, not the rename. A duplicate `class` statement
        silently makes the earlier definition unreachable — there is no error,
        no warning, and (as `HorizonScanItem` proved) the resulting outage can
        survive indefinitely because the failure surfaces as a `ValidationError`
        at a call site that catches `ValueError`.
        """
        tree = _models_ast()
        names = [
            node.name for node in tree.body if isinstance(node, ast.ClassDef)
        ]
        duplicates = {
            name: count
            for name, count in collections.Counter(names).items()
            if count > 1
        }
        assert duplicates == {}, (
            "Duplicate top-level class names in hyperion/schemas/models.py: "
            f"{duplicates}. Python binds the name to the LAST definition, so "
            "every earlier class of that name is unreachable — including from "
            "agents that import it. Rename one of them."
        )

    def test_horizon_scan_item_names_are_distinct_classes(self):
        """Both horizon models must still exist, under distinct names.

        A tempting non-fix is to delete one of the two colliding classes. Both
        are real, in-use output contracts for different agents (9 and 13), so
        the fix has to be a rename — this asserts nothing was lost.
        """
        assert RegulatoryHorizonItem is not HorizonScanItem
        assert issubclass(RegulatoryHorizonItem, BaseModel)
        assert issubclass(HorizonScanItem, BaseModel)

    def test_the_two_horizon_models_have_genuinely_different_shapes(self):
        """Documents *why* these could never have been one class.

        If their fields overlapped, a single shared model would have been the
        right fix. They don't overlap at all on required fields, which is the
        proof that the collision was an accident rather than a merge.
        """
        reg_required = {
            name
            for name, f in RegulatoryHorizonItem.model_fields.items()
            if f.is_required()
        }
        innov_required = {
            name
            for name, f in HorizonScanItem.model_fields.items()
            if f.is_required()
        }
        # Agent 9 speaks of regulations; Agent 13 speaks of signals.
        assert "regulation_name" in reg_required
        assert "horizon" in innov_required or "signal" in innov_required
        assert reg_required != innov_required

    def test_all_model_names_exported_resolve_to_a_class(self):
        """Every ClassDef name in models.py is importable and is that class.

        Catches the subtler variant of the same bug: a class shadowed by a
        later *non-class* assignment (alias, TypeAlias, constant) to the same
        name, which `test_no_duplicate_class_names_in_models` would not see.
        """
        tree = _models_ast()
        for node in tree.body:
            if not isinstance(node, ast.ClassDef):
                continue
            resolved = getattr(models_mod, node.name, None)
            assert resolved is not None, f"{node.name} not importable"
            assert inspect.isclass(resolved), (
                f"models.{node.name} is defined as a class but resolves to "
                f"{type(resolved).__name__} — something rebound the name."
            )
            assert resolved.__name__ == node.name


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2a: the schema half of the outage
# ─────────────────────────────────────────────────────────────────────────────


class TestRegulatoryHorizonItemIsReachable:
    def test_constructs_from_agent_9_field_names(self):
        """The exact construction that raised ValidationError before the fix.

        These are the keyword arguments `_scan_horizon` has always passed. If
        this test ever fails with "horizon Field required / signal Field
        required", the collision has been reintroduced.
        """
        item = RegulatoryHorizonItem(
            regulation_name="EU AI Act — high-risk obligations",
            jurisdiction="EU",
            status="proposed",
            timeline="2027",
            probability="high",
            potential_impact="Conformity assessment required before launch.",
            recommended_action="Begin gap analysis now.",
            sources=[],
        )
        assert item.regulation_name.startswith("EU AI Act")
        assert item.jurisdiction == "EU"

    def test_agent_13_field_names_are_rejected_by_the_regulatory_model(self):
        """Confirms the two models are not silently interchangeable.

        The mirror of the test above: if `RegulatoryHorizonItem` accepted
        Agent 13's fields, the models would be structurally identical and the
        rename would be cosmetic.
        """
        with pytest.raises(ValidationError):
            RegulatoryHorizonItem(  # type: ignore[call-arg]
                horizon="3-5 years",
                signal="Neuromorphic compute",
            )

    def test_regulatory_analysis_horizon_scan_is_typed_to_the_regulatory_item(self):
        """`RegulatoryAnalysis.horizon_scan` must hold Agent 9's model.

        Pointing at Agent 13's model is exactly what the collision did
        implicitly, and it is what makes every append fail.
        """
        annotation = RegulatoryAnalysis.model_fields["horizon_scan"].annotation
        assert annotation == list[RegulatoryHorizonItem], (
            f"horizon_scan is typed {annotation!r}; expected "
            "list[RegulatoryHorizonItem]"
        )

    def test_regulatory_analysis_accepts_a_populated_horizon_scan(self):
        """End-to-end on the schema: a populated horizon_scan survives.

        Before the fix this raised, so the only value the field could ever
        legally hold in production was `[]`.
        """
        item = RegulatoryHorizonItem(
            regulation_name="CSRD assurance phase-in",
            jurisdiction="EU",
            status="adopted",
            timeline="2026",
            probability="high",
            potential_impact="Limited assurance on sustainability reporting.",
            recommended_action="Select an assurance provider.",
            sources=[],
        )
        analysis = RegulatoryAnalysis(
            confidence=ConfidenceLevel.MEDIUM, horizon_scan=[item]
        )
        assert len(analysis.horizon_scan) == 1
        assert analysis.horizon_scan[0].regulation_name.startswith("CSRD")
        assert isinstance(analysis.horizon_scan[0], RegulatoryHorizonItem)

    def test_regulatory_analyst_imports_the_renamed_model(self):
        """The agent must import `RegulatoryHorizonItem`, not the old name.

        A rename in models.py alone would leave `regulatory_analyst.py`
        importing `HorizonScanItem` — i.e. Agent 13's model — reproducing the
        original bug with none of the F811 evidence to find it by.
        """
        import hyperion.agents.specialists.regulatory_analyst as mod

        assert hasattr(mod, "RegulatoryHorizonItem")
        assert mod.RegulatoryHorizonItem is RegulatoryHorizonItem
        assert not hasattr(mod, "HorizonScanItem"), (
            "regulatory_analyst still imports HorizonScanItem — that is "
            "Agent 13's innovation-signal model, not Agent 9's."
        )


# ─────────────────────────────────────────────────────────────────────────────
# Layer 2b: the silence half of the outage
# ─────────────────────────────────────────────────────────────────────────────


class TestScanHorizonNoLongerFailsSilently:
    """The collision was survivable for years only because of the `except: pass`.

    The rename fixes today's outage; removing the silence is what makes the
    *next* schema drift in this method a loud, one-log-line diagnosis instead
    of a permanently empty field.
    """

    def _agent_returning(self, content: str) -> RegulatoryAnalyst:
        agent = RegulatoryAnalyst()
        agent._llm_complete = AsyncMock(  # type: ignore[method-assign]
            return_value=_router_response(content)
        )
        agent._sources = []
        return agent

    def _scan(self, agent: RegulatoryAnalyst) -> list[RegulatoryHorizonItem]:
        return asyncio.run(
            agent._scan_horizon(
                question="Should we enter the EU market?",
                search_results=[],
                historical_snapshots=[],
                jurisdictions=["EU"],
                context={},
            )
        )

    def test_well_formed_items_are_returned(self):
        """The happy path that production never once reached."""
        agent = self._agent_returning(
            '{"horizon_items": [{"regulation_name": "DSA tier-2 duties",'
            ' "jurisdiction": "EU", "status": "proposed", "timeline": "2027",'
            ' "probability": "medium", "potential_impact": "Audit duties.",'
            ' "recommended_action": "Scope an audit."}]}'
        )
        items = self._scan(agent)
        assert len(items) == 1
        assert items[0].regulation_name == "DSA tier-2 duties"
        assert isinstance(items[0], RegulatoryHorizonItem)

    def test_one_malformed_item_does_not_discard_the_good_ones(self):
        """Per-item construction, not batch-abort.

        The old code built the whole list inside one `try`, so a single bad
        element threw away every item — including the ones that parsed. With
        the batch also being silently swallowed, one bad item was
        indistinguishable from a total model failure.
        """
        agent = self._agent_returning(
            '{"horizon_items": ['
            '  "not-a-dict",'
            '  {"regulation_name": "Good one", "jurisdiction": "EU",'
            '   "status": "proposed", "timeline": "2027",'
            '   "probability": "low", "potential_impact": "x",'
            '   "recommended_action": "y"}'
            ']}'
        )
        items = self._scan(agent)
        assert [i.regulation_name for i in items] == ["Good one"]

    def test_unparseable_json_is_logged_not_swallowed(self, caplog):
        """Bad JSON must leave a trace at WARNING or above."""
        agent = self._agent_returning("this is not json at all")
        with caplog.at_level(logging.WARNING):
            items = self._scan(agent)
        assert items == []
        assert caplog.records, "unparseable JSON produced no log record"
        assert any(
            "horizon" in r.getMessage().lower() for r in caplog.records
        ), f"no horizon-scan diagnostic logged; got {[r.getMessage() for r in caplog.records]}"

    def test_scan_horizon_logs_when_every_item_fails_construction(self, caplog):
        """The precise signature of the original outage must now be an ERROR.

        "The model gave us N items and we produced zero" is the observable
        symptom of a schema mismatch. It was logged nowhere. Now it is an
        ERROR, because it is never a normal outcome.
        """
        agent = self._agent_returning(
            '{"horizon_items": [{"status": "proposed"}, {"status": "draft"}]}'
        )
        with caplog.at_level(logging.DEBUG):
            items = self._scan(agent)

        assert items == [], "unnameable items must not construct"
        errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
        assert errors, (
            "2 items offered, 0 constructed — and nothing was logged at ERROR. "
            "That is exactly the silence that hid the HorizonScanItem "
            "collision for the project's entire life."
        )
        assert any("0 constructed" in r.getMessage() for r in errors)

    def test_unnamed_regulation_is_dropped_not_labelled_unknown(self, caplog):
        """An item with no `regulation_name` must be dropped, never defaulted.

        Found by this very test file: the parse block defaulted both
        `regulation_name` and `jurisdiction` to the literal string "Unknown".
        Two independent defects in one line:

          * "Unknown" is a template-leak token counted by
            `tools/audit_render_probe.py`; §11 criterion 11 of the audit
            requires the count to be zero. A nameless item would render as a
            regulation literally called "Unknown" in a compliance deliverable.
          * Naming an unnameable regulation *fabricates a finding*. The model
            did not identify a regulation; emitting a row asserts that it did.

        Dropping is the only honest option, and it must be logged.
        """
        agent = self._agent_returning(
            '{"horizon_items": ['
            '  {"jurisdiction": "EU", "status": "proposed"},'
            '  {"regulation_name": "Real one", "jurisdiction": "US",'
            '   "status": "proposed", "timeline": "2027",'
            '   "probability": "high", "potential_impact": "x",'
            '   "recommended_action": "y"}'
            ']}'
        )
        with caplog.at_level(logging.DEBUG):
            items = self._scan(agent)

        assert [i.regulation_name for i in items] == ["Real one"], (
            "the unnamed item was kept — it must be dropped, not renamed"
        )
        assert not any(i.regulation_name == "Unknown" for i in items)
        assert any(
            r.levelno >= logging.WARNING and "regulation_name" in r.getMessage()
            for r in caplog.records
        ), "dropping an item must be logged at WARNING or above"

    def test_no_horizon_item_field_leaks_a_probe_token(self):
        """No constructed item may carry a render-probe leak token.

        `Unknown` is the token that actually leaked here, but this asserts the
        general property against every string field, so a future default of
        `None`/`{'`/`{{page}}` is caught by the same test.
        """
        leak_tokens = ("Unknown", "{'", "=None", "{{page}}")
        agent = self._agent_returning(
            '{"horizon_items": [{"regulation_name": "GDPR successor",'
            ' "status": "proposed"}]}'
        )
        items = self._scan(agent)
        assert len(items) == 1, "a named item must survive with fields defaulted"
        for name in type(items[0]).model_fields:
            value = getattr(items[0], name)
            if isinstance(value, str):
                for token in leak_tokens:
                    assert token not in value, (
                        f"field {name!r} == {value!r} contains probe leak "
                        f"token {token!r}"
                    )

    def test_empty_item_list_does_not_log_an_error(self, caplog):
        """Discipline check: 'the model found nothing' is not an error.

        A fix that logs ERROR on every empty result is noise, and noise is how
        the next real signal gets ignored. Zero-offered must stay quiet.
        """
        agent = self._agent_returning('{"horizon_items": []}')
        with caplog.at_level(logging.DEBUG):
            items = self._scan(agent)
        assert items == []
        assert not [r for r in caplog.records if r.levelno >= logging.ERROR]

    def test_scan_horizon_has_no_silent_except_pass(self):
        """Static guard: no `except ...: pass` anywhere in `_scan_horizon`.

        Behavioural tests above can be satisfied by logging in one branch while
        another still swallows. This walks the AST of the method itself.
        """
        tree = ast.parse(
            Path(
                inspect.getfile(RegulatoryAnalyst)
            ).read_text(encoding="utf-8")
        )
        target = None
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "_scan_horizon":
                target = node
                break
        assert target is not None, "_scan_horizon not found"

        for handler in [n for n in ast.walk(target) if isinstance(n, ast.ExceptHandler)]:
            body = [s for s in handler.body if not isinstance(s, ast.Pass)]
            assert body, (
                f"_scan_horizon has an `except ...: pass` at line "
                f"{handler.lineno}. Every handler in this method must log — "
                "the swallowed ValidationError is the whole reason this fix "
                "exists."
            )

    def test_scan_horizon_distinguishes_schema_errors_from_bad_model_output(self):
        """A `ValidationError` branch must exist and be separate.

        Lumping `ValidationError` in with `ValueError`/`TypeError` is what made
        our own bug read as "the LLM returned garbage". The distinction is the
        diagnostic: one is our defect, the other is theirs.
        """
        src = inspect.getsource(RegulatoryAnalyst._scan_horizon)
        assert "ValidationError" in src, (
            "_scan_horizon does not mention ValidationError — a schema "
            "mismatch will again be misreported as bad model output."
        )
        tree = ast.parse(src.lstrip())
        names: list[str] = []
        for handler in [n for n in ast.walk(tree) if isinstance(n, ast.ExceptHandler)]:
            if isinstance(handler.type, ast.Name):
                names.append(handler.type.id)
            elif isinstance(handler.type, ast.Tuple):
                names.extend(
                    e.id for e in handler.type.elts if isinstance(e, ast.Name)
                )
        assert "ValidationError" in names, (
            f"no dedicated `except ValidationError` handler; handlers caught {names}"
        )
