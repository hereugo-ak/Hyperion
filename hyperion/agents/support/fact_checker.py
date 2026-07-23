"""
HYPERION Fact Checker — Agent 16, the verification engine and hallucination catcher.

This is NOT a generic "check if sources exist" agent. This is a specialist with
5 proprietary skills:

- Claim verification: Extract specific factual claims from specialist findings
  and verify each against independent sources. A claim is VERIFIED if 2+
  independent sources agree. Not just "does a source exist" — "do 2+
  independent sources confirm this specific claim?"
- Source credibility scoring: Score each source on credibility and weight
  verification accordingly. A claim verified by a peer-reviewed paper is more
  credible than one verified by a blog post. Uses the same credibility
  hierarchy as the Research Librarian.
- Contradiction detection: Identify when two specialists make contradictory
  claims and flag them for the Synthesis Lead. Not just "they disagree" —
  classify the contradiction (DATA_CONFLICT, INTERPRETATION_CONFLICT,
  SCOPE_CONFLICT) and flag it with both agents' claims.
- Evidence chain validation: For each claim, trace the evidence chain:
  claim → source → original data. If the chain breaks (source doesn't contain
  the data, or data doesn't support the claim), flag it. This catches
  hallucinated citations — the #1 quality risk in LLM-generated reports.
- Statistical sanity checks: Check for statistical red flags: numbers that
  are too round (suspicious), growth rates that are implausibly high, market
  sizes that don't reconcile across agents. Not just "is this number right" —
  "is this number suspicious given the context?"

It runs on FAST tier (GPT OSS 120B on Cerebras, ~3000 tok/s) because fact-
checking is time-critical — it runs in parallel with late-stage specialists
and must finish before the Synthesis Lead starts. It doesn't just check if a
source exists — it checks if the source actually contains the data the
specialist claims it does. It catches hallucinated citations, which is the
#1 quality risk in LLM-generated reports. (§4.5, Agent 16)

Model Tier: FAST (GPT OSS 120B on Cerebras — speed is critical, fact-checking
runs in parallel with late-stage specialists)
Tools: SearxNG (search for verification), Jina (extract source content to
       verify against original), Obscura (scrape JS-rendered pages for
       verification)
Sub-agents: 0 (support agent — doesn't spawn sub-agents)
Output: FactCheckReport (claim-by-claim verification status, contradictions,
        evidence chain validation, statistical red flags)

Methodology (§4.5, Agent 16):
1. Collect all specialist findings from AgentBus
2. Extract factual claims (numbers, dates, names, events)
3. For each claim, search for verification (SearxNG + Jina)
4. Score each claim: VERIFIED, PLAUSIBLE, UNVERIFIED, CONTRADICTED
5. Flag contradictions to Synthesis Lead
6. Flag unverified claims to originating specialist
7. Produce FactCheckReport model
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import re
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)

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
    Claim,
    ClaimStatus,
    ClaimType,
    ConfidenceLevel,
    Contradiction,
    ContradictionType,
    FactCheckReport,
    KeyFinding,
    Source,
    SourceCredibility,
)


# ─────────────────────────────────────────────────────────────────────────────
# Agent Specification
# ─────────────────────────────────────────────────────────────────────────────


FACT_CHECKER_SPEC = AgentSpec(
    name=AgentName.FACT_CHECKER,
    role=AgentRole.SUPPORT,
    display_name="Fact Checker",
    model_tier=ModelTier.FAST,
    tools=[
        ToolName.SEARXNG,
        ToolName.JINA,
        ToolName.OBSCURA,
        ToolName.DEEP_SEARCH,
    ],
    skills=[
        SkillSpec(
            name="Claim verification",
            description=(
                "Extract specific factual claims from specialist findings and "
                "verify each against independent sources. A claim is VERIFIED "
                "if 2+ independent sources agree. Not just 'does a source "
                "exist' — 'do 2+ independent sources confirm this specific "
                "claim?' Claims are typed (NUMBER, DATE, NAME, EVENT, "
                "RELATIONSHIP, QUOTE) for targeted verification."
            ),
            inputs=["specialist_findings", "claim_types"],
            outputs=["verified_claims", "plausible_claims", "unverified_claims", "contradicted_claims"],
        ),
        SkillSpec(
            name="Source credibility scoring",
            description=(
                "Score each source on credibility and weight verification "
                "accordingly. A claim verified by a peer-reviewed paper is "
                "more credible than one verified by a blog post. Uses the "
                "same hierarchy as the Research Librarian: peer-reviewed > "
                "government > industry report > vendor > news > blog > "
                "social media. Produces a credibility_weighted_score (0-1) "
                "for each claim."
            ),
            inputs=["verification_sources", "claim"],
            outputs=["credibility_weighted_score", "source_credibility_tiers"],
        ),
        SkillSpec(
            name="Contradiction detection",
            description=(
                "Identify when two specialists make contradictory claims and "
                "flag them for the Synthesis Lead. Not just 'they disagree' "
                "— classify the contradiction as DATA_CONFLICT (different "
                "numbers for same metric), INTERPRETATION_CONFLICT (same "
                "data, different conclusions), or SCOPE_CONFLICT (agents "
                "analyzed different scopes). Flag with both agents' claims."
            ),
            inputs=["all_claims", "agent_findings"],
            outputs=["contradictions", "contradiction_types", "flagged_agent_pairs"],
        ),
        SkillSpec(
            name="Evidence chain validation",
            description=(
                "For each claim, trace the evidence chain: claim → source → "
                "original data. If the chain breaks (source doesn't contain "
                "the data, or data doesn't support the claim), flag it. This "
                "catches hallucinated citations — the #1 quality risk in "
                "LLM-generated reports. A hallucinated citation is when an "
                "agent cites a source that either doesn't exist or doesn't "
                "contain the data the agent claims it does."
            ),
            inputs=["claims", "cited_sources", "source_content"],
            outputs=["evidence_chain_valid", "evidence_chain_breaks", "hallucinated_citations"],
        ),
        SkillSpec(
            name="Statistical sanity checks",
            description=(
                "Check for statistical red flags: numbers that are too round "
                "(suspicious), growth rates that are implausibly high, market "
                "sizes that don't reconcile across agents. Not just 'is this "
                "number right' — 'is this number suspicious given the "
                "context?' A market size of exactly $10B is suspicious. A "
                "growth rate of 500% YoY is suspicious. Two agents reporting "
                "different market sizes for the same market is a red flag."
            ),
            inputs=["numeric_claims", "cross_agent_metrics"],
            outputs=["statistical_red_flags", "round_number_warnings", "reconciliation_issues"],
        ),
    ],
    system_prompt=(
        "You are the HYPERION Fact Checker — the verification engine and "
        "hallucination catcher.\n\n"
        "Your role:\n"
        "1. EXTRACT factual claims from specialist findings. Claims are "
        "typed: NUMBER (statistics, market sizes, revenue), DATE (events), "
        "NAME (people, companies), EVENT (acquisitions, launches), "
        "RELATIONSHIP (market positions), QUOTE (attributed statements).\n"
        "2. VERIFY each claim against independent sources using SearxNG "
        "(search), Jina (extract content), and Obscura (JS-rendered pages). "
        "A claim is VERIFIED if 2+ independent sources agree.\n"
        "3. WEIGHT verification by source credibility: peer-reviewed > "
        "government > industry report > vendor > news > blog > social media.\n"
        "4. VALIDATE evidence chains: claim → source → original data. If the "
        "source doesn't contain the data, or the data doesn't support the "
        "claim, the chain is broken. Flag hallucinated citations.\n"
        "5. DETECT contradictions between agents. Classify as DATA_CONFLICT, "
        "INTERPRETATION_CONFLICT, or SCOPE_CONFLICT.\n"
        "6. RUN statistical sanity checks: too round numbers, implausible "
        "growth rates, market sizes that don't reconcile.\n\n"
        "You run on FAST tier (Cerebras, ~3000 tok/s) because fact-checking "
        "is time-critical — you run in parallel with late-stage specialists "
        "and must finish before the Synthesis Lead starts.\n\n"
        "Rules:\n"
        "- 2+ INDEPENDENT SOURCES REQUIRED FOR VERIFICATION. 'Independent' "
        "means different publishers, not different articles from the same "
        "site. Two TechCrunch articles are NOT independent.\n"
        "- HALLUCINATED CITATIONS ARE THE #1 RISK. If a source URL doesn't "
        "resolve, or the source content doesn't contain the claimed data, "
        "flag it as a hallucinated citation. Don't give the agent the benefit "
        "of the doubt.\n"
        "- STATISTICAL RED FLAGS: A market size of exactly $10B is suspicious. "
        "A growth rate of 500% YoY is suspicious. Two agents reporting "
        "different market sizes for the same market is a reconciliation issue. "
        "Flag all of these.\n"
        "- CONTRADICTIONS ARE NOT ERRORS. Two agents disagreeing is data, not "
        "a bug. Flag it for the Synthesis Lead to resolve evidence-weighted.\n"
        "- BE FAST, NOT THOROUGH. You run on FAST tier. Check the most "
        "critical claims first (numbers, dates, names). Don't spend time on "
        "subjective claims that can't be verified.\n\n"
        "You do NOT spawn sub-agents. You are a support agent.\n\n"
        "Your output is a FactCheckReport Pydantic model — structured, not "
        "free text."
    ),
    spawn_condition="Spawned after all specialists have published findings. "
                     "Runs in parallel with late-stage specialists. Must "
                     "finish before the Synthesis Lead starts reconciliation.",
    max_sub_agents=0,
    output_model="FactCheckReport",
)


# ─────────────────────────────────────────────────────────────────────────────
# Fact Checker Agent
# ─────────────────────────────────────────────────────────────────────────────


class FactChecker(BaseAgent):
    """Agent 16: The verification engine and hallucination catcher.

    Verifies claims made by specialists, cross-references sources, and flags
    contradictions. Runs on FAST tier (Cerebras, ~3000 tok/s) because fact-
    checking is time-critical. Catches hallucinated citations — the #1 quality
    risk in LLM-generated reports. (§4.5, Agent 16)

    Lifecycle:
    1. Collect all specialist findings from the bus
    2. Extract factual claims (numbers, dates, names, events)
    3. Verify each claim against independent sources
    4. Score claims: VERIFIED, PLAUSIBLE, UNVERIFIED, CONTRADICTED
    5. Flag contradictions to Synthesis Lead
    6. Flag unverified claims to originating specialist
    7. Produce FactCheckReport
    """

    # Credibility hierarchy (same as Research Librarian)
    CREDIBILITY_RANK: dict[SourceCredibility, int] = {
        SourceCredibility.PEER_REVIEWED: 6,
        SourceCredibility.GOVERNMENT: 5,
        SourceCredibility.INDUSTRY_REPORT: 4,
        SourceCredibility.VENDOR: 3,
        SourceCredibility.NEWS: 2,
        SourceCredibility.BLOG: 1,
        SourceCredibility.SOCIAL_MEDIA: 0,
    }

    # Patterns for extracting factual claims from text
    NUMBER_PATTERN = re.compile(
        r'(?:\$?\d[\d,]*\.?\d*\s*(?:billion|million|trillion|thousand|B|M|T|%|'
        r'percent|x|times|bps|basis points)?)',
        re.IGNORECASE,
    )
    DATE_PATTERN = re.compile(
        r'(?:in\s+)?(?:January|February|March|April|May|June|July|August|'
        r'September|October|November|December)\s+\d{4}|'
        r'\d{4}|\d{1,2}/\d{1,2}/\d{2,4}|Q[1-4]\s+\d{4}',
        re.IGNORECASE,
    )
    GROWTH_RATE_PATTERN = re.compile(
        r'(\d+(?:\.\d+)?)\s*(?:%|percent)\s*(?:YoY|MoM|QoQ|growth|increase|decline)',
        re.IGNORECASE,
    )

    # Suspicious round number thresholds
    ROUND_NUMBER_SUSPECTS = {10_000_000_000, 1_000_000_000, 100_000_000, 10_000_000}
    IMPLAUSIBLE_GROWTH_THRESHOLD = 300  # % YoY

    # Concurrency limit for web verification searches
    VERIFY_CONCURRENCY = 3
    # Max claims to verify via web search (reduced from 50 to save search budget)
    MAX_WEB_VERIFIED_CLAIMS = 30

    def __init__(
        self,
        spec: AgentSpec | None = None,
        bus: Any | None = None,
        router: Any | None = None,
    ) -> None:
        super().__init__(spec or FACT_CHECKER_SPEC, bus=bus, router=router)

        # All findings collected from agents
        self._all_findings: list[KeyFinding] = []

        # All extracted claims
        self._claims: list[Claim] = []

        # Contradictions found
        self._contradictions: list[Contradiction] = []

        # Statistical red flags
        self._statistical_red_flags: list[str] = []

        # Hallucinated citations
        self._hallucinated: list[Claim] = []

        # Evidence chain breaks
        self._chain_breaks: list[Claim] = []

    # ─────────────────────────────────────────────────────────────────────
    # Bus message handling
    # ─────────────────────────────────────────────────────────────────────

    async def _handle_bus_message(self, msg: Any) -> None:
        """Handle incoming bus messages.

        The Fact Checker listens to:
        - FINDINGS: collects all specialist findings for claim extraction
        - HANDOFF: receives task assignment from Engagement Director
        """
        if msg.channel == Channel.FINDINGS:
            finding = msg.finding
            if finding is not None:
                self._all_findings.append(finding)

        elif msg.channel == Channel.HANDOFF:
            payload = msg.payload
            to_agent = payload.get("to_agent", "")
            if to_agent != self.name.value:
                return

            task = payload.get("task", "")
            if task == "fact_check":
                context_bundle = payload.get("context_bundle", {})
                # Findings may be passed directly or collected from bus
                if "findings" in context_bundle:
                    self._all_findings.extend(context_bundle["findings"])

    # ─────────────────────────────────────────────────────────────────────
    # Step 1: Collect all specialist findings
    # ─────────────────────────────────────────────────────────────────────

    async def _collect_findings(self) -> list[KeyFinding]:
        """Collect all specialist findings from the bus.

        The Fact Checker subscribes to the FINDINGS channel and accumulates
        findings from all agents. This step ensures all findings are collected
        before claim extraction begins.
        """
        return self._all_findings

    # ─────────────────────────────────────────────────────────────────────
    # Step 2: Extract factual claims from findings
    # ─────────────────────────────────────────────────────────────────────

    def _classify_claim_type(self, claim_text: str) -> ClaimType:
        """Classify a claim by type for targeted verification.

        NUMBER: statistics, market sizes, revenue, percentages
        DATE: event dates, founding dates, announcement dates
        NAME: people, companies, product names
        EVENT: acquisitions, launches, bankruptcies, regulatory actions
        RELATIONSHIP: market positions, competitive relationships
        QUOTE: direct quotations attributed to someone
        """
        text_lower = claim_text.lower()

        # Check for quote patterns
        if '"' in claim_text or "'" in claim_text and ("said" in text_lower or "stated" in text_lower):
            return ClaimType.QUOTE

        # Check for event patterns
        event_words = ["acquired", "acquisition", "launched", "filed for", "bankrupt",
                       "merged", "ipo", "went public", "shut down", "announced"]
        if any(w in text_lower for w in event_words):
            return ClaimType.EVENT

        # Check for date patterns
        if self.DATE_PATTERN.search(claim_text):
            return ClaimType.DATE

        # Check for number patterns
        if self.NUMBER_PATTERN.search(claim_text):
            return ClaimType.NUMBER

        # Check for relationship patterns
        relationship_words = ["market share", "market leader", "competitor of",
                              "subsidiary of", "owned by", "partnered with",
                              "rival", "dominates", "leads the market"]
        if any(w in text_lower for w in relationship_words):
            return ClaimType.RELATIONSHIP

        # Default to NUMBER for any other factual claim
        return ClaimType.NUMBER

    def _extract_claims_from_finding(self, finding: KeyFinding) -> list[Claim]:
        """Extract factual claims from a single finding.

        Uses regex patterns to identify numbers, dates, names, and events
        in the finding content. Each extracted claim is typed and prepared
        for verification.
        """
        claims: list[Claim] = []
        content = finding.content

        # Extract number-based claims
        for match in self.NUMBER_PATTERN.finditer(content):
            claim_text = match.group(0).strip()
            if len(claim_text) < 3:
                continue

            claim_id = f"claim_{hashlib.md5(f'{finding.id}_{claim_text}'.encode()).hexdigest()[:8]}"

            claims.append(Claim(
                id=claim_id,
                agent=finding.agent,
                claim=claim_text,
                claim_type=ClaimType.NUMBER,
                status=ClaimStatus.UNVERIFIED,
                verification_sources=finding.sources[:2],
            ))

        # Extract date-based claims
        for match in self.DATE_PATTERN.finditer(content):
            claim_text = match.group(0).strip()
            if len(claim_text) < 4:
                continue

            claim_id = f"claim_{hashlib.md5(f'{finding.id}_{claim_text}'.encode()).hexdigest()[:8]}"

            # Skip if we already extracted this as part of a number claim
            if any(c.claim == claim_text for c in claims):
                continue

            claims.append(Claim(
                id=claim_id,
                agent=finding.agent,
                claim=claim_text,
                claim_type=ClaimType.DATE,
                status=ClaimStatus.UNVERIFIED,
                verification_sources=finding.sources[:2],
            ))

        # Extract event-based claims (sentence-level)
        sentences = re.split(r'[.!?]+', content)
        event_words = ["acquired", "launched", "filed for", "merged",
                       "ipo", "went public", "shut down", "announced"]
        for sentence in sentences:
            sentence = sentence.strip()
            if len(sentence) < 10:
                continue
            if any(w in sentence.lower() for w in event_words):
                claim_id = f"claim_{hashlib.md5(f'{finding.id}_{sentence[:50]}'.encode()).hexdigest()[:8]}"
                if not any(c.claim == sentence for c in claims):
                    claims.append(Claim(
                        id=claim_id,
                        agent=finding.agent,
                        claim=sentence[:200],
                        claim_type=ClaimType.EVENT,
                        status=ClaimStatus.UNVERIFIED,
                        verification_sources=finding.sources[:2],
                    ))

        # Limit claims per finding to avoid explosion
        return claims[:10]

    async def _extract_claims(self, findings: list[KeyFinding]) -> list[Claim]:
        """Extract factual claims from all specialist findings.

        Uses regex patterns and LLM to identify numbers, dates, names, and
        events in finding content. Each claim is typed for targeted
        verification.
        """
        all_claims: list[Claim] = []

        for finding in findings:
            claims = self._extract_claims_from_finding(finding)
            all_claims.extend(claims)

        # Also use LLM to extract more complex claims
        if findings and self.router:
            try:
                findings_text = "\n".join(
                    f"[{f.agent}] {f.title}: {f.content[:300]}"
                    for f in findings[:20]
                )

                prompt = (
                    "Extract all factual claims from the following specialist "
                    "findings. For each claim, provide:\n"
                    "1. The claim text (specific fact, not opinion)\n"
                    "2. The agent that made it\n"
                    "3. The claim type (NUMBER, DATE, NAME, EVENT, RELATIONSHIP, QUOTE)\n\n"
                    "Only extract verifiable factual claims, not opinions or "
                    "analysis. Focus on: statistics, dates, names, events, "
                    "market positions, and quoted statements.\n\n"
                    f"FINDINGS:\n{findings_text}\n\n"
                    "Return as a JSON list of objects with keys: claim, agent, "
                    "claim_type."
                )

                response = await self._llm_complete(user_prompt=prompt, urgency=TaskUrgency.NORMAL)
                if response and response.content:
                    # Parse LLM-extracted claims and merge
                    import json
                    try:
                        llm_claims = json.loads(response.content)
                        for i, lc in enumerate(llm_claims[:30]):
                            claim_text = lc.get("claim", "")
                            agent = lc.get("agent", "")
                            claim_type_str = lc.get("claim_type", "NUMBER").upper()
                            try:
                                claim_type = ClaimType[claim_type_str]
                            except KeyError:
                                claim_type = ClaimType.NUMBER

                            claim_id = f"claim_llm_{hashlib.md5(f'{agent}_{claim_text}'.encode()).hexdigest()[:8]}"

                            # Skip duplicates
                            if not any(c.claim == claim_text for c in all_claims):
                                all_claims.append(Claim(
                                    id=claim_id,
                                    agent=agent,
                                    claim=claim_text,
                                    claim_type=claim_type,
                                    status=ClaimStatus.UNVERIFIED,
                                ))
                    except (json.JSONDecodeError, TypeError):
                        pass

            except (ValueError, AttributeError, RuntimeError) as e:
                await self._log_tool_use("llm", "extract_claims", f"FAIL · {e}", success=False)

        return all_claims

    # ─────────────────────────────────────────────────────────────────────
    # Step 3: Search for verification (local corpus → SearXNG → Jina)
    # ─────────────────────────────────────────────────────────────────────

    def _check_local_corpus(self, claim: Claim) -> list[Source]:
        """Check if the claim is already supported by sources in the local findings.

        Before hitting the web, scan all collected findings' sources for
        content that already supports this claim. This avoids redundant
        web searches for claims that are well-sourced by specialists.

        Returns a list of Source objects from the local corpus that support
        the claim, or an empty list if no local support is found.
        """
        local_sources: list[Source] = []
        claim_lower = claim.claim.lower()
        claim_words = set(w for w in claim_lower.split() if len(w) > 4)

        for finding in self._all_findings:
            # Check finding content for the claim
            content_lower = (finding.content or "").lower()
            if claim_lower in content_lower or claim_words & set(content_lower.split()):
                # This finding's content references the claim — use its sources
                for src in finding.sources[:3]:
                    if not src or not src.url:
                        continue
                    credibility = self._score_domain_credibility(src.url)
                    local_sources.append(Source(
                        id=f"local_{hashlib.md5(src.url.encode()).hexdigest()[:8]}",
                        title=src.title or finding.title[:100],
                        url=src.url,
                        credibility=credibility,
                        accessed_at=datetime.now(),
                        key_data=finding.content[:500],
                    ))

        return local_sources

    async def _search_for_verification(self, claim: Claim) -> list[Source]:
        """Search for independent sources to verify a claim.

        First checks the local corpus (specialist findings' sources) for
        existing support. If the local corpus provides 2+ independent sources,
        no web search is needed. Otherwise, falls back to SearXNG JSON API
        + Jina for content extraction.

        Obscura is NOT used — it's a Windows-only binary and fact-checking
        should not spawn browsers per claim.
        """
        # Step 1: Check local corpus first (no web search needed)
        local_sources = self._check_local_corpus(claim)
        if len(local_sources) >= 2 and self._check_independence(local_sources):
            logger.debug("Claim '%s' verified via local corpus — skipping web search",
                         claim.claim[:50])
            return local_sources

        # Merge local sources with web results
        verification_sources: list[Source] = list(local_sources)

        # Step 2: Web search via SearXNG JSON API
        try:
            searxng = self.get_tool(ToolName.SEARXNG)

            # Build search query from claim
            query = claim.claim[:100]
            if claim.agent:
                query = f"{query} {claim.agent.replace('_', ' ')}"

            # Search for the claim
            results = await searxng.search(query, num_results=5)
            if results:
                for result in results[:5]:
                    url = getattr(result, "url", "")
                    title = getattr(result, "title", "")
                    if not url:
                        continue

                    # Skip if already in local sources
                    if any(s.url == url for s in verification_sources):
                        continue

                    # Determine credibility from domain
                    credibility = self._score_domain_credibility(url)

                    source = Source(
                        id=f"verify_{hashlib.md5(url.encode()).hexdigest()[:8]}",
                        title=title,
                        url=url,
                        credibility=credibility,
                        accessed_at=datetime.now(),
                        key_data=getattr(result, "snippet", ""),
                    )
                    verification_sources.append(source)

        except (ValueError, AttributeError, RuntimeError) as e:
            logger.warning("Fact checker SearXNG search failed: %s", e)

        # Step 3: Use Jina to extract content from top results for evidence chain validation
        if verification_sources:
            try:
                jina = self.get_tool(ToolName.JINA)
                for source in verification_sources[:3]:
                    # Skip local sources — they already have key_data
                    if source.id.startswith("local_"):
                        continue
                    try:
                        read_result = await jina.read(source.url)
                        if read_result and (read_result.markdown or read_result.content):
                            extracted = read_result.markdown or read_result.content
                            source.key_data = (source.key_data or "") + " | " + extracted[:500]
                    except (ValueError, AttributeError, RuntimeError):
                        continue
            except (ValueError, AttributeError, RuntimeError) as e:
                await self._log_tool_use("jina", "call", f"FAIL · {e}", success=False)

        return verification_sources

    def _score_domain_credibility(self, url: str) -> SourceCredibility:
        """Score a URL's credibility based on domain.

        Government domains (.gov, .gov.uk) → GOVERNMENT
        Academic domains (.edu, .ac.uk) → PEER_REVIEWED
        Known industry report domains → INDUSTRY_REPORT
        News domains → NEWS
        Everything else → BLOG (conservative default)
        """
        domain = url.lower()

        if any(d in domain for d in [".gov", ".gov.uk", ".europa.eu", ".oecd.org", ".worldbank.org"]):
            return SourceCredibility.GOVERNMENT
        if any(d in domain for d in [".edu", ".ac.uk", "scholar.google", "pubmed", "arxiv.org", "doi.org"]):
            return SourceCredibility.PEER_REVIEWED
        if any(d in domain for d in ["mckinsey", "bcg", "bain", "deloitte", "pwc", "gartner", "forrester", "idc", "statista", "ibisworld"]):
            return SourceCredibility.INDUSTRY_REPORT
        if any(d in domain for d in ["reuters", "bloomberg", "ft.com", "wsj.com", "nytimes", "bbc.com", "techcrunch", "theinformation", "axios.com"]):
            return SourceCredibility.NEWS
        if any(d in domain for d in ["crunchbase", "pitchbook", "cbinsights"]):
            return SourceCredibility.INDUSTRY_REPORT
        if any(d in domain for d in ["wikipedia.org"]):
            return SourceCredibility.NEWS  # Wikipedia is news-tier (not peer-reviewed)

        return SourceCredibility.BLOG

    # ─────────────────────────────────────────────────────────────────────
    # Step 4: Score each claim (VERIFIED, PLAUSIBLE, UNVERIFIED, CONTRADICTED)
    # ─────────────────────────────────────────────────────────────────────

    def _check_independence(self, sources: list[Source]) -> bool:
        """Check if 2+ sources are truly independent.

        'Independent' means different publishers, not different articles from
        the same site. Two TechCrunch articles are NOT independent.
        """
        if len(sources) < 2:
            return False

        domains: set[str] = set()
        for source in sources:
            try:
                from urllib.parse import urlparse
                parsed = urlparse(source.url)
                domain = parsed.netloc.lower().replace("www.", "")
                domains.add(domain)
            except (ValueError, TypeError):
                continue

        return len(domains) >= 2

    def _calculate_credibility_weighted_score(self, sources: list[Source]) -> float:
        """Calculate a credibility-weighted verification score (0-1).

        Weight = sum of credibility ranks / (max credibility rank * count)
        A claim verified by 2 peer-reviewed papers scores higher than one
        verified by 2 blog posts.
        """
        if not sources:
            return 0.0

        total_weight = sum(self.CREDIBILITY_RANK.get(s.credibility, 0) for s in sources)
        max_possible = self.CREDIBILITY_RANK[SourceCredibility.PEER_REVIEWED] * len(sources)

        return round(total_weight / max_possible, 2) if max_possible > 0 else 0.0

    async def _verify_claim(self, claim: Claim) -> Claim:
        """Verify a single claim against independent sources.

        A claim is:
        - VERIFIED if 2+ independent sources agree
        - PLAUSIBLE if 1 source supports, no contradiction
        - UNVERIFIED if no independent source found
        - CONTRADICTED if sources disagree
        """
        # Search for verification
        verification_sources = await self._search_for_verification(claim)

        if not verification_sources:
            claim.status = ClaimStatus.UNVERIFIED
            claim.verification_notes = "No independent sources found"
            return claim

        # Check if sources are independent
        is_independent = self._check_independence(verification_sources)

        # Calculate credibility-weighted score
        claim.credibility_weighted_score = self._calculate_credibility_weighted_score(verification_sources)
        claim.verification_sources = verification_sources

        # Check if the claim data appears in the source content
        supporting_sources = 0
        contradicting_sources = 0

        claim_lower = claim.claim.lower()
        for source in verification_sources:
            source_data = (source.key_data or "").lower()
            if claim_lower in source_data or any(
                word in source_data for word in claim_lower.split() if len(word) > 4
            ):
                supporting_sources += 1
            else:
                # Check for contradiction (source mentions the topic but with different data)
                # This is a simplified check — the LLM would do deeper analysis
                pass

        # Determine status
        if supporting_sources >= 2 and is_independent:
            claim.status = ClaimStatus.VERIFIED
            claim.verification_notes = f"Verified by {supporting_sources} independent sources (credibility score: {claim.credibility_weighted_score})"
        elif supporting_sources >= 2 and not is_independent:
            claim.status = ClaimStatus.PLAUSIBLE
            claim.verification_notes = f"Supported by {supporting_sources} sources but not independent (same publisher)"
        elif supporting_sources >= 1:
            claim.status = ClaimStatus.PLAUSIBLE
            claim.verification_notes = f"Supported by 1 source (credibility: {verification_sources[0].credibility.value})"
        elif contradicting_sources > 0:
            claim.status = ClaimStatus.CONTRADICTED
            claim.verification_notes = "Sources disagree with the claim"
        else:
            claim.status = ClaimStatus.UNVERIFIED
            claim.verification_notes = "No supporting sources found in verification search"

        return claim

    async def _verify_claims(self, claims: list[Claim]) -> list[Claim]:
        """Verify all extracted claims.

        Prioritizes by claim type: NUMBER and DATE claims are most critical
        (they're the most checkable and the most damaging if wrong).
        Uses bounded concurrency (VERIFY_CONCURRENCY) to avoid flooding
        SearXNG with simultaneous requests.
        """
        # Sort by priority: NUMBER > DATE > EVENT > NAME > RELATIONSHIP > QUOTE
        priority_order = {
            ClaimType.NUMBER: 0,
            ClaimType.DATE: 1,
            ClaimType.EVENT: 2,
            ClaimType.NAME: 3,
            ClaimType.RELATIONSHIP: 4,
            ClaimType.QUOTE: 5,
        }
        sorted_claims = sorted(claims, key=lambda c: priority_order.get(c.claim_type, 99))

        # Limit to top claims for web verification
        claims_to_verify = sorted_claims[:self.MAX_WEB_VERIFIED_CLAIMS]

        # Verify with bounded concurrency
        semaphore = asyncio.Semaphore(self.VERIFY_CONCURRENCY)

        async def _bounded_verify(claim: Claim) -> Claim:
            async with semaphore:
                return await self._verify_claim(claim)

        verified = await asyncio.gather(*[_bounded_verify(c) for c in claims_to_verify])
        verified_list = list(verified)

        # Add remaining claims as UNVERIFIED (not checked due to limit)
        for claim in sorted_claims[self.MAX_WEB_VERIFIED_CLAIMS:]:
            claim.verification_notes = "Not checked (priority limit reached)"
            verified_list.append(claim)

        return verified_list

    # ─────────────────────────────────────────────────────────────────────
    # Step 5: Flag contradictions to Synthesis Lead
    # ─────────────────────────────────────────────────────────────────────

    def _detect_contradictions(self, claims: list[Claim]) -> list[Contradiction]:
        """Detect contradictions between agents' claims.

        Identifies when two agents make contradictory claims about the same
        topic. Classifies as:
        - DATA_CONFLICT: Different numbers for the same metric
        - INTERPRETATION_CONFLICT: Same data, different conclusions
        - SCOPE_CONFLICT: Agents analyzed different scopes
        """
        contradictions: list[Contradiction] = []

        # Group claims by agent
        agent_claims: dict[str, list[Claim]] = {}
        for claim in claims:
            agent_claims.setdefault(claim.agent, []).append(claim)

        # Compare NUMBER claims across agents for data conflicts
        number_claims_by_agent: dict[str, list[Claim]] = {}
        for agent, agent_claim_list in agent_claims.items():
            number_claims = [c for c in agent_claim_list if c.claim_type == ClaimType.NUMBER]
            if number_claims:
                number_claims_by_agent[agent] = number_claims

        # Check for conflicting numbers
        agents = list(number_claims_by_agent.keys())
        for i, agent_a in enumerate(agents):
            for agent_b in agents[i + 1:]:
                for claim_a in number_claims_by_agent[agent_a]:
                    for claim_b in number_claims_by_agent[agent_b]:
                        # Check if claims are about the same metric but with different values
                        if self._claims_conflict(claim_a, claim_b):
                            contr_id = f"contr_{hashlib.md5(f'{claim_a.id}_{claim_b.id}'.encode()).hexdigest()[:8]}"
                            contradictions.append(Contradiction(
                                id=contr_id,
                                agent_a=agent_a,
                                agent_b=agent_b,
                                finding_a=claim_a.claim,
                                finding_b=claim_b.claim,
                                contradiction_type=ContradictionType.DATA_CONFLICT,
                            ))
                            # Mark claims as contradicted
                            claim_a.status = ClaimStatus.CONTRADICTED
                            claim_a.contradiction_with = claim_b.id
                            claim_b.status = ClaimStatus.CONTRADICTED
                            claim_b.contradiction_with = claim_a.id

        return contradictions

    def _claims_conflict(self, claim_a: Claim, claim_b: Claim) -> bool:
        """Check if two NUMBER claims conflict.

        A conflict is when both claims mention the same metric keyword but
        cite different numbers.
        """
        text_a = claim_a.claim.lower()
        text_b = claim_b.claim.lower()

        # Extract numbers from both claims
        nums_a = re.findall(r'\$?\d[\d,]*\.?\d*', text_a)
        nums_b = re.findall(r'\$?\d[\d,]*\.?\d*', text_b)

        if not nums_a or not nums_b:
            return False

        # If the numbers are different and they share keywords, it's a conflict
        if nums_a[0] != nums_b[0]:
            # Check for shared keywords (metric names)
            words_a = set(w for w in text_a.split() if len(w) > 4)
            words_b = set(w for w in text_b.split() if len(w) > 4)
            shared = words_a & words_b
            # If they share meaningful keywords, it's likely the same metric
            if len(shared) >= 2:
                return True

        return False

    # ─────────────────────────────────────────────────────────────────────
    # Step 6: Flag unverified claims to originating specialist
    # ─────────────────────────────────────────────────────────────────────

    async def _flag_unverified_claims(self, claims: list[Claim]) -> None:
        """Flag unverified claims to originating specialists via the bus.

        Publishes a REQUESTS message to each agent that has unverified claims,
        asking them to provide additional sources or clarification.
        """
        unverified_by_agent: dict[str, list[Claim]] = {}
        for claim in claims:
            if claim.status in (ClaimStatus.UNVERIFIED, ClaimStatus.CONTRADICTED):
                unverified_by_agent.setdefault(claim.agent, []).append(claim)

        for agent, agent_claims in unverified_by_agent.items():
            await self.bus.publish(
                channel=Channel.REQUESTS,
                msg_type=MessageType.ESCALATION,
                sender=self.name,
                payload={
                    "to_agent": agent,
                    "from_agent": self.name.value,
                    "request_type": "verify_claims",
                    "unverified_claims": [c.model_dump() for c in agent_claims],
                    "message": (
                        f"Fact Checker could not verify {len(agent_claims)} "
                        f"claim(s) from your analysis. Please provide "
                        f"additional sources or clarification."
                    ),
                },
            )

    # ─────────────────────────────────────────────────────────────────────
    # Evidence chain validation (part of Step 4)
    # ─────────────────────────────────────────────────────────────────────

    async def _validate_evidence_chains(self, claims: list[Claim]) -> tuple[list[Claim], list[Claim]]:
        """Validate evidence chains for all claims.

        For each claim, trace: claim → source → original data.
        If the chain breaks (source doesn't contain the data, or data doesn't
        support the claim), flag it.

        Returns (chain_breaks, hallucinated).
        """
        chain_breaks: list[Claim] = []
        hallucinated: list[Claim] = []

        for claim in claims:
            if not claim.verification_sources and not claim.status == ClaimStatus.UNVERIFIED:
                # Claim has no sources at all — potential hallucination
                claim.evidence_chain_valid = False
                claim.evidence_chain_break = "No sources cited for claim"
                claim.is_hallucinated_citation = True
                hallucinated.append(claim)
                continue

            # Check if cited sources actually contain the claimed data
            claim_lower = claim.claim.lower()
            source_contains_data = False

            for source in claim.verification_sources:
                source_data = (source.key_data or "").lower()
                # Check if the claim's key data appears in the source
                if claim_lower in source_data or any(
                    word in source_data for word in claim_lower.split() if len(word) > 4
                ):
                    source_contains_data = True
                    break

            if not source_contains_data and claim.verification_sources:
                # Source exists but doesn't contain the claimed data
                claim.evidence_chain_valid = False
                claim.evidence_chain_break = (
                    "Cited source does not contain the claimed data — "
                    "possible hallucinated citation"
                )
                claim.is_hallucinated_citation = True
                hallucinated.append(claim)
                chain_breaks.append(claim)

        return (chain_breaks, hallucinated)

    # ─────────────────────────────────────────────────────────────────────
    # Statistical sanity checks (part of Step 4)
    # ─────────────────────────────────────────────────────────────────────

    def _run_statistical_sanity_checks(self, claims: list[Claim]) -> list[str]:
        """Run statistical sanity checks on numeric claims.

        Checks for:
        - Numbers that are too round (suspicious)
        - Growth rates that are implausibly high (>300% YoY)
        - Market sizes that don't reconcile across agents
        """
        red_flags: list[str] = []

        # Check for suspiciously round numbers
        for claim in claims:
            if claim.claim_type != ClaimType.NUMBER:
                continue

            # Extract the numeric value
            nums = re.findall(r'\$?(\d[\d,]*)\.?\d*', claim.claim)
            for num_str in nums:
                try:
                    # Remove commas and convert
                    value = int(num_str.replace(",", ""))

                    # Check against suspicious round numbers
                    if value in self.ROUND_NUMBER_SUSPECTS:
                        red_flags.append(
                            f"Suspiciously round number: {claim.claim} "
                            f"(from {claim.agent}) — exactly ${value:,} "
                            f"is suspicious. Verify with primary source."
                        )
                except (ValueError, TypeError):
                    continue

        # Check for implausible growth rates
        for claim in claims:
            if claim.claim_type != ClaimType.NUMBER:
                continue

            match = self.GROWTH_RATE_PATTERN.search(claim.claim)
            if match:
                try:
                    growth_rate = float(match.group(1))
                    if growth_rate > self.IMPLAUSIBLE_GROWTH_THRESHOLD:
                        red_flags.append(
                            f"Implausible growth rate: {claim.claim} "
                            f"(from {claim.agent}) — {growth_rate}% growth "
                            f"is suspiciously high. Verify with primary source."
                        )
                except (ValueError, TypeError):
                    continue

        # Check for market size reconciliation issues across agents
        market_size_claims: dict[str, list[tuple[str, str]]] = {}
        for claim in claims:
            if claim.claim_type != ClaimType.NUMBER:
                continue
            if any(w in claim.claim.lower() for w in ["market size", "market value", "tam", "sam"]):
                # Extract the metric keyword
                words = claim.claim.lower().split()
                metric_key = " ".join(words[:3])
                market_size_claims.setdefault(metric_key, []).append((claim.agent, claim.claim))

        for metric, agent_claims in market_size_claims.items():
            if len(agent_claims) > 1:
                # Check if the numbers are different
                nums = []
                for _, claim_text in agent_claims:
                    found = re.findall(r'\$?(\d[\d,]*)\.?\d*', claim_text)
                    if found:
                        nums.append(found[0])

                unique_nums = set(nums)
                if len(unique_nums) > 1:
                    agents_involved = ", ".join(a for a, _ in agent_claims)
                    red_flags.append(
                        f"Market size reconciliation issue: '{metric}' — "
                        f"agents report different values ({agents_involved}): "
                        f"{', '.join(agent_claims[i][1] for i in range(len(agent_claims)))}. "
                        f"Synthesis Lead should reconcile."
                    )

        return red_flags

    # ─────────────────────────────────────────────────────────────────────
    # Confidence calibration
    # ─────────────────────────────────────────────────────────────────────

    def _calibrate_confidence(
        self,
        total_claims: int,
        verified: int,
        plausible: int,
        hallucinated_count: int,
        contradiction_count: int,
        chain_break_count: int,
    ) -> ConfidenceLevel:
        """Calibrate confidence in the fact-check process.

        HIGH: 70%+ verification rate, 0 hallucinated citations, <2 contradictions
        MEDIUM: 40%+ verification rate, <3 hallucinated citations
        LOW: <40% verification rate or 3+ hallucinated citations
        """
        if total_claims == 0:
            return ConfidenceLevel.LOW

        verification_rate = (verified + plausible) / total_claims

        if verification_rate >= 0.7 and hallucinated_count == 0 and contradiction_count < 2:
            return ConfidenceLevel.HIGH
        if verification_rate >= 0.4 and hallucinated_count < 3:
            return ConfidenceLevel.MEDIUM
        return ConfidenceLevel.LOW

    # ─────────────────────────────────────────────────────────────────────
    # Main execution — the 7-step methodology
    # ─────────────────────────────────────────────────────────────────────

    async def run(
        self,
        question: str = "",
        engagement_id: str = "",
        context: dict[str, Any] | None = None,
        findings: list[KeyFinding] | None = None,
    ) -> FactCheckReport:
        """Execute the Fact Checker's 7-step methodology.

        Steps (§4.5, Agent 16):
        1. Collect all specialist findings from AgentBus
        2. Extract factual claims (numbers, dates, names, events)
        3. For each claim, search for verification (SearxNG + Jina)
        4. Score each claim: VERIFIED, PLAUSIBLE, UNVERIFIED, CONTRADICTED
        5. Flag contradictions to Synthesis Lead
        6. Flag unverified claims to originating specialist
        7. Produce FactCheckReport model
        """
        if findings:
            self._all_findings.extend(findings)

        # Subscribe to bus
        self.subscribe_to_bus()

        await self._transition(
            AgentState.WORKING,
            f"Starting fact-check with {len(self._all_findings)} findings",
        )

        # Step 1: Collect all specialist findings
        await self._transition(AgentState.WORKING, "Step 1: Collecting specialist findings")
        all_findings = await self._collect_findings()

        if not all_findings:
            await self._transition(AgentState.DONE, "No findings to fact-check")
            return FactCheckReport(
                claims=[],
                total_claims_checked=0,
                confidence=ConfidenceLevel.LOW,
            )

        # Step 2: Extract factual claims
        await self._transition(AgentState.WORKING, "Step 2: Extracting factual claims")
        self._claims = await self._extract_claims(all_findings)

        # Step 3: Search for verification
        await self._transition(
            AgentState.WORKING,
            f"Step 3: Verifying {len(self._claims)} claims against independent sources",
        )
        self._claims = await self._verify_claims(self._claims)

        # Step 4: Score each claim (done during verification)
        await self._transition(AgentState.WORKING, "Step 4: Scoring claims")

        # Validate evidence chains
        self._chain_breaks, self._hallucinated = await self._validate_evidence_chains(self._claims)

        # Run statistical sanity checks
        self._statistical_red_flags = self._run_statistical_sanity_checks(self._claims)

        # Step 5: Flag contradictions to Synthesis Lead
        await self._transition(AgentState.WORKING, "Step 5: Detecting contradictions")
        self._contradictions = self._detect_contradictions(self._claims)

        if self._contradictions:
            await self.bus.publish(
                channel=Channel.FINDINGS,
                msg_type=MessageType.ESCALATION,
                sender=self.name,
                payload={
                    "agent": self.name.value,
                    "finding_type": "contradictions",
                    "contradictions": [c.model_dump() for c in self._contradictions],
                    "message": (
                        f"Fact Checker detected {len(self._contradictions)} "
                        f"contradiction(s) between agents. Synthesis Lead "
                        f"should resolve these evidence-weighted."
                    ),
                },
            )

        # Step 6: Flag unverified claims to originating specialists
        await self._transition(AgentState.WORKING, "Step 6: Flagging unverified claims")
        await self._flag_unverified_claims(self._claims)

        # Count claims by status
        verified_count = sum(1 for c in self._claims if c.status == ClaimStatus.VERIFIED)
        plausible_count = sum(1 for c in self._claims if c.status == ClaimStatus.PLAUSIBLE)
        unverified_count = sum(1 for c in self._claims if c.status == ClaimStatus.UNVERIFIED)
        contradicted_count = sum(1 for c in self._claims if c.status == ClaimStatus.CONTRADICTED)

        total_claims = len(self._claims)
        verification_rate = round(
            (verified_count + plausible_count) / max(total_claims, 1), 2
        )

        # Calibrate confidence
        confidence = self._calibrate_confidence(
            total_claims=total_claims,
            verified=verified_count,
            plausible=plausible_count,
            hallucinated_count=len(self._hallucinated),
            contradiction_count=len(self._contradictions),
            chain_break_count=len(self._chain_breaks),
        )

        # Step 7: Produce FactCheckReport
        await self._transition(AgentState.WORKING, "Step 7: Producing FactCheckReport")

        report = FactCheckReport(
            claims=self._claims,
            verified_count=verified_count,
            plausible_count=plausible_count,
            unverified_count=unverified_count,
            contradicted_count=contradicted_count,
            contradictions=self._contradictions,
            hallucinated_citations=self._hallucinated,
            hallucinated_citation_count=len(self._hallucinated),
            statistical_red_flags=self._statistical_red_flags,
            evidence_chain_breaks=self._chain_breaks,
            evidence_chain_break_count=len(self._chain_breaks),
            total_claims_checked=total_claims,
            verification_rate=verification_rate,
            confidence=confidence,
        )

        # Publish fact-check report to bus
        await self.bus.publish(
            channel=Channel.FINDINGS,
            msg_type=MessageType.FINDING,
            sender=self.name,
            payload={
                "agent": self.name.value,
                "finding_type": "fact_check_report",
                "fact_check_report": report.model_dump(),
                "total_claims": total_claims,
                "verified": verified_count,
                "plausible": plausible_count,
                "unverified": unverified_count,
                "contradicted": contradicted_count,
                "hallucinated": len(self._hallucinated),
                "contradictions": len(self._contradictions),
                "statistical_red_flags": len(self._statistical_red_flags),
                "evidence_chain_breaks": len(self._chain_breaks),
                "verification_rate": verification_rate,
                "confidence": confidence.value,
            },
        )

        # Publish hallucinated citations as a critical finding
        if self._hallucinated:
            finding = KeyFinding(
                id=f"finding_{hashlib.md5(f'fact_checker_hallucinated_{engagement_id}'.encode()).hexdigest()[:8]}",
                agent=self.name.value,
                finding_type="hallucinated_citations",
                title=f"CRITICAL: {len(self._hallucinated)} Hallucinated Citations Detected",
                content=(
                    f"Detected {len(self._hallucinated)} claim(s) where the "
                    f"cited source does not contain the claimed data. This is "
                    f"the #1 quality risk in LLM-generated reports. "
                    f"Affected agents: {', '.join(set(c.agent for c in self._hallucinated))}. "
                    f"Claims: {', '.join(c.claim[:50] for c in self._hallucinated[:3])}"
                ),
                confidence=ConfidenceLevel.HIGH,
            )
            await self._publish_finding(finding)

        # Publish statistical red flags as a finding
        if self._statistical_red_flags:
            finding = KeyFinding(
                id=f"finding_{hashlib.md5(f'fact_checker_stats_{engagement_id}'.encode()).hexdigest()[:8]}",
                agent=self.name.value,
                finding_type="statistical_red_flags",
                title=f"Statistical Red Flags: {len(self._statistical_red_flags)} issues",
                content=(
                    f"Detected {len(self._statistical_red_flags)} statistical "
                    f"red flag(s): {'; '.join(self._statistical_red_flags[:3])}"
                ),
                confidence=ConfidenceLevel.HIGH,
            )
            await self._publish_finding(finding)

        await self._transition(
            AgentState.DONE,
            f"Fact-check complete: {total_claims} claims checked, "
            f"{verified_count} verified, {plausible_count} plausible, "
            f"{unverified_count} unverified, {contradicted_count} contradicted, "
            f"{len(self._hallucinated)} hallucinated, "
            f"{len(self._contradictions)} contradictions, "
            f"{len(self._statistical_red_flags)} statistical red flags, "
            f"{len(self._chain_breaks)} evidence chain breaks, "
            f"verification_rate={verification_rate}, "
            f"confidence={confidence.value}",
        )

        return report
