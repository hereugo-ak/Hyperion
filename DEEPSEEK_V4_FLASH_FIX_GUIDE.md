# Working Guide — Fixing HYPERION with DeepSeek‑V4‑Flash

**Purpose:** a copy‑paste operating manual for driving **`deepseek-v4-flash`** (the coding agent) to implement every fix in `overhaul3_audit.md` — the defects **D‑A … D‑L** and the **fail‑safe self‑healing system** in §5 — safely, in order, with a verification gate after each step.

**Audience:** whoever wires DeepSeek‑V4‑Flash into a coding agent (Claude Code / OpenCode / a custom tool‑calling loop) and runs it against this repo.

> Golden rule for this repo: **overhaul 1 and 2 "passed" and the run still BLOCKED.** The reason was always the same — a fix was verified only on the happy path. So here: **every change ships with a test that reproduces the real failure**, and nothing is "done" until its VERIFY command is green.

---

## 1. Model configuration (verified specs)

`deepseek-v4-flash` is the current DeepSeek model (legacy `deepseek-chat` = non‑thinking, `deepseek-reasoner` = thinking mode of the same model). Use these settings:

| Setting | Value | Why |
|---|---|---|
| `model` | `deepseek-v4-flash` | fast, low‑cost, reasoning ≈ V4‑Pro on agent tasks |
| Context window | **1M tokens** | can hold `orchestrator.py` (3.4k lines) + `base.py` + audit + tests at once |
| Max output | **384K tokens** | large multi‑file patches are fine |
| Mode | **thinking enabled** (`thinking: {"type":"enabled"}`) | multi‑file tracing / debugging |
| `reasoning_effort` | `high` normal, **`max` for D‑B, D‑E, and §5 recovery loop** | those touch async control flow |
| Tools | **function calling on** (parallel, up to 128) | read/search/patch/test in one loop |
| Agent loop invariant | **echo `reasoning_content` back on every follow‑up turn after a tool call** | omitting it returns HTTP 400 in thinking mode |
| Structured steps | request **JSON** for the plan/diff summary (include the word "json" + a schema) | machine‑checkable progress |

**Recommended agent loop (per task):** `inspect → state the minimal fix → patch via tools → run the VERIFY command → summarize {files_changed, tests_run, residual_risk} as JSON`. Do not let it free‑run across multiple W‑steps; drive **one D‑item at a time** and stop at each VERIFY.

---

## 2. Non‑negotiable guardrails (paste into the system prompt)

These come straight from `overhaul3_audit.md §4 (Anti‑patterns)` and this repo's conventions. The agent must treat them as hard constraints:

1. **Do not raise retries / timeouts / iteration caps.** D‑A…D‑L are logic bugs, not time budgets. Adding time re‑pays for the same defect.
2. **Do not relax the Quality Gate** thresholds or delete integrity blockers. The gate is the only component that told the truth. Fix the *input*, not the judge.
3. **Do not add keyed search APIs or new scraper engines** (product decision — `overhaul.md` P1.4). Work within the existing 5 free egress identities.
4. **Do not reframe against a dead class or an already‑reframed variant** (D‑E).
5. **Logs must not lie.** A budget‑refused self‑heal must never log "still failed on STRONG tier" (D‑C). Stamp a typed `FailureClass`.
6. **Do not batch W0–W5 into one commit.** Each step is separately verifiable — that is how causal attribution survives.
7. **Do not treat BLOCKED as terminal without a recovery attempt** (D‑F / §5).
8. **Every fix adds a test that fails before the fix and passes after.** No exceptions — this is the rule that overhaul 1 & 2 violated.
9. **Smallest safe patch.** Prefer surgical edits over rewrites; preserve the existing `# W-xx / OVERHAUL2 Sx / F-x` provenance comments.

---

## 3. The work order (implement in this sequence)

Ordering principle from the audit: **crash first → un‑block the pipeline → contain waste → retrieval hygiene → self‑healing loop → regression lock.** Do not reorder — later steps assume earlier ones landed.

### W0 — Crash + arity (highest yield)

**Task S1 (D‑A) — fix ALL FOUR `_log` arity bugs.**
The 2026‑08‑11 AST sweep proved there are exactly **4** sites, not the 2 the audit first named:
- `hyperion/agents/specialists/competitive_intel.py:529`
- `hyperion/agents/specialists/competitive_intel.py:568`
- `hyperion/orchestrator.py:2015`  ← latent, fires on a starved fleet
- `hyperion/orchestrator.py:3341`  ← silent, loses KPI telemetry

Convert each `self._log("… %s …", arg)` to a single f‑string: `self._log(f"… {arg} …")`.
**VERIFY:** create `tests/test_log_arity.py` that AST‑walks `hyperion/**/*.py` and asserts **0** `self._log(` call sites with >1 positional arg, then:
```
python -m pytest tests/test_log_arity.py -q          # must go 4-fail → 0-fail
python -c "import ast,pathlib; print(sum(1 for p in pathlib.Path('hyperion').rglob('*.py') for n in ast.walk(ast.parse(p.read_text(encoding='utf-8',errors='replace'))) if isinstance(n,ast.Call) and getattr(getattr(n,'func',None),'attr',None)=='_log' and len([a for a in n.args if not isinstance(a,ast.Starred)])>1))"
# → must print 0
```

