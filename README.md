# HYPERION

**many minds. one reading.**

HYPERION is a multi-agent consulting intelligence system that produces
premium PDF reports from business questions. It is NOT a generic LLM
wrapper — it is a proprietary consulting model with 20 specialized
agents, dynamic workflow orchestration, and a 5-stage report pipeline.

## Quick Start

```bash
# Clone and install
git clone https://github.com/your-org/hyperion.git
cd hyperion
uv sync

# Configure API keys
cp .env.example .env
# Edit .env with your API keys

# Run a consultation (non-interactive)
hyperion consult "Should we enter the Tier-2 Indian SaaS market?"

# Or launch the TUI (interactive)
hyperion shell
```

## Architecture

HYPERION is built on 5 principles from ARCHITECTURE.md:

1. **Dynamic Workflows** — No two engagements look the same. The
   Engagement Director decomposes each question into a custom DAG
   of tasks with dependencies and model tier assignments.

2. **20 Specialized Agents** — Each agent has proprietary skills,
   assigned tools, and a specific model tier. No generic agents.
   No idle tools. No decorative skills.

3. **5-Stage Pipeline** — Engagement Director → Specialists (parallel)
   → Fact Checker → Synthesis Lead → Quality Gate → Presentation
   Designer → Data Visualizer → Render Engine → PDF

4. **Adaptive Replanning** — The Engagement Director monitors the
   AgentBus for escalations and can spawn new agents, reroute
   dependencies, or reallocate model tiers mid-engagement.

5. **Premium Output** — 300 DPI PDFs with embedded fonts, brand
   colors (warm palette, no blue/purple AI slop), Tufte-compliant
   charts, and Pillow-processed images.

## Agent Roster

| # | Agent | Role | Tier |
|---|---|---|---|
| 1 | Engagement Director | Orchestrate, plan, adapt | STRONG |
| 2 | Synthesis Lead | Reconcile, synthesize, recommend | DEEP |
| 3-14 | 12 Specialists | Market, Competitive, Financial, Risk, Tech, Ops, Regulatory, Sustainability, Consumer, M&A, Innovation, Strategy | STANDARD/STRONG |
| 15 | Research Librarian | Vault management, citations | STANDARD |
| 16 | Fact Checker | Claim verification, contradictions | FAST |
| 17 | Data Visualizer | Charts, Tufte principles | STANDARD |
| 18 | Quality Gate | 10-dimension rubric scoring | STRONG |
| 19 | Presentation Designer | Layout, images, Jinja2 | STRONG |
| 20 | Render Engine | WeasyPrint, Pillow, PDF assembly | STANDARD |

## LLM Providers

All 5 providers expose OpenAI-compatible APIs:

| Provider | Models | Tiers Served |
|---|---|---|
| Google AI Studio | Gemma 4, Gemini 3.x | MICRO, FAST, STANDARD, DEEP |
| NVIDIA NIM | Nemotron 70B | STRONG |
| Cerebras | GPT OSS 120B | FAST, STRONG |
| Groq | Llama 3.3, GPT OSS | MICRO, FAST, STANDARD |
| Mistral AI | Mistral Large, Mistral Medium | FAST, STANDARD, STRONG |

## Data Sources

| Source | Type | API Key Required | Env Var |
|---|---|---|---|
| SearxNG | Meta-search (Docker) | No | `HYPERION_SEARXNG_URL` |
| Jina | Search + reader | Yes | `HYPERION_JINA_API_KEY` |
| Obscura | Headless browser | No (binary) | `HYPERION_OBSCURA_PATH` |
| FlareSolverr | CAPTCHA bypass (Docker) | No | `HYPERION_FLARESOLVERR_URL` |
| Alpha Vantage | Financial data | Yes | `HYPERION_ALPHA_VANTAGE_API_KEY` |
| FRED | Economic data | Yes | `HYPERION_FRED_API_KEY` |
| SEC EDGAR | Filings | No | — |
| Semantic Scholar | Academic papers | Optional (higher rate limit with key) | `HYPERION_SEMANTIC_SCHOLAR_API_KEY` |
| OpenAlex | Academic research | No | — |
| World Bank | Economic indicators | No | — |
| Google Trends | Trend data | No | — |
| HackerNews | Social sentiment | No | — |
| Reddit | Social sentiment | Yes | `HYPERION_REDDIT_CLIENT_ID` / `HYPERION_REDDIT_CLIENT_SECRET` |
| Unsplash | Image search | Yes | `HYPERION_UNSPLASH_ACCESS_KEY` |
| Wayback Machine | Archived pages | No | — |

## TUI Commands

| Command | Description |
|---|---|
| `/consult <question>` | Start a new engagement |
| `/providers` | Show LLM provider status |
| `/vault <query>` | Search the Obsidian vault |
| `/export <format>` | Export report (pdf, markdown, json) |
| `/resume <id>` | Resume a previous engagement |
| `/help` | Show available commands |
| `/clear` | Clear current engagement |

