# HYPERION OVERHAUL 2 — The Output Contract (Final Architecture, No More Patches)

**Audit date:** 2026-08-11
**Evidence:** full TUI log + Docker log of the 2026-08-10 17:10→17:52 engagement ("should india build more in-house space startups…"), build `50065cf+dirty`, diagnostic `reports/diagnostics/blocked_eng_217c6b3ff979.json`, line-level code read of this snapshot
**Posture:** this is the root-cause document. Every claim below cites a code site or a log line you can re-check in minutes.
**Execution owner:** the next remediation session (DeepSeek v4 flash — runbook in §5 is written for it)

---

## 1. Verdict — "Docker? SearXNG? Architectural?"

**Architectural. Docker and SearXNG are accomplices, not the cause.**

One sentence: **Hyperion has three separate "truths" about whether work happened — the message-bus status broadcast, the DAG's `task.status`, and the orchestrator's `_task_outputs` bag — with no single writer and no contract between them; and findings travel on a fourth, disconnected channel (`_all_findings`) that the report layer cannot trace back to sources.**

The previous overhaul (overhaul.md) built the **Evidence** control plane (ledger, preflight, KPI telemetry). It never built the **Output** control plane: the guarantee that `task completed ⇒ output object exists ⇒ synthesis consumes it ⇒ report carries its sources`. That missing guarantee is why **87 collected findings produced a report with 0 citable domains and the Quality Gate — the only honest component — blocked delivery after 39 minutes and 482k tokens.**

Contribution split for this run:

| Layer | Contribution | Verdict |
|---|---|---|
| Docker/WSL2 | Intermittent `Temporary failure in name resolution` (mwmbl, arxiv, openalex, OSM at 23:14–23:17) — flaky embedded DNS under load | **Aggravating**, fixable in 20 lines |
| SearXNG | brave perma-banned (anonymous HTML scrape, 429 `suspended_time=180` re-tripping all run); github 403; wikipedia 429; openalex 400 on over-long queries | **Aggravating**, mostly upstream state |
| **Hyperion config generator** | reference replica **rejects 100% of `categories=reference` requests** with `Invalid value: "['reference']"` — its own settings file never declares that category | **Hard bug, ours** |
| **Hyperion architecture** | task-output contract broken (synthesis refuses to run), `sub_findings` crash in 10 specialists, preflight GREENs a 2/3-dead fleet, provenance unenforced in schema | **The cause** |

Proof it is architectural: at 17:13:31 the preflight itself reported `web=0d/0e · reference=0d/0e` — two of three source classes dead **at second zero** — and still licensed a full 16-task DAG. Nothing downstream of that decision could have succeeded, and the system spent 39 minutes discovering it. A SearXNG fix alone changes nothing; the Aug-10 run already proved that (fix0.1→0.3 all "worked").

---

## 2. Run autopsy — the "87 findings → zero report" chain, end to end

| # | Log evidence (this run) | Mechanism (code site) |
|---|---|---|
| B-1 | `CORPUS PREFLIGHT GREEN: corpus contract met (16/8 domains, 29 items; web=0d/0e; scholar=16d/29e; reference=0d/0e)` | `corpus_preflight.py:185-190` — GREEN is decided on the **TOTAL** domain count only. Two dead classes + one live class = full DAG. Per-class status is recorded and never gated. |
| B-2 | Docker `22:43:25 searxng-reference ERROR searx.webapp: Invalid value: "['reference']"` (17:13 UTC = the preflight canary) | `corpus_preflight.py:43` sends `categories=reference`; `searxng.py:348-352` maps it to the reference profile; `searxng.py:759-763` forwards the raw category name; `searxng_settings.reference.yml` declares **no engine** in a `reference` category → SearXNG rejects the request with HTTP 400 **before any engine runs**. Reference class is dead by config, not by network. |
| B-3 | `17:43:43 ✗ cannot access local variable 'sub_findings' where it is not associated with a value` | 10 specialists wire the P-CORE funnel with `sub_findings` assigned **inside** an `if` guard but consumed **outside** it. E.g. `ma_analyst.py:1227-1234`: `if short_list:` assigns; line 1234 `self._detect_sub_agent_contradictions(sub_findings)` runs unconditionally → `UnboundLocalError` whenever the list is empty. Same defect in technology/sustainability/strategy/risk/regulatory/operations/innovation/financial/consumer (sites in §3 D2). |
| B-4 | M&A: reconciled sub-agent findings never published unless contradictions exist | `ma_analyst.py:1242-1243` — the `for _reconciled in self._sub_agent_reconciled: await self._publish_finding(...)` loop is indented **inside** `if self._sub_agent_contradictions:`. No contradictions → sub-agent evidence silently dropped. (competitive_intel.py:1452 has it right.) |
| B-5 | `17:49:39 competitive_intel: completed with 12 findings` followed by `17:51:21 ✗ SYNTHESIS: task 'task_synthesis_lead' depends on 'task_competitive_intel' which has no output (status=completed)` | Three-way contract violation: (a) REFRAMED variants get **new IDs** with `dependencies=[]` (`orchestrator.py:1307-1326`) and their outputs land under the variant ID — the original task's output slot stays empty forever; (b) `engagement_director.py:766-777` marks **every** task carrying the agent's name `COMPLETED` on a bare bus "done" broadcast — including the FAILED original — so the DAG says "completed" while `_task_outputs` has no entry; (c) `workflow.py:269-290` releases dependents when a dep is FAILED ("run with partial findings") while `orchestrator.py:710-723` **raises** `MissingDependencyOutput` on exactly that condition. Scheduler and executor disagree; one failed specialist ⇒ synthesis and fact-check are *guaranteed* to die. |
| B-6 | `SYNTHESIS: no FinalReport produced — building floor-report fallback from 87 collected findings` → `QUALITY: CORPUS FLOOR: only 0 distinct source domain(s)` | Floor report attaches `finding.sources` (`orchestrator.py:2431-2437`). 0 domains ⇒ all 87 findings carried **zero source URLs**. Provenance is still advisory: `KeyFinding.sources` defaults to `[]` (`models.py:293`) and the promised P3.2 validator ("no substantive finding without a bound source") **does not exist in the schema** — the comment at `models.py:311-317` describes intent; no validator enforces it. Sub-agent citation binding (`sub_agent.py:1736-1797`) works only when the LLM echoes valid `[E]` IDs; specialists' own findings go out with `sources=[]` whenever their searches failed, and nothing stops them. |
| B-7 | `_recheck_corpus_midrun` never fired a degradation despite a dead fleet | `orchestrator.py:3384-3387` counts **all** ledger domains — including the 16 **preflight canary** domains recorded at t=0. Canary evidence is indistinguishable from engagement evidence (`stage="discovery"` for both), so the mid-run gate reads the preflight forever and never sees the collapse. |
| B-8 | `SUB-AGENT concurrent budget reached (3/3); proceeding without spawning` ×3 (RISK) | `base.py:1283-1289` — cap hit ⇒ the work item is **silently dropped** (`return []`). No queue, no retry, no escalation. On a system where sub-agents routinely time out on a sick pool, dropped specs are lost work, and the parent proceeds on thinner data without saying so. |
| B-9 | RISK findings include "Money Laundering Risks in the Real Estate Sector", "Regulatory Risks in the Energy Sector" for a space-sector question; reconciliation then computes "contradictions" between irrelevant corpora | No topicality guard anywhere in the sub-agent → parent funnel. Broadened queries drift off-topic until *something* returns; whatever returns is summarized and counted. |
| B-10 | `QUALITY iteration 2/3: score=2.9 … produced no score change — terminating early` → BLOCKED | The early-terminate fired (good) but only **after** two polish iterations of an evidence-free floor report. A `CORPUS FLOOR` integrity blocker should skip iteration entirely — polishing cannot create evidence. |
| B-11 | Docker: brave 429 ×6 across 40 min; github 403; wikipedia 429; openalex `400 Bad Request` (paragraph-length `search=` param); arxiv/pubmed/crossref timeouts; `Temporary failure in name resolution` bursts | Capacity reality: the anonymous web pool is banned state, not bad luck; openalex rejects queries >~a sentence; container DNS flakes under load (WSL2). All fixable — but they are capacity items, not the reason there was no report. |

