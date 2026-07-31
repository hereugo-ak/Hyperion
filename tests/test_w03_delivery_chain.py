"""W-03 — One writer, running last, deriving everything from the document.

Verifies the acceptance criteria from HYPERION_DEEP_AUDIT_2026-07-31.md §W-03
without a live engagement (sandbox has no providers/Docker):

1. The delivery DAG edges are re-pointed: visualizer runs before the
   designer, the render engine depends only on the designer, and the chain
   is acyclic.
2. The Presentation Designer no longer authors a PDF: no `_generate_pdf`,
   no WeasyPrint tool acquisition, and LayoutPlan carries no `pdf_path`.
3. The orchestrator's pdf_path has exactly one source (the render engine);
   the RC-4 `elif layout_plan.pdf_path` fallback is gone.
4. The render engine's two-pass TOC: pass-1 named destinations are resolved
   and substituted into the TOC rows; page count must not change.
5. page_audit's TOC check runs at zero tolerance (phantom entry + stated
   page mismatch both produce violations) on a real rendered document.
"""

from __future__ import annotations

import pytest

from hyperion.schemas.agents import AgentName


# ── 1. DAG ordering ────────────────────────────────────────────────────────────


def _build_delivery_dag():
    from hyperion.agents.engagement_director import EngagementDirector
    from hyperion.schemas.workflow import QuestionType

    director = EngagementDirector.__new__(EngagementDirector)
    # _build_dag only touches self for logging/escalation counters in paths
    # not exercised here; construct the minimal surface it needs.
    director._escalation_count = 0
    return director._build_dag(
        engagement_id="eng_test",
        question="Should we enter the Brazilian logistics market?",
        question_types=[QuestionType.GENERAL],
        selected_agents=[AgentName.MARKET_ANALYST],
        key_question="",
        second_brain_context="",
    )


def test_delivery_dag_edges_repointed_and_acyclic() -> None:
    dag = _build_delivery_dag()
    tasks = {t.id: t for t in dag.tasks}

    viz = tasks["task_data_visualizer"]
    designer = tasks["task_presentation_designer"]
    render = tasks["task_render_engine"]

    # The exact W-03 edge set.
    assert set(viz.dependencies) == {"task_quality_gate"}
    assert set(designer.dependencies) == {"task_quality_gate", "task_data_visualizer"}
    assert set(render.dependencies) == {"task_presentation_designer"}

    # The visualizer must have NO dependency on the designer (spec check).
    assert "task_presentation_designer" not in viz.dependencies

    # Acyclic: topological sort must cover every delivery task.
    order: list[str] = []
    done: set[str] = set()
    remaining = dict(tasks)
    while remaining:
        ready = [
            tid for tid, t in remaining.items()
            if all(d in done or d not in tasks for d in t.dependencies)
        ]
        assert ready, "DAG has a cycle"
        for tid in ready:
            order.append(tid)
            done.add(tid)
            del remaining[tid]

    # Delivery chain executes in the re-pointed order.
    assert order.index("task_data_visualizer") < order.index("task_presentation_designer")
    assert order.index("task_presentation_designer") < order.index("task_render_engine")


# ── 2. designer no longer writes a PDF ────────────────────────────────────────


def test_designer_has_no_pdf_authorship() -> None:
    src = open("hyperion/agents/delivery/presentation_designer.py", encoding="utf-8").read()
    assert "async def _generate_pdf" not in src
    assert "self._generate_pdf(" not in src
    assert "get_tool(ToolName.WEASYPRINT)" not in src
    assert "render_pdf(" not in src

    import re
    # The AgentSpec must not list WEASYPRINT as a designer tool (comments OK).
    spec_block = src[src.index("PRESENTATION_DESIGNER_SPEC"):src.index("PRESENTATION_DESIGNER_SPEC") + 400]
    active = "\n".join(ln for ln in spec_block.splitlines() if not ln.strip().startswith("#"))
    assert "ToolName.WEASYPRINT" not in active


def test_layout_plan_has_no_pdf_path() -> None:
    from hyperion.schemas.models import LayoutPlan

    plan = LayoutPlan(engagement_id="eng_x")
    assert not hasattr(plan, "pdf_path")


