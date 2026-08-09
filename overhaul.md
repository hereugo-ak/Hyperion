# HYPERION OVERHAUL — Chief Auditor's Report and Phase-Wise Fix Plan

**Audit date:** 2026-08-10
**Auditor posture:** chief auditor — diagnosis and overhaul plan only; no code changed in this session
**Evidence:** full TUI log + Docker log of the 2026-08-10 18:46→19:28 UTC engagement ("should india build more inhouse space startups?"), repository snapshot `build 5b47371+dirty`, prior audits `docs/CHIEF_AUDIT_FIX0.3_ZERO_FINDINGS.md` and `docs/FIX0.3_RUNBOOK_2026-08-09_SEARCH_AGENT_STABILITY.md`
**Execution owner:** the next remediation session

---

## 1. Verdict

The core issue is **not** "SearXNG is down" and it is **not** "the models are weak." Those are symptoms.

**The core issue: Hyperion's control plane tracks tasks, not evidence. Evidence is not a first-class object anywhere in the system.**

Every loop in the pipeline — the DAG fan-out, the sub-agent respawns, the REFRAMER, the quality iterations — measures *work performed* (tasks dispatched, LLM calls made, queries reworded, retries attempted). The one quantity that determines whether a report can exist — *retrieved, source-bound evidence* — is measured exactly once, at the very end, by the Quality Gate's corpus floor. By that point the run has already spent 39 minutes and 775.7k tokens analyzing a vacuum.

This single design defect expresses itself as three concrete failures, and all three fired in the Aug-10 run:

1. **Capacity defect (search layer).** The system's only web corpus is anonymous, unauthenticated, single-egress-IP scraping: 4 crawler engines on the web profile (mojeek, mwmbl, brave-HTML, yep) and anonymous free APIs on scholar/reference (openalex, crossref, semantic scholar, wikipedia). After a week of daily runs from the same IP, the upstream bans are **persistent upstream state** — openalex returns 429 on the *first* query of the engagement, 1 second after the question is typed. Every engine 403/429s for the entire 40 minutes and never recovers. There is no authenticated search API, no egress proxy, and Gemini grounding is capped at 20 calls/day. **The retrieval layer had zero effective capacity before the first task was dispatched.**

2. **Provenance defect (data model).** Evidence never exists as a system object. Sub-agents flatten all search/extraction results into one prompt string (`sub_agent.py:414-488, 680`) and then ask the LLM to produce `KeyFinding` JSON — including the `sources` — from that text (`sub_agent.py:1381-1487`). `KeyFinding.sources` defaults to `[]` and is optional (`schemas/models.py:293`). A `Source` requires id/title/url/credibility, so LLM-minted sources usually fail validation (finding dropped) or are absent (finding sourceless). Specialists do construct real `Source` objects from SearXNG results (e.g. `market_analyst.py:331`) — but only when search returns results, which it didn't. Gap placeholders carry `sources=[]` by construction. **Net effect, exactly as observed: 86 collected findings → 0 parseable source domains → CORPUS FLOOR counts 0 and blocks.** The gate is not wrong. The pipeline genuinely produced zero citable evidence.

3. **Control defect (orchestration).** There is no evidence precondition anywhere in the graph. The Director fans out 16 tasks / 90 LLM calls with no corpus contract; every specialist runs its full 8-10 step LLM pipeline over the literal string `"No raw data available from tools."`; the REFRAMER rewords `competitive_intel` through 2 attempts × 3 variants and re-executes the full pipeline ~8 times against the same dead pool; "Sub-agent returned 1 findings" counts a gap placeholder as a finding; the sub-agent total ceiling is exceeded at `8/6`; synthesis refuses to run (its dependency `task_competitive_intel` failed), a floor-report fallback is assembled from the 86 sourceless findings, and the quality loop then *iterates twice polishing an evidence-free artifact* (2.95 → 3.2) before the corpus floor — checked for the first time — terminates the run.

**Answer to "search layer or system level?":** the trigger is the search layer; the catastrophe is system level. The three defects are **multiplicative**. Dead capacity × LLM-transcription provenance × no evidence gating = a run that is guaranteed to burn maximum tokens and produce zero domains. This is why three rounds of patch fixes changed nothing: each patch treated one factor while the multiplicative chain stayed intact. Fix any one factor alone and the run still fails — fix capacity only, and provenance still yields ~0 domains; fix provenance only, and the run still burns 40 minutes against a dead pool; fix gating only, and you still have no corpus.

---

## 2. Run Autopsy — what the Aug-10 logs actually prove

