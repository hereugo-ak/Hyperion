"""HYPERION — deterministic chart specification mining.

WHY THIS MODULE EXISTS
----------------------
Every HYPERION report shipped with **zero charts**. The cause was a silent
contract gap, not a rendering bug: the orchestrator asked

    if final_report and hasattr(final_report, "chart_specifications"):
        chart_specs = final_report.chart_specifications or []

but `FinalReport` never declared a `chart_specifications` field, so `hasattr`
was always False, `chart_specs` was always `[]`, and the Data Visualizer
returned early with "No chart specifications received". The entire
Plotly → 300 DPI → Pillow pipeline was fully implemented and never once
invoked with real data.

Declaring the field is half the fix. The other half is *producing* specs.
Relying on the Synthesis Lead's LLM to emit well-formed chart JSON is fragile
— that is exactly the kind of soft contract that failed silently before. So
this module mines chart-ready series **deterministically** from the numbers
the agents already found and cited, with no extra LLM calls.

DESIGN PRINCIPLES
-----------------
1. **Never invent data.** Every value plotted is a number that appeared in an
   agent's finding text. A fabricated chart in a consulting deliverable is far
   worse than a missing one — it looks authoritative and is unfalsifiable.
2. **Question-derived, not templated.** Titles and axis labels come from the
   findings and the user's question. No hardcoded "Market Size 2024" scaffolds.
3. **Degrade quietly.** If nothing numeric is minable, return `[]` and let the
   report be text-only rather than emitting an empty-axis chart.
4. **Cite everything.** Each spec carries the source citation of the finding
   it was mined from, so the chart footnote is real.
"""

from __future__ import annotations

import re
from typing import Any

__all__ = ["mine_chart_specs"]


# ── Number extraction ────────────────────────────────────────────────────────
#
# Matches the shapes analysts actually write:
#   $2.4B  |  12.5%  |  1,250 units  |  USD 340 million  |  ₹4.2 lakh crore
_MAGNITUDES: dict[str, float] = {
    "trillion": 1e12, "tn": 1e12, "t": 1e12,
    "billion": 1e9, "bn": 1e9, "b": 1e9,
    "million": 1e6, "mn": 1e6, "m": 1e6,
    "crore": 1e7,          # Indian numbering — 10 million
    "lakh": 1e5,           # Indian numbering — 100 thousand
    "thousand": 1e3, "k": 1e3,
}

# BUG HISTORY. _MAGNITUDES has always defined bare "b" and "m", but the
# magnitude alternation below used to read
#     (?P<magnitude>trillion|billion|million|thousand|crore|lakh|tn|bn|mn|k)?
# — no bare b, m or t. So the single most common way an analyst writes a
# business number, "$2.4B", matched with magnitude=None and _parse_number
# returned (2.4, "USD"): a $2.4 billion market plotted as a bar of height 2.4
# next to a "$88.9 billion" bar of height 88,900,000,000. Mixing the two
# spellings in one finding mis-scaled the chart by a factor of 10^9 and made
# the abbreviated series visually vanish. Verified before the fix:
#     _parse_number("$2.4B")  -> (2.4, 'USD')
#     _parse_number("$5M")    -> (5.0, 'USD')
#     _parse_number("$1.2T")  -> (1.2, 'USD')
#
# The single-letter forms are matched CASE-SENSITIVELY as uppercase, or as
# lowercase only when a currency symbol precedes them. That distinction is
# load-bearing:
#   "45 t CO2e"   -> lowercase "t" is the SI tonne, a unit, not 45 trillion.
#   "12 m of pipe"-> lowercase "m" is metres.
#   "$5m"         -> lowercase, but the "$" proves it is a money magnitude.
# Multi-letter aliases (billion, bn, k…) stay case-insensitive because none of
# them collide with a unit symbol. The negative lookahead stops "5 bn" from
# also matching the "b" branch inside a longer word like "5 bps".
_NUM_RE = re.compile(
    r"""
    (?P<currency>[$€£¥₹]|USD|EUR|GBP|INR|JPY)?\s*
    (?P<value>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)
    \s*
    (?P<magnitude>
        trillion|billion|million|thousand|crore|lakh|tn|bn|mn|k
      | (?-i:[TBM])                       # uppercase single letter: $2.4B, 5M
      | (?(currency)(?-i:[tbm]))          # lowercase only after a currency
    )?
    (?![A-Za-z])
    \s*
    (?P<percent>%|percent|percentage\s+points?|pp)?
    """,
    re.IGNORECASE | re.VERBOSE,
)

