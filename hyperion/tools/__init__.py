"""Tool registry — SearxNG, Jina, Obscura, Crawl4AI, Wayback, Alpha Vantage, FRED, Unsplash, Second Brain, Unified Search, Unified Extract, DeepSearch, EvidenceScorer.

Every tool is assigned to agents who actually use it. No decorative tools.
No tool is assigned to an agent that doesn't need it. No agent lacks a tool
it does need. This is deliberate — tool assignment is a design decision.
(§5.1)
"""

from hyperion.tools.alpha_vantage import (
    AlphaVantageClient,
    FundamentalData,
    StockQuote,
    TimeSeriesData,
)
from hyperion.tools.camoufox_client import CamoufoxClient, CamoufoxResult
from hyperion.tools.content_selector import (
    Chunk,
    SelectionResult,
    chunk_content,
    rerank_chunks,
    select_content,
    select_relevant_content,
)
from hyperion.tools.crawl4ai import Crawl4AIClient, CrawlResult
from hyperion.tools.curl_cffi_client import CurlCffiClient, CurlCffiResult
from hyperion.tools.deep_search import (
    DeepSearchClient,
    DeepSearchResult,
    ExtractedContent,
    YieldMetrics,
    engagement_yield_report,
    reset_engagement_yield,
)
from hyperion.tools.evidence_scorer import EvidenceScorer, EvidenceSummary, ScoredResult
from hyperion.tools.flaresolverr import FlareBreaker, FlareSolverrClient, FlareSolverrResult
from hyperion.tools.fred import FredClient, FREDSearchResult, FREDSeries
from hyperion.tools.google_trends import GoogleTrendsClient, RelatedQuery, RelatedTopic, TrendResult
from hyperion.tools.hackernews import HackerNewsClient, HNComment, HNStory
from hyperion.tools.http_extract import HttpExtractClient, HttpExtractResult
from hyperion.tools.jina import JinaClient, JinaReadResult, JinaSearchResponse, JinaSearchResult
from hyperion.tools.market_data import MarketDataClient, MarketQuote, PriceBar, PriceHistory
from hyperion.tools.nodriver_client import NodriverClient, NodriverResult
from hyperion.tools.obscura import ObscuraClient, ObscuraFetchResult, ObscuraScrapeResult
from hyperion.tools.openalex import OpenAlexClient, OpenAlexInstitution, OpenAlexWork
from hyperion.tools.query_utils import normalize_query
from hyperion.tools.reddit import (
    RedditClient,
    RedditComment,
    RedditPost,
    SentimentSummary,
    Subreddit,
)
from hyperion.tools.scrapling import ScraplingBatchResult, ScraplingClient, ScraplingResult
from hyperion.tools.sdmx import (
    EurostatClient,
    IMFClient,
    OECDClient,
    SDMXDataflow,
    SDMXPoint,
    SDMXSeries,
)
from hyperion.tools.search_budget import SearchBudget
from hyperion.tools.searxng import SearchResponse, SearchResult, SearxNGClient

# ── Phase 2 Data Sources ──
from hyperion.tools.sec_edgar import SECCompanyInfo, SECEdgarClient, SECFiling, SECFilingContent
from hyperion.tools.second_brain import SecondBrainClient, VaultNote, VaultSearchResult
from hyperion.tools.semantic_scholar import AcademicPaper, CitationGraph, SemanticScholarClient
from hyperion.tools.stealth_search import StealthSearchClient, StealthSearchResult
from hyperion.tools.unified_extract import UnifiedExtract, UnifiedExtractResult
from hyperion.tools.unified_search import UnifiedSearch, UnifiedSearchResult
from hyperion.tools.unsplash import UnsplashClient, UnsplashImage, UnsplashSearchResult
from hyperion.tools.vector_brain import VectorHit, VectorStore, backend_name, embed
from hyperion.tools.wayback import (
    WaybackClient,
    WaybackContentResult,
    WaybackSnapshot,
    WaybackTimelineResult,
)
from hyperion.tools.world_bank import (
    WorldBankClient,
    WorldBankCountryProfile,
    WorldBankIndicator,
    WorldBankIndicatorData,
)

