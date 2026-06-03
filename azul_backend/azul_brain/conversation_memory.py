"""Memory and semantic-retrieval behaviour for the conversation orchestrator.

Extracted from ``conversation.py`` as a mixin so the orchestrator's memory
layer (SafeMemory short-term history, vector store, embeddings, preference
extraction, and profile seeding) lives on its own. The mixin relies only on
attributes the orchestrator sets in ``__init__``/``_setup_memory_layers``
(``self.memory``, ``self.vector_memory``, ``self.embedding_service``,
``self.preference_extractor``, ``self.runtime_manager``), so it carries no state
of its own.
"""

import logging

from .memory.embedding_service import EmbeddingService
from .memory.preference_extractor import PreferenceExtractor
from .memory.safe_memory import SafeMemory
from .memory.vector_store import VectorMemoryStore

LOGGER = logging.getLogger(__name__)


class MemoryMixin:
    """Memory setup, persistence, and semantic retrieval for the orchestrator."""

    def _setup_memory_layers(self) -> None:
        """Initialises SafeMemory, embeddings, vector store, and preference extractor."""
        self.memory = SafeMemory.from_env()
        self.embedding_service = None
        self.vector_memory = None
        self.preference_extractor = None

        # Vector store always starts â€” it works text-only (BM25) even without embeddings.
        try:
            self.vector_memory = VectorMemoryStore.from_env()
            LOGGER.info("[Memory] Vector memory (SQLite) enabled.")
        except Exception as error:
            LOGGER.warning("[Memory] Vector memory disabled: %s", error)

        # Embedding service is optional â€” vector search upgrades automatically when present.
        if self.vector_memory is not None:
            try:
                self.embedding_service = EmbeddingService.from_env()
                LOGGER.info("[Memory] Embedding service enabled.")
            except Exception as error:
                LOGGER.info("[Memory] Embedding service unavailable, using text-only storage: %s", error)

        # Extractor only needs the vector store â€” it stores text even without embeddings.
        if self.vector_memory is not None:
            try:
                self.preference_extractor = PreferenceExtractor(
                    runtime_manager=self.runtime_manager,
                    embedding_service=self.embedding_service,  # may be None
                    vector_store=self.vector_memory,
                )
                LOGGER.info("[Memory] Preference extractor enabled.")
            except Exception as error:
                LOGGER.warning("[Memory] Preference extractor disabled: %s", error)

    def reload_persistent_memory(self) -> None:
        """Re-open SQLite using the current hatching profile (e.g. after onboarding)."""
        if self.vector_memory is not None:
            try:
                self.vector_memory.close()
            except Exception as error:
                LOGGER.warning("[Memory] Error closing vector store: %s", error)
            self.vector_memory = None

        self.memory.close()
        self._setup_memory_layers()

    async def persist_with_vector_memory(
        self,
        user_id: str,
        role: str,
        content: str,
        conversation_id: str | None = None,
    ) -> str:
        """Persists to short-term conversation history (SafeMemory only).

        Raw conversation turns are NOT indexed in the vector store â€” only
        extracted preferences and facts go there (via PreferenceExtractor).
        """
        return self.memory.add_message(user_id, role, content, conversation_id=conversation_id)

    async def retrieve_semantic_memories(self, user_id: str, query_text: str) -> list[dict]:
        """Retrieves relevant memories. Uses hybrid search when embeddings are available,
        falls back to BM25 text-only search when the embedding service is not configured."""
        if self.vector_memory is None:
            return []

        try:
            if self.embedding_service is not None:
                query_embedding = await self.embedding_service.embed_text(query_text)
                if query_embedding:
                    return self.vector_memory.search_hybrid(
                        user_id=user_id,
                        query_embedding=query_embedding,
                        query_text=query_text,
                        limit=5,
                        min_similarity=0.5,
                    )
            # Fallback: BM25 text search when no embedding service or embedding failed
            return self.vector_memory.search_text(user_id=user_id, query_text=query_text, limit=5)
        except Exception as error:
            LOGGER.warning("[Memory] Error retrieving vector memory: %s", error)
            return []

    async def seed_profile_facts(self, user_id: str = "desktop-user") -> None:
        """Seeds hatching profile preferences as memories. Called once after onboarding."""
        if self.vector_memory is None:
            return
        from .api.hatching_store import HatchingStore
        profile = HatchingStore().load()
        if not profile.is_hatched:
            LOGGER.debug("[Memory] Skipping profile seeding for user %s â€” onboarding not complete", user_id)
            return

        # Each entry: (stable feature_key, content). Using upsert so re-running this
        # after onboarding edits updates the existing featured row in place.
        featured_slots: list[tuple[str, str]] = []

        if profile.role.strip():
            featured_slots.append(("profile:role", f"The user wants this assistant to be: {profile.role.strip()}"))

        if profile.mission.strip():
            featured_slots.append(("profile:mission", f"The user's main goal is: {profile.mission.strip()}"))

        style_parts = [p for p in [profile.tone, profile.style, profile.autonomy] if p.strip()]
        if style_parts:
            featured_slots.append((
                "profile:style",
                f"The user prefers communication that is {', '.join(style_parts).lower()}.",
            ))

        featured_slots.append((
            "profile:confirmation",
            "The user wants to be asked for confirmation before sensitive or destructive actions."
            if profile.confirm_sensitive_actions
            else "The user has granted full autonomy â€” no confirmation needed for actions.",
        ))

        # Exclude built-in capabilities (Memory) â€” only show real integrations
        _BUILTIN_SKILLS = {"Memory"}
        active_integrations = [s for s in profile.skills if s not in _BUILTIN_SKILLS]
        if active_integrations:
            featured_slots.append(("profile:skills", f"Active integrations the assistant can use: {', '.join(active_integrations)}."))

        seeded = 0
        for feature_key, content in featured_slots:
            if not content.strip():
                continue
            try:
                embedding: list[float] | None = None
                if self.embedding_service is not None:
                    result = await self.embedding_service.embed_text(content)
                    embedding = result if result else None
                self.vector_memory.upsert_featured(user_id, feature_key, content, embedding)
                seeded += 1
            except Exception as error:
                LOGGER.warning("[Memory] Failed to upsert featured slot '%s': %s", feature_key, error)
        LOGGER.info("[Memory] Upserted %d featured profile slots for user %s", seeded, user_id)

    def retrieve_user_knowledge(self, user_id: str) -> list[dict]:
        """Returns atemporal preferences and facts learned about the user."""
        if self.vector_memory is None:
            return []
        try:
            return self.vector_memory.get_user_knowledge(user_id, limit=30)
        except Exception as error:
            LOGGER.warning("[Memory] Error retrieving user knowledge: %s", error)
            return []
