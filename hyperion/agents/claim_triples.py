"""W-05: claim triples and opposition-based contradiction detection.

A contradiction is two claims about the SAME subject with incompatible
values or polarity. Nothing else may be called a contradiction. This module
is the replacement for the deleted inequality predicate
(``content_a != content_b``) in ``SynthesisLead._identify_contradictions``.

The pipeline (PART 1 §W-05):

1. Every finding is normalised into a ``ClaimTriple``
   ``(subject, predicate, value)`` before any pairing. ``value`` is either
   a numeric measurement (units and period extracted) or a categorical
   polarity in {supports, opposes} toward a named proposition. A finding
   that cannot be normalised is INELIGIBLE — logged, never forced.

2. Triples pair only when predicate matches AND subject matches. Subject
   matching is token-set similarity after entity normalisation; substring
   containment is never used ("India" must not match "Indian textiles").

3. Opposition is explicit:
   - Numeric: values disagree when they differ by more than
     ``RELATIVE_TOLERANCE`` after unit normalisation. Same number in
     different units ($1.2B vs $1200M) is NOT a contradiction.
   - Temporal guard: two measurements of the same quantity in different
     periods are NOT a contradiction.
   - Categorical: supports vs opposes on the same proposition.

4. Telemetry is never a claim: a finding whose text is a confidence enum,
   an agent name, or a dict repr is ineligible, so "Confidence: low" can
   never appear as a Position.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Start at 15 percent (PART 1 §W-05 step 4).
RELATIVE_TOLERANCE = 0.15

# Cap eligible contradictions, ranked by materiality (§W-05 step 7).
MAX_MATERIAL_CONTRADICTIONS = 5

# Subject/proposition token-set similarity threshold. Deliberately strict:
# loose matching is how related-but-distinct entities got paired.
SUBJECT_SIMILARITY_THRESHOLD = 0.6


class Polarity(str, Enum):
    SUPPORTS = "supports"
    OPPOSES = "opposes"


_NUM_RE = re.compile(
    r"(?<![A-Za-z0-9_])"  # never digits glued to a word token ("Metric0")
    r"(?P<cur>[$€£])?\s*(?P<num>\d[\d,]*\.?\d*)\s*"
    r"(?P<unit>trillion|billion|million|thousand|percent|percentage|points?|%|[TtBbMmKk])?\b",
    re.IGNORECASE,
)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")

_UNIT_MULTIPLIERS: dict[str, float] = {
    "trillion": 1e12, "t": 1e12,
    "billion": 1e9, "b": 1e9,
    "million": 1e6, "m": 1e6,
    "thousand": 1e3, "k": 1e3,
}
_PERCENT_UNITS = {"percent", "percentage", "%", "point", "points"}

_SUPPORT_TOKENS = {
    "support", "supports", "supported", "supporting", "favor", "favors",
    "favour", "favours", "endorse", "endorses", "endorsed", "recommends",
    "recommended", "backs", "backed", "advocates", "advocated", "beneficial",
    "tailwind", "favorable", "favourable", "approves", "approved",
}
_OPPOSE_TOKENS = {
    "oppose", "opposes", "opposed", "opposing", "against", "rejects",
    "rejected", "criticizes", "criticises", "criticized", "warns", "warned",
    "harmful", "headwind", "threatens", "threatened", "blocks", "blocked",
    "disapproves", "condemns", "resists", "resisted",
}

_STOPWORDS = {
    "the", "a", "an", "of", "in", "for", "to", "on", "at", "by", "is",
    "are", "was", "were", "and", "or", "with", "from", "that", "this",
    "it", "its", "as", "be", "been", "has", "have", "had", "will",
    "would", "could", "should", "vs", "versus", "per", "over", "under",
    "between", "into", "about", "their", "they", "them", "we", "our",
    "report", "reports", "reported", "according", "said", "says", "than",
    "then", "there", "these", "those", "which", "while", "when", "where",
    "also", "but", "not", "no", "new", "more", "most", "other", "some",
    "such", "only", "own", "same", "so", "too", "very", "can", "just",
    "now", "out", "up", "down", "after", "before", "during", "year",
    "years", "rose", "fell", "grew", "growth", "increase", "increased",
    "decrease", "decreased", "reached", "reach", "estimated", "estimate",
    "approximately", "around", "nearly", "roughly", "circa",
}

_CONFIDENCE_VALUES = {"low", "medium", "high"}
_AGENT_NAME_RE = re.compile(
    r"^[a-z]+(_(analyst|lead|checker|gate|designer|engine|director|visualizer|designer))+$"
)
_TELEMETRY_RES = (
    re.compile(r"confidence\s*[:\-]?\s*(low|medium|high)\b", re.IGNORECASE),
    re.compile(r"^\s*[\{\[]"),                      # dict/list repr
    re.compile(r"\{\s*'"),                          # embedded dict repr
    re.compile(r"^[A-Z][A-Z_ ]{2,}$"),              # ALL-CAPS enum name
)


@dataclass(frozen=True)
class NumericMeasurement:
    """One numeric measurement, normalised for comparison.

    ``value`` is canonical: unit multipliers applied ($1.2B -> 1.2e9) and
    percentages expressed as fractions (17% -> 0.17), so the same number in
    different units compares equal.
    """

    value: float
    raw: str
    unit: str | None
    year: int | None


@dataclass(frozen=True)
class ClaimTriple:
    """A finding normalised for contradiction analysis (§W-05 step 2).

    ``claim_text`` is the single string that is BOTH the compared text and
    the rendered text (§W-05 step 5) — the detector compares it and the
    appendix renders it, so a title can never be substituted for content.
    """

    agent: str
    predicate: str
    claim_text: str
    subject_tokens: frozenset[str]
    measurement: NumericMeasurement | None
    polarity: Polarity | None
    source_count: int
    confidence: str


def _is_year_number(raw_num: str, unit: str | None, cur: str | None) -> bool:
    """A bare 4-digit 19xx/20xx token with no unit/currency is a year, not
    a measurement."""
    if unit or cur:
        return False
    cleaned = raw_num.replace(",", "")
    if not cleaned.isdigit() or len(cleaned) != 4:
        return False
    return 1900 <= int(cleaned) <= 2099


def extract_measurements(text: str) -> list[NumericMeasurement]:
    """All numeric measurements in the text, with the text's period attached."""
    year_match = _YEAR_RE.search(text)
    year = int(year_match.group(1)) if year_match else None
    out: list[NumericMeasurement] = []
    for m in _NUM_RE.finditer(text):
        raw_num = m.group("num")
        unit = (m.group("unit") or "").lower() or None
        cur = m.group("cur")
        if _is_year_number(raw_num, unit, cur):
            continue
        try:
            base = float(raw_num.replace(",", ""))
        except ValueError:
            continue
        if unit in _PERCENT_UNITS:
            value = base / 100.0
        elif unit in _UNIT_MULTIPLIERS:
            value = base * _UNIT_MULTIPLIERS[unit]
        else:
            value = base
        out.append(
            NumericMeasurement(value=value, raw=m.group(0).strip(), unit=unit, year=year)
        )
    return out