__all__ = [
    # SearxNG
    "SearxNGClient",
    "SearchResult",
    "SearchResponse",
    # Jina
    "JinaClient",
    "JinaSearchResult",
    "JinaSearchResponse",
    "JinaReadResult",
    # HTTP Extract
    "HttpExtractClient",
    "HttpExtractResult",
    # Obscura
    "ObscuraClient",
    "ObscuraFetchResult",
    "ObscuraScrapeResult",
    # Scrapling
    "ScraplingClient",
    "ScraplingResult",
    "ScraplingBatchResult",
    # Crawl4AI
    "Crawl4AIClient",
    "CrawlResult",
    # FlareSolverr
    "FlareSolverrClient",
    "FlareSolverrResult",
    "FlareBreaker",
    # P12: Stealth Extraction
    "CurlCffiClient",
    "CurlCffiResult",
    "NodriverClient",
    "NodriverResult",
    "CamoufoxClient",
    "CamoufoxResult",
    # Search Budget + Query Hygiene
    "SearchBudget",
    "normalize_query",
    # Stealth Search
    "StealthSearchClient",
    "StealthSearchResult",
    # Wayback
    "WaybackClient",
    "WaybackSnapshot",
    "WaybackTimelineResult",
    "WaybackContentResult",
    # Alpha Vantage
    "AlphaVantageClient",
    "StockQuote",
    "TimeSeriesData",
    "FundamentalData",
    # FRED
    "FredClient",
    "FREDSeries",
    "FREDSearchResult",
    # Unsplash
    "UnsplashClient",
    "UnsplashImage",
    "UnsplashSearchResult",
    # Second Brain
    # Vector Brain (5.4)
    "VectorHit",
    "VectorStore",
    "backend_name",
    "embed",
    "SecondBrainClient",
    "VaultNote",
    "VaultSearchResult",
    # Unified Search
    "UnifiedSearch",
    "UnifiedSearchResult",
    # Unified Extract
    "UnifiedExtract",
    "UnifiedExtractResult",
    # Content Selector (fix 2.2 — chunk → rerank → top-k)
    "Chunk",
    "SelectionResult",
    "chunk_content",
    "rerank_chunks",
    "select_content",
    "select_relevant_content",
    # DeepSearch
    "DeepSearchClient",
    "DeepSearchResult",
    "ExtractedContent",
    "YieldMetrics",
    "engagement_yield_report",
    "reset_engagement_yield",
    # EvidenceScorer
    "EvidenceScorer",
    "ScoredResult",
    "EvidenceSummary",
    # ── Phase 2 Data Sources ──
    # SEC EDGAR
    "SECEdgarClient",
    "SECFiling",
    "SECFilingContent",
    "SECCompanyInfo",
    # Semantic Scholar
    "SemanticScholarClient",
    "AcademicPaper",
    "CitationGraph",
    # OpenAlex
    "OpenAlexClient",
    "OpenAlexWork",
    "OpenAlexInstitution",
    # World Bank
    "WorldBankClient",
    "WorldBankIndicator",
    "WorldBankIndicatorData",
    "WorldBankCountryProfile",
    # SDMX (5.5 — OECD / Eurostat / IMF: breaks the FRED US-only ceiling)
    "OECDClient",
    "EurostatClient",
    "IMFClient",
    "SDMXDataflow",
    "SDMXPoint",
    "SDMXSeries",
    # Market Data (5.5 — yfinance global equities)
    "MarketDataClient",
    "MarketQuote",
    "PriceBar",
    "PriceHistory",
    # Google Trends
    "GoogleTrendsClient",
    "TrendResult",
    "RelatedQuery",
    "RelatedTopic",
    # HackerNews
    "HackerNewsClient",
    "HNStory",
    "HNComment",
    # Reddit
    "RedditClient",
    "RedditPost",
    "RedditComment",
    "SentimentSummary",
    "Subreddit",
]