**The sentence to remember:** the fleet was crippled (capacity), the pipeline fabricated thin-but-sourceless findings (provenance), the DAG lost the outputs that did exist (contract), and the gate blocked at the end (the only part that worked). Fixing any one alone reproduces this run.

---

## 3. The seven core defects (what the last overhaul missed and why)

- **D1 — No single writer of execution truth.** Director writes `task.status` from bus broadcasts by *agent name*; orchestrator writes it by *task ID* with the output object; the reframer creates new IDs without aliasing outputs back; the scheduler (FAILED-dep-is-ready) and the executor (missing-output-is-fatal) encode *opposite* policies. → Fix in W1.
- **D2 — `sub_findings` UnboundLocalError in 10 specialists** (copy-pasted P-CORE wiring placed outside its guard), plus M&A's publish loop gated on contradictions. → Fix in W0 (this is a crash; it goes first).
- **D3 — Reference replica category contract broken in the generator.** 100% of `categories=reference` traffic 400s. → Fix in W0 (config; highest yield-per-line in the whole document).
- **D4 — Preflight measures the wrong quantity.** Total-only GREEN; canary evidence pollutes the ledger so the mid-run recheck can never detect a collapse; no per-class floors. → Fix in W2.
- **D5 — Provenance is a comment, not a validator.** `KeyFinding` accepts substantive findings with `sources=[]`; floor report then renders a 0-domain report; the corpus floor discovers it at minute 39. → Fix in W3.
- **D6 — Sub-agent budgets are silent work-destroyers.** Concurrent-cap drop discards the spec; no auto-raise on retry (your requirement: 3→5), no deferred queue. → Fix in W4.
- **D7 — Retrieval hygiene gaps that are ours** (not upstream): no DNS fallback in compose, no clamp on scholar query length, no topicality guard in the funnel, quality loop polishing floor reports. → Fix in W4/W5.

---

## 4. Target architecture — the Output Contract (5 invariants)

After this overhaul, these hold **by construction**, not by convention:

- **OC-1 · Single writer.** Only the orchestrator's execution record writes `task.status = COMPLETED/FAILED` and `_task_outputs`. The Director's bus handler is display-only. A task cannot be "completed" without an output object — the two writes are one statement.
- **OC-2 · Dependency outputs are aliased, never lost.** A successful reframed variant backfills its origin's output slot. Synthesis and fact-check receive *available* outputs + the full findings channel + a typed `missing_dependencies` list — they run on partial context **by design**, never crash on it.
- **OC-3 · Findings are source-bound in the schema.** A substantive `KeyFinding` with zero usable source URLs is retyped `unverified_assertion` at construction. Floor reports, KPI-3, and the corpus floor all read the same enforced truth.
- **OC-4 · Corpus gates measure per-class and delta.** Preflight GREEN requires every source class alive (per-class floors) *and* the total floor. Canaries are stage-tagged `preflight`; the mid-run recheck counts only engagement-retrieved domains.
- **OC-5 · Budgets degrade loudly and recover.** Concurrent-cap pressure auto-raises 3→5 (bounded) on retry and defers—never silently drops—specs. A corpus-floor integrity blocker skips quality iteration entirely (no polishing vacuum).

