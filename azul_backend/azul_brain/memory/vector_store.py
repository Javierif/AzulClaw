"""Persistent hybrid vector memory store backed by SQLite + FTS5.

All data survives restarts and supports three search modes:

* **vector** — cosine similarity computed in Python (vectors stored as BLOBs)
* **text** — BM25 keyword search via FTS5 (built into SQLite)
* **hybrid** — weighted fusion of both (default 70 % vector / 30 % text)

Vector storage uses standard SQLite BLOBs with pure-Python cosine similarity.
This approach works on *every* Python installation (including the macOS system
Python) and is performant for personal-assistant scale (thousands of memories).
If sqlite-vec becomes available in a production environment, the search can
be swapped to use hardware-accelerated KNN with no schema changes.
"""

from __future__ import annotations

import logging
import math
import os
import sqlite3
import struct
import uuid
from pathlib import Path

from ..api.hatching_store import is_vector_memory_enabled, resolve_memory_db_path

LOGGER = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
_DEFAULT_EMBEDDING_DIM = 1536  # text-embedding-3-small


# ---------------------------------------------------------------------------
# Vector serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_vector(vec: list[float]) -> bytes:
    """Packs a float list into a compact binary BLOB."""
    return struct.pack(f"{len(vec)}f", *vec)


def _deserialize_vector(blob: bytes) -> list[float]:
    """Unpacks a binary BLOB back into a float list."""
    count = len(blob) // 4  # 4 bytes per float32
    return list(struct.unpack(f"{count}f", blob))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    """Returns the cosine similarity between two vectors."""
    if len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x * x for x in a))
    mag_b = math.sqrt(sum(x * x for x in b))
    if mag_a == 0.0 or mag_b == 0.0:
        return 0.0
    return dot / (mag_a * mag_b)


# ---------------------------------------------------------------------------
# Public store
# ---------------------------------------------------------------------------

