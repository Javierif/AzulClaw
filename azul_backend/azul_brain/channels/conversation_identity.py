"""Conversation identity helpers for Bot Framework channel activities."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Mapping

from .access_control import extract_channel_principal_ids


_MAX_TITLE_PART_LENGTH = 80


@dataclass(frozen=True)
class ChannelConversationIdentity:
    """Stable local identity for an external channel conversation."""

    user_id: str
    conversation_id: str
    title: str
    channel_id: str
    channel_user_id: str
    channel_chat_id: str


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _clean_title_part(value: str) -> str:
    cleaned = re.sub(r"\s+", " ", _stringify(value))
    if len(cleaned) <= _MAX_TITLE_PART_LENGTH:
        return cleaned
    return cleaned[: _MAX_TITLE_PART_LENGTH - 3].rstrip() + "..."


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _telegram_chat_label(activity: Mapping[str, Any]) -> str:
    channel_data = _mapping(activity.get("channelData"))
    message = _mapping(channel_data.get("message"))
    chat = _mapping(channel_data.get("chat")) or _mapping(message.get("chat"))
    if not chat:
        return ""
    for key in ("title", "username", "first_name"):
        value = _clean_title_part(_stringify(chat.get(key)))
        if value:
            return value
    first_name = _stringify(chat.get("first_name"))
    last_name = _stringify(chat.get("last_name"))
    return _clean_title_part(f"{first_name} {last_name}")


def _display_channel_name(channel_id: str) -> str:
    if channel_id == "telegram":
        return "Telegram"
    if not channel_id:
        return "External channel"
    return channel_id[:1].upper() + channel_id[1:]


def _conversation_title(activity: Mapping[str, Any], channel_id: str, chat_id: str) -> str:
    channel_name = _display_channel_name(channel_id)
    if channel_id == "telegram":
        chat_label = _telegram_chat_label(activity)
        if chat_label:
            return f"{channel_name}: {chat_label} ({chat_id})"
    if chat_id:
        return f"{channel_name}: {chat_id}"
    return f"{channel_name} conversation"


def _local_user_id(channel_id: str, user_id: str, chat_id: str) -> str:
    normalized_channel = channel_id or "channel"
    if chat_id:
        return f"{normalized_channel}:chat:{chat_id}"
    if user_id:
        return f"{normalized_channel}:user:{user_id}"
    return f"{normalized_channel}:anonymous"


def _get_or_create_channel_conversation(memory, local_user_id: str, title: str) -> tuple[str, str]:
    """Returns the stable conversation owned by an external channel identity."""
    list_conversations = getattr(memory, "list_conversations", None)
    if callable(list_conversations):
        try:
            conversations = list_conversations(local_user_id, limit=1)
        except Exception:
            conversations = []
        if conversations:
            conversation = conversations[0]
            conversation_id = _stringify(conversation.get("id") if isinstance(conversation, Mapping) else "")
            current_title = _stringify(conversation.get("title") if isinstance(conversation, Mapping) else "")
            if conversation_id:
                resolved_title = title or current_title
                if resolved_title and resolved_title != current_title:
                    update_title = getattr(memory, "update_conversation_title", None)
                    if callable(update_title):
                        try:
                            update_title(conversation_id, resolved_title)
                        except Exception:
                            pass
                return conversation_id, resolved_title or current_title

    get_or_create = getattr(memory, "get_or_create_named_conversation", None)
    if callable(get_or_create):
        return get_or_create(local_user_id, title)

    return "", title


def resolve_channel_conversation_identity(activity: Mapping[str, Any], memory) -> ChannelConversationIdentity:
    """Returns a stable local conversation for an inbound channel activity.

    Desktop chat already supplies explicit conversation ids. External channels
    instead provide Bot Framework channel/conversation metadata, so this maps a
    channel chat to an AzulClaw user/conversation pair before invoking the
    orchestrator.
    """
    channel_id, channel_user_id, channel_chat_id = extract_channel_principal_ids(activity)
    local_user_id = _local_user_id(channel_id, channel_user_id, channel_chat_id)
    title = _conversation_title(activity, channel_id, channel_chat_id)
    conversation_id, title = _get_or_create_channel_conversation(memory, local_user_id, title)

    return ChannelConversationIdentity(
        user_id=local_user_id,
        conversation_id=conversation_id,
        title=title,
        channel_id=channel_id,
        channel_user_id=channel_user_id,
        channel_chat_id=channel_chat_id,
    )
