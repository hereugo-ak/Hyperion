"""HYPERION path resolution — one answer to "where is the project root?".

WHY THIS MODULE EXISTS
----------------------
Path resolution was previously open-coded at every site that needed it, and no
two sites agreed:

  * ``tools/obscura.py``  → ``Path(__file__).resolve().parents[2]``
  * ``tui/boot.py``       → walk parents looking for ``searxng_settings.yml``,
                            else fall back to ``parents[2]``
  * ``tui/screens/splash.py`` → walk parents looking for ``obscura-bin``
  * ``obs/health.py``     → ``Path("obscura-bin/obscura.exe")``, i.e. relative
                            to the **current working directory**

``parents[2]`` is a positional bet on the package never moving. It is correct
only while the file sits exactly three levels below the root, so moving
``obscura.py`` one directory deeper silently resolves the "project root" to
``hyperion/`` and every lookup under it fails. The ``health.py`` variant is
worse: it depends on the user's shell CWD, so ``hyperion shell`` reported
Obscura OFFLINE from any directory other than the repo root even though the
binary was present — a *false* health reading, which is how the user ends up
being told to launch things manually.

The rules here:

1. **Anchor on a marker, not on a count.** The root is the nearest ancestor
   containing a real project marker (``pyproject.toml``). A marker cannot drift
   when files move.
2. **The environment wins.** ``HYPERION_PROJECT_ROOT`` overrides discovery, so
   an installed/frozen deployment can point at its own data directory.
3. **Never guess silently.** If no marker is found, fall back to the package
   parent *and say so* via :func:`project_root_origin`, so a health panel can
   report the assumption rather than pretending to know.
4. **Everything downstream is absolute.** Callers get absolute paths, so a
   CWD change cannot alter behaviour.
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

# Files that mark the HYPERION project root. `pyproject.toml` is the canonical
# marker; the others are checked so a partial checkout (or a packaged layout
# that ships data but not build metadata) still resolves.
_ROOT_MARKERS: tuple[str, ...] = (
    "pyproject.toml",
    "searxng_settings.yml",
    "docker-compose.yml",
)

# Environment override. Documented in .env.example.
_ROOT_ENV_VAR = "HYPERION_PROJECT_ROOT"


def _package_dir() -> Path:
    """Absolute path of the ``hyperion`` package directory."""
    # parents[0] is `hyperion/infra`, parents[1] is `hyperion`. This is a
    # *package-internal* relationship (this file's own package), not a
    # project-layout assumption, so it cannot drift the way `parents[2]` did.
    return Path(__file__).resolve().parents[1]


@lru_cache(maxsize=1)
def _resolve_root() -> tuple[Path, str]:
    """Return ``(root, origin)``. Cached: the answer cannot change mid-process.

    ``origin`` is one of ``"env"``, ``"marker"`` or ``"fallback"`` and exists so
    callers can *report* how the root was determined instead of assuming it was
    known confidently.
    """
    # 1. Explicit override always wins.
    env_value = os.environ.get(_ROOT_ENV_VAR, "").strip()
    if env_value:
        candidate = Path(env_value).expanduser()
        try:
            return candidate.resolve(), "env"
        except OSError:
            return candidate, "env"

    # 2. Walk up from this file looking for a project marker.
    package_dir = _package_dir()
    for parent in (package_dir, *package_dir.parents):
        for marker in _ROOT_MARKERS:
            if (parent / marker).exists():
                return parent, "marker"

    # 3. Nothing found. Assume the directory containing the package and let the
    #    caller surface that this is an assumption.
    return package_dir.parent, "fallback"


def project_root() -> Path:
    """Absolute path to the HYPERION project root."""
    return _resolve_root()[0]


def project_root_origin() -> str:
    """How the root was determined: ``env`` | ``marker`` | ``fallback``."""
    return _resolve_root()[1]


def reset_path_cache() -> None:
    """Forget the cached root.

    Needed by tests that set ``HYPERION_PROJECT_ROOT`` and by any embedder that
    relocates the project at runtime.
    """
    _resolve_root.cache_clear()


def resolve_path(value: str | os.PathLike[str] | None, *, default: str = "") -> Path:
    """Resolve ``value`` to an absolute path, relative paths being project-relative.

    This is the fix for settings like ``vault_path = Path("./vault")``. Pydantic
    stores them verbatim, so every consumer resolved them against the *process*
    CWD — meaning ``hyperion consult`` launched from a user's home directory
    wrote its vault and reports into that home directory instead of the project.
    Anchoring relative values to the project root makes the behaviour identical
    from any working directory, while still honouring absolute paths supplied by
    the user.
    """
    raw = value if value not in (None, "") else default
    if raw in (None, ""):
        return project_root()
    path = Path(raw).expanduser()
    if path.is_absolute():
        return path
    return (project_root() / path).resolve()


def project_file(*parts: str) -> Path:
    """Absolute path to a file inside the project root."""
    return project_root().joinpath(*parts)


# ─────────────────────────────────────────────────────────────────────────────
# Named project resources
# ─────────────────────────────────────────────────────────────────────────────


def searxng_settings_file() -> Path:
    """Absolute path to ``searxng_settings.yml``."""
    return project_file("searxng_settings.yml")


def searxng_limiter_file() -> Path:
    """Absolute path to ``searxng-limiter.toml``."""
    return project_file("searxng-limiter.toml")


def obscura_bin_dir() -> Path:
    """Absolute path to the bundled Obscura binary directory."""
    return project_file("obscura-bin")


def obscura_binary_names() -> tuple[str, ...]:
    """Candidate Obscura executable names for the current platform.

    Windows first on Windows, bare name first elsewhere, so the platform's
    native artefact is preferred rather than probing an incompatible one.
    """
    if sys.platform == "win32":
        return ("obscura.exe", "obscura")
    return ("obscura", "obscura.exe")


def docker_mount_path(path: Path) -> str:
    """Format an absolute host path for a ``docker run -v`` argument.

    Docker on Windows accepts forward slashes; backslashes inside a ``-v``
    value are parsed as separators and corrupt the mount specification.
    """
    return str(path).replace("\\", "/")
