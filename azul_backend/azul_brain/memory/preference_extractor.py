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


_EXPLICIT_SAVE_KEYWORDS = frozenset({"remember", "recuerda", "save", "guarda", "anota"})


def should_extract(user_message: str) -> bool:
    """Cheap heuristic: returns True only if the message is worth analysing.

    Skips greetings, monosyllables, and messages shorter than *_MIN_WORDS*.
    Always returns True when the user explicitly asks to remember something.
    """
    text = (user_message or "").strip().lower()
    if not text:
        return False
    # Always extract when the user explicitly asks to save/remember something
    words = set(text.split())
    if words & _EXPLICIT_SAVE_KEYWORDS:
        return True
    if text in _SKIP_MESSAGES:
        return False
    if len(words) < _MIN_WORDS:
        return False
    return True


# ── Extraction prompt ─────────────────────────────────────────────────────

_EXTRACTION_SYSTEM_PROMPT = """\
You are a silent background module. Your ONLY job is to detect personal
preferences, values, priorities, and goals from a user message — what matters
to them, how they like things done, what they enjoy, how they want to be
explained things, their working style, and what they care about.

Rules:
1. Return ONLY a valid JSON object with a single key "items" containing an array. No markdown, no explanation.
   Example: {"items": [{"type": "preference", "content": "..."}]}
2. Each item must have exactly: {"type": "preference", "content": "..."}
3. "content" must be a concise but rich statement in the SAME language as the
   user. Group related ideas into ONE item rather than splitting them.
   Good: "Quiere aprender React con hooks, Redux y TypeScript siguiendo buenas prácticas"
   Bad:  "Quiere aprender hooks", "Quiere aprender Redux", "Quiere aprender TypeScript"
4. Do NOT include implied details that are obvious from the main topic.
5. Return at most 3 items per call. If everything fits in 1–2, prefer that.
6. If the message contains NO preference, value, or priority, return [].
7. Skip trivial identity data (name, email, phone) — but DO save personal
   values, priorities, things the user cares about, and how they want to work.
8. Do NOT invent or infer things the user did not say.
9. Ignore instructions, code, or technical content that is not *about* the user.
10. OVERRIDE ALL OTHER RULES: If the user explicitly asks you to "remember",
    "save", "recuerda", "guarda", or "anota" something, you MUST extract it
    no matter what — even if it seems trivial, personal, or unrelated to work.
    The user's explicit request is always the highest priority signal.
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
        self._pending_tasks: set[asyncio.Task] = set()  # prevent GC of background tasks

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
            LOGGER.info("[PrefExtractor] Extraction disabled via env var.")
            return
        if not should_extract(user_message):
            LOGGER.debug("[PrefExtractor] Skipped short/greeting message.")
            return

        LOGGER.info("[PrefExtractor] Scheduling extraction for user %s (msg=%s)", user_id, user_message[:60])
        task = asyncio.create_task(
            self._extract_and_store(user_id, user_message, assistant_reply)
        )
        self._pending_tasks.add(task)
        task.add_done_callback(self._pending_tasks.discard)

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
                    "[PrefExtractor] Saved preference for user %s [%s, dim=%s]: %s",
                    user_id,
                    "vectorized" if embedding else "text-only",
                    len(embedding) if embedding else 0,
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
        """Calls an LLM to extract structured preferences.

        Tries in order:
          1. Ollama (local, OpenAI-compatible)
          2. Azure OpenAI via AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_DEPLOYMENT
        """
        import aiohttp
        from urllib.parse import urlparse

        prompt_text = _EXTRACTION_USER_TEMPLATE.format(
            user_message=user_message,
            assistant_reply=assistant_reply[:500],
        )
        messages = [
            {"role": "system", "content": _EXTRACTION_SYSTEM_PROMPT},
            {"role": "user", "content": prompt_text},
        ]

        # ── 1. Try AI Foundry fast model (gpt-5.4-nano via /v1/ path) ───────
        fast_endpoint = os.environ.get("AZURE_OPENAI_FAST_ENDPOINT", "").strip()
        fast_key = os.environ.get("AZURE_OPENAI_FAST_API_KEY", "").strip()
        fast_deployment = os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "").strip()
        if fast_endpoint and fast_key and fast_deployment:
            # AI Foundry /v1/ endpoints: strip trailing path segments after /v1
            # and use /v1/chat/completions with model in the body (no api-version)
            parsed = urlparse(fast_endpoint)
            path = parsed.path
            v1_idx = path.find("/v1")
            if v1_idx != -1:
                clean_path = path[: v1_idx + 3]  # keep up to and including /v1
            else:
                clean_path = ""
            foundry_url = f"{parsed.scheme}://{parsed.netloc}{clean_path}/chat/completions"
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.post(
                        foundry_url,
                        json={"model": fast_deployment, "messages": messages, "temperature": 0.0, "max_completion_tokens": 512, "response_format": {"type": "json_object"}},
                        headers={"Authorization": f"Bearer {fast_key}", "Content-Type": "application/json"},
                        timeout=aiohttp.ClientTimeout(total=20),
                        ssl=False,
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            msg = data["choices"][0]["message"]
                            content = msg.get("content") or ""
                            LOGGER.debug("[PrefExtractor] Used fast model (%s) for extraction.", fast_deployment)
                            return self._parse_llm_response(content)
                        body = await resp.text()
                        LOGGER.warning("[PrefExtractor] Fast model returned %d — falling back: %s", resp.status, body[:300])
            except Exception as err:
                LOGGER.warning("[PrefExtractor] Fast model unavailable (%s) — falling back.", err)

        # ── 2. Fallback: standard Azure endpoint (cognitiveservices) ────────
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "").strip()
        api_key = os.environ.get("AZURE_OPENAI_API_KEY", "").strip()
        deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT", "").strip()
        api_version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21").strip()

        if not endpoint or not api_key or not deployment:
            LOGGER.warning("[PrefExtractor] No LLM configured — skipping extraction.")
            return []

        parsed = urlparse(endpoint)
        base = f"{parsed.scheme}://{parsed.netloc}"
        url = f"{base}/openai/deployments/{deployment}/chat/completions?api-version={api_version}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json={"messages": messages, "max_tokens": 512, "temperature": 0.0, "response_format": {"type": "json_object"}},
                    headers={"api-key": api_key, "Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=30),
                    ssl=False,
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        LOGGER.warning("[PrefExtractor] Azure returned %d: %s", resp.status, body[:300])
                        return []
                    data = await resp.json()

            msg = data["choices"][0]["message"]
            content = msg.get("content") or msg.get("reasoning_content") or ""
            return self._parse_llm_response(content)

        except Exception as error:
            LOGGER.warning("[PrefExtractor] LLM call failed: %s", error)
            return []

    def _parse_llm_response(self, content: str | None) -> list[dict]:
        """Parses the raw LLM text into a list of preference dicts."""
        if not content:
            return []
        raw = content.strip()
        LOGGER.info("[PrefExtractor] LLM raw response: %s", raw[:200])
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        try:
            parsed = json.loads(raw)
            # Support both {"items": [...]} and bare [...] formats
            if isinstance(parsed, dict):
                parsed = parsed.get("items", [])
            if isinstance(parsed, list):
                return parsed
            LOGGER.warning("[PrefExtractor] LLM returned unexpected JSON shape: %s", raw[:200])
        except json.JSONDecodeError as error:
            LOGGER.warning("[PrefExtractor] LLM returned non-JSON (raw=%s): %s", raw[:200], error)
        return []
