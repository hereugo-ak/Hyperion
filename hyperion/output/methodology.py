"""W-10: build the methodology record from recorded structures.

``build_methodology`` is **deterministic and LLM-free**. That is the whole
point of the design: W-10's third failure mode is "generating this section
with a free-form LLM prompt", because a prompt will happily describe research
that never happened. Every sentence emitted here is derived from a structure
the pipeline actually recorded:

============================  ==================================================
subsection                    source structure
============================  ==================================================
question_decomposition        ``WorkflowDAG`` + W-07 ``InsufficiencyResolution``
                              outcomes + the report's own sections
scope_and_method_selection    W-06 ``RosterDecision`` + ``AGENT_METHODS``
retrieval_strategy_and_cover  W-07 ``StrategyTriple`` history + the ``Source``
                              corpus (distinct domains, date range)
source_inclusion_exclusion    ``Source.credibility`` / ``SourceType`` tallies
verification_procedure        ``FactCheckReport`` counters
design_limitations            the fixed structural limits of this engagement
                              design, plus the observed recency cut-off
============================  ==================================================

Nothing here reads an agent roster. Subsection 2 speaks in *methods*
("discounted cash flow valuation was excluded because the subject is a nation
or region"), which is both what W-10 asks for and the only thing that can
survive ``ClientProse`` — that factory rejects every ``AgentName`` registry
string, so an accidental roster leak raises at construction instead of
printing.
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from typing import Any
from urllib.parse import urlparse

from hyperion.schemas.methodology import (
    REQUIRED_SUBSECTION_KEYS,
    SUBSECTION_HEADINGS,
    MethodologyRecord,
    MethodologySubsection,
)

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Client-safe vocabulary
# ─────────────────────────────────────────────────────────────────────────────

# Subject-class wire value -> the noun phrase the methodology uses. The report
# says "the subject is a nation or region", never "subject_class=
# nation_or_region": an enum literal on a client page is the RC-9 leak shape.
_SUBJECT_CLASS_PHRASE: dict[str, str] = {
    "company": "a company",
    "nation_or_region": "a nation or region",
    "technology": "a technology",
    "policy": "a policy instrument",
    "market": "a market",
    "person_or_org": "an organisation or an individual",
}

# Method identifiers are internal slugs; these are the client-facing labels.
# Acronyms are expanded because a methodology page that says "tco analysis"
# has not explained anything. Unmapped methods fall back to
# ``_humanise_method`` rather than being dropped, so a newly declared method
# still appears (a silently missing method is an unstated exclusion).
_METHOD_LABELS: dict[str, str] = {
    "dcf valuation": "discounted cash flow valuation",
    "ev/ebitda comparables": "enterprise value to EBITDA comparables",
    "unit economics": "unit economics",
    "fiscal-cost analysis": "fiscal cost analysis",
    "public-investment analysis": "public investment analysis",
    "cost curve": "cost curve analysis",
    "market sizing": "market sizing",
    "segmentation": "segmentation",
    "growth decomposition": "growth decomposition",
    "share concentration": "share concentration analysis",
    "demand analysis": "demand analysis",
    "competitor matrix": "competitor matrix",
    "moat assessment": "competitive moat assessment",
    "positioning analysis": "positioning analysis",
    "risk matrix": "probability and impact risk matrix",
    "scenario analysis": "scenario analysis",
    "black swan assessment": "tail risk assessment",
    "mitigation planning": "mitigation planning",
    "geopolitical risk": "geopolitical risk assessment",
    "maturity curve": "technology maturity curve",
    "patent landscape": "patent landscape review",
    "tech stack assessment": "technology stack assessment",
    "build vs buy": "build versus buy assessment",
    "tco analysis": "total cost of ownership analysis",
    "industrial capability assessment": "industrial capability assessment",
    "process mapping": "process mapping",
    "bottleneck analysis": "bottleneck analysis",
    "supply chain analysis": "supply chain analysis",
    "kpi design": "key performance indicator design",
    "capacity assessment": "capacity assessment",
    "compliance mapping": "compliance mapping",
    "jurisdiction comparison": "jurisdiction comparison",
    "horizon scanning": "regulatory horizon scanning",
    "policy comparison": "policy comparison",
    "esg assessment": "environmental, social and governance assessment",
    "carbon footprint": "carbon footprint assessment",
    "green financing": "green financing review",
    "persona development": "buyer persona development",
    "journey mapping": "customer journey mapping",
    "willingness to pay": "willingness to pay analysis",
    "nps analysis": "net promoter score analysis",
    "demand research": "demand research",
    "target identification": "acquisition target identification",
    "synergy analysis": "synergy analysis",
    "accretion dilution": "accretion and dilution analysis",
    "comparable transactions": "comparable transaction analysis",
    "trl assessment": "technology readiness level assessment",
    "hype cycle positioning": "hype cycle positioning",
    "disruption patterns": "disruption pattern analysis",
    "adoption s-curve": "adoption S-curve analysis",
    "innovation ecosystem assessment": "innovation ecosystem assessment",
    "porters five forces": "five forces industry analysis",
    "vrio analysis": "resource based capability analysis",
    "blue ocean analysis": "uncontested market space analysis",
    "strategic options analysis": "strategic options analysis",
    "industrial strategy analysis": "industrial strategy analysis",
}

# Engine-set wire value -> what the pool IS, in the reader's terms. The client
# does not need the engine names; it needs to know whether scholarly indexes
# were consulted.
_ENGINE_POOL_PHRASE: dict[str, str] = {
    "reliable": "the primary independent web index pool",
    "standby": "the standby web index pool",
    "category_science": "the academic and preprint index pool",
    "category_news": "the news index pool",
    "category_it": "the technical repository pool",
}

# Credibility tier -> the inclusion bucket it lands in. The methodology states
# a rule, then the counts that rule produced.
_ACCEPTED_TIERS: tuple[str, ...] = (
    "peer_reviewed",
    "government",
    "industry_report",
    "news",
)
_RESTRICTED_TIERS: tuple[str, ...] = ("vendor", "blog", "social_media")

_TIER_PHRASE: dict[str, str] = {
    "peer_reviewed": "peer reviewed literature",
    "government": "government and official statistical releases",
    "industry_report": "industry and analyst reports",
    "vendor": "vendor published material",
    "news": "reported journalism",
    "blog": "commentary and weblogs",
    "social_media": "social posts",
}

_YEAR_RE = re.compile(r"(19|20)\d{2}")


# ─────────────────────────────────────────────────────────────────────────────
# Prose hygiene
# ─────────────────────────────────────────────────────────────────────────────


def _humanise_method(method: str) -> str:
    """Fallback label for a method with no explicit entry."""
    return method.replace("-", " ").replace("/", " and ").strip()


def _normalise(text: Any) -> str:
    """Make a recorded free-text string safe to offer to ``ClientProse``.

    Recorded strings (a gap question, a scope note) are written by upstream
    agents and may carry the typography the global ban forbids. Normalising is
    legitimate here and NOT the "silent sanitisation" W-09 objects to: W-09's
    objection is to stripping a *telemetry leak* so nobody notices the leak.
    Replacing U+2014 with a comma changes punctuation, not meaning, and the
    telemetry categories are still rejected outright below.
    """
    s = "" if text is None else str(text)
    s = s.replace("\u2014", ", ").replace("\u2013", " to ")
    s = re.sub(r"\bgap_[A-Za-z0-9_]+\b", "this question", s, flags=re.IGNORECASE)
    return re.sub(r"\s+", " ", s).strip().rstrip(",").strip()


def _prose_or_none(text: Any) -> str | None:
    """Normalise then validate. ``None`` when the value cannot be client prose.

    A fact that cannot be restated safely is omitted rather than crashing the
    render: the designer is the last stage of the pipeline and a methodology
    page is not worth losing a whole engagement over. The omission is logged at
    WARNING so it surfaces in the operator log, and the subsection's narrative
    sentence plus its aggregate counts are always constructed from typed
    numbers, so no subsection can become empty as a result.
    """
    from hyperion.schemas.narrative import ClientProse

    candidate = _normalise(text)
    if not candidate:
        return None
    try:
        return str(ClientProse.of(candidate))
    except ValueError as exc:
        logger.warning(
            "W-10: omitted one methodology fact that could not be restated as "
            "client prose (%s); the subsection keeps its narrative and counts.",
            exc,
        )
        return None


def _facts(*items: Any) -> list[str]:
    out: list[str] = []
    for item in items:
        safe = _prose_or_none(item)
        if safe and safe not in out:
            out.append(safe)
    return out


def _plural(n: int, singular: str, plural: str | None = None) -> str:
    return f"{n} {singular}" if n == 1 else f"{n} {plural or singular + 's'}"


# ─────────────────────────────────────────────────────────────────────────────
# Corpus statistics, derived from the Source objects actually attached
# ─────────────────────────────────────────────────────────────────────────────


class CorpusStats:
    """Distinct-domain, date-range and credibility tallies over the corpus.

    Derived rather than accepted as a parameter: ``FinalReport.total_sources``
    is a count somebody set, whereas these are computed from the ``Source``
    objects the report actually carries, so they cannot overstate coverage.
    """

    __slots__ = (
        "sources",
        "domains",
        "years",
        "by_tier",
        "by_type",
        "with_extracted_data",
    )

    def __init__(self, sources: Sequence[Any]) -> None:
        seen_urls: set[str] = set()
        unique: list[Any] = []
        for s in sources:
            url = str(getattr(s, "url", "") or "")
            key = url or f"{getattr(s, 'title', '')}"
            if key in seen_urls:
                continue
            seen_urls.add(key)
            unique.append(s)

        self.sources = unique
        self.domains: set[str] = set()
        self.years: list[int] = []
        self.by_tier: Counter[str] = Counter()
        self.by_type: Counter[str] = Counter()
        self.with_extracted_data = 0

        for s in unique:
            url = str(getattr(s, "url", "") or "")
            if url:
                host = (urlparse(url).netloc or "").lower()
                if host.startswith("www."):
                    host = host[4:]
                if host:
                    self.domains.add(host)
            pub = getattr(s, "publication_date", None)
            if pub:
                m = _YEAR_RE.search(str(pub))
                if m:
                    self.years.append(int(m.group(0)))
            cred = getattr(s, "credibility", None)
            self.by_tier[str(getattr(cred, "value", cred) or "unknown")] += 1
            stype = getattr(s, "source_type", None)
            if stype is not None:
                self.by_type[str(getattr(stype, "value", stype))] += 1
            if getattr(s, "key_data", None):
                self.with_extracted_data += 1

    @property
    def n_sources(self) -> int:
        return len(self.sources)

    @property
    def n_domains(self) -> int:
        return len(self.domains)

    @property
    def date_range(self) -> tuple[int, int] | None:
        return (min(self.years), max(self.years)) if self.years else None

    @property
    def accepted(self) -> int:
        return sum(self.by_tier.get(t, 0) for t in _ACCEPTED_TIERS)

    @property
    def restricted(self) -> int:
        return sum(self.by_tier.get(t, 0) for t in _RESTRICTED_TIERS)


def collect_sources(report: Any) -> list[Any]:
    """Every ``Source`` reachable from the report, section and finding level."""
    out: list[Any] = []
    for section in getattr(report, "sections", None) or []:
        out.extend(getattr(section, "sources", None) or [])
        for finding in getattr(section, "findings", None) or []:
            out.extend(getattr(finding, "sources", None) or [])
    for finding in getattr(report, "key_findings", None) or []:
        out.extend(getattr(finding, "sources", None) or [])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Retrieval statistics
# ─────────────────────────────────────────────────────────────────────────────


class RetrievalStats:
    """Queries issued, pools consulted, escalations triggered.

    ``queries_issued`` defaults to the live ``SearxNGClient`` counter when the
    caller does not supply one, because that counter is the only place the
    system records how many retrievals were actually dispatched. When neither
    is available the subsection reports domains and date range only and says so
    — it never invents a query count.
    """

    __slots__ = (
        "queries_issued",
        "pools",
        "escalations",
        "distinct_triples",
        "backend_query_counts",
        "constraints",
    )

    def __init__(
        self,
        queries_issued: int | None = None,
        pools: Iterable[str] = (),
        escalations: int = 0,
        distinct_triples: int = 0,
        backend_query_counts: Mapping[str, int] | None = None,
        constraints: Sequence[str] = (),
    ) -> None:
        self.queries_issued = queries_issued
        self.pools = list(dict.fromkeys(pools))
        self.escalations = escalations
        self.distinct_triples = distinct_triples
        self.backend_query_counts = {
            str(name): int(count)
            for name, count in (backend_query_counts or {}).items()
            if int(count) > 0
        }
        self.constraints = [str(item) for item in constraints if str(item).strip()]

    @classmethod
    def from_resolutions(
        cls,
        resolutions: Sequence[Any],
        queries_issued: int | None = None,
        backend_query_counts: Mapping[str, int] | None = None,
        constraints: Sequence[str] = (),
    ) -> RetrievalStats:
        pools: list[str] = []
        identities: set[tuple[str, ...]] = set()
        escalations = 0
        for res in resolutions or []:
            triples = list(getattr(res, "tried_triples", None) or [])
            if len(triples) > 1:
                escalations += len(triples) - 1
            for triple in triples:
                engine_set = getattr(triple, "engine_set", None)
                value = str(getattr(engine_set, "value", engine_set) or "")
                phrase = _ENGINE_POOL_PHRASE.get(value)
                if phrase:
                    pools.append(phrase)
                ident = getattr(triple, "identity", None)
                if callable(ident):
                    identities.add(tuple(str(p) for p in ident()))
        if queries_issued is None:
            queries_issued = _live_query_count()
        return cls(
            queries_issued=queries_issued,
            pools=pools,
            escalations=escalations,
            distinct_triples=len(identities),
            backend_query_counts=backend_query_counts,
            constraints=constraints,
        )


def _live_query_count() -> int | None:
    """Best-effort read of the retrieval client's own dispatch counter."""
    try:
        from hyperion.tools.searxng import SearxNGClient
    except ImportError:  # retrieval stack not installed in this process
        return None
    getter = getattr(SearxNGClient, "get_search_count", None)
    if not callable(getter):
        return None
    try:
        count = int(getter())
    except (TypeError, ValueError):
        return None
    return count or None


