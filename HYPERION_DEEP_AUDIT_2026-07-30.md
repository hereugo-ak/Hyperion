# HYPERION — DEEP SYSTEM AUDIT
**Date:** 2026-07-30
**Branch:** `fix0.1`
**Session audited:** `0x5EDA0B` — engagement `eng_1cf32bfa6cc4` — question *"should india import less ?"*
**Auditor scope:** full stack — search → extraction → specialists → synthesis → quality → delivery → PDF
**Predecessor:** `HYPERION_DEEP_AUDIT_2026-07-27.md` (fixes from that document are **in** this branch and did **not** resolve the reported symptoms)

---

## 0. TL;DR — What actually happened

The 2026-07-27 audit fixed real bugs. None of them were **the** bug.

The engagement ran for 1,216 seconds, made 95 LLM calls across 17 tasks and 20 agents, and shipped a
deliverable with **zero analysis chapters**, **zero charts**, **zero section images**, **no PDF**, and an
executive summary about *"a $2B TAM at 12% penetration with three dominant players holding 70% market
share"* — a market that does not exist, in a report that was supposed to be about Indian import policy.

That output was not a rendering problem, a CSS problem, or a prompt-quality problem. It was **one
type error** that cascaded through a **fail-open quality loop** that is architecturally incapable of
detecting its own emptiness, and which then **overwrote the system's own honest failure notice** with
fabricated prose copied out of a few-shot example in its own prompt.

Five independent single points of failure all fired in the same run:

| # | Failure | Effect on deliverable |
|---|---------|----------------------|
| 1 | `AttributeError: 'VaultSearchResult' object has no attribute 'strip'` | **All analysis chapters deleted** (0 sections) |
| 2 | Quality-iteration LLM rewrote prose over an empty report | **Fabricated content presented as findings** |
| 3 | `playwright` absent from `pyproject.toml` | **No PDF at all** → HTML fallback → "looks like a website" |
| 4 | Both SearXNG engines dead (CAPTCHA + 24h 403 ban) | **0 sources extracted, 0 chars retained** |
| 5 | `AttributeError: 'DataVisualizer' object has no attribute '_logger'` | **0 charts** |

And three systemic reasons the system *could not tell you* any of that:

| # | Blindness | Effect |
|---|-----------|--------|
| 6 | Health check is a TCP port probe | Reported `✓ SearxNG ready · 13 data sources ready` while every engine was banned |
| 7 | Escalation payload schema mismatch | Director silently discarded **every** escalation as `unknown: Unknown issue` |
| 8 | Quality Gate cannot fail closed | Scored 2.2/5.0, hit max iterations, **shipped anyway** |

**The system behaved "like a wrapper" because in this run it *was* one.** With 0 bytes of retrieved
evidence, every specialist degraded to an unconstrained LLM call, and the one component whose job is
to notice that (the Quality Gate) is wired to warn and proceed rather than to block.

---

## 1. Evidence base

Everything in this document is traced to a primary artifact. Nothing is inferred from vibes.

| Artifact | What it proved |
|---|---|
| `should_india_import_less_FALLBACK.html` (6,684,746 B) | Deliverable structure; 0 chapters; content of the fabricated summary |
| PowerShell session log (17 screenshots, `13:55:33`→`14:16:31`) | Exact crash strings, timings, duplicate task execution, yield metrics |
| Docker logs (`searxng`, `flaresolverr`) | DuckDuckGo CAPTCHA storm → `HTTP 403 suspended_time=86400` |
| Windows Security notification | `obscura.exe` blocked — unsigned binary, publisher unverifiable |
| `should_india_reduce_its_dependence_on_the_imports.css` | Pre-fix CSS artifact with literal `{{page}}` (older run — confirms fix landed later) |
| Live source tree on `fix0.1` | All root causes located and confirmed by reading and executing the code |

### 1.1 Deliverable anatomy — the 6.6 MB is a lie

```
total file                   6,684,746 bytes
  ├─ <style> block           2,273,412 bytes   (34%)  ← 7 base64 @font-face payloads
  ├─ base64 cover image      4,399,000 bytes   (66%)  ← ONE Unsplash JPEG
  └─ actual HTML content        11,642 bytes   (0.17%) ← the entire "report"
```

**11.6 KB of content.** Of which the real analysis is ~4 KB. The file is 99.8% binary payload.
A genuine MBB-grade 20-page report is 60,000–90,000 characters of prose. This shipped 1.7% of that.

### 1.2 Confirmed from the log, verbatim

```
[14:10:29] WARN  SYNTHESIS: Synthesis failed: 'VaultSearchResult' object has no attribute 'strip'
[14:16:30] TOOL  DESIGNER system.log.RENDER: PDF unavailable (PDF generation failed
                 (WeasyPrint: cannot load library 'libgobject-2.0-0': error 0x7e.
                 Additionally, ctypes.util.f; Pla); delivering HTML fallback
[14:16:30] WARN  VISUAL: Delivery agent failed: 'DataVisualizer' object has no attribute '_logger'
[14:16:30] TOOL  EXTRACTION YIELD: 0/0 URLs (0%) extracted, 0 chars retained across
                 0 cited sources (avg 0 chars/source, 0 search calls)
[14:16:18] QUALITY  Quality Gate: REJECTED (score 2.2/5.0, iteration 2) — max iterations reached
[14:16:18] DESIGNER Layout plan complete: 7 pages with 0 images and 0 charts
[14:10:09] FACTCHECK CRITICAL: 4 Hallucinated Citations Detected
[14:10:06] TOOL  DIRECTOR: skipping duplicate escalation from unknown: Unknown issue   (×7)
```

Note `0 search calls` in the yield metric while the Docker log shows dozens of outbound SearXNG
queries in the same window. **The yield metric is instrumented on a code path that never executed** —
a second, independent observability defect (§2.9).

---

## 2. Defect register

Severity: **S1** = destroys the deliverable · **S2** = destroys a major feature · **S3** = quality/cost/trust · **S4** = hygiene

---

### D-01 · S1 · Synthesis type error deletes every analysis chapter
**This is the bug. Everything else is either a co-conspirator or a symptom.**

`hyperion/agents/synthesis_lead.py:1391`

```python
async def _query_second_brain_for_patterns(self, question: str) -> str:   # ← promises str
    try:
        brain = self.get_tool(ToolName.SECOND_BRAIN)
        results = await brain.search(f"synthesis patterns: {question}")
        return results if results else ""        # ← returns VaultSearchResult (a @dataclass)
    except (ValueError, AttributeError, RuntimeError):
        return ""
```

The sole consumer, `_identify_and_draft()` at line 1480:

```python
patterns_block = (... f"{prior_patterns}\n\n" if prior_patterns.strip() else "")
                                                #  ^^^^^^^^^^^^^^^^^^^^ AttributeError
```

**Why the local `except` cannot save it:** the raise happens at the *call site*, in a different
method. The handler here is decorative.

**Why the blast radius is the whole report — not one prompt block.** `_run_synthesis()` is strictly
sequential and the report body is assembled **last**:

```
step 5+6  _identify_and_draft()          ← RAISES HERE
step 7    _calibrate_confidence()          never runs
step 8    _build_analysis_sections()       never runs   ← THE ENTIRE REPORT BODY
step 8    FinalReport(...)                 never runs
```

`run()` catches it and returns `_minimal_report()` (line 1564), which hardcodes **`sections=[]`**.

Confirmed by execution:
```
BUG1 method returns: True
BUG1 CONFIRMED -> 'VaultSearchResult' object has no attribute 'strip'
```

Deliverable consequence — every one of these traces to this single line:
- `0 chapters · 12 agents` (At a Glance)
- `0` under **Analysis Sections** (KPI strip)
- Table of Contents jumps Executive Summary → Risk Analysis → Methodology with no analysis chapters
- `Layout plan complete: 7 pages with 0 images and 0 charts` — **0 sections means 0 section images**
- 33 data points and 12 specialists' work discarded unread

**Root cause class:** an unvalidated tool-boundary contract on a *decorative* input was allowed to
abort a *critical* pipeline. Annotated `-> str`, never enforced; `mypy --strict` does not cover this
module (see D-14).

---

### D-02 · S1 · The quality loop fabricates content over an empty report and erases the failure notice

This is why you received confident nonsense instead of an honest error.

After D-01, `_minimal_report()` produced *correct and honest* text:

> "This is a degraded report. Synthesis could not complete fully: 'VaultSearchResult' object has no
> attribute 'strip'. The recommendation is INVESTIGATE pending additional research."

