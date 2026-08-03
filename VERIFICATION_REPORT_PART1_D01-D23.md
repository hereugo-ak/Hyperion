# INDEPENDENT VERIFICATION — HYPERION_DEEP_AUDIT_2026-07-30.md (Part 1, D-01…D-23)

**Branch verified:** `fix0.1` @ `995cdf5` (`fix(quality): repair production Ruff defects`)
**Confirmed identical to** `origin/fix0.1`; **33 commits ahead of** `main`.
**Method:** code reading only. Commit messages and claims were deliberately ignored.
Every verdict below is backed by a file:line citation, a grep with an empty/non-empty
result, a runtime probe, or a test execution — never by a commit heading.

**Empirical baseline:** `2887 passed · 4 failed · 14 skipped` (2905 collected), after
installing the render/chart deps the audit itself flags as missing (`kaleido`,
`weasyprint`). Before installing them, 40 tests failed; **36 of those 40 were purely
environmental**, and 4 are real.

---

## 0. Headline

Of the 23 Part 1 defects, **9 are genuinely and verifiably fixed, 2 are partially fixed,
and 12 are not fixed at all.** The S1 (deliverable-destroying) tier is where the work
actually landed; the depth cluster (D-13, D-17, D-18, D-19, D-20) — which the audit
itself called *"the second independent cause of no deep content"* — is **untouched in
its entirety.** Every single grep the audit used to prove those five defects still
returns exactly the same result today.

Two further problems that the audit could not have known about, because they were
introduced *by this branch*:

1. **`ec85826` (W-18) silently broke the D-07-adjacent router regression tests** — the
   tests written to guard P2-29/P2-30 no longer run against the router at all.
2. **`03b2c44` (W-14) silently broke the W-07 gap-closure round budget test** written
   three commits earlier by `19d77e3`.

Both are live failures in the suite on this branch.

---

## 1. Verdict table

Legend: **FIXED** = mechanism verified present and effective · **PARTIAL** = comment or
detection fixed, behaviour not · **NOT FIXED** = audit's own reproduction still reproduces

