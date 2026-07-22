# HYPERION Phase 2 — Data Source Expansion: Implementation Plan

> **Status**: READY FOR EXECUTION
> **Date**: July 2026
> **Scope**: 7 new structured data source tools, full pipeline integration
> **Constraint**: All free/self-hosted/free-API. Zero ongoing cost.
> **Prerequisite**: Phase 1 (VIGIL Integration) — COMPLETE

---

## Summary

| Metric | Before | After | Delta |
|---|---|---|---|
| Tools in registry | 16 | 23 | +7 |
| Data source types | 3 | 10 | +233% |
| New tool files | 0 | 7 | 7 |
| Agent spec modifications | 0 | 13 | 13 files |
| New dependencies | 0 | 2 (pytrends, praw) | 2 |
| Monthly cost | $0 | $0 | Maintained |

---

## Integration Points (per tool)

Every Phase 2 tool is integrated in **7 places**:

1. **Tool file**: `hyperion/tools/{name}.py` — client class + data models
2. **ToolName enum**: `hyperion/schemas/agents.py` — add enum value
3. **`_instantiate_tool` (base.py)**: `hyperion/agents/base.py` — add branch
4. **`_instantiate_tool` (sub_agent.py)**: `hyperion/agents/sub_agent.py` — add branch
5. **Agent specs**: Add `ToolName.{TOOL}` to relevant `AgentSpec.tools=[]`
6. **`_gather_raw_data`**: `hyperion/agents/sub_agent.py` — add data source block
7. **`__init__.py` + tests**: `hyperion/tools/__init__.py` + `tests/test_{name}.py` + update `tests/test_tools.py`

---

## Tool Design Pattern

All Phase 2 tools follow the pattern established by `alpha_vantage.py` and `fred.py`:

- `__init__(self, settings=None)` with lazy `httpx.AsyncClient` init
- `@dataclass` result models with `to_dict()`
- In-memory cache with 1-hour TTL (`_cache_get` / `_cache_set`)
- Rate limiting via `_rate_limit()` (sleep between calls)
- `async def close()` for cleanup
- Graceful error handling (return `None` on failure, never crash)
- `MAX_CONTENT_CHARS = 15000` for content truncation

---

## Step 2.1 — SEC EDGAR (`sec_edgar.py`)

**API**: `https://data.sec.gov` + `https://efts.sec.gov/LATEST/search-index`
**Auth**: None (User-Agent header with email required)
**Rate limit**: 10 req/sec (100ms delay)
**Key methods**:
- `get_cik(company_name) -> str | None` — CIK lookup via tickers JSON
- `get_company_info(cik) -> SECCompanyInfo` — name, ticker, SIC, fiscal year
- `get_filings(cik, filing_type, start_date, end_date, limit) -> list[SECFiling]`
- `get_filing_content(filing) -> SECFilingContent` — full text, truncated 15K
- `search_full_text(query, filing_type, limit) -> list[SECFiling]`
- `get_financial_statements(cik, statement) -> dict` — XBRL structured data

**Data models**: `SECFiling`, `SECFilingContent`, `SECCompanyInfo`

**Agent assignment**:
- Financial Analyst — 10-K/10-Q for DCF modeling (more detail than Alpha Vantage)
- M&A Analyst — due diligence on target companies
- Competitive Intel — competitor financial data from filings

**Sub-agent block**: Search full-text → format filing list → fetch most recent filing content

---

## Step 2.2 — Semantic Scholar (`semantic_scholar.py`)

**API**: `https://api.semanticscholar.org/graph/v1`
**Auth**: None (optional API key for higher limits)
**Rate limit**: 100 req/5min no key = 3s delay; 1 req/sec with key
**Key methods**:
- `search(query, limit, fields, year_range) -> list[AcademicPaper]`
- `get_paper(paper_id) -> AcademicPaper | None`
- `get_citations(paper_id, limit) -> list[AcademicPaper]`
- `get_references(paper_id, limit) -> list[AcademicPaper]`
- `get_tldr(paper_id) -> str` — AI-generated summary
- `recommend(paper_id, limit) -> list[AcademicPaper]`

