from __future__ import annotations

import importlib.util
import sys
import unittest
from pathlib import Path


RELAY_ROOT = Path(__file__).resolve().parents[1] / "skills" / "official" / "telegram" / "src" / "relay_function"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Unable to load module from {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class TelegramRelayFunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.access_control = _load_module("telegram_relay_access_control_test", RELAY_ROOT / "access_control.py")
        sys.path.insert(0, str(RELAY_ROOT))
        try:
            cls.function_app = _load_module("telegram_relay_function_app_test", RELAY_ROOT / "function_app.py")
        finally:
            try:
                sys.path.remove(str(RELAY_ROOT))
            except ValueError:
                pass

    def test_relay_allowlist_extracts_telegram_chat_and_user_ids(self) -> None:
        decision = self.access_control.evaluate_telegram_access(
            {
                "channelId": "telegram",
                "from": {"id": "user-1"},
                "conversation": {"id": "chat-1"},
                "channelData": {
                    "message": {
                        "from": {"id": "user-2"},
                        "chat": {"id": "chat-2"},
                    }
                },
            },
            frozenset({"user-1"}),
            frozenset({"chat-1"}),
        )

        self.assertTrue(decision.authorized)
        self.assertEqual(decision.user_id, "user-1")
        self.assertEqual(decision.chat_id, "chat-1")

    def test_relay_allowlist_rejects_blocked_telegram_chat(self) -> None:
        decision = self.access_control.evaluate_telegram_access(
            {
                "channelId": "telegram",
                "from": {"id": "user-1"},
                "conversation": {"id": "chat-blocked"},
            },
            frozenset({"user-1"}),
            frozenset({"chat-allowed"}),
        )

        self.assertFalse(decision.authorized)
        self.assertEqual(decision.reason, "telegram chat not allowlisted")

    def test_relay_session_mode_and_sync_reply_policy_are_normalized(self) -> None:
        self.assertEqual(self.function_app._normalize_session_mode("true"), "true")
        self.assertEqual(self.function_app._normalize_session_mode("false"), "false")
        self.assertEqual(self.function_app._normalize_session_mode("unexpected"), "auto")
        self.assertTrue(self.function_app._should_wait_for_sync_reply("telegram", "expectReplies"))
        self.assertFalse(self.function_app._should_wait_for_sync_reply("telegram", ""))


if __name__ == "__main__":
    unittest.main()
