# HYPERION FIX-0.3 RUNBOOK — Search Collapse · Agent Timeouts · Zero Findings

**Session under audit:** 2026-08-09 09:20–09:48 (TUI log) / 2026-08-09 14:50–15:00 (Docker host log)
**Branch policy:** cut `fix0.3` from `fix0.2`. **NEVER touch `main` or `fixo.1`.**
**Status:** AUDIT COMPLETE (session 2, 2026-08-09) — **no code was changed; this runbook is the fix plan for the next session.** New findings from the 3-replica fleet audit and the respawn-with-broadened-search requirement are folded into the items below as in-place adjustments (no new sections).
**Snapshot evidence:** this repo snapshot passes `100/100` of the search/agent/engine-health unit tests →
the failure is **environmental (live engines rate-limited) + stale deployment**, not mocked logic.

---

## 0. How to use this runbook (THE LOOP)

Every fix below is a **closed verification loop**. Do not apply fixes linearly like a checklist —
each item is a loop:

```
┌────────────────────────────────────────────────────────────────────────────┐
│  LOOP (per fix item F-0x):                                                  │
│                                                                            │
│  1. PROBE   — run the listed command / reproduce the symptom. Record the   │
│               BEFORE number (results, timeouts, engine states).            │
│  2. ROOT    — confirm the root cause in code (line numbers cited).         │
│  3. FIX     — apply the minimal change on branch fix0.3 (one commit/fix).  │
│  4. VERIFY  — run the unit/regression tests listed for that item.          │
│  5. LIVE    — run the live probe (real Docker stack) and record AFTER.     │
│  6. GATE    — AFTER meets the EXIT CRITERION → commit, move to next item.  │
│               AFTER misses → REVERT or re-FIX, loop again (max 3 passes).  │
│               If 3 passes fail → STOP, escalate the item to Phase N+1.     │
└────────────────────────────────────────────────────────────────────────────┘
```

Master loop (run after every engagement, and as a daily smoke):

```
PROBE (live stack) → measure 4 KPIs → each KPI above threshold? 
   YES → green, done. 
   NO  → enter the F-0x item whose GATE failed → fix → re-PROBE
```

The 4 KPIs that define "the pipeline is healthy again":

| KPI | Threshold | Where measured |
|---|---|---|
| Fleet live engines (ANY replica) | ≥ 2 engines returning results in total, incl. scholar/reference API engines | `_check_searxng` boot table + `FULL-POOL FAN-OUT` telemetry / `docker logs hyperion-searxng-{web,reference,scholar}` |
| Smoke probe results | ≥ 3 results on the web profile | boot health table `SEARCH` line |
| Sub-agent timeout rate | 0 timeouts in an engagement | TUI log `Sub-agent timed out` count |
| Distinct source domains at Quality Gate | ≥ 8 (corpus floor) | `CORPUS FLOOR` blocker / diagnostic JSON |

---

## 1. Incident timeline (what actually happened 09:20–09:48)

| TUI time | Event | Verdict |
|---|---|---|
| 09:20:53 | Boot `SEARCH ✓ scholar:ok@8888 · reference:ok@8889 · web:ok@8890` | The **boot smoke probe** just fired a real query at the web replica (`india import tariff` on `mojeek,mwmbl,brave,yep`) |
| 09:21:54 | docker log: `yep 403 (suspended_time=180)` + `brave 429 (suspended_time=180)` | **Smoke probe tripped web-profile engine bans 1 min before the engagement began** |
| 09:23:10 | REGULATORY: `0 regulations across 0 jurisdictions`; spawns 3 sub-agents | First wave sees the web pool already dead |
| 09:23:15 | COMPETE: `✗ Escalated: Zero competitors found in public sources` | Direct consequence of zero web results |
| 09:23:33–35 | docker log: `mwmbl timeouts`, `mojeek 403` | Entire web profile now dead (4/4 engines) |
| 09:23:10 → 09:28:10 | REGULATORY sub-agents **time out at exactly 300s** | Deployed code timeout = 300s, repo now defaults 600s → **stale deploy proven** |
| 09:25:52–09:32:34 | A few sub-agents return 1–3 findings (CONSUMER, MARKET, INNOVATE, TECH) | Sparse, from surviving scholar/reference engines + parametric model knowledge |
| 09:32:21 | `DIRECTOR: escalation evaluation cap reached (12); logging without LLM re-planning` | Sub-agent timeouts + gaps burned all 12 Director evaluations |
| 09:44:30–09:46:12 | SYNTHESIS runs; FACTCHECK: 46 evidence chain breaks, verification_rate=0.0 | Thin evidence propagates |
| 09:46:12 | QUALITY: `REJECTED 2.7/4.0 iteration 2/2` | Below threshold AND ship floor 3.0 |
| 09:47:49 | QUALITY: `BLOCKED — 4 integrity blockers` (CORPUS FLOOR 3<8, VERDICT CONTRADICTION, DISHONEST CONFIDENCE, META-TEXT 'parse error') | Run ends with no deliverable; diagnostic written to `reports/diagnostics/blocked_eng_*.json` |

**One-sentence root cause:** the web-search engine pool was dead before the engagement started
(smoke probe + fragile engine choices + persisted cooldowns), every fallback that could have rescued
it (Jina, Gemini grounding, retrieval escalation) was either unconfigured, silently failing, or not
present in the deployed copy, and the agent layer's bounded budgets (300s timeout, 3 sub-agents,
200-search cap, 12-escalation cap) converted that dead pool into timeouts, gap findings, and a
BLOCKED run.