**Data models**: `AcademicPaper` (title, abstract, authors, year, venue, citation_count, tldr, open_access_pdf_url, fields_of_study)

**Agent assignment**:
- Innovation Analyst — TRL assessment needs academic papers
- Technology Analyst — peer-reviewed tech evaluations
- Market Analyst — market sizing academic studies

**Sub-agent block**: Search papers → format title/year/abstract/citations → append to raw_data

---

## Step 2.3 — OpenAlex (`openalex.py`)

**API**: `https://api.openalex.org`
**Auth**: None (email in User-Agent for polite pool)
**Rate limit**: None (10 req/sec recommended = 100ms delay)
**Key methods**:
- `search_works(query, limit) -> list[OpenAlexWork]`
- `get_institution(institution_id) -> OpenAlexInstitution | None`
- `search_by_concept(concept, limit) -> list[OpenAlexWork]`
- `get_citations(work_id, limit) -> list[OpenAlexWork]`
- `_reconstruct_abstract(inverted_index) -> str` — OpenAlex stores abstracts as inverted indices

**Data models**: `OpenAlexWork` (title, abstract, authors, year, venue, cited_by_count, doi, concepts, institutions), `OpenAlexInstitution` (name, country, r&d_spending, faculty_count, works_count)

**Agent assignment**:
- Innovation Analyst — complements Semantic Scholar with institution data
- Research Librarian — source credibility scoring for academic sources

**Sub-agent block**: Search works → format title/year/citation count → append

---

## Step 2.4 — World Bank (`world_bank.py`)

**API**: `https://api.worldbank.org/v2`
**Auth**: None
**Rate limit**: None (100ms delay)
**Key methods**:
- `get_indicator(indicator_code, country, date_range) -> list[WorldBankIndicator]`
- `get_country_profile(country_code) -> WorldBankCountryProfile`
- `search_indicators(query) -> list[Indicator]`
- `compare_countries(indicator_code, countries, year) -> dict[str, float]`

**Pre-defined indicator codes**:
- `gdp`: `NY.GDP.MKTP.CD`, `gdp_per_capita`: `NY.GDP.PCAP.CD`
- `inflation`: `FP.CPI.TOTL.ZG`, `trade_pct_gdp`: `NE.TRD.GNFS.ZS`
- `population`: `SP.POP.TOTL`, `health_spending`: `SH.XPD.CHEX.GD.ZS`
- `education_spending`: `SE.XPD.TOTL.GD.ZS`, `unemployment`: `SL.UEM.TOTL.ZS`
- `fdi`: `BX.KLT.DINV.WD.GD.ZS`, `co2_emissions`: `EN.ATM.CO2E.KT`
- `renewable_energy`: `EG.FEC.RNEW.ZS`

**Data models**: `WorldBankIndicator`, `WorldBankIndicatorData`, `WorldBankCountryProfile`

**Agent assignment**:
- Market Analyst — international market sizing (GDP, population, trade)
- Financial Analyst — country risk premiums, inflation for DCF
- Sustainability Analyst — ESG indicators (CO2, health, education)

**Sub-agent block**: Search indicators → fetch time series → format country/value/year → append

---

## Step 2.5 — Google Trends (`google_trends.py`)

**API**: Unofficial via `pytrends` library
**Auth**: None
**Rate limit**: ~100 req/hour (Google throttles aggressively — 36s delay)
**Dependency**: `pytrends>=4.7.3` (add to `pyproject.toml`)
**Key methods**:
- `get_interest_over_time(keywords, timeframe, geography) -> TrendResult`
- `get_related_queries(keyword, rising) -> list[RelatedQuery]`
- `get_related_topics(keyword) -> list[RelatedTopic]`
- `get_interest_by_region(keyword, resolution) -> dict[str, int]`

