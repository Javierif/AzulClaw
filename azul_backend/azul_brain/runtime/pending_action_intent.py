"""Structured pending approvals for non-heartbeat sensitive actions."""

from __future__ import annotations

import contextlib
import contextvars
import json
import os
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from ..api.hatching_store import HatchingStore
from .approval_protocol import (
    contains_pending_action_block,
    render_approval_block,
    strip_pending_action_block,
)
from .approval_service import ApprovalService, default_approval_lifecycle_path
from .json_pending_store import JsonPendingStore
from .store import to_iso_z, utc_now

FOLDER_ORGANIZER_SKILL_ID = "dev.azulclaw.desktop-organizer"

PENDING_SENSITIVE_ACTION_TTL_SECONDS = 10 * 60
PENDING_SENSITIVE_ACTION_CARD_ONLY_CONFIRMATION = (
    "For security, approve or cancel this action using the confirmation card in chat. "
    "Typed yes/no replies are not accepted."
)
PENDING_SENSITIVE_ACTION_APPROVAL_PROMPT = (
    "Execute the previously approved sensitive action now. "
    "Do not ask for confirmation again. "
    "Use the relevant tool immediately and report the concrete result."
)
PENDING_SENSITIVE_ACTION_UNRESOLVED_FILESYSTEM_CONFIRMATION = (
    "I didn't receive a precise executable Folder Organizer plan for that approval. "
    "For safety, I did not move any files. Please rerun the preview and approve the generated action card."
)
def _runtime_root() -> Path:
    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "memory"


def _default_pending_actions_path() -> Path:
    return _runtime_root() / "runtime_pending_sensitive_actions.json"


def _default_preview_actions_path() -> Path:
    return _runtime_root() / "runtime_folder_organizer_previews.json"


def _default_execution_receipts_path() -> Path:
    return _runtime_root() / "runtime_pending_sensitive_action_receipts.json"


def _default_approval_lifecycle_path() -> Path:
    return default_approval_lifecycle_path()



def _stable_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _hash_plan_snapshot(snapshot: dict[str, Any]) -> str:
    return sha256(_stable_json(snapshot).encode("utf-8")).hexdigest()


def _normalize_override_path(raw: str) -> str:
    stripped = str(raw or "").strip().replace("\\", "/")
    if not stripped:
        raise ValueError("category_overrides keys must be non-empty relative source paths.")
    parts = [part for part in stripped.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ValueError("category_overrides keys must be relative source paths inside the configured folder.")
    return "/".join(parts)


def _sanitize_override_name(raw: str) -> str:
    value = " ".join(str(raw or "").split()).strip(" .")
    if not value:
        raise ValueError("category_overrides values must resolve to a non-empty folder name.")
    return value


def _validate_folder_organizer_arguments(arguments: dict[str, Any]) -> dict[str, Any]:
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
            _normalize_override_path(str(key)): _sanitize_override_name(str(value))
            for key, value in raw_overrides.items()
        }

    if normalized.get("batch_index") is not None and not normalized.get("recursive", False):
        raise ValueError("batch_index requires recursive=true.")
    if normalized.get("plan_token") and not normalized.get("recursive", False):
        raise ValueError("plan_token requires recursive=true.")
    return normalized


_TOOL_CAPTURE_CONTEXT: contextvars.ContextVar[tuple[str, str] | None] = contextvars.ContextVar(
    "pending_sensitive_tool_capture_context",
    default=None,
)


@dataclass
class PendingSensitiveAction:
    """Pending non-heartbeat action awaiting approval."""

    id: str
    user_id: str
    conversation_id: str
    title: str
    summary: str
    source_user_message: str
    created_at: str
    action_kind: str = "generic"
    skill_id: str = ""
    tool_name: str = ""
    tool_arguments: dict[str, Any] | None = None
    plan_snapshot: dict[str, Any] | None = None
    plan_hash: str = ""
    idempotency_key: str = ""
    revision_label: str = ""


@dataclass
class FolderOrganizerPreviewRecord:
    """Most recent safe Folder Organizer preview for one chat context."""

    user_id: str
    conversation_id: str
    created_at: str
    relative_path: str = "."
    recursive: bool = False
    max_depth: int = 1
    plan_token: str = ""
    batch_index: int | None = None
    category_overrides: dict[str, str] | None = None
    summary: str = ""
    preview_payload: dict[str, Any] | None = None


