"""
HYPERION Workflow Schemas — the dynamic engagement DAG.

This is not a fixed pipeline. The Engagement Director analyzes the question,
selects the right agents, and builds a custom DAG of tasks with dependencies.
No two engagements look the same. A market entry question spawns Market +
Competitive + Financial + Risk + Consumer. An M&A question spawns M&A +
Financial + Regulatory + Strategy. A pricing question spawns Market + Financial
+ Consumer + Competitive — no Risk, no Regulatory. (ARCHITECTURE.md §4.9)

The DAG supports:
- Parallel execution for tasks with no dependencies (asyncio.gather)
- Sequential execution for tasks with dependencies
- Adaptive replanning — spawn new agents mid-engagement
- Topological sort for execution order
- Budget-aware tier assignment per task
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from hyperion.config import ModelTier
from hyperion.schemas.agents import AgentName, SubAgentSpec


# ─────────────────────────────────────────────────────────────────────────────
# Question Classification (ARCHITECTURE.md §4.9, Agent 1)
# ─────────────────────────────────────────────────────────────────────────────


class QuestionType(str, Enum):
    """The 6 question types the Engagement Director classifies into.

    This determines which specialists to spawn. Not all 12 are spawned
    every time — that would waste resources. A pricing question needs
    Financial + Market + Consumer, not Regulatory + M&A. (§4.3, Agent 1)
    """

    GO_NO_GO = "go_no_go"          # Should we enter/acquire/launch?
    COMPARISON = "comparison"       # Compare options A vs B vs C
    FORECAST = "forecast"           # What will happen in market X by year Y?
    DIAGNOSTIC = "diagnostic"       # Why is X happening / what's wrong?
    OPTIMIZATION = "optimization"   # How do we improve X?
    GENERAL = "general"             # Broad strategic question


# ─────────────────────────────────────────────────────────────────────────────
# Research Domain — a research area identified by the Engagement Director
# ─────────────────────────────────────────────────────────────────────────────


class ResearchDomain(BaseModel):
    """A research domain identified during question decomposition.

    The Engagement Director breaks the question into research domains,
    each mapped to a primary specialist agent. Domains have priority
    levels (1=critical, 2=high, 3=standard) that influence tier
    assignment and execution order. (§4.9)
    """

    id: str = Field(description="Unique domain identifier (e.g. 'domain_1')")
    name: str = Field(description="Human-readable domain name")
    question: str = Field(description="The specific question this domain investigates")
    primary_agent: AgentName = Field(description="Which agent leads this domain")
    priority: int = Field(default=3, description="Priority 1 (critical) to 3 (standard)")
    rationale: str = Field(default="", description="Why this domain was selected")


# ─────────────────────────────────────────────────────────────────────────────
# Task Node — a single unit of work in the DAG
# ─────────────────────────────────────────────────────────────────────────────


class TaskStatus(str, Enum):
    """Status of a task in the workflow DAG."""

    PENDING = "pending"         # Not yet started
    READY = "ready"             # Dependencies met, ready to dispatch
    RUNNING = "running"         # Currently executing
    COMPLETED = "completed"     # Finished successfully
    FAILED = "failed"           # Errored out
    CANCELLED = "cancelled"     # Cancelled by Engagement Director
    ESCALATED = "escalated"     # Agent escalated an issue to the Director


class TaskNode(BaseModel):
    """A single task in the engagement workflow DAG.

    Each task maps to one agent doing one piece of analysis. The task
    specifies which agent, what tier, what the task is, what it depends
    on, and what it produces.

    Dependencies are task IDs — a task cannot start until all its
    dependencies are COMPLETED. Tasks with no dependencies run in
    parallel via asyncio.gather (§4.9).
    """

    id: str = Field(description="Unique task identifier within this DAG")
    agent: AgentName = Field(description="Which agent executes this task")
    model_tier: ModelTier = Field(description="Intelligence tier for this task")
    description: str = Field(description="What the agent needs to do")
    dependencies: list[str] = Field(
        default_factory=list,
        description="Task IDs that must complete before this task starts"
    )
    status: TaskStatus = TaskStatus.PENDING
    sub_agents: list[SubAgentSpec] = Field(
        default_factory=list,
        description="Sub-agents spawned by this task's agent"
    )
    output: dict[str, Any] | None = Field(
        default=None,
        description="Structured output from the agent (typed Pydantic model dict)"
    )
    estimated_llm_calls: int = Field(default=5, description="Estimated LLM calls for this task")
    estimated_tokens: int = Field(default=5000, description="Estimated token consumption")
    started_at: float | None = Field(default=None, description="Unix timestamp when task started")
    completed_at: float | None = Field(default=None, description="Unix timestamp when task completed")
    error: str | None = Field(default=None, description="Error message if task failed")

    @property
    def is_ready(self) -> bool:
        """Check if this task is ready to run (pending and no unmet dependencies)."""
        return self.status == TaskStatus.PENDING

    @property
    def is_terminal(self) -> bool:
        """Check if this task is in a terminal state."""
        return self.status in (
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        )


# ─────────────────────────────────────────────────────────────────────────────
# Workflow DAG — the complete engagement plan
# ─────────────────────────────────────────────────────────────────────────────


class WorkflowDAG(BaseModel):
    """The complete workflow DAG for a single engagement.

    Built by the Engagement Director after decomposing the question.
    This is the single most important planning artifact — it determines
    which agents run, in what order, at what tier, and with what
    dependencies. No two engagements produce the same DAG. (§4.9)

    The DAG supports:
    - Topological sort for execution order
    - Parallel execution for independent tasks
    - Adaptive replanning (add/remove tasks mid-engagement)
    - Budget tracking (estimated LLM calls + tokens)
    """

    engagement_id: str = Field(description="Unique engagement identifier")
    question: str = Field(description="The original business question")
    question_type: QuestionType = Field(description="Classified question type")
    tasks: list[TaskNode] = Field(default_factory=list, description="All tasks in the DAG")
    agents_selected: list[AgentName] = Field(
        default_factory=list,
        description="Specialists selected by the Director for this engagement",
    )
    estimated_total_llm_calls: int = Field(description="Total estimated LLM calls")
    estimated_total_tokens: int = Field(description="Total estimated token consumption")
    estimated_duration_minutes: float = Field(description="Estimated engagement duration")
    adapted: bool = Field(default=False, description="Whether the DAG was adapted mid-engagement")
    adaptation_log: list[str] = Field(
        default_factory=list,
        description="Log of adaptations made during engagement (escalations, reroutes, tier changes)",
    )

    def get_task(self, task_id: str) -> TaskNode | None:
        """Get a task by ID."""
        return next((t for t in self.tasks if t.id == task_id), None)

    def get_tasks_for_agent(self, agent: AgentName) -> list[TaskNode]:
        """Get all tasks assigned to a specific agent."""
        return [t for t in self.tasks if t.agent == agent]

    def get_ready_tasks(self) -> list[TaskNode]:
        """Get all tasks that are ready to run.

        A task is ready when all its dependencies have reached a terminal
        state (COMPLETED or FAILED). This allows downstream agents like
        Synthesis Lead to run with partial findings when some specialists fail.
        """
        terminal_ids = {
            t.id for t in self.tasks
            if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED)
        }
        return [
            t for t in self.tasks
            if t.status == TaskStatus.PENDING
            and all(dep in terminal_ids for dep in t.dependencies)
        ]

    def cascade_failed_dependencies(self) -> list[TaskNode]:
        """Mark pending tasks as failed if any dependency failed.

        Without this, a failed dependency causes dependents to deadlock
        (they never become ready because the dep never reaches COMPLETED).
        Returns the list of tasks that were newly marked as failed.
        """
        failed_ids = {t.id for t in self.tasks if t.status == TaskStatus.FAILED}
        newly_failed: list[TaskNode] = []
        for task in self.tasks:
            if task.status == TaskStatus.PENDING:
                if any(dep in failed_ids for dep in task.dependencies):
                    task.status = TaskStatus.FAILED
                    task.error = "Upstream dependency failed"
                    newly_failed.append(task)
        return newly_failed

    def get_running_tasks(self) -> list[TaskNode]:
        """Get all currently running tasks."""
        return [t for t in self.tasks if t.status == TaskStatus.RUNNING]

    def add_task(self, task: TaskNode) -> None:
        """Add a task to the DAG (for adaptive replanning)."""
        self.tasks.append(task)
        self._recalculate_estimates()

    def remove_task(self, task_id: str) -> None:
        """Remove a task from the DAG (for adaptive replanning)."""
        self.tasks = [t for t in self.tasks if t.id != task_id]
        # Remove this task from any dependency lists
        for t in self.tasks:
            if task_id in t.dependencies:
                t.dependencies.remove(task_id)
        self._recalculate_estimates()

    def topological_sort(self) -> list[str]:
        """Return task IDs in topological order (dependencies before dependents).

        Uses Kahn's algorithm. Raises ValueError if the DAG has cycles.
        """
        # Build adjacency list and in-degree count
        in_degree: dict[str, int] = {t.id: 0 for t in self.tasks}
        adj: dict[str, list[str]] = {t.id: [] for t in self.tasks}

        for t in self.tasks:
            for dep in t.dependencies:
                adj[dep].append(t.id)
                in_degree[t.id] += 1

        # Start with nodes that have no dependencies
        queue: list[str] = [tid for tid, deg in in_degree.items() if deg == 0]
        result: list[str] = []

        while queue:
            node = queue.pop(0)
            result.append(node)
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if len(result) != len(self.tasks):
            raise ValueError("Workflow DAG has a cycle — cannot topologically sort")

        return result

    @property
    def is_complete(self) -> bool:
        """Check if all tasks are in terminal states."""
        return all(t.is_terminal for t in self.tasks)

    @property
    def all_completed(self) -> bool:
        """Check if all tasks completed successfully."""
        return all(t.status == TaskStatus.COMPLETED for t in self.tasks)

    def _recalculate_estimates(self) -> None:
        """Recalculate total estimates after adding/removing tasks."""
        self.estimated_total_llm_calls = sum(t.estimated_llm_calls for t in self.tasks)
        self.estimated_total_tokens = sum(t.estimated_tokens for t in self.tasks)


# ─────────────────────────────────────────────────────────────────────────────
# Engagement Metadata
# ─────────────────────────────────────────────────────────────────────────────


class EngagementMetadata(BaseModel):
    """Metadata about an engagement, for the methodology page and vault.

    The methodology page of the PDF report (§6.1) includes:
    - Agents used (which specialists were spawned and why)
    - Sources accessed (count by type)
    - Data points collected
    - Confidence breakdown by domain
    - Limitations
    """

    engagement_id: str
    question: str
    question_type: QuestionType
    agents_used: list[AgentName] = Field(default_factory=list)
    sources_accessed: int = 0
    data_points_collected: int = 0
    duration_seconds: float = 0.0
    llm_calls_made: int = 0
    tokens_consumed: int = 0
    sub_agents_spawned: int = 0
    quality_iterations: int = 0
    final_quality_score: float | None = None