| # | Evidence (log) | What it proves |
|---|---|---|
| A-1 | Boot `SEARCH ✓ scholar:ok · reference:ok · web:ok · valkey:ok` | Readiness probe measures *process*, not *corpus*. All engines were already effectively banned. |
| A-2 | Docker: first query of the engagement (`openalex`, 18:49:42 UTC) → `429 (suspended_time=180)` | Upstream rate-limit state is **persistent across restarts**. The IP is pre-banned from a week of runs. |
| A-3 | Docker, 18:49→19:28 UTC: brave 429 ×6+, mojeek 403 ×5+, yep 403 ×4+, openalex 429 ×4+, wikipedia 429 ×1 — spanning the whole run | Not a transient blip. The fleet had ~zero yield for 40 consecutive minutes. The 180s suspensions lapse and immediately re-trip. |
| A-4 | `CONSUMER ✓ complete: 0 personas, 0 segments, confidence=low`; `TECH ✓ 0 vendors`; `MARKET ✓ TAM , confidence=low` — all within seconds of dispatch | Specialists "complete" with empty domain models. The word "complete" means "the function returned," not "evidence was found." |
| A-5 | `SUB-AGENT RESPAWN (broadened, reason=zero_findings)` → `RETRY EXHAUSTED … 1 finding(s), 1 gap(s)` — repeated for MARKET, RISK, TECH, REGULATORY, INNOVATE, COMPETE | fix0.3 F-07 respawn exists and works mechanically — but broadening a query into a dead pool is recovery theater. The "1 finding" is the synthetic gap. |
| A-6 | COMPETE: `REFRAMER … attempt 1/2 → 2/2`, then ~8 full competitive-intel restarts, each ending `No competitors identified from search, publishing gap finding` / `ESCALATION suppressed (duplicate)` | The loop changes *wording*, never *capacity*. ~8 full pipelines × multi-step LLM analysis of nothing. |
| A-7 | `SUB-AGENT total budget reached (8/6)` | The ceiling of 6 is exceeded by 8. Budget accounting is still broken (spawn-count vs yield conflation). |
| A-8 | RISK: `completed with 35 findings` incl. precise figures ("15-20% brain drain", "12-24 months sales cycle") while all its sub-agents returned gaps | Parametric hallucination at scale. Confident, specific, sourceless numbers — the most dangerous output the system can produce. |
| A-9 | `SYNTHESIS: no FinalReport produced — building floor-report fallback from 86 collected findings` → corpus floor counts **0 distinct domains** | The smoking gun. 86 findings, zero attached URLs. Provenance is LLM-transcription-based and collapsed entirely. |
| A-10 | `QUALITY: CORPUS FLOOR blocker active — running targeted retrieval escalation with floor 8` → `escalation failed` (19:28:07, after 38 minutes) | fix0.3 F-10 escalation wiring exists and fires — at the *end*, against the still-dead pool. Retrieval escalation cannot retrieve when capacity is zero. |
| A-11 | Final: `status error · elapsed 39:02 · tools 176 · tokens 775.7k` | Total cost of discovering "search is dead": three quarters of a million tokens. |
| A-12 | Score displayed as `3.2/4.0` in one line and `2.95/5.0` in another; boot POLICY shows `quality_source_floor: 3` vs gate floor 8 | Score-scale inconsistency and the two-floor tension from the prior audit (E-11) are still present. Minor, but indicative. |

**Deployment-drift note:** unlike the Aug-9 incident, this run's fingerprint (`5b47371+dirty`, budget cap 600, sub-agent ceiling 6) matches the repo — **stale deploy is ruled out this time. The current code, as written, produces this failure.** That is what makes this an overhaul, not another patch.

---

## 3. Why fix0.1 → fix0.3 didn't hold

The prior audits were largely *correct about mechanics*. Their fixes landed and are visible in this run: full-pool fan-out (`searxng.py:888-935, 1376-1391`), per-profile fail-fast (`:1314-1354`), budget cap 600 (`:482`), broadened respawns (F-07), corpus-floor retrieval escalation (`orchestrator.py:1968-1997`), format repair (`sub_agent.py:1450-1460`), de-rated boot smoke (`obs/health.py:101`).

They didn't hold because they share one blind spot: **they all assume retrieval capacity exists and that "search returned nothing" is a query problem.**

