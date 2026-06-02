"""Shared lifecycle service for chat approvals."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

from .store import to_iso_z, utc_now

ApprovalStatus = Literal[
    "pending",
    "approved",
    "rejected",
    "superseded",
    "expired",
    "running",
    "completed",
    "failed",
]


@dataclass
class ApprovalRecord:
    """Global approval lifecycle record shared by all approval-producing features."""

    action_id: str
    user_id: str
    conversation_id: str
    source: str
    action_kind: str
    title: str
    summary: str
    status: ApprovalStatus
    created_at: str
    updated_at: str
    resolved_at: str = ""
    superseded_by: str = ""
    decision: str = ""
    idempotency_key: str = ""
    metadata: dict[str, Any] | None = None


def default_approval_lifecycle_path() -> Path:
    """Returns the shared runtime path for approval lifecycle persistence."""

    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser() / "runtime_approval_lifecycle.json"
    return Path(__file__).resolve().parents[3] / "memory" / "runtime_approval_lifecycle.json"


class ApprovalService:
    """Persists approval lifecycle state independently from feature-specific payloads."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> list[ApprovalRecord]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(raw, list):
            return []
        items: list[ApprovalRecord] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            action_id = str(item.get("action_id", "")).strip()
            user_id = str(item.get("user_id", "")).strip()
            if not action_id or not user_id:
                continue
            metadata = item.get("metadata")
            items.append(
                ApprovalRecord(
                    action_id=action_id,
                    user_id=user_id,
                    conversation_id=str(item.get("conversation_id", "")).strip(),
                    source=str(item.get("source", "")).strip() or "generic",
                    action_kind=str(item.get("action_kind", "")).strip() or "generic",
                    title=str(item.get("title", "")).strip(),
                    summary=str(item.get("summary", "")).strip(),
                    status=self._normalize_status(item.get("status")),
                    created_at=str(item.get("created_at", "")).strip(),
                    updated_at=str(item.get("updated_at", "")).strip(),
                    resolved_at=str(item.get("resolved_at", "")).strip(),
                    superseded_by=str(item.get("superseded_by", "")).strip(),
                    decision=str(item.get("decision", "")).strip(),
                    idempotency_key=str(item.get("idempotency_key", "")).strip(),
                    metadata=metadata if isinstance(metadata, dict) else None,
                )
            )
        return items

    def get_by_action_id(self, action_id: str) -> ApprovalRecord | None:
        safe_action_id = str(action_id).strip()
        return next((item for item in self.load() if item.action_id == safe_action_id), None)

    def get_active_for_user(self, user_id: str, *, source: str | None = None) -> ApprovalRecord | None:
        safe_user_id = str(user_id).strip()
        return next(
            (
                item
                for item in self.load()
                if item.user_id == safe_user_id
                and item.status == "pending"
                and (source is None or item.source == source)
            ),
            None,
        )

    def register_pending(
        self,
        *,
        action_id: str,
        user_id: str,
        conversation_id: str,
        source: str,
        action_kind: str,
        title: str,
        summary: str,
        idempotency_key: str = "",
        metadata: dict[str, Any] | None = None,
        supersede_existing: bool = False,
        supersede_scope: Literal["user", "conversation"] = "user",
    ) -> ApprovalRecord:
        current = self.load()
        safe_action_id = str(action_id).strip()
        now = to_iso_z(utc_now())
        if supersede_existing:
            for item in current:
                if (
                    item.user_id == str(user_id).strip()
                    and item.source == str(source).strip()
                    and item.status == "pending"
                    and item.action_id != safe_action_id
                    and (
                        supersede_scope != "conversation"
                        or item.conversation_id == str(conversation_id).strip()
                    )
                ):
                    item.status = "superseded"
                    item.updated_at = now
                    item.resolved_at = now
                    item.superseded_by = safe_action_id
        existing = next((item for item in current if item.action_id == safe_action_id), None)
        if existing is None:
            record = ApprovalRecord(
                action_id=safe_action_id,
                user_id=str(user_id).strip(),
                conversation_id=str(conversation_id).strip(),
                source=str(source).strip() or "generic",
                action_kind=str(action_kind).strip() or "generic",
                title=str(title).strip(),
                summary=str(summary).strip(),
                status="pending",
                created_at=now,
                updated_at=now,
                idempotency_key=str(idempotency_key).strip(),
                metadata=metadata if isinstance(metadata, dict) else None,
            )
            current.insert(0, record)
        else:
            existing.user_id = str(user_id).strip()
            existing.conversation_id = str(conversation_id).strip()
            existing.source = str(source).strip() or existing.source or "generic"
            existing.action_kind = str(action_kind).strip() or existing.action_kind or "generic"
            existing.title = str(title).strip()
            existing.summary = str(summary).strip()
            existing.status = "pending"
            existing.updated_at = now
            existing.resolved_at = ""
            existing.superseded_by = ""
            existing.decision = ""
            existing.idempotency_key = str(idempotency_key).strip()
            existing.metadata = metadata if isinstance(metadata, dict) else None
            record = existing
        self._save(current)
        return record

    def mark_superseded(self, action_id: str, *, superseded_by: str = "") -> ApprovalRecord | None:
        return self._transition(
            action_id,
            status="superseded",
            resolved=True,
            superseded_by=superseded_by,
        )

    def mark_expired(self, action_id: str) -> ApprovalRecord | None:
        return self._transition(action_id, status="expired", resolved=True)

    def mark_rejected(self, action_id: str) -> ApprovalRecord | None:
        return self._transition(action_id, status="rejected", resolved=True, decision="reject")

    def mark_approved(self, action_id: str) -> ApprovalRecord | None:
        return self._transition(action_id, status="approved", decision="approve")

    def mark_running(self, action_id: str) -> ApprovalRecord | None:
        return self._transition(action_id, status="running", decision="approve")

    def mark_completed(self, action_id: str) -> ApprovalRecord | None:
        return self._transition(action_id, status="completed", resolved=True, decision="approve")

    def mark_failed(self, action_id: str) -> ApprovalRecord | None:
        return self._transition(action_id, status="failed", resolved=True, decision="approve")

    def _transition(
        self,
        action_id: str,
        *,
        status: ApprovalStatus,
        resolved: bool = False,
        decision: str = "",
        superseded_by: str = "",
    ) -> ApprovalRecord | None:
        current = self.load()
        safe_action_id = str(action_id).strip()
        target = next((item for item in current if item.action_id == safe_action_id), None)
        if target is None:
            return None
        now = to_iso_z(utc_now())
        target.status = status
        target.updated_at = now
        if resolved:
            target.resolved_at = now
        if decision:
            target.decision = decision
        if superseded_by:
            target.superseded_by = str(superseded_by).strip()
        self._save(current)
        return target

    def _save(self, items: list[ApprovalRecord]) -> None:
        self.path.write_text(
            json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _normalize_status(self, value: Any) -> ApprovalStatus:
        candidate = str(value or "pending").strip().lower()
        allowed = {
            "pending",
            "approved",
            "rejected",
            "superseded",
            "expired",
            "running",
            "completed",
            "failed",
        }
        return candidate if candidate in allowed else "pending"
