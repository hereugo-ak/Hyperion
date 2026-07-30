# HYPERION: DEEP SYSTEM AUDIT, PART 2

**Date:** 2026-07-30
**Branch:** `fix0.1`
**Predecessor:** `HYPERION_DEEP_AUDIT_2026-07-30.md` (rev 2, defects D-01 … D-23, DoD gates 1 … 24). Those fixes are **in this branch**.
**Artifacts audited:** two production PDFs generated *after* pulling the Part 1 fixes.

| # | File | Pages | Text chars | Engagement | Question |
|---|------|-------|-----------|-----------|----------|
| A | `should_tesla_enter_in_india.pdf` | 31 | 54,499 | `eng_4dab9b2483fb` | "should tesla enter in india ?" |
| B | `should_six_sense_mobilityhexense_lab_starts_manufacturing_ra.pdf` | 32 | 47,649 | (six-sense / Hexense Lab) | "should six sense mobility/hexense lab starts manufacturing ra…" |

**Method.** PyMuPDF 1.28.0 / MuPDF 1.29.0 forensics on both PDFs (`get_image_info`, `get_text("blocks")`, clipped `get_text(clip=rect)` to prove occlusion, per page ink-area integration, 200 dpi page rasters), then reverse tracing of every measured defect to a specific file and line in the tree. Every claim below is anchored to a measurement or a `file:line`. Nothing is inferred.

**Note on style.** This document contains **zero em dashes in its own prose**. The seven that remain are inside quoted evidence: strings copied verbatim from the source tree or extracted from the PDFs, reproduced exactly because a quote that is silently edited is not evidence. See P2-32: the em dash is now a banned character across the entire product surface, and an audit that bans it must obey its own rule.

**Scope correction.** The Google API problem reported alongside these PDFs is **closed and out of scope**: the key had leaked, Google deleted it, a replacement key works. What remains from that incident is not a credential defect but an **observability** defect, filed as P2-27: a hard credential failure was reported to the operator as `google/none` tier exhaustion, which is why nobody knew the key was dead for two entire engagements.

---

## 0. TL;DR: what these two PDFs actually are

Part 1 fixed the pipeline that produced an empty report. It worked. Both of these engagements produced 31 and 32 pages with chapters, exhibits, images, endnotes and appendices. The pipeline runs end to end.

And both deliverables are unshippable, for **eight independent reasons**, of which **six are single lines of code**.

| # | Root cause | One-line location | Visible defect the user reported |
|---|-----------|-------------------|----------------------------------|
| 1 | `column-fill: auto` on a 2-column body | `presentation_designer.py:963` | **"pages only half covered"** and **"the two column thing works at some pages, at some it doesn't"**. Measured max page fill across 63 pages: **46.6 %**. On 6 pages of report A column 2 contains literally **0 words** while column 1 holds 180 to 297. Alternating, deterministic. |
| 2 | `float: right` image is a **sibling preceding** a multicol container | `presentation_designer.py:1274` + `:640` | **"images overlapping text"**. 2,773 pt² overlap covering **83 %** of a text block. Proven by clipped extraction. |
| 3 | `background-color` set on `body`, not on the page canvas | `presentation_designer.py:294` | **"blank white space on all page corners"**. Cream panel is inset inside a white A4 sheet. |
| 4 | Hard-coded arithmetic TOC page numbers | `presentation_designer.py:1177-1187` | **"Table of Contents absolute fuck up"**. 13 of 19 entries point at the wrong page. Off by up to **9 pages**. |
| 5 | `str(val)` / `json.dumps(...)` on Pydantic models into `KeyFinding.content` | `synthesis_lead.py:337-341` and `:318` | **raw Python dicts in prose**. 23 and 25 `{'` occurrences; 40 `accessed_at` leaks; 35 raw `\uXXXX` escapes. |
| 6 | `_findings_by_agent` has no specialist allowlist | `synthesis_lead.py:296-298` | **"Fact Checker" is a client-facing chapter**, listed in the TOC of both reports. |
| 7 | Orchestrator breaks the quality loop on `total_score >= 4.0` and **never reads `approved`** | `orchestrator.py:1113` | The Layer 4 truth gate **fired correctly** on both reports and was **discarded**. |
| 8 | `RELIABLE_ENGINES = "bing,duckduckgo"` | `searxng.py:397` | Report B's entire evidence base is **3 generic sources**; several chapters cite dictionary definitions. |

### 0.1 The finding that outranks the other seven

The Quality Gate already contains exactly the gate this product needs. `quality_gate.py:1161` `_detect_hard_blockers()` scans the report for leaked Python dicts (`{'`), banned filler including the literal string `"insufficient evidence to state implications"`, `Unknown`-as-data, broken URLs, verdict contradictions and dishonest confidence. On **both** of these reports it would have returned a non-empty blocker list. It sets `approved = False` at `quality_gate.py:1440`.

And then:

```python
# orchestrator.py:1112-1114
# Check if score meets threshold (≥ 4.0/5.0)
if current_score.total_score >= 4.0:
    self._log(f"QUALITY: threshold met at iteration {iteration}")
    break  # Quality threshold met
```

The loop exits on the **weighted score** and never consults `current_score.approved`. A report that leaks internals, ships filler and claims dishonest confidence is waved through at iteration 1 with zero fix passes, because ten weighted dimensions averaged above 4.0.

Downstream, `presentation_designer.py:3038` *does* check `approved`, and refuses to lay out an unapproved report. But by then `approved` has been recomputed by nobody: the score object the orchestrator hands to delivery is the one whose `approved=False` was already ignored, and the `max_iterations_reached` escape hatch at `:3043` converts the refusal into `"proceeding with best report (escalation)"`.

**So HYPERION detected every content defect in these two PDFs, wrote them into `QualityScore.gaps`, set `approved=False`, and printed them anyway.** This is not a missing feature. It is one `if` statement reading the wrong field.

### 0.2 Why the specialists did not fill the gaps

The user asked: *"don't our specialists fill the gaps?"* The answer is that the request to fill them is published to a channel nobody is listening on.

`fact_checker.py:976` `_flag_unverified_claims()` publishes `Channel.REQUESTS` / `MessageType.ESCALATION` with `request_type="verify_claims"` to each originating specialist. Every specialist's bus handler matches `request_type` against a fixed literal:

```
competitive_intel.py:277    if request_type == "moat_assessment":
consumer_insights.py:353    if request_type == "personas":
financial_analyst.py:324    if request_type == "lbo_scenario":
financial_analyst.py:328    elif request_type == "key_value_drivers":
innovation_analyst.py:356   if request_type == "emerging_tech":
ma_analyst.py:338           if request_type == "acquisition_targets":
market_analyst.py:291       if request_type == "tam_number":
operations_analyst.py:334   if request_type == "capacity_constraints":
```

`grep -rn "verify_claims" hyperion/agents/ | grep -v fact_checker` returns **nothing**. Not one specialist handles it. The Fact Checker asks 11 agents to close their evidence gaps, 11 agents ignore the message, and the Synthesis Lead then writes `"Insufficient evidence to state implications — this section requires additional research."` into the client deliverable, 4 times in report A and 8 times in report B.

Additionally, by the time the Fact Checker runs, the specialists are in `AgentState.DONE` and their DAG tasks are `COMPLETED`. Even a correctly named handler would be firing into a torn-down agent. Gap filling is not merely unwired, it is **temporally impossible** in the current DAG ordering.

---

## 1. Forensic measurements

### 1.1 Page fill: every page is at most half full

Ink area is the sum of text-block bounding boxes divided by page area. A dense two-column consulting page measures 40 % to 55 % by this metric; the diagnostic signal is not the absolute value but the **ceiling** and the **bimodal distribution**.

**Report A (31 pages):** maximum fill **46.6 %** (p8). Twelve pages below 13 %.

```
p 1  3.2%   14 words   (cover)
p 2 12.7%  152 words   At a Glance
p 3  9.4%   66 words   Table of Contents
p 4 24.4%  234 words   Executive Summary
p 5 10.9%  125 words   Risk Assessment      <-- whole chapter, 125 words
p 6 43.3%  495 words
p 7 17.6%  205 words
p 8 46.6%  518 words   <-- densest page in the document
p 9 19.7%  223 words
p10  8.8%  102 words   Consumer Insights    <-- whole chapter, 102 words
...
p19 10.5%  115 words
p20  8.1%   98 words
p21  7.1%   76 words   Methodology
p27  8.1%   94 words
p28  1.4%   14 words   Appendix: Sources heading, alone on the page
p30 11.1%   54 words
p31  3.2%   30 words   (back cover)
```

**Report B (32 pages):** maximum fill **45.3 %**. The distribution is not merely low, it **alternates**:

```
p 8 40.0%  399 words
p 9 25.0%  181 words
p10 45.3%  398 words
p11  2.9%   26 words   <-- "So What?" box, alone on an A4 page
p12 44.1%  371 words
p13  2.0%   25 words   <-- "So What?" box, alone on an A4 page
p14 39.9%  281 words
p15  2.0%   25 words   <-- "So What?" box, alone on an A4 page
p16 39.4%  299 words
p17  2.0%   25 words   <-- "So What?" box, alone on an A4 page
p18 41.1%  298 words
p19  2.0%   25 words   <-- "So What?" box, alone on an A4 page
p20 42.8%  351 words
p21  2.6%   31 words   <-- "So What?" box, alone on an A4 page
```

Six pages in report B carry **nothing but the implication callout**. Twenty five words on an A4 sheet is 2.0 % ink.

The alternating pattern and the 46 % ceiling are the same defect measured twice. See P2-01 and P2-04.

### 1.1b The two-column layout works on exactly half the pages, and this is measurable

The user's observation, verbatim: *"this two column thing is at some page working at some page it dosent."* Correct, and it is precisely alternating.

Method: split each page at its vertical centre line, sum words whose block bbox lies wholly left of centre (column 1) and wholly right of centre (column 2), and count full-measure blocks separately.

**Report A, chapter body pages:**

| Page | Col 1 | Col 2 | Verdict |
|---|---|---|---|
| p6 | 242 w | 229 w | 2 columns, balanced |
| **p7** | 180 w | **0 w** | **1 column. Right half of the page is empty.** |
| p8 | 254 w | 247 w | 2 columns, balanced |
| **p9** | 198 w | **0 w** | **1 column** |
| p11 | 244 w | 232 w | 2 columns |
| **p12** | 254 w | **0 w** | **1 column** |
| p13 | 230 w | 244 w | 2 columns |
| **p14** | 238 w | **0 w** | **1 column** |
| p15 | 264 w | 246 w | 2 columns |
| **p16** | 297 w | **0 w** | **1 column** |
| p17 | 250 w | 235 w | 2 columns |
| **p18** | 235 w | **0 w** | **1 column** |