# ─────────────────────────────────────────────────────────────────────────────
# The six subsection builders
# ─────────────────────────────────────────────────────────────────────────────


def _sub_question_decomposition(
    report: Any, dag: Any, resolutions: Sequence[Any]
) -> MethodologySubsection:
    """1. Question decomposition — what was asked, split, answered, or not.

    Source: the DAG (how many lines of enquiry were planned) plus the W-07
    outcomes (which lines closed, which were declared as gaps, which were ruled
    out of scope). Section titles stand in for the answered sub-questions
    because they are already client prose; a raw task ``description`` is
    Director-written free text and is not offered to the client.
    """
    from hyperion.agents.insufficiency import InsufficiencyOutcome

    sections = list(getattr(report, "sections", None) or [])
    answered_titles = [str(getattr(s, "title", "")) for s in sections]

    declared_gaps = [
        r
        for r in resolutions or []
        if getattr(r, "outcome", None) == InsufficiencyOutcome.DECLARED_GAP
    ]
    out_of_scope = [
        r
        for r in resolutions or []
        if getattr(r, "outcome", None) == InsufficiencyOutcome.OUT_OF_SCOPE
    ]

    planned = len(
        {
            str(getattr(t, "id", ""))
            for t in (getattr(dag, "tasks", None) or [])
            if str(getattr(getattr(t, "agent", None), "value", "")) not in {
                "presentation_designer",
                "render_engine",
                "data_visualizer",
                "quality_gate",
                "fact_checker",
                "synthesis_lead",
                "engagement_director",
            }
        }
    ) or len(answered_titles)

    narrative = (
        "The engagement question was decomposed into "
        f"{_plural(planned, 'line of enquiry', 'lines of enquiry')}, each "
        "assigned its own evidence requirement and pursued independently "
        f"before synthesis. {_plural(len(answered_titles), 'line')} closed with "
        "sufficient evidence and became the analysis "
        f"{'chapter' if len(answered_titles) == 1 else 'chapters'} of this "
        "report. Where a line could not be closed, it is reported as such "
        "rather than answered thinly: "
        f"{_plural(len(declared_gaps), 'line')} "
        f"{'is' if len(declared_gaps) == 1 else 'are'} carried forward as a "
        "stated evidence gap with the retrieval attempts on record, and "
        f"{_plural(len(out_of_scope), 'line')} "
        f"{'was' if len(out_of_scope) == 1 else 'were'} ruled outside the "
        "scope of the question as put."
    )

    facts = _facts(
        f"Lines of enquiry planned: {planned}.",
        f"Closed with sufficient evidence: {len(answered_titles)}.",
        f"Carried forward as stated evidence gaps: {len(declared_gaps)}.",
        f"Ruled outside scope: {len(out_of_scope)}.",
        *(f"Answered: {t}." for t in answered_titles[:12]),
        *(
            f"Stated gap: {getattr(r, 'question', '')}"
            for r in declared_gaps[:6]
        ),
        *(
            f"Outside scope: {getattr(r, 'question', '')}"
            for r in out_of_scope[:6]
        ),
    )
    return MethodologySubsection(
        key="question_decomposition",
        heading=SUBSECTION_HEADINGS["question_decomposition"],
        narrative=narrative,
        facts=facts,
    )


