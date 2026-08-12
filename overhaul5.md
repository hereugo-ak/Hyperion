# OVERHAUL5 — The Fire, Not the Alarm: Paid Web Backbone + Finding Quality

**Date:** 2026-08-13 (drafted from the 2026-08-12 run, build `dc334b8`)
**Companion docs:** overhaul.md (O1), overhaul2.md (O2), overhaul3_audit.md (O3), overhaul4.md (O4)
**Evidence:**
- TUI log `reports/diagnostics/tui_log_0x325EA4_20260812_160229_218091.txt` (UTC; run 14:53→16:02)
- Docker log `docker log post overalhaul4 run.txt` (IST host; run window 20:24→21:33; test-session leftovers 00:39/15:06)
- Ledger `eng_b41f2918c39c/evidence_ledger.json` (4,640 records / 1,012 domains)
- Diagnostic `blocked_eng_b41f2918c39c.json` (score 2.65/4.0, blocked)
- Journal `artifacts/eng_b41f2918c39c/journal.sqlite` (14 tasks, 3× timeout:1200s)
- Live key verification `scripts/check_you_yep_search.py` + direct curls (all four providers, 2026-08-12)

---

## 0. Verdict — one paragraph

Every previous overhaul fixed the **alarm** — the gates, the assembly, the contracts, the honesty of the failure — and each one moved the terminal failure forward one layer: empty report (O1) → shell report (O2/O3) → honest-but-thin report (O4). None fixed the **fire**: the web class has no living, relevant, extractable source, and findings are built from snippets over a wrong corpus. The 08-12 run proves the O1–O4 machinery now works *as designed*: preflight is honest AMBER, the ledger-aware floor did not block, recovery ran, the report has 13 sections. It still BLOCKED because the report is shallow (2.65 < 3.0): sections 400–950 chars, 40 evidence-chain breaks, 23% verification, two integrity blockers. The system itself diagnosed the disease — `QUALITY: iteration 2 produced no score change (2.85) — terminating early; the input, not the polish, is the problem.` Overhaul5 fixes the **input**: a paid web backbone that actually fires (all four providers verified live — the adapters were stale), a web class that returns real pages instead of paywall DOIs, extraction that finally has extractable URLs, and a finding-quality gate at birth instead of at minute 68.

---

## 1. Why four overhauls didn't converge (the meta-failure)

| Overhaul | What it fixed (and landed) | What it *assumed* | What the 08-12 run proved |
|---|---|---|---|
| **O1** (evidence control plane) | Ledger, preflight, KPI telemetry. **Live:** ledger holds 4,640 records / 1,012 domains this run | "Measuring evidence will fix retrieval" | Measurement is honest; the corpus is still 80% scholar metadata and the web class is dead |
| **O2** (output contract) | Single writer, dependency aliasing, `sub_findings` crash ×10, reference category 400. **Live:** no `MissingDependencyOutput` crash; synthesis ran on partial context | "If outputs flow, the report will be full" | Outputs flow — but they are thin findings built from snippets over a wrong corpus |
| **O3** (self-healing) | Recovery supervisor, honest failure typing, 16 canaries, `_log` arity. **Live:** recovery pass 1 ran; `PLACEHOLDER_VALUE→risk_analyst` | "Re-running a specialist over a live pool recovers" | The pool is the same thin/off-topic pool; recovery produced 19 duplicate findings and was discarded (2.65 < best+0.05) |
| **O4** (pacing + paid layer + extraction tiers) | Engine interval 6s, debris sanitizer, P8 paid chain, P7 extraction ladder, ledger-aware floor, digest fallback, cover. **Live:** report has 13 sections; floor didn't block | "Paid providers rescue dead SearXNG; extraction enriches findings" | **Both false.** Paid chain never effectively fired (stale adapters + wrong trigger); extraction got 0 extractable URLs (paywalls) |

**The shared blind spot:** every overhaul verified the *mechanism* (does the code run?) and never verified the *outcome* (does a web-class query return a relevant, extractable web page?). O4 came closest — it *added* the paid chain and the extraction ladder — but neither was tested against a live web query end-to-end. The 08-12 run is that test, and both failed. This is the last time we add machinery without a live-query gate.

---

## 2. Evidence trail — the 08-12 run (build `dc334b8`)

Question: *"how india can beat china in manufacturing ?"* · 14:53→16:02 UTC · 17-task DAG · 95 LLM calls