---

## 2. The time mismatch — explained (not a bug, but must be fixed)

**Observed:** same instant stamped three ways —
Docker log prefix `2026-08-09 14:50:54` · container internal log `09 Aug 2026 09:20:54` · TUI `09:20:45`.

**Proof of what it is:**
- Host (WSL/Docker daemon) clock = **IST (UTC+5:30)** → 14:50.
- Containers run **UTC** → 09:20. Difference = exactly 5h30m.
- TUI prints the container clock (UTC), so TUI `09:20:45` matches valkey's internal `09:20:54`.
- Valkey RDB age `392577 s ≈ 4.55 days` = Aug 4 20:07 UTC → Aug 9 09:20 UTC. Consistent with UTC inside containers.

**This is a timezone display mismatch, not clock skew.** Epoch-based logic (valkey TTLs, engine
cooldowns in `engine_health.py`, budgets) is safe — only human log correlation is confusing.

**FIX (loop):**
1. PROBE: `docker exec hyperion-valkey date` vs `date` on the host. Record the delta.
2. FIX: add `TZ: "Asia/Kolkata"` (or UTC) to every service in `docker-compose.yml`
   (`valkey`, the 3 searxng replicas, `flaresolverr`) so container logs and host logs agree.
   Optionally document `TZ` in `.env`.
3. VERIFY: `docker compose config | grep -i tz` shows the value on every service.
4. LIVE: restart stack, confirm docker log prefix and container internal timestamps now match.
5. GATE: `docker logs hyperion-valkey | tail -3` timestamps within the same TZ as `date`.
   If the daemon itself must stay UTC, document "subtract 5h30m when correlating" in the ops
   README instead — the important thing is a **single documented rule**.

---

## 3. Root-cause chain (why zero findings → BLOCKED)

```
Web replica engines (mojeek, mwmbl, brave, yep) all 403/429/timeout
   ▲ (fragile engine choices + boot smoke probe burns their rate limits
      + persisted engine-health cooldowns from the previous session)
   │
SearxNGClient.search → zero results → rotation (standby=yep, also 403) → Jina fallback
   │                 (keyless free tier, rate-limited) → Gemini grounding
   │                 (REQUIRES GOOGLE_API_KEY + quota ledger — silently skips if absent)
   ▼
Sub-agent search legs return 0 URLs → extraction "no candidate URLs to render"
   ▼
Sub-agent LLM analyzes "No raw data available from tools." → 0 validated findings
   ▼
300s wall-clock consumed by multi-query fan-out on a dead pool → TimeoutError
   → base.py converts to ONE gap finding (no retry, no respawn)
   ▼
Specialists report "completed with 0 findings" / "Zero competitors" → escalate
   ▼
Director evaluation cap (12) exhausted → later escalations get NO re-planning
   ▼
Synthesis writes report from parametric knowledge → FACTCHECK 46 chain breaks
   → QUALITY 2.7/4.0 → corpus floor (3<8) + verdict/confidence/meta-text blockers → BLOCKED
```

---

## 4. Fix items (each is a loop — do NOT batch-apply)

### F-01 · Deploy the repo to WSL (the #1 suspect)

**Evidence:** deployed sub-agent timeout was 300s (09:23:10→09:28:10) while this snapshot has
600s in `config.py:810`, `schemas/agents.py:225`, and every specialist. `100/100` tests pass in the
snapshot. The audit itself (HYPERION_DEEP_AUDIT_2026-07-31.md:1695) already recorded this failure
mode: *"the pool … did not exist in the deployment, and the corpus floor then fires on the
resulting thin [evidence]"* — **fix0.2's fixes existed in the repo but not in the deployed copy.**

1. PROBE: `grep -n "timeout_seconds" /home/abuzar/Hyperion/hyperion/agents/specialists/competitive_intel.py`
   → if it says 300, the deployment is stale. Also compare `git -C /home/abuzar/Hyperion log --oneline -5`
   against the repo HEAD.
2. ROOT: WSL `/home/abuzar/Hyperion` is a different checkout than the code you iterate on.
3. FIX: on WSL — `git checkout fix0.3 && git pull origin fix0.3 && pip install -e . --no-deps`
   (or whatever your local install flow is). Reboot the shell so `provenance_strict` passes.
4. VERIFY: `grep -rn "timeout_seconds=600" hyperion/agents/specialists/ | wc -l` == number of
   specialist spec sites (12 specialists × 3 = 36).
5. LIVE: run one short engagement; confirm sub-agent timeout messages now say 600s if any fire.
6. GATE: deployed `sub_agent_timeout` matches repo. If WSL repo was already at 600s, skip this item.

### F-02 · Stop the boot smoke probe from killing the web pool

**Evidence:** docker log at 09:21:54 shows `yep 403` + `brave 429` — the exact engines the boot
probe sends (`obs/health.py:62 SMOKE_QUERY`, `_searxng_probe_targets` → web profile
`mojeek,mwmbl,brave,yep`). The engagement started at 09:21:49 with the web pool already banned.

1. PROBE: `docker logs hyperion-searxng-web --since 5m | grep -c "Too many request\|HTTP error 403"`
   right after a fresh boot.
