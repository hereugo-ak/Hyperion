# Hyperion Chief Audit: Fix0.3 Zero-Findings and No-Report Incident

**Audit date:** 2026-08-09  
**Scope:** repository snapshot, existing Fix0.3 runbooks/audits, supplied TUI log, supplied Docker log  
**Requested posture:** audit and remediation instructions only; no runtime code changes were made  
**Implementation owner:** a later remediation session  
**Branch target requested by operator:** `fix0.3`

## 1. Executive Verdict

The incident is a **retrieval-and-budget cascade**, not a single SearXNG outage and not an LLM-quality problem.

The report cannot be generated reliably because the pipeline accepts an unhealthy retrieval state as a normal research result. The sequence is:

```text
boot smoke traffic
  -> fragile web engines are rate-limited or suspended
  -> general queries remain concentrated on web:8890
  -> each sub-agent executes multiple query variants sequentially
  -> each SearXNG attempt can retry across endpoints and wait up to the request budget
  -> search/extraction consumes most of the sub-agent wall clock
  -> runner emits synthetic research_gap instead of a typed infrastructure failure
  -> parent logs "1 finding" although it is only a gap
  -> one broadened respawn repeats against the same unavailable dependency
  -> respawn returns another gap and is logged as failed
  -> specialists publish zero substantive findings
  -> synthesis writes from thin/parametric context
  -> fact-check has zero verified claims and many evidence-chain breaks
  -> quality gate blocks on corpus floor and consistency defects
  -> no report is rendered
```

### Chief conclusion

The current system has **retry loops without dependency-state recovery**. It retries work, but it does not first repair, bypass, or formally quarantine the failed retrieval dependency. Increasing model tier, timeout, or synthesis iterations will not solve this incident and can increase the 35-minute failure cost.

## 2. What Was Audited

- `hyperion/tools/searxng.py`
- `hyperion/agents/sub_agent.py`
- `hyperion/agents/base.py`
- `hyperion/orchestrator.py`
- `hyperion/agents/support/quality_gate.py`
- `hyperion/config.py` and `hyperion/schemas/agents.py`
- `hyperion/tui/boot.py`
- `hyperion/infra/services.py` and `hyperion/infra/searxng_profiles.py`
- `docker-compose.yml`
- `searxng_settings.yml`
- generated `searxng_settings.web.yml`, `reference.yml`, `scholar.yml`
- prior audit and runbook documents in `docs/` and the repository root
- supplied TUI and Docker logs

The directory supplied for this audit has no discoverable `.git` metadata. `git status`, `git branch`, and `git log` fail both in the project directory and its immediate parent. Therefore the requested historical `fix0.1`/`fix0.2` commit comparison and push cannot be truthfully completed from this workspace. The audit uses the checked-in audit/runbook artifacts as historical evidence and records this as an operational blocker below.

## 3. Evidence Ledger

