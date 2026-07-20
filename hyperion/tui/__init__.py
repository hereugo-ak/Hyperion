"""HYPERION TUI — a premium terminal interface for the multi-agent orchestrator.

Spec: HYPERION_INTERFACE_SPEC.md. Terminal-first (Textual/Rich), truecolor,
hand-tuned motion, no AI-slop tropes.
"""

from hyperion.tui.app import HyperionApp, run

__all__ = ["HyperionApp", "run"]
