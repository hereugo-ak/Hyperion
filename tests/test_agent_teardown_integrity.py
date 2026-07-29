"""
Phase 5.1d — `BaseAgent.close()` must never silently swallow a teardown failure.

The defect this pins (live, proven before the fix):

    for tool_name, tool in self._tools.items():   # tool_name unused (B007)
        ...
        except (RuntimeError, OSError, Exception):  # <- catch-all in a tuple
            pass

Because `Exception` is a *member of the tuple*, that `except` clause is a bare
catch-all wearing a disguise: `(RuntimeError, OSError, Exception)` matches
every non-BaseException. Every tool whose `close()` raised — a leaked httpx
client, an orphaned Playwright browser process, a sqlite connection pool that
never released its file handle — vanished without a single log line. And
because `tool_name` was never referenced, no log line was even *possible*.

This is exactly the §0.3 silent-failure anti-pattern the audit identifies as
the root cause class of the original P0, sitting in the single method every
one of the 20 agents inherits.

What must hold after the fix:
  1. A raising tool is *logged*, at WARNING, with the tool's name in the record.
  2. One bad tool does not strand the others — teardown continues.
  3. `cleanup()` still runs (bus unsubscribe) even when a tool blew up.
  4. `asyncio.CancelledError` propagates: cancellation is control flow.
  5. Structural guard: no `except` clause anywhere in `hyperion/agents/base.py`
     may list a catch-all alongside narrow types, and no `except Exception:
     pass` may reappear.

Negative controls (see NC block at the bottom of the module docstring in the
commit message) deliberately reintroduce each defect and must fail.
"""

from __future__ import annotations

import ast
import asyncio
import logging
from pathlib import Path
from typing import Any

import pytest

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import AgentBus
from hyperion.schemas.agents import AgentName, ToolName

BASE_PY = Path(__file__).resolve().parents[1] / "hyperion" / "agents" / "base.py"


# ─────────────────────────────────────────────────────────────────────────
# Fixtures / doubles
# ─────────────────────────────────────────────────────────────────────────


