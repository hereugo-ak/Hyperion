"""HYPERION CLI — Typer entry point.

Two primary modes:
  hyperion shell               → launch the premium TUI command bridge (§6)
  hyperion consult "<q>"       → run an engagement non-interactively → PDF

Plus:  providers · vault · export · resume · help

The TUI (`shell`) is the flagship experience. `consult` is the headless path
for scripting / CI. Both drive the SAME real WorkflowEngine.
"""

from __future__ import annotations

import asyncio
import json as json_module
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from hyperion.config import ProviderType, get_settings

app = typer.Typer(
    name="hyperion",
    help="HYPERION — multi-agent consulting system. Orchestration · reasoning · synthesis.",
    no_args_is_help=True,
    rich_markup_mode="rich",
)

console = Console()

# Brand palette (§4.1) for Rich (non-TUI) output.
CYAN = "#00D9FF"
VIOLET = "#8B5CF6"
MAGENTA = "#F0ABFC"
SUCCESS = "#10D9A0"
WARN = "#FFB627"
ERROR = "#FF5C7A"
DIM = "#6B7A99"


def _banner() -> None:
    t = Text()
    t.append("HYPERION", style=f"bold {CYAN}")
    t.append("  ◆ ", style=VIOLET)
    t.append("multi-agent consulting system", style=DIM)
    console.print(t)
    console.print(Text("orchestration · reasoning · synthesis", style=DIM))
    console.print()


# ── shell ─────────────────────────────────────────────────────────────────────


@app.command()
def shell(
    reduced_motion: bool = typer.Option(
        False, "--reduced-motion", help="Disable shimmer/sweep/spinners (accessibility)."
    ),
    demo: bool = typer.Option(
        False, "--demo", help="Start in demo mode — preview the interface with no API keys."
    ),
    no_mouse: bool = typer.Option(
        False,
        "--no-mouse",
        help=(
            "Do not capture the mouse, so your terminal's own click-drag "
            "text selection & copy keep working (classic conhost / PowerShell)."
        ),
    ),
) -> None:
    """Launch the HYPERION TUI — the interactive command bridge.

    Copy text with drag-to-highlight + Ctrl+Shift+C (works in Windows Terminal,
    iTerm2, kitty, WezTerm). On terminals where that is blocked, relaunch with
    ``--no-mouse`` and use your terminal's native selection instead.
    """
    try:
        from hyperion.tui.app import HyperionApp
    except ImportError as exc:
        console.print(f"[{ERROR}]Textual not installed. Run: pip install textual rich[/{ERROR}]")
        # `from exc` (ruff B904): an ImportError here is not always "textual is
        # missing" — it is just as often a broken import *inside* the TUI
        # package. Chaining keeps that traceback reachable instead of replacing
        # it with a misleading install hint.
        raise typer.Exit(code=1) from exc

    # Teardown is guaranteed here as well as inside the app.
    #
    # The TUI tears down in `action_quit` / `on_unmount`, which covers a normal
    # quit. Neither runs if the process dies another way: Ctrl+C during the boot
    # sequence, an unhandled exception in a screen, or a terminal that closes
    # the pty. Those paths previously left `searxng` and `flaresolverr` running
    # with no user-visible sign, and the user's requirement is that quitting the
    # shell terminates everything.
    #
    # `stop_services` is idempotent (docker stop/rm of an absent container is a
    # no-op) and the app marks itself stopped, so the common case does not pay
    # for this twice.
    #
    # BaseException, not Exception: SystemExit and KeyboardInterrupt both inherit
    # from BaseException, and a bare `except Exception` here let a SystemExit
    # raised inside Textual skip straight past teardown. The `finally` covers it,
    # but the explicit handler is what lets the user see *why* the shell ended
    # before the (potentially slow) container teardown output appears.
    try:
        HyperionApp(reduced_motion=reduced_motion, demo=demo, mouse=not no_mouse).run(
            mouse=not no_mouse
        )
    except KeyboardInterrupt:
        console.print(f"[{DIM}]interrupted — shutting services down…[/{DIM}]")
    except Exception as exc:  # noqa: BLE001 - report, then still tear down
        console.print(f"[{ERROR}]shell error: {type(exc).__name__}: {exc}[/{ERROR}]")
        raise
    finally:
        _ensure_services_stopped()


def _ensure_services_stopped() -> None:
    """Best-effort synchronous teardown for the process-exit path.

    Runs on its own event loop because by this point Textual's loop is gone.
    Never raises: an exception escaping here would replace whatever error
    actually terminated the shell, hiding the real cause.

    Reports what was actually torn down rather than staying silent, because the
    user's complaint was precisely that containers outlived the shell — a claim
    that is impossible to check without the shell saying what it did.
    """
    try:
        from hyperion.tui.boot import stop_services

        asyncio.run(stop_services())
    except Exception as exc:  # noqa: BLE001 - must not mask the original failure
        console.print(
            f"[{WARN}]warning: could not confirm service shutdown "
            f"({type(exc).__name__}). Check `docker ps`.[/{WARN}]"
        )
        return

    # Verify rather than assume. If a container survived teardown, say so with
    # the exact command to fix it instead of leaving it running silently.
    try:
        from hyperion.infra.services import docker_available, running_containers

        if docker_available():
            leftover = asyncio.run(running_containers())
            if leftover:
                names = " ".join(sorted(leftover))
                console.print(
                    f"[{WARN}]these containers are still running: {names} — "
                    f"remove with `docker rm -f {names}`[/{WARN}]"
                )
    except Exception:  # noqa: BLE001 - verification is advisory only
        pass


