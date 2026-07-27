"""Regression tests for scroll-and-select copy in the TUI transcript.

These tests exist because of one specific, reproducible user complaint: you
could drag to highlight text in the transcript, but you could only ever copy
*what was visible on screen*. Dragging toward an edge did not carry the
selection into the scrolled-off history, and small drags selected everything.

Three separate defects combined to cause that, and each has a test group here.
Every test was written to fail against the pre-fix code:

1. ``render_line`` inherited from ``RichLog`` never called
   ``Strip.apply_offsets``. Without per-cell offset metadata the compositor
   cannot map a screen coordinate back to a document position, so
   ``Screen.get_widget_and_offset_at`` returned ``content_offset=None`` and the
   screen fell through to its ``SELECT_ALL`` branch. Consequence: every drag,
   however small, selected the whole widget, and there was no anchor to extend
   *from* when the pointer ran past an edge.

2. ``LogRow._line_index`` was captured as ``len(self.lines)`` at write time,
   but rendering is deferred until the widget's width is known — so every row
   written during mount recorded index 0 and rewriting any of them spliced over
   line 0.

3. Rewriting a live row spliced ``len(new_strips)`` entries over the old span.
   When a row *grew* (the normal case for a spinner line whose text got
   longer) the surplus physical lines were dropped and scrollback silently lost
   content.

Group 4 pins the copy plumbing itself, and group 5 pins that ``FindingsStream``
inherits the fix rather than re-introducing the RichLog bug.
"""

from __future__ import annotations

from textual.app import App, ComposeResult
from textual.geometry import Offset
from textual.selection import SELECT_ALL, Selection
from textual.strip import Strip

from hyperion.tui.widgets.findings_stream import FindingsStream
from hyperion.tui.widgets.transcript import LogRow, Transcript


def _offset_meta(strip: Strip) -> list[tuple[int, int]]:
    """Collect the selection offsets Textual stamped onto a strip's segments.

    ``Strip.apply_offsets`` attaches ``{"offset": (x, y)}`` style metadata to
    every segment, and the compositor reads exactly that metadata to turn a
    pointer position into a character position. Its presence is therefore the
    observable proof that precise selection is possible — which is why we
    assert on it instead of on a private cache attribute that upstream is free
    to rename.
    """
    found: list[tuple[int, int]] = []
    for segment in strip._segments:
        style = segment.style
        meta = style.meta if style is not None else {}
        if "offset" in meta:
            found.append(tuple(meta["offset"]))
    return found


def _fill(t: Transcript, n: int, badge: str = "INFO") -> None:
    for i in range(n):
        t.add_entry(badge, f"row {i:02d} payload text")


class _TranscriptApp(App):
    ALLOW_SELECT = True
    ENABLE_SELECT_AUTO_SCROLL = True

    def compose(self) -> ComposeResult:
        yield Transcript(id="log")


class _FindingsApp(App):
    ALLOW_SELECT = True
    ENABLE_SELECT_AUTO_SCROLL = True

    def compose(self) -> ComposeResult:
        yield FindingsStream(id="findings")


# ── 1. offsets: the root cause of "only copies the visible screen" ──────────


class TestRenderLineAppliesOffsets:
    """Without offsets, selection degrades to SELECT_ALL and cannot scroll."""

    async def test_strips_carry_cell_offsets(self):
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 20)
            await pilot.pause()

            strip = t.render_line(0)
            assert isinstance(strip, Strip)
            offsets = _offset_meta(strip)
            assert offsets, (
                "render_line returned a strip with no offset metadata — the "
                "compositor cannot map a pointer to a character, so drags "
                "collapse to SELECT_ALL and only the visible screen is copyable."
            )
            # Offsets must advance across the row, one per segment start.
            assert offsets == sorted(offsets), f"offsets not monotonic: {offsets}"

    async def test_offsets_track_the_scroll_position(self):
        """Screen row 0 must report its *document* row, not always 0.

        If offsets were emitted relative to the viewport, a selection made
        after scrolling would extract text from the wrong lines.
        """
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 60)
            await pilot.pause()

            t.scroll_to(y=0, animate=False)
            await pilot.pause()
            top_at_home = _offset_meta(t.render_line(0))

            t.scroll_to(y=20, animate=False)
            await pilot.pause()
            top_after_scroll = _offset_meta(t.render_line(0))

            assert top_at_home and top_after_scroll
            assert top_at_home[0][1] == 0
            assert top_after_scroll[0][1] == 20, (
                f"screen row 0 reported document row {top_after_scroll[0][1]} "
                "after scrolling to 20 — selections would extract wrong text."
            )

    async def test_screen_resolves_a_precise_content_offset(self):
        """A mouse-down must yield a real anchor, not None.

        ``content_offset=None`` is exactly what pushed ``Screen`` into its
        SELECT_ALL branch, so this is the assertion that pins the user-visible
        bug at its source.
        """
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 40)
            await pilot.pause()
            t.scroll_to(y=0, animate=False)
            await pilot.pause()

            await pilot.mouse_down(t, offset=(4, 1))
            await pilot.pause()

            state = pilot.app.screen._select_state
            assert state is not None and state.start is not None
            assert state.start.content_offset is not None, (
                "mouse-down produced no content offset — the screen would fall "
                "back to SELECT_ALL and the drag could not be extended."
            )


