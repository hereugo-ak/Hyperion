# HYPERION FIX-0.1 — Sub-Agent Retrieval Exhaustion: Diagnosis & Production-Grade Remediation

**Session under audit:** 2026-08-10 (TUI transcript, `COMPETE` pricing-scrape wave)
**Branch policy:** cut on `fix0.3`. **NEVER touch `main`.** All fixes land on `fix0.3` in one commit per layer.
**Status:** **REMEDIATED 2026-08-10** — all 14 items (F-0.1-1..F-0.1-14) implemented and covered by `tests/test_fix01_sub_agent_retry.py`. See §9 for the implementation map.
**Observed symptom:** the **majority** of respawned sub-agents — overwhelmingly the "Scrape <company> pricing page" class — terminate in `SUB-AGENT RETRY EXHAUSTED` with exactly `1 finding(s), 1 gap(s)`. The same session surfaced four companion specialist-tier defects this plan also closes: framework-empty "✓ complete" reports, a raw `KeyFinding` placeholder validation error instead of a gap, a finance run that "completed with 0 findings" despite recorded findings, and the sub-agent budget blowing through its ceiling (`8/6`) so legitimate late tasks never spawned.

---

## 1. Executive verdict

The sub-agent failure is **structural, not environmental**. It is the product of a three-layer defect chain:

1. **Runner architecture** — `SubAgentRunner._gather_raw_data()` only extracts URLs discovered by its two search legs (`searxng` / `jina`). A spec granted neither produces zero URLs, and the extraction ladder (which would run free tiers) never executes.
2. **Spec tool grants** — the pricing-scrape specs grant only `[ToolName.OBSCURA]`. No discovery leg. The competitor's resolved URL is passed in `context` and **never fetched by any code path**.
3. **Respawn policy** — the one permitted broadened respawn is a *search* recovery (drop geography, skip the planner, cap extraction at 3 URLs, halve the timeout). For a *fetch* task it retries the wrong thing and then honestly types `RETRY_EXHAUSTED`.

Because layers 1 and 2 are deterministic, an OBSCURA-only scrape sub-agent **cannot succeed on any site, ever**, regardless of data availability, engine health, or model tier. That is why the failure hits the majority, not a minority.

**Chief conclusion:** fixing this requires (a) a direct-fetch path for explicit URLs, (b) restoring discovery capability to scrape specs, and (c) differentiating the recovery action by the *failure class* — with a fallback data-route ladder and a labeled-estimate closure contract so the final deliverable never ships a blank cell. This document specifies that architecture. The same session's companion defects — fake-success "✓ complete" reports, placeholder rejection surfacing as raw errors, the finance ledger/bus accounting mismatch, and the `8/6` budget blow-through (specified in §3.5–3.8, F-0.1-11..14) — share the same empty-retrieval upstream cause and are closed in the same plan.

---

## 2. Observed symptom (verbatim)

```
[19:06:19]  TOOL        ▸ COMPETE system.log.SUB-AGENT RETRY EXHAUSTED: Scrape AST SpaceMobile
                        pricing page, extract pricing tiers, features per tier, a —
                        1 finding(s), 1 gap(s); ending with explicit insufficient evidence
[19:06:19]  COMPETE     ▸ Sub-agent returned 1 findings
```

Mechanically, this exact pair of lines is produced by `BaseAgent._spawn_sub_agent` (`hyperion/agents/base.py:1069`, `:1102`):

1. `SubAgentRunner.run()` runs the full lifecycle (search → extract → analyze).
2. Nothing usable is gathered; the analysis LLM receives `"No raw data available from tools."`.
3. The runner returns a single synthetic `research_gap` finding ("retrieval or LLM analysis returned no validated findings", `sub_agent.py:1597-1608`), stamps `outcome`, and the parent logs `Sub-agent returned 1 findings` — the "1 finding" is the gap itself.
4. F-07 grants exactly **one** broadened respawn (`base.py:1077-1092`).
5. The respawn hits the identical dead end; `outcome = ResearchOutcome.RETRY_EXHAUSTED` is stamped (`sub_agent.py:1567-1572`) and the parent logs `SUB-AGENT RETRY EXHAUSTED ... 1 finding(s), 1 gap(s)`.

**Additional symptoms from the same session** — the companion defects this plan also closes (see §3.5–3.8):

