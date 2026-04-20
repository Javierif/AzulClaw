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
import uuid
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
        # Mirrors conversation titles so reads succeed even if SQLite write fails or is disabled.
        self._conversation_titles: dict[str, str] = {}
        self._active_conversation_by_user: dict[str, str] = {}

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
        """Creates conversation tables and migrates existing schema if needed."""
        if self._conn is None:
            return

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS conversations (
                id         TEXT PRIMARY KEY,
                user_id    TEXT NOT NULL,
                title      TEXT NOT NULL DEFAULT 'New conversation',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_conversations_user
            ON conversations(user_id, updated_at)
        """)

        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_history (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         TEXT NOT NULL,
                role            TEXT NOT NULL,
                content         TEXT NOT NULL,
                conversation_id TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convhist_user
            ON conversation_history(user_id, created_at)
        """)

        # Migration: add conversation_id column if it doesn't exist yet
        try:
            self._conn.execute(
                "ALTER TABLE conversation_history ADD COLUMN conversation_id TEXT"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convhist_conv
            ON conversation_history(conversation_id, created_at)
        """)

        self._conn.commit()

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def create_conversation(self, user_id: str, title: str = "New conversation") -> str:
        """Creates a new conversation and returns its ID."""
        conv_id = str(uuid.uuid4())
        self._conversation_titles[conv_id] = title
        if self._conn is not None:
            try:
                self._conn.execute(
                    "INSERT INTO conversations (id, user_id, title) VALUES (?, ?, ?)",
                    (conv_id, user_id, title),
                )
                self._conn.commit()
            except Exception as error:
                LOGGER.warning("[SafeMemory] create_conversation failed: %s", error)
        return conv_id

    def get_or_create_empty_conversation(self, user_id: str) -> tuple[str, str]:
        """Returns an existing empty conversation or creates one. Idempotent.

        Returns (id, title).
        """
        if self._conn is not None:
            try:
                row = self._conn.execute(
                    """
                    SELECT c.id, c.title FROM conversations c
                    WHERE c.user_id = ?
                    AND NOT EXISTS (
                        SELECT 1 FROM conversation_history h
                        WHERE h.conversation_id = c.id
                    )
                    ORDER BY c.created_at DESC LIMIT 1
                    """,
                    (user_id,),
                ).fetchone()
                if row:
                    return row["id"], row["title"]
            except Exception as error:
                LOGGER.warning("[SafeMemory] get_or_create_empty_conversation lookup failed: %s", error)
        conv_id = self.create_conversation(user_id)
        return conv_id, "New conversation"

    def get_or_create_named_conversation(self, user_id: str, title: str) -> tuple[str, str]:
        """Returns a conversation with the exact title for a user, creating it if missing."""
        safe_title = (title or "").strip() or "New conversation"
        if self._conn is not None:
            try:
                row = self._conn.execute(
                    """
                    SELECT id, title FROM conversations
                    WHERE user_id = ? AND title = ?
                    ORDER BY updated_at DESC LIMIT 1
                    """,
                    (user_id, safe_title),
                ).fetchone()
                if row:
                    self._conversation_titles[row["id"]] = row["title"]
                    return row["id"], row["title"]
            except Exception as error:
                LOGGER.warning("[SafeMemory] get_or_create_named_conversation lookup failed: %s", error)
        conv_id = self.create_conversation(user_id, safe_title)
        return conv_id, safe_title

    def conversation_exists(self, conversation_id: str) -> bool:
        """Returns whether a conversation row is available."""
        safe_id = (conversation_id or "").strip()
        if not safe_id:
            return False
        if safe_id in self._conversation_titles:
            return True
        if self._conn is None:
            return False
        try:
            row = self._conn.execute(
                "SELECT 1 FROM conversations WHERE id = ? LIMIT 1",
                (safe_id,),
            ).fetchone()
            return row is not None
        except Exception as error:
            LOGGER.warning("[SafeMemory] conversation_exists failed: %s", error)
            return False

    def set_active_conversation(self, user_id: str, conversation_id: str) -> None:
        """Records which conversation the desktop user currently has open."""
        safe_user_id = (user_id or "").strip()
        safe_conversation_id = (conversation_id or "").strip()
        if not safe_user_id or not safe_conversation_id:
            return
        if not self.conversation_exists(safe_conversation_id):
            return
        self._active_conversation_by_user[safe_user_id] = safe_conversation_id

    def get_active_conversation_id(self, user_id: str) -> str:
        """Returns the current active conversation id for a user, if it still exists."""
        safe_user_id = (user_id or "").strip()
        if not safe_user_id:
            return ""
        conversation_id = self._active_conversation_by_user.get(safe_user_id, "")
        if conversation_id and self.conversation_exists(conversation_id):
            return conversation_id
        self._active_conversation_by_user.pop(safe_user_id, None)
        return ""

    def list_conversations(self, user_id: str, limit: int = 20) -> list[dict]:
        """Returns conversations ordered by most recently updated."""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT id, title, updated_at
                FROM conversations
                WHERE user_id = ?
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
            return [
                {
                    "id": row["id"],
                    "title": self._conversation_titles.get(row["id"], row["title"]),
                    "updated_at": row["updated_at"],
                }
                for row in rows
            ]
        except Exception as error:
            LOGGER.warning("[SafeMemory] list_conversations failed: %s", error)
            return []

    def get_conversation_title(self, conversation_id: str) -> str | None:
        """Returns the current title for a conversation, or None if missing."""
        if conversation_id in self._conversation_titles:
            return self._conversation_titles[conversation_id]
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                "SELECT title FROM conversations WHERE id = ?",
                (conversation_id,),
            ).fetchone()
            if row is None:
                return None
            return row["title"]
        except Exception as error:
            LOGGER.warning("[SafeMemory] get_conversation_title failed: %s", error)
            return None

    def get_conversation_messages(self, conversation_id: str, limit: int = 50) -> list[dict]:
        """Returns messages for a specific conversation, oldest first."""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT role, content
                FROM conversation_history
                WHERE conversation_id = ?
                ORDER BY created_at ASC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
            return [{"role": row["role"], "content": row["content"]} for row in rows]
        except Exception as error:
            LOGGER.warning("[SafeMemory] get_conversation_messages failed: %s", error)
            return []

    def update_conversation_title(self, conversation_id: str, title: str) -> None:
        """Updates the title of a conversation."""
        self._conversation_titles[conversation_id] = title
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "UPDATE conversations SET title = ?, updated_at = datetime('now') WHERE id = ?",
                (title, conversation_id),
            )
            self._conn.commit()
        except Exception as error:
            LOGGER.warning("[SafeMemory] update_conversation_title failed: %s", error)

    def delete_conversation(self, conversation_id: str) -> bool:
        """Deletes a conversation and all its messages. Returns True if found."""
        if self._conn is None:
            return False
        try:
            self._conn.execute(
                "DELETE FROM conversation_history WHERE conversation_id = ?",
                (conversation_id,),
            )
            cur = self._conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            self._conn.commit()
            self._conversation_titles.pop(conversation_id, None)
            for user_id, active_id in list(self._active_conversation_by_user.items()):
                if active_id == conversation_id:
                    self._active_conversation_by_user.pop(user_id, None)
            return cur.rowcount > 0
        except Exception as error:
            LOGGER.warning("[SafeMemory] delete_conversation failed: %s", error)
            return False

    def _touch_conversation(self, conversation_id: str) -> None:
        """Bumps updated_at on a conversation row."""
        if self._conn is None:
            return
        try:
            self._conn.execute(
                "UPDATE conversations SET updated_at = datetime('now') WHERE id = ?",
                (conversation_id,),
            )
            self._conn.commit()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        conversation_id: str | None = None,
    ) -> None:
        """Appends a message to the user's conversation history (RAM + SQLite)."""
        self._store[user_id].append({"role": role, "content": content})

        if self._conn is not None:
            try:
                self._conn.execute(
                    """
                    INSERT INTO conversation_history (user_id, role, content, conversation_id)
                    VALUES (?, ?, ?, ?)
                    """,
                    (user_id, role, content, conversation_id),
                )
                self._conn.commit()
                if conversation_id:
                    self._touch_conversation(conversation_id)
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
