"""OVERHAUL5 W7 (D-11 / D-12) — checkpointed specialist timeouts + COMPETE lock.

The 08-12 run lost 20 minutes of completed work three times: OPS/ESG/
REGULATORY finished their pipelines and died only on the FINAL completion LLM
call (1200s wall). W7 classifies that as ``timeout_at_final_completion`` with
a partial output (published findings preserved) instead of a bare timeout.
The COMPETE ``content`` UnboundLocalError (D-12) was eliminated by the W5
ladder rewire; this file locks the happy-path behavior.

Fail-first: the timeout classification tests fail on pre-W7 code (no partial
output / no typed reason); the COMPETE lock test fails on the pre-W5 bespoke
loop (crashes or produces no sources).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from hyperion.agents.specialists.competitive_intel import CompetitiveIntel
from hyperion.orchestrator import WorkflowEngine
from hyperion.schemas.agents import AgentName, ModelTier
from hyperion.schemas.models import ConfidenceLevel, KeyFinding, Source, SourceCredibility
from hyperion.schemas.workflow import TaskNode


def _task() -> TaskNode:
    return TaskNode(
        id="task_operations_analyst",
        agent=AgentName.OPERATIONS_ANALYST,
        model_tier=ModelTier.STANDARD,
        description="Find operational benchmarks for manufacturing",
    )


def _finding(i: int) -> KeyFinding:
    return KeyFinding(
        id=f"f_{i}", agent="operations_analyst", finding_type="operational_benchmark",
        title=f"Capacity utilisation {i}",
        content="Capacity utilisation rose to 78 percent per ministry data.",
        sources=[
            Source(id=f"src_{i}", title=f"source {i}",
                   url=f"https://example.com/{i}",
                   credibility=SourceCredibility.GOVERNMENT)
        ],
        confidence=ConfidenceLevel.MEDIUM,
    )


def _orch() -> WorkflowEngine:
    obj = WorkflowEngine.__new__(WorkflowEngine)
    obj._task_outputs = {}
    obj._journal = None
    return obj


def test_timeout_with_findings_is_typed_final_completion() -> None:
    """[FF] A specialist that published findings before the wall gets a typed
    timeout_at_final_completion partial output — the evidence is preserved."""
    orch = _orch()
    agent = MagicMock()
    agent._findings = [_finding(1), _finding(2)]
    partial = orch._timeout_partial_output(_task(), agent)
    assert partial is not None
    assert partial["status"].startswith("timeout_at_final_completion")
    assert len(partial["findings"]) == 2
    assert partial["agent"] == "operations_analyst"


def test_timeout_without_findings_stays_bare() -> None:
    """[FF] A genuinely empty pipeline keeps the bare timeout classification —
    nothing was completed, nothing to checkpoint."""
    orch = _orch()
    agent = MagicMock()
    agent._findings = []
    assert orch._timeout_partial_output(_task(), agent) is None


@pytest.mark.asyncio
async def test_compete_scrape_happy_path_produces_sources() -> None:
    """[FF→lock] COMPETE's competitor scrape with a SUCCEEDING extractor must
    produce sources and pages without raising — pre-W5 this crashed with
    UnboundLocalError exactly when Obscura succeeded (08-12 run 14:56:52)."""
    agent = CompetitiveIntel.__new__(CompetitiveIntel)
    agent._competitor_urls = {}
    agent._scraped_pages = {}
    agent._sources = []

    async def _find_website(competitor: str) -> str:
        return "https://competitor.example.com"

    agent._find_competitor_website = _find_website  # type: ignore[method-assign]

    class _FakeExtractor:
        async def extract(self, urls: list[str], query: str = "") -> list[dict[str, str]]:
            return [
                {"url": urls[0], "content": "Product page content. " * 60,
                 "source": "fake"}
            ]

    agent.get_tool = MagicMock(return_value=_FakeExtractor())  # type: ignore[method-assign]

    await agent._scrape_competitor_sites(["Competitor A"])

    assert agent._sources, "a successful scrape must produce a citable source"
    assert agent._scraped_pages.get("Competitor A", {}).get("homepage"), (
        "scraped homepage content must be recorded"
    )