| ID | Sev | One-line subject | Verdict | Hard evidence |
|---|---|---|---|---|
| D-01 | S1 | `VaultSearchResult` returned where `-> str` declared | **FIXED** | `synthesis_lead.py:1659-1668` renders `notes` to a real string; `:1714` coerces at the call site too. 22/22 pass in `test_synthesis_type_contract.py` + `test_synthesis_body_survives.py` |
| D-02 | S1 | Quality loop launders a crash into a confident report | **FIXED** | `synthesis_lead.py:1564-1575` restores conclusion fields verbatim when `report.is_degraded`; `:1581-1586` records the 0-section case. Few-shot numbers deleted (`grep -c "2B TAM"` → **0**) and class-guarded by `test_fewshot_leakage_tripwire.py` |
| D-03 | S1 | No PDF engine on Windows; `playwright` undeclared | **NOT FIXED** | `grep playwright pyproject.toml` → **one hit, line 222, the mypy ignore list only.** Absent from `[project.dependencies]` and from both extras. Secondary defect also open: the false comment was corrected (`presentation_designer.py:227-238`) but `header_template`/`footer_template` appear **nowhere in `hyperion/`** — `render.py:446` still calls bare `page.pdf()` |
| D-04 | S1 | Search layer has two engines, both dead | **FIXED** | `searxng_settings.yml` now declares **16 engines, 16 enabled**, zero CAPTCHA-tier; `bing`/`duckduckgo` fully removed. New `tools/engine_health.py` (288 lines) consumes `unresponsive_engines` with exponential cooldown + disk persistence. 28/28 pass |
| D-05 | S2 | `self._logger` AttributeError inside `except` | **FIXED** | Both sites now use the module logger: `data_visualizer.py:851`, `:1049`. `grep "self\._logger"` → **0 hits**. AST guard `test_no_phantom_self_attrs.py` passes |
| D-06 | S2 | Health check is a port probe, so the dashboard lies | **FIXED** | `health.py:152-201` `_check_searxng` issues a real smoke query, requires `MIN_SMOKE_RESULTS=3`, reads `unresponsive_engines`, and returns `OFFLINE` on port-open-but-engines-dead — the exact 07-30 state. `credential_preflight` (`:77`) adds a real completion per provider |
| D-07 | S2 | Escalation schema mismatch; Director discards every escalation | **NOT FIXED** | **Runtime-proven.** `reports/verify_d07_escalation_probe.py`: **5/5 Shape-B payloads still lack the `issue` key**; 3 are discarded as duplicates, the 2 "evaluated" carry no content. Director still reads `payload.get("issue")` / `.get("agent")` at `engagement_director.py:523-524` and fingerprints on them at `:539`. Publishers unchanged: `quality_gate.py:1545,1573`, `fact_checker.py:1187,1592`, `render_engine.py:1285` |
| D-08 | S2 | Quality Gate cannot fail closed | **FIXED** | Real three-state terminal enum `QualityTerminalState` (`models.py:2386`); `orchestrator.py:1756` derives it and **deliberately does not read `max_iterations_reached`**; `:2397-2438` returns before Stage 5 with `failure_reason="quality_gate"` and writes an operator diagnostic. `SHIP_WITH_CAVEAT` is opt-in and forces a limitations notice |
| D-09 | S3 | `_apply_adaptation` re-runs completed agents | **NOT FIXED** | `engagement_director.py:636-653` is byte-for-byte the audit's excerpt: no COMPLETED/RUNNING check, still `dependencies=[]`, still `status=PENDING`. The adjacent self-dependency bug is also open — `grep "reroute_from != reroute_to"` → **0 hits**; `:660-668` can still make a task depend on itself |
| D-10 | S3 | Unsigned `obscura.exe` blocked by Defender | **FIXED** | `health.py:239-270` delegates to `client._binary_available()` and adds a distinct **`BLOCKED`** status for "file exists and is executable but the OS refuses to load it", which is precisely what health could not previously see. `test_obscura_load_probe.py` passes |
| D-11 | S3 | FRED is US-only and is the macro path for every geography | **NOT FIXED** | Mismatch is still only *detected and reported*, never routed around: `financial_analyst.py:425-437`, `market_analyst.py:460`. `WORLD_BANK` is declared in three tool lists and instantiable (`base.py:881`) but `grep "get_tool(ToolName.WORLD_BANK)"` → **0 hits**, and `grep "ToolName.SDMX"` in `hyperion/agents/` → **0 hits**. Zero call sites; the fallback does not exist |
| D-12 | S3 | Yield instrumentation reports on a dead code path | **FIXED** | `deep_search.py` now records on **all three** exits — zero-discovery `:668`, exception `:711`, success `:812`. `orchestrator.py:2859` adds a `zero_evidence_failure` gate so `0 calls / 0 chars` becomes an engagement failure, not a tidy `0%`. 22/22 pass |
| D-13 | S3 | Sub-agent research runs on 16K-context MICRO models | **NOT FIXED** | `config.py:119-138`: `gemma-4-31b` and `gemma-4-26b` are still `context_window=16_000, tpm=16_000, tier=MICRO`, and `gemma-4-31b` still carries the literal role **`"sub-agent quick tasks"`** the audit quotes and demands be removed |
| D-14 | S4 | `mypy --strict` does not cover the modules that broke | **NOT FIXED** | The quarantine at `pyproject.toml:225+` still contains, verbatim, `hyperion.agents.synthesis_lead` (D-01) and `hyperion.agents.support.data_visualizer` (D-05) — the two files that destroyed the deliverable — plus `agents.base`, `agents.bus`, `engagement_director`, `orchestrator`, `obs.health`, `render_engine`, `presentation_designer` |
| D-15 | S4 | Self-contained embedding pathologically inefficient | **NOT FIXED** | No font subsetting and no image byte budget anywhere: `grep -E "subset\|MAX_COVER_BYTES\|cover_budget"` across `render.py`/`images.py` → **0 hits**. `render.py:531` still base64-encodes whatever it is handed |
| D-16 | S4 | `dpi: 300` is not a CSS property | **NOT FIXED** | `presentation_designer.py:241` still emits `dpi: 300;` inside `@page` |
| D-17 | S1 | No LLM call ever specifies an output length | **NOT FIXED** | The audit's own reproduction, re-run verbatim: `grep -rn "max_tokens=" hyperion/agents/ \| grep -v "max_tokens=max_tokens"` → **no output**. `base.py:588` still defaults `max_tokens: int \| None = None` and passes it straight through at `:626` |
| D-18 | S1 | Truncated output structurally undetectable | **NOT FIXED** | `grep -rn "finish_reason" hyperion/` → **no output, entire tree.** `RouterResponse` still has no completeness field; nothing owns the question "was this response complete?" |
| D-19 | S2 | Sub-agents architecturally forbidden a large context | **NOT FIXED** | Both hard assertions survive unchanged: `sub_agent.py:105-109` and `base.py:945-948`. All 11 specialists still spawn `ModelTier.MICRO` (e.g. `market_analyst.py:1108,1118`). `engagement_director.py:1620-1626` still budgets `ModelTier.MICRO: 500` output tokens against `MIN_SECTION_WORDS = 450` |
| D-20 | S2 | The only word-budget instruction lives on the dead path | **NOT FIXED** | `grep -rn "prompt_clause" hyperion --include=*.py` → still **exactly one consumer**, `synthesis_lead.py:1160`. And `grep -rn "words\b" hyperion/agents/specialists/*.py` → **no output**: specialists still receive no length instruction whatsoever |
| D-21 | S3 | Declared return type is a lie, masked by `len()` sniffing | **NOT FIXED** | `market_analyst.py:738` still annotates `-> tuple[FinancialMetric, list[KeyFinding]]` while `:1382-1391` still runtime-sniffs `if len(triangulated_result) == 3:` under the same `# Handle both 2-tuple ... and 3-tuple` comment |
| D-22 | S3 | Escalation handler dereferences a nullable DAG | **PARTIAL** | `_handle_escalation` early-returns on `self._current_dag is None` (`:518`), so `_evaluate_escalation`'s deref at `:592-593` is unreachable in practice. But the guard **pre-existed on `main`** (verified: `git show main:...` has it at `:374/:488/:548`) — nothing on `fix0.1` fixed this, and `_evaluate_escalation` itself is still unguarded if ever called from elsewhere |
| D-23 | S4 | mypy quarantine hides 120 errors from one entry point | **NOT FIXED** | Same quarantine list as D-14. The two `data_visualizer._logger` errors the audit quotes are no longer *present* (D-05 was fixed by hand), but the mechanism that hid them is fully intact |