2. ROOT: the probe is a **real** search (D-06 design) but it runs on the same IP as the engagement
   and immediately triples the most fragile engines.
3. FIX (choose one, in preference order):
   - **(a) Probe scholar + reference profiles only** (`crossref,openalex` + `wikipedia`), skip the
     web-profile smoke, OR
   - **(b) De-rate the web smoke** to a single cheap engine (`mwmbl` only) with `timeout: 6`, OR
   - **(c) Gate the probe behind `engine_health`**: if web engines are already cooling, probe
     returns DEGRADED without issuing traffic.
4. VERIFY: `python -m pytest tests/test_engagement_preflight.py tests/test_search_health_smoke.py -q`
5. LIVE: `docker logs hyperion-searxng-web --since 5m | grep -c "brave\|yep"` after boot → 0.
6. GATE: boot probe causes **no** new engine suspensions. Revert to (a) if (c) is too invasive.

### F-03 · Widen / harden the general-web engine pool AND use the whole 3-replica fleet (the "never finds competitors" fix)

**Replica-fleet audit (new finding — answers "are we properly utilising the three SearXNG instances?"):**

- **Wiring: yes, all three are live and reachable.** `SearxngPool.from_config()` (searxng.py:317-334)
  builds endpoints from `SEARXNG_REPLICAS` (services.py:157-176): scholar `8888`
  {arxiv, crossref, openalex, semantic scholar, pubmed} · reference `8889` {wikipedia, openstreetmap,
  github, stackexchange, hackernews} · web `8890` {mojeek, mwmbl, brave, yep}. Category routing:
  general/news → web, science/medical → scholar, it/geo → reference; a sequential zero-result walk
  (web → reference → scholar) already exists and is proven by `tests/test_w12_search_replicas.py`.
- **Utilisation: lopsided — this is the bug.** All 12 specialists and their sub-agents issue GENERAL
  queries, so ~100% of live traffic lands on the web replica. Scholar (documented APIs — crossref /
  openalex / semantic scholar) and reference only join via the sequential zero-result fallback, and
  even then only with a NARROW fallback engine set (reference → `{wikipedia}` only; scholar →
  `{crossref, openalex, semantic scholar}`). In the Aug 9 session the web replica's 4 engines were
  all 403/429/timeout → zero findings, while scholar's API engines — which do NOT ban datacenter IPs
  the way the crawlers do — were never asked for the general query.
- **Production-grade assessment: mostly yes, with 5 gaps.** Hardened: pinned image
  `searxng/searxng:2026.7.19-6da6eee26`, `mem_limit 512m`, `cpus 2.0`, `cap_drop: [ALL]`,
  `read_only`, `tmpfs`, `no-new-privileges`, loopback-only ports, healthchecks with X-Forwarded-For,
  json-file log rotation, per-replica volumes, disjoint engine sets (W-12), valkey-shared engine
  token buckets, engine-health cooldowns + circuit breakers. Gaps: (1) `server.limiter: false` —
  `searxng-limiter.toml` is mounted but the limiter is disabled; (2) no `TZ` (see §2); (3) engine
  timeouts 6–10s vs flaky upstreams; (4) **no cross-replica fan-out → 2 of 3 replicas idle during
  general research**; (5) no outbound proxy → the datacenter IP is the banning trigger.

**Evidence:** the W-11 policy restricts the web profile to `mojeek, mwmbl, brave, yep` (searxng.py:79-86,
searxng_settings.web.yml) and ALL FOUR failed in the observed session. `competitive_intel.py:359-395`
plans queries and calls `searxng.search(...)`; with a dead pool it escalates "Zero competitors" —
**competitor detection is 100% downstream of this pool.**

1. PROBE: `curl -s "http://127.0.0.1:8890/search?q=India+AI+startups&format=json&engines=mojeek,mwmbl,brave,yep" | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['results']), d.get('unresponsive_engines'))"`
   → expect 0 results + all four unresponsive. Then prove the idle replica: `curl -s "http://127.0.0.1:8888/search?q=India+AI+startups&format=json&engines=crossref,openalex,semantic%20scholar" | python -c "import json,sys; d=json.load(sys.stdin); print('scholar results:', len(d['results']))"` — expected to STILL return results (the engines that were never asked).
2. ROOT: crawler engines (mojeek/yep) 403, brave HTML endpoint 429s datacenter IPs, mwmbl is
   slow/flaky — and the architecture never routes general queries to the API engines that survive.
   Tier C (bing/ddg/google/startpage) stays **forbidden by W-11 policy** — the fix must stay
   Tier-A/B compliant.