The Quality Gate scored it 2.2/5.0 and sent it back for iteration. `_apply_quality_feedback()`
(line 1233) then did this — `hyperion/agents/synthesis_lead.py:1352`:

```python
if "executive_summary" in data and data["executive_summary"]:
    updated.executive_summary = data["executive_summary"]          # ← OVERWRITES the honest notice
if "recommendation_rationale" in data and data["recommendation_rationale"]:
    updated.recommendation_rationale = data["recommendation_rationale"]

section_updates = data.get("section_updates", {})
if isinstance(section_updates, dict):
    for section in updated.sections:            # ← updated.sections == []  →  LOOP BODY NEVER RUNS
        ...
```

Two fatal properties:

1. **The loop iterates an empty list.** `section_updates` can only ever *edit existing* sections.
   The repair path is structurally incapable of *creating* the sections D-01 deleted. The report can
   be re-prosed infinitely and will never regain a body. Log: `applied targeted fixes (4 fields updated)`
   — 4 scalar fields, 0 sections, twice.

2. **It overwrites the degradation notice with an LLM completion.** The model was asked to improve an
   empty report with no evidence. It pattern-completed the nearest text in its own context: the
   few-shot example hardcoded in its own system prompt.

**The smoking gun.** `hyperion/agents/synthesis_lead.py:157-160` (system prompt):

> "You synthesize. You say: **'Market says $2B TAM**, Financial says too small, but Financial's model
> assumes **5% penetration** while Market's data supports **12%** — at 12% penetration the market is
> viable. The recommendation is ENTER, with the critical assumption being penetration rate. If
> penetration falls below **8%**, ...'"

The shipped deliverable:

> "Market's **$2B TAM at 12% penetration** contradicts Financial's **5% assumption** … Financial
> viability is contingent on penetration exceeding **8%**, a threshold supported by sensitivity
> analysis (Source E, 2023)."

Same four numbers. Same argument. Same structure. **The report is a paraphrase of its own prompt's
teaching example**, dressed with invented citations "(Source A, 2023)"…"(Source E, 2023)" — which is
exactly what the Fact Checker independently flagged at 14:10:09 as `4 Hallucinated Citations Detected`,
and which the Director then discarded as a duplicate escalation (D-07).

**Root cause class:** (a) a repair path with write access to conclusions but no access to evidence;
(b) illustrative few-shot data indistinguishable from real data at generation time; (c) no
invariant forbidding a *degraded* report from being *upgraded* in tone without gaining evidence.

---

### D-03 · S1 · No PDF engine on Windows — `playwright` is not a declared dependency

`hyperion/output/render.py` has a correct three-stage ladder: WeasyPrint → Playwright → HTML.

- **Stage 1, WeasyPrint:** fails on Windows by design. WeasyPrint needs native GTK
  (`libgobject-2.0-0`, Pango, Cairo); these are not installed by `pip` and the wheel does not vendor
  them. `cannot load library 'libgobject-2.0-0': error 0x7e` is `ERROR_MOD_NOT_FOUND`. **Expected.**
- **Stage 2, Playwright:** `render.py:396` does `from playwright.sync_api import sync_playwright`.

```console
$ grep -n "playwright" pyproject.toml
221:    "nodriver", "camoufox", "playwright.*", "aiohttp",     ← mypy ignore list ONLY
```

**`playwright` appears nowhere in `[project.dependencies]`.** It is declared only as a type-checker
exemption. So `ImportError` → `[RENDER] Playwright not installed` → stage 3.
- **Stage 3, HTML fallback:** ships. Log confirms the truncated `; Pla` = "Playwright not installed".

**This is the direct and complete answer to "the pdf still looks like a fucking website".**
It is not a PDF. It has never been a PDF on your machine. It is an HTML file with a dark red
`DEGRADED OUTPUT` banner, and browsers render HTML like websites because it is one.

**Secondary defect inside stage 2 (would bite the moment Playwright is installed).** Chromium's
`page.pdf()` **does not implement CSS paged-media margin boxes.** `@top-center` / `@bottom-center`
are silently dropped. The brand CSS puts the running header and the page number there:

```css
@bottom-center { content: "HYPERION · many minds. one reading. · " counter(page) " / " counter(pages); }
@top-center    { content: string(section-title); }
```

WeasyPrint honours these. Chromium ignores them → **installing Playwright alone yields a PDF with no
running heads and no page numbers.** Chromium requires `header_template` / `footer_template`.
The code comment at `presentation_designer.py:221` asserts *"Both degrade gracefully in
Chromium/Playwright"* — **this assertion is false for margin boxes** and must be corrected.

---

### D-04 · S1 · The search layer has two engines and both are dead

`searxng_settings.yml` sets `use_default_settings: false` and enables exactly two engines:

```yaml
engines:
  - name: bing            # disabled: false
  - name: duckduckgo      # disabled: false
  - name: wikipedia       # disabled: true
  - name: arxiv           # disabled: true
  - name: github          # disabled: true
  - name: hackernews      # disabled: true
```

**DuckDuckGo — hard-banned mid-run.** Docker log, 39 consecutive failures then escalation:
```
08:26:55  SearxEngineCaptchaException: CAPTCHA (us-en) (suspended_time=0)      ← ×39
08:27:34  SearxEngineAccessDeniedException: HTTP error 403 (suspended_time=86400)
```
`suspended_time=86400` = **SearXNG suspended DuckDuckGo for 24 hours.** Every subsequent engagement
on that machine that day started with DDG already dead.

**Bing — silent zero.** *Not one* `bing` line appears in the Docker log: no error, no timeout, no
result. Microsoft retired the classic unauthenticated HTML SERP endpoint; SearXNG's scraper receives
HTTP 200 with markup its selectors no longer match, so it returns an empty list without raising.
**Silent failure is worse than a loud one** — nothing in the log or the health check registers it.

Consequence: the general-web tier of a 13-source research stack was **0 engines wide** for the entire
run. `EXTRACTION YIELD: 0/0 URLs (0%) extracted, 0 chars retained across 0 cited sources`.

**Root cause class:** an availability-critical dependency with a fan-out of 2, no diversity, no
health verification, no circuit breaker, and one member that fails silently. The 07-27 audit
*narrowed* this list (from bing+wikipedia+arxiv+github+hackernews) to reduce timeout noise — a
correct diagnosis with a fix that removed the redundancy that would have saved this run.

---

### D-05 · S2 · `DataVisualizer._logger` does not exist — every chart dies

`hyperion/agents/support/data_visualizer.py:845` and `:1043`

```python
self._logger.warning(f"MBB trace construction failed for {chart_spec.id} ...")
self._logger.warning(f"Plotly chart generation failed for {chart_id}: {e}")
```

`BaseAgent` defines no `_logger`. It uses a module-level `logger = logging.getLogger(__name__)`
(`base.py:59`). Confirmed by execution:

```
BUG2 DataVisualizer has _logger attr on class? False
BUG2 BaseAgent defines _logger?                False
```

Vicious detail: both call sites are **inside `except` handlers**. So the *error handler itself*
raises `AttributeError`, replacing a recoverable per-chart failure with an unrecoverable agent
crash: `VISUAL: Delivery agent failed: 'DataVisualizer' object has no attribute '_logger'`.
One bad chart spec takes down the entire visualization agent → `0 charts`.

Chart *quality* config is correct and unused: `scale=3` (300 DPI) at `data_visualizer.py:1034,1069`.
The renderer is fine. It never runs.

---

### D-06 · S2 · Health check is a port probe, so the dashboard lies

`hyperion/obs/health.py:65-82`

```python
if name == "searxng":
    if _check_port(host, port):
        h.status = "OK"          # ← "is TCP 8888 accepting connections?"
```

That is the *only* check. Meanwhile SearXNG was serving 403s from every engine. Boot banner:

```
[13:55:45] SEARXNG  ✓ SearXNG ready · localhost:8888 → container:8080
[13:55:51] TOOLS    ✓ 13 data sources ready
[13:55:53] READY    ✓ all systems online · type a question to begin
```

**Three green checkmarks over a completely non-functional research stack.** The operator had no
signal. Same class of defect for `flaresolverr` (port probe) and `jina` (API-key presence only).

---

### D-07 · S2 · Escalation schema mismatch — the Director discards every escalation

Two incompatible payload shapes are published to `MessageType.ESCALATION`.

**Shape A** — `bus.publish_escalation()` (`bus.py:419`), used by `BaseAgent._escalate()`:
```python
{"agent": ..., "issue": ..., "suggested_action": ...}
```

