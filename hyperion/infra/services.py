"""HYPERION container lifecycle — Docker engine + the containers we own.

THE BUG IN THE SCREENSHOT
-------------------------
    ERROR  ✗ SearxNG failed to start: Unable to find image
           'searxng/searxng:2024.12.10-a4d2a5f68'

That tag does not exist. The Docker Hub API returns 404 for it, and the oldest
tag SearxNG still publishes is roughly 2025-09. The pin was chosen to make
builds reproducible, but SearxNG's registry only retains a rolling window of
date-stamped tags, so a pin written months ago **ages out and becomes
unresolvable**. A pin to a deleted tag is not reproducibility — it is a
guaranteed startup failure, and because `docker run` fails before the container
exists, SearxNG never came up and every search fell through to the degraded
path.

FlareSolverr was pinned to ``v3.3.21``, which still resolves — which is exactly
why the second screenshot shows FlareSolverr running while SearxNG is absent.
One container up, one down, from the same boot sequence.

THE FIX
-------
Pin to a tag *and* survive its disappearance. :func:`ensure_container` pulls the
pinned tag first; if the registry no longer has it, it falls back to the pinned
fallback tag, and finally to a floating tag — reporting which one it used in
:class:`ServiceStatus.image_used` so the transcript can say so out loud rather
than silently running something other than what was pinned.

WHY THE USER HAD TO START DOCKER BY HAND
----------------------------------------
The old auto-start was Windows-only and fired at one hardcoded location::

    Path(os.environ.get("ProgramFiles", "C:\\Program Files")) / "Docker" /
        "Docker" / "Docker Desktop.exe"

Docker Desktop installed per-user (``%LOCALAPPDATA%``), on a non-default drive,
via winget, on macOS, or as a plain Linux daemon all miss that path, so
``docker_desktop.exists()`` was False and the code went straight to "start
Docker Desktop manually". :func:`ensure_docker_engine` probes every real install
location per platform, and on Linux tries the service manager, so the shell
starts the engine itself.

READINESS, NOT ``sleep(3)``
---------------------------
The old code slept three seconds and declared success. A cold SearxNG image
takes longer, so the first searches of an engagement hit a container that
accepts TCP but is not yet serving — indistinguishable, from the agent's side,
from "search returned nothing". Here every service declares an HTTP readiness
probe that is polled until it actually answers.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

from hyperion.infra.paths import (
    docker_mount_path,
    searxng_limiter_file,
    searxng_settings_file,
)

# ─────────────────────────────────────────────────────────────────────────────
# Ports and images — ONE definition, imported by every caller
# ─────────────────────────────────────────────────────────────────────────────

SEARXNG_PORT = 8888
SEARXNG_CONTAINER_PORT = 8080
FLARESOLVERR_PORT = 8191
FLARESOLVERR_CONTAINER_PORT = 8191

# Primary pin. Verified to exist in the registry at the time of writing.
#
# `2024.12.10-a4d2a5f68` (the previous value) is gone from Docker Hub — the
# tag list starts around 2025-09 — which is the failure in the screenshot.
SEARXNG_IMAGE = "searxng/searxng:2026.7.19-6da6eee26"

# Fallback pin, used only if the primary has been reaped from the registry.
# Deliberately an older tag from a different retention cohort, so both are
# unlikely to age out in the same window.
SEARXNG_IMAGE_FALLBACK = "searxng/searxng:2025.9.10-a9b088d"

# Last resort. Floating, therefore not reproducible — which is why it is last
# and why its use is reported rather than hidden.
SEARXNG_IMAGE_FLOATING = "searxng/searxng:latest"

# FlareSolverr keeps every release tag, so this pin is stable.
FLARESOLVERR_IMAGE = "flaresolverr/flaresolverr:v3.3.21"
FLARESOLVERR_IMAGE_FALLBACK = "flaresolverr/flaresolverr:v3.4.6"
FLARESOLVERR_IMAGE_FLOATING = "flaresolverr/flaresolverr:latest"

MANAGED_CONTAINERS: tuple[str, ...] = ("searxng", "flaresolverr")


# ─────────────────────────────────────────────────────────────────────────────
# Result types
# ─────────────────────────────────────────────────────────────────────────────

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class ServiceStatus:
    """Outcome of bringing one service up."""

    name: str
    state: str = FAIL
    detail: str = ""
    image_used: str = ""
    #: True when a tag other than the primary pin was used.
    used_fallback_image: bool = False
    ready: bool = False

    @property
    def ok(self) -> bool:
        return self.state == OK


@dataclass
class DockerStatus:
    """Outcome of ensuring the Docker engine is usable."""

    state: str = FAIL
    detail: str = ""
    version: str = ""
    #: True when HYPERION started the engine (rather than finding it running).
    started_by_us: bool = False

    @property
    def ok(self) -> bool:
        return self.state == OK


@dataclass
class ContainerSpec:
    """Everything needed to run one container and know when it is ready."""

    name: str
    image: str
    image_fallback: str
    image_floating: str
    host_port: int
    container_port: int
    #: Path that answers 200 once the app inside the container is serving.
    health_path: str
    volumes: list[tuple[Path, str]] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    #: Seconds to wait for the readiness probe to pass.
    ready_timeout: float = 90.0

    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.host_port}{self.health_path}"


def searxng_spec() -> ContainerSpec:
    """Container spec for SearxNG, mounting the project's real settings files."""
    volumes: list[tuple[Path, str]] = []
    settings = searxng_settings_file()
    if settings.exists():
        volumes.append((settings, "/etc/searxng/settings.yml"))
    limiter = searxng_limiter_file()
    if limiter.exists():
        volumes.append((limiter, "/etc/searxng/limiter.toml"))
    return ContainerSpec(
        name="searxng",
        image=SEARXNG_IMAGE,
        image_fallback=SEARXNG_IMAGE_FALLBACK,
        image_floating=SEARXNG_IMAGE_FLOATING,
        host_port=SEARXNG_PORT,
        container_port=SEARXNG_CONTAINER_PORT,
        # `/healthz` is not present on every build; the root document is, and a
        # 200 from it means Flask is serving, which is what we need to know.
        health_path="/",
        volumes=volumes,
        env={"SEARXNG_BASE_URL": f"http://localhost:{SEARXNG_PORT}/"},
    )