**Tally: 9 FIXED · 2 PARTIAL · 12 NOT FIXED.**

---

## 2. What was genuinely, well fixed

I want to be specific about this, because the quality of the work that *was* done is high
and it is not evenly distributed.

**The S1 report-killer chain is properly closed.** D-01 was not patched at the call site
and left there — the method now actually renders `VaultSearchResult.notes` into the string
its annotation always promised (`:1659-1668`), *and* the call site coerces defensively
(`:1714`), *and* there is a type-contract test. That is fixing the class, not the instance.

**D-02 is the best fix on the branch.** The obvious patch would have been "sanitize the
iteration output". Instead there are three independent layers: the `is_degraded` invariant
that restores conclusion fields verbatim (`:1564-1575`), the meta-text blocklist with an
"an iteration may never reduce information" rule, and — most importantly — the few-shot
example's fabricated numbers were **deleted from the source entirely** and a test now
asserts they cannot appear anywhere in the `hyperion` package. A token that cannot be
transcribed cannot leak.

**D-04 and D-06 were fixed together, which is the correct instinct.** Widening the engine
pool without fixing the lying health check would have left the operator blind to the next
collapse. `_check_searxng` now returns `OFFLINE` for the exact port-open-engines-dead
state that produced three green checkmarks over a dead research stack.

**D-08 is a real policy change, not a threshold tweak.** The docstring at
`orchestrator.py:1775-1776` explicitly states that `max_iterations_reached` is *not* read
when deriving the terminal state, and the code honours it. That was the whole defect.

**D-12** is a small fix done exactly right: the counter now fires on all three exits, and
a zero-evidence report is now an engagement failure rather than a tidy `0%`.

---

## 3. The three findings that matter most

### 3.1 The entire depth cluster is untouched — and it is the audit's own headline

The audit's §0.1 says one finding *"outranks all eight"*, and D-19 is described as *"the
most uncomfortable finding in this document"*: **HYPERION is not failing to work as
designed; it is working exactly as designed, and the design caps depth.**

Five defects make up that cluster. All five still reproduce, using the audit's own commands:

| Audit's reproduction command | Expected if fixed | Actual on `fix0.1` |
|---|---|---|
| `grep -rn "max_tokens=" hyperion/agents/ \| grep -v "max_tokens=max_tokens"` | call sites | **no output** (D-17) |
| `grep -rn "finish_reason" hyperion/` | a capture site | **no output** (D-18) |
| `grep -rn "prompt_clause" hyperion --include=*.py` | multiple consumers | **one** (D-20) |
| `grep -rn "words\b" hyperion/agents/specialists/*.py` | length clauses | **no output** (D-20) |
| `sub_agent.py:105` tier assertion | removed/relaxed | **present, unchanged** (D-19) |

D-13's `"sub-agent quick tasks"` role string is still sitting at `config.py:127-128` on a
16K/16K-TPM model. The audit asked for that exact string to be removed.

The consequence is unchanged and arithmetic: a sub-agent is still budgeted **500 output
tokens** (`engagement_director.py:1621`) against a **`MIN_SECTION_WORDS` of 450**. One
sub-agent still cannot produce a single section's worth of prose. No amount of the
excellent D-01/D-02 work changes that, because those fixes restore the *container* for
depth, not the depth.

There is a real risk of a false sense of completion here: the report will now have
chapters, and those chapters will be thin for reasons nothing on this branch addressed.

### 3.2 D-07 is not fixed, and D-08 accidentally proves the team knows the right shape

This one deserves emphasis because it is quietly load-bearing for two other defects.