def _strip_plural(token: str) -> str:
    if len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        return token[:-1]
    return token


def normalize_tokens(text: str) -> frozenset[str]:
    """Entity-normalised token set: lowercase, stopword/number/unit/year/
    polarity-free, plural-stripped. This — never substring containment —
    is what subject matching uses."""
    raw = re.sub(r"[^a-z0-9\s]", " ", text.lower())
    tokens: set[str] = set()
    for tok in raw.split():
        if (
            tok in _STOPWORDS
            or tok in _SUPPORT_TOKENS
            or tok in _OPPOSE_TOKENS
            or tok in _UNIT_MULTIPLIERS
            or tok in _PERCENT_UNITS
            or tok.isdigit()
            or _YEAR_RE.fullmatch(tok)
        ):
            continue
        stripped = _strip_plural(tok)
        if stripped and stripped not in _STOPWORDS and len(stripped) > 1:
            tokens.add(stripped)
    return frozenset(tokens)


def is_telemetry(text: str) -> bool:
    """§W-05 step 6: a confidence enum, an agent name, or a dict repr is
    telemetry, never a claim."""
    stripped = text.strip()
    if not stripped:
        return True
    if stripped.lower() in _CONFIDENCE_VALUES:
        return True
    if _AGENT_NAME_RE.match(stripped.lower()):
        return True
    return any(r.search(stripped) for r in _TELEMETRY_RES)


