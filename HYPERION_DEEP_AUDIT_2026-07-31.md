# HYPERION Deep System Audit, 2026-07-31

**Artifact under audit:** `should_india_icreases_manufacturing.pdf` (34 pages, 36,694,790 bytes)
**Artifact metadata:** producer `HYPERION (WeasyPrint + pikepdf post-pass)`, created `D:20260731140527+00'00`
**Repository state:** branch `main`, HEAD `87f0582` ("Merge pull request #16 from hereugo-ak/fix0.1"), working tree clean
**Previous round:** `HYPERION_DEEP_AUDIT_2026-07-30_PART2.md`, 15 P2-\* commits, merged via PR #16
**Reported outcome of previous round:** every defect still present, identically

---

## 0. Executive verdict

The previous round did not fail because the fixes were wrong. Fifteen of them are
present in this tree and I verified several of them line by line. The round failed
because **not one fix was ever verified against a generated artifact**, and because
the pipeline is built so that an unverified artifact can still reach the user's hands
under the exact filename the user expects the deliverable to have.

That is the finding that outranks every other finding in this document, so it goes
first, in section 1, with its proof.

The rest of the audit then addresses what the user actually reported. Those reports
are all accurate. The report content is genuinely bad, and it is bad for reasons that
sit in the architecture, not in the prose. I found twelve root causes. Six of them are
the same six from 2026-07-30, unfixed in the running system for the reason in section 1.
Six are new and were not visible until now.

### 0.1 Root cause table

| ID | Root cause | Evidence anchor | User-visible symptom | Class |
|----|-----------|-----------------|----------------------|-------|
| RC-1 | The artifact was not produced by this source tree, and nothing in the system detects that | measured: white page corners, section 1.3 | "all of them exactly same" | provenance |
| RC-2 | Audit rejection is a return value, not a file operation. Rejected bytes stay on disk under the deliverable filename | `output/render.py:698-706`, `presentation_designer.py:1729,3197` | user opens a PDF the system believes it withheld | fail-open |
| RC-3 | The PDF is authored before charts are generated, so charts can never enter it | `engagement_director.py:1087-1125` | "there is no visualization too man" | DAG ordering |
| RC-4 | Delivery-stage failures are logged and skipped, never fatal | `orchestrator.py:1913-1956` | success reported on a broken run | fail-open |
| RC-5 | Contradiction detection compares one field and displays another. String inequality is treated as semantic opposition | `synthesis_lead.py:600-625` | "position a and then position b is about the fucking low confidence?" | algorithm |
| RC-6 | Agent roster is selected by question grammatical form, never by subject class | `engagement_director.py:236-277` | "wtf is dcf doing for a question related to an country?" | missing layer |
| RC-7 | Evidence insufficiency terminates into filler prose instead of escalating retrieval strategy | measured: 32 occurrences of "Confidence: low" | "why dont find more evidence?" | missing ladder |
| RC-8 | Quality Gate has an unconditional ship path after 2 iterations | `orchestrator.py:1466-1471` | 2.15/4.0 shipped as a client deliverable | governance |
| RC-9 | Internal pipeline vocabulary is printed to the client | measured: 4x "Fact Checker", 4x "allucinat" | "then the system directly write about hallucination?" | layering |
| RC-10 | Methodology section enumerates agents instead of describing method | `presentation_designer.py:1448-1477` | "check the methodology what the fuck is that? only agents used?" | content model |
| RC-11 | The search pool is built on engines that block automated clients, and half the code-referenced identities are not even registered. Degradation is silent | `searxng.py:473-490` vs `searxng_settings.yml`; operator Docker log | CAPTCHA storm, 24 hour 403, thin corpus, then RC-7 | wrong dependency class |
| RC-12 | Docker bring-up assumes systemd owns the daemon. No WSL2 awareness anywhere | `infra/services.py` Linux branch | "i have to manually start the docker application" | platform |

### 0.2 What is different about this audit

The 2026-07-30 audit produced correct diagnoses and correct code changes, and the
system still shipped the same garbage. So a list of correct code changes is provably
not a sufficient deliverable. This document therefore contains two things the previous
one did not:

1. **Section 6, the artifact verification protocol.** A fix is not done when the diff
   is written, not when the unit test passes, and not when the PR merges. It is done
   when a freshly generated PDF passes `audit_pdf()` with zero violations. Every work
   item in section 5 carries a command that produces that proof.
2. **Per-step failure-mode notes.** Each work item lists the specific mistakes that
   would let that item regress into the state we are in now.

---

## 1. RC-1 and RC-2: why the last round had no effect

### 1.1 The claim being tested

PR #16 contains fifteen commits. Seven of them change CSS or templates in ways that
have a directly measurable fingerprint in a rendered PDF:

| Commit tag | Change | Measurable fingerprint if present |
|---|---|---|
| P2-03 | cream background moved to `@page` and `html` | page corners are cream, not white |
| P2-05/06 | TOC uses `target-counter(attr(href), page)` | TOC page numbers match real headings |
| P2-02 | section imagery no longer floats into the text column | zero image-over-text occlusion |
| P2-09 | dict reprs cleaned before render | zero `{'` sequences in text |
| P2-12 | Fact Checker demoted from client chapter | zero "Fact Checker" strings |
| P2-32 | global em dash and en dash ban, four layers | zero U+2014 and U+2013 |
| P2-16 | filler strings made unconstructible | zero "Insufficient evidence to state implications" |

Every one of those seven fingerprints is **absent** from the artifact. All seven
defects are present instead.

### 1.2 Establishing that the source tree really does contain the fixes

I confirmed the CSS fixes exist in this tree at these exact lines:

`hyperion/agents/delivery/presentation_designer.py:237-246`

```
@page {{
    size: A4;
    dpi: 300;
    /* P2-03: paint the margin boxes too. Without this, the 25mm/15mm/19mm
       margin ring stays white while the canvas is cream. */
    background: {cream};
```

`hyperion/agents/delivery/presentation_designer.py:296-300`

```
/* P2-03: the background belongs on the ROOT element - CSS Backgrounds
   propagates a background to the page canvas only from the root. It used
   to sit on body, which is inset by the page margin, producing a cream
   panel floating on a white A4 sheet (white corners on every page). */
html {{ counter-reset: exhibit; background-color: {cream}; }}
```

Additionally: `target-counter` at `:876-884`, `column-fill: balance` at `:1033`, the
TOC template with real anchors at `:1252-1277`.

I then confirmed by grep across `*.py`, `*.css`, `*.html` and `*.j2` that
`presentation_designer.py` is the **only** file in the repository that emits an
`@page` rule. There is no second stylesheet, no alternative theme, and no template
fork that could produce different page geometry.

### 1.3 The measurement that closes the question

The comment at `:298-299`, written during the previous round, describes the pre-fix
defect in words: "a cream panel floating on a white A4 sheet (white corners on every
page)". That is a falsifiable signature. I measured it:

```
page  1 corners [(255,255,255) x4]  centre (172,176,160)
page  4 corners [(255,255,255) x4]  centre (244,244,237)
page 10 corners [(255,255,255) x4]  centre (244,244,237)
page 16 corners [(255,255,255) x4]  centre (244,244,237)
page 34 corners [(255,255,255) x4]  centre (244,244,237)
```

The brand cream is `DEFAULT_BACKGROUND_RGB = (0xF5, 0xF4, 0xEE)` = `(245,244,238)`.
The measured page centre is `(244,244,237)`, which is that colour within one unit of
compression rounding. The measured page corners are pure white.

So the artifact is exactly a cream panel inset on a white A4 sheet. That is the
pre-P2-03 rendering. The current tree paints cream on `@page` and on `html`, and CSS
background propagation from the root element covers the full canvas including the
margin ring, so this tree **cannot** produce white corners.

**Conclusion: the PDF was not rendered by the code in this tree.** This is not an
inference from behaviour, it is a physical property of the bytes.

### 1.4 Narrowing which build did render it

Two timestamps bracket it.

```
4dc9820  2026-07-30T07:57:05Z  feat(5.6): PDF/A-2b post-pass via pikepdf + bookmarks
c12f150  2026-07-30T19:00:39Z  fix(render): page audit wired fail-closed after PDF bytes exist (P2-08, P2-G1)
87f0582  2026-07-31T13:41:59Z  Merge pull request #16 from hereugo-ak/fix0.1
```

The producer string `HYPERION (WeasyPrint + pikepdf post-pass)` is written at
`hyperion/output/pdf_postprocess.py:180`, reached only through
`render.py:_apply_pdf_post_pass` at `:530`. The artifact carries that string, so the
running build was at or after `4dc9820`.

In this tree, `_apply_pdf_post_pass` and the `audit_pdf` call are consecutive
statements with nothing between them and no condition on the audit
(`output/render.py:693-706`):

```python
            self._apply_pdf_post_pass(result, output_path, full_html)

            # P2-08/P2-G1: render-time page audit, fail closed. ...
            from hyperion.output.page_audit import PageAuditError, audit_pdf

            try:
                audit_pdf(output_path)
            except PageAuditError as exc:
                result.success = False
```

The artifact has the post-pass stamp and 277 audit violations. If the running build
had contained both statements, the audit could not have been silent. So the running
build was **at or after `4dc9820` and before `c12f150`**, and it also predates P2-03.

The PDF was stamped at 2026-07-31T14:05:27Z, which is 23 minutes after the merge
landed. A HYPERION engagement runs for roughly half an hour, so the process was
launched before the merge existed, from a checkout or an installed copy that predated
it. There is nothing wrong with that in itself. What is wrong is that the system had
no way to say so.

### 1.5 RC-1: there is no build provenance check

I grepped `hyperion/tui/boot.py` and `hyperion/cli.py` for `__file__`, `rev-parse`,
`git_sha` and `GIT_SHA`. Zero hits. The shell never reports which files it loaded or
which commit they correspond to. Consequences:

- A stale non-editable `pip install` shadows the working tree silently.
- Stale `__pycache__` survives a `git pull` when mtimes are unhelpful.
- A second checkout on the same machine is indistinguishable from the intended one.
- The user reads a commit log, sees fifteen fixes, runs the shell, and receives
  pre-fix output with no signal that anything is out of date.

Every future fix round inherits this. It must be fixed first. Work item W-01.

### 1.6 RC-2: rejection does not remove the rejected bytes

This is a separate defect from RC-1, it is real in the current tree, and it would have
produced the identical user experience even on the correct build.

Follow the path. `presentation_designer.py:1729` and `:3197`:

```python
    PDF_OUTPUT = "output/report.pdf"
    ...
            self.PDF_OUTPUT = f"output/{slug}.pdf"
```

The user's file is named `should_india_icreases_manufacturing.pdf`. The report title
in the metadata is `should india icreases manufacturing ?`. That is precisely the
slug of the title. So the artifact is the file at `output/<slug>.pdf`.

Now `_generate_pdf` at `:3103-3126`:

```python
            result = weasyprint_tool.render_pdf(
                html=html_content,
                output_path=self.PDF_OUTPUT,
            )

            if result and result.success:
                self._log(f"RENDER: PDF produced at {self.PDF_OUTPUT}")
                return self.PDF_OUTPUT
            ...
            fallback = getattr(result, "html_path", "") if result else ""
```

And `render_pdf` writes the PDF to `output_path` at `:672`, applies the post-pass in
place, then runs the audit and on failure sets `result.success = False` and returns.

**The bytes are never deleted.** The "withhold" is a boolean. The rejected,
277-violation PDF remains at `output/should_india_icreases_manufacturing.pdf`, which
is the most plausible filename in the output directory, timestamped now, and looks
exactly like the deliverable. The orchestrator correctly reports `PDF=NO`, the log
correctly says the PDF was withheld, and the user correctly opens the file that is
sitting there and finds garbage.

So the system has a fail-closed gate whose closure is invisible in the filesystem.
Work item W-02.

### 1.7 Why this pair explains "all of them exactly same"

The user's complaint is not that the fixes were bad. It is that the fixes had no
observable effect. RC-1 and RC-2 are two independent mechanisms that each produce
exactly zero observable effect from a correct fix. Until both are closed, no fix in
this document can be trusted either, and the next round will end in the same place.

---

## 2. Measured defect census of the artifact

Everything in this section is a measurement on the uploaded bytes, not a reading of code.

### 2.1 The repository's own gate, run manually

```
audit_pdf('should_india_icreases_manufacturing.pdf')
  -> PageAuditError: 277 violations
scan_text_integrity(extract_pdf_text(...))
  -> 7 hits
```

Violation breakdown:

| Count | Violation |
|---|---|
| 136 | page corner colour is not the theme background |
| 57 | image bounding box occludes text bounding box |
| 30 | empty list item |
| 25 | ink fill below `INK_FILL_MIN` (0.30) |
| 14 | column balance below `COLUMN_BALANCE_MIN` (0.35) |
| 7 | banned substring present |
| 6 | duplicate paragraph |
| 5 | page word count below `WORDS_PER_PAGE_MIN` (90) |

Integrity hits: `{'`, U+2014, U+2013, `Insufficient evidence to state implications`,
`hallucinat`, `unverified claim`, `Fact Checker`.

The gate at `hyperion/output/page_audit.py` needs no change whatsoever. It already
catches every single thing the user complained about. It was simply not in effect.

### 2.2 Page occupancy: "parts of it is blank white exactly same"

| Metric | Measured | Gate threshold |
|---|---|---|
| median page ink fill | 23.2% | 45% (`INK_FILL_MEDIAN_MIN`) |
| maximum page ink fill | 45.3% | n/a |
| minimum acceptable per page | see below | 30% (`INK_FILL_MIN`) |
| page 10 fill | 2.9% | 30% |
| page 16 fill | 2.0% | 30% |
| page 34 fill | 3.2% | 30% |

The best page in the document barely reaches the median the gate requires. Twenty-five
pages are below the per-page floor. The user is describing the truth precisely.

### 2.3 Imagery: "fucking overlapping photos over the text then fucked up photos credit"

- Images per page: **12 on every page**, 34 pages.
- Charts in the document: **0**.
- Image-occludes-text violations: **57**, tolerance is 1.0 pt² of overlap.

Twelve images per page with zero charts is not a design. It is a decorative image
loop running unbounded while the visualization stage never contributed anything. See
RC-3 for why zero charts is structural rather than accidental.

### 2.4 Table of contents

- Entries: 17. **Wrong: 16.** Maximum error: **13 pages**.
- The advertised page numbers are `2, 4, 5, 6, ...`, perfectly sequential. That is
  arithmetic on a section index, not `target-counter`.
- The TOC lists a "Risk Analysis" page. **That section does not exist in the document.**

A TOC that advertises a chapter which was never rendered is worse than a wrong page
number, because it proves the TOC is generated from the plan rather than from the
document.

### 2.5 Text integrity counts

| Token | Count | Should be |
|---|---|---|
| U+2014 em dash | 79 | 0 (P2-32 banned it) |
| U+2013 en dash | 3 | 0 |
| `{'` Python dict repr | 24 | 0 |
| `Confidence: low` | 32 | 0 in client prose |
| `Fact Checker` | 4 | 0 |
| `allucinat` | 4 | 0 |

Four occurrences of `allucinat` means the document contains a chapter that announces
its own unreliability to the reader. The user's reaction ("then the system directly
write about hallucination?") is the correct reaction. See RC-9.

---

## 3. Root causes

Section 1 covered RC-1 and RC-2. This section covers RC-3 through RC-12.

### 3.1 RC-3: charts cannot reach the PDF, by construction

`hyperion/agents/engagement_director.py:1087-1125` builds the delivery TaskNodes:

```
task_presentation_designer  deps ["task_quality_gate"]
task_data_visualizer        deps ["task_presentation_designer"]
task_render_engine          deps ["task_data_visualizer", "task_presentation_designer"]
```

The designer runs first. The designer is the component that writes the HTML and calls
WeasyPrint (`:3319 pdf_path = await self._generate_pdf(html_path)`). The visualizer
runs **after** the PDF already exists on disk.

So the ordering is: author the document, then draw the charts, then run a render
engine that in principle could re-render but whose output the orchestrator treats as
optional. There is no arrangement of retries under which a chart produced by
`DataVisualizer` appears in a PDF authored before `DataVisualizer` started.

This is not a bug in the visualizer. The user's "there is no visualization too man" is
a direct consequence of a DAG edge pointing the wrong way. Work item W-03.

### 3.2 RC-4: the delivery stage is structurally fail-open

`hyperion/orchestrator.py:1913-1956`:

```python
    if ready:
        try: await self._execute_task(task, dag)
        except Exception as e:
            self._log(f"DELIVERY: {task.agent.value} failed: {e!s:.200}")
            ... task.status = TaskStatus.FAILED
    else:
        self._log(f"DELIVERY: {task.agent.value} dependencies not met — skipping")
```

Both branches continue. A crash in `DataVisualizer` marks it FAILED, which makes
`task_render_engine` never ready, which logs "dependencies not met" and skips. The only
delivery-path caller of `audit_pdf` besides `render.py` is
`render_engine.py:989-996`, so skipping the render engine skips that gate too. Then
`:1962-1966`:

```python
            if result.render_output and hasattr(result.render_output, "pdf_path"):
                result.pdf_path = result.render_output.pdf_path
            elif result.layout_plan and hasattr(result.layout_plan, "pdf_path"):
                result.pdf_path = result.layout_plan.pdf_path
```

The fallback promotes the designer's PDF to the engagement deliverable. Combined with
RC-2, the bytes are on disk regardless. The engagement reports success.

An `except Exception` around the step that produces the only client-visible artifact,
with a continue, is the definition of fail-open. Work item W-04.

### 3.3 RC-5: contradiction detection is a string comparison wearing a suit

This is the direct cause of the table the user pointed at. `synthesis_lead.py:600-625`:

```python
        content_a = entry_a["content"].lower().strip()
        content_b = entry_b["content"].lower().strip()
        if content_a == content_b: continue
        ctype = self._classify_contradiction(entry_a, entry_b, ftype)
        contradiction = Contradiction(
            id=f"contradiction_{contradiction_id}",
            agent_a=entry_a["agent"], agent_b=entry_b["agent"],
            finding_a=entry_a["title"], finding_b=entry_b["title"],
            contradiction_type=ctype)
```

Three defects stacked:

1. **The predicate is wrong.** `content_a != content_b` is string inequality. Two
   findings that say completely unrelated things are "contradictory". There is no
   check that they address the same claim subject, no numeric comparison, no polarity
   detection, no entity matching.
2. **Compare field and display field differ.** It filters on `content` and renders
   `title`. So two findings whose titles are both literally `Confidence: low` survive
   the filter (their bodies differ) and are then printed as Position A and Position B.
   That is verbatim what the user is looking at.
3. **It is expensive.** The run log shows "Resolving 28 contradictions". The entire
   sub-agent deep-dive budget was spent adjudicating manufactured disputes, until
   `:817` logs "sub-agent budget spent; resolving contradiction ... by source count
   instead of deep dive".

Then `presentation_designer.py:2979` prints the result into the client appendix:

```
"<tr><th>Type</th><th>Position A</th><th>Position B</th><th>Resolution</th></tr>"
```

with `html_escape(item.finding_a)` unmodified, which is how raw dict reprs reach the
page. Work item W-05.

### 3.4 RC-6: no subject ontology, so DCF lands on a nation state

`engagement_director.py:236-277` defines `QUESTION_TYPE_AGENTS` keyed on six question
types: GO_NO_GO, COMPARISON, FORECAST, DIAGNOSTIC, OPTIMIZATION, GENERAL.
`FINANCIAL_ANALYST` appears in **all six**.

"Should India increase manufacturing?" classifies as GO_NO_GO because of its
grammatical form. GO_NO_GO includes the financial analyst, whose playbook is DCF and
EV/EBITDA. There is no step anywhere that asks what kind of *thing* the question is
about. Company, country, technology, policy, market, and person all get the same
roster.

The same missing layer explains the rest of the empty chapters:

| Agent | Playbook it ran | Fit to a national industrial policy question |
|---|---|---|
| Financial Analyst | DCF, EV/EBITDA, WACC | none, there is no cash flow to discount |
| Consumer Insights | NPS, personas, G2 reviews | none, there is no product |
| Competitive Intel | competitor matrix, win/loss | none, there is no competitor set |

Six of eleven chapters returned zero findings. Those six chapters then emitted
`Confidence: low` (32 occurrences) and the filler in RC-7. **This is a task-fit
failure, not a search failure.** No amount of extra SearXNG capacity produces a
competitor matrix for India. Work item W-06.

### 3.5 RC-7: insufficiency is a terminal state instead of a trigger

The user asked the exactly right question: "insufficient evidence man? why dont find
more evidence?"

The artifact contains "Insufficient evidence to state implications. This section
requires additional research." That string exists as an outcome. In the current
architecture, when an agent's retrieval comes back thin, the terminal behaviour is to
emit a confidence downgrade and a placeholder sentence. There is a gap-closure ladder
from the previous round (`d1e472a`, "3-round gap-closure ladder") and a retrieval
escalation (`5b9fe60`, "thin evidence triggers retrieval escalation, not a stop"), and
both are aimed at this. Neither addresses the case in RC-6, where the agent is asking
questions that have no answers because the agent should not have been dispatched.

What is missing is a **decision** at the insufficiency point, rather than a fallback
string. The decision has four possible outcomes and the current code can only express
the fourth:

1. Retry with a different query strategy (different phrasing class, different engine
   set, different category route).
2. Retry with a different scope (broaden entity, change time window, change geography).
3. Declare the sub-question out of scope for this engagement and **remove the section**.
4. Emit filler and continue.

Outcome 3 is the correct answer for the six misfit chapters, and it is not currently
representable. A section that has nothing to say should not exist, and its absence
should be explained once in the limitations, not thirty-two times in the body.
Work item W-07.

### 3.6 RC-8: the Quality Gate cannot refuse

`orchestrator.py:239`:

```python
    MAX_QUALITY_ITERATIONS = 2  # P7: capped at ≤2
```

`orchestrator.py:1466-1471`:

```python
        self._log(f"QUALITY: max iterations ({self.MAX_QUALITY_ITERATIONS}) reached — proceeding with best available")
        ...
        if current_score and not current_score.approved and iterations >= self.MAX_QUALITY_ITERATIONS:
            current_score.max_iterations_reached = True
```

and then the QUALITY_GATE task is marked COMPLETED so delivery proceeds.

The threshold is 4.0 (`schemas/models.py:2396`). The run scored 2.15 with five
critical dimensions failing and sixteen open gaps. Two iterations later it shipped
under a client brand.

`max_iterations_reached` is not a quality state. It is a scheduling fact being used to
override a quality decision. There is a difference between "we could not improve this
further" and "this is acceptable to send", and the code conflates them. Work item W-08.

### 3.7 RC-9: no boundary between pipeline vocabulary and client prose

The document contains a chapter headed by the internal agent name "Fact Checker",
which announces "CRITICAL: 149 Hallucinated Citations Detected". Four occurrences of
`Fact Checker`, four of `allucinat`, thirty-two of `Confidence: low`.

`page_audit.BANNED_SUBSTRINGS` already lists `hallucinat`, `Fact Checker` and
`Quality Gate`, which means the previous round correctly identified that these words
must never appear. But banning them at render time is a backstop, not a design. The
design defect is that agent findings are serialised into client sections without any
transformation step that distinguishes:

- **internal telemetry** (agent names, confidence enums, verification states, gap ids)
- **client narrative** (claims, evidence, implications)

`Confidence: low` appearing 32 times means a confidence enum is being used as a
finding title. That is the same defect as RC-5 seen from a different angle: internal
structure is being rendered as prose. Work item W-09.

### 3.8 RC-10: methodology enumerates agents

`presentation_designer.py:1448-1477` emits exactly four things: Agents Used, Sources
Accessed count, Data Points count, Limitations.

The user is right that this is not a methodology. A methodology section in a research
deliverable states the research question decomposition, the retrieval strategy and its
coverage, the inclusion and exclusion criteria for sources, the verification procedure
and its pass rate, the analytical methods applied and why they fit the subject, and the
known limits of the design.

Listing twenty agent names tells the reader about our software. It also directly
violates the RC-9 boundary, since agent names are internal telemetry. Work item W-10.

### 3.9 RC-11: the search engine registry has drifted, silently

`hyperion/tools/searxng.py:473-490`:

```python
RELIABLE_ENGINES = "bing,duckduckgo,brave,mojeek,startpage,qwant"
STANDBY_ENGINES  = "google,ecosia,swisscows"
CATEGORY_ENGINES = {
    "science": "arxiv,google scholar,semantic scholar",
    "it": "github,stackoverflow",
    "news": "bing news,duckduckgo news",
}
```

`searxng_settings.yml` sets `use_default_settings: false` and declares exactly twelve
engines: bing, duckduckgo, brave, mojeek, startpage, qwant, ecosia, swisscows,
wikipedia, arxiv, github, hackernews.

Under `use_default_settings: false`, an engine not declared does not exist. So these
six identities referenced in code are dead: `google`, `google scholar`,
`semantic scholar`, `stackoverflow`, `bing news`, `duckduckgo news`.

Consequences:

- Standby rotation at `:359-390` promotes `google,ecosia,swisscows` on zero results.
  One third of that promotion is a no-op, and the promotion happens exactly when the
  system is already failing.
- **Every** category route is degraded: `science` loses two of three, `it` loses one of
  two, `news` loses both and returns nothing at all.
- None of this raises. It silently narrows the corpus, which then triggers RC-7.

Also in that file: `doi_resolvers` is declared twice, `server.limiter: false`, and
`secret_key` is still the shipped placeholder `hyperion-searxng-secret-change-me`.

The user's Docker log is the operational half of this. DuckDuckGo returned
`SearxEngineCaptchaException: CAPTCHA (us-en)` repeatedly and then
`SearxEngineAccessDeniedException: HTTP error 403 (suspended_time=86400)`. A 24 hour
suspension on one of six live engines, with a third of the standby set nonexistent, is
a corpus collapse. And `ERROR:searx.botdetection: X-Forwarded-For nor X-Real-IP header
is set!` indicates the request headers are not being set as SearXNG expects, which
makes the bot-detection path noisier than it needs to be. Work items W-11 and W-12.

There is a deeper point here than a missing registration, and it changes the shape of the
fix. Half the declared pool consists of engines that answer by serving HTML to a browser
and defend that surface against automation: DuckDuckGo, Google, Bing, Startpage, Ecosia,
Qwant, Swisscows. Depending on them means the corpus is one IP-reputation decision away
from collapse at all times, and no amount of rotation, cooling, or replica count changes
that, because the block is on the address rather than on the request. The pool is
therefore not merely misconfigured, it is **built on the wrong class of engine**. W-11 is
consequently a rebuild of the pool around documented APIs and independent crawlers rather
than a registration exercise, and the recall that decision gives up is recovered through
W-14. The operator has since made this an explicit requirement: no engine that CAPTCHAs
or bans may be in the pool at all.

### 3.10 RC-12: WSL2 is treated as generic Linux

`hyperion/infra/services.py`, `_launch_docker_desktop()`, Linux branch:

```python
        for cmd in (["systemctl","--user","start","docker"],
                    ["systemctl","--user","start","docker.service"],
                    ["systemctl","start","docker"]):
```

I grepped the whole codebase for `WSL`, `microsoft-standard`, `/proc/version`,
`wslpath` and `interop`. Zero hits.

Under WSL2 with Docker Desktop, `sys.platform` is `linux`, so this branch is taken,
but the daemon is a Windows process. `systemctl` cannot start it. All three commands
fail, `ensure_docker_engine` times out after its 90 second wait, and SearXNG never
comes up. That is exactly the user's report: start Docker Desktop by hand first, then
the shell works.

`MANAGED_CONTAINERS = ("searxng", "flaresolverr")` and the bring-up at
`tui/boot.py:211-240` are otherwise correct. Only the daemon-start step is wrong.
Work item W-13.

---

## 4. The architectural pattern behind all twelve

Four structural properties keep producing this class of failure. Every work item in
section 5 is an instance of fixing one of these.

### 4.1 Gates that report rather than gates that stop

`audit_pdf` sets a boolean. The Quality Gate sets `max_iterations_reached`. The
delivery loop logs and continues. Insufficient evidence writes a sentence. In each
case the system detects the problem correctly and then proceeds anyway.

**Principle:** a gate that the pipeline can walk past is telemetry, not a gate. If the
artifact fails, there must be no artifact.

### 4.2 Artifact authorship placed before artifact inputs

The PDF is written by the designer, before the visualizer runs, before the render
engine runs, using a TOC computed from the plan rather than the document. The
document is not the output of the pipeline, it is a side effect emitted partway
through it.

**Principle:** exactly one component may write the deliverable, it must run last, and
it must derive every number in the deliverable from the deliverable.

### 4.3 Internal structure leaking outward at every seam

Confidence enums as finding titles, agent names as chapter headings, dict reprs as
table cells, gap ids in prose, `Fact Checker` as a client-facing section. There is no
boundary type between "what the pipeline knows" and "what the client reads".

**Principle:** client sections must be constructed from a narrative type that
structurally cannot hold telemetry, rather than filtered for forbidden words after the
fact.

### 4.4 Capability selected by surface form instead of by subject

The agent roster keys on question grammar. The contradiction detector keys on string
inequality. Both substitute a cheap syntactic proxy for a semantic judgement, and both
produce confident nonsense at scale (a DCF on a country, 28 fabricated contradictions).

**Principle:** dispatch decisions must be justified against the subject of the
question, and a decision that cannot be justified must abstain rather than default.

---

## 5. Remediation programme

### 5.0 How to read this section, and the rule that makes it different

The previous round produced correct diffs that had zero effect. So this section is
written under one rule:

> **A work item is not complete until a PDF generated after the change passes
> `audit_pdf()` with zero violations, and the passing PDF's SHA-256 is recorded in the
> work item's completion note.**

Not "the unit test passes". Not "the code reads correctly". Not "the PR is merged".
A hash of a clean artifact, or it did not happen.

Each work item below has the same eight parts:

1. **Objective**, one sentence.
2. **Why the last round did not fix this**, so the same trap is not re-entered.
3. **Files and anchors**, exact paths and line numbers as of `87f0582`.
4. **Procedure**, ordered steps.
5. **Verification**, a command that produces evidence, and the expected output.
6. **Acceptance criteria**, binary conditions.
7. **Failure modes to avoid**, the specific ways this item regresses.
8. **Rollback**, how to undo safely.

Work items are ordered by dependency, not by severity. W-01 and W-02 come first
because without them nothing else can be verified.

---

### W-01: Build provenance assertion at shell boot

**Objective.** Make it impossible to run HYPERION without knowing exactly which files
and which commit are loaded.

**Why the last round did not fix this.** It was never identified. The previous audit
assumed that a merged fix is a running fix. RC-1 shows that assumption is false, and it
is the reason fifteen correct commits produced pre-fix output.

**Files and anchors.**
- `hyperion/tui/boot.py:61-83` (imports and `__all__`), `:211-240` (service bring-up)
- `hyperion/cli.py:124` (`_ensure_services_stopped`), entry point `hyperion.cli:app`
- New module: `hyperion/infra/provenance.py`

**Procedure.**

1. Create `hyperion/infra/provenance.py` exposing a single dataclass and one function:

   ```python
   @dataclass(frozen=True)
   class Provenance:
       package_dir: str        # Path(hyperion.__file__).parent, resolved
       repo_root: str | None   # nearest ancestor containing .git, else None
       git_sha: str | None     # short SHA of HEAD in repo_root
       git_dirty: bool         # working tree has modifications
       install_mode: str       # "editable" | "site-packages" | "unknown"
       stale_pycache: list[str]  # .pyc newer-source pairs found
   ```

   `install_mode` is determined by whether `package_dir` is inside `repo_root`. If
   `package_dir` resolves under a `site-packages` directory, it is `"site-packages"`.

2. Resolve `git_sha` by running `git rev-parse --short HEAD` with `cwd=repo_root`. Do
   not import a git library. Use `run_command` which already exists in
   `hyperion/infra/services.py`. If `git` is absent or the call fails, leave `None`.

3. Detect stale bytecode: for each `*.py` under `package_dir`, compare mtime with the
   corresponding `__pycache__/*.pyc`. Any `.pyc` older than its `.py` is stale. Cap the
   walk at the package directory only, never the whole filesystem.

4. Call this at the very top of the shell boot sequence in `hyperion/tui/boot.py`,
   before `ensure_docker_engine` at `:211`, and render a banner:

   ```
   HYPERION  build 87f0582  editable  /home/user/webapp/hyperion
   ```

   When `git_dirty` is true, append ` +dirty`. When `install_mode` is
   `"site-packages"`, render the banner in the error style and append the resolved
   path of the shadowed working tree if one is discoverable on `sys.path`.

