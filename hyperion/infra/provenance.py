"""HYPERION build provenance — which files and which commit are loaded.

W-01 (RC-1): a merged fix is not a running fix. The previous audit assumed
that the merged commit was the code executing; RC-1 proved a site-packages
shadow plus stale bytecode served pre-fix output for fifteen correct
commits. This module makes the loaded build physically observable at every
shell boot, and refuses to boot in the two configurations that can actually
execute code different from the checkout:

1. a ``site-packages`` copy shadowing a git checkout on ``sys.path``
2. an unchecked-hash ``.pyc`` whose embedded source hash does not match disk

Timestamp caches older than source are deliberately not refused. CPython
validates their embedded source mtime and size before execution, recompiling
from source when they differ; this is the normal state immediately after a
``git pull`` and cannot execute the stale cache.

Two collection paths exist because the boot sequence is async but the PDF
render path is sync: ``collect_async`` does the full collection (including
the bounded git subprocesses) and caches the result module-globally;
``collect`` is the sync wrapper for pre-loop callers; ``current`` returns
the cached snapshot for the render path without re-running git.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Reuse the bounded command runner from services.py. It is async; the sync
# wrapper drives it on a private loop for pre-loop callers.
from hyperion.infra.services import detect_platform, run_command

_GIT_TIMEOUT_SECONDS = 5.0
_MAX_ANCESTOR_LEVELS = 8

# The one cached snapshot per process, populated by the first collection.
_cached: Provenance | None = None


@dataclass(frozen=True)
class Provenance:
    """The loaded build's identity. Collected once per shell boot.

    F-08 (CHIEF_AUDIT_FIX0.3): the runtime fingerprint must prove that the
    running process matches the audited checkout AND the audited policy.
    The audit requires: Git commit, Python executable, import path, source
    hash, generated profile hash, settings hash, timeout values and search
    budgets — all printed at boot and attachable to engagement artifacts.
    """

    package_dir: str  # Path(hyperion.__file__).parent, resolved
    repo_root: str | None  # nearest ancestor containing .git, else None
    git_sha: str | None  # short SHA of HEAD in repo_root
    git_dirty: bool  # working tree has modifications
    install_mode: str  # "editable" | "site-packages" | "unknown"
    # Unsafe unchecked-hash caches retained under the historical field name.
    stale_pycache: list[str]
    # F-08: content hashes of the files that define the loaded build and its
    # policy. ``source_hash`` covers the hyperion package tree; the settings
    # and generated SearXNG profile hashes let an operator prove the mounted
    # YAML matches the repository's YAML without trusting a timestamp.
    source_hash: str = ""
    settings_hash: str = ""
    profile_hashes: dict[str, str] = field(default_factory=dict)
    # F-08: the exact policy numbers the audit demands be observable —
    # timeouts, retry budgets and search caps as executed, not as documented.
    policy: dict[str, object] = field(default_factory=dict)


def _package_dir() -> Path:
    import hyperion  # local import to avoid circularity at module load

    return Path(hyperion.__file__).parent.resolve()


def _find_repo_root(package_dir: Path) -> Path | None:
    """Walk parents of package_dir only, looking for a .git directory.

    Never walks the whole filesystem; stops at the filesystem root or after
    eight levels, per W-01's failure-mode guidance.
    """
    current = package_dir
    for _ in range(_MAX_ANCESTOR_LEVELS):
        if (current / ".git").exists():
            return current
        if current.parent == current:
            break
        current = current.parent
    return None


def _detect_install_mode(package_dir: Path, repo_root: Path | None) -> str:
    """Classify the install mode from the package's resolved location."""
    if "site-packages" in package_dir.parts:
        return "site-packages"
    if repo_root is not None:
        try:
            package_dir.relative_to(repo_root)
            return "editable"
        except ValueError:
            pass
    return "unknown"


def _find_shadowed_checkout(package_dir: Path) -> Path | None:
    """When running from site-packages, find a git checkout of hyperion on
    sys.path that this install is shadowing (the exact RC-1 case)."""
    package_resolved = str(package_dir.resolve())
    for entry in sys.path:
        if not entry:
            continue
        candidate = Path(entry) / "hyperion"
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if str(resolved) == package_resolved:
            continue
        if candidate.is_dir() and (candidate.parent / ".git").exists():
            return candidate
    return None


