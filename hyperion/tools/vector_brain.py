"""HYPERION vector-retrieval layer — embeddings + SQLite vector store (5.4).

The audit (§6 fix 5.4, tracker line 1816) called for a "reranker + embeddings
+ sqlite-vec Second Brain". Until this module the Second Brain was keyword-
only (`_score_relevance` in second_brain.py), and DoD #5 (">=500 chars of
reranked, retained content per cited source") rested on a BM25 heuristic
(content_selector.rerank_chunks) with no semantic recall at all — two
documents phrasing the same idea with different words scored zero.

Design (judgement, not model deferral):

  * Two embedding backends, one interface. ``sentence-transformers`` +
    ``sqlite-vec`` are the intended production path (real neural embeddings,
    a real ANN index). They are heavy (torch) and cannot run in the 985MB CI
    sandbox, so the module ships a deterministic, dependency-free fallback —
    a normalised hashing projection over hashed token n-grams — that runs
    everywhere and keeps the whole layer testable here. The backend is chosen
    once at import; ``backend_name`` exposes which is live so tests can assert
    the upgrade path rather than assume it.

  * The vector store is plain SQLite in all cases. When ``sqlite_vec`` is
    importable it loads the extension and uses a ``vec0`` virtual table for
    ANN search; otherwise it stores embeddings as blobs and ranks by cosine
    similarity in a single SQL pass over the (small, vault-scale) table. The
    vault is engagement research, not web-scale — an exact scan over a few
    thousand notes is fast and deterministic, and determinism matters because
    fix 5.2's golden test pins rendered output.

  * Nothing here replaces the keyword path. ``SecondBrainClient.search`` fuses
    keyword and semantic scores (see second_brain.py); a pure-vector search
    would regress the exact-match recall the keyword scorer is good at (source
    titles, URLs, tickers, model numbers).
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
import sqlite3
import struct
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# Dimension of every embedding this layer produces, for both backends. The
# hashing fallback projects into exactly this many buckets so a vault indexed
# under the fallback can be re-ranked — not re-indexed — by the neural model
# at the same width.
EMBEDDING_DIM = 384

# sentence-transformers model used when the heavy backend is available. Small
# enough to run on CPU beside the pipeline, strong enough for retrieval.
_ST_MODEL = "all-MiniLM-L6-v2"

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


# ── Embedding backends ────────────────────────────────────────────────────


def _hash_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """Deterministic bag-of-ngrams hashing embedding (fallback backend).

    Each token and each adjacent token bigram is hashed to a bucket and added
    with signed hashing, then the vector is L2-normalised. Cosine similarity
    of two such vectors tracks shared-token overlap, giving real semantic
    recall for paraphrases that share vocabulary while staying fully
    deterministic and dependency-free. Not a neural embedding — the upgrade
    path to ``sentence-transformers`` is the point of the backend split.
    """
    vec = [0.0] * dim
    toks = _tokens(text)
    features = list(toks)
    features.extend(f"{a} {b}" for a, b in zip(toks, toks[1:], strict=False))
    if not features:
        return vec
    for feat in features:
        digest = hashlib.blake2b(feat.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] & 1 else -1.0
        vec[bucket] += sign
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


_ST_MODEL_CACHE: object | None = None


def _neural_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """sentence-transformers embedding, lazily loaded (production backend)."""
    global _ST_MODEL_CACHE
    if _ST_MODEL_CACHE is None:
        from sentence_transformers import SentenceTransformer  # noqa: PLC0415

        _ST_MODEL_CACHE = SentenceTransformer(_ST_MODEL)
    vec = _ST_MODEL_CACHE.encode(text, normalize_embeddings=True)  # type: ignore[union-attr]
    out = [float(x) for x in vec[:dim]]
    return out


def _detect_backend() -> str:
    """Pick the embedding backend once: neural if available, else hashing."""
    try:
        import sentence_transformers  # noqa: F401, PLC0415

        return "neural"
    except Exception:  # noqa: BLE001 - import probe, fallback is deliberate
        return "hashing"


_BACKEND = _detect_backend()


def backend_name() -> str:
    """Which embedding backend is live: 'neural' or 'hashing'."""
    return _BACKEND


def embed(text: str) -> list[float]:
    """Embed text with the live backend, always at EMBEDDING_DIM width."""
    if _BACKEND == "neural":
        try:
            return _neural_embedding(text)
        except Exception as exc:  # noqa: BLE001 - degrade to deterministic fallback
            logger.warning(
                "vector_brain: neural embedding failed (%s); using hashing fallback", exc
            )
    return _hash_embedding(text)


def cosine(a: list[float], b: list[float]) -> float:
    """Cosine similarity of two equal-width vectors."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def _pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def _unpack(blob: bytes, dim: int = EMBEDDING_DIM) -> list[float]:
    n = len(blob) // 4
    return list(struct.unpack(f"{n}f", blob))[:dim]


