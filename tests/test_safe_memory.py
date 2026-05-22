from __future__ import annotations

import shutil
import sqlite3
import unittest
import uuid
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from azul_backend.azul_brain.memory.safe_memory import SafeMemory


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "memory" / "test-safe-memory"


@contextmanager
def temp_memory_dir() -> Iterator[Path]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    root = TEST_TMP_ROOT / f"case-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class SafeMemoryTests(unittest.TestCase):
    def test_create_attachment_draft_rejects_non_owned_conversation(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(
                db_path=str(tmp / "memory.sqlite"),
                attachments_root=str(tmp / "attachments"),
            )
            try:
                conversation_id = memory.create_conversation("desktop-user")
                with self.assertRaisesRegex(ValueError, "Conversation not found"):
                    memory.create_attachment_draft(
                        user_id="other-user",
                        filename="notes.txt",
                        data=b"hello attachment",
                        conversation_id=conversation_id,
                    )
            finally:
                memory.close()

    def test_ram_only_conversation_message_is_visible_and_successful(self) -> None:
        memory = SafeMemory(db_path=None)
        conversation_id = memory.create_conversation("desktop-user")

        delivered = memory.add_message(
            "desktop-user",
            "assistant",
            "hello from heartbeat",
            conversation_id=conversation_id,
        )

        self.assertTrue(delivered)
        self.assertEqual(
            memory.get_conversation_messages(conversation_id),
            [{"role": "assistant", "content": "hello from heartbeat"}],
        )

    def test_active_conversation_requires_ownership(self) -> None:
        memory = SafeMemory(db_path=None)
        conversation_id = memory.create_conversation("desktop-user")

        self.assertFalse(memory.set_active_conversation("other-user", conversation_id))
        self.assertEqual(memory.get_active_conversation_id("other-user"), "")

        self.assertTrue(memory.set_active_conversation("desktop-user", conversation_id))
        self.assertEqual(memory.get_active_conversation_id("desktop-user"), conversation_id)

    def test_add_message_rejects_conversation_owned_by_another_user(self) -> None:
        memory = SafeMemory(db_path=None)
        conversation_id = memory.create_conversation("desktop-user")

        with self.assertLogs("azul_backend.azul_brain.memory.safe_memory", level="WARNING"):
            self.assertFalse(
                memory.add_message(
                    "other-user",
                    "assistant",
                    "wrong chat",
                    conversation_id=conversation_id,
                )
            )
        self.assertEqual(memory.get_conversation_messages(conversation_id), [])

    def test_ram_conversation_lookup_is_scoped_to_owner(self) -> None:
        memory = SafeMemory(db_path=None)
        conversation_id = memory.create_conversation("desktop-user")
        memory._store["other-user"].append(
            {
                "role": "assistant",
                "content": "wrong user",
                "conversation_id": conversation_id,
            }
        )

        self.assertEqual(memory.get_conversation_messages(conversation_id), [])

    def test_conversation_messages_include_ram_only_rows_when_sqlite_has_history(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(db_path=str(tmp / "memory.sqlite"))
            conversation_id = memory.create_conversation("desktop-user")

            self.assertTrue(
                memory.add_message(
                    "desktop-user",
                    "assistant",
                    "persisted reply",
                    conversation_id=conversation_id,
                )
            )
            memory._store["desktop-user"].append(
                {
                    "role": "assistant",
                    "content": "ram-only reply",
                    "conversation_id": conversation_id,
                }
            )

            self.assertEqual(
                memory.get_conversation_messages(conversation_id),
                [
                    {"role": "assistant", "content": "persisted reply"},
                    {"role": "assistant", "content": "ram-only reply"},
                ],
            )
            memory.close()

    def test_attachment_draft_binds_to_persisted_message_and_surfaces_in_history(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(
                db_path=str(tmp / "memory.sqlite"),
                attachments_root=str(tmp / "attachments"),
            )
            try:
                conversation_id = memory.create_conversation("desktop-user")

                draft = memory.create_attachment_draft(
                    user_id="desktop-user",
                    filename="notes.txt",
                    data=b"hello attachment",
                    conversation_id=conversation_id,
                )
                message_id = memory.add_message(
                    "desktop-user",
                    "user",
                    "please read this",
                    conversation_id=conversation_id,
                )
                bound = memory.bind_draft_attachments_to_message(
                    attachment_ids=[draft["id"]],
                    user_id="desktop-user",
                    conversation_id=conversation_id,
                    message_id=message_id,
                )

                self.assertEqual(len(bound), 1)
                records = memory.get_conversation_message_records(conversation_id)
                self.assertEqual(records[0]["message_id"], message_id)
                self.assertEqual(records[0]["attachments"][0]["filename"], "notes.txt")
            finally:
                memory.close()

    def test_attachment_draft_commit_failure_removes_orphaned_file(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(
                db_path=str(tmp / "memory.sqlite"),
                attachments_root=str(tmp / "attachments"),
            )

            class CommitFailConn:
                def __init__(self, inner: sqlite3.Connection) -> None:
                    self._inner = inner

                def execute(self, *args, **kwargs):
                    return self._inner.execute(*args, **kwargs)

                def commit(self) -> None:
                    raise sqlite3.OperationalError("commit failed")

                def rollback(self) -> None:
                    self._inner.rollback()

            try:
                real_conn = memory._conn
                self.assertIsNotNone(real_conn)
                memory._conn = CommitFailConn(real_conn)

                with self.assertRaises(sqlite3.OperationalError):
                    memory.create_attachment_draft(
                        user_id="desktop-user",
                        filename="notes.txt",
                        data=b"hello attachment",
                    )

                memory._conn = real_conn
                attachment_files = list((tmp / "attachments").glob("*"))
                self.assertEqual(attachment_files, [])
                count = real_conn.execute("SELECT COUNT(*) FROM conversation_attachments").fetchone()[0]
                self.assertEqual(count, 0)
            finally:
                memory._conn = real_conn
                memory.close()

    def test_bind_draft_attachments_validates_before_updating_any_rows(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(
                db_path=str(tmp / "memory.sqlite"),
                attachments_root=str(tmp / "attachments"),
            )
            try:
                conversation_id = memory.create_conversation("desktop-user")
                draft = memory.create_attachment_draft(
                    user_id="desktop-user",
                    filename="notes.txt",
                    data=b"hello attachment",
                    conversation_id=conversation_id,
                )

                with self.assertRaisesRegex(ValueError, "Attachment not found"):
                    memory.bind_draft_attachments_to_message(
                        attachment_ids=[draft["id"], "missing"],
                        user_id="desktop-user",
                        conversation_id=conversation_id,
                        message_id="msg-1",
                    )

                stored = memory.get_attachment(draft["id"], "desktop-user")
                self.assertEqual(stored["message_id"], "")
                self.assertEqual(stored["conversation_id"], conversation_id)
            finally:
                memory.close()

    def test_conversation_attachment_queries_only_return_sent_rows_for_owner(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(
                db_path=str(tmp / "memory.sqlite"),
                attachments_root=str(tmp / "attachments"),
            )
            try:
                conversation_id = memory.create_conversation("desktop-user")
                sent = memory.create_attachment_draft(
                    user_id="desktop-user",
                    filename="diagram.png",
                    data=(
                        b"\x89PNG\r\n\x1a\n"
                        b"\x00\x00\x00\rIHDR"
                        b"\x00\x00\x00\x01\x00\x00\x00\x01\x08\x02\x00\x00\x00"
                        b"\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc```\x00\x00\x00\x04\x00\x01"
                        b"\x0b\x0e-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
                    ),
                    conversation_id=conversation_id,
                )
                draft = memory.create_attachment_draft(
                    user_id="desktop-user",
                    filename="notes.txt",
                    data=b"unsent draft",
                    conversation_id=conversation_id,
                )
                memory.bind_draft_attachments_to_message(
                    attachment_ids=[sent["id"]],
                    user_id="desktop-user",
                    conversation_id=conversation_id,
                    message_id="msg-1",
                )
                memory._conn.execute(
                    """
                    INSERT INTO conversation_attachments (
                        id, message_id, conversation_id, user_id, filename, mime_type, size_bytes,
                        storage_path, sha256, kind, extraction_status, extracted_text, page_count, preview_json
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "foreign-row",
                        "msg-foreign",
                        conversation_id,
                        "other-user",
                        "other.txt",
                        "text/plain",
                        4,
                        str(tmp / "attachments" / "foreign-row.txt"),
                        "deadbeef",
                        "text",
                        "ready",
                        "nope",
                        1,
                        "{}",
                    ),
                )
                memory._conn.commit()

                conversation_items = memory.list_conversation_attachments(conversation_id, "desktop-user")
                visual_items = memory.list_recent_visual_attachments(conversation_id, "desktop-user", limit=5)

                self.assertEqual([item["id"] for item in conversation_items], [sent["id"]])
                self.assertEqual([item["id"] for item in visual_items], [sent["id"]])
                self.assertNotIn(draft["id"], [item["id"] for item in conversation_items])
            finally:
                memory.close()

    def test_delete_draft_attachment_removes_unsent_file(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(
                db_path=str(tmp / "memory.sqlite"),
                attachments_root=str(tmp / "attachments"),
            )
            try:
                draft = memory.create_attachment_draft(
                    user_id="desktop-user",
                    filename="notes.txt",
                    data=b"hello attachment",
                )

                stored = memory.get_attachment(draft["id"], "desktop-user")
                self.assertIsNotNone(stored)
                stored_path = Path(str(stored["storage_path"]))
                self.assertTrue(stored_path.exists())
                self.assertTrue(memory.delete_draft_attachment(draft["id"], "desktop-user"))
                self.assertFalse(stored_path.exists())
                self.assertIsNone(memory.get_attachment(draft["id"], "desktop-user"))
            finally:
                memory.close()

    def test_cleanup_expired_draft_attachments_removes_old_rows(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(
                db_path=str(tmp / "memory.sqlite"),
                attachments_root=str(tmp / "attachments"),
            )
            try:
                draft = memory.create_attachment_draft(
                    user_id="desktop-user",
                    filename="notes.txt",
                    data=b"hello attachment",
                )

                stored = memory.get_attachment(draft["id"], "desktop-user")
                self.assertIsNotNone(stored)
                stored_path = Path(str(stored["storage_path"]))
                memory._conn.execute(
                    "UPDATE conversation_attachments SET created_at = datetime('now', '-48 hours') WHERE id = ?",
                    (draft["id"],),
                )
                memory._conn.commit()

                memory.cleanup_expired_draft_attachments(max_age_hours=24)

                self.assertFalse(stored_path.exists())
                self.assertIsNone(memory.get_attachment(draft["id"], "desktop-user"))
            finally:
                memory.close()

    def test_conversation_message_merge_preserves_legitimate_repeated_messages(self) -> None:
        with temp_memory_dir() as tmp:
            memory = SafeMemory(db_path=str(tmp / "memory.sqlite"))
            conversation_id = memory.create_conversation("desktop-user")

            self.assertTrue(
                memory.add_message(
                    "desktop-user",
                    "user",
                    "ok",
                    conversation_id=conversation_id,
                )
            )
            memory._store["desktop-user"].append(
                {
                    "role": "user",
                    "content": "ok",
                    "conversation_id": conversation_id,
                }
            )

            self.assertEqual(
                memory.get_conversation_messages(conversation_id),
                [
                    {"role": "user", "content": "ok"},
                    {"role": "user", "content": "ok"},
                ],
            )
            memory.close()


if __name__ == "__main__":
    unittest.main()