@dataclass
class PendingSensitiveExecutionReceipt:
    """Resolution receipt used to make approved executions idempotent."""

    action_id: str
    user_id: str
    decision: Literal["approve", "reject"]
    created_at: str
    idempotency_key: str = ""
    plan_hash: str = ""
    status: str = "completed"
    response: str = ""


@dataclass
class PendingSensitiveDecision:
    """Structured resolution for a pending sensitive action."""

    kind: Literal["approve", "reject", "card-only"]
    response: str
    action: PendingSensitiveAction | None = None


class PendingSensitiveActionStore(JsonPendingStore[PendingSensitiveAction]):
    """Small JSON store for chat approval cards tied to sensitive actions."""

    def __init__(
        self,
        path: Path | None = None,
        ttl_seconds: int = PENDING_SENSITIVE_ACTION_TTL_SECONDS,
        approval_service: ApprovalService | None = None,
    ) -> None:
        super().__init__(path or _default_pending_actions_path(), ttl_seconds)
        lifecycle_path = (
            self.path.parent / "approval-lifecycle.json"
            if path is not None
            else _default_approval_lifecycle_path()
        )
        self.approval_service = approval_service or ApprovalService(lifecycle_path)

    def _deserialize(self, item: dict[str, Any]) -> PendingSensitiveAction | None:
        action_id = str(item.get("id", "")).strip()
        user_id = str(item.get("user_id", "")).strip()
        title = str(item.get("title", "")).strip()
        summary = str(item.get("summary", "")).strip()
        source_user_message = str(item.get("source_user_message", "")).strip()
        if not action_id or not user_id or not title or not summary:
            return None
        return PendingSensitiveAction(
            id=action_id,
            user_id=user_id,
            conversation_id=str(item.get("conversation_id", "")).strip(),
            title=title,
            summary=summary,
            source_user_message=source_user_message,
            action_kind=str(item.get("action_kind", "generic")).strip() or "generic",
            skill_id=str(item.get("skill_id", "")).strip(),
            tool_name=str(item.get("tool_name", "")).strip(),
            tool_arguments=item.get("tool_arguments")
            if isinstance(item.get("tool_arguments"), dict)
            else None,
            plan_snapshot=item.get("plan_snapshot")
            if isinstance(item.get("plan_snapshot"), dict)
            else None,
            plan_hash=str(item.get("plan_hash", "")).strip(),
            idempotency_key=str(item.get("idempotency_key", "")).strip(),
            revision_label=str(item.get("revision_label", "")).strip(),
            created_at=str(item.get("created_at", "")).strip(),
        )

    def _on_prune_expired(self, item: PendingSensitiveAction) -> None:
        self.approval_service.mark_expired(item.id)

    def get_for_user(self, user_id: str) -> PendingSensitiveAction | None:
        safe_user_id = str(user_id).strip()
        return next((item for item in self.load() if item.user_id == safe_user_id), None)

    def get_for_context(self, user_id: str, conversation_id: str | None) -> PendingSensitiveAction | None:
        safe_user_id = str(user_id).strip()
        safe_conversation_id = str(conversation_id or "").strip()
        items = self.load()
        if safe_conversation_id:
            return next(
                (
                    item
                    for item in items
                    if item.user_id == safe_user_id and item.conversation_id == safe_conversation_id
                ),
                None,
            )
        return next((item for item in items if item.user_id == safe_user_id), None)

    def get_by_action_id(self, user_id: str, action_id: str) -> PendingSensitiveAction | None:
        safe_user_id = str(user_id).strip()
        safe_action_id = str(action_id).strip()
        return next(
            (
                item
                for item in self.load()
                if item.user_id == safe_user_id and item.id == safe_action_id
            ),
            None,
        )

    def save(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        title: str,
        summary: str,
        source_user_message: str,
        action_kind: str = "generic",
        skill_id: str = "",
        tool_name: str = "",
        tool_arguments: dict[str, Any] | None = None,
        plan_snapshot: dict[str, Any] | None = None,
        plan_hash: str = "",
        idempotency_key: str = "",
        revision_label: str = "",
    ) -> PendingSensitiveAction:
        action = PendingSensitiveAction(
            id=f"pending-sensitive-action-{uuid4().hex[:12]}",
            user_id=str(user_id).strip(),
            conversation_id=str(conversation_id or "").strip(),
            title=title.strip(),
            summary=summary.strip(),
            source_user_message=(source_user_message or "").strip(),
            action_kind=str(action_kind or "generic").strip() or "generic",
            skill_id=str(skill_id or "").strip(),
            tool_name=str(tool_name or "").strip(),
            tool_arguments=tool_arguments if isinstance(tool_arguments, dict) else None,
            plan_snapshot=plan_snapshot if isinstance(plan_snapshot, dict) else None,
            plan_hash=str(plan_hash or "").strip(),
            idempotency_key=str(idempotency_key or "").strip() or f"pending-sensitive-exec-{uuid4().hex}",
            revision_label=str(revision_label or "").strip(),
            created_at=to_iso_z(utc_now()),
        )
        items = [
            item
            for item in self.load()
            if not (
                item.user_id == action.user_id
                and item.conversation_id == action.conversation_id
            )
        ]
        items.insert(0, action)
        self._save(items)
        self.approval_service.register_pending(
            action_id=action.id,
            user_id=action.user_id,
            conversation_id=action.conversation_id,
            source="sensitive_action",
            action_kind=action.action_kind,
            title=action.title,
            summary=action.summary,
            idempotency_key=action.idempotency_key,
            metadata={
                "skill_id": action.skill_id,
                "tool_name": action.tool_name,
                "tool_arguments": action.tool_arguments or {},
                "plan_hash": action.plan_hash,
                "revision_label": action.revision_label,
            },
            supersede_existing=True,
            supersede_scope="conversation",
        )
        return action

    def pop_for_user(self, user_id: str, *, status: str = "completed") -> PendingSensitiveAction | None:
        safe_user_id = str(user_id).strip()
        current = self.load()
        action = next((item for item in current if item.user_id == safe_user_id), None)
        if action is None:
            return None
        self._save([item for item in current if item.user_id != safe_user_id])
        if status == "rejected":
            self.approval_service.mark_rejected(action.id)
        elif status == "superseded":
            self.approval_service.mark_superseded(action.id)
        elif status == "expired":
            self.approval_service.mark_expired(action.id)
        elif status == "running":
            self.approval_service.mark_running(action.id)
        elif status == "failed":
            self.approval_service.mark_failed(action.id)
        else:
            self.approval_service.mark_completed(action.id)
        return action

    def pop_by_action_id(self, user_id: str, action_id: str, *, status: str = "completed") -> PendingSensitiveAction | None:
        safe_user_id = str(user_id).strip()
        safe_action_id = str(action_id).strip()
        current = self.load()
        action = next(
            (
                item
                for item in current
                if item.user_id == safe_user_id and item.id == safe_action_id
            ),
            None,
        )
        if action is None:
            return None
        self._save([item for item in current if item.id != safe_action_id])
        if status == "rejected":
            self.approval_service.mark_rejected(action.id)
        elif status == "superseded":
            self.approval_service.mark_superseded(action.id)
        elif status == "expired":
            self.approval_service.mark_expired(action.id)
        elif status == "running":
            self.approval_service.mark_running(action.id)
        elif status == "failed":
            self.approval_service.mark_failed(action.id)
        else:
            self.approval_service.mark_completed(action.id)
        return action


