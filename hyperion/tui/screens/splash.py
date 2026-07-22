"""HYPERION splash screen — boot display with provider/vault/searxng/obscura status.

Per ARCHITECTURE.md §8.2:

    Splash Screen:
    - HYPERION wordmark (large, Parchment on Obsidian)
    - Tagline: "many minds. one reading."
    - Animated Mark (Dormant state — slow pulse)
    - Provider status (4 providers with green/red indicator)
    - Vault status (Obsidian vault connected/missing)
    - SearxNG status (Docker running/stopped)
    - Obscura status (binary found/missing)
    - Press any key to start

This screen runs the boot sequence (Docker/SearxNG auto-start, provider health,
roster init, vault prime) and transitions to the engagement screen when ready.
"""

from __future__ import annotations

import asyncio
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Static

from hyperion.tui.banner import WORDMARK, TAGLINE
from hyperion.tui.content import build, build_line, line, span
from hyperion.tui.motion.color import ramp
from hyperion.tui.theme import (
    BG_CANVAS,
    BORDER_SUBTLE,
    CLAY,
    CLAY_DEEP,
    CLAY_SOFT,
    LOGO_STOPS,
    ROSE,
    SAGE,
    SIG_ERROR,
    SIG_SUCCESS,
    SIG_WARN,
    TEXT_DIM,
    TEXT_GHOST,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
)

_LOGO_WIDTH = max(len(s) for s in WORDMARK)


def _wordmark_content():
    """Gradient wordmark as Content spans."""
    lines: list = []
    for s in WORDMARK:
        row = []
        for x, ch in enumerate(s):
            if ch == " ":
                row.append(span(" ", ""))
            else:
                color = ramp(LOGO_STOPS, x / max(1, _LOGO_WIDTH - 1))
                row.append(span(ch, color))
        lines.append(row)
    lines.append(line(""))
    lines.append([span("  " + TAGLINE, f"bold {TEXT_SECONDARY}")])
    lines.append([span('  many minds. one reading.', TEXT_DIM)])
    return build(lines)


class SplashStatus(Static):
    """Status line for one boot component."""

    def __init__(self, label: str, **kwargs) -> None:
        self._label = label
        self._status: str = "checking…"
        self._ok: bool | None = None
        super().__init__(self._build(), **kwargs)

    def set_status(self, status: str, ok: bool | None = None) -> None:
        self._status = status
        self._ok = ok
        self.update(self._build())

    def _build(self):
        if self._ok is True:
            icon, color = "●", SIG_SUCCESS
        elif self._ok is False:
            icon, color = "●", SIG_ERROR
        else:
            icon, color = "◐", SIG_WARN
        return build_line(
            span(f"  {icon} ", color),
            span(f"{self._label.ljust(12)}  ", TEXT_DIM),
            span(self._status, TEXT_PRIMARY),
        )


