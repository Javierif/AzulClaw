from __future__ import annotations

import json
import unittest

from azul_backend.azul_brain.api.routes import (
    MAX_ATTACHMENTS_PER_TURN,
    desktop_attachments_post_handler,
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


class FakeAttachmentPart:
    def __init__(self, filename: str, chunks: list[bytes]) -> None:
        self.filename = filename
        self._chunks = list(chunks)

    async def read_chunk(self) -> bytes:
        if not self._chunks:
            return b""
        return self._chunks.pop(0)


class FakeMultipartReader:
    def __init__(self, parts: list[FakeAttachmentPart]) -> None:
        self._parts = parts

    def __aiter__(self):
        self._index = 0
        return self

    async def __anext__(self) -> FakeAttachmentPart:
        if self._index >= len(self._parts):
            raise StopAsyncIteration
        part = self._parts[self._index]
        self._index += 1
        return part


class FakeAttachmentRequest(FakeRequest):
    def __init__(self, orchestrator: object, parts: list[FakeAttachmentPart], *, query: dict | None = None) -> None:
        super().__init__({}, orchestrator, query=query)
        self._parts = parts
        self.headers: dict[str, str] = {}

    async def multipart(self) -> FakeMultipartReader:
        return FakeMultipartReader(self._parts)


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
        self.viewed: list[tuple[str, str]] = []
        self.owned_conversations = {"conv-open": "desktop-user"}
        self.created_attachments: list[dict] = []

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

    def mark_conversation_viewed(self, user_id: str, conversation_id: str) -> bool:
        self.viewed.append((user_id, conversation_id))
        return True

    def create_attachment_draft(
        self,
        *,
        user_id: str,
        filename: str,
        data: bytes,
        conversation_id: str | None = None,
    ) -> dict:
        created = {
            "id": f"att-{len(self.created_attachments) + 1}",
            "filename": filename,
            "user_id": user_id,
            "conversation_id": conversation_id or "",
            "size_bytes": len(data),
        }
        self.created_attachments.append(created)
        return created

    def delete_draft_attachment(self, attachment_id: str, user_id: str) -> bool:
        before = len(self.created_attachments)
        self.created_attachments = [
            item
            for item in self.created_attachments
            if not (item["id"] == attachment_id and item["user_id"] == user_id)
        ]
        return len(self.created_attachments) != before


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
        attachment_ids: list[str] | None = None,
    ) -> FakeReply:
        self.calls.append(
            {
                "user_id": user_id,
                "message": message,
                "lane": lane,
                "conversation_id": conversation_id,
                "attachment_ids": attachment_ids or [],
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
        self.assertEqual(orchestrator.memory.viewed, [("desktop-user", "conv-open")])
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
        self.assertEqual(orchestrator.memory.viewed, [("desktop-user", "conv-created")])
        self.assertEqual(payload["conversation_id"], "conv-created")

    async def test_non_stream_chat_accepts_attachment_only_turns(self) -> None:
        orchestrator = FakeOrchestrator()
        request = FakeRequest({"attachment_ids": ["att-1"]}, orchestrator)

        response = await desktop_chat_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(orchestrator.calls[0]["message"], "Please analyze the attached files.")
        self.assertEqual(orchestrator.calls[0]["attachment_ids"], ["att-1"])
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
        self.assertEqual(orchestrator.memory.viewed, [])

    async def test_messages_marks_conversation_viewed(self) -> None:
        orchestrator = FakeOrchestrator()
        request = FakeRequest(
          {},
          orchestrator,
          query={"user_id": "desktop-user"},
          match_info={"conv_id": "conv-open"},
        )

        response = await desktop_conversation_messages_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(orchestrator.memory.active, [("desktop-user", "conv-open")])
        self.assertEqual(orchestrator.memory.viewed, [("desktop-user", "conv-open")])
        self.assertEqual(payload["messages"][0]["conversation_id"], "conv-open")

    async def test_attachments_reject_non_owned_conversation(self) -> None:
        orchestrator = FakeOrchestrator()
        orchestrator.memory.owned_conversations["other-conv"] = "other-user"
        request = FakeAttachmentRequest(
            orchestrator,
            [FakeAttachmentPart("notes.txt", [b"hello"])],
            query={"user_id": "desktop-user", "conversation_id": "other-conv"},
        )

        response = await desktop_attachments_post_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 404)
        self.assertEqual(payload["error"], "Conversation not found")
        self.assertEqual(orchestrator.memory.created_attachments, [])

    async def test_attachments_reject_requests_above_turn_limit(self) -> None:
        orchestrator = FakeOrchestrator()
        parts = [
            FakeAttachmentPart(f"file-{index}.txt", [b"x"])
            for index in range(MAX_ATTACHMENTS_PER_TURN + 1)
        ]
        request = FakeAttachmentRequest(orchestrator, parts, query={"user_id": "desktop-user"})

        response = await desktop_attachments_post_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 400)
        self.assertIn("At most", payload["error"])
        self.assertEqual(orchestrator.memory.created_attachments, [])

    async def test_attachments_create_draft_for_owned_conversation(self) -> None:
        orchestrator = FakeOrchestrator()
        request = FakeAttachmentRequest(
            orchestrator,
            [FakeAttachmentPart("notes.txt", [b"hello", b" world"])],
            query={"user_id": "desktop-user", "conversation_id": "conv-open"},
        )

        response = await desktop_attachments_post_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(payload["items"][0]["filename"], "notes.txt")
        self.assertEqual(orchestrator.memory.created_attachments[0]["conversation_id"], "conv-open")
        self.assertEqual(orchestrator.memory.created_attachments[0]["size_bytes"], 11)


if __name__ == "__main__":
    unittest.main()