**Data models**: `TrendResult` (keywords, interest_data, timeframe, geography), `RelatedQuery` (query, growth_pct), `RelatedTopic` (topic, type)

**Timeframe codes**: `"today 5-y"` (5yr), `"today 12-m"` (12mo), `"today 3-m"` (3mo), `"now 7-d"` (7days)

**Agent assignment**:
- Market Analyst — market demand signals, trend analysis
- Consumer Insights — consumer interest by geography, related queries
- Innovation Analyst — hype cycle positioning via search interest

**Sub-agent block**: Get interest over time (12mo) → get related rising queries → format → append

**Note**: pytrends is synchronous — must run in `asyncio.to_thread()` wrapper.

---

## Step 2.6 — HackerNews (`hackernews.py`)

**API**: `https://hn.algolia.com/api/v1` (Algolia-powered HN search)
**Auth**: None
**Rate limit**: None (100ms delay)
**Key methods**:
- `search_stories(query, tags, hits) -> list[HNStory]`
- `get_story(story_id) -> HNStory`
- `get_comments(story_id) -> list[HNComment]`
- `search_by_date(query, days) -> list[HNStory]`

**Data models**: `HNStory` (title, url, points, num_comments, author, created_at), `HNComment` (text, author, points, created_at)

**Agent assignment**:
- Consumer Insights — developer/tech sentiment for B2B products
- Technology Analyst — tech adoption signals, developer sentiment
- Innovation Analyst — emerging tech discussions, hype signals

**Sub-agent block**: Search stories → format title/points/comments/url → append

---

## Step 2.7 — Reddit (`reddit.py`)

**API**: OAuth2 via `praw` library
**Auth**: `HYPERION_REDDIT_CLIENT_ID` + `HYPERION_REDDIT_CLIENT_SECRET` in `.env`
**Rate limit**: 100 req/min (OAuth2)
**Dependency**: `praw>=7.7.0` (add to `pyproject.toml`)
**Config**: Add `reddit_client_id`, `reddit_client_secret`, `reddit_user_agent` to `Settings`
**Key methods**:
- `search_subreddits(query, limit) -> list[Subreddit]`
- `search_posts(query, subreddit, sort, time_filter, limit) -> list[RedditPost]`
- `get_comments(post_id, limit) -> list[RedditComment]`
- `get_sentiment(posts) -> SentimentSummary` — keyword-based (no ML)

**Data models**: `Subreddit` (name, subscribers, description), `RedditPost` (title, subreddit, upvote_ratio, num_comments, score, created_at), `RedditComment` (text, author, score), `SentimentSummary` (positive/negative/neutral counts, key_themes)

**Agent assignment**:
- Consumer Insights — consumer sentiment, pain points, reviews
- Market Analyst — market demand signals, consumer discussions

**Sub-agent block**: Search posts (relevance, year, 15 results) → format title/subreddit/upvote/comments → append

**Note**: praw is synchronous — must run in `asyncio.to_thread()` wrapper.

---

## Step 2.8 — Register All Tools in ToolName Enum

**File**: `hyperion/schemas/agents.py`

Add 7 new enum values after `DEEP_SEARCH`:

```python
# ── Data Sources (Phase 2) ──
SEC_EDGAR = "sec_edgar"
SEMANTIC_SCHOLAR = "semantic_scholar"
OPEN_ALEX = "open_alex"
WORLD_BANK = "world_bank"
GOOGLE_TRENDS = "google_trends"
HACKERNEWS = "hackernews"
REDDIT = "reddit"
```

Update docstring: `"""The 23 tools in HYPERION's registry (§5.1)."""`

---

## Step 2.9 — Wire All Tools in `_instantiate_tool`

### `hyperion/agents/base.py` (after `DEEP_SEARCH` branch, before `PLOTLY`):