| fix0.x assumption | Reality in the Aug-10 run |
|---|---|
| Fan-out to scholar/reference rescues general queries ("API engines don't ban datacenter IPs") | Anonymous openalex/wikipedia/crossref *do* rate-limit shared/cloud IPs, and the fan-out now routes general traffic onto them — they 429 within the first minutes. The safety net is burned too. |
| Broadened respawn recovers zero-findings | Broadening rewords the query. The failure class was ENGINE_BLOCKED, not BAD_QUERY. Rewording a dead engine is pure token spend. |
| Corpus-floor escalation recovers thin evidence | It fires once, at minute 38, against the same zero-capacity pool, recovers 0, terminates. |
| Quality iterations improve the report | Both iterations polished a floor-report built from sourceless findings. Score moved 2.95→3.2 with zero new evidence. |
| The gate will catch bad output | It does — after 775k tokens. A gate that only fires at the end is a post-mortem, not a control. |

And the two things no fix cycle touched:

1. **No authenticated retrieval capacity was ever added.** The web profile is still the same 4 scrapers; `.env.example` still has no Brave/Tavily/Exa/SerpAPI/Bing key slot; docker-compose still has one egress IP and no proxy. You cannot fix a capacity problem with orchestration patches.
2. **Provenance was never bound to retrieval.** Findings still get sources from LLM JSON transcription. Even on a *healthy* search day, the corpus floor would chronically undercount because the LLM is not a faithful URL copier — and the Aug-10 mechanism (86 findings → 0 domains) would keep producing surprise blocks.

---

## 4. The Overhaul — target architecture: the Evidence Control Plane

Hyperion is a proprietary research system, not a wrapper. The overhaul therefore does not "add more fallbacks" — it re-foundations the pipeline on one principle:

> **Evidence is a first-class object. Every stage of the graph is gated on it, every finding is bound to it, every loop decision is driven by its delta.**

### Five architectural invariants (non-negotiable after the overhaul)

- **I-1 · Evidence objects exist.** Every retrieved URL becomes an `Evidence` record (url, domain, title, snippet, content_hash, engine/tool, profile, fetched_at) in a run-scoped **Evidence Ledger** *before* any LLM sees anything. Search results are never again flattened into prompt text without leaving a structured trace.
- **I-2 · Corpus contract before fan-out.** No DAG executes without a preflight **Corpus Contract** (canary probes across source classes → GREEN/AMBER/RED). RED terminates in under 60 seconds at near-zero token cost with a typed `INSUFFICIENT_EVIDENCE` diagnostic. The system must be *cheap when it's going to fail*.
- **I-3 · Findings are source-bound in code.** The LLM cites evidence IDs (`[E1], [E2]`); code maps IDs to `Source` objects. The LLM can no longer mint, drop, or mangle URLs. A substantive finding with zero evidence refs is typed `unverified_assertion` — never counted, never rendered. A gap is a separate type — never counted as a finding.
- **I-4 · Loops route on failure class and progress, not attempt count.** Every retrieval failure is classified (`ENGINE_BLOCKED` / `NO_RESULTS` / `EXTRACTION_FAILED` / `ANALYSIS_FAILED` / `TIMEOUT`) and routed differently (quarantine+reroute / broaden once / alternate extractor / format-repair once / typed terminal). The loop metric is `Δ domains + Δ evidence` per iteration — an iteration with zero progress is a stop signal, not a retry reason.
- **I-5 · The corpus floor is measured continuously, not discovered at the end.** The Evidence Ledger exposes `distinct_domains()` at every stage boundary. The Quality Gate keeps its hard block as defense-in-depth but should never again be the *first* component to notice an empty corpus.

---

## 5. The Overhaul Loop (the remediation process — iterate until green)

This is the graph-based loop for the **fixing session itself**. Every phase is a node; every node runs the same micro-loop; the master loop iterates on 5 KPIs until all are green for 3 consecutive live runs.

```
┌──────────────────────────── MICRO-LOOP (per fix item) ───────────────────────────┐
│                                                                                  │
│   PROBE ──► ROOT ──► FIX (one edge only) ──► UNIT ──► CANARY ──► KPI GATE        │
│     ▲                                                            │               │
│     │                                              pass ◄────────┤               │
│     │                                              fail          ▼               │
│     │                                          same failure → REVERT/REWORK      │
│     │                                          new failure  → open new node      │
│     └──────────────────────── 3 failed passes → FREEZE + escalate ───────────────┘
│
└──────────────────────────────────────────────────────────────────────────────────┘

┌──────────────────────────── MASTER LOOP (per live run) ──────────────────────────┐
│                                                                                  │
│  RUN canary engagement ──► measure KPI-1..5                                      │
│       │                                                                          │
│       ├─ all green ×3 consecutive runs ──► DONE (Definition of Done, §8)         │
│       │                                                                          │
│       └─ any KPI red ──► enter the FAILED KPI's phase node (§6)                  │
│                           run micro-loop ──► re-run master loop                  │
│                                                                                  │
│  INVARIANT: one edge per pass. Never batch fixes — batching destroys causal      │
│  attribution, which is exactly how fix0.1–0.3 lost the thread.                   │
└──────────────────────────────────────────────────────────────────────────────────┘
```

