"""W-08 — The Quality Gate can refuse to ship.

Verifies the acceptance criteria from HYPERION_DEEP_AUDIT_2026-07-31.md §W-08:

1. A 2.15/4.0 score with critical dimension failures produces no client PDF:
   the orchestrator never invokes Stage 5 (delivery), the run ends FAILED
   with failure_reason="quality_gate", and an operator diagnostic is written.
2. ``max_iterations_reached`` appears in no shipping condition (grep audit).
3. The designer contains no quality evaluation (grep audit: zero "approved").
4. A blocked run writes an operator diagnostic containing dimension scores
   and blockers.
5. SHIP_WITH_CAVEAT is off by default and, when on, forces a limitations
   page notice onto the report before delivery runs.

Terminal-state derivation rules (orchestrator._compute_quality_terminal_state):
- BLOCKED: any integrity blocker, OR total_score < quality_ship_floor (3.0).
- APPROVED: gate's authoritative ``approved`` flag.
- otherwise SHIP_WITH_CAVEAT only when allow_ship_with_caveat is set;
  without it, BLOCKED.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from hyperion.config import ModelTier
from hyperion.orchestrator import WorkflowEngine
from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import (
    ConfidenceLevel,
    FinalReport,
    QualityDimension,
    QualityDimensionName,
    QualityScore,
    QualityTerminalState,
    Recommendation,
)
from hyperion.schemas.workflow import (
    QuestionType,
    TaskNode,
    TaskStatus,
    WorkflowDAG,
)

# ─────────────────────────────────────────────────────────────────────────────
# Fixtures / builders
# ─────────────────────────────────────────────────────────────────────────────


def _minimal_report(sources: int = 12) -> FinalReport:
    return FinalReport(
        engagement_id="ENG-TEST",
        question="Should Acme enter the market?",
        recommendation=Recommendation.ENTER,
        recommendation_rationale="Evidence supports entry.",
        critical_assumptions=["Prices stay flat."],
        confidence=ConfidenceLevel.MEDIUM,
        confidence_breakdown={"market": ConfidenceLevel.MEDIUM},
        executive_summary="Enter the market given favorable conditions.",
        total_sources=sources,
    )


def _dim(name: QualityDimensionName, score: int) -> QualityDimension:
    return QualityDimension(
        dimension_id=name,
        name=name.value.replace("_", " "),
        score=score,
        weight=0.1,
        feedback="feedback",
        critical=score < 3,
    )


def _blocked_score_low() -> QualityScore:
    """The audit's pathological case: 2.15/4.0 with five critical dimensions."""
    return QualityScore(
        dimensions=[
            _dim(QualityDimensionName.EVIDENCE_SUFFICIENCY, 1),
            _dim(QualityDimensionName.ANALYTICAL_DEPTH, 2),
            _dim(QualityDimensionName.COMPLETENESS, 2),
            _dim(QualityDimensionName.TONE_AND_VOICE, 2),
            _dim(QualityDimensionName.VISUAL_QUALITY, 3),
        ],
        total_score=2.15,
        approved=False,
        iteration=4,
        gaps=["16 unresolved gaps"],
        critical_dimensions=[
            QualityDimensionName.EVIDENCE_SUFFICIENCY,
            QualityDimensionName.ANALYTICAL_DEPTH,
            QualityDimensionName.COMPLETENESS,
            QualityDimensionName.TONE_AND_VOICE,
        ],
        max_iterations_reached=True,
    )


def _approved_score() -> QualityScore:
    return QualityScore(
        dimensions=[_dim(QualityDimensionName.TONE_AND_VOICE, 5)],
        total_score=4.5,
        approved=True,
        iteration=1,
    )


def _sub_threshold_no_blockers() -> QualityScore:
    """Score 3.5: above the ship floor (3.0), below the approval threshold
    (4.0), no integrity blockers. The SHIP_WITH_CAVEAT candidate."""
    return QualityScore(
        dimensions=[_dim(QualityDimensionName.TONE_AND_VOICE, 3)],
        total_score=3.5,
        approved=False,
        iteration=2,
    )