**Shape B** — five direct `bus.publish()` call sites:
| File | Line | Payload keys | Escalation |
|---|---|---|---|
| `support/quality_gate.py` | 1458 | `from_agent`, `to_agent`, `message`, `escalation_report` | **quality gate failed** |
| `support/quality_gate.py` | 1486 | `from_agent`, `to_agent`, `task`, `quality_score` | send back for iteration |
| `support/fact_checker.py` | 990 | `from_agent`, `to_agent`, `message`, `unverified_claims` | **unverified claims** |
| `support/fact_checker.py` | 1244 | `agent`, `finding_type`, `message`, `contradictions` | **contradictions** |
| `delivery/render_engine.py` | 1153 | `from_agent`, `to_agent`, `message`, `issues` | **render verification failed** |

The Director reads Shape A only (`engagement_director.py:379`):
```python
issue      = payload.get("issue", "Unknown issue")   # Shape B has no "issue"
agent_name = payload.get("agent", "unknown")         # Shape B has "from_agent"
```

Then it deduplicates on those values (`:396`):
```python
fingerprint = f"{agent_name}:{issue.strip().lower()[:160]}"   # → "unknown:unknown issue"
if fingerprint in self._seen_escalations:
    self._log(f"DIRECTOR: skipping duplicate escalation from {agent_name}: {issue[:100]}")
    return
```

**Every Shape-B escalation collapses to the identical fingerprint `"unknown:unknown issue"`.** The
first is evaluated with empty content; **all others are discarded as duplicates.** The log's
7× `skipping duplicate escalation from unknown: Unknown issue` is the Director throwing away:

- the Quality Gate's 2.2/5.0 failure report
- the Fact Checker's 4 hallucinated citations
- the Fact Checker's contradiction set
- the Render Engine's verification failure

**Adaptive replanning — a headline capability — is 100% inoperative for all support agents.** The
07-27 audit added the storm defence that now guarantees the data loss; the payload divergence
predates it and was never detected because the fingerprint made all failures look identical.

---

### D-08 · S2 · Quality Gate cannot fail closed

`orchestrator` log, 14:16:18:
```
QUALITY iteration 2/2: score=2.2/4.0 approved=False critical=6 gaps=10
QUALITY: max iterations (2) reached — proceeding with best available
DELIVERY: starting 3 delivery tasks
```

A report that scored **2.2/4.0 with 6 critical dimensions failing and 10 gaps** was rendered and
delivered. `MAX_ITERATIONS` is an escape hatch with no terminal `REJECT` state. There is no
threshold below which the system refuses to produce a deliverable.

For a system whose value proposition is trustworthy output, **"warn and ship" is the wrong default.**
Below a floor it must emit a diagnostic instead of a report.

---

### D-09 · S3 · Duplicate task execution — `_apply_adaptation` re-runs completed agents

`hyperion/agents/engagement_director.py:486`

```python
spawn_agent = adaptation.get("spawn_agent")
if spawn_agent and spawn_question:
    agent_name = AgentName(spawn_agent)
    new_task = TaskNode(
        id=f"task_adapted_{agent_name.value}_{int(time.time())}",
        agent=agent_name,
        dependencies=[],                 # ← no ordering constraint
        status=TaskStatus.PENDING,       # ← immediately "ready"
    )
    self._current_dag.add_task(new_task)
```

**No check for whether that agent already has a COMPLETED or RUNNING task.** The orchestrator's
`get_ready_tasks()` sees a PENDING task with no dependencies and runs it.

Observed: `regulatory_analyst` completed at 13:56:56 (`0 regulations across 0 jurisdictions`) and
was re-spawned at **14:10:30** — 12 seconds after the D-01 synthesis crash — and ran again, twice
concurrently (every log line duplicated at `14:10:30`, `14:11:21`→`14:12:37`, identical payload
sizes `32610`/`8288` chars emitted twice). ~2 minutes and a full second set of LLM calls burned.
Because `dependencies=[]`, it ran **after** synthesis had already consumed findings — its output
could not reach the report even in principle. This also explains the duplicated `Key Value Driver`
entries appearing twice each in the deliverable's findings list.

Adjacent bug in the same method (`:512-527`): the reroute branch has no `reroute_from != reroute_to`
guard, so an LLM returning the same agent for both makes a task depend on **itself** →
`get_ready_tasks()` can never satisfy it → guaranteed deadlock, caught only by the 100-iteration
safety valve.

---

### D-10 · S3 · Unsigned `obscura.exe` blocked by Windows Defender

Screenshot: *"Part of this app has been blocked — we can't confirm who published obscura.exe that
the app tried to load."* SmartScreen/ASR blocked an unsigned 43 MB binary committed to the repo
(`obscura-x86_64-windows.zip`, `obscura-bin/`).

The Obscura scraping tier is therefore unavailable on any managed Windows host. Log shows the
system announcing `Scraping ESG rating platforms (Obscura)`, `Scraping government regulatory portals
(Obscura)`, `Scraping research portals (Obscura)` — **all no-ops.** `health.py` checks
`_binary_available()` (existence + exec) which **passes**, because the file is present and
executable; Defender blocks it at *load* time, in-process. Health cannot see that.

---

### D-11 · S3 · FRED is US-only and is the macro path for every geography

```
[14:00:53] MARKET  system.log.FRED macro context is US-only; requested 'India' cannot be served
[14:06:04] FINANCE system.log.FRED macro inputs are US-only; requested geography 'India'
                   cannot be served — flagging mismatch
```

Correctly *detected* and correctly *reported* — then not *routed around*. `hyperion/tools/sdmx.py`
(OECD/Eurostat/IMF) and `world_bank` exist in the tree and were added by the 07-27 audit's item 5.5,
but the mismatch does not trigger a fallback to them. For a question about **Indian imports**, the
authoritative sources are World Bank WITS, IMF DOTS, UN Comtrade, RBI, and India's Ministry of
Commerce — none reached.

---

### D-12 · S3 · Yield instrumentation reports on a dead code path

`EXTRACTION YIELD: 0/0 URLs (0%) extracted, 0 chars retained across 0 cited sources
(avg 0 chars/source, **0 search calls**)`

`0 search calls` is false — the Docker log records dozens of SearXNG queries between 13:56:55 and
13:57:34. The counter is incremented on a path the specialists did not take (they went through
sub-agents / `deep_search`). The one metric designed to catch exactly this failure was itself
mis-instrumented, so it read `0/0` (which formats as a tidy `0%`) instead of screaming.

---

### D-13 · S3 · Sub-agent research runs on 16K-context MICRO models

`hyperion/config.py:113-135` — the MICRO tier, whose declared roles include `"sub-agent quick tasks"`:

```python
ModelSpec(name="gemma-4-31b", context_window=16_000, tpm=16_000, tier=ModelTier.MICRO,
          roles=[..., "sub-agent quick tasks", ...])
ModelSpec(name="gemma-4-26b", context_window=16_000, tpm=16_000, tier=ModelTier.MICRO, ...)
```

**16,000 tokens is a hard ceiling of ~64 KB.** A sub-agent tasked with *"Find all applicable
regulations for India in the imports industry"* must hold: its instructions + SERP results + the
extracted text of several documents + its output schema. Indian import regulation runs to hundreds
of pages. `tpm=16_000` means **one request per minute at full context.**

This is the direct cause of the shallow, empty specialist returns:
```
CONSUMER  ✓ complete: 0 personas, 0 journey stages, 0 segments, NPS=no, demand=no, WTP=no, reviews=0
INNOVATE  ✓ complete: 0 TRL assessments, 0 hype cycle positions, 0 horizon signals, disruption=no
REGULATORY ✓ complete: 0 regulations across 0 jurisdictions
MARKET    ✓ complete: TAM Parse error, maturity=unknown
```
Every one reports **success** with **zero substance** — the exact "success while delivering nothing"
class the 07-27 audit's commit `7327f27` claimed to close. It closed the *reporting*, not the *cause*.
Also note `SUB-AGENT budget reached (3/3); proceeding without spawning` — the 3-sub-agent cap fired
while sub-agents were returning nothing, so the cap spent its budget on noise.

---

### D-14 · S4 · `mypy --strict` does not cover the modules that broke

`pyproject.toml` maintains a staged allowlist (07-27 item 5.1f). Both D-01 (`-> str` returning a
dataclass) and D-05 (`self._logger` on a class without it) are **exactly** what `mypy --strict`
catches for free. Neither `synthesis_lead.py` nor `data_visualizer.py` is enforced. The process
gate exists and does not cover the two files that destroyed the deliverable.

