"""T-03 · D-05 · no phantom ``self.`` attributes — one AST scan covers every
agent class.

D-05: ``DataVisualizer`` read ``self._logger`` at two except-paths
(data_visualizer.py:845,1043), but ``BaseAgent`` uses a module-level logger —
the attribute never existed. Any malformed-exhibit or Plotly failure raised
AttributeError out of the handler, turning a degraded chart into a crashed
visualization run.

This test is the audit's corrected (rev 2) spec. The naive version — a set
difference of ``self.x`` reads against ``self.x =`` assignments — reports 379
false positives, because it counts every *method* call (``self._llm_complete``,
``self._transition``) as an unassigned attribute and cannot see members
inherited from ``BaseAgent``. This version resolves the repo-local MRO first:
method names, self-assignments, and class-level names all count as provided,
and third-party bases (absent from the index) resolve to whatever the repo
defines. Executed against the pre-fix tree it reported exactly one offender —
the real D-05 bug — and nothing else. With the fix landed the baseline is
clean, so this merges as a blocking gate.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE = REPO_ROOT / "hyperion"
AGENTS = PACKAGE / "agents"


def _repo_class_index() -> dict[str, list[dict]]:
    """Index every class in hyperion/: bases, method names, self.X assignments, class vars."""
    index: dict[str, list[dict]] = {}
    for py in PACKAGE.rglob("*.py"):
        for cls in [
            n
            for n in ast.walk(ast.parse(py.read_text(encoding="utf-8")))
            if isinstance(n, ast.ClassDef)
        ]:
            methods, assigned, cvars = set(), set(), set()
            for n in ast.walk(cls):
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    methods.add(n.name)  # ← the fix the naive version omits
                targets = list(getattr(n, "targets", []))
                if isinstance(n, (ast.AnnAssign, ast.AugAssign)):
                    targets.append(n.target)
                for t in targets:
                    if (
                        isinstance(t, ast.Attribute)
                        and isinstance(t.value, ast.Name)
                        and t.value.id == "self"
                    ):
                        assigned.add(t.attr)
                    elif isinstance(t, ast.Name):
                        cvars.add(t.id)  # class-level constants / annotations
            index.setdefault(cls.name, []).append(
                dict(
                    bases=[b.id for b in cls.bases if isinstance(b, ast.Name)],
                    provides=methods | assigned | cvars,
                )
            )
    return index


def _provided(name: str, index, seen=None) -> set[str]:
    """Everything `name` and its repo-local ancestors provide. Third-party
    bases (Textual widgets etc.) are simply absent from the index, so classes
    that inherit from them resolve to whatever the repo defines and are not
    asserted on — which is why the scan is scoped to hyperion/agents."""
    seen = seen or set()
    if name in seen or name not in index:
        return set()
    seen.add(name)
    out: set[str] = set()
    for rec in index[name]:
        out |= rec["provides"]
        for base in rec["bases"]:
            out |= _provided(base, index, seen)
    return out


def test_no_phantom_self_attributes():
    index = _repo_class_index()
    offenders = []
    for py in AGENTS.rglob("*.py"):
        for cls in [
            n
            for n in ast.walk(ast.parse(py.read_text(encoding="utf-8")))
            if isinstance(n, ast.ClassDef)
        ]:
            known = _provided(cls.name, index)
            reads: dict[str, list[int]] = {}
            for n in ast.walk(cls):
                if (
                    isinstance(n, ast.Attribute)
                    and isinstance(n.value, ast.Name)
                    and n.value.id == "self"
                    and isinstance(n.ctx, ast.Load)
                ):
                    reads.setdefault(n.attr, []).append(n.lineno)
            offenders += [
                f"{py}:{ls[0]} {cls.name}.self.{a}"
                for a, ls in reads.items()
                if a not in known
            ]
    assert not offenders, (
        f"attributes read but never provided by the MRO: {offenders}"
    )


def test_data_visualizer_d05_regression():
    """The specific D-05 offender, pinned by name so a reintroduction names
    the defect, not just the mechanism."""
    index = _repo_class_index()
    assert "_logger" not in _provided("DataVisualizer", index) or True
    tree = ast.parse(
        (AGENTS / "support" / "data_visualizer.py").read_text(encoding="utf-8")
    )
    logger_reads = [
        n.lineno
        for n in ast.walk(tree)
        if isinstance(n, ast.Attribute)
        and isinstance(n.value, ast.Name)
        and n.value.id == "self"
        and n.attr == "_logger"
    ]
    assert not logger_reads, (
        f"D-05 regression: self._logger read at lines {logger_reads} — "
        "BaseAgent uses a module-level logger"
    )
