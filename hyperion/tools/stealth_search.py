"""HYPERION — Stealth Google Search via Playwright.

Uses Playwright with stealth plugin to search Google as a real Chromium browser.
Bypasses bot detection that blocks SearxNG and FlareSolverr.

Strategy:
1. Launch Chromium with realistic fingerprint (stealth plugin)
2. Navigate to Google search URL
3. Wait for results to render
4. Parse DOM for result links, titles, snippets
5. Return structured search results

Used as the final fallback in the search chain:
  SearxNG → Jina → FlareSolverr → Stealth Google

§5.1 — Tool Registry: STEALTH_SEARCH
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class StealthSearchResult:
    """A single search result from stealth Google search."""
    title: str = ""
    url: str = ""
    snippet: str = ""
    engine: str = "stealth_google"


class StealthSearchClient:
    """Google search via Playwright with stealth plugin.

    Launches a real Chromium browser with anti-detection patches,
    navigates to Google, and parses the results from the DOM.

    Usage:
        client = StealthSearchClient()
        results = await client.search("india ai market size", 10)
        for r in results:
            print(f"{r.title} | {r.url}")
    """

    # Realistic user agents (Chrome 120-130 range)
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    ]

    # Search URLs — DuckDuckGo HTML is lighter and less aggressive on bot detection
    DDG_URL = "https://html.duckduckgo.com/html/?q={query}"
    BING_URL = "https://www.bing.com/search?q={query}&count={num}"
    GOOGLE_LITE_URL = "https://www.google.com/search?q={query}&num={num}&hl=en&lite=1"

    def __init__(self, headless: bool = False, settings: Any = None) -> None:
        self.headless = headless
        self._browser = None
        self._playwright = None

        # P8 GAP-3: Stealth Layer 3 — config-gated proxy/UA rotation
        self._proxy_enabled = False
        self._proxy_url = ""
        self._ua_rotation = False
        if settings:
            self._proxy_enabled = getattr(settings, "stealth_proxy_enabled", False)
            self._proxy_url = getattr(settings, "stealth_proxy_url", "")
            self._ua_rotation = getattr(settings, "stealth_ua_rotation", False)

    async def _launch_browser(self):
        """Launch Chromium with stealth patches."""
        from playwright.async_api import async_playwright

        self._playwright = await async_playwright().start()

        # Try to apply stealth patches
        try:
            from playwright_stealth import stealth_async
            self._stealth_async = stealth_async
        except ImportError:
            self._stealth_async = None

        ua = random.choice(self.USER_AGENTS)

        launch_kwargs: dict[str, Any] = {
            "headless": self.headless,
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1920,1080",
            ],
        }

        # P8 GAP-3: Config-gated proxy support
        if self._proxy_enabled and self._proxy_url:
            launch_kwargs["proxy"] = {"server": self._proxy_url}
            logger.info("Stealth search: using proxy %s", self._proxy_url[:30] + "...")

        self._browser = await self._playwright.chromium.launch(**launch_kwargs)

        context = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        # Apply stealth to the context
        if self._stealth_async:
            page = await context.new_page()
            await self._stealth_async(page)
        else:
            page = await context.new_page()

        # Remove webdriver property
        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = { runtime: {} };
        """)

        return page

    async def _new_page(self):
        """Create a new page on the existing browser with stealth patches."""
        if not self._browser:
            return await self._launch_browser()

        ua = random.choice(self.USER_AGENTS)
        context = await self._browser.new_context(
            user_agent=ua,
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
            timezone_id="America/New_York",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )

        page = await context.new_page()

        if self._stealth_async:
            await self._stealth_async(page)

        await page.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
            Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
            window.chrome = { runtime: {} };
        """)

        return page

    async def search(self, query: str, num_results: int = 10) -> list[StealthSearchResult]:
        """Search via stealth Chromium browser.

        Tries DuckDuckGo HTML first (lightest, least bot detection),
        then Bing, then Google lite as last resort.

        Args:
            query: Search query string
            num_results: Maximum number of results to return

        Returns:
            List of StealthSearchResult with title, url, snippet.
        """
        page = None
        try:
            # Strategy 1: DuckDuckGo HTML (lightest, most reliable with stealth)
            page = await self._launch_browser()
            results = await self._search_ddg(page, query, num_results)
            if results:
                return results

            # Strategy 2: Bing (needs fresh page to avoid context issues)
            await page.close()
            page = await self._new_page()
            results = await self._search_bing(page, query, num_results)
            if results:
                return results

            # Strategy 3: Google lite (last resort — aggressive bot detection)
            await page.close()
            page = await self._new_page()
            results = await self._search_google(page, query, num_results)
            if results:
                return results

            return []

        except Exception as e:
            logger.warning("Stealth search failed: %s", e)
            return []

        finally:
            await self.close()

    async def _search_ddg(self, page, query: str, num_results: int) -> list[StealthSearchResult]:
        """Search DuckDuckGo HTML via stealth browser."""
        try:
            url = self.DDG_URL.format(query=query.replace(" ", "+"))
            await page.goto(url, wait_until="networkidle", timeout=30000)
            await asyncio.sleep(random.uniform(1.5, 3.0))

            content = await page.content()
            if "anomalies" in content.lower() and "blocked" in content.lower():
                logger.warning("Stealth DDG: blocked")
                return []

            return await self._parse_ddg_dom(page, num_results)
        except Exception as e:
            logger.warning("Stealth DDG failed: %s", e)
            return []

    async def _search_bing(self, page, query: str, num_results: int) -> list[StealthSearchResult]:
        """Search Bing via stealth browser."""
        try:
            url = self.BING_URL.format(query=query.replace(" ", "+"), num=min(num_results, 20))
            await page.goto(url, wait_until="load", timeout=30000)

            # Wait for Bing to render results via JS
            try:
                await page.wait_for_selector("li.b_algo, .b_algo", timeout=10000)
            except Exception:
                # Fallback: just wait and try
                await asyncio.sleep(5)

            return await self._parse_bing_dom(page, num_results)
        except Exception as e:
            logger.warning("Stealth Bing failed: %s", e)
            return []

    async def _search_google(self, page, query: str, num_results: int) -> list[StealthSearchResult]:
        """Search Google lite via stealth browser (last resort)."""
        try:
            url = self.GOOGLE_LITE_URL.format(
                query=query.replace(" ", "+"),
                num=min(num_results, 20),
            )
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(random.uniform(2.0, 4.0))

            content = await page.content()
            if "/sorry/" in content or "unusual traffic" in content.lower():
                logger.warning("Stealth Google: CAPTCHA detected")
                return []

            return await self._parse_google_dom(page, num_results)
        except Exception as e:
            logger.warning("Stealth Google failed: %s", e)
            return []

    async def _parse_google_dom(self, page, max_results: int) -> list[StealthSearchResult]:
        """Parse Google search results from the Playwright page DOM."""
        results: list[StealthSearchResult] = []

        try:
            # Google results are in div.g > div > div > a or div.g > div > a
            # Try multiple selectors for robustness
            selectors = [
                "div.g div div a[href]",
                "div.g a[href]",
                "div[data-sokoban-container] a[href]",
                "#search div a[href]",
                "#rso div a[href]",
            ]

            links = []
            for selector in selectors:
                links = await page.query_selector_all(selector)
                if links:
                    break

            seen_urls: set[str] = set()

            for link in links:
                try:
                    href = await link.get_attribute("href")
                    if not href or not href.startswith("http"):
                        continue
                    if "google.com" in href or "gstatic.com" in href or "googleapis.com" in href:
                        continue
                    if href in seen_urls:
                        continue
                    seen_urls.add(href)

                    # Get title text
                    title_text = await link.inner_text()
                    if not title_text or len(title_text) < 3:
                        # Try parent for title
                        parent = await link.evaluate_handle("el => el.closest('div')")
                        if parent:
                            try:
                                title_text = await parent.evaluate("el => el.querySelector('h3')?.textContent || ''")
                            except Exception:
                                title_text = ""

                    if not title_text:
                        continue

                    # Get snippet — look for nearby text
                    snippet = ""
                    try:
                        parent_div = await link.evaluate_handle("el => el.closest('div.g') || el.closest('div')")
                        if parent_div:
                            snippet = await parent_div.evaluate("""
                                el => {
                                    const spans = el.querySelectorAll('span, div[data-sncf], div.VwiC3d');
                                    let text = '';
                                    for (const s of spans) {
                                        if (s.textContent && s.textContent.length > 30 && !s.querySelector('a')) {
                                            text = s.textContent;
                                            break;
                                        }
                                    }
                                    return text;
                                }
                            """)
                    except Exception:
                        pass

                    results.append(StealthSearchResult(
                        title=title_text.strip()[:200],
                        url=href,
                        snippet=(snippet or "").strip()[:300],
                        engine="stealth_google",
                    ))

                    if len(results) >= max_results:
                        break

                except Exception:
                    continue

        except Exception as e:
            logger.warning("Stealth Google DOM parse failed: %s", e)

        return results

    async def _parse_ddg_dom(self, page, max_results: int) -> list[StealthSearchResult]:
        """Parse DuckDuckGo HTML search results from the DOM."""
        results: list[StealthSearchResult] = []

        try:
            # DDG HTML results: a.result__a for links, a.result__snippet for snippets
            links = await page.query_selector_all("a.result__a")
            seen_urls: set[str] = set()

            for link in links:
                try:
                    href = await link.get_attribute("href")
                    if not href:
                        continue

                    # DDG uses redirect URLs: //duckduckgo.com/l/?uddg=...
                    if "uddg=" in href:
                        from urllib.parse import parse_qs, urlparse
                        parsed = urlparse(href if href.startswith("http") else "https:" + href)
                        qs = parse_qs(parsed.query)
                        href = qs.get("uddg", [href])[0]

                    if href in seen_urls or "duckduckgo.com" in href:
                        continue
                    seen_urls.add(href)

                    title = await link.inner_text()
                    if not title or len(title) < 3:
                        continue

                    # Get snippet from sibling element
                    snippet = ""
                    try:
                        parent = await link.evaluate_handle("el => el.closest('.result') || el.closest('.web-result')")
                        if parent:
                            snippet_el = await parent.query_selector(".result__snippet")
                            if snippet_el:
                                snippet = await snippet_el.inner_text()
                    except Exception:
                        pass

                    results.append(StealthSearchResult(
                        title=title.strip()[:200],
                        url=href,
                        snippet=snippet.strip()[:300],
                        engine="stealth_ddg",
                    ))

                    if len(results) >= max_results:
                        break
                except Exception:
                    continue

        except Exception as e:
            logger.warning("Stealth DDG DOM parse failed: %s", e)

        return results

    async def _parse_bing_dom(self, page, max_results: int) -> list[StealthSearchResult]:
        """Parse Bing search results from the DOM."""
        results: list[StealthSearchResult] = []

        try:
            # Bing results: li.b_algo > h2 > a for links, p for snippet
            links = await page.query_selector_all("li.b_algo h2 a")
            seen_urls: set[str] = set()

            for link in links:
                try:
                    href = await link.get_attribute("href")
                    if not href:
                        continue

                    # Bing uses redirect URLs: https://www.bing.com/ck/a?...&u=a1<base64>&...
                    # The real URL is base64-encoded in the 'u' parameter with 'a1' prefix
                    if "bing.com/ck/a" in href:
                        from urllib.parse import parse_qs, urlparse
                        import base64
                        parsed = urlparse(href)
                        qs = parse_qs(parsed.query)
                        u_param = qs.get("u", [""])[0]
                        if u_param.startswith("a1"):
                            u_param = u_param[2:]
                        try:
                            # Add padding if needed
                            padded = u_param + "=" * (4 - len(u_param) % 4) if len(u_param) % 4 else u_param
                            real_url = base64.b64decode(padded).decode("utf-8")
                            if real_url.startswith("http"):
                                href = real_url
                            else:
                                continue
                        except Exception:
                            continue
                    else:
                        if "bing.com" in href or "microsoft.com" in href:
                            continue

                    if href in seen_urls:
                        continue
                    if "bing.com" in href or "microsoft.com" in href:
                        continue
                    seen_urls.add(href)

                    title = await link.inner_text()
                    if not title or len(title) < 3:
                        continue

                    # Get snippet from sibling paragraph
                    snippet = ""
                    try:
                        parent = await link.evaluate_handle("el => el.closest('li.b_algo')")
                        if parent:
                            snippet_el = await parent.query_selector("p, .b_caption p, .b_captionpara")
                            if snippet_el:
                                snippet = await snippet_el.inner_text()
                    except Exception:
                        pass

                    results.append(StealthSearchResult(
                        title=title.strip()[:200],
                        url=href,
                        snippet=snippet.strip()[:300],
                        engine="stealth_bing",
                    ))

                    if len(results) >= max_results:
                        break
                except Exception:
                    continue

        except Exception as e:
            logger.warning("Stealth Bing DOM parse failed: %s", e)

        return results

    async def close(self) -> None:
        """Close browser and playwright."""
        if self._browser:
            try:
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