**Report B:** p6 `col1 = 207 w, col2 = 0 w`; p9 `col1 = 169 w, col2 = 0 w`; p22 `col1 = 67 w, col2 = 0 w`. Report B's chapters were shorter than one column height, so several render single-column from the first page.

**Every second page of every chapter has column 2 completely empty**, with 180 to 297 words stacked in column 1 on a page that holds roughly 500 in two columns. Column 2 is not thin, it is `0 w`: the right 84.5 mm of the page carries nothing. That is what the user is seeing on the pages where "it doesn't work", and it is the same `column-fill: auto` declaration as P2-01, observed at the fragment boundary instead of at the section boundary.

Mechanism: with `column-fill: auto`, WeasyPrint fills column 1 to the **full available height** before starting column 2. When the multicol block fragments across pages, the **final fragment** receives only the remaining content, which fits inside column 1 alone, so column 2 is never started. The first fragment therefore looks correct (both columns full to the bottom) and the last fragment looks broken (one column, half a page of white). A chapter spanning 2 pages is 50 % correct by construction. This also explains why the ink ceiling in §1.1 is 46.6 %: 46.6 % is a genuinely full two-column page under this metric, and no page ever exceeds it because no page is ever more than two full columns.

`column-fill: balance` distributes the remaining content across both columns on every fragment, including the last, which fixes both symptoms with one word.

### 1.2 Image occlusion, proven by clipped extraction

Method: for each page, intersect every `get_image_info()` bbox with every `get_text("blocks")` bbox; where the intersection exceeds 100 pt², re-extract the text inside the image rectangle. Text returned from inside an opaque raster is text the reader cannot see.

| Report | Page | Chapter | Image bbox | Overlap | % of block hidden | Text destroyed |
|---|---|---|---|---|---|---|
| A | 8 | Sustainability Assessment | `Rect(351.99, 210.43, 546.75, 340.27)` | 2,773 pt² | **83 %** | 5 lines of prose |
| A | 11 | Technology Architecture | (same geometry) | 2,773 pt² | 83 % | 5 lines of prose |
| B | 8 | Market Landscape | (same geometry) | 2,773 pt² | 83 % | 5 lines of prose |
| B | 14 | Technology Architecture | (same geometry) | 2,773 pt² | 83 % | 5 lines of prose |

The geometry is **identical on every occurrence**, which is the signature of a deterministic layout rule rather than a content-dependent overflow. See the p8 raster of report B: the right column's lines 1 to 5 run **underneath** a candlestick chart photograph. Cause is P2-02.

### 1.3 Cover bleed and right-edge overflow

* Report A page 1: content bounding box `x0 = -328.0 … x1 = 934.0` on a **595.28 pt** wide page. The cover composition extends 328 pt off the left edge and 339 pt off the right.
* Report B page 14: content reaches `x1 = 596.0` against a page width of `595.28`. Text is clipped at the trim edge.

### 1.4 Content integrity counters

| Metric | Report A | Report B |
|---|---|---|
| Em dashes in client-visible text | **51** | **21** |
| `{'` Python dict openings in prose | **23** | **25** |
| Literal `"Insufficient evidence to state implications"` | **4** | **8** |
| `hallucinat*` mentions in client-visible text | **8** | 1 |
| `[verified citation]` placeholder shipped as a source | **1** | 0 |
| Raw serialization key `accessed_at` in prose | 0 | **40** |
| Unescaped `\u2026` / `\u20xx` JSON escapes in prose | 0 | **35** |
| Distinct sources in the entire evidence base | **4** | **3** |

### 1.5 Table of contents drift, measured

Report A TOC (page 3) versus the actual page each heading appears on:

| TOC entry | TOC says | Actually on | Error |
|---|---|---|---|
| At a Glance | 2 | 2 | ok |
| Executive Summary | 4 | 4 | ok |
| Risk Assessment | 5 | 5 | ok |
| Operational Feasibility | 6 | 6 | ok |
| Sustainability Assessment | 7 | **8** | -1 |
| Consumer Insights | 8 | **10** | -2 |
| Technology Architecture | 9 | **11** | -2 |
| Regulatory Environment | 10 | **13** | -3 |
| Financial Viability | 11 | ~15 | -4 |
| Strategic Options | 12 | ~17 | -5 |
| Market Landscape | 13 | ~19 | -6 |
| **Fact Checker** | 14 | ~20 | -6, and should not exist at all |
| **Risk Analysis** | 15 | **never rendered** | phantom entry |
| Methodology | 16 | **21** | -5 |
| Endnotes | 17 | **22** | -5 |
| Technical Appendix | 18 | **23** | -5 |
| Appendix: Sources | 19 | **28** | **-9** |

Thirteen of nineteen entries are wrong, and one entry ("Risk Analysis") points at a chapter the template skipped. Report B has the same structure with the same defect. Cause is P2-05 and P2-06.

### 1.6 Evidence corpus collapse

**Report B, complete source list (3 sources):**

1. `en.m.wikipedia.org/wiki/Manufacturing`
2. `britannica.com/technology/manufacturing`
3. `investopedia.com/terms/m/manufacturing.asp`

All three are generic encyclopedia entries for the *word* "manufacturing". All three carry `"credibility": "industry_report"` in the leaked JSON, and elsewhere in the document dictionary sites are labelled `"credibility": "government"`. Chapters in report B cite:

* Merriam-Webster definition of "EMERGING"
* Cambridge Dictionary entry for "MOBILITY"
* `iciba.com` (a Chinese/English dictionary)
* `health.harvard.edu`
* Motability UK (a British disability car-lease scheme)

for a report about a chemicals and hardware manufacturing ramp. **Report A** has 4 sources, repeated 20 times across the endnotes, and the At a Glance page states `4 sources · 35 data points` while the cover states `Confidence: HIGH`.

This is consistent with the SearXNG Docker log supplied with the engagements: a DuckDuckGo CAPTCHA storm followed by `HTTP error 403 (suspended_time=86400)`. See P2-22 through P2-26.

### 1.7 Self-referential and placeholder text shipped to the client

Verbatim strings found in client-visible prose:

```
$XB
$YB-$ZB
Source: [verified citation]
[new source for TAM]
the section previously lacked a key insight
TAM triangulation previously resulted in a parse error
TAM: {'name': 'TAM (Triangulated)', 'value': 'Parse error', 'unit': '$', 'low_estimate': None, ...
Size: Unknown - Data Sparse. Growth: Unknown. Competition: high. Attractiveness: 4/10.   (x2 verbatim)
CRITICAL: 17 Hallucinated Citations Detected
43 unverified claims
Data accuracy is critically low (40% verified)
So What? Insufficient evidence to state implications — this section requires additional research.
```

`"the section previously lacked a key insight"` and `"TAM triangulation previously resulted in a parse error"` are **the quality-iteration feedback loop talking to itself in the client's document**. See P2-14.

### 1.8 Duplicate paragraph census

Verbatim paragraph repetition inside a single chapter, report B: Competitive Landscape, Risk Assessment, Operational Feasibility, Market Landscape, Technology Architecture, Sustainability Assessment. Report B page 8 shows the paragraph `"Size: Unknown - Data Sparse. Growth: Unknown. Competition: high. Attractiveness: 4/10. High-purity chemicals required for lab-scale synthesis…"` printed **twice**, 600 pt apart, on the same page. See P2-13.

### 1.9 Empty rendering

* Report B Methodology page prints **11 bullet glyphs with no text**: `report.agents_used` contains 11 empty strings, and `presentation_designer.py:1355-1357` loops them unguarded.
* Report B "Risk Analysis" and "Appendix: Sources" appear in the TOC pointing at pages whose only content is the `<h2>`.
* Report A page 28 is the string `Appendix: Sources` twice (heading plus running header) and nothing else: 14 words, 1.4 % ink.

---

## 2. Defect register

Severity: **S1** blocks delivery, **S2** materially degrades the deliverable, **S3** quality or maintainability.

### Group A. Paged-media layout (WeasyPrint)

---

#### P2-01 `column-fill: auto` guarantees every section page is half empty (S1)

**Location:** `hyperion/agents/delivery/presentation_designer.py:960-964`

```css
.section-body {
    column-count: 2;
    column-gap: 7mm;
    column-fill: auto;  /* balance columns on the final page of the section */
}
```

The comment states the intent and the value contradicts it. In CSS Multi-column, `column-fill: balance` distributes content evenly across columns; `column-fill: auto` fills column 1 to the full column height, then starts column 2. A chapter whose prose is shorter than one full column height therefore renders **column 1 full, column 2 empty**, and the page measures ~46 % ink no matter how much prose the model wrote.

This is the single defect behind "pages only half covered (huge dead space)" **and** behind "the two column thing works at some pages, at some it doesn't". Two symptoms, one declaration:

* **At the section boundary** (a chapter whose prose is shorter than one column height): column 1 fills, column 2 is empty, the page measures 40 % to 46 % ink. Report B p6, p9, p22.
* **At the fragment boundary** (a chapter spanning several pages): the first fragments fill both columns and look correct; the **final fragment** receives only the remainder, which fits in column 1, so column 2 is never started and the right 84.5 mm is blank. Report A p7, p9, p12, p14, p16, p18, all measuring `col2 = 0 w` against `col1 = 180 to 297 w`. Full table in §1.1b.

A reader flipping through report A sees a correct spread, then a broken one, then a correct one, alternating for 14 pages. That is not intermittent behaviour, it is deterministic behaviour observed at two different points in the same fragmentation sequence.

**Fix:** `column-fill: balance`. One word. Do not attempt to compensate by setting explicit column heights, and do not compensate by inflating word counts: the content quantity is not the defect.

**Verification:** re-render both fixtures and assert **both**:
1. `max(page_fill) >= 0.62` and `median(body page fill) >= 0.45`, and
2. **column balance**: on every body page, `min(col1_words, col2_words) >= 0.35 * max(col1_words, col2_words)`. This is the assertion that actually catches the `col2 = 0` pages, because an ink-fill threshold alone can be satisfied by one very full column. See P2-G3b.

---

#### P2-02 Floated image adjacent to a multicol container overlaps the text (S1)

**Location:** `presentation_designer.py:1273-1275` (markup) with `:640-645` (CSS)

```html
{% if section_images[section.id] %}
<img src="..." class="section-image" alt="...">
<p class="section-image-caption">...</p>
{% endif %}

<div class="section-body">   <!-- column-count: 2 -->
    {{ section.body | md_to_html }}
</div>
```

```css
.section-image { width: 40%; float: right; margin: 0 0 8px 16px; page-break-inside: avoid; }
```