### W1 — Pipeline un‑block (the three state‑drift fixes = Primitive P1)

- **S2 (D‑B)** — `orchestrator.py:727`: when a dependency task exists and its `status == FAILED` (a *specialist crash*, not a missing retrieval input), run the dependent on reduced context carrying `missing_dependencies` — extend the exemption beyond `{SYNTHESIS_LEAD, FACT_CHECKER}` to all specialist consumers (add STRATEGY). Keep the strict raise only for a genuinely missing retrieval artifact.
  **VERIFY:** unit test — a specialist whose dep FAILED runs with `context["missing_dependencies"]`; a missing retrieval input still raises.
- **S3 (D‑C)** — `base.py:1377`: make the budget gate **membership‑aware**: `if spec.question not in distinct_questions and len(distinct_questions) >= self.SUB_AGENT_TOTAL_CEILING: return []`. Fix the self‑heal EXHAUSTED log (`base.py:~1521`) to stamp `BUDGET_REFUSED` vs `PROVIDER_FAILURE`.
  **VERIFY:** `python -m pytest tests/test_fix03_regressions.py -q` + new test "ceiling full, retry of already‑counted question executes".
- **S4 (D‑D)** — `orchestrator.py:1054`: after `self._all_findings.extend(agent._findings)`, also drain `self.bus.get_retained_findings()` filtered by `sender == task.agent`, dedup by finding id. (The accessor already exists — `bus.py:487`.)
  **VERIFY:** unit test — a specialist that only does `bus.publish(Channel.FINDINGS)` lands in `_all_findings`; the `8 (7)` / `1 (0)` mismatch disappears.
- **S4b (D‑K)** — `synthesis_lead.py`: assign `FinalReport.risk_analysis` from the RISK aggregate payload.
  **VERIFY:** `python -m pytest tests/test_synthesis_body_survives.py -q` + new "RISK full model → report.risk_analysis is not None".
