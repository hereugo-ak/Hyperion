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


def _format_quality_line(
    total_score: float,
    *,
    threshold: float = 4.0,
    iterations: int | str = "?",
) -> str:
    """P5.2 (overhaul §6 P5 / A-12): ONE quality score scale everywhere.

    The weighted total is a 1-5 rubric score; the approval threshold is a
    comparison line, never a denominator. Returns ``score/5.0`` with the
    threshold stated explicitly, so no surface can render the audit's broken
    ``3.2/4.0`` (threshold-as-scale) against the CLI's ``3.2/5.0``.
    """
    threshold = threshold or 4.0
    return (
        f"  Quality:     {total_score:.1f}/5.0 "
        f"(approve \u2265 {threshold:.1f}; iterations: {iterations})"
    )


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


def _searxng_probe_targets(settings: Any) -> list[tuple[str, str, tuple[str, ...]]]:
    """Return LOCAL-ONLY readiness targets: (url, profile, engines).

    P1.5 (overhaul §6 P1, 2026-08-10): readiness is local. The probe NEVER
    issues a search query — the Aug-9/10 autopsy showed the boot smoke
    tripping the fleet into 403/429 one minute before the engagement even
    began. Each managed replica is checked for (1) a reachable TCP port,
    (2) a serving ``/config`` endpoint (served locally, zero upstream), and
    (3) persisted engine-health state. The corpus probe — the thing that
    DOES send queries — belongs to the Phase-2 preflight
    (``corpus_preflight.py``), never to boot.

    The configured compatibility URL points at the scholar replica on 8888.
    Probing only that endpoint produced the exact false OFFLINE refusal in
    the reported run even while the web and reference replicas were healthy,
    so every managed replica is probed. Custom/remote SearXNG deployments
    remain a single local target.
    """
    from urllib.parse import urlsplit

    configured = getattr(settings, "searxng_url", "") or "http://localhost:8888"
    parsed = urlsplit(configured if "://" in configured else f"http://{configured}")
    if (parsed.hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        return [(configured.rstrip("/"), "custom", ())]

    from hyperion.infra.services import SEARXNG_REPLICAS

    return [
        (f"http://127.0.0.1:{replica.port}", replica.profile, replica.engines)
        for replica in SEARXNG_REPLICAS
    ]


# P2-29: a TCP connect or a key-presence check cannot detect a dead key.
# The Google credential that triggered Part 2 had been deleted by Google;
# every reachability probe succeeded for two entire engagements while every
# completion returned 401, and the failure was misreported as quota
# exhaustion. The only honest credential signal is one real minimal
# completion per configured provider.
_PREFLIGHT_MESSAGES = [
    {"role": "user", "content": "Reply with exactly the word: ok"},
]


async def credential_preflight(router: Any) -> dict[Any, str]:
    """One real minimal completion per configured provider (P2-29 / P2-G29).

    For every provider the router holds, dispatch one MICRO-tier ping and
    classify the outcome:

      "OK"               provider answered
      "UNAUTHENTICATED"  401/403 — the credential is dead (distinct from quota)
      "QUOTA"            429 / rate limit
      "UNAVAILABLE"      anything else (network, server error, no model)

    A dead key is stamped onto ``provider.health`` via ``record_auth_error``
    so the router diagnoses it as unauthenticated for the rest of the
    process and never aggregates it into rate-limit reporting.
    """
    from hyperion.config import ModelTier
    from hyperion.router import network_health

    # L1 fix: ONE preflight network probe before dispatching per-provider
    # pings. If the environment is unreachable, every subsequent per-
    # provider ping will fail and independently trip its circuit — the
    # exact "three parallel circuit storms" the audit calls out. A single
    # probe → global degraded flag lets the whole system report
    # UNAVAILABLE cleanly instead of firing five noisy failures in
    # parallel while the network is down.
    try:
        await network_health.probe(force=True)
    except Exception as exc:  # noqa: BLE001 - probe is contractually non-raising
        # Best effort: a probe failure is not a preflight failure.
        _log_preflight("__network__", "PROBE_ERROR", f"{type(exc).__name__}: {exc}")

    results: dict[Any, str] = {}
    for provider_type, provider in router._providers.items():
        models = provider.get_models_for_tier(ModelTier.MICRO)
        if not models:
            # Fall back to any non-deprecated model this provider serves
            models = [
                m
                for tier in ModelTier
                for m in provider.get_models_for_tier(tier)
            ]
        if not models:
            results[provider_type] = "UNAVAILABLE"
            continue
        try:
            resp = await provider.complete(
                model=models[0].name,
                messages=_PREFLIGHT_MESSAGES,
                tier=ModelTier.MICRO,
                temperature=0.0,
                max_tokens=8,
            )
        except Exception as exc:  # noqa: BLE001 - preflight failure is recorded, never raised
            results[provider_type] = "UNAVAILABLE"
            _log_preflight(provider_type, "UNAVAILABLE", f"exception: {type(exc).__name__}")
            continue

        if resp.success:
            results[provider_type] = "OK"
            continue

        error = resp.error or ""
        lower = error.lower()
        if "401" in error or "403" in error or "api key" in lower or "unauthorized" in lower or "authentication" in lower:
            provider.health.record_auth_error()
            results[provider_type] = "UNAUTHENTICATED"
        elif "429" in error or "rate_limit" in lower:
            results[provider_type] = "QUOTA"
        else:
            results[provider_type] = "UNAVAILABLE"

        if results[provider_type] == "UNAUTHENTICATED":
            _log_preflight(provider_type, "UNAUTHENTICATED", error[:100])

    return results


def _log_preflight(provider_type: Any, status: str, detail: str) -> None:
    """Loud startup log for a credential failure — never silent."""
    from hyperion.obs import trace

    trace(
        "preflight",
        provider=getattr(provider_type, "value", str(provider_type)),
        status=status,
        detail=detail,
    )


def _check_searxng(settings: Any) -> ToolHealth:
    """LOCAL-ONLY SearXNG readiness (P1.5) — never issues a search query.

    Readiness answers three local questions per replica:
      1. Is the TCP port reachable?
      2. Does the instance serve its ``/config`` endpoint (local, zero
         upstream traffic)?
      3. What does PERSISTED engine health say about its engines?

    Verdicts:
      OK       every replica serves locally and no replica has a fully
               cooling/suspended engine set
      DEGRADED some replicas unreachable, or a reachable replica has zero
               available engines (persisted bans from an earlier session)
      OFFLINE  no replica is reachable at all

    The corpus probe (the check that sends queries) is the Phase-2
    preflight's job (``corpus_preflight.py``), not boot's.
    """
    h = ToolHealth(name="searxng")
    targets = _searxng_probe_targets(settings)
    total = len(targets)
    reachable = 0
    cooling: set[str] = set()
    failures: list[str] = []

    try:
        import httpx
    except ImportError as exc:
        h.status = "OFFLINE"
        h.detail = f"local readiness unavailable: {exc}"
        return h

    from hyperion.tools.engine_health import get_engine_health

    health = get_engine_health()

    for url, profile, engines in targets:
        from urllib.parse import urlsplit

        parsed = urlsplit(url)
        host = parsed.hostname or "localhost"
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        label = f"{profile}:{port}"
        if not _check_port(host, port):
            failures.append(f"{label}: unreachable")
            continue
        try:
            response = httpx.get(f"{url.rstrip('/')}/config", timeout=5.0)
            response.raise_for_status()
            response.json()
        except Exception as exc:  # noqa: BLE001 - aggregate every profile failure
            failures.append(f"{label}: /config {type(exc).__name__}: {str(exc)[:60]}")
            continue
        reachable += 1
        # Persisted engine health: a replica whose engines are all cooling or
        # suspended has no usable capacity even though it serves locally.
        if engines:
            unavailable = [e for e in engines if not health.is_available(e)]
            if len(unavailable) == len(engines):
                failures.append(f"{label}: no capacity ({', '.join(unavailable)})")
            cooling.update(unavailable)

    if reachable == 0:
        h.status = "OFFLINE"
        h.detail = "no local SearXNG replica reachable; " + "; ".join(failures)
    elif reachable == total and not failures:
        h.status = "OK"
        h.detail = (
            f"{reachable}/{total} replicas local"
            + (f"; cooling: {', '.join(sorted(cooling))}" if cooling else "")
        )
    else:
        h.status = "DEGRADED"
        h.detail = f"{reachable}/{total} replicas local; " + "; ".join(failures)
    return h


def _check_tool(name: str, settings: Any) -> ToolHealth:
    """Check the health of a single tool."""
    h = ToolHealth(name=name)

    if name == "searxng":
        return _check_searxng(settings)

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
                reason = client.unavailable_detail()
                if "refused to load" in reason or "PermissionError" in reason:
                    # D-10: the file EXISTS and is "executable" — but the OS
                    # refuses to load it. On managed Windows hosts this is
                    # Defender/SmartScreen blocking the unsigned obscura.exe
                    # (the 07-30 screenshot). Health could never see that
                    # before because availability was an existence check; now
                    # the client probes load time, so health can say BLOCKED —
                    # the one status that tells the operator to act.
                    h.status = "BLOCKED"
                    h.detail = f"{resolved} — {reason[:80]}"
                else:
                    # Present but not runnable here — the honest answer, and
                    # the reason the fallback chain skips Obscura on this
                    # platform (e.g. the Windows .exe on Linux).
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
    for tool_health in tool_results:
        marker = (
            "✓"
            if tool_health.status == "OK"
            else ("⚠" if tool_health.status == "DEGRADED" else "✗")
        )
        print(
            f"  {marker} {tool_health.name:<20} "
            f"{tool_health.status:<10} {tool_health.detail[:36]}"
        )

    print("\n  MODEL TIERS:")
    print(f"  {'Tier':<22} {'Status':<10} {'Detail'}")
    print(f"  {'-'*22} {'-'*10} {'-'*36}")
    for tier_health in tier_results:
        marker = (
            "✓"
            if tier_health.status == "OK"
            else ("⚠" if tier_health.status == "DEGRADED" else "✗")
        )
        print(
            f"  {marker} {tier_health.name:<20} "
            f"{tier_health.status:<10} {tier_health.detail[:36]}"
        )

    ok_count = sum(1 for t in tool_results if t.status == "OK")
    deg_count = sum(1 for t in tool_results if t.status == "DEGRADED")
    # BLOCKED (D-10: OS refused to load the binary) counts as offline for the
    # summary — it needs operator action, not just a fallback tier.
    off_count = sum(1 for t in tool_results if t.status in ("OFFLINE", "BLOCKED"))
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
        # P5.2 (overhaul §6 P5 / A-12): ONE score scale everywhere. The
        # weighted total is on a 1-5 rubric; the threshold (4.0) is a
        # comparison line, not a denominator. Displaying "3.2/4.0" here while
        # the CLI shows "3.2/5.0" is the audit's scale inconsistency. The
        # scale is always /5.0; the threshold is stated next to it.
        threshold = getattr(qs, "threshold", 4.0) or 4.0
        print(_format_quality_line(
            qs.total_score,
            threshold=threshold,
            iterations=getattr(result, 'quality_iterations', '?'),
        ))
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

    # F-05b: surface the search budget so silent mid-engagement exhaustion
    # (the Aug 9 "total collected: N" plateau) is visible at run end.
    try:
        from hyperion.tools.searxng import SearxNGClient

        budget = SearxNGClient.budget_snapshot()
        used = int(budget.get("used", 0))
        cap = int(budget.get("cap", 0))
        exhausted = bool(budget.get("exhausted"))
        status = "EXHAUSTED" if exhausted else "OK"
        print(f"  Search:      {used}/{cap} ({status})")
        if exhausted:
            print("  ⚠ SEARCH BUDGET EXHAUSTED — later searches returned empty; "
                  "raise SEARCH_BUDGET_CAP or reduce query fan-out.")
        owners = budget.get("owners_exhausted")
        if owners:
            print(f"  ⚠ Per-owner budget exhausted: {', '.join(sorted(owners))}")
    except Exception as exc:  # noqa: BLE001 - health table must never crash
        print(f"  ⚠ search budget render failed: {exc}")

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
