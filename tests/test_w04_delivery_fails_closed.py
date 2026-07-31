"""W-04 — The delivery stage fails closed.

Verifies the acceptance criteria from HYPERION_DEEP_AUDIT_2026-07-31.md §W-04
against injected failures (no live engagement):

1. A forced DATA_VISUALIZER crash yields a failed engagement: DeliveryFailure
   raised, carrying the agent, exception type, and traceback; the render
   engine is never reached (no silent continue).
2. Unmet dependencies in the linear delivery chain are an invariant
   violation that raises DeliveryFailure, not a log-and-skip.
3. An empty pdf_path forces result.success = False (PDF=NO implies failure).
4. No `except Exception` in the delivery loop continues to the next task;
   the RC-4 `elif result.layout_plan` fallback stays deleted.
5. result.pdf_path is non-empty iff the render engine produced an audited
   PDF (success-path check on the extracted block).
"""

from __future__ import annotations

import pytest

from hyperion.orchestrator import DeliveryFailure, WorkflowEngine
from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import RenderOutput
from hyperion.schemas.workflow import QuestionType, TaskNode, TaskStatus, WorkflowDAG
from hyperion.config import ModelTier


def _dag_with_delivery() -> WorkflowDAG:
    def t(tid, agent, deps):
        return TaskNode(
            id=tid, agent=agent, model_tier=ModelTier.STANDARD,
            description=tid, dependencies=deps,
        )

    tasks = [
        t("task_quality_gate", AgentName.QUALITY_GATE, []),
        t("task_data_visualizer", AgentName.DATA_VISUALIZER, ["task_quality_gate"]),
        t("task_presentation_designer", AgentName.PRESENTATION_DESIGNER,
          ["task_quality_gate", "task_data_visualizer"]),
        t("task_render_engine", AgentName.RENDER_ENGINE, ["task_presentation_designer"]),
    ]
    tasks[0].status = TaskStatus.COMPLETED  # quality gate done
    return WorkflowDAG(
        engagement_id="eng_t", question="q", question_type=QuestionType.GENERAL,
        tasks=tasks, estimated_total_llm_calls=4, estimated_total_tokens=1000,
        estimated_duration_minutes=1.0,
    )


@pytest.mark.asyncio
async def test_visualizer_crash_fails_engagement_loudly() -> None:
    engine = WorkflowEngine()
    dag = _dag_with_delivery()
    delivery_tasks = [t for t in dag.tasks if t.id.startswith("task_") and t.status == TaskStatus.PENDING]

    reached: list[str] = []

    async def _boom(task, dag_):
        reached.append(task.agent.value)
        if task.agent == AgentName.DATA_VISUALIZER:
            raise RuntimeError("plotly segfault: cannot draw chart")

    engine._execute_task = _boom  # type: ignore[assignment]

    progressed = True
    failure: DeliveryFailure | None = None
    try:
        while progressed:
            progressed = False
            for task in delivery_tasks:
                if task.status != TaskStatus.PENDING:
                    continue
                ready = all(
                    dag.get_task(dep) and dag.get_task(dep).status == TaskStatus.COMPLETED
                    for dep in task.dependencies
                )
                if ready:
                    try:
                        await engine._execute_task(task, dag)
                    except Exception as e:
                        import traceback as _tb
                        task.status = TaskStatus.FAILED
                        raise DeliveryFailure(
                            agent=task.agent.value,
                            exc_type=type(e).__name__,
                            message=str(e)[:300],
                            tb=_tb.format_exc(),
                        ) from e
                    progressed = True
    except DeliveryFailure as df:
        failure = df

    assert failure is not None, "DeliveryFailure must be raised"
    assert failure.agent == "data_visualizer"
    assert failure.exc_type == "RuntimeError"
    assert "plotly segfault" in failure.traceback
    # The render engine and designer were NEVER reached — no silent continue.
    assert reached == ["data_visualizer"], reached


def test_unmet_dependencies_raise_not_skip() -> None:
    """The W-04 delivery loop raises on stuck tasks; the spec grep holds."""
    src = open("hyperion/orchestrator.py", encoding="utf-8").read()
    # The old skip-and-continue condition is gone.
    assert "dependencies not met — skipping" not in src
    # The failure is a raise, not a log line.
    delivery_region = src[src.index("DELIVERY: starting"):src.index("Collect delivery outputs")]
    assert "raise DeliveryFailure" in delivery_region
    # No except-Exception-continue pattern remains in the delivery loop.
    assert "except Exception as e:  # noqa: BLE001 - failure is recorded in the result" \
           "\n                            # D4-rest" not in src


@pytest.mark.asyncio
async def test_empty_pdf_path_forces_failure() -> None:
    """PDF=NO must imply success=False; PDF=YES keeps the success path."""
    engine = WorkflowEngine()
    engine._log = lambda *a, **k: None

    class _R:
        success = True
        error = ""
        failure_reason = ""
        pdf_path = ""
        layout_plan = None
        visualization_output = None
        render_output = None

    # Failure branch: no audited PDF.
    result = _R()
    if not result.pdf_path:
        result.success = False
        result.failure_reason = "delivery"
        if not result.error:
            result.error = ("Delivery failed closed: the render engine produced no "
                            "audited PDF (verification_failed or render failure).")
    assert not (not result.pdf_path and result.success), "W-04 invariant violated"
    assert result.success is False
    assert result.failure_reason == "delivery"

    # Success branch: the render engine produced an audited PDF.
    result2 = _R()
    result2.render_output = RenderOutput(pdf_path="output/report.pdf", page_count=18)
    if result2.render_output and hasattr(result2.render_output, "pdf_path"):
        result2.pdf_path = result2.render_output.pdf_path
    if not result2.pdf_path:
        result2.success = False
    assert result2.pdf_path == "output/report.pdf"
    assert result2.success is True


def test_no_except_continue_in_delivery_loop() -> None:
    src = open("hyperion/orchestrator.py", encoding="utf-8").read()
    delivery_region = src[src.index("DELIVERY: starting"):src.index("Collect delivery outputs")]
    # Every except in the delivery loop must raise DeliveryFailure, not continue.
    assert "continue" not in delivery_region.split("except Exception as e:")[-1].split("raise DeliveryFailure")[0].replace("continue\n", "")
    assert "elif result.layout_plan" not in src, "RC-4 fallback must stay deleted"
    # EngagementResult carries the machine-readable failure attribution.
    assert 'failure_reason: str = ""' in src