A `float: right` element that is a **preceding sibling** of a multi-column container is laid out in the containing block, not inside the column flow. WeasyPrint does not shorten the column boxes around an out-of-flow float that originates outside them, so column 2's line boxes are generated at full width and the raster is composited over them. Result: 2,773 pt², 83 % of a text block, on **every** section page carrying an image, with byte-identical geometry.

The CSS comment at `:967-969` even asserts the opposite: *"the section image sits outside the columned div in the HTML, so it is already full-measure."* It is outside the columned div, which is exactly why it collides with it.

**Fix, in order of preference:**
1. Move the figure **inside** `.section-body` and give it `column-span: all`, so it becomes a full-measure band in the column flow. This is the only construction WeasyPrint honours reliably.
2. Or take the image out of the prose flow entirely: a dedicated full-width plate band above `.section-body`, `float: none`, `width: 100%`, `max-height: 62mm`.

Under no circumstances keep `float` adjacent to `column-count`.

**Verification:** T-02 below (automated occlusion assertion, zero tolerance).

---

#### P2-03 Page canvas is white because the background is on `body` (S1)

**Location:** `presentation_designer.py:294` (`body { background-color: {cream} }`) and `:275` (`html { counter-reset: exhibit; }`)

The CSS Backgrounds spec propagates a background to the page canvas **only from the root element**. `html` here carries a counter reset and no background, so the cream fills the `body` box, which is inset by `@page { margin: 25mm 15mm 25mm 19mm }`. The result is a cream panel floating on a white A4 sheet: exactly the reported "blank white space on all page corners". Visible in every raster.

**Fix:**
```css
html { counter-reset: exhibit; background-color: {cream}; }
@page { background: {cream}; }
body { background-color: transparent; }
```
Set it in both places. `@page` background covers the margin boxes; root propagation covers the canvas. Do not attempt to fake it with an absolutely positioned full-bleed div: that breaks the running headers.

**Verification:** sample the pixel at (2 pt, 2 pt) and at (page_w - 2, page_h - 2) on every page of a rendered fixture; assert it equals the cream palette value on all four corners of all pages.

---

#### P2-04 The implication callout is orphaned onto its own page (S1)

**Location:** `presentation_designer.py:1338-1340`, with `:983-991` (`.implication-box { column-span: all }`) and `:845-847` (`.page-break { page-break-before: always }`)

`.implication-box` is the last child of the section wrapper, after the multicol `.section-body` and after the exhibit figures. Because `column-fill: auto` (P2-01) drives the column block to consume a full page height, and because `column-span: all` forces the callout out of the column flow, the callout is placed after the column block's last fragment. With a full-height column block, that placement lands on the next page, alone. Six pages in report B are exactly this: 25 words, 2.0 % ink.

Note this defect is **caused by** P2-01 and will partially self-resolve when `column-fill: balance` is set, but it must be fixed independently because a section whose prose exactly fills a page will still orphan the callout.

**Fix:** wrap the last prose fragment and the callout in a `break-inside: avoid` group, or simpler and more robust: give `.implication-box` `break-before: avoid` and set `orphans/widows` on the column block so at least 4 lines of prose accompany it. Reject any page whose only content is `.implication-box` in the render-time page audit (P2-08).

**Verification:** assert no page in any fixture contains fewer than 90 words unless it is the cover, the back cover, or a full-bleed exhibit plate.

---

#### P2-05 Table of contents page numbers are arithmetic fiction (S1)

**Location:** `presentation_designer.py:1175-1189`

```html
<tr><td>At a Glance</td><td>2</td></tr>
<tr><td>Executive Summary</td><td>4</td></tr>
{% for section in report.sections %}
<tr><td>{{ section.title }}</td><td>{{ loop.index + 4 }}</td></tr>
{% endfor %}
<tr><td>Risk Analysis</td><td>{{ report.sections | length + 5 }}</td></tr>
<tr><td>Methodology</td><td>{{ report.sections | length + 6 }}</td></tr>
...
```

The template asserts **one page per section**. `page_budget.py` (the module written specifically to model this) documents that a section occupies 3 to 4 pages, and its own docstring says so at `page_budget.py:24-31`: *"the production CSS sets `page-break-before: always` on every section, so a section cannot share a page with its neighbour."* The TOC ignores the module that exists to answer this question.

Measured error: up to **9 pages** (§1.5). The comment above the At a Glance block at `:1105-1107` even admits the problem in passing: *"so they cannot drift from the body the way the hardcoded TOC page numbers below already do."* The defect was documented and left in place.

**Fix:** use real cross references. WeasyPrint supports CSS generated content page references:

```html
<tr><td><a href="#sec-{{ loop.index }}">{{ section.title }}</a></td>
    <td class="toc-page"></td></tr>
```
```css
.toc-table a::after { content: target-counter(attr(href), page); }
```
and give every section wrapper `id="sec-{{ loop.index }}"`. Emit the row **only if** the target exists (see P2-06). Delete every hardcoded integer and every `+ N` expression from the TOC.

**Verification:** T-05, parse the rendered TOC and assert every entry's stated page equals the page on which the corresponding `<h2>` is drawn.

---

#### P2-06 TOC lists chapters that are never rendered (S2)

**Location:** `presentation_designer.py:1182` versus `:1344`

The TOC emits a `Risk Analysis` row unconditionally. The chapter is guarded: `{% if report.risk_analysis %}`. In both fixtures `report.risk_analysis` was falsy, so the TOC advertises a chapter that does not exist and every subsequent page number is shifted by its absence. Same class of defect for `Appendix: Sources` in report B, where the chapter renders as a bare heading.

**Fix:** every TOC row must be emitted from the same condition that emits the chapter. Build the TOC from a single list comprehension over the actual emitted blocks, not from a hand-maintained parallel list.

---

#### P2-07 Cover composition bleeds 328 pt off the left edge (S2)

**Location:** cover block, `presentation_designer.py` cover section, plus `.cover-*` CSS

Report A page 1 content bbox is `x0 = -328.0 … x1 = 934.0` on a 595.28 pt page. Whatever the intended full-bleed effect, negative-x content is a PDF/A hazard and a print hazard, and pikepdf's PDF/A-2b pass in `output/pdf_postprocess.py` does not clip it.

**Fix:** clip the cover to the trim box. If full bleed is intended, use a dedicated `@page :first { margin: 0 }` and size the cover art to exactly `210mm x 297mm`, not to an overflowing transform.

---

#### P2-08 There is no render-time page audit (S1)

**Location:** `output/render.py:845-850`, `agents/delivery/render_engine.py:971-975`

Both call `page_count_verdict()`. That function checks **how many pages** there are. Nothing checks **what is on them**. A 31-page document where 12 pages are under 13 % ink, 6 pages carry 25 words, 4 pages have text under a photograph and all 4 corners are the wrong colour passes every existing assertion.

**Fix:** add `hyperion/output/page_audit.py`, run it inside `render.py` immediately after PDF bytes exist, and make it **fail closed**. Minimum assertions, all on the produced PDF via PyMuPDF:

| Assertion | Threshold |
|---|---|
| image / text bbox intersection | `== 0` pt², excluding declared full-bleed plates |
| words per body page | `>= 90` |
| ink fill per body page | `>= 0.30`, median `>= 0.45` |
| column balance per body page | `min(col) >= 0.35 * max(col)` |
| content bbox within trim box | `0 <= x0`, `x1 <= page_w` |
| corner pixel colour | equals theme background on all 4 corners of all pages |
| TOC stated page vs actual heading page | exact match for every entry |
| `{'` or `{"` in extracted text | `== 0` |
| U+2014 in extracted text | `== 0` |

A failing audit must raise, not warn. See DoD gate P2-G1.

---

### Group B. Content integrity: serialized objects in prose

---

#### P2-09 The bus handler serializes Pydantic models into report prose (S1)

**Location:** `hyperion/agents/synthesis_lead.py:316-341`

```python
for key in analysis_keys:
    if key in payload:
        try:
            summary = json.dumps(payload[key], default=str)[:3000]      # line 318
        ...
        for title_key, label in [("tam_triangulated", "TAM"),
                                 ("dcf_valuation", "DCF Valuation"), ...]:
            val = analysis_data.get(title_key)
            if val is not None:
                val_str = str(val)                                      # line 337
                if len(val_str) > 120:
                    val_str = val_str[:117] + "..."
                headlines.append(f"{label}: {val_str}")
```

Two independent leaks in one block:

1. **Line 337, `str(val)`.** `val` is a `DataPoint`/`Valuation` Pydantic model. `str()` on it yields the field repr. Truncating at 117 characters produces the exact string the user pasted:
   `DCF Valuation: {'name': 'DCF Valuation', 'value': '$12.5B - $38.9B', ...`
   and, from report B page 8:
   `TAM: {'name': 'TAM (Triangulated)', 'value': 'Parse error', 'unit': '$', 'low_estimate': None, 'high_estimate': None, 'bas...`

2. **Line 318 plus line 365-366, `summary` becomes the body.**
   ```python
   content = summary
   if headlines:
       content = "\n".join(headlines) + "\n\n" + summary
   ```
   `summary` is up to 3,000 characters of `json.dumps(..., default=str)`. That is what produced the 40 `accessed_at` occurrences and the 35 `\u2026` escapes in report B, including whole `sources` arrays with `"id": "src_000"` printed as chapter prose. Visible verbatim on the p8 raster.

**Fix:**
1. Add a single canonical presenter, `hyperion/output/display.py::display_value(obj) -> str`, that accepts a `DataPoint`, `Valuation`, dict, list or scalar and returns **prose**. Never `str()`, never `repr()`, never `json.dumps` for anything reachable by the renderer.
2. Replace line 337 with `display_value(val)`.
3. Delete the `summary` path entirely. A JSON dump is not analysis. If a specialist publishes a payload with no `finding`, the correct behaviour is to raise a structured gap (P2-16), not to paste the payload.
4. Make `KeyFinding.content` reject serialized objects at the schema boundary: a Pydantic `field_validator` that raises if the value matches `r"\{['\"]\w+['\"]\s*:"`. Fail at construction, not at render.

---

#### P2-10 `clean_dict_repr` cannot fire on the strings that actually leak (S1)

**Location:** `hyperion/output/render.py:183-224`

```python
if text.strip().startswith("{") and "'" in text:
```

Every leaked string in both PDFs is of the form `LABEL: {'…}` (`TAM: {'name': …`, `DCF Valuation: {'name': …`, `Build vs Buy: {'…`). None of them **start** with `{`, so the guard is false and the raw repr is returned unchanged at line 224.

