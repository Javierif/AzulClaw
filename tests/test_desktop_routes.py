from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from aiohttp.client_exceptions import ClientConnectionResetError

from azul_backend.azul_brain.api.routes import (
    MAX_ATTACHMENTS_PER_TURN,
    desktop_attachments_post_handler,
    desktop_chat_handler,
    desktop_chat_stream_handler,
    desktop_conversation_messages_handler,
    desktop_workflow_request_decision_handler,
)
from azul_backend.azul_brain.runtime.approval_service import ApprovalRecord, ApprovalService
from azul_backend.azul_brain.runtime.pending_action_intent import (
    PendingSensitiveActionService,
    PendingSensitiveActionStore,
)
from azul_backend.azul_brain.runtime.skill_workflow_runtime import SkillWorkflowRuntime, SkillWorkflowStore


class _FakeToolText:
    def __init__(self, text: str) -> None:
        self.text = text


class _FakeToolResult:
    def __init__(self, payload: dict) -> None:
        self.content = [_FakeToolText(json.dumps(payload))]


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


class FakeWorkflowRequest(FakeRequest):
    def __init__(
        self,
        payload: dict,
        runtime: SkillWorkflowRuntime,
        *,
        match_info: dict | None = None,
    ) -> None:
        super().__init__(payload, object(), match_info=match_info)
        self.app["skill_workflow_runtime"] = runtime


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
    turn_status = "final_answer"
    model_id = "model-fast"
    model_label = "Fast"
    process_id = "proc-1"
    attempt_count = 2
    skipped_models = [{"model_id": "fast", "model_label": "Fast", "lane": "fast", "reason": "cooldown", "reason_label": "Cooling down after a previous failure", "detail": "2026-05-26T13:00:00Z"}]
    failed_attempts = [{"model_id": "fast", "label": "Fast", "error": "Fast deployment timed out"}]
    triage_reason = "test"
    conversation_title = None


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
        self.pending_calls: list[dict] = []

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

    async def _try_handle_pending_action_decision(
        self,
        user_id: str,
        action_id: str,
        decision: str,
        *,
        conversation_id: str | None = None,
    ) -> FakeReply | None:
        self.pending_calls.append(
            {
                "user_id": user_id,
                "action_id": action_id,
                "decision": decision,
                "conversation_id": conversation_id,
            }
        )
        return FakeReply()


class FakeStreamResponse:
    def __init__(self, *, fail_after_writes: int | None = None, **_kwargs) -> None:
        self.fail_after_writes = fail_after_writes
        self.write_calls = 0
        self.events: list[dict] = []
        self.status = 200

    def enable_chunked_encoding(self) -> None:
        return None

    async def prepare(self, _req) -> "FakeStreamResponse":
        return self

    async def write(self, chunk: bytes) -> None:
        if self.fail_after_writes is not None and self.write_calls >= self.fail_after_writes:
            raise ClientConnectionResetError("disconnect")
        self.write_calls += 1
        self.events.append(json.loads(chunk.decode("utf-8").strip()))

    async def drain(self) -> None:
        return None

    async def write_eof(self) -> None:
        return None


