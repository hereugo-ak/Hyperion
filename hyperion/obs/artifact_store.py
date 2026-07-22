"""
HYPERION ArtifactStore — content-addressed store for durable execution.

Every step output (findings, section drafts, chart specs, FinalReport) is
serialized to JSON and written to ``artifacts/<run_id>/<step_id>.json``.
The journal records the artifact path as ``output_ref`` so replay can
load the cached output without re-executing the step.

This is the "blackboard" from IV.1.2 — a shared, versioned run-state
store that survives crashes and enables replay/resume.
"""

from __future__ import annotations

import json
import os
from typing import Any


class ArtifactStore:
    """Content-addressed artifact store for durable execution.

    Artifacts are stored as JSON files under ``artifacts/<run_id>/``.
    Each artifact is named by its step_id, making lookup trivial:

        store = ArtifactStore(run_id="eng_abc123")
        store.save("task_market_analyst", findings_dict)
        loaded = store.load("task_market_analyst")
    """

    def __init__(self, run_id: str, base_dir: str = "artifacts") -> None:
        self.run_id = run_id
        self._dir = os.path.join(base_dir, run_id)
        os.makedirs(self._dir, exist_ok=True)

    def _path(self, step_id: str) -> str:
        return os.path.join(self._dir, f"{step_id}.json")

    def save(self, step_id: str, data: Any) -> str:
        """Serialize and save an artifact. Returns the file path.

        The data must be JSON-serializable (dicts, lists, primitives,
        or Pydantic models via ``model_dump()``).
        """
        path = self._path(step_id)
        if hasattr(data, "model_dump"):
            payload = data.model_dump()
        elif isinstance(data, (dict, list, str, int, float, bool)):
            payload = data
        else:
            payload = str(data)

        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, default=str, indent=2)

        return path

    def load(self, step_id: str) -> Any | None:
        """Load an artifact by step_id. Returns None if not found."""
        path = self._path(step_id)
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    def exists(self, step_id: str) -> bool:
        """Check if an artifact exists for this step."""
        return os.path.exists(self._path(step_id))

    def list_artifacts(self) -> list[str]:
        """List all artifact step_ids in this run."""
        if not os.path.isdir(self._dir):
            return []
        return [
            f[:-5]  # strip .json
            for f in os.listdir(self._dir)
            if f.endswith(".json")
        ]