| Quantity | Value | Reading |
|---|---|---|
| Ledger | 4,640 records / 1,012 domains | Retrieval collected a **massive** corpus |
| …of which `crossref` | 2,625 (57%) | Web queries answered by scholar metadata |
| …`web` profile | 297 (mwmbl 223, brave 74) — 6.4% | Web class effectively dead |
| …`you/exa/tavily/yep` | **0** | Paid chain never contributed |
| …`stage=extraction` | **0** | Extraction produced nothing |
| Preflight (t=12s) | 28/8 domains; `web=9d/10e; scholar=19d/50e; reference=0d/0e` | Honest AMBER; reference dead by config-time 403 |
| Task outcomes | 13 ok / 3 timeout:1200s (ops, sustainability, regulatory→reframed) | Completed pipelines discarded at the timeout boundary |
| Findings | 45 collected, 13 task outputs | Thin, off-topic, duplicated |
| FACTCHECK | 78 claims; 13 verified; 2 hallucinated; **40 evidence-chain breaks**; 23% | Facts not grounded in extracted text |
| Gate | **2.65/4.0**; DATA VOID + VERDICT CONTRADICTION; 4 critical dims | Correctly blocked a shallow report |

**Live provider verification (2026-08-12, all four):** You `HTTP 200` 10 results/4.52s (`ydc-index.io/v1/search`); Exa `HTTP 200` 3 results; Tavily `HTTP 200` 3 results; Yep `HTTP 200` 10 results/0.71s, $0.004/call (`platform.yep.com/api/search`). The old endpoints in the adapters return 403.

---

## 3. Defects in super depth

Each defect: **code site → log evidence → mechanism → why it survived O1–O4 → fix direction.** Ordered by the chain they break: adapters → trigger → corpus → extraction → findings → report → recovery.

### D-01 · Stale paid-provider adapters — You.com and Yep hit dead endpoints

**Code site:**
- `hyperion/search/adapters/you.py:22` `endpoint = "https://api.ydc-index.io/search"` (dead) · `:46` body `{"query":…, "num_web_results": N}` (ignored) · `:57` `data.get("hits")` (field no longer exists)
- `hyperion/search/adapters/yep.py:22` `endpoint = "https://api.yep.com/fs/2/search"` (dead) · `:41` `client.get(endpoint, params={q, gl, max_results, safe_search})` (API is now POST+JSON at `platform.yep.com/api/search`)

**Log evidence:** run docker/TUI — zero paid records; the only visible signal would be `search provider You 403 — suspended for run` at `logger.warning` (not surfaced in TUI). Direct curls at the old endpoints: `HTTP 403 {"message":"Missing Authentication Token"}` (You), `HTTP 403` HTML block (Yep). Verified working at the new endpoints with the **same keys**.

**Mechanism:** both APIs moved (You.com to `ydc-index.io/v1/search` with `count` + `results.web[]` shape; Yep pivoted to the Ahrefs "YEP Search API" at `platform.yep.com/api/search` with `{query,type,limit,language,location}` + `results[]` + `api_cost/balance`). The adapters were written against the old specs and never re-verified. Every call 403s; `suspension.py:48` marks 403 **permanent** (`suspended_until = now`, `permanent = True`) → the provider is exiled for the rest of the run on call #1.

**Why it survived O1–O4:** O4 added the adapters (commit `174459f`) but verified them with a SearXNG smoke test only (`live WSL smoke: SearXNG 10 results … paid untouched` — literally "paid untouched" in the TODO). No live paid-query gate existed. This is the exact "added but never outcome-tested" pattern from §1.

**Fix:** replace both adapters with the verified code from `docs/YOU_YEP_API_FINDINGS.md` (endpoint, method/body, response parsing). Add a **boot-time key+endpoint probe** (D-13) so a stale adapter or dead key is visible and skipped, never exiling the chain.

### D-02 · The paid-chain trigger is binary-zero, not "no proper result"

**Code site:** `hyperion/tools/searxng.py:1604-1612` — the MULTI-PROVIDER PAID CHAIN block runs only after:
1. `_search_with_rotation` returned nothing (`:1568` `if searxng_response and searxng_response.results: return`), **and**
2. F-03 full-pool fan-out returned nothing (`:1594-1601`)

**Log evidence:** ledger `web=297` records and `scholar=3728` — for most queries *something* non-zero came back, so the paid chain was reached only for the ~491 queries that ended in the Jina fallback. The web class was "answered" by scholar results **every query** — non-zero, wrong corpus.

**Mechanism:** the trigger checks *existence* ("did SearXNG return anything?"), but the disease is *quality* ("did a web-class query return web-class results?"). `SearxNGClient.search()` returns non-empty whenever *any* replica answers — and the fan-out guarantees the scholar replica always answers with crossref/openalex metadata. Binary-zero is therefore almost never true for general queries, and the paid providers — the only living general-web sources — are structurally unreachable.

**Why it survived O1–O4:** O4's §5.1 spec says the paid chain is "the canonical fallback … after the free SearXNG stack (rotation + full-pool fan-out) produced nothing." The spec itself encoded the wrong trigger; the TODO then marked P8 done on a SearXNG smoke test.