```
[18:50:35]  INNOVATE     ✓ Innovation analysis complete: 0 TRL assessments, 0 hype cycle positions,
                        6 horizon signals, ... confidence=low
[18:50:35]  COMPETE      ✗ 1 validation error for KeyFinding — title: Value error, placeholder text is
                        unrepresentable ... ('no competitors identified'); raise an AnalysisGap ...
[19:08:11]  STRATEGY     system.log.SUB-AGENT RETRY EXHAUSTED: Find competitor strategic moves in space,
                        recent announcements, M&A ... — 1 finding(s), 1 gap(s)
[19:08:12]  ORCHESTRAT   system.log · financial_analyst: completed with 0 findings (total collected: 23)
[19:12:19]  COMPETE      system.log.SUB-AGENT total budget reached (8/6); proceeding without spawning:
                        Scrape SpaceX pricing page ... / Rocket Lab ... / Find ISRO funding stage ...
```

---

## 3. Root-cause analysis (the defect chain)

### 3.1 Layer 1 — The runner never fetches an explicit URL

- `_gather_raw_data()` builds `all_urls` exclusively from the `searxng` + `jina` search legs (`sub_agent.py:918`).
- Extraction runs only `if all_urls:` — otherwise the `UnifiedExtract` ladder never fires.
- `spec.context` is read in exactly two places: the LLM user prompt (`sub_agent.py:391`) and query planning (`sub_agent.py:1085`). **The context URL is never handed to the ladder.**

### 3.2 Layer 2 — OBSCURA-only specs have no discovery leg

- Pricing-scrape specs grant `tools=[ToolName.OBSCURA]` (`competitive_intel.py:1092-1108`). No `searxng`, no `jina`.
- The specialist *does* resolve the competitor's website before spawning (`_find_competitor_website`, `competitive_intel.py:496-507`) and even builds the concrete page list (`{url}/pricing`, `{url}/product`, …) — but only passes `context={"competitor": ..., "url": ...}` into the spec, which the runner ignores for retrieval.
- Result: `counters.raw_results = 0`, `counters.extracted_documents = 0`, analysis input is the literal string `"No raw data available from tools."` → deterministic gap → deterministic exhaustion.

### 3.3 Layer 3 — The respawn recovers the wrong failure class

- `_should_respawn_broadened` (`base.py:1129-1148`) triggers on timeout or the synthetic zero-yield gap when dependency health is GREEN.
- The broadened spec: `timeout_seconds // 2` (`base.py:1085`), planner skipped, geography dropped (`sub_agent.py:1280-1284`), **extraction capped at 3 URLs** (`sub_agent.py:965`).
- Those are search-thinness remedies. For a blocked/absent fetch they neither find the page nor fetch it. The retry is spent; `RETRY_EXHAUSTED` is then the honest, correct terminal state given the current recovery set.

### 3.4 Why the majority

- In COMPETE, **2 of the 3** sub-agent specs are the OBSCURA-only scrape type.
- The identical grant pattern recurs across specialists: M&A culture reviews (`tools=[OBSCURA]`), Market Analyst adoption/penetration (`OBSCURA+JINA`), Technology Analyst vendor pages, Consumer Insights review scrapes.
- Where search tools *are* granted, JS-rendered pricing pages still defeat the always-on browserless tiers (`curl_cffi`/`http`), and Obscura may be unavailable (binary absent → tier skipped) → same zero-extraction path.
- The same session shows the **search-empty variant**: STRATEGY's "Find competitor strategic moves, M&A" sub-agent also exhausted (`19:08:11`) — there the search legs *ran* but returned zero usable results, so the failure class is `LOW_YIELD` (nothing found), not `FETCH_BLOCKED`. Both classes funnel into the identical synthetic-gap → one-broaden-respawn → exhausted path, which is why the recovery action must be typed by failure class (F-0.1-10): broadening helps `LOW_YIELD`, never `FETCH_BLOCKED`.

### 3.5 Specialist-tier fake success — "✓ complete" with zero substance

`INNOVATE ✓ complete: 0 TRL, 0 hype, confidence=low` (`18:50:35`). The model returned a structurally-valid but content-empty `InnovationAnalysis`. Nothing gates a specialist on its **mandatory framework outputs** (INNOVATE: `trl_assessments ≥ 1`, `hype_positions ≥ 1`; COMPETE: `competitors ≥ 1`), so an empty-but-valid response is reported as success and the wave moves on. This is the §0.3 anti-pattern your audits already fought, surfacing at the specialist tier instead of the sub-agent tier.

### 3.6 Placeholder rejection is not converted into a gap

