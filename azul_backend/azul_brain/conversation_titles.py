"""Conversation sidebar title generation for the orchestrator.

Extracted from ``conversation.py`` as a mixin. Relies on the orchestrator's
``self.memory`` and ``self.runtime_manager``; carries no state of its own.
"""

import logging
import os

from agent_framework import Message

from .conversation_helpers import _is_placeholder_conversation_title

LOGGER = logging.getLogger(__name__)


class TitleMixin:
    """Decides when to title a thread and refines the title via the fast lane."""

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
        if is_first_turn:
            return True
        current = self.memory.get_conversation_title(conversation_id)
        return _is_placeholder_conversation_title(current)

    def _finalize_generated_title(self, title: str, source_message: str) -> str:
        """Falls back to the user's message only when the model returned nothing usable."""
        cleaned = (title or "").strip().strip('"').strip()
        if cleaned:
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
            ans = ans[:1200] + "â€¦"

        title_prompt = (
            "You name chat threads for a sidebar list.\n\n"
            f"User asked:\n\"\"\"{user_message[:500]}\"\"\"\n\n"
            f"Assistant answered (excerpt):\n\"\"\"{ans}\"\"\"\n\n"
            "Write ONE short title (4â€“7 words) summarizing the topic or outcome of this exchange. "
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
