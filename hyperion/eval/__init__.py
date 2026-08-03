"""HYPERION offline evaluation package — golden-set + regression gate."""

from hyperion.eval.harness import (
    GOLDEN_SET,
    CheckResult,
    EvalHarness,
    EvalResults,
    GoldenQuery,
    QueryEvalResult,
    run_deterministic_checks,
)

__all__ = [
    "CheckResult",
    "EvalHarness",
    "EvalResults",
    "GoldenQuery",
    "GOLDEN_SET",
    "QueryEvalResult",
    "run_deterministic_checks",
]
