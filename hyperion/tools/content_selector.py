"""
HYPERION Content Selector — chunk → rerank → top-k relevance-aware assembly.

Fix 2.2 (HYPERION_DEEP_AUDIT_2026-07-27.md §4.7 Finding B-6, §6 Phase 2 item
2.2): "Replace blind ``content[:15000]`` with **chunk → rerank →
top-k-by-relevance** assembly."

THE DEFECT THIS MODULE FIXES
----------------------------
``MAX_CONTENT_CHARS = 15000`` was applied as a blind head-slice at every
extraction tier in ``deep_search.py`` and ``http_extract.py``::

    content = (result.markdown or result.content)[:MAX_CONTENT_CHARS]

For a short article that is a no-op. For the documents that actually carry the
numbers a consulting report needs — a 60-page IEA outlook, an IMF Article IV, a
World Bank country diagnostic, a 10-K — it retains roughly the **first 12%** and
discards the rest. And the rest is where the value is:

  * The **front matter** of an institutional PDF is a title page, a copyright
    notice, a foreword, a table of contents, and a list of abbreviations. That
    is what a head-slice keeps.
  * The **tables, the exhibits, the methodology, and the conclusions** are in
    the back half. That is what a head-slice throws away.

So the truncation was not merely lossy, it was *adversely* biased: it
systematically preferred boilerplate over evidence. It also silently defeated
two downstream systems that the audit separately flagged as under-performing:

  * ``evidence_scorer._score_relevance`` scores whatever survives truncation.
    Feed it front matter and it scores front matter — the relevance floor
    (§4.8) cannot reject a document whose relevant half was already deleted
    before scoring.
  * ``chart_specs.mine_chart_specs`` correctly returns ``[]`` rather than
    inventing data (§3.5 / §12). It mines numbers out of retained content. A
    head-slice that drops every table guarantees it finds nothing — which is a
    direct contributor to the measured ``has_exhibits: false``.

THE FIX
-------
Same output budget, *chosen by relevance instead of by position*:

  1. **Chunk** the document on structural boundaries (markdown headings, then
     blank-line paragraph breaks), never mid-sentence where avoidable.
  2. **Rerank** every chunk against the query with a lexical relevance model —
     BM25 term saturation over token-boundary matches, plus explicit boosts for
     the things a consulting analyst is actually mining for (numerals,
     currency/percentage/unit tokens, table markup, magnitude words like
     ``billion``/``CAGR``).
  3. **Assemble** the top-k chunks up to the *same* char budget, then **restore
     document order** so the retained text still reads as prose rather than as
     a relevance-sorted jumble.

Two properties are deliberate and pinned by tests:

  * **The lead chunk is always kept.** The opening of a document carries the
    abstract/thesis/executive summary and the entity naming that makes the rest
    interpretable. Dropping it to make room for a high-scoring table produces
    context-free numbers. It is kept even when it scores zero.
  * **Never-raises, never-empty.** Every failure mode — no query, no content,
    a degenerate tokenizer result, an unexpected exception — degrades to the
    old head-slice behaviour and says so via ``degraded``/``strategy``. A
    silent query-layer failure is exactly how the audit's P0 hid for so long
    (§0), so this module's outages are visible in its own return value.

Zero new dependencies: the BM25 implementation is ~40 lines of local code, not
``rank_bm25`` (which is present only transitively via ``crawl4ai`` and could
vanish on any dependency bump). A reranker *model* is Phase 5.4; this is the
lexical stage that has to exist underneath it either way, and the
:func:`select_relevant_content` signature is where that model plugs in.
"""

from __future__ import annotations

import logging
import math
import re
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# The retained-content budget. Unchanged from ``deep_search.MAX_CONTENT_CHARS``
# on purpose: this fix does not buy more context window, it spends the same
# budget on better text.
DEFAULT_BUDGET_CHARS = 15000

