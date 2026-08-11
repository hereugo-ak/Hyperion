# OVERHAUL4 — Root-Cause Fix: Retrieval Death-Spiral + Empty-Report Assembly

**Date:** 2026-08-11
**Status:** Plan (phase-wise), not yet applied
**Scope:** retrieval pacing & query hygiene, synthesis report assembly, corpus-floor semantics, recovery escalation
**Companion docs:** overhaul.md (P1–P6), overhaul2.md (S1–S12), overhaul3_audit.md (W1–W4)

---

## 1. The problem, stated in one paragraph

After three overhauls the runs still die with the *identical* terminal state —
`CORPUS FLOOR: only 0 distinct source domain(s) (minimum 8)` → BLOCKED — while
the evidence ledger of the very same run holds **2,838 records across 542
distinct domains** (crossref 2059, arxiv 277, openalex 294, mwmbl 160,
semantic scholar 48; `eng_8b3c55112d20/evidence_ledger.json`).

The quality gate is not wrong: it counts what the **report cites**, and the
report cited nothing because it was **assembled empty**. The retrieval layer
rate-limits itself into a 180-second death spiral, so the web class is dead
(`web=0d/0e` in every preflight since Aug 9), and the synthesis layer omits
every section whose deep-tier narrative LLM call fails or returns too short a
body — so a report built over real findings still reaches the gate as a shell.
The overhauls kept adding machinery to the gate/recovery loop (the alarm) and
never fixed the two broken inputs (the fire): **queries+rate-limits** and
**findings→sections assembly**.

---

## 2. Evidence trail (2026-08-04 → 2026-08-11)

| Run | findings_collected | task_outputs | Report state | Blocker |
|---|---|---|---|---|
| `blocked_eng_217c6b3ff979` (08-10) | 87 | 14 | **empty** (0 sections, 0 KF, 0 domains) | CORPUS FLOOR 0 |
| `blocked_eng_8b3c55112d20` (08-11) | 47 | 10 | **empty** (0 sections, 0 KF, 0 domains) | CORPUS FLOOR 0 |
| `blocked_eng_d7e007cb43bf` (08-11) | 50 | 14 | has sections (Market Landscape 1182 chars) | DATA VOID + VERDICT CONTRADICTION + no risk |
| `blocked_eng_7fd0b98983fc` (08-09) | 13 | 15 | has sections | CORPUS FLOOR 3 |
| `blocked_eng_c4f7a55cb067` (08-09) | 8 | 13 | has sections | CORPUS FLOOR 5 |

Two distinct failure modes:
- **Mode A (recent, fatal):** findings exist (47–87) but the FinalReport is an
  empty shell → `CORPUS FLOOR: 0`. This is a *synthesis assembly* defect.
- **Mode B (earlier):** report has real content but only 3–5 distinct domains →
  genuine *retrieval starvation* (only scholar/reference alive).

The current blocker is Mode A stacked on Mode B: retrieval is starved, *and*
the little that is retrieved never makes it into the report.

---

## 3. Root causes (code-verified)

### RC-1 — Retrieval rate-limit death spiral (`hyperion/tools/searxng.py`)

1. **Web replica has two enabled engines** (`searxng_settings.web.yml`):
   `brave` (HTML crawler → 429 `suspended_time=180` from a datacenter IP,
   *every* run) and `mwmbl` (times out at 20 s). mojeek/yep were disabled in
   P1.2 for categorical 403s. Result: `web=0d/0e` in every preflight.
2. **Full-pool fan-out** (`_search_all_replicas`) sends *every general query*
   to the scholar replica too — which is why docker logs show crossref being
   hit with `fintech Scrape Circle Internet Financial Inc pricing page extract
   pricing tiers features per tier discounts India`, and arxiv with a trailing
   `...OR` (→ HTTP 400).
3. **Those instruction-shaped queries** come from the LLM query planner
   (`hyperion/tools/query_planner.py` — FAST-tier model, shape-validated
   ≤120 chars but semantically garbage) and from specialist prompts whose
   "scrape pricing pages / extract tiers" instructions leak verbatim into
   query strings.
4. **`EngineTokenBucket.interval_seconds = 2.0`** — one request per engine per
   2 s, × 12 specialists × 3 sub-agents × ~7 queries × 3-way concurrency =
   guaranteed upstream 429s. Once suspended (`suspended_time=180`), engines
   are dead for *longer than the rest of the run*; nothing in the run waits
   the window out.
