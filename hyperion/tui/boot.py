"""HYPERION premium boot sequence.

Runs on shell init — each step streams into the transcript with a live spinner,
then resolves to ✓ / ⚠ / ✗.  Steps:

  1. CORE     — core systems init (drops every stale process singleton)
  2. DOCKER   — Docker engine check, and **start it if it is not running**
  3. SEARXNG  — SearxNG container: resolve image → run → wait until it serves
  3b.FLARE    — FlareSolverr container (CAPTCHA-bypass headless Chromium)
  3c.TOOLS    — data source readiness, including the Obscura binary
  4. PROVIDER — LLM provider health (NVIDIA, Cerebras, Groq, Mistral, Google)
  5. ROSTER   — specialist agent instantiation
  6. CONTEXT  — Second Brain vault prime
  7. READY    — all systems online

WHAT CHANGED, AND WHY
---------------------
Three defects in this module produced the failure in the user's screenshots
("✗ SearxNG failed to start: Unable to find image
'searxng/searxng:2024.12.10-a4d2a5f68'", with FlareSolverr the only container in
Docker Desktop):

1. **Its own image pins.** This file carried ``SEARXNG_IMAGE`` /
   ``FLARESOLVERR_IMAGE`` as literals. The SearxNG pin had been reaped from
   Docker Hub, so ``docker run`` failed before a container existed, and there
   was no recovery path. FlareSolverr's pin still resolved, which is why exactly
   one container was up. Images now come from
   :mod:`hyperion.infra.services`, which pulls a pinned tag *and* falls back
   when the registry no longer has it.

2. **Windows-only, single-path Docker autostart.** The old code probed exactly
   ``%ProgramFiles%\\Docker\\Docker\\Docker Desktop.exe``. Per-user installs,
   non-default drives, macOS and Linux all missed it, so the shell told the user
   to start Docker by hand — which is precisely what they reported having to do.
   :func:`hyperion.infra.services.ensure_docker_engine` probes every real
   install location per platform and starts the Linux service.

3. **``sleep(3)`` instead of a readiness check.** A cold container start is
   longer than three seconds, so the step reported success while the app inside
   was still booting. The first searches then failed against a service the
   transcript had already called ready. Containers are now polled on their real
   health endpoint.

The vault step additionally read ``settings.second_brain_vault``, an attribute
:class:`hyperion.config.Settings` does not define — so ``getattr`` returned
None, the step took the "default path" branch and reported "vault ready" without
ever looking at a directory. It now reads the real ``vault_path``.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
from typing import Any

from hyperion.infra.paths import obscura_bin_dir, obscura_binary_names
from hyperion.infra.provenance import banner as _provenance_banner
from hyperion.infra.provenance import collect_async as _collect_provenance_async
from hyperion.infra.provenance import refusal_reason as _provenance_refusal
from hyperion.infra.services import (
    FLARESOLVERR_IMAGE,
    FLARESOLVERR_PORT,
    MANAGED_CONTAINERS,
    SEARXNG_IMAGE,
    SEARXNG_REPLICAS,
    ensure_docker_engine,
    run_command,
)
from hyperion.infra.services import (
    start_services as _infra_start_services,
)
from hyperion.infra.services import (
    stop_services as _infra_stop_services,
)
from hyperion.tools.searxng import EngineRegistryMismatch, reconcile_engine_registry
from hyperion.tui.widgets.transcript import LogRow, Transcript

logger = logging.getLogger(__name__)

__all__ = [
    "FLARESOLVERR_IMAGE",
    "FLARESOLVERR_PORT",
    "MANAGED_CONTAINERS",
    "SEARXNG_IMAGE",
    "SEARXNG_REPLICAS",
    "BootStep",
    "ProvenanceRefusal",
    "reset_process_state",
    "run_boot_sequence",
    "start_services",
    "stop_searxng",
    "stop_services",
]


# ── helpers ──────────────────────────────────────────────────────────────────


async def _run_subprocess(cmd: list[str], timeout: float = 15.0) -> tuple[int, str, str]:
    """Run a subprocess asynchronously, return ``(returncode, stdout, stderr)``.

    Retained as the module's subprocess entry point because
    :mod:`hyperion.tui.screens.splash` imports it. It delegates to
    :func:`hyperion.infra.services.run_command` so there is one implementation of
    "run a command without ever raising on the startup path".
    """
    return await run_command(cmd, timeout=timeout)


async def _run_powershell(script: str, timeout: float = 15.0) -> tuple[int, str, str]:
    """Run a PowerShell command string."""
    return await _run_subprocess(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )


# ── boot step result ─────────────────────────────────────────────────────────

OK = "ok"
WARN = "warn"
FAIL = "fail"


class ProvenanceRefusalError(RuntimeError):
    """W-01: the shell refuses to boot in an RC-1 configuration.

    Raised by run_boot_sequence when the loaded build is a site-packages
    copy shadowing a git checkout on sys.path, or when stale .pyc bytecode
    sits under the package directory — the two mechanisms that served
    pre-fix output for fifteen correct commits. This is a hard stop, never
    a warning.
    """


# Backward-compatible public name retained for callers and boot integrations.
ProvenanceRefusal = ProvenanceRefusalError


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

    Returns a dict with keys: core, docker, searxng, flare, tools, providers,
    agents, vault — each mapping to ``(status_str, detail_str)``.
    """
    results: dict[str, Any] = {}
    step_num = 0

    # ── W-01: build provenance, before ANY service bring-up ─────────────
    # RC-1: a merged fix is not a running fix. Fifteen correct commits
    # produced pre-fix output because the shell booted a site-packages
    # shadow with stale bytecode and nobody could see it. The banner is
    # printed unconditionally (never a log line at INFO level), and the two
    # RC-1 configurations are a hard refusal, not a warning.
    from hyperion.config import get_settings

    # collect_async: this function runs inside the Textual event loop, so
    # the sync wrapper would have fallen back to a SHA-less snapshot. The
    # async path runs the bounded git subprocesses and caches the snapshot
    # for the render path's XMP stamp.
    provenance = await _collect_provenance_async()
    banner_text = _provenance_banner(provenance)
    # Banner goes to the transcript AND stderr — it must be on screen even
    # if the TUI crashes before the first frame paints.
    print(banner_text, file=sys.stderr, flush=True)
    log.add_entry("BUILD", banner_text, spinner=False)
    refusal = _provenance_refusal(provenance)
    if refusal is not None and get_settings().provenance_strict:
        print(f"BOOT REFUSED — {refusal}", file=sys.stderr, flush=True)
        raise ProvenanceRefusal(refusal)
    results["build"] = (
        OK,
        f"build {provenance.git_sha or 'unknown'} "
        f"{'+dirty ' if provenance.git_dirty else ''}{provenance.install_mode}",
    )

    def _start_step(badge: str, label: str, spinner: bool = True) -> BootStep:
        nonlocal step_num
        step_num += 1
        step = BootStep(badge, label)
        step.row = log.add_entry(badge, label, spinner=spinner)
        try:
            metrics.set_phase("boot")
            metrics._repaint()
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.debug("%s: %s", "_start_step", exc)
        return step

    def _progress_for(step: BootStep):
        """Live sub-status callback for a long-running step.

        The container and engine helpers report what they are doing
        (``resolving image…``, ``waiting for Docker daemon… (42s left)``). Wiring
        that into the row is what turns a 90-second wait from an apparent hang
        into visible progress.
        """

        def _update(message: str) -> None:
            if step.row is not None:
                with contextlib.suppress(Exception):
                    log.update_row(step.row, content=message, spinner=True)

        return _update

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

    async def _pause(seconds: float) -> None:
        await asyncio.sleep(seconds if not reduced_motion else min(seconds, 0.05))

    # ── Step 1: Core ──────────────────────────────────────────────────────
    step = _start_step("BOOT", "initializing HYPERION core systems")
    # Discard any in-process state left by a previous engagement in this same
    # interpreter before anything else touches it. Without this, "initializing
    # core systems" was a cosmetic sleep: the router, agent bus, search budget
    # and engagement focus are module-level singletons, so a second engagement
    # inherited the first one's provider health, cooldowns, spent search budget
    # and — most damaging — its subject/geography focus, which is precisely the
    # mechanism by which one engagement's geography can leak into the next.
    reset_count = reset_process_state()
    await _pause(0.4)
    _finish_step(step, OK, f"core systems initialized · {reset_count} subsystems reset")
    results["core"] = (OK, f"core systems initialized · {reset_count} subsystems reset")

    # ── Step 2: Docker engine ─────────────────────────────────────────────
    # The user's requirement: "when we start hyperion shell docker should launch
    # automatically". `ensure_docker_engine` starts the engine on Windows, macOS
    # and Linux, then waits for the daemon to actually answer.
    step = _start_step("DOCKER", "checking Docker engine")
    await _pause(0.3)

    docker_status = await ensure_docker_engine(on_progress=_progress_for(step))
    _finish_step(step, docker_status.state, docker_status.detail)
    results["docker"] = (docker_status.state, docker_status.detail)

    # ── Step 3: profile-isolated retrieval stack ───────────────────────────
    if docker_status.ok:
        step = _start_step("SEARCH", "starting three SearXNG replicas and Valkey")
        statuses = await _infra_start_services(on_progress=_progress_for(step))
        replica_details: list[str] = []
        replica_ok = True
        for replica in SEARXNG_REPLICAS:
            status = statuses[replica.name]
            if status.ok:
                try:
                    registry = await reconcile_engine_registry(
                        f"http://127.0.0.1:{replica.port}",
                        expected_engines=set(replica.engines),
                    )
                except EngineRegistryMismatch as exc:
                    status.state = FAIL
                    status.detail = str(exc)
                else:
                    status.detail += f" · {len(registry.enabled)} engines reconciled"
            replica_ok = replica_ok and status.ok
            replica_details.append(f"{replica.profile}:{status.state}@{replica.port}")
            results[f"searxng_{replica.profile}"] = (status.state, status.detail)
        valkey = statuses["hyperion-valkey"]
        aggregate_state = OK if replica_ok and valkey.ok else FAIL
        aggregate_detail = " · ".join(replica_details + [f"valkey:{valkey.state}"])
        _finish_step(step, aggregate_state, aggregate_detail)
        results["searxng"] = (aggregate_state, aggregate_detail)
        results["valkey"] = (valkey.state, valkey.detail)
    else:
        step = _start_step("SEARCH", "retrieval stack — skipped (Docker unavailable)")
        await _pause(0.2)
        _finish_step(step, WARN, "SearXNG unavailable — agents will use grounded fallbacks")
        results["searxng"] = (WARN, "skipped")
        results["valkey"] = (WARN, "skipped")

    # W-12: CAPTCHA tooling is investigation-only. Starting it during a normal
    # engagement would conceal a forbidden Tier C engine regression.
    results["flare"] = (OK, "disabled by default; opt in for investigation")

    # ── Step 3c: Data tools readiness ───────────────────────────────────
    step = _start_step("TOOLS", "checking data source tool readiness")
    await _pause(0.3)

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

        # Obscura — a binary, not an API. Report whether it is actually present
        # rather than assuming. The old boot sequence never mentioned Obscura at
        # all, so a missing binary only surfaced as degraded scraping later.
        if _obscura_present(settings):
            tools_ready.append("obscura")
        else:
            tools_warn.append("obscura(binary not found)")

        # Tools that don't need API keys (free public APIs)
        free_tools = [
            "sec_edgar", "open_alex", "world_bank",
            "google_trends", "hackernews", "reddit",
            "wayback",
        ]
        tools_ready.extend(free_tools)

        # SearxNG counts as ready only if its container actually came up — the
        # old code listed it unconditionally, so the transcript claimed SearxNG
        # was ready in the very same boot that failed to start it.
        if results.get("searxng", (FAIL,))[0] == OK:
            tools_ready.append("searxng")
        else:
            tools_warn.append("searxng(container not ready)")

    except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
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
    await _pause(0.3)

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
    except Exception as e:  # noqa: BLE001 - best-effort, returns a safe default
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
        with contextlib.suppress(Exception):
            metrics.touch_provider(p)

    # ── Step 5: Agent roster ──────────────────────────────────────────────
    step = _start_step("ROSTER", "instantiating specialist agents")
    await _pause(0.3)

    try:
        from hyperion.tui.roster import ROSTER

        count = len(ROSTER)
        _finish_step(step, OK, f"{count} specialist agents online")
        results["agents"] = (OK, f"{count} agents")
    except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
        _finish_step(step, FAIL, f"roster init failed: {e!s:.50}")
        results["agents"] = (FAIL, str(e)[:80])

    # ── Step 6: Second Brain vault ────────────────────────────────────────
    step = _start_step("CONTEXT", "priming Second Brain vault")
    await _pause(0.3)

    try:
        from hyperion.config import get_settings

        settings = get_settings()
        # `vault_path` is the attribute Settings actually defines, and it is now
        # absolutised at validation time, so this no longer depends on the
        # directory the shell happened to be launched from. The old code read
        # `second_brain_vault`, which does not exist — the step could therefore
        # never fail and never told the truth.
        vault = getattr(settings, "vault_path", None)
        if vault is None:
            _finish_step(step, WARN, "vault_path is not configured")
            results["vault"] = (WARN, "unconfigured")
        else:
            from pathlib import Path

            vault = Path(vault)
            # Creating it is the correct behaviour: SecondBrainClient writes
            # notes here, so a missing directory is a first-run condition, not
            # an error.
            with contextlib.suppress(OSError):
                vault.mkdir(parents=True, exist_ok=True)
            if vault.exists():
                engagements_dir = vault / "engagements"
                engagements = (
                    list(engagements_dir.glob("*.md")) if engagements_dir.exists() else []
                )
                _finish_step(
                    step, OK, f"vault primed · {len(engagements)} prior engagements · {vault}"
                )
                results["vault"] = (OK, f"{len(engagements)} engagements")
            else:
                _finish_step(step, WARN, f"vault path unavailable: {vault}")
                results["vault"] = (WARN, "path missing")
    except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
        _finish_step(step, WARN, f"vault check skipped: {e!s:.40}")
        results["vault"] = (WARN, str(e)[:60])

    # ── Step 7: READY ─────────────────────────────────────────────────────
    await _pause(0.2)
    all_ok = all(v[0] == OK for v in results.values())
    has_fails = any(v[0] == FAIL for v in results.values())

    if all_ok:
        log.add_entry(
            "READY",
            "all systems online · type a question to begin",
            aurora=True,
        )
    elif not has_fails:
        warns = [k for k, v in results.items() if v[0] == WARN]
        log.add_entry(
            "READY",
            f"systems online with warnings ({', '.join(warns)}) · type to begin",
            icon="⚠",
        )
    else:
        fails = [k for k, v in results.items() if v[0] == FAIL]
        log.add_entry(
            "READY",
            f"core ready · needs attention: {', '.join(fails)} — type /providers to check",
            icon="▸",
        )

    return results