### The 5 KPIs (the loop's iteration condition)

| KPI | Green threshold | Measured from |
|---|---|---|
| **KPI-1 · Time-to-first-evidence** | < 60s from question | Evidence Ledger first-write timestamp |
| **KPI-2 · Retrieval yield** | ≥ 8 distinct domains in the ledger **before synthesis** | Ledger `distinct_domains()` at synthesis boundary |
| **KPI-3 · Provenance binding** | 100% of substantive findings carry ≥ 1 evidence URL; gaps counted separately | Ledger + finding audit at run end |
| **KPI-4 · Failure cost** | A fully-degraded run terminates < 5 min and < 50k tokens with a typed terminal state | Run telemetry |
| **KPI-5 · Report integrity** | Healthy run ships with 0 integrity blockers, consistent verdict, confidence = f(coverage) | Quality Gate diagnostic JSON |

---

## 6. Phase-wise fix plan

Phases are ordered by dependency: **you cannot verify provenance (P3) or loops (P4) without capacity (P1), and you cannot attribute anything without instrumentation (P0).** Do not reorder. Each phase lists: goal · concrete changes (with code sites) · probe · exit gate · loop-back edge.

### PHASE 0 — Instrument the evidence truth
**Goal:** make evidence measurable at every stage boundary. No behavior change yet — without this, every later phase is unfalsifiable.

**Changes:**
1. Add the run-scoped **Evidence Ledger** (`hyperion/tools/evidence_ledger.py`, new): `record(Evidence)`, `distinct_domains()`, `by_stage(stage)`, per-engine/per-tool counters. Back it with the engagement ID; persist a snapshot into `reports/diagnostics/` at run end.
2. Wire ledger writes into: `SearxNGClient.search` (every result URL, with engine + profile), the Jina/grounding fallbacks, the extraction ladder (`sub_agent.py` `_gather_raw_data` and `UnifiedExtract`), and each specialist's `self._sources` construction.
3. Split the counters the TUI shows: `substantive findings` vs `research gaps` vs `evidence items` vs `distinct domains` — kill the "returned 1 findings" lie at the display layer (`sub_agent.py:1489+` gap path, `base.py` spawn logging).
4. Emit one structured JSON event stream per engagement (the log you *wish* you had for this audit).

**Probe:** replay a degraded run; confirm the ledger shows 0 domains from minute 1 and the event stream shows every engine 403/429 as typed events.
**Exit gate:** at any timestamp you can answer "how many distinct domains, from which engines, bound to which findings" — the Aug-10 autopsy above must be reproducible from telemetry alone, not from reading logs.
**Loop-back:** if the ledger itself is incomplete, fix instrumentation before touching anything else.

### PHASE 1 — Retrieval capacity overhaul (the search layer)
**Goal:** give the system *real* capacity. This is the phase no fix cycle ever did. Constraint: stays within your engine policy — amend W-11 *in writing* where needed.

**Changes (in priority order):**
1. **Add one authenticated web-search API as a first-class Hyperion tool** — not as a SearXNG engine, as a tool (`ToolName.WEB_SEARCH_API`), wired into `sub_agent.py` discovery alongside SearXNG and into the specialist search steps. Options in order of fit for an agentic system: **Brave Search API** (has a free tier, official JSON API, trivially keyed), **Tavily** or **Exa** (built for agents, return clean text). One is enough to change the failure mode; two gives redundancy. Key via `.env` (`HYPERION_BRAVE_API_KEY` / `HYPERION_TAVILY_API_KEY`). This single change breaks the "anonymous scraping is the corpus" trap.
2. **Stop feeding the banned scrapers.** In `searxng_settings.web.yml` (via `hyperion/infra/searxng_profiles.py` generator): disable `yep` and `mojeek` (categorical 403s from datacenter IPs — they will never work from WSL/Docker egress), keep `mwmbl` + `brave` only behind the health circuit, and consider `duckduckgo`/`bing`/`startpage`/`qwant` **only** with a documented W-11 policy amendment in `ARCHITECTURE.md`.
3. **Join the polite pools.** openalex: add `mailto=` (config: `HYPERION_CONTACT_EMAIL` is already interpolated into the UA suffix — openalex wants the param); crossref: same via UA; wikipedia: descriptive UA with contact. This moves you from the anonymous bucket to a 10× higher rate bucket.
4. **Egress decision, made explicitly.** Either a rotating residential proxy for the SearXNG replicas (`outgoing.proxies:` + env), **or** a written decision that SearXNG is reference/scholar-only and all general web goes through the keyed API from step 1. Both are legitimate. The illegitimate option is the status quo: anonymous scraping from one pre-banned IP with no decision record.
5. **Boot smoke goes local-only.** `obs/health.py:62, 92-101`: readiness = `/config` + persisted engine health. Zero upstream traffic at boot. The corpus probe belongs to the Phase-2 preflight, not to boot.
6. **Cooldown sweep at boot** — verify the fix0.3 F-04 TTL sweep actually landed in `engine_health.py`; stale 24h suspensions must not poison a fresh process. Cap max suspension at 4h.

