#!/usr/bin/env python3"""
HYPERION CI Regression Gate — fail CI if golden-set quality regresses.

Usage:
    python -m hyperion.eval.ci_gate           # run full golden set
    python -m hyperion.eval.ci_gate --report path/to/report.json  # check one report
    python -m hyperion.eval.ci_gate --update-baseline  # set new baseline after intentional changes

Exit codes:
    0 = pass (no regression)
    1 = regression detected
    2 = eval harness error
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any


async def run_gate(args: argparse.Namespace) -> int:
    from hyperion.eval import EvalHarness, run_deterministic_checks, GOLDEN_SET

    if args.report:
        # Single report mode — run deterministic checks only
        with open(args.report, "r", encoding="utf-8") as f:
            report = json.load(f)
        golden = GOLDEN_SET[0]
        checks = run_deterministic_checks(report, pdf_path="", golden=golden)
        failed = [c for c in checks if not c.passed]
        if failed:
            print(f"FAIL: {len(failed)}/{len(checks)} deterministic checks failed:")
            for c in failed:
                print(f"  - {c.name}: {c.detail}")
            return 1
        print(f"PASS: All {len(checks)} deterministic checks passed")
        return 0

    # Full golden-set mode
    harness = EvalHarness()
    print(f"Running golden set: {len(harness.golden_set)} queries...")

    try:
        results = await harness.run_all(save_baseline=args.update_baseline)
    except Exception as e:
        print(f"ERROR: Eval harness failed: {e}")
        return 2

    print(f"\nResults:")
    print(f"  Mean score:  {results.mean_score:.2f}")
    print(f"  Baseline:    {results.baseline_score:.2f}")
    print(f"  Pass rate:   {results.pass_rate:.1%}")
    print(f"  Regression:  {'YES' if results.regression_detected else 'NO'}")

    for r in results.results:
        status = "PASS" if r.success and r.all_checks_passed else "FAIL"
        print(f"  [{status}] {r.query_id}: {r.question[:50]}... "
              f"score={r.overall_score:.1f} "
              f"checks={'all pass' if r.all_checks_passed else 'some fail'}")

    if results.regression_detected:
        print(f"\nQUALITY REGRESSION DETECTED: "
              f"{results.mean_score:.2f} < {results.baseline_score:.2f} "
              f"- {harness.REGRESSION_THRESHOLD}")
        return 1

    print("\nNo regression detected.")
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="HYPERION CI Regression Gate")
    parser.add_argument(
        "--report",
        type=str,
        default=None,
        help="Path to a single report JSON for deterministic-only checks",
    )
    parser.add_argument(
        "--update-baseline",
        action="store_true",
        help="Update the stored baseline with current scores",
    )
    args = parser.parse_args()

    exit_code = asyncio.run(run_gate(args))
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
