# HYPERION — Master Remediation & Report-Quality Plan
### A forensic root-cause analysis + a full rebuild blueprint for a proprietary, zero-cost, McKinsey/BCG-grade research→PDF system

> **Status:** Authoritative plan. All prior plan docs (`PHASE2_IMPLEMENTATION_PLAN.md`, `PIPELINE_UPGRADE_PLAN.md`) are deleted and superseded by this file.
> **Scope of this document:** This is a *planning and diagnosis* artifact only — it changes no runtime code. It tells the implementer exactly **what is broken, why, and how to fix it, in priority order**, with acceptance criteria for each fix.
> **North Star:** A single command turns one strategic question into a **20–40 page, 300-DPI, boardroom-grade PDF** with real data, real citations, real charts, and pixel-perfect Unsplash imagery — good enough to hand to an S&P 500 executive. Zero paid APIs. Five free LLM providers, shared intelligently with fallback.

---

## Table of Contents

1. [Executive Summary — what is actually wrong](#1-executive-summary)
2. [How I diagnosed this (evidence trail)](#2-evidence-trail)
3. [The 12 root-cause defects, ranked](#3-root-cause-defects)
4. [Defect deep-dives with fixes](#4-defect-deep-dives)
5. [The search & retrieval stack — correct target architecture](#5-search-stack)
6. [The LLM router — five providers, shared with fallback](#6-llm-router)
7. [Sub-agent depth engine — how to get real content](#7-subagent-depth)
8. [Synthesis Lead — why FinalReport dies and how to guarantee it](#8-synthesis)
9. [Report generation — the McKinsey/BCG quality bar](#9-report-quality)
10. [Image pipeline — pixel-perfect Unsplash, zero distortion](#10-image-pipeline)
11. [Observability — logs that actually tell you where it broke](#11-observability)
12. [Fallback & graceful degradation doctrine](#12-degradation)
13. [Phased execution roadmap with acceptance gates](#13-roadmap)
14. [Acceptance test matrix](#14-acceptance-tests)
15. [Appendix A — file-by-file change map](#15-appendix-file-map)
16. [Appendix B — what to add that isn't there yet](#16-appendix-additions)

---

<a name="1-executive-summary"></a>
## 1. Executive Summary — what is actually wrong

The system is **not** a wrapper, and it is **not** conceptually wrong. The architecture on paper (13-task DAG, specialists → fact-check → synthesis → quality gate → design → render) is genuinely good. The failure is that **the pipeline is a chain of silent-failure links**, and when the early links produce thin/empty data, every downstream link "succeeds" on garbage, and the final PDF is a confident-sounding hallucination.

From the logs and the attached report, the four fatal symptoms are:

| Symptom (from your logs / output) | What it looks like | Underlying cause |
|---|---|---|
| `X No competitors identified — cannot proceed`, `regulatory: 0 regulations across 0 jurisdictions` | Specialists return empty structured findings | Search returns URLs but **extraction returns almost nothing**, so the LLM has no data to structure |
| `financial_analyst: completed with 1 findings`, report Methodology says **"Total unique sources: 1"** | 17 "findings" collapse into a report built on **one** source (FRED) | Findings are being dropped/not-persisted between specialists and Synthesis; only financial's survived |
| `ERROR X timed out after 300s` / `X Synthesis Lead did not produce a FinalReport` | Synthesis dies, no report | Synthesis makes **many sequential DEEP LLM calls** (per-section, per-contradiction) that blow the 300s window; DEEP tier is also the most rate-limited |
| FlareSolverr log flooded with `$15B technology analyst`, `50% risk analyst`, `12 m risk analyst`, `70% risk analyst` | Hundreds of nonsense Google searches | Something is **feeding fragments of already-written analysis text back into the search engine as queries** (fact-checker or a "verify claim" loop turning `"50%"` into a search) |
| Output HTML shows `C:\Users\Abuza\...\image.jpg`, raw `&lt;p&gt;` escaped tags, `should india enter into ai ?>` | Broken PDF: local file paths, double-escaped HTML, junk title | Render layer uses **absolute Windows paths** (never resolves in PDF), and **double-escapes** LLM HTML output |

**The core insight:** you don't have one bug. You have **one missing discipline** repeated everywhere — *no link in the chain validates the quality of what it received before proceeding, and no link fails loudly.* Every `except Exception: continue` in the extraction chain, every `return []` on failure, is a place where the system quietly degrades to garbage instead of stopping and reporting.

The fix is therefore **not** "add more tools." It is:

1. **Make the search stack actually return clean text** (fix the extraction chain — it is currently the #1 failure).
2. **Make every stage assert a minimum-data contract** and emit a structured, visible reason when it can't meet it.
3. **Make Synthesis parallel + bounded** so it always finishes and always emits a FinalReport.
4. **Make the router genuinely share 5 providers with fallback**, so a single provider's rate limit doesn't starve a whole tier.
5. **Rebuild the render/image layer** for portable paths, no double-escaping, and pixel-perfect images.
6. **Instrument everything** so the TUI log shows *per-URL extraction outcomes, per-finding counts, per-LLM-call token/latency/provider*, not just "completed with 0 findings."

---

<a name="2-evidence-trail"></a>
## 2. How I diagnosed this (evidence trail)

This diagnosis is grounded in reading, not guessing. Sources examined:

- **`VIGIL-search-stack-architecture.md`** — the intended search design (SearXNG curated engines + Jina, Obscura extraction, pgvector rerank, evidence scoring). Explicitly says: *drop Google/DuckDuckGo — they are the CAPTCHA source.*
- **`should_india_enter_into_ai.html`** — the actual broken output. Key tells:
  - `<img src="C:\Users\Abuza\CascadeProjects\Hyperion\assets\images\1FxMET2U5dU.jpg">` → absolute local path, will never render in a shared/served PDF.
  - `&lt;p&gt;Based on synthesis...&lt;/p&gt;` inside a `<p>` → the LLM returned HTML, and the template **escaped it again** (double-escaping).
  - Methodology: **"Total unique sources: 1"**, "Agents Used: financial_analyst" only, and a limitations block admitting *"Market Analyst, Competitive Intelligence... were not completed or provided."* → the multi-agent synthesis collapsed to a single agent.
  - `should india enter into ai ?>` title → raw unsanitized user string with a stray `>`.
- **9 TUI screenshots** — the live run. Confirmed:
  - Boot: `12 ready`, warnings on `semantic_scholar(no key)`, providers `google · nvidia · cerebras · groq · mistral` online, 20 agents.
  - DAG: 13 tasks, but `X MARKET — failed`, `X OPS — failed`, `X timed out after 600s`, `No competitors identified`, `0 regulations across 0 jurisdictions`, `carbon=N/A`.
  - Specialists *did* run long (18:26 → 18:38, ~12 min) and *did* make many real LLM calls (nemotron, mistral, magistral) returning `✓ OK` with reasonable char counts (4000–19000 chars) — **so the LLMs and router basically work**.
  - `financial_analyst: completed with 1 findings (total collected: 17)` then Synthesis: `Querying Second Brain`, `Resolving 0 contradictions`, then **`ERROR X timed out after 300s`** twice, and finally **`X Synthesis Lead did not produce a FinalReport`** at 18:48 after a 19240-char DEEP call.
- **FlareSolverr docker log** — hundreds of requests. Two damning patterns:
  1. Legit specialist queries (`Find TAM data for: ...`, `regulations US compliance requirements`).
  2. **Garbage queries built from analysis fragments**: `$15B technology analyst`, `50% risk analyst`, `70% risk analyst`, `12 m risk analyst`, `$200M risk analyst`, `Export demand reduces total addressable market (TAM) by 30-50% risk analyst`, `FX volatility causes 10-20% erosion... risk analyst`. These are **claims being turned into search queries** — a fact-check/verify loop is tokenizing findings and searching each number. This burns the entire FlareSolverr budget, triggers `net::ERR_CONNECTION_CLOSED` and `session not created: cannot connect to chrome` (Chrome OOM/crash from overload), which then makes *all* searches fail.
- **SearXNG docker log** — `wikidata: HTTP error 403`, `ahmia/torch: can't register engine`, `missing config file: /etc/searxng/limiter.toml`. SearXNG is **up but degraded**, and critically **the code isn't even using it** — `searxng.py::search()` routes everything through FlareSolverr→Google/DDG (the exact opposite of the VIGIL design).
- **Source code** — confirmed the architectural contradiction and the silent-failure pattern (details in §3–4).

**Conclusion:** LLMs work. The DAG works. The failure is concentrated in **(a) search/extraction returning no usable text, (b) a fact-check loop DoSing FlareSolverr, (c) findings not surviving to synthesis, (d) synthesis timing out, (e) a render layer that mangles paths and HTML.**

---

<a name="3-root-cause-defects"></a>
## 3. The 12 root-cause defects, ranked by blast radius

| # | Defect | Blast radius | Fix effort | Priority |
|---|---|---|---|---|
| **D1** | Extraction chain returns near-empty text (Obscura/Scrapling/Crawl4AI not actually producing content in this env), so specialists have no data | 🔴 Total — every specialist starves | M | **P0** |
| **D2** | `searxng.py` contradicts VIGIL: routes ALL search via FlareSolverr→Google/DDG instead of SearXNG curated engines | 🔴 Total — CAPTCHAs, rate limits, the thing VIGIL says never to do | S | **P0** |
| **D3** | Fact-check / claim-verify loop turns analysis fragments (`"50%"`, `"$15B"`) into search queries → floods FlareSolverr → Chrome OOM → all search dies | 🔴 Total — kills search mid-run | S | **P0** |
| **D4** | Findings don't survive specialist→Synthesis; report built on 1 source though 17 "collected" | 🔴 Total — report is single-agent | M | **P0** |
| **D5** | Synthesis Lead makes many sequential DEEP LLM calls → exceeds 300s → "did not produce a FinalReport" | 🔴 Total — no report at all | M | **P0** |
| **D6** | Render uses absolute Windows paths for images/CSS → nothing renders in PDF | 🟠 High — ugly/broken PDF | S | **P1** |
| **D7** | Double HTML-escaping of LLM output → `&lt;p&gt;` literal tags in report | 🟠 High — unreadable body text | S | **P1** |
| **D8** | Specialists emit empty structured findings (0 competitors, 0 regs) instead of degrading with partial data + explicit gap | 🟠 High — false "failed" states | M | **P1** |
| **D9** | Router tier fallback insufficient: DEEP tier starves under rate limits, no cross-provider spillover | 🟠 High — synthesis/quality stall | M | **P1** |
| **D10** | Logs are opaque: "completed with 0 findings" with no per-URL / per-call detail → impossible to debug live | 🟡 Medium — slows every fix | S | **P1** |
| **D11** | Image selection not semantic/curated → generic or distorted images, wrong aspect ratios | 🟡 Medium — looks amateur | M | **P2** |
| **D12** | No minimum-quality gate before render → garbage reports still get produced and "delivered" | 🟡 Medium — no floor on output | S | **P2** |

**Rule of sequencing:** fix P0 in order D2→D3→D1→D4→D5 (search sanity first, because you cannot diagnose extraction while FlareSolverr is being DoS'd), then P1, then P2.

---

<a name="4-defect-deep-dives"></a>
## 4. Defect deep-dives with fixes

Each defect below has: **Evidence → Root cause → Fix → Files → Acceptance criterion.** The fix descriptions are prescriptive enough that an implementer can execute without re-deriving the analysis.

### D1 — Extraction chain returns near-empty text (P0)

**Evidence.** Specialists ran for minutes and made real LLM calls, yet produced `0 findings` for MARKET/COMPETE/OPS/REGULATORY, and the final report has 1 source. The LLM calls succeeded (char counts 4k–19k) — that char count is the *model's own output*, not extracted web text. The web text going *in* was empty.

**Root cause.** In `deep_search.py::_extract_batch` and `sub_agent.py::_gather_raw_data`, every extractor is wrapped in `except Exception: continue` / `logger.debug(...)`. In this Linux/WSL sandbox:
- **Obscura** ships as `obscura-x86_64-windows.zip` (43 MB Windows binary in the repo). On Linux it cannot launch → silent fail.
- **Scrapling / Crawl4AI** need a working Playwright Chromium; the FlareSolverr log shows Chrome is already crashing (`session not created`), and these share the same fragile browser layer.
- **Jina Reader** (`r.jina.ai/{url}`) is the only extractor with *no local browser dependency* — but it's tier 3, reached only after Obscura+Scrapling "fail" slowly, and it's rate-limited without a key.

Net: the fallback chain's first two tiers waste time failing, and the reliable tier (Jina) is hit last and thinly.

**Fix.**
1. **Reorder the extraction chain by reliability-in-this-environment, not by the VIGIL ideal:**
   `Jina Reader (keyless, no browser) → FlareSolverr GET (already running, solves Cloudflare) → Crawl4AI/Playwright (only if browser healthy) → Obscura (only if Linux binary present)`.
   Detect environment at startup: if `obscura` binary isn't a working Linux executable, **disable it entirely** and log `obscura: DISABLED (no linux binary)` once — don't attempt-and-fail per URL.
2. **Add a real extractor that needs no browser and no key:** a plain `httpx` GET + `trafilatura`/`readability-lxml` HTML→text. This is the true floor and must always be present. Add `hyperion/tools/http_extract.py`.
3. **Make FlareSolverr a first-class *extractor* (fetch the article URL), not just a search proxy.** It's already up and solving challenges; use it to GET content URLs and run the same trafilatura pass on the returned HTML.
4. **Assert content quality with a real threshold** (≥ 500 chars of prose, not boilerplate) and **count successes**. If a URL yields < 500 chars from all extractors, drop it and log `extract MISS url=... tried=[jina,flare,http]`.
5. **Parallelize per-URL across the chain with a global concurrency cap of 4** (not 5 separate sequential tier-sweeps). One `asyncio.Semaphore(4)`, each URL runs its own fallback ladder, so a slow tier on one URL doesn't block others.

**Files.** `hyperion/tools/deep_search.py`, `hyperion/tools/http_extract.py` (new), `hyperion/tools/obscura.py` (env-guard), `hyperion/agents/sub_agent.py` (`_gather_raw_data`), `hyperion/config.py` (extractor enable flags).

**Acceptance.** For a query like *"India space sector TAM 2025"*, `deep_search(depth="standard")` returns **≥ 4 sources with ≥ 500 chars each** in < 45s, and the TUI logs one line per URL: `extract OK url=... tool=jina chars=3120`.

---

### D2 — `searxng.py` routes everything through Google/DDG (P0)

**Evidence.** `searxng.py::search()` docstring literally says *"SearxNG container and Jina are NOT used — they were unreliable"* and calls `FlareSolverrClient().search()` → Google then DuckDuckGo. The SearXNG container is up (docker log) but unused. VIGIL doc §Layer1 says the exact opposite: *use SearXNG curated engines, drop Google/DDG — they're the CAPTCHA source.*

**Root cause.** A previous "fix" gave up on the flaky SearXNG container and hard-wired the CAPTCHA path. This is why the FlareSolverr log is 100% Google/DDG hits and why they intermittently 500.

**Fix.**
1. **Restore SearXNG as the primary discovery engine**, querying its JSON API (`/search?q=...&format=json`) against a **curated, low-CAPTCHA engine set**: `brave, bing, mojeek, startpage, wikipedia, duckduckgo` configured *inside SearXNG* (server-side), not by scraping Google directly.
2. Fix the container config that the log flags:
   - Provide `/etc/searxng/limiter.toml` (silence the warning, enable the bot-limiter properly).
   - Disable the dead engines (`ahmia`, `torch`, and `wikidata` which 403s) in `searxng_settings.yml`.
   - Ensure `format: json` is allowed in `search.formats`.
3. **Add Jina Search (`s.jina.ai`) as the parallel second discovery source** (keyless), exactly as VIGIL specifies. Merge+dedup URLs.
4. Keep FlareSolverr **only** as (a) a content-extraction fallback for Cloudflare pages and (b) an absolute last-resort search if *both* SearXNG and Jina return nothing — never as the default.
5. Delete/retire the `stealth_search.py` Playwright-Google path as the primary; it competes for the same crashing Chrome.

**Files.** `hyperion/tools/searxng.py` (restore JSON API path), `searxng_settings.yml`, new `searxng-limiter.toml`, `docker-compose.yml` (mount configs), `hyperion/tools/deep_search.py` (discovery uses SearXNG+Jina).

**Acceptance.** `curl "http://localhost:8888/search?q=india+space+sector+TAM&format=json"` returns ≥ 10 results; FlareSolverr log shows **zero** `google.com/search` hits during a normal run except explicit fallback.

---

### D3 — Fact-check/verify loop DoSes FlareSolverr with claim-fragments (P0)

**Evidence.** FlareSolverr log: `$15B technology analyst`, `50% risk analyst`, `70% risk analyst`, `12 m risk analyst`, `$200M risk analyst`, `Export demand reduces TAM by 30-50% risk analyst`, `FX volatility causes 10-20% erosion... risk analyst`. These are **findings text + agent name** turned into search queries — hundreds of them — immediately preceding `net::ERR_CONNECTION_CLOSED` and `session not created: cannot connect to chrome` (Chrome OOM). This is the event that kills search for the rest of the run.

**Root cause.** The Fact Checker (`fact_checker.py`, "Verifying 34 claims against independent sources" in the log) extracts factual claims from findings and **searches each one individually**. Because claims are tokenized crudely, a claim like *"CAC below $55, 50% penetration"* becomes queries like `50% risk analyst`. 34 claims × multiple search variants × Google+DDG each = hundreds of FlareSolverr hits, serialized, each 3–40s → the run stalls and Chrome dies.

**Fix.**
1. **Fact-checking must NOT issue one web search per claim.** Replace with: batch-verify against the **already-extracted source corpus** the specialists collected (in-memory), using the LLM to check claim-vs-evidence. Only escalate to a *new* web search for the top-N (≤ 5) highest-risk claims, and **never** build a query from raw claim text — build a clean keyword query (strip `%`, `$`, units, agent names).
2. **Add a global per-run search budget** (e.g. 60 discovery searches / engagement) enforced in a shared `SearchBudget` singleton. When exhausted, search calls return cached/empty with a logged `SEARCH BUDGET EXHAUSTED` instead of hammering.
3. **Add a per-engine circuit breaker:** after 2 consecutive FlareSolverr 500s, open the breaker for 60s (stop sending to it) so one Chrome crash doesn't cascade.
4. **Query hygiene:** a `normalize_query()` util that rejects/repairs queries that are (a) < 3 meaningful tokens, (b) mostly punctuation/numbers, (c) contain internal tokens like `risk analyst`, `technology analyst`.

**Files.** `hyperion/agents/support/fact_checker.py` (stop per-claim web search), new `hyperion/tools/search_budget.py`, `hyperion/tools/flaresolverr.py` (circuit breaker), new `hyperion/tools/query_utils.py`.

**Acceptance.** A full run issues **< 60 total** FlareSolverr requests; no `session not created` errors; fact-check completes in < 60s using the local corpus.

---

### D4 — Findings don't survive to Synthesis (P0)

**Evidence.** `total collected: 17` during the run, but the final report Methodology says `Total unique sources: 1` and `Agents Used: financial_analyst`. The limitations block confirms other specialists' analyses "were not completed or provided." So 17 findings existed on the bus but only financial's reached the report.

**Root cause (two candidates, both must be closed):**
1. **Bus timing:** Synthesis subscribes to `Channel.FINDINGS` but specialists that finished *before* Synthesis subscribed had their messages dropped (no replay/buffer). The orchestrator comment at line ~403 even says it "collects findings directly" as a workaround — meaning the bus path is unreliable.
2. **Failed specialists publish nothing:** MARKET/OPS/COMPETE/REGULATORY "failed" (D8) so they never published findings; only financial (and a few) did, and Synthesis had a near-empty pool.

**Fix.**
1. **Make the AgentBus buffer/replay findings.** `FINDINGS` channel must retain all messages for the engagement; a late subscriber (Synthesis) gets the full backlog on subscribe. Add `bus.get_all_findings(engagement_id)` as the source of truth, and have the orchestrator pass the **collected findings list explicitly** into `SynthesisLead.run(findings=...)` rather than relying on subscription timing.
2. **Every specialist always publishes findings**, even partial/gap findings (see D8). A "failed" specialist publishes a structured `KeyFinding(finding_type="research_gap", confidence=low, gaps=[...])` so Synthesis sees *why* it's thin and can state it honestly — instead of the finding silently not existing.
3. **Deduplicate sources across findings correctly** so the report's `total_sources` reflects the union of every specialist's sources, not one.

**Files.** `hyperion/agents/bus.py` (retain/replay), `hyperion/orchestrator.py` (explicit findings handoff), all specialists (`_finalize` always publishes), `hyperion/agents/synthesis_lead.py` (`run(findings=...)`).

**Acceptance.** For a run where ≥ 5 specialists complete, the report shows `Agents Used: [≥5]` and `Total unique sources ≥ 15`.

---

### D5 — Synthesis Lead times out, "did not produce a FinalReport" (P0)

**Evidence.** Log: Synthesis reaches `Resolving 0 contradictions`, `Identifying critical path`, then `ERROR X timed out after 300s` (twice), then a `19240 chars` DEEP call, then `X Synthesis Lead did not produce a FinalReport`. Orchestrator `TASK_TIMEOUT_SECONDS=300`, specialists get 600.

**Root cause.** `synthesis_lead.run()` executes **~8 sequential awaited LLM calls at DEEP tier**: resolve contradictions, identify critical path, draft recommendation, then `_build_analysis_sections` which calls `_build_one_section` **once per section** (could be 5–8 more DEEP calls), plus a Second Brain query. DEEP tier = Gemini 3.1 Flash Lite / nemotron-ultra-550b — the slowest and most rate-limited. 8–15 sequential DEEP calls × (5–40s + rate-limit waits) >> 300s. It also has **no timeout-safe partial-report path**: if it doesn't finish, it returns nothing.

**Fix.**
1. **Parallelize independent DEEP calls.** `_build_analysis_sections` must `asyncio.gather` all sections concurrently (bounded by a semaphore of 3), not loop sequentially. Same for any per-contradiction deep dives.
2. **Collapse the call count.** Contradiction-resolution + critical-path + recommendation can be **one structured DEEP call** returning a single JSON object (recommendation, rationale, critical_path, resolved_contradictions, confidence). Sections can be a **second** call that returns all sections at once. Target: **≤ 3 DEEP calls total**, not 15.
3. **Give Synthesis its own generous timeout** (e.g. `SYNTHESIS_TIMEOUT_SECONDS = 480`) separate from specialists, and **always emit a FinalReport** — wrap `run()` so that on timeout/partial it assembles a `FinalReport` from whatever sections/recommendation completed, marked `confidence=low` with an explicit limitation. **Never** return `None`.
4. **DEEP tier must have fallback** (see D9): if Gemini DEEP is rate-limited, spill to nemotron-super-120b (STRONG, 262k ctx) rather than blocking.

**Files.** `hyperion/agents/synthesis_lead.py` (parallelize + collapse calls + partial-report guard), `hyperion/orchestrator.py` (dedicated synthesis timeout, treat partial report as success).

**Acceptance.** Synthesis completes in < 300s wall-clock for a 5-specialist engagement and **always** returns a non-null FinalReport with ≥ 4 sections.

---

### D6 — Absolute Windows paths in rendered HTML (P1)

**Evidence.** Output HTML: `href="C:\Users\Abuza\CascadeProjects\Hyperion\output\...css"` and `src="C:\Users\Abuza\...\1FxMET2U5dU.jpg"`. These resolve on nobody's machine but the author's; in a served/exported PDF they are broken links → blank images, unstyled text.

**Root cause.** The render/design layer writes machine-absolute paths into the template instead of (a) embedding assets as `data:` URIs or (b) using `file://` absolute paths *computed at render time* and passed to WeasyPrint's `base_url`.

**Fix.**
1. **Embed images as base64 `data:` URIs** in the HTML the renderer consumes (WeasyPrint handles large data URIs fine at report scale). This makes the PDF fully self-contained and portable. Downscale/encode to the target print box first (see D11/§10).
2. **Inline the CSS** into a `<style>` block (no external `<link href="C:\...">`).
3. If any real file paths remain, pass `base_url=os.path.abspath(output_dir)` to WeasyPrint and use **relative** `src="assets/images/x.jpg"`, never `C:\`.

**Files.** `hyperion/output/render.py`, `hyperion/output/images.py`, `hyperion/agents/delivery/presentation_designer.py`, `hyperion/agents/delivery/render_engine.py`.

**Acceptance.** The generated `.html`/`.pdf` contains **no** `C:\` or machine-absolute path; opening the PDF on any machine shows all images and full styling.

---

### D7 — Double HTML-escaping of LLM output (P1)

**Evidence.** Output: `<p>&lt;p&gt;Based on synthesis...&lt;/p&gt;</p>` and `&lt;div class=&#39;no-break&#39;&gt;...`. The LLM returned HTML; the template escaped it, so tags render as literal text.

**Root cause.** LLM sections are generated *as HTML fragments*, then inserted into a Jinja template with autoescaping on (or `| e`), which escapes the already-HTML content. Alternatively the model is told to "write HTML" but the pipeline also wraps it in `<p>{{ content | escape }}</p>`.

**Fix — pick ONE content contract and enforce it end-to-end:**
- **Recommended:** LLMs return **clean Markdown / plain structured text**, never HTML. The renderer converts Markdown→HTML with a single trusted `markdown` pass, then styles via CSS. This removes all escaping ambiguity and is far more robust than asking models to emit valid HTML.
- Where a fragment truly must be raw HTML, mark it `| safe` (Jinja) **once** and guarantee the model output is sanitized (bleach allowlist) — but avoid this; prefer Markdown.
- Add a `sanitize_and_render(md_text) -> html` util used by every section.

**Files.** `hyperion/output/markdown.py`, `hyperion/output/render.py`, specialists/synthesis prompts (demand Markdown, forbid HTML), templates in `themes/`.

**Acceptance.** No `&lt;` / `&gt;` / `&#39;` literals in the rendered body; headings, lists, tables render as real HTML.

---

### D8 — Specialists emit empty findings instead of degrading (P1)

**Evidence.** `No competitors identified — cannot proceed`, `0 regulations across 0 jurisdictions`, `carbon=N/A`, `X MARKET — failed`.

**Root cause.** Specialists have a hard "cannot proceed" branch when their first structured extraction is empty. Combined with D1 (no data) and D3 (search dies), the *normal* path becomes the failure path. They then publish nothing (feeding D4).

**Fix.**
1. **Remove all "cannot proceed / failed" hard stops.** A specialist with thin data must still produce its best-effort structured analysis using (a) whatever web data exists, (b) the free structured APIs (FRED, World Bank, SEC EDGAR, OpenAlex — these need no scraping and are reliable), and (c) explicit, labeled assumptions — plus a `gaps` list saying what's missing.
2. **Route specialists to the right free data source first**, before generic web search:
   - Financial → SEC EDGAR + FRED + World Bank.
   - Market/TAM → World Bank + OpenAlex + curated web.
   - Regulatory → curated web + SEC filings' risk sections.
   - Sustainability → World Bank indicators + curated web.
   These APIs are keyless/reliable and were **underused** (report cites only FRED once).
3. **Every specialist ends by publishing findings** (D4), never by failing silently.

**Files.** all `hyperion/agents/specialists/*.py`, `hyperion/agents/base.py` (shared finalize-always logic).

**Acceptance.** No specialist logs "failed" or "cannot proceed"; each publishes ≥ 2 findings (real or explicitly-gap) with sources where available.

---

### D9 — Router DEEP-tier starvation, weak cross-provider fallback (P1)

**Evidence.** Boot shows 5 providers online (google, nvidia, cerebras, groq, mistral). Synthesis (DEEP) still times out. DEEP maps to Gemini/nemotron-ultra which are the most limited.

**Root cause.** Tiers map to specific models, but on rate-limit there's no *graceful spill to a different provider's comparable model*, and no queue/wait accounting that prefers switching providers over waiting.

**Fix.** See §6 for the full router design. Summary:
1. Each tier has an **ordered candidate list spanning multiple providers** (e.g. DEEP = [gemini-3.1-flash-lite (google), nemotron-ultra-550b (nvidia), nemotron-super-120b (nvidia, 262k), gemini-3-flash]). On 429/timeout, immediately try the next candidate instead of waiting.
2. **Provider-level token/RPM accounting** so the router knows *before* calling whether a provider is likely rate-limited and skips it.
3. **DEEP degradation ladder:** DEEP→STRONG→STANDARD with a logged warning, rather than blocking.

**Files.** `hyperion/router/router.py`, `hyperion/router/providers/*`, `hyperion/router/budget.py`, `hyperion/config.py` (tier candidate lists).

**Acceptance.** With Gemini artificially rate-limited, a DEEP call still returns via an NVIDIA model in < 30s and logs `DEEP fallback google→nvidia`.

---

### D10 — Opaque logs (P1)

**Evidence.** TUI shows `completed with 0 findings (total collected: 0)` — no reason, no per-URL, no per-call detail. You literally said "logs are not showing enough details where things go wrong."

**Root cause.** Logging is at the agent-summary level only. The extraction chain uses `logger.debug` (invisible at default level) and swallows exceptions.

**Fix.** See §11. Add structured, leveled, always-visible pipeline events:
- `search`: engine, query (normalized), n_results, took_ms.
- `extract`: url, tool, chars, OK/MISS, reason.
- `llm`: agent, tier, provider, model, prompt_tokens, completion_tokens, took_ms, OK/ERR.
- `finding`: agent, n_findings, n_sources, avg_content_len.
- `stage`: name, status, duration, why (on fail).
Emit these to both the TUI and a `reports/<engagement>/trace.jsonl` for post-mortem.

**Files.** `hyperion/tui/*`, new `hyperion/obs/trace.py`, wired into router/tools/agents.

**Acceptance.** After a run, `trace.jsonl` lets you reconstruct exactly which URL/engine/model failed and why, with zero guesswork.

---

### D11 — Non-semantic / distorted images (P2)

**Evidence.** Attached report uses generic Unsplash photos ("Nick Chong", "Héctor J. Rivas") loosely related, and local-path broken. Goal is McKinsey/BCG: purposeful, high-res, correctly-cropped imagery.

**Root cause.** Image selection is keyword-crude and not fit-to-box; no aspect-ratio-aware cropping → distortion when CSS forces dimensions.

**Fix.** See §10. Semantic query per section, fetch at ≥ target print resolution, **cover-crop to the exact print box** (never stretch), embed as data URI, always attribute.

**Files.** `hyperion/output/images.py`, `hyperion/tools/unsplash.py`, `render_engine.py`.

**Acceptance.** Every image is ≥ 300 DPI for its print box, correct aspect ratio (no stretching), semantically matched, attributed.

---

### D12 — No minimum-quality floor before "delivery" (P2)

**Evidence.** A report built on 1 source with "MEDIUM on market adoption... single-specialist depth" was still rendered and delivered as final.

**Root cause.** Quality Gate scores but doesn't **block** delivery; and even the low score didn't stop render.

**Fix.** Define a hard floor: **do not emit a "final" PDF** if (sources < 8) OR (specialists_completed < 4) OR (quality < 3.0). Instead emit a clearly-watermarked `DRAFT — INSUFFICIENT DATA` PDF listing exactly what's missing, so failures are honest and visible, not disguised as finished work.

**Files.** `hyperion/agents/support/quality_gate.py`, `hyperion/orchestrator.py`, `render_engine.py`.

**Acceptance.** Thin runs produce a `DRAFT` watermark + gap list; only runs meeting the floor produce a clean final.

---

<a name="5-search-stack"></a>
## 5. The search & retrieval stack — correct target architecture

This reconciles the VIGIL design with **what actually works in a keyless Linux sandbox**. The VIGIL doc is the intent; this is the pragmatic realization.

### 5.1 Discovery layer (find candidate URLs)

Two independent, keyless sources, queried in parallel, merged + deduped:

1. **SearXNG (self-hosted, primary)** — JSON API, curated engine set configured server-side.
   - Enabled engines (low-CAPTCHA, no key): `brave, bing, mojeek, startpage, wikipedia, duckduckgo (lite)`.
   - Disabled/broken (per docker log): `ahmia, torch, wikidata` (403), plus google (CAPTCHA).
   - Add `limiter.toml`, set `search.formats: [html, json]`.
2. **Jina Search `s.jina.ai/<query>` (secondary)** — keyless, returns clean result list, independent of SearXNG's engines.

**Never** default to scraping `google.com/search` — that is the entire cause of the FlareSolverr meltdown.

### 5.2 Extraction layer (URL → clean text) — reliability-ordered

Per URL, run this ladder, stop at first success (≥ 500 chars clean prose):

1. **Jina Reader `r.jina.ai/<url>`** — keyless, no local browser, most reliable here. **Primary.**
2. **HTTP + trafilatura** (`http_extract.py`, new) — plain `httpx` GET → `trafilatura.extract`. Zero deps on browser. The floor.
3. **FlareSolverr GET** — for Cloudflare/JS-challenge pages only; already running. Run its returned HTML through trafilatura.
4. **Crawl4AI / Playwright** — only if a health check says Chromium is alive; otherwise skip.
5. **Obscura** — only if a working **Linux** binary exists (the repo ships a *Windows* zip → disable on Linux).

Global concurrency: **one `Semaphore(4)`**; each URL owns its ladder; failures logged per-URL.

### 5.3 Structured data sources (prefer these over scraping)

These are keyless/reliable and were badly underused (report cited FRED once):
- **FRED** — macro/rates/FX.
- **World Bank** — GDP, sector indicators by country.
- **SEC EDGAR** — filings, risk-factor sections, financials (full-text search + document fetch).
- **OpenAlex** — academic/works, citation counts.
- **HackerNews (Algolia)** — tech sentiment (keyless).
- (Dropped by request: **semantic_scholar** needs a key/warns; **reddit** needs OAuth — remove from the default tool set; keep code but don't wire into specialists.)

**Specialist routing:** each specialist hits its *authoritative* structured source first, then curated web, then LLM synthesis. Example: Financial → SEC EDGAR + FRED; Market → World Bank + OpenAlex + web.

### 5.4 Rerank & evidence scoring (the "Exa replacement")

Keep the existing heuristic `EvidenceScorer` (keyword overlap + domain quality + freshness → support/conflict/neutral). **Do not** add pgvector/Ollama now — it's infra you don't need for the current failure. Revisit only after the pipeline reliably produces reports.

### 5.5 Budget, breaker, hygiene (the guardrails that were missing)

- `SearchBudget(engagement)` — hard cap (e.g. 60 discovery searches). Exhaustion logs + returns empty, never hammers.
- Per-engine **circuit breaker** — 2 consecutive 5xx → open 60s.
- `normalize_query()` — reject fragment/number-only/internal-token queries (kills the `50% risk analyst` class of garbage at the source).

---

<a name="6-llm-router"></a>
## 6. The LLM router — five providers, shared with fallback

Providers online at boot: **google, nvidia, cerebras, groq, mistral.** The five tiers (MICRO/FAST/STANDARD/STRONG/DEEP) must map to **ordered, cross-provider candidate lists**, not single models.

### 6.1 Tier → candidate ladder (spill on 429/timeout, don't wait)

| Tier | Use | Candidate ladder (try in order) | Ctx |
|---|---|---|---|
| MICRO | query-gen, tiny tasks | cerebras gpt-oss-120b → groq → mistral-small | 16–131k |
| FAST | fact snippets, inline verify | groq gpt-oss-120b → cerebras → nvidia nemotron-nano-30b | 131k |
| STANDARD | specialist analysis | nvidia nemotron-super-49b → nvidia nano-30b → mistral-medium → magistral | 131–262k |
| STRONG | planning, section writing | nvidia nemotron-super-120b → mistral-large → gemini-3-flash | 262k |
| DEEP | synthesis, long-doc reconcile | gemini-3.1-flash-lite → nemotron-ultra-550b → nemotron-super-120b → gemini-3-flash | 250k–1M |

### 6.2 Rules

1. **Fallback-first, wait-last.** On 429/5xx/timeout for candidate _i_, immediately try _i+1_ (different provider where possible). Only wait if the whole ladder is cooling down.
2. **Provider accounting.** Track RPM/TPM per provider from response headers/own counters; skip a provider predicted to be limited.
3. **Tier degradation ladder.** DEEP→STRONG→STANDARD with a logged `tier downgrade` if the whole DEEP ladder is exhausted — better a slightly weaker synthesis than *no report*.
4. **Per-call budget/urgency** stays, but urgency changes *ordering*, never *blocks to zero*.
5. **Log every call** (D10): agent, tier, chosen provider/model, tokens, latency, outcome.

### 6.3 Token allocation (give sub-agents room)

`MAX_OUTPUT_TOKENS` per tier is currently 500/2000/4000/8000/16000. Raise **STANDARD→6000** and **STRONG→10000** so specialists/sections can be *detailed* (the report goal is depth). Sub-agents run at FAST/STANDARD and should get ≥ 4000 output tokens to return 200–500-word findings as the sub_agent prompt already demands.

---

<a name="7-subagent-depth"></a>
## 7. Sub-agent depth engine — how to get real content

The sub-agent design (context isolation, structured findings) is correct. It produces "shit" today only because **its input (`_gather_raw_data`) is empty** (D1) and its **output isn't validated** (D8). Fixes:

1. **Feed it real text.** With §5 extraction fixed, `_gather_raw_data` returns 4–8 clean sources of ≥ 500 chars. That alone transforms output quality.
2. **Give it the right tools per parent.** Currently sub-agents get a generic tool list. Financial sub-agents must get SEC/FRED; market sub-agents World Bank/OpenAlex. Set `SubAgentSpec.tools` from the parent's domain.
3. **Validate findings before returning.** Reject findings with `content` < 150 chars or `sources == []` **unless** explicitly a gap finding. Retry once with a tightened prompt if the first structured call is empty (the current code returns a single gap finding on any parse failure — add one retry).
4. **Raise output token ceiling** (see §6.3) so 200–500-word findings aren't truncated.
5. **Cap wall-time honestly.** 3 sub-agents × 5-min timeout can eat 15 min *per specialist* serially. **Run a specialist's sub-agents in parallel** (`asyncio.gather`, bounded) so depth doesn't cost linear time — this is why runs took 15–25 min. (The last commit claims to parallelize; verify it actually gathers rather than awaits in a loop.)

**Acceptance.** A specialist with 3 sub-agents finishes in < 5 min wall-clock (parallel) and returns ≥ 6 substantive findings totaling ≥ 3000 words of analysis with ≥ 10 sources.

---

<a name="8-synthesis"></a>
## 8. Synthesis Lead — guaranteeing a FinalReport

Target shape of `run()` after fix:

```
findings = bus.get_all_findings(engagement)      # D4: explicit, replayed, complete
if len(findings) == 0: return minimal_report(reason="no findings")   # honest, never None

# ONE structured DEEP call (collapse steps 3–7):
core = await deep_call_json(prompt=reconcile_prompt(findings, prior_patterns))
#   -> {recommendation, rationale, critical_assumptions, critical_path,
#       resolved_contradictions[], confidence, confidence_breakdown, key_finding_titles}

# ONE structured DEEP call for all sections, or gather() per-section bounded(3):
sections = await gather_sections(core, findings)   # parallel, not sequential

report = FinalReport(... core ..., sections=sections, sources=union(findings.sources))
publish(report); return report
```

Guards:
- **Dedicated timeout** `SYNTHESIS_TIMEOUT_SECONDS=480`; orchestrator treats a returned (even partial) report as success.
- **Partial-report assembler**: if `gather_sections` partially times out, build the report from completed sections + a limitation noting which were skipped. Never `None`, never "did not produce a FinalReport".
- **DEEP fallback** to STRONG (nemotron-super-120b, 262k ctx) if Gemini DEEP is limited (D9).
- **Contradiction step is cheap**: with 0–3 contradictions typical, fold it into the single core call rather than a separate `_deep_dive_contradiction` per item.

**Acceptance.** 100% of runs with ≥ 1 finding produce a non-null FinalReport; ≤ 3 DEEP calls; < 300s.

---

<a name="9-report-quality"></a>
## 9. Report generation — the McKinsey/BCG quality bar

Reference targets: McKinsey *Risk & Resilience #14* and *BCG Sustainability Report 2025* — both are: strong cover, disciplined typographic hierarchy, generous white space, purposeful full-bleed imagery, data-dense exhibits (charts/tables) with captions and source lines, pull-quotes, and a rigorous exec summary.

### 9.1 Content depth requirements (per final report)
- **20–40 pages.** Exec summary (1–2p) + 4–8 specialist sections (2–4p each) + methodology + appendix.
- Every section: a narrative (600–1200 words), **≥ 1 data exhibit** (chart or table), a "So What?" implication box, and a source line.
- **Every quantitative claim cited** to a real extracted source (post-D1/D4 there will be real sources).
- Exec summary must **stand alone** and carry the recommendation + critical assumptions + confidence.

### 9.2 Visual system (in `themes/`)
- **Typography:** one serif for headings (e.g. a Georgia/Tiempos-like), one humanist sans for body; strict scale (H1 28pt / H2 18pt / H3 13pt / body 10.5pt / caption 8pt).
- **Palette:** restrained — one accent (the existing terracotta `#C8704D` is fine), neutrals `#1A1A1A / #8B8680 / #F5F4EE`.
- **Grid:** A4, 300 DPI, consistent margins, `page-break` control, `no-break` for exhibits.
- **Components:** cover, TOC, key-insight box, implication box, data-table, chart figure + caption, confidence badge, section hero image, closing page. (These class names already exist — the CSS just needs to be real and *inlined*, per D6/D7.)

### 9.3 Charts (`output/charts.py`)
- Use **matplotlib/Plotly → static PNG at 300 DPI**, brand palette, embedded as data URI.
- Chart types earned by data: TAM waterfall, sensitivity tornado, scenario bands, competitor 2×2, risk 5×5 heatmap (the risk agent already computes a 5×5 grid), ESG scorecards.
- Every chart has title, axis labels, units, and a source caption. No chart without data → if data thin, render a labeled table instead (never a fake chart).

### 9.4 Rendering (`output/render.py`)
- WeasyPrint primary (300 DPI, embedded fonts), Playwright-Chromium fallback (already present).
- **Self-contained HTML**: inlined CSS + base64 images (D6). No `C:\`, no external links.
- **Markdown→HTML** single trusted pass (D7). Sanitize.

### 9.5 Report assembly order
`cover → exec summary → per-section (narrative + exhibit + implication) → contradictions/limitations → methodology (agents, sources, data points) → appendix (full source list) → closing`.

**Acceptance.** Side-by-side with the McKinsey/BCG PDFs, the output has: real charts, real citations (≥ 15 sources), correct images, no escaped tags, no broken paths, consistent typography, and reads as one coherent argument.

---

<a name="10-image-pipeline"></a>
## 10. Image pipeline — pixel-perfect Unsplash, zero distortion

1. **Semantic query per placement.** Derive the image query from the *section's topic and tone*, not the raw question (e.g. section "Regulatory Landscape" → `"government building policy india architecture"`). Use the platform image search (Creative-Commons filtered) or Unsplash source; if results are weak/irrelevant (commercial-license risk), **generate** an original image instead.
2. **Fetch at ≥ target resolution.** Print box for a full-bleed cover at A4/300DPI ≈ 2480×3508; section hero ≈ 2480×900. Always fetch ≥ that.
3. **Cover-crop, never stretch.** Compute the crop that fills the box at the correct aspect ratio (center or rule-of-thirds), then resize. This is what prevents the distortion you called out. Use Pillow `ImageOps.fit(img, (w,h), method=LANCZOS)`.
4. **Embed as base64 data URI** (D6) so the PDF is portable.
5. **Always attribute** ("Source: Unsplash via <photographer>") in a caption line, and keep a machine record for the appendix.
6. **Deterministic fallback.** If no suitable image, render a branded gradient/pattern block with the section title — never a broken `img`.

**Acceptance.** Zero stretched/distorted images; every image ≥ 300 DPI for its box; every image attributed; PDF portable.

---

<a name="11-observability"></a>
## 11. Observability — logs that show where it broke

Add `hyperion/obs/trace.py`: a structured event emitter that writes to (a) the TUI stream and (b) `reports/<engagement>/trace.jsonl`.

Event schema (one JSON line each):
```json
{"t": 1730000000.12, "stage":"extract", "engagement":"eng_x",
 "agent":"market_analyst", "url":"https://...", "tool":"jina",
 "status":"OK", "chars":3120, "took_ms":812, "reason":null}
```
Mandatory event types & fields:
- `search` — engine, query_normalized, n_results, took_ms, status.
- `extract` — url, tool, chars, status(OK/MISS), reason.
- `llm` — agent, tier, provider, model, prompt_tokens, completion_tokens, took_ms, status, fallback_from?.
- `finding` — agent, n_findings, n_sources, avg_len.
- `subagent` — parent, question, n_findings, took_ms.
- `stage` — name, status, duration_ms, reason(on fail).
- `budget` — search_used/search_cap, provider RPM/TPM snapshots.

TUI: show a compact live view (current stage, per-agent finding counts, search-budget gauge, provider health lights). On any failure, the TUI prints the `reason`. This directly fixes "logs not showing where it broke."

**Acceptance.** From `trace.jsonl` alone you can answer: which URLs failed extraction and why; which LLM calls fell back and to whom; how many findings each agent produced; which stage stalled.

---

<a name="12-degradation"></a>
## 12. Fallback & graceful-degradation doctrine

The governing principle that was missing: **degrade loudly, never silently.**

- Every fallback logs `fallback A→B reason=...`.
- Every empty result carries a `reason`.
- No bare `except Exception: continue` — catch, log a `trace` event, then continue.
- Minimum-data contracts (D12): below-floor runs emit an honest `DRAFT — INSUFFICIENT DATA` PDF with a gap list, not a polished lie.
- The system's worst-case output is a **short, correct, clearly-labeled draft**, not a long confident hallucination on 1 source.

---

<a name="13-roadmap"></a>
## 13. Phased execution roadmap with acceptance gates

Do these strictly in order; each phase has a gate that must pass before the next.

### Phase 0 — Stop the bleeding (search sanity) — P0
- D2: restore SearXNG JSON + Jina discovery; stop defaulting to Google/DDG.
- D3: kill per-claim web search in fact-checker; add SearchBudget + circuit breaker + query hygiene.
- **Gate:** a run issues < 60 FlareSolverr requests, zero `session not created`, SearXNG returns ≥ 10 results for a test query.

### Phase 1 — Make extraction return real text — P0
- D1: reorder chain (Jina→http_extract→Flare→browser→obscura); add `http_extract.py`; env-guard Obscura; parallel per-URL.
- D10 (partial): add `trace.py` + `extract`/`search`/`llm` events so you can *see* it working.
- **Gate:** `deep_search("India space TAM 2025")` returns ≥ 4 sources ≥ 500 chars in < 45s, visible in trace.

### Phase 2 — Findings survive & specialists degrade gracefully — P0/P1
- D4: bus replay + explicit findings handoff to Synthesis.
- D8: remove "cannot proceed"; route to structured APIs; always publish findings.
- **Gate:** 5-specialist run → report shows ≥ 5 agents, ≥ 15 unique sources.

### Phase 3 — Synthesis always finishes — P0
- D5: collapse to ≤ 3 DEEP calls, parallel sections, partial-report guard, dedicated timeout.
- D9: cross-provider tier ladders + DEEP→STRONG degradation.
- **Gate:** 100% of runs with ≥ 1 finding produce a non-null FinalReport in < 300s.

### Phase 4 — Report looks boardroom-grade — P1/P2
- D6 (portable paths/base64), D7 (Markdown→HTML, no double-escape), §9 visual system, §9.3 real charts.
- D11/§10 image pipeline (semantic + cover-crop + embed).
- **Gate:** output PDF has no `C:\`, no escaped tags, real charts, correct images; visually comparable to McKinsey/BCG references.

### Phase 5 — Honest quality floor — P2
- D12: quality floor + `DRAFT` watermark for thin runs.
- Full D10 TUI dashboard.
- **Gate:** thin run → labeled DRAFT; full run → clean final; every failure explains itself.

---

<a name="14-acceptance-tests"></a>
## 14. Acceptance test matrix

Run the system on three fixed questions and assert:

| Test | Question | Must pass |
|---|---|---|
| T1 (happy) | "Should India expand its private space sector?" | ≥ 5 agents, ≥ 15 sources, FinalReport non-null, 20+ page PDF, real charts, portable images |
| T2 (search-hostile) | A niche B2B question with sparse web data | Specialists degrade gracefully, publish gap findings, DRAFT watermark if < floor, **no crash, no 25-min hang** |
| T3 (provider-stress) | Any question with Gemini DEEP disabled | DEEP falls back to NVIDIA, synthesis still finishes, trace shows `DEEP fallback google→nvidia` |

Automated assertions live in `tests/`; each maps to a Gate above. Add:
- `tests/test_search_budget.py`, `tests/test_query_hygiene.py`
- `tests/test_extraction_floor.py`
- `tests/test_findings_survive.py`
- `tests/test_synthesis_always_returns.py`
- `tests/test_render_portable.py` (asserts no `C:\`, no `&lt;p&gt;` in output)

---

<a name="15-appendix-file-map"></a>
## 15. Appendix A — file-by-file change map

| File | Change | Defects |
|---|---|---|
| `hyperion/tools/searxng.py` | Restore SearXNG JSON API as primary; demote FlareSolverr to fallback | D2 |
| `searxng_settings.yml` | Curated engines; disable ahmia/torch/wikidata/google; enable json format | D2 |
| `searxng-limiter.toml` (new) | Silence limiter warning; proper bot limiter | D2 |
| `docker-compose.yml` | Mount settings + limiter; healthchecks | D2 |
| `hyperion/agents/support/fact_checker.py` | Stop per-claim web search; verify vs local corpus; ≤5 escalations | D3 |
| `hyperion/tools/search_budget.py` (new) | Per-engagement search cap singleton | D3 |
| `hyperion/tools/query_utils.py` (new) | `normalize_query()` reject fragments/number-only | D3 |
| `hyperion/tools/flaresolverr.py` | Circuit breaker; use as extractor GET too | D1,D3 |
| `hyperion/tools/http_extract.py` (new) | httpx + trafilatura floor extractor | D1 |
| `hyperion/tools/deep_search.py` | Reorder extraction ladder; parallel per-URL; quality threshold; trace | D1,D10 |
| `hyperion/tools/obscura.py` | Env-guard: disable on Linux (repo ships Windows binary) | D1 |
| `hyperion/agents/sub_agent.py` | Real data in; validate findings; retry once; parallel sub-agents; domain tools | D1,D7 |
| `hyperion/agents/bus.py` | Retain/replay FINDINGS for late subscribers | D4 |
| `hyperion/orchestrator.py` | Explicit findings handoff; dedicated synthesis timeout; partial=success; floor | D4,D5,D12 |
| `hyperion/agents/specialists/*.py` | Remove hard stops; route to structured APIs; always publish findings | D8 |
| `hyperion/agents/base.py` | Shared "finalize-always-publishes" logic | D4,D8 |
| `hyperion/agents/synthesis_lead.py` | Collapse to ≤3 DEEP calls; parallel sections; partial-report guard | D5 |
| `hyperion/router/router.py` | Cross-provider candidate ladders; fallback-first; tier degradation | D9 |
| `hyperion/router/providers/*` | RPM/TPM accounting; skip-if-limited | D9 |
| `hyperion/config.py` | Tier candidate lists; raise STANDARD/STRONG output tokens; extractor flags | D6,D9 |
| `hyperion/output/render.py` | Self-contained HTML; base_url; no absolute paths | D6,D7 |
| `hyperion/output/markdown.py` | Single trusted Markdown→HTML + sanitize | D7 |
| `hyperion/output/images.py` | Cover-crop to box; base64 embed; attribution | D6,D11 |
| `hyperion/output/charts.py` | 300-DPI branded PNG charts; table fallback | §9.3 |
| `hyperion/tools/unsplash.py` | Semantic query; ≥res fetch; CC/generation fallback | D11 |
| `hyperion/agents/delivery/presentation_designer.py` | Emit Markdown sections; relative/base64 assets | D6,D7,§9 |
| `hyperion/agents/delivery/render_engine.py` | Portable assembly; DRAFT watermark | D6,D12 |
| `hyperion/agents/support/quality_gate.py` | Enforce floor; block clean-final if below | D12 |
| `hyperion/obs/trace.py` (new) | Structured JSONL + TUI events | D10 |
| `hyperion/tui/*` | Live dashboard: stage, findings, budget, provider health, failure reasons | D10 |
| `themes/*` | Real inlined CSS design system (typography/grid/components) | §9 |
| `tests/*` | Gate tests (see §14) | all |

---

<a name="16-appendix-additions"></a>
## 16. Appendix B — what to ADD that isn't there yet

Beyond fixing defects, these additions move the output from "works" to "S&P-500-grade":

1. **Exhibit engine.** A dedicated module that, given a specialist's structured numbers, *chooses and renders* the right exhibit (waterfall/tornado/2×2/heatmap/scorecard) — so charts are earned by data, consistent, and branded. This is the single biggest visual differentiator vs. the current text-only report.
2. **Citation manager.** Central registry mapping every claim → source with dedup, credibility tier, and a numbered footnote system rendered in the appendix (McKinsey-style `¹`). Guarantees "every number is cited."
3. **Narrative coherence pass.** One final STRONG-tier call that reads the assembled sections and rewrites transitions so the report reads as one voice, not stapled agent outputs. (Cheap: 1 call, big quality gain.)
4. **Prior-engagement memory (Second Brain).** Actually persist each engagement's findings/report to the vault so Synthesis's "query prior patterns" returns real precedent over time — the proprietary compounding moat.
5. **Deterministic cover generator.** Branded cover with question, recommendation badge, confidence, date, engagement id — sanitized (fixes `should india enter into ai ?>`).
6. **Run manifest.** `reports/<engagement>/manifest.json` capturing config, provider usage, token spend, timings, source list — for reproducibility and cost/telemetry.
7. **Health preflight.** At boot, actively test each dependency (SearXNG json, Jina reachable, FlareSolverr, Chromium, each LLM provider ping) and print a green/amber/red board — so you know *before* a 25-minute run what's degraded. (The boot screen already shows some of this; make it actionable and per-dependency.)
8. **Per-domain rate limiter** in the orchestrator (VIGIL §6 honest-limit note) so fan-out across agents doesn't burn shared IP reputation.

---

## Closing note

Nothing here requires a paid API. The system already has the hard parts — a real multi-agent DAG, five working LLM providers, a router, a WeasyPrint pipeline, and 20 agents. It fails because **the search/extraction floor collapsed, a fact-check loop DoS'd the browser, findings didn't survive to synthesis, synthesis timed out, and the renderer mangled paths/HTML** — and because **every link failed silently instead of loudly.**

Fix the five P0 defects in order (D2→D3→D1→D4→D5), instrument with `trace.py`, then invest in the visual/exhibit/citation layers. The result is a proprietary, zero-cost engine that turns one question into a boardroom-grade, fully-cited, beautifully-typeset PDF — on par with the McKinsey and BCG references, and defensibly *yours*.

---

# PART II — IMPLEMENTATION-GRADE DETAIL (code sketches, configs, exact rules)

> Part I is the *what/why*. Part II is the *how* — copy-adaptable sketches so the implementer never has to re-derive intent. These are illustrative (types may need aligning to the real schemas) but capture the exact control flow each fix requires.

<a name="p2-d2"></a>
## II.1 — D2 fix in full: SearXNG-first discovery

### II.1.1 The corrected `SearxNGClient.search()`

The current method throws away SearXNG and calls FlareSolverr→Google. Replace with a real JSON-API call, and only fall back after SearXNG *and* Jina both fail.

```python
async def search(self, query, num_results=10, categories="general",
                 language="en", time_range="", engines="", safesearch=1,
                 max_results=None) -> SearchResponse:
    query = normalize_query(query)                 # D3 hygiene — reject junk early
    if not query:
        trace("search", engine="searxng", status="SKIP", reason="empty/invalid query")
        return SearchResponse(query=query)

    if max_results is not None:
        num_results = max_results

    cache_key = self._cache_key(query, num_results=num_results, categories=categories)
    if (cached := self._get_cached(cache_key)):
        return cached

    if not SearchBudget.current().allow("searxng"):
        trace("budget", engine="searxng", status="EXHAUSTED")
        return SearchResponse(query=query)

    # ── PRIMARY: SearXNG JSON API ──
    try:
        client = await self._get_client()
        params = {
            "q": query, "format": "json", "language": language,
            "safesearch": safesearch, "categories": categories,
        }
        if time_range:
            params["time_range"] = time_range
        if engines:
            params["engines"] = engines
        async with self._semaphore:
            r = await client.get("/search", params=params)
        r.raise_for_status()
        data = r.json()
        results = [
            SearchResult(
                title=it.get("title", ""), url=it.get("url", ""),
                snippet=it.get("content", ""), engine=it.get("engine", "searxng"),
                score=float(it.get("score", 0.0)), category=categories,
                published_date=it.get("publishedDate", "") or "",
            )
            for it in data.get("results", []) if it.get("url")
        ]
        results = self._deduplicate(results)[:num_results]
        trace("search", engine="searxng", query=query, n_results=len(results), status="OK")
        if results:
            resp = SearchResponse(query=query, results=results, total=len(results),
                                  engines_used=list({x.engine for x in results}))
            self._set_cached(cache_key, resp)
            return resp
    except Exception as e:
        trace("search", engine="searxng", query=query, status="ERR", reason=str(e)[:120])

    # ── SECONDARY: Jina Search (keyless) ──
    try:
        jina = JinaClient(settings=self.settings)
        jresp = await jina.search(query=query, num_results=num_results)
        await jina.close()
        if jresp.results:
            trace("search", engine="jina", query=query, n_results=len(jresp.results), status="OK")
            resp = SearchResponse(query=query, results=[
                SearchResult(title=j.title, url=j.url, snippet=j.snippet,
                             engine="jina", score=1.0, category=categories)
                for j in jresp.results
            ][:num_results], total=len(jresp.results), engines_used=["jina"])
            self._set_cached(cache_key, resp)
            return resp
    except Exception as e:
        trace("search", engine="jina", query=query, status="ERR", reason=str(e)[:120])

    # ── LAST RESORT ONLY: FlareSolverr (Cloudflare-protected SERP) ──
    if SearchBudget.current().allow("flaresolverr") and FlareBreaker.closed():
        try:
            flare = FlareSolverrClient()
            raw = await flare.search(query, num_results=num_results)  # bing/brave SERP, NOT google-default
            await flare.close()
            if raw:
                trace("search", engine="flaresolverr", query=query, n_results=len(raw), status="OK")
                resp = SearchResponse(query=query, results=[
                    SearchResult(title=x.get("title",""), url=x.get("url",""),
                                 snippet=x.get("snippet",""), engine="flaresolverr",
                                 score=0.5, category=categories) for x in raw
                ][:num_results], engines_used=["flaresolverr"])
                self._set_cached(cache_key, resp)
                return resp
        except Exception as e:
            FlareBreaker.record_error()
            trace("search", engine="flaresolverr", query=query, status="ERR", reason=str(e)[:120])

    return SearchResponse(query=query)   # honest empty, already traced
```

### II.1.2 `searxng_settings.yml` — curated, low-CAPTCHA engine set

```yaml
use_default_settings: true
server:
  secret_key: "change-me"
  limiter: true
  image_proxy: true
search:
  safe_search: 1
  formats:
    - html
    - json          # REQUIRED — the code calls format=json
engines:
  - name: brave
    disabled: false
  - name: bing
    disabled: false
  - name: mojeek
    disabled: false
  - name: startpage
    disabled: false
  - name: duckduckgo
    disabled: false
  - name: wikipedia
    disabled: false
  # ── disabled: broken or CAPTCHA sources (per docker log) ──
  - name: google
    disabled: true          # CAPTCHA farm — never default here
  - name: wikidata
    disabled: true          # 403 in log
  - name: ahmia
    disabled: true          # can't register
  - name: torch
    disabled: true          # can't register
```

### II.1.3 `searxng-limiter.toml` — silence the warning, enable the limiter

```toml
[real_ip]
x_for = 1
ipv4_prefix = 32
ipv6_prefix = 48

[botdetection.ip_limit]
filter_link_local = true
link_token = false

[botdetection.ip_lists]
pass_ip = ["127.0.0.1", "172.17.0.0/16"]
```

### II.1.4 `docker-compose.yml` — mount configs + healthchecks

```yaml
services:
  searxng:
    image: searxng/searxng
    ports: ["8888:8080"]
    volumes:
      - ./searxng_settings.yml:/etc/searxng/settings.yml:ro
      - ./searxng-limiter.toml:/etc/searxng/limiter.toml:ro
    environment:
      - SEARXNG_BASE_URL=http://localhost:8888/
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:8080/search?q=test&format=json"]
      interval: 30s
      timeout: 10s
      retries: 3
  flaresolverr:
    image: ghcr.io/flaresolverr/flaresolverr
    ports: ["8191:8191"]
    environment:
      - LOG_LEVEL=info
      - BROWSER_TIMEOUT=40000
    restart: unless-stopped
```

**Acceptance re-stated:** `curl "http://localhost:8888/search?q=india+space+sector+TAM&format=json" | jq '.results | length'` ≥ 10.

---

<a name="p2-d3"></a>
## II.2 — D3 fix in full: kill the FlareSolverr flood

### II.2.1 `query_utils.normalize_query()`

The garbage in the log (`50% risk analyst`, `$15B technology analyst`, `12 m risk analyst`) all share tells: agent-name suffix, currency/percent/number-only content, < 3 real tokens. Reject them.

```python
import re

_INTERNAL_TOKENS = {
    "risk analyst", "technology analyst", "financial analyst", "market analyst",
    "competitive intel", "operations analyst", "regulatory analyst",
    "sustainability analyst", "innovation analyst", "consumer insights",
    "strategy analyst", "ma analyst",
}
_STOPish = re.compile(r"^[\s\W\d%$.,]+$")     # only punctuation/numbers/symbols

def normalize_query(q: str) -> str:
    if not q:
        return ""
    s = q.strip()
    low = s.lower()
    # strip agent-name suffixes that leaked in
    for tok in _INTERNAL_TOKENS:
        if low.endswith(tok):
            s = s[: len(s) - len(tok)].strip()
            low = s.lower()
    # remove standalone currency/percent tokens
    s = re.sub(r"[$£€]\s?\d[\d,.\-–—]*\s?(?:[mbtk]|bn|billion|million|trillion)?", " ", s, flags=re.I)
    s = re.sub(r"\b\d[\d,.\-–—]*\s?%?\b", " ", s)
    s = re.sub(r"\s+", " ", s).strip()
    tokens = [t for t in s.split() if len(t) > 1]
    if len(tokens) < 3:            # too thin to be a real query
        return ""
    if _STOPish.match(s):
        return ""
    return " ".join(tokens)[:256]
```

### II.2.2 `SearchBudget` — per-engagement hard cap

```python
class SearchBudget:
    _instance: "SearchBudget | None" = None
    def __init__(self, cap: int = 60):
        self.cap = cap
        self.used: dict[str, int] = {}
    @classmethod
    def start(cls, cap=60):    cls._instance = SearchBudget(cap); return cls._instance
    @classmethod
    def current(cls):          return cls._instance or cls.start()
    def allow(self, engine: str) -> bool:
        total = sum(self.used.values())
        if total >= self.cap:
            return False
        self.used[engine] = self.used.get(engine, 0) + 1
        return True
    def snapshot(self): return {"used": sum(self.used.values()), "cap": self.cap, "by_engine": dict(self.used)}
```

Orchestrator calls `SearchBudget.start(cap=60)` at the top of each engagement.

### II.2.3 `FlareBreaker` — circuit breaker

```python
import time
class FlareBreaker:
    _fails = 0
    _open_until = 0.0
    THRESHOLD = 2
    COOLDOWN = 60.0
    @classmethod
    def closed(cls) -> bool:
        return time.time() >= cls._open_until
    @classmethod
    def record_error(cls):
        cls._fails += 1
        if cls._fails >= cls.THRESHOLD:
            cls._open_until = time.time() + cls.COOLDOWN
            cls._fails = 0
            trace("breaker", engine="flaresolverr", status="OPEN", reason="2 consecutive 5xx")
    @classmethod
    def record_ok(cls): cls._fails = 0
```

### II.2.4 Fact Checker — verify against local corpus, not the web

Current fact-checker searches each of ~34 claims. Replace core loop:

```python
async def verify(self, claims: list[Claim], corpus: list[ExtractedContent]) -> FactCheckReport:
    # corpus = all sources specialists already extracted (passed in, NOT re-fetched)
    corpus_text = "\n\n".join(c.content[:4000] for c in corpus)[:120_000]
    # ONE batched FAST/STANDARD LLM call verifying all claims vs corpus
    verdicts = await self._llm_batch_verify(claims, corpus_text)   # supported/contradicted/unverified
    # Escalate to web ONLY the top-N unverified, HIGH-materiality claims
    to_escalate = [v.claim for v in verdicts
                   if v.status == "unverified" and v.materiality == "high"][:5]
    for claim in to_escalate:
        q = normalize_query(claim.subject_keywords())   # clean keywords, NOT raw claim text
        if not q:
            continue
        res = await self.search.search(q, num_results=5)   # subject to SearchBudget + breaker
        ...
    return FactCheckReport(verdicts=verdicts, ...)
```

**Acceptance re-stated:** whole run < 60 FlareSolverr hits; fact-check < 60s; zero `session not created`.

---

<a name="p2-d1"></a>
## II.3 — D1 fix in full: extraction returns real text

### II.3.1 `http_extract.py` — the browserless floor

```python
import httpx
from dataclasses import dataclass

@dataclass
class ExtractResult:
    url: str = ""; title: str = ""; content: str = ""; markdown: str = ""; tool_used: str = "http"

class HTTPExtractClient:
    UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128 Safari/537.36"
    async def fetch(self, url: str) -> ExtractResult:
        try:
            async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                         headers={"User-Agent": self.UA}) as c:
                r = await c.get(url)
                r.raise_for_status()
                html = r.text
        except Exception:
            return ExtractResult(url=url)
        try:
            import trafilatura
            text = trafilatura.extract(html, include_comments=False,
                                       include_tables=True, favor_recall=True) or ""
        except Exception:
            import re
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
        return ExtractResult(url=url, content=text[:15000], markdown=text[:15000], tool_used="http")
    async def close(self): ...
```

Add `trafilatura` and `readability-lxml` to `pyproject.toml` dependencies.

### II.3.2 Reordered, per-URL-parallel extraction in `deep_search._extract_batch`

```python
EXTRACTOR_LADDER = ["jina", "http", "flaresolverr", "crawl4ai", "obscura"]

async def _extract_one(self, sem, url) -> ExtractedContent | None:
    async with sem:
        for tool in self._enabled_extractors():        # env-guarded order
            try:
                content, title = await self._run_extractor(tool, url)
            except Exception as e:
                trace("extract", url=url, tool=tool, status="ERR", reason=str(e)[:100])
                continue
            if content and self._is_quality_content(content):
                trace("extract", url=url, tool=tool, status="OK", chars=len(content))
                return ExtractedContent(url=url, title=title, content=content[:15000],
                                        markdown=content[:15000], tool_used=tool)
            trace("extract", url=url, tool=tool, status="MISS", chars=len(content or ""))
        return None

async def _extract_batch(self, urls):
    sem = asyncio.Semaphore(4)
    results = await asyncio.gather(*[self._extract_one(sem, u) for u in urls])
    extracted = [r for r in results if r]
    return extracted, list({r.tool_used for r in extracted})

def _enabled_extractors(self):
    order = ["jina", "http"]                            # always available (keyless, no browser)
    if FlareBreaker.closed(): order.append("flaresolverr")
    if self._chromium_healthy(): order.append("crawl4ai")
    if self._obscura_linux_ok(): order.append("obscura")
    return order

def _is_quality_content(self, content: str) -> bool:
    if not content or len(content) < 500:              # raised floor (was 100)
        return False
    low = content.lower()
    junk = sum(k in low for k in ("access denied","captcha","enable javascript","are you a robot"))
    return junk < 2
```

### II.3.3 Obscura env-guard (`obscura.py`)

```python
import shutil, platform, os
def obscura_available() -> bool:
    if platform.system() != "Linux":
        return False
    # repo ships obscura-x86_64-windows.zip — a Windows binary can't run on Linux
    binpath = os.environ.get("OBSCURA_BIN") or shutil.which("obscura")
    if not binpath or not os.access(binpath, os.X_OK):
        trace("extract", tool="obscura", status="DISABLED", reason="no linux executable")
        return False
    return True
```

**Acceptance re-stated:** `deep_search("India space sector TAM 2025", depth="standard")` → ≥ 4 sources ≥ 500 chars in < 45s; trace shows per-URL OK/MISS.

---

<a name="p2-d4"></a>
## II.4 — D4 fix in full: findings survive to Synthesis

### II.4.1 Bus retention/replay (`bus.py`)

```python
class AgentBus:
    def __init__(self):
        self._findings_log: dict[str, list[Any]] = {}   # engagement -> [KeyFinding-bearing msgs]
    async def publish(self, channel, msg_type, sender, payload):
        if channel == Channel.FINDINGS and payload.get("findings"):
            eng = payload.get("engagement_id", "default")
            self._findings_log.setdefault(eng, []).append(payload)
        ... existing dispatch to live subscribers ...
    def get_all_findings(self, engagement_id: str) -> list[KeyFinding]:
        out = []
        for payload in self._findings_log.get(engagement_id, []):
            out.extend(_coerce_findings(payload.get("findings", [])))
        # dedup by (agent,title)
        seen, uniq = set(), []
        for f in out:
            k = (getattr(f,"agent",""), getattr(f,"title",""))
            if k not in seen: seen.add(k); uniq.append(f)
        return uniq
```

### II.4.2 Orchestrator hands findings to Synthesis explicitly

```python
# Stage 4
all_findings = self.bus.get_all_findings(engagement_id)   # source of truth, replayed
trace("stage", name="synthesis_input", n_findings=len(all_findings),
      agents=sorted({f.agent for f in all_findings}))
report = await asyncio.wait_for(
    synthesis_agent.run(engagement_id=engagement_id, question=question,
                        dag=dag, findings=all_findings),      # <-- explicit
    timeout=self.SYNTHESIS_TIMEOUT_SECONDS,                   # 480
)
```

### II.4.3 Every specialist always publishes (`base.py` shared finalize)

```python
async def finalize(self, findings: list[KeyFinding]):
    if not findings:
        findings = [KeyFinding(
            id=f"gap_{self.name.value}_{int(time.time())}",
            agent=self.name.value, finding_type="research_gap",
            title=f"{self.display_name}: insufficient data",
            content="No sufficient data was extractable for this domain in this run. "
                    "Downstream synthesis should treat this domain as a gap, not absent.",
            sources=[], confidence=ConfidenceLevel.LOW,
            gaps=[self._question])]
    await self.bus.publish(Channel.FINDINGS, MessageType.FINDING, self.name,
        {"engagement_id": self._engagement_id, "agent": self.name.value,
         "findings": [f.model_dump() for f in findings]})
    trace("finding", agent=self.name.value, n_findings=len(findings),
          n_sources=sum(len(f.sources) for f in findings))
```

**Acceptance re-stated:** ≥ 5 specialists complete → report `Agents Used ≥ 5`, `Total unique sources ≥ 15`.

---

<a name="p2-d5"></a>
## II.5 — D5 fix in full: Synthesis always returns, fast

### II.5.1 Collapse to ≤ 3 DEEP calls + parallel sections + partial guard

```python
async def run(self, engagement_id="", question="", dag=None, findings=None) -> FinalReport:
    self._engagement_id, self._question, self._dag = engagement_id, question, dag
    findings = findings if findings is not None else self.bus.get_all_findings(engagement_id)
    if not findings:
        return self._minimal_report(reason="no specialist findings")   # never None

    prior = await self._safe(self._query_second_brain_for_patterns(question), default="")

    # CALL 1 (DEEP): reconcile everything at once
    core = await self._safe(self._reconcile_core(findings, prior), default=None)
    if core is None:                       # DEEP unavailable -> STRONG fallback already tried in router
        core = self._heuristic_core(findings)

    # CALL 2..: sections in PARALLEL, bounded, each independently timeout-guarded
    sections = await self._gather_sections(core, findings, concurrency=3, per_section_timeout=90)

    report = self._assemble(core, sections, findings)   # tolerant of missing sections
    self._current_report = report
    await self._publish(report)
    return report

async def _gather_sections(self, core, findings, concurrency, per_section_timeout):
    sem = asyncio.Semaphore(concurrency)
    async def one(sec_spec):
        async with sem:
            try:
                return await asyncio.wait_for(self._build_one_section(sec_spec, core, findings),
                                              timeout=per_section_timeout)
            except Exception as e:
                trace("stage", name=f"section:{sec_spec.title}", status="SKIP", reason=str(e)[:100])
                return AnalysisSection(title=sec_spec.title,
                    body="_This section could not be fully generated in time; "
                         "see limitations._", confidence=ConfidenceLevel.LOW)
    return await asyncio.gather(*[one(s) for s in self._section_specs(core, findings)])
```

`_assemble` builds a `FinalReport` from whatever is present, appends a limitation listing skipped sections, and sets `confidence=low` if > 1 section was skipped. **It cannot return None.**

### II.5.2 Orchestrator treats partial as success

```python
try:
    report = await asyncio.wait_for(synthesis_agent.run(...), timeout=self.SYNTHESIS_TIMEOUT_SECONDS)
except asyncio.TimeoutError:
    report = synthesis_agent.get_current_report()     # partial, built incrementally
    if report is None:
        report = synthesis_agent._minimal_report(reason="synthesis wall-clock exceeded")
    trace("stage", name="synthesis", status="PARTIAL", reason="timeout -> using partial report")
self.final_report = report                            # ALWAYS set
```

**Acceptance re-stated:** every run with ≥ 1 finding → non-null FinalReport, ≤ 3 DEEP calls, < 300s.

---

<a name="p2-d6d7"></a>
## II.6 — D6+D7 fix in full: portable, correctly-rendered HTML

### II.6.1 Content contract: LLM emits Markdown; renderer owns HTML

- Specialist/synthesis prompts: *"Return the section body as GitHub-flavored Markdown. Do NOT emit HTML tags."*
- One conversion util:

```python
import markdown as md, bleach
ALLOWED = ["p","h1","h2","h3","h4","ul","ol","li","strong","em","blockquote",
           "table","thead","tbody","tr","th","td","a","code","pre","hr","br","img","span"]
ALLOWED_ATTRS = {"a":["href","title"], "img":["src","alt"], "span":["class"], "*":["class"]}

def md_to_html(text: str) -> str:
    html = md.markdown(text or "", extensions=["tables","fenced_code","sane_lists"])
    return bleach.clean(html, tags=ALLOWED, attributes=ALLOWED_ATTRS, strip=True)
```

- Jinja templates insert section HTML with `{{ body_html | safe }}` (already sanitized) — **never** `| e` on already-HTML content, and never wrap it in another `<p>`.

### II.6.2 Portable assets: inline CSS + base64 images

```python
def build_self_contained_html(sections, css_text, images: dict[str,bytes]) -> str:
    def data_uri(b, mime="image/jpeg"):
        import base64
        return f"data:{mime};base64,{base64.b64encode(b).decode()}"
    # replace every image placeholder with a data URI; inline CSS in <style>
    ...
```

- WeasyPrint call passes `base_url=os.path.abspath(output_dir)` as a belt-and-suspenders default; but with data URIs there are **no external refs at all**.
- Sanitize the cover title: `html.escape(question.strip().rstrip("?>").strip()) + "?"` → fixes `should india enter into ai ?>`.

**Acceptance re-stated:** output contains no `C:\`, no `&lt;p&gt;`; images + styling render on any machine.

---

<a name="p2-d8"></a>
## II.7 — D8 fix: specialists degrade, never hard-fail

Pattern for every specialist's research phase:

```python
async def _research(self):
    data = []
    # 1) authoritative structured source FIRST (keyless, reliable)
    data += await self._safe(self._structured_source())      # SEC/FRED/WorldBank/OpenAlex per domain
    # 2) curated web via deep_search (post-D1/D2 this returns real text)
    data += await self._safe(self._web_research())
    # 3) NEVER "cannot proceed" — analyze with whatever exists + explicit assumptions
    findings = await self._analyze(data)                     # LLM structured output
    findings = [f for f in findings if len(f.content) >= 150 or f.finding_type == "research_gap"]
    await self.finalize(findings)                            # always publishes (D4)
```

Delete every `if not X: escalate("cannot proceed"); return`. Replace with a gap-annotated finding.

Domain→source routing table (implement in each specialist):

| Specialist | Primary structured | Secondary | Web |
|---|---|---|---|
| financial_analyst | SEC EDGAR, FRED | World Bank | deep_search |
| market_analyst | World Bank | OpenAlex | deep_search |
| competitive_intel | — | OpenAlex | deep_search |
| regulatory_analyst | SEC risk-factors | — | deep_search |
| sustainability | World Bank | — | deep_search |
| technology/innovation | OpenAlex, HackerNews | — | deep_search |
| operations, strategy, consumer, m&a | — | — | deep_search + FRED where relevant |

---

<a name="p2-d9"></a>
## II.8 — D9 fix: router cross-provider ladders

### II.8.1 Config: tier → ordered candidates

```python
TIER_CANDIDATES = {
    ModelTier.MICRO:    [("cerebras","gpt-oss-120b"), ("groq","gpt-oss-120b"), ("mistral","mistral-small")],
    ModelTier.FAST:     [("groq","gpt-oss-120b"), ("cerebras","gpt-oss-120b"), ("nvidia","nemotron-3-nano-30b-a3b")],
    ModelTier.STANDARD: [("nvidia","llama-3.3-nemotron-super-49b-v1.5"), ("nvidia","nemotron-3-nano-30b-a3b"),
                         ("mistral","mistral-medium-latest"), ("mistral","magistral-small-latest")],
    ModelTier.STRONG:   [("nvidia","nemotron-3-super-120b-a12b"), ("mistral","mistral-large-latest"), ("google","gemini-3-flash")],
    ModelTier.DEEP:     [("google","gemini-3.1-flash-lite"), ("nvidia","nemotron-3-ultra-550b-a55b"),
                         ("nvidia","nemotron-3-super-120b-a12b"), ("google","gemini-3-flash")],
}
TIER_DOWNGRADE = {ModelTier.DEEP: ModelTier.STRONG, ModelTier.STRONG: ModelTier.STANDARD}
```

### II.8.2 Router `complete()` — fallback-first

```python
async def complete(self, tier, messages, agent_name, urgency, **kw) -> RouterResponse:
    tried = []
    ladder = TIER_CANDIDATES[tier][:]
    for provider, model in ladder:
        if self._predicted_rate_limited(provider):
            trace("llm", agent=agent_name, tier=tier.value, provider=provider, status="SKIP", reason="predicted RL")
            continue
        t0 = time.time()
        resp = await self._call(provider, model, messages, **kw)
        dt = int((time.time()-t0)*1000)
        if resp.success:
            trace("llm", agent=agent_name, tier=tier.value, provider=provider, model=model,
                  prompt_tokens=resp.prompt_tokens, completion_tokens=resp.completion_tokens,
                  took_ms=dt, status="OK", fallback_from=(tried[0] if tried else None))
            self._record_usage(provider, resp)
            return resp
        tried.append(provider)
        trace("llm", agent=agent_name, tier=tier.value, provider=provider, model=model,
              took_ms=dt, status="ERR", reason=resp.error[:100])
        if resp.rate_limited:
            self._mark_rate_limited(provider)
    # whole ladder exhausted -> degrade tier
    if tier in TIER_DOWNGRADE:
        trace("llm", agent=agent_name, tier=tier.value, status="DOWNGRADE", reason=f"->{TIER_DOWNGRADE[tier].value}")
        return await self.complete(TIER_DOWNGRADE[tier], messages, agent_name, urgency, **kw)
    return RouterResponse(success=False, error="all providers exhausted")
```

**Acceptance re-stated:** with Gemini DEEP disabled, DEEP returns via NVIDIA in < 30s; trace shows `fallback_from=google` / `DOWNGRADE`.

---

<a name="p2-obs"></a>
## II.9 — Observability wiring (`obs/trace.py`)

```python
import json, time, os, threading
_LOCK = threading.Lock()
_SINKS = []   # callables(event: dict) -> None  (TUI adds one; file sink below)

def add_sink(fn): _SINKS.append(fn)

def trace(stage: str, **fields):
    ev = {"t": round(time.time(), 3), "stage": stage, **fields}
    with _LOCK:
        for fn in _SINKS:
            try: fn(ev)
            except Exception: pass

def file_sink(engagement_id: str):
    path = f"reports/{engagement_id}/trace.jsonl"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    def _w(ev):
        with open(path, "a") as f: f.write(json.dumps(ev) + "\n")
    return _w
```

Orchestrator at engagement start: `trace_add_sink(file_sink(engagement_id)); trace_add_sink(tui_sink)`.
TUI `tui_sink` renders a compact rolling view + a status strip (stage, per-agent finding counts, `SearchBudget.snapshot()`, provider health).

---

## II.10 — Definition of Done (single checklist)

- [ ] SearXNG JSON primary; FlareSolverr demoted; no default google scraping.
- [ ] `normalize_query` + `SearchBudget(60)` + `FlareBreaker` live; < 60 flare hits/run; no `session not created`.
- [ ] Fact-check verifies vs local corpus; ≤ 5 web escalations, clean keyword queries only.
- [ ] Extraction ladder Jina→http→flare→browser→obscura, per-URL parallel, ≥ 500-char floor, Obscura Linux-guarded.
- [ ] `deep_search` returns ≥ 4 sources ≥ 500 chars in < 45s; per-URL trace visible.
- [ ] Bus replays findings; Synthesis receives explicit full findings list; ≥ 5 agents / ≥ 15 sources in report.
- [ ] Specialists never hard-fail; always publish (real or gap) findings; route to structured APIs first.
- [ ] Synthesis ≤ 3 DEEP calls, parallel sections, partial-report guard, dedicated 480s timeout; **never None**.
- [ ] Router cross-provider ladders + DEEP→STRONG degradation; every LLM call traced.
- [ ] Render: Markdown→HTML single sanitized pass; inline CSS; base64 images; no `C:\`; sanitized title.
- [ ] Images cover-cropped to box (no distortion), ≥ 300 DPI, attributed, embedded.
- [ ] Charts: real 300-DPI branded PNGs earned by data; table fallback when thin.
- [ ] Quality floor: below-floor → `DRAFT — INSUFFICIENT DATA` watermark + gap list; above-floor → clean final.
- [ ] `trace.jsonl` + TUI dashboard make every failure self-explaining.
- [ ] T1/T2/T3 acceptance tests pass.

---
---

# PART III — DEEP PER-LEVEL FORENSIC AUDIT (upgrade)

> **Why this part exists.** Part I/II proved *that* the pipeline breaks and gave the rebuild blueprint. Part III goes one level lower and audits **every layer independently** — each LLM tier, each tool, each agent, each sub-agent, and every wire that connects them — against the **actual runtime environment** (verified by running the interpreter and inspecting the installed binaries/packages, not by reading docstrings). It also answers the specific questions raised: *what is the MAIN problem, why are designer agents never used, why do reports look like trash, is extraction broken, is Obscura used properly, how do we make the architecture stealthy, and what else should we add.*
>
> **General-purpose framing (non-negotiable).** HYPERION is a **proprietary engine that must serve many question types and workflows** (go/no-go, comparison, forecast, diagnostic, optimization, M&A, regulatory, sustainability, generic research). Every finding and fix below is written to be **query-agnostic**. The India-AI example is used only as a reproduction case; nothing in the fix set may hard-code a domain, geography, ticker, or sector. Where the current code leaks a specific assumption, that leak is itself flagged as a defect.
>
> **Scope reminder:** Reddit and the academic/semantic-scholar path stay **deliberately excluded**. They are not part of any fix here. OpenAlex is treated as an *optional* keyless structured source, not an academic-search dependency.

---

## III.0 — The MAIN problem, stated once, precisely

You asked whether the main problem is *"not enough context, not enough content, or the search stack not working."* The honest answer, from the evidence:

**It is primarily a CONTENT problem caused by a SEARCH-AND-EXTRACTION-STACK problem, which then cascades into a CONTEXT problem and finally a DELIVERY problem. It is not one bug; it is one *fault line* running through four layers, and every layer fails silently instead of loudly.**

The single causal chain, top to bottom:

```
(1) SEARCH mis-wired        searxng.py ignores SearXNG, routes to FlareSolverr→Google/DDG
        │                    → CAPTCHA/soft-blocks, throttling, a shared Chrome that OOM-crashes
        ▼
(2) EXTRACTION dead on host  Obscura is a *Windows .exe* on a *Linux* host → cannot execute;
        │                    every fetch returns "binary not found", swallowed at debug level
        │                    → sub-agents receive ~0 usable characters
        ▼
(3) CONTENT starvation       specialists analyze near-empty raw_data → emit "gap" findings
        │                    → few real KeyFindings, tiny source counts ("1 unique source")
        ▼
(4) CONTEXT collapse         thin findings + bus-timing loss → Synthesis has almost nothing to
        │                    reconcile; DEEP tier (RPD 500 / RPD 20) starves; 300s deep-dives
        │                    → Synthesis times out → returns None → "did not produce a FinalReport"
        ▼
(5) DELIVERY never runs      orchestrator returns *before Stage 5* when final_report is None
        │                    → Presentation Designer / Data Visualizer / Render Engine NEVER execute
        ▼
(6) EVEN IF it ran           WeasyPrint not installed + no system Chrome → both PDF paths fail;
                             jinja2.Markup import is dead → HTML double-escaped; abs Windows paths
                             → the only artifact is a broken .html with escaped tags and C:\ images
```

So, in one sentence: **the search stack is mis-wired AND the primary extractor physically cannot run on the host, which starves every downstream layer of content; the system then hides each failure, so the symptom you see (garbage report after 15–25 min, "did not produce a FinalReport") is the *last* domino, not the first.**

Everything in Part III is organized to attack this fault line at each level so that (a) content actually arrives, (b) failures are loud, and (c) partial success still produces an honest deliverable.


---

## III.1 — Per-LLM forensic audit (5 providers × 5 tiers)

> **Framing (general-purpose, not example-specific):** HYPERION is a *proprietary orchestration engine*, not a wrapper around one model. The LLM layer is a **5-provider, 5-tier substitution fabric**: any tier request must be satisfiable from *more than one provider* so that a single provider's rate-limit floor never becomes the system's ceiling. Below, each tier is audited against the **config-verified** limits in `hyperion/config.py`. The verdict for each is written as a *routing invariant*, so it holds for any query type or workflow — not for one report.

### III.1.0 How the tiers are supposed to behave

| Tier | Intended role in the DAG | Output budget (`TIER_OUTPUT_BUDGET`) | Who calls it |
|------|--------------------------|--------------------------------------|--------------|
| MICRO | Sub-agent scouting, extraction summarization, cheap classification | 500 | juniors spawned by specialists |
| FAST | Fact-checker, light specialist reasoning, sub-agent analysis | 2 000 | fact_checker, sub_agents |
| STANDARD | Specialist main reasoning, data visualizer, render engine | 4 000 | market/tech/etc. specialists, delivery |
| STRONG | Quality gate, presentation designer, contradiction-heavy specialists | 8 000 | quality_gate, presentation_designer |
| DEEP | Synthesis Lead (whole-report reasoning) | 16 000 | synthesis_lead |

The invariant that must hold: **every tier is reachable from ≥2 providers, and the router's adjacency ladder must let a starved tier borrow a neighbouring tier on a *different* provider.** Today it does not. That is the LLM-layer root cause.

### III.1.1 GOOGLE — the DEEP-tier starvation source

Config-verified models:

| Model | Tier | rpm | tpm | rpd | tpd | Verdict |
|-------|------|-----|-----|-----|-----|---------|
| gemma-4-31b | MICRO | 30 | 16k | 14 400 | — | Healthy for scouting. Fine. |
| gemma-4-26b | MICRO | 30 | 16k | 14 400 | — | Redundant MICRO, fine as backup. |
| gemini-3.1-flash-lite | DEEP | 15 | 250k | **500** | — | **RPD 500 is the softest DEEP floor.** Survivable *only* if DEEP is called ≤ a few times/run. |
| gemini-3.5-flash | DEEP | 15 | 250k | **20** | — | **RPD 20 = effectively unusable** as a workhorse. One heavy run can exhaust it. |
| gemini-3-flash | DEEP | 15 | 250k | **20** | — | Same RPD-20 trap. |

**Problem P-G1 (DEEP daily-cap starvation):** Google supplies most of the DEEP capacity, but two of its three DEEP models are capped at **RPD 20**, and the "good" one at **RPD 500**. Synthesis Lead makes *multiple* DEEP calls per run (`_identify_critical_path` 681, `_draft_recommendation` 764, one `_build_one_section` per section ~920, plus contradiction deep-dives). A single multi-section report can burn 8–15 DEEP calls. Across a day of testing the RPD-20 models die almost immediately, leaving only flash-lite (RPD 500), and when *it* throttles, **the DEEP tier has no non-Google fallback that is genuinely DEEP-class** except NVIDIA ultra-550b (slow, see III.1.2).
**Problem P-G2 (no STANDARD Google model):** Google contributes **nothing** to STANDARD. Every STANDARD request must land on Groq or NVIDIA or Mistral — see III.1.4 for why that's fragile.
**Fix invariant:** DEEP requests must round-robin across `{gemini-3.1-flash-lite, mistral devstral-DEEP, nvidia ultra-550b}` with per-model RPD accounting, and Synthesis must be *capped* at ≤3 DEEP calls/run (see P7). Never let DEEP depend on an RPD-20 model as anything but a last-resort candidate.

### III.1.2 NVIDIA — strong capacity, but the DEEP option is slow

| Model | Tier | rpm | tpm | Verdict |
|-------|------|-----|-----|---------|
| nemotron-3-super-120b | STRONG | 40 | 262k | **Best STRONG workhorse.** High TPM, healthy RPM. |
| llama-3.3-super-49b | STANDARD | ~40 | high | Solid STANDARD backbone. |
| nano-30b | STANDARD | ~40 | high | Good STANDARD backup. |
| ultra-550b | DEEP | 40 | 1M | **Huge TPM but SLOW.** Acceptable as DEEP *fallback*, dangerous as DEEP *primary* under a 300s timeout. |

**Problem P-N1 (DEEP-by-latency timeout):** ultra-550b's 1M TPM makes it look like the ideal DEEP escape hatch, but its wall-clock latency on long synthesis prompts routinely approaches the Synthesis deep-dive timeout (`timeout_seconds=300`, synthesis_lead:620). So the "fallback" that should *save* a starved DEEP request instead *times out* and returns None — feeding the exact "did not produce a FinalReport" symptom.
**Fix invariant:** When DEEP falls back to ultra-550b, the caller must (a) raise the per-call timeout for that candidate specifically, or (b) shard the synthesis prompt so each DEEP call is small enough to finish. NVIDIA STRONG (nemotron) is healthy — lean on it as the *adjacency* target for a starved DEEP (DEEP→STRONG ladder), which the router already permits but under-uses.

### III.1.3 CEREBRAS — the FAST-tier bottleneck

| Model | Tier | rpm | tpm | tpd | Verdict |
|-------|------|-----|-----|-----|---------|
| gpt-oss-120b | FAST | **5** | 30k | 1M | **RPM 5 is the single tightest RPM in the whole fabric.** |
| gemma-4-31b | FAST | **5** | 30k | — | Same RPM-5 wall. |

**Problem P-C1 (FAST RPM-5 convoy):** The fact-checker and every sub-agent lean on FAST. With Cerebras at **RPM 5**, more than 5 FAST calls in any 60s window queue behind the WaitGate. During Wave-0 specialist fan-out (multiple specialists × up to 3 juniors each, all issuing FAST/MICRO calls) the FAST tier is instantly over-subscribed, so the WaitGate inserts long sleeps → this is a major contributor to the "15–25 min then nothing" wall-clock.
**Fix invariant:** FAST must *not* be Cerebras-primary. Route FAST first to Mistral `mistral-small` (FAST, rpm60) and Groq `llama-3.1-8b`-class, and treat Cerebras as an overflow lane only. The router's FAST candidate list must be ordered by *effective throughput* (rpm×concurrency), not by provider name.

### III.1.4 GROQ — high RPM, but a TPM trap on the STANDARD workhorse

| Model | Tier | rpm | tpm | rpd | Verdict |
|-------|------|-----|-----|-----|---------|
| gpt-oss-120b | STANDARD | 30 | **8k** | 1000 | **TPM 8k is too small for STANDARD's 4 000-token outputs + prompt.** |
| llama-3.3-70b | STANDARD | 30 | 12k | — | Slightly better TPM; still tight for long specialist context. |
| llama-3.1-8b | MICRO | high | high | — | Excellent MICRO scout. |

**Problem P-Gq1 (STANDARD TPM undersizing):** A STANDARD specialist call sends a large context (enriched question + gathered findings) and asks for up to 4 000 output tokens. On Groq gpt-oss-120b (**TPM 8k**) a *single* such call can exceed the per-minute token budget, forcing the WaitGate to serialize STANDARD calls one-per-minute. With several specialists running STANDARD, this alone can add many minutes.
**Fix invariant:** STANDARD's primary should be a high-TPM provider (NVIDIA llama-3.3-super-49b / nano-30b, or Mistral mistral-medium). Groq STANDARD is a *burst* lane for short calls, not the backbone. Also: cap specialist output to what the section actually needs (P7) so a STANDARD call rarely approaches 4k tokens.

### III.1.5 MISTRAL — the most balanced provider, currently under-used

| Model | Tier | rpm | tpm | Verdict |
|-------|------|-----|-----|---------|
| mistral-large | STRONG | 60 | 500k | Excellent STRONG alternative to NVIDIA nemotron. |
| magistral-medium | STRONG | ~60 | high | Good STRONG backup. |
| mistral-medium | STANDARD | ~60 | high | **Should be a STANDARD primary** — high RPM+TPM. |
| mistral-small | FAST | 60 | high | **Should be the FAST primary** (fixes the Cerebras RPM-5 convoy). |
| devstral | DEEP | ~ | high | **Non-Google DEEP option** — critical for breaking Google's DEEP monopoly. |
| ministral-3b | MICRO | high | high | Great MICRO scout. |

**Problem P-M1 (idle capacity):** Mistral is the only provider with a *genuine model at every tier* (MICRO→DEEP) at healthy RPM/TPM, yet the current candidate ordering does not prioritize it. It is the natural "shock absorber" for FAST (small), STANDARD (medium), STRONG (large), and — crucially — a **second DEEP source (devstral)** so Synthesis is not hostage to Google's RPD-20/500 caps.
**Fix invariant:** Rebuild every tier's candidate list so Mistral is a *first-class* primary/secondary at FAST/STANDARD/STRONG and a co-primary at DEEP alongside Google flash-lite.

### III.1.6 Cross-tier verdict — the LLM layer's real failure

The models are individually fine. The **fabric wiring** is the defect:

1. **DEEP is a Google monopoly** with RPD-20/500 floors and only a slow NVIDIA escape hatch → Synthesis starves → returns None → designer never runs.
2. **FAST is a Cerebras monopoly** at RPM 5 → Wave-0 convoy → the multi-minute wall.
3. **STANDARD is undersized on Groq TPM** and has **no Google contributor** → specialist serialization.
4. **Mistral, the one balanced provider, is not prioritized** → the fabric's best shock absorber sits idle.

> **LLM-layer invariant to enforce everywhere (P8):** *Every tier must have ≥2 providers in its candidate list ordered by effective throughput; no tier may depend on a single provider whose RPM<10 or RPD<100 as its primary; DEEP must have ≥2 non-Google candidates; and the router's adjacency ladder must allow a starved tier to borrow the neighbouring tier on a different provider before it ever returns None.* This is written as an invariant precisely because HYPERION is proprietary and must hold across **all** query types and workflows, not the one example.


---

## III.2 — Per-tool forensic audit ("are we using each tool PROPERLY?")

> **Scope reminder:** these tools are the VIGIL search/extraction stack plus the data/render tools. HYPERION deliberately **excludes reddit and semantic/academic (semantic_scholar)** — that exclusion is intentional and is NOT a defect; do not "fix" it by re-adding them. The audit below covers every *included* tool, states whether it is wired correctly, and gives a per-tool verdict + fix. Verdicts are general-purpose: they hold for any query/workflow.

### III.2.1 Discovery layer

**SearXNG (`hyperion/tools/*`, called from `sub_agent.py:597`, `fact_checker.py:554`)**
- *Wired?* Yes — it is the primary discovery engine, `num_results=15` for sub-agents, `num_results=5` per fact-check claim.
- *Used properly?* **Partially.** Two defects: (a) fact-checker fans out `sorted_claims[:50] × num_results=5` → up to **250 searches** per run (D3) — abusive and slow; (b) no result-dedup/host-cap → the same domains get hammered.
- *Verdict/fix:* Keep as primary discovery, but (1) budget total searches per run, (2) dedup by registrable domain, (3) fact-checker should verify against the **already-collected corpus first** and only search for the top-N unverified claims.

**Jina discovery (`hyperion/tools/jina.py`, `s.jina.ai`; reader `r.jina.ai`)**
- *Wired?* Yes, and **keyless** — `SEARCH_URL=https://s.jina.ai`, `READER_URL=https://r.jina.ai`. Called at `sub_agent.py:613` (`num_results=10`).
- *Used properly?* **Yes, and under-leveraged.** Jina Reader (`r.jina.ai/<url>`) is a *server-side* clean-text extractor that needs **no browser** — it is the single most valuable tool on a headless Linux host because it sidesteps the entire Chrome dependency. Today it is only a secondary discovery source, not promoted as the primary *extraction* fallback.
- *Verdict/fix:* **Promote Jina Reader to the first extraction fallback** whenever a browser-based extractor is unavailable (which, on the current host, is always — see III.2.2). This one change restores content flow even before Obscura is fixed.

### III.2.2 Extraction layer — the dead center of the system

**Obscura (`hyperion/tools/obscura.py`, `_find_obscura` line 185)**
- *Wired?* Nominally yes — it is the **primary** extractor, invoked with `--stealth` for `fetch`/`scrape`, and it exposes a full CDP-WebSocket client + 12 MCP tools when `obscura serve` is running.
- *Used properly?* **NO — this is the extraction root cause.** `_find_obscura()` checks a configured path, PATH, `obscura.exe` (win32 branch), then `obscura-bin/obscura.exe|obscura`, and **if nothing is found it still returns the string `"obscura"`** and lets the subprocess fail. The repo ships `obscura-bin/obscura.exe` + `obscura-worker.exe` — **Windows binaries** — but the host is **Linux**. So:
  - There is **no platform guard**: on Linux it either tries a non-existent `obscura` on PATH or tries to exec a `.exe`, both fail.
  - The failure returns "binary not found" and is **swallowed at debug level**, so every sub-agent silently gets ~0 characters.
  - The CDP/MCP path requires a long-running `obscura serve` that **nothing starts**.
- *Verdict/fix (P6):* (1) Add a hard **platform guard**: on non-win32, only use Obscura if a genuine native ELF binary is present and executable; otherwise mark the tool `unavailable` and **fail loudly** (WARNING, not debug). (2) When unavailable, the extraction chain must **fall through to Jina Reader → Scrapling(httpx) → Crawl4AI(httpx)** automatically. (3) If the CDP/stealth features are wanted on Linux, either ship a Linux Obscura build or run it via `obscura serve` in a managed process and connect over CDP — never assume the `.exe`.

**Scrapling (`hyperion/tools/scrapling.py`, `MAX_CONTENT_CHARS=15000`)**
- *Wired?* Yes, with an **httpx fallback** if the `scrapling` package is not installed.
- *Used properly?* **Mostly.** The httpx fallback means it *works headless*, which is exactly what the host needs. But it sits *below* Obscura in priority, so when Obscura silently fails the chain does not reliably reach Scrapling.
- *Verdict/fix:* Elevate Scrapling(httpx) in the fallback order (after Jina Reader). Keep `MAX_CONTENT_CHARS` but make it configurable per tier.

**Crawl4AI (`hyperion/tools/crawl4ai.py`)**
- *Wired?* Yes, with an httpx fallback if the package/its Playwright browser is absent.
- *Used properly?* **Only in httpx mode on this host.** Its full power (Playwright rendering) is unavailable because there is no system Chrome. That's acceptable *if* the chain treats it as a text-mode fallback, not a JS-rendering primary.
- *Verdict/fix:* Use Crawl4AI(httpx) as a tertiary text fallback. Do not rely on its browser mode unless a Chromium is actually installed (see III.4 stealth section).

**FlareSolverr (Cloudflare bypass)**
- *Wired?* Referenced as the anti-bot escalation for Cloudflare-protected targets.
- *Used properly?* **No — it requires a running FlareSolverr service** (a separate proxy container/process) that nothing in the sandbox starts, and it itself needs a browser. On the current host it is effectively a no-op.
- *Verdict/fix:* Treat FlareSolverr as **optional and health-checked**: probe its endpoint at startup; if absent, mark unavailable and skip cleanly (never block a fetch waiting on a service that isn't there).

**stealth_search (Playwright-based)**
- *Wired?* Yes, as a stealth discovery/extraction path.
- *Used properly?* **No on this host** — Playwright needs a Chromium that isn't installed. Same class of failure as Crawl4AI browser mode.
- *Verdict/fix:* Gate all Playwright-based tools behind a **one-time browser-availability probe** (III.4). If no Chromium, disable them and rely on the server-side text extractors (Jina Reader / httpx) — do not let them throw mid-run.

### III.2.3 Structured-data tools (the "hard numbers" that make reports credible)

These are keyless/low-friction APIs that return **structured facts** — they are the antidote to "thin content," and they work perfectly on a headless host because they're plain HTTP+JSON.

| Tool | Wired? | Proper use verdict |
|------|--------|--------------------|
| **FRED** (macro/econ series) | Yes | Under-used. Should be a *first-class* evidence source for any economic/market question — deterministic numbers, no scraping. Needs API key handling + graceful skip if key absent. |
| **World Bank** | Yes | Keyless, reliable. Promote for country/development indicators. |
| **SEC / EDGAR** | Yes | Keyless. Excellent for company/financial questions. Ensure UA header + rate etiquette. |
| **OpenAlex** | Yes | Keyless scholarly metadata. **NOTE:** this is *not* the excluded "semantic_scholar" path — OpenAlex is a distinct, allowed metadata source; keep it, it does not violate the reddit/semantic exclusion. |
| **Hacker News (Algolia)** | Yes | Keyless. Good for tech-trend/signal questions. Fine as-is. |
- *Cross-cutting verdict/fix:* These structured tools are the **cheapest, most reliable content source on a headless host** and are currently secondary to fragile scraping. Elevate them: for any query, the specialist should pull the relevant structured sources *first*, then use web extraction to add color. This directly attacks "not enough content."

### III.2.4 Media + render tools

**Unsplash (`hyperion/tools/unsplash.py`, needs access key)**
- *Wired?* Yes; `BASE_URL=https://api.unsplash.com`.
- *Used properly?* **Fragile** — requires an access key; if absent, image lookups fail. The presentation designer then references images that don't exist.
- *Verdict/fix:* Make imagery **optional**: if no key, the designer must omit image slots gracefully (never emit a broken `<img>` or a `C:\...` path). Prefer generated/So charts over stock photos for a data report anyway.

**kaleido (chart export, `charts.py:518`, `pio.write_image(scale=3)`)**
- *Wired?* Yes; `EXPORT_SCALE=3`.
- *Used properly?* **Partially** — kaleido imports OK on the host, but Plotly static export can still pull a Chromium in some configs. `scale=3` at 300 DPI is heavy.
- *Verdict/fix:* Pin kaleido's static engine (no browser), verify export with a startup smoke-test, and drop to `scale=2` if export latency is high. If chart export fails, the designer must **embed a data table instead of a missing image** (never a broken reference).

**WeasyPrint (primary PDF, `render.py:448`) + Playwright Chromium (fallback, `render.py:364`)**
- *Wired?* Yes — WeasyPrint first, Chromium `file://` fallback.
- *Used properly?* **NO on this host — both paths are dead:** `weasyprint` is **not installed** (`ModuleNotFoundError`), and there is **no system Chrome** for the Playwright fallback. So even if a FinalReport existed, PDF rendering would fail and only a broken `.html` could be produced.
- *Verdict/fix (P6):* (1) **Install weasyprint** + its native deps (pango/cairo/gdk-pixbuf) as a hard requirement, and add a startup smoke-test that renders a 1-line PDF. (2) Keep Chromium fallback but only enable it when a browser probe passes. (3) The renderer must never emit an escaped-HTML artifact — see the Jinja2 `Markup` fix in III.6/D-render.

### III.2.5 Tool-layer verdict

- The **only reliably-working extraction path on the current host is server-side text** (Jina Reader + httpx-mode Scrapling/Crawl4AI) and **structured JSON APIs** (FRED/WorldBank/SEC/OpenAlex/HN). Everything browser-based (Obscura.exe, FlareSolverr, stealth_search, Playwright) is **dead** until a real Linux browser stack is provisioned.
- The chain is ordered **browser-first**, so it leads with the dead tools and only *maybe* reaches the working ones — this is the tool-layer expression of the content-starvation root cause.
- **Fix invariant (P6):** Re-order the extraction chain to **[structured APIs] → [Jina Reader] → [httpx Scrapling/Crawl4AI] → [browser tools *only if* a browser probe passes]**, with every tool health-checked at startup and failures logged at WARNING. This makes HYPERION produce real content on a headless host *today*, and automatically upgrades to stealth-browser extraction the moment a Linux browser + native Obscura is installed.


---

## III.3 — Per-agent, per-subagent & connection/wiring audit

> This section walks the DAG **node by node and edge by edge**. For each agent: its tier, what it consumes, what it emits, how it connects to the bus, and its specific defect. The connection audit at the end is the part most systems get wrong — HYPERION's bugs are as much in the *edges* (who hears whom, and when) as in the *nodes*.

### III.3.1 Engagement Director (orchestration root)
- *Tier:* STRONG for planning. *Consumes:* the user question. *Emits:* the DAG + question-type→agent mapping (`QUESTION_TYPE_AGENTS`), waves wired in `_build_dag`.
- *Wiring (verified):* specialists (Wave 0/1) → synthesis (DEEP, deps=all specialists, ~line 832) → fact_checker (FAST) + quality_gate (STRONG, deps synthesis+factcheck) → **presentation_designer (STRONG, deps quality_gate, ~871)** → data_visualizer (STANDARD, deps designer) → render_engine (STANDARD, deps viz+designer).
- *Defect A-ED1:* The DAG makes **delivery strictly downstream of a single point of failure** (synthesis). If synthesis yields None, the entire delivery subtree is unreachable. The director builds a correct *happy-path* DAG but no *degraded-path* — there is no "floor report" node that runs when synthesis fails.
- *Fix:* Director must add a **fallback synthesis contract**: if the DEEP synthesis fails/times out, a STANDARD-tier "floor synthesizer" assembles a minimal FinalReport from whatever findings exist, so the delivery subtree always has an input (ties to P7 + orchestrator fix P6).

### III.3.2 Specialists (Wave 0/1 — market/tech/etc.)
- *Tier:* STANDARD main reasoning (e.g. `market_analyst.py:78`); spawn sub-agents at MICRO/MICRO/FAST (`1065–1086`), gathered in parallel (`1097`).
- *Consumes:* enriched question (`base._enrich_context:151` regex-extracts geography/industry/tech/company). *Emits:* `KeyFinding`s onto the FINDINGS channel.
- *Defect A-SP1 (content):* Because extraction is dead (III.2.2), specialists' sub-agents return near-empty `raw_data`, so specialists emit **"gap" findings** ("insufficient data on X") instead of substance. This is *not* a reasoning bug — it's starvation propagating upward.
- *Defect A-SP2 (context enrichment is regex-only):* `_enrich_context` uses regex to pull entities from the question. For an arbitrary proprietary query this is brittle — it can mis-tag or miss the domain, sending sub-agents to search the wrong things. General-purpose fix: replace/augment regex enrichment with a single **MICRO-tier classification call** that returns structured `{geographies, industries, entities, timeframe, intent}` for *any* query type.
- *Fix:* (1) fix extraction (P6) so `raw_data` is non-empty; (2) upgrade enrichment (P7); (3) require each specialist to pull the relevant **structured-data tools first** (III.2.3) so it always has *some* hard numbers even if scraping is thin.

### III.3.3 Sub-agents (juniors)
- *Wiring (verified):* `sub_agent._gather_raw_data` calls `searxng.search(num_results=15)` (`597`) + `jina.search(num_results=10)` (`613`); `_analyze_and_produce_findings` runs at `self.spec.model_tier` (`650`).
- *Defect A-SU1 (isolation OK, input empty):* Context isolation is correct (juniors are MICRO/FAST, capped ≤3 per specialist — good design). But the junior's whole value is extracting text from discovered URLs, and extraction is dead → the junior LLM call summarizes nothing.
- *Defect A-SU2 (no extraction step wired to the working tools):* The junior discovers URLs but the *fetch* of each URL goes through the browser-first chain (dead), not Jina Reader. So discovery succeeds and extraction fails silently.
- *Fix:* Point the junior's per-URL extraction at the **re-ordered chain from III.2.5** (Jina Reader first). This is the single highest-leverage content fix — it turns thousands of discovered URLs into actual text.

### III.3.4 Synthesis Lead (DEEP — the report author)
- *Tier:* DEEP (`synthesis_lead.py:93`), `max_sub_agents=1` (`185`). *Consumes:* `self._collected_findings` injected by orchestrator (`orchestrator.py:409`). *Emits:* `FinalReport`.
- *LLM calls:* `_identify_critical_path` (681), `_draft_recommendation` (764), one `_build_one_section` per section (920; parallel-gathered 946), contradiction `_deep_dive_contradiction` timeout 300s (620).
- *Defect A-SY1 (too many DEEP calls on a starved tier):* multiple DEEP calls × Google RPD-20/500 (III.1.1) + slow NVIDIA fallback (III.1.2) → WaitGate stalls or timeouts → returns None.
- *Defect A-SY2 (all-or-nothing):* if any critical DEEP call fails, the whole method returns None instead of a partial report. There is no "assemble what we have" path.
- *Fix (P7):* (1) cap Synthesis at **≤3 DEEP calls/run**; batch sections into one call where possible; (2) shard long prompts so ultra-550b can finish inside timeout; (3) **never return None** — on partial failure, emit a FinalReport marked `degraded=True` with the sections that succeeded. This alone guarantees the delivery subtree gets an input.

### III.3.5 Fact Checker (FAST)
- *Wiring (verified):* `_verify_claims` sorts and takes `sorted_claims[:50]` (`767`); each claim → `searxng.search(num_results=5)` (`554`) ⇒ up to **250 searches** (D3).
- *Defect A-FC1:* the search storm (a) is slow, (b) hammers SearXNG, (c) needs extraction (dead) to actually verify. So it spends minutes and verifies little.
- *Fix:* verify against the **already-collected corpus** first; only search the top-N (e.g. 10) highest-impact unverified claims; share the global search budget with discovery.

### III.3.6 Quality Gate (STRONG — iteration loop)
- *Tier:* STRONG, deps = synthesis + fact_checker. *Emits:* `QualityScore`; may loop back for another synthesis pass.
- *Defect A-QG1 (loop amplifies starvation):* each iteration re-invokes DEEP synthesis → multiplies the RPD burn in III.1.1. A gate that keeps failing quality (because content is thin) can loop until DEEP is exhausted.
- *Fix:* cap iterations (e.g. ≤2); make the gate **content-aware** — if source counts are below a floor, it should *stop looping and flag degraded* rather than demanding a rewrite the content can't support.

### III.3.7 Delivery trio (Presentation Designer STRONG → Data Visualizer STANDARD → Render Engine STANDARD)
- *Wiring (verified):* excluded from `_execute_dag` (`orchestrator.py:596`) and run in a separate **Stage 5 delivery loop** (`945–968`) — but only if `final_report` is truthy (see III.5).
- *Defect A-DL1 (never reached):* covered fully in III.5.
- *Defect A-DL2 (designer emits machine paths):* `presentation_designer._render_html_template` (`1136`) converts image paths to `os.path.abspath()` (`1159–1191`) → `C:\Users\...` absolute paths that break in any PDF/HTML; unsanitized `<title>` (`334`); relies on `md_to_html` (`378`) which is double-escaping (III.6/D-render).
- *Fix:* base64-inline or use relative asset paths; sanitize title; fix the Markdown filter; degrade image slots gracefully when imagery is unavailable.

### III.3.8 Connection / wiring audit (the edges)

This is where the *silent* failures live. Each edge below is a place where a message can be produced but never consumed, or consumed before produced.

1. **Bus subscription gap (DELIVERY hears too little):** `base.subscribe_to_bus` has the **DELIVERY** role subscribe to `{FINDINGS}` only. But the designer needs the *FinalReport* (a HANDOFF), and the visualizer needs chart specs. If those arrive on channels DELIVERY doesn't subscribe to, they're missed. **Fix:** DELIVERY must subscribe to `{FINDINGS, HANDOFF}` at minimum.
2. **Findings-injection race (Synthesis):** orchestrator injects `agent._collected_findings = list(self._all_findings)` (`409`) at spawn time. Any finding published *after* injection but *before* synthesis reads is lost. **Fix:** synthesis should read findings at *execution* time (pull from bus/store), not rely solely on a snapshot injected at construction.
3. **HANDOFF timing (Synthesis→QualityGate→Designer):** because delivery is a separate Stage 5 loop rather than DAG edges, the HANDOFF of the FinalReport is implicit (via `final_report` variable), not a bus message. That's why a None synthesis silently strands delivery. **Fix:** make the FinalReport an explicit HANDOFF *and* a guaranteed variable (floor report), so both the bus path and the direct path always carry an input.
4. **Sub-agent → specialist gather:** parallel gather (`market_analyst:1097`, `synthesis:946`) is correct, but a single sub-agent exception can reject the whole `gather` if not wrapped. **Fix:** gather with `return_exceptions=True` and treat a failed junior as an empty-finding, never as a run-killer.
5. **ESCALATION channel underused:** when a tool is unavailable (Obscura dead), nothing escalates; the failure is swallowed at debug. **Fix:** unavailable-tool and starved-tier conditions must publish to ESCALATION so the director/quality-gate can adapt (e.g. widen the extraction fallback, lower the quality floor) instead of silently degrading.

> **Wiring invariant (P9):** every producer→consumer edge must be **either** an explicit bus message on a channel the consumer subscribes to, **or** a guaranteed variable with a floor value — never an implicit "hope it's there" that turns into a silent stall. And every parallel gather must isolate child failures.


---

## III.4 — How to make the WHOLE architecture stealthy (dedicated section)

> The user explicitly asked: *"how to make the whole architecture stealthy?"* Stealth here means **HYPERION's outbound web activity is indistinguishable from ordinary human browsing**, so free/keyless sources don't rate-limit, CAPTCHA, or block it — which directly protects the content pipeline. Stealth is a *pipeline-wide* property, not a single tool flag. This section is general-purpose: it applies to any query/workflow.

### III.4.0 The stealth threat model (what actually blocks a zero-cost scraper)
1. **TLS/HTTP fingerprint** — plain `httpx`/`requests` have a JA3/JA4 signature and header order that scream "bot."
2. **Missing browser signals** — no JS execution, no `navigator.*`, no canvas/WebGL, no realistic timing.
3. **IP reputation & rate** — too many requests from one IP in a short window → throttle/block.
4. **Behavioural** — identical intervals, no jitter, hitting the same host in a tight loop.
5. **Cloudflare/Turnstile/PerimeterX** — active challenges that need a real browser (this is what FlareSolverr/Obscura are *for*).

HYPERION's current posture fails 1–5 simultaneously: on the host the only working path is bare httpx (fingerprintable), the stealth tools (Obscura/FlareSolverr/stealth_search) are all dead, and there is no jitter/host-cap/rotation layer.

### III.4.1 Layer 1 — Transport stealth (works today, headless)
- **Impersonate a real browser's TLS+HTTP fingerprint** for all httpx-mode fetches: use `curl_cffi` (JA3/JA4 impersonation of Chrome/Safari) or an equivalent, instead of bare httpx. This alone defeats a large class of naïve bot filters with **zero browser dependency**.
- **Realistic header sets**: full, ordered Chrome header profiles (Accept, Accept-Language, Sec-CH-UA, Sec-Fetch-*, Referer), rotated per session, matched to the impersonated browser.
- **Prefer server-side clean-text endpoints** that are *designed* to be hit programmatically: **Jina Reader (`r.jina.ai`)** and the structured JSON APIs (III.2.3). These are the stealthiest of all because they expect automated access — no fingerprint problem exists.

### III.4.2 Layer 2 — Behavioural stealth (works today)
- **Per-host concurrency cap + politeness delay** with **randomized jitter** (e.g. 1–4s, non-uniform) between requests to the same registrable domain.
- **Global + per-host token buckets** so a single site never sees a burst. This also fixes the SearXNG hammering in III.2.1/III.3.5.
- **Request-order randomization**: shuffle the URL fetch order so the traffic pattern isn't a predictable crawl.
- **Honour robots and back-off on 429/403** with exponential backoff + rotation, instead of retrying blindly.

### III.4.3 Layer 3 — Identity/route stealth (optional, config-gated)
- **Proxy rotation** (residential/datacenter pool) behind a single config switch; disabled by default (zero-cost), enabled when the user supplies a pool. Rotate per-session or per-N-requests.
- **User-Agent + viewport + locale rotation** consistent *within* a session (don't change UA mid-session — that itself is a tell).
- **Cookie/session persistence** per host so repeat visits look like a returning user, not a fresh bot each time.

### III.4.4 Layer 4 — Browser stealth (only once a Linux browser exists)
- Provision a **real Linux Chromium** (Playwright/`playwright install chromium` + system deps), then:
  - Enable **Obscura's `--stealth`** path *with a native Linux binary* (fixes III.2.2) for fingerprint randomization (canvas/WebGL/navigator patching).
  - Bring up **FlareSolverr as a managed service** (health-checked, III.2.2) for Cloudflare/Turnstile challenges only — not for every fetch.
  - Use **stealth_search** for the handful of targets that truly require JS rendering.
- **Escalation ladder (cheap→expensive):** structured API → Jina Reader → curl_cffi httpx → Scrapling/Crawl4AI httpx → *(browser)* stealth Chromium → FlareSolverr. Only climb when the current rung is blocked. Most requests should resolve at the cheap rungs.

### III.4.5 Stealth invariants (P8/P6)
1. **No bare-fingerprint HTTP:** every outbound fetch uses an impersonated TLS+header profile.
2. **No bursts:** per-host token bucket + jitter on every path, discovery *and* extraction *and* fact-check.
3. **Cheap-first escalation:** never open a browser when Jina Reader or a JSON API would answer.
4. **Loud health, quiet traffic:** stealth *failures* (blocked, challenged) escalate on the ESCALATION channel; stealth *traffic* stays low-and-slow.
5. **Config-gated identity:** proxy/UA rotation is a switch, off by default (preserves zero-cost), on when credentials exist.

> **Net effect:** even *before* a browser is installed, Layers 1–2 make HYPERION's headless traffic look like a polite human using clean-text endpoints — which is enough to unblock the free sources it depends on. Layers 3–4 are the upgrade path for hard targets, gated so they never break the zero-cost, headless default.


---

## III.5 — Definitive answer: why the designer agents are NEVER used

> The user's sharpest question. Here is the exact, code-verified causal chain, with the single line that kills delivery.

### III.5.1 The DAG says delivery *should* run
The Engagement Director wires the delivery trio strictly downstream of synthesis:
```
… specialists → synthesis_lead (DEEP)
                     │
        ┌────────────┴───────────┐
   fact_checker (FAST)      quality_gate (STRONG)
                     │
          presentation_designer (STRONG)   ← delivery starts here
                     │
             data_visualizer (STANDARD)
                     │
              render_engine (STANDARD)  → PDF
```
So *by design* the designer is reachable. The bug is in **execution**, not the plan.

### III.5.2 Delivery is executed in a SEPARATE stage — behind a guard
The orchestrator **excludes** the three delivery agents from the normal DAG executor:
```python
# orchestrator.py:596  (conceptual)
_DELIVERY_AGENTS = {PRESENTATION_DESIGNER, DATA_VISUALIZER, RENDER_ENGINE}
# these are skipped inside _execute_dag …
```
…and runs them later in a dedicated **Stage 5 delivery loop** (`orchestrator.py:945–968`, the `delivery_tasks` loop). That loop is what actually instantiates and runs the Presentation Designer, Data Visualizer, and Render Engine.

### III.5.3 The exact killer: an early `return` before Stage 5
Immediately **before** the Stage 5 delivery block, the orchestrator does:
```python
# orchestrator.py:919–922
if not final_report:
    result.error = "Synthesis Lead did not produce a FinalReport"
    result.duration_seconds = time.time() - self._start_time
    return result          # ← RETURNS HERE — Stage 5 (lines 945–985) is never reached
```
**That is the whole answer.** Because Synthesis Lead returns `None` on this host (DEEP-tier starvation + 300s deep-dive timeouts + all-or-nothing assembly — III.1.1/III.1.2/III.3.4), `final_report` is falsy, the orchestrator returns at line ~919, and **control never reaches the delivery loop at line ~945.** The Presentation Designer, Data Visualizer, and Render Engine are therefore *never constructed and never called* — not because they're broken, but because the function exits one stage too early.

### III.5.4 Why it *looks* like the designer is "broken"
The user sees: long run → "did not produce a FinalReport" → at best a stray broken `.html`. That broken HTML is not from the designer at all — it's from earlier/other render attempts (with the double-escape + `C:\` path bugs of III.6). So the designer's *absence* and the render layer's *escaping bug* compound into "the designer produces trash," when in reality the designer **never executed**.

### III.5.5 The fix (P6 + P7), stated precisely
1. **Never let synthesis return None** (III.3.4 / P7): on partial/timeout, emit a `FinalReport(degraded=True)` from whatever findings exist. This makes `final_report` truthy in the normal case.
2. **Remove the hard early-return as a dead end** (P6): replace the `if not final_report: return` with a **floor-report fallback** — synthesize a minimal FinalReport from `self._all_findings` (even if it's thin) so Stage 5 *always* runs. Only truly catastrophic failures (zero findings *and* zero fallback) should short-circuit, and even then it should emit a diagnostic stub PDF, not silently return.
3. **Guarantee Stage 5 executes** (P6/P9): move delivery so it runs on *any* terminal state that has a report object (full or degraded), and make the FinalReport an explicit HANDOFF (III.3.8 edge #3) so delivery is driven by a message, not a fragile local variable.
4. **Make delivery itself robust** (P6): once it runs, fix the designer's `os.path.abspath`/title/`md_to_html` issues (III.3.7 / III.6) and the missing WeasyPrint/browser (III.2.4) so the artifact is a real PDF, not escaped HTML.

> **One-line verdict:** *The designer never runs because `orchestrator.py:~919` returns the moment Synthesis yields None, one stage before the delivery loop at `~945`. Fix Synthesis to always yield a (possibly degraded) report and convert the early-return into a floor-report fallback, and the designer runs every time.*


---

## III.6 — New defects discovered in this deep audit (D13–D22)

These extend the D1–D12 catalogue from Part I with the runtime-verified findings of Part III. Each has a severity, the evidence, and the owning phase.

| ID | Severity | Defect | Evidence (verified) | Owning phase |
|----|----------|--------|---------------------|--------------|
| **D13** | 🔴 Blocker | **Designer never runs**: orchestrator early-returns before Stage 5 when synthesis is None | `orchestrator.py:919–922` return vs delivery loop `945–968` | P6 |
| **D14** | 🔴 Blocker | **Obscura is a Windows `.exe` on a Linux host, no platform guard** → primary extractor 100% dead, failure swallowed at debug | `obscura.py:_find_obscura:185`; `obscura-bin/obscura.exe`,`obscura-worker.exe` present; host is Linux | P6 |
| **D15** | 🔴 Blocker | **PDF impossible on host**: `weasyprint` not installed AND no system Chrome for Playwright fallback | `ModuleNotFoundError: weasyprint`; "NO system chrome" | P6 |
| **D16** | 🔴 Blocker | **HTML double-escape**: `from jinja2 import Markup` is dead in Jinja2 3.x → `Markup=str` → `select_autoescape` escapes the HTML → `&lt;p&gt;` output | `render.py:_markdown_to_html:166`, import `:178`; jinja2 3.1.6 `ImportError` confirmed | P6 |
| **D17** | 🟠 High | **Designer emits machine-absolute image paths** (`C:\Users\...`) that break every artifact | `presentation_designer.py:1159–1191` `os.path.abspath` | P6 |
| **D18** | 🟠 High | **Markdown exporter reads the wrong schema keys** vs `FinalReport` → near-empty markdown | `markdown.py` `report.get("title")`/`summary.get("key_findings")`/`methodology.get("agents_used")` vs actual `question`/`executive_summary`/`sections`/`agents_used` | P7 |
| **D19** | 🟠 High | **DEEP tier is a Google monopoly** with RPD-20/500 floors + only a slow NVIDIA escape → synthesis starves/timeouts | `config.py` gemini flash rpd20, flash-lite rpd500; ultra-550b slow | P8 |
| **D20** | 🟠 High | **FAST tier bottlenecked by Cerebras RPM-5** → Wave-0 convoy / multi-minute wall | `config.py` cerebras gpt-oss-120b rpm5, gemma-4-31b rpm5 | P8 |
| **D21** | 🟡 Med | **STANDARD undersized on Groq TPM-8k + no Google STANDARD model** → specialist serialization | `config.py` groq gpt-oss-120b tpm8k; no Google STANDARD entry | P8 |
| **D22** | 🟡 Med | **No transport/behavioural stealth** on the working (httpx) path; browser stealth tools all dead | bare httpx fingerprint; Obscura/FlareSolverr/stealth_search unavailable on host | P6/P8 |

Supporting (already noted, reaffirmed): **D3** fact-checker 250-search storm (`fact_checker.py:767 × 554`); **D-charts** `pio.write_image(scale=3)` kaleido/Chrome coupling (`charts.py:518`); **DELIVERY bus subscription** only `{FINDINGS}` (III.3.8 #1); **findings-injection race** (`orchestrator.py:409`, III.3.8 #2).

---

## III.7 — Phase-wise fix plan (P6–P9) — extends Part I's P1–P5

> Ordered by *unblock-first*: get real content flowing and a real PDF out (P6), make the pipeline honest and never-None (P7), fix the LLM fabric (P8), then harden the wiring (P9). Each phase lists exit criteria so completion is testable for **any** query type, not just one example.

### P6 — UNBLOCK: content flows + a real PDF always comes out (🔴 do first)
**Goal:** on the current headless Linux host, an arbitrary query produces a real, non-escaped PDF with actual content.
1. **Extraction chain re-order (D14):** platform-guard Obscura (disable on Linux unless native ELF present, log WARNING); make extraction chain **structured-API → Jina Reader → curl_cffi httpx Scrapling/Crawl4AI → browser (only if probe passes)**; wire juniors' per-URL fetch to it (III.3.3).
2. **Render fixes (D15/D16/D17):** `pip install weasyprint` + native deps (pango/cairo/gdk-pixbuf) + startup smoke-test; change `from jinja2 import Markup` → `from markupsafe import Markup`; replace `os.path.abspath` image paths with base64-inline or relative assets; sanitize `<title>`.
3. **Designer always runs (D13):** convert `orchestrator.py:919` early-return into a **floor-report fallback**; guarantee Stage 5 runs whenever a report object exists.
4. **Stealth Layer 1–2 (D22):** curl_cffi impersonation + per-host token bucket + jitter on all fetch paths.
**Exit criteria:** run 3 *different* query types headless → each yields a PDF with real prose, real numbers from ≥1 structured source, no `&lt;` escapes, no `C:\` paths, and the designer log shows it executed.

### P7 — HONEST + NEVER-NONE synthesis & extraction quality
**Goal:** the pipeline degrades gracefully and reports the truth about its own coverage.
1. **Synthesis never returns None (D13 root):** ≤3 DEEP calls/run, batch sections, shard long prompts for slow candidates, emit `FinalReport(degraded=True)` on partial failure.
2. **Content-aware quality gate (A-QG1):** cap iterations ≤2; if source counts below floor, flag degraded instead of re-looping DEEP.
3. **Enrichment upgrade (A-SP2):** replace regex `_enrich_context` with a MICRO classification call returning structured intent for any query.
4. **Markdown exporter schema fix (D18):** align keys to `FinalReport`.
5. **Fact-checker budget (D3):** verify against local corpus first, cap to top-N searches, share global search budget.
**Exit criteria:** force-fail the DEEP tier → run still emits a `degraded` PDF; markdown export is non-empty and matches the PDF; fact-checker issues ≤ budgeted searches.

### P8 — LLM FABRIC: multi-provider tiers + stealth identity
**Goal:** no tier depends on a single provider's floor; DEEP has ≥2 non-Google sources.
1. **Rebuild tier candidate lists (D19/D20/D21):** FAST primary = Mistral small (+Groq 8b), Cerebras overflow only; STANDARD primary = NVIDIA super-49b/nano-30b or Mistral medium, Groq burst-only; DEEP = {Google flash-lite, Mistral devstral, NVIDIA ultra-550b} round-robin with per-model RPD accounting; STRONG = NVIDIA nemotron / Mistral large.
2. **Router adjacency ladders:** allow starved tier to borrow neighbouring tier on a *different* provider before returning None; per-candidate timeout override for slow DEEP (ultra-550b).
3. **Output-budget right-sizing:** cap outputs to section need so STANDARD rarely nears Groq TPM.
4. **Stealth Layer 3 (config-gated):** proxy/UA rotation switch, off by default.
**Exit criteria:** kill any one provider's key → every tier still resolves from another provider; DEEP resolves with Google keys removed.

### P9 — WIRING: explicit edges, isolated failures, loud health
**Goal:** no silent stalls; every producer→consumer edge is guaranteed.
1. **Bus subscriptions (III.3.8 #1):** DELIVERY subscribes `{FINDINGS, HANDOFF}`.
2. **Findings read-at-execution (III.3.8 #2):** synthesis pulls findings at run time, not construction snapshot.
3. **Explicit FinalReport HANDOFF (III.3.8 #3):** delivery driven by message + guaranteed floor variable.
4. **Isolated gathers (III.3.8 #4):** all `asyncio.gather(..., return_exceptions=True)`; failed child = empty finding.
5. **ESCALATION on degradation (III.3.8 #5):** unavailable tool / starved tier / blocked host publish to ESCALATION; startup health-report of every tool + tier.
**Exit criteria:** inject a failing sub-agent and an unavailable tool → run completes with a degraded PDF, an ESCALATION log entry, and a startup health table listing each tool/tier as available/unavailable.

---

### III.8 — Closing invariants (why this holds for a PROPRIETARY multi-workflow system)

Every fix above is stated as an **invariant** (a property that must hold for *all* queries) rather than a patch for one example, because HYPERION is a proprietary engine serving many query types and workflows:
- **Content invariant:** every run reaches ≥1 working extraction path (structured API or Jina Reader) regardless of host/browser state.
- **Delivery invariant:** every run that has *any* findings produces a PDF; synthesis never returns None; the designer always executes.
- **Fabric invariant:** every tier resolves from ≥2 providers; DEEP has ≥2 non-Google sources.
- **Stealth invariant:** every outbound request is fingerprint-impersonated, rate-jittered, and cheap-first-escalated.
- **Wiring invariant:** every edge is an explicit message or a floored variable; every gather isolates failures; every degradation is loud.
- **Exclusion honoured:** reddit and semantic/academic (semantic_scholar) remain deliberately out; OpenAlex (distinct, allowed) stays in.

*End of PART III.*


---
---

# PART IV — IS THIS BEST-IN-CLASS? (production-grade gap analysis + SOTA upgrade)

> **The user's question, verbatim:** *"is this the best most robust proprietary production-grade architecture? or we can improve it? better tools? more robust stealth system?"*
>
> **Honest one-line answer:** Parts I–III turn HYPERION from *broken* into *working and robust*. They do **not** yet make it *best-in-class*. There is a real, evidence-backed gap between "does not break" and "state-of-the-art production-grade," and this part closes it. Every recommendation below is grounded in 2025–2026 SOTA practice and a live anti-bot benchmark, not opinion — and every recommendation is a general-purpose invariant for a proprietary multi-workflow engine, not a patch for one query. reddit and semantic/academic remain deliberately excluded.

## IV.0 — Honest verdict: where HYPERION sits on the maturity curve

Scored against how a genuinely production-grade research-automation platform is built in 2026:

| Capability axis | Parts I–III target | Best-in-class (2026) | Gap |
|---|---|---|---|
| **Orchestration durability** | In-memory DAG + "floor report" fallback | **Durable execution**: event-history persistence, crash-resume, replay (Temporal-style) | 🔴 Large — a process crash still loses the whole run |
| **Failure model** | Graceful degradation, never-None synthesis | Durable + **idempotent step retries** that resume from last success, not restart | 🟠 Medium |
| **Extraction** | Jina Reader + curl_cffi httpx + Obscura(fixed) | **Layered by detection surface**: nodriver (CDP layer) / Camoufox (TLS) / curl_cffi (HTTP) / Trafilatura (parse) | 🟠 Medium — wrong primary browser tool named |
| **Stealth** | 4 conceptual layers (transport/behaviour/identity/browser) | **Shape-coherence** discipline + automation-protocol-fingerprint defeat (direct-CDP) | 🔴 Large — named tools (FlareSolverr/Obscura) don't defeat the layer that actually blocks you |
| **LLM fabric** | Multi-provider tiers + adjacency ladders | Same + **semantic caching**, **speculative/parallel provider racing**, **structured-output validation loop** | 🟡 Small–Medium |
| **Quality assurance** | LLM quality-gate + fact-checker | **Offline eval harness** (golden-set scoring, regression gates in CI) | 🔴 Large — no measurable, repeatable quality metric exists |
| **Observability** | Structured logs + trace ids | Logs + **metrics + distributed traces + per-run cost/latency ledger + replay** | 🟠 Medium |
| **Cost/rate governance** | WaitGate + DailyBudgetPlanner | Same + **cache-hit-first** + **provider cost-per-token routing** | 🟡 Small |
| **Reproducibility** | None (non-deterministic run) | **Seeded, replayable runs** with pinned prompts/versions | 🟠 Medium |

**Verdict:** After Parts I–III, HYPERION would be roughly a **solid Level-2 (“reliable”)** system on a 4-level maturity scale. Best-in-class is **Level-4 (“durable, measurable, self-improving”)**. The three biggest missing pillars are: **(1) durable execution**, **(2) an offline evaluation harness**, and **(3) a stealth stack built around shape-coherence and the CDP/automation-protocol layer** — not the tools currently named.

### IV.0.1 What Parts I–III got RIGHT (keep these)
- The **root-cause diagnosis** is correct and code-verified — do not re-litigate it.
- **curl_cffi for transport stealth** is validated by benchmark (see IV.3): a 21-line wrapper matched a 130 MB patched Chromium fork on 26/31 real targets. Keep it.
- **Jina Reader + structured JSON APIs first** is the correct headless-host strategy. Keep it.
- **Never-None synthesis + guaranteed delivery** is the right *reliability floor*. Keep it — durable execution sits *above* it, not instead of it.
- **Multi-provider tier fabric with adjacency ladders** is the right shape. Keep it; add caching + racing.

### IV.0.2 What Parts I–III got INCOMPLETE (this part fixes)
1. **Named the wrong stealth primaries.** FlareSolverr / Obscura / stealth_search do **not** defeat *automation-protocol fingerprinting* (the CDP-handshake layer), which is the layer that actually hard-blocks a headless scraper on Cloudflare Turnstile / DataDome. IV.3 replaces them with the benchmark-winning approach.
2. **No durability.** A crash, OOM, or sandbox reset loses the entire multi-minute run. IV.1 adds checkpoint/resume.
3. **No measurable quality.** "McKinsey-grade" is asserted, never measured. IV.1 adds an offline eval harness with a golden set and CI regression gates.
4. **No reproducibility/replay.** IV.1 adds seeded, replayable runs.
5. **Under-specified extraction parsing.** "Get the text" is not the same as *clean, boilerplate-stripped, main-content* text. IV.2 adds Trafilatura/readability as the parse layer.


---

## IV.1 — Architecture upgrades (from "reliable DAG" to "durable, measurable engine")

### IV.1.1 Durable execution — the single biggest architecture upgrade
**Problem it solves:** today (and even after Parts I–III) a run lives entirely in one process's memory. A crash, OOM, sandbox reset, or a 25-minute run that dies at minute 24 loses *everything* and starts from zero. That is the defining trait of a *non-production* long-running system.

**SOTA pattern (2026):** *durable execution* — persist an **event history** of every completed step; on restart, **replay** the history to reconstruct state and **resume from the last completed step** instead of restarting. This is the model behind Temporal/Inngest/DBOS. It is strictly stronger than framework "checkpoints" (LangGraph/CrewAI-style), which snapshot state but do not guarantee exactly-once, resumable step execution across crashes.

**How HYPERION should adopt it (proportionate, zero-cost-friendly):**
- Do **not** require a Temporal cluster. Implement a **lightweight durable layer**: a `RunJournal` (append-only JSONL/SQLite per `run_id`) that records each agent/step as `{step_id, inputs_hash, status, output_ref, ts}`.
- Every DAG node becomes an **idempotent step**: before executing, check the journal — if this `step_id`+`inputs_hash` already succeeded, **load the cached output** instead of re-running (this also cuts LLM spend and rate-limit pressure).
- On restart with the same `run_id`, the orchestrator **replays the journal** and continues from the frontier of completed steps.
- Findings, section drafts, chart specs, and the FinalReport are written to a **content-addressed store** (`artifacts/<run_id>/<step_id>.json`) so replay is cheap and outputs survive a crash.
- **Result:** a crash at minute 24 resumes at minute 24. Extraction results, specialist findings, and completed sections are never recomputed. This is the jump from Level-2 to Level-3.

### IV.1.2 Blackboard + explicit HANDOFF (supersedes the fragile local-variable handoff)
Part III (III.3.8) already flagged that the FinalReport travels as a local variable. The SOTA form is a **blackboard architecture**: a single shared, versioned run-state store that every agent reads/writes, with explicit typed HANDOFF messages on the bus. This makes the delivery trio driven by *state presence* ("a FinalReport artifact exists for this run") rather than control-flow reaching a specific line — which is exactly the class of bug that made the designer never run. Combine with the durable journal: the blackboard *is* the content-addressed store.

### IV.1.3 Offline evaluation harness — make "McKinsey-grade" measurable
**Problem:** quality is currently asserted by an LLM quality-gate at *runtime* with no repeatable, offline metric. You cannot improve what you cannot measure, and you cannot detect a regression when you change a prompt/model.
**SOTA pattern:** an **eval harness** run in CI, separate from production:
- A **golden set** of representative queries spanning *all* workflow types HYPERION serves (proprietary/general-purpose — not one example).
- **Deterministic checks** per report: has ≥N sections, ≥M cited sources, every KeyFinding has a source, no empty sections, no template artifacts (`&lt;`, `C:\`, unrendered `{{ }}`), PDF renders, charts present.
- **LLM-as-judge rubric** (scored 1–5) on: evidence density, analytical depth, structure, actionability — with the *same* rubric the runtime quality-gate uses, so runtime and offline agree.
- **Regression gate:** CI fails if golden-set mean score drops > threshold vs the last release. This is what actually holds a quality bar over time.

### IV.1.4 Reproducibility & replay
- Pin per-run: prompt template versions, model IDs, tool versions, and a **seed** for any stochastic choice (provider ordering, sampling).
- Persist the full `run_manifest.json` (question, config snapshot, seed, model matrix hash).
- **Replay mode:** re-run a `run_id` against its journaled inputs to reproduce a report bit-for-bit (LLM nondeterminism aside) — essential for debugging "why did *this* report look thin?" long after the fact.

### IV.1.5 LLM-fabric refinements (on top of Part III P8)
- **Semantic + exact response cache** keyed on `(tier, normalized_prompt_hash)`: identical or near-identical sub-agent prompts (common across specialists) hit the cache → fewer calls, less rate-limit pressure, faster runs. Pairs perfectly with the durable step cache in IV.1.1.
- **Speculative provider racing for critical-path DEEP calls:** fire the DEEP synthesis call at the *two* fastest healthy DEEP providers simultaneously, take the first valid structured response, cancel the other. Trades a little quota for large latency/reliability wins on the exact call that most often times out.
- **Structured-output validation loop:** every `_llm_complete_structured` call validates against the Pydantic schema and, on failure, does one bounded "repair" re-prompt before falling back — instead of discarding the whole result.

### IV.1.6 Observability upgrade (on top of II.9)
Add to structured logs: **metrics** (per-tier call counts, cache-hit rate, per-tool success rate, per-provider latency), **distributed trace** spanning the whole DAG under one `run_id`, and a **per-run ledger** (tokens, est. cost, wall-clock per stage) written to the manifest. A one-screen **run health table** at completion: each tool available/unavailable, each tier's calls/limit, degraded? yes/no.


---

## IV.2 — Better tools (evidence-backed replacements & additions)

> The guiding principle: **match the tool to the detection surface / job**, and prefer tools that work *headless on Linux with zero cost*. Additions are marked ➕; replacements/repositions ♻.

### IV.2.1 Extraction / parsing (the content pipeline)
- ➕ **Trafilatura** (or `readability-lxml` / `resiliparse`) as the **main-content parse layer**. Getting bytes ≠ getting the article. Trafilatura strips nav/ads/boilerplate and returns clean main text + metadata + optional markdown — this is what turns a fetched page into *usable* findings and is a large, cheap quality win. Runs pure-Python, headless, no browser.
- ♻ **curl_cffi** promoted to the **default HTTP fetcher** for every non-JS target (validated in IV.3). Replaces bare `httpx` everywhere in the extraction chain.
- ➕ **nodriver** as the **JS-rendering / hard-anti-bot extractor** (see IV.3 for why it beats Obscura/FlareSolverr/Playwright). Direct-CDP, headless-capable, free (AGPL-3.0 — review license for the proprietary product).
- ➕ **Camoufox** as the **TLS-shape-alternative** browser for targets that whitelist Firefox / block Chrome-shape.
- ➕ **Patchright (`channel=chrome`)** as a **drop-in Playwright replacement** if/when the codebase is already Playwright-shaped and a full nodriver rewrite is too costly.
- ♻ **Obscura** demoted from "primary extractor" to "optional, only if a genuine native Linux binary is present and `obscura serve` is healthy." On the current host it stays disabled (Part III D14).
- ♻ **FlareSolverr** kept only as an *optional* Cloudflare-IUAM helper, health-checked; it is **not** a general anti-bot answer (it does not defeat automation-protocol fingerprinting) and must never block the chain.
- **Resulting extraction ladder (final):** structured JSON API → Jina Reader → **curl_cffi + Trafilatura** → **nodriver (system Chrome, direct-CDP)** → **Camoufox (Firefox shape)** → FlareSolverr (IUAM only). Climb only when the current rung is blocked; ~most requests resolve in the first three rungs with no browser at all.

### IV.2.2 Discovery
- Keep **SearXNG** (self-hostable, keyless meta-search) as primary discovery, but **run it as a managed local instance** (Docker) for reliability instead of depending on public instances that rate-limit. Keep **Jina search** (`s.jina.ai`) as a keyless secondary.
- ➕ Add **sitemap / RSS discovery** for known high-value domains — cheaper and stealthier than search for sites you revisit.

### IV.2.3 Structured-data breadth (the credibility layer — biggest report-quality lever)
Keep the existing keyless set (World Bank, SEC/EDGAR, OpenAlex, Hacker News, FRED). Consider adding, gated by availability:
- ➕ **Wikidata / Wikipedia REST** (entities, definitions, baselines) — keyless.
- ➕ **OpenCorporates / GLEIF LEI** (company identity) — mostly keyless.
- ➕ **Crossref** (DOIs, publication metadata) — keyless; note this is *not* the excluded semantic_scholar path, same as OpenAlex.
- ➕ **data.gov / Eurostat / OECD SDMX** (official statistics) — keyless.
- These are the *cheapest, most reliable, most citable* content on a headless host and are the strongest antidote to "reports look thin." A specialist should query the relevant subset **first**, then use web extraction for color.

### IV.2.4 Rendering / charts
- ♻ **WeasyPrint** stays the primary PDF path (install + native deps + smoke-test, Part III D15). It is the right headless, no-browser choice.
- ➕ **Typst** as an *optional* high-end typesetting path for truly "consulting-grade" layout if WeasyPrint's CSS proves limiting — headless, fast, no browser.
- ♻ **Playwright/Chromium PDF fallback** only when a real Linux browser is provisioned (already the case if nodriver/Patchright are installed for extraction — reuse that Chrome).
- ♻ **Plotly + kaleido** kept for charts, but pin the static engine (no browser), drop to `scale=2`, and on failure **embed a data table** rather than a missing image (Part III). ➕ Consider **matplotlib** as a zero-dependency chart fallback that never needs a browser.

### IV.2.5 Caching / infra
- ➕ **SQLite (or DuckDB) as the run store** for the durable journal + response cache + artifact index (IV.1.1) — zero-cost, embedded, no server.
- ➕ **A managed local SearXNG + (optional) FlareSolverr** via a single `docker-compose` so the "services that must be running" are reproducible instead of assumed.


---

## IV.3 — A genuinely more robust STEALTH system (benchmark-grounded)

> This supersedes Part III.4's *conceptual* stealth layers with a **concrete, evidence-backed** stack. The evidence is a 2026 anti-detect benchmark: **7 stealth tools × 31 real targets (Cloudflare/DataDome/Akamai/F5) × 3 sweeps from a residential IP.** The findings overturn some of Part III's tool choices — this is the honest correction.

### IV.3.1 The four detection surfaces (you must defeat the RIGHT one)
A target can block you at any of four layers. Most tools only address one:
1. **IP reputation** (datacenter vs residential ASN) — fixed by proxies *only*.
2. **TLS / JA4 + HTTP/2 SETTINGS fingerprint** — fixed by the HTTPS client shape (curl_cffi / Camoufox), **not** by a proxy.
3. **JS-runtime fingerprint** (navigator, canvas, WebGL, screen) — fixed by browser-level spoofing (Camoufox/patched Chromium).
4. **Automation-protocol fingerprint** (the CDP handshake: `Runtime.enable`, `Target.setAutoAttach` sequence Playwright emits) — **this is the layer that hard-blocks headless scrapers, and NO fingerprint patch reaches it.** Only a control plane that is *not Playwright* defeats it.

**Why Part III was incomplete:** FlareSolverr, Obscura `--stealth`, and Playwright-based stealth_search all operate at layers 2–3. The benchmark showed that on the hardest real targets (Cloudflare Turnstile), **every Playwright-based and every fingerprint-patched tool was hard-blocked**, while the tool that drove Chrome *directly over CDP with no Playwright shim* passed with **zero blocked cells**. So the tools Part III named as the stealth answer do not defeat layer 4 — the layer that matters most.

### IV.3.2 Benchmark results that drive the design (31 targets, N=3, residential IP)
| Tool | Mechanism | OK / Blocked | Verdict for HYPERION |
|---|---|---|---|
| **nodriver** | Direct-CDP, no Playwright shim, system Chrome | **28 / 0** | **Primary hard-target extractor.** Only tool with zero blocks; passed Turnstile targets others couldn't. |
| **curl_cffi** | Chrome-shaped TLS/JA4, HTTP-only (21-line wrapper) | 26 / 2 | **Primary HTTP fetcher.** Matched a 130 MB patched Chromium fork. Use for everything non-JS. |
| CloakBrowser | Patched Chromium, 49 C++ patches | 26 / 2 | Ties curl_cffi — not worth its weight vs curl_cffi. Skip. |
| **Camoufox** | Firefox fork, C-level FP spoof, Firefox TLS shape | 25 / 3 | **Secondary browser.** Beats Chromium forks on some Chrome-shape-blocking targets; use as TLS-shape alternative. |
| Patchright (`channel=chrome`) | Playwright fork, CDP-leak patches + real Chrome | 25 / 3 | Best *drop-in* if already on Playwright; the `channel=chrome` (real Chrome TLS) matters more than the patches. |
| vanilla Playwright | baseline | 24 / 5 | Do not use for stealth. |
| rebrowser-playwright | CDP-patch fork, unmaintained | 24 / 5 | Functionally = vanilla. Skip. |

### IV.3.3 The shape-coherence law (the deepest insight — changes HYPERION's host strategy)
The benchmark proved gates cross-check layers for **consistency**. A **Linux server behind a residential proxy is *worse* than no proxy**, because it manufactures a contradiction: residential IP + Linux-shape TLS/JS fingerprints = obvious mismatch → *harder* block. Consequences for HYPERION (which runs headless on Linux):
- **You cannot buy your way past layer 4 with a proxy.** For JS-rendered hard targets, the browser must run on a host whose OS-shape matches its fingerprints.
- **Therefore: prefer the layers you *can* win headless-on-Linux** — layers 2 (curl_cffi Chrome-TLS) and, for JS, nodriver driving a real Linux Chrome (coherent: Linux IP + Linux Chrome shape). Do **not** spoof a macOS/Windows browser from a Linux host — that's an incoherent shape.
- **Proxies are for layer 1 only**, and only when shape-coherent (residential IP + a browser running on that same residential host, or datacenter IP + honest datacenter behavior). Keep proxy rotation *off by default* (zero-cost) and *coherent* when on.

### IV.3.4 HYPERION's final stealth stack (tiered, cheap-first, shape-coherent)
```
Tier 0  Structured JSON APIs .............. no stealth needed (designed for automated access)
Tier 1  Jina Reader (r.jina.ai) ........... server-side extraction, no fingerprint exposure
Tier 2  curl_cffi (impersonate=chrome) .... defeats TLS/JA4 layer, headless, ~26/31 targets
        + Trafilatura for clean main-content parse
Tier 3  nodriver (system Linux Chrome, .... defeats automation-protocol layer (Turnstile)
        direct-CDP)                          shape-coherent: Linux IP + Linux Chrome
Tier 4  Camoufox (Firefox TLS shape) ...... for targets that block Chrome-shape specifically
Tier 5  FlareSolverr (Cloudflare IUAM) .... optional, health-checked, last resort only
```
**Behavioural discipline across ALL tiers (from Part III.4, kept):** per-host token bucket + randomized jitter, request-order shuffling, exponential backoff on 429/403, session/cookie persistence per host, consistent UA *within* a session. **Identity (optional, config-gated):** shape-coherent proxy rotation only.

### IV.3.5 Stealth invariants (final)
1. **Pick the tool by the detection surface** — never throw a browser at a TLS problem or a proxy at a fingerprint problem.
2. **Cheap-first escalation** — never open a browser when curl_cffi + Jina Reader answer (most requests).
3. **Shape-coherence is law** — never present a fingerprint that contradicts the host OS or the IP class. On Linux, be an honest Linux Chrome (nodriver) or an honest HTTP client (curl_cffi).
4. **Defeat layer 4 with a non-Playwright control plane** (nodriver/direct-CDP) — this is the only thing that passes the hardest gates.
5. **Loud health, quiet traffic** — blocked/challenged escalates on the ESCALATION channel; traffic stays low-and-slow.
6. **Proxies are layer-1-only and off by default** — preserve zero-cost; enable only when shape-coherent.

> **Net:** even fully headless on Linux with zero proxies, Tiers 0–2 (APIs + Jina + curl_cffi/Trafilatura) resolve the large majority of real targets, and Tier 3 (nodriver on a local Linux Chrome) adds the hard Cloudflare-Turnstile targets that *nothing in Part III's named toolset could reach*. That is a materially more robust stealth system than "FlareSolverr + Obscura --stealth."


---

## IV.4 — Upgraded roadmap (P10–P13) & maturity scorecard

> Parts I–III delivered P1–P9 (fix + robustness). These phases take HYPERION from "reliable" (Level 2) to "best-in-class" (Level 4). They are **additive and optional-in-order**: ship P1–P9 first (the system must *work*), then layer these to make it *best-in-class*.

### P10 — DURABILITY (Level 2 → 3)
1. `RunJournal` (SQLite, append-only) + content-addressed artifact store (IV.1.1).
2. Every DAG node = idempotent step with `inputs_hash` cache lookup before execution.
3. Orchestrator replay/resume by `run_id`; blackboard-based HANDOFF (IV.1.2).
4. `run_manifest.json` with seed + pinned prompt/model versions (IV.1.4).
**Exit:** kill the process at minute N of a run → restart with same `run_id` resumes from step N; no completed extraction/finding/section is recomputed.

### P11 — MEASURABLE QUALITY (Level 3, the quality bar)
1. Golden-set of representative queries across all workflow types (IV.1.3).
2. Deterministic report checks + LLM-as-judge rubric (shared with runtime gate).
3. CI regression gate on golden-set mean score.
**Exit:** CI fails on a prompt/model change that drops golden-set quality > threshold; every release has a quality number.

### P12 — SOTA EXTRACTION + STEALTH (robustness ceiling)
1. curl_cffi as default fetcher + Trafilatura parse layer (IV.2.1).
2. nodriver (system Linux Chrome, direct-CDP) as the hard-target extractor; Camoufox secondary (IV.3.4).
3. Tiered cheap-first extraction ladder + shape-coherence discipline + behavioural token-bucket/jitter (IV.3).
4. Managed local SearXNG (+optional FlareSolverr) via docker-compose (IV.2.5).
**Exit:** on a headless Linux host with zero proxies, ≥90% of a target sample yields clean main-content text; Cloudflare-Turnstile targets resolve via nodriver; no incoherent-shape requests are emitted.

### P13 — EFFICIENCY + SELF-IMPROVEMENT (Level 4)
1. Semantic + exact response cache (IV.1.5) wired to the durable step cache.
2. Speculative provider racing for critical-path DEEP calls (IV.1.5).
3. Structured-output validation-and-repair loop (IV.1.5).
4. Metrics + distributed trace + per-run cost/latency ledger + completion health table (IV.1.6).
**Exit:** cache-hit rate reported per run; DEEP-call p95 latency drops materially; a dashboard/health table shows tool/tier/cost/degraded status for every run.

### IV.4.1 Maturity scorecard (target end-state)
| Level | Name | Reached by |
|---|---|---|
| 1 | **Broken** | (starting point — empty/garbage reports, designer never runs) |
| 2 | **Reliable** | P1–P9 (Parts I–III): works, never-None, graceful degradation |
| 3 | **Durable & Measurable** | P10–P12: crash-resume, measurable quality, SOTA extraction/stealth |
| 4 | **Best-in-class** | P13: cached, fast, self-observing, regression-gated |

### IV.4.2 Direct answers to the user's four questions
1. **"Is this the best, most robust production-grade architecture?"** — After Parts I–III: **no — it's *reliable* (Level 2), not best-in-class.** The honest gaps are durability, measurable quality, and stealth-tool correctness.
2. **"Can we improve it?"** — **Yes, materially**, via P10–P13: durable execution, an offline eval harness, and reproducible/replayable runs move it to Level 4.
3. **"Better tools?"** — **Yes:** add **Trafilatura** (parse), make **curl_cffi** the default fetcher, add **nodriver/Camoufox** for hard targets, broaden **structured-data APIs**, and use **SQLite** for the durable store. Demote Obscura/FlareSolverr to optional.
4. **"More robust stealth system?"** — **Yes, and this is the biggest correction:** Part III named tools (FlareSolverr/Obscura/Playwright-stealth) that do **not** defeat *automation-protocol fingerprinting* — the layer that actually hard-blocks you. The benchmark-grounded stack (curl_cffi TLS + **nodriver direct-CDP** + Camoufox + **shape-coherence law**) is materially more robust and works headless-on-Linux at zero cost.

### IV.4.3 Constraints reaffirmed (proprietary, multi-workflow)
- Every recommendation is a **general-purpose invariant**, not tailored to any one example query.
- **reddit and semantic/academic (semantic_scholar) remain deliberately excluded**; OpenAlex/Crossref (distinct, allowed) may be used.
- Zero-cost posture preserved: every added tool (Trafilatura, curl_cffi, nodriver, Camoufox, SQLite, SearXNG, WeasyPrint/Typst) is free and headless-capable; proxies stay off by default.

*End of PART IV. — After Parts I–IV, HYPERION has: a verified root-cause diagnosis (I), implementation-grade fixes (II), a per-level forensic audit (III), and an evidence-backed path from reliable to best-in-class (IV).*


---
---

# PART V — OBSCURA VERDICT + DEEP AUDIT OF REPORT GENERATION & PRESENTATION

> **The user's questions, verbatim:** *"tell me we downgraded Obscura and FlareSolverr — are they not worthy? their replacements are better? then do a deep audit on everything around report generation, the designer agents etc. can we even get absolute production-grade HD quality content, text, page, photos? properly formatted? use of premium colours? presentable to S&P 500 executives? is our presentation good enough — because presentation is the main thing? we want complete robustness."*
>
> This part answers both: (A) the honest Obscura/FlareSolverr verdict, and (B) a **code-verified** audit of whether the designer/render layer can actually produce an S&P-500-executive-grade PDF today (spoiler: the design *system* is excellent, but four concrete defects make it fail at render time).

## V.0 — Are Obscura & FlareSolverr "not worthy"? (the honest verdict)

**Short answer: they were never doing their job *on this host*, and their replacements are strictly better *for HYPERION's actual deployment* — but they are not "worthless," they're mis-deployed.**

### V.0.1 Obscura — mis-deployed, not worthless
- **Why it's downgraded:** on this Linux host it is a *Windows `.exe`* (`obscura-bin/obscura.exe`) with **no platform guard** — it physically cannot run (Part III D14), and its CDP/stealth features need an `obscura serve` process nothing starts. So its measured contribution to content today is **zero**.
- **Is it worthy in principle?** Obscura's *design* (fingerprint randomization, `--stealth`, 12 MCP tools, CDP control) targets detection **layers 2–3** (TLS/JS fingerprint). That's real value **if** you run a genuine native Linux binary. But the benchmark (Part IV.3) shows layers 2–3 are **not** where the hardest gates block you — **layer 4 (automation-protocol/CDP-handshake)** is, and Obscura-via-subprocess does not clearly defeat layer 4 the way **nodriver** (direct-CDP, no Playwright shim) provably does (28/31, 0 blocked).
- **Verdict:** Not worthless — but **out-classed for the primary role** by `curl_cffi` (layer 2, headless, 26/31) + `nodriver` (layer 4). Keep Obscura only as an *optional* extractor *if* a native Linux build + `obscura serve` are present and healthy. It is a "nice-to-have specialist," not the backbone.

### V.0.2 FlareSolverr — a narrow tool, wrongly treated as general
- **Why it's downgraded:** it requires a **separate running service** (nothing starts it), it **needs a browser**, and it only solves **Cloudflare IUAM/interstitial** challenges. It does **not** defeat DataDome/Akamai/Turnstile-class gates or layer-4 fingerprinting. Treating it as a general anti-bot answer is the error.
- **Is it worthy in principle?** Yes — for the *narrow* job of clearing a Cloudflare IUAM cookie on a specific domain, run as a health-checked sidecar. That's it.
- **Verdict:** Keep as an **optional, health-checked, last-resort sidecar for Cloudflare-IUAM only.** Never in the hot path; never blocking a fetch.

### V.0.3 Are the replacements actually better? (evidence, not opinion)
| Role | Old (downgraded) | New (promoted) | Why better — measured |
|---|---|---|---|
| HTTP fetch (non-JS) | bare httpx / Obscura | **curl_cffi** (impersonate=chrome) | 26/31 real targets; matched a 130 MB patched Chromium fork; headless; 6.4 MB |
| Hard anti-bot / JS render | Obscura `--stealth` / stealth_search (Playwright) | **nodriver** (direct-CDP) | **28/31, 0 blocked**; only tool through Cloudflare-Turnstile targets |
| Chrome-shape-blocked sites | — | **Camoufox** (Firefox TLS) | passes google-search/medium where Chromium forks block |
| Clean article text | (none — raw HTML) | **Trafilatura** | strips boilerplate → usable findings, not nav/ads soup |
| Cloudflare IUAM | FlareSolverr (as general) | FlareSolverr (as narrow sidecar) | same tool, correct scope |

**Bottom line:** the replacements are better **for a zero-cost, headless-Linux, proprietary multi-workflow engine** — which is exactly what HYPERION is. Obscura/FlareSolverr remain available as optional specialists, not the backbone.

## V.1 — Deep audit: can HYPERION produce S&P-500-executive-grade output TODAY?

**Headline: the *design system* is genuinely excellent — but four code-verified defects make the rendered PDF fall short of executive-grade right now.** I read the actual templates/CSS/pipelines; this is not theoretical.

### V.1.1 What is GENUINELY GOOD (keep — this is real craft)
Verified in `hyperion/output/templates/styles/hyperion.css` (574 lines) + `presentation_designer.py`:
- **A deliberate, non-generic palette** (§7.2): warm charcoal `#1A1A1A` (never pure black), cream `#F5F4EE` (never white), terracotta `#C8704D` accent, sage `#7C9885` positive, alert-red `#B5533C` risk-only, deep-brown `#3D3530` grounding. This is a *sophisticated editorial palette*, not clip-art colors. ✅
- **Real print-design discipline:** `@page` A4 with binding margins, running footer with `counter(page)/counter(pages)`, `string-set: report-title`, full-bleed cover with gradient overlay, dedicated methodology page with inverted dark theme, back cover. ✅
- **Semantic callout system:** `.key-insight` (terracotta), `.positive-finding` (sage), `.risk-indicator` (alert-red) with `break-inside: avoid`. ✅
- **Table craft:** dark-brown header, terracotta rule, zebra striping, risk/financial table variants. ✅
- **Chart brand system** (`charts.py`): fixed color sequence (terracotta first, ≤5 colors, Tufte-minimal), cream paper, 1200×800. ✅
- **Image pipeline intent** (`images.py`): 6-step Pillow pipeline, 300 DPI, never-upscale, center-weighted crop. ✅

**This is easily the strongest part of the codebase.** The *taste* is executive-grade. The problem is execution.

### V.1.2 What BREAKS executive-grade at render time (code-verified defects D23–D27)

- **D23 🔴 — The premium fonts DO NOT EXIST (fatal to the whole look).**
  `hyperion.css` declares `@font-face` for **Instrument Serif** and **JetBrains Mono** at `../assets/fonts/*.ttf`, but **there is NO `assets/fonts/` directory in the repo** (verified: "NO fonts dir found"). WeasyPrint silently falls back to a default system serif/sans → **every deliberate typographic decision is discarded at render.** The report you get is *not* the report the CSS describes. **Fix:** vendor the actual `.ttf` files into `assets/fonts/`, verify with a render smoke-test that the embedded font is present in the PDF, and fail loudly if a declared font is missing.

- **D24 🟠 — Monospace body text is the wrong register for S&P-500 executives.**
  Body copy is set in **JetBrains Mono (a monospace *developer* font)**. It's a deliberate choice ("aligns numbers in tables"), but **no McKinsey/BCG/Bain/Goldman deliverable sets body prose in monospace** — it reads as "technical/engineering," not "boardroom." Monospace is correct for *data tables and figures*, wrong for *narrative*. **Fix:** body/narrative in a refined humanist serif or a clean grotesque sans (e.g. keep Instrument Serif for display headers; use a professional text serif like Source Serif / Newsreader, or a sans like Inter for body); reserve JetBrains Mono for tabular numerals and code only. This single change moves the *register* from "dev report" to "consulting report."

- **D25 🟠 — Photos are fragile and can vanish or error the run.**
  `images.py` "never upscale — if too small, raise error" + Unsplash requires an **API key**. On a keyless or low-res result, the image step **errors or drops** → covers/section images disappear, and (pre-Part-III) could even fail the render. Stock photos are also the *weakest* executive-grade element — a boardroom report wants **data visualizations and clean iconography**, not stock imagery. **Fix:** (1) make imagery fully optional and non-fatal; (2) prefer a curated set of abstract/editorial cover treatments (solid palette + typographic cover, or a generated gradient/texture) over stock photos; (3) if using photos, downscale-only from a guaranteed-high-res source and always provide a typographic-cover fallback.

- **D26 🔴 — Charts need Chrome (kaleido) and die headless.**
  `charts.py` exports via `pio.write_image(scale=3)` (kaleido), which pulls a Chromium in many setups → on a headless host with no browser the charts **fail**, and a data report with no charts is not executive-grade. **Fix:** pin kaleido's non-browser static engine and smoke-test it; if it fails, fall back to **matplotlib** (pure-Python, no browser) rendered in the same brand palette; never emit a chart-less report silently — emit a styled data table in place of a failed chart.

- **D27 🟡 — Layout richness is under-used vs the palette's potential.**
  The CSS supports it, but the *generated* HTML tends toward single-column prose. Executive-grade decks use **executive-summary dashboards, KPI stat-strips, 2–3 column feature grids, pull-quotes, and “so-what” callouts**. **Fix:** give the Presentation Designer explicit **layout components** (KPI strip, two-column comparison, quadrant/matrix, timeline, callout band) and require the executive summary to render as a **visual dashboard**, not a paragraph.

### V.1.3 Honest scorecard — presentation readiness
| Dimension | Design intent | Render reality (today) | Executive-grade? |
|---|---|---|---|
| Color palette | Sophisticated editorial | Correct (CSS colors are inline hex) | ✅ Yes |
| Typography | Instrument Serif + JetBrains Mono | **Fonts missing → default fallback** (D23); monospace body wrong register (D24) | ❌ No |
| Layout / structure | Cover, footer, callouts, methodology | Structure good; **under-uses rich layouts** (D27) | 🟡 Partial |
| Charts | Branded Plotly, 300 DPI | **kaleido/Chrome → fails headless** (D26) | ❌ No |
| Photos | 300 DPI Pillow pipeline | **key-dependent + raises on low-res** (D25) | 🟡 Fragile |
| PDF render | WeasyPrint 300 DPI | **weasyprint not installed** (Part III D15) | ❌ No |
| Text quality | McKinsey-grade prose | depends on content pipeline (Parts I–IV) | 🟡 Depends |

**Verdict:** The design *system* is ~90% of the way to executive-grade — genuinely good taste. But because **fonts are missing, charts need an absent browser, photos are fragile, and WeasyPrint isn't installed**, the *rendered artifact today* is **not** S&P-500-ready. These are all fixable, and none require redesigning the (excellent) visual language.

## V.2 — Phase P14: "Executive-Grade Presentation" (make it boardroom-ready)

> Ships after P6 (which installs WeasyPrint and fixes the escape/paths). P14 is the "presentation is the main thing" phase — it turns the good design *system* into a good rendered *artifact*.

1. **Embed real fonts (D23):** vendor `Instrument Serif` (display) + a professional **body serif/sans** (D24) + `JetBrains Mono` (tabular only) into `assets/fonts/`; render smoke-test asserts each declared font is embedded in the output PDF; fail loudly if missing.
2. **Fix the body register (D24):** narrative in humanist serif/clean sans; monospace reserved for numerals/tables/code. A/B the two on the golden set (IV.1.3) with the LLM-judge "boardroom register" rubric.
3. **Charts without a browser (D26):** pin kaleido static engine + smoke-test; matplotlib brand-palette fallback; failed chart → styled data table, never blank.
4. **Photos optional & premium (D25):** imagery non-fatal; default to typographic/gradient editorial covers; stock photos only from guaranteed-high-res, downscale-only, with fallback.
5. **Rich executive layouts (D27):** add layout components (KPI stat-strip, 2–3 col grids, quadrant matrix, timeline, callout bands, pull-quotes); executive summary renders as a **visual dashboard**.
6. **Pixel QA gate:** render the golden set to PDF in CI and assert: fonts embedded, ≥1 chart per data section, no missing-image boxes, no default-font fallback, no orphaned headers, footer on every page, cover full-bleed. A report fails CI if any check fails.
**Exit criteria:** a blind reviewer cannot distinguish a HYPERION PDF's *production values* (typography, color, charts, layout) from a real McKinsey/BCG deliverable; every golden-set report passes the pixel QA gate.

### V.2.1 Direct answers to the user's presentation questions
- **"Can we get absolute production-grade HD content/text/pages/photos?"** — **Yes, after P6 + P14.** Text: yes (Parts I–IV content pipeline + real fonts). Pages: yes (WeasyPrint 300 DPI + rich layouts). Charts: yes (kaleido-static/matplotlib at 300 DPI). Photos: yes but **optional/premium** — for boardroom output, prefer data-viz + typographic covers over stock photos.
- **"Properly formatted / premium colours?"** — the palette is **already premium and correct**; formatting becomes properly executive-grade once fonts + layouts land (D23/D24/D27).
- **"Presentable to S&P 500 executives? Is presentation good enough?"** — **Not today** (fonts missing, charts/PDF dead on host), **but the design language is already at that level** and P14 closes the execution gap. Presentation *is* treated as the main thing — hence a dedicated phase + a pixel QA gate.
- **"Complete robustness?"** — full stack: Parts I–III (works, never-None) → IV (durable + measurable + SOTA extraction/stealth) → **V/P14 (executive-grade presentation with a CI pixel gate)**. That is complete, boardroom-grade robustness.

### V.2.2 New defects added: **D23** (fonts missing), **D24** (monospace body register), **D25** (fragile photos), **D26** (kaleido/Chrome charts), **D27** (under-used layouts). Owning phase: **P14** (+ P6 for WeasyPrint/render prerequisites).

*End of PART V.*


---
---

# PART VI — CONSOLIDATED MASTER EXECUTION PLAN (whole pipeline, phase-wise) + CODING-AGENT HANDOFF

> **What this part is:** Parts I–V diagnosed and prescribed in layers. This part collapses **all 27 defects (D1–D27)** and **all 14 phases (P1–P14)** into **one ordered, end-to-end execution plan for the whole pipeline**, plus a per-tool "use it at its best capability" doctrine (including Obscura), and finishes with a **crisp handoff prompt** to give coding agents.
>
> **First principle — read this before touching code:** **HYPERION is a proprietary multi-agent research→PDF system. It is NOT a thin wrapper around a model or a scraping library.** Every tool is deployed *at its best capability for its correct use-case*, orchestrated by a purpose-built DAG. Fixes must preserve that proprietary architecture and the deliberate design language — do not "simplify" it into a generic wrapper.

## VI.0 — Tool doctrine: every tool at its BEST capability (including Obscura)

The pipeline is capability-routed. Each tool has ONE job it is best at; the orchestrator escalates cheap→expensive and picks the tool by the *detection surface / data type*, never by habit.

| Tool | Best-capability role (use it for THIS) | Never use it for |
|---|---|---|
| **Structured JSON APIs** (World Bank, SEC/EDGAR, FRED, OpenAlex, Crossref, Wikidata, HN) | **First-choice evidence** — hard, citable numbers; zero fingerprint risk; perfect headless | Narrative color / opinion |
| **Jina Reader** (`r.jina.ai`) | **Server-side clean-text extraction** — no browser, no fingerprint; the default extractor | JS-heavy interactive apps |
| **curl_cffi** (impersonate=chrome) | **Default HTTP fetch** for non-JS pages — defeats TLS/JA4 (layer 2), headless | Pages requiring JS execution |
| **Trafilatura** | **Main-content parsing** — strip boilerplate → usable article text + metadata | Fetching (it's a parser, not a client) |
| **nodriver** (direct-CDP, system Linux Chrome) | **Hard anti-bot / JS render** — defeats automation-protocol layer (Turnstile); the heavy hitter | Simple pages curl_cffi already handles |
| **Camoufox** (Firefox TLS shape) | **Chrome-shape-blocked targets** — sites that whitelist Firefox | Default use (heavier than curl_cffi) |
| **Obscura** (native Linux build + `obscura serve`) | **Fingerprint-randomized stealth fetch (layers 2–3) + its 12 MCP tools** for sites where a *rotating, per-request fingerprint* beats a static one; use its CDP/MCP surface when a session needs programmatic control | As a Windows `.exe` on Linux; as the sole backbone |
| **FlareSolverr** (health-checked sidecar) | **Cloudflare-IUAM cookie clearing ONLY** — narrow, last resort | General anti-bot; hot path |
| **SearXNG** (managed local instance) | **Primary discovery** meta-search — keyless, budgeted, deduped | Bulk verification storms |
| **WeasyPrint** | **Primary PDF render** — headless, 300 DPI, embedded fonts | JS-driven layouts |
| **Plotly+kaleido (static engine) / matplotlib fallback** | **Branded charts at 300 DPI** — kaleido-static primary, matplotlib when no browser | Screenshots of charts |
| **Pillow pipeline** | **300-DPI image processing** — downscale-only, center-crop | Upscaling (must fail-soft, not raise) |

> **Obscura's rightful place (per the user's instruction to use it at best capability):** ship/point to a **genuine native Linux Obscura binary**, run **`obscura serve`** as a managed process, and use it as the **rotating-fingerprint stealth extractor + MCP tool surface** on targets where per-request fingerprint randomization (layers 2–3) is the winning move and a persistent CDP session is useful. It sits in the ladder *between* Camoufox and FlareSolverr. It is a first-class specialist — just not the sole backbone, because layer-4 gates still need nodriver's direct-CDP control plane.

## VI.1 — The whole-pipeline fix sequence (one ordered path, P1→P14)

Grouped into 5 stages. Each phase lists its defects and the one-line "done" test. Ship stages in order; within a stage, tasks can parallelize.

### STAGE A — MAKE IT WORK (unblock content + a real PDF)  → phases P1, P2, P3, P6
- **P1/P2/P3 (Parts I–II):** SearXNG-first discovery; kill the FlareSolverr flood (D3); extraction returns real text (D1); findings survive to Synthesis (D4).
- **P6 (Part III):** platform-guard Obscura (D14); re-order extraction ladder (structured API → Jina Reader → curl_cffi+Trafilatura → nodriver → Camoufox → Obscura(native) → FlareSolverr); **install WeasyPrint + native deps** (D15); fix `jinja2.Markup`→`markupsafe.Markup` double-escape (D16); base64/relative image paths (D17); sanitize `<title>`; convert orchestrator early-return into **floor-report fallback so delivery ALWAYS runs** (D13); curl_cffi transport + per-host jitter (D22).
- **DONE-A:** 3 different query types run headless → each yields a real PDF with actual content, no `&lt;` escapes, no `C:\` paths, designer log shows it executed.

### STAGE B — MAKE IT HONEST & NEVER-BREAK  → phases P4, P5, P7, P8, P9
- **P4/P5 (Part II):** specialists degrade never hard-fail (D8); synthesis always returns, fast (D5); router cross-provider ladders (D9).
- **P7:** synthesis ≤3 DEEP calls, shard long prompts, **never return None** → `FinalReport(degraded=True)`; content-aware quality gate (≤2 loops); MICRO enrichment classifier (D-enrich); markdown exporter schema fix (D18); fact-checker corpus-first + search budget (D3).
- **P8:** rebuild tier candidate lists — FAST=Mistral-small primary (Cerebras overflow, D20), STANDARD=NVIDIA/Mistral (Groq burst-only, D21), **DEEP={Google flash-lite, Mistral devstral, NVIDIA ultra-550b} round-robin, ≥2 non-Google** (D19); per-candidate timeout override for slow DEEP; output-budget right-sizing.
- **P9:** DELIVERY subscribes `{FINDINGS, HANDOFF}`; synthesis reads findings at execution-time (fix injection race, D4); explicit FinalReport HANDOFF; all `gather(return_exceptions=True)`; ESCALATION on any tool-unavailable/tier-starved + startup health table.
- **DONE-B:** kill any one provider key → every tier still resolves; force-fail DEEP → still emits a `degraded` PDF; inject a failing sub-agent + an unavailable tool → run completes with an ESCALATION log entry.

### STAGE C — MAKE IT DURABLE & MEASURABLE  → phases P10, P11
- **P10:** `RunJournal` (SQLite append-only) + content-addressed artifact store; every DAG node an **idempotent step** with `inputs_hash` cache lookup; orchestrator **replay/resume by `run_id`**; blackboard HANDOFF; `run_manifest.json` with seed + pinned prompt/model versions.
- **P11:** golden-set across ALL workflow types; deterministic report checks + shared LLM-judge rubric; **CI regression gate** on golden-set mean score.
- **DONE-C:** kill process at minute N → restart resumes from step N, no recompute; CI fails on a change that drops golden-set quality > threshold.

### STAGE D — MAKE IT SOTA (extraction, stealth, efficiency)  → phases P12, P13
- **P12:** curl_cffi default fetcher + Trafilatura parse; **nodriver** hard-target extractor; Camoufox secondary; **native Obscura + `obscura serve`** as rotating-fingerprint specialist; tiered cheap-first ladder + **shape-coherence law** (no Linux+residential-proxy contradiction) + behavioural token-bucket/jitter; managed local SearXNG (+optional FlareSolverr) via docker-compose.
- **P13:** semantic+exact response cache wired to durable step cache; **speculative provider racing** for critical-path DEEP calls; structured-output validate-and-repair loop; metrics + distributed trace + per-run cost/latency ledger + completion health table.
- **DONE-D:** on headless Linux, zero proxies, ≥90% of a target sample yields clean main-content text; Turnstile targets resolve via nodriver; cache-hit rate reported; DEEP p95 latency drops materially.

### STAGE E — MAKE IT BOARDROOM-GRADE (presentation is the main thing)  → phase P14
- **P14:** **embed real fonts** into `assets/fonts/` — Instrument Serif (display) + a professional body serif/sans + JetBrains Mono (tabular only) (D23); **fix body register** off monospace (D24); **browserless charts** — kaleido-static + matplotlib brand-palette fallback, failed chart → styled table (D26); **photos optional & premium** — non-fatal, typographic/gradient editorial covers default, downscale-only stock as option (D25); **rich exec layouts** — KPI stat-strips, 2–3 col grids, quadrant/matrix, timelines, callout bands, pull-quotes; exec summary as a **visual dashboard** (D27); **CI pixel-QA gate** — fonts embedded, ≥1 chart per data section, no missing-image boxes, no default-font fallback, footer every page, cover full-bleed.
- **DONE-E:** a blind reviewer cannot distinguish HYPERION's production values (type, color, charts, layout) from a real McKinsey/BCG deliverable; every golden-set report passes the pixel-QA gate.

## VI.2 — Master defect → phase → done map (single source of truth)

| Stage | Phase | Defects closed | The one test that proves it |
|---|---|---|---|
| A | P1–P3 | D2, D3(part), D1, D4(part) | discovery + extraction return real text |
| A | P6 | D13, D14, D15, D16, D17, D22 | headless run → real non-escaped PDF, designer runs |
| B | P4,P5,P7 | D5, D8, D18, D3(rest), enrich | force-fail DEEP → degraded PDF still ships |
| B | P8 | D19, D20, D21, D9 | kill one provider → all tiers resolve |
| B | P9 | D4(rest), wiring edges 1–5 | failing sub-agent + dead tool → run still completes + escalates |
| C | P10 | durability gap | crash at min N → resume at min N |
| C | P11 | measurability gap | CI quality regression gate live |
| D | P12 | extraction/stealth ceiling | ≥90% clean-text; Turnstile via nodriver; Obscura native specialist live |
| D | P13 | efficiency/self-improvement | cache-hit + DEEP p95 reported |
| E | P14 | D23, D24, D25, D26, D27 | blind-reviewer boardroom parity; pixel-QA gate green |

## VI.3 — Global guardrails (apply to EVERY phase)
1. **Proprietary, not a wrapper:** preserve the DAG orchestration, the agent roles, the design language, and the tool-doctrine routing. Do not collapse it into a single-model or single-library wrapper.
2. **Zero-cost, headless-first:** every added tool is free + headless-capable; proxies off by default; browser tools gated behind a startup probe.
3. **Never silent, never None:** every failure escalates (ESCALATION channel + logs); synthesis and delivery always produce *something* honest (`degraded=True` when thin).
4. **Capability routing:** pick the tool by detection surface / data type; escalate cheap→expensive; use each tool (incl. Obscura) only at its best-capability role.
5. **Exclusions honoured:** reddit and semantic/academic (semantic_scholar) stay OUT; OpenAlex/Crossref (distinct, allowed) may be used.
6. **Every change committed + PR'd** on `genspark_ai_developer`; each phase has a passing DONE test before the next.

## VI.4 — CODING-AGENT HANDOFF PROMPT (copy-paste this)

> Give the block below to the coding agent(s). It is intentionally short and points them at THIS file as the single source of truth.

```text
You are working on HYPERION.

HYPERION is a PROPRIETARY multi-agent research→PDF system — NOT a generic
wrapper around an LLM or a scraping library. It has a purpose-built DAG
orchestrator, specialist/sub-agent roles, a 5-provider LLM tier fabric, a
capability-routed tool stack, and a deliberate McKinsey/BCG-grade design
language. Preserve that architecture. Do not simplify it into a wrapper.

Your single source of truth is:  docs/HYPERION_MASTER_REMEDIATION_PLAN.md

TASK: Fix and upgrade the WHOLE pipeline exactly as that document specifies —
end to end: search/discovery, extraction, LLM router + tiers, specialists +
sub-agents, synthesis, quality gate, delivery (presentation designer, data
visualizer, render engine), charts, images, PDF render, observability,
durability, and presentation quality.

Follow PART VI (the consolidated master plan). Execute the stages IN ORDER:
  STAGE A (P1–P3, P6)  — make it work: content flows + a real non-escaped PDF; designer always runs.
  STAGE B (P4,P5,P7–P9)— make it honest & never-break: never-None synthesis, multi-provider tiers, wired edges.
  STAGE C (P10,P11)    — durable (crash-resume) + measurable (golden-set CI gate).
  STAGE D (P12,P13)    — SOTA extraction/stealth + efficiency (cache, provider racing).
  STAGE E (P14)        — boardroom-grade presentation + CI pixel-QA gate.

RULES:
- Close defects D1–D27; each phase has a "DONE" test in the doc — a phase is not
  done until its test passes. Do not start the next stage until the current
  stage's DONE tests pass.
- Use every tool AT ITS BEST CAPABILITY per the VI.0 tool doctrine — including
  Obscura (native Linux binary + `obscura serve`, as the rotating-fingerprint
  stealth/MCP specialist), curl_cffi (default HTTP), nodriver (hard anti-bot /
  Turnstile), Camoufox (Chrome-shape-blocked), Jina Reader + structured APIs
  (first-choice), Trafilatura (parsing), WeasyPrint (PDF), kaleido-static/
  matplotlib (charts). Route by detection surface / data type, cheap→expensive.
- Never silent, never None: every failure escalates + logs; synthesis and
  delivery always emit something honest (degraded=True when thin).
- Zero-cost, headless-first; proxies off by default; browser tools gated behind
  a startup availability probe; obey the shape-coherence law (IV.3.3).
- Keep reddit and semantic/academic (semantic_scholar) EXCLUDED. OpenAlex and
  Crossref are allowed.
- Keep the existing design language (palette + layout system in
  hyperion/output/templates/); fix its execution (embed the real fonts, move
  body off monospace, browserless charts, optional premium photos, richer
  executive layouts) — do not redesign the visual identity.

WORKFLOW: work on branch genspark_ai_developer. Commit after every change with
a clear message. After each STAGE, open/update a PR summarizing what was fixed,
which defects closed, and which DONE tests now pass. Share the PR link.

Deliverable of the whole effort: a headless Linux run of several different
query types, each producing an S&P-500-executive-grade PDF (real embedded
fonts, branded charts, premium palette, rich layout), with durable resume,
a green golden-set quality gate, and a green pixel-QA gate.
```

*End of PART VI — this is the master execution plan and the coding-agent handoff. Parts I–VI together are the complete, self-contained brief: diagnosis (I), implementation detail (II), forensic per-level audit (III), best-in-class SOTA upgrade (IV), Obscura verdict + presentation audit (V), and the consolidated whole-pipeline plan + handoff (VI).*