| ID | Evidence | Meaning |
|---|---|---|
| E-01 | TUI: `Consumer insights complete: 0 personas ... confidence=low`; Tech: `0 vendors`; Regulatory: `0 regulations`; Market: `completed with 0 findings` | Empty domain models are being treated as completed specialist outputs. |
| E-02 | TUI: `Sub-agent respawn ...`; then `Sub-agent returned 1 findings`; then `Sub-agent respawn failed` | The “1 finding” is commonly the synthetic `research_gap`, not evidence. The retry is bounded but not state-aware. |
| E-03 | TUI: `concurrent budget reached (3/3)` while initial workers were still failing | Resource budget and yield budget are conflated in the deployed behavior, or the deployed checkout predates the current yield-aware code. |
| E-04 | Docker: Yep 403, Brave 429, Mojeek 403, MWMBl timeouts, Wikipedia/Crossref/OpenAlex rate limits | Upstream engines are unavailable/rate-limited. SearXNG containers themselves are mostly alive; upstream corpus is degraded. |
| E-05 | TUI: first engagement starts immediately after boot smoke; Docker shows web-engine bans during smoke | The readiness probe is generating real upstream traffic against the same public IP before research begins. |
| E-06 | Code: `_fan_out_search` loops with `await search_fn(...)` per query (`sub_agent.py:1049-1078`) | Search queries within a leg are serial, despite the surrounding architecture being described as parallel. |
| E-07 | Code: SearXNG `MAX_RETRIES=3`, request timeout 45 seconds, retry delay 2 seconds (`searxng.py:470-475`) | One query can consume a large fraction of a sub-agent search budget before extraction or LLM analysis. |
| E-08 | Code: `SubAgentRunner.run()` catches search timeout, then still runs analysis (`sub_agent.py:1332-1356`) | A dead retrieval phase is converted into an LLM call over an explicit “No raw data” string rather than an infrastructure recovery event. |
| E-09 | Code: empty/invalid LLM JSON becomes `[]`, then a `research_gap` (`sub_agent.py:1261-1289`, `1360-1367`) | Provider/schema failure is indistinguishable from “the world has no evidence.” |
| E-10 | Code: broad respawn is triggered only by timeout or exact content substring `no validated findings` (`base.py:1097-1125`) | Valid zero/empty failure forms can bypass retry; all matching failures retry without checking dependency health. |
| E-11 | Code: quality loop has `quality_source_floor=3` while hard corpus blocker requires 8 domains (`config.py:796`, `quality_gate.py:1203-1230`) | Two evidence floors exist and can produce inconsistent escalation/termination behavior. |
| E-12 | Code: thin-evidence escalation runs after quality scoring, but report state is not clearly rebuilt from newly recovered source material before the next score (`orchestrator.py:1968-2016`) | Retrieval escalation can be operationally successful yet fail to change the scored report. This matches “no score change; terminating early.” |

## 4. Severity-Ranked Findings

### F-01 P0: Retrieval failure is represented as empty research, not an infrastructure incident

**Evidence:** `SubAgentRunner._gather_raw_data()` returns the literal `No raw data available from tools.` when all sources fail (`sub_agent.py:598`). Search and tool exceptions are appended to a text blob, not returned as a typed failure. `_analyze_and_produce_findings()` can then return no findings, and `run()` fabricates a `research_gap`.

**Impact:** Every downstream layer believes it received a valid research outcome. The TUI says “1 finding” even when the only object is a low-confidence gap. The system loses the distinction between:

- no relevant evidence exists;
- search provider was rate-limited;
- extraction failed;
- LLM provider failed;
- JSON/schema validation failed;
- task timed out.

**Required remediation:** Introduce an explicit outcome state machine, at minimum `SUCCESS`, `NO_EVIDENCE`, `RETRIEVAL_DEGRADED`, `ANALYSIS_FAILED`, `TIMEOUT`, and `RETRY_EXHAUSTED`. A `research_gap` must not increment substantive finding counts. Every transition must carry dependency, attempt, elapsed time, and recovery eligibility.

**Exit gate:** A run with zero substantive findings shows `0 substantive / N gaps`, never `1 finding`, and the specialist cannot report “completed” without a success or an explicit blocked/degraded state.

### F-02 P0: Retry logic repeats the failed dependency instead of changing the recovery path

**Evidence:** `BaseAgent._spawn_sub_agent()` performs one broadened respawn (`base.py:1072-1095`). The broadened runner changes query breadth and extraction cap, but continues to use the same SearXNG/Jina path. The supplied logs show exactly this pattern: respawn, one finding, respawn failure.

**Impact:** Query broadening cannot recover a provider returning 403/429/timeout. It spends additional time proving the same outage. This is retry amplification, not self-healing.

**Required remediation:** Make retries dependency-aware:

1. Classify the failure before retrying.
2. If an endpoint/engine is unavailable, quarantine it and select a different source class.
3. If all local retrieval sources are degraded, fail fast into a deterministic fallback or a clearly blocked evidence state.
4. Only broaden the query after the dependency health gate is green.

**Exit gate:** A forced 403/429 scenario produces at most one local probe, then switches source class or ends in a visible degraded state. It never issues the same failed engine set again merely with different query text.

### F-03 P0: The sub-agent search leg is serial and has multiplicative retry latency

**Evidence:** `_fan_out_search()` iterates queries and awaits each search sequentially (`sub_agent.py:1065-1078`). Each SearXNG request can select an endpoint, wait for token-bucket delay, run up to three attempts, and sleep between attempts (`searxng.py:688-817`). SearXNG and Jina are parallel at the outer level, but each leg is serial internally.