5. The only sanitizer that exists (`_sanitize_scholar_query`) strips
   `, ? .` and clamps length — it does **not** strip `scrape`/`extract`/
   `site:`/`OR` debris, so scholar APIs still 400/429 on them.

### RC-2 — Synthesis assembles an empty report (`hyperion/agents/synthesis_lead.py`)

1. `_build_analysis_sections()` (line ~1428) iterates
   `SECTION_PRODUCING_AGENTS` — **11 names; `strategy_analyst` is missing** —
   keyed by `finding.agent` / `msg.sender.value`. Any finding keyed outside
   the allowlist (sub-agent names, synthetic findings with an empty agent,
   strategy) → `tasks = []` → **zero sections**.
2. P2-11/P2-16 policy: any section whose deep-tier
   `mistral/devstral-2512` narrative call fails, times out, or returns
   < `min_body_chars` (≥800) is **omitted** (`SectionGapError`) and its
   question pushed into `limitations`. When those calls fail (intermittent —
   one run builds 7 sections, the next builds 0), the report is a shell:
   0 sections → 0 cited domains → CORPUS FLOOR 0 → terminal.
3. The gate then scores the empty shell with vacuous passes
   (evidence_sufficiency=5 "every claim has ≥1 source", analytical_depth=5)
   and honest failures (completeness=1, structural=1, risk=1) — the
   dimensions are meaningless on an empty report, but the CORPUS FLOOR
   blocker is what makes it terminal.

### RC-3 — Recovery is cosmetic (`hyperion/orchestrator.py`)

1. `_escalate_retrieval` (line ~1916) re-queries the **same SearxNG pool**
   whose engines are still inside the 180 s suspension window → fails in
   **< 1 second** (11:58:53 → 11:58:54 in the system log).
