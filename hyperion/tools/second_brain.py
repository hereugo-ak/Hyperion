"""
HYPERION Second Brain Client — Obsidian vault read/write/search.

The Obsidian vault is HYPERION's institutional memory. It makes the
system smarter over time — each engagement's findings are saved and
can be retrieved by future engagements.

This is NOT a generic "read markdown files" wrapper. It:
- Fuses keyword matching (threshold: 0.15) with vector-embedding retrieval
  (5.4: hyperion/tools/vector_brain.py) so paraphrased prior research is
  recalled, not just literal-term matches
- Embeddings index the vault in SQLite (sqlite-vec ANN when the extension is
  available; deterministic exact cosine scan otherwise)
- Reads and writes Obsidian-compatible markdown files
- Organizes by directory structure: engagements/, markets/, competitors/,
  frameworks/, sources/
- Cross-engagement knowledge linking (prior research feeds new engagements)
- Saves findings back to vault at end of each engagement
- Supports full-text search across all vault notes
- Returns structured results with relevance scores and source paths

Architecture reference: §5.1 — "Obsidian vault — prior research, notes,
keyword retrieval. Relevance threshold: 0.15. Makes the system smarter
over time."

§5.5 — "The Obsidian vault is HYPERION's institutional memory. It makes
the system smarter over time — each engagement's findings are saved and
can be retrieved by future engagements."

Vault structure (§5.5):
  vault/
  ├── engagements/          # One note per engagement
  ├── markets/              # Market research accumulated over time
  ├── competitors/          # Competitor profiles accumulated over time
  ├── frameworks/           # Analytical framework templates
  └── sources/              # Source library with credibility scores

Retrieval (§5.5, upgraded 5.4):
- Keyword matching with relevance scoring (threshold: 0.15), fused with
  embedding similarity from the vector index — final score is
  max(keyword, semantic) so semantic recall can only help a note, never
  hide an exact-match hit
- The Research Librarian queries the vault at the start of each engagement
- At the end of each engagement, findings are saved back to the vault and
  indexed into the embedding store

Cross-engagement knowledge linking (§5.5):
"If a new engagement touches a topic researched in a prior engagement
(e.g., 'Indian SaaS market' was researched 3 months ago), the Librarian
retrieves the prior research and provides it to the specialists."

Used by: Research Librarian, all agents (read) (§5.1)
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from hyperion.tools.vector_brain import VectorStore, backend_name, embed

logger = logging.getLogger(__name__)


@dataclass
class VaultNote:
    """A single note in the Obsidian vault."""

    path: str
    title: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    frontmatter: dict[str, Any] = field(default_factory=dict)
    category: str = ""  # engagements, markets, competitors, frameworks, sources
    created_at: str = ""
    modified_at: str = ""
    word_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "title": self.title,
            "content": self.content,
            "tags": self.tags,
            "frontmatter": self.frontmatter,
            "category": self.category,
            "created_at": self.created_at,
            "modified_at": self.modified_at,
            "word_count": self.word_count,
        }


@dataclass
class VaultSearchResult:
    """Result of searching the vault."""

    query: str
    notes: list[tuple[VaultNote, float]] = field(default_factory=list)  # (note, relevance_score)
    total: int = 0
    above_threshold: int = 0
    threshold: float = 0.15

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "notes": [
                {"note": note.to_dict(), "relevance": score}
                for note, score in self.notes
            ],
            "total": self.total,
            "above_threshold": self.above_threshold,
            "threshold": self.threshold,
        }


@dataclass
class VaultSaveResult:
    """Result of saving a note to the vault."""

    path: str
    success: bool = False
    error: str = ""
    overwritten: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "success": self.success,
            "error": self.error,
            "overwritten": self.overwritten,
        }


class SecondBrainClient:
    """Obsidian vault client — institutional memory for HYPERION.

    Reads and writes Obsidian-compatible markdown files with keyword
    search and relevance scoring. No embeddings — lightweight and fast.
    (§5.1, §5.5)

    Usage:
        client = SecondBrainClient(settings=settings)

        # Search for prior research
        results = await client.search("Indian SaaS market size")
        for note, score in results.notes:
            if score >= 0.15:
                print(f"{note.title} (relevance: {score:.2f})")

        # Save engagement findings
        await client.save_engagement(
            engagement_id="2026-07-19-tier2-saas-india",
            title="Tier-2 Indian SaaS Market Entry",
            content="## Key Findings\n...",
            tags=["market-entry", "saas", "india"],
        )
    """

    RELEVANCE_THRESHOLD = 0.15
    CATEGORIES = ["engagements", "markets", "competitors", "frameworks", "sources"]

    def __init__(self, settings: Any | None = None) -> None:
        self.settings = settings
        # Anchored at the project root, not the CWD.
        #
        # `Path("./vault")` is relative to wherever the shell was launched, so a
        # vault written during one session was invisible to the next if the user
        # started HYPERION from a different directory — and "prior engagements"
        # was therefore always 0 while the notes sat in a stray ./vault folder.
        from hyperion.infra.paths import resolve_path

        self._vault_path = resolve_path(
            getattr(settings, "vault_path", None) if settings else None,
            default="vault",
        )
        self._cache: dict[str, tuple[float, VaultSearchResult]] = {}
        self._vector_store: VectorStore | None = None
        self._ensure_vault_structure()

    def _get_vector_store(self) -> VectorStore:
        """Lazily open the embedding index (5.4).

        Deferred so a read-only consumer that never searches pays no SQLite
        cost, and so a vault that is never written stays index-free.
        """
        if self._vector_store is None:
            db_path = self._vault_path / ".embeddings.db"
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._vector_store = VectorStore(db_path)
            logger.debug(
                "second_brain: embedding index opened (backend=%s, ann=%s)",
                backend_name(),
                self._vector_store.uses_ann,
            )
        return self._vector_store

    def _index_note(self, note: VaultNote) -> None:
        """Embed and upsert a single note into the vector index."""
        text = f"{note.title}\n{' '.join(note.tags)}\n{note.content}"
        self._get_vector_store().upsert(note.path, embed(text))

    def _semantic_scores(self, query: str, limit: int = 50) -> dict[str, float]:
        """Embedding similarity per vault-relative path; {} when index empty.

        A failure to build/query the index degrades to keyword-only search
        rather than failing the retrieval — the keyword path alone is the
        pre-5.4 behaviour, which is a safe floor, not an outage.
        """
        try:
            store = self._get_vector_store()
            if store.count() == 0:
                return {}
            hits = store.search(embed(query), limit=limit)
            return {h.key: h.score for h in hits}
        except Exception as exc:  # noqa: BLE001 - degrade to keyword-only floor
            logger.warning("second_brain: semantic search degraded (%s)", exc)
            return {}

    def _ensure_vault_structure(self) -> None:
        """Create the vault directory structure if it doesn't exist."""
        for category in self.CATEGORIES:
            (self._vault_path / category).mkdir(parents=True, exist_ok=True)

    def _parse_frontmatter(self, content: str) -> tuple[dict[str, Any], str]:
        """Parse YAML frontmatter from a markdown note.

        Returns (frontmatter_dict, body_content).
        """
        frontmatter: dict[str, Any] = {}
        body = content

        if content.startswith("---"):
            end = content.find("---", 3)
            if end != -1:
                fm_text = content[3:end].strip()
                body = content[end + 3:].strip()

                # Simple YAML parsing (key: value)
                for line in fm_text.split("\n"):
                    if ":" in line:
                        key, raw_value = line.split(":", 1)
                        key = key.strip()
                        raw_value = raw_value.strip()
                        value: Any = raw_value
                        # Handle lists [item1, item2]
                        if raw_value.startswith("[") and raw_value.endswith("]"):
                            value = [
                                item.strip().strip('"\'')
                                for item in raw_value[1:-1].split(",")
                                if item.strip()
                            ]
                        # Handle quoted strings
                        elif (
                            raw_value.startswith('"') and raw_value.endswith('"')
                        ) or (
                            raw_value.startswith("'") and raw_value.endswith("'")
                        ):
                            value = raw_value[1:-1]
                        frontmatter[key] = value

        return frontmatter, body

    def _extract_tags(self, content: str) -> list[str]:
        """Extract #tags from markdown content."""
        tags: list[str] = []
        # Match #tag patterns (not in code blocks)
        for match in re.finditer(r"(?:^|\s)#([a-zA-Z0-9_-]+)", content):
            tag = match.group(1)
            if tag not in tags and len(tag) > 1:
                tags.append(tag)
        return tags

    def _read_note(self, file_path: Path) -> VaultNote | None:
        """Read a single note from the vault."""
        try:
            content = file_path.read_text(encoding="utf-8")
            frontmatter, body = self._parse_frontmatter(content)

            # Extract title from frontmatter or first heading
            title = frontmatter.get("title", "")
            if not title:
                for line in body.split("\n"):
                    if line.strip().startswith("# "):
                        title = line.strip()[2:]
                        break
            if not title:
                title = file_path.stem

            tags = self._extract_tags(body)
            if "tags" in frontmatter:
                fm_tags = frontmatter["tags"]
                if isinstance(fm_tags, list):
                    tags.extend(fm_tags)

            stat = file_path.stat()
            category = file_path.parent.name

            return VaultNote(
                path=str(file_path.relative_to(self._vault_path)),
                title=title,
                content=body,
                tags=list(set(tags)),
                frontmatter=frontmatter,
                category=category,
                created_at=datetime.fromtimestamp(stat.st_ctime).isoformat(),
                modified_at=datetime.fromtimestamp(stat.st_mtime).isoformat(),
                word_count=len(body.split()),
            )
        except (OSError, UnicodeDecodeError):
            return None

    def _score_relevance(self, query: str, note: VaultNote) -> float:
        """Score the relevance of a note to a query.

        Uses keyword matching with TF-IDF-like scoring.
        No embeddings — lightweight and fast. (§5.5)

        Scoring factors:
        - Title match: 3x weight (title is most important)
        - Tag match: 2x weight (tags are curated)
        - Content match: 1x weight
        - Frontmatter keywords: 2x weight
        """
        query_terms = [t.lower() for t in re.split(r"\s+", query.strip()) if len(t) > 2]
        if not query_terms:
            return 0.0

        title_lower = note.title.lower()
        content_lower = note.content.lower()
        tags_lower = [t.lower() for t in note.tags]

        score = 0.0
        total_terms = len(query_terms)

        for term in query_terms:
            # Title match (3x)
            if term in title_lower:
                score += 3.0 / total_terms

            # Tag match (2x)
            if any(term in tag for tag in tags_lower):
                score += 2.0 / total_terms

            # Content match (1x) — count occurrences
            occurrences = content_lower.count(term)
            if occurrences > 0:
                # Diminishing returns for repeated occurrences
                content_score = min(occurrences, 5) / 5.0
                score += content_score / total_terms

            # Frontmatter keywords (2x)
            fm_keywords = str(note.frontmatter.get("keywords", "")).lower()
            if term in fm_keywords:
                score += 2.0 / total_terms

        # Normalize to 0-1 range (max possible score per term is 3+2+1+2=8)
        return min(score / 8.0, 1.0)

    async def search(
        self,
        query: str,
        categories: list[str] | None = None,
        limit: int = 20,
        threshold: float = RELEVANCE_THRESHOLD,
    ) -> VaultSearchResult:
        """Search the vault for notes matching the query.

        Uses keyword matching with relevance scoring. No embeddings.
        (§5.5)

        Args:
            query: Search query string
            categories: Limit search to these categories. None = all.
            limit: Maximum number of results
            threshold: Minimum relevance score (default 0.15)

        Returns:
            VaultSearchResult with notes sorted by relevance.
        """
        if categories is None:
            categories = self.CATEGORIES

        all_notes: list[tuple[VaultNote, float]] = []

        for category in categories:
            cat_path = self._vault_path / category
            if not cat_path.exists():
                continue

            for md_file in cat_path.rglob("*.md"):
                note = self._read_note(md_file)
                if note is None:
                    continue

                score = self._score_relevance(query, note)
                if score > 0:
                    all_notes.append((note, score))

        # 5.4: fuse keyword score with embedding similarity. max() (not a
        # weighted sum) so semantic recall can only lift a note above the
        # threshold, never push an exact keyword match below it — the keyword
        # path stays authoritative for source titles, URLs and model numbers.
        semantic = self._semantic_scores(query)
        if semantic:
            all_notes = [
                (note, max(kw, semantic.get(note.path, 0.0)))
                for note, kw in all_notes
            ]
            # Re-add notes the keyword pass scored 0 but the vector index
            # recalls — the paraphrase case the keyword pass is blind to.
            known = {note.path for note, _ in all_notes}
            for category in categories:
                cat_path = self._vault_path / category
                if not cat_path.exists():
                    continue
                for md_file in cat_path.rglob("*.md"):
                    rel = str(md_file.relative_to(self._vault_path))
                    if rel in known or semantic.get(rel, 0.0) < threshold:
                        continue
                    note = self._read_note(md_file)
                    if note is not None:
                        all_notes.append((note, semantic[rel]))

        # Sort by relevance (descending)
        all_notes.sort(key=lambda x: x[1], reverse=True)

        # Filter by threshold
        above = [(n, s) for n, s in all_notes if s >= threshold]
        result_notes = above[:limit] if above else all_notes[:limit]

        return VaultSearchResult(
            query=query,
            notes=result_notes,
            total=len(all_notes),
            above_threshold=len(above),
            threshold=threshold,
        )

    async def get_note(self, note_path: str) -> VaultNote | None:
        """Read a specific note from the vault.

        Args:
            note_path: Relative path within the vault (e.g., "markets/saas-india.md")
        """
        full_path = self._vault_path / note_path
        if not full_path.exists():
            return None
        return self._read_note(full_path)

    async def save_note(
        self,
        category: str,
        filename: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        frontmatter: dict[str, Any] | None = None,
        overwrite: bool = False,
    ) -> VaultSaveResult:
        """Save a note to the vault.

        Args:
            category: Vault category (engagements, markets, competitors, etc.)
            filename: Filename without extension (e.g., "saas-india")
            title: Note title
            content: Markdown content (body only, no frontmatter)
            tags: List of tags
            frontmatter: Additional frontmatter fields
            overwrite: Whether to overwrite if file exists

        Returns:
            VaultSaveResult with save status.
        """
        if category not in self.CATEGORIES:
            return VaultSaveResult(
                path="",
                success=False,
                error=f"Invalid category: {category}. Must be one of {self.CATEGORIES}",
            )

        cat_path = self._vault_path / category
        cat_path.mkdir(parents=True, exist_ok=True)

        file_path = cat_path / f"{filename}.md"

        if file_path.exists() and not overwrite:
            return VaultSaveResult(
                path=str(file_path.relative_to(self._vault_path)),
                success=False,
                error="File already exists. Set overwrite=True to replace.",
            )

        # Build frontmatter
        fm: dict[str, Any] = {
            "title": title,
            "created": datetime.now().isoformat(),
            "tags": tags or [],
        }
        if frontmatter:
            fm.update(frontmatter)

        # Build YAML frontmatter
        fm_lines = ["---"]
        for key, value in fm.items():
            if isinstance(value, list):
                fm_lines.append(f"{key}: [{', '.join(str(v) for v in value)}]")
            else:
                fm_lines.append(f'{key}: "{value}"')
        fm_lines.append("---")

        # Build full note
        full_content = "\n".join(fm_lines) + "\n\n" + content

        try:
            file_path.write_text(full_content, encoding="utf-8")
            # 5.4: keep the embedding index in step with the vault. Indexing
            # failure must not fail the save — the note is on disk and the
            # keyword path still retrieves it; the index self-heals on the
            # next successful write.
            note = self._read_note(file_path)
            if note is not None:
                try:
                    self._index_note(note)
                except Exception as exc:  # noqa: BLE001 - index lag degrades to keyword-only
                    logger.warning("second_brain: note saved but not indexed (%s)", exc)
            return VaultSaveResult(
                path=str(file_path.relative_to(self._vault_path)),
                success=True,
                overwritten=file_path.exists(),
            )
        except OSError as e:
            return VaultSaveResult(
                path=str(file_path),
                success=False,
                error=str(e),
            )

    async def save_engagement(
        self,
        engagement_id: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
        findings_count: int = 0,
        quality_score: float = 0.0,
    ) -> VaultSaveResult:
        """Save engagement findings to the vault.

        Called at the end of each engagement to persist findings for
        future retrieval. (§5.5)
        """
        return await self.save_note(
            category="engagements",
            filename=engagement_id,
            title=title,
            content=content,
            tags=tags or [],
            frontmatter={
                "engagement_id": engagement_id,
                "findings_count": findings_count,
                "quality_score": quality_score,
                "completed": datetime.now().isoformat(),
            },
            overwrite=True,
        )

    async def save_market_research(
        self,
        market_name: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
    ) -> VaultSaveResult:
        """Save market research to the vault.

        Market research accumulates over time — each engagement that
        touches a market updates its research note. (§5.5)
        """
        filename = market_name.lower().replace(" ", "-").replace("/", "-")
        return await self.save_note(
            category="markets",
            filename=filename,
            title=title,
            content=content,
            tags=tags or [],
            frontmatter={"market": market_name, "updated": datetime.now().isoformat()},
            overwrite=True,
        )

    async def save_competitor_profile(
        self,
        competitor_name: str,
        title: str,
        content: str,
        tags: list[str] | None = None,
    ) -> VaultSaveResult:
        """Save a competitor profile to the vault.

        Competitor profiles accumulate over time — each engagement that
        analyzes a competitor updates its profile. (§5.5)
        """
        filename = competitor_name.lower().replace(" ", "-").replace("/", "-")
        return await self.save_note(
            category="competitors",
            filename=filename,
            title=title,
            content=content,
            tags=tags or [],
            frontmatter={"competitor": competitor_name, "updated": datetime.now().isoformat()},
            overwrite=True,
        )

    async def list_notes(self, category: str | None = None) -> list[VaultNote]:
        """List all notes in the vault, optionally filtered by category."""
        notes: list[VaultNote] = []
        categories = [category] if category else self.CATEGORIES

        for cat in categories:
            cat_path = self._vault_path / cat
            if not cat_path.exists():
                continue
            for md_file in cat_path.rglob("*.md"):
                note = self._read_note(md_file)
                if note:
                    notes.append(note)

        return notes

    async def get_related(
        self,
        note_path: str,
        limit: int = 5,
    ) -> list[tuple[VaultNote, float]]:
        """Get notes related to a given note.

        Uses the note's tags and title to find related notes.
        """
        note = await self.get_note(note_path)
        if not note:
            return []

        # Build a query from title + tags
        query = note.title + " " + " ".join(note.tags)
        results = await self.search(query, limit=limit + 1)

        # Exclude the source note
        return [(n, s) for n, s in results.notes if n.path != note_path][:limit]

    async def close(self) -> None:
        """Close any open resources."""
        pass

    async def __aenter__(self) -> SecondBrainClient:
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.close()