**Fix:** the caller must classify the response: for a web-class query, "proper" = ≥5 results **with web-class domains that pass a relevance check** (D-03/D-08). If the web class contributed < threshold — regardless of scholar rescue — fire the paid chain. Define `MIN_WEB_RESULTS = 5` and `web_class` provenance on every `SearchResult`.

### D-03 · Scholar fan-out contaminates web queries (the corpus poison)

**Code site:** `hyperion/tools/searxng.py:1063-1150` `_search_all_replicas` — "one parallel request per replica with that replica's full set of currently-healthy engines"; the comment at `:1076` says the scholar/reference API engines "do not ban datacenter IPs … a dead web pool can still be rescued by crossref/openalex/wikipedia."

**Log evidence:** ledger profiles `web=297 / scholar=3728`; docker run-window: web replica yep `403` **every query**, brave `429` **every query**, mojeek `httpx.RemoteProtocolError` repeatedly, mwmbl 20s ReadTimeouts; scholar replica crossref answered continuously. The report's Risk Assessment section cites *Apple iPhone-ban*, *Italian Fashion*, *additive-manufacturing* papers for an India-vs-China manufacturing question — exactly the scholar-rescue garbage.

**Mechanism:** a web query with a dead web pool falls through to the fan-out, which serves it from the scholar replica (crossref DOIs). Three consequences stack:
1. **Wrong corpus:** academic metadata answers general-web questions (findings cite paywalled papers).
2. **Extraction death:** the URLs are `doi.org/10.1016/…` and `linkinghub.elsevier.com/…` → paywalls → D-06 (0 extracted).
3. **Paid chain masked:** non-zero scholar results satisfy the D-02 trigger, so the paid web backbone never runs.

**Why it survived O1–O4:** O1 added the fan-out as a rescue and O2–O4 treated it as the safety net ("the safety net is burned too" was diagnosed in O1 §3 but the fan-out was kept and even hardened). Nobody asked: *what does the fan-out return for a web query, and is it the right corpus?* The ledger answers: 80% scholar.

**Fix:** the fan-out must **not** convert a web-class query into scholar results. Two options, both implementable: (a) exclude scholar replicas from the general/web fan-out (scholar remains reachable only via `categories=scholar`); (b) keep the rescue but **tag the response's corpus** (`SearchResult.web_class=True/False` via domain classifier) so the caller sees "corpus=scholar, expected=web" and escalates to the paid chain (D-02). Option (b) is chosen — it preserves the rescue for genuinely empty fleets while making the trigger honest.

### D-04 · In-orchestrator SearxNGAdapter re-enters the same search (double-dip, no recursion guard)

**Code site:** `hyperion/search/orchestrator.py:42` `TIERS_LOOP = (SearxNGAdapter, YouAdapter, ExaAdapter)` · `:143-148` the loop; `hyperion/search/adapters/searxng.py:44-46` calls `SearxNGClient.search(query, num_results)` — **the same method the caller at `searxng.py:1604` just exhausted** (rotation → fan-out → paid chain).

**Mechanism:** when the outer `SearxNGClient.search()` reaches the paid chain, `orchestrator.search()` starts by calling `SearxNGAdapter` → `SearxNGClient.search()` again → rotation (0) → fan-out (0) → **paid chain again** → `orchestrator.search()` again → … There is **no recursion guard** (grep: zero matches for recursion/contextvar/guard in `searxng.py`, `orchestrator.py`, `adapters/searxng.py`). The recursion is bounded only by `SEARCH_BUDGET_CAP = 600` (`searxng.py:1497`) and the orchestrator's per-provider budget — each level burns one search on the same dead query. Additionally, if the re-search *does* return ≥5 results (`MIN_RESULTS = 5`, `orchestrator.py:34`), the loop exits early with **wrong-corpus results and the paid providers never run** (`:147`).

**Log evidence:** the run's ledger shows mwmbl 223 — consistent with re-searches succeeding slowly on retry; and the paid providers got 0 — consistent with the loop exiting on re-search or never being reached. No "paid chain" line exists in the TUI log at all.

**Why it survived O1–O4:** O4 wired the orchestrator into `searxng.py:1612` as a fallback but never traced the re-entry. The unit test `tests/test_search_layer.py` (10 tests) exercises the orchestrator with **mocked adapters** — the real SearxNGAdapter re-entering the real `SearxNGClient.search()` is never exercised.

