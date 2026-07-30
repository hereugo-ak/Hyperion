"""D-10 · obscura.exe blocked at load time must surface as BLOCKED, never OK.

The 07-30 run (screenshot: Windows Security — "we can't confirm who published
obscura.exe") announced "Scraping … (Obscura)" three times and every call was
a no-op: availability was an existence+mode check, and Windows Defender blocks
an unsigned binary at LOAD time, in-process. These tests lock the load probe:

1. On win32 the probe must actually LAUNCH the binary (`--version`) — the old
   code returned True unconditionally.
2. A PermissionError / OSError at launch marks the client unavailable AND
   records the reason (so fetch/scrape and health can say why).
3. Health reports BLOCKED (not OK, not merely OFFLINE) for a binary the OS
   refuses to load — the one status that tells the operator to act.
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace

import pytest

from hyperion.tools.obscura import ObscuraClient


@pytest.fixture(autouse=True)
def reset_probe_cache():
    ObscuraClient._platform_supported_cache = None
    ObscuraClient._unavailable_reason = ""
    yield
    ObscuraClient._platform_supported_cache = None
    ObscuraClient._unavailable_reason = ""


def _client(tmp_path) -> ObscuraClient:
    return ObscuraClient(settings=SimpleNamespace(obscura_path=""))


class TestLoadProbe:
    def test_permission_error_means_blocked(self):
        import hyperion.tools.obscura as mod

        orig = mod.subprocess.run
        mod.subprocess.run = _raise(PermissionError(5, "Access is denied"))
        try:
            ran, reason = ObscuraClient._exec_load_probe("obscura.exe")
        finally:
            mod.subprocess.run = orig
        assert ran is False
        assert "PermissionError" in reason
        assert "unsigned" in reason.lower() or "antivirus" in reason.lower()

    def test_oserror_means_blocked(self):
        import hyperion.tools.obscura as mod

        orig = mod.subprocess.run
        mod.subprocess.run = _raise(OSError(4551, "This app has been blocked"))
        try:
            ran, reason = ObscuraClient._exec_load_probe("obscura.exe")
        finally:
            mod.subprocess.run = orig
        assert ran is False
        assert "refused to load" in reason

    def test_successful_version_means_runnable(self):
        import hyperion.tools.obscura as mod

        orig = mod.subprocess.run
        mod.subprocess.run = lambda *a, **k: subprocess.CompletedProcess(
            args=a[0], returncode=0, stdout=b"obscura 1.2.3", stderr=b""
        )
        try:
            ran, reason = ObscuraClient._exec_load_probe("obscura.exe")
        finally:
            mod.subprocess.run = orig
        assert ran is True
        assert reason == ""

    def test_timeout_means_blocked(self):
        import hyperion.tools.obscura as mod

        orig = mod.subprocess.run
        mod.subprocess.run = _raise(subprocess.TimeoutExpired(cmd="obscura", timeout=5))
        try:
            ran, reason = ObscuraClient._exec_load_probe("obscura.exe")
        finally:
            mod.subprocess.run = orig
        assert ran is False
        assert "timed out" in reason


class TestWindowsProbeLaunchesBinary:
    def test_win32_does_not_get_a_free_pass(self, tmp_path, monkeypatch):
        """The core D-10 regression: win32 must not return True without
        launching anything. A blocked binary on Windows is UNAVAILABLE."""
        exe = tmp_path / "obscura.exe"
        exe.write_bytes(b"MZ fake")  # exists, "executable" — but OS will refuse
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "hyperion.tools.obscura.subprocess.run",
            _raise(PermissionError(5, "Access is denied")),
        )
        client = ObscuraClient(settings=SimpleNamespace(obscura_path=str(exe)))
        assert client._probe_platform_support() is False
        assert "refused to load" in ObscuraClient._unavailable_reason
        assert client._binary_available() is False

    def test_win32_working_binary_passes(self, tmp_path, monkeypatch):
        exe = tmp_path / "obscura.exe"
        exe.write_bytes(b"MZ fake")
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "hyperion.tools.obscura.subprocess.run",
            lambda *a, **k: subprocess.CompletedProcess(
                args=a[0], returncode=0, stdout=b"obscura 1.2.3", stderr=b""
            ),
        )
        client = ObscuraClient(settings=SimpleNamespace(obscura_path=str(exe)))
        assert client._probe_platform_support() is True
        assert client._binary_available() is True
        assert client.unavailable_detail() == ""


class TestCallersCarryReason:
    @pytest.mark.asyncio
    async def test_fetch_error_names_the_block(self, tmp_path, monkeypatch):
        exe = tmp_path / "obscura.exe"
        exe.write_bytes(b"MZ fake")
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "hyperion.tools.obscura.subprocess.run",
            _raise(PermissionError(5, "Access is denied")),
        )
        client = ObscuraClient(settings=SimpleNamespace(obscura_path=str(exe)))
        result = await client.fetch("https://example.com")
        assert result.error
        assert "refused to load" in result.error
        assert "fallback" in result.error

    def test_unavailable_detail_reports_reason(self, tmp_path, monkeypatch):
        exe = tmp_path / "obscura.exe"
        exe.write_bytes(b"MZ fake")
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "hyperion.tools.obscura.subprocess.run",
            _raise(OSError(4551, "blocked")),
        )
        client = ObscuraClient(settings=SimpleNamespace(obscura_path=str(exe)))
        assert "refused to load" in client.unavailable_detail()


class TestHealthSaysBlocked:
    def test_blocked_binary_reports_blocked(self, tmp_path, monkeypatch):
        exe = tmp_path / "obscura.exe"
        exe.write_bytes(b"MZ fake")
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "hyperion.tools.obscura.subprocess.run",
            _raise(PermissionError(5, "Access is denied")),
        )
        from hyperion.obs.health import _check_tool

        settings = SimpleNamespace(obscura_path=str(exe))
        h = _check_tool("obscura", settings)
        assert h.status == "BLOCKED"
        assert "refused to load" in h.detail

    def test_missing_binary_still_offline(self, tmp_path, monkeypatch):
        """No binary anywhere → OFFLINE, never BLOCKED. _find_obscura must not
        locate any real binary: empty project dir, no PATH hit, bogus
        configured path."""
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setattr(
            "hyperion.tools.obscura.obscura_bin_dir", lambda: tmp_path / "empty"
        )
        monkeypatch.setattr(
            "hyperion.tools.obscura.obscura_binary_names", lambda: ["obscura.exe"]
        )
        monkeypatch.setattr("hyperion.tools.obscura.shutil.which", lambda name: None)
        from hyperion.obs.health import _check_tool

        settings = SimpleNamespace(obscura_path=str(tmp_path / "none" / "obscura.exe"))
        h = _check_tool("obscura", settings)
        assert h.status == "OFFLINE"


def _raise(exc: BaseException):
    def _run(*a, **k):
        raise exc

    return _run