# ── 3. single writer at the orchestrator ──────────────────────────────────────


def test_orchestrator_pdf_path_single_source() -> None:
    src = open("hyperion/orchestrator.py", encoding="utf-8").read()
    assert "elif result.layout_plan" not in src, "RC-4 fallback must be deleted"
    # The fix-point delivery loop must exist (designer runs after viz now).
    assert "while progressed" in src


# ── 4. two-pass TOC in the render engine ──────────────────────────────────────


def test_two_pass_toc_resolves_and_substitutes(tmp_path) -> None:
    """Render a real TOC document; pass 2 must fill cells from the artifact."""
    from hyperion.agents.delivery.render_engine import RenderEngine

    engine = RenderEngine.__new__(RenderEngine)
    engine._log = lambda *a, **k: None  # silence

    html = """<!DOCTYPE html><html><head><style>
        @page { size: A4; background: #F5F4EE; }
        .page-break { page-break-after: always; }
        </style></head><body>
        <div class="page-break"><h2>Table of Contents</h2>
        <div class="data-table toc-table"><table>
            <tr><td><a href="#exec-summary">Executive Summary</a></td><td class="toc-page"></td></tr>
            <tr><td><a href="#methodology">Methodology</a></td><td class="toc-page"></td></tr>
        </table></div></div>
        <div class="page-break" id="exec-summary"><h2>Executive Summary</h2><p>body</p></div>
        <div class="page-break" id="methodology"><h2>Methodology</h2><p>body</p></div>
        </body></html>"""
    html_path = tmp_path / "report.html"
    html_path.write_text(html, encoding="utf-8")

    # Pass 1: render the document.
    from weasyprint import HTML

    pdf_path = tmp_path / "report.pdf"
    HTML(filename=str(html_path)).write_pdf(str(pdf_path))

    resolved = engine._resolve_toc_page_numbers(str(pdf_path))
    assert resolved.get("exec-summary") == 2, f"exec-summary on page 2, got {resolved}"
    assert resolved.get("methodology") == 3, f"methodology on page 3, got {resolved}"

    # Pass 2: substitution must fill the TOC cells with the real numbers.
    final = engine._inject_toc_page_numbers(str(html_path), resolved)
    updated = open(final, encoding="utf-8").read()
    assert 'data-toc-verified="2">2</td>' in updated
    assert 'data-toc-verified="3">3</td>' in updated


# ── 5. zero-tolerance TOC gate in page_audit ──────────────────────────────────


def test_page_audit_toc_zero_tolerance(tmp_path) -> None:
    """A phantom TOC entry and a wrong stated page must both be violations."""
    from weasyprint import HTML

    # Phantom entry: TOC lists a chapter that does not exist in the document.
    html = """<html><head><style>
        @page { size: A4; background: #F5F4EE; }
        .page-break { page-break-after: always; }
        </style></head><body>
        <div class="page-break"><h2>Table of Contents</h2><table>
            <tr><td>Executive Summary ............ 2</td></tr>
            <tr><td>Risk Analysis ................ 5</td></tr>
        </table></div>
        <div class="page-break"><h2>Executive Summary</h2><p>body</p></div>
        </body></html>"""
    p = tmp_path / "phantom.pdf"
    HTML(string=html).write_pdf(str(p))

    from hyperion.output.page_audit import PageAuditError, _check_toc
    import fitz

    doc = fitz.open(str(p))
    violations = _check_toc(doc)
    doc.close()
    assert any("phantom entry" in v for v in violations), violations

    # Wrong stated page: entry claims page 5, heading is on page 2.
    html2 = """<html><head><style>
        @page { size: A4; background: #F5F4EE; }
        .page-break { page-break-after: always; }
        </style></head><body>
        <div class="page-break"><h2>Table of Contents</h2><table>
            <tr><td>Executive Summary ............ 5</td></tr>
        </table></div>
        <div class="page-break"><h2>Executive Summary</h2><p>body</p></div>
        </body></html>"""
    p2 = tmp_path / "wrong.pdf"
    HTML(string=html2).write_pdf(str(p2))
    doc = fitz.open(str(p2))
    violations2 = _check_toc(doc)
    doc.close()
    assert any("stated page 5" in v and "page 2" in v for v in violations2), violations2
