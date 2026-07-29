"""HYPERION — Query hygiene and grounding utilities.

Two responsibilities:

1. **Hygiene** (`normalize_query`) — strip junk that leaks from agent
   internals (agent-name suffixes, bare numbers/currency) and reject
   queries too thin to be real searches.

2. **Grounding** (`ground_query`) — anchor every search to the USER'S
   actual question. This is the fix for the class of bug where a
   specialist's hardcoded f-string template interpolated an empty
   variable and issued a subject-less search. Real observed failures
   from a run whose question was *"should india reduce its dependence
   on the imports"*:

       q=carbon+footprint+emissions+data           ← {sector} was ""
       q=manufacturing+throughput+benchmarks+industry
       q=all:vendor+comparison+2024+2025           ← no subject at all
       q=all:architecture+case+study

   Those searches cannot answer the question that was asked. Grounding
   guarantees two things for every outbound query:

     - It is never subject-less. If the template produced nothing
       meaningful, the query is rebuilt from the engagement subject.
     - It carries the engagement's subject/geography anchor, so an
       India question never silently returns US-only material.

The engagement focus is set once per engagement by the orchestrator via
`set_engagement_focus()` and read by the search client. Module-level
state is deliberate: search calls happen deep inside 11 specialist
modules across 45 call sites, and threading a context object through all
of them would be far more invasive and easier to get wrong.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Any

_module_logger = logging.getLogger(__name__)

_INTERNAL_TOKENS = {
    "risk analyst", "technology analyst", "financial analyst", "market analyst",
    "competitive intel", "operations analyst", "regulatory analyst",
    "sustainability analyst", "innovation analyst", "consumer insights",
    "strategy analyst", "ma analyst",
}

_STOPISH = re.compile(r"^[\s\W\d%$.,]+$")

# Placeholder debris left behind when an f-string template interpolated an
# empty or null variable, e.g. f"{company} ESG rating" with company="".
_PLACEHOLDER_DEBRIS = re.compile(
    r"\b(?:none|null|n/?a|unknown|not\s+specified|undefined|true|false)\b",
    re.IGNORECASE,
)

# Generic template words that carry no subject on their own. A query made
# up of only these is a template that failed to interpolate.
_CONTENTLESS = {
    "best", "top", "vendor", "comparison", "compare", "review", "reviews",
    "case", "study", "studies", "architecture", "benchmark", "benchmarks",
    "data", "report", "reports", "analysis", "overview", "guide", "list",
    "industry", "sector", "market", "companies", "company", "pricing",
    "alternatives", "documentation", "api", "requirements", "opportunities",
    "performance", "rating", "ratings", "trends", "statistics", "stats",
    "emissions", "footprint", "carbon", "throughput", "practices", "rates",
}


# ─────────────────────────────────────────────────────────────────────────────
# Geography detection
# ─────────────────────────────────────────────────────────────────────────────
#
# This gazetteer exists to DETECT a geography that the user actually named,
# never to supply a default one. The distinction matters: a hardcoded default
# of ["US", "EU"] once caused a question about INDIA to be analysed under the
# US Buy American Act, producing 119 authoritative-looking findings about the
# wrong country. Detection is question-derived and therefore safe; defaulting
# is fabrication. When nothing is detected we return an empty list and let the
# caller degrade to a jurisdiction-agnostic analysis.
#
# Maps: alias found in free text -> canonical jurisdiction label.
_GEO_ALIASES: dict[str, str] = {
    # South Asia
    "india": "India", "indian": "India", "bharat": "India",
    "pakistan": "Pakistan", "bangladesh": "Bangladesh", "sri lanka": "Sri Lanka",
    "nepal": "Nepal",
    # North America
    #
    # NOTE the absence of a lowercase "us" alias here — see
    # _ACRONYM_ONLY_ALIASES below. "us" is an English pronoun, and matching it
    # case-insensitively made "help us decide whether to enter India" resolve
    # to the United States as the PRIMARY geography (US appears at offset 5,
    # India at offset 38), which then anchored every grounded search to the
    # wrong country. That is the original failure of this system, reintroduced
    # through the back door of a convenience alias.
    "united states": "US", "usa": "US", "u.s.": "US", "u.s.a": "US",
    "america": "US", "american": "US", "US": "US",
    "canada": "Canada", "canadian": "Canada",
    "mexico": "Mexico", "mexican": "Mexico",
    # Europe
    "european union": "EU", "europe": "EU", "european": "EU", "eu": "EU",
    "eea": "EU", "eurozone": "EU",
    "united kingdom": "UK", "uk": "UK", "britain": "UK", "british": "UK",
    "england": "UK",
    "germany": "Germany", "german": "Germany",
    "france": "France", "french": "France",
    "italy": "Italy", "spain": "Spain", "netherlands": "Netherlands",
    "poland": "Poland", "sweden": "Sweden", "ireland": "Ireland",
    "switzerland": "Switzerland",
    # East / Southeast Asia
    "china": "China", "chinese": "China", "prc": "China",
    "japan": "Japan", "japanese": "Japan",
    "south korea": "South Korea", "korea": "South Korea", "korean": "South Korea",
    "taiwan": "Taiwan", "hong kong": "Hong Kong",
    "singapore": "Singapore", "vietnam": "Vietnam", "thailand": "Thailand",
    "indonesia": "Indonesia", "malaysia": "Malaysia", "philippines": "Philippines",
    # Middle East / Africa
    "uae": "UAE", "united arab emirates": "UAE", "dubai": "UAE",
    "saudi arabia": "Saudi Arabia", "saudi": "Saudi Arabia",
    "israel": "Israel", "turkey": "Turkey", "qatar": "Qatar",
    "egypt": "Egypt", "nigeria": "Nigeria", "kenya": "Kenya",
    "south africa": "South Africa",
    # Oceania / LatAm
    "australia": "Australia", "australian": "Australia",
    "new zealand": "New Zealand",
    "brazil": "Brazil", "brazilian": "Brazil",
    "argentina": "Argentina", "chile": "Chile", "colombia": "Colombia",
    # Blocs
    "asean": "ASEAN", "brics": "BRICS", "g7": "G7", "g20": "G20",
    "apac": "APAC", "emea": "EMEA", "latam": "LatAm",
    "global": "Global", "worldwide": "Global",
}

# Aliases short enough to be matched by other words ("us" inside "thus",
# "eu" inside "euro") need boundary matching. Longest-first so "united states"
# beats "states".
#
# Boundaries are alphanumeric LOOKAROUNDS rather than `\b`. This is not
# cosmetic: `\b` is a transition between a word char and a non-word char, and
# `.` is itself a non-word char, so `r"\bu\.s\.\b"` requires a word character
# immediately after the final dot. In the overwhelmingly common phrasing
# "the U.S. market" the dot is followed by a SPACE, so the pattern could not
# match and `detect_geographies("the U.S. market")` returned []. An
# undetected geography then propagates as "no jurisdiction filter", which is
# the honest degradation — but it is still a detection miss on one of the most
# frequently written aliases in the gazetteer.
#
# The lookarounds assert only that the alias is not glued to another
# alphanumeric token, which is the property actually wanted: "U.S." matches in
# "the U.S. market" while "u.s.a" is correctly left to the longer alias.
#
# CASE SENSITIVITY. Most aliases are proper nouns and are safely matched
# case-insensitively — "should india reduce imports" must find India even
# though the user did not capitalise it. But three aliases collide with common
# English function words, and for those a case-insensitive match is actively
# harmful:
#
#   "us" -> the first-person plural pronoun. "help us decide whether to
#           enter India" put US at offset 5 and India at offset 38, so US
#           became the PRIMARY geography and every grounded search was
#           anchored to the United States. This is verbatim the failure that
#           produced 119 findings about America for a question about India.
#   "it" -> not in the gazetteer, but reserved here for the same reason.
#   "in" -> the preposition; likewise never an alias.
#
# The rule: an alias listed with any uppercase character in _GEO_ALIASES is
# matched CASE-SENSITIVELY, so only the acronym form counts. "US", "U.S.",
# "USA", "United States" and "America" all still resolve to the United
# States; the bare lowercase pronoun "us" does not resolve to anything.
# Because `re.escape` is applied to the alias as written, adding an
# uppercase-only alias is the whole mechanism — no separate list to keep in
# sync.
_ACRONYM_ONLY_ALIASES = frozenset({"US"})


def _alias_flags(alias: str) -> int:
    """Return the regex flags for one alias.

    Aliases that are indistinguishable from English function words when
    lowercased must be matched case-sensitively so a pronoun cannot anchor an
    engagement to the wrong jurisdiction.
    """
    if alias in _ACRONYM_ONLY_ALIASES or alias != alias.lower():
        return 0
    return re.IGNORECASE


_GEO_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"(?<![A-Za-z0-9])" + re.escape(alias) + r"(?![A-Za-z0-9])",
            _alias_flags(alias),
        ),
        canonical,
    )
    for alias, canonical in sorted(
        _GEO_ALIASES.items(), key=lambda kv: len(kv[0]), reverse=True
    )
]


def canonicalize_geographies(values: Any, limit: int = 4) -> list[str]:
    """Normalise geography labels that an *agent* extracted, preserving order.

    ROLE OF THIS FUNCTION IN THE ARCHITECTURE. Geography is decided by the
    Engagement Director — the agent that receives the question and breaks it
    down. It already makes an LLM call to decompose the question, and an LLM
    reading "should India reduce its dependence on the imports" understands
    the subject country far better than any word list can. This gazetteer's
    job is therefore NOT to decide the geography. It has two narrower jobs:

    1. Canonicalise — map the agent's phrasing onto the labels the rest of
       the pipeline uses, so "Bharat", "the Indian market" and "India" all
       become "India", and "the States" becomes "US". Without this, the
       jurisdiction filter and the report headers disagree with each other.
    2. Backstop — when the LLM call fails outright (five providers, any of
       which can time out or rate-limit), ``detect_geographies`` still gives
       the engagement a country anchor drawn from the user's own words.

    It must never OVERRIDE what the agent extracted. A label the agent
    returned that this gazetteer does not recognise is kept verbatim and
    merely title-cased: the gazetteer being incomplete is not evidence that
    the agent is wrong. "Uzbekistan" is absent from the alias table, and
    silently dropping it would be exactly the class of bug this whole module
    exists to prevent.

    Returns ``[]`` for empty input — meaning "no jurisdiction filter", which
    is honest. It never invents a default.
    """
    if values is None:
        return []
    if isinstance(values, str):
        raw_items = [values]
    elif isinstance(values, (list, tuple, set)):
        raw_items = [v for v in values]
    else:
        return []

    out: list[str] = []
    for item in raw_items:
        if not isinstance(item, str):
            continue
        label = _strip_debris(item).strip(" ,;:-\"'")
        if not label:
            continue
        # Alias lookup comes BEFORE the contentless check, deliberately.
        # is_contentless() only counts words of 3+ letters, because it exists
        # to reject query templates like "vendor comparison 2024". Applied to
        # a geography label that logic discards every two-letter jurisdiction
        # code: canonicalize_geographies("US") and ("EU") both returned []
        # before this reordering, silently dropping the two most common
        # jurisdictions in the gazetteer.
        exact = _GEO_ALIASES.get(label.lower()) or _GEO_ALIASES.get(label.upper())
        if exact:
            if exact not in out:
                out.append(exact)
            continue
        if is_contentless(label):
            continue
        # A sentence is not a geography label. LLMs occasionally answer
        # "the question concerns India and its trade partners" — run the
        # gazetteer over that prose instead of using it verbatim.
        if len(label.split()) > 6 or any(ch in label for ch in ".?!"):
            for hit in detect_geographies(label, limit=limit):
                if hit not in out:
                    out.append(hit)
            continue
        # Exact alias hit — the common case. Case-insensitive lookup is safe
        # here because the agent asserted this string IS a geography; the
        # pronoun ambiguity that forces case-sensitivity in free-text
        # scanning cannot arise from a dedicated geography field.
        canonical = _GEO_ALIASES.get(label.lower())
        if canonical is None and label.upper() in _GEO_ALIASES:
            canonical = _GEO_ALIASES[label.upper()]
        if canonical is None:
            # Try the gazetteer inside the phrase ("the Indian market").
            hits = detect_geographies(label, limit=1)
            canonical = hits[0] if hits else None
        if canonical is None:
            # Unknown to the gazetteer — trust the agent, do not drop it.
            canonical = label if label.isupper() else label.title()
        if canonical not in out:
            out.append(canonical)

    if len(out) > 1 and "Global" in out:
        out = [g for g in out if g != "Global"] + ["Global"]
    return out[:limit]


def detect_geographies(*texts: str, limit: int = 4) -> list[str]:
    """Return canonical jurisdictions explicitly named in the given text.

    This is the BACKSTOP path, not the primary one. Geography is extracted by
    the Engagement Director's decomposition call (see
    ``canonicalize_geographies``); this deterministic scan exists so that a
    failed or partial LLM response still leaves the engagement anchored to a
    country the user actually named.

    Returns ``[]`` when nothing is named — callers MUST treat that as
    "analyse without a jurisdiction filter", never as "assume US/EU".

    Order is by first appearance across ``texts``, so the primary subject of
    the question ranks first (an India question yields ["India", ...]).
    """
    found: list[str] = []
    for text in texts:
        if not text or not isinstance(text, str):
            continue
        hits: list[tuple[int, str]] = []
        for pattern, canonical in _GEO_PATTERNS:
            m = pattern.search(text)
            if m:
                hits.append((m.start(), canonical))
        for _pos, canonical in sorted(hits, key=lambda h: h[0]):
            if canonical not in found:
                found.append(canonical)
    # "Global" is a weak signal — demote it below any concrete country.
    if len(found) > 1 and "Global" in found:
        found = [g for g in found if g != "Global"] + ["Global"]
    return found[:limit]


# ─────────────────────────────────────────────────────────────────────────────
# Engagement focus — set once per engagement, read by the search client
# ─────────────────────────────────────────────────────────────────────────────

class _EngagementFocus:
    """Thread-safe holder for the current engagement's search anchor."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.question: str = ""
        self.subject: str = ""
        self.geography: str = ""

    def set(self, question: str = "", subject: str = "", geography: str = "") -> None:
        with self._lock:
            self.question = (question or "").strip()
            self.subject = (subject or "").strip()
            self.geography = (geography or "").strip()

    def clear(self) -> None:
        self.set("", "", "")

    def snapshot(self) -> tuple[str, str, str]:
        with self._lock:
            return self.question, self.subject, self.geography