**Impact:** Five or more query variants multiplied by endpoint retries and extraction can consume the 420-second search allocation before the 180-second analysis remainder. Across many specialists this explains the 35-minute engagement without a report.

**Required remediation:** Use a bounded per-sub-agent query scheduler with cancellation, a global deadline, per-source budgets, and a maximum number of useful attempts. Do not wait for every planned query after the minimum evidence contract is met. Do not retry a query when the failure is a provider-level circuit-open state.

**Exit gate:** A dead-pool probe terminates within the configured fail-fast budget; a healthy probe reaches the analysis phase with at least 60 seconds remaining. Telemetry reports time spent in planning, discovery, extraction, and analysis separately.

### F-04 P0: General research is still dependent on the four-engine web replica

**Evidence (verified against `search()`):** `SearxngPool.CATEGORY_PROFILE` routes `general/news` to `web` (`searxng.py:307-315`). The web profile contains only Mojeek, MWMBl, Brave, and Yep (`searxng_settings.web.yml:44-68`). The call order in `search()` is: fail-fast gate (`searxng.py:1296-1315`) → primary rotation on the web profile (`:1322-1331`) → full-pool fan-out (`:1347-1361`) → Jina (`:1365`) → grounding (`:1384`). Two compounding defects:

1. **Web-first latency.** General queries burn the full rotation budget on the dead web profile before fan-out, Jina, or grounding are reached. With 3 retry attempts × 45-second request timeout, one query variant can spend over two minutes proving the web pool is dead.
2. **Fail-fast gate uses the global engine set.** The fail-fast at `:1297` checks `health.healthy_count(referenced_engines()) < 2` — `referenced_engines()` spans ALL profiles (web + reference + scholar, ~15 engines deduped). So if the web profile is 100% dead but just 2 scholar API engines are healthy, the fail-fast does NOT trigger, and general queries still proceed to web-first rotation. The gate protects against a total fleet outage, not a per-profile outage.

**Impact:** Two replicas can be HTTP-healthy and still contribute nothing to normal business questions until the web profile has fully exhausted its retry budget. Scholar/reference engines are idle until late fallback, while web upstream failures consume the first budget window.

**Required remediation:** Treat the fleet as a retrieval pool from the first attempt, with policy-safe routing by source class. Preserve isolation by sending each replica only its own engines. General queries need a controlled mix of independent web, reference, public-statistical, and academic APIs; not all business evidence should be forced through crawler SERPs.

**Exit gate:** With web engines artificially suspended, a general smoke query obtains evidence from at least two other permitted source classes without waiting for all web retries.

### F-05 P0: SearXNG is operationally up but the upstream corpus is not healthy

**Evidence:** Docker shows 403/429/timeout events for web and scholar engines. `server.limiter: false` is present in all settings, and the limiter file is mounted but not enabled by the settings. Client headers can satisfy local bot-detection/trusted-proxy handling, but they do not change the public outbound IP used against upstream engines.

**Impact:** “SearXNG ready” is incorrectly interpreted as “search corpus ready.” `/config` health proves the process responds, not that the configured engines return results.

**Required remediation:** Separate process readiness from corpus readiness. Maintain per-engine health with cooldown and circuit states, expose a boot health table containing recent result counts and unresponsive engines, and suppress real upstream traffic during readiness unless explicitly requested. Decide and document whether inbound limiter protection is required; do not mount a limiter configuration that the service does not use.

**Exit gate:** Boot reports `process=ready` and `corpus=degraded/ready` independently. An engagement cannot start normal research when the minimum corpus contract is false without selecting a fallback mode.

### F-06 P1: The boot smoke probe burns live upstream capacity

**Evidence:** The supplied Docker log shows the web engines being suspended shortly after boot and before the engagement. The existing runbook identifies the smoke query and web profile as the likely traffic source.

**Impact:** The health check creates the incident it is intended to detect, especially because all three replicas share one public IP.

