"""HYPERION TUI widgets — logo, header, log stream, prompt, rules."""

from hyperion.tui.widgets.header import CollapsedIdentity, HeaderBar
from hyperion.tui.widgets.log_stream import LogRow, LogStream
from hyperion.tui.widgets.logo import (
    BANNER_LINE,
    SUBBANNER_LINE,
    WORDMARK,
    HyperionLogo,
)
from hyperion.tui.widgets.prompt import (
    CancelTurn,
    ClearScrollback,
    PromptBar,
    PromptSubmitted,
)
from hyperion.tui.widgets.rule import HR_WIDTH, PhaseRule, Rule, hr

__all__ = [
    "HeaderBar",
    "CollapsedIdentity",
    "HyperionLogo",
    "WORDMARK",
    "BANNER_LINE",
    "SUBBANNER_LINE",
    "LogStream",
    "LogRow",
    "PromptBar",
    "PromptSubmitted",
    "ClearScrollback",
    "CancelTurn",
    "Rule",
    "PhaseRule",
    "hr",
    "HR_WIDTH",
]
