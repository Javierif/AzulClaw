"""Agent invocation and message construction for the orchestrator.

Extracted from ``conversation.py`` as a mixin. Wraps the runtime manager's
message execution (buffered and streaming), the fast visible plan/commentary
helpers, the content-filter fallback, and the agent-message builder. Relies on
orchestrator state (``self.runtime_manager``) and on sibling mixin methods
resolved via the combined MRO.
"""

import logging
from collections.abc import Awaitable, Callable

from agent_framework import Content, Message

from .conversation_helpers import _strip_machine_pending_blocks
from .conversation_types import ConversationReply
from .cortex.fast.commentary import (
    build_commentary,
    build_progress_snapshot,
    normalize_fast_visible_commentary,
    normalize_fast_visible_plan,
    prompt_for_fast_visible_commentary,
    prompt_for_fast_visible_plan,
)

LOGGER = logging.getLogger(__name__)


class InferenceMixin:
    """Agent invocation, fast-visible narration, and message construction."""

    async def invoke_messages(
        self,
        messages: list[Message],
        user_message: str,
        *,
        lane: str,
        source: str,
        title: str,
        tools_enabled: bool = True,
    ) -> ConversationReply:
        """Invokes the agent with structured messages and falls back on content filter errors."""
        try:
            result = await self.runtime_manager.execute_messages(
                messages=messages,
                lane=lane,
                title=title,
                source=source,
                kind="agent-run",
                tools_enabled=tools_enabled,
            )
            return ConversationReply(
                text=result.text,
                model_id=result.model.id if result.model else "",
                model_label=result.model.label if result.model else "",
                process_id=result.process_id,
                attempt_count=result.attempt_count,
                skipped_models=result.skipped_models,
                failed_attempts=result.failed_attempts,
                lane=lane,
                turn_status="final_answer",
            )
        except Exception as error:
            error_text = str(error)
            if "content_filter" in error_text or "ResponsibleAIPolicyViolation" in error_text:
                fallback = self._fallback_for_filtered_prompt(user_message)
                if fallback:
                    LOGGER.warning("[Brain] Azure filtered the prompt. Using local fallback.")
                    return ConversationReply(text=fallback, lane=lane, turn_status="final_answer")
            return ConversationReply(
                text=(
                    "Could not execute the cognitive layer yet. "
                    "Check Azure OpenAI configuration and Microsoft Entra authentication.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,
                turn_status="tool_failure",
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
        tools_enabled: bool = True,
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
                tools_enabled=tools_enabled,
            )
            return ConversationReply(
                text=result.text,
                model_id=result.model.id if result.model else "",
                model_label=result.model.label if result.model else "",
                process_id=result.process_id,
                attempt_count=result.attempt_count,
                skipped_models=result.skipped_models,
                failed_attempts=result.failed_attempts,
                lane=lane,
                turn_status="final_answer",
            )
        except Exception as error:
            error_text = str(error)
            if "content_filter" in error_text or "ResponsibleAIPolicyViolation" in error_text:
                fallback = self._fallback_for_filtered_prompt(user_message)
                if fallback:
                    LOGGER.warning("[Brain] Azure filtered the prompt. Using local fallback.")
                    await on_delta(fallback)
                    return ConversationReply(text=fallback, lane=lane, turn_status="final_answer")
            return ConversationReply(
                text=(
                    "Could not execute the cognitive layer yet. "
                    "Check Azure OpenAI configuration and Microsoft Entra authentication.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,
                turn_status="tool_failure",
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
        """Returns a neutral fallback when Azure filters a prompt.

        Detection is intentionally not keyword-based. Matching greeting word
        lists only works for the languages hard-coded here and silently breaks
        for every other language, so we never branch on natural-language
        literals. Because the model is unavailable on the content-filter path
        we cannot run a semantic check either, so we fall back to a single
        neutral, language-independent acknowledgement.
        """
        if not (user_message or "").strip():
            return None
        return "No he podido procesar tu mensaje en este momento. ¿Puedes reformularlo?"

    def build_agent_messages(
        self,
        history: list[dict],
        semantic_memories: list[dict],
        user_message: str,
        user_knowledge: list[dict] | None = None,
        document_context: str = "",
        visual_contents: list[Content] | None = None,
        confirmed_sensitive_action: bool = False,
        extra_system_messages: list[str] | None = None,
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

        if confirmed_sensitive_action:
            messages.append(
                Message(
                    role="system",
                    contents=(
                        "The user is explicitly confirming a previously proposed sensitive action. "
                        "Do not ask for confirmation again unless the scope has changed. "
                        "Execute the relevant tool now and report the concrete result. "
                        "Never claim a filesystem action was executed unless a tool result confirms it. "
                        "If the previous plan needs to be rebuilt, do so and then execute it in the same turn."
                    ),
                )
            )

        for system_message in extra_system_messages or []:
            text = str(system_message or "").strip()
            if text:
                messages.append(Message(role="system", contents=text))

        for item in history:
            role = item.get("role", "user")
            if role not in {"user", "assistant"}:
                continue
            content = _strip_machine_pending_blocks(str(item.get("content", "")).strip())
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

        if document_context.strip():
            messages.append(
                Message(
                    role="assistant",
                    contents="Document context for this conversation:\n" + document_context.strip(),
                )
            )

        user_contents: list[Content] = [Content.from_text(user_message)]
        if visual_contents:
            user_contents.extend(visual_contents)
        messages.append(Message(role="user", contents=user_contents))
        return messages