Runtime shape (unchanged from overhaul.md §8 — this overhaul makes the edges real):

```
PREFLIGHT (per-class floors, stage-tagged) → GREEN/AMBER/RED
   → DAG waves (orchestrator is the only status writer)
   → specialists (crash-free funnel, schema-bound provenance)
   → synthesis + factcheck (partial-context-safe, findings-channel fed)
   → quality gate (verifier; corpus floor already known per stage)
   → render
```

---

## 5. Implementation runbook (for DeepSeek v4 flash)

**Operating rules for the executing model — read first:**
1. Execute steps in order: **W0 → W1 → W2 → W3 → W4 → W5 → W6**. Do not batch two steps into one edit pass.
2. Anchors are **exact current code snippets**. Match on the snippet, never on the line number (lines drift the moment you edit).
3. After every step run its VERIFY command. On failure: revert that step's edit, re-read the file region, retry once. If it still fails, stop and report — do not improvise around it.
4. Test runner (Windows venv in repo root): `.venv\Scripts\python.exe -m pytest tests/ -x -q`
5. The live stack runs under WSL2/Docker (`/home/abuzar/Hyperion`). The working tree you edit is `C:\Users\Abuza\Downloads\Hyperion-fix0.3\Hyperion-fix0.3`. Config-file changes require `docker compose up -d --force-recreate <service>` in the deployed tree to take effect.
6. Never lower the corpus floor (8), the ship floor (3.0), or delete a gate to make a run pass. Never add keyed search APIs (product decision, overhaul.md P1.4).

### W0 — Crash + config (the run dies without these)

---

**S1 · Fix the reference-replica category contract (D3) — highest yield-per-line in this document**

GOAL: `categories=reference` must be a valid selection on the reference instance.

FILE: `searxng_settings.reference.yml`

ANCHOR (current):
```yaml
engines:
- name: wikipedia
  engine: wikipedia
  shortcut: wp
  disabled: false
  timeout: 10
  weight: 1.0
- name: openstreetmap
  engine: openstreetmap
  shortcut: osm
  disabled: false
  timeout: 12
  weight: 0.6
- name: github
  engine: github
  shortcut: gh
  categories: it
```

REPLACE WITH (every engine gains the `reference` category; keep existing categories as a list):
```yaml
engines:
- name: wikipedia
  engine: wikipedia
  shortcut: wp
  categories: [general, reference]
  disabled: false
  timeout: 10
  weight: 1.0
- name: openstreetmap
  engine: openstreetmap
  shortcut: osm
  categories: [general, reference]
  disabled: false
  timeout: 12
  weight: 0.6
- name: github
  engine: github
  shortcut: gh
  categories: [it, reference]
```
Apply the same `[it, reference]` change to `stackexchange` and `hackernews` entries.

THEN: find the generator that emits this file (`hyperion/infra/searxng_profiles.py` — the file header says GENERATED). Update the generator's reference-profile engine definitions to emit the same list-form categories, so regeneration cannot regress the fix. If the generator builds engines from a table, add the category there.

VERIFY:
```powershell
docker compose up -d --force-recreate searxng-reference
curl.exe -s -o NUL -w "%{http_code}" "http://127.0.0.1:8889/search?q=india+space&format=json&categories=reference"
```
Expect `200`. Then `curl.exe -s "http://127.0.0.1:8889/search?q=india+space&format=json&categories=reference"` and confirm a `results` array. If you get 400 with `Invalid value`, the running container is still using the old file — recreate it in the deployed tree.

---

**S2 · Kill the `sub_findings` UnboundLocalError in all 10 specialists (D2)**

GOAL: one never-raising ingestion path; no hand-wired copies.

STEP 1 — add the helper. FILE: `hyperion/agents/base.py`. Insert after the `_detect_sub_agent_contradictions` method:

```python
    async def _ingest_sub_findings(self, sub_findings: list[KeyFinding] | None) -> None:
        """OVERHAUL2 S2: the single sub-agent ingestion path.

        Replaces the 10 hand-wired assign/merge/reconcile/contradiction
        blocks whose ``sub_findings`` was assigned inside an ``if`` guard and
        consumed outside it (UnboundLocalError on empty collections), and
        whose publish loop was in one file gated on contradictions existing.
        Never raises; always publishes reconciled findings.
        """
        sub_findings = list(sub_findings or [])
        self._sub_agent_findings = sub_findings
        try:
            self._sources = self._merge_evidence(sub_findings, getattr(self, "_sources", []))
            self._sub_agent_reconciled = self._reconcile_findings(sub_findings)
            self._sub_agent_contradictions = self._detect_sub_agent_contradictions(sub_findings)
        except Exception as exc:  # noqa: BLE001 - ingestion must never break analysis
            logger.warning("_ingest_sub_findings failed: %s", exc)
            self._sub_agent_reconciled = []
            self._sub_agent_contradictions = []
        if self._sub_agent_contradictions:
            self._log(
                "SUB-AGENT RECONCILIATION: {} contradiction(s) surfaced: {}".format(
                    len(self._sub_agent_contradictions),
                    "; ".join(self._sub_agent_contradictions[:3]),
                )
            )
        for _reconciled in self._sub_agent_reconciled:
            await self._publish_finding(_reconciled)
```

STEP 2 — rewrite each guarded call site to this shape (example is M&A):

