"""Channel access-control helpers for inbound Bot Framework activities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class ChannelAccessDecision:
    """Authorization result for an inbound channel activity."""

    authorized: bool
    channel_id: str = ""
    user_id: str = ""
    chat_id: str = ""
    reason: str = ""


def parse_csv_allowlist(raw_value: str) -> frozenset[str]:
    """Parses a comma-separated env var into a normalized immutable allowlist."""
    if not raw_value:
        return frozenset()
    return frozenset(part.strip() for part in raw_value.split(",") if part.strip())


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _first_non_empty(*values: Any) -> str:
    for value in values:
        text = _stringify(value)
        if text:
            return text
    return ""


def extract_channel_principal_ids(activity: Mapping[str, Any]) -> tuple[str, str, str]:
    """Extracts channel id, user id, and conversation/chat id from a Bot Framework activity."""
    channel_id = _stringify(activity.get("channelId")).lower()
    from_block = activity.get("from") or {}
    conversation_block = activity.get("conversation") or {}
    channel_data = activity.get("channelData") or {}
    message_block = channel_data.get("message") if isinstance(channel_data, Mapping) else {}
    if not isinstance(message_block, Mapping):
        message_block = {}

    user_id = _first_non_empty(
        from_block.get("id") if isinstance(from_block, Mapping) else "",
        channel_data.get("from", {}).get("id") if isinstance(channel_data.get("from"), Mapping) else "",
        message_block.get("from", {}).get("id") if isinstance(message_block.get("from"), Mapping) else "",
    )
    chat_id = _first_non_empty(
        conversation_block.get("id") if isinstance(conversation_block, Mapping) else "",
        channel_data.get("chat", {}).get("id") if isinstance(channel_data.get("chat"), Mapping) else "",
        message_block.get("chat", {}).get("id") if isinstance(message_block.get("chat"), Mapping) else "",
    )
    return channel_id, user_id, chat_id


def evaluate_channel_connector_access(
    activity: Mapping[str, Any],
    channel_policies: Mapping[str, Mapping[str, frozenset[str] | str]] | None,
) -> ChannelAccessDecision:
    """Checks whether an inbound channel activity is permitted by the active connector policies."""
    channel_id, user_id, chat_id = extract_channel_principal_ids(activity)
    if not channel_id or not isinstance(channel_policies, Mapping):
        return ChannelAccessDecision(authorized=True, channel_id=channel_id, user_id=user_id, chat_id=chat_id)

    policy = channel_policies.get(channel_id)
    if not isinstance(policy, Mapping):
        return ChannelAccessDecision(authorized=True, channel_id=channel_id, user_id=user_id, chat_id=chat_id)

    allowed_user_ids = policy.get("allowed_user_ids", frozenset())
    allowed_chat_ids = policy.get("allowed_chat_ids", frozenset())
    if not isinstance(allowed_user_ids, frozenset):
        allowed_user_ids = frozenset(str(item).strip() for item in allowed_user_ids if str(item).strip()) if isinstance(allowed_user_ids, (list, tuple, set)) else frozenset()
    if not isinstance(allowed_chat_ids, frozenset):
        allowed_chat_ids = frozenset(str(item).strip() for item in allowed_chat_ids if str(item).strip()) if isinstance(allowed_chat_ids, (list, tuple, set)) else frozenset()

    if not allowed_user_ids and not allowed_chat_ids:
        return ChannelAccessDecision(authorized=True, channel_id=channel_id, user_id=user_id, chat_id=chat_id)
    if allowed_user_ids and user_id not in allowed_user_ids:
        return ChannelAccessDecision(
            authorized=False,
            channel_id=channel_id,
            user_id=user_id,
            chat_id=chat_id,
            reason=f"{channel_id} user not allowlisted",
        )
    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return ChannelAccessDecision(
            authorized=False,
            channel_id=channel_id,
            user_id=user_id,
            chat_id=chat_id,
            reason=f"{channel_id} chat not allowlisted",
        )
    return ChannelAccessDecision(authorized=True, channel_id=channel_id, user_id=user_id, chat_id=chat_id)


def evaluate_telegram_access(
    activity: Mapping[str, Any],
    allowed_user_ids: frozenset[str],
    allowed_chat_ids: frozenset[str],
) -> ChannelAccessDecision:
    """Checks whether a Telegram activity is permitted by the configured allowlists."""
    return evaluate_channel_connector_access(
        activity,
        {
            "telegram": {
                "allowed_user_ids": allowed_user_ids,
                "allowed_chat_ids": allowed_chat_ids,
            }
        },
    )
