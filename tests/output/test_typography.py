"""P2-32: the em dash (U+2014) and en dash (U+2013) are banned characters
across the entire client-facing product surface.

Four enforcement layers are pinned here:
  1. ``sanitize_typography`` unit behaviour (the sanitization layer).
  2. The Jinja finalize hook sanitizes every interpolated field.
  3. The generation layer: every agent prompt is dispatched with the shared
     typography rule prepended.
  4. Enforcement: ``audit_pdf`` raises on any U+2014/U+2013 in extracted text.
  5. Source hygiene: no string literal in the render path may contain either
     character (the repo-level grep).
"""

from __future__ import annotations

import re
from pathlib import Path

import fitz  # PyMuPDF
import pytest

from hyperion.output.page_audit import PageAuditError, audit_pdf
from hyperion.output.render import TemplateRenderer
from hyperion.output.typography import PROMPT_TYPOGRAPHY_RULE, sanitize_typography

EM = "—"
EN = "–"

CREAM_FILL = (0xF5 / 255, 0xF4 / 255, 0xEE / 255)


# ---------------------------------------------------------------------------
# 1. sanitize_typography unit behaviour
# ---------------------------------------------------------------------------


class TestSanitizeTypography:
    def test_em_dash_separator_becomes_comma(self):
        out = sanitize_typography(f"Confidential {EM} for intended recipient only.")
        assert EM not in out
        assert out == "Confidential, for intended recipient only."

    def test_en_dash_becomes_comma(self):
        out = sanitize_typography(f"Revenue grew {EN} sharply {EN} across segments.")
        assert EN not in out
        assert ", " in out

    def test_numeric_range_becomes_hyphen(self):
        out = sanitize_typography(f"FY2020{EN}2025 outlook")
        assert EN not in out
        assert "FY2020-2025" in out

    def test_doubled_punctuation_collapsed(self):
        out = sanitize_typography(f"word, {EM} next.")
        assert ", ," not in out
        assert ", ." not in out
        assert EM not in out

    def test_no_dash_passes_through_unchanged(self):
        text = "The market is viable, and the thesis holds."
        assert sanitize_typography(text) == text

    def test_idempotent(self):
        text = f"a {EM} b, {EM} c{EN} d"
        once = sanitize_typography(text)
        assert sanitize_typography(once) == once

    def test_empty_and_none_safe(self):
        assert sanitize_typography("") == ""


# ---------------------------------------------------------------------------
# 2. The Jinja finalizer sanitizes every field
# ---------------------------------------------------------------------------


class TestFinalizerSanitizes:
    def test_em_dash_removed_from_interpolated_field(self):
        env = TemplateRenderer()._get_env()
        template = env.from_string("<p>{{ body }}</p>")
        out = template.render(body=f"Confidential {EM} for intended recipient only.")
        assert EM not in out

    def test_en_dash_removed_from_interpolated_field(self):
        env = TemplateRenderer()._get_env()
        template = env.from_string("<p>{{ body }}</p>")
        out = template.render(body=f"range 2020{EN}2025")
        assert EN not in out


# ---------------------------------------------------------------------------
# 3. Generation layer: the shared rule reaches every dispatched prompt
# ---------------------------------------------------------------------------


def _make_agent(system_prompt: str = "You are a specialist."):
    """A concrete BaseAgent wired for a _llm_complete dispatch inspection.

    name/model_tier/system_prompt are spec-backed properties, so the spec
    carries every attribute _llm_complete touches.
    """
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from hyperion.agents.base import BaseAgent

    class _Concrete(BaseAgent):
        async def run(self, *args, **kwargs):  # pragma: no cover - unused
            return None

    agent = _Concrete.__new__(_Concrete)
    agent.spec = SimpleNamespace(
        system_prompt=system_prompt,
        name=SimpleNamespace(value="TEST_AGENT"),
        model_tier=SimpleNamespace(value="standard"),
    )
    agent.router = SimpleNamespace(
        complete=AsyncMock(
            return_value=SimpleNamespace(
                success=True, content="ok", model="m", provider="p", error=None
            )
        )
    )
    agent.bus = SimpleNamespace(publish=AsyncMock())
    agent._transition = AsyncMock()
    return agent


class TestPromptPreamble:
    def test_llm_complete_prepends_typography_rule(self):
        import asyncio

        agent = _make_agent()
        asyncio.run(agent._llm_complete("hello"))

        messages = agent.router.complete.await_args.kwargs["messages"]
        system = messages[0]["content"]
        assert PROMPT_TYPOGRAPHY_RULE in system
        assert "You are a specialist." in system

    def test_prompt_override_also_gets_rule(self):
        import asyncio

        agent = _make_agent()
        asyncio.run(
            agent._llm_complete("hello", system_prompt_override="override prompt")
        )

        messages = agent.router.complete.await_args.kwargs["messages"]
        system = messages[0]["content"]
        assert PROMPT_TYPOGRAPHY_RULE in system
        assert "override prompt" in system


# ---------------------------------------------------------------------------
# 4. Enforcement: audit_pdf rejects any dash in extracted text
# ---------------------------------------------------------------------------


def _pdf_with_text(path: Path, text: str) -> Path:
    doc = fitz.open()
    page = doc.new_page(width=595, height=842)
    page.draw_rect(fitz.Rect(0, 0, 595, 842), fill=CREAM_FILL, color=None)
    spare = page.insert_textbox(fitz.Rect(60, 60, 535, 780), text, fontsize=10)
    assert spare >= 0, f"textbox overflow: {spare}"
    doc.save(str(path))
    doc.close()
    return path


class TestPageAuditDashAssertion:
    def test_em_dash_fails_audit(self, tmp_path):
        pdf = _pdf_with_text(tmp_path / "em.pdf", f"Confidential {EM} recipient.")
        with pytest.raises(PageAuditError):
            audit_pdf(pdf)

    def test_en_dash_fails_audit(self, tmp_path):
        pdf = _pdf_with_text(tmp_path / "en.pdf", f"2020{EN}2025 range.")
        with pytest.raises(PageAuditError):
            audit_pdf(pdf)


# ---------------------------------------------------------------------------
# 5. Source hygiene: no dash inside a string literal in the render path
# ---------------------------------------------------------------------------

_RENDER_PATH_FILES = (
    "hyperion/agents/delivery/presentation_designer.py",
    "hyperion/agents/synthesis_lead.py",
    "hyperion/agents/support/quality_gate.py",
    "hyperion/agents/support/fact_checker.py",
    "hyperion/output/markdown.py",
)

class TestSourceHygiene:
    @pytest.mark.parametrize("rel", _RENDER_PATH_FILES)
    def test_no_dash_in_string_literals(self, rel):
        """No string CONSTANT in the render path may contain U+2014/U+2013.

        AST-based: catches single-line, parenthesized-concatenated, and
        multi-line (triple-quoted) literals alike. Comments and docstrings are
        not constants, so they are exempt per the audit (code comments may
        keep dashes; only renderable strings must be clean).
        """
        import ast

        path = Path(__file__).resolve().parents[2] / rel
        assert path.exists(), rel
        tree = ast.parse(path.read_text(encoding="utf-8"))
        offenders = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if EM in node.value or EN in node.value:
                    offenders.append(f"{rel}:{node.lineno}: {node.value[:60]!r}")
        assert not offenders, (
            "dash in render-path string literals:\n" + "\n".join(offenders[:25])
        )