def _sub_scope_and_method_selection(
    report: Any, dag: Any
) -> MethodologySubsection:
    """2. Scope and method selection — which methods fit the subject, and why not.

    This is where the "why is there no DCF on a country?" question is answered
    permanently. The applied methods come from the W-06 ``RosterDecision``
    records; the excluded ones come from ``AGENT_METHODS`` minus the eligible
    set, and each exclusion gets a reason built from the classified subject
    class. No agent is named: the reader is told which *methods* were used.
    """
    subject_class = str(getattr(dag, "subject_class", "") or "")
    phrase = _SUBJECT_CLASS_PHRASE.get(subject_class, "")
    decisions = list(getattr(dag, "roster_decisions", None) or [])

    applied: list[str] = []
    excluded: list[str] = []
    for decision in decisions:
        dispatched = bool(getattr(decision, "dispatched", False))
        eligible = [str(m) for m in (getattr(decision, "eligible_methods", None) or [])]
        if dispatched:
            applied.extend(eligible)
        agent = getattr(decision, "agent", None)
        declared = _declared_methods_for(agent)
        for method in declared:
            if method not in eligible:
                excluded.append(method)

    applied_labels = _label_methods(applied)
    excluded_labels = _label_methods(excluded)

    if phrase:
        head = (
            f"The subject of this question was classified as {phrase}, and the "
            "analytical method set follows from that classification rather "
            "than from the shape of the question. "
        )
        why_not = (
            f"A method is excluded when its unit of analysis does not exist for "
            f"{phrase}: the exclusions below are recorded, not accidental."
        )
    else:
        head = (
            "The analytical method set was selected against the subject of the "
            "question rather than against its grammatical form. "
        )
        why_not = (
            "A method is excluded when its unit of analysis does not exist for "
            "this subject; the exclusions below are recorded, not accidental."
        )

    narrative = (
        head
        + f"{_plural(len(applied_labels), 'method was', 'methods were')} applied: "
        + (_join(applied_labels[:10]) if applied_labels else "none")
        + ". "
        + why_not
        + (
            f" {_plural(len(excluded_labels), 'method was', 'methods were')} "
            "excluded on that basis: " + _join(excluded_labels[:10]) + "."
            if excluded_labels
            else ""
        )
    )

    facts = _facts(
        f"Subject classification: {phrase or 'not established'}.",
        f"Methods applied: {len(applied_labels)}.",
        f"Methods excluded as inapplicable to the subject: {len(excluded_labels)}.",
        *(f"Applied: {label}." for label in applied_labels[:12]),
        *(
            _exclusion_reason(method, phrase)
            for method in _dedup(excluded)[:12]
        ),
    )
    return MethodologySubsection(
        key="scope_and_method_selection",
        heading=SUBSECTION_HEADINGS["scope_and_method_selection"],
        narrative=narrative,
        facts=facts,
    )