class FolderOrganizerPreviewStore(JsonPendingStore[FolderOrganizerPreviewRecord]):
    """Persists the latest preview per user and conversation."""

    def __init__(
        self,
        path: Path | None = None,
        ttl_seconds: int = PENDING_SENSITIVE_ACTION_TTL_SECONDS,
    ) -> None:
        super().__init__(path or _default_preview_actions_path(), ttl_seconds)

    def _deserialize(self, item: dict[str, Any]) -> FolderOrganizerPreviewRecord | None:
        user_id = str(item.get("user_id", "")).strip()
        conversation_id = str(item.get("conversation_id", "")).strip()
        if not user_id or not conversation_id:
            return None
        overrides = item.get("category_overrides")
        return FolderOrganizerPreviewRecord(
            user_id=user_id,
            conversation_id=conversation_id,
            created_at=str(item.get("created_at", "")).strip(),
            relative_path=str(item.get("relative_path", ".")).strip() or ".",
            recursive=bool(item.get("recursive", False)),
            max_depth=max(1, int(item.get("max_depth", 1) or 1)),
            plan_token=str(item.get("plan_token", "")).strip(),
            batch_index=(
                int(item["batch_index"])
                if item.get("batch_index") not in {None, ""}
                else None
            ),
            category_overrides=overrides if isinstance(overrides, dict) else None,
            summary=str(item.get("summary", "")).strip(),
            preview_payload=item.get("preview_payload")
            if isinstance(item.get("preview_payload"), dict)
            else None,
        )

    def save(
        self,
        *,
        user_id: str,
        conversation_id: str,
        relative_path: str = ".",
        recursive: bool = False,
        max_depth: int = 1,
        plan_token: str = "",
        batch_index: int | None = None,
        category_overrides: dict[str, str] | None = None,
        summary: str = "",
        preview_payload: dict[str, Any] | None = None,
    ) -> FolderOrganizerPreviewRecord:
        record = FolderOrganizerPreviewRecord(
            user_id=str(user_id).strip(),
            conversation_id=str(conversation_id).strip(),
            created_at=to_iso_z(utc_now()),
            relative_path=str(relative_path or ".").strip() or ".",
            recursive=bool(recursive),
            max_depth=max(1, int(max_depth or 1)),
            plan_token=str(plan_token or "").strip(),
            batch_index=int(batch_index) if batch_index not in {None, ""} else None,
            category_overrides=dict(category_overrides or {}) or None,
            summary=str(summary or "").strip(),
            preview_payload=preview_payload if isinstance(preview_payload, dict) else None,
        )
        items = [
            item
            for item in self.load()
            if not (item.user_id == record.user_id and item.conversation_id == record.conversation_id)
        ]
        items.insert(0, record)
        self._save(items)
        return record

    def get_for_context(self, user_id: str, conversation_id: str | None) -> FolderOrganizerPreviewRecord | None:
        safe_user_id = str(user_id).strip()
        safe_conversation_id = str(conversation_id or "").strip()
        if not safe_user_id or not safe_conversation_id:
            return None
        return next(
            (
                item
                for item in self.load()
                if item.user_id == safe_user_id and item.conversation_id == safe_conversation_id
            ),
            None,
        )


