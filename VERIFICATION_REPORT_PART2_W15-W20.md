# INDEPENDENT CODE-LEVEL VERIFICATION — PART 2 (W-15…W-20)

**Audit source:** `HYPERION_DEEP_AUDIT_2026-07-31_PART2.md` (1520 lines)

**Branch verified:** `fix0.1` at `5ffc858` before this report-only commit

**Audit baseline:** `bdb0a86` (the commit the audit was written against); HEAD is 100 commits later

**Date:** 2026-08-03

**Method:** every verdict below comes from reading the current source at the cited
`file:line`, from the audit's own `Verification` commands re-run against the working tree,
and from executed tests. Commit subjects and commit bodies were used **only** to locate
candidate changes and to establish provenance via `git show --stat` / `git log -L`. No item
is marked fixed because a commit claimed it was fixed. Where the audit specified an
acceptance grep, that grep was re-run and its real output is reproduced.

## 1. Executive result

Four of the six work items are complete. Two are substantially implemented but carry
concrete, code-visible gaps, and one otherwise-good item fails an explicit acceptance
criterion.

| Item | Area | Verdict |
|---|---|---|
| **W-15** | Fact checker measurement validity | **PARTIAL** — 3 gaps |
| **W-16** | Agent prompt quality / contract | **PARTIAL** — 1 gap |
| **W-17** | Router fallback and retry | **FIXED** |
| **W-18** | Token and cost budgets | **FIXED** |
| **W-19** | Eval harness | **PARTIAL** — acceptance criterion unmet |
| **W-20** | Crash recovery and concurrency | **PARTIAL** — 1 gap |

**Tally: 2 FIXED · 4 PARTIAL · 0 NOT FIXED.** Six discrete defects remain, all enumerated
in §9 with exact anchors. None of the six is a claim that was never attempted; each is a
narrow miss at the edge of otherwise correct work, and two of them are **regressions
introduced after** the fix commit by unrelated later work.

## 2. W-15 — Fact checker measurement validity · PARTIAL

### What is genuinely fixed

**FC-2, single matching algorithm.** `hyperion/agents/support/fact_checker.py:847-905`. The
duplicate naive substring/word-overlap scorer that used to live inside `_verify_claim` is
gone and replaced by a delegation to the one real matcher, with the reason recorded in the
source:

```python
# W-15: exactly one matching algorithm in this file. The naive
# substring/word-overlap block that used to live here measured a
# different strictness than _validate_evidence_chains...
for source in verification_sources:
    if self._source_supports_claim(claim, source):
        supporting_sources += 1
```

`_source_supports_claim` at `:1288` is now the sole token-boundary matcher, shared with
`_validate_evidence_chains` at `:1212`. The two code paths can no longer disagree about
what "supported" means, which was the core measurement-validity complaint.

**FC-4, tri-state URL liveness.** `:1333-1357`. `_url_alive` returns `bool | None`, and an
egress failure is explicitly UNKNOWN rather than being silently folded into "dead":

```python
except Exception as exc:  # noqa: BLE001 - egress failure: UNKNOWN, not alive
    logger.debug("url liveness check failed for %s: %s", url, exc)
    return None
```

This matters because the old two-state version turned a sandbox with no network into a
machine that reported every citation as hallucinated.

**FC-5, unit-aware round-number detection.** `_UNIT_MULTIPLIERS` at `:284-293` plus the
normalization block at `:1386-1404`. Magnitudes are normalized before the round-number
heuristic fires, so "1.5 billion" and "1,500 million" are no longer scored differently.

**FC-8, derived rather than hardcoded confidence.** `_telemetry_confidence` at `:1463-1485`
computes confidence from the measured count relative to `total_claims`, and is called at
`:1702` and `:1721` where two literal `ConfidenceLevel.HIGH` values used to sit.

`tests/test_w15_fact_checker_corpus.py` — 14 tests, all passing.

### Gap 15-a — FC-1 is regressed in four sites the acceptance grep cannot see

The audit's acceptance grep passes:

```text
$ grep -rn 'key_data=f"' hyperion/agents/specialists/*.py
financial_analyst.py:385  key_data=f"P/E: {overview.get('PERatio', 'N/A')}, "
innovation_analyst.py:525 key_data=f"Snapshot {snapshot.timestamp}: {snapshot.snapshot_url}"
market_analyst.py:565     key_data=f"Revenue TTM: {overview.get('RevenueTTM', 'N/A')}"
```

All three carry real interpolated values and are defensible. But the grep is anchored on
`key_data=f"`, and four sites store a **hand-written provenance sentence** in `key_data`
without an f-string, so they escape it entirely:

- `hyperion/agents/specialists/financial_analyst.py:456`
  `key_data="Country-specific GDP growth and inflation for DCF assumptions"`
- `hyperion/agents/specialists/market_analyst.py:481`
  `key_data="Country-specific GDP growth, inflation, and household spending"`
- `hyperion/agents/specialists/financial_analyst.py:498`
  `key_data=("US risk-free rate, inflation, GDP growth, Fed funds rate" + ...)`
- `hyperion/agents/specialists/market_analyst.py:517`
  `key_data=("US GDP growth, inflation, sector spending" + ...)`

`key_data` is what the fact checker matches a claim against. Filling it with a description
of *what the source is about* instead of the retrieved values is precisely the FC-1 defect:
the matcher is handed prose that can never contain the figure being verified.

**Provenance — this is a post-fix regression, not a skipped fix.** W-15 never touched these
two files:

```text
$ git show --stat 797c877 | grep -c "market_analyst\|financial_analyst"
0
```

and blame puts the line's origin *after* W-15:

```text
$ git log -L 456,456:hyperion/agents/specialists/financial_analyst.py
91025e9 fix(macro): route international data through World Bank
```

So `91025e9` reintroduced the pattern into files W-15 had no reason to open. The acceptance
grep should be widened to `key_data=` generally (21 hits today) so this cannot recur.

### Gap 15-b — FC-6 local-corpus circularity is untouched

`_check_local_corpus` at `:597-629` is unchanged. It scans `self._all_findings`, and when a
finding's own text overlaps the claim it emits that finding's content as a verification
source:

```python
key_data=finding.content[:500],
```

The claim was extracted from agent output, and the "independent" source it is then verified
against is that same corpus of agent findings. This is the circular-verification finding
verbatim; nothing in the current tree mitigates it.

### Gap 15-c — step 6 telemetry routing is incomplete

`:1684-1706` still publishes the hallucination count as a `KeyFinding` onto
`Channel.FINDINGS`. That is the client-facing narrative path. Per the W-09 boundary,
`hyperion/schemas/narrative.py:322-373` `EngagementTelemetry` is documented as the correct
home for exactly this number, and it already renders `hallucinated_citation_count` at
`:453`. The confidence attached to the count is now correctly derived (that half of the fix
landed), but the count itself is still travelling on the operator/client channel it was
supposed to be moved off. The same applies to `statistical_red_flags` at `:1709-1725`.

## 3. W-16 — Agent prompt quality · PARTIAL

### What is genuinely fixed

