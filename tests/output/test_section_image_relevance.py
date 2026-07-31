"""P2-33 / P2-G32: section imagery must be topic-relevant, not generic stock.

The measured defect: report B's Market Landscape chapter (a chemicals and
hardware manufacturing engagement) carried a crypto candlestick chart
photograph credited ``Source: Unsplash via Maxim Hopman``, and report A's
chapters carried the same class of decorative stock. The engagement subject
was never interpolated into the query, there was no relevance gate
(``output/images.py`` crops/warms/resizes whatever it is given), chart-like
photos were allowed as decoration, and the caption printed a photo credit
where a caption belongs.

Fix contract under test:
1. The section image query interpolates the engagement subject.
2. A relevance floor rejects below-threshold candidates (no image > wrong image).
3. Chart-like categories are banned outright for section decoration.
4. The caption is a caption, not a photo credit.
"""

from __future__ import annotations

import pytest

from hyperion.output.images import (
    CHART_LIKE_SECTION_BAN,
    ImageRelevanceGate,
)
from hyperion.tools.unsplash import UnsplashImage


def _img(desc: str, alt: str = "", photographer: str = "Some Person") -> UnsplashImage:
    return UnsplashImage(
        id=f"u_{abs(hash((desc, alt))) % 10_000}",
        description=desc,
        alt_description=alt,
        photographer=photographer,
    )


class TestQueryInterpolatesSubject:
    """Sub-fix 1: the query is built from the engagement subject."""

    def test_section_query_contains_subject(self):
        from hyperion.agents.delivery.presentation_designer import (
            PresentationDesigner,
        )

        q = PresentationDesigner.build_section_image_query(
            subject="Hexense Lab",
            geography="India",
            section_topic="chemicals manufacturing",
        )
        assert "hexense lab" in q.lower(), "query must interpolate the subject"
        assert "chemicals" in q.lower() or "manufacturing" in q.lower()

    def test_section_query_falls_back_without_geography(self):
        from hyperion.agents.delivery.presentation_designer import (
            PresentationDesigner,
        )

        q = PresentationDesigner.build_section_image_query(
            subject="Tesla",
            geography="",
            section_topic="EV market entry",
        )
        assert "tesla" in q.lower()
        assert "ev market entry" in q.lower() or "market entry" in q.lower()


class TestRelevanceGate:
    """Sub-fix 2: reject below-floor candidates; no image is better than wrong."""

    def setup_method(self):
        self.gate = ImageRelevanceGate()

    def test_relevant_candidate_accepted(self):
        cand = _img(
            desc="Automated chemicals manufacturing plant reactor vessels",
            alt="factory floor with stainless steel reaction tanks",
        )
        score = self.gate.score(cand, subject="Hexense Lab", topic="chemicals manufacturing")
        assert self.gate.is_relevant(cand, subject="Hexense Lab", topic="chemicals manufacturing"), (
            f"on-topic candidate scored {score:.3f}, must clear the floor"
        )

    def test_offtopic_crypto_chart_rejected(self):
        """The exact report-B failure: a candlestick chart photo for a
        manufacturing chapter must not pass."""
        cand = _img(
            desc="Stock market candlestick chart on a trading screen",
            alt="financial graphs glowing green and red",
        )
        assert not self.gate.is_relevant(cand, subject="Hexense Lab", topic="chemicals manufacturing")

    def test_no_candidate_is_acceptable(self):
        """When nothing clears the floor, the result is None (no image), never
        a below-floor wrong image."""
        candidates = [
            _img("abstract colourful paint swirl", "vivid acrylic texture"),
            _img("person hiking a mountain at sunrise", "hiker on a ridge"),
        ]
        chosen = self.gate.pick_relevant(
            candidates, subject="Hexense Lab", topic="chemicals manufacturing"
        )
        assert chosen is None, "below-floor candidates must yield no image"


class TestChartLikeBan:
    """Sub-fix 3: chart-like categories are banned for section decoration."""

    def test_chart_like_tokens_banned(self):
        gate = ImageRelevanceGate()
        for phrase in ("stock chart", "candlestick", "trading screen"):
            cand = _img(desc=f"a {phrase} displayed on a monitor")
            assert gate.is_chart_like(cand), f"{phrase!r} must be flagged chart-like"

    def test_chart_like_rejected_even_if_topical(self):
        gate = ImageRelevanceGate()
        cand = _img(
            desc="candlestick chart of market landscape data",
            alt="trading screen with financial market chart",
        )
        # Even with topical tokens present, a chart-like photo must not be used.
        assert not gate.is_relevant(cand, subject="market", topic="market landscape")

    def test_ban_set_non_empty(self):
        assert CHART_LIKE_SECTION_BAN
        assert any("candlestick" in t for t in CHART_LIKE_SECTION_BAN)


class TestCaptionIsCaption:
    """Sub-fix 4: the figcaption is a caption, not a photo credit."""

    def test_caption_not_a_photo_credit(self):
        from hyperion.agents.delivery.presentation_designer import (
            PresentationDesigner,
        )

        caption = PresentationDesigner.build_section_image_caption(
            section_title="Market Landscape",
            photographer="Maxim Hopman",
        )
        assert "Unsplash" not in caption
        assert "Source:" not in caption
        assert "Maxim Hopman" not in caption
        assert caption.strip(), "caption must be a real caption, not empty"

    def test_credit_goes_to_colophon(self):
        from hyperion.agents.delivery.presentation_designer import (
            PresentationDesigner,
        )

        credit = PresentationDesigner.build_image_credit(
            photographer="Maxim Hopman",
        )
        assert "Maxim Hopman" in credit
        assert "Unsplash" in credit