# ── 2. auto-scroll: dragging past the edge must keep selecting ──────────────


class TestSelectionSurvivesScrolling:
    async def test_widget_is_vertically_scrollable(self):
        """Auto-scroll-while-selecting requires an actually scrollable widget.

        ``Widget.allow_vertical_scroll`` gates Textual's auto-scroll. If CSS
        ever hides the scrollbar, auto-scroll dies silently and the user is
        back to "can only copy the visible screen".
        """
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 60)
            await pilot.pause()
            assert t.allow_vertical_scroll is True
            assert t.max_scroll_y > 0

    async def test_selection_can_exceed_the_viewport(self):
        """The whole point: a selection longer than one screen extracts fully."""
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 60)
            await pilot.pause()
            viewport = t.scrollable_content_region.height
            assert len(t.lines) > viewport, "test needs more lines than fit"

            plain = t._plain_lines()
            selection = Selection.from_offsets(
                Offset(0, 0), Offset(len(plain[-1]), len(plain) - 1)
            )
            text = t.selected_text(selection)
            assert len(text.splitlines()) > viewport, (
                "extraction was capped at the viewport — scrolled-off lines "
                "were not copyable."
            )
            assert "row 00" in text and "row 59" in text

    async def test_select_all_covers_scrolled_off_history(self):
        """SELECT_ALL must mean the whole buffer, not the rendered window."""
        async with _TranscriptApp().run_test(size=(70, 10)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 80)
            await pilot.pause()
            t.select_all()
            await pilot.pause()

            assert t.text_selection == SELECT_ALL
            text = t.selected_text(t.text_selection)
            assert "row 00" in text, "earliest row missing from select-all"
            assert "row 79" in text, "latest row missing from select-all"


# ── 3. the physical-line model invariants ──────────────────────────────────


class TestPhysicalLineModel:
    async def test_lines_and_plain_stay_in_lockstep(self):
        """Every selection offset assumes these two lists are index-aligned."""
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 30)
            await pilot.pause()
            assert len(t.lines) == len(t._plain_lines())

    async def test_row_indices_are_not_all_zero_after_mount(self):
        """Defect 2: deferred rendering made every mount-time row index 0."""
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            rows = [t.add_entry("INFO", f"row {i}") for i in range(6)]
            await pilot.pause()
            indices = [r._line_index for r in rows]
            assert indices == sorted(indices)
            assert len(set(indices)) == len(indices), (
                f"rows share physical indices {indices} — a rewrite would "
                "splice over the wrong line."
            )
            assert indices[0] >= 0

    async def test_growing_a_row_does_not_lose_lines(self):
        """Defect 3: a row that grew dropped its surplus physical lines."""
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            first = t.add_entry("INFO", "short")
            t.add_entry("INFO", "sentinel-after")
            await pilot.pause()
            before = len(t.lines)

            # Grow the row with detail lines, which adds physical lines.
            first.detail = ["detail one", "detail two", "detail three"]
            t.update_row(first, content="short")
            await pilot.pause()

            assert len(t.lines) == len(t._plain_lines())
            assert len(t.lines) > before, "row grew but buffer did not"
            joined = "\n".join(t._plain_lines())
            assert "sentinel-after" in joined, (
                "the following row was overwritten when the earlier row grew — "
                "scrollback silently lost content."
            )
            assert "detail three" in joined

    async def test_shrinking_a_row_keeps_the_buffer_consistent(self):
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            row = t.add_entry("INFO", "x", detail=["a", "b", "c"])
            t.add_entry("INFO", "sentinel-after")
            await pilot.pause()
            before = len(t.lines)

            row.detail = []
            t.update_row(row, content="x")
            await pilot.pause()

            assert len(t.lines) == len(t._plain_lines())
            assert len(t.lines) < before
            assert "sentinel-after" in "\n".join(t._plain_lines())

    async def test_later_row_indices_shift_when_an_earlier_row_grows(self):
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            first = t.add_entry("INFO", "first")
            later = t.add_entry("INFO", "later")
            await pilot.pause()
            later_before = later._line_index

            first.detail = ["d1", "d2"]
            t.update_row(first, content="first")
            await pilot.pause()

            assert later._line_index > later_before, (
                "an earlier row grew but the later row's index did not shift — "
                "the next rewrite would corrupt the log."
            )
            # And the index must still point at that row's own text.
            assert "later" in t._plain_lines()[later._line_index]

    async def test_rewrite_before_materialisation_is_safe(self):
        """Rows written before a width is known must not splice over line 0."""
        app = _TranscriptApp()
        async with app.run_test(size=(70, 12)) as pilot:
            t = app.query_one(Transcript)
            row = LogRow(badge="INFO", content="pre-size")
            t.add_row(row)
            t.update_row(row, content="pre-size updated")
            await pilot.pause()
            assert len(t.lines) == len(t._plain_lines())
            assert "pre-size updated" in "\n".join(t._plain_lines())

    async def test_reflow_on_resize_preserves_all_content(self):
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 25)
            await pilot.pause()

            # Force a genuine width change, which triggers a full reflow.
            await pilot.resize_terminal(40, 12)
            await pilot.pause()

            assert len(t.lines) == len(t._plain_lines())
            joined = "\n".join(t._plain_lines())
            assert "row 00" in joined and "row 24" in joined

    async def test_clear_empties_both_buffers(self):
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 15)
            await pilot.pause()
            t.clear()
            await pilot.pause()
            assert t.lines == []
            assert t._plain_lines() == []