def _integrity_blocked_score() -> QualityScore:
    return QualityScore(
        dimensions=[_dim(QualityDimensionName.TONE_AND_VOICE, 5)],
        total_score=4.5,
        approved=False,
        iteration=1,
        integrity_blockers=["LEAK: raw dict literal in report body"],
    )


# ─────────────────────────────────────────────────────────────────────────────
# 1. Terminal-state derivation (unit)
# ─────────────────────────────────────────────────────────────────────────────


class TestTerminalStateDerivation:
    def _engine(self) -> WorkflowEngine:
        engine = WorkflowEngine(bus=MagicMock())
        engine._log = lambda *a, **k: None  # type: ignore[assignment]
        return engine

    def test_low_score_five_critical_dimensions_is_blocked(self):
        engine = self._engine()
        score = _blocked_score_low()
        engine._compute_quality_terminal_state(score)
        assert score.terminal_state == QualityTerminalState.BLOCKED
        assert score.blocked_reason is not None
        assert "2.15" in score.blocked_reason
        assert "ship floor" in score.blocked_reason
        # Critical dimension names appear in the reason for the operator.
        assert "evidence_sufficiency" in score.blocked_reason

    def test_integrity_blocker_is_blocked_even_with_high_score(self):
        engine = self._engine()
        score = _integrity_blocked_score()
        engine._compute_quality_terminal_state(score)
        assert score.terminal_state == QualityTerminalState.BLOCKED
        assert "integrity blocker" in (score.blocked_reason or "")
        assert "LEAK" in (score.blocked_reason or "")

    def test_approved_score_is_approved(self):
        engine = self._engine()
        score = _approved_score()
        engine._compute_quality_terminal_state(score)
        assert score.terminal_state == QualityTerminalState.APPROVED
        assert score.blocked_reason is None

    def test_caveat_off_by_default_blocks_sub_threshold(self):
        """allow_ship_with_caveat defaults to False: a 3.5 score (above floor,
        below threshold, no blockers) must BLOCK, not silently degrade."""
        engine = self._engine()
        score = _sub_threshold_no_blockers()
        engine._compute_quality_terminal_state(score)
        assert score.terminal_state == QualityTerminalState.BLOCKED
        assert "allow_ship_with_caveat" in (score.blocked_reason or "")

    def test_caveat_enabled_ships_with_caveat(self, monkeypatch):
        from hyperion import config as _config

        settings = _config.get_settings()
        monkeypatch.setattr(settings, "allow_ship_with_caveat", True, raising=False)
        engine = self._engine()
        score = _sub_threshold_no_blockers()
        engine._compute_quality_terminal_state(score)
        assert score.terminal_state == QualityTerminalState.SHIP_WITH_CAVEAT

    def test_max_iterations_reached_not_read_in_derivation(self):
        """A score identical in every way except max_iterations_reached must
        land in the same terminal state — iteration exhaustion is diagnostic."""
        engine = self._engine()
        a = _blocked_score_low()
        b = _blocked_score_low()
        b.max_iterations_reached = False
        engine._compute_quality_terminal_state(a)
        engine._compute_quality_terminal_state(b)
        assert a.terminal_state == b.terminal_state == QualityTerminalState.BLOCKED


# ─────────────────────────────────────────────────────────────────────────────
# 2. Operator diagnostic on BLOCKED
# ─────────────────────────────────────────────────────────────────────────────


