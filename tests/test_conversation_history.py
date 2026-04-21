from __future__ import annotations

import unittest

from azul_backend.azul_brain.conversation import ConversationOrchestrator


class RamOnlyMemory:
    _conn = None

    def get_conversation_messages(self, conversation_id: str, limit: int = 12) -> list[dict]:
        return []

    def get_history(self, user_id: str, limit: int = 12) -> list[dict]:
        return [{"role": "assistant", "content": "previous RAM reply"}]


class ConversationHistoryTests(unittest.TestCase):
    def test_conversation_history_falls_back_to_ram_when_sqlite_unavailable(self) -> None:
        orchestrator = ConversationOrchestrator.__new__(ConversationOrchestrator)
        orchestrator.memory = RamOnlyMemory()

        history = orchestrator._load_chat_history(
            "desktop-user",
            "conv-1",
            limit=12,
        )

        self.assertEqual(history, [{"role": "assistant", "content": "previous RAM reply"}])


if __name__ == "__main__":
    unittest.main()