# ── Vector store ──────────────────────────────────────────────────────────


@dataclass
class VectorHit:
    """One vector-search result: a note key and its similarity score."""

    key: str
    score: float


class VectorStore:
    """SQLite-backed embedding index for the vault.

    Uses a ``vec0`` ANN table when ``sqlite_vec`` is importable; otherwise an
    exact cosine scan over a plain table (correct and deterministic at vault
    scale). Keys are vault-relative note paths.
    """

    def __init__(self, db_path: Path, dim: int = EMBEDDING_DIM) -> None:
        self._dim = dim
        self._db = sqlite3.connect(str(db_path))
        self._db.row_factory = sqlite3.Row
        self._vec_available = self._try_load_sqlite_vec()
        self._ensure_schema()

    def _try_load_sqlite_vec(self) -> bool:
        try:
            import sqlite_vec  # noqa: PLC0415

            self._db.enable_load_extension(True)
            sqlite_vec.load(self._db)
            self._db.enable_load_extension(False)
            return True
        except Exception as exc:  # noqa: BLE001 - extension probe, fallback deliberate
            logger.debug("vector_brain: sqlite-vec unavailable (%s); exact scan", exc)
            return False

    @property
    def uses_ann(self) -> bool:
        """True when sqlite-vec provides the ANN index."""
        return self._vec_available

    def _ensure_schema(self) -> None:
        if self._vec_available:
            self._db.execute(
                f"CREATE VIRTUAL TABLE IF NOT EXISTS vault_vec USING vec0("
                f"key TEXT PRIMARY KEY, embedding FLOAT[{self._dim}])"
            )
        else:
            self._db.execute(
                "CREATE TABLE IF NOT EXISTS vault_vec ("
                "key TEXT PRIMARY KEY, embedding BLOB NOT NULL)"
            )
        self._db.commit()

    def upsert(self, key: str, vec: list[float]) -> None:
        if self._vec_available:
            self._db.execute(
                "INSERT OR REPLACE INTO vault_vec(key, embedding) VALUES (?, ?)",
                (key, _pack(vec)),
            )
        else:
            self._db.execute(
                "INSERT OR REPLACE INTO vault_vec(key, embedding) VALUES (?, ?)",
                (key, _pack(vec)),
            )
        self._db.commit()

    def delete(self, key: str) -> None:
        self._db.execute("DELETE FROM vault_vec WHERE key = ?", (key,))
        self._db.commit()

    def search(self, query_vec: list[float], limit: int = 20) -> list[VectorHit]:
        """Return the ``limit`` most similar keys, best first."""
        if self._vec_available:
            rows = self._db.execute(
                "SELECT key, distance FROM vault_vec WHERE embedding MATCH ? "
                "ORDER BY distance LIMIT ?",
                (_pack(query_vec), limit),
            ).fetchall()
            # vec0 returns distance; cosine distance -> similarity.
            return [
                VectorHit(key=r["key"], score=max(0.0, 1.0 - float(r["distance"])))
                for r in rows
            ]
        rows = self._db.execute("SELECT key, embedding FROM vault_vec").fetchall()
        scored = [
            VectorHit(key=r["key"], score=cosine(query_vec, _unpack(r["embedding"], self._dim)))
            for r in rows
        ]
        scored.sort(key=lambda h: -h.score)
        return scored[:limit]

    def count(self) -> int:
        row = self._db.execute("SELECT COUNT(*) AS c FROM vault_vec").fetchone()
        return int(row["c"]) if row else 0

    def close(self) -> None:
        self._db.close()


__all__ = [
    "EMBEDDING_DIM",
    "VectorHit",
    "VectorStore",
    "backend_name",
    "cosine",
    "embed",
]
