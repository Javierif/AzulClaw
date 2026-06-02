from __future__ import annotations

import asyncio
import json
import shutil
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from azul_backend.azul_brain.attachments import AttachmentError
from azul_backend.azul_brain.conversation import (
    ConversationOrchestrator,
    ConversationReply,
    FolderOrganizerRequestVerdict,
    PendingActionUserIntentVerdict,
)
from azul_backend.azul_brain.cortex.fast.commentary import build_commentary
from azul_backend.azul_brain.cortex.fast.triage import TriageDecision
from azul_backend.azul_brain.runtime.pending_action_intent import PendingSensitiveActionService
from azul_backend.azul_brain.runtime.pending_action_intent import FolderOrganizerPreviewStore
from azul_backend.azul_brain.runtime.pending_action_intent import PendingSensitiveExecutionReceiptStore
from azul_backend.azul_brain.runtime.pending_action_intent import PendingSensitiveActionStore
from azul_backend.azul_brain.runtime.heartbeat_intent import PendingHeartbeatStore, HeartbeatDraft


class _ClientDisconnected(Exception):
    pass


class ConversationStreamingCommentaryTests(unittest.IsolatedAsyncioTestCase):
    def _pending_store_path(self, name: str) -> Path:
        root = Path("memory") / "test-pending-actions-streaming" / name
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root / "pending.json"

    def _receipt_store_path(self, name: str) -> Path:
        root = Path("memory") / "test-pending-actions-streaming" / f"{name}-receipt"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root / "receipt.json"

    def _preview_store_path(self, name: str) -> Path:
        root = Path("memory") / "test-pending-actions-streaming" / f"{name}-preview"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root / "preview.json"

    async def test_typed_confirmation_with_pending_sensitive_action_returns_card_only_reminder(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("typed-confirmation")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("typed-confirmation")),
        )
        orchestrator._judge_pending_action_user_intent = AsyncMock(
            return_value=PendingActionUserIntentVerdict(decision="approve")
        )
        try:
            orchestrator.pending_sensitive_actions.pending_store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza mi carpeta",
            )
            orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")

            reply = await orchestrator._try_handle_sensitive_action_confirmation_attempt(
                "desktop-user",
                "Sí, hazlo",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("confirmation card in chat", reply.text)
            self.assertEqual(reply.triage_reason, "pending-action-card-only")
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions-streaming" / "typed-confirmation", ignore_errors=True)

    async def test_sensitive_decision_ignores_heartbeat_approval_records(self) -> None:
        store = PendingSensitiveActionStore(self._pending_store_path("ignore-heartbeat-record"))
        service = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("ignore-heartbeat-record")),
        )
        heartbeat_store = PendingHeartbeatStore(Path("memory") / "test-pending-actions-streaming" / "ignore-heartbeat-record-heartbeat" / "pending.json")
        try:
            pending = heartbeat_store.save_for_user(
                "desktop-user",
                HeartbeatDraft(
                    name="Heartbeat",
                    prompt="Check inbox.",
                    cron_expression="*/30 * * * *",
                    lane="fast",
                ),
                "conv-1",
            )

            outcome = service.handle_pending_decision(
                "desktop-user",
                pending.id,
                "approve",
            )

            self.assertIsNone(outcome)
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions-streaming" / "ignore-heartbeat-record-heartbeat", ignore_errors=True)

    async def test_structured_sensitive_action_approval_executes_in_slow_lane(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("structured-approval")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("structured-approval")),
        )
        try:
            orchestrator.pending_sensitive_actions.pending_store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Sensitive action",
                summary="Approve executing the proposed sensitive action.",
                source_user_message="Ejecuta el cambio aprobado",
                action_kind="generic",
            )
            orchestrator.memory = type(
                "_Memory",
                (),
                {"get_conversation_messages": staticmethod(lambda _conversation_id, limit=12: [{"role": "assistant", "content": "Confirmas que proceda?"}])},
            )()
            orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: [
                {"role": "assistant", "content": "Confirmas que proceda?"}
            ]
            orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
            orchestrator.retrieve_user_knowledge = lambda _user_id: []
            orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")

            captured: dict[str, object] = {}

            def build_agent_messages(*args, **kwargs):
                captured["confirmed_sensitive_action"] = kwargs.get("confirmed_sensitive_action")
                return []

            async def invoke_messages(
                _messages,
                _user_message: str,
                *,
                lane: str,
                source: str,
                title: str,
                tools_enabled: bool = True,
            ) -> ConversationReply:
                captured["lane"] = lane
                return ConversationReply(text="Moved files.", lane=lane)

            orchestrator.build_agent_messages = build_agent_messages
            orchestrator.invoke_messages = invoke_messages

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                orchestrator.pending_sensitive_actions.pending_store.get_for_user("desktop-user").id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertEqual(reply.text, "Moved files.")
            self.assertEqual(captured["lane"], "slow")
            self.assertTrue(captured["confirmed_sensitive_action"])
            persisted_roles = [call.args[1] for call in orchestrator.persist_with_vector_memory.await_args_list]
            self.assertNotIn("user", persisted_roles)
            self.assertIn("assistant", persisted_roles)
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions-streaming" / "structured-approval", ignore_errors=True)

    async def test_unresolved_folder_organizer_approval_does_not_execute_anything(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-unresolved")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-unresolved")),
        )
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.invoke_messages = AsyncMock()
        try:
            pending = orchestrator.pending_sensitive_actions.pending_store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza mi carpeta objetivo",
                action_kind="folder_organizer_unresolved",
            )

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("did not move any files", reply.text)
            orchestrator.invoke_messages.assert_not_awaited()
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions-streaming" / "folder-unresolved", ignore_errors=True)

    async def test_folder_organizer_pending_approval_executes_mcp_tool_directly(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = None
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        store = PendingSensitiveActionStore(self._pending_store_path("folder-direct"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-direct")),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: False
        try:
            pending = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza la carpeta objetivo",
                action_kind="folder_organizer",
                skill_id="dev.azulclaw.desktop-organizer",
                tool_name="organize_target_folder",
                tool_arguments={"recursive": True, "plan_token": "abc123"},
            )

            class _FakeToolText:
                def __init__(self, text: str) -> None:
                    self.text = text

            class _FakeToolResult:
                def __init__(self, text: str) -> None:
                    self.content = [_FakeToolText(text)]

            mcp_client = type("_McpClient", (), {})()
            mcp_client.call_tool = AsyncMock(
                return_value=_FakeToolResult(
                    '{"relative_path": ".", "summary": "3 file(s) moved. Documents: 2, Images: 1."}'
                )
            )
            orchestrator.mcp_client = mcp_client

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("3 file(s) moved", reply.text)
            mcp_client.call_tool.assert_awaited_once_with(
                "organize_target_folder",
                {"recursive": True, "plan_token": "abc123"},
                skill_id="dev.azulclaw.desktop-organizer",
            )
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions-streaming" / "folder-direct", ignore_errors=True)

    async def test_enabled_folder_workflow_blocks_legacy_pending_approval_execution(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = None
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        store = PendingSensitiveActionStore(self._pending_store_path("folder-direct-disabled"))
        service = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-direct-disabled")),
        )
        orchestrator.pending_sensitive_actions = service
        orchestrator._folder_organizer_workflow_enabled = lambda: True
        try:
            pending = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza la carpeta objetivo",
                action_kind="folder_organizer",
                skill_id="dev.azulclaw.desktop-organizer",
                tool_name="organize_target_folder",
                tool_arguments={"recursive": True, "plan_token": "abc123"},
            )

            mcp_client = type("_McpClient", (), {})()
            mcp_client.call_tool = AsyncMock()
            orchestrator.mcp_client = mcp_client

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertEqual(reply.turn_status, "tool_failure")
            self.assertIn("workflow-backed approval", reply.text)
            mcp_client.call_tool.assert_not_awaited()
            record = service.pending_store.approval_service.get_by_action_id(pending.id)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.status, "failed")
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions-streaming" / "folder-direct-disabled",
                ignore_errors=True,
            )

    async def test_folder_organizer_stale_execution_marks_failure_instead_of_completed(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = None
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        store = PendingSensitiveActionStore(self._pending_store_path("folder-stale-preview-execution"))
        service = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-stale-preview-execution")),
        )
        orchestrator.pending_sensitive_actions = service
        orchestrator._folder_organizer_workflow_enabled = lambda: False
        try:
            pending = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza la carpeta objetivo",
                action_kind="folder_organizer",
                skill_id="dev.azulclaw.desktop-organizer",
                tool_name="organize_target_folder",
                tool_arguments={"recursive": True, "plan_token": "abc123"},
                plan_snapshot={
                    "tool_arguments": {"recursive": True, "plan_token": "abc123"},
                    "preview": {
                        "relative_path": ".",
                        "recursive": True,
                        "plan_token": "abc123",
                        "summary": "59 file(s) ready to organize. Documents: 13, Images: 14.",
                    },
                },
            )

            class _FakeToolText:
                def __init__(self, text: str) -> None:
                    self.text = text

            class _FakeToolResult:
                def __init__(self, text: str) -> None:
                    self.content = [_FakeToolText(text)]

            mcp_client = type("_McpClient", (), {})()
            mcp_client.call_tool = AsyncMock(
                return_value=_FakeToolResult(
                    '{"relative_path": ".", "summary": "No files are ready to organize. 1 blocked by conflicts. Other: 1."}'
                )
            )
            orchestrator.mcp_client = mcp_client

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertEqual(reply.turn_status, "tool_failure")
            self.assertIn("did not apply any changes", reply.text)
            record = service.pending_store.approval_service.get_by_action_id(pending.id)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.status, "failed")
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions-streaming" / "folder-stale-preview-execution", ignore_errors=True)

    async def test_folder_organizer_plan_token_approval_executes_all_pending_batches(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = None
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        store = PendingSensitiveActionStore(self._pending_store_path("folder-plan-token-all-batches"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(
                self._receipt_store_path("folder-plan-token-all-batches")
            ),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: False
        try:
            pending = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza la carpeta objetivo",
                action_kind="folder_organizer",
                skill_id="dev.azulclaw.desktop-organizer",
                tool_name="organize_target_folder",
                tool_arguments={"recursive": True, "plan_token": "abc123"},
            )

            class _FakeToolText:
                def __init__(self, text: str) -> None:
                    self.text = text

            class _FakeToolResult:
                def __init__(self, text: str) -> None:
                    self.content = [_FakeToolText(text)]

            mcp_client = type("_McpClient", (), {})()
            mcp_client.call_tool = AsyncMock(
                side_effect=[
                    _FakeToolResult(
                        json.dumps(
                            {
                                "relative_path": ".",
                                "summary": "2 file(s) moved. Presentations: 2.",
                                "remaining_batch_count": 1,
                                "plan_complete": False,
                            }
                        )
                    ),
                    _FakeToolResult(
                        json.dumps(
                            {
                                "relative_path": ".",
                                "summary": "1 file(s) moved. Other: 1.",
                                "remaining_batch_count": 0,
                                "plan_complete": True,
                            }
                        )
                    ),
                ]
            )
            orchestrator.mcp_client = mcp_client

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("across 2 batch(es)", reply.text)
            self.assertIn("Batch 1: 2 file(s) moved. Presentations: 2.", reply.text)
            self.assertIn("Batch 2: 1 file(s) moved. Other: 1.", reply.text)
            self.assertEqual(mcp_client.call_tool.await_count, 2)
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions-streaming" / "folder-plan-token-all-batches",
                ignore_errors=True,
            )

    async def test_folder_organizer_zero_moves_with_conflicts_is_not_marked_completed(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = None
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        store = PendingSensitiveActionStore(self._pending_store_path("folder-zero-moves-conflicts"))
        service = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(
                self._receipt_store_path("folder-zero-moves-conflicts")
            ),
        )
        orchestrator.pending_sensitive_actions = service
        orchestrator._folder_organizer_workflow_enabled = lambda: False
        try:
            pending = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza la carpeta objetivo",
                action_kind="folder_organizer",
                skill_id="dev.azulclaw.desktop-organizer",
                tool_name="organize_target_folder",
                tool_arguments={"recursive": True, "plan_token": "abc123"},
                plan_snapshot={
                    "tool_arguments": {"recursive": True, "plan_token": "abc123"},
                    "preview": {
                        "relative_path": ".",
                        "recursive": True,
                        "plan_token": "abc123",
                    },
                },
            )

            class _FakeToolText:
                def __init__(self, text: str) -> None:
                    self.text = text

            class _FakeToolResult:
                def __init__(self, text: str) -> None:
                    self.content = [_FakeToolText(text)]

            mcp_client = type("_McpClient", (), {})()
            mcp_client.call_tool = AsyncMock(
                return_value=_FakeToolResult(
                    json.dumps(
                        {
                            "relative_path": ".",
                            "summary": "No files are ready to organize. 1 blocked by conflicts. Other: 1. Blocked files: Escritorio/kdenlive-25.04.1_standalone/LICENSE.",
                            "moved_count": 0,
                            "blocked_count": 1,
                        }
                    )
                )
            )
            orchestrator.mcp_client = mcp_client

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertEqual(reply.turn_status, "tool_failure")
            self.assertIn("did not apply any changes", reply.text)
            record = service.pending_store.approval_service.get_by_action_id(pending.id)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.status, "failed")
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions-streaming" / "folder-zero-moves-conflicts",
                ignore_errors=True,
            )

    async def test_duplicate_folder_organizer_approval_reuses_cached_result(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = None
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        store = PendingSensitiveActionStore(self._pending_store_path("folder-duplicate-approve"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(
                self._receipt_store_path("folder-duplicate-approve")
            ),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: False
        try:
            pending = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza la carpeta objetivo",
                action_kind="folder_organizer",
                skill_id="dev.azulclaw.desktop-organizer",
                tool_name="organize_target_folder",
                tool_arguments={"recursive": True, "plan_token": "abc123"},
            )

            class _FakeToolText:
                def __init__(self, text: str) -> None:
                    self.text = text

            class _FakeToolResult:
                def __init__(self, text: str) -> None:
                    self.content = [_FakeToolText(text)]

            mcp_client = type("_McpClient", (), {})()
            mcp_client.call_tool = AsyncMock(
                return_value=_FakeToolResult(
                    '{"relative_path": ".", "summary": "3 file(s) moved. Documents: 2, Images: 1."}'
                )
            )
            orchestrator.mcp_client = mcp_client

            first = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )
            second = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            assert first is not None and second is not None
            self.assertEqual(first.text, second.text)
            mcp_client.call_tool.assert_awaited_once()
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions-streaming" / "folder-duplicate-approve",
                ignore_errors=True,
            )

    async def test_folder_plan_streaming_waits_for_finalized_reply_when_preview_preflight_runs(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = type(
            "_Memory",
            (),
            {
                "_conn": object(),
                "get_conversation_messages": staticmethod(lambda _conversation_id, limit=12: []),
                "get_history": staticmethod(lambda _user_id, limit=12: []),
                "get_conversation_title": staticmethod(lambda _conversation_id: ""),
            },
        )()
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-stream-finalized")),
            preview_store=FolderOrganizerPreviewStore(self._preview_store_path("folder-stream-finalized")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-stream-finalized")),
        )
        orchestrator.heartbeat_intents = type("_Heartbeat", (), {"handle_message": AsyncMock(return_value=None)})()
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **kwargs: ("", [], False, kwargs["lane"])
        orchestrator.resolve_route_async = AsyncMock(
            return_value=TriageDecision(lane="slow", reason="complex-request")
        )
        orchestrator._judge_folder_organizer_request = AsyncMock(
            return_value=FolderOrganizerRequestVerdict(decision="plan_request", reason="plan")
        )
        orchestrator._should_generate_conversation_title = lambda *_args, **_kwargs: False
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator._try_handle_sensitive_action_confirmation_attempt = AsyncMock(return_value=None)
        orchestrator._try_recover_folder_approval_from_preview = AsyncMock(return_value=None)
        orchestrator._consume_pending_action_follow_up_context = AsyncMock(return_value=[])
        orchestrator._is_confirming_sensitive_action = lambda history, user_message: False
        orchestrator._enforce_turn_closure = AsyncMock(
            side_effect=lambda **kwargs: kwargs["reply"]
        )
        orchestrator.generate_fast_visible_plan = AsyncMock(
            return_value=(
                "Preview ready",
                {
                    "title": "Preview ready",
                    "badge": "Slow brain",
                    "summary": {"thinking": "Preview ready"},
                    "phases": [],
                },
            )
        )
        orchestrator.build_agent_messages = lambda *args, **kwargs: []
        orchestrator.invoke_messages = AsyncMock(
            return_value=ConversationReply(
                text="Final reviewed folder plan based on the real preview.",
                lane="slow",
                turn_status="final_answer",
            )
        )
        orchestrator.invoke_messages_stream = AsyncMock(
            side_effect=AssertionError("process_user_message_stream should not leak provisional stream output here")
        )

        class _FakeToolText:
            def __init__(self, text: str) -> None:
                self.text = text

        class _FakeToolResult:
            def __init__(self, text: str) -> None:
                self.content = [_FakeToolText(text)]

        mcp_client = type("_McpClient", (), {})()
        mcp_client.call_tool = AsyncMock(
            return_value=_FakeToolResult(
                json.dumps(
                    {
                        "relative_path": ".",
                        "recursive": True,
                        "summary": "No files are ready to organize. 1 blocked by conflicts. Other: 1.",
                    }
                )
            )
        )
        orchestrator.mcp_client = mcp_client

        deltas: list[str] = []
        commentaries: list[str] = []
        progress_events: list[tuple[str, dict]] = []

        try:
            with unittest.mock.patch(
                "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
                return_value=[],
            ):
                reply = await orchestrator.process_user_message_stream(
                    "desktop-user",
                    "Dame un plan con Folder Organizer",
                    conversation_id="conv-1",
                    on_delta=lambda text: deltas.append(text) or asyncio.sleep(0),
                    on_commentary=lambda text: commentaries.append(text) or asyncio.sleep(0),
                    on_progress=lambda event_type, progress: progress_events.append((event_type, progress)) or asyncio.sleep(0),
                )

            self.assertEqual(reply.text, "Final reviewed folder plan based on the real preview.")
            self.assertEqual(deltas, ["Final reviewed folder plan based on the real preview."])
            orchestrator.invoke_messages.assert_awaited_once()
            orchestrator.invoke_messages_stream.assert_not_awaited()
            mcp_client.call_tool.assert_awaited_once()
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions-streaming" / "folder-stream-finalized",
                ignore_errors=True,
            )

    async def test_stale_folder_organizer_approval_returns_safe_message_instead_of_not_found(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = None
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        store = PendingSensitiveActionStore(self._pending_store_path("folder-stale-approve"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(
                self._receipt_store_path("folder-stale-approve")
            ),
        )
        try:
            pending = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
                source_user_message="Organiza la carpeta objetivo",
                action_kind="folder_organizer",
                skill_id="dev.azulclaw.desktop-organizer",
                tool_name="organize_target_folder",
                tool_arguments={"recursive": True, "plan_token": "abc123"},
            )
            store.pop_for_user("desktop-user", status="superseded")

            reply = await orchestrator._try_handle_pending_action_decision(
                "desktop-user",
                pending.id,
                "approve",
                conversation_id="conv-1",
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("replaced by a newer reviewed plan", reply.text)
            self.assertEqual(reply.turn_status, "final_answer")
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions-streaming" / "folder-stale-approve",
                ignore_errors=True,
            )

    async def test_confirmation_turn_forces_slow_lane_and_execution_instruction(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = type(
            "_Memory",
            (),
            {"get_conversation_title": staticmethod(lambda _conversation_id: None)},
        )()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="fast",
            reason="short-utterance",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: [
            {
                "role": "assistant",
                "content": "Esto implica mover archivos. ¿Confirmas que proceda?",
            }
        ]
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, "slow")
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator._should_generate_conversation_title = lambda *_args, **_kwargs: False
        orchestrator.generate_fast_visible_plan = AsyncMock(
            return_value=(
                "Processing the confirmed action now.",
                {
                    "title": "Plan",
                    "badge": "Slow brain",
                    "summary": {"thinking": "Processing the confirmed action now."},
                    "phases": [],
                },
            )
        )

        captured: dict[str, object] = {}

        def build_agent_messages(*args, **kwargs):
            captured["confirmed_sensitive_action"] = kwargs.get("confirmed_sensitive_action")
            return []

        async def invoke_messages_stream(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            on_delta,
            tools_enabled: bool,
        ) -> ConversationReply:
            captured["lane"] = lane
            await on_delta("done")
            return ConversationReply(text="done", lane=lane)

        orchestrator.build_agent_messages = build_agent_messages
        orchestrator.invoke_messages_stream = invoke_messages_stream

        await orchestrator.process_user_message_stream(
            "desktop-user",
            "Sí, hazlo",
            conversation_id="conv-1",
            on_delta=AsyncMock(),
            on_commentary=AsyncMock(),
            on_progress=AsyncMock(),
        )

        self.assertEqual(captured["lane"], "slow")
        self.assertFalse(captured["confirmed_sensitive_action"])

    async def test_fast_stream_emits_immediate_commentary_before_context_lookup(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="fast",
            reason="short-question",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: []

        lifecycle: list[str] = []
        commentary_events: list[str] = []
        delta_events: list[str] = []
        progress_events: list[tuple[str, dict]] = []

        async def retrieve_semantic_memories(_user_id: str, _user_message: str) -> list[dict]:
            lifecycle.append("semantic")
            return []

        async def invoke_messages_stream(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            on_delta,
            tools_enabled: bool,
        ) -> ConversationReply:
            lifecycle.append("invoke")
            await on_delta("final answer")
            return ConversationReply(text="final answer", lane=lane)

        async def persist_with_vector_memory(*_args, **_kwargs):
            return None

        async def on_commentary(text: str) -> None:
            commentary_events.append(text)
            lifecycle.append("commentary")

        async def on_delta(text: str) -> None:
            delta_events.append(text)

        async def on_progress(event_type: str, progress: dict) -> None:
            progress_events.append((event_type, progress))
            lifecycle.append("progress")

        orchestrator.retrieve_semantic_memories = retrieve_semantic_memories
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, "fast")
        orchestrator.build_agent_messages = lambda *_args, **_kwargs: []
        orchestrator.invoke_messages_stream = invoke_messages_stream
        orchestrator.persist_with_vector_memory = persist_with_vector_memory
        orchestrator._should_generate_conversation_title = lambda *_args, **_kwargs: False

        reply = await orchestrator.process_user_message_stream(
            "desktop-user",
            "Que hora es?",
            on_delta=on_delta,
            on_commentary=on_commentary,
            on_progress=on_progress,
        )

        self.assertEqual(
            commentary_events,
            [build_commentary("Que hora es?", reason="short-question", lane="fast")],
        )
        self.assertEqual(lifecycle[:3], ["commentary", "progress", "semantic"])
        self.assertEqual(
            [item[0] for item in progress_events],
            ["progress-init", "progress-update", "progress-update"],
        )
        self.assertEqual(progress_events[0][1]["lane"], "fast")
        self.assertEqual(progress_events[0][1]["lane_label"], "Fast brain")
        self.assertEqual(progress_events[0][1]["triage_reason"], "short-question")
        self.assertEqual(progress_events[0][1]["reason_label"], "Quick question")
        self.assertEqual(progress_events[0][1]["current_step_label"], "Interpret the request")
        self.assertTrue(progress_events[0][1]["started_at"])
        self.assertTrue(progress_events[0][1]["last_updated_at"])
        self.assertEqual(progress_events[1][1]["current_step_label"], "Waiting for model response")
        self.assertEqual(progress_events[2][1]["current_step_label"], "Streaming answer")
        self.assertEqual(delta_events, ["final answer"])
        self.assertEqual(reply.text, "final answer")
        self.assertEqual(reply.lane, "fast")
        self.assertEqual(reply.triage_reason, "short-question")

    async def test_heartbeat_intent_stream_still_emits_progress_card(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(
            return_value=ConversationReply(
                text="Confirmed.",
                lane="fast",
                triage_reason="heartbeat-intent",
            )
        )

        commentary_events: list[str] = []
        delta_events: list[str] = []
        progress_events: list[tuple[str, dict]] = []

        async def on_commentary(text: str) -> None:
            commentary_events.append(text)

        async def on_delta(text: str) -> None:
            delta_events.append(text)

        async def on_progress(event_type: str, progress: dict) -> None:
            progress_events.append((event_type, progress))

        reply = await orchestrator.process_user_message_stream(
            "desktop-user",
            "yes, do it",
            on_delta=on_delta,
            on_commentary=on_commentary,
            on_progress=on_progress,
        )

        self.assertEqual(commentary_events, ["Processing that request now."])
        self.assertEqual(delta_events, ["Confirmed."])
        self.assertEqual([item[0] for item in progress_events], ["progress-init"])
        self.assertEqual(progress_events[0][1]["current_step_label"], "Processing confirmation")
        self.assertEqual(reply.text, "Confirmed.")
        self.assertEqual(reply.triage_reason, "heartbeat-intent")

    async def test_stream_attachment_error_persists_user_and_error_reply(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="fast",
            reason="complex-marker",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: []
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: (_ for _ in ()).throw(AttachmentError("Attachment not found: att-1"))
        orchestrator.persist_with_vector_memory = AsyncMock(return_value=None)

        delta_events: list[str] = []

        reply = await orchestrator.process_user_message_stream(
            "desktop-user",
            "Check this attachment",
            conversation_id="conv-1",
            attachment_ids=["att-1"],
            on_delta=lambda text: _collect(delta_events, text),
            on_commentary=AsyncMock(),
            on_progress=AsyncMock(),
        )

        self.assertEqual(delta_events, ["Attachment not found: att-1"])
        self.assertEqual(reply.text, "Attachment not found: att-1")
        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[0].args,
            ("desktop-user", "user", "Check this attachment"),
        )
        self.assertEqual(orchestrator.persist_with_vector_memory.await_args_list[0].kwargs["conversation_id"], "conv-1")
        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[1].args,
            ("desktop-user", "assistant", "Attachment not found: att-1"),
        )
        self.assertEqual(orchestrator.persist_with_vector_memory.await_args_list[1].kwargs["conversation_id"], "conv-1")

    async def test_stream_attachment_error_does_not_emit_streaming_answer_progress(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="fast",
            reason="complex-marker",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: []
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: (_ for _ in ()).throw(
            AttachmentError("Attachment not found: att-1")
        )
        orchestrator.persist_with_vector_memory = AsyncMock(return_value=None)

        progress_events: list[tuple[str, dict]] = []

        async def on_progress(event_type: str, progress: dict) -> None:
            progress_events.append((event_type, progress))

        await orchestrator.process_user_message_stream(
            "desktop-user",
            "Check this attachment",
            conversation_id="conv-1",
            attachment_ids=["att-1"],
            on_delta=AsyncMock(),
            on_commentary=AsyncMock(),
            on_progress=on_progress,
        )

        self.assertEqual(
            [item[0] for item in progress_events],
            ["progress-init", "progress-update", "progress-done"],
        )
        self.assertEqual(progress_events[-1][1]["current_step_label"], "Preparation failed")
        self.assertNotIn(
            "Streaming answer",
            [item[1].get("current_step_label", "") for item in progress_events],
        )

    async def test_stream_disconnect_after_partial_delta_persists_user_and_partial_reply(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="fast",
            reason="complex-marker",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: []
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, "fast")
        orchestrator.build_agent_messages = lambda *_args, **_kwargs: []
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator._should_generate_conversation_title = lambda *_args, **_kwargs: False

        async def invoke_messages_stream(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            on_delta,
            tools_enabled: bool,
        ) -> ConversationReply:
            await on_delta("partial answer")
            return ConversationReply(text="partial answer", lane=lane)

        async def disconnecting_delta(_text: str) -> None:
            raise _ClientDisconnected("socket closed")

        orchestrator.invoke_messages_stream = invoke_messages_stream

        with self.assertRaises(_ClientDisconnected):
            await orchestrator.process_user_message_stream(
                "desktop-user",
                "Tell me something",
                conversation_id="conv-1",
                on_delta=disconnecting_delta,
                on_commentary=AsyncMock(),
                on_progress=AsyncMock(),
            )

        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[0].args,
            ("desktop-user", "user", "Tell me something"),
        )
        self.assertEqual(orchestrator.persist_with_vector_memory.await_args_list[0].kwargs["conversation_id"], "conv-1")
        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[1].args,
            ("desktop-user", "assistant", "partial answer"),
        )
        self.assertEqual(orchestrator.persist_with_vector_memory.await_args_list[1].kwargs["conversation_id"], "conv-1")

    async def test_stream_disconnect_during_commentary_still_persists_user_turn(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="fast",
            reason="short-question",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: []
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, "fast")
        orchestrator.build_agent_messages = lambda *_args, **_kwargs: []
        orchestrator.invoke_messages_stream = AsyncMock()
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator._should_generate_conversation_title = lambda *_args, **_kwargs: False

        async def disconnecting_commentary(_text: str) -> None:
            raise _ClientDisconnected("socket closed")

        with self.assertRaises(_ClientDisconnected):
            await orchestrator.process_user_message_stream(
                "desktop-user",
                "Hello there",
                conversation_id="conv-1",
                on_delta=AsyncMock(),
                on_commentary=disconnecting_commentary,
                on_progress=AsyncMock(),
            )

        self.assertEqual(len(orchestrator.persist_with_vector_memory.await_args_list), 1)
        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[0].args,
            ("desktop-user", "user", "Hello there"),
        )
        self.assertEqual(orchestrator.persist_with_vector_memory.await_args_list[0].kwargs["conversation_id"], "conv-1")
        orchestrator.invoke_messages_stream.assert_not_awaited()

    async def test_slow_commentary_loop_failure_does_not_abort_reply(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = type("MemoryStub", (), {"get_conversation_title": lambda self, _conversation_id: "Current chat"})()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="slow",
            reason="complex-marker",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: []
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, "slow")
        orchestrator.build_agent_messages = lambda *_args, **_kwargs: []
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator._should_generate_conversation_title = lambda *_args, **_kwargs: False
        orchestrator.generate_fast_visible_plan = AsyncMock(
            return_value=("Planning the response.", {"title": "Plan", "badge": "Slow brain", "summary": {"thinking": "Planning the response."}, "phases": []})
        )

        async def invoke_messages_stream(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            on_delta,
            tools_enabled: bool,
        ) -> ConversationReply:
            await on_delta("final answer")
            return ConversationReply(text="final answer", lane=lane)

        async def broken_slow_commentary_loop(*_args, **_kwargs) -> None:
            raise RuntimeError("commentary boom")

        delta_events: list[str] = []

        orchestrator.invoke_messages_stream = invoke_messages_stream
        orchestrator._slow_commentary_loop = broken_slow_commentary_loop

        reply = await orchestrator.process_user_message_stream(
            "desktop-user",
            "Need a detailed plan",
            conversation_id="conv-1",
            on_delta=lambda text: _collect(delta_events, text),
            on_commentary=AsyncMock(),
            on_progress=AsyncMock(),
        )

        self.assertEqual(delta_events, ["final answer"])
        self.assertEqual(reply.text, "final answer")
        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[1].args,
            ("desktop-user", "assistant", "final answer"),
        )

    async def test_non_stream_attachment_error_persists_user_and_error_reply(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.memory = object()
        orchestrator._try_handle_heartbeat_intent = AsyncMock(return_value=None)
        orchestrator.resolve_route = lambda _message, _lane="auto": TriageDecision(
            lane="fast",
            reason="complex-marker",
        )
        orchestrator._load_chat_history = lambda _user_id, _conversation_id, limit=12: []
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: (_ for _ in ()).throw(AttachmentError("Attachment not found: att-1"))
        orchestrator.persist_with_vector_memory = AsyncMock(return_value=None)

        reply = await orchestrator.process_user_message(
            "desktop-user",
            "Check this attachment",
            conversation_id="conv-1",
            attachment_ids=["att-1"],
        )

        self.assertEqual(reply.text, "Attachment not found: att-1")
        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[0].args,
            ("desktop-user", "user", "Check this attachment"),
        )
        self.assertEqual(orchestrator.persist_with_vector_memory.await_args_list[0].kwargs["conversation_id"], "conv-1")
        self.assertEqual(
            orchestrator.persist_with_vector_memory.await_args_list[1].args,
            ("desktop-user", "assistant", "Attachment not found: att-1"),
        )
        self.assertEqual(orchestrator.persist_with_vector_memory.await_args_list[1].kwargs["conversation_id"], "conv-1")


async def _collect(bucket: list[str], text: str) -> None:
    bucket.append(text)


if __name__ == "__main__":
    unittest.main()

