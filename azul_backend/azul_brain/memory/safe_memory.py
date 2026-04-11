"""Short-term in-memory conversation history, keyed by user ID."""

import os
from collections import defaultdict, deque

_DEFAULT_MAX_MESSAGES = 50


class SafeMemory:
    """Thread-safe, bounded conversation history store."""

    def __init__(self, max_messages: int = _DEFAULT_MAX_MESSAGES):
        self._max = max_messages
        self._store: dict[str, deque[dict]] = defaultdict(lambda: deque(maxlen=self._max))

    @classmethod
    def from_env(cls) -> "SafeMemory":
        """Builds a SafeMemory instance from environment configuration."""
        max_messages = int(os.environ.get("MEMORY_MAX_MESSAGES", str(_DEFAULT_MAX_MESSAGES)))
        return cls(max_messages=max_messages)

    def add_message(self, user_id: str, role: str, content: str) -> None:
        """Appends a message to the user's conversation history."""
        self._store[user_id].append({"role": role, "content": content})

    def get_history(self, user_id: str, limit: int = 12) -> list[dict]:
        """Returns the most recent messages for a user, up to limit."""
        history = list(self._store[user_id])
        return history[-limit:] if len(history) > limit else history

    def clear(self, user_id: str) -> None:
        """Clears the conversation history for a user."""
        self._store[user_id].clear()