# Target chunk size. Large enough that a table plus its caption survives intact
# (a 400-char chunk splits a table away from the sentence that says what it
# measures); small enough that a 15k budget still holds a dozen-plus distinct
# passages rather than three.
DEFAULT_CHUNK_CHARS = 1200

# Hard floor on chunk size. Below this a "chunk" is a heading or a stray line
# and is merged forward instead of competing for budget on its own.
MIN_CHUNK_CHARS = 200

# Minimum leftover worth topping the budget up with. Chunk boundaries never
# align with a char budget, so the greedy pass leaves a tail unspent; below this
# many characters a top-up appends a fragment too short to carry a fact while
# guaranteeing the selection ends mid-sentence, so it is not worth taking.
MIN_TOPUP_CHARS = 150

# BM25 parameters. k1 controls term-frequency saturation, b controls
# length normalisation. These are the standard Robertson/Sparck-Jones defaults
# and are exposed as module constants only so a test can pin them.
BM25_K1 = 1.5
BM25_B = 0.75

# Stop words, shared in spirit with ``evidence_scorer._extract_keywords``.
# Kept local rather than imported so a change to the scorer's list — tuned for
# a different job (scoring a whole document) — cannot silently retune chunk
# selection.
_STOP_WORDS: frozenset[str] = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for", "of",
    "with", "by", "from", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "must", "can", "this", "that", "these", "those",
    "i", "you", "he", "she", "it", "we", "they", "as", "if", "into", "than",
    "then", "there", "their", "its", "also", "about", "over", "under", "such",
})

# Tokens that mark a passage as carrying quantitative evidence. A consulting
# report is built out of these; a passage containing them is worth more budget
# than an equally on-topic passage of pure narrative.
_EVIDENCE_TOKENS: tuple[str, ...] = (
    "billion", "million", "trillion", "bn", "cagr", "yoy", "y/y",
    "percent", "growth", "revenue", "market size", "forecast", "share",
    "gwh", "kwh", "mwh", "tonnes", "tons", "barrels", "capex", "opex",
    "ebitda", "margin", "tariff", "median", "average", "compound annual",
)

# Markdown/plaintext signals that a chunk contains a table. Tables are the
# single densest evidence form in an institutional PDF and are exactly what the
# old head-slice discarded (§4.7).
_TABLE_MARKERS: tuple[str, ...] = ("|---", "| ---", "|:--", "\t")


@dataclass
class Chunk:
    """One candidate passage, with its position and its score.

    ``index`` is the document-order position and is what
    :func:`select_relevant_content` sorts by when reassembling — the selection
    is by score, the *output order* is by position, so retained text still
    reads forwards.
    """

    text: str
    index: int
    start: int
    end: int
    score: float = 0.0
    heading: str = ""

    @property
    def length(self) -> int:
        return len(self.text)

    def to_dict(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "start": self.start,
            "end": self.end,
            "score": round(self.score, 6),
            "length": self.length,
            "heading": self.heading,
        }


@dataclass
class SelectionResult:
    """The assembled content plus the provenance of how it was assembled.

    The audit's recurring lesson is that a silent degradation is worse than a
    loud failure (§0, fix 0.3). So this type reports *why* it returned what it
    returned: ``strategy`` names the path taken, ``degraded`` flags the
    head-slice fallback, and the chunk counters make the retention ratio
    measurable — which is what fix 2.6's per-engagement yield metric consumes.
    """

    content: str = ""
    strategy: str = "reranked"
    degraded: bool = False
    reason: str = ""
    chunks_total: int = 0
    chunks_kept: int = 0
    chars_in: int = 0
    chars_out: int = 0
    kept_indices: list[int] = field(default_factory=list)
    scores: list[float] = field(default_factory=list)

    @property
    def retention(self) -> float:
        """Fraction of the input retained. 1.0 when nothing was dropped."""
        if self.chars_in <= 0:
            return 0.0
        return min(self.chars_out / self.chars_in, 1.0)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "degraded": self.degraded,
            "reason": self.reason,
            "chunks_total": self.chunks_total,
            "chunks_kept": self.chunks_kept,
            "chars_in": self.chars_in,
            "chars_out": self.chars_out,
            "retention": round(self.retention, 4),
            "kept_indices": self.kept_indices,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Tokenisation
