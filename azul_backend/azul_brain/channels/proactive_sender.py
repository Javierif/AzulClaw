"""Proactive messaging for Bot Framework."""

from botbuilder.core import BotFrameworkAdapter, TurnContext
from botbuilder.schema import Activity, TextFormatTypes


async def send_proactive_reply(adapter: BotFrameworkAdapter, original_activity: dict, text: str) -> None:
    """Uses a ConversationReference to push a message back to the channel asynchronously."""
    # Deserialize the dictionary into a BotBuilder schema Activity object
    activity_obj = Activity().deserialize(original_activity)
    # Extract the officially formatted conversation reference
    reference = TurnContext.get_conversation_reference(activity_obj)

    async def _send_reply(turn_context: TurnContext) -> object:
        activity = Activity(
            type="message",
            text=text,
            text_format=TextFormatTypes.plain,
            speak=text,  # Important for Voice Channels like Alexa
            reply_to_id=original_activity.get("id"),
        )
        return await turn_context.send_activity(activity)

    app_id = adapter.settings.app_id if adapter.settings else ""
    await adapter.continue_conversation(reference, _send_reply, app_id)
