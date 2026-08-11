# HYPERION OVERHAUL 3 — Audit of the 2026-08-11 run (post-Overhaul-2)

**Audit date:** 2026-08-11
**Evidence:**
- TUI/system log: `reports/diagnostics/tui_log_0x2EA887_20260811_071219_568759.txt` (713 lines, identical to the WSL copy `\\wsl$\Ubuntu\home\abuzar\Hyperion\reports\diagnostics\...`, SHA-256 `A8DAED6D...`) — full 06:27→07:12 engagement.
- Docker log: the in-conversation `docker logs` paste for the same window (11:57→12:33 UTC / 06:27→07:03 container time) covering the searxng-scholar / searxng-web / searxng-reference / valkey containers.
- Blocked diagnostic: `\\wsl$\Ubuntu\home\abuzar\Hyperion\reports\diagnostics\blocked_eng_d7e007cb43bf.json` — the operator diagnostic for this exact run.
- Deployed build `0bee26d` (contains Overhaul-2 `S1-S16`, commit `af5f6b1`).

**Posture:** every finding below cites a code site and a log line. The goal is a single, ordered, production-grade remediation plan — not a patch list. **This audit's job: enumerate the complete set of defects that, together, convert a run that *should* ship into a BLOCKED run, and specify the self-healing loop that makes BLOCKED the exception rather than the default.**

---

## 0. TL;DR — the run in one paragraph

Overhaul-2's core fixes are **provably live** (preflight went honest AMBER with per-class floors instead of fake GREEN; synthesis ran on partial context instead of `MissingDependencyOutput`; no `sub_findings` crash; reference category 400 gone; DNS failures gone). But the run still ended BLOCKED at 07:11:05. The chain:

1. `competitive_intel.py` crashes with `_log() takes 2 positional arguments but 3 were given` (06:31:36) — **a latent 2-arg `_log` call** that only fires now that Stage A discovery actually returns candidates.
2. COMPETE dies → its output slot never fills → **STRATEGY (a specialist) hits `MissingDependencyOutput`** at 06:57:51 because S4's partial-context exemption covers only SYNTHESIS/FACT_CHECKER.
3. The reframer **reframes already-reframed variants** (attempt 2/2 → `task_reframed_1_0/1_1/1_2_*`) against a dead fleet — an unbounded variant explosion.
4. Sub-agents that hit `PROVIDER_FAILURE` try to self-heal to STRONG but are **refused by the total-budget gate** (base.py:1377 checks set *size*, not membership) — `SUB-AGENT total budget reached (3/3)` on every retry, because AMBER halved the ceiling 6→3.
5. `_all_findings` is still fed **only from `agent._findings`, not the bus** → `completed with 1 findings (total collected: 0)` and `8 (7)` — aggregate model publishes silently lost from the report channel.
6. Synthesis still built a report (38→50 findings), but it contained `Unknown` values (FINANCE DCF/Comp OUT OF SCOPE) and a verdict contradiction (`CONDITIONAL` vs `no-go`) → **Quality Gate correctly BLOCKED** with DATA VOID + VERDICT CONTRADICTION.
7. **`FinalReport.risk_analysis` is never assigned** (D-K) — RISK produced 18 findings and published a full `RiskAnalysis`, but the report has no risk section → the gate fails `risk_coverage=1` *independently of retrieval*.
8. **Delivery/visualization output never exists before the gate** (D-L) — DATA_VISUALIZER runs only in Stage 5, which is skipped on BLOCKED, so the gate scores `visual_quality` against a nonexistent input.
9. **The orchestrator has no recovery loop** (D-F) — on BLOCKED it writes a diagnostic and terminates. A proprietary self-healing system must diagnose the blocker and re-dispatch the offending agents/sub-agents with corrected prompts instead of giving up.

The fleet was additionally rate-limited (brave/crossref/wikipedia/openalex `suspended_time=180`), which starved retrieval — capacity is an aggravator, not the cause of the block.

---

## 1. Verified defects — the complete list (this is everything, cross-checked)

### D-A · `_log()` called with 2 positional args — crashes COMPETE (system log 06:31:36)

**Log:** `06:31:36 ✗ BaseAgent._log() takes 2 positional arguments but 3 were given`

**Code:** `hyperion/agents/specialists/competitive_intel.py:529-532` and `568-573`:

```python
self._log(
    "COMPETE Stage A: model-knowledge discovery named %d candidate(s)",
    len(llm_candidates),     # ← second positional arg
)
```

**Mechanism:** `BaseAgent._log(self, message: str)` accepts exactly one message string. This call passes two. It only raises when `llm_candidates` is non-empty — i.e. exactly when discovery *succeeds*. COMPETE then dies mid-`run()`, the task is marked FAILED, and everything downstream (STRATEGY depends on it) falls over.

**Why it survived Overhaul-2:** the audit's specialist rewrites (S2) touched `_ingest_sub_findings` sites; this pre-existing `%d`-style `_log` in Stage A/B was never exercised by tests because tests mock discovery to return empty.

**Fix:** convert both to f-strings (single arg). Add an arity guard test that greps every `self._log(` call site for >1 positional arg.

**⚠ COMPLETENESS UPDATE (2026-08-11 verification pass — AST sweep):** an `ast`-level scan of every `self._log(` call site across `hyperion/**/*.py` proves the D-A bug class has **exactly 4 sites, not 2**. The audit originally listed only the two `competitive_intel.py` sites; the orchestrator carries two more of the identical `%`-style antipattern (`orchestrator._log(self, message)` is also single-arg):

| Site | Positional args | Fires when | Blast radius |
|---|---|---|---|
| `competitive_intel.py:529` | 2 | Stage-A discovery returns candidates | **fatal** — kills COMPETE (the observed 06:31:36 crash) |
| `competitive_intel.py:568` | 2 | Stage-B fallback to model-knowledge | **fatal** — kills COMPETE on the fallback path |
| `orchestrator.py:2015` | 2 | `CORPUS PROGRESS SIGNAL` — N consecutive waves with zero new domains | **fatal & latent** — fires precisely under a rate-limited/dead fleet (D-J), i.e. this exact run nearly hit it |
| `orchestrator.py:3341` | 4 | `KPI REGRESSION` telemetry line | **silent** — swallowed by the enclosing `except Exception` (KPI recording), so it loses regression telemetry instead of crashing |

`orchestrator.py:2015` is the dangerous one: it is on the fleet-starvation path, so a future run with worse capacity than this one would crash the DAG wave loop with the same `_log() takes 2 positional arguments but 3 were given` — a second D-A, undetected by overhaul 1 & 2 because no test drives the zero-progress branch. **This is the concrete answer to "are there more errors of this class": yes, two, both now listed; the AST sweep confirms there are no others.**