---

### D-15 · S4 · Self-contained embedding is pathologically inefficient

2.27 MB of base64 fonts + 4.4 MB base64 for **one** cover image, inlined into a single HTML file
(§1.1). Base64 adds 33% over binary. A 4.4 MB encoded cover is a ~3.3 MB JPEG — far beyond what
A4 at 300 DPI needs (~1.2 MB at quality 88). Fonts should be subset (Latin + punctuation ≈ 15-40 KB
each, not 325 KB). Correct instinct (portability), no budget.

---

### D-16 · S4 · Invalid CSS in the page rule

`@page { size: A4; dpi: 300; ... }` — **`dpi` is not a CSS property.** It is silently discarded by
every engine. Raster DPI is a property of the *images* (`scale=3`) and of the PDF writer, never of
`@page`. Harmless, but it is load-bearing in the code comments' claim of "300 DPI output", which
misleads the next reader.

---

## 3. Why the 2026-07-27 fixes did not fix this

Not one of the 12 defects above was addressed by the previous audit, and three were **made worse by
it**. This is the pattern to break.

| 07-27 action | Intent | Actual effect on 07-30 run |
|---|---|---|
| Narrowed SearXNG to bing + duckduckgo | Kill timeout noise | **Removed the redundancy that would have survived the DDG ban** (D-04) |
| Added Director escalation storm-defence | Save STRONG quota | **Guaranteed silent loss of every support-agent escalation** (D-07) |
| `7327f27` "specialist could report success while delivering nothing" | Fix false success | Fixed the *reporting*; the 16K MICRO context **cause** untouched (D-13) |
| `4dc9820` PDF/A-2b post-pass via pikepdf | Archival-grade PDFs | Post-processes a PDF **that is never produced on Windows** (D-03) |
| `9ea5022` OECD/Eurostat/IMF SDMX | Break the FRED US-only ceiling | Sources added; **FRED mismatch still doesn't route to them** (D-11) |
| `17b98b8` embeddings + sqlite-vec Second Brain | Semantic retrieval | Made `search()` return a richer object → **triggered D-01** |
| `0120191` ruff + `mypy --strict` gate | Process gate | Allowlist **excludes the two files that broke** (D-14) |
| `881533a` golden-PDF regression test | Catch regressions | Asserts on a **golden PDF**; can't run where PDF generation is impossible (D-03) |

**The structural error:** every fix was verified against the *component* it touched, never against
the *deliverable*. There has never been an assertion of the form *"the shipped artifact contains N
analysis chapters, M charts, and cites S distinct real sources."* Without that end-to-end invariant,
a type error in a decorative prompt block can delete the entire report and every test still passes.

**The 07-27 document's own §3.1 delivery contract says "15-20 pages".** The 07-30 run shipped 7
pages with 0 chapters and no test noticed.

---

## 4. The fix — phase by phase

Ordering is strict and deliberate: **make it honest → make it produce a PDF → make it find evidence
→ make it deep → make it beautiful.** Each phase ends at a state that is verifiable and shippable,
and no phase depends on a later one.

> **Non-negotiable rule for every phase:** each fix lands with a test that asserts on the
> **deliverable**, not on the function. A fix without a deliverable-level assertion is a patch.

---

### PHASE 0 — Stop lying (½ day) · unblocks all diagnosis

Nothing else can be trusted until the system reports its own state accurately.

**0.1 — Real search health check.** Replace the TCP probe (D-06) with a smoke query.

`hyperion/obs/health.py`
```python
SMOKE_QUERY = "india import tariff"
MIN_SMOKE_RESULTS = 3

def _check_searxng(settings) -> ToolHealth:
    h = ToolHealth(name="searxng")
    url = _searxng_url(settings)
    if not _check_port(*_searxng_hostport(settings)):
        h.status, h.detail = "OFFLINE", f"not reachable at {url}"
        return h
    try:
        r = httpx.get(f"{url}/search",
                      params={"q": SMOKE_QUERY, "format": "json"}, timeout=20.0)
        r.raise_for_status()
        body    = r.json()
        results = body.get("results", [])
        # SearxNG reports per-engine failures in `unresponsive_engines`.
        dead    = [e[0] for e in body.get("unresponsive_engines", [])]
        live    = {res.get("engine") for res in results if res.get("engine")}
        if len(results) >= MIN_SMOKE_RESULTS:
            h.status = "OK" if not dead else "DEGRADED"
            h.detail = f"{len(results)} results from {sorted(live)}" + (f"; DEAD: {dead}" if dead else "")
        else:
            h.status = "OFFLINE"          # port open, engine layer dead — the 07-30 state
            h.detail = (f"reachable but returned {len(results)} results for "
                        f"{SMOKE_QUERY!r}; unresponsive: {dead or 'none reported'}")
    except Exception as exc:
        h.status, h.detail = "OFFLINE", f"smoke query failed: {type(exc).__name__}: {exc!s:.80}"
    return h
```

**0.2 — Refuse to start an engagement with a dead research stack.** In `orchestrator`, before
building the DAG:
```python
if search_health.status == "OFFLINE":
    raise EngagementPreflightError(
        "Research stack is offline — SearXNG returns no results. "
        "An engagement started now can only produce ungrounded output. "
        f"Detail: {search_health.detail}")
```
The 07-30 run should never have been allowed to begin.

**0.3 — Fix the yield metric (D-12).** Move the counters into the single choke point every retrieval
path traverses (`tools/deep_search.py` / `unified extract ladder`), not the specialist wrapper. Then
make `0 search calls` alongside `0 chars` a **hard error**, not a log line.

**0.4 — Correct the false comment** at `presentation_designer.py:221` claiming margin boxes
"degrade gracefully in Chromium".

**Exit criteria:** boot banner shows `DEGRADED`/`OFFLINE` for search when engines are banned; a run
started against a dead stack aborts in <10 s with an actionable message.

---

### PHASE 1 — Restore the report body (1 day) · fixes D-01, D-02, D-05

**1.1 — D-01: honour the type contract.** ✅ **Already applied on this branch** (verified):

```python
async def _query_second_brain_for_patterns(self, question: str) -> str:
    try:
        brain   = self.get_tool(ToolName.SECOND_BRAIN)
        results = await brain.search(f"synthesis patterns: {question}")
    except (ValueError, AttributeError, RuntimeError):
        return ""
    notes = getattr(results, "notes", None)
    if not notes:
        return ""
    lines = []
    for note, score in notes[: self.PRIOR_PATTERN_LIMIT]:
        title = getattr(note, "title", "") or "(untitled note)"
        body  = (getattr(note, "content", "") or "").strip().replace("\n", " ")
        lines.append(f"[relevance {score:.2f}] {title}: {body[:400]}")
    return "\n".join(lines)
```
Plus boundary coercion at the call site so a future contract change degrades to a missing prompt
block instead of a contentless report:
```python
patterns_text = prior_patterns if isinstance(prior_patterns, str) else str(prior_patterns or "")
```

**1.2 — D-01 structural: build the body before it can be lost.** The real defect is *ordering*: the
report body is assembled at step 8, so any earlier raise discards 12 specialists' work. Reorder so
`_build_analysis_sections()` runs on findings **before** the recommendation call, and make
`_minimal_report()` carry whatever was already built:

```python
def _minimal_report(self, reason: str = "", sections=None, **kw) -> FinalReport:
    return FinalReport(..., sections=sections or self._partial_sections, ...)
```
A synthesis failure must cost you the *recommendation*, never the *analysis*.

**1.3 — D-02: forbid tone upgrades without evidence gain.** In `_apply_quality_feedback()`:

```python
# A degraded report may gain STRUCTURE, never CONFIDENCE. The quality loop has
# write access to conclusions and no access to evidence; without this guard it
# launders a crash into a confident recommendation. (D-02)
if report.is_degraded:
    updated.executive_summary       = report.executive_summary
    updated.recommendation_rationale = report.recommendation_rationale
    updated.limitations             = report.limitations
```
Add `FinalReport.is_degraded: bool`, set by `_minimal_report()`. And make the loop honest about
sections it cannot create:
```python
if not updated.sections and section_updates:
    self._record_failure("quality loop returned section_updates for a report with 0 sections "
                         "— body was never built; see D-01")
```

**1.4 — D-02: de-fang the few-shot example.** Replace concrete fabricated numbers in
`synthesis_lead.py:16-19` and `:157-160` with non-transcribable placeholders, and add an explicit
prohibition:

```python
"You synthesize. You say: 'Market says ⟨TAM_FIGURE⟩, Financial says too small, but "
"Financial's model assumes ⟨LOW_PENETRATION⟩ while Market's data supports "
"⟨HIGH_PENETRATION⟩ …'\n\n"
"HARD RULE: ⟨…⟩ are placeholders showing SHAPE, never values. Every number you emit "
"must appear verbatim in the findings above. If the findings contain no numbers, write "
"'no quantitative evidence retrieved' — inventing a figure is the single worst failure "
"mode of this role."
```

**1.5 — D-02: leakage tripwire.** A test asserting the canonical example tokens never appear in a
generated report (§5, T-04). This is the assertion that would have caught the 07-30 deliverable.

**1.6 — D-05: `self._logger` → module `logger`.** Two lines. Then add the guard that makes the class
of bug impossible:
```python
# tests/test_no_phantom_self_attrs.py — AST scan of all BaseAgent subclasses:
# every `self.X` attribute read must be assigned somewhere in the class or its MRO.
```
This catches every remaining `self._foo` typo in ~40 agent files in one shot. Both D-01 and D-05 are
also `mypy --strict` catches — see Phase 6.

**Exit criteria:** a synthesis run with a populated vault produces `len(report.sections) >= 5`; a
forced mid-synthesis exception still yields sections; no report contains the example tokens.

---

### PHASE 2 — Produce an actual PDF (1 day) · fixes D-03

**2.1 — Declare the dependency.** `pyproject.toml`:
```toml
dependencies = [
    ...
    "playwright>=1.44.0",
]
```

**2.2 — Install the browser, don't just import it.** `pip install playwright` does not download
Chromium. Add a first-run bootstrap and a health check that verifies the *binary*, not the module:
```python
def ensure_chromium() -> tuple[bool, str]:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return False, "playwright not installed — `pip install playwright`"
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
            if Path(path).exists():
                return True, path
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc!s:.100}"
    return False, "chromium not downloaded — run `python -m playwright install chromium`"
```
Surface it in the boot banner next to SearXNG. **Silence here cost you every PDF you have ever
generated on this machine.**

**2.3 — Give Chromium its running heads.** Margin boxes are not implemented in Chromium, so pass
templates explicitly:
```python
page.pdf(
    path=output_path, format="A4", print_background=True,
    display_header_footer=True,
    header_template=(
        '<div style="font:9pt Georgia,serif;color:#8B8680;width:100%;'
        'padding:0 25mm;text-align:center;">' + html.escape(section_title) + "</div>"),
    footer_template=(
        '<div style="font:8pt \'JetBrains Mono\',monospace;color:#8B8680;width:100%;'
        'padding:0 25mm;text-align:center;">HYPERION · many minds. one reading. · '
        '<span class="pageNumber"></span> / <span class="totalPages"></span></div>'),
    margin={"top": "28mm", "bottom": "24mm", "left": "40mm", "right": "25mm"},
    prefer_css_page_size=True,
)
```
`pageNumber` / `totalPages` are Chromium's substitution hooks. **Header/footer templates need
explicit top/bottom margin or they overlap the body** — hence 28/24 mm.

**2.4 — Add a third engine for hardened Windows hosts.** If Defender blocks Chromium too
(cf. D-10), fall back to a locally installed Edge via `channel="msedge"` before HTML.

**2.5 — Make the HTML fallback loud in the pipeline, not just the page.** Currently only a red
banner in the artifact. It must set `RenderResult.success = False`, fail the delivery task, and
appear in the final summary as `pdf → FAILED (HTML fallback)`. The 07-30 log printed
`pdf → output/should_india_import_less_FALLBACK.html` under a line that read `✓ engagement complete`.

**Exit criteria:** on Windows without GTK, `hyperion` emits a real `.pdf`, page count > 0, running
header and `n / N` footer present on every page; if it cannot, the run is marked FAILED.

---

### PHASE 3 — Restore evidence acquisition (2 days) · fixes D-04, D-10, D-11, D-12

**3.1 — Engine diversity with independent failure domains.** Two engines is not a search stack.
Enable a broad set, and pick for *independent indexes* — Mojeek and Brave run their own crawlers, so
a Bing outage cannot take them out:

```yaml
engines:
  - {name: mojeek,     engine: mojeek,     shortcut: mj, disabled: false, timeout: 20}  # own index
  - {name: brave,      engine: brave,      shortcut: br, disabled: false, timeout: 20}  # own index
  - {name: startpage,  engine: startpage,  shortcut: sp, disabled: false, timeout: 25}  # Google proxy
  - {name: qwant,      engine: qwant,      shortcut: qw, disabled: false, timeout: 20}
  - {name: wikipedia,  engine: wikipedia,  shortcut: wp, disabled: false, timeout: 20}
  - {name: duckduckgo, engine: duckduckgo, shortcut: ddg, disabled: false, timeout: 25}
  - {name: bing,       engine: bing,       shortcut: bi, disabled: true,  timeout: 25}  # silent-zero; see D-04
```
Re-enable Bing only when a test proves it returns results.

**3.2 — Circuit breaker + minimum-breadth assertion.** A search that returns 0 results from all
engines must raise, not return `[]`. And track per-engine success so one banned engine degrades
breadth instead of zeroing it:
```python
if not results:
    raise SearchUnavailableError(
        f"0 results from {len(attempted)} engines for {query!r}; "
        f"unresponsive={dead}. Refusing to return an empty result set that "
        f"downstream agents will silently treat as 'nothing exists'.")
```
This single change converts D-04 from *silent fabrication* into *a loud, actionable stop*.

**3.3 — Route DDG through FlareSolverr.** FlareSolverr is running, healthy, and unused for its actual
purpose. Configure it as DDG's proxy so CAPTCHA becomes a solvable challenge, not a 24-hour ban.

**3.4 — D-11: route around FRED instead of only reporting the mismatch.** Where the log says
*"FRED macro context is US-only; requested 'India' cannot be served"*, dispatch to a geography-aware
provider chain. The SDMX client from 07-27 already exists; wire it in:
```python
MACRO_PROVIDERS = {  # first match wins
    "US":      [fred, world_bank, imf_sdmx],
    "default": [world_bank, imf_sdmx, oecd_sdmx, eurostat_sdmx],
}
```
For trade questions specifically add **UN Comtrade** and **World Bank WITS** (tariff lines, HS-code
import/export by partner) — for "should India import less?" these are the primary sources and the
absence of a Comtrade client is why the run had nothing real to say.

**3.5 — D-10: stop shipping an unsigned binary as a hard dependency.** Detect the Defender block at
*load* time (not existence time) by executing a `--version` probe in-process and catching the OS
error, mark Obscura `BLOCKED` in health, and make every "Scraping … (Obscura)" step announce its
actual tier. Medium-term: drop the vendored `.exe` (43 MB in git) in favour of
`curl_cffi`/`camoufox`, both already in the tree and both pure-Python.

**Exit criteria:** smoke query returns ≥3 results from ≥2 *distinct* engines; a run on
"should india import less?" retains ≥25,000 chars across ≥12 distinct real URLs; FRED mismatch
produces World Bank/IMF data, not a warning.

---

### PHASE 4 — Depth: fix the context and token starvation (2 days) · fixes D-13

This is the phase that answers *"make sure we get deep content, give subagents more context windows
or tokens if needed."* The current sub-agent tier physically cannot produce depth.

**4.1 — Ban MICRO from research roles.** 16K context / 16K TPM cannot hold a SERP plus extracted
documents. Reserve MICRO for what it is good at (keyword expansion, tag generation, yes/no
classification) and route all *research* sub-agents to ≥128K models:

```python
# hyperion/config.py
SUBAGENT_MIN_CONTEXT = 128_000

MICRO_ALLOWED_ROLES = frozenset({
    "query generation", "keyword expansion", "tag generation", "simple extraction",
})
# "sub-agent quick tasks" and "fact-check snippets" REMOVED from MICRO roles:
# a sub-agent that must read source documents is not a quick task. (D-13)
```
And enforce it in the router, so this can never silently regress:
```python
def select(self, *, role: str, min_context: int = 0, ...):
    candidates = [m for m in pool if m.context_window >= min_context]
    if not candidates:
        raise NoCapableModelError(
            f"role={role!r} needs ≥{min_context:,} ctx; largest available is "
            f"{max(m.context_window for m in pool):,}")
```
The tree already has 128K–1M models (`config.py:185` is 1,000,000 tokens). Depth was one routing
decision away the whole time.

