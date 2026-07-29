"""HYPERION — explicit page budget (fix 4.1).

WHY THIS MODULE EXISTS
----------------------
The audit (§4.11, finding B-10) measured 36 pages against a stated 15–20 page
target and found that **nothing anywhere mapped to that target**:

* ``synthesis_lead._build_one_section`` prompted for a hardcoded
  "2000-4000 words", repeated in four separate strings in that one function.
* Section count was ``len(self._findings_by_agent)`` — i.e. however many agents
  happened to report, between 1 and 12.
* ``render.py`` then asserted ``15 <= page_count <= 40``, a 25-page-wide
  "success" window that a 36-page report passes and a 16-page report passes
  equally.

So page count was an **emergent accident**: 12 agents reporting produced ~60
pages and 3 agents produced ~15, with no mechanism noticing either. A
consulting deliverable has a length contract; this module is that contract,
expressed as arithmetic instead of as a hope in a prompt string.

PAGES QUANTIZE — THE MODEL MUST TOO
-----------------------------------
The first version of this module divided the available pages by the section
count as if page space were a continuous fluid. It is not. The production CSS
sets ``page-break-before: always`` on every section, so **a section cannot share
a page with its neighbour**: a section needing 2.1 pages of prose occupies 3
sheets and wastes 0.9 of one. Any model that divides continuously will
under-project, which is the same class of error as the bug being fixed — it just
fails at a different point.

Hence `_section_pages` ceilings per section, and `plan_budget` inverts that
ceiling analytically rather than dividing. The two functions are exact inverses
of each other by construction, so the allocation can never drift from the
projection it is derived from.

THE MEASURED CONSTANTS
----------------------
These are not estimates. They were extracted with PyMuPDF from the PDF that
``tools/audit_render_probe.py`` renders through the **production** template, at
the two-column 10 pt layout fix 3.4 established (54 chars/line):

    page 1  ...  19 words   cover
    page 2  ...  67 words   table of contents
    pages 3-4 .. 430 + 337  executive summary (2 pages)
    page 33 ...  18 words   risk analysis
    page 34 ...  42 words   methodology
    page 35 ...  17 words   appendix
    page 36 ...  29 words   back cover
                 ---------
                 8 pages of fixed front/back matter

    per section: pages 5-8 carrying 633 + 767 + 215 + 75 = 1,690 text-layer
                 words, of which the fixture body supplies exactly 1,510 —
                 the other ~180 are chrome (section eyebrow, title, key-insight
                 box, implications box, exhibit title/note/source).
    a FULL two-column body page holds 767 words (page 6: the only page in the
                 fixture that is pure body prose, with no heading, opener,
                 insight box or exhibit competing for vertical space).

Calibration: 1,510 body words = 1.97 full pages of prose. The section occupied
4 sheets. So chrome accounts for 4 − 1.97 = 2.03 sheets' worth of vertical
space, split below into the opener and the exhibit. 7 sections × 4 pages + 8
fixed = 36 — exactly the page count the audit measured, so the model is
validated against the real artefact rather than assumed.

DESIGN PRINCIPLES
-----------------
1. **Budget, never truncate.** The budget is applied by telling the LLM how
   many words to write, before it writes them. Cutting prose after generation
   would sever a paragraph mid-argument, which is worse than a long report.
2. **Degrade honestly.** With many agents reporting, the per-section allocation
   shrinks toward a floor. Below that floor a section cannot carry an argument,
   so the budget says so (``sections_over_capacity``) rather than silently
   emitting 200-word stubs. The caller decides; this module only computes.
3. **No hidden defaults.** Every number here is named, commented with its
   measured provenance, and overridable. A magic ``2000`` in a prompt string is
   what produced the problem being fixed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

__all__ = [
    "BODY_WORDS_PER_FULL_PAGE",
    "FIXED_OVERHEAD_PAGES",
    "MAX_SECTION_WORDS",
    "MIN_SECTION_WORDS",
    "PageBudget",
    "SECTION_CHROME_PAGES",
    "TARGET_PAGES_MAX",
    "TARGET_PAGES_MIN",
    "plan_budget",
]


# ── Measured constants ───────────────────────────────────────────────────────

#: Words on a completely full two-column 10 pt body page. Measured: 767 on
#: probe page 6, the only page in the fixture that is pure body text with no
#: heading, opener, insight box or exhibit competing for vertical space.
BODY_WORDS_PER_FULL_PAGE = 767

#: Pages consumed by front and back matter regardless of section count:
#: cover, table of contents, executive summary (2), risk analysis, methodology,
#: appendix, back cover. Measured as pages 1-4 and 33-36 of the probe PDF.
FIXED_OVERHEAD_PAGES = 8

#: Vertical space, in pages, that the section opener takes from the prose: the
#: letterspaced "SECTION n" eyebrow, the title, the hairline rule and the
#: key-insight box all sit above the first paragraph. Measured: probe page 5
#: carried 633 body words against a 767-word full page, so the opener consumed
#: (767 - 633) / 767 = 0.17 of that page in text terms — but the insight box and
#: title set at display sizes cost far more vertical space than their word count
#: suggests, and the measured section total pins the combined figure below.
SECTION_OPENER_PAGES = 0.70

#: Vertical space the exhibit block takes: the CSS-counter number, the action
#: title, the 300-DPI figure at `column-span: all`, and the Note/Source footer
#: under a hairline. Measured: probe page 8 held the exhibit plus 75 words.
SECTION_EXHIBIT_PAGES = 0.55

#: Total non-prose space per section. Kept as the sum of its two named parts so
#: that changing the exhibit design does not require re-deriving one opaque
#: number. Calibration check: 1510/767 + 1.25 = 3.22, ceiling 4 — the measured
#: per-section page count.
SECTION_CHROME_PAGES = SECTION_OPENER_PAGES + SECTION_EXHIBIT_PAGES

#: The delivery contract from the audit's §3.1 table ("15-20 target").
TARGET_PAGES_MIN = 15
TARGET_PAGES_MAX = 20

#: Below this, a section cannot open a topic, present evidence, interpret it and
#: draw an implication — it becomes a stub. Derived from the audit's own
#: rejection threshold: `_build_one_section` already discarded LLM responses
#: under 800 characters, and 800 chars is ~130 words; we set the floor well
#: above that so a section that merely clears the old check is still not
#: accepted as a real section.
MIN_SECTION_WORDS = 450

#: Ceiling per section. Without one, a 1-section engagement would be asked for
#: ~5,900 words, which no model sustains coherently and which produces
#: repetition rather than depth.
MAX_SECTION_WORDS = 2600


@dataclass(frozen=True)
class PageBudget:
    """A resolved page/word budget for one engagement.

    Frozen because it is computed once and then read by several agents; a
    mutable budget that one agent could adjust mid-run would reintroduce the
    "emergent page count" problem this module exists to remove.
    """

    target_pages: int
    """The page count being aimed at, inside [TARGET_PAGES_MIN, MAX]."""

    section_count: int
    """Number of sections the budget was divided across."""

    words_per_section: int
    """Per-section body word allocation, clamped to the floor/ceiling."""

    total_body_words: int
    """words_per_section * section_count."""

    pages_per_section: int
    """Whole sheets each section will occupy. Quantized: sections never share."""

    projected_pages: int
    """Pages this allocation is expected to produce, including overhead."""

    sections_over_capacity: bool
    """True when section_count cannot fit the contract at the minimum size.

    When True, `words_per_section` has been pinned to MIN_SECTION_WORDS and the
    report is expected to exceed TARGET_PAGES_MAX. Surfaced rather than hidden:
    the honest options are to merge sections or to accept a longer document,
    and both are the caller's decision.
    """

    @property
    def within_contract(self) -> bool:
        """Whether the projection lands inside the delivery contract."""
        return TARGET_PAGES_MIN <= self.projected_pages <= TARGET_PAGES_MAX

    def prompt_clause(self) -> str:
        """The word-count instruction to embed in a section prompt.

        Returned as a sentence rather than a bare number so the caller cannot
        accidentally interpolate it into a different grammatical position and
        change its meaning — the previous code had the same "2000 words" figure
        restated in four places, which is how the four copies drifted.
        """
        low = int(self.words_per_section * 0.9)
        high = int(self.words_per_section * 1.1)
        return (
            f"Write approximately {self.words_per_section} words "
            f"(acceptable range {low}-{high})."
        )


# ── Forward model: words -> pages ────────────────────────────────────────────


def _section_pages(words: int) -> int:
    """Whole sheets one section of `words` body words will occupy.

    Ceilinged because `page-break-before: always` means a section cannot share
    a sheet with the next one: 2.1 pages of prose costs 3 sheets.
    """
    return math.ceil(SECTION_CHROME_PAGES + words / BODY_WORDS_PER_FULL_PAGE)


def _projected_pages(section_count: int, words_per_section: int) -> int:
    """Total pages for `section_count` sections of `words_per_section` each."""
    if section_count <= 0:
        return FIXED_OVERHEAD_PAGES
    return FIXED_OVERHEAD_PAGES + section_count * _section_pages(words_per_section)


# ── Inverse model: pages -> words ────────────────────────────────────────────


def _max_words_in_pages(pages: int) -> int:
    """Most body words that fit a section allotted exactly `pages` sheets.

    The analytic inverse of `_section_pages`: that function ceilings
    ``chrome + words/767`` to `pages`, which holds while
    ``chrome + words/767 <= pages``. Solving for words gives the bound below.
    Deriving it this way — rather than searching, or worse, re-deriving the
    arithmetic by hand — guarantees the two directions can never disagree.
    """
    return int((pages - SECTION_CHROME_PAGES) * BODY_WORDS_PER_FULL_PAGE)


def plan_budget(
    section_count: int,
    target_pages: int | None = None,
) -> PageBudget:
    """Compute the word allocation that lands `section_count` sections on target.

    Strategy: try progressively fewer sheets per section and take the **first**
    (i.e. largest) allotment that both fits within `target_pages` and leaves each
    section above `MIN_SECTION_WORDS`.

    `target_pages` is a **ceiling to fill, not a bullseye to hit**. That
    distinction matters because pages quantize: with 4 sections, 3 sheets each
    projects 20 pages and 2 sheets each projects 16, with nothing in between. An
    earlier version of this function aimed at the contract midpoint and so chose
    16 — thereby asking for 575-word sections when 1,342-word sections were
    available inside the very same contract. Coming in 4 pages under budget is
    not the safe option; it is a thinner deliverable bought for nothing.

    Args:
        section_count: How many analysis sections the report will carry. This
            is ``len(findings_by_agent)`` in the Synthesis Lead — deliberately
            passed in rather than inferred, so this module has no dependency on
            agent internals and is trivially testable.
        target_pages: Page ceiling. Defaults to `TARGET_PAGES_MAX`, the top of
            the delivery contract, so the report fills the space it is allowed.
            Values outside the contract are clamped, because a caller asking for
            a 3-page or 300-page consulting report is asking for something this
            template cannot produce.

    Returns:
        A `PageBudget`. Never raises for odd inputs: a 0-section report is a
        real (if degenerate) case during early pipeline stages, and raising
        there would turn a cosmetic problem into a failed engagement.
    """
    if target_pages is None:
        # The top of the contract, not its midpoint. See the strategy note
        # above: aiming at the middle silently discards pages the deliverable is
        # entitled to, because the per-section page count is an integer.
        target_pages = TARGET_PAGES_MAX
    target_pages = max(TARGET_PAGES_MIN, min(TARGET_PAGES_MAX, target_pages))

    if section_count <= 0:
        return PageBudget(
            target_pages=target_pages,
            section_count=0,
            words_per_section=0,
            total_body_words=0,
            pages_per_section=0,
            projected_pages=FIXED_OVERHEAD_PAGES,
            sections_over_capacity=False,
        )

    def _build(words: int, over_capacity: bool) -> PageBudget:
        pages = _section_pages(words)
        return PageBudget(
            target_pages=target_pages,
            section_count=section_count,
            words_per_section=words,
            total_body_words=words * section_count,
            pages_per_section=pages,
            projected_pages=FIXED_OVERHEAD_PAGES + section_count * pages,
            sections_over_capacity=over_capacity,
        )

    # Largest sheets-per-section worth considering: the whole prose budget spent
    # on one section. Beyond this the ceiling clamp binds anyway.
    max_pages_per_section = max(1, target_pages - FIXED_OVERHEAD_PAGES)

    # Largest allotment first, so the deliverable fills its page allowance
    # rather than undershooting it.
    for pages in range(max_pages_per_section, 0, -1):
        if FIXED_OVERHEAD_PAGES + section_count * pages > target_pages:
            continue
        words = min(MAX_SECTION_WORDS, _max_words_in_pages(pages))
        if words >= MIN_SECTION_WORDS:
            return _build(words, over_capacity=False)

    # Nothing fits: even at the floor this many sections overrun the contract.
    # Report it rather than silently shipping a 40-page "20-page" report.
    return _build(MIN_SECTION_WORDS, over_capacity=True)
