"""Path and service-layer regression tests.

These pin the defects behind the user's report that they "have to manually
launch docker, otherwise both docker based service is not working", that Obscura
needed to be "properly configured", and that the architecture should have "no
hardcoded path".

Every test here fails against the pre-fix code. None of them needs a Docker
daemon: the container layer is exercised through its argv and its resolution
order, which is where the bugs actually lived.
"""

from __future__ import annotations

import ast
import inspect
import os
import py_compile
import sys
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────────────────────
# Project root resolution
# ─────────────────────────────────────────────────────────────────────────────


class TestProjectRoot:
    """The root must be found by marker, and must not depend on the CWD.

    The old code used `Path(__file__).resolve().parents[2]` (a positional bet
    that breaks the moment a file moves) in `tools/obscura.py`, and
    `Path("obscura-bin/obscura.exe")` (CWD-relative) in `obs/health.py`. The
    latter is why health reported Obscura OFFLINE whenever the shell was
    launched from anywhere but the repo root.
    """

    def test_root_contains_a_marker(self):
        from hyperion.infra.paths import project_root, project_root_origin

        root = project_root()
        assert root.is_absolute(), "project root must be absolute"
        assert (root / "pyproject.toml").exists(), (
            f"resolved root {root} has no pyproject.toml, so marker discovery "
            f"did not actually anchor on the project"
        )
        assert project_root_origin() in ("env", "marker", "fallback")

    def test_root_is_independent_of_cwd(self, tmp_path, monkeypatch):
        """The failure mode: correct from the repo, wrong from anywhere else."""
        from hyperion.infra.paths import project_root, reset_path_cache

        reset_path_cache()
        before = project_root()

        monkeypatch.chdir(tmp_path)
        reset_path_cache()
        after = project_root()

        assert before == after, (
            f"project root changed with the CWD ({before} → {after}): every "
            f"path derived from it would move with the user's shell"
        )

    def test_env_var_overrides_discovery(self, tmp_path, monkeypatch):
        """An installed/frozen deployment must be able to relocate the root."""
        from hyperion.infra import paths

        monkeypatch.setenv("HYPERION_PROJECT_ROOT", str(tmp_path))
        paths.reset_path_cache()
        try:
            assert paths.project_root() == tmp_path.resolve()
            assert paths.project_root_origin() == "env"
        finally:
            monkeypatch.delenv("HYPERION_PROJECT_ROOT", raising=False)
            paths.reset_path_cache()

    def test_resolve_path_anchors_relative_but_respects_absolute(self):
        from hyperion.infra.paths import project_root, resolve_path

        root = project_root()
        assert resolve_path("vault") == (root / "vault").resolve()
        assert resolve_path("./vault") == (root / "vault").resolve()
        # An explicitly absolute value must survive untouched, or a user's
        # HYPERION_VAULT_PATH=/data/vault would be silently rewritten.
        absolute = Path(os.sep) / "data" / "vault"
        assert resolve_path(absolute) == absolute
        # Empty falls back to the supplied default, not to the CWD.
        assert resolve_path("", default="reports") == (root / "reports").resolve()
        assert resolve_path(None, default="reports") == (root / "reports").resolve()

    def test_no_positional_parents_walk_survives(self):
        """`parents[N]` for project-root discovery must be gone.

        It is correct only while a file stays exactly N levels deep, so it fails
        silently after any refactor — the worst kind of path bug.
        """
        from hyperion.infra import paths

        offenders: list[str] = []
        for module_path in (
            "hyperion/tools/obscura.py",
            "hyperion/obs/health.py",
            "hyperion/tui/boot.py",
        ):
            src = (paths.project_root() / module_path).read_text(encoding="utf-8")
            tree = ast.parse(src)
            for node in ast.walk(tree):
                # Match `<expr>.parents[<int>]`
                if (
                    isinstance(node, ast.Subscript)
                    and isinstance(node.value, ast.Attribute)
                    and node.value.attr == "parents"
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, int)
                ):
                    offenders.append(f"{module_path}:{node.lineno}")
        assert not offenders, (
            f"positional project-root walk still present at {offenders}: use "
            f"hyperion.infra.paths instead"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Obscura configuration
# ─────────────────────────────────────────────────────────────────────────────


class TestObscuraConfiguration:
    """Obscura must resolve the same way everywhere, and report the truth.

    Three code paths independently decided whether Obscura was available —
    the client, `obs/health.py` and the splash screen — and they disagreed. On
    Linux the splash reported "found" because `obscura-bin/` was non-empty, even
    though it contains only the Windows `.exe`, which cannot execute.
    """

    def test_client_resolves_the_project_binary(self):
        from hyperion.config import get_settings
        from hyperion.infra.paths import obscura_bin_dir
        from hyperion.tools.obscura import ObscuraClient

        bin_dir = obscura_bin_dir()
        if not bin_dir.exists() or not any(bin_dir.iterdir()):
            pytest.skip("no obscura-bin/ in this checkout")

        resolved = ObscuraClient(settings=get_settings())._find_obscura()
        assert Path(resolved).is_absolute(), f"{resolved} is not absolute, so it depends on the CWD"
        assert Path(resolved).is_file()

    def test_configured_path_wins_over_everything(self, tmp_path):
        """An explicit obscura_path must not be overridden by discovery."""
        from hyperion.tools.obscura import ObscuraClient

        fake = tmp_path / "my-obscura"
        fake.write_text("#!/bin/sh\n")
        fake.chmod(0o755)

        class _S:
            obscura_path = str(fake)

        assert ObscuraClient(settings=_S())._find_obscura() == str(fake)

    def test_project_binary_beats_path(self, tmp_path, monkeypatch):
        """A stray `obscura` on PATH must not shadow the shipped binary.

        The old order checked PATH first, so which Obscura ran depended on the
        machine's PATH rather than on the checkout.
        """
        from hyperion.infra.paths import obscura_bin_dir
        from hyperion.tools.obscura import ObscuraClient

        bin_dir = obscura_bin_dir()
        if not bin_dir.exists() or not any(bin_dir.iterdir()):
            pytest.skip("no obscura-bin/ in this checkout")

        decoy = tmp_path / "obscura"
        decoy.write_text("#!/bin/sh\n")
        decoy.chmod(0o755)
        monkeypatch.setenv("PATH", str(tmp_path))

        resolved = ObscuraClient()._find_obscura()
        assert str(decoy) != resolved, "a decoy on PATH shadowed the project's own obscura binary"
        assert str(bin_dir) in resolved

    def test_availability_requires_executability_not_existence(self):
        """Present-but-unrunnable must not count as available.

        Otherwise every Obscura call fails one at a time instead of the
        extraction fallback chain skipping it once.
        """
        from hyperion.config import get_settings
        from hyperion.tools.obscura import ObscuraClient

        ObscuraClient._platform_supported_cache = None
        client = ObscuraClient(settings=get_settings())
        resolved = client._find_obscura()
        available = client._binary_available()

        if sys.platform != "win32" and resolved.endswith(".exe"):
            assert not available, (
                "a Windows .exe was reported available on a non-Windows "
                "platform; it cannot execute here"
            )

    def test_health_and_client_agree(self):
        """The health table must not contradict the client that does the work."""
        from hyperion.config import get_settings
        from hyperion.obs.health import _check_tool
        from hyperion.tools.obscura import ObscuraClient

        settings = get_settings()
        ObscuraClient._platform_supported_cache = None
        client_says_ok = ObscuraClient(settings=settings)._binary_available()
        health = _check_tool("obscura", settings)

        if client_says_ok:
            assert health.status == "OK", f"client can run Obscura but health says {health.status}"
        else:
            assert health.status != "OK", (
                f"health claims Obscura is OK but the client cannot run it ({health.detail})"
            )

    def test_health_obscura_check_is_not_cwd_relative(self, tmp_path, monkeypatch):
        """The reported defect: OFFLINE purely because of the launch directory."""
        from hyperion.config import get_settings
        from hyperion.infra import paths
        from hyperion.obs.health import _check_tool

        settings = get_settings()
        before = _check_tool("obscura", settings)

        monkeypatch.chdir(tmp_path)
        paths.reset_path_cache()
        after = _check_tool("obscura", settings)
        paths.reset_path_cache()

        assert before.status == after.status, (
            f"obscura health changed with the CWD ({before.status} → "
            f"{after.status}): a false reading is what makes users think they "
            f"must configure things by hand"
        )
        assert before.detail == after.detail, (
            f"obscura health detail changed with the CWD ({before.detail!r} → {after.detail!r})"
        )

    def test_health_uses_no_relative_path_literal(self):
        """The actual mechanism of the CWD bug, checked structurally.

        A behavioural before/after-chdir comparison cannot see this defect on
        Linux: the old code looked for the extensionless `obscura-bin/obscura`,
        which is absent from every directory, so it answered OFFLINE
        consistently. The bug only *bites* on Windows, where
        `Path("obscura-bin/obscura.exe")` resolves against the user's shell —
        exactly the platform in the user's screenshots.

        So assert the cause rather than a platform-specific symptom: no relative
        path literal may be used to locate a binary.
        """
        from hyperion.infra.paths import project_root
        from hyperion.obs import health

        src = (project_root() / "hyperion" / "obs" / "health.py").read_text(encoding="utf-8")
        tree = ast.parse(src)

        offenders: list[str] = []
        for node in ast.walk(tree):
            # Match `Path("...")` with a relative literal argument.
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "Path"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                literal = node.args[0].value
                if literal and not Path(literal).is_absolute():
                    offenders.append(f"line {node.lineno}: Path({literal!r})")

        assert not offenders, (
            f"health.py builds paths from relative literals {offenders}: these "
            f"resolve against the user's shell CWD, so the health table reports "
            f"a tool missing purely because of where HYPERION was launched"
        )
        assert health is not None

    def test_health_reports_where_it_looked(self):
        """A bare "not found" is undiagnosable; name the searched location.

        The old detail strings were "obscura.exe not found" / "no Linux binary
        found" — neither says *where*, which is why a present-but-unfound binary
        looked like a configuration problem the user had to solve by hand.
        """
        from hyperion.config import get_settings
        from hyperion.obs.health import _check_tool

        detail = _check_tool("obscura", get_settings()).detail
        assert detail, "obscura health reported no detail at all"
        # Either the resolved binary, or the directory that was searched — but
        # in both cases an absolute path the user can actually go and inspect.
        assert any(part.startswith(os.sep) or ":" in part for part in detail.split()), (
            f"obscura health detail {detail!r} names no absolute location, so a "
            f"wrong answer cannot be diagnosed"
        )

    def test_health_distinguishes_absent_from_unrunnable(self):
        """ "Missing" and "present but wrong platform" need different answers.

        The old code collapsed both into OFFLINE/"no Linux binary found", which
        told the user to install something that was already installed.
        """
        from hyperion.config import get_settings
        from hyperion.infra.paths import obscura_bin_dir
        from hyperion.obs.health import _check_tool
        from hyperion.tools.obscura import ObscuraClient

        bin_dir = obscura_bin_dir()
        if not bin_dir.exists() or not any(bin_dir.iterdir()):
            pytest.skip("no obscura-bin/ in this checkout")

        ObscuraClient._platform_supported_cache = None
        health = _check_tool("obscura", get_settings())
        resolved = ObscuraClient(settings=get_settings())._find_obscura()

        if resolved and Path(resolved).is_file():
            assert health.status in ("OK", "DEGRADED"), (
                f"a binary exists at {resolved} but health says "
                f"{health.status} ({health.detail}) — reporting an installed "
                f"binary as absent sends the user to fix the wrong thing"
            )

    def test_boot_reports_obscura(self):
        """A missing Obscura must be visible at boot, not discovered later."""
        import inspect as _inspect

        from hyperion.tui import boot

        src = _inspect.getsource(boot.run_boot_sequence)
        assert "_obscura_present" in src, (
            "the boot tool-readiness step never mentions Obscura, so a missing "
            "binary only surfaces as unexplained scraping failures mid-engagement"
        )

    def test_obscura_binary_names_are_platform_ordered(self):
        from hyperion.infra.paths import obscura_binary_names

        names = obscura_binary_names()
        assert set(names) == {"obscura", "obscura.exe"}
        if sys.platform == "win32":
            assert names[0] == "obscura.exe", "Windows must prefer the .exe"
        else:
            assert names[0] == "obscura", "POSIX must prefer the extensionless name"


# ─────────────────────────────────────────────────────────────────────────────
# Docker engine autostart
# ─────────────────────────────────────────────────────────────────────────────


class TestDockerAutostart:
    """The shell must start the engine itself, on every platform.

    "i have to mannuly launch the docker optherwise both docker based service is
    not working or starting" — because the old probe checked exactly one path,
    `%ProgramFiles%\\Docker\\Docker\\Docker Desktop.exe`, and only on Windows.
    """

    def test_windows_candidates_cover_real_install_locations(self, monkeypatch):
        from hyperion.infra import services

        monkeypatch.setenv("ProgramFiles", r"C:\Program Files")
        monkeypatch.setenv("LOCALAPPDATA", r"C:\Users\u\AppData\Local")
        monkeypatch.setenv("ProgramW6432", r"C:\Program Files")

        candidates = [str(p) for p in services._windows_desktop_candidates()]
        assert len(candidates) > 1, (
            "only one Docker Desktop location is probed — a per-user or "
            "non-default-drive install is invisible, which is exactly why "
            "auto-start never fired"
        )
        # Per-user install (winget default) must be covered.
        assert any("AppData" in c for c in candidates), (
            "per-user (%LOCALAPPDATA%) install location not probed"
        )
        # Non-default drives must be covered.
        assert any(c.startswith("D:") for c in candidates), (
            "non-default drive install location not probed"
        )
        # No duplicates — each miss costs a filesystem stat.
        lowered = [c.lower() for c in candidates]
        assert len(lowered) == len(set(lowered)), "duplicate candidate paths"

    def test_launcher_handles_every_platform(self):
        """macOS, native Linux, WSL2, and Windows all need start paths."""
        src = inspect.getsource(
            __import__("hyperion.infra.services", fromlist=["x"])._launch_docker_desktop
        )
        for platform in ("MACOS", "LINUX_SYSTEMD", "LINUX_OTHER", "WSL2", "WINDOWS"):
            assert f"Platform.{platform}" in src, f"no {platform} start path"
        assert "systemctl" in src, "no systemd Linux start path"
        assert "_launch_wsl2_docker_desktop" in src, "no WSL2 interop start path"

    async def test_engine_start_is_attempted_when_daemon_is_down(self, monkeypatch):
        """The whole point: a stopped engine must be started, not reported."""
        from hyperion.infra import services

        launched = {"n": 0}
        versions = iter(["", "", "27.1.1"])

        async def _version():
            return next(versions, "27.1.1")

        async def _launch():
            launched["n"] += 1
            return "launching Docker Desktop.exe"

        monkeypatch.setattr(services, "docker_available", lambda: True)
        monkeypatch.setattr(services, "docker_engine_version", _version)
        monkeypatch.setattr(services, "_launch_docker_desktop", _launch)
        monkeypatch.setattr(services.asyncio, "sleep", lambda *_a, **_k: _noop())

        status = await services.ensure_docker_engine(wait_seconds=30)

        assert launched["n"] == 1, "the engine was never actually launched"
        assert status.ok, f"engine did not come up: {status.detail}"
        assert status.started_by_us, "status does not record that we started it"

    async def test_running_daemon_is_not_relaunched(self, monkeypatch):
        """Relaunching Docker Desktop on every boot steals window focus."""
        from hyperion.infra import services

        launched = {"n": 0}

        async def _version():
            return "27.1.1"

        async def _launch():
            launched["n"] += 1
            return "launched"

        monkeypatch.setattr(services, "docker_available", lambda: True)
        monkeypatch.setattr(services, "docker_engine_version", _version)
        monkeypatch.setattr(services, "_launch_docker_desktop", _launch)

        status = await services.ensure_docker_engine()
        assert status.ok
        assert launched["n"] == 0, "already-running engine was relaunched"
        assert not status.started_by_us

    async def test_missing_docker_cli_warns_and_does_not_crash(self, monkeypatch):
        from hyperion.infra import services

        monkeypatch.setattr(services, "docker_available", lambda: False)
        status = await services.ensure_docker_engine()
        assert not status.ok
        assert status.state == services.WARN, (
            "a missing Docker CLI must degrade, not fail the whole boot"
        )

    def test_boot_uses_the_shared_engine_starter(self):
        from hyperion.tui import boot

        src = inspect.getsource(boot.run_boot_sequence)
        assert "ensure_docker_engine" in src
        # The old inline single-path probe must be gone.
        assert "Docker Desktop.exe" not in src, (
            "boot still contains its own hardcoded Docker Desktop path"
        )
        assert "ProgramFiles" not in src

    def test_consult_also_starts_the_engine(self):
        """The headless path assumed a running daemon and searched nothing."""
        from hyperion import cli

        src = inspect.getsource(cli._run_engagement)
        assert "ensure_docker_ready" in src, (
            "`hyperion consult` does not start the container engine, so a "
            "scripted run on an idle machine silently loses all search"
        )

    def test_w13_platform_detection_distinguishes_wsl2(self, monkeypatch):
        from hyperion.infra import services

        monkeypatch.setattr(services.sys, "platform", "linux")
        monkeypatch.setattr(
            services,
            "_read_platform_file",
            lambda path: "Linux microsoft-standard-WSL2" if path == "/proc/version" else "",
        )
        assert services.detect_platform() is services.Platform.WSL2

        monkeypatch.setattr(services, "_read_platform_file", lambda _path: "Linux generic")
        monkeypatch.setattr(
            services.Path, "exists", lambda self: str(self) == "/run/systemd/system"
        )
        assert services.detect_platform() is services.Platform.LINUX_SYSTEMD

    async def test_w13_wsl2_missing_cli_has_distinct_integration_fix(self, monkeypatch):
        from hyperion.infra import services

        monkeypatch.setattr(services, "detect_platform", lambda: services.Platform.WSL2)
        monkeypatch.setattr(services, "docker_available", lambda: False)
        status = await services.ensure_docker_engine()
        assert "WSL integration" in status.detail
        assert "Docker CLI" in status.detail

    async def test_w13_wsl2_interop_disabled_is_actionable(self, monkeypatch):
        from hyperion.infra import services

        async def _no_version():
            return ""

        async def _interop_disabled():
            return "WSL_INTEROP_DISABLED"

        monkeypatch.setattr(services, "detect_platform", lambda: services.Platform.WSL2)
        monkeypatch.setattr(services, "docker_available", lambda: True)
        monkeypatch.setattr(services, "docker_engine_version", _no_version)
        monkeypatch.setattr(services, "_launch_docker_desktop", _interop_disabled)
        status = await services.ensure_docker_engine()
        assert "WSL interop is disabled" in status.detail
        assert "wsl.exe --shutdown" in status.detail

    async def test_w13_native_linux_never_uses_prompting_sudo(self, monkeypatch):
        from hyperion.infra import services

        commands: list[list[str]] = []

        async def _run(cmd, timeout=30.0):
            commands.append(cmd)
            return (1, "", "failed")

        monkeypatch.setattr(services, "detect_platform", lambda: services.Platform.LINUX_SYSTEMD)
        monkeypatch.setattr(services, "run_command", _run)
        monkeypatch.setattr(services.os, "geteuid", lambda: 1000)
        monkeypatch.setattr(
            services.shutil, "which", lambda name: "/usr/bin/sudo" if name == "sudo" else None
        )
        assert await services._launch_docker_desktop() == ""
        assert ["sudo", "-n", "true"] in commands
        assert all(cmd[:1] != ["sudo"] or "-n" in cmd for cmd in commands)

    def test_timestamp_cache_older_than_source_does_not_refuse_boot(self, tmp_path):
        """A post-pull timestamp cache is rejected by CPython, not executed."""
        from hyperion.infra import provenance

        package_dir = tmp_path / "hyperion"
        package_dir.mkdir()
        source = package_dir / "module.py"
        source.write_text("VALUE = 'old'\n", encoding="utf-8")
        pyc = Path(py_compile.compile(str(source), doraise=True))

        # Reproduce the screenshot: git updates source after a cache was made.
        source.write_text("VALUE = 'new'\n", encoding="utf-8")
        newer = pyc.stat().st_mtime + 10
        os.utime(source, (newer, newer))

        assert pyc.stat().st_mtime < source.stat().st_mtime
        assert provenance._find_stale_pycache(package_dir) == []
        snapshot = provenance.Provenance(
            package_dir=str(package_dir),
            repo_root=str(tmp_path),
            git_sha="abc1234",
            git_dirty=False,
            install_mode="editable",
            stale_pycache=[],
        )
        assert provenance.refusal_reason(snapshot) is None

    def test_mismatched_unchecked_hash_cache_still_refuses_boot(self, tmp_path):
        """Retain the hard stop only where CPython can trust stale bytecode."""
        from hyperion.infra import provenance

        package_dir = tmp_path / "hyperion"
        package_dir.mkdir()
        source = package_dir / "module.py"
        source.write_text("VALUE = 'old'\n", encoding="utf-8")
        pyc = Path(py_compile.compile(
            str(source),
            doraise=True,
            invalidation_mode=py_compile.PycInvalidationMode.UNCHECKED_HASH,
        ))
        source.write_text("VALUE = 'new'\n", encoding="utf-8")

        unsafe = provenance._find_stale_pycache(package_dir)
        assert unsafe == [str(pyc)]
        snapshot = provenance.Provenance(
            package_dir=str(package_dir),
            repo_root=str(tmp_path),
            git_sha="abc1234",
            git_dirty=False,
            install_mode="editable",
            stale_pycache=unsafe,
        )
        reason = provenance.refusal_reason(snapshot)
        assert reason is not None
        assert "Unsafe unchecked-hash bytecode" in reason

    def test_w13_boot_banner_reports_detected_platform(self, monkeypatch):
        from hyperion.infra import provenance, services

        snapshot = provenance.Provenance(
            package_dir="/repo/hyperion",
            repo_root="/repo",
            git_sha="abc1234",
            git_dirty=False,
            install_mode="editable",
            stale_pycache=[],
        )
        monkeypatch.setattr(services, "detect_platform", lambda: services.Platform.WSL2)
        monkeypatch.setattr(provenance, "detect_platform", services.detect_platform)
        assert "platform=wsl2" in provenance.banner(snapshot)


async def _noop() -> None:
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Container specs and readiness
# ─────────────────────────────────────────────────────────────────────────────


class TestContainerSpecs:
    """`docker run` argv must be built from resolved absolute paths."""

    def test_mounts_are_absolute_and_docker_shaped(self):
        from hyperion.infra.services import _docker_run_argv, searxng_spec

        spec = searxng_spec()
        argv = _docker_run_argv(spec, spec.image)
        bind_mounts = [
            argv[i + 1]
            for i, value in enumerate(argv)
            if value == "-v" and argv[i + 1].endswith(":ro")
        ]
        assert bind_mounts, "SearXNG runs with no settings mount"
        for mount in bind_mounts:
            host = mount.split(":/")[0]
            assert "\\" not in mount, (
                f"{mount} contains a backslash; Docker requires forward slashes "
                f"even for Windows host paths"
            )
            assert Path(host).is_absolute() or host.endswith(":"), (
                f"mount source {host} is not absolute, so it resolves against the CWD inside docker"
            )

    def test_searxng_publishes_all_replica_ports_on_loopback(self):
        from hyperion.infra.services import SEARXNG_REPLICAS, _docker_run_argv, searxng_specs

        for replica, spec in zip(SEARXNG_REPLICAS, searxng_specs(), strict=True):
            argv = _docker_run_argv(spec, spec.image)
            assert f"127.0.0.1:{replica.port}:{spec.container_port}" in argv

    def test_client_pool_matches_published_profiles(self):
        """Every category must resolve to the intended published replica."""
        from hyperion.tools.searxng import SearxngPool

        pool = SearxngPool.from_config()
        assert pool.endpoint_for(category="science").port == 8888
        assert pool.endpoint_for(category="it").port == 8889
        assert pool.endpoint_for(category="general").port == 8890

    def test_every_spec_declares_a_readiness_probe(self):
        from hyperion.infra.services import all_specs

        for spec in all_specs():
            if spec.name == "hyperion-valkey":
                assert spec.host_port == 0  # internal only; readiness uses valkey-cli PING
            else:
                assert spec.health_path.startswith("/"), f"{spec.name} has no HTTP readiness path"
                assert spec.health_url().startswith("http://127.0.0.1:")
            assert spec.ready_timeout >= 30, (
                f"{spec.name} readiness timeout {spec.ready_timeout}s is shorter "
                f"than a realistic cold start"
            )

    def test_no_fixed_sleep_stands_in_for_readiness(self):
        """`await asyncio.sleep(3.0)` used to be the entire readiness check."""
        from hyperion.infra import services

        src = inspect.getsource(services.ensure_container)
        assert "wait_until_ready" in src
        tree = ast.parse(inspect.getsource(services))
        run_src = inspect.getsource(services.ensure_container)
        assert "sleep(3" not in run_src, "fixed sleep still used as readiness"
        assert tree is not None

    async def test_failed_image_resolution_is_reported_not_raised(self, monkeypatch):
        """The screenshot error path: no usable tag must yield a status."""
        from hyperion.infra import services

        async def _absent(image):
            return False

        async def _pull_fails(image, timeout=600.0):
            return False, "manifest unknown"

        monkeypatch.setattr(services, "image_present_locally", _absent)
        monkeypatch.setattr(services, "pull_image", _pull_fails)

        spec = services.searxng_spec()
        image, used_fallback, detail = await services.resolve_image(spec)
        assert image == "", "resolution claimed success with no usable image"
        assert "manifest unknown" in detail, (
            "the real registry error must reach the user; a generic message is "
            "what made the original failure hard to diagnose"
        )

    async def test_aged_out_primary_falls_back(self, monkeypatch):
        """The actual fix: a reaped pin must not be fatal."""
        from hyperion.infra import services

        spec = services.searxng_spec()

        async def _absent(image):
            return False

        async def _pull(image, timeout=600.0):
            # Simulate the primary pin having been reaped from the registry.
            if image == spec.image:
                return False, f"manifest for {image} not found"
            return True, ""

        monkeypatch.setattr(services, "image_present_locally", _absent)
        monkeypatch.setattr(services, "pull_image", _pull)

        image, used_fallback, detail = await services.resolve_image(spec)
        assert image == spec.image_fallback, (
            f"expected fallback {spec.image_fallback}, got {image!r}: an "
            f"aged-out pin is still fatal"
        )
        assert used_fallback, "fallback use was not flagged"
        assert spec.image_fallback in detail, (
            "the substituted tag must be reported, not silently used"
        )

    async def test_local_image_avoids_the_network(self, monkeypatch):
        """Offline runs must work when the image is already pulled."""
        from hyperion.infra import services

        spec = services.searxng_spec()
        pulled = {"n": 0}

        async def _present(image):
            return image == spec.image

        async def _pull(image, timeout=600.0):
            pulled["n"] += 1
            return False, "network unreachable"

        monkeypatch.setattr(services, "image_present_locally", _present)
        monkeypatch.setattr(services, "pull_image", _pull)

        image, used_fallback, _ = await services.resolve_image(spec)
        assert image == spec.image
        assert not used_fallback
        assert pulled["n"] == 0, "pulled over the network despite a local image"


# ─────────────────────────────────────────────────────────────────────────────
# Settings paths
# ─────────────────────────────────────────────────────────────────────────────


class TestSettingsPaths:
    """Vault/report/asset paths must not follow the user's shell."""

    @pytest.mark.parametrize("field", ["vault_path", "reports_dir", "assets_dir"])
    def test_paths_are_absolute(self, field):
        from hyperion.config import Settings

        value = getattr(Settings(), field)
        assert Path(value).is_absolute(), (
            f"{field} is {value!r} — relative, so `hyperion consult` run from a "
            f"home directory writes there instead of the project"
        )

    def test_explicit_absolute_path_is_respected(self, tmp_path):
        from hyperion.config import Settings

        s = Settings(vault_path=str(tmp_path / "mine"))
        assert Path(s.vault_path) == tmp_path / "mine"

    def test_second_brain_uses_the_resolved_vault(self):
        from hyperion.config import get_settings
        from hyperion.tools.second_brain import SecondBrainClient

        settings = get_settings()
        client = SecondBrainClient(settings=settings)
        assert client._vault_path.is_absolute()
        assert client._vault_path == Path(settings.vault_path)

    def test_second_brain_default_is_not_cwd_relative(self, tmp_path, monkeypatch):
        from hyperion.infra import paths
        from hyperion.tools.second_brain import SecondBrainClient

        before = SecondBrainClient()._vault_path
        monkeypatch.chdir(tmp_path)
        paths.reset_path_cache()
        after = SecondBrainClient()._vault_path
        paths.reset_path_cache()
        assert before == after, (
            f"vault moved with the CWD ({before} → {after}): notes written in "
            f"one session become invisible to the next"
        )

    def test_boot_vault_step_reads_a_real_attribute(self):
        """The step read `second_brain_vault`, which does not exist.

        `getattr` returned None, the step took its "default path" branch and
        reported "vault ready" without ever looking at a directory — a check
        that could never fail and never told the truth.
        """
        import textwrap

        from hyperion.config import Settings
        from hyperion.tui import boot

        src = inspect.getsource(boot.run_boot_sequence)

        # Inspect the attribute names the code actually reads, not the raw text:
        # the fix documents the old attribute as the bug it repairs, and a test
        # that forbids naming a bug forces the fix to go unexplained.
        tree = ast.parse(textwrap.dedent(src))
        read_attrs = {
            node.args[1].value
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        }

        assert "second_brain_vault" not in read_attrs, (
            "boot still reads the nonexistent `second_brain_vault` attribute"
        )
        assert "vault_path" in read_attrs, (
            f"boot does not read vault_path (reads: {sorted(read_attrs)})"
        )

        # And every attribute the boot sequence reads must actually exist, or
        # the step silently takes its fallback branch forever.
        settings = Settings()
        missing = [
            name for name in read_attrs if not name.startswith("_") and not hasattr(settings, name)
        ]
        assert not missing, (
            f"boot reads Settings attributes that do not exist: {missing} — "
            f"getattr returns the default, so the check can never fail and "
            f"never reports the truth"
        )
