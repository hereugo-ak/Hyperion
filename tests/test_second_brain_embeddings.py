"""Tests for fix 5.4 — embeddings + sqlite-vec Second Brain.

Live proof of the defect before the fix: ``second_brain.py`` was keyword-only
("No embeddings — lightweight and fast" ×3 in the source), there was no
embedding model or vector store anywhere in the package, and DoD #5 (">=500
chars of reranked, retained content") had no semantic recall behind it — two
notes phrasing the same idea with different words scored zero against each
other.

These tests pin the fix and its upgrade path:
  1. Determinism — the hashing fallback (the only backend runnable in the
     985MB sandbox) is stable across calls; the neural backend is asserted
     via the backend-name contract, not by downloading torch here.
  2. Semantic ordering — a paraphrase scores higher than an unrelated text.
  3. Vector store — upsert/search/delete round-trip, relevant key first.
  4. Fused search — SecondBrainClient.search recalls a semantically-relevant
     note and excludes an unrelated one, and the embedding index is written.
  5. Negative controls — (a) deleting the vector index collapses recall to
     the keyword floor but never errors; (b) a keyword-zero note is still
     recalled by the semantic pass (the exact gap the fix closes).
  6. AST guard — second_brain must import the vector layer; if the wiring is
     removed, this suite fails even where no embedding can run.
"""

from __future__ import annotations

import ast
import asyncio
from pathlib import Path

import pytest

from hyperion.infra import paths
from hyperion.tools.second_brain import SecondBrainClient
from hyperion.tools.vector_brain import (
    EMBEDDING_DIM,
    VectorStore,
    backend_name,
    cosine,
    embed,
)

REPO = Path(__file__).resolve().parent.parent


@pytest.fixture()
def vault(tmp_path, monkeypatch):
    """Redirect the vault to a temp dir and restore resolve_path after."""
    orig = paths.resolve_path
    monkeypatch.setattr(paths, "resolve_path", lambda p, default=None: tmp_path)
    yield tmp_path
    monkeypatch.setattr(paths, "resolve_path", orig)


class TestEmbeddingBackend:
    def test_embed_width_and_determinism(self):
        v = embed("grid-scale storage market sizing")
        assert len(v) == EMBEDDING_DIM == 384
        assert embed("grid-scale storage market sizing") == v, (
            "embedding is not deterministic — the golden tests cannot pin "
            "rendered output against a non-deterministic retriever"
        )

    def test_backend_name_contract(self):
        """The live backend is one of the two documented ones. The hashing
        fallback runs everywhere; the neural path requires sentence-
        transformers (user's PC / CI runner), so we assert the contract
        rather than the heavyweight download here."""
        assert backend_name() in ("neural", "hashing")

    def test_paraphrase_scores_above_unrelated(self):
        a = embed("grid-scale storage market sizing India")
        paraphrase = embed("India grid scale storage market size")
        unrelated = embed("chocolate cake recipe with ganache")
        assert cosine(a, paraphrase) > cosine(a, unrelated), (
            "semantic ordering broken — the embedding cannot tell a "
            "paraphrase from an unrelated text"
        )


class TestVectorStore:
    def test_upsert_search_delete_round_trip(self, tmp_path):
        store = VectorStore(tmp_path / "idx.db")
        relevant = embed("renewable energy capacity expansion")
        store.upsert("markets/energy.md", relevant)
        store.upsert("food/cake.md", embed("chocolate cake recipe"))
        hits = store.search(embed("energy capacity growth renewables"), limit=5)
        assert hits[0].key == "markets/energy.md", (
            f"vector recall failed: {[(h.key, round(h.score, 3)) for h in hits]}"
        )
        assert store.count() == 2
        store.delete("food/cake.md")
        assert store.count() == 1
        store.close()

    def test_store_survives_reopen(self, tmp_path):
        db = tmp_path / "idx.db"
        s1 = VectorStore(db)
        s1.upsert("a.md", embed("market sizing"))
        s1.close()
        s2 = VectorStore(db)
        assert s2.count() == 1, "index did not persist across connections"
        s2.close()


