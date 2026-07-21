"""HYPERION session screen — one single scrolling document.

The whole interface is ONE scroll surface. The wordmark, tagline and full agent
roster are the first things written into it; every event the agents emit is
appended below. Ask a question and the conversation simply grows downward —
scroll back up at any moment and the logo and roster are still right there.
Nothing is docked away, nothing collapses, and there is no empty middle band
that reads as a "blank screen".

    ┌ HYPERION · session · providers ─────────────────────┬ TELEMETRY ┐
    │  ██╗  ██╗██╗   ██╗ … (wordmark, gradient)            │ status    │
    │  Multi-Agent Consulting Intelligence                 │ elapsed   │
    │                                                      │ phase     │
    │  ROSTER · 20 specialist agents                       │           │
    │    DIRECTOR   Engagement Director   decomposes …     │ AGENTS    │
    │    MARKET     Market Analyst        TAM/SAM/SOM …     │  live …   │
    │    …                                                 │           │
    │  ──────────────────────────────────────────────     │ RESOURCES │
    │  [12:00:03] READY   20 specialist agents online      │  tools    │
    │  ❯ should India enter the EV market?                 │  tokens   │
    │  [12:00:05] MARKET  sizing the addressable market ⠋  │           │
    │  …                                                   │           │
    ├──────────────────────────────────────────────────────┴───────────┤
    │  ❯ ▊                                                              │
    └────────────────────────────────────────────────────────────────────┘

The transcript is a selectable RichLog, so drag-to-highlight + Ctrl+Shift+C
copies anything on screen — logo, roster and events alike.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from hyperion.tui.banner import (
    hint_content,
    logo_content,
    roster_content,
    roster_summary,
)
from hyperion.tui.roster import ROSTER
from hyperion.tui.widgets.header import HeaderBar
from hyperion.tui.widgets.metrics import MetricsRail
from hyperion.tui.widgets.prompt import (
    CancelTurn,
    ClearScrollback,
    PromptBar,
    PromptSubmitted,
)
from hyperion.tui.widgets.rule import hr
from hyperion.tui.widgets.transcript import LogRow, Transcript

_HELP_LINES = [
    "consult          →  just type a question, e.g.  should India enter the EV market?",
    "/agents          →  show the full roster and what each specialist can do",
    "/providers       →  check which model providers are online",
    "/demo            →  run a simulated engagement (no API keys needed)",
    "/clear  /help    →  reset the session · show this help",
    "copy             →  drag to highlight, then Ctrl+Shift+C  (Ctrl+Shift+A = all)",
    "keys             →  Enter submit · ↑/↓ history · Ctrl+L clear · Ctrl+C cancel · Ctrl+Q quit",
]


class SessionScreen(Screen):
    """Main HYPERION session — a single, always-scrollable command surface."""

    DEFAULT_CSS = """
    SessionScreen {
        layout: vertical;
        background: #141413;
        color: #F4F3EE;
    }
    #topbar { dock: top; height: 2; width: 100%; }
    #topbar > #hdr { height: 1; }
    #topbar > #hdr-rule { height: 1; }
    /* ONE full-width scroll surface. Everything — logo, roster, event log —
       lives inside this single selectable RichLog, so the whole screen scrolls
       together and every character is copyable. It fills the space between the
       top header and the bottom footer group. */
    #log-stream { width: 100%; height: 1fr; }
    /* Bottom footer group: a single Vertical dock so its children stack
       correctly (status rule, live telemetry strip, prompt rule, prompt)
       instead of all colliding on one docked row. */
    #footer { dock: bottom; height: 4; width: 100%; }
    #footer > #status-rule { height: 1; }
    #footer > #status-bar { height: 1; }
    #footer > #prompt { height: 1; }
    #footer > #pre-prompt-rule { height: 1; }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("ctrl+l", "clear", "Clear", show=False),
        Binding("ctrl+home", "scroll_top", "Top", show=False),
        Binding("ctrl+end", "scroll_bottom", "Bottom", show=False),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, reduced_motion: bool = False, demo: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reduced = reduced_motion
        self._demo = demo
        self._session_id = "0x" + f"{random.randint(0, 0xFFFFFF):06X}"
        self._engagement_task: asyncio.Task | None = None
        self._bus_sub_id = "tui_session"
        self._active_rows: dict[str, LogRow] = {}

    # ── compose ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        with Vertical(id="topbar"):
            yield HeaderBar(version="v1.0.0", session_id=self._session_id, id="hdr")
            yield Static(hr(), id="hdr-rule")
        # single full-width scroll surface
        yield Transcript(id="log-stream")
        # bottom footer group (docked as one Vertical so children stack)
        with Vertical(id="footer"):
            yield Static(hr(), id="status-rule")
            yield MetricsRail(compact=True, id="status-bar")
            yield Static(hr(), id="pre-prompt-rule")
            yield PromptBar(id="prompt")

    def on_mount(self) -> None:
        self.query_one("#prompt", PromptBar).focus()
        self._render_intro()
        delay = 0.05 if self._reduced else 0.6
        self.set_timer(delay, self._show_ready)
        if self._demo:
            self.set_timer(delay + 0.4, self._start_demo)

    def _render_intro(self) -> None:
        """Write the wordmark + compact roster as the opening of the scroll doc.

        The roster here is the COMPACT summary so the event log below stays on
        screen; `/agents` prints the full roster with each agent's ability.
        """
        log = self._log()
        log.write_block(logo_content(), blank_after=1)
        log.write_block(roster_summary(online=len(ROSTER)), blank_after=1)
        log.write_block(hint_content(), blank_after=1)
        log.write_block(hr(), blank_after=1)
        # keep the top (logo) in view on first paint
        try:
            log.scroll_home(animate=False)
        except Exception:
            pass

    def _log(self) -> Transcript:
        return self.query_one("#log-stream", Transcript)

    def _metrics(self) -> MetricsRail:
        return self.query_one("#status-bar", MetricsRail)

    def _show_ready(self) -> None:
        self._log().add_entry(
            "READY", f"{len(ROSTER)} specialist agents online · context primed"
        )

    # ── selection / copy helpers used by the App ───────────────────────────────

    def select_all_transcript(self) -> None:
        self._log().select_all()

    def selected_transcript_text(self) -> str:
        try:
            get_sel = getattr(self, "get_selected_text", None)
            if callable(get_sel):
                return get_sel() or ""
        except Exception:
            pass
        return ""

    # ── prompt handling ──────────────────────────────────────────────────────────

    def on_prompt_submitted(self, event: PromptSubmitted) -> None:
        value = event.value.strip()
        log = self._log()
        log.add_row(LogRow(badge="❯", content=value))

        raw = value.lower()
        cmd = raw.lstrip("/")

        if cmd in ("help", "?"):
            self._show_help()
        elif cmd in ("clear", "cls"):
            self.action_clear()
        elif cmd == "demo":
            self._start_demo()
        elif cmd in ("agents", "roster"):
            self._show_roster()
        elif cmd in ("providers", "provider"):
            self._run_providers()
        elif cmd.startswith("vault"):
            self._run_vault(value)
        elif cmd.startswith("export"):
            log.add_entry("DONE", "session transcript exported", icon="✓")
        elif cmd in ("quit", "exit"):
            self.app.exit()
        else:
            question = value
            if raw.startswith("/consult") or raw.startswith("consult"):
                parts = value.split(None, 1)
                question = parts[1] if len(parts) > 1 else ""
            if not question.strip():
                log.add_entry("WARN", "give me a question to consult on", icon="▸")
                return
            self._start_engagement(question)

    # ── real engagement, streamed from the AgentBus ────────────────────────────

    def _start_engagement(self, question: str) -> None:
        if self._engagement_task and not self._engagement_task.done():
            self._log().add_entry("WARN", "an engagement is already running", icon="▸")
            return
        self.query_one("#prompt", PromptBar).set_busy(True)
        self._active_rows.clear()
        self._metrics().start(phase="decompose")
        self._log().add_entry(
            "THINKING",
            "decomposing objective — routing to specialist agents",
            spinner=True,
        )
        self._engagement_task = asyncio.create_task(self._run_engagement(question))

    async def _run_engagement(self, question: str) -> None:
        log = self._log()
        try:
            from hyperion.agents.bus import Channel, get_bus, reset_bus
            from hyperion.orchestrator import WorkflowEngine

            reset_bus()
            bus = get_bus()
            await bus.start()
            bus.subscribe(
                self._bus_sub_id,
                agent=None,
                channels={
                    Channel.STATUS,
                    Channel.FINDINGS,
                    Channel.HANDOFF,
                    Channel.ESCALATION,
                },
                callback=self._on_bus_message,
            )

            engine = WorkflowEngine(bus=bus)
            try:
                result = await engine.run_engagement(question=question)
            finally:
                await engine.close()

            if result.success and result.final_report:
                fr = result.final_report
                rec = getattr(getattr(fr, "recommendation", None), "value", "see report")
                conf = getattr(getattr(fr, "confidence", None), "value", "")
                detail = [
                    f"recommendation → {rec}" + (f" ({conf} confidence)" if conf else ""),
                ]
                if result.quality_score is not None:
                    detail.append(
                        f"quality → {result.quality_score.weighted_total:.1f}/5.0"
                        f" · {result.quality_iterations} iteration(s)"
                    )
                if result.pdf_path:
                    detail.append(f"pdf → {result.pdf_path}")
                log.add_entry(
                    "DONE",
                    f"engagement complete · {result.duration_seconds:.0f}s",
                    detail=detail,
                    icon="✓",
                )
                self._metrics().finish(ok=True)
            else:
                log.add_entry("ERROR", result.error or "engagement did not complete", icon="✗")
                self._metrics().finish(ok=False)
        except asyncio.CancelledError:
            log.add_entry("WARN", "engagement cancelled", icon="▸")
            self._metrics().finish(ok=False)
            raise
        except Exception as exc:  # surfaced inline, never blanks the screen
            log.add_entry("ERROR", f"{type(exc).__name__}: {exc}", icon="✗")
            log.add_entry(
                "SYSTEM",
                "the orchestrator raised — try `/demo` to preview the interface, "
                "or check keys with `/providers`",
                icon="▸",
            )
            self._metrics().finish(ok=False)
        finally:
            try:
                from hyperion.agents.bus import get_bus

                get_bus().unsubscribe(self._bus_sub_id)
            except Exception:
                pass
            try:
                self.query_one("#prompt", PromptBar).set_busy(False)
            except Exception:
                pass

    async def _on_bus_message(self, msg: Any) -> None:
        from hyperion.agents.bus import Channel
        from hyperion.tui.theme import agent_badge

        log = self._log()
        metrics = self._metrics()
        try:
            if msg.channel == Channel.STATUS:
                agent = msg.agent
                state = (msg.state or "").lower()
                detail = msg.detail or ""
                badge = agent_badge(agent)
                metrics.set_agent(agent, badge, state)
                if state == "working":
                    metrics.set_phase("execute")
                    row = self._active_rows.get(agent)
                    if row is None:
                        row = log.add_entry(badge, detail or "working…", spinner=True)
                        self._active_rows[agent] = row
                    else:
                        log.update_row(row, content=detail or row.content, spinner=True)
                elif state == "done":
                    row = self._active_rows.get(agent)
                    if row is not None:
                        log.update_row(
                            row, spinner=False, content=detail or "complete", icon="✓"
                        )
                    else:
                        log.add_entry(badge, detail or "complete", icon="✓")
                elif state == "blocked":
                    row = self._active_rows.get(agent)
                    if row is not None:
                        log.update_row(
                            row, badge="ERROR", spinner=False,
                            content=detail or "blocked", icon="✗",
                        )
                    else:
                        log.add_entry("ERROR", f"{badge}: {detail}", icon="✗")
                elif state == "waiting":
                    row = self._active_rows.get(agent)
                    if row is not None:
                        log.update_row(row, content=detail or "waiting…", spinner=True)
            elif msg.channel == Channel.FINDINGS:
                finding = msg.finding
                text = (
                    getattr(finding, "headline", None)
                    or getattr(finding, "summary", None)
                    or "finding recorded"
                )
                log.add_entry(agent_badge(msg.agent), str(text)[:90], icon="▸")
            elif msg.channel == Channel.HANDOFF:
                metrics.set_phase("handoff")
                log.add_entry(
                    "HANDOFF",
                    f"{agent_badge(msg.from_agent)} → {agent_badge(msg.to_agent)}",
                )
            elif msg.channel == Channel.ESCALATION:
                log.add_entry("WARN", f"{agent_badge(msg.agent)}: {msg.issue}", icon="▸")
        except Exception:
            pass

    # ── demo mode: premium animations without any API keys ─────────────────────

    def _start_demo(self) -> None:
        if self._engagement_task and not self._engagement_task.done():
            self._log().add_entry("WARN", "already running — cancel first (Ctrl+C)", icon="▸")
            return
        self.query_one("#prompt", PromptBar).set_busy(True)
        self._active_rows.clear()
        self._metrics().start(phase="decompose")
        self._engagement_task = asyncio.create_task(self._run_demo())

    async def _run_demo(self) -> None:
        """Simulated engagement so the motion/metrics layer is visible offline."""
        log = self._log()
        m = self._metrics()
        try:
            log.add_entry(
                "THINKING",
                "decomposing objective — routing to specialist agents",
                spinner=True,
            )
            await asyncio.sleep(1.0)
            m.set_phase("execute")
            for p in ("groq", "cerebras", "google"):
                m.touch_provider(p)

            plan = [
                ("market_analyst", "MARKET", "sizing the addressable EV market"),
                ("competitive_intel", "COMPETE", "mapping incumbents & new entrants"),
                ("financial_analyst", "FINANCE", "unit economics & capex model"),
                ("risk_analyst", "RISK", "policy, supply-chain & FX exposure"),
                ("regulatory_analyst", "REGULATORY", "FAME-II & state incentives"),
            ]
            rows: dict[str, LogRow] = {}
            for key, badge, task in plan:
                m.set_agent(key, badge, "working")
                rows[key] = log.add_entry(badge, task, spinner=True)
                m.add_tool_call(random.randint(1, 3))
                m.add_tokens(random.randint(1200, 4200))
                await asyncio.sleep(0.7)

            log.add_entry("TOOL", "web.search · fetching 12 sources", aurora=True)
            await asyncio.sleep(1.4)

            for key, badge, _ in plan:
                m.set_agent(key, badge, "done")
                log.update_row(rows[key], spinner=False, content="analysis complete", icon="✓")
                m.add_tokens(random.randint(800, 2600))
                await asyncio.sleep(0.4)

            m.set_phase("synthesize")
            m.set_agent("synthesis_lead", "SYNTHESIS", "working")
            srow = log.add_entry("SYNTHESIS", "reconciling findings", progress=(0, 5))
            for step in range(1, 6):
                log.update_row(srow, progress=(step, 5))
                m.add_tokens(random.randint(1500, 3000))
                await asyncio.sleep(0.5)
            log.update_row(srow, progress=None, content="synthesis complete", icon="✓")
            m.set_agent("synthesis_lead", "SYNTHESIS", "done")

            m.set_phase("quality")
            log.add_entry(
                "DONE",
                "engagement complete · 11s  (demo)",
                detail=[
                    "recommendation → ENTER, staged  (high confidence)",
                    "quality → 4.6/5.0 · 1 iteration",
                    "pdf → reports/demo_ev_market.pdf",
                ],
                icon="✓",
            )
            m.finish(ok=True)
        except asyncio.CancelledError:
            log.add_entry("WARN", "demo cancelled", icon="▸")
            m.finish(ok=False)
            raise
        finally:
            try:
                self.query_one("#prompt", PromptBar).set_busy(False)
            except Exception:
                pass

    # ── lightweight commands ────────────────────────────────────────────────────

    def _show_roster(self) -> None:
        self._log().write_block(roster_content(online=len(ROSTER)), blank_after=1)
        self._log().scroll_end(animate=True)

    def _run_providers(self) -> None:
        log = self._log()
        try:
            from hyperion.router.router import get_router

            router = get_router()
            health = router.get_provider_health()
            up = [str(k).split(".")[-1].lower() for k, v in health.items() if v.get("available")]
            if up:
                log.add_entry("READY", "providers online: " + " · ".join(up))
                for p in up:
                    self._metrics().touch_provider(p)
            else:
                log.add_entry("WARN", "no providers report available — check API keys", icon="▸")
        except Exception as exc:
            log.add_entry("WARN", f"provider status unavailable: {exc}", icon="▸")

    def _run_vault(self, value: str) -> None:
        log = self._log()
        parts = value.split(None, 1)
        query = parts[1] if len(parts) > 1 else ""
        if not query:
            log.add_entry("WARN", "usage: /vault <search query>", icon="▸")
            return
        log.add_entry("SYSTEM", f"vault search: \u201c{query}\u201d — 0 prior entries", icon="▸")

    def _show_help(self) -> None:
        log = self._log()
        for line_text in _HELP_LINES:
            log.add_entry("SYSTEM", line_text)

    # ── actions ──────────────────────────────────────────────────────────────────

    def on_clear_scrollback(self, event: ClearScrollback) -> None:
        self.action_clear()

    def on_cancel_turn(self, event: CancelTurn) -> None:
        self.action_cancel()

    def action_clear(self) -> None:
        self._log().clear()
        self._active_rows.clear()
        self._metrics().reset()
        self._render_intro()
        self.set_timer(0.1, self._show_ready)

    def action_cancel(self) -> None:
        if self._engagement_task and not self._engagement_task.done():
            self._engagement_task.cancel()
            self._log().add_entry("WARN", "agent turn cancelled", icon="▸")
        self.query_one("#prompt", PromptBar).set_busy(False)

    def action_scroll_top(self) -> None:
        self._log().scroll_home(animate=True)

    def action_scroll_bottom(self) -> None:
        self._log().scroll_end(animate=True)

    def action_help(self) -> None:
        self._show_help()

    def action_quit(self) -> None:
        self.app.exit()