@app.command()
def boot(
    reduced_motion: bool = typer.Option(False, "--reduced-motion"),
    demo: bool = typer.Option(False, "--demo"),
    no_mouse: bool = typer.Option(False, "--no-mouse"),
) -> None:
    """Alias for `shell`."""
    shell(reduced_motion=reduced_motion, demo=demo, no_mouse=no_mouse)


# ── consult ─────────────────────────────────────────────────────────────────────


@app.command()
def consult(
    question: str = typer.Argument(..., help="The business question to analyze."),
    context: str = typer.Option("", "--context", "-c", help="Extra engagement context."),
    output: str = typer.Option("", "--output", "-o", help="PDF output path."),
    markdown: bool = typer.Option(False, "--markdown", "-m", help="Also export markdown."),
) -> None:
    """Run a full engagement non-interactively and save the report."""
    _banner()
    console.print(
        Panel(
            Text(question, style="bold"),
            title=Text("engagement", style=VIOLET),
            border_style=DIM,
        )
    )
    console.print()

    result = asyncio.run(_run_engagement(question, context, output))

    if result.success:
        lines = [
            Text.assemble(("recommendation  "
                "", DIM), (str(getattr(getattr(result.final_report, "recommendation", None), "value", "—")), f"bold {SUCCESS}")),
        ]
        if result.pdf_path:
            lines.append(Text.assemble(("pdf             ", DIM), (result.pdf_path, MAGENTA)))
        if result.quality_score is not None:
            lines.append(Text.assemble(("quality         "
                "", DIM), (f"{result.quality_score.weighted_total:.1f}/5.0", CYAN)))
        lines.append(Text.assemble(("duration        "
            "", DIM), (f"{result.duration_seconds:.0f}s", DIM)))
        body = Text("\n").join(lines)
        console.print(Panel(body, title=Text("done", style=SUCCESS), border_style=SUCCESS))
        if markdown and result.markdown_path:
            console.print(f"[{DIM}]markdown → {result.markdown_path}[/{DIM}]")
    else:
        console.print(Panel(Text(result.error or "engagement "
            "failed", style=ERROR), title=Text("error", style=ERROR), border_style=ERROR))
        raise typer.Exit(code=1)


async def _run_engagement(question: str, context: str, output_path: str) -> Any:
    from hyperion.orchestrator import WorkflowEngine
    from hyperion.tui.boot import (
        ensure_docker_ready,
        reset_process_state,
        start_services,
        stop_services,
    )

    # Fresh start, same as the shell.
    #
    # This function used to inline its own copy of the container launch: its own
    # settings-path resolvers, its own run argv, its own image strings — and
    # those strings had drifted to unpinned `:latest` while docker-compose.yml
    # and boot.py were pinned. `start_services()` is now the single launcher, so
    # a pin change cannot be applied to one entry point and missed by the other.
    reset_process_state()

    # The headless path needs the engine started too. `consult` previously
    # assumed a running daemon, so a scripted/CI run on a machine where the
    # engine was idle produced an engagement with no search at all — reported
    # only as thin sourcing, never as a startup error.
    engine_ready = await ensure_docker_ready(
        on_progress=lambda msg: console.print(f"[{DIM}]{msg}[/{DIM}]")
    )
    if engine_ready:
        started = await start_services()
        down = [name for name, ok in started.items() if not ok]
        if down:
            console.print(
                f"[{WARN}]degraded: {', '.join(down)} did not come up — "
                f"search coverage will be reduced[/{WARN}]"
            )
    else:
        console.print(
            f"[{WARN}]container engine unavailable — running with fallback "
            f"search only[/{WARN}]"
        )

    engine = WorkflowEngine()
    try:
        result = await engine.run_engagement(question=question, conversation_context=context)
        if output_path and result.pdf_path:
            import shutil

            shutil.move(result.pdf_path, output_path)
            result.pdf_path = output_path
        return result
    finally:
        await engine.close()
        # Stop all services on exit (Docker containers + global tool clients)
        await stop_services()


# ── providers ────────────────────────────────────────────────────────────────


