"""Short-term in-memory conversation history, keyed by user ID.

Now backed by an optional SQLite persistence layer so that recent
conversation context survives application restarts.  The in-memory deque
remains the primary data structure for speed; SQLite acts as a write-through
backup that is loaded once at startup.
"""

from __future__ import annotations

import logging
import os
import sqlite3
from collections import defaultdict, deque
from pathlib import Path

from ..api.hatching_store import resolve_memory_db_path

LOGGER = logging.getLogger(__name__)

_DEFAULT_MAX_MESSAGES = 50


class SafeMemory:
    """Thread-safe, bounded conversation history store with SQLite backup."""

    def __init__(
        self,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        db_path: str | None = None,
    ):
        self._max = max_messages
        self._store: dict[str, deque[dict]] = defaultdict(
            lambda: deque(maxlen=self._max)
        )

        # Optional SQLite persistence
        self._conn: sqlite3.Connection | None = None
        if db_path:
            try:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._init_schema()
                LOGGER.info("[SafeMemory] SQLite backup enabled at %s", db_path)
            except Exception as error:
                LOGGER.warning("[SafeMemory] SQLite backup disabled: %s", error)
                self._conn = None

    @classmethod
    def from_env(cls) -> "SafeMemory":
        """Builds a SafeMemory instance from environment configuration."""
        max_messages = int(
            os.environ.get("MEMORY_MAX_MESSAGES", str(_DEFAULT_MAX_MESSAGES))
        )
        db_path = resolve_memory_db_path()
        return cls(max_messages=max_messages, db_path=db_path)

    def close(self) -> None:
        """Closes the SQLite connection if open."""
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        """Creates the conversation history table if it does not exist."""
        if self._conn is None:
            return
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     TEXT NOT NULL,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convhist_user
            ON conversation_history(user_id, created_at)
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_message(self, user_id: str, role: str, content: str) -> None:
        """Appends a message to the user's conversation history (RAM + SQLite)."""
        self._store[user_id].append({"role": role, "content": content})

        if self._conn is not None:
            try:
                self._conn.execute(
                    "INSERT INTO conversation_history (user_id, role, content) VALUES (?, ?, ?)",
                    (user_id, role, content),
                )
                self._conn.commit()
            except Exception as error:
                LOGGER.warning("[SafeMemory] SQLite write failed: %s", error)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_history(self, user_id: str, limit: int = 12) -> list[dict]:
        """Returns the most recent messages for a user, up to limit."""
        history = list(self._store[user_id])

        # If RAM is empty but SQLite has data, restore from DB
        if not history and self._conn is not None:
            history = self.restore_from_db(user_id, limit)

        return history[-limit:] if len(history) > limit else history

    def restore_from_db(self, user_id: str, limit: int | None = None) -> list[dict]:
        """Restores conversation history from SQLite into the in-memory store.

        Called automatically when get_history finds an empty deque, or can
        be called explicitly at startup.
        """
        if self._conn is None:
            return []

        effective_limit = limit or self._max
        try:
            rows = self._conn.execute(
                """
                SELECT role, content
                FROM conversation_history
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, effective_limit),
            ).fetchall()

            messages = [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

            # Re-populate the in-memory store
            if messages and not self._store[user_id]:
                for msg in messages:
                    self._store[user_id].append(msg)

            LOGGER.info("[SafeMemory] Restored %d messages for user %s", len(messages), user_id)
            return messages

        except Exception as error:
            LOGGER.warning("[SafeMemory] SQLite restore failed: %s", error)
            return []

    # ------------------------------------------------------------------
    # Management
    # ------------------------------------------------------------------

    def clear(self, user_id: str) -> None:
        """Clears the conversation history for a user (RAM and SQLite)."""
        self._store[user_id].clear()

        if self._conn is not None:
            try:
                self._conn.execute(
                    "DELETE FROM conversation_history WHERE user_id = ?",
                    (user_id,),
                )
                self._conn.commit()
            except Exception as error:
                LOGGER.warning("[SafeMemory] SQLite clear failed: %s", error)