**Fix (all 4):** convert every site to a single f-string. Then add `tests/test_log_arity.py` that AST-walks the whole package and asserts **0** `_log()` call sites with >1 positional arg — this makes the "4 sites, verified complete" claim a permanent regression lock, not a one-time grep.

---

### D-B · S4 partial-context is too narrow — STRATEGY still crashes (system log 06:57:51)

**Log:**
```
06:57:51 ✗ STRATEGY: task 'task_strategy_analyst' depends on 'task_competitive_intel'
           which has no output (status=failed) — refusing to run with a partial context
```

**Code:** `hyperion/orchestrator.py:727` — the exemption is only `(AgentName.SYNTHESIS_LEAD, AgentName.FACT_CHECKER)`.

**Mechanism:** a specialist whose dependency *failed* (not a retrieval-input failure — a pipeline/agent failure) raises `MissingDependencyOutput` and is itself marked FAILED → reframed → variant explosion. The strict contract was meant for "the dependent needs real *retrieval* inputs", not for "the upstream *specialist* crashed".

**Fix:** distinguish two cases:
- **Retrieval-input dep missing** (e.g. a search tool down) → keep strict.
- **Specialist dep failed** (upstream task status is FAILED/refused) → run on reduced context with a typed `missing_dependencies` list, exactly like synthesis. STRATEGY must be able to produce its analysis from the findings channel + whatever partial context exists, clearly stating the dependency gap.

---

### D-C · Total-budget gate refuses retries of already-counted questions (system log, throughout)

**Log:** `SUB-AGENT total budget reached (3/3 distinct work items); proceeding without spawning` — every time a `PROVIDER_FAILURE` self-heal tries to retry the same question on STRONG.

**Code:** `hyperion/agents/base.py:1377`:

```python
if len(distinct_questions) >= self.SUB_AGENT_TOTAL_CEILING:
    ... return []
distinct_questions.add(spec.question)
```

**Mechanism:** the gate checks **set size**, not **membership**. A STRONG self-heal or a broadened respawn re-enters with the **same question** (already counted). Once the set is full (3/3 — AMBER halved the ceiling from 6 to 3 at orchestrator.py:3453), *every* retry is refused. So `PROVIDER_FAILURE` → self-heal → "EXHAUSTED" — but STRONG **never actually ran** (the log line "still failed on STRONG tier" is a lie; it was refused at the gate).

**Fix:**
```python
if spec.question not in distinct_questions and len(distinct_questions) >= self.SUB_AGENT_TOTAL_CEILING:
    ... return []
distinct_questions.add(spec.question)
```
Also fix the misleading self-heal EXHAUSTED log to say "refused by budget" vs "ran and failed".

---

### D-D · `_all_findings` fed only from `agent._findings` — aggregate publishes lost (system log 06:40:41, 06:41:27)

**Log:**
```
06:40:41 sustainability_analyst: completed with 1 findings (total collected: 0)
06:41:27 market_analyst: completed with 8 findings (total collected: 7)
```

**Code:** `hyperion/orchestrator.py:1054`:
```python
findings_count = bus_count or len(agent._findings)   # count ← BUS (authoritative)
async with self._findings_lock:
    self._all_findings.extend(agent._findings)        # collection ← agent._findings ONLY
```

**Mechanism:** specialists publish two ways — `_publish_finding()` (→ `agent._findings` + bus) and the **aggregate model publish** `bus.publish(Channel.FINDINGS, payload={model_dump...})` (→ bus **only**). The count correctly uses the bus; the *collection* into `_all_findings` (what synthesis/floor report/KPI-3 consume) reads only `agent._findings`. So ESG's aggregate finding is counted (1) but never collected (0); MARKET's 8th is lost (8 vs 7).

**Fix:** after `extend(agent._findings)`, drain that agent's retained bus findings (`bus.get_retained_findings()`, filter `sender == task.agent`), dedup by finding id.

---

### D-E · Reframer reframes already-reframed variants — unbounded explosion (system log 06:47-06:58)

**Log:**
```
06:47:43 REFRAMER: task_competitive_intel (failed) → 3 reframed variant(s) [attempt 1/2]
06:57:55 REFRAMER: task_strategy_analyst (failed) → 3 reframed variant(s) [attempt 1/2]
06:58:03 REFRAMER: task_reframed_1_1_task_competitive_intel (failed) → 3 reframed [attempt 2/2]
06:58:06 REFRAMER: task_reframed_1_2_task_competitive_intel (failed) → 1 reframed [attempt 2/2]
```

**Code:** `hyperion/orchestrator.py:1370-1417` — variants are created with `reframe_attempts = original.reframe_attempts + 1` and the same agent; nothing stops a reframed variant from being reframed again, and the health-gate (`living_classes()`) only checks that *some* class is alive — it does not check that the *dependency* or *class the query targets* is alive.

**Mechanism:** a specialist whose upstream dep failed gets reframed into N variants; each variant re-runs the same dead search path; each variant fails and is reframed again. Against a rate-limited fleet this is pure token+time burn (dozens of COMPETE/STRATEGY LLM calls) that never fills the original output slot.

**Fix:** refuse to reframe a task when (a) it is itself a `task_reframed_*` variant that already failed once, **or** (b) the target source class is dead (`living_classes()` should be per-class, or `engine_health` should report which class the query targets), **or** (c) the task's own dependency failed (reframing won't fix an upstream crash). Cap the *variant tree*, not just per-task attempts.

---

### D-F · No recovery loop on QUALITY BLOCKED — orchestrator terminates (system log 07:11:05)

**Log:**
```
07:11:05 QUALITY: BLOCKED — refusing to ship. 2 integrity blocker(s):
         DATA VOID: 'Unknown' value(s) rendered as data ...
         VERDICT CONTRADICTION: recommendation is 'CONDITIONAL' but the narrative
         contains conflicting language ('no-go') ...
07:11:05 ERROR ✗ Quality Gate BLOCKED: ... → (run ends, no report)
```

**Code:** `hyperion/orchestrator.py` `_quality_iteration_loop` → `_compute_quality_terminal_state` → `_write_blocked_diagnostic`. On BLOCKED the run writes `blocked_eng_*.json` and stops.

**Mechanism:** BLOCKED is treated as terminal. But a **self-healing proprietary system** must treat it as a *diagnostic input*: classify the blocker, identify the responsible agents/sub-agents, and re-dispatch them with corrected prompts/context, then re-run quality — bounded. The data needed to do this is already on hand: `integrity_blockers`, `critical_dimensions`, `gaps`, the findings channel, the ledger.

