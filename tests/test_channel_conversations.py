from __future__ import annotations

import json
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from azul_backend.azul_brain.bot.azul_bot import AzulBot
from azul_backend.azul_brain.channels.conversation_identity import resolve_channel_conversation_identity
from azul_backend.azul_brain.channels.servicebus_worker import ServiceBusWorker
from azul_backend.azul_brain.conversation import ConversationReply
from azul_backend.azul_brain.memory.safe_memory import SafeMemory


class _Message:
    def __init__(self, payload: dict, correlation_id: str = "corr-1") -> None:
        self.body = json.dumps(payload).encode("utf-8")
        self.correlation_id = correlation_id
        self.session_id = ""


class _Orchestrator:
    def __init__(self, memory: SafeMemory) -> None:
        self.memory = memory
        self.calls: list[dict] = []

    async def resolve_route_async(self, text: str, lane: str = "auto"):
        return SimpleNamespace(lane="fast", reason="test-route")

    async def process_user_message(
        self,
        user_id: str,
        user_message: str,
        lane: str = "auto",
        conversation_id: str | None = None,
    ) -> ConversationReply:
        self.calls.append(
            {
                "user_id": user_id,
                "user_message": user_message,
                "lane": lane,
                "conversation_id": conversation_id,
            }
        )
        return ConversationReply(text=f"reply to {user_message}", lane=lane)


def _telegram_activity(chat_id: str, text: str = "hola", *, user_id: str = "user-1", title: str = "") -> dict:
    chat: dict[str, str] = {"id": chat_id}
    if title:
        chat["title"] = title
    return {
        "type": "message",
        "channelId": "telegram",
        "from": {"id": user_id},
        "conversation": {"id": chat_id},
        "text": text,
        "channelData": {
            "message": {
                "chat": chat,
                "from": {"id": user_id},
            }
        },
    }


class ChannelConversationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.root = Path("memory") / "test-channel-conversations"
        shutil.rmtree(self.root, ignore_errors=True)
        self.root.mkdir(parents=True, exist_ok=True)
        self.memory = SafeMemory(db_path=str(self.root / "memory.sqlite"))

    def tearDown(self) -> None:
        self.memory.close()
        shutil.rmtree(self.root, ignore_errors=True)

    def test_telegram_chat_reuses_stable_local_conversation(self) -> None:
        first = resolve_channel_conversation_identity(
            _telegram_activity("chat-100", title="Project Room"),
            self.memory,
        )
        second = resolve_channel_conversation_identity(
            _telegram_activity("chat-100", text="otra", title="Project Room"),
            self.memory,
        )

        self.assertEqual(first.user_id, "telegram:chat:chat-100")
        self.assertEqual(second.user_id, first.user_id)
        self.assertEqual(second.conversation_id, first.conversation_id)
        self.assertEqual(first.title, "Telegram: Project Room (chat-100)")

    def test_telegram_chat_title_change_keeps_same_local_conversation(self) -> None:
        first = resolve_channel_conversation_identity(
            _telegram_activity("chat-100", title="Old Room"),
            self.memory,
        )
        second = resolve_channel_conversation_identity(
            _telegram_activity("chat-100", title="New Room"),
            self.memory,
        )

        self.assertEqual(second.conversation_id, first.conversation_id)
        self.assertEqual(
            self.memory.get_conversation_title(first.conversation_id),
            "Telegram: New Room (chat-100)",
        )

    def test_telegram_chats_do_not_share_conversation_history(self) -> None:
        first = resolve_channel_conversation_identity(_telegram_activity("chat-100"), self.memory)
        second = resolve_channel_conversation_identity(_telegram_activity("chat-200"), self.memory)

        self.assertNotEqual(first.user_id, second.user_id)
        self.assertNotEqual(first.conversation_id, second.conversation_id)

    async def test_servicebus_worker_passes_channel_conversation_to_orchestrator(self) -> None:
        orchestrator = _Orchestrator(self.memory)
        worker = ServiceBusWorker(
            orchestrator=orchestrator,
            adapter=object(),
            connection_str="Endpoint=sb://example/;",
            inbound_queue="bot-inbound",
            outbound_queue="bot-outbound",
            use_sessions="false",
        )

        with patch(
            "azul_backend.azul_brain.channels.servicebus_worker.send_proactive_reply",
            new=AsyncMock(),
        ) as send_reply:
            await worker._handle_message(_Message(_telegram_activity("chat-100", title="Support")))

        self.assertEqual(len(orchestrator.calls), 1)
        call = orchestrator.calls[0]
        self.assertEqual(call["user_id"], "telegram:chat:chat-100")
        self.assertTrue(call["conversation_id"])
        self.assertEqual(
            self.memory.get_conversation_title(call["conversation_id"]),
            "Telegram: Support (chat-100)",
        )
        send_reply.assert_awaited_once()

    async def test_bot_framework_handler_passes_channel_conversation_to_orchestrator(self) -> None:
        class _Activity:
            text = "hola bot"

            def serialize(self) -> dict:
                return _telegram_activity("chat-300", text=self.text, title="Direct Bot")

        class _TurnContext:
            def __init__(self) -> None:
                self.activity = _Activity()
                self.send_activity = AsyncMock()

        orchestrator = _Orchestrator(self.memory)
        turn_context = _TurnContext()

        await AzulBot(orchestrator).on_message_activity(turn_context)

        self.assertEqual(len(orchestrator.calls), 1)
        call = orchestrator.calls[0]
        self.assertEqual(call["user_id"], "telegram:chat:chat-300")
        self.assertEqual(call["conversation_id"], self.memory.list_conversations("telegram:chat:chat-300")[0]["id"])
        self.assertEqual(
            self.memory.get_conversation_title(call["conversation_id"]),
            "Telegram: Direct Bot (chat-300)",
        )
        turn_context.send_activity.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
