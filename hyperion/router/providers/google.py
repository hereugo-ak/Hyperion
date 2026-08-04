"""
HYPERION Google AI Studio Provider.

Google AI Studio provides Gemma and Gemini models via an OpenAI-compatible
API. This is our most abundant provider by RPD (~29,460/day) and serves
both the MICRO tier (Gemma workhorses) and the DEEP tier (Gemini long
context models). (§2.1)

Models on this provider:
- gemma-4-31b: MICRO — query generation, fact-check snippets, simple extraction
- gemma-4-26b: MICRO — backup workhorse
- gemini-2.5-flash: DEEP — deep context, long doc synthesis, grounding (20 RPD)
- gemini-3.5-flash: DEEP — disabled reserve
- gemini-3-flash: DEEP — disabled reserve

This is NOT a generic OpenAI client wrapper. It is the Google-specific
implementation that knows about Gemma's high RPD workhorse role and
Gemini's scarce DEEP-tier reserve role.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import httpx

from hyperion.config import ProviderType
from hyperion.router.providers.base import BaseProvider


@dataclass
class GoogleGroundingResponse:
    """Provider-native grounding output, preserving Google's audit metadata."""

    text: str = ""
    web_search_queries: list[str] = field(default_factory=list)
    grounding_chunks: list[dict[str, Any]] = field(default_factory=list)
    grounding_supports: list[dict[str, Any]] = field(default_factory=list)
    safety_refused: bool = False
    raw_response: dict[str, Any] = field(default_factory=dict)
    model: str = ""

    @property
    def billable_units(self) -> int:
        if "gemini-3" in self.model.casefold():
            return len([query for query in self.web_search_queries if query.strip()])
        return int(bool(self.web_search_queries or self.grounding_chunks))


class GoogleProvider(BaseProvider):
    """Google AI Studio provider — Gemma (MICRO) + Gemini (DEEP).

    The most abundant provider by RPD. Gemma models handle the high-volume
    MICRO tier work (query generation, fact-check snippets, sub-agent tasks)
    with 14,400 RPD each. Gemini 2.5 Flash serves the DEEP tier and native
    Google Search grounding with a 20 RPD project limit, so the budget planner
    preserves that capacity for critical tasks only.
    """

    @property
    def provider_type(self) -> ProviderType:
        return ProviderType.GOOGLE

    async def grounded_generate(
        self,
        *,
        model: str,
        query: str,
        client: httpx.AsyncClient | None = None,
    ) -> GoogleGroundingResponse:
        """Call Gemini's native API with Google Search; ordinary chat stays OpenAI-compatible."""
        if not self.config.api_key:
            raise RuntimeError("Google API key is not configured")
        url = (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            f"{model}:generateContent"
        )
        payload = {
            "contents": [{"role": "user", "parts": [{"text": query}]}],
            "tools": [{"google_search": {}}],
            "generationConfig": {"temperature": 0.0},
        }
        owns_client = client is None
        http = client or httpx.AsyncClient(timeout=60.0)
        try:
            response = await http.post(
                url,
                params={"key": self.config.api_key},
                json=payload,
            )
            response.raise_for_status()
            body = response.json()
        finally:
            if owns_client:
                await http.aclose()
        return self.parse_grounding_response(body, model=model)

    @staticmethod
    def parse_grounding_response(
        payload: dict[str, Any], *, model: str
    ) -> GoogleGroundingResponse:
        """Normalize generateContent metadata and newer Interactions search steps."""
        text_parts: list[str] = []
        queries: list[str] = []
        chunks: list[dict[str, Any]] = []
        supports: list[dict[str, Any]] = []
        safety_refused = False

        candidates = payload.get("candidates", [])
        for candidate in candidates if isinstance(candidates, list) else []:
            if not isinstance(candidate, dict):
                continue
            reason = str(candidate.get("finishReason", "")).upper()
            safety_refused = safety_refused or reason in {
                "SAFETY", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"
            }
            content = candidate.get("content", {})
            for part in content.get("parts", []) if isinstance(content, dict) else []:
                if isinstance(part, dict) and isinstance(part.get("text"), str):
                    text_parts.append(part["text"])
            metadata = candidate.get("groundingMetadata", {})
            if isinstance(metadata, dict):
                queries.extend(
                    str(item) for item in metadata.get("webSearchQueries", [])
                    if str(item).strip()
                )
                chunks.extend(
                    item for item in metadata.get("groundingChunks", [])
                    if isinstance(item, dict)
                )
                supports.extend(
                    item for item in metadata.get("groundingSupports", [])
                    if isinstance(item, dict)
                )

        outputs = payload.get("outputs", payload.get("output", []))
        if isinstance(outputs, dict):
            outputs = [outputs]
        for output in outputs if isinstance(outputs, list) else []:
            if not isinstance(output, dict):
                continue
            output_type = str(output.get("type", ""))
            if output_type == "google_search_call":
                arguments = output.get("arguments", {})
                if isinstance(arguments, dict):
                    queries.extend(
                        str(item) for item in arguments.get("queries", [])
                        if str(item).strip()
                    )
            if output_type in {"google_search_result", "model_output"}:
                for block in output.get("content", []):
                    if not isinstance(block, dict):
                        continue
                    if isinstance(block.get("text"), str):
                        text_parts.append(block["text"])
                    for annotation in block.get("annotations", []):
                        citation = annotation.get("url_citation", annotation)
                        if not isinstance(citation, dict):
                            continue
                        url = str(citation.get("url", ""))
                        if url:
                            chunks.append({"web": {
                                "uri": url,
                                "title": str(citation.get("title") or url),
                            }})

        prompt_feedback = payload.get("promptFeedback", {})
        if isinstance(prompt_feedback, dict) and prompt_feedback.get("blockReason"):
            safety_refused = True
        return GoogleGroundingResponse(
            text="".join(text_parts),
            web_search_queries=queries,
            grounding_chunks=chunks,
            grounding_supports=supports,
            safety_refused=safety_refused,
            raw_response=payload,
            model=model,
        )