**Fix (production-grade recovery loop, below in §3):** the orchestrator must own a `BLOCKED → diagnose → recover → re-score` loop instead of terminating. At minimum: DATA VOID → re-dispatch the source specialist(s) that produced `Unknown`/`OUT OF SCOPE` with an explicit "no data → emit typed gap, never 'Unknown'" instruction; VERDICT CONTRADICTION → re-dispatch synthesis with a single-verdict reconciliation instruction; CORPUS FLOOR → escalate retrieval (already exists) or terminate cheaply with a floor report.

---

### D-G · Reference-profile queries not condensed — wikipedia `/page/summary` 400 (docker log 12:31:05)

**Log:**
```
12:31:05 wikipedia 400 Bad Request
https://en.wikipedia.org/api/rest_v1/page/summary/competitor%20strategic%20moves%20space%2C%20recent%20announcements%2C...
```

**Code:** `hyperion/tools/searxng.py` `_search_searxng_json` (S10 clamp at line 718-723 only truncates >200 chars; no reference-profile condensation).

**Mechanism:** wikipedia's `page/summary/{title}` API treats the whole query as an article title. Full-sentence specialist queries always 400. S1 made the reference class *reachable*; now it's dead-by-query instead of dead-by-config. Sub-agents/preflight use `SubAgentRunner._condense_query()`; specialist direct reference searches do not.

**Fix:** in `_search_searxng_json`, when the resolved endpoint profile is `reference` (or engines include `wikipedia`), run the query through `_condense_query(query, max_len=120)` before dispatch.

---

### D-H · Scholar queries not sanitized — openalex 400 below the S10 clamp (docker log 12:02:35)

**Log:**
```
12:02:35 openalex 400 Bad Request
search=historical+failures+space+sector%2C+startups+failed%2C...What+caused+failure%3F+India   (~145 chars)
```

**Code:** `hyperion/tools/searxng.py:722-723` — `if len(query) > 200: query = query[:200].rsplit(" ", 1)[0]`.

**Mechanism:** openalex rejects sentence *punctuation* (commas, `?`), not just length. The 145-char query is under the 200 clamp and still 400s. The clamp alone is insufficient for the scholar profile.

**Fix:** replace the bare clamp with scholar-profile sanitation: clamp to ~120 chars **and** strip `,`/`?`/`.` (reuse `_condense_query` semantics). Keep web behavior unchanged.

---

### D-I · Non-JSON responses never cool the engine — semantic scholar retried all run (docker log 12:01:55 → 12:31:22)

**Log:** `semantic scholar JSONDecodeError: Expecting value: line 1 column 1 (char 0)` — dozens of times over 30 minutes.

**Code:** `hyperion/tools/searxng.py:869` catches `ValueError` (JSONDecodeError is a ValueError) as a generic request failure and *retries* the endpoint; it never feeds `health.record_response(unresponsive_engines=[["semantic scholar", "HTTP error 429"]])`. The engine-health circuit only sees suspensions when searxng *reports* them in the JSON payload — a non-JSON body bypasses that.

**Mechanism:** when the response body is not JSON, we cannot know the engine list, but we *can* mark the endpoint's engines cooling so the next request skips them. Today the same dead engine is re-queried for 30 minutes.

**Fix:** on a non-JSON/parse-error body from a profile, call `health.record_response(unresponsive_engines=[[engine, "HTTP error (non-JSON)"] for engine in endpoint.engines], responding_engines=[])` before the retry loop, so the engine enters the cooldown circuit.

---

### D-J · Fleet rate-limited (capacity — aggravator, documented for context)

**Log (docker):** brave `suspended_time=180` ×7; crossref ×13; wikipedia 429 + 400; openalex 429 + 400; arxiv 429 + timeout; pubmed ReadTimeout.

**Verdict:** this is the documented upstream reality (overhaul.md P1.4 — no keyed APIs, five free egress identities). It starved retrieval, which made D-F's `Unknown` values worse. It is *not* the cause of the block — the defects D-A..D-I are logic bugs; capacity only amplifies them. Fixing capacity alone reproduces this run.

---

### D-K · `FinalReport.risk_analysis` is never assigned — risk section absent despite RISK producing 18 findings (blocked diagnostic `d7e007cb43bf`)

**Log (blocked diagnostic):** the Quality Gate scored `risk_coverage=1/5` with "No risk analysis present", `structural_quality=3` "No risk analysis section", and `completeness=3` "No risk analysis section present" — yet the TUI log shows `06:47:38 risk_analyst: completed with 18 findings` and `06:47:38 finding recorded` (the RISK aggregate publish).

**Code:**
- `hyperion/agents/specialists/risk_analyst.py:1323-1339` publishes `"risk_analysis": analysis.model_dump()` on the bus (Channel.FINDINGS).
- `hyperion/agents/synthesis_lead.py:356` lists `"risk_analysis"` in `analysis_keys` (only used to mine headline metrics).
- `hyperion/schemas/models.py:2654` defines `risk_analysis: RiskAnalysis | None = Field(default=None)`.
- **Nothing in synthesis_lead.py ever assigns `FinalReport.risk_analysis`** (the only other reference, synthesis_lead.py:1461, is a log-string check). So the field stays `None` even when RISK returns a full model with 25 risks + residual summary.

**Mechanism:** RISK produces and publishes a complete `RiskAnalysis`; synthesis mines a couple of headline numbers from the payload but never carries the structured risk model into `FinalReport.risk_analysis`. The report therefore has no risk section (or only a thin derived one), and the Quality Gate — correctly — fails `risk_coverage`. This is a **schema-to-agent wiring gap**, independent of retrieval: even a healthy run with strong RISK findings would produce a report the gate blocks on risk coverage.

**Fix:** in synthesis, after collecting the RISK aggregate payload, construct the report's risk section from `payload["risk_analysis"]` (or the `_findings_by_agent[RISK]` set) and assign `FinalReport.risk_analysis`. Add a regression test: a RISK specialist returning a full `RiskAnalysis` → `FinalReport.risk_analysis` is not None.

---

### D-L · Delivery/visualization output never exists before the quality gate — scored as a gap, not a gate input

**Log (blocked diagnostic):** `visual_quality=3/5`, rationale "No Visualization Output received, cannot verify visual quality", plus open_gap "[Visual Quality] Run the Data Visualizer before quality gating."

**Mechanism:** the delivery agents (DATA_VISUALIZER, PRESENTATION_DESIGNER, RENDER_ENGINE) are excluded from `_execute_dag` and run only in Stage 5 — which is **skipped entirely on BLOCKED** (orchestrator.py:2970). So the Quality Gate scores `visual_quality` against a viz output that by construction does not exist yet. The gate cannot distinguish "viz genuinely failed" from "viz is a downstream stage" — it punishes a BLOCKED run for a stage that never runs, and a SHIPPING run would be scored the same way.

