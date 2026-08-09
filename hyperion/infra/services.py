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
import contextlib
import logging
import os
import secrets
import shutil
import subprocess
import sys
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, cast

from hyperion.infra.paths import (
    docker_mount_path,
    searxng_limiter_file,
    searxng_settings_file,
)

logger = logging.getLogger(__name__)


class Platform(str, Enum):
    """Host modes that require materially different Docker startup paths."""

    WINDOWS = "windows"
    MACOS = "macos"
    WSL2 = "wsl2"
    LINUX_SYSTEMD = "linux_systemd"
    LINUX_OTHER = "linux_other"


def _read_platform_file(path: str) -> str:
    """Read a small kernel identity file without making detection fragile."""
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def detect_platform() -> Platform:
    """Resolve WSL2 separately from native Linux.

    ``sys.platform`` is ``linux`` under WSL2, but Docker Desktop is a Windows
    process there. Treating it as a systemd daemon is the W-13 defect.
    """
    if sys.platform == "win32":
        return Platform.WINDOWS
    if sys.platform == "darwin":
        return Platform.MACOS
    if sys.platform == "linux":
        version = _read_platform_file("/proc/version").lower()
        release = _read_platform_file("/proc/sys/kernel/osrelease").lower()
        if "microsoft" in version or "wsl" in release:
            return Platform.WSL2
        if Path("/run/systemd/system").exists():
            return Platform.LINUX_SYSTEMD
    return Platform.LINUX_OTHER


# ─────────────────────────────────────────────────────────────────────────────
# Ports and images — ONE definition, imported by every caller
# ─────────────────────────────────────────────────────────────────────────────

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

VALKEY_IMAGE = "valkey/valkey:8.1.3-alpine"
VALKEY_IMAGE_FALLBACK = "valkey/valkey:8-alpine"
VALKEY_IMAGE_FLOATING = "valkey/valkey:alpine"

# FlareSolverr keeps every release tag, so this pin is stable.
FLARESOLVERR_IMAGE = "flaresolverr/flaresolverr:v3.3.21"
FLARESOLVERR_IMAGE_FALLBACK = "flaresolverr/flaresolverr:v3.4.6"
FLARESOLVERR_IMAGE_FLOATING = "flaresolverr/flaresolverr:latest"


@dataclass(frozen=True)
class SearxngReplica:
    name: str
    port: int
    profile: str
    engines: tuple[str, ...]


SEARXNG_REPLICAS = (
    SearxngReplica(
        "hyperion-searxng-scholar",
        8888,
        "scholar",
        ("arxiv", "crossref", "openalex", "semantic scholar", "pubmed"),
    ),
    SearxngReplica(
        "hyperion-searxng-reference",
        8889,
        "reference",
        ("wikipedia", "openstreetmap", "github", "stackexchange", "hackernews"),
    ),
    SearxngReplica(
        "hyperion-searxng-web",
        8890,
        "web",
        ("mojeek", "mwmbl", "brave", "yep"),
    ),
)
SEARXNG_PRIMARY_PORT = SEARXNG_REPLICAS[0].port
RETRIEVAL_NETWORK = "hyperion-retrieval"
MANAGED_CONTAINERS: tuple[str, ...] = (
    *(replica.name for replica in SEARXNG_REPLICAS),
    "hyperion-valkey",
    "flaresolverr",
)
# Names used by the pre-W-12 single-instance launcher.  The old ``searxng``
# container publishes 8888, so leaving it alive prevents the scholar profile
# from binding while making Docker Desktop misleadingly show "one SearXNG".
# These names were owned by HYPERION, not arbitrary operator containers.
LEGACY_MANAGED_CONTAINERS: tuple[str, ...] = ("searxng",)


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
    named_volumes: list[tuple[str, str]] = field(default_factory=list)
    health_headers: dict[str, str] = field(default_factory=dict)
    network_aliases: tuple[str, ...] = ()

    def health_url(self) -> str:
        return f"http://127.0.0.1:{self.host_port}{self.health_path}"


