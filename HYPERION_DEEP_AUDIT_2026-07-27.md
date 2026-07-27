# HYPERION — Deep Forensic Audit & Phase-Wise Fix Plan

**Date:** 2026-07-27
**Branch:** `fix0.1`
**Auditor:** automated forensic audit (measured, not estimated)
**Scope:** (A) content depth + premium formatting vs MBB benchmark · (B) agent/sub-agent query formulation, search, and information extraction · (C) tool utilisation + tool recommendations

---

## 0. TL;DR — THE NEXT FOOKING STEP

> **Fix `hyperion/agents/sub_agent.py:626` right now. One line. Before anything else.**

```python
q = re.sub(r'\s*[\u2014\u2013--]+\s*', ' ', q)
#                        ^^^^^^^^^ this is parsed as a character RANGE \u2013-- → PatternError
```

Under Python 3.13 this raises `re.PatternError: bad character range \u-- at position 14` **on every single call, unconditionally, for every input string.** It is not an edge case. It is not input-dependent. It fires 100% of the time.

`_condense_query` is called at **13 sites** in `sub_agent.py` — it is the *only* query builder the sub-agent layer has. Every call site is wrapped in `except Exception: pass` or `except Exception as e: errors.append(...)`. Therefore:

**Every sub-agent spawned by all 12 specialists performs ZERO research. SearxNG returns nothing. Jina returns nothing. Wayback, Alpha Vantage, FRED, SEC EDGAR, Semantic Scholar, OpenAlex, World Bank, Google Trends, HackerNews, Reddit, Second Brain — all return nothing.** The sub-agent then hits `run()`'s "no findings" branch and emits a `research_gap` KeyFinding. The system is fabricating a research gap for 100% of sub-questions and the report is being written almost entirely from the specialists' own parametric knowledge, not from retrieved evidence.

This single character in a regex character class invalidates the answer to your question *"make sure the search and information is properly extracted."* It is not properly extracted. It is not extracted at all.

**Proof (reproduced live in this sandbox):**

```
$ python3 --version
Python 3.13.13

CRASH 'plain question about market size in Nigeria' PatternError bad character range \u-- at position 14
CRASH 'Find lithium battery cost data'             PatternError bad character range \u-- at position 14

searxng leg -> ('searxng', [], None)   # crash swallowed, empty result returned
jina leg    -> ('jina',    [], None)   # crash swallowed, empty result returned
```

Zero tests cover `_condense_query` (`grep -rn "_condense_query" tests/` → no matches), which is exactly why 345 green tests coexist with a total research outage.

---

## 1. Audit Method

Nothing in this report is inferred from reading code alone. Every claim is backed by one of:

| Method | Instrument |
|---|---|
| Benchmark measurement | PyMuPDF (`fitz`) forensic extraction of the BCG PDF — fonts, sizes, margins, image resolution, chars/line |
| Benchmark structure | `crawler` tool on the MGI report (direct PDF download was blocked: `code=000 size=0`) |
| Our own output measurement | `tools/audit_render_probe.py` — renders the **production** template path and measures the resulting PDF |
| Visual comparison | `understand_images` on rasterised page pairs |
| Behavioural proof | Live Python execution against the real modules (not mocks) |
| Coverage proof | `grep -c` on every search entry point |

**Critical methodological note:** the probe renders `presentation_designer.HTML_TEMPLATE` + `CSS_TEMPLATE` (the inline strings that actually ship), **not** the `.j2` files in `hyperion/output/templates/`. Those `.j2` files are dead code (see §3.2). An earlier version of this audit that measured the `.j2` path would have produced entirely wrong conclusions.

---

## 2. The Benchmark, Quantified

### 2.1 BCG Annual Sustainability Report 2024 (measured)

| Dimension | Measured value |
|---|---|
| Pages | 126 (A4) |
| Total words | 44,890 |
| Words per page | **356** |
| Body font size | 10.0 pt |
| Footnote size | 7.0 – 7.5 pt |
| Heading sizes | 14 / 16 / 18 / 20 / 22 / 34 pt |
| **Median chars per line** | **56** |
| **Column structure** | **Two-column** |
| Margins | L 36 pt · R ~35 pt |
| Embedded images | 92 (median 808×608, **max 1660×2346**) |
| Brand font | `HendersonBCGSans` — **subset-embedded in the PDF** |
| Palette | body `#4a4e4f`; accents `#197a56` `#207e5d` `#3fb76d`; tints `#dcf9e3` `#dfd7cd` |

### 2.2 MGI "Next Big Arenas of Competition" (structure confirmed)

```
At-a-glance → Introduction → Executive summary → 3 chapters
→ Arena compendium → Endnotes → Technical appendix
```

Exhibit anatomy is rigid and repeated: **`Exhibit E1` → action title → figure → `Note:` → `Source:` → "McKinsey & Company" signature.** Every argument is carried by an exhibit, not by prose.

---

## 3. PART A — Depth & Premium Formatting: DO WE HIT IT?

**Verdict: No. Content volume is fine. Typography and visual architecture fail.**

### 3.1 Measured HYPERION output vs benchmark

| Metric | HYPERION (measured) | Benchmark | Verdict |
|---|---|---|---|
| Page count | 36 | 15–20 target | ⚠️ over target |
| Total words | 12,498 | — | ✅ |
| Words / page | 347 | 356 | ✅ **on the money** |
| **Fonts embedded** | **DejaVu-Serif, DejaVu-Sans-Mono, Liberation-Sans** | HendersonBCGSans (brand) | ❌ **ZERO brand fonts** |
| **Chars / line** | **87** | **56** | ❌ 55% too wide |
| **Columns** | **1** | **2** | ❌ |
| **Exhibits present** | **0** (`has_exhibits: false`) | every argument | ❌ |
| Body size | 10.0 pt (69,751 glyphs) | 10.0 pt | ✅ |
| Blank pages | 0 | — | ✅ |
| Template leaks (`{'`, `=None`, `{{page}}`, `Unknown`) | 0 / 0 / 0 / 0 | — | ✅ prior fixes hold |

### 3.2 Root cause #1 — the font pipeline is 100% dead (P0)

Three independent failures stack:

1. `assets/fonts/` contains **only** `.gitkeep` and `README.md`. **Zero `.ttf` files.**
2. The **shipped** `CSS_TEMPLATE` (`presentation_designer.py:186`, 19,315 chars) contains **zero `@font-face` blocks and zero `url()` references** — verified programmatically. It declares `"Instrument Serif"`, `"Source Sans 3"`, `"JetBrains Mono"` and nothing defines them.
3. The **only** file in the repo with `@font-face` is `hyperion/output/templates/styles/hyperion.css` — which is dead code, *and* its `url("../assets/fonts/*.ttf")` resolves to `hyperion/output/assets/fonts/`, **a directory that does not exist**.

⇒ WeasyPrint silently falls back to DejaVu/Liberation on every render. The PDF proves it. This alone destroys the "crystal-clear premium HD font" requirement — the output is typographically indistinguishable from a LaTeX default.

### 3.3 Root cause #2 — the dead-template fork (P0 architectural)

| File | Lines | Status |
|---|---|---|
| `presentation_designer.HTML_TEMPLATE` (inline, :811) | 8,400 chars | ✅ **SHIPS** |
| `presentation_designer.CSS_TEMPLATE` (inline, :186) | 19,315 chars | ✅ **SHIPS** |
| `hyperion/output/templates/report.html.j2` | 323 | ❌ dead |
| `hyperion/output/templates/cover.html.j2` | 38 | ❌ dead |
| `hyperion/output/templates/styles/hyperion.css` | 790 | ❌ dead |

Two parallel, diverging template systems. `render.py` maintains a full `_embed_fonts_in_css` implementation pointed at the dead path. **Any fix applied to the `.j2`/`hyperion.css` files has zero effect on output.** This is a trap for every future contributor.

### 3.4 Root cause #3 — escaped-HTML divergence (P1)

`presentation_designer.py:1947` (fallback render path):
```python
env.filters["md_to_html"] = lambda v: v or ""      # returns plain str
```
vs the production path `TemplateRenderer._markdown_to_html` (`render.py:210`) which returns `markupsafe.Markup`. When the fallback path is taken, Jinja autoescapes the plain string and **literal `<p>` and `<strong>` tags render onto the page.**

