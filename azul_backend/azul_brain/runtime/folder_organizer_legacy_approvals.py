"""Legacy Folder Organizer approval helpers.

New Folder Organizer runs should use the marketplace workflow HITL path. This
module exists so old chat approval cards and stored previews can still be read
or, when workflows are disabled, executed through the legacy compatibility path.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from .approval_protocol import parse_pending_action_block_fields

FOLDER_ORGANIZER_SKILL_ID = "dev.azulclaw.desktop-organizer"
FOLDER_ORGANIZER_TOOL_NAME = "organize_target_folder"


def folder_preview_expected_changes(preview_summary: str) -> bool:
    """Returns whether a legacy preview summary claimed executable moves."""

    marker = " file(s) ready to organize"
    text = str(preview_summary or "")
    index = text.casefold().find(marker)
    if index < 0:
        return False
    prefix = text[:index].rstrip()
    digits: list[str] = []
    for char in reversed(prefix):
        if char.isdigit():
            digits.append(char)
            continue
        if digits:
            break
    if not digits:
        return False
    try:
        return int("".join(reversed(digits))) > 0
    except ValueError:
        return False


def folder_summary_reports_no_ready_files(summary: str) -> bool:
    normalized = " ".join(str(summary or "").casefold().split())
    return (
        "no file(s) are ready to organize" in normalized
        or "no files are ready to organize" in normalized
    )


def normalize_override_path(raw: str) -> str:
    stripped = str(raw or "").strip().replace("\\", "/")
    if not stripped:
        raise ValueError("category_overrides keys must be non-empty relative source paths.")
    parts = [part for part in stripped.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("category_overrides keys must be relative source paths inside the configured folder.")
    return "/".join(parts)


def sanitize_override_name(raw: str) -> str:
    value = " ".join(str(raw or "").split()).strip(" .")
    if not value:
        raise ValueError("category_overrides values must resolve to a non-empty folder name.")
    return value


def validate_folder_organizer_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
    allowed = {
        "relative_path",
        "recursive",
        "max_depth",
        "plan_token",
        "dry_run",
        "batch_index",
        "include_moves",
        "category_overrides",
    }
    unknown = sorted(key for key in arguments if key not in allowed)
    if unknown:
        raise ValueError(f"Unsupported Folder Organizer argument(s): {', '.join(unknown)}")

    normalized: dict[str, Any] = {}
    if "relative_path" in arguments:
        relative_path = str(arguments.get("relative_path", "")).strip()
        if relative_path:
            normalized["relative_path"] = relative_path

    if "recursive" in arguments:
        normalized["recursive"] = bool(arguments["recursive"])
    if "include_moves" in arguments:
        normalized["include_moves"] = bool(arguments["include_moves"])
    if "dry_run" in arguments:
        if bool(arguments["dry_run"]):
            raise ValueError("Approved Folder Organizer executions cannot use dry_run=true.")
        normalized["dry_run"] = False

    if "max_depth" in arguments:
        max_depth = int(arguments["max_depth"])
        if max_depth < 1 or max_depth > 8:
            raise ValueError("max_depth must be an integer between 1 and 8.")
        normalized["max_depth"] = max_depth

    if "batch_index" in arguments:
        batch_index = int(arguments["batch_index"])
        if batch_index < 1:
            raise ValueError("batch_index must be an integer greater than or equal to 1.")
        normalized["batch_index"] = batch_index

    if "plan_token" in arguments:
        plan_token = str(arguments.get("plan_token", "")).strip()
        if not plan_token:
            raise ValueError("plan_token cannot be empty when provided.")
        normalized["plan_token"] = plan_token

    if "category_overrides" in arguments:
        raw_overrides = arguments["category_overrides"]
        if not isinstance(raw_overrides, dict):
            raise ValueError("category_overrides must be an object mapping source_relative_path to folder name.")
        normalized["category_overrides"] = {
            normalize_override_path(str(key)): sanitize_override_name(str(value))
            for key, value in raw_overrides.items()
        }

    if normalized.get("batch_index") is not None and not normalized.get("recursive", False):
        raise ValueError("batch_index requires recursive=true.")
    if normalized.get("plan_token") and not normalized.get("recursive", False):
        raise ValueError("plan_token requires recursive=true.")
    return normalized


def extract_folder_organizer_action(assistant_response: str) -> dict[str, Any] | None:
    fields = parse_pending_action_block_fields(assistant_response, "folder_organizer")
    if not fields:
        return None
    title = fields.get("Title", "").strip() or "Folder Organizer"
    summary = fields.get("Summary", "").strip() or "Approve applying the proposed folder organization changes."
    skill_id = fields.get("SkillId", "").strip() or FOLDER_ORGANIZER_SKILL_ID
    tool_name = fields.get("ToolName", "").strip() or FOLDER_ORGANIZER_TOOL_NAME
    raw_arguments = fields.get("ArgumentsJson", "").strip() or "{}"
    try:
        tool_arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(tool_arguments, dict):
        return None
    if skill_id != FOLDER_ORGANIZER_SKILL_ID or tool_name != FOLDER_ORGANIZER_TOOL_NAME:
        return None
    try:
        validated_arguments = validate_folder_organizer_arguments(tool_arguments)
    except ValueError:
        return None
    return {
        "action_kind": "folder_organizer",
        "title": title,
        "summary": summary,
        "skill_id": skill_id,
        "tool_name": tool_name,
        "tool_arguments": validated_arguments,
    }


def extract_folder_organizer_preview_request(assistant_response: str) -> dict[str, Any] | None:
    fields = parse_pending_action_block_fields(assistant_response, "folder_organizer")
    if not fields:
        return None
    skill_id = fields.get("SkillId", "").strip() or FOLDER_ORGANIZER_SKILL_ID
    tool_name = fields.get("ToolName", "").strip() or FOLDER_ORGANIZER_TOOL_NAME
    if skill_id != FOLDER_ORGANIZER_SKILL_ID or tool_name != FOLDER_ORGANIZER_TOOL_NAME:
        return None
    raw_arguments = fields.get("ArgumentsJson", "").strip() or "{}"
    try:
        tool_arguments = json.loads(raw_arguments)
    except json.JSONDecodeError:
        return None
    if not isinstance(tool_arguments, dict) or not bool(tool_arguments.get("dry_run")):
        return None
    preview_arguments = {key: value for key, value in tool_arguments.items() if key != "dry_run"}
    preview_arguments.setdefault("include_moves", not bool(preview_arguments.get("recursive", False)))
    try:
        return validate_folder_organizer_arguments(preview_arguments)
    except ValueError:
        return None


def derive_folder_organizer_action_from_preview(preview: Any) -> dict[str, Any] | None:
    if preview is None:
        return None

    tool_arguments: dict[str, Any] = {}
    if bool(getattr(preview, "recursive", False)):
        tool_arguments["recursive"] = True
    plan_token = str(getattr(preview, "plan_token", "") or "").strip()
    relative_path = str(getattr(preview, "relative_path", "") or "").strip()
    if plan_token:
        tool_arguments["plan_token"] = plan_token
    else:
        if relative_path not in {"", "."}:
            tool_arguments["relative_path"] = relative_path
        if bool(getattr(preview, "recursive", False)):
            tool_arguments["max_depth"] = getattr(preview, "max_depth", 1)
    batch_index = getattr(preview, "batch_index", None)
    if batch_index is not None:
        tool_arguments["batch_index"] = batch_index
    category_overrides = getattr(preview, "category_overrides", None)
    if category_overrides:
        tool_arguments["category_overrides"] = category_overrides
    try:
        validated_arguments = validate_folder_organizer_arguments(tool_arguments)
    except ValueError:
        return None

    if plan_token and batch_index is None:
        summary = "Approve applying the latest previewed folder organization plan across all pending batches."
    elif batch_index is not None:
        summary = f"Approve applying the latest previewed folder organization batch {batch_index}."
    else:
        summary = "Approve applying the latest previewed folder organization changes."
    preview_summary = str(getattr(preview, "summary", "") or "").strip()
    if preview_summary:
        summary = f"{summary} Preview: {preview_summary}"

    return {
        "action_kind": "folder_organizer",
        "title": "Folder Organizer",
        "summary": summary,
        "skill_id": FOLDER_ORGANIZER_SKILL_ID,
        "tool_name": FOLDER_ORGANIZER_TOOL_NAME,
        "tool_arguments": validated_arguments,
    }


def enrich_folder_organizer_action_with_snapshot(
    *,
    action: dict[str, Any],
    preview: Any,
    hash_plan_snapshot,
) -> dict[str, Any]:
    enriched = dict(action)
    if enriched.get("action_kind") != "folder_organizer":
        return enriched
    snapshot = {
        "skill_id": str(enriched.get("skill_id", "")).strip() or FOLDER_ORGANIZER_SKILL_ID,
        "tool_name": str(enriched.get("tool_name", "")).strip() or FOLDER_ORGANIZER_TOOL_NAME,
        "tool_arguments": enriched.get("tool_arguments", {})
        if isinstance(enriched.get("tool_arguments"), dict)
        else {},
    }
    if preview is not None:
        snapshot["preview"] = getattr(preview, "preview_payload", None) or {
            "relative_path": getattr(preview, "relative_path", ""),
            "recursive": getattr(preview, "recursive", False),
            "max_depth": getattr(preview, "max_depth", 1),
            "plan_token": getattr(preview, "plan_token", ""),
            "batch_index": getattr(preview, "batch_index", None),
            "summary": getattr(preview, "summary", ""),
            "category_overrides": getattr(preview, "category_overrides", {}) or {},
        }
    enriched["plan_snapshot"] = snapshot
    enriched["plan_hash"] = hash_plan_snapshot(snapshot)
    enriched["idempotency_key"] = (
        str(enriched.get("idempotency_key", "")).strip() or f"pending-sensitive-exec-{uuid4().hex}"
    )
    return enriched


def render_folder_organizer_metadata(action: Any) -> list[tuple[str, str]]:
    if getattr(action, "action_kind", "") != "folder_organizer":
        return []
    snapshot = getattr(action, "plan_snapshot", None) if isinstance(getattr(action, "plan_snapshot", None), dict) else {}
    preview = snapshot.get("preview") if isinstance(snapshot.get("preview"), dict) else {}
    tool_arguments = snapshot.get("tool_arguments") if isinstance(snapshot.get("tool_arguments"), dict) else {}

    metadata: list[tuple[str, str]] = []
    if preview:
        metadata.append(("ExecutionBinding", "Reviewed preview"))

    scope = str(preview.get("relative_path", tool_arguments.get("relative_path", "."))).strip() or "."
    metadata.append(("Scope", scope))

    batch_index = tool_arguments.get("batch_index", preview.get("batch_index"))
    remaining_batches = preview.get("remaining_batch_count", preview.get("batch_count"))
    try:
        remaining_batches_value = int(remaining_batches) if remaining_batches not in {None, ""} else None
    except (TypeError, ValueError):
        remaining_batches_value = None

    if batch_index not in {None, ""}:
        metadata.append(("Batches", f"Batch {int(batch_index)} only"))
    elif str(tool_arguments.get("plan_token", "")).strip():
        if remaining_batches_value and remaining_batches_value > 1:
            metadata.append(("Batches", f"Apply full reviewed plan - {remaining_batches_value} pending batches"))
        else:
            metadata.append(("Batches", "Apply full reviewed plan"))

    overrides = tool_arguments.get("category_overrides")
    if isinstance(overrides, dict) and overrides:
        metadata.append(("CustomCategories", f"{len(overrides)} semantic overrides"))

    plan_hash = str(getattr(action, "plan_hash", "") or "").strip()
    if plan_hash:
        metadata.append(("PlanHash", plan_hash[:12]))
    return metadata


def render_folder_organizer_review_details(action: Any) -> list[tuple[str, str]]:
    if getattr(action, "action_kind", "") != "folder_organizer":
        return []
    snapshot = getattr(action, "plan_snapshot", None) if isinstance(getattr(action, "plan_snapshot", None), dict) else {}
    preview = snapshot.get("preview") if isinstance(snapshot.get("preview"), dict) else {}
    tool_arguments = snapshot.get("tool_arguments") if isinstance(snapshot.get("tool_arguments"), dict) else {}
    if not preview:
        return []

    details: list[tuple[str, str]] = []
    preview_summary = str(preview.get("summary", "")).strip()
    if preview_summary:
        details.append(("PreviewSummary", preview_summary))

    if bool(preview.get("recursive", tool_arguments.get("recursive", False))):
        batch_count = preview.get("batch_count", preview.get("remaining_batch_count"))
        try:
            batch_count_value = int(batch_count) if batch_count not in {None, ""} else None
        except (TypeError, ValueError):
            batch_count_value = None
        mode = "Recursive preview"
        if batch_count_value and batch_count_value > 1:
            mode = f"Recursive preview - {batch_count_value} batches"
        details.append(("PreviewMode", mode))
    else:
        details.append(("PreviewMode", "Single-scope preview"))

    try:
        max_depth = int(preview.get("max_depth", tool_arguments.get("max_depth", 1) or 1))
    except (TypeError, ValueError):
        max_depth = 1
    details.append(("PreviewDepth", f"{max_depth} level{'s' if max_depth != 1 else ''}"))

    semantic_categories = preview.get("semantic_custom_categories")
    if isinstance(semantic_categories, list):
        labels = [str(item).strip() for item in semantic_categories if str(item).strip()]
        if labels:
            details.append(("PreviewCategories", ", ".join(labels[:6])))
    return details