**4.2 — Give sub-agents an explicit evidence budget.** Replace "quick task" framing with a contract:

| Sub-agent class | Min context | Input budget | Output floor | Sources |
|---|---|---|---|---|
| Regulatory / legal | 262K | 120K tok extracted text | 900 words | ≥5 primary (`.gov`, official gazettes) |
| Market sizing | 131K | 60K tok | 700 words | ≥4, ≥2 with figures |
| Competitive | 131K | 60K tok | 700 words | ≥4 distinct entities |
| Consumer / ESG | 131K | 50K tok | 600 words | ≥3 |
| Fact-check snippet | 32K | 12K tok | n/a | 1 (the cited one) |

**4.3 — Replace the flat 3-sub-agent cap with a yield-aware one.** Log shows
`SUB-AGENT budget reached (3/3); proceeding without spawning` **while sub-agents were returning
nothing**. A cap must count *productive* spawns:
```python
# Only a spawn that returned ≥MIN_USEFUL_CHARS of sourced text consumes budget.
# A cap that counts failures spends the whole budget on noise and then declares
# itself done — which is what produced "0 regulations across 0 jurisdictions". (D-13)
if result.retained_chars >= MIN_USEFUL_CHARS:
    self._subagent_budget_used += 1
```
Keep a separate hard wall-clock/attempt ceiling so this can't loop forever.

**4.4 — Make "0 findings" a task failure.** `regulatory_analyst: completed with 0 findings` must be
`FAILED`, not `COMPLETED`. Nine specialists reported success with zero substance and the DAG called
it a win.

**4.5 — Enforce the word budget that already exists.** `page_budget.py` defines
`MIN_SECTION_WORDS = 450` / `MAX_SECTION_WORDS = 2600` and a `prompt_clause()`. Nothing checked it.
Assert post-generation and re-request short sections once:
```python
if section.word_count < MIN_SECTION_WORDS:
    section = await self._expand_section(section, budget.prompt_clause())
```

**Exit criteria:** every analysis section ≥450 words; ≥8 sections; total prose ≥60,000 chars;
no research sub-agent dispatched to a <128K model; specialists returning 0 findings marked FAILED.

---

### PHASE 5 — Premium output: typography, imagery, layout (2 days) · fixes D-15, D-16

This phase answers *"HD quality paper photos and texts, proper text style heading and premium
formatting."* The brand system is largely right; it is starved of content and unbudgeted.

**5.1 — Establish a real typographic scale.** Current CSS jumps 36pt → 22pt → 14pt with no
intermediate levels and no baseline grid. Adopt a modular scale on a 12pt baseline:

| Element | Face | Size / leading | Tracking | Notes |
|---|---|---|---|---|
| Cover title | Instrument Serif | 44/48pt | −0.02em | full-bleed, optical margin |
| H2 chapter | Instrument Serif | 26/30pt | −0.01em | `break-before: page` |
| H3 section | Source Sans 3 Bold | 15/20pt | +0.01em | `break-after: avoid` |
| H4 run-in | JetBrains Mono Bold | 10/16pt, uppercase | +0.08em | |
| Body | Source Sans 3 | 10.5/15pt | 0 | **serif-humanist for prose, not mono** |
| Exhibit label | JetBrains Mono | 8/12pt | +0.04em | |
| Footnote | Source Sans 3 | 8/11pt | 0 | |

**Change body copy off monospace.** `body { font-family: "JetBrains Mono" }` sets the entire report
in a typewriter face. No MBB deliverable does this — it reads as a terminal dump, costs ~40% more
line width, and is the single largest contributor to "this doesn't look like a consulting report."
Mono belongs on data, labels, and exhibit numbers. `SourceSans3` is already vendored in
`assets/fonts/` and unused for body text.

Add the paged-media hygiene that separates print from web:
```css
h2, h3, h4          { break-after: avoid; }
p                   { orphans: 3; widows: 3; }
.exhibit, table     { break-inside: avoid; }
table               { font-variant-numeric: tabular-nums; }
body                { hyphens: auto; text-align: left; }   /* ragged right, no rivers */
```

**5.2 — Subset the fonts (D-15).** 7 full TTFs → 2.27 MB. Subset to the used codepoint set with
`fonttools` and emit WOFF2:
```python
subset = "--unicodes=U+0020-007E,U+00A0-00FF,U+2018-201D,U+2013-2014,U+20B9,U+2192,U+00B7"
# U+20B9 = ₹ — required for an India report; currently no glyph coverage is verified at all.
```
Expected 2.27 MB → ~120 KB total, and it guarantees the rupee sign renders instead of tofu.

**5.3 — Budget the imagery, and actually source it (D-15).** Cover downloads Unsplash `full`
(3.3 MB, often 5000px+). For A4 at 300 DPI a full-bleed cover needs 2480×3508. Resample on ingest:
```python
COVER_TARGET   = (2480, 3508)   # A4 @300dpi
SECTION_TARGET = (2480, 1100)   # section band
JPEG_QUALITY   = 88             # visually lossless at 300dpi
```
→ ~1.1 MB cover instead of 4.4 MB, with **no perceptible quality loss at print size**.
Section images were 0 purely because sections were 0 (D-01); once Phase 1 lands, enforce
one image per chapter and assert `>= 1` in the deliverable test.

