from __future__ import annotations

import json
import unittest

from azul_backend.azul_brain.api.routes import desktop_chat_handler


class FakeRequest:
    def __init__(self, payload: dict, orchestrator: object) -> None:
        self._payload = payload
        self.app = {"orchestrator": orchestrator}

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

    def get_or_create_empty_conversation(self, user_id: str) -> tuple[str, str]:
        self.created_for.append(user_id)
        return "conv-created", "New conversation"

    def set_active_conversation(self, user_id: str, conversation_id: str) -> None:
        self.active.append((user_id, conversation_id))

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


if __name__ == "__main__":
    unittest.main()
