"""HYPERION offline evaluation package — golden-set + regression gate."""

from hyperion.eval.harness import (
    CheckResult,
    EvalHarness,
    EvalResults,
    GoldenQuery,
    GOLDEN_SET,
    JUDGE_RUBRIC,
    QueryEvalResult,
    run_deterministic_checks,
)

__all__ = [
    "CheckResult",
    "EvalHarness",
    "EvalResults",
    "GoldenQuery",
    "GOLDEN_SET",
    "JUDGE_RUBRIC",
    "QueryEvalResult",
    "run_deterministic_checks",
]