class TestBlockedDiagnostic:
    def test_diagnostic_contains_dimensions_blockers_gaps(self, tmp_path, monkeypatch):
        from hyperion import config as _config

        settings = _config.get_settings()
        monkeypatch.setattr(settings, "reports_dir", tmp_path, raising=False)

        engine = WorkflowEngine(bus=MagicMock())
        engine._log = lambda *a, **k: None  # type: ignore[assignment]
        engine._engagement_id = "eng_w08test"

        score = _blocked_score_low()
        engine._compute_quality_terminal_state(score)
        path = engine._write_blocked_diagnostic(score)

        assert path, "diagnostic path must be non-empty"
        with open(path, encoding="utf-8") as diagnostic_file:
            payload = json.load(diagnostic_file)

        assert payload["terminal_state"] == "blocked"
        assert payload["blocked_reason"]
        assert payload["total_score"] == pytest.approx(2.15)
        # Dimension scores present with numeric scores.
        assert len(payload["dimension_scores"]) == 5
        assert all("score" in d for d in payload["dimension_scores"])
        # Blockers / gaps / critical dims present.
        assert "integrity_blockers" in payload
        assert payload["open_gaps"] == ["16 unresolved gaps"]
        assert "evidence_sufficiency" in payload["critical_dimensions"]
        # Corpus stats and roster decisions keys exist.
        assert "corpus_stats" in payload
        assert "roster_decisions" in payload
        assert "max_iterations_reached" in payload

    def test_diagnostic_never_written_to_deliverable_path(self, tmp_path, monkeypatch):
        """The diagnostic lives under reports/diagnostics/, never at a
        deliverable name — a blocked run produces no client artifact."""
        from hyperion import config as _config

        settings = _config.get_settings()
        monkeypatch.setattr(settings, "reports_dir", tmp_path, raising=False)

        engine = WorkflowEngine(bus=MagicMock())
        engine._log = lambda *a, **k: None  # type: ignore[assignment]
        engine._engagement_id = "eng_w08test2"

        score = _blocked_score_low()
        engine._compute_quality_terminal_state(score)
        path = engine._write_blocked_diagnostic(score)

        assert "diagnostics" in path
        assert "blocked_eng_w08test2" in path
        assert not path.endswith(".pdf")

    def test_diagnostic_failure_is_logged_not_raised(self, monkeypatch):
        """A write failure must not break the block decision itself."""
        from hyperion import config as _config

        settings = _config.get_settings()

        class _BadPath:
            def __truediv__(self, other):
                return self

            def mkdir(self, *a, **k):
                raise PermissionError("read-only fs")

        monkeypatch.setattr(settings, "reports_dir", _BadPath(), raising=False)
        engine = WorkflowEngine(bus=MagicMock())
        engine._log = lambda *a, **k: None  # type: ignore[assignment]
        engine._engagement_id = "eng_w08test3"
        score = _blocked_score_low()
        assert engine._write_blocked_diagnostic(score) == ""


# ─────────────────────────────────────────────────────────────────────────────
# 3. run_engagement: BLOCKED short-circuits before Stage 5
# ─────────────────────────────────────────────────────────────────────────────


def _delivery_dag() -> WorkflowDAG:
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
    tasks[0].status = TaskStatus.COMPLETED
    return WorkflowDAG(
        engagement_id="eng_t", question="q", question_type=QuestionType.GENERAL,
        tasks=tasks, estimated_total_llm_calls=4, estimated_total_tokens=1000,
        estimated_duration_minutes=1.0,
    )