class VectorMemoryStore:
    """Persistent vector + full-text store for semantic memory retrieval."""

    def __init__(self, db_path: str, embedding_dim: int = _DEFAULT_EMBEDDING_DIM) -> None:
        self._db_path = db_path
        self._dim = embedding_dim

        # Ensure the parent directory exists
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)

        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.row_factory = sqlite3.Row

        self._init_schema()
        LOGGER.info(
            "[VectorStore] Opened %s (dim=%d)", db_path, embedding_dim
        )

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_env(cls) -> "VectorMemoryStore":
        """Creates a VectorMemoryStore from environment variables.

        Raises RuntimeError if vector memory is explicitly disabled.
        """
        if not is_vector_memory_enabled():
            raise RuntimeError("Vector memory is disabled in Settings.")

        db_path = resolve_memory_db_path()
        dim = int(os.environ.get("AZUL_EMBEDDING_DIM", str(_DEFAULT_EMBEDDING_DIM)))
        return cls(db_path=db_path, embedding_dim=dim)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Creates tables, virtual tables, and triggers if they don't exist."""
        cur = self._conn.cursor()

        # Main memories table (metadatos + text + embedding BLOB)
        cur.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id          TEXT PRIMARY KEY,
                user_id     TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                source      TEXT DEFAULT 'chat',
                category    TEXT DEFAULT 'conversation',
                feature_key TEXT,
                embedding   BLOB,
                created_at  TEXT DEFAULT (datetime('now')),
                updated_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        # Migration: add feature_key to existing DBs that predate this column
        try:
            cur.execute("ALTER TABLE memories ADD COLUMN feature_key TEXT")
        except Exception:
            pass  # column already exists

        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_user
            ON memories(user_id)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_category
            ON memories(user_id, category)
        """)
        cur.execute("""
            CREATE INDEX IF NOT EXISTS idx_memories_feature_key
            ON memories(user_id, feature_key)
        """)

        # FTS5 index for BM25 full-text search
        cur.execute("""
            CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
            USING fts5(content, content=memories, content_rowid=rowid)
        """)

        # Triggers to keep FTS5 synchronised with the memories table
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                INSERT INTO memories_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
            END
        """)
        cur.execute("""
            CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                INSERT INTO memories_fts(memories_fts, rowid, content)
                VALUES ('delete', old.rowid, old.content);
                INSERT INTO memories_fts(rowid, content)
                VALUES (new.rowid, new.content);
            END
        """)

        self._conn.commit()

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def add_memory(
        self,
        user_id: str,
        role: str,
        content: str,
        embedding: list[float] | None,
        source: str = "chat",
        category: str = "conversation",
        feature_key: str | None = None,
    ) -> str:
        """Stores a memory entry. Embedding may be None for text-only (BM25-searchable) records.

        Returns the generated memory id.
        """
        memory_id = str(uuid.uuid4())
        vec_blob = _serialize_vector(embedding) if embedding else None

        self._conn.execute(
            """
            INSERT INTO memories (id, user_id, role, content, source, category, feature_key, embedding)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (memory_id, user_id, role, content, source, category, feature_key, vec_blob),
        )
        self._conn.commit()
        return memory_id

    def upsert_featured(
        self,
        user_id: str,
        feature_key: str,
        content: str,
        embedding: list[float] | None,
    ) -> str:
        """Inserts or updates a featured preference by its stable feature_key.

        Featured preferences represent the most important things to know about the
        user (goals, communication style, autonomy). They are always shown at the
        top of the memory list and updated in place when the user's preferences change.
        Returns the memory id.
        """
        vec_blob = _serialize_vector(embedding) if embedding else None
        existing = self._conn.execute(
            "SELECT id FROM memories WHERE user_id = ? AND feature_key = ?",
            (user_id, feature_key),
        ).fetchone()

        if existing:
            self._conn.execute(
                """
                UPDATE memories
                SET content = ?, embedding = ?, updated_at = datetime('now')
                WHERE id = ?
                """,
                (content, vec_blob, existing["id"]),
            )
            self._conn.commit()
            LOGGER.debug("[VectorStore] Updated featured memory %s: %s", feature_key, content[:60])
            return existing["id"]

        return self.add_memory(
            user_id=user_id,
            role="system",
            content=content,
            embedding=embedding,
            source="featured",
            category="preference",
            feature_key=feature_key,
        )

    def add_preference(
        self,
        user_id: str,
        content: str,
        embedding: list[float] | None,
        source: str = "extractor",
    ) -> str:
        """Shortcut to store a user preference (atemporal fact)."""
        return self.add_memory(
            user_id=user_id,
            role="system",
            content=content,
            embedding=embedding,
            source=source,
            category="preference",
        )

    def add_fact(
        self,
        user_id: str,
        content: str,
        embedding: list[float] | None,
        source: str = "extractor",
    ) -> str:
        """Shortcut to store a learned fact about the user."""
        return self.add_memory(
            user_id=user_id,
            role="system",
            content=content,
            embedding=embedding,
            source=source,
            category="fact",
        )

    # ------------------------------------------------------------------
    # Search: vector (semantic)
    # ------------------------------------------------------------------

    def search_vector(
        self,
        user_id: str,
        query_embedding: list[float],
        limit: int = 5,
        min_similarity: float = 0.28,
        candidate_pool: int = 150,
    ) -> list[dict]:
        """Returns the most semantically similar memories for a user.

        Loads the most recent *candidate_pool* entries and computes cosine
        similarity in Python.  This is fast for personal-assistant scale.
        """
        rows = self._conn.execute(
            """
            SELECT id, content, source, category, embedding
            FROM memories
            WHERE user_id = ? AND embedding IS NOT NULL AND length(embedding) > 0
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, candidate_pool),
        ).fetchall()

        scored: list[tuple[dict, float]] = []
        for row in rows:
            stored_vec = _deserialize_vector(row["embedding"])
            sim = _cosine_similarity(query_embedding, stored_vec)
            if sim >= min_similarity:
                scored.append((
                    {
                        "id": row["id"],
                        "content": row["content"],
                        "source": row["source"],
                        "category": row["category"],
                        "similarity": sim,
                    },
                    sim,
                ))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored[:limit]]

    # ------------------------------------------------------------------
    # Search: BM25 full-text
    # ------------------------------------------------------------------

    def search_text(
        self,
        user_id: str,
        query_text: str,
        limit: int = 20,
    ) -> list[dict]:
        """BM25 keyword search using FTS5."""
        if not query_text or not query_text.strip():
            return []

        # Escape special FTS5 characters to prevent syntax errors
        safe_query = self._sanitize_fts_query(query_text)
        if not safe_query:
            return []

        try:
            rows = self._conn.execute(
                """
                SELECT
                    m.id,
                    m.content,
                    m.source,
                    m.category,
                    memories_fts.rank AS bm25_score
                FROM memories_fts
                JOIN memories m ON m.rowid = memories_fts.rowid
                WHERE memories_fts MATCH ?
                  AND m.user_id = ?
                ORDER BY memories_fts.rank
                LIMIT ?
                """,
                (safe_query, user_id, limit),
            ).fetchall()

            return [
                {
                    "id": row["id"],
                    "content": row["content"],
                    "source": row["source"],
                    "category": row["category"],
                    "bm25_score": float(row["bm25_score"]),
                }
                for row in rows
            ]
        except sqlite3.OperationalError as error:
            LOGGER.debug("[VectorStore] FTS5 query failed: %s", error)
            return []

    # ------------------------------------------------------------------
    # Search: hybrid (vector + BM25 fusion)
    # ------------------------------------------------------------------

    def search_hybrid(
        self,
        user_id: str,
        query_embedding: list[float],
        query_text: str,
        limit: int = 5,
        min_similarity: float = 0.28,
        vector_weight: float | None = None,
        text_weight: float | None = None,
        candidate_multiplier: int = 4,
    ) -> list[dict]:
        """Weighted fusion of vector + BM25 results using Reciprocal Rank Fusion."""
        from .hybrid_ranker import hybrid_rank

        vw = vector_weight if vector_weight is not None else float(
            os.environ.get("AZUL_HYBRID_VECTOR_WEIGHT", "0.7")
        )
        tw = text_weight if text_weight is not None else float(
            os.environ.get("AZUL_HYBRID_TEXT_WEIGHT", "0.3")
        )

        expanded_limit = limit * candidate_multiplier

        vector_results = self.search_vector(
            user_id=user_id,
            query_embedding=query_embedding,
            limit=expanded_limit,
            min_similarity=min_similarity,
        )

        text_results = self.search_text(
            user_id=user_id,
            query_text=query_text,
            limit=expanded_limit,
        )

        return hybrid_rank(
            vector_results=vector_results,
            text_results=text_results,
            vector_weight=vw,
            text_weight=tw,
            limit=limit,
        )

    # ------------------------------------------------------------------
    # Search: backward compatible (drop-in for old API)
    # ------------------------------------------------------------------

    def search_similar(
        self,
        user_id: str,
        query_embedding: list[float],
        limit: int = 5,
        min_similarity: float = 0.28,
        candidate_pool: int = 150,
    ) -> list[dict]:
        """Backward-compatible API matching the old in-memory store signature."""
        return self.search_vector(
            user_id=user_id,
            query_embedding=query_embedding,
            limit=limit,
            min_similarity=min_similarity,
            candidate_pool=candidate_pool,
        )

    # ------------------------------------------------------------------
    # User-centric queries (preferences & facts)
    # ------------------------------------------------------------------

    def get_user_preferences(self, user_id: str, limit: int = 50) -> list[dict]:
        """Returns atemporal preferences learned about the user."""
        return self._get_by_category(user_id, "preference", limit)

    def get_user_facts(self, user_id: str, limit: int = 50) -> list[dict]:
        """Returns atemporal facts learned about the user."""
        return self._get_by_category(user_id, "fact", limit)

    def get_user_knowledge(self, user_id: str, limit: int = 100) -> list[dict]:
        """Returns saved user preferences. Featured memories (feature_key IS NOT NULL) come first."""
        rows = self._conn.execute(
            """
            SELECT id, content, source, category, feature_key, created_at
            FROM memories
            WHERE user_id = ? AND category = 'preference'
            ORDER BY
                CASE WHEN feature_key IS NOT NULL THEN 0 ELSE 1 END,
                created_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_memory(self, memory_id: str, user_id: str) -> bool:
        """Deletes a memory entry by ID. Returns True if a row was deleted."""
        cur = self._conn.execute(
            "DELETE FROM memories WHERE id = ? AND user_id = ?",
            (memory_id, user_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def preference_exists(self, user_id: str, content: str) -> bool:
        """Checks if a similar preference or fact already exists (deduplication)."""
        row = self._conn.execute(
            """
            SELECT 1 FROM memories
            WHERE user_id = ?
              AND category IN ('preference', 'fact')
              AND content = ?
            LIMIT 1
            """,
            (user_id, content),
        ).fetchone()
        return row is not None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_by_category(self, user_id: str, category: str, limit: int) -> list[dict]:
        rows = self._conn.execute(
            """
            SELECT id, content, source, category, created_at
            FROM memories
            WHERE user_id = ? AND category = ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (user_id, category, limit),
        ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def _sanitize_fts_query(text: str) -> str:
        """Strips FTS5 special characters to build a safe MATCH expression."""
        # Remove FTS5 operators: AND OR NOT NEAR + - * ^ " ( )
        import re
        words = re.findall(r"[a-zA-ZÀ-ÿ0-9]+", text)
        if not words:
            return ""
        # Join with implicit AND (FTS5 default)
        return " ".join(words)

    def close(self) -> None:
        """Closes the underlying database connection."""
        self._conn.close()