`✗ 1 validation error for KeyFinding — 'no competitors identified'` (`18:50:35`). The P2-16 placeholder guard (`schemas/models.py:73-83`) correctly rejects unrepresentable filler — but COMPETE surfaced a raw validation ERROR instead of executing the instruction embedded in the error message: "raise an AnalysisGap and run the gap-closure loop instead." The machinery exists (`AnalysisGap` is first-class; `orchestrator._gap_closure_phase` at `orchestrator.py:1312/1331` runs the W-07 ladder); the specialist never converts to it.

### 3.7 Specialist delivery accounting — finance "completed with 0 findings" despite recorded findings

`financial_analyst: completed with 0 findings (total collected: 23)` (`19:08:12`) after a full 9-step run whose steps published findings ("finding recorded" surfaced in the TUI from `Channel.FINDINGS` messages). The orchestrator counts `len(agent._findings)` at wave end (`orchestrator.py:944-952`), so the per-agent `_findings` ledger and the bus publish path disagree: findings that reached the client channel never entered the attribute the orchestrator counts. The delivery contract between specialist publish and orchestrator collection is unenforced.

### 3.8 Sub-agent budget semantics — ceiling blown (`8/6`) before the last tasks spawn

`SUB-AGENT total budget reached (8/6); proceeding without spawning` (`19:12:19`) starved the final three legitimate tasks (SpaceX pricing, Rocket Lab pricing, ISRO funding). The ceiling is `SUB_AGENT_TOTAL_CEILING = 6` (`base.py:972`), checked as `len(self._sub_agent_specs) >= 6` (`base.py:1003-1009`) — but F-07e lets the **broadened respawn bypass the gate** (it is a retry of the same logical question), so `_sub_agent_specs` counts *attempts*, not *work items*. Every question that fails its first pass double-dips the budget (original + respawn); a wave that front-loads on failing questions consumes the ceiling and the remaining questions never spawn. The F-08 yield-aware intent (zero-yield releases the slot) is defeated because the respawn attempt still appends to the same counter.

---

## 4. Impact

| Dimension | Impact |
|---|---|
| Evidence yield | Pricing tiers / features / discounts missing from competitive, market, tech, M&A sections |
| Quality gate | Fewer substantive findings per section → corpus floor / consistency pressure downstream |
| Wall-clock | Every exhausted sub-agent burns a full pass + a respawn against a known-dead path |
| Trust | `Sub-agent returned 1 findings` reads as success in the TUI while being a pure gap |
| Report depth | Sections degrade from evidence-backed to assumption-thin without the gap being labeled as an estimate |
| Zero-substance success | Empty-but-valid specialist responses report "✓ complete" — the reporting closes, not the cause |
| Delivery integrity | `KeyFinding` placeholder validation errors surface as raw ERROR rows instead of typed gaps |
| Findings accounting | Bus-published findings can count as 0 (`financial_analyst ... 0 findings`) — the ledger and the client disagree |
| Budget control | Ceiling blow-through (`8/6`) starves legitimate late tasks; budget counts attempts, not work items |

---

## 5. Remediation architecture

### P0 — Make the primary path work (stop the bleeding; ~half a day + tests)

**F-0.1-1 — Targeted fetch of the context URL (core fix).**
In `_gather_raw_data()`, before search-derived extraction, seed the extraction targets with the explicit URL from `spec.context["url"]` (and any URL named in the question), ranked first, merged with search URLs and deduplicated. Reuse the existing `_extract_urls` / `UnifiedExtract.extract_ladder` path unchanged. The ladder's always-on `curl_cffi` / `http` tiers need no tool grant, so a plain fetch works even with `tools=[OBSCURA]`; granted tools add the JS-capable tiers.

**F-0.1-2 — Tier escalation with capability gating.**
Single page fetch returning zero usable text must escalate up the tier ladder (curl_cffi → http → obscura → scrapling → crawl4ai), each tier with a short per-tier timeout, gated on (a) tool granted, (b) binary present, (c) platform support. Never attempt a browser tier the grant forbids.

**F-0.1-3 — Route probing.**
Explicit URL fails (404/redirect/empty) → bounded probing of `{url}/pricing`, `/plans`, `/pricing/`, `/packages`, plus the pricing link discovered from a homepage fetch. Max 3 probes; first page yielding usable text wins.

**F-0.1-4 — Spec hygiene.**
Scrape specs gain discovery: `tools=[SEARXNG, JINA, OBSCURA]` (`competitive_intel.py:1092-1108` and the same pattern in the other specialists). Parent passes the concrete page URL (already computed at `competitive_intel.py:502-507`) into `context["url"]` instead of only the bare website root.

### P1 — Never let the associate give up (consultant-grade; ~1 day + tests)

