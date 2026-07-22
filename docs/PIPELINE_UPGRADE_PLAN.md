# HYPERION Pipeline Upgrade Plan — VIGIL Integration + Data Source Expansion

> **Status**: DRAFT — Awaiting approval before implementation
> **Date**: July 2026
> **Scope**: Replace paid search tools (Tavily/Exa/Brave) with free, production-grade alternatives
> **Constraint**: All tools must be free, self-hosted, or free-API. Zero ongoing cost.

---

## Executive Summary

This plan upgrades HYPERION's research pipeline in **2 major phases**:

| Phase | Focus | New Tools | Files Created | Files Modified | Est. Effort |
|---|---|---|---|---|---|
| **Phase 1** | VIGIL Integration | Scrapling, DeepSearch, EvidenceScorer | 4 new tool files | 8 existing files | ~6-8 hours |
| **Phase 2** | Data Source Expansion | SEC EDGAR, Semantic Scholar, OpenAlex, World Bank, Google Trends, HackerNews, Reddit | 7 new tool files | 12 existing files | ~10-12 hours |

**Total**: 11 new tool files, ~20 file modifications, 7 new `ToolName` enum entries, 7 new `_instantiate_tool` branches, sub-agent fallback chain rewrite, content depth 3x increase.

---

## VIGIL Verification Summary (Pre-Plan)

The VIGIL document was verified component-by-component. Key findings:

**Adopted** (Layers 1-3, 5):
- Obscura as **primary** extractor (promote from fallback)
- **Scrapling** as middle-tier extractor (69K stars, production-grade, anti-bot bypass)
- **Parallel discovery** (SearXNG + Jina Search simultaneously)
- **Evidence scoring** (lightweight heuristic — support/conflict/neutral)
- **`deep_search()`** orchestration tool (single entry point for agents)
- **File-based caching** (not Redis — Hyperion has no Redis)

**Rejected** (Layer 4):
- `pgvector` — Hyperion has no PostgreSQL. Skip semantic rerank.
- `Redis` — Hyperion has no Redis. Use in-memory dict cache (already exists in SecondBrain).
- `Ollama` local embeddings — 2GB new infra, not "zero cost." Defer indefinitely.
- Cosine similarity as "Exa replacement" — overstated. Keyword-adjacent, not semantic.

**Added** (VIGIL gaps):
- 7 data source tools VIGIL doesn't mention (SEC EDGAR, academic, macro, sentiment)
- Hyperion-specific integration plan (ToolName, _instantiate_tool, agent specs)
- Content depth increase (8000→20000 chars)
- Batch scraping with Obscura `scrape()` (already implemented, underutilized)

---

## PHASE 1: VIGIL Integration

### Step 1.1 — Add Scrapling Tool

**New file**: `hyperion/tools/scrapling.py`

Scrapling (D4Vinci/Scrapling, 69K stars, v0.4.11, BSD-3) is an adaptive web scraping framework with anti-bot bypass (Cloudflare Turnstile), Playwright integration, and adaptive selectors that survive page changes.

```python
# hyperion/tools/scrapling.py — outline

class ScraplingClient:
    """Adaptive web scraper — middle-tier extractor in the VIGIL chain.
    
    Position in extraction fallback:
      1. Obscura (fast, stealth, JS rendering)
      2. Scrapling (adaptive, anti-bot, Playwright)  ← NEW
      3. Jina Reader (fast, simple extraction)
      4. Crawl4AI (heavy extraction, PDFs)
      5. Wayback (if page is down or changed)
    
    Use cases:
      - Pages where Obscura gets flagged but aren't behind Cloudflare
      - Pages with dynamic selectors that change frequently
      - Pages needing Playwright-level browser automation
      - Batch scraping with adaptive parsing
    
    Dependencies: scrapling>=0.4.11 (pip install scrapling)
    """
    
    def __init__(self, settings):
        self.settings = settings
        self._fetcher = None  # Lazy init StealthyFetcher
    
    async def fetch(self, url: str, stealth: bool = True) -> ScraplingResult:
        """Fetch a single URL with adaptive parsing.
        
        Uses StealthyFetcher (Playwright + anti-bot) by default.
        Falls back to DynamicFetcher if stealth fails.
        Returns markdown content + metadata.
        """
        ...
    
    async def scrape_batch(self, urls: list[str], concurrency: int = 10) -> list[ScraplingResult]:
        """Batch scrape multiple URLs in parallel.
        
        Uses asyncio.gather with semaphore for concurrency control.
        Each URL gets adaptive parsing — selectors auto-recover
        when page structure changes.
        """
        ...
    
    async def close(self):
        """Clean up Playwright browser instances."""
        ...
```

**Dependencies to add** in `pyproject.toml`:
```toml
"scrapling>=0.4.11",      # Adaptive web scraping with anti-bot
```

**Key design decisions**:
- `StealthyFetcher` as default (Playwright-based, bypasses Cloudflare Turnstile out of the box)
- `DynamicFetcher` as internal fallback (lighter, no stealth)
- Adaptive selectors: Scrapling auto-generates CSS/XPath selectors that survive page changes
- Batch mode: `scrape_batch()` uses `asyncio.gather` with semaphore, same pattern as Obscura's `scrape()`
- Content truncation: 15000 chars (up from 8000 — see Step 1.6)

---

### Step 1.2 — Register Scrapling in ToolName Enum

**File**: `hyperion/schemas/agents.py`
**Lines**: 101-121 (ToolName enum)

Add `SCRAPLING` to the enum:

```python
class ToolName(str, Enum):
    """The tools in HYPERION's registry (§5.1)."""

    SEARXNG = "searxng"
    JINA = "jina"
    OBSCURA = "obscura"
    SCRAPLING = "scrapling"          # ← NEW: adaptive web scraper
    CRAWL4AI = "crawl4ai"
    FLARESOLVERR = "flaresolverr"
    WAYBACK = "wayback"
    ALPHA_VANTAGE = "alpha_vantage"
    FRED = "fred"
    UNSPLASH = "unsplash"
    SECOND_BRAIN = "second_brain"
    PLOTLY = "plotly"
    WEASYPRINT = "weasyprint"
    JINJA2 = "jinja2"
    PILLOW = "pillow"
    # Phase 2 additions (see Step 2.x below)
```

**File**: `tests/test_tools.py`
**Lines**: 22-34

Update test to expect 14 tools (13 existing + 1 Scrapling):
```python
def test_all_tools_defined(self):
    expected_tools = [
        "searxng", "jina", "obscura", "scrapling", "crawl4ai", "wayback",
        "alpha_vantage", "fred", "unsplash", "second_brain",
        "plotly", "weasyprint", "jinja2", "pillow",
    ]
    ...

def test_tool_count(self):
    assert len(list(ToolName)) == 14  # 13 + scrapling
```

---

### Step 1.3 — Wire Scrapling into _instantiate_tool

**File**: `hyperion/agents/base.py`
**Lines**: 521-574 (`_instantiate_tool` method)

Add Scrapling branch after Obscura:

```python
def _instantiate_tool(self, tool: ToolName) -> Any:
    ...
    elif tool == ToolName.OBSCURA:
        from hyperion.tools.obscura import ObscuraClient
        return ObscuraClient(settings=self.settings)
    elif tool == ToolName.SCRAPLING:           # ← NEW
        from hyperion.tools.scrapling import ScraplingClient
        return ScraplingClient(settings=self.settings)
    elif tool == ToolName.CRAWL4AI:
        ...
```

**File**: `hyperion/agents/sub_agent.py`
**Lines**: 142-177 (`_instantiate_tool` method)