### 3.5 Root cause #4 — no visual architecture

87-char single-column justified text with no exhibits, no callouts, no pull-quotes is a wall of text. MBB reports never let the reader's eye travel more than ~1/3 page without a visual anchor. The CSS *has* the classes (`.kpi-strip`, `.key-insight-box`, `.implication-box`, `.exhibit-number`, `.exhibit-note`, `.exhibit-source`) — they are simply **not being populated**, because `synthesis_lead` sets `charts=[]` and `images=[]` and nothing reliably fills them.

### 3.6 Root cause #5 — chart export is broken in this environment (P0 for imagery)

```
$ plotly export test
plotly export FAIL: ValueError — Image export using the "kaleido" engine requires the kaleido package
Warning: You have Plotly version 6.0.1, which is not compatible with this version of Kaleido (1.0.0).
```
Plotly 6.0.1 ⇄ Kaleido 1.0.0 are mutually incompatible. **Every Plotly chart silently degrades to the matplotlib fallback, and if matplotlib is unavailable, to a text table.** Explains `has_exhibits: false` cleanly. Needs a pinned pair (`plotly>=6.1.1` or `kaleido==0.2.1`).

### 3.7 Imagery gaps

- Section header target is **800×400** (`images.py`). BCG's max embedded image is **1660×2346**. We are far below print grade. 300 DPI at 170 mm width needs ≈2000 px.
- `_pick_unused_image` (`:1598`) falls back to `images[0]` at `:1612`, defeating dedup when the LLM term-generation path fails.

### 3.8 Root cause #6 — `.gitignore` structurally forbids the font fix (P0 blocker)

```gitignore
# Assets (cached images, downloaded fonts — gitignored per §9)
assets/fonts/*
!assets/fonts/.gitkeep
```

The fonts are not merely missing — **the repo is configured to guarantee they stay
missing.** `assets/fonts/*` is ignored with only `.gitkeep` whitelisted, so no `.ttf`
can ever be committed. The "downloaded fonts" comment reveals the original intent:
fonts were to be fetched at runtime by the `assets/fonts/README.md` download
commands. That runtime fetch either never ran or was never wired, and there is no
build step, no CI step, and no startup check that performs it.

This makes the font failure **structural, not accidental**, and it means Phase 3.1
cannot begin until `.gitignore` is amended to whitelist the specific font files
(or a fetch step is added to setup and asserted at render time). Either is
acceptable; doing neither guarantees every future report ships in DejaVu.

### 3.9 Chart vocabulary gaps

Supported: bar, line, scatter, histogram, stacked_bar, treemap, sankey, heatmap, radar, waterfall.
**Missing the MBB exhibit vocabulary:** tornado/sensitivity, marimekko (mekko), football-field valuation range, growth-share matrix, bubble-with-size-encoding.

---

## 4. PART B — Are Agents & Sub-Agents Asking the Right Questions? Is Extraction Working?

**Verdict: The query-grounding *library* is excellent. Its *deployment* is broken in three compounding ways, one of which is total.**

### 4.1 ✅ What is genuinely well-built — `hyperion/tools/query_utils.py`

Do not touch this file. It is the strongest module in the repo.

- `normalize_query` — strips debris and internal agent-name tokens, **preserves qualifying digits** ("Scope 3", "Section 301", "ISO 14001"), strips bare years as recency noise, returns `""` below 3 alphabetic tokens
- `is_contentless` — catches template collapse like `"vendor comparison 2024 2025"`, `"carbon footprint emissions data"`
- `ground_query(raw, subject, geography)` — rebuilds from engagement focus when a template collapses; prepends subject if absent; appends geography if absent
- `_GEO_ALIASES` (~90 aliases, all continents) + `_ACRONYM_ONLY_ALIASES = {"US"}` with case-sensitive matching, so the **pronoun "us" cannot hijack a query into "United States"** — a genuinely subtle bug, correctly pre-empted
- `_GEO_PATTERNS` uses alphanumeric lookarounds instead of `\b`, so `"the U.S. market"` matches
- `detect_geographies()` returns `[]` and **never defaults** — honest
- `canonicalize_geographies()` never overrides agent output; keeps unknown labels verbatim
- `_EngagementFocus` is thread-safe

Empirically verified: grounding repaired **6/6** deliberately-collapsed query templates against a Nigeria/lithium-ion focus.

**Also good:** queries are f-string/question-derived throughout (~5–8 literal templates repo-wide, all adaptive). There is no static boilerplate query problem. `world_bank.py` is properly country-parametric. `market_analyst.py` and `financial_analyst.py` both carry an **honest** `geography_mismatch` guard — FRED serves US-only series (`GDP`, `CPIAUCSL`, `PCES`, `DGS10`), so a non-US request records `{"requested": X, "actual": "US", "note": "…must not be presented as X data."}` rather than silently lying. That is exactly the right behaviour for a system claiming multi-country generality.

### 4.2 ❌ FINDING B-1 (P0, TOTAL OUTAGE) — sub-agent research is entirely dead

Covered in §0. Restated as an audit finding:

- `sub_agent.py:626` raises `re.PatternError` unconditionally on Python 3.13
- `_condense_query` is the sole query builder, called at **13 sites**
- Every call site swallows exceptions (`except Exception: pass`)
- **Blast radius: all 12 specialists.** `grep -lc "_spawn_sub_agent" hyperion/agents/specialists/*.py` → all 12 files
- Result: every sub-agent emits a synthetic `research_gap` finding; the report is written from parametric model knowledge, not retrieved evidence
- **Zero test coverage** on `_condense_query`

### 4.3 ❌ FINDING B-2 (P0) — grounding enforced at 1 of 5 search entry points

```
unified_search.py   ground_query = 0
deep_search.py      ground_query = 0
jina.py             ground_query = 0
stealth_search.py   ground_query = 0
searxng.py          ground_query = 2   ← the only guarded door
```

Concretely:
- `unified_search.search()` Step 1 delegates to the grounded SearxNG client (OK), but **Step 2 calls `jina.search(query=query, …)` with the RAW query.**
- `deep_search._discover()` (`:414–417`) fans out **in parallel** to `_search_searxng(...)` *and* `_search_jina(...)`. The Jina leg is ungrounded.

This is precisely the path that historically produced the "Best Buy / Greek e-shop" irrelevant sources. Half of every discovery fan-out is ungrounded.

### 4.4 ❌ FINDING B-3 (P0) — no agent or sub-agent ever *reasons* about what to search

`grep -E "_llm_complete|generate.*quer|query.*llm" hyperion/agents/sub_agent.py` → **no matches.** There is no LLM query-planning step anywhere in the sub-agent path. Query construction is a pure regex + stopword pipeline. Its `filler` set deletes the exact words that carry a consulting question's analytical intent:

```python
'should', 'now', 'is', 'are', 'how', 'why', 'what', 'which',
'not', 'no', 'nor', 'only', 'more', 'most', 'can', 'will', 'would', 'could', …
```

Consequences:
- `re.sub(r'\([^)]*\)', '', q)` **destroys parentheticals** — often where the specific entities live (`"(Bitcoin, Ethereum)"`)
- the em-dash rule was *intended* to drop everything after `—`; it now just crashes
- `'not'` in the filler set **inverts the meaning** of any negative-framed question
- hard 120-char truncation
- **exactly one query per tool.** No query-set expansion. No decomposition into sub-questions. No reformulation when yield is low. No follow-up on a promising lead.

A human MBB associate given "should we enter now or wait?" runs 8–15 differently-angled searches. HYPERION runs one, built by a stopword filter — and currently, zero.

### 4.5 ❌ FINDING B-4 (P1) — `UnifiedExtract` is entirely unwired

`UnifiedExtract` implements a 7-tier cheap-first stealth ladder (curl_cffi → Jina → Obscura → nodriver → Crawl4AI → Camoufox → Wayback). Consumer audit:

```
grep -rn "unified_extract|UnifiedExtract" --include=*.py .
  → hyperion/tools/__init__.py:27   (import)
  → hyperion/tools/__init__.py:108  (__all__)
  → hyperion/tools/__init__.py:109  (__all__)
```

**Zero agents use it.** It is exported and never called. Meanwhile `sub_agent._gather_raw_data` hand-rolls its *own* 5-tier ladder inline (Obscura → Scrapling → Jina → Crawl4AI → FlareSolverr) and `deep_search._extract_batch` hand-rolls a *third* one (Jina → HTTP → Obscura → Crawl4AI → FlareSolverr). Three divergent implementations of the same ladder; the best-engineered one is dead.

### 4.6 ❌ FINDING B-5 (P0 environment) — the extraction stack is not installed

```
MISS trafilatura   MISS crawl4ai   MISS scrapling
MISS curl_cffi     MISS nodriver   MISS camoufox    MISS playwright
```

`http_extract.py` *is* the keyless, browserless workhorse (httpx + trafilatura) and it returns `error="trafilatura not installed — run: pip install trafilatura"`. So in `deep_search._extract_batch`, Tier 1 (Jina) needs network, **Tier 2 (HTTP/trafilatura) is guaranteed to fail**, Tiers 3–5 have no modules. Combined with B-1, retrieval is zero at both the query layer and the extraction layer. Also note `playwright` is missing, so the PDF renderer has **no fallback** if WeasyPrint fails.

### 4.7 ⚠️ FINDING B-6 (P1) — extraction is truncated at 15,000 chars, unweighted

`MAX_CONTENT_CHARS = 15000` in both `deep_search.py:43` and `http_extract.py:34`, applied as a blind head-slice `content[:15000]`. A 60-page IEA or IMF PDF is cut at ~12% with **no relevance-aware selection** — the tables and conclusions (usually the back half) are discarded. There is no chunk-and-rank step.

### 4.8 ✅/⚠️ FINDING B-7 — `evidence_scorer` gate is real but shallow

**Good:** it genuinely rejects garbage. `DENIED_DOMAINS` (30 retail/social/travel domains incl. `bestbuy.com`), `DENIED_DOMAIN_SUBSTRINGS` (`shop`, `store`, `eshop`, `buy`, `deals`, `coupon`, `affiliate`…), and a `MIN_RELEVANCE = 0.08` floor — both enforced *before* scoring with `logger.info` on every rejection (fail-loud). `KNOWN_DOMAINS` has ~50 sensible credibility weights (`sec.gov` 0.95 … `quora.com` 0.25). Composite = 0.35 relevance + 0.25 credibility + 0.15 freshness + 0.25 evidence.

**Weak:**
- `_score_relevance` is bag-of-words substring counting. `if word in content_lower` matches **substrings, not tokens** — `"ai"` matches `"said"`, `"chain"`, `"maintain"`. Relevance is systematically inflated.
- No semantic similarity, so a topically-adjacent-but-wrong page passes easily.
- `MIN_RELEVANCE = 0.08` is extremely permissive — 1 keyword in 12 passes.
- `summarize()` sets `overall_stance = "insufficient"` when `total < 3`, but `confidence` is still computed and returned — a downstream consumer reading `confidence` without checking `overall_stance` gets a misleadingly high number.

### 4.9 ⚠️ FINDING B-8 (P2) — Fact Checker query is unguarded and untruncated-by-meaning

`fact_checker.py:605`:
```python
query = claim.claim[:100]
if claim.agent:
    query = f"{query} {claim.agent.replace('_', ' ')}"
```
Two problems: (1) a blind 100-char slice of a claim sentence is not a search query; (2) **appending the internal agent name** (`"market analyst"`, `"risk analyst"`) injects HYPERION's own org vocabulary into the search string — which is exactly the debris `normalize_query` was written to strip. And it never calls `ground_query`. Verification searches are therefore polluted at source.

### 4.10 ⚠️ FINDING B-9 (P2) — `resolve_subject` missing from 3 specialists

Imported by 9 of 12. **Missing from `market_analyst.py`, `regulatory_analyst.py`, `risk_analyst.py`** — three of the highest-search-volume agents. They resolve subject ad hoc, so their queries are the most likely to collapse into contentless templates.

### 4.11 ⚠️ FINDING B-10 (P2) — no page/word budget tied to the 15–20 page target

`synthesis_lead._build_one_section` prompts for "2000-4000 words" and rejects below 800 chars. Section count = `len(self._findings_by_agent)` — i.e. **however many agents happened to report.** Nothing anywhere maps to the 15–20 page target; the measured 36 pages is an emergent accident. `render.py:889` asserts `15 <= page_count <= 40`, which is a 25-page-wide "success" window.

---

## 5. PART C — Tool Utilisation

### 5.1 Are we using our tools to their potential?

| Tool | Wired? | Verdict |
|---|---|---|
| `query_utils` (grounding) | 1 of 5 search doors | ❌ under-deployed — best module, least used |
| SearXNG | yes, grounded, budgeted, cached | ✅ correct |
| Jina (search + reader) | yes | ⚠️ ungrounded on search |
| `UnifiedExtract` (7-tier) | **no consumers** | ❌ dead |
| trafilatura / crawl4ai / scrapling / curl_cffi / nodriver / camoufox | not installed | ❌ 0% |
| SEC EDGAR, OpenAlex, Semantic Scholar, World Bank, FRED, Alpha Vantage, Google Trends, HN, Reddit, Wayback | registered in sub-agent | ❌ **unreachable — all behind the crashing `_condense_query`** |
| Plotly + kaleido | version-incompatible | ❌ silently degraded |
| WeasyPrint | works | ✅ |
| Playwright (PDF fallback) | not installed | ❌ no fallback |
| Fonts | `assets/fonts/` empty | ❌ 0% |
| `evidence_scorer` | yes | ⚠️ substring relevance |
| `chart_specs` mining | yes | ✅ honest (returns `[]` rather than inventing) |
| `pytest-asyncio` | declared in `pyproject` dev extras, absent from env | ⚠️ env drift |

**Blunt summary: roughly half the tool surface is unreachable, uninstalled, or unwired.** The architecture is far better than the wiring.

### 5.2 Recommended additional tools

**Retrieval quality (highest leverage):**
1. **A reranker** — `bge-reranker-v2-m3` or Cohere Rerank. Directly fixes B-7's substring relevance and B-6's blind truncation: chunk pages, rerank chunks against the sub-question, keep the top 15k chars *by relevance* instead of the first 15k *by position*.
2. **Embeddings + a vector store** (`fastembed`/`sentence-transformers` + `sqlite-vec` or LanceDB — no new infra). Enables semantic dedup, cross-agent evidence reuse, and a real Second Brain.
3. **`tenacity`-backed structured query planner** — an LLM step that emits **5–10 diversified queries** per sub-question (entity, metric, counter-thesis, regulatory, competitor, time-series angles) with a schema-validated output. This is the fix for B-3.
4. **`ddgs` / Brave Search API / Tavily** as a third discovery leg — removes the single-point dependency on SearXNG instance health.

**Data depth:**
5. **`yfinance` + OECD SDMX + Eurostat + IMF SDMX** — kills the FRED US-only ceiling honestly instead of flagging it.
6. **`pdfplumber` / `camelot`** — table extraction from the PDFs that IEA/IMF/World Bank publish. Right now a 60-page PDF yields prose only; the tables are where the exhibits live.

**Output quality:**
7. **Pin `plotly>=6.1.1` with `kaleido>=1.0`** (or `kaleido==0.2.1` with plotly 6.0.x). Add a CI smoke test that asserts `fig.to_image()` returns >1 kB.
8. **Vendor the fonts** — Instrument Serif, Source Sans 3, JetBrains Mono as `.ttf` in `assets/fonts/`, base64-embedded into the shipped `CSS_TEMPLATE`.
9. **`pikepdf`/`qpdf`** — PDF/A-2b post-pass, metadata, outline/bookmarks. Free credibility.
10. **`playwright`** installed as the real PDF fallback.