3. FIX (do these in order; each is independently valuable):
   - **(a) FULL-POOL FAN-OUT (primary fix — "use all available engines smartly").** When the
     preferred profile + standby rotation yield zero results, fire ONE parallel request per replica
     with that replica's FULL set of currently-healthy engines and merge + dedup. Design:
     `SearxngPool.healthy_engines() -> dict[profile, set[engine]]` (respects engine-health
     cooldowns, never sends cooled engines) + `SearxngClient._search_all_replicas()` (parallel
     gather, explicit engines bound to their owning replica, per-replica failure logged not fatal),
     wired into `search()` between rotation and the Jina fallback, guarded by `not explicit_engines`.
     Scholar's crossref/openalex/semantic scholar and reference's wikipedia/hackernews then act as a
     real safety net for business queries; W-11 isolation stays intact because explicit engines are
     bound to the replica that owns them.
   - **(b) Add Brave Search API as a Tier-A API engine** (documented API, `api.search.brave.com`,
     needs `BRAVE_SEARCH_API_KEY` env → `searxng_settings.web.yml` engine entry `brave.api`). The
     single highest-leverage addition: a paid API replaces the 429-prone HTML scrape.
   - **(c) Add a residential/rotating outbound proxy for the searxng replicas** (`outgoing.proxies`
     in settings + `HTTPS_PROXY`), so the datacenter IP stops being the banning trigger.
   - **(d) Add `startpage`/`qwant`/`duckduckgo` only if you are willing to amend the W-11 Tier-C
     policy** (document the policy change in ARCHITECTURE.md §5.2 — do not silently violate it).
   - **(e) Raise engine `timeout` values above the 6-10s floor for slow engines, and set
     `max_request_timeout` ≥ 20s** so mwmbl has room to answer.
4. VERIFY: `python -m pytest tests/test_w11_search_registry.py tests/test_w12_search_replicas.py tests/test_search_health_smoke.py tests/test_router_failure_attribution.py -q`
   (+ add tests: fan-out dispatches to every replica with its own healthy engine set, dead engines
   excluded; a dead web profile still recovers evidence from scholar via the fan-out).
5. LIVE: repeat the PROBE curl on all three ports — ≥ 2 engines returning results across the
   FLEET (web OR reference OR scholar), and the log shows `FULL-POOL FAN-OUT` only when the primary
   profile failed.
6. GATE: fleet live engines ≥ 2 across 3 consecutive probes 60s apart, with scholar/reference
   demonstrably serving general queries via the fan-out. If (b) is blocked on a key, land
   (a)+(c)+(e) first and keep the item open.

### F-04 · Engine-health cooldowns: don't let last session's bans poison this one

**Evidence:** `engine_health.py` persists cooldowns to `vault/engine_health.json` with up to **24h**
suspensions (`suspended_time=180` → 180s, but a 403+captcha → 24h). The Aug 4 session's bans can
therefore still be active on Aug 9.

1. PROBE: `cat vault/engine_health.json` — count engines with a future `suspended`/`cooldowns` epoch.
2. ROOT: `EngineHealthTracker._load()` restores state at process start; nothing ages it out at boot.
3. FIX:
   - **(a) Add a boot-time TTL sweep**: any cooldown with `until < now` is dropped on load (the
     `state()` method already lazily pops expired entries — call it for every known engine at boot).
   - **(b) Add an operator command**: `hyperion health --reset-engine-state` (or
     `rm vault/engine_health.json`) documented in README.
   - **(c) Cap the max suspension** at 4h (change `_MAX_COOLDOWN_SECONDS`) so a single bad session
     can't waste a whole day of capacity.
4. VERIFY: `python -m pytest tests/test_engine_health.py -q`
5. LIVE: boot, then immediately `docker logs hyperion-searxng-web --since 3m | grep -c 403` — no
   new bans, and `vault/engine_health.json` shows only healthy engines.
6. GATE: 0 pre-existing suspensions at boot.

### F-05 · Search budget cap: 200 process-global searches exhausts mid-engagement

**Evidence:** `searxng.py:452 SEARCH_BUDGET_CAP = 200`; once hit, **every later search returns an
empty response for the rest of the engagement** (searxng.py:1070-1075). With 12 specialists × up to
3 sub-agents × up to 7 queries/leg, the cap is reached long before M&A / STRATEGY / SYNTHESIS run —
exactly the "total collected: N" plateau seen in the log.

1. PROBE: in the TUI log, count `SearxNG search` accesses before the first "no results" cascade,
   or add a debug print of `SearxNGClient.get_search_count()` at engagement end.
2. ROOT: the budget is class-global and does not distinguish cached vs. fresh, nor per-specialist.
3. FIX:
   - **(a) Raise the cap** to ≥ 600 (200 is a guess from an era with 1 query per sub-agent; the
     planner now emits up to 7 per leg).
   - **(b) Make exhaustion fail loud**: when the cap is hit, publish a TUI/telemetry event
     `SEARCH BUDGET EXHAUSTED (200/200)` and surface it in the completion health table
     (`obs/health.py print_completion_health`) — currently ONE warning log line is all there is.
   - **(c) Count per-specialist** instead of globally (reset per agent at spawn) so one heavy
     specialist can't starve the rest.
4. VERIFY: `python -m pytest tests/test_router_w17_attempt_budget.py tests/test_search_health_smoke.py -q`
5. LIVE: full engagement; completion table shows the search count and NO silent "budget" empties
   before the last specialist.
6. GATE: last-running specialist still gets fresh (non-cached, non-empty) searches.

### F-06 · Sub-agent timeout: budget search, not wall-clock everything

**Evidence:** REGULATORY sub-agents spawned 09:23:10, timed out 09:28:10 (300s deployed). Each
sub-agent can legally spend: search fan-out (up to 7 queries × per-query SearxNG 3-attempt + Jina +
grounding) **plus** extraction (up to 10 URLs × ladder) **plus** an LLM call (up to 60s). On a dead
pool the search phase alone can exceed the budget before the LLM ever runs.

1. PROBE: `grep "Sub-agent timed out" <tui log>` count; correlate with engine dead time.
2. ROOT: `base.py:1015` `asyncio.wait_for(runner.run(), timeout=spec.timeout_seconds)` wraps the
   **entire** lifecycle; there is no per-phase budget and no fail-fast when the pool is dead.