# Years must not be plotted as magnitudes — "2024" is a label, not a value.
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")

# Words that make a number a *quantity worth charting* rather than an
# incidental reference ("Section 301", "Tier 1", "page 4").
_ORDINAL_NOISE = re.compile(
    r"\b(?:section|article|clause|tier|phase|chapter|page|figure|table|"
    r"rule|act|no\.?|number|part|step|iso|paragraph)\s*$",
    re.IGNORECASE,
)


def _parse_number(text: str, match: re.Match[str]) -> tuple[float, str] | None:
    """Turn a regex match into (scaled_value, unit_label), or None if unusable."""
    raw = match.group("value").replace(",", "")
    try:
        value = float(raw)
    except ValueError:
        return None

    # Reject bare years — they are x-axis labels, not measurements.
    if not match.group("magnitude") and not match.group("percent"):
        if _YEAR_RE.fullmatch(match.group("value")):
            return None
        # Reject ordinals/identifiers ("Section 301", "Tier 1").
        prefix = text[max(0, match.start()) - 20:match.start()]
        if _ORDINAL_NOISE.search(prefix):
            return None

    magnitude = (match.group("magnitude") or "").lower()
    if magnitude:
        value *= _MAGNITUDES.get(magnitude, 1.0)

    if match.group("percent"):
        unit = "%"
    elif match.group("currency"):
        cur = match.group("currency").upper()
        unit = {"$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY", "₹": "INR"}.get(cur, cur)
    elif magnitude:
        unit = "count"
    else:
        unit = ""
    return value, unit


def _extract_numbers(text: str, limit: int = 12) -> list[tuple[float, str, str]]:
    """Extract (value, unit, label) triples from finding prose.

    The label is the nearest preceding noun phrase, so a bar actually says
    what it measures instead of being an anonymous magnitude.
    """
    if not text:
        return []
    out: list[tuple[float, str, str]] = []
    for m in _NUM_RE.finditer(text):
        parsed = _parse_number(text, m)
        if parsed is None:
            continue
        value, unit = parsed
        if value == 0:
            continue

        label = _label_for(text, m, len(out) + 1)
        out.append((value, unit, label))
        if len(out) >= limit:
            break
    return out


