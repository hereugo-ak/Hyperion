"""
HYPERION Mistral Provider.

Mistral AI provides the largest free-tier token budget (~1B tokens/month)
via their Experiment plan. OpenAI-compatible API at api.mistral.ai/v1.
This is our volume provider — 7 models across all 5 tiers, with
model-specific Experiment-plan limits and no daily request cap. (§2.5)

Models on this provider:
- mistral-large-2512: STRONG — planning, writing, synthesis, quality gate
- mistral-medium-2605: STRONG — reasoning (DCF, risk, game theory)
- mistral-medium-2508: STANDARD — research, analysis, structured output
- ministral-14b-2512: STANDARD — reasoning (fact-check, quality scoring)
- mistral-small-2603: FAST — fast extraction, sub-agent research
- devstral-2512: DEEP — 256K context, tool orchestration
- ministral-3b-2512: MICRO — quick lookups, simple classification

This is NOT a generic OpenAI client wrapper. It is the Mistral-specific
implementation that leverages Mistral's unique model diversity — the
reasoning models (Magistral) provide chain-of-thought capabilities that
no other free-tier provider offers, and Devstral's 256K context window
is the longest available on any free tier. The wait gate routes
reasoning-heavy tasks to Magistral and long-context tasks to Devstral.

Note: The free Experiment tier requires opting into data training. This
is acceptable for research and prototyping but should not be used with
sensitive client data. (§2.5)
"""

from __future__ import annotations

from hyperion.config import ProviderType
from hyperion.router.providers.base import BaseProvider


class MistralProvider(BaseProvider):
    """Mistral provider — Mistral, Magistral, Devstral, Ministral (all tiers).

    The volume provider. 7 models give the wait gate maximum flexibility:

    - STRONG reasoning: mistral-medium-2605 (chain-of-thought for DCF, risk)
    - STRONG general: mistral-large-2512 (flagship for synthesis, quality gate)
    - STANDARD reasoning: ministral-14b-2512 (fact-check logic, scoring)
    - STANDARD general: mistral-medium-2508 / mistral-medium-2605 (research, structured output)
    - FAST: mistral-small-2603 (fast extraction, sub-agent tasks)
    - DEEP: devstral-2512 (256K context — longest free-tier context window)
    - MICRO: ministral-3b-2512 (quick lookups, simple classification)

    Mistral's ~1B tokens/month free quota is the largest of any provider.
    With model-specific TPM/RPS limits and no RPD cap, this provider can absorb high-volume
    workloads that would exhaust Groq or Cerebras daily limits.
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.MISTRAL
