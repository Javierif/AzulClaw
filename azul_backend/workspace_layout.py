"""Default folders and starter doc for the AzulWorkspace sandbox."""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

LOGGER = logging.getLogger(__name__)

_SEED_DIRS = ("Inbox", "Projects", "Generated")
_AZUL_STATE_DIR_NAME = ".azul"

_WORKSPACE_GUIDE = """# AzulWorkspace

This folder is AzulClaw's sandbox: the agent can only read and write here.

| Path | Purpose |
|------|---------|
| `Inbox/` | Drop notes, imports, quick captures |
| `Projects/` | Longer-lived work |
| `Generated/` | Outputs the agent creates for you |
| `HEARTBEAT.md` | Scheduler checklist (lines starting with `#` are ignored) |
| `.azul/` | Local SQLite memory (do not delete) |

Nothing here is uploaded automatically; it stays on your machine.
"""


def _ensure_memory_db(azul_dir: Path) -> None:
    """Creates the SQLite memory file with WAL mode if it does not already exist."""
    db_file = azul_dir / "azul_memory.db"
    if db_file.exists():
        return
    try:
        conn = sqlite3.connect(str(db_file))
        conn.execute("PRAGMA journal_mode=WAL")
        conn.close()
        LOGGER.info("[Workspace] Created memory database at %s", db_file)
    except Exception as error:
        LOGGER.warning("[Workspace] Could not create memory database: %s", error)


def ensure_workspace_scaffold(root: Path) -> None:
    """Creates default subfolders, the memory DB, and a one-time guide; never deletes user files."""
    expanded = root.expanduser()
    expanded.mkdir(parents=True, exist_ok=True)
    for name in _SEED_DIRS:
        (expanded / name).mkdir(exist_ok=True)
    azul_dir = expanded / _AZUL_STATE_DIR_NAME
    azul_dir.mkdir(exist_ok=True)
    _ensure_memory_db(azul_dir)
    guide = expanded / "WORKSPACE.md"
    if not guide.exists():
        guide.write_text(_WORKSPACE_GUIDE, encoding="utf-8")
        LOGGER.info(
            "[Workspace] Seeded scaffold under %s",
            expanded.resolve(),
        )