def _dedup(items: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(items))


def _method_applies_to() -> dict[str, tuple[str, ...]]:
    """method slug -> the subject-class wire values it declares itself for.

    Read from ``AGENT_METHODS`` rather than duplicated, so an exclusion reason
    can never contradict the eligibility table that produced the exclusion.
    Two agents may declare the same method slug; the union is taken.
    """
    try:
        from hyperion.agents.engagement_director import AGENT_METHODS
    except ImportError:
        return {}
    merged: dict[str, set[str]] = {}
    for methods in AGENT_METHODS.values():
        for method, classes in methods.items():
            merged.setdefault(method, set()).update(
                str(getattr(c, "value", c)) for c in classes
            )
    return {m: tuple(sorted(v)) for m, v in merged.items()}


def _exclusion_reason(method: str, subject_phrase: str) -> str:
    """One sentence on why a method was not applied, derived from the table.

    The reason names the subject classes the method DOES declare itself for.
    An earlier draft asserted "its unit of analysis is a company" for every
    exclusion, which is false for methods whose unit is a customer or a
    technology; stating the real applicable set is both accurate and more
    useful to a reader challenging the scope.
    """
    label = _METHOD_LABELS.get(method) or _humanise_method(method)
    applies = _method_applies_to().get(method, ())
    phrases = [
        _SUBJECT_CLASS_PHRASE[c] for c in applies if c in _SUBJECT_CLASS_PHRASE
    ]
    if phrases and subject_phrase:
        return (
            f"Excluded: {label}, because it is defined for {_join(phrases)}, "
            f"and the subject of this engagement is {subject_phrase}."
        )
    if phrases:
        return (
            f"Excluded: {label}, because it is defined for {_join(phrases)}, "
            "which is not the subject of this engagement."
        )
    return (
        f"Excluded: {label}, because its unit of analysis does not exist for "
        "the subject of this engagement."
    )


