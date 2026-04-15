"""Services for the desktop app endpoints."""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from azul_backend.azul_hands_mcp.path_validator import PathValidator
from azul_backend.workspace_layout import ensure_workspace_scaffold

from .hatching_store import (
    HatchingProfile,
    HatchingStore,
    _AZUL_STATE_DIR,
    resolve_memory_db_path,
)

LOGGER = logging.getLogger(__name__)

WIPE_CONFIRMATION_PHRASE = "RESET_ALL_LOCAL_DATA"


def get_workspace_root() -> Path:
    """Returns the root of the AzulClaw sandbox workspace."""
    profile = HatchingStore().load()
    workspace_root = Path(profile.workspace_root).expanduser()
    workspace_root.mkdir(parents=True, exist_ok=True)
    ensure_workspace_scaffold(workspace_root)
    return workspace_root


def build_workspace_validator() -> PathValidator:
    """Builds a path validator for the desktop workspace."""
    return PathValidator(str(get_workspace_root()))


def list_workspace_entries(relative_path: str = ".") -> dict:
    """Lists entries in a workspace folder with basic metadata."""
    validator = build_workspace_validator()
    safe_dir = validator.safe_resolve(relative_path or ".")

    if not safe_dir.exists():
        safe_dir.mkdir(parents=True, exist_ok=True)

    if not safe_dir.is_dir():
        raise ValueError(f"Path '{relative_path}' is not a valid directory.")

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
    """Returns real runtime processes for the desktop app."""
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
    """Returns the memory view for the desktop app, sourced from the vector store and history."""
    records: list[dict] = []

    # Preferences and facts persisted in SQLite (extracted + seeded from hatching profile)
    vector_memory = getattr(orchestrator, "vector_memory", None)
    if vector_memory is not None:
        try:
            for item in vector_memory.get_user_knowledge(user_id, limit=50):
                category = item.get("category", "fact")
                kind = "preference" if category == "preference" else "semantic"
                full_content = item["content"]
                short_title = full_content if len(full_content) <= 72 else f"{full_content[:69]}..."
                is_featured = item.get("feature_key") is not None
                records.append(
                    {
                        "id": item["id"],
                        "title": short_title,
                        "content": full_content,
                        "kind": kind,
                        "source": "featured" if is_featured else item.get("source", "extractor"),
                        "pinned": is_featured,
                        "created_at": item.get("created_at", ""),
                    }
                )
        except Exception as error:
            LOGGER.warning("[Memory] Could not load user knowledge: %s", error)

    return records


def summarize_runtime(runtime_manager, scheduler, process_registry) -> dict:
    """Returns aggregated model and scheduler status."""
    settings = runtime_manager.load_settings()
    scheduler_status = scheduler.get_status()
    return {
        "default_lane": settings.default_lane,
        "models": runtime_manager.list_model_status(),
        "scheduler_running": scheduler_status["scheduler_running"],
        "scheduler_last_error": scheduler_status["scheduler_last_error"],
        "jobs_total": scheduler_status["jobs_total"],
        "jobs_running": scheduler_status["jobs_running"],
        "processes_visible": len(process_registry.list_processes()),
    }


def summarize_jobs(store) -> list[dict]:
    """Lists scheduled jobs for the desktop app."""
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
                "system": job.system,
                "source": job.source,
                "created_at": job.created_at,
                "updated_at": job.updated_at,
                "last_run_at": job.last_run_at,
                "next_run_at": job.next_run_at,
            }
        )
    return items


def load_hatching_profile() -> dict:
    """Returns the current Hatching profile as a serialisable dictionary."""
    data = HatchingStore().load().__dict__.copy()
    data["memory_db_path"] = resolve_memory_db_path()
    return data


def _delete_sqlite_bundle(db_file: Path) -> None:
    """Removes the main DB file and common SQLite sidecar files in the same folder."""
    if not db_file.name:
        return
    parent = db_file.parent
    try:
        for candidate in parent.glob(db_file.name + "*"):
            if candidate.is_file():
                candidate.unlink(missing_ok=True)
    except OSError as error:
        LOGGER.warning("[Wipe] Could not remove SQLite files under %s: %s", parent, error)


def _remove_workspace_azul_store(db_file: Path, workspace_root: Path) -> bool:
    """Deletes ``<workspace>/.azul`` when it is the parent of the resolved DB path."""
    try:
        ws = workspace_root.expanduser().resolve()
        parent = db_file.parent
        if parent.name != _AZUL_STATE_DIR or not parent.is_dir():
            return False
        if parent.resolve().parent != ws:
            return False
        shutil.rmtree(parent, ignore_errors=True)
        return True
    except OSError as error:
        LOGGER.warning("[Wipe] Could not remove .azul directory: %s", error)
        return False


def wipe_local_user_data(confirmation: str) -> dict:
    """Deletes persistent memory (SQLite) and resets the Hatching profile.

    The running process still holds open DB handles; restart the brain after
    calling this so memory subsystems reopen a clean file.
    """
    if (confirmation or "").strip() != WIPE_CONFIRMATION_PHRASE:
        raise ValueError("Invalid confirmation phrase.")

    store = HatchingStore()
    profile = store.load()
    workspace = Path(profile.workspace_root)
    db_file = Path(resolve_memory_db_path())

    removed_azul = _remove_workspace_azul_store(db_file, workspace)
    if not removed_azul:
        _delete_sqlite_bundle(db_file)

    fresh = HatchingProfile()
    store.save(fresh)
    try:
        ensure_workspace_scaffold(Path(fresh.workspace_root).expanduser())
    except OSError as error:
        LOGGER.warning("[Workspace] Scaffold after wipe failed: %s", error)
    LOGGER.info("[Wipe] Local memory cleared and hatching profile reset to defaults.")

    data = fresh.__dict__.copy()
    data["memory_db_path"] = resolve_memory_db_path()
    data["restart_required"] = True
    return data


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
    """Validates and persists the Hatching profile."""
    current = HatchingStore().load()

    profile = HatchingProfile(
        name=str(payload.get("name", current.name)).strip() or current.name,
        role=str(payload.get("role", current.role)).strip() or current.role,
        mission=str(payload.get("mission", current.mission)).strip() or current.mission,
        tone=str(payload.get("tone", current.tone)).strip() or current.tone,
        style=str(payload.get("style", current.style)).strip() or current.style,
        autonomy=str(payload.get("autonomy", current.autonomy)).strip() or current.autonomy,

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

    saved = HatchingStore().save(profile)
    try:
        ensure_workspace_scaffold(Path(saved.workspace_root).expanduser())
    except OSError as error:
        LOGGER.warning("[Workspace] Scaffold after hatching save failed: %s", error)

    data = saved.__dict__.copy()
    data["memory_db_path"] = resolve_memory_db_path()
    return data