FILE `hyperion/agents/specialists/ma_analyst.py`. ANCHOR:
```python
        if short_list:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning M&A data collection "
                "sub-agents")
            sub_findings = await self._spawn_ma_sub_agents(self._acquisition_criteria, sector, short_list)
            self._sub_agent_findings = sub_findings
            self._sources = self._merge_evidence(sub_findings, self._sources)
            self._sub_agent_reconciled = self._reconcile_findings(sub_findings)
        self._sub_agent_contradictions = self._detect_sub_agent_contradictions(sub_findings)
        if self._sub_agent_contradictions:
            self._log(
                "SUB-AGENT RECONCILIATION: {} contradiction(s) surfaced: {}".format(
                    len(self._sub_agent_contradictions),
                    "; ".join(self._sub_agent_contradictions[:3]),
                )
            )
            for _reconciled in self._sub_agent_reconciled:
                await self._publish_finding(_reconciled)
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with "
                "analysis")
```
REPLACE WITH:
```python
        sub_findings: list[KeyFinding] = []
        if short_list:
            await self._transition(AgentState.SUB_AGENT_SPAWNED, "Spawning M&A data collection "
                "sub-agents")
            sub_findings = await self._spawn_ma_sub_agents(self._acquisition_criteria, sector, short_list)
        await self._ingest_sub_findings(sub_findings)
        if short_list:
            await self._transition(AgentState.WORKING, "Sub-agents returned, proceeding with "
                "analysis")
```

STEP 3 — repeat the identical rewrite at these guarded sites (same pattern: spawn inside `if`, consumption outside):

| File | Guarded block starts near |
|---|---|
| `technology_analyst.py` | `sub_findings = await self._spawn_vendor_sub_agents(vendors, technology)` |
| `sustainability_analyst.py` | `self._spawn_sustainability_sub_agents(company, sector, jurisdictions)` |
| `strategy_analyst.py` | `self._spawn_strategy_sub_agents(sector, company)` |
| `risk_analyst.py` | `self._spawn_risk_sub_agents(industry, jurisdiction, space)` |
| `regulatory_analyst.py` | `self._spawn_regulatory_sub_agents(jurisdictions, industry)` |
| `operations_analyst.py` | `self._spawn_ops_sub_agents(industry, sector, process_type)` |
| `innovation_analyst.py` | `self._spawn_innovation_sub_agents(space, sector, technologies)` |
| `financial_analyst.py` | `self._spawn_financial_sub_agents(tickers, industry, business_model)` |
| `consumer_insights.py` | `self._spawn_consumer_sub_agents(company, sector, product_category, segment)` |

STEP 4 — route the two unguarded sites (`competitive_intel.py:1440-1453`, `market_analyst.py:1388-1400`) through `_ingest_sub_findings` as well (delete their hand-wired merge/reconcile/contradiction/publish blocks). Uniformity is the point: exactly one ingestion path exists after this step.

VERIFY: `.venv\Scripts\python.exe -m pytest tests/test_p_core_reconciliation.py -q` and a compile pass: `.venv\Scripts\python.exe -m compileall hyperion/agents -q`. Then grep — there must be ZERO remaining matches: `rg "_detect_sub_agent_contradictions\(sub_findings\)" hyperion/agents/specialists`.

### W1 — The Output Contract (the "87 findings → zero report" fix)

---

**S3 · Orchestrator is the single writer of task status (D1a)**

FILE: `hyperion/agents/engagement_director.py`. ANCHOR:
```python
        # Find tasks for this agent and update status
        for task in self._current_dag.tasks:
            if task.agent != agent_name:
                continue
            if state_str == "working" and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
            elif state_str == "done":
                task.status = TaskStatus.COMPLETED
                task.completed_at = time.time()
            elif state_str == "blocked":
                task.status = TaskStatus.FAILED
                task.error = payload.get("detail", "")
```
REPLACE WITH:
```python
        # OVERHAUL2 S3: the orchestrator is the SINGLE WRITER of execution
        # truth. This handler previously marked EVERY task carrying this
        # agent's name COMPLETED on a bare "done" broadcast — including
        # FAILED originals and unrelated reframed variants — producing
        # "status=completed but no output" tasks that crashed synthesis
        # (MissingDependencyOutput). Bus heartbeats now only drive the
        # PENDING→RUNNING display transition; terminal states are written
        # exclusively by the orchestrator together with the output object.
        for task in self._current_dag.tasks:
            if task.agent != agent_name:
                continue
            if state_str == "working" and task.status == TaskStatus.PENDING:
                task.status = TaskStatus.RUNNING
                task.started_at = time.time()
```

VERIFY: `.venv\Scripts\python.exe -m pytest tests/ -k "director" -q`

---

**S4 · Synthesis and fact-check are partial-context-safe (D1b)**

FILE: `hyperion/orchestrator.py`. ANCHOR (in `_execute_task`):
```python
        context: dict[str, Any] = {}
        for dep_id in task.dependencies:
            if dep_id in self._task_outputs:
                dep_output = self._task_outputs[dep_id]
                dep_task = dag.get_task(dep_id)
                if dep_task:
                    context[dep_task.agent.value] = dep_output
            else:
                dep_task = dag.get_task(dep_id)
                dep_status = dep_task.status.value if dep_task else "missing"
                raise MissingDependencyOutput(
                    f"task '{task.id}' ({task.agent.value}) depends on "
                    f"'{dep_id}' which has no output (status={dep_status}) — "
                    f"refusing to run with a partial context"
                )
```
REPLACE WITH:
```python
        context: dict[str, Any] = {}
        missing_deps: list[str] = []
        for dep_id in task.dependencies:
            if dep_id in self._task_outputs:
                dep_output = self._task_outputs[dep_id]
                dep_task = dag.get_task(dep_id)
                if dep_task:
                    context[dep_task.agent.value] = dep_output
            else:
                missing_deps.append(dep_id)
        if missing_deps:
            # OVERHAUL2 S4: the scheduler (workflow.get_ready_tasks) already
            # licenses FAILED dependencies — "run with partial findings".
            # Synthesis and fact-check are the aggregation stages: their
            # inputs are the FINDINGS CHANNEL plus whatever outputs exist.
            # Crashing them on a missing dep converts one specialist failure
            # into a zero-report run (the 17:51:21 incident). Specialists
            # keep the strict contract (they need real inputs).
            if task.agent not in (AgentName.SYNTHESIS_LEAD, AgentName.FACT_CHECKER):
                dep_status = "unknown"
                dep_task = dag.get_task(missing_deps[0])
                if dep_task is not None:
                    dep_status = dep_task.status.value
                raise MissingDependencyOutput(
                    f"task '{task.id}' ({task.agent.value}) depends on "
                    f"'{missing_deps[0]}' which has no output (status={dep_status}) — "
                    f"refusing to run with a partial context"
                )
            self._log(
                f"{task.agent.value}: proceeding with partial context — "
                f"missing dependency outputs: {missing_deps}"
            )
            context["missing_dependencies"] = missing_deps
            async with self._findings_lock:
                context["collected_findings"] = list(self._all_findings)
```