def _searxng_secret(profile: str) -> str:
    """Return a per-profile operator secret or fresh high-entropy secret."""
    key = f"SEARXNG_{profile.upper()}_SECRET"
    configured = os.environ.get(key, "").strip()
    return configured or secrets.token_urlsafe(48)


def searxng_spec(replica: SearxngReplica | None = None) -> ContainerSpec:
    """Build one isolated SearXNG replica spec."""
    replica = replica or SEARXNG_REPLICAS[0]
    volumes: list[tuple[Path, str]] = []
    settings = searxng_settings_file().with_name(f"searxng_settings.{replica.profile}.yml")
    if settings.exists():
        volumes.append((settings, "/etc/searxng/settings.yml"))
    limiter = searxng_limiter_file()
    if limiter.exists():
        volumes.append((limiter, "/etc/searxng/limiter.toml"))
    return ContainerSpec(
        name=replica.name,
        image=SEARXNG_IMAGE,
        image_fallback=SEARXNG_IMAGE_FALLBACK,
        image_floating=SEARXNG_IMAGE_FLOATING,
        host_port=replica.port,
        container_port=SEARXNG_CONTAINER_PORT,
        health_path="/config",
        health_headers={
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
        },
        volumes=volumes,
        named_volumes=[(f"{replica.name}-data", "/var/cache/searxng")],
        env={
            # FIX0.3 §2: one documented timezone rule so container logs and
            # host/TUI logs correlate without mental arithmetic.
            "TZ": os.environ.get("HYPERION_TZ", "Asia/Kolkata"),
            "SEARXNG_BASE_URL": f"http://localhost:{replica.port}/",
            "SEARXNG_SECRET": _searxng_secret(replica.profile),
            "SEARXNG_SETTINGS_PATH": "/etc/searxng/settings.yml",
            "HYPERION_CONTACT_EMAIL": os.environ.get(
                "HYPERION_CONTACT_EMAIL", "research@localhost"
            ),
        },
    )


def searxng_specs() -> list[ContainerSpec]:
    return [searxng_spec(replica) for replica in SEARXNG_REPLICAS]


