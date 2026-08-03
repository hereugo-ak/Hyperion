"""Tests for fix 3.4 — two-column body targeting 56 chars/line.

The audit (§3.4/§6 Phase 3 item 3.4) requires:
    Two-column body via column-count: 2; column-gap: 7mm targeting
    **56 chars/line**; keep exhibits/KPI strips full-bleed with
    column-span: all.

Exit criterion (§6 Phase 3): probe reports 52–60 chars/line, 2 columns.

These tests cover:
  1. Structure: the shipped CSS_TEMPLATE columns the prose body and
     column-spans the visual anchors; the HTML_TEMPLATE wraps body prose
     in .section-body (not the old impossible .no-break).
  2. Page frame: margins are benchmark-like (BCG measures L 36pt · R 35pt)
     so the columns actually resolve to the 52–60 char band.
  3. End-to-end: a real WeasyPrint render of the production template path
     measures 2 column bands and a median line measure inside 52–60 chars.

The end-to-end render runs in a SUBPROCESS (`tools/measure_two_column.py`).
That is a memory requirement, not a style preference. Measured on this host:

    baseline interpreter          10 MB
    + weasyprint/hyperion imports 113 MB
    + jinja render (2.3 MB HTML)  138 MB
    + WeasyPrint write_pdf        245 MB   (+107 MB)
    + one fitz get_text sweep     414 MB   (+169 MB)

Neither library returns that memory to the OS, and the original class-scoped
fixture re-ran the fitz sweep once per test (299 → 332 → 340 MB), so this one
module peaked at **454 MB** — more than the other 37 modules combined. On a
985 MB host that is what OOM-killed the whole single-process suite at 91%.
Streaming the pages and `gc.collect()` were both tried and neither helped;
process exit is the only thing that reclaims it. The parent now retains just
the handful of numbers it asserts on, and stays at ~17 MB.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from hyperion.agents.delivery.presentation_designer import (  # noqa: E402
    CSS_TEMPLATE,
    HTML_TEMPLATE,
)


def _css_rule(css: str, selector: str) -> str:
    """Extract the declaration block for a selector (best-effort)."""
    m = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", css)
    return m.group(1) if m else ""


class TestTwoColumnStructure:
    def test_section_body_is_two_column(self) -> None:
        rule = _css_rule(CSS_TEMPLATE, ".section-body")
        assert "column-count: 2" in rule
        assert "column-gap: 7mm" in rule

    def test_column_fill_balances_last_page(self) -> None:
        """P2-01: column-fill: auto fills column 1 to full height and never
        starts column 2 on short chapters (6 pages of report A measured
        col2=0w). balance is the one-word fix."""
        rule = _css_rule(CSS_TEMPLATE, ".section-body")
        assert "column-fill: balance" in rule
        assert "column-fill: auto" not in rule

    def test_visual_anchors_span_all_columns(self) -> None:
        """Exhibits/KPI strips/insight/implication boxes must be full-bleed —
        squeezed into a 68mm column they would read as sidebars."""
        for selector in (
            ".exhibit",
            ".kpi-strip",
            ".key-insight-box",
            ".implication-box",
            ".callout",
        ):
            m = re.search(
                re.escape(selector) + r"[^}]*column-span: all;", CSS_TEMPLATE
            )
            assert m is not None, f"{selector} missing column-span: all"

    def test_headings_not_orphaned_in_columns(self) -> None:
        assert re.search(
            r"\.section-body h3, \.section-body h4 \{[^}]*break-after: avoid",
            CSS_TEMPLATE,
        )

    def test_html_wraps_body_in_section_body(self) -> None:
        assert '<div class="section-body">' in HTML_TEMPLATE

    def test_body_no_longer_wrapped_in_no_break(self) -> None:
        """The old .no-break wrapper tried to keep a whole 2000-word body on
        one page — impossible, silently ignored, and incompatible with
        columns."""
        assert re.search(
            r'<div class="no-break">\s*\{\{ section\.body', HTML_TEMPLATE
        ) is None

    def test_page_margins_benchmark_like(self) -> None:
        """The BCG benchmark measures L 36pt · R 35pt. A 40mm binding margin
        (the old frame) cannot resolve a two-column measure to 52-60 chars;
        assert the frame never drifts back."""
        m = re.search(r"@page \{.*?size: A4;.*?margin: ([0-9.mm ]+);", CSS_TEMPLATE, re.DOTALL)
        assert m is not None
        top, right, bottom, left = (
            float(v.replace("mm", "")) for v in m.group(1).split()
        )
        assert left <= 20, f"left margin {left}mm too wide for two-column measure"
        assert right <= 20, f"right margin {right}mm too wide for two-column measure"
        assert left >= right, "binding allowance must be on the left, not the right"


class TestRenderedTwoColumnMeasure:
    """The audit's exit criterion measured on a real render of the production
    template path (same payload as tools/audit_render_probe.py).

    The render+measure happens once, in a child process, and this class asserts
    on the JSON it returns. See the module docstring for the memory numbers
    that force that design.
    """

    @pytest.fixture(scope="class")
    def metrics(self, tmp_path_factory: pytest.TempPathFactory) -> dict[str, float]:
        pytest.importorskip("weasyprint")
        pytest.importorskip("fitz")
        script = ROOT / "tools" / "measure_two_column.py"
        assert script.is_file(), f"missing measurement script: {script}"
        out_pdf = tmp_path_factory.mktemp("pdf") / "two_column_test.pdf"
        env = {**os.environ, "MPLBACKEND": "Agg"}

        def phase(flag: str) -> subprocess.CompletedProcess[str]:
            return subprocess.run(  # noqa: S603
                [sys.executable, str(script), flag, str(out_pdf)],
                capture_output=True,
                text=True,
                cwd=str(ROOT),
                timeout=300,
                check=False,
                env=env,
            )

        # The parent's OWN high-water mark, sampled either side of the two
        # child phases. RUSAGE_SELF excludes children, so this delta is the
        # amount the render pushed THIS interpreter's peak up: near zero while
        # the work stays in a subprocess, ~400 MB the moment anyone inlines it.
        # test_render_stays_within_a_child_process_memory_budget asserts on the
        # delta rather than on the absolute peak, because the absolute peak of
        # a full-suite run also carries plotly, kaleido, matplotlib and pymupdf
        # imported by 37 other modules and is therefore not attributable to
        # anything this class does.
        import resource

        rss_before_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss

        # Drive the two phases from here rather than letting the script fan out,
        # so the parent pytest process never has two heavyweight children alive
        # at the same time. rc=-9 means the CHILD was OOM-killed, which is a
        # host-capacity signal and reads nothing like an assertion failure —
        # hence the explicit decoding below.
        rendered = phase("--render")
        assert rendered.returncode == 0, (
            f"render phase failed rc={rendered.returncode}"
            f"{' (OOM-killed by the host)' if rendered.returncode == -9 else ''}\n"
            f"stderr tail:\n{rendered.stderr[-2000:]}"
        )
        assert out_pdf.is_file() and out_pdf.stat().st_size > 50_000, (
            f"render produced no usable PDF: "
            f"{out_pdf.stat().st_size if out_pdf.exists() else 'missing'} bytes"
        )

        measured = phase("--measure")
        assert measured.returncode == 0, (
            f"measure phase failed rc={measured.returncode}"
            f"{' (OOM-killed by the host)' if measured.returncode == -9 else ''}\n"
            f"stderr tail:\n{measured.stderr[-2000:]}"
        )
        data = json.loads(measured.stdout)
        # A child that renders nothing would otherwise sail through every
        # assertion below on empty data.
        assert data["line_count"] > 200, (
            f"only {data['line_count']} body lines measured — the render "
            f"produced no measurable prose, so the numbers below are vacuous"
        )
        assert data["pages"] > 0
        rss_after_kb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        data["parent_rss_growth_mb"] = max(0, rss_after_kb - rss_before_kb) // 1024
        data["parent_rss_peak_mb"] = rss_after_kb // 1024
        return data

    def test_two_column_bands(self, metrics: dict[str, float]) -> None:
        assert metrics["bands"] == 2, (
            f"{metrics['bands']} column band(s) — body prose is not two-column"
        )

    def test_median_chars_per_line_in_benchmark_band(self, metrics: dict[str, float]) -> None:
        median = metrics["median"]
        assert 52 <= median <= 60, f"median {median} chars/line outside 52-60"

    def test_no_wall_of_text_lines(self, metrics: dict[str, float]) -> None:
        """p90 must stay inside the band too — a handful of 87-char lines is
        the single-column wall of text sneaking back."""
        p90 = metrics["p90"]
        assert p90 <= 64, f"p90 {p90} chars/line — wide single-column lines remain"

    def test_render_stays_within_a_child_process_memory_budget(
        self, metrics: dict[str, float]
    ) -> None:
        """The isolation itself is the fix, so it is pinned.

        If someone inlines the render back into this interpreter, the parent's
        peak RSS jumps from ~17 MB to ~414 MB and the single-process suite is
        OOM-killed again on a 985 MB host. Making that regression visible here,
        rather than as an unexplained rc=137 in CI 37 modules later, is the
        point of this test.

        MEASUREMENT CORRECTED. The previous form asserted
        ``getrusage(RUSAGE_SELF).ru_maxrss < 300 MB``, i.e. the ABSOLUTE peak of
        the whole pytest interpreter. ``ru_maxrss`` is a high-water mark for the
        entire process lifetime, so in a full-suite run it also carries plotly,
        kaleido, matplotlib and pymupdf imported by other test modules: the run
        peaked at 351 MB and this test failed while the render was still
        correctly isolated in a child process. It reported a defect that did not
        exist, and — worse — would have kept failing for the wrong reason if the
        render HAD been inlined, so the signal was lost either way.

        The attributable figure is the DELTA in the parent's own high-water mark
        across the two child phases, sampled in the fixture. ``RUSAGE_SELF``
        excludes children, so subprocess work moves it barely at all, while an
        inlined WeasyPrint/fitz render moves it by hundreds of megabytes. The
        ceiling is set at 120 MB: comfortably above the tens of megabytes of
        parent-side JSON and fixture overhead, and far below the ~400 MB an
        in-process render would add.
        """
        growth_mb = metrics["parent_rss_growth_mb"]
        assert growth_mb < 120, (
            f"this test process's own peak RSS grew by {growth_mb} MB across "
            f"the render (absolute peak {metrics['parent_rss_peak_mb']} MB) — "
            f"the WeasyPrint/fitz render appears to have moved back "
            f"in-process; keep it in tools/measure_two_column.py"
        )
