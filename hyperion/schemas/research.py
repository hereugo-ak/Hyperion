"""
HYPERION Research Schemas — the research decomposition tree.

When a specialist receives a task, it decomposes it into sub-questions,
spawns sub-agents for each, and collects structured findings. The research
tree tracks this decomposition — which sub-questions were asked, which
sub-agents answered them, what was found, and what gaps remain.

This is the context-window management strategy — not truncation, not
compression, but delegation with structured handoff. The specialist's
context window is used for synthesis, not for raw research. (§4.7)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field

from hyperion.config import ModelTier
from hyperion.schemas.agents import AgentName, SubAgentSpec, ToolName
from hyperion.schemas.models import ConfidenceLevel, KeyFinding, Source

# ─────────────────────────────────────────────────────────────────────────────
# Research Node Status
# ─────────────────────────────────────────────────────────────────────────────


class ResearchStatus(str, Enum):
    """Status of a research node in the decomposition tree."""

    PENDING = "pending"         # Not yet started
    IN_PROGRESS = "in_progress"  # Sub-agent is researching
    COMPLETED = "completed"      # Sub-agent returned findings
    TIMED_OUT = "timed_out"      # Sub-agent didn't return in 5 minutes
    FAILED = "failed"            # Sub-agent errored out
    SKIPPED = "skipped"          # Parent decided to skip this node


# ─────────────────────────────────────────────────────────────────────────────
# Research Brief — the input to a research sub-question
# ─────────────────────────────────────────────────────────────────────────────


class ResearchBrief(BaseModel):
    """A brief given to a sub-agent for a focused research sub-question.

    This is what the specialist sends to the junior agent. It contains:
    - The specific sub-question (focused, not broad)
    - The context from the parent (what's already known)
    - The tools the sub-agent can use (subset of parent's tools)
    - The findings model the sub-agent must produce (structured, not free text)
    - The tier to operate at (MICRO or FAST only)

    The sub-agent returns KeyFinding objects with data, sources, confidence,
    and gaps. The parent synthesizes these into its own analysis. (§4.7)
    """

    sub_question: str = Field(description="The focused sub-question to research")
    parent_context: str = Field(description="What the parent already knows — gives the sub-agent "
        "context")
    tools: list[ToolName] = Field(description="Subset of parent's tools needed for this "
        "sub-question")
    model_tier: ModelTier = Field(description="MICRO or FAST — don't burn STRONG/DEEP quota")
    expected_findings: list[str] = Field(
        default_factory=list,
        description="What kinds of findings the parent expects (e.g., 'TAM data', 'adoption rates')"
    )
    search_hints: list[str] = Field(
        default_factory=list,
        description="Suggested search terms or URLs to start with"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Research Node — a single node in the research decomposition tree
# ─────────────────────────────────────────────────────────────────────────────


class ResearchNode(BaseModel):
    """A single node in the research decomposition tree.

    Each specialist decomposes its task into sub-questions. Each sub-question
    becomes a ResearchNode. The node tracks:
    - The brief (what was asked)
    - The sub-agent spec (how it was dispatched)
    - The findings (what came back — structured, not free text)
    - The status (pending/in_progress/completed/timed_out/failed)
    - The gaps (what couldn't be found)

    If a sub-agent times out (5-min limit), the parent proceeds with
    available findings and flags the gap. (§4.7)
    """

    id: str = Field(description="Unique node identifier")
    brief: ResearchBrief = Field(description="The research brief for this node")
    sub_agent_spec: SubAgentSpec | None = Field(default=None, description="How the sub-agent was "
        "dispatched")
    status: ResearchStatus = ResearchStatus.PENDING
    findings: list[KeyFinding] = Field(default_factory=list, description="Structured findings "
        "returned")
    sources: list[Source] = Field(default_factory=list, description="All sources found by this "
        "node")
    gaps: list[str] = Field(default_factory=list, description="What this node couldn't find")
    confidence: ConfidenceLevel | None = Field(default=None, description="Confidence of findings")
    started_at: datetime | None = Field(default=None)
    completed_at: datetime | None = Field(default=None)
    error: str | None = Field(default=None, description="Error message if failed")
    token_usage: int = Field(default=0, description="Tokens consumed by this research node")

    @property
    def is_terminal(self) -> bool:
        """Check if this node is in a terminal state."""
        return self.status in (
            ResearchStatus.COMPLETED,
            ResearchStatus.TIMED_OUT,
            ResearchStatus.FAILED,
            ResearchStatus.SKIPPED,
        )

    @property
    def duration_seconds(self) -> float | None:
        """How long this research node took (or is taking)."""
        if self.started_at is None:
            return None
        end = self.completed_at or datetime.now()
        return (end - self.started_at).total_seconds()


# ─────────────────────────────────────────────────────────────────────────────
# Research Tree — the complete decomposition for one specialist
# ─────────────────────────────────────────────────────────────────────────────


class ResearchTree(BaseModel):
    """The complete research decomposition tree for a single specialist.

    When a specialist receives a task, it:
    1. Decomposes the task into 1-3 sub-questions
    2. Creates a ResearchNode for each sub-question
    3. Dispatches sub-agents (MICRO or FAST tier) for each node
    4. Waits for all sub-agents to return (or timeout)
    5. Synthesizes the findings into its own analysis model

    The tree tracks the full decomposition — which sub-questions were
    asked, which sub-agents answered them, what was found, and what
    gaps remain. This is the context isolation strategy: the specialist's
    context window is used for synthesis, not for raw research. (§4.7)
    """

    specialist: AgentName = Field(description="Which specialist owns this tree")
    task_description: str = Field(description="The original task from the Engagement Director")
    nodes: list[ResearchNode] = Field(default_factory=list, description="All research "
        "sub-question nodes")
    synthesized_findings: list[KeyFinding] = Field(
        default_factory=list,
        description="Findings the specialist synthesized from sub-agent results"
    )
    total_sources: int = Field(default=0, description="Total unique sources across all nodes")
    total_tokens: int = Field(default=0, description="Total tokens consumed across all nodes")
    total_gaps: list[str] = Field(default_factory=list, description="All gaps aggregated from all "
        "nodes")

    def add_node(self, node: ResearchNode) -> None:
        """Add a research node to the tree."""
        self.nodes.append(node)

    def get_completed_nodes(self) -> list[ResearchNode]:
        """Get all completed research nodes."""
        return [n for n in self.nodes if n.status == ResearchStatus.COMPLETED]

    def get_pending_nodes(self) -> list[ResearchNode]:
        """Get all pending/in-progress research nodes."""
        return [n for n in self.nodes if n.status in (ResearchStatus.PENDING, ResearchStatus.IN_PROGRESS)]

    def get_timed_out_nodes(self) -> list[ResearchNode]:
        """Get all nodes that timed out (5-min limit exceeded)."""
        return [n for n in self.nodes if n.status == ResearchStatus.TIMED_OUT]

    @property
    def all_complete(self) -> bool:
        """Check if all research nodes are in terminal states."""
        return all(n.is_terminal for n in self.nodes) if self.nodes else True

    @property
    def success_rate(self) -> float:
        """Fraction of nodes that completed successfully."""
        if not self.nodes:
            return 0.0
        completed = sum(1 for n in self.nodes if n.status == ResearchStatus.COMPLETED)
        return completed / len(self.nodes)

    def aggregate_sources(self) -> list[Source]:
        """Get all unique sources across all nodes."""
        seen_urls: set[str] = set()
        all_sources: list[Source] = []
        for node in self.nodes:
            for source in node.sources:
                if source.url not in seen_urls:
                    seen_urls.add(source.url)
                    all_sources.append(source)
        return all_sources

    def aggregate_gaps(self) -> list[str]:
        """Get all gaps across all nodes."""
        gaps: list[str] = []
        for node in self.nodes:
            gaps.extend(node.gaps)
        return gaps

    def finalize(self) -> None:
        """Finalize the tree after all nodes are terminal — aggregate stats."""
        self.total_sources = len(self.aggregate_sources())
        self.total_tokens = sum(n.token_usage for n in self.nodes)
        self.total_gaps = self.aggregate_gaps()
