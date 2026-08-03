# INDEPENDENT RE-VERIFICATION — PART 1 (D-01…D-23)

**Audit source:** `HYPERION_DEEP_AUDIT_2026-07-30.md`  
**Branch verified:** `fix0.1` at `a32eef5` before this report-only commit  
**Previous verification point:** `995cdf5` / report commit `6d19fcd`  
**Date:** 2026-08-03  
**Method:** current source inspection, defect-signature searches, focused regression tests,
repository-wide Ruff, strict mypy, and all pytest modules. Commit subjects were used only to
locate candidate changes; verdicts below are based on current code and executable evidence.

## 1. Executive result

The previous version of this report found **9 fixed, 2 partial, and 12 not fixed**. On the
current `fix0.1` head, **all 23 Part 1 findings are fixed**.

| Result | Previous report | Current re-verification |
|---|---:|---:|
| Fixed | 9 | **23** |
| Partial | 2 | **0** |
| Not fixed | 12 | **0** |

All 14 findings that were previously partial or not fixed have now been remediated. During
this re-verification, strict mypy also found one residual type/lifecycle mismatch in the
optional aiohttp CDP fallback. It was corrected in `519b159`, tested, committed, and pushed
before the final validation run.

## 2. Validation evidence

### Static gates

```text
$ ruff check hyperion tests tools
All checks passed!

$ mypy hyperion
Success: no issues found in 155 source files
```

The D-14/D-23 `ignore_errors` quarantine is absent from `pyproject.toml`. Strict checking is
therefore applied to every project module; only `ignore_missing_imports` remains for untyped
third-party packages.

### Pytest

All **114** `test*.py` modules were run in four isolated processes to prevent the
Kaleido/Chromium renderer from retaining enough memory to kill a single long pytest process.
No module was omitted.

```text
Batch 1:  258 passed, 2 skipped
Batch 2: 1062 passed, 5 skipped
Batch 3: 1052 passed, 4 skipped
Batch 4:  539 passed
Total:   2911 passed, 11 skipped, 0 failed
```

The declared `kaleido>=0.2.1,<1.0` constraint was used. The golden PDF regression passes
(`6 passed`) with that declared renderer version. A focused D-01…D-23 selection also passed
**399/400** initially; its only failure was the same golden test under the sandbox's globally
installed, undeclared Kaleido 1.0.0. Installing the project-declared 0.2.1 version made it
pass, and the complete batched run above stayed green.

### D-07 executable probe

`verification/verify_d07_escalation_probe.py` now reflects the current five support-agent
publishers and exits successfully:

```text
distinct fingerprints : 5
evaluated             : 5
discarded as duplicate: 0
payloads lacking required keys: 0/5
RESULT: D-07 FIXED; all publishers satisfy the Director contract.
```

## 3. D-01 through D-23 verdicts

| ID | Verdict | Current hard evidence |
|---|---|---|
| **D-01** | **FIXED** | `SynthesisLead` renders `VaultSearchResult.notes` to text and defensively coerces at the call boundary. `test_synthesis_type_contract.py` and `test_synthesis_body_survives.py` pass. |
| **D-02** | **FIXED** | Degraded reports preserve conclusion fields; zero-section output is recorded; fabricated few-shot figures remain absent. `test_fewshot_leakage_tripwire.py` and degraded-quality regressions pass. |
| **D-03** | **FIXED** | `playwright>=1.46.0` is a production dependency. The Chromium fallback supplies `display_header_footer`, explicit header/footer templates, margins, print background, A4 sizing, CSS page sizing, and guaranteed temporary-HTML cleanup. Golden PDF tests pass under declared dependencies. |
| **D-04** | **FIXED** | The expanded SearxNG engine pool and persistent engine-health/cooldown logic remain present. Engine-health regressions pass. |
| **D-05** | **FIXED** | No executable `self._logger` access remains in Data Visualizer; both handlers use the module logger. The AST phantom-attribute guard passes. |
| **D-06** | **FIXED** | SearxNG health performs a real smoke query and checks result yield/unresponsive engines rather than treating an open port as healthy. Search-health and credential-preflight tests pass. |
| **D-07** | **FIXED** | All five Quality Gate, Fact Checker, and Render Engine escalation publishers now provide the Director's canonical `agent`, `issue`, and `suggested_action` keys. The refreshed runtime probe reports 5 distinct populated escalations and zero collapsed duplicates. |
| **D-08** | **FIXED** | Quality Gate has explicit terminal states and blocks delivery on refusal; ship-with-caveat remains opt-in. `test_w08_quality_gate_refusal.py` passes. |
| **D-09** | **FIXED** | `_apply_adaptation` skips agents already RUNNING or COMPLETED, permits deliberate failed-task retry, rejects `reroute_from == reroute_to`, avoids duplicate dependency IDs, and prevents task self-dependency. |
| **D-10** | **FIXED** | Obscura health distinguishes an absent binary from a present binary the OS refuses to load and reports BLOCKED. `test_obscura_load_probe.py` passes. |
| **D-11** | **FIXED** | Market and Financial analysts route non-US macro requests through `ToolName.WORLD_BANK`; the World Bank client supports country resolution/indicator retrieval. World Bank regressions pass. |
| **D-12** | **FIXED** | Extraction yield is recorded on zero-discovery, exception, and success exits, and zero evidence fails the engagement. Yield regressions pass. |
| **D-13** | **FIXED** | The 16K MICRO models no longer advertise or receive sub-agent research work; the literal `sub-agent quick tasks` role is absent. Research-capable STANDARD/STRONG/DEEP models own this path. |
| **D-14** | **FIXED** | Strict mypy covers the entire `hyperion` package with no project-module `ignore_errors` quarantine. Live result: 155 source files, zero issues. |
| **D-15** | **FIXED** | Embedded images have per-cover, per-chart, per-section, and aggregate byte ceilings with recompression/omission; fonts are subset to used glyphs. Asset-budget and font-embedding regressions pass. |
| **D-16** | **FIXED** | Invalid `dpi: 300` CSS is absent from the tree. PDF quality is controlled by renderer/image mechanisms rather than a silently ignored CSS declaration. |
| **D-17** | **FIXED** | `BaseAgent._llm_complete` resolves a positive tier-specific output ceiling for every call and passes it to the router; direct sub-agent and Fact Checker calls also specify budgets. No provider-default output cap remains on the agent path. |
| **D-18** | **FIXED** | `RouterResponse` records `finish_reason`/completeness, provider parsing classifies completion, and length/max-token truncation is rejected as an incomplete response. Router truncation tests pass. |
| **D-19** | **FIXED** | Every specialist sub-agent spawn now requests STANDARD; `SubAgentRunner` accepts STANDARD/STRONG/DEEP and rejects MICRO/FAST research tiers. Specialist spawn searches show no MICRO sub-agent call sites. |
| **D-20** | **FIXED** | The shared, versioned live prompt contract is prepended in `BaseAgent._llm_complete` and requires at least 450 words for substantive analytical output while exempting compact routing/extraction work and forbidding padding. Prompt-contract dispatch tests pass. |
| **D-21** | **FIXED** | Market CAGR triangulation has one invariant three-value return contract. The runtime `len(triangulated_result)` sniff and “Handle both 2-tuple” branch are absent. Contract tests pass. |
| **D-22** | **FIXED** | `_evaluate_escalation` itself returns `None` when `_current_dag` is absent, independently of the caller guard. Nullable-DAG tests prove no LLM call/dereference occurs. |
| **D-23** | **FIXED** | The entry-point quarantine is gone together with the broader D-14 quarantine. Strict mypy is green across all 155 source files, including previously hidden modules. |

**Final tally: 23 FIXED · 0 PARTIAL · 0 NOT FIXED.**

## 4. Previously reported branch regressions

The previous report also identified two regressions introduced during remediation. Both are
closed:

1. Router test doubles now accept `estimated_tokens`; router failure-attribution and
   per-candidate regressions pass.
2. Grounded-search escalation no longer consumes an extra strategy/scope attempt; the
   five-round gap-closure contract passes in `test_w07_insufficiency_ladder.py`.

## 5. Scope and platform caveats

- Windows Defender/SmartScreen behavior cannot be executed in this Linux sandbox. D-10 is
  verified at the load-probe/status mechanism and regression-test level.
- A real Windows Chromium installation was not launched. D-03 is verified through the
  declared Playwright dependency, complete `page.pdf` options, cleanup path, and Linux PDF
  regressions.
- No live paid-provider engagement was run; all verification is deterministic source,
  static-gate, probe, and test evidence.

Within those explicit platform boundaries, there are **no remaining Part 1 D-01…D-23 items
marked partial or not fixed** on `fix0.1`.