class PendingSensitiveExecutionReceiptStore(JsonPendingStore[PendingSensitiveExecutionReceipt]):
    """Caches pending-action resolutions to make approvals idempotent."""

    def __init__(
        self,
        path: Path | None = None,
        ttl_seconds: int = PENDING_SENSITIVE_ACTION_TTL_SECONDS,
    ) -> None:
        super().__init__(path or _default_execution_receipts_path(), ttl_seconds)

    def _deserialize(self, item: dict[str, Any]) -> PendingSensitiveExecutionReceipt | None:
        action_id = str(item.get("action_id", "")).strip()
        user_id = str(item.get("user_id", "")).strip()
        decision = str(item.get("decision", "")).strip()
        if not action_id or not user_id or decision not in {"approve", "reject"}:
            return None
        return PendingSensitiveExecutionReceipt(
            action_id=action_id,
            user_id=user_id,
            decision=decision,
            created_at=str(item.get("created_at", "")).strip(),
            idempotency_key=str(item.get("idempotency_key", "")).strip(),
            plan_hash=str(item.get("plan_hash", "")).strip(),
            status=str(item.get("status", "completed")).strip() or "completed",
            response=str(item.get("response", "")).strip(),
        )

    def get_by_action_id(
        self,
        *,
        user_id: str,
        action_id: str,
        decision: str,
    ) -> PendingSensitiveExecutionReceipt | None:
        safe_user_id = str(user_id).strip()
        safe_action_id = str(action_id).strip()
        safe_decision = str(decision).strip()
        return next(
            (
                item
                for item in self.load()
                if item.user_id == safe_user_id
                and item.action_id == safe_action_id
                and item.decision == safe_decision
            ),
            None,
        )

    def get_by_idempotency_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> PendingSensitiveExecutionReceipt | None:
        safe_user_id = str(user_id).strip()
        safe_key = str(idempotency_key).strip()
        if not safe_user_id or not safe_key:
            return None
        return next(
            (
                item
                for item in self.load()
                if item.user_id == safe_user_id and item.idempotency_key == safe_key
            ),
            None,
        )

    def save(self, receipt: PendingSensitiveExecutionReceipt) -> PendingSensitiveExecutionReceipt:
        items = [
            item
            for item in self.load()
            if not (
                item.user_id == receipt.user_id
                and (
                    item.action_id == receipt.action_id
                    or (receipt.idempotency_key and item.idempotency_key == receipt.idempotency_key)
                )
            )
        ]
        items.insert(0, receipt)
        self._save(items)
        return receipt


