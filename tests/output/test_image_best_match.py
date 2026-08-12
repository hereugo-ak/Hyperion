"""OVERHAUL4: image selection must pick the BEST topic match, never the
first search result — for the cover AND the section headers.

The audited behavior: the cover took ``_pick_unused_image`` (first
candidate); sections took the first gate-passing candidate. "First in the
Unsplash ranking" is not "best match" — the engine ranks by its own
signals, not by the report topic. Selection now scores every candidate
against the engagement subject + topic (ImageRelevanceGate) and picks the
highest scorer that is not already used.

Also locks the LLM-first section search-term flow: the content-aware LLM
term (from the actual section body/key insight) wins over the generic
hardcoded agent map.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from hyperion.agents.delivery.presentation_designer import PresentationDesigner
from hyperion.output.images import ImageRelevanceGate
from hyperion.tools.unsplash import UnsplashImage


def _img(desc: str, alt: str = "", photographer: str = "Photographer") -> UnsplashImage:
    return UnsplashImage(
        id=f"u_{abs(hash((desc, alt))) % 10_000}",
        description=desc,
        alt_description=alt,
        photographer=photographer,
    )


def _designer(tmp_path: Path) -> PresentationDesigner:
    designer = PresentationDesigner.__new__(PresentationDesigner)
    designer._used_image_ids = set()
    designer.IMAGE_DIR = str(tmp_path)
    designer._llm_complete = None  # type: ignore[assignment] - patched per test
    return designer


def _run(coro) -> object:
    return asyncio.run(coro)


class TestPickBestImage:
    def test_prefers_highest_scoring_candidate_not_first(self, tmp_path):
        """Cover/section selection: of three candidates, the one whose
        description matches the topic wins — even when it is NOT first in
        the search response."""
        d = _designer(tmp_path)
        gate = ImageRelevanceGate()
        candidates = [
            _img("A serene lake in the mountains at sunrise"),
            _img("Mumbai skyline at dusk over the bay"),
            _img("Abstract geometric shapes on a white background"),
        ]
        best = d._pick_best_image(
            candidates, gate=gate, subject="india investment", topic="mumbai skyline"
        )
        assert best is not None
        assert "skyline" in (best.description or ""), (
            "the topic-matched candidate must win, not the first result"
        )

    def test_rejects_everything_below_the_floor(self, tmp_path):
        """No candidate clears the relevance floor -> None (no image is
        better than a wrong image)."""
        d = _designer(tmp_path)
        gate = ImageRelevanceGate()
        candidates = [
            _img("Macro photograph of a butterfly wing"),
            _img("Abstract paint swirls on canvas"),
        ]
        best = d._pick_best_image(
            candidates, gate=gate, subject="india battery storage", topic="grid storage"
        )
        assert best is None

    def test_skips_already_used_best(self, tmp_path):
        """L5.17 dedup: if the highest scorer is already used (by the cover
        or an earlier section), the next-best is chosen."""
        d = _designer(tmp_path)
        best_img = _img("Mumbai skyline at dusk", alt="mumbai skyline")
        d._used_image_ids.add(best_img.id)
        gate = ImageRelevanceGate()
        candidates = [
            _img("Mumbai skyline at dusk", alt="mumbai skyline"),  # same topic, used
            _img("India flag waving on a hilltop", alt="india"),
        ]
        chosen = d._pick_best_image(
            candidates, gate=gate, subject="india investment", topic="mumbai india"
        )
        assert chosen is not None
        assert chosen.id != best_img.id or "flag" in (chosen.description or "")


class TestCoverSelectsBestMatch:
    def test_cover_picks_best_scored_candidate(self, tmp_path, monkeypatch):
        """End-to-end cover selection: the LLM writes the search term, and of
        the 8 candidates the topic-matched one is downloaded, not the first."""
        d = _designer(tmp_path)
        downloaded = tmp_path / "cover_downloaded.jpg"
        downloaded.write_bytes(b"fake-jpeg")

        on_topic = _img(
            "Mumbai skyline at dusk over the harbour", alt="mumbai skyline"
        )
        first_in_ranking = _img("Generic corporate office building", alt="office")

        class _Tool:
            async def search(self, query, per_page=5, orientation="landscape"):
                assert "skyline" in query or "india" in query.lower(), query
                assert per_page == 8
                return SimpleNamespace(
                    images=[first_in_ranking, on_topic, _img("a beach"), _img("a forest")]
                )

            async def download_image(self, img, quality="high"):
                assert img.id == on_topic.id, "must download the best match"
                return str(downloaded)

        monkeypatch.setattr(d, "get_tool", lambda name: _Tool())

        async def _llm(**kwargs):
            return SimpleNamespace(
                success=True, content='{"search_term": "mumbai skyline"}'
            )

        d._llm_complete = _llm  # type: ignore[assignment]
        selection = _run(
            d._select_cover_image(
                SimpleNamespace(
                    question="Should India invest in Mumbai infrastructure?",
                    sections=[],
                    recommendation=SimpleNamespace(value="investigate"),
                )
            )
        )

        assert selection is not None
        assert selection.unsplash_id == on_topic.id
        assert selection.search_term == "mumbai skyline"


class TestSectionTermsAreLLMFirst:
    def test_llm_term_wins_over_hardcoded_map(self, tmp_path, monkeypatch):
        """The content-aware LLM term is used even when the agent has a
        hardcoded map entry (the old flow short-circuited on the map)."""
        d = _designer(tmp_path)

        async def _llm(**kwargs):
            assert "Key insight" in kwargs.get("user_prompt", ""), (
                "the LLM prompt must carry the section content"
            )
            return SimpleNamespace(
                success=True, content='{"search_term": "semiconductor fab cleanroom"}'
            )

        d._llm_complete = _llm  # type: ignore[assignment]
        section = SimpleNamespace(
            agent="market_analyst",
            title="Semiconductor Manufacturing",
            key_insight="Fab capacity is the bottleneck",
            body="The foundry ecosystem in Gujarat is scaling fast...",
        )
        term = _run(d._generate_section_search_term(section))
        assert term == "semiconductor fab cleanroom"

    def test_llm_failure_falls_back_to_hardcoded(self, tmp_path):
        d = _designer(tmp_path)

        async def _llm(**kwargs):
            return SimpleNamespace(success=False, content=None)

        d._llm_complete = _llm  # type: ignore[assignment]
        section = SimpleNamespace(
            agent="risk_analyst",
            title="Risk Assessment",
            key_insight="",
            body="",
        )
        term = _run(d._generate_section_search_term(section))
        assert term  # hardcoded map fallback (never empty)
        assert term != "semiconductor fab cleanroom"