def _obscura_present(settings: Any) -> bool:
    """True when an Obscura binary can actually be located.

    Mirrors :meth:`hyperion.tools.obscura.ObscuraClient._find_obscura` so the
    boot report and the client cannot disagree.
    """
    import shutil
    from pathlib import Path

    configured = str(getattr(settings, "obscura_path", "") or "").strip()
    if configured:
        candidate = Path(configured).expanduser()
        if candidate.is_file():
            return True

    bin_dir = obscura_bin_dir()
    for name in obscura_binary_names():
        if (bin_dir / name).is_file():
            return True
        if shutil.which(name):
            return True
    return False


async def start_services(*, on_progress: object = None) -> dict[str, bool]:
    """Recreate the managed containers from a clean slate. Headless entry point.

    Delegates to :func:`hyperion.infra.services.start_services`, which owns the
    image resolution, the ``docker run`` argv and the readiness probe. Keeping
    the ``{name: bool}`` return shape preserves the existing callers while the
    richer per-service detail stays available through the infra layer.
    """
    statuses = await _infra_start_services(on_progress=on_progress)
    return {name: status.ok for name, status in statuses.items()}


async def ensure_docker_ready(*, on_progress: object = None) -> bool:
    """Start the Docker engine if needed. True when the daemon is usable."""
    status = await ensure_docker_engine(on_progress=on_progress)
    return status.ok


