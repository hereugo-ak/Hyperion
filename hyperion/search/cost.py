"""Session search-cost report (OVERHAUL4 P9).

Cost per provider = calls_total × cost_per_1000 / 1000, with
cost_per_1000 read from ``config/search_providers.yaml`` (the runtime
source of truth). Displayed at session end so the operator sees what the
search layer actually spent per provider, alongside the free SearXNG tier.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

#: provider name -> cost per 1000 queries (from config/search_providers.yaml)
_cost_table_cache: dict[str, float] | None = None


def _config_path() -> Path:
    try:
        from hyperion.infra.paths import project_root

        return project_root() / "config" / "search_providers.yaml"
    except Exception:  # noqa: BLE001 - fall back to cwd
        return Path("config") / "search_providers.yaml"


def search_cost_table() -> dict[str, float]:
    """Provider -> cost per 1000 queries. Never raises."""
    global _cost_table_cache
    if _cost_table_cache is not None:
        return _cost_table_cache
    table: dict[str, float] = {}
    try:
        data = yaml.safe_load(_config_path().read_text(encoding="utf-8"))
        for name, cfg in (data.get("providers") or {}).items():
            try:
                table[str(name).lower()] = float(cfg.get("cost_per_1000", 0.0) or 0.0)
            except (TypeError, ValueError):
                table[str(name).lower()] = 0.0
    except Exception as exc:  # noqa: BLE001 - cost display must never break quit
        logger.debug("search cost table unavailable: %s", exc)
    _cost_table_cache = table
    return table


def session_search_cost(
    metrics: dict[str, dict[str, Any]],
    cost_table: dict[str, float] | None = None,
) -> list[dict[str, Any]]:
    """Per-provider cost lines for one session's metrics snapshot.

    Covers EVERY provider in the cost table (zero-call providers included,
    so the report shows which wallets were used) plus any provider in the
    metrics snapshot that the config table does not know.
    """
    table = cost_table if cost_table is not None else search_cost_table()
    display_names = {
        "searxng": "SearXNG", "you": "You", "exa": "Exa",
        "tavily": "Tavily", "yep": "Yep",
    }
    provider_names: set[str] = set()
    provider_names.update(metrics.keys())
    for name in table.keys():
        provider_names.add(display_names.get(name.lower(), name.title()))
    lines: list[dict[str, Any]] = []
    for name in sorted(provider_names):
        m = metrics.get(name) or {}
        calls = int(m.get("calls_total", 0) or 0)
        per_1000 = table.get(name.lower(), 0.0)
        cost = calls * per_1000 / 1000.0
        lines.append({
            "provider": name,
            "calls": calls,
            "results": int(m.get("results_total", 0) or 0),
            "cost_per_1000": per_1000,
            "cost_usd": round(cost, 4),
        })
    lines.sort(key=lambda l: (-l["cost_usd"], l["provider"]))
    return lines


def format_search_cost_report(
    metrics: dict[str, dict[str, Any]],
    cost_table: dict[str, float] | None = None,
) -> str:
    """Human-readable session cost report (one line per provider + total)."""
    lines = session_search_cost(metrics, cost_table)
    if not lines:
        return "search layer: no provider activity this session"
    rows = []
    for l in lines:
        rows.append(
            f"  {l['provider']:<10s} calls={l['calls']:<5d} "
            f"results={l['results']:<5d} cost=${l['cost_usd']:.4f}"
        )
    total = round(sum(l["cost_usd"] for l in lines), 4)
    return (
        "SEARCH SESSION COST:\n"
        + "\n".join(rows)
        + f"\n  {'TOTAL':<10s} {'':>11s} {'':>13s} cost=${total:.4f}"
    )


def reset_cost_table_cache() -> None:
    """Drop the cached table (used by tests)."""
    global _cost_table_cache
    _cost_table_cache = None