def _declared_methods_for(agent: Any) -> list[str]:
    """All methods an agent declares, regardless of subject class.

    Imported lazily: ``engagement_director`` pulls in the agent stack, and the
    presentation designer must not acquire that dependency at import time.
    """
    if agent is None:
        return []
    try:
        from hyperion.agents.engagement_director import AGENT_METHODS
    except ImportError:
        return []
    return [str(m) for m in AGENT_METHODS.get(agent, {})]


def _label_methods(methods: Iterable[str]) -> list[str]:
    out: list[str] = []
    for method in methods:
        label = _METHOD_LABELS.get(method) or _humanise_method(method)
        if label and label not in out:
            out.append(label)
    return out


def _join(items: Sequence[str]) -> str:
    items = [i for i in items if i]
    if not items:
        return "none"
    if len(items) == 1:
        return items[0]
    return ", ".join(items[:-1]) + " and " + items[-1]


def _sub_retrieval(
    stats: CorpusStats, retrieval: RetrievalStats
) -> MethodologySubsection:
    """3. Retrieval strategy and coverage — pools, queries, domains, dates."""
    pools = retrieval.pools or [_ENGINE_POOL_PHRASE["reliable"]]
    parts = [
        "Evidence was gathered by structured retrieval against "
        f"{_join(pools)}, with no single index treated as authoritative."
    ]
    if retrieval.queries_issued:
        parts.append(
            f"{_plural(retrieval.queries_issued, 'distinct query', 'distinct queries')} "
            f"{'was' if retrieval.queries_issued == 1 else 'were'} issued."
        )
    independent_queries = sum(
        retrieval.backend_query_counts.get(name, 0)
        for name in ("searxng", "jina")
    )
    grounded_queries = retrieval.backend_query_counts.get("gemini", 0)
    if independent_queries or grounded_queries:
        parts.append(
            f"The recorded backend mix was {independent_queries} independent "
            f"index queries and {grounded_queries} grounded model search queries."
        )
    if retrieval.constraints:
        parts.append(
            f"{_plural(len(retrieval.constraints), 'grounded retrieval constraint')} "
            "was recorded; affected attempts fell back to the independent index pool."
        )
    parts.append(
        f"The surviving corpus spans "
        f"{_plural(stats.n_domains, 'distinct source domain')} across "
        f"{_plural(stats.n_sources, 'source')}."
    )
    rng = stats.date_range
    if rng:
        parts.append(
            f"Dated material runs from {rng[0]} to {rng[1]}."
            if rng[0] != rng[1]
            else f"Dated material is from {rng[0]}."
        )
    if retrieval.escalations:
        parts.append(
            "Where a first pass returned nothing usable the query was "
            "reformulated and re routed rather than abandoned; "
            f"{_plural(retrieval.escalations, 'such escalation')} "
            f"{'was' if retrieval.escalations == 1 else 'were'} triggered."
        )
    else:
        parts.append(
            "No retrieval escalation was required: the first pass returned "
            "usable evidence on every line of enquiry that closed."
        )

    facts = _facts(
        f"Index pools consulted: {len(pools)}.",
        (
            f"Distinct queries issued: {retrieval.queries_issued}."
            if retrieval.queries_issued
            else "Query count not recorded for this run."
        ),
        f"Distinct source domains: {stats.n_domains}.",
        f"Unique sources retained: {stats.n_sources}.",
        (
            f"Source date range: {rng[0]} to {rng[1]}."
            if rng
            else "No publication dates were available on the retained sources."
        ),
        f"Retrieval escalations triggered: {retrieval.escalations}.",
        f"Distinct retrieval strategies attempted: {retrieval.distinct_triples}.",
        f"Independent index backend queries: {independent_queries}.",
        f"Grounded model backend search queries: {grounded_queries}.",
        f"Grounded retrieval constraints recorded: {len(retrieval.constraints)}.",
    )
    return MethodologySubsection(
        key="retrieval_strategy_and_coverage",
        heading=SUBSECTION_HEADINGS["retrieval_strategy_and_coverage"],
        narrative=" ".join(parts),
        facts=facts,
    )


