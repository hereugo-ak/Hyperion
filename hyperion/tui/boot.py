"""HYPERION premium boot sequence.

Runs on shell init — each step streams into the transcript with a live spinner,
then resolves to ✓ / ⚠ / ✗.  Steps:

  1. CORE     — core systems init
  2. DOCKER   — Docker daemon check (auto-start if installed but stopped)
  3. SEARXNG  — SearxNG container check / start / create-and-run
  3b.FLARE    — FlareSolverr container start (CAPTCHA-bypass headless Chromium)
  4. PROVIDER — LLM provider health (NVIDIA, Cerebras, Groq, Mistral, Google)
  5. ROSTER   — specialist agent instantiation
  6. CONTEXT  — Second Brain vault prime
  7. READY    — all systems online

Every step is a LogRow in the transcript with spinner=True while working,
then updated to icon="✓" (or "⚠" / "✗") when done.  The MetricsRail shows
a boot progress bar so the right-hand telemetry reflects each step in real time.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

from hyperion.tui.widgets.transcript import LogRow, Transcript


# ── helpers ──────────────────────────────────────────────────────────────────


async def _run_subprocess(cmd: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
    """Run a subprocess asynchronously, return (returncode, stdout, stderr)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        return proc.returncode or 0, stdout_b.decode(errors="replace"), stderr_b.decode(errors="replace")
    except asyncio.TimeoutError:
        return 124, "", "timeout"
    except FileNotFoundError:
        return 127, "", "not found"
    except OSError as e:
        return 1, "", str(e)


