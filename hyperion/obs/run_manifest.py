"""
HYPERION RunManifest — reproducibility & replay metadata for each engagement.

The manifest pins everything needed to reproduce or replay a run:
- The original question and conversation context
- A deterministic seed for stochastic choices (provider ordering, sampling)
- Pinned prompt template versions, model IDs, and tool versions
- A config snapshot (provider matrix, tier assignments)
- Per-run cost/latency ledger (filled in as the run progresses)

Written to ``artifacts/<run_id>/run_manifest.json`` at engagement start
and updated at engagement end with final metrics.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass, field
from typing import Any


@dataclass
class RunManifest:
    """Reproducibility manifest for a single HYPERION engagement."""

    run_id: str
    question: str
    conversation_context: str = ""
    seed: int = 0
    created_at: float = field(default_factory=time.time)

    # Pinned versions
    prompt_version: str = "1.0"
    model_matrix_hash: str = ""

    # Config snapshot (provider matrix, tier assignments)
    config_snapshot: dict[str, Any] = field(default_factory=dict)

    # Per-run ledger (filled during execution)
    ledger: dict[str, Any] = field(default_factory=dict)

    # Final metrics (filled at engagement end)
    final_metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.seed == 0:
            # Deterministic seed from question hash
            self.seed = int(
                hashlib.sha256(self.question.encode()).hexdigest()[:8], 16
            )

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "question": self.question,
            "conversation_context": self.conversation_context,
            "seed": self.seed,
            "created_at": self.created_at,
            "prompt_version": self.prompt_version,
            "model_matrix_hash": self.model_matrix_hash,
            "config_snapshot": self.config_snapshot,
            "ledger": self.ledger,
            "final_metrics": self.final_metrics,
        }

    def save(self, base_dir: str = "artifacts") -> str:
        """Write the manifest to ``artifacts/<run_id>/run_manifest.json``."""
        dir_path = os.path.join(base_dir, self.run_id)
        os.makedirs(dir_path, exist_ok=True)
        path = os.path.join(dir_path, "run_manifest.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2, default=str)
        return path

    @classmethod
    def load(cls, run_id: str, base_dir: str = "artifacts") -> RunManifest | None:
        """Load a manifest for an existing run. Returns None if not found."""
        path = os.path.join(base_dir, run_id, "run_manifest.json")
        if not os.path.exists(path):
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            run_id=data["run_id"],
            question=data["question"],
            conversation_context=data.get("conversation_context", ""),
            seed=data.get("seed", 0),
            created_at=data.get("created_at", time.time()),
            prompt_version=data.get("prompt_version", "1.0"),
            model_matrix_hash=data.get("model_matrix_hash", ""),
            config_snapshot=data.get("config_snapshot", {}),
            ledger=data.get("ledger", {}),
            final_metrics=data.get("final_metrics", {}),
        )

    def record_ledger_entry(self, key: str, value: Any) -> None:
        """Record a ledger entry (tokens, cost, latency per stage)."""
        self.ledger[key] = value

    def record_final_metrics(
        self,
        duration_seconds: float,
        quality_score: float | None,
        pdf_path: str,
        success: bool,
        llm_calls: int = 0,
        tokens_consumed: int = 0,
    ) -> None:
        """Record final engagement metrics."""
        self.final_metrics = {
            "duration_seconds": duration_seconds,
            "quality_score": quality_score,
            "pdf_path": pdf_path,
            "success": success,
            "llm_calls": llm_calls,
            "tokens_consumed": tokens_consumed,
        }
