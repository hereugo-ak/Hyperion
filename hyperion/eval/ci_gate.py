#!/usr/bin/env python3
"""HYPERION CI Regression Gate — fail CI if golden-set quality regresses.

Usage:
    python -m hyperion.eval.ci_gate           # run full golden set
    python -m hyperion.eval.ci_gate --report path/to/report.json  # check one report
    python -m hyperion.eval.ci_gate --update-baseline  # set new baseline after intentional changes

Exit codes:
    0 = pass (no regression)
    1 = regression detected
    2 = eval harness error

History
-------
This module shipped for its entire life with the shebang and the opening
docstring quotes fused onto line 1 (`#!/usr/bin/env python3\"\"\"`), which made
the whole docstring parse as code and the module a `SyntaxError`. Nothing
imported it, so nothing noticed: the regression gate was itself the one file in
the tree that could not run. `tests/test_module_importability.py` now compiles
and imports every shipped module so that class of failure cannot recur.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

logger = logging.getLogger(__name__)

# Exit codes are the module's contract with CI; name them so callers and tests
# agree on the meaning rather than re-hardcoding integers.
EXIT_PASS = 0
EXIT_REGRESSION = 1
EXIT_HARNESS_ERROR = 2


async def run_gate(args: argparse.Namespace) -> int:
    """Execute the requested gate mode and return a process exit code."""
    from hyperion.eval import GOLDEN_SET, EvalHarness, run_deterministic_checks

    if args.report:
        # Single-report mode — deterministic structural checks only, no LLM.
        try:
            with open(args.report, encoding="utf-8") as f:
                report = json.load(f)
        except (OSError, json.JSONDecodeError) as exc:
            # A missing or malformed report is an operator/harness error, not a
            # quality regression: the two must not share an exit code or CI
            # cannot distinguish "the build got worse" from "the path is wrong".
            print(f"ERROR: could not read report {args.report!r}: {exc}")
            return EXIT_HARNESS_ERROR

        golden = _select_golden(GOLDEN_SET, args.golden_id)
        if golden is None:
            valid = ", ".join(g.id for g in GOLDEN_SET)
            print(f"ERROR: unknown --golden-id {args.golden_id!r}. Valid ids: {valid}")
            return EXIT_HARNESS_ERROR

        checks = run_deterministic_checks(report, pdf_path=args.pdf or "", golden=golden)
        failed = [c for c in checks if not c.passed]
        if failed:
            print(f"FAIL: {len(failed)}/{len(checks)} deterministic checks failed:")
            for c in failed:
                print(f"  - {c.name}: {c.detail}")
            return EXIT_REGRESSION
        print(f"PASS: All {len(checks)} deterministic checks passed")
        return EXIT_PASS

    # Full golden-set mode.
    harness = EvalHarness()
    print(f"Running golden set: {len(harness.golden_set)} queries...")

    try:
        results = await harness.run_all(save_baseline=args.update_baseline)
    except Exception as exc:  # noqa: BLE001 - top-level CI boundary, must not traceback
        # Deliberately broad: this is the outermost boundary of a CI entrypoint,
        # where any escape must become exit code 2 rather than a traceback. The
        # cause is logged with a stack trace so the failure is still diagnosable.
        logger.error("Eval harness failed", exc_info=True)
        print(f"ERROR: Eval harness failed: {exc}")
        return EXIT_HARNESS_ERROR

    print("\nResults:")
    print(f"  Mean score:  {results.mean_score:.2f}")
    print(f"  Baseline:    {results.baseline_score:.2f}")
    print(f"  Pass rate:   {results.pass_rate:.1%}")
    print(f"  Regression:  {'YES' if results.regression_detected else 'NO'}")

    for r in results.results:
        status = "PASS" if r.success and r.all_checks_passed else "FAIL"
        print(
            f"  [{status}] {r.query_id}: {r.question[:50]}... "
            f"score={r.overall_score:.1f} "
            f"checks={'all pass' if r.all_checks_passed else 'some fail'}"
        )

    if results.regression_detected:
        print(
            f"\nQUALITY REGRESSION DETECTED: "
            f"{results.mean_score:.2f} < {results.baseline_score:.2f} "
            f"- {harness.REGRESSION_THRESHOLD}"
        )
        return EXIT_REGRESSION

    print("\nNo regression detected.")
    return EXIT_PASS


def _select_golden(golden_set, golden_id):  # type: ignore[no-untyped-def]
    """Pick the golden query to check a single report against.

    Defaults to the first entry to preserve the historical behaviour, but an
    explicit `--golden-id` is honoured so a report is not silently graded
    against the wrong question type's expectations.
    """
    if not golden_set:
        return None
    if not golden_id:
        return golden_set[0]
    for g in golden_set:
        if g.id == golden_id:
            return g
    return None


def build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser (exposed separately so tests can exercise it)."""
    parser = argparse.ArgumentParser(description="HYPERION CI Regression Gate")
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Path to a single report JSON for deterministic-only checks",
    )
    parser.add_argument(
        "--pdf",
        type=str,
        default=None,
        help="Optional path to the rendered PDF, enabling the PDF-side checks",
    )
    parser.add_argument(
        "--golden-id",
        type=str,
        default=None,
        help="Golden query id to grade --report against (default: first in the set)",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Update the stored baseline with current scores",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    sys.exit(asyncio.run(run_gate(args)))


if __name__ == "__main__":
    main()
