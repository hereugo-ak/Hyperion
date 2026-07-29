"""Tests for fix 5.1f — the ruff + mypy --strict process gate.

The original P0's root cause (audit §0.3, §11 DoD #13) was that lint findings
— F401/F841/B905 among them — repeatedly proved to be live outages hiding as
style noise, yet nothing enforced them: no pre-commit config, no CI workflow,
and a CI gate that ran quality checks only. This module is the negative
control for the fix: if the gate is removed, weakened, or silently skips when
a tool is missing, these tests MUST fail.

Covered:
  1. LINT_STEPS contract — ruff checks hyperion/tests/tools; mypy runs hyperion.
  2. Exit codes — green tree passes; a failing tool yields EXIT_REGRESSION;
     a missing tool yields EXIT_HARNESS_ERROR (never a silent pass).
  3. Ordering and output — both tools run; failures are named, not swallowed.
  4. Parser — --lint is accepted and reaches run_lint.
  5. Structural guards — pre-commit config exists and forbids silent
     auto-rewriting; pyproject keeps strict mypy and the ruff rule select;
     the mypy backlog quarantine can only shrink (AST/data guard).

No LLM, no network: subprocess and shutil.which are stubbed throughout, so
this runs in the 985MB CI sandbox.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import yaml

from hyperion.eval.ci_gate import (
    EXIT_HARNESS_ERROR,
    EXIT_PASS,
    EXIT_REGRESSION,
    LINT_STEPS,
    build_parser,
    run_lint,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    return subprocess.CompletedProcess(args=(), returncode=returncode, stdout=stdout, stderr=stderr)


class TestLintStepsContract:
    """The gate must run exactly the two tools the P0 went unenforced on."""

    def test_ruff_checks_all_three_trees(self):
        steps = dict(LINT_STEPS)
        assert "ruff" in steps, "ruff was dropped from the gate"
        cmd = steps["ruff"]
        assert cmd[:2] == ("ruff", "check")
        for tree in ("hyperion", "tests", "tools"):
            assert tree in cmd, f"ruff no longer checks {tree}/"

    def test_mypy_checks_the_package(self):
        steps = dict(LINT_STEPS)
        assert "mypy" in steps, "mypy was dropped from the gate"
        cmd = steps["mypy"]
        assert cmd[0] == "mypy"
        assert "hyperion" in cmd

    def test_commands_are_data_not_shell_strings(self):
        for _name, cmd in LINT_STEPS:
            assert isinstance(cmd, tuple), (
                "gate commands must be argv tuples — a shell string invites "
                "injection and hides the command from these tests"
            )


class TestRunLintExitCodes:
    def test_green_tree_passes(self, monkeypatch):
        monkeypatch.setattr("hyperion.eval.ci_gate.shutil.which", lambda _c: "/usr/bin/tool")
        monkeypatch.setattr(
            "hyperion.eval.ci_gate.subprocess.run",
            lambda *a, **k: _completed(0, "ok"),
        )
        assert run_lint() == EXIT_PASS

    def test_failing_tool_is_a_regression_not_a_pass(self, monkeypatch):
        monkeypatch.setattr("hyperion.eval.ci_gate.shutil.which", lambda _c: "/usr/bin/tool")
        monkeypatch.setattr(
            "hyperion.eval.ci_gate.subprocess.run",
            lambda *a, **k: _completed(1, "F841 something unused"),
        )
        assert run_lint() == EXIT_REGRESSION

    def test_missing_tool_is_harness_error_never_silent_pass(self, monkeypatch):
        """The gate must not degrade to a pass when ruff/mypy is absent — that
        is precisely 'the gate exists but does not run' (the P0 process defect)."""
        monkeypatch.setattr("hyperion.eval.ci_gate.shutil.which", lambda _c: None)
        monkeypatch.setattr(
            "hyperion.eval.ci_gate.subprocess.run",
            lambda *a, **k: _completed(0, "ok"),
        )
        assert run_lint() == EXIT_HARNESS_ERROR

    def test_both_tools_run_and_failures_are_named(self, monkeypatch, capsys):
        calls = []

        def _run(cmd, *a, **k):
            calls.append(cmd[0])
            if cmd[0] == "ruff":
                return _completed(1, "I001 unsorted imports")
            return _completed(0, "ok")

        monkeypatch.setattr("hyperion.eval.ci_gate.shutil.which", lambda _c: "/usr/bin/tool")
        monkeypatch.setattr("hyperion.eval.ci_gate.subprocess.run", _run)
        rc = run_lint()
        out = capsys.readouterr().out
        assert calls == ["ruff", "mypy"], "both tools must run even when the first fails"
        assert rc == EXIT_REGRESSION
        assert "ruff" in out and "FAIL" in out, "the failing tool must be named in output"

    def test_output_tail_is_capped(self, monkeypatch, capsys):
        huge = "\n".join(f"line {i}" for i in range(500))
        monkeypatch.setattr("hyperion.eval.ci_gate.shutil.which", lambda _c: "/usr/bin/tool")
        monkeypatch.setattr(
            "hyperion.eval.ci_gate.subprocess.run",
            lambda *a, **k: _completed(1, huge),
        )
        run_lint()
        out = capsys.readouterr().out
        assert "line 499" in out, "the tail of the failure output must be visible"
        assert "line 0\n" not in out, "the gate must not dump 500 lines into CI logs"


class TestParser:
    def test_lint_flag_parses(self):
        args = build_parser().parse_args(["--lint"])
        assert args.lint is True

    def test_lint_defaults_off(self):
        args = build_parser().parse_args([])
        assert args.lint is False


class TestStructuralGuards:
    """AST/config guards so the NEXT recurrence of 'gate silently weakened'
    is caught by the suite, not by an audit."""

    def test_pre_commit_config_exists_and_covers_both_tools(self):
        cfg = REPO_ROOT / ".pre-commit-config.yaml"
        assert cfg.exists(), (
            ".pre-commit-config.yaml was deleted — the local half of the "
            "5.1f gate is gone"
        )
        text = cfg.read_text(encoding="utf-8")
        assert "ruff" in text and "mypy" in text

    def test_pre_commit_does_not_silently_rewrite_code(self):
        """The 5.1e triage proved ruff's autofix can be semantics-changing
        (UP042/TC00x). A hook with --fix rewrites code at commit time with no
        review — the exact surprise class this gate exists to prevent.

        Structural check, not a text scan: the config's own comments document
        why rewriting is forbidden and may mention the flag by name."""
        cfg = yaml.safe_load(
            (REPO_ROOT / ".pre-commit-config.yaml").read_text(encoding="utf-8")
        )
        hooks = [
            hook for repo in cfg.get("repos", []) for hook in repo.get("hooks", [])
        ]
        assert hooks, "no hooks parsed — pre-commit config structure changed"
        for hook in hooks:
            hook_id = hook.get("id", "<unknown>")
            assert "--fix" not in hook.get("args", []), (
                f"hook {hook_id} passes an autofix flag — pre-commit must "
                "report, not rewrite"
            )
            assert "--fix" not in str(hook.get("entry", "")).split(), (
                f"hook {hook_id} runs an autofix entry — pre-commit must "
                "report, not rewrite"
            )

    def test_pyproject_keeps_strict_mypy_and_ruff_select(self):
        cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        assert cfg["tool"]["mypy"]["strict"] is True, "mypy strict was weakened"
        select = cfg["tool"]["ruff"]["lint"]["select"]
        for family in ("E", "F", "I", "UP", "B", "SIM"):
            assert family in select, f"ruff family {family} was dropped from select"

    def test_mypy_backlog_quarantine_is_explicit_and_shrinkable_only(self):
        """The staged allowlist exists so NEW modules are strict by default.
        It must live in pyproject as an explicit ignore_errors list — and it
        may only ever shrink (a growing quarantine re-creates the P0)."""
        cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        quarantine = [
            o for o in cfg["tool"]["mypy"]["overrides"] if o.get("ignore_errors") is True
        ]
        assert quarantine, "the backlog quarantine was removed from pyproject"
        modules = quarantine[0]["module"]
        assert isinstance(modules, list) and modules, "quarantine must be a module list"
        # Baseline: 67 backlog modules at the 5.1f commit. Anything larger
        # means someone added modules to the quarantine instead of fixing them.
        assert len(modules) <= 67, (
            f"backlog quarantine grew to {len(modules)} modules — fix the "
            "module's types instead of hiding it"
        )

    def test_semantics_changing_rule_families_stay_ignored_with_reason(self):
        """UP042/TC00x/SIM112 are ignored because the 'fix' changes runtime
        behavior in this codebase (documented in 989372d/6acbdb3). If the
        ignore is dropped without the codebase being made safe, the next
        --fix run silently rewrites client-facing strings."""
        cfg = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        ignore = cfg["tool"]["ruff"]["lint"]["ignore"]
        for rule in ("UP042", "TC001", "SIM112"):
            assert rule in ignore, (
                f"{rule} un-ignored — only safe after the codebase is "
                "converted deliberately, not by autofix"
            )


class TestNegativeControl:
    """Prove the gate actually gates: with the defect reintroduced (failing
    tool), the suite path that CI takes MUST produce EXIT_REGRESSION."""

    def test_reintroduced_defect_fails_the_gate(self, monkeypatch):
        monkeypatch.setattr("hyperion.eval.ci_gate.shutil.which", lambda _c: "/usr/bin/tool")
        monkeypatch.setattr(
            "hyperion.eval.ci_gate.subprocess.run",
            lambda *a, **k: _completed(2, "error: something broke"),
        )
        rc = run_lint()
        assert rc != EXIT_PASS, (
            "negative control: a failing tool must NEVER produce EXIT_PASS"
        )
        assert rc == EXIT_REGRESSION