3. FIX:
   - **(a) Confirm deploy is at 600s** (F-01) — do not raise it further without F-06b.
   - **(b) Fail fast on a dead pool**: in `SearxNGClient.search`, if `get_engine_health().healthy_count(referenced_engines()) < 2` BEFORE issuing queries, return the empty response immediately with `retrieval_degraded=True` (saves 45s × 7 queries of timeout). Also short-circuit `_search_searxng_json` when the endpoint's `engines_for()` set is empty after health filtering (searxng.py already does this for the explicit case — extend to the implicit case).
   - **(c) Give search its own budget**: wrap `_gather_raw_data` in `asyncio.wait_for(..., timeout=min(spec.timeout_seconds * 0.7, ...))` and the analysis call in the remainder, so a stuck search can't eat the LLM's time. (The F-07 broadened respawn then gets `max(60, timeout // 2)` of budget for its second pass.)
4. VERIFY: `python -m pytest tests/test_sub_agent_query.py tests/test_yield_zero_evidence.py tests/test_w18_persistent_budget.py -q`
5. LIVE: run the AI-India question; `Sub-agent timed out` count == 0.
6. GATE: 0 timeouts, and sub-agent LLM calls always have ≥ 60s of their budget left.

### F-07 · Respawn with a BROADENED search on timeout / zero findings (the "no respawning" fix)

**New finding / requirement (session 2):** every sub-agent that times out or returns zero findings
must be respawned ONCE with a broadened search around the MAIN question — not recorded as a
terminal gap.

**Evidence:** `base.py:1015-1033` — TimeoutError → one `gap_finding`, done; the synthetic
"no validated findings" gap from `runner.run()` is also recorded as-is. A sub-agent whose only sin
was infra latency contributes a gap to the report, and the parent's `max_sub_agents=3` budget is
already spent.

1. PROBE: TUI log shows `Sub-agent returned 0 findings` + `timed out` with **no** retry line.
2. ROOT: the spawn boundary treats timeout/zero as terminal instead of retryable; there is no
   "broaden and try again around the main query" step anywhere in the sub-agent lifecycle.
3. FIX (bounded retry — exactly one respawn per question, never unbounded):
   - **(a) `SubAgentSpec.broadened: bool`** (default False).
   - **(b) `BaseAgent._respawn_broadened(spec, reason=timeout|zero_findings)`** — triggered on
     (i) TimeoutError with a production budget (`spec.timeout_seconds >= 300`; unit-test / stress
     configs stay deterministic) or (ii) a single `research_gap` containing "no validated findings"
     (the runner's own synthetic gap). NOT triggered on generic exceptions (a code bug must not be
     retried) or on an already-broadened spec.
   - **(c) Broadened mode = faster AND wider**: search legs drop the geography anchor on the primary
     pass (whole-corpus breadth around the main question); the LLM query planner is skipped
     (deterministic `_condense_query_variants` only); extraction capped at 3 URLs; respawn timeout =
     `max(60, spec.timeout_seconds // 2)` because it runs AFTER a full primary pass.
   - **(d) Logging**: `SUB-AGENT RESPAWN (broadened, reason=timeout|zero_findings): <question>`, and
     on a second failure `Sub-agent respawn timed out/failed` — the retry path is fully visible.
   - **(e) Accounting**: the respawn is a retry of the same logical sub-agent; it is tracked in
     `_sub_agent_respawned` (per question) so it can never loop, and it is allowed even when the
     `max_sub_agents` budget is exhausted.
4. VERIFY: `python -m pytest tests/test_sub_agent_query.py -q` (+ add tests: timeout → one broadened
   respawn, assert `spec.broadened is True` and the respawn budget is halved; zero-findings gap → one
   broadened respawn; respawn failing again → final gap, no loop; generic exception → NO respawn).
5. LIVE: force a timeout via a fake slow provider in a test engagement; confirm exactly one respawn
   per question, then a gap only after the respawn also fails.
6. GATE: every timeout/zero result is followed by exactly one broadened respawn before a gap is
   recorded; `grep -c "SUB-AGENT RESPAWN" <log>` == number of failed sub-agent questions (never more).

### F-08 · max_sub_agents=3: budget spent on noise

**Evidence:** README:171, config.py:811 — 3 sub-agents/specialist. In the observed run the 3 slots
were consumed by sub-agents that timed out on a dead pool ("SUB-AGENT budget reached (3/3)").

1. PROBE: TUI log `SUB-AGENT budget reached` occurrences.
2. ROOT: fixed 3-slot budget, no relationship to actual yield.
3. FIX:
   - **(a)** Make the cap **yield-aware**: count a slot as spent only when the sub-agent returned
     ≥1 non-gap finding; a timeout/zero result releases the slot (works naturally once F-07 retry
     exists).
   - **(b)** Keep the absolute ceiling at 3 **concurrent** sub-agents (resource bound), but allow
     **sequential re-fills** up to a per-specialist total of 6.
4. VERIFY: `python -m pytest tests/test_w06_subject_roster.py tests/test_agents.py -q`
5. LIVE: REGULATORY run shows "budget reached (2/6) after 1 timeout released".
6. GATE: no specialist prints "budget reached (3/3)" while sub-agents are still producing nothing.

### F-09 · Gemini "websearch" (grounding): verify it is actually reachable