**Engineering hygiene:**
11. **`ruff` + `mypy --strict` in pre-commit.** Ruff's `RUF039`/regex lints and mypy would have caught `[\u2014\u2013--]` before it shipped. **This is the process fix for the P0.**
12. **A golden-PDF regression test** — assert on the probe metrics (embedded font families, chars/line, exhibit count, leak counts) so typography can never silently regress again.
13. **`pytest --cov` with a floor**, plus a `tests/test_query_pipeline.py` that actually calls `_condense_query`.

---

## 6. Phase-Wise Fix & Upgrade Plan

### PHASE 0 — STOP THE BLEEDING (hours, not days)

| # | Fix | File | Why |
|---|---|---|---|
| 0.1 | **Fix the regex character class** → `r'\s*[\u2013\u2014-]+\s*'` (hyphen last, or escaped) | `sub_agent.py:626` | Restores ALL sub-agent research. Highest ROI change in the repo. |
| 0.2 | Add `tests/test_sub_agent_query.py` asserting `_condense_query` never raises across ~30 inputs incl. em-dash, en-dash, hyphen, parentheses, unicode | new | Prevents recurrence |
| 0.3 | Replace every `except Exception: pass` in the search legs with `logger.warning(..., exc_info=True)` | `sub_agent.py:672, 689` | **A silent failure caused a 100% outage. Fail loud.** |
| 0.4 | Pin `plotly`/`kaleido` to a compatible pair; smoke-test `to_image()` | `pyproject.toml` | Restores charts → restores exhibits |
| 0.5 | `pip install trafilatura playwright` + `playwright install chromium`; add to dev extras | env | Unblocks extraction Tier 2 + PDF fallback |
| 0.6 | Install `pytest-asyncio` in the standard env setup (already in `pyproject` dev) | env | 345 tests only pass with it |

**Exit criterion:** a live sub-agent run returns non-empty `raw_data` and at least one non-`research_gap` finding with a real source URL.

### PHASE 1 — GROUNDING & QUERY INTELLIGENCE (week 1)

| # | Fix | File |
|---|---|---|
| 1.1 | Call `ground_query` at **all** search entry points | `unified_search.py`, `deep_search.py`, `jina.py`, `stealth_search.py` |
| 1.2 | Move the grounding call into a single shared decorator/guard so a new search tool cannot be added ungrounded | `query_utils.py` |
| 1.3 | **Add an LLM query planner**: sub-question → 5–10 schema-validated diversified queries (entity / metric / counter-thesis / regulatory / competitor / time-series). Run at FAST tier, cache by sub-question hash. | `sub_agent.py` |
| 1.4 | Remove intent-destroying words from `filler` (`not`, `should`, `how`, `why`, `what`, `which`, `most`, `more`) and **keep parentheticals as a second query variant** rather than deleting them | `sub_agent.py` |
| 1.5 | Add low-yield reformulation: if a query returns <3 scored results, broaden (drop geography) and retry once | `sub_agent.py` |
| 1.6 | Import + use `resolve_subject` in `market_analyst.py`, `regulatory_analyst.py`, `risk_analyst.py` | 3 specialists |
| 1.7 | Fact Checker: stop appending the internal agent name; extract the claim's entity+metric and `ground_query` it | `fact_checker.py:605` |

**Exit criterion:** ≥8 distinct grounded queries per sub-question; 0 ungrounded search calls provable by grep; no internal agent vocabulary in any outbound query.

### PHASE 2 — EXTRACTION & EVIDENCE (week 2)

| # | Fix |
|---|---|
| 2.1 | **Delete two of the three extraction ladders.** Make `UnifiedExtract` the single implementation and wire `sub_agent` + `deep_search` to it. |
| 2.2 | Replace blind `content[:15000]` with **chunk → rerank → top-k-by-relevance** assembly |
| 2.3 | Add `pdfplumber`/`camelot` table extraction for PDF sources; feed tables to `chart_specs` |
| 2.4 | `evidence_scorer._score_relevance`: token-boundary matching (not substring) + optional embedding cosine; raise `MIN_RELEVANCE` after measuring the new distribution |
| 2.5 | Make `summarize()` refuse to emit a `confidence` above 0.3 when `overall_stance == "insufficient"` |
| 2.6 | Log an extraction-yield metric per engagement (`urls_discovered`, `urls_extracted`, `chars_retained`, `sources_cited`) and surface it in the run report |

**Exit criterion:** extraction success ≥60% of discovered URLs; every cited source has ≥500 chars of retained, reranked content.

### PHASE 3 — TYPOGRAPHY & VISUAL ARCHITECTURE (week 3)

| # | Fix |
|---|---|
| 3.1 | **Vendor the `.ttf` files** into `assets/fonts/` |
| 3.2 | **Inject `@font-face` with base64 data-URIs into the shipped `CSS_TEMPLATE`** — assert in a test that the output PDF embeds `InstrumentSerif`/`SourceSans3` and **not** DejaVu |
| 3.3 | **Delete the dead templates** (`report.html.j2`, `cover.html.j2`, `hyperion.css`) or make them the single source and delete the inline strings. **One template system, not two.** |
| 3.4 | Two-column body via `column-count: 2; column-gap: 7mm` targeting **56 chars/line**; keep exhibits/KPI strips full-bleed with `column-span: all` |
| 3.5 | Fix `presentation_designer.py:1947` — register the real `Markup`-returning filter in the fallback env |
| 3.6 | Raise section image target to ≥2000 px wide; keep the no-upscale rule |
| 3.7 | Wire the exhibit path end-to-end so `has_exhibits: true` and every section carries ≥1 exhibit with `Note:` + `Source:` |

**Exit criterion:** probe reports brand fonts embedded, 52–60 chars/line, 2 columns, `has_exhibits: true`, ≥1 exhibit per section.

### PHASE 4 — DEPTH CONTROL & MBB EXHIBIT VOCABULARY (week 4)

| # | Fix |
|---|---|
| 4.1 | Introduce an explicit **page budget**: target pages → word budget → per-section word allocation, passed into `_build_one_section` |
| 4.2 | Narrow `render.py:889` to the actual contract (`15 <= pages <= 22`) and make violation a quality-gate failure, not a log line |
| 4.3 | Add tornado/sensitivity, marimekko, football-field, growth-share matrix, bubble chart to `charts.py` |
| 4.4 | Enforce MGI exhibit anatomy in the template: number → action title → figure → `Note:` → `Source:` |
| 4.5 | Add the missing MBB front/back matter: At-a-glance, Technical appendix, Endnotes |

### PHASE 5 — HARDENING (ongoing)

| # | Fix |
|---|---|
| 5.1 | `ruff` + `mypy --strict` in pre-commit — **the process fix that prevents the next P0** |
| 5.2 | Golden-PDF regression test asserting probe metrics |
| 5.3 | Coverage floor; ban bare `except Exception: pass` via ruff `BLE001`/`S110` |
| 5.4 | Add the reranker + embeddings + `sqlite-vec` Second Brain |
| 5.5 | OECD/Eurostat/IMF SDMX + `yfinance` to break the FRED US-only ceiling |
| 5.6 | PDF/A-2b post-pass via `pikepdf` + bookmarks |

---

## 7. Scorecard

| Dimension | Grade | Note |
|---|---|---|
| Architecture & separation of concerns | **A−** | 20 agents, 5 stages, AgentBus, adaptive replanning — genuinely not an LLM wrapper |
| Query grounding *library* | **A** | `query_utils.py` is excellent; the "us"/"US" case-sensitivity catch is expert-level |
| Query grounding *deployment* | **F** | 1 of 5 doors guarded |
| Query *intelligence* (reasoning) | **F** | no LLM planning; stopword filter deletes analytical intent |
| Sub-agent retrieval | **F** | 100% outage from one regex character |
| Extraction | **F** | best ladder unwired; stack uninstalled; blind truncation |
| Evidence scoring | **C+** | real deny-list + fail-loud logging; substring relevance |
| Generality (domain/country/workflow) | **A−** | no hardcoded country logic; FRED mismatch honestly flagged; World Bank parametric |
| Content volume | **B+** | 347 w/p vs 356 benchmark |
| Depth *control* | **C** | 36 pages vs 15–20 target; emergent, not budgeted |
| Typography | **F** | zero brand fonts embedded |
| Layout & visual architecture | **D** | 87 chars/line, 1 column, 0 exhibits |
| Data integrity / leak hygiene | **A** | 0 leaks, 0 blank pages — prior fixes hold |
| Test suite health | **B−** | 345 pass, but 0 coverage on the code path that caused a total outage |

