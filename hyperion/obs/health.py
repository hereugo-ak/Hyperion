"""HYPERION Health Reporting — startup + completion health tables (P9/P13 GAP-4/GAP-6).

Two health table generators:

1. **Startup health table** (GAP-4): Checks every tool and tier at startup,
   prints a one-screen status table showing which tools are available, which
   are degraded, and which are offline.

2. **Completion health table** (GAP-6): At run end, prints a one-screen
   summary of tool usage, tier costs, degraded status, and overall health.

Usage::

    from hyperion.obs.health import check_startup_health, print_completion_health

    # At startup:
    check_startup_health(settings)

    # At run end:
    print_completion_health(engagement_result, trace_events)
"""

from __future__ import annotations

import socket
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hyperion.infra.paths import obscura_bin_dir


@dataclass
class ToolHealth:
    """Health status of a single tool."""
    name: str
    status: str = "UNKNOWN"  # OK, DEGRADED, OFFLINE
    detail: str = ""
    latency_ms: float = 0.0


@dataclass
class TierHealth:
    """Health status of a single model tier."""
    name: str
    providers: list[str] = field(default_factory=list)
    status: str = "OK"  # OK, DEGRADED, OFFLINE
    detail: str = ""


def _check_port(host: str, port: int, timeout: float = 2.0) -> bool:
    """Check if a TCP port is open (tool is reachable)."""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except (TimeoutError, OSError):
        return False


def _check_tool(name: str, settings: Any) -> ToolHealth:
    """Check the health of a single tool."""
    h = ToolHealth(name=name)

    if name == "searxng":
        # Host and port come from the configured URL via one shared parser.
        #
        # This used to be `url.split(":")` with `parts[2]` as the port, which
        # breaks on every URL shape except the exact literal default: a trailing
        # path made `int()` raise, and any URL without an explicit port silently
        # fell back to a hardcoded 8888. Pointing HYPERION at a SearxNG on
        # another port therefore probed the wrong port and reported OFFLINE for
        # a service that was serving perfectly.
        url = getattr(settings, "searxng_url", "") or "http://localhost:8888"
        host = getattr(settings, "searxng_host", None) or "localhost"
        port = getattr(settings, "searxng_port", None) or 8888
        if _check_port(host, port):
            h.status = "OK"
            h.detail = f"{url}"
        else:
            h.status = "OFFLINE"
            h.detail = f"not reachable at {host}:{port} ({url})"

    elif name == "flaresolverr":
        # Was hardcoded to localhost:8191 regardless of `flaresolverr_url`, so
        # the check and the client could point at different endpoints.
        url = getattr(settings, "flaresolverr_url", "") or "http://localhost:8191/v1"
        host = getattr(settings, "flaresolverr_host", None) or "localhost"
        port = getattr(settings, "flaresolverr_port", None) or 8191
        if _check_port(host, port):
            h.status = "OK"
            h.detail = f"{url}"
        else:
            h.status = "OFFLINE"
            h.detail = f"not reachable at {host}:{port}"

    elif name == "jina":
        key = getattr(settings, "jina_api_key", "")
        h.status = "OK" if key else "DEGRADED"
        h.detail = "API key set" if key else "no API key (free tier only)"

    elif name == "obscura":
        # Delegate to the client, so health and runtime agree by construction.
        #
        # This branch previously used `Path("obscura-bin/obscura.exe")` — a
        # CWD-relative path. Launching the shell from anywhere other than the
        # project root made the file "missing", so health reported Obscura
        # OFFLINE while the client (which walked to the project root) used it
        # successfully. The two answers came from two different code paths, and
        # health's was simply wrong. It also only checked existence, not
        # executability, so on Linux the Windows .exe counted as available.
        try:
            from hyperion.tools.obscura import ObscuraClient

            client = ObscuraClient(settings=settings)
            resolved = client._find_obscura()
            if client._binary_available():
                h.status = "OK"
                h.detail = resolved
            elif resolved and Path(resolved).is_file():
                # Present but not runnable here — the honest answer, and the
                # reason the fallback chain skips Obscura on this platform.
                h.status = "DEGRADED"
                h.detail = f"{resolved} present but not executable on {sys.platform}"
            else:
                h.status = "OFFLINE"
                h.detail = f"binary not found (looked in {obscura_bin_dir()} and PATH)"
        except Exception as exc:  # noqa: BLE001 - failure is recorded in the result
            h.status = "OFFLINE"
            h.detail = f"probe failed: {type(exc).__name__}: {exc}"[:80]

    elif name == "alpha_vantage":
        key = getattr(settings, "alpha_vantage_api_key", "")
        h.status = "OK" if key else "DEGRADED"
        h.detail = "API key set" if key else "no API key"

    elif name == "fred":
        key = getattr(settings, "fred_api_key", "")
        h.status = "OK" if key else "DEGRADED"
        h.detail = "API key set" if key else "no API key"

    elif name == "unsplash":
        key = getattr(settings, "unsplash_access_key", "")
        h.status = "OK" if key else "DEGRADED"
        h.detail = "API key set" if key else "no API key (typographic cover fallback)"

    elif name == "reddit":
        cid = getattr(settings, "reddit_client_id", "")
        h.status = "OK" if cid else "DEGRADED"
        h.detail = "credentials set" if cid else "no credentials"

    elif name == "weasyprint":
        # D15: Smoke-test WeasyPrint at startup to detect missing GTK
        # libraries (common on Windows — libgobject-2.0 not available).
        try:
            import os as _os
            import tempfile

            from weasyprint import HTML
            tmp = _os.path.join(tempfile.gettempdir(), "hyperion_wp_smoke.pdf")
            HTML(string="<p>smoke</p>").write_pdf(tmp)
            if _os.path.exists(tmp):
                _os.remove(tmp)
            h.status = "OK"
            h.detail = "render smoke-test passed"
        except ImportError:
            h.status = "OFFLINE"
            h.detail = "weasyprint not installed"
        except OSError as exc:
            h.status = "DEGRADED"
            h.detail = f"GTK libs missing: {str(exc)[:40]}"
        except Exception as exc:  # noqa: BLE001 - failure is recorded in the result
            h.status = "DEGRADED"
            h.detail = f"smoke-test failed: {str(exc)[:40]}"

    else:
        h.status = "UNKNOWN"
        h.detail = "no health check defined"

    return h