Add the same branch in the sub-agent's tool instantiator:

```python
def _instantiate_tool(self, tool: Any) -> Any:
    ...
    elif tool == ToolName.OBSCURA:
        from hyperion.tools.obscura import ObscuraClient
        return ObscuraClient(settings=settings)
    elif tool == ToolName.SCRAPLING:           # ← NEW
        from hyperion.tools.scrapling import ScraplingClient
        return ScraplingClient(settings=settings)
    elif tool == ToolName.CRAWL4AI:
        ...
```

---

### Step 1.4 — Add Scrapling to Agent Specs

Scrapling goes to agents that currently have Obscura. These agents need JS-rendered page scraping and will benefit from Scrapling's anti-bot and adaptive selectors.

**Files to modify** (add `ToolName.SCRAPLING` to the `tools` list in each agent's spec):

| File | Agent | Current Obscura? | Add Scrapling? |
|---|---|---|---|
| `hyperion/agents/specialists/competitive_intel.py` | Competitive Intelligence | ✅ | ✅ |
| `hyperion/agents/specialists/consumer_insights.py` | Consumer Insights | ✅ | ✅ |
| `hyperion/agents/specialists/technology_analyst.py` | Technology Analyst | ✅ | ✅ |
| `hyperion/agents/specialists/regulatory_analyst.py` | Regulatory Analyst | ✅ | ✅ |
| `hyperion/agents/specialists/ma_analyst.py` | M&A Analyst | ✅ | ✅ |
| `hyperion/agents/specialists/market_analyst.py` | Market Analyst | ✅ | ✅ |
| `hyperion/agents/specialists/sustainability_analyst.py` | Sustainability Analyst | ✅ | ✅ |
| `hyperion/agents/specialists/operations_analyst.py` | Operations Analyst | ✅ | ✅ |

**Example modification** in each file (e.g., `competitive_intel.py`):

```python
# BEFORE:
tools=[
    ToolName.SEARXNG,
    ToolName.JINA,
    ToolName.OBSCURA,
    ToolName.WAYBACK,
],

# AFTER:
tools=[
    ToolName.SEARXNG,
    ToolName.JINA,
    ToolName.OBSCURA,
    ToolName.SCRAPLING,    # ← NEW: adaptive scraper for anti-bot pages
    ToolName.WAYBACK,
],
```

**Also add to Fact Checker** (`hyperion/agents/support/fact_checker.py`):
Fact Checker currently has Obscura. Add Scrapling for verifying claims on pages that block Obscura.

**Also add to SubAgentRunner default tools**: The sub-agent's `_gather_raw_data` method needs a Scrapling step (see Step 1.5).

---

### Step 1.5 — Rewrite Sub-Agent Fallback Chain

**File**: `hyperion/agents/sub_agent.py`
**Lines**: 252-401 (`_gather_raw_data` method)

This is the core change. The current fallback chain is:
```
Search: SearxNG → (done)
Extract: Jina → Obscura → Crawl4AI → FlareSolverr
```

The new fallback chain (VIGIL-aligned) is:
```
Search: SearxNG + Jina Search (parallel)  ← parallel discovery
Extract: Obscura → Scrapling → Jina Reader → Crawl4AI → FlareSolverr  ← Scrapling inserted
```

**Detailed changes to `_gather_raw_data`**:

```python
async def _gather_raw_data(self) -> str:
    """Gather raw data using the available tools.
    
    VIGIL-aligned fallback chain (§5.2 updated):
    - Search: SearxNG + Jina Search in parallel (discovery layer)
    - Extract: Obscura → Scrapling → Jina Reader → Crawl4AI → FlareSolverr
    - Historical: Wayback Machine
    - Financial: Alpha Vantage
    - Macro: FRED
    - Prior research: Second Brain
    """
    raw_data: list[str] = []
    errors: list[str] = []

    # ── PARALLEL DISCOVERY ──────────────────────────────────────────
    # Run SearxNG and Jina Search simultaneously, merge + dedup results
    
    searxng_urls: list[str] = []
    jina_search_urls: list[str] = []
    
    search_tasks = []
    
    if self._has_tool("searxng"):
        search_tasks.append(self._search_searxng())
    if self._has_tool("jina"):
        search_tasks.append(self._search_jina())
    
    # Run searches in parallel
    if search_tasks:
        results = await asyncio.gather(*search_tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                errors.append(f"Search: {result!s:.80}")
            elif isinstance(result, tuple):
                label, urls, formatted = result
                if formatted:
                    raw_data.append(formatted)
                if label == "searxng":
                    searxng_urls = urls
                elif label == "jina":
                    jina_search_urls = urls
    
    # Merge + dedup URLs from both search sources
    all_urls = list(dict.fromkeys(searxng_urls + jina_search_urls))  # preserves order, dedups
    
    # ── EXTRACTION (VIGIL fallback chain) ───────────────────────────
    # Obscura → Scrapling → Jina Reader → Crawl4AI → FlareSolverr
    
    extracted_urls: set[str] = set()
    
    # Tier 1: Obscura (stealth, fast, JS rendering)
    if self._has_tool("obscura") and all_urls:
        try:
            obscura = self._get_tool("obscura")
            for url in all_urls[:6]:
                if url in extracted_urls:
                    continue
                try:
                    fetch_result = await obscura.fetch(url)
                    if fetch_result and (fetch_result.markdown or fetch_result.content):
                        text = (fetch_result.markdown or fetch_result.content)[:15000]
                        raw_data.append(f"Obscura content from {url}:\n{text}")
                        extracted_urls.add(url)
                except Exception:
                    continue
        except Exception as e:
            errors.append(f"Obscura: {e!s:.80}")
    
    # Tier 2: Scrapling (adaptive, anti-bot, Playwright)  ← NEW
    if self._has_tool("scrapling") and all_urls:
        try:
            scrapling = self._get_tool("scrapling")
            for url in all_urls[:6]:
                if url in extracted_urls:
                    continue
                try:
                    scrape_result = await scrapling.fetch(url, stealth=True)
                    if scrape_result and scrape_result.content:
                        text = scrape_result.content[:15000]
                        raw_data.append(f"Scrapling content from {url}:\n{text}")
                        extracted_urls.add(url)
                except Exception:
                    continue
        except Exception as e:
            errors.append(f"Scrapling: {e!s:.80}")
    
    # Tier 3: Jina Reader (fast, simple extraction)
    if self._has_tool("jina") and all_urls:
        try:
            jina = self._get_tool("jina")
            for url in all_urls[:8]:
                if url in extracted_urls:
                    continue
                try:
                    read_result = await jina.read(url)
                    if read_result and (read_result.markdown or read_result.content):
                        text = (read_result.markdown or read_result.content)[:15000]
                        raw_data.append(f"Jina content from {url}:\n{text}")
                        extracted_urls.add(url)
                except Exception:
                    continue
        except Exception as e:
            errors.append(f"Jina: {e!s:.80}")
    
    # Tier 4: Crawl4AI (heavy extraction, PDFs)
    # ... (existing Crawl4AI block, increase truncation to 15000)
    
    # Tier 5: FlareSolverr (CAPTCHA-protected pages)
    # ... (existing FlareSolverr block, increase truncation to 15000)
    
    # ── DATA SOURCES (unchanged) ────────────────────────────────────
    # Wayback, Alpha Vantage, FRED, Second Brain blocks remain the same
    # ...
```

**Helper methods to add** (private async methods for parallel search):

```python
async def _search_searxng(self) -> tuple[str, list[str], str | None]:
    """Search via SearxNG. Returns (label, urls, formatted_results)."""
    try:
        searxng = self._get_tool("searxng")
        results = await searxng.search(self.spec.question, num_results=15)
        if results:
            formatted = "\n".join(
                f"- {r.title}: {r.url}\n  {r.snippet[:500]}"
                for r in results[:15]
            )
            urls = [r.url for r in results[:8] if r.url]
            return ("searxng", urls, f"SearxNG results:\n{formatted}")
    except Exception as e:
        ...
    return ("searxng", [], None)

async def _search_jina(self) -> tuple[str, list[str], str | None]:
    """Search via Jina s.jina.ai. Returns (label, urls, formatted_results)."""
    try:
        jina = self._get_tool("jina")
        results = await jina.search(self.spec.question, num_results=10)
        if results:
            formatted = "\n".join(
                f"- {r.title}: {r.url}\n  {r.snippet[:500]}"
                for r in results[:10]
            )
            urls = [r.url for r in results[:6] if r.url]
            return ("jina", urls, f"Jina search results:\n{formatted}")
    except Exception as e:
        ...
    return ("jina", [], None)
```

**Content truncation changes**: All `[:8000]` slices in `_gather_raw_data` become `[:15000]`. This is a 87.5% increase in content depth per source. The sub-agent's LLM analysis will have significantly more context to work with.

**Sub-agent system prompt update** (`_build_system_prompt`, line 204-207):
```python
# BEFORE:
"8. Follow the tool selection strategy: SearxNG first (free, "
"unlimited), Jina for extraction, Obscura for JS-rendered pages, "
"Crawl4AI for heavy/PDF extraction."

# AFTER:
"8. Follow the tool selection strategy: SearxNG + Jina Search in "
"parallel for discovery, then Obscura → Scrapling → Jina Reader → "
"Crawl4AI for extraction. Scrapling handles anti-bot pages that "
"Obscura can't crack."
```

---

### Step 1.6 — Increase Content Truncation Limits

**File**: `hyperion/agents/sub_agent.py`
**All `[:8000]` occurrences in `_gather_raw_data`**: Change to `[:15000]`

This affects:
- Jina Reader extraction (line 292)
- Obscura extraction (line 308)
- Crawl4AI extraction (line 326)
- FlareSolverr extraction (line 348)

**Rationale**: 8000 chars ≈ 2000 tokens. With free-tier models having 128K-262K context windows, 8000 chars is needlessly restrictive. 15000 chars ≈ 3750 tokens per source, × 6 sources = ~22,500 tokens of raw data. This fits comfortably in even the smallest context window (16K Gemma) after system prompt + user prompt.

**Also update specialist agents**: Each specialist agent file has its own Jina/Obscura extraction loops with `[:8000]` or `[:5000]` truncation. These should also be increased to `[:15000]`.

**Files to update** (truncation limits in extraction loops):

| File | Current Limit | New Limit |
|---|---|---|
| `hyperion/agents/sub_agent.py` | 8000 | 15000 |
| `hyperion/agents/specialists/financial_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/market_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/competitive_intel.py` | 8000 | 15000 |
| `hyperion/agents/specialists/consumer_insights.py` | 8000 | 15000 |
| `hyperion/agents/specialists/technology_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/regulatory_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/risk_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/operations_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/sustainability_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/ma_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/innovation_analyst.py` | 8000 | 15000 |
| `hyperion/agents/specialists/strategy_analyst.py` | 8000 | 15000 |

---

### Step 1.7 — Create DeepSearch Orchestration Tool

**New file**: `hyperion/tools/deep_search.py`

This is VIGIL's key innovation — a single tool that wraps the entire search → extract → score pipeline. Agents call `deep_search(query, depth)` instead of individually invoking SearxNG, Jina, Obscura, etc.

```python
# hyperion/tools/deep_search.py — outline

class DeepSearchClient:
    """Unified search orchestration tool — VIGIL Layer 5.
    
    Wraps the entire discovery → extraction → scoring pipeline into
    a single call. Agents don't need to know about SearxNG, Obscura,
    Scrapling, or Jina — they just call deep_search().
    
    Pipeline:
      1. Parallel discovery (SearxNG + Jina Search)
      2. URL dedup + ranking by source credibility
      3. Extraction (Obscura → Scrapling → Jina → Crawl4AI)
      4. Evidence scoring (support/conflict/neutral heuristic)
      5. Result ranking by relevance + evidence score + freshness
      6. Return ranked, cited markdown
    
    Args:
        query: The search query
        depth: "quick" (3 sources), "standard" (6 sources), "deep" (10 sources)
        geography: Optional geography filter
    
    Returns:
        DeepSearchResult with:
          - ranked_results: list of extracted content with scores
          - evidence_summary: support/conflict/neutral assessment
          - sources: deduplicated source list with credibility scores
          - raw_urls: all discovered URLs for further scraping
    """
    
    def __init__(self, settings):
        self.settings = settings
        self._searxng = None
        self._jina = None
        self._obscura = None
        self._scrapling = None
        self._crawl4ai = None
        self._evidence_scorer = None  # EvidenceScorer from Step 1.8
        self._cache: dict[str, DeepSearchResult] = {}  # in-memory cache
    
    async def search(
        self,
        query: str,
        depth: str = "standard",
        geography: str | None = None,
    ) -> DeepSearchResult:
        """Execute a deep search with parallel discovery and ranked extraction."""
        # Check cache
        cache_key = f"{query}:{depth}:{geography}"
        if cache_key in self._cache:
            return self._cache[cache_key]
        
        num_sources = {"quick": 3, "standard": 6, "deep": 10}.get(depth, 6)
        
        # Phase 1: Parallel discovery
        urls = await self._discover(query, geography)
        
        # Phase 2: Extraction (VIGIL fallback chain)
        extracted = await self._extract_batch(urls[:num_sources * 2])
        
        # Phase 3: Evidence scoring
        scored = self._evidence_scorer.score(query, extracted)
        
        # Phase 4: Rank by relevance + evidence + freshness
        ranked = sorted(scored, key=lambda r: (r.relevance_score + r.evidence_score) / 2, reverse=True)
        
        # Phase 5: Build result
        result = DeepSearchResult(
            query=query,
            ranked_results=ranked[:num_sources],
            evidence_summary=self._evidence_scorer.summarize(ranked),
            sources=[r.source for r in ranked[:num_sources]],
            raw_urls=urls,
        )
        
        # Cache for 1 hour
        self._cache[cache_key] = result
        return result
    
    async def _discover(self, query: str, geography: str | None) -> list[str]:
        """Parallel discovery via SearxNG + Jina Search."""
        ...
    
    async def _extract_batch(self, urls: list[str]) -> list[ExtractedContent]:
        """Extract content from URLs using VIGIL fallback chain."""
        ...
    
    async def close(self):
        """Clean up all tool instances."""
        ...
```

**Register in ToolName enum**:
```python
DEEP_SEARCH = "deep_search"  # ← NEW: unified search orchestration
```

**Register in `_instantiate_tool`** (both `base.py` and `sub_agent.py`):
```python
elif tool == ToolName.DEEP_SEARCH:
    from hyperion.tools.deep_search import DeepSearchClient
    return DeepSearchClient(settings=self.settings)
```

**Agent assignment**: `DEEP_SEARCH` goes to **all specialists** and **Fact Checker**. It replaces the need for agents to individually manage SearxNG/Jina/Obscura/Scrapling — though those tools remain available for direct use when an agent needs specific control (e.g., Competitive Intel using Obscura's CDP for interactive pricing calculators).

**Important**: `DEEP_SEARCH` does NOT replace `OBSCURA` or `SCRAPLING` in agent specs. It supplements them. Agents use `deep_search()` for standard research and fall back to direct tool access for specialized tasks (CDP interactive sessions, batch scraping, etc.).

---

### Step 1.8 — Create Evidence Scorer

**New file**: `hyperion/tools/evidence_scorer.py`

This replaces VIGIL's Layer 4 (pgvector/Redis/embeddings) with a lightweight heuristic that requires zero new infrastructure. It scores extracted content as supporting, conflicting, or neutral relative to the search query.

```python
# hyperion/tools/evidence_scorer.py — outline

class EvidenceScorer:
    """Heuristic evidence scoring — VIGIL Layer 4 (no embeddings).
    
    Replaces VIGIL's proposed pgvector + Ollama embeddings with a
    lightweight keyword-overlap + domain-quality + freshness heuristic.
    Zero new infrastructure required.
    
    Scoring dimensions:
      1. Relevance: keyword overlap between query and content (TF-IDF-like)
      2. Source credibility: peer_reviewed > government > industry_report 
         > news > blog > social_media
      3. Freshness: newer content scores higher (decay over 2 years)
      4. Evidence stance: does the content support, conflict, or remain
         neutral relative to the query's implied claim?
    
    Output per result:
      - relevance_score: 0.0-1.0
      - evidence_score: 0.0-1.0  
      - stance: "support" | "conflict" | "neutral"
      - credibility_score: 0.0-1.0
      - freshness_score: 0.0-1.0
      - composite_score: weighted average of all dimensions
    """
    
    # Domain credibility mapping
    DOMAIN_CREDIBILITY = {
        ".gov": 0.95, ".edu": 0.90, ".org": 0.75,
        ".com": 0.50, ".io": 0.45, ".ai": 0.45,
        "arxiv.org": 0.90, "nature.com": 0.90, "sciencedirect.com": 0.85,
        "bloomberg.com": 0.80, "reuters.com": 0.80, "ft.com": 0.80,
        "statista.com": 0.75, "crunchbase.com": 0.70,
        "wikipedia.org": 0.60, "reddit.com": 0.35, "medium.com": 0.30,
    }
    
    def score(self, query: str, results: list[ExtractedContent]) -> list[ScoredResult]:
        """Score extracted content against the query."""
        ...
    
    def _score_relevance(self, query: str, content: str) -> float:
        """TF-IDF-like keyword overlap scoring."""
        ...
    
    def _score_credibility(self, url: str) -> float:
        """Score source credibility based on domain."""
        ...
    
    def _score_freshness(self, published_date: str | None) -> float:
        """Score freshness with 2-year exponential decay."""
        ...
    
    def _determine_stance(self, query: str, content: str) -> str:
        """Determine if content supports, conflicts, or is neutral.
        
        Heuristic approach:
        - Extract key claims from query (numbers, directions, assertions)
        - Check if content contains supporting or contradicting language
        - Look for negation patterns ("however", "but", "contrary to", 
          "despite", "actually", "in fact")
        - If no clear signal, return "neutral"
        """
        ...
    
    def summarize(self, results: list[ScoredResult]) -> EvidenceSummary:
        """Build an evidence summary across all results.
        
        Returns:
          - support_count: how many sources support the implied claim
          - conflict_count: how many sources conflict
          - neutral_count: how many are neutral
          - overall_stance: "supported" | "contested" | "mixed" | "insufficient"
          - confidence: based on source count + credibility + agreement
        """
        ...
```

**No ToolName enum entry needed** — `EvidenceScorer` is an internal component of `DeepSearchClient`, not a standalone tool. It's instantiated by `DeepSearchClient.__init__()`.

---

### Step 1.9 — Update ARCHITECTURE.md

**File**: `hyperion/ARCHITECTURE.md`

Update the following sections:

**§5.1 Core Tools table** (line 1788): Add Scrapling and DeepSearch rows:
```
| Scrapling | Browser | Competitive Intel, Consumer, Technology, Regulatory, M&A, Market, Sustainability, Operations, Fact Checker | Adaptive web scraper. Anti-bot bypass (Cloudflare Turnstile), Playwright-based, adaptive selectors. Middle-tier extractor between Obscura and Jina. |
| DeepSearch | Orchestration | All specialists, Fact Checker | Unified search orchestration. Wraps discovery → extraction → scoring into one call. Agents call deep_search(query, depth) instead of managing individual tools. |
```

**§5.2 Tool Selection Logic** (line 1804): Update the extract fallback chain:
```
Extract task:
  1. Obscura (stealth, JS rendering, fast)
  2. Scrapling (adaptive, anti-bot, Playwright)  ← NEW
  3. Jina Reader (fast, simple extraction)
  4. Crawl4AI (if Obscura + Scrapling fail — heavy extraction, PDFs)
  5. Wayback (if the page is down or changed)
```

**§5.3 Extraction fallback chain** (line 1908): Update:
```
Obscura (stealth, JS rendering)
  → Scrapling (adaptive, anti-bot, Playwright)  ← NEW
    → Jina Reader (fast, simple extraction)
      → Crawl4AI (heavy extraction, PDFs)
        → Wayback (if page is down or changed)
```

**§11 Dependencies** (line 2727): Add scrapling:
```toml
"scrapling>=0.4.11",      # Adaptive web scraping with anti-bot
```

**§4.4 Agent tool lists**: Add Scrapling to each specialist's tool list in the documentation.

---

### Step 1.10 — Docker Compose for Scrapling

Scrapling runs in-process (Python library), not as a separate service. No Docker container needed. However, it requires Playwright browsers:

**System requirement to add to README.md and ARCHITECTURE.md §11**:
```bash
# After pip install scrapling, install Playwright browsers:
scrapling install  # Downloads Chromium for StealthyFetcher
```

---

## PHASE 2: Data Source Expansion

VIGIL only covers web search/extraction. It doesn't mention the structured data sources that consulting reports need: financial filings, academic research, macroeconomic indicators, market trends, and community sentiment. This phase adds 7 new data source tools.

### Step 2.1 — SEC EDGAR Tool

**New file**: `hyperion/tools/sec_edgar.py`

SEC EDGAR provides free, unlimited access to all US public company filings (10-K, 10-Q, 8-K, S-1, proxy statements). No API key required.

```python
# hyperion/tools/sec_edgar.py — outline

class SECEdgarClient:
    """SEC EDGAR filing retrieval — free, unlimited, no API key.
    
    Provides access to:
      - 10-K (annual reports), 10-Q (quarterly), 8-K (current events)
      - S-1 (IPO filings), DEF 14A (proxy statements)
      - CIK lookup (company name → CIK number)
      - Full-text search of all filings
      - Financial statement data (via XBRL)
    
    Used by: Financial Analyst, M&A Analyst, Competitive Intel
    Rate limit: 10 req/sec (SEC asks for polite rate limiting)
    """
    
    BASE_URL = "https://data.sec.gov"
    SEARCH_URL = "https://efts.sec.gov/LATEST/search-index?q="
    HEADERS = {"User-Agent": "HYPERION Consulting research@hyperion.ai"}
    
    async def get_cik(self, company_name: str) -> str | None:
        """Look up CIK number by company name."""
        ...
    
    async def get_filings(
        self, cik: str, filing_type: str = "10-K",
        start_date: str | None = None, end_date: str | None = None,
    ) -> list[Filing]:
        """Get filings for a company."""
        ...
    
    async def get_filing_content(self, filing_url: str) -> str:
        """Get the full text content of a filing."""
        ...
    
    async def search_full_text(self, query: str, filing_type: str = "10-K") -> list[Filing]:
        """Full-text search across all SEC filings."""
        ...
    
    async def get_financial_statements(self, cik: str, statement: str = "income") -> dict:
        """Get structured financial statement data via XBRL."""
        ...
```

**Register in ToolName**:
```python
SEC_EDGAR = "sec_edgar"  # ← NEW: SEC filings
```

**Agent assignment**:
- `FinancialAnalyst`: Add `SEC_EDGAR` (replaces some Alpha Vantage usage for US public companies — more detailed than Alpha Vantage)
- `MAAnalyst`: Add `SEC_EDGAR` (M&A due diligence needs 10-K filings)
- `CompetitiveIntel`: Add `SEC_EDGAR` (competitor financial data from filings)

---

### Step 2.2 — Semantic Scholar Tool

**New file**: `hyperion/tools/semantic_scholar.py`

Semantic Scholar provides free API access to 200M+ academic papers. No API key required (optional key for higher rate limits).

```python
# hyperion/tools/semantic_scholar.py — outline

class SemanticScholarClient:
    """Academic paper search — Semantic Scholar API.
    
    Provides access to:
      - Paper search by keyword
      - Paper details (abstract, authors, citations, references)
      - Citation graphs (who cited whom)
      - TLDRs (AI-generated paper summaries)
      - Recommendations (similar papers)
    
    Used by: Innovation Analyst, Technology Analyst, Market Analyst
    Rate limit: 100 req/5min (no key), 1 req/sec (with free key)
    """
    
    BASE_URL = "https://api.semanticscholar.org/graph/v1"
    
    async def search(self, query: str, limit: int = 10, fields: list[str] = None) -> list[Paper]:
        """Search for papers by keyword."""
        ...
    
    async def get_paper(self, paper_id: str) -> Paper:
        """Get paper details by ID."""
        ...
    
    async def get_citations(self, paper_id: str, limit: int = 50) -> list[Paper]:
        """Get papers that cite this paper."""
        ...
    
    async def get_references(self, paper_id: str, limit: int = 50) -> list[Paper]:
        """Get papers referenced by this paper."""
        ...
    
    async def get_tldr(self, paper_id: str) -> str:
        """Get AI-generated TLDR for a paper."""
        ...
    
    async def recommend(self, paper_id: str, limit: int = 10) -> list[Paper]:
        """Get recommended papers similar to this one."""
        ...
```

**Register in ToolName**:
```python
SEMANTIC_SCHOLAR = "semantic_scholar"  # ← NEW: academic papers
```

**Agent assignment**:
- `InnovationAnalyst`: Add `SEMANTIC_SCHOLAR` (TRL assessment needs academic papers)
- `TechnologyAnalyst`: Add `SEMANTIC_SCHOLAR` (tech evaluations need peer-reviewed sources)
- `MarketAnalyst`: Add `SEMANTIC_SCHOLAR` (market sizing sometimes has academic studies)

---

### Step 2.3 — OpenAlex Tool

**New file**: `hyperion/tools/openalex.py`

OpenAlex is a fully open catalog of scholarly works (250M+ works, 90M+ authors). No API key, no rate limit (just be polite).

```python
# hyperion/tools/openalex.py — outline

class OpenAlexClient:
    """Open scholarly catalog — OpenAlex API.
    
    Complements Semantic Scholar with:
      - Works search (by concept, institution, author, venue)
      - Institution data (R&D spending, faculty count, research output)
      - Concept/classification taxonomy
      - Citation counts and altmetrics
      - Grant/funding data
    
    Used by: Innovation Analyst, Technology Analyst, Research Librarian
    Rate limit: None (polite rate, 10 req/sec recommended)
    """
    
    BASE_URL = "https://api.openalex.org"
    
    async def search_works(self, query: str, limit: int = 25) -> list[Work]:
        """Search works by keyword."""
        ...
    
    async def get_institution(self, institution_id: str) -> Institution:
        """Get institution details (R&D, faculty, output)."""
        ...
    
    async def search_by_concept(self, concept: str, limit: int = 25) -> list[Work]:
        """Search works by concept classification."""
        ...
    
    async def get_citations(self, work_id: str) -> list[Work]:
        """Get citing works."""
        ...
```

**Register in ToolName**:
```python
OPEN_ALEX = "open_alex"  # ← NEW: open scholarly catalog
```

**Agent assignment**:
- `InnovationAnalyst`: Add `OPEN_ALEX` (complements Semantic Scholar with institution data)
- `ResearchLibrarian`: Add `OPEN_ALEX` (source credibility scoring for academic sources)

---

### Step 2.4 — World Bank Tool

**New file**: `hyperion/tools/world_bank.py`

World Bank API provides free, unlimited access to global economic indicators (GDP, inflation, trade, population, health, education) for 200+ countries.

```python
# hyperion/tools/world_bank.py — outline

class WorldBankClient:
    """World Bank economic indicators — free, unlimited, no API key.
    
    Provides access to:
      - Macroeconomic indicators (GDP, GNI, inflation, trade)
      - Social indicators (population, literacy, health spending)
      - Sector indicators (energy, agriculture, infrastructure)
      - Country profiles and comparisons
      - Time series data (1960-present for most indicators)
    
    Used by: Market Analyst, Financial Analyst, Sustainability Analyst
    Rate limit: None (unpoliced, just be reasonable)
    """
    
    BASE_URL = "https://api.worldbank.org/v2"
    
    async def get_indicator(
        self, indicator_code: str, country: str = "all",
        date_range: str = "2010:2025", format: str = "json",
    ) -> list[IndicatorData]:
        """Get time series data for an indicator.
        
        Common indicator codes:
          NY.GDP.MKTP.CD - GDP (current US$)
          FP.CPI.TOTL.ZG - Inflation, consumer prices (annual %)
          NE.TRD.GNFS.ZS - Trade (% of GDP)
          SP.POP.TOTL - Population, total
          SH.XPD.CHEX.GD.ZS - Current health expenditure (% of GDP)
        """
        ...
    
    async def get_country_profile(self, country_code: str) -> CountryProfile:
        """Get a country's economic profile."""
        ...
    
    async def search_indicators(self, query: str) -> list[Indicator]:
        """Search for indicators by name."""
        ...
    
    async def compare_countries(
        self, indicator_code: str, countries: list[str],
        year: str = "2024",
    ) -> dict[str, float]:
        """Compare an indicator across countries."""
        ...
```

**Register in ToolName**:
```python
WORLD_BANK = "world_bank"  # ← NEW: global economic indicators
```

**Agent assignment**:
- `MarketAnalyst`: Add `WORLD_BANK` (market sizing for international markets needs GDP, population, trade data)
- `FinancialAnalyst`: Add `WORLD_BANK` (DCF models for international markets need country risk premiums, inflation rates)
- `SustainabilityAnalyst`: Add `WORLD_BANK` (ESG scoring needs environmental indicators, health spending, education data)

---

### Step 2.5 — Google Trends Tool

**New file**: `hyperion/tools/google_trends.py`

Google Trends provides search interest over time. No official API, but the `pytrends` library provides unofficial access. Free, no API key.

```python
# hyperion/tools/google_trends.py — outline

class GoogleTrendsClient:
    """Google Trends search interest — free, no API key.
    
    Provides access to:
      - Search interest over time (0-100 relative scale)
      - Related queries (rising and top)
      - Related topics
      - Geographic breakdown (country, region, city)
      - Time range filtering (1 month to 5 years)
    
    Used by: Market Analyst, Consumer Insights, Innovation Analyst
    Rate limit: ~100 req/hour (Google throttles aggressively)
    Dependencies: pytrends>=4.7.3 (unofficial API client)
    """
    
    async def get_interest_over_time(
        self, keywords: list[str], timeframe: str = "today 5-y",
        geography: str = "",
    ) -> TrendResult:
        """Get search interest over time for keywords.
        
        Returns 0-100 relative interest scale.
        timeframe: "today 5-y" (5 years), "today 12-m" (12 months),
                   "today 3-m" (3 months), "now 7-d" (7 days)
        geography: "" (global), "US", "IN", "US-CA" (California)
        """
        ...
    
    async def get_related_queries(self, keyword: str, rising: bool = True) -> list[RelatedQuery]:
        """Get related queries (rising or top)."""
        ...
    
    async def get_related_topics(self, keyword: str) -> list[RelatedTopic]:
        """Get related topics."""
        ...
    
    async def get_interest_by_region(self, keyword: str, resolution: str = "COUNTRY") -> dict[str, int]:
        """Get geographic breakdown of search interest."""
        ...
```

**Dependencies to add** in `pyproject.toml`:
```toml
"pytrends>=4.7.3",       # Google Trends unofficial API
```

**Register in ToolName**:
```python
GOOGLE_TRENDS = "google_trends"  # ← NEW: search interest trends
```

**Agent assignment**:
- `MarketAnalyst`: Add `GOOGLE_TRENDS` (market demand signals, trend analysis)
- `ConsumerInsights`: Add `GOOGLE_TRENDS` (consumer interest by geography, related queries)
- `InnovationAnalyst`: Add `GOOGLE_TRENDS` (hype cycle positioning via search interest)

---

### Step 2.6 — HackerNews Tool

**New file**: `hyperion/tools/hackernews.py`

HackerNews (Y Combinator) provides a free, public API for tech community sentiment. No API key, no rate limit.

```python
# hyperion/tools/hackernews.py — outline

class HackerNewsClient:
    """HackerNews community sentiment — free, no API key.
    
    Provides access to:
      - Story search (by keyword, date range, points threshold)
      - Comment extraction (full text of discussions)
      - Point count and comment count (popularity signals)
      - User profiles (karma, account age)
    
    Used by: Consumer Insights, Technology Analyst, Innovation Analyst
    Rate limit: None
    """
    
    BASE_URL = "https://hn.algolia.com/api/v1"  # Algolia-powered HN search
    
    async def search_stories(self, query: str, tags: str = "story", hits: int = 20) -> list[HNStory]:
        """Search HackerNews stories by keyword."""
        ...
    
    async def get_story(self, story_id: str) -> HNStory:
        """Get a story with full comment tree."""
        ...
    
    async def get_comments(self, story_id: str) -> list[HNComment]:
        """Get all comments for a story."""
        ...
    
    async def search_by_date(self, query: str, days: int = 30) -> list[HNStory]:
        """Search stories from the last N days."""
        ...
```

**Register in ToolName**:
```python
HACKERNEWS = "hackernews"  # ← NEW: tech community sentiment
```

**Agent assignment**:
- `ConsumerInsights`: Add `HACKERNEWS` (developer/tech sentiment for B2B products)
- `TechnologyAnalyst`: Add `HACKERNEWS` (tech adoption signals, developer sentiment)
- `InnovationAnalyst`: Add `HACKERNEWS` (emerging tech discussions, hype signals)

---

### Step 2.7 — Reddit Tool

**New file**: `hyperion/tools/reddit.py`

Reddit provides free API access (OAuth2) for subreddit search, post extraction, and comment retrieval. Free tier: 100 queries/min.

```python
# hyperion/tools/reddit.py — outline

class RedditClient:
    """Reddit community sentiment — free OAuth2 API.
    
    Provides access to:
      - Subreddit search (find relevant subreddits by keyword)
      - Post search (by keyword, subreddit, time range)
      - Comment extraction (full text of discussions)
      - Upvote ratio and comment count (popularity signals)
      - Sentiment aggregation (positive/negative/neutral)
    
    Used by: Consumer Insights, Market Analyst
    Rate limit: 100 req/min (free OAuth2)
    Dependencies: praw>=7.7.0 (Reddit API wrapper)
    Config: REDDIT_CLIENT_ID, REDDIT_CLIENT_SECRET in .env
    """
    
    async def search_subreddits(self, query: str, limit: int = 10) -> list[Subreddit]:
        """Find relevant subreddits by keyword."""
        ...
    
    async def search_posts(
        self, query: str, subreddit: str = "all",
        sort: str = "relevance", time_filter: str = "year",
        limit: int = 25,
    ) -> list[RedditPost]:
        """Search posts by keyword within a subreddit."""
        ...
    
    async def get_comments(self, post_id: str, limit: int = 50) -> list[RedditComment]:
        """Get top comments for a post."""
        ...
    
    async def get_sentiment(self, posts: list[RedditPost]) -> SentimentSummary:
        """Aggregate sentiment across posts and comments.
        
        Returns positive/negative/neutral counts and key themes.
        Uses keyword-based sentiment (no ML model needed).
        """
        ...
```

**Dependencies to add** in `pyproject.toml`:
```toml
"praw>=7.7.0",            # Reddit API wrapper
```

**Config to add** in `.env`:
```
HYPERION_REDDIT_CLIENT_ID=your_client_id
HYPERION_REDDIT_CLIENT_SECRET=your_client_secret
HYPERION_REDDIT_USER_AGENT=hyperion_research/1.0
```

**Register in ToolName**:
```python
REDDIT = "reddit"  # ← NEW: community sentiment
```

**Agent assignment**:
- `ConsumerInsights`: Add `REDDIT` (consumer sentiment, pain points, reviews)
- `MarketAnalyst`: Add `REDDIT` (market demand signals, consumer discussions)

---

### Step 2.8 — Register All Phase 2 Tools in _instantiate_tool

**File**: `hyperion/agents/base.py` (`_instantiate_tool` method)

Add all 7 new tool branches:

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

**File**: `hyperion/agents/sub_agent.py` (`_instantiate_tool` method)

Add the same 7 branches. Sub-agents need access to data source tools when their parent specialist has them.

---

### Step 2.9 — Update Sub-Agent Fallback Chain with Data Sources

**File**: `hyperion/agents/sub_agent.py` (`_gather_raw_data` method)

Add data source extraction blocks after the existing Wayback/Alpha Vantage/FRED blocks:

```python
# SEC filings — SEC EDGAR
if self._has_tool("sec_edgar"):
    try:
        edgar = self._get_tool("sec_edgar")
        filings = await edgar.search_full_text(self.spec.question, filing_type="10-K")
        if filings:
            formatted = "\n".join(f"- {f.company_name} {f.filing_type} ({f.date}): {f.url}" for f in filings[:5])
            raw_data.append(f"SEC filings:\n{formatted}")
    except Exception as e:
        errors.append(f"SEC EDGAR: {e!s:.80}")

# Academic papers — Semantic Scholar
if self._has_tool("semantic_scholar"):
    try:
        scholar = self._get_tool("semantic_scholar")
        papers = await scholar.search(self.spec.question, limit=10)
        if papers:
            formatted = "\n".join(f"- {p.title} ({p.year}): {p.abstract[:500]}" for p in papers[:5])
            raw_data.append(f"Academic papers:\n{formatted}")
    except Exception as e:
        errors.append(f"SemanticScholar: {e!s:.80}")

# Open scholarly catalog — OpenAlex
if self._has_tool("open_alex"):
    try:
        openalex = self._get_tool("open_alex")
        works = await openalex.search_works(self.spec.question, limit=10)
        if works:
            formatted = "\n".join(f"- {w.title} ({w.year}): cited {w.cited_by_count} times" for w in works[:5])
            raw_data.append(f"OpenAlex works:\n{formatted}")
    except Exception as e:
        errors.append(f"OpenAlex: {e!s:.80}")

# Global economic indicators — World Bank
if self._has_tool("world_bank"):
    try:
        wb = self._get_tool("world_bank")
        indicators = await wb.search_indicators(self.spec.question)
        if indicators:
            for ind in indicators[:3]:
                data = await wb.get_indicator(ind.code, date_range="2015:2025")
                if data:
                    formatted = "\n".join(f"  {d.country}: {d.value} ({d.year})" for d in data[:10])
                    raw_data.append(f"World Bank — {ind.name}:\n{formatted}")
    except Exception as e:
        errors.append(f"WorldBank: {e!s:.80}")

# Search interest trends — Google Trends
if self._has_tool("google_trends"):
    try:
        trends = self._get_tool("google_trends")
        interest = await trends.get_interest_over_time([self.spec.question[:50]], timeframe="today 12-m")
        if interest:
            raw_data.append(f"Google Trends interest (12 months):\n{interest}")
        related = await trends.get_related_queries(self.spec.question[:50], rising=True)
        if related:
            formatted = "\n".join(f"- {r.query} (+{r.growth}%)" for r in related[:10])
            raw_data.append(f"Related rising queries:\n{formatted}")
    except Exception as e:
        errors.append(f"GoogleTrends: {e!s:.80}")

# Tech community sentiment — HackerNews
if self._has_tool("hackernews"):
    try:
        hn = self._get_tool("hackernews")
        stories = await hn.search_stories(self.spec.question, hits=10)
        if stories:
            formatted = "\n".join(f"- {s.title} ({s.points} pts, {s.num_comments} comments): {s.url}" for s in stories[:5])
            raw_data.append(f"HackerNews stories:\n{formatted}")
    except Exception as e:
        errors.append(f"HackerNews: {e!s:.80}")

# Community sentiment — Reddit
if self._has_tool("reddit"):
    try:
        reddit = self._get_tool("reddit")
        posts = await reddit.search_posts(self.spec.question, sort="relevance", time_filter="year", limit=15)
        if posts:
            formatted = "\n".join(f"- {p.title} (r/{p.subreddit}, {p.upvote_ratio:.0%} upvoted, {p.num_comments} comments)" for p in posts[:10])
            raw_data.append(f"Reddit posts:\n{formatted}")
    except Exception as e:
        errors.append(f"Reddit: {e!s:.80}")
```

---

### Step 2.10 — Update ToolName Enum (Final State)

**File**: `hyperion/schemas/agents.py`

After both phases, the complete ToolName enum:

```python
class ToolName(str, Enum):
    """HYPERION's tool registry (§5.1)."""

    # ── Search + Extraction ──────────────────────────────────────────
    SEARXNG = "searxng"
    JINA = "jina"
    OBSCURA = "obscura"
    SCRAPLING = "scrapling"            # Phase 1: adaptive scraper
    CRAWL4AI = "crawl4ai"
    FLARESOLVERR = "flaresolverr"
    WAYBACK = "wayback"
    DEEP_SEARCH = "deep_search"        # Phase 1: unified orchestration

    # ── Data Sources ─────────────────────────────────────────────────
    ALPHA_VANTAGE = "alpha_vantage"
    FRED = "fred"
    SEC_EDGAR = "sec_edgar"            # Phase 2: SEC filings
    SEMANTIC_SCHOLAR = "semantic_scholar"  # Phase 2: academic papers
    OPEN_ALEX = "open_alex"            # Phase 2: open scholarly catalog
    WORLD_BANK = "world_bank"          # Phase 2: global economic indicators
    GOOGLE_TRENDS = "google_trends"    # Phase 2: search interest trends
    HACKERNEWS = "hackernews"          # Phase 2: tech community sentiment
    REDDIT = "reddit"                  # Phase 2: community sentiment

    # ── Report Generation ────────────────────────────────────────────
    UNSPLASH = "unsplash"
    SECOND_BRAIN = "second_brain"
    PLOTLY = "plotly"
    WEASYPRINT = "weasyprint"
    JINJA2 = "jinja2"
    PILLOW = "pillow"
```

**Total**: 21 tools (was 13, +8 new: Scrapling, DeepSearch, SEC EDGAR, Semantic Scholar, OpenAlex, World Bank, Google Trends, HackerNews, Reddit — wait, that's 9. Let me recount: 13 + Scrapling + DeepSearch + SEC_EDGAR + Semantic_Scholar + Open_Alex + World_Bank + Google_Trends + HackerNews + Reddit = 13 + 9 = 22 tools).

**Update test** in `tests/test_tools.py`:
```python
def test_tool_count(self):
    assert len(list(ToolName)) == 22  # 13 original + 9 new
```

---

### Step 2.11 — Update Dependencies

**File**: `pyproject.toml`

Add to `dependencies`:
```toml
# Phase 1: VIGIL integration
"scrapling>=0.4.11",      # Adaptive web scraping with anti-bot

# Phase 2: Data source expansion
"pytrends>=4.7.3",       # Google Trends unofficial API
"praw>=7.7.0",            # Reddit API wrapper
```

Note: SEC EDGAR, Semantic Scholar, OpenAlex, World Bank, and HackerNews all use `httpx` (already a dependency) — no new packages needed.

---

### Step 2.12 — Update Agent Specs (Complete Tool Assignment Matrix)

After both phases, the complete tool assignment per agent:

| Agent | Tools (new in **bold**) |
|---|---|
| Engagement Director | All tools (read-only) |
| Synthesis Lead | Second Brain |
| Market Analyst | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, Alpha Vantage, FRED, **World Bank**, **Google Trends**, **Semantic Scholar**, **Reddit** |
| Competitive Intel | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, Wayback, **SEC EDGAR** |
| Financial Analyst | Alpha Vantage, FRED, SearxNG, Jina, **DeepSearch**, **SEC EDGAR**, **World Bank** |
| Risk Analyst | SearxNG, Jina, Obscura, **DeepSearch** |
| Technology Analyst | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, **Semantic Scholar**, **HackerNews** |
| Operations Analyst | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch** |
| Regulatory Analyst | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, Wayback |
| Sustainability Analyst | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, FRED, **World Bank** |
| Consumer Insights | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, **Google Trends**, **HackerNews**, **Reddit** |
| M&A Analyst | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, Alpha Vantage, **SEC EDGAR** |
| Innovation Analyst | SearxNG, Jina, Obscura, **DeepSearch**, Wayback, **Semantic Scholar**, **OpenAlex**, **Google Trends**, **HackerNews** |
| Strategy Analyst | SearxNG, Jina, Obscura, **DeepSearch** |
| Research Librarian | Second Brain, **OpenAlex** |
| Fact Checker | SearxNG, Jina, Obscura, **Scrapling**, **DeepSearch**, Crawl4AI |
| Data Visualizer | Plotly, Unsplash, Pillow |
| Quality Gate | All outputs (read-only) |
| Presentation Designer | Unsplash, Plotly, Jinja2, WeasyPrint |
| Render Engine | WeasyPrint, Pillow |

**Rule**: No agent gets a tool it doesn't use. Every tool is assigned to agents who actually need it. This follows ARCHITECTURE.md §5.1: "No decorative tools. No tool is assigned to an agent that doesn't need it."

---

### Step 2.13 — Update ARCHITECTURE.md (Final)

**File**: `hyperion/ARCHITECTURE.md`

Update the following sections:

**§5.1 Core Tools table**: Add all 9 new tools with descriptions.

**§5.2 Tool Selection Logic**: Add new data source selection logic:
```
Academic research task:
  1. Semantic Scholar (paper search + TLDRs)
  2. OpenAlex (broader catalog + institution data)

SEC filings task:
  1. SEC EDGAR (always — it's the only source for SEC filings)

Global macro task:
  1. FRED (US economic data)
  2. World Bank (international economic data)

Market trends task:
  1. Google Trends (search interest over time)
  2. HackerNews (tech community sentiment)
  3. Reddit (broader community sentiment)

Unified search task:
  1. DeepSearch (wraps SearxNG + Jina + Obscura + Scrapling + scoring)
```

**§11 Dependencies**: Add all new dependencies.

**§4.4 Agent tool lists**: Update each agent's documented tool list.

---

## Testing Plan

### Phase 1 Tests

**New test file**: `tests/test_scrapling.py`
```python
class TestScraplingClient:
    async def test_fetch_single_url(self):
        """Scrapling can fetch a single URL and return content."""
        ...
    
    async def test_fetch_stealth_mode(self):
        """StealthyFetcher bypasses basic bot detection."""
        ...
    
    async def test_scrape_batch(self):
        """Batch scraping returns results for multiple URLs."""
        ...
    
    async def test_adaptive_selectors(self):
        """Scrapling recovers when page structure changes."""
        ...
```

**New test file**: `tests/test_deep_search.py`
```python
class TestDeepSearchClient:
    async def test_search_quick(self):
        """Quick depth returns 3 ranked results."""
        ...
    
    async def test_search_standard(self):
        """Standard depth returns 6 ranked results."""
        ...
    
    async def test_parallel_discovery(self):
        """SearxNG and Jina Search run in parallel."""
        ...
    
    async def test_evidence_scoring(self):
        """Results are scored with support/conflict/neutral."""
        ...
    
    async def test_caching(self):
        """Repeated queries return cached results."""
        ...
```

**Update**: `tests/test_tools.py` — expect 22 tools.

### Phase 2 Tests

**New test files**: `tests/test_sec_edgar.py`, `tests/test_semantic_scholar.py`, `tests/test_openalex.py`, `tests/test_world_bank.py`, `tests/test_google_trends.py`, `tests/test_hackernews.py`, `tests/test_reddit.py`

Each test file follows the same pattern:
```python
class Test[ToolName]Client:
    async def test_search_basic(self):
        """Basic search returns results."""
        ...
    
    async def test_rate_limiting(self):
        """Client respects rate limits."""
        ...
    
    async def test_error_handling(self):
        """Client handles API errors gracefully."""
        ...
```

---

## Implementation Order

### Phase 1 (VIGIL Integration) — Do first, ~6-8 hours

1. **Step 1.1**: Create `scrapling.py` tool file
2. **Step 1.2**: Add `SCRAPLING` to `ToolName` enum
3. **Step 1.3**: Wire Scrapling into `_instantiate_tool` (base.py + sub_agent.py)
4. **Step 1.4**: Add Scrapling to 8 specialist agent specs + Fact Checker
5. **Step 1.5**: Rewrite sub-agent fallback chain (parallel discovery + Scrapling)
6. **Step 1.6**: Increase content truncation from 8000→15000 in all 13 agent files
7. **Step 1.7**: Create `deep_search.py` orchestration tool
8. **Step 1.8**: Create `evidence_scorer.py` heuristic scorer
9. **Step 1.9**: Update ARCHITECTURE.md
10. **Step 1.10**: Document Scrapling/Playwright install requirement
11. Run tests, verify all agents still function

### Phase 2 (Data Source Expansion) — Do second, ~10-12 hours

1. **Step 2.1**: Create `sec_edgar.py` + register in enum + wire in _instantiate_tool
2. **Step 2.2**: Create `semantic_scholar.py` + register + wire
3. **Step 2.3**: Create `openalex.py` + register + wire
4. **Step 2.4**: Create `world_bank.py` + register + wire
5. **Step 2.5**: Create `google_trends.py` + register + wire + add pytrends dependency
6. **Step 2.6**: Create `hackernews.py` + register + wire
7. **Step 2.7**: Create `reddit.py` + register + wire + add praw dependency + .env config
8. **Step 2.8**: Register all 7 tools in both `_instantiate_tool` methods
9. **Step 2.9**: Add data source blocks to sub-agent `_gather_raw_data`
10. **Step 2.10**: Finalize ToolName enum (22 tools)
11. **Step 2.11**: Update pyproject.toml dependencies
12. **Step 2.12**: Update all agent specs with new tool assignments
13. **Step 2.13**: Update ARCHITECTURE.md
14. Run tests, verify all tools function

---

## Risk Assessment

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| Scrapling Playwright install fails on Windows | Medium | High | Document `scrapling install` step; provide Docker fallback |
| Google Trends throttling (pytrends is unofficial) | High | Medium | Cache aggressively; 100 req/hr limit; fall back to SearxNG |
| Reddit OAuth2 setup complexity | Low | Low | Document .env setup; Reddit API approval is automatic |
| SEC EDGAR rate limiting | Low | Low | 10 req/sec is generous; add 100ms delay between calls |
| Content truncation increase causes context overflow | Low | Medium | 15000 chars × 6 sources = 90K chars ≈ 22.5K tokens; fits in 128K context |
| Sub-agent fallback chain too slow (more tiers) | Medium | Medium | Each tier has early exit if URL already extracted; parallel discovery saves time |
| DeepSearch caching causes stale results | Low | Low | 1-hour TTL; agents can bypass cache with `force_refresh=True` |

---

## What This Plan Does NOT Do

- **No pgvector** — Hyperion has no PostgreSQL. Evidence scoring is heuristic, not semantic.
- **No Redis** — Hyperion has no Redis. Caching is in-memory dict (existing pattern from SecondBrain).
- **No Ollama / local embeddings** — 2GB+ infrastructure for marginal benefit. Deferred indefinitely.
- **No Docker Compose changes** — All new tools run in-process (Python libraries) or use external free APIs.
- **No new LLM providers** — The 4 existing providers (Google, NVIDIA, Cerebras, Groq) are sufficient.
- **No agent roster changes** — Same 20 agents. Only their tool lists and the sub-agent fallback chain change.
- **No report structure changes** — Same PDF pipeline (WeasyPrint + Jinja2 + Plotly + Pillow).
- **No TUI changes** — Same Textual/Rich TUI. New tools appear in agent tool lists automatically.

---

## Success Metrics

After implementation:

| Metric | Before | After | Improvement |
|---|---|---|---|
| Tools in registry | 13 | 22 | +69% |
| Extraction fallback tiers | 4 (Jina→Obscura→Crawl4AI→FlareSolverr) | 5 (Obscura→Scrapling→Jina→Crawl4AI→FlareSolverr) | +25% |
| Content per source | 8000 chars | 15000 chars | +87.5% |
| Discovery mode | Sequential (SearxNG only) | Parallel (SearxNG + Jina Search) | ~2x faster |
| Data source types | 3 (financial, macro, archive) | 10 (+SEC, academic×2, global macro, trends, sentiment×2, unified search) | +233% |
| Evidence scoring | None | Heuristic (support/conflict/neutral) | New capability |
| Search orchestration | Manual per-tool calls | `deep_search()` unified API | Simplified agent code |
| Monthly cost | $0 | $0 | Maintained |