**Required remediation:** Make readiness probes local and cheap by default. Prefer `/config`, endpoint health, and a cached fixture; if a live smoke is mandatory, use one selected low-risk API engine, enforce a long cooldown, and never probe every fragile crawler at every boot.

**Exit gate:** Repeated service restart cycles do not increase upstream suspension counters or consume the same engagement search budget.

### F-07 P1: “Zero findings” is emitted for several unrelated failure classes

**Evidence:** Invalid JSON and invalid `KeyFinding` items are silently skipped (`sub_agent.py:1264-1289`). Specialist gather calls use `return_exceptions=True` in multiple modules. The orchestrator converts failures to `None` or generic task failure while specialist summaries still say “completed.”

**Impact:** Operators cannot know whether to fix retrieval, provider routing, schema contracts, prompt/output format, or a specialist implementation. The final report is allowed to synthesize around missing data.

**Required remediation:** Preserve per-attempt error envelopes and publish counters for raw results, extracted documents, valid findings, invalid findings, provider failures, and gaps. Invalid structured output must trigger a bounded format repair/retry or a typed analysis failure, not disappear.

**Exit gate:** Every zero-substantive-finding specialist log includes a machine-readable reason and counts. No exception disappears through `return_exceptions=True` without an attached task outcome.

### F-08 P1: Respawn budget and concurrency budget are not observably aligned

**Evidence:** Current source intends `max_sub_agents` to be concurrent and a total ceiling of six (`base.py:967-1009`), while the supplied deployment reports `concurrent budget reached (3/3)` and the project’s older schema comments still describe a three-agent total. This is strong evidence of deployment drift or mixed code versions.

**Impact:** Fixes present in the Windows snapshot may not exist in the WSL runtime that generated the log. A correct local code review cannot explain a different runtime behavior until provenance is proven.

**Required remediation:** Add a runtime build fingerprint containing Git commit, package path, source hash, timeout, retry policy, search budget, and generated profile hash. Print it at boot and attach it to every engagement artifact. Refuse strict mode when runtime source and expected checkout disagree.

**Exit gate:** The exact running process reports the same commit/source hash and timeout policy as the audited checkout. No incident analysis proceeds from screenshots alone.

### F-09 P1: Quality thresholds conflict and thin-evidence recovery can terminate without changing the report

**Evidence:** Configuration says `quality_source_floor=3`, while the quality gate hard-blocks below eight distinct domains. The quality loop can call retrieval escalation, then stop after no score change (`orchestrator.py:1954-1965`). The supplied log shows 5 domains, 27 evidence-chain breaks, 0% verification, score 2.7, and an early terminal block.

**Impact:** The system spends more LLM time polishing an evidence-deficient report, then refuses delivery. If retrieval results are not injected into a rebuilt report before scoring, escalation is cosmetic.

**Required remediation:** Define one evidence contract by report type. Make retrieval escalation a first-class phase that returns new source objects, rebuilds affected sections, recalculates coverage, and only then invokes quality scoring. If recovery fails, stop early with a diagnostic report, not a client report.

**Exit gate:** A corpus-floor failure always produces either a measurable increase in distinct domains/substantive claims or a terminal `INSUFFICIENT_EVIDENCE` state. It cannot produce an unchanged score while claiming recovery was attempted.

### F-10 P1: Quality gate correctly blocks, but upstream verdict/confidence defects remain

**Evidence:** The supplied run has `CONDITIONAL` recommendation conflicting with narrative `no-go`, and the gate identifies corpus, verdict, analytical-depth, risk-coverage, and data-accuracy failures. Existing gate blocker code is a useful tripwire, not a repair mechanism.

**Impact:** Raising the gate threshold or adding iterations would not create evidence. The system reaches synthesis with invalid inputs and asks QA to detect what earlier contracts should have prevented.

**Required remediation:** Compute verdict and confidence from one typed state derived from evidence coverage. Run consistency validation before presentation. Keep hard blockers at the render boundary as defense in depth.

**Exit gate:** A blocked evidence state has one consistent verdict across all fields, honest low confidence, and no “no-go”/“conditional” contradiction.

## 5. SearXNG Configuration Assessment

### What is working

