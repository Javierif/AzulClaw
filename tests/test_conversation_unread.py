from __future__ import annotations

import shutil
import unittest
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from azul_backend.azul_brain.memory.safe_memory import SafeMemory


TEST_TMP_ROOT = Path(__file__).resolve().parents[1] / "memory" / "test-conversation-unread"


@contextmanager
def temp_memory_dir() -> Iterator[Path]:
    TEST_TMP_ROOT.mkdir(parents=True, exist_ok=True)
    root = TEST_TMP_ROOT / f"case-{uuid.uuid4().hex}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


class ConversationUnreadTests(unittest.TestCase):
    def test_assistant_messages_mark_conversation_unread_until_viewed(self) -> None:
        with temp_memory_dir() as root:
            memory = SafeMemory(db_path=str(root / "memory.sqlite"))
            conversation_id = memory.create_conversation("desktop-user", "Main conversation")

            memory.add_message("desktop-user", "user", "hello", conversation_id=conversation_id)
            memory.add_message("desktop-user", "assistant", "assistant reply", conversation_id=conversation_id)

            listed = memory.list_conversations("desktop-user")
            self.assertEqual(len(listed), 1)
            self.assertTrue(listed[0]["has_unread"])
            self.assertEqual(listed[0]["last_message_role"], "assistant")
            self.assertTrue(listed[0]["last_message_id"])
            self.assertTrue(listed[0]["last_message_at"])
            self.assertEqual(listed[0]["last_message_preview"], "assistant reply")

            self.assertTrue(memory.mark_conversation_viewed("desktop-user", conversation_id))
            viewed = memory.list_conversations("desktop-user")
            self.assertFalse(viewed[0]["has_unread"])
            memory.close()

    def test_user_messages_do_not_mark_conversation_unread(self) -> None:
        with temp_memory_dir() as root:
            memory = SafeMemory(db_path=str(root / "memory.sqlite"))
            conversation_id = memory.create_conversation("desktop-user", "Main conversation")

            self.assertTrue(memory.mark_conversation_viewed("desktop-user", conversation_id))
            memory.add_message("desktop-user", "user", "follow up", conversation_id=conversation_id)

            listed = memory.list_conversations("desktop-user")
            self.assertEqual(len(listed), 1)
            self.assertFalse(listed[0]["has_unread"])
            self.assertEqual(listed[0]["last_message_role"], "user")
            self.assertEqual(listed[0]["last_message_preview"], "follow up")
            memory.close()

    def test_mark_conversation_viewed_rejects_wrong_owner(self) -> None:
        with temp_memory_dir() as root:
            memory = SafeMemory(db_path=str(root / "memory.sqlite"))
            conversation_id = memory.create_conversation("desktop-user", "Main conversation")
            memory.add_message("desktop-user", "assistant", "assistant reply", conversation_id=conversation_id)

            self.assertFalse(memory.mark_conversation_viewed("other-user", conversation_id))
            listed = memory.list_conversations("desktop-user")
            self.assertTrue(listed[0]["has_unread"])
            memory.close()


if __name__ == "__main__":
    unittest.main()
