# HYPERION v0.1 — Production Architecture

## Deep Research Intelligence System for AI Consulting

> **This is not a wrapper. This is not a generic LLM pipeline.**
> This is a proprietary consulting model — the engine that powers
> HYPERION Consulting. Every agent has skills. Every tool is used.
> Every output is McKinsey/BCG-grade. No bullshit. No filler. No idle components.

---

## 0. What HYPERION Is

HYPERION is a **dynamic consulting firm in software**. It takes a real
business question — enter a market, price a product, evaluate an acquisition,
defend a position — and runs it through a team of specialist AI agents that
assemble per engagement. Each agent has its own skills, its own tools, its own
model tier. They work in parallel, spawn junior sub-agents for deeper research,
reconcile their findings through a synthesis lead, and deliver one
recommendation as a premium PDF report.

**This is not a chatbot. This is not a wrapper around an LLM API.**
This is a multi-agent system where:

- The **Engagement Director** decomposes the question and builds a custom
  workflow DAG — no two engagements look the same.
- **14 specialist agents** each have domain expertise, assigned tools, and
  proprietary skills (DCF modeling, Porter's Five Forces, ESG scoring, etc.).
- **6 support/delivery agents** handle fact-checking, visualization, quality
  gating, and PDF composition.
- **Sub-agents** (junior) spawn for context isolation — a specialist sends a
  focused sub-question to a junior agent, gets structured findings back, and
  synthesizes them. This is how we handle context window limits without
  truncating or compressing.
- A **predictive wait gate** monitors TPM/RPM/RPD across 4 LLM providers in
  real-time and routes requests to avoid any 429 errors before they happen.
- Reports are **300 DPI PDFs** with Unsplash hero images, Plotly charts, and
  print-quality typography — no distortion, no blank pages, no generic stock.

**Cost: $0.00 per engagement.** Entirely free-tier LLMs.
**Runtime: 1-15 minutes.** Dynamic based on engagement complexity.
**Output: McKinsey/BCG-grade PDF.** Premium structure, real analysis.

### 0.1 The "Best Version" Mandate

Every agent in HYPERION must be the **best possible version of itself**. This
is not a suggestion. It is the core design constraint.

What "best version" means in practice:

- **Deeper than any consultant you could hire for free.** The Market Analyst
doesn't just "search and summarize." It decomposes a market using top-down
and bottom-up sizing, cross-validates with CAGR triangulation, segments by
behavioral/demographic/psychographic dimensions, and assesses market maturity
using the BCG growth-share matrix. It is a market sizing expert, not a
search-to-text converter.

- **Every skill is a real analytical framework.** The Financial Analyst doesn't
"analyze finances." It runs DCF with sensitivity tables, LBO modeling,
comparable company analysis, unit economics (LTV/CAC/payback/margin), and
break-even analysis. Each skill is a named, structured methodology with
defined inputs, outputs, and quality criteria — not a vague instruction to
"be thorough."

- **Every tool is wielded with intent.** The Competitive Intelligence agent
doesn't "use Obscura to browse competitor websites." It uses Obscura's stealth
mode to scrape competitor pricing pages without triggering bot detection,
extracts structured pricing data via `browser_evaluate`, cross-references
with Wayback Machine for historical pricing changes, and builds a strategic
group map from the results. The tool is a weapon, not a toy.

- **Every agent has a personality, a voice, a methodology.** The Risk Analyst
thinks in scenarios — best case, base case, worst case — and always asks
"what would kill this?" before asking "what would help this?" The Strategy
Analyst thinks in frameworks — Porter's, VRIO, Blue Ocean — and selects the
right framework for the question, not the same framework every time. These
aren't generic system prompts. They are proprietary expert personas.

- **Every agent knows what it doesn't know.** The Regulatory Analyst knows it
is not a lawyer. It flags regulatory risks, maps the compliance landscape, and
identifies jurisdictions to investigate — but it explicitly recommends legal
counsel for definitive opinions. The Technology Analyst knows it is not a
software architect. It evaluates tech stacks against business requirements,
not against engineering preferences. This self-awareness is what makes them
credible, not hallucinatory.

- **Every agent produces structured output, not free text.** Findings are
Pydantic models with typed fields — `KeyFinding`, `Risk`, `AnalysisSection`,
`FinancialMetric`. This means the Synthesis Lead can programmatically
reconcile findings, the Quality Gate can programmatically score them, and the
Presentation Designer can programmatically lay them out. Free text is the
enemy of quality at scale.

**If an agent could be replaced by a single LLM call with a good prompt, it
shouldn't exist in HYPERION.** Every agent exists because it does something
that a generic prompt cannot: it applies a specific framework, uses specific
tools in a specific order, produces a specific structured output, and
operates at a specific intelligence tier. That is the difference between a
wrapper and a consulting model.

---

## 1. Language & Runtime

| Decision | Choice | Rationale |
|---|---|---|
| Language | Python 3.12+ | Rich LLM/PDF/scraping ecosystem. User mandate. |
| Async | asyncio | Fully async — old system was sync (bug source). |
| Concurrency | asyncio + thread pool | I/O-bound LLM calls async; CPU-bound PDF render in threads. |
| Package manager | uv | 10-100x faster than pip, lockfile, reproducible. |

---

## 2. Provider & Model Matrix (Verified July 2026)

### 2.1 Google AI Studio (Free Tier)

| Model | RPM | TPM | RPD | Context | Role |
|---|---|---|---|---|---|
| Gemma 4 31B | 30 | 16K | 14,400 | 16K | **MICRO workhorse** — 14.4K RPD is enormous |
| Gemma 4 26B | 30 | 16K | 14,400 | 16K | Backup workhorse |
| Gemini 3.1 Flash Lite | 15 | 250K | 500 | 250K | **DEEP context** — long doc synthesis |
| Gemini 3.5 Flash | 5 | 250K | 20 | 250K | Reserve |
| Gemini 3 Flash | 5 | 250K | 20 | 250K | Reserve |

### 2.2 NVIDIA NIM (Free Tier — ~40 RPM)

| Model | Context | Role |
|---|---|---|
| Nemotron 3 Super 120B A12B | 262K | **STRONG** — planning, writing, design |
| Nemotron 3 Ultra 550B A55B | 1M | **DEEP reserve** — ultra-long context |
| Nemotron 3 Nano 30B A3B | 262K | **STANDARD** — research, sub-agents |
| Llama 3.3 Nemotron Super 49B v1.5 | 131K | Backup standard |

### 2.3 Cerebras (Free Tier)

| Model | RPM | TPM | TPD | Context | Speed | Role |
|---|---|---|---|---|---|---|
| GPT OSS 120B | 5 | 30K | 1M | 131K | ~3000 tok/s | **FAST** — real-time extraction |
| Gemma 4 31B | 5 | 30K | 1M | 131K | ~1850 tok/s | Backup fast |
| ~~Z.ai GLM 4.7~~ | — | — | — | — | — | **DEPRECATED Aug 17 2026 — DO NOT USE** |

### 2.4 Groq (Free Tier)

| Model | RPM | TPM | RPD | TPD | Context | Role |
|---|---|---|---|---|---|---|
| GPT OSS 120B | 30 | 8K | 1,000 | 200K | 128K | **STANDARD** — research, analysis |
| Llama 3.3 70B Versatile | 30 | 12K | 1,000 | 100K | 128K | STANDARD alt — higher TPM |
| Llama 3.1 8B Instant | 30 | 6K | 14,400 | 500K | 128K | **MICRO backup** — 14.4K RPD |
| Llama 4 Scout 17B | 30 | 30K | 1,000 | 500K | 128K | High TPM tasks |
| Qwen 3 32B | 60 | 6K | 1,000 | 500K | 128K | High RPM tasks |
| GPT OSS 20B | 30 | 8K | 1,000 | 200K | 128K | Lightweight reasoning |

### 2.5 Model Tier Assignment

```
Tier 0 — MICRO (high RPD workhorse, simple tasks)
  Primary:  Gemma 4 31B (Google)     → 14.4K RPD, 30 RPM, 16K TPM
  Backup:   Llama 3.1 8B (Groq)      → 14.4K RPD, 30 RPM, 6K TPM
  Use:      query generation, fact-check snippets, simple extraction,
            sub-agent quick tasks, keyword expansion, tag generation

Tier 1 — FAST (speed-critical, real-time)
  Primary:  GPT OSS 120B (Cerebras)  → 5 RPM, 30K TPM, 1M TPD, ~3000 tok/s
  Backup:   GPT OSS 120B (Groq)      → 30 RPM, 8K TPM, 1K RPD
  Use:      real-time extraction validation, quick reasoning, structured
            output when speed matters, inline fact verification

Tier 2 — STANDARD (research & analysis)
  Primary:  GPT OSS 120B (Groq)      → 30 RPM, 8K TPM, 1K RPD
  Backup:   Nemotron 3 Nano 30B (NVIDIA) → 40 RPM, 262K context
  Tertiary: Llama 3.3 70B (Groq)     → 30 RPM, 12K TPM, 1K RPD
  Use:      research rounds, specialist analysis, structured Pydantic output,
            sub-agent research tasks

Tier 3 — STRONG (planning & writing)
  Primary:  Nemotron 3 Super 120B (NVIDIA) → 40 RPM, 262K context
  Backup:   GPT OSS 120B (Cerebras)  → 5 RPM, 131K context
  Tertiary: GPT OSS 120B (Groq)      → 30 RPM, 128K context
  Use:      engagement planning, section writing, synthesis, design decisions,
            quality gate evaluation

Tier 4 — DEEP (ultra-long context)
  Primary:  Gemini 3.1 Flash Lite (Google) → 15 RPM, 250K TPM, 500 RPD
  Backup:   Nemotron 3 Ultra 550B (NVIDIA) → 40 RPM, 1M context
  Use:      multi-source reconciliation, full-document synthesis,
            cross-referencing 50+ sources, long-context reasoning
```

---

## 3. TPM-Aware Wait Gate System

### 3.1 Principle

The old system fired requests, got 429s, then failover. v0.1 **predicts** when
a provider will be rate-limited and routes around it **before** the 429 happens.
This is the difference between reactive and proactive rate limit management.

A 429 is a failure. Every 429 wastes time (the request was prepared, sent,
rejected), wastes tokens (the response is an error, not content), and forces
failover to a potentially suboptimal provider. The wait gate eliminates 429s
by tracking capacity in real-time and routing intelligently.

### 3.2 Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    LLMRouter (singleton)                      │
│                                                               │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐          │
│  │  Provider    │  │  Provider    │  │  Provider    │          │
│  │  Google      │  │  NVIDIA      │  │  Cerebras    │          │
│  │  ┌─────────┐│  │  ┌─────────┐│  │  ┌─────────┐│          │
│  │  │Model    ││  │  │Model    ││  │  │Model    ││          │
│  │  │Tracker  ││  │  │Tracker  ││  │  │Tracker  ││          │
│  │  │┌───────┐││  │  │┌───────┐││  │  │┌───────┐││          │
│  │  ││RPM    │││  │  ││RPM    │││  │  ││RPM    │││          │
│  │  ││Window │││  │  ││Window │││  │  ││Window │││          │
│  │  │├───────┤││  │  │├───────┤││  │  │├───────┤││          │
│  │  ││TPM    │││  │  ││TPM    │││  │  ││TPM    │││          │
│  │  ││Window │││  │  ││Window │││  │  ││Window │││          │
│  │  │├───────┤││  │  │├───────┤││  │  │├───────┤││          │
│  │  ││RPD    │││  │  ││RPD    │││  │  ││RPD    │││          │
│  │  ││Counter│││  │  ││Counter│││  │  ││Counter│││          │
│  │  │├───────┤││  │  │├───────┤││  │  │├───────┤││          │
│  │  ││Health │││  │  ││Health │││  │  ││Health │││          │
│  │  ││Check  │││  │  ││Check  │││  │  ││Check  │││          │
│  │  │└───────┘││  │  │└───────┘││  │  │└───────┘││          │
│  │  └─────────┘│  │  └─────────┘│  │  └─────────┘│          │
│  └─────────────┘  └─────────────┘  └─────────────┘          │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              WaitGate Coordinator                     │    │
│  │  • Evaluates all provider/model trackers             │    │
│  │  • Calculates optimal wait time or provider switch    │    │
│  │  • Predicts TPM consumption based on prompt size      │    │
│  │  • Maintains global request queue with priorities     │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                               │
│  ┌─────────────────────────────────────────────────────┐    │
│  │              Failover Handler                         │    │
│  │  • 429 → mark provider cooldown, switch to backup     │    │
│  │  • 500/503 → health check, circuit breaker            │    │
│  │  • Timeout → retry with exponential backoff           │    │
│  └─────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────┘
```

### 3.3 Sliding Window Tracker

Each provider+model pair has a rolling 60-second window tracking:
- **RPM**: count of requests in the window (deque of timestamps)
- **TPM**: sum of estimated tokens in the window (deque of (timestamp, token_count))
- **RPD**: daily counter (resets at UTC midnight)
- **Health**: last response timestamp, last error, consecutive failures

The window is a `deque` that prunes entries older than 60 seconds on every
access. This gives O(1) amortized cost per check.

Before dispatching a request, the router checks all eligible provider+model
pairs for the requested tier:

1. **Filter**: exclude pairs where RPD is exhausted, provider is in cooldown,
   or health check failed
2. **Score**: for each remaining pair, calculate a score:
   - `available_capacity = (rpm_limit - current_rpm) + (tpm_limit - current_tpm) / tpm_limit`
   - `latency_estimate = historical average response time for this provider`
   - `context_fit = how well the model's context window fits the request`
   - `score = available_capacity * 0.5 + (1 / latency_estimate) * 0.3 + context_fit * 0.2`
3. **Dispatch**: pick the highest-scoring pair

If no pair can serve NOW:
- **< 5s wait**: sleep and retry (blocking the coroutine)
- **5-30s wait**: queue the request, yield to async scheduler, other agents
  continue working
- **> 30s wait**: try adjacent tier (up or down based on task priority)

### 3.4 Token Estimation

Pre-request token estimation for TPM planning. This is critical —
underestimating tokens leads to 429s, overestimating leads to underutilization.

```python
def estimate_tokens(system_prompt: str, user_prompt: str, tier: ModelTier) -> int:
    """
    Conservative token estimation.
    - 1 token ≈ 3 chars for English (conservative vs. 4 char average)
    - 1 token ≈ 2 chars for code/structured data
    - Add output budget based on tier
    """
    input_chars = len(system_prompt) + len(user_prompt)
    input_tokens = input_chars // 3  # conservative
    
    output_budget = {
        Tier.MICRO: 500,
        Tier.FAST: 2000,
        Tier.STANDARD: 4000,
        Tier.STRONG: 8000,
        Tier.DEEP: 16000,
    }
    
    return input_tokens + output_budget[tier]
```

After each response, the router records the **actual** token usage (from
response headers or API metadata) and calibrates future estimates. Over time,
the estimator learns the real token consumption patterns of each agent.

### 3.5 Daily Budget Planner

Tracks RPD across all models per provider. The daily budget planner ensures we
never exhaust a provider's daily quota before the engagement is complete.

**Provider daily budgets (total RPD across all models):**
- Google: ~29,460 RPD (Gemma 14.4K + Gemma 14.4K + Gemini 500 + reserves)
- Groq: ~18,400 RPD (6 models × ~1K-14.4K each)
- NVIDIA: ~1,000 credits/month → ~33/day (scarce — reserve for STRONG/DEEP)
- Cerebras: 1M TPD per model → effectively unlimited by tokens, but 5 RPM

**Allocation strategy:**
- **High urgency** (quality gate, synthesis): use high-RPD providers first
  (Google Gemma, Groq Llama 3.1 8B) to preserve NVIDIA credits
- **Normal** (research, analysis): balanced selection across all providers
- **Low** (background tasks): use NVIDIA sparingly, prefer Google/Groq
- **20% reserve**: preserved on every provider for critical end-of-engagement
  tasks (Quality Gate scoring, Synthesis Lead reconciliation, final render)

### 3.6 Failover Handler

Even with predictive routing, failures happen. The failover handler ensures
graceful degradation:

- **429 (Rate Limited)**: mark provider+model cooldown (60s), failover to next
  provider in tier. Record actual token usage for future estimation. If all
  providers in tier are 429'd, wait gate engages globally.
- **500/503 (Server Error)**: health check (GET /models), circuit breaker
  (3 consecutive failures → 5-min cooldown). Skip provider until healthy.
- **Timeout**: exponential backoff (1s, 2s, 4s, max 3 retries). On final
  timeout, failover to next provider.
- **Network Error**: immediate failover, no retry (likely DNS/connectivity).

### 3.7 Provider Rotation Strategy

The router doesn't just failover on errors — it **proactively rotates** across
providers to maximize throughput and avoid hitting any single provider's limits.

**Round-robin with weighting:** within a tier, the router cycles through
available providers but weights the selection by remaining capacity. A provider
with 90% remaining capacity gets more requests than one with 20% remaining.

**Cross-tier fallback:** if all providers in a tier are exhausted, the router
can fall to the tier below (faster, less capable) or above (slower, more
capable) based on the task's priority:
- Critical tasks (synthesis, quality gate): fall UP to a higher tier
- Non-critical tasks (keyword expansion, tag generation): fall DOWN

**Provider affinity:** certain agents develop affinity for certain providers
based on historical performance. If the Financial Analyst's DCF models
consistently produce better output on NVIDIA Nemotron than on Groq GPT OSS,
the router learns this preference and biases toward NVIDIA for that agent's
requests (when capacity allows).

---

## 4. Agent System — Dynamic Consulting Team

### 4.1 Core Principle

Every agent has:
- **Name and role** — what they are
- **Model tier** — what intelligence level they operate at
- **Tools** — what they can actually use (not decorative)
- **Skills** — proprietary analytical methods they apply
- **System prompt** — their expertise, voice, methodology
- **Spawn condition** — when the Engagement Director activates them

**No agent is generic. No tool is idle. No skill is decorative.**

### 4.2 Agent Roster — Overview

HYPERION has 20 agents across 4 tiers of the organization:

| Tier | Agents | Count |
|---|---|---|
| Core (always active) | Engagement Director, Synthesis Lead | 2 |
| Specialists (dynamic spawn) | Market, Competitive, Financial, Risk, Technology, Operations, Regulatory, Sustainability, Consumer, M&A, Innovation, Strategy | 12 |
| Support | Research Librarian, Fact Checker, Data Visualizer, Quality Gate | 4 |
| Delivery | Presentation Designer, Render Engine | 2 |

Each agent below is documented with its full profile: role, model tier, tools,
skills, methodology, sub-agent patterns, output format, and what makes it the
best version of itself.

---

### 4.3 Core Agents

#### Agent 1: Engagement Director

**Role:** The partner. Receives the question, decomposes it into a workflow
DAG, selects the specialist team, orchestrates execution, and adapts mid-flight
when agents discover unexpected findings.

**Model Tier:** STRONG (Nemotron 3 Super 120B primary)

**Tools:** All tools (read-only) — can see everything, modify nothing directly.

**Skills:**
- **Question classification**: Categorizes the question into one or more types
  (GO_NO_GO, COMPARISON, FORECAST, DIAGNOSTIC, OPTIMIZATION, GENERAL) which
determines which specialists to spawn.
- **Workflow design**: Builds a custom DAG of tasks with dependencies. A market
  entry question creates Market → Competitive → Financial → Risk (parallel) →
  Synthesis. An M&A question creates M&A → Financial + Regulatory (parallel) →
  Synthesis. No two DAGs are identical.
- **Agent selection**: Chooses which of the 12 specialists to activate based on
  the question. Not all 12 are spawned every time — that would waste resources.
  A pricing question needs Financial + Market + Consumer, not Regulatory + M&A.
- **Dependency mapping**: Determines which agents can run in parallel and which
  depend on others' findings. Market sizing must complete before Financial can
  model unit economics. Competitive intelligence can run in parallel with Market.
- **Adaptive replanning**: When an agent publishes an ESCALATION message
  ("I found an unexpected regulatory barrier that changes the market sizing"),
  the Engagement Director can spawn a new agent (Regulatory) mid-engagement and
  reroute the DAG.
- **Budget allocation**: Assigns model tiers to each task based on complexity
  and available daily budget. Simple tasks get MICRO, complex analysis gets
  STRONG, synthesis gets DEEP.

**Methodology:**
1. Receive question + conversation context
2. Classify question type(s)
3. Query Second Brain for prior research on this topic
4. Decompose into 4-8 research domains
5. Select specialists for each domain
6. Build dependency graph (parallel vs sequential)
7. Assign model tiers per task
8. Estimate total LLM calls + token consumption
9. Dispatch to AgentBus
10. Monitor execution, adapt if needed

**Sub-agent pattern:** The Engagement Director does not spawn sub-agents
directly. It spawns specialists, who in turn spawn sub-agents.

**Output:** `WorkflowDAG` (Pydantic model) containing all task nodes,
dependencies, tier assignments, and estimated duration.

**What makes it the best version of itself:**
It doesn't just "plan." It thinks like a senior consulting partner — it
identifies the key question behind the question, knows which frameworks apply,
anticipates which findings will change the analysis direction, and adjusts the
team composition in real-time. A generic planner says "research these 5 topics."
The Engagement Director says "Market sizing is the critical path — start it
first and give it STRONG tier. Competitive intelligence can run in parallel at
STANDARD. Financial depends on Market's TAM number, so queue it. If Regulatory
finds a compliance barrier, reroute to add a Legal Risk sub-task."

---

#### Agent 2: Synthesis Lead

**Role:** The senior consultant who reconciles all specialist findings into a
single, coherent recommendation. This is the most intellectually demanding
role — it requires holding 4-6 specialists' findings in mind simultaneously,
identifying contradictions, resolving them, and producing one answer.

**Model Tier:** DEEP (Gemini 3.1 Flash Lite primary — 250K context window for
holding all findings simultaneously)

**Tools:** Second Brain (retrieve prior engagements for pattern matching),
all specialist findings (read-only).

**Skills:**
- **Cross-source reconciliation**: When Market Analyst says "TAM is $2B" and
  Financial Analyst says "the market is too small to justify entry," the
  Synthesis Lead identifies the contradiction, determines which finding is
  better supported by evidence, and resolves it in the final recommendation.
- **Contradiction resolution**: Explicitly maps contradictions between agents
  on a contradiction matrix. Each contradiction is classified as: data conflict
  (different numbers for the same metric), interpretation conflict (same data,
  different conclusions), or scope conflict (agents analyzed different scopes).
- **Confidence calibration**: Aggregates individual agent confidence scores
  into a system-level confidence with domain-weighted breakdown. If Market is
  HIGH confidence but Regulatory is LOW confidence, the system confidence
  reflects the weakest critical link.
- **Narrative synthesis**: Produces a coherent narrative that weaves all
  findings into a single story with a clear recommendation, supporting
  evidence, and acknowledged limitations. Not a summary — a synthesis.

**Methodology:**
1. Collect all specialist findings from AgentBus
2. Build a finding matrix (agent × finding × evidence × confidence)
3. Identify contradictions and classify them
4. Resolve contradictions (evidence-weighted, not averaging)
5. Identify the critical path to the recommendation
6. Draft the recommendation with supporting evidence chain
7. Calibrate system confidence level
8. Produce FinalReport model

**Sub-agent pattern:** Can spawn 1 sub-agent for contradiction resolution —
if two agents' findings are deeply contradictory, a sub-agent does a focused
deep dive on the specific point of conflict.

**Output:** `FinalReport` (Pydantic model) — the single most important data
structure in the system.

**What makes it the best version of itself:**
It doesn't "summarize" or "combine" findings. It synthesizes. A summarizer
lists what each agent found. A synthesizer says "Market says $2B TAM, Financial
says too small, but Financial's model assumes 5% penetration while Market's
data supports 12% — at 12% penetration the market is viable. The recommendation
is ENTER, with the critical assumption being penetration rate. If penetration
falls below 8%, the recommendation flips to NO-GO." That is synthesis.

---

### 4.4 Specialist Agents

#### Agent 3: Market Analyst

**Role:** Sizes markets, maps market structure, identifies growth drivers, and
segments the market. The go-to agent for any "how big is this opportunity"
question.

**Model Tier:** STANDARD (GPT OSS 120B on Groq primary, Nemotron 3 Nano 30B
backup)

**Tools:**
- **SearxNG**: Search for market reports, industry analyses, government data
- **Jina**: Extract content from market research sites, trade publications
- **Obscura**: Scrape JS-rendered market data dashboards (Statista-style sites,
  interactive market maps) using stealth mode
- **Alpha Vantage**: Pull market data for publicly traded companies in the space
- **FRED**: Pull macroeconomic indicators (GDP growth, sector spending, inflation)
  that drive market size

**Skills (proprietary analytical frameworks):**
- **Top-down market sizing**: Start with a large known market, apply filters
  (geography, segment, price point) to narrow to TAM. Example: Global SaaS
  market → India SaaS → India Tier-2 SaaS.
- **Bottom-up market sizing**: Start with unit economics and customer count.
  Example: 50M Tier-2 businesses × 2% SaaS adoption × $500 ARPU = $500M.
- **CAGR triangulation**: Cross-validate market size estimates by calculating
  implied CAGR from different sources and checking for consistency.
- **Market maturity assessment**: Classify market as emerging/growing/mature/
  declining using indicators like penetration rate, growth rate, number of
  competitors, and price compression.
- **Growth driver decomposition**: Break market growth into components —
  population growth, penetration increase, ARPU expansion, new use cases.
- **Segment analysis**: Segment the market by demographics, behavior, and
  psychographics. Identify which segment is the most attractive entry point.

**Methodology:**
1. Search for existing market reports (SearxNG + Jina)
2. If no direct data, scrape interactive dashboards (Obscura)
3. Pull macroeconomic context (FRED)
4. Pull public company revenue data for market sizing (Alpha Vantage)
5. Apply top-down sizing
6. Apply bottom-up sizing
7. Cross-validate via CAGR triangulation
8. Segment the market
9. Identify growth drivers
10. Produce structured MarketAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Find TAM data for [specific market]" (MICRO, SearxNG + Jina)
- Sub-agent B: "Find [geography] spending data" (MICRO, SearxNG + Obscura)
- Sub-agent C: "Find adoption/penetration rates" (FAST, Obscura + Jina)

**Output:** `MarketAnalysis` containing TAM, SAM, SOM, CAGR, segments, growth
drivers, market maturity classification, and confidence score.

**What makes it the best version of itself:**
It never reports a single market size number. It always reports a range with
a top-down estimate, a bottom-up estimate, and a triangulated best estimate.
It always cites the source for each number. It always flags when market data
is sparse or unreliable. It always segments before sizing — a market size
without segmentation is useless for strategy.

---

#### Agent 4: Competitive Intelligence

**Role:** Profiles competitors, maps competitive positioning, assesses moats,
and tracks market share. The agent that answers "who are we up against and
how do they win?"

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for competitor news, press releases, analyst reports
- **Jina**: Extract competitor website content, product pages, pricing pages
- **Obscura**: Scrape JS-rendered competitor sites (pricing calculators,
  feature comparison pages, customer testimonials) with stealth mode to avoid
  bot detection. Uses `browser_evaluate` to extract structured data from
  interactive elements.
- **Wayback**: Pull historical competitor website snapshots to track pricing
  changes, product evolution, and strategic pivots over time.

**Skills:**
- **Competitor matrix**: Build a structured comparison of competitors across
  dimensions: product features, pricing, target customer, geographic coverage,
  funding stage, headcount, key partnerships.
- **Strategic group mapping**: Cluster competitors into strategic groups based
  on similarities in strategy, target market, and competitive approach. This
  reveals which competitors are direct rivals vs. adjacent players.
- **Market share analysis**: Estimate market share from available data
  (revenue, customer count, search volume, app downloads). Always with
  confidence intervals and source citations.
- **Moat assessment**: Evaluate each competitor's competitive moat using the
  Hamilton Helmer framework: network effects, switching costs, scale advantages,
  brand, regulatory, IP/proprietary tech, distribution. Score each moat as
  strong/moderate/weak/nascent.
- **Positioning map**: Plot competitors on a 2D map (price vs. quality,
  feature breadth vs. focus, etc.) to identify white space and competitive
  density.

**Methodology:**
1. Identify all competitors in the space (SearxNG)
2. Scrape each competitor's website for product/pricing/team info (Obscura)
3. Pull historical snapshots for trend analysis (Wayback)
4. Build competitor matrix
5. Assess moats for top 5 competitors
6. Create strategic group map
7. Create positioning map
8. Identify white space opportunities

**Sub-agent pattern (max 3):**
- Sub-agent A: "Scrape [competitor1] pricing page" (MICRO, Obscura)
- Sub-agent B: "Scrape [competitor2] pricing page" (MICRO, Obscura)
- Sub-agent C: "Find [competitor3] funding/headcount" (FAST, SearxNG + Jina)

**Output:** `CompetitiveLandscape` containing competitor matrix, moat
assessments, strategic group map, positioning map, and white space
identification.

**What makes it the best version of itself:**
It uses Obscura's stealth mode because competitor sites actively block bots.
It cross-references current pricing with Wayback historical pricing to show
  pricing trends, not just current prices. It doesn't just list competitors —
  it maps their moats and identifies which are defensible vs. eroding. It always
  identifies white space — where no competitor is currently playing.

---

#### Agent 5: Financial Analyst

**Role:** Builds financial models, evaluates unit economics, runs valuations,
and assesses financial viability. The agent that answers "do the numbers
work?"

**Model Tier:** STANDARD+ (STANDARD for research, STRONG for modeling)

**Tools:**
- **Alpha Vantage**: Pull financial statements, ratios, and market data for
  public companies. Used for comparable company analysis and market data.
- **FRED**: Pull macroeconomic data (interest rates, inflation, GDP) for DCF
  discount rates and scenario modeling.
- **SearxNG**: Search for industry financial benchmarks, margin data, cost
  structures.
- **Jina**: Extract financial data from research reports, 10-K filings,
  earnings call transcripts.

**Skills:**
- **DCF (Discounted Cash Flow)**: Build a DCF model with explicit forecast
  period, terminal value (Gordon growth or exit multiple), WACC calculation,
  and sensitivity table on discount rate and terminal growth rate.
- **LBO (Leveraged Buyout)**: Model LBO scenarios with debt structure, interest
  coverage, IRR calculation, and exit assumptions. Used for M&A support.
- **Comparable company analysis**: Identify 5-10 comparable public companies,
  pull their trading multiples (EV/Revenue, EV/EBITDA, P/E), and apply to the
  target company.
- **Unit economics**: Calculate LTV, CAC, LTV/CAC ratio, payback period, gross
  margin, contribution margin, and burn rate. Identify which unit economics
  assumptions are the most sensitive.
- **Sensitivity analysis**: Build two-variable sensitivity tables (price ×
  volume, discount rate × terminal growth, penetration × ARPU) to show how
  the recommendation changes under different assumptions.
- **Scenario modeling**: Build best case, base case, worst case scenarios with
  assigned probabilities and expected values.
- **Break-even analysis**: Calculate break-even point in units and revenue,
  contribution margin per unit, and margin of safety.

**Methodology:**
1. Pull comparable company financials (Alpha Vantage)
2. Pull macroeconomic inputs for discount rates (FRED)
3. Search for industry benchmarks (SearxNG + Jina)
4. Build DCF model with sensitivity tables
5. Build comparable company analysis
6. Calculate unit economics
7. Run scenario analysis (best/base/worst)
8. Calculate break-even
9. Produce FinancialAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Pull financial statements for [company1, company2, company3]"
  (MICRO, Alpha Vantage)
- Sub-agent B: "Find industry margin benchmarks for [industry]" (MICRO,
  SearxNG + Jina)
- Sub-agent C: "Find cost structure data for [business model]" (FAST,
  SearxNG + Jina)

**Output:** `FinancialAnalysis` containing DCF model, comparable analysis,
unit economics, sensitivity tables, scenario analysis, break-even, and
confidence score.

**What makes it the best version of itself:**
It never reports a single valuation number. It always reports a range with
sensitivity tables showing how the valuation changes under different
assumptions. It always identifies the key value drivers — the 2-3 assumptions
that account for 80% of the valuation variance. It always cross-validates DCF
with comparable company analysis. If the DCF says $100M but comparables say
$50M, it flags the discrepancy and explains it.

---

#### Agent 6: Risk Analyst

**Role:** Identifies risks, builds risk matrices, plans scenarios, and designs
mitigations. Every engagement includes a risk analysis — risk is universal.

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for risk reports, regulatory risks, industry-specific
  risk factors, historical failures in the space.
- **Jina**: Extract risk disclosures from 10-K filings, risk reports, and
  industry analyses.
- **Obscura**: Scrape government risk portals, sanctions lists, and regulatory
  databases for jurisdiction-specific risk data.

**Skills:**
- **Risk matrix**: Build a probability × impact matrix with risks plotted on a
  5×5 grid. Each risk is scored on probability (1-5) and impact (1-5), with
  color-coded zones (green/yellow/red).
- **Monte Carlo simulation**: Run 10,000-trial Monte Carlo simulations on key
  variables (revenue, cost, timeline) to produce probability distributions of
  outcomes. Shows P10/P50/P90 values.
- **Black swan analysis**: Identify low-probability, high-impact events that
  could invalidate the entire strategy. These are risks that don't appear in
  the risk matrix because they're too unlikely — but if they happen, they're
  catastrophic.
- **Scenario planning**: Build best case, base case, worst case scenarios with
  trigger conditions, leading indicators, and response plans for each.
- **Mitigation design**: For each risk, design a specific mitigation action,
  assign an owner (which agent monitors it), and calculate residual risk
  (risk after mitigation).
- **Residual risk scoring**: After mitigations, re-score each risk to show
  residual risk. Some risks are fully mitigatable, others are inherent.

**Methodology:**
1. Search for known risks in the industry/space (SearxNG + Jina)
2. Scrape regulatory/sanctions databases (Obscura)
3. Identify risks across 6 categories: market, financial, operational,
   regulatory, technology, and strategic
4. Score each risk on probability × impact
5. Build risk matrix
6. Design mitigations for top 10 risks
7. Calculate residual risk scores
8. Identify black swan scenarios
9. Build scenario plan with triggers
10. Produce RiskAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Find historical failures in [industry]" (MICRO, SearxNG + Jina)
- Sub-agent B: "Find regulatory risks in [jurisdiction]" (MICRO, SearxNG)
- Sub-agent C: "Find technology/cyber risks in [space]" (FAST, SearxNG + Jina)

**Output:** `RiskAnalysis` containing risk matrix, top 10 risks with
mitigations, residual risk scores, black swan scenarios, and scenario plan.

**What makes it the best version of itself:**
It thinks in scenarios, not in lists. A generic risk analyst lists 20 risks.
The HYPERION Risk Analyst identifies the 5 risks that actually matter, explains
why the other 15 are noise, and designs mitigations that are specific enough to
act on. It always asks "what would kill this?" before asking "what could help
this?" — because surviving the downside is more important than capturing the
upside.

---

#### Agent 7: Technology Analyst

**Role:** Evaluates technology stacks, assesses build-vs-buy decisions, maps
digital transformation paths, and evaluates vendor platforms.

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for tech stack reviews, vendor comparisons, engineering
  blog posts, architecture case studies.
- **Jina**: Extract documentation, API specs, pricing pages, and technical
  whitepapers.
- **Obscura**: Scrape JS-rendered vendor sites (AWS, GCP, Azure pricing
calculators), GitHub repositories for code quality assessment, and Stack
  Overflow for developer sentiment.

**Skills:**
- **Architecture review**: Evaluate a technology architecture against business
  requirements (scalability, reliability, maintainability, cost). Identify
  architectural anti-patterns and single points of failure.
- **Tech debt assessment**: Quantify technical debt using the SIG/TÜViT model
  or similar. Categorize debt as intentional vs. unintentional, and estimate
  remediation cost.
- **Vendor evaluation**: Score vendors across dimensions: feature fit, pricing,
  scalability, support quality, ecosystem, lock-in risk, and roadmap alignment.
  Produce a vendor comparison matrix.
- **Build-vs-buy framework**: Structured analysis comparing build vs. buy on:
  time to market, total cost of ownership (5-year), strategic differentiation,
  maintenance burden, team capability, and opportunity cost.
- **TCO analysis**: 5-year total cost of ownership including licensing,
  infrastructure, maintenance, integration, and switching costs.
- **Platform assessment**: Evaluate platform play vs. point solution. Assess
  API quality, integration ecosystem, and extensibility.

**Methodology:**
1. Search for vendor/technology options (SearxNG + Jina)
2. Scrape vendor pricing and feature pages (Obscura)
3. Search for developer sentiment and reviews (SearxNG)
4. Build vendor comparison matrix
5. Run build-vs-buy analysis
6. Calculate 5-year TCO
7. Assess architecture if applicable
8. Produce TechnologyAssessment model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Scrape [vendor1] pricing and features" (MICRO, Obscura)
- Sub-agent B: "Scrape [vendor2] pricing and features" (MICRO, Obscura)
- Sub-agent C: "Find developer reviews for [technology]" (FAST, SearxNG + Jina)

**Output:** `TechnologyAssessment` containing vendor matrix, build-vs-buy
recommendation, TCO analysis, architecture review, and confidence score.

**What makes it the best version of itself:**
It evaluates tech against business requirements, not engineering preferences.
It doesn't recommend Kubernetes because it's "modern" — it recommends the
simplest technology that meets the scalability/reliability requirements. It
always calculates 5-year TCO, not just licensing cost. It always assesses
lock-in risk — a vendor that's 20% cheaper but impossible to leave is more
expensive than one that's 20% pricier but easy to switch.

---

#### Agent 8: Operations Analyst

**Role:** Optimizes processes, maps supply chains, identifies bottlenecks, and
designs operational KPIs.

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for operational benchmarks, supply chain data, process
  optimization case studies.
- **Jina**: Extract operational reports, industry efficiency data, and supply
  chain analyses.
- **Obscura**: Scrape JS-rendered supply chain databases, logistics platforms,
  and operational dashboards.

**Skills:**
- **Process mapping**: Map end-to-end processes using SIPOC (Supplier-Input-
  Process-Output-Customer) and value stream mapping. Identify non-value-adding
  steps.
- **Lean/Six Sigma**: Apply Lean principles (eliminate waste) and Six Sigma
  (reduce variation) to identify improvement opportunities. Calculate process
  sigma level and DPMO (defects per million opportunities).
- **Bottleneck analysis**: Identify process bottlenecks using theory of
  constraints. Calculate throughput at each stage and identify the binding
  constraint.
- **Supply chain mapping**: Map the supply chain from raw materials to end
  customer. Identify single-source suppliers, geographic concentration risks,
  and lead time vulnerabilities.
- **Capacity planning**: Calculate current capacity utilization, identify
  capacity constraints, and model capacity expansion scenarios.
- **Operational KPI design**: Design a KPI dashboard specific to the business —
  not generic metrics, but the 5-7 metrics that actually drive performance for
  this specific operational model.
- **Efficiency benchmarking**: Benchmark operational metrics against industry
  leaders. Identify the gap and estimate the improvement potential.

**Methodology:**
1. Search for operational data and benchmarks (SearxNG + Jina)
2. Map the end-to-end process
3. Identify bottlenecks
4. Calculate capacity utilization
5. Benchmark against industry leaders
6. Identify improvement opportunities (Lean/Six Sigma)
7. Design KPI dashboard
8. Produce OperationsAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Find operational benchmarks for [industry]" (MICRO, SearxNG)
- Sub-agent B: "Find supply chain data for [sector]" (MICRO, SearxNG + Jina)
- Sub-agent C: "Find efficiency metrics for [process type]" (FAST, SearxNG)

**Output:** `OperationsAnalysis` containing process map, bottleneck analysis,
capacity assessment, benchmark comparison, KPI dashboard, and improvement
recommendations.

**What makes it the best version of itself:**
It doesn't just map processes — it identifies the binding constraint and
estimates the improvement potential in dollars. A generic ops analyst says
"the process has bottlenecks." The HYPERION Operations Analyst says "Step 3
is the bottleneck at 40 units/hour vs. 60 units/hour for the rest of the
process. Adding one worker to Step 3 costs $50K/year but increases throughput
by 50%, generating $200K/year in additional contribution margin. ROI = 300%."

---

#### Agent 9: Regulatory Analyst

**Role:** Maps regulatory landscapes, identifies compliance requirements,
assesses regulatory risks, and scans the regulatory horizon.

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for regulations, compliance requirements, regulatory
  news, and legal analyses.
- **Jina**: Extract regulatory documents, compliance guides, and legal
  commentaries.
- **Obscura**: Scrape government regulatory portals, agency websites, and
  regulatory databases that require JS rendering.
- **Wayback**: Pull historical regulatory snapshots to track how regulations
  have evolved over time.

**Skills:**
- **Regulatory mapping**: Map all regulations applicable to the business
  across jurisdictions. Categorize by type (data protection, financial,
  industry-specific, labor, environmental).
- **Jurisdiction comparison**: Compare regulatory requirements across
  jurisdictions (US, EU, India, etc.) to identify the most favorable regulatory
  environment and the most restrictive.
- **Compliance checklist**: Build a structured compliance checklist with
  specific requirements, documentation needed, and estimated compliance cost.
- **Regulatory horizon scanning**: Identify pending regulations, proposed
  rules, and regulatory trends that could impact the business in 1-3 years.
- **Precedent analysis**: Find regulatory enforcement actions against similar
  companies to understand regulatory priorities and penalties.

**Methodology:**
1. Search for applicable regulations (SearxNG + Jina)
2. Scrape government portals for current rules (Obscura)
3. Pull historical regulatory data (Wayback)
4. Map regulations by jurisdiction
5. Build compliance checklist
6. Scan regulatory horizon
7. Analyze enforcement precedents
8. Produce RegulatoryAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Find regulations for [jurisdiction1]" (MICRO, SearxNG + Jina)
- Sub-agent B: "Find regulations for [jurisdiction2]" (MICRO, SearxNG + Jina)
- Sub-agent C: "Find pending/proposed regulations" (FAST, SearxNG + Obscura)

**Output:** `RegulatoryAnalysis` containing regulatory map, jurisdiction
comparison, compliance checklist, horizon scan, and enforcement precedents.

**What makes it the best version of itself:**
It knows it is not a lawyer. It maps the landscape, identifies risks, and
recommends legal counsel for definitive opinions. It doesn't give legal
advice — it gives regulatory intelligence. It tracks regulatory evolution
using Wayback Machine, not just current state. It always identifies the
  jurisdiction with the lightest regulatory touch as a potential strategic
  advantage.

---

#### Agent 10: Sustainability Analyst

**Role:** Assesses ESG performance, calculates carbon footprint, evaluates
sustainability strategy, and maps ESG reporting requirements.

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for ESG ratings, sustainability reports, climate data,
  and environmental regulations.
- **Jina**: Extract sustainability reports, ESG ratings, and environmental
  impact assessments.
- **Obscura**: Scrape JS-rendered ESG rating platforms (MSCI, Sustainalytics),
  carbon calculators, and sustainability databases.
- **FRED**: Pull environmental economic data (carbon prices, green bond rates,
  clean energy investment trends).

**Skills:**
- **ESG scoring**: Score the company/strategy on ESG frameworks: MSCI ESG
  Ratings, SASB standards, TCFD recommendations, GRI standards. Identify
  which framework is most relevant for the stakeholder audience.
- **Carbon footprint**: Calculate Scope 1 (direct), Scope 2 (purchased
  electricity), and Scope 3 (value chain) emissions. Identify the largest
  emission sources and reduction opportunities.
- **Sustainability reporting**: Map reporting requirements (CSRD, SEC climate,
  TCFD, CDP). Identify which reports are mandatory vs. voluntary.
- **Green financing**: Evaluate green bonds, sustainability-linked loans, and
  carbon credit opportunities. Calculate potential financing cost savings.
- **Circular economy**: Assess opportunities for circular economy models
  (reduce, reuse, recycle, refurbish) in the business model.

**Methodology:**
1. Search for ESG data and ratings (SearxNG + Jina)
2. Scrape ESG rating platforms (Obscura)
3. Pull environmental economic data (FRED)
4. Score on relevant ESG framework
5. Calculate carbon footprint (Scope 1/2/3)
6. Map reporting requirements
7. Identify green financing opportunities
8. Produce SustainabilityAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Find ESG ratings for [company/sector]" (MICRO, SearxNG + Jina)
- Sub-agent B: "Find carbon emission data for [industry]" (MICRO, SearxNG)
- Sub-agent C: "Find sustainability regulations for [jurisdiction]" (FAST,
  SearxNG + Obscura)

**Output:** `SustainabilityAnalysis` containing ESG scores, carbon footprint,
reporting requirements, green financing opportunities, and circular economy
assessment.

**What makes it the best version of itself:**
It doesn't just calculate a carbon number — it identifies the specific
emission sources that account for 80% of the footprint and calculates the
abatement cost for each. It maps ESG to financial impact (green financing
savings, regulatory penalty avoidance, investor access) not just to
compliance. It always identifies which ESG framework matters for the specific
stakeholder (investors want TCFD, regulators want CSRD, customers want GRI).

---

#### Agent 11: Consumer Insights

**Role:** Analyzes customer behavior, develops personas, maps customer
journeys, and estimates demand.

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for consumer research, market research reports, survey
data, and behavioral studies.
- **Jina**: Extract consumer research content, review aggregations, and
  behavioral analysis reports.
- **Obscura**: Scrape JS-rendered review sites (G2, Capterra, Trustpilot),
  social media platforms, and consumer forums to extract real customer
  sentiment and pain points.

**Skills:**
- **Persona development**: Build data-driven customer personas with
demographics, behaviors, motivations, frustrations, and preferred channels.
  Not generic personas — personas grounded in scraped review data and survey
  responses.
- **Journey mapping**: Map the end-to-end customer journey from awareness to
  advocacy. Identify friction points, drop-off points, and moments of truth.
- **NPS analysis**: Analyze Net Promoter Score data and qualitative feedback
  to identify the drivers of promotion and detraction.
- **Segmentation**: Segment customers using three approaches:
  - Demographic (age, income, geography, company size)
  - Behavioral (usage patterns, purchase frequency, feature adoption)
  - Psychographic (values, motivations, attitudes)
  Identify which segmentation approach is most predictive of purchase behavior.
- **Demand estimation**: Estimate demand using willingness-to-pay analysis,
  conjoint analysis proxies, and price elasticity estimation from market data.
- **Willingness-to-pay analysis**: Estimate the price point that maximizes
  revenue using Van Westendorp price sensitivity meter methodology.

**Methodology:**
1. Search for consumer research (SearxNG + Jina)
2. Scrape review sites and forums (Obscura)
3. Build personas from data
4. Map customer journey
5. Segment the market
6. Estimate demand and willingness-to-pay
7. Produce ConsumerInsights model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Scrape reviews from [review site]" (MICRO, Obscura)
- Sub-agent B: "Find consumer survey data for [segment]" (MICRO, SearxNG)
- Sub-agent C: "Find willingness-to-pay studies for [product category]"
  (FAST, SearxNG + Jina)

**Output:** `ConsumerInsights` containing personas, journey map, segmentation,
demand estimate, and willingness-to-pay analysis.

**What makes it the best version of itself:**
It builds personas from real scraped data, not from imagination. It doesn't
say "Tech-Savvy Tom, age 25-35." It says "Based on 847 G2 reviews and 234
Reddit threads, the primary persona is a mid-market IT manager (35-45, $80K-
$120K budget) whose top frustration is 'integration complexity' (mentioned in
34% of negative reviews) and whose primary buying trigger is 'peer
recommendation from a similar company' (mentioned in 41% of positive reviews)."

---

#### Agent 12: M&A Analyst

**Role:** Identifies acquisition targets, conducts due diligence, models
synergies, and plans integration.

**Model Tier:** STRONG (Nemotron 3 Super 120B — M&A analysis is complex and
requires strong reasoning)

**Tools:**
- **SearxNG**: Search for M&A transactions, deal databases, and acquisition
  news.
- **Jina**: Extract deal announcements, merger documents, and M&A analysis
  reports.
- **Obscura**: Scrape JS-rendered M&A databases (PitchBook-style sites),
  company databases (Crunchbase-style), and deal trackers.
- **Alpha Vantage**: Pull financial data for target companies and acquirers.

**Skills:**
- **Target identification**: Screen for acquisition targets using criteria:
  strategic fit, size, geography, technology, talent, customer base. Build a
  long list (20-50) and short list (5-10) with rationale for each.
- **Synergy analysis**: Quantify revenue synergies (cross-sell, upsell, new
  markets) and cost synergies (headcount reduction, facility consolidation,
  procurement savings). Always with a reality discount — synergies rarely
  materialize at 100% of the estimate.
- **Integration planning**: Build a 100-day integration plan with workstreams,
  milestones, owners, and risk flags. Identify the top 3 integration risks.
- **Valuation gap analysis**: Compare the acquirer's maximum acceptable price
  to the target's minimum acceptable price. Identify the zone of possible
  agreement.
- **Accretion/dilution analysis**: Model the impact of the acquisition on the
  acquirer's EPS over 1-3 years. Identify whether the deal is accretive or
  dilutive and under what conditions.
- **Cultural fit assessment**: Evaluate cultural compatibility using public
  data (Glassdoor reviews, LinkedIn company pages, employee sentiment). Cultural
  mismatch is the #1 reason M&A deals fail to deliver synergies.

**Methodology:**
1. Define acquisition criteria with Engagement Director
2. Search for potential targets (SearxNG + Jina + Obscura)
3. Build long list → short list
4. Pull financial data for targets (Alpha Vantage)
5. Run synergy analysis
6. Run accretion/dilution
7. Assess cultural fit
8. Build integration plan
9. Produce M&AAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Screen targets by [criteria]" (MICRO, SearxNG + Obscura)
- Sub-agent B: "Pull financials for [target1, target2, target3]" (MICRO,
  Alpha Vantage)
- Sub-agent C: "Find cultural reviews for [target companies]" (FAST, Obscura)

**Output:** `M&AAnalysis` containing target list, synergy analysis,
accretion/dilution, cultural fit, and integration plan.

**What makes it the best version of itself:**
It always applies a reality discount to synergies — 50-70% of estimated
synergies typically materialize. It always assesses cultural fit because that's
  the #1 failure cause. It always builds an integration plan, not just a deal
  rationale — because the deal is the easy part, integration is the hard part.

---

#### Agent 13: Innovation Analyst

**Role:** Scans for emerging technologies, maps disruption patterns, assesses
innovation portfolios, and evaluates first-mover vs. fast-follower strategies.

**Model Tier:** STANDARD

**Tools:**
- **SearxNG**: Search for emerging tech news, research papers, patent filings,
  and innovation case studies.
- **Jina**: Extract academic papers, tech blogs, and innovation reports.
- **Obscura**: Scrape JS-rendered patent databases, arXiv, research portals,
  and innovation dashboards.
- **Wayback**: Pull historical snapshots of technology trends to track hype vs.
  reality over time.

**Skills:**
- **Technology readiness levels (TRL)**: Assess technologies on the NASA TRL
  scale (1-9) from basic research to deployed. Identify which emerging techs
  are ready for production use vs. still experimental.
- **Gartner hype cycle positioning**: Plot technologies on the hype cycle
  (innovation trigger → peak of inflated expectations → trough of disillusionment
  → slope of enlightenment → plateau of productivity). Identify where each tech
  currently sits.
- **Horizon scanning**: Systematically scan for signals of change across 3
  horizons: H1 (current, 0-12 months), H2 (emerging, 1-3 years), H3 (future,
  3-10 years).
- **Disruption pattern analysis**: Identify which disruption pattern applies:
  low-end disruption (cheaper, simpler), new-market disruption (serving
  non-consumers), or architectural disruption (reconfiguring the value chain).
- **First-mover vs. fast-follower**: Analyze whether first-mover advantage
  applies in this market or whether fast-follower is the better strategy.
  Consider: network effects, switching costs, learning curve, patent
  protection, and brand.
- **Innovation portfolio**: Map the company's innovation initiatives on the
  3-horizon portfolio. Identify if the portfolio is balanced or over-invested
  in one horizon.

**Methodology:**
1. Search for emerging technologies in the space (SearxNG + Jina)
2. Scrape patent databases and research portals (Obscura)
3. Pull historical trend data (Wayback)
4. Assess TRL for each technology
5. Plot on hype cycle
6. Run horizon scan
7. Analyze disruption patterns
8. Assess first-mover vs. fast-follower
9. Produce InnovationAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Find emerging tech in [space]" (MICRO, SearxNG + Jina)
- Sub-agent B: "Find patent filings for [technology]" (MICRO, Obscura)
- Sub-agent C: "Find historical adoption curves for [similar tech]" (FAST,
  SearxNG + Wayback)

**Output:** `InnovationAnalysis` containing TRL assessments, hype cycle
positioning, horizon scan, disruption pattern analysis, and innovation
portfolio assessment.

**What makes it the best version of itself:**
It separates hype from reality using the Gartner hype cycle and TRL scale. It
doesn't say "AI is transformative" — it says "LLM-based customer support is at
  the slope of enlightenment (TRL 8) and ready for production, while
  autonomous agents are at the peak of inflated expectations (TRL 4) and 2-3
  years from production readiness." It always assesses first-mover advantage
  — because in some markets, being first is a disadvantage.

---

#### Agent 14: Strategy Analyst

**Role:** Applies strategic frameworks to the question, evaluates competitive
positioning, and designs strategic options.

**Model Tier:** STRONG (Nemotron 3 Super 120B — strategy requires the strongest
reasoning)

**Tools:**
- **SearxNG**: Search for strategic analyses, industry reports, and
  competitive strategy research.
- **Jina**: Extract strategy reports, case studies, and academic strategic
  management papers.
- **Obscura**: Scrape JS-rendered strategy databases, industry reports, and
  competitive intelligence platforms.

**Skills:**
- **Porter's Five Forces**: Analyze industry attractiveness through the five
  forces: threat of new entrants, bargaining power of suppliers, bargaining
  power of buyers, threat of substitutes, and competitive rivalry. Score each
  force as strong/moderate/weak.
- **BCG growth-share matrix**: Plot the company's products/business units on
  the growth-share matrix (stars, cash cows, question marks, dogs). Identify
  resource allocation recommendations.
- **SWOT analysis**: Structured SWOT with the critical distinction that it's a
  snapshot, not a strategy. Convert SWOT into a TOWS matrix to generate
  strategic options (SO, WO, ST, WT strategies).
- **Blue Ocean strategy**: Identify whether the company can create uncontested
  market space using the eliminate-reduce-raise-create framework. Build a
  strategy canvas comparing the company to competitors.
- **VRIO framework**: Evaluate resources/capabilities on Value, Rarity,
  Imitability, and Organization. Identify which resources provide sustainable
  competitive advantage.
- **Core competence analysis**: Identify the 2-3 core competencies that give
  the company its competitive advantage. Assess whether these competencies are
  defensible and transferable.
- **Strategic option grid**: Build a grid of 3-5 strategic options, each scored
  on: feasibility, impact, risk, time to value, and resource requirements.
- **Game theory**: Analyze competitive interactions using game theory
  (prisoner's dilemma, sequential games, signaling). Identify dominant
  strategies and Nash equilibria.

**Methodology:**
1. Search for strategic context (SearxNG + Jina)
2. Run Porter's Five Forces
3. Run VRIO on company resources
4. Build SWOT → TOWS matrix
5. Generate 3-5 strategic options
6. Score options on strategic option grid
7. Run game theory analysis on competitive dynamics
8. Produce StrategyAnalysis model

**Sub-agent pattern (max 3):**
- Sub-agent A: "Find Porter's Five Forces data for [industry]" (MICRO,
  SearxNG + Jina)
- Sub-agent B: "Find competitor strategic moves in [space]" (MICRO, SearxNG)
- Sub-agent C: "Find VRIO-relevant resources for [company]" (FAST, SearxNG +
  Obscura)

**Output:** `StrategyAnalysis` containing Five Forces analysis, VRIO
assessment, SWOT/TOWS, strategic options grid, and game theory analysis.

**What makes it the best version of itself:**
It doesn't apply every framework to every question. It selects the right
framework for the specific question — Porter's for industry attractiveness,
VRIO for resource-based strategy, Blue Ocean for market creation, game theory
for competitive dynamics. A generic strategist applies SWOT to everything.
The HYPERION Strategy Analyst applies the framework that actually illuminates
the specific question, and explicitly says why it chose that framework over
the alternatives.

---

### 4.5 Support Agents

#### Agent 15: Research Librarian

**Role:** Manages the Obsidian vault (Second Brain), retrieves prior research,
organizes sources, and links findings across engagements.

**Model Tier:** MICRO (Gemma 4 31B — this is keyword matching and note
management, not complex reasoning)

**Tools:**
- **Second Brain (Obsidian vault)**: Read/write markdown notes, keyword
  retrieval, tag-based search, cross-note linking.

**Skills:**
- **Keyword retrieval**: Find relevant prior research using keyword matching
  with relevance scoring (threshold: 0.15). No embeddings — lightweight and
  fast.
- **Source deduplication**: Detect when multiple agents cite the same source
  and deduplicate the source list.
- **Citation management**: Format citations consistently (footnote style) and
  ensure every claim in the final report has a traceable source.
- **Cross-engagement knowledge linking**: When a new engagement touches a
  topic researched in a prior engagement, link the prior research for the
  Synthesis Lead to reference.
- **Source credibility scoring**: Score each source on credibility (peer-
  reviewed > government > industry report > news > blog > social media) and
  flag low-credibility sources.

**Methodology:**
1. Query vault for prior research on the engagement topic
2. Retrieve relevant notes and return to requesting agent
3. Collect all sources from all agents at end of engagement
4. Deduplicate sources
5. Score source credibility
6. Save engagement findings to vault for future reference
7. Format citation list for final report

**Output:** `SourceCollection` containing deduplicated sources with credibility
scores, and prior research links.

**What makes it the best version of itself:**
It runs on MICRO tier (Gemma 4 31B, 14.4K RPD) because it doesn't need strong
reasoning — it needs fast, high-throughput keyword matching. It makes the
system smarter over time by accumulating knowledge in the vault. Each
engagement makes the next one faster and better because the Librarian can
retrieve prior findings.

---

#### Agent 16: Fact Checker

**Role:** Verifies claims made by specialists, cross-references sources, and
flags contradictions.

**Model Tier:** FAST (GPT OSS 120B on Cerebras — speed is critical, fact-
checking runs in parallel with late-stage specialists)

**Tools:**
- **SearxNG**: Search for verification of specific claims.
- **Jina**: Extract source content to verify claims against original sources.
- **Obscura**: Scrape JS-rendered pages to verify claims that require JS
  rendering.

**Skills:**
- **Claim verification**: Extract specific factual claims from specialist
  findings and verify each against independent sources. A claim is verified if
  2+ independent sources agree.
- **Source credibility scoring**: Score each source on credibility and weight
  verification accordingly. A claim verified by a peer-reviewed paper is more
  credible than one verified by a blog post.
- **Contradiction detection**: Identify when two specialists make contradictory
  claims and flag them for the Synthesis Lead.
- **Evidence chain validation**: For each claim, trace the evidence chain:
  claim → source → original data. If the chain breaks (source doesn't contain
  the data, or data doesn't support the claim), flag it.
- **Statistical sanity checks**: Check for statistical red flags: numbers that
  are too round (suspicious), growth rates that are implausibly high, market
  sizes that don't reconcile across agents.

**Methodology:**
1. Collect all specialist findings from AgentBus
2. Extract factual claims (numbers, dates, names, events)
3. For each claim, search for verification (SearxNG + Jina)
4. Score each claim: VERIFIED, PLAUSIBLE, UNVERIFIED, CONTRADICTED
5. Flag contradictions to Synthesis Lead
6. Flag unverified claims to originating specialist
7. Produce FactCheckReport model

**Output:** `FactCheckReport` containing claim-by-claim verification status,
contradictions, and evidence chain validation.

**What makes it the best version of itself:**
It runs on FAST tier (Cerebras, ~3000 tok/s) because fact-checking is time-
critical — it runs in parallel with late-stage specialists and must finish
before the Synthesis Lead starts. It doesn't just check if a source exists —
it checks if the source actually contains the data the specialist claims it
does. It catches hallucinated citations, which is the #1 quality risk in LLM-
generated reports.

---

#### Agent 17: Data Visualizer

**Role:** Generates charts, graphs, and visual elements for the report.

**Model Tier:** STANDARD

**Tools:**
- **Plotly**: Generate charts (bar, line, scatter, heatmap, radar, waterfall,
  treemap, sankey) with brand colors and 300 DPI export.
- **Unsplash**: Search for contextual images to complement charts.
- **Pillow**: Post-process chart images (sharpen, color-correct for print).

**Skills:**
- **Chart type selection**: Select the right chart type for the data:
  comparison → bar, trend → line, distribution → histogram, correlation →
  scatter, composition → stacked bar/treemap, flow → sankey.
- **Data viz best practices**: Apply Tufte principles: minimize chartjunk,
  maximize data-ink ratio, use color purposefully (not decoratively).
- **Brand-compliant styling**: All charts use the HYPERION chart color
  sequence (terracotta, sage, deep brown, warm gray, beige, alert red).
- **Axis calibration**: Choose axis ranges that show the data honestly — no
  truncated y-axes that exaggerate differences, no log scales without
  labeling.
- **Annotation**: Add contextual annotations to charts (benchmark lines,
  callout boxes, trend lines) that help the reader understand the key insight.

**Methodology:**
1. Receive chart specifications from Presentation Designer
2. For each chart, select chart type based on data shape
3. Generate chart with Plotly using brand colors
4. Export at scale=3 for 300 DPI
5. Post-process with Pillow (sharpen for print)
6. Return chart image paths to Presentation Designer

**Output:** Chart images (PNG, 300 DPI) with metadata (title, caption, source).

**What makes it the best version of itself:**
It follows Tufte principles — no chartjunk, no 3D effects, no gradient fills.
Every chart has a purpose: it reveals a pattern that the text alone cannot
convey. It never uses a pie chart when a bar chart would be clearer. It always
labels axes, always cites the data source, and always chooses the chart type
that best reveals the insight — not the chart type that looks most impressive.

---

#### Agent 18: Quality Gate

**Role:** Final review of the report against a 10-dimension rubric. If the
score is below threshold, the report goes back for iteration.

**Model Tier:** STRONG (Nemotron 3 Super 120B — quality evaluation requires
strong reasoning)

**Tools:** All outputs (read-only) — can read everything the engagement
produced.

**Skills:**
- **Rubric scoring (10 dimensions)**:
  1. **Completeness**: Are all sections present? Are all key questions answered?
  2. **Evidence sufficiency**: Is every claim backed by ≥1 source? Are key
     claims backed by ≥2 sources?
  3. **Analytical depth**: Does the analysis go beyond surface-level findings?
     Are frameworks applied correctly?
  4. **Logical consistency**: Do the recommendations follow from the findings?
     Are there logical gaps?
  5. **Contradiction resolution**: Have all contradictions between agents been
     resolved by the Synthesis Lead?
  6. **Tone and voice**: Is the tone consulting-grade (confident, specific,
     evidence-based)? No hedging, no waffling, no generic statements.
  7. **Structural quality**: Does the report follow the premium structure? Are
     sections properly ordered? Is the executive summary upfront?
  8. **Risk coverage**: Has the Risk Analyst identified the top risks? Are
     mitigations specific and actionable?
  9. **Data accuracy**: Has the Fact Checker verified all claims? Are there
     unverified claims?
  10. **Visual quality**: Are charts brand-compliant? Are images properly
      placed? Is the PDF 300 DPI?

- **Gap analysis**: Identify specific gaps in the analysis — questions that
  should have been answered but weren't, data that should have been collected
  but wasn't.
- **Tone enforcement**: Flag any language that is too hedgy ("might possibly
  perhaps"), too generic ("it depends"), or too absolute ("this will
definitely").
- **Structural validation**: Check that the report follows the premium
  structure (cover → TOC → exec summary → sections → risk → methodology →
  appendix → back cover).
- **Evidence sufficiency check**: Verify that every claim has at least one
  source and that key claims have at least two.

**Methodology:**
1. Receive FinalReport from Synthesis Lead
2. Receive FactCheckReport from Fact Checker
3. Score each of the 10 dimensions (1-5 scale)
4. Calculate weighted total score
5. If score ≥ 4.0/5.0: approve for delivery
6. If score < 4.0: identify specific gaps and send back for iteration
7. Max 3 iterations before escalation

**Output:** `QualityScore` containing per-dimension scores, total score,
identified gaps, and approve/reject decision.

**What makes it the best version of itself:**
It doesn't just say "good" or "bad." It produces a specific, actionable score
report that tells the Synthesis Lead exactly what to fix. "Dimension 3
(analytical depth) scored 2/5: the Market Analysis section presents data but
doesn't interpret it. Fix: add 'so what?' implications to each finding.
Dimension 6 (tone) scored 3/5: 4 instances of hedgy language in the executive
summary. Fix: replace 'might possibly' with 'is likely to'."

---

### 4.6 Delivery Agents

#### Agent 19: Presentation Designer

**Role:** Designs the report layout, selects images, and composes the visual
structure of the PDF.

**Model Tier:** STRONG

**Tools:**
- **Unsplash**: Search and select images for cover, section headers, and
  contextual illustrations.
- **Plotly**: Receive chart specifications from Data Visualizer.
- **Jinja2**: Render the HTML template with report content and layout plan.
- **WeasyPrint**: Generate the final PDF from HTML/CSS.

**Skills:**
- **Layout design**: Design page layouts that follow the premium structure.
  Each page has a clear visual hierarchy: header → key insight → body →
  chart/image → implication.
- **Typography**: Apply the HYPERION typography system (Instrument Serif for
  headers, JetBrains Mono for body) consistently.
- **Image placement**: Place images according to the 5 image placement rules
  (see Section 6.3). No orphaned images, no blank pages.
- **Print design**: Ensure the PDF is print-ready: 300 DPI, embedded fonts,
  proper margins, no color bleeding.
- **Page flow**: Control page breaks to ensure no blank pages, no orphaned
  images, and no awkward section breaks. Use `page-break-inside: avoid` in CSS.
- **Visual hierarchy**: Use size, weight, and color to guide the reader's eye
  through the report. The most important content (recommendation, key findings)
  gets the most visual weight.
- **White space management**: Use white space deliberately — not as empty
  space, but as a design element that improves readability and focus.

**Methodology:**
1. Receive FinalReport from Synthesis Lead
2. Receive QualityScore from Quality Gate
3. Design layout plan (which content goes on which page)
4. Select Unsplash images for cover and section headers
5. Receive chart images from Data Visualizer
6. Render HTML template with Jinja2
7. Generate PDF with WeasyPrint
8. Post-process images with Pillow (via Render Engine)

**Output:** `LayoutPlan` containing page-by-page layout, image selections,
and chart placements. The final PDF is produced by the Render Engine.

**What makes it the best version of itself:**
It treats layout as design, not as formatting. It doesn't just dump content
into a template — it makes deliberate decisions about what goes on each page,
how to balance text and visuals, and how to guide the reader through the
narrative. It always ensures images are adjacent to their context text. It
never produces a blank page or an orphaned image.

---

#### Agent 20: Render Engine

**Role:** Final PDF assembly — converts HTML/CSS + images into a 300 DPI PDF.

**Model Tier:** — (CPU-only, no LLM calls)

**Tools:**
- **WeasyPrint**: HTML/CSS → PDF conversion at 300 DPI.
- **Pillow**: Image processing (resize, crop, color-correct, sharpen).

**Skills:**
- **PDF generation**: Convert Jinja2-rendered HTML to PDF with proper page
  sizing (A4), DPI (300), and font embedding.
- **Image processing**: Process all images through the Pillow pipeline:
  resize (never upscale), crop (center-weighted), color-correct (match brand
  warmth), sharpen (unsharp mask), export as PNG (lossless).
- **Color management**: Ensure brand colors render correctly in PDF (CMYK
  fallback for print, exact hex for digital).
- **Font embedding**: Embed Instrument Serif and JetBrains Mono in the PDF so
  it renders identically on any system.
- **Page break control**: Use CSS `page-break-inside: avoid` and `page-break-
  before: always` to control page flow and prevent blank pages.

**Methodology:**
1. Receive HTML from Presentation Designer
2. Receive image paths from Data Visualizer and Unsplash tool
3. Process all images through Pillow pipeline
4. Convert HTML → PDF with WeasyPrint at 300 DPI
5. Verify PDF: no blank pages, no orphaned images, all fonts embedded
6. Save to reports/ directory
7. Return PDF path

**Output:** Final PDF file path.

**What makes it the best version of itself:**
It is the last line of defense for quality. It verifies the PDF after
rendering — checks for blank pages, checks that all images are properly
placed, checks that fonts are embedded. If any check fails, it reports the
issue back to the Presentation Designer for correction. It never ships a
broken PDF.

### 4.7 Sub-Agent System (Junior Agents)

Each specialist can spawn **junior sub-agents** for deeper dives. This is the
context-window management strategy — not truncation, not compression, but
**delegation with structured handoff**.

This is what makes HYPERION fundamentally different from a single-LLM system.
A single LLM hits a context window limit and starts forgetting earlier findings.
HYPERION delegates — the specialist keeps its context clean, sends a focused
sub-question to a junior agent, and gets back structured findings. The
specialist's context window is used for synthesis, not for raw research.

**Rules:**
- Max 3 sub-agents per specialist per engagement
- Sub-agents use Tier MICRO or FAST (don't burn STRONG/DEEP quota)
- Sub-agent findings are structured (Pydantic model), not free text
- Parent specialist receives structured findings and synthesizes them
- Sub-agents have 5-minute timeout — if a sub-agent doesn't return in 5 min,
  the parent proceeds with available findings and flags the gap
- Sub-agents have access to a subset of parent's tools (specified at spawn time)
- Sub-agents cannot spawn their own sub-agents (no recursive spawning —
  prevents uncontrolled agent proliferation)
- Sub-agent findings include: data, sources, confidence score, and gaps
  (what the sub-agent couldn't find)

**Sub-agent lifecycle:**
```
Specialist identifies sub-question
  → Creates SubAgent spec (question, tier, tools, findings_model)
  → SubAgent dispatched to LLMRouter with appropriate tier
  → SubAgent executes: searches → extracts → analyzes → produces findings
  → SubAgent returns structured findings to parent
  → Parent synthesizes sub-agent findings into its own analysis
  → Parent reports to Engagement Director
```

**Example:**
1. Market Analyst gets task: "Size the Tier-2 city SaaS market in India"
2. Spawns 3 sub-agents:
   - A: "Find TAM data for Indian SaaS market" (MICRO, SearxNG + Jina)
   - B: "Find Tier-2 city IT spending data" (MICRO, SearxNG + Obscura)
   - C: "Find SaaS adoption rates in Tier-2 cities" (FAST, Obscura + Jina)
3. Each returns structured KeyFinding objects with data, sources, confidence
4. Market Analyst synthesizes 3 reports into its own MarketAnalysis model
5. Market Analyst reports to Engagement Director via AgentBus

### 4.8 Agent Communication — AgentBus

In-memory async pub/sub system. No external broker (no Redis, no RabbitMQ).
Built on `asyncio.Queue` — lightweight, fast, zero dependencies.

**Channels:**
- `status`: all agents publish state changes (IDLE/WORKING/WAITING/DONE/BLOCKED)
- `findings`: agents publish completed findings for other agents to consume
- `requests`: agents request data/context from other agents
- `escalation`: agents flag issues to Engagement Director
- `handoff`: agents pass tasks to other agents
- `tui`: status updates for the TUI to display

**Message types:**
- **FINDING**: `{agent, finding_type, content, sources, confidence, timestamp}`
  Published when an agent completes a finding. Other agents can consume it
  if relevant to their task.
- **REQUEST**: `{from_agent, to_agent, request_type, context, timestamp}`
  Direct request from one agent to another. Example: Financial Analyst
  requests Market Analyst's TAM number before building the DCF model.
- **STATUS**: `{agent, state, detail, timestamp}`
  State change broadcast. The TUI subscribes to this for live display.
- **HANDOFF**: `{from_agent, to_agent, task, context_bundle, timestamp}`
  One agent passes a task to another. Example: Engagement Director hands
  off a sub-task to a specialist.
- **ESCALATION**: `{agent, issue, suggested_action, timestamp}`
  Agent flags an issue to the Engagement Director. Example: Regulatory
  Analyst finds an unexpected compliance barrier and escalates so the
  Director can reroute the DAG.

**Subscription pattern:**
- Engagement Director subscribes to ALL channels (omniscient)
- Specialists subscribe to `findings` and `requests` (need-aware)
- Support agents subscribe to `findings` (Fact Checker needs all findings)
- TUI subscribes to `status` and `findings` (display only)
- Delivery agents subscribe to `findings` (need final report content)

### 4.9 Dynamic Workflow Engine

The Engagement Director analyzes the question, selects the right agents,
and builds a custom DAG of tasks. No two engagements look the same.

**DAG construction process:**
1. Classify the question (GO_NO_GO, COMPARISON, FORECAST, etc.)
2. Identify required analytical domains
3. Select specialists for each domain
4. Map dependencies (which agents need other agents' output)
5. Assign model tiers based on task complexity + daily budget
6. Estimate total LLM calls + token consumption + duration
7. Build topological sort for execution order
8. Dispatch to AgentBus

**Execution model:**
- Tasks with no dependencies run in parallel (asyncio.gather)
- Tasks with dependencies wait for their dependency nodes to complete
- The Engagement Director monitors all tasks and can:
  - Spawn new agents mid-engagement (adaptive replanning)
  - Reroute tasks if an agent fails or escalates
  - Reallocate model tiers if budget is running low
  - Cancel tasks if findings make them unnecessary

**Example DAGs for different question types:**

```
MARKET ENTRY ("Should we enter the Tier-2 Indian SaaS market?")

  [Engagement Director: decompose]
           |
      +----+----+--------+--------+
      v    v    v        v        v
   Market  Comp  Financial  Risk   Consumer
   Analyst  Intel  Analyst  Analyst Insights
      |      |      |        |       |
      v      v      v        v       v
   [each spawns 1-3 sub-agents]
      |      |      |        |       |
      +----+-+------+--------+-------+
           v
   [Fact Checker: verify all findings]
           v
   [Synthesis Lead: reconcile → recommendation]
           v
   [Quality Gate: rubric score]
           v
   [Presentation Designer: layout + images]
           v
   [Render Engine: PDF at 300 DPI]

M&A ("Should we acquire Company X?")

  [Engagement Director: decompose]
           |
      +----+----+--------+--------+
      v    v    v        v        v
    M&A   Financial  Regulatory  Strategy  Culture
   Analyst  Analyst   Analyst   Analyst   (sub-agent)
      |      |      |        |       |
      +----+-+------+--------+-------+
           v
   [Fact Checker] → [Synthesis] → [Quality Gate] → [Design] → [Render]

PRICING ("What should we price our product at?")

  [Engagement Director: decompose]
           |
      +----+----+--------+
      v    v    v        v
   Market  Financial  Consumer  Competitive
   Analyst  Analyst  Insights   Intel
      |      |      |        |
      +----+-+------+--------+
           v
   [Fact Checker] → [Synthesis] → [Quality Gate] → [Design] → [Render]
```

Notice: different questions spawn different teams. The M&A engagement
spawns a Culture sub-agent (via M&A Analyst). The Pricing engagement
doesn't need Risk or Regulatory. The Engagement Director makes these
decisions based on the question type and context.

---

## 5. Tool Registry

Every tool is assigned to agents who **actually use it**. No decorative tools.
No tool is assigned to an agent that doesn't need it. No agent lacks a tool it
does need. This is deliberate — tool assignment is a design decision, not a
kitchen-sink afterthought.

### 5.1 Core Tools

| Tool | Type | Used By | Description |
|---|---|---|---|
| SearxNG | Search | All specialists | Self-hosted meta-search, free, unlimited. Docker-based. Aggregates 70+ search engines. No API key, no rate limit, no tracking. |
| Jina | Search + Extract | All specialists | `s.jina.ai` search, `r.jina.ai` read. 500 RPM, 10M tokens/mo. Used for content extraction from URLs returned by SearxNG. |
| Obscura | Browser | Competitive Intel, Consumer Insights, Technology, Regulatory, M&A, Market, Sustainability, Operations, Fact Checker | Rust headless browser. 70MB binary, 30MB RAM, instant cold start. CDP-compatible. Stealth mode. MCP server with 12 tools. **Primary browser for JS-heavy pages.** |
| Crawl4AI | Deep Extract | Research Librarian, Fact Checker | Heavy page extraction. Fallback when Obscura unavailable. Transformers patch required. Used for PDF extraction and complex document parsing. |
| Wayback | Archive | Regulatory, Innovation, Competitive Intel | Historical page snapshots via Wayback Machine. Used for tracking changes over time — pricing evolution, regulatory changes, tech hype cycles. |
| Alpha Vantage | Financial Data | Financial Analyst, M&A Analyst | Market data, fundamentals, forex, crypto. 25 API calls/day (free key), 500/day (premium). Used for comparable company analysis and market data. |
| FRED | Economic Data | Market Analyst, Financial Analyst, Sustainability | Federal Reserve economic data. GDP, inflation, interest rates, sector spending. Free, unlimited. Used for macroeconomic context in market sizing and DCF discount rates. |
| Unsplash | Images | Presentation Designer, Data Visualizer | Free tier: 50 req/hr (demo) → 1000 req/hr (production). Cover images, section headers, contextual photos. Image file requests don't count against rate limit. |
| Second Brain | Knowledge | Research Librarian, all agents (read) | Obsidian vault — prior research, notes, keyword retrieval. Relevance threshold: 0.15. Makes the system smarter over time. |
| Plotly | Charts | Data Visualizer | 300 DPI charts, brand colors, kaleido export. Supports bar, line, scatter, heatmap, radar, waterfall, treemap, sankey. |
| WeasyPrint | PDF | Render Engine | HTML/CSS → PDF at 300 DPI. GTK3 required on Windows. Supports `page-break-inside: avoid`, embedded fonts, CSS paged media. |
| Jinja2 | Templates | Presentation Designer | HTML template engine for report structure. Templates in `output/templates/`. |
| Pillow | Image Processing | Render Engine, Data Visualizer | Resize, crop, color-correct, sharpen for print. Never upscales. Exports PNG (lossless). |

### 5.2 Tool Selection Logic

Agents don't use tools randomly. Each agent has a **tool selection strategy**
that determines which tool to use for which task:

```
Search task:
  1. SearxNG (free, unlimited, fast) — always try first
  2. Jina search (if SearxNG returns poor results)
  3. Obscura (if the data is behind JS rendering)

Extract task:
  1. Jina Reader (fast, clean markdown extraction)
  2. Obscura (if JS rendering required — pricing calculators,
     interactive dashboards, review sites)
  3. Crawl4AI (if Obscura fails — heavy extraction, PDFs)
  4. Wayback (if the page is down or has changed)

Historical task:
  1. Wayback Machine (always — it's the only source for historical snapshots)

Financial data task:
  1. Alpha Vantage (always — it's the only financial data source)

Macro data task:
  1. FRED (always — it's the only macroeconomic data source)

Image task:
  1. Unsplash API search (query → results → select best match)
  2. Cached images (if similar query was made before)
  3. Curated library (if API limit is hit)
```

This is not a generic "use whatever tool" approach. Each agent knows exactly
which tool to use for which task, in what order, and when to fall back.

### 5.3 Obscura Integration — Deep Dive

Obscura is the **primary browser tool** for JS-heavy pages, replacing Crawl4AI
as the first option in the extraction chain. It is not just a browser — it is
an **agentic browser** designed for AI workflows.

**Why Obscura over headless Chrome:**
- 70MB binary vs. 400MB+ for Chrome — lightweight
- 30MB RAM vs. 200MB+ for Chrome — efficient
- Instant cold start vs. 3-5s for Chrome — fast
- No Chrome dependencies, no Node.js — self-contained
- Built for automation at scale, not desktop browsing
- Stealth mode: anti-fingerprinting + tracker blocking — competitors can't
detect our scraping

**MCP Server Tools (12 tools):**
- `browser_navigate(url, waitUntil)`: Navigate to a URL. Wait conditions:
  `load`, `domcontentloaded`, `networkidle0`.
- `browser_snapshot()`: Get accessibility tree snapshot of the current page.
  Used for understanding page structure.
- `browser_click(selector)`: Click an element by selector. Used for interacting
  with pricing calculators, dropdowns, tabs.
- `browser_fill(selector, value)`: Fill an input field. Used for search forms,
  filter inputs.
- `browser_type(selector, text)`: Type text into an element. Used for
  form submission.
- `browser_press_key(key, selector)`: Press a keyboard key. Used for
  Enter, Escape, Tab.
- `browser_select_option(selector, value)`: Select an `<option>` from a
  dropdown. Used for filter selection.
- `browser_evaluate(expression)`: Execute JavaScript in the page context.
  **This is the most powerful tool** — used for extracting structured data
  from interactive elements (pricing tables, feature comparisons, review
  counts) that aren't in the HTML source.
- `browser_wait_for(selector, timeout)`: Wait for an element to appear.
  Used for pages with dynamic content loading.
- `browser_network_requests()`: Get all network requests made by the page.
  Used for intercepting API calls and extracting data from XHR responses.
- `browser_console_messages()`: Get console messages. Used for debugging
  scraping issues.
- `browser_close()`: Close the browser instance.

**CLI commands:**
- `obscura serve --port 9222 --stealth`: Start CDP WebSocket server for
  persistent browser sessions. Agents connect via CDP for multi-step
  interactions (navigate → click → extract).
- `obscura fetch <URL> --dump markdown`: Fetch and render a single page,
  output as markdown. Used for one-shot extraction.
- `obscura scrape <URL...> --concurrency 10`: Scrape multiple URLs in
  parallel. Used for batch extraction (e.g., scraping 10 competitor pricing
  pages simultaneously).

**Agent usage patterns:**
- **Competitive Intel**: `obscura scrape` for batch competitor pricing
  extraction. `browser_evaluate` for extracting structured pricing data from
  interactive calculators. Stealth mode to avoid bot detection.
- **Consumer Insights**: `browser_navigate` + `browser_snapshot` for review
  sites. `browser_evaluate` for extracting review counts, ratings,
  sentiment data from JS-rendered review widgets.
- **Technology Analyst**: `obscura fetch` for vendor pricing pages.
  `browser_evaluate` for extracting pricing from interactive calculators
  (AWS pricing calculator, GCP pricing).
- **Regulatory Analyst**: `obscura fetch` for government portals that
  require JS rendering. `browser_navigate` + `browser_click` for navigating
  multi-step regulatory databases.
- **Fact Checker**: `obscura fetch` for verifying claims on JS-rendered
  pages. `browser_evaluate` for extracting specific data points to verify.

**Extraction fallback chain:**
```
Obscura (stealth, JS rendering)
  → Crawl4AI (heavy extraction, PDFs)
    → Jina Reader (fast, simple extraction)
      → Wayback (if page is down or changed)
```

### 5.4 Unsplash Integration

- Free tier: 50 req/hr (demo) or 1000 req/hr (production approval)
- Image file requests (`images.unsplash.com`) do NOT count against rate limit
- Cache all API search responses to minimize API calls
- Pre-download curated library of 50-100 generic business images (boardrooms,
  cityscapes, technology, nature, abstract) for fallback
- Trigger download endpoint for attribution compliance
- Max 1 API call per report section (use cached results for similar queries)
- All images processed through Pillow pipeline:
  1. Resize (never upscale — if image is too small, find a different one)
  2. Crop (center-weighted, preserving the most visually interesting region)
  3. Color-correct (match brand warmth — slightly warm, not cold/blue)
  4. Sharpen (unsharp mask for print clarity)
  5. Export as PNG (lossless, 300 DPI equivalent)

**Image selection strategy:**
The Presentation Designer specifies exact search terms per section. Not
"business" — but "modern boardroom meeting" for the market entry section,
"financial charts on screen" for the financial analysis section, "city skyline
india" for the geographic analysis section. Specific, relevant, not generic.

### 5.5 Second Brain (Obsidian Vault)

The Obsidian vault is HYPERION's institutional memory. It makes the system
smarter over time — each engagement's findings are saved and can be retrieved
by future engagements.

**Structure:**
```
vault/
├── engagements/          # One note per engagement
│   ├── 2026-07-18-tier2-saas-india.md
│   └── 2026-07-19-acquisition-company-x.md
├── markets/              # Market research accumulated over time
│   ├── saas-india.md
│   └── fintech-sea.md
├── competitors/          # Competitor profiles accumulated over time
│   ├── competitor-a.md
│   └── competitor-b.md
├── frameworks/           # Analytical framework templates
│   ├── dcf-template.md
│   └── porter-five-forces-template.md
└── sources/              # Source library with credibility scores
    ├── peer-reviewed/
    ├── government/
    └── industry-reports/
```

**Retrieval:**
- Keyword matching with relevance scoring (threshold: 0.15)
- No embeddings — lightweight and fast
- The Research Librarian queries the vault at the start of each engagement
  and returns relevant prior research to the Engagement Director
- At the end of each engagement, findings are saved back to the vault

**Cross-engagement knowledge linking:**
If a new engagement touches a topic researched in a prior engagement (e.g.,
"Indian SaaS market" was researched 3 months ago), the Librarian retrieves the
prior research and provides it to the specialists. This means:
- The Market Analyst doesn't start from scratch — it starts with prior TAM
  estimates and updates them with new data
- The Competitive Intel agent starts with prior competitor profiles and
  checks for changes
- The Synthesis Lead can reference prior engagement conclusions for pattern
  matching

This is what makes HYPERION a **learning system**, not a one-shot generator.

---

## 6. Report Quality — McKinsey/BCG Grade

The report is the deliverable. Everything else — the agents, the tools, the
wait gate, the routing — exists to produce this PDF. If the PDF isn't
McKinsey/BCG-grade, the system has failed.

### 6.1 Report Structure

Every report follows the same premium structure. This is not a suggestion —
it is a contract. The Presentation Designer enforces this structure, and the
Quality Gate validates it.

```
COVER PAGE
  • Full-bleed Unsplash hero image (relevant to topic, 300 DPI)
  • HYPERION wordmark + tagline ("many minds. one reading.")
  • Report title (large, Instrument Serif, 36pt)
  • Subtitle / the question being answered
  • Date, engagement ID
  • Confidence level badge (HIGH / MEDIUM / LOW with color coding)
  • No other text — the cover is a visual statement, not a summary

TABLE OF CONTENTS
  • Numbered sections with page references
  • Clean, minimal layout — section number, title, dotted line, page number

EXECUTIVE SUMMARY (1-2 pages)
  • The recommendation (bold, upfront, no burying the lede)
  • Key findings (3-5 bullet points, each with evidence reference)
  • Confidence level + reasoning (why we're confident, what would change our
    mind)
  • Critical risks (top 3, with mitigation summary)
  • This is the page the CEO reads. It must stand alone.

SECTION 1-N (each 3-8 pages)
  • Section header with relevant Unsplash image (40% page width,
    right-aligned, with caption)
  • Key insight box (highlighted, beige background, terracotta left border)
  • Body text with inline data (JetBrains Mono, 10pt)
  • Charts/graphs (Plotly, brand colors, 300 DPI, with source citation)
  • Source citations (footnote style, numbered)
  • "So what?" implication box at end of each section (sage background,
    answering: what does this finding mean for the recommendation?)
  • Each section is self-contained — a reader can jump to any section and
    understand it without reading prior sections

RISK ANALYSIS (2-3 pages)
  • Risk matrix chart (probability × impact, 5×5 grid, color-coded)
  • Top 10 risks with mitigations (table format)
  • Black swan scenarios (narrative format, with trigger conditions)
  • Residual risk summary (after mitigations)

METHODOLOGY (1 page)
  • Agents used (which specialists were spawned and why)
  • Sources accessed (count by type: peer-reviewed, government, industry,
    news, etc.)
  • Data points collected (total number of data points)
  • Confidence breakdown by domain (which areas are HIGH vs LOW confidence)
  • Limitations (what we couldn't research, what we would research with more
    time/resources)

APPENDIX
  • Full source list with URLs (numbered, matching footnote citations)
  • Data tables (raw data behind charts)
  • Agent transcripts (optional, for transparency)
  • Glossary (if industry-specific terms are used)

BACK COVER
  • HYPERION compact lockup (wordmark + Mark)
  • "many minds. one reading."
  • Generated date, engagement metadata
  • Confidentiality notice
```

### 6.2 PDF Quality Standards

| Requirement | Standard | How | Why |
|---|---|---|---|
| Resolution | 300 DPI | WeasyPrint CSS: `size: A4; dpi: 300` | Print-quality. Anything less looks pixelated. |
| Image quality | No distortion, no zoom | Pillow: fit/crop, never stretch | Stretched images scream amateur. |
| Text rendering | Crisp, print-quality | WeasyPrint + embedded fonts | Fonts must render identically on any system. |
| Color accuracy | Brand palette only | CSS with exact hex values | No random colors. Every color is deliberate. |
| Layout | No blank pages, no orphaned images | Jinja2: `page-break-inside: avoid` | Blank pages are the #1 sign of a broken layout. |
| Charts | 300 DPI, brand colors | Plotly + kaleido, scale=3 | Screenshots are blurry. Vector exports are crisp. |
| Footer | Premium, consistent | Running footer on every page | `HYPERION · many minds. one reading. · {page}` |
| Margins | 25mm all sides, 15mm binding | CSS `@page { margin: 25mm }` | Consistent margins look professional. |
| Headers | Section title in running header | CSS `string-set` from section title | Helps navigation in printed reports. |
| Typography | Instrument Serif + JetBrains Mono | Embedded in PDF | Two-font system: serif for headers, mono for body. |

### 6.3 Image Placement Rules

These rules are non-negotiable. The Presentation Designer enforces them, and
the Quality Gate validates them.

1. **Every image has adjacent text context on the SAME page.** No image on
   page 5 with explanation on page 6. If the text doesn't fit, the image
   moves to the next page with the text — not the other way around.

2. **Cover image = full-bleed.** No text overlay except the title, subtitle,
   and wordmark. The cover is a visual statement. Section images = 40% page
   width, right-aligned, with caption below.

3. **Images are topic-relevant, not generic stock.** The Presentation Designer
   specifies exact Unsplash search terms per section. "Modern boardroom
   meeting" not "business." "Mumbai skyline at dusk" not "city." The image
   must add meaning, not just fill space.

4. **All images processed through Pillow pipeline:**
   - Resize (never upscale — if image is too small, find a different one)
   - Crop (center-weighted, preserving the most visually interesting region)
   - Color-correct (match brand warmth — slightly warm, not cold/blue)
   - Sharpen (unsharp mask for print clarity)
   - Export as PNG (lossless, 300 DPI equivalent)

5. **Charts are NEVER screenshots.** Always Plotly → kaleido → PNG at
   scale=3. Screenshots are blurry, have wrong colors, and can't be edited.

6. **No image is larger than 50% of the page height** (except cover). Large
   images push text to the next page, creating blank space. Images should
   complement text, not replace it.

7. **Every image has a caption** with source attribution. "Source: Unsplash
   via [photographer name]" for photos. "Source: [data source]" for charts.

### 6.4 The Pillow Image Pipeline

Every image — whether from Unsplash or generated by Plotly — goes through the
same Pillow pipeline before being placed in the PDF:

```python
def process_image(image_path: str, target_width: int, target_height: int) -> str:
    """
    Pillow image processing pipeline for print-quality PDF.
    
    1. Open and verify image is high enough resolution
    2. Resize (downscale only, never upscale)
    3. Crop (center-weighted, preserving most interesting region)
    4. Color-correct (match brand warmth)
    5. Sharpen (unsharp mask for print)
    6. Export as PNG (lossless)
    """
    img = Image.open(image_path)
    
    # Step 1: Verify resolution
    if img.width < target_width or img.height < target_height:
        raise ImageTooSmallError(f"Image {img.width}x{img.height} < target {target_width}x{target_height}")
    
    # Step 2: Resize (downscale only)
    img = img.resize((target_width, target_height), Image.LANCZOS)
    
    # Step 3: Crop (center-weighted)
    # ... crop logic ...
    
    # Step 4: Color-correct (warm the image slightly)
    img = apply_warm_filter(img, intensity=0.05)
    
    # Step 5: Sharpen for print
    img = img.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3))
    
    # Step 6: Export as PNG
    output_path = image_path.replace('.jpg', '_processed.png')
    img.save(output_path, 'PNG', dpi=(300, 300))
    return output_path
```

### 6.5 Quality Gate Enforcement

The Quality Gate (Agent 18) validates the final PDF against the 10-dimension
rubric (see Section 4.5, Agent 18). If any dimension scores below 3/5, the
report goes back for iteration. Max 3 iterations before escalation.

The Quality Gate specifically checks:
- No blank pages (scan PDF for pages with no text content)
- No orphaned images (scan for images without adjacent text)
- All images are 300 DPI (check image metadata)
- All charts use brand colors (check chart image colors against palette)
- All fonts are embedded (check PDF metadata)
- Footer present on every page (scan for footer text)
- Page count honours the delivery contract (15-22 pages: the 15-20 target plus 2
  pages of render slack). Enforced by `hyperion/output/page_budget.page_count_verdict`
  and failed as a verification issue by the Render Engine — not merely logged.

---

## 7. Color System

HYPERION has two distinct color systems: one for the TUI (terminal) and one
for the PDF (print). Both are deliberate, both are warm, and neither uses
blue. Blue is the color of AI slop — every generic AI product uses
blue-to-purple gradients. HYPERION uses warm, earthy, premium tones inspired
by Claude's palette and aged instrument metals.

### 7.1 TUI Palette (from Design Brief — STRICT)

The TUI palette is inspired by aged instrument metals — bronze, verdigris,
obsidian. It is warm, dark, and premium. These colors are not suggestions —
they are enforced by the Textual theme.

| Token | Hex | Role |
|---|---|---|
| Obsidian | `#0C0A08` | Base surface — warm black, not cold black |
| Parchment | `#EDE4D3` | Primary text — warm off-white, not pure white |
| Burnished Bronze | `#C89550` | Primary accent — needle, actions, focus, selected items |
| Verdigris | `#4B8F7E` | Status accent — agent active, links, success states |
| Umber | `#362E22` | Structure — borders, dim chrome, inactive elements |
| Oxide | `#B5533C` | Alert — errors only, never decorative |

**Anti-slop directives:**
- No blue. No purple. No cyan. No magenta.
- No gradient backgrounds. Solid colors only.
- No glassmorphism. No blur effects.
- No neon on black. The palette is warm, not electric.
- No emoji as icons. Use Unicode symbols or custom Mark states.
- No braille spinners. Use ring segment progress.

### 7.2 PDF Report Palette (Claude-Inspired Warm, Not Blue AI Slop)

The PDF palette is inspired by Claude's warm, paper-like aesthetic. It uses
cream backgrounds, warm charcoal text, and terracotta/sage accents. This
makes the report feel like a premium printed document, not a screen export.

| Token | Hex | Role |
|---|---|---|
| Warm Charcoal | `#1A1A1A` | Primary text, headings — not pure black (too harsh) |
| Cream | `#F5F4EE` | Page background — warm paper, not white |
| Terracotta | `#C8704D` | Primary accent — headers, key boxes, chart primary |
| Sage | `#7C9885` | Secondary accent — positive findings, opportunity |
| Beige | `#E8E6DD` | Section backgrounds, callout boxes |
| Warm Gray | `#8B8680` | Captions, footnotes, secondary text |
| Deep Brown | `#3D3530` | Footer, methodology section |
| Alert Red | `#B5533C` | Risk indicators only — never decorative |

**Color usage rules:**
- Terracotta is the primary accent. Use it for section headers, key insight
  box borders, and the first series in every chart.
- Sage is the secondary accent. Use it for positive findings, opportunities,
  and the second series in charts.
- Alert Red is reserved for risk indicators. Never use it for decoration.
- Warm Gray is for secondary text only. Never use it for primary content.
- Cream is the page background. Never use white — white is cold and clinical.
- Deep Brown is for the footer and methodology section. It grounds the report.

### 7.3 Chart Color Sequence

All charts use the same color sequence. This creates visual consistency
across the report — a reader can immediately recognize which series is which
because the colors are always the same order.

```python
CHART_COLORS = [
    "#C8704D",  # Terracotta (primary — always first series)
    "#7C9885",  # Sage (secondary — always second series)
    "#3D3530",  # Deep Brown (tertiary)
    "#8B8680",  # Warm Gray (quaternary)
    "#E8E6DD",  # Beige (light fill — for backgrounds/shading)
    "#B5533C",  # Alert Red (risk series only — never default)
]
```

**Chart color rules:**
- First series is always Terracotta. No exceptions.
- Risk-related data (risk matrix, risk scenarios) uses Alert Red.
- Positive findings (opportunities, growth) use Sage.
- Never use more than 5 colors in a single chart. If you need more, group
  the remaining items into "Other."
- Never use blue, purple, or green (standard Plotly defaults). Override
  with `colorway=CHART_COLORS` in every chart.

### 7.4 Typography

Two fonts. Only two. One for headers, one for body. This is a design
constraint, not a limitation — it creates visual consistency.

| Element | Font | Size | Weight | Why |
|---|---|---|---|---|
| Cover title | Instrument Serif | 36pt | Regular | Serif conveys authority and tradition. |
| Section headers | Instrument Serif | 22pt | Regular | Consistent with cover title. |
| Subsection headers | JetBrains Mono | 14pt | Bold | Mono creates clear hierarchy from serif headers. |
| Body text | JetBrains Mono | 10pt | Regular | Mono is readable at small sizes and looks technical. |
| Captions/footnotes | JetBrains Mono | 8pt | Regular | Same family, smaller size. |
| Key insight boxes | JetBrains Mono | 11pt | Medium | Slightly larger than body to draw attention. |
| Data tables | JetBrains Mono | 9pt | Regular | Mono aligns numbers in tables perfectly. |

**Why Instrument Serif for headers:**
It is a free, high-quality serif font that conveys authority, tradition, and
premium quality. It is the font of McKinsey-style reports, not of tech blogs.

**Why JetBrains Mono for body:**
It is a free, high-quality monospace font that is highly readable at small
sizes. It looks technical and precise — appropriate for a data-driven
consulting report. It also aligns numbers perfectly in tables, which is
critical for financial data.

**Font embedding:**
Both fonts are embedded in the PDF via WeasyPrint. This ensures the PDF
renders identically on any system, regardless of installed fonts.

---

## 8. TUI — Better Than Hermes

The TUI is the user's window into HYPERION. It must be premium, branded, and
informative — not a generic terminal output. It is built on **Textual + Rich**
(Python) and exceeds Hermes Agents in every dimension.

### 8.1 Why It's Better Than Hermes

| Feature | Hermes Agents | HYPERION |
|---|---|---|
| Brand system | Terminal defaults | 6-token palette (Obsidian/Parchment/Bronze/Verdigris/Umber/Oxide) |
| Agent display | Kaomoji faces | Live agent status grid with state, tier, tools, findings count |
| Progress | Braille spinner | Ring segment progress (percentage-based, visual) |
| Brand mark | None | Animated Mark widget with 6 states |
| Markdown | Plain text | Rich Markdown rendering (headers, bold, lists, code blocks) |
| TPM visibility | None | Live TPM usage bars per provider (Google/NVIDIA/Cerebras/Groq) |
| Commands | None | Slash commands with autocomplete |
| Session | No resume | Session resume — pick up where you left off |
| Streaming | No | Streaming markdown rendering as agents produce findings |

### 8.2 TUI Screens

**Splash Screen:**
- HYPERION wordmark (large, Parchment on Obsidian)
- Tagline: "many minds. one reading."
- Animated Mark (Dormant state — slow pulse)
- Provider status (4 providers with green/red indicator)
- Vault status (Obsidian vault connected/missing)
- SearxNG status (Docker running/stopped)
- Obscura status (binary found/missing)
- Press any key to start

**Engagement Room (main screen):**
```
┌─────────────────────────────────────────────────────────────────┐
│  HYPERION · many minds. one reading.                    [Mark]  │
├─────────────────────────────────────────────────────────────────┤
│  ┌─ Agents ──────────────────┐  ┌─ TPM Usage ────────────────┐  │
│  │ ● Engagement Director     │  │ Google  ████████░░  80%    │  │
│  │   STRONG · WORKING        │  │ NVIDIA  ███░░░░░░░  30%    │  │
│  │ ● Market Analyst          │  │ Cerebras████████░░  85%    │  │
│  │   STANDARD · WORKING      │  │ Groq    ██████░░░░  60%    │  │
│  │ ● Competitive Intel       │  └────────────────────────────┘  │
│  │   STANDARD · WORKING      │                                   │
│  │ ● Financial Analyst       │  ┌─ Findings Stream ──────────┐  │
│  │   STANDARD+ · WAITING     │  │ [Market] TAM estimate:     │  │
│  │ ● Risk Analyst            │  │   $1.8B - $2.3B (range)    │  │
│  │   STANDARD · WORKING      │  │   Sources: 4 · Confidence: │  │
│  │                           │  │   HIGH                      │  │
│  │ ● Fact Checker            │  │ [Comp] Competitor A pricing │  │
│  │   FAST · IDLE             │  │   increased 15% YoY...      │  │
│  │                           │  │                             │  │
│  │ Sub-agents: 7 active      │  │                             │  │
│  └───────────────────────────┘  └─────────────────────────────┘  │
├─────────────────────────────────────────────────────────────────┤
│  > _                                                            │
│  /consult /providers /vault /export /help                       │
└─────────────────────────────────────────────────────────────────┘
```

**Deliverable View:**
- Rendered markdown of the final report (Rich Markdown widget)
- Export button (save as PDF, save as markdown)
- Quality score display (10-dimension rubric scores)
- Engagement metadata (duration, agents used, sources accessed)
- "Open PDF" button (opens the generated PDF in the system viewer)

### 8.3 Animated Mark Widget

The Mark is HYPERION's brand symbol. It is a custom Textual widget that
animates based on system state:

| State | Animation | When |
|---|---|---|
| Dormant | Slow pulse (1 cycle/3s) | System idle, no engagement running |
| Listening | Quick pulse (1 cycle/1s) | User typing a question |
| Orchestrating | Rotating segments | Engagement Director building DAG |
| Synthesizing | Converging segments | Synthesis Lead reconciling findings |
| Delivered | Solid glow | PDF generated, report ready |
| Blocked | Red flash | Error, rate limit, or quality gate rejection |

The Mark is rendered using Rich's `Text` widget with ANSI color codes. It is
not an image — it is a text-based animation that works in any terminal.

### 8.4 Slash Commands

| Command | Description |
|---|---|
| `/consult <question>` | Start a new engagement |
| `/providers` | Show LLM provider status and rate limits |
| `/vault` | Search the Obsidian vault for prior research |
| `/export <format>` | Export current report (pdf, markdown, json) |
| `/resume <id>` | Resume a previous engagement session |
| `/help` | Show available commands |
| `/clear` | Clear current engagement and return to splash |

Commands have autocomplete — typing `/` shows all available commands, and
typing `/p` filters to `/providers`.

### 8.5 Live Agent Status Grid

The agent grid shows real-time status of all active agents:

- **Agent name**: Engagement Director, Market Analyst, etc.
- **Model tier**: MICRO, FAST, STANDARD, STRONG, DEEP (color-coded)
- **State**: IDLE, WORKING, WAITING, DONE, BLOCKED (with icon)
- **Tools active**: Which tools the agent is currently using
- **Findings count**: How many findings the agent has published
- **Sub-agents**: How many sub-agents this agent has spawned

The grid updates in real-time via the AgentBus `status` channel. When an
agent changes state, the grid updates within 100ms.

### 8.6 TPM Usage Bars

Live TPM usage bars show real-time token consumption per provider:

- Each provider has a bar showing current TPM usage as a percentage of limit
- Color coding: green (<70%), yellow (70-90%), red (>90%)
- Updates in real-time as the wait gate tracks token consumption
- Helps the user understand why the system might be waiting ("Google is at
  90% TPM, requests are being routed to Cerebras")

### 8.7 Findings Stream

The findings stream shows findings as agents publish them to the AgentBus:

- Agent name (color-coded by tier)
- Finding summary (first 2-3 lines of the finding)
- Source count and confidence level
- Updates in real-time as findings are published
- Scrollable — user can scroll back to see earlier findings
- Clickable — user can expand a finding to see full content

This gives the user transparency into the research process. They can see what
agents are finding in real-time, not just the final report.

---

## 9. Project Structure

The project structure reflects the architecture — every directory maps to a
component in the system. No orphan directories. No files without a home.

```
hyperion/
├── pyproject.toml              # uv-managed dependencies, pinned versions
├── .env                        # HYPERION_ prefix (not FORGE_)
├── searxng_settings.yml        # SearxNG Docker config
├── ARCHITECTURE.md             # This file — the source of truth
├── README.md                   # Quick start, installation, usage
├── hyperion/                   # Main package
│   ├── __init__.py             # Package metadata, version
│   ├── cli.py                  # Typer CLI: consult, shell, providers, vault
│   ├── config.py               # Pydantic Settings, HYPERION_ prefix
│   ├── orchestrator.py         # Engagement Director + WorkflowEngine
│   │
│   ├── router/                 # LLM routing layer
│   │   ├── __init__.py
│   │   ├── router.py           # LLMRouter — async, TPM-aware, singleton
│   │   ├── wait_gate.py        # SlidingWindowTracker, WaitGate coordinator
│   │   ├── budget.py           # DailyBudgetPlanner with 20% reserve
│   │   ├── estimator.py        # Token estimation + calibration
│   │   └── providers/          # One file per provider
│   │       ├── __init__.py
│   │       ├── base.py         # BaseProvider — common interface
│   │       ├── google.py       # Google AI Studio (Gemma, Gemini)
│   │       ├── nvidia.py       # NVIDIA NIM (Nemotron)
│   │       ├── cerebras.py     # Cerebras (GPT OSS 120B)
│   │       └── groq.py         # Groq (Llama, GPT OSS)
│   │
│   ├── agents/                 # Multi-agent system
│   │   ├── __init__.py
│   │   ├── base.py             # BaseAgent — async, tools, tier, bus
│   │   ├── bus.py              # AgentBus — in-memory async pub/sub
│   │   ├── engagement_director.py  # Agent 1: decompose, orchestrate, adapt
│   │   ├── synthesis_lead.py   # Agent 2: reconcile, synthesize, recommend
│   │   ├── specialists/        # 12 specialist agents
│   │   │   ├── __init__.py
│   │   │   ├── market.py        # Agent 3: TAM/SAM/SOM, sizing, segmentation
│   │   │   ├── competitive.py   # Agent 4: competitor profiling, moats
│   │   │   ├── financial.py     # Agent 5: DCF, LBO, unit economics
│   │   │   ├── risk.py          # Agent 6: risk matrix, Monte Carlo
│   │   │   ├── technology.py    # Agent 7: tech stack, build-vs-buy
│   │   │   ├── operations.py    # Agent 8: process, supply chain, KPIs
│   │   │   ├── regulatory.py    # Agent 9: compliance, horizon scanning
│   │   │   ├── sustainability.py # Agent 10: ESG, carbon, green finance
│   │   │   ├── consumer.py      # Agent 11: personas, journey, WTP
│   │   │   ├── ma.py            # Agent 12: targets, synergies, integration
│   │   │   ├── innovation.py    # Agent 13: TRL, hype cycle, disruption
│   │   │   └── strategy.py      # Agent 14: Porter's, VRIO, Blue Ocean
│   │   ├── support/            # 4 support agents
│   │   │   ├── __init__.py
│   │   │   ├── librarian.py     # Agent 15: vault management, citations
│   │   │   ├── fact_checker.py  # Agent 16: claim verification, contradictions
│   │   │   ├── data_viz.py      # Agent 17: charts, Tufte principles
│   │   │   └── quality_gate.py  # Agent 18: 10-dimension rubric scoring
│   │   ├── delivery/           # 2 delivery agents
│   │   │   ├── __init__.py
│   │   │   ├── presentation.py  # Agent 19: layout, image selection, Jinja2
│   │   │   └── renderer.py      # Agent 20: WeasyPrint, Pillow, PDF assembly
│   │   └── sub_agent.py        # Junior agent spawner with 5-min timeout
│   │
│   ├── schemas/                # Pydantic models — the data contract
│   │   ├── __init__.py
│   │   ├── models.py           # FinalReport, KeyFinding, Risk, etc.
│   │   ├── workflow.py         # WorkflowDAG, TaskNode, SubTask
│   │   ├── research.py         # ResearchTree, ResearchNode, ResearchBrief
│   │   └── agents.py           # AgentSpec, AgentState, SubAgentSpec
│   │
│   ├── tools/                  # Tool implementations
│   │   ├── __init__.py
│   │   ├── searxng.py          # SearxNG search client
│   │   ├── jina.py             # Jina search + reader client
│   │   ├── obscura.py          # Obscura browser (CDP + MCP integration)
│   │   ├── crawl4ai.py         # Fallback deep extraction
│   │   ├── wayback.py          # Wayback Machine archive client
│   │   ├── unified_search.py   # SearxNG → Jina → Obscura → DDG
│   │   ├── unified_extract.py  # Obscura → Crawl4AI → Jina → Wayback
│   │   ├── alpha_vantage.py    # Financial data client
│   │   ├── fred.py             # FRED economic data client
│   │   ├── unsplash.py         # Image search + download + caching
│   │   └── second_brain.py     # Obsidian vault read/write/search
│   │
│   ├── output/                 # Report generation
│   │   ├── __init__.py
│   │   ├── render.py           # WeasyPrint + Jinja2 → PDF at 300 DPI
│   │   ├── charts.py           # Plotly → 300 DPI PNG via kaleido
│   │   ├── images.py           # Pillow pipeline (resize, crop, correct)
│   │   ├── markdown.py         # Markdown export for TUI display
│   │   └── templates/          # Jinja2 templates
│   │       ├── report.html.j2  # Main report template
│   │       ├── cover.html.j2   # Cover page template
│   │       └── styles/
│   │           └── hyperion.css # Brand CSS — colors, fonts, page rules
│   │
│   └── tui/                    # Terminal UI
│       ├── __init__.py
│       ├── app.py              # Textual App — main entry point
│       ├── theme.py            # HYPERION theme (6-token palette)
│       ├── widgets/            # Custom Textual widgets
│       │   ├── __init__.py
│       │   ├── mark.py         # Animated Mark widget (6 states)
│       │   ├── agent_grid.py   # Agent status grid (live updates)
│       │   ├── tpm_bar.py      # TPM usage bars per provider
│       │   ├── findings_stream.py # Live findings feed
│       │   └── deliverable.py  # Markdown render widget
│       └── screens/            # TUI screens
│           ├── __init__.py
│           ├── splash.py       # Splash screen with provider status
│           ├── engagement.py   # Main engagement room
│           └── deliverable.py  # Report view + export
│
├── vault/                      # Obsidian vault (Second Brain)
│   ├── engagements/            # One note per engagement
│   ├── markets/                # Market research (accumulates over time)
│   ├── competitors/            # Competitor profiles (accumulates)
│   ├── frameworks/             # Analytical framework templates
│   └── sources/                # Source library with credibility scores
│
├── reports/                    # Generated PDFs (gitignored)
├── assets/                     # Cached Unsplash images, fonts
│   ├── fonts/                  # Instrument Serif, JetBrains Mono
│   └── images/                 # Curated Unsplash fallback library
│
└── tests/                      # Test suite
    ├── __init__.py
    ├── test_router.py          # LLM router, provider selection, failover
    ├── test_wait_gate.py       # Sliding window, token estimation, budget
    ├── test_agents.py          # Agent behavior, sub-agent spawning
    ├── test_tools.py           # Tool integration tests
    └── test_output.py          # PDF generation, image pipeline, charts
```

**Key design decisions in the structure:**
- `router/` is separate from `agents/` because routing is infrastructure,
  not intelligence. Agents don't know which provider they're using — they
  request a tier and the router decides.
- `schemas/` is separate from `agents/` because schemas are the data contract
  between agents. Changing a schema affects all agents, so it needs to be
  centralized.
- `tools/` is separate from `agents/` because tools are shared infrastructure.
  Multiple agents use the same tools (SearxNG, Jina, Obscura).
- `output/` is separate from `agents/` because output generation is a pipeline,
  not intelligence. The Render Engine doesn't think — it assembles.
- `vault/` is at the root because it's persistent state, not code. It survives
  across engagements and across code changes.

---

## 10. Engagement Lifecycle (1-15 min)

An engagement is the full cycle from question to PDF. It takes 1-15 minutes
depending on complexity. Here's the detailed timeline:

### 10.1 Timeline

```
T+0:00    User enters question in TUI
          → "Should we enter the Tier-2 Indian SaaS market?"

T+0:02    Engagement Director decomposes question (STRONG tier)
          → Classifies as GO_NO_GO
          → Queries Second Brain for prior research on Indian SaaS
          → Selects specialists: Market, Competitive, Financial, Risk, Consumer
          → Builds WorkflowDAG with dependencies
          → Estimates: ~45 LLM calls, ~120K tokens, ~8 min
          → Dispatches to AgentBus

T+0:05    Specialists spawn in parallel (5 agents)
          → Market Analyst (STANDARD) starts sizing
          → Competitive Intel (STANDARD) starts profiling
          → Financial Analyst (STANDARD+) waits for Market's TAM
          → Risk Analyst (STANDARD) starts risk identification
          → Consumer Insights (STANDARD) starts persona research

T+0:05    Each specialist spawns 1-3 sub-agents for deep dives
          → Market spawns 3: TAM data, India IT spending, adoption rates
          → Competitive spawns 3: scrape 3 competitor pricing pages
          → Risk spawns 2: historical failures, regulatory risks
          → Consumer spawns 2: scrape G2 reviews, find survey data
          → Total sub-agents: 10 (all MICRO or FAST tier)

T+2:00    Sub-agents report back to specialists (structured findings)
          → Each sub-agent returns KeyFinding objects with data, sources,
            confidence
          → Specialists synthesize sub-agent findings into their own analysis

T+3:00    Specialists report findings to AgentBus
          → Market publishes MarketAnalysis (TAM $1.8B-$2.3B, HIGH confidence)
          → Competitive publishes CompetitiveLandscape (5 competitors mapped)
          → Financial receives Market's TAM, builds DCF model
          → Risk publishes RiskAnalysis (8 risks, 3 critical)
          → Consumer publishes ConsumerInsights (3 personas, WTP analysis)

T+4:00    Fact Checker verifies key claims (parallel with Financial)
          → Extracts 25 factual claims from all findings
          → Verifies each against independent sources
          → Flags 2 unverified claims, 1 contradiction
          → Sends contradiction to Synthesis Lead

T+5:00    Synthesis Lead reconciles all findings (DEEP tier)
          → Collects all findings from AgentBus
          → Builds finding matrix (5 agents × ~8 findings each)
          → Identifies 1 contradiction (Market vs Financial on penetration)
          → Resolves: Market's 12% penetration is better supported
          → Drafts recommendation: ENTER, with penetration as critical assumption
          → Produces FinalReport model

T+7:00    Quality Gate scores report (STRONG tier)
          → Scores 10 dimensions
          → Result: 4.2/5.0 (PASS)
          → Dimension 3 (analytical depth): 3/5 — flagged for improvement
          → Approved for delivery (score ≥ 4.0)

T+8:00    Presentation Designer creates layout (STRONG tier)
          → Designs page-by-page layout plan
          → Selects Unsplash images: "modern boardroom" for cover,
            "mumbai skyline" for market section, "financial charts" for
            financial section
          → Specifies chart types: TAM waterfall, competitor positioning map,
            DCF sensitivity heatmap, risk matrix

T+10:00   Data Viz generates charts (STANDARD tier)
          → TAM waterfall chart (Terracotta primary)
          → Competitor positioning map (Terracotta + Sage)
          → DCF sensitivity heatmap (Terracotta gradient)
          → Risk matrix 5×5 grid (Alert Red for high-risk cells)
          → All exported at scale=3 (300 DPI) via kaleido

T+11:00   Unsplash images selected + processed (Pillow)
          → 5 images downloaded (cover + 4 section headers)
          → Each processed through Pillow pipeline:
            resize → crop → color-correct → sharpen → PNG

T+12:00   Render Engine assembles PDF (WeasyPrint, 300 DPI)
          → Jinja2 renders HTML from template + report content
          → WeasyPrint converts HTML → PDF at 300 DPI
          → Fonts embedded (Instrument Serif + JetBrains Mono)
          → Page flow verified: no blank pages, no orphaned images
          → Footer on every page: "HYPERION · many minds. one reading. · {page}"

T+13:00   Save to vault + reports/
          → PDF saved to reports/2026-07-18-tier2-saas-india.pdf
          → Findings saved to vault/engagements/ for future reference
          → TUI switches to Deliverable View
          → User sees rendered markdown + "Open PDF" button
```

### 10.2 Adaptive Replanning Scenario

If the Regulatory Analyst discovers an unexpected compliance barrier at
T+3:30:

```
T+3:30    Regulatory Analyst publishes ESCALATION to AgentBus
          → "India's DPDP Act requires data localization for SaaS handling
            personal data — this affects 3 of 5 competitors and potentially
            our entry strategy"

T+3:31    Engagement Director receives ESCALATION
          → Spawns Regulatory Analyst (was not in original DAG)
          → Reroutes: Financial Analyst receives regulatory finding
          → Financial updates DCF to include compliance costs (+15% opex)
          → Risk Analyst adds regulatory risk to matrix
          → Synthesis Lead receives additional finding
          → Timeline extends by ~2 min (T+15:00 total)
```

This is what makes HYPERION dynamic — the team adapts mid-engagement when
new information changes the analysis.

### 10.3 Iteration Loop Scenario

If the Quality Gate scores below 4.0:

```
T+7:00    Quality Gate scores report: 3.6/5.0 (FAIL)
          → Dimension 2 (evidence sufficiency): 2/5 — 4 claims lack sources
          → Dimension 3 (analytical depth): 2/5 — Market section too shallow
          → Dimension 6 (tone): 3/5 — 3 instances of hedgy language

T+7:01    Quality Gate sends specific feedback to Synthesis Lead
          → "Add sources to claims #3, #7, #12, #18"
          → "Add 'so what?' implications to Market section findings"
          → "Replace 'might possibly' with 'is likely to' in exec summary"

T+7:02    Synthesis Lead iterates
          → Requests additional research from Market Analyst (sub-agent)
          → Fixes tone issues in executive summary
          → Adds evidence to unsupported claims
          → Produces updated FinalReport

T+9:00    Quality Gate re-scores: 4.3/5.0 (PASS)
          → Proceeds to delivery
```

Max 3 iterations. If the report still doesn't pass after 3 iterations, the
Engagement Director is notified and the report is delivered with a quality
warning.

---

## 11. Dependencies

All dependencies are pinned to minimum versions. The project uses `uv` for
dependency management — fast, reproducible, no poetry overhead.

```toml
[project]
name = "hyperion"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    # LLM + structured output
    "instructor>=1.4.0",       # Pydantic-structured LLM output
    "openai>=1.40.0",          # OpenAI-compatible client (all providers)
    "httpx>=0.27.0",           # Async HTTP for all API calls
    "tenacity>=8.5.0",         # Retry logic with exponential backoff

    # Data models + config
    "pydantic>=2.7.0",         # Schema validation, data models
    "pydantic-settings>=2.3.0", # Settings from .env with HYPERION_ prefix
    "python-dotenv>=1.0.0",    # .env file loading

    # Search + extraction
    "duckduckgo-search>=6.0.0", # DDG fallback search
    "crawl4ai>=0.4.0",         # Heavy page extraction fallback
    "transformers>=4.44.0",    # Required by Crawl4AI
    "websockets>=12.0",        # CDP protocol for Obscura

    # Report generation
    "weasyprint>=62.0",        # HTML/CSS → PDF at 300 DPI
    "jinja2>=3.1.0",           # HTML template engine
    "plotly>=5.22.0",          # Charts (bar, line, scatter, heatmap, etc.)
    "kaleido>=0.2.1",          # Plotly → PNG export at scale=3
    "pillow>=10.4.0",          # Image processing pipeline

    # TUI
    "textual>=0.70.0",         # TUI framework (screens, widgets, themes)
    "rich>=13.7.0",            # Markdown rendering, syntax highlighting
    "typer>=0.12.0",           # CLI framework (consult, shell, providers)

    # Data sources
    "alpha-vantage>=3.0.0",    # Financial data (market, fundamentals)
    "fredpy>=1.0.6",           # FRED economic data (GDP, inflation, rates)
]
```

**Why these dependencies and not others:**
- `instructor` + `openai`: All four providers (Google, NVIDIA, Cerebras, Groq)
  expose OpenAI-compatible APIs. We use one client (`openai`) with different
  base URLs. `instructor` wraps it for Pydantic-structured output.
- `httpx`: Async HTTP client. All API calls are async — the system runs
  multiple agents in parallel, so blocking HTTP would kill throughput.
- `tenacity`: Retry with exponential backoff. Used for transient failures
  (network timeouts, 503s). Not used for 429s — the wait gate prevents those
  before they happen.
- `textual` + `rich`: The best Python TUI framework. Textual provides screens,
  widgets, and themes. Rich provides markdown rendering and syntax
  highlighting. Together they create a premium terminal experience.
- `weasyprint`: The only Python library that can produce print-quality PDFs
  from HTML/CSS with proper page breaks, embedded fonts, and 300 DPI support.
  Alternatives (reportlab, fpdf2) require manual layout — WeasyPrint uses CSS,
  which is declarative and maintainable.
- `plotly` + `kaleido`: Plotly is the most expressive charting library in
  Python. Kaleido exports charts as PNG at arbitrary scale (scale=3 for
  300 DPI). Alternatives (matplotlib) produce uglier charts with more code.
- `pillow`: The standard Python image processing library. Used for the image
  pipeline (resize, crop, color-correct, sharpen).

**System requirements:**
- Python 3.12+ (match/case, improved asyncio, type parameter syntax)
- GTK3 Runtime (Windows): `winget install tschoonj.GTKForWindows` — required
  by WeasyPrint for PDF rendering
- Docker Desktop: Required for SearxNG (self-hosted meta-search)
- Obscura binary: Download from GitHub releases — required for JS-rendered
  page extraction. Place in PATH or specify location in .env.
- Obsidian (optional): For viewing/editing the vault directly. HYPERION reads
  and writes to the vault as markdown files — Obsidian is not required but
  recommended for the user to browse prior research.

**Why `uv` and not `pip`/`poetry`:**
`uv` is 10-100x faster than pip and poetry for dependency resolution and
installation. It handles virtual environments, locking, and reproducibility
in a single tool. For a project with 20+ dependencies, this matters.

---

## 12. What Makes This Not Generic

This section is the manifesto. If you read only one section, read this one.

### 12.1 Dynamic Workflow DAG — Not a Fixed Pipeline

A generic AI system has a fixed pipeline: search → analyze → write. Every
question goes through the same steps. HYPERION doesn't.

The Engagement Director analyzes the question and builds a **custom DAG** per
engagement. A market entry question spawns Market + Competitive + Financial +
Risk + Consumer. An M&A question spawns M&A + Financial + Regulatory +
Strategy. A sustainability question spawns Sustainability + Regulatory +
Operations + Consumer. A pricing question spawns Market + Financial +
Consumer + Competitive — no Risk, no Regulatory.

No two engagements are identical. The team assembles per question, just like
a real consulting firm staffs per project.

### 12.2 Every Agent Has Proprietary Skills — Not "Research and Write"

A generic AI agent is a prompt that says "research X and write about it."
HYPERION agents have **proprietary analytical frameworks**:

- Market Analyst knows TAM/SAM/SOM decomposition, bottom-up vs top-down
  sizing, market segmentation by geography/demographic/use-case.
- Financial Analyst knows DCF modeling, LBO analysis, comparable company
  analysis, unit economics (CAC, LTV, payback period).
- Risk Analyst knows Monte Carlo simulation, black swan theory, risk matrix
  construction (probability × impact), residual risk calculation.
- Strategy Analyst knows Porter's Five Forces, VRIO framework, Blue Ocean
  Strategy, SWOT (but refuses to use it — it's too generic).
- M&A Analyst knows synergy quantification (revenue synergies, cost
  synergies), integration risk assessment, accretion/dilution analysis.
- Consumer Insights knows Jobs-to-be-Done, persona construction, customer
  journey mapping, willingness-to-pay analysis.
- Innovation Analyst knows Technology Readiness Levels, Gartner Hype Cycle,
  Christensen's disruption theory, first-mover advantage analysis.
- Sustainability Analyst knows GRI standards, TCFD framework, carbon
  accounting, green finance instruments.

These aren't generic "research and write" agents. They apply specific
analytical frameworks that a real consultant would use.

### 12.3 Every Tool Is Used by the Right Agent — No Decorative Tools

A generic AI system gives every agent every tool. HYPERION assigns tools
deliberately:

- Obscura goes to specialists who need JS rendering (Competitive Intel
  scraping competitor sites, Consumer Insights scraping review sites,
  Technology Analyst scraping vendor pricing pages).
- Alpha Vantage goes to Financial Analyst and M&A Analyst only — no other
  agent needs financial market data.
- FRED goes to Market Analyst, Financial Analyst, and Sustainability
  Analyst — they need macroeconomic context.
- Wayback goes to Regulatory, Innovation, and Competitive Intel — they need
  historical snapshots to track changes over time.
- Unsplash goes to Presentation Designer and Data Visualizer only — no
  research agent touches images.
- Second Brain goes to Research Librarian (read/write) and all agents (read
  only) — agents query prior research but don't write to the vault directly.

No tool sits idle. No agent has a tool it doesn't use. Every assignment is
deliberate.

### 12.4 Sub-Agents for Context Isolation — Not Truncation

A generic AI system hits a context window limit and either truncates earlier
content or compresses it (losing detail). HYPERION delegates.

A specialist sends a focused sub-question to a junior agent. The junior agent
does focused research in its own context window, returns structured findings
(data, sources, confidence, gaps). The parent synthesizes. The parent's
context window is used for synthesis, not for raw research.

This is how real consulting teams work — a partner doesn't read 200 pages of
raw research. They read a senior associate's 5-page summary. HYPERION's
specialists are partners; sub-agents are associates.

### 12.5 Predictive Wait Gate — Not Reactive Retry

A generic AI system hits a 429 rate limit, waits, and retries. HYPERION
never hits a 429.

The wait gate tracks RPM/TPM/RPD in real-time sliding windows across all 4
providers. Before dispatching a request, it:
1. Estimates token consumption
2. Checks if the target provider has capacity
3. If yes, dispatches immediately
4. If no, finds an alternative provider with capacity
5. If no provider has capacity, waits intelligently (calculates exact wait
   time until capacity is available) rather than failing

This is predictive, not reactive. The system plans ahead.

### 12.6 Premium PDF Output — Not a Chatbot Export

A generic AI system outputs markdown or plain text. HYPERION outputs a
300 DPI PDF with:
- Full-bleed Unsplash hero image on the cover
- Plotly charts at scale=3 (300 DPI, brand colors, never screenshots)
- Instrument Serif headers + JetBrains Mono body (embedded fonts)
- Running footers on every page
- No blank pages, no orphaned images (enforced by Jinja2 page-break rules)
- 10-dimension quality gate that rejects sub-standard reports
- Cream background (not white), terracotta/sage accents (not blue)

This looks like a McKinsey deck, not a chatbot export.

### 12.7 Warm Palette — Not AI Slop

A generic AI product uses blue-to-purple gradients, glassmorphism, and neon
on black. HYPERION uses warm, earthy, premium tones:

- TUI: Obsidian, Parchment, Burnished Bronze, Verdigris, Umber, Oxide —
  inspired by aged instrument metals.
- PDF: Warm Charcoal, Cream, Terracotta, Sage, Beige, Warm Gray, Deep Brown —
  inspired by Claude's warm, paper-like aesthetic.

No blue. No purple. No cyan. No gradients. No glassmorphism. No neon. Every
color is deliberate and specific.

### 12.8 Learning System — Not a One-Shot Generator

A generic AI system starts from scratch every time. HYPERION has a Second
Brain (Obsidian vault) that accumulates knowledge across engagements:

- Each engagement's findings are saved to the vault
- The Research Librarian queries the vault at the start of each new engagement
- Prior research is provided to specialists as starting context
- The Market Analyst doesn't start from scratch — it starts with prior TAM
  estimates and updates them
- The Competitive Intel agent starts with prior competitor profiles and
  checks for changes
- The Synthesis Lead can reference prior engagement conclusions for pattern
  matching

This makes HYPERION smarter over time. The 100th engagement is better than
the 1st because it benefits from 99 prior engagements' research.

### 12.9 Quality Gate — Not "Ship Whatever the LLM Produces"

A generic AI system ships whatever the LLM produces. HYPERION has a Quality
Gate that scores the report on 10 dimensions:

1. Evidence sufficiency
2. Source quality
3. Analytical depth
4. Logical consistency
5. Risk coverage
6. Tone and register
7. Format compliance
8. Visual quality
9. Actionability
10. Completeness

If any dimension scores below 3/5, the report goes back for iteration. Max 3
iterations. This ensures every report meets a minimum quality bar before it
reaches the user.

### 12.10 Fact Checker — Not "Trust the LLM"

A generic AI system trusts whatever the LLM produces. HYPERION has a Fact
Checker that:

- Extracts factual claims from all findings
- Verifies each claim against independent sources
- Flags unverified claims
- Identifies contradictions between agents
- Sends contradictions to the Synthesis Lead for resolution

This is the difference between a system that produces opinions and a system
that produces evidence-based recommendations.

---

## 13. Summary

HYPERION is not a wrapper. It is not a generic LLM pipeline. It is a
proprietary consulting model with:

- **20 agents**, each the best version of itself — with proprietary skills,
  assigned tools, and specific methodologies
- **4 LLM providers** with predictive wait gating that never hits a 429
- **12 tools**, each assigned to agents who actually use them
- **Sub-agent spawning** for context isolation without truncation
- **Dynamic workflow DAGs** that assemble per question
- **300 DPI PDF output** with Unsplash images, Plotly charts, and brand
  typography
- **Quality gate** that rejects sub-standard reports
- **Fact checker** that verifies claims against independent sources
- **Second Brain** that makes the system smarter over time
- **Premium TUI** with live agent grid, TPM bars, and findings stream

Every component is the best version of itself. No bullshit. No filler.
No idle components. This is the engine that powers HYPERION Consulting.