def _check_tier(name: str, settings: Any) -> TierHealth:
    """Check the health of a single model tier by verifying provider API keys."""
    h = TierHealth(name=name)

    tier_provider_map = {
        "MICRO": ["google", "groq", "mistral"],
        "FAST": ["cerebras", "mistral"],
        "STANDARD": ["nvidia", "groq", "mistral"],
        "STRONG": ["nvidia", "mistral"],
        "DEEP": ["google", "mistral"],
    }

    providers = tier_provider_map.get(name, [])
    available = []
    for p in providers:
        key_attr = f"{p}_api_key"
        key = getattr(settings, key_attr, "")
        if key:
            available.append(p)

    if available:
        h.providers = available
        h.status = "OK"
        h.detail = f"providers: {', '.join(available)}"
    elif providers:
        h.status = "OFFLINE"
        h.detail = f"no API keys for {', '.join(providers)}"
    else:
        h.status = "UNKNOWN"
        h.detail = "no provider mapping"

    return h


def check_startup_health(settings: Any) -> list[ToolHealth]:
    """Check every tool + tier at startup and print a health table.

    Returns the list of ToolHealth results for programmatic use.
    """
    tools = [
        "searxng", "flaresolverr", "jina", "obscura",
        "alpha_vantage", "fred", "unsplash", "reddit",
        "weasyprint",
    ]
    tiers = ["MICRO", "FAST", "STANDARD", "STRONG", "DEEP"]

    tool_results = [_check_tool(t, settings) for t in tools]
    tier_results = [_check_tier(t, settings) for t in tiers]

    # Print the health table
    print("\n" + "=" * 72)
    print("  HYPERION STARTUP HEALTH REPORT")
    print("=" * 72)

    print("\n  TOOLS:")
    print(f"  {'Tool':<22} {'Status':<10} {'Detail'}")
    print(f"  {'-'*22} {'-'*10} {'-'*36}")
    for t in tool_results:
        marker = "✓" if t.status == "OK" else ("⚠" if t.status == "DEGRADED" else "✗")
        print(f"  {marker} {t.name:<20} {t.status:<10} {t.detail[:36]}")

    print("\n  MODEL TIERS:")
    print(f"  {'Tier':<22} {'Status':<10} {'Detail'}")
    print(f"  {'-'*22} {'-'*10} {'-'*36}")
    for t in tier_results:
        marker = "✓" if t.status == "OK" else ("⚠" if t.status == "DEGRADED" else "✗")
        print(f"  {marker} {t.name:<20} {t.status:<10} {t.detail[:36]}")

    ok_count = sum(1 for t in tool_results if t.status == "OK")
    deg_count = sum(1 for t in tool_results if t.status == "DEGRADED")
    off_count = sum(1 for t in tool_results if t.status == "OFFLINE")
    print(f"\n  Tools: {ok_count} OK, {deg_count} degraded, {off_count} offline")

    tier_ok = sum(1 for t in tier_results if t.status == "OK")
    tier_off = sum(1 for t in tier_results if t.status == "OFFLINE")
    print(f"  Tiers: {tier_ok} OK, {tier_off} offline")

    if off_count > 0 or tier_off > 0:
        print("  ⚠ Some tools/tiers offline — pipeline will degrade gracefully.")
    else:
        print("  ✓ All systems operational.")

    print("=" * 72 + "\n")

    return tool_results


