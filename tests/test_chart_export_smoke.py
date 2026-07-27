"""
Chart export smoke test — plotly/kaleido version-compatibility guard.

Fix 0.4 (HYPERION_DEEP_AUDIT_2026-07-27.md §3.6 / §6 PHASE 0): the audit's
live probe found `fig.to_image()` raising `ValueError` because the sandbox
had `plotly==6.0.1` alongside `kaleido==1.0.0` installed — an incompatible
pair (kaleido 1.x needs a bundled Chrome runtime and a different plotly
integration than kaleido 0.x's static-engine mode). `pyproject.toml` already
pinned `kaleido>=0.2.1,<1.0`, which is the *correct* constraint; the failure
was environment drift (an editable install had never been run), not a wrong
pin. Reinstalling from `pyproject.toml` resolves `kaleido==0.2.1`, which is
compatible with `plotly==6.0.1`, and `to_image()` works.

This test exists so that any future dependency bump that reintroduces the
plotly/kaleido incompatibility fails CI immediately instead of silently
degrading every chart in every report to the matplotlib fallback (which is
functional, but is a lesser-quality "we didn't notice kaleido broke" state
per the audit's `has_exhibits: false` finding).
"""

from __future__ import annotations

import pytest


def test_kaleido_is_pinned_below_v1() -> None:
    """kaleido must stay on the 0.x static-engine line per pyproject.toml.

    kaleido>=1.0 requires a separately-managed Chrome runtime and is not
    drop-in compatible with plotly's `to_image()`/`write_image()` calls at
    the versions HYPERION currently pins plotly to. If this test starts
    failing because kaleido was intentionally upgraded to 1.x, the plotly
    pin and the full export path must be re-validated together, not just
    the version string.
    """
    import kaleido

    version = getattr(kaleido, "__version__", None)
    assert version is not None, "kaleido must be installed"
    major = int(version.split(".")[0])
    assert major < 1, (
        f"kaleido {version} is >= 1.0 — this requires validating plotly/kaleido "
        "compatibility end-to-end (see HYPERION_DEEP_AUDIT_2026-07-27.md §3.6)."
    )


def test_plotly_to_image_smoke() -> None:
    """A minimal Plotly figure must export to a non-trivial PNG.

    This directly reproduces the audit's live probe:
        `fig.to_image()` -> ValueError: kaleido engine required.
    If this raises, every Plotly chart in the report silently degrades to
    the matplotlib fallback (or, if matplotlib is also unavailable, to a
    text table) — which is exactly the failure mode that produced
    `has_exhibits: false` in the audited run.
    """
    import plotly.graph_objects as go

    fig = go.Figure(data=[go.Bar(x=["a", "b", "c"], y=[1, 2, 3])])
    image_bytes = fig.to_image(format="png")

    assert isinstance(image_bytes, bytes)
    assert len(image_bytes) > 1024, (
        f"Expected a real PNG (>1kB), got {len(image_bytes)} bytes — "
        "kaleido may be exporting a blank/error image."
    )


def test_chart_generator_tier1_plotly_path_succeeds(tmp_path, monkeypatch) -> None:
    """ChartGenerator.generate() should succeed via Tier 1 (Plotly+kaleido),
    not silently fall through to the matplotlib or data-table tiers.

    This is a stronger assertion than the raw plotly smoke test: it exercises
    the actual production code path (`hyperion.output.charts.ChartGenerator`)
    end-to-end, including brand styling and the write_image() call.
    """
    from pathlib import Path

    from hyperion.config import get_settings
    from hyperion.output.charts import ChartGenerator, ChartSpec

    settings = get_settings()
    gen = ChartGenerator(settings=settings)
    # Redirect output to tmp_path instead of the real assets dir.
    gen._output_dir = Path(tmp_path)

    spec = ChartSpec(
        chart_type="bar",
        title="Smoke Test Chart",
        x_data=["A", "B", "C"],
        y_data=[[10, 20, 30]],
        series_names=["Value"],
    )

    result = gen.generate(spec)

    assert result.success, f"Chart generation failed: {result.error}"
    assert result.image_path is not None

    image_file = Path(result.image_path)
    assert image_file.exists(), "Chart image file was not written to disk"
    assert image_file.stat().st_size > 1024, "Chart image file is suspiciously small"