def reset_process_state() -> int:
    """Drop every module-level singleton so a session starts genuinely clean.

    HYPERION keeps the router, agent bus, search budget, settings and the
    engagement focus as process-global singletons. Each module already shipped a
    `reset_*` helper, but every one of them was documented as "useful for
    testing" and none was ever called by the application. The consequence is
    that a shell session which runs two engagements carries the first one's
    provider cooldowns, spent search budget and — the dangerous one — its
    subject/geography focus into the second.

    Returns the number of subsystems successfully reset, so the boot transcript
    can report a real number instead of an unverifiable "initialized".

    Each reset is independent: one missing module must not prevent the rest.
    """
    reset = 0

    # Engagement focus first — a stale subject/geography is the failure mode
    # that silently produces a report about the wrong country.
    try:
        from hyperion.tools.query_utils import clear_engagement_focus

        clear_engagement_focus()
        reset += 1
    except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
        logger.debug("%s: %s", "reset_process_state", exc)

    try:
        from hyperion.router.router import reset_router

        reset_router()
        reset += 1
    except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
        logger.debug("%s: %s", "reset_process_state", exc)

    try:
        from hyperion.agents.bus import reset_bus

        reset_bus()
        reset += 1
    except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
        logger.debug("%s: %s", "reset_process_state", exc)

    try:
        from hyperion.tools.search_budget import SearchBudget

        # `start()` replaces the instance outright, zeroing per-engine spend.
        SearchBudget.start()
        reset += 1
    except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
        logger.debug("%s: %s", "reset_process_state", exc)

    return reset