## Project Structure

```
hyperion/
├── hyperion/
│   ├── cli.py              # Typer CLI
│   ├── config.py           # Pydantic Settings
│   ├── orchestrator.py     # WorkflowEngine
│   ├── router/             # LLM routing layer
│   ├── agents/             # 20-agent system
│   │   ├── base.py         # BaseAgent + sub-agent spawning
│   │   ├── bus.py          # AgentBus pub/sub
│   │   ├── engagement_director.py
│   │   ├── synthesis_lead.py
│   │   ├── support/        # FactChecker, ResearchLibrarian
│   │   └── specialists/    # 12 specialist agents
│   ├── schemas/            # Pydantic models
│   ├── tools/              # 16 tool clients
│   ├── output/             # PDF/charts/images/markdown
│   └── tui/                # Textual TUI + boot sequence
├── vault/                  # Obsidian vault (Second Brain)
├── reports/                # Generated PDFs
├── assets/                 # Fonts, cached images
└── tests/                  # Test suite (101 tests)
```

## Configuration

All configuration is via environment variables with `HYPERION_` prefix.
See `.env.example` for the full list.

### LLM Providers

- `HYPERION_GOOGLE_API_KEY` — Google AI Studio
- `HYPERION_NVIDIA_API_KEY` — NVIDIA NIM
- `HYPERION_CEREBRAS_API_KEY` — Cerebras
- `HYPERION_GROQ_API_KEY` — Groq
- `HYPERION_MISTRAL_API_KEY` — Mistral AI

### Data Sources

- `HYPERION_SEARXNG_URL` — SearxNG URL (default: http://localhost:8888)
- `HYPERION_JINA_API_KEY` — Jina search + reader
- `HYPERION_OBSCURA_PATH` — Obscura headless browser binary path
- `HYPERION_FLARESOLVERR_URL` — FlareSolverr URL (default: http://localhost:8191/v1)
- `HYPERION_ALPHA_VANTAGE_API_KEY` — Alpha Vantage financial data
- `HYPERION_FRED_API_KEY` — FRED economic data
- `HYPERION_SEMANTIC_SCHOLAR_API_KEY` — Semantic Scholar (optional, raises rate limit)
- `HYPERION_REDDIT_CLIENT_ID` — Reddit app client ID
- `HYPERION_REDDIT_CLIENT_SECRET` — Reddit app client secret
- `HYPERION_UNSPLASH_ACCESS_KEY` — Unsplash image search

### Infrastructure

- `HYPERION_VAULT_PATH` — Obsidian vault path (default: ./vault)
- `HYPERION_REPORTS_DIR` — Output directory for PDFs (default: ./reports)
- `HYPERION_ASSETS_DIR` — Cached fonts and images (default: ./assets)
- `HYPERION_QUALITY_THRESHOLD` — Min quality score 1-5 (default: 4.0)
- `HYPERION_MAX_QUALITY_ITERATIONS` — Quality gate iterations (default: 3)
- `HYPERION_SUB_AGENT_TIMEOUT` — Sub-agent timeout in seconds (default: 300)
- `HYPERION_MAX_SUB_AGENTS` — Max sub-agents per specialist (default: 3)

## Recent Upgrades

### Timeout & Parallelization Fixes

- **FactChecker LLM call fixed** — `FactChecker._extract_claims` was calling
  `self._call_llm()` which doesn't exist on `BaseAgent`. Fixed to use
  `self._llm_complete()` with correct `response.content` attribute.

- **Sub-agent parallelization** — All 12 specialist agents were spawning
  sub-agents sequentially (3 × 300s = 900s, exceeding the 600s specialist
  timeout). Replaced sequential `for` loops with `asyncio.gather()` for
  parallel execution, reducing total sub-agent time to ~300s.

- **Synthesis Lead timeout** — `SynthesisLead.run()` executes 8 sequential
  steps with multiple LLM calls but was given only 300s. Increased to 600s
  (`SPECIALIST_TIMEOUT_SECONDS`). Quality Gate iteration loop timeouts
  also increased to 600s.

- **Parallel section building** — `SynthesisLead._build_analysis_sections`
  was generating each section's LLM narrative sequentially. Refactored to
  use `asyncio.gather()` so all sections build concurrently.

### Data Source Expansion

- **5th LLM provider** — Added Mistral AI (free Experiment tier) with
  Mistral Large and Mistral Medium models.

- **New data sources** — SEC EDGAR, Semantic Scholar, OpenAlex, World Bank,
  Google Trends, HackerNews, Reddit, Wayback Machine, FlareSolverr.

- **Config schema** — Added `semantic_scholar_api_key` to `ToolPathsConfig`
  and `HyperionSettings` so Pydantic loads it from `.env`.

## License

Proprietary. © HYPERION Consulting.