def valkey_spec() -> ContainerSpec:
    return ContainerSpec(
        name="hyperion-valkey",
        image=VALKEY_IMAGE,
        image_fallback=VALKEY_IMAGE_FALLBACK,
        image_floating=VALKEY_IMAGE_FLOATING,
        host_port=0,
        container_port=6379,
        health_path="",
        named_volumes=[("hyperion-valkey-data", "/data")],
        network_aliases=("valkey",),
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


def all_specs(*, include_flaresolverr: bool = False) -> list[ContainerSpec]:
    """Default retrieval stack; CAPTCHA tooling is explicitly opt-in."""
    specs = [*searxng_specs(), valkey_spec()]
    if include_flaresolverr:
        specs.append(flaresolverr_spec())
    return specs


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
    except TimeoutError:
        with contextlib.suppress(Exception):
            proc.kill()
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
    rc, out, _ = await run_command(["docker", "info", "--format", "{{.ServerVersion}}"], timeout=15)
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
        candidates.append(Path(f"{drive}\\Program Files\\Docker\\Docker\\Docker Desktop.exe"))
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


async def _launch_wsl2_docker_desktop() -> str:
    """Launch the Windows Docker Desktop process through WSL interop."""
    if not Path("/proc/sys/fs/binfmt_misc/WSLInterop").exists():
        return "WSL_INTEROP_DISABLED"

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    if powershell:
        rc, _, _ = await run_command(
            [
                powershell,
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                "Start-Process 'Docker Desktop'",
            ],
            timeout=20,
        )
        if rc == 0:
            return "launching Docker Desktop through WSL interop"

    wslpath = shutil.which("wslpath")
    if not wslpath:
        return ""
    for windows_path in _windows_desktop_candidates():
        rc, translated, _ = await run_command([wslpath, "-u", str(windows_path)], timeout=5)
        if rc != 0 or not translated or not Path(translated).is_file():
            continue
        try:
            subprocess.Popen(
                [translated],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
            return f"launching {windows_path.name} through WSL interop"
        except OSError:
            continue
    return ""


async def _launch_docker_desktop() -> str:
    """Try to launch the Docker engine using the detected host mode."""
    platform = detect_platform()
    if platform == Platform.WINDOWS:
        for exe in _windows_desktop_candidates():
            if exe.exists():
                try:
                    subprocess.Popen([str(exe)], **cast("Any", _no_window_kwargs()))
                    return f"launching {exe.name}"
                except OSError:
                    continue
        return ""

    if platform == Platform.MACOS:
        for app in _macos_desktop_candidates():
            if app.exists():
                rc, _, _ = await run_command(["open", "-a", str(app)], timeout=20)
                if rc == 0:
                    return "launching Docker.app"
        return ""

    if platform == Platform.WSL2:
        return await _launch_wsl2_docker_desktop()

    if platform == Platform.LINUX_SYSTEMD:
        # Rootless user service never prompts and is always safe to try first.
        for cmd in (
            ["systemctl", "--user", "start", "docker"],
            ["systemctl", "--user", "start", "docker.service"],
        ):
            rc, _, _ = await run_command(cmd, timeout=25)
            if rc == 0:
                return f"started via {' '.join(cmd[:3])}"

        # Never invoke a command that can prompt inside the TUI. Root may call
        # systemctl directly; other users get only passwordless sudo (-n).
        if hasattr(os, "geteuid") and os.geteuid() == 0:
            cmd = ["systemctl", "start", "docker"]
            rc, _, _ = await run_command(cmd, timeout=25)
            if rc == 0:
                return "started via systemctl start docker"
        elif shutil.which("sudo"):
            probe, _, _ = await run_command(["sudo", "-n", "true"], timeout=5)
            if probe == 0:
                cmd = ["sudo", "-n", "systemctl", "start", "docker"]
                rc, _, _ = await run_command(cmd, timeout=25)
                if rc == 0:
                    return "started via passwordless sudo systemctl"
        return ""

    # Non-systemd Linux has no universally safe daemon launcher. Root may use
    # the traditional service command; an unprivileged TUI must not prompt.
    if (
        platform == Platform.LINUX_OTHER
        and hasattr(os, "geteuid")
        and os.geteuid() == 0
        and shutil.which("service")
    ):
        rc, _, _ = await run_command(["service", "docker", "start"], timeout=25)
        if rc == 0:
            return "started via service docker start"
    return ""


async def ensure_docker_engine(
    *,
    wait_seconds: float = 90.0,
    on_progress: Callable[[str], None] | None = None,
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
        # Progress callbacks are UI concerns — a broken one must never abort
        # an infra operation, so the suppression is intentional.
        if callable(on_progress):
            with suppress(Exception):
                on_progress(message)

    platform = detect_platform()
    if not docker_available():
        if platform == Platform.WSL2:
            return DockerStatus(
                state=WARN,
                detail=(
                    "Docker CLI is not available inside this WSL distro. Docker Desktop "
                    "may be running on Windows, but WSL integration is disabled here. "
                    "Enable Settings > Resources > WSL Integration for this distro."
                ),
            )
        return DockerStatus(
            state=WARN,
            detail="Docker CLI not found on PATH — install Docker to enable SearxNG",
        )

    version = await docker_engine_version()
    if version:
        return DockerStatus(state=OK, detail=f"daemon ready · v{version}", version=version)

    _progress("Docker daemon not running — starting the engine…")
    note = await _launch_docker_desktop()
    if note == "WSL_INTEROP_DISABLED":
        return DockerStatus(
            state=WARN,
            detail=(
                "WSL interop is disabled (/proc/sys/fs/binfmt_misc/WSLInterop is absent), "
                "so HYPERION cannot start Windows Docker Desktop. Enable WSL interop "
                "in /etc/wsl.conf, then run `wsl.exe --shutdown` from Windows."
            ),
        )
    if not note:
        detail = "Docker installed but the engine could not be started automatically"
        if platform == Platform.WSL2:
            detail += (
                ". Confirm Docker Desktop is installed on Windows and WSL integration "
                "is enabled for this distro."
            )
        return DockerStatus(state=WARN, detail=detail)

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
    if spec.host_port == 0:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + spec.ready_timeout
        while loop.time() < deadline:
            rc, out, _ = await run_command(
                ["docker", "exec", spec.name, "valkey-cli", "ping"], timeout=5
            )
            if rc == 0 and out.strip() == "PONG":
                return True
            await asyncio.sleep(1.0)
        return False

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
                response = await client.get(url, headers=spec.health_headers)
                # Readiness must be a successful application response. Treating
                # 3xx/4xx as ready hid bad SearXNG configuration and bot-detection
                # failures behind a merely open HTTP socket.
                if response.status_code == 200:
                    return True
            except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                logger.warning("%s: %s", "wait_until_ready", exc)
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
            with contextlib.suppress(Exception):
                await writer.wait_closed()
            return True
        except Exception:  # noqa: BLE001 - retry/poll loop, failure advances the loop
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
        "docker",
        "run",
        "-d",
        "--name",
        spec.name,
        "--network",
        RETRIEVAL_NETWORK,
    ]
    for alias in spec.network_aliases:
        argv += ["--network-alias", alias]
    if spec.host_port:
        argv += ["-p", f"127.0.0.1:{spec.host_port}:{spec.container_port}"]
    for host_path, container_path in spec.volumes:
        argv += ["-v", f"{docker_mount_path(host_path)}:{container_path}:ro"]
    for volume, container_path in spec.named_volumes:
        argv += ["-v", f"{volume}:{container_path}"]
    for key, value in spec.env.items():
        argv += ["-e", f"{key}={value}"]
    argv.append(image)
    return argv


async def container_logs(name: str, lines: int = 20) -> str:
    """Tail a container's logs — used to explain a failed start."""
    rc, out, err = await run_command(["docker", "logs", "--tail", str(lines), name], timeout=20)
    if rc != 0:
        return ""
    return (out or err or "").strip()


async def ensure_container(
    spec: ContainerSpec,
    *,
    on_progress: Callable[[str], None] | None = None,
) -> ServiceStatus:
    """Bring ``spec`` up from a clean slate and wait until it truly serves."""

    def _progress(message: str) -> None:
        # Progress callbacks are UI concerns — a broken one must never abort
        # an infra operation, so the suppression is intentional.
        if callable(on_progress):
            with suppress(Exception):
                on_progress(message)

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
        base = f"ready · localhost:{spec.host_port} → container:{spec.container_port}"
        if used_fallback:
            # Surfaced deliberately: the pinned tag was not what ran.
            base += f" · using {image} (pinned tag unavailable)"
        status.detail = base
    else:
        # The container exists but never served. Its own logs are the only
        # useful diagnostic, so include them instead of a generic message.
        logs = await container_logs(spec.name, lines=6)
        status.state = WARN
        status.detail = f"started but not ready within {spec.ready_timeout:.0f}s" + (
            f" · {logs.splitlines()[-1][:100]}" if logs else ""
        )
    return status


async def start_services(
    *, on_progress: Callable[[str], None] | None = None
) -> dict[str, ServiceStatus]:
    """Start every managed container. Returns per-service status.

    Used by both the TUI boot sequence and ``hyperion consult``, so the two
    entry points cannot drift apart the way they previously did.  Before
    binding ports, remove containers owned by the legacy single-instance
    launcher and optional managed services that are not part of this boot.
    This makes upgrading from ``searxng`` on port 8888 self-healing instead of
    requiring a manual Docker Desktop cleanup.
    """
    results: dict[str, ServiceStatus] = {}
    specs = all_specs()
    if not docker_available():
        for spec in specs:
            results[spec.name] = ServiceStatus(
                name=spec.name, state=WARN, detail="Docker CLI not available"
            )
        return results

    desired_names = {spec.name for spec in specs}
    stale_names = tuple(dict.fromkeys((
        *LEGACY_MANAGED_CONTAINERS,
        *(name for name in MANAGED_CONTAINERS if name not in desired_names),
    )))
    if stale_names:
        if callable(on_progress):
            with suppress(Exception):
                on_progress("removing legacy single-instance retrieval containers…")
        await asyncio.gather(*(remove_container(name) for name in stale_names))

    rc, _, err = await run_command(["docker", "network", "inspect", RETRIEVAL_NETWORK], timeout=20)
    if rc != 0:
        created, _, create_err = await run_command(
            ["docker", "network", "create", RETRIEVAL_NETWORK], timeout=30
        )
        if created != 0:
            detail = create_err or err or "unable to create retrieval network"
            return {
                spec.name: ServiceStatus(spec.name, state=FAIL, detail=detail)
                for spec in all_specs()
            }

    async def _start_batch(
        batch: list[ContainerSpec], *, deadline: float
    ) -> dict[str, ServiceStatus]:
        tasks = {
            spec.name: asyncio.create_task(
                ensure_container(spec, on_progress=on_progress),
                name=f"start-{spec.name}",
            )
            for spec in batch
        }
        done, pending = await asyncio.wait(tasks.values(), timeout=deadline)
        statuses: dict[str, ServiceStatus] = {}
        names_by_task = {task: name for name, task in tasks.items()}
        for task in done:
            name = names_by_task[task]
            try:
                statuses[name] = task.result()
            except Exception as exc:  # noqa: BLE001 - convert startup faults to status
                statuses[name] = ServiceStatus(
                    name=name,
                    state=FAIL,
                    detail=f"startup raised {type(exc).__name__}: {exc}",
                )
        for task in pending:
            task.cancel()
            name = names_by_task[task]
            statuses[name] = ServiceStatus(
                name=name,
                state=FAIL,
                detail=f"startup exceeded shared {deadline:.0f}s deadline",
            )
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        return statuses

    # SearXNG initializes its Valkey client during application startup. Starting
    # all containers concurrently races DNS and readiness, producing a running
    # uWSGI process whose application cannot serve. Bring the dependency fully
    # online first; the independent SearXNG replicas may then start in parallel.
    valkey = next(spec for spec in specs if spec.name == "hyperion-valkey")
    results.update(
        await _start_batch([valkey], deadline=valkey.ready_timeout + 30.0)
    )
    valkey_status = results[valkey.name]
    dependents = [spec for spec in specs if spec.name != valkey.name]
    if not valkey_status.ok or not valkey_status.ready:
        for spec in dependents:
            results[spec.name] = ServiceStatus(
                name=spec.name,
                state=FAIL,
                detail="dependency hyperion-valkey did not become ready",
            )
        return results

    deadline = max(spec.ready_timeout for spec in dependents) + 210.0
    results.update(await _start_batch(dependents, deadline=deadline))
    return results


async def stop_services() -> dict[str, bool]:
    """Stop and remove every managed container. Idempotent, never raises.

    Removal (not just stop) is deliberate: a stopped-but-present container keeps
    its cached SERPs and its old settings mount, so the next boot would not be
    the fresh instance the boot sequence claims to create.
    """
    if not docker_available():
        return {name: False for name in MANAGED_CONTAINERS}

    async def _remove(name: str) -> tuple[str, bool]:
        try:
            await remove_container(name)
            return name, True
        except Exception:  # noqa: BLE001 - failure is recorded in the result
            return name, False

    outcomes = await asyncio.gather(*(_remove(name) for name in MANAGED_CONTAINERS))
    removed = dict(outcomes)
    # The network is HYPERION-owned and can only be removed after every
    # attached managed container has been removed. Failure is harmless and is
    # intentionally not conflated with a container removal result.
    await run_command(["docker", "network", "rm", RETRIEVAL_NETWORK], timeout=25)
    return removed


async def running_containers() -> set[str]:
    """Names of managed containers currently running — used by health reporting."""
    rc, out, _ = await run_command(["docker", "ps", "--format", "{{.Names}}"], timeout=20)
    if rc != 0:
        return set()
    names = {line.strip() for line in out.splitlines() if line.strip()}
    return names & set(MANAGED_CONTAINERS)