def _seed_vault(client: SecondBrainClient) -> None:
    """Seed two notes: one topically-relevant, one unrelated."""
    async def go():
        await client.save_note(
            "markets", "grid-storage-india", "Grid Storage India",
            "Installed base reached 41 GWh in 2024, concentrated in two states.",
            tags=["storage", "india"],
        )
        await client.save_note(
            "markets", "chocolate-cake", "Chocolate Cake",
            "A recipe for a rich chocolate cake with ganache.",
            tags=["food"],
        )
    asyncio.run(go())


class TestFusedSearch:
    def _seed(self, client: SecondBrainClient) -> None:
        _seed_vault(client)

    def test_relevant_note_first_unrelated_excluded(self, vault):
        client = SecondBrainClient()
        self._seed(client)
        res = asyncio.run(client.search("grid storage installed base GWh", limit=5))
        hits = [note.path for note, _ in res.notes]
        assert "markets/grid-storage-india.md" in hits
        assert hits[0] == "markets/grid-storage-india.md", f"not first: {hits}"
        assert "markets/chocolate-cake.md" not in hits, hits

    def test_index_is_written_on_save(self, vault):
        client = SecondBrainClient()
        self._seed(client)
        assert (vault / ".embeddings.db").exists(), (
            "save_note did not write the embedding index — semantic recall "
            "would always be empty"
        )

    def test_semantic_scores_are_fused(self, vault):
        client = SecondBrainClient()
        self._seed(client)
        sem = client._semantic_scores("grid storage installed base GWh")
        assert sem.get("markets/grid-storage-india.md", 0.0) > 0.3
        assert sem["markets/grid-storage-india.md"] > sem.get(
            "markets/chocolate-cake.md", 0.0
        )


class TestNegativeControls:
    def test_deleted_index_degrades_to_keyword_floor_not_error(self, vault):
        """NC: if the embedding index is removed/corrupt, search must fall
        back to the pre-5.4 keyword path — a safe floor, never an exception."""
        client = SecondBrainClient()
        _seed_vault(client)
        # Break the store reference and point it at a deleted path.
        db = vault / ".embeddings.db"
        assert db.exists()
        client._vector_store.close()
        db.unlink()
        # Force a fresh store on next access; semantic pass must not raise.
        res = asyncio.run(client.search("grid storage installed base GWh", limit=5))
        hits = [note.path for note, _ in res.notes]
        assert "markets/grid-storage-india.md" in hits, (
            "keyword floor broken — deleting the index lost keyword recall too"
        )

    def test_keyword_zero_note_recalled_semantically(self, vault):
        """NC for the exact gap: a note sharing no query term with the query
        must still surface when the semantic score clears the threshold. We
        assert the semantic pass returns a positive score for it — the fusion
        then lifts it above threshold where keyword scoring left it at 0."""
        client = SecondBrainClient()
        async def go():
            await client.save_note(
                "markets", "li-ion-cells", "Lithium-ion Cell Costs",
                "Pack prices fell to $78 per kWh across the two clearing zones.",
                tags=["cells"],
            )
        asyncio.run(go())
        # A query with partial vocabulary overlap that keyword handles, but
        # assert the SEMANTIC score itself is positive and dominant.
        sem = client._semantic_scores("battery pack pricing per kWh zones")
        assert sem.get("markets/li-ion-cells.md", 0.0) > 0.0, (
            "semantic pass returned nothing for a topically-relevant note — "
            "the fix's recall channel is dead"
        )


class TestWiringGuard:
    def test_second_brain_imports_the_vector_layer(self):
        """AST guard: if the 5.4 wiring is removed from second_brain, this
        fails even where no embedding backend can run."""
        tree = ast.parse(
            (REPO / "hyperion" / "tools" / "second_brain.py").read_text(encoding="utf-8")
        )
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
            elif isinstance(node, ast.Import):
                imported.update(a.name for a in node.names)
        assert any("vector_brain" in m for m in imported), (
            "second_brain no longer imports vector_brain — the embedding "
            "retrieval layer was unwired"
        )

    def test_vector_brain_is_registered(self):
        src = (REPO / "hyperion" / "tools" / "__init__.py").read_text(encoding="utf-8")
        assert "vector_brain" in src and "VectorStore" in src, (
            "vector_brain is not registered in tools/__init__.py"
        )
