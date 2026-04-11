"""Reusable conversation services for the bot and desktop API."""

import asyncio
import logging
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
from .memory.safe_memory import SafeMemory
from .memory.vector_store import VectorMemoryStore
from .runtime.agent_runtime import AgentRuntimeManager

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


def extract_result_text(result) -> str:
    """Normalises the agent adapter response to serialisable text."""
    value = getattr(result, "value", None)
    if isinstance(value, str):
        return value
    return str(result)


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
        self.memory = SafeMemory.from_env()

        self.embedding_service = None
        self.vector_memory = None
        try:
            self.embedding_service = EmbeddingService.from_env()
            self.vector_memory = VectorMemoryStore.from_env()
            LOGGER.info("[Memory] Vector memory enabled.")
        except Exception as error:
            LOGGER.warning("[Memory] Vector memory disabled: %s", error)

    async def persist_with_vector_memory(self, user_id: str, role: str, content: str) -> None:
        """Persists to short-term memory and, if applicable, indexes in vector memory."""
        self.memory.add_message(user_id, role, content)

        if (
            self.embedding_service is None
            or self.vector_memory is None
            or should_skip_vectorization(content)
        ):
            return

        try:
            embedding = await self.embedding_service.embed_text(content)
            if embedding:
                self.vector_memory.add_memory(
                    user_id=user_id,
                    role=role,
                    content=content,
                    embedding=embedding,
                    source="chat",
                )
        except Exception as error:
            LOGGER.warning("[Memory] Error indexing vector memory: %s", error)

    async def retrieve_semantic_memories(self, user_id: str, query_text: str) -> list[dict]:
        """Retrieves relevant semantic memories to enrich the prompt."""
        if self.embedding_service is None or self.vector_memory is None:
            return []

        try:
            query_embedding = await self.embedding_service.embed_text(query_text)
            if not query_embedding:
                return []
            return self.vector_memory.search_similar(
                user_id=user_id,
                query_embedding=query_embedding,
                limit=5,
                min_similarity=0.28,
                candidate_pool=150,
            )
        except Exception as error:
            LOGGER.warning("[Memory] Error retrieving vector memory: %s", error)
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
                    "Verifica dependencias y variables AZURE_OPENAI_*.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,

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
                    "Verifica dependencias y variables AZURE_OPENAI_*.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,
            )

    async def generate_fast_visible_plan(self, user_message: str, *, reason: str) -> tuple[str, dict]:
        """Asks the fast brain for the first visible narration and a summarised plan."""
        prompt_messages = [
            Message(role=item["role"], text=item["text"])
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
            Message(role=item["role"], text=item["text"])
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
    ) -> list[Message]:
        """Converts history and context into real messages for the framework."""
        messages: list[Message] = []

        for item in history:
            role = item.get("role", "user")
            if role not in {"user", "assistant"}:
                continue
            content = str(item.get("content", "")).strip()
            if content:
                messages.append(Message(role=role, text=content))

        if semantic_memories:
            memory_lines: list[str] = []
            for memory in semantic_memories:
                content = str(memory.get("content", "")).strip()
                if not content:
                    continue
                source = str(memory.get("source", "chat"))
                similarity = float(memory.get("similarity", 0.0))
                memory_lines.append(f"- ({source}, sim={similarity:.2f}) {content}")

            if memory_lines:
                messages.append(
                    Message(
                        role="assistant",
                        text="Retrieved context for this conversation:\n" + "\n".join(memory_lines),
                    )
                )

        messages.append(Message(role="user", text=user_message))
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
        messages = self.build_agent_messages(history, semantic_memories, user_message)

        LOGGER.info("[Brain] Message received. History=%s", len(history))
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

        return reply.text

    async def process_user_message(self, user_id: str, user_message: str, lane: str = "auto") -> ConversationReply:
        """Builds context, runs inference, and persists the conversation."""
        route = self.resolve_route(user_message, lane)
        effective_lane = route.lane
        history = self.memory.get_history(user_id, limit=12)
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        messages = self.build_agent_messages(history, semantic_memories, user_message)

        LOGGER.info("[Brain] Message received. History=%s", len(history))
        reply = await self.invoke_messages(
            messages,
            user_message,
            lane=effective_lane,
            source="chat",
            title="Main conversation",
        )

        await self.persist_with_vector_memory(user_id, "user", user_message)
        await self.persist_with_vector_memory(user_id, "assistant", reply.text)
        reply.triage_reason = route.reason
        return reply

    async def process_user_message_stream(
        self,
        user_id: str,
        user_message: str,
        *,
        lane: str = "auto",
        on_delta: Callable[[str], Awaitable[None]],
        on_commentary: Callable[[str], Awaitable[None]] | None = None,
        on_progress: Callable[[dict], Awaitable[None]] | None = None,
    ) -> ConversationReply:
        """Builds context, runs inference, and emits streaming if applicable."""
        route = self.resolve_route(user_message, lane)
        effective_lane = route.lane
        progress_blueprint: dict | None = None
        if effective_lane == "slow":
            initial_commentary, progress_blueprint = await self.generate_fast_visible_plan(
                user_message,
                reason=route.reason,
            )
        else:
            initial_commentary = await self.generate_fast_visible_commentary(
                user_message,
                reason=route.reason,
                lane=effective_lane,
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
        history = self.memory.get_history(user_id, limit=12)
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        messages = self.build_agent_messages(history, semantic_memories, user_message)
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

        await self.persist_with_vector_memory(user_id, "user", user_message)
        await self.persist_with_vector_memory(user_id, "assistant", reply.text)
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