Three further defects in the same function:
* `json_str = text.replace("'", '"')` corrupts any value containing an apostrophe, and `None`/`True` are not JSON literals, so `json.loads` raises on most real payloads and the function silently falls through.
* The regex fallback `r"'([\w_]+):\s*'([^']*)'"` only matches string-valued keys, so numeric and nested fields vanish.
* Final fallback is `text[:197] + "..."`, which **ships the leak, truncated**. That is precisely what the user saw.

**Location of the second half of this defect:** `render.py:137-142` and `presentation_designer.py:1269`. The filter is registered but applied to **exactly one field** in the whole template:

```html
<div class="key-insight-box">{{ section.key_insight | clean_dict_repr }}</div>
```

`section.body`, `section.implications`, `report.executive_summary`, `report.recommendation_rationale`, `finding.title`, `finding.content`, the At a Glance cells and every appendix table cell are unfiltered. The So What box at `:1338` is `<strong>So What?</strong> {{ section.implications }}` with no filter at all.

**Fix:**
1. Rewrite as `hyperion/output/display.py::humanize(text)`: detect a dict or model repr **anywhere** in the string with a compiled regex, parse with `ast.literal_eval` (correct tool for a Python repr; `json.loads` is the wrong tool), and render `Key: Value · Key: Value`. On failure, **raise**, do not truncate and ship.
2. Register it as the Jinja **default finalizer** on the environment (`Environment(finalize=humanize)`) so no field can be forgotten, rather than as an opt-in filter applied at 1 of ~40 sites.
3. Keep the explicit filter for readability but make the finalizer the guarantee.

---

#### P2-11 Section body falls back to concatenating raw finding content (S1)

**Location:** `synthesis_lead.py:1177` and the swallowed exception at `:1235`

```python
section_body = "\n\n".join(f.content for f in findings)   # ~1177
...
except (ValueError, AttributeError, RuntimeError):
    pass  # Use fallback concatenation
```

When the narrative LLM call fails or returns unusable output, the section body becomes the concatenation of `KeyFinding.content` values, which after P2-09 are JSON dumps. This is the mechanism that put 3,000-character JSON blobs into seven chapters of report B, and the `except: pass` is why no operator was told.

**Fix:** delete the fallback. A section with no synthesized narrative is a **gap**, and gaps go to the gap-closure loop (P2-16), not to the page. If the loop cannot close it, the chapter is dropped and the omission is declared in Limitations. Log the exception at ERROR with the agent and section identity; never `pass`.

---

#### P2-12 The Fact Checker becomes a client-facing chapter (S1)

**Location:** `synthesis_lead.py:294-298` with the section builder at `:1245`

```python
if agent_name not in self._findings_by_agent:
    self._findings_by_agent[agent_name] = []
self._findings_by_agent[agent_name].append(finding)
```

`_findings_by_agent` is keyed by **any** sender on `Channel.FINDINGS`. Sections are then built one per key (`:1245`). The Fact Checker publishes findings, so `"Fact Checker"` becomes a chapter, appears in the TOC of both reports (§1.5), and its content is quoted into At a Glance and the Executive Summary:

> *"…and 17 hallucinated citations break evidence chains."*
> *"CRITICAL: 17 Hallucinated Citations Detected"*
> *"Data accuracy is critically low (40% verified)"*

The client is being handed HYPERION's internal QA log as a chapter of their strategy report.

**Fix:**
1. Add an explicit allowlist: `SECTION_PRODUCING_AGENTS: frozenset[AgentName]` containing the 11 specialists only. `_findings_by_agent` accepts findings from any agent (they are useful input) but the section builder iterates the allowlist.
2. Route Fact Checker output to `FinalReport.fact_check_report` only, which the Technical Appendix may summarize **quantitatively** ("43 of 72 claims independently verified") and must not quote.
3. Add a lexical blocklist to the Layer 4 gate: the words `hallucinat*`, `unverified claim`, `fact checker`, `quality gate`, `iteration`, `parse error`, `data sparse` must not appear in `executive_summary`, `recommendation_rationale`, any `key_insight`, any `implications`, or any section `title`.

See also P2-19: the number 17 is itself wrong.

---

#### P2-13 Verbatim paragraph duplication inside a chapter (S2)

**Location:** `synthesis_lead.py:1177` (concatenation with no dedup) and the specialist emit paths

Six chapters of report B repeat paragraphs verbatim, twice on the same page in the Market Landscape case (§1.8). Cause: multiple `KeyFinding` objects carry the same generated `content` (one per market segment, all reading `"Size: Unknown - Data Sparse. Growth: Unknown…"`), and the concatenation path has no deduplication.

**Fix:** normalized-hash dedup on paragraphs at the point of assembly, and a `page_audit` assertion that no normalized paragraph of >= 12 words appears twice in the document.

---

#### P2-14 Quality-iteration feedback is written into the client deliverable (S1)

**Location:** `synthesis_lead.py::iterate_on_quality` / `_apply_quality_feedback`

Strings shipped to the client:

```
the section previously lacked a key insight
TAM triangulation previously resulted in a parse error
$XB
$YB-$ZB
Source: [verified citation]
[new source for TAM]
```

The iteration prompt hands the LLM the Quality Gate's fix instructions, the LLM narrates the instruction instead of executing it, and the result is stored as content. `$XB` and `$YB-$ZB` are the prompt's own shape placeholders (see the `⟨…⟩` convention at `synthesis_lead.py:168-172`) surviving into the document.

**Fix:**
1. Post-validate every iteration output against a **meta-text blocklist**: `previously`, `the section`, `this section requires`, `[.*citation.*]`, `[new source`, `\$[XYZ]B`, `⟨`, `parse error`, `placeholder`. On match, discard the iteration output and retry at a higher tier; on second failure, escalate as a gap.
2. Never let an iteration **reduce** information. Compare before/after: if the new text is shorter and contains a blocklist token, keep the old text and raise the gap.
3. Add these to `_BANNED_FILLER` at `quality_gate.py:1152-1159`.

---

#### P2-15 Confidence is incoherent across the document (S1)

**Location:** `quality_gate.py:1244-1256` (the check) and `synthesis_lead.py` confidence calibration

Report A cover says `Confidence: HIGH`. At a Glance says `4 sources · 35 data points`. The WHY block on the same page says the recommendation is CONDITIONAL because *"critical sections lack verified sources"*. Eight of ten agents reported Low or Medium confidence.

The existing dishonest-confidence blocker only fires when `void_ratio >= 0.34 or total_sources < 3`. Report A had 2 of 10 unsourced sections (0.20) and 4 sources, so it passed by 1 source and 1.4 sections.

**Fix:**
1. Confidence must be **derived**, not asserted: `HIGH` requires `total_sources >= 12`, `>= 3` independent domains, `void_ratio == 0`, and no critical dimension below 4. Otherwise clamp to MEDIUM or LOW. Compute this in one function, `derive_confidence(report)`, and have the cover, At a Glance, Executive Summary and Technical Appendix all read it.
2. Tighten the blocker: `HIGH` with `total_sources < 12` or `void_ratio > 0` is a hard blocker.
3. Add a cross-surface consistency blocker: the confidence word on the cover, in At a Glance and in the appendix must be the same token.

---

### Group C. Gap closure: the specialists must fill gaps, not print placeholders

---

#### P2-16 `"Insufficient evidence to state implications"` is a hardcoded default (S1)

**Location:** `synthesis_lead.py:~1235`

```python
implications=(key_finding.implications
    or "Insufficient evidence to state implications — this section requires additional research.")
```

This is the string the user quoted. It shipped 4 times in report A and 8 times in report B. It is also on the Quality Gate's own banned-filler list at `quality_gate.py:1158`, which means the system writes a string it knows is forbidden and then fails to act on the detection (P2-20).

The user's question, restated precisely: *why is a placeholder the fallback instead of a re-dispatch?*

**Fix. This is a policy change, not a string change.**

Introduce a first-class gap object and a closure loop:

```python
# hyperion/schemas/models.py
class AnalysisGap(BaseModel):
    id: str
    section_id: str
    agent: AgentName
    field: Literal["key_insight", "body", "implications", "sources", "datapoint"]
    question: str            # the specific question that must be answered
    attempts: int = 0
    resolved: bool = False
    resolution: str | None = None
```

Rules:
1. **No placeholder may ever be constructed.** Every site that currently emits a default string raises an `AnalysisGap` instead.
2. **Closure loop, before the Quality Gate, owned by the Engagement Director:**
   * Round 1: re-dispatch the gap to the **originating specialist** with the specific question, at one tier **above** its normal tier, with `urgency=HIGH`.
   * Round 2: if unresolved, dispatch to a **different** specialist (cross-check) plus a targeted search with a reformulated query.
   * Round 3: if still unresolved, escalate to STRONG/DEEP tier synthesis with the full section context.
   * Maximum 3 rounds per gap, maximum 2 concurrent gap rounds per agent, total wall-clock budget bounded by the existing engagement timeout.
3. **If a gap survives all 3 rounds**, the section is **not** shipped with a placeholder. Either:
   * the field is **omitted** (no So What box at all, which is honest and invisible), and
   * the omission is recorded in `FinalReport.limitations` with the specific question that could not be answered.
4. `_BANNED_FILLER` becomes a **construction-time** validator on `AnalysisSection` and `KeyFinding`, not just a render-time scan. Pydantic raises. The string becomes unrepresentable.

**Verification:** T-07, grep the rendered PDF text for every `_BANNED_FILLER` phrase; assert 0. And a unit test asserting `AnalysisSection(implications="Insufficient evidence to state implications")` raises `ValidationError`.

---

#### P2-17 The gap-fill request is published to a channel with no subscribers (S1)

**Location:** `fact_checker.py:976-1004` versus the 11 specialist handlers

Documented in §0.2. `request_type="verify_claims"` is handled by nobody. The Fact Checker's own docstring at `fact_checker.py:50` lists *"6. Flag unverified claims to originating specialist"* as a designed step. Step 6 has never done anything.

**Fix:**
1. Implement a shared `BaseSpecialist._handle_verify_claims(claims)` on the specialist base class so all 11 inherit it, rather than 11 copies of a literal comparison.
2. Add a bus-level guard: `bus.publish` with `msg_type=ESCALATION` and a `request_type` must assert at least one live subscriber matches, and log at ERROR when it does not. A message with no possible recipient is a bug, and the bus is the only place that can see it.
3. Keep the specialist agents **alive** (subscribed, not torn down) until the gap-closure loop closes, which requires the DAG change in P2-18.

---

#### P2-18 Specialists are dead before verification runs (S1)

**Location:** `orchestrator.py` DAG construction, specialist tasks reach `TaskStatus.COMPLETED` before `task_fact_check` and `task_quality_gate`

Even with P2-17 fixed, a `verify_claims` request arrives at an agent whose task is `COMPLETED` and whose state is `DONE`.

