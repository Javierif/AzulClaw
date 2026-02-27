"""ActivityHandler principal de AzulClaw con memoria híbrida y Agent Framework."""

from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import ChannelAccount

from ..cortex.kernel_setup import create_agent
from ..memory.embedding_service import EmbeddingService
from ..memory.safe_memory import SafeMemory
from ..memory.vector_store import VectorMemoryStore
from ..soul.system_prompt import AZULCLAW_SYSTEM_PROMPT


def _extract_result_text(result) -> str:
    """Normaliza la respuesta del adapter de agente a texto serializable."""
    value = getattr(result, "value", None)
    if isinstance(value, str):
        return value
    return str(result)


def _build_history_block(history: list[dict]) -> str:
    """Serializa historial conversacional en un bloque compacto para prompt."""
    if not history:
        return "Sin historial previo."

    lines: list[str] = []
    for message in history:
        role = message.get("role", "unknown")
        content = message.get("content", "")
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)


def _build_retrieval_block(memories: list[dict]) -> str:
    """Serializa recuerdos semánticos recuperados para contexto del agente."""
    if not memories:
        return "Sin recuerdos semanticos relevantes."

    lines: list[str] = []
    for memory in memories:
        role = memory.get("role", "unknown")
        source = memory.get("source", "chat")
        content = memory.get("content", "")
        similarity = memory.get("similarity", 0.0)
        lines.append(f"[{role} | source={source} | sim={similarity:.3f}] {content}")
    return "\n".join(lines)


def _should_skip_vectorization(text: str) -> bool:
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


class AzulBot(ActivityHandler):
    """Controlador del bot: orquesta contexto, inferencia, tools y persistencia."""

    def __init__(self, mcp_client):
        """Inicializa cliente MCP, memorias y stack opcional de embeddings."""
        self.mcp_client = mcp_client
        self.kernel = None
        self.memory = SafeMemory.from_env()

        # La memoria vectorial es opcional: si falla, el bot sigue operativo.
        self.embedding_service = None
        self.vector_memory = None
        try:
            self.embedding_service = EmbeddingService.from_env()
            self.vector_memory = VectorMemoryStore.from_env()
            print("[Memory] Vector memory habilitada.")
        except Exception as error:
            print(f"[Memory] Vector memory deshabilitada: {error}")

    async def _persist_with_vector_memory(self, user_id: str, role: str, content: str) -> None:
        """Persiste en memoria corta y, si procede, indexa en memoria vectorial."""
        self.memory.add_message(user_id, role, content)

        if (
            self.embedding_service is None
            or self.vector_memory is None
            or _should_skip_vectorization(content)
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
            # La indexación nunca debe bloquear el flujo principal del bot.
            print(f"[Memory] Error indexando memoria vectorial: {error}")

    async def _retrieve_semantic_memories(self, user_id: str, query_text: str) -> list[dict]:
        """Recupera recuerdos semánticos relevantes para enriquecer el prompt."""
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
            print(f"[Memory] Error recuperando memoria vectorial: {error}")
            return []

    async def on_message_activity(self, turn_context: TurnContext):
        """Gestiona un mensaje entrante y produce una respuesta del agente."""
        user_message = (turn_context.activity.text or "").strip()
        user_id = (
            turn_context.activity.from_property.id
            if turn_context.activity.from_property
            else "anonymous"
        )

        if not user_message:
            await turn_context.send_activity(
                MessageFactory.text("No recibi texto en el mensaje.")
            )
            return

        # El contexto se obtiene antes de guardar el mensaje actual para evitar sesgo recursivo.
        history = self.memory.get_history(user_id, limit=12)
        semantic_memories = await self._retrieve_semantic_memories(user_id, user_message)

        prompt = (
            f"{AZULCLAW_SYSTEM_PROMPT}\n\n"
            f"<CONVERSATION_HISTORY>\n{_build_history_block(history)}\n</CONVERSATION_HISTORY>\n\n"
            f"<SEMANTIC_MEMORY>\n{_build_retrieval_block(semantic_memories)}\n</SEMANTIC_MEMORY>\n\n"
            f"<USER_MESSAGE>\n{user_message}\n</USER_MESSAGE>"
        )

        print(
            "[Brain] Mensaje recibido. Preparando razonamiento con MCP Client conectado: "
            f"{self.mcp_client is not None}"
        )

        try:
            if self.kernel is None:
                self.kernel = await create_agent(self.mcp_client)
            result = await self.kernel.invoke_prompt(prompt)
            reply_text = _extract_result_text(result)
        except Exception as error:
            reply_text = (
                "No pude ejecutar la capa cognitiva aun. "
                "Verifica dependencias y variables AZURE_OPENAI_*.\n"
                f"Detalle tecnico: {error}"
            )

        await self._persist_with_vector_memory(user_id, "user", user_message)
        await self._persist_with_vector_memory(user_id, "assistant", reply_text)
        await turn_context.send_activity(MessageFactory.text(reply_text, reply_text))

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ):
        """Envía mensaje de bienvenida a nuevos miembros de la conversación."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome_msg = (
                    "Hola. Soy AzulClaw. Mi cerebro esta conectado a Azure y "
                    "mis manos al workspace seguro local."
                )
                await turn_context.send_activity(
                    MessageFactory.text(welcome_msg, welcome_msg)
                )