**Evidence:** `grounded_search.py` exists and is wired at `searxng.py:890` (final fallback,
`RETRY_EXHAUSTED`, ungated by engine health), `fact_checker.py:743`, `orchestrator.py:1417`. **But**
it is gated by: `google_grounding_enabled` (config.py:775, default True), `GOOGLE_API_KEY`
(config.py:769 → `providers[GOOGLE].api_key`), and a quota ledger
(`vault/grounding_quota.json`, daily 20 / monthly 600). **If the key is missing, the fallback is a
silent no-op** — "Google grounding credential unavailable" is only a log line, and the search
returns empty. This is very likely why "we tried to make it search use websearch" changed nothing.

1. PROBE: `grep -c GOOGLE_API_KEY .env 2>/dev/null; ls -la vault/grounding_quota.json 2>/dev/null`
   and run `python -c "from hyperion.config import get_settings; s=get_settings(); print(bool(s.google_api_key), s.google_grounding_enabled)"`.
2. ROOT: no key → `GroundedSearchClient.search` appends a constraint and returns empty; also the
   **quota ledger uses `reserve_fraction=0.10` and daily=20** — a few failed calls can eat it.
3. FIX:
   - **(a)** Ensure `GOOGLE_API_KEY` is set in `.env` (a Google AI Studio key with Gemini API +
     Search grounding enabled). Re-run the PROBE → `True`.
   - **(b)** Make grounding failure **visible**: `_search_grounded_fallback` should publish a
     TUI/telemetry event with the constraint string (`grounding unavailable: <reason>`) instead of
     only a logger.warning — silent empty results are how this bug hid.
   - **(c)** Confirm the ledger path exists (`vault/grounding_quota.json`) and raise the daily limit
     if you expect > 20 grounding rescues/day.
4. VERIFY: `python -m pytest tests/test_w14_grounded_search.py tests/test_search_grounding.py -q`
5. LIVE: `python - <<'EOF'` calling `GroundedSearchClient().search("India AI market size 2025", reason=GroundingReason.RETRY_EXHAUSTED)` with the stack up → results non-empty.
6. GATE: with SearXNG deliberately degraded, grounding returns ≥ 3 results and the event log shows
   the rescue. If no key is available, document grounding as DISABLED and rely on F-03 instead.

### F-10 · Wire thin-evidence retrieval escalation BEFORE the gate blocks

**Evidence:** `orchestrator.py:1949-1950` calls `_handle_thin_evidence(report, source_floor)` in the
quality iteration loop, and `_escalate_retrieval` (orchestrator.py:1633) fires reformulated queries.
The Aug 9 log shows **no** retrieval-escalation activity before BLOCKED → the deployed copy predates
this wiring, or the escalation recovered 0 because the pool was dead (F-03/F-05 fix that part).

1. PROBE: grep the pasted log for `RETRIEVAL ESCALATION` — absent.
2. ROOT: the BLOCKED path (orchestrator.py:2676) writes a diagnostic + escalates to the Director —
   and the Director had already hit the 12-evaluation cap (F-12). Deterministic retrieval
   escalation must not depend on Director LLM re-planning.
3. FIX:
   - **(a)** Ensure `_handle_thin_evidence` is invoked for the **corpus-floor blocker path**
     specifically (source_floor = 8 when the blocker fired), not only the generic `source_floor`.
   - **(b)** Make `_escalate_retrieval` fail loud: log `RETRIEVAL ESCALATION: recovered N` or
     `...recovered 0 — reason: <top constraint>`.
   - **(c)** Add a regression test: quality gate with `CORPUS FLOOR` blocker → `_handle_thin_evidence`
     is called with floor ≥ 8 before terminal state is computed.
4. VERIFY: `python -m pytest tests/output/test_phase7_corpus.py -q`
5. LIVE: run the AI-India question on a healthy pool; confirm `RETRIEVAL ESCALATION` lines appear
   only if the floor is breached, and the run never blocks without one attempted escalation.
6. GATE: no BLOCKED run without a prior, logged retrieval-escalation attempt.

### F-11 · Quality blockers 2-4: fix at source, not at the gate

**Evidence (from the 09:47:49 blocker list):**
- **VERDICT CONTRADICTION** — recommendation `CONDITIONAL` but narrative says `no-go`.
- **DISHONEST CONFIDENCE** — `HIGH` confidence with 8/11 unsourced sections and 4 total sources.
- **META-TEXT** — `'parse error'` reached the client deliverable. Source: `market_analyst.py:663/759/885/891`,
  `financial_analyst.py:728/822/1073`, `technology_analyst.py:840-849` all set
  `value="Parse error"` when an LLM parse fails; the blocklist in `quality_gate.py:1199`,
  `meta_text.py:36`, `page_audit.py:80` only detects it after the fact.

1. PROBE: grep the diagnostic JSON `reports/diagnostics/blocked_eng_*.json` for the three blocker
   texts; confirm which fields carried them.
2. ROOT:
   - "Parse error": specialists `except` the parse and persist the literal string. Fix at the
     ~15 sites — on parse failure, **retry once at the tier above, and if still failing, omit the
     metric** (null) rather than emitting the string.
   - Verdict/confidence: synthesis_lead derives recommendation + confidence from findings; with a
     thin corpus the LLM waffles. Confidence must be computed from **measured evidence coverage**
     (sourced sections / total sections), not asserted by the model.
