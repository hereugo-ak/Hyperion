"""HYPERION TUI widgets — logo, header, transcript, metrics, prompt, rules.

All widgets are built on Textual ``Content`` and native selectable widgets
(``Static`` / ``RichLog``), so every surface is copyable.
"""

from hyperion.tui.banner import WORDMARK, hint_content, logo_content, roster_content
from hyperion.tui.widgets.header import HeaderBar
from hyperion.tui.widgets.metrics import AgentState, MetricsRail, Telemetry
from hyperion.tui.widgets.prompt import (
    CancelTurn,
    ClearScrollback,
    PromptBar,
    PromptSubmitted,
)
from hyperion.tui.widgets.rule import HR_WIDTH, PhaseRule, Rule, hr
from hyperion.tui.widgets.transcript import LogRow, Transcript

# Backwards-compatible alias — the old ScrollView log wasn't copyable; the
# RichLog-based Transcript is a drop-in replacement with the same API.
LogStream = Transcript

__all__ = [
    "HeaderBar",
    "WORDMARK",
    "logo_content",
    "roster_content",
    "hint_content",
    "Transcript",
    "LogStream",
    "LogRow",
    "MetricsRail",
    "Telemetry",
    "AgentState",
    "PromptBar",
    "PromptSubmitted",
    "ClearScrollback",
    "CancelTurn",
    "Rule",
    "PhaseRule",
    "hr",
    "HR_WIDTH",
]
