# OVERHAUL4 — TODO / Fix Checklist

Status legend: ⬜ pending · 🔧 in progress · ✅ done · ❌ blocked (state why)

## Phase 1 — Kill the rate-limit death spiral
- [x] ✅ P1.1 `EngineTokenBucket.interval_seconds` 2.0 → 6.0 (searxng.py)
- [x] ✅ P1.2 suspension expiry verified lazy-strict (engine_health.py `state()` — no change needed)
- [x] ✅ P1.3 engine-health + category-contract tests pass (13 passed)

## Phase 2 — Query hygiene at the dispatch choke point
- [x] ✅ P2.1 add `_strip_instruction_debris` (scrape/extract/site:/OR/AND) + apply in `_shape_query_for_profile` (searxng.py)
- [x] ✅ P2.2 planner system-prompt rule: "queries are keyword searches, never scrape/extract instructions"
- [x] ✅ P2.3 regression test: plan containing scrape/site: fails sanitization (test_overhaul4_regressions.py — 6 sanitizer cases pass)

## Phase 3 — The report must never be empty when findings exist
- [x] ✅ P3.1 add `AgentName.STRATEGY_ANALYST` to `SECTION_PRODUCING_AGENTS`
- [x] ✅ P3.2 deterministic finding-digest section fallback in `_build_one_section` (loud log, last resort)
- [x] ✅ P3.3 `_get_participating_agents` fallback so methodology is never empty
- [x] ✅ P3.4 unit test: `_deterministic_section_body` produces a real, citable section (test_overhaul4_regressions.py)

## Phase 4 — Corpus floor counts the ledger, not just citations
- [x] ✅ P4.1 `_corpus_floor_blocker` consults `get_evidence_ledger().distinct_domains()`; ledger ≥ floor ⇒ no hard block
- [x] ✅ P4.2 tests: rich ledger ⇒ no blocker; thin ledger ⇒ blocker (test_overhaul4_regressions.py)

## Phase 5 — Recovery escalates to living backends
- [x] ✅ P5.1 `_escalate_retrieval`: skips SearxNG when fleet < 2 healthy engines
- [x] ✅ P5.2 OpenAlex + Semantic Scholar + Jina direct-API recovery legs (error-safe)
- [x] ✅ P5.3 escalation wall-clock cap 45 s
- [ ] ⬜ P5.4 test: all engines suspended ⇒ escalation still recovers (moved to Phase 7 canaries — needs orchestrator mocking)

## Phase 6 — Restore a working web class (no keyed APIs)
- [ ] ⬜ P6.1 **probe running image `/config` on the HOST** (this box can't reach the containers) — command below
- [x] ✅ P6.2 generator + base settings + `services.py` replicas + `searxng.py` constants updated; profiles regenerated (web += marginalia, wiby; reference += wikidata)
- [x] ✅ P6.3 `.env.example`/config: semantic scholar key (added), openalex mailto (wired as Settings field)
- [ ] ⬜ P6.4 copy settings to host + restart + live preflight shows web class > 0 domains

> **PROBE (run on the Linux host where the stack lives, BEFORE deploying):**
> ```
> for p in 8888 8889 8890; do echo "port $p:"; curl -s -m 5 http://127.0.0.1:$p/config | python3 -c "import sys,json; d=json.load(sys.stdin); print(sorted(e['name'] for e in d['engines'] if e.get('enabled')))"; done
> ```
> Required: `marginalia` + `wiby` on 8890 (web), `wikidata` on 8889 (reference).
> If any are missing from the image, revert: remove the 3 new blocks from
> `searxng_settings.yml`, the tuple entries in `hyperion/infra/services.py`,
> the new names in `hyperion/tools/searxng.py` constants, then re-run
> `.venv/Scripts/python.exe -m hyperion.infra.searxng_profiles`.

## Phase 7 — Regression lock
- [ ] ⬜ P7.1 empty-report canary
- [ ] ⬜ P7.2 suspension-guard canary
- [ ] ⬜ P7.3 ledger-aware floor canary
- [ ] ⬜ P7.4 KPI diff: web domains > 0, sections > 0 per run

## Cross-cutting
- [ ] ⬜ verify all post-overhaul3 docker-log issues are covered (see overhaul4.md §4.1)
- [ ] ⬜ final `pytest` run + 1 live engagement