# ─────────────────────────────────────────────────────────────────────────────


def tokenize(text: str) -> list[str]:
    """Token-boundary tokenisation, lowercased, stop words removed.

    Deliberately **token-boundary, not substring**. This is the same defect
    audit §4.8 records against ``evidence_scorer._score_relevance``
    (``if word in content_lower`` makes ``"ai"`` match ``"said"``,
    ``"chain"``, ``"maintain"``) and it must not be reintroduced here, where
    it would misrank chunks *and* inflate the relevance the scorer later sees.

    Numerals are kept as tokens: ``"Scope 3"``, ``"Section 301"``, ``"2030"``
    are qualifying terms in exactly the queries this system issues, and
    ``query_utils.normalize_query`` already goes to some trouble to preserve
    them upstream.
    """
    if not text:
        return []
    raw = re.findall(r"[a-z0-9]+(?:\.[0-9]+)?", text.lower())
    return [t for t in raw if t not in _STOP_WORDS and (len(t) > 1 or t.isdigit())]


# ─────────────────────────────────────────────────────────────────────────────
# Chunking
# ─────────────────────────────────────────────────────────────────────────────


def _split_on_structure(text: str) -> list[str]:
    """Split on markdown headings first, then blank lines.

    Heading-first matters: a heading is a semantic boundary the document's own
    author declared, and it also gives every chunk below it a label
    (:attr:`Chunk.heading`) that a downstream consumer can cite. Falling back
    to blank lines covers plain-text extractions (trafilatura output, Jina
    Reader markdown without headings, FlareSolverr tag-stripped HTML).
    """
    # Keep the heading line attached to the section it introduces.
    parts = re.split(r"\n(?=#{1,6}\s)", text)
    if len(parts) > 1:
        return [p for p in parts if p.strip()]
    parts = re.split(r"\n\s*\n", text)
    return [p for p in parts if p.strip()]


def _heading_of(block: str) -> str:
    """Return the markdown heading that opens ``block``, if any."""
    first = block.lstrip().split("\n", 1)[0].strip()
    if first.startswith("#"):
        return first.lstrip("#").strip()[:120]
    return ""


def _is_titled_section(block: str) -> bool:
    """True when ``block`` is a heading *plus real body text* beneath it.

    Distinguishes a genuine short section (``## Conclusions`` followed by two
    sentences — keep it, keep its label) from a bare heading line with nothing
    under it (merge it forward, it is not a passage).

    "Real body text" deliberately excludes further headings. A document opening
    ``# Title`` / ``## Subtitle`` / ``### Section`` is a *heading stack*, and
    ``test_no_runt_chunks`` caught the first version of this predicate treating
    ``"# H1\\n\\n## H2"`` as a titled section — because ``H2`` is technically
    "text under the H1" — and emitting it as a 12-character chunk of pure label.
    A chunk with no prose cannot answer anything; it can only consume a slot.
    """
    stripped = block.strip()
    if not _heading_of(stripped):
        return False
    parts = stripped.split("\n", 1)
    if len(parts) != 2:
        return False
    body_lines = [
        ln.strip() for ln in parts[1].splitlines()
        if ln.strip() and not ln.strip().startswith("#")
    ]
    return bool(body_lines)


