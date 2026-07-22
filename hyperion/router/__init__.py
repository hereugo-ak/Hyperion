"""
HYPERION Router — the LLM routing layer.

This is NOT a generic LLM client wrapper. This is the predictive wait gate
system that tracks RPM/TPM/RPD across 4 providers in real-time sliding
windows and routes requests to avoid 429s before they happen. (§3)

The router is infrastructure, not intelligence. Agents don't know which
provider they're using — they request a tier and the router decides.
(§9, key design decisions)

Components:
- providers/base.py: BaseProvider — common interface for all 4 providers
- providers/google.py: Google AI Studio (Gemma, Gemini)
- providers/nvidia.py: NVIDIA NIM (Nemotron)
- providers/cerebras.py: Cerebras (GPT OSS 120B)
- providers/groq.py: Groq (Llama, GPT OSS)
- wait_gate.py: SlidingWindowTracker, WaitGate coordinator
- budget.py: DailyBudgetPlanner with 20% reserve
- estimator.py: Token estimation + calibration
- router.py: LLMRouter — async, TPM-aware, singleton
"""

from hyperion.router.budget import DailyBudgetPlanner, TaskUrgency
from hyperion.router.estimator import TokenEstimator
from hyperion.router.providers.base import BaseProvider, ProviderHealth, ProviderStatus, RouterResponse
from hyperion.router.router import LLMRouter, get_router, reset_router
from hyperion.router.semantic_cache import ResponseCache
from hyperion.router.speculative_racer import SpeculativeRacer
from hyperion.router.structured_validator import StructuredValidator, ValidationResult
from hyperion.router.wait_gate import SlidingWindowTracker, WaitGate

__all__ = [
    "LLMRouter",
    "RouterResponse",
    "BaseProvider",
    "ProviderHealth",
    "ProviderStatus",
    "SlidingWindowTracker",
    "WaitGate",
    "DailyBudgetPlanner",
    "TaskUrgency",
    "TokenEstimator",
    "get_router",
    "reset_router",
    "ResponseCache",
    "SpeculativeRacer",
    "StructuredValidator",
    "ValidationResult",
]
