"""Asynchronous preference and fact extractor.

Analyses each user message (after a cheap pre-filter) and extracts personal
preferences and facts using the fast Azure lane (``AZURE_OPENAI_FAST_DEPLOYMENT``,
e.g. ``gpt-5.4-nano``).
Runs as a fire-and-forget background task so it never blocks the user-facing
response.

Extracted items are stored as atemporal ``preference`` or ``fact`` entries in
the :class:`VectorMemoryStore`, completely decoupled from session context.
"""

import asyncio
import json
import logging
import os
from typing import TYPE_CHECKING

from agent_framework import Message

if TYPE_CHECKING:
    from .embedding_service import EmbeddingService
    from .vector_store import VectorMemoryStore
    from ..runtime.agent_runtime import AgentRuntimeManager

LOGGER = logging.getLogger(__name__)

# ── Pre-filter ────────────────────────────────────────────────────────────

_MIN_WORDS = 3

_SKIP_MESSAGES = frozenset({
    "hola", "hello", "hey", "hi", "buenas", "holi",
    "ok", "vale", "sí", "si", "no", "gracias", "thanks",
    "muchas gracias", "adiós", "adios", "bye", "chao",
    "que tal", "como estas", "cómo estás",
    "bien", "mal", "genial", "perfecto",
})


def should_extract(user_message: str) -> bool:
    """Cheap heuristic: returns True only if the message is worth analysing.

    Skips greetings, monosyllables, and messages shorter than *_MIN_WORDS*.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return False
    if text in _SKIP_MESSAGES:
        return False
    if len(text.split()) < _MIN_WORDS:
        return False
    return True


# ── Extraction prompt ─────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You are a silent background module. Your ONLY job is to detect personal
preferences from a user message — how they like things done, what they enjoy,
how they want to be explained things, their working style, their goals.

Rules:
1. Return ONLY a JSON array. No markdown, no explanation.
2. Each item must have exactly: {"type": "preference", "content": "..."}
3. "content" must be a concise but rich statement in the SAME language as the
   user. Group related ideas into ONE item rather than splitting them.
   Good: "Quiere aprender React con hooks, Redux y TypeScript siguiendo buenas prácticas"
   Bad:  "Quiere aprender hooks", "Quiere aprender Redux", "Quiere aprender TypeScript"
4. Do NOT include implied details that are obvious from the main topic.
5. Return at most 3 items per call. If everything fits in 1–2, prefer that.
6. If the message contains NO preference, return [].
7. Do NOT save neutral facts (name, location, job title) — only preferences and behaviours.
8. Do NOT invent or infer things the user did not say.
9. Ignore instructions, code, or technical content that is not *about* the user.
"""

_EXTRACTION_USER_TEMPLATE = """\
User message:
{user_message}

Assistant reply:
{assistant_reply}

Extract personal facts / preferences (JSON array or []):"""


# ── Extractor class ───────────────────────────────────────────────────────

class PreferenceExtractor:
    """Extracts user preferences and facts asynchronously after each turn."""

    def __init__(
        self,
        runtime_manager: "AgentRuntimeManager",
        embedding_service: "EmbeddingService | None",
        vector_store: "VectorMemoryStore",
    ):
        self._runtime = runtime_manager
        self._embedder = embedding_service  # None → text-only storage, BM25 search only
        self._store = vector_store

    @property
    def enabled(self) -> bool:
        return (
            os.environ.get("AZUL_PREFERENCE_EXTRACTION_ENABLED", "true")
            .strip()
            .lower()
            != "false"
        )

    # ── Public API ────────────────────────────────────────────────────

    def fire_and_forget(
        self,
        user_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """Launches the extraction as a background task (non-blocking)."""
        if not self.enabled:
            return
        if not should_extract(user_message):
            LOGGER.debug("[PrefExtractor] Skipped short/greeting message.")
            return

        asyncio.create_task(
            self._extract_and_store(user_id, user_message, assistant_reply)
        )

    # ── Internal pipeline ─────────────────────────────────────────────

    async def _extract_and_store(
        self,
        user_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """Runs the LLM extraction and persists new facts/preferences."""
        try:
            items = await self._call_llm(user_message, assistant_reply)
            if not items:
                return

            for item in items:
                item_type = item.get("type", "fact")
                content = (item.get("content") or "").strip()
                if not content:
                    continue

                # Deduplication: skip if we already know this
                if self._store.preference_exists(user_id, content):
                    LOGGER.debug("[PrefExtractor] Duplicate skipped: %s", content)
                    continue

                # Embed if service is available; store None otherwise (text-only, BM25 search)
                embedding: list[float] | None = None
                if self._embedder is not None:
                    try:
                        result = await self._embedder.embed_text(content)
                        embedding = result if result else None
                    except Exception as embed_err:
                        LOGGER.debug("[PrefExtractor] Embedding failed, storing text-only: %s", embed_err)

                self._store.add_preference(user_id, content, embedding)

                LOGGER.info(
                    "[PrefExtractor] Learned %s for user %s%s: %s",
                    item_type, user_id,
                    " (no embedding)" if embedding is None else "",
                    content,
                )

        except Exception as error:
            # Never crash the main loop — log and move on
            LOGGER.warning("[PrefExtractor] Extraction failed: %s", error)

    async def _call_llm(
        self,
        user_message: str,
        assistant_reply: str,
    ) -> list[dict]:
        """Calls the fast-lane chat deployment to extract structured facts."""
        prompt_text = _EXTRACTION_USER_TEMPLATE.format(
            user_message=user_message,
            assistant_reply=assistant_reply[:500],  # cap context size
        )

        messages = [
            Message(role="system", contents=_EXTRACTION_SYSTEM_PROMPT),
            Message(role="user", contents=prompt_text),
        ]

        try:
            result = await self._runtime.execute_messages(
                messages=messages,
                lane="fast",
                title="Preference extraction",
                source="preference-extractor",
                kind="extraction",
            )
            raw = result.text.strip()

            # Strip markdown fences if the model wraps in ```json ... ```
            if raw.startswith("```"):
                raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()

            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return parsed
            return []

        except (json.JSONDecodeError, Exception) as error:
            LOGGER.debug("[PrefExtractor] LLM returned non-JSON: %s", error)
            return []