_FOCUS = _EngagementFocus()


def set_engagement_focus(question: str = "", subject: str = "", geography: str = "") -> None:
    """Set the engagement's search anchor. Called once per engagement.

    Args:
        question: The user's verbatim question — the ground truth.
        subject: Industry/sector/topic distilled from the question.
        geography: Country/region the question is about.
    """
    _FOCUS.set(question=question, subject=subject, geography=geography)


def clear_engagement_focus() -> None:
    """Clear the anchor so one engagement can't leak into the next."""
    _FOCUS.clear()


def get_engagement_focus() -> tuple[str, str, str]:
    """Return the current (question, subject, geography) anchor."""
    return _FOCUS.snapshot()


def resolve_subject(context: Any = None, *keys: str, question: str = "") -> str:
    """Resolve the subject a specialist should search about.

    WHY THIS EXISTS. Every specialist used to build queries as
    ``f"{sector} carbon footprint emissions data"`` where ``sector`` came
    straight from ``self._context.get("sector", "")``. When the Engagement
    Director's handover carried no ``sector`` key — the normal case for a
    macro question like "should India reduce its dependence on imports" —
    that interpolated to the empty string and the agent fired
    ``"carbon footprint emissions data"`` at the search engine. The docker
    logs confirmed exactly that query going out. The search was
    grammatically fine and completely untethered from the engagement.

    Resolution order, most specific first:

    1. The named ``context`` keys, in the order given (an explicit handover
       value from the Director is the best signal we have).
    2. The engagement subject distilled from the user's question.
    3. Intent words mined out of the question itself.

    Returns ``""`` only when there is genuinely no subject anywhere. Callers
    should then omit the subject token rather than emit a bare template —
    ``ground_query`` will rebuild the query from the engagement focus at the
    search choke point, so a stray empty result is still recoverable.

    A value from ``context`` is only accepted if it looks like a *label*, not
    a sentence. Handover payloads sometimes contain whole paragraphs, and
    splicing a paragraph into a search query is as useless as splicing "".
    """
    def _usable(value: Any) -> str:
        if not isinstance(value, str):
            # Lists of labels are common; take the first usable entry.
            if isinstance(value, (list, tuple)):
                for item in value:
                    got = _usable(item)
                    if got:
                        return got
            return ""
        candidate = _strip_debris(value).strip(" ,;:-")
        if not candidate:
            return ""
        # A label, not prose. >12 words or sentence punctuation means the
        # Director handed us a description, which we must not use verbatim.
        words = candidate.split()
        if len(words) > 12 or any(ch in candidate for ch in ".?!"):
            return ""
        if is_contentless(candidate):
            return ""
        return candidate[:80]

    if isinstance(context, dict):
        for key in keys:
            got = _usable(context.get(key))
            if got:
                return got

    focus_q, focus_subject, _focus_geo = _FOCUS.snapshot()
    got = _usable(focus_subject)
    if got:
        return got

    # Last resort: mine the question. Better a rough subject drawn from the
    # user's own words than a query about nothing.
    source = question or focus_q
    if source:
        mined = normalize_query(source)
        if mined and not is_contentless(mined):
            return mined[:80]

    return ""