**Overall: the design is MBB-grade. The wiring is not.** Nothing in this report requires re-architecting HYPERION. Phase 0 is a handful of lines and restores the entire research capability. Phases 1–3 are wiring, not invention.

---

## 8. Answering Your Questions Directly

**"Are we able to get the depth of the content we want?"**
Volume yes — 347 words/page against BCG's 356, 12,498 words. But it is currently 36 pages against a 15–20 target, un-budgeted, and — critically — the words are **not grounded in retrieved evidence**, because sub-agent research returns nothing (§4.2). Depth of *text* yes. Depth of *research* currently zero.

**"The premium formatting and the look?"**
No. Three hard failures: **zero brand fonts embedded** (DejaVu/Liberation ship in every PDF), **87 chars/line in a single column** vs the benchmark's 56 in two, and **zero exhibits**. The CSS classes for premium layout exist and are simply not populated. Charts additionally fail at the `plotly`/`kaleido` version boundary.

**"Are agents and sub-agents searching the right questions/queries per the problem statement?"**
The *library* for this is excellent and correctly designed. But: sub-agents are searching **nothing at all** (P0 regex crash); grounding is enforced at **1 of 5** entry points; and **no agent ever reasons about what to search** — one regex-condensed query per tool, with the analytical intent words (`should`, `how`, `why`, `not`, `most`) deleted by a stopword filter.

**"Is the search and information properly extracted?"**
No. The best extraction implementation (`UnifiedExtract`, 7 tiers) has **zero consumers**. Six of seven extraction libraries are **not installed**. Content is truncated at a blind 15,000-char head-slice with no relevance weighting. The evidence gate does real work (deny-lists, relevance floor, fail-loud logging) but scores relevance by substring matching, which systematically inflates it.

**"Are we using all tools to their best potential?"**
No — roughly half the tool surface is unreachable, uninstalled, or unwired. Ten data tools (SEC EDGAR, OpenAlex, Semantic Scholar, World Bank, FRED, Alpha Vantage, Google Trends, HackerNews, Reddit, Wayback) are correctly registered and **all sit behind the crashing query builder.** §5.2 lists 13 additions, with a reranker and an LLM query planner as the two highest-leverage.

---

## 9. Appendix — Evidence Index

| Claim | Command / instrument |
|---|---|
| `_condense_query` crashes unconditionally | live `python3 -c` against `SubAgentRunner._condense_query` |
| Both search legs return empty silently | live `asyncio.run(r._search_searxng())` → `('searxng', [], None)` |
| 13 call sites | `grep -n "_condense_query(" hyperion/agents/sub_agent.py` |
| Zero test coverage | `grep -rn "_condense_query" tests/` → no matches |
| All 12 specialists affected | `grep -lc "_spawn_sub_agent" hyperion/agents/specialists/*.py` |
| Grounding 1-of-5 | `grep -c ground_query` per file |
| `UnifiedExtract` unwired | `grep -rn "UnifiedExtract" --include=*.py .` → only `__init__.py` |
| 6 extraction libs missing | `importlib.import_module` loop |
| Plotly export broken | live `fig.to_image()` → `ValueError` |
| Zero `@font-face` in shipped CSS | programmatic scan of `CSS_TEMPLATE` |
| `assets/fonts/` empty | `ls assets/fonts/` → `.gitkeep`, `README.md` |
| 36 pages / 12,498 words / 347 w-p / DejaVu / no exhibits | `reports/_audit/probe_metrics.json` via `tools/audit_render_probe.py` |
| BCG 356 w-p, 56 chars/line, 2-col, HendersonBCGSans | PyMuPDF forensic pass |
| MGI structure + exhibit anatomy | `crawler` tool (PDF download blocked) |
| 345 tests pass | `python3 -m pytest tests/ -q` |

---

## 10. Live Execution Tracker

This section is the working checklist. It is updated in place as fixes land, so this
file is both the audit and the burn-down chart. `[ ]` = not started, `[~]` = in
progress, `[x]` = landed with proof.

### Phase 0 — Stop the bleeding
- [x] **0.1** Fix regex character class `sub_agent.py:626` → restores all sub-agent research
  - Changed `r'\s*[\u2014\u2013--]+\s*'` → `r'\s*[\u2013\u2014-]+\s*'` (hyphen moved to
    last position in the character class so it is a literal, not a range operator).
    Verified live: `SubAgentRunner._condense_query("Find market size in Nigeria — 2024 data")`
    now returns `'market size Nigeria 2024 data'` instead of raising `re.PatternError`.
- [x] **0.2** `tests/test_sub_agent_query.py` — assert `_condense_query` never raises
  - New file, 41 tests: parametrized adversarial corpus (em-dash, en-dash, hyphen,
    doubled hyphens, parentheticals, unicode, empty/whitespace-only, 500-char strings,
    a direct regex-pattern compile check) + behavioral sanity checks. All 41 pass.
- [x] **0.3** Replace silent `except Exception: pass` in search legs with loud logging
  - Added module logger (`logging.getLogger(__name__)`). `_search_searxng` and
    `_search_jina` now `logger.warning(..., exc_info=True)` on failure instead of
    swallowing silently. The 5 per-URL extraction-tier inner loops (Obscura,
    Scrapling, Jina Reader, Crawl4AI, FlareSolverr) now `logger.debug(...)` each
    per-URL failure instead of a bare `except Exception: continue`.
  - Full suite re-run after these three fixes: **506 passed, 3 skipped** (was 465
    passed pre-fix; +41 from the new test file, zero regressions).
- [x] **0.4** Pin `plotly`/`kaleido` compatible pair + `to_image()` smoke test
  - `pyproject.toml` already specified `kaleido>=0.2.1,<1.0` (the *correct* pin —
    the audit's live probe had `kaleido==1.0.0` installed in the sandbox from a
    stale environment, not from the pin). Reinstalling from `pyproject.toml` via
    `pip install -e ".[dev,stealth]"` resolved `kaleido==0.2.1` + `plotly==6.0.1`,
    and `fig.to_image(format="png")` now returns 14,223 bytes (no `ValueError`).
    Added `tests/test_chart_export_smoke.py` asserting `to_image()` works and
    returns >1 kB, so a future stale/incompatible pin fails CI instead of
    silently degrading every chart to the matplotlib fallback.
- [x] **0.5** Install `trafilatura` + `playwright` (+ chromium) and add to dev extras
  - Installed via the project's own `pip install -e ".[dev,stealth]"` — this also
    resolved `crawl4ai`, `curl_cffi`, `scrapling`, `nodriver`/`camoufox` (stealth
    extra) into the environment; ran `playwright install chromium` (downloaded
    Chrome for Testing 148 + ffmpeg + headless-shell). `pyproject.toml` already
    listed these as direct/optional deps — the audit's "MISS" results reflected
    an environment that hadn't run `pip install -e .` yet, not a missing pin.
- [x] **0.6** Ensure `pytest-asyncio` in standard env setup
  - Installed as part of `.[dev]` extra (`pytest-asyncio>=0.23.0` in `pyproject.toml`,
    resolved to `1.4.0`). `asyncio_mode = "auto"` already configured in
    `[tool.pytest.ini_options]`. Full suite: 506 passed, 3 skipped.

