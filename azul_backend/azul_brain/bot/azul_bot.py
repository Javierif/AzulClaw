"""Main AzulClaw ActivityHandler."""

from botbuilder.core import ActivityHandler, MessageFactory, TurnContext
from botbuilder.schema import ChannelAccount

from ..channels.conversation_identity import resolve_channel_conversation_identity
from ..conversation import ConversationOrchestrator


class AzulBot(ActivityHandler):
    """Bot controller that delegates cognitive logic to the orchestrator."""

    def __init__(self, orchestrator: ConversationOrchestrator):
        """Initialises the bot with a reusable orchestrator."""
        self.orchestrator = orchestrator

    async def on_message_activity(self, turn_context: TurnContext):
        """Handles an incoming message and produces an agent response."""
        user_message = (turn_context.activity.text or "").strip()

        if not user_message:
            await turn_context.send_activity(
                MessageFactory.text("No text received in the message.")
            )
            return

        activity_payload = (
            turn_context.activity.serialize()
            if hasattr(turn_context.activity, "serialize")
            else {}
        )
        identity = resolve_channel_conversation_identity(
            activity_payload,
            self.orchestrator.memory,
        )
        reply = await self.orchestrator.process_user_message(
            identity.user_id,
            user_message,
            conversation_id=identity.conversation_id,
        )
        await turn_context.send_activity(MessageFactory.text(reply.text, reply.text))

    async def on_members_added_activity(
        self, members_added: list[ChannelAccount], turn_context: TurnContext
    ):
        """Sends a welcome message to new conversation members."""
        for member in members_added:
            if member.id != turn_context.activity.recipient.id:
                welcome_msg = (
                    "Hi. I'm AzulClaw. My brain is connected to Azure and "
                    "my hands to your secure local workspace."
                )
                await turn_context.send_activity(
                    MessageFactory.text(welcome_msg, welcome_msg)
                )