# ─────────────────────────────────────────────────────────────────────────────
# Hygiene
# ─────────────────────────────────────────────────────────────────────────────

def _strip_debris(s: str) -> str:
    """Remove placeholder debris and collapse whitespace/punctuation."""
    s = _PLACEHOLDER_DEBRIS.sub(" ", s)
    # Collapse the empty-interpolation artifacts: "for  in", "of  in", stray
    # quotes, and repeated separators left where a variable used to be.
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\s*([,;:])\s*\1+", r"\1 ", s)
    s = re.sub(r"^[\s\-–—,;:]+|[\s\-–—,;:]+$", "", s)
    s = re.sub(r"\b(for|in|of|at|by|with|and|or)\s+(?=(?:for|in|of|at|by|with|and|or)\b)", "", s, flags=re.I)
    return re.sub(r"\s+", " ", s).strip()


def normalize_query(q: str) -> str:
    """Normalize a search query — strip junk, reject thin queries.

    Returns an empty string if the query is too thin to be a real search.
    Callers should treat "" as "do not search".
    """
    if not q:
        return ""
    s = _strip_debris(q.strip())
    low = s.lower()

    # Strip agent-name suffixes that leaked in
    for tok in _INTERNAL_TOKENS:
        if low.endswith(tok):
            s = s[: len(s) - len(tok)].strip()
            low = s.lower()

    # Remove standalone currency/percent tokens
    s = re.sub(
        r"[$\u20ac\u00a3]\s?\d[\d,.\-\u2013\u2014]*\s?(?:[mbtk]|bn|billion|million|trillion)?",
        " ", s, flags=re.I,
    )
    # Strip percentages outright (pure noise in a search query).
    s = re.sub(r"(?<![A-Za-z0-9])\d[\d,.]*\s?%", " ", s)
    # Strip bare numeric tokens, but PRESERVE numbers that qualify a preceding
    # word — "Section 301", "ISO 14001", "Scope 3", "Article 5" are the actual
    # subject of a regulatory search, and dropping the digits destroys them.
    kept: list[str] = []
    for tok in s.split():
        bare_num = re.fullmatch(r"[\d,.\-\u2013\u2014]+", tok)
        if bare_num:
            prev_is_word = bool(kept) and re.search(r"[A-Za-z]", kept[-1])
            is_year = re.fullmatch(r"(?:19|20)\d{2}", tok)
            # Keep it only if it qualifies a preceding word and isn't a year
            # (years are recency noise: "vendor comparison 2024 2025").
            if prev_is_word and not is_year:
                kept.append(tok)
            continue
        kept.append(tok)
    s = re.sub(r"\s+", " ", " ".join(kept)).strip()

    # Drop 1-char noise, but keep single digits that qualify the previous word
    # ("Scope 3", "Tier 1", "Article 5") — dropping them changes the meaning.
    tokens: list[str] = []
    for tok in s.split():
        if len(tok) > 1 or tok.isdigit() and tokens and re.search(r"[A-Za-z]", tokens[-1]):
            tokens.append(tok)
    # Count only alphabetic tokens toward the "too thin" threshold, so
    # "Scope 3 emissions" (2 words + a digit) isn't rejected as thin.
    if len([t for t in tokens if re.search(r"[A-Za-z]", t)]) < 3:
        return ""
    if _STOPISH.match(s):
        return ""

    return " ".join(tokens)[:256]


