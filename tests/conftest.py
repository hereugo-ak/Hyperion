"""Suite-wide fixtures. Primarily: keep the kaleido renderer from taxing every
module that runs after the chart tests.

Why this file exists
--------------------
`ChartGenerator.release_renderer()` (fix 4.3) frees the ~277 MB Chromium tree
kaleido reserves on its first export, and `generate_batch()` calls it in a
`finally`. But tests that call `ChartGenerator.generate()` **directly** never go
through `generate_batch`, so in the test session the tree simply stayed resident
for the life of the interpreter.

That is not a leak — latency is flat at ~0.15 s over 60 exports — it is a
steady-state reservation. On a 985 MB host it is still most of the free memory,
and it is charged to whichever module happens to run next. Measured
consequence: `tests/test_two_column_layout.py` spawns a WeasyPrint render child
that needs ~300 MB, and with the kaleido tree still held that child spent so
long in swap that it blew a 180 s per-test deadline — surfacing as four
`Timeout` errors in a module that has nothing to do with charts.

Releasing after each chart module means the reservation is paid once per module
that actually renders, instead of once per suite. Release is idempotent and the
next export transparently respawns the tree (~1.5 s cold vs ~0.15 s warm), so
this costs at most one respawn and never changes a result.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

# Modules that export real PNGs and therefore start the Chromium tree.
_RENDERER_MODULES = ("test_mbb_chart_vocabulary", "test_chart_export_smoke")


@pytest.fixture(autouse=True, scope="module")
def _release_kaleido_after_renderer_modules(request: pytest.FixtureRequest) -> Iterator[None]:
    """Reap the Chromium tree when a chart-exporting module finishes."""
    yield
    if not any(name in request.node.name for name in _RENDERER_MODULES):
        return
    try:
        from hyperion.output.charts import ChartGenerator
    except ImportError:  # pragma: no cover - charts optional at import time
        return
    # Never let cleanup be the reason a suite fails; release_renderer() already
    # swallows its own errors and returns False.
    ChartGenerator.release_renderer()


@pytest.fixture(autouse=True)
def _isolate_engine_health_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Give every test its own engine-health state file.

    ``EngineHealthTracker`` persists to ``vault/engine_health.json`` and the
    SearXNG fail-fast gate reads that file back on every query. Without
    isolation, one test's recorded 403/429 suspensions poison every later
    test in the same session: the fleet-health gate drops below its 2-engine
    floor and ``search()`` fail-fasts to an empty result — "zero findings" —
    for unrelated tests, and the file is left dirty for real runs. This is
    the same stale-state poisoning that can make an engagement fail-fast on
    boot after a previous session's bans.

    Redirecting the state path to a per-test temp file (and resetting the
    process-wide singleton) keeps the suite hermetic and order-independent.
    """
    monkeypatch.setenv(
        "HYPERION_ENGINE_HEALTH_STATE",
        str(tmp_path / "engine_health.json"),
    )
    from hyperion.tools.engine_health import reset_engine_health

    reset_engine_health()
    yield
    reset_engine_health()


@pytest.fixture(autouse=True)
def _isolate_fetch_cache() -> None:
    """F-0.1-8: clear the shared per-engagement fetch cache between tests.

    The cache is module-level (shared across UnifiedExtract instances) so a
    test that seeds a cached URL would leak it into the next test's ladder
    climb — exactly the same stale-state poisoning class the engine-health
    isolation guards against. Clearing per test keeps the suite hermetic and
    order-independent.
    """
    from hyperion.tools.unified_extract import clear_fetch_cache

    clear_fetch_cache()
    yield
    clear_fetch_cache()
