"""
HYPERION Evidence Scorer — heuristic evidence scoring (VIGIL Layer 4).

This replaces VIGIL's proposed pgvector + Ollama embeddings with a lightweight
keyword-overlap + domain-quality + freshness heuristic. Zero new infrastructure
required.

Scoring dimensions:
  1. Relevance: keyword overlap between query and content (TF-IDF-like)
  2. Source credibility: peer_reviewed > government > industry_report
     > news > blog > social_media
  3. Freshness: newer content scores higher (decay over 2 years)
  4. Evidence stance: does the content support, conflict, or remain
     neutral relative to the query's implied claim?

This is an internal component of DeepSearchClient, not a standalone tool.
It's instantiated by DeepSearchClient.__init__().
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


@dataclass
class ScoredResult:
    """A single scored extraction result."""

    url: str = ""
    title: str = ""
    content: str = ""
    markdown: str = ""
    tool_used: str = ""
    relevance_score: float = 0.0
    credibility_score: float = 0.0
    freshness_score: float = 0.0
    evidence_score: float = 0.0
    stance: str = "neutral"  # "support" | "conflict" | "neutral"
    composite_score: float = 0.0
    published_date: str | None = None
    source: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "url": self.url,
            "title": self.title,
            "content": self.content,
            "markdown": self.markdown,
            "tool_used": self.tool_used,
            "relevance_score": self.relevance_score,
            "credibility_score": self.credibility_score,
            "freshness_score": self.freshness_score,
            "evidence_score": self.evidence_score,
            "stance": self.stance,
            "composite_score": self.composite_score,
            "published_date": self.published_date,
            "source": self.source,
        }


@dataclass
class EvidenceSummary:
    """Summary of evidence across all scored results."""

    support_count: int = 0
    conflict_count: int = 0
    neutral_count: int = 0
    overall_stance: str = "insufficient"  # "supported" | "contested" | "mixed" | "insufficient"
    confidence: float = 0.0
    top_sources: list[dict[str, Any]] = field(default_factory=list)
    key_findings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "support_count": self.support_count,
            "conflict_count": self.conflict_count,
            "neutral_count": self.neutral_count,
            "overall_stance": self.overall_stance,
            "confidence": self.confidence,
            "top_sources": self.top_sources,
            "key_findings": self.key_findings,
        }


class EvidenceScorer:
    """Heuristic evidence scoring — VIGIL Layer 4 (no embeddings).

    Replaces VIGIL's proposed pgvector + Ollama embeddings with a
    lightweight keyword-overlap + domain-quality + freshness heuristic.
    Zero new infrastructure required.

    Usage:
        scorer = EvidenceScorer()
        scored = scorer.score("Indian SaaS market size 2024", extracted_results)
        summary = scorer.summarize(scored)
    """

    # Domain credibility mapping — TLD and known domains
    DOMAIN_CREDIBILITY: dict[str, float] = {
        ".gov": 0.95,
        ".edu": 0.90,
        ".org": 0.75,
        ".com": 0.50,
        ".io": 0.45,
        ".ai": 0.45,
        ".net": 0.45,
        ".co": 0.45,
    }

    # Known high-credibility domains
    KNOWN_DOMAINS: dict[str, float] = {
        "arxiv.org": 0.90,
        "nature.com": 0.90,
        "sciencedirect.com": 0.85,
        "ieee.org": 0.85,
        "acm.org": 0.85,
        "bloomberg.com": 0.80,
        "reuters.com": 0.80,
        "ft.com": 0.80,
        "wsj.com": 0.80,
        "nytimes.com": 0.75,
        "economist.com": 0.75,
        "mckinsey.com": 0.75,
        "bcg.com": 0.75,
        "bain.com": 0.75,
        "deloitte.com": 0.75,
        "pwc.com": 0.75,
        "kpmg.com": 0.75,
        "statista.com": 0.75,
        "crunchbase.com": 0.70,
        "pitchbook.com": 0.70,
        "cbinsights.com": 0.70,
        "sec.gov": 0.95,
        "data.gov": 0.95,
        "worldbank.org": 0.95,
        "imf.org": 0.95,
        "oecd.org": 0.90,
        "wto.org": 0.90,
        "who.int": 0.95,
        "un.org": 0.90,
        "ec.europa.eu": 0.90,
        "federalreserve.gov": 0.95,
        "treasury.gov": 0.95,
        "bea.gov": 0.95,
        "bls.gov": 0.95,
        "census.gov": 0.95,
        "nist.gov": 0.95,
        "wikipedia.org": 0.60,
        "reddit.com": 0.35,
        "medium.com": 0.30,
        "substack.com": 0.30,
        "quora.com": 0.25,
        "yahoo.com": 0.40,
        "investopedia.com": 0.50,
        "techcrunch.com": 0.55,
        "theverge.com": 0.55,
        "wired.com": 0.55,
        "forbes.com": 0.55,
        "businessinsider.com": 0.50,
    }

    # Retail / e-commerce / ad / low-signal domains that are almost never a
    # credible source for a consulting engagement. Results on these domains are
    # dropped outright (Layer 1.3 — "retail/ad domains auto-rejected"). This
    # prevents garbage like "Best Buy" or a Greek e-shop appearing as a source
    # for a blockchain or M&A report.
    DENIED_DOMAINS: frozenset[str] = frozenset({
        "bestbuy.com", "amazon.com", "ebay.com", "walmart.com", "target.com",
        "aliexpress.com", "alibaba.com", "etsy.com", "wish.com", "temu.com",
        "shopify.com", "wayfair.com", "homedepot.com", "lowes.com",
        "costco.com", "newegg.com", "overstock.com", "ikea.com",
        "pinterest.com", "tiktok.com", "instagram.com", "facebook.com",
        "tripadvisor.com", "yelp.com", "groupon.com", "expedia.com",
        "booking.com", "airbnb.com", "indeed.com", "glassdoor.com",
    })

    # Substrings in a domain that flag retail / ad / affiliate content.
    DENIED_DOMAIN_SUBSTRINGS: tuple[str, ...] = (
        "shop", "store", "e-shop", "eshop", "buy", "deals", "coupon",
        "affiliate", "franchise", "classifieds",
    )

    # A result must clear this relevance floor to be kept. Below it, the result
    # is off-topic noise (SERP ad tiles, shopping results) and is dropped rather
    # than passed downstream where an agent would try to write around it.
    MIN_RELEVANCE: float = 0.08

    # Negation / conflict indicators
    CONFLICT_INDICATORS = [
        "however", "but", "contrary to", "despite", "actually", "in fact",
        "on the other hand", "nevertheless", "nonetheless", "yet",
        "dispute", "disputed", "refute", "refuted", "debunk", "debunked",
        "false", "incorrect", "wrong", "misleading", "inaccurate",
        "challenge", "challenged", "question", "questioned",
    ]

    # Support indicators
    SUPPORT_INDICATORS = [
        "confirm", "confirmed", "verify", "verified", "support", "supported",
        "agree", "agrees", "consistent with", "in line with", "corroborate",
        "corroborated", "validate", "validated", "demonstrate", "demonstrated",
        "evidence shows", "data shows", "according to", "reported by",
        "found that", "concluded that", "established that",
    ]

    # Weights for composite score
    WEIGHT_RELEVANCE = 0.35
    WEIGHT_CREDIBILITY = 0.25
    WEIGHT_FRESHNESS = 0.15
    WEIGHT_EVIDENCE = 0.25

    def score(
        self,
        query: str,
        results: list[dict[str, Any]],
    ) -> list[ScoredResult]:
        """Score extracted content against the query.

        Args:
            query: The original search query
            results: List of extracted content dicts with keys:
                url, title, content/markdown, tool_used, published_date (optional)

        Returns:
            List of ScoredResult objects, sorted by composite_score descending.
        """
        scored: list[ScoredResult] = []

        for result in results:
            url = result.get("url", "")
            title = result.get("title", "")
            content = result.get("content") or result.get("markdown") or ""
            tool_used = result.get("tool_used", "")
            published_date = result.get("published_date")

            # Layer 1.3 — drop retail/ad/off-topic domains before they can be
            # cited. Fail loud: log every rejection so low-yield queries surface.
            if self._is_denied_domain(url):
                logger.info("EvidenceScorer: dropped denied/retail domain %s", url)
                continue

            relevance = self._score_relevance(query, f"{title} {content}")

            # Drop clearly off-topic results (below the relevance floor).
            if relevance < self.MIN_RELEVANCE:
                logger.info(
                    "EvidenceScorer: dropped low-relevance result (%.3f) %s",
                    relevance, url,
                )
                continue

            credibility = self._score_credibility(url)
            freshness = self._score_freshness(published_date)
            stance = self._determine_stance(query, content)
            evidence_score = self._score_evidence(stance, credibility, relevance)

            composite = (
                self.WEIGHT_RELEVANCE * relevance
                + self.WEIGHT_CREDIBILITY * credibility
                + self.WEIGHT_FRESHNESS * freshness
                + self.WEIGHT_EVIDENCE * evidence_score
            )

            scored.append(ScoredResult(
                url=url,
                title=title,
                content=content,
                markdown=result.get("markdown", content),
                tool_used=tool_used,
                relevance_score=relevance,
                credibility_score=credibility,
                freshness_score=freshness,
                evidence_score=evidence_score,
                stance=stance,
                composite_score=composite,
                published_date=published_date,
                source={
                    "url": url,
                    "title": title,
                    "credibility": credibility,
                    "tool_used": tool_used,
                },
            ))

        # Sort by composite score descending
        scored.sort(key=lambda r: r.composite_score, reverse=True)
        return scored

    def summarize(self, results: list[ScoredResult]) -> EvidenceSummary:
        """Build an evidence summary across all results.

        Args:
            results: List of ScoredResult objects

        Returns:
            EvidenceSummary with counts, overall stance, and confidence.
        """
        if not results:
            return EvidenceSummary()

        support_count = sum(1 for r in results if r.stance == "support")
        conflict_count = sum(1 for r in results if r.stance == "conflict")
        neutral_count = sum(1 for r in results if r.stance == "neutral")
        total = len(results)

        # Determine overall stance
        if total < 3:
            overall_stance = "insufficient"
        elif conflict_count == 0 and support_count > 0:
            overall_stance = "supported"
        elif support_count == 0 and conflict_count > 0:
            overall_stance = "contested"
        elif conflict_count > 0 and support_count > 0:
            overall_stance = "mixed"
        else:
            overall_stance = "insufficient"

        # Confidence: based on source count + credibility + agreement
        avg_credibility = sum(r.credibility_score for r in results) / total
        agreement = 1.0 - (conflict_count / total) if total > 0 else 0.0
        source_factor = min(total / 10.0, 1.0)  # 10+ sources = max confidence
        confidence = (avg_credibility * 0.4 + agreement * 0.4 + source_factor * 0.2)

        # Top sources (top 5 by composite score)
        top_sources = [r.source for r in results[:5]]

        # Key findings — extract titles of top supporting/conflicting
        key_findings: list[str] = []
        for r in results[:5]:
            stance_label = r.stance.upper()
            key_findings.append(f"[{stance_label}] {r.title} — {r.url}")

        return EvidenceSummary(
            support_count=support_count,
            conflict_count=conflict_count,
            neutral_count=neutral_count,
            overall_stance=overall_stance,
            confidence=confidence,
            top_sources=top_sources,
            key_findings=key_findings,
        )

    def _score_relevance(self, query: str, content: str) -> float:
        """TF-IDF-like keyword overlap scoring.

        Extracts keywords from the query and checks how many appear
        in the content, weighted by term frequency.
        """
        if not query or not content:
            return 0.0

        # Extract keywords from query (remove stop words, lowercase)
        query_words = self._extract_keywords(query)
        if not query_words:
            return 0.0

        content_lower = content.lower()
        content_words = set(content_lower.split())

        # Count how many query keywords appear in content
        matches = 0
        for word in query_words:
            if word in content_lower:
                matches += 1

        # Base score: ratio of matched keywords
        base_score = matches / len(query_words)

        # Bonus for exact phrase matches
        query_lower = query.lower()
        if query_lower in content_lower:
            base_score = min(base_score + 0.2, 1.0)

        # Bonus for number matches (data points are high-value)
        numbers_in_query = re.findall(r"\d+(?:\.\d+)?%?", query)
        for num in numbers_in_query:
            if num in content:
                base_score = min(base_score + 0.1, 1.0)

        return min(base_score, 1.0)

    def _is_denied_domain(self, url: str) -> bool:
        """Return True if the URL's domain is a retail/ad/off-topic source.

        These are auto-rejected (Layer 1.3) so they can never be cited as a
        source in a consulting report.
        """
        if not url:
            return False
        try:
            domain = urlparse(url).netloc.lower()
        except (ValueError, AttributeError):
            return False
        if domain.startswith("www."):
            domain = domain[4:]
        if not domain:
            return False

        # Exact / suffix match against the explicit deny-list.
        if domain in self.DENIED_DOMAINS:
            return True
        for denied in self.DENIED_DOMAINS:
            if domain.endswith("." + denied):
                return True

        # Substring heuristic for retail/ad/affiliate domains.
        host = domain.split(":")[0]
        label = host.split(".")[0] if "." in host else host
        for token in self.DENIED_DOMAIN_SUBSTRINGS:
            if token in label:
                return True
        return False

    def _score_credibility(self, url: str) -> float:
        """Score source credibility based on domain."""
        if not url:
            return 0.30  # Unknown source default

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower()

            # Remove www. prefix
            if domain.startswith("www."):
                domain = domain[4:]

            # Check known domains first (exact match)
            if domain in self.KNOWN_DOMAINS:
                return self.KNOWN_DOMAINS[domain]

            # Check if known domain is a subdomain
            for known, score in self.KNOWN_DOMAINS.items():
                if domain.endswith(known) or domain.endswith(f".{known}"):
                    return score

            # Check TLD
            for tld, score in self.DOMAIN_CREDIBILITY.items():
                if domain.endswith(tld):
                    return score

            # Unknown domain — default
            return 0.40

        except Exception:
            return 0.30

    def _score_freshness(self, published_date: str | None) -> float:
        """Score freshness with 2-year exponential decay."""
        if not published_date:
            return 0.50  # Unknown date — neutral

        try:
            # Try to parse the date
            date_str = published_date.strip()

            # Try ISO format first
            try:
                pub_date = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
            except ValueError:
                # Try common date formats
                for fmt in ["%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%B %d, %Y", "%b %d, %Y"]:
                    try:
                        pub_date = datetime.strptime(date_str, fmt)
                        break
                    except ValueError:
                        continue
                else:
                    return 0.50  # Couldn't parse

            # Calculate age in days
            now = datetime.now(pub_date.tzinfo) if pub_date.tzinfo else datetime.now()
            age_days = (now - pub_date).days

            if age_days < 0:
                return 1.0  # Future date (likely error) — give max

            # Exponential decay: half-life of 1 year, floor at 0.1
            import math
            decay = math.exp(-age_days / 365.0)
            return max(decay, 0.1)

        except Exception:
            return 0.50

    def _determine_stance(self, query: str, content: str) -> str:
        """Determine if content supports, conflicts, or is neutral.

        Heuristic approach:
        - Look for negation/conflict patterns
        - Look for support/corroboration patterns
        - If no clear signal, return "neutral"
        """
        if not content or not query:
            return "neutral"

        content_lower = content.lower()

        # Count conflict indicators
        conflict_count = sum(
            1 for indicator in self.CONFLICT_INDICATORS
            if indicator in content_lower
        )

        # Count support indicators
        support_count = sum(
            1 for indicator in self.SUPPORT_INDICATORS
            if indicator in content_lower
        )

        # Determine stance based on relative counts
        if conflict_count > support_count and conflict_count >= 2:
            return "conflict"
        elif support_count > conflict_count and support_count >= 2:
            return "support"
        elif support_count > 0 and conflict_count == 0:
            return "support"
        elif conflict_count > 0 and support_count == 0:
            return "conflict"
        else:
            return "neutral"

    def _score_evidence(self, stance: str, credibility: float, relevance: float) -> float:
        """Compute evidence score from stance + credibility + relevance.

        - Supporting evidence from credible sources scores high
        - Conflicting evidence from credible sources also scores high
          (it's valuable for identifying contested claims)
        - Neutral evidence scores moderate
        """
        if stance == "neutral":
            return 0.4 * relevance

        # Both support and conflict are valuable — the key is credibility
        stance_multiplier = 1.0 if stance in ("support", "conflict") else 0.5

        return min(credibility * stance_multiplier * relevance, 1.0)

    def _extract_keywords(self, text: str) -> list[str]:
        """Extract meaningful keywords from text (remove stop words)."""
        stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at",
            "to", "for", "of", "with", "by", "from", "is", "are",
            "was", "were", "be", "been", "being", "have", "has",
            "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "must", "can", "this", "that",
            "these", "those", "i", "you", "he", "she", "it", "we",
            "they", "what", "which", "who", "when", "where", "why",
            "how", "all", "each", "every", "both", "few", "more",
            "most", "other", "some", "such", "no", "nor", "not",
            "only", "own", "same", "so", "than", "too", "very",
            "just", "also", "about", "if", "as", "into", "through",
            "during", "before", "after", "above", "below", "up",
            "down", "out", "off", "over", "under", "again",
        }

        words = re.findall(r"[a-zA-Z]{2,}", text.lower())
        return [w for w in words if w not in stop_words]