def chunk_content(
    text: str,
    *,
    target_chars: int = DEFAULT_CHUNK_CHARS,
    min_chars: int = MIN_CHUNK_CHARS,
) -> list[Chunk]:
    """Split ``text`` into ranked-candidate chunks on structural boundaries.

    Guarantees, each pinned by a test:

      * **Lossless.** Concatenating every chunk reproduces every non-whitespace
        character of the input in order. A chunker that drops content would
        reintroduce the very defect this module exists to remove, just less
        visibly than a head-slice does.
      * **No mid-sentence splits where avoidable.** Oversized blocks (a single
        20k-char paragraph, common in tag-stripped HTML) are split on sentence
        boundaries; only a block with no sentence boundary at all is hard-cut.
      * **No runt chunks.** Blocks below ``min_chars`` are merged forward, so a
        bare heading or a one-line caption never competes for budget as if it
        were a passage.
    """
    if not text or not text.strip():
        return []

    target = max(int(target_chars), min_chars)
    blocks = _split_on_structure(text)
    if not blocks:
        blocks = [text]

    # Pass 1 — merge runts forward and split oversized blocks on sentences.
    #
    # One exception to the runt-merge rule, found by
    # `test_headings_are_captured_as_labels`: a block that opens with a markdown
    # heading AND has body text under it is a section boundary its own author
    # declared, and a short section is still a section. Absorbing it into the
    # previous chunk silently deleted its heading label — and the block it did
    # that to in the test fixture was `## Conclusions`, i.e. exactly the kind of
    # short, high-value closing section (`Key Takeaways`, `Implications`,
    # `Recommendation`) whose label a consulting report most wants to keep.
    # Heading-only blocks with no body are still merged, so a document of bare
    # headings does not explode into hundreds of label-sized chunks.
    merged: list[str] = []
    buffer = ""
    for block in blocks:
        if buffer and _is_titled_section(block):
            # A titled section starts here, so the buffer must not absorb it.
            # But if the buffer is itself just a stack of bare headings
            # (``# Title`` / ``## Subtitle`` with no body between them, the
            # normal shape of a document's opening), emitting it alone produces
            # a 12-char "chunk" that is pure label. Prepend it to the section it
            # introduces instead — which is also where it belongs semantically.
            if len(buffer) < min_chars:
                block = f"{buffer}\n\n{block}"
            else:
                merged.append(buffer)
            buffer = ""
        candidate = f"{buffer}\n\n{block}" if buffer else block
        if len(candidate) < min_chars and not _is_titled_section(candidate):
            buffer = candidate
            continue
        buffer = ""
        if len(candidate) <= target * 2:
            merged.append(candidate)
            continue
        merged.extend(_split_oversized(candidate, target))
    if buffer:
        if merged and not _is_titled_section(buffer):
            merged[-1] = f"{merged[-1]}\n\n{buffer}"
        else:
            merged.append(buffer)

    # Pass 2 — locate each chunk in the source so callers can map back.
    chunks: list[Chunk] = []
    cursor = 0
    for i, body in enumerate(merged):
        stripped = body.strip()
        if not stripped:
            continue
        found = text.find(stripped, cursor)
        if found < 0:
            found = cursor
        chunks.append(
            Chunk(
                text=stripped,
                index=i,
                start=found,
                end=found + len(stripped),
                heading=_heading_of(stripped),
            )
        )
        cursor = found + len(stripped)
    return chunks


def _split_oversized(block: str, target: int) -> list[str]:
    """Split a too-large block on sentence boundaries, hard-cutting only if forced."""
    sentences = re.split(r"(?<=[.!?])\s+", block)
    out: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > target * 2:
            # A single sentence larger than two chunks: no boundary exists to
            # respect, so hard-cut it rather than emit a 40k-char "chunk".
            if current:
                out.append(current)
                current = ""
            for i in range(0, len(sentence), target):
                out.append(sentence[i : i + target])
            continue
        if current and len(current) + len(sentence) + 1 > target:
            out.append(current)
            current = sentence
        else:
            current = f"{current} {sentence}".strip() if current else sentence
    if current:
        out.append(current)
    return [c for c in out if c.strip()]


# ─────────────────────────────────────────────────────────────────────────────
# Reranking
# ─────────────────────────────────────────────────────────────────────────────


