from __future__ import annotations

import unittest

from azul_backend.azul_brain.memory.safe_memory import SafeMemory


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


if __name__ == "__main__":
    unittest.main()