3. FIX:
   - **(a)** Replace all `value="Parse error"` sites with a retry-and-omit helper
     (`_parse_or_none(...)`) shared across the three specialists.
   - **(b)** Add a meta-text sanitizer at the **render boundary** (already exists in
     `output/meta_text.py`) — keep it, but now it should never fire because the value is never
     generated.
   - **(c)** In synthesis_lead, downgrade `confidence` to MEDIUM/LOW automatically when
     `sourced_sections / total_sections < 0.5`, and reject verdict language that contradicts the
     `recommendation` enum in the same pass that writes the summary.
4. VERIFY: `python -m pytest tests/output/test_section_producing_agents.py tests/output/test_iteration_meta_text.py tests/output/test_display.py tests/output/test_page_audit.py tests/output/test_phase7_corpus.py -q`
5. LIVE: re-run; QUALITY blockers list contains only genuine integrity issues, never "Parse error".
6. GATE: `grep -r "Parse error" reports/` → 0 hits in any rendered deliverable.

### F-12 · Director escalation cap 12: deterministic actions must bypass the LLM cap

**Evidence:** `engagement_director.py:488 _max_escalation_evaluations = 12`; at 09:32:21 and again at
09:47:49 every escalation (sub-agent timeouts, quality BLOCKED) was met with "cap reached (12);
logging without LLM re-planning".

1. PROBE: count `escalation evaluation cap reached` in the TUI log.
2. ROOT: the cap exists to stop STRONG-tier LLM storm (correct), but it also suppresses
   **deterministic** recovery (retrieval escalation is an HTTP search, not an LLM call).
3. FIX:
   - **(a)** Separate the two concerns: cap only `_evaluate_escalation` (LLM). Deterministic
     handlers — `_escalate_retrieval`, sub-agent retry (F-07), engine rotation — run regardless.
   - **(b)** Raise the cap to 20 or make it proportional to the DAG task count (cap =
     `max(12, 2 × len(dag.tasks))`).
4. VERIFY: `python -m pytest tests/test_director_nullable_dag.py tests/test_agents.py tests/test_w06_subject_roster.py -q`
5. LIVE: force >12 escalations in a test engagement; confirm the 13th+ still triggers the
   deterministic retrieval path while LLM evaluations stay capped.
6. GATE: `escalation evaluation cap reached` may appear, but never in the same log line as
   "Quality Gate BLOCKED" without a preceding retrieval-escalation attempt.

### F-13 · (Cosmetic but real) TUI timezone: print local time consistently

1. PROBE: note TUI `09:20:45` vs your wall clock (`date`).
2. FIX: `hyperion/tui` timestamps → format with the same TZ rule as docker-compose (F-02/F-2).
3. GATE: TUI, container logs, and host logs correlate without mental arithmetic.

---

## 5. Verification suite (run these after every fix, and on CI for fix0.3)

```bash
# Unit / regression (fast, mocked — proves code, not environment)
python -m pytest tests/test_engine_health.py \
                 tests/test_search_health_smoke.py \
                 tests/test_sub_agent_query.py \
                 tests/test_yield_zero_evidence.py \
                 tests/test_engagement_preflight.py \
                 tests/test_w11_search_registry.py \
                 tests/test_w12_search_replicas.py \
                 tests/test_w14_grounded_search.py \
                 tests/test_search_grounding.py \
                 tests/test_w17_router_attempt_budget.py \
                 tests/output/test_phase7_corpus.py \
                 tests/output/test_iteration_meta_text.py \
                 tests/output/test_section_producing_agents.py \
                 tests/test_router_failure_attribution.py -q
# Baseline today: 100 passed (subset). All must stay green.

# Live stack probes (prove the environment)
docker compose up -d --wait
curl -s "http://127.0.0.1:8890/search?q=India+AI+startups+2025&format=json" \
  | python -c "import json,sys; d=json.load(sys.stdin); print('results:', len(d['results'])); print('dead:', d.get('unresponsive_engines'))"
curl -s "http://127.0.0.1:8888/search?q=India+AI&format=json&engines=crossref,openalex" \
  | python -c "import json,sys; d=json.load(sys.stdin); print('scholar results:', len(d['results']))"
cat vault/engine_health.json | python -m json.tool | head -20
ls -la vault/grounding_quota.json
```

---

## 6. Master loop — keep it fixed (run after every engagement)

```
after_each_engagement:
  KPI1 = web live engines (from TUI/completion table)          → must be ≥ 2
  KPI2 = smoke probe results at next boot                      → must be ≥ 3
  KPI3 = sub-agent timeouts this engagement                    → must be 0
  KPI4 = distinct source domains in the diagnostic JSON        → must be ≥ 8
  if all pass: record green in reports/.gitkeep ops log; done
  else: open the failing KPI's F-0x item, run its loop (PROBE→FIX→VERIFY→LIVE→GATE),
        then re-run this master loop.
```

Weekly: re-run §5 verification suite + one full AI-India engagement on the live stack.
Any regression in KPI1-4 → the corresponding F-0x loop, not a band-aid at the gate.

---

## 7. Definition of Done for fix0.3