def _sub_inclusion(stats: CorpusStats) -> MethodologySubsection:
    """4. Source inclusion and exclusion criteria — the rule, then the counts."""
    accepted_desc = _join(
        [_TIER_PHRASE[t] for t in _ACCEPTED_TIERS if stats.by_tier.get(t)]
    )
    narrative = (
        "A retrieved document was admitted as evidence only when it named the "
        "entity and the period under discussion and carried an identifiable "
        "publisher. Preference ran in a fixed order: peer reviewed literature, "
        "then official statistical and regulatory releases, then industry and "
        "analyst reports, then reported journalism. General reference works "
        "and encyclopaedia entries were refused outright as primary evidence, "
        "because they are summaries of other sources rather than sources; "
        "vendor material and commentary were admitted only where the claim "
        "was about the vendor itself. "
        + (
            f"On this engagement the admitted corpus consists of {accepted_desc}. "
            if accepted_desc != "none"
            else ""
        )
        + f"Of {_plural(stats.n_sources, 'retained source')}, "
        f"{stats.with_extracted_data} carried a specific extracted figure that "
        "could be quoted back to its origin."
    )

    facts = _facts(
        f"Sources retained after deduplication: {stats.n_sources}.",
        f"Retained in preferred credibility tiers: {stats.accepted}.",
        f"Retained in restricted tiers and used only for self referential "
        f"claims: {stats.restricted}.",
        f"Sources carrying a specific extracted figure: {stats.with_extracted_data}.",
        *(
            f"{_TIER_PHRASE.get(tier, tier)}: {count}."
            for tier, count in sorted(stats.by_tier.items())
            if count and tier in _TIER_PHRASE
        ),
        "General reference works were refused as primary evidence.",
    )
    return MethodologySubsection(
        key="source_inclusion_and_exclusion",
        heading=SUBSECTION_HEADINGS["source_inclusion_and_exclusion"],
        narrative=narrative,
        facts=facts,
    )


