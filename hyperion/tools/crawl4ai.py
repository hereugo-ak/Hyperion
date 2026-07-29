"""
HYPERION Crawl4AI Client — heavy page extraction fallback.

Crawl4AI is the fallback when Obscura is unavailable or fails. It
handles:
- Heavy page extraction (long articles, multi-page documents)
- PDF extraction (extracting text from PDF files)
- Complex document parsing (tables, nested structures)
- Transformers patch required for full functionality

This is NOT a generic "crawl a website" wrapper. It:
- Uses Crawl4AI's async crawler with configurable strategies
- Supports markdown and structured data output
- Handles PDF text extraction with layout preservation
- Falls back gracefully when transformers are not available
- Returns structured results with metadata (title, content, links, tables)

Architecture reference: §5.1 — "Heavy page extraction. Fallback when
Obscura unavailable. Transformers patch required. Used for PDF extraction
and complex document parsing."

Tool selection logic (§5.2):
  Extract task:
    1. Jina Reader (fast, simple extraction)
    2. Obscura (if JS rendering required)
    3. Crawl4AI (if Obscura fails — heavy extraction, PDFs) ← THIS
    4. Wayback (if the page is down or has changed)

Extraction fallback chain (§5.3):
  Obscura (stealth, JS rendering)
    → Crawl4AI (heavy extraction, PDFs) ← THIS (second option)
      → Jina Reader (fast, simple extraction)
        → Wayback (if page is down or changed)
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, field
from typing import Any

import httpx


@dataclass
class CrawlResult:
    """Result of a Crawl4AI extraction."""

    url: str
    title: str = ""
    content: str = ""
    markdown: str = ""
    html: str = ""
    links: list[dict[str, str]] = field(default_factory=list)
    tables: list[dict[str, Any]] = field(default_factory=list)
    images: list[dict[str, str]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    status_code: int = 0
    error: str = ""
    extraction_method: str = "crawl4ai"

    # Raw downloaded payload for PDF crawls. ``crawl_pdf`` already downloads
    # the full document to extract its text; carrying the bytes lets
    # downstream table extraction (fix 2.3, pdfplumber) run on the SAME
    # document without a second network fetch. Internal hand-back only —
    # deliberately excluded from ``to_dict()`` like ``UnifiedExtractResult.raw``.
    pdf_bytes: bytes | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "html": self.html,
            "links": self.links,
            "tables": self.tables,
            "images": self.images,
            "metadata": self.metadata,
            "status_code": self.status_code,
            "error": self.error,
            "extraction_method": self.extraction_method,
        }


class Crawl4AIClient:
    """Crawl4AI deep extraction client.

    Heavy page extraction fallback when Obscura fails. Handles PDFs,
    complex documents, tables, and multi-page content.
    (§5.1)

    Usage:
        client = Crawl4AIClient(settings=settings)
        result = await client.crawl("https://example.com/long-report")
        print(result.markdown[:500])

        # PDF extraction
        pdf_result = await client.crawl_pdf("https://example.com/report.pdf")
        print(pdf_result.content[:500])
    """

    REQUEST_TIMEOUT = 120  # Crawl4AI can be slow on heavy pages
    MAX_RETRIES = 2
    RETRY_DELAY = 3

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._client: httpx.AsyncClient | None = None
        self._transformers_available: bool | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(self.REQUEST_TIMEOUT),
                follow_redirects=True,
            )
        return self._client

    def _check_transformers(self) -> bool:
        """Check if transformers library is available (needed for full Crawl4AI)."""
        if self._transformers_available is not None:
            return self._transformers_available
        try:
            import transformers  # noqa: F401
            self._transformers_available = True
        except ImportError:
            self._transformers_available = False
        return self._transformers_available

    async def crawl(
        self,
        url: str,
        output_format: str = "markdown",
        extract_tables: bool = True,
        extract_links: bool = True,
        extract_images: bool = False,
    ) -> CrawlResult:
        """Crawl a URL and extract content via Crawl4AI.

        Args:
            url: URL to crawl
            output_format: Output format (markdown, html, text)
            extract_tables: Whether to extract tables as structured data
            extract_links: Whether to extract all links
            extract_images: Whether to extract image URLs

        Returns:
            CrawlResult with the extracted content and metadata.
        """
        # Try using Crawl4AI library directly
        try:
            from crawl4ai import AsyncWebCrawler

            async with AsyncWebCrawler(verbose=False) as crawler:
                result = await crawler.arun(
                    url=url,
                    bypass_cache=True,
                    word_count_threshold=10,
                    output_format=output_format,
                )

                tables: list[dict[str, Any]] = []
                if extract_tables and hasattr(result, "tables"):
                    tables = result.tables or []

                links: list[dict[str, str]] = []
                if extract_links and hasattr(result, "links"):
                    links = result.links or []

                images: list[dict[str, str]] = []
                if extract_images and hasattr(result, "media"):
                    images = result.media or []

                return CrawlResult(
                    url=url,
                    title=getattr(result, "title", "") or "",
                    content=getattr(result, "markdown", "") or getattr(result, "content", ""),
                    markdown=getattr(result, "markdown", "") or "",
                    html=getattr(result, "html", "") or "",
                    links=links,
                    tables=tables,
                    images=images,
                    metadata=getattr(result, "metadata", {}) or {},
                    status_code=200,
                    extraction_method="crawl4ai",
                )

        except ImportError:
            # Crawl4AI not installed — fall back to httpx + basic extraction
            return await self._fallback_extract(url, output_format, extract_tables, extract_links)

        except (TimeoutError, RuntimeError, ValueError) as e:
            # Crawl4AI failed — try fallback
            fallback = await self._fallback_extract(url, output_format, extract_tables, extract_links)
            if fallback.error:
                fallback.error = f"Crawl4AI error: {e}; Fallback error: {fallback.error}"
            fallback.extraction_method = "fallback"
            return fallback

    async def _fallback_extract(
        self,
        url: str,
        output_format: str,
        extract_tables: bool,
        extract_links: bool,
    ) -> CrawlResult:
        """Fallback extraction using httpx + basic HTML parsing.

        This is used when Crawl4AI is not installed or fails.
        It's not as good as Crawl4AI but it's better than nothing.
        """
        client = await self._get_client()

        for attempt in range(self.MAX_RETRIES):
            try:
                response = await client.get(url)
                response.raise_for_status()

                html = response.text
                title = ""

                # Extract title
                if "<title>" in html:
                    start = html.find("<title>") + 7
                    end = html.find("</title>", start)
                    title = html[start:end].strip()

                # Basic HTML to markdown conversion
                content = self._html_to_markdown(html)

                # Extract links
                links: list[dict[str, str]] = []
                if extract_links:
                    links = self._extract_links(html, url)

                # Extract tables
                tables: list[dict[str, Any]] = []
                if extract_tables:
                    tables = self._extract_tables(html)

                return CrawlResult(
                    url=url,
                    title=title,
                    content=content,
                    markdown=content,
                    html=html,
                    links=links,
                    tables=tables,
                    metadata={"method": "fallback"},
                    status_code=response.status_code,
                    extraction_method="fallback",
                )

            except (httpx.HTTPError, httpx.RequestError) as e:
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY)
                return CrawlResult(
                    url=url,
                    error=f"Fallback extraction failed: {e}",
                    status_code=0,
                    extraction_method="fallback",
                )

        return CrawlResult(url=url, error="All retries exhausted", extraction_method="fallback")

    def _html_to_markdown(self, html: str) -> str:
        """Basic HTML to markdown conversion.

        Not as sophisticated as Crawl4AI but handles common cases:
        - Headers (h1-h6) → # ## ###
        - Paragraphs → plain text
        - Links → [text](url)
        - Lists → - item
        - Bold/italic → **bold** / *italic*
        - Code blocks → ```code```
        """
        # Remove script and style tags
        import re

        # Remove scripts and styles
        html = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<style[^>]*>.*?</style>", "", html, flags=re.DOTALL | re.IGNORECASE)

        # Headers
        for i in range(6, 0, -1):
            html = re.sub(
                rf"<h{i}[^>]*>(.*?)</h{i}>",
                lambda m, level=i: "\n" + "#" * level + " " + m.group(1).strip() + "\n",
                html,
                flags=re.DOTALL | re.IGNORECASE,
            )

        # Bold and italic
        html = re.sub(r"<b[^>]*>(.*?)</b>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<strong[^>]*>(.*?)</strong>", r"**\1**", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<i[^>]*>(.*?)</i>", r"*\1*", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<em[^>]*>(.*?)</em>", r"*\1*", html, flags=re.DOTALL | re.IGNORECASE)

        # Links
        html = re.sub(
            r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>",
            r"[\2](\1)",
            html,
            flags=re.DOTALL | re.IGNORECASE,
        )

        # List items
        html = re.sub(r"<li[^>]*>(.*?)</li>", r"- \1\n", html, flags=re.DOTALL | re.IGNORECASE)

        # Paragraphs and breaks
        html = re.sub(r"<p[^>]*>(.*?)</p>", r"\1\n\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<br\s*/?>", "\n", html, flags=re.IGNORECASE)

        # Code blocks
        html = re.sub(r"<pre[^>]*>(.*?)</pre>", lambda m: "\n```\n" + m.group(1).strip() + "\n```\n", html, flags=re.DOTALL | re.IGNORECASE)
        html = re.sub(r"<code[^>]*>(.*?)</code>", r"`\1`", html, flags=re.DOTALL | re.IGNORECASE)

        # Remove all remaining HTML tags
        html = re.sub(r"<[^>]+>", "", html)

        # Clean up whitespace
        html = re.sub(r"\n{3,}", "\n\n", html)
        html = re.sub(r"  +", " ", html)

        return html.strip()

    def _extract_links(self, html: str, base_url: str) -> list[dict[str, str]]:
        """Extract all links from HTML."""
        import re

        links: list[dict[str, str]] = []
        pattern = r'<a[^>]+href=["\']([^"\']+)["\'][^>]*>(.*?)</a>'
        for match in re.finditer(pattern, html, re.DOTALL | re.IGNORECASE):
            href = match.group(1)
            text = re.sub(r"<[^>]+>", "", match.group(2)).strip()
            if href and not href.startswith("#") and not href.startswith("javascript:"):
                links.append({"url": href, "text": text})
        return links

    def _extract_tables(self, html: str) -> list[dict[str, Any]]:
        """Extract tables from HTML as structured data."""
        import re

        tables: list[dict[str, Any]] = []
        table_pattern = re.compile(r"<table[^>]*>(.*?)</table>", re.DOTALL | re.IGNORECASE)

        for table_match in table_pattern.finditer(html):
            table_html = table_match.group(1)

            # Extract headers
            headers: list[str] = []
            header_match = re.search(r"<thead[^>]*>(.*?)</thead>", table_html, re.DOTALL | re.IGNORECASE)
            if header_match:
                for th in re.finditer(r"<th[^>]*>(.*?)</th>", header_match.group(1), re.DOTALL | re.IGNORECASE):
                    headers.append(re.sub(r"<[^>]+>", "", th.group(1)).strip())

            # Extract rows
            rows: list[list[str]] = []
            for tr in re.finditer(r"<tr[^>]*>(.*?)</tr>", table_html, re.DOTALL | re.IGNORECASE):
                cells: list[str] = []
                for td in re.finditer(r"<t[dh][^>]*>(.*?)</t[dh]>", tr.group(1), re.DOTALL | re.IGNORECASE):
                    cells.append(re.sub(r"<[^>]+>", "", td.group(1)).strip())
                if cells:
                    rows.append(cells)

            if rows:
                tables.append({
                    "headers": headers,
                    "rows": rows,
                })

        return tables

    async def crawl_pdf(self, url: str) -> CrawlResult:
        """Extract text from a PDF file.

        Downloads the PDF and extracts text with layout preservation.
        Used for extracting content from PDF reports, whitepapers, and
        regulatory documents.
        """
        client = await self._get_client()

        try:
            response = await client.get(url)
            response.raise_for_status()

            # Save to temp file
            temp_path = os.path.join("output", ".crawl4ai_temp.pdf")
            with open(temp_path, "wb") as f:
                f.write(response.content)

            pdf_bytes = response.content

            # Try PyMuPDF first (best quality)
            try:
                import fitz

                doc = fitz.open(temp_path)
                text_parts: list[str] = []
                for page in doc:
                    text_parts.append(page.get_text())
                doc.close()

                content = "\n\n".join(text_parts)
                os.unlink(temp_path)

                return CrawlResult(
                    url=url,
                    title=url.split("/")[-1],
                    content=content,
                    markdown=content,
                    status_code=200,
                    extraction_method="pymupdf",
                    metadata={"pages": len(text_parts)},
                    pdf_bytes=pdf_bytes,
                )

            except ImportError:
                pass

            # Try PyPDF2
            try:
                from PyPDF2 import PdfReader

                reader = PdfReader(temp_path)
                text_parts = []
                for page in reader.pages:
                    text_parts.append(page.extract_text())

                content = "\n\n".join(text_parts)
                os.unlink(temp_path)

                return CrawlResult(
                    url=url,
                    title=url.split("/")[-1],
                    content=content,
                    markdown=content,
                    status_code=200,
                    extraction_method="pypdf2",
                    metadata={"pages": len(text_parts)},
                    pdf_bytes=pdf_bytes,
                )

            except ImportError:
                pass

            # No PDF library available
            os.unlink(temp_path)
            return CrawlResult(
                url=url,
                error="No PDF extraction library available (install PyMuPDF or PyPDF2)",
                status_code=200,
            )

        except (httpx.HTTPError, httpx.RequestError, OSError) as e:
            return CrawlResult(url=url, error=str(e), status_code=0)

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> Crawl4AIClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