5. Add a hard refusal, controlled by one setting, defaulting to on:
   - refuse to boot when `install_mode == "site-packages"` and a git checkout of
     `hyperion` also exists on `sys.path`, because that is the exact shadowing case
     from RC-1
   - refuse to boot when `stale_pycache` is non-empty
   The refusal message must state the fix: `pip install -e .` for the first case,
   `find . -name __pycache__ -type d -prune -exec rm -rf {} +` for the second.

6. Record the provenance into every engagement result and stamp `git_sha` into the PDF
   XMP metadata. `hyperion/output/pdf_postprocess.py:180` already writes
   `xmp["pdf:Producer"]`. Extend it to
   `HYPERION <sha> (WeasyPrint + pikepdf post-pass)`.

   This step is the one that would have saved the entire previous round. With it, the
   uploaded artifact would have carried a SHA that visibly predated the merge, and the
   diagnosis in section 1 would have taken thirty seconds instead of an afternoon.

**Verification.**

```bash
cd /home/user/webapp
python3 -c "from hyperion.infra.provenance import collect; print(collect())"
# expect: package_dir under /home/user/webapp, install_mode='editable', stale_pycache=[]

# negative test: prove the refusal fires
python3 - <<'PY'
import pathlib, time
p = pathlib.Path("hyperion/output/page_audit.py")
p.touch()                      # source now newer than its .pyc
from hyperion.infra.provenance import collect
assert collect().stale_pycache, "stale bytecode not detected"
print("stale detection OK")
PY
```

**Acceptance criteria.**
- `hyperion shell` prints a banner containing a git SHA on every start.
- Booting from a `site-packages` copy while a checkout is on `sys.path` refuses.
- Stale `.pyc` refuses.
- A generated PDF's producer string contains the SHA it was built from.

**Failure modes to avoid.**
- Making the banner a log line at INFO level. It must be on screen, unconditionally.
- Making the refusal a warning. A warning is what got us here.
- Shelling out to `git` without a timeout. Use `run_command` and bound it.
- Walking the filesystem for `.git`. Walk parents of `package_dir` only, and stop at
  the filesystem root or after eight levels.

**Rollback.** The module is additive. Remove the two call sites in `boot.py` and the
XMP line to revert with no behavioural residue.

---

### W-02: Rejected artifacts must not remain on disk under the deliverable name

**Objective.** Make a failed audit physically observable, so a withheld PDF cannot be
opened by the user.

**Why the last round did not fix this.** P2-08 wired the audit fail-closed and stopped
there. "Fail closed" was implemented as `result.success = False`. Nobody asked what was
sitting in `output/` afterwards. RC-2 is the answer: a 277-violation PDF named exactly
what the user expects the deliverable to be named.

**Files and anchors.**
- `hyperion/output/render.py:693-706` (WeasyPrint path audit), `:730-745` (Playwright
  path audit), `:672` and `:718` (`result.pdf_path = output_path`)
- `hyperion/agents/delivery/presentation_designer.py:1729` (`PDF_OUTPUT`), `:3197`
  (slug assignment), `:3103-3126` (`_generate_pdf` result handling)

**Procedure.**

1. Render to a **staging path**, never directly to the deliverable path. In
   `render_pdf`, write to `<output_path>.staging.pdf`.

2. Run the post-pass on the staging file, then `audit_pdf` on the staging file.

3. On pass: `os.replace(staging, output_path)`. `os.replace` is atomic on the same
   filesystem, so the deliverable path either does not exist or is a clean PDF. There
   is no window in which a partial or unaudited file occupies the deliverable name.

4. On fail: move the staging file to
   `output/_rejected/<slug>.<timestamp>.rejected.pdf` and write a sibling
   `<slug>.<timestamp>.violations.txt` containing the full violation list. Then ensure
   the deliverable path does not exist (`Path(output_path).unlink(missing_ok=True)`).

   Keeping the rejected bytes matters for debugging, which is why they are preserved
   rather than deleted. Putting them under `_rejected/` with a `.rejected.pdf` suffix
   matters for the user, who will never mistake that for a deliverable.

5. Apply the identical staging discipline to the Playwright fallback path at `:730-745`.
   Two copies of this logic is a regression risk, so extract one private helper
   `_finalize_or_reject(result, staging_path, output_path)` and call it from both.

6. In `presentation_designer._generate_pdf`, when `result.success` is false, the log
   line must name the rejected path and the violation count, not just the error string.
   The current message at `:3117-3120` truncates the error at 120 characters, which
   discards the violation list entirely.

**Verification.**

```bash
# force a failure and prove the deliverable path is absent
cd /home/user/webapp
python3 - <<'PY'
from pathlib import Path
from hyperion.output.render import PDFRenderer
r = PDFRenderer()
# an intentionally banned string guarantees an integrity violation
html = "<html><body><p>Insufficient evidence to state implications</p></body></html>"
res = r.render_pdf(html=html, output_path="output/_probe.pdf")
assert not res.success, "audit did not fail on a banned string"
assert not Path("output/_probe.pdf").exists(), "REGRESSION: rejected bytes at deliverable path"
rej = list(Path("output/_rejected").glob("_probe.*.rejected.pdf"))
assert rej, "rejected artifact was not quarantined"
assert list(Path("output/_rejected").glob("_probe.*.violations.txt")), "no violations report"
print("W-02 OK, quarantined:", rej[0])
PY
```

**Acceptance criteria.**
- After a failed audit, `output/<slug>.pdf` does not exist.
- After a failed audit, `output/_rejected/<slug>.<ts>.rejected.pdf` exists.
- After a failed audit, a `.violations.txt` sibling lists all violations in full.
- After a passing audit, `output/<slug>.pdf` exists and no staging file remains.
- Both render engines share one finalisation helper.

**Failure modes to avoid.**
- Using `shutil.move` across filesystems and losing atomicity. Stage inside the same
  directory as `output_path` so `os.replace` is atomic.
- Deleting the rejected bytes. Debuggability dies and the next audit round is blind.
- Leaving `.staging.pdf` behind on an exception. Wrap in `try/finally`.
- Forgetting `unlink(missing_ok=True)` on the deliverable path. A previous run's clean
  PDF sitting at that path would be presented as the output of this run, which is a
  worse failure than the one being fixed.

**Rollback.** Revert to direct-to-output writing. Do not do this.

---

### W-03: One writer, running last, deriving everything from the document

**Objective.** Make it structurally impossible to author the PDF before its inputs
exist, and impossible for the TOC to disagree with the document.

**Why the last round did not fix this.** P2-05 and P2-06 replaced arithmetic TOC page
numbers with `target-counter(attr(href), page)` at `presentation_designer.py:876-884`,
which is the correct CSS. But the artifact still shows sequential arithmetic numbers
and a TOC entry for a chapter that does not exist. The CSS fix addresses page numbers.
It cannot address a TOC built from the plan's section list rather than from the
document's actual sections. That is a data-flow problem, and it was not touched.

**Files and anchors.**
- `hyperion/agents/engagement_director.py:1087-1125` (delivery TaskNodes)
- `hyperion/orchestrator.py:1913-1956` (delivery loop), `:1962-1966` (pdf_path)
- `hyperion/agents/delivery/presentation_designer.py:3319` (`_generate_pdf` call),
  `:1252-1277` (TOC template), `:3053-3130` (`_generate_pdf`)
- `hyperion/agents/delivery/render_engine.py:989-996` (the audit call)

**Procedure.**

1. **Re-point the DAG edges.** Change `engagement_director.py:1087-1125` to:

   ```
   task_data_visualizer        deps ["task_quality_gate"]
   task_presentation_designer  deps ["task_quality_gate", "task_data_visualizer"]
   task_render_engine          deps ["task_presentation_designer"]
   ```

   The visualizer now runs before the designer, so charts exist as files before any
   HTML references them.

2. **Remove PDF authorship from the designer.** Delete the `_generate_pdf` call at
   `:3319`. The designer's contract becomes: produce the staged HTML and the layout
   plan, and nothing else. Its result type must no longer carry a `pdf_path`.

   This single change eliminates the RC-4 fallback at `orchestrator.py:1966`, because
   there will be no `layout_plan.pdf_path` to fall back to. Delete that `elif` branch
   as well.

3. **The render engine becomes the only writer.** It already holds the audit call at
   `render_engine.py:989-996`. It now also owns: reading the staged HTML, invoking
   `PDFRenderer.render_pdf` (which after W-02 stages and quarantines), and returning
   the finalised path.

4. **Two-pass TOC.** The page numbers of a document are not knowable until the
   document is laid out, so:
   - Pass 1: render the full HTML with the TOC present but page cells empty.
   - Read back the real page index of every anchor from the rendered PDF. WeasyPrint
     exposes the document structure, and PyMuPDF can resolve named destinations from
     the produced file. Either source is acceptable; the requirement is that the number
     comes from the rendered artifact.
   - Pass 2: re-render with the resolved numbers substituted.

   Keep `target-counter` in the CSS as the primary mechanism. The two-pass step is the
   verification and the fallback for the renderer paths where `target-counter` is not
   honoured.

5. **The TOC must be generated from the document, not the plan.** Build the TOC entry
   list by scanning the assembled HTML for heading elements that carry section anchor
   ids. If "Risk Analysis" was suppressed by W-07, it has no heading, so it cannot
   appear in the TOC. This is what makes section 2.4's phantom entry impossible rather
   than merely unlikely.

6. **Add a TOC consistency check to the render-time gate.** `page_audit` already
   compares TOC entries against actual heading pages. Confirm the check runs on the
   two-pass output and that its tolerance is zero pages, not one.

**Verification.**

```bash
# 1. DAG ordering is correct and acyclic
cd /home/user/webapp && python3 - <<'PY'
from hyperion.agents.engagement_director import EngagementDirector
# build a DAG for any question, then assert order
# visualizer must have no dependency on the designer
PY

# 2. the designer no longer writes a PDF
grep -n "_generate_pdf\|PDF_OUTPUT" hyperion/agents/delivery/presentation_designer.py
# expect: no call site, no WEASYPRINT tool acquisition

# 3. charts reach the artifact
python3 - <<'PY'
import fitz
d = fitz.open("output/<slug>.pdf")
charts = sum(1 for p in d for im in p.get_images() if "chart" in str(im).lower())
print("images/page:", [len(p.get_images()) for p in d])
PY

# 4. TOC agrees with the document, zero tolerance
python3 -c "
from hyperion.output.page_audit import audit_pdf
audit_pdf('output/<slug>.pdf')
print('audit clean')
"
```

**Acceptance criteria.**
- `grep -c "render_pdf" hyperion/agents/delivery/presentation_designer.py` is 0.
- The engagement result's `pdf_path` has exactly one source, the render engine.
- A generated report contains at least one chart produced by `DataVisualizer`.
- Zero TOC mismatches at zero page tolerance.
- No TOC entry lacks a corresponding heading in the document.

**Failure modes to avoid.**
- Reordering the DAG but leaving `_generate_pdf` in the designer. Then two components
  write PDFs, and the orchestrator fallback picks whichever ran, which is exactly the
  current bug with a different ordering.
- Keeping the `elif result.layout_plan ... pdf_path` fallback "just in case". That
  fallback is the mechanism by which an unaudited PDF became the deliverable. Delete it.
- Implementing pass 2 by string-replacing page numbers in the PDF. Re-render the HTML.
- Letting pass 2 change page count. Reserve the page cell width in pass 1 so the
  substituted digits cannot reflow the document. Verify page count is identical between
  passes and fail loudly if it is not.

**Rollback.** Revert the DAG edges and restore the designer's `_generate_pdf` call.
This reintroduces RC-3 and RC-4, so rollback is only acceptable as a temporary
mitigation with the shell refusing to run.

---

### W-04: The delivery stage fails closed

**Objective.** A failure in any delivery task ends the engagement without a deliverable,
loudly.

**Why the last round did not fix this.** The previous round audited content quality and
never examined the orchestrator's delivery loop control flow. The `except Exception:
log; continue` at `orchestrator.py:1913-1956` was left intact, and it is what converted
a `DataVisualizer` crash into a silent success.

**Files and anchors.**
- `hyperion/orchestrator.py:1913-1956` (loop), `:1962-1966` (pdf_path selection),
  `:2018-2048` (extraction yield hard-fail, the pattern to copy)

**Procedure.**

1. The extraction-yield code at `:2018-2048` already implements the correct pattern: a
   `zero_evidence_failure` that hard-fails the engagement. Mirror it exactly. Do not
   invent a second style.

2. Classify delivery tasks. `DATA_VISUALIZER`, `PRESENTATION_DESIGNER` and
   `RENDER_ENGINE` are all **required**. There are no optional delivery tasks. If a
   chart cannot be drawn, the report is wrong, not merely plainer.

3. Replace the loop body:
   - On exception in a required task: record a structured `DeliveryFailure` with the
     agent, exception type, and full traceback, then break out of the loop and set
     `result.success = False`, `result.failure_reason = "delivery"`.
   - On unmet dependencies for a required task: that is now an invariant violation
     rather than a normal condition, because W-03 makes the chain linear and W-04 stops
     on the first failure. Raise, do not log and skip.

4. Delete the `elif result.layout_plan ... pdf_path` fallback at `:1966`.

5. The final log line at `:1969-1973` currently reports `PDF=YES/NO`. Make `PDF=NO`
   imply `result.success is False`. Add an assertion to that effect so the two can
   never diverge.

**Verification.**

```bash
# inject a failure into the visualizer and prove the engagement fails
cd /home/user/webapp && python3 - <<'PY'
# monkeypatch DataVisualizer.run to raise, run a short engagement,
# assert result.success is False and result.pdf_path == ""
PY

grep -n "dependencies not met" hyperion/orchestrator.py
# expect: no occurrence in the delivery loop

grep -n "elif result.layout_plan" hyperion/orchestrator.py
# expect: no output
```

**Acceptance criteria.**
- No `except Exception` in the delivery loop that continues to the next task.
- `result.pdf_path` is non-empty if and only if the render engine produced an audited
  PDF.
- `result.success is False` whenever `result.pdf_path` is empty.
- A forced visualizer crash yields a failed engagement with a traceback in the result.

**Failure modes to avoid.**
- Narrowing `except Exception` to a tuple of expected exceptions. The problem is not
  the breadth of the catch, it is the `continue`. A narrow catch that continues is the
  same bug.
- Treating the visualizer as optional "for robustness". That reasoning produced a
  34-page report with zero charts.
- Failing the engagement but leaving the rejected PDF in place. W-02 must be complete
  first, which is why it is ordered before this item.

**Rollback.** Restore the try/except/continue. Only as an emergency, and only with the
provenance banner from W-01 marking the build as degraded.

---

### W-05: Contradiction detection rebuilt around opposition, not inequality

**Objective.** A contradiction is two claims about the same subject with incompatible
values or polarity. Nothing else may be called a contradiction.

**Why the last round did not fix this.** Commit `eb481e0` added "contradiction guards"
(P2-19/20/21) in the fact checker. Those guards constrain what the fact checker does
with contradictions. They do not touch `synthesis_lead._identify_contradictions`, which
is where contradictions are manufactured. Guarding the consumer of a broken producer
leaves the 28 fabricated contradictions intact, and they still reached the appendix.

**Files and anchors.**
- `hyperion/agents/synthesis_lead.py:570-627` (`_identify_contradictions`), `:629-640`
  (`_classify_contradiction`), `:680-740` (entrenched resolution), `:817` (budget log)
- `hyperion/agents/delivery/presentation_designer.py:2888`
  (`_build_technical_appendix_html`), `:2979` (Position A/B table header)

**Procedure.**

1. **Delete the inequality predicate.** `content_a != content_b` is removed entirely.
   It has no salvageable role.

2. **Introduce a claim triple.** Before any pairing, normalise each finding into
   `(subject, predicate, value)` where `value` is one of:
   - a numeric measurement with units and a period, or
   - a categorical polarity in `{supports, opposes, neutral}` toward a named
     proposition.
   A finding that cannot be normalised into a triple is **not eligible** for
   contradiction analysis. Log a count of ineligible findings; do not force them.

3. **Pair only on subject identity.** Two triples may be compared only when their
   `subject` matches after normalisation, and their `predicate` matches. Subject
   matching must use entity normalisation, not string equality, and must not use
   substring containment (which would match "India" to "Indian textiles").

4. **Define opposition explicitly.**
   - Numeric: values disagree when their confidence intervals do not overlap, or when
     they differ by more than a declared relative tolerance (start at 15 percent) after
     unit normalisation. Same number in different units is not a contradiction, which
     the current code cannot express at all.
   - Categorical: `supports` versus `opposes` on the same proposition.
   - Temporal guard: two measurements of the same quantity in different periods are
     **not** a contradiction. This alone would have eliminated a large share of the 28.

5. **One field for compare and display.** This is the specific defect that produced
   the user's screenshot. Introduce a single `claim_text` on the contradiction record
   that is both the compared text and the rendered text. Assert in a unit test that
   the rendered Position A string is the same string the detector compared. Make
   `finding_a` and `finding_b` carry `claim_text`, and delete any path where a title is
   substituted for content.

6. **Reject telemetry as a claim.** A finding whose title or claim text is a confidence
   enum, an agent name, or a dict repr is ineligible. This overlaps W-09 and should
   ultimately be enforced by the narrative type there, but add the local guard now so
   `Confidence: low` can never again appear as a Position.

7. **Budget guard.** Cap eligible contradictions at a small number (start at 5) ranked
   by materiality to the main question. `:817` shows the deep-dive budget being
   exhausted; with a real predicate the count collapses naturally, but the cap prevents
   a regression from consuming the budget again.

8. **Appendix rendering.** At `presentation_designer.py:2979`, the table must render
   `claim_text` for both positions plus the source count and resolution basis. If zero
   contradictions survive, **omit the table and the section**, do not render an empty
   one.

**Verification.**

```bash
cd /home/user/webapp && python3 - <<'PY'
from hyperion.agents.synthesis_lead import SynthesisLead
# Case 1: identical titles, different bodies, no shared subject -> 0 contradictions
# Case 2: "India manufacturing share 17% (2023)" vs "India manufacturing share 14% (2015)"
#         -> 0 contradictions (temporal guard)
# Case 3: "$1.2B" vs "$1200M" -> 0 contradictions (unit normalisation)
# Case 4: "supports tariff" vs "opposes tariff", same proposition -> 1 contradiction
# Case 5: finding titled "Confidence: low" -> ineligible, never a Position
PY

