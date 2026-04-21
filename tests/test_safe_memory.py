from __future__ import annotations

import shutil
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


if __name__ == "__main__":
    unittest.main()