**Fix:** add an explicit `GAP_CLOSURE` DAG phase between `fact_check` and `quality_gate`. Specialist tasks move to a new `TaskStatus.AWAITING_FOLLOWUP` rather than `COMPLETED` until that phase closes. Only then are they finalized. This is the structural precondition for "specialists fill the gaps".

---

### Group D. Fact Checker

---

#### P2-19 The hallucination detector is a false-positive factory (S1)

**Location:** `fact_checker.py:1031-1052`

```python
for source in claim.verification_sources:
    source_data = (source.key_data or "").lower()
    if claim_lower in source_data or any(
        word in source_data for word in claim_lower.split() if len(word) > 4
    ):
        source_contains_data = True
        break

if not source_contains_data and claim.verification_sources:
    claim.is_hallucinated_citation = True
```

`source.key_data` is populated at `fact_checker.py:701` as `getattr(result, "snippet", "")`. When the search snippet is empty, which is the normal case for the Bing-only corpus these engagements had, `source_data` is `""`, no word matches, and **every claim citing that source is labelled a hallucinated citation**. Report A's "17 hallucinated citations" is therefore not a measure of model hallucination; it is a measure of **missing snippets**.

Additional flaws in the same block:
* Substring matching (`word in source_data`) with a 4-character floor matches "india" inside "indian", but also "market" inside "supermarket", and "rate" inside "corporate". It is the same substring-inflation defect that `evidence_scorer._score_relevance` was already fixed for (see the history comment at `evidence_scorer.py:195-201`). The fix was not propagated here.
* `if not claim.verification_sources and claim.status != ClaimStatus.UNVERIFIED` at `:1022` labels any claim with zero sources a hallucination, conflating "we did not look" with "the model invented a citation".

**Fix:**
1. Only run chain validation when `key_data` is **non-empty and derived from fetched page content**, not from a SERP snippet. If content was never fetched, the correct status is `UNVERIFIABLE`, a distinct third state, not `HALLUCINATED`.
2. Token-boundary matching with a stopword list, reusing `evidence_scorer`'s corrected matcher. Do not reimplement it.
3. Require **two** independent signals before asserting a hallucinated citation: no numeric or named-entity overlap **and** the URL failing a liveness check.
4. Separate the three states in the schema: `VERIFIED` / `UNVERIFIABLE` / `CONTRADICTED` / `HALLUCINATED`, and never aggregate `UNVERIFIABLE` into the hallucination count.

---

#### P2-20 The Fact Checker runs on FAST tier and cannot be escalated (S2)

**Location:** `fact_checker.py:101` (`model_tier=ModelTier.FAST`), prompt at `:215` (*"BE FAST, NOT THOROUGH"*)

The user asked directly: *"can't we handle it in a better way, giving it to better models?"* Yes, and the architecture already supports it. Verification is the one task where a wrong answer is maximally expensive: a false hallucination flag propagates into At a Glance, the Executive Summary, the confidence calibration and the TOC.

**Fix:** a **two-stage** verification ladder.
* Stage 1 (FAST, unchanged): triage all claims. Cheap, high recall.
* Stage 2 (**STRONG**, `urgency=HIGH`): re-adjudicate only the claims Stage 1 flagged as `HALLUCINATED` or `CONTRADICTED`, with the full fetched source text in context. Stage 2's verdict is authoritative.
* Only Stage 2 verdicts may affect `verification_rate`, confidence, or the Technical Appendix.
* Budget: Stage 2 sees only flagged claims, typically 10 to 20 % of the total, so the marginal cost is bounded.

This directly answers the user's question and it removes the incentive to hide the numbers rather than fix them.

---

#### P2-21 Contradiction detection compares metadata strings (S2)

**Location:** `fact_checker.py:896-972`, rendered by `presentation_designer.py::technical_appendix_html`

Report A's Technical Appendix contains roughly 20 contradiction rows whose two sides are the literal strings `"Confidence: low"` and `"Confidence: low"`, or two dict reprs, "resolved" by `"source count"` with the note `"equal evidence weight: 0.00"`, with the same boilerplate repeated for every pair. This is O(n²) over the wrong inputs.

**Fix:**
1. Extract claims for comparison **before** any metadata is folded in; never compare `Confidence: …` strings.
2. A contradiction requires two claims about the **same quantity** with **non-overlapping** values. Require a shared subject and a numeric or categorical conflict. No numeric conflict, no contradiction row.
3. Suppress rows whose resolution weight is 0.00: an unresolved tie is not a resolution and must not be presented as one.
4. Cap the appendix table at the 10 highest-weight contradictions and state the total.

---

### Group E. The truth gate that fires and is ignored

---

#### P2-22 The orchestrator exits the quality loop on score and ignores `approved` (S1, highest priority)

**Location:** `orchestrator.py:1112-1115`

Full analysis in §0.1. `quality_gate.py:1434-1441` computes hard blockers, sets `approved = False`, prepends the blockers to `gaps`. `orchestrator.py:1113` breaks on `total_score >= 4.0` without reading `approved`. Both fixtures were waved through.

**Fix:**
```python
if current_score.approved:
    break
```
and nothing else. The score threshold is already part of `_determine_approval` at `quality_gate.py:1148`. Reading two things where one is authoritative is how this defect happened.

Then handle the non-approved paths explicitly:
* blockers present and iterations remaining: run the **gap-closure loop** (P2-16) targeting the blockers, then re-score.
* blockers present and iterations exhausted: **do not render a client PDF**. Emit an internal diagnostic document and a non-zero exit. See P2-23.

---

#### P2-23 `max_iterations_reached` is a universal bypass (S1)

**Location:** `presentation_designer.py:3038-3059`

```python
if self._quality_score and not self._quality_score.approved:
    ...
    if self._quality_score.max_iterations_reached:
        quality_note += " — max iterations reached, proceeding with best report (escalation)"
```

The one component that checks `approved` has an unconditional override. Since `orchestrator.py:1150` sets `max_iterations_reached = True` on every non-approved run that reaches the iteration cap, the override is the normal path.

**Fix:** partition blockers by class.
* **Cosmetic or thin-evidence** deficits (low score, few sources): proceed, and declare the limitation on the page.
* **Integrity** blockers (leaked object, banned filler, verdict contradiction, dishonest confidence, broken URL, meta-text): **never** proceed. There is no acceptable version of shipping `{'name': 'DCF Valuation'` to a client.

Implement as `QualityScore.integrity_blockers: list[str]` distinct from `gaps`, and make `presentation_designer` refuse when it is non-empty regardless of `max_iterations_reached`.

---

#### P2-24 Hard blockers scan the model, not the rendered document (S2)

**Location:** `quality_gate.py:1173-1185`

The blocker scan serializes `executive_summary`, `recommendation_rationale`, `key_findings` and `sections`. It does **not** see: the At a Glance grid, the Technical Appendix contradiction table, the Endnotes, the Methodology bullets, the Appendix: Sources table, or chart captions. Report A's `[verified citation]` placeholder and the 20 degenerate contradiction rows are all in surfaces the gate cannot see.

**Fix:** run the integrity scan **twice**: once on the model (fast, pre-render) and once on the **extracted text of the produced PDF** (authoritative, post-render, inside `page_audit`, P2-08). The PDF is the artifact the client receives; it is the only correct place for a final gate.

---

#### P2-25 The content-aware stop fires before any fix attempt (S2)

**Location:** `orchestrator.py:1119-1126`

```python
report_sources = getattr(current_report, "total_sources", 0)
if report_sources < source_floor:      # source_floor = 3
    current_score.max_iterations_reached = True
    break
```

The reasoning ("more synthesis passes won't fix thin evidence") is sound; the conclusion is wrong. Thin evidence should trigger **more retrieval**, not less synthesis. Report B had exactly 3 sources, so it sat one source above the floor and iterated pointlessly; a report with 2 would have skipped straight to delivery.

**Fix:** replace the stop with a **retrieval escalation**: when `total_sources < 12`, dispatch a targeted search round (new engines, reformulated queries, P2-26) before giving up. Only after retrieval escalation fails is thin evidence terminal, and then the correct output is a short honest report with a stated evidence limitation, not 32 padded pages.

---

### Group F. Search corpus collapse

---

#### P2-26 The general-web corpus is a two-engine duopoly, and both were banned (S1)

**Location:** `hyperion/tools/searxng.py:397` and `searxng_settings.yml:104-146`

```python
RELIABLE_ENGINES = "bing,duckduckgo"
```

`searxng_settings.yml` sets `use_default_settings: false` and enables **exactly two** engines (`bing`, `duckduckgo`); `wikipedia`, `arxiv`, `github`, `hackernews` are all `disabled: true`. The Docker log supplied with these engagements shows a DuckDuckGo CAPTCHA storm terminating in `HTTP error 403 (suspended_time=86400)`. That leaves **one** engine for the remainder of a 24-hour window, and Bing alone against a datacenter IP is what produced 3 sources and a corpus of dictionary definitions.

Compounding defects in the same file:

* **`:321-323` zero results does not retry and does not rotate engines.**
  ```python
  # SearXNG returned zero results — don't retry, engines are likely blocked
  break  # No point retrying if engines are blocked/CAPTCHA'd
  ```
  Correct reasoning, wrong action. If engines are blocked, the action is to **use different engines**, not to give up.
* **`:313-320` `unresponsive_engines` is logged and discarded.** SearXNG tells us exactly which engine failed, in the response body, and nothing consumes it. There is no engine health tracker, no cooldown, no blacklist, no promotion of a standby engine.
* **`:143 SEARCH_BUDGET_CAP = 200` and `:495-502`.** A process-global counter. Once exhausted, `search()` returns an empty `SearchResponse` for the rest of the engagement, having logged **one** warning. Every downstream specialist sees "no results" and degrades silently. This is the search-side twin of P2-27.
* **Jina fallback at `:333-374`** requires an API key. When absent, the fallback is a no-op and `search()` returns empty.

**Fix:**
1. **Widen the pool.** Enable and register at least 6 general-web engines: `bing`, `duckduckgo`, `brave`, `mojeek`, `startpage`, `qwant`, plus `wikipedia` for definitional grounding only. Keep the specialist corpora reachable by category.
2. **Engine health tracker.** New `hyperion/tools/engine_health.py`: parse `unresponsive_engines` from every response, apply exponential cooldown per engine, persist across the process, and **exclude** cooled engines from the next `engines=` parameter. Treat a 403 with `suspended_time` as a 24-hour cooldown for that engine specifically.
3. **Rotate on zero.** Replace the `break` with: drop the engines that reported unresponsive, add the next standby engines, retry once. Only then fall through.
4. **FlareSolverr** is already vendored (`tools/flaresolverr.py`). Wire it as the CAPTCHA path for engines that return a challenge, rather than dropping the engine.
5. **Corpus floor as a hard gate.** If an engagement finishes with `< 8` distinct source domains, that is an `integrity_blocker`, not a footnote. A 32-page report built on 3 encyclopedia entries must not render.
6. Make the search-budget exhaustion an escalation to the Director, not a silent empty response.

