"""Persistencia local del estado de Hatching."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from datetime import datetime


@dataclass
class HatchingProfile:
    """Configuracion base del agente definida en Hatching."""

    name: str = "AzulClaw"
    role: str = "Companero tecnico local"
    mission: str = "Ayudarte sin perder seguridad ni contexto."
    tone: str = "Directo"
    style: str = "Explicativo"
    autonomy: str = "Autonomo moderado"
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
    """Lee y escribe el perfil de Hatching en disco local."""

    def __init__(self, profile_path: Path | None = None):
        self.profile_path = profile_path or _default_profile_path()
        self.profile_path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> HatchingProfile:
        """Carga el perfil o devuelve uno por defecto si no existe."""
        if not self.profile_path.exists():
            return HatchingProfile()

        data = json.loads(self.profile_path.read_text(encoding="utf-8"))
        return HatchingProfile(**data)

    def save(self, profile: HatchingProfile) -> HatchingProfile:
        """Persiste el perfil y devuelve la version almacenada."""
        if profile.is_hatched and not profile.completed_at:
            profile.completed_at = datetime.utcnow().isoformat(timespec="seconds") + "Z"
        self.profile_path.write_text(
            json.dumps(asdict(profile), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return profile
