"""
HYPERION Provider implementations.

Each provider exposes an OpenAI-compatible API. We use one client (openai)
with different base URLs. The BaseProvider defines the common interface;
each provider implements it with provider-specific details.

All 5 providers:
- Google AI Studio: Gemma 4 31B (MICRO), Gemini 3.1 Flash Lite (DEEP)
- NVIDIA NIM: Nemotron 3 Super 120B (STRONG), Nemotron 3 Nano 30B (STANDARD)
- Cerebras: GPT OSS 120B (FAST) — ~3000 tok/s
- Groq: GPT OSS 120B (STANDARD), Llama 3.1 8B (MICRO)
- Mistral AI: Mistral Large 3 (STRONG), Magistral (reasoning), Devstral (DEEP 256K)
"""

from hyperion.router.providers.base import (
    BaseProvider,
    ProviderHealth,
    ProviderStatus,
    RouterResponse,
)
from hyperion.router.providers.cerebras import CerebrasProvider
from hyperion.router.providers.google import GoogleProvider
from hyperion.router.providers.groq import GroqProvider
from hyperion.router.providers.mistral import MistralProvider
from hyperion.router.providers.nvidia import NvidiaProvider

__all__ = [
    "BaseProvider",
    "ProviderHealth",
    "ProviderStatus",
    "RouterResponse",
    "GoogleProvider",
    "NvidiaProvider",
    "CerebrasProvider",
    "GroqProvider",
    "MistralProvider",
]
