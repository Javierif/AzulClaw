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
import json
import hashlib
import uuid
from collections import defaultdict, deque
from pathlib import Path

from ..attachments import AttachmentExtractionResult, extract_attachment
from ..api.hatching_store import resolve_memory_db_path

LOGGER = logging.getLogger(__name__)

_DEFAULT_MAX_MESSAGES = 50


def _conversation_match_snippet(content: str, query: str, radius: int = 70) -> str:
    """Builds a compact preview around the first query match."""
    text = " ".join((content or "").split())
    if not text:
        return ""
    normalized_query = (query or "").strip().lower()
    if not normalized_query:
        return text[: (radius * 2) + 1]
    index = text.lower().find(normalized_query)
    if index < 0:
        return text[: (radius * 2) + 1]
    start = max(0, index - radius)
    end = min(len(text), index + len(normalized_query) + radius)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _conversation_preview(content: str, limit: int = 160) -> str:
    """Builds a compact one-line preview for recent-message surfaces."""
    text = " ".join((content or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 3)].rstrip()}..."


class SafeMemory:
    """Thread-safe, bounded conversation history store with SQLite backup."""

    def __init__(
        self,
        max_messages: int = _DEFAULT_MAX_MESSAGES,
        db_path: str | None = None,
        attachments_root: str | None = None,
    ):
        self._max = max_messages
        self._store: dict[str, deque[dict]] = defaultdict(
            lambda: deque(maxlen=self._max)
        )
        # Mirrors conversation titles so reads succeed even if SQLite write fails or is disabled.
        self._conversation_titles: dict[str, str] = {}
        self._conversation_users: dict[str, str] = {}
        self._active_conversation_by_user: dict[str, str] = {}
        self._attachments_root = Path(attachments_root).expanduser() if attachments_root else None
        if self._attachments_root is not None:
            self._attachments_root.mkdir(parents=True, exist_ok=True)

        # Optional SQLite persistence
        self._conn: sqlite3.Connection | None = None
        if db_path:
            try:
                Path(db_path).parent.mkdir(parents=True, exist_ok=True)
                self._conn = sqlite3.connect(db_path, check_same_thread=False)
                self._conn.row_factory = sqlite3.Row
                self._init_schema()
                self.cleanup_expired_draft_attachments()
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
        runtime_dir = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
        attachments_root = (
            Path(runtime_dir).expanduser() / "attachments"
            if runtime_dir
            else Path(db_path).expanduser().parent / "attachments"
        )
        return cls(max_messages=max_messages, db_path=db_path, attachments_root=str(attachments_root))

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
                last_viewed_at TEXT,
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
                message_id      TEXT,
                conversation_id TEXT,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convhist_user
            ON conversation_history(user_id, created_at)
        """)

        # Migration: extend legacy conversation/message tables with newer metadata columns.
        try:
            self._conn.execute(
                "ALTER TABLE conversations ADD COLUMN last_viewed_at TEXT"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            self._conn.execute(
                "ALTER TABLE conversation_history ADD COLUMN conversation_id TEXT"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists
        try:
            self._conn.execute(
                "ALTER TABLE conversation_history ADD COLUMN message_id TEXT"
            )
        except sqlite3.OperationalError:
            pass  # Column already exists

        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convhist_conv
            ON conversation_history(conversation_id, created_at)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convhist_message
            ON conversation_history(message_id)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS conversation_attachments (
                id                TEXT PRIMARY KEY,
                message_id        TEXT,
                conversation_id   TEXT,
                user_id           TEXT NOT NULL,
                filename          TEXT NOT NULL,
                mime_type         TEXT NOT NULL,
                size_bytes        INTEGER NOT NULL,
                storage_path      TEXT NOT NULL,
                sha256            TEXT NOT NULL,
                kind              TEXT NOT NULL,
                extraction_status TEXT NOT NULL,
                extracted_text    TEXT NOT NULL DEFAULT '',
                page_count        INTEGER NOT NULL DEFAULT 0,
                preview_json      TEXT NOT NULL DEFAULT '{}',
                created_at        TEXT DEFAULT (datetime('now'))
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convattachments_message
            ON conversation_attachments(message_id, created_at)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_convattachments_conversation
            ON conversation_attachments(conversation_id, created_at)
        """)

        self._conn.commit()

    # ------------------------------------------------------------------
    # Conversations
    # ------------------------------------------------------------------

    def create_conversation(self, user_id: str, title: str = "New conversation") -> str:
        """Creates a new conversation and returns its ID."""
        conv_id = str(uuid.uuid4())
        self._conversation_titles[conv_id] = title
        self._conversation_users[conv_id] = user_id
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
                    self._conversation_titles[row["id"]] = row["title"]
                    self._conversation_users[row["id"]] = user_id
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
                    self._conversation_users[row["id"]] = user_id
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

    def conversation_belongs_to_user(self, conversation_id: str, user_id: str) -> bool:
        """Returns whether the conversation exists and is owned by the user."""
        safe_id = (conversation_id or "").strip()
        safe_user_id = (user_id or "").strip()
        if not safe_id or not safe_user_id:
            return False
        cached_user_id = self._conversation_users.get(safe_id)
        if cached_user_id is not None:
            return cached_user_id == safe_user_id
        if self._conn is None:
            return False
        try:
            row = self._conn.execute(
                "SELECT user_id, title FROM conversations WHERE id = ? LIMIT 1",
                (safe_id,),
            ).fetchone()
            if row is None:
                return False
            owner_id = str(row["user_id"])
            self._conversation_users[safe_id] = owner_id
            self._conversation_titles[safe_id] = row["title"]
            return owner_id == safe_user_id
        except Exception as error:
            LOGGER.warning("[SafeMemory] conversation ownership check failed: %s", error)
            return False

    def set_active_conversation(self, user_id: str, conversation_id: str) -> bool:
        """Records which conversation the desktop user currently has open."""
        safe_user_id = (user_id or "").strip()
        safe_conversation_id = (conversation_id or "").strip()
        if not safe_user_id or not safe_conversation_id:
            return False
        if not self.conversation_belongs_to_user(safe_conversation_id, safe_user_id):
            return False
        self._active_conversation_by_user[safe_user_id] = safe_conversation_id
        return True

    def get_active_conversation_id(self, user_id: str) -> str:
        """Returns the current active conversation id for a user, if it still exists."""
        safe_user_id = (user_id or "").strip()
        if not safe_user_id:
            return ""
        conversation_id = self._active_conversation_by_user.get(safe_user_id, "")
        if conversation_id and self.conversation_belongs_to_user(conversation_id, safe_user_id):
            return conversation_id
        self._active_conversation_by_user.pop(safe_user_id, None)
        return ""

    def mark_conversation_viewed(self, user_id: str, conversation_id: str) -> bool:
        """Marks a conversation as viewed by its owning user."""
        safe_user_id = (user_id or "").strip()
        safe_conversation_id = (conversation_id or "").strip()
        if not safe_user_id or not safe_conversation_id:
            return False
        if not self.conversation_belongs_to_user(safe_conversation_id, safe_user_id):
            return False
        if self._conn is None:
            return False
        try:
            cur = self._conn.execute(
                """
                UPDATE conversations
                SET last_viewed_at = datetime('now')
                WHERE id = ? AND user_id = ?
                """,
                (safe_conversation_id, safe_user_id),
            )
            self._conn.commit()
            return cur.rowcount > 0
        except Exception as error:
            LOGGER.warning("[SafeMemory] mark_conversation_viewed failed: %s", error)
            return False

    def list_conversations(self, user_id: str, limit: int = 20, query: str = "") -> list[dict]:
        """Returns conversations ordered by most recently updated."""
        if self._conn is None:
            return []
        normalized_query = query.strip()
        latest_message_select = """
                        (
                            SELECT h.message_id
                            FROM conversation_history h
                            WHERE h.conversation_id = c.id
                            ORDER BY h.created_at DESC, h.id DESC
                            LIMIT 1
                        ) AS last_message_id,
                        (
                            SELECT h.created_at
                            FROM conversation_history h
                            WHERE h.conversation_id = c.id
                            ORDER BY h.created_at DESC, h.id DESC
                            LIMIT 1
                        ) AS last_message_at,
                        (
                            SELECT h.role
                            FROM conversation_history h
                            WHERE h.conversation_id = c.id
                            ORDER BY h.created_at DESC, h.id DESC
                            LIMIT 1
                        ) AS last_message_role,
                        (
                            SELECT h.content
                            FROM conversation_history h
                            WHERE h.conversation_id = c.id
                            ORDER BY h.created_at DESC, h.id DESC
                            LIMIT 1
                        ) AS last_message_content,
                        EXISTS (
                            SELECT 1
                            FROM conversation_history h
                            WHERE h.conversation_id = c.id
                              AND h.role = 'assistant'
                              AND (
                                c.last_viewed_at IS NULL
                                OR h.created_at > c.last_viewed_at
                              )
                        ) AS has_unread
        """
        try:
            if normalized_query:
                like_query = f"%{normalized_query.lower()}%"
                rows = self._conn.execute(
                    f"""
                    SELECT
                        c.id,
                        c.title,
                        c.updated_at,
                        {latest_message_select},
                        (
                            SELECT h.content
                            FROM conversation_history h
                            WHERE h.conversation_id = c.id
                              AND lower(h.content) LIKE ?
                            ORDER BY h.created_at DESC, h.id DESC
                            LIMIT 1
                        ) AS matched_content
                    FROM conversations c
                    WHERE c.user_id = ?
                      AND (
                        lower(c.title) LIKE ?
                        OR EXISTS (
                            SELECT 1
                            FROM conversation_history h
                            WHERE h.conversation_id = c.id
                              AND lower(h.content) LIKE ?
                        )
                      )
                    ORDER BY c.updated_at DESC
                    LIMIT ?
                    """,
                    (like_query, user_id, like_query, like_query, limit),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    f"""
                    SELECT
                        c.id,
                        c.title,
                        c.updated_at,
                        {latest_message_select}
                    FROM conversations c
                    WHERE c.user_id = ?
                    ORDER BY c.updated_at DESC
                    LIMIT ?
                    """,
                    (user_id, limit),
                ).fetchall()
            return [
                {
                    "id": row["id"],
                    "title": self._conversation_titles.get(row["id"], row["title"]),
                    "updated_at": row["updated_at"],
                    "has_unread": bool(row["has_unread"]),
                    "last_message_id": str(row["last_message_id"] or ""),
                    "last_message_at": str(row["last_message_at"] or ""),
                    "last_message_role": str(row["last_message_role"] or ""),
                    "last_message_preview": _conversation_preview(str(row["last_message_content"] or "")),
                    "snippet": _conversation_match_snippet(
                        str(row["matched_content"] or ""),
                        normalized_query,
                    ) if normalized_query else "",
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

    def get_conversation_message_records(self, conversation_id: str, limit: int = 50) -> list[dict]:
        """Returns persisted message rows with stable IDs and attachments."""
        if self._conn is None:
            messages = self._conversation_messages_from_ram(conversation_id, limit)
            return [
                {
                    "message_id": item.get("message_id") or f"ram-{index}",
                    "role": item.get("role", "user"),
                    "content": item.get("content", ""),
                    "created_at": item.get("created_at", ""),
                    "attachments": [],
                }
                for index, item in enumerate(messages)
            ]

        try:
            rows = self._conn.execute(
                """
                SELECT message_id, role, content, created_at
                FROM conversation_history
                WHERE conversation_id = ?
                ORDER BY created_at ASC, id ASC
                LIMIT ?
                """,
                (conversation_id, limit),
            ).fetchall()
            message_ids = [str(row["message_id"] or "") for row in rows if row["message_id"]]
            attachments_by_message = self._attachments_by_message_id(message_ids)
            return [
                {
                    "message_id": row["message_id"] or "",
                    "role": row["role"],
                    "content": row["content"],
                    "created_at": row["created_at"] or "",
                    "attachments": attachments_by_message.get(str(row["message_id"] or ""), []),
                }
                for row in rows
            ]
        except Exception as error:
            LOGGER.warning("[SafeMemory] get_conversation_message_records failed: %s", error)
            return []

    def get_conversation_messages(self, conversation_id: str, limit: int = 50) -> list[dict]:
        """Returns messages for a specific conversation, oldest first."""
        if self._conn is None:
            return self._conversation_messages_from_ram(conversation_id, limit)
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
            messages = [{"role": row["role"], "content": row["content"]} for row in rows]
            return self._merge_conversation_messages(
                messages,
                self._conversation_messages_from_ram(conversation_id, limit),
                limit,
            )
        except Exception as error:
            LOGGER.warning("[SafeMemory] get_conversation_messages failed: %s", error)
            return self._conversation_messages_from_ram(conversation_id, limit)

    def _merge_conversation_messages(
        self,
        persisted: list[dict],
        in_memory: list[dict],
        limit: int,
    ) -> list[dict]:
        """Merges persisted rows with RAM-only messages after transient write failures."""
        if not persisted:
            return in_memory[-limit:]
        overlap = 0
        max_overlap = min(len(persisted), len(in_memory))
        for size in range(max_overlap, 0, -1):
            if self._message_signature(persisted[-size:]) == self._message_signature(in_memory[:size]):
                overlap = size
                break
        return [*persisted, *in_memory[overlap:]][-limit:]

    def _message_signature(self, messages: list[dict]) -> list[tuple[str, str]]:
        return [
            (str(item.get("role", "")), str(item.get("content", "")))
            for item in messages
        ]

    def _conversation_messages_from_ram(self, conversation_id: str, limit: int) -> list[dict]:
        """Returns conversation-scoped messages from the in-memory store."""
        owner_id = self._conversation_users.get(conversation_id)
        if not owner_id:
            return []
        messages: list[dict] = []
        for item in self._store.get(owner_id, []):
            if item.get("conversation_id") != conversation_id:
                continue
            messages.append(
                {
                    "role": item.get("role", "user"),
                    "content": item.get("content", ""),
                }
            )
        return messages[-limit:]

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
            attachment_rows = self._conn.execute(
                "SELECT storage_path FROM conversation_attachments WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchall()
            self._conn.execute(
                "DELETE FROM conversation_history WHERE conversation_id = ?",
                (conversation_id,),
            )
            self._conn.execute(
                "DELETE FROM conversation_attachments WHERE conversation_id = ?",
                (conversation_id,),
            )
            cur = self._conn.execute(
                "DELETE FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            self._conn.commit()
            for row in attachment_rows:
                try:
                    Path(str(row["storage_path"])).unlink(missing_ok=True)
                except OSError:
                    pass
            self._conversation_titles.pop(conversation_id, None)
            self._conversation_users.pop(conversation_id, None)
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

    def create_attachment_draft(
        self,
        *,
        user_id: str,
        filename: str,
        data: bytes,
        conversation_id: str | None = None,
    ) -> dict:
        """Stores a draft attachment and its extracted metadata."""
        if self._conn is None:
            raise ValueError("Attachment persistence requires SQLite memory.")
        if self._attachments_root is None:
            raise ValueError("Attachment storage root is not configured.")
        if conversation_id and not self.conversation_belongs_to_user(conversation_id, user_id):
            raise ValueError("Conversation not found.")

        attachment_id = str(uuid.uuid4())
        safe_filename = Path(filename or "attachment").name or "attachment"
        file_path = self._attachments_root / f"{attachment_id}{Path(safe_filename).suffix.lower()}"
        extraction: AttachmentExtractionResult = extract_attachment(safe_filename, data)
        sha256 = hashlib.sha256(data).hexdigest()
        file_path.write_bytes(data)
        try:
            self._conn.execute(
                """
                INSERT INTO conversation_attachments (
                    id, message_id, conversation_id, user_id, filename, mime_type, size_bytes,
                    storage_path, sha256, kind, extraction_status, extracted_text, page_count, preview_json
                )
                VALUES (?, NULL, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    conversation_id,
                    user_id,
                    safe_filename,
                    extraction.mime_type,
                    len(data),
                    str(file_path),
                    sha256,
                    extraction.kind,
                    extraction.extraction_status,
                    extraction.extracted_text,
                    extraction.page_count,
                    extraction.preview_json,
                ),
            )
            self._conn.commit()
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            file_path.unlink(missing_ok=True)
            raise
        return self.get_attachment(attachment_id, user_id) or {}

    def get_attachment(self, attachment_id: str, user_id: str | None = None) -> dict | None:
        if self._conn is None:
            return None
        try:
            row = self._conn.execute(
                """
                SELECT id, message_id, conversation_id, user_id, filename, mime_type, size_bytes,
                       storage_path, sha256, kind, extraction_status, extracted_text, page_count,
                       preview_json, created_at
                FROM conversation_attachments
                WHERE id = ?
                """,
                (attachment_id,),
            ).fetchone()
            if row is None:
                return None
            if user_id and str(row["user_id"]) != str(user_id):
                return None
            return self._attachment_row_to_dict(row)
        except Exception as error:
            LOGGER.warning("[SafeMemory] get_attachment failed: %s", error)
            return None

    def list_recent_visual_attachments(self, conversation_id: str, user_id: str, limit: int = 2) -> list[dict]:
        """Returns recent visual attachments for follow-up questions in the same conversation."""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT *
                FROM conversation_attachments
                WHERE conversation_id = ?
                  AND user_id = ?
                  AND message_id IS NOT NULL
                  AND (kind = 'image' OR extraction_status = 'low_text_quality')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (conversation_id, user_id, limit),
            ).fetchall()
            return [self._attachment_row_to_dict(row) for row in rows]
        except Exception as error:
            LOGGER.warning("[SafeMemory] list_recent_visual_attachments failed: %s", error)
            return []

    def list_conversation_attachments(self, conversation_id: str, user_id: str) -> list[dict]:
        """Returns all attachments associated with a conversation."""
        if self._conn is None:
            return []
        try:
            rows = self._conn.execute(
                """
                SELECT *
                FROM conversation_attachments
                WHERE conversation_id = ?
                  AND user_id = ?
                  AND message_id IS NOT NULL
                ORDER BY created_at ASC
                """,
                (conversation_id, user_id),
            ).fetchall()
            return [self._attachment_row_to_dict(row) for row in rows]
        except Exception as error:
            LOGGER.warning("[SafeMemory] list_conversation_attachments failed: %s", error)
            return []

    def bind_draft_attachments_to_message(
        self,
        *,
        attachment_ids: list[str],
        user_id: str,
        conversation_id: str,
        message_id: str,
    ) -> list[dict]:
        """Associates draft attachments with the persisted user message."""
        if not attachment_ids:
            return []
        if self._conn is None:
            raise ValueError("Attachment persistence requires SQLite memory.")

        for attachment_id in attachment_ids:
            row = self._conn.execute(
                """
                SELECT *
                FROM conversation_attachments
                WHERE id = ? AND user_id = ?
                """,
                (attachment_id, user_id),
            ).fetchone()
            if row is None:
                raise ValueError(f"Attachment not found: {attachment_id}")
            if row["message_id"]:
                raise ValueError(f"Attachment already sent: {attachment_id}")

        bound: list[dict] = []
        try:
            for attachment_id in attachment_ids:
                self._conn.execute(
                    """
                    UPDATE conversation_attachments
                    SET message_id = ?, conversation_id = ?
                    WHERE id = ? AND user_id = ?
                    """,
                    (message_id, conversation_id, attachment_id, user_id),
                )
                updated = self._conn.execute(
                    "SELECT * FROM conversation_attachments WHERE id = ?",
                    (attachment_id,),
                ).fetchone()
                if updated is not None:
                    bound.append(self._attachment_row_to_dict(updated))
            self._conn.commit()
            return bound
        except Exception:
            try:
                self._conn.rollback()
            except Exception:
                pass
            raise

    def delete_draft_attachment(self, attachment_id: str, user_id: str) -> bool:
        """Deletes a draft attachment that has not yet been sent."""
        if self._conn is None:
            return False
        row = self._conn.execute(
            """
            SELECT storage_path, message_id
            FROM conversation_attachments
            WHERE id = ? AND user_id = ?
            """,
            (attachment_id, user_id),
        ).fetchone()
        if row is None or row["message_id"]:
            return False
        storage_path = Path(str(row["storage_path"]))
        self._conn.execute(
            "DELETE FROM conversation_attachments WHERE id = ? AND user_id = ?",
            (attachment_id, user_id),
        )
        self._conn.commit()
        try:
            storage_path.unlink(missing_ok=True)
        except OSError:
            pass
        return True

    def cleanup_expired_draft_attachments(self, max_age_hours: int = 24) -> None:
        """Deletes abandoned draft attachments and their stored files."""
        if self._conn is None:
            return
        try:
            rows = self._conn.execute(
                """
                SELECT id, storage_path
                FROM conversation_attachments
                WHERE message_id IS NULL
                  AND created_at < datetime('now', ?)
                """,
                (f"-{int(max_age_hours)} hours",),
            ).fetchall()
            for row in rows:
                try:
                    Path(str(row["storage_path"])).unlink(missing_ok=True)
                except OSError:
                    pass
            self._conn.execute(
                """
                DELETE FROM conversation_attachments
                WHERE message_id IS NULL
                  AND created_at < datetime('now', ?)
                """,
                (f"-{int(max_age_hours)} hours",),
            )
            self._conn.commit()
        except Exception as error:
            LOGGER.warning("[SafeMemory] cleanup_expired_draft_attachments failed: %s", error)

    def _attachments_by_message_id(self, message_ids: list[str]) -> dict[str, list[dict]]:
        if self._conn is None or not message_ids:
            return {}
        placeholders = ",".join("?" for _ in message_ids)
        try:
            rows = self._conn.execute(
                f"""
                SELECT *
                FROM conversation_attachments
                WHERE message_id IN ({placeholders})
                ORDER BY created_at ASC
                """,
                tuple(message_ids),
            ).fetchall()
            grouped: dict[str, list[dict]] = {}
            for row in rows:
                message_id = str(row["message_id"] or "")
                grouped.setdefault(message_id, []).append(self._attachment_row_to_summary(row))
            return grouped
        except Exception as error:
            LOGGER.warning("[SafeMemory] _attachments_by_message_id failed: %s", error)
            return {}

    def _attachment_row_to_summary(self, row: sqlite3.Row) -> dict:
        preview_json = str(row["preview_json"] or "{}")
        try:
            preview = json.loads(preview_json)
        except json.JSONDecodeError:
            preview = {}
        return {
            "id": row["id"],
            "filename": row["filename"],
            "mime_type": row["mime_type"],
            "size_bytes": row["size_bytes"],
            "kind": row["kind"],
            "extraction_status": row["extraction_status"],
            "page_count": row["page_count"],
            "preview": preview,
        }

    def _attachment_row_to_dict(self, row: sqlite3.Row) -> dict:
        payload = self._attachment_row_to_summary(row)
        payload.update(
            {
                "message_id": row["message_id"] or "",
                "conversation_id": row["conversation_id"] or "",
                "user_id": row["user_id"],
                "storage_path": row["storage_path"],
                "sha256": row["sha256"],
                "extracted_text": row["extracted_text"] or "",
                "created_at": row["created_at"] or "",
            }
        )
        return payload

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def add_message(
        self,
        user_id: str,
        role: str,
        content: str,
        conversation_id: str | None = None,
        message_id: str | None = None,
    ) -> str:
        """Appends a message to the user's conversation history (RAM + SQLite)."""
        if conversation_id and not self.conversation_belongs_to_user(conversation_id, user_id):
            LOGGER.warning(
                "[SafeMemory] Refusing message for conversation %s owned by another user",
                conversation_id,
            )
            return ""
        safe_message_id = (message_id or "").strip() or str(uuid.uuid4())
        item = {"role": role, "content": content, "message_id": safe_message_id}
        if conversation_id:
            item["conversation_id"] = conversation_id
        self._store[user_id].append(item)

        if self._conn is None:
            return safe_message_id

        try:
            self._conn.execute(
                """
                INSERT INTO conversation_history (user_id, role, content, message_id, conversation_id)
                VALUES (?, ?, ?, ?, ?)
                """,
                (user_id, role, content, safe_message_id, conversation_id),
            )
            self._conn.commit()
            if conversation_id:
                self._touch_conversation(conversation_id)
            return safe_message_id
        except Exception as error:
            LOGGER.warning("[SafeMemory] SQLite write failed: %s", error)
            return ""

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
                SELECT role, content, message_id, conversation_id
                FROM conversation_history
                WHERE user_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (user_id, effective_limit),
            ).fetchall()

            messages = [
                {
                    "role": row["role"],
                    "content": row["content"],
                    "message_id": row["message_id"] or "",
                    "conversation_id": row["conversation_id"] or "",
                }
                for row in reversed(rows)
            ]

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