# ── 4. copy plumbing ───────────────────────────────────────────────────────


class TestPartialSelectionExtraction:
    async def test_selection_of_a_line_range(self):
        async with _TranscriptApp().run_test(size=(70, 14)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 10)
            await pilot.pause()

            plain = t._plain_lines()
            # Whole lines 2..5 inclusive; reach past the last character of 5.
            selection = Selection.from_offsets(
                Offset(0, 2), Offset(len(plain[5]), 5)
            )
            text = t.selected_text(selection)
            assert "row 02" in text
            assert "row 05" in text
            assert "row 01" not in text
            assert "row 06" not in text

    async def test_selection_of_a_single_line(self):
        async with _TranscriptApp().run_test(size=(70, 14)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 10)
            await pilot.pause()
            plain = t._plain_lines()
            selection = Selection.from_offsets(
                Offset(0, 3), Offset(len(plain[3]), 3)
            )
            text = t.selected_text(selection)
            assert "row 03" in text
            assert "row 04" not in text

    async def test_no_selection_yields_empty_string(self):
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 5)
            await pilot.pause()
            assert t.text_selection is None
            assert t.selected_text(t.text_selection) == ""

    async def test_screen_get_selected_text_matches_the_widget(self):
        """The App copy action goes through the screen helper, so pin it."""
        async with _TranscriptApp().run_test(size=(70, 12)) as pilot:
            t = pilot.app.query_one(Transcript)
            _fill(t, 40)
            await pilot.pause()
            t.select_all()
            await pilot.pause()

            widget_text = t.selected_text(t.text_selection) or ""
            screen_text = pilot.app.screen.get_selected_text() or ""
            assert widget_text, "widget reported no selected text"
            assert screen_text, "screen reported no selected text"
            assert "row 00" in screen_text and "row 39" in screen_text
            assert len(screen_text.splitlines()) == len(widget_text.splitlines())


class TestSessionScreenCopyFallback:
    """The fallback used to re-call the same helper that had already failed."""

    def test_fallback_does_not_recurse_into_get_selected_text(self):
        """Inspect the compiled body, not the source text.

        Grepping ``inspect.getsource`` would also match the docstring that
        *explains* the old bug, so we look at the names the function actually
        references at runtime.
        """
        from hyperion.tui.screens.session import SessionScreen

        fn = SessionScreen.selected_transcript_text
        names = set(fn.__code__.co_names)
        assert "get_selected_text" not in names, (
            "selected_transcript_text still calls screen.get_selected_text — "
            "but App._gather_selection only reaches it *after* that same call "
            "returned nothing, so the fallback is a tautology that can never "
            "recover any text."
        )
        assert "text_selection" in names and "selected_text" in names, (
            "the fallback must ask the Transcript widget directly for the text "
            f"under its own selection; referenced names were {sorted(names)}"
        )


# ── 5. findings feed must inherit the fix, not the bug ─────────────────────