**F-0.1-5 — Sufficiency gate.**
After extraction, a cheap deterministic check for pricing artifacts (`$`, `per month`, `per year`, tier names, plan columns). If absent, the run is *not* accepted as success — it feeds the fallback routes (F-0.1-6) instead of returning a gap.

**F-0.1-6 — Fallback data-route ladder.**
In order: (1) Wayback snapshot of the pricing page; (2) search-snippet mining (the fan-out already returns snippets — mine them for `from $X/mo` patterns); (3) third-party aggregators / review sites (G2, Capterra, press releases); (4) analyst coverage. First route that satisfies the sufficiency gate wins.

**F-0.1-7 — Closure contract (labeled-estimate path).**
If every route fails and the question is quantitative (pricing, sizing): trigger the **analog-estimation** path — benchmark 2-3 comparable companies (e.g., for AST SpaceMobile: Starlink / Iridium / ViaSat), produce the estimate, stamp `confidence=low` + `assumption=analog_estimate`, and surface a "data not publicly available" limitation in the report. **Never ship a blank cell; ship a labeled estimate.** If no reasonable analog exists, escalate for human-in-the-loop rather than silently degrading.

### P2 — Operations-grade hardening (~half a day + tests)

**F-0.1-8 — Shared fetch cache.**
Per-engagement URL-hash → extracted-content cache so the same competitor page is never fetched twice by concurrently running specialists. Enforce a per-phase deadline for the scrape phase (mirror `FAN_OUT_DEADLINE_SECONDS`).

**F-0.1-9 — Recovery telemetry.**
Aggregate `RETRY_EXHAUSTED` by failure class (`NO_DISCOVERY` / `FETCH_BLOCKED` / `PROVIDER_FAILURE`) and by URL into the engagement report; wire ladder tier-level yield into `engagement_yield_report`. Surface `raw_results` / `extracted_documents` on the RETRY EXHAUSTED TUI line so `raw=0` (no discovery) reads differently from `raw=14, extracted=0` (fetched but blocked).

**F-0.1-10 — Failure-class-aware respawn.**
Carry a typed `recovery_hint` on the runner and branch `_should_respawn_broadened` on it: `FETCH_BLOCKED` → re-fetch same URLs up the tier ladder (no 3-URL cap, full timeout); `LOW_YIELD` → today's broaden behavior (this is the STRATEGY M&A case — search ran, nothing found); `PROVIDER_FAILURE` → no respawn, typed `ANALYSIS_FAILED`, escalate.

**F-0.1-11 — Framework-completeness gate (specialist tier).**
Each specialist declares **mandatory output keys** (INNOVATE: `trl_assessments ≥ 1`, `hype_positions ≥ 1`; COMPETE: `competitors ≥ 1`; FINANCE: quantitative metrics present; …). On completion, any empty mandatory key → the run is *not* "✓ complete"; it is stamped `AnalysisGap(reason=framework_insufficient: <key>=0)` and routed into the existing W-07 gap-closure ladder (`orchestrator._gap_closure_phase`). The TUI shows `▸ gap` with the typed reason, never a false success.

**F-0.1-12 — Placeholder → AnalysisGap conversion guard.**
At the specialist boundary, catch `ValueError`s from `KeyFinding`/model validation whose message matches the placeholder class (`schemas/models.py:73-83`) and convert them into `AnalysisGap(reason=...)`, then continue — never a raw `ERROR` row. Prompt contract hardened in parallel: *"If retrieval returns 0 results, return `findings=[], gaps=[...]` — never placeholder text as a finding."* (F-07 discipline applied to the specialist tier.)

**F-0.1-13 — Specialist delivery contract (single ledger of record).**
The findings count at wave end must come from the same store the bus publishes from — route `Channel.FINDINGS` writes through the specialist's `_findings` ledger, or count from the bus/ledger at `orchestrator.py:944-952`. Assert ≥1 published finding after any run that recorded one; "finding recorded" that counts as 0 is a contract failure, not a normal outcome.

**F-0.1-14 — Budget semantics: count work items, not attempts.**
Budget in *distinct work items*: the broadened respawn is a retry of the same question and must not consume additional budget — the gate checks a `{question-hash}` set of distinct questions rather than `len(self._sub_agent_specs)`. Add per-wave **priority ordering** so a question that exhausts its retries is deprioritized rather than counted as a fresh slot; late legitimate tasks (SpaceX / Rocket Lab / ISRO) must never starve behind failed questions. `max_sub_agents` remains the true concurrency bound.

---

## 6. Acceptance criteria & verification

