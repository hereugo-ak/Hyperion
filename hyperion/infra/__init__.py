"""HYPERION infrastructure layer — paths and external service lifecycle.

Two modules, each the single source of truth for its concern:

* :mod:`hyperion.infra.paths` — where the project and its resources live.
* :mod:`hyperion.infra.services` — the Docker engine and the containers
  HYPERION owns (SearxNG, FlareSolverr): start, readiness, teardown.

Both were previously open-coded in three or four places each (``tui/boot.py``,
``cli.py``, ``obs/health.py``, ``tui/screens/splash.py``, ``tools/obscura.py``)
and the copies had drifted apart. Anything needing a path or a container goes
through here.
"""

from __future__ import annotations

from hyperion.infra.paths import (
    docker_mount_path,
    obscura_bin_dir,
    obscura_binary_names,
    project_file,
    project_root,
    project_root_origin,
    reset_path_cache,
    resolve_path,
    searxng_limiter_file,
    searxng_settings_file,
)

__all__ = [
    "docker_mount_path",
    "obscura_bin_dir",
    "obscura_binary_names",
    "project_file",
    "project_root",
    "project_root_origin",
    "reset_path_cache",
    "resolve_path",
    "searxng_limiter_file",
    "searxng_settings_file",
]