- [x] **0.4b (found while verifying 0.4)** — three additional, independent
  Plotly bugs in `hyperion/output/charts.py` that were silently degrading
  charts to the matplotlib/data-table fallback tiers on **every single
  call**, compounding the `has_exhibits: false` finding from §3.6:
  1. `fig.update_yaxis(rangemode="tozero")` — `update_yaxis` (singular) has
     never existed on a Plotly `Figure`; the real method is `update_yaxes`
     (plural). Raised `AttributeError` on every `bar`/`stacked_bar` chart —
     the single most common chart type — and `AttributeError` was **not**
     in the `except (ValueError, RuntimeError, OSError, ImportError)` tuple
     in `generate()`, so it propagated past all 3 fallback tiers instead of
     degrading gracefully. **Fixed**: `update_yaxes`.
  2. `fig.update_traces(marker_line_width=0, opacity=0.95)` was applied
     unconditionally to every chart type in `_apply_brand_styling`, but
     `Sankey` supports neither `marker` nor `opacity` at the trace level,
     and `Heatmap`/`Waterfall` support `opacity` but not `marker`. This
     raised `ValueError` on **100% of sankey/heatmap/waterfall calls**,
     meaning those three chart types had *never* actually rendered via
     Plotly in production — every one silently fell to the matplotlib
     Tier 2 fallback. **Fixed**: scoped the styling call per trace-type
     via `selector=`, wrapped in `try/except (ValueError, TypeError): pass`
     so cosmetic styling can never again break chart generation.
  3. Widened `generate()`'s Tier-1 exception tuple from
     `(ValueError, RuntimeError, OSError, ImportError)` to also catch
     `AttributeError, TypeError, KeyError` — defense in depth so a future
     coding-error-class bug in the styling/creation path degrades to
     Tier 2/3 instead of crashing the whole engagement's chart generation.
  - Verified: all 10 chart types (`bar, line, scatter, histogram,
    stacked_bar, treemap, sankey, heatmap, radar, waterfall`) now render
    successfully via **Tier 1 Plotly+kaleido** (previously sankey, heatmap,
    and waterfall silently used Tier 2 matplotlib; bar/stacked_bar crashed
    past all tiers). New tests in `tests/test_chart_export_smoke.py` lock
    this in. Full suite: **509 passed, 3 skipped** (was 465 pre-Phase-0).

### Phase 1 — Grounding & query intelligence
- [x] **1.1** `ground_query` at all 5 search entry points
  - Before: `grep -c ground_query` → `unified_search.py=0, deep_search.py=0,
    jina.py=0, stealth_search.py=0, searxng.py=2` (audit §4.3 Finding B-2).
  - **`jina.py`** (`JinaClient.search()`): added the exact grounding block
    already proven in `searxng.py` — capture `original_query`, call the
    grounding helper, drop with `logger.warning(...)` if the query has no
    subject after grounding (returning an empty `JinaSearchResponse`
    instead of firing a useless request), `logger.info(...)` if the query
    changed. This directly fixes the "Step 2 calls `jina.search(query=query,
    …)` with the RAW query" half of Finding B-2 — and because
    `unified_search.py` and `deep_search.py` both call into
    `JinaClient.search()`, their own Jina legs are fixed transitively by
    this one change, without needing a duplicate call at each orchestration
    layer.
  - **`stealth_search.py`** (`StealthSearchClient.search()`): added the same
    grounding block immediately after the `_check_available()` gate and
    before `_launch_browser()`. This was the most important of the four —
    Stealth is the last-resort tier (real headless Chromium launch), so an
    ungrounded query reaching it previously burned the single most
    expensive operation in the entire fallback ladder on a search that
    could not answer the user's question.
  - **`deep_search.py`** (`DeepSearchClient.search()`, the entry point every
    specialist actually calls): grounded once at the very top, before
    `_discover()` fans out in parallel to `_search_searxng()` *and*
    `_search_jina()` — this is the literal fix for "`deep_search._discover()`
    (:414–417) fans out in parallel … the Jina leg is ungrounded", applied
    at the point where both legs originate so they can never diverge again.
  - **`unified_search.py`**: audited and *deliberately left unchanged* at
    its own orchestration layer — see 1.2 below for why, and why that is
    still a complete fix rather than a gap.
  - **`searxng.py`**: unchanged in behaviour, refactored in 1.2 to call the
    new shared choke point instead of its original hand-rolled inline block
    (still `grep -c` ≥ 1, now via the shared helper).
  - Verified live (no mocks) with `set_engagement_focus`/`clear_engagement_focus`:
    a contentless query (`"2024 2025 $100 50%"`) run through
    `DeepSearchClient().search()` and `UnifiedSearch().search()` both
    returned zero results with `error` populated, **without** the request
    ever reaching a live network/browser boundary — confirmed by
    `errors == {"searxng": "returned no results", "jina": "returned no
    results", ...}` rather than a connection-timeout style failure, i.e.
    the search was never attempted at all, it was dropped before dispatch.
  - After: `grep -c 'ground_query\|grounded_search_or_empty'` →
    `unified_search.py=3, deep_search.py=2, jina.py=3, stealth_search.py=3,
    searxng.py=3`. All 5 entry points now non-zero.
- [x] **1.2** Shared grounding guard so new tools cannot be added ungrounded
  - Added `grounded_search_or_empty(raw, empty_factory, subject="",
    geography="", *, logger=None, tool_name="search")` to
    `hyperion/tools/query_utils.py` — the single choke point that captures
    the original query, calls `ground_query`, logs+drops with a consistent
    message format if it came back empty (returning the caller-supplied
    "empty response" object), logs if the query changed, and returns
    `(grounded_query, None)` on success or `("", empty_response)` on drop.
    Also added `ground_query_or_raise()` + `ContentlessQueryError` for call
    sites that should fail loudly rather than degrade to an empty result.
  - Refactored **both** pre-existing grounding call sites (`searxng.py`,
    and the newly-fixed `jina.py`/`stealth_search.py`/`deep_search.py`) to
    call this shared helper instead of re-implementing the same
    ground/log/drop sequence inline — so the five entry points cannot drift
    from each other's behaviour, and a sixth search tool added later gets
    identical grounding semantics for free by calling the same helper.
  - **`unified_search.py` — the one deliberate exception, and why it is
    still correct.** A first attempt added the same top-level
    `grounded_search_or_empty` call at the start of `UnifiedSearch.search()`
    (matching the audit's literal "grounding at all 5 entry points"
    wording). Running the full suite immediately surfaced 4 regressions in
    `tests/test_tool_capability_gating.py`
    (`TestUnifiedSearchGating::test_empty_result_reports_why`,
    `::test_tools_used_excludes_tiers_that_produced_nothing`,
    `TestSearchNewsIsReachable::test_time_range_reaches_searxng`,
    `TestStealthSearchIsUsable::test_stealth_only_runs_when_text_tiers_found_nothing`)
    — all four use a bare placeholder query (`"q"`) against fully-mocked
    leaf clients to test *tier-selection/fan-out logic* in isolation from
    query semantics, and grounding at that layer silently ate the
    placeholder before it ever reached the (correctly) mocked tier,
    collapsing "tier X behaves correctly when mocked" into "tier X was
    never reached because grounding intercepted it first" — a regression in
    the *meaning* of those tests, not just their pass/fail state. Reverted
    the top-level call; `unified_search.py` instead relies on the fact that
    every leaf tier it calls (`SearxNGClient.search`, `JinaClient.search`,
    `StealthSearchClient.search`) now grounds internally at its own
    network/browser boundary (fixed above), which is the actual point where
    an ungrounded query does damage (a real HTTP request or a real browser
    launch). `Obscura`'s step is unaffected either way since it re-fetches
    already-discovered URLs and never takes a query. A code comment
    documenting this decision (and the four failing tests it caused) is now
    in `unified_search.py` immediately before Step 1, so a future editor
    does not re-introduce the same regression by "fixing" the same grep gap
    the same way.
  - New test file `tests/test_search_grounding.py` (21 tests): direct tests
    of `grounded_search_or_empty`/`ground_query_or_raise`; per-entry-point
    tests that monkeypatch each client's actual network/browser boundary
    method (`SearxNGClient._search_single_attempt`, `JinaClient._get_client`,
    `StealthSearchClient._launch_browser`, `DeepSearchClient._discover`) to
    raise `AssertionError` if reached, proving a contentless query is
    dropped *before* that boundary rather than merely asserting on the
    returned value; a `unified_search.py`-specific test that grounds via
    the *real* (unmocked) `SearxNGClient` to verify the transitive-fix
    property described above; and a parametrized coverage-regression test
    over the four entry points that ground directly, asserting each module
    imports `grounded_search_or_empty` into its own namespace.
  - Full suite after 1.1+1.2: **525 passed, 3 skipped** (was 509 passed,
    3 skipped pre-Phase-1; +16 net new tests, zero regressions after the
    `unified_search.py` revert described above).
- [ ] **1.3** LLM query planner — 5–10 diversified queries per sub-question
- [x] **1.4** Purge intent-destroying words from `filler`; keep parentheticals as a variant
  - **Before**: `sub_agent.py`'s `_condense_query` `filler` set (used by all 12
    specialists' 15 sub-agent search/scrape methods via
    `SubAgentRunner._condense_query`, audit §4.4 Finding B-3) included
    `'not'`, `'should'`, `'how'`, `'why'`, `'what'`, `'which'`, `'most'`,
    `'more'` — every one of these is intent-carrying, not grammatical
    filler: stripping `'not'` inverts a negated question into its opposite
    (`"Should we NOT enter this market?"` → after stripping `not`, the
    condensed query reads as an unqualified *enter-market* search, the
    literal opposite of what was asked); stripping `'should'`/`'how'`/
    `'why'`/`'what'`/`'which'` deletes the interrogative that tells a
    search engine what KIND of answer is wanted; stripping `'most'`/
    `'more'` deletes superlative/comparative scope (`"the most affected
    sectors"` → `"affected sectors"`, silently broadening the intended
    scope). Separately, `_condense_query` unconditionally deleted
    parenthetical asides (`r'\([^)]*\)'`) with no recovery path, so a
    question like `"Should we enter now or wait? (Bitcoin, Ethereum)"`
    lost the only tokens naming the actual subject entities.
  - **Fixed — filler set**: removed all 8 words above from `filler` (verified
    live: `SubAgentRunner._condense_query("Should we NOT enter this
    market?")` now retains `"Should NOT enter market?"`-equivalent tokens
    instead of silently dropping the negation/interrogative/modal).
  - **Fixed — parentheticals**: added a new classmethod
    `_condense_query_variants(question, max_len=120) -> list[str]` that
    keeps `_condense_query`'s existing parenthetical-stripping contract
    unchanged for its primary output (zero behavioural change for the 11
    other call sites — Wayback, Alpha Vantage, FRED, SEC EDGAR, Semantic
    Scholar, OpenAlex, Google Trends, HackerNews, Reddit, Second Brain —
    which still call the single-query `_condense_query` directly and are
    therefore unaffected), and additionally returns a **second** variant
    that folds the parenthetical's content back in — but only when the
    parenthetical looks like a real entity list rather than an
    instructional aside (`"(see above)"`, bare `"(e.g.)"`/`"(etc.)"`
    alone are filtered out as trivial). A leading `"e.g."`/`"i.e."`/
    `"etc."` label *inside* an otherwise-real entity parenthetical (e.g.
    `"(e.g. Salesforce, HubSpot)"`) is stripped from the variant so the
    literal abbreviation token doesn't ride along into the search query —
    verified live: `_condense_query_variants("Compare vendor pricing (e.g.
    Salesforce, HubSpot)")` → `["Compare vendor pricing", "Compare vendor
    pricing Salesforce, HubSpot"]`. Only wired into the two callers that
    already fan out in parallel and can afford a second search leg without
    doubling every tool call in the whole pipeline: `_search_searxng` and
    `_search_jina` now loop over `_condense_query_variants(...)`, run each
    variant, and merge+dedup results by URL (first-seen order preserved)
    before formatting — so a named-entity comparison question fires one
    query anchored on the general topic and one anchored on the named
    entities, instead of the entities being silently discarded.
  - Verified live (no mocks): confirmed the exact filler-word removal via
    inline `_condense_query` calls on words `not/should/how/why/what/
    which/most/more`, and confirmed `_condense_query_variants` recovers
    `"Bitcoin, Ethereum"` as a second variant from
    `"Should we enter now or wait? (Bitcoin, Ethereum)"` while the primary
    variant stays entity-free (preserving `_condense_query`'s existing
    contract) — also confirmed trivial parentheticals (`"(see above)"`,
    `"(etc.)"`) correctly produce only 1 variant, and the `max_len` cap is
    respected on both variants independently.
  - New tests added to `tests/test_sub_agent_query.py` (24 net new,
    41 → 65 → 66 after one follow-up regression test): `TestCondenseQuery
    IntentPreservation` (7 tests: parametrized survival check for all 8
    removed filler words, explicit negation/superlative/comparative/
    interrogative semantic-preservation assertions, one control test
    confirming genuine grammatical filler — `"the"`, `"of"`, `"in"` — is
    still stripped); `TestCondenseQueryVariants` (7 tests: single-variant
    when no parenthetical, never-empty-list guarantee, entity parenthetical
    → 2 variants with entities isolated to the second, the `"(e.g.
    Salesforce, HubSpot)"` label-stripping regression test added during
    this fix's polish pass, trivial-parenthetical → 1 variant, `max_len`
    respected on both variants, and the full adversarial-input corpus from
    Phase 0 run through the new method with never-raises/never-empty
    assertions); `TestSearchMethodsUseVariants` (5 tests, real
    `SubAgentRunner` instances with mocked `searxng`/`jina` tool clients:
    both search methods fire exactly 2 awaited calls when an entity
    parenthetical is present and exactly 1 when it isn't, the Jina variant
    call is confirmed to carry the entity text, cross-variant URL
    deduplication is confirmed via a shared URL appearing in both mocked
    variant responses but only once in the final `urls` list, and a tool
    exception on the primary call is confirmed to be caught and logged
    rather than propagating — `_search_searxng` still returns
    `("searxng", [], None)` cleanly).
  - Full suite: **564 passed, 3 skipped** (was 563 passed, 3 skipped
    pre-1.4-polish-test, 539 passed/3 skipped after 1.6; +25 net new tests
    across this fix, zero regressions).
