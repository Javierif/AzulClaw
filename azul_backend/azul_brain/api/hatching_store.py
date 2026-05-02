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
_MEMORY_SETTINGS_FILENAME = "memory_settings.json"


@dataclass
class MemorySettings:
    """User-editable memory persistence settings."""

    memory_db_path: str = ""
    vector_memory_enabled: bool = True


def _runtime_root() -> Path:
    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "memory"


def _default_profile_path() -> Path:
    return _runtime_root() / "hatching_profile.json"


def _memory_settings_path() -> Path:
    return _runtime_root() / _MEMORY_SETTINGS_FILENAME


def default_memory_db_path() -> str:
    """Returns the default SQLite DB path derived from the current workspace."""
    profile = HatchingStore().load()
    root = Path(profile.workspace_root).expanduser()
    return str(root / _AZUL_STATE_DIR / _MEMORY_DB_FILENAME)


def load_memory_settings() -> MemorySettings:
    """Loads user memory settings, falling back to legacy env vars if no file exists."""
    settings_path = _memory_settings_path()
    if settings_path.exists():
        try:
            raw = json.loads(settings_path.read_text(encoding="utf-8"))
            return MemorySettings(
                memory_db_path=str(raw.get("memory_db_path", "")).strip(),
                vector_memory_enabled=bool(raw.get("vector_memory_enabled", True)),
            )
        except Exception:
            return MemorySettings()

    legacy_path = os.environ.get("AZUL_MEMORY_DB_PATH", "").strip()
    legacy_vector_enabled = (
        os.environ.get("VECTOR_MEMORY_ENABLED", "true").strip().lower() != "false"
    )
    return MemorySettings(
        memory_db_path=legacy_path,
        vector_memory_enabled=legacy_vector_enabled,
    )


def save_memory_settings(settings: MemorySettings) -> MemorySettings:
    """Persists user memory settings under the local runtime directory."""
    settings_path = _memory_settings_path()
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    memory_db_path = settings.memory_db_path.strip()
    if memory_db_path:
        memory_db_path = str(Path(memory_db_path).expanduser())
    cleaned = MemorySettings(
        memory_db_path=memory_db_path,
        vector_memory_enabled=bool(settings.vector_memory_enabled),
    )
    settings_path.write_text(
        json.dumps(asdict(cleaned), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return cleaned


def reset_memory_settings() -> None:
    """Removes persisted memory Settings so defaults apply again."""
    try:
        _memory_settings_path().unlink(missing_ok=True)
    except OSError:
        pass


def resolve_memory_db_path() -> str:
    """SQLite path shared by vector store, SafeMemory, and episodic store.

    Settings win. Legacy ``AZUL_MEMORY_DB_PATH`` is only used when no
    persisted memory settings exist.
    """
    configured_path = load_memory_settings().memory_db_path.strip()
    if configured_path:
        return str(Path(configured_path).expanduser())
    return default_memory_db_path()


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


def is_vector_memory_enabled() -> bool:
    """Returns whether semantic/vector memory is enabled in Settings."""
    return load_memory_settings().vector_memory_enabled


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
