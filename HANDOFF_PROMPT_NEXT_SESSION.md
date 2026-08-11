# NEXT-SESSION HANDOFF PROMPT — HYPERION OVERHAUL 3 (D-A..D-L)

You are the coding agent for the next HYPERION remediation session. Read these two files FIRST, in order — they are the complete contract for this session. Do not improvise beyond them.

1. `overhaul3_audit.md` — the full audit of the 2026-08-11 run: defects D-A..D-L, each with code site + log evidence + fix + VERIFY, plus §5 (the fail-safe self-healing system).
2. `DEEPSEEK_V4_FLASH_FIX_GUIDE.md` — the operating manual for YOU: model config, guardrails, work order W0→W5, per-task prompt template, context strategy, and the global Definition of Done.

## Your mission

Implement every defect in `overhaul3_audit.md` — D-A..D-L — plus the §5 fail-safe self-healing system, in the exact W0→W5 order the guide specifies. **One defect at a time.** Do not batch W-steps.

## Non-negotiable rules (from guide §2 / audit §4)

- Do NOT raise retries / timeouts / iteration caps. These are logic bugs, not time budgets.
- Do NOT relax the Quality Gate or delete integrity blockers — fix the input, never the judge.
- Do NOT add keyed search APIs or new scraper engines (product decision, overhaul.md P1.4).
- Do NOT reframe a dead class or an already-reframed variant (D-E).
- Logs must NEVER lie: a budget-refused self-heal must never log "still failed on STRONG tier" (D-C).
- Every fix ships with a test that FAILS before the change and PASSES after — this is the rule overhaul 1 & 2 violated, and it is the whole reason this session exists.
- Smallest safe patch; preserve existing `# W-xx / OVERHAUL2 Sx / F-x` provenance comments.

## Execution loop per defect

1. Load the D-item's section from `overhaul3_audit.md` + the exact named file(s) + the neighbouring test.
2. Write the failing test (reproduce the REAL failure, not a happy-path mock).
3. Apply the minimal patch.
4. Run the audit's VERIFY command for that D-item; paste output.
5. Return JSON: `{"d_item","files_changed":[...],"test_added","verify_cmd","verify_pass":bool,"residual_risk"}`.
6. Stop. Do not start the next W-step.

## Context discipline

Do NOT dump the 425 KB docker log or the 713-line TUI log into context — the audit already distilled every signal from them. Load a log only to confirm a specific line the audit cites.

## Definition of done

- `python -m pytest -q` green (minus known environment failures: matplotlib/kaleido/render/yfinance/docker-dependent tests).
- `python -m hyperion.eval.canaries` green incl. the new canaries.
- All 9 live-run gates in guide §6 hold on the original India-space question.

Begin with **W0 / S1 (D-A)**: f-string ALL FOUR `_log()` 2-arg sites — `competitive_intel.py:529,568` and `orchestrator.py:2015,3341` — plus `tests/test_log_arity.py` that AST-walks the whole package and asserts 0 such sites. Run the audit's VERIFY command and stop.