def _label_for(text: str, m: re.Match[str], ordinal: int) -> str:
    """Derive an axis label for one number.

    Two rules, both learned from bad output:

    1. **Period labels must follow the number, not precede it.** English writes
       "$67.2 billion in 2022", so searching a window *centred* on the number
       let one value steal the previous clause's year and the next value inherit
       it too — collapsing four data points into three and silently dropping
       $67.2B. We therefore look FORWARD first (the "in <year>" idiom) and only
       then backward, and never reuse a year already consumed.

    2. **Category labels must be the entity, not the sentence.** Taking the six
       preceding words produced axis labels like
       "at followed by display panels at" — unshippable in a client
       deliverable. We instead take the trailing noun phrase, stopping at
       connectives and dropping the trailing preposition/verb.
    """
    tail = text[m.end():m.end() + 40]
    head = text[max(0, m.start() - 70):m.start()]

    # Rule 1: "<value> in 2022" / "<value> (FY24)" — period follows the value.
    fwd = re.match(r"\s*(?:in|during|for|by|as of)?\s*\(?((?:FY\s?)?(?:19|20)\d{2})\b", tail, re.IGNORECASE)
    if fwd:
        return fwd.group(1).replace(" ", "")
    fwd_fy = re.match(r"\s*(?:in|during|for)?\s*\(?(FY\s?\d{2})\b", tail, re.IGNORECASE)
    if fwd_fy:
        return fwd_fy.group(1).replace(" ", "")

    # Backward period reference, e.g. "in 2021 imports reached $55.6 billion".
    back_year = re.findall(r"\b((?:FY\s?)?(?:19|20)\d{2})\b", head)
    if back_year and re.search(r"\b(?:in|during|for|of)\s+$", head[:len(head)].rstrip()[:0] or head, re.IGNORECASE) is None:
        # Only trust a backward year if it is close to the number.
        idx = head.rfind(back_year[-1])
        if idx != -1 and len(head) - idx <= 28:
            return str(back_year[-1]).replace(" ", "")

    # Rule 2: categorical — trailing noun phrase before the number.
    # Cut at the last connective so we keep only this item's own name.
    phrase = re.split(
        r",|;|\band\b|\bfollowed by\b|\bwhile\b|\bwhereas\b|\bversus\b|\bvs\.?\b|\bthen\b|\bbut\b|\.",
        head,
        flags=re.IGNORECASE,
    )[-1]
    # Drop leading filler and the trailing preposition/verb that introduced
    # the number ("... in semiconductors at" -> "semiconductors").
    phrase = re.sub(
        r"^\s*(?:is|are|was|were|the|a|an|of|in|at|to|by|with|highest|lowest|"
        r"about|around|roughly|approximately|nearly|over|under|up|down|"
        r"reached|rose|fell|eased|grew|declined|totalling|totaling|stood)\b\s*",
        "",
        phrase.strip(),
        flags=re.IGNORECASE,
    )
    # Strip the trailing verb phrase that introduced the number. This must
    # handle MULTI-WORD verb phrases, not just single verbs: English reports
    # write "crude oil made up $132 billion" and "electronics accounted for
    # $88 billion", which a single-token strip left as the bar labels
    # "Crude oil made up" and "electronics accounted for". Looping lets a
    # particle/preposition be removed after the verb it attaches to.
    for _ in range(4):
        shortened = re.sub(
            r"\s+(?:is|are|was|were|of|in|at|to|by|with|for|up|from|than|"
            r"reached|rose|fell|eased|grew|declined|stood|hit|represented|"
            r"comprised|contributed|constituted|accounted|made|totalled|"
            r"totaled|totalling|totaling|averaged|remained|reaching)\s*$",
            "",
            phrase.strip(),
            flags=re.IGNORECASE,
        )
        if shortened == phrase.strip():
            break
        phrase = shortened
    phrase = re.sub(r"\s+", " ", phrase).strip(" -–—:,()")

    # Keep it to a short, readable axis tick, then re-strip filler: truncating
    # to the last N words can re-expose a leading verb/preposition
    # ("Dependence is highest in semiconductors" -> "is highest in
    # semiconductors"), which would ship as a bar label.
    words = phrase.split()
    if len(words) > 4:
        phrase = " ".join(words[-4:])
    for _ in range(4):
        stripped = re.sub(
            r"^\s*(?:is|are|was|were|the|a|an|of|in|at|to|by|with|highest|lowest|"
            r"about|around|roughly|approximately|nearly|over|under|up|down|for|"
            r"reached|rose|fell|eased|grew|declined|stood|and|then|also)\b\s*",
            "",
            phrase,
            flags=re.IGNORECASE,
        )
        if stripped == phrase:
            break
        phrase = stripped
    phrase = phrase.strip(" -–—:,()")
    if phrase and len(phrase) >= 3:
        return phrase[:40]
    return f"Value {ordinal}"


def _coherent_group(
    numbers: list[tuple[float, str, str]],
) -> list[tuple[float, str, str]] | None:
    """Keep only numbers that are comparable on one axis.

    Mixing a percentage and a billion-dollar figure on the same axis is a
    dishonest chart, so we group by unit and take the largest coherent group.
    Also drops groups whose values span >4 orders of magnitude, where a bar
    chart would render all but one bar invisible.
    """
    if len(numbers) < 2:
        return None
    by_unit: dict[str, list[tuple[float, str, str]]] = {}
    for value, unit, label in numbers:
        by_unit.setdefault(unit, []).append((value, unit, label))

    best = max(by_unit.values(), key=len)
    if len(best) < 2:
        return None

    values = [abs(v) for v, _u, _l in best if v]
    if values and max(values) / max(min(values), 1e-9) > 1e4:
        return None

    # Deduplicate by label, preserving order.
    seen: set[str] = set()
    deduped: list[tuple[float, str, str]] = []
    for value, unit, label in best:
        key = label.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append((value, unit, label))
    return deduped if len(deduped) >= 2 else None


def _display_scale(values: list[float], unit: str) -> tuple[float, str]:
    """Choose a display divisor and an axis-unit suffix for a value series.

    Numbers are parsed into ABSOLUTE units so that "$67.2 billion" and
    "$71,400 million" are directly comparable — that comparability is what
    makes `_coherent_group` able to reject dishonest mixed-axis charts, so it
    must not be given up.

    But absolute units are wrong for *display*: an axis tick reading
    ``67200000000`` is unreadable, and a benchmark-grade exhibit never shows
    one. Consulting convention puts the magnitude in the axis label ("USD
    billion") and leaves the tick a small number ("67.2").

    Returns ``(divisor, suffix)``. The suffix is "" when no scaling applies,
    so percentages and small counts are untouched.
    """
    magnitudes = [abs(v) for v in values if v]
    if not magnitudes or unit == "%":
        return 1.0, ""
    peak = max(magnitudes)
    for divisor, suffix in (
        (1e12, "trillion"),
        (1e9, "billion"),
        (1e6, "million"),
        (1e3, "thousand"),
    ):
        # Require the peak to reach the magnitude, so a 900-million series is
        # shown as "900 million" rather than "0.9 billion".
        if peak >= divisor:
            return divisor, suffix
    return 1.0, ""