def flaresolverr_spec() -> ContainerSpec:
    """Container spec for FlareSolverr."""
    return ContainerSpec(
        name="flaresolverr",
        image=FLARESOLVERR_IMAGE,
        image_fallback=FLARESOLVERR_IMAGE_FALLBACK,
        image_floating=FLARESOLVERR_IMAGE_FLOATING,
        host_port=FLARESOLVERR_PORT,
        container_port=FLARESOLVERR_CONTAINER_PORT,
        health_path="/health",
        env={"LOG_LEVEL": "info"},
        # FlareSolverr boots a Chromium instance; a cold start is slow.
        ready_timeout=120.0,
    )


def all_specs() -> list[ContainerSpec]:
    return [searxng_spec(), flaresolverr_spec()]


# ─────────────────────────────────────────────────────────────────────────────
# Subprocess helpers
# ─────────────────────────────────────────────────────────────────────────────


def _no_window_kwargs() -> dict[str, object]:
    """Keep spawned Windows processes from flashing a console window."""
    if sys.platform == "win32":
        return {"creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {}


async def run_command(cmd: list[str], timeout: float = 30.0) -> tuple[int, str, str]:
    """Run a command, returning ``(returncode, stdout, stderr)``.

    Never raises. A missing binary, a timeout and a non-zero exit are all
    reported through the return value, because every caller here is on a
    startup or shutdown path where an exception would mask the real state.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **_no_window_kwargs(),  # type: ignore[arg-type]
        )
    except FileNotFoundError:
        return 127, "", f"{cmd[0]}: not found"
    except OSError as exc:
        return 1, "", str(exc)

    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except Exception:
            pass
        return 124, "", f"timed out after {timeout:.0f}s"
    return (
        proc.returncode or 0,
        stdout_b.decode(errors="replace").strip(),
        stderr_b.decode(errors="replace").strip(),
    )


def docker_available() -> bool:
    """True when a ``docker`` CLI is on PATH."""
    return shutil.which("docker") is not None


async def docker_engine_version() -> str:
    """Return the running engine version, or ``""`` if the daemon is not up."""
    rc, out, _ = await run_command(
        ["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15
    )
    return out if rc == 0 and out else ""


# ─────────────────────────────────────────────────────────────────────────────
# Docker engine autostart
# ─────────────────────────────────────────────────────────────────────────────


def _windows_desktop_candidates() -> list[Path]:
    """Every real Docker Desktop location on Windows.

    The previous code checked exactly one (``%ProgramFiles%``). Per-user
    installs, non-default drives and winget installs all live elsewhere, so the
    file was "missing" and auto-start never even attempted.
    """
    candidates: list[Path] = []
    env_dirs = [
        os.environ.get("ProgramFiles"),
        os.environ.get("ProgramFiles(x86)"),
        os.environ.get("ProgramW6432"),
        os.environ.get("LOCALAPPDATA"),
        os.environ.get("APPDATA"),
    ]
    for base in env_dirs:
        if not base:
            continue
        candidates.append(Path(base) / "Docker" / "Docker" / "Docker Desktop.exe")
    # Common absolute installs, including non-default drives.
    for drive in ("C:", "D:", "E:"):
        candidates.append(
            Path(f"{drive}\\Program Files\\Docker\\Docker\\Docker Desktop.exe")
        )
    # De-duplicate while preserving order.
    seen: set[str] = set()
    unique: list[Path] = []
    for path in candidates:
        key = str(path).lower()
        if key not in seen:
            seen.add(key)
            unique.append(path)
    return unique


def _macos_desktop_candidates() -> list[Path]:
    return [
        Path("/Applications/Docker.app"),
        Path.home() / "Applications" / "Docker.app",
    ]


async def _launch_docker_desktop() -> str:
    """Try to launch the Docker engine. Returns a human-readable attempt note."""
    if sys.platform == "win32":
        for exe in _windows_desktop_candidates():
            if exe.exists():
                try:
                    subprocess.Popen([str(exe)], **_no_window_kwargs())  # type: ignore[arg-type]
                    return f"launching {exe.name}"
                except OSError:
                    continue
        return ""

    if sys.platform == "darwin":
        for app in _macos_desktop_candidates():
            if app.exists():
                rc, _, _ = await run_command(["open", "-a", str(app)], timeout=20)
                if rc == 0:
                    return "launching Docker.app"
        return ""

    # Linux: the daemon is a service. Try user-level first (rootless Docker is
    # common on dev machines and needs no privileges), then system-level.
    for cmd in (
        ["systemctl", "--user", "start", "docker"],
        ["systemctl", "--user", "start", "docker.service"],
        ["systemctl", "start", "docker"],
    ):
        rc, _, _ = await run_command(cmd, timeout=25)
        if rc == 0:
            return f"started via {' '.join(cmd[:3])}"
    return ""


async def ensure_docker_engine(
    *,
    wait_seconds: float = 90.0,
    on_progress: object = None,
) -> DockerStatus:
    """Make sure the Docker daemon is running, starting it if necessary.

    Args:
        wait_seconds: How long to wait for the daemon after launching it. A cold
            Docker Desktop start routinely exceeds the old 30s budget, which is
            why the old code so often gave up and told the user to do it.
        on_progress: Optional ``callable(str)`` for live status text.

    Returns:
        A :class:`DockerStatus`. ``state`` is ``ok`` only when the daemon
        actually answered.
    """

    def _progress(message: str) -> None:
        if callable(on_progress):
            try:
                on_progress(message)  # type: ignore[misc]
            except Exception:
                pass

    if not docker_available():
        return DockerStatus(
            state=WARN,
            detail="Docker CLI not found on PATH — install Docker to enable SearxNG",
        )

    version = await docker_engine_version()
    if version:
        return DockerStatus(state=OK, detail=f"daemon ready · v{version}", version=version)

    _progress("Docker daemon not running — starting the engine…")
    note = await _launch_docker_desktop()
    if not note:
        return DockerStatus(
            state=WARN,
            detail="Docker installed but the engine could not be started automatically",
        )

    _progress(f"{note} — waiting for the daemon…")
    deadline = asyncio.get_running_loop().time() + wait_seconds
    attempt = 0
    while asyncio.get_running_loop().time() < deadline:
        attempt += 1
        version = await docker_engine_version()
        if version:
            return DockerStatus(
                state=OK,
                detail=f"daemon started · v{version}",
                version=version,
                started_by_us=True,
            )
        remaining = int(deadline - asyncio.get_running_loop().time())
        _progress(f"waiting for Docker daemon… ({remaining}s left)")
        # Poll on a gentle interval: `docker info` against a booting daemon can
        # itself block for a second or two.
        await asyncio.sleep(2.0)

    return DockerStatus(
        state=WARN,
        detail=f"Docker engine did not report ready within {wait_seconds:.0f}s",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Image resolution
# ─────────────────────────────────────────────────────────────────────────────


async def image_present_locally(image: str) -> bool:
    """True when the image is already in the local image store."""
    rc, out, _ = await run_command(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"], timeout=20
    )
    return rc == 0 and bool(out)


async def pull_image(image: str, timeout: float = 600.0) -> tuple[bool, str]:
    """Pull ``image``. Returns ``(success, error_detail)``.

    A first pull of SearxNG is ~200 MB, so the timeout is generous: the old
    60s ``docker run`` budget could abort a legitimate cold pull and report it
    as a startup failure.
    """
    rc, _, err = await run_command(["docker", "pull", image], timeout=timeout)
    if rc == 0:
        return True, ""
    return False, err or f"docker pull exited {rc}"


async def resolve_image(spec: ContainerSpec) -> tuple[str, bool, str]:
    """Find a usable image tag for ``spec``.

    Returns ``(image, used_fallback, detail)``. Tries, in order: a tag already
    present locally, the primary pin, the fallback pin, then the floating tag.
    This is what makes an aged-out pin recoverable instead of fatal.
    """
    candidates = [spec.image, spec.image_fallback, spec.image_floating]
    # De-duplicate, preserving priority order.
    ordered: list[str] = []
    for image in candidates:
        if image and image not in ordered:
            ordered.append(image)

    # Prefer anything already local — no network, and it keeps offline runs working.
    for image in ordered:
        if await image_present_locally(image):
            return image, image != spec.image, "already present locally"

    errors: list[str] = []
    for image in ordered:
        ok, err = await pull_image(image)
        if ok:
            detail = "pulled" if image == spec.image else f"pulled fallback ({image})"
            return image, image != spec.image, detail
        errors.append(f"{image}: {err[:80]}")

    return "", False, "; ".join(errors)[:240]


# ─────────────────────────────────────────────────────────────────────────────
# Readiness probing
# ─────────────────────────────────────────────────────────────────────────────


async def wait_until_ready(spec: ContainerSpec) -> bool:
    """Poll the container's health URL until it answers, or the timeout expires.

    Replaces ``await asyncio.sleep(3.0)``. Three seconds is shorter than a cold
    SearxNG or FlareSolverr start, so the old code frequently declared success
    while the container was still booting — and the first searches of the
    engagement failed against a service that was "already reported ready".
    """
    try:
        import httpx
    except ImportError:
        # No HTTP client available; fall back to a TCP check.
        return await _wait_tcp(spec)

    url = spec.health_url()
    loop = asyncio.get_running_loop()
    deadline = loop.time() + spec.ready_timeout
    async with httpx.AsyncClient(timeout=5.0) as client:
        while loop.time() < deadline:
            try:
                response = await client.get(url)
                # Any HTTP answer below 500 proves the app is serving. SearxNG
                # answers 200 on `/`; FlareSolverr answers 200 on `/health`.
                if response.status_code < 500:
                    return True
            except Exception:
                pass
            await asyncio.sleep(1.0)
    return False


async def _wait_tcp(spec: ContainerSpec) -> bool:
    """TCP fallback readiness check."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + spec.ready_timeout
    while loop.time() < deadline:
        try:
            _, writer = await asyncio.wait_for(
                asyncio.open_connection("127.0.0.1", spec.host_port), timeout=3.0
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            return True
        except Exception:
            await asyncio.sleep(1.0)
    return False


# ─────────────────────────────────────────────────────────────────────────────
# Container lifecycle
# ─────────────────────────────────────────────────────────────────────────────


async def remove_container(name: str) -> None:
    """Stop and remove a container. Idempotent, never raises."""
    await run_command(["docker", "stop", "-t", "5", name], timeout=40)
    await run_command(["docker", "rm", "-f", name], timeout=25)


def _docker_run_argv(spec: ContainerSpec, image: str) -> list[str]:
    """Build the ``docker run`` argv for ``spec``."""
    argv = [
        "docker", "run", "-d",
        "--name", spec.name,
        "-p", f"{spec.host_port}:{spec.container_port}",
    ]
    for host_path, container_path in spec.volumes:
        argv += ["-v", f"{docker_mount_path(host_path)}:{container_path}:ro"]
    for key, value in spec.env.items():
        argv += ["-e", f"{key}={value}"]
    argv.append(image)
    return argv


async def container_logs(name: str, lines: int = 20) -> str:
    """Tail a container's logs — used to explain a failed start."""
    rc, out, err = await run_command(
        ["docker", "logs", "--tail", str(lines), name], timeout=20
    )
    if rc != 0:
        return ""
    return (out or err or "").strip()


async def ensure_container(
    spec: ContainerSpec,
    *,
    on_progress: object = None,
) -> ServiceStatus:
    """Bring ``spec`` up from a clean slate and wait until it truly serves."""

    def _progress(message: str) -> None:
        if callable(on_progress):
            try:
                on_progress(message)  # type: ignore[misc]
            except Exception:
                pass

    status = ServiceStatus(name=spec.name)

    _progress(f"removing any existing {spec.name} container…")
    await remove_container(spec.name)

    _progress(f"resolving {spec.name} image…")
    image, used_fallback, image_detail = await resolve_image(spec)
    if not image:
        status.state = FAIL
        status.detail = f"no usable image — {image_detail}"
        return status

    status.image_used = image
    status.used_fallback_image = used_fallback

    _progress(f"starting {spec.name} ({image})…")
    rc, _, err = await run_command(_docker_run_argv(spec, image), timeout=180)
    if rc != 0:
        status.state = FAIL
        status.detail = f"docker run failed: {(err or 'unknown error')[:120]}"
        return status

    _progress(f"waiting for {spec.name} to accept requests…")
    status.ready = await wait_until_ready(spec)

    if status.ready:
        status.state = OK
        base = (
            f"ready · localhost:{spec.host_port}"
            f" → container:{spec.container_port}"
        )
        if used_fallback:
            # Surfaced deliberately: the pinned tag was not what ran.
            base += f" · using {image} (pinned tag unavailable)"
        status.detail = base
    else:
        # The container exists but never served. Its own logs are the only
        # useful diagnostic, so include them instead of a generic message.
        logs = await container_logs(spec.name, lines=6)
        status.state = WARN
        status.detail = (
            f"started but not ready within {spec.ready_timeout:.0f}s"
            + (f" · {logs.splitlines()[-1][:100]}" if logs else "")
        )
    return status


async def start_services(*, on_progress: object = None) -> dict[str, ServiceStatus]:
    """Start every managed container. Returns per-service status.

    Used by both the TUI boot sequence and ``hyperion consult``, so the two
    entry points cannot drift apart the way they previously did.
    """
    results: dict[str, ServiceStatus] = {}
    if not docker_available():
        for spec in all_specs():
            results[spec.name] = ServiceStatus(
                name=spec.name, state=WARN, detail="Docker CLI not available"
            )
        return results

    # Sequential, not concurrent: two simultaneous cold pulls saturate the
    # connection and both appear to hang.
    for spec in all_specs():
        results[spec.name] = await ensure_container(spec, on_progress=on_progress)
    return results


async def stop_services() -> dict[str, bool]:
    """Stop and remove every managed container. Idempotent, never raises.

    Removal (not just stop) is deliberate: a stopped-but-present container keeps
    its cached SERPs and its old settings mount, so the next boot would not be
    the fresh instance the boot sequence claims to create.
    """
    removed: dict[str, bool] = {}
    if not docker_available():
        return {name: False for name in MANAGED_CONTAINERS}

    for name in MANAGED_CONTAINERS:
        try:
            await remove_container(name)
            removed[name] = True
        except Exception:
            removed[name] = False
    return removed


async def running_containers() -> set[str]:
    """Names of managed containers currently running — used by health reporting."""
    rc, out, _ = await run_command(
        ["docker", "ps", "--format", "{{.Names}}"], timeout=20
    )
    if rc != 0:
        return set()
    names = {line.strip() for line in out.splitlines() if line.strip()}
    return names & set(MANAGED_CONTAINERS)