def _bm25_scores(query_tokens: list[str], docs: list[list[str]]) -> list[float]:
    """BM25 over the chunk set, computed locally (no ``rank_bm25`` dependency).

    BM25 rather than raw overlap for two reasons that matter on real
    extractions:

      * **Term saturation (``k1``).** A navigation sidebar repeating
        ``"lithium"`` 40 times must not outrank a table that states the number
        once. Linear term-frequency scoring — which is what a naive
        ``content.count(word)`` gives — gets this exactly backwards.
      * **Length normalisation (``b``).** Without it every long chunk wins on
        volume alone, which reduces the reranker to a slower head-slice.

    IDF is computed *within the document's own chunk set*, which is the right
    corpus here: a term appearing in every chunk of this page (the page's own
    subject, boilerplate headers, the site name) carries no signal about
    *which chunk* to keep, and BM25's IDF term discounts it automatically.
    """
    n = len(docs)
    if n == 0 or not query_tokens:
        return [0.0] * n

    lengths = [len(d) for d in docs]
    avgdl = (sum(lengths) / n) or 1.0

    freqs: list[dict[str, int]] = []
    doc_freq: dict[str, int] = {}
    for doc in docs:
        counts: dict[str, int] = {}
        for token in doc:
            counts[token] = counts.get(token, 0) + 1
        freqs.append(counts)
        for token in counts:
            doc_freq[token] = doc_freq.get(token, 0) + 1

    idf: dict[str, float] = {}
    for token in set(query_tokens):
        df = doc_freq.get(token, 0)
        # Robertson/Sparck-Jones IDF with the standard +1 to keep it positive
        # even for a term present in every chunk.
        idf[token] = math.log(1.0 + (n - df + 0.5) / (df + 0.5))

    scores: list[float] = []
    for i, counts in enumerate(freqs):
        dl = lengths[i] or 1
        total = 0.0
        for token in query_tokens:
            tf = counts.get(token, 0)
            if not tf:
                continue
            denom = tf + BM25_K1 * (1.0 - BM25_B + BM25_B * dl / avgdl)
            total += idf.get(token, 0.0) * (tf * (BM25_K1 + 1.0)) / denom
        scores.append(total)
    return scores


def _evidence_boost(text: str) -> float:
    """Bonus for a chunk that carries quantitative evidence.

    This is the part of the ranking that encodes *what HYPERION is for*. Two
    chunks equally on-topic are not equally useful: the one containing
    ``"the market reached USD 4.2 billion in 2024, a 17% CAGR"`` is a citable
    finding, the one containing ``"the market has grown considerably"`` is not.
    Additive and capped, so it re-orders ties without ever overpowering BM25 —
    a chunk full of unrelated numbers must not beat a chunk that is actually
    about the query.
    """
    if not text:
        return 0.0
    lowered = text.lower()
    boost = 0.0

    digits = sum(c.isdigit() for c in text)
    density = digits / max(len(text), 1)
    boost += min(density * 8.0, 0.6)

    if re.search(r"(?:\$|€|£|usd|eur|gbp)\s?\d|\d\s?(?:%|percent)", lowered):
        boost += 0.35

    hits = sum(1 for token in _EVIDENCE_TOKENS if token in lowered)
    boost += min(hits * 0.08, 0.4)

    if any(marker in text for marker in _TABLE_MARKERS):
        boost += 0.3

    return min(boost, 1.2)