def print_completion_health(
    result: Any,
    trace_events: list[dict[str, Any]] | None = None,
) -> None:
    """Print a one-screen completion health table at run end (GAP-6).

    Shows tool usage, tier costs, degraded status, and overall health.
    """
    print("\n" + "=" * 72)
    print("  HYPERION COMPLETION HEALTH REPORT")
    print("=" * 72)

    # Engagement summary
    print(f"\n  Engagement:  {getattr(result, 'engagement_id', '?')}")
    print(f"  Question:    {getattr(result, 'question', '?')[:60]}")
    print(f"  Duration:    {getattr(result, 'duration_seconds', 0):.0f}s")
    print(f"  Success:     {'YES' if getattr(result, 'success', False) else 'NO'}")

    if hasattr(result, 'quality_score') and result.quality_score:
        qs = result.quality_score
        print(f"  Quality:     {qs.total_score:.1f}/{qs.threshold:.1f} "
              f"(iterations: {getattr(result, 'quality_iterations', '?')})")
    else:
        print("  Quality:     N/A")

    if hasattr(result, 'pdf_path') and result.pdf_path:
        print(f"  PDF:         {result.pdf_path}")
    else:
        print("  PDF:         NOT GENERATED")

    # Metadata
    if hasattr(result, 'metadata') and result.metadata:
        m = result.metadata
        print(f"  Sources:     {getattr(m, 'sources_accessed', '?')}")
        print(f"  Findings:    {getattr(m, 'data_points_collected', '?')}")
        print(f"  LLM calls:   {getattr(m, 'llm_calls_made', '?')}")
        print(f"  Tokens:      {getattr(m, 'tokens_consumed', '?')}")
        print(f"  Escalations: {getattr(result, 'escalation_count', 0)}")

    # Tool/tier usage from trace events
    if trace_events:
        tool_usage: dict[str, int] = {}
        tier_usage: dict[str, int] = {}
        for ev in trace_events:
            stage = ev.get("stage", "")
            if stage == "search" or stage == "extract":
                tool = ev.get("tool", ev.get("agent", "?"))
                tool_usage[tool] = tool_usage.get(tool, 0) + 1
            elif stage == "llm":
                tier = ev.get("tier", "?")
                tier_usage[tier] = tier_usage.get(tier, 0) + 1

        if tool_usage:
            print("\n  TOOL USAGE:")
            for tool, count in sorted(tool_usage.items(), key=lambda x: -x[1]):
                print(f"    {tool:<20} {count} calls")

        if tier_usage:
            print("\n  TIER USAGE:")
            for tier, count in sorted(tier_usage.items()):
                print(f"    {tier:<20} {count} calls")

    # Degraded status
    if not getattr(result, 'success', False):
        print(f"\n  ⚠ ENGAGEMENT FAILED: {getattr(result, 'error', 'unknown')[:60]}")
    elif (
        hasattr(result, 'quality_score')
        and result.quality_score
        and result.quality_score.total_score < result.quality_score.threshold
    ):
            print("\n  ⚠ Quality below threshold — report delivered with caveats.")

    print("=" * 72 + "\n")
