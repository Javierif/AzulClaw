"""Reusable conversation services for the bot and desktop API."""

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from agent_framework import Message

from .cortex.fast.commentary import (
    build_commentary,
    build_progress_snapshot,
    normalize_fast_visible_commentary,
    normalize_fast_visible_plan,
    prompt_for_fast_visible_commentary,
    prompt_for_fast_visible_plan,
)
from .cortex.fast.triage import TriageDecision, classify_message
from .memory.embedding_service import EmbeddingService
from .memory.preference_extractor import PreferenceExtractor
from .memory.safe_memory import SafeMemory
from .memory.vector_store import VectorMemoryStore
from .runtime.agent_runtime import AgentRuntimeManager
from .runtime.heartbeat_intent import HeartbeatIntentService

LOGGER = logging.getLogger(__name__)


@dataclass
class ConversationReply:
    """Enriched reply from the orchestrator."""

    text: str
    model_id: str = ""
    model_label: str = ""
    process_id: str = ""
    lane: str = "auto"
    triage_reason: str = ""
    conversation_title: str | None = None


def extract_result_text(result) -> str:
    """Normalises the agent adapter response to serialisable text."""
    value = getattr(result, "value", None)
    if isinstance(value, str):
        return value
    return str(result)


_TRIVIAL_QUERY = re.compile(
    r"^\s*(?:hi|hello|hey|hola|good\s+\w+|thanks?|thank\s+you|ok|okay|bye|ciao|sup|yo|howdy)"
    r"[\W\s]{0,20}$",
    re.I,
)


def _is_trivial_query(text: str) -> bool:
    """Returns True for greetings and small-talk that don't benefit from memory retrieval."""
    stripped = (text or "").strip()
    if len(stripped.split()) < 4:
        return bool(_TRIVIAL_QUERY.match(stripped))
    return False


_PLACEHOLDER_TITLES = frozenset(
    {
        "",
        "new conversation",
        "new chat",
        "main conversation",
    }
)


def _is_placeholder_conversation_title(title: str | None) -> bool:
    """True when the row still has a generic default title (not user-meaningful)."""
    t = (title or "").strip().lower()
    return t in _PLACEHOLDER_TITLES


def _looks_like_bad_generated_title(title: str) -> bool:
    """Heuristic: model returned a greeting or generic label instead of a topic."""
    t = (title or "").strip().lower()
    if not t:
        return True
    if "conversation starter" in t or "chat with" in t:
        return True
    if t.startswith(("hello:", "hi:", "hey:", "hola:")):
        return True
    return False


def should_skip_vectorization(text: str) -> bool:
    """Avoids indexing potentially sensitive text in local vector memory."""
    low = (text or "").lower()
    sensitive_markers = (
        "api_key",
        "apikey",
        "token",
        "password",
        "contraseña",
        "secret",
        "bearer ",
        "authorization:",
    )
    return any(marker in low for marker in sensitive_markers)