- [ ] **1.5** Low-yield reformulation (<3 results → broaden → retry once)
- [x] **1.6** `resolve_subject` into `market_analyst`, `regulatory_analyst`, `risk_analyst`
  - Confirmed via `grep -rln "resolve_subject" hyperion/agents/specialists/*.py`
    that exactly 9 of 12 specialists already imported it and the 3 missing
    were precisely the 3 the audit named — `market_analyst.py`,
    `regulatory_analyst.py`, `risk_analyst.py` (§4.10 Finding B-9).
  - **`market_analyst.py`**: `run()` used to hand `self._question` (the raw
    user question) straight into `_spawn_data_collection_sub_agents`,
    `_search_market_reports`, and `_scrape_dashboards` with no subject
    resolution at all — those methods build queries like
    `f"{market_query} market size TAM report"`, so the *question itself*
    was silently doing double duty as the subject with no explicit
    "market"/"segment"/"sector"/"industry" context ever consulted. Added
    `market_query = resolve_subject(self._context, "market", "segment",
    "sector", "industry", question=self._question) or self._question` right
    after the opening `_transition`, and threaded `market_query` (not
    `self._question`) into all three call sites. Behaviourally this is a
    no-op when `self._context` carries no market-ish key (the `or
    self._question` fallback preserves the pre-fix behaviour exactly), but
    it means an explicit `context["market"]`/`context["segment"]` from the
    Director's handover is now honoured instead of being silently ignored.
  - **`regulatory_analyst.py`**: `run()` did
    `industry = self._context.get("industry") or self._context.get("sector") or ""`
    — a two-key OR chain with **no** further fallback, so a handover
    naming neither key produced `industry = ""`, which
    `_search_regulations`'s `f"{subject} regulations {jurisdiction}
    compliance requirements"` template degraded straight through (it had
    its own local `get_engagement_focus()`-based patch for this, but `run()`
    itself, and the sibling `_scrape_government_portals`'s unmapped-
    jurisdiction discovery search, did not). Replaced the `run()` extraction
    with `resolve_subject(self._context, "industry", "sector",
    question=self._question)`, and replaced both hand-rolled
    `get_engagement_focus()`-based subject blocks inside
    `_search_regulations` and `_scrape_government_portals` with the same
    canonical `resolve_subject` call — removing the duplicated,
    less-capable version (no label-sanity check, no question-mining
    fallback) in favour of the shared helper everywhere in the file.
  - **`risk_analyst.py`**: `run()` did the most literal version of the
    finding — `industry = self._context.get("industry", "")` with zero
    fallback of any kind. `_search_known_risks(industry, space)` then built
    `f"{industry} industry risks challenges"`, `f"{space} startup failures
    lessons"`, etc. — five query templates degrading to
    `" industry risks challenges"` and worse. Replaced the `run()`
    extraction with `resolve_subject(self._context, "industry", "sector",
    question=self._question)`, and replaced the hand-rolled
    `get_engagement_focus()` subject block inside
    `_discover_regulatory_portals` with the same call.
  - Verified live: `resolve_subject({}, "market", "segment", "sector",
    "industry", question="Should India reduce its dependence on
    semiconductor imports")` returns the full question text (never `""`
    while the question has content), and an explicit
    `{"market": "Indian semiconductor manufacturing"}` context returns
    that label verbatim, confirming the four-tier order (explicit key >
    question fallback) works as intended for all three new call sites.
  - New `tests/test_specialist_resolve_subject.py` (10 tests): for each of
    the 3 files, (a) asserts the module now imports `resolve_subject`, and
    (b) exercises the real search-query-construction methods
    (`_search_market_reports`, `_scrape_dashboards` /
    `_search_regulations`, `_scrape_government_portals` /
    `_search_known_risks`, `_discover_regulatory_portals`) with an
    otherwise-empty `self._context`, asserting the outbound query is
    anchored to the engagement subject/question rather than degrading to
    the bare template fragment (`" regulations ... compliance
    requirements"`, `"industry risks challenges"`, `"market size TAM
    report"`) that the pre-fix code would have produced. All 10 pass.
  - Full suite: **539 passed, 3 skipped** (was 529 passed, 3 skipped after
    1.7; +10 new tests, zero regressions).
