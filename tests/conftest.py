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