def is_contentless(q: str) -> bool:
    """True if the query has no subject — only generic template words.

    This is what catches "vendor comparison 2024 2025" and
    "carbon footprint emissions data": grammatically fine, but they
    describe a *shape* of information with nothing to look it up about.
    """
    if not q:
        return True
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z\-]+", q.lower()) if len(w) > 2]
    if not words:
        return True
    return all(w in _CONTENTLESS for w in words)


# ─────────────────────────────────────────────────────────────────────────────
# Grounding
# ─────────────────────────────────────────────────────────────────────────────

def ground_query(
    raw: str,
    subject: str = "",
    geography: str = "",
    *,
    drop_geography: bool = False,
) -> str:
    """Anchor a search query to the engagement's real subject.

    Guarantees the returned query is about what the user actually asked.
    Falls back to explicit args, then to the engagement focus.

    Returns "" only when there is genuinely nothing to search for — no
    usable template query AND no engagement subject.

    Args:
        drop_geography: fix 1.5 (audit §7 item 1.5, low-yield reformulation).
            When True, the geography anchor is *never* applied — neither the
            explicit ``geography`` argument nor the engagement-focus
            fallback — regardless of whether either is set. This is the
            "broaden" half of "if a query returns <3 scored results,
            broaden (drop geography) and retry once": a jurisdiction anchor
            that is too narrow for the live corpus (e.g. "Uzbekistan
            semiconductor tariffs") should not be silently re-added by the
            normal fallback path when a caller has deliberately asked to
            search without it.
    """
    focus_q, focus_subject, focus_geo = _FOCUS.snapshot()
    subject = (subject or focus_subject or "").strip()
    geography = "" if drop_geography else (geography or focus_geo or "").strip()

    cleaned = normalize_query(raw or "")

    # If the template collapsed to nothing (or to pure boilerplate), rebuild
    # the query from the engagement subject instead of firing a useless search.
    if not cleaned or is_contentless(cleaned):
        intent = _extract_intent(raw or "")
        rebuilt = " ".join(p for p in (subject, intent) if p).strip()
        if not rebuilt:
            rebuilt = subject or normalize_query(focus_q)
        cleaned = normalize_query(rebuilt) or rebuilt.strip()
        if not cleaned:
            return ""

    low = cleaned.lower()

    # Ensure the subject anchor is present so results stay on-topic.
    if subject:
        subject_tokens = [t for t in re.findall(r"[a-z][a-z\-]+", subject.lower()) if len(t) > 2]
        if subject_tokens and not any(t in low for t in subject_tokens):
            cleaned = f"{subject} {cleaned}"
            low = cleaned.lower()

    # Ensure the geography anchor is present. Without this, an India
    # question happily returned US-only regulatory material.
    if geography and geography.lower() not in low:
        cleaned = f"{cleaned} {geography}"

    return cleaned.strip()[:256]