**Fix:** (a) when the caller has already exhausted SearXNG (rotation + fan-out), the chain must **skip `SearxNGAdapter`** — the orchestrator needs a `skip_first: set[str]` parameter or a variant chain `TIERS_LOOP_PAID = (YouAdapter, ExaAdapter)`; (b) add a hard **recursion guard** in `SearxNGClient.search()` (module-level re-entrancy flag or contextvar: if already inside a paid-chain escalation for this query, return empty immediately); (c) add a regression test that runs the real orchestrator + real adapter against a mocked-0 SearXNG and asserts paid tiers are called **and** `SearxNGClient.search` is entered at most once per query.

### D-05 · One error = per-run exile, invisibly

**Code site:** `hyperion/search/suspension.py:18` (`"403": None` cooldown), `:48-52` (`signal in ("403","bucket_exhausted")` → `permanent=True`); `hyperion/search/orchestrator.py:186-192` (`logger.warning("search provider %s %s — suspended for run …")`), `:196-199` (`logger.warning("search provider %s failed: %s")`), adapter skips at `logger.debug` (`you.py:39`, `tavily.py:34`).

**Mechanism:** a single 403 (stale endpoint, transient IP block, free-tier hiccup) marks a paid provider **permanent for the run**. 429 → 90s cooldown. 5xx after 3 → 120s. All at `logger.warning/debug` — which the TUI does not surface. Result: the user's paid keys sat unused with zero visible trace; we could not even tell *which* provider failed, let alone why.

**Why it survived O1–O4:** the suspension registry was designed for SearXNG's *crawler engines* (permanent 403 = banned crawler — correct there). It was reused for paid *APIs* with no distinction, and O4 never observed it firing because D-02/D-04 masked the chain entirely.

**Fix:** (a) paid providers: 403 → 120s cooldown + retry (transient), permanent only after N consecutive failures (N=3) — and never permanent for `bucket_exhausted` until the budget owner confirms; (b) every paid attempt emits a TUI-visible `system.log` line: `provider, query[:60], results, error, cooldown`; (c) the boot probe (D-13) pre-classifies providers so a dead endpoint is skipped with a visible banner instead of a silent exile.

### D-06 · Extraction input is poisoned — paywall URLs, no pre-classifier

**Code site:** `hyperion/tools/unified_extract.py` TIER_ORDER (`:263-277`) climbs **all 11 tiers** for every URL; no paywall/DOI short-circuit. `hyperion/agents/sub_agent.py:1050-1122` feeds search URLs straight into `extract_ladder` (cap 10, 3 when broadened).

**Log evidence:** docker run-window: firecrawl/playwright `SCRAPE_ALL_ENGINES_FAILED` on `doi.org/10.1016/…`, `linkinghub.elsevier.com/…`, `mdpi.com/…` (playwright + fetch both failed, paywalls/auth); firecrawl worker `Can't accept connection due to RAM/CPU load` × **7,244** in the run window; `WORKER STALLED {memoryUsage: 0.89}`. Sub-agents logged `raw=8, extracted=0`. Ledger `stage=extraction` = **0**.

**Mechanism:** the URLs the sub-agents extract are the scholar-rescue DOIs (D-03). Paywalled/academic hosts reject headless browsers; the ladder burns 11 tiers × 3 attempts per URL, all fail, zero `record_evidence` calls (`sub_agent.py:1106`). The findings then fall back to snippets — which is the whole reason the report is shallow.

**Why it survived O1–O4:** O4's P7 verified the ladder *in isolation* (`js_heavy→obscura 15.7k chars` on a live test page) — the tier machinery works. Nobody verified the ladder against the *URLs the pipeline actually produces*. The ladder is fine; its input is the disease.

**Fix:** (a) a **paywall/DOI pre-classifier** in the ladder (`classify_url`): known-paywall hosts (`doi.org`, `linkinghub.*`, `sciencedirect.com`, `springer.com`, `wiley.com`, `taylorfrancis.com`, `emerald.com`, `acs.org`, …) → skip the browser tiers and go straight to wayback/abstract extraction, or mark the URL `unextractable` and return a typed reason instead of a 33-attempt death march; (b) a **domain-class heuristic** so scholar DOIs are never offered to a web-class finding path; (c) firecrawl worker health: the 7,244 RAM/CPU rejects show the self-hosted stack was overloaded — cap firecrawl concurrency in `extract_ladder` and treat `Can't accept connection` as a typed tier failure (retry later, not mid-flight).

### D-07 · Extraction is not a first-class tool for every specialist

**Code site:** `hyperion/schemas/agents.py:100-121` ToolName — **no `UNIFIED_EXTRACT`**; specialist grants (verified per-file): competitive/consumer/technology `JINA,OBSCURA`; market/innovation/regulatory/risk/strategy/sustainability `JINA`; ma `OBSCURA`; financial `JINA,DEEP_SEARCH`; **operations `SEARXNG` only**. Firecrawl has no ToolName at all.