- **S4c (D‑L)** — `agents/support/quality_gate.py`: when `viz_output is None` because delivery has not run yet, score `visual_quality` as N/A (don't penalize a stage scheduled for later); keep the hard check on the re‑render path.
  **VERIFY:** unit test — pre‑delivery gate does not penalize visual_quality; re‑render path still hard‑checks.

### W2 — Contain reframe waste (Primitive P3)

- **S5 (D‑E)** — `_maybe_reframe_failed_tasks` / `_task_needs_reframe` (`orchestrator.py:1223`, health‑gate `:1298`): refuse to reframe when (a) the task is itself a `task_reframed_*` variant, (b) the target source class is dead (per‑class living check, not "any class alive"), or (c) the task's own dependency FAILED. Cap the variant **tree**, not just per‑task attempts.
  **VERIFY:** unit test — reframed variant with dead target class → no new variant; existing reframe tests still pass.

### W3 — Retrieval hygiene

- **S6 (D‑G)** — `tools/searxng.py` `_search_searxng_json`: condense reference‑profile (wikipedia) queries to ≤120 chars title‑shaped.
- **S7 (D‑H)** — scholar‑profile sanitation: ≤120 chars **and** strip `, ? .`, replacing the bare 200‑char clamp (`searxng.py:722‑723`). Web behavior unchanged.
- **S8 (D‑I)** — `searxng.py:869`: on a non‑JSON / parse‑error body, call `health.record_response(unresponsive_engines=endpoint.engines, responding_engines=[])` **before** the retry, so the engine enters cooldown instead of being hammered for 30 min.
  **VERIFY (each):** unit test asserting the condensed/sanitized query shape or the cooldown record; confirm zero wikipedia/openalex 400 and semantic‑scholar cooldown in a healthy‑run docker log.

### W4 — The self‑healing loop (D‑F) — the systemic deliverable

Implement **§5 of `overhaul3_audit.md` in full** (Recovery Supervisor). Summary:
- New `orchestrator._recover_from_blocked(dag, report, score)` invoked from the `terminal_state == BLOCKED` branch (`orchestrator.py:~2970`) **before** `return result`.
- State machine: **diagnose → plan (drop actions failing `can_make_progress`) → snapshot `best` → recover (re‑dispatch only the responsible agent, idempotent task ids) → re‑score via existing `_quality_iteration_loop` → decide**.
- Blocker→remediation routing table (§5.2): DATA VOID/OUT‑OF‑SCOPE → source specialist with "typed gap, never Unknown"; VERDICT CONTRADICTION → SYNTHESIS single‑verdict; risk_coverage/D‑K → re‑assign risk section (no re‑research); CORPUS FLOOR → `_handle_thin_evidence`.
- Fail‑safe invariants (§5.3): **bounded** (`quality_recovery_max_passes` default 1 + shared wall‑clock), **monotonic** (commit only if score strictly improves, else keep `best`), **idempotent**, **progress‑gated**, **honest** (manifest‑recorded), **non‑authoritative** (never override the gate), **degrades gracefully**.
- New config knobs (§5.5): `quality_recovery_max_passes`, `quality_recovery_min_score_gain`, `recovery_wall_clock_seconds`. New KPIs: `kpi_9_recovery_passes`, `kpi_9_recovered`.
  **VERIFY:** recovery‑loop canary — a report carrying `Unknown` in a numeric field triggers exactly one recovery pass, re‑scores, `kpi_9_recovery_passes == 1`; monotonicity test (a pass that lowers the score is discarded); honesty test (budget‑refused ⇒ `BUDGET_REFUSED`, never "ran on STRONG").

### W5 — Regression lock

- **S11** — extend the canary suite (`hyperion/eval/canaries.py`): `_log`‑arity canary, reference‑condensation, scholar‑sanitation, non‑JSON cooldown, `_all_findings`‑bus‑fed, recovery‑loop.
- **S12/S13** — contract test that reference queries are title‑shaped; update `ARCHITECTURE.md §14` with D‑A…D‑L and the recovery loop.

---

## 4. Per‑task prompt template (give the model one D‑item at a time)

```
ROLE: You are a senior Python engineer fixing HYPERION, a proprietary multi‑agent
consulting system. Work in thinking mode; use tools to read/search/patch/test.

CONTEXT TO LOAD FIRST (read, do not guess):
  - overhaul3_audit.md  → the section for <D‑ITEM> (mechanism + Fix)
  - the exact file(s) named in that section
  - the existing test that neighbours the change

TASK: Implement the fix for <D‑ITEM> ONLY. Smallest safe patch.
HARD CONSTRAINTS: obey overhaul3_audit.md §4 anti‑patterns (do NOT raise caps,
  relax the Quality Gate, add search engines, reframe dead classes, or let logs lie).
DELIVERABLE:
  1. A test that FAILS before your change and PASSES after (reproduce the real failure).
  2. The minimal code patch.
  3. Run the VERIFY command from the audit for <D‑ITEM>; paste output.
RETURN JSON: {"d_item","files_changed":[...],"test_added","verify_cmd","verify_pass":bool,"residual_risk"}
STOP after this one item. Do not start the next W‑step.
```

---

## 5. Context strategy for the 1M window

Load, in priority order, and no more than needed:
1. The **target D‑item section** of `overhaul3_audit.md` (not the whole file each time).
2. The **named file(s)** for that item (they fit: `orchestrator.py` ≈ 3.4k lines, `base.py` ≈ 1.6k, `quality_gate.py` ≈ 1.5k, `searxng.py`).
3. The **neighbouring test file** (`tests/test_*`), to match existing style.
4. For §5 only: also load `agents/bus.py` (find `get_retained_findings`), `agents/support/quality_gate.py` (blocker strings), `tools/engine_health.py` (`living_classes`), `config.py` (knob block ~line 798).

Do **not** dump the 425 KB docker log or the 713‑line TUI log into context — the audit already distilled every signal from them. Load a log only to confirm a specific line the audit cites.

---

## 6. Global Definition of Done (run after all W‑steps)

```
python -m pytest -q                     # full suite green (minus known env failures)
python -m hyperion.eval.canaries        # all canaries incl. the 6 new ones
```
Then a **live smoke run** on the original question ("should indian private sector invest more for home grown tech in space sector…") and confirm the audit's DoD gates:
1. Zero `_log() takes 2 positional arguments` lines (D‑A — all 4 sites).
2. No `MissingDependencyOutput` on a specialist whose dep failed (D‑B).
3. Zero `SUB‑AGENT total budget reached … proceeding without spawning` for an already‑counted question; self‑heal actually runs STRONG (D‑C).
4. Every `completed with N findings (total collected: M)` has M ≥ N (D‑D).
5. No reframed variant is itself reframed (D‑E).
6. A BLOCKED run triggers ≤1 recovery pass and either ships or terminates **with the recovery attempt recorded** (D‑F / §5).
7. Zero wikipedia `/page/summary` 400 and zero openalex 400 in docker (D‑G/H).
8. semantic scholar stops being queried after a non‑JSON response (D‑I).
9. Report has a populated risk section (D‑K); gate doesn't penalize pre‑delivery viz (D‑L).

**Definition of "fixed": all 9 gates hold on a live run — not "the unit suite is green."** That distinction is the whole reason overhaul 3 exists.

---

## 7. One‑paragraph brief for the model

> You are fixing HYPERION using `deepseek-v4-flash` in thinking mode with tools. The full diagnosis is in `overhaul3_audit.md` (defects D‑A…D‑L + the §5 fail‑safe self‑healing design). Implement the fixes **in the W0→W5 order**, **one defect at a time**, each with a test that reproduces the real failure and its VERIFY command green before moving on. Obey the §4 anti‑patterns as hard constraints — never raise caps, relax the Quality Gate, add search engines, reframe dead classes, or let a log claim an action that did not happen. The system is proprietary and self‑healing by design: treat a BLOCKED verdict as an input to repair, never as an exit. "Done" means the 9 live‑run gates in §6 hold, not merely that pytest is green.