**Fix:** exclude the visual-quality dimension (or score it as "N/A / not-applicable at this boundary") when `viz_output is None` **because delivery has not run yet** — the gate should verify what exists, not penalize what is scheduled later. Keep the hard `visual_quality` check for the re-render/validation path where delivery output is expected.

---

### Anatomy · How COMPETE finds competitors, and where each step breaks

Competitor discovery is the single most failure-prone chain in this run, so it gets its own walk-through. All code in `competitive_intel.py`; every step below maps to one or more documented defects.

**Step 1 — Resolve the arena.** `_identify_competitors()` (line 509) resolves a subject (company/sector/industry from context, else the question) — `arena`. For a nation/region arena like "space sector in India" the arena becomes e.g. `"space sector"`.

**Step 2 — Stage A: model-knowledge discovery.** `_discover_competitors_llm(arena)` (line 429) fires a **STRONG-tier Mistral call** that names 3–5 concrete organizations from model knowledge, classified by entity class (nation/region, technology, market, person/org, company). It is deliberately *not* gated on search — so even a dead web pool yields candidate names.
**Breaks here → D-A:** on success (`llm_candidates` non-empty) it calls `self._log("... %d candidate(s)", len(llm_candidates))` with 2 positional args → `BaseAgent._log()` raises → **COMPETE dies mid-`run()`**. Line 568 has the identical bug in the Stage-B fallback log.

**Step 3 — Stage B: search validation.** `_build_discovery_queries(arena)` (line 382) builds **entity-class-correct** queries — `"india space sector companies startups players"`, `"top space companies in India"`, `"industry landscape leading firms"` — instead of the old broken `"<subject> direct competitors"`. Each query runs `searxng.search(pattern, max_results=10)`; rows are collected as `{result_id, title, url, snippet}`.
**Breaks here → D-J, D-G, D-H:** if the web pool is 429-banned (brave) and the reference class 400s on sentence queries (wikipedia `/page/summary`) and scholar 400s on unsanitized queries (openalex), `results` is empty or contains nothing citable.

**Step 4 — Stage B judge.** `_extract_competitor_names()` (line 588) runs an **LLM semantic judge** that gets the search rows + the Stage-A candidates as seeds, and must return competitors **each citing ≥1 valid `evidence_result_id`** (line 660). Names without citable evidence are rejected — provenance stays ledger-bound.
**Breaks here → D-J:** with an empty `results` set there are no `evidence_result_id`s to cite, so the judge returns `([], set())`.

**Step 5 — Stage C fallback.** If the judge returned nothing but Stage A named entities, `_identify_competitors` falls back to the model-knowledge set (line 566-573), typed LOW confidence because provenance will be unverified downstream.
**Breaks here → D-A:** the fallback log at line 568 is the second 2-arg `_log` crash.

**Step 6 — Downstream.** `_scrape_competitor_sites` (Obscura stealth + Jina), Wayback snapshots, competitor matrix, moats, etc. These only matter if Steps 2–5 produced a non-empty `_competitor_names`.

**The run's COMPETE chain, end to end (all verified log lines):**
```
06:31:36  Stage A returned candidates → D-A crash (2-arg _log) → COMPETE task FAILED
06:47:43  REFRAMER: task_competitive_intel (failed) → 3 variants        (D-E)
06:57:51  STRATEGY depends on task_competitive_intel (no output, failed)
          → MissingDependencyOutput                                    (D-B)
06:58:03  REFRAMER: task_reframed_1_1_task_competitive_intel → 3 more   (D-E)
07:11:05  QUALITY BLOCKED (DATA VOID 'Unknown' + VERDICT CONTRADICTION) (D-F)
```
So: **D-A kills discovery the moment it succeeds; D-J/D-G/D-H guarantee search cannot rescue it; D-B converts the missing output into a cascade; D-E multiplies the waste; D-K/D-L skew the gate's verdict; D-F refuses to ship.** No single fix reproduces a report — the full D-A..D-L set must be cleared.

---

## 2. What is CONFIRMED WORKING (so nobody re-fixes it)

- ✅ **S1** reference-category contract — zero `Invalid value: "['reference']"` in docker log.
- ✅ **S6** per-class preflight — honest `AMBER ... dead/thin classes: ['web', 'reference']`.
- ✅ **S7** canary stage-tagging — mid-run recheck counts engagement evidence.
- ✅ **S4** partial context for SYNTHESIS/FACT_CHECKER — `proceeding with partial context — missing dependency outputs: [...]`.
- ✅ **S2** single ingestion path — no `sub_findings` UnboundLocalError.
- ✅ **S8** provenance retype — `unverified_assertion` appears; no sourceless "87→0".
- ✅ **S9** concurrent budget raise — `SUB-AGENT concurrent budget raised to 4` + deferred dispatch visible.
- ✅ **S10** DNS fallback — zero `Temporary failure in name resolution`.
- ✅ **S11** topicality guard — `TOPICALITY: dropped 2 off-topic sub-agent finding(s)`.
- ✅ Quality Gate integrity blockers — the only component that told the truth.

---

## 3. Production-grade remediation plan (Overhaul 3)

**Ordering principle:** crash first (D-A), then un-block the pipeline (D-B/D-C/D-D), then fix the schema-to-agent wiring that skews the gate (D-K/D-L), then contain waste (D-E), then retrieval hygiene (D-G/D-H/D-I), then the self-healing loop (D-F), then regression lock.

### W0 — Crash + arity (highest yield)

**S1 (D-A)** — f-string **all four** `_log` calls: competitive_intel.py:529,568 **and orchestrator.py:2015,3341** (the two orchestrator sites were found in the 2026-08-11 AST verification pass — see the D-A completeness update). Add `tests/test_log_arity.py`: AST-walk every `self._log(` / `self.bus.publish_status(` call site; assert **0** with >1 positional arg (currently 4).
VERIFY: `python -m pytest tests/test_log_arity.py tests/test_competitor_discovery.py -q`

### W1 — Pipeline un-block

**S2 (D-B)** — in `_execute_task`, when a dependency is missing:
- if the dep task exists and `status == FAILED` (specialist/agent failure, not retrieval) → run the dependent on reduced context for **all** agents that consume specialist output (add STRATEGY and any other non-retrieval-dependent specialists), carrying `missing_dependencies`.
- keep the strict raise only when the dep is genuinely a required retrieval artifact.
VERIFY: unit test — specialist whose dep FAILED runs with `context["missing_dependencies"]`; a missing retrieval input still raises.

**S3 (D-C)** — base.py:1377 membership-aware gate (`spec.question not in distinct_questions and ...`). Fix self-heal EXHAUSTED log to distinguish refused-vs-failed.
VERIFY: `tests/test_fix03_regressions.py::TestAdaptiveConcurrentBudget` + new test "ceiling full, retry of counted question executes".