2. Re-synthesis reproduces the same empty shell (RC-2 unchanged).
3. The recovery pass records both attempts as `discarded` ("score 3.30 not
   ≥ best+0.05") → `blocked_eng_*.json` → BLOCKED. "Self-healing" = louder
   logging, no behavioral change.

### RC-4 — Corpus floor counts citations, not evidence

`QualityGate._corpus_floor_blocker` counts distinct domains **cited in the
report**. The 2,838-record / 542-domain ledger is invisible to it, so a
synthesis assembly defect (empty report) is indistinguishable from a true
retrieval outage, and both terminate identically.

---

## 4. What overhauls 1–3 tried, and why it didn't work

| Overhaul | What it added (all output-layer) | Why the runs still die |
|---|---|---|
| **overhaul.md P1–P6** | engine-health circuits, standby pools, per-owner budgets, fail-fast gates, Gemini grounding 1500/day, Jina fallback | Engine bans are **upstream state on one egress IP**; circuits just quarantine the dead engines faster. The query garbage and the 2 s pacing still hammer every healthy engine into the same suspension. |
| **overhaul2.md S1–S12** | reference-replica category fix, DNS fallback in compose, query-length clamps, "no-score-change early terminate", skip polishing floor reports | Fixed the reference 400 and DNS flakes, but the **web class was never restored** and the **empty-report assembly path was never touched**. The early-terminate just made the failure *faster and better-documented*. |
| **overhaul3_audit.md W1–W4** | profile-aware query shaping, non-JSON body → engine cooldown, risk-analysis wiring, synthetic-finding single path, mid-run corpus re-probe, preflight AMBER | Query shaping fixed *punctuation* but not *instruction debris* (`scrape`/`site:`/`OR`). Preflight AMBER correctly *detected* `web=0d/0e` and shrank the DAG — which then produced **fewer findings and an emptier report**. The gate loop (RECOVERY pass 1, QUALITY iteration, CORPUS FLOOR escalation) all fire and all fail in the logs — proof the machinery runs and the inputs are still dead. |

**Systemic failure of the approach:** every overhaul treated the symptom at
the quality-gate boundary (thresholds, floors, recovery passes) instead of the
two inputs: (1) queries+rate-limits, (2) findings→sections assembly. The log
line `QUALITY: corpus-floor retrieval escalation failed` in <1 s is the entire
story in one timestamp.

---

## 4.1 Post-overhaul3 run issue catalog (2026-08-11, session 0x376B64)

Every failure observed in the post-overhaul3 live run, mapped to a root
cause. This is the checklist the fixes above are measured against.

| # | Observation (docker/system log) | Root cause | Fixed by |
|---|---|---|---|
| A1 | `brave: Too many request (suspended_time=180)` — repeated | crawler 429 from datacenter IP | P1 (pacing), P6 (web-class engines) |
| A2 | `crossref: Too many request (suspended_time=180)` + `engine timeout` (12 s) | scholar API hammered with garbage queries at 2 s/engine | P1 (6 s), P2 (debris sanitizer) |
| A3 | `semantic scholar: JSONDecodeError: Expecting value` × 30+ | empty/non-JSON body — upstream throttling/blocking; engine not cooled fast enough historically | P1, P2; non-JSON cooling (overhaul3) |
| A4 | `openalex: Too many request (suspended_time=180)` | same as A2 | P1, P2 |
| A5 | `arxiv: 400 Bad Request ... site:crunchbase.com OR site:techcrunch.com OR` | trailing `OR` + `site:` operator in query | **P2 (debris sanitizer)** |
| A6 | `arxiv/pubmed/wikipedia: Too many request` | volume + operators | P1, P2 |
| A7 | `mwmbl: engine timeout` (20 s) | slow independent index | P6 (don't rely on mwmbl alone) |
| A8 | `X-Forwarded-For nor X-Real-IP header is set!` (searx botdetection, once per replica) | a request without the trusted-forwarded headers; likely a pre-fix probe | verify all callers go through `SearxNGClient` |
| A9 | `call to ResultContainer.add_unresponsive_engine after ResultContainer.close` | searxng internal race when engines answer after timeout | upstream; log-noise only |
| A10 | `CORPUS PREFLIGHT AMBER ... web=0d/0e ... dead/thin classes: ['web']` | web replica has no living engine | P6 (web-class engines) |
| A11 | `QUALITY iteration 1/3: score=3.3 ... CORPUS FLOOR blocker active` → escalation failed in **<1 s** → terminal BLOCKED | report empty (RC-2) + escalation re-hits suspended pool (RC-3) | P3, P4, P5 |
| A12 | `findings recorded` (41+) yet `no report` | findings never reach sections (RC-2 allowlist / P2-11 omission) | P3 |
| A13 | `blocked_eng_8b3c...json`: 47 findings / 10 task outputs but 0 report sections, 0 cited domains | RC-2 + RC-4 | P3, P4 |

Two additional configuration gaps found during the post-mortem:

- **G1:** `HYPERION_SEMANTIC_SCHOLAR_API_KEY` was never in `.env.example`
  although `config.semantic_scholar_api_key` exists and the client reads it
  (key raises the rate ceiling from 100 req/5 min to ~1 req/s). **FIXED:**
  added to `.env.example` (2026-08-11).
- **G2:** OpenAlex needs no API key — it uses a `mailto:` in the User-Agent
  for the polite pool (raises the rate ceiling ~10x). `HYPERION_OPENALEX_EMAIL`
  was already in `.env.example`; set it to a real address. **FIXED (2026-08-11):
  `openalex_email` is now a first-class `Settings` field + `ToolPathsConfig`
  member, so `OpenAlexClient` reads it through the same wiring as every other
  tool key instead of reaching into `os.environ` directly.**

---

## 5. Phase-wise fix plan

**Implementation status (2026-08-11): Phases 1–5 implemented + tests green
(`tests/test_overhaul4_regressions.py`, 10 passed; broader sweep 66 passed).
Phases 6–7 pending. Tracked in `TODO_OVERHAUL4.md`.**

Each phase is independently shippable, has a VERIFY step, and does not lower
any gate or floor.

### Phase 1 — Kill the rate-limit spiral (RC-1.4, RC-1.5) — ✅ implemented
**Files:** `hyperion/tools/searxng.py`, `hyperion/tools/engine_health.py`

1. `EngineTokenBucket.interval_seconds`: 2.0 → **6.0** (per engine).
2. Before dispatch, skip engines whose `engine_health.state() == SUSPENDED`
   — already partially done via `filter_available`, but make the fan-out and
   rotation **never** re-ask a suspended engine inside its window (assert +
   log once per window, don't consume retry budget).
3. Respect `suspended_time` from SearXNG responses: `record_response` already
   parses it (engine_health.py) — add a hard guard so `filter_available`
   excludes an engine until `time.time() > suspended_until` (verify it does;
   current code pops the suspension on first `state()` call — make expiry
   lazy-strict).

**VERIFY:** `python -m pytest tests/test_fix03_regressions.py -q` (engine
health suite) + one live engagement; docker logs show zero
`suspended_time=180` repeats inside one run.

### Phase 2 — Query hygiene at the dispatch choke point (RC-1.3, RC-1.5) — ✅ implemented
**Files:** `hyperion/tools/searxng.py` (`_shape_query_for_profile`),
`hyperion/tools/query_planner.py`

1. Add `_strip_instruction_debris(query)` applied to **every** dispatched
   query: remove standalone `scrape|scraping|extract|extracting`,
   `site:<domain>` operators, and standalone `OR`/`AND` (the trailing-`OR`
   arxiv 400). Reuse it in `_sanitize_scholar_query`.
2. In the query planner `_SYSTEM_PROMPT`, add a hard rule: "Never include
   instructions to scrape/extract a page; queries are keyword searches only."
   Keep the schema validation; add a pytest that a planner plan containing
   `scrape`/`site:` fails sanitization.
3. Stop fanning *general* queries into scholar replicas **unless** the query
   passes the debris check (a keyword-shaped query may still go to scholar).

**VERIFY:** grep docker logs for `Scrape`, `extract`, `site%3A`, trailing
`OR` — expect zero.

### Phase 3 — The report must never be empty when findings exist (RC-2) — ✅ implemented
**Files:** `hyperion/agents/synthesis_lead.py`

1. Add `AgentName.STRATEGY_ANALYST` to `SECTION_PRODUCING_AGENTS`
   (12 specialists, not 11).
2. In `_build_one_section`: when the narrative LLM fails, times out, or
   returns < `min_body_chars` on both attempts, **build a deterministic
   finding-digest section** (titles + content + implications + sources,
   structured prose) instead of raising `SectionGapError`. Log it loudly
   (`narrative synthesis degraded, deterministic digest used`). Keep
   `SectionGapError` only for the genuinely-empty (no findings) case.
   - This reverses P2-11's "never concatenate" rule **only as a last resort**,
     with a loud log, so a 0-domain shell can never reach the gate again.
3. `_get_participating_agents()` should fall back to
   `list(self._findings_by_agent.keys())` ∪ keys derived from
   `_collected_findings` so methodology metadata is never empty when
   findings exist.

**VERIFY:** unit test — 47 synthetic findings with a forced-failing narrative
LLM must produce a FinalReport with ≥1 section and ≥1 cited domain.

### Phase 4 — Corpus floor counts the ledger, not just citations (RC-4) — ✅ implemented
**Files:** `hyperion/agents/support/quality_gate.py`

1. `_corpus_floor_blocker(urls)`: if report-cited domains < floor, consult
   `get_evidence_ledger().distinct_domains()`. If the **ledger** has ≥ floor
   domains, return **no hard blocker** (the deficiency is synthesis citation,
   which the `evidence_sufficiency` dimension already penalizes) and append a
   gap: "report cites N domains; ledger holds M — synthesis must cite more".
2. Only a genuinely thin ledger (report **and** ledger below floor) blocks.

**VERIFY:** replay `eng_8b3c55112d20/evidence_ledger.json` through the gate:
must NOT terminate on CORPUS FLOOR; must instead terminate on the (still
honest) completeness/structural scores — and Phase 3 removes those.

### Phase 5 — Recovery escalates to living backends, not dead engines (RC-3) — ✅ implemented
**Files:** `hyperion/orchestrator.py` (`_escalate_retrieval`)

1. Before the SearxNG loop, check `get_engine_health()`: if every fleet
   engine is suspended/cooling, **skip SearxNG entirely** (don't burn the
   <1 s ritual).
2. Add direct-API recovery legs using the existing clients that bypass
   SearxNG suspensions: `tools/openalex.py` (`OpenAlexClient.search_works`)
   and `tools/semantic_scholar.py`, plus `tools/jina.py` — same subject
   query, merged into `found`. Error-safe (never raises).
3. Cap the escalation wall-clock (e.g., 45 s) so it cannot consume the
   quality-loop budget.

**VERIFY:** force-suspend all engines in a test; `_escalate_retrieval` must
recover sources via OpenAlex/Jina, and the quality loop must proceed.

### Phase 6 — Restore a working web class without keyed APIs (RC-1.1) — ✅ probe done, deployed (home-IP egress)

**P6.1 probe result (2026-08-11, live `/config` on the running image
`searxng:2026.7.19-6da6eee26`):**

- `marginalia` / `wiby` are **NOT in the image** (`wiby` → `FileNotFoundError:
  .../searx/engines/wiby.py` in the container log) — the original plan was
  reverted; do not declare them.
- `wikidata` **IS in the image** → enabled on the reference replica.
- **Egress is a HOME IP, not the VPS** — P1.2's reason for disabling
  mojeek/yep (datacenter 403s) no longer applies → mojeek/yep re-enabled on
  the web replica (watch logs; revert if 403s return).
- Live smoke test (web replica, home IP): **mwmbl = 50 results** (Fortune
  Business Insights, Statista — real report domains); **brave = 429
  (suspended, cools)**, **yep = access denied (cools)**, **mojeek = silent 0
  (no error, no log — investigate or disable later)**.
- Web class is now ALIVE from this egress (`web` > 0 domains achievable via
  mwmbl), replacing the VPS-era `web=0d/0e` every run.
- **Ops fix:** `docker-compose.yml` valkey service was missing `cap_add` —
  with `cap_drop: [ALL]` + `no-new-privileges`, the entrypoint's `setpriv`
  crash-looped on WSL2/Docker Desktop (`setresuid: Operation not permitted`);
  added `cap_add: [CHOWN, SETGID, SETUID]` (mirrors the searxng replicas).
- **Policy fix (Semantic Scholar):** documented ceiling is 1 request/second
  CUMULATIVE across all endpoints. Client pacing was per-instance and exactly
  1.0s (on the threshold); now process-wide shared lock + 1.5s delay
  (~0.67 req/s), safely below.
**Files:** `hyperion/infra/searxng_profiles.py` (generator),
`searxng_settings.*.yml` (generated), `docker-compose.yml`

1. **Verify against the running image** (`GET /config` on each replica) which
   of these are present in `searxng:2026.7.19`, then enable the tolerant,
   keyless candidates on the web replica: **marginalia**, **wiby**,
   **presearch** (watch: throttles), keep **mwmbl**. Expectation: they pad
   domains (indie/text web) — they carry niche queries, not the whole corpus.
2. Add **core.ac.uk** to the scholar replica (free keyed, full-text, tolerant)
   if present in the image and a key is acceptable (add `HYPERION_CORE_API_KEY`
   to `.env.example`, optional).
3. **Decision-record revision (P1.4):** do NOT re-add scraper engines that
   ban datacenter IPs. Revisit the "no keyed APIs" rule **only** for a slot
   whose free tier is *confirmed live at the time of wiring* (Brave was
   checked 2026-08-11 and its free tier is discontinued — do not add it;
   re-verify Tavily / Bing / Mojeek-API when deciding).
4. Keep 3 containers (or add replicas only for concurrency, never for bans —
   same egress IP).

**VERIFY:** preflight shows `web` class > 0 domains on a live run; the
web-class engines appear in `healthy_engines()`.

### Phase 7 — Regression lock (so this never regresses) — ⬜ pending
**Files:** `hyperion/eval/canaries.py`, `hyperion/eval/ci_gate.py`, `tests/`

1. Canary: **empty-report guard** — a forced narrative-LLM failure must still
   yield a FinalReport with ≥1 section + ≥1 domain (Phase 3).
2. Canary: **suspension guard** — an engine inside `suspended_time` receives
   zero dispatches (Phase 1).
3. Canary: **ledger-aware floor** — ledger ≥ 8 domains ⇒ no CORPUS FLOOR hard
   block even if the report cites < 8 (Phase 4).
4. KPI regression diff (already wired via `hyperion/eval/kpi.py`): `web`
   class domains per run must be > 0; report sections must be > 0.

**Exit gate:** 3 consecutive green live engagements; each has web-class
domains > 0, a non-empty report, no CORPUS FLOOR blocker, and a stated
evidence limitation only when the ledger is genuinely thin.

---

## 6. What this does NOT do (guardrails)

- Does not lower the corpus floor (8), the ship score (3.0), or delete any gate.
- Does not add scraper engines to a datacenter egress IP (P1.4 stands).
- Does not add keyed search APIs unless a free tier is verified live at
  wiring time (Brave's is discontinued as of 2026-08-11).
- Does not treat "system said BLOCKED" as failure — an honest, stated
  evidence limitation on a genuinely thin corpus is a *correct* outcome.
- Phase 3's deterministic digest is a **last resort** with a loud log; the
  narrative LLM remains the primary path.
