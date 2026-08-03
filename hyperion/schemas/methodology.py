"""W-10: the methodology record — a description of METHOD, not of staffing.

RC-10's root cause was that the methodology page answered "who ran?" when the
reader asked "how do you know?". The four bullets it emitted (Agents Used,
Sources Accessed, Data Points, Limitations) were three counts and a list. A
count is not a method, and an agent roster is internal telemetry that W-09
already forbids in client prose.

This module defines the *record*: six ordered subsections, each of which must
exist and each of which carries a narrative sentence plus the specific facts
that sentence rests on. The record is a leaf schema — it imports only stdlib
and pydantic — so ``hyperion.schemas.models.FinalReport`` can declare a field
of this type without an import cycle.

Two enforcement properties matter and both are structural, not advisory:

1. **The six subsections are mandatory and ordered.** ``MethodologyRecord``
   refuses to validate unless its subsection keys are exactly
   :data:`REQUIRED_SUBSECTION_KEYS` in that order. A record with five
   subsections, or with an extra "Agents Used" seventh subsection, is
   unconstructible (W-10 acceptance criteria + first failure mode).

2. **Every string is client prose.** ``heading``, ``narrative`` and each entry
   in ``facts`` are routed through ``ClientProse.of()`` by a field validator.
   That factory RAISES on any agent-name registry string, so "zero agent names
   in the methodology section" is not a filter that could be forgotten, it is a
   construction-time invariant. The import of ``ClientProse`` is deliberately
   deferred into the validator body: ``hyperion.schemas.narrative`` imports
   ``models``, ``models`` imports this module, so a module-level import here
   would close a cycle. Function-local imports for exactly this reason are
   used throughout the codebase (see ``orchestrator._apply_gap_resolutions``).

The *builder* lives in ``hyperion.output.methodology``. It is deterministic
and takes no LLM: a free-form prompt can describe research that never
happened, which is W-10's third failure mode.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# The six subsections of W-10, in the order the page prints them. The keys are
# stable machine identifiers; the human headings live on the subsection so the
# wording can change without breaking a test that asserts structure.
REQUIRED_SUBSECTION_KEYS: tuple[str, ...] = (
    "question_decomposition",
    "scope_and_method_selection",
    "retrieval_strategy_and_coverage",
    "source_inclusion_and_exclusion",
    "verification_procedure",
    "design_limitations",
)

# Canonical headings, keyed by subsection key. Held here rather than in the
# builder so the verification snippet in the audit (which greps the rendered
# PDF for "Question decomposition", "Scope and method selection",
# "Retrieval strategy", "inclusion", "Verification", "Limitations") has a
# single source of truth.
SUBSECTION_HEADINGS: dict[str, str] = {
    "question_decomposition": "Question decomposition",
    "scope_and_method_selection": "Scope and method selection",
    "retrieval_strategy_and_coverage": "Retrieval strategy and coverage",
    "source_inclusion_and_exclusion": "Source inclusion and exclusion criteria",
    "verification_procedure": "Verification procedure",
    "design_limitations": "Limitations of the design",
}


def _validate_client_prose(value: Any) -> str:
    """Route one string through ``ClientProse.of`` and return a plain ``str``.

    The return type is ``str``, not ``ClientProse``: the value has been
    *validated*, and keeping the field a plain ``str`` means the record
    round-trips through ``model_dump()`` / ``model_validate()`` unchanged
    (the record crosses the message bus inside ``FinalReport.model_dump()``).
    Re-validation on the far side re-runs this same check, so the boundary is
    enforced on both ends of the wire.
    """
    from hyperion.schemas.narrative import ClientProse

    return str(ClientProse.of(value))


class MethodologySubsection(BaseModel):
    """One of the six subsections: a heading, a sentence, and its evidence.

    ``narrative`` is required and non-empty. W-10's second failure mode is
    "filling the six subsections with counts only" — a subsection that carries
    ``facts`` and no sentence is exactly that failure, so it cannot validate.
    """

    model_config = ConfigDict(frozen=True)

    key: str = Field(description="Stable subsection identifier")
    heading: str = Field(description="Client-facing heading")
    narrative: str = Field(
        min_length=1,
        description="At least one sentence describing the method, not a count",
    )
    facts: list[str] = Field(
        default_factory=list,
        description="The specific figures/statements the narrative rests on",
    )

    _prose = field_validator("heading", "narrative")(_validate_client_prose)

    @field_validator("facts")
    @classmethod
    def _prose_facts(cls, v: list[Any]) -> list[str]:
        return [_validate_client_prose(f) for f in v]

    @field_validator("key")
    @classmethod
    def _known_key(cls, v: str) -> str:
        if v not in REQUIRED_SUBSECTION_KEYS:
            raise ValueError(
                f"Unknown methodology subsection key {v!r}. The methodology has "
                f"exactly six subsections: {list(REQUIRED_SUBSECTION_KEYS)}. "
                "Adding a seventh (for example an agent roster) is forbidden by "
                "W-09 and is what W-10 removed."
            )
        return v


class MethodologyRecord(BaseModel):
    """The complete methodology: exactly six subsections, in order.

    Built only by ``hyperion.output.methodology.build_methodology`` from
    recorded structures (the DAG's roster decisions, the W-07 insufficiency
    resolutions, the fact checker's counters, and the corpus of ``Source``
    objects actually attached to the report).
    """

    model_config = ConfigDict(frozen=True)

    subsections: list[MethodologySubsection] = Field(
        description="The six W-10 subsections, in REQUIRED_SUBSECTION_KEYS order"
    )

    @model_validator(mode="after")
    def _exactly_the_six_in_order(self) -> MethodologyRecord:
        keys = tuple(s.key for s in self.subsections)
        if keys != REQUIRED_SUBSECTION_KEYS:
            raise ValueError(
                "MethodologyRecord must carry exactly the six W-10 subsections "
                f"in order. Expected {list(REQUIRED_SUBSECTION_KEYS)}, got "
                f"{list(keys)}."
            )
        return self

    def by_key(self, key: str) -> MethodologySubsection:
        """Lookup used by tests and by the operator telemetry artifact."""
        for sub in self.subsections:
            if sub.key == key:
                return sub
        raise KeyError(key)
