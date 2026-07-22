"""
HYPERION ResponseCache — semantic + exact LLM response cache (P13).

Caches LLM responses to avoid redundant API calls. Two cache modes:

1. **Exact cache:** SHA-256 hash of (tier, messages, temperature, max_tokens).
   If the exact same request is made again, return the cached response
   without hitting any provider. This is the common case — agents often
   re-request the same analysis with identical prompts.

2. **Semantic cache:** Normalized content hash (strip whitespace, lowercase,
   remove punctuation). Catches near-duplicate requests that differ only
   in formatting. Opt-in per agent — not all agents benefit from semantic
   caching (synthesis should always be fresh).

The cache is in-memory with a configurable TTL (default 1 hour). It is
NOT shared across processes — each WorkflowEngine instance has its own.
This is the proportionate adoption: no Redis, no external infra.

Usage (inside the router)::

    from hyperion.router.semantic_cache import ResponseCache

    cache = ResponseCache(ttl_seconds=3600)
    cached = cache.get(tier, messages, temperature)
    if cached:
        return cached  # cache hit — skip provider call
    # ... execute request ...
    cache.set(tier, messages, temperature, response)
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any

from hyperion.config import ModelTier
from hyperion.router.providers.base import RouterResponse


def _exact_key(
    tier: ModelTier,
    messages: list[dict[str, str]],
    temperature: float,
    max_tokens: int | None,
) -> str:
    """Compute exact cache key from request parameters."""
    payload = f"{tier.value}|{temperature}|{max_tokens}"
    for msg in messages:
        payload += f"|{msg.get('role', '')}:{msg.get('content', '')}"
    return hashlib.sha256(payload.encode()).hexdigest()


def _semantic_key(messages: list[dict[str, str]]) -> str:
    """Compute semantic cache key — normalized content hash.

    Strips whitespace, lowercases, removes punctuation. This catches
    near-duplicate requests that differ only in formatting.
    """
    normalized_parts: list[str] = []
    for msg in messages:
        content = msg.get("content", "")
        # Normalize: lowercase, strip whitespace, remove punctuation
        content = re.sub(r"\s+", " ", content.lower().strip())
        content = re.sub(r"[^\w\s]", "", content)
        normalized_parts.append(f"{msg.get('role', '')}:{content}")
    return hashlib.sha256("|".join(normalized_parts).encode()).hexdigest()


@dataclass
class CacheEntry:
    """A single cache entry with expiry."""

    response: RouterResponse
    expires_at: float
    cached_at: float = field(default_factory=time.time)
    hit_count: int = 0


class ResponseCache:
    """In-memory LLM response cache with exact + semantic matching.

    The cache is tiered:
    - Exact match: same tier + messages + temperature → instant hit
    - Semantic match: normalized content match → hit (if enabled)

    Semantic caching is opt-in per agent because some agents (synthesis)
    should always produce fresh output.
    """

    def __init__(
        self,
        ttl_seconds: int = 3600,
        semantic_enabled: bool = True,
        max_entries: int = 500,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.semantic_enabled = semantic_enabled
        self.max_entries = max_entries
        self._exact_cache: dict[str, CacheEntry] = {}
        self._semantic_cache: dict[str, CacheEntry] = {}
        self._hits = 0
        self._misses = 0

    def get(
        self,
        tier: ModelTier,
        messages: list[dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int | None = None,
        use_semantic: bool = False,
    ) -> RouterResponse | None:
        """Check the cache for a matching response.

        Args:
            tier: Model tier of the request
            messages: Conversation messages
            temperature: Sampling temperature
            max_tokens: Max output tokens
            use_semantic: Whether to also check semantic cache

        Returns:
            Cached RouterResponse if found, None on miss.
        """
        now = time.time()

        # Check exact cache first
        exact_key = _exact_key(tier, messages, temperature, max_tokens)
        entry = self._exact_cache.get(exact_key)
        if entry and entry.expires_at > now:
            entry.hit_count += 1
            self._hits += 1
            return entry.response

        # Check semantic cache if enabled
        if use_semantic and self.semantic_enabled:
            sem_key = _semantic_key(messages)
            entry = self._semantic_cache.get(sem_key)
            if entry and entry.expires_at > now:
                entry.hit_count += 1
                self._hits += 1
                return entry.response

        self._misses += 1
        return None

    def set(
        self,
        tier: ModelTier,
        messages: list[dict[str, str]],
        response: RouterResponse,
        temperature: float = 0.7,
        max_tokens: int | None = None,
        semantic: bool = False,
    ) -> None:
        """Store a response in the cache.

        Args:
            tier: Model tier of the request
            messages: Conversation messages
            response: The RouterResponse to cache
            temperature: Sampling temperature
            max_tokens: Max output tokens
            semantic: Also store in semantic cache
        """
        self._evict_if_needed()

        expires_at = time.time() + self.ttl_seconds
        exact_key = _exact_key(tier, messages, temperature, max_tokens)
        self._exact_cache[exact_key] = CacheEntry(
            response=response,
            expires_at=expires_at,
        )

        if semantic and self.semantic_enabled:
            sem_key = _semantic_key(messages)
            self._semantic_cache[sem_key] = CacheEntry(
                response=response,
                expires_at=expires_at,
            )

    def _evict_if_needed(self) -> None:
        """Evict expired entries and enforce max size."""
        now = time.time()

        # Remove expired entries
        expired_exact = [
            k for k, v in self._exact_cache.items() if v.expires_at <= now
        ]
        for k in expired_exact:
            del self._exact_cache[k]

        expired_sem = [
            k for k, v in self._semantic_cache.items() if v.expires_at <= now
        ]
        for k in expired_sem:
            del self._semantic_cache[k]

        # Enforce max size (LRU-ish: remove oldest entries)
        if len(self._exact_cache) > self.max_entries:
            sorted_keys = sorted(
                self._exact_cache.keys(),
                key=lambda k: self._exact_cache[k].cached_at,
            )
            for k in sorted_keys[: len(sorted_keys) - self.max_entries]:
                del self._exact_cache[k]

    @property
    def hit_rate(self) -> float:
        """Cache hit rate (0.0 to 1.0)."""
        total = self._hits + self._misses
        return self._hits / total if total > 0 else 0.0

    @property
    def stats(self) -> dict[str, Any]:
        """Cache statistics for observability."""
        return {
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
            "exact_entries": len(self._exact_cache),
            "semantic_entries": len(self._semantic_cache),
        }

    def clear(self) -> None:
        """Clear all cache entries."""
        self._exact_cache.clear()
        self._semantic_cache.clear()
        self._hits = 0
        self._misses = 0
