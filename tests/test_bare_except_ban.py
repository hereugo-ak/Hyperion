"""Tests for fix 5.3 — the ban on bare ``except Exception: pass``.

Audit §11 DoD #13 + §0.3: the original P0's root cause was a silent
``except Exception: pass`` in a search leg that turned a recoverable error
into a 100% outage that nothing surfaced. 5.3 (a) converted every silent
``pass``/``continue`` handler in the codebase to a recorded one, (b) added a
documented per-site ``noqa: BLE001 - <reason>`` to each intentional blanket
catch, and (c) enabled BLE + S110/S112 in the ruff select so any NEW silent
swallow fails the gate.

These tests are the negative control and recurrence guard:
  1. AST scan — no handler in hyperion/ whose only body is ``pass`` or
     ``continue`` under a blanket Exception catches silently (the defect class).
  2. Ruff config — BLE + S110/S112 must stay in the select (if dropped, the
     gate stops gating and these tests fail).
  3. Live negative control — a real silent ``except Exception: pass`` dropped
     into the scanned tree MUST make the ruff gate fail (skipped where ruff is
     absent, e.g. the 985MB sandbox).
  4. Justification — every remaining BLE001 noqa carries a non-empty reason, so
     the ban cannot be circumvented with a bare ``noqa: BLE001``.
"""

from __future__ import annotations

import ast
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
HYPERION = REPO / "hyperion"


def _blanket(handler: ast.ExceptHandler) -> bool:
    """True if the handler catches all exceptions (bare or Exception)."""
    t = handler.type
    return t is None or (isinstance(t, ast.Name) and t.id in ("Exception", "BaseException"))


def _silent(handler: ast.ExceptHandler) -> bool:
    """True if the handler body is exactly one pass/continue (swallows)."""
    return len(handler.body) == 1 and isinstance(handler.body[0], (ast.Pass, ast.Continue))


def _iter_python(root: Path):
    yield from sorted(root.rglob("*.py"))


class TestNoSilentBlanketExcept:
    """AST guard: no blanket ``except`` in the package may silently swallow.

    This is the exact defect class behind the P0 (§0.3). Unlike the ruff
    rules, this scan does not require ruff to be installed, so it runs in the
    985MB CI sandbox and catches the defect even if the ruff select is
    weakened.
    """

    def test_no_silent_except_exception_pass_in_package(self):
        offenders = []
        for path in _iter_python(HYPERION):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ExceptHandler) and _blanket(node) and _silent(node):
                    offenders.append(f"{path.relative_to(REPO)}:{node.lineno}")
        assert not offenders, (
            "silent blanket-except handlers reintroduced (the P0 defect "
            "class):\n  " + "\n  ".join(offenders)
        )


class TestRuffSelectKeepsTheBan:
    def test_ble_and_s110_s112_in_select(self):
        cfg = tomllib.loads((REPO / "pyproject.toml").read_text(encoding="utf-8"))
        select = set(cfg["tool"]["ruff"]["lint"]["select"])
        for rule in ("BLE", "S110", "S112"):
            assert rule in select, (
                f"ruff {rule} was dropped from select — the 5.3 ban is no "
                "longer enforced"
            )

    def test_every_ble001_noqa_has_a_reason(self):
        """A bare ``noqa: BLE001`` (no ``- reason``) is the ban circumvented."""
        bare = []
        for path in _iter_python(HYPERION):
            for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
                if "noqa: BLE001" in line and "noqa: BLE001 -" not in line:
                    bare.append(f"{path.relative_to(REPO)}:{i}")
        assert not bare, (
            "unjustified blanket-except suppressions (noqa without a reason):\n  "
            + "\n  ".join(bare)
        )


class TestLiveNegativeControl:
    """Drop a real silent ``except Exception: pass`` into the scanned tree;
    the ruff gate MUST catch it. Skipped where ruff is not installed."""

    @pytest.mark.skipif(shutil.which("ruff") is None, reason="ruff not installed")
    def test_reintroduced_silent_except_fails_ruff(self):
        probe = HYPERION / "_ble_nc_probe.py"
        probe.write_text(
            '"""NC probe — deleted by the test that writes it."""\n'
            "\n"
            "def f():\n"
            "    try:\n"
            "        return 1\n"
            "    except Exception:\n"
            "        pass\n"
        )
        try:
            r = subprocess.run(
                ["ruff", "check", str(probe), "--select", "BLE001,S110,S112"],
                capture_output=True,
                text=True,
            )
        finally:
            probe.unlink()
        assert r.returncode != 0, (
            "negative control: a reintroduced silent except must fail ruff"
        )
        assert "S110" in r.stdout or "BLE001" in r.stdout