def _infer_shape(labels: list[str], unit: str) -> tuple[str, str]:
    """Infer (data_shape, chart_type_hint) from the labels themselves.

    Returns a shape string the Data Visualizer's existing
    `_select_chart_type` already understands, so chart choice stays in one
    place rather than being duplicated here.
    """
    if all(_YEAR_RE.fullmatch(label.strip()) for label in labels) and len(labels) >= 3:
        return "trend over time", "line"
    if unit == "%" and len(labels) <= 5:
        return "composition parts of whole", "bar"
    return "comparison across categories", "bar"


def _title_from(finding_title: str, question: str) -> str:
    """Build a chart title from the finding, falling back to the question.

    Deliberately NOT a template. The old hardcoded-template approach is what
    produced subject-less artefacts elsewhere in this codebase.
    """
    title = (finding_title or "").strip().rstrip(".")
    if title and len(title) > 8:
        return title[:90]
    q = (question or "").strip().rstrip("?").strip()
    return (q[:90] or "Supporting Data")


def mine_chart_specs(
    report: Any,
    question: str = "",
    max_charts: int = 8,
) -> list[dict[str, Any]]:
    """Mine chart specs from a FinalReport's findings and sections.

    Args:
        report: A `FinalReport` (or anything exposing `key_findings`/`sections`).
        question: The user's question, used for titles when a finding is untitled.
        max_charts: Upper bound — a premium report is not a chart dump.

    Returns:
        A list of plain dicts in the shape the Data Visualizer's
        `_receive_chart_specs` already expects. Empty when nothing numeric and
        comparable can be honestly extracted.
    """
    if report is None:
        return []

    specs: list[dict[str, Any]] = []

    # ── Section index, for homing every spec to a real section (fix 3.7) ──
    #
    # BUG HISTORY. Exec-summary findings used to be paired with `section_id=""`.
    # `""` is not the `id` of any section, and the template iterates
    # `section_charts[section.id]` — so every chart mined from `key_findings`
    # was generated at 300 DPI, placed under the dict key `""`, and then
    # rendered by nobody. Those are the *headline* findings, so the most
    # important exhibits in the document were precisely the ones silently
    # dropped. `_receive_chart_images` reproduced the same `""` key downstream.
    #
    # Every spec must therefore name a section that actually exists *whenever
    # the report has sections*. A finding carries the `agent` that produced it
    # and `AnalysisSection` carries the same `agent`, so the authoring agent is
    # an honest, non-arbitrary anchor: a market-sizing finding lands in the
    # market section. Failing that, the first section.
    #
    # SCOPE NOTE. When the report has NO sections, `section` stays `""` and the
    # spec is still emitted. Mining ("is this series chartable?") and placement
    # ("which page renders it?") are separate concerns, and the miner is also
    # called on section-less reports — by `mine_chart_specs`' own callers and
    # by the chart-mining tests — where returning `[]` would report a report
    # with no chartable data when it has plenty. The renderability guard
    # therefore lives at the single hop that owns placement,
    # `PresentationDesigner._receive_chart_images`, which re-homes any spec
    # whose section does not resolve. Enforcing it in both places once caused
    # 9 chart-miner tests to fail for the wrong reason.
    sections = list(getattr(report, "sections", None) or [])
    section_ids: list[str] = []
    section_id_by_agent: dict[str, str] = {}
    for section in sections:
        sid = getattr(section, "id", "") or getattr(section, "title", "")
        if not sid:
            continue
        section_ids.append(sid)
        agent = (getattr(section, "agent", "") or "").strip()
        if agent and agent not in section_id_by_agent:
            section_id_by_agent[agent] = sid

    def _home_section(finding: Any) -> str:
        """Pick the real section a homeless finding belongs to, or ''."""
        agent = (getattr(finding, "agent", "") or "").strip()
        if agent and agent in section_id_by_agent:
            return section_id_by_agent[agent]
        return section_ids[0] if section_ids else ""

    # Candidate pool: exec-summary findings first (most important), then each
    # section's own findings, so the earliest charts back the headline claims.
    candidates: list[tuple[Any, str]] = []
    for finding in (getattr(report, "key_findings", None) or []):
        candidates.append((finding, _home_section(finding)))
    for section in sections:
        section_id = getattr(section, "id", "") or getattr(section, "title", "")
        for finding in (getattr(section, "findings", None) or []):
            candidates.append((finding, section_id))

    used_signatures: set[tuple[float, ...]] = set()

    for finding, section_id in candidates:
        if len(specs) >= max_charts:
            break

        content = getattr(finding, "content", "") or ""
        numbers = _extract_numbers(content)
        group = _coherent_group(numbers)
        if not group:
            continue

        # Skip a chart that would duplicate one we already built.
        signature = tuple(sorted(round(v, 4) for v, _u, _l in group))
        if signature in used_signatures:
            continue
        used_signatures.add(signature)

        values = [v for v, _u, _l in group]
        # `label` not `l`: E741 bans `l` because in most fonts it is
        # indistinguishable from `1` and `I`, and this comprehension sits two
        # lines from a `values` list of numbers.
        labels = [label for _v, _u, label in group]
        unit = group[0][1]
        shape, hint = _infer_shape(labels, unit)

        # Real citation from the finding's own sources — never a placeholder.
        sources = getattr(finding, "sources", None) or []
        citation = ""
        if sources:
            names = []
            for s in sources[:3]:
                name = getattr(s, "title", "") or getattr(s, "url", "")
                if name:
                    names.append(str(name)[:60])
            if names:
                citation = "Source: " + "; ".join(names)

        axis_unit = {"%": "Percent", "USD": "USD", "EUR": "EUR", "GBP": "GBP",
                     "INR": "INR", "count": "Volume"}.get(unit, "Value")

        # Rescale for display and move the magnitude into the axis label, so a
        # tick reads "67.2" under an axis titled "USD billion" rather than
        # "67200000000". Values stay internally comparable up to this point.
        divisor, magnitude_suffix = _display_scale(values, unit)
        if divisor > 1.0:
            values = [round(v / divisor, 4) for v in values]
            axis_unit = f"{axis_unit} {magnitude_suffix}"

        # Fix 3.7: the "Note:" line of the MGI/BCG exhibit anatomy. Both
        # benchmarks carry one under EVERY exhibit; HYPERION carried none, so
        # every figure shipped with a truncated anatomy.
        #
        # It is assembled from facts that are already true and checkable, never
        # from an LLM and never from a template with invented content — the
        # figure's provenance is the last place a consulting deliverable can
        # afford to guess. Two components, both derived from what actually
        # happened during mining:
        #   * how the series was obtained (values quoted verbatim from the named
        #     analyst's finding — this is the honest description of what
        #     `_extract_numbers` did, and it tells a reader the figures were not
        #     modelled or interpolated by HYPERION), and
        #   * the display rescaling, when one was applied, so a reader is never
        #     left to guess whether a tick reading "67.2" means 67.2 or
        #     67,200,000,000.
        note_parts: list[str] = []
        finding_agent = (getattr(finding, "agent", "") or "").strip()
        if finding_agent:
            note_parts.append(
                f"Values quoted as reported in the {finding_agent.replace('_', ' ')} "
                f"finding; not modelled or interpolated"
            )
        else:
            note_parts.append("Values quoted as reported; not modelled or interpolated")
        if divisor > 1.0:
            note_parts.append(f"figures shown in {magnitude_suffix}")
        if unit == "%":
            note_parts.append("figures are percentages")
        note = "Note: " + "; ".join(note_parts) + "."

        specs.append({
            "id": f"chart_{getattr(finding, 'id', len(specs)) or len(specs)}_{len(specs)}",
            "title": _title_from(getattr(finding, "title", ""), question),
            "section": section_id,
            "data_shape": shape,
            "chart_type_hint": hint,
            "data_series": [{
                "name": axis_unit,
                "values": values,
                "labels": labels,
            }],
            "x_axis_label": "Period" if hint == "line" else "Category",
            "y_axis_label": axis_unit,
            "source_citation": citation,
            "note": note,
            "caption": (getattr(finding, "implications", "") or "")[:180],
            "insight": (getattr(finding, "title", "") or "")[:140],
        })

    return specs
