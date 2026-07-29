"""
HYPERION Token Estimator — pre-request token estimation for TPM planning.

This is NOT a generic token counter. This is the system that estimates token
consumption BEFORE a request is dispatched, so the wait gate can predict
whether a provider has capacity. Underestimating tokens leads to 429s;
overestimating leads to underutilization. (§3.4)

The estimator uses conservative ratios (1 token ≈ 3 chars for English,
1 token ≈ 2 chars for code/structured data) and adds an output budget
based on the model tier. After each response, the router records the
ACTUAL token usage and the estimator calibrates future estimates.

Over time, the estimator learns the real token consumption patterns of
each agent on each model — it tracks per-agent, per-model calibration
factors that adjust the base estimate up or down.
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass
from typing import Any

from hyperion.config import TIER_OUTPUT_BUDGET, ModelTier

# Conservative char-to-token ratios (§3.4)
_CHARS_PER_TOKEN_ENGLISH = 3
_CHARS_PER_TOKEN_CODE = 2


@dataclass
class CalibrationSample:
    """A single calibration sample — estimated vs actual tokens."""

    agent_name: str
    model_name: str
    tier: ModelTier
    estimated_tokens: int
    actual_tokens: int
    timestamp: float

    @property
    def ratio(self) -> float:
        """actual / estimated — if > 1.0, we underestimated."""
        if self.estimated_tokens == 0:
            return 1.0
        return self.actual_tokens / self.estimated_tokens


class TokenEstimator:
    """Pre-request token estimation with per-agent, per-model calibration.

    The base estimate follows §3.4:
    - input_tokens = (len(system_prompt) + len(user_prompt)) // 3
    - output_tokens = TIER_OUTPUT_BUDGET[tier]
    - total = input_tokens + output_tokens

    After each response, the router calls record_actual() with the real
    token count. The estimator maintains a rolling window of calibration
    samples per (agent, model) pair and computes a calibration factor
    that adjusts future estimates.

    If an agent consistently uses 1.3x the estimated tokens on a model,
    the estimator multiplies future estimates by 1.3x for that pair.
    This is how the system learns and avoids 429s over time.
    """

    def __init__(self, max_samples_per_pair: int = 20) -> None:
        self._max_samples = max_samples_per_pair
        # (agent_name, model_name) -> deque of CalibrationSample
        self._samples: dict[tuple[str, str], deque[CalibrationSample]] = {}

    def estimate_tokens(
        self,
        system_prompt: str,
        user_prompt: str,
        tier: ModelTier,
        agent_name: str = "",
        model_name: str = "",
    ) -> int:
        """Estimate total token consumption for a request.

        Uses the conservative formula from §3.4, then applies a calibration
        factor if we have historical data for this (agent, model) pair.
        """
        combined = system_prompt + user_prompt
        input_chars = len(combined)

        # Detect if the prompt is code-heavy (structured data, JSON, code blocks)
        # Simple heuristic: if >30% of chars are non-alpha, treat as code
        alpha_count = sum(1 for c in combined if c.isalpha())
        alpha_ratio = alpha_count / max(1, input_chars)
        chars_per_token = _CHARS_PER_TOKEN_CODE if alpha_ratio < 0.7 else _CHARS_PER_TOKEN_ENGLISH

        input_tokens = input_chars // chars_per_token
        output_tokens = TIER_OUTPUT_BUDGET.get(tier, 4000)

        base_estimate = input_tokens + output_tokens

        # Apply calibration factor if we have history
        calibration = self.get_calibration_factor(agent_name, model_name)
        return int(base_estimate * calibration)

    def estimate_input_tokens(self, system_prompt: str, user_prompt: str) -> int:
        """Estimate input tokens only (no output budget)."""
        combined = system_prompt + user_prompt
        input_chars = len(combined)
        alpha_count = sum(1 for c in combined if c.isalpha())
        alpha_ratio = alpha_count / max(1, input_chars)
        chars_per_token = _CHARS_PER_TOKEN_CODE if alpha_ratio < 0.7 else _CHARS_PER_TOKEN_ENGLISH
        return input_chars // chars_per_token

    def record_actual(
        self,
        agent_name: str,
        model_name: str,
        tier: ModelTier,
        estimated_tokens: int,
        actual_tokens: int,
    ) -> None:
        """Record actual token usage for calibration.

        Called by the router after each response. The estimator maintains
        a rolling window of samples and computes a calibration factor
        that adjusts future estimates for this (agent, model) pair.
        """
        key = (agent_name, model_name)
        if key not in self._samples:
            self._samples[key] = deque(maxlen=self._max_samples)

        self._samples[key].append(
            CalibrationSample(
                agent_name=agent_name,
                model_name=model_name,
                tier=tier,
                estimated_tokens=estimated_tokens,
                actual_tokens=actual_tokens,
                timestamp=time.time(),
            )
        )

    def get_calibration_factor(self, agent_name: str, model_name: str) -> float:
        """Get the calibration factor for an (agent, model) pair.

        Returns the average ratio of actual/estimated tokens, clamped to
        [0.5, 3.0] to prevent extreme corrections. If no history, returns 1.0.

        A factor > 1.0 means we've been underestimating — multiply up.
        A factor < 1.0 means we've been overestimating — multiply down.
        """
        key = (agent_name, model_name)
        samples = self._samples.get(key)
        if not samples:
            return 1.0

        avg_ratio = sum(s.ratio for s in samples) / len(samples)

        # Clamp to prevent extreme corrections
        return max(0.5, min(3.0, avg_ratio))

    def get_stats(self, agent_name: str = "", model_name: str = "") -> dict[str, Any]:
        """Get calibration statistics for debugging/TUI display."""
        stats: dict[str, Any] = {}
        for key, samples in self._samples.items():
            if agent_name and key[0] != agent_name:
                continue
            if model_name and key[1] != model_name:
                continue
            avg_ratio = sum(s.ratio for s in samples) / len(samples)
            stats[f"{key[0]}:{key[1]}"] = {
                "samples": len(samples),
                "avg_ratio": round(avg_ratio, 3),
                "calibration_factor": round(self.get_calibration_factor(key[0], key[1]), 3),
            }
        return stats