class FakeStreamOrchestrator(FakeOrchestrator):
    def __init__(self) -> None:
        super().__init__()
        self.stream_events: list[str] = []

    async def process_user_message_stream(
        self,
        user_id: str,
        message: str,
        *,
        lane: str = "auto",
        conversation_id: str | None = None,
        attachment_ids: list[str] | None = None,
        on_delta,
        on_commentary,
        on_progress,
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
        self.stream_events.append("before-commentary")
        await on_commentary("Working")
        self.stream_events.append("after-commentary")
        await on_delta("final")
        self.stream_events.append("after-delta")
        return FakeReply()


class DesktopChatRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_workflow_request_decision_resolves_hitl_approval(self) -> None:
        root = Path("memory") / "test-desktop-routes" / "workflow-decision"
        if root.exists():
            import shutil

            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        run = runtime.start_run(
            skill_id="dev.azulclaw.desktop-organizer",
            user_id="desktop-user",
            conversation_id="conv-open",
        )
        runtime.request_human_approval(
            run_id=run.run_id,
            request_id="request-1",
            action_kind="move_files",
            title="Folder Organizer",
            summary="Approve moving files.",
        )
        request = FakeWorkflowRequest(
            {"user_id": "desktop-user", "decision": "reject"},
            runtime,
            match_info={"run_id": run.run_id, "request_id": "request-1"},
        )

        response = await desktop_workflow_request_decision_handler(request)

        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["status"], "rejected")
        self.assertFalse(payload["approved"])

    async def test_workflow_request_decision_resumes_worker_and_returns_result_reply(self) -> None:
        root = Path("memory") / "test-desktop-routes" / "workflow-decision-resume"
        if root.exists():
            import shutil

            shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        worker = (Path("skills") / "official" / "desktop-organizer" / "workflow" / "main.py").resolve()
        workflow_spec = {
            "skill_id": "dev.azulclaw.desktop-organizer",
            "skill_name": "Folder Organizer",
            "mode": "isolated_process",
            "protocol_version": "1.0",
            "command": str(Path(sys.executable).resolve()),
            "args": [str(worker)],
            "cwd": str(worker.parent),
            "tools": {
                "preview": "preview_folder_organization",
                "execute": "organize_target_folder",
            },
            "tool_policies": {
                "execute": {
                    "requires_approval": True,
                    "sensitive_action": "move_files",
                }
            },
            "sensitive_actions": ["move_files"],
        }
        organization_plan = {
            "status": "ready_for_approval",
            "summary": "2 file(s) ready to organize.",
            "executable": True,
            "planned_move_count": 2,
            "blocked_count": 0,
            "preview": {"moves": [{"source": "invoice.pdf", "status": "planned"}]},
            "execute_tool": "execute",
            "execute_arguments": {"recursive": True},
        }
        run, events = await runtime.start_isolated_workflow(
            spec=workflow_spec,
            user_id="desktop-user",
            conversation_id="conv-open",
            input_payload={"approved_organization_plan": organization_plan},
        )
        request_id = events[-1].data["request_id"]

        class _McpClient:
            def __init__(self) -> None:
                self.calls: list[tuple[str, dict, str]] = []

            async def call_tool(self, tool_name: str, arguments: dict, *, skill_id: str = "") -> _FakeToolResult:
                self.calls.append((tool_name, arguments, skill_id))
                return _FakeToolResult({"summary": "2 file(s) moved."})

        class _PersistingOrchestrator:
            def __init__(self) -> None:
                self.persisted: list[tuple[str, str, str, str | None]] = []

            async def persist_with_vector_memory(
                self,
                user_id: str,
                role: str,
                content: str,
                conversation_id: str | None = None,
            ) -> str:
                self.persisted.append((user_id, role, content, conversation_id))
                return "msg-workflow-result"

        mcp_client = _McpClient()
        orchestrator = _PersistingOrchestrator()
        request = FakeWorkflowRequest(
            {"user_id": "desktop-user", "decision": "approve"},
            runtime,
            match_info={"run_id": run.run_id, "request_id": request_id},
        )
        request.app["mcp_client"] = mcp_client
        request.app["orchestrator"] = orchestrator

        try:
            with patch(
                "azul_backend.azul_brain.api.routes.list_enabled_workflow_runtime_specs",
                return_value=[workflow_spec],
            ):
                response = await desktop_workflow_request_decision_handler(request)

            payload = json.loads(response.text)
            lifecycle = runtime.approval_service.get_by_action_id(request_id)
            self.assertEqual(response.status, 200)
            self.assertEqual(payload["status"], "completed")
            self.assertEqual(payload["workflow_status"], "completed")
            self.assertIn("2 file(s) moved.", payload["reply"])
            self.assertEqual(
                mcp_client.calls,
                [("organize_target_folder", {"recursive": True}, "dev.azulclaw.desktop-organizer")],
            )
            self.assertIsNotNone(lifecycle)
            assert lifecycle is not None
            self.assertEqual(lifecycle.status, "completed")
            self.assertEqual(orchestrator.persisted[-1][1], "assistant")
            self.assertIn("2 file(s) moved.", orchestrator.persisted[-1][2])
        finally:
            import shutil

            shutil.rmtree(root, ignore_errors=True)

    async def test_conversation_messages_include_approval_lifecycle_status(self) -> None:
        orchestrator = FakeOrchestrator()
        orchestrator.memory.get_conversation_messages = lambda conversation_id, limit=12: [
            {
                "role": "assistant",
                "content": (
                    "[PENDING_ACTION:approval]\n"
                    "ActionId: pending-sensitive-action-123\n"
                    "ActionKind: folder_organizer\n"
                    "Title: Folder Organizer\n"
                    "Summary: Approve applying the reviewed plan.\n"
                    "ApproveLabel: Apply changes\n"
                    "RejectLabel: Cancel\n"
                    "[/PENDING_ACTION]"
                ),
                "conversation_id": conversation_id,
            }
        ]
        request = FakeRequest({}, orchestrator, query={"user_id": "desktop-user"}, match_info={"conv_id": "conv-open"})

        with patch(
            "azul_backend.azul_brain.api.routes.ApprovalService.load",
            return_value=[
                ApprovalRecord(
                    action_id="pending-sensitive-action-123",
                    user_id="desktop-user",
                    conversation_id="conv-open",
                    source="sensitive_action",
                    action_kind="folder_organizer",
                    title="Folder Organizer",
                    summary="Approve applying the reviewed plan.",
                    status="superseded",
                    created_at="2026-05-29T10:00:00Z",
                    updated_at="2026-05-29T10:01:00Z",
                )
            ],
        ):
            response = await desktop_conversation_messages_handler(request)

        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["messages"][0]["approval_status"], "superseded")
        self.assertEqual(payload["messages"][0]["approval_status_label"], "Superseded")

    async def test_conversation_messages_downgrade_pending_approval_without_live_payload(self) -> None:
        orchestrator = FakeOrchestrator()
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(Path("memory") / "test-desktop-routes" / "stale-pending.json")
        )
        orchestrator.memory.get_conversation_messages = lambda conversation_id, limit=12: [
            {
                "role": "assistant",
                "content": (
                    "[PENDING_ACTION:approval]\n"
                    "ActionId: pending-sensitive-action-stale\n"
                    "ActionKind: folder_organizer\n"
                    "Title: Folder Organizer\n"
                    "Summary: Approve applying the reviewed plan.\n"
                    "ApproveLabel: Apply changes\n"
                    "RejectLabel: Cancel\n"
                    "[/PENDING_ACTION]"
                ),
                "conversation_id": conversation_id,
            }
        ]
        request = FakeRequest({}, orchestrator, query={"user_id": "desktop-user"}, match_info={"conv_id": "conv-open"})

        with patch(
            "azul_backend.azul_brain.api.routes.ApprovalService.load",
            return_value=[
                ApprovalRecord(
                    action_id="pending-sensitive-action-stale",
                    user_id="desktop-user",
                    conversation_id="conv-open",
                    source="sensitive_action",
                    action_kind="folder_organizer",
                    title="Folder Organizer",
                    summary="Approve applying the reviewed plan.",
                    status="pending",
                    created_at="2026-05-31T10:00:00Z",
                    updated_at="2026-05-31T10:01:00Z",
                )
            ],
        ):
            response = await desktop_conversation_messages_handler(request)

        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["messages"][0]["approval_status"], "expired")
        self.assertEqual(payload["messages"][0]["approval_status_label"], "Expired")

    async def test_conversation_messages_downgrade_approval_without_lifecycle_record(self) -> None:
        orchestrator = FakeOrchestrator()
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(Path("memory") / "test-desktop-routes" / "missing-lifecycle.json")
        )
        orchestrator.memory.get_conversation_messages = lambda conversation_id, limit=12: [
            {
                "role": "assistant",
                "content": (
                    "[PENDING_ACTION:approval]\n"
                    "ActionId: pending-sensitive-action-missing\n"
                    "ActionKind: folder_organizer\n"
                    "Title: Folder Organizer\n"
                    "Summary: Approve applying the reviewed plan.\n"
                    "ApproveLabel: Apply changes\n"
                    "RejectLabel: Cancel\n"
                    "[/PENDING_ACTION]"
                ),
                "conversation_id": conversation_id,
            }
        ]
        request = FakeRequest({}, orchestrator, query={"user_id": "desktop-user"}, match_info={"conv_id": "conv-open"})

        with patch("azul_backend.azul_brain.api.routes.ApprovalService.load", return_value=[]):
            response = await desktop_conversation_messages_handler(request)

        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["messages"][0]["approval_status"], "expired")
        self.assertEqual(payload["messages"][0]["approval_status_label"], "Expired")

    async def test_conversation_messages_keep_pending_when_live_payload_exists_without_lifecycle_record(self) -> None:
        orchestrator = FakeOrchestrator()
        store = PendingSensitiveActionStore(Path("memory") / "test-desktop-routes" / "live-without-lifecycle.json")
        pending = store.save(
            user_id="desktop-user",
            conversation_id="conv-open",
            title="Folder Organizer",
            summary="Approve applying the reviewed plan.",
            source_user_message="Organiza la carpeta objetivo",
            action_kind="folder_organizer",
        )
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(store)
        orchestrator.memory.get_conversation_messages = lambda conversation_id, limit=12: [
            {
                "role": "assistant",
                "content": (
                    "[PENDING_ACTION:approval]\n"
                    f"ActionId: {pending.id}\n"
                    "ActionKind: folder_organizer\n"
                    "Title: Folder Organizer\n"
                    "Summary: Approve applying the reviewed plan.\n"
                    "ApproveLabel: Apply changes\n"
                    "RejectLabel: Cancel\n"
                    "[/PENDING_ACTION]"
                ),
                "conversation_id": conversation_id,
            }
        ]
        request = FakeRequest({}, orchestrator, query={"user_id": "desktop-user"}, match_info={"conv_id": "conv-open"})

        with patch("azul_backend.azul_brain.api.routes.ApprovalService.load", return_value=[]):
            response = await desktop_conversation_messages_handler(request)

        payload = json.loads(response.text)
        self.assertEqual(response.status, 200)
        self.assertEqual(payload["messages"][0]["approval_status"], "pending")
        self.assertEqual(payload["messages"][0]["approval_status_label"], "Awaiting approval")

    async def test_non_stream_chat_accepts_structured_pending_action_decision(self) -> None:
        orchestrator = FakeOrchestrator()
        request = FakeRequest(
            {
                "conversation_id": "conv-open",
                "pending_action_id": "pending-heartbeat-create",
                "pending_action_decision": "approve",
            },
            orchestrator,
        )

        response = await desktop_chat_handler(request)
        payload = json.loads(response.text)

        self.assertEqual(response.status, 200)
        self.assertEqual(orchestrator.pending_calls[0]["action_id"], "pending-heartbeat-create")
        self.assertEqual(orchestrator.pending_calls[0]["decision"], "approve")
        self.assertEqual(payload["conversation_id"], "conv-open")

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
        self.assertEqual(payload["runtime"]["attempt_count"], 2)
        self.assertEqual(payload["runtime"]["turn_status"], "final_answer")
        self.assertEqual(payload["runtime"]["skipped_models"][0]["reason"], "cooldown")
        self.assertEqual(payload["runtime"]["failed_attempts"][0]["error"], "Fast deployment timed out")

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

    async def test_stream_chat_stops_processing_when_client_disconnects(self) -> None:
        orchestrator = FakeStreamOrchestrator()
        request = FakeRequest({"message": "hello"}, orchestrator)
        fake_response = FakeStreamResponse(fail_after_writes=1)

        with patch("azul_backend.azul_brain.api.routes.web.StreamResponse", return_value=fake_response):
            response = await desktop_chat_stream_handler(request)

        self.assertIs(response, fake_response)
        self.assertEqual(fake_response.events, [{"type": "start"}])
        self.assertEqual(orchestrator.stream_events, ["before-commentary"])

    async def test_stream_chat_done_event_includes_attempt_count(self) -> None:
        orchestrator = FakeStreamOrchestrator()
        request = FakeRequest({"message": "hello"}, orchestrator)
        fake_response = FakeStreamResponse()

        with patch("azul_backend.azul_brain.api.routes.web.StreamResponse", return_value=fake_response):
            response = await desktop_chat_stream_handler(request)

        self.assertIs(response, fake_response)
        done_event = next(item for item in fake_response.events if item["type"] == "done")
        self.assertEqual(done_event["runtime"]["attempt_count"], 2)
        self.assertEqual(done_event["runtime"]["turn_status"], "final_answer")
        self.assertEqual(done_event["runtime"]["skipped_models"][0]["reason"], "cooldown")
        self.assertEqual(done_event["runtime"]["failed_attempts"][0]["error"], "Fast deployment timed out")

    async def test_stream_chat_accepts_structured_pending_action_decision(self) -> None:
        orchestrator = FakeStreamOrchestrator()
        request = FakeRequest(
            {
                "conversation_id": "conv-open",
                "pending_action_id": "pending-heartbeat-create",
                "pending_action_decision": "reject",
            },
            orchestrator,
        )
        fake_response = FakeStreamResponse()

        with patch("azul_backend.azul_brain.api.routes.web.StreamResponse", return_value=fake_response):
            response = await desktop_chat_stream_handler(request)

        self.assertIs(response, fake_response)
        self.assertEqual(orchestrator.pending_calls[0]["action_id"], "pending-heartbeat-create")
        self.assertEqual(orchestrator.pending_calls[0]["decision"], "reject")
        self.assertEqual([event["type"] for event in fake_response.events[:3]], ["start", "commentary", "progress-init"])

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
