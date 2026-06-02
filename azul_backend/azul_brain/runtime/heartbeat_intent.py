"""Natural-language heartbeat creation flow for chat turns."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field, field_validator

from ..api.hatching_store import HatchingStore
from .approval_protocol import render_approval_block
from .approval_service import ApprovalService, default_approval_lifecycle_path
from .semantic_judge import SemanticJudgeService
from .store import RuntimeStore, ScheduledJob, parse_iso_datetime, to_iso_z, utc_now


PENDING_HEARTBEAT_TTL_SECONDS = 10 * 60
FREQUENCY_CLARIFICATION = (
    "I could not process the frequency. Could you confirm how often you want me "
    "to run this task? For example: 'every 2 hours'."
)
HEARTBEAT_CREATION_ERROR = (
    "I could not create the heartbeat because the schedule is invalid. "
    "Could you confirm how often you want me to run this task? "
    "For example: 'every 2 hours'."
)
HEARTBEAT_CARD_ONLY_CONFIRMATION = (
    "For security, approve or cancel this heartbeat using the confirmation card in chat. "
    "Typed yes/no replies are not accepted."
)


def _runtime_root() -> Path:
    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "memory"


def _default_pending_actions_path() -> Path:
    return _runtime_root() / "runtime_pending_actions.json"


def _default_approval_lifecycle_path() -> Path:
    return default_approval_lifecycle_path()


class HeartbeatDraftModel(BaseModel):
    """Structured heartbeat draft returned by the fast semantic router."""

    name: str = Field(default="", max_length=80)
    prompt: str = Field(default="", max_length=4000)
    cron_expression: str = Field(
        default="",
        description="A standard 5-field Linux cron expression for the heartbeat schedule.",
    )
    lane: Literal["auto", "fast", "slow"] = "fast"

    @field_validator("name", "prompt", "cron_expression", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()


class HeartbeatRouteModel(BaseModel):
    """Structured semantic route returned by Agent Framework."""

    route: Literal["create_heartbeat", "confirm_pending", "cancel_pending", "none"]
    draft: HeartbeatDraftModel | None = None


@dataclass
class HeartbeatDraft:
    """Validated heartbeat draft extracted from a natural-language request."""

    name: str
    prompt: str
    cron_expression: str
    lane: Literal["auto", "fast", "slow"] = "fast"


@dataclass
class PendingHeartbeatAction:
    """Pending heartbeat creation awaiting user confirmation."""

    id: str
    user_id: str
    conversation_id: str
    draft: dict[str, Any]
    created_at: str


@dataclass
class HeartbeatIntentOutcome:
    """Result returned to the chat flow when a heartbeat intent was handled."""

    response: str
    job: ScheduledJob | None = None
    pending: PendingHeartbeatAction | None = None


class PendingHeartbeatStore:
    """Small JSON store for pending heartbeat confirmations."""

    def __init__(
        self,
        path: Path | None = None,
        ttl_seconds: int = PENDING_HEARTBEAT_TTL_SECONDS,
        approval_service: ApprovalService | None = None,
    ):
        self.path = path or _default_pending_actions_path()
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lifecycle_path = (
            self.path.parent / "approval-lifecycle.json"
            if path is not None
            else _default_approval_lifecycle_path()
        )
        self.approval_service = approval_service or ApprovalService(lifecycle_path)

    def load(self) -> list[PendingHeartbeatAction]:
        if not self.path.exists():
            return []
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return []
        if not isinstance(raw, list):
            return []

        items: list[PendingHeartbeatAction] = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            action_id = str(item.get("id", "")).strip()
            user_id = str(item.get("user_id", "")).strip()
            draft = item.get("draft")
            if not action_id or not user_id or not isinstance(draft, dict):
                continue
            items.append(
                PendingHeartbeatAction(
                    id=action_id,
                    user_id=user_id,
                    conversation_id=str(item.get("conversation_id", "")).strip(),
                    draft=draft,
                    created_at=str(item.get("created_at", "")).strip(),
                )
            )
        active_items: list[PendingHeartbeatAction] = []
        for item in items:
            if self._is_expired(item):
                self.approval_service.mark_expired(item.id)
                continue
            active_items.append(item)
        if len(active_items) != len(items):
            self._save(active_items)
        return active_items

    def get_for_user(self, user_id: str) -> PendingHeartbeatAction | None:
        safe_user_id = str(user_id).strip()
        return next((item for item in self.load() if item.user_id == safe_user_id), None)

    def get_for_context(self, user_id: str, conversation_id: str | None) -> PendingHeartbeatAction | None:
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

    def get_by_action_id(self, user_id: str, action_id: str) -> PendingHeartbeatAction | None:
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

    def save_for_user(
        self,
        user_id: str,
        draft: HeartbeatDraft,
        conversation_id: str | None = None,
    ) -> PendingHeartbeatAction:
        safe_user_id = str(user_id).strip()
        action = PendingHeartbeatAction(
            id=f"pending-heartbeat-{uuid4().hex[:12]}",
            user_id=safe_user_id,
            conversation_id=str(conversation_id or "").strip(),
            draft=asdict(draft),
            created_at=to_iso_z(utc_now()),
        )
        items = [
            item
            for item in self.load()
            if not (
                item.user_id == safe_user_id
                and item.conversation_id == action.conversation_id
            )
        ]
        items.insert(0, action)
        self._save(items)
        self.approval_service.register_pending(
            action_id=action.id,
            user_id=action.user_id,
            conversation_id=action.conversation_id,
            source="heartbeat",
            action_kind="heartbeat_create",
            title="Heartbeat draft",
            summary=f"Approve creating the heartbeat '{draft.name}'.",
            metadata={"draft": action.draft},
            supersede_existing=True,
            supersede_scope="conversation",
        )
        return action

    def pop_for_user(self, user_id: str, *, status: str = "completed") -> PendingHeartbeatAction | None:
        safe_user_id = str(user_id).strip()
        current = self.load()
        action = next((item for item in current if item.user_id == safe_user_id), None)
        if action is None:
            return None
        self._save([item for item in current if item.user_id != safe_user_id])
        if status == "rejected":
            self.approval_service.mark_rejected(action.id)
        elif status == "expired":
            self.approval_service.mark_expired(action.id)
        elif status == "failed":
            self.approval_service.mark_failed(action.id)
        else:
            self.approval_service.mark_completed(action.id)
        return action

    def pop_by_action_id(self, user_id: str, action_id: str, *, status: str = "completed") -> PendingHeartbeatAction | None:
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
        elif status == "expired":
            self.approval_service.mark_expired(action.id)
        elif status == "failed":
            self.approval_service.mark_failed(action.id)
        else:
            self.approval_service.mark_completed(action.id)
        return action

    def _save(self, items: list[PendingHeartbeatAction]) -> None:
        self.path.write_text(
            json.dumps([asdict(item) for item in items], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _is_expired(self, item: PendingHeartbeatAction) -> bool:
        if self.ttl_seconds <= 0:
            return False
        created_at = parse_iso_datetime(item.created_at)
        if created_at is None:
            return False
        return (utc_now() - created_at).total_seconds() > self.ttl_seconds


class HeartbeatIntentService:
    """Turns semantically-routed chat requests into scheduled heartbeat jobs."""

    def __init__(
        self,
        *,
        runtime_manager: Any,
        store: RuntimeStore,
        pending_store: PendingHeartbeatStore | None = None,
    ):
        self.runtime_manager = runtime_manager
        self.store = store
        self.pending_store = pending_store or PendingHeartbeatStore()
        self.semantic_judges = SemanticJudgeService(runtime_manager)

    async def handle_message(
        self,
        user_id: str,
        user_message: str,
        conversation_id: str | None = None,
    ) -> HeartbeatIntentOutcome | None:
        """Handles pending confirmations or a new heartbeat draft."""
        pending = self.pending_store.get_for_context(user_id, conversation_id)
        route = await self._semantic_route(user_message, has_pending=pending is not None)

        if route is None:
            return None

        if pending is not None:
            if route.route == "confirm_pending":
                return HeartbeatIntentOutcome(
                    response=HEARTBEAT_CARD_ONLY_CONFIRMATION,
                    pending=pending,
                )
            if route.route == "cancel_pending":
                return HeartbeatIntentOutcome(
                    response=HEARTBEAT_CARD_ONLY_CONFIRMATION,
                    pending=pending,
                )

        if route.route == "none":
            return None

        draft = _validate_draft(route.draft)
        if draft is None:
            return HeartbeatIntentOutcome(response=FREQUENCY_CLARIFICATION)

        if self._requires_confirmation():
            action = self.pending_store.save_for_user(user_id, draft, conversation_id)
            return HeartbeatIntentOutcome(
                response=self._confirmation_response(action, draft),
                pending=action,
            )

        try:
            job = self._create_job(draft)
        except ValueError:
            return HeartbeatIntentOutcome(response=HEARTBEAT_CREATION_ERROR)
        return HeartbeatIntentOutcome(response=self._created_response(job), job=job)

    def handle_pending_decision(
        self,
        user_id: str,
        action_id: str,
        decision: Literal["approve", "reject"],
    ) -> HeartbeatIntentOutcome | None:
        """Executes a structured approval/rejection coming from the chat UI."""
        safe_action_id = str(action_id).strip()
        pending = self.pending_store.get_by_action_id(user_id, safe_action_id)
        if pending is None or pending.id != safe_action_id:
            record = self.pending_store.approval_service.get_by_action_id(safe_action_id)
            if record is None or record.user_id != str(user_id).strip():
                return None
            if record.source != "heartbeat" and record.action_kind != "heartbeat_create":
                return None
            if record.status == "pending":
                self.pending_store.approval_service.mark_expired(safe_action_id)
                response = "That heartbeat approval is no longer available. Please regenerate it before approving."
            elif record.status == "running":
                response = "That heartbeat approval is already running."
            elif record.status in {"completed", "approved"}:
                response = "That heartbeat approval was already processed."
            elif record.status == "rejected":
                response = "That heartbeat approval was already canceled."
            elif record.status == "failed":
                response = "That heartbeat approval was accepted earlier, but the execution failed."
            else:
                response = "That heartbeat approval is no longer active."
            return HeartbeatIntentOutcome(response=response)
        if decision == "reject":
            self.pending_store.pop_by_action_id(user_id, pending.id, status="rejected")
            return HeartbeatIntentOutcome(response="I cancelled the pending heartbeat creation.")

        draft = self._draft_from_dict(pending.draft)
        try:
            job = self._create_job(draft)
        except ValueError:
            return HeartbeatIntentOutcome(
                response=HEARTBEAT_CREATION_ERROR,
                pending=pending,
            )
        self.pending_store.pop_by_action_id(user_id, pending.id, status="completed")
        return HeartbeatIntentOutcome(
            response=self._created_response(job),
            job=job,
        )
    async def _semantic_route(
        self,
        user_message: str,
        *,
        has_pending: bool,
    ) -> HeartbeatRouteModel | None:
        """Routes the turn through the shared semantic-judge service."""
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        value = await service.judge_heartbeat_route(
            user_message=user_message,
            has_pending=has_pending,
            response_format=HeartbeatRouteModel,
        )
        return value if isinstance(value, HeartbeatRouteModel) else None

    def _get_semantic_judge_service(self) -> SemanticJudgeService | None:
        service = getattr(self, "semantic_judges", None)
        if service is not None:
            return service
        runtime_manager = getattr(self, "runtime_manager", None)
        if runtime_manager is None:
            return None
        service = SemanticJudgeService(runtime_manager)
        self.semantic_judges = service
        return service

    def _draft_from_dict(self, payload: dict[str, Any]) -> HeartbeatDraft:
        model = HeartbeatDraftModel.model_validate(payload)
        draft = _validate_draft(model)
        if draft is None:
            raise ValueError("Invalid pending heartbeat draft")
        return draft

    def _requires_confirmation(self) -> bool:
        try:
            return bool(HatchingStore().load().confirm_sensitive_actions)
        except Exception:
            return True

    def _create_job(self, draft: HeartbeatDraft) -> ScheduledJob:
        existing = self._find_duplicate(draft)
        if existing is not None:
            return existing
        return self.store.upsert_job(
            {
                "id": f"heartbeat-{uuid4().hex[:12]}",
                "name": draft.name,
                "prompt": draft.prompt,
                "lane": draft.lane,
                "schedule_kind": "cron",
                "cron_expression": draft.cron_expression,
                "enabled": True,
            }
        )

    def _find_duplicate(self, draft: HeartbeatDraft) -> ScheduledJob | None:
        wanted_prompt = draft.prompt.strip().casefold()
        wanted_cron = draft.cron_expression.strip()
        for job in self.store.load_jobs():
            if job.system:
                continue
            if job.schedule_kind != "cron":
                continue
            if job.cron_expression != wanted_cron:
                continue
            if job.prompt.strip().casefold() == wanted_prompt:
                return job
        return None

    def _confirmation_response(self, pending: PendingHeartbeatAction, draft: HeartbeatDraft) -> str:
        return render_approval_block(
            action_id=pending.id,
            action_kind="heartbeat_create",
            title="Heartbeat draft",
            summary=f"Approve creating the heartbeat '{draft.name}'.",
            approve_label="Create heartbeat",
            reject_label="Cancel",
            extra_fields={
                "Name": draft.name,
                "Schedule": f"`{draft.cron_expression}`",
                "Action": draft.prompt,
                "Delivery": "desktop chat",
            },
        )

    def _created_response(self, job: ScheduledJob) -> str:
        return (
            f"I created the heartbeat '{job.name}'. "
            f"It will run on cron schedule `{job.cron_expression}`. "
            "When it produces a response, I will send it to desktop chat."
        )


def _validate_draft(model: HeartbeatDraftModel | None) -> HeartbeatDraft | None:
    if model is None:
        return None
    name = model.name.strip()
    prompt = model.prompt.strip()
    cron_expression = model.cron_expression.strip()
    if not prompt or not cron_expression:
        return None
    if not name:
        name = "Chat heartbeat"
    return HeartbeatDraft(
        name=name[:80],
        prompt=prompt[:4000],
        cron_expression=cron_expression,
        lane=model.lane,
    )