class _Harness:
    """Drives run_engagement with every stage before Stage 4b stubbed, so the
    only real logic under test is the W-08 terminal-state gate itself."""

    def __init__(self, monkeypatch, score: QualityScore, report: FinalReport | None = None):
        self.engine = WorkflowEngine(bus=MagicMock())
        self.engine._log = lambda *a, **k: None  # type: ignore[assignment]
        self.engine._print_run_summary = lambda *a, **k: None  # type: ignore[assignment]
        self.delivery_invoked: list[str] = []
        self.report = report or _minimal_report()

        dag = _delivery_dag()
        director = MagicMock()
        director.run = AsyncMock(return_value=dag)
        self.engine._director = director

        async def _no_exec(*args, **kwargs):
            return None

        self.engine._execute_dag = _no_exec  # type: ignore[assignment]
        self.engine._get_output_by_agent = (  # type: ignore[assignment]
            lambda d, agent: self.report if agent == AgentName.SYNTHESIS_LEAD else None
        )
        self.engine._ensure_gap_closure_task = lambda d: None  # type: ignore[assignment]
        self.engine._gap_closure_phase = _no_exec  # type: ignore[assignment]
        self.engine._record_unresolved_gaps = lambda r, g: None  # type: ignore[assignment]
        self.engine._get_agent = lambda name: MagicMock(section_gaps=[])  # type: ignore[assignment]
        self.engine._publish_dag_to_tui = lambda d: None  # type: ignore[assignment]
        self.engine._publish_task_update = lambda t: None  # type: ignore[assignment]

        async def _quality_loop(d, fr, fcr):
            # Mirror the real loop's post-loop step: the terminal state is
            # computed inside _quality_iteration_loop, so the stub must run
            # the same derivation (otherwise every score keeps the BLOCKED
            # default and APPROVED/CAVEAT paths can never be exercised).
            self.engine._compute_quality_terminal_state(score)
            return self.report, score, score.iteration

        self.engine._quality_iteration_loop = _quality_loop  # type: ignore[assignment]

        # Track every Stage 5 delivery invocation.
        async def _track_delivery(task, d):
            # Must mark COMPLETED: the delivery fix-point loop re-runs until
            # no task changes state — a stub that records but never completes
            # would spin that loop forever.
            self.delivery_invoked.append(task.agent.value)
            task.status = TaskStatus.COMPLETED
            task.completed_at = 0.0

        self.engine._execute_task = _track_delivery  # type: ignore[assignment]

        async def _no_markdown(fr, eid):
            return ""

        self.engine._generate_markdown = _no_markdown  # type: ignore[assignment]

        # Stub the pre-pipeline externals: health, preflight, credentials,
        # journal/artifacts/manifest, bus start, director construction.
        monkeypatch.setattr(
            "hyperion.obs.health.check_startup_health", lambda s: [], raising=True
        )
        monkeypatch.setattr(
            "hyperion.infra.preflight.assert_research_stack_usable",
            lambda s: None,
            raising=True,
        )
        # NOTE: patch with factories, not the MagicMock class itself —
        # RunJournal("eng_x") with MagicMock as the class would treat the
        # run_id positional arg as `spec` and produce a str-specced mock
        # without .open()/.close().
        monkeypatch.setattr(
            "hyperion.orchestrator.RunJournal",
            lambda *a, **k: MagicMock(),
            raising=False,
        )
        monkeypatch.setattr(
            "hyperion.orchestrator.ArtifactStore",
            lambda *a, **k: MagicMock(),
            raising=False,
        )
        monkeypatch.setattr(
            "hyperion.orchestrator.RunManifest",
            lambda *a, **k: MagicMock(),
            raising=False,
        )

        bus = self.engine.bus
        bus._running = True
        bus.start = AsyncMock()
        bus.clear_retained_findings = MagicMock()
        bus.publish = AsyncMock()

        monkeypatch.setattr("hyperion.orchestrator.get_bus", lambda: bus, raising=False)
        monkeypatch.setattr(
            "hyperion.orchestrator.EngagementDirector",
            lambda **kw: director,
            raising=False,
        )


