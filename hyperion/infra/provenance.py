"""HYPERION build provenance — which files and which commit are loaded.

W-01 (RC-1): a merged fix is not a running fix. The previous audit assumed
that the merged commit was the code executing; RC-1 proved a site-packages
shadow plus stale bytecode served pre-fix output for fifteen correct
commits. This module makes the loaded build physically observable at every
shell boot, and refuses to boot in the two configurations that produced
that failure:

1. a ``site-packages`` copy shadowing a git checkout on ``sys.path``
2. stale ``.pyc`` bytecode newer-source pairs under the package directory

The refusal is a hard stop, not a warning — a warning is what got us here.

Two collection paths exist because the boot sequence is async but the PDF
render path is sync: ``collect_async`` does the full collection (including
the bounded git subprocesses) and caches the result module-globally;
``collect`` is the sync wrapper for pre-loop callers; ``current`` returns
the cached snapshot for the render path without re-running git.
"""

from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from pathlib import Path

# Reuse the bounded command runner from services.py. It is async; the sync
# wrapper drives it on a private loop for pre-loop callers.
from hyperion.infra.services import run_command

_GIT_TIMEOUT_SECONDS = 5.0
_MAX_ANCESTOR_LEVELS = 8

# The one cached snapshot per process, populated by the first collection.
_cached: Provenance | None = None


@dataclass(frozen=True)
class Provenance:
    """The loaded build's identity. Collected once per shell boot."""

    package_dir: str  # Path(hyperion.__file__).parent, resolved
    repo_root: str | None  # nearest ancestor containing .git, else None
    git_sha: str | None  # short SHA of HEAD in repo_root
    git_dirty: bool  # working tree has modifications
    install_mode: str  # "editable" | "site-packages" | "unknown"
    stale_pycache: list[str]  # .pyc older than its .py source


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
    """Find .pyc files older than their .py source under the package dir.

    The walk is capped at the package directory — never the whole
    filesystem. A stale .pyc means the interpreter may execute bytecode that
    predates the source on disk (RC-1's second mechanism).
    """
    stale: list[str] = []
    for py_file in package_dir.rglob("*.py"):
        cache_dir = py_file.parent / "__pycache__"
        if not cache_dir.is_dir():
            continue
        for pyc_file in cache_dir.glob(py_file.stem + ".cpython-*.pyc"):
            try:
                if pyc_file.stat().st_mtime < py_file.stat().st_mtime:
                    stale.append(str(pyc_file))
            except OSError:
                continue
    return stale


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
    """Render the one-line boot banner, e.g.

    HYPERION  build 87f0582  editable  /home/user/webapp/hyperion
    """
    sha = provenance.git_sha or "unknown"
    dirty = " +dirty" if provenance.git_dirty else ""
    return (
        f"HYPERION  build {sha}{dirty}  {provenance.install_mode}  "
        f"{provenance.package_dir}"
    )


def refusal_reason(provenance: Provenance) -> str | None:
    """Return the hard-refusal reason, or None when boot may proceed.

    The two refused configurations are exactly the two RC-1 mechanisms;
    the message states the fix for each.
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
            "Stale bytecode detected — .pyc files older than their source:\n"
            f"{listed}{more}\n"
            "The interpreter may execute code that predates the source on "
            "disk.\n"
            "Fix: find . -name __pycache__ -type d -prune -exec rm -rf {} +"
        )
    return None
