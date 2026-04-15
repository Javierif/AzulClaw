"""Channel access-control helpers for inbound Bot Framework activities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


@dataclass(frozen=True)
class TelegramAccessDecision:
    """Authorization result for a Telegram activity."""

    authorized: bool
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


def evaluate_telegram_access(
    activity: Mapping[str, Any],
    allowed_user_ids: frozenset[str],
    allowed_chat_ids: frozenset[str],
) -> TelegramAccessDecision:
    """Checks whether a Telegram activity is permitted by the configured allowlists."""
    channel_id = _stringify(activity.get("channelId")).lower()
    if channel_id != "telegram":
        return TelegramAccessDecision(authorized=True)

    if not allowed_user_ids and not allowed_chat_ids:
        return TelegramAccessDecision(authorized=True)

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

    if allowed_user_ids and user_id not in allowed_user_ids:
        return TelegramAccessDecision(
            authorized=False,
            user_id=user_id,
            chat_id=chat_id,
            reason="telegram user not allowlisted",
        )
    if allowed_chat_ids and chat_id not in allowed_chat_ids:
        return TelegramAccessDecision(
            authorized=False,
            user_id=user_id,
            chat_id=chat_id,
            reason="telegram chat not allowlisted",
        )
    return TelegramAccessDecision(authorized=True, user_id=user_id, chat_id=chat_id)