class ContentlessQueryError(ValueError):
    """Raised by :func:`ground_query_or_raise` when grounding yields nothing.

    A distinct exception type (rather than returning "") lets a call site
    that truly cannot proceed without a query fail loudly, while callers
    that want the historical "drop and return empty results" behaviour can
    catch it explicitly instead of accidentally treating "" as a query.
    """


def ground_query_or_raise(raw: str, subject: str = "", geography: str = "") -> str:
    """``ground_query`` that raises instead of returning "".

    Same anchoring rules as :func:`ground_query`. Use this at a call site
    that should never silently proceed with a subject-less query.
    """
    grounded = ground_query(raw, subject=subject, geography=geography)
    if not grounded:
        raise ContentlessQueryError(
            f"query has no subject after grounding: {raw[:120]!r}"
        )
    return grounded


def grounded_search_or_empty(
    raw: str,
    empty_factory: Any,
    subject: str = "",
    geography: str = "",
    *,
    logger: Any = None,
    tool_name: str = "search",
    drop_geography: bool = False,
) -> tuple[str, Any | None]:
    """Shared choke point for "ground, or drop the query and return empty".

    Fix 1.2 (HYPERION_DEEP_AUDIT_2026-07-27.md §7, item 1.2): every search
    client independently re-implemented the same three lines — ground the
    query, log+drop if it came back empty, log if it changed. Duplicating
    that logic per-client is exactly how `jina.py`, `unified_search.py`,
    `deep_search.py` and `stealth_search.py` ended up with `ground_query`
    call counts of zero: a new search tool could be added without anyone
    remembering to wire grounding in, because there was no single place
    that *made* it happen.

    This helper is now that single place. It does not by itself guarantee
    every tool calls it — Python cannot enforce that structurally — but it
    turns "add grounding" from "copy 12 lines correctly" into "call this
    once at the top of your search() method", which is the intent of 1.2's
    "shared decorator/guard" instruction.

    Args:
        raw: The caller's raw, possibly-ungrounded query.
        empty_factory: Zero-arg callable that builds the "no results"
            response object this client's ``search()`` should return
            when the query is dropped (e.g. ``lambda: JinaSearchResponse(
            query=raw, results=[], total=0)``).
        subject: Optional explicit subject override (see ``ground_query``).
        geography: Optional explicit geography override.
        logger: The calling module's logger. Defaults to this module's
            logger if omitted, but callers should pass their own so log
            lines carry the right module name.
        tool_name: Short label used in log messages (e.g. "Jina", "SearxNG",
            "Stealth").
        drop_geography: fix 1.5 — forwarded to :func:`ground_query`; see its
            docstring. Lets a caller doing a low-yield-reformulation retry
            broaden the search by dropping the geography anchor.

    Returns:
        ``(grounded_query, None)`` when grounding produced a usable query —
        the caller should proceed with ``grounded_query``.
        ``("", empty_response)`` when the query was contentless and dropped —
        the caller should ``return empty_response`` immediately.
    """
    log = logger or _module_logger
    original = raw
    grounded = ground_query(
        raw, subject=subject, geography=geography, drop_geography=drop_geography
    )
    if not grounded:
        log.warning(
            "Dropping contentless %s query (no subject after grounding): %r",
            tool_name,
            (original or "")[:120],
        )
        return "", empty_factory()
    if grounded != original:
        log.info(
            "Grounded %s query: %r -> %r", tool_name, (original or "")[:100], grounded[:100]
        )
    return grounded, None


def _extract_intent(raw: str) -> str:
    """Keep the meaningful intent words from a failed template query.

    "  carbon footprint emissions data" → "carbon footprint emissions data",
    so grounding yields "<subject> carbon footprint emissions data" rather
    than discarding the analyst's intent entirely.
    """
    if not raw:
        return ""
    s = _strip_debris(raw)
    words = re.findall(r"[A-Za-z][A-Za-z0-9\-]+", s)
    keep = [w for w in words if len(w) > 2 and w.lower() not in _INTERNAL_TOKENS]
    return " ".join(keep[:8])