class SplashScreen(Screen):
    """HYPERION splash — wordmark, tagline, Mark, system status, press any key."""

    DEFAULT_CSS = """
    SplashScreen {
        layout: vertical;
        background: #141413;
        color: #F4F3EE;
        align: center top;
    }
    #splash-logo {
        height: auto;
        min-height: 8;
        width: 100%;
        content-align: center top;
        padding: 2 0;
    }
    #splash-mark {
        height: 1;
        width: 100%;
        content-align: center middle;
    }
    #splash-status {
        height: auto;
        min-height: 6;
        width: 100%;
        padding: 1 0;
    }
    #splash-hint {
        height: 1;
        width: 100%;
        content-align: center middle;
    }
    """

    BINDINGS = [
        Binding("escape", "skip", "Skip", show=False),
    ]

    def __init__(self, reduced_motion: bool = False, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._reduced = reduced_motion
        self._boot_done = False
        self._boot_task: asyncio.Task | None = None

    def compose(self) -> ComposeResult:
        yield Static(_wordmark_content(), id="splash-logo")
        # Mark widget in dormant state
        from hyperion.tui.widgets.mark import Mark, MarkState
        mark = Mark(id="splash-mark")
        mark.set_state(MarkState.DORMANT)
        yield mark
        with Vertical(id="splash-status"):
            yield SplashStatus("PROVIDERS", id="stat-providers")
            yield SplashStatus("VAULT", id="stat-vault")
            yield SplashStatus("SEARXNG", id="stat-searxng")
            yield SplashStatus("OBSCURA", id="stat-obscura")
        yield Static(build_line(span("  press any key to start  ·  esc to skip", TEXT_GHOST)), id="splash-hint")

    def on_mount(self) -> None:
        self._boot_task = asyncio.create_task(self._run_boot())

    async def _run_boot(self) -> None:
        """Run boot checks and update status lines."""
        await asyncio.sleep(0.3 if not self._reduced else 0.05)

        # ── Providers ──────────────────────────────────────────────────────────
        prov = self.query_one("#stat-providers", SplashStatus)
        try:
            from hyperion.router.router import get_router
            router = get_router()
            health = router.get_provider_health()
            up = [str(k).split(".")[-1].lower() for k, v in health.items() if v.get("available")]
            down = [str(k).split(".")[-1].lower() for k, v in health.items() if not v.get("available")]
            if up:
                prov.set_status(f"online: {', '.join(up)}", ok=True)
            else:
                prov.set_status("no providers available — check API keys", ok=False)
        except Exception as e:
            prov.set_status(f"check failed: {e!s:.40}", ok=False)

        await asyncio.sleep(0.2 if not self._reduced else 0.02)

        # ── Vault ───────────────────────────────────────────────────────────────
        vault = self.query_one("#stat-vault", SplashStatus)
        try:
            from hyperion.config import get_settings
            from pathlib import Path
            settings = get_settings()
            vault_path = getattr(settings, "second_brain_vault", None)
            if vault_path and Path(vault_path).exists():
                vault.set_status(f"connected · {vault_path}", ok=True)
            else:
                vault.set_status("not found (optional)", ok=None)
        except Exception:
            vault.set_status("check skipped", ok=None)

        await asyncio.sleep(0.2 if not self._reduced else 0.02)

        # ── SearxNG ─────────────────────────────────────────────────────────────
        searx = self.query_one("#stat-searxng", SplashStatus)
        try:
            import shutil
            docker_path = shutil.which("docker")
            if docker_path is None:
                searx.set_status("Docker not installed — Jina fallback", ok=None)
            else:
                searx.set_status("checking Docker…", ok=None)
                from hyperion.tui.boot import _run_subprocess
                rc, out, _ = await _run_subprocess(
                    ["docker", "ps", "--filter", "name=searxng", "--format", "{{.Status}}"],
                    timeout=8,
                )
                if rc == 0 and "Up" in out:
                    searx.set_status("running · localhost:8888", ok=True)
                else:
                    searx.set_status("container stopped — will auto-start", ok=None)
        except Exception:
            searx.set_status("check skipped", ok=None)

        await asyncio.sleep(0.2 if not self._reduced else 0.02)

        # ── Obscura ─────────────────────────────────────────────────────────────
        obs = self.query_one("#stat-obscura", SplashStatus)
        try:
            import shutil
            obscura_path = shutil.which("obscura")
            if obscura_path:
                obs.set_status(f"found · {obscura_path}", ok=True)
            else:
                # Check obscura-bin directory
                from pathlib import Path
                here = Path(__file__).resolve()
                for parent in here.parents:
                    candidate = parent / "obscura-bin"
                    if candidate.exists() and any(candidate.iterdir()):
                        obs.set_status(f"found · {candidate}", ok=True)
                        break
                else:
                    obs.set_status("not in PATH (optional for JS pages)", ok=None)
        except Exception:
            obs.set_status("check skipped", ok=None)

        # ── Done ────────────────────────────────────────────────────────────────
        self._boot_done = True
        try:
            hint = self.query_one("#splash-hint", Static)
            hint.update(build_line(
                span("  press any key to start  ·  ", SAGE),
                span("all systems checked", f"bold {SAGE}"),
            ))
        except Exception:
            pass

    def on_key(self, event) -> None:
        """Any key transitions to the engagement screen."""
        if self._boot_task and not self._boot_task.done():
            self._boot_task.cancel()
        self.app.pop_screen()

    def action_skip(self) -> None:
        if self._boot_task and not self._boot_task.done():
            self._boot_task.cancel()
        self.app.pop_screen()
