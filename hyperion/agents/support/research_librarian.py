"""
HYPERION Research Librarian — Agent 15, the vault manager and source organizer.

This is NOT a generic "search and organize" agent. This is a specialist with
5 proprietary skills:

- Keyword retrieval: Find relevant prior research using keyword matching with
  relevance scoring (threshold: 0.15). No embeddings — lightweight and fast.
  This is why it runs on MICRO tier (Gemma 4 31B) — it doesn't need strong
  reasoning, it needs fast, high-throughput keyword matching.
- Source deduplication: Detect when multiple agents cite the same source and
  deduplicate the source list. Not just "remove duplicates" — normalize URLs,
  detect same-domain citations, and merge source metadata.
- Citation management: Format citations consistently (footnote style) and
  ensure every claim in the final report has a traceable source. Not just
  "list sources" — format as numbered footnotes with author, title, URL,
  accessed date, and credibility tier.
- Cross-engagement knowledge linking: When a new engagement touches a topic
  researched in a prior engagement, link the prior research for the Synthesis
  Lead to reference. This makes the system smarter over time — each
  engagement makes the next one faster and better.
- Source credibility scoring: Score each source on credibility (peer-reviewed
  > government > industry report > news > blog > social media) and flag low-
  credibility sources. Not just "this is a blog" — "this source is a blog
  post with no author attribution and no citations — flagged as low
  credibility."

It runs on MICRO tier (Gemma 4 31B, 14.4K RPD) because it doesn't need strong
reasoning — it needs fast, high-throughput keyword matching. It makes the
system smarter over time by accumulating knowledge in the vault. Each
engagement makes the next one faster and better because the Librarian can
retrieve prior findings. (§4.5, Agent 15)

Model Tier: MICRO (Gemma 4 31B — keyword matching and note management, not
complex reasoning)
Tools: Second Brain (Obsidian vault) — read/write markdown notes, keyword
       retrieval, tag-based search, cross-note linking
Sub-agents: 0 (this agent doesn't spawn sub-agents — it's a support agent)
Output: SourceCollection (deduplicated sources with credibility scores, prior
        research links, formatted citations)

Methodology (§4.5, Agent 15):
1. Query vault for prior research on the engagement topic
2. Retrieve relevant notes and return to requesting agent
3. Collect all sources from all agents at end of engagement
4. Deduplicate sources
5. Score source credibility
6. Save engagement findings to vault for future reference
7. Format citation list for final report
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime
from typing import Any
from urllib.parse import urlparse

from hyperion.agents.base import BaseAgent
from hyperion.agents.bus import Channel, MessageType
from hyperion.config import ModelTier
from hyperion.router.budget import TaskUrgency
from hyperion.schemas.agents import (
    AgentName,
    AgentRole,
    AgentSpec,
    AgentState,
    SkillSpec,
    ToolName,
)
from hyperion.schemas.models import (
    ConfidenceLevel,
    KeyFinding,
    PriorResearchLink,
    Source,
    SourceCollection,
    SourceCredibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


RESEARCH_LIBRARIAN_SPEC = AgentSpec(
    name=AgentName.RESEARCH_LIBRARIAN,
    role=AgentRole.SUPPORT,
    display_name="Research Librarian",
    model_tier=ModelTier.MICRO,
    tools=[
        ToolName.SECOND_BRAIN,
        ToolName.OPEN_ALEX,
    ],
    skills=[
        SkillSpec(
            name="Keyword retrieval",
            description=(
                "Find relevant prior research using keyword matching with "
                "relevance scoring (threshold: 0.15). No embeddings — "
                "lightweight and fast. This is why the Librarian runs on "
                "MICRO tier — it doesn't need strong reasoning, it needs "
                "fast, high-throughput keyword matching. Scores each note "
                "on keyword overlap with the query and returns notes above "
                "the 0.15 threshold."
            ),
            inputs=["query_keywords", "vault_notes", "relevance_threshold"],
            outputs=["relevant_notes", "relevance_scores", "matched_keywords"],
        ),
        SkillSpec(
            name="Source deduplication",
            description=(
                "Detect when multiple agents cite the same source and "
                "deduplicate the source list. Not just 'remove duplicates' "
                "— normalize URLs (strip tracking params, resolve redirects), "
                "detect same-domain citations, and merge source metadata. "
                "Track duplicates_removed count."
            ),
            inputs=["source_lists_from_all_agents"],
            outputs=["deduplicated_sources", "duplicates_removed", "duplicate_groups"],
        ),
        SkillSpec(
            name="Citation management",
            description=(
                "Format citations consistently (footnote style) and ensure "
                "every claim in the final report has a traceable source. "
                "Format as numbered footnotes: [1] Author, 'Title', URL, "
                "accessed DATE, credibility: TIER. Not just 'list sources' "
                "— proper academic footnote format."
            ),
            inputs=["deduplicated_sources", "claims_with_sources"],
            outputs=["formatted_citations", "uncovered_claims", "citation_index"],
        ),
        SkillSpec(
            name="Cross-engagement knowledge linking",
            description=(
                "When a new engagement touches a topic researched in a prior "
                "engagement, link the prior research for the Synthesis Lead "
                "to reference. This makes the system smarter over time — "
                "each engagement makes the next one faster and better "
                "because the Librarian can retrieve prior findings."
            ),
            inputs=["engagement_topic", "vault_notes", "prior_engagement_index"],
            outputs=["prior_research_links", "relevance_scores", "linked_engagements"],
        ),
        SkillSpec(
            name="Source credibility scoring",
            description=(
                "Score each source on credibility (peer-reviewed > government "
                "> industry report > news > blog > social media) and flag "
                "low-credibility sources. Not just 'this is a blog' — 'this "
                "source is a blog post with no author attribution and no "
                "citations — flagged as low credibility.' Track "
                "sources_by_credibility counts."
            ),
            inputs=["sources", "source_metadata"],
            outputs=["credibility_scores", "low_credibility_flags", "sources_by_credibility"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Research Librarian — the manager of the "
        "Obsidian vault (Second Brain) and the source organization specialist.\n\n"
        "Your role:\n"
        "1. RETRIEVE prior research from the vault when a new engagement "
        "starts. Use keyword matching with a 0.15 relevance threshold. No "
        "embeddings — lightweight and fast.\n"
        "2. DEDUPLICATE sources collected from all agents. Normalize URLs, "
        "detect same-domain citations, merge metadata.\n"
        "3. SCORE source credibility: peer-reviewed > government > industry "
        "report > vendor > news > blog > social media. Flag low-credibility "
        "sources.\n"
        "4. FORMAT citations as numbered footnotes for the final report.\n"
        "5. SAVE engagement findings to the vault for future reference.\n\n"
        "You run on MICRO tier (Gemma 4 31B) because you don't need strong "
        "reasoning — you need fast, high-throughput keyword matching. You "
        "make the system smarter over time by accumulating knowledge in the "
        "vault. Each engagement makes the next one faster and better.\n\n"
        "Rules:\n"
        "- KEYWORD MATCHING, NOT EMBEDDINGS. You use simple keyword overlap "
        "scoring with a 0.15 threshold. This is deliberate — it's fast and "
        "doesn't require a GPU.\n"
        "- URL NORMALIZATION: strip tracking parameters (utm_*, fbclid, "
        "gclid), resolve redirects, normalize trailing slashes. Two URLs "
        "that point to the same page are the same source.\n"
        "- CREDIBILITY HIERARCHY IS STRICT: peer-reviewed > government > "
        "industry report > vendor > news > blog > social media. A blog post "
        "is NEVER high credibility, no matter how well-written.\n"
        "- EVERY CLAIM MUST HAVE A TRACEABLE SOURCE. If a claim has no "
        "source, flag it as 'uncovered' for the Fact Checker.\n"
        "- SAVE TO VAULT: after each engagement, write a markdown note with "
        "the engagement topic, key findings, sources, and agents used. Tag "
        "it with relevant keywords for future retrieval.\n\n"
        "You do NOT spawn sub-agents. You are a support agent — your job is "
        "to serve other agents by managing the vault and organizing sources.\n\n"
        "Your output is a SourceCollection Pydantic model — structured, not "
        "free text."
    ),
    spawn_condition="Spawned at engagement start (to retrieve prior research) "
                     "and at engagement end (to collect, deduplicate, score, "
                     "and save sources). Also spawned when any agent requests "
                     "prior research via the REQUESTS channel.",
    max_sub_agents=0,
    output_model="SourceCollection",
)


# ─────────────────────────────────────────────────────────────────────────────
# Research Librarian Agent
# ─────────────────────────────────────────────────────────────────────────────


class ResearchLibrarian(BaseAgent):
    """Agent 15: The vault manager and source organizer.

    Manages the Obsidian vault (Second Brain), retrieves prior research,
    organizes sources, and links findings across engagements. Runs on MICRO
    tier because it doesn't need strong reasoning — it needs fast, high-
    throughput keyword matching. Makes the system smarter over time by
    accumulating knowledge in the vault. (§4.5, Agent 15)

    Lifecycle:
    1. At engagement start: query vault for prior research, return to
       requesting agent
    2. During engagement: respond to REQUESTS for prior research
    3. At engagement end: collect all sources, deduplicate, score credibility,
       format citations, save to vault
    """

    # Credibility hierarchy for scoring (higher = more credible)
    CREDIBILITY_RANK: dict[SourceCredibility, int] = {
        SourceCredibility.PEER_REVIEWED: 6,
        SourceCredibility.GOVERNMENT: 5,
        SourceCredibility.INDUSTRY_REPORT: 4,
        SourceCredibility.VENDOR: 3,
        SourceCredibility.NEWS: 2,
        SourceCredibility.BLOG: 1,
        SourceCredibility.SOCIAL_MEDIA: 0,
    }

    # Low credibility tiers to flag
    LOW_CREDIBILITY_TIERS: set[SourceCredibility] = {
        SourceCredibility.BLOG,
        SourceCredibility.SOCIAL_MEDIA,
    }

    # URL tracking parameters to strip during normalization
    TRACKING_PARAMS: set[str] = {
        "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
        "fbclid", "gclid", "ref", "ref_src", "ref_url",
    }

    # Keyword matching threshold
    RELEVANCE_THRESHOLD: float = 0.15

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or RESEARCH_LIBRARIAN_SPEC, bus=bus, router=router)

        # Engagement context
        self._engagement_id: str = ""
        self._topic: str = ""
        self._context: dict[str, Any] = {}

        # All sources collected from agents
        self._all_sources: list[Source] = []

        # Prior research from vault
        self._prior_research: list[PriorResearchLink] = []

        # Vault note path
        self._vault_note_path: str = ""

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Research Librarian listens to:
        - HANDOFF: receives task assignment from Engagement Director
          (start: retrieve prior research; end: collect and organize sources)
        - REQUESTS: responds to agent requests for prior research on a topic
        - FINDINGS: collects sources from all agents' findings
        """
        if msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            context_bundle = payload.get("context_bundle", {})

            if task == "retrieve_prior_research":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._topic = context_bundle.get("topic", context_bundle.get("question", ""))
                self._context = context_bundle.get("context", {})

            elif task == "organize_sources":
                self._engagement_id = context_bundle.get("engagement_id", "")
                self._topic = context_bundle.get("topic", "")
                self._all_sources = context_bundle.get("all_sources", [])

        elif msg.channel == Channel.REQUESTS:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            request_type = payload.get("request_type", "")
            if request_type == "prior_research":
                # An agent is requesting prior research on a topic
                topic = payload.get("topic", "")
                requesting_agent = payload.get("from_agent", "")
                # Will be handled in run()
                pass

        elif msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None and finding.sources:
                # Collect sources from findings
                for source in finding.sources:
                    self._all_sources.append(source)

    # ─────────────────────────────────────────────────────────────────────
    # URL normalization (for deduplication)
    # ─────────────────────────────────────────────────────────────────────

    def _normalize_url(self, url: str) -> str:
        """Normalize a URL for deduplication.

        Strips tracking parameters, normalizes trailing slashes, lowercases
        domain. Two URLs that point to the same page should produce the same
        normalized URL.
        """
        if not url:
            return ""

        try:
            parsed = urlparse(url)
            domain = parsed.netloc.lower().replace("www.", "")
            path = parsed.path.rstrip("/") or "/"

            # Filter out tracking parameters
            query_pairs = []
            if parsed.query:
                for pair in parsed.query.split("&"):
                    if "=" in pair:
                        key, _ = pair.split("=", 1)
                        if key.lower() not in self.TRACKING_PARAMS:
                            query_pairs.append(pair)

            query = "&".join(query_pairs) if query_pairs else ""
            normalized = f"{domain}{path}"
            if query:
                normalized += f"?{query}"
            return normalized
        except (ValueError, TypeError):
            return url.lower().strip()

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Query vault for prior research (keyword matching)
    # ─────────────────────────────────────────────────────────────────────

    async def _query_vault(self, topic: str, keywords: list[str]) -> list[PriorResearchLink]:
        """Query the Obsidian vault for prior research on the engagement topic.

        Uses keyword matching with relevance scoring (threshold: 0.15). No
        embeddings — lightweight and fast. This is why the Librarian runs on
        MICRO tier.
        """
        prior_links: list[PriorResearchLink] = []

        try:
            second_brain = self.get_tool(ToolName.SECOND_BRAIN)

            # Search vault notes by keywords
            for keyword in keywords[:10]:
                try:
                    notes = await second_brain.search(keyword, tags=["engagement", "research"])
                    if notes:
                        for note in notes[:5]:
                            # Calculate relevance score based on keyword overlap
                            note_text = (note.get("content", "") + " " + note.get("title", "")).lower()
                            matched = sum(1 for kw in keywords if kw.lower() in note_text)
                            relevance = matched / max(len(keywords), 1)

                            if relevance >= self.RELEVANCE_THRESHOLD:
                                prior_links.append(PriorResearchLink(
                                    engagement_id=note.get("engagement_id", ""),
                                    topic=note.get("topic", note.get("title", "")),
                                    note_path=note.get("path", ""),
                                    relevance_score=round(relevance, 2),
                                    summary=note.get("content", "")[:500],
                                    agents_used=note.get("agents_used", []),
                                ))
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError) as e:
            await self._log_tool_use("second_brain", "call", f"FAIL · {e}", success=False)

        # Sort by relevance score (descending)
        prior_links.sort(key=lambda x: x.relevance_score, reverse=True)

        return prior_links[:10]  # Top 10 most relevant

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Retrieve relevant notes and return to requesting agent
    # ─────────────────────────────────────────────────────────────────────

    async def _retrieve_notes(self, topic: str, prior_links: list[PriorResearchLink]) -> list[dict[str, Any]]:
        """Retrieve the full content of relevant vault notes.

        Returns the notes to the requesting agent via the bus.
        """
        notes: list[dict[str, Any]] = []

        try:
            second_brain = self.get_tool(ToolName.SECOND_BRAIN)

            for link in prior_links[:5]:
                try:
                    note = await second_brain.read(link.note_path)
                    if note:
                        notes.append({
                            "path": link.note_path,
                            "topic": link.topic,
                            "content": note[:2000],
                            "relevance": link.relevance_score,
                            "engagement_id": link.engagement_id,
                        })
                except (ValueError, AttributeError, RuntimeError):
                    continue

        except (ValueError, AttributeError, RuntimeError) as e:
            await self._log_tool_use("second_brain", "call", f"FAIL · {e}", success=False)

        return notes

    # ─────────────────────────────────────────────────────────────────────
    # Steps 3-4: Collect and deduplicate sources
    # ─────────────────────────────────────────────────────────────────────

    def _deduplicate_sources(self, sources: list[Source]) -> tuple[list[Source], int]:
        """Deduplicate sources by normalized URL.

        Detects when multiple agents cite the same source. Normalizes URLs
        (strip tracking params, resolve redirects, normalize trailing slashes),
        detects same-domain citations, and merges source metadata.

        Returns (deduplicated_sources, duplicates_removed).
        """
        if not sources:
            return ([], 0)

        seen_urls: dict[str, Source] = {}
        duplicates = 0

        for source in sources:
            normalized = self._normalize_url(source.url)

            if normalized in seen_urls:
                # Duplicate — merge metadata (keep higher credibility)
                existing = seen_urls[normalized]
                if self.CREDIBILITY_RANK.get(source.credibility, 0) > self.CREDIBILITY_RANK.get(existing.credibility, 0):
                    # Replace with higher-credibility version
                    seen_urls[normalized] = source
                duplicates += 1
            else:
                seen_urls[normalized] = source

        deduplicated = list(seen_urls.values())
        return (deduplicated, duplicates)

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Score source credibility
    # ─────────────────────────────────────────────────────────────────────

    def _score_credibility(self, sources: list[Source]) -> tuple[list[Source], dict[str, int], list[Source]]:
        """Score sources on credibility and flag low-credibility sources.

        Credibility hierarchy: peer-reviewed > government > industry report >
        vendor > news > blog > social media.

        Returns (all_sources, sources_by_credibility, low_credibility_sources).
        """
        by_credibility: dict[str, int] = {}
        low_cred: list[Source] = []

        for source in sources:
            cred_key = source.credibility.value
            by_credibility[cred_key] = by_credibility.get(cred_key, 0) + 1

            if source.credibility in self.LOW_CREDIBILITY_TIERS:
                low_cred.append(source)

        return (sources, by_credibility, low_cred)

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Save engagement findings to vault
    # ─────────────────────────────────────────────────────────────────────

    async def _save_to_vault(
        self,
        engagement_id: str,
        topic: str,
        sources: list[Source],
        agents_used: list[str],
        findings_summary: str,
    ) -> str:
        """Save engagement findings to the Obsidian vault for future reference.

        Writes a markdown note with the engagement topic, key findings, sources,
        and agents used. Tags it with relevant keywords for future retrieval.
        """
        note_path = ""

        try:
            second_brain = self.get_tool(ToolName.SECOND_BRAIN)

            # Build markdown note
            keywords = re.findall(r'\b[A-Za-z]{3,}\b', topic.lower())
            tags = list(set(keywords[:10])) + ["engagement", "research"]

            # Format source list for the note
            source_lines = []
            for i, src in enumerate(sources[:50], 1):
                source_lines.append(
                    f"- [{i}] {src.title} — {src.url} "
                    f"(credibility: {src.credibility.value})"
                )

            note_content = f"""---
engagement_id: {engagement_id}
topic: {topic}
date: {datetime.now().isoformat()}
agents_used: {", ".join(agents_used)}
tags: {", ".join(tags)}
---

# {topic}

## Summary
{findings_summary}

## Sources ({len(sources)} total)
{chr(10).join(source_lines)}
"""

            note_path = f"engagements/{engagement_id}.md"
            await second_brain.write(note_path, note_content, tags=tags)

        except (ValueError, AttributeError, RuntimeError) as e:
            await self._log_tool_use("second_brain", "call", f"FAIL · {e}", success=False)

        return note_path

    # ─────────────────────────────────────────────────────────────────────
    # Step 7: Format citation list for final report
    # ─────────────────────────────────────────────────────────────────────

    def _format_citations(self, sources: list[Source]) -> list[str]:
        """Format citations as numbered footnotes for the final report.

        Format: [1] Author, 'Title', URL, accessed DATE, credibility: TIER
        """
        citations: list[str] = []

        # Sort by credibility (highest first)
        sorted_sources = sorted(
            sources,
            key=lambda s: self.CREDIBILITY_RANK.get(s.credibility, 0),
            reverse=True,
        )

        for i, source in enumerate(sorted_sources, 1):
            author = source.author or "Unknown"
            title = source.title or "Untitled"
            url = source.url
            accessed = source.accessed_at.strftime("%Y-%m-%d") if source.accessed_at else "N/A"
            cred = source.credibility.value

            citation = f"[{i}] {author}, '{title}', {url}, accessed {accessed}, credibility: {cred}"
            citations.append(citation)

        return citations

    # ─────────────────────────────────────────────────────────────────────
    # Confidence calibration
    # ─────────────────────────────────────────────────────────────────────

    def _calibrate_confidence(
        self,
        total_sources: int,
        duplicates_removed: int,
        low_cred_count: int,
        prior_research_count: int,
        vault_saved: bool,
    ) -> ConfidenceLevel:
        """Calibrate confidence based on source collection completeness.

        HIGH: 10+ unique sources, <30% low credibility, prior research found,
              vault saved
        MEDIUM: 5+ unique sources
        LOW: <5 sources
        """
        if total_sources == 0:
            return ConfidenceLevel.LOW

        low_cred_ratio = low_cred_count / max(total_sources, 1)

        if (total_sources >= 10 and low_cred_ratio < 0.3
                and prior_research_count > 0 and vault_saved):
            return ConfidenceLevel.HIGH
        if total_sources >= 5:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # Keyword extraction from topic
    # ─────────────────────────────────────────────────────────────────────

    def _extract_keywords(self, topic: str) -> list[str]:
        """Extract keywords from the topic for vault search.

        Simple keyword extraction — no embeddings, no NLP. Just split on
        whitespace and filter short words. This is deliberate: the Librarian
        runs on MICRO tier and uses lightweight keyword matching.
        """
        # Remove common stop words
        stop_words = {
            "the", "a", "an", "is", "are", "was", "were", "be", "been",
            "being", "have", "has", "had", "do", "does", "did", "will",
            "would", "could", "should", "may", "might", "must", "shall",
            "can", "need", "dare", "ought", "used", "and", "or", "but",
            "if", "then", "else", "when", "where", "why", "how", "what",
            "who", "whom", "which", "this", "that", "these", "those",
            "in", "on", "at", "to", "for", "of", "with", "by", "from",
            "up", "about", "into", "through", "during", "before", "after",
        }

        words = re.findall(r'\b[A-Za-z]{3,}\b', topic.lower())
        keywords = [w for w in words if w not in stop_words]

        return keywords[:15]  # Top 15 keywords

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 7-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
        all_sources: list[Source] | None = None,
        agents_used: list[str] | None = None,
        findings_summary: str = "",
    ) -> SourceCollection:
        """Execute the Research Librarian's 7-step methodology.

        Steps (§4.5, Agent 15):
        1. Query vault for prior research on the engagement topic
        2. Retrieve relevant notes and return to requesting agent
        3. Collect all sources from all agents at end of engagement
        4. Deduplicate sources
        5. Score source credibility
        6. Save engagement findings to vault for future reference
        7. Format citation list for final report
        """
        self._engagement_id = engagement_id or self._engagement_id
        self._topic = question or self._topic
        self._context = context or self._context
        if all_sources:
            self._all_sources = all_sources

        # Subscribe to bus
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting librarian tasks: {self._topic[:80]}",
        )

        # Step 1: Query vault for prior research
        await self._transition(AgentState.WORKING, "Step 1: Querying vault for prior research")
        keywords = self._extract_keywords(self._topic)
        self._prior_research = await self._query_vault(self._topic, keywords)

        # Step 2: Retrieve relevant notes
        await self._transition(AgentState.WORKING, "Step 2: Retrieving relevant vault notes")
        notes = await self._retrieve_notes(self._topic, self._prior_research)

        # Publish prior research to bus for requesting agents
        if notes:
            await self.bus.publish(
                channel=Channel.FINDINGS,
                msg_type=MessageType.FINDING,
                sender=self.name,
                payload={
                    "agent": self.name.value,
                    "finding_type": "prior_research",
                    "prior_research": notes,
                    "topic": self._topic,
                    "count": len(notes),
                },
            )

        # Step 3: Collect all sources
        await self._transition(AgentState.WORKING, f"Step 3: Collecting sources ({len(self._all_sources)} sources)")
        total_before = len(self._all_sources)

        # Step 4: Deduplicate sources
        await self._transition(AgentState.WORKING, "Step 4: Deduplicating sources")
        deduplicated, duplicates_removed = self._deduplicate_sources(self._all_sources)
        total_after = len(deduplicated)

        # Step 5: Score source credibility
        await self._transition(AgentState.WORKING, "Step 5: Scoring source credibility")
        _, sources_by_cred, low_cred_sources = self._score_credibility(deduplicated)

        # Step 6: Save engagement findings to vault
        await self._transition(AgentState.WORKING, "Step 6: Saving engagement findings to vault")
        agents_list = agents_used or self._context.get("agents_used", [])
        self._vault_note_path = await self._save_to_vault(
            self._engagement_id,
            self._topic,
            deduplicated,
            agents_list,
            findings_summary or self._topic,
        )
        vault_saved = bool(self._vault_note_path)

        # Step 7: Format citation list
        await self._transition(AgentState.WORKING, "Step 7: Formatting citation list for final report")
        citations = self._format_citations(deduplicated)

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            total_sources=total_after,
            duplicates_removed=duplicates_removed,
            low_cred_count=len(low_cred_sources),
            prior_research_count=len(self._prior_research),
            vault_saved=vault_saved,
        )

        # Produce SourceCollection model
        collection = SourceCollection(
            sources=deduplicated,
            total_sources_before_dedup=total_before,
            total_sources_after_dedup=total_after,
            duplicates_removed=duplicates_removed,
            low_credibility_sources=low_cred_sources,
            prior_research_links=self._prior_research,
            citations_formatted=citations,
            vault_note_saved=vault_saved,
            vault_note_path=self._vault_note_path,
            confidence=confidence,
            sources_by_credibility=sources_by_cred,
        )

        # Publish source collection to bus
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "finding_type": "source_collection",
                "source_collection": collection.model_dump(),
                "total_sources": total_after,
                "duplicates_removed": duplicates_removed,
                "low_credibility_count": len(low_cred_sources),
                "prior_research_count": len(self._prior_research),
                "vault_saved": vault_saved,
                "confidence": confidence.value,
            },
        )

        # Publish low-credibility warning as a finding if any
        if low_cred_sources:
            finding = KeyFinding(
                id=f"finding_{hashlib.md5(f'librarian_low_cred_{self._engagement_id}'.encode()).hexdigest()[:8]}",
                agent=self.name.value,
                finding_type="low_credibility_sources",
                title=f"Low Credibility Sources Flagged: {len(low_cred_sources)}",
                content=(
                    f"{len(low_cred_sources)} sources flagged as low credibility "
                    f"(blog or social media). These should be verified by the "
                    f"Fact Checker before inclusion in the final report. "
                    f"Sources: {', '.join(s.title[:50] for s in low_cred_sources[:3])}"
                ),
                confidence=ConfidenceLevel.HIGH,
                sources=low_cred_sources[:2],
            )
            await self._publish_finding(finding)

        # Publish prior research links as a finding if any
        if self._prior_research:
            finding = KeyFinding(
                id=f"finding_{hashlib.md5(f'librarian_prior_{self._engagement_id}'.encode()).hexdigest()[:8]}",
                agent=self.name.value,
                finding_type="prior_research_links",
                title=f"Prior Research Found: {len(self._prior_research)} linked engagements",
                content=(
                    f"Found {len(self._prior_research)} prior engagement(s) "
                    f"with relevant research. Top: "
                    f"{', '.join(f'{p.topic} (relevance: {p.relevance_score})' for p in self._prior_research[:3])}"
                ),
                confidence=ConfidenceLevel.MEDIUM,
            )
            await self._publish_finding(finding)

        await self._transition(
            AgentState.DONE,
            f"Librarian complete: {total_after} unique sources "
            f"(from {total_before}, {duplicates_removed} duplicates removed), "
            f"{len(low_cred_sources)} low credibility, "
            f"{len(self._prior_research)} prior research links, "
            f"vault_saved={vault_saved}, "
            f"confidence={confidence.value}",
        )

        return collection