```python
elif tool == ToolName.SEC_EDGAR:
    from hyperion.tools.sec_edgar import SECEdgarClient
    return SECEdgarClient(settings=self.settings)
elif tool == ToolName.SEMANTIC_SCHOLAR:
    from hyperion.tools.semantic_scholar import SemanticScholarClient
    return SemanticScholarClient(settings=self.settings)
elif tool == ToolName.OPEN_ALEX:
    from hyperion.tools.openalex import OpenAlexClient
    return OpenAlexClient(settings=self.settings)
elif tool == ToolName.WORLD_BANK:
    from hyperion.tools.world_bank import WorldBankClient
    return WorldBankClient(settings=self.settings)
elif tool == ToolName.GOOGLE_TRENDS:
    from hyperion.tools.google_trends import GoogleTrendsClient
    return GoogleTrendsClient(settings=self.settings)
elif tool == ToolName.HACKERNEWS:
    from hyperion.tools.hackernews import HackerNewsClient
    return HackerNewsClient(settings=self.settings)
elif tool == ToolName.REDDIT:
    from hyperion.tools.reddit import RedditClient
    return RedditClient(settings=self.settings)
```

### `hyperion/agents/sub_agent.py` (after `DEEP_SEARCH` branch):

Same 7 branches (using `settings = get_settings()` pattern instead of `self.settings`).

---

## Step 2.10 — Update `__init__.py`

**File**: `hyperion/tools/__init__.py`

Add imports:
```python
from hyperion.tools.sec_edgar import SECEdgarClient, SECFiling, SECFilingContent, SECCompanyInfo
from hyperion.tools.semantic_scholar import SemanticScholarClient, AcademicPaper, CitationGraph
from hyperion.tools.openalex import OpenAlexClient, OpenAlexWork, OpenAlexInstitution
from hyperion.tools.world_bank import WorldBankClient, WorldBankIndicator, WorldBankIndicatorData, WorldBankCountryProfile
from hyperion.tools.google_trends import GoogleTrendsClient, TrendResult, RelatedQuery, RelatedTopic
from hyperion.tools.hackernews import HackerNewsClient, HNStory, HNComment
from hyperion.tools.reddit import RedditClient, RedditPost, RedditComment, SentimentSummary, Subreddit
```

Add to `__all__`: all new class names.

---

## Step 2.11 — Update Agent Specs

| Agent | File | Add Tools |
|---|---|---|
| Financial Analyst | `specialists/financial_analyst.py` | `SEC_EDGAR`, `WORLD_BANK` |
| M&A Analyst | `specialists/ma_analyst.py` | `SEC_EDGAR` |
| Competitive Intel | `specialists/competitive_intel.py` | `SEC_EDGAR` |
| Innovation Analyst | `specialists/innovation_analyst.py` | `SEMANTIC_SCHOLAR`, `OPEN_ALEX`, `GOOGLE_TRENDS`, `HACKERNEWS` |
| Technology Analyst | `specialists/technology_analyst.py` | `SEMANTIC_SCHOLAR`, `HACKERNEWS` |
| Market Analyst | `specialists/market_analyst.py` | `WORLD_BANK`, `GOOGLE_TRENDS`, `SEMANTIC_SCHOLAR`, `REDDIT` |
| Sustainability Analyst | `specialists/sustainability_analyst.py` | `WORLD_BANK` |
| Consumer Insights | `specialists/consumer_insights.py` | `GOOGLE_TRENDS`, `HACKERNEWS`, `REDDIT` |
| Research Librarian | `support/research_librarian.py` | `OPEN_ALEX` |

**Rule**: No agent gets a tool it doesn't use. Every tool is assigned to agents who actually need it.

---

## Step 2.12 — Add Data Source Blocks to `_gather_raw_data`

**File**: `hyperion/agents/sub_agent.py`, in `_gather_raw_data()` method

Add after the existing FRED block (before Second Brain block), 7 new blocks:

1. **SEC EDGAR**: `search_full_text()` → format filing list → fetch most recent content
2. **Semantic Scholar**: `search()` with year_range → format title/year/abstract/citations
3. **OpenAlex**: `search_works()` → format title/year/citation count
4. **World Bank**: `search_indicators()` → `get_indicator()` → format country/value/year
5. **Google Trends**: `get_interest_over_time()` (12mo) → `get_related_queries()` (rising) → format
6. **HackerNews**: `search_stories()` → format title/points/comments/url
7. **Reddit**: `search_posts()` (relevance, year, 15 results) → format title/subreddit/upvote/comments

Each block follows the pattern:
```python
if self._has_tool("{tool_name}"):
    try:
        client = self._get_tool("{tool_name}")
        results = await client.{method}(...)
        if results:
            formatted = "\n".join(...)
            raw_data.append(f"{label}:\n{formatted}")
    except Exception as e:
        errors.append(f"{ToolName}: {e!s:.80}")
```

---

## Step 2.13 — Update Config

**File**: `hyperion/config.py`, in `Settings` class

Add after `fred_api_key`:
```python
# ── Phase 2 Data Sources ──
reddit_client_id: str = ""
reddit_client_secret: str = ""
reddit_user_agent: str = "hyperion_research/1.0"
```

SEC EDGAR, Semantic Scholar, OpenAlex, World Bank, Google Trends, HackerNews need no config (no API keys).

---

## Step 2.14 — Update Dependencies

**File**: `pyproject.toml`

Add to `dependencies`:
```toml
"pytrends>=4.7.3",       # Google Trends unofficial API
"praw>=7.7.0",            # Reddit API wrapper
```

SEC EDGAR, Semantic Scholar, OpenAlex, World Bank, HackerNews all use `httpx` (already a dependency).

---

## Step 2.15 — Update Tests

### `tests/test_tools.py`

- Update `expected_tools` list: add `"sec_edgar"`, `"semantic_scholar"`, `"open_alex"`, `"world_bank"`, `"google_trends"`, `"hackernews"`, `"reddit"`
- Update `test_tool_count`: `assert len(list(ToolName)) == 23`
- Add init tests for each new client

### New test files (7)

Each `tests/test_{tool_name}.py` follows the pattern:
```python
class Test{ToolClient}:
    def test_init(self):
        """Client initializes with settings."""
        ...
    
    async def test_search_basic(self):
        """Basic search returns results (mocked)."""
        ...
    
    def test_error_handling(self):
        """Client handles API errors gracefully."""
        ...
```

---

## Step 2.16 — Update Sub-Agent System Prompt

**File**: `hyperion/agents/sub_agent.py`, in `_build_system_prompt()`

Update instruction 8 to mention new data sources:
```python
"8. Follow the tool selection strategy: SearxNG + Jina Search in "
"parallel for discovery, then Obscura → Scrapling → Jina Reader → "
"Crawl4AI for extraction. Use SEC EDGAR for financial filings, "
"Semantic Scholar/OpenAlex for academic papers, World Bank for "
"macro indicators, Google Trends for demand signals, HackerNews/Reddit "
"for community sentiment. Scrapling handles anti-bot pages."
```

---

## Final Tool Assignment Matrix (Post-Phase 2)

