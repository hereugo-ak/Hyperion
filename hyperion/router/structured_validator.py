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

from pydantic import BaseModel, ValidationError

from hyperion.obs.trace import trace

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

# Phase 5.1d: the old prose scanner was a single non-counting character-class
# regex over braces. A regex cannot count brackets, so on `{"a": {"b": 1},
# "c": 2}` it matched the INNER object and returned `{"b": 1}` — every
# top-level key silently destroyed. And it never matched square brackets at
# all, so an LLM returning a JSON *array* (which is exactly what
# fact_checker's claim-extraction prompt asks for) had its array wrapper
# stripped and only the first element survived: 30 claims -> 1.
# Balanced scanning replaces it. See `_scan_balanced`.
#
# The literal pattern is deliberately NOT written out here: a structural guard
# in tests/test_json_and_content_quality.py greps this file for it, and a
# comment quoting it would make that guard unfalsifiable.

_OPEN_TO_CLOSE = {"{": "}", "[": "]"}


def _scan_balanced(text: str, start: int) -> int | None:
    """Return the index just past the JSON value opening at ``text[start]``.

    Counts nesting depth and, critically, skips over delimiters that appear
    *inside string literals* (honouring backslash escapes) — otherwise a value
    like ``{"note": "cost is $5 (}"}`` would terminate early.

    Returns ``None`` if the value never closes (truncated LLM output).
    """
    opener = text[start]
    closer = _OPEN_TO_CLOSE.get(opener)
    if closer is None:
        return None

    depth = 0
    in_string = False
    escaped = False

    for i in range(start, len(text)):
        ch = text[i]

        if in_string:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
        elif ch in _OPEN_TO_CLOSE:
            depth += 1
        elif ch in ("}", "]"):
            depth -= 1
            if depth == 0:
                # Only a *matching* closer terminates the value; a stray `]`
                # closing a `{` means the payload is malformed, not complete.
                return i + 1 if ch == closer else None
            if depth < 0:
                return None

    return None


def _first_balanced_value(text: str) -> str | None:
    """Find the first complete, balanced JSON object or array in ``text``.

    Critically, this does **not** search past an opener that fails to close.
    On ``'[{"a": 1}'`` (a truncated array) the first opener is the ``[``; the
    ``{"a": 1}`` one character later *is* balanced, and a naive forward search
    would happily return it — silently converting "the LLM's response was cut
    off mid-array" into "here is your single-element result". That is the same
    salvage-a-fragment behaviour as the regex this replaced. An unbalanced
    leading opener means the payload is truncated, and truncated is ``None``.
    """
    for i, ch in enumerate(text):
        if ch not in _OPEN_TO_CLOSE:
            continue
        end = _scan_balanced(text, i)
        if end is not None:
            return text[i:end]
        # First opener never closes → everything after it is the interior of a
        # truncated value. Refuse rather than return a fragment.
        return None
    return None


def extract_json(content: str) -> str | None:
    """Extract JSON from LLM response content.

    Handles:
    - Raw JSON objects: ``{"key": "value"}`` (including arbitrary nesting)
    - Raw JSON arrays: ``[{"a": 1}, {"a": 2}]`` — the array is returned WHOLE,
      never collapsed to its first element
    - Code blocks: ````json\n{...}\n````
    - JSON embedded in prose: "Here is the result: {...}"

    Returns the JSON *substring*, or ``None`` when no balanced JSON value is
    present. Never returns a structurally-truncated fragment: a fragment that
    parses is far more dangerous than a clean ``None``, because the caller
    treats it as a successful extraction (§0.3).
    """
    if not content:
        return None

    # 1. Fenced code block — the LLM told us exactly where the payload is.
    match = _JSON_BLOCK_RE.search(content)
    if match:
        fenced = match.group(1).strip()
        if fenced:
            # A fence can still wrap prose ("here is the json: {...}"), and a
            # model can emit a fence that contains no JSON at all. Prefer the
            # balanced value inside the fence; fall back to the raw fence body
            # so behaviour never regresses for payloads json.loads accepts
            # (bare scalars, `null`, quoted strings).
            return _first_balanced_value(fenced) or fenced

    # 2. Whole content is a JSON value.
    stripped = content.strip()
    if stripped[:1] in _OPEN_TO_CLOSE and _scan_balanced(stripped, 0) == len(stripped):
        return stripped

    # 3. JSON embedded in prose — balanced scan, so nesting and arrays survive.
    return _first_balanced_value(stripped)


def validate_json(content: str) -> Any | None:
    """Parse and validate JSON from LLM content. Returns None on failure.

    Return type is ``Any``, not ``dict``: JSON arrays are legitimate LLM
    payloads (claim lists, chart series, source lists) and the previous
    ``dict``-only annotation is why array handling was never implemented.
    """
    json_str = extract_json(content)
    if json_str is None:
        return None
    try:
        return json.loads(json_str)
    except (json.JSONDecodeError, TypeError):
        logger.debug("extracted candidate was not valid JSON: %r", json_str[:200])
        return None


def validate_json_object(content: str) -> dict[str, Any] | None:
    """``validate_json`` restricted to objects — for schema-shaped callers."""
    data = validate_json(content)
    return data if isinstance(data, dict) else None


def validate_json_list(content: str) -> list[Any] | None:
    """``validate_json`` restricted to arrays.

    Callers that prompt for "a JSON list" use this so that a model wrapping
    its array in ``{"items": [...]}`` is unwrapped rather than discarded.
    """
    data = validate_json(content)
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        # Single-key envelope around a list is the most common LLM deviation.
        list_values = [v for v in data.values() if isinstance(v, list)]
        if len(list_values) == 1:
            return list_values[0]
    return None


def validate_pydantic[ModelT: BaseModel](
    content: str,
    model_cls: type[ModelT],
) -> tuple[ModelT | None, str]:
    """Validate JSON content against a Pydantic model.

    Returns (model_instance, error_message). On success, error is "".
    """
    data = validate_json(content)
    if data is None:
        return None, "Failed to extract valid JSON from response"

    try:
        instance = model_cls.model_validate(data)
        return instance, ""
    except ValidationError as e:
        return None, str(e)[:500]
    except (TypeError, ValueError, AttributeError) as e:
        # Not a validation failure — the *caller* passed something unusable
        # (non-model class, non-dict payload). Distinguish it in the log so a
        # wiring bug is not mistaken for a flaky LLM.
        logger.warning(
            "validate_pydantic could not apply %r to payload of type %s: %s",
            getattr(model_cls, "__name__", model_cls),
            type(data).__name__,
            e,
        )
        return None, f"{type(e).__name__}: {str(e)[:400]}"


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
        model_cls: type[BaseModel] | None = None,
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
                except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
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
                except Exception as e:  # noqa: BLE001 - best-effort, failure must not propagate
                    error_msg = str(e)[:300]

            repair_messages = list(messages) + [
                {"role": "assistant", "content": current_content},
                {"role": "user", "content": REPAIR_PROMPT.format(
                    error=error_msg,
                    previous_response=current_content[:2000],
                    schema_hint=schema_hint
                    or "See the original system prompt for the expected schema.",
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
                        except Exception as exc:  # noqa: BLE001 - failure is logged, not swallowed
                            logger.warning("%s: %s", "validate_and_repair", exc)
                            continue  # Still doesn't match schema
                    trace("structured", agent=agent_name, status="repaired",
                          repair_attempts=attempt)
                    return ValidationResult(
                        success=True,
                        data=data,
                        repair_attempts=attempt,
                        original_content=content,
                    )
            except Exception as e:  # noqa: BLE001 - failure is logged, not swallowed
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