async def stop_services() -> None:
    """Terminate everything HYPERION started, in dependency order.

    Called on shell quit (and from `hyperion consult`'s finally block). Must be
    idempotent: `docker stop` / `docker rm` on an absent container is a no-op,
    and the singleton resets are naturally idempotent, so running this twice is
    harmless. It must also never raise — it runs on the exit path, where an
    exception would mask whatever actually ended the session.

    Order matters. LLM clients are closed BEFORE the containers they may be
    mid-request against, so an in-flight HTTP call fails against a closed client
    rather than a vanished container (the latter can hang until timeout).
    """
    # ── 1. Close LLM provider clients ────────────────────────────────────────
    # Each provider holds a persistent httpx AsyncClient with a connection pool.
    # Left open, those sockets and their reader tasks survive until GC, which is
    # what "the LLMs did not terminate" looks like from outside.
    try:
        from hyperion.router.router import get_router, reset_router

        router = get_router()
        close_method = getattr(router, "close", None)
        if callable(close_method):
            result = close_method()
            if asyncio.iscoroutine(result):
                await result
        # Drop the singleton so a relaunch in the same interpreter builds fresh
        # clients rather than reusing ones whose transports are now closed.
        reset_router()
    except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
        logger.debug("%s: %s", "stop_services", exc)

    # ── 2. Stop and REMOVE the containers ────────────────────────────────────
    # `rm` as well as `stop`: a stopped-but-present container keeps its cached
    # SearxNG results, so the next boot is not actually a fresh instance.
    with contextlib.suppress(Exception):
        await _infra_stop_services()

    # ── 3. Close shared tool HTTP clients ────────────────────────────────────
    # SearxNG / FlareSolverr / Obscura clients hold their own httpx pools whose
    # sockets point at containers that are, by this line, gone.
    await _close_tool_clients()

    # ── 4. Clear in-process state ────────────────────────────────────────────
    # Mirrors the boot-time reset so quit leaves nothing behind even when the
    # interpreter itself keeps running (embedded / test / REPL use).
    with contextlib.suppress(Exception):
        reset_process_state()


async def _close_tool_clients() -> None:
    """Close module-level tool clients, if the modules expose a closer.

    Best effort by design: this runs after the containers are already gone, so a
    module that cannot be imported or has no closer is not a problem worth
    reporting on the exit path.
    """
    for module_name, closer in (
        ("hyperion.tools.searxng", "close_client"),
        ("hyperion.tools.flaresolverr", "close_client"),
        ("hyperion.tools.obscura", "close_client"),
    ):
        try:
            import importlib

            module = importlib.import_module(module_name)
            fn = getattr(module, closer, None)
            if callable(fn):
                result = fn()
                if asyncio.iscoroutine(result):
                    await result
        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
            logger.debug("%s: %s", "_close_tool_clients", exc)
            continue


# Backward-compatible alias
stop_searxng = stop_services
