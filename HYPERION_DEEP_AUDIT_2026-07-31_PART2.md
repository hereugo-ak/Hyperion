# HYPERION Deep System Audit, 2026-07-31, PART 2

**Predecessor:** `HYPERION_DEEP_AUDIT_2026-07-31.md` (RC-1 .. RC-12, W-01 .. W-14). Those
findings are not re-litigated here. This document covers exactly the six areas that
document's own section 10.5 named as unaudited: agent prompt quality, router and
provider fallback behaviour, token and cost budgets, the eval harness, mid-engagement
crash recovery and concurrency, and whether the fact checker's "149 hallucinated
citations" finding was itself correct.
**Repository state verified against:** branch `fix0.1`, commit `bdb0a86` ("docs(audit):
HYPERION deep system audit 2026-07-31 with per-step remediation guide"), working tree
clean.
**Method.** Every claim below was re-derived in this session by reading the file at the
stated `file:line` in this exact commit, or by running the stated command. Line numbers
differ in places from the draft this document supersedes, because that draft was not
checked against a pinned commit. Two claims in that draft did not survive verification
and are corrected in section 8 rather than silently dropped.

---

## 0. Executive verdict

The six areas are real, and the headline claim is confirmed: **the "149 Hallucinated
Citations" finding is not a hallucination count.** It is the output of a string
comparison between a claim fragment and a provenance label such as `"Regulatory portal
data from https://..."`, which cannot contain the claim's words by construction. That
finding is published at `ConfidenceLevel.HIGH` and is the documented cause of the
artifact's 32 occurrences of `Confidence: low`. This is confirmed against the exact
commit (`eb481e0^`) that produced the audited artifact, and the underlying defect (the
`Source.key_data` field holding a provenance sentence instead of fetched content) is
still present in the current tree for 15 of 17 specialist call sites.

Two things in the draft this document is based on did not hold up under direct
verification and are corrected here rather than repeated:

1. **The claim that `hallucinated_count` in the current tree is still driven by the
   naive matcher is not quite right.** Commit `eb481e0` (P2-19/20, already in this
   tree) replaced the mechanism that produces the *published* hallucination count with
   a stricter two-signal test (token-boundary overlap, reusing `EvidenceScorer`, plus a
   URL-liveness check). The naive substring/word matcher described in the original
   report **is still live** in `_verify_claim` (`fact_checker.py:818-826` in this
   commit) and it still determines `VERIFIED` / `PLAUSIBLE` / `UNVERIFIED`, which still
   feeds `verification_rate`, which still feeds `_calibrate_confidence`. So the
   confidence-calibration poisoning is real, but it now runs through
   `verification_rate`, not through `hallucinated_count` directly. See section 1.4.
2. **"CONTRADICTED is unreachable dead code" is not true system-wide.** It is true of
   the specific branch inside `_verify_claim` (the `else: pass` at
   `fact_checker.py:829-831`), which is genuinely dead. But a separate, working
   mechanism, `_detect_contradictions` / `_claims_conflict`
   (`fact_checker.py:895-965`), does set `ClaimStatus.CONTRADICTED` from a real numeric
   comparison across agents, and that status does reach `_stage2_readjudicate`'s gate.
   See section 1.5.

Neither correction weakens the overall verdict. The corpus defect (FC-1) still poisons
both the old and the new mechanism, because the "fixed" P2-19 matcher also reads
`source.key_data`, and 15 of 17 specialists still write a provenance sentence there
instead of fetched text. What changed between the two mechanisms is *how* the poisoned
corpus fails: the old matcher over-flagged (fired on almost any word-overlap miss); the
new matcher under-flags in a low-egress sandbox, because `_url_alive` treats every
network failure as "alive" (confirmed still present, `fact_checker.py:1260-1281`). Both
are broken, in opposite directions, for the same root reason.

Behind that, the remaining five areas are confirmed largely as described, with line
numbers re-anchored to `bdb0a86`. Ranked by severity:

| # | Area | Verdict | Severity |
|---|------|---------|----------|
| 1 | Fact checker "149" | Confirmed false as a hallucination count; confirmed the mechanism now differs from the draft's description; corpus defect (FC-1) confirmed live in 15/17 specialists | Critical |
| 2 | Router fallback and retry | Confirmed exactly: unbounded mutual recursion between `_dispatch` and `_try_next_candidate`, no attempt budget, no status-code branching | Critical |
| 3 | Token and cost budgets | Confirmed exactly: zero persistence in `budget.py`, `tpd` field populated with 9 distinct values and read at zero sites, no cost model | High |
| 4 | Eval harness | Confirmed exactly: no `audit_pdf()` call, `mean_score` filters on `success`, LLM judge is a heuristic restatement of the same 15 checks, no CI workflow exists | High |
| 5 | Agent system prompts | Confirmed almost exactly: 86 em/en dashes measured (identical to the draft, verified independently via AST), 0/20 out-of-scope clauses, 1/20 anti-fabrication clauses (draft said 2/20, corrected in section 8) | High |
| 6 | Crash recovery and concurrency | Confirmed exactly: `run_id` is `f"eng_{uuid.uuid4().hex[:12]}"`, no `resume` command exists among the 7 registered, zero `signal.SIG*`/`atexit` hits in the tree | Medium-High |

None of these are covered by W-01 through W-14. Six new work items, W-15 through W-20,
are specified in section 9.

---

## 1. Fact checker measurement validity (confirms and corrects the "149" finding)

### 1.1 The provenance-label defect (FC-1), confirmed

`hyperion/schemas/models.py:216-217` declares intent:

```python
key_data: str | None = Field(default=None, description="The specific data point extracted "
    "from this source")
```

Grepping every `key_data=f"..."` assignment in the specialists tree at this commit
returns 17 hits. 15 are provenance sentences, not fetched content:

```
regulatory_analyst.py:516       key_data=f"Regulatory portal data from {url}",
competitive_intel.py:512        key_data=f"Scraped {page_type} content from {competitor}",
sustainability_analyst.py:496   key_data=f"ESG rating data from {url}",
risk_analyst.py:498             key_data=f"Regulatory risk data for {jurisdiction}",
operations_analyst.py:447       key_data=f"Supply chain data for {sector}",
strategy_analyst.py:482         key_data=f"Strategy data from {url.split('/')[2]}",
technology_analyst.py:497       key_data=f"Pricing and features for {vendor}",
innovation_analyst.py:474       key_data=f"Patent/research data from {url.split('/')[2]}",
consumer_insights.py:481        key_data=f"Customer reviews from {url.split('/')[2]}",
ma_analyst.py:507               key_data=f"M&A database data from {url.split('/')[2]}",
competitive_intel.py:557        key_data=f"Historical snapshots for {competitor} (1y, 2y, 5y)",
innovation_analyst.py:525       key_data=f"Historical snapshot from {years_ago} years ago",
regulatory_analyst.py:564       key_data=f"3-year regulatory evolution for {url}",
ma_analyst.py:670               key_data=f"Financial data for {target.company_name} ({target.ticker})",
```

Two write real interpolated numbers rather than prose (`financial_analyst.py:386`,
`market_analyst.py:537`), and one writes real fetched content
(`fact_checker.py:_check_local_corpus`, `finding.content[:500]`, see section 1.6). The
population is 15 provenance labels to 2-3 real-content sources. This is confirmed
exactly as originally reported.

### 1.2 The mechanism that produced the artifact's "149" (FC-2), confirmed verbatim

The artifact was generated by a build at or after `4dc9820` and before `c12f150`
(established in the predecessor document's section 1.4), which places it at
`eb481e0^` for the fact checker file specifically. At that commit,
`_validate_evidence_chains` (then around line 1029) reads:

```python
claim_lower = claim.claim.lower()
source_contains_data = False

for source in claim.verification_sources:
    source_data = (source.key_data or "").lower()
    if claim_lower in source_data or any(
        word in source_data for word in claim_lower.split() if len(word) > 4
    ):
        source_contains_data = True
        break

if not source_contains_data and claim.verification_sources:
    claim.evidence_chain_valid = False
    claim.evidence_chain_break = (
        "Cited source does not contain the claimed data"
        "possible hallucinated citation"
    )
    claim.is_hallucinated_citation = True
    hallucinated.append(claim)
```

Verified verbatim via `git show eb481e0^:hyperion/agents/support/fact_checker.py`. For
a claim like `"$5 million"`, `claim.claim.lower().split()` yields `["$5", "million"]`;
`"$5"` is discarded by `len(word) > 4`, so the test degenerates to: does `"million"`
appear in `"regulatory portal data from https://..."`? It does not. The claim is
flagged with no second signal. For a bare-integer claim (`"2024"`, `"30"`), there is no
word longer than four characters at all, so `any(...)` over an empty generator is
`False` unconditionally, and the claim is flagged by construction regardless of
correctness.

The title and confidence level for the resulting finding, in the **current** tree
(`fact_checker.py:1574,1582`, unchanged by `eb481e0`):

```python
title=f"CRITICAL: {len(self._hallucinated)} Hallucinated Citations Detected",
...
confidence=ConfidenceLevel.HIGH,
```

`ConfidenceLevel.HIGH` is a hardcoded literal on this `KeyFinding`, not a computed
value proportional to `len(self._hallucinated)`. A count of 3 and a count of 300 both
publish at the same stated confidence. This is confirmed in the current tree exactly as
originally reported; `eb481e0` changed how the count is computed but did not touch this
publication code.

### 1.3 The narrative escalation (FC-3), partially verifiable

The claim that the artifact's prose ("falsely cited by multiple analysts") over-states
what the finding actually says is consistent with the finding's `content` field
(`fact_checker.py:1576-1580`), which lists only the agent names and the first three
claim fragments, no polarity or intent language. The specific sentence in the client
PDF could not be re-derived here because the artifact bytes are not part of this
repository and were not re-fetched for this session; this point is carried forward as
reported, with a `HIGH` confidence relative to the mechanism trace but without an
independent re-read of the PDF text in this session.

### 1.4 The naive matcher's actual current-tree effect (FC-4, corrected)

`fact_checker.py:818-826`, in the current tree, is character-for-character the same
predicate quoted in section 1.2:

```python
claim_lower = claim.claim.lower()
for source in verification_sources:
    source_data = (source.key_data or "").lower()
    if claim_lower in source_data or any(
        word in source_data for word in claim_lower.split() if len(word) > 4
    ):
        supporting_sources += 1
```

This lives in `_verify_claim`, and it is genuinely still broken for the same reason as
section 1.2. It sets `VERIFIED` / `PLAUSIBLE` / `UNVERIFIED` at `:834-848`. Those counts
feed `verification_rate` inside `_calibrate_confidence` (`:1375-1398`):

```python
verification_rate = (verified + plausible) / total_claims
if verification_rate >= 0.7 and hallucinated_count == 0 and contradiction_count < 2:
    return ConfidenceLevel.HIGH
if verification_rate >= 0.4 and hallucinated_count < 3:
    return ConfidenceLevel.MEDIUM
return ConfidenceLevel.LOW
```

**Correction to the draft this document supersedes:** `hallucinated_count` in this
expression is `len(self._hallucinated)` (`run()`, `:1520`), and in the current tree
`self._hallucinated` is populated by `_validate_evidence_chains` **as fixed by
`eb481e0`** (the two-signal, token-boundary test described in section 1.6), then
re-filtered by `_stage2_readjudicate`. It is *not* produced by the naive matcher at
`:818-826`. So the specific claim "a metric that fires on nearly every numeric claim
[is what] forces LOW unconditionally via `hallucinated_count >= 3`" overstates the
current tree: `hallucinated_count` is no longer that metric. What remains true, and is
sufficient to sustain the same conclusion, is that `verification_rate` **is** still
computed by that exact broken metric, and `verification_rate < 0.4` reaches `LOW`
independently of `hallucinated_count`. A corpus of provenance-label sources will still
produce a `verification_rate` near zero (almost no claim's words appear in a sentence
like `"Regulatory portal data from {url}"`), so `LOW` remains the dominant, effectively
forced outcome, via a different one of the function's two inputs than originally
described. The corpus problem (section 1.1) is what makes both inputs fail together in
practice, whichever one the sentence above blames.

### 1.5 CONTRADICTED is not unreachable system-wide (FC-5, corrected)

The claim that `ClaimStatus.CONTRADICTED` is unreachable dead code is true only for the
branch inside `_verify_claim` (`fact_checker.py:828-831`):

```python
else:
    # Check for contradiction (source mentions the topic but with different data)
    # This is a simplified check — the LLM would do deeper analysis
    pass
```

`contradicting_sources` is indeed initialised to `0` and never incremented in this
function, so the `elif contradicting_sources > 0: claim.status =
ClaimStatus.CONTRADICTED` branch at `:843-845` in `_verify_claim` is genuinely
unreachable. That much is confirmed.

**Correction:** a second, independent, and functioning path exists.
`_detect_contradictions` / `_claims_conflict` (`fact_checker.py:895-965`) compares
`NUMBER`-type claims pairwise across different agents and, on conflict, does set
`ClaimStatus.CONTRADICTED` on both claims (`:936,938`) plus records a
`Contradiction` object. This is a real numeric comparison (not a string-equality
proxy of the kind described for `synthesis_lead.py` in the predecessor document's
RC-5), and its output does reach `_stage2_readjudicate`'s gate at `:1010`
(`c.is_hallucinated_citation or c.status == ClaimStatus.CONTRADICTED`). So the
sentence "stage 2 only ever sees claims flagged by the broken hallucination test" is
not accurate in the current tree; it can also see claims flagged by this separate,
working contradiction detector. Whether `_claims_conflict`'s own predicate is itself
sound was not audited in this session and is a candidate for future work, but it is
not dead code.

### 1.6 Local corpus circularity (FC-6), confirmed exactly

`fact_checker.py:585-614`, current tree:

```python
def _check_local_corpus(self, claim: Claim) -> list[Source]:
    ...
    for finding in self._all_findings:
        content_lower = (finding.content or "").lower()
        if claim_lower in content_lower or claim_words & set(content_lower.split()):
            for src in finding.sources[:3]:
                ...
                local_sources.append(Source(
                    ...
                    key_data=finding.content[:500],
                ))
```

Confirmed verbatim. A claim extracted from a finding's content is looked up against
all findings' content, trivially matches its own origin finding, and is "verified"
against `key_data=finding.content[:500]`, which is that same content. `_check_independence`
(`:757`, not re-quoted here) checks source URL hosts and does not detect this, because
the URLs are real; only the `key_data` text is circular.

### 1.7 URL-liveness environment dependence (FC-7), confirmed exactly

`fact_checker.py:1260-1281`, current tree, verbatim:

```python
async def _url_alive(self, url: str) -> bool:
    ...
    try:
        import httpx
        async with httpx.AsyncClient(follow_redirects=True, timeout=8.0) as client:
            try:
                resp = await client.head(url)
            except Exception:
                resp = await client.get(url, headers={"Range": "bytes=0-0"})
            return resp.status_code < 400
    except Exception as exc:
        logger.debug("url liveness check failed for %s: %s", url, exc)
        return True
```

Confirmed exactly. This function is the second of the two signals `_validate_evidence_chains`
now requires before flagging a hallucination (post-`eb481e0`). In a sandbox or
environment with restricted egress, every URL check falls into the outer `except` and
returns `True` ("alive"), so no claim can ever be confirmed a hallucination regardless
of the underlying corpus. This is a genuine finding independent of section 1.4's
correction: it means the **current, fixed** mechanism is just as environment-dependent
as the old one was corpus-dependent, only in the opposite direction (under-reporting
instead of over-reporting).

### 1.8 Round-number check is unit-blind (FC-8), confirmed exactly

`fact_checker.py:281` and `:1306-1316`, current tree:

```python
ROUND_NUMBER_SUSPECTS = {10_000_000_000, 1_000_000_000, 100_000_000, 10_000_000}
...
nums = re.findall(r'\$?(\d[\d,]*)\.?\d*', claim.claim)
for num_str in nums:
    value = int(num_str.replace(",", ""))
    if value in self.ROUND_NUMBER_SUSPECTS:
        red_flags.append(
            f"Suspiciously round number: {claim.claim} ... exactly ${value:,} "
            f"is suspicious. Verify with primary source."
        )
```

Confirmed exactly. `"$10 billion"` captures the digit group `"10"`, which is not in
`ROUND_NUMBER_SUSPECTS` (10 != 10,000,000,000), so it is never flagged; `"$10,000,000"`
captures `10_000_000` and is flagged. Two claims of the same real-world magnitude are
treated differently purely by which unit-suffix notation the specialist happened to
use. There is no deduplication across claims sharing the same literal, so N claims
citing the same suspect number produce N separate red flags.

### 1.9 Verdict on section 1

The "149" figure is not a hallucination count and must not be presented to a client or
an operator as a quality signal without a structural fix. The immediate, root-cause fix
is not in the fact checker at all: it is populating `Source.key_data` with actual
fetched text (the retrieval layer's job) rather than a description of where the source
came from. Both the pre-fix and post-fix (`eb481e0`) matching mechanisms are only ever
as good as that field, and today it is empty of real content in 15 of 17 specialist
call sites. See W-15.

---

## 2. Router and provider fallback and retry behaviour

All claims in this section were re-verified against `hyperion/router/router.py` in the
current tree (798 lines) and are confirmed with corrected line numbers.

### 2.1 Unbounded mutual recursion (RT-1), confirmed

`_dispatch` (`:661-742`), on failure, ends with:

```python
return await self._try_next_candidate(
    tier=candidate.model.tier,
    ...
    exclude_provider=candidate.provider_type,
) or response
```

`_try_next_candidate` (`:598-656`) iterates `ordered_providers` (every available
provider except the single excluded one) and, for each, calls `self._dispatch(...)`
again with no exclusion list carried forward beyond that one candidate. Tracing 5
providers A-E all failing: `_dispatch(A)` excludes only A when it recurses;
`_try_next_candidate(exclude=A)` tries B, and if `_dispatch(B)` fails it recurses into
`_try_next_candidate(exclude=B)`, whose `ordered_providers` now again includes A. There
is no visited set threaded through the recursion, no depth counter, and no total
attempt budget anywhere in either function. Confirmed exactly as described; this is a
real, unbounded mutual recursion, terminating only when `get_available_providers`
starts returning fewer candidates (health/circuit breaker/budget exhaustion), by which
point multiple real HTTP calls and multiple real budget-planner consumptions have
already happened per frame.

### 2.2 Second full pass with no exclusions (RT-2), confirmed exactly

`_try_tier` (`:516-596`), after its own priority-ordered loop, ends with:

```python
# All providers in this tier exhausted — try remaining without priority filter
return await self._try_next_candidate(
    ...
    exclude_provider=None,  # Don't exclude any — we already tried all above
)
```

Confirmed verbatim at this line range. The comment states the intended semantics
("we already tried all above") but the code does the opposite: `exclude_provider=None`
means every provider that just failed is eligible again in the very next call. This
duplicates the RT-1 recursion at one additional level.

### 2.3 No retry, no backoff, no status-code branching (RT-3), confirmed

`_dispatch` branches on exactly one condition, `if response.success:` (`:757` in the
success path). No code in this function inspects an HTTP status code, distinguishes a
transient 500/503/timeout from a permanent 401/403, or treats a 429 differently from
any other failure, despite the adjacent comment claiming "Don't failover on 429" — that
claim is not implemented; there is nothing in `_dispatch` that reads a status code to
check for 429 specifically. Confirmed exactly.

### 2.4 Budget consumed before dispatch, never refunded on failure (RT-4), confirmed

`_dispatch:680-702`, current tree:

```python
self.wait_gate.record_dispatch(...)
self.budget_planner.consume(
    provider=candidate.provider_type,
    model_name=candidate.model.name,
    urgency=urgency,
)
response = await provider.complete(...)
```

Confirmed: `consume()` runs unconditionally before the network call, and there is no
call to any refund/rollback method on the failure path later in the function. A
provider returning 401 on every attempt (e.g. a revoked key) has its budget decremented
on every attempt, and under the RT-1 recursion that can happen many times per real
underlying failure.

### 2.5 Total failure is advisory (RT-5), confirmed

`router.py:complete()` returns a `RouterResponse(success=False, content="", ...)` on
total exhaustion across tiers (`:466-484`). `hyperion/agents/base.py:648-657` handles
it:

```python
if not response.success:
    await self._transition(AgentState.BLOCKED, f"LLM completion failed: {response.error}")
    await self._escalate(
        issue=f"LLM completion failed at {self.model_tier.value} tier: {response.error}",
        suggested_action="Reroute to adjacent tier or retry with different provider",
    )
```

Confirmed: after this block, execution falls through to `return response` (`:688`)
regardless of the branch taken. `_escalate` publishes a bus message
(`base.py:511-541`) and does not raise or cancel the calling task. The caller receives
`content=""` and, per the predecessor audit's own count of roughly 70
`json.loads(response.content)` call sites, this is the confirmed seam by which a total
provider failure degrades into an apparently-successful, empty analytical result.

---

## 3. Token and cost budgets

All claims re-verified against `hyperion/router/budget.py` in the current tree.

### 3.1 No persistence anywhere (BG-1), confirmed

Grepping `budget.py` for `json.dump`, `pickle`, `sqlite`, and `open(` returns zero
hits. `ProviderBudget.__post_init__` (`:87-88`) seeds `_reset_day` from
`time.gmtime().tm_yday` and `consumed` from the dataclass default `0`.
`DailyBudgetPlanner` is constructed once per `LLMRouter.__init__` (`:146-148`), and
`get_router()` (`:786-791`) is a module-level singleton whose lifetime is the process.
Confirmed exactly: `_PROVIDER_DAILY_BUDGETS` (Google 29,460; Groq 18,400; NVIDIA 33;
Cerebras 10,000; Mistral 86,400 requests/day) is enforced per process invocation, not
per calendar day. Multiple `hyperion consult` invocations in the same day each start
from zero, and the stated daily ceilings are not a real ceiling across them.

### 3.2 Only requests counted; `tpd` populated and read nowhere (BG-2), confirmed

`ProviderBudget.consume` (`:137-143`) increments `self.consumed` by a request count.
`remaining_for_model` (`:145-156`) consults `model.rpd` only. `hyperion/config.py:87`
declares `tpd: int | None`, and it is populated with 9 distinct non-`None` values
across the model specs (`:228,239,256,267,278,290,301,312,323`, values from 100,000 to
1,000,000). Grepping the entire `hyperion/` tree for `.tpd` returns exactly those 9
assignment sites and zero read sites. `budget.py:55`'s own comment states the binding
constraint for one provider explicitly: `ProviderType.CEREBRAS: 10_000,  # Effectively
unlimited by RPD (TPD-limited)`. The provider whose own inline comment names tokens as
the real constraint is the provider whose token limit is never checked anywhere in the
codebase. Confirmed exactly.

### 3.3 No cost model exists (BG-3), confirmed

Grepping `hyperion/router/` for `cost`, `usd`, `price`, `dollar` (case-insensitive)
returns exactly one incidental hit, a JSON-escaping example string in
`structured_validator.py:68` ("cost is $5"), and no model, field, or function anywhere
converts token or request counts into a monetary figure. Confirmed exactly as
described: there is no answer, anywhere in this system, to "what did this engagement
cost."

### 3.4 Check-then-consume gap under concurrency (BG-4), confirmed as a real but bounded gap

`get_available_providers` (`router.py:276-311`) filters by budget before a candidate is
selected; `wait_gate.select_with_wait` and, when `wait_seconds > 0`,
`wait_gate.wait_for_capacity` both `await` between that filter and the actual
`budget_planner.consume()` call at `:688`. Under `asyncio.gather` (confirmed at
`orchestrator.py:958`, 12 specialists dispatched concurrently, each of which can spawn
up to 3 sub-agents), multiple coroutines can pass the same budget check during the same
await window and then all consume. This is a real gap, but bounded by the concurrency
width of the running engagement (tens, not thousands, of requests), which is why it is
ranked below BG-1/BG-2 rather than above them.

### 3.5 The budget numbers are stated estimates (BG-5), confirmed

`budget.py:49-57`'s own docstring calls the table "approximate total RPD across all
providers per provider." The Mistral entry carries the comment `# ~60 RPM * 1440 min`,
which is arithmetic assuming one request per second sustained for 24 hours, not a
figure sourced from provider documentation. None of the five values carries a source
citation or a date. Confirmed as an internally-acknowledged estimate, not a measured
limit.

---

## 4. The eval harness

All claims re-verified against `hyperion/eval/harness.py` (568 lines) and
`hyperion/eval/ci_gate.py` (current tree). One correction to the draft: `ci_gate.py`
already contains more machinery than the draft credited it with, and that correction is
recorded in section 4.6.

### 4.1 `audit_pdf()` is never called from the harness (EV-1), confirmed

Grepping `hyperion/eval/` for `audit_pdf` and `page_audit` returns zero hits. The three
PDF-related deterministic checks are: check 7 `pdf_renders` (`harness.py:198-205`,
`os.path.exists`), check 11 `fonts_embedded` (`:238-251`, PyMuPDF font count >= 2), and
check 15 `page_count_reasonable` (`:285-307`, delegated to
`hyperion.output.page_budget.page_count_verdict`). None of the three inspects page
corner colour, ink fill, image occlusion, TOC fidelity, or banned substrings, which are
exactly the checks that `audit_pdf()` performs and that the audited artifact fails 277
times. Confirmed exactly.

### 4.2 Two disjoint banned-content lists, harness checks the wrong object (EV-2), confirmed

`harness.py:104-112`, `_TEMPLATE_ARTIFACTS`, 7 patterns: `&lt;`, `C:\`,
`{{\s*\w+`, `\{\{`, `\}\}`, `\bNone\b`, `<template>`. Check 6 (`:185-196`) runs this
against `json.dumps(report, default=str)`, the report **dictionary**, not the rendered
PDF's extracted text. `hyperion/output/page_audit.py:56-79` (`BANNED_SUBSTRINGS`)
declares 24 tokens including `"{'"`, which is exactly the dict-repr leak measured 24
times in the audited artifact's rendered text. `_TEMPLATE_ARTIFACTS` does not contain
`"{'"`. Both the target object (dict, not rendered PDF text) and the token list (7
patterns, missing the specific one that actually fired) are confirmed wrong.

### 4.3 `charts_present` counts specifications, not rendered images (EV-3), confirmed

`harness.py:207-216`:

```python
charts_count = sum(len(s.get("charts", [])) for s in sections)
results.append(CheckResult(name="charts_present", passed=charts_count > 0, ...))
```

Confirmed: this counts entries in the report dictionary's `charts` lists, not images
present in the rendered PDF. Check 12, `no_missing_images` (`:253-264`), does verify
`os.path.exists()` on each declared chart's image path, so a genuinely missing file is
caught, but a chart spec whose image exists on disk yet is never placed onto a page (as
happened in the audited artifact, 0 charts across 34 pages despite a non-trivial
`charts` list existing upstream) passes both checks. Confirmed as described.

### 4.4 The gate scores only surviving runs (EV-4), confirmed exactly

`run_all` (`:525-556`):

```python
scores = [r.overall_score for r in results.results if r.success]
results.mean_score = sum(scores) / len(scores) if scores else 0.0
passed = sum(1 for r in results.results if r.success and r.all_checks_passed)
results.pass_rate = passed / len(results.results) if results.results else 0.0

if results.baseline_score > 0:
    results.regression_detected = (
        results.mean_score < results.baseline_score - self.REGRESSION_THRESHOLD
    )
```

Confirmed exactly. `mean_score` filters on `r.success`; `regression_detected` reads
only `mean_score`; `pass_rate` is computed and stored but never referenced in the
regression decision, and `ci_gate.py` prints it but does not gate on it. Four of five
golden queries crashing (`_run_single_query`, `:519-521`, swallows any exception into
`result.error` and returns rather than propagating) and the fifth scoring `5.0` yields
`mean_score = 5.0`, `regression_detected = False`, and `ci_gate.py`'s exit code `0`.
Confirmed as the fail-open pattern the predecessor audit's section 4 names, reproduced
inside the harness meant to catch it.

### 4.5 The LLM judge does not exist (EV-5), confirmed exactly

`harness.py:513-517`:

```python
# LLM-as-judge (optional — requires router)
# For now, compute a heuristic score from deterministic checks
passed = sum(1 for c in result.deterministic_checks if c.passed)
total = len(result.deterministic_checks)
result.overall_score = (passed / total) * 5.0 if total > 0 else 0.0
```

Confirmed exactly against the class docstring's claim of "deterministic checks +
LLM-as-judge rubric" (`:450-452`). `overall_score` is a restatement of the same 15
structural checks on a 0-5 scale; no rubric, no judge call, and no assessment of
analytical quality exists anywhere in this file. One check flipping in one of five
queries moves the mean by `(1/15) * 5.0 / 5 = 0.0667`; `REGRESSION_THRESHOLD = 0.3`
(`:457`), so roughly 4.5 check-failures across the entire golden set are required
before a regression is even flagged.

### 4.6 CI gate has no CI, correction to the draft

Confirmed: `find . -path "*/.github/*"` returns nothing in this repository. There is no
CI workflow that invokes `ci_gate.py` on any event.

**Correction to the draft this document supersedes:** the draft describes
`ci_gate.py` as still containing the fused shebang/docstring `SyntaxError` and as
lacking a lint gate. Neither is true in the current tree. `ci_gate.py`'s own docstring
(`:11-19`) documents that history in the past tense and states
`tests/test_module_importability.py` now prevents recurrence (confirmed, that test
exists and the module parses and imports cleanly in this session). The file also
already implements `run_lint()` (`:47-70`), wired to a `--lint` flag
(`build_parser`, `:170-176`), which runs `ruff check` and `mypy` and treats a missing
tool as `EXIT_HARNESS_ERROR` rather than a silent pass. What is unchanged, and is the
actual defect: **nothing invokes `ci_gate.py` in either mode**, because no CI workflow
file exists. A gate that exists, correctly implements two modes, and is never called by
anything is functionally identical to a gate that does not exist, for the purpose of
catching a regression before merge. The fix (W-19) is therefore "add a workflow that
calls it," not "repair its internals," which is a narrower and cheaper fix than the
draft implied.

### 4.7 Golden set excludes the failing question class (EV-7), confirmed exactly

`harness.py:54-95`, `GOLDEN_SET`, 5 entries, verified verbatim:

```
gq_001  Should we enter the Tier-2 Indian SaaS market?                          market_entry
gq_002  What is the competitive landscape for AI-powered supply chain platforms? competitive_analysis
gq_003  Assess the regulatory risks of launching a fintech product in the EU.   risk_assessment
gq_004  Should we acquire Company X or build the capability in-house?          ma_analysis
gq_005  What technology stack should we adopt for our next-gen data platform?  technology_assessment
```

Confirmed exactly: all five are firm-level commercial questions with an implicit
first-person owner of a P&L, for which a DCF, a comparables set, and a competitor
matrix are appropriate methods. None exercises the `NATION_OR_REGION` subject class the
predecessor audit's W-06 introduces. A 5/5 pass on this set carries no information about
whether W-06's subject-fit gate works.

### 4.8 The baseline is a single float (EV-8), confirmed exactly

`_save_baseline` (`:474-478`):

```python
def _save_baseline(self, score: float) -> None:
    os.makedirs(os.path.dirname(self.baseline_path), exist_ok=True)
    with open(self.baseline_path, "w", encoding="utf-8") as f:
        json.dump({"mean_score": score, "ts": time.time()}, f, indent=2)
```

Confirmed exactly. One number for the entire golden set; no per-query baseline, no
per-check baseline. A regression in one query fully offset by an improvement in another
is invisible by construction, and even a detected regression cannot be attributed to a
specific query or check from this file alone.

### 4.9 Running the harness against unpersisted budgets (EV-9), confirmed as a consequence of section 3

`run_all` (`:534-536`) iterates the 5-query golden set serially through
`run_engagement`, each a full 20-agent engagement. Combined with BG-1 (section 3.1),
each of the 5 engagements believes it has the full, unshared daily provider quota.
Running the gate more than once in a day plausibly consumes several times the intended
daily allotment with no accounting anywhere to detect it.

---

## 5. Agent system prompt quality

Measured independently in this session via an AST walk of every `system_prompt=`
keyword argument across `hyperion/agents/**/*.py` (20 agents: 3 delivery, 12
specialists, 4 support, plus `engagement_director.py` and `synthesis_lead.py` at the
package root), reconstructing each literal from its constant/`BinOp` concatenation
chain. The results reproduce the predecessor draft's table exactly on length and dash
count for all 20 prompts, which is a strong independent confirmation:

```
render_engine.py                    line= 209 chars=1550 em=7 en=0
research_librarian.py               line= 160 chars=1985 em=6 en=0
competitive_intel.py                line= 152 chars=2039 em=6 en=0
market_analyst.py                   line= 166 chars=2146 em=3 en=0
ma_analyst.py                       line= 185 chars=2193 em=5 en=0
data_visualizer.py                  line= 224 chars=2244 em=3 en=0
technology_analyst.py               line= 182 chars=2256 em=6 en=0
financial_analyst.py                line= 184 chars=2287 em=4 en=0
risk_analyst.py                     line= 176 chars=2312 em=8 en=0
innovation_analyst.py               line= 202 chars=2323 em=5 en=0
operations_analyst.py               line= 187 chars=2323 em=5 en=0
fact_checker.py                     line= 178 chars=2326 em=0 en=0
regulatory_analyst.py               line= 176 chars=2327 em=6 en=0
sustainability_analyst.py           line= 177 chars=2328 em=6 en=0
presentation_designer.py            line=1629 chars=2369 em=0 en=0
engagement_director.py              line= 180 chars=2375 em=5 en=0
quality_gate.py                     line= 295 chars=2422 em=0 en=0
consumer_insights.py                line= 200 chars=2535 em=5 en=0
synthesis_lead.py                   line= 192 chars=2684 em=0 en=0
strategy_analyst.py                 line= 217 chars=2703 em=6 en=0

TOTAL em/en dashes across the 20 system prompts: 86 (86 em, 0 en)
```

### 5.1 Every prompt demonstrates the rule it opens with (PR-1), confirmed exactly

`hyperion/agents/base.py:603-606`:

```python
# P2-32 generation layer: the shared typography rule is prepended to
# EVERY dispatched prompt (base prompt and overrides alike), so the
# em/en dash ban is stated once here, not 20 times across specs.
base_prompt = system_prompt_override or self.system_prompt
system = f"{PROMPT_TYPOGRAPHY_RULE}\n\n{base_prompt}"
```

Confirmed: `PROMPT_TYPOGRAPHY_RULE` (`hyperion/output/typography.py:26`) prohibits
U+2014 and U+2013 and is prepended to every one of the 20 prompts on every call. 16 of
the 20 prompts (all but `fact_checker.py`, `quality_gate.py`, `presentation_designer.py`,
`synthesis_lead.py`) then contain between 3 and 8 instances of exactly the banned
character, immediately after the prohibition, in the highest-salience position in the
context window. `risk_analyst.py` carries the most at 8; `render_engine.py` carries 7
in only 1,550 characters, the highest density (1 per 221 characters) of any prompt in
the system.

### 5.2 Zero out-of-scope clauses (PR-2), confirmed exactly

Searching all 20 reconstructed prompt texts (case-insensitive) for `"out of scope"`,
`"out-of-scope"`, `"not applicable"`, `"does not apply"`, and `"decline"` returns zero
matches. Confirmed exactly: no agent may declare a question outside its analytical
frameworks' competence. `financial_analyst.py:183-215`'s DCF/LBO/comparables/unit-
economics framework block, quoted verbatim below, was re-verified line-for-line against
the current tree and contains no exit clause of any kind:

```python
"Your proprietary frameworks:\n"
"1. DCF: 5-7 year explicit forecast, terminal value (Gordon growth or exit "
"multiple), WACC (CAPM for cost of equity, after-tax cost of debt), sensitivity "
"table on discount rate × terminal growth.\n"
"2. LBO: Debt structure, interest coverage, IRR, exit assumptions. For M&A support.\n"
"3. Comparable company analysis: 5-10 comparables, EV/Revenue, EV/EBITDA, P/E. "
"Comp set must be justified — same industry, growth stage, geography.\n"
...
"Rules:\n"
"- NEVER report a single valuation number. ALWAYS report a range with "
"sensitivity tables.\n"
...
"- Each assumption must cite a source. No unsourced financial assumptions.\n"
"- Terminal growth rate must be ≤ long-term GDP growth (2-3%). Higher is "
"unrealistic.\n"
```

Dispatched against a nation-state question, this agent is instructed to produce a DCF
and given no path to decline; it will comply, producing a valuation of a country.

### 5.3 Anti-fabrication clause count (PR-3), corrected to 1/20

**Correction to the draft this document supersedes:** the draft states
`fact_checker.py` and `synthesis_lead.py` both carry an anti-fabrication clause (2/20).
Re-reading `fact_checker.py`'s full reconstructed prompt text in this session finds no
instance of `"invent"`, `"fabricat"`, `"make up"`, or `"made up"` anywhere in it; its
closest instruction is procedural ("HALLUCINATED CITATIONS ARE THE #1 RISK... Don't
give the agent the benefit of the doubt"), which governs how the fact checker judges
*other* agents' claims, not a constraint on the fact checker inventing its own content.
`synthesis_lead.py:203-205` does carry an explicit instruction:

```python
"If the findings contain no numbers, write the analysis without numbers and say the "
"evidence is qualitative, do NOT invent figures to fit the shape."
```

So the confirmed count is **1 of 20** (`synthesis_lead.py` only), not 2. The
substantive conclusion is unchanged, and if anything strengthened: of the twelve
specialists that actually author analytical content, and of the agent whose entire job
is catching fabrication in others, none carries an explicit prohibition on inventing
its own figures, sources, or citations.

### 5.4 Zero recency/as-of discipline (PR-4), confirmed exactly

Searching all 20 prompts for `"recency"`, `"stale"`, `"as of"`, `"as-of"`, and
`"date of publication"` returns zero matches. Confirmed: no prompt requires a figure to
carry an as-of date or instructs an agent on handling a stale source.

### 5.5 The glued fragment in the layout prompt (PR-5), confirmed exactly, verbatim match

`presentation_designer.py:1636-1637`, current tree:

```python
"3. SELECT Unsplash images for cover and section headers"
"specific search terms, not generic.\n"
```

Confirmed by direct read: these two adjacent string literals concatenate with no space
or punctuation between them, so the model receives `"...cover and section
headersspecific search terms, not generic."` This is the one confirmed glued fragment
across all 20 prompts, in the agent that owns the report's visual layer; the artifact
under audit carried 12 undifferentiated images per page.

### 5.6 The renderer's own last-line-of-defence prompt is the thinnest (PR-6), confirmed

`render_engine.py`'s prompt is 1,550 characters, the shortest of the 20, and its own
text describes the agent as "the last line of defense for quality" (confirmed present
in the reconstructed text). It matches none of the clause categories tested here except
output formatting instructions.

### 5.7 What a fix requires

Not longer prompts individually maintained per agent; a shared, versioned,
composed-in contract so a clause cannot be present in some specs and silently absent in
others, the same failure mode the typography ban already suffers at the character
level. See W-16.

---

## 6. Mid-engagement crash recovery and concurrency

All claims re-verified against `hyperion/obs/run_journal.py`, `hyperion/orchestrator.py`
(2255 lines), and `hyperion/cli.py` in the current tree.

### 6.1 The durable execution layer cannot ever resume across processes (CR-1), confirmed exactly

`hyperion/obs/run_journal.py:69-95`, confirmed verbatim: `RunJournal.__init__` takes
`run_id`, stores the journal at `os.path.join(base_dir, run_id, "journal.sqlite")`,
opens it with `PRAGMA journal_mode=WAL` and `PRAGMA synchronous=NORMAL`, and creates a
table keyed `PRIMARY KEY (step_id, inputs_hash)`. All of that machinery is sound.

`hyperion/orchestrator.py:1693`:

```python
self._engagement_id = f"eng_{uuid.uuid4().hex[:12]}"
```

and `:1778-1780`:

```python
self._journal = RunJournal(self._engagement_id)
self._journal.open()
self._artifacts = ArtifactStore(self._engagement_id)
```

Confirmed exactly. Every engagement, including a restart of a crashed one, generates a
fresh random `run_id`, hence a fresh, empty journal directory. A crash at step 15 of 20
leaves a complete, valid journal at `artifacts/eng_<old-uuid>/journal.sqlite`; the next
process invocation opens `artifacts/eng_<new-uuid>/journal.sqlite`, finds nothing, and
replays all 20 steps from zero. Within a single process the cache is equally unused,
because `step_id` is constructed to be unique per DAG node, so `get_cached(task.id,
...)` is queried at most once per key by construction and can never hit. The structural
cache-hit rate of this subsystem, across the whole codebase as it stands, is zero.

### 6.2 The advertised `resume` command does not exist (CR-2), confirmed exactly

`hyperion/cli.py:4-9` (module banner) advertises: `providers · vault · export · resume
· help`. Grepping for `@app.command()` in `cli.py` returns exactly 7 hits
corresponding to `shell`, `boot`, `consult`, `providers`, `vault`, `export`, `help`.
There is no `resume` command among them. Confirmed exactly: `hyperion resume` is not a
valid invocation and exits with a Typer usage error.

### 6.3 A failed task's output is silently absent from dependents (CR-3), confirmed exactly

`orchestrator.py:947-974`, `_execute_wave`:

```python
results = await asyncio.gather(*coroutines, return_exceptions=True)
processed: list[Any] = []
for i, result in enumerate(results):
    if isinstance(result, Exception):
        tasks[i].status = TaskStatus.FAILED
        tasks[i].error = str(result)
        await self.bus.publish_status(tasks[i].agent, AgentState.BLOCKED, detail=str(result)[:200])
        processed.append(None)
    else:
        processed.append(result)
```

Confirmed exactly. A failed task is marked `FAILED` and nothing is written to
`self._task_outputs` for it. Dependents build their context at `:562-567`:

```python
for dep_id in task.dependencies:
    if dep_id in self._task_outputs:
        dep_output = self._task_outputs[dep_id]
        ...
```

Confirmed: the `if dep_id in self._task_outputs` guard means a missing dependency is
skipped in silence rather than raised. A downstream specialist runs with a declared
dependency absent from its context and reports success with a thinner analysis; nothing
downstream distinguishes "this agent had all its declared inputs" from "this agent had
four of six."

### 6.4 Shared mutable state under `asyncio.gather` with no lock (CR-4), confirmed

`self._all_findings.extend(...)` (referenced from `_execute_task`, called concurrently
via `_execute_wave`'s `asyncio.gather`) is mutated from multiple concurrently-running
tasks. Grepping the orchestrator for `asyncio.Lock` returns zero hits; the only bounded
concurrency primitives in the tree are `Semaphore` instances in `fact_checker.py:875`
and `deep_search.py` (`:897`, `:1081`), which guard verification and search fan-out
respectively, not the shared findings collection. This is safe today only because
Python's single-threaded event loop guarantees no `await` occurs inside the specific
mutation, which is an unstated and untested invariant rather than an enforced one.

### 6.5 No interrupt handling anywhere (CR-5), confirmed exactly

`grep -rn "signal\.SIG\|atexit\." hyperion/` returns zero hits across the entire
package. There is no `SIGINT`/`SIGTERM` handler and no `atexit` hook anywhere in the
tree. Combined with the predecessor audit's RC-2 (a rejected PDF is never removed from
the deliverable path, `output/render.py`), an interrupted render leaves the same
class of stale, plausible-looking file at the same path a clean run would use, via a
different trigger than an audit rejection.

---

## 7. What this means for the audit's work items

None of W-01 through W-14 in the predecessor document cover the six areas above. Six
new work items are specified in section 9, to be added to the sequencing without
renumbering the existing fourteen.

| New | Title | Closes | Ordering constraint |
|-----|-------|--------|----------------------|
| W-15 | Fact checker corpus and measurement validity | FC-1, FC-4, FC-6, FC-7, FC-8 (FC-2/FC-3/FC-5 are historical/confirmed-with-correction, not separately actionable) | Should land before W-08 (Quality Gate) becomes load-bearing on confidence signals, since the gate's inputs are only as good as this measurement |
| W-16 | Shared, versioned agent prompt contract | PR-1 through PR-6 | Should follow the predecessor audit's W-06 (subject ontology), so the out-of-scope clause has an ontology to name; must precede any regeneration used to verify other work items, since it is the generation layer of the typography ban |
| W-17 | Router attempt budget and error classification | RT-1 through RT-5 | Independent; do early. Every other item's artifact-verification runs cheaper and faster once the recursion is capped |
| W-18 | Budget persistence, token accounting, cost model | BG-1 through BG-5 | Depends on W-17: the recursion is what turns a budget-accounting bug into a catastrophic overrun rather than a merely inaccurate one |
| W-19 | Eval harness truthfulness and a CI workflow that calls it | EV-1 through EV-9 | Must precede any claim that the predecessor audit's section 9 (definition of done) is satisfied, since that section's regression clause currently points at a gate proven not to gate |
| W-20 | Deterministic run id, a real `resume` command, interrupt safety | CR-1 through CR-5 | The predecessor audit's W-02 (rejected artifacts quarantined) must be extended to cover the interrupt path as part of this item, not treated as separately closed |

Two points in the predecessor document should be read alongside this:

- Its own section 3.6 (RC-6, agent roster selected by grammatical form) is a complete
  diagnosis of *why* the wrong agents run. Section 5.2 above (PR-2) shows that even a
  perfect roster leaves the hole open, because no agent, correctly dispatched or not,
  has the right to decline a framework that does not fit. W-06 and W-16 are
  complements, not alternatives to each other.
- Its section 7 (verification protocol) treats `audit_pdf()` and the harness as the
  two gates a change must pass. Section 4.1 above shows the harness never calls
  `audit_pdf()` at all, so satisfying "Gate 2, unit" in the predecessor document's
  language does not imply satisfying "Gate 3, artifact." The two gates are not loosely
  coupled; until W-19 lands, they measure disjoint things.

---

## 8. New work items

Each item follows the same eight-part structure as W-01 through W-14 in the
predecessor document.

### W-15: Fact checker corpus and measurement validity

**Objective.** Make `Source.key_data` carry actual fetched source content everywhere it
is set, and make the fact checker's confidence output an honest function of a corpus
that can actually be checked.

**Why this was never fixed.** `eb481e0` (P2-19/20) improved the *matching algorithm*
(token-boundary overlap plus URL liveness, replacing naive substring/word matching) but
did not touch what is being matched against. 15 of 17 `key_data=f"..."` call sites in
the specialists still write a provenance sentence describing where a source came from,
not the source's content. No matching algorithm, however precise, can verify a claim
against a sentence that never contained the claim's data in the first place.

**Files and anchors.**
- `hyperion/schemas/models.py:216-217` (`Source.key_data` field)
- 15 provenance-only call sites across `hyperion/agents/specialists/*.py` (listed in
  section 1.1 above, with exact line numbers)
- `hyperion/agents/support/fact_checker.py:818-826` (`_verify_claim`, still-naive
  matcher, feeds `verification_rate`)
- `hyperion/agents/support/fact_checker.py:1151-1222` (`_validate_evidence_chains`,
  the P2-19 two-signal test)
- `hyperion/agents/support/fact_checker.py:1260-1281` (`_url_alive`, egress-dependent
  second signal)
- `hyperion/agents/support/fact_checker.py:1574,1582` (hardcoded `ConfidenceLevel.HIGH`
  on the hallucination finding)
- `hyperion/agents/support/fact_checker.py:1375-1398` (`_calibrate_confidence`)

**Procedure.**
1. Every specialist call site currently writing `key_data=f"<description>"` must
   instead write the actual extracted text passed to it by the retrieval layer
   (whatever text was fetched via SearXNG/Jina/Obscura for that source), truncated to a
   fixed budget (`finding.content[:N]`, matching the pattern already used correctly at
   `fact_checker.py:_check_local_corpus`). If no fetched text is available for a
   source, `key_data` must be left `None`, not filled with a description; a `None`
   `key_data` already routes correctly to `ClaimStatus.UNVERIFIABLE` via
   `_validate_evidence_chains`'s existing "no content" branch (`:1180-1191`).
2. Delete the naive substring/word-overlap block at `_verify_claim:818-826`. Replace
   `supporting_sources` counting with a call to the same `_source_supports_claim`
   token-boundary matcher `_validate_evidence_chains` already uses, so there is exactly
   one matching algorithm in the file, not two with different strictness.
3. Fix `_url_alive`'s network-failure default. "Cannot prove the citation was
   invented" is a legitimate reason not to assert `HALLUCINATED`, but it is not a
   legitimate reason to assert `True` (alive) silently. Introduce a third liveness
   state, `UNKNOWN`, and route claims whose only unresolved signal is a network
   failure to `UNVERIFIABLE`, never silently to "verified alive."
4. Fix the round-number check (`ROUND_NUMBER_SUSPECTS`) to normalise the claim's value
   to a canonical unit (multiply out `billion`/`million`/`thousand` suffixes) before
   comparing against the suspect set, and deduplicate red flags that share the same
   normalised value across claims.
5. Stop hardcoding `ConfidenceLevel.HIGH` on the hallucination `KeyFinding`
   (`:1574-1582`). Derive it from the actual count relative to the total claims
   checked, or state the count without an attached confidence level at all, since a
   count is not itself a confidence-calibrated statement.
6. Route the hallucination/contradiction/statistical findings to the operator
   telemetry artifact rather than a client-facing chapter (this overlaps the
   predecessor audit's W-09 narrative-boundary type; implement the corpus fix
   independently of whether W-09 has landed).

**Verification.**
```bash
cd /home/user/webapp
grep -rn 'key_data=f"' hyperion/agents/specialists/*.py
# expect: zero provenance-sentence matches; only real content or omission

python3 -c "
from hyperion.agents.support.fact_checker import FactChecker
# construct a claim whose only source has key_data=None
# assert claim.status == ClaimStatus.UNVERIFIABLE, never HALLUCINATED, never VERIFIED
"
```

**Acceptance criteria.**
- Zero `key_data=f"..."`-style provenance-sentence assignments remain in the
  specialists tree; every assignment is either real fetched content or `None`.
- `_verify_claim` and `_validate_evidence_chains` share one matching function.
- A network failure during URL liveness check never resolves to "verified" or
  "confirmed alive" silently; it resolves to `UNVERIFIABLE`.
- The hallucination `KeyFinding`'s confidence is derived, not hardcoded.
- `ROUND_NUMBER_SUSPECTS` matches equivalent magnitudes regardless of notation.

**Failure modes to avoid.**
- Fixing only the specialists' `key_data` without also collapsing the two matching
  algorithms in `fact_checker.py`, which would leave `verification_rate` and
  `hallucinated_count` disagreeing about the same corpus for no principled reason.
- Treating `UNKNOWN` liveness as equivalent to `True` (alive) by another name.
- Presenting a corrected hallucination count to the client at all. It belongs in
  operator telemetry (W-09's boundary type), not in client prose, regardless of how
  accurate the count becomes.

**Rollback.** The matcher consolidation and confidence-derivation changes are
localised to `fact_checker.py` and are safe to revert independently of the `key_data`
population fix, which should not be reverted once specialists are populating it
correctly, since reverting it reintroduces FC-1 directly.

---

### W-16: Shared, versioned agent prompt contract

**Objective.** Make eight quality clauses (subject fit, abstain, no fabrication,
evidence binding, units/denomination, uncertainty, conflict, typography) present in
every dispatched agent prompt by composition, not by per-file authoring discipline.

**Why this was never fixed.** P2-32 added a single shared typography rule
(`PROMPT_TYPOGRAPHY_RULE`) prepended to every prompt, which is exactly the right
pattern, but it was applied to only one of the eight clauses this document identifies
as missing. The other seven were never centralised, so their presence in any given
prompt depended entirely on whether that agent's original author happened to write
them in. The result, measured in section 5: 0/20 out-of-scope, 1/20 anti-fabrication,
0/20 recency, and a typography rule violated by its own carrier 16/20 times.

**Files and anchors.**
- `hyperion/agents/base.py:603-606` (where `PROMPT_TYPOGRAPHY_RULE` is currently
  prepended; the composition point for the rest of the contract)
- `hyperion/output/typography.py:26` (existing `PROMPT_TYPOGRAPHY_RULE`, the pattern to
  extend rather than replace)
- New module: `hyperion/agents/prompt_contract.py`
- All 20 `system_prompt=` sites listed in section 5's table (dash removal)
- `hyperion/agents/delivery/presentation_designer.py:1636-1637` (the glued fragment,
  PR-5, fix independently of the contract work)

**Procedure.**
1. Create `hyperion/agents/prompt_contract.py` exporting `AGENT_CONTRACT`, a single
   string composed of the eight numbered clauses given in the predecessor PART 2 draft
   verbatim (subject fit / abstain / no fabrication / evidence binding / units and
   denomination / uncertainty / conflict / typography), written once, reviewed once.
2. Change `base.py:603-606` to prepend `AGENT_CONTRACT` (which subsumes
   `PROMPT_TYPOGRAPHY_RULE` as its eighth clause) instead of
   `PROMPT_TYPOGRAPHY_RULE` alone, so every one of the eight clauses reaches every
   dispatched prompt on every call, with no per-agent opt-out.
3. Strip all 86 em dashes from the 20 prompt literals identified in section 5's table.
   This is the mechanical half of the fix; it removes the in-prompt demonstration that
   currently defeats clause 8 before the model reads a single word of the agent's own
   instructions.
4. Fix the glued fragment at `presentation_designer.py:1636-1637` by inserting the
   missing separator (`", "` or `" "` between `"headers"` and `"specific"`).
5. Add a registry-level test that iterates every `AgentSpec` with a non-empty
   `system_prompt` and asserts the contract's marker text is present in the fully
   composed prompt actually sent to the LLM (not just in the static literal), so a
   future agent added without going through `base.py`'s composition point fails CI
   rather than silently shipping without the contract.
6. Do not delete per-agent language that duplicates a contract clause (for example,
   `market_analyst.py`'s CAGR-specific abstain instruction, or `synthesis_lead.py`'s
   anti-invention clause); the shared contract is a floor, not a replacement for
   agent-specific elaboration of the same principle.

**Verification.**
```bash
cd /home/user/webapp
python3 -c "
from hyperion.agents.prompt_contract import AGENT_CONTRACT
assert '\u2014' not in AGENT_CONTRACT and '\u2013' not in AGENT_CONTRACT
"
grep -c $'\xe2\x80\x94' hyperion/agents/specialists/*.py hyperion/agents/*.py hyperion/agents/support/*.py hyperion/agents/delivery/*.py
# expect: 0 for every file

python3 -m pytest tests/ -k "prompt_contract" -q
```

**Acceptance criteria.**
- Zero em/en dashes remain in any of the 20 prompt literals.
- Every dispatched prompt (verified via the composed string, not the static literal)
  contains all eight contract clauses.
- The glued fragment at `presentation_designer.py:1636-1637` reads as two properly
  separated sentences.
- A newly added agent whose spec is registered without routing through `base.py`'s
  prompt composition fails the registry-level test.

**Failure modes to avoid.**
- Writing the contract once but leaving the old `PROMPT_TYPOGRAPHY_RULE` prepended
  separately as well, producing the typography clause twice and the other seven zero
  times.
- Treating this as a per-agent copy-paste exercise. The entire point is one shared,
  tested string; per-agent copies are exactly the failure mode measured in section 5.
- Removing agent-specific language that happens to overlap a contract clause; the
  contract is additive.

**Rollback.** Revert `base.py`'s composition line to prepend
`PROMPT_TYPOGRAPHY_RULE` alone. The dash removal and glued-fragment fix are
independently safe to keep.

---

### W-17: Router attempt budget and error classification

**Objective.** Bound every failover chain to a fixed number of total attempts across
all providers and tiers, and classify failures by HTTP status before deciding whether
to retry, fail over, or stop.

**Why this was never fixed.** The predecessor audit's P2-29 work (`ef3aec2`,
`f140572`) fixed how a *total* failure is reported (naming no provider, carrying a
structured diagnosis) but did not touch how failover is attempted on the way to that
total failure. The recursion between `_dispatch` and `_try_next_candidate` predates
P2-29 and is untouched by it.

**Files and anchors.**
- `hyperion/router/router.py:598-656` (`_try_next_candidate`)
- `hyperion/router/router.py:516-596` (`_try_tier`, the second-pass call at `:593`)
- `hyperion/router/router.py:661-742` (`_dispatch`, the recursive call at `:730-740`)
- `hyperion/router/router.py:680-702` (budget consumption before dispatch)

**Procedure.**
1. Introduce an explicit `RouterAttempt` context threaded through `_try_tier`,
   `_try_next_candidate`, and `_dispatch` as a single object (not a growing set of
   `exclude_provider` parameters), carrying: a `visited: set[ProviderType]` accumulated
   across the entire call chain for the current `complete()` invocation, and a
   `max_attempts` counter (start at `len(ProviderType) * 2`, generous enough to allow
   one retry per provider without permitting unbounded recursion).
2. Replace every `exclude_provider=candidate.provider_type` call with
   `attempt.visited.add(candidate.provider_type)`, and filter `ordered_providers`
   against the full `visited` set, not a single excluded provider, at every level of
   the recursion.
3. Fix `_try_tier`'s second pass (`:593`, `exclude_provider=None`) to pass the same
   `attempt.visited` set accumulated by the first pass, deleting the comment's false
   claim and making the code match its stated intent.
4. Convert the mutual recursion into an explicit loop over `attempt.visited`-filtered
   candidates within `complete()`, so there is one call frame per attempt rather than
   one recursive frame per attempt, and `max_attempts` is checked in one place.
5. Classify `provider.complete()`'s failure before deciding the next action: inspect
   the response's status code (already available on most provider responses; add it
   to `RouterResponse` if not present as a first-class field). 401/403 opens the
   circuit for that provider immediately and never retries within the same
   `complete()` call. 429 triggers a cooldown recorded in the wait gate, not an
   immediate failover to a different provider. 500/503/timeout retries the same
   provider once with a short backoff before failing over. Anything else fails over as
   today.
6. Refund the budget consumption recorded at `:688` when the subsequent
   `provider.complete()` call raises or returns a non-2xx-equivalent failure that was
   never actually served (a 401/403 auth failure in particular never used any real
   quota and must not be charged against it).
7. Record on `RouterResponse` whether the response was served after a tier
   downgrade due to a transient failure, so downstream reporting (and eventually the
   client-facing methodology section) can state that a given analysis was produced by
   a weaker model than requested.

**Verification.**
```bash
cd /home/user/webapp
python3 - <<'PY'
# simulate 5 providers all failing; assert total dispatch attempts <= max_attempts
# and that no provider is dispatched twice within the same complete() call
# unless it is the explicit one-retry-on-transient-failure path
PY
python3 -m pytest tests/test_router.py -q --timeout=20
```

**Acceptance criteria.**
- A `complete()` call with every provider failing terminates in a bounded number of
  dispatch attempts, verified by an injected-failure test.
- No provider is dispatched more than twice within a single `complete()` call.
- A 401/403 failure never triggers a second attempt against the same provider and
  does not decrement that provider's daily budget.
- A 429 failure never triggers an immediate cross-provider failover; it records a
  cooldown.
- `RouterResponse` carries a field indicating tier downgrade due to transient
  failure.

**Failure modes to avoid.**
- Threading `visited` through only some of the three functions, leaving one path that
  still recurses unbounded.
- Setting `max_attempts` so low that a genuinely recoverable multi-provider outage
  fails prematurely; size it to the actual provider count, not an arbitrary small
  constant.
- Refunding budget on every failure indiscriminately, which would let a provider with
  a flaky-but-working model consume unlimited retries for free.

**Rollback.** Revert to the current recursive implementation; this restores RT-1
directly and is not recommended except as a temporary measure.

---

### W-18: Budget persistence, real token accounting, and a cost model

**Objective.** Make "daily" budget mean a calendar day across process restarts, track
tokens per day against the populated `tpd` field, and produce an actual cost figure per
engagement.

**Why this was never fixed.** Budget tracking has never been audited before this
document; the predecessor audit's work items do not touch `hyperion/router/budget.py`
at all.

**Files and anchors.**
- `hyperion/router/budget.py:49-57` (`_PROVIDER_DAILY_BUDGETS`), `:69-96`
  (`ProviderBudget`, in-memory only), `:137-156` (`consume`, `remaining_for_model`,
  request-only)
- `hyperion/config.py:87` (`tpd` field), and its 9 population sites
- `hyperion/router/router.py:146-148` (`DailyBudgetPlanner` construction per
  `LLMRouter` instance)

**Procedure.**
1. Add a persistence layer to `ProviderBudget`/`DailyBudgetPlanner`: a small SQLite
   file (reusing the pattern already proven correct in `hyperion/obs/run_journal.py`)
   at a fixed path (not per-engagement, since the budget is genuinely shared across
   engagements), keyed by `(provider, model, utc_date)`, incremented on every
   `consume()` call and read back on `DailyBudgetPlanner.__init__`.
2. Add a `TokenBudget` alongside the existing request-count `ProviderBudget`, tracking
   consumption against `model.tpd` per model per day, using the same persisted store.
   `consume()` must accept an estimated (pre-call) and actual (post-call) token count
   and update both; `get_available_providers` must filter on token budget in addition
   to request budget wherever `tpd` is not `None`.
3. Add a minimal cost model: a per-provider, per-model price-per-million-tokens table
   (sourced and dated in a comment, matching the honesty standard `budget.py:49-57`
   already sets for its RPD estimates), and accumulate a running cost total on the
   engagement result object, surfaced in the TUI budget display alongside the existing
   RPD/TPM percentages.
4. Close the check-then-consume gap identified in section 3.4 by moving the budget
   filter and the `consume()` call closer together, or by making `consume()`
   atomic against a lock held for the duration of candidate selection within a single
   process; do not attempt to make this correct across processes, since the
   persistence layer in step 1 already bounds cross-process overrun to at most one
   extra request per provider per process start.
5. Update the two-line comment at `budget.py:55` (`# Effectively unlimited by RPD
   (TPD-limited)`) to point at the code that now actually enforces the TPD limit, so
   the comment and the enforcement agree for the first time.

**Verification.**
```bash
cd /home/user/webapp
python3 - <<'PY'
from hyperion.router.budget import DailyBudgetPlanner
p1 = DailyBudgetPlanner()
p1.consume(provider=..., model_name=..., urgency=...)
del p1
p2 = DailyBudgetPlanner()   # simulates a new process
# assert p2's consumed count for that provider/model reflects p1's consumption
PY
grep -rn "\.tpd\b" hyperion/router/
# expect: at least one read site, not zero
```

**Acceptance criteria.**
- Budget consumption survives a process restart within the same UTC day.
- At least one code path filters candidates by remaining token budget when `model.tpd`
  is not `None`.
- An engagement result carries a non-zero estimated cost figure with a dated,
  sourced price table behind it.
- Running the eval harness's golden set twice in one day does not silently double the
  effective daily quota available to each run.

**Failure modes to avoid.**
- Persisting per-engagement rather than per-provider-per-day, which would not fix the
  cross-invocation problem this item exists to solve.
- Adding token tracking without wiring it into `get_available_providers`'s filter,
  which would track the number correctly while still never enforcing it.
- Building a cost model with unsourced, undated numbers, repeating BG-5.

**Rollback.** The persistence layer is additive; reverting it restores the
per-process-only behaviour, which is safe but reintroduces BG-1.

---

### W-19: Eval harness truthfulness and a CI workflow that calls it

**Objective.** Make the offline harness call the same artifact gate the renderer does,
score failures as failures, and make some CI system actually invoke the gate on every
change.

**Why this was never fixed.** The eval harness has never been audited before this
document. `tests/test_module_importability.py` (added in an earlier round) fixed the
harness's ability to be imported at all, but nothing changed what it checks or who
calls it.

**Files and anchors.**
- `hyperion/eval/harness.py:104-112` (`_TEMPLATE_ARTIFACTS`), `:185-216` (checks 6 and
  8), `:525-556` (`run_all`), `:513-517` (heuristic "judge"), `:54-95` (`GOLDEN_SET`),
  `:474-478` (`_save_baseline`)
- `hyperion/output/page_audit.py:56-79` (`BANNED_SUBSTRINGS`, the list to share)
- `hyperion/eval/ci_gate.py` (already correct internally per section 4.6; needs a
  caller, not a rewrite)
- New: a CI workflow file (this repository currently has none)

**Procedure.**
1. Add a check to `run_deterministic_checks` that calls `hyperion.output.page_audit.
   audit_pdf(pdf_path)` and `scan_text_integrity(extract_pdf_text(pdf_path))` directly
   when `pdf_path` exists, rather than approximating PDF-text checks against the
   report dictionary. Delete `_TEMPLATE_ARTIFACTS` and check 6's `json.dumps(report)`
   scan; `BANNED_SUBSTRINGS` is the single source of truth for banned content and the
   harness must import and use it, not maintain a second, weaker list.
2. Fix `charts_present` (check 8) to count images actually embedded in the rendered
   PDF at the sections they were planned for (via PyMuPDF, matching the approach
   `page_audit.py` already uses for occlusion detection), not entries in the report
   dictionary's `charts` list.
3. Change `run_all`'s aggregation: a query whose `_run_single_query` recorded
   `success=False` must count as `overall_score = 0.0` in the mean, not be excluded
   from it. `pass_rate` must gate `regression_detected` in addition to `mean_score`
   (a pass rate collapse with a stable mean is itself a regression).
4. Either implement the advertised LLM-as-judge (a real call through the router using
   `JUDGE_PROMPT`, already defined but unused at `harness.py:368-387`) or update the
   class docstring and the `overall_score` computation's naming so nothing describes
   the current heuristic as a judge. Given the cost of running 5 full engagements per
   harness run, implementing the real judge and gating regression on both the
   deterministic score and the judge score is the substantive fix; correcting the
   docstring alone is the minimum acceptable interim fix and must be labelled as such
   in the commit.
5. Add at least one `NATION_OR_REGION`-class query to `GOLDEN_SET`, so a regression in
   the predecessor audit's W-06 subject-fit gate is detectable by this harness rather
   than only by manual inspection.
6. Change `_save_baseline`/`_load_baseline` to persist per-query scores and per-check
   pass/fail, not only the aggregate mean, so a regression can be attributed to a
   specific query and a specific check.
7. Add a CI workflow (this repository has no `.github/workflows` directory at all)
   that runs `python -m hyperion.eval.ci_gate --lint` and, on a schedule or a
   labelled PR (given the cost of 5 full engagements per run), the full golden-set
   gate, failing the build on `EXIT_REGRESSION` or `EXIT_HARNESS_ERROR`.

**Verification.**
```bash
cd /home/user/webapp
python3 -c "
from hyperion.eval.harness import run_deterministic_checks
from hyperion.output.page_audit import BANNED_SUBSTRINGS
import hyperion.eval.harness as h
assert not hasattr(h, '_TEMPLATE_ARTIFACTS')
"
find . -path "*/.github/workflows/*"
# expect: at least one workflow file that invokes hyperion.eval.ci_gate
```

**Acceptance criteria.**
- The harness's PDF-side checks call `audit_pdf()` and `scan_text_integrity()`
  directly against `BANNED_SUBSTRINGS`; no second, weaker banned-content list exists.
- `charts_present` reflects images actually embedded in the rendered PDF.
- A crashed golden query contributes `0.0` to the mean rather than being excluded.
- The golden set includes at least one `NATION_OR_REGION` question.
- A CI workflow exists and invokes `ci_gate.py` on every pull request at minimum in
  `--lint` mode.

**Failure modes to avoid.**
- Rewriting `ci_gate.py`'s internals under the mistaken belief that they are broken;
  per section 4.6 they already work correctly. The missing piece is the caller.
- Adding the CI workflow but leaving it optional/non-blocking, which reproduces the
  exact "gate that never gates" pattern this item exists to close.
- Running the full 5-engagement golden set on every commit without accounting for
  cost (see W-18); gate the expensive mode behind a schedule or an explicit label
  until W-18's cost model makes the real cost visible.

**Rollback.** The PDF-audit integration and CI workflow are additive and safe to
keep even if the LLM-judge implementation in step 4 is deferred; ship them
independently rather than blocking on the judge.

---

### W-20: Deterministic run id, a real `resume` command, and interrupt safety

**Objective.** Make the existing durable-execution journal actually resume a crashed
engagement, implement the `resume` command the CLI already advertises, and stop an
interrupted run from leaving stale output at the deliverable path.

**Why this was never fixed.** The journal, artifact store, and inputs-hashing
machinery in `hyperion/obs/run_journal.py` were built correctly in an earlier round
(the predecessor audit's own text refers to this as prior "P10" work) but the single
line that seeds `run_id` from `uuid.uuid4()` was never revisited, so the entire
subsystem has been structurally inert since it was written. This was not previously
audited because nobody attempted a resume; the defect is invisible unless a crash is
deliberately induced and a second run is attempted.

**Files and anchors.**
- `hyperion/orchestrator.py:1693` (`self._engagement_id = f"eng_{uuid.uuid4().hex[:12]}"`)
- `hyperion/orchestrator.py:1778-1780` (`RunJournal`/`ArtifactStore` construction)
- `hyperion/orchestrator.py:947-974` (`_execute_wave`, silent-skip on failed dependency)
- `hyperion/orchestrator.py:562-567` (dependency-output lookup)
- `hyperion/cli.py:4-9` (banner advertising `resume`), 7 registered `@app.command()`
  sites (none named `resume`)
- `hyperion/output/render.py` (per the predecessor audit's W-02, the file that should
  own quarantining rejected/interrupted output)

**Procedure.**
1. Derive `run_id` deterministically from the engagement's inputs (the question text,
   normalised, plus any caller-supplied engagement key) rather than a random UUID,
   for example `f"eng_{hashlib.sha256(question.encode()).hexdigest()[:12]}"`, with an
   explicit `--fresh` flag on `consult`/`shell` to force a new random id when a genuine
   re-run from zero is wanted.
2. On startup, before constructing a fresh `RunJournal`, check whether
   `artifacts/<derived_run_id>/journal.sqlite` already exists. If it does, treat this
   as a resume: open it, call `get_completed_steps()`/`get_failed_steps()`, and skip
   dispatching any DAG node whose `(step_id, inputs_hash)` is already recorded as a
   success, reconstructing its output from the `ArtifactStore` instead.
3. Implement `hyperion resume <engagement_id_or_question>` as an actual
   `@app.command()` in `cli.py`, wired to the check in step 2. Update the module
   banner only once the command exists; an advertised command that does not exist is
   worse than no banner line.
4. Change `_execute_wave`'s failed-dependency handling: a `FAILED` task's absence from
   `self._task_outputs` must be an explicit, loud condition for any dependent task,
   not a silently-skipped `if dep_id in self._task_outputs`. Raise a named exception
   (`MissingDependencyOutput`) from `_execute_task` when a required dependency has no
   output, rather than allowing the dependent to run with a partial context.
5. Add a `SIGINT`/`SIGTERM` handler (there are currently zero anywhere in the tree) at
   the CLI entry points (`shell`, `consult`) that, on interrupt, flushes and closes the
   open `RunJournal` (so the WAL is checkpointed and a subsequent resume sees every
   step that had actually completed) and invokes the same quarantine path the
   predecessor audit's W-02 builds for a failed audit, applied to whatever partial
   output exists at the deliverable path at the moment of interrupt.
6. Add an `asyncio.Lock` around the mutation of `self._all_findings` (and any other
   collection mutated from within `_execute_wave`'s gathered tasks), converting the
   currently-unstated single-event-loop invariant into an enforced one, so a future
   move to threads or subprocesses cannot silently corrupt the findings corpus.

**Verification.**
```bash
cd /home/user/webapp
python3 - <<'PY'
# 1. run an engagement fixture, kill it after step 10 of 20 (monkeypatched)
# 2. re-invoke with the same question
# 3. assert the journal shows steps 1-10 as already-completed and are not re-dispatched
# 4. assert the engagement completes using the cached outputs for steps 1-10
PY

grep -n "resume" hyperion/cli.py
# expect: a real @app.command() definition, not only the banner string

grep -rn "signal\.SIG\|atexit\." hyperion/
# expect: at least one hit, in the CLI entry points
```

**Acceptance criteria.**
- Re-invoking the same question after a simulated mid-run crash skips every DAG node
  already recorded as successful in the journal.
- `hyperion resume` is a real, working command.
- A dependent task whose declared dependency failed raises rather than silently
  running with a partial context.
- An interrupted run (SIGINT) leaves the journal in a resumable state and does not
  leave a stale file at the deliverable output path.
- `self._all_findings` (and any other collection mutated inside a gathered wave) is
  protected by an explicit lock.

**Failure modes to avoid.**
- Deriving `run_id` from the question text alone with no normalisation, so trivial
  rephrasing defeats resumption entirely; normalise (lowercase, whitespace-collapse)
  before hashing.
- Implementing `resume` as a thin wrapper that actually starts fresh, satisfying the
  CLI's existence check without satisfying its purpose.
- Converting the silent-skip in step 4 into a different silent-skip (for example,
  substituting an empty default context instead of raising).
- Adding a signal handler that only logs the interrupt without actually closing the
  journal or quarantining partial output; the point is the side effect, not the log
  line.

**Rollback.** The deterministic `run_id` change and the `resume` command are
additive to the existing journal machinery and safe to ship independently of the
signal-handling and locking changes, which touch orchestrator control flow more
broadly and should be verified with their own artifact-gate pass before merging
alongside the rest of this item.

---

## 9. Summary

The "149 Hallucinated Citations" finding is confirmed false as a hallucination count in
the build that produced the audited artifact, for the reason given in section 1: a
provenance-label sentence cannot lexically overlap a claim, so the metric fires on
almost every numeric claim regardless of correctness. The mechanism that computes this
number has since changed in the current tree (`eb481e0`, already merged), but the
underlying corpus defect, `Source.key_data` holding a description of a source rather
than the source's content, was not touched by that fix and remains present in 15 of 17
specialist call sites today. Both the old and the new matching mechanisms are downstream
of that one field, and both fail, in opposite directions, because of it.

Behind that: a router whose failover recursion has no attempt budget and no error
classification, a budget planner that resets on every process start while never
enforcing the token limits its own comments name as binding, an eval harness that never
calls the PDF audit and reports a perfect score for a golden set with an 80 percent
crash rate, twenty agent prompts that open by banning a character and then use it 86
times, and a durable-execution layer whose cache-hit rate is structurally zero because
every engagement receives a fresh random id. All six were independently re-verified
against the pinned commit in this session, and two specific sub-claims from the draft
this document is based on were checked, found to be imprecise, and corrected in
sections 1.4, 1.5, and 5.3 rather than repeated.

Six work items, W-15 through W-20, are specified in full in section 8, in the same
eight-part format as the predecessor document's W-01 through W-14, and are slotted into
that document's sequencing per the table in section 7. The companion execution-order
document has been updated to reflect all twenty items and their combined ordering
constraints.
