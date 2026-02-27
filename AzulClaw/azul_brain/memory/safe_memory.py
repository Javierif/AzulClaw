"""Memoria conversacional de corto plazo basada en SQLite local."""

import os
import sqlite3
from pathlib import Path

class SafeMemory:
    """Gestiona historial conversacional estructurado por usuario."""

    def __init__(self, db_path: str | None = None):
        """Inicializa la base SQLite y asegura el esquema mínimo requerido."""
        default_db = Path(__file__).resolve().parent / "azul_memory.db"
        resolved_db = Path(db_path) if db_path else default_db
        resolved_db.parent.mkdir(parents=True, exist_ok=True)

        self.db_path = resolved_db
        self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS conversations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                timestamp TEXT NOT NULL DEFAULT (datetime('now')),
                role TEXT NOT NULL,
                content TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    @classmethod
    def from_env(cls):
        """Crea instancia leyendo ruta opcional desde AZUL_MEMORY_DB_PATH."""
        return cls(os.environ.get("AZUL_MEMORY_DB_PATH"))

    def add_message(self, user_id: str, role: str, content: str) -> None:
        """Guarda un mensaje (usuario o asistente) en la tabla de conversaciones."""
        self.conn.execute(
            "INSERT INTO conversations (user_id, role, content) VALUES (?, ?, ?)",
            (user_id, role, content),
        )
        self.conn.commit()

    def get_history(self, user_id: str, limit: int = 20) -> list[dict]:
        """Recupera historial reciente de un usuario en orden cronológico."""
        bounded_limit = max(1, min(limit, 100))
        cursor = self.conn.execute(
            """
            SELECT role, content
            FROM conversations
            WHERE user_id = ?
            ORDER BY id DESC
            LIMIT ?
            """,
            (user_id, bounded_limit),
        )
        rows = cursor.fetchall()
        rows.reverse()
        return [{"role": role, "content": content} for role, content in rows]