def rerank_chunks(query: str, chunks: list[Chunk]) -> list[Chunk]:
    """Score ``chunks`` against ``query`` in place and return them, best first.

    Composite = BM25 (topical match, saturated and length-normalised) +
    evidence boost (quantitative density), where **the boost applies only to
    chunks that are topically relevant at all**. Ties break on document order
    so the result is deterministic — a non-deterministic selector would make
    fix 5.2's golden-PDF regression test unpinnable.

    THE BOOST GATE, AND WHY IT IS NOT OPTIONAL
    ------------------------------------------
    ``_evidence_boost`` was written as a tie-breaker: "additive and capped, so
    it re-orders ties without ever overpowering BM25." Applied unconditionally
    it does not honour that description, and
    ``test_boilerplate_loses_to_evidence_under_a_tight_budget`` caught the
    consequence on the most on-the-nose possible example — **a table of
    contents**:

        total=0.600  bm25=0.000  boost=0.600  ## Table of Contents
                                                Foreword ... 3
                                                Chapter 1 ... 9
                                                Chapter 2 ... 41

    A ToC is nothing but numerals, so the digit-density term scored it 0.600
    while its BM25 was exactly 0.000 — it shares no term with the query. That
    lifted it above every genuinely-scored-but-lower chunk and won it budget
    under pressure. A page-number list is the single least useful passage in an
    institutional PDF, and the unconditional boost was actively seeking it out.

    The same shape applies beyond the fixture: stock-ticker sidebars, "related
    articles" date lists, pagination controls, footnote number runs, and cookie
    banners with policy version numbers are all numeral-dense and topically
    irrelevant. Gating on ``bm25 > 0`` restores the documented intent — a chunk
    that matches no query term gets no boost, so evidence density can only ever
    reorder passages that are *about the question*.
    """
    if not chunks:
        return []
    query_tokens = tokenize(query)
    docs = [tokenize(c.text) for c in chunks]
    bm25 = _bm25_scores(query_tokens, docs)
    for chunk, base in zip(chunks, bm25, strict=False):
        chunk.score = base + (_evidence_boost(chunk.text) if base > 0 else 0.0)
    return sorted(chunks, key=lambda c: (-c.score, c.index))


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────