| ID | Criterion | Verification |
|---|---|---|
| A1 | OBSCURA-only scrape spec + `context.url` returns `extracted_documents ≥ 1` on a fetchable page | Unit test on `_gather_raw_data` with a golden HTML fixture |
| A2 | Live repro flip: COMPETE pricing scrape before → `raw_results=0` + `RETRY_EXHAUSTED`; after → ≥1 substantive finding | Live probe on a real competitor page (or wayback snapshot) |
| A3 | Blocked primary page still yields data via fallback ladder | Chaos test: primary URL 403s → wayback/snippet route succeeds |
| A4 | Quantitative question with no public data → labeled analog estimate, not a gap | Contract test on the closure path (`confidence=low`, `assumption=analog_estimate`) |
| A5 | Duplicate fetches of the same URL within an engagement = 1 network call | Cache unit test |
| A6 | All F-01 / F-02 / F-07 / F-08 regression tests stay green | `pytest tests/test_chief_audit_fix03_findings.py tests/test_fix03_regressions.py` |
| A7 | INNOVATE returning 0 TRL/hype → `AnalysisGap(framework_insufficient)`, not "✓ complete" | Contract test on the completeness gate with an empty-but-valid model |
| A8 | Placeholder `KeyFinding` title → converted `AnalysisGap`, no raw validation ERROR | Unit test on the specialist boundary guard |
| A9 | FINANCE run with bus-published findings counts ≥1 at wave end (ledger == bus) | Accounting contract test at `orchestrator.py:944-952` |
| A10 | Budget consumed per distinct question; failed-then-respawned question costs 1, not 2; late tasks still spawn | Budget-semantics unit test (8/6 repro) |

---

## 7. Honest limitations

- **Paywalls / login walls / geo-blocking** cannot be automated around; those resolve via the labeled-estimate or human-in-the-loop path.
- **Genuinely unpublished pricing** (e.g., AST SpaceMobile — B2B enterprise satellite connectivity sold through carriers; consumer pricing is not public) must be *concluded*, not retried. The correct output is an explicit "not publicly disclosed" finding + analog benchmark, which F-0.1-7 formalizes.
- **Engine-health dependence** (F-02): the fallback ladder must still respect the dependency health gate — a dead pool means the snippet route is also dead; the system should say so instead of looping.
- **Search-layer zeros are upstream of this plan.** When a well-known query ("competitors in space India") returns zero results, the failure may be engine health (the FIX0.3 domain) rather than the page. The gates must distinguish `LOW_YIELD` (no data found) from `PROVIDER_FAILURE` (search dead) — never conclude "no competitors exist" because a dead pool answered — via F-0.1-10's typed recovery and the engine-health floor.

---

## 8. Code references

| Location | Role |
|---|---|
| `hyperion/agents/sub_agent.py:918` | `all_urls` built only from search legs |
| `hyperion/agents/sub_agent.py:391`, `:1085` | `spec.context` read only for prompt/planning — never fetched |
| `hyperion/agents/sub_agent.py:965` | broadened extraction cap = 3 |
| `hyperion/agents/sub_agent.py:1567-1572` | `RETRY_EXHAUSTED` typing |
| `hyperion/agents/sub_agent.py:1597-1608` | synthetic `research_gap` production |
| `hyperion/agents/base.py:1077-1092` | one broadened respawn + timeout halving |
| `hyperion/agents/base.py:1102` | `SUB-AGENT RETRY EXHAUSTED` log line |
| `hyperion/agents/base.py:1129-1148` | `_should_respawn_broadened` policy |
| `hyperion/agents/specialists/competitive_intel.py:1092-1108` | OBSCURA-only pricing-scrape specs |
| `hyperion/agents/specialists/competitive_intel.py:496-507` | competitor URL resolution + page list (unused by sub-agents) |
| `hyperion/schemas/models.py:73-83` | P2-16 placeholder guard — rejects, but specialists don't convert to gap (F-0.1-12) |
| `hyperion/orchestrator.py:1312`, `:1331` | existing gap-closure phase — the underused target for F-0.1-11/12 |
| `hyperion/orchestrator.py:944-952` | per-agent `_findings` count — the ledger/bus mismatch (F-0.1-13) |
| `hyperion/agents/base.py:972`, `:1003-1009` | `SUB_AGENT_TOTAL_CEILING=6` + attempt-counted gate (F-0.1-14) |
| `hyperion/agents/specialists/innovation_analyst.py` | framework outputs (TRL / hype positions) — no completeness gate (F-0.1-11) |
| `hyperion/agents/specialists/financial_analyst.py` | publishes findings while `_findings` stays empty at wave end (F-0.1-13) |