@contextlib.contextmanager
def pending_sensitive_action_capture_context(user_id: str, conversation_id: str | None):
    """Associates downstream tool calls with the active chat turn."""

    safe_user_id = str(user_id).strip()
    safe_conversation_id = str(conversation_id or "").strip()
    if not safe_user_id or not safe_conversation_id:
        yield
        return
    token = _TOOL_CAPTURE_CONTEXT.set((safe_user_id, safe_conversation_id))
    try:
        yield
    finally:
        _TOOL_CAPTURE_CONTEXT.reset(token)


def maybe_record_folder_organizer_preview(
    *,
    tool_name: str,
    arguments: dict[str, Any],
    result: Any,
    preview_store: FolderOrganizerPreviewStore | None = None,
) -> None:
    """Captures preview results so approvals can execute the exact reviewed plan."""

    if str(tool_name).strip() != "preview_folder_organization":
        return
    context = _TOOL_CAPTURE_CONTEXT.get()
    if context is None:
        return
    user_id, conversation_id = context
    content = getattr(result, "content", None)
    if not content:
        return
    first = content[0]
    raw_text = getattr(first, "text", None)
    if not isinstance(raw_text, str) or not raw_text.strip().startswith("{"):
        return
    try:
        payload = json.loads(raw_text)
    except json.JSONDecodeError:
        return
    if not isinstance(payload, dict):
        return

    preview_arguments: dict[str, Any] = {}
    if bool(payload.get("recursive", arguments.get("recursive", False))):
        preview_arguments["recursive"] = True
    relative_path = str(payload.get("relative_path", arguments.get("relative_path", "."))).strip() or "."
    if relative_path not in {"", "."}:
        preview_arguments["relative_path"] = relative_path
    max_depth = int(payload.get("max_depth", arguments.get("max_depth", 1) or 1))
    if preview_arguments.get("recursive"):
        preview_arguments["max_depth"] = max_depth
    batch_index = arguments.get("batch_index", payload.get("requested_batch_index"))
    if batch_index not in {None, ""}:
        preview_arguments["batch_index"] = batch_index
    raw_overrides = arguments.get("category_overrides", {})
    if isinstance(raw_overrides, dict) and raw_overrides:
        preview_arguments["category_overrides"] = raw_overrides
    try:
        validated_arguments = _validate_folder_organizer_arguments(preview_arguments)
    except ValueError:
        return

    store = preview_store or FolderOrganizerPreviewStore()
    store.save(
        user_id=user_id,
        conversation_id=conversation_id,
        relative_path=relative_path,
        recursive=bool(validated_arguments.get("recursive", False)),
        max_depth=int(validated_arguments.get("max_depth", max_depth)),
        plan_token=str(payload.get("plan_token", "")).strip(),
        batch_index=validated_arguments.get("batch_index"),
        category_overrides=validated_arguments.get("category_overrides"),
        summary=str(payload.get("summary", "")).strip(),
        preview_payload=payload,
    )