**S4 (D-D)** — orchestrator.py:1054: extend `_all_findings` from `agent._findings` **plus** `bus.get_retained_findings()` for that agent (dedup by id).
VERIFY: unit test — specialist that only does `bus.publish(Channel.FINDINGS)` → finding lands in `_all_findings`; the "1 (0)" / "8 (7)" mismatch disappears.

**S4b (D-K)** — synthesis: assign `FinalReport.risk_analysis` from the RISK aggregate payload (`payload["risk_analysis"]` / `_findings_by_agent[RISK]`). Add regression test — RISK returning a full `RiskAnalysis` → `FinalReport.risk_analysis` is not None.
VERIFY: `python -m pytest tests/test_synthesis_body_survives.py -q` + new test.

**S4c (D-L)** — Quality Gate: when `viz_output is None` because delivery hasn't run yet (pre-Stage-5 boundary), score `visual_quality` as N/A instead of 3/5; keep the hard check on the re-render/validation path where delivery output is expected.
VERIFY: unit test — gate with `viz_output=None` at the pre-delivery boundary does not penalize visual_quality; the re-render path still hard-checks.

### W2 — Contain reframe waste

**S5 (D-E)** — `_maybe_reframe_failed_tasks`: refuse when (a) the task is already a `task_reframed_*` variant, or (b) the query's target source class is dead (per-class living check), or (c) the task's dependency failed. Cap the variant *tree*.
VERIFY: unit test — reframed variant with dead class → no new variant; existing reframe tests still pass.

### W3 — Retrieval hygiene

**S6 (D-G)** — reference-profile query condensation in `_search_searxng_json`.
VERIFY: unit test — sentence query to reference profile arrives ≤120 chars title-shaped.

**S7 (D-H)** — scholar-profile sanitation (≤120 + strip `,?\.`) replacing the 200 clamp.
VERIFY: unit test — 145-char comma/`?` sentence routed to scholar arrives sanitized; web unchanged.

**S8 (D-I)** — non-JSON body → `health.record_response(unresponsive=endpoint.engines, ...)` before retry.
VERIFY: unit test — non-JSON marks engines cooling; next request skips them.

### W4 — The self-healing loop (D-F) — the systemic ask

**S9 — Orchestrator-owned `BLOCKED → diagnose → recover → re-score` loop.**
When `_compute_quality_terminal_state` returns BLOCKED, instead of terminating:

1. **Classify** the blockers:
   - `DATA VOID` / `'Unknown'` / `OUT OF SCOPE` → source specialists produced placeholder values. Re-dispatch the *finding-bearing* specialists listed in the diagnostic with an explicit **"no data is a typed gap; never emit 'Unknown'/'OUT OF SCOPE' as a value"** instruction, fresh sub-agent prompts.
   - `VERDICT CONTRADICTION` → re-dispatch SYNTHESIS with a single-verdict reconciliation directive.
   - `CORPUS FLOOR` → escalate retrieval (existing `_handle_thin_evidence`) or terminate cheaply with a floor report (existing).
2. **Budget the loop** — max 1 recovery pass (config: `quality_recovery_max_passes`, default 1), wall-clock bounded, counted in the same manifest as iterations so a loop cannot become an engine.
3. **Re-run** quality on the recovered report; if still BLOCKED, emit the honest diagnostic (current behavior) but now *with* the recovery attempt recorded.
4. **Telemetry** — every recovery pass writes to the diagnostic + KPI (new `kpi_9_recovery_passes` / `kpi_9_recovered`).

VERIFY: fault-injection test — a report carrying `Unknown` in a numeric field triggers one recovery re-dispatch of that specialist, the report is re-scored, and `recovery_passes == 1`.

**S10 — Failure-class accuracy for sub-agents** (supports D-C): when a sub-agent self-heal is *refused by the budget gate*, stamp the runner outcome `BUDGET_REFUSED` (not `RETRY_EXHAUSTED`/`PROVIDER_FAILURE`) so logs and telemetry tell the truth.

### W5 — Regression lock

**S11** — extend the canary suite (S14): reference-condensation canary, scholar-sanitation canary, non-JSON-cooldown canary, `_log`-arity grep canary, `_all_findings`-bus-fed canary, recovery-loop canary, risk-section-populated canary, visual-quality-N/A canary.
**S12** — extend `tests/test_searxng_category_contract.py` contract to assert reference queries are title-shaped.
**S13** — update `ARCHITECTURE.md` §14 with D-A..D-L and the recovery loop; mark the KPI additions.

### DoD (live-run gates — India-space question)

1. Zero `_log() takes 2 positional arguments` lines. (D-A)
2. No `MissingDependencyOutput` on a specialist whose dep failed — it runs on reduced context. (D-B)
3. Zero `SUB-AGENT total budget reached ... proceeding without spawning` for a question already counted — self-heal actually runs STRONG. (D-C)
4. Every specialist's `completed with N findings (total collected: M)` has M ≥ N (bus-fed). (D-D)
5. No reframed variant is reframed; zero `task_reframed_1_1_* → reframed` lines. (D-E)
6. A BLOCKED-quality run triggers one recovery pass and either ships or terminates with the recovery attempt recorded. (D-F)
7. Zero wikipedia `/page/summary` 400 and zero openalex 400 in docker during a healthy run. (D-G/D-H)
8. semantic scholar stops being queried after a non-JSON response (cooldown). (D-I)
9. `FinalReport.risk_analysis` is populated whenever RISK returns a full model — a run with strong risk findings is never blocked on "no risk section". (D-K)
10. Quality Gate does not penalize `visual_quality` before delivery has run; the re-render path still hard-checks. (D-L)
11. Full suite green (minus known env failures); `python -m hyperion.eval.canaries` green incl. new canaries.

---

## 4. Anti-patterns — must NOT do

1. Do not raise retries/timeouts/iteration caps — that pays more for the same defects (D-A..D-L are logic bugs, not time).
2. Do not relax the Quality Gate thresholds or delete integrity blockers — it is the only honest component; teach the *orchestrator* to fix the input instead.
3. Do not add keyed search APIs or more scraper engines (product decision, overhaul.md P1.4).
4. Do not reframe against a dead class or already-reframed variants (D-E).
5. Do not mark a budget-refused self-heal as "still failed on STRONG tier" (D-C) — logs must tell the truth.
6. Do not batch W0–W5; each step is separately verifiable (causal attribution).
7. Do not treat BLOCKED as terminal without a recovery attempt (D-F) — that is the difference between a wrapper and a self-healing system.

---

## 5. FAIL-SAFE SELF-HEALING SYSTEM — full design (Overhaul 3, the systemic deliverable)

