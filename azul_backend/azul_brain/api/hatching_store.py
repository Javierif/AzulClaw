"""Local persistence for the Hatching state."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import datetime


@dataclass
class HatchingProfile:
    """Base agent configuration defined during Hatching."""

    name: str = "AzulClaw"
    role: str = "Local technical companion"
    mission: str = "Help you without losing safety or context."
    tone: str = "Direct"
    style: str = "Explanatory"
    autonomy: str = "Moderately autonomous"
    archetype: str = "Companion"
    workspace_root: str = field(
        default_factory=lambda: str(Path.home() / "Desktop" / "AzulWorkspace")
    )
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
