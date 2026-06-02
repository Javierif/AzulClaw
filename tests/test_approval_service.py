from __future__ import annotations

import shutil
import unittest
import uuid
from pathlib import Path

from azul_backend.azul_brain.runtime.approval_service import ApprovalService
from azul_backend.azul_brain.runtime.heartbeat_intent import (
    HeartbeatDraft,
    PendingHeartbeatStore,
)
from azul_backend.azul_brain.runtime.pending_action_intent import PendingSensitiveActionStore


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "memory" / "test-approval-service"


class ApprovalServiceTests(unittest.TestCase):
    def _case_root(self) -> Path:
        TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
        root = TEST_TMP_ROOT / f"case-{uuid.uuid4().hex}"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def test_shared_service_tracks_lifecycle_transitions(self) -> None:
        root = self._case_root()
        try:
            service = ApprovalService(root / "approvals.json")
            service.register_pending(
                action_id="approval-1",
                user_id="desktop-user",
                conversation_id="conv-1",
                source="sensitive_action",
                action_kind="folder_organizer",
                title="Folder Organizer",
                summary="Approve applying the reviewed plan.",
                supersede_existing=True,
            )
            service.mark_running("approval-1")
            record = service.mark_completed("approval-1")

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.status, "completed")
            self.assertEqual(record.decision, "approve")
            self.assertTrue(record.resolved_at)
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_sensitive_store_supersedes_previous_pending_approval(self) -> None:
        root = self._case_root()
        try:
            store = PendingSensitiveActionStore(root / "pending.json")
            first = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve first plan.",
                source_user_message="Organiza carpeta",
                action_kind="folder_organizer",
            )
            second = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve revised plan.",
                source_user_message="Organiza carpeta revisada",
                action_kind="folder_organizer",
            )

            lifecycle = store.approval_service
            first_record = lifecycle.get_by_action_id(first.id)
            second_record = lifecycle.get_by_action_id(second.id)

            self.assertIsNotNone(first_record)
            self.assertIsNotNone(second_record)
            assert first_record is not None and second_record is not None
            self.assertEqual(first_record.status, "superseded")
            self.assertEqual(first_record.superseded_by, second.id)
            self.assertEqual(second_record.status, "pending")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_sensitive_store_keeps_pending_approvals_isolated_per_conversation(self) -> None:
        root = self._case_root()
        try:
            store = PendingSensitiveActionStore(root / "pending.json")
            first = store.save(
                user_id="desktop-user",
                conversation_id="conv-1",
                title="Folder Organizer",
                summary="Approve plan for conv-1.",
                source_user_message="Organiza carpeta uno",
                action_kind="folder_organizer",
            )
            second = store.save(
                user_id="desktop-user",
                conversation_id="conv-2",
                title="Folder Organizer",
                summary="Approve plan for conv-2.",
                source_user_message="Organiza carpeta dos",
                action_kind="folder_organizer",
            )

            self.assertIsNotNone(store.get_for_context("desktop-user", "conv-1"))
            self.assertIsNotNone(store.get_for_context("desktop-user", "conv-2"))
            first_record = store.approval_service.get_by_action_id(first.id)
            second_record = store.approval_service.get_by_action_id(second.id)

            self.assertIsNotNone(first_record)
            self.assertIsNotNone(second_record)
            assert first_record is not None and second_record is not None
            self.assertEqual(first_record.status, "pending")
            self.assertEqual(second_record.status, "pending")
        finally:
            shutil.rmtree(root, ignore_errors=True)

    def test_heartbeat_store_marks_rejection_in_shared_lifecycle(self) -> None:
        root = self._case_root()
        try:
            store = PendingHeartbeatStore(root / "pending-heartbeat.json")
            pending = store.save_for_user(
                "desktop-user",
                HeartbeatDraft(
                    name="Inbox triage",
                    prompt="Review Inbox.",
                    cron_expression="*/30 * * * *",
                    lane="fast",
                ),
            )
            store.pop_for_user("desktop-user", status="rejected")
            record = store.approval_service.get_by_action_id(pending.id)

            self.assertIsNotNone(record)
            assert record is not None
            self.assertEqual(record.status, "rejected")
            self.assertEqual(record.source, "heartbeat")
        finally:
            shutil.rmtree(root, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