> This section supersedes the W4/S9 sketch above. It is the complete architecture, grounded in the code paths read for this audit (`orchestrator.py`, `agents/base.py`, `agents/bus.py`, `agents/support/quality_gate.py`, `tools/task_reframer.py`, `tools/engine_health.py`, `config.py`). It is what makes Hyperion a proprietary self-healing system rather than a 20-agent wrapper.

### 5.0 The design principle — failure is an input, not an exit

Every defect D-A…D-L is one disease with three faces: **a component acted on a belief about its own state that was wrong, stale, or dishonestly logged, and had no supervisor to catch it.** The run proves it:

- The **Quality Gate is already the smartest component in the system** — it correctly diagnosed DATA VOID + VERDICT CONTRADICTION + risk_coverage=0 at 07:11:05. Then the orchestrator wrote that perfect diagnosis to `blocked_eng_d7e007cb43bf.json` and **threw it away** (`orchestrator.py:~2970`, the `return result` in the `terminal_state == BLOCKED` branch).
- The sub-agent self-heal **logged "still failed on STRONG tier" when STRONG never ran** (D-C) — the system lied to itself, so every downstream decision started from a false premise.
- The reframer **spawned variants of variants against a dead fleet** (D-E) because nothing asked "can retrying possibly change the outcome?"

So the fix is not "more retries / bigger caps" (explicitly forbidden — §4.1). It is a **supervisory control layer** built on three primitives, plus one bounded recovery loop that consumes them. The primitives are cheap; the loop is the flagship.

**The three primitives (cross-cutting, fix the disease not the symptom):**

| # | Primitive | One-line contract | Kills |
|---|-----------|-------------------|-------|
| P1 | **State Store** — one authoritative source of truth | Findings, budgets, task status are read from ONE accessor (the bus); no component keeps a private shadow copy that can drift. | D-C, D-D, D-K |
| P2 | **Failure Taxonomy** — typed, honest outcomes | Every retry/reframe/self-heal/recovery path stamps a typed `FailureClass`; a log line may never assert an action happened that the gate refused. | D-C (lying log), D-E |
| P3 | **Progress Predicate** — `can_make_progress()` | Before ANY remediation (reframe, self-heal, retrieval escalation, recovery pass), a single predicate asks "is this action *capable* of changing the outcome?" If no → skip and degrade honestly. | D-E, half of D-C |

The recovery loop (5.1) is the orchestrator-owned supervisor that turns a BLOCKED verdict into a bounded, idempotent, monotonic repair attempt using P1–P3.

---

### 5.1 The Recovery Supervisor — `BLOCKED → diagnose → plan → recover → re-score → decide`

A new orchestrator method `_recover_from_blocked(dag, report, score)` is invoked from the `terminal_state == QualityTerminalState.BLOCKED` branch of `run_engagement` (`orchestrator.py:~2970`) **before** the `return result`. It is a bounded state machine:

```
                 ┌─────────────────────────────────────────────┐
   BLOCKED ─────▶│ 1. DIAGNOSE  classify each integrity_blocker │
   (score)       │              into a RecoveryClass             │
                 └───────────────────────┬─────────────────────┘
                                         ▼
                 ┌─────────────────────────────────────────────┐
                 │ 2. PLAN      map class → remediation action;  │
                 │              drop actions failing P3          │
                 │              (can_make_progress == False)     │
                 └───────────────────────┬─────────────────────┘
                          plan empty? ────┴──▶ 6a. DEGRADE (floor report / honest BLOCK)
                                         ▼
                 ┌─────────────────────────────────────────────┐
                 │ 3. SNAPSHOT  keep current report as `best`    │  ← monotonicity guard
                 └───────────────────────┬─────────────────────┘
                                         ▼
                 ┌─────────────────────────────────────────────┐
                 │ 4. RECOVER   re-dispatch ONLY the responsible │
                 │              agent(s)/sub-agent(s) with a     │
                 │              corrected, blocker-specific      │
                 │              directive (idempotent task ids)  │
                 └───────────────────────┬─────────────────────┘
                                         ▼
                 ┌─────────────────────────────────────────────┐
                 │ 5. RE-SCORE  re-run _quality_iteration_loop   │
                 │              on the repaired report           │
                 └───────────────────────┬─────────────────────┘
              improved & APPROVED ───────┼──────▶ 6b. SHIP (Stage 5 runs)
              not improved / still BLOCKED│
                                         ▼
                 ┌─────────────────────────────────────────────┐
                 │ 6. DECIDE    pass_count < max_passes AND      │
                 │              wall-clock left AND score        │
                 │              strictly improved? → loop to 1   │
                 │              else → keep `best`, DEGRADE      │
                 └─────────────────────────────────────────────┘
```

**Key properties:** the loop re-uses the *existing* `_quality_iteration_loop` / `_compute_quality_terminal_state` as its scorer (no second ship/no-ship authority), re-uses `_handle_thin_evidence` for retrieval escalation, and re-uses `_write_blocked_diagnostic` for the final honest give-up. It adds a supervisor, not a parallel pipeline.

---

### 5.2 Blocker → remediation routing table (the "diagnose" + "plan" tables)

The Quality Gate already emits typed `integrity_blockers` strings and `critical_dimensions`. `_recover_from_blocked` classifies each into a `RecoveryClass` and maps it to exactly one remediation. **Every action is gated by P3 (`can_make_progress`) — if the predicate says no, the action is dropped, not attempted.**

| Blocker signature (from `score.integrity_blockers` / gate) | RecoveryClass | Remediation action | Responsible agent | P3 progress condition |
|---|---|---|---|---|
| `DATA VOID: 'Unknown'…` / `OUT OF SCOPE` rendered as data | `PLACEHOLDER_VALUE` | Re-dispatch the finding-bearing specialist that emitted the placeholder with a hard directive: **"no data is a typed research_gap; NEVER emit 'Unknown'/'OUT OF SCOPE' as a value."** Prefer the D-B partial-context path. | the source specialist (e.g. FINANCE) | that specialist's source class has ≥1 living engine OR it can honestly downgrade to a typed gap (always true) |
| `VERDICT CONTRADICTION: recommendation 'CONDITIONAL' but narrative 'no-go'` | `VERDICT_CONFLICT` | Re-dispatch **SYNTHESIS_LEAD** with a single-verdict reconciliation directive (pick one verdict, purge conflicting language). No retrieval needed. | SYNTHESIS_LEAD | always (pure re-synthesis over existing findings) |
| `risk_coverage=1` with `FinalReport.risk_analysis is None` (**D-K**) | `MISSING_SECTION` | Re-run the synthesis body-assembly step that maps the RISK aggregate payload → `report.risk_analysis`; **no re-research** — the 18 findings already exist on the bus (P1). | SYNTHESIS_LEAD | bus has RISK findings (true) |
| `CORPUS FLOOR` / sources < floor | `THIN_EVIDENCE` | `_handle_thin_evidence(report, corpus_floor)` — already implemented; escalate retrieval on living classes only. | orchestrator retrieval | `engine_health.living_classes()` non-empty (P3) — else skip straight to floor report |
| leaked object / banned filler / broken URL | `PRESENTATION_DEFECT` | Targeted SYNTHESIS polish of the offending field only. | SYNTHESIS_LEAD | always |
| `visual_quality` scored against nonexistent viz (**D-L**) | `SCORER_BOUNDARY` | **Not a recovery action — a gate fix.** Gate must score `visual_quality = N/A` pre-Stage-5 (S4c). Listed so the supervisor does not "recover" a phantom. | — | — |

