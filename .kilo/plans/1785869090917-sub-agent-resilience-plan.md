# Sub-Agent Resilience: Fix Timeouts, Zero Findings, and Dead Retry Path

## Context (root-cause audit)

A sub-agent's entire research lifecycle runs inside one `asyncio.wait_for(timeout=spec.timeout_seconds)` in `hyperion/agents/base.py:1016`. Inside that window `SubAgentRunner.run()` (`hyperion/agents/sub_agent.py:1280`) does: 2 parallel searches → extract up to 10 URLs → a **sequential chain of ~13 data-source tools** (Wayback, AlphaVantage, FRED, SEC EDGAR, Semantic Scholar, OpenAlex, World Bank, Google Trends, HackerNews, Reddit, Second Brain), each 30–120s → a STANDARD-tier LLM call.

Three compounding defects (confirmed against the shipped code and the user's log):

1. **Timeout** — when SearxNG is slow/down, searches return 0 URLs but the runner still walks all 13 data-source tools, and a slow network or backed-up LLM blows the wall-clock budget. The last commit (`614d80e`) only raised the budget 300→600 but did **not** reduce the work, so it still times out. The log's *exactly* 300s proves the deployed artifact predates even that bump (`"Sub-agent timed out"` is emitted **only** by `base.py:1020-1029`, which reads `spec.timeout_seconds`).
2. **Zero findings masked as success** — when search returns nothing, `raw_data = "No raw data available from tools."`; the LLM emits 0 schema-valid findings; `run()` (sub_agent.py:1307) converts that into a single `research_gap` finding. The parent sees "1 finding" and proceeds — the failure is invisible.
3. **Zero retries** — the L3 reframer `_maybe_reframe_failed_tasks` (`hyperion/orchestrator.py:1155`) only inspects **specialist `TaskNode`s**. Sub-agent timeouts/zero-findings are swallowed inside `_spawn_sub_agent` into a local `gap_finding` and never surface as FAILED/zero-finding. The specialist completes with its own (gap-laden) findings, so the orchestrator sees `COMPLETED` + non-empty and skips reframing. The retry added by `614d80e` is **dead for exactly this failure mode** (log shows repeated "Requesting strong tier completion", no `REFRAMER:` line).

## Goal

Make sub-agent research (a) fast and bounded so it stops timing out, (b) self-retrying so empty results are re-attempted before being reported, and (c) visible to the orchestrator reframer when a whole specialist yields nothing. **No reduction in content depth** — depth lives in the LLM synthesis steps, which stay untouched.

## Decisions / Changes

### 1. Bound and parallelize sub-agent I/O (root cause of timeouts)
File: `hyperion/agents/sub_agent.py` (`_gather_raw_data`, `_search_searxng`, `_search_jina`, `_extract_urls`).

- Parallelize the ~13 data-source tool calls with `asyncio.gather` + a per-tool `asyncio.wait_for(cap≈30s)`. A hung tool can no longer consume the whole window.
- **Relevance-gate tools**: select the data-source subset by question type (e.g. skip `world_bank`/"gdp" for "developer reviews"; skip `alpha_vantage`/`fred` for pure sentiment questions). Keep the two search legs + extraction always-on.
- **Short-circuit**: once a conservative threshold of usable evidence blocks is gathered (default: ≥3 non-empty extracts OR any SEC/structured source), skip the remaining optional tools.
- Keep `_extract_urls` concurrency=4 and full `select_content` budget — caps must **not** truncate extraction.

### 2. Retry at the correct layer (kills "zero retrying")
File: `hyperion/agents/base.py` (`_spawn_sub_agent`).

- Wrap the `wait_for(runner.run())` in an internal retry loop (max `SUB_AGENT_MAX_RETRIES = 2`, new constant near `max_sub_agents`).
- On `TimeoutError` **or** a result that is only `research_gap` findings (count gaps vs real), re-run with an escalated strategy built from the existing reframer primitives:
  - broaden query / `drop_geography=True` (reuse `_condense_query_variants` + planner),
  - switch/narrow tool subset to the always-available extract tiers,
  - bump tier `STANDARD → STRONG` on the retry attempt only.
- Only after exhausting retries return the `gap_finding`. Track per-attempt outcome for observability/logging.

### 3. Close the layer gap so the orchestrator reframer can still catch empties
Files: `hyperion/agents/base.py` (expose sub-agent outcome) + `hyperion/orchestrator.py` (`_task_needs_reframe`).

- Have `_spawn_sub_agent` record a per-specialist summary of sub-agent outcomes (counts of ok / timed_out / zero_findings) on the agent state / task context.
- In `_task_needs_reframe` (`orchestrator.py:1113`), also treat a specialist `TaskNode` as eligible when **all** of its sub-agents produced only `research_gap` findings (or the specialist emitted 0 real findings). This makes `_maybe_reframe_failed_tasks` actually fire a reframed `TaskNode` for the real failure mode.

### 4. Verify the deployed artifact loads the new timeout
File: `hyperion/agents/base.py` (`_spawn_sub_agent`).

- Add a spawn-time `logger.info` of `spec.timeout_seconds` (expected 600) so the active value is observable in logs.
- Build/restart the running process. The log shows 300s → the live artifact predates `614d80e`; confirm the fix is actually loaded before claiming resolution. (Timeout default is already 600 in `hyperion/schemas/agents.py:225` and passed explicitly by all 12 specialists.)

## Depth guardrails (explicit, non-negotiable)
- Synthesis/analysis LLM calls (`_analyze_and_produce_findings`, specialist synthesis) are **not** modified.
- Per-tool caps bound *hung* fetches only; extraction still retrieves full content via `select_content`.
- Short-circuit threshold is conservative (≥3 usable blocks or any structured source); primary search + extract leg is never skipped.
- Tier bump STANDARD→STRONG happens **only** on retry/failure — strictly additive coverage.

## Affected boundaries
- `hyperion/agents/sub_agent.py` — I/O parallelism, relevance-gating, short-circuit.
- `hyperion/agents/base.py` — retry loop, outcome tracking, timeout logging.
- `hyperion/orchestrator.py` — `_task_needs_reframe` zero-finding detection for sub-agent-emptied specialists.
- `hyperion/schemas/agents.py` — (already 600) no change needed unless a higher research budget is desired.

## Failure modes / risks
- Retry loops add latency + LLM cost → mitigated by `SUB_AGENT_MAX_RETRIES=2` and tier escalation only on failure.
- Parallel data-source fanout may hit provider rate limits → keep concurrency modest and per-tool caps.
- Over-aggressive short-circuit could drop a relevant late source → conservative threshold + never skip search/extract.

## Validation
- Extend `tests/test_sub_agent_query.py`:
  - new `test_sub_agent_timeout_retries` (mock slow tool → assert retry + eventual findings/gap after cap),
  - new `test_sub_agent_zero_findings_reframes` (assert orchestrator marks specialist zero_findings when all sub-agents gap),
  - existing timeout/empty tests still pass.
- Run a real engagement (e.g. automotive/India) and confirm in logs: sub-agents return **real** findings, `REFRAMER:` lines appear on empties, and no `research_gap` is silently treated as content.
- Confirm spawn log prints `timeout_seconds=600`.

## Open questions
- Desired `SUB_AGENT_MAX_RETRIES` (proposed 2) and per-tool cap (proposed 30s) — tune after the first live run.
- Whether to also raise the research sub-agent budget above 600s (currently 600) given the parallelized I/O should make 600 ample.
