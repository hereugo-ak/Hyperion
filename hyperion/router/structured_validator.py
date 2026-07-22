"""
HYPERION StructuredValidator — validate-and-repair loop for JSON outputs.

When agents request structured JSON output (response_format), the LLM
sometimes returns malformed JSON — missing fields, wrong types, or
truncated responses. This module implements a validate-and-repair loop:

1. Parse the LLM response as JSON
2. Validate against the expected schema (Pydantic model or dict spec)
3. If invalid, send a repair prompt asking the LLM to fix the JSON
4. Retry up to ``max_repair_attempts`` times
5. If all repairs fail, return the best partial result

This is the proportionate adoption of structured-output validation
(IV.1.5): no external schema validator, just Pydantic + a repair prompt.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from hyperion.obs import trace

logger = logging.getLogger(__name__)


@dataclass
class ValidationResult:
    """Result of a structured-output validation attempt."""

    success: bool
    data: dict[str, Any] | None
    error: str = ""
    repair_attempts: int = 0
    original_content: str = ""


# Common JSON extraction patterns
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n?(.*?)\n?```", re.DOTALL)
_JSON_OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)


def extract_json(content: str) -> str | None:
    """Extract JSON from LLM response content.

    Handles:
    - Raw JSON: ``{"key": "value"}``
    - Code blocks: ````json\n{...}\n````
    - JSON embedded in prose: "Here is the result: {...}"
    """
    # Try code block extraction first
    match = _JSON_BLOCK_RE.search(content)
    if match:
        return match.group(1).strip()

    # Try raw JSON (content is just JSON)
    stripped = content.strip()
    if stripped.startswith("{") and stripped.endswith("}"):
        return stripped

    # Try to find a JSON object in the content
    match = _JSON_OBJECT_RE.search(content)
    if match:
        return match.group(0)

    return None


def validate_json(content: str) -> dict[str, Any] | None:
    """Parse and validate JSON from LLM content. Returns None on failure."""
    json_str = extract_json(content)
    if json_str is None:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        return None


def validate_pydantic(content: str, model_cls: type) -> tuple[Any | None, str]:
    """Validate JSON content against a Pydantic model.

    Returns (model_instance, error_message). On success, error is "".
    """
    data = validate_json(content)
    if data is None:
        return None, "Failed to extract valid JSON from response"

    try:
        instance = model_cls.model_validate(data)
        return instance, ""
    except Exception as e:
        return None, str(e)[:500]


REPAIR_PROMPT = """The previous response contained invalid JSON. Please fix it.

Error: {error}

Your previous response:
{previous_response}

Return ONLY valid JSON, no explanation, no code blocks. The JSON must match this schema:
{schema_hint}

Return the corrected JSON now:"""


class StructuredValidator:
    """Validate-and-repair loop for structured LLM outputs.

    Usage (inside agents that request JSON output)::

        validator = StructuredValidator(router=router)
        result = await validator.validate_and_repair(
            content=response.content,
            model_cls=MyOutputModel,
            messages=messages,
            tier=ModelTier.STANDARD,
            agent_name="my_agent",
        )
        if result.success:
            my_obj = MyOutputModel.model_validate(result.data)
    """

    MAX_REPAIR_ATTEMPTS = 2

    def __init__(self, router: Any = None) -> None:
        self.router = router

    async def validate_and_repair(
        self,
        content: str,
        model_cls: type | None = None,
        messages: list[dict[str, str]] | None = None,
        tier: Any = None,
        agent_name: str = "",
        schema_hint: str = "",
    ) -> ValidationResult:
        """Validate structured output and repair if needed.

        Args:
            content: The LLM response content to validate
            model_cls: Pydantic model class to validate against
            messages: Original messages (for repair prompt context)
            tier: Model tier for repair calls
            agent_name: Agent name for repair calls
            schema_hint: Human-readable schema description for repair prompt

        Returns:
            ValidationResult with the validated/repaired data.
        """
        # First attempt: validate the original content
        data = validate_json(content)
        if data is not None:
            if model_cls is not None:
                try:
                    model_cls.model_validate(data)
                except Exception as e:
                    # JSON is valid but doesn't match schema — try repair
                    trace("structured", agent=agent_name, status="schema_mismatch",
                          error=str(e)[:200])
                else:
                    trace("structured", agent=agent_name, status="valid",
                          repair_attempts=0)
                    return ValidationResult(
                        success=True,
                        data=data,
                        repair_attempts=0,
                        original_content=content,
                    )
            else:
                trace("structured", agent=agent_name, status="valid",
                      repair_attempts=0)
                return ValidationResult(
                    success=True,
                    data=data,
                    repair_attempts=0,
                    original_content=content,
                )

        # Need to repair — but we need a router for that
        if self.router is None or messages is None or tier is None:
            trace("structured", agent=agent_name, status="no_repair",
                  reason="no_router")
            return ValidationResult(
                success=False,
                data=data,
                error="Invalid JSON and no router available for repair",
                repair_attempts=0,
                original_content=content,
            )

        # Repair loop
        current_content = content
        for attempt in range(1, self.MAX_REPAIR_ATTEMPTS + 1):
            trace("structured", agent=agent_name, status="repairing",
                  attempt=attempt)

            error_msg = "Invalid JSON" if data is None else "Schema validation failed"
            if model_cls and data is not None:
                try:
                    model_cls.model_validate(data)
                except Exception as e:
                    error_msg = str(e)[:300]

            repair_messages = list(messages) + [
                {"role": "assistant", "content": current_content},
                {"role": "user", "content": REPAIR_PROMPT.format(
                    error=error_msg,
                    previous_response=current_content[:2000],
                    schema_hint=schema_hint or "See the original system prompt for the expected schema.",
                )},
            ]

            try:
                response = await self.router.complete(
                    tier=tier,
                    messages=repair_messages,
                    agent_name=agent_name,
                )
                current_content = response.content
                data = validate_json(current_content)

                if data is not None:
                    if model_cls is not None:
                        try:
                            model_cls.model_validate(data)
                        except Exception:
                            continue  # Still doesn't match schema
                    trace("structured", agent=agent_name, status="repaired",
                          repair_attempts=attempt)
                    return ValidationResult(
                        success=True,
                        data=data,
                        repair_attempts=attempt,
                        original_content=content,
                    )
            except Exception as e:
                logger.debug(f"Repair attempt {attempt} failed: {e}")
                continue

        # All repair attempts failed
        trace("structured", agent=agent_name, status="repair_failed",
              attempts=self.MAX_REPAIR_ATTEMPTS)
        return ValidationResult(
            success=False,
            data=data,
            error=f"Failed to produce valid JSON after {self.MAX_REPAIR_ATTEMPTS} repair attempts",
            repair_attempts=self.MAX_REPAIR_ATTEMPTS,
            original_content=content,
        )