def _find_stale_pycache(package_dir: Path) -> list[str]:
    """Find caches that CPython can execute without validating the source.

    A filesystem mtime comparison is not a valid safety check. Timestamp-based
    ``.pyc`` files contain the source mtime and size in their header; CPython
    rejects and recompiles them when source changes. Checked-hash caches are
    similarly validated. Both are safe even when the cache file itself is
    older than the source, which commonly happens after ``git pull``.

    Unchecked-hash caches are the exception: CPython normally trusts them. We
    therefore compare their embedded hash ourselves and refuse only a mismatch.
    Bad magic, malformed headers, and caches for another interpreter are skipped
    because CPython will reject them rather than execute their bytecode.
    """
    import importlib.util

    stale: list[str] = []
    for py_file in package_dir.rglob("*.py"):
        cache_dir = py_file.parent / "__pycache__"
        if not cache_dir.is_dir():
            continue
        for pyc_file in cache_dir.glob(py_file.stem + ".cpython-*.pyc"):
            try:
                header = pyc_file.read_bytes()[:16]
                if len(header) < 16 or header[:4] != importlib.util.MAGIC_NUMBER:
                    continue
                flags = int.from_bytes(header[4:8], "little")
                hash_based = bool(flags & 0x01)
                check_source = bool(flags & 0x02)
                if not hash_based or check_source or flags & ~0x03:
                    continue
                source_hash = importlib.util.source_hash(py_file.read_bytes())
                if header[8:16] != source_hash:
                    stale.append(str(pyc_file))
            except OSError:
                continue
    return stale


def _hash_tree(root: Path) -> str:
    """Content hash of every ``.py`` file under ``root`` (F-08 source hash)."""
    import hashlib

    digest = hashlib.sha256()
    try:
        for py_file in sorted(root.rglob("*.py")):
            digest.update(py_file.relative_to(root).as_posix().encode())
            digest.update(py_file.read_bytes())
    except OSError:
        return ""
    return digest.hexdigest()[:16]


def _hash_file(path: Path | None) -> str:
    """Content hash of one file, or "" when absent/unreadable."""
    import hashlib

    if path is None:
        return ""
    try:
        if not path.is_file():
            return ""
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return ""


def _profile_hashes(root: Path) -> dict[str, str]:
    """Hash of each generated ``searxng_settings.*.yml`` profile (F-08).

    Lets an operator prove the mounted YAML matches the repository's
    generated YAML — the audit's "runtime profile hash" diagnostic —
    without trusting file timestamps.
    """
    result: dict[str, str] = {}
    try:
        for yml in sorted(root.glob("searxng_settings.*.yml")):
            if yml.name == "searxng_settings.yml":
                continue
            result[yml.name] = _hash_file(yml)
    except OSError:
        return {}
    return result


def _policy_snapshot() -> dict[str, object]:
    """The executed timeout/retry/budget policy (F-08).

    Read from the actual modules the runtime uses, so the fingerprint always
    matches the loaded code — never a documentation string that drifted.
    """
    policy: dict[str, object] = {}
    try:
        from hyperion.tools.searxng import SearxNGClient

        policy.update({
            "searxng_request_timeout_s": SearxNGClient.REQUEST_TIMEOUT,
            "search_budget_cap": SearxNGClient.SEARCH_BUDGET_CAP,
            "per_owner_budget_cap": SearxNGClient.PER_OWNER_BUDGET_CAP,
            "search_max_retries": SearxNGClient.MAX_RETRIES,
        })
    except Exception as exc:  # noqa: BLE001 - fingerprint must never crash boot
        logger.debug("policy: searxng budget values unavailable: %s", exc)
    try:
        from hyperion.orchestrator import WorkflowEngine

        policy.update({
            "task_timeout_s": WorkflowEngine.TASK_TIMEOUT_SECONDS,
            "specialist_timeout_s": WorkflowEngine.SPECIALIST_TIMEOUT_SECONDS,
            "max_quality_iterations": WorkflowEngine.MAX_QUALITY_ITERATIONS,
        })
    except Exception as exc:  # noqa: BLE001 - fingerprint must never crash boot
        logger.debug("policy: orchestrator timeouts unavailable: %s", exc)
    try:
        from hyperion.agents.base import BaseAgent

        policy.update({"sub_agent_total_ceiling": BaseAgent.SUB_AGENT_TOTAL_CEILING})
    except Exception as exc:  # noqa: BLE001 - fingerprint must never crash boot
        logger.debug("policy: sub-agent ceiling unavailable: %s", exc)
    try:
        from hyperion.config import get_settings

        settings = get_settings()
        policy.update({"quality_source_floor": settings.quality_source_floor})
    except Exception as exc:  # noqa: BLE001 - fingerprint must never crash boot
        logger.debug("policy: quality source floor unavailable: %s", exc)
    return policy


def _source_hash(package_dir: Path) -> str:
    """Content hash of the loaded hyperion package tree (F-08)."""
    return _hash_tree(package_dir)


def _settings_hash(repo_root: Path | None) -> str:
    """Hash of the active SearXNG settings file (F-08)."""
    if repo_root is None:
        return ""
    return _hash_file(repo_root / "searxng_settings.yml")


def _sync_snapshot() -> Provenance:
    """Metadata-only snapshot with no git subprocesses — the fallback for
    contexts where the async runner cannot be driven."""
    package_dir = _package_dir()
    repo_root = _find_repo_root(package_dir)
    return Provenance(
        package_dir=str(package_dir),
        repo_root=str(repo_root) if repo_root is not None else None,
        git_sha=None,
        git_dirty=False,
        install_mode=_detect_install_mode(package_dir, repo_root),
        stale_pycache=_find_stale_pycache(package_dir),
        source_hash=_source_hash(package_dir),
        settings_hash=_settings_hash(repo_root),
        profile_hashes=_profile_hashes(package_dir.parent),
        policy=_policy_snapshot(),
    )