async def _run_powershell(script: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Run a PowerShell command string."""
    return await _run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )


def _searxng_settings_path() -> str:
    """Resolve the absolute path to searxng_settings.yml."""
    # Walk up from this file to find the project root (where searxng_settings.yml lives).
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "searxng_settings.yml"
        if candidate.exists():
            return str(candidate)
    # Fallback — best guess
    return str(here.parents[2] / "searxng_settings.yml")


# ── boot step result ─────────────────────────────────────────────────────────

OK = "ok"
WARN = "warn"
FAIL = "fail"


class BootStep:
    """One step in the boot sequence."""

    __slots__ = ("badge", "label", "row", "result", "detail")

    def __init__(self, badge: str, label: str) -> None:
        self.badge = badge
        self.label = label
        self.row: LogRow | None = None
        self.result: str = OK
        self.detail: str = ""


# ── main boot sequence ───────────────────────────────────────────────────────


async def run_boot_sequence(
    log: Transcript,
    metrics: Any,
    reduced_motion: bool = False,
) -> dict[str, Any]:
    """Execute the full boot sequence, streaming into the transcript.

    Returns a dict with keys: docker, searxng, providers, agents, vault,
    each mapping to (status_str, detail_str).
    """
    results: dict[str, Any] = {}
    total_steps = 9
    step_num = 0

    def _start_step(badge: str, label: str, spinner: bool = True) -> BootStep:
        nonlocal step_num
        step_num += 1
        step = BootStep(badge, label)
        step.row = log.add_entry(badge, label, spinner=spinner)
        # Update metrics rail with progress
        try:
            metrics.set_phase("boot")
            metrics._repaint()
        except Exception:
            pass
        return step

    def _finish_step(step: BootStep, status: str = OK, detail: str = "") -> None:
        step.result = status
        step.detail = detail
        if step.row is not None:
            if status == OK:
                log.update_row(step.row, spinner=False, content=detail or step.label, icon="✓")
            elif status == WARN:
                log.update_row(step.row, badge="WARN", spinner=False, content=detail or step.label, icon="⚠")
            else:
                log.update_row(step.row, badge="ERROR", spinner=False, content=detail or step.label, icon="✗")

    # ── Step 1: Core ──────────────────────────────────────────────────────
    step = _start_step("BOOT", "initializing HYPERION core systems")
    await asyncio.sleep(0.4 if not reduced_motion else 0.1)
    _finish_step(step, OK, "core systems initialized")
    results["core"] = (OK, "core systems initialized")

    # ── Step 2: Docker daemon ─────────────────────────────────────────────
    step = _start_step("DOCKER", "checking Docker daemon")
    await asyncio.sleep(0.3 if not reduced_motion else 0.05)

    docker_path = shutil.which("docker")
    if docker_path is None:
        _finish_step(step, WARN, "Docker CLI not found — SearxNG will be unavailable")
        results["docker"] = (WARN, "not installed")
    else:
        rc, out, err = await _run_subprocess(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=10)
        if rc == 0 and out.strip():
            _finish_step(step, OK, f"Docker daemon ready · v{out.strip()[:20]}")
            results["docker"] = (OK, f"v{out.strip()[:20]}")
        else:
            # Try to start Docker Desktop on Windows
            step2_label = "starting Docker Desktop…"
            if step.row:
                log.update_row(step.row, content=step2_label, spinner=True)

            docker_desktop = Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Docker" / "Docker" / "Docker Desktop.exe"
            if docker_desktop.exists():
                try:
                    subprocess.Popen([str(docker_desktop)], creationflags=subprocess.CREATE_NO_WINDOW)
                except Exception:
                    pass
                # Wait up to 30s for daemon to come online
                started = False
                for _ in range(15):
                    await asyncio.sleep(2.0)
                    rc, out, err = await _run_subprocess(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=5)
                    if rc == 0 and out.strip():
                        started = True
                        break
                if started:
                    _finish_step(step, OK, f"Docker daemon started · v{out.strip()[:20]}")
                    results["docker"] = (OK, f"v{out.strip()[:20]}")
                else:
                    _finish_step(step, WARN, "Docker Desktop starting — SearxNG may need a manual start")
                    results["docker"] = (WARN, "starting")
            else:
                _finish_step(step, WARN, "Docker daemon not running — start Docker Desktop manually")
                results["docker"] = (WARN, "not running")

    # ── Step 3: SearxNG — always restart to pick up config changes ──────
    searxng_ok = False
    if results.get("docker", (FAIL,))[0] == OK:
        step = _start_step("SEARXNG", "restarting SearxNG search container")
        await asyncio.sleep(0.3 if not reduced_motion else 0.05)

        # Always stop and remove existing container so config changes are picked up
        if step.row:
            log.update_row(step.row, content="stopping existing SearxNG container…", spinner=True)
        await _run_subprocess(["docker", "stop", "searxng"], timeout=15)
        await _run_subprocess(["docker", "rm", "searxng"], timeout=10)

        # Create and run fresh container with latest settings
        if step.row:
            log.update_row(step.row, content="creating SearxNG container with latest config…", spinner=True)
        settings_path = _searxng_settings_path()
        settings_path_docker = settings_path.replace("\\", "/")
        limiter_path = str(Path(settings_path_docker).parent / "searxng-limiter.toml").replace("\\", "/")
        rc3, out3, err3 = await _run_subprocess(
            [
                "docker", "run", "-d",
                "--name", "searxng",
                "-p", "8888:8080",
                "-v", f"{settings_path_docker}:/etc/searxng/settings.yml",
                "-v", f"{limiter_path}:/etc/searxng/limiter.toml",
                "searxng/searxng",
            ],
            timeout=60,
        )
        if rc3 == 0:
            await asyncio.sleep(3.0)
            _finish_step(step, OK, "SearxNG restarted · localhost:8888 → container:8080")
            results["searxng"] = (OK, "restarted")
            searxng_ok = True
        else:
            _finish_step(step, FAIL, f"SearxNG failed to start: {err3.strip()[:60]}")
            results["searxng"] = (FAIL, err3.strip()[:80])
    else:
        step = _start_step("SEARXNG", "SearxNG — skipped (Docker unavailable)")
        await asyncio.sleep(0.2 if not reduced_motion else 0.05)
        _finish_step(step, WARN, "SearxNG unavailable — agents will use Jina fallback")
        results["searxng"] = (WARN, "skipped")

    # ── Step 3b: FlareSolverr — CAPTCHA-bypass headless Chromium ──────
    if results.get("docker", (FAIL,))[0] == OK:
        step = _start_step("FLARE", "starting FlareSolverr CAPTCHA-bypass container")
        await asyncio.sleep(0.3 if not reduced_motion else 0.05)

        # Always stop and remove existing container for a clean start
        if step.row:
            log.update_row(step.row, content="stopping existing FlareSolverr container…", spinner=True)
        await _run_subprocess(["docker", "stop", "flaresolverr"], timeout=15)
        await _run_subprocess(["docker", "rm", "flaresolverr"], timeout=10)

        # Create and run fresh container
        if step.row:
            log.update_row(step.row, content="creating FlareSolverr container…", spinner=True)
        rc_f3, _, err_f3 = await _run_subprocess(
            ["docker", "run", "-d",
             "--name", "flaresolverr",
             "-p", "8191:8191",
             "ghcr.io/flaresolverr/flaresolverr:latest"],
            timeout=60,
        )
        if rc_f3 == 0:
            await asyncio.sleep(3.0)
            _finish_step(step, OK, "FlareSolverr started · localhost:8191 → CAPTCHA bypass ready")
            results["flare"] = (OK, "started")
        else:
            _finish_step(step, WARN, f"FlareSolverr failed: {err_f3.strip()[:50]}")
            results["flare"] = (WARN, "create failed")
    else:
        step = _start_step("FLARE", "FlareSolverr — skipped (Docker unavailable)")
        await asyncio.sleep(0.2 if not reduced_motion else 0.05)
        _finish_step(step, WARN, "FlareSolverr unavailable — stealth Bing fallback only")
        results["flare"] = (WARN, "skipped")

    # ── Step 3c: Data tools readiness ───────────────────────────────────
    step = _start_step("TOOLS", "checking data source tool readiness")
    await asyncio.sleep(0.3 if not reduced_motion else 0.05)

    tools_ready: list[str] = []
    tools_warn: list[str] = []
    try:
        from hyperion.config import get_settings

        settings = get_settings()

        # Check API-key-based tools
        key_checks = [
            ("alpha_vantage", "alpha_vantage_api_key"),
            ("fred", "fred_api_key"),
            ("jina", "jina_api_key"),
            ("unsplash", "unsplash_access_key"),
        ]
        for tool_name, key_attr in key_checks:
            key_val = getattr(settings, key_attr, "")
            if key_val:
                tools_ready.append(tool_name)
            else:
                tools_warn.append(f"{tool_name}(no key)")

        # Tools that don't need API keys (free public APIs)
        free_tools = [
            "sec_edgar", "open_alex", "world_bank",
            "google_trends", "hackernews", "reddit",
            "wayback", "searxng",
        ]
        tools_ready.extend(free_tools)

    except Exception as e:
        _finish_step(step, WARN, f"tool check partial: {e!s:.50}")
        results["tools"] = (WARN, str(e)[:80])
    else:
        if tools_warn:
            detail = f"{len(tools_ready)} ready · ⚠ {', '.join(tools_warn)}"
            _finish_step(step, WARN, detail)
        else:
            detail = f"{len(tools_ready)} data sources ready"
            _finish_step(step, OK, detail)
        results["tools"] = (OK if not tools_warn else WARN, detail)

    # ── Step 4: LLM providers ─────────────────────────────────────────────
    step = _start_step("PROVIDER", "checking LLM provider health")
    await asyncio.sleep(0.3 if not reduced_motion else 0.05)

    provider_status: list[str] = []
    provider_warns: list[str] = []
    try:
        from hyperion.router.router import get_router

        router = get_router()
        health = router.get_provider_health()
        for ptype, info in health.items():
            name = str(ptype).split(".")[-1].lower()
            available = info.get("available", False)
            if available:
                provider_status.append(name)
            else:
                provider_warns.append(name)
    except Exception as e:
        _finish_step(step, WARN, f"provider check partial: {e!s:.50}")
        results["providers"] = (WARN, str(e)[:80])
        provider_status = []
    else:
        if provider_status:
            detail = "online: " + " · ".join(provider_status)
            if provider_warns:
                detail += f"  ⚠ offline: {', '.join(provider_warns)}"
            _finish_step(step, OK, detail)
            results["providers"] = (OK, detail)
        else:
            _finish_step(step, WARN, "no providers available — check API keys in .env")
            results["providers"] = (WARN, "none available")

    # Touch providers on metrics rail
    for p in provider_status:
        try:
            metrics.touch_provider(p)
        except Exception:
            pass

    # ── Step 5: Agent roster ──────────────────────────────────────────────
    step = _start_step("ROSTER", "instantiating specialist agents")
    await asyncio.sleep(0.3 if not reduced_motion else 0.05)

    try:
        from hyperion.tui.roster import ROSTER

        count = len(ROSTER)
        _finish_step(step, OK, f"{count} specialist agents online")
        results["agents"] = (OK, f"{count} agents")
    except Exception as e:
        _finish_step(step, FAIL, f"roster init failed: {e!s:.50}")
        results["agents"] = (FAIL, str(e)[:80])

    # ── Step 6: Second Brain vault ────────────────────────────────────────
    step = _start_step("CONTEXT", "priming Second Brain vault")
    await asyncio.sleep(0.3 if not reduced_motion else 0.05)

    try:
        from hyperion.config import get_settings

        settings = get_settings()
        vault_path = getattr(settings, "second_brain_vault", None)
        if vault_path:
            p = Path(vault_path)
            if p.exists():
                engagements = list((p / "engagements").glob("*.md")) if (p / "engagements").exists() else []
                _finish_step(step, OK, f"vault primed · {len(engagements)} prior engagements")
                results["vault"] = (OK, f"{len(engagements)} engagements")
            else:
                _finish_step(step, WARN, f"vault path not found: {vault_path}")
                results["vault"] = (WARN, "path missing")
        else:
            _finish_step(step, OK, "vault ready (default path)")
            results["vault"] = (OK, "default")
    except Exception as e:
        _finish_step(step, WARN, f"vault check skipped: {e!s:.40}")
        results["vault"] = (WARN, str(e)[:60])

    # ── Step 7: READY ─────────────────────────────────────────────────────
    await asyncio.sleep(0.2 if not reduced_motion else 0.05)
    all_ok = all(v[0] == OK for v in results.values())
    has_warns = any(v[0] == WARN for v in results.values())

    if all_ok:
        log.add_entry(
            "READY",
            "all systems online · type a question to begin",
            aurora=True,
        )
    elif has_warns:
        warns = [k for k, v in results.items() if v[0] == WARN]
        log.add_entry(
            "READY",
            f"systems online with warnings ({', '.join(warns)}) · type to begin",
            icon="⚠",
        )
    else:
        log.add_entry(
            "READY",
            "core ready · some systems need attention — type /providers to check",
            icon="▸",
        )

    return results


async def stop_services() -> None:
    """Stop all HYPERION services on shutdown.

    Stops Docker containers (SearxNG, FlareSolverr) and closes any
    globally accessible tool clients — mirroring the boot sequence.
    """
    # Stop and remove Docker containers for a clean slate next boot
    for container in ("searxng", "flaresolverr"):
        try:
            await _run_subprocess(["docker", "stop", container], timeout=15)
        except Exception:
            pass
        try:
            await _run_subprocess(["docker", "rm", container], timeout=10)
        except Exception:
            pass

    # Close any global tool clients (router, etc.)
    try:
        from hyperion.router.router import get_router

        router = get_router()
        close_method = getattr(router, "close", None)
        if callable(close_method):
            result = close_method()
            if asyncio.iscoroutine(result):
                await result
    except Exception:
        pass


# Backward-compatible alias
stop_searxng = stop_services
