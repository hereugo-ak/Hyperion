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