class TestFindingsStreamInheritsTheFix:
    def test_findings_stream_is_a_transcript(self):
        assert issubclass(FindingsStream, Transcript), (
            "FindingsStream subclasses RichLog again — RichLog.render_line "
            "never applies offsets, so every drag in the findings feed will "
            "collapse to SELECT_ALL."
        )

    async def test_findings_render_line_applies_offsets(self):
        async with _FindingsApp().run_test(size=(80, 12)) as pilot:
            f = pilot.app.query_one(FindingsStream)
            f.add_finding(agent="scout", badge="FINDING", title="t", snippet="s")
            await pilot.pause()
            assert _offset_meta(f.render_line(0)), (
                "FindingsStream strips carry no offset metadata — the findings "
                "feed would fall back to SELECT_ALL on every drag."
            )

    async def test_findings_are_selectable_beyond_the_viewport(self):
        async with _FindingsApp().run_test(size=(80, 10)) as pilot:
            f = pilot.app.query_one(FindingsStream)
            for i in range(30):
                f.add_finding(
                    agent="scout",
                    badge="FINDING",
                    title=f"finding {i:02d}",
                    snippet="evidence",
                    sources=3,
                    confidence="HIGH",
                )
            await pilot.pause()
            f.select_all()
            await pilot.pause()
            text = f.selected_text(f.text_selection) or ""
            assert "finding 00" in text
            assert "finding 29" in text

    async def test_clear_resets_findings_and_lines(self):
        async with _FindingsApp().run_test(size=(80, 12)) as pilot:
            f = pilot.app.query_one(FindingsStream)
            f.add_finding(agent="scout", badge="FINDING", title="t")
            await pilot.pause()
            assert f.get_findings()
            f.clear()
            await pilot.pause()
            assert f.get_findings() == []
            assert f.lines == []

    async def test_richlog_only_kwargs_are_tolerated(self):
        """Old call sites passed markup/highlight/wrap; they must not explode."""
        stream = FindingsStream(markup=False, highlight=False, wrap=True)
        assert isinstance(stream, Transcript)
        assert stream.auto_scroll is True


class TestAppSelectionConfig:
    """The App-level switches that make scroll-while-selecting possible."""

    def test_auto_scroll_and_select_are_enabled(self):
        from hyperion.tui.app import HyperionApp

        assert HyperionApp.ALLOW_SELECT is True
        assert HyperionApp.ENABLE_SELECT_AUTO_SCROLL is True, (
            "without ENABLE_SELECT_AUTO_SCROLL a drag that reaches the edge "
            "stops scrolling, so the selection cannot grow past one screen."
        )

    def test_auto_scroll_is_declared_explicitly(self):
        """Pin the intent, not just the effective value.

        ``ENABLE_SELECT_AUTO_SCROLL`` currently defaults to True upstream, so
        asserting the effective value alone would pass vacuously and would keep
        passing if a future Textual flipped that default. Requiring our own
        class to declare it makes the guarantee ours.
        """
        from hyperion.tui.app import HyperionApp

        assert "ENABLE_SELECT_AUTO_SCROLL" in HyperionApp.__dict__, (
            "HyperionApp relies on Textual's default for "
            "ENABLE_SELECT_AUTO_SCROLL; declare it so scroll-while-selecting "
            "cannot be silently switched off by a dependency upgrade."
        )
        assert "ALLOW_SELECT" in HyperionApp.__dict__

    def test_css_selector_names_the_transcript_widget(self):
        """Assert on the CSS *selectors*, with comments stripped.

        Two weaker assertions were tried and rejected. ``"Transcript" in
        HyperionApp.CSS`` passes even after the selector is reverted, because
        the word also appears in the comment that explains the rule. And
        asserting the computed ``text_wrap`` style is vacuous, because
        ``wrap`` is already Textual's default — the widget looks correct whether
        or not our rule matched. So we strip comments and inspect the selector
        text itself, which is the only thing that actually encodes the fix.
        """
        import re

        from hyperion.tui.app import HyperionApp

        css_no_comments = re.sub(r"/\*.*?\*/", "", HyperionApp.CSS, flags=re.S)
        selectors = [
            line.split("{")[0].strip()
            for line in css_no_comments.splitlines()
            if "{" in line
        ]
        wrapping = [s for s in selectors if "Transcript" in s]
        assert wrapping, (
            "no CSS rule selects Transcript. The wrap rule is still keyed only "
            "on RichLog, which no longer matches the ScrollView-based "
            f"Transcript. Selectors found: {selectors}"
        )
