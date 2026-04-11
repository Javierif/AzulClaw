"""Servicios para endpoints de la desktop app."""

from __future__ import annotations

from pathlib import Path

from azul_backend.azul_hands_mcp.path_validator import PathValidator

from .hatching_store import HatchingProfile, HatchingStore


def get_workspace_root() -> Path:
    """Devuelve la raiz del workspace sandbox de AzulClaw."""
    return Path.home() / "Desktop" / "AzulWorkspace"


def build_workspace_validator() -> PathValidator:
    """Construye un validador de rutas para el workspace desktop."""
    return PathValidator(str(get_workspace_root()))


def list_workspace_entries(relative_path: str = ".") -> dict:
    """Lista entradas de una carpeta del workspace con metadata simple."""
    validator = build_workspace_validator()
    safe_dir = validator.safe_resolve(relative_path or ".")

    if not safe_dir.exists():
        safe_dir.mkdir(parents=True, exist_ok=True)

    if not safe_dir.is_dir():
        raise ValueError(f"La ruta '{relative_path}' no es un directorio valido.")

    entries = []
    for child in sorted(safe_dir.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        relative_name = "."
        if child != validator.allowed_base:
            relative_name = str(child.relative_to(validator.allowed_base)).replace("\\", "/")
        entries.append(
            {
                "name": child.name,
                "path": relative_name,
                "kind": "folder" if child.is_dir() else "file",
            }
        )

    current_path = "."
    if safe_dir != validator.allowed_base:
        current_path = str(safe_dir.relative_to(validator.allowed_base)).replace("\\", "/")

    return {
        "root": str(validator.allowed_base),
        "current_path": current_path,
        "entries": entries,
    }


def summarize_processes() -> list[dict]:
    """Devuelve procesos base para la desktop app mientras no exista event bus real."""
    return [
        {
            "id": "workspace-scan",
            "title": "Exploracion del sandbox",
            "status": "running",
            "skill": "Workspace",
            "started_at": "now",
            "detail": "Inspeccionando contenido reciente dentro de AzulWorkspace.",
        },
        {
            "id": "memory-sync",
            "title": "Sincronizacion de memoria local",
            "status": "done",
            "skill": "Memory",
            "started_at": "recent",
            "detail": "Persistencia de historial y recuerdos recientes completada.",
        },
        {
            "id": "approval-gate",
            "title": "Aprobaciones sensibles",
            "status": "waiting",
            "skill": "Security",
            "started_at": "idle",
            "detail": "Esperando confirmacion del usuario para acciones de mayor riesgo.",
        },
    ]


def summarize_memory(orchestrator, user_id: str) -> list[dict]:
    """Devuelve una vista simple de memoria para la app desktop."""
    profile = HatchingStore().load()
    history = orchestrator.memory.get_history(user_id, limit=12)
    records = [
        {
            "id": "pref-directness",
            "title": f"Tono preferido: {profile.tone}",
            "kind": "preference",
            "source": "hatching-profile",
            "pinned": True,
        }
    ]

    for index, item in enumerate(reversed(history), start=1):
        content = item.get("content", "")
        compact_content = content if len(content) <= 80 else f"{content[:77]}..."
        records.append(
            {
                "id": f"history-{index}",
                "title": compact_content or "(sin contenido)",
                "kind": "episodic",
                "source": item.get("role", "unknown"),
                "pinned": False,
            }
        )

    return records


def load_hatching_profile() -> dict:
    """Devuelve el perfil actual de Hatching como diccionario serializable."""
    return HatchingStore().load().__dict__


def _sanitize_skill_configs(raw_configs: object, fallback: dict[str, dict[str, str]]) -> dict[str, dict[str, str]]:
    if not isinstance(raw_configs, dict):
        return fallback

    cleaned: dict[str, dict[str, str]] = {}
    for skill, config in raw_configs.items():
        skill_name = str(skill).strip()
        if not skill_name or not isinstance(config, dict):
            continue

        entries = {
            str(key).strip(): str(value).strip()
            for key, value in config.items()
            if str(key).strip() and str(value).strip()
        }
        cleaned[skill_name] = entries

    return cleaned


def save_hatching_profile(payload: dict) -> dict:
    """Valida y persiste el perfil de Hatching."""
    current = HatchingStore().load()

    profile = HatchingProfile(
        name=str(payload.get("name", current.name)).strip() or current.name,
        role=str(payload.get("role", current.role)).strip() or current.role,
        mission=str(payload.get("mission", current.mission)).strip() or current.mission,
        tone=str(payload.get("tone", current.tone)).strip() or current.tone,
        style=str(payload.get("style", current.style)).strip() or current.style,
        autonomy=str(payload.get("autonomy", current.autonomy)).strip() or current.autonomy,
        archetype=str(payload.get("archetype", current.archetype)).strip() or current.archetype,
        workspace_root=str(payload.get("workspace_root", current.workspace_root)).strip()
        or current.workspace_root,
        confirm_sensitive_actions=bool(
            payload.get("confirm_sensitive_actions", current.confirm_sensitive_actions)
        ),
        is_hatched=bool(payload.get("is_hatched", current.is_hatched)),
        completed_at=str(payload.get("completed_at", current.completed_at)).strip()
        or current.completed_at,
        skills=[
            str(skill).strip()
            for skill in payload.get("skills", current.skills)
            if str(skill).strip()
        ]
        or current.skills,
        skill_configs=_sanitize_skill_configs(
            payload.get("skill_configs", current.skill_configs),
            current.skill_configs,
        ),
    )

    return HatchingStore().save(profile).__dict__