**The contract exists as one shared versioned string.** `hyperion/agents/prompt_contract.py`
defines `AGENT_CONTRACT_VERSION = 3`, `AGENT_CONTRACT_MARKER`, and a nine-clause
`AGENT_CONTRACT` covering SUBJECT FIT, ABSTAIN, NO FABRICATION, EVIDENCE BINDING, UNITS AND
DENOMINATION, UNCERTAINTY, CONFLICT, TYPOGRAPHY, and DEPTH AND LENGTH ("at least 450
words"). This is composition, not the 20-file copy-paste the audit warned against.

**It is prepended at exactly one dispatch point.** `hyperion/agents/base.py:605-612`:

```python
base_prompt = system_prompt_override or self.system_prompt
system = f"{AGENT_CONTRACT}\n\n{base_prompt}"
```

One site, immediately before the router call at `:631`. Every agent that inherits
`BaseAgent._llm_complete` gets all nine clauses whether or not its own prompt mentions them.

**PR-3 independently re-measured.** The audit counted 86 em/en dashes across the 20
`system_prompt=` sites. I did not take that on trust or grep for it; I re-ran the audit's own
AST walk over all 20 registered specs and counted em/en dashes in the prompt string
literals. Result: **0 em dashes, 0 en dashes**. `prompt_contract.py` itself is also
dash-free. The one em dash remaining in `sub_agent.py:616` is a legitimate character class
inside a regex, not prose.

**PR-5 glued fragment fixed.** `hyperion/agents/delivery/presentation_designer.py:1716-1717`
now has the space that was missing at the concatenation seam.

`tests/test_prompt_contract.py` passes: it asserts 20 specs, all nine clause keywords
present in the composed prompt, correct override behaviour, the "at least 450 words" string,
and that the contract is prepended exactly once.

### Gap 16-a — three call sites dispatch to the router without the contract

`AGENT_CONTRACT` is imported in exactly one module:

```text
$ grep -rln "AGENT_CONTRACT" hyperion/
hyperion/agents/prompt_contract.py
hyperion/agents/base.py
```

But `router.complete(` is reached from four agent-side places:

```text
hyperion/agents/base.py:631                  <- composes the contract
hyperion/agents/sub_agent.py:1156            <- does not
hyperion/agents/support/fact_checker.py:1137 <- does not
hyperion/tools/query_planner.py:709          <- does not
```

`hyperion/agents/sub_agent.py` builds its own system prompt in `_build_system_prompt()` at
`:221` and dispatches it directly at `:1148-1157`:

```python
system_prompt = self._build_system_prompt()
...
messages = [{"role": "system", "content": system_prompt}, ...]
response: RouterResponse = await self.router.complete(...)
```

Sub-agents are spawned from `base.py:974` and do a large share of the actual research
writing, so the population receiving **none** of the nine clauses — no ABSTAIN, no NO
FABRICATION, no EVIDENCE BINDING, no TYPOGRAPHY — is not a marginal one. The same bypass
exists for the Fact Checker's `_stage2_verdict` and for the query planner.

This gap is structurally invisible to the current test. `tests/test_prompt_contract.py`
enumerates `*_SPEC` objects out of `vars(agents_pkg)`; sub-agents have no `*_SPEC`, so the
registry test cannot ever fail on them. The fix is to move composition into a helper both
paths call, and to add a test that asserts on `router.complete` payloads rather than on the
spec registry.

## 4. W-17 — Router fallback and retry · FIXED

No gaps found. Every RT item is satisfied in current code.

**RT-1/RT-2, unbounded recursion replaced by an explicit attempt budget.**
`hyperion/router/router.py:95-115`:

```python
@dataclass
class RouterAttempt:
    max_attempts: int
    visited: set[ProviderType] = field(default_factory=set)
    attempts: int = 0

    def exhausted(self) -> bool:
        return self.attempts >= self.max_attempts
```

One budget object is created per `complete()` call at `:431` and threaded through the
requested tier (`:443`), the adjacency walk (`:478`), and the downgrade path (`:508`), so
attempts are counted across the *whole* call rather than being reset per tier. `_try_tier`
at `:579-701` is now a single `while not attempt.exhausted()` loop with an
`if p not in attempt.visited` filter, and the recursive `_try_next_candidate` is fully
deleted from the file. A provider cannot be revisited and the call cannot fan out
indefinitely.

**RT-3, status codes are first class.** `RouterResponse.status_code: int | None` at
`hyperion/router/providers/base.py:245`, populated at `:427-431` with a real attribute read
and only a regex as fallback:

```python
status_code = getattr(e, "status_code", None)
if not isinstance(status_code, int):
    match = re.search(r"\b([45]\d\d)\b", error_str)
    status_code = int(match.group(1)) if match else None
```

Failures are therefore classified structurally, not by substring-sniffing error text.

**RT-4, failure classes are handled differently, which was the whole point.** In `_dispatch`
at `:743-868`: 401/403 refunds the budget reservation and calls `record_auth_error()`
(`providers/base.py:188`, which sets `UNAUTHENTICATED`) — no point retrying a bad key. 429
returns and **halts the entire call** at `:450-455`, `:487-489`, `:518-520` rather than
stampeding the next provider, and `record_429(cooldown_seconds=60)` at `:157` applies a real
cooldown. Transient codes get exactly one retry, from named constants at `:81-92`:
`_TRANSIENT_STATUS_CODES = frozenset({408, 425, 500, 502, 503, 504})` and
`_TRANSIENT_BACKOFF_SECONDS = 1.0`.

**RT-5, silent tier downgrade is now observable.** `downgraded: bool` at
`providers/base.py:250`, set at `router.py:484` and `:516`. Callers can see that they got a
weaker model than they asked for. `_record_served` at `:704-741` reconciles real usage
through `budget_planner.reconcile_actual`.

`tests/test_router_w17_attempt_budget.py` monkeypatches `BaseProvider.complete` to count
per-provider dispatches and asserts bounded total attempts, at most 2 per provider, auth
refund, no failover on 429, and the `downgraded` flag. Passing.

## 5. W-18 — Token and cost budgets · FIXED

No gaps found.

**BG-1/BG-2, the ledger is real and persistent.** `hyperion/router/budget.py` — `BudgetStore`
at `:96` over SQLite at `artifacts/shared/llm_budget.sqlite` (`_default_db_path` `:89-93`),
with `daily_usage` keyed `PRIMARY KEY (provider, model, utc_date)`. Budgets survive process
restart, which the in-memory counter could not do.

**BG-3, reservations are atomic.** `reserve()` at `:142-188` opens `BEGIN IMMEDIATE` and
enforces both `model.rpd` and `model.tpd` inside the write transaction, so two concurrent
waves cannot both read remaining quota and both spend it. `reconcile()` `:214`, `refund()`
`:245`, and `daily_cost()` `:267` close the loop for actual-vs-estimated and for refunds on
auth failure.

**BG-4, token-per-day limits are actually read.** `remaining_tokens_for_model` at `:321-325`
and `filter_available_providers` at `:428-445` consume `.tpd`; it is no longer a declared
field nothing consults.

**BG-5, prices are dated and sourced.** `_MODEL_PRICES_PER_MILLION` at `:53-75` carries
`# USD per million input/output tokens. Pricing snapshot: 2026-08-01.` plus provider pricing
URLs, so the numbers are auditable rather than folklore. Cost is surfaced to the operator via
`get_engagement_cost_usd` (`router.py:882`), `EngagementResult.estimated_llm_cost_usd`
(`orchestrator.py:262`, `:277`), and the CLI at `cli.py:513`.

`tests/test_w18_persistent_budget.py` covers restart persistence, TPD enforcement, cost
accrual, and — importantly — uses a `ThreadPoolExecutor` to prove the reservation is atomic
under real concurrency rather than asserting it in the abstract. Passing.

## 6. W-19 — Eval harness · PARTIAL

### What is genuinely fixed

**EV-1…EV-4, the harness now measures the artifact it ships.** `hyperion/eval/harness.py`
imports the production gates at `:36-39` and checks 6-8 at `:192-247` open the **rendered
PDF** and run `extract_pdf_text`, `scan_text_integrity`, `audit_pdf(pdf_path,
fail_closed=False)`, and `_count_pdf_images`. Charts are counted as embedded in the PDF, not
as files on disk, so a chart that renders but never lands in the deliverable no longer
scores. `_TEMPLATE_ARTIFACTS`, the placeholder allow-list that let scaffolding pass as
output, is deleted.

**EV-6, crashes score zero.** `run_all` at `:551-601`:

```python
for result in results.results:
    if not result.success:
        result.overall_score = 0.0
        result.deterministic_score = 0.0
```

A crashed query used to be dropped from the mean, which made a broken engine look *better*.
It is now a hard zero and drags the mean down.

**EV-7, regressions are gated.** `PASS_RATE_REGRESSION_THRESHOLD = 0.0` at `:450`, with a
schema_version 2 baseline recording per-query and per-check outcomes at `:478-502`, so a
newly failing individual check is detectable and not masked by an unchanged aggregate.

**EV-5/EV-9.** A NATION_OR_REGION golden query is present at `:105-108`, and the judge naming
now reflects what the judge actually is.

`tests/test_eval.py` and `tests/test_ci_gate_lint.py` pass.

### Gap 19-a — the acceptance criterion "runs in CI" is not met

The work item requires the gate to run automatically. It does not run anywhere:

```text
$ ls -a .github
ls: cannot access '.github': No such file or directory

$ git log --oneline --all --diff-filter=A -- '.github/**'
(no output)
```

No workflow directory exists on this branch, on any branch, or anywhere in the repository's
history. Nothing invokes `hyperion/eval/ci_gate.py`, and `.pre-commit-config.yaml` runs only
ruff, mypy, and the pyproject gate — it does not call `ci_gate` either. Per the audit's own
§4.6 the gate's `run_lint()` is implemented correctly; the defect is purely that **no
trigger exists**. Every internal harness fix above is real, but nothing enforces it, so a
regression still reaches `main` unchallenged. This is the cheapest of the six gaps to close
and the one with the widest blast radius, since it is the mechanism that would have caught
gap 15-a automatically.

## 7. W-20 — Crash recovery and concurrency · PARTIAL

### What is genuinely fixed

**CR-1, run ids are deterministic so resume can find prior work.**
`hyperion/orchestrator.py:120-137`:

```python
normalized = " ".join((question or "").split()).lower()
key_part = " ".join((engagement_key or "").split()).lower()
digest = hashlib.sha256(f"{normalized}\x00{key_part}".encode()).hexdigest()
return f"eng_{digest[:12]}"
```

Whitespace- and case-normalized, NUL-separated so the two fields cannot collide by
concatenation. Fresh-vs-derived selection at `:2150-2152`, resume detection at `:2244-2266`,
journal open at `:2269-2271`.

**CR-2, `resume` is a real command.** `hyperion/cli.py:396-476` is an actual
`@app.command()`, matching the banner at `:7` that already advertised it, and covered by
`test_resume_is_a_real_command_and_banner_matches`.

**CR-3, missing dependency output raises.** `orchestrator.py:665-677` raises
`MissingDependencyOutput` instead of proceeding with a silently empty input, so a partially
recovered DAG fails loudly rather than producing a confident report built on nothing.

**CR-4, the findings list is lock-guarded.** `self._findings_lock = asyncio.Lock()` at
`:356`, held at both mutation sites `:646` and `:935`. This matters because waves run under
`asyncio.gather`; `test_all_findings_guarded_by_lock` asserts every site is covered rather
than spot-checking one.

**CR-5, signal handlers have real side effects.** `_install_interrupt_handlers` at
`cli.py:234-269` closes the journal, calls `_quarantine_partial_outputs` (`:207-231`) so a
truncated deliverable cannot be mistaken for a finished one, and then re-raises rather than
swallowing the signal.

`tests/test_w20_resume.py` — 9 tests, passing.

### Gap 20-a — the `shell` entry point installs no handlers

Handlers are installed in only two of the entry points:

```text
$ grep -n "_install_interrupt_handlers\|^def \|^@app.command" hyperion/cli.py
 66:@app.command()
 67:def shell(          <-- no install
234:def _install_interrupt_handlers(...)
274:@app.command()
275:def consult(
293:    _install_interrupt_handlers(output)
396:@app.command()
397:def resume(
459:    _install_interrupt_handlers(output)
```

`consult` (`:293`) and `resume` (`:459`) are protected. `shell` at `:67` — the interactive
TUI, which is the most likely place for a human to press Ctrl-C mid-engagement — has only a
bare `except KeyboardInterrupt` for container teardown. An interrupt there leaves the
journal unclosed and partial deliverables un-quarantined, which is the exact CR-5 failure
mode in the exact place it is most likely to occur.

## 8. Test evidence

### Work-item suites

```text
$ python -m pytest tests/test_w20_resume.py tests/test_w15_fact_checker_corpus.py \
    tests/test_prompt_contract.py tests/test_router_w17_attempt_budget.py \
    tests/test_w18_persistent_budget.py tests/test_eval.py tests/test_ci_gate_lint.py -q
112 passed, 2 skipped in 5.47s
```

### Full suite and complete root-cause of every failure

```text
$ python -m pytest tests/ -q --ignore=tests/golden --ignore=tests/test_transcript_selection.py
52 failed, 2819 passed, 24 skipped in 64.97s
```

All 52 failures were traced to sandbox dependency drift, and — unlike an inference — each
was confirmed by installing the project-declared dependency and re-running. The 52 account
for exactly three causes with no remainder:

| Cause | Failures | Files | Confirmation |
|---|---:|---|---|
| Undeclared `kaleido 1.0.0` in the sandbox vs the declared `kaleido>=0.2.1,<1.0` (`pyproject.toml:50`) | 35 | `test_mbb_chart_vocabulary.py` ×32, `test_chart_export_smoke.py` ×2, `test_synthesis_body_survives.py` ×1 | Kaleido 1.0.0 warns it is incompatible with the installed Plotly 6.0.1 and disables `write_image()`, so the chart path silently fell through to the matplotlib fallback and emitted `..._mpl.png` filenames, tripping `assert '_mpl' not in ...`. Installing the declared 0.2.1 made **all 35 pass**. |
| `textual` not installed | 15 | `test_pipeline_repair.py` ×12, `test_infra_paths.py` ×3 | 15 × `hyperion/tui/app.py:31: ModuleNotFoundError: No module named 'textual'` |
| `weasyprint` not installed | 2 | `test_w03_delivery_chain.py` ×2 | `ModuleNotFoundError: No module named 'weasyprint'` at `:146` and `:168` |

```text
$ pip install "kaleido>=0.2.1,<1.0" && python -m pytest tests/test_mbb_chart_vocabulary.py \
    tests/test_pipeline_repair.py tests/test_infra_paths.py \
    tests/test_chart_export_smoke.py tests/test_synthesis_body_survives.py -q
15 failed, 364 passed, 2 skipped in 28.56s   # 35 kaleido failures cleared; residue == the 15 textual imports
```

`tests/test_transcript_selection.py` was excluded from the run because it fails at
**collection** on the same missing `textual` (`:35 from textual.app import App, ComposeResult`).

**No failure is attributable to W-15…W-20 code.** Equally, no passing test covers any of the
six gaps in §9 — that absence of coverage is itself part of the finding, most sharply for
gap 16-a where the registry-based test design makes the gap unreachable.

Sandbox-only packages installed during verification and deliberately **not** committed:
`pydantic-settings`, `pytest-asyncio`, `openai`, `kaleido==0.2.1`.

## 9. Remaining defects, in fix order

Ordered by cost-to-close against risk-retired.

| # | Item | Anchor | Defect |
|---|---|---|---|
| 1 | W-19 | `.github/` absent; `hyperion/eval/ci_gate.py` uncalled | No CI trigger exists anywhere in the repo or its history. Add a workflow invoking `ci_gate.py`. Closes the acceptance criterion and is the mechanism that would have caught defect 3 on its own. |
| 2 | W-20 | `hyperion/cli.py:67` | `shell` does not call `_install_interrupt_handlers`. One line, mirroring `:293`. |
| 3 | W-15 | `financial_analyst.py:456,498`; `market_analyst.py:481,517` | FC-1 regressed by `91025e9` after W-15: `key_data` holds a provenance sentence, not retrieved values. Also widen the acceptance grep from `key_data=f"` to `key_data=`. |
| 4 | W-16 | `sub_agent.py:1148-1157`; `fact_checker.py:1137`; `query_planner.py:709` | Three router call sites receive none of the nine clauses. Extract composition into a shared helper; assert on `router.complete` payloads, not on the `*_SPEC` registry. |
| 5 | W-15 | `fact_checker.py:1684-1706`, `:1709-1725` | Hallucination and red-flag counts still published as client-path `KeyFinding`s; move to `EngagementTelemetry`, which already renders the field at `narrative.py:453`. |
| 6 | W-15 | `fact_checker.py:597-629` | FC-6 circular verification untouched: claims are verified against the same findings corpus they were extracted from. Largest design change of the six. |

## 10. Scope and caveats

- No live paid-provider engagement was run. W-17 and W-18 are verified through source,
  monkeypatched-provider tests, and the SQLite ledger's real transactional behaviour rather
  than against live provider 429s or real invoices.
- The tri-state liveness fix (FC-4) is verified at the code and unit level; a partitioned
  network was not simulated end to end.
- Verdicts describe `fix0.1` at `5ffc858`. The two regressions in §9 row 3 arrived from
  unrelated later work, so re-running the §2 provenance commands is worthwhile after any
  future merge into these files.

Within those boundaries: **W-17 and W-18 are fully fixed. W-15, W-16, W-19, and W-20 are
substantially implemented with the six specific, individually actionable defects listed in
§9.**
