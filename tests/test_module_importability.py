"""Every shipped module must compile, and every safe module must import.

Why this file exists
--------------------
`hyperion/eval/ci_gate.py` shipped for its entire life as a `SyntaxError`: its
shebang and its opening docstring quotes were fused onto one line
(`#!/usr/bin/env python3\"\"\"`), so the docstring body parsed as code and the
first `—` in it raised `SyntaxError: invalid character '—' (U+2014)`.

The failure survived indefinitely for one reason: **nothing imported it.** The
package `__init__` re-exports only `harness`, no test referenced it, and the
audit's own test-count metric could not see it. It was the CI regression gate —
the file whose job is to catch regressions — and it was the single file in the
tree that could not run.

A test suite that only covers what is imported cannot detect an unimportable
module. So this module does not test behaviour; it tests *existence of a valid
parse* across the whole shipped package, which is the only assertion that closes
the gap. It is deliberately cheap (compile-only for the sweep) so it can afford
to be exhaustive.

The sibling control for the *other* invisible-failure class — silent exception
swallowing in the retrieval path — lives in `tests/test_no_silent_failures.py`.
"""

from __future__ import annotations

import ast
import importlib
import py_compile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_ROOT = REPO_ROOT / "hyperion"


def _all_package_files() -> list[Path]:
    return sorted(p for p in PACKAGE_ROOT.rglob("*.py") if "__pycache__" not in p.parts)


def _module_name(path: Path) -> str:
    rel = path.relative_to(REPO_ROOT).with_suffix("")
    parts = list(rel.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


ALL_FILES = _all_package_files()


class TestEveryShippedModuleCompiles:
    """The regression lock for the `ci_gate.py` SyntaxError."""

    def test_the_package_is_not_empty(self) -> None:
        # Guard against the sweep silently passing because the glob broke: a
        # zero-file parametrization is a green test that checks nothing.
        assert len(ALL_FILES) > 50, f"expected the full package, found {len(ALL_FILES)} files"

    @pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: str(p.relative_to(PACKAGE_ROOT)))
    def test_module_compiles(self, path: Path, tmp_path: Path) -> None:
        """Byte-compile every file. Catches SyntaxError with no import cost."""
        try:
            py_compile.compile(
                str(path),
                cfile=str(tmp_path / "out.pyc"),
                doraise=True,
            )
        except py_compile.PyCompileError as exc:  # pragma: no cover - failure path
            pytest.fail(f"{path.relative_to(REPO_ROOT)} does not compile:\n{exc}")

    def test_ci_gate_specifically_compiles_and_imports(self) -> None:
        """The exact module that was broken, named explicitly.

        The parametrized sweep already covers it, but naming it means the
        regression appears in the report as itself rather than as one id among
        130 — and it additionally asserts a real import, not just a parse.
        """
        module = importlib.import_module("hyperion.eval.ci_gate")
        assert hasattr(module, "run_gate")
        assert hasattr(module, "main")
        assert module.EXIT_PASS == 0
        assert module.EXIT_REGRESSION == 1
        assert module.EXIT_HARNESS_ERROR == 2

    def test_ci_gate_parser_accepts_its_documented_flags(self) -> None:
        """The docstring promises three flags; assert the parser honours them."""
        from hyperion.eval.ci_gate import build_parser

        args = build_parser().parse_args(["--report", "r.json", "--update-baseline"])
        assert args.report == "r.json"
        assert args.update_baseline is True

        default = build_parser().parse_args([])
        assert default.report is None
        assert default.update_baseline is False

    def test_ci_gate_docstring_is_a_real_docstring(self) -> None:
        """The precise shape of the original bug: docstring parsed as code.

        Asserting `ast.get_docstring` is non-None proves the shebang/docstring
        fusion is gone, in a way that survives future edits to the text.
        """
        source = (PACKAGE_ROOT / "eval" / "ci_gate.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        doc = ast.get_docstring(tree)
        assert doc is not None, "ci_gate.py has no module docstring — is it fused to the shebang?"
        assert "Regression Gate" in doc

        lines = source.splitlines()
        assert lines[0] == "#!/usr/bin/env python3", "shebang must occupy its own line"
        assert '"""' not in lines[0], "docstring quotes must not share the shebang line"


class TestNoShebangDocstringFusionAnywhere:
    """Generalise the bug: no file may fuse a shebang with anything else."""

    @pytest.mark.parametrize("path", ALL_FILES, ids=lambda p: str(p.relative_to(PACKAGE_ROOT)))
    def test_shebang_line_is_clean(self, path: Path) -> None:
        first = path.read_text(encoding="utf-8").splitlines()[:1]
        if not first or not first[0].startswith("#!"):
            return
        assert '"""' not in first[0] and "'''" not in first[0], (
            f"{path.relative_to(REPO_ROOT)} fuses its shebang with a docstring — "
            "the exact defect that made ci_gate.py unparseable"
        )
