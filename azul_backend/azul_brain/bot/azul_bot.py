"""ActivityHandler principal de AzulClaw."""

from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import ChannelAccount

from ..conversation import ConversationOrchestrator


class AzulBot(ActivityHandler):
    """Controlador del bot que delega la logica cognitiva al orquestador."""

    def __init__(self, orchestrator: ConversationOrchestrator):
        """Inicializa el bot con un orquestador reutilizable."""
        self.orchestrator = orchestrator

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

        reply_text = await self.orchestrator.process_user_message(user_id, user_message)
        await turn_context.send_activity(MessageFactory.text(reply_text, reply_text))

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ):
        """Envia mensaje de bienvenida a nuevos miembros de la conversacion."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome_msg = (
                    "Hola. Soy AzulClaw. Mi cerebro esta conectado a Azure y "
                    "mis manos al workspace seguro local."
                )
                await turn_context.send_activity(
                    MessageFactory.text(welcome_msg, welcome_msg)
                )
