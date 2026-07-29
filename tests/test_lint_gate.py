"""Tests for 5.1f — the ruff + mypy static-analysis gate (the process fix for
the original P0).

Live proof of the defect before the fix: `.pre-commit-config.yaml` did not
exist, no CI workflow existed, `ci_gate.py` had no lint mode, and pyproject
had no ignore list — lint findings accumulated with zero enforcement, and
5.1b–5.1e repeatedly proved those findings were live outages, not style noise.

These tests cover:
  1. The gate runs green on the current tree (ruff + mypy both invoked).
  2. NC1: a deliberately reintroduced F401 in the scanned tree makes the gate
     FAIL — a gate that can't catch the finding class that caused the P0 is
     not a gate.
  3. NC2: a failing tool yields EXIT_REGRESSION, not a pass.
  4. NC3: a missing tool yields EXIT_HARNESS_ERROR — the gate must never
     silently pass because ruff/mypy was absent.
  5. Structural guards: ci_gate routes --lint before the quality harness, the
     pre-commit config pins both tools, and pyproject's gate config cannot be
     weakened without failing this suite.
"""

from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

import pytest
import yaml

from hyperion.eval import ci_gate

REPO = Path(__file__).resolve().parent.parent


class TestLintGateGreen:
    def test_gate_passes_on_current_tree(self):
        """The full gate — real ruff + mypy — must be green after 5.1f."""
        pytest.importorskip("mypy")
        rc = ci_gate.run_lint()
        assert rc == ci_gate.EXIT_PASS, (
            "lint gate is red on the tree it was committed against — either a "
            "new finding landed or the quarantine was widened silently"
        )


class TestLintGateNegativeControls:
    def test_nc1_reintroduced_f401_fails_the_gate(self):
        """Drop an unused import into the scanned tree; the gate MUST catch it.

        F401 unused-import is precisely the class that hid 5.1b's dead horizon
        scan and 5.1e's unimplemented frameworks. If this control ever stops
        failing, the gate is decorative.
        """
        pytest.importorskip("mypy")
        probe = REPO / "hyperion" / "_lint_gate_nc_probe.py"
        # NOTE: no comment containing "noqa" anywhere near this import — ruff
        # treats any comment starting with that token as a suppression
        # directive, which is how the first version of this probe silenced
        # its own finding and produced a false-pass control.
        probe.write_text(
            '"""NC probe for test_lint_gate — deleted by the test."""\n'
            "import os\n"
            "\n"
            "NC_MARKER = True\n"
        )
        try:
            rc = ci_gate.run_lint()
        finally:
            probe.unlink()
        assert rc == ci_gate.EXIT_REGRESSION

    def test_nc2_failing_tool_yields_regression_not_pass(self, monkeypatch):
        class _Fail:
            returncode = 1
            stdout = "E999 synthetic finding"
            stderr = ""

        monkeypatch.setattr(ci_gate.shutil, "which", lambda _c: "/usr/bin/x")
        monkeypatch.setattr(
            ci_gate.subprocess, "run", lambda *a, **k: _Fail()
        )
        assert ci_gate.run_lint() == ci_gate.EXIT_REGRESSION

    def test_nc3_missing_tool_yields_harness_error_not_pass(self, monkeypatch):
        """A gate that 'passes' when ruff is absent is the defect this fixes."""
        monkeypatch.setattr(ci_gate.shutil, "which", lambda _c: None)
        assert ci_gate.run_lint() == ci_gate.EXIT_HARNESS_ERROR


class TestStructuralGuards:
    def test_run_gate_routes_lint_before_quality_harness(self):
        """--lint must short-circuit before the eval harness import, so the
        lint gate works even where the LLM stack is not installed."""
        src = (REPO / "hyperion" / "eval" / "ci_gate.py").read_text()
        tree = ast.parse(src)
        run_gate = next(
            n for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name == "run_gate"
        )
        # body[0] is the docstring; the first EXECUTABLE statement must be the
        # lint route so --lint works even where the LLM stack is not installed.
        body = run_gate.body
        if body and isinstance(body[0], ast.Expr) and isinstance(
            body[0].value, ast.Constant
        ):
            body = body[1:]
        first_stmt = ast.get_source_segment(src, body[0]) or ""
        assert "lint" in first_stmt, (
            "run_gate's first executable statement must be the --lint route, "
            "before the harness import"
        )

    def test_pre_commit_config_exists_and_pins_both_tools(self):
        cfg_text = (REPO / ".pre-commit-config.yaml").read_text()
        assert "ruff" in cfg_text, "pre-commit config lost the ruff hook"
        assert "mypy" in cfg_text, "pre-commit config lost the mypy hook"
        # Structural check, not a text scan: the config's comments document
        # why auto-fixing is forbidden and may mention the flag by name.
        cfg = yaml.safe_load(cfg_text)
        hooks = [
            hook for repo in cfg.get("repos", []) for hook in repo.get("hooks", [])
        ]
        assert hooks, "no hooks parsed — pre-commit config structure changed"
        for hook in hooks:
            hook_id = hook.get("id", "<unknown>")
            assert "--fix" not in hook.get("args", []), (
                f"hook {hook_id} auto-fixes: 5.1e proved ruff fixes can be "
                "semantics-changing (UP042, TC00x)"
            )
            assert "--fix" not in str(hook.get("entry", "")).split()

    def test_pyproject_gate_config_not_weakened(self):
        cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
        ruff = cfg["tool"]["ruff"]
        selected = set(ruff["lint"]["select"])
        # The families that caught live defects in 5.1b–5.1e must stay on.
        assert {"E", "F", "I", "B", "SIM"} <= selected
        assert cfg["tool"]["mypy"]["strict"] is True
        # Documented semantics-changing families stay off — re-enabling them
        # without the triage work re-creates the UP042/TC00x breakage.
        ignored = set(ruff["lint"]["ignore"])
        assert {"UP042", "TC001", "TC002", "TC003", "SIM112"} <= ignored

    def test_mypy_quarantine_shrinks_never_grows(self):
        """The staged allowlist is a burn-down list: 67 modules at the 5.1f
        baseline. Any PR that adds to it without removing errors first fails."""
        cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
        overrides = cfg["tool"]["mypy"]["overrides"]
        quarantine = next(
            o for o in overrides if o.get("ignore_errors") is True
        )
        assert len(quarantine["module"]) <= 67, (
            "mypy quarantine grew — annotate the module and shrink the list "
            "instead"
        )

    def test_e501_quarantine_shrinks_never_grows(self):
        cfg = tomllib.loads((REPO / "pyproject.toml").read_text())
        pfi = cfg["tool"]["ruff"]["lint"]["per-file-ignores"]
        e501_count = sum(1 for v in pfi.values() if "E501" in v)
        assert e501_count <= 60, (
            "E501 per-file quarantine grew beyond the 5.1f baseline — reflow "
            "lines instead of quarantining new files"
        )


class TestLintGateContract:
    def test_lint_steps_match_pre_commit_commands(self):
        """CI and pre-commit must run the same checks, in the same order —
        otherwise something passes locally and fails remotely (or vice versa),
        which is the split-brain this gate exists to prevent."""
        assert ci_gate.LINT_STEPS[0][1][:3] == ("ruff", "check", "hyperion")
        assert ci_gate.LINT_STEPS[1][1] == ("mypy", "hyperion")
        cfg = (REPO / ".pre-commit-config.yaml").read_text()
        assert re.search(r"args:\s*\[hyperion, tests, tools\]", cfg)
        assert re.search(r"args:\s*\[hyperion\]", cfg)
