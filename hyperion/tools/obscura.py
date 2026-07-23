"""
HYPERION Obscura Client — Rust headless browser with CDP + MCP integration.

Obscura is the primary browser tool for JS-heavy pages. It is NOT just
a headless Chrome wrapper — it is a purpose-built agentic browser:

- 70MB binary vs 400MB+ for Chrome — lightweight
- 30MB RAM vs 200MB+ for Chrome — efficient
- Instant cold start vs 3-5s for Chrome — fast
- No Chrome dependencies, no Node.js — self-contained
- Stealth mode: anti-fingerprinting + tracker blocking
- CDP-compatible (Chrome DevTools Protocol)
- MCP server with 12 tools

This client provides two interfaces:

1. **CLI commands** — for one-shot operations:
   - `obscura fetch <URL> --dump markdown` — fetch and render a single page
   - `obscura scrape <URL...> --concurrency 10` — batch scrape multiple URLs

2. **CDP WebSocket** — for multi-step interactions (persistent session):
   - Connect to `obscura serve --port 9222 --stealth`
   - Navigate → click → extract → evaluate (multi-step workflows)
   - Used for interactive pricing calculators, dropdowns, tabs

The 12 MCP tools (§5.3):
- browser_navigate(url, waitUntil)
- browser_snapshot()
- browser_click(selector)
- browser_fill(selector, value)
- browser_type(selector, text)
- browser_press_key(key, selector)
- browser_select_option(selector, value)
- browser_evaluate(expression) — THE MOST POWERFUL: extract structured data
- browser_wait_for(selector, timeout)
- browser_network_requests()
- browser_console_messages()
- browser_close()

Architecture reference: §5.1, §5.3 — "Rust headless browser. 70MB binary,
30MB RAM, instant cold start. CDP-compatible. Stealth mode. MCP server
with 12 tools. Primary browser for JS-heavy pages."

Agent usage patterns (§5.3):
- Competitive Intel: `obscura scrape` for batch competitor pricing.
  `browser_evaluate` for structured pricing data from calculators.
  Stealth mode to avoid bot detection.
- Consumer Insights: `browser_navigate` + `browser_snapshot` for review
  sites. `browser_evaluate` for review counts, ratings, sentiment.
- Technology Analyst: `obscura fetch` for vendor pricing pages.
  `browser_evaluate` for AWS/GCP pricing calculators.
- Regulatory Analyst: `obscura fetch` for government portals.
  `browser_navigate` + `browser_click` for multi-step databases.
- Fact Checker: `obscura fetch` for verifying claims on JS-rendered pages.

Extraction fallback chain (§5.2):
  Obscura (stealth, JS rendering) ← THIS (first option)
    → Crawl4AI (heavy extraction, PDFs)
      → Jina Reader (fast, simple extraction)
        → Wayback (if page is down or changed)
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ObscuraFetchResult:
    """Result of an `obscura fetch` command."""

    url: str
    title: str = ""
    content: str = ""
    markdown: str = ""
    status_code: int = 0
    error: str = ""
    took_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "status_code": self.status_code,
            "error": self.error,
            "took_ms": self.took_ms,
        }


@dataclass
class ObscuraScrapeResult:
    """Result of an `obscura scrape` command (batch)."""

    results: list[ObscuraFetchResult] = field(default_factory=list)
    total: int = 0
    successful: int = 0
    failed: int = 0
    took_ms: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "results": [r.to_dict() for r in self.results],
            "total": self.total,
            "successful": self.successful,
            "failed": self.failed,
            "took_ms": self.took_ms,
        }


@dataclass
class CDPResponse:
    """Response from a CDP WebSocket command."""

    id: int
    result: Any = None
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "result": self.result,
            "error": self.error,
        }


class ObscuraClient:
    """Obscura headless browser client.

    Provides two interfaces:
    1. CLI commands (fetch, scrape) — for one-shot operations
    2. CDP WebSocket — for multi-step interactive browser sessions

    Stealth mode is always enabled to avoid bot detection.
    (§5.1, §5.3)

    Usage:
        client = ObscuraClient(settings=settings)

        # One-shot fetch
        result = await client.fetch("https://competitor.com/pricing")
        print(result.markdown[:500])

        # Batch scrape
        results = await client.scrape([
            "https://comp-a.com/pricing",
            "https://comp-b.com/pricing",
            "https://comp-c.com/pricing",
        ], concurrency=10)

        # Multi-step CDP session
        await client.connect_cdp(port=9222)
        await client.navigate("https://example.com/calculator")
        await client.click("#pricing-tab")
        data = await client.evaluate("document.querySelector('.price').textContent")
        await client.close_browser()
    """

    DEFAULT_PORT = 9222
    CLI_TIMEOUT = 60  # seconds for CLI commands
    CDP_TIMEOUT = 30  # seconds for CDP commands
    MAX_RETRIES = 2

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._obscura_path = "obscura"
        if settings:
            self._obscura_path = getattr(settings, "obscura_path", "") or "obscura"
        self._cdp_client: httpx.AsyncClient | None = None
        self._cdp_ws: Any = None
        self._cdp_port: int = self.DEFAULT_PORT
        self._command_id: int = 0
        self._stealth: bool = True
        # P12 GAP-5: Managed `obscura serve` subprocess
        self._serve_proc: asyncio.subprocess.Process | None = None

    def _find_obscura(self) -> str:
        """Find the obscura binary."""
        # Check configured path first
        if self._obscura_path and os.path.exists(self._obscura_path):
            return self._obscura_path

        # Check PATH
        found = shutil.which("obscura")
        if found:
            return found

        # On Windows, also try obscura.exe in PATH
        if sys.platform == "win32":
            found = shutil.which("obscura.exe")
            if found:
                return found

        # Check local obscura-bin directory in project root
        project_root = Path(__file__).resolve().parents[2]
        for candidate in [
            project_root / "obscura-bin" / "obscura.exe",
            project_root / "obscura-bin" / "obscura",
        ]:
            if candidate.exists():
                return str(candidate)

        return self._obscura_path  # Return configured path even if not found

    def _is_platform_supported(self) -> bool:
        """Check if Obscura binary is supported on this platform.

        Obscura is distributed as a Windows binary (.exe). On non-Windows
        platforms, it won't run even if the file exists in the project.
        This guard prevents subprocess errors and lets the extraction
        fallback chain proceed to the next tool.
        """
        # Windows: always supported (binary is .exe)
        if sys.platform == "win32":
            return True

        # Non-Windows: check if a platform-native binary exists in PATH
        # (user may have compiled from source for Linux/macOS)
        found = shutil.which("obscura")
        if found:
            return True

        # Check if the local obscura-bin has a non-.exe binary
        project_root = Path(__file__).resolve().parents[2]
        native_binary = project_root / "obscura-bin" / "obscura"
        if native_binary.exists() and sys.platform != "win32":
            # Verify it's actually executable (not the Windows .exe renamed)
            try:
                result = subprocess.run(
                    [str(native_binary), "--version"],
                    capture_output=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    return True
            except (subprocess.SubprocessError, OSError):
                pass

        logger.debug(
            "Obscura binary not available on platform %s — skipping, "
            "extraction fallback chain will use next tool",
            sys.platform,
        )
        return False

    def _binary_available(self) -> bool:
        """Check if the Obscura binary exists and is platform-supported."""
        if not self._is_platform_supported():
            return False
        obscura_bin = self._find_obscura()
        return bool(obscura_bin) and os.path.exists(obscura_bin)

    # ─────────────────────────────────────────────────────────────────────
    # CLI Commands — one-shot operations
    # ─────────────────────────────────────────────────────────────────────

    async def fetch(
        self,
        url: str,
        output_format: str = "markdown",
        stealth: bool = True,
    ) -> ObscuraFetchResult:
        """Fetch and render a single page via `obscura fetch`.

        Args:
            url: URL to fetch
            output_format: Output format (markdown, html, text)
            stealth: Whether to use stealth mode (anti-fingerprinting)

        Returns:
            ObscuraFetchResult with the rendered page content.
        """
        # Platform guard — skip gracefully if binary not available
        if not self._binary_available():
            return ObscuraFetchResult(
                url=url,
                error=f"Obscura binary not available on {sys.platform}",
            )

        obscura_bin = self._find_obscura()

        cmd = [obscura_bin, "fetch", url, "--dump", output_format]
        if stealth or self._stealth:
            cmd.append("--stealth")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.CLI_TIMEOUT
            )

            if proc.returncode == 0:
                content = stdout.decode("utf-8", errors="replace")
                # Extract title from first line if markdown
                title = ""
                if output_format == "markdown":
                    lines = content.split("\n")
                    for line in lines:
                        if line.strip().startswith("# "):
                            title = line.strip()[2:]
                            break
                    if not title and lines:
                        title = lines[0][:200]

                return ObscuraFetchResult(
                    url=url,
                    title=title,
                    content=content,
                    markdown=content,
                    status_code=200,
                )
            else:
                error = stderr.decode("utf-8", errors="replace")
                return ObscuraFetchResult(
                    url=url,
                    status_code=proc.returncode or 500,
                    error=error,
                )

        except asyncio.TimeoutError:
            return ObscuraFetchResult(
                url=url,
                status_code=408,
                error=f"Obscura fetch timed out after {self.CLI_TIMEOUT}s",
            )
        except (OSError, FileNotFoundError) as e:
            return ObscuraFetchResult(
                url=url,
                status_code=500,
                error=f"Obscura binary not found: {e}",
            )

    async def scrape(
        self,
        urls: list[str],
        concurrency: int = 10,
        output_format: str = "markdown",
        stealth: bool = True,
    ) -> ObscuraScrapeResult:
        """Batch scrape multiple URLs via `obscura scrape`.

        Args:
            urls: List of URLs to scrape
            concurrency: Number of concurrent fetches
            output_format: Output format (markdown, html, text)
            stealth: Whether to use stealth mode

        Returns:
            ObscuraScrapeResult with all fetch results.
        """
        if not urls:
            return ObscuraScrapeResult()

        # Accept a single URL string for convenience (agents call with one URL)
        if isinstance(urls, str):
            urls = [urls]

        # Platform guard — skip gracefully if binary not available
        if not self._binary_available():
            return ObscuraScrapeResult(
                results=[ObscuraFetchResult(
                    url=u,
                    error=f"Obscura binary not available on {sys.platform}",
                ) for u in urls],
                total=len(urls),
                failed=len(urls),
            )

        obscura_bin = self._find_obscura()

        cmd = [obscura_bin, "scrape"] + urls
        cmd.extend(["--concurrency", str(concurrency), "--dump", output_format])
        if stealth or self._stealth:
            cmd.append("--stealth")

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=self.CLI_TIMEOUT * max(1, len(urls) // concurrency),
            )

            if proc.returncode == 0:
                output = stdout.decode("utf-8", errors="replace")
                # Obscura scrape outputs JSON with results array
                try:
                    data = json.loads(output)
                    results: list[ObscuraFetchResult] = []
                    for item in data.get("results", []):
                        results.append(ObscuraFetchResult(
                            url=item.get("url", ""),
                            title=item.get("title", ""),
                            content=item.get("content", ""),
                            markdown=item.get("content", ""),
                            status_code=item.get("status_code", 200),
                        ))

                    successful = sum(1 for r in results if r.status_code == 200)
                    failed = len(results) - successful

                    return ObscuraScrapeResult(
                        results=results,
                        total=len(results),
                        successful=successful,
                        failed=failed,
                    )
                except (json.JSONDecodeError, KeyError):
                    # Fallback: treat entire output as single result
                    return ObscuraScrapeResult(
                        results=[ObscuraFetchResult(
                            url=urls[0],
                            content=output,
                            markdown=output,
                            status_code=200,
                        )],
                        total=1,
                        successful=1,
                    )
            else:
                error = stderr.decode("utf-8", errors="replace")
                return ObscuraScrapeResult(
                    results=[ObscuraFetchResult(url=u, error=error) for u in urls],
                    total=len(urls),
                    failed=len(urls),
                )

        except asyncio.TimeoutError:
            return ObscuraScrapeResult(
                results=[ObscuraFetchResult(url=u, error="Timeout") for u in urls],
                total=len(urls),
                failed=len(urls),
            )
        except (OSError, FileNotFoundError) as e:
            return ObscuraScrapeResult(
                results=[ObscuraFetchResult(url=u, error=str(e)) for u in urls],
                total=len(urls),
                failed=len(urls),
            )

    # ─────────────────────────────────────────────────────────────────────
    # P12 GAP-5: Managed `obscura serve` subprocess
    # ─────────────────────────────────────────────────────────────────────

    async def start_serve(self, port: int = DEFAULT_PORT) -> bool:
        """Start `obscura serve` as a managed subprocess.

        Spawns the Obscura CDP server in the background and waits for it
        to become ready.  The process is tracked and cleaned up by
        stop_serve() or close().

        Args:
            port: CDP WebSocket port (default 9222)

        Returns:
            True if the server started successfully, False otherwise.
        """
        if self._serve_proc and self._serve_proc.returncode is None:
            # Already running
            return True

        if not self._binary_available():
            logger.debug("Obscura serve: binary not available on %s", sys.platform)
            return False

        obscura_bin = self._find_obscura()
        cmd = [obscura_bin, "serve", "--port", str(port)]
        if self._stealth:
            cmd.append("--stealth")

        try:
            self._serve_proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info("Obscura serve started (pid=%d, port=%d)", self._serve_proc.pid, port)

            # Wait for the server to become ready
            if await self._wait_for_serve(port):
                self._cdp_port = port
                return True
            else:
                logger.warning("Obscura serve did not become ready within timeout")
                await self.stop_serve()
                return False

        except (OSError, FileNotFoundError) as e:
            logger.warning("Obscura serve failed to start: %s", e)
            self._serve_proc = None
            return False

    async def _wait_for_serve(self, port: int, timeout: float = 10.0) -> bool:
        """Wait for the Obscura serve HTTP endpoint to respond."""
        import time as _time
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            try:
                async with httpx.AsyncClient(timeout=2.0) as client:
                    resp = await client.get(f"http://localhost:{port}/json/version")
                    if resp.status_code == 200:
                        return True
            except (httpx.HTTPError, httpx.RequestError):
                pass
            await asyncio.sleep(0.5)
        return False

    async def stop_serve(self) -> None:
        """Stop the managed `obscura serve` subprocess."""
        if self._serve_proc and self._serve_proc.returncode is None:
            try:
                self._serve_proc.terminate()
                await asyncio.wait_for(self._serve_proc.wait(), timeout=5.0)
                logger.info("Obscura serve stopped (pid=%d)", self._serve_proc.pid)
            except asyncio.TimeoutError:
                self._serve_proc.kill()
                logger.warning("Obscura serve killed (did not terminate gracefully)")
            except (OSError, ProcessLookupError):
                pass
        self._serve_proc = None

    def _is_serve_running(self) -> bool:
        """Check if the managed serve process is still alive."""
        return (
            self._serve_proc is not None
            and self._serve_proc.returncode is None
        )

    # ─────────────────────────────────────────────────────────────────────
    # CDP WebSocket — multi-step interactive browser session
    # ─────────────────────────────────────────────────────────────────────

    async def connect_cdp(self, port: int = DEFAULT_PORT) -> bool:
        """Connect to an Obscura CDP WebSocket server.

        P12 GAP-5: If no server is running on the specified port, this
        method will auto-start `obscura serve` as a managed subprocess
        before connecting.

        Requires `obscura serve --port <port> --stealth` to be running.

        Args:
            port: CDP WebSocket port (default 9222)

        Returns:
            True if connection succeeded, False otherwise.
        """
        # Platform guard — skip if binary not available
        if not self._binary_available():
            logger.debug("Obscura CDP not available on %s — skipping", sys.platform)
            return False

        self._cdp_port = port

        # P12 GAP-5: Auto-start managed serve process if not already running
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                await client.get(f"http://localhost:{port}/json/version")
        except (httpx.HTTPError, httpx.RequestError):
            # Server not running — try to start it
            if not await self.start_serve(port):
                return False

        try:
            # Get the WebSocket endpoint from the CDP HTTP API
            async with httpx.AsyncClient(timeout=self.CDP_TIMEOUT) as client:
                response = await client.get(f"http://localhost:{port}/json/version")
                data = response.json()
                ws_url = data.get("webSocketDebuggerUrl", "")

                if not ws_url:
                    # Try /json/list for page targets
                    response = await client.get(f"http://localhost:{port}/json/list")
                    targets = response.json()
                    if targets:
                        ws_url = targets[0].get("webSocketDebuggerUrl", "")

                if not ws_url:
                    return False

                # Connect via WebSocket
                try:
                    import websockets

                    self._cdp_ws = await websockets.connect(ws_url)
                    return True
                except ImportError:
                    # websockets not available — try aiohttp
                    try:
                        from aiohttp import ClientSession, WSMsgType

                        session = ClientSession()
                        self._cdp_ws = await session.ws_connect(ws_url)
                        self._cdp_client = session
                        return True
                    except ImportError:
                        return False

        except (httpx.HTTPError, httpx.RequestError, KeyError, ValueError):
            return False

    async def _send_cdp_command(
        self,
        method: str,
        params: dict[str, Any] | None = None,
    ) -> CDPResponse:
        """Send a CDP command and wait for the response."""
        if not self._cdp_ws:
            return CDPResponse(id=0, error="Not connected to CDP")

        self._command_id += 1
        cmd_id = self._command_id

        command = {
            "id": cmd_id,
            "method": method,
        }
        if params:
            command["params"] = params

        try:
            await self._cdp_ws.send(json.dumps(command))

            # Wait for response with matching id
            while True:
                msg = await asyncio.wait_for(
                    self._cdp_ws.recv(),
                    timeout=self.CDP_TIMEOUT,
                )

                if isinstance(msg, bytes):
                    msg = msg.decode("utf-8", errors="replace")

                data = json.loads(msg)
                if data.get("id") == cmd_id:
                    return CDPResponse(
                        id=cmd_id,
                        result=data.get("result"),
                        error=data.get("error", {}).get("message", "") if "error" in data else "",
                    )
                # Ignore events and other responses

        except asyncio.TimeoutError:
            return CDPResponse(id=cmd_id, error="CDP command timed out")
        except (ConnectionError, json.JSONDecodeError, RuntimeError) as e:
            return CDPResponse(id=cmd_id, error=str(e))

    # ── The 12 MCP tools (§5.3) ──

    async def navigate(
        self,
        url: str,
        wait_until: str = "load",
    ) -> CDPResponse:
        """Navigate to a URL. (MCP tool: browser_navigate)

        Wait conditions: load, domcontentloaded, networkidle0.
        """
        return await self._send_cdp_command("Page.navigate", {"url": url})

    async def snapshot(self) -> CDPResponse:
        """Get accessibility tree snapshot. (MCP tool: browser_snapshot)

        Used for understanding page structure.
        """
        return await self._send_cdp_command("Accessibility.getFullAXTree")

    async def click(self, selector: str) -> CDPResponse:
        """Click an element by selector. (MCP tool: browser_click)

        Used for interacting with pricing calculators, dropdowns, tabs.
        """
        # Find the element first
        find_result = await self._send_cdp_command("DOM.querySelector", {
            "selector": selector,
        })
        if find_result.error or not find_result.result:
            return CDPResponse(id=0, error=f"Element not found: {selector}")

        node_id = find_result.result.get("nodeId", 0)
        if not node_id:
            return CDPResponse(id=0, error=f"Element not found: {selector}")

        # Scroll into view and click
        await self._send_cdp_command("DOM.scrollIntoViewIfNeeded", {"nodeId": node_id})
        return await self._send_cdp_command("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": 0, "y": 0,
            "button": "left",
            "clickCount": 1,
        })

    async def fill(self, selector: str, value: str) -> CDPResponse:
        """Fill an input field. (MCP tool: browser_fill)

        Used for search forms, filter inputs.
        """
        find_result = await self._send_cdp_command("DOM.querySelector", {
            "selector": selector,
        })
        if find_result.error or not find_result.result:
            return CDPResponse(id=0, error=f"Element not found: {selector}")

        node_id = find_result.result.get("nodeId", 0)
        return await self._send_cdp_command("DOM.setAttributeValue", {
            "nodeId": node_id,
            "name": "value",
            "value": value,
        })

    async def type_text(self, selector: str, text: str) -> CDPResponse:
        """Type text into an element. (MCP tool: browser_type)

        Used for form submission.
        """
        for char in text:
            await self._send_cdp_command("Input.dispatchKeyEvent", {
                "type": "char",
                "text": char,
            })
        return CDPResponse(id=0, result={"typed": len(text)})

    async def press_key(self, key: str, selector: str = "") -> CDPResponse:
        """Press a keyboard key. (MCP tool: browser_press_key)

        Used for Enter, Escape, Tab.
        """
        return await self._send_cdp_command("Input.dispatchKeyEvent", {
            "type": "keyDown",
            "key": key,
        })

    async def select_option(self, selector: str, value: str) -> CDPResponse:
        """Select an <option> from a dropdown. (MCP tool: browser_select_option)

        Used for filter selection.
        """
        # Use JavaScript to select the option
        js = f"""
        const select = document.querySelector({json.dumps(selector)});
        if (select) {{
            select.value = {json.dumps(value)};
            select.dispatchEvent(new Event('change', {{bubbles: true}}));
            return true;
        }}
        return false;
        """
        return await self.evaluate(js)

    async def evaluate(self, expression: str) -> CDPResponse:
        """Execute JavaScript in the page context. (MCP tool: browser_evaluate)

        THE MOST POWERFUL TOOL — used for extracting structured data from
        interactive elements (pricing tables, feature comparisons, review
        counts) that aren't in the HTML source.
        """
        return await self._send_cdp_command("Runtime.evaluate", {
            "expression": expression,
            "returnByValue": True,
            "awaitPromise": True,
        })

    async def wait_for(
        self,
        selector: str,
        timeout: int = 30000,
    ) -> CDPResponse:
        """Wait for an element to appear. (MCP tool: browser_wait_for)

        Used for pages with dynamic content loading.
        """
        js = f"""
        new Promise((resolve, reject) => {{
            const timeoutId = setTimeout(() => reject('Timeout waiting for {selector}'), {timeout});
            const check = () => {{
                if (document.querySelector({json.dumps(selector)})) {{
                    clearTimeout(timeoutId);
                    resolve(true);
                }} else {{
                    requestAnimationFrame(check);
                }}
            }};
            check();
        }})
        """
        return await self.evaluate(js)

    async def network_requests(self) -> CDPResponse:
        """Get all network requests made by the page. (MCP tool: browser_network_requests)

        Used for intercepting API calls and extracting data from XHR responses.
        """
        return await self._send_cdp_command("Network.enable")

    async def console_messages(self) -> CDPResponse:
        """Get console messages. (MCP tool: browser_console_messages)

        Used for debugging scraping issues.
        """
        return await self._send_cdp_command("Runtime.enable")

    async def close_browser(self) -> None:
        """Close the browser instance. (MCP tool: browser_close)"""
        if self._cdp_ws:
            try:
                await self._send_cdp_command("Browser.close")
            except (ConnectionError, RuntimeError):
                pass
            try:
                await self._cdp_ws.close()
            except (ConnectionError, RuntimeError):
                pass
            self._cdp_ws = None

        if self._cdp_client and not self._cdp_client.is_closed:
            await self._cdp_client.aclose()
            self._cdp_client = None

    async def close(self) -> None:
        """Close all connections and stop managed serve process."""
        await self.close_browser()
        await self.stop_serve()

    async def __aenter__(self) -> ObscuraClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