- [ ] F-01: deployed WSL code == repo HEAD (proven by 600s timeouts present at runtime).
- [ ] F-02: boot probe causes zero new engine suspensions (docker log check).
- [ ] F-03: the FLEET (web + reference + scholar) serves ≥ 2 live engines on 3 consecutive probes, with scholar/reference participating in general queries via the full-pool fan-out.
- [ ] F-04: `vault/engine_health.json` has no pre-existing suspensions at boot.
- [ ] F-05: search budget exhaustion is loud and per-specialist; no mid-engagement silent empties.
- [ ] F-06: 0 sub-agent timeouts on the AI-India engagement; LLM always gets ≥ 60s budget.
- [ ] F-07: every timeout/zero result gets exactly one BROADENED respawn (logged `SUB-AGENT RESPAWN`), with a gap recorded only after the respawn also fails.
- [ ] F-08: no "budget reached (3/3)" while sub-agents yield nothing.
- [ ] F-09: grounding reachable (key set) and its failure mode is visible in telemetry.
- [ ] F-10: no BLOCKED run without a logged retrieval-escalation attempt.
- [ ] F-11: `grep -r "Parse error" reports/` = 0; confidence tracks evidence coverage.
- [ ] F-12: deterministic recovery paths run past the 12-escalation LLM cap.
- [ ] F-13: timestamps correlate across TUI/container/host with one documented rule.
- [ ] §5 suite green; one full AI-India engagement completes with ≥ 8 distinct source domains
      and no integrity blockers.

---

## 8. Anti-regression — mistakes from the fix0.2 era we must not repeat

| Mistake (seen in audits 07-27/07-30/07-31 + this session) | Guard in fix0.3 |
|---|---|
| Fixes committed to the repo but **never deployed to WSL** (300s vs 600s timeouts; missing retrieval escalation; the audit's own "did not exist in the deployment" note) | F-01 deploy-sync gate + provenance_strict boot check; DoD item 1 |
| **Silent failures**: `except: pass` / one-log-line empties hide total outages (P0 B-1 in 07-27; budget-cap one-liner; grounding "unavailable" as a log line) | F-05b, F-06b, F-09b, F-10b — every empty path publishes visible telemetry |
| **Engine pool too narrow / wrong corpus** (arxiv+github for business queries in 07-30; 4 fragile engines today) | F-03 pool hardening + W-11 policy amendment path documented |
| **Lopsided fleet utilisation** — general queries hammered the web replica while scholar/reference API engines sat idle (Aug 9: web 4/4 dead, scholar never asked) | F-03a full-pool fan-out — every replica's healthy engines participate before any off-box fallback |
| **Bounded budgets used as terminal states**: sub-agent 3-slot budget, 200-search cap, 12-escalation cap all converted a dead pool into "no findings" | F-05, F-07, F-08, F-12 — budgets release/retry/rotate; only genuine exhaustion is terminal |
| **Internal QA vocabulary leaking to the client** ("Parse error", "iteration", "quality gate") | F-11a — kill at source; meta_text blocklist kept as tripwire |
| **Thin evidence → more synthesis instead of more retrieval** | F-10 — retrieval escalation wired before BLOCKED |
| **Rate-limit bans treated as per-request noise** (DuckDuckGo 24h CAPTCHA ban in 07-30) | F-04 engine-health persistence + cooldown TTL sweep |
| **Fixing the gate instead of the pipeline** (raising thresholds instead of fixing retrieval) | §7 DoD — all items are pipeline-level; ship floor 3.0 stays |

---

## Appendix A — key code sites referenced

| Concern | File:line |
|---|---|
| Sub-agent timeout default | `hyperion/config.py:810` (600), `hyperion/schemas/agents.py:225` (600) |
| Sub-agent spawn / timeout → gap (PLAN: `BaseAgent._respawn_broadened` + `SubAgentSpec.broadened` for F-07) | `hyperion/agents/base.py:977-1033`, `hyperion/schemas/agents.py:196-231` |
| Sub-agent search legs + query plan | `hyperion/agents/sub_agent.py:534-905` |
| Engine pool / registry / budget (PLAN: `SearxngPool.healthy_engines` + `SearxngClient._search_all_replicas` full-pool fan-out for F-03) | `hyperion/tools/searxng.py:79-86, 317-334, 452, 890-925, 1070-1075` |
| Engine health persistence | `hyperion/tools/engine_health.py:52-78, 205-232` |
| Gemini grounding gating | `hyperion/tools/grounded_search.py:96-130`, `hyperion/config.py:769-783` |
| Boot smoke probe | `hyperion/obs/health.py:62, 105-160` |
| Director escalation cap | `hyperion/agents/engagement_director.py:488, 558-565` |
| Quality corpus floor (8) | `hyperion/agents/support/quality_gate.py:1206-1228` |
| Retrieval escalation wiring | `hyperion/orchestrator.py:1600-1631, 1633-1700, 1944-1954, 2676-2698` |
| "Parse error" emission sites | `market_analyst.py:663,759,885,891` · `financial_analyst.py:728,822,1073` · `technology_analyst.py:840-849` |
| SearXNG replica profiles | `hyperion/infra/services.py:157-176`, `searxng_settings.{web,reference,scholar}.yml` |
| Prior audits (fix0.2 record) | `HYPERION_DEEP_AUDIT_2026-07-27.md` (B-1..B-6), `HYPERION_DEEP_AUDIT_2026-07-30.md` (D-13/D-19), `HYPERION_DEEP_AUDIT_2026-07-31.md:1695` (deploy-drift), `docs/HYPERION_MASTER_REMEDIATION_PLAN.md` |