---

#### P2-27 Off-topic reference sites are not filtered, and are mislabelled as credible (S2)

**Location:** `evidence_scorer.py:174-188` (`DENIED_DOMAINS`, `DENIED_DOMAIN_SUBSTRINGS`)

The blocklist covers retail and social domains. It does not cover **dictionary, thesaurus and consumer-health** domains, which is what a Bing-only corpus returns for an unknown entity. Report B cites Merriam-Webster on "EMERGING", Cambridge on "MOBILITY", `iciba.com`, `health.harvard.edu` and Motability UK, and the leaked JSON labels some of them `"credibility": "government"`.

Two distinct problems:
1. No denial for reference-work domains.
2. The credibility label is assigned from a domain table with no mapping for these hosts, so it falls back to a default that reads as authoritative. `grep -rn "SourceType.GOVERNMENT\|source_type=" hyperion/` returns **nothing**: the `SourceType` enum at `schemas/models.py:91` is never assigned by any classifier, so whatever writes `"credibility": "government"` is doing it by accident.

**Fix:**
1. Extend `DENIED_DOMAINS` with reference works: `merriam-webster.com`, `dictionary.cambridge.org`, `dictionary.com`, `thefreedictionary.com`, `collinsdictionary.com`, `vocabulary.com`, `wiktionary.org`, `iciba.com`, `urbandictionary.com`, plus `health.harvard.edu` and similar consumer-health hosts, for business engagements.
2. Add a **definitional-result detector**: a result whose title matches `r"\b(definition|meaning|synonyms?|pronunciation)\b"` or whose URL path contains `/dictionary/`, `/define/`, `/terms/` is dropped for a business query.
3. Implement a real `classify_source_type(url) -> SourceType` and use it. A source whose type cannot be classified is `SourceType.UNKNOWN` and scores accordingly. Never default to a credible label.
4. Add a **subject-presence gate**: if fewer than 3 results across all queries contain the engagement subject as a token, the subject has no web corpus. Declare that, and stop. Do not write 32 pages about a company that does not appear on the web. Report B is a report about an entity with no findable footprint, and the system had no way to say so.

---

#### P2-28 Query planning cannot detect a no-corpus subject (S2)

**Location:** `tools/query_planner.py:164-176`, `:381-401`

The planner validates that a query has `>= 2` alphabetic tokens of `>= 3` characters, which correctly rejects a bare `"EMERGING"` as a query. The dictionary results therefore did not come from single-word queries; they came from **multi-word queries about an entity with no corpus**, where the engine matched on the common nouns. The planner has no feedback signal for that condition.

**Fix:** after the first search round, compute subject recall (fraction of results whose title or snippet contains the subject token). Below 0.15, the planner must switch strategy: query the entity's own domain, corporate registries, and news archives, and if recall stays below 0.15, raise a `no_corpus` escalation. Feed the result into P2-27 item 4.

---

### Group G. Router observability

---

#### P2-29 A total routing failure is reported as `google/none` (S1 for observability)

**Location:** `hyperion/router/router.py:416-428`

```python
return RouterResponse(
    content="",
    model="none",
    provider=ProviderType.GOOGLE,  # Placeholder
    tier=tier,
    success=False,
    error="All providers exhausted across all adjacent tiers",
)
```

Every "no candidate anywhere" outcome is attributed to Google with model `none`. The operator sees `google/none` and a quota-exhaustion message. During these two engagements the actual cause was a **deleted API key**, which is a credential failure on one provider, and the router's own label pointed the operator at quota limits on a provider it had never successfully contacted. The user's observation *"we haven't even touched other providers still it says limit used"* is a direct consequence of this string.

The credential issue is resolved. **The label is not**, and the next different failure will be misreported in exactly the same way.

**Fix:**
1. Add `ProviderType.NONE` (or make the field `ProviderType | None`) and use it. Never name an innocent provider.
2. Replace the single error string with a structured diagnosis:
   ```python
   RouterFailure(
       tiers_attempted=[...],          # every tier walked
       providers_considered={...},     # per tier
       skip_reasons={provider: reason} # "health_open" | "budget_exhausted"
                                       # | "predicted_rate_limited" | "no_model_for_tier"
                                       # | "wait_exceeded_threshold" | "auth_error"
   )
   ```
3. Distinguish **auth** from **quota** at the provider layer: a 401/403 from a provider must set a distinct `HealthState.UNAUTHENTICATED` that is logged loudly at startup and on every occurrence, and must **not** be aggregated into rate-limit reporting.
4. Add a startup **credential preflight**: one minimal completion per configured provider. `obs/health.py` currently probes TCP reachability, which cannot detect a dead key. This is the same class of defect as Part 1's D-06 (TCP-only SearXNG probe) in a different subsystem.

---

#### P2-30 One hot model disables an entire provider (S2)

**Location:** `router.py:200-223`

```python
for (pt, _model_name), tracker in self._trackers.items():
    if pt != provider_type:
        continue
    if tracker.model.rpm > 0 and tracker.current_rpm() / tracker.model.rpm > 0.85:
        return True
```

The function answers "is any model on this provider near its limit" and is used to answer "should I skip this provider". Google runs Gemma at 14,400 RPD and Gemini at 500 RPD; saturating the small model marks the large one unavailable, and vice versa. Same for Groq's six models.

**Fix:** evaluate the prediction for the **specific candidate** the wait gate selects, not for the provider. Order of operations must be: select candidate via `wait_gate.select_with_wait` → check that candidate's tracker → skip the candidate (not the provider) → ask the wait gate for the next candidate on the same provider.

---

#### P2-31 MICRO tier has no provider priority, so ordering is non-deterministic (S3)

**Location:** `router.py:81-87`

`_TIER_PROVIDER_PRIORITY` has entries for FAST, STANDARD, DEEP, STRONG. MICRO is absent, so `_sort_providers_by_priority` returns `[] + list(set)`, and set iteration order over an enum is unspecified. MICRO is the tier all 11 specialists' sub-agents run on (Part 1, D-19), which makes the highest-volume tier the least deterministic.

**Fix:** add an explicit MICRO entry. Also note `_TIER_ADJACENCY[STRONG] = [DEEP, STANDARD]` and `_TIER_DOWNGRADE[STRONG] = STANDARD`, so STANDARD is attempted twice on every STRONG failure; deduplicate the walk.

---

### Group H. Typography policy

---

#### P2-32 Global em dash ban (S2, explicit user requirement)

**Measured:** 51 em dashes in report A, 21 in report B, in headings, prose, boilerplate and running footers.

**Sources of the character, all of which must be addressed:**

| Source | Locations | Count |
|---|---|---|
| Hardcoded in agent **system prompts**, so the LLM imitates the style | `synthesis_lead.py:157,163,166,180,182,187` and 79 more in that file | 85 in `synthesis_lead.py` |
| Hardcoded in **template boilerplate** and CSS content strings | `presentation_designer.py:1409` (`Confidential — for intended recipient only.`), and 97 more | 98 in `presentation_designer.py` |
| Hardcoded in **generated content** f-strings | `synthesis_lead.py:348` (`f"Key Value Driver — {vd}"`), `markdown.py:318,399,459`, `quality_gate.py:978`, and the placeholder at `synthesis_lead.py:~1235` | 7 in `markdown.py`, 78 in `quality_gate.py`, 33 in `fact_checker.py` |
| Model output, unconstrained | no rule anywhere: `grep -rn "em dash\|em-dash" hyperion/` matches only two comments in `sub_agent.py` | n/a |

**Fix, three layers, all required:**
1. **Generation.** Add one line to the shared system-prompt preamble used by every agent: `"Never use the em dash character (U+2014) or the en dash (U+2013). Use a comma, a colon, or a full stop."` Add it once, in the base spec, not 20 times.
2. **Sanitization.** A `sanitize_typography(text)` function applied in the **Jinja finalizer** (same hook as P2-10): replace U+2014 and U+2013 with `", "` or `": "` per context, collapse resulting double punctuation. This catches model output regardless of prompt compliance.
3. **Source hygiene.** Purge U+2014 from every string literal that can reach the page: template boilerplate, CSS `content:` values, f-strings in `markdown.py`, `synthesis_lead.py`, `quality_gate.py`, `fact_checker.py`, `presentation_designer.py`. Code **comments** may keep them; only strings that can be rendered must be clean.
4. **Enforcement.** `page_audit` asserts `"\u2014" not in extracted_text` and `"\u2013" not in extracted_text`. Zero tolerance. Plus a repo-level test that greps for U+2014 inside string literals in the render path.

---

### Group I. Imagery

---

#### P2-33 Section images are generic stock queries with no engagement topic (S1)

**Location:** `presentation_designer.py:139-185` (`SECTION_IMAGE_SEARCH_TERMS`)

A static per-agent map of queries such as `"green sustainable business"`, `"risk management dashboard"`, `"chess strategy pieces board"`, `"government building columns"`. The engagement subject is **not interpolated**. Directly above it, `:131` states the design rule: *"3. Images are topic-relevant, not generic stock."* The comment and the code disagree, and the code wins.

Measured consequence: report B's **Market Landscape** chapter, for a chemicals and hardware manufacturing engagement, carries a **crypto candlestick chart photograph** credited `Source: Unsplash via Maxim Hopman`. Report A's chapters carry the same class of decorative stock.

**Fix:**
1. Build the query from the engagement: `f"{subject} {geography} {section_topic}"`, fall back to `f"{subject} {section_topic}"`, and only then to a neutral abstract texture.
2. Add a **relevance gate**. `output/images.py` has no relevance scoring at all (`grep -n "relevan" hyperion/output/images.py` returns nothing): it crops, warms and resizes whatever it is given. Score the candidate's title, tags and description against the subject and section topic with the corrected token matcher from `evidence_scorer`, and **reject below a floor**. A section with no relevant image gets **no image**, which is strictly better than a wrong one.
3. Ban the chart-like categories outright for section decoration (`stock chart`, `candlestick`, `trading screen`): an exhibit is a chart, a decorative photo must not look like one, or the reader reads it as data.
4. The caption must be a **caption**, not a photo credit. Report B page 8 prints `Source: Unsplash via Maxim Hopman` where a caption belongs, and the credit belongs in a colophon.
5. Fix the caption's position: at `:1274-1275` the `<p class="section-image-caption">` follows the `<img>` in markup but renders **above** it because the float removes the image from flow. Once P2-02 moves the figure into a `column-span: all` `<figure>`, use `<figcaption>` and the order is correct by construction.