**Probe:** 3 canary queries × 3 consecutive boots, with all 4 web scrapers force-disabled: each returns ≥ 5 distinct domains; `docker logs` shows **zero** 403/429 during the canary.
**Exit gate:** KPI-1 and KPI-2 green on the canary. If no API key is procurable, the gate is: general queries served by scholar/reference + Jina + grounding with ≥ 5 distinct domains and no engine 403s — and the RED preflight (Phase 2) must be demonstrated to fail cheap when even that is gone.
**Loop-back:** if yield is still < floor, the answer is *more capacity* (second keyed API, proxy) — never more retries.

### PHASE 2 — Corpus Contract preflight (control defect, part 1)
**Goal:** the system decides *whether it can research* before it spends a token on research.

**Changes:**
1. New module `hyperion/agents/support/corpus_preflight.py`: fires a small fixed battery of canary probes (one per source class: web-API, scholar, reference, grounding) at engagement start; reads the Evidence Ledger; computes `CorpusContract{min_domains=8, min_evidence_items, per_class_status}` → **GREEN** (full DAG) / **AMBER** (reduced DAG: retrieval-first task order, reduced sub-agent budget, skip delivery agents until contract met) / **RED** (terminal).
2. RED path: write the typed `INSUFFICIENT_EVIDENCE` diagnostic (which classes are down, since when, what was probed), notify the TUI, **stop**. Target: < 60s, < 5k tokens. This converts the Aug-10 39-minute/775k-token failure into a 1-minute/5k-token failure with a *useful* artifact.
3. Re-probe the contract at the mid-run boundary; if the fleet collapsed mid-run, degrade to AMBER behavior instead of letting specialists hallucinate (see A-8).
4. The Director's DAG builder takes the contract as an input — no more unconditional 16-task fan-outs (`engagement_director.py` / `orchestrator.py` DAG build site).

**Probe:** integration test with retrieval fully mocked-dead: engagement terminates RED in < 60s; router receives ≤ 2 LLM calls.
**Exit gate:** KPI-4 green. AMBER mode demonstrably re-orders retrieval before analysis.
**Loop-back:** if RED mis-fires on a healthy stack, tune the canary battery — never delete the gate to stop the noise.

### PHASE 3 — Retrieval-bound provenance (provenance defect)
**Goal:** make the Aug-10 "86 findings → 0 domains" mechanism structurally impossible.

**Changes:**
1. **`Evidence` ↔ `Source` binding in code.** In `sub_agent.py`: `_search_searxng`/`_search_jina`/extraction ladder create `Evidence` records (ledger, P0) *and* per-finding `Source` candidates. The raw_data prompt block carries evidence IDs (`[E3] title — url — snippet`). `_analyze_and_produce_findings` (`sub_agent.py:1381-1487`) maps the LLM's cited IDs to real `Source` objects; cited-ID ∉ ledger ⇒ dropped; finding with zero valid citations ⇒ typed `unverified_assertion`.
2. **Schema change** (`schemas/models.py:271-308`): split `KeyFinding` into `EvidenceFinding` (requires ≥1 bound source, enforced by validator) and `AnalysisGap` (a different type with its own channel — never in finding counts, never in floor-reports). Keep `KeyFinding` as the wire-compatible base if the refactor must be incremental, but the validator must reject sourceless substantive findings.
3. **Specialists:** the `self._sources` pattern (`market_analyst.py:331, 1481`) becomes mandatory — every specialist's final `_publish_finding` attaches search-derived sources; and **if a specialist's search steps returned zero evidence, it emits `AnalysisGap` and returns before any analysis LLM call.** No more 10-step Porter/VRIO/Monte-Carlo over `"No raw data available from tools."` This is where the 775k tokens went; this is where they're reclaimed.
4. **Floor-report** (`orchestrator.py:2264-2373`): builds only from `EvidenceFinding`s; if none exist, there is no floor report — the run is terminal `INSUFFICIENT_EVIDENCE`, and *that* is the artifact.
5. Kill the remaining `"Parse error"`-value sites with the retry-and-omit helper (fix0.3 F-11 — verify it landed; the banned-filler validator already proved it can catch downstream leaks).