async def collect_async() -> Provenance:
    """Full provenance collection including the bounded git subprocesses.

    Called by the shell boot sequence (which has a running event loop) so
    the banner always carries the real SHA — never the "unknown" fallback
    that a sync-collect-from-async-context would produce.
    """
    global _cached
    package_dir = _package_dir()
    repo_root = _find_repo_root(package_dir)

    git_sha: str | None = None
    git_dirty = False
    if repo_root is not None:
        try:
            rc, out, _err = await run_command(
                ["git", "rev-parse", "--short", "HEAD"],
                timeout=_GIT_TIMEOUT_SECONDS,
            )
            if rc == 0 and out.strip():
                git_sha = out.strip().splitlines()[0]
            rc2, out2, _err2 = await run_command(
                ["git", "status", "--porcelain"],
                timeout=_GIT_TIMEOUT_SECONDS,
            )
            if rc2 == 0:
                git_dirty = bool(out2.strip())
        except Exception:  # noqa: BLE001 - provenance must never crash boot
            git_sha = None
            git_dirty = False

    _cached = Provenance(
        package_dir=str(package_dir),
        repo_root=str(repo_root) if repo_root is not None else None,
        git_sha=git_sha,
        git_dirty=git_dirty,
        install_mode=_detect_install_mode(package_dir, repo_root),
        stale_pycache=_find_stale_pycache(package_dir),
        source_hash=_source_hash(package_dir),
        settings_hash=_settings_hash(repo_root),
        profile_hashes=_profile_hashes(package_dir.parent),
        policy=_policy_snapshot(),
    )
    return _cached


def collect() -> Provenance:
    """Collect the provenance of the currently loaded build (sync wrapper).

    For callers before an event loop exists. Inside a running loop use
    ``collect_async``; calling this from async context returns the cached
    snapshot if one exists, else a metadata-only snapshot (no git).
    """
    global _cached
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None and loop.is_running():
        if _cached is None:
            _cached = _sync_snapshot()
        return _cached
    try:
        return asyncio.run(collect_async())
    except Exception:  # noqa: BLE001 - provenance must never crash the boot
        _cached = _sync_snapshot()
        return _cached


def current() -> Provenance:
    """Return the cached provenance snapshot for the render path.

    The PDF post-pass runs in sync code long after boot; it must not spawn
    git subprocesses per render. Falls back to a one-shot sync collection
    when nothing has been collected yet (e.g. a bare render invocation).
    """
    if _cached is None:
        return collect()
    return _cached


def banner(provenance: Provenance) -> str:
    """Render the boot banner, e.g.

    HYPERION  build 87f0582  editable  /home/user/webapp/hyperion

    F-08: the fingerprint line carries the content hashes and the executed
    policy so a screenshot alone can prove (or refute) that the running
    build matches the audited checkout.
    """
    sha = provenance.git_sha or "unknown"
    dirty = " +dirty" if provenance.git_dirty else ""
    profile_bits = "".join(
        f" {name}={h}" for name, h in sorted(provenance.profile_hashes.items())
    )
    return (
        f"HYPERION  build {sha}{dirty}  {provenance.install_mode}  "
        f"platform={detect_platform().value}  {provenance.package_dir}\n"
        f"FINGERPRINT source={provenance.source_hash or 'n/a'} "
        f"settings={provenance.settings_hash or 'n/a'}{profile_bits}\n"
        f"POLICY {provenance.policy}"
    )


def refusal_reason(provenance: Provenance) -> str | None:
    """Return the hard-refusal reason, or None when boot may proceed.

    Refusal is limited to configurations capable of executing code that does
    not correspond to the active checkout; the message states the fix.
    """
    if provenance.install_mode == "site-packages":
        shadow = _find_shadowed_checkout(Path(provenance.package_dir))
        if shadow is not None:
            return (
                "HYPERION is running from a site-packages copy while a git "
                f"checkout exists at {shadow}. The checkout is shadowed and "
                "your edits are not the code that is running.\n"
                "Fix: pip install -e ."
            )
    if provenance.stale_pycache:
        listed = "\n".join(f"  {p}" for p in provenance.stale_pycache[:10])
        more = (
            f"\n  … and {len(provenance.stale_pycache) - 10} more"
            if len(provenance.stale_pycache) > 10
            else ""
        )
        return (
            "Unsafe unchecked-hash bytecode does not match its source:\n"
            f"{listed}{more}\n"
            "The interpreter may execute code that differs from the source on "
            "disk.\n"
            "Fix: find . -name __pycache__ -type d -prune -exec rm -rf {} +"
        )
    return None