class PendingSensitiveActionService:
    """Creates and resolves secure chat approval cards for sensitive actions."""

    def __init__(
        self,
        pending_store: PendingSensitiveActionStore | None = None,
        preview_store: FolderOrganizerPreviewStore | None = None,
        receipt_store: PendingSensitiveExecutionReceiptStore | None = None,
    ) -> None:
        self.pending_store = pending_store or PendingSensitiveActionStore()
        self.preview_store = preview_store or FolderOrganizerPreviewStore()
        self.receipt_store = receipt_store or PendingSensitiveExecutionReceiptStore()

    def maybe_stage_action(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
        assistant_response: str,
        semantic_action_kind: str = "",
        semantic_title: str = "",
        semantic_summary: str = "",
        revision_label: str = "",
    ) -> str:
        if not self._requires_confirmation():
            return assistant_response
        structured = self._extract_structured_action(user_message, assistant_response)
        if structured is None:
            normalized_action_kind = str(semantic_action_kind or "").strip()
            if normalized_action_kind == "generic":
                structured = {
                    "action_kind": "generic",
                    "title": str(semantic_title or "").strip() or "Sensitive action",
                    "summary": (
                        str(semantic_summary or "").strip()
                        or "Approve executing the proposed sensitive action."
                    ),
                    "skill_id": "",
                    "tool_name": "",
                    "tool_arguments": {},
                }
        if structured is not None:
            structured = self._enrich_action_with_snapshot(
                user_id=user_id,
                conversation_id=conversation_id,
                action=structured,
            )
            action = self.pending_store.save(
                user_id=user_id,
                conversation_id=conversation_id,
                title=structured["title"],
                summary=structured["summary"],
                source_user_message=user_message,
                action_kind=structured["action_kind"],
                skill_id=structured["skill_id"],
                tool_name=structured["tool_name"],
                tool_arguments=structured["tool_arguments"],
                plan_snapshot=structured.get("plan_snapshot"),
                plan_hash=structured.get("plan_hash", ""),
                idempotency_key=structured.get("idempotency_key", ""),
                revision_label=revision_label,
            )
            base_text = strip_pending_action_block(assistant_response)
            block = self._render_block(action)
            if base_text:
                return f"{base_text}\n\n{block}"
            return block
        return assistant_response

    def get_pending_action_for_context(
        self,
        user_id: str,
        conversation_id: str | None,
    ) -> PendingSensitiveAction | None:
        return self.pending_store.get_for_context(user_id, conversation_id)

    def discard_pending_action_for_context(
        self,
        user_id: str,
        conversation_id: str | None,
    ) -> PendingSensitiveAction | None:
        pending = self.get_pending_action_for_context(user_id, conversation_id)
        if pending is None:
            return None
        self.pending_store.pop_by_action_id(user_id, pending.id, status="superseded")
        return pending

    def handle_pending_decision(
        self,
        user_id: str,
        action_id: str,
        decision: Literal["approve", "reject"],
    ) -> PendingSensitiveDecision | None:
        pending = self.pending_store.get_by_action_id(user_id, action_id)
        safe_action_id = str(action_id).strip()
        if pending is None or pending.id != safe_action_id:
            receipt = self.receipt_store.get_by_action_id(
                user_id=user_id,
                action_id=safe_action_id,
                decision=decision,
            )
            if receipt is not None:
                if receipt.status == "running":
                    response = "That approved action is already running."
                else:
                    response = receipt.response or "That pending action was already processed."
                return PendingSensitiveDecision(
                    kind=decision,
                    response=response,
                    action=None,
                )
            record = self.pending_store.approval_service.get_by_action_id(safe_action_id)
            if record is None or record.user_id != str(user_id).strip():
                return None
            if record.source != "sensitive_action":
                return None
            if record.status == "pending":
                if pending is not None and pending.id != safe_action_id:
                    self.pending_store.approval_service.mark_superseded(
                        safe_action_id,
                        superseded_by=pending.id,
                    )
                    response = "That approval was replaced by a newer reviewed plan. Please use the latest approval card."
                else:
                    self.pending_store.approval_service.mark_expired(safe_action_id)
                    response = "That approval is no longer available. Please regenerate the reviewed plan before applying changes."
            elif record.status == "superseded":
                response = "That approval was replaced by a newer reviewed plan. Please use the latest approval card."
            elif record.status == "running":
                response = "That approved action is already running."
            elif record.status in {"completed", "approved"}:
                response = "That pending action was already processed."
            elif record.status == "rejected":
                response = "That approval was already canceled."
            elif record.status == "failed":
                response = "That approval was accepted earlier, but the execution failed."
            else:
                response = "That approval is no longer active."
            return PendingSensitiveDecision(
                kind=decision,
                response=response,
                action=None,
            )
        if decision == "reject":
            self.pending_store.pop_by_action_id(user_id, pending.id, status="rejected")
            response = "I cancelled the pending sensitive action."
            self.receipt_store.save(
                PendingSensitiveExecutionReceipt(
                    action_id=pending.id,
                    user_id=pending.user_id,
                    decision="reject",
                    created_at=to_iso_z(utc_now()),
                    idempotency_key=pending.idempotency_key,
                    plan_hash=pending.plan_hash,
                    status="completed",
                    response=response,
                )
            )
            return PendingSensitiveDecision(
                kind="reject",
                response=response,
                action=pending,
            )
        if pending.action_kind == "folder_organizer_unresolved":
            self.pending_store.pop_by_action_id(user_id, pending.id, status="completed")
            self.receipt_store.save(
                PendingSensitiveExecutionReceipt(
                    action_id=pending.id,
                    user_id=pending.user_id,
                    decision="approve",
                    created_at=to_iso_z(utc_now()),
                    idempotency_key=pending.idempotency_key,
                    plan_hash=pending.plan_hash,
                    status="completed",
                    response=PENDING_SENSITIVE_ACTION_UNRESOLVED_FILESYSTEM_CONFIRMATION,
                )
            )
            return PendingSensitiveDecision(
                kind="approve",
                response=PENDING_SENSITIVE_ACTION_UNRESOLVED_FILESYSTEM_CONFIRMATION,
                action=None,
            )
        self.pending_store.pop_by_action_id(user_id, pending.id, status="running")
        self.receipt_store.save(
            PendingSensitiveExecutionReceipt(
                action_id=pending.id,
                user_id=pending.user_id,
                decision="approve",
                created_at=to_iso_z(utc_now()),
                idempotency_key=pending.idempotency_key,
                plan_hash=pending.plan_hash,
                status="running",
                response="",
            )
        )
        return PendingSensitiveDecision(
            kind="approve",
            response="",
            action=pending,
        )

    def get_execution_receipt_by_key(
        self,
        *,
        user_id: str,
        idempotency_key: str,
    ) -> PendingSensitiveExecutionReceipt | None:
        return self.receipt_store.get_by_idempotency_key(
            user_id=user_id,
            idempotency_key=idempotency_key,
        )

    def mark_execution_completed(self, action: PendingSensitiveAction, response: str) -> None:
        self.pending_store.approval_service.mark_completed(action.id)
        self.receipt_store.save(
            PendingSensitiveExecutionReceipt(
                action_id=action.id,
                user_id=action.user_id,
                decision="approve",
                created_at=to_iso_z(utc_now()),
                idempotency_key=action.idempotency_key,
                plan_hash=action.plan_hash,
                status="completed",
                response=str(response or "").strip(),
            )
        )

    def mark_execution_failed(self, action: PendingSensitiveAction, response: str) -> None:
        self.pending_store.approval_service.mark_failed(action.id)
        self.receipt_store.save(
            PendingSensitiveExecutionReceipt(
                action_id=action.id,
                user_id=action.user_id,
                decision="approve",
                created_at=to_iso_z(utc_now()),
                idempotency_key=action.idempotency_key,
                plan_hash=action.plan_hash,
                status="failed",
                response=str(response or "").strip(),
            )
        )

    def _requires_confirmation(self) -> bool:
        try:
            return bool(HatchingStore().load().confirm_sensitive_actions)
        except Exception:
            return True

    def _already_structured(self, assistant_response: str) -> bool:
        return contains_pending_action_block(assistant_response)

    def _extract_structured_action(
        self,
        user_message: str,
        assistant_response: str,
    ) -> dict[str, Any] | None:
        return None

    def _enrich_action_with_snapshot(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        action: dict[str, Any],
    ) -> dict[str, Any]:
        return action

    def _render_block(self, action: PendingSensitiveAction) -> str:
        extra_fields: dict[str, str] = {}
        if action.revision_label:
            extra_fields["RevisionLabel"] = action.revision_label
        return render_approval_block(
            action_id=action.id,
            action_kind=action.action_kind or "sensitive_action",
            title=action.title,
            summary=action.summary,
            approve_label="Apply changes",
            reject_label="Cancel",
            extra_fields=extra_fields,
        )
