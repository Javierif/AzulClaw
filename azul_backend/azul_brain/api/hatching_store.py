"""Local persistence for the Hatching state."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path


def _default_workspace_root() -> str:
    """Sandbox folder for MCP + desktop; override with AZUL_WORKSPACE_ROOT."""
    override = os.environ.get("AZUL_WORKSPACE_ROOT", "").strip()
    if override:
        return override
    return str(Path.home() / "Documents" / "dev" / "AzulWorkspace")


_AZUL_STATE_DIR = ".azul"
_MEMORY_DB_FILENAME = "azul_memory.db"


def resolve_memory_db_path() -> str:
    """SQLite path shared by vector store, SafeMemory, and episodic store.

    If ``AZUL_MEMORY_DB_PATH`` is set, it wins. Otherwise the file is
    ``<workspace_root>/.azul/azul_memory.db`` from the hatching profile.
    """
    env_path = os.environ.get("AZUL_MEMORY_DB_PATH", "").strip()
    if env_path:
        return env_path
    profile = HatchingStore().load()
    root = Path(profile.workspace_root).expanduser()
    return str(root / _AZUL_STATE_DIR / _MEMORY_DB_FILENAME)


@dataclass
class HatchingProfile:
    """Base agent configuration defined during Hatching."""

    name: str = "AzulClaw"
    role: str = "Local technical companion"
    mission: str = "Help you without losing safety or context."
    tone: str = "Direct"
    style: str = "Explanatory"
    autonomy: str = "Moderately autonomous"

    workspace_root: str = field(default_factory=_default_workspace_root)
    confirm_sensitive_actions: bool = True
    is_hatched: bool = False
    completed_at: str = ""
    skills: list[str] = field(
        default_factory=lambda: ["Email", "Telegram", "Workspace", "Memory"]
    )
    skill_configs: dict[str, dict[str, str]] = field(default_factory=dict)


def _default_profile_path() -> Path:
    return Path(__file__).resolve().parents[3] / "memory" / "hatching_profile.json"


class HatchingStore:
    """Reads and writes the Hatching profile to local disk."""

    def __init__(self, profile_path: Path | None = None):
        self.profile_path = profile_path or _default_profile_path()
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> HatchingProfile:
        """Loads the profile or returns a default one if it does not exist."""
        if not self.profile_path.exists():
            return HatchingProfile()

        data = json.loads(self.profile_path.read_text(encoding="utf-8"))
        data.pop("archetype", None)
        return HatchingProfile(**data)

    def save(self, profile: HatchingProfile) -> HatchingProfile:
        """Persists the profile and returns the stored version."""
        if profile.is_hatched and not profile.completed_at:
            profile.completed_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self.profile_path.write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return profile
