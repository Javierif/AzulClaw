from __future__ import annotations

import asyncio
import json
import shutil
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock
from types import SimpleNamespace

from azul_backend.azul_brain.conversation import (
    CapabilityContractVerdict,
    FolderOrganizerPreviewContextVerdict,
    PendingActionStageVerdict,
    PendingActionUserIntentVerdict,
    FolderOrganizerRequestVerdict,
    TurnClosureVerdict,
    TURN_CLOSURE_FAILURE_TEXT,
    ConversationOrchestrator,
    ConversationReply,
)
from azul_backend.azul_brain.runtime.pending_action_intent import (
    FolderOrganizerPreviewStore,
    PendingSensitiveActionService,
    PendingSensitiveExecutionReceiptStore,
    PendingSensitiveActionStore,
    maybe_record_folder_organizer_preview,
    pending_sensitive_action_capture_context,
)
from azul_backend.azul_brain.runtime.approval_service import ApprovalService
from azul_backend.azul_brain.runtime.skill_workflow_runtime import (
    SkillWorkflowEvent,
    SkillWorkflowRuntime,
    SkillWorkflowStore,
)


class RamOnlyMemory:
    _conn = None

    def get_conversation_messages(self, conversation_id: str, limit: int = 12) -> list[dict]:
        return []

    def get_history(self, user_id: str, limit: int = 12) -> list[dict]:
        return [{"role": "assistant", "content": "previous RAM reply"}]

    def get_conversation_title(self, conversation_id: str) -> str | None:
        return None


