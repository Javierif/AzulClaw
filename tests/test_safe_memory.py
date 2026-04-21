from __future__ import annotations

import unittest

from azul_backend.azul_brain.memory.safe_memory import SafeMemory


class SafeMemoryTests(unittest.TestCase):
    def test_ram_only_conversation_message_is_visible_and_successful(self) -> None:
        memory = SafeMemory(db_path=None)

        delivered = memory.add_message(
            "desktop-user",
            "assistant",
            "hello from heartbeat",
            conversation_id="conv-1",
        )

        self.assertTrue(delivered)
        self.assertEqual(
            memory.get_conversation_messages("conv-1"),
            [{"role": "assistant", "content": "hello from heartbeat"}],
        )


if __name__ == "__main__":
    unittest.main()