@app.command()
def providers() -> None:
    """Show LLM provider status and rate limits."""
    _banner()
    from hyperion.router.router import get_router

    router = get_router()
    health = router.get_provider_health()
    tpm_status = router.get_tpm_status()
    budget_status = router.get_budget_status()

    table = Table(border_style=DIM, header_style=f"bold {CYAN}", title=Text("providers", style=VIOLET))
    table.add_column("provider", style=f"bold {CYAN}")
    table.add_column("status", justify="center")
    table.add_column("tpm", justify="right")
    table.add_column("budget", justify="right")
    table.add_column("uptime", justify="right")

    for pt in ProviderType:
        h = health.get(pt, {})
        tpm = tpm_status.get(pt, {})
        budget = budget_status.get(pt, {})
        available = h.get("available", False)
        status = f"[{SUCCESS}]● online[/{SUCCESS}]" if available else f"[{ERROR}]● offline[/{ERROR}]"
        table.add_row(
            pt.value.upper(),
            status,
            f"{tpm.get('percentage', 0.0):.0f}%",
            f"{budget.get('percentage', 0.0):.0f}%",
            f"{h.get('uptime_pct', 0.0):.0f}%",
        )
    console.print(table)


# ── vault ────────────────────────────────────────────────────────────────────


@app.command()
def vault(
    query: str = typer.Argument(..., help="Search query for the Second Brain vault."),
    limit: int = typer.Option(10, "--limit", "-n"),
) -> None:
    """Search the Second Brain (Obsidian vault) for prior research."""
    _banner()
    try:
        from hyperion.tools.second_brain import SecondBrainClient

        client = SecondBrainClient(settings=get_settings())
        res = asyncio.run(client.search(query))
        if not res or not res.notes:
            console.print(f"[{DIM}]no results for “{query}”[/{DIM}]")
            return
        table = Table(border_style=DIM, header_style=f"bold {CYAN}", title=Text(f"vault: {query}", style=VIOLET))
        table.add_column("#", justify="right", style=DIM)
        table.add_column("title", style="bold")
        table.add_column("relevance", justify="right")
        for i, (note, rel) in enumerate(res.notes[:limit], 1):
            table.add_row(str(i), note.title or "untitled", f"{rel:.2f}")
        console.print(table)
    except Exception as exc:
        console.print(f"[{ERROR}]vault error: {exc}[/{ERROR}]")
        raise typer.Exit(code=1) from exc


# ── export ───────────────────────────────────────────────────────────────────


@app.command()
def export(
    format: str = typer.Argument("markdown", help="pdf | markdown | json"),
    output: str = typer.Option("", "--output", "-o"),
) -> None:
    """Export the most recent report."""
    _banner()
    fmt = format.lower().strip()
    if fmt not in ("pdf", "markdown", "json"):
        console.print(f"[{ERROR}]invalid format '{fmt}'. use: pdf | markdown | json[/{ERROR}]")
        raise typer.Exit(code=1)

    # `Path("reports")` is relative to the shell's CWD, so `hyperion export`
    # from any directory other than the project root reported "no engagement
    # data found" for reports that plainly existed. Settings.reports_dir is
    # absolutised at validation time, so this now finds them from anywhere.
    reports = Path(get_settings().reports_dir)
    json_files = sorted(reports.glob("hyperion_report_*.json"), key=lambda p: p.stat().st_mtime, reverse=True) if reports.exists() else []
    if not json_files:
        console.print(f"[{DIM}]no engagement data found in reports/. run `hyperion consult` first.[/{DIM}]")
        raise typer.Exit(code=1)

    with open(json_files[0], encoding="utf-8") as f:
        data = json_module.load(f)

    out = output or str(reports / f"hyperion_export.{ 'md' if fmt == 'markdown' else fmt }")
    if fmt == "json":
        with open(out, "w", encoding="utf-8") as f:
            json_module.dump(data, f, indent=2, ensure_ascii=False)
    elif fmt == "markdown":
        from hyperion.output.markdown import MarkdownExporter

        MarkdownExporter().export_to_file(data.get("final_report", {}), file_path=out)
    else:
        # fmt == "pdf". The dead-template fork (fix 3.3) is gone, so there is
        # no longer a second Jinja2 path that could re-render a PDF here. The
        # live pipeline writes the PDF directly during `hyperion consult` —
        # surface that PDF rather than pretending to regenerate one.
        pdf_files = sorted(
            reports.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True
        ) if reports.exists() else []
        if pdf_files:
            console.print(
                f"[{DIM}]the latest report PDF was already generated by `hyperion consult`:[/{DIM}]"
            )
            console.print(f"[{SUCCESS}]{pdf_files[0]}[/{SUCCESS}]")
        else:
            console.print(
                f"[{ERROR}]no PDF found in {reports}. PDFs are produced by the "
                f"live `hyperion consult` pipeline, not by export.[/{ERROR}]"
            )
            raise typer.Exit(code=1)
        return
    console.print(f"[{SUCCESS}]exported → {out}[/{SUCCESS}]")


# ── help ─────────────────────────────────────────────────────────────────────


@app.command()
def help() -> None:
    """Show available commands."""
    _banner()
    table = Table(border_style=DIM, header_style=f"bold {CYAN}")
    table.add_column("command", style=f"bold {CYAN}")
    table.add_column("description", style=DIM)
    table.add_row("shell", "launch the interactive TUI command bridge")
    table.add_row("consult <q>", "run an engagement non-interactively → PDF")
    table.add_row("providers", "show LLM provider status and rate limits")
    table.add_row("vault <query>", "search the Second Brain for prior research")
    table.add_row("export <fmt>", "export the latest report (pdf | markdown | json)")
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()
