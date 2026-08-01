"""W-09: narrative boundary — types that structurally cannot hold telemetry.

RC-9's root cause was that ``FinalReport`` carried BOTH client prose and
operator telemetry, and the client template read whichever fields it liked.
Every prior defence (P2-09 dict-repr cleaning, P2-32 dash ban,
``BANNED_SUBSTRINGS``) is a string filter applied AFTER the leak. This module
is the boundary:

- ``ClientProse``: a frozen value object whose validating factory RAISES on
  any of the six telemetry categories (dict repr, em/en dash, agent name,
  confidence-enum literal, verification-state literal, gap identifier). It
  never sanitises — silently stripping hides the upstream bug (W-09 failure
  modes, HYPERION_DEEP_AUDIT_2026-07-31.md §W-09).

- ``ClientReport``: the client-facing view of a ``FinalReport``. Built by one
  named transformation (``ClientReport.from_report``), carries no telemetry
  attributes at all, so a client template has nothing telemetry-shaped to
  resolve. This is what "the client template may reference ClientReport only"
  means at the type level.

- ``EngagementTelemetry`` + ``write_telemetry_artifact``: telemetry's own
  destination — an operator JSON artifact under ``<reports_dir>/diagnostics/``
  (the same home as the W-08 blocked-run diagnostic). The fact checker's
  findings are genuinely valuable there; they were simply never client copy.

``page_audit.BANNED_SUBSTRINGS`` stays exactly as it is — the render-time
backstop. After this item it should never fire; if it fires, a transformation
is missing, and that is precisely the signal we want (W-09 step 5).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field

from hyperion.schemas.agents import AgentName
from hyperion.schemas.models import ClaimStatus, ConfidenceLevel

# ─────────────────────────────────────────────────────────────────────────────
# ClientProse — validated narrative text (W-09 step 1)
# ─────────────────────────────────────────────────────────────────────────────

_DICT_REPR_RE = re.compile(r"\{['\"]")
_GAP_ID_RE = re.compile(r"\bgap_[A-Za-z0-9_]+", re.IGNORECASE)


def _display_name(agent: AgentName) -> str:
    """'market_analyst' -> 'Market Analyst' (the human-facing leak form)."""
    return agent.value.replace("_", " ").title()


# Exact-match sets, built once from the live enums so a new agent name or a
# new claim status is automatically banned without touching this list.
_CONFIDENCE_LITERALS: frozenset[str] = frozenset(c.value for c in ConfidenceLevel)
_VERIFICATION_LITERALS: frozenset[str] = frozenset(s.value for s in ClaimStatus)
_AGENT_SNIPPETS: frozenset[str] = frozenset(
    {a.value for a in AgentName}
    | {_display_name(a) for a in AgentName}
)


def _first_substring(haystack_lower: str, needles: Iterable[str]) -> str | None:
    for needle in needles:
        if needle.lower() in haystack_lower:
            return needle
    return None


class ClientProse(str):
    """A ``str`` subclass that can only be built through :meth:`of`.

    Subclassing ``str`` (rather than a NewType alias) means the value keeps
    full str ergonomics for templates — slicing, Jinja filters, ``[:300]`` —
    while construction is impossible without validation. The factory REJECTS,
    by raising ``ValueError``:

    1. any ``{`` followed by ``'`` or ``"`` (dict repr)
    2. U+2014 and U+2013 (em/en dash — the global typography ban)
    3. any agent-name registry string (``fact_checker`` or ``Fact Checker``)
    4. any confidence-enum literal standing alone (``Confidence: low``)
    5. any verification-state literal (``UNVERIFIABLE``, ``hallucinated``,
       ``unverified claim``)
    6. any gap identifier pattern (``gap_12``)
    """

    __slots__ = ()

    @classmethod
    def __get_pydantic_core_schema__(cls, source_type: Any, handler: Any) -> Any:
        """Teach pydantic to accept this type as a model field.

        Wire input may be a plain str (deserialization) or an already-built
        ClientProse; both go through the validating factory, never through a
        bare cast. Validation at construction is the whole point — a
        permissive schema here would be documentation, not a boundary.
        """
        from pydantic_core import core_schema

        return core_schema.no_info_after_validator_function(
            cls.of, core_schema.str_schema()
        )

    def __new__(cls, value: str, *, _validated: bool = False) -> "ClientProse":
        if not _validated:
            # Direct construction bypasses validation — reject it. The only
            # sanctioned path is ClientProse.of().
            raise TypeError("ClientProse must be constructed via ClientProse.of()")
        return super().__new__(cls, value)

    @classmethod
    def of(cls, value: Any) -> "ClientProse":
        """Validating factory. Raises ValueError on any telemetry category."""
        text = "" if value is None else str(value)
        lowered = text.lower()

        # 1. dict repr
        m = _DICT_REPR_RE.search(text)
        if m:
            raise ValueError(
                f"ClientProse rejected a dict repr at index {m.start()}: {text[:80]!r}"
            )
        # 2. dashes (the global em/en dash ban — P2-32 / W-16)
        if "—" in text or "–" in text:
            raise ValueError("ClientProse rejected an em/en dash (U+2014/U+2013)")
        # 3. agent names (registry-derived; covers 'Fact Checker', 'market_analyst')
        hit = _first_substring(lowered, _AGENT_SNIPPETS)
        if hit is not None:
            raise ValueError(f"ClientProse rejected an agent name: {hit!r}")
        # 4. confidence enum rendered as prose. The leak shape is a literal
        # attached to the word 'confidence' ('Confidence: low', 'confidence
        # is low'). A bare 'low'/'medium'/'high' is ordinary English ("costs
        # are low") and must NOT be rejected — banning common adjectives
        # would make legitimate narrative unconstructible.
        m = re.search(
            r"confidence\s*[:]\s*(high|medium|low)\b|confidence\s+is\s+(high|medium|low)\b",
            lowered,
        )
        if m:
            raise ValueError(
                f"ClientProse rejected a confidence literal: {m.group(0)!r}"
            )
        # 5. verification-state literals. 'verified' and 'plausible' are
        # excluded from substring matching — both are ordinary English words
        # that legitimately appear in client prose ("verified against two
        # independent sources" is exactly what the methodology section should
        # say). The remaining values are unambiguous telemetry: 'unverified',
        # 'contradicted', 'unverifiable', 'hallucinated'.
        _ordinary_english = {"verified", "plausible"}
        hit = _first_substring(
            lowered, [v for v in _VERIFICATION_LITERALS if v not in _ordinary_english]
        )
        if hit is not None:
            raise ValueError(f"ClientProse rejected a verification state: {hit!r}")
        if "unverified claim" in lowered:
            raise ValueError("ClientProse rejected 'unverified claim'")
        # 6. gap identifiers
        m = _GAP_ID_RE.search(text)
        if m:
            raise ValueError(f"ClientProse rejected a gap identifier: {m.group(0)!r}")

        return cls(text, _validated=True)

    @classmethod
    def of_many(cls, values: Iterable[Any]) -> list["ClientProse"]:
        return [cls.of(v) for v in values]


# ─────────────────────────────────────────────────────────────────────────────
# Client view models (W-09 step 2)
# ─────────────────────────────────────────────────────────────────────────────


class ClientFinding(BaseModel):
    """The client-visible slice of a KeyFinding — no agent, no enum, no gaps."""

    model_config = ConfigDict(frozen=True)

    title: ClientProse
    content: ClientProse


class ClientRisk(BaseModel):
    model_config = ConfigDict(frozen=True)

    description: ClientProse
    mitigation: ClientProse
    probability: int = Field(ge=1, le=5)
    impact: int = Field(ge=1, le=5)
    risk_score: int = Field(ge=1, le=25)


class ClientRiskAnalysis(BaseModel):
    model_config = ConfigDict(frozen=True)

    risks: list[ClientRisk] = Field(default_factory=list)
    top_risks: list[ClientRisk] = Field(default_factory=list)
    residual_risk_summary: ClientProse


class ClientSection(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    title: ClientProse
    key_insight: ClientProse
    body: ClientProse
    implications: ClientProse | None = None


class ClientReport(BaseModel):
    """Everything the client template may read — and nothing else.

    Deliberately absent: ``agents_used``, ``quality_score``,
    ``fact_check_report``, ``confidence_breakdown``, ``contradictions``,
    ``is_degraded``, ``chart_specifications``, every per-finding agent and
    confidence. A Jinja template holding this object CANNOT resolve a
    telemetry attribute; that is the enforcement (tested by
    ``client_template_isolation``).
    """

    model_config = ConfigDict(frozen=True)

    question: ClientProse
    recommendation: str  # wire form, e.g. 'conditional' — no enum object
    recommendation_rationale: ClientProse
    executive_summary: ClientProse
    critical_assumptions: list[ClientProse] = Field(default_factory=list)
    key_findings: list[ClientFinding] = Field(default_factory=list)
    sections: list[ClientSection] = Field(default_factory=list)
    risk_analysis: ClientRiskAnalysis | None = None
    limitations: list[ClientProse] = Field(default_factory=list)
    total_sources: int = 0
    total_data_points: int = 0
    generated_at: Any = None
    engagement_id: str = ""

    @classmethod
    def from_report(cls, report: Any) -> "ClientReport":
        """The one named transformation from telemetry-bearing FinalReport to
        client-safe view (W-09 step 4). Raises if any narrative field carries
        telemetry — the leak fails at construction, not on the printed page."""
        sections = [
            ClientSection(
                id=str(getattr(s, "id", "")),
                title=ClientProse.of(getattr(s, "title", "")),
                key_insight=ClientProse.of(getattr(s, "key_insight", "")),
                body=ClientProse.of(getattr(s, "body", "")),
                implications=(
                    ClientProse.of(s.implications)
                    if getattr(s, "implications", None)
                    else None
                ),
            )
            for s in (getattr(report, "sections", None) or [])
        ]
        risk = getattr(report, "risk_analysis", None)
        client_risk = None
        if risk is not None:
            def _cr(r: Any) -> ClientRisk:
                return ClientRisk(
                    description=ClientProse.of(getattr(r, "description", "")),
                    mitigation=ClientProse.of(getattr(r, "mitigation", "")),
                    probability=int(getattr(r, "probability", 1)),
                    impact=int(getattr(r, "impact", 1)),
                    risk_score=int(getattr(r, "risk_score", 1)),
                )

            client_risk = ClientRiskAnalysis(
                risks=[_cr(r) for r in (getattr(risk, "risks", None) or [])],
                top_risks=[_cr(r) for r in (getattr(risk, "top_risks", None) or [])],
                residual_risk_summary=ClientProse.of(
                    getattr(risk, "residual_risk_summary", "")
                ),
            )
        rec = getattr(report, "recommendation", "")
        return cls(
            question=ClientProse.of(getattr(report, "question", "")),
            recommendation=str(getattr(rec, "value", rec)),
            recommendation_rationale=ClientProse.of(
                getattr(report, "recommendation_rationale", "")
            ),
            executive_summary=ClientProse.of(getattr(report, "executive_summary", "")),
            critical_assumptions=ClientProse.of_many(
                getattr(report, "critical_assumptions", None) or []
            ),
            key_findings=[
                ClientFinding(
                    title=ClientProse.of(getattr(f, "title", "")),
                    content=ClientProse.of(getattr(f, "content", "")),
                )
                for f in (getattr(report, "key_findings", None) or [])
            ],
            sections=sections,
            risk_analysis=client_risk,
            limitations=ClientProse.of_many(getattr(report, "limitations", None) or []),
            total_sources=int(getattr(report, "total_sources", 0) or 0),
            total_data_points=int(getattr(report, "total_data_points", 0) or 0),
            generated_at=getattr(report, "generated_at", None),
            engagement_id=str(getattr(report, "engagement_id", "")),
        )


# ─────────────────────────────────────────────────────────────────────────────
# EngagementTelemetry — telemetry's own destination (W-09 step 3)
# ─────────────────────────────────────────────────────────────────────────────


class EngagementTelemetry(BaseModel):
    """Operator-side record of how the engagement actually went.

    This is where "N hallucinated citations detected" belongs. The problem was
    never that the fact checker found something; it is that the finding was
    addressed to the client (W-09 objective).
    """

    engagement_id: str = ""
    agents_used: list[str] = Field(default_factory=list)
    quality_score: dict[str, Any] | None = None
    fact_check_report: dict[str, Any] | None = None
    confidence_breakdown: dict[str, str] = Field(default_factory=dict)
    contradictions: list[dict[str, Any]] = Field(default_factory=list)
    section_confidence: dict[str, str] = Field(default_factory=dict)
    limitations: list[str] = Field(default_factory=list)
    is_degraded: bool = False

    @classmethod
    def from_report(cls, report: Any) -> "EngagementTelemetry":
        def _dump(obj: Any) -> dict[str, Any] | None:
            if obj is None:
                return None
            if hasattr(obj, "model_dump"):
                return obj.model_dump(mode="json")
            return {"repr": str(obj)}

        return cls(
            engagement_id=str(getattr(report, "engagement_id", "")),
            agents_used=[str(a) for a in (getattr(report, "agents_used", None) or [])],
            quality_score=_dump(getattr(report, "quality_score", None)),
            fact_check_report=_dump(getattr(report, "fact_check_report", None)),
            confidence_breakdown={
                str(k): str(getattr(v, "value", v))
                for k, v in (getattr(report, "confidence_breakdown", None) or {}).items()
            },
            contradictions=[
                c if isinstance(c, dict) else c.model_dump(mode="json")
                for c in (getattr(report, "contradictions", None) or [])
            ],
            section_confidence={
                str(getattr(s, "id", i)): str(
                    getattr(getattr(s, "confidence", ""), "value", getattr(s, "confidence", ""))
                )
                for i, s in enumerate(getattr(report, "sections", None) or [])
            },
            limitations=[str(x) for x in (getattr(report, "limitations", None) or [])],
            is_degraded=bool(getattr(report, "is_degraded", False)),
        )

    def render_html(self) -> str:
        """Self-contained operator telemetry page (standalone HTML document).

        Built from this object's own fields — it is addressed to the operator,
        so agent names, confidence literals and verification states are
        legitimate content here (they are not ClientProse).
        """

        from html import escape as _esc

        parts: list[str] = [
            "<!DOCTYPE html><html><head><meta charset='utf-8'>",
            f"<title>Engagement telemetry — {_esc(self.engagement_id)}</title>",
            "<style>body{font-family:Georgia,serif;margin:40px;color:#26221e}"
            "table{border-collapse:collapse;margin:12px 0}td,th{border:1px solid "
            "#d8d3c8;padding:4px 10px;text-align:left;font-size:13px}"
            "h1{font-size:22px}h2{font-size:16px;margin-top:28px;color:#8a4a32}"
            ".note{color:#8a8580;font-size:12px}</style></head><body>",
            f"<h1>Engagement telemetry</h1><p class='note'>Operator artifact. "
            f"Engagement {_esc(self.engagement_id)}. Not client copy.</p>",
        ]
        if self.is_degraded:
            parts.append(
                "<p><strong>DEGRADED RUN:</strong> synthesis failed; the "
                "recommendation is a placeholder. Do not ship.</p>"
            )
        if self.agents_used:
            parts.append("<h2>Roster</h2><ul>")
            parts.extend(f"<li>{_esc(a)}</li>" for a in self.agents_used)
            parts.append("</ul>")
        if self.confidence_breakdown:
            parts.append("<h2>Confidence by dimension</h2><table>")
            parts.append("<tr><th>Dimension</th><th>Confidence</th></tr>")
            for k, v in self.confidence_breakdown.items():
                label = str(k).replace("_", " ").capitalize()
                value = str(v).replace("_", " ").title()
                parts.append(f"<tr><td>{_esc(label)}</td><td>{_esc(value)}</td></tr>")
            parts.append("</table>")
        if self.section_confidence:
            parts.append("<h2>Confidence by section</h2><table>")
            parts.append("<tr><th>Section</th><th>Confidence</th></tr>")
            for k, v in self.section_confidence.items():
                parts.append(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>")
            parts.append("</table>")
        qs = self.quality_score
        if qs:
            parts.append("<h2>Quality scorecard</h2><table>")
            parts.append(
                f"<tr><td>Total</td><td>{qs.get('total_score')}</td></tr>"
                f"<tr><td>Threshold</td><td>{qs.get('threshold')}</td></tr>"
                f"<tr><td>Terminal state</td><td>{_esc(str(qs.get('terminal_state')))}</td></tr>"
                f"<tr><td>Iteration</td><td>{qs.get('iteration')}</td></tr>"
            )
            for dim in qs.get("dimensions") or []:
                if isinstance(dim, Mapping):
                    name = str(dim.get("name") or dim.get("dimension_id") or "dimension")
                    critical = " <em>(critical)</em>" if dim.get("critical") else ""
                    parts.append(
                        f"<tr><td>{_esc(name)}{critical}</td>"
                        f"<td>{dim.get('score')}/5</td></tr>"
                    )
            parts.append("</table>")
            if qs.get("gaps"):
                parts.append("<h2>Residual gaps</h2><ul>")
                parts.extend(f"<li>{_esc(str(g))}</li>" for g in qs["gaps"])
                parts.append("</ul>")
        fc = self.fact_check_report
        if fc:
            # Human-facing labels, matching the wording the client appendix
            # used before W-09 — the operator reads the same phrases in a new
            # home. Hallucinated citations and evidence-chain breaks are shown
            # even when zero: "0" is an affirmative claim, not an omission.
            fc_labels = (
                ("total_claims_checked", "Claims checked"),
                ("verified_count", "Verified"),
                ("unverified_count", "Unverified"),
                ("contradicted_count", "Contradicted"),
                ("verification_rate", "Verification rate"),
                ("hallucinated_citation_count", "Hallucinated citations"),
                ("evidence_chain_break_count", "Evidence-chain breaks"),
            )
            parts.append("<h2>Fact-check telemetry</h2><table>")
            for key, label in fc_labels:
                if key in fc:
                    value = fc[key]
                    if key == "verification_rate" and isinstance(value, (int, float)):
                        value = f"{value:.0%}"
                    parts.append(
                        f"<tr><td>{_esc(label)}</td><td>{value}</td></tr>"
                    )
            parts.append("</table>")
        if self.limitations:
            parts.append("<h2>Limitations</h2><ul>")
            parts.extend(f"<li>{_esc(x)}</li>" for x in self.limitations)
            parts.append("</ul>")
        if self.contradictions:
            parts.append("<h2>Contradictions</h2><table>")
            parts.append(
                "<tr><th>Type</th><th>Position A</th><th>Position B</th>"
                "<th>Agents</th><th>Resolution</th></tr>"
            )
            for c in self.contradictions:
                ctype = c.get("contradiction_type", "")
                resolution = c.get("resolution") or (
                    "Resolved" if c.get("resolved") else "Unresolved"
                )
                parts.append(
                    f"<tr><td>{_esc(str(ctype))}</td>"
                    f"<td>{_esc(str(c.get('finding_a', '')))}</td>"
                    f"<td>{_esc(str(c.get('finding_b', '')))}</td>"
                    f"<td>{_esc(str(c.get('agent_a', '')))} vs "
                    f"{_esc(str(c.get('agent_b', '')))}</td>"
                    f"<td>{_esc(str(resolution))}</td></tr>"
                )
            parts.append("</table>")
        parts.append("</body></html>")
        return "\n".join(parts)


def _reports_dir() -> Path:
    try:
        from hyperion.config import get_settings

        return Path(get_settings().reports_dir)
    except Exception:  # noqa: BLE001 - settings unavailable (tests, bare import)
        return Path("./reports")


def write_telemetry_artifact(report: Any) -> Path:
    """W-09 step 3: write ``EngagementTelemetry`` to its own operator artifact.

    JSON (machine-readable) plus a standalone HTML rendering — the same home
    as the W-08 blocked-run diagnostic, ``<reports_dir>/diagnostics/``. NEVER
    the deliverable path. Returns the JSON path.
    """
    telemetry = EngagementTelemetry.from_report(report)
    diag_dir = _reports_dir() / "diagnostics"
    diag_dir.mkdir(parents=True, exist_ok=True)
    slug = telemetry.engagement_id or "unknown"
    json_path = diag_dir / f"telemetry_{slug}.json"
    json_path.write_text(telemetry.model_dump_json(indent=2), encoding="utf-8")
    html_path = diag_dir / f"telemetry_{slug}.html"
    html_path.write_text(telemetry.render_html(), encoding="utf-8")
    return json_path


__all__ = [
    "ClientFinding",
    "ClientProse",
    "ClientReport",
    "ClientRisk",
    "ClientRiskAnalysis",
    "ClientSection",
    "EngagementTelemetry",
    "write_telemetry_artifact",
]
