"""Local in-process vector memory store with cosine similarity search."""

import math
import os
import uuid
from dataclasses import dataclass, field


@dataclass
class _MemoryEntry:
    id: str
    user_id: str
    role: str
    content: str
    embedding: list[float]
    source: str


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


class VectorMemoryStore:
    """In-process vector store for semantic memory retrieval."""

    def __init__(self) -> None:
        self._entries: list[_MemoryEntry] = []

    @classmethod
    def from_env(cls) -> "VectorMemoryStore":
        """Creates a VectorMemoryStore. Raises if vector memory is explicitly disabled."""
        if os.environ.get("VECTOR_MEMORY_ENABLED", "true").strip().lower() == "false":
            raise RuntimeError("Vector memory is disabled via VECTOR_MEMORY_ENABLED=false.")
        return cls()

    def add_memory(
        self,
        user_id: str,
        role: str,
        content: str,
        embedding: list[float],
        source: str = "chat",
    ) -> None:
        """Stores a memory entry with its embedding vector."""
        self._entries.append(
            _MemoryEntry(
                id=str(uuid.uuid4()),
                user_id=user_id,
                role=role,
                content=content,
                embedding=embedding,
                source=source,
            )
        )

    def search_similar(
        self,
        user_id: str,
        query_embedding: list[float],
        limit: int = 5,
        min_similarity: float = 0.28,
        candidate_pool: int = 150,
    ) -> list[dict]:
        """Returns the most semantically similar memory entries for a user."""
        candidates = [e for e in self._entries if e.user_id == user_id]
        candidates = candidates[-candidate_pool:]

        scored = [
            (e, _cosine_similarity(query_embedding, e.embedding))
            for e in candidates
        ]
        scored = [(e, s) for e, s in scored if s >= min_similarity]
        scored.sort(key=lambda x: x[1], reverse=True)

        return [
            {"content": e.content, "source": e.source, "similarity": s}
            for e, s in scored[:limit]
        ]