VERIFY: `.venv\Scripts\python.exe -m pytest tests/ -k "orchestr or dag or synthesis" -q`

---

**S5 · Reframed variants backfill the original task's output (D1c)**

FILE: `hyperion/orchestrator.py`. ANCHOR (success path of `_execute_task`):
```python
            self._task_outputs[task.id] = result
            self._publish_task_update(task)
```
REPLACE WITH:
```python
            self._task_outputs[task.id] = result
            self._publish_task_update(task)
            # OVERHAUL2 S5: a successful reframed variant satisfies the
            # ORIGINAL task's downstream contract. Without this alias,
            # dependents keep pointing at an output slot the reframer's new
            # task IDs never fill.
            reframed_from = getattr(task, "reframed_from", None)
            if reframed_from and reframed_from not in self._task_outputs:
                self._task_outputs[reframed_from] = result
                original = dag.get_task(reframed_from)
                if original is not None and original.status == TaskStatus.FAILED:
                    original.status = TaskStatus.COMPLETED
                    original.error = ""
                    self._publish_task_update(original)
```

VERIFY: `.venv\Scripts\python.exe -m pytest tests/test_phase4_loop_controller.py -q`

### W2 — Corpus gates measure the right quantities

---

**S6 · Per-class preflight floors (D4a)**

FILE: `hyperion/agents/support/corpus_preflight.py`. ANCHOR (inside `_evaluate_contract`):
```python
    evidence_items = len(records)
    distinct_domains = len(all_domains)
    if distinct_domains >= min_domains:
        status = CorpusStatus.GREEN
    elif distinct_domains > 0:
        status = CorpusStatus.AMBER
    else:
        status = CorpusStatus.RED
```
REPLACE WITH:
```python
    evidence_items = len(records)
    distinct_domains = len(all_domains)

    # OVERHAUL2 S6: a fleet with a dead source class is NOT green. The
    # 17:13 run went GREEN on scholar alone (web=0d, reference=0d) and
    # fanned out a full 16-task DAG over two dead classes. GREEN requires
    # the total floor AND a per-class pulse; any dead class degrades to
    # AMBER (reduced DAG + that class's queries rerouted to living classes).
    _PER_CLASS_MIN_DOMAINS = {"web": 1, "scholar": 2, "reference": 1}
    dead_classes = [
        p.source_class
        for p in per_class
        if p.distinct_domains < _PER_CLASS_MIN_DOMAINS.get(p.source_class, 1)
    ]
    if distinct_domains == 0:
        status = CorpusStatus.RED
    elif distinct_domains >= min_domains and not dead_classes:
        status = CorpusStatus.GREEN
    else:
        status = CorpusStatus.AMBER
```
Also append the dead-class list to the AMBER `detail` string: `f"dead/thin classes: {dead_classes}"`.

VERIFY: `.venv\Scripts\python.exe -m pytest tests/ -k "preflight or corpus" -q`. Then a live check: with the stack up, run one engagement and confirm the boot log shows `reference>=1d` (post-S1) or the contract degrades to AMBER instead of a fake GREEN.

---

**S7 · Stage-tag canary evidence; mid-run recheck counts engagement evidence only (D4b)**

FILE: `hyperion/tools/evidence_ledger.py` — add this method to `EvidenceLedger` (next to `record`):

```python
    def retag_stage(self, *, urls: set[str], stage: str) -> int:
        """OVERHAUL2 S7: re-stage already-recorded URLs (preflight canaries).

        Canary evidence is real evidence, but gates that measure mid-run
        collapse must be able to exclude it. First-sighting-wins record
        semantics mean a simple re-record cannot change a stage, so this
        rewrites the stored record under the lock. Returns rows changed.
        """
        changed = 0
        with self._lock:
            for key, ev in list(self._items.items()):
                if ev.url in urls and ev.stage != stage:
                    self._items[key] = Evidence(
                        url=ev.url, domain=ev.domain, title=ev.title,
                        snippet=ev.snippet, content_hash=ev.content_hash,
                        engine=ev.engine, profile=ev.profile, stage=stage,
                        fetched_at=ev.fetched_at, run_id=ev.run_id,
                    )
                    changed += 1
        return changed
```

