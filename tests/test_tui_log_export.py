"""Tests for whole-log export (Ctrl+Shift+S / `/export`).

The user-visible contract: the ENTIRE transcript — logo, roster and every
event, including rows scrolled out of view — can be written to a file without
any selection, because selection+clipboard paths vary by terminal (some only
ever copy the visible screen). The `/export` command used to be a fake-success
stub: it logged "session transcript exported" and wrote nothing. These tests
pin the real write.

The plain-text surface for the write is ``Transcript.full_text`` — the same
physical-line mirror that selection extraction uses, joined back together — so
an export and a perfect select-all can never disagree about what "the whole
log" means.
"""

from __future__ import annotations

from textual.app import App, ComposeResult

from hyperion.tui.screens.session import export_transcript_text
from hyperion.tui.widgets.transcript import Transcript


def _fill(t: Transcript, n: int) -> None:
    for i in range(n):
        t.add_entry("INFO", f"row {i:02d} payload text")


class _TranscriptApp(App):
    def compose(self) -> ComposeResult:
        yield Transcript(id="log")


class TestTranscriptFullText:
    """The whole-log copy surface must cover scrolled-off history."""

    async def test_full_text_includes_scrolled_off_history(self):
        async with _TranscriptApp().run_test(size=(70, 10)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 80)
            await pilot.pause()
            assert len(t.lines) > t.scrollable_content_region.height, (
                "test needs more lines than the viewport holds"
            )
            text = t.full_text()
            assert "row 00" in text, "earliest row missing from full text"
            assert "row 79" in text, "latest row missing from full text"
            assert len(text.splitlines()) == len(t.lines), (
                "full_text and the physical-line mirror disagree — an export "
                "would omit wrapped lines"
            )

    async def test_full_text_equals_a_perfect_select_all(self):
        """Export and select-all must be the same text, by construction."""
        async with _TranscriptApp().run_test(size=(70, 10)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 40)
            await pilot.pause()
            t.select_all()
            await pilot.pause()
            selected = t.selected_text(t.text_selection) or ""
            assert t.full_text() == selected

    async def test_full_text_reflects_live_row_updates(self):
        """A spinner row that resolves must export its final content."""
        async with _TranscriptApp().run_test(size=(70, 10)) as pilot:
            t = pilot.app.query_one(Transcript)
            row = t.add_entry("INFO", "spinning…", spinner=True)
            _fill(t, 5)
            await pilot.pause()
            t.update_row(row, spinner=False, content="complete", icon="✓")
            await pilot.pause()
            text = t.full_text()
            assert "complete" in text
            assert "spinning…" not in text


class TestTranscriptExport:
    """The file-write path behind /export and Ctrl+Shift+S."""

    def test_export_writes_the_whole_log(self, monkeypatch, tmp_path):
        from hyperion.tui.screens import session as session_mod

        target = tmp_path / "tui_log_0xABC123.txt"
        monkeypatch.setattr(
            session_mod, "_transcript_export_path", lambda session_id: target
        )
        path = export_transcript_text("line one\nline two\n", "0xABC123")
        assert path == target
        assert path.read_text(encoding="utf-8") == "line one\nline two\n"

    def test_export_creates_the_diagnostics_directory(self, monkeypatch, tmp_path):
        from hyperion.tui.screens import session as session_mod

        target = tmp_path / "reports" / "diagnostics" / "tui_log_x.txt"
        monkeypatch.setattr(
            session_mod, "_transcript_export_path", lambda session_id: target
        )
        export_transcript_text("log", "x")
        assert target.parent.is_dir()
        assert target.read_text(encoding="utf-8") == "log\n", (
            "exported file must be a POSIX-clean text file (trailing newline)"
        )

    def test_export_preserves_an_existing_trailing_newline(self, monkeypatch, tmp_path):
        from hyperion.tui.screens import session as session_mod

        target = tmp_path / "tui_log_y.txt"
        monkeypatch.setattr(
            session_mod, "_transcript_export_path", lambda session_id: target
        )
        export_transcript_text("a\nb\n", "y")
        assert target.read_text(encoding="utf-8") == "a\nb\n", (
            "an already-newline-terminated export must not gain a blank line"
        )

    def test_export_path_lives_under_reports_diagnostics(self):
        """The real path helper must not land in the CWD or the repo root."""
        from hyperion.tui.screens.session import _transcript_export_path

        path = _transcript_export_path("0xABC123")
        assert "reports" in path.parts
        assert "diagnostics" in path.parts
        assert path.name.startswith("tui_log_0xABC123_")
        assert path.name.endswith(".txt")
