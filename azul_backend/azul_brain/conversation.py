"""Servicios de conversacion reutilizables para bot y desktop API."""

import logging

from agent_framework import Message

from .cortex.kernel_setup import create_agent
from .memory.embedding_service import EmbeddingService
from .memory.safe_memory import SafeMemory
from .memory.vector_store import VectorMemoryStore

LOGGER = logging.getLogger(__name__)


def extract_result_text(result) -> str:
    """Normaliza la respuesta del adapter de agente a texto serializable."""
    value = getattr(result, "value", None)
    if isinstance(value, str):
        return value
    return str(result)


def should_skip_vectorization(text: str) -> bool:
    """Evita indexar texto potencialmente sensible en memoria vectorial local."""
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
    """Orquesta memoria, recuperacion semantica e invocacion del agente."""

    def __init__(self, mcp_client):
        self.mcp_client = mcp_client
        self.kernel = None
        self.memory = SafeMemory.from_env()

        self.embedding_service = None
        self.vector_memory = None
        try:
            self.embedding_service = EmbeddingService.from_env()
            self.vector_memory = VectorMemoryStore.from_env()
            LOGGER.info("[Memory] Vector memory habilitada.")
        except Exception as error:
            LOGGER.warning("[Memory] Vector memory deshabilitada: %s", error)

    async def persist_with_vector_memory(self, user_id: str, role: str, content: str) -> None:
        """Persiste en memoria corta y, si procede, indexa en memoria vectorial."""
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
            LOGGER.warning("[Memory] Error indexando memoria vectorial: %s", error)

    async def retrieve_semantic_memories(self, user_id: str, query_text: str) -> list[dict]:
        """Recupera recuerdos semanticos relevantes para enriquecer el prompt."""
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
            LOGGER.warning("[Memory] Error recuperando memoria vectorial: %s", error)
            return []

    async def invoke_messages(self, messages: list[Message], user_message: str) -> str:
        """Invoca el agente con mensajes estructurados y fallback ante filtros."""
        try:
            if self.kernel is None:
                self.kernel = await create_agent(self.mcp_client)
            result = await self.kernel.invoke_messages(messages)
            return extract_result_text(result)
        except Exception as error:
            error_text = str(error)
            if "content_filter" in error_text or "ResponsibleAIPolicyViolation" in error_text:
                fallback = self._fallback_for_filtered_prompt(user_message)
                if fallback:
                    LOGGER.warning("[Brain] Azure filtro el prompt. Usando fallback local.")
                    return fallback
            return (
                "No pude ejecutar la capa cognitiva aun. "
                "Verifica dependencias y variables AZURE_OPENAI_*.\n"
                f"Detalle tecnico: {error}"
            )

    def _fallback_for_filtered_prompt(self, user_message: str) -> str | None:
        """Devuelve una respuesta segura si Azure filtra una peticion simple."""
        normalized = (user_message or "").strip().lower()
        if normalized in {"hola", "buenas", "hey", "hello", "holi"}:
            return "Hola. Estoy activo y listo para ayudarte."
        if normalized in {"gracias", "muchas gracias"}:
            return "De nada."
        if normalized in {"que tal", "como estas", "cómo estás"}:
            return "Estoy operativo y listo para trabajar contigo."
        return None

    def build_agent_messages(
        self,
        history: list[dict],
        semantic_memories: list[dict],
        user_message: str,
    ) -> list[Message]:
        """Convierte historial y contexto en mensajes reales para el framework."""
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
                        text="Contexto recuperado para esta conversacion:\n" + "\n".join(memory_lines),
                    )
                )

        messages.append(Message(role="user", text=user_message))
        return messages

    async def process_user_message(self, user_id: str, user_message: str) -> str:
        """Construye contexto, ejecuta inferencia y persiste conversacion."""
        history = self.memory.get_history(user_id, limit=12)
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        messages = self.build_agent_messages(history, semantic_memories, user_message)

        LOGGER.info("[Brain] Mensaje recibido. Historial=%s", len(history))
        reply_text = await self.invoke_messages(messages, user_message)

        await self.persist_with_vector_memory(user_id, "user", user_message)
        await self.persist_with_vector_memory(user_id, "assistant", reply_text)

        return reply_text
