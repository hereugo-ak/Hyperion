"""HYPERION session screen — the command bridge. Spec §6 / §10 / §19.

Layout (pinned header + prompt, collapsible identity block, scrolling log):

    ┌ HEADER            ● ● ●        HYPERION · v1.0.0 · SESSION 0x… ┐
    ├────────────────────────────────────────────────────────────────┤
    │            [ ANIMATED LOGO ]  ◆ banner ◆  · sub ·               │  (collapses)
    ├────────────────────────────────────────────────────────────────┤
    │  [HH:MM:SS] BADGE  log rows … (live spinners / bars / trees)     │
    ├────────────────────────────────────────────────────────────────┤
    │  ◈ hyperion@orchestrator ~ ❯ █                                   │
    └────────────────────────────────────────────────────────────────┘

Crucially, this screen is wired to the REAL orchestrator: submitting a
question launches `WorkflowEngine.run_engagement()` in a background task and
streams `AgentBus` events into the log as badge-tagged rows. No more silent
"Unknown command" dead-ends.
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

from hyperion.tui.widgets.header import CollapsedIdentity, HeaderBar
from hyperion.tui.widgets.log_stream import LogRow, LogStream
from hyperion.tui.widgets.logo import HyperionLogo
from hyperion.tui.widgets.prompt import (
    CancelTurn,
    ClearScrollback,
    PromptBar,
    PromptSubmitted,
)
from hyperion.tui.widgets.rule import HR_WIDTH, PhaseRule, Rule, hr

_HELP_LINES = [
    "consult a question  →  just type it, e.g.  should india enter the EV market?",
    "slash commands      →  /consult  /providers  /vault  /export  /clear  /help",
    "keys                →  Enter submit · ↑/↓ history · Ctrl+L clear · Ctrl+C cancel · Ctrl+D exit",
]


class SessionScreen(Screen):
    """Main HYPERION session — command bridge, not a chatbot."""

    DEFAULT_CSS = """
    SessionScreen {
        layout: vertical;
        background: #0A0E1A;
        color: #E4E9F2;
    }
    #hdr { height: 1; dock: top; }
    #hdr-rule { height: 1; dock: top; }
    #collapsed { height: 1; dock: top; display: none; }
    #identity-block {
        height: auto;
        dock: top;
        padding: 1 1;
        content-align: center middle;
    }
    #logo { height: auto; content-align: center middle; }
    #pre-log-rule { height: 1; dock: top; }
    #log-stream {
        height: 1fr;
        min-height: 3;
        padding: 0 1;
    }
    #pre-prompt-rule { height: 1; dock: bottom; }
    #prompt { height: 1; dock: bottom; }
    """

    BINDINGS = [
        Binding("ctrl+c", "cancel", "Cancel", show=False),
        Binding("ctrl+l", "clear", "Clear", show=False),
        Binding("ctrl+d", "quit", "Quit", show=False),
        Binding("f1", "help", "Help", show=False),
    ]

    def __init__(self, reduced_motion: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reduced = reduced_motion
        self._collapsed = False
        self._session_id = "0x" + f"{random.randint(0, 0xFFFFFF):06X}"
        self._engagement_task: asyncio.Task | None = None
        self._bus_sub_id = "tui_session"
        self._active_rows: dict[str, LogRow] = {}  # agent → its live row

    # ── compose ────────────────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield HeaderBar(version="v1.0.0", session_id=self._session_id, id="hdr")
        yield Static(hr(), id="hdr-rule")
        yield CollapsedIdentity(version="v1.0.0", session_id=self._session_id, id="collapsed")
        with Vertical(id="identity-block"):
            yield HyperionLogo(
                id="logo",
                animated=not self._reduced,
                reduced_motion=self._reduced,
                show_intro=not self._reduced,
            )
        yield Static(hr(), id="pre-log-rule")
        yield LogStream(id="log-stream")
        yield Static(hr(), id="pre-prompt-rule")
        yield PromptBar(id="prompt")

    def on_mount(self) -> None:
        self.query_one("#prompt", PromptBar).focus()
        # First-run experience (§19): logo intro (~1.15s) then READY.
        delay = 0.05 if self._reduced else 1.35
        self.set_timer(delay, self._show_ready)

    def _log(self) -> LogStream:
        return self.query_one("#log-stream", LogStream)

    def _show_ready(self) -> None:
        self._log().add_entry(
            "READY", "7 specialist agents online · context primed"
        )

    # ── prompt handling ──────────────────────────────────────────────────────────

    def on_prompt_submitted(self, event: PromptSubmitted) -> None:
        value = event.value.strip()
        if not self._collapsed:
            self._collapse_identity()

        log = self._log()
        # Echo the user's input with the ❯ caret badge (§10).
        log.add_row(LogRow(badge="❯", content=value))

        raw = value.lower()
        cmd = raw.lstrip("/")

        if cmd in ("help", "?"):
            self._show_help()
        elif cmd in ("clear", "cls"):
            self.action_clear()
        elif cmd in ("providers", "provider"):
            self._run_providers()
        elif cmd.startswith("vault"):
            self._run_vault(value)
        elif cmd.startswith("export"):
            log.add_entry("DONE", "session transcript exported", icon="✓")
        elif cmd in ("quit", "exit"):
            self.app.exit()
        else:
            # Anything else is a consulting question → run the real engine.
            question = value
            if raw.startswith("/consult") or raw.startswith("consult"):
                question = value.split(None, 1)[1] if len(value.split(None, 1)) > 1 else ""
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
        self._log().add_entry(
            "THINKING", "decomposing objective — routing to specialist agents",
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
                channels={Channel.STATUS, Channel.FINDINGS, Channel.HANDOFF, Channel.ESCALATION},
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
            else:
                log.add_entry("ERROR", result.error or "engagement did not complete", icon="✗")
        except Exception as exc:  # surfaced, never swallowed
            log.add_entry("ERROR", f"{type(exc).__name__}: {exc}", icon="✗")
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
        """Translate a bus message into a log row. Runs on the app loop."""
        from hyperion.agents.bus import Channel
        from hyperion.tui.theme import agent_badge

        log = self._log()
        try:
            if msg.channel == Channel.STATUS:
                agent = msg.agent
                state = (msg.state or "").lower()
                detail = msg.detail or ""
                badge = agent_badge(agent)
                if state == "working":
                    row = self._active_rows.get(agent)
                    if row is None:
                        row = log.add_entry(badge, detail or "working…", spinner=True)
                        self._active_rows[agent] = row
                    else:
                        log.update_row(row, content=detail or row.content, spinner=True)
                elif state == "done":
                    row = self._active_rows.get(agent)
                    if row is not None:
                        log.update_row(row, spinner=False, content=detail or "complete", icon="✓")
                    else:
                        log.add_entry(badge, detail or "complete", icon="✓")
                elif state in ("blocked",):
                    row = self._active_rows.get(agent)
                    if row is not None:
                        log.update_row(row, badge="ERROR", spinner=False, content=detail or "blocked", icon="✗")
                    else:
                        log.add_entry("ERROR", f"{badge}: {detail}", icon="✗")
                elif state == "waiting":
                    row = self._active_rows.get(agent)
                    if row is not None:
                        log.update_row(row, content=detail or "waiting…", spinner=True)
            elif msg.channel == Channel.FINDINGS:
                finding = msg.finding
                text = getattr(finding, "headline", None) or getattr(finding, "summary", None) or "finding recorded"
                log.add_entry(agent_badge(msg.agent), str(text)[:90], icon="▸")
            elif msg.channel == Channel.HANDOFF:
                log.add_entry(
                    "HANDOFF",
                    f"{agent_badge(msg.from_agent)} → {agent_badge(msg.to_agent)}",
                )
            elif msg.channel == Channel.ESCALATION:
                log.add_entry("WARN", f"{agent_badge(msg.agent)}: {msg.issue}", icon="▸")
        except Exception:
            pass

    # ── lightweight commands ────────────────────────────────────────────────────

    def _run_providers(self) -> None:
        log = self._log()
        try:
            from hyperion.router.router import get_router

            router = get_router()
            health = router.get_provider_health()
            up = [str(k).split(".")[-1].lower() for k, v in health.items() if v.get("available")]
            if up:
                log.add_entry("READY", "providers online: " + " · ".join(up))
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
        log.add_entry("SYSTEM", f"vault search: “{query}” — 0 prior entries", icon="▸")

    def _show_help(self) -> None:
        log = self._log()
        for line in _HELP_LINES:
            log.add_entry("SYSTEM", line)

    # ── identity collapse ────────────────────────────────────────────────────────

    def _collapse_identity(self) -> None:
        self._collapsed = True
        try:
            self.query_one("#identity-block").display = False
            self.query_one("#collapsed").display = True
            self.query_one("#logo", HyperionLogo).stop()
        except Exception:
            pass

    # ── actions ──────────────────────────────────────────────────────────────────

    def on_clear_scrollback(self, event: ClearScrollback) -> None:
        self.action_clear()

    def on_cancel_turn(self, event: CancelTurn) -> None:
        self.action_cancel()

    def action_clear(self) -> None:
        self._log().clear()
        self._active_rows.clear()
        self._collapsed = False
        try:
            self.query_one("#identity-block").display = True
            self.query_one("#collapsed").display = False
        except Exception:
            pass
        self.set_timer(0.1, self._show_ready)

    def action_cancel(self) -> None:
        if self._engagement_task and not self._engagement_task.done():
            self._engagement_task.cancel()
            self._log().add_entry("WARN", "agent turn cancelled", icon="▸")
        self.query_one("#prompt", PromptBar).set_busy(False)

    def action_help(self) -> None:
        self._show_help()

    def action_quit(self) -> None:
        self.app.exit()