- The image is pinned in `docker-compose.yml`.
- Three replicas have separate ports and generated profiles.
- The generated profiles match the declared replica engine sets.
- `/config` reconciliation is called with the expected engine set per replica in `tui/boot.py:273-285`.
- Client requests include `X-Forwarded-For` and `X-Real-IP`, which helps local trusted-proxy handling.
- Valkey persistence and per-engine cooldown machinery exist.

### What is not sufficient

- `server.limiter: false` means the mounted limiter file is not an active inbound limiter policy. This may be intentional, but it is currently ambiguous and must not be described as protection.
- The web profile is only four fragile upstream crawlers. The Docker evidence confirms all four can fail together.
- The same public IP is used by all replicas; replicas provide isolation and concurrency, not ban evasion.
- `request_timeout` and engine timeouts are large enough to make a dead upstream expensive when multiplied by serial query variants.
- A service health check can pass while every useful upstream engine is suspended.
- The supplied logs show upstream throttling in Brave, Yep, Mojeek, MWMBl, Wikipedia, Crossref, OpenAlex, and PubMed. This is broader than one bad YAML entry.
- There is no evidence in the supplied logs that the generated profiles fail to parse. The stronger conclusion is **upstream availability plus orchestration policy**, not “SearXNG YAML is definitely invalid.”

### Required SearXNG diagnostic matrix

Run in the remediation session, record results, and attach to the engagement:

| Probe | Must answer |
|---|---|
| `/config` per port | Process and enabled-engine readiness |
| one request per engine, rate-limited | Which upstreams return results, 403, 429, timeout, or parser error |
| no-engine general query | Whether the profile can return any corpus at all |
| explicit-engine request | Whether code engine names match generated profile names |
| three consecutive probes with 60-second spacing | Whether cooldown/circuit state is stable rather than a transient success |
| `vault/engine_health.json` before and after boot | Whether stale cooldown state is poisoning the engagement |
| runtime profile hash | Whether mounted YAML matches repository-generated YAML |

## 6. Why the Logs Say “1 Finding” When the Agent Has No Evidence

This is a semantic accounting defect.

`SubAgentRunner.run()` guarantees a non-empty list by inserting a `KeyFinding` with `finding_type="research_gap"` when the analysis returns no validated findings. The parent then logs `len(findings)`, so one gap is displayed as one finding. The respawn path checks for a non-gap finding and correctly rejects the gap, producing `Sub-agent respawn failed`.

Therefore both messages can be true:

- “Sub-agent returned 1 findings” means one object was returned;
- “Sub-agent respawn failed” means zero substantive findings survived the respawn.

The TUI must display separate counters:

```text
substantive findings=0 · research gaps=1 · sources=0 · retrieval state=DEGRADED
```

Never use list length as evidence yield.

## 7. Why Respawn Is Failing

The log does not prove that the respawn mechanism itself is broken. It proves that its success condition is unmet.

The current broadened retry:

- skips query planning;
- drops geography on the primary pass;
- caps extraction to three URLs;
- halves timeout;
- still relies on the same granted tools and underlying local/upstream services.

When SearXNG/Jina return no usable URLs or extraction produces no usable text, the broadened runner calls the LLM with no factual corpus. The LLM output is empty/invalid or deliberately gap-oriented, so the parent logs the respawn as failed.

The correct self-healing graph must branch on the failure class:

```text
attempt
  -> provider health?
       YES -> query broadening / extraction retry
       NO  -> quarantine engine
             -> alternate replica/source class
             -> grounded/API fallback
             -> explicit insufficient-evidence terminal state
```

## 8. Deep Loop/Graph-Based Remediation Pattern

Every remediation item must use this closed loop. Do not apply all changes and then run one large engagement; that destroys causal attribution.

```text
OBSERVE
  -> fingerprint runtime + capture counters
  -> classify failure
  -> select one remediation edge
  -> apply one minimal fix
  -> unit contract probe
  -> live dependency probe
  -> canary engagement
  -> compare before/after KPIs
      pass -> commit and advance
      fail -> inspect new evidence
              same failure -> revert/rework item
              new failure -> open new node
              3 failed passes -> freeze and escalate
```

### State graph

