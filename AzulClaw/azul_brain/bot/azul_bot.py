from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import ChannelAccount
import re

from ..cortex.kernel_setup import create_agent
from ..memory.safe_memory import SafeMemory
from ..soul.system_prompt import AZULCLAW_SYSTEM_PROMPT

def _extract_result_text(result) -> str:
    value = getattr(result, "value", None)
    if isinstance(value, str):
        return value
    return str(result)

def _build_history_block(history: list[dict]) -> str:
    if not history:
        return "Sin historial previo."

    lines: list[str] = []
    for message in history:
        role = message.get("role", "unknown")
        content = message.get("content", "")
        lines.append(f"[{role}] {content}")
    return "\n".join(lines)

class AzulBot(ActivityHandler):
    """
    Nucleo cognitivo de AzulClaw (El Cerebro).
    """

    def __init__(self, mcp_client):
        self.mcp_client = mcp_client
        self.kernel = None
        self.memory = SafeMemory.from_env()

    async def on_message_activity(self, turn_context: TurnContext):
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

        # Quick deterministic handling for simple greetings to avoid model
        # paraphrasing system instructions instead of replying to the greeting.
        def _is_greeting(text: str) -> bool:
            t = (text or "").lower().strip()
            if not t:
                return False
            # remove punctuation to normalize variants like "hola, AzulClaw"
            t = "".join(ch for ch in t if ch.isalnum() or ch.isspace())
            greetings = (
                "hola",
                "buenas",
                "buenos dias",
                "buenos días",
                "buenas tardes",
                "buenas noches",
                "hey",
                "hi",
            )
            return any(t.startswith(g) for g in greetings) or t in greetings

        if _is_greeting(user_message):
            reply_text = "Hola. ¿En qué puedo ayudarte?"
            # Persist messages
            self.memory.add_message(user_id, "user", user_message)
            self.memory.add_message(user_id, "assistant", reply_text)
            await turn_context.send_activity(MessageFactory.text(reply_text, reply_text))
            return

        # Build history BEFORE persisting the current user message so the model
        # does not receive the current message twice (avoids startup/priming artifacts).
        history = self.memory.get_history(user_id, limit=12)
        prompt = (
            f"{AZULCLAW_SYSTEM_PROMPT}\n\n"
            f"<CONVERSATION_HISTORY>\n{_build_history_block(history)}\n</CONVERSATION_HISTORY>\n\n"
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

        # Fallback: if the user input was a greeting but the LLM replied by
        # restating instructions or giving unrelated content, enforce a short greeting.
        try:
            if _is_greeting(user_message):
                low = (reply_text or "").lower()
                if not any(k in low for k in ("hola", "buen", "saludo", "hey")):
                    reply_text = "Hola. ¿En qué puedo ayudarte?"
        except Exception:
            # conservative: ignore fallback errors and keep original reply
            pass

        # Persist user + assistant messages after generating the reply to keep
        # history consistent and avoid duplications in prompts.
        self.memory.add_message(user_id, "user", user_message)
        self.memory.add_message(user_id, "assistant", reply_text)
        await turn_context.send_activity(MessageFactory.text(reply_text, reply_text))

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ):
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome_msg = (
                    "Hola. Soy AzulClaw. Mi cerebro esta conectado a Azure y "
                    "mis manos al workspace seguro local."
                )
                await turn_context.send_activity(
                    MessageFactory.text(welcome_msg, welcome_msg)
                )