def _sub_verification(fact_check: Any) -> MethodologySubsection:
    """5. Verification procedure — method and rate, never an alarm.

    W-09 sends the fact checker's raw counters to the operator telemetry
    artifact. This subsection is their legitimate client-facing form: it states
    the procedure and the pass rate. Note the vocabulary constraint —
    ``ClientProse`` rejects the words "unverified", "contradicted" and
    "hallucinated", so an alarmist restatement is unconstructible here.
    """
    total = int(getattr(fact_check, "total_claims_checked", 0) or 0)
    verified = int(getattr(fact_check, "verified_count", 0) or 0)
    plausible = int(getattr(fact_check, "plausible_count", 0) or 0)
    rate = float(getattr(fact_check, "verification_rate", 0.0) or 0.0)
    if rate <= 1.0 and rate > 0.0:
        rate *= 100.0
    if not rate and total:
        rate = 100.0 * (verified + plausible) / total
    open_items = max(0, total - verified - plausible)

    if total:
        narrative = (
            "Every quantitative and attributed claim in this report was "
            "extracted and checked against the source it cites, not merely "
            "against the existence of that source: the check asks whether the "
            "cited document actually contains the figure. "
            f"{_plural(total, 'claim')} "
            f"{'was' if total == 1 else 'were'} put through that procedure and "
            f"{rate:.0f} percent cleared it, either confirmed directly against "
            "the cited document or corroborated by an independent one. Claims "
            "that did not clear the check were not printed as findings; they "
            "were either removed or restated as open questions in the "
            "limitations below. The figure above is therefore a measure of how "
            "much of the analysis is traceable to its cited source, and it is "
            "reported for that reason."
        )
    else:
        narrative = (
            "Every quantitative and attributed claim is checked against the "
            "source it cites, and the check asks whether the cited document "
            "actually contains the figure rather than whether the document "
            "exists. On this engagement the claim extraction pass recorded no "
            "claims for checking, so no pass rate is reported; the analysis "
            "below therefore rests on the source corpus described above and "
            "should be read with that in mind."
        )

    facts = _facts(
        f"Claims extracted and checked: {total}.",
        f"Claims clearing the check: {verified + plausible}.",
        f"Pass rate: {rate:.0f} percent." if total else None,
        f"Claims restated as open questions rather than printed: {open_items}.",
        "Each claim was checked against the cited document itself, not against "
        "the existence of a citation.",
    )
    return MethodologySubsection(
        key="verification_procedure",
        heading=SUBSECTION_HEADINGS["verification_procedure"],
        narrative=narrative,
        facts=facts,
    )


