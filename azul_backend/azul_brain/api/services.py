"""Servicios para endpoints de la desktop app."""

from __future__ import annotations

from pathlib import Path

from azul_backend.azul_hands_mcp.path_validator import PathValidator

from .hatching_store import HatchingProfile, HatchingStore


def get_workspace_root() -> Path:
    """Devuelve la raiz del workspace sandbox de AzulClaw."""
    profile = HatchingStore().load()
    workspace_root = Path(profile.workspace_root)
    workspace_root.mkdir(parents=True, exist_ok=True)
    return workspace_root


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


def summarize_processes(process_registry) -> list[dict]:
    """Devuelve procesos reales del runtime para la desktop app."""
    items = []
    for item in process_registry.list_processes():
        items.append(
            {
                "id": item["id"],
                "title": item["title"],
                "status": item["status"],
                "skill": item["source"],
                "kind": item["kind"],
                "lane": item["lane"],
                "started_at": item["started_at"],
                "updated_at": item["updated_at"],
                "model_label": item.get("model_label", ""),
                "detail": item["detail"],
            }
        )
    return items


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


def summarize_runtime(runtime_manager, scheduler, process_registry) -> dict:
    """Devuelve estado agregado de modelos, scheduler y heartbeats."""
    settings = runtime_manager.load_settings()
    scheduler_status = scheduler.get_status()
    return {
        "default_lane": settings.default_lane,
        "models": runtime_manager.list_model_status(),
        "heartbeat": scheduler_status["heartbeat"],
        "jobs_total": scheduler_status["jobs_total"],
        "jobs_running": scheduler_status["jobs_running"],
        "processes_visible": len(process_registry.list_processes()),
    }


def summarize_jobs(store) -> list[dict]:
    """Lista jobs programados para la desktop app."""
    items = []
    for job in store.load_jobs():
        items.append(
            {
                "id": job.id,
                "name": job.name,
                "prompt": job.prompt,
                "lane": job.lane,
                "schedule_kind": job.schedule_kind,
                "run_at": job.run_at,
                "interval_seconds": job.interval_seconds,
                "enabled": job.enabled,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "last_run_at": job.last_run_at,
                "next_run_at": job.next_run_at,
            }
        )
    return items


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