class ConversationHistoryTests(unittest.TestCase):
    def _pending_store_path(self, name: str) -> Path:
        root = Path("memory") / "test-pending-actions" / name
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root / "pending.json"

    def _preview_store_path(self, name: str) -> Path:
        root = Path("memory") / "test-pending-actions" / f"{name}-preview"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root / "preview.json"

    def _receipt_store_path(self, name: str) -> Path:
        root = Path("memory") / "test-pending-actions" / f"{name}-receipt"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)
        return root / "receipt.json"

    def test_conversation_history_falls_back_to_ram_when_sqlite_unavailable(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.memory = RamOnlyMemory()

        history = orchestrator._load_chat_history(
            "desktop-user",
            "conv-1",
            limit=12,
        )

        self.assertEqual(history, [{"role": "assistant", "content": "previous RAM reply"}])

    def test_sensitive_confirmation_detected_from_last_assistant_turn(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        history = [
            {
                "role": "assistant",
                "content": "Esto implica mover archivos. Â¿Confirmas que proceda y ejecute la organizaciÃ³n?",
            }
        ]

        self.assertFalse(orchestrator._is_confirming_sensitive_action(history, "SÃ­, hazlo"))

    def test_sensitive_confirmation_not_detected_without_confirmation_prompt(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        history = [{"role": "assistant", "content": "Puedo ayudarte a revisar esa carpeta."}]

        self.assertFalse(orchestrator._is_confirming_sensitive_action(history, "SÃ­, hazlo"))

    def test_sensitive_confirmation_reply_is_wrapped_in_pending_action_card(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(
                decision="approval_ready",
                action_kind="generic",
                title="Folder Organizer",
                summary="Approve applying the proposed folder organization changes.",
            )
        )
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("generic-card")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("generic-card")),
        )
        try:
            reply = asyncio.run(orchestrator._maybe_stage_sensitive_action_card(
                user_id="desktop-user",
                conversation_id="conv-1",
                user_message="Necesito que organices la carpeta objetivo",
                reply_text="Esto implica mover archivos. Â¿Confirmas que proceda con la organizaciÃ³n?",
                allow_pending_action_staging=True,
            ))

            self.assertIn("[PENDING_ACTION:approval]", reply)
            self.assertIn("ActionKind: generic", reply)
            self.assertIn("Folder Organizer", reply)
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "generic-card", ignore_errors=True)

    def test_folder_organizer_structured_block_is_staged_with_tool_arguments(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        store = PendingSensitiveActionStore(self._pending_store_path("folder-structured"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-structured")),
        )
        try:
            reply = asyncio.run(orchestrator._maybe_stage_sensitive_action_card(
                user_id="desktop-user",
                conversation_id="conv-1",
                user_message="Organiza la carpeta objetivo",
                reply_text=(
                    "He preparado el plan.\n\n"
                    "[PENDING_ACTION:folder_organizer]\n"
                    "Title: Folder Organizer\n"
                    "Summary: Approve applying the proposed folder organization changes.\n"
                    "SkillId: dev.azulclaw.desktop-organizer\n"
                    "ToolName: organize_target_folder\n"
                    "ArgumentsJson: {\"recursive\": true, \"plan_token\": \"abc123\"}\n"
                    "ApproveLabel: Apply changes\n"
                    "RejectLabel: Cancel\n"
                    "[/PENDING_ACTION]"
                ),
                allow_pending_action_staging=True,
            ))
            pending = store.get_for_user("desktop-user")

            self.assertIn("[PENDING_ACTION:approval]", reply)
            self.assertIn("ActionKind: folder_organizer", reply)
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.action_kind, "folder_organizer")
            self.assertEqual(pending.skill_id, "dev.azulclaw.desktop-organizer")
            self.assertEqual(pending.tool_name, "organize_target_folder")
            self.assertEqual(pending.tool_arguments, {"recursive": True, "plan_token": "abc123"})
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-structured", ignore_errors=True)

    def test_folder_organizer_confirmation_uses_latest_preview_when_block_is_missing(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        pending_store = PendingSensitiveActionStore(self._pending_store_path("folder-preview-derived"))
        preview_store = FolderOrganizerPreviewStore(self._preview_store_path("folder-preview-derived"))
        preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            recursive=True,
            max_depth=2,
            plan_token="plan-123",
            category_overrides={"LoL+IA.pptx": "TFG - LoL e IA"},
            summary="4 file(s) ready to organize. Presentations: 3, Other: 1.",
        )
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            pending_store=pending_store,
            preview_store=preview_store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-preview-derived")),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: False
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="approval_ready", action_kind="folder_organizer")
        )
        try:
            reply = asyncio.run(orchestrator._maybe_stage_sensitive_action_card(
                user_id="desktop-user",
                conversation_id="conv-1",
                user_message="Organiza la carpeta objetivo",
                reply_text="Esto implica mover archivos. Â¿Confirmas que proceda con la organizaciÃ³n?",
                allow_pending_action_staging=True,
            ))
            pending = pending_store.get_for_user("desktop-user")

            self.assertIn("[PENDING_ACTION:approval]", reply)
            self.assertIn("ActionKind: folder_organizer", reply)
            self.assertIn("ExecutionBinding: Reviewed preview", reply)
            self.assertIn("Batches: Apply full reviewed plan", reply)
            self.assertIn("PreviewSummary: 4 file(s) ready to organize.", reply)
            self.assertIn("PreviewMode: Recursive preview", reply)
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertEqual(pending.action_kind, "folder_organizer")
            self.assertEqual(
                pending.tool_arguments,
                {
                    "recursive": True,
                    "plan_token": "plan-123",
                    "category_overrides": {"LoL+IA.pptx": "TFG - LoL e IA"},
                },
            )
            self.assertIsInstance(pending.plan_snapshot, dict)
            self.assertTrue(pending.plan_hash)
            self.assertTrue(pending.idempotency_key)
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-preview-derived", ignore_errors=True)

    def test_textual_apply_after_reviewed_preview_recovers_missing_approval_card(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        pending_store = PendingSensitiveActionStore(self._pending_store_path("folder-preview-recovery"))
        preview_store = FolderOrganizerPreviewStore(self._preview_store_path("folder-preview-recovery"))
        preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            recursive=True,
            max_depth=2,
            plan_token="plan-123",
            summary="4 file(s) ready to organize. Presentations: 3, Other: 1.",
        )
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            pending_store=pending_store,
            preview_store=preview_store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-preview-recovery")),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: False
        orchestrator._judge_pending_action_user_intent = AsyncMock(
            return_value=PendingActionUserIntentVerdict(decision="approve")
        )
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        try:
            reply = asyncio.run(
                orchestrator._try_recover_folder_approval_from_preview(
                    "desktop-user",
                    "Sí, aplica este plan",
                    conversation_id="conv-1",
                )
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertEqual(reply.turn_status, "approval_required")
            self.assertIn("[PENDING_ACTION:approval]", reply.text)
            self.assertIn("ActionKind: folder_organizer", reply.text)
            pending = pending_store.get_for_user("desktop-user")
            self.assertIsNotNone(pending)
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-preview-recovery", ignore_errors=True)

    def test_enabled_folder_workflow_keeps_legacy_preview_recovery_read_only(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        pending_store = PendingSensitiveActionStore(self._pending_store_path("folder-preview-recovery-read-only"))
        preview_store = FolderOrganizerPreviewStore(self._preview_store_path("folder-preview-recovery-read-only"))
        preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            recursive=True,
            max_depth=2,
            plan_token="plan-123",
            summary="4 file(s) ready to organize. Presentations: 3, Other: 1.",
        )
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            pending_store=pending_store,
            preview_store=preview_store,
            receipt_store=PendingSensitiveExecutionReceiptStore(
                self._receipt_store_path("folder-preview-recovery-read-only")
            ),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: True
        orchestrator._judge_pending_action_user_intent = AsyncMock(
            return_value=PendingActionUserIntentVerdict(decision="approve")
        )
        try:
            reply = asyncio.run(
                orchestrator._try_recover_folder_approval_from_preview(
                    "desktop-user",
                    "Sí, aplica este plan",
                    conversation_id="conv-1",
                )
            )

            self.assertIsNone(reply)
            self.assertIsNone(pending_store.get_for_user("desktop-user"))
            orchestrator._judge_pending_action_user_intent.assert_not_awaited()
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions" / "folder-preview-recovery-read-only",
                ignore_errors=True,
            )

    def test_blocking_request_for_missing_folder_path_does_not_stage_approval_card(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        store = PendingSensitiveActionStore(self._pending_store_path("folder-blocking-input"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-blocking-input")),
        )
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="blocking_input")
        )
        try:
            reply = asyncio.run(orchestrator._maybe_stage_sensitive_action_card(
                user_id="desktop-user",
                conversation_id="conv-1",
                user_message="Dame el plan",
                reply_text=(
                    "Voy a hacerlo con datos reales, pero necesito la ruta exacta de tu carpeta objetivo.\n\n"
                    "Dime una de estas dos cosas:\n"
                    "1. La ruta exacta.\n"
                    "2. El id de la carpeta.\n\n"
                    "Y luego te pedirÃƒÂ© confirmaciÃƒÂ³n para aplicar cambios."
                ),
                allow_pending_action_staging=True,
            ))

            self.assertNotIn("[PENDING_ACTION:approval]", reply)
            self.assertIsNone(store.get_for_user("desktop-user"))
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-blocking-input", ignore_errors=True)

    def test_multi_question_plan_refinement_does_not_stage_approval_card(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        store = PendingSensitiveActionStore(self._pending_store_path("folder-multi-question"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-multi-question")),
        )
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="blocking_input")
        )
        try:
            reply = asyncio.run(orchestrator._maybe_stage_sensitive_action_card(
                user_id="desktop-user",
                conversation_id="conv-1",
                user_message="CamelCase",
                reply_text=(
                    "ConfirmaciÃ³n rÃ¡pida (para dejar el plan listo)\n\n"
                    "1. Â¿Quieres que Presentaciones siga global o vaya dentro de Eventos/BizzSummit?\n"
                    "2. Para Guaranpis: Â¿todo el desarrollo va a Juegos/Guaranpis?\n\n"
                    "RespÃ³ndeme esas 2 y ya te armo el mapeo final carpeta actual â†’ carpeta destino listo para ejecutar."
                ),
                allow_pending_action_staging=True,
            ))

            self.assertNotIn("[PENDING_ACTION:approval]", reply)
            self.assertIsNone(store.get_for_user("desktop-user"))
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-multi-question", ignore_errors=True)

    def test_preview_permission_question_does_not_stage_approval_card(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        store = PendingSensitiveActionStore(self._pending_store_path("folder-preview-permission"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-preview-permission")),
        )
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="blocking_input")
        )
        try:
            reply = asyncio.run(orchestrator._maybe_stage_sensitive_action_card(
                user_id="desktop-user",
                conversation_id="conv-1",
                user_message="Guaranpis deberÃ­a unificarse",
                reply_text=(
                    "Perfecto: ajustarÃ© el plan para que BizzSummit vaya a Eventos y Guaranpis quede unificado.\n\n"
                    "Antes de mover nada, Â¿me confirmas que puedo hacer el preview y proponer el Ã¡rbol final con la skill folder organizer?"
                ),
                allow_pending_action_staging=True,
            ))

            self.assertNotIn("[PENDING_ACTION:approval]", reply)
            self.assertIsNone(store.get_for_user("desktop-user"))
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-preview-permission", ignore_errors=True)

    def test_legacy_folder_preview_block_executes_preview_instead_of_staging_approval(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        store = PendingSensitiveActionStore(self._pending_store_path("folder-legacy-preview-block"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-legacy-preview-block")),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: False

        class _FakeToolText:
            def __init__(self, text: str) -> None:
                self.text = text

        class _FakeToolResult:
            def __init__(self, text: str) -> None:
                self.content = [_FakeToolText(text)]

        mcp_client = type("_McpClient", (), {})()
        mcp_client.call_tool = AsyncMock(
            return_value=_FakeToolResult(
                '{"relative_path": ".", "summary": "4 file(s) ready to organize. Documents: 2, Images: 2.", "workflow_hint": "Review the preview and then approve the execution card if you want me to move files."}'
            )
        )
        orchestrator.mcp_client = mcp_client

        reply = asyncio.run(
            orchestrator._maybe_execute_folder_organizer_preview_request(
                "Voy a pedir una previsualización.\n\n"
                "[PENDING_ACTION:folder_organizer]\n"
                "Title: Folder Organizer (Preview/Dry-run)\n"
                "Summary: Preview only.\n"
                "SkillId: dev.azulclaw.desktop-organizer\n"
                "ToolName: organize_target_folder\n"
                "ArgumentsJson: {\"recursive\": true, \"dry_run\": true}\n"
                "ApproveLabel: Ejecutar previsualización\n"
                "RejectLabel: Cancelar\n"
                "[/PENDING_ACTION]"
            )
        )

        self.assertIn("Folder Organizer preview for `.`.", reply)
        self.assertIn("4 file(s) ready to organize.", reply)
        self.assertNotIn("[PENDING_ACTION:", reply)
        self.assertIsNone(store.get_for_user("desktop-user"))
        mcp_client.call_tool.assert_awaited_once_with(
            "preview_folder_organization",
            {"recursive": True, "include_moves": False},
            skill_id="dev.azulclaw.desktop-organizer",
        )
        shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-legacy-preview-block", ignore_errors=True)

    def test_enabled_folder_workflow_does_not_execute_legacy_preview_block(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        store = PendingSensitiveActionStore(self._pending_store_path("folder-legacy-preview-block-disabled"))
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            store,
            receipt_store=PendingSensitiveExecutionReceiptStore(
                self._receipt_store_path("folder-legacy-preview-block-disabled")
            ),
        )
        orchestrator._folder_organizer_workflow_enabled = lambda: True

        mcp_client = type("_McpClient", (), {})()
        mcp_client.call_tool = AsyncMock()
        orchestrator.mcp_client = mcp_client

        reply = asyncio.run(
            orchestrator._maybe_execute_folder_organizer_preview_request(
                "Voy a pedir una previsualización.\n\n"
                "[PENDING_ACTION:folder_organizer]\n"
                "Title: Folder Organizer (Preview/Dry-run)\n"
                "Summary: Preview only.\n"
                "SkillId: dev.azulclaw.desktop-organizer\n"
                "ToolName: organize_target_folder\n"
                "ArgumentsJson: {\"recursive\": true, \"dry_run\": true}\n"
                "ApproveLabel: Ejecutar previsualización\n"
                "RejectLabel: Cancelar\n"
                "[/PENDING_ACTION]"
            )
        )

        self.assertIn("installed workflow", reply)
        self.assertIn("did not run the legacy preview block", reply)
        self.assertNotIn("[PENDING_ACTION:", reply)
        self.assertIsNone(store.get_for_user("desktop-user"))
        mcp_client.call_tool.assert_not_awaited()
        shutil.rmtree(
            Path("memory") / "test-pending-actions" / "folder-legacy-preview-block-disabled",
            ignore_errors=True,
        )

    def test_folder_organizer_preview_recorder_persists_last_preview_for_context(self) -> None:
        preview_store = FolderOrganizerPreviewStore(self._preview_store_path("preview-recorder"))

        class _FakeToolText:
            def __init__(self, text: str) -> None:
                self.text = text

        class _FakeToolResult:
            def __init__(self, text: str) -> None:
                self.content = [_FakeToolText(text)]

        try:
            with pending_sensitive_action_capture_context("desktop-user", "conv-1"):
                maybe_record_folder_organizer_preview(
                    tool_name="preview_folder_organization",
                    arguments={
                        "recursive": True,
                        "category_overrides": {"LoL+IA.pptx": "TFG - LoL e IA"},
                    },
                    result=_FakeToolResult(
                        '{"relative_path": ".", "recursive": true, "max_depth": 2, "summary": "4 file(s) ready to organize.", "plan_token": "plan-123"}'
                    ),
                    preview_store=preview_store,
                )

            preview = preview_store.get_for_context("desktop-user", "conv-1")

            self.assertIsNotNone(preview)
            assert preview is not None
            self.assertEqual(preview.plan_token, "plan-123")
            self.assertTrue(preview.recursive)
            self.assertEqual(preview.max_depth, 2)
            self.assertEqual(preview.category_overrides, {"LoL+IA.pptx": "TFG - LoL e IA"})
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "preview-recorder-preview", ignore_errors=True)

    def test_follow_up_to_pending_folder_plan_revises_plan_in_same_turn(self) -> None:
        pending_store = PendingSensitiveActionStore(self._pending_store_path("folder-follow-up-revision"))
        preview_store = FolderOrganizerPreviewStore(self._preview_store_path("folder-follow-up-revision"))
        receipt_store = PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-follow-up-revision"))
        preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            recursive=True,
            max_depth=2,
            plan_token="plan-123",
            category_overrides={"BizzSummitES - Conquista sector Koprulu.pptx": "Presentaciones"},
            summary="59 file(s) ready to organize across 6 semantic categories.",
            preview_payload={
                "relative_path": ".",
                "recursive": True,
                "max_depth": 2,
                "plan_token": "plan-123",
                "summary": "59 file(s) ready to organize across 6 semantic categories.",
                "semantic_custom_categories": ["Proyectos", "Facturas", "Presentaciones"],
                "remaining_batch_count": 2,
            },
        )
        pending_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            title="Folder Organizer",
            summary="Approve applying the proposed folder organization changes.",
            source_user_message="Dame un plan de organizaciÃ³n semÃ¡ntico",
            action_kind="folder_organizer",
            skill_id="dev.azulclaw.desktop-organizer",
            tool_name="organize_target_folder",
            tool_arguments={"recursive": True, "plan_token": "plan-123"},
            plan_snapshot={
                "tool_arguments": {"recursive": True, "plan_token": "plan-123"},
                "preview": {
                    "relative_path": ".",
                    "recursive": True,
                    "max_depth": 2,
                    "plan_token": "plan-123",
                    "summary": "59 file(s) ready to organize across 6 semantic categories.",
                    "semantic_custom_categories": ["Proyectos", "Facturas", "Presentaciones"],
                    "remaining_batch_count": 2,
                },
            },
            plan_hash="hash-123",
            idempotency_key="idem-123",
        )

        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            pending_store=pending_store,
            preview_store=preview_store,
            receipt_store=receipt_store,
        )
        orchestrator._judge_folder_organizer_follow_up = AsyncMock(
            return_value=SimpleNamespace(decision="revise_plan")
        )
        orchestrator.preference_extractor = None
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, _kwargs["lane"])
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator._folder_organizer_workflow_enabled = lambda: False

        captured: dict[str, object] = {}

        def build_agent_messages(*args, **kwargs):
            captured["extra_system_messages"] = kwargs.get("extra_system_messages")
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
            return ConversationReply(
                text="AquÃ­ tienes el plan revisado. Esto implica mover archivos. Â¿Confirmas que proceda con la organizaciÃ³n?",
                lane=lane,
                triage_reason="pending-action-revision",
            )

        orchestrator.build_agent_messages = build_agent_messages
        orchestrator.invoke_messages = invoke_messages
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="approval_ready", action_kind="folder_organizer")
        )
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="fast", reason="short-question"))

        try:
            reply = asyncio.run(
                orchestrator.process_user_message(
                    "desktop-user",
                    (
                        "Bizzsummit es un evento y Guaranpis es mi juego; "
                        "ajusta el plan para que queden unificados por proyecto."
                    ),
                    conversation_id="conv-1",
                )
            )

            self.assertEqual(captured["lane"], "slow")
            extra_messages = captured["extra_system_messages"]
            self.assertIsInstance(extra_messages, list)
            assert isinstance(extra_messages, list)
            self.assertTrue(any("revising a previously reviewed Folder Organizer plan" in item for item in extra_messages))
            self.assertTrue(any("Do not say that you will inspect" in item for item in extra_messages))
            self.assertIn("[PENDING_ACTION:approval]", reply.text)
            self.assertIn("ActionKind: folder_organizer", reply.text)
            self.assertIn("RevisionLabel: Plan revised", reply.text)
            self.assertIn("ExecutionBinding: Reviewed preview", reply.text)
            self.assertIn("PreviewSummary: 59 file(s) ready to organize", reply.text)
            pending = pending_store.get_for_user("desktop-user")
            self.assertIsNotNone(pending)
            assert pending is not None
            self.assertNotEqual(pending.idempotency_key, "idem-123")
            self.assertEqual(pending.revision_label, "Plan revised")
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-follow-up-revision", ignore_errors=True)

    def test_irrelevant_follow_up_supersedes_pending_folder_plan(self) -> None:
        pending_store = PendingSensitiveActionStore(self._pending_store_path("folder-follow-up-ignore"))
        preview_store = FolderOrganizerPreviewStore(self._preview_store_path("folder-follow-up-ignore"))
        receipt_store = PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-follow-up-ignore"))
        preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            recursive=True,
            max_depth=2,
            plan_token="plan-123",
            summary="59 file(s) ready to organize.",
            preview_payload={"relative_path": ".", "recursive": True, "plan_token": "plan-123"},
        )
        original = pending_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            title="Folder Organizer",
            summary="Approve applying the proposed folder organization changes.",
            source_user_message="Dame un plan de organizaciÃ³n semÃ¡ntico",
            action_kind="folder_organizer",
            skill_id="dev.azulclaw.desktop-organizer",
            tool_name="organize_target_folder",
            tool_arguments={"recursive": True, "plan_token": "plan-123"},
        )

        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            pending_store=pending_store,
            preview_store=preview_store,
            receipt_store=receipt_store,
        )
        orchestrator._judge_folder_organizer_follow_up = AsyncMock(
            return_value=SimpleNamespace(decision="move_on")
        )

        try:
            guidance = asyncio.run(orchestrator._consume_pending_action_follow_up_context(
                "desktop-user",
                "conv-1",
                "Por cierto, Â¿quÃ© hora es?",
            ))

            self.assertTrue(any("Folder Organizer approval flow" in item for item in guidance))
            pending = pending_store.get_for_context("desktop-user", "conv-1")
            self.assertIsNone(pending)
            record = pending_store.approval_service.get_by_action_id(original.id)
            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.status, "superseded")
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-follow-up-ignore", ignore_errors=True)

    def test_incomplete_promissory_reply_is_rewritten_before_turn_closes(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("turn-closure-guard")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("turn-closure-guard")),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, _kwargs["lane"])
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="slow", reason="complex-request"))

        draft_calls: list[str] = []

        async def invoke_messages(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            tools_enabled: bool = True,
        ) -> ConversationReply:
            draft_calls.append(source)
            if source == "turn-closure-retry":
                return ConversationReply(text="AquÃ­ tienes el plan de organizaciÃ³n revisado.", lane=lane)
            return ConversationReply(text="Voy a prepararte un plan de organizaciÃ³n.", lane=lane)

        runtime_calls: list[dict[str, str]] = []

        async def execute_messages(**kwargs):
            runtime_calls.append(
                {
                    "lane": kwargs["lane"],
                    "source": kwargs["source"],
                    "title": kwargs["title"],
                }
            )
            if len(runtime_calls) >= 2:
                return SimpleNamespace(
                    text=json.dumps(
                        {
                            "turn_status": "final_answer",
                            "should_retry": False,
                            "reason": "The reply delivers the requested plan.",
                        }
                    )
                )
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "turn_status": "incomplete_promise",
                        "should_retry": True,
                        "reason": "The reply promises future work without delivering a result.",
                    }
                )
            )

        orchestrator.runtime_manager = SimpleNamespace(execute_messages=execute_messages)
        orchestrator.build_agent_messages = lambda *args, **kwargs: []
        orchestrator.invoke_messages = invoke_messages
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="slow", reason="complex-request"))
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="no_sensitive_action")
        )
        orchestrator._judge_folder_organizer_request = AsyncMock(return_value=None)
        try:
            reply = asyncio.run(
                orchestrator.process_user_message(
                    "desktop-user",
                    "Dame un plan de organizaciÃ³n para la carpeta objetivo",
                    conversation_id="conv-1",
                )
            )

            self.assertEqual(reply.text, "AquÃ­ tienes el plan de organizaciÃ³n revisado.")
            self.assertEqual(reply.turn_status, "final_answer")
            self.assertEqual(len(draft_calls), 2)
            self.assertEqual(draft_calls[-1], "turn-closure-retry")
            self.assertEqual(runtime_calls[0]["lane"], "fast")
            self.assertEqual(runtime_calls[0]["source"], "turn-closure-judge")
            self.assertEqual(runtime_calls[1]["source"], "turn-closure-judge")
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "turn-closure-guard", ignore_errors=True)

    def test_deterministic_turn_closure_fallback_retries_when_fast_judge_fails(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("turn-closure-fallback")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("turn-closure-fallback")),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, _kwargs["lane"])
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="slow", reason="complex-request"))

        draft_calls: list[str] = []

        async def invoke_messages(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            tools_enabled: bool = True,
        ) -> ConversationReply:
            draft_calls.append(source)
            if source == "turn-closure-retry":
                return ConversationReply(text="AquÃ­ tienes el plan final de organizaciÃ³n.", lane=lane)
            return ConversationReply(text="Voy a prepararte un plan de organizaciÃ³n.", lane=lane)

        async def execute_messages(**_kwargs):
            raise RuntimeError("fast judge unavailable")

        orchestrator.runtime_manager = SimpleNamespace(execute_messages=execute_messages)
        orchestrator.build_agent_messages = lambda *args, **kwargs: []
        orchestrator.invoke_messages = invoke_messages
        orchestrator._judge_folder_organizer_request = AsyncMock(return_value=None)
        try:
            reply = asyncio.run(
                orchestrator.process_user_message(
                    "desktop-user",
                    "Dame un plan de organizaciÃ³n para la carpeta objetivo",
                    conversation_id="conv-1",
                )
            )

            self.assertEqual(reply.text, TURN_CLOSURE_FAILURE_TEXT)
            self.assertEqual(reply.turn_status, "tool_failure")
            self.assertEqual(len(draft_calls), 2)
            self.assertEqual(draft_calls[-1], "turn-closure-retry")
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "turn-closure-fallback", ignore_errors=True)

    def test_folder_organizer_capability_guard_rewrites_invalid_path_and_unsupported_actions(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-capability-guard")),
            preview_store=FolderOrganizerPreviewStore(self._preview_store_path("folder-capability-guard")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-capability-guard")),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, _kwargs["lane"])
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="slow", reason="complex-request"))
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="no_sensitive_action")
        )
        orchestrator._judge_folder_organizer_capability_contract = AsyncMock(
            side_effect=[
                CapabilityContractVerdict(
                    decision="invalid",
                    should_retry=True,
                    reason="The reply asks for an exact path and claims unsupported runtime actions.",
                    guidance=(
                        "Do not ask for the configured root path again. "
                        "Do not claim you can create empty subfolder skeletons or activate semantic mode."
                    ),
                ),
                CapabilityContractVerdict(decision="valid"),
            ]
        )

        draft_calls: list[str] = []
        captured: dict[str, object] = {}

        async def invoke_messages(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            tools_enabled: bool = True,
        ) -> ConversationReply:
            draft_calls.append(source)
            if source == "turn-closure-retry":
                return ConversationReply(
                    text=(
                        "La carpeta objetivo ya es la configurada en la skill. "
                        "Ahora mismo no hay archivos existentes que mover, así que no hay una reorganización ejecutable todavía."
                    ),
                    lane=lane,
                )
            return ConversationReply(
                text=(
                    "Primero crearé subcarpetas vacías y activaré la organización semántica. "
                    "Pégame el path exacto de la carpeta objetivo."
                ),
                lane=lane,
            )

        async def execute_messages(**kwargs):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "turn_status": "final_answer",
                        "should_retry": False,
                        "reason": "The reply delivers a concrete, non-executable explanation.",
                    }
                )
            )

        def build_agent_messages(*args, **kwargs):
            captured["extra_system_messages"] = kwargs.get("extra_system_messages")
            return []

        orchestrator.runtime_manager = SimpleNamespace(execute_messages=execute_messages)
        orchestrator.build_agent_messages = build_agent_messages
        orchestrator.invoke_messages = invoke_messages

        try:
            reply = asyncio.run(
                orchestrator.process_user_message(
                    "desktop-user",
                    "Usa la skill folder organizer para organizar la carpeta objetivo",
                    conversation_id="conv-1",
                )
            )

            self.assertEqual(reply.turn_status, "final_answer")
            self.assertIn("configurada en la skill", reply.text)
            self.assertNotIn("path exacto", reply.text)
            self.assertEqual(draft_calls, ["chat", "turn-closure-retry"])
            extra_messages = captured["extra_system_messages"]
            self.assertIsInstance(extra_messages, list)
            assert isinstance(extra_messages, list)
            self.assertTrue(any("Capability contract correction" in item for item in extra_messages))
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-capability-guard", ignore_errors=True)

    def test_folder_organizer_plan_request_runs_preview_before_drafting(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-plan-preflight")),
            preview_store=FolderOrganizerPreviewStore(self._preview_store_path("folder-plan-preflight")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-plan-preflight")),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, _kwargs["lane"])
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="slow", reason="complex-request"))
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="approval_ready", action_kind="folder_organizer")
        )
        orchestrator._judge_folder_organizer_request = AsyncMock(
            return_value=FolderOrganizerRequestVerdict(decision="plan_request", reason="explicit folder organizer plan request")
        )
        orchestrator._judge_folder_organizer_capability_contract = AsyncMock(
            return_value=CapabilityContractVerdict(decision="valid")
        )

        class _FakeToolText:
            def __init__(self, text: str) -> None:
                self.text = text

        class _FakeToolResult:
            def __init__(self, text: str) -> None:
                self.content = [_FakeToolText(text)]

        mcp_client = type("_McpClient", (), {})()

        async def call_tool(tool_name, arguments, *, skill_id=None):
            orchestrator.pending_sensitive_actions.preview_store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                recursive=True,
                max_depth=1,
                summary="4 file(s) ready to organize. Documents: 2, Images: 2.",
                preview_payload={
                    "relative_path": ".",
                    "recursive": True,
                    "summary": "4 file(s) ready to organize. Documents: 2, Images: 2.",
                },
            )
            return _FakeToolResult(
                json.dumps(
                    {
                        "relative_path": ".",
                        "summary": "4 file(s) ready to organize. Documents: 2, Images: 2.",
                        "workflow_hint": "Review the preview and then approve the execution card if you want me to move files.",
                    }
                )
            )

        mcp_client.call_tool = AsyncMock(side_effect=call_tool)
        orchestrator.mcp_client = mcp_client

        captured: dict[str, object] = {}

        def build_agent_messages(*args, **kwargs):
            captured["extra_system_messages"] = kwargs.get("extra_system_messages")
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
            return ConversationReply(
                text="Aquí tienes el plan revisado. ¿Confirmas que aplique estos cambios?",
                lane=lane,
            )

        orchestrator.runtime_manager = SimpleNamespace(execute_messages=AsyncMock(return_value=SimpleNamespace(text=json.dumps(
            {"turn_status": "action_pending", "should_retry": False, "reason": "approval required"}
        ))))
        orchestrator.build_agent_messages = build_agent_messages
        orchestrator.invoke_messages = invoke_messages

        try:
            with unittest.mock.patch(
                "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
                return_value=[],
            ):
                reply = asyncio.run(
                    orchestrator.process_user_message(
                        "desktop-user",
                        "Dame un plan de organización utilizando la skill de folder organizer",
                        conversation_id="conv-1",
                    )
                )

            mcp_client.call_tool.assert_awaited_once_with(
                "preview_folder_organization",
                {"recursive": True, "include_moves": False},
                skill_id="dev.azulclaw.desktop-organizer",
            )
            extra_messages = captured["extra_system_messages"]
            self.assertIsInstance(extra_messages, list)
            assert isinstance(extra_messages, list)
            self.assertTrue(any("A real Folder Organizer preview just ran" in item for item in extra_messages))
            self.assertTrue(any("4 file(s) ready to organize" in item for item in extra_messages))
            self.assertIn("[PENDING_ACTION:approval]", reply.text)
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-plan-preflight", ignore_errors=True)

    def test_folder_organizer_plan_request_then_acceptance_uses_workflow_hitl(self) -> None:
        root = Path("memory") / "test-pending-actions" / "folder-workflow-entry"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(root / "pending.json"),
            preview_store=FolderOrganizerPreviewStore(root / "preview.json"),
            receipt_store=PendingSensitiveExecutionReceiptStore(root / "receipt.json"),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.skill_workflow_runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        orchestrator.semantic_judges = SimpleNamespace(
            judge_skill_workflow_route=AsyncMock(
                return_value={
                    "decision": "run_workflow",
                    "skill_id": "dev.azulclaw.desktop-organizer",
                    "reason": "explicit skill request",
                }
            ),
            judge_skill_workflow_plan_follow_up=AsyncMock(
                return_value={
                    "decision": "approve_plan",
                    "reason": "user accepted the reviewed plan",
                }
            ),
        )
        orchestrator._judge_folder_organizer_request = AsyncMock()
        orchestrator.invoke_messages = AsyncMock()

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
                        "summary": "2 file(s) ready to organize.",
                        "recursive": True,
                        "moves": [
                            {"source": "invoice.pdf", "destination": "Documents/invoice.pdf", "status": "planned"},
                            {"source": "photo.jpg", "destination": "Images/photo.jpg", "status": "planned"},
                        ],
                    }
                )
            )
        )
        orchestrator.mcp_client = mcp_client

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
            "input_defaults": {
                "recursive": True,
                "preview_arguments": {"recursive": True, "include_moves": True},
            },
            "tool_policies": {
                "execute": {
                    "requires_approval": True,
                    "sensitive_action": "move_files",
                }
            },
            "sensitive_actions": ["move_files"],
        }

        try:
            with unittest.mock.patch(
                "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
                return_value=[workflow_spec],
            ):
                plan_reply = asyncio.run(
                    orchestrator.process_user_message(
                        "desktop-user",
                        "Dame un plan de organización utilizando la skill de folder organizer",
                        conversation_id="conv-1",
                    )
                )
                approval_reply = asyncio.run(
                    orchestrator.process_user_message(
                        "desktop-user",
                        "Me parece bien, puedes prepararlo para aplicarlo",
                        conversation_id="conv-1",
                    )
                )

            self.assertEqual(plan_reply.turn_status, "final_answer")
            self.assertEqual(plan_reply.triage_reason, "skill-workflow")
            self.assertIn("Organization plan generated", plan_reply.text)
            self.assertTrue(plan_reply.workflow_events)
            self.assertFalse(any(event.get("type") == "request_info" for event in plan_reply.workflow_events or []))
            self.assertEqual(approval_reply.turn_status, "approval_required")
            self.assertEqual(approval_reply.triage_reason, "skill-workflow-plan-approved")
            self.assertIn("Approval request", approval_reply.text)
            self.assertTrue(approval_reply.workflow_events)
            self.assertTrue(any(event.get("type") == "request_info" for event in approval_reply.workflow_events or []))
            mcp_client.call_tool.assert_awaited_once_with(
                "preview_folder_organization",
                {"recursive": True, "include_moves": True},
                skill_id="dev.azulclaw.desktop-organizer",
            )
            orchestrator.invoke_messages.assert_not_awaited()
            workflow_runs = orchestrator.skill_workflow_runtime.store.list_runs()
            self.assertEqual(len(workflow_runs), 2)
            self.assertEqual(
                sorted(run.status for run in workflow_runs),
                ["completed", "waiting_for_human"],
            )
            orchestrator.semantic_judges.judge_skill_workflow_route.assert_awaited_once()
            orchestrator.semantic_judges.judge_skill_workflow_plan_follow_up.assert_awaited_once()
            orchestrator._judge_folder_organizer_request.assert_not_awaited()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_marketplace_skill_workflow_router_runs_selected_plug_and_play_flow(self) -> None:
        root = Path("memory") / "test-pending-actions" / "generic-workflow-entry"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(root / "pending.json"),
            preview_store=FolderOrganizerPreviewStore(root / "preview.json"),
            receipt_store=PendingSensitiveExecutionReceiptStore(root / "receipt.json"),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.semantic_judges = SimpleNamespace(
            judge_skill_workflow_route=AsyncMock(
                return_value={
                    "decision": "run_workflow",
                    "skill_id": "dev.example.invoice-flow",
                    "reason": "invoice workflow requested",
                }
            )
        )
        orchestrator.skill_workflow_runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        orchestrator.skill_workflow_runtime.start_isolated_workflow = AsyncMock(
            return_value=(
                SimpleNamespace(run_id="run-1", skill_id="dev.example.invoice-flow", status="completed"),
                [
                    SkillWorkflowEvent(
                        type="completed",
                        run_id="run-1",
                        skill_id="dev.example.invoice-flow",
                        data={"summary": "Invoice workflow finished."},
                    )
                ],
            )
        )
        orchestrator.invoke_messages = AsyncMock()

        mcp_client = type("_McpClient", (), {})()
        mcp_client.call_tool = AsyncMock()
        orchestrator.mcp_client = mcp_client

        workflow_spec = {
            "skill_id": "dev.example.invoice-flow",
            "skill_name": "Invoice Flow",
            "description": "Extracts and files invoice documents.",
            "mode": "isolated_process",
            "protocol_version": "1.0",
            "command": str(Path(sys.executable).resolve()),
            "args": ["workflow.py"],
            "activation": {
                "workflow_intents": ["Extract invoices and file them by client."],
                "workflow_examples": ["Process the invoices in my inbox."],
            },
            "tools": {},
            "input_defaults": {
                "document_scope": "inbox",
                "preview_arguments": {"include_details": True},
            },
        }

        try:
            with unittest.mock.patch(
                "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
                return_value=[workflow_spec],
            ):
                reply = asyncio.run(
                    orchestrator.process_user_message(
                        "desktop-user",
                        "Process the invoices in my inbox with the installed invoice workflow",
                        conversation_id="conv-1",
                    )
                )

            self.assertEqual(reply.turn_status, "final_answer")
            self.assertEqual(reply.triage_reason, "skill-workflow")
            self.assertIn("Invoice Flow completed the installed skill workflow.", reply.text)
            self.assertIn("Invoice workflow finished.", reply.text)
            orchestrator.semantic_judges.judge_skill_workflow_route.assert_awaited_once()
            orchestrator.skill_workflow_runtime.start_isolated_workflow.assert_awaited_once()
            start_kwargs = orchestrator.skill_workflow_runtime.start_isolated_workflow.await_args.kwargs
            self.assertEqual(start_kwargs["spec"], workflow_spec)
            self.assertEqual(
                start_kwargs["input_payload"],
                {
                    "document_scope": "inbox",
                    "preview_arguments": {"include_details": True},
                    "prompt": "Process the invoices in my inbox with the installed invoice workflow",
                },
            )
            mcp_client.call_tool.assert_not_awaited()
            orchestrator.invoke_messages.assert_not_awaited()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_enabled_folder_organizer_workflow_blocks_legacy_preview_when_router_declines(self) -> None:
        root = Path("memory") / "test-pending-actions" / "folder-workflow-no-legacy-preview"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(root / "pending.json"),
            preview_store=FolderOrganizerPreviewStore(root / "preview.json"),
            receipt_store=PendingSensitiveExecutionReceiptStore(root / "receipt.json"),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.semantic_judges = SimpleNamespace(
            judge_skill_workflow_route=AsyncMock(
                return_value={"decision": "none", "skill_id": "", "reason": "not a workflow turn"}
            )
        )
        orchestrator._judge_folder_organizer_request = AsyncMock(
            return_value=FolderOrganizerRequestVerdict(decision="plan_request", reason="would be legacy")
        )
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **kwargs: ("", [], False, kwargs["lane"])
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="fast", reason="default-fast"))
        orchestrator.build_agent_messages = lambda *args, **kwargs: []
        orchestrator.invoke_messages = AsyncMock(return_value=ConversationReply(text="Regular chat route.", lane="fast"))
        orchestrator._enforce_turn_closure = AsyncMock(side_effect=lambda **kwargs: kwargs["reply"])

        mcp_client = type("_McpClient", (), {})()
        mcp_client.call_tool = AsyncMock()
        orchestrator.mcp_client = mcp_client

        workflow_spec = {
            "skill_id": "dev.azulclaw.desktop-organizer",
            "skill_name": "Folder Organizer",
            "mode": "isolated_process",
            "protocol_version": "1.0",
            "command": str(Path(sys.executable).resolve()),
            "args": ["workflow.py"],
            "tools": {
                "preview": "preview_folder_organization",
                "execute": "organize_target_folder",
            },
            "input_defaults": {
                "recursive": True,
                "preview_arguments": {"recursive": True, "include_moves": True},
            },
        }

        try:
            with unittest.mock.patch(
                "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
                return_value=[workflow_spec],
            ):
                reply = asyncio.run(
                    orchestrator.process_user_message(
                        "desktop-user",
                        "Dame un plan de organización utilizando la skill de folder organizer",
                        conversation_id="conv-1",
                    )
                )

            self.assertEqual(reply.text, "Regular chat route.")
            orchestrator.semantic_judges.judge_skill_workflow_route.assert_awaited_once()
            orchestrator._judge_folder_organizer_request.assert_not_awaited()
            mcp_client.call_tool.assert_not_awaited()
            orchestrator.invoke_messages.assert_awaited_once()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_organizer_enabled_skill_workflow_failure_does_not_use_legacy_flow(self) -> None:
        root = Path("memory") / "test-pending-actions" / "folder-workflow-failure"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(root / "pending.json"),
            preview_store=FolderOrganizerPreviewStore(root / "preview.json"),
            receipt_store=PendingSensitiveExecutionReceiptStore(root / "receipt.json"),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.skill_workflow_runtime = SkillWorkflowRuntime(
            store=SkillWorkflowStore(root / "workflows.json"),
            approval_service=ApprovalService(root / "approval-lifecycle.json"),
        )
        orchestrator.skill_workflow_runtime.start_isolated_workflow = AsyncMock(
            side_effect=RuntimeError("worker crashed")
        )
        orchestrator.semantic_judges = SimpleNamespace(
            judge_skill_workflow_route=AsyncMock(
                return_value={
                    "decision": "run_workflow",
                    "skill_id": "dev.azulclaw.desktop-organizer",
                    "reason": "explicit skill request",
                }
            )
        )
        orchestrator._judge_folder_organizer_request = AsyncMock()
        orchestrator.invoke_messages = AsyncMock()

        mcp_client = type("_McpClient", (), {})()
        mcp_client.call_tool = AsyncMock()
        orchestrator.mcp_client = mcp_client

        workflow_spec = {
            "skill_id": "dev.azulclaw.desktop-organizer",
            "skill_name": "Folder Organizer",
            "mode": "isolated_process",
            "protocol_version": "1.0",
            "command": str(Path(sys.executable).resolve()),
            "args": ["missing-worker.py"],
            "tools": {
                "preview": "preview_folder_organization",
                "execute": "organize_target_folder",
            },
            "input_defaults": {
                "recursive": True,
                "preview_arguments": {"recursive": True, "include_moves": True},
            },
        }

        try:
            with unittest.mock.patch(
                "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
                return_value=[workflow_spec],
            ):
                reply = asyncio.run(
                    orchestrator.process_user_message(
                        "desktop-user",
                        "Dame un plan de organización utilizando la skill de folder organizer",
                        conversation_id="conv-1",
                    )
                )

            self.assertEqual(reply.turn_status, "tool_failure")
            self.assertEqual(reply.triage_reason, "skill-workflow-failed")
            self.assertIn("installed skill workflow", reply.text)
            self.assertIn("embedded fallback path", reply.text)
            self.assertTrue(reply.workflow_events)
            orchestrator.skill_workflow_runtime.start_isolated_workflow.assert_awaited_once()
            orchestrator.semantic_judges.judge_skill_workflow_route.assert_awaited_once()
            orchestrator._judge_folder_organizer_request.assert_not_awaited()
            mcp_client.call_tool.assert_not_awaited()
            orchestrator.invoke_messages.assert_not_awaited()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_organizer_enabled_skill_workflow_without_runtime_does_not_use_legacy_flow(self) -> None:
        root = Path("memory") / "test-pending-actions" / "folder-workflow-missing-runtime"
        shutil.rmtree(root, ignore_errors=True)
        root.mkdir(parents=True, exist_ok=True)

        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(root / "pending.json"),
            preview_store=FolderOrganizerPreviewStore(root / "preview.json"),
            receipt_store=PendingSensitiveExecutionReceiptStore(root / "receipt.json"),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.semantic_judges = SimpleNamespace(
            judge_skill_workflow_route=AsyncMock(
                return_value={
                    "decision": "run_workflow",
                    "skill_id": "dev.azulclaw.desktop-organizer",
                    "reason": "explicit skill request",
                }
            )
        )
        orchestrator._judge_folder_organizer_request = AsyncMock()
        orchestrator.invoke_messages = AsyncMock()

        mcp_client = type("_McpClient", (), {})()
        mcp_client.call_tool = AsyncMock()
        orchestrator.mcp_client = mcp_client

        workflow_spec = {
            "skill_id": "dev.azulclaw.desktop-organizer",
            "skill_name": "Folder Organizer",
            "mode": "isolated_process",
            "protocol_version": "1.0",
            "command": str(Path(sys.executable).resolve()),
            "args": ["workflow.py"],
            "tools": {
                "preview": "preview_folder_organization",
                "execute": "organize_target_folder",
            },
            "input_defaults": {
                "recursive": True,
                "preview_arguments": {"recursive": True, "include_moves": True},
            },
        }

        try:
            with unittest.mock.patch(
                "azul_backend.azul_brain.conversation.list_enabled_workflow_runtime_specs",
                return_value=[workflow_spec],
            ):
                reply = asyncio.run(
                    orchestrator.process_user_message(
                        "desktop-user",
                        "Dame un plan de organización utilizando la skill de folder organizer",
                        conversation_id="conv-1",
                    )
                )

            self.assertEqual(reply.turn_status, "tool_failure")
            self.assertEqual(reply.triage_reason, "skill-workflow-failed")
            self.assertIn("workflow runtime is not available", reply.text)
            self.assertIn("embedded fallback path", reply.text)
            self.assertTrue(reply.workflow_events)
            orchestrator.semantic_judges.judge_skill_workflow_route.assert_awaited_once()
            orchestrator._judge_folder_organizer_request.assert_not_awaited()
            mcp_client.call_tool.assert_not_awaited()
            orchestrator.invoke_messages.assert_not_awaited()
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_folder_organizer_preview_safe_fallback_replaces_generic_turn_closure_failure(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-preview-safe-fallback")),
            preview_store=FolderOrganizerPreviewStore(self._preview_store_path("folder-preview-safe-fallback")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-preview-safe-fallback")),
        )
        orchestrator.pending_sensitive_actions.preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            summary="No files are ready to organize. 1 blocked by conflicts. Other: 1.",
            preview_payload={
                "relative_path": ".",
                "summary": "No files are ready to organize. 1 blocked by conflicts. Other: 1.",
                "blocked_items": [
                    {
                        "source_relative_path": "Escritorio/kdenlive-25.04.1_standalone/LICENSE",
                        "destination_relative_path": "Other/LICENSE",
                        "reason": "destination_exists",
                    }
                ],
            },
        )
        orchestrator._maybe_execute_folder_organizer_preview_request = AsyncMock(return_value="invalid draft")
        orchestrator._maybe_stage_sensitive_action_card = AsyncMock(side_effect=lambda **kwargs: kwargs["reply_text"])
        orchestrator._resolve_turn_closure_verdict = AsyncMock(
            side_effect=[
                TurnClosureVerdict(status="incomplete_promise", should_retry=True, reason="retry"),
                TurnClosureVerdict(status="incomplete_promise", should_retry=True, reason="retry-again"),
                TurnClosureVerdict(status="final_answer", should_retry=False, reason="recovered"),
            ]
        )
        orchestrator._judge_folder_organizer_capability_contract = AsyncMock(
            return_value=CapabilityContractVerdict(
                decision="invalid",
                should_retry=True,
                reason="invalid",
                guidance="fix",
            )
        )
        orchestrator._judge_folder_organizer_preview_context = AsyncMock(
            return_value=FolderOrganizerPreviewContextVerdict(
                reply_language="es",
                has_executable_plan=False,
                conceptual_plan_requested=True,
                status_summary="No hay archivos listos para organizar. 1 bloqueado por conflictos. Other: 1.",
            )
        )
        orchestrator.runtime_manager = SimpleNamespace(execute_messages=lambda **kwargs: None)
        orchestrator.build_agent_messages = lambda *args, **kwargs: []
        orchestrator.invoke_messages = AsyncMock(
            return_value=ConversationReply(text="Second invalid draft", lane="slow")
        )

        try:
            reply = asyncio.run(
                orchestrator._enforce_turn_closure(
                    user_id="desktop-user",
                    conversation_id="conv-1",
                    history=[],
                    user_message="Dame un plan con Folder Organizer",
                    reply=ConversationReply(text="First invalid draft", lane="slow"),
                    lane="slow",
                    route_reason="complex-request",
                    tools_enabled=True,
                )
            )

            self.assertEqual(reply.turn_status, "final_answer")
            self.assertNotEqual(reply.text, TURN_CLOSURE_FAILURE_TEXT)
            self.assertIn("No hay archivos listos para organizar", reply.text)
            self.assertIn("Elementos bloqueados", reply.text)
            self.assertIn("Plan conceptual recomendado", reply.text)
            self.assertIn("Facturas y Recibos", reply.text)
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions" / "folder-preview-safe-fallback",
                ignore_errors=True,
            )

    def test_folder_organizer_safe_fallback_gives_spanish_taxonomy_when_no_moves(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-empty-plan-fallback")),
            preview_store=FolderOrganizerPreviewStore(self._preview_store_path("folder-empty-plan-fallback")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-empty-plan-fallback")),
        )
        orchestrator.pending_sensitive_actions.preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            summary="No files need organizing.",
            preview_payload={
                "relative_path": ".",
                "summary": "No files need organizing.",
                "categorization_mode": "deterministic",
                "moves": [],
            },
        )
        orchestrator._judge_folder_organizer_preview_context = AsyncMock(
            return_value=FolderOrganizerPreviewContextVerdict(
                reply_language="es",
                has_executable_plan=False,
                conceptual_plan_requested=True,
                status_summary="No hay archivos que necesiten reorganizacion.",
            )
        )

        try:
            reply = asyncio.run(
                orchestrator._build_folder_organizer_preview_safe_reply(
                    user_id="desktop-user",
                    conversation_id="conv-1",
                    user_message=(
                        "Dame un plan de organizacion utilizando la skill de folder organizer "
                        "con nombres que no sean genericos sino proyectos, facturas, etc"
                    ),
                ),
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("He revisado el estado actual", reply)
            self.assertIn("No hay archivos que necesiten reorganizacion", reply)
            self.assertIn("Proyectos Activos", reply)
            self.assertIn("Facturas y Recibos", reply)
            self.assertIn("solo pedire aprobacion cuando exista un plan ejecutable concreto", reply)
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions" / "folder-empty-plan-fallback",
                ignore_errors=True,
            )

    def test_folder_organizer_safe_fallback_treats_omitted_moves_summary_as_executable(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-ready-summary-fallback")),
            preview_store=FolderOrganizerPreviewStore(self._preview_store_path("folder-ready-summary-fallback")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-ready-summary-fallback")),
        )
        orchestrator.pending_sensitive_actions.preview_store.save(
            user_id="desktop-user",
            conversation_id="conv-1",
            summary="4 file(s) ready to organize. Documents: 2, Images: 2.",
            preview_payload={
                "relative_path": ".",
                "summary": "4 file(s) ready to organize. Documents: 2, Images: 2.",
                "moves": [],
                "moves_omitted_for_batching": True,
            },
        )
        orchestrator._judge_folder_organizer_preview_context = AsyncMock(
            return_value=FolderOrganizerPreviewContextVerdict(
                reply_language="es",
                has_executable_plan=True,
                conceptual_plan_requested=True,
                status_summary="4 archivo(s) listos para organizar. Documents: 2, Images: 2.",
            )
        )

        try:
            reply = asyncio.run(
                orchestrator._build_folder_organizer_preview_safe_reply(
                    user_id="desktop-user",
                    conversation_id="conv-1",
                    user_message="Dame un plan con Folder Organizer",
                )
            )

            self.assertIsNotNone(reply)
            assert reply is not None
            self.assertIn("4 archivo(s) listos para organizar", reply)
            self.assertNotIn("Plan conceptual recomendado", reply)
        finally:
            shutil.rmtree(
                Path("memory") / "test-pending-actions" / "folder-ready-summary-fallback",
                ignore_errors=True,
            )

    def test_folder_organizer_capability_guard_second_invalid_reply_returns_safe_failure(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("folder-capability-guard-failure")),
            preview_store=FolderOrganizerPreviewStore(self._preview_store_path("folder-capability-guard-failure")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("folder-capability-guard-failure")),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, _kwargs["lane"])
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="slow", reason="complex-request"))
        orchestrator._judge_pending_action_stage = AsyncMock(
            return_value=PendingActionStageVerdict(decision="no_sensitive_action")
        )
        orchestrator._judge_folder_organizer_capability_contract = AsyncMock(
            side_effect=[
                CapabilityContractVerdict(decision="invalid", should_retry=True, guidance="Do not ask for the exact path."),
                CapabilityContractVerdict(decision="invalid", should_retry=True, guidance="Still invalid."),
            ]
        )

        async def invoke_messages(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            tools_enabled: bool = True,
        ) -> ConversationReply:
            if source == "turn-closure-retry":
                return ConversationReply(text="Pégame el path exacto y luego procedo.", lane=lane)
            return ConversationReply(text="Voy a crear subcarpetas vacías. Pégame el path exacto.", lane=lane)

        async def execute_messages(**kwargs):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "turn_status": "final_answer",
                        "should_retry": False,
                        "reason": "The reply appears final.",
                    }
                )
            )

        orchestrator.runtime_manager = SimpleNamespace(execute_messages=execute_messages)
        orchestrator.build_agent_messages = lambda *args, **kwargs: []
        orchestrator.invoke_messages = invoke_messages

        try:
            reply = asyncio.run(
                orchestrator.process_user_message(
                    "desktop-user",
                    "Usa la skill folder organizer para organizar la carpeta objetivo",
                    conversation_id="conv-1",
                )
            )

            self.assertEqual(reply.text, TURN_CLOSURE_FAILURE_TEXT)
            self.assertEqual(reply.turn_status, "tool_failure")
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "folder-capability-guard-failure", ignore_errors=True)

    def test_second_incomplete_promissory_reply_returns_safe_failure(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.preference_extractor = None
        orchestrator.pending_sensitive_actions = PendingSensitiveActionService(
            PendingSensitiveActionStore(self._pending_store_path("turn-closure-second-failure")),
            receipt_store=PendingSensitiveExecutionReceiptStore(self._receipt_store_path("turn-closure-second-failure")),
        )
        orchestrator.heartbeat_intents = SimpleNamespace(handle_message=AsyncMock(return_value=None))
        orchestrator.memory = RamOnlyMemory()
        orchestrator.retrieve_semantic_memories = AsyncMock(return_value=[])
        orchestrator.retrieve_user_knowledge = lambda _user_id: []
        orchestrator._prepare_attachment_inputs = lambda **_kwargs: ("", [], False, _kwargs["lane"])
        orchestrator.persist_with_vector_memory = AsyncMock(return_value="msg-1")
        orchestrator.resolve_route_async = AsyncMock(return_value=SimpleNamespace(lane="slow", reason="complex-request"))

        draft_calls: list[str] = []

        async def invoke_messages(
            _messages,
            _user_message: str,
            *,
            lane: str,
            source: str,
            title: str,
            tools_enabled: bool = True,
        ) -> ConversationReply:
            draft_calls.append(source)
            if source == "turn-closure-retry":
                return ConversationReply(text="VDBD\n\nVoy a solicitar el plan con la preview para que veas los movimientos.", lane=lane)
            return ConversationReply(text="Voy a prepararte un plan de organizaciÃ³n.", lane=lane)

        async def execute_messages(**_kwargs):
            return SimpleNamespace(
                text=json.dumps(
                    {
                        "turn_status": "incomplete_promise",
                        "should_retry": True,
                        "reason": "The reply promises future work without delivering a result.",
                    }
                )
            )

        orchestrator.runtime_manager = SimpleNamespace(execute_messages=execute_messages)
        orchestrator.build_agent_messages = lambda *args, **kwargs: []
        orchestrator.invoke_messages = invoke_messages
        orchestrator._judge_folder_organizer_request = AsyncMock(return_value=None)
        try:
            reply = asyncio.run(
                orchestrator.process_user_message(
                    "desktop-user",
                    "Dame un plan de organizaciÃ³n para la carpeta objetivo",
                    conversation_id="conv-1",
                )
            )

            self.assertEqual(reply.text, TURN_CLOSURE_FAILURE_TEXT)
            self.assertEqual(reply.turn_status, "tool_failure")
            self.assertEqual(draft_calls, ["chat", "turn-closure-retry"])
        finally:
            shutil.rmtree(Path("memory") / "test-pending-actions" / "turn-closure-second-failure", ignore_errors=True)


if __name__ == "__main__":
    unittest.main()