| Agent | Tools |
|---|---|
| Engagement Director | SECOND_BRAIN, DEEP_SEARCH |
| Synthesis Lead | SECOND_BRAIN |
| Market Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, ALPHA_VANTAGE, FRED, **WORLD_BANK**, **GOOGLE_TRENDS**, **SEMANTIC_SCHOLAR**, **REDDIT** |
| Competitive Intel | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, WAYBACK, **SEC_EDGAR** |
| Financial Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, ALPHA_VANTAGE, FRED, **SEC_EDGAR**, **WORLD_BANK** |
| Risk Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH |
| Technology Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, **SEMANTIC_SCHOLAR**, **HACKERNEWS** |
| Operations Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH |
| Regulatory Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, WAYBACK |
| Sustainability Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, FRED, **WORLD_BANK** |
| Consumer Insights | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, **GOOGLE_TRENDS**, **HACKERNEWS**, **REDDIT** |
| M&A Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, ALPHA_VANTAGE, **SEC_EDGAR** |
| Innovation Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, WAYBACK, **SEMANTIC_SCHOLAR**, **OPEN_ALEX**, **GOOGLE_TRENDS**, **HACKERNEWS** |
| Strategy Analyst | SEARXNG, JINA, OBSCURA, DEEP_SEARCH |
| Research Librarian | SECOND_BRAIN, **OPEN_ALEX** |
| Fact Checker | SEARXNG, JINA, OBSCURA, DEEP_SEARCH, CRAWL4AI |
| Data Visualizer | PLOTLY, UNSPLASH, PILLOW |
| Quality Gate | SECOND_BRAIN, DEEP_SEARCH |
| Presentation Designer | UNSPLASH, PLOTLY, JINJA2, WEASYPRINT |
| Render Engine | WEASYPRINT, PILLOW |

---

## Execution Order

| # | Step | Files | Est. Time |
|---|---|---|---|
| 1 | Create `sec_edgar.py` | 1 new file | ~45 min |
| 2 | Create `semantic_scholar.py` | 1 new file | ~30 min |
| 3 | Create `openalex.py` | 1 new file | ~30 min |
| 4 | Create `world_bank.py` | 1 new file | ~30 min |
| 5 | Create `google_trends.py` | 1 new file + pyproject.toml | ~40 min |
| 6 | Create `hackernews.py` | 1 new file | ~20 min |
| 7 | Create `reddit.py` | 1 new file + pyproject.toml + config.py | ~40 min |
| 8 | Update ToolName enum | `schemas/agents.py` | ~5 min |
| 9 | Wire `_instantiate_tool` (base.py) | `agents/base.py` | ~10 min |
| 10 | Wire `_instantiate_tool` (sub_agent.py) | `agents/sub_agent.py` | ~10 min |
| 11 | Update `__init__.py` | `tools/__init__.py` | ~10 min |
| 12 | Update 9 agent specs | 9 specialist/support files | ~30 min |
| 13 | Add 7 data source blocks to `_gather_raw_data` | `agents/sub_agent.py` | ~30 min |
| 14 | Update sub-agent system prompt | `agents/sub_agent.py` | ~5 min |
| 15 | Update config.py | `config.py` | ~5 min |
| 16 | Update tests | `test_tools.py` + 7 new test files | ~60 min |
| 17 | Run tests, verify all pass | — | ~15 min |
| **Total** | | | **~7.5 hours** |

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Google Trends throttling (pytrends unofficial) | High | Medium | 36s delay between calls; cache aggressively; fall back to SearxNG |
| Reddit OAuth2 setup complexity | Low | Low | Document .env setup; Reddit API approval is automatic |
| SEC EDGAR rate limiting | Low | Low | 100ms delay = 10 req/sec (generous) |
| Semantic Scholar rate limit (no key) | Medium | Low | 3s delay; cache 1 hour; optional API key |
| OpenAlex abstract reconstruction | Low | Low | Inverted index is well-documented; handle missing abstracts |
| pytrends/praw sync blocking event loop | Medium | Medium | Wrap in `asyncio.to_thread()` |
| Context overflow from too many data sources | Low | Medium | Each source truncated to 15K chars; sub-agent context is isolated |

---

## What This Plan Does NOT Do

- No new LLM providers
- No agent roster changes (same 20 agents)
- No report structure changes (same PDF pipeline)
- No TUI changes (new tools appear in agent tool lists automatically)
- No pgvector / Redis / Ollama / embeddings
- No new Docker containers (all tools are in-process Python or external free APIs)
