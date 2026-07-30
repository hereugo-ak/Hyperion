"""HYPERION engagement preflight — refuse to start on a dead research stack (D-06/§4 0.2).

The 07-30 engagement ran for 1,216 seconds, made 95 LLM calls, and shipped a
fabricated report *because it was allowed to start at all*: every search
engine was dead (DuckDuckGo under a 24-hour 403 ban, Bing silent-zero) and
nothing checked that fact before the DAG was built. An engagement started
against a stack that returns zero evidence can only produce ungrounded
output — the honest response is to refuse, fast, with an actionable message.
"""

from __future__ import annotations

from typing import Any


class EngagementPreflightError(RuntimeError):
    """Raised when the research stack is too degraded to ground an engagement."""


def assert_research_stack_usable(
    settings: Any,
    *,
    health_result: Any | None = None,
) -> Any:
    """Return the SearXNG ToolHealth, or raise if it is OFFLINE.

    ``health_result`` lets a caller that already ran the startup health table
    pass the existing result in instead of paying for a second smoke query;
    when omitted, the check is run here.

    DEGRADED (some engines dead, some answering) is allowed through: breadth
    is reduced but the engagement is still groundable. OFFLINE (port closed,
    query failing, or fewer than MIN_SMOKE_RESULTS results) is not.
    """
    if health_result is None:
        from hyperion.obs.health import _check_searxng

        health_result = _check_searxng(settings)
    if health_result.status == "OFFLINE":
        raise EngagementPreflightError(
            "Research stack is offline — SearXNG returns no results. "
            "An engagement started now can only produce ungrounded output. "
            f"Detail: {health_result.detail}"
        )
    return health_result
