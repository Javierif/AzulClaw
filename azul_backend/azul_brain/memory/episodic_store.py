"""Episodic memory: session-level diary with automatic summaries.

Each conversation session is recorded as an *episode*.  When a session ends
(or after prolonged inactivity), the system generates a short LLM summary of
what was discussed.  This allows the agent to recall "what we did yesterday"
without replaying the full history.

Episodes are stored in the same SQLite database as the vector store
(``azul_memory.db``) but in a dedicated ``episodes`` table.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from ..api.hatching_store import resolve_memory_db_path

if TYPE_CHECKING:
    from ..runtime.agent_runtime import AgentRuntimeManager

LOGGER = logging.getLogger(__name__)


class EpisodicStore:
    """Manages session episodes (the agent's diary)."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn
        self._init_schema()

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_connection(cls, conn: sqlite3.Connection) -> "EpisodicStore":
        """Creates an EpisodicStore reusing an existing SQLite connection."""
        return cls(conn)

    @classmethod
    def from_env(cls) -> "EpisodicStore":
        """Creates an EpisodicStore from environment variables."""
        db_path = resolve_memory_db_path()
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        return cls(conn)

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    def _init_schema(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS episodes (
                id              TEXT PRIMARY KEY,
                user_id         TEXT NOT NULL,
                session_id      TEXT NOT NULL,
                summary         TEXT,
                started_at      TEXT DEFAULT (datetime('now')),
                ended_at        TEXT,
                message_count   INTEGER DEFAULT 0,
                topics          TEXT
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_episodes_user
            ON episodes(user_id)
        """)
        self._conn.commit()

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def start_session(self, user_id: str) -> str:
        """Opens a new episode for the user.  Returns the session_id."""
        session_id = str(uuid.uuid4())
        episode_id = str(uuid.uuid4())
        self._conn.execute(
            """
            INSERT INTO episodes (id, user_id, session_id, started_at)
            VALUES (?, ?, ?, ?)
            """,
            (episode_id, user_id, session_id, _now_iso()),
        )
        self._conn.commit()
        LOGGER.info("[Episodic] Session %s started for user %s", session_id, user_id)
        return session_id

    def increment_message_count(self, session_id: str) -> None:
        """Bumps the message counter for the active session."""
        self._conn.execute(
            "UPDATE episodes SET message_count = message_count + 1 WHERE session_id = ?",
            (session_id,),
        )
        self._conn.commit()

    async def end_session(
        self,
        session_id: str,
        messages: list[dict],
        runtime_manager: "AgentRuntimeManager | None" = None,
    ) -> None:
        """Closes a session and generates an automatic summary if possible."""
        summary = None
        topics: list[str] = []

        if runtime_manager and messages:
            summary, topics = await self._generate_summary(messages, runtime_manager)

        self._conn.execute(
            """
            UPDATE episodes
            SET ended_at = ?, summary = ?, topics = ?
            WHERE session_id = ?
            """,
            (_now_iso(), summary, json.dumps(topics, ensure_ascii=False), session_id),
        )
        self._conn.commit()
        LOGGER.info("[Episodic] Session %s ended. Summary: %s", session_id, summary[:80] if summary else "None")

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_recent_episodes(self, user_id: str, limit: int = 5) -> list[dict]:
        """Returns the most recent episodes for a user."""
        rows = self._conn.execute(
            """
            SELECT id, session_id, summary, started_at, ended_at,
                   message_count, topics
            FROM episodes
            WHERE user_id = ?
            ORDER BY started_at DESC
            LIMIT ?
            """,
            (user_id, limit),
        ).fetchall()

        results = []
        for row in rows:
            entry = dict(row)
            # Parse topics JSON
            raw_topics = entry.get("topics")
            if raw_topics:
                try:
                    entry["topics"] = json.loads(raw_topics)
                except (json.JSONDecodeError, TypeError):
                    entry["topics"] = []
            else:
                entry["topics"] = []
            results.append(entry)
        return results

    def get_active_session(self, user_id: str) -> dict | None:
        """Returns the currently open (un-ended) session, if any."""
        row = self._conn.execute(
            """
            SELECT id, session_id, started_at, message_count
            FROM episodes
            WHERE user_id = ? AND ended_at IS NULL
            ORDER BY started_at DESC
            LIMIT 1
            """,
            (user_id,),
        ).fetchone()
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Summary generation
    # ------------------------------------------------------------------

    async def _generate_summary(
        self,
        messages: list[dict],
        runtime_manager: "AgentRuntimeManager",
    ) -> tuple[str, list[str]]:
        """Uses the fast LLM to produce a session summary and topic list."""
        # Build a condensed transcript (last 30 messages max)
        transcript_lines: list[str] = []
        for msg in messages[-30:]:
            role = msg.get("role", "user")
            content = str(msg.get("content", "")).strip()
            if content:
                transcript_lines.append(f"{role}: {content[:200]}")

        transcript = "\n".join(transcript_lines)

        from agent_framework import Message

        prompt = Message(
            role="user",
            contents=(
                "Generate a brief summary (2-3 sentences max) of the following "
                "conversation session.  Also list the main topics discussed as a "
                "JSON array of short strings.\n\n"
                "Respond in this exact format:\n"
                "SUMMARY: <your summary>\n"
                "TOPICS: [\"topic1\", \"topic2\"]\n\n"
                f"Transcript:\n{transcript}"
            ),
        )

        try:
            result = await runtime_manager.execute_messages(
                messages=[prompt],
                lane="fast",
                title="Session summary",
                source="episodic-store",
                kind="summary",
            )
            return self._parse_summary_response(result.text)
        except Exception as error:
            LOGGER.warning("[Episodic] Summary generation failed: %s", error)
            return None, []

    @staticmethod
    def _parse_summary_response(text: str) -> tuple[str | None, list[str]]:
        """Parses the structured summary+topics response from the LLM."""
        summary = None
        topics: list[str] = []

        for line in text.strip().splitlines():
            line = line.strip()
            if line.upper().startswith("SUMMARY:"):
                summary = line.split(":", 1)[1].strip()
            elif line.upper().startswith("TOPICS:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        topics = [str(t) for t in parsed]
                except json.JSONDecodeError:
                    pass

        return summary, topics


# ------------------------------------------------------------------
# Utility
# ------------------------------------------------------------------

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