**Signals the supervisor consumes as diagnostics (observed this run, previously undocumented):**
- `SUB-AGENT NUMERIC CONTRADICTION … (47.1x apart)` (MARKET 06:35:14) and `(15.0x apart)` (INNOVATE 06:39:54) — the reconciliation layer working *as designed*. These are **recovery inputs**: an unresolved numeric contradiction that survives into the report should raise a `VERDICT_CONFLICT`-adjacent `DATA_CONFLICT` recovery (re-dispatch the specialist to pick the evidence-weighted figure), not ship two contradictory numbers.
- `103 evidence chain breaks, verification_rate=0.1, confidence=low` (SYNTHESIS 06:58:11) — a downstream *symptom* of the D-J retrieval starvation feeding the D-F block, **not a separate defect**. The supervisor reads `verification_rate` as the confidence signal that decides DEGRADE-to-floor vs retry: a `verification_rate` this low with living classes → one retrieval escalation; with dead classes → floor report immediately (never burn a recovery pass on a dead fleet).

---

### 5.3 Fail-safe invariants — the "can never" guarantees

These are the properties that make it *fail-safe*, i.e. it can never make the run worse, never run away, and never lie. Each is a hard assertion, testable.

1. **Bounded** — total recovery passes ≤ `quality_recovery_max_passes` (default **1**), AND the loop shares the engagement wall-clock budget (`quality_iteration_wall_clock_seconds`, already enforced at `orchestrator.py:2085`). A recovery pass can never turn a 34-minute run into a 2-hour one.
2. **Monotonic** — the pre-recovery report is snapshotted as `best`; a recovery pass is *committed* only if the new score is **strictly higher**. A pass that lowers or flatlines the score is discarded and `best` is kept. Recovery can never regress the deliverable.
3. **Idempotent** — recovery re-dispatch uses deterministic task ids (`task_recover_<pass>_<agent>`); re-entry is a no-op (mirrors the reframer's `dag.get_task(new_id) is not None` guard at `orchestrator.py:1372`). No variant-tree explosion (D-E).
4. **Progress-gated** — no remediation runs unless P3 `can_make_progress()` is True. A dead fleet routes straight to DEGRADE, never to a retry storm.
5. **Honest** — every recovery pass writes a typed outcome to the run manifest (`obs/run_manifest.py`) and the diagnostic; no log line asserts an action that a gate refused (P2). The final give-up still calls `_write_blocked_diagnostic` — now *with* the recovery attempt recorded.
6. **Non-authoritative** — the supervisor never overrides the Quality Gate. It changes the *input*, then asks the existing gate again. `_compute_quality_terminal_state` remains the single ship/no-ship decision point (§W-08). The gate's thresholds and blockers are never relaxed (§4.2).
7. **Degrades gracefully** — terminal DEGRADE produces the best available artifact honestly: either SHIP_WITH_CAVEAT (only if operator opted in) or a floor report with a stated evidence limitation — never a silent empty run, never a crash.

---

### 5.4 The cross-cutting primitives, concretely

**P1 · State Store (single source of truth).** The bus already *is* the ledger of record and already exposes the accessors — this is a wiring fix, not new infrastructure:
- `bus.get_findings_count(agent)` (bus.py:476), `bus.get_retained_findings()` (bus.py:487), `bus.clear_retained_findings()` (bus.py:495) already exist.
- **D-D fix:** at `orchestrator.py:1054`, after `self._all_findings.extend(agent._findings)`, drain `bus.get_retained_findings()` filtered by `sender == task.agent`, dedup by finding id. Then *count and collection read the same source* and can never disagree (`8 (7)` / `1 (0)` become impossible).
- **D-C fix:** at `base.py:1377`, make the gate membership-aware: `if spec.question not in distinct_questions and len(distinct_questions) >= CEILING: return []`. A retry of an already-counted question is budget-free.
- **D-K fix:** synthesis body assembly reads the RISK aggregate from the bus (P1) and assigns `report.risk_analysis` — the section is a *view* over the store, never a separately-maintained copy.

**P2 · Failure Taxonomy.** Introduce a single enum (extend the existing `recovery_hint` strings, which already include `PROVIDER_FAILURE`, `FETCH_BLOCKED`, `FETCH_INSUFFICIENT`, `TIMEOUT`, `ENGINE_BLOCKED`):
```
class FailureClass(Enum):
    PROVIDER_FAILURE   # router tier chain failed (code/provider bug)
    RATE_LIMITED       # 429 / suspended_time — capacity, transient
    RETRIEVAL_EMPTY    # search ran, zero citable rows
    PARSE_ERROR        # non-JSON body (D-I) — engine must cool
    DEP_CRASHED        # upstream specialist FAILED (D-B) — reframing can't fix
    ENGINE_BLOCKED     # no living class — route to capacity recovery
    BUDGET_REFUSED     # refused at a budget gate — NEVER "ran and failed" (D-C)
    TIMEOUT            # bounded-resource outcome
```
Rule (P2 honesty): the self-heal EXHAUSTED log (base.py:1521) must stamp `BUDGET_REFUSED` when the STRONG spawn was refused at the gate, and `PROVIDER_FAILURE` only when STRONG actually ran and failed. Telemetry and the recovery planner both read the typed class, never the prose.

**P3 · Progress Predicate.** One pure function, called by the reframer, the sub-agent self-heal, `_handle_thin_evidence`, and the Recovery Supervisor:
```
def can_make_progress(action, target_class, failure_class, engine_health) -> bool:
    if failure_class in (BUDGET_REFUSED,):        return True   # retry is free & may help
    if failure_class == DEP_CRASHED:              return False  # reframing won't fix upstream (D-B/D-E)
    if failure_class in (PROVIDER_FAILURE,):      return True   # STRONG self-heal may help (once)
    if action is RETRIEVAL and not engine_health.living_classes_for(target_class):
        return False                                            # dead target class (D-E fix (b))
    if action is REFRAME and task.is_reframed_variant:          return False  # no variant-of-variant (D-E fix (a))
    return True
```
This single predicate closes D-E (all three sub-cases the audit lists) and removes the phantom retries in D-C. The reframer's health-gate at `orchestrator.py:1298` becomes **per-class** (`living_classes_for(target)`) instead of "any class alive", and `_task_needs_reframe` (orchestrator.py:1223) gains the `DEP_CRASHED` and `is_reframed_variant` refusals.

---

### 5.5 New config knobs + telemetry (KPIs)

**Config (`config.py`, add near the quality block at line ~798):**
```
quality_recovery_max_passes: int = 1        # bounded self-healing; 0 disables (old behavior)
quality_recovery_min_score_gain: float = 0.05   # a pass must beat `best` by this to commit (monotonicity)
recovery_wall_clock_seconds: int = 300      # sub-budget carved from the engagement wall-clock
```
No existing cap is raised (§4.1 respected) — these are *new* bounds on a *new* loop.

**KPIs (`eval/kpi.py`):**
- `kpi_9_recovery_attempted` (bool), `kpi_9_recovery_passes` (int), `kpi_9_recovered` (bool — did a BLOCKED run ship after recovery?), `kpi_9_recovery_outcome_by_class` (map RecoveryClass → committed/discarded/skipped-by-P3).
- Manifest (`obs/run_manifest.py`): each pass appends `{pass, blocker_class, action, agent, score_before, score_after, committed, failure_class}` — so a blocked run is now *replayable*, not just disappointing.

---

### 5.6 Integration points (exact code sites)

| Change | Site | Nature |
|---|---|---|
| Invoke supervisor before terminating | `orchestrator.py` `run_engagement`, BLOCKED branch `~2970` (the `return result`) | insert `await self._recover_from_blocked(...)` |
| Recovery supervisor | new `orchestrator._recover_from_blocked()` | new method; re-uses `_quality_iteration_loop`, `_handle_thin_evidence`, `_compute_quality_terminal_state`, `_write_blocked_diagnostic` |
| State store drain | `orchestrator.py:1054` | D-D fix (P1) |
| Membership-aware budget | `base.py:1377` | D-C fix (P1) |
| Honest self-heal log | `base.py:1499-1522` | P2 (`BUDGET_REFUSED` vs `PROVIDER_FAILURE`) |
| Per-class reframe gate | `orchestrator.py:1298` + `_task_needs_reframe:1223` | D-E fix (P3) |
| `can_make_progress()` | new `tools/engine_health.py` helper or `tools/progress.py` | P3 |
| Partial-context for crashed dep | `orchestrator.py:727` exemption set | D-B fix (feeds `PLACEHOLDER_VALUE` recovery) |
| `risk_analysis` assignment | `synthesis_lead.py` body assembly | D-K fix (`MISSING_SECTION` recovery) |
| Gate viz N/A pre-Stage-5 | `agents/support/quality_gate.py` | D-L fix (`SCORER_BOUNDARY`) |

---

### 5.7 Verification — fault injection, not happy-path mocks

The `_log` crash (D-A) survived because tests mocked discovery to return empty — the success path was never exercised. The self-healing layer must be verified under the *real* production environment (rate-limited fleet + crashing specialists), so it becomes the default CI scenario:

1. **Recovery-loop canary** (`eval/canaries.py`): a report carrying `Unknown` in a numeric field → exactly one recovery pass re-dispatches that specialist → report re-scored → `kpi_9_recovery_passes == 1` and `kpi_9_recovered` reflects the true outcome.
2. **Monotonicity test:** a recovery pass that lowers the score is discarded; `best` is shipped/blocked, never the worse report.
3. **Progress-predicate test:** dead target class → reframe/retrieval skipped (no spawn), routes to floor report; `BUDGET_REFUSED` retry of a counted question executes.
4. **Honesty test:** a budget-refused self-heal stamps `BUDGET_REFUSED`, never emits "still failed on STRONG tier".
5. **Fault-injection harness** (`eval/harness.py` + `eval/ci_gate.py`): run the pipeline with (a) forced `PROVIDER_FAILURE` on one specialist, (b) a rate-limited fleet, (c) one crashing specialist — assert the DoD gates in §3 hold and a bounded recovery is attempted.

**DoD additions (extend §3):** a BLOCKED-quality run triggers ≤ `quality_recovery_max_passes` recovery pass(es); it either ships or terminates with the recovery attempt recorded in the manifest; recovery never lowers the committed score; zero variant-of-variant reframes; zero "ran on STRONG" logs for budget-refused retries.

---

### 5.8 Reference pseudocode — `_recover_from_blocked`

```python
async def _recover_from_blocked(self, dag, report, score):
    cfg = get_settings()
    max_passes = int(getattr(cfg, "quality_recovery_max_passes", 1))
    if max_passes <= 0:
        return report, score                      # feature-flagged off → old behavior
    deadline = time.time() + float(getattr(cfg, "recovery_wall_clock_seconds", 300))
    best_report, best_score = report, score        # monotonicity snapshot

    for pass_no in range(1, max_passes + 1):
        if time.time() > deadline:
            break
        plan = [                                    # DIAGNOSE + PLAN + P3 filter
            action for blocker in best_score.integrity_blockers
            for action in [self._remediation_for(blocker, dag)]
            if action and can_make_progress(action, get_engine_health())
        ]
        if not plan:
            break                                   # nothing capable of helping → DEGRADE
        for action in plan:                          # RECOVER (idempotent task ids)
            await self._dispatch_recovery(action, dag, pass_no)
        cand_report, cand_score, _ = await self._quality_iteration_loop(
            dag, self._rebuild_report(dag), self._fact_check_report,
        )                                            # RE-SCORE via existing authority
        self._manifest.record_recovery(pass_no, plan, best_score, cand_score)
        gain = cand_score.total_score - best_score.total_score
        if cand_score.approved:                      # SHIP
            return cand_report, cand_score
        if gain >= float(getattr(cfg, "quality_recovery_min_score_gain", 0.05)):
            best_report, best_score = cand_report, cand_score   # commit, loop
        else:
            break                                    # no real progress → keep best, DEGRADE

    return best_report, best_score                   # caller re-reads terminal_state (honest BLOCK/caveat)
```

**Net effect on the audited run:** D-A/D-B/D-C/D-D/D-K would have prevented COMPETE's death and populated the report; but *even if* the report still arrived with `Unknown` + verdict contradiction, the Recovery Supervisor would classify both blockers, re-dispatch FINANCE ("typed gap, never Unknown") and SYNTHESIS (single verdict), re-score once, and either ship or terminate **with a recorded, honest recovery attempt** — instead of discarding a perfect diagnosis and dying. That is the line between a wrapper and a proprietary self-healing system.