class _RecordingTool:
    """A tool whose close() succeeds and records that it was called."""

    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _AsyncRecordingTool:
    """A tool with an async close()."""

    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _RaisingTool:
    """A tool that fails teardown — the leaked-resource case."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.called = False

    def close(self) -> None:
        self.called = True
        raise self._exc


class _AsyncRaisingTool:
    """A tool whose async close() fails after the coroutine is awaited."""

    def __init__(self, exc: BaseException) -> None:
        self._exc = exc
        self.called = False

    async def close(self) -> None:
        self.called = True
        raise self._exc


class _NoCloseTool:
    """A tool with no close() at all — must be skipped, not crash."""


class _NonCallableClose:
    """A tool where `close` is an attribute, not a method."""

    close = "not callable"


def _make_agent(tools: dict[ToolName, Any]) -> BaseAgent:
    """Build a minimal concrete BaseAgent with a private bus and given tools."""
    from hyperion.agents.engagement_director import ENGAGEMENT_DIRECTOR_SPEC

    class _Agent(BaseAgent):
        async def run(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
            return None

    agent = _Agent(spec=ENGAGEMENT_DIRECTOR_SPEC, bus=AgentBus(), router=_StubRouter())
    agent._tools = dict(tools)
    return agent


class _StubRouter:
    """Router stand-in — close() never touches the router."""

    async def route(self, *args: Any, **kwargs: Any) -> Any:  # pragma: no cover
        raise AssertionError("teardown tests must not call the router")


# ─────────────────────────────────────────────────────────────────────────


class TestCloseSucceedsOnHealthyTools:
    """Positive control: without this the failure tests could pass vacuously."""

    def test_sync_close_is_called(self) -> None:
        tool = _RecordingTool()
        agent = _make_agent({ToolName.SEARXNG: tool})
        asyncio.run(agent.close())
        assert tool.closed is True, "healthy sync tool was never closed"

    def test_async_close_is_awaited(self) -> None:
        tool = _AsyncRecordingTool()
        agent = _make_agent({ToolName.SEARXNG: tool})
        asyncio.run(agent.close())
        assert tool.closed is True, "async close() coroutine was never awaited"

    def test_tool_registry_is_cleared(self) -> None:
        agent = _make_agent({ToolName.SEARXNG: _RecordingTool()})
        asyncio.run(agent.close())
        assert agent._tools == {}, "closed tools must not stay in the registry"

    def test_tool_without_close_is_skipped(self) -> None:
        agent = _make_agent({ToolName.SEARXNG: _NoCloseTool()})
        asyncio.run(agent.close())  # must not raise
        assert agent._tools == {}

    def test_non_callable_close_attribute_is_skipped(self) -> None:
        agent = _make_agent({ToolName.SEARXNG: _NonCallableClose()})
        asyncio.run(agent.close())  # must not raise
        assert agent._tools == {}


class TestTeardownFailureIsLoggedNotSwallowed:
    """The core regression: a failing close() must produce a log record."""

    @pytest.mark.parametrize(
        "exc",
        [
            RuntimeError("event loop is closed"),
            OSError("[Errno 9] Bad file descriptor"),
            AttributeError("'NoneType' object has no attribute 'aclose'"),
            TypeError("close() takes 0 positional arguments"),
            ValueError("I/O operation on closed file"),
        ],
    )
    def test_sync_failure_emits_warning(
        self, exc: BaseException, caplog: pytest.LogCaptureFixture
    ) -> None:
        tool = _RaisingTool(exc)
        agent = _make_agent({ToolName.SEARXNG: tool})
        with caplog.at_level(logging.WARNING, logger="hyperion.agents.base"):
            asyncio.run(agent.close())
        assert tool.called is True
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert warnings, (
            f"tool close() raised {type(exc).__name__} and NOTHING was logged — "
            "the failure was swallowed (§0.3 silent-failure anti-pattern)"
        )

    def test_async_failure_emits_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        tool = _AsyncRaisingTool(RuntimeError("cannot reuse closed transport"))
        agent = _make_agent({ToolName.SEARXNG: tool})
        with caplog.at_level(logging.WARNING, logger="hyperion.agents.base"):
            asyncio.run(agent.close())
        assert tool.called is True
        assert [r for r in caplog.records if r.levelno >= logging.WARNING], (
            "async close() failure was swallowed"
        )

    def test_log_record_names_the_failing_tool(self, caplog: pytest.LogCaptureFixture) -> None:
        """`tool_name` must actually be used — B007 meant no log could name it."""
        agent = _make_agent({ToolName.SEARXNG: _RaisingTool(OSError("boom"))})
        with caplog.at_level(logging.WARNING, logger="hyperion.agents.base"):
            asyncio.run(agent.close())
        blob = " ".join(r.getMessage() for r in caplog.records)
        assert "searxng" in blob.lower() or "SEARXNG" in blob, (
            "the warning does not identify which tool failed to close — "
            f"an operator cannot act on it. Got: {blob!r}"
        )

    def test_log_record_carries_the_traceback(self, caplog: pytest.LogCaptureFixture) -> None:
        """exc_info=True: without the traceback the log is nearly useless."""
        agent = _make_agent({ToolName.SEARXNG: _RaisingTool(OSError("boom"))})
        with caplog.at_level(logging.WARNING, logger="hyperion.agents.base"):
            asyncio.run(agent.close())
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert any(r.exc_info for r in warnings), (
            "warning logged without exc_info — the traceback that identifies "
            "the leaked resource is gone"
        )

    def test_agent_name_is_in_the_record(self, caplog: pytest.LogCaptureFixture) -> None:
        agent = _make_agent({ToolName.SEARXNG: _RaisingTool(OSError("boom"))})
        with caplog.at_level(logging.WARNING, logger="hyperion.agents.base"):
            asyncio.run(agent.close())
        blob = " ".join(r.getMessage() for r in caplog.records)
        assert AgentName.ENGAGEMENT_DIRECTOR.value in blob or "engagement" in blob.lower(), (
            f"warning does not say which agent leaked: {blob!r}"
        )


class TestOneBadToolDoesNotStrandTheOthers:
    """Teardown is best-effort *across* tools, not abandoned at the first error."""

    def test_later_tools_still_close(self, caplog: pytest.LogCaptureFixture) -> None:
        bad = _RaisingTool(OSError("first tool explodes"))
        good_a = _RecordingTool()
        good_b = _AsyncRecordingTool()
        agent = _make_agent(
            {
                ToolName.SEARXNG: bad,
                ToolName.JINA: good_a,
                ToolName.WORLD_BANK: good_b,
            }
        )
        with caplog.at_level(logging.WARNING, logger="hyperion.agents.base"):
            asyncio.run(agent.close())
        assert good_a.closed is True, "a tool after the failing one was never closed"
        assert good_b.closed is True, "an async tool after the failing one was never closed"

    def test_every_failure_is_logged_not_just_the_first(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        agent = _make_agent(
            {
                ToolName.SEARXNG: _RaisingTool(OSError("a")),
                ToolName.JINA: _RaisingTool(RuntimeError("b")),
                ToolName.WORLD_BANK: _RaisingTool(ValueError("c")),
            }
        )
        with caplog.at_level(logging.WARNING, logger="hyperion.agents.base"):
            asyncio.run(agent.close())
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) >= 3, (
            f"3 tools failed to close but only {len(warnings)} warning(s) logged"
        )

    def test_registry_cleared_even_after_failures(self) -> None:
        agent = _make_agent({ToolName.SEARXNG: _RaisingTool(OSError("x"))})
        asyncio.run(agent.close())
        assert agent._tools == {}, "registry must be cleared even when teardown failed"

    def test_cleanup_still_runs_after_failure(self) -> None:
        """Bus unsubscribe must happen even if a tool blew up."""
        agent = _make_agent({ToolName.SEARXNG: _RaisingTool(OSError("x"))})
        ran: list[bool] = []
        original = agent.cleanup

        async def _tracking_cleanup() -> None:
            ran.append(True)
            await original()

        agent.cleanup = _tracking_cleanup  # type: ignore[method-assign]
        asyncio.run(agent.close())
        assert ran == [True], "cleanup() (bus unsubscribe) was skipped after a tool failure"


class TestCancellationPropagates:
    """CancelledError is control flow — absorbing it hangs shutdown."""

    def test_cancelled_error_is_not_absorbed(self) -> None:
        async def _scenario() -> None:
            agent = _make_agent({ToolName.SEARXNG: _RaisingTool(asyncio.CancelledError())})
            await agent.close()

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_scenario())

    def test_async_cancelled_error_is_not_absorbed(self) -> None:
        async def _scenario() -> None:
            agent = _make_agent(
                {ToolName.SEARXNG: _AsyncRaisingTool(asyncio.CancelledError())}
            )
            await agent.close()

        with pytest.raises(asyncio.CancelledError):
            asyncio.run(_scenario())

    def test_keyboard_interrupt_is_not_absorbed(self) -> None:
        """BaseException subclasses must never be caught by teardown."""

        async def _scenario() -> None:
            agent = _make_agent({ToolName.SEARXNG: _RaisingTool(KeyboardInterrupt())})
            await agent.close()

        with pytest.raises(KeyboardInterrupt):
            asyncio.run(_scenario())


# ─────────────────────────────────────────────────────────────────────────
# Structural guards — stop the *next* recurrence, not just this one
# ─────────────────────────────────────────────────────────────────────────


def _except_handlers(path: Path) -> list[tuple[ast.ExceptHandler, str]]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[ast.ExceptHandler, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler):
            out.append((node, f"{path.name}:{node.lineno}"))
    return out


def _handler_type_names(handler: ast.ExceptHandler) -> list[str]:
    if handler.type is None:
        return ["<bare>"]
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names: list[str] = []
    for n in nodes:
        if isinstance(n, ast.Name):
            names.append(n.id)
        elif isinstance(n, ast.Attribute):
            names.append(n.attr)
        else:
            names.append(ast.dump(n))
    return names


CATCH_ALLS = {"Exception", "BaseException", "<bare>"}


class TestNoDisguisedCatchAllInBase:
    """The exact shape of the defect: a catch-all hidden inside a tuple."""

    def test_no_catch_all_mixed_with_narrow_types(self) -> None:
        offenders: list[str] = []
        for handler, where in _except_handlers(BASE_PY):
            names = _handler_type_names(handler)
            if len(names) > 1 and CATCH_ALLS.intersection(names):
                offenders.append(f"{where}: except ({', '.join(names)})")
        assert not offenders, (
            "A catch-all listed alongside narrow exception types is a bare "
            "catch-all in disguise — the narrow names are decoration and every "
            "error is swallowed:\n  " + "\n  ".join(offenders)
        )

    def test_close_has_no_bare_except(self) -> None:
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "close":
                for handler in [h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)]:
                    names = _handler_type_names(handler)
                    assert not CATCH_ALLS.intersection(names), (
                        f"BaseAgent.close() line {handler.lineno} catches "
                        f"{names} — resource-cleanup failures must be narrow + logged"
                    )
                return
        pytest.fail("BaseAgent.close() not found — did the method get renamed?")

    def test_no_except_body_is_a_lone_pass_in_close(self) -> None:
        """`except ...: pass` is the audited §0.3 anti-pattern."""
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "close":
                for handler in [h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)]:
                    body = handler.body
                    is_lone_pass = len(body) == 1 and isinstance(body[0], ast.Pass)
                    is_lone_reraise = (
                        len(body) == 1
                        and isinstance(body[0], ast.Raise)
                        and body[0].exc is None
                    )
                    assert (not is_lone_pass) or is_lone_reraise, (
                        f"BaseAgent.close() line {handler.lineno}: "
                        "`except ...: pass` discards a teardown failure silently"
                    )
                return
        pytest.fail("BaseAgent.close() not found")

    def test_close_logs_inside_its_handler(self) -> None:
        """A narrow catch that still does nothing is no better than the bug."""
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "close":
                handlers = [h for h in ast.walk(node) if isinstance(h, ast.ExceptHandler)]
                assert handlers, "close() no longer guards tool teardown at all"
                logging_handlers = 0
                for handler in handlers:
                    src = "\n".join(ast.dump(s) for s in handler.body)
                    if "logger" in src or "'raise'" in src or isinstance(
                        handler.body[0], ast.Raise
                    ):
                        logging_handlers += 1
                assert logging_handlers == len(handlers), (
                    "an except handler in close() neither logs nor re-raises"
                )
                return
        pytest.fail("BaseAgent.close() not found")


class TestLoopVariableIsUsed:
    """B007: `tool_name` unused meant the log could not name the tool."""

    def test_close_references_tool_name(self) -> None:
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.AsyncFunctionDef) and node.name == "close":
                loops = [n for n in ast.walk(node) if isinstance(n, ast.For)]
                assert loops, "close() no longer iterates the tool registry"
                for loop in loops:
                    targets = {
                        t.id for t in ast.walk(loop.target) if isinstance(t, ast.Name)
                    }
                    if "tool_name" not in targets:
                        continue
                    used = {
                        n.id
                        for n in ast.walk(ast.Module(body=loop.body, type_ignores=[]))
                        if isinstance(n, ast.Name) and isinstance(n.ctx, ast.Load)
                    }
                    assert "tool_name" in used, (
                        "close() unpacks `tool_name` but never uses it (ruff B007) — "
                        "so no diagnostic can say which tool leaked"
                    )
                return
        pytest.fail("BaseAgent.close() not found")


class TestEmptyOverridableMethodIsDocumented:
    """B027: `_handle_bus_message` empty body in an ABC must be intentional."""

    def test_handle_bus_message_is_not_an_empty_pass(self) -> None:
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_handle_bus_message"
            ):
                body = [s for s in node.body if not isinstance(s, ast.Expr)] or node.body
                # Strip the docstring
                stmts = node.body[1:] if (
                    node.body
                    and isinstance(node.body[0], ast.Expr)
                    and isinstance(node.body[0].value, ast.Constant)
                ) else node.body
                assert stmts, "_handle_bus_message has no body at all"
                assert not all(isinstance(s, ast.Pass) for s in stmts), (
                    "_handle_bus_message is a bare `pass` in an abstract base "
                    "(ruff B027): a message routed here disappears with no trace. "
                    "Either mark it @abstractmethod or trace the drop."
                )
                assert body is not None
                return
        pytest.fail("_handle_bus_message not found")

    def test_handle_bus_message_has_docstring(self) -> None:
        tree = ast.parse(BASE_PY.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.AsyncFunctionDef)
                and node.name == "_handle_bus_message"
            ):
                assert ast.get_docstring(node), (
                    "an intentionally-empty overridable method must document why"
                )
                return
        pytest.fail("_handle_bus_message not found")
