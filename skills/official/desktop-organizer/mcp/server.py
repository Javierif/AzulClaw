"""Local MCP runtime for the Folder Organizer skill."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from collections import Counter, OrderedDict
from pathlib import Path, PurePosixPath
from uuid import uuid4

try:
    import mcp.types as types
    from mcp.server import Server
    from mcp.server.stdio import stdio_server
except ModuleNotFoundError:  # pragma: no cover - allows helper imports in lightweight test envs
    types = None
    Server = None
    stdio_server = None

app = Server("folder-organizer-skill") if Server is not None else None
LOGGER = logging.getLogger(__name__)

CATEGORY_BY_EXTENSION = {
    ".pdf": "Documents",
    ".doc": "Documents",
    ".docx": "Documents",
    ".txt": "Documents",
    ".md": "Documents",
    ".rtf": "Documents",
    ".odt": "Documents",
    ".csv": "Spreadsheets",
    ".xls": "Spreadsheets",
    ".xlsx": "Spreadsheets",
    ".ods": "Spreadsheets",
    ".ppt": "Presentations",
    ".pptx": "Presentations",
    ".odp": "Presentations",
    ".jpg": "Images",
    ".jpeg": "Images",
    ".png": "Images",
    ".gif": "Images",
    ".webp": "Images",
    ".bmp": "Images",
    ".svg": "Images",
    ".mp3": "Audio",
    ".wav": "Audio",
    ".m4a": "Audio",
    ".flac": "Audio",
    ".mp4": "Video",
    ".mov": "Video",
    ".avi": "Video",
    ".mkv": "Video",
    ".zip": "Archives",
    ".rar": "Archives",
    ".7z": "Archives",
    ".tar": "Archives",
    ".gz": "Archives",
    ".py": "Code",
    ".js": "Code",
    ".ts": "Code",
    ".tsx": "Code",
    ".jsx": "Code",
    ".json": "Code",
    ".html": "Code",
    ".css": "Code",
    ".yml": "Code",
    ".yaml": "Code",
    ".xml": "Code",
    ".sh": "Code",
    ".ps1": "Code",
}

CATEGORY_DIRECTORY_NAMES = frozenset(CATEGORY_BY_EXTENSION.values())
MAX_TOOL_RESULT_CHARS = 20_000
MAX_TOOL_RESULT_HEAD_ITEMS = 80
MAX_TOOL_RESULT_TAIL_ITEMS = 20
MIN_TOOL_RESULT_HEAD_ITEMS = 12
MAX_BATCH_RESULT_HEAD_ITEMS = 40
MAX_BATCH_RESULT_TAIL_ITEMS = 10
MAX_PLAN_SNAPSHOT_COUNT = 8
MAX_CATEGORY_NAME_CHARS = 80
TOOL_RESULT_TRUNCATION_NOTE = (
    "Result truncated to keep the planning context bounded. "
    "Use a narrower relative_path or lower max_depth to inspect the omitted items."
)
BATCHING_NOTE = (
    "Recursive organization is grouped by source subfolder to keep each batch bounded."
)
DETAIL_HINT = (
    "For detailed file moves, rerun the tool with batch_index or a narrower relative_path. "
    "Set include_moves=true only when you explicitly need raw move rows."
)
PLAN_TOKEN_HINT = (
    "Continue recursive organization with organize_target_folder using the same plan_token. "
    "Omit batch_index to execute the next pending batch automatically."
)
SEMANTIC_CATEGORY_NOTE = (
    "Semantic categorization is enabled for this skill. You may pass category_overrides "
    "mapping source_relative_path to a custom destination folder name."
)
INVALID_CATEGORY_NAME_CHARS = '<>:"/\\|?*'
PLAN_SNAPSHOTS: OrderedDict[str, dict[str, object]] = OrderedDict()


def _configured_folder() -> Path:
    folder = Path(os.environ.get("AZUL_SKILL_CONFIG_TARGETFOLDER", "").strip()).expanduser()
    if not str(folder):
        raise ValueError("Folder Organizer is missing AZUL_SKILL_CONFIG_TARGETFOLDER.")
    resolved = folder.resolve()
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Configured target folder does not exist: {resolved}")
    return resolved


def _configured_organization_depth() -> int:
    raw = os.environ.get("AZUL_SKILL_CONFIG_ORGANIZATIONDEPTH", "").strip()
    return _coerce_max_depth(raw, default=3)


def _configured_semantic_categorization() -> bool:
    return _coerce_bool(os.environ.get("AZUL_SKILL_CONFIG_SEMANTICCATEGORIZATION", "").strip())


def _is_hidden(path: Path) -> bool:
    return path.name.startswith(".")


def _category_for_file(path: Path) -> str:
    return CATEGORY_BY_EXTENSION.get(path.suffix.lower(), "Other")


def _coerce_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _coerce_max_depth(value: object, *, default: int = 3) -> int:
    if value in {None, ""}:
        return default
    if isinstance(value, bool):
        raise ValueError("max_depth must be an integer between 1 and 8.")
    try:
        depth = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("max_depth must be an integer between 1 and 8.") from error
    if depth < 1 or depth > 8:
        raise ValueError("max_depth must be an integer between 1 and 8.")
    return depth


def _coerce_batch_index(value: object) -> int | None:
    if value in {None, ""}:
        return None
    if isinstance(value, bool):
        raise ValueError("batch_index must be an integer greater than or equal to 1.")
    try:
        index = int(value)
    except (TypeError, ValueError) as error:
        raise ValueError("batch_index must be an integer greater than or equal to 1.") from error
    if index < 1:
        raise ValueError("batch_index must be an integer greater than or equal to 1.")
    return index


def _coerce_plan_token(value: object) -> str | None:
    token = str(value or "").strip()
    return token or None


def _normalize_source_override_path(value: object) -> str:
    raw = str(value or "").strip()
    if not raw:
        raise ValueError("category_overrides keys must be non-empty relative source paths.")
    relative = PurePosixPath(raw.replace("\\", "/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("category_overrides keys must be relative source paths inside the configured folder.")
    return "/".join(relative.parts)


def _sanitize_category_name(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("category_overrides values must be folder names as strings.")
    cleaned = "".join(" " if char in INVALID_CATEGORY_NAME_CHARS or ord(char) < 32 else char for char in value)
    normalized = " ".join(cleaned.split()).strip(" .")
    if len(normalized) > MAX_CATEGORY_NAME_CHARS:
        normalized = normalized[:MAX_CATEGORY_NAME_CHARS].rstrip(" .")
    if not normalized:
        raise ValueError("category_overrides values must resolve to a non-empty folder name.")
    return normalized


def _coerce_category_overrides(value: object) -> dict[str, str]:
    if value is None or value == "":
        return {}
    if not isinstance(value, dict):
        raise ValueError("category_overrides must be an object mapping source_relative_path to folder name.")
    overrides: dict[str, str] = {}
    for raw_path, raw_category in value.items():
        overrides[_normalize_source_override_path(raw_path)] = _sanitize_category_name(raw_category)
    return overrides


def _validate_batch_selection(
    *,
    recursive: bool,
    batch_index: int | None,
    plan_token: str | None = None,
) -> None:
    if batch_index is not None and not recursive:
        raise ValueError("batch_index requires recursive=true.")
    if plan_token is not None and not recursive:
        raise ValueError("plan_token requires recursive=true.")


def _validate_category_overrides(
    *,
    semantic_enabled: bool,
    category_overrides: dict[str, str],
) -> None:
    if category_overrides and not semantic_enabled:
        raise ValueError(
            "category_overrides requires semantic categorization enabled in the Folder Organizer skill settings."
        )


def _visible_entries(folder: Path) -> list[Path]:
    return sorted(
        (path for path in folder.iterdir() if not _is_hidden(path)),
        key=lambda path: (path.is_file(), path.name.lower()),
    )


def _relative_display_path(root: Path, path: Path) -> str:
    relative = path.resolve().relative_to(root.resolve())
    if not relative.parts:
        return "."
    return str(relative).replace("\\", "/")


def _resolve_relative_folder(root: Path, relative_path: object) -> Path:
    resolved_root = root.resolve()
    raw = str(relative_path or "").strip()
    if not raw or raw == ".":
        return resolved_root
    relative = PurePosixPath(raw.replace("\\", "/"))
    if relative.is_absolute() or any(part in {"", ".", ".."} for part in relative.parts):
        raise ValueError("relative_path must stay within the configured folder.")
    resolved = (resolved_root / Path(*relative.parts)).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("relative_path must stay within the configured folder.")
    if not resolved.exists() or not resolved.is_dir():
        raise ValueError(f"Requested folder does not exist: {resolved}")
    return resolved


def _entry_payload(root: Path, path: Path) -> dict[str, object]:
    payload: dict[str, object] = {
        "path": _relative_display_path(root, path),
        "name": path.name,
        "kind": "directory" if path.is_dir() else "file",
    }
    if path.is_dir():
        payload["child_count"] = len(_visible_entries(path))
        return payload
    payload["size_bytes"] = path.stat().st_size
    payload["category"] = _category_for_file(path)
    return payload


def _collect_entries(root: Path, folder: Path, *, recursive: bool, remaining_depth: int) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    for path in _visible_entries(folder):
        entries.append(_entry_payload(root, path))
        if recursive and path.is_dir() and remaining_depth > 1:
            entries.extend(_collect_entries(root, path, recursive=True, remaining_depth=remaining_depth - 1))
    return entries


def _list_folder_contents(
    root: Path,
    *,
    relative_path: object = ".",
    recursive: bool = False,
    max_depth: object = 3,
) -> dict[str, object]:
    target = _resolve_relative_folder(root, relative_path)
    recursive_flag = _coerce_bool(recursive)
    depth = _coerce_max_depth(max_depth)
    entries = _collect_entries(root, target, recursive=recursive_flag, remaining_depth=depth)
    return {
        "folder": str(root),
        "relative_path": _relative_display_path(root, target),
        "recursive": recursive_flag,
        "max_depth": depth if recursive_flag else 1,
        "entry_count": len(entries),
        "entries": entries,
    }


def _top_level_files(folder: Path) -> list[Path]:
    return [path for path in sorted(folder.iterdir()) if path.is_file() and not _is_hidden(path)]


def _is_scope_category_directory(scope_root: Path, path: Path) -> bool:
    return path.is_dir() and path.parent == scope_root and path.name in CATEGORY_DIRECTORY_NAMES


def _effective_plan_depth(*, recursive: bool, max_depth: object = None) -> int:
    if not recursive:
        return 1
    return _coerce_max_depth(max_depth, default=_configured_organization_depth())


def _collect_files_for_plan(scope_root: Path, folder: Path, *, remaining_depth: int) -> list[Path]:
    files: list[Path] = []
    for path in _visible_entries(folder):
        if path.is_file():
            files.append(path)
            continue
        if remaining_depth <= 1 or _is_scope_category_directory(scope_root, path):
            continue
        files.extend(_collect_files_for_plan(scope_root, path, remaining_depth=remaining_depth - 1))
    return files


def _sort_plan_files(scope_root: Path, files: list[Path]) -> list[Path]:
    return sorted(
        files,
        key=lambda path: (
            len(path.relative_to(scope_root).parts),
            _relative_display_path(scope_root, path).lower(),
        ),
    )


def _candidate_files(scope_root: Path, *, recursive: bool, max_depth: object = None) -> list[Path]:
    depth = _effective_plan_depth(recursive=recursive, max_depth=max_depth)
    if depth == 1:
        return _top_level_files(scope_root)
    return _sort_plan_files(scope_root, _collect_files_for_plan(scope_root, scope_root, remaining_depth=depth))


def _slice_tool_items(
    items: list[dict[str, object]],
    *,
    head: int,
    tail: int,
) -> tuple[list[dict[str, object]], int]:
    if len(items) <= head + tail:
        return list(items), 0
    visible = list(items[:head])
    if tail:
        visible.extend(items[-tail:])
    return visible, len(items) - len(visible)


def _serialize_tool_payload(
    body: dict[str, object],
    *,
    array_key: str | None = None,
    item_label: str = "item",
) -> str:
    raw = json.dumps(body, indent=2)
    if len(raw) <= MAX_TOOL_RESULT_CHARS or not array_key:
        return raw

    original_items = body.get(array_key)
    if not isinstance(original_items, list):
        return raw

    items = [item for item in original_items if isinstance(item, dict)]
    if len(items) != len(original_items):
        return raw

    head = min(MAX_TOOL_RESULT_HEAD_ITEMS, len(items))
    tail = min(MAX_TOOL_RESULT_TAIL_ITEMS, max(0, len(items) - head))

    while True:
        visible, omitted = _slice_tool_items(items, head=head, tail=tail)
        payload = dict(body)
        payload[array_key] = visible
        payload["truncated"] = omitted > 0
        payload[f"total_{item_label}_count"] = len(items)
        payload[f"displayed_{item_label}_count"] = len(visible)
        payload[f"omitted_{item_label}_count"] = omitted
        payload["next_step_hint"] = TOOL_RESULT_TRUNCATION_NOTE
        raw = json.dumps(payload, indent=2)
        if len(raw) <= MAX_TOOL_RESULT_CHARS:
            return raw
        if head <= MIN_TOOL_RESULT_HEAD_ITEMS:
            payload[array_key] = []
            payload[f"displayed_{item_label}_count"] = 0
            payload[f"omitted_{item_label}_count"] = len(items)
            return json.dumps(payload, indent=2)
        head = max(MIN_TOOL_RESULT_HEAD_ITEMS, head // 2)
        tail = min(tail, max(0, head // 4))


def _summarize_plan_batch(
    folder_relative_path: str,
    items: list[dict[str, object]],
    *,
    batch_index: int,
) -> dict[str, object]:
    status_counts = Counter(str(item.get("status", "planned")) for item in items)
    category_counts = Counter(str(item.get("category", "Other")) for item in items)
    blocked_items = [
        {
            "source_relative_path": str(item.get("source_relative_path", "")),
            "destination_relative_path": str(item.get("destination_relative_path", "")),
            "reason": str(item.get("reason", "")),
        }
        for item in items
        if item.get("status") == "blocked"
    ]
    return {
        "batch_index": batch_index,
        "source_folder_relative_path": folder_relative_path,
        "item_count": len(items),
        "planned_count": status_counts.get("planned", 0),
        "blocked_count": status_counts.get("blocked", 0),
        "moved_count": status_counts.get("moved", 0),
        "summary": _plan_summary(items),
        "categories": dict(sorted(category_counts.items())),
        "blocked_items": blocked_items,
    }


def _group_plan_by_source_folder(plan: list[dict[str, object]]) -> list[tuple[str, list[dict[str, object]]]]:
    grouped: dict[str, list[dict[str, object]]] = {}
    ordered_keys: list[str] = []
    for item in plan:
        source_relative_path = str(item.get("source_relative_path", "") or "").replace("\\", "/").strip()
        folder_relative_path = str(PurePosixPath(source_relative_path).parent) if source_relative_path else "."
        if folder_relative_path in {"", "."}:
            folder_relative_path = "."
        if folder_relative_path not in grouped:
            grouped[folder_relative_path] = []
            ordered_keys.append(folder_relative_path)
        grouped[folder_relative_path].append(item)
    return [(key, grouped[key]) for key in ordered_keys]


def _build_plan_batches(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    return [
        _summarize_plan_batch(folder_relative_path, items, batch_index=index)
        for index, (folder_relative_path, items) in enumerate(_group_plan_by_source_folder(plan), start=1)
    ]


def _remember_plan_snapshot(
    *,
    folder: Path,
    relative_path: str,
    recursive: bool,
    max_depth: int,
    plan: list[dict[str, object]],
) -> dict[str, object] | None:
    grouped_plan = _group_plan_by_source_folder(plan)
    if not recursive or len(grouped_plan) <= 1:
        return None
    token = uuid4().hex[:12]
    snapshot = {
        "token": token,
        "folder": str(folder),
        "relative_path": relative_path,
        "recursive": recursive,
        "max_depth": max_depth,
        "grouped_plan": [
            (folder_relative_path, [dict(item) for item in items])
            for folder_relative_path, items in grouped_plan
        ],
        "completed_batch_indices": set(),
    }
    PLAN_SNAPSHOTS[token] = snapshot
    PLAN_SNAPSHOTS.move_to_end(token)
    while len(PLAN_SNAPSHOTS) > MAX_PLAN_SNAPSHOT_COUNT:
        PLAN_SNAPSHOTS.popitem(last=False)
    return snapshot


def _get_plan_snapshot(plan_token: str) -> dict[str, object]:
    snapshot = PLAN_SNAPSHOTS.get(plan_token)
    if snapshot is None:
        raise ValueError("plan_token is invalid or has expired.")
    PLAN_SNAPSHOTS.move_to_end(plan_token)
    return snapshot


def _release_plan_snapshot(plan_token: str) -> None:
    PLAN_SNAPSHOTS.pop(plan_token, None)


def _completed_snapshot_batch_indices(snapshot: dict[str, object]) -> set[int]:
    raw = snapshot.get("completed_batch_indices", set())
    return {int(index) for index in raw} if isinstance(raw, set) else set()


def _pending_snapshot_batch_indices(snapshot: dict[str, object]) -> list[int]:
    grouped_plan = snapshot.get("grouped_plan", [])
    if not isinstance(grouped_plan, list):
        return []
    completed = _completed_snapshot_batch_indices(snapshot)
    return [index for index in range(1, len(grouped_plan) + 1) if index not in completed]


def _snapshot_progress_payload(snapshot: dict[str, object]) -> dict[str, object]:
    pending = _pending_snapshot_batch_indices(snapshot)
    completed = _completed_snapshot_batch_indices(snapshot)
    batch_count = len(snapshot.get("grouped_plan", [])) if isinstance(snapshot.get("grouped_plan", []), list) else 0
    payload: dict[str, object] = {
        "plan_token": str(snapshot.get("token", "")),
        "plan_token_strategy": "source-folder-batches",
        "plan_batch_count": batch_count,
        "completed_batch_count": len(completed),
        "remaining_batch_count": len(pending),
        "next_batch_index": pending[0] if pending else None,
        "plan_complete": not pending,
    }
    if pending:
        payload["workflow_hint"] = f"{PLAN_TOKEN_HINT} Next batch: {pending[0]}."
    else:
        payload["workflow_hint"] = "All recursive batches from this plan_token have been completed."
    return payload


def _apply_plan_snapshot_metadata(body: dict[str, object], snapshot: dict[str, object]) -> dict[str, object]:
    enriched = dict(body)
    enriched.update(_snapshot_progress_payload(snapshot))
    return enriched


def _select_snapshot_items(
    snapshot: dict[str, object],
    *,
    batch_index: int | None,
) -> tuple[list[dict[str, object]], dict[str, object] | None, int | None]:
    grouped_plan = snapshot.get("grouped_plan", [])
    if not isinstance(grouped_plan, list):
        return [], None, None
    pending_indices = _pending_snapshot_batch_indices(snapshot)
    effective_batch_index = batch_index if batch_index is not None else (pending_indices[0] if pending_indices else None)
    if effective_batch_index is None:
        return [], None, None
    if effective_batch_index < 1 or effective_batch_index > len(grouped_plan):
        raise ValueError(
            f"batch_index {effective_batch_index} is out of range for {len(grouped_plan)} available batch(es)."
        )
    if effective_batch_index not in pending_indices:
        raise ValueError(f"batch_index {effective_batch_index} has already been completed for this plan_token.")
    folder_relative_path, items = grouped_plan[effective_batch_index - 1]
    batch_items = [dict(item) for item in items]
    return batch_items, _summarize_plan_batch(
        str(folder_relative_path),
        batch_items,
        batch_index=effective_batch_index,
    ), effective_batch_index


def _mark_snapshot_batch_completed(snapshot: dict[str, object], batch_index: int) -> None:
    completed = _completed_snapshot_batch_indices(snapshot)
    completed.add(batch_index)
    snapshot["completed_batch_indices"] = completed


def _apply_batch_metadata(body: dict[str, object], batches: list[dict[str, object]]) -> dict[str, object]:
    if len(batches) <= 1:
        return body
    visible_batches, omitted = _slice_tool_items(
        batches,
        head=MAX_BATCH_RESULT_HEAD_ITEMS,
        tail=MAX_BATCH_RESULT_TAIL_ITEMS,
    )
    enriched = dict(body)
    enriched["batched"] = True
    enriched["batching_strategy"] = "source-folder"
    enriched["batch_count"] = len(batches)
    enriched["displayed_batch_count"] = len(visible_batches)
    enriched["omitted_batch_count"] = omitted
    enriched["batching_note"] = BATCHING_NOTE
    enriched["batches"] = visible_batches
    return enriched


def _select_plan_batch(
    grouped_plan: list[tuple[str, list[dict[str, object]]]],
    batch_index: int | None,
) -> tuple[str, list[dict[str, object]]] | None:
    if batch_index is None:
        return None
    if batch_index > len(grouped_plan):
        raise ValueError(f"batch_index {batch_index} is out of range for {len(grouped_plan)} available batch(es).")
    return grouped_plan[batch_index - 1]


def _select_plan_items(
    plan: list[dict[str, object]],
    *,
    batch_index: int | None,
) -> tuple[list[dict[str, object]], dict[str, object] | None]:
    grouped_plan = _group_plan_by_source_folder(plan)
    selected_batch = _select_plan_batch(grouped_plan, batch_index)
    if selected_batch is None:
        return list(plan), None
    batch_folder_relative_path, batch_items = selected_batch
    return list(batch_items), _summarize_plan_batch(
        batch_folder_relative_path,
        batch_items,
        batch_index=batch_index or 1,
    )


def _build_plan_tool_body(
    *,
    folder: Path,
    relative_path: object,
    recursive: bool,
    max_depth: int,
    plan: list[dict[str, object]],
    dry_run: bool | None = None,
    batches: list[dict[str, object]] | None = None,
    include_moves: bool = True,
    batch_index: int | None = None,
) -> dict[str, object]:
    grouped_plan = _group_plan_by_source_folder(plan) if recursive else []
    selected_batch = _select_plan_batch(grouped_plan, batch_index)
    effective_batches = list(batches or [])
    body = {
        "folder": str(folder),
        "relative_path": _relative_display_path(folder, _resolve_relative_folder(folder, relative_path)),
        "recursive": recursive,
        "max_depth": max_depth,
        "summary": _plan_summary(plan),
        "blocked_items": [
            {
                "source_relative_path": str(item.get("source_relative_path", "")),
                "destination_relative_path": str(item.get("destination_relative_path", "")),
                "reason": str(item.get("reason", "")),
            }
            for item in plan
            if item.get("status") == "blocked"
        ],
    }
    if dry_run is not None:
        body["dry_run"] = dry_run

    if selected_batch is not None:
        batch_folder_relative_path, batch_items = selected_batch
        body["selected_batch"] = _summarize_plan_batch(
            batch_folder_relative_path,
            batch_items,
            batch_index=batch_index or 1,
        )
        body["move_detail_mode"] = "selected-batch"
        body["detail_hint"] = DETAIL_HINT
        body["moves"] = list(batch_items)
    elif recursive and len(effective_batches) > 1 and not include_moves:
        body["move_detail_mode"] = "summary-first"
        body["detail_hint"] = DETAIL_HINT
        body["moves_omitted_for_batching"] = True
        body["moves"] = []
    else:
        body["move_detail_mode"] = "full"
        body["moves"] = list(plan)

    return _apply_batch_metadata(body, effective_batches)


def _apply_categorization_metadata(
    body: dict[str, object],
    *,
    plan: list[dict[str, object]],
    semantic_enabled: bool,
    category_overrides: dict[str, str],
) -> dict[str, object]:
    enriched = dict(body)
    enriched["categorization_mode"] = "semantic" if semantic_enabled else "deterministic"
    if not semantic_enabled:
        return enriched
    used_override_paths = sorted(
        str(item.get("source_relative_path", ""))
        for item in plan
        if str(item.get("category_source", "")) == "semantic_override"
    )
    used_override_set = set(used_override_paths)
    unused_override_paths = sorted(path for path in category_overrides if path not in used_override_set)
    enriched["semantic_categorization_enabled"] = True
    enriched["semantic_note"] = SEMANTIC_CATEGORY_NOTE
    enriched["semantic_override_count"] = len(used_override_paths)
    enriched["unused_category_override_count"] = len(unused_override_paths)
    if unused_override_paths:
        enriched["unused_category_override_paths"] = unused_override_paths
    if used_override_paths:
        enriched["semantic_custom_categories"] = sorted(
            {str(item.get("category", "Other")) for item in plan if item.get("category_source") == "semantic_override"}
        )
    if enriched.get("move_detail_mode") == "summary-first":
        enriched["semantic_next_step"] = (
            "Inspect one batch with batch_index, then rerun preview_folder_organization or organize_target_folder "
            "with category_overrides for those source_relative_path values."
        )
    return enriched


def _build_base_plan(
    root: Path,
    *,
    relative_path: object = ".",
    recursive: bool = False,
    max_depth: object = None,
) -> list[dict[str, object]]:
    target = _resolve_relative_folder(root, relative_path)
    plan: list[dict[str, object]] = []
    reserved_destinations: dict[str, Path] = {}
    for path in _candidate_files(target, recursive=recursive, max_depth=max_depth):
        source_relative_path = _relative_display_path(root, path)
        category = _category_for_file(path)
        destination = target / category / path.name
        if path.resolve() == destination.resolve():
            continue
        destination_key = str(destination).lower()
        item: dict[str, object] = {
            "source": str(path),
            "destination": str(destination),
            "source_relative_path": source_relative_path,
            "destination_relative_path": _relative_display_path(root, destination),
            "category": category,
            "category_source": "extension_rule",
            "status": "planned",
        }
        if destination.exists() and destination.resolve() != path.resolve():
            item["status"] = "blocked"
            item["reason"] = f"Destination already exists: {_relative_display_path(root, destination)}"
        elif destination_key in reserved_destinations:
            item["status"] = "blocked"
            item["reason"] = (
                "Destination would collide with "
                f"{_relative_display_path(root, reserved_destinations[destination_key])}"
            )
        else:
            reserved_destinations[destination_key] = path
        plan.append(item)
    return plan


def _apply_category_overrides_to_plan(
    root: Path,
    *,
    relative_path: object,
    plan: list[dict[str, object]],
    category_overrides: dict[str, str],
) -> list[dict[str, object]]:
    if not category_overrides:
        return [dict(item) for item in plan]
    target = _resolve_relative_folder(root, relative_path)
    remapped: list[dict[str, object]] = []
    reserved_destinations: dict[str, Path] = {}
    for original in plan:
        item = dict(original)
        source = Path(str(item["source"]))
        source_relative_path = str(item.get("source_relative_path", "") or _relative_display_path(root, source))
        override_category = category_overrides.get(source_relative_path)
        category = override_category or str(item.get("category") or _category_for_file(source))
        destination = target / category / source.name
        destination_key = str(destination).lower()
        item["category"] = category
        item["category_source"] = "semantic_override" if override_category else str(
            item.get("category_source", "extension_rule")
        )
        item["destination"] = str(destination)
        item["destination_relative_path"] = _relative_display_path(root, destination)
        item["status"] = "planned"
        item.pop("reason", None)
        if destination.exists() and destination.resolve() != source.resolve():
            item["status"] = "blocked"
            item["reason"] = f"Destination already exists: {_relative_display_path(root, destination)}"
        elif destination_key in reserved_destinations:
            item["status"] = "blocked"
            item["reason"] = (
                "Destination would collide with "
                f"{_relative_display_path(root, reserved_destinations[destination_key])}"
            )
        else:
            reserved_destinations[destination_key] = source
        remapped.append(item)
    return remapped


def _build_plan(
    root: Path,
    *,
    relative_path: object = ".",
    recursive: bool = False,
    max_depth: object = None,
    category_overrides: dict[str, str] | None = None,
) -> list[dict[str, object]]:
    plan = _build_base_plan(
        root,
        relative_path=relative_path,
        recursive=recursive,
        max_depth=max_depth,
    )
    if category_overrides:
        return _apply_category_overrides_to_plan(
            root,
            relative_path=relative_path,
            plan=plan,
            category_overrides=category_overrides,
        )
    return plan


def _summarize_scope(
    root: Path,
    *,
    relative_path: object = ".",
    recursive: bool = False,
    max_depth: object = None,
) -> dict[str, object]:
    target = _resolve_relative_folder(root, relative_path)
    depth = _effective_plan_depth(recursive=recursive, max_depth=max_depth)
    files = _candidate_files(target, recursive=recursive, max_depth=depth)
    counts = Counter(_category_for_file(path) for path in files)
    return {
        "folder": str(root),
        "relative_path": _relative_display_path(root, target),
        "recursive": recursive,
        "max_depth": depth,
        "file_count": len(files),
        "categories": dict(sorted(counts.items())),
        "directories_scanned": sorted(
            {
                _relative_display_path(root, target),
                *[_relative_display_path(root, path.parent) for path in files],
            }
        ),
    }


def _plan_summary(plan: list[dict[str, object]]) -> str:
    if not plan:
        return "No files need organizing."
    planned_count = sum(1 for item in plan if item.get("status") == "planned")
    blocked_count = sum(1 for item in plan if item.get("status") == "blocked")
    moved_count = sum(1 for item in plan if item.get("status") == "moved")
    counts = Counter(str(item["category"]) for item in plan)
    summary = ", ".join(f"{category}: {count}" for category, count in sorted(counts.items()))
    if moved_count and not planned_count:
        headline = f"{moved_count} file(s) moved"
    elif planned_count:
        headline = f"{planned_count} file(s) ready to organize"
    else:
        headline = "No files are ready to organize"
    if blocked_count:
        headline += f". {blocked_count} blocked by conflicts"
    blocked_names = [
        str(item.get("source_relative_path", "") or Path(str(item.get("source", ""))).name)
        for item in plan
        if item.get("status") == "blocked"
    ]
    blocked_suffix = ""
    if blocked_names:
        preview = ", ".join(blocked_names[:3])
        remaining = len(blocked_names) - 3
        if remaining > 0:
            preview = f"{preview}, +{remaining} more"
        blocked_suffix = f" Blocked files: {preview}."
    return f"{headline}. {summary}.{blocked_suffix}"


def _execute_plan(plan: list[dict[str, object]]) -> list[dict[str, object]]:
    moved: list[dict[str, object]] = []
    for item in plan:
        next_item = dict(item)
        if next_item.get("status") != "planned":
            moved.append(next_item)
            continue
        source = Path(str(next_item["source"]))
        destination = Path(str(next_item["destination"]))
        if destination.exists() and destination.resolve() != source.resolve():
            next_item["status"] = "blocked"
            next_item["reason"] = f"Destination already exists: {destination}"
            moved.append(next_item)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        try:
            source.rename(destination)
        except OSError as error:
            next_item["status"] = "blocked"
            next_item["reason"] = f"Move failed: {error}"
            moved.append(next_item)
            continue
        next_item["status"] = "moved"
        moved.append(next_item)
    return moved


def _execute_plan_in_batches(plan: list[dict[str, object]]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    moved: list[dict[str, object]] = []
    batches: list[dict[str, object]] = []
    for index, (folder_relative_path, items) in enumerate(_group_plan_by_source_folder(plan), start=1):
        batch_result = _execute_plan(items)
        moved.extend(batch_result)
        batches.append(_summarize_plan_batch(folder_relative_path, batch_result, batch_index=index))
    return moved, batches


if app is not None:
    @app.call_tool()
    async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
        folder = _configured_folder()
        semantic_enabled = _configured_semantic_categorization()

        if name == "list_target_folder_contents":
            recursive = _coerce_bool(arguments.get("recursive", False))
            body = _list_folder_contents(
                folder,
                relative_path=arguments.get("relative_path", "."),
                recursive=recursive,
                max_depth=arguments.get("max_depth", _configured_organization_depth() if recursive else 1),
            )
            return [
                types.TextContent(
                    type="text",
                    text=_serialize_tool_payload(body, array_key="entries", item_label="entry"),
                )
            ]

        if name == "preview_folder_organization":
            relative_path = arguments.get("relative_path", ".")
            recursive = _coerce_bool(arguments.get("recursive", False))
            include_moves = _coerce_bool(arguments.get("include_moves", not recursive))
            batch_index = _coerce_batch_index(arguments.get("batch_index", None))
            category_overrides = _coerce_category_overrides(arguments.get("category_overrides", None))
            _validate_batch_selection(recursive=recursive, batch_index=batch_index)
            _validate_category_overrides(
                semantic_enabled=semantic_enabled,
                category_overrides=category_overrides,
            )
            max_depth = _effective_plan_depth(
                recursive=recursive,
                max_depth=arguments.get("max_depth", None),
            )
            plan = _build_plan(
                folder,
                relative_path=relative_path,
                recursive=recursive,
                max_depth=max_depth,
                category_overrides=category_overrides,
            )
            batches = _build_plan_batches(plan) if recursive else []
            body = _build_plan_tool_body(
                folder=folder,
                relative_path=relative_path,
                recursive=recursive,
                max_depth=max_depth,
                plan=plan,
                batches=batches,
                include_moves=include_moves,
                batch_index=batch_index,
            )
            body = _apply_categorization_metadata(
                body,
                plan=plan,
                semantic_enabled=semantic_enabled,
                category_overrides=category_overrides,
            )
            snapshot = _remember_plan_snapshot(
                folder=folder,
                relative_path=str(body["relative_path"]),
                recursive=recursive,
                max_depth=max_depth,
                plan=plan,
            )
            if snapshot is not None:
                body = _apply_plan_snapshot_metadata(body, snapshot)
            if batches:
                LOGGER.info(
                    "[FolderOrganizer] Preview batched by source folder: relative_path=%s batches=%s moves=%s detail_mode=%s token=%s semantic=%s overrides=%s",
                    body["relative_path"],
                    len(batches),
                    len(plan),
                    body["move_detail_mode"],
                    body.get("plan_token", "-"),
                    semantic_enabled,
                    len(category_overrides),
                )
            return [
                types.TextContent(
                    type="text",
                    text=_serialize_tool_payload(body, array_key="moves", item_label="move"),
                )
            ]

        if name == "organize_target_folder":
            dry_run = _coerce_bool(arguments.get("dry_run", False))
            relative_path = arguments.get("relative_path", ".")
            recursive = _coerce_bool(arguments.get("recursive", False))
            include_moves = _coerce_bool(arguments.get("include_moves", not recursive))
            batch_index = _coerce_batch_index(arguments.get("batch_index", None))
            plan_token = _coerce_plan_token(arguments.get("plan_token", None))
            category_overrides = _coerce_category_overrides(arguments.get("category_overrides", None))
            _validate_batch_selection(recursive=recursive, batch_index=batch_index, plan_token=plan_token)
            _validate_category_overrides(
                semantic_enabled=semantic_enabled,
                category_overrides=category_overrides,
            )
            if plan_token is not None:
                snapshot = _get_plan_snapshot(plan_token)
                if str(snapshot.get("folder", "")) != str(folder):
                    raise ValueError("plan_token does not belong to the current configured folder.")
                relative_path = snapshot.get("relative_path", ".")
                max_depth = int(snapshot.get("max_depth", _configured_organization_depth()))
                selected_plan, selected_batch, effective_batch_index = _select_snapshot_items(
                    snapshot,
                    batch_index=batch_index,
                )
                if selected_batch is None or effective_batch_index is None:
                    body = {
                        "folder": str(folder),
                        "relative_path": str(relative_path),
                        "recursive": recursive,
                        "max_depth": max_depth,
                        "dry_run": dry_run,
                        "summary": "No pending batches remain for this plan_token.",
                        "move_detail_mode": "summary-first",
                        "moves": [],
                    }
                    body = _apply_categorization_metadata(
                        body,
                        plan=[],
                        semantic_enabled=semantic_enabled,
                        category_overrides=category_overrides,
                    )
                    body = _apply_plan_snapshot_metadata(body, snapshot)
                    body["batch_execution_scope"] = "complete"
                    return [
                        types.TextContent(
                            type="text",
                            text=_serialize_tool_payload(body, array_key="moves", item_label="move"),
                        )
                    ]
                if category_overrides:
                    selected_plan = _apply_category_overrides_to_plan(
                        folder,
                        relative_path=relative_path,
                        plan=selected_plan,
                        category_overrides=category_overrides,
                    )
                    selected_batch = _summarize_plan_batch(
                        str(selected_batch["source_folder_relative_path"]),
                        selected_plan,
                        batch_index=effective_batch_index,
                    )
                if dry_run:
                    body = _build_plan_tool_body(
                        folder=folder,
                        relative_path=relative_path,
                        recursive=recursive,
                        max_depth=max_depth,
                        plan=selected_plan,
                        dry_run=True,
                        batches=[selected_batch],
                        include_moves=True,
                    )
                    body["selected_batch"] = selected_batch
                    body["move_detail_mode"] = "selected-batch"
                    body["detail_hint"] = DETAIL_HINT
                    body["batch_execution_scope"] = (
                        "plan-token-next-batch" if batch_index is None else "selected-batch"
                    )
                    body["requested_batch_index"] = effective_batch_index
                    body = _apply_categorization_metadata(
                        body,
                        plan=selected_plan,
                        semantic_enabled=semantic_enabled,
                        category_overrides=category_overrides,
                    )
                    body = _apply_plan_snapshot_metadata(body, snapshot)
                    return [
                        types.TextContent(
                            type="text",
                            text=_serialize_tool_payload(body, array_key="moves", item_label="move"),
                        )
                    ]
                moved = _execute_plan(selected_plan)
                executed_batch = _summarize_plan_batch(
                    str(selected_batch["source_folder_relative_path"]),
                    moved,
                    batch_index=effective_batch_index,
                )
                _mark_snapshot_batch_completed(snapshot, effective_batch_index)
                body = _build_plan_tool_body(
                    folder=folder,
                    relative_path=relative_path,
                    recursive=recursive,
                    max_depth=max_depth,
                    plan=moved,
                    dry_run=False,
                    batches=[executed_batch],
                    include_moves=True,
                )
                body["selected_batch"] = executed_batch
                body["move_detail_mode"] = "selected-batch"
                body["detail_hint"] = DETAIL_HINT
                body["batch_execution_scope"] = (
                    "plan-token-next-batch" if batch_index is None else "selected-batch"
                )
                body["requested_batch_index"] = effective_batch_index
                body = _apply_categorization_metadata(
                    body,
                    plan=moved,
                    semantic_enabled=semantic_enabled,
                    category_overrides=category_overrides,
                )
                body = _apply_plan_snapshot_metadata(body, snapshot)
                if body.get("plan_complete"):
                    _release_plan_snapshot(plan_token)
                    body["plan_token_released"] = True
                LOGGER.info(
                    "[FolderOrganizer] Execute snapshot batch: relative_path=%s batch=%s remaining=%s token=%s",
                    body["relative_path"],
                    effective_batch_index,
                    body.get("remaining_batch_count", 0),
                    plan_token,
                )
                return [
                    types.TextContent(
                        type="text",
                        text=_serialize_tool_payload(body, array_key="moves", item_label="move"),
                    )
                ]
            max_depth = _effective_plan_depth(
                recursive=recursive,
                max_depth=arguments.get("max_depth", None),
            )
            plan = _build_plan(
                folder,
                relative_path=relative_path,
                recursive=recursive,
                max_depth=max_depth,
                category_overrides=category_overrides,
            )
            batches = _build_plan_batches(plan) if recursive else []
            if dry_run:
                body = _build_plan_tool_body(
                    folder=folder,
                    relative_path=relative_path,
                    recursive=recursive,
                    max_depth=max_depth,
                    plan=plan,
                    dry_run=True,
                    batches=batches,
                    include_moves=include_moves,
                    batch_index=batch_index,
                )
                body = _apply_categorization_metadata(
                    body,
                    plan=plan,
                    semantic_enabled=semantic_enabled,
                    category_overrides=category_overrides,
                )
                if batches:
                    LOGGER.info(
                        "[FolderOrganizer] Dry-run batched by source folder: relative_path=%s batches=%s moves=%s detail_mode=%s semantic=%s overrides=%s",
                        body["relative_path"],
                        len(batches),
                        len(plan),
                        body["move_detail_mode"],
                        semantic_enabled,
                        len(category_overrides),
                    )
                return [
                    types.TextContent(
                        type="text",
                        text=_serialize_tool_payload(body, array_key="moves", item_label="move"),
                    )
                ]
            selected_plan, selected_batch = _select_plan_items(plan, batch_index=batch_index)
            if batches and batch_index is None:
                moved, executed_batches = _execute_plan_in_batches(plan)
            elif selected_batch is not None:
                moved = _execute_plan(selected_plan)
                executed_batches = [_summarize_plan_batch(
                    str(selected_batch["source_folder_relative_path"]),
                    moved,
                    batch_index=int(selected_batch["batch_index"]),
                )]
            else:
                moved = _execute_plan(selected_plan)
                executed_batches = []
            body = _build_plan_tool_body(
                folder=folder,
                relative_path=relative_path,
                recursive=recursive,
                max_depth=max_depth,
                plan=moved,
                dry_run=False,
                batches=executed_batches,
                include_moves=include_moves,
            )
            if selected_batch is not None:
                body["batch_execution_scope"] = "selected-batch"
                body["requested_batch_index"] = int(selected_batch["batch_index"])
                body["selected_batch"] = executed_batches[0]
                body["move_detail_mode"] = "selected-batch"
                body["detail_hint"] = DETAIL_HINT
            body = _apply_categorization_metadata(
                body,
                plan=moved,
                semantic_enabled=semantic_enabled,
                category_overrides=category_overrides,
            )
            if executed_batches:
                LOGGER.info(
                    "[FolderOrganizer] Execute batched by source folder: relative_path=%s batches=%s moves=%s detail_mode=%s semantic=%s overrides=%s",
                    body["relative_path"],
                    len(executed_batches),
                    len(moved),
                    body["move_detail_mode"],
                    semantic_enabled,
                    len(category_overrides),
                )
            return [
                types.TextContent(
                    type="text",
                    text=_serialize_tool_payload(body, array_key="moves", item_label="move"),
                )
            ]

        if name == "summarize_target_folder":
            body = _summarize_scope(
                folder,
                relative_path=arguments.get("relative_path", "."),
                recursive=_coerce_bool(arguments.get("recursive", False)),
                max_depth=arguments.get("max_depth", None),
            )
            return [types.TextContent(type="text", text=_serialize_tool_payload(body))]

        raise ValueError(f"Unknown tool: {name}")

    @app.list_tools()
    async def list_tools() -> list[types.Tool]:
        semantic_enabled = _configured_semantic_categorization()
        preview_properties = {
            "relative_path": {
                "type": "string",
                "description": "Optional path relative to the configured folder. Defaults to the folder root.",
            },
            "recursive": {
                "type": "boolean",
                "description": "When true, builds one consolidated plan for the selected subtree.",
            },
            "max_depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
                "description": "Optional override for how deep the scan may descend. Defaults to the configured traversal depth.",
            },
            "batch_index": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional batch number from the recursive preview summary when you want detailed moves for a single source subfolder.",
            },
            "include_moves": {
                "type": "boolean",
                "description": "When true, returns raw move rows. Recursive previews default to false so the response stays compact.",
            },
        }
        organize_properties = {
            "relative_path": {
                "type": "string",
                "description": "Optional path relative to the configured folder. Defaults to the folder root.",
            },
            "recursive": {
                "type": "boolean",
                "description": "When true, builds one consolidated plan for the selected subtree.",
            },
            "max_depth": {
                "type": "integer",
                "minimum": 1,
                "maximum": 8,
                "description": "Optional override for how deep the scan may descend. Defaults to the configured traversal depth.",
            },
            "plan_token": {
                "type": "string",
                "description": "Optional token returned by a recursive preview. Reuses that saved plan and, when batch_index is omitted, executes the next pending batch automatically.",
            },
            "dry_run": {
                "type": "boolean",
                "description": "When true, returns the move plan without applying it.",
            },
            "batch_index": {
                "type": "integer",
                "minimum": 1,
                "description": "Optional batch number from the recursive preview summary when you want to organize only one source subfolder per call.",
            },
            "include_moves": {
                "type": "boolean",
                "description": "When true, returns raw move rows. Recursive executions default to false so the response stays compact.",
            },
        }
        if semantic_enabled:
            override_schema = {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": "Optional mapping from source_relative_path to a custom destination folder name when semantic categorization is enabled.",
            }
            preview_properties["category_overrides"] = override_schema
            organize_properties["category_overrides"] = override_schema
        return [
            types.Tool(
                name="list_target_folder_contents",
                description="Lists files and subfolders inside the configured folder or one of its subfolders without changing anything.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "relative_path": {
                            "type": "string",
                            "description": "Optional path relative to the configured folder. Defaults to the folder root.",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "When true, includes nested subfolders in the listing.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 8,
                            "description": "Maximum depth to walk when recursive is true.",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="summarize_target_folder",
                description="Summarizes a folder inside the configured root, optionally across its subfolders.",
                inputSchema={
                    "type": "object",
                    "properties": {
                        "relative_path": {
                            "type": "string",
                            "description": "Optional path relative to the configured folder. Defaults to the folder root.",
                        },
                        "recursive": {
                            "type": "boolean",
                            "description": "When true, counts files across the selected folder and its subfolders.",
                        },
                        "max_depth": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 8,
                            "description": "Optional override for how deep the scan may descend. Defaults to the configured traversal depth.",
                        },
                    },
                    "required": [],
                },
            ),
            types.Tool(
                name="preview_folder_organization",
                description=(
                    "Shows the file move plan for a folder inside the configured root without changing anything. "
                    "Recursive previews default to batch summaries first and return a plan_token when progressive execution is available."
                    + (
                        " Semantic categorization is enabled for this skill instance; use category_overrides to preview custom folder names."
                        if semantic_enabled
                        else ""
                    )
                ),
                inputSchema={
                    "type": "object",
                    "properties": preview_properties,
                    "required": [],
                },
            ),
            types.Tool(
                name="organize_target_folder",
                description=(
                    "Moves files into category subfolders without deleting anything. Recursive runs return batch summaries first unless include_moves is explicitly requested, and plan_token can continue one pending batch at a time."
                    + (
                        " Semantic categorization is enabled for this skill instance; use category_overrides to apply personalized folder names."
                        if semantic_enabled
                        else ""
                    )
                ),
                inputSchema={
                    "type": "object",
                    "properties": organize_properties,
                    "required": [],
                },
            ),
        ]


async def main() -> None:
    if app is None or stdio_server is None:
        raise RuntimeError("The 'mcp' package is required to run the Folder Organizer MCP server.")
    folder = _configured_folder()
    print(f"[Info] Folder Organizer MCP started for {folder}", file=sys.stderr)
    async with stdio_server() as (read_stream, write_stream):
        await app.run(read_stream, write_stream, app.create_initialization_options())


if __name__ == "__main__":
    asyncio.run(main())