def detect_polarity(text: str) -> Polarity | None:
    """Categorical polarity toward the claim's proposition, decided by the
    earliest polarity token's position so "supports but warns against"
    reads as its leading stance."""
    raw = re.sub(r"[^a-z\s]", " ", text.lower())
    best: tuple[int, Polarity] | None = None
    for position, tok in enumerate(raw.split()):
        if tok in _SUPPORT_TOKENS:
            if best is None:
                best = (position, Polarity.SUPPORTS)
            break
        if tok in _OPPOSE_TOKENS:
            if best is None:
                best = (position, Polarity.OPPOSES)
            break
    return best[1] if best else None


def extract_triple(
    *,
    agent: str,
    predicate: str,
    claim_text: str,
    source_count: int = 0,
    confidence: str = "",
) -> ClaimTriple | None:
    """Normalise one finding into a ClaimTriple, or None when ineligible.

    Ineligible means: telemetry, no extractable subject, or neither a
    numeric measurement nor a categorical polarity. Ineligible findings are
    never forced into the analysis (§W-05 step 2).
    """
    text = claim_text.strip()
    if is_telemetry(text):
        return None
    tokens = normalize_tokens(text)
    if len(tokens) < 1:
        return None
    measurements = extract_measurements(text)
    measurement = measurements[0] if measurements else None
    polarity = detect_polarity(text)
    if measurement is None and polarity is None:
        return None
    return ClaimTriple(
        agent=agent,
        predicate=predicate,
        claim_text=text,
        subject_tokens=tokens,
        measurement=measurement,
        polarity=polarity,
        source_count=source_count,
        confidence=confidence,
    )


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def subjects_match(a: ClaimTriple, b: ClaimTriple) -> bool:
    """§W-05 step 3: token-set similarity only. Substring containment is
    never used, so "India" cannot pair with "Indian textiles"."""
    return _jaccard(a.subject_tokens, b.subject_tokens) >= SUBJECT_SIMILARITY_THRESHOLD


def numeric_opposition(a: NumericMeasurement, b: NumericMeasurement) -> bool:
    """§W-05 step 4: values disagree only beyond the relative tolerance,
    after unit normalisation, in the same period.

    - Same number in different units: NOT a contradiction (normalised
      values are equal).
    - Different periods: NOT a contradiction (temporal guard).
    """
    if a.year is not None and b.year is not None and a.year != b.year:
        return False
    magnitude = max(abs(a.value), abs(b.value))
    if magnitude == 0:
        return False
    return abs(a.value - b.value) / magnitude > RELATIVE_TOLERANCE


def opposition(a: ClaimTriple, b: ClaimTriple) -> str | None:
    """Whether two triples contradict, and how.

    Returns "data_conflict", "interpretation", or None. Pairing requires
    predicate match AND subject match; opposition requires incompatible
    values (numeric) or polarity (categorical). Mixed pairs (one numeric,
    one categorical) never contradict.
    """
    if a.predicate != b.predicate:
        return None
    if not subjects_match(a, b):
        return None
    if a.measurement is not None and b.measurement is not None:
        if numeric_opposition(a.measurement, b.measurement):
            return "data_conflict"
        return None
    if a.polarity is not None and b.polarity is not None:
        if a.polarity != b.polarity:
            return "interpretation"
        return None
    return None


def rank_materiality(triples_pair: tuple[ClaimTriple, ClaimTriple, str]) -> tuple[int, int]:
    """Materiality ordering for the budget cap (§W-05 step 7): total
    evidence behind the pair, then combined confidence."""
    a, b, _ = triples_pair
    confidence_rank = {"high": 3, "medium": 2, "low": 1}
    return (
        a.source_count + b.source_count,
        confidence_rank.get(a.confidence, 0) + confidence_rank.get(b.confidence, 0),
    )