**5.4 — Exhibits are the actual deliverable.** MBB reports are exhibit-led. Require per chapter:
one lead exhibit (chart or table), a `Exhibit N —` label, a **source line**, and a takeaway
caption written as an assertion (not "Revenue by year" but "Revenue growth is concentrated in
two segments"). The chart engine (`scale=3`, 300 DPI) is already correct; it just needs to run
(D-05) and to be mandatory.

**5.5 — Drop `dpi: 300` from `@page` (D-16)** and put the real assertion in the test: rasterize the
PDF and verify embedded image DPI ≥ 300.

**Exit criteria:** PDF 15–20 pages; body set in a humanist sans/serif; ≥1 exhibit and ≥1 image per
chapter; every exhibit has label + source + assertive caption; ₹ renders; file < 8 MB; all fonts
embedded and subset.

---

### PHASE 6 — Make the failure classes structurally impossible (1 day) · fixes D-07, D-08, D-09, D-14

**6.1 — D-07: one escalation schema, enforced by type.** Delete the five ad-hoc payload dicts.
Introduce a model and route everything through it:
```python
class Escalation(BaseModel):
    agent: str                      # canonical — never "from_agent"
    issue: str                      # canonical — never "message"
    severity: Literal["info", "warn", "critical"]
    suggested_action: str = ""
    context: dict[str, Any] = {}    # payload-specific extras live HERE
```
`bus.publish_escalation(Escalation(...))` becomes the only publisher. Then harden the consumer so a
schema drift can never again masquerade as a duplicate:
```python
if not issue or issue == "Unknown issue" or agent_name == "unknown":
    raise MalformedEscalationError(f"unparseable escalation payload: {sorted(payload)}")
    # Previously this collapsed every support-agent escalation to the fingerprint
    # "unknown:unknown issue", so the FIRST one was evaluated with empty content and
    # every subsequent one was dropped as a duplicate. Adaptive replanning was
    # 100% inoperative and the only symptom was a log line. (D-07)
```

**6.2 — D-08: give the Quality Gate a floor.** Below it, no deliverable:
```python
HARD_FLOOR = 2.5
if total_score < HARD_FLOOR and iteration >= MAX_ITERATIONS:
    return QualityVerdict.REJECTED_TERMINAL   # emit a diagnostic, NOT a report
```
Delivery must refuse `REJECTED_TERMINAL`. A 2.2/5.0 report with 6 critical failures must never
reach a renderer.

**6.3 — D-09: idempotent adaptation.** In `_apply_adaptation()`:
```python
existing = [t for t in dag.tasks if t.agent == agent_name]
if any(t.status in (TaskStatus.COMPLETED, TaskStatus.RUNNING) for t in existing):
    self._log(f"DIRECTOR: {agent_name.value} already ran — recording gap instead of re-spawning")
    dag.adaptation_log.append(f"Suppressed duplicate spawn of {agent_name.value}")
    return
if reroute_from == reroute_to:      # self-dependency → guaranteed deadlock
    return
```
Also: a re-spawned task must depend on its predecessors, never `dependencies=[]`, or it runs after
synthesis and its output is unreachable.

**6.4 — D-14: extend `mypy --strict` to the delivery path.** Add `synthesis_lead.py`,
`data_visualizer.py`, `render.py`, `presentation_designer.py`, `engagement_director.py` to the
enforced set. **Both S1 type errors in this audit are free `mypy --strict` catches.** Add the AST
phantom-attribute scan from 1.6 as a belt-and-braces guard for dynamic attribute typos.

**Exit criteria:** malformed escalation raises in tests; 2.2-score report produces no PDF; a
double-spawn attempt is suppressed and logged; `mypy --strict` clean on all five files.

---

## 5. Test plan

**Designed for the free-tier Genspark sandbox.** Constraints assumed and respected throughout:
disk ≲ 1 GB free, RAM ≈ 2 GB, no GPU, no persistent daemons, wall-clock per command ≲ 10 min, no
paid LLM keys in CI. Therefore:

- **No Chromium download in the default suite.** ~450 MB. Gated behind `HYPERION_E2E=1`.
- **No live LLM calls in the default suite.** All specialist output comes from recorded fixtures.
- **No Docker in the default suite.** SearXNG responses are served by a local stub.
- **Artifacts capped at 2 MB** and written to `tmp_path`, never the repo.
- **Every test single-process, <15 s**, so the suite fits the sandbox's memory ceiling
  (`tools/run_suite_sharded.sh` already exists for shard-wise execution — reuse it).

### 5.1 Tier structure

| Tier | Marker | Runtime | Needs | Runs |
|---|---|---|---|---|
| **T-unit** | *(none)* | < 60 s total | nothing | every commit, sandbox-safe |
| **T-contract** | `-m contract` | < 90 s | nothing (stubs) | every commit, sandbox-safe |
| **T-render** | `-m render` | < 3 min | `playwright` + chromium | pre-merge, `HYPERION_E2E=1` |
| **T-live** | `-m live` | ~20 min | Docker + API keys | nightly / local only |

```toml
# pyproject.toml
[tool.pytest.ini_options]
markers = ["contract: stubbed cross-component", "render: needs chromium", "live: needs net+keys"]
addopts = "-m 'not render and not live' --maxfail=5 -q"
```

---

### 5.2 T-unit — the regression locks (each is a bug from §2, and each would have failed on 07-30)

**T-01 · D-01 · the type contract** — *fails on the code that shipped*
```python
async def test_prior_patterns_returns_str(monkeypatch, vault_with_notes):
    lead = SynthesisLead()
    out = await lead._query_second_brain_for_patterns("india imports")
    assert isinstance(out, str)          # was VaultSearchResult
    out.strip()                          # the exact call that raised
```

**T-02 · D-01 · sections survive a mid-synthesis crash** — the invariant that was missing
```python
@pytest.mark.asyncio
async def test_synthesis_failure_preserves_analysis_body(monkeypatch, findings_fixture):
    lead = SynthesisLead(); lead._collected_findings = findings_fixture
    monkeypatch.setattr(lead, "_identify_and_draft",
                        AsyncMock(side_effect=RuntimeError("boom")))
    report = await lead.run(engagement_id="t", question="should india import less ?")
    assert report.is_degraded
    assert len(report.sections) >= 3, "a recommendation failure must not delete the analysis"
```

**T-03 · D-05 · no phantom `self.` attributes** — one AST scan covers ~40 agent files
```python
def test_no_phantom_self_attributes():
    offenders = []
    for py in Path("hyperion/agents").rglob("*.py"):
        tree = ast.parse(py.read_text())
        for cls in [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]:
            assigned = {t.attr for n in ast.walk(cls)
                        for t in getattr(n, "targets", []) + ([n.target] if hasattr(n,"target") else [])
                        if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name)
                        and t.value.id == "self"}
            read = {n.attr for n in ast.walk(cls)
                    if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name)
                    and n.value.id == "self" and isinstance(n.ctx, ast.Load)
                    and n.attr.startswith("_")}
            for attr in read - assigned - BASEAGENT_ATTRS - INHERITED_ATTRS:
                offenders.append(f"{py}:{cls.name}.self.{attr}")
    assert not offenders, f"attributes read but never assigned: {offenders}"
```
> Catches `self._logger` in `data_visualizer.py` **and** any sibling typo, permanently.

**T-04 · D-02 · few-shot leakage tripwire** — *the single test that would have caught the deliverable you received*
```python
FORBIDDEN = ["$2B TAM", "12% penetration", "5% penetration", "8% penetration",
             "three dominant players", "70% market share",
             "(Source A,", "(Source B,", "(Source C,", "(Source D,", "(Source E,"]

def test_report_never_echoes_prompt_example(generated_report_text):
    hits = [t for t in FORBIDDEN if t.lower() in generated_report_text.lower()]
    assert not hits, f"prompt example / placeholder citations leaked into deliverable: {hits}"
```

**T-05 · D-02 · degraded reports cannot be re-prosed into confidence**
```python
@pytest.mark.asyncio
async def test_quality_loop_cannot_overwrite_degradation_notice():
    degraded = lead._minimal_report(reason="VaultSearchResult has no attribute 'strip'")
    fake_llm_json = {"executive_summary": "Market's $2B TAM at 12% penetration is attractive.",
                     "recommendation_rationale": "ENTER."}
    out = await lead._apply_quality_feedback(degraded, _resp(fake_llm_json))
    assert "degraded" in out.executive_summary.lower()
    assert "$2B" not in out.executive_summary
```

**T-06 · D-07 · escalation schema is enforced**
```python
@pytest.mark.parametrize("payload", [
    {"from_agent": "quality_gate", "message": "failed"},   # Shape B — quality_gate.py:1458
    {"to_agent": "synthesis_lead", "task": "iterate"},     # Shape B — quality_gate.py:1486
])
def test_malformed_escalation_raises(payload):
    with pytest.raises(MalformedEscalationError):
        director._parse_escalation(payload)

def test_distinct_escalations_get_distinct_fingerprints():
    fps = {director._fingerprint(p) for p in ALL_FIVE_REAL_PAYLOADS}
    assert len(fps) == 5, "schema drift collapsed distinct failures into one fingerprint"
```

**T-07 · D-09 · adaptation is idempotent**
```python
def test_no_duplicate_spawn_for_completed_agent():
    dag = _dag_with(regulatory_analyst=TaskStatus.COMPLETED)
    director._apply_adaptation({"spawn_agent": "regulatory_analyst",
                                "spawn_question": "import tariffs in India"})
    assert len([t for t in dag.tasks if t.agent is AgentName.REGULATORY_ANALYST]) == 1

def test_self_reroute_is_rejected():
    director._apply_adaptation({"reroute_from": "risk_analyst", "reroute_to": "risk_analyst"})
    assert all(t.id not in t.dependencies for t in dag.tasks)   # no self-dependency
```

**T-08 · D-08 · the quality floor blocks delivery**
```python
def test_score_below_floor_blocks_render():
    verdict = gate.verdict(total_score=2.2, iteration=2)
    assert verdict is QualityVerdict.REJECTED_TERMINAL
    with pytest.raises(DeliveryRefused):
        delivery.run(report, verdict)
```

**T-09 · D-13 · research sub-agents are never routed to MICRO**
```python
def test_no_research_role_on_micro_models():
    for spec in ALL_MODELS:
        if spec.tier is ModelTier.MICRO:
            assert set(spec.roles) <= MICRO_ALLOWED_ROLES, \
                f"{spec.name} ({spec.context_window:,} ctx) claims research role"

def test_router_refuses_undersized_context():
    with pytest.raises(NoCapableModelError):
        router.select(role="regulatory research", min_context=262_000, pool=MICRO_ONLY)
```

**T-10 · D-16/5.1 · CSS is print-grade**
```python
def test_css_is_print_grade():
    css = build_css()
    assert "dpi:" not in css                              # invalid property (D-16)
    assert "{{" not in css and "{page}" not in css        # unsubstituted template markers
    assert 'font-family: "JetBrains Mono"' not in _body_rule(css)   # mono body (5.1)
    for req in ["orphans:", "widows:", "break-after: avoid",
                "break-inside: avoid", "tabular-nums"]:
        assert req in css
    assert css.count("@font-face") >= 3
```

---

### 5.3 T-contract — stubbed cross-component (no net, no keys, no Docker)

**T-11 · D-06 · health reports OFFLINE when engines are dead** — replays the exact 07-30 state
```python
@pytest.mark.contract
def test_health_offline_when_all_engines_banned(httpx_mock):
    httpx_mock.add_response(json={"results": [],
        "unresponsive_engines": [["duckduckgo", "CAPTCHA"], ["bing", "no results"]]})
    h = _check_searxng(settings)
    assert h.status == "OFFLINE"           # was "OK" — the port was open
    assert "duckduckgo" in h.detail
```

**T-12 · D-02/D-04 · zero evidence must abort, never fabricate** — the top-level guarantee
```python
@pytest.mark.contract
async def test_zero_evidence_aborts_engagement(stub_searxng_empty):
    with pytest.raises(EngagementPreflightError, match="Research stack is offline"):
        await orchestrator.run("should india import less ?")
```

**T-13 · D-04 · empty search raises instead of returning `[]`**
```python
@pytest.mark.contract
async def test_empty_search_raises(stub_searxng_empty):
    with pytest.raises(SearchUnavailableError):
        await SearxngClient(settings).search("india import tariff")
```

**T-14 · deliverable contract — the assertion that has never existed**

This is the test whose absence let every previous fix "pass" while the product broke. It runs on
recorded fixtures, so it is free and sandbox-safe.
```python
@pytest.mark.contract
async def test_deliverable_meets_contract(recorded_findings, tmp_path):
    report = await synthesise(recorded_findings, question="should india import less ?")
    html   = build_html(report)

    # ── content ──
    assert len(report.sections) >= 8,                 f"only {len(report.sections)} chapters"
    assert all(s.word_count >= 450 for s in report.sections)
    assert _prose_chars(html) >= 60_000,              "report is thinner than an exec summary"

    # ── grounding ──
    assert report.total_sources >= 12
    assert all(_is_real_url(s.url) for f in report.key_findings for s in f.sources)
    assert not _placeholder_citations(html)           # "(Source A, 2023)"

    # ── exhibits & imagery ──
    assert len(report.charts) >= 5
    assert all(_has_exhibit(s) for s in report.sections)
    assert all(_has_image(s)   for s in report.sections)

    # ── no duplicates (D-09) ──
    titles = [f.title for f in report.key_findings]
    assert len(titles) == len(set(titles)),           f"duplicated findings: {titles}"

    # ── topicality: the 07-30 report was about a market that doesn't exist ──
    assert _topic_overlap(html, "india import tariff trade deficit") > 0.30
```

**T-15 · fixture-sized golden HTML** — replaces the un-runnable golden-PDF test (D-03/§3)
```python
@pytest.mark.contract
def test_golden_html_structure(recorded_report, tmp_path):
    html = build_html(recorded_report)
    out  = tmp_path / "g.html"; out.write_text(html)
    assert out.stat().st_size < 2_000_000, "artifact exceeds sandbox budget"
    soup = BeautifulSoup(html, "html.parser")
    assert soup.select(".cover") and soup.select(".at-a-glance")
    assert len(soup.select("h2")) >= 10
    assert not soup.select(".degraded-banner")       # 07-30 shipped WITH this banner
```

---

### 5.4 T-render — real PDF (gated: `HYPERION_E2E=1`, needs ~450 MB Chromium)

**T-16 · D-03 · a real PDF exists and has running heads**
```python
@pytest.mark.render
def test_pdf_has_page_numbers_and_running_heads(tmp_path, recorded_report):
    res = PDFRenderer().render(recorded_report, str(tmp_path / "r.pdf"))
    assert res.success and res.pdf_path.endswith(".pdf")
    assert "FALLBACK" not in res.pdf_path
    doc = fitz.open(res.pdf_path)
    assert 15 <= doc.page_count <= 20
    for i, page in enumerate(doc, 1):
        text = page.get_text()
        assert f"{i} / {doc.page_count}" in text, f"page {i} lost its footer"
    assert res.fonts_embedded and all("JetBrains" in f or "Instrument" in f or "Source"
                                      in f for f in res.fonts_embedded)
```

**T-17 · D-15/5.3 · print resolution and file budget**
```python
@pytest.mark.render
def test_image_dpi_and_size_budget(rendered_pdf):
    assert Path(rendered_pdf).stat().st_size < 8_000_000
    doc = fitz.open(rendered_pdf)
    for page in doc:
        for img in page.get_images(full=True):
            info = doc.extract_image(img[0])
            rect = page.get_image_bbox(img)
            dpi  = info["width"] / (rect.width / 72)
            assert dpi >= 300, f"image at {dpi:.0f} DPI — below print grade"
```

**T-18 · glyph coverage (₹ for an India report)**
```python
@pytest.mark.render
def test_no_tofu_in_pdf(rendered_pdf):
    text = "".join(p.get_text() for p in fitz.open(rendered_pdf))
    assert "\ufffd" not in text and "\u25a1" not in text
    assert "₹" in text or "INR" in text
```

### 5.5 T-live — nightly only (never in sandbox)

**T-19** real SearXNG via Docker: ≥3 results from ≥2 distinct engines for 5 varied queries.
**T-20** full engagement on "should india import less ?" against live keys; asserts **T-14** on the
real artifact plus wall-clock < 25 min and cost within budget.

### 5.6 Sandbox execution recipe

```bash
# default — the only thing that must pass on every commit (< 3 min, < 400 MB RSS)
cd /home/user/webapp && python -m pytest -q

# memory-shaped shards, for the 2 GB ceiling (harness already in tools/)
bash tools/run_suite_sharded.sh

# pre-merge, when Chromium is available
python -m playwright install chromium --with-deps
HYPERION_E2E=1 python -m pytest -m render -q

# process gates
ruff check hyperion/ && mypy --strict hyperion/agents/synthesis_lead.py \
    hyperion/agents/support/data_visualizer.py hyperion/output/render.py
```

---

## 6. Definition of done

A phase is complete only when its exit criteria hold **and** the deliverable test (T-14) still
passes. The release gate is the *artifact*, not the test count.

| # | Gate | Measured by | 07-30 actual |
|---|---|---|---|
| 1 | PDF is a PDF | `T-16` | ❌ HTML fallback |
| 2 | 15–20 pages | `T-16` | ❌ 7 |
| 3 | ≥8 chapters, ≥450 words each | `T-14` | ❌ **0** |
| 4 | ≥60,000 chars prose | `T-14` | ❌ ~4,000 |
| 5 | ≥12 real cited sources | `T-14` | ❌ 4, placeholders |
| 6 | ≥5 exhibits at ≥300 DPI | `T-14`, `T-17` | ❌ 0 |
| 7 | ≥1 image per chapter | `T-14` | ❌ 0 |
| 8 | Running head + `n / N` every page | `T-16` | ❌ none |
| 9 | Quality score ≥ 4.0 | `T-08` | ❌ 2.2, shipped anyway |
| 10 | 0 hallucinated citations | `T-04` | ❌ 4 detected, ignored |
| 11 | No prompt-example leakage | `T-04` | ❌ verbatim leak |
| 12 | Health = OK only when engines answer | `T-11` | ❌ green over a dead stack |
| 13 | Escalations delivered, not deduped away | `T-06` | ❌ 7 discarded |
| 14 | No duplicate agent execution | `T-07` | ❌ regulatory ran 2× |
| 15 | File < 8 MB, fonts subset | `T-17` | ❌ 6.6 MB, 0.17% content |
| 16 | `mypy --strict` on delivery path | CI | ❌ not enforced |

---

## 7. Sequencing summary

```
PHASE 0  Stop lying                ½d   D-06 D-12        → diagnosis becomes possible
PHASE 1  Restore the report body    1d   D-01 D-02 D-05   → content exists and is honest
PHASE 2  Produce an actual PDF      1d   D-03            → deliverable is a PDF, not a web page
PHASE 3  Restore evidence           2d   D-04 D-10 D-11  → content is grounded in real sources
PHASE 4  Depth (context/tokens)     2d   D-13            → content is deep, not thin
PHASE 5  Premium output             2d   D-15 D-16       → content is beautiful
PHASE 6  Make failures impossible   1d   D-07 D-08 D-09 D-14
                                   ────
                                   9.5 days
```

Phases 0–2 (2.5 days) are the difference between *"a confident report about a market that does not
exist, delivered as a web page"* and *"an honest, printable PDF."* Phase 3–4 make it worth reading.
Phase 5 makes it worth paying for. Phase 6 keeps it that way.

**The one-line lesson:** this system has excellent components and no contract between them. Every
defect above is an interface where one side made a promise the other never checked — a `-> str` that
returned a dataclass, an escalation payload with two shapes, a health check that measured a socket
instead of a search, a quality gate with no floor, and a test suite that verified functions instead
of the artifact. Fix the contracts and the "wrapper" behaviour disappears, because the wrapper
behaviour *is* the absence of contracts.