**Probe:** unit run with mocked search returning 12 URLs across 9 domains ⇒ every substantive finding carries ≥ 1 of those URLs; ledger `distinct_domains() == 9`; corpus floor sees 9. Second run with mocked-dead search ⇒ 0 substantive findings, 0 analysis LLM calls, N typed gaps.
**Exit gate:** KPI-3 green (100% binding). KPI-2 and KPI-3 now move together by construction.
**Loop-back:** if the LLM chronically cites IDs that don't exist, constrain the output schema (enum-constrained citation field) — do not relax the binding.

### PHASE 4 — Progress-driven loop controller (control defect, part 2)
**Goal:** replace every attempt-count loop with a failure-class-routed, progress-signaled loop.

**Changes:**
1. **Failure taxonomy everywhere.** `SubAgentRunner.run` returns a typed outcome (`SUCCESS` / `NO_EVIDENCE` / `RETRIEVAL_DEGRADED` / `ANALYSIS_FAILED` / `TIMEOUT`) with dependency + attempt + elapsed metadata. The typed-outcome enum from the prior audit (F-01) lands for real this time, and `return_exceptions=True` sites wrap exceptions in task-identified envelopes.
2. **Routing table, not retry table:** `ENGINE_BLOCKED` ⇒ quarantine engine (engine_health circuit) + reroute to a different source class — *never* reword-and-retry the same class. `NO_RESULTS` (engines healthy) ⇒ at most one broaden respawn (the F-07 path is correct *for this class only*). `EXTRACTION_FAILED` ⇒ next extractor tier. `ANALYSIS_FAILED` ⇒ one format repair, then typed failure. `TIMEOUT` ⇒ halved-scope respawn once, then terminal.
3. **REFRAMER health-gate** (`orchestrator.py:1106-1260`): reframe only when the preferred source class is GREEN; add a *global* reframe budget per engagement. A `task_failed` whose failure signal is retrieval-degraded routes to capacity recovery, not to the reframer. (A-6's ~8 restarts die here.)
4. **Progress signal:** each orchestration iteration records `Δ domains + Δ evidence`; an iteration with zero delta consumes the *progress* budget (default 2 consecutive zero-delta iterations ⇒ terminal), not just the attempt budget.
5. **Deterministic escalation bypasses the LLM cap** — verify fix0.3 F-12 actually landed in `engagement_director.py:488+`; the Aug-10 log shows escalation firing, so confirm deterministic retrieval actions never wait on the 12-evaluation LLM cap.
6. **Fix budget accounting:** `SUB-AGENT total budget reached (8/6)` (A-7) — the ceiling is decorative. Make the total ceiling a hard invariant and make slots yield-aware (a gap-only sub-agent releases its slot; concurrent cap 3 / total cap 6).

**Probe:** forced-dead-pool integration test ⇒ terminal in < 5 min, < 50k tokens, exactly one typed diagnostic, **zero** reframer runs, zero broadened respawns. Forced-`NO_RESULTS` test ⇒ exactly one broaden. Forced-malformed-JSON ⇒ exactly one repair.
**Exit gate:** KPI-4 stays green against every fault-injection scenario; healthy runs show positive progress per iteration or a clean terminal.
**Loop-back:** any new "same-failure-twice" pattern in telemetry ⇒ the taxonomy missed a class; add it, don't bump a retry count.

### PHASE 5 — Verification repositioned (gate becomes a verifier)
**Goal:** the Quality Gate stops being the first detector of an empty corpus.

**Changes:**
1. Corpus floor reads from the Evidence Ledger at three boundaries — pre-synthesis, pre-factcheck, pre-render (`quality_gate.py:1203-1231` keeps its hard block as Layer-4 defense). Pre-synthesis breach ⇒ Phase-4 retrieval loop or terminal; the floor-report path can no longer reach the gate with 0 domains.
2. Resolve the two-floor tension (`config.py:796-806` vs gate): document one contract — floor 3 governs *iteration effort*, floor 8 governs *deliverability* — and make the boot POLICY line print both explicitly. Fix the `3.2/4.0` vs `2.95/5.0` scale inconsistency in gate messaging (A-12).
3. Verdict and confidence are *computed* from measured coverage (`sourced_sections/total_sections`, `distinct_domains`, verification rate), not asserted by the synthesis LLM — kills the VERDICT CONTRADICTION / DISHONEST CONFIDENCE blocker class at the source (fix0.3 F-11c).
4. Fact-check consumes ledger evidence for claim→source verification instead of re-searching a dead pool.

**Probe:** corpus-floor breach is detected pre-synthesis in 100% of fault-injection canaries; blocked artifacts show one consistent verdict string across cover/summary/body.
**Exit gate:** KPI-5 green. The gate's corpus blocker becomes theoretically unreachable — if it ever fires, that's a P0 bug in Phases 0–4, and the loop routes there.

### PHASE 6 — Regression canaries + CI lock
**Goal:** the failure modes of Aug-9/Aug-10 become permanent integration tests. This is what makes the fix *stay* fixed — the piece fix0.1–0.3 all skipped.

**Changes:**
1. Fault-injection suite (live-stack canaries, runnable via one command): `all-engines-403` · `429-storm` · `healthy` · `malformed-JSON` · `sub-agent-timeout` · `budget-exhaustion` · `grounding-key-missing`. Each asserts its phase gates above.
2. The 5 KPIs recorded per run into `reports/diagnostics/` and diffed run-over-run; a KPI regression auto-opens the owning phase node.
3. Weekly: full healthy engagement; assert ≥ 8 domains pre-synthesis, 0 blockers, 0 "1 findings"-gap displays, and total tokens within an agreed envelope.

**Exit gate:** 3 consecutive green master-loop runs ⇒ the overhaul is done. Any regression ⇒ micro-loop on the failed KPI's phase.

---

## 7. Runtime graph after the overhaul (what the engagement loop becomes)

```
                 ┌────────────────────────────────────────────────────┐
                 │                    ENGAGEMENT                       │
                 ▼                                                    │
        CORPUS PREFLIGHT (canary probes → Evidence Ledger)            │
                 │                                                    │
     ┌───────────┼─────────────┐                                      │
     ▼ RED       ▼ AMBER       ▼ GREEN                                │
  TERMINAL    reduced DAG    full DAG                                 │
  INSUFFICIENT     │            │                                     │
  _EVIDENCE        ▼            ▼                                     │
  (<60s,       RETRIEVE ◄───────────────┐                             │
   <5k tokens)     │                    │                             │
                   ▼                    │ Δ domains/Δ evidence = 0    │
              ledger-gate: evidence ≥ contract? ──► reroute source ───┘
                   │ yes                │    class / broaden once /
                   ▼                    │    quarantine engine
              ANALYZE (specialists)     │
                   │                    │
                   ▼                    │
              SYNTHESIZE (EvidenceFindings only)                      │
                   │                    │                             │
                   ▼                    │ gap-closure: NEW evidence?  │
              FACTCHECK ⇄ VERIFY ───────┘ (else terminal, typed)      │
                   │
                   ▼
              QUALITY GATE (verifier; corpus floor already known-green)
                   │
                   ▼
                 RENDER
```

Invariants at runtime: no analysis before evidence · no finding without a bound source · no loop without a progress signal · no failure without a typed terminal state · no render without the contract.

---

## 8. Definition of Done (all five KPIs, 3 consecutive live runs)

- [ ] KPI-1: first evidence lands < 60s after the question.
- [ ] KPI-2: ≥ 8 distinct domains in the ledger **before** synthesis on the India-space question (or an equivalent live question).
- [ ] KPI-3: 100% of substantive findings carry ≥ 1 ledger-bound URL; TUI shows `findings=N · gaps=M · domains=D` and never counts a gap as a finding.
- [ ] KPI-4: with retrieval force-disabled, the run terminates RED in < 5 min and < 50k tokens with a typed diagnostic — contrast: Aug-10 cost 39:02 and 775.7k tokens to learn the same fact.
- [ ] KPI-5: healthy run ships with 0 integrity blockers, consistent verdict, confidence derived from coverage.
- [ ] Zero 403/429 lines in Docker logs during a healthy canary (post-Phase-1 capacity).
- [ ] `SUB-AGENT total budget` never exceeds its ceiling; REFRAMER never fires against a RED source class.
- [ ] The §6 fault-injection suite passes and runs in CI.

---

## 9. Anti-patterns — what the next session must NOT do

These are the exact moves that consumed the last three fix cycles:

1. **Do not raise timeouts, token budgets, quality iterations, or retry counts.** Every one of those increases the cost of the same failure.
2. **Do not add more anonymous scraper engines to SearXNG.** Same IP, same bans, same 403s. Capacity comes from keyed APIs or different egress, not from a longer scraper list.
3. **Do not lower the 8-domain corpus floor or the 3.0 ship floor.** The gate is currently the only component telling the truth.
4. **Do not let the LLM attest, echo, or format sources.** Provenance is constructed in code from retrieved evidence. This single rule would have prevented the Aug-10 zero-domain block.
5. **Do not broaden, reframe, or respawn against a dead source class.** Rewording is a `NO_RESULTS` remedy; `ENGINE_BLOCKED` requires rerouting.
6. **Do not count gap placeholders as findings** — in logs, budgets, floor-reports, or gates.
7. **Do not fire boot smoke at upstream engines.** Readiness is local; corpus probing is the preflight's job.
8. **Do not "fix" the Quality Gate** (thresholds, iterations, messaging) as a substitute for fixing the pipeline.
9. **Do not batch fixes.** One edge per micro-loop pass, or you lose causal attribution again.
10. **Do not accept a "healthy" run defined by process readiness.** Healthy is defined by KPI-1..5 only.

---

## 10. Code-site appendix (verified against this snapshot)

| Concern | Site |
|---|---|
| Corpus floor domain counting (report sources only) | `hyperion/agents/support/quality_gate.py:1203-1231` |
| Floor-report fallback from collected findings | `hyperion/orchestrator.py:2264-2373` (invoked `:2664-2672`) |
| Corpus-floor retrieval escalation (post-hoc) | `hyperion/orchestrator.py:1968-1997` |
| REFRAMER (wording loops, attempt-capped) | `hyperion/orchestrator.py:338, 1106-1260`; `hyperion/tools/task_reframer.py` |
| Sub-agent raw data flattened to text (evidence destroyed) | `hyperion/agents/sub_agent.py:414-488, 680` |
| LLM-transcribed finding sources (provenance hole) | `hyperion/agents/sub_agent.py:1381-1487` |
| Gap placeholder fabricated as a KeyFinding | `hyperion/agents/sub_agent.py:1489+` |
| `KeyFinding.sources` optional, defaults `[]`; `Source` all-required | `hyperion/schemas/models.py:240-263, 271-308` |
| Specialist real-Source construction (correct pattern, search-dependent) | `hyperion/agents/specialists/market_analyst.py:331-336, 1481` (same pattern across specialists) |
| SearXNG call order: profile fail-fast → rotation → full-pool fan-out → Jina → grounding | `hyperion/tools/searxng.py:1290-1418` |
| Full-pool fan-out (fix0.3 F-03a, landed) | `hyperion/tools/searxng.py:888-935, 1376-1400` |
| Budget caps (600 global / 200 owner) | `hyperion/tools/searxng.py:473-482, 1245-1288` |
| Web profile = 4 anonymous scrapers | `searxng_settings.web.yml:44-68` (generator: `hyperion/infra/searxng_profiles.py`) |
| Scholar/reference = anonymous free APIs | `searxng_settings.scholar.yml`, `searxng_settings.reference.yml` |
| One egress IP, no proxy, limiter mounted-but-disabled | `docker-compose.yml`; `server.limiter: false` in all three settings files |
| Boot smoke (de-rated to mwmbl, still live traffic) | `hyperion/obs/health.py:62, 92-101` |
| Two-floor tension + ship floor | `hyperion/config.py:792-814` |
| Gemini grounding gating + 20/day ledger | `hyperion/tools/grounded_search.py`; `.env.example:34-42` |
| No keyed web-search tool exists | `.env.example` (no Brave/Tavily/Exa/SerpAPI slot); `hyperion/tools/` has no such tool |
| Director LLM escalation cap (verify F-12 landed) | `hyperion/agents/engagement_director.py:488+` |
| Engine health persistence / cooldown sweep (verify F-04 landed) | `hyperion/tools/engine_health.py` |

---

## 11. Closing note to the operator

The Aug-10 run is the most useful failure the system has produced, because deployment drift is ruled out: the code as-written, with all three fix cycles applied, deterministically converts "a week of upstream bans" into "39 minutes, 775.7k tokens, 86 sourceless findings, 0 domains, no report." Nothing is intermittent. Every link in the chain is in this document with a code site.

The overhaul is six phases, but the posture change is one sentence: **stop orchestrating tasks and start orchestrating evidence.** If the next session does only three things, they are: **(1)** wire one authenticated search API as the primary web path (Phase 1.1), **(2)** bind findings to retrieved evidence in code and gate the DAG on a corpus preflight (Phases 2–3), **(3)** make every loop route on failure class and progress instead of rewording (Phase 4). Everything else is verification that those three hold.