FILE: `hyperion/agents/support/corpus_preflight.py`. In `_fire_canaries`, ANCHOR:
```python
    query = SubAgentRunner._condense_query(question)
    client = SearxNGClient(settings=settings)
```
REPLACE WITH:
```python
    query = SubAgentRunner._condense_query(question)
    client = SearxNGClient(settings=settings)
    # OVERHAUL2 S7: tag canary evidence so mid-run gates can exclude it.
    from hyperion.tools.evidence_ledger import get_evidence_ledger
    _ledger = get_evidence_ledger()
    _before = {e.url for e in _ledger.all()}
```
and in the same function's `finally` block, after `await client.close()`:
```python
        _new_urls = {e.url for e in _ledger.all()} - _before
        if _new_urls:
            _ledger.retag_stage(urls=_new_urls, stage="preflight")
```

FILE: `hyperion/orchestrator.py`, `_recheck_corpus_midrun`. ANCHOR:
```python
            ledger = get_evidence_ledger()
            domains = len(ledger.distinct_domains())
```
REPLACE WITH:
```python
            ledger = get_evidence_ledger()
            # OVERHAUL2 S7: measure ENGAGEMENT-retrieved evidence only.
            # Preflight canary records persist in the ledger; counting them
            # made this gate read the t=0 probe forever and never detect a
            # mid-run fleet collapse (B-7).
            domains = len({
                e.domain for e in ledger.all()
                if e.domain and e.stage != "preflight"
            })
```

VERIFY: `.venv\Scripts\python.exe -m pytest tests/test_phase5_verification.py -q`

### W3 — Provenance enforced in the schema

---

**S8 · A substantive finding without a source is not a finding (D5)**

FILE: `hyperion/schemas/models.py`. ANCHOR:
```python
    # P2-16: a placeholder is unrepresentable; a gap is an AnalysisGap.
    _reject_filler = field_validator("content", "title", "implications")(
        _reject_banned_filler
    )
```
INSERT AFTER (plus add `model_validator` to the pydantic import at the top of the file):
```python
    @model_validator(mode="after")
    def _enforce_provenance(self) -> "KeyFinding":
        """OVERHAUL2 S8: provenance is schema-enforced, not advisory.

        The 17:52 block: 87 findings → 0 report domains, because substantive
        findings shipped with ``sources=[]`` whenever the author's searches
        failed. A substantive finding with no usable source URL is retyped
        ``unverified_assertion`` at construction — it is then excluded from
        yield, floor reports and the corpus floor BY the existing
        NON_SUBSTANTIVE filters, everywhere, automatically.
        """
        if self.finding_type in NON_SUBSTANTIVE_FINDING_TYPES:
            return self
        if not any(getattr(s, "url", "") for s in self.sources):
            object.__setattr__(self, "finding_type", UNVERIFIED_ASSERTION_TYPE)
            object.__setattr__(self, "confidence", ConfidenceLevel.LOW)
        return self
```
NOTE on constants: `NON_SUBSTANTIVE_FINDING_TYPES` / `UNVERIFIED_ASSERTION_TYPE` are defined at `models.py:318-320`, AFTER the class. Move the three constant definitions ABOVE `class KeyFinding` so the validator can reference them at validation time (module order matters at class-definition time only for decorators — a `mode="after"` validator reads them at call time, but keep the move for clarity and import safety).

WARNING to the executing model: this will retype existing test fixtures that construct sourceless "substantive" findings. Run the full suite; update fixtures by either giving them a `Source(url=...)` or asserting the retype. Do NOT weaken the validator to make fixtures pass.

VERIFY: `.venv\Scripts\python.exe -m pytest tests/ -x -q` (full suite green after fixture updates).

### W4 — Budgets that recover + retrieval hygiene

---

**S9 · Concurrent sub-agent budget auto-raises 3→5 on pressure (D6 — operator requirement)**

