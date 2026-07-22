"""HYPERION engagement room — the main 3-panel engagement screen.

Per ARCHITECTURE.md §8.2:

    Engagement Room (main screen):
    ┌─────────────────────────────────────────────────────────────────┐
    │  HYPERION · many minds. one reading.                    [Mark]  │
    ├─────────────────────────────────────────────────────────────────┤
    │  ┌─ Agents ──────────────────┐  ┌─ TPM Usage ────────────────┐  │
    │  │ ● Engagement Director     │  │ Google  ████████░░  80%    │  │
    │  │   STRONG · WORKING        │  │ NVIDIA  ███░░░░░░░  30%    │  │
    │  │ ● Market Analyst          │  │ Cerebras████████░░  85%    │  │
    │  │   STANDARD · WORKING      │  │ Groq    ██████░░░░  60%    │  │
    │  │ ● Competitive Intel       │  └────────────────────────────┘  │
    │  │   STANDARD · WORKING      │                                   │
    │  │ ● Financial Analyst       │  ┌─ Findings Stream ──────────┐  │
    │  │   STANDARD+ · WAITING     │  │ [Market] TAM estimate:     │  │
    │  │ ● Risk Analyst            │  │   $1.8B - $2.3B (range)    │  │
    │  │   STANDARD · WORKING      │  │   Sources: 4 · Confidence: │  │
    │  │                           │  │   HIGH                      │  │
    │  │ ● Fact Checker            │  │ [Comp] Competitor A pricing │  │
    │  │   FAST · IDLE             │  │   increased 15% YoY...      │  │
    │  │                           │  │                             │  │
    │  │ Sub-agents: 7 active      │  │                             │  │
    │  └───────────────────────────┘  └─────────────────────────────┘  │
    ├─────────────────────────────────────────────────────────────────┤
    │  > _                                                            │
    │  /consult /providers /vault /export /help                       │
    └─────────────────────────────────────────────────────────────────┘

This is the spec'd 3-panel layout: AgentGrid (left) + TPMBar + FindingsStream
(right) + PromptBar (bottom), with the animated Mark in the header.

The existing SessionScreen remains as the default single-surface mode; this
EngagementScreen provides the spec'd multi-panel alternative.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Static

from hyperion.tui.content import build_line, span
from hyperion.tui.roster import ROSTER
from hyperion.tui.theme import (
    BORDER_SUBTLE,
    CLAY,
    CLAY_DEEP,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)
from hyperion.tui.widgets.agent_grid import AgentGrid
from hyperion.tui.widgets.findings_stream import FindingsStream
from hyperion.tui.widgets.mark import Mark, MarkState
from hyperion.tui.widgets.prompt import (
    CancelTurn,
    ClearScrollback,
    PromptBar,
    PromptSubmitted,
)
from hyperion.tui.widgets.rule import hr
from hyperion.tui.widgets.tpm_bar import TPMBar


class EngagementScreen(Screen):
    """Main HYPERION engagement room — 3-panel layout per architecture spec.

    Left panel:  AgentGrid (live agent status)
    Right panel: TPMBar (provider usage) + FindingsStream (live findings)
    Bottom:      PromptBar (command input)
    Header:      HYPERION wordmark + animated Mark
    """

    DEFAULT_CSS = """
    EngagementScreen {
        layout: vertical;
        background: #141413;
        color: #F4F3EE;
    }
    #eng-header {
        dock: top;
        height: 1;
        width: 100%;
    }
    #eng-body {
        height: 1fr;
        width: 100%;
    }
    #eng-left {
        width: 40%;
        height: 100%;
        border-right: solid #2A2926;
        padding: 0 1;
    }
    #eng-right {
        width: 1fr;
        height: 100%;
        padding: 0 1;
    }
    #eng-tpm {
        height: auto;
        min-height: 6;
        border-bottom: solid #2A2926;
    }
    #eng-findings {
        height: 1fr;
    }
    #eng-footer {
        dock: bottom;
        height: 2;
        width: 100%;
    }
    #eng-prompt-rule {
        height: 1;
    }
    #eng-prompt {
        height: 1;
    }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("ctrl+l", "clear", "Clear", show=False),
    ]

    def __init__(self, reduced_motion: bool = False, demo: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reduced = reduced_motion
        self._demo = demo
        self._engagement_task: asyncio.Task | None = None
        self._bus_sub_id = "tui_engagement"

    def compose(self) -> ComposeResult:
        # Header: HYPERION · tagline · Mark
        with Horizontal(id="eng-header"):
            yield Static(build_line(
                span("  HYPERION", f"bold {CLAY}"),
                span("  ·  many minds. one reading.", TEXT_DIM),
            ), id="eng-title")
            yield Mark(id="eng-mark")
        # Body: left (agents) | right (TPM + findings)
        with Horizontal(id="eng-body"):
            with Vertical(id="eng-left"):
                yield AgentGrid(id="eng-agents")
            with Vertical(id="eng-right"):
                yield TPMBar(id="eng-tpm")
                yield FindingsStream(id="eng-findings")
        # Footer: rule + prompt
        with Vertical(id="eng-footer"):
            yield Static(hr(), id="eng-prompt-rule")
            yield PromptBar(id="eng-prompt")

    def on_mount(self) -> None:
        self.query_one("#eng-prompt", PromptBar).focus()
        self.query_one("#eng-mark", Mark).set_state(MarkState.DORMANT)
        # Show roster in agent grid
        grid = self.query_one("#eng-agents", AgentGrid)
        for a in ROSTER:
            grid.set_agent(
                key=a.key, name=a.name, badge=a.badge,
                tier="STANDARD", state="idle",
            )
        if self._demo:
            self.set_timer(0.5, self._run_demo)

    # ── prompt handling ──────────────────────────────────────────────────────────

    def on_prompt_submitted(self, event: PromptSubmitted) -> None:
        value = event.value.strip()
        if not value:
            return

        raw = value.lower()
        cmd = raw.lstrip("/")

        if cmd in ("help", "?"):
            self._show_help()
        elif cmd in ("clear", "cls"):
            self.action_clear()
        elif cmd == "demo":
            self._run_demo()
        elif cmd in ("quit", "exit"):
            self.app.exit()
        else:
            question = value
            if raw.startswith("/consult") or raw.startswith("consult"):
                parts = value.split(None, 1)
                question = parts[1] if len(parts) > 1 else ""
            if not question.strip():
                return
            self._start_engagement(question)

    # ── engagement ───────────────────────────────────────────────────────────────

    def _start_engagement(self, question: str) -> None:
        if self._engagement_task and not self._engagement_task.done():
            return
        self.query_one("#eng-prompt", PromptBar).set_busy(True)
        self.query_one("#eng-mark", Mark).set_state(MarkState.ORCHESTRATING)
        self._engagement_task = asyncio.create_task(self._run_engagement(question))

    async def _run_engagement(self, question: str) -> None:
        mark = self.query_one("#eng-mark", Mark)
        findings = self.query_one("#eng-findings", FindingsStream)
        grid = self.query_one("#eng-agents", AgentGrid)
        tpm = self.query_one("#eng-tpm", TPMBar)

        try:
            from hyperion.agents.bus import Channel, get_bus, reset_bus
            from hyperion.orchestrator import WorkflowEngine

            reset_bus()
            bus = get_bus()
            await bus.start()
            bus.subscribe(
                self._bus_sub_id,
                agent=None,
                channels={Channel.STATUS, Channel.FINDINGS, Channel.HANDOFF, Channel.ESCALATION, Channel.TUI},
                callback=self._on_bus_message,
            )

            mark.set_state(MarkState.ORCHESTRATING)
            engine = WorkflowEngine(bus=bus)
            try:
                result = await engine.run_engagement(question=question)
            finally:
                await engine.close()

            if result.success:
                mark.set_state(MarkState.DELIVERED)
                # Transition to deliverable screen if we have a report
                if result.final_report and hasattr(self.app, "show_deliverable"):
                    self.app.show_deliverable(result)
            else:
                mark.set_state(MarkState.BLOCKED)
        except asyncio.CancelledError:
            mark.set_state(MarkState.DORMANT)
            raise
        except Exception:
            mark.set_state(MarkState.BLOCKED)
        finally:
            try:
                from hyperion.agents.bus import get_bus
                get_bus().unsubscribe(self._bus_sub_id)
            except Exception:
                pass
            try:
                self.query_one("#eng-prompt", PromptBar).set_busy(False)
            except Exception:
                pass

    async def _on_bus_message(self, msg: Any) -> None:
        from hyperion.agents.bus import Channel
        from hyperion.tui.theme import agent_badge

        grid = self.query_one("#eng-agents", AgentGrid)
        findings = self.query_one("#eng-findings", FindingsStream)
        mark = self.query_one("#eng-mark", Mark)

        try:
            if msg.channel == Channel.STATUS:
                agent = msg.agent
                state = (msg.state or "").lower()
                detail = msg.detail or ""
                badge = agent_badge(agent)
                grid.update_state(agent, state)
                if state == "working":
                    mark.set_state(MarkState.ORCHESTRATING)
                elif state == "done":
                    grid.add_finding(agent)
            elif msg.channel == Channel.FINDINGS:
                finding = msg.finding
                title = (
                    getattr(finding, "title", None)
                    or getattr(finding, "headline", None)
                    or getattr(finding, "summary", None)
                    or "finding recorded"
                )
                content_val = getattr(finding, "content", "")
                snippet = str(content_val)[:200] if content_val else ""
                sources = getattr(finding, "source_count", 0) or 0
                confidence = ""
                conf_obj = getattr(finding, "confidence", None)
                if conf_obj:
                    confidence = getattr(conf_obj, "value", str(conf_obj))
                findings.add_finding(
                    agent=msg.agent,
                    badge=agent_badge(msg.agent),
                    title=str(title)[:120],
                    snippet=snippet,
                    sources=sources,
                    confidence=confidence,
                )
                grid.add_finding(msg.agent)
            elif msg.channel == Channel.HANDOFF:
                mark.set_state(MarkState.SYNTHESIZING)
            elif msg.channel == Channel.ESCALATION:
                mark.set_state(MarkState.BLOCKED)
                self.set_timer(2.0, lambda: mark.set_state(MarkState.ORCHESTRATING))
        except Exception:
            pass

    # ── demo mode ────────────────────────────────────────────────────────────────

    def _run_demo(self) -> None:
        if self._engagement_task and not self._engagement_task.done():
            return
        self.query_one("#eng-prompt", PromptBar).set_busy(True)
        self._engagement_task = asyncio.create_task(self._run_demo_async())

    async def _run_demo_async(self) -> None:
        grid = self.query_one("#eng-agents", AgentGrid)
        findings = self.query_one("#eng-findings", FindingsStream)
        tpm = self.query_one("#eng-tpm", TPMBar)
        mark = self.query_one("#eng-mark", Mark)

        try:
            mark.set_state(MarkState.ORCHESTRATING)
            tpm.touch_provider("google")
            tpm.touch_provider("nvidia")
            tpm.touch_provider("cerebras")
            tpm.touch_provider("groq")

            plan = [
                ("market_analyst", "MARKET", "sizing the addressable EV market"),
                ("competitive_intel", "COMPETE", "mapping incumbents & new entrants"),
                ("financial_analyst", "FINANCE", "unit economics & capex model"),
                ("risk_analyst", "RISK", "policy, supply-chain & FX exposure"),
                ("regulatory_analyst", "REGULATORY", "FAME-II & state incentives"),
            ]

            for key, badge, task in plan:
                grid.update_state(key, "working")
                tpm.set_usage("google", random.uniform(0.3, 0.8))
                tpm.set_usage("cerebras", random.uniform(0.4, 0.9))
                await asyncio.sleep(0.7)
                findings.add_finding(
                    agent=key, badge=badge, title=task,
                    snippet=f"analysis complete for {badge.lower()}",
                    sources=random.randint(2, 6),
                    confidence=random.choice(["HIGH", "MEDIUM", "HIGH"]),
                )
                grid.update_state(key, "done")
                await asyncio.sleep(0.3)

            mark.set_state(MarkState.SYNTHESIZING)
            await asyncio.sleep(1.5)

            mark.set_state(MarkState.DELIVERED)
            findings.add_finding(
                agent="synthesis_lead", badge="SYNTHESIS",
                title="Recommendation: ENTER, staged entry",
                snippet="High confidence · quality 4.6/5.0",
                sources=18, confidence="HIGH",
            )
        except asyncio.CancelledError:
            mark.set_state(MarkState.DORMANT)
            raise
        finally:
            try:
                self.query_one("#eng-prompt", PromptBar).set_busy(False)
            except Exception:
                pass

    # ── commands ─────────────────────────────────────────────────────────────────

    def _show_help(self) -> None:
        findings = self.query_one("#eng-findings", FindingsStream)
        findings.add_finding(
            agent="system", badge="SYSTEM",
            title="Commands: /consult <q> · /providers · /demo · /clear · /help · /quit",
        )

    def on_clear_scrollback(self, event: ClearScrollback) -> None:
        self.action_clear()

    def on_cancel_turn(self, event: CancelTurn) -> None:
        self.action_cancel()

    def action_clear(self) -> None:
        self.query_one("#eng-findings", FindingsStream).clear()
        self.query_one("#eng-agents", AgentGrid).clear()
        for a in ROSTER:
            self.query_one("#eng-agents", AgentGrid).set_agent(
                key=a.key, name=a.name, badge=a.badge, tier="STANDARD", state="idle",
            )

    def action_cancel(self) -> None:
        if self._engagement_task and not self._engagement_task.done():
            self._engagement_task.cancel()
        self.query_one("#eng-prompt", PromptBar).set_busy(False)
        self.query_one("#eng-mark", Mark).set_state(MarkState.DORMANT)