```text
BOOT
  -> PROCESS_READY
       -> CORPUS_READY
            -> ENGAGEMENT_ELIGIBLE
                 -> DISCOVERY
                      -> EXTRACTION
                           -> STRUCTURED_FINDINGS
                                -> SYNTHESIS
                                     -> FACTCHECK
                                          -> QUALITY
                                               -> RENDER
  -> CORPUS_DEGRADED -> RECOVER_RETRIEVAL
                          -> alternate source class
                          -> bounded retry
                          -> INSUFFICIENT_EVIDENCE
  -> any failure -> OBSERVABILITY_RECORD
                      -> retry only if failure class is retryable
```

### Mandatory loop invariants

1. A retry must change at least one dependency, source class, endpoint, or bounded policy; changing only wording is not recovery from a provider outage.
2. A gap is not a substantive finding.
3. A successful HTTP response is not a successful corpus response.
4. Every timeout has phase attribution.
5. Every fallback has a visible reason and result count.
6. A budget exhaustion event is terminal for that budget, not an empty successful answer.
7. Quality cannot repair missing evidence; it can only reject it or score it.
8. No report rendering starts from an unqualified `COMPLETED` specialist set.

## 9. Ordered Remediation TODO

### Phase 0: Provenance and evidence capture

- [ ] Restore or locate the real Git checkout used by WSL/Docker.
- [ ] Confirm `fix0.3` exists locally and on the remote; record `fix0.1` and `fix0.2` commit IDs.
- [ ] Add a runtime fingerprint procedure: Git commit, Python executable, import path, source hash, generated profile hash, settings hash, image digest, timeout values, and search budgets.
- [ ] Capture one complete engagement event stream as JSON, not only a terminal screenshot.
- [ ] Record counts for attempted searches, cache hits, HTTP failures, engine responses, URLs, extracted documents, valid findings, gap findings, provider calls, and phase durations.

**Gate:** The runtime that produced the next log is proven identical to the audited checkout.

### Phase 1: Correct the accounting model

- [ ] Split substantive findings from research gaps in all TUI, agent state, specialist summaries, and orchestrator totals.
- [ ] Define the typed research outcome enum and preserve error causes.
- [ ] Make invalid JSON, schema rejection, provider failure, timeout, and no-evidence distinct.
- [ ] Ensure `return_exceptions=True` preserves exception envelopes with task identity.
- [ ] Make specialist completion conditional on its output contract, not merely function return.

**Gate:** A synthetic empty search run reports zero substantive findings, one gap, zero sources, and `RETRIEVAL_DEGRADED`.

### Phase 2: Make retrieval health authoritative

- [ ] Separate SearXNG process readiness from corpus readiness.
- [ ] Implement per-engine result/403/429/timeout health and circuit state in the boot and engagement telemetry.
- [ ] Sweep expired persisted cooldowns and report active cooldowns before the first query.
- [ ] Decide whether the mounted limiter is intentionally disabled; make configuration and documentation agree.
- [ ] Disable or redesign the boot smoke so it does not consume fragile upstream capacity.

**Gate:** Restarting the stack does not create new bans, and boot explicitly identifies degraded corpus state.

### Phase 3: Rebuild bounded retrieval scheduling

- [ ] Replace serial query fan-out with a deadline-aware scheduler.
- [ ] Add per-query, per-engine, per-source-class, and per-sub-agent budgets.
- [ ] Stop dispatching after the evidence minimum is met.
- [ ] Cancel pending work when the phase deadline expires.
- [ ] Do not retry circuit-open engines or repeat the same endpoint after a provider-level failure.
- [ ] Ensure the analysis phase retains its minimum budget after retrieval.

**Gate:** Dead-pool sub-agent reaches explicit degraded outcome quickly; healthy-pool sub-agent reaches analysis with at least 60 seconds remaining.

### Phase 4: Use the SearXNG fleet intelligently

- [ ] Route general research through a policy-safe fleet strategy rather than web-first serial fallback.
- [ ] Preserve replica engine isolation while allowing parallel source-class requests.
- [ ] Add a permitted API-backed general evidence path where policy allows.
- [ ] Validate that full-pool fan-out is actually called from `search()` before Jina/grounding and is not dead code.
- [ ] Add a canary where web is fully suspended and reference/scholar recover a general query.

