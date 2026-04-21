from __future__ import annotations

import json
import unittest

from azul_backend.azul_brain.api.routes import (
    desktop_chat_handler,
    desktop_conversation_messages_handler,
)


class FakeRequest:
    def __init__(
        self,
        payload: dict,
        orchestrator: object,
        *,
        query: dict | None = None,
        match_info: dict | None = None,
    ) -> None:
        self._payload = payload
        self.app = {"orchestrator": orchestrator}
        self.query = query or {}
        self.match_info = match_info or {}

    async def json(self) -> dict:
        return self._payload


class FakeReply:
    text = "ok"
    lane = "fast"
    model_id = "model-fast"
    model_label = "Fast"
    process_id = "proc-1"
    triage_reason = "test"


class FakeMemory:
    def __init__(self) -> None:
        self.active: list[tuple[str, str]] = []
        self.created_for: list[str] = []
        self.owned_conversations = {"conv-open": "desktop-user"}

    def get_or_create_empty_conversation(self, user_id: str) -> tuple[str, str]:
        self.created_for.append(user_id)
        return "conv-created", "New conversation"

    def set_active_conversation(self, user_id: str, conversation_id: str) -> None:
        self.active.append((user_id, conversation_id))

    def conversation_belongs_to_user(self, conversation_id: str, user_id: str) -> bool:
        return self.owned_conversations.get(conversation_id) == user_id

    def get_conversation_title(self, conversation_id: str) -> str:
        return "Current chat"

    def get_history(self, user_id: str, limit: int = 12) -> list[dict]:
        return [{"role": "user", "content": "hello"}]

    def get_conversation_messages(self, conversation_id: str, limit: int = 12) -> list[dict]:
        return [{"role": "user", "content": "hello", "conversation_id": conversation_id}]


class FakeOrchestrator:
    def __init__(self) -> None:
        self.memory = FakeMemory()
        self.calls: list[dict] = []

    async def process_user_message(
        self,
        user_id: str,
        message: str,
        lane: str = "auto",
        conversation_id: str | None = None,
    ) -> FakeReply:
        self.calls.append(
            {
                "user_id": user_id,
                "message": message,
                "lane": lane,
                "conversation_id": conversation_id,
            }
        )
        return FakeReply()


class DesktopChatRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_non_stream_chat_marks_supplied_conversation_active(self) -> None:
        orchestrator = FakeOrchestrator()
        request = FakeRequest(
            {
                "user_id": "desktop-user",
                "message": "hello",
                "conversation_id": "conv-open",
            },
            orchestrator,
        )

        response = await desktop_chat_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(orchestrator.memory.active, [("desktop-user", "conv-open")])
        self.assertEqual(orchestrator.memory.created_for, [])
        self.assertEqual(orchestrator.calls[0]["conversation_id"], "conv-open")
        self.assertEqual(payload["conversation_id"], "conv-open")
        self.assertEqual(payload["conversation_title"], "Current chat")

    async def test_non_stream_chat_creates_and_marks_active_conversation(self) -> None:
        orchestrator = FakeOrchestrator()
        request = FakeRequest({"message": "hello"}, orchestrator)

        response = await desktop_chat_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(orchestrator.memory.created_for, ["desktop-user"])
        self.assertEqual(orchestrator.memory.active, [("desktop-user", "conv-created")])
        self.assertEqual(orchestrator.calls[0]["conversation_id"], "conv-created")
        self.assertEqual(payload["conversation_id"], "conv-created")

    async def test_non_stream_chat_ignores_non_owned_conversation(self) -> None:
        orchestrator = FakeOrchestrator()
        orchestrator.memory.owned_conversations["other-conv"] = "other-user"
        request = FakeRequest(
            {
                "user_id": "desktop-user",
                "message": "hello",
                "conversation_id": "other-conv",
            },
            orchestrator,
        )

        response = await desktop_chat_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(orchestrator.memory.created_for, ["desktop-user"])
        self.assertEqual(orchestrator.memory.active, [("desktop-user", "conv-created")])
        self.assertEqual(orchestrator.calls[0]["conversation_id"], "conv-created")
        self.assertEqual(payload["conversation_id"], "conv-created")

    async def test_messages_rejects_non_owned_conversation(self) -> None:
        orchestrator = FakeOrchestrator()
        orchestrator.memory.owned_conversations["other-conv"] = "other-user"
        request = FakeRequest(
            {},
            orchestrator,
            query={"user_id": "desktop-user"},
            match_info={"conv_id": "other-conv"},
        )

        response = await desktop_conversation_messages_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"], "Conversation not found")
        self.assertEqual(orchestrator.memory.active, [])


if __name__ == "__main__":
    unittest.main()