---

#### P2-34 Methodology and appendix loops render empty bullets (S2)

**Location:** `presentation_designer.py:1353-1370`

```html
{% for agent in report.agents_used %}
<li>{{ agent }}</li>
{% endfor %}
```

Report B's Methodology page prints 11 empty bullet glyphs. `report.agents_used` held 11 empty strings. Same unguarded pattern for `report.limitations`.

**Fix:** filter falsy entries in every template loop (`{% for x in list if x %}`), and suppress the enclosing `<h3>`/`<ul>` when the filtered list is empty. Add a `page_audit` assertion that no page contains a list item with no text content.

---

## 3. Definition of Done

Every gate is a test that fails today and must pass. Gate numbering continues from Part 1 (which ended at gate 24) with a `P2-G` prefix for clarity.

| Gate | Assertion | Blocks |
|---|---|---|
| **P2-G1** | `page_audit` module exists, runs inside `render.py` after PDF bytes exist, and **raises** on failure | P2-08 |
| **P2-G2** | Image / text bbox intersection area `== 0` on every page of both fixtures | P2-02 |
| **P2-G3** | `max(page_fill) >= 0.62` and `median(body page fill) >= 0.45` | P2-01 |
| **P2-G3b** | **Column balance:** on every body page, `min(col1_words, col2_words) >= 0.35 * max(col1_words, col2_words)`. Fails today on 6 pages of report A with `col2 = 0` | P2-01 |
| **P2-G4** | No page has `< 90` words except cover, back cover, declared plate | P2-04, P2-34 |
| **P2-G5** | All 4 corner pixels of all pages equal the theme background | P2-03 |
| **P2-G6** | Every content bbox satisfies `0 <= x0` and `x1 <= page_width` | P2-07 |
| **P2-G7** | Every TOC entry's stated page equals the page of its heading; no TOC entry lacks a target | P2-05, P2-06 |
| **P2-G8** | `"{'"` and `'{"'` occurrences in extracted PDF text `== 0` | P2-09, P2-10, P2-11 |
| **P2-G9** | `AnalysisSection(implications=<any _BANNED_FILLER phrase>)` raises `ValidationError` | P2-16 |
| **P2-G10** | Every `_BANNED_FILLER` phrase: 0 occurrences in extracted PDF text | P2-16 |
| **P2-G11** | U+2014 and U+2013 occurrences in extracted PDF text `== 0` | P2-32 |
| **P2-G12** | `hallucinat*`, `unverified claim`, `fact checker`, `quality gate`, `iteration`, `parse error`: 0 occurrences in client-visible text | P2-12 |
| **P2-G13** | `report.sections` contains no entry whose agent is outside `SECTION_PRODUCING_AGENTS` | P2-12 |
| **P2-G14** | `orchestrator` quality loop exits **only** on `current_score.approved`; unit test with a 4.5-score, blocker-present score object asserts the loop continues | P2-22 |
| **P2-G15** | `integrity_blockers` non-empty ⇒ no client PDF produced, non-zero exit, diagnostic document written | P2-23 |
| **P2-G16** | Integrity scan runs on **extracted PDF text**, not only on the model | P2-24 |
| **P2-G17** | `AnalysisGap` exists; a section with an unresolvable gap **omits** the field and records it in `limitations`; no placeholder string is constructible | P2-16 |
| **P2-G18** | All 11 specialists handle `request_type="verify_claims"` via the base class; `bus.publish` logs ERROR when an addressed message has no matching subscriber | P2-17 |
| **P2-G19** | DAG contains a `GAP_CLOSURE` phase between fact check and quality gate; specialists are not `COMPLETED` before it | P2-18 |
| **P2-G20** | A claim whose sources all have empty `key_data` is `UNVERIFIABLE`, never `HALLUCINATED`; `UNVERIFIABLE` is excluded from the hallucination count | P2-19 |
| **P2-G21** | Stage 2 STRONG-tier re-adjudication exists and is the only authority for `HALLUCINATED` / `CONTRADICTED` | P2-20 |
| **P2-G22** | No contradiction row is emitted with resolution weight `0.00` or with `Confidence: …` as either side; appendix table capped at 10 | P2-21 |
| **P2-G23** | `>= 6` general-web engines enabled and registered; engine health tracker parses `unresponsive_engines` and applies per-engine cooldown | P2-26 |
| **P2-G24** | Zero-result response triggers engine rotation and one retry before falling through | P2-26 |
| **P2-G25** | `< 8` distinct source domains ⇒ `integrity_blocker` | P2-26 |
| **P2-G26** | Reference-work and definitional results are dropped for business queries; `classify_source_type` is implemented and used; no source is labelled `government` without a `.gov`-class host | P2-27 |
| **P2-G27** | Subject recall `< 0.15` after round 1 ⇒ strategy switch, then `no_corpus` escalation | P2-28 |
| **P2-G28** | Router failure response never names an uncontacted provider; `RouterFailure` carries per-provider skip reasons | P2-29 |
| **P2-G29** | Startup credential preflight issues one real completion per provider; a 401/403 sets `UNAUTHENTICATED` and is reported distinctly from quota | P2-29 |
| **P2-G30** | `_predicted_rate_limited` is evaluated per **candidate model**, not per provider; unit test with a saturated small model asserts the large model on the same provider is still selected | P2-30 |
| **P2-G31** | `derive_confidence()` is the single source of confidence; cover, At a Glance and appendix all read it; `HIGH` requires `>= 12` sources, `>= 3` domains, `void_ratio == 0` | P2-15 |
| **P2-G32** | Section image query interpolates the engagement subject; relevance floor rejects below threshold; no image is preferable to a wrong image | P2-33 |
| **P2-G33** | No duplicate normalized paragraph of `>= 12` words in the document | P2-13 |
| **P2-G34** | Meta-text blocklist rejects iteration output containing `previously`, `$XB`, `[.*citation.*]`, `⟨` | P2-14 |

---

## 4. Phased remediation plan

Ordered by **blast radius per hour of work**. Do not reorder: Phase 1 makes the rest observable, and Phase 2 makes the rest verifiable.

### Phase 0. Stop shipping broken artifacts (about 30 minutes, 2 lines)

| Step | File:line | Change |
|---|---|---|
| 0.1 | `orchestrator.py:1113` | `if current_score.total_score >= 4.0:` → `if current_score.approved:` |
| 0.2 | `presentation_designer.py:3043` | Remove the `max_iterations_reached` bypass for integrity blockers |

After Phase 0 the pipeline will **refuse** to produce these two PDFs. That is the correct behaviour and the baseline for everything else.

### Phase 1. Observability (about 3 hours)

| Step | Defect | Change |
|---|---|---|
| 1.1 | P2-29 | `ProviderType.NONE`, `RouterFailure` with per-provider skip reasons |
| 1.2 | P2-29 | Startup credential preflight, `HealthState.UNAUTHENTICATED` |
| 1.3 | P2-26 | Engine health tracker consuming `unresponsive_engines`; log every engine cooldown |
| 1.4 | P2-17 | Bus-level "no subscriber for addressed message" ERROR |
| 1.5 | P2-11 | Replace every `except: pass` in the synthesis path with a logged, typed error |

### Phase 2. The page audit (about 4 hours)

| Step | Defect | Change |
|---|---|---|
| 2.1 | P2-08 | `hyperion/output/page_audit.py` implementing all of §2 P2-08's table |
| 2.2 | P2-08 | Wire into `render.py` after PDF bytes; fail closed |
| 2.3 | P2-24 | Run the integrity scan on extracted PDF text |
| 2.4 | P2-G1..G8, G11 | Both fixtures as regression fixtures in `tests/fixtures/` |

### Phase 3. Layout (about 4 hours, high visual payoff)

| Step | Defect | Change |
|---|---|---|
| 3.1 | P2-01 | `column-fill: balance`. Verify with **T-02b** (column balance), not only with the ink-fill test: an ink threshold can be satisfied by one overfull column |
| 3.2 | P2-02 | Move the figure inside `.section-body` as a `column-span: all` `<figure>` with `<figcaption>`; delete `float: right` |
| 3.3 | P2-03 | Background on `html` **and** `@page` |
| 3.4 | P2-04 | `break-before: avoid` on `.implication-box`, plus the page-audit rejection |
| 3.5 | P2-05, P2-06 | `target-counter` TOC, entries emitted from the same conditions as the chapters |
| 3.6 | P2-07 | Clip the cover to the trim box |
| 3.7 | P2-34 | `{% for x in list if x %}` in every loop, suppress empty sections |

### Phase 4. Content integrity (about 6 hours)

| Step | Defect | Change |
|---|---|---|
| 4.1 | P2-09 | `hyperion/output/display.py::display_value` and `humanize`; replace `str(val)` at `synthesis_lead.py:337` |
| 4.2 | P2-09 | **Delete** the `json.dumps` summary path at `synthesis_lead.py:318` and the `content = summary` assignment |
| 4.3 | P2-10 | Register `humanize` as the Jinja **finalizer**; keep `clean_dict_repr` as an alias |
| 4.4 | P2-09 | `field_validator` on `KeyFinding.content` / `AnalysisSection.body` rejecting object reprs |
| 4.5 | P2-11 | Delete the concatenation fallback; raise a gap instead |
| 4.6 | P2-12 | `SECTION_PRODUCING_AGENTS` allowlist; route Fact Checker to `fact_check_report` only; lexical blocklist |
| 4.7 | P2-13 | Paragraph dedup at assembly |
| 4.8 | P2-14 | Meta-text blocklist on iteration output, with tier escalation on match |
| 4.9 | P2-15 | `derive_confidence()`, single source, read by all four surfaces |
| 4.10 | P2-32 | Em dash: prompt rule, finalizer sanitizer, source purge, audit assertion |

### Phase 5. Gap closure, the policy change (about 8 hours)

| Step | Defect | Change |
|---|---|---|
| 5.1 | P2-16 | `AnalysisGap` schema; every placeholder site raises a gap |
| 5.2 | P2-16 | `_BANNED_FILLER` becomes a construction-time validator |
| 5.3 | P2-18 | `GAP_CLOSURE` DAG phase, `TaskStatus.AWAITING_FOLLOWUP` |
| 5.4 | P2-17 | `BaseSpecialist._handle_verify_claims` on the base class |
| 5.5 | P2-16 | 3-round closure loop: same specialist at tier+1 → different specialist plus targeted search → STRONG/DEEP synthesis |
| 5.6 | P2-16 | Unresolved gap ⇒ omit the field, record the question in `limitations` |

### Phase 6. Verification quality (about 5 hours)