def select_relevant_content(
    content: str,
    query: str = "",
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    always_keep_lead: bool = True,
) -> SelectionResult:
    """Assemble up to ``budget_chars`` of the **most relevant** part of ``content``.

    This is the drop-in replacement for ``content[:MAX_CONTENT_CHARS]``.

    Args:
        content: The full extracted document text.
        query: The query the content was retrieved for. Empty query ⇒ there is
            nothing to rank against, so this honestly degrades to a head-slice
            rather than pretending to have reranked.
        budget_chars: Output budget. Same default as the old constant.
        chunk_chars: Target chunk size.
        always_keep_lead: Keep chunk 0 regardless of score (see module
            docstring — context-free numbers are worse than fewer numbers).

    Returns:
        :class:`SelectionResult`. Never raises; on any internal failure the
        result carries ``degraded=True`` and the original head-slice content,
        so a bug here can degrade retrieval quality but can never zero it —
        the failure mode that produced the audit's P0.
    """
    text = content or ""
    chars_in = len(text)

    def _head_slice(strategy: str, reason: str) -> SelectionResult:
        sliced = text[:budget_chars]
        return SelectionResult(
            content=sliced,
            strategy=strategy,
            degraded=strategy != "verbatim",
            reason=reason,
            chunks_total=0,
            chunks_kept=0,
            chars_in=chars_in,
            chars_out=len(sliced),
        )

    if not text.strip():
        return _head_slice("empty", "no content")

    if chars_in <= budget_chars:
        # Nothing to choose between — the whole document fits. Reported as
        # ``verbatim`` (not ``degraded``) because no selection was needed.
        return SelectionResult(
            content=text,
            strategy="verbatim",
            degraded=False,
            reason="content within budget",
            chunks_total=1,
            chunks_kept=1,
            chars_in=chars_in,
            chars_out=chars_in,
            kept_indices=[0],
        )

    if not query or not query.strip():
        logger.debug(
            "content_selector: no query supplied for a %d-char document — "
            "falling back to head-slice (nothing to rank against)",
            chars_in,
        )
        return _head_slice("head_slice", "no query to rank against")

    try:
        chunks = chunk_content(text, target_chars=chunk_chars)
        if len(chunks) < 2:
            return _head_slice("head_slice", "content did not chunk")

        ranked = rerank_chunks(query, chunks)

        # Does *anything* in this document match the query at all?
        #
        # This single fact drives both the relevance gate below and the
        # ``degraded`` flag at the end, so it is computed once, from `ranked`
        # (the full chunk set) rather than from what happens to get retained.
        #
        # `any_scored is False` means BM25 found no query term anywhere in the
        # document. Selection then cannot be relevance-driven no matter what it
        # returns — see the `degraded` reasoning at the end of this function.
        any_scored = any(c.score > 0 for c in ranked)

        # Budget assembly. Separators cost characters too — count them, or a
        # "15000-char" budget quietly ships 15000 + n_chunks * 2.
        sep = "\n\n"
        kept: list[Chunk] = []
        used = 0

        if always_keep_lead:
            lead = chunks[0]
            if lead.length <= budget_chars:
                kept.append(lead)
                used = lead.length

        # Greedy fill, highest score first — WITH a relevance gate.
        #
        # The gate is the fix for the defect `test_boilerplate_loses_to_evidence
        # _under_a_tight_budget` caught. At a 900-char budget the lead chunk
        # (1,618 chars) does not fit, so the loop started from an empty
        # selection, correctly took the three evidence chunks (673 chars) — and
        # then kept going, because 227 chars were still unspent and a 121-char
        # **table of contents** fit. Six identical copies of a page-number list
        # are in this document; they score exactly 0.0, and among equal scores
        # the lowest index wins, so the front matter the audit's §4.7 complains
        # about walked straight back into the output through the side door.
        #
        # An unscored chunk contains no query term at all. Spending budget on it
        # is not a partial win over leaving that budget unspent: it is the
        # head-slice behaviour this module exists to replace, just arrived at by
        # a longer route. So a zero-scoring chunk is admitted only when the
        # document has *no* scored chunk anywhere (`any_scored` False) — the
        # genuinely unmatchable document, where a prefix of something really is
        # the best available answer.
        #
        # `break`, not `continue`: `ranked` is sorted by descending score, so
        # the first zero reached means every chunk after it is also zero.
        for chunk in ranked:
            if any(k.index == chunk.index for k in kept):
                continue
            if chunk.score <= 0 and any_scored:
                break
            cost = chunk.length + (len(sep) if kept else 0)
            if used + cost > budget_chars:
                continue
            kept.append(chunk)
            used += cost

        # Top-up. Chunk boundaries do not align with the budget, so the greedy
        # pass above routinely leaves a tail of it unspent — and in the
        # pathological case found by
        # `test_single_chunk_document_degrades_rather_than_returning_nothing`
        # (a 5,000-char document with no internal boundary at all, against a
        # 1,000-char budget) it spent 200 of 1,000 because the only chunk small
        # enough to fit whole was the 200-char remainder.
        #
        # Leaving budget unspent is not a safe default: this budget is the
        # entire evidence base a specialist gets for a source, so an 80% unspent
        # budget is an 80% smaller evidence base than the caller asked for —
        # quietly worse than the head-slice this module replaces. So the
        # highest-scoring chunk that did not fit whole contributes a prefix of
        # itself to fill the remainder.
        #
        # Threshold, not always: topping up for the last handful of characters
        # would append a fragment too short to carry a fact, at the cost of
        # ending every selection mid-sentence.
        #
        # The top-up carries the same relevance gate as the greedy pass, for the
        # same reason: filling a budget with a page-number list is not an
        # improvement over leaving it unspent; it is the audit's §4.7 complaint
        # in miniature. A zero-scoring prefix is taken only for the document
        # where nothing scored anywhere (`any_scored` False) — no topical match
        # exists to prefer, so a prefix of *something* genuinely is the best
        # available answer and returning 200 of 1,000 requested chars would be a
        # silent shortfall.
        remaining = budget_chars - used - (len(sep) if kept else 0)
        if remaining >= MIN_TOPUP_CHARS:
            for chunk in ranked:
                if any(k.index == chunk.index for k in kept):
                    continue
                if chunk.score <= 0 and any_scored:
                    break
                kept.append(
                    Chunk(
                        text=chunk.text[:remaining],
                        index=chunk.index,
                        start=chunk.start,
                        end=chunk.start + remaining,
                        score=chunk.score,
                        heading=chunk.heading,
                    )
                )
                break

        if not kept:
            # Not even a prefix could be placed (budget below MIN_TOPUP_CHARS
            # with no chunk fitting whole). Head-slice is the only option left,
            # and taking it is what keeps this module's floor equal to the old
            # behaviour rather than below it.
            return _head_slice("head_slice", "no chunk fits the budget")

        # Selection was by score; OUTPUT is by document order, so the retained
        # text reads forwards. A relevance-sorted jumble would be measurably
        # worse input for the LLM that consumes it, even though it contains the
        # identical characters.
        kept.sort(key=lambda c: c.index)
        assembled = sep.join(c.text for c in kept)

        # Honest reporting of a relevance-blind selection.
        #
        # `any_scored is False` means BM25 matched no query term in any chunk:
        # the reranker ran, but had nothing to rank on, so what came back is
        # ordered by document position and is a head-slice in all but name. That
        # is exactly the case `test_single_chunk_document_degrades_rather_than
        # _returning_nothing` pins ("z" * 5000 against any query).
        #
        # It must be flagged, because fix 2.6 reports extraction yield per
        # engagement off these flags. An unflagged relevance-blind selection
        # tells the operator "15,000 chars, reranked, clean" for a source that
        # actually contributed nothing topical — a healthy-looking metric over a
        # silent quality failure, which is the shape of the audit's own P0.
        #
        # Note this deliberately does NOT flag a prefix-cut top-up on its own. A
        # cut chunk is a *boundary* artefact, not a relevance failure: the
        # selection above it is still genuinely ranked, and treating a normal
        # budget-edge trim as a degradation would light the warning on nearly
        # every healthy selection and thereby make the flag worthless.
        degraded = not any_scored
        reason = (
            "no chunk matched the query — selection is relevance-blind"
            if degraded
            else ""
        )

        return SelectionResult(
            content=assembled,
            strategy="reranked",
            degraded=degraded,
            reason=reason,
            chunks_total=len(chunks),
            chunks_kept=len(kept),
            chars_in=chars_in,
            chars_out=len(assembled),
            kept_indices=[c.index for c in kept],
            scores=[round(c.score, 6) for c in kept],
        )
    except Exception as e:  # noqa: BLE001 - never-raises contract, logged loud
        logger.warning(
            "content_selector: rerank failed for a %d-char document, falling "
            "back to head-slice: %s",
            chars_in,
            e,
            exc_info=True,
        )
        return _head_slice("head_slice", f"rerank failed: {e!s:.200}")


def select_content(
    content: str,
    query: str = "",
    *,
    budget_chars: int = DEFAULT_BUDGET_CHARS,
) -> str:
    """String-in/string-out convenience wrapper — the literal head-slice swap.

    Exists so a call site that only ever wanted ``content[:15000]`` can adopt
    relevance-aware selection with a one-line edit and without restructuring
    around :class:`SelectionResult`. Call sites that report yield metrics
    (fix 2.6) should use :func:`select_relevant_content` and keep the
    provenance.
    """
    return select_relevant_content(content, query, budget_chars=budget_chars).content


__all__ = [
    "DEFAULT_BUDGET_CHARS",
    "DEFAULT_CHUNK_CHARS",
    "Chunk",
    "SelectionResult",
    "chunk_content",
    "rerank_chunks",
    "select_content",
    "select_relevant_content",
    "tokenize",
]