class TestBlockedShortCircuit:
    @pytest.mark.asyncio
    async def test_blocked_run_never_invokes_delivery(self, monkeypatch, tmp_path):
        from hyperion import config as _config

        settings = _config.get_settings()
        monkeypatch.setattr(settings, "reports_dir", tmp_path, raising=False)

        score = _blocked_score_low()  # 2.15, five critical dims
        harness = _Harness(monkeypatch, score)

        result = await harness.engine.run_engagement("Should Acme enter the market?")

        # The run FAILED with quality_gate attribution.
        assert result.success is False
        assert result.failure_reason == "quality_gate"
        assert "BLOCKED" in result.error
        # Stage 5 was NEVER touched: no delivery agent ran.
        assert harness.delivery_invoked == []
        # No PDF anywhere on the result.
        assert result.pdf_path == ""
        # An operator diagnostic was written and surfaced on the metadata.
        assert result.metadata is not None
        assert result.metadata.blocked_diagnostic_path
        with open(
            result.metadata.blocked_diagnostic_path, encoding="utf-8"
        ) as diagnostic_file:
            payload = json.load(diagnostic_file)
        assert payload["terminal_state"] == "blocked"
        assert len(payload["dimension_scores"]) == 5

    @pytest.mark.asyncio
    async def test_approved_run_reaches_delivery(self, monkeypatch, tmp_path):
        """Sanity: APPROVED does not short-circuit; Stage 5 runs."""
        from hyperion import config as _config

        settings = _config.get_settings()
        monkeypatch.setattr(settings, "reports_dir", tmp_path, raising=False)

        score = _approved_score()
        harness = _Harness(monkeypatch, score)

        result = await harness.engine.run_engagement("Should Acme enter the market?")

        # Delivery tasks ran in topological order.
        assert harness.delivery_invoked == [
            "data_visualizer",
            "presentation_designer",
            "render_engine",
        ]
        # No quality-gate failure attribution.
        assert result.failure_reason != "quality_gate"

    @pytest.mark.asyncio
    async def test_ship_with_caveat_forces_limitations_notice(self, monkeypatch, tmp_path):
        from hyperion import config as _config

        settings = _config.get_settings()
        monkeypatch.setattr(settings, "reports_dir", tmp_path, raising=False)
        monkeypatch.setattr(settings, "allow_ship_with_caveat", True, raising=False)

        score = _sub_threshold_no_blockers()  # 3.5 → SHIP_WITH_CAVEAT when enabled
        report = _minimal_report()
        harness = _Harness(monkeypatch, score, report=report)

        result = await harness.engine.run_engagement("Should Acme enter the market?")

        # Delivery still ran (caveat ships), but the report carries the notice.
        assert harness.delivery_invoked != []
        assert result.failure_reason != "quality_gate"
        assert any("QUALITY CAVEAT" in lim for lim in report.limitations)
        # The notice is the FIRST limitation (prominent).
        assert report.limitations[0].startswith("QUALITY CAVEAT")

    @pytest.mark.asyncio
    async def test_caveat_disabled_blocks_sub_threshold_run(self, monkeypatch, tmp_path):
        """Default (caveat off): a 3.5 run BLOCKS before delivery."""
        from hyperion import config as _config

        settings = _config.get_settings()
        monkeypatch.setattr(settings, "reports_dir", tmp_path, raising=False)
        monkeypatch.setattr(settings, "allow_ship_with_caveat", False, raising=False)

        score = _sub_threshold_no_blockers()
        harness = _Harness(monkeypatch, score)

        result = await harness.engine.run_engagement("Should Acme enter the market?")

        assert result.success is False
        assert result.failure_reason == "quality_gate"
        assert harness.delivery_invoked == []


# ─────────────────────────────────────────────────────────────────────────────
# 4. Source-level acceptance audits (greps from the spec)
# ─────────────────────────────────────────────────────────────────────────────


class TestSourceAudits:
    def test_designer_contains_no_quality_evaluation(self):
        """Acceptance: `grep -n "approved" presentation_designer.py` → nothing."""
        with open(
            "hyperion/agents/delivery/presentation_designer.py", encoding="utf-8"
        ) as source_file:
            src = source_file.read()
        assert "approved" not in src, (
            "the designer must contain zero 'approved' mentions — delivery "
            "never evaluates quality (W-08 step 4)"
        )

    def test_max_iterations_reached_never_in_ship_condition(self):
        """Every read of max_iterations_reached in the orchestrator must be a
        write or a diagnostic/reporting read, never a ship/no-ship branch."""
        with open("hyperion/orchestrator.py", encoding="utf-8") as source_file:
            src = source_file.read()
        # The terminal-state method must not branch on it.
        start = src.index("def _compute_quality_terminal_state")
        end = src.index("def _write_blocked_diagnostic")
        body = src[start:end]
        assert "max_iterations_reached" not in body or (
            "deliberately NOT read" in body
        ), "terminal-state derivation must not read max_iterations_reached"
        # No conditional anywhere uses it as a ship condition.
        for line in src.splitlines():
            stripped = line.strip()
            if "max_iterations_reached" not in stripped:
                continue
            assert not stripped.startswith("if ") or "not read" in stripped, (
                f"max_iterations_reached used in a conditional: {stripped!r}"
            )

    def test_terminal_state_computed_once_in_loop(self):
        with open("hyperion/orchestrator.py", encoding="utf-8") as source_file:
            src = source_file.read()
        assert src.count("_compute_quality_terminal_state(") == 2, (
            "exactly one definition + one call site (single decision point)"
        )

    def test_engagement_metadata_carries_diagnostic_path(self):
        with open("hyperion/schemas/workflow.py", encoding="utf-8") as source_file:
            src = source_file.read()
        assert "blocked_diagnostic_path" in src