| Step | Defect | Change |
|---|---|---|
| 6.1 | P2-19 | `UNVERIFIABLE` as a distinct status; chain validation only on fetched content |
| 6.2 | P2-19 | Token-boundary matching reusing `evidence_scorer`'s matcher |
| 6.3 | P2-20 | Stage 2 STRONG re-adjudication of flagged claims only |
| 6.4 | P2-21 | Contradiction detection on claims with numeric or categorical conflict; suppress 0.00 resolutions; cap at 10 |

### Phase 7. Search corpus (about 6 hours)

| Step | Defect | Change |
|---|---|---|
| 7.1 | P2-26 | 6+ general engines in `searxng_settings.yml` and `RELIABLE_ENGINES` |
| 7.2 | P2-26 | Engine rotation on zero results; FlareSolverr on challenge |
| 7.3 | P2-26 | Search-budget exhaustion escalates instead of returning empty |
| 7.4 | P2-27 | Reference-work denial list, definitional-result detector, `classify_source_type` |
| 7.5 | P2-28 | Subject-recall metric, strategy switch, `no_corpus` escalation |
| 7.6 | P2-26, P2-25 | Corpus floor of 8 domains as an `integrity_blocker`; retrieval escalation replaces the content-aware stop |

### Phase 8. Router correctness (about 2 hours)

| Step | Defect | Change |
|---|---|---|
| 8.1 | P2-30 | Per-candidate rate-limit prediction |
| 8.2 | P2-31 | MICRO entry in `_TIER_PROVIDER_PRIORITY`; deduplicate the STRONG tier walk |

---

## 5. Test specifications

### T-01 Page geometry audit (new, `tests/output/test_page_audit.py`)

```python
def test_no_image_text_occlusion(rendered_pdf):
    doc = fitz.open(rendered_pdf)
    for page in doc:
        images = [fitz.Rect(i["bbox"]) for i in page.get_image_info()]
        blocks = [fitz.Rect(b[:4]) for b in page.get_text("blocks")]
        for img in images:
            for blk in blocks:
                inter = img & blk
                assert inter.is_empty or inter.get_area() < 1.0, (
                    f"page {page.number+1}: image {img} occludes text {blk} "
                    f"({inter.get_area():.0f} pt2)"
                )
```
Threshold is 1.0 pt², not 100 pt². Any measurable overlap is a defect. On the current fixtures this fails on 4 pages with 2,773 pt² each.

### T-02 Fill and orphan audit

```python
def test_no_half_empty_or_orphan_pages(rendered_pdf, manifest):
    doc = fitz.open(rendered_pdf)
    fills = []
    for page in doc:
        if page.number in manifest.cover_pages: continue
        words = len(page.get_text().split())
        ink = sum(fitz.Rect(b[:4]).get_area() for b in page.get_text("blocks"))
        fill = ink / page.rect.get_area()
        assert words >= 90, f"page {page.number+1}: {words} words"
        assert fill >= 0.30, f"page {page.number+1}: {fill:.1%} fill"
        fills.append(fill)
    assert max(fills) >= 0.62
    assert statistics.median(fills) >= 0.45
```

### T-02b Column balance audit (this is the test that catches `col2 = 0`)

```python
def test_two_columns_are_actually_two_columns(rendered_pdf, manifest):
    doc = fitz.open(rendered_pdf)
    for page in doc:
        if page.number not in manifest.two_column_body_pages:
            continue
        mid = page.rect.width / 2
        blocks = [b for b in page.get_text("blocks") if b[4].strip()]
        c1 = sum(len(b[4].split()) for b in blocks if b[2] <= mid + 6)
        c2 = sum(len(b[4].split()) for b in blocks if b[0] >= mid - 6)
        if max(c1, c2) < 60:      # not a prose page, skip
            continue
        assert min(c1, c2) >= 0.35 * max(c1, c2), (
            f"page {page.number+1}: column imbalance col1={c1}w col2={c2}w "
            f"(column-fill regression)"
        )
```
Fails today on report A pages 7, 9, 12, 14, 16, 18 (`col2 = 0`) and report B pages 6, 9, 22.

### T-03 Canvas colour

```python
def test_page_canvas_is_theme_background(rendered_pdf, theme):
    doc = fitz.open(rendered_pdf)
    for page in doc:
        pix = page.get_pixmap(dpi=72)
        w, h = pix.width, pix.height
        for x, y in [(1,1), (w-2,1), (1,h-2), (w-2,h-2)]:
            assert pix.pixel(x, y) == theme.background_rgb, (
                f"page {page.number+1} corner ({x},{y}) is {pix.pixel(x,y)}"
            )
```

### T-04 Trim box containment

```python
def test_no_content_outside_trim(rendered_pdf):
    for page in fitz.open(rendered_pdf):
        for b in page.get_text("blocks"):
            assert b[0] >= -0.5 and b[2] <= page.rect.width + 0.5
```
Fails today on report A page 1 (`x0 = -328.0`) and report B page 14 (`x1 = 596.0`).

### T-05 TOC fidelity

Parse the TOC table's rows; for each, locate the page whose first `<h2>`-styled span equals the entry text; assert equality with the printed number. Assert every TOC entry has a located target. Fails today on 13 of 19 entries plus 1 phantom.

### T-06 Text hygiene, zero tolerance

```python
BANNED_SUBSTRINGS = [
    "{'", '{"', "\u2014", "\u2013",
    "Insufficient evidence to state implications",
    "no specific implications stated", "no competitors identified",
    "accessed_at", "\\u20", "$XB", "$YB", "[verified citation]",
    "[new source", "previously lacked", "parse error", "Data Sparse",
    "hallucinat", "unverified claim", "Fact Checker", "Quality Gate",
]

def test_no_banned_text(rendered_pdf):
    text = "\n".join(p.get_text() for p in fitz.open(rendered_pdf))
    for s in BANNED_SUBSTRINGS:
        assert s.lower() not in text.lower(), f"banned text in PDF: {s!r}"
```
Note `"hallucinat"` and `"Fact Checker"` are banned in **client-visible** text; the Technical Appendix may report a **count**, so the assertion runs on pages excluding a declared internal-metrics block, or the appendix must phrase it without those tokens. Prefer the latter: "Independent verification: 43 of 72 claims confirmed."

### T-07 Placeholder unrepresentability

```python
@pytest.mark.parametrize("phrase", QualityGate._BANNED_FILLER)
def test_banned_filler_rejected_at_construction(phrase):
    with pytest.raises(ValidationError):
        AnalysisSection(id="s1", title="T", key_insight="k",
                        body="b" * 100, implications=phrase)
```

### T-08 Duplicate paragraph detection

Normalize whitespace and case, hash every paragraph of `>= 12` words, assert no hash appears twice. Fails today in 6 chapters of report B.

### T-09 Router failure attribution

```python
async def test_router_failure_names_no_provider(router_with_all_providers_down):
    r = await router_with_all_providers_down.complete(ModelTier.STRONG, MESSAGES)
    assert not r.success
    assert r.provider is ProviderType.NONE
    assert r.model != "none" or r.provider is ProviderType.NONE
    assert r.failure.skip_reasons  # per-provider, non-empty
```

### T-10 Per-candidate rate-limit prediction

Saturate the tracker for Google Gemma to 100 % RPM; assert `get_available_providers(DEEP)` still yields Google and that the wait gate selects the Gemini model.

### T-11 Gap closure

```python
async def test_unresolved_gap_omits_field_and_declares_limitation(engagement):
    # force a specialist to return no implications, 3 closure rounds all fail
    report = await engagement.run()
    sec = report.sections[0]
    assert sec.implications is None            # omitted, not placeholdered
    assert any("implication" in l.lower() for l in report.limitations)
```

### T-12 Verify-claims wiring

```python
@pytest.mark.parametrize("agent_cls", ALL_SPECIALISTS)
async def test_specialist_handles_verify_claims(agent_cls, bus):
    agent = agent_cls(bus=bus)
    await bus.publish(Channel.REQUESTS, MessageType.ESCALATION, sender=AgentName.FACT_CHECKER,
                      payload={"to_agent": agent.name.value, "request_type": "verify_claims",
                               "unverified_claims": [SAMPLE_CLAIM]})
    await bus.drain()
    assert agent.verify_claims_calls == 1
```

### T-13 Engine rotation

Stub SearXNG to return `{"results": [], "unresponsive_engines": [["duckduckgo", "CAPTCHA"]]}` on the first call; assert the second call's `engines=` parameter excludes `duckduckgo` and includes a standby engine.

### T-14 Corpus floor

Build a `FinalReport` with 3 sources on 3 domains; assert the Quality Gate returns a non-empty `integrity_blockers` and that `render` refuses.

---

## 6. What this audit does not cover

For honesty about the boundary of this pass:

* `router/budget.py` reserve arithmetic was read but not stress-tested. The 20 % reserve is in-memory and per-process; a restart resets the day's consumption. Filed as an observation, not a defect, because it did not contribute to these two artifacts.
* `router/wait_gate.py` `select_provider` scoring was read at the interface level only.
* `output/charts.py` (1,484 lines) and `chart_specs.py` were not audited. Exhibits rendered in both fixtures and no chart-specific defect was measured, but "rendered" is not "correct".
* `tools/deep_search.py`, `unified_search.py`, `content_selector.py` were not audited. P2-26 addresses the engine layer beneath them; a defect in the selection layer above would present similarly and should be checked after Phase 7.
* `sub_agent.py` tiering remains as Part 1 described it (D-19, MICRO-only, 500 output tokens against a 450-word floor). Part 1's fix plan for that item is unchanged and still applies.
* PDF/A-2b conformance of the pikepdf post-pass was not re-verified against the negative-x cover content in P2-07.

---

## 7. One-paragraph summary for the record

Part 1 fixed a pipeline that produced nothing. It now produces something, and the something is wrong in eight independent ways, six of which are single lines. The most important is not a rendering bug: it is that HYPERION's Layer 4 truth gate **already detected every content defect in both of these PDFs**, wrote them into `QualityScore.gaps`, set `approved = False`, and was then overruled by an orchestrator that reads `total_score` instead of `approved`. Change that one comparison and both of these documents stop existing. Everything else in this audit is about making the report that replaces them worth reading: filling the page (`column-fill: balance`), keeping images out of the text (`float` removed), telling the truth about page numbers (`target-counter`), never printing a Python object (a single `display_value` presenter behind a Jinja finalizer), never printing a placeholder where a specialist should have been re-dispatched (the `AnalysisGap` closure loop), never handing the client the QA log (a section-producing allowlist plus a two-stage STRONG-tier verifier instead of a FAST-tier false-positive factory), never resting a 32-page argument on three encyclopedia entries (six engines, engine health, a corpus floor), and never printing an em dash again (prompt rule, finalizer sanitizer, source purge, zero-tolerance audit).