class ConversationOrchestrator:
    """Orchestrates memory, semantic retrieval, and agent invocation."""

    def __init__(self, mcp_client, runtime_manager: AgentRuntimeManager):
        self.mcp_client = mcp_client
        self.runtime_manager = runtime_manager
        self.heartbeat_intents = HeartbeatIntentService(
            runtime_manager=runtime_manager,
            store=runtime_manager.store,
        )
        self._setup_memory_layers()

    def _setup_memory_layers(self) -> None:
        """Initialises SafeMemory, embeddings, vector store, and preference extractor."""
        self.memory = SafeMemory.from_env()
        self.embedding_service = None
        self.vector_memory = None
        self.preference_extractor = None

        # Vector store always starts — it works text-only (BM25) even without embeddings.
        try:
            self.vector_memory = VectorMemoryStore.from_env()
            LOGGER.info("[Memory] Vector memory (SQLite) enabled.")
        except Exception as error:
            LOGGER.warning("[Memory] Vector memory disabled: %s", error)

        # Embedding service is optional — vector search upgrades automatically when present.
        if self.vector_memory is not None:
            try:
                self.embedding_service = EmbeddingService.from_env()
                LOGGER.info("[Memory] Embedding service enabled.")
            except Exception as error:
                LOGGER.info("[Memory] Embedding service unavailable, using text-only storage: %s", error)

        # Extractor only needs the vector store — it stores text even without embeddings.
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
    ) -> None:
        """Persists to short-term conversation history (SafeMemory only).

        Raw conversation turns are NOT indexed in the vector store — only
        extracted preferences and facts go there (via PreferenceExtractor).
        """
        self.memory.add_message(user_id, role, content, conversation_id=conversation_id)

    def _should_generate_conversation_title(
        self,
        conversation_id: str | None,
        user_message: str,
        *,
        is_first_turn: bool,
    ) -> bool:
        """Sidebar title once: first substantive turn, or next substantive turn if still placeholder."""
        if not conversation_id:
            return False
        if _is_trivial_query(user_message):
            return False
        if is_first_turn:
            return True
        current = self.memory.get_conversation_title(conversation_id)
        return _is_placeholder_conversation_title(current)

    def _finalize_generated_title(self, title: str, source_message: str) -> str:
        """Drop generic model outputs; prefer a short clip of the user's question."""
        cleaned = (title or "").strip().strip('"').strip()
        if cleaned and not _looks_like_bad_generated_title(cleaned):
            return cleaned
        fallback = source_message[:60].strip()
        return fallback

    async def _refine_conversation_title_with_llm(
        self,
        conversation_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """LLM sidebar title from the first exchange (question + answer excerpt)."""
        deployment = os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "").strip()
        if not deployment:
            return

        ans = (assistant_reply or "").strip()
        if len(ans) > 1200:
            ans = ans[:1200] + "…"

        title_prompt = (
            "You name chat threads for a sidebar list.\n\n"
            f"User asked:\n\"\"\"{user_message[:500]}\"\"\"\n\n"
            f"Assistant answered (excerpt):\n\"\"\"{ans}\"\"\"\n\n"
            "Write ONE short title (4–7 words) summarizing the topic or outcome of this exchange. "
            "Prefer concrete subject matter (e.g. weather in Barcelona, Python error) over generic words. "
            "Do not start with Hello, Hi, or Hey. "
            "Do not use 'Conversation Starter', 'New chat', 'Chat', or 'Main conversation'. "
            "Reply with the title only, no quotes."
        )
        try:
            result = await self.runtime_manager.execute_messages(
                messages=[Message(role="user", contents=title_prompt)],
                lane="fast",
                title="Conversation title",
                source="conversation-title",
                kind="agent-run",
                tools_enabled=False,
                instructions="Return only a concise conversation title.",
            )
            raw = (result.text or "").strip()
            title = self._finalize_generated_title(raw, user_message)
            if title:
                self.memory.update_conversation_title(conversation_id, title)
                return
        except Exception as error:
            LOGGER.debug("[Brain] Title generation failed: %s", error)

    async def retrieve_semantic_memories(self, user_id: str, query_text: str) -> list[dict]:
        """Retrieves relevant memories. Uses hybrid search when embeddings are available,
        falls back to BM25 text-only search when the embedding service is not configured.
        Skips retrieval entirely for trivial greetings and small-talk."""
        if self.vector_memory is None:
            return []
        if _is_trivial_query(query_text):
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
            LOGGER.debug("[Memory] Skipping profile seeding for user %s — onboarding not complete", user_id)
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
            else "The user has granted full autonomy — no confirmation needed for actions.",
        ))

        # Exclude built-in capabilities (Memory) — only show real integrations
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

    async def invoke_messages(
        self,
        messages: list[Message],
        user_message: str,
        *,
        lane: str,
        source: str,
        title: str,
    ) -> ConversationReply:
        """Invokes the agent with structured messages and falls back on content filter errors."""
        try:
            result = await self.runtime_manager.execute_messages(
                messages=messages,
                lane=lane,
                title=title,
                source=source,
                kind="agent-run",
            )
            return ConversationReply(
                text=result.text,
                model_id=result.model.id if result.model else "",
                model_label=result.model.label if result.model else "",
                process_id=result.process_id,
                lane=lane,
            )
        except Exception as error:
            error_text = str(error)
            if "content_filter" in error_text or "ResponsibleAIPolicyViolation" in error_text:
                fallback = self._fallback_for_filtered_prompt(user_message)
                if fallback:
                    LOGGER.warning("[Brain] Azure filtered the prompt. Using local fallback.")
                    return ConversationReply(text=fallback, lane=lane)
            return ConversationReply(
                text=(
                    "Could not execute the cognitive layer yet. "
                    "Check Azure OpenAI configuration and Microsoft Entra authentication.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,
            )

    async def invoke_messages_stream(
        self,
        messages: list[Message],
        user_message: str,
        *,
        lane: str,
        source: str,
        title: str,
        on_delta: Callable[[str], Awaitable[None]],
    ) -> ConversationReply:
        """Invokes the agent and emits deltas when the runtime uses streaming."""
        try:
            result = await self.runtime_manager.execute_messages_stream(
                messages=messages,
                lane=lane,
                title=title,
                source=source,
                kind="agent-run",
                on_delta=on_delta,
            )
            return ConversationReply(
                text=result.text,
                model_id=result.model.id if result.model else "",
                model_label=result.model.label if result.model else "",
                process_id=result.process_id,
                lane=lane,
            )
        except Exception as error:
            error_text = str(error)
            if "content_filter" in error_text or "ResponsibleAIPolicyViolation" in error_text:
                fallback = self._fallback_for_filtered_prompt(user_message)
                if fallback:
                    LOGGER.warning("[Brain] Azure filtered the prompt. Using local fallback.")
                    await on_delta(fallback)
                    return ConversationReply(text=fallback, lane=lane)
            return ConversationReply(
                text=(
                    "Could not execute the cognitive layer yet. "
                    "Check Azure OpenAI configuration and Microsoft Entra authentication.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,
            )

    async def generate_fast_visible_plan(self, user_message: str, *, reason: str) -> tuple[str, dict]:
        """Asks the fast brain for the first visible narration and a summarised plan."""
        prompt_messages = [
            Message(role=item["role"], contents=item["text"])
            for item in prompt_for_fast_visible_plan(user_message, reason=reason)
        ]
        try:
            reply = await self.invoke_messages(
                prompt_messages,
                user_message,
                lane="fast",
                source="commentary",
                title="Visible narration",
            )
            return normalize_fast_visible_plan(reply.text, user_message=user_message, reason=reason)
        except Exception as error:
            LOGGER.warning("[Brain] Fast visible plan failed: %s", error)
            fallback_commentary = build_commentary(user_message, reason=reason, lane="slow")
            fallback_progress = build_progress_snapshot(
                user_message,
                reason=reason,
                lane="slow",
                stage="delegated",
                summary=fallback_commentary,
            )
            fallback_blueprint = {
                "title": fallback_progress["title"],
                "badge": fallback_progress["badge"],
                "summary": {"thinking": fallback_progress["summary"]},
                "phases": [
                    {
                        "id": phase["id"],
                        "label": phase["label"],
                        "steps": [step["label"] for step in phase["steps"]],
                    }
                    for phase in fallback_progress["phases"]
                ],
            }
            return fallback_commentary, fallback_blueprint

    async def generate_fast_visible_commentary(
        self,
        user_message: str,
        *,
        reason: str,
        lane: str,
    ) -> str:
        """Asks the fast brain for the first visible bubble for any route."""
        prompt_messages = [
            Message(role=item["role"], contents=item["text"])
            for item in prompt_for_fast_visible_commentary(user_message, reason=reason, lane=lane)
        ]
        try:
            reply = await self.invoke_messages(
                prompt_messages,
                user_message,
                lane="fast",
                source="commentary",
                title="First visible bubble",
            )
            return normalize_fast_visible_commentary(
                reply.text,
                user_message=user_message,
                reason=reason,
                lane=lane,
            )
        except Exception as error:
            LOGGER.warning("[Brain] Fast visible commentary failed: %s", error)
            return build_commentary(user_message, reason=reason, lane=lane)

    def _fallback_for_filtered_prompt(self, user_message: str) -> str | None:
        """Returns a safe response if Azure filters a simple request."""
        normalized = (user_message or "").strip().lower()
        if normalized in {"hola", "buenas", "hey", "hello", "holi"}:
            return "Hi. I'm active and ready to help."
        if normalized in {"gracias", "muchas gracias"}:
            return "You're welcome."
        if normalized in {"que tal", "como estas", "cómo estás"}:
            return "I'm operational and ready to work with you."
        return None

    def build_agent_messages(
        self,
        history: list[dict],
        semantic_memories: list[dict],
        user_message: str,
        user_knowledge: list[dict] | None = None,
    ) -> list[Message]:
        """Converts history and context into real messages for the framework."""
        messages: list[Message] = []

        # Inject user knowledge split by source so the LLM knows which takes precedence
        if user_knowledge:
            explicit = [k for k in user_knowledge if k.get("source") == "extractor"]
            baseline = [k for k in user_knowledge if k.get("source") == "hatching-profile"]

            sections: list[str] = []
            if explicit:
                lines = "\n".join(f"- {k['content']}" for k in explicit if k.get("content"))
                sections.append("What the user has told you directly (higher priority):\n" + lines)
            if baseline:
                lines = "\n".join(f"- {k['content']}" for k in baseline if k.get("content"))
                sections.append("Initial setup preferences (use as baseline, explicit statements above override these):\n" + lines)

            if sections:
                messages.append(
                    Message(
                        role="system",
                        contents="\n\n".join(sections),
                    )
                )

        for item in history:
            role = item.get("role", "user")
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content", "")).strip()
            if content:
                messages.append(Message(role=role, contents=content))

        if semantic_memories:
            memory_lines: list[str] = []
            for memory in semantic_memories:
                content = str(memory.get("content", "")).strip()
                if not content:
                    continue
                source = str(memory.get("source", "chat"))
                similarity = float(memory.get("similarity", 0.0))
                hybrid_score = memory.get("hybrid_score")
                if hybrid_score is not None:
                    memory_lines.append(f"- ({source}, hybrid={hybrid_score:.4f}) {content}")
                else:
                    memory_lines.append(f"- ({source}, sim={similarity:.2f}) {content}")

            if memory_lines:
                messages.append(
                    Message(
                        role="assistant",
                        contents="Retrieved context for this conversation:\n" + "\n".join(memory_lines),
                    )
                )

        messages.append(Message(role="user", contents=user_message))
        return messages

    def resolve_route(self, user_message: str, requested_lane: str = "auto") -> TriageDecision:
        """Determines the effective cognitive route for this turn."""
        normalized = (requested_lane or "").strip().lower()
        if normalized in {"fast", "slow"}:
            return TriageDecision(lane=normalized, reason="explicit")
        if normalized == "auto":
            return classify_message(user_message)

        default_lane = self.runtime_manager.load_settings().default_lane
        if default_lane == "auto":
            return classify_message(user_message)
        return TriageDecision(lane=default_lane, reason="runtime-default")

    def resolve_lane(self, user_message: str, requested_lane: str = "auto") -> str:
        """Backwards compatibility helper to get only the lane."""
        return self.resolve_route(user_message, requested_lane).lane

    async def process_message(
        self,
        *,
        user_id: str,
        user_message: str,
        lane: str = "auto",
        source: str = "chat",
        store_memory: bool = True,
        title: str | None = None,
    ) -> str:
        """Builds context, runs inference, and persists the conversation if applicable."""
        route = self.resolve_route(user_message, lane)
        effective_lane = route.lane
        history = self.memory.get_history(user_id, limit=12)
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        user_knowledge = self.retrieve_user_knowledge(user_id)
        messages = self.build_agent_messages(history, semantic_memories, user_message, user_knowledge)

        LOGGER.info("[Brain] Message received. History=%s Knowledge=%s", len(history), len(user_knowledge))
        reply = await self.invoke_messages(
            messages,
            user_message,
            lane=effective_lane,
            source=source,
            title=title or "Main conversation",
        )

        if store_memory:
            await self.persist_with_vector_memory(user_id, "user", user_message)
            await self.persist_with_vector_memory(user_id, "assistant", reply.text)
            # Fire-and-forget preference extraction (skip if message contains credentials)
            if self.preference_extractor and not should_skip_vectorization(user_message):
                self.preference_extractor.fire_and_forget(user_id, user_message, reply.text)

        return reply.text

    async def process_user_message(
        self,
        user_id: str,
        user_message: str,
        lane: str = "auto",
        conversation_id: str | None = None,
    ) -> ConversationReply:
        """Builds context, runs inference, and persists the conversation."""
        heartbeat_reply = await self._try_handle_heartbeat_intent(
            user_id,
            user_message,
            conversation_id=conversation_id,
        )
        if heartbeat_reply is not None:
            return heartbeat_reply

        route = self.resolve_route(user_message, lane)
        effective_lane = route.lane
        history = self._load_chat_history(user_id, conversation_id, limit=12)
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        user_knowledge = self.retrieve_user_knowledge(user_id)
        messages = self.build_agent_messages(history, semantic_memories, user_message, user_knowledge)

        LOGGER.info("[Brain] Message received. History=%s Knowledge=%s", len(history), len(user_knowledge))
        reply = await self.invoke_messages(
            messages,
            user_message,
            lane=effective_lane,
            source="chat",
            title="Main conversation",
        )

        await self.persist_with_vector_memory(user_id, "user", user_message, conversation_id=conversation_id)
        await self.persist_with_vector_memory(user_id, "assistant", reply.text, conversation_id=conversation_id)
        # Fire-and-forget preference extraction (skip if message contains credentials)
        if self.preference_extractor and not should_skip_vectorization(user_message):
            self.preference_extractor.fire_and_forget(user_id, user_message, reply.text)
        if conversation_id:
            reply.conversation_title = self.memory.get_conversation_title(conversation_id)
        reply.triage_reason = route.reason
        return reply

    def _load_chat_history(
        self,
        user_id: str,
        conversation_id: str | None,
        *,
        limit: int,
    ) -> list[dict]:
        """Returns conversation history, with a RAM fallback when SQLite is unavailable."""
        if not conversation_id:
            return self.memory.get_history(user_id, limit=limit)

        history = self.memory.get_conversation_messages(conversation_id, limit=limit)
        if history:
            return history
        if getattr(self.memory, "_conn", None) is None:
            return self.memory.get_history(user_id, limit=limit)
        return history

    async def process_user_message_stream(
        self,
        user_id: str,
        user_message: str,
        *,
        lane: str = "auto",
        conversation_id: str | None = None,
        on_delta: Callable[[str], Awaitable[None]],
        on_commentary: Callable[[str], Awaitable[None]] | None = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> ConversationReply:
        """Builds context, runs inference, and emits streaming if applicable."""
        heartbeat_reply = await self._try_handle_heartbeat_intent(
            user_id,
            user_message,
            conversation_id=conversation_id,
        )
        if heartbeat_reply is not None:
            await on_delta(heartbeat_reply.text)
            return heartbeat_reply

        route = self.resolve_route(user_message, lane)
        effective_lane = route.lane
        progress_blueprint: dict | None = None
        if effective_lane == "slow":
            initial_commentary, progress_blueprint = await self.generate_fast_visible_plan(
                user_message,
                reason=route.reason,
            )
            if on_commentary is not None:
                await on_commentary(initial_commentary)
        if effective_lane == "slow" and on_progress is not None:
            await on_progress(
                build_progress_snapshot(
                    user_message,
                    reason=route.reason,
                    lane=effective_lane,
                    stage="delegated",
                    summary=initial_commentary,
                    blueprint=progress_blueprint,
                )
            )
        history = self._load_chat_history(user_id, conversation_id, limit=12)
        is_first_turn = len(history) == 0
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        user_knowledge = self.retrieve_user_knowledge(user_id)
        messages = self.build_agent_messages(history, semantic_memories, user_message, user_knowledge)
        context_commentary = "I now have the necessary context. Preparing the full response."
        if effective_lane == "slow" and on_commentary is not None:
            await on_commentary(context_commentary)
        if effective_lane == "slow" and on_progress is not None:
            await on_progress(
                build_progress_snapshot(
                    user_message,
                    reason=route.reason,
                    lane=effective_lane,
                    stage="context-ready",
                    summary=context_commentary,
                    blueprint=progress_blueprint,
                )
            )
        commentary_task: asyncio.Task | None = None
        if effective_lane == "slow" and on_commentary is not None:
            commentary_task = asyncio.create_task(
                self._slow_commentary_loop(
                    user_message,
                    reason=route.reason,
                    on_commentary=on_commentary,
                    on_progress=on_progress,
                    progress_blueprint=progress_blueprint,
                )
            )

        LOGGER.info("[Brain] Streaming message received. History=%s", len(history))
        try:
            reply = await self.invoke_messages_stream(
                messages,
                user_message,
                lane=effective_lane,
                source="chat",
                title="Main conversation",
                on_delta=on_delta,
            )
        finally:
            if commentary_task is not None:
                commentary_task.cancel()
                try:
                    await commentary_task
                except asyncio.CancelledError:
                    pass

        await self.persist_with_vector_memory(user_id, "user", user_message, conversation_id=conversation_id)
        await self.persist_with_vector_memory(user_id, "assistant", reply.text, conversation_id=conversation_id)
        # Fire-and-forget preference extraction (skip if message contains credentials)
        if self.preference_extractor and not should_skip_vectorization(user_message):
            self.preference_extractor.fire_and_forget(user_id, user_message, reply.text)
        # Title from first substantive exchange (skip greeting-only first turns).
        # Quick clip first; then await fast LLM so the stream "done" carries the final title.
        if self._should_generate_conversation_title(
            conversation_id, user_message, is_first_turn=is_first_turn
        ):
            quick = self._finalize_generated_title("", user_message)
            if quick:
                self.memory.update_conversation_title(conversation_id, quick)
            await self._refine_conversation_title_with_llm(
                conversation_id, user_message, reply.text
            )
        if conversation_id:
            reply.conversation_title = self.memory.get_conversation_title(conversation_id)
        reply.triage_reason = route.reason
        if effective_lane == "slow" and on_progress is not None:
            await on_progress(
                build_progress_snapshot(
                    user_message,
                    reason=route.reason,
                    lane=effective_lane,
                    stage="done",
                    summary="Process complete. Delivering the final response.",
                    blueprint=progress_blueprint,
                )
            )
        return reply

    async def _try_handle_heartbeat_intent(
        self,
        user_id: str,
        user_message: str,
        conversation_id: str | None = None,
    ) -> ConversationReply | None:
        """Intercepts chat turns that create or confirm heartbeat automations."""
        try:
            outcome = await self.heartbeat_intents.handle_message(user_id, user_message)
        except Exception as error:
            LOGGER.warning("[Heartbeats] Intent flow failed, falling back to chat: %s", error)
            return None

        if outcome is None:
            return None

        await self.persist_with_vector_memory(
            user_id,
            "user",
            user_message,
            conversation_id=conversation_id,
        )
        await self.persist_with_vector_memory(
            user_id,
            "assistant",
            outcome.response,
            conversation_id=conversation_id,
        )
        return ConversationReply(
            text=outcome.response,
            lane="fast",
            triage_reason="heartbeat-intent",
        )

    async def _slow_commentary_loop(
        self,
        user_message: str,
        *,
        reason: str,
        on_commentary: Callable[[str], Awaitable[None]],
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
        progress_blueprint: dict | None = None,
    ) -> None:
        """Emits lightweight feedback while the slow brain is still working."""
        updates = [
            "Still thinking this through to give you a thorough answer.",
            "Structuring the response and making sure the approach makes sense.",
            "Almost there. Closing the key points before responding.",
        ]
        index = 0
        while True:
            await asyncio.sleep(2.4)
            commentary = updates[index % len(updates)]
            await on_commentary(commentary)
            if on_progress is not None:
                stage = "thinking" if index < 2 else "finalizing"
                await on_progress(
                    build_progress_snapshot(
                        user_message,
                        reason=reason,
                        lane="slow",
                        stage=stage,
                        tick=index,
                        summary=commentary,
                        blueprint=progress_blueprint,
                    )
                )
            index += 1