# the compare/display invariant
grep -n "finding_a\s*=" hyperion/agents/synthesis_lead.py
# expect: assigned from claim_text, never from entry["title"]

# artifact check
python3 -c "
from hyperion.output.page_audit import extract_pdf_text
t = extract_pdf_text('output/<slug>.pdf')
assert 'Confidence: low' not in t
assert '{\'' not in t
print('OK')
"
```

**Acceptance criteria.**
- Five unit cases above pass.
- `finding_a` and `finding_b` are never assigned from a title field.
- Contradiction count on a real engagement is in single digits.
- Zero `Confidence: low` and zero `{'` in the generated PDF text.
- Zero-contradiction runs omit the section entirely.

**Failure modes to avoid.**
- Implementing subject matching with `in` or `startswith`. Substring containment
  creates false pairs across related entities.
- Keeping the old detector behind a flag. Two detectors means the old one runs in
  production eventually.
- Filtering `Confidence: low` at render time only. That is the RC-9 pattern: the string
  disappears but the fabricated contradiction still burns the sub-agent budget and
  still distorts the synthesis.
- Comparing numbers without normalising units and periods first. Most apparent
  numeric contradictions in research corpora are unit or vintage mismatches.

**Rollback.** The new detector is a replacement, not an addition. Rolling back means
restoring fabricated contradictions, so treat this as forward-only.

---

### W-06: Engagement scope layer, subject ontology gates the roster

**Objective.** No agent is dispatched unless its analytical method fits the subject of
the question. This is the fix for the DCF on a country.

**Why the last round did not fix this.** The previous round attacked the symptom from
the retrieval side (`6114455` subject recall strategy switch, `a33f4e6` definitional
detector). Better retrieval for a financial analyst asking about India's WACC still
produces nothing, because the question is malformed. The roster was never questioned.
`QUESTION_TYPE_AGENTS` at `engagement_director.py:236-277` is unchanged and still puts
`FINANCIAL_ANALYST` in all six question types.

**Files and anchors.**
- `hyperion/agents/engagement_director.py:236-277` (`QUESTION_TYPE_AGENTS`), `:604-646`
  (`_classify_question_heuristic`), `:666-802` (`_classify_question_llm`), `:1087-1125`
  (DAG construction)

**Procedure.**

1. **Introduce a second classification axis.** Today there is one axis, question type
   (the grammatical form). Add subject class:

   | Subject class | Examples | Meaningful methods |
   |---|---|---|
   | `COMPANY` | a firm, a business unit | DCF, EV/EBITDA, unit economics, competitor matrix, NPS |
   | `NATION_OR_REGION` | India, ASEAN, Bavaria | macro indicators, trade balance, policy comparison, cross-country benchmark |
   | `TECHNOLOGY` | solid state batteries | maturity curve, cost curve, patent landscape, adoption S-curve |
   | `POLICY` | a tariff, a subsidy scheme | incidence analysis, counterfactual, comparable-jurisdiction evidence |
   | `MARKET` | the EV market in Europe | sizing, segmentation, growth decomposition, share concentration |
   | `PERSON_OR_ORG` | a regulator, an individual | track record, stated positions, network |

   The roster is then a function of `(question_type, subject_class)`, not of
   `question_type` alone.

2. **Method eligibility, not agent exclusion.** Do not simply drop the financial
   analyst for `NATION_OR_REGION`. Give each agent a declared set of methods and a
   declared set of subject classes each method applies to. `FINANCIAL_ANALYST` retains
   DCF for `COMPANY`, and gains fiscal-cost and public-investment analysis for
   `POLICY` and `NATION_OR_REGION`. An agent with no eligible method for the subject
   class is not dispatched. This is better than a hardcoded exclusion table because it
   states the reason, and the reason is what belongs in the methodology section
   (W-10).

3. **Classification must abstain.** `_classify_question_llm` at `:666-802` returns a
   type. Extend it to return `(question_type, subject_class, confidence)`. When subject
   class confidence is low, do not guess. Ask the user one clarifying question in the
   shell before building the DAG. A thirty second clarification is cheaper than a
   thirty minute engagement that produces six empty chapters.

4. **Record the roster decision.** For every agent considered, store
   `(agent, method, subject_class, eligible, reason)`. This structure is the input to
   the real methodology section in W-10 and to the scope statement in the report. It is
   also the audit trail that makes a future DCF-on-a-country immediately traceable.

5. **Sanity assertion in the DAG builder.** At `:1087-1125`, assert that every
   dispatched agent has at least one eligible method for the classified subject class.
   Fail the engagement at planning time, before any tokens are spent, if that does not
   hold.

**Verification.**

```bash
cd /home/user/webapp && python3 - <<'PY'
from hyperion.agents.engagement_director import EngagementDirector
d = EngagementDirector.__new__(EngagementDirector)
q = "Should India increase manufacturing?"
# assert subject_class == NATION_OR_REGION
# assert AgentName.CONSUMER_INSIGHTS not in roster
# assert AgentName.COMPETITIVE_INTEL not in roster
# assert no roster entry whose method is DCF or EV/EBITDA
q2 = "Should Acme Corp increase manufacturing?"
# assert subject_class == COMPANY
# assert FINANCIAL_ANALYST in roster with method DCF
PY
```

**Acceptance criteria.**
- A country-scoped question dispatches zero agents whose only methods are firm-level.
- A company-scoped question still dispatches the financial analyst with DCF.
- Every dispatched agent has a recorded eligible method and a recorded reason.
- Low subject-class confidence triggers a clarifying prompt, not a default.
- Fewer than two chapters return zero findings on a real engagement (down from six).

**Failure modes to avoid.**
- Implementing subject class with keyword matching on country names. Use the LLM
  classifier with a strict schema and an abstain path. Keyword lists will misclassify
  "Should Tata expand in India" as `NATION_OR_REGION`.
- Adding subject class but leaving `QUESTION_TYPE_AGENTS` as the actual roster source.
  Then the new axis is decorative. Delete the single-axis table.
- Dropping agents silently. Every exclusion must be recorded with a reason, because
  W-10 needs to state it and the user needs to see that the omission was deliberate.
- Letting the clarifying question block a non-interactive run. Provide a documented
  default behaviour for scripted runs: abstain and fail, never guess.

**Rollback.** Restore the single-axis table. This reintroduces RC-6 directly.

---

### W-07: Evidence insufficiency becomes a decision with four outcomes

**Objective.** Replace terminal filler prose with an escalation ladder that ends in
either evidence or an explicit, single, well-placed declaration of scope limits.

**Why the last round did not fix this.** Two commits aimed at this. `d1e472a` added a
"3-round gap-closure ladder" and `5b9fe60` made "thin evidence trigger retrieval
escalation, not a stop". Both operate on the assumption that the sub-question is
answerable and the retrieval was unlucky. In the RC-6 case the sub-question is
unanswerable because the agent should never have asked it, so the ladder runs three
rounds, finds nothing three times, and then emits the filler anyway. The artifact
proves the outcome: 32 `Confidence: low` occurrences and the "Insufficient evidence to
state implications" string that P2-16 was supposed to make unconstructible.

**Files and anchors.**
- `hyperion/orchestrator.py:2018-2048` (extraction yield, `zero_evidence_failure`)
- `hyperion/schemas/models.py` (`AnalysisGap`, introduced by `c5c41a2`)
- `hyperion/tools/searxng.py:359-390` (`_search_with_rotation`), `:531-609` (`search`)
- `hyperion/tools/deep_search.py:594-603` (grounding guard), `engagement_yield_report()`

**Procedure.**

1. **Name the four outcomes as a type.** An insufficiency event resolves to exactly one
   of:

   | Outcome | Meaning | Effect on the report |
   |---|---|---|
   | `RETRY_STRATEGY` | same question, different query construction | none, retrieval continues |
   | `RETRY_SCOPE` | broadened entity, period, or geography | none, retrieval continues, scope change recorded |
   | `OUT_OF_SCOPE` | the sub-question is not answerable for this subject | section removed, one line in scope note |
   | `DECLARED_GAP` | answerable, genuinely under-documented | section retained, gap stated with what was searched |

   The current code can only express a degenerate form of the fourth. `OUT_OF_SCOPE` is
   the outcome the six misfit chapters needed, and it does not exist today.

2. **Make the strategies concrete and enumerable.** `RETRY_STRATEGY` must change
   something specific and record what it changed:
   - query form: natural question, keyword conjunction, exact-phrase, entity plus
     metric, site-scoped to a known authority domain
   - engine set: reliable pool, standby pool, category route (see W-11, which must be
     complete or these routes are dead)
   - language and locale variation where the subject is non-Anglophone
   - time window: unbounded, last 3 years, last 10 years, specific year
   A strategy is not permitted to repeat a `(query_form, engine_set, window)` triple
   that already returned zero. Log the triples tried. This is the direct answer to
   "when we dont have enough data why dont we trying again with different settings".

3. **Ladder with an explicit budget.** Per sub-question: up to 3 `RETRY_STRATEGY`, then
   up to 2 `RETRY_SCOPE`, then classify as `OUT_OF_SCOPE` or `DECLARED_GAP`. The
   classification is a judgement the LLM makes with the tried-triples log in context,
   and it must justify the choice in one sentence that is retained.

4. **Section suppression.** `OUT_OF_SCOPE` removes the section from the report
   entirely. No heading, no placeholder, no TOC entry. W-03 step 5 makes the TOC follow
   automatically. The scope note carries one consolidated statement, for example: "This
   engagement does not include firm-level valuation or consumer research, because the
   subject is a national policy question." That is one sentence replacing 32
   occurrences of `Confidence: low`.

5. **Declared gaps must be specific.** A `DECLARED_GAP` states the question, the
   strategies attempted, and what source would resolve it. "Insufficient evidence" is
   banned and already in `BANNED_SUBSTRINGS`. A specific gap is a legitimate research
   finding; a vague one is filler.

6. **Confidence is never prose.** `derive_confidence` is already the single source of
   confidence per `9095194` (P2-15). Enforce that confidence renders only as a
   structured field in a defined UI position, never as a heading, title, or sentence.
   W-09 provides the type that makes this structural.

**Verification.**

```bash
cd /home/user/webapp && python3 - <<'PY'
# 1. strategy non-repetition
# assert the ladder never retries an already-zero (query_form, engine_set, window)
# 2. outcome coverage
# assert all four outcomes are reachable in unit tests
# 3. suppression
# feed a sub-question classified OUT_OF_SCOPE, assert no heading in the assembled HTML
PY

python3 - <<'PY'
from hyperion.output.page_audit import extract_pdf_text
t = extract_pdf_text("output/<slug>.pdf")
for bad in ("Insufficient evidence", "Confidence: low", "requires additional research"):
    assert bad not in t, bad
print("W-07 text OK")
PY
```

**Acceptance criteria.**
- Zero occurrences of `Confidence: low` in client prose.
- Zero occurrences of "Insufficient evidence" and "requires additional research".
- Every retried sub-question has a logged list of distinct strategy triples.
- At least one strategy escalation observably changes the engine set or time window.
- `OUT_OF_SCOPE` sections are absent from the document and from the TOC.
- The scope note contains a single consolidated exclusion statement.

**Failure modes to avoid.**
- Implementing retries that re-issue the same query to the same engines. That is a
  loop, not a ladder, and it looks identical in the logs unless triples are recorded.
- Building the ladder before W-11. Half the category routes point at unregistered
  engines, so `RETRY_STRATEGY` would exhaust its budget on dead engine sets.
- Using `OUT_OF_SCOPE` as a convenience for anything hard. It is reserved for
  subject-class mismatch. `DECLARED_GAP` is the honest answer for a genuinely thin topic.
- Replacing the filler string with a different filler string. The gate bans the current
  phrasings; the requirement is that no section exists with nothing to say.

**Rollback.** Not advisable. Reverting restores terminal filler.

---

### W-08: The Quality Gate can refuse to ship

**Objective.** Separate "cannot improve further" from "acceptable to deliver", and make
the second an actual gate.

**Why the last round did not fix this.** The previous audit identified the quality loop
reading the wrong field (its section 0.1) and `e68e4d2` added a corpus floor as an
integrity blocker. But `MAX_QUALITY_ITERATIONS = 2` and the unconditional
`max_iterations_reached = True` path at `orchestrator.py:1466-1471` survived, and that
path marks the gate task COMPLETED so delivery proceeds. A blocker that an escape hatch
routes around is not a blocker.

**Files and anchors.**
- `hyperion/orchestrator.py:239` (`MAX_QUALITY_ITERATIONS`), `:1389-1471` (quality loop),
  `:1466` (the log line), `:1470-1471` (`max_iterations_reached`)
- `hyperion/agents/support/quality_gate.py` (`APPROVAL_THRESHOLD`, `:1383`, `:1511`,
  `:1533`, `:1562`, `:1581`), `_detect_hard_blockers()`
- `hyperion/agents/delivery/presentation_designer.py:3038-3043` (the escalation escape
  hatch)
- `hyperion/schemas/models.py:2396` (`threshold: float = 4.0`)

**Procedure.**

1. **Three terminal states, not two.** Replace the boolean `approved` with:
   - `APPROVED`: score at or above threshold, no hard blockers. Ships normally.
   - `SHIP_WITH_CAVEAT`: score below threshold but above a floor, no hard blockers.
     Ships **only** with a prominent limitations page and a visible confidence
     statement on the cover. Requires an explicit setting to be enabled.
   - `BLOCKED`: any hard blocker, or score below the floor. **Does not ship.** The
     engagement ends with a diagnostic report for the operator, not a client PDF.

   The run under audit scored 2.15 with five critical dimensions failing. Under any
   sane floor that is `BLOCKED`.

2. **Delete `max_iterations_reached` as a ship condition.** Iteration exhaustion sets a
   field for diagnostics. It must not appear in the expression that decides whether
   delivery runs. Grep for every read of it and confirm none is in a shipping decision.

3. **Raise the iteration cap and make it useful.** Two iterations is too few to fix
   sixteen gaps. Raise to 4 with a wall-clock budget so it cannot run away, and require
   each iteration to change something measurable. An iteration that produces no score
   change on any dimension terminates the loop early and escalates to `BLOCKED`, because
   looping without improvement is the signal that the input is the problem, not the
   polish.

4. **Close the designer escape hatch.** At `presentation_designer.py:3038-3043` the
   `approved` check was converted into "proceeding with best report (escalation)".
   Delivery must not evaluate quality at all. The orchestrator decides; the designer
   either receives a report to lay out or is never invoked. Remove the check rather than
   repairing it, because a second quality decision point is a second escape hatch.

5. **Operator diagnostic on BLOCKED.** Emit a machine-readable failure report:
   dimension scores, hard blockers, open gaps with their W-07 outcomes, corpus
   statistics, and the roster decisions from W-06. This is what makes a blocked run
   actionable instead of merely disappointing.

**Verification.**

```bash
cd /home/user/webapp && grep -n "max_iterations_reached" hyperion/ -r
# expect: written for diagnostics, read only in reporting, never in a ship condition

grep -n "approved" hyperion/agents/delivery/presentation_designer.py
# expect: no output

python3 - <<'PY'
# construct a QualityScore with total 2.15 and 5 critical dimensions failing
# assert terminal state is BLOCKED
# assert the orchestrator does not invoke the delivery stage
# assert no PDF exists at the deliverable path
PY
```

**Acceptance criteria.**
- A 2.15/4.0 score with critical dimension failures produces no client PDF.
- `max_iterations_reached` appears in no shipping condition.
- The designer contains no quality evaluation.
- A blocked run writes an operator diagnostic containing dimension scores and blockers.
- `SHIP_WITH_CAVEAT` is off by default and, when on, forces a limitations page.

**Failure modes to avoid.**
- Lowering the threshold so more runs pass. The threshold is not the problem; the
  bypass is.
- Making `BLOCKED` produce an HTML fallback deliverable. Then the user opens the HTML
  and we are back to shipping garbage under a different extension.
- Leaving the designer's quality check in place "as defence in depth". Two decision
  points on the same question is how the current escape hatch was introduced.
- Raising the iteration cap without a wall-clock budget. A 34 minute engagement becomes
  a two hour one.

**Rollback.** Re-enable `SHIP_WITH_CAVEAT` by default as an intermediate step if
`BLOCKED` proves too strict in practice. Never restore the unconditional path.

---

### W-09: A narrative type that structurally cannot hold telemetry

**Objective.** Make it impossible, at the type level, for agent names, confidence
enums, dict reprs, and verification states to appear in client prose.

**Why the last round did not fix this.** P2-09 cleaned dict reprs, P2-12 demoted the
fact checker, P2-32 banned em dashes, and `page_audit.BANNED_SUBSTRINGS` catches the
rest. Every one of those is a string filter applied after the leak. The artifact shows
24 dict reprs, 4 `Fact Checker`, 4 `allucinat` and 79 em dashes, which is what happens
when the filters are the only defence and the filter never runs (RC-1, RC-2). Filters
are a backstop. The design needs a boundary.

**Files and anchors.**
- `hyperion/output/page_audit.py` (`BANNED_SUBSTRINGS`, keep as backstop)
- `hyperion/agents/delivery/presentation_designer.py:2680` (escaped-HTML handling),
  `:2888` (technical appendix), `:2979` (Position A/B)
- `hyperion/output/render.py:202` (`_clean_dict_repr`), `:245` (`_markdown_to_html`)
- `hyperion/schemas/models.py` (new narrative types)

**Procedure.**

1. **Define `ClientProse` as a distinct type.** A frozen value object constructed only
   through a validating factory. The factory rejects, by raising:
   - any `{` followed by `'` or `"` (dict repr)
   - U+2014 and U+2013
   - any string in the agent-name registry
   - any confidence enum literal
   - any verification-state literal, including `UNVERIFIABLE`, `hallucinat`,
     `unverified claim`
   - any gap identifier pattern
   Every client-facing template field takes `ClientProse`, never `str`.

2. **Split the report model in two.** `FinalReport` currently carries both narrative and
   telemetry. Separate them:
   - `ClientReport`: sections built exclusively from `ClientProse` and typed exhibits
   - `EngagementTelemetry`: agent findings, confidences, verification states, gaps,
     roster decisions, contradiction records, corpus statistics
   The client template may reference `ClientReport` only. Enforce with a test that
   asserts the Jinja environment for client templates has no access to telemetry
   attributes.

3. **Telemetry gets its own destination.** Write `EngagementTelemetry` to a separate
   operator artifact (JSON plus an optional operator PDF). This is where "149
   hallucinated citations detected" belongs, and it is genuinely valuable there. The
   problem was never that the fact checker found something; it is that the finding was
   addressed to the client.

4. **Transformation is explicit.** Where telemetry legitimately informs client prose,
   for example a verification pass rate in the methodology (W-10), write a named
   transformation function that takes telemetry and returns `ClientProse`. Named,
   tested, and reviewable, rather than a serialised object landing in a template.

5. **Keep the render-time backstop.** `BANNED_SUBSTRINGS` stays exactly as it is. After
   this item it should never fire. If it fires, a transformation is missing, and that is
   precisely the signal we want.

**Verification.**

```bash
cd /home/user/webapp && python3 - <<'PY'
from hyperion.schemas.models import ClientProse
import pytest
for bad in ("{'a': 1}", "em\u2014dash", "Fact Checker", "Confidence: low",
            "hallucinated", "UNVERIFIABLE"):
    try:
        ClientProse.of(bad)
    except ValueError:
        continue
    raise AssertionError(f"ClientProse accepted telemetry: {bad!r}")
print("ClientProse rejects telemetry")
PY

# no client template can reach telemetry
python3 -m pytest tests/ -k "client_template_isolation" -q
```

**Acceptance criteria.**
- `ClientProse.of()` raises on all six categories above.
- Client templates cannot resolve any telemetry attribute.
- Telemetry is written to a separate operator artifact.
- `BANNED_SUBSTRINGS` does not fire on a generated report.
- Zero em dashes, zero dict reprs, zero agent names in the client PDF.

**Failure modes to avoid.**
- Making `ClientProse` a `NewType` alias over `str`. It must validate at construction
  or it is documentation.
- Sanitising instead of raising. Silently stripping a dict repr hides the upstream bug
  that produced it, and the next leak takes a different shape.
- Leaving one template field as raw `str` "temporarily". That field becomes the leak.
- Deleting the fact checker's findings instead of rerouting them. They are useful; they
  are simply not client copy.

**Rollback.** The types are additive until templates are switched over. Switch back
per-template if needed, but the leak returns with each one.

---

### W-10: A methodology section that describes method

**Objective.** Replace the agent list with a defensible account of how the research was
conducted.

**Why the last round did not fix this.** It was not in scope for the previous round.
`presentation_designer.py:1448-1477` still emits Agents Used, Sources Accessed count,
Data Points count, Limitations.

**Files and anchors.**
- `hyperion/agents/delivery/presentation_designer.py:1448-1477`
- Inputs now available: W-06 roster decisions, W-07 strategy triples and outcomes,
  fact checker verification statistics, corpus statistics

**Procedure.**

Replace the four bullets with six subsections, each sourced from a real structure
rather than a count:

1. **Question decomposition.** The main question, the sub-questions derived from it, and
   which sub-questions were answered, declared as gaps, or excluded as out of scope.
   Source: the DAG plus W-07 outcomes.

2. **Scope and method selection.** The classified subject class and, for each
   analytical method applied, one sentence on why it fits. Also, for each method
   deliberately excluded, one sentence on why not. This is where the user's DCF
   question gets its answer permanently: the report will state that firm-level valuation
   was excluded because the subject is a nation state. Source: W-06 roster decisions.

3. **Retrieval strategy and coverage.** The engine pools used, the number of distinct
   queries, the number of distinct source domains, the date range of sources, and the
   strategy escalations that were triggered. Source: W-07 triples and corpus statistics.

4. **Source inclusion and exclusion criteria.** What was accepted as evidence and what
   was rejected, with counts. `a33f4e6` already added reference-work denial and a real
   `classify_source_type`, so the data exists.

5. **Verification procedure.** How claims were checked, the pass rate, and how
   unverifiable claims were handled. This is the legitimate home for fact checker
   output, transformed through W-09 rather than pasted. Stated as method and rate, never
   as an alarm.

6. **Limitations of the design.** Structural limits: no primary research, no paywalled
   sources, English-language bias where applicable, recency cut-off. Distinct from the
   evidence gaps in item 1, and honest about what this kind of engagement cannot do.

**Verification.**

```bash
cd /home/user/webapp && python3 - <<'PY'
from hyperion.output.page_audit import extract_pdf_text
t = extract_pdf_text("output/<slug>.pdf")
i = t.find("Methodology")
sec = t[i:i+6000]
for required in ("Question decomposition", "Scope and method selection",
                 "Retrieval strategy", "inclusion", "Verification", "Limitations"):
    assert required.lower() in sec.lower(), required
# and no agent names
for agent in ("Financial Analyst", "Fact Checker", "Data Visualizer"):
    assert agent not in sec, agent
print("W-10 OK")
PY
```

**Acceptance criteria.**
- All six subsections present.
- Zero agent names in the methodology section.
- Every excluded method has a stated reason.
- Retrieval coverage cites distinct query count and distinct domain count.
- Verification is stated as a rate, not as a warning.

**Failure modes to avoid.**
- Keeping "Agents Used" as a seventh subsection. It is internal telemetry; W-09 forbids
  it in client prose, and it is what the user objected to.
- Filling the six subsections with counts only. A count is not a method. Each needs a
  narrative sentence.
- Generating this section with a free-form LLM prompt. Build it from the recorded
  structures, so it cannot describe research that did not happen.

**Rollback.** Restore the four bullets. This is the least risky item to revert and the
least valuable to revert.

---

### W-11: Reconcile the engine registry and make drift impossible

**Objective.** Every engine identity referenced in code must exist in the running
SearXNG, and a mismatch must fail at boot rather than silently narrowing the corpus.

**Why the last round did not fix this.** `d8a8687` created the "6-engine pool with
standby rotation on zero results" and `e68e4d2` added a corpus floor. The pool was
defined in Python without checking `searxng_settings.yml`, which sets
`use_default_settings: false`. So the standby pool was written against engines that do
not exist in the deployment, and the corpus floor then fires on the resulting thin
corpus, which looks like a search-quality problem rather than a configuration bug.

**Files and anchors.**
- `hyperion/tools/searxng.py:473` (`RELIABLE_ENGINES`), `:480` (`STANDBY_ENGINES`),
  `:484-490` (`CATEGORY_ENGINES`), `:359-390` (`_search_with_rotation`), `:586` (engine
  health log)
- `searxng_settings.yml` (12 engines declared, `use_default_settings: false`,
  duplicate `doi_resolvers`, `limiter: false`, placeholder `secret_key`)

**Procedure.**

1. **Adopt a no-block engine policy and rebuild the pool from it.** The governing rule,
   set by the operator: **no engine that CAPTCHAs, bans, or rate-bans a residential IP
   may be in the pool at all.** That decides every case below, and it deletes the
   "register `google`, accept the CAPTCHA risk" tradeoff outright.

   Engines sort into three tiers by how they answer a request:

   **Tier A, documented APIs. Cannot CAPTCHA, because there is no HTML and no bot
   challenge.** These are the pool.

   | Engine | Backing | Notes |
   |---|---|---|
   | `wikipedia` | MediaWiki API | already registered, keep |
   | `wikidata` | MediaWiki API | add, structured facts and identifiers |
   | `arxiv` | arXiv API | already registered, keep |
   | `crossref` | Crossref REST | add, DOI metadata for every citation |
   | `openalex` | OpenAlex REST | add, open scholarly graph, generous limits |
   | `pubmed` | NCBI E-utilities | add, only route to medical literature |
   | `semantic scholar` | S2 REST | add, set an API key, unauthenticated share a global bucket |
   | `github` | GitHub REST | already registered, set a token, 60/hr becomes 5000/hr |
   | `stackexchange` | Stack Exchange API | add, replaces `stackoverflow`, which is the HTML scraper |
   | `hackernews` | Algolia API | already registered, keep |
   | `openstreetmap` | Nominatim | add, geographic resolution |

   **Tier B, independent crawlers with their own index that do not challenge clients.**
   These carry general web recall.

   | Engine | Notes |
   |---|---|
   | `mojeek` | already registered, own index, the most tolerant general engine available, has an official API |
   | `marginalia` | add, own index, non-commercial, explicitly welcomes automated clients |
   | `brave` | already registered, own index, has an official API with a free tier; prefer the API over HTML |
   | `yep` | add if present in the pinned image, own index |
   | `wiby` | optional, small index, useful for long-tail documents |

   **Tier C, removed. Every one of these either CAPTCHAs directly or proxies an engine
   that does.**

   | Engine | Disposition | Reason |
   |---|---|---|
   | `duckduckgo` | **remove from settings and code** | the engine in the operator's log: CAPTCHA storm then `HTTP error 403 (suspended_time=86400)` |
   | `duckduckgo news` | **remove from code** | same upstream, same block |
   | `google` | **do not register** | CAPTCHAs residential IPs aggressively; this reverses the earlier recommendation in this document |
   | `google scholar` | **do not register** | same upstream challenge; `crossref`, `openalex`, `semantic scholar` and `pubmed` cover scholarly search through APIs instead |
   | `startpage` | **remove from settings** | proxies Google, inherits the challenge, and is the most frequently broken engine in SearXNG |
   | `ecosia` | **remove from settings** | Google and Bing backed, inherits the block |
   | `bing` | **remove from settings** | HTML scraper, rate-bans by IP; the `news` route moves to Tier A and Tier B sources |
   | `bing news` | **remove from code** | same upstream |
   | `qwant` | **remove from settings** | returns 429 under modest load and needs per-locale handling |
   | `swisscows` | **remove from settings** | Bing backed, inherits the block |
   | `stackoverflow` | **replace with `stackexchange`** | the former is the HTML scraper, the latter is the API |

   So `CATEGORY_ENGINES` is rebuilt against Tier A and Tier B only:

   ```python
   RELIABLE_ENGINES = "wikipedia,wikidata,mojeek,marginalia,brave"
   STANDBY_ENGINES  = "yep,wiby"
   CATEGORY_ENGINES = {
       "science": "arxiv,crossref,openalex,semantic scholar",
       "medical": "pubmed,openalex",
       "it":      "github,stackexchange,hackernews",
       "geo":     "openstreetmap,wikidata",
       "news":    "mojeek,marginalia",     # no Bing or DDG news; see the honesty note
   }
   ```

   **Be honest about the cost of this policy.** Removing Google, Bing and DuckDuckGo
   removes the three largest general web indexes. Mojeek, Marginalia and Brave together
   index a small fraction of what Google does, so **general web recall drops
   substantially**. What is gained is that recall becomes *predictable*: it never
   collapses to zero mid-engagement, which is what actually happened in the audited run.

   Two consequences follow, and both are already in this document rather than being new
   asks:
   - The scholarly and reference Tier A engines become the primary evidence source, which
     suits research questions and suits the subject class in the audited artifact well.
   - **W-14 is promoted from a nice-to-have to a requirement.** Gemini grounding queries
     Google's index server-side with no IP exposure, so it is now the only route to
     large-index general web coverage. The direct authority fetch in W-14 step 7 becomes
     the primary source for macroeconomic data rather than a supplement.

   Net effect: the pool never gets blocked, and the recall that the pool gives up is
   recovered through a channel that cannot be blocked either. That is the robust
   configuration; a pool containing Google is not robust, it is merely wider until it
   is banned.

2. **Fix the settings file hygiene issues.**
   - Remove the duplicate `doi_resolvers` key. In YAML the second silently wins, so the
     first block is dead configuration and nobody can tell which is in effect.
   - `secret_key` must not remain `hyperion-searxng-secret-change-me`. Generate one per
     instance and source it from the environment.
   - `server.limiter: false` is correct for a private single-user instance, but the
     `X-Forwarded-For nor X-Real-IP header is set!` error in the user's log comes from
     the bot-detection path. Set `X-Forwarded-For` and `X-Real-IP` on requests sent
     from `hyperion/tools/searxng.py`, or disable the botdetection module explicitly for
     a loopback-only deployment. Either removes the log noise; do not leave it, because
     noise in this exact subsystem is what hid the corpus collapse.

2a. **Configure outbound behaviour so the pool stays welcome.** The no-block policy in
   step 1 removes the engines that punish clients. This step is what stops HYPERION from
   earning a block on the engines that remain, which is the second half of "robust". All
   of these are `outgoing` and per-engine keys in the settings file:

   ```yaml
   outgoing:
     request_timeout: 6.0
     max_request_timeout: 12.0
     pool_connections: 20        # not 100; this is a single-user instance
     pool_maxsize: 10
     enable_http2: true
     keepalive_expiry: 30
   ```

   Then, per engine, set an explicit `timeout` and a conservative `weight`, and for every
   Tier A engine that offers credentials, supply them. An authenticated API request is
   both faster and immune to the shared anonymous bucket:

   | Engine | Credential | Effect |
   |---|---|---|
   | `github` | personal access token | 60/hr becomes 5000/hr |
   | `semantic scholar` | free API key | leaves the shared anonymous pool |
   | `brave` | free API key | official API instead of HTML scraping |
   | `crossref` | contact mail in the User-Agent | moves into Crossref's polite pool |
   | `openalex` | contact mail | same, polite pool |
   | `pubmed` | NCBI API key | 3/s becomes 10/s |

   Crossref and OpenAlex both document that a contact address in the User-Agent grants
   access to a better-served request pool. That is the opposite of evading detection: it
   identifies the client honestly and is rewarded for it. Prefer that everywhere it is
   offered.

   Also disable the outbound calls that are pure overhead for a programmatic client:
   `autocomplete: ""`, `image_proxy: false`, and no `default_doi_resolver` lookups on
   paths that do not need one. Each one is an upstream request nobody reads.

2b. **Rate limit outbound requests in HYPERION, not only in SearXNG.** SearXNG's
   `limiter` guards its own inbound surface. Nothing in the current design limits how fast
   HYPERION drives it, and request volume is exactly how the DuckDuckGo suspension was
   earned. Add a token bucket keyed on engine name, shared across all instances, with a
   default of roughly one request every two seconds per engine plus jitter, and a lower
   rate for any engine whose terms ask for it. The bucket must be shared process-wide, or
   three instances will each honour a limit that collectively is three times too fast.

3. **Add a boot-time reconciliation check.** SearXNG exposes its configuration at
   `/config`. At container readiness, fetch it, extract the set of enabled engine names,
   and compare with the union of `RELIABLE_ENGINES`, `STANDBY_ENGINES` and every value
   in `CATEGORY_ENGINES`. Any name in code that is absent in the instance is a **boot
   failure**, not a warning. Print the missing names and the file to edit.

   This check is the item that makes the whole class of defect non-recurring. Config
   drift between a Python constant and a YAML file is inevitable; detecting it at boot
   is cheap.

4. **Make the health tracker distinguish three states.** `:586` currently skips
   "cooled" engines. Add:
   - `HEALTHY`
   - `COOLING`, transient, from a timeout or a 429, retry after an exponential backoff
   - `SUSPENDED`, from `SearxEngineAccessDeniedException` with `suspended_time`, honour
     the full suspension window and record the expiry
   The user's log shows DuckDuckGo moving to a 86400 second suspension. Treating that as
   a short cool-down means every subsequent query pays for a request that cannot succeed.

   After step 1 there should be **no CAPTCHA exceptions at all**, because no engine in the
   pool issues them. So treat `SearxEngineCaptchaException` as a policy violation rather
   than a normal condition: log it at error level, name the engine, and evict that engine
   from the pool for the rest of the process. If it fires, either an engine changed its
   behaviour or a Tier C engine was reintroduced, and both need a human to look.

5. **Surface pool degradation as a first-class signal.** When the count of `HEALTHY`
   engines drops below a floor (start at 4), the engagement must record a retrieval
   degradation event that reaches the quality gate as an input, and reaches the operator
   in the shell. A corpus collapse must never be diagnosable only by reading Docker logs.

**Verification.**

```bash
# 1. reconciliation
cd /home/user/webapp && python3 - <<'PY'
import json, urllib.request
cfg = json.load(urllib.request.urlopen("http://127.0.0.1:8888/config"))
enabled = {e["name"] for e in cfg["engines"] if e.get("enabled", True)}
from hyperion.tools import searxng as S
referenced = set()
for blob in (S.RELIABLE_ENGINES, S.STANDBY_ENGINES, *S.CATEGORY_ENGINES.values()):
    referenced |= {x.strip() for x in blob.split(",") if x.strip()}
missing = sorted(referenced - enabled)
assert not missing, f"engines referenced but not registered: {missing}"
print("registry reconciled,", len(enabled), "engines enabled")
PY

# 2. duplicate key detection
python3 -c "
import yaml,collections
class D(yaml.SafeLoader): pass
def nodup(loader,node,deep=False):
    keys=[loader.construct_object(k) for k,_ in node.value]
    dups=[k for k,c in collections.Counter(keys).items() if c>1]
    assert not dups, f'duplicate keys: {dups}'
    return yaml.SafeLoader.construct_mapping(loader,node,deep)
D.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, nodup)
yaml.load(open('searxng_settings.yml'), Loader=D); print('no duplicate keys')
"
```

**Acceptance criteria.**
- Boot fails with a named list when any referenced engine is unregistered.
- Zero Tier C engines present in any settings file or in any code constant.
- Zero duplicate keys in every settings file.
- `secret_key` is sourced from the environment, distinct per instance.
- `SUSPENDED` engines are not queried until their suspension expires.
- Dropping below the healthy-engine floor produces a visible degradation event.
- Zero `X-Forwarded-For nor X-Real-IP` errors in the container log during an engagement.
- **Zero `SearxEngineCaptchaException` and zero `SearxEngineAccessDeniedException` across
  a full engagement.** This is the headline acceptance test for the whole item, and it is
  measured by grepping the container logs after a real run:

  ```bash
  docker compose logs --no-color | grep -cE "CaptchaException|AccessDeniedException"
  # required: 0
  ```

**Failure modes to avoid.**
- Registering engines in a settings file without restarting the container, then
  concluding the check is broken. Settings are read at container start.
- Making the reconciliation check a warning. A warning is how the current mismatch
  survived a full audit round.
- Reintroducing Google, Bing or DuckDuckGo later "just for recall". That is the decision
  this item exists to reverse, and it will re-earn the 403. Recall is recovered through
  W-14, not through re-adding blocked engines.
- Adding a Tier B engine to a category route without checking it has an index for that
  category. Marginalia is excellent for long-tail documents and poor for current news;
  routing news at it produces empty results, which then feeds W-07's ladder for nothing.
- Skipping the credentials in step 2a. Unauthenticated Semantic Scholar and GitHub share
  global buckets, so a busy engagement will 429 on engines that were supposed to be the
  reliable ones.
- Fixing the code constants to match the YAML by deleting the category routes. That
  removes capability rather than restoring it; the category routes are needed by W-07's
  strategy ladder.

**Rollback.** Downgrade the boot check to a warning temporarily. Keep the reconciliation
report visible.

---

### W-12: Multiple SearXNG instances, and an honest account of what that buys

**Objective.** Increase retrieval throughput and isolate engine failures, sized to a
16 GB Ryzen 7 7840HS.

**Read this before implementing.** The operator's expectation needs one correction,
stated plainly. Three SearXNG containers on one laptop share **one public IP address**.
DuckDuckGo's `SearxEngineCaptchaException` and the subsequent
`HTTP error 403 (suspended_time=86400)` are IP-reputation responses. Running three
replicas behind the same IP does not evade them, and querying the same engine from
three containers concurrently makes the rate limiting **worse**, not better.

That correction is now largely moot, because W-11 step 1 removes every engine that
blocks. With a Tier A and Tier B only pool there is no CAPTCHA to evade, so the replicas
stop being an evasion strategy (which would not have worked) and become a **capacity and
isolation** strategy (which does). The two items must therefore land in the order W-11
then W-12; three replicas configured with the current engine list would triple the
request volume against DuckDuckGo and re-earn the ban three times faster.

So replicas are worth doing, for three reasons:

1. **Engine partitioning.** Give each replica a disjoint engine set. No engine is ever
   queried by two instances, so per-engine request rate is unchanged by adding instances.
   This is the property that makes three instances raise no more suspicion than one.
2. **Concurrency.** Each SearXNG instance serialises work internally. Three instances
   let three agents retrieve in parallel without queueing behind each other.
3. **Configuration experiments.** A replica can carry different settings, which is what
   W-07's `RETRY_STRATEGY` needs when it varies locale or time window.

**Sizing for the stated hardware.** Ryzen 7 7840HS is 8 cores and 16 threads, with
16 GB RAM.

| Service | Instances | Memory limit each | CPU limit each | Port |
|---|---|---|---|---|
| SearXNG `scholar` | 1 | 512 MB | 2.0 | 127.0.0.1:8888 |
| SearXNG `reference` | 1 | 512 MB | 2.0 | 127.0.0.1:8889 |
| SearXNG `web` | 1 | 512 MB | 2.0 | 127.0.0.1:8890 |
| Valkey | 1 | 256 MB | 0.5 | internal only |
| FlareSolverr | 1 | 1024 MB | 2.0 | 127.0.0.1:8191 |

Total ceiling is roughly 2.8 GB, which is comfortable on 16 GB alongside a browser and
an IDE. Do not run more than 3 SearXNG instances: the constraint is upstream engine rate
limits and the number of distinct engine profiles, not local CPU, so a fourth instance
adds contention and no capacity. Do not run more than one FlareSolverr; it drives a
headless Chromium and is the memory-heavy component.

**On Valkey, and a correction to make while implementing.** SearXNG uses Valkey for its
inbound limiter and for a few plugins. It does **not** cache upstream engine results
there, so a shared Valkey does not deduplicate outbound requests between instances by
itself. The result cache that W-14 step 7 calls for therefore belongs in HYPERION's own
client layer, keyed on the normalised query plus engine set, with Valkey as its backing
store. Valkey is also where the shared per-engine token bucket from W-11 step 2b lives,
which is the only way three separate processes can honour one rate limit. Provision it for
those two HYPERION-owned purposes, not on the assumption that SearXNG will use it for
caching.

**On FlareSolverr, after the W-11 engine policy.** Its only job was solving CAPTCHAs, and
the no-block pool has no CAPTCHAs to solve. Keep the container defined but **do not start
it by default**: remove it from the boot path, leave it available behind a setting for a
one-off investigation. That reclaims a full gigabyte and the slowest code path in the
retrieval stack. If FlareSolverr turns out to be needed during normal operation, that is
evidence a Tier C engine crept back into the pool, and the fix is to remove the engine
rather than to solve its challenge.

**Files and anchors.**
- `hyperion/infra/services.py:85-100` (image pins), `:102` (`MANAGED_CONTAINERS`),
  `searxng_spec()`, `flaresolverr_spec()`, `all_specs()`, `SEARXNG_PORT = 8888`
- `docker-compose.yml` (two services; header states tags must match `services.py`)
- `hyperion/tools/searxng.py:531-609` (`search`), `:359-390` (`_search_with_rotation`)
- `hyperion/tui/boot.py:211-240`, `hyperion/tui/app.py:215-244`, `hyperion/cli.py:127-163`

**Procedure.**

1. **Parameterise the container spec.** Replace the single `SEARXNG_PORT` constant with
   a replica descriptor list:

   ```python
   SEARXNG_REPLICAS = (
       # scholarly APIs: heaviest per-query latency, fully authenticated, never blocks
       SearxngReplica(name="hyperion-searxng-scholar", port=8888,
                      profile="scholar",
                      engines=("arxiv", "crossref", "openalex",
                               "semantic scholar", "pubmed")),
       # reference and structured data APIs: fastest, highest volume
       SearxngReplica(name="hyperion-searxng-reference", port=8889,
                      profile="reference",
                      engines=("wikipedia", "wikidata", "openstreetmap",
                               "github", "stackexchange", "hackernews")),
       # independent general-web crawlers: the only HTML-scraping instance
       SearxngReplica(name="hyperion-searxng-web", port=8890,
                      profile="web",
                      engines=("mojeek", "marginalia", "brave", "yep")),
   )
   ```

   Three properties make this partition the robust one:

   - **The engine sets are disjoint**, so per-engine request rate does not scale with the
     number of instances. This is the single most important property; violating it is how
     replicas turn into a ban.
   - **Only one instance scrapes HTML at all.** The `scholar` and `reference` instances
     talk exclusively to documented APIs with credentials attached. If anything is ever
     going to attract attention it is the `web` instance, and it is isolated, rate limited
     hardest, and carries the smallest share of traffic.
   - **The partition matches the latency profile.** Scholarly APIs are slow, reference
     APIs are fast. Mixing them in one instance makes fast queries wait behind slow ones,
     which is the concurrency problem this item is meant to solve.

   Keep all three image tags identical and pinned to the same digest; only settings
   differ. Three different image versions is three different sets of engine behaviours to
   debug, which defeats the purpose.

2. **One settings file per profile, generated from one base.**
   `searxng_settings.scholar.yml`, `.reference.yml`, `.web.yml`, produced by a small
   generator from a single base document plus the profile's engine tuple. One source of
   truth, so the W-11 reconciliation check can validate each instance independently and
   drift between three hand-maintained files is impossible.

   **Per-instance values that must differ, or the instances conflict.** This is the
   checklist that makes three containers coexist cleanly:

   | Setting | Why it must be unique |
   |---|---|
   | `server.secret_key` | shared keys mean shared session and CSRF material across instances |
   | container name | `docker stop` by name, and readable logs |
   | host port | 8888, 8889, 8890 |
   | `SEARXNG_SETTINGS_PATH` | each container mounts only its own profile |
   | named volume | separate caches, so one instance's state cannot corrupt another's |
   | `general.instance_name` | appears in `/config`, makes the reconciliation output legible |
   | Valkey logical DB index | `redis://valkey:6379/0`, `/1`, `/2` if the limiter is ever enabled |

   Everything else stays identical: same image digest, same `outgoing` block from W-11
   step 2a, same timeouts. Divergence anywhere else is a debugging cost with no benefit.

3. **Update `MANAGED_CONTAINERS` and `all_specs()`** to enumerate the three replicas plus
   Valkey plus FlareSolverr. Every place that assumed a single SearXNG must be found by
   grep, not by memory; `SEARXNG_PORT` is the marker and it must cease to exist as a
   scalar.

4. **Endpoint pool in the client.** `hyperion/tools/searxng.py` gains a pool object with:
   - per-endpoint health, reusing the three states from W-11
   - **routing by profile**, driven by an explicit category-to-profile map:
     `science` and `medical` to `scholar`; `it`, `geo` and any reference lookup to
     `reference`; `general` and `news` to `web`
   - least-outstanding-requests selection when more than one endpoint serves a profile
   - a circuit breaker per endpoint, opening after consecutive failures, with the
     shared per-engine token bucket from W-11 step 2b sitting **underneath** the pool so
     that rate limits are enforced per engine regardless of which endpoint is chosen
   - a **fallback ladder across profiles**, not just within one: if the `web` instance is
     unhealthy, a general query may be served by `reference` (Wikipedia and Wikidata
     answer many factual queries), and only then does it escalate to W-14

   Do not implement round-robin over all endpoints. Round-robin sends scholarly queries to
   the web instance, which has no scholarly index, and produces empty results that then
   waste W-07's retry budget.

5. **Update `docker-compose.yml` in the same commit.** The header comment states the
   pins must match `services.py`, and a test asserts it. Per replica add `mem_limit`,
   `cpus`, a healthcheck, `restart: unless-stopped`, and these hardening settings that
   cost nothing and prevent a noisy container from destabilising the laptop:

   ```yaml
   cap_drop: [ALL]
   cap_add: [CHOWN, SETGID, SETUID]
   security_opt: [no-new-privileges:true]
   read_only: true
   tmpfs: [/tmp]
   logging:
     driver: json-file
     options: { max-size: "10m", max-file: "3" }
   ```

   Bind the published ports to `127.0.0.1` explicitly (`127.0.0.1:8888:8080`), not to
   `0.0.0.0`. Three unauthenticated search endpoints reachable from the local network is
   an open proxy for anyone on the same wifi, and it is also the fastest way to have the
   instances abused into a genuine ban. The log rotation matters too: the audited failure
   produced a CAPTCHA storm, and an unbounded json-file log on a laptop is how that fills
   a disk.

6. **Lifecycle.** `tui/boot.py:211-240` brings up all replicas concurrently, not
   sequentially, and waits on all healthchecks with one overall deadline. Report each
   replica's readiness individually in the boot output, so a partial bring-up is visible.
   `tui/app.py:215-244` and `cli.py:127-163` stop all managed containers on quit; this
   already iterates `MANAGED_CONTAINERS`, so extending that tuple is sufficient.

**Verification.**

```bash
cd /home/user/webapp && docker compose up -d
for p in 8888 8889 8890; do
  printf "%s: " "$p"
  curl -s -o /dev/null -w "%{http_code}\n" "http://127.0.0.1:$p/healthz" || echo unreachable
done

# memory actually consumed
docker stats --no-stream --format "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"
# expect: each searxng under 512MiB, total under 3GiB

# profile routing
python3 - <<'PY'
from hyperion.tools.searxng import SearxngPool
pool = SearxngPool.from_config()
assert pool.endpoint_for(category="science").profile == "scholar"
assert pool.endpoint_for(category="it").profile == "reference"
assert pool.endpoint_for(category="general").profile == "web"
# the web instance goes down; general queries degrade to reference, not to nothing
pool.mark_unhealthy(8890)
assert pool.endpoint_for(category="general").profile == "reference"
print("routing and cross-profile fallback OK")
PY

# no engine is served by more than one instance
python3 - <<'PY'
import collections, json, urllib.request
seen = collections.Counter()
for port in (8888, 8889, 8890):
    cfg = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/config"))
    for e in cfg["engines"]:
        if e.get("enabled", True):
            seen[e["name"]] += 1
dupes = {k: v for k, v in seen.items() if v > 1}
assert not dupes, f"engine served by multiple instances: {dupes}"
print("engine sets are disjoint across", len(seen), "engines")
PY

# ports are loopback only, not exposed to the LAN
ss -ltnp 2>/dev/null | grep -E "888[89]|8890"
# every line must show 127.0.0.1, never 0.0.0.0 or *

# compose and services.py agree
python3 -m pytest tests/ -k "compose" -q
```

**Acceptance criteria.**
- Three SearXNG instances healthy on 8888, 8889, 8890 after `hyperion shell` boot.
- **No engine is enabled on more than one instance**, verified by the disjointness check.
- All three ports bind to `127.0.0.1` only.
- Total container memory under 3 GiB at steady state.
- An unhealthy instance removes itself from routing and queries degrade to another
  profile rather than failing.
- Category queries route to the correct profile.
- FlareSolverr is not running after a default boot.
- All instances stop on shell quit, verified by `docker ps`.
- The compose file and `services.py` pins match, existing test passes.
- Zero CAPTCHA or access-denied exceptions in the logs of any instance after a full
  engagement, per the W-11 acceptance test.

**Failure modes to avoid.**
- Implementing W-12 before W-11. Three instances against the current engine list triples
  the request rate at DuckDuckGo and earns the ban faster. Order is not negotiable.
- Overlapping engine sets. It is the one property that turns three helpful instances into
  three times the suspicion, and it is easy to reintroduce by copying a settings file
  instead of generating it.
- Publishing on `0.0.0.0`. Three unauthenticated search endpoints on the local network is
  an open proxy, and abuse by anyone else on that network produces a ban that will look
  inexplicable from inside HYPERION.
- Sharing one `secret_key` across instances because the generator was written to emit one
  base file. Per-instance values are listed in step 2 for exactly this reason.
- Sequential health waits with per-instance timeouts. Three 90 second waits is a 4.5
  minute boot. Wait concurrently under one deadline.
- Expecting instances to solve the DuckDuckGo 403. They will not, and after W-11 there is
  no DuckDuckGo. State both facts in the release note so the expectation does not resurface.
- Hardcoding 8888 anywhere after this change. Grep for it and remove every occurrence.

**Rollback.** Set `SEARXNG_REPLICAS` to a single entry on 8888. The pool abstraction
degrades to one endpoint with no other code change.

---

### W-13: WSL2 and Docker Desktop, correct daemon bring-up and full lifecycle

**Objective.** Starting `hyperion shell` starts the Docker daemon on any supported
platform, brings up every managed container, and stops them on quit, with no manual step.

**Why the last round did not fix this.** It was never raised. `_launch_docker_desktop()`
has Windows and macOS branches and a Linux branch that tries `systemctl` three ways. No
part of the codebase knows WSL2 exists; I confirmed by grepping for `WSL`,
`microsoft-standard`, `/proc/version`, `wslpath` and `interop` with zero hits.

**Files and anchors.**
- `hyperion/infra/services.py`: `_windows_desktop_candidates()`,
  `_macos_desktop_candidates()`, `_launch_docker_desktop()`,
  `ensure_docker_engine()` at `:348`, `docker_available()`, `docker_engine_version()`
- `hyperion/tui/boot.py:211-240`, `:469` (second `ensure_docker_engine`)
- `hyperion/tui/app.py:215-244`, `hyperion/cli.py:124-163`

**Procedure.**

1. **Detect the platform properly.** Add a `Platform` enum resolved once:
   - `WINDOWS`, `MACOS`
   - `WSL2`: `sys.platform == "linux"` and `/proc/version` contains `microsoft` case
     insensitively, or `/proc/sys/kernel/osrelease` contains `WSL`
   - `LINUX_SYSTEMD`: `linux`, not WSL, and `/run/systemd/system` exists
   - `LINUX_OTHER`: everything else
   The current code collapses the last three into one, which is the bug.

2. **WSL2 daemon start.** Under `WSL2`, the daemon is a Windows process, so invoke it
   through interop:
   - Preferred: `powershell.exe -NoProfile -Command "Start-Process 'Docker Desktop'"`.
     Interop makes Windows executables directly callable from WSL.
   - Fallback: launch the resolved `.exe` path directly. Translate the Windows path with
     `wslpath -u` before use. Reuse `_windows_desktop_candidates()` for the candidate
     list and map each through `wslpath`.
   - If `/proc/sys/fs/binfmt_misc/WSLInterop` is absent, interop is disabled. Do not
     retry silently; report that interop must be enabled, with the fix.
   - Detect Docker Desktop's WSL integration being off for this distro: the daemon is
     running on the host but `docker` is not on `PATH` inside the distro. That is a
     distinct failure with a distinct fix (enable integration for the distro in Docker
     Desktop settings), and conflating it with "daemon not running" sends the user in
     circles.

3. **Native Linux daemon start.** Under `LINUX_SYSTEMD`, keep the existing systemctl
   attempts but order them correctly: rootless user service first
   (`systemctl --user start docker`), then system service, and only attempt the system
   service if the user is root or passwordless sudo is available. Probing a command that
   will prompt for a password inside a TUI is a hang, not an error.

4. **Readiness is a probe, not a sleep.** `ensure_docker_engine(wait_seconds=90.0)`
   already polls. Keep it, and additionally poll for the **API** being ready, not just
   the CLI answering, because Docker Desktop under WSL2 answers `docker version` before
   it can create containers. `docker info` returning a server version is the correct
   readiness signal.

5. **Full lifecycle on shell start and quit.** `tui/boot.py:211-240` becomes:
   detect platform, ensure daemon, ensure all specs from W-12 concurrently, then run the
   W-11 reconciliation against each replica. `:469` holds a second
   `ensure_docker_engine` call; make the operation idempotent and cheap when already
   ready, or remove the duplicate.

   On quit, `cli.py:127-163` and `app.py:215-244` already stop `MANAGED_CONTAINERS`.
   Two things to add: stop containers concurrently, and make the Docker Desktop process
   itself **not** be stopped. The user asked for containers to close on quit; killing
   Docker Desktop would disrupt anything else on their machine using it. Document that
   choice explicitly so it is not mistaken for an omission.

6. **Report platform in the boot banner** from W-01, so a future report of "Docker did
   not start" arrives with the detected platform attached.

**Verification.**

```bash
# platform detection
cd /home/user/webapp && python3 -c "
from hyperion.infra.services import detect_platform
print(detect_platform())
"
# on the user's Ubuntu-under-WSL2 laptop: Platform.WSL2
# on a native Ubuntu box with systemd:     Platform.LINUX_SYSTEMD

# end to end, from a cold Docker
# 1. quit Docker Desktop entirely on the Windows host
# 2. run: hyperion shell
# 3. expect: no manual step, banner shows platform, all replicas report healthy
docker ps --format "table {{.Names}}\t{{.Status}}\t{{.Ports}}"

# after quitting the shell
docker ps --format "{{.Names}}"
# expect: no hyperion-managed container listed
docker info --format "{{.ServerVersion}}"
# expect: still answers, Docker Desktop was deliberately left running
```

**Acceptance criteria.**
- `detect_platform()` returns `WSL2` on the user's machine.
- `hyperion shell` starts Docker Desktop from a cold state with no manual step.
- Interop disabled and WSL-integration-off produce two distinct, actionable messages.
- All managed containers are running after boot and gone after quit.
- Docker Desktop itself remains running after quit.
- No systemctl invocation can block on a password prompt.

**Failure modes to avoid.**
- Using `sys.platform` alone. It is `linux` under WSL2, which is the entire bug.
- Assuming `docker.exe` is on `PATH`. It usually is with WSL integration enabled, and
  is absent exactly in the failure case being handled.
- Passing Windows paths to WSL syscalls. Always translate with `wslpath -u`.
- Treating `docker version` success as readiness. Use `docker info` server version.
- Stopping Docker Desktop on quit. That is a hostile side effect on a shared machine.
- Leaving the duplicate `ensure_docker_engine` at `boot.py:469` doing a second 90 second
  wait on a cold start.

**Rollback.** The platform enum defaults to current behaviour for `LINUX_SYSTEMD`, so
reverting the WSL2 branch alone is safe and restores the manual step.

---

### W-14: Gemini search grounding as a first-class retrieval backend

**Objective.** Add a retrieval backend whose failure modes are uncorrelated with
SearXNG's, so an IP-reputation block cannot collapse the corpus.

**Why this is the right lever.** Section 3.9 and the user's Docker log show the real
retrieval risk: a scraping-based meta-search on a residential IP loses engines to
CAPTCHA and 403 suspensions, and W-12 explains why replicas cannot fix that. Gemini
grounding runs server-side at Google, so it has no shared IP reputation with the laptop,
no CAPTCHA surface, and returns the queries it actually issued. It is the only proposed
change that addresses the failure mode rather than working around it.

Confirmed gap: `hyperion/router/providers/google.py` is a plain `GoogleProvider` with no
grounding capability. `hyperion/tools/query_utils.py:605-701` has grounding helpers
including `ground_query_or_raise`, and `deep_search.py:594-603` has a grounding guard,
but these concern claim grounding, not web search grounding. There is no search
grounding anywhere.

**Quota model, as the user documented it.** This determines the whole design:

| Model family | Counting unit | Approximate free allowance |
|---|---|---|
| Gemini 1.5, 2.0 | one grounded API **call** | 1000 to 1500 per day |
| Gemini 3.x | one individual **search query** | about 5000 per month |

The countable unit for 3.x is each entry in `groundingMetadata.webSearchQueries`. That
field is returned in the response, so the true consumption is measurable rather than
estimated. A single grounded 3.x call can issue several searches, so an unmetered
implementation can burn a month of quota in an afternoon. Metering is not optional.

**Files and anchors.**
- `hyperion/router/providers/google.py:27` (`GoogleProvider`), `:38` (`provider_type`)
- `hyperion/tools/searxng.py:531-609` (`search`, the integration seam)
- `hyperion/tools/deep_search.py` (`engagement_yield_report()`)
- New: `hyperion/tools/grounded_search.py`, `hyperion/infra/quota.py`

**Procedure.**

1. **Add grounding to the provider.** Extend `GoogleProvider` with a grounded generation
   method that attaches the Google Search tool, and that returns both the text and the
   parsed `groundingMetadata`: `webSearchQueries`, `groundingChunks` (which carry source
   URIs and titles), and `groundingSupports` (which map text spans to chunks).

   `groundingSupports` is the valuable part and the reason this is more than a search
   API: it gives claim-to-source attribution directly, which feeds the fact checker
   without a separate verification round trip.

2. **Normalise to the existing result type.** `grounded_search.py` converts
   `groundingChunks` into the same result objects SearXNG produces (url, title, snippet,
   engine, plus a `backend="gemini"` marker). Downstream code must not branch on
   backend. If it does, every consumer needs changing and half will be missed.

3. **Build a persistent quota ledger** in `hyperion/infra/quota.py`:
   - store on disk, so a restart does not reset the count
   - record per model family, per day and per calendar month
   - for 3.x, increment by `len(webSearchQueries)` from the actual response, never by 1
   - expose `remaining()` and `reserve(n)`
   - enforce a reserve floor, for example refuse to spend the last 10 percent on
     routine queries so it remains available for a high-value escalation
   - log every spend with the query and the engagement id, so consumption is auditable

4. **Define the routing policy explicitly.** Gemini grounding is scarce and SearXNG is
   effectively free, so:

   | Situation | Backend |
   |---|---|
   | routine breadth retrieval | SearXNG only |
   | SearXNG healthy engines below floor | Gemini, until SearXNG recovers |
   | W-07 `RETRY_STRATEGY` exhausted, before declaring a gap | Gemini, one attempt |
   | claim verification needing attribution | Gemini, for `groundingSupports` |
   | quota below reserve floor | SearXNG only, and record the constraint |

   The third row is the important one. It converts W-07's last resort from "declare a
   gap" into "try a genuinely different retrieval system", which is exactly what the
   user asked for when they said to try again with different settings.

5. **Fail open to SearXNG, never the reverse.** A Gemini error, a quota exhaustion, or a
   safety refusal must fall back to SearXNG and record the event. Gemini must never
   become a hard dependency of an engagement, because its quota is a monthly cliff.

6. **Report backend mix in the methodology.** W-10 item 3 states retrieval coverage. Add
   the per-backend query counts. A reader is entitled to know that part of the evidence
   came from a grounded model rather than a crawled index.

7. **Additional robustness measures worth taking, in priority order.**
   - **Prefer API-based engines** in the reliable pool, per W-11. `semantic scholar`,
     `crossref`, `openalex`, `wikidata` and `arxiv` do not CAPTCHA.
   - **Persistent result cache** keyed on the normalised query plus engine set, in the
     shared Valkey from W-12. Retries in W-07 must not re-pay for identical queries, and
     an engagement rerun after a crash should be nearly free.
   - **Per-engine token bucket** on the client side. The DuckDuckGo suspension was
     earned by request volume; rate limiting outbound requests per engine prevents
     earning it again.
   - **Direct authority fetch.** For a `NATION_OR_REGION` subject, statistical agencies,
     central banks, the World Bank and the IMF publish structured data at stable URLs.
     Fetching those directly is more reliable and more authoritative than any
     meta-search. This is the highest-value addition after Gemini for exactly the
     question in the audited artifact.
   - **FlareSolverr stays CAPTCHA-only.** It is the heaviest container and the slowest
     path. Never route routine queries through it.

**Verification.**

```bash
# grounding returns metadata and the ledger counts real queries
cd /home/user/webapp && python3 - <<'PY'
import asyncio
from hyperion.tools.grounded_search import grounded_search
from hyperion.infra.quota import ledger

before = ledger().remaining("gemini-3")
res = asyncio.run(grounded_search("India manufacturing share of GDP 2024"))
assert res.results, "no normalised results returned"
assert res.web_search_queries, "groundingMetadata.webSearchQueries was empty"
after = ledger().remaining("gemini-3")
assert before - after == len(res.web_search_queries), "ledger did not count actual queries"
# results are the same shape as SearXNG's
assert {"url", "title", "snippet"} <= set(vars(res.results[0]))
print("W-14 OK, spent", len(res.web_search_queries), "queries")
PY

# fail-open behaviour
python3 - <<'PY'
# force quota to zero, assert routing falls back to SearXNG and records the constraint
PY

# no downstream branching on backend
grep -rn 'backend\s*==\s*"gemini"' hyperion/agents/ | grep -v grounded_search
# expect: no output
```

**Acceptance criteria.**
- Grounded search returns results in the SearXNG result shape.
- The ledger increments by `len(webSearchQueries)`, verified against a live response.
- The ledger survives a process restart.
- Quota exhaustion falls back to SearXNG and records the constraint.
- No agent code branches on the backend.
- Methodology reports per-backend query counts.
- An engagement completes with SearXNG fully unavailable.

**Failure modes to avoid.**
- Counting one call as one query on a 3.x model. That under-counts by the fan-out factor
  and the monthly quota vanishes without warning.
- Making Gemini the default backend. The monthly allowance is roughly 5000 queries;
  a single engagement can issue hundreds.
- Discarding `groundingSupports`. It is the attribution data that makes this backend
  worth more than a search API, and the fact checker needs it.
- Letting a Gemini safety refusal propagate as a retrieval failure. Fall back and record.
- Adding a second result type for grounded results. One type, one `backend` field.

**Rollback.** Routing policy is a table. Set every row to SearXNG to disable the backend
without removing the code.

---

## 6. Does the report answer the question? A direct assessment

The user asked for a genuine judgement with no sugar-coating on whether the content
makes sense against the main question. Here it is.

**The question:** "should india icreases manufacturing ?"

**The verdict: no, the document does not answer it, and it would not answer it even if
every rendering defect in this audit were fixed.**

### 6.1 What a competent answer requires

This is a national industrial-policy question with a large, contested, well-documented
evidence base. A defensible answer has to engage with at least the following, and none
of it requires exotic sources:

| Required element | Why it is load bearing |
|---|---|
| Current manufacturing share of GDP and its trend | the question presupposes a baseline; without it "increase" is undefined |
| The stated policy target and instruments already in force | there is an existing policy position to argue with, not a blank slate |
| Employment elasticity of manufacturing versus services | the strongest argument for manufacturing is jobs, and it is contested |
| The services-led growth counter-argument | a serious economics debate exists on exactly this question; omitting it is not neutrality, it is incompleteness |
| Comparator economies at a similar stage | the only way to make "increase" quantitatively meaningful |
| Binding constraints: logistics cost, power reliability, labour regulation, land | determines whether an increase is achievable, not merely desirable |
| Trade and tariff posture, and global value chain positioning | manufacturing growth is an export question before it is a domestic one |
| Fiscal cost of the instruments and their measured returns | the opportunity cost of the policy |
| A stated recommendation with conditions and falsifiers | the question is a decision question and demands a decision |

### 6.2 What the document did instead

From the measurements in section 2 and the structure in section 3:

- **Six of eleven analytical chapters returned zero findings** and emitted
  `Confidence: low` and filler. So more than half the analysis is absent.
- The chapters that did run include a **discounted cash flow analysis of a country**, a
  **competitor matrix** for an entity that has no competitors in the sense intended, and
  **consumer-research instruments** (NPS, personas, review mining) for a subject that is
  not a product. These are not weak answers, they are answers to different questions.
- A **Fact Checker chapter** announcing 149 hallucinated citations occupies space that
  should hold analysis, and simultaneously tells the reader not to trust the rest.
- **Zero charts.** A question about shares, trends and comparators is inherently
  quantitative, and there is not one visualisation in 34 pages.
- The TOC advertises a **Risk Analysis** section that does not exist. For a decision
  question, risk analysis is not optional garnish, it is half the answer.
- **Median page fill 23.2 percent.** The document is physically mostly empty, so even
  the parts that ran are thin.

### 6.3 The honest diagnosis

The pipeline did not fail to research India's manufacturing sector. It never tried. It
dispatched a corporate-strategy engagement template at a macroeconomic policy question,
and the template's questions have no answers, so the retrieval came back empty, so the
confidence came back low, so the filler came back, and the fact checker correctly
observed that a large number of citations were unsupported because the agents were
searching for things that do not exist.

Every downstream symptom the user listed follows from that one decision. That is why
RC-6 and W-06 exist in this document, and why W-06 is the single highest-value content
change proposed here. The rendering fixes make the document look like a consulting
deliverable. W-06 is what makes it be one.

### 6.4 What the chapter structure should have been

For subject class `NATION_OR_REGION` and question type GO_NO_GO, the roster should
produce approximately this outline. Recording it here gives W-06 a concrete target to
test against:

1. Answer and recommendation, with conditions
2. Baseline: manufacturing share, composition, trend, employment
3. The case for increase: jobs, trade balance, value chain capture
4. The case against, or for a services-led alternative
5. Comparator evidence from economies at a similar stage
6. Binding constraints and their tractability
7. Policy instruments in force and their measured returns
8. Fiscal cost and opportunity cost
9. Risks, and what would falsify the recommendation
10. Methodology and limitations

Ten sections, none of which requires a DCF, a persona, or a competitor matrix. If W-06
is implemented correctly, this is roughly what the director should plan, and the absence
of financial-valuation and consumer-research chapters should be stated once in the scope
note rather than appearing as eleven low-confidence stubs.

---

## 7. The verification protocol

This section exists because the previous round had nothing like it. Fifteen fixes were
written, unit tested, reviewed and merged, and the system's observable behaviour did not
change at all. The gap was that nobody generated a report and looked at it.

### 7.1 The four gates, in order

A change passes through all four or it is not done.

**Gate 1, static.** Linting, typing, and the existing test suite. Necessary, and by
itself worth almost nothing for this class of defect, as the previous round proved.

**Gate 2, unit.** The specific tests named in each work item's Verification block. Also
necessary, also insufficient. `tests/test_no_phantom_self_attrs.py` passes on this tree
while the running system was raising the very error it tests for.

**Gate 3, artifact.** Generate a real PDF and run the repository's own gate on it:

```bash
cd /home/user/webapp
python3 - <<'PY'
from hyperion.output.page_audit import audit_pdf, extract_pdf_text, scan_text_integrity
p = "output/<slug>.pdf"
res = audit_pdf(p)                      # raises PageAuditError on any violation
hits = scan_text_integrity(extract_pdf_text(p))
assert not hits, hits
print("artifact gate passed:", res)
PY
sha256sum output/<slug>.pdf
```

The SHA-256 goes into the work item's completion note. That hash is the only acceptable
evidence of completion.

**Gate 4, provenance.** Confirm the artifact came from the code under test:

```bash
python3 -c "
import fitz
d = fitz.open('output/<slug>.pdf')
print(d.metadata['producer'])
"
# expect the producer string to contain the git SHA of the commit under test (W-01 step 6)
git rev-parse --short HEAD
```

Gate 4 is the direct countermeasure to RC-1. Had it existed, the uploaded artifact's
producer string would have named a pre-merge commit and this entire audit round would
have been a one line answer.

### 7.2 The environment reset that must precede any verification run

The stale-build hypothesis in section 1.4 is not exotic; it is the default outcome of a
normal development workflow. Before any verification run:

```bash
cd /home/user/webapp

# 1. no shadowing install
pip uninstall -y hyperion 2>/dev/null; pip install -e .
python3 -c "import hyperion, pathlib; print(pathlib.Path(hyperion.__file__).resolve())"
# must print /home/user/webapp/hyperion/__init__.py

# 2. no stale bytecode
find . -name "__pycache__" -type d -prune -exec rm -rf {} +

# 3. no second checkout on sys.path
python3 -c "import sys; [print(p) for p in sys.path]" | grep -i hyperion
# expect at most the working tree

# 4. the tree is what you think it is
git status --porcelain && git rev-parse --short HEAD
```

Run this before every artifact-gate attempt until W-01 makes it automatic.

### 7.3 Regression fixtures worth adding

The existing suite has good bones: `test_quality_loop_approval_gate.py`,
`test_page_count_gate.py`, `test_integrity_blocker_never_bypassed.py`,
`test_degraded_quality_guard.py`, `tests/output/test_page_canvas_background.py`,
`tests/output/test_engine_audit_wiring.py`. Add these, each named for the defect it
prevents:

| Test | Asserts |
|---|---|
| `test_rejected_pdf_not_at_deliverable_path` | W-02, a failed audit leaves nothing at `output/<slug>.pdf` |
| `test_designer_never_renders_pdf` | W-03, zero `render_pdf` call sites in the designer |
| `test_delivery_failure_fails_engagement` | W-04, a visualizer crash yields `success is False` |
| `test_contradiction_requires_shared_subject` | W-05, the five cases in that work item |
| `test_country_question_excludes_firm_methods` | W-06, no DCF for `NATION_OR_REGION` |
| `test_no_section_without_findings` | W-07, `OUT_OF_SCOPE` sections are absent |
| `test_blocked_score_ships_nothing` | W-08, 2.15/4.0 produces no client PDF |
| `test_client_prose_rejects_telemetry` | W-09, all six rejection categories |
| `test_engine_registry_reconciled` | W-11, code-referenced engines exist in the instance |
| `test_provenance_detects_stale_bytecode` | W-01, the refusal fires |

`tests/test_no_phantom_self_attrs.py:116` contains `assert ... or True`, which is a
vacuously passing assertion. Fix it while in the area; a test that cannot fail is worse
than no test because it consumes the credibility of a green suite.

### 7.4 The golden artifact

`tests/golden/` and `test_golden_pdf.py` already exist. After W-01 through W-10 land,
regenerate the golden PDF from a fixed engagement fixture and commit its SHA-256. Then a
rendering regression is a one-command detection rather than an audit round.

Note the practical constraint: `weasyprint` is **not installed in this sandbox**
(`ModuleNotFoundError: No module named 'weasyprint'`), so Gate 3 cannot be executed here.
It must be executed on the user's machine, which is also the only place where W-13 can be
verified at all. Any claim of completion made from an environment that cannot render a
PDF is exactly the mistake this section exists to prevent.

---

## 8. Sequencing

Dependency order, not severity order. Each phase is independently shippable and leaves
the system strictly better than the phase before.

### Phase 0, make verification possible (W-01, W-02)

Nothing else can be trusted until the running build is identifiable and a rejected
artifact cannot be mistaken for a deliverable. These two items are small, low risk, and
they are the reason the previous round produced no observable change. **Do not start any
other work item before both are merged and their artifact gates pass.**

### Phase 1, make the pipeline fail honestly (W-03, W-04, W-08)

Single writer running last, delivery fails closed, quality gate can refuse. After this
phase the system may produce fewer reports, and that is the intended outcome. A pipeline
that ships nothing when it has nothing is a strict improvement over one that ships 34
mostly-empty pages.

### Phase 2, make the content correct (W-06, W-07, W-05)

Subject ontology first, because it determines what the other two operate on. Then the
insufficiency ladder, then the contradiction detector. W-06 is the single highest-value
change in this document, per section 6.3.

### Phase 3, make the document presentable (W-09, W-10)

The narrative boundary type and the real methodology section. These are best done after
Phase 2 because both consume structures that Phase 2 creates (roster decisions, strategy
triples, W-07 outcomes).

### Phase 4, make retrieval robust (W-11, W-12, W-14)

Strict internal order, for a reason that matters more than the usual sequencing
preference:

1. **W-11 first.** It rebuilds the pool around engines that do not block. Doing W-12
   first would point three instances at DuckDuckGo and Google and earn the 403 three
   times faster.
2. **W-12 second.** Instances are safe to add only once the engine sets they will hold
   are the non-blocking ones, and only once the sets are disjoint.
3. **W-14 third, and it is now required rather than optional.** W-11 deliberately gives up
   the three largest general web indexes in exchange for reliability. Gemini grounding and
   the direct authority fetch are what restore that coverage. Shipping W-11 without W-14
   leaves the system reliable but under-informed on general web questions, which trades
   one failure mode for another.

W-07's ladder should already exist by this point, since it is W-14's most valuable
consumer.

### Phase 5, platform (W-13)

Independent of everything else and can be done in parallel at any point. It is sequenced
last only because it is an operator-convenience fix rather than a correctness fix. If the
user wants the manual Docker step gone before anything else, W-13 can be pulled forward
without disturbing any other item.

### 8.1 What to do first, concretely

1. Reset the environment per section 7.2, then run one engagement on the current tree and
   run the artifact gate on the result. This establishes the true current baseline, which
   nobody has, because every number in this audit comes from an artifact built by an
   unknown pre-merge commit. It is entirely possible that the merged fixes work and the
   only real defects are RC-1 through RC-8. **Measure before building.**
2. Implement W-01, so step 1 never needs to be done by hand again.
3. Implement W-02.
4. Re-run step 1 and record the SHA-256. That hash is the baseline this programme is
   measured against.

Step 1 is not optional and it is not a formality. Writing Phase 1 through 5 before
establishing a measured baseline on a known build would repeat the exact error of the
previous round at five times the scale.

---

## 9. Definition of done

### 9.1 Per work item

- [ ] Diff written and reviewed
- [ ] Named unit tests from the work item pass
- [ ] Environment reset per section 7.2
- [ ] A report generated after the change passes `audit_pdf()` with zero violations
- [ ] `scan_text_integrity()` returns zero hits on that report
- [ ] The report's producer string contains the SHA of the commit under test
- [ ] SHA-256 of the passing artifact recorded in the completion note
- [ ] The regression test from section 7.3 for this item is committed

### 9.2 Programme level

- [ ] Zero of the 277 violation classes reproduce on a fresh artifact
- [ ] Median page ink fill at or above 45 percent, no page below 30 percent
- [ ] Page corners equal the theme background on every page
- [ ] Zero image-over-text occlusions
- [ ] At least one chart per quantitative section, zero pages with 12 decorative images
- [ ] TOC matches the document at zero page tolerance, no phantom entries
- [ ] Zero em dashes, zero en dashes, zero dict reprs
- [ ] Zero occurrences of `Confidence: low`, `Fact Checker`, `hallucinat`,
      `Insufficient evidence` in client prose
- [ ] No section exists with zero findings
- [ ] Contradictions in single digits, every one with a shared subject
- [ ] No firm-level valuation method dispatched for a `NATION_OR_REGION` subject
- [ ] Methodology contains all six subsections and zero agent names
- [ ] A sub-4.0 score with critical failures produces no client PDF
- [ ] Every code-referenced search engine exists in the instance that serves it
- [ ] Zero Tier C (blocking) engines anywhere in settings or code
- [ ] Zero CAPTCHA and zero access-denied exceptions across a full engagement
- [ ] Three SearXNG instances healthy under 3 GiB total, engine sets provably disjoint
- [ ] All search ports bound to `127.0.0.1`, FlareSolverr not started by default
- [ ] Per-engine token bucket shared across all instances
- [ ] Gemini grounding metered against `webSearchQueries`, fails open to SearXNG
- [ ] `hyperion shell` starts Docker Desktop under WSL2 with no manual step, and stops
      all containers on quit while leaving Docker Desktop running
- [ ] Golden artifact regenerated and its SHA-256 committed

### 9.3 The one-line test of whether this round succeeded

Generate a report on the same question, `should india increase manufacturing?`, and read
it. If it opens with a recommendation, supports it with charted quantitative evidence,
engages the services-led counter-argument, names its risks, and contains no sentence that
mentions an agent, a confidence level, or a hallucination, then this round worked.

If it produces nothing at all because the quality gate refused, that is also a success.
It is a much better outcome than the artifact under audit, and the operator diagnostic
from W-08 step 5 will say exactly why.

---

## 10. Corrections to expectations, stated plainly

Three things the user asked for will not do what the user expects, and saying so now is
cheaper than discovering it after the work is done.

1. **Multiple SearXNG instances do not evade a block.** Three containers on one laptop
   share one public IP, and the block is on the IP. Instances buy fault isolation,
   concurrency and profile separation, which are worth having. They are not an evasion
   mechanism, and after W-11 they do not need to be, because the pool no longer contains
   an engine that blocks. Details in W-12.

2. **The no-block engine policy costs real recall, and that is the correct trade.**
   Removing Google, Bing, DuckDuckGo and everything that proxies them removes the three
   largest general web indexes. Mojeek, Marginalia and Brave together index a small
   fraction of what Google does. What is bought is that retrieval never collapses to zero
   mid-engagement, which is what actually happened in the audited run. The lost coverage
   comes back through W-14, so W-14 is a requirement of this policy rather than an
   enhancement.

3. **Gemini grounding is not a drop-in replacement for SearXNG.** Roughly 5000 search
   queries per month on the free tier, counted per individual query on 3.x models, is a
   small fraction of what one engagement consumes. It is a high-value fallback and an
   attribution source, not a primary backend. Details and the metering design in W-14.

4. **A correct fix is not an effective fix until an artifact proves it.** This is the
   lesson of the previous round and the reason section 7 exists. Expect the next round to
   feel slower per item, because each item now ends with generating a PDF and hashing it
   rather than with a green test run. That slowness is the entire point.

5. **This document does not, by itself, produce a complete system.** It is a diagnosis and
   a plan; zero lines of `hyperion/` are changed by it. It addresses the twelve root causes
   that are provable from the audited artifact, and it explicitly does not cover agent
   prompt quality, router and provider fallback behaviour, token and cost budgets, the
   eval harness, mid-engagement crash recovery, concurrency races, or whether the fact
   checker's "149 hallucinated citations" finding was itself correct. Those are unaudited,
   not absolved. Section 8.1 step 1 exists because every measurement here comes from an
   artifact built by an unknown pre-merge commit, so the true current baseline is still
   unmeasured.

---

## 11. Summary

The previous round wrote fifteen correct fixes into a tree that the running system was
not loading, and shipped a rejected PDF from the exact path a user would look in. Those
two mechanisms (RC-1, RC-2) explain "all of them exactly same" completely, and both are
fixed by two small work items, W-01 and W-02, which must land before anything else.

Underneath that, the content complaints are all correct and all trace to four structural
properties described in section 4: gates that report instead of stopping, the deliverable
being authored before its inputs exist, internal telemetry leaking into client prose, and
capability selected by surface form rather than by subject. The DCF on a country, the
Position A versus Position B garbage, the thirty-two low-confidence stubs, the fact
checker chapter, and the missing charts are five faces of those four properties.

Section 6 answers the question the user asked most directly: the report does not answer
its own question, and it would not answer it even with every rendering defect repaired,
because a corporate-strategy engagement template was pointed at a macroeconomic policy
question. W-06 is the fix, and it is the highest-value item in this document.

Fourteen work items, five phases, one rule: **a hash of a clean artifact, or it did not
happen.**