- [x] **1.7** Fact Checker: drop internal agent name, ground the claim query
  - `fact_checker.py:605` (`_search_for_verification`) previously built its
    verification query as `claim.claim[:100]` then appended
    `f"{query} {claim.agent.replace('_', ' ')}"` — a blind character slice
    (could cut mid-word/mid-clause) with the internal agent role name
    (`"market analyst"`, `"risk analyst"`) glued on, and it never called
    `ground_query` at all (audit §4.9 Finding B-8).
  - **Fixed**: `query = ground_query(claim.claim)` — grounds the claim's
    own text directly, no agent-name suffix, no pre-truncation (`ground_query`
    normalizes then truncates to 256 chars on its own, which lands on word
    boundaries rather than cutting a token in half). If the claim grounds
    to `""` (no subject at all, e.g. a bare `"18%"` with no engagement
    focus), the web-search step is skipped rather than firing an empty or
    junk query — falls through cleanly to whatever local-corpus sources
    were already found for that claim.
  - New `tests/test_fact_checker_query.py` (4 tests): asserts the outbound
    query never contains `"analyst"`/the raw agent-name token; asserts a
    long claim's query contains only well-formed word tokens (no mid-word
    slice debris); asserts a thin claim (`"18%"`) still searches anchored
    to the engagement subject/geography via grounding's rebuild path
    rather than firing the bare unanchored original; asserts a genuinely
    contentless claim skips the web-search call without raising.
  - Full suite: **529 passed, 3 skipped** (was 525 passed, 3 skipped after
    1.1/1.2; +4 new tests, zero regressions).

### Phase 2 — Extraction & evidence
- [ ] **2.1** Collapse 3 extraction ladders into `UnifiedExtract`; wire consumers
- [ ] **2.2** Chunk → rerank → top-k assembly replacing blind 15k head-slice
- [ ] **2.3** `pdfplumber`/`camelot` table extraction → `chart_specs`
- [ ] **2.4** Token-boundary relevance in `evidence_scorer`; recalibrate `MIN_RELEVANCE`
- [ ] **2.5** Cap `confidence` when `overall_stance == "insufficient"`
- [ ] **2.6** Per-engagement extraction-yield metrics

### Phase 3 — Typography & visual architecture
- [ ] **3.1** Vendor `.ttf` files into `assets/fonts/` — **requires a `.gitignore` change first** (see §3.8)
- [ ] **3.2** Base64 `@font-face` injection into shipped `CSS_TEMPLATE` + embed assertion
- [ ] **3.3** Collapse the dead-template fork — one template system
- [ ] **3.4** Two-column body targeting 56 chars/line; exhibits `column-span: all`
- [ ] **3.5** Fix `presentation_designer.py:1947` fallback filter (`Markup`)
- [ ] **3.6** Section image target ≥2000 px wide (keep no-upscale rule)
- [ ] **3.7** Wire exhibits end-to-end → `has_exhibits: true`, ≥1 per section

### Phase 4 — Depth control & MBB exhibit vocabulary
- [ ] **4.1** Explicit page budget → word budget → per-section allocation
- [ ] **4.2** Narrow page-count contract to `15..22` and make it a quality-gate failure
- [ ] **4.3** Add tornado, marimekko, football-field, growth-share, bubble charts
- [ ] **4.4** Enforce MGI exhibit anatomy in template
- [ ] **4.5** Add At-a-glance, Technical appendix, Endnotes

### Phase 5 — Hardening
- [ ] **5.1** `ruff` + `mypy --strict` pre-commit (the process fix for the P0)
- [ ] **5.2** Golden-PDF regression test on probe metrics
- [ ] **5.3** Coverage floor; ban bare `except Exception: pass` (ruff `BLE001`/`S110`)
- [ ] **5.4** Reranker + embeddings + `sqlite-vec` Second Brain
- [ ] **5.5** OECD/Eurostat/IMF SDMX + `yfinance` to break the FRED US-only ceiling
- [ ] **5.6** PDF/A-2b post-pass via `pikepdf` + bookmarks

---

## 11. Definition of Done

The audit closes when a single live engagement demonstrably satisfies **all** of:

**Research (Part B)**
1. ≥8 distinct grounded queries issued per sub-question
2. `grep -c ground_query` > 0 at every search entry point
3. Zero `research_gap`-only sub-agents on a well-specified question
4. ≥60% of discovered URLs successfully extracted
5. Every cited source carries ≥500 chars of reranked, retained content
6. Zero internal agent vocabulary (`market analyst`, `risk analyst`) in any outbound query

**Output (Part A)**
7. Probe reports brand fonts embedded — and **no** DejaVu/Liberation
8. 52–60 chars/line, two-column body
9. `has_exhibits: true`, ≥1 exhibit per section, each with `Note:` + `Source:`
10. Page count within `15..22`, budget-driven not emergent
11. Zero template leaks (`{'`, `=None`, `{{page}}`, `Unknown`), zero blank pages
12. Section imagery ≥2000 px wide

**Process (Part C)**
13. `ruff` + `mypy --strict` clean; no bare `except Exception: pass` in search/extract paths
14. Golden-PDF regression test green
15. `_condense_query` (or its successor) has direct test coverage

---

## 12. Sequencing Rationale

Phase order is deliberate and dependency-driven, not priority-sorted:

- **Phase 0 before all else.** Every downstream measurement is meaningless while
  retrieval is at zero. Tuning rerankers or fonts against an empty evidence base
  optimises noise.
- **Phase 1 before Phase 2.** No point improving extraction quality on URLs
  discovered by ungrounded, unreasoned queries. Fix what we ask before fixing what
  we read.
- **Phase 2 before Phase 3.** Exhibits are mined from extracted numbers
  (`chart_specs.mine_chart_specs` returns `[]` rather than inventing data — correctly).
  Real extraction must land before the exhibit pipeline has anything to render.
- **Phase 3 before Phase 4.** Establish the typographic grid before budgeting pages
  against it; page count is a function of column width and exhibit density.
- **Phase 5 runs continuously**, but `ruff`/`mypy` should land alongside Phase 0 —
  it is the control that would have caught the P0 regex before it shipped, and it is
  the only item that prevents the *next* one.

**One-line summary of the whole plan:** HYPERION's architecture is MBB-grade and needs
no redesign; the failures are a single crashing regex, four ungrounded doors, one
unwired extraction ladder, an empty font directory, and a version-mismatched chart
exporter. All are wiring, not invention.
