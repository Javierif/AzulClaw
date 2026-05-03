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
from .store import RuntimeStore, ScheduledJob, parse_iso_datetime, to_iso_z, utc_now


HEARTBEAT_CONFIRMATION_ID = "pending-heartbeat-create"
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


def _runtime_root() -> Path:
    override = os.environ.get("AZUL_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[3] / "memory"


def _default_pending_actions_path() -> Path:
    return _runtime_root() / "runtime_pending_actions.json"


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

    def __init__(self, path: Path | None = None, ttl_seconds: int = PENDING_HEARTBEAT_TTL_SECONDS):
        self.path = path or _default_pending_actions_path()
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)

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
                    draft=draft,
                    created_at=str(item.get("created_at", "")).strip(),
                )
            )
        active_items = [item for item in items if not self._is_expired(item)]
        if len(active_items) != len(items):
            self._save(active_items)
        return active_items

    def get_for_user(self, user_id: str) -> PendingHeartbeatAction | None:
        safe_user_id = str(user_id).strip()
        return next((item for item in self.load() if item.user_id == safe_user_id), None)

    def save_for_user(self, user_id: str, draft: HeartbeatDraft) -> PendingHeartbeatAction:
        safe_user_id = str(user_id).strip()
        action = PendingHeartbeatAction(
            id=HEARTBEAT_CONFIRMATION_ID,
            user_id=safe_user_id,
            draft=asdict(draft),
            created_at=to_iso_z(utc_now()),
        )
        items = [item for item in self.load() if item.user_id != safe_user_id]
        items.insert(0, action)
        self._save(items)
        return action

    def pop_for_user(self, user_id: str) -> PendingHeartbeatAction | None:
        safe_user_id = str(user_id).strip()
        current = self.load()
        action = next((item for item in current if item.user_id == safe_user_id), None)
        if action is None:
            return None
        self._save([item for item in current if item.user_id != safe_user_id])
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

    async def handle_message(self, user_id: str, user_message: str) -> HeartbeatIntentOutcome | None:
        """Handles pending confirmations or a new heartbeat draft."""
        pending = self.pending_store.get_for_user(user_id)
        route = await self._semantic_route(user_message, has_pending=pending is not None)

        if route is None:
            route = self._local_pending_route(user_message) if pending is not None else None
            if route is None:
                return None

        if pending is not None:
            if route.route == "confirm_pending":
                draft = self._draft_from_dict(pending.draft)
                try:
                    job = self._create_job(draft)
                except ValueError:
                    return HeartbeatIntentOutcome(
                        response=HEARTBEAT_CREATION_ERROR,
                        pending=pending,
                    )
                self.pending_store.pop_for_user(user_id)
                return HeartbeatIntentOutcome(
                    response=self._created_response(job),
                    job=job,
                )
            if route.route == "cancel_pending":
                self.pending_store.pop_for_user(user_id)
                return HeartbeatIntentOutcome(
                    response="I cancelled the pending heartbeat creation."
                )

        if route.route == "none":
            return None

        draft = _validate_draft(route.draft)
        if draft is None:
            return HeartbeatIntentOutcome(response=FREQUENCY_CLARIFICATION)

        if self._requires_confirmation():
            action = self.pending_store.save_for_user(user_id, draft)
            return HeartbeatIntentOutcome(
                response=self._confirmation_response(draft),
                pending=action,
            )

        try:
            job = self._create_job(draft)
        except ValueError:
            return HeartbeatIntentOutcome(response=HEARTBEAT_CREATION_ERROR)
        return HeartbeatIntentOutcome(response=self._created_response(job), job=job)

    def _local_pending_route(self, user_message: str) -> HeartbeatRouteModel | None:
        """Fallback for explicit pending confirmation/cancellation when the router is unavailable."""
        normalized = " ".join((user_message or "").strip().casefold().split())
        normalized = normalized.strip(" .!¡?¿")
        confirm = {
            "yes",
            "yes create it",
            "yes, create it",
            "create it",
            "confirm",
            "ok",
            "okay",
            "si",
            "sí",
            "si crealo",
            "sí créalo",
            "crealo",
            "créalo",
            "confirmar",
        }
        cancel = {
            "no",
            "cancel",
            "cancel it",
            "cancelar",
            "descartar",
            "no lo crees",
            "no crear",
        }
        if normalized in confirm:
            return HeartbeatRouteModel(route="confirm_pending")
        if normalized in cancel:
            return HeartbeatRouteModel(route="cancel_pending")
        return None

    async def _semantic_route(
        self,
        user_message: str,
        *,
        has_pending: bool,
    ) -> HeartbeatRouteModel | None:
        """Routes the turn with the fast model using native structured output."""
        try:
            from agent_framework import Message
        except ModuleNotFoundError:
            return None

        messages = [
            Message(
                role="system",
                contents=(
                    "You are a semantic router for AzulClaw chat turns. "
                    "Select exactly one route: create_heartbeat, confirm_pending, "
                    "cancel_pending, or none. "
                    "Use create_heartbeat only when the user is asking to create a recurring "
                    "automation, heartbeat, or scheduled task. "
                    "For create_heartbeat, return a draft with a short name, the action prompt, "
                    "a standard 5-field Linux cron expression evaluated in the machine's local "
                    "timezone, and lane='fast' unless the user explicitly asks for deep reasoning. "
                    "Use confirm_pending or cancel_pending only when has_pending is true and "
                    "the user is clearly confirming or cancelling the pending heartbeat draft. "
                    "If the schedule is ambiguous or cannot be represented as cron, still use "
                    "create_heartbeat but leave draft.cron_expression empty."
                ),
            ),
            Message(
                role="user",
                contents=json.dumps(
                    {
                        "has_pending": has_pending,
                        "message": user_message,
                    },
                    ensure_ascii=False,
                ),
            ),
        ]
        try:
            result = await self.runtime_manager.execute_messages(
                messages=messages,
                lane="fast",
                title="Heartbeat semantic routing",
                source="heartbeat-router",
                kind="agent-run",
                response_format=HeartbeatRouteModel,
                tools_enabled=False,
                instructions=None,
            )
        except Exception:
            return None
        value = getattr(result, "value", None)
        return value if isinstance(value, HeartbeatRouteModel) else None

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

    def _confirmation_response(self, draft: HeartbeatDraft) -> str:
        return (
            "I can create this heartbeat:\n\n"
            f"Name: {draft.name}\n"
            f"Schedule: `{draft.cron_expression}`\n"
            f"Action: {draft.prompt}\n"
            "Delivery: desktop chat\n\n"
            "Reply 'yes, create it' to activate it or 'no' to cancel."
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
