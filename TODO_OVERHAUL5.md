# OVERHAUL5 — TODO / Fix Checklist

Status legend: ⬜ pending · 🔧 in progress · ✅ done · ❌ blocked (state why)
Master plan: `overhaul5.md` (defects D-01..D-14, W0..W8)

## W0 · Fix the two stale paid adapters (D-01) — ✅ done
- [x] ✅ W0.1 you.py → `ydc-index.io/v1/search`, body `count`, parse `results.web[]` (verified code in docs/YOU_YEP_API_FINDINGS.md)
- [x] ✅ W0.2 yep.py → `platform.yep.com/api/search`, POST+JSON, parse `results[]` + api_cost/balance
- [x] ✅ W0.3 bring docs/YOU_YEP_API_FINDINGS.md + scripts/check_you_yep_search.py into repo
- [x] ✅ W0.4 live test: `python scripts/check_you_yep_search.py` → HTTP 200 × 2, ≥5 results each (WSL, verified 2026-08-12)
- [x] ✅ W0.5 test file (fail-first) — 2 parsing tests FAIL on old adapters / PASS on new; 2 live tests skip without keys
- [ ] ⬜ W0.6 commit + push fix0.3 (next commit)

## W1 · Web-class quality trigger + corpus tagging (D-02, D-03) — ✅ done
- [x] ✅ W1.1 `SearchResult.web_class` field (types.py + searxng.py local class) + classify_web_class (source_classifier: web engines / non-web engines / paywall hosts)
- [x] ✅ W1.2 searxng.py trigger: general-web query returns early only if ≥ MIN_WEB_RESULTS=5 web-class results (rotation + fan-out both gated)
- [x] ✅ W1.3 fan-out results tagged web_class=False (crossref/wikipedia/arxiv…) — never satisfy web trigger; rescue preserved
- [x] ✅ W1.4 tests (fail-first): 5 new — thin-web→paid, scholar-DOI→paid, full-web→no-paid, non-web never gated, paid flow retrieval_degraded; F-03 test updated to new contract
- [ ] ⬜ W1.5 commit + push (next commit)

## W2 · No re-entry + recursion guard (D-04) — ✅ done
- [x] ✅ W2.1 orchestrator paid-only chain: `search(..., exclude={SearxNGAdapter})` — caller already exhausted SearXNG
- [x] ✅ W2.2 recursion guard: `_IN_PAID_CHAIN` ContextVar — re-entrant `SearxNGClient.search` returns empty before cache/budget/rotation
- [x] ✅ W2.3 tests (fail-first, 3): exclude skips real SearxNG adapter (SearxNGClient.search 0 calls, paid tiers reached); guard returns empty w/o rotation; searxng.py passes exclude + clears guard
- [ ] ⬜ W2.4 commit + push (next commit)

## W3 · Paid suspension + visibility (D-05) — ✅ done
- [x] ✅ W3.1 suspension.py: paid 403 → 120s cooldown + retry, permanent only after 3 consecutive; success resets counter (bucket_exhausted stays permanent)
- [x] ✅ W3.2 orchestrator: TUI system.log per paid attempt — provider, query, results/error, cooldown state (search_layer sender)
- [x] ✅ W3.3 tests (fail-first, 4): cooldown-then-retry-then-permanent; success resets; failure emits TUI line; success emits TUI line; search_layer 403 test updated to new contract
- [x] ✅ W3.4 canaries green (16/16); commit + push (next commit)

## W4 · Paywall pre-classifier + firecrawl load guard (D-06) — ✅ done
- [x] ✅ W4.1 unified_extract: `paywall` URL profile — fail fast with typed PAYWALL reason, zero tier attempts (single + ladder paths)
- [x] ✅ W4.2 paywall host list: `_PAYWALL_HOSTS` + `is_paywall_host()` in source_classifier (doi.org, elsevier, springer, wiley, taylorfrancis, emerald, mdpi, ssrn…; subdomain match)
- [x] ✅ W4.3 firecrawl wave cap ≤2 concurrent (single-worker stack) — transient "Can't accept connection" already retried
- [x] ✅ W4.4 tests (fail-first, 4): single paywall fails fast w/ zero tiers; ladder batch fails fast; paywall beats js_heavy; firecrawl waves ≤2
- [ ] ⬜ W4.5 live: ladder on doi.org → PAYWALL < 2s (WSL, after pull); commit + push (next commit)

## W5 · UNIFIED_EXTRACT tool for all specialists (D-07) — ✅ done
- [x] ✅ W5.1 ToolName.UNIFIED_EXTRACT + _instantiate_tool binding (UnifiedExtractTool facade: extract(urls, query) → [{url, content, source}])
- [x] ✅ W5.2 grant to all 12 specialists + schema test (12 parametrized specs all have it)
- [x] ✅ W5.3 rewired highest-value direct sites: competitive_intel (crash site, ladder), operations (was tool-less); other specialists keep working jina/obscura direct paths (migrate opportunistically)
- [x] ✅ W5.4 commit + push (next commit)

## W6 · Finding quality at birth (D-08, D-09, D-10)
- [ ] ⬜ W6.1 relevance gate at finding construction (OFF_TOPIC typed reject)
- [ ] ⬜ W6.2 gap placeholders → open_gaps only, never findings
- [ ] ⬜ W6.3 content-hash dedupe in findings bus (recovery can't double-add)
- [ ] ⬜ W6.4 metric parse failure → absent (omit row + stated gap), never 'Unknown'
- [ ] ⬜ W6.5 verdict: narrative generated FROM structured field (one writer)
- [ ] ⬜ W6.6 tests (fail-first) × 4; canaries green; commit + push

## W7 · Checkpointed specialists + COMPETE crash (D-11, D-12)
- [ ] ⬜ W7.1 specialists publish partial model checkpoints at step boundaries
- [ ] ⬜ W7.2 timeout_at_final_completion typed; final completion retried once
- [ ] ⬜ W7.3 competitive_intel.py:725/743 — bind content in both paths
- [ ] ⬜ W7.4 tests (fail-first) × 2; commit + push

## W8 · Visibility + typed recovery (D-13, D-14)
- [ ] ⬜ W8.1 boot probe: provider key_ok/endpoint_ok table at engagement start
- [ ] ⬜ W8.2 run-end cost report always printed (P9) + /status live panel
- [ ] ⬜ W8.3 mid-run telemetry (provider calls, corpus per class, budget)
- [ ] ⬜ W8.4 recovery typed-failure → remedy table (EVIDENCE_THIN → paid-first re-run)
- [ ] ⬜ W8.5 recovery budget: 1 pass per blocker class
- [ ] ⬜ W8.6 tests (fail-first) × 3; commit + push

## Definition of Done (overhaul5.md §5)
- [ ] ⬜ `python -m pytest -q` green (minus env-dependent)
- [ ] ⬜ `python -m hyperion.eval.canaries` green (16 + new W0-W8)
- [ ] ⬜ `python -m hyperion.eval.ci_gate` green
- [ ] ⬜ LIVE GATE (WSL): paid chain fires (≥1 paid ledger record), web ≥ 8 domains, extraction > 0, no integrity blockers, score ≥ 3.0, PDF ships, no 180s storm

## Open decisions (overhaul5.md §7 — change if you disagree)
- [ ] ⬜ confirm MIN_WEB_RESULTS=5, chain order SearXNG→You→Exa→Tavily→Yep, Yep cap 30/run, fan-out rescue kept (tagged), paid 403→cooldown
