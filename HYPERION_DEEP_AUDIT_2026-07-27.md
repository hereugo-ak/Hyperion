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

- [x] **0.7 (found while re-verifying 0.5/0.6)** — `pyproject.toml` pinned
  `pdfplumber>=1.0.0`, a release that **does not exist** (PyPI tops out at
  `0.11.10`). The pin was added by fix 2.3. Its blast radius was much larger
  than one optional feature: `pip install -e ".[dev]"` aborted the *entire*
  install transaction with `No matching distribution found`, so on a clean
  checkout `pydantic_settings` was never installed and **15 test modules failed
  at collection** with `ModuleNotFoundError` — i.e. the suite could not run at
  all, which is precisely the "green tests coexisting with a broken system"
  failure mode this audit exists to eliminate (here inverted: a broken install
  masquerading as broken tests). Corrected to `>=0.11.0`; install succeeds and
  the suite collects and passes. A CI step that runs the documented install
  command from a clean environment would have caught this at commit time —
  folded into 5.1.

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
- [x] **1.3** LLM query planner — 5–10 diversified queries per sub-question
  - **Before**: the audit's own grep — `grep -E "_llm_complete|generate.*quer|
    query.*llm" hyperion/agents/sub_agent.py` → **no matches** (§4.4 Finding
    B-3). There was no reasoning step anywhere in the sub-agent path. Query
    construction was a pure regex + stopword pipeline producing **exactly one
    query per tool**, where "a human MBB associate given *should we enter now
    or wait?* runs 8–15 differently-angled searches."
  - **New module `hyperion/tools/query_planner.py`** — deliberately a
    separate module, not inline in `sub_agent.py`, so it is unit-testable
    without spinning up a sub-agent and reusable by any future caller
    (specialists, `deep_search`, the research librarian). Implements the four
    properties §7 item 1.3 specifies, each pinned by tests:
    1. **5–10 schema-validated queries.** `PlannedQuery` (Pydantic) validates
       every query: the `angle` must be in the six-value `ANGLES` vocabulary
       named verbatim in the audit (`entity`, `metric`, `counter_thesis`,
       `regulatory`, `competitor`, `time_series`), with an alias table so a
       model writing `"counter-thesis"`/`"timeseries"`/`"REGULATORY"`/
       `"competitors"` doesn't lose an otherwise-perfect query to a
       formatting nit; the `query` must carry ≥2 alphabetic tokens, which
       rejects the exact contentless pattern (`"2024 2025 $100 50%"`) that
       `query_utils.is_contentless` exists to catch — so the planner can
       never *originate* one; length is capped at 120 chars (same rationale
       as `_condense_query`). An individually-invalid query is dropped with a
       DEBUG log rather than sinking the whole plan — a model returning 9
       good queries and 1 malformed one yields 9, not 0. Near-duplicates are
       collapsed on a sorted-token key, so `"Nigeria battery manufacturers"`
       and `"manufacturers battery Nigeria"` count once. The set is clamped
       to `[5, 10]`.
    2. **Diversified across the six audit angles.** The system prompt names
       and defines all six, requires ≥4 distinct angles, requires
       keyword-style (not sentence/question) queries, and explicitly forbids
       inventing entity names ("if you do not know the incumbents, write a
       query that would FIND them, do not name a guess") — the same
       no-fabrication discipline `chart_specs.mine_chart_specs` already
       follows. `_top_up()` then fills **missing angles before filling raw
       count**, so an under-delivering model (2 queries returned) is topped
       back up to 8 *and* to full angle coverage rather than silently halving
       research breadth; the LLM's own queries stay ranked first.
    3. **FAST tier.** `PLANNER_TIER = ModelTier.FAST` is a **module
       constant, not a caller argument** — no call site can accidentally
       escalate query planning onto STRONG/DEEP quota (§4.7). Dispatched at
       `TaskUrgency.LOW` with `response_format={"type": "json_object"}`.
       Tested: the sub-agent itself runs MICRO and the planner call still
       goes out as FAST, i.e. the tier is pinned, not inherited.
    4. **Cached by sub-question hash.** `sub_question_hash()` normalizes
       case/whitespace/trailing punctuation before hashing, so
       `"Market size in Nigeria?"` and `"  market   SIZE in nigeria  "`
       collapse to one entry — the common case when several specialists
       independently spawn the same sub-question in one engagement. Subject
       and geography participate in the key, so `"what is the regulatory
       outlook?"` under a lithium engagement cannot reuse a plan built for an
       offshore-wind one. Thread-safe LRU (`_PlanCache`, 512 entries) with
       hit/miss counters exposed via `plan_cache_stats()` for the Phase 2.6
       metrics surface.
  - **Never-raises / never-empty contract.** This is the direct lesson of the
    audit's P0 (a silent query-layer failure zeroed out all research).
    `plan_queries()` catches every failure mode — router exception,
    `success=False`, non-JSON response, empty `queries` list, all-queries-
    invalid, no router available at all — logs each at **WARNING with
    `exc_info`** (fix 0.3 discipline), and degrades to `deterministic_plan()`,
    which builds 8 queries across all 6 angles from angle-keyword suffixes
    with **no network and no LLM**. Every returned plan carries
    `degraded: bool`, so a planner outage is *visible* in logs and metrics
    instead of looking identical to success — the precise distinction whose
    absence let the P0 hide. Degraded plans are still cached, so an outage
    causes one failed call per sub-question rather than a retry storm.
  - **Wired into `sub_agent.py`** via a new `_plan_queries(leg=...)` method
    called by **both** `_search_searxng` and `_search_jina` (replacing the
    direct `_condense_query_variants` call at each). Design points:
    - **Strictly additive.** The fix-1.4 `_condense_query_variants` baseline
      is prepended unconditionally and returned to *both* legs, so the proven
      regex path (including the parenthetical-entity recovery) survives
      untouched no matter what the planner returns. The planner *adds*
      angles; it does not replace anything.
    - **Partitioned across legs, not duplicated.** Sending all 10 queries to
      both SearxNG and Jina would be up to 20 near-duplicate requests per
      sub-question — blowing the search budget for almost no marginal recall,
      since both engines index largely the same open web. Instead SearxNG
      takes the even-indexed planner queries and Jina the odd-indexed ones
      (`PLANNED_QUERIES_PER_LEG = 5`). Because `_top_up` orders the plan
      angle-first, each leg gets a diversified subset while **the union
      across legs is the whole plan** — the audit's ">=8 distinct grounded
      queries per sub-question" exit criterion is met at roughly half the
      request cost.
    - **One planner call per sub-question**, not one per leg — the second leg
      hits the hash cache. Verified live and asserted in tests.
  - **Two real bugs found and fixed during live verification** (neither was
    hypothesised from reading code — both surfaced from running it):
    1. **`"market"` was being deleted from market-size queries.** The
       agent-vocabulary sanitizer (guarding audit §4.9 Finding B-8: internal
       agent names must never reach an outbound query) initially split
       `parent_agent="market_analyst"` into tokens `{"market", "analyst"}`
       and stripped each with token-boundary matching. Live run showed
       `"Nigeria battery market size 2025 CAGR"` → `"Nigeria battery size
       2025 CAGR"` — the sanitizer was destroying the very query it existed
       to protect. **Fixed**: strip the full agent *phrase* (`"market
       analyst"`) plus the unambiguous role nouns (`analyst`, `hyperion`,
       `sub-agent`), never the phrase's individual tokens. Pinned by
       `test_subject_word_market_is_not_stripped_as_agent_vocabulary`.
    2. **Angle keywords were being truncated off the end of long queries.**
       The first live run produced `"...lithium ion battery market wait?
       regulation compliance requirements"` at 110 chars — and for a longer
       sub-question the 120-char cap cut the angle suffix away entirely,
       leaving a "regulatory" query with no regulatory keyword in it (i.e. not
       a regulatory query at all, while still being counted as one). **Fixed**:
       the anchor is pre-trimmed to reserve room for the longest angle
       suffix before the suffix is appended, and interrogative punctuation is
       stripped from the anchor (a `?` sitting mid-string once a suffix is
       appended — `"... market wait? regulation compliance"` — is a broken
       keyword query). Pinned by `test_angle_keywords_survive_truncation`
       and `test_no_interrogative_punctuation_mid_query`.
  - **Verified live (no mocks for the deterministic path, fake router for the
    LLM path)**: `deterministic_plan(...)` → 8 queries covering all 6 angles,
    `degraded=True`; a good LLM plan → 8 queries / 6 angles / `degraded=False`
    with `tier=ModelTier.FAST` and `urgency=TaskUrgency.LOW` confirmed on the
    captured router call; a plan containing a contentless query, an
    out-of-vocabulary angle, and a duplicate → all three correctly rejected
    while the 6 valid queries survived; a second call with different
    case/whitespace → **0 additional router calls** (`stats={'entries': 1,
    'hits': 1, 'misses': 1}`); a raising router → 8 queries, `degraded=True`,
    WARNING logged with traceback. End-to-end through the real
    `SubAgentRunner`: SearxNG leg dispatched 5 queries and Jina 5, **9
    distinct queries across the union** (audit criterion ≥8) from **1 planner
    LLM call**.
  - **New `tests/test_query_planner.py` (105 tests)**:
    `TestPlannedQuerySchema` (17: every documented angle accepted, 11-case
    parametrized alias normalization, unknown angle rejected, contentless
    query rejected, empty rejected, over-length rejected);
    `TestSubQuestionHash` (6: determinism, case/whitespace/punctuation
    normalization, distinct questions differ, subject and geography each
    participate in the key); `TestDeterministicPlan` (11+9 parametrized:
    count bounds, target count, all-6-angle coverage, `degraded=True`,
    baseline-query preservation, length cap, **angle-keyword survival**,
    **no mid-query `?`**, geography anchoring, distinctness, and the full
    Phase-0 adversarial corpus run through it with never-raises assertions);
    `TestPlanQueriesLLMPath` (12: count bounds, target, angle coverage,
    `degraded=False`, **FAST tier**, **never escalates**, LOW urgency, JSON
    response format, all six angles present in the prompt, subject/geography/
    context reach the prompt); `TestPlanQueriesValidationHardening` (11:
    invalid-angle drop without sinking the plan, contentless drop, exact- and
    near-duplicate collapse, over-long truncation-not-drop, bare-string list
    accepted, top-level list accepted, code-fenced JSON accepted, internal
    agent vocabulary stripped, the `"market"`-preservation regression guard,
    trailing `?` stripped); `TestPlanQueriesDegradation` (10: router
    exception, `success=False`, non-JSON, empty list, all-invalid, WARNING is
    actually logged, no-router, empty question, under-delivery topped up to
    target *with* angle coverage *and* LLM queries ranked first,
    over-delivery clamped); `TestPlanQueriesCache` (9: second call skips the
    LLM, `cached` flag, identical queries returned, normalized variants share
    one entry, different subject is a miss, `use_cache=False` bypass,
    `clear_plan_cache`, stats, degraded plans cached so no retry storm);
    `TestSubAgentUsesThePlanner` (12+5 parametrized: the audit's own grep now
    comes back positive, multiple queries returned, fix-1.4 baseline still
    present, **both legs dispatch planner queries**, **legs are partitioned
    not duplicated**, **union ≥8 distinct queries**, **one planner call
    shared via cache**, planner failure falls back to the fix-1.4 variants,
    the search leg still returns URLs under total planner failure, FAST tier
    pinned despite a MICRO sub-agent, and never-raises/never-empty over the
    adversarial corpus).
  - **Two pre-existing test classes updated, deliberately, with the reasoning
    recorded in code.** `TestSearchMethodsUseVariants` (fix 1.4) and
    `TestLowYieldReformulation` (fix 1.5) assert *exact* `await_count` values
    to pin variant-fanout and retry behaviour in isolation. With the planner
    engaged, those counts become a function of how many angles the planner
    happened to emit — silently converting "the fix-1.4 variant fired" into
    "some number of queries fired" and losing the property each test was
    written to protect. Rather than weaken the assertions, both classes now
    hold the planner constant via a new `_disable_planner()` helper (a
    26-line docstring explains why, and points at
    `TestSubAgentUsesThePlanner` as where the planner's own contribution *is*
    covered) — exactly as they already hold the tool clients constant. This
    mirrors the judgement call fix 1.5 made when it updated fix 1.4's mocks
    to return ≥`LOW_YIELD_THRESHOLD` results.
  - Full suite: **682 passed, 3 skipped** (was 577 passed, 3 skipped after
    1.5; **+105 net new tests, zero regressions**).
  - **Phase 1 is now complete** — 1.1 through 1.7 all landed.
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
- [x] **1.5** Low-yield reformulation (<3 results → broaden → retry once)
  - **Before**: `_search_searxng`/`_search_jina` in `sub_agent.py` ran their
    (post-1.4) query variant(s) exactly once and accepted whatever came
    back — 0, 1, or 2 results were treated identically to 20. A geography
    anchor too narrow for the live corpus (a small/emerging market, a
    niche multi-word regulatory topic) could starve every query built from
    it, and the sub-agent would proceed to analysis with almost no raw
    data and no attempt to recover.
  - **Fixed — grounding layer plumbing**: added `drop_geography: bool =
    False` (keyword-only) to `ground_query()` in `query_utils.py` — when
    `True`, the geography anchor is skipped entirely (neither the explicit
    argument nor the engagement-focus fallback is applied), while the
    subject anchor is untouched, so broadening drops only the
    jurisdiction, never the topic. Forwarded the same parameter through
    `grounded_search_or_empty()` and from there into both
    `SearxNGClient.search(..., drop_geography=...)` and
    `JinaClient.search(..., drop_geography=...)`, so the "broaden" half of
    the fix is available at the exact two network-boundary call sites
    `sub_agent.py` uses (and any future caller can opt in the same way).
  - **Fixed — retry logic**: added `SubAgentRunner.LOW_YIELD_THRESHOLD = 3`
    (matches the audit's own "<3 scored results" wording) and a shared
    `_fan_out_search()` helper that runs a list of query variants through a
    given search callable and merges+dedups by URL — refactored out of the
    duplicated loop bodies fix 1.4 had put in `_search_searxng`/
    `_search_jina`, so the fix-1.5 retry uses the identical merge logic as
    the fix-1.4 primary pass rather than a second, possibly-diverging copy.
    Both `_search_searxng` and `_search_jina` now: run the primary pass; if
    the merged result count is below `LOW_YIELD_THRESHOLD`, run the SAME
    query variant(s) again with `drop_geography=True`; merge the broadened
    results into the same dedup set (so a thin-but-nonzero primary result
    keeps its original, more specific hits ranked first — broadening
    *adds*, it does not replace); log at INFO when the retry actually grew
    the result count. Wrapped in the same outer try/except as the rest of
    the method, so a failure on the retry call degrades to the existing
    "fail loud via `logger.warning`, return empty" behaviour rather than
    raising past the caller.
  - Verified live: `ground_query("steel tariff exemptions", geography=
    "India")` → `"steel tariff exemptions India"`; the same call with
    `drop_geography=True` → `"steel tariff exemptions"` (subject-only,
    geography suppressed); confirmed the same holds when geography comes
    from `set_engagement_focus(...)` instead of the explicit argument
    (the more common real path, since specialists don't pass `geography=`
    explicitly).
  - New tests: 6 added to `tests/test_sub_agent_query.py`'s new
    `TestLowYieldReformulation` class (zero-result retry fires with
    `drop_geography=True` on the retry call only; a 2-result primary pass
    still triggers the retry and the results merge to 4; a 3-result
    primary pass — at the threshold — does NOT retry; retry results dedup
    against primary results by URL; the Jina leg retries identically; a
    retry whose broadened call also raises still degrades cleanly instead
    of propagating). The 4 pre-existing `TestSearchMethodsUseVariants`
    tests from fix 1.4 were updated to return ≥3 results from their mocks
    (via a new `_n_results()` helper) specifically so they exercise the
    fix-1.4 variant-fanout behaviour in isolation, without incidentally
    tripping the new fix-1.5 retry path — each such test now also asserts
    the exact `await_count` to make that isolation explicit and
    regression-checked. 5 more added to `tests/test_search_grounding.py`'s
    new `TestDropGeography` class (explicit-argument suppression,
    engagement-focus-fallback suppression, subject preserved while
    geography is dropped, `grounded_search_or_empty` forwards the flag,
    never raises on an already-contentless query) plus 2 more verifying
    `SearxNGClient.search`/`JinaClient.search` actually thread
    `drop_geography` through to the query that reaches their respective
    network boundaries (captured via a monkeypatched
    `_search_searxng_json`/`_cache_key`), not just accept and drop the
    kwarg. 13 net new tests across the two files.
  - Full suite: **577 passed, 3 skipped** (was 564 passed, 3 skipped after
    1.4's polish pass; +13 net new tests, zero regressions).
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
- [x] **2.1** Collapse 3 extraction ladders into `UnifiedExtract`; wire consumers
  — **DONE.** Proof of fix:
  - **Before state (the three ladders, and the proof the third was dead).**
    `grep -rn "UnifiedExtract" hyperion/ --include=*.py` returned only its own
    definition file and the `hyperion/tools/__init__.py` re-export (L27/108/109)
    — **zero call sites**, confirming §4.5's finding. Meanwhile two *live*
    ladders existed and had silently diverged:
    - `sub_agent._gather_raw_data` — 5 unrolled inline `if tool in tools:` blocks
      (jina → obscura → crawl4ai → scrapling → wayback), ~100 lines, no `http`
      tier at all, no `curl_cffi`, no per-tier error reporting.
    - `deep_search._extract_batch` — its own climb over
      jina/obscura/crawl4ai/http/scrapling/flaresolverr, where `scrapling` was
      *unreachable dead code* (never reached because the tier list ordering
      short-circuited before it).
    - `UnifiedExtract` — 586 lines of unrolled inline tier blocks covering
      curl_cffi/jina/obscura/nodriver/camoufox/wayback, with **no callers**.
  - **Design decision: UNION, not intersection.** The three ladders did not
    cover the same tiers. `UnifiedExtract` alone knew `curl_cffi`, `nodriver`,
    `camoufox`; `deep_search` alone knew `http` and `flaresolverr`; `scrapling`
    was live only in `sub_agent`. Collapsing to the *intersection* would have
    been a regression dressed as a cleanup — it would silently delete working
    retrieval capability. The merged ladder therefore takes the union, 10 tiers,
    ordered cheapest/least-detectable → most expensive:
    `curl_cffi → jina → http → obscura → nodriver → crawl4ai → scrapling →
    camoufox → flaresolverr → wayback`, with `NON_JS_TIERS = (curl_cffi, jina,
    http)` so `force_js_render=True` can skip the tiers that cannot execute JS.
  - **Table-driven, not unrolled.** The 3 × N inline blocks became one
    `TIER_ORDER` tuple plus one `_extract_<tier>` coroutine per tier, dispatched
    by `getattr(self, f"_extract_{tier}")`. The three divergent per-tier
    quality gates (each tier previously decided for itself what "good enough"
    meant) became a single `_finish()` gate applying `MIN_CONTENT_LENGTH = 100`
    uniformly, so a 40-character stub can no longer pass at one tier and fail at
    another.
  - **Two drivers.** `extract()` climbs the ladder for one URL.
    `extract_ladder()` climbs it for a batch and is **tier-major**: *every*
    pending URL is attempted at tier N before *any* URL is attempted at tier
    N+1. This is the property the old per-URL loops lacked — they would launch a
    headless browser for URL A while URL B had not yet been tried against free
    `curl_cffi`. It returns a `LadderOutcome(results, tools_used, tools_tried,
    errors, tiers_unavailable)`.
  - **The resolver seam — why the consumers keep their own `_extract_*`
    methods.** A naive collapse (have `_extract_batch` simply call
    `UnifiedExtract`'s tiers) would have quietly broken the *test* contract:
    `tests/test_tool_capability_gating.py` monkeypatches
    `client._extract_jina` / `._extract_obscura` / … as its substitution point,
    and L720's assertion requires the literal string `f"_extract_{tier}"` to
    remain in `deep_search`'s source. Under a naive collapse those 81 tests
    would have kept passing while silently no longer exercising doubles — they'd
    have been hitting real HTTP clients. So `extract_ladder` accepts a
    `tier_resolver` callback: **the climb lives in one place, the per-tier calls
    stay overridable per consumer.** All 81 pre-existing tests in
    `test_tool_capability_gating.py` + `test_stealth_extract.py` +
    `test_tools.py` pass **unmodified** (`81 passed`).
  - **`raw` field for lossless delegation.** `deep_search` carries a
    `published_date` that `UnifiedExtractResult` has no field for. Rather than
    widen the shared schema for one consumer, `UnifiedExtractResult.raw: Any`
    parks the consumer's native result object for it to read back. Deliberately
    excluded from `to_dict()` so it never leaks into serialised output.
  - **Semaphore reentrancy contract (documented in two docstrings).**
    `deep_search`'s `_extract_<tier>(semaphore, url)` methods acquire the
    concurrency semaphore *themselves*. `asyncio.Semaphore` is not reentrant, so
    if the shared driver also acquired it every URL would need two permits and
    the batch would deadlock. The contract is therefore: **the resolved callable
    owns its own bounding; the driver must not wrap it.** `_default_resolver`
    acquires (its `_extract_*` methods don't), `_resolve_extraction_tier`
    doesn't (deep_search's do), and `extract_ladder` gathers bare.
  - **`_normalize_tiers` — a restriction that cannot reorder.** Consumers pass
    `tiers=` to request a subset. Passing a *list* risks a caller silently
    reordering the cost ladder (e.g. putting `camoufox` first). Normalisation
    therefore treats the argument as a **set** and re-projects it through
    `TIER_ORDER`, so cost ordering is structurally unforgeable. Unknown tier
    names are warned-and-ignored; an all-unknown request falls back to the full
    ladder rather than extracting nothing.
  - **Consumer 1 — `deep_search._extract_batch`.** Its climb is gone; it now
    builds `UnifiedExtract` lazily (`_get_unified_extract()`) and calls
    `extract_ladder(urls, concurrency=EXTRACTION_CONCURRENCY,
    tiers=self.EXTRACTION_TIERS, tier_resolver=self._resolve_extraction_tier,
    tier_available=self._tier_available)`. Its own gating and its human-facing
    `TIER_LABELS` are preserved by mapping the outcome's tier names back through
    the label table for `tools_used` / `tools_tried` / `errors`, so no
    log-string or API surface changed. `close()` now also closes the ladder.
  - **Consumer 2 — `sub_agent._gather_raw_data`.** The 5 unrolled blocks became
    `await self._extract_urls(all_urls)`. This removed four defects at once:
    (a) `http` and `curl_cffi` were entirely absent from the sub-agent path;
    (b) failures were invisible — a tier that failed reported nothing;
    (c) there was no URL cap, so a broad leg could fan out unboundedly
    (`MAX_EXTRACT_URLS = 10`); (d) the climb was per-URL rather than tier-major.
    The §4.7 tool-quota discipline is preserved by a **three-way** split, each
    branch carrying its rationale in code: `curl_cffi`/`http` are always offered
    (plain HTTP, no `ToolName` member exists for them, and gating them behind an
    inexpressible grant is precisely how `http` came to be missing from this
    path); `jina`/`obscura`/`crawl4ai`/`scrapling`/`flaresolverr`/`wayback` are
    gated on the granted `ToolName`; `nodriver`/`camoufox` are **never**
    auto-granted, because they launch real browsers. The whole call is wrapped
    try/except/finally so a ladder failure can never lose the already-collected
    data-source blocks.
  - **Live verification** (not just unit tests) — three scripted runs against
    stubbed tiers proved: (1) the climb is genuinely tier-major (invocation log
    across a mixed batch shows all URLs at tier N before tier N+1); (2) a
    12-URL `deep_search` batch delegates through the seam and returns
    `published_date` intact via `raw`; (3) sub-agent tool-subset gating produces
    exactly the expected tier list for a spec granted only `JINA`.
  - **Tests: `tests/test_unified_extract_ladder.py`, 76 tests / 950 lines**, in
    8 classes: `TestLadderIsSingleAndTableDriven` (asserts the duplicate ladders
    are *gone*, not merely bypassed), `TestLadderCoversTheUnion` (10
    parametrized — one per tier, so a future "cleanup" that drops a tier fails
    loudly), `TestSingleUrlClimb`, `TestTierMajorBatchClimb`,
    `TestTierRestriction`, `TestCapabilityGating`, `TestDeepSearchDelegates`,
    `TestSubAgentDelegates`.
  - **Mutation testing — 3 mutations, 2 initially SURVIVED and forced the tests
    to be strengthened.** This is the part worth recording:
    1. *Reorder the ladder* (move `wayback` before `camoufox`) → 3 failures.
       Killed immediately.
    2. *Disable the `if not pending: break` stop-when-done* → **SURVIVED.** The
       invocation-log assertion alone still passed. Investigating the real
       consequence: `tools_tried` becomes all 10 tiers and `errors` gains 9
       spurious `"no usable content from 0 URL(s)"` entries — i.e. **dishonest
       provenance for a batch that fully succeeded at the first tier**, which
       would poison any yield metric built on it (cf. fix 2.6). Strengthened
       with exact `tools_tried == ["curl_cffi"]` and `errors == {}` assertions
       plus a standalone invariant test. Now killed.
    3. *Make the driver double-acquire the semaphore* → **SURVIVED at
       `concurrency=2`.** Probing 1/2/3 permits showed the deadlock is
       deterministic **only at `concurrency=1`**; with ≥2 permits a single task
       can hold both acquisitions and progress, so the batch merely serialises
       invisibly. The test was parametrized over `[1, 2, 5]`, a dedicated
       `test_resolved_callable_owns_its_own_bounding` was added, and the
       `deep_search` counterpart now forces `EXTRACTION_CONCURRENCY = 1` via
       monkeypatch. Now killed (2 failures, `TimeoutError`).
  - Net diff: **1,055 insertions / 446 deletions** across
    `unified_extract.py` (the single ladder), `deep_search.py` (consumer),
    `sub_agent.py` (consumer).
  - Full suite: **758 passed, 3 skipped** (was 682 passed, 3 skipped after
    Phase 1; +76 new tests, **zero regressions**, zero pre-existing tests
    modified).
- [x] **2.2** Chunk → rerank → top-k assembly replacing blind 15k head-slice
  - **The defect (§4.7 / B-6), reproduced live before fixing.** `MAX_CONTENT_CHARS
    = 15000` was applied as `content[:15000]` at **6 call sites**. On a
    10,936-char fixture whose evidence sits in the back half (as it does in every
    real report — tables and conclusions are never in the front matter), against
    a 4,000-char budget:
    - head-slice retained **0 of 4** evidence markers, and *did* retain
      `"Copyright"`;
    - chunk→rerank→top-k retained **4 of 4**.
    - Ranking diagnostic: the evidence chunk scores `bm25=19.611`, the foreword
      `bm25=0.000`. The budget is **unchanged** — only how it is *spent*.
  - **New module `hyperion/tools/content_selector.py`** (828 lines): `tokenize`,
    `chunk_content` (structure-aware: headings → blank lines → sentences),
    `rerank_chunks`, `select_relevant_content` → `SelectionResult`,
    `select_content` (string-in/string-out, the literal one-line head-slice
    swap), `Chunk`/`SelectionResult` dataclasses carrying provenance.
  - **BM25 hand-implemented** (Robertson/Sparck-Jones, `k1=1.5`, `b=0.75`, IDF
    within the document's own chunk set). Not a dependency: `rank-bm25` is
    present only *transitively* via crawl4ai and is not declared in
    `pyproject.toml`, so importing it would have been an undeclared-dependency
    landmine.
  - **Selection is by score; output is by document order** — a relevance-sorted
    jumble contains the identical characters but is measurably worse input for
    the LLM that consumes it.
  - **Wired into all 6 sites**, threading the grounded query end-to-end:
    `deep_search.py` (`_fit_content`, `_extract_batch(query)`, `search()`),
    `http_extract.py` (`extract`/`extract_batch`, both `content` and `markdown` —
    selected independently because markdown retains table markup),
    `unified_extract.py` (`_fit` hooked into `_finish`, **only when `ok`** so the
    quality gate still sees pre-selection text — a selection bug must not read as
    an extraction failure and send the ladder to a browser tier),
    `sub_agent.py` (`_extract_urls(query)`, SEC-filing path).
  - **Never-raises / never-empty contract.** On any internal failure the result
    is the old head-slice with `degraded=True` and a WARNING with `exc_info=True`
    (fix 0.3 discipline). A bug here can cost retrieval *quality*; it can never
    zero retrieval — the failure mode that produced the audit's P0.
  - **6 real implementation bugs caught by the new tests and fixed in the
    implementation, never by weakening a test:**
    1. Short titled sections (`## Conclusions`) were absorbed into the previous
       chunk, **deleting the heading label** — precisely the short, high-value
       closing section a consulting report most wants. Fixed with
       `_is_titled_section()`.
    2. Budget **under-filled**: 200 of 1,000 chars on a boundary-less document.
       An 80% unspent budget is an 80% smaller evidence base than the caller
       asked for — quietly *worse* than the head-slice being replaced. Fixed
       with a top-up pass (`MIN_TOPUP_CHARS = 150`; threshold, not always, or
       every selection ends mid-sentence for a fragment too short to carry a
       fact).
    3. A **table of contents** scored `bm25=0.000` but `boost=0.600` on pure
       numeral density, beating genuinely-scored chunks. `_evidence_boost` was
       documented as a tie-breaker but applied unconditionally; now gated on
       `base > 0`. Generalises to stock-ticker sidebars, date lists, pagination,
       footnote runs, cookie banners.
    4. `_is_titled_section("# H1\n\n## H2")` returned True (an H2 counts as "text
       under H1"), emitting a 12-char pure-label chunk. Fixed by excluding
       heading lines from the body check.
    5. **The greedy fill pass had no relevance gate** — only the top-up did. At a
       900-char budget the 1,618-char lead chunk does not fit, so assembly began
       empty, correctly took the three evidence chunks (673 chars), then spent
       the remaining 227 on a **121-char table of contents** (one of six
       identical copies; at score 0.0 all zeros tie and the lowest index wins).
       The front matter §4.7 complains about walked back in through the side
       door. Both passes now share one gate: a zero-scoring chunk is admitted
       only when the document has no scored chunk *anywhere*. Post-fix, budget
       900 keeps `[0, 15, 16, 17]` — all 4 evidence markers, no ToC, no
       acknowledgements, 900/900 chars spent.
    6. A **relevance-blind selection reported itself as clean.** When BM25
       matches no query term in any chunk, the reranker ran but had nothing to
       rank on, so the output is a head-slice in all but name; it returned
       `degraded=False`. Now flagged with a reason, because fix 2.6 reports
       extraction yield off these flags — an unflagged relevance-blind selection
       tells the operator "15,000 chars, reranked, clean" for a source that
       contributed nothing topical, which is the exact shape of the audit's P0:
       a healthy-looking metric over a silent quality failure. Deliberately
       does **not** flag a prefix-cut top-up on its own — a cut chunk is a
       *boundary* artefact, not a relevance failure, and firing on every normal
       budget-edge trim would make the flag worthless.
  - **Invariants proved, not assumed**, before being pinned as tests: lossless
    chunking across 6 document shapes (markdown, blank-line-only, no boundaries,
    one huge sentence, tabs/tables, CRLF); never-raises across 13 content × 4
    query combinations; budget respected at 300/1,000/5,000/15,000 including 0
    and negative; determinism across 5 runs; token-boundary matching
    (`tokenize("said chain maintain")` does **not** yield `"ai"` — the §4.8 defect
    fix 2.4 will address in `evidence_scorer`).
  - `tests/test_content_selector.py`: **+271 tests** (`TestTheAuditsActualComplaint`,
    `TestChunking`, `TestReranking`, `TestTokenization`, `TestAssemblyContract`,
    `TestNeverRaisesNeverSilentlyEmpty`).
  - Net diff: **1,684 insertions / 20 deletions** across 7 files.
  - Full suite: **1,029 passed, 8 skipped** (was 758 passed, 3 skipped after
    2.1; **+271 new tests, zero regressions, zero pre-existing tests
    modified**). `ruff` clean on both new files; the 28 pre-existing `ruff`
    findings in the touched consumers were verified pre-existing at HEAD and are
    left for fixes 5.1/5.3.
  - Note: `deep_search.to_markdown()` retains a `[:MAX_CONTENT_CHARS]` cap. That
    is a render-time cap on *already-selected* content, not a selection, and is
    deliberately left in place.
- [x] **2.3** `pdfplumber`/`camelot` table extraction → `chart_specs` (commit `c996c01`)
- [x] **2.4** Token-boundary relevance in `evidence_scorer`; recalibrate `MIN_RELEVANCE` (commit `1ae1f38`)
- [x] **2.5** Cap `confidence` when `overall_stance == "insufficient"` (commit `29a1ebe`)
- [x] **2.6** Per-engagement extraction-yield metrics (commit `d8ad3be`)

### Phase 3 — Typography & visual architecture
- [x] **3.1** Vendor `.ttf` files into `assets/fonts/` + amend the `.gitignore` blocker (commit `a3060fd`)
- [x] **3.2** Base64 `@font-face` injection into shipped `CSS_TEMPLATE` + embed assertion (commit `bd6443c`)
- [x] **3.3** Collapse the dead-template fork — one template system (commit `9490c2e`)
- [x] **3.4** Two-column body targeting 56 chars/line; exhibits `column-span: all` (commit `61deabb`)
- [x] **3.5** Fix the fallback Jinja env's filter to return `Markup` (commit `3bdee90`)
- [x] **3.6** Section image target ≥2000 px wide (keep no-upscale rule) (commit `a4afead`)
- [x] **3.7** Wire exhibits end-to-end → `has_exhibits: true`, ≥1 per section
  - The audit read `has_exhibits: false` as "charts are simply not being
    populated". Measurement showed something worse: charts *were* being
    generated at 300 DPI and then discarded, by **four independent breaks** in
    the hand-off chain. Each was fixed at the one hop that owns it.
  - **Break 1 — homeless specs (`chart_specs.py`).** `mine_chart_specs` paired
    every `report.key_findings` finding with `section_id=""`. The template
    iterates `section_charts[section.id]`, no section has the id `""`, and
    Jinja returns `Undefined` (not an error) for a missing key — so those
    charts were rendered by nobody, silently. Because `key_findings` holds the
    *headline* findings, the most important exhibits in the document were
    exactly the ones dropped. Fixed by homing each spec on the section whose
    `agent` matches the finding's `agent` (an honest anchor: a market-sizing
    finding lands in the market section), falling back to the first section.
  - **Break 2 — the note field did not exist (`schemas/models.py`).**
    `ChartPlacement.note` existed *and the template already rendered it*, but
    `ChartSpecification` had no `note` field, so any methodology note was
    dropped at the Data Visualizer hop. Every exhibit therefore shipped with a
    three-part anatomy against the benchmark's four. Added the field, and had
    `data_visualizer.py` copy it through.
  - **Break 3 — unrenderable charts were still placed
    (`presentation_designer.py`).** `_receive_chart_images` reproduced break 1
    downstream *and* placed charts whose `image_path` was empty (failed
    export). An empty path renders as a broken-image box under a real
    "Exhibit N" label, and — because the number comes from a CSS counter over
    placed exhibits — it also **consumed a number**, pushing every later
    exhibit out of sequence. Both are now dropped/re-homed with a
    `self._log(...)` line each: the audit's own lesson (§0.3) is that a silent
    failure caused the outage, so these fail loud.
  - **Break 4 — dead CSS label rules.** `.exhibit-note-label` and
    `.exhibit-source-label` were defined in the shipped CSS and referenced by
    **no markup** — so the italic `Note:` / `Source:` convention of both
    benchmarks was never actually applied. The template now emits each label
    as its own span and strips the prefix from the value, so the label appears
    exactly once whether or not the producer pre-prefixed the string (the
    deterministic miner does; an LLM-supplied spec may not).
  - **The probe was measuring its own fixture, not the pipeline.**
    `tools/audit_render_probe.py` passed `section_charts={"section_N": []}` —
    every section empty. The exhibit branch of the template was therefore
    never entered, so `has_exhibits: false` was a property of the fixture and
    the probe *could not have detected an exhibit regression in either
    direction*. It now generates real charts through the real `ChartGenerator`
    and places real `ChartPlacement`s. Two new metrics were added
    (`exhibit_count` from the rendered counter labels, `exhibit_note_count`,
    `exhibit_source_count`) because `has_exhibits` is a weak assertion — it is
    true if the word "Exhibit" appears anywhere, including in prose.
    Note the counter reaches the PDF text layer as `EXHIBIT 1`
    (`text-transform: uppercase`), so the extraction regex must be
    case-insensitive; a case-sensitive one reported 0 exhibits on a PDF
    carrying 7.
  - **Measured after the fix** (`reports/_audit/probe_metrics.json`):
    `has_exhibits: true` · `exhibit_count: 7` ·
    `exhibit_numbers: [1,2,3,4,5,6,7]` (contiguous — no dropped or
    double-counted figure) · `exhibit_note_count: 7` ·
    `exhibit_source_count: 7` → **every one of the 7 sections carries exactly
    one exhibit with the full four-part anatomy.** Previously 0/0/0. The
    Phase-3 gains hold simultaneously: 54 chars/line (target 52–60),
    2 column bands, brand fonts only (no DejaVu/Liberation), 0 blank pages,
    0 template leaks.
  - `tests/test_exhibit_pipeline.py` — 40 new tests pinning each break
    *independently*, so a regression in any single hop fails a named test
    rather than quietly reducing the exhibit count. Includes a check that the
    exhibit-number element is empty (the number must come from `counter()`,
    never from an agent, so it cannot be wrong) and a check that a dropped
    chart does not consume an exhibit number.
  - **Regression caught and corrected during this fix.** The first version also
    skipped specs with no home section inside `mine_chart_specs`. That broke 9
    pre-existing `TestChartMiner` tests, which legitimately exercise the miner
    on section-less reports. The lesson is a scope one: *mining* ("is this
    series chartable?") and *placement* ("which page renders it?") are separate
    concerns. Enforcing renderability in both places made the miner claim a
    report had no chartable data when it had plenty. The guard now lives only
    at the placement hop, and the reasoning is recorded in both the module
    comment and a test docstring so it is not "fixed" back.
  - Full suite: **1,191 passed, 8 skipped** (was 1,151 passed / 8 skipped;
    **+40 tests, zero regressions, zero pre-existing tests modified**).
    `ruff` clean on the new test file; the 4 findings in `chart_specs.py` and
    2 in `audit_render_probe.py` were verified byte-identical to `HEAD` before
    the change and are left for 5.1/5.3.

### Phase 4 — Depth control & MBB exhibit vocabulary
- [x] **4.1** Explicit page budget → word budget → per-section allocation
  — `hyperion/output/page_budget.py` (new) + `synthesis_lead.py`, commit pending
  - **The finding restated.** B-10 was not "the report is too long" — length is a
    symptom. The defect was that *nothing related page count to anything*:
    section count was `len(self._findings_by_agent)` (1–12, whatever reported),
    word count was a literal `2000` retyped in **four** prompt strings in one
    function, and the only check was `15 <= page_count <= 40` — a window wide
    enough that a 16-page and a 39-page report both "pass". Page count was an
    emergent accident.
  - **The fix inverts the dependency.** `plan_budget(section_count)` now takes
    the page contract as the *input* and emits words-per-section as the *output*.
    A 2-agent engagement is asked for 2,600-word sections; a 6-agent engagement
    for 575-word sections. Both land inside 15–20 pages.
  - **Two bugs the tests caught before commit** (both were in my first draft, and
    both are the *same class of error as the bug being fixed*, which is why they
    are recorded rather than quietly corrected):
    1. **Continuous division.** The first model divided available pages by
       section count as if page space were a fluid. It is not: the production CSS
       sets `page-break-before: always`, so a section needing 2.1 pages burns
       **3 sheets**. The continuous model therefore *under-projected* — it would
       have promised 15 pages and rendered 18. Fixed by ceiling-ing per section
       (`_section_pages`) and inverting that ceiling analytically
       (`_max_words_in_pages`), with a test asserting the two are exact inverses
       at the boundary so they cannot drift apart.
    2. **Aiming at the midpoint.** The first model targeted the middle of the
       15–20 band, so with 4 sections it chose 16 pages / 575 words when
       20 pages / **1,342 words** fit the same contract. Undershooting by 4 pages
       is not the safe option — it is a thinner deliverable bought for nothing.
       `target_pages` is now documented and tested as a *ceiling to fill*, not a
       bullseye, and `test_no_larger_allotment_would_have_fitted` asserts the
       chosen allocation is **maximal**, not merely legal. A naive
       `within_contract` assertion passed the buggy version — which is exactly
       why that test exists.
  - **Constants are measured, not guessed.** Re-derived with PyMuPDF from the
    probe PDF: 767 words/full two-column page (page 6 — the only pure-prose page
    in the fixture), 8 pages fixed front/back matter (pages 1–4, 33–36), 1.25
    pages/section of chrome (opener + exhibit). Calibration: the probe fixture
    emits **1,510** body words/section (I had initially recorded 1,615 — corrected
    by re-deriving it from `LOREM_PARA` rather than trusting the earlier note),
    and `_projected_pages(7, 1510) == 36` — *exactly* the page count the audit
    measured. `test_fixture_word_count_is_still_1510` re-computes that input from
    the probe source so the calibration cannot silently stop referring to the
    real artefact.
  - **Degrades honestly.** ≥7 sections cannot fit 20 pages even at the 450-word
    floor, so `sections_over_capacity` is set and the Synthesis Lead logs a
    warning. The alternative — silently emitting 200-word stubs, or shipping 32
    pages under a "20-page" contract — is what the old code did. Tests pin that
    over-capacity means *overrun*, never undershoot, so the operator is never
    sent looking in the wrong direction.
  - **Retry threshold now tracks the ask.** The old gate was a fixed
    `len(content) > 800`. At ~6 chars/word that is ~130 words, so a section that
    answered a 2,000-word request with 130 words was **accepted silently**. Now
    `max(800, words_per_section * 6 * 0.5)` — floored at 800 so the change can
    only ever be stricter than what it replaced.
  - **Wiring is tested, not assumed.** `TestSynthesisLeadUsesTheBudget` greps the
    agent source (comments stripped, so the explanatory notes naming the old
    values don't self-satisfy the check) for `2000-4000 words` /
    `at least 2000 words` / `fewer than 2000 words`. Without this, someone could
    delete the wiring and still see green on the module's own tests.
  - Full suite: **1,236 passed, 8 skipped** (was 1,191 / 8; **+45 tests, zero
    regressions, zero pre-existing tests modified**). `ruff` clean on both new
    files; `synthesis_lead.py` went 20 → 19 pre-existing findings (one removed,
    none added). Probe re-run unchanged: 7/7 exhibits, Note+Source on each, no
    leaks.
  - **Not yet closed:** the budget now *governs the request*, but nothing yet
    *verifies the result* — that is 4.2, which narrows `render.py`'s 15–40 window
    and promotes the violation from a log line to a quality-gate failure.
    **→ Closed by 4.2 below.**
- [x] **4.2** Narrow page-count contract to `15..22` and make it a quality-gate failure
  — `page_budget.page_count_verdict` (new) + `render.py`, `render_engine.py`,
  `harness.py`, `orchestrator.py`, `presentation_designer.py`, `models.py`
  - **The finding restated.** The audit reported "36 pages vs a 15–20 target" as a
    ⚠️ row, but the deeper defect was that **three separate checks all measured
    page count and none could fail on it**:
    1. `render.py:757` recorded `page_count_reasonable: 15 <= page_count <= 40`,
       then computed `passed` from blank pages and fonts **only** — the page count
       was written into the result dict and structurally discarded.
    2. `render_engine._verify_pdf` — the method whose own docstring calls the
       agent "the last line of defense for quality" which "never ships a broken
       PDF" — **did not check page count at all**. It was measured two steps
       later in `run()` to populate a status string.
    3. `harness.py` used `5 <= page_count <= 60`: a 55-page window on a 20-page
       contract, which no plausible render could fail.
    So the 36-page report did not fail a check; it passed three of them. And the
    windows were too wide to be informative anyway — `15..40` returns the same
    verdict for a 16-page and a 39-page document, so no achievable improvement
    could ever move it.
  - **The fix makes the verdict load-bearing.** `page_count_verdict()` is now the
    single definition of the band, and it participates in `passed` /
    `verification_issues` at all three sites. The band is *derived*
    (`PAGE_COUNT_MAX = TARGET_PAGES_MAX + RENDER_SLACK_PAGES`) rather than
    retyped, so it moves with the contract; `RENDER_SLACK_PAGES = 2` is global,
    not per-section, because a per-section allowance would admit +6 pages at 6
    sections — a third of the contract.
  - **A defect *inside 4.1* that this work exposed.** 4.1 sized each section to
    the *most* words fitting its sheet allotment, then advertised "acceptable
    range 0.9N–1.1N" on top. Those two statements contradicted each other: at 4
    sections the allocation was 1,342 words (exactly 3 sheets) while the clause
    invited 1,476, which needs 4. **A model that obeyed its instructions pushed
    the report from 20 pages to 24 — and this held at 4 of the 6 realistic
    section counts (3, 4, 5, 6), by up to 6 pages.** While nothing verified the
    output this was invisible; the moment page count became a gate it would have
    failed reports whose only fault was compliance, and the natural-looking "fix"
    would have been to widen the gate — entrenching the real bug. The allocation
    now reserves the tolerance it advertises (`SECTION_WORD_TOLERANCE`, one
    constant shared by the clause and the allocation, which previously used a
    hardcoded `1.1` and *no* allowance respectively). Costs ~9% of prose;
    `test_a_maximally_compliant_report_passes_the_gate` pins it for all 12
    section counts.
  - **Fair, not merely strict.** A flat band would fail two reports for
    conditions the budget already declared: a 1-section engagement clamped by
    `MAX_SECTION_WORDS` projects 13 pages and *cannot* reach 15 without padding,
    and an over-capacity engagement already logged a warning naming its
    projection. Passing the budget widens the band for exactly those two
    self-declared cases — and `test_over_capacity_still_fails_if_it_overruns_its_own_admission`
    pins that the widening is bounded, so it is not a blank cheque. A
    zero-section budget is explicitly refused as a widening basis, since
    `plan_budget(0)` projects 8 pages and would otherwise let anything through.
  - **Wired in production, not just in tests.** `orchestrator._page_budget_for`
    reconstructs the budget from the finished report's section count and passes it
    to `RenderEngine.run(page_budget=...)`. Reconstructed rather than carried as
    state because the quality-iteration loop can revise the report — and its
    section count — several times before delivery, so a budget captured at
    synthesis time would describe a report that no longer exists. `plan_budget`
    is pure, so recomputation is exact (`test_reconstructed_budget_equals_the_planned_one`).
  - **The contract is now stated once.** It previously appeared in five places
    with three different values (`15-40` in `render.py`, `5-60` in `harness.py`,
    `15-40` in the `LayoutPlan` schema and in the Presentation Designer's own
    prompt, `15-20` in the budget) — so the agent laying out the pages was told a
    looser rule than the agent verifying them. `TestTheContractIsStatedOnceInTheCodebase`
    greps for each stale window; it tokenises the source to strip comments *and*
    docstrings, because the explanatory notes legitimately name the old values —
    a naive `#`-only filter failed on this fix's own docstring, caught on first run.
  - **Two negative controls run before commit.** Reverting
    `page_count_reasonable` to the old expression, and deleting the `run()` budget
    assignment, each made the intended test fail — confirming the assertions bite
    rather than passing vacuously. This matters here specifically because the
    pre-fix code *computed the band correctly* and merely ignored the result:
    band arithmetic alone cannot distinguish fixed from broken, only wiring can.
  - Full suite: **1,334 passed, 8 skipped** (was 1,236 / 8; **+98 tests, zero
    regressions, zero pre-existing tests modified**). `ruff` clean on
    `page_budget.py` and the new test file; no new findings in any touched file,
    and `presentation_designer.py` went 34 → 27 (a stray `logger =` sitting above
    the import block was making 7 imports `E402`; moving it below removed all
    seven rather than adding an eighth). Probe re-run unchanged: 36 pages on the
    7-section fixture — which the gate now correctly *rejects*, 54 chars/line,
    2 columns, 7/7 exhibits with Note+Source, 0 leaks, 0 blank pages, brand
    fonts embedded.
  - **Note on the probe's 36 pages:** the fixture deliberately over-fills (7
    sections × 1,510 words, written before the budget existed) and is retained as
    the *calibration* artefact for the page model — `test_page_budget` asserts the
    model reproduces 36 exactly. It is not a contract violation by the pipeline,
    which would now ask those 7 sections for 450 words each.
- [x] **4.3** Add tornado, marimekko, football-field, growth-share, bubble charts
  - `5d21d07` geometry + reachability + tests, `9e698ba` renderer lifecycle.
  - **The hard part was not drawing five shapes.** The chart type list was
    written out in THREE independent places, and drift between them is silent
    *and directional*, which is why no existing test caught it:
    - `ChartType` (`schemas/models.py`) — now declared CANONICAL in its docstring.
    - `_get_chart_creator` (`output/charts.py`) — a type missing here renders a
      **BAR chart**, because the dispatch ends in `.get(type, self._create_bar)`.
      Right data, wrong geometry, no exception, no log line.
    - `data_visualizer.py` — `type_map`, `_select_chart_type`, and the
      `_build_plotly_traces` chain. Missing from the chain renders **EMPTY**;
      present but unreachable from `_select_chart_type` is **dead code that
      every direct unit test still passes**.
  - **Latent bug found by the parity test, not by reading code:**
    `ChartType.PIE` was enumerated and selectable but had no dispatch key, so
    every pie request had been silently rendering a bar chart. Fixed.
  - **Rule ordering in `_select_chart_type` is load-bearing, not stylistic.**
    The MBB rules had to go *before* the generic families: `"growth-share"`
    contains `"growth"`(→LINE), `"share mekko"` contains `"share"`
    (→composition), `"comparable"` contains `"compar"`(→BAR). Appending them —
    the obvious additive change — leaves 4 of 9 natural phrasings unreachable
    while all direct unit tests still pass.
  - **Encoding:** bubbles use `sizemode="area"` with explicit `sizeref`;
    Plotly's default scales *diameter*, which overstates quadratically (Tufte
    lie factor). Growth-share divides at share = **1.0x (BCG parity)**, not the
    data median. Tornado overrides "first series is Terracotta" and colors by
    sign (Alert Red down / Sage up) because the sign IS the information.
  - **A hang that was NOT what it looked like — the real find.** The new suite
    made the full run hang in `kaleido/scopes/base.py:308`, which reads as
    kaleido process exhaustion. Measurement falsified that: 60 consecutive
    exports show **zero** degradation (flat ~0.15s). What actually happens is
    one export spawns a Chromium tree of **7 procs / 277MB and holds it for the
    life of the interpreter**. On this 985MB box with swap already exhausted
    that is most of the free memory, so the *next* allocation thrashes and
    kaleido's pipe read is merely where the stall becomes visible. Decisive
    control: the exact combination that timed out at >45s finished in **12s**
    after reaping orphaned trees, nothing else changed.
    `ChartGenerator.close()` was literally `pass`, so `async with` and every
    `finally: await close()` freed nothing. Now `release_renderer()` +
    `close()` + a `finally` in `generate_batch` (released per batch, not per
    chart: respawn is ~1.5s vs ~0.15s warm).
  - **Verified by measurement, not assertion:** all **16** chart types export
    Tier 1 as real PNGs (191–501KB) and Tier 2 via matplotlib (42–99KB); 24
    hostile-input combinations all stay on Tier 1; selection 6/6 collision
    phrases, 12/12 generic regressions, 9/9 hints. All 5 exhibits were
    *visually* inspected — which is the only reason the football-field
    label-clipping bug was found, and it is now pinned by an explicit
    x-range-headroom test.
  - **Five negative controls, all bit, all restored:** missing dispatch key
    (6 failures), rule ordering (4), marimekko widths (5), area-vs-diameter
    (1), and `close()` reverted to `pass` (2, incl. the behavioural one).
  - Two of my own tests were wrong and were rewritten to pin what is true:
    one expected `[]` where reality is 2 valid empty traces (creators coerce
    rather than raise — a *better* outcome), and one hardcoded `expected`
    while leaving `widths` unused (ruff F841 caught a second unverified
    implementation).
  - `tests/test_mbb_chart_vocabulary.py` — **122 tests / 11 classes.** Lint:
    "All checks passed"; no new findings in touched modules vs `bcdf98e`
    (B905 18→12, E501 20→19 are improvements).
- [x] **4.3-followup** Full-suite verification + the OOM was NOT kaleido
  - `dad5b03` declares `pytest-timeout` (it was installed ad hoc and undeclared,
    so a clean `pip install -e ".[dev]"` produced an interpreter where
    `--timeout=120` is an unrecognised argument). Not hypothetical: this sandbox
    was rebuilt between sessions and came back without it. Negative control — a
    test that blocks forever: **without** the plugin it ran until an external
    `timeout 15` killed it (rc=124) and pytest printed *nothing*; **with**
    `--timeout=5` it reported `Failed: Timeout (>5.0s)`, rc=1, in 5.04s.
  - **The suite is green. 1456 passed, 8 skipped, 0 failed, 0 errors** across
    38 per-module shards (`tools/run_suite_sharded.sh`), wall 118s.
    Collection parity proves sharding hides nothing: full-suite collection and
    the sum of the 38 shard collections both report **1464**, and
    1456 + 8 = 1464 exactly.
  - **The handoff's expected total (~1573) was wrong** and was arithmetic, not
    measurement: it came from a stale `1451` that contradicted its own `1334`
    baseline. Measured: 1342 collected without the 4.3 module + 122 in it =
    **1464**, i.e. `1334 + 122 = 1456` passing. No tests are missing.
  - 🔴 **The single-process OOM is not the kaleido/Chromium tree.** The prior
    session inferred that from the 34%→93% improvement after the `close()` fix.
    Falsified by direct control: the full suite run with **both** chart modules
    excluded (`--ignore` mbb + chart_export_smoke) *still* dies `rc=137` at
    **91%**. Kaleido cannot be the cause of a crash that happens without it.
  - **Actual cause, by bisect on module prefixes** (`tools/probe_suite_memory.sh`,
    peak RSS via `resource.getrusage`): a prefix of the first **34** modules
    peaks at **316 MB and passes**; extending to 35–36 OOMs. Module 35 is
    `tests/test_two_column_layout.py`, which alone peaks at **454 MB** — more
    than the whole 34-module prefix. Its neighbour
    `test_unified_extract_ladder.py` is 93 MB, so this is one module, not
    ambient growth. WeasyPrint PDF rendering, not chart export, is the
    allocation that breaks the 985 MB ceiling.
  - Peak RSS is monotonic within one interpreter (221→327→344 MB over the first
    26 modules) and never returns, which is why sharding is the only thing that
    reclaims: process exit does the freeing. `release_renderer()` can return the
    Chromium tree *between batches* but cannot lower an interpreter's own
    high-water mark — so 4.3's fix was real but was never the whole story.
  - Two measurement instruments were themselves wrong before they were right:
    `/usr/bin/time -f %M` is **not installed** in this image and its absence
    returned `rc=127` with a `0 MB` reading — which reads as "no memory growth"
    rather than "did not run", the exact green-test-over-broken-system failure
    this audit is about. Replaced with in-process `getrusage`. A per-module RSS
    pytest plugin was abandoned after its detached launches were repeatedly
    reaped, giving stale traces from an earlier validation run; the prefix
    bisect is coarser but its numbers are trustworthy.
  - Still open: no single-process green summary exists, and on this host one is
    not achievable while `test_two_column_layout.py` needs 454 MB. The correct
    fix is to bound that module's peak (or shard in CI), which is 5.x work —
    not a claim that the suite is unverified. Sharded green is a real
    measurement; it is simply a different one.
- [x] **4.4** Enforce MGI exhibit anatomy in template — `b74b370`
  - The anatomy was already *emitted* by 3.7, which made this look done. It was
    not: **every part was independently optional** in the Jinja source —
    `{% if chart.caption %}` around the action title and
    `{% if chart.source_citation or chart.note %}` around the *entire* footer.
    Only `image_path` was ever guarded.
  - Measured before the fix, all five degenerate combinations rendered clean —
    no exception, no log line, and a PDF that still looked deliberate:
    `NO action title` → `title=0 figure=1 Note=1 Source=1 footer=1`;
    `NO note+source` → `title=1 figure=1 Note=0 Source=0 footer=0`. An exhibit
    with no takeaway title and no provenance shipped silently.
  - Fix: `_enforce_exhibit_anatomy(placements) -> list[str]` runs inside
    `_assemble_chart_placements` *before* it returns, repairing and reporting
    defects per exhibit; `_humanise_chart_id` supplies a readable fallback
    title; the `exhibit-title` div and `exhibit-footer` figcaption are now
    **unconditional**.
  - `tests/test_exhibit_anatomy.py` — **21 tests / 5 classes**, asserting on the
    repaired object and on rendered HTML, never on template text (the pre-4.4
    template *contained* `Note:` too, inside an `{% if %}` that could skip it).
    `21 passed`; combined exhibit/output/page suites `220 passed`; ruff "All
    checks passed" on the new file and `IDENTICAL` findings vs HEAD on the
    modified module.
  - 🔴 **One of my own negative controls did not bite, and that is the finding.**
    NC2 restored the old `{% if chart.source_citation or chart.note %}` guard
    and the behavioural footer test **still passed** — because enforcement
    defaults the source upstream, so `source or note` is now always true. The
    test was measuring a condition the fix had made unreachable. Replaced with a
    *structural* assertion on the template, which does fail when the guard
    returns. A negative control that passes is not a reassurance; it is a bug in
    the test.
- [x] **4.5** Add At-a-glance, Technical appendix, Endnotes — this session
  - Three sections every MGI/BCG report carries were absent. The third was the
    worst, and not merely for being missing: `quality_score`,
    `confidence_breakdown`, `contradictions`, `fact_check_report` and
    `limitations` **already existed on `FinalReport`**, were computed by the
    pipeline, and **none reached the PDF**. The system graded itself and threw
    the scorecard away. The data was there; only the page was missing.
  - Added: an **At a Glance** page *before* the TOC (question, recommendation,
    confidence, evidence base, analysis depth, why, top-5 findings capped,
    top-4 assumptions capped); **Endnotes** numbered continuously across the
    document and grouped by citing chapter, de-duplicated by URL *within* a
    chapter; a **Technical Appendix** publishing quality dimensions, residual
    gaps, confidence breakdown, contradictions, fact-check counts and
    limitations. TOC rows and page offsets updated; `Appendix` renamed
    `Appendix: Sources`.
  - Both render call sites are fed (`endnotes_html`, `technical_appendix_html`)
    — deliberately, because fix 3.5 was exactly the bug of registering in one
    Jinja env and not the other. `TestBothRenderPathsAreFed` asserts it by count.
  - Also fixed in `_build_appendix_sources_html`: the literal `"Unknown"`
    fallback title, which `tools/audit_render_probe.py` counts as a **template
    leak** (§11 requires zero), and titles/URLs being interpolated
    **unescaped** — an `&` or `<` in a real headline corrupted the table.
  - 🔴 **Three wrong field names shipped in my first draft, all hidden by
    `getattr(obj, "field", default)`.** Caught only by checking the builders
    against `model_fields` rather than trusting them:
    `QualityScore.dimensions` is a `list[QualityDimension]`, **not a dict** — the
    draft guarded with `isinstance(dimensions, dict)`, so the dimension table
    would have rendered on **no report ever**; `FactCheckReport` exposes
    `total_claims_checked`/`verified_count`, **not** `claims_checked`/
    `claims_verified` — both reads returned `None` and the whole fact-check block
    was skipped; `Contradiction` carries `finding_a`/`finding_b`, **not**
    `description`/`topic` — the draft's `or str(item)` fallback would have
    printed a **raw pydantic field dump** into a client-facing PDF. A defensive
    default converts a schema mismatch into a plausible-looking empty section.
    All `getattr` defaults removed from both builders and the construct is now
    **banned by test** inside them.
  - The probe fixture was rebuilt on **real pydantic models** instead of
    `SimpleNamespace` stubs, and immediately rejected three of my own fixture
    errors that a stub would have accepted: `dimension_id` is an enum (not a
    free string), `score` is an `int` constrained 1..5 (not a float), and the
    enum member is `INTERPRETATION_CONFLICT` (not `INTERPRETATION`). Production
    formatting corrected to `N/5` accordingly — `4.0` implies precision the
    schema does not carry.
  - 🔴 **My own probe metric was wrong twice, in both cases reading healthier
    than reality.** `glance_labels_present` scored `1/4` because the labels are
    uppercased by CSS `text-transform`, so the literal `"Recommendation"` never
    matched — and the single hit was the word "Confidence" on the *Technical
    Appendix* page, meaning the metric would have reported non-zero with the
    entire At-a-glance grid deleted. `endnote_entries` read `24` because the
    regex ran over the whole document and counted the At-a-glance findings list
    as endnotes. Both are now scoped **per page** (and skip the TOC, which lists
    every heading). A measurement that cannot distinguish the thing it names
    from an unrelated page is not a measurement.
  - Measured on the rendered PDF (`tools/audit_render_probe.py`, rc=0):
    `at_a_glance_page=2`, `endnotes_page=36`, `technical_appendix_page=37`,
    `glance_labels_present=4/4`, `glance_words=111`, `endnote_entries=21`,
    `technical_appendix_sections=5/5`, `glance_precedes_toc=true`,
    `page_count=42`, `blank_pages=0`, all four `leaks` **0** (incl. `unknown`),
    `exhibit_count=7` with `note=7`/`source=7`. The `21` is itself the
    de-duplication proof: 7 chapters × 4 raw sources = 28 → 21 after collapsing
    the shared URL.
  - `tests/test_front_back_matter.py` — **56 tests / 6 classes**. `56 passed`;
    adjacent suites `175 passed`; related suites `108 passed, 5 skipped`. Ruff:
    new files clean except one **pre-existing** `N802` in the probe (2 findings
    before my change, 2 after); `presentation_designer.py` held at **27
    findings, identical to HEAD** after I wrapped the three long lines I had
    introduced. Pre-existing findings remain deferred to 5.1/5.3.
  - Negative controls — all four bit: **NC1** restore `isinstance(dict)` on
    dimensions → 2 failures (heading and tables render, dimension rows vanish);
    **NC2** gate fact-check on `claims_checked` → 5 failures, including the
    `getattr` ban, catching the *mechanism* and not just the symptom; **NC3**
    reset endnote numbering per chapter → 2 failures; **NC4** reinstate the
    `description`/`topic` + `str(item)` fallback → 4 failures. Fix restored, no
    NC residue, `56 passed`.
  - 🔴 **NC4 exposed two more weak tests of mine, which is why it was worth
    running.** `test_no_raw_pydantic_repr_leaks_into_the_appendix` **passed**
    with the leak deliberately reinstated: pydantic v2's `str()` is not
    `repr()` (no `ClassName(` prefix) and `html_escape` rewrites `'` to
    `&#x27;`, so both of my literals were unmatchable. So was
    `test_contradictions_render_both_opposed_findings`, because a leaked dump
    *contains* the finding text — a substring assertion cannot tell a formatted
    table from a dumped object. Rewrote the first to look for pydantic
    field-dump syntax (`agent_a=`, `finding_a=`) and added
    `test_each_contradiction_occupies_its_own_structured_cells`, which asserts
    four `<td>` per row and no `colspan`. NC4 then produced **4** failures
    instead of 2.

### Phase 5 — Hardening
- [x] **5.1** `ruff` + `mypy --strict` pre-commit (the process fix for the P0)
      — 5.0: the CI regression gate itself could not run (`c181e04`); 5.1: 8 live
      defects hiding as "unused variable" lint (`f9d4118`); 5.1b: two models
      shared one name — Agent 9's horizon scan never ran (`45d3448`); 5.1c:
      `zip()` dropped every chart series (`7739d86`); 5.1d: three silent-failure
      defects behind "style" lint (`529a171`); 5.1e: specialists could report
      success while delivering nothing (`7327f27`); 5.1e-cont: 939→503 E501
      reflow + SIM105 triage (`6acbdb3`, `99eeda6`, `989372d`, `cd46f85`);
      5.1f: pre-commit hooks + `ci_gate --lint` + staged mypy allowlist +
      E501 quarantine ≤60, shrink-never-grow (`7290633`, merged `0120191`).
- [x] **5.2** Golden-PDF regression test on probe metrics — `tests/golden/
      pdf_metrics_golden.json` encodes DoD #7–#12 bounds; `test_golden_pdf.py`
      ships the comparator, instrument-honesty via synthetic fitz PDFs (healthy
      passes, degraded MUST fail ≥8 checks), and integrity guards (`881533a`).
- [x] **5.3** Coverage floor; ban bare `except Exception: pass` (ruff `BLE001`/`S110`)
      — 220 findings triaged: 51 silent handlers converted to recorded failures
      (S110/S112=0), 169 `noqa: BLE001 - <reason>` on intentional catches;
      BLE+S110/S112 in the lint select; `test_bare_except_ban.py` gates the ban
      with a live negative-control probe (`a72ded7`).
- [x] **5.4** Reranker + embeddings + `sqlite-vec` Second Brain —
      `vector_brain.py`: dual-backend embeddings (sentence-transformers
      all-MiniLM-L6-v2 + sqlite-vec vec0 ANN in production; deterministic
      blake2b bag-of-ngrams hashing + exact cosine blob scan as fallback);
      `second_brain.py`: max(keyword, semantic) fusion — semantic recall only
      lifts, never hides exact keyword hits; index-on-save degrades to
      keyword-only. 12 tests incl. 2 negative controls (`17b98b8`).
      (Reranker itself was delivered earlier in Phase 2 via content_selector.)
- [x] **5.5** OECD/Eurostat/IMF SDMX + `yfinance` to break the FRED US-only
      ceiling — `sdmx.py`: OECDClient (path-key, SDMX-CSV), EurostatClient
      (query-param, TSV with flag-letter stripping, compare_countries),
      IMFClient (dot-key, get_exchange_rate for non-US DCF FX), header-driven
      parser; `market_data.py`: yfinance wrapper, lazy import, thread-executor
      so the sync library never blocks the AgentBus, 15-min cache,
      compare_peers for global peer groups. 26 tests incl. US-only-ceiling
      negative controls + AST registry guards (`9ea5022`).
- [x] **5.6** PDF/A-2b post-pass via `pikepdf` + bookmarks —
      `pdf_postprocess.py`: stamps XMP pdfaid:part=2/conformance=B + Dublin
      Core metadata; outline from BookmarkSpec; lazy pikepdf import; atomic
      temp-file + os.replace (a failed pass never leaves a half-written
      deliverable); never raises. Wired into BOTH render engine success paths
      (WeasyPrint + Playwright) via `_apply_pdf_post_pass`, bookmarks extracted
      from h1/h2 headings located in the rendered PDF. 16 tests incl.
      atomicity + refuse negative controls + AST guards (`4dc9820`).

**Final verification (fix0.1 @ 4dc9820):** `ruff` clean; `mypy` clean (135
files); `ci_gate --lint` PASS. Sharded suite on the 985MB sandbox:
2171 passed / 49 failed / 1 error / 18 skipped — every failure classified as
a pre-existing sandbox dependency gap (kaleido/textual absent), zero caused
by Phase 5; all six Phase 5 test shards green. `audit_render_probe.py`
requires weasyprint (absent in sandbox) — the live golden-PDF run is the
user-side step.

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
