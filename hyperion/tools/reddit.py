"""
HYPERION Reddit Client — community sentiment and consumer discussion signals.

Reddit provides:
- Subreddit search and metadata (subscribers, description)
- Post search with sort options (relevance, hot, top, new, comments)
- Comment extraction for individual posts
- Time-filtered search (hour, day, week, month, year, all)
- Keyword-based sentiment analysis (no ML — positive/negative/neutral)

This is NOT a generic "search Reddit" wrapper. It:
- Uses the praw library with OAuth2 authentication
- 100 req/min rate limit (OAuth2)
- praw is synchronous — all calls wrapped in asyncio.to_thread()
- Returns structured RedditPost data for consumer sentiment analysis
- Provides keyword-based sentiment scoring (no ML dependency)
- Caches responses to minimize API calls
- Handles missing subreddits and API errors gracefully

Architecture reference: §5.1 — "Consumer sentiment, pain points,
reviews. OAuth2 via praw. 100 req/min. Used for consumer sentiment
analysis and market demand signals."

Tool selection logic (§5.2):
  Community sentiment task (consumer/broad):
    1. Reddit (primary — consumer/broad community) ← THIS
    2. HackerNews (complementary — developer/tech community)

Used by: Consumer Insights (consumer sentiment, pain points, reviews),
Market Analyst (market demand signals, consumer discussions) (§5.1)
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

MAX_CONTENT_CHARS = 15000
CACHE_TTL_SECONDS = 1800  # 30 min — Reddit discussions evolve
RATE_LIMIT_DELAY = 0.6  # 600ms = ~100 req/min


@dataclass
class Subreddit:
    """A Reddit subreddit from search results."""

    name: str = ""
    display_name: str = ""
    subscribers: int = 0
    active_users: int = 0
    description: str = ""
    over18: bool = False
    url: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "display_name": self.display_name,
            "subscribers": self.subscribers,
            "active_users": self.active_users,
            "description": self.description,
            "over18": self.over18,
            "url": self.url,
        }


@dataclass
class RedditPost:
    """A Reddit post from search results."""

    post_id: str = ""
    title: str = ""
    subreddit: str = ""
    author: str = ""
    score: int = 0
    upvote_ratio: float = 0.0
    num_comments: int = 0
    created_at: float = 0.0
    created_at_str: str = ""
    url: str = ""
    selftext: str = ""
    permalink: str = ""
    flair: str = ""
    is_self: bool = False
    over18: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "post_id": self.post_id,
            "title": self.title,
            "subreddit": self.subreddit,
            "author": self.author,
            "score": self.score,
            "upvote_ratio": self.upvote_ratio,
            "num_comments": self.num_comments,
            "created_at": self.created_at,
            "created_at_str": self.created_at_str,
            "url": self.url,
            "selftext": self.selftext,
            "permalink": self.permalink,
            "flair": self.flair,
            "is_self": self.is_self,
            "over18": self.over18,
        }


@dataclass
class RedditComment:
    """A Reddit comment on a post."""

    comment_id: str = ""
    text: str = ""
    author: str = ""
    score: int = 0
    created_at: float = 0.0
    created_at_str: str = ""
    parent_id: str = ""
    post_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "comment_id": self.comment_id,
            "text": self.text,
            "author": self.author,
            "score": self.score,
            "created_at": self.created_at,
            "created_at_str": self.created_at_str,
            "parent_id": self.parent_id,
            "post_id": self.post_id,
        }


@dataclass
class SentimentSummary:
    """Keyword-based sentiment summary of Reddit posts/comments."""

    positive_count: int = 0
    negative_count: int = 0
    neutral_count: int = 0
    total_analyzed: int = 0
    positive_pct: float = 0.0
    negative_pct: float = 0.0
    neutral_pct: float = 0.0
    key_themes: list[str] = field(default_factory=list)
    sentiment_score: float = 0.0  # -1.0 to 1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "positive_count": self.positive_count,
            "negative_count": self.negative_count,
            "neutral_count": self.neutral_count,
            "total_analyzed": self.total_analyzed,
            "positive_pct": self.positive_pct,
            "negative_pct": self.negative_pct,
            "neutral_pct": self.neutral_pct,
            "key_themes": self.key_themes,
            "sentiment_score": self.sentiment_score,
        }


class RedditClient:
    """Reddit community sentiment and discussion client.

    Provides access to subreddit search, post search, comments, and
    keyword-based sentiment analysis. Uses praw with OAuth2. (§5.1)

    Usage:
        client = RedditClient(settings=settings)

        # Search subreddits
        subs = await client.search_subreddits("machine learning", limit=5)
        for s in subs:
            print(f"r/{s.display_name} — {s.subscribers} subscribers")

        # Search posts
        posts = await client.search_posts("python vs rust", limit=15,
                                           sort="relevance", time_filter="year")
        for p in posts:
            print(f"{p.title} (r/{p.subreddit}, {p.score} pts)")

        # Get comments
        comments = await client.get_comments("abc123", limit=20)
        for c in comments:
            print(f"{c.author}: {c.text[:100]}")

        # Get sentiment summary
        sentiment = await client.get_sentiment(posts)
        print(f"Positive: {sentiment.positive_pct}%, "
              f"Negative: {sentiment.negative_pct}%")
    """

    REQUEST_TIMEOUT = 30
    MAX_RETRIES = 2
    RETRY_DELAY = 5

    # Sentiment keyword lists (keyword-based, no ML)
    POSITIVE_KEYWORDS = {
        "love", "great", "excellent", "amazing", "awesome", "fantastic",
        "perfect", "best", "wonderful", "recommend", "impressed", "happy",
        "satisfied", "delighted", "outstanding", "superb", "brilliant",
        "innovative", "breakthrough", "game-changer", "gamechanger",
        "disruptive", "revolutionary", "exciting", "promising", "strong",
        "bullish", "optimistic", "growth", "profitable", "efficient",
        "reliable", "seamless", "intuitive", "powerful", "robust",
    }
    NEGATIVE_KEYWORDS = {
        "hate", "terrible", "awful", "worst", "horrible", "disappointing",
        "frustrated", "angry", "broken", "buggy", "unreliable", "slow",
        "expensive", "overpriced", "scam", "fraud", "fail", "failure",
        "crash", "bug", "issue", "problem", "concern", "worried",
        "bearish", "pessimistic", "decline", "loss", "unprofitable",
        "inefficient", "confusing", "complicated", "difficult", "poor",
        "weak", "flawed", "disappointing", "underwhelming", "mediocre",
    }

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        self._reddit: Any | None = None
        self._cache: dict[str, tuple[float, Any]] = {}
        self._last_request_time: float = 0.0

        # Extract Reddit credentials from settings
        self._client_id = ""
        self._client_secret = ""
        self._user_agent = "hyperion_research/1.0"
        if settings:
            self._client_id = getattr(settings, "reddit_client_id", "") or ""
            self._client_secret = getattr(settings, "reddit_client_secret", "") or ""
            ua = getattr(settings, "reddit_user_agent", "")
            if ua:
                self._user_agent = ua

    def _get_reddit(self) -> Any:
        """Lazily initialize the praw Reddit instance."""
        if self._reddit is None:
            try:
                import praw
            except ImportError:
                logger.error("praw not installed. Install with: pip install praw")
                raise ImportError("praw is required for RedditClient. Install with: pip install praw>=7.7.0")

            if not self._client_id or not self._client_secret:
                logger.warning("Reddit credentials not configured. Set REDDIT_CLIENT_ID and REDDIT_CLIENT_SECRET in .env")

            self._reddit = praw.Reddit(
                client_id=self._client_id,
                client_secret=self._client_secret,
                user_agent=self._user_agent,
                check_for_async=False,
            )
        return self._reddit

    def _cache_key(self, *args: Any) -> str:
        key_str = ":".join(str(a) for a in args)
        return hashlib.md5(key_str.encode()).hexdigest()

    def _get_cached(self, key: str) -> Any | None:
        if key in self._cache:
            timestamp, data = self._cache[key]
            if time.time() - timestamp < CACHE_TTL_SECONDS:
                return data
            else:
                del self._cache[key]
        return None

    def _set_cached(self, key: str, data: Any) -> None:
        self._cache[key] = (time.time(), data)

    async def _rate_limit(self) -> None:
        """Enforce ~100 req/min rate limit (600ms between calls)."""
        elapsed = time.time() - self._last_request_time
        if elapsed < RATE_LIMIT_DELAY:
            await asyncio.sleep(RATE_LIMIT_DELAY - elapsed)
        self._last_request_time = time.time()

    # ─────────────────────────────────────────────────────────────────────
    # Subreddit Search
    # ─────────────────────────────────────────────────────────────────────

    async def search_subreddits(
        self,
        query: str,
        limit: int = 10,
    ) -> list[Subreddit]:
        """Search for subreddits by name/keyword.

        Args:
            query: Search query (e.g., "machine learning")
            limit: Maximum number of results

        Returns:
            List of Subreddit objects matching the query.
        """
        if not query or not query.strip():
            return []

        cache_key = self._cache_key("subs", query, limit)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        def _fetch() -> list[Subreddit]:
            reddit = self._get_reddit()
            results: list[Subreddit] = []

            try:
                for sub in reddit.subreddits.search(query, limit=limit):
                    results.append(Subreddit(
                        name=sub.display_name or "",
                        display_name=sub.display_name or "",
                        subscribers=sub.subscribers or 0,
                        active_users=getattr(sub, "active_user_count", 0) or 0,
                        description=sub.public_description or sub.description or "",
                        over18=sub.over18 or False,
                        url=f"https://reddit.com/r/{sub.display_name}" if sub.display_name else "",
                    ))
            except Exception as e:
                logger.warning("Reddit subreddit search failed: %s", e)

            return results

        try:
            result = await asyncio.to_thread(_fetch)
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            logger.warning("Reddit search_subreddits failed: %s", e)
            return []

    # ─────────────────────────────────────────────────────────────────────
    # Post Search
    # ─────────────────────────────────────────────────────────────────────

    async def search_posts(
        self,
        query: str,
        subreddit: str = "",
        sort: str = "relevance",
        time_filter: str = "year",
        limit: int = 15,
    ) -> list[RedditPost]:
        """Search for Reddit posts.

        Args:
            query: Search query
            subreddit: Optional subreddit to search within (e.g., "technology")
            sort: Sort order ("relevance", "hot", "top", "new", "comments")
            time_filter: Time filter ("hour", "day", "week", "month", "year", "all")
            limit: Maximum number of results

        Returns:
            List of RedditPost objects matching the query.
        """
        if not query or not query.strip():
            return []

        cache_key = self._cache_key("posts", query, subreddit, sort, time_filter, limit)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        def _fetch() -> list[RedditPost]:
            reddit = self._get_reddit()
            results: list[RedditPost] = []

            try:
                if subreddit:
                    search_target = reddit.subreddit(subreddit)
                else:
                    search_target = reddit

                for submission in search_target.search(
                    query,
                    sort=sort,
                    time_filter=time_filter,
                    limit=limit,
                ):
                    created_utc = submission.created_utc or 0.0
                    created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(created_utc)) if created_utc else ""

                    results.append(RedditPost(
                        post_id=submission.id or "",
                        title=submission.title or "",
                        subreddit=str(submission.subreddit) if submission.subreddit else "",
                        author=str(submission.author) if submission.author else "[deleted]",
                        score=submission.score or 0,
                        upvote_ratio=submission.upvote_ratio or 0.0,
                        num_comments=submission.num_comments or 0,
                        created_at=created_utc,
                        created_at_str=created_str,
                        url=submission.url or "",
                        selftext=(submission.selftext or "")[:MAX_CONTENT_CHARS],
                        permalink=f"https://reddit.com{submission.permalink}" if submission.permalink else "",
                        flair=submission.link_flair_text or "",
                        is_self=submission.is_self or False,
                        over18=submission.over_18 or False,
                    ))
            except Exception as e:
                logger.warning("Reddit post search failed: %s", e)

            return results

        try:
            result = await asyncio.to_thread(_fetch)
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            logger.warning("Reddit search_posts failed: %s", e)
            return []

    # ─────────────────────────────────────────────────────────────────────
    # Comments
    # ─────────────────────────────────────────────────────────────────────

    async def get_comments(
        self,
        post_id: str,
        limit: int = 20,
    ) -> list[RedditComment]:
        """Get comments for a specific Reddit post.

        Args:
            post_id: Reddit post ID (e.g., "abc123")
            limit: Maximum number of comments to return

        Returns:
            List of RedditComment objects.
        """
        if not post_id:
            return []

        cache_key = self._cache_key("comments", post_id, limit)
        cached = self._get_cached(cache_key)
        if cached is not None:
            return cached

        await self._rate_limit()

        def _fetch() -> list[RedditComment]:
            reddit = self._get_reddit()
            results: list[RedditComment] = []

            try:
                submission = reddit.submission(id=post_id)
                submission.comments.replace_more(limit=0)  # Remove "load more" stubs

                count = 0
                for comment in submission.comments.list():
                    if count >= limit:
                        break

                    if not hasattr(comment, "body") or not comment.body:
                        continue

                    created_utc = comment.created_utc or 0.0
                    created_str = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(created_utc)) if created_utc else ""

                    results.append(RedditComment(
                        comment_id=comment.id or "",
                        text=comment.body[:MAX_CONTENT_CHARS] if comment.body else "",
                        author=str(comment.author) if comment.author else "[deleted]",
                        score=comment.score or 0,
                        created_at=created_utc,
                        created_at_str=created_str,
                        parent_id=str(comment.parent_id) if comment.parent_id else "",
                        post_id=post_id,
                    ))
                    count += 1
            except Exception as e:
                logger.warning("Reddit get_comments failed: %s", e)

            return results

        try:
            result = await asyncio.to_thread(_fetch)
            self._set_cached(cache_key, result)
            return result
        except Exception as e:
            logger.warning("Reddit get_comments failed: %s", e)
            return []

    # ─────────────────────────────────────────────────────────────────────
    # Sentiment Analysis (keyword-based, no ML)
    # ─────────────────────────────────────────────────────────────────────

    def _analyze_text_sentiment(self, text: str) -> str:
        """Analyze sentiment of a single text using keyword matching.

        Args:
            text: Text to analyze

        Returns:
            "positive", "negative", or "neutral"
        """
        if not text:
            return "neutral"

        text_lower = text.lower()
        words = set(re.findall(r"\b\w+\b", text_lower))

        positive_hits = len(words & self.POSITIVE_KEYWORDS)
        negative_hits = len(words & self.NEGATIVE_KEYWORDS)

        if positive_hits > negative_hits:
            return "positive"
        elif negative_hits > positive_hits:
            return "negative"
        else:
            return "neutral"

    def _extract_themes(self, texts: list[str], top_n: int = 5) -> list[str]:
        """Extract key themes from a collection of texts.

        Uses simple word frequency analysis (excluding stopwords).

        Args:
            texts: List of text strings
            top_n: Number of top themes to return

        Returns:
            List of top recurring keywords (themes).
        """
        stop_words = {
            "the", "a", "an", "and", "or", "but", "is", "are", "was", "were",
            "be", "been", "being", "have", "has", "had", "do", "does", "did",
            "will", "would", "could", "should", "may", "might", "must", "can",
            "this", "that", "these", "those", "i", "you", "he", "she", "it",
            "we", "they", "what", "which", "who", "when", "where", "why", "how",
            "all", "each", "every", "both", "few", "more", "most", "other",
            "some", "such", "no", "nor", "not", "only", "own", "same", "so",
            "than", "too", "very", "just", "in", "on", "at", "to", "for",
            "of", "with", "by", "from", "as", "into", "through", "during",
            "before", "after", "above", "below", "up", "down", "out", "off",
            "over", "under", "again", "further", "then", "once", "here",
            "there", "about", "my", "your", "his", "her", "its", "our", "their",
            "if", "because", "while", "any", "also", "them", "me", "him",
            "us", "am", "been", "being", "myself", "yourself",
        }

        word_freq: dict[str, int] = {}
        for text in texts:
            if not text:
                continue
            words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
            for word in words:
                if word not in stop_words:
                    word_freq[word] = word_freq.get(word, 0) + 1

        # Sort by frequency and return top N
        sorted_words = sorted(word_freq.items(), key=lambda x: x[1], reverse=True)
        return [word for word, _ in sorted_words[:top_n]]

    async def get_sentiment(self, posts: list[RedditPost]) -> SentimentSummary:
        """Analyze sentiment across a list of Reddit posts.

        Performs keyword-based sentiment analysis on post titles and
        self-text. No ML dependency.

        Args:
            posts: List of RedditPost objects to analyze

        Returns:
            SentimentSummary with positive/negative/neutral counts and themes.
        """
        if not posts:
            return SentimentSummary()

        positive_count = 0
        negative_count = 0
        neutral_count = 0
        all_texts: list[str] = []

        for post in posts:
            # Combine title and selftext for analysis
            combined = f"{post.title} {post.selftext}".strip()
            all_texts.append(combined)

            sentiment = self._analyze_text_sentiment(combined)
            if sentiment == "positive":
                positive_count += 1
            elif sentiment == "negative":
                negative_count += 1
            else:
                neutral_count += 1

        total = len(posts)
        positive_pct = (positive_count / total * 100) if total > 0 else 0.0
        negative_pct = (negative_count / total * 100) if total > 0 else 0.0
        neutral_pct = (neutral_count / total * 100) if total > 0 else 0.0

        # Sentiment score: -1.0 to 1.0
        sentiment_score = ((positive_count - negative_count) / total) if total > 0 else 0.0

        # Extract key themes
        key_themes = self._extract_themes(all_texts, top_n=5)

        return SentimentSummary(
            positive_count=positive_count,
            negative_count=negative_count,
            neutral_count=neutral_count,
            total_analyzed=total,
            positive_pct=round(positive_pct, 1),
            negative_pct=round(negative_pct, 1),
            neutral_pct=round(neutral_pct, 1),
            key_themes=key_themes,
            sentiment_score=round(sentiment_score, 3),
        )

    # ─────────────────────────────────────────────────────────────────────
    # Convenience Methods
    # ─────────────────────────────────────────────────────────────────────

    async def get_subreddit_summary(
        self,
        query: str,
        limit: int = 5,
    ) -> dict[str, Any]:
        """Get a summary of subreddits for a topic.

        Args:
            query: Search query
            limit: Number of subreddits to include

        Returns:
            Dict with subreddits, total_subscribers, and descriptions.
        """
        subreddits = await self.search_subreddits(query, limit=limit)

        total_subscribers = sum(s.subscribers for s in subreddits)

        return {
            "query": query,
            "subreddits": [s.to_dict() for s in subreddits],
            "total_subscribers": total_subscribers,
            "count": len(subreddits),
        }

    async def get_discussion_summary(
        self,
        query: str,
        limit: int = 15,
        time_filter: str = "year",
    ) -> dict[str, Any]:
        """Get a comprehensive discussion summary for a topic.

        Combines post search and sentiment analysis.

        Args:
            query: Search query
            limit: Number of posts to analyze
            time_filter: Time range for posts

        Returns:
            Dict with posts, sentiment, and summary stats.
        """
        posts = await self.search_posts(
            query,
            sort="relevance",
            time_filter=time_filter,
            limit=limit,
        )

        sentiment = await self.get_sentiment(posts)

        total_score = sum(p.score for p in posts)
        total_comments = sum(p.num_comments for p in posts)

        return {
            "query": query,
            "time_filter": time_filter,
            "posts": [p.to_dict() for p in posts],
            "sentiment": sentiment.to_dict(),
            "total_score": total_score,
            "total_comments": total_comments,
            "post_count": len(posts),
        }

    async def close(self) -> None:
        """Cleanup — praw doesn't hold persistent connections."""
        self._reddit = None

    async def __aenter__(self) -> RedditClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