**Gate:** Two independent source classes return usable results when the web profile is unavailable.

### Phase 5: Implement real self-healing

- [ ] Classify failures before retry.
- [ ] Retry provider outages through alternate source classes, not query broadening alone.
- [ ] Permit exactly one broadened query retry only after dependency health is green.
- [ ] Make respawn accounting logical-question based and visible.
- [ ] Release failed sub-agent capacity only after its outcome is recorded.
- [ ] End with explicit `RETRY_EXHAUSTED`/`INSUFFICIENT_EVIDENCE`, never a fake successful finding.

**Gate:** Forced timeout, forced 403, forced empty corpus, and forced malformed JSON each follow the intended distinct branch and never loop indefinitely.

### Phase 6: Evidence-to-report contract

- [ ] Define minimum substantive findings and source-domain requirements by specialist.
- [ ] Rebuild affected report sections after retrieval escalation before rescoring.
- [ ] Unify the three/source and eight-domain evidence floors into one documented policy.
- [ ] Derive confidence from evidence coverage and verification state.
- [ ] Derive recommendation from one authoritative typed field and render it everywhere.
- [ ] Keep the quality gate as a refusal mechanism, not a repair mechanism.

**Gate:** Thin evidence produces a consistent insufficient-evidence report state and never a polished client report with contradictory verdict language.

### Phase 7: End-to-end canary and release gate

- [ ] Run a short healthy-stack canary with full telemetry.
- [ ] Run a web-degraded canary.
- [ ] Run an upstream-rate-limit canary.
- [ ] Run a provider/schema-failure canary.
- [ ] Run the full India/agriculture question only after all canaries pass.
- [ ] Confirm no report artifact is rendered unless quality is approved and no integrity blockers exist.
- [ ] Record before/after latency, source domains, substantive findings, timeout rate, and report completion.

**Release gate:** 0 silent empty outcomes, 0 unclassified exceptions, 0 substantive-count inflation from gaps, 0 unbounded retries, at least 8 distinct source domains for the full canary, and a completed report artifact.

## 10. Required Tests for the Remediation Session

These are instructions for the implementer, not tests run during this audit.

| Scenario | Expected result |
|---|---|
| SearXNG `/config` succeeds, every upstream fails | `PROCESS_READY`, `CORPUS_DEGRADED`; no normal engagement start |
| One web engine returns 429 | Engine quarantined; alternate source class selected |
| All web engines fail, scholar/reference healthy | General query recovers through permitted fleet path |
| Search phase timeout | No analysis call with empty evidence unless explicitly configured; phase outcome says timeout |
| LLM returns malformed JSON | Format repair or typed analysis failure; no silent `[]` |
| LLM returns only a gap | TUI shows zero substantive findings and one gap |
| Broad respawn fails | One retry only, then `RETRY_EXHAUSTED`; no third attempt |
| Search budget exhausted | Loud terminal budget event; no later empty-success searches |
| Quality corpus floor fails | Retrieval escalation is logged, changes report inputs, then rescoring occurs |
| Verdict disagreement | Block before design/render with one authoritative diagnostic |

## 11. Non-Findings and Avoided Misdiagnoses

- The evidence does not establish that the YAML profiles are syntactically broken. The `/config` readiness path and generated profile structure are broadly coherent.
- More model tokens will not repair a zero-source evidence bundle.
- Raising the 600-second timeout will likely make the user wait longer unless retrieval is fail-fast and phase-budgeted.
- Increasing the quality iteration count will not create independent source domains.
- Adding more retries to the same SearXNG engines will increase rate limiting and worsen the incident.
- A healthy Valkey startup does not prove healthy upstream search.
- Three replicas on one public IP do not evade upstream bans.

## 12. Audit Disposition

**Status:** remediation required; report generation is not release-safe under the observed degraded-search conditions.  
**Code changes made in this audit:** none.  
**Testing performed:** none, per operator instruction.  
**Commit/push:** blocked because the supplied workspace is not a Git checkout and no remote/branch metadata is available. The implementer must place this document in the actual `fix0.3` checkout, inspect status/diff/log, commit only the audit document, and push after verifying the remote target.
