"""Tests for P13: semantic cache, structured validator."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from hyperion.config import ModelTier
from hyperion.router.providers.base import ProviderType, RouterResponse
from hyperion.router.semantic_cache import ResponseCache, _exact_key, _semantic_key
from hyperion.router.structured_validator import (
    StructuredValidator,
    ValidationResult,
    extract_json,
    validate_json,
    validate_pydantic,
)


# ─────────────────────────────────────────────────────────────────────────────
# ResponseCache Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestResponseCache:
    def _make_response(self, content: str = "test response") -> RouterResponse:
        return RouterResponse(
            content=content,
            model="test-model",
            provider=ProviderType.GOOGLE,
            tier=ModelTier.STANDARD,
        )

    def test_exact_cache_hit(self):
        cache = ResponseCache(ttl_seconds=60)
        messages = [{"role": "user", "content": "What is the market size?"}]
        response = self._make_response()

        cache.set(ModelTier.STANDARD, messages, response, temperature=0.7)
        cached = cache.get(ModelTier.STANDARD, messages, temperature=0.7)

        assert cached is not None
        assert cached.content == "test response"
        assert cache.hit_rate > 0

    def test_exact_cache_miss_different_messages(self):
        cache = ResponseCache(ttl_seconds=60)
        messages1 = [{"role": "user", "content": "What is the market size?"}]
        messages2 = [{"role": "user", "content": "What is the competitive landscape?"}]
        response = self._make_response()

        cache.set(ModelTier.STANDARD, messages1, response, temperature=0.7)
        cached = cache.get(ModelTier.STANDARD, messages2, temperature=0.7)

        assert cached is None

    def test_exact_cache_miss_different_temperature(self):
        cache = ResponseCache(ttl_seconds=60)
        messages = [{"role": "user", "content": "What is the market size?"}]
        response = self._make_response()

        cache.set(ModelTier.STANDARD, messages, response, temperature=0.7)
        cached = cache.get(ModelTier.STANDARD, messages, temperature=0.5)

        assert cached is None

    def test_semantic_cache_hit(self):
        cache = ResponseCache(ttl_seconds=60, semantic_enabled=True)
        messages1 = [{"role": "user", "content": "What is the  market size?"}]
        messages2 = [{"role": "user", "content": "what is the market size?"}]
        response = self._make_response()

        cache.set(ModelTier.STANDARD, messages1, response, temperature=0.7, semantic=True)
        cached = cache.get(ModelTier.STANDARD, messages2, temperature=0.7, use_semantic=True)

        assert cached is not None
        assert cached.content == "test response"

    def test_cache_expiry(self):
        cache = ResponseCache(ttl_seconds=1)  # 1 second TTL
        messages = [{"role": "user", "content": "test"}]
        response = self._make_response()

        cache.set(ModelTier.STANDARD, messages, response, temperature=0.7)
        time.sleep(1.1)  # Wait for expiry
        cached = cache.get(ModelTier.STANDARD, messages, temperature=0.7)

        assert cached is None

    def test_cache_stats(self):
        cache = ResponseCache(ttl_seconds=60)
        messages = [{"role": "user", "content": "test"}]
        response = self._make_response()

        cache.set(ModelTier.STANDARD, messages, response, temperature=0.7)
        cache.get(ModelTier.STANDARD, messages, temperature=0.7)  # hit
        cache.get(ModelTier.STANDARD, [{"role": "user", "content": "miss"}], temperature=0.7)  # miss

        stats = cache.stats
        assert stats["hits"] == 1
        assert stats["misses"] == 1
        assert 0 < stats["hit_rate"] < 1

    def test_cache_clear(self):
        cache = ResponseCache(ttl_seconds=60)
        messages = [{"role": "user", "content": "test"}]
        cache.set(ModelTier.STANDARD, messages, self._make_response(), temperature=0.7)

        cache.clear()
        assert cache.stats["exact_entries"] == 0
        assert cache.stats["hits"] == 0

    def test_semantic_key_normalization(self):
        """Semantic keys should be insensitive to whitespace and case."""
        key1 = _semantic_key([{"role": "user", "content": "What is the market size?"}])
        key2 = _semantic_key([{"role": "user", "content": "  what IS the MARKET  size?  "}])
        assert key1 == key2


# ─────────────────────────────────────────────────────────────────────────────
# Structured Validator Tests
# ─────────────────────────────────────────────────────────────────────────────


class TestExtractJson:
    def test_raw_json(self):
        result = extract_json('{"key": "value"}')
        assert result == '{"key": "value"}'

    def test_code_block_json(self):
        result = extract_json('```json\n{"key": "value"}\n```')
        assert result == '{"key": "value"}'

    def test_code_block_no_language(self):
        result = extract_json('```\n{"key": "value"}\n```')
        assert result == '{"key": "value"}'

    def test_embedded_json(self):
        result = extract_json('Here is the result: {"key": "value"} as shown.')
        assert result == '{"key": "value"}'

    def test_no_json(self):
        result = extract_json("This is just text, no JSON here.")
        assert result is None


class TestValidateJson:
    def test_valid_json(self):
        result = validate_json('{"key": "value"}')
        assert result == {"key": "value"}

    def test_invalid_json(self):
        result = validate_json("not json at all")
        assert result is None

    def test_code_block_json(self):
        result = validate_json('```json\n{"key": "value"}\n```')
        assert result == {"key": "value"}


class TestValidatePydantic:
    def test_valid_pydantic(self):
        class MyModel(BaseModel):
            name: str
            age: int

        instance, error = validate_pydantic('{"name": "Alice", "age": 30}', MyModel)
        assert instance is not None
        assert instance.name == "Alice"
        assert error == ""

    def test_invalid_pydantic(self):
        class MyModel(BaseModel):
            name: str
            age: int

        instance, error = validate_pydantic('{"name": "Alice"}', MyModel)
        assert instance is None
        assert "age" in error.lower() or "missing" in error.lower()

    def test_not_json(self):
        class MyModel(BaseModel):
            name: str

        instance, error = validate_pydantic("not json", MyModel)
        assert instance is None
        assert "json" in error.lower()


class TestStructuredValidator:
    @pytest.mark.asyncio
    async def test_valid_json_no_repair_needed(self):
        validator = StructuredValidator(router=None)
        result = await validator.validate_and_repair(
            content='{"key": "value"}',
            model_cls=None,
        )
        assert result.success
        assert result.data == {"key": "value"}
        assert result.repair_attempts == 0

    @pytest.mark.asyncio
    async def test_invalid_json_no_router(self):
        validator = StructuredValidator(router=None)
        result = await validator.validate_and_repair(
            content="not json",
            model_cls=None,
            messages=[{"role": "user", "content": "test"}],
        )
        assert not result.success
        assert "no router" in result.error.lower() or "invalid" in result.error.lower()

    @pytest.mark.asyncio
    async def test_pydantic_validation_success(self):
        class MyModel(BaseModel):
            name: str
            age: int

        validator = StructuredValidator(router=None)
        result = await validator.validate_and_repair(
            content='{"name": "Alice", "age": 30}',
            model_cls=MyModel,
        )
        assert result.success
        assert result.data["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_pydantic_validation_failure_no_router(self):
        class MyModel(BaseModel):
            name: str
            age: int

        validator = StructuredValidator(router=None)
        result = await validator.validate_and_repair(
            content='{"name": "Alice"}',
            model_cls=MyModel,
            messages=[{"role": "user", "content": "test"}],
        )
        assert not result.success