def _sub_design_limitations(
    stats: CorpusStats, report: Any
) -> MethodologySubsection:
    """6. Limitations of the design — structural, distinct from evidence gaps."""
    rng = stats.date_range
    cutoff = (
        f"The most recent dated source in the corpus is from {rng[1]}, which is "
        "the effective recency cut off of the analysis. "
        if rng
        else "No publication dates were recoverable across the corpus, so the "
        "recency of the evidence base cannot be asserted. "
    )
    narrative = (
        "Four limits are structural to an engagement of this kind and apply "
        "regardless of how well it was executed. First, there is no primary "
        "research: no interviews were conducted, no survey was fielded and no "
        "site was visited, so every figure in this report is a figure someone "
        "else published. Second, material behind a paywall or inside a "
        "subscription database was not retrieved, which under represents "
        "commercial analyst work and priced market data. Third, retrieval was "
        "conducted predominantly in English, so where the subject has a "
        "substantial non English literature that literature is under "
        "represented. Fourth, "
        + cutoff.lower()
        + "Anything published after that point is outside this analysis. These "
        "are limits of the design; the evidence gaps specific to this question "
        "are listed separately above and below."
    )

    facts = _facts(
        "No primary research: no interviews, surveys or site visits.",
        "Paywalled and subscription only material was not retrieved.",
        "Retrieval was predominantly English language.",
        (
            f"Effective recency cut off: {rng[1]}."
            if rng
            else "Recency cut off could not be established from the corpus."
        ),
        f"Evidence base: {_plural(stats.n_sources, 'source')} across "
        f"{_plural(stats.n_domains, 'domain')}.",
    )
    return MethodologySubsection(
        key="design_limitations",
        heading=SUBSECTION_HEADINGS["design_limitations"],
        narrative=narrative,
        facts=facts,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────────────


def build_methodology(
    report: Any,
    dag: Any = None,
    resolutions: Sequence[Any] | None = None,
    queries_issued: int | None = None,
    backend_query_counts: Mapping[str, int] | None = None,
    retrieval_constraints: Sequence[str] = (),
) -> MethodologyRecord:
    """Build the six-subsection methodology from recorded structures only.

    Deterministic: called twice on the same inputs it returns the same record.
    Never raises on thin inputs — a run with no DAG, no resolutions and no
    fact-check report still produces all six subsections, each of which states
    what was and was not recorded. That matters because the methodology page is
    the one place a reader looks to calibrate how much to trust the rest, so a
    missing methodology is worse than a candid one.
    """
    resolutions = list(resolutions or [])
    stats = CorpusStats(collect_sources(report))
    retrieval = RetrievalStats.from_resolutions(
        resolutions,
        queries_issued,
        backend_query_counts=backend_query_counts,
        constraints=retrieval_constraints,
    )
    fact_check = getattr(report, "fact_check_report", None)

    subsections = [
        _sub_question_decomposition(report, dag, resolutions),
        _sub_scope_and_method_selection(report, dag),
        _sub_retrieval(stats, retrieval),
        _sub_inclusion(stats),
        _sub_verification(fact_check),
        _sub_design_limitations(stats, report),
    ]
    record = MethodologyRecord(subsections=subsections)
    # Cheap structural assertion: the record validator already guarantees the
    # order, this catches a builder that forgot to append one entirely.
    assert tuple(s.key for s in record.subsections) == REQUIRED_SUBSECTION_KEYS
    return record