GOAL (operator's exact requirement): when a spawn is blocked by the concurrent cap, the system raises the per-specialist concurrent budget toward 5 on the next attempt/retry instead of silently dropping the work item; blocked specs are deferred, not discarded.

STEP 1 — config. FILE: `hyperion/config.py` (next to the other sub-agent policy settings) and `.env.example`:
```python
    #: OVERHAUL2 S9: hard upper bound for the per-specialist CONCURRENT
    #: sub-agent budget under pressure. Starts at each spec's
    #: max_sub_agents (3); cap pressure raises it toward this ceiling.
    sub_agent_concurrent_max: int = 5
```
`.env.example`: `HYPERION_SUB_AGENT_CONCURRENT_MAX=5`

STEP 2 — adaptive cap + deferred queue. FILE: `hyperion/agents/base.py`.

Add near `SUB_AGENT_TOTAL_CEILING = 6`:
```python
    #: OVERHAUL2 S9: concurrent-cap pressure raises the concurrent budget
    #: toward this bound; the sequential TOTAL ceiling above is unaffected.
    SUB_AGENT_CONCURRENT_MAX = 5
```

Replace the `max_sub_agents` property (ANCHOR: `return self.spec.max_sub_agents`) with:
```python
        boost = getattr(self, "_concurrent_boost", 0)
        return min(self.spec.max_sub_agents + boost, self.SUB_AGENT_CONCURRENT_MAX)
```

Replace the concurrent-drop branch (ANCHOR):
```python
        if not spec.broadened and self.state.sub_agents_active >= self.max_sub_agents:
            self._log(
                f"SUB-AGENT concurrent budget reached "
                f"({self.state.sub_agents_active}/{self.max_sub_agents}); "
                f"proceeding without spawning: {spec.question[:80]}"
            )
            return []
```
WITH:
```python
        if not spec.broadened and self.state.sub_agents_active >= self.max_sub_agents:
            # OVERHAUL2 S9: pressure raises the budget (3→…→5) and DEFERS
            # the spec — the old branch silently discarded the work item.
            if self.max_sub_agents < self.SUB_AGENT_CONCURRENT_MAX:
                self._concurrent_boost = getattr(self, "_concurrent_boost", 0) + 1
                self._log(
                    f"SUB-AGENT concurrent budget raised to {self.max_sub_agents} "
                    f"(cap pressure; deferred: {spec.question[:60]})"
                )
            deferred = getattr(self, "_deferred_specs", None)
            if deferred is None:
                deferred = []
                self._deferred_specs = deferred
            deferred.append(spec)
            return []
```

STEP 3 — drain on slot release. In the same `_spawn_sub_agent`, find the `finally`/return path where `self.state.sub_agents_active` is decremented after a spawn completes, and append:
```python
            # OVERHAUL2 S9: a released slot drains the deferred queue first.
            deferred = getattr(self, "_deferred_specs", None)
            if deferred and self.state.sub_agents_active < self.max_sub_agents:
                next_spec = deferred.pop(0)
                self._log(f"SUB-AGENT deferred spawn dispatched: {next_spec.question[:60]}")
                await self._spawn_sub_agent(next_spec)
```
(If the decrement happens in the caller rather than the runner, put the drain there — one drain site only. Recursion depth is naturally bounded by the queue.)

STEP 4 — retry waves jump straight to the ceiling (operator: "increase to 5 in next attempt or retry"). In `_spawn_sub_agent`, right after the budget gates, add:
```python
        if spec.broadened and self.max_sub_agents < self.SUB_AGENT_CONCURRENT_MAX:
            self._concurrent_boost = self.SUB_AGENT_CONCURRENT_MAX - self.spec.max_sub_agents
```

VERIFY: `.venv\Scripts\python.exe -m pytest tests/test_fix03_regressions.py tests/test_phase4_loop_controller.py -q`. Add one new test: cap 3, three active sub-agents, fourth spec arrives → budget becomes 4, spec lands in `_deferred_specs`, nothing is dropped; after a completion, the deferred spec is dispatched.

---

**S10 · Retrieval hygiene (D7a/b)**

STEP 1 — DNS fallback. FILE: `docker-compose.yml`, under `x-searxng-common:` add:
```yaml
  dns: [1.1.1.1, 9.9.9.9]
```
VERIFY: `docker compose up -d --force-recreate` the three replicas; during the next engagement, `docker logs hyperion-searxng-web 2>&1 | Select-String "name resolution"` should show zero new lines.

STEP 2 — clamp scholar query length (openalex 400s on paragraph queries). FILE: `hyperion/tools/searxng.py`, inside `_search_searxng_json` before the retry loop (ANCHOR: `endpoint: SearxngEndpoint | None = None`):
```python
        # OVERHAUL2 S10: scholar APIs 400 on paragraph-length natural-language
        # queries (docker: openalex '400 Bad Request'). Clamp at the client.
        if len(query) > 200:
            query = query[:200].rsplit(" ", 1)[0]
```
VERIFY: next run's docker log shows zero openalex 400s.

### W5 — Funnel hygiene + gate discipline

---

**S11 · Topicality guard in the funnel (D7c — the "money-laundering in a space report" filter)**

FILE: `hyperion/agents/base.py`, inside `_ingest_sub_findings` (S2), immediately after `sub_findings = list(sub_findings or [])`:
```python
        # OVERHAUL2 S11: drop off-topic sub-agent yield BEFORE merge/reconcile.
        # Broadened queries drift until *something* returns; without a guard
        # that something is summarized and counted (B-9: real-estate money
        # laundering inside a space-sector risk analysis). v1 is a blunt,
        # deterministic lexical overlap check against the engagement focus.
        try:
            from hyperion.tools.query_utils import get_engagement_focus
            _fq, _subject, _geo = get_engagement_focus()
            focus_tokens = {
                t.lower() for t in f"{_subject} {_geo}".split() if len(t) >= 4
            }
            if focus_tokens:
                kept, dropped = [], 0
                for f in sub_findings:
                    hay = f"{getattr(f, 'title', '')} {getattr(f, 'content', '')}".lower()
                    if any(tok in hay for tok in focus_tokens):
                        kept.append(f)
                    else:
                        dropped += 1
                if dropped:
                    self._log(f"TOPICALITY: dropped {dropped} off-topic sub-agent finding(s)")
                sub_findings = kept
        except Exception as exc:  # noqa: BLE001 - guard must never break ingestion
            logger.debug("topicality guard skipped: %s", exc)
```
VERIFY: unit test — findings about "real estate" dropped on an `industry="space", geography="India"` engagement; findings mentioning "India" or "space" kept.

---

**S12 · A corpus-floor blocker skips quality iteration entirely (B-10)**

FILE: `hyperion/orchestrator.py`, `_quality_iteration_loop` (starts near line 1966). After the FIRST scoring pass produces `quality_score`, insert before any re-iteration decision:
```python
            # OVERHAUL2 S12: polishing cannot create evidence. A CORPUS FLOOR
            # integrity blocker terminates immediately — two iterations of
            # prose-polish on a sourceless floor report is how the 17:52 run
            # burned its last 2 minutes.
            _blockers = getattr(quality_score, "integrity_blockers", None) or []
            if any("CORPUS FLOOR" in b for b in _blockers):
                self._log("QUALITY: CORPUS FLOOR blocker — skipping iterations, terminal BLOCKED")
                break
```
(Adapt the attribute name to the actual QualityScore field — `grep "integrity_blockers" hyperion/schemas/`.)

### W6 — Permanent regression lock

---

**S13 · Stack contract test (would have caught D3 in CI)**

NEW FILE: `tests/test_searxng_category_contract.py`
```python
"""OVERHAUL2 S13: every profile-named category sent by the client/preflight
must be declared by at least one engine in that profile's settings file."""
import yaml
from hyperion.agents.support.corpus_preflight import _CANARY_CATEGORIES

_PROFILE_FILES = {
    "web": "searxng_settings.web.yml",
    "scholar": "searxng_settings.scholar.yml",
    "reference": "searxng_settings.reference.yml",
}
# categories the client can send to each profile (searxng.CATEGORY_PROFILE +
# preflight canaries), and the general fallback always sent on failover.
_PROFILE_CATEGORIES = {
    "web": {"general", "news"},
    "scholar": {"science", "medical", "general"},
    "reference": {"reference", "it", "geo", "general"},
}

def _declared_categories(path: str) -> set[str]:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    declared: set[str] = set()
    for engine in cfg.get("engines", []):
        cats = engine.get("categories")
        if cats is None:
            declared.add("general")
        elif isinstance(cats, str):
            declared.add(cats)
        else:
            declared.update(cats)
    return declared

def test_every_profile_accepts_the_categories_we_send():
    for profile, path in _PROFILE_FILES.items():
        declared = _declared_categories(path)
        for cat in _PROFILE_CATEGORIES[profile]:
            assert cat in declared, f"{path}: no engine declares category {cat!r}"

def test_canary_categories_match_profiles():
    assert set(_CANARY_CATEGORIES) == {"web", "scholar", "reference"}
```
VERIFY: `.venv\Scripts\python.exe -m pytest tests/test_searxng_category_contract.py -q`

---

**S14 · Live canary battery extension**

FILE: `hyperion/eval/canaries.py` — add one fault-injection canary: `reference-category-400` (mock the reference endpoint returning 400 on `categories=reference` ⇒ assert the preflight marks reference thin and the contract degrades to AMBER, never GREEN). And one contract canary: `missing-dep-output` (a specialist dependency with no output ⇒ synthesis still runs and produces a FinalReport from the findings channel; assert no `MissingDependencyOutput`).

VERIFY: `.venv\Scripts\python.exe -m hyperion.eval.canaries`

---

**S15 · KPI gates for the new invariants**

FILE: `hyperion/eval/kpi.py` — extend with: (a) `pct_tasks_completed_with_output` must be 100; (b) `synthesis_produced_final_report` must be 1 on any run that reaches the synthesis boundary; (c) `off_topic_dropped` counter (from S11 logs) visible in telemetry.

VERIFY: run one engagement; `reports/diagnostics/kpis.json` contains the new keys.

---

**S16 · Update AGENTS.md / ARCHITECTURE.md**

Record OC-1..OC-5, the single-writer rule, the per-class preflight floors, the 3→5 adaptive budget, and the reference-category contract (with the S13 test as its guard). These are now load-bearing invariants; the docs must say so.

---

## 6. Definition of Done (live-run gates — all must pass on the India-space question)

1. Boot log preflight shows **all three classes ≥ their per-class floor** (post-S1 reference must be ≥1d), or the contract is honestly AMBER/RED — never a GREEN with a dead class.
2. Zero `sub_findings` / `UnboundLocalError` lines in the run; zero `Invalid value` lines in docker.
3. With any single specialist force-failed: synthesis **still produces a FinalReport**; the run ends SHIP or honest BLOCKED — never `MissingDependencyOutput`.
4. Final report (or floor report) carries **≥ 8 distinct source domains**, or the run terminates RED/AMBER before synthesis at < 5 minutes and < 50k tokens. "87 findings → 0 domains" is now structurally impossible (S8).
5. Force concurrent-cap pressure: log shows `concurrent budget raised to 4 … 5` and **zero** `proceeding without spawning` drops.
6. Zero `Temporary failure in name resolution` and zero openalex 400s in docker during a healthy run.
7. `tests/` full suite green; `python -m hyperion.eval.canaries` green including the two new canaries.
8. Three consecutive live engagements with KPI-1..5 green (overhaul.md §5) — then, and only then, this is done.

---

## 7. Anti-patterns — the moves that must NOT happen next

1. Do not raise timeouts, retry counts, token budgets, or quality iterations — that is paying more for the same failure.
2. Do not "fix" the Quality Gate (thresholds/messaging). It is the only component that told the truth in both runs.
3. Do not add scraper engines or keyed search APIs. brave's ban is upstream state; capacity comes from the five free egress identities (overhaul.md P1.4) — and from not 400-ing your own reference replica.
4. Do not let any component other than the orchestrator write terminal task status. One writer, or OC-1 is decoration.
5. Do not count preflight canaries as engagement evidence in any gate (S7), and do not let a single living class GREEN the fleet (S6).
6. Do not publish, count, or render a substantive finding with zero sources (S8). Do not drop a sub-agent spec on budget pressure without a deferred queue (S9).
7. Do not batch the W-steps. Each one is separately verifiable; batching is how fix0.1–0.3 lost causal attribution.
8. Do not broaden/reframe against a dead source class — and after S6, the system knows a class is dead at second 40, not minute 39.

---

## 8. Why this is the final nail

The Aug-10 morning run proved retrieval capacity matters (overhaul.md). The Aug-10 evening run proves capacity was **never the binding constraint on producing a report**: with 16 preflight domains, real sub-agent content (MARKET/STRATEGY/RISK all returned on-topic retrieved text), and 87 findings, the system still emitted zero citable domains and no synthesis — because outputs, statuses, and sources have no contract. Overhaul 1 built the Evidence Control Plane. Overhaul 2 builds the **Output Contract**: single writer (S3), aliased dependencies (S4/S5), schema-bound provenance (S8), honest per-class gates (S6/S7), recovering budgets (S9), and the two one-line infra bugs (S1, S10) that were masquerading as "the internet hates us." After W0–W6, every failure mode in both runs either cannot occur or terminates cheaply, loudly, and typed. That is the difference between an architecture and a patch.
