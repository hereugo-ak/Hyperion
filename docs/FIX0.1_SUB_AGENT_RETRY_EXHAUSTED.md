# HYPERION FIX-0.1 — Sub-Agent Retrieval Exhaustion: Diagnosis & Production-Grade Remediation

**Session under audit:** 2026-08-10 (TUI transcript, `COMPETE` pricing-scrape wave)
**Branch policy:** cut on `fix0.3`. **NEVER touch `main`.** All fixes land on `fix0.3` in one commit per layer.
**Status:** AUDIT COMPLETE — **no code changed in this session; this document is the remediation plan.**
**Observed symptom:** the **majority** of respawned sub-agents — overwhelmingly the "Scrape <company> pricing page" class — terminate in `SUB-AGENT RETRY EXHAUSTED` with exactly `1 finding(s), 1 gap(s)`.

---

## 1. Executive verdict

The sub-agent failure is **structural, not environmental**. It is the product of a three-layer defect chain:

1. **Runner architecture** — `SubAgentRunner._gather_raw_data()` only extracts URLs discovered by its two search legs (`searxng` / `jina`). A spec granted neither produces zero URLs, and the extraction ladder (which would run free tiers) never executes.
2. **Spec tool grants** — the pricing-scrape specs grant only `[ToolName.OBSCURA]`. No discovery leg. The competitor's resolved URL is passed in `context` and **never fetched by any code path**.
3. **Respawn policy** — the one permitted broadened respawn is a *search* recovery (drop geography, skip the planner, cap extraction at 3 URLs, halve the timeout). For a *fetch* task it retries the wrong thing and then honestly types `RETRY_EXHAUSTED`.

Because layers 1 and 2 are deterministic, an OBSCURA-only scrape sub-agent **cannot succeed on any site, ever**, regardless of data availability, engine health, or model tier. That is why the failure hits the majority, not a minority.

**Chief conclusion:** fixing this requires (a) a direct-fetch path for explicit URLs, (b) restoring discovery capability to scrape specs, and (c) differentiating the recovery action by the *failure class* — with a fallback data-route ladder and a labeled-estimate closure contract so the final deliverable never ships a blank cell. This document specifies that architecture.

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

---

## 4. Impact

| Dimension | Impact |
|---|---|
| Evidence yield | Pricing tiers / features / discounts missing from competitive, market, tech, M&A sections |
| Quality gate | Fewer substantive findings per section → corpus floor / consistency pressure downstream |
| Wall-clock | Every exhausted sub-agent burns a full pass + a respawn against a known-dead path |
| Trust | `Sub-agent returned 1 findings` reads as success in the TUI while being a pure gap |
| Report depth | Sections degrade from evidence-backed to assumption-thin without the gap being labeled as an estimate |

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
Carry a typed `recovery_hint` on the runner and branch `_should_respawn_broadened` on it: `FETCH_BLOCKED` → re-fetch same URLs up the tier ladder (no 3-URL cap, full timeout); `LOW_YIELD` → today's broaden behavior; `PROVIDER_FAILURE` → no respawn, typed `ANALYSIS_FAILED`, escalate.

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

---

## 7. Honest limitations

- **Paywalls / login walls / geo-blocking** cannot be automated around; those resolve via the labeled-estimate or human-in-the-loop path.
- **Genuinely unpublished pricing** (e.g., AST SpaceMobile — B2B enterprise satellite connectivity sold through carriers; consumer pricing is not public) must be *concluded*, not retried. The correct output is an explicit "not publicly disclosed" finding + analog benchmark, which F-0.1-7 formalizes.
- **Engine-health dependence** (F-02): the fallback ladder must still respect the dependency health gate — a dead pool means the snippet route is also dead; the system should say so instead of looping.

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