Adaptive replanning is still 100% inoperative for all five support-agent escalations. I
proved this at runtime rather than by reading — see `reports/verify_d07_escalation_probe.py`:

```
source                       agent read       issue read       verdict
quality_gate:1545            unknown          Unknown issue    evaluated (with EMPTY content)
quality_gate:1573            unknown          Unknown issue    DISCARDED as duplicate
fact_checker:1187            unknown          Unknown issue    DISCARDED as duplicate
fact_checker:1592            fact_checker     Unknown issue    evaluated (with EMPTY content)
render_engine:1285           unknown          Unknown issue    DISCARDED as duplicate
payloads lacking 'issue' key: 5/5
```

The Director is still thrown the Quality Gate's failure report, the Fact Checker's
hallucinated citations, its contradiction set, and the Render Engine's verification
failure — and it still reads `"Unknown issue"` from all five.

The irony: the D-08 fix publishes its *own* escalation at `orchestrator.py:2407-2419`
using the **correct Shape A** (`"agent"` + `"issue"` + `"suggested_action"`). So the right
payload shape was written on this very branch, three files away from five call sites that
still use the wrong one. This is the "patch the instance, not the class" habit that the
audit calls out by name in D-21.

Also note this makes **D-22 permanently untestable rather than fixed** — the audit's own
words: *"Fix D-07 alone and this becomes reachable."*

### 3.3 Two commits on this branch broke earlier commits' regression tests

These are not pre-existing defects. They were introduced by `fix0.1` and are failing now.

**(a) `ec85826` (W-18) disabled the P2-29/P2-30 router guards.**
It added a third parameter, `estimated_tokens`, to `get_available_providers`
(`router/router.py:310-315`) and updated the one production call site (`:617`). It did not
update the test doubles. Result:

```
TypeError: dead_router.<locals>.<lambda>() takes from 1 to 2 positional arguments but 3 were given
```

Three tests now fail: `test_router_failure_names_no_provider`,
`test_router_failure_no_model_for_tier`, `test_standard_tier_attempted_once`. Their module
docstrings identify them as **T-09/T-10 — the guards for P2-29 (a total routing failure
must never be blamed on an innocent provider) and P2-30/P2-31.** So Part 2's router
correctness work is currently unguarded, and the failure mode is a signature change that a
`--strict` type checker covering `hyperion.router.router` would have caught — except that
module is in the D-14 quarantine.

**(b) `03b2c44` (W-14) broke `19d77e3`'s (W-07) round budget.**
W-07 established a 3-strategy + 2-scope closure ladder and a test asserting
`gap.attempts == 5`. W-14 then inserted a Phase 3 grounded-search attempt that does
`gap.attempts += 1` at `orchestrator.py:1251`, inside the same loop. The declared budget is
now 6:

```
assert gap.attempts == 5, "3 strategy + 2 scope rounds maximum"
AssertionError: assert 6 == 5
```

Either the ladder's contract is now 6 rounds and the test is stale, or the grounded attempt
should not consume a ladder round. Right now the code and its own specification disagree,
which is exactly the condition D-21 is about.

---

## 4. Cheap wins still outstanding

Two of these are one-line changes and both are quoted verbatim in the audit:

- **D-16** — delete `dpi: 300;` at `presentation_designer.py:241`. Invalid CSS, silently
  discarded, and load-bearing in the code comments' false "300 DPI output" claim.
- **D-03 (declaration half)** — add `playwright` to `[project.dependencies]` or a
  `pdf` extra. The three-stage render ladder is correct and stage 2 is unreachable purely
  because the package is undeclared. Note the golden-PDF test passes here only because
  **I installed `weasyprint` manually** — stage 1 works on Linux, which is why the suite
  is green about a defect that is Windows-specific.
- **D-09** — a single `any(t.agent == agent_name and t.status in (COMPLETED, RUNNING) ...)`
  check before `add_task`, plus a `reroute_from != reroute_to` guard.

---

## 5. Confidence and caveats

- Verdicts rest on code, not commits. Where a fix could be misread as present I ran it:
  D-07 via a runtime probe, D-01/D-02/D-04/D-05/D-06/D-10/D-12 via their own test modules,
  D-03 via the golden-PDF render.
- **`D-03`, `D-10`, and `D-15` cannot be fully closed from this Linux sandbox.** D-03's
  primary symptom and D-10 are Windows/Defender-specific; D-15 needs a real rendered
  artifact to measure byte budgets. For those I verified the *mechanism* (dependency
  declaration, `BLOCKED` status path, absence of any subsetting code) rather than the
  runtime outcome.
- The 4 real test failures are reproducible: `pytest tests/ -q` after
  `pip install kaleido==0.2.1 weasyprint`.
- I did not assess Part 2 (P2-01…P2-34) or the 07-31 audit here; those are the next phase.