**Mechanism:** sub-agents get all tiers via `sub_agent._extraction_tiers()` (`:1024` returns `list(UnifiedExtract.TIER_ORDER)`, availability-probed — the P7 fix). But **specialist direct steps** (non-sub-agent code like COMPETE's site scraping, `competitive_intel.py:704-748`) call `get_tool(ToolName.JINA/OBSCURA)` — and operations has neither, so its direct scraping steps cannot extract at all. And nothing can call firecrawl directly (tier-only).

**Why it survived O1–O4:** O4's P7.0 made the *sub-agent* path grant-independent (correct) and documented the specialist grants as deliberate ("not decorative"). But the run shows specialists' direct extraction is exactly where content died (COMPETE — D-12; OPS — D-11).

**Fix:** add `ToolName.UNIFIED_EXTRACT` (`schemas/agents.py`) + `_instantiate_tool` binding (`agents/base.py:1027-1041` pattern) and grant it to **all 12 specialists**; specialists' direct steps route through the ladder (page-aware, D-06) instead of bespoke per-tool calls. Keep firecrawl ladder-internal (plumbing, not an LLM surface).

### D-08 · No finding-quality gate — relevance, gaps, duplicates

**Code site:** the TOPICALITY drop exists (`sub_agent` funnel: TUI `TOPICALITY: dropped 2 off-topic sub-agent finding(s)`) but: (a) it fires on *sub-agent* results only; specialist own-findings are ungated; (b) gap placeholders are findings (`RISK: No risks identified … publishing gap finding`); (c) recovery re-runs produce duplicate findings with new IDs.

**Log evidence:** the report's Risk Assessment section lists the same 5 sources twice (recovery pass re-added them); `sections[1].findings[5]` is titled `Risk analysis gap, insufficient source data` **with** sources attached; the TUI shows `SUB-AGENT RECONCILIATION: 8 contradiction(s)` across OPS/RISK/INNOVATE where the "contradictions" are between off-topic papers.

**Mechanism:** findings are the only thing the gate scores, and they enter with three unguarded properties: off-topic (no relevance score vs the question), hollow (gap placeholders carry sources=[] or borrowed sources), duplicate (recovery re-runs append, never dedupe against existing findings by content hash).

**Why it survived O1–O4:** O2's OC-3 made *source-binding* a schema requirement (good) but nobody gated *relevance* or *substantiveness*. O3's recovery loop re-dispatches on blocker class without dedupe.

**Fix:** (a) **relevance gate at construction** — reuse the `ImageRelevanceGate` pattern: score every finding's title+sources against the engagement subject + question; below-floor → reject with a typed `OFF_TOPIC` reason (no silent drop); (b) gap placeholders are **not findings** — they become `open_gaps` on the report only; (c) **content-hash dedupe** in the findings bus — recovery re-runs cannot double-add; (d) section assembly dedupes findings per section.

### D-09 · DATA VOID: metric-parse failure rendered as data

**Code site:** `hyperion/agents/specialists/financial_analyst.py:739,836,1090` and `market_analyst.py:676,775` — `assumptions=["… failed, parsing error — metric omitted"]` are emitted as structured metrics with empty name/unit; the report then renders a TAM row: `TAM: Name: TAM (Triangulated) · Unit: $ · Assumptions: CAGR triangulation failed, parsing error — metric omitted`.

**Log evidence:** TUI `MARKET … Step 7: CAGR triangulation` → the report contains the row above → `quality_gate.py:1340` DATA VOID: *"'Unknown' value(s) rendered as data, omit the row or re-query; never ship 'Unknown' as a data point."*

**Mechanism:** the metric model carries an empty/`Unknown` value with a narrative `assumptions` field; synthesis renders the row (it exists → it's data); the gate correctly refuses. The parse failure was never modeled as `absent`, only as `empty`.

**Fix:** metric parsing returns `None` (absent) on failure; synthesis **omits** absent metrics and adds a stated gap to `open_gaps`; a regression test asserts a parse-failed metric never appears in a section body (fail-first: currently the TAM row renders).

### D-10 · VERDICT CONTRADICTION: free-form narrative vs structured field

**Code site:** `hyperion/agents/support/quality_gate.py:1381` — the gate cross-checks the structured `recommendation` field against narrative language; the synthesis writes the narrative and the field independently.

**Log evidence:** `recommendation: conditional` in `task_synthesis_lead.json` while the body contains *'no-go'* → gate: *"recommendation is 'CONDITIONAL' but the narrative contains conflicting language ('no-go')."*

**Fix:** the narrative is generated **from** the structured field — one writer. The synthesis prompt receives `recommendation=CONDITIONAL` as a hard constraint ("your narrative must be consistent with this verdict; do not use absolute terms like no-go/unviable"); the gate keeps its check as a backstop. Regression test: a report whose body contains a conflicting verdict word fails construction, not the gate.

### D-11 · The 1200s specialist timeout discards completed pipelines

**Code site:** `hyperion/orchestrator.py:326` `SPECIALIST_TIMEOUT_SECONDS = 1200`.

**Log evidence:** journal: `task_sustainability_analyst` timeout:1200s, `task_operations_analyst` timeout:1200s, `task_regulatory_analyst` timeout:1200s (reframed → succeeded). TUI: OPS completed 7 real steps (sub-agent findings: capacity utilization, supplier concentration, logistics costs, contradiction resolution at 15:06:07) then `Step 7: Designing operational KPI dashboard` → final completion call at 15:16:14 — **the specialist hit the 1200s wall on its last LLM call**. Synthesis ran with `missing dependency outputs: ['task_operations_analyst', 'task_competitive_intel', 'task_sustainability_analyst']`.

**Mechanism:** a specialist publishes its model **once, at the end** (`run()` returns the model). Every intermediate step's findings live in memory. If the final completion call exceeds the wall, the whole pipeline — including sub-agent evidence already recorded — is marked FAILED and its output slot never fills. 20 minutes of real work, lost at the boundary.

**Fix:** (a) **incremental publish** — findings publish to the bus as they're produced (most specialists already do via `_publish_finding`; ensure the *model* is checkpointed too: write a partial task output at each step boundary); (b) the timeout for the *final* completion is extended or the completion call is retried once before the task is marked failed; (c) journal records a typed reason (`timeout_at_final_completion`) so recovery can re-run only the completion, not the pipeline.

### D-12 · COMPETE `content` UnboundLocalError — the happy-path crash

**Code site:** `hyperion/agents/specialists/competitive_intel.py:725` `content = read_result.markdown or read_result.content` (bound only inside the Jina `if`) · `:743` `key_data=content[:500]` (consumed whenever `page_data` is set — including when **Obscura** succeeded and `content` was never bound).

**Log evidence:** TUI `14:56:52 ✗ ERROR cannot access local variable 'content' where it is not associated with a value` — right at COMPETE's discovery step. The task never produced an output slot; synthesis ran without competitive_intel.

**Mechanism:** the Obscura path (`:715-722`) sets `page_data` from `fetch_result` but never binds `content`; the Jina fallback binds `content` only on success. When Obscura succeeds — the happy path — line 743 raises. Identical disease to O3's D-A (`_log` arity): **crashes exactly when the tool works.**

**Why it survived O1–O4:** tests mock extraction to return empty, so the success path is never exercised (the same note as O3 D-A: "tests mock discovery to return empty").

**Fix:** bind `content` in both paths (or read from `page_data["content"]`), and add a test that runs the scrape loop with a **succeeding** Obscura mock — fail-first.

### D-13 · No visibility — provider metrics, cost report, mid-run telemetry

**Code site:** `hyperion/search/cost.py` (P9 cost report) + `cli.py:172-178` (`/metrics` command) + `boot.py:737-739` — exist; **nothing printed them in the run**; the TUI log ends at `/export` with no cost/status panel.

**Mechanism:** a proprietary system whose operators cannot see provider health, budget burn, or corpus state during a 68-minute run is flying blind — this whole audit was reconstructed from a docker dump and a ledger. The `/metrics` command requires the operator to know it exists; the run never surfaces it.

**Fix:** (a) **boot probe panel** — at engagement start, test all 4 paid keys + endpoints + SearXNG replica health; print a table: `provider, key_ok, endpoint_ok, last_error`; (b) **run-end cost report always printed** (P9) with per-provider calls/results/errors/cooldowns; (c) **mid-run telemetry line** every N minutes: `provider calls, corpus per class, budget used/total`; (d) `/status` surfaces the same live.

### D-14 · Recovery is blind — re-runs the same specialist over the same pool

**Code site:** `hyperion/orchestrator.py:2569` (DATA VOID → responsible agent), recovery pass machinery from O3.

**Log evidence:** TUI `RECOVERY pass 1: PLACEHOLDER_VALUE → risk_analyst` → risk_analyst re-ran over the same thin/off-topic pool → 19 findings, mostly duplicates of the 5 existing sources → `QUALITY iteration 2/3: score=2.6` (dropped) → `RECOVERY pass 1 discarded (score 2.65 not ≥ best+0.05) — keeping best, degrading` → BLOCKED.

**Mechanism:** recovery re-dispatches the responsible agent with a prompt directive, but the agent's *inputs* (search pool, extraction URLs) are unchanged. The score cannot improve because the input is the problem — the system says so itself. Recovery needs to know *why* the agent failed (typed failure) and change the right input: dead pool → escalate to paid chain; paywall URLs → wayback route; thin findings → relevance-broaden with different provider.

**Fix:** recovery consults a **typed-failure → remedy table** (D-06/D-08/D-11 outputs feed it): `EVIDENCE_THIN → re-run with paid-chain-first search + broader queries`; `TIMEOUT_AT_FINAL → re-run completion only`; `PROVIDER_DEAD → skip provider, use next tier`; and **dedupes** recovered findings against the existing bus (D-08c). Recovery only counts as a pass if the score improves; otherwise it must **not** re-run the same agent a second time with the same inputs (add a per-run recovery budget: 1 pass per blocker class, then degrade honestly — which already exists, keep it).

---

## 4. The fix plan (W0 → W8, one defect at a time)

Each W-step: minimal patch, provenance comments preserved, **test that fails before / passes after**, VERIFY command, stop after each.

### W0 · D-01 — fix the two stale paid adapters
**Files:** `hyperion/search/adapters/you.py`, `hyperion/search/adapters/yep.py` (replace with the verified code from `docs/YOU_YEP_API_FINDINGS.md`; bring that doc + `scripts/check_you_yep_search.py` into the repo).
**Test (fail-first):** `tests/test_search_layer.py` + new `tests/test_paid_adapters_live.py` — you/yep adapters against the real endpoints with the real `.env` keys (marked `@pytest.mark.live`, run in WSL only); assert ≥1 result each and `engine in {you.com, yep}`.
**VERIFY:** `python scripts/check_you_yep_search.py "india manufacturing competitiveness 2026"` → HTTP 200 × 2 with ≥5 results each.

### W1 · D-02 + D-03 — web-class quality trigger + corpus tagging
**Files:** `hyperion/tools/searxng.py` (trigger at `:1610`; tag results with `web_class`), `hyperion/tools/source_classifier.py` (exists — wire a domain→class map incl. paywall hosts), `hyperion/search/types.py` (add `web_class: bool` to `SearchResult`).
**Behavior:** a web-class query returns early only if web-class results ≥ `MIN_WEB_RESULTS=5`; else, after rotation + fan-out, the paid chain fires regardless of scholar rescue. Fan-out keeps rescuing but its results are tagged `web_class=False`, so they can never satisfy the web trigger.
**Test (fail-first):** mock `_search_with_rotation` + `_search_all_replicas` to return 3 scholar DOIs → assert the paid chain is called and the returned response is marked `retrieval_degraded` with the paid engines listed.
**VERIFY:** `python -m pytest tests/test_search_layer.py tests/test_overhaul5_web_trigger.py -q`.

### W2 · D-04 — no re-entry, recursion guard
**Files:** `hyperion/search/orchestrator.py` (add `skip_first`/paid-only chain), `hyperion/search/adapters/searxng.py` (re-entrancy guard), `hyperion/tools/searxng.py` (pass through the skip).
**Test (fail-first):** real orchestrator + real SearxNGAdapter against a stub `SearxNGClient.search` that returns 0 → assert `SearxNGClient.search` entered exactly once per query and YouAdapter was called.
**VERIFY:** pytest (same file).

### W3 · D-05 — paid suspension + visibility
**Files:** `hyperion/search/suspension.py` (paid-provider 403 → 120s cooldown, permanent after 3), `hyperion/search/orchestrator.py` (TUI-visible `system.log` per paid attempt via bus publish or `logger` surfaced to TUI).
**Test (fail-first):** 3× 403 → after 3rd, provider suspended; before 3rd, retried. Assert TUI channel received the attempt lines.
**VERIFY:** pytest; then `python -m hyperion.eval.canaries` (existing suspension canary must stay green).

### W4 · D-06 — paywall pre-classifier + firecrawl load guard
**Files:** `hyperion/tools/unified_extract.py` (URL classify step before tier climb; typed `PAYWALL` result; cap concurrent firecrawl; `Can't accept connection` → typed retryable), `hyperion/tools/source_classifier.py` (paywall host list).
**Test (fail-first):** `extract_ladder([doi.org/…])` → returns typed `PAYWALL` reason with **zero** tier attempts against live hosts (mock tiers); ladder on a normal URL still climbs.
**VERIFY:** pytest; then live: `python -c` one-liner ladder call on a doi.org URL → `PAYWALL` in < 2s.

### W5 · D-07 — UNIFIED_EXTRACT tool for all specialists
**Files:** `hyperion/schemas/agents.py` (ToolName + grants for all 12), `hyperion/agents/base.py` (`_instantiate_tool`), the 12 specialists' direct scrape sites → `get_tool(ToolName.UNIFIED_EXTRACT)`.
**Test (fail-first):** every `AgentSpec.tools` contains `UNIFIED_EXTRACT` (schema assertion test).
**VERIFY:** pytest; `python -m hyperion.eval.ci_gate`.

### W6 · D-08 + D-09 + D-10 — finding quality at birth
**Files:** findings construction path (`hyperion/agents/base.py` `_publish_finding` or the funnel), relevance scorer (reuse image-gate), content-hash dedupe, metric-model `absent` semantics (`financial_analyst.py`, `market_analyst.py`), synthesis verdict constraint (`orchestrator.py` synthesis prompt), `quality_gate.py` backstops stay.
**Tests (fail-first):** (a) off-topic finding rejected with `OFF_TOPIC`; (b) parse-failed metric never renders (TAM row absent, gap stated); (c) verdict word `no-go` in narrative with `CONDITIONAL` field fails at construction; (d) recovery re-run cannot duplicate findings (hash dedupe).
**VERIFY:** `python -m pytest tests/test_overhaul5_finding_quality.py -q`; `python -m hyperion.eval.canaries` (DATA VOID + verdict canaries still green).

### W7 · D-11 + D-12 — checkpointed specialists + COMPETE crash
**Files:** `hyperion/orchestrator.py:326` timeout handling (extend final-completion retry; typed `timeout_at_final_completion`), specialists' model publish → incremental checkpoint, `competitive_intel.py:715-745` (bind `content` in both paths).
**Tests (fail-first):** (a) a specialist whose final call exceeds the wall still yields a partial output with `reason=timeout_at_final_completion`; (b) COMPETE scrape loop with succeeding Obscura mock does not raise.
**VERIFY:** pytest; live WSL smoke on COMPETE Stage B only.

### W8 · D-13 + D-14 — visibility + typed recovery
**Files:** boot probe (`hyperion/tui/boot.py` or `hyperion/orchestrator.py` boot), run-end cost report always printed (`hyperion/search/cost.py` + call site), mid-run telemetry, recovery remedy table (`hyperion/orchestrator.py` recovery pass).
**Tests (fail-first):** (a) boot probe prints one row per provider with `key_ok/endpoint_ok`; (b) recovery with `EVIDENCE_THIN` chooses the paid-first remedy; (c) recovery budget: 1 pass per blocker class.
**VERIFY:** pytest; then **the live engagement gate (§5)**.

---

## 5. Definition of done

- `python -m pytest -q` green (minus known env-dependent: matplotlib/kaleido/render/yfinance/docker-only).
- `python -m hyperion.eval.canaries` green (existing 16 + new W0–W8 canaries: paid-live, web-trigger, paywall-classify, finding-quality, checkpoint).
- `python -m hyperion.eval.ci_gate` green.
- **Live gate (the outcome test O1–O4 never had):** one engagement on the original question (*how india can beat china in manufacturing?*) must show, in the TUI/ledger:
  1. **Paid chain fires with results**: ≥1 you/exa/tavily/yep record in the ledger, with a visible `system.log` line per paid attempt.
  2. **Web class alive**: preflight `web` ≥ 8 domains (GREEN per-class), and web findings cite non-paywall, on-topic domains.
  3. **Extraction > 0**: `stage=extraction` ledger records ≥ 10, and ≥1 finding cites extracted text (content_hash present).
  4. **No integrity blockers**: report ships with zero DATA VOID and zero VERDICT CONTRADICTION.
  5. **Score ≥ 3.0** and delivery (PDF) produced.
  6. **No `suspended_time=180` repeat-storm** for a single engine inside the run (P1 stays true).

---

## 6. Guardrails (what this overhaul does NOT do)

- Does not lower the corpus floor (8), the ship score (3.0), or delete any gate — the gate is correct; the input is the fix.
- Does not add keyed APIs for extraction (paid providers are **search-only**; extraction stays on the local ladder — free).
- Does not burn the Yep balance casually: Yep capped (default 30 calls/run, ~$0.12) and last in the chain.
- Does not touch the evidence ledger schema (it already has everything: `web_class` tagging goes on `SearchResult`, not the ledger).
- Does not convert gap placeholders into findings (O1's "honest, stated evidence limitation" stays the correct outcome for a genuinely thin corpus).
- Does not relax the 1200s wall — it checkpoints so the wall discards nothing.

---

## 7. Open decisions (locked by default, change if you disagree)

1. `MIN_WEB_RESULTS = 5` for the web-quality trigger (D-02).
2. Paid chain order = SearXNG → You → Exa → Tavily → Yep (your spec; all four live after W0).
3. Yep burn cap = 30 calls/run (~$0.12/run at $0.004) — bump if you want more depth at cost.
4. Fan-out keeps rescuing (option b, D-03) — tagged `web_class=False`, never satisfies the web trigger.
5. 403 on paid = 120s cooldown, permanent only after 3 consecutive failures (D-05).
