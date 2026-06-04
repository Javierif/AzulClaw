"""Semantic-judge calls and turn-status helpers for the orchestrator.

Extracted from ``conversation.py`` as a mixin. Wraps the
``SemanticJudgeService`` with typed verdict adapters and provides the
deterministic turn-status helpers used when a judge is unavailable. Relies on
orchestrator state (``self.semantic_judges``, ``self.runtime_manager``) and on
sibling mixin methods resolved via the combined MRO.
"""

import json

from .conversation_helpers import _coerce_semantic_bool, _strip_machine_pending_blocks
from .conversation_types import (
    FolderOrganizerPreviewContextVerdict,
    FolderOrganizerRequestVerdict,
    PendingActionStageVerdict,
    PendingActionUserIntentVerdict,
    TurnClosureVerdict,
)
from .cortex.fast.triage import TriageDecision
from .runtime.approval_protocol import contains_pending_action_block
from .runtime.pending_action_intent import PendingSensitiveAction
from .runtime.semantic_judge import SemanticJudgeService


class SemanticJudgeMixin:
    """Typed wrappers over the semantic judge plus deterministic turn-status helpers."""

    def _reply_contains_pending_action_block(self, text: str) -> bool:
        return contains_pending_action_block(text)

    def _looks_like_blocking_question(self, text: str) -> bool:
        candidate = (text or "").strip()
        return "?" in candidate or "¿" in candidate

    def _derive_turn_status_from_text(self, text: str, *, default: str = "final_answer") -> str:
        if self._reply_contains_pending_action_block(text):
            return "approval_required"
        if self._looks_like_blocking_question(text):
            return "blocking_question"
        return default

    def _deterministic_turn_closure_fallback(
        self,
        *,
        candidate_reply: str,
        lane: str,
        facts: dict[str, object],
    ) -> TurnClosureVerdict:
        if self._reply_contains_pending_action_block(candidate_reply):
            return TurnClosureVerdict(status="action_pending")
        if self._looks_like_blocking_question(candidate_reply):
            return TurnClosureVerdict(status="blocking_question")

        stripped = (candidate_reply or "").strip()
        if not stripped:
            return TurnClosureVerdict(
                status="incomplete_promise",
                should_retry=True,
                reason="Empty reply from deterministic fallback.",
            )

        structural_signal_count = 0
        if len(stripped) >= 240:
            structural_signal_count += 1
        if stripped.count("\n") >= 2:
            structural_signal_count += 1
        if "- " in stripped or "1. " in stripped or "2. " in stripped or "```" in stripped or "|" in stripped:
            structural_signal_count += 1

        if structural_signal_count >= 1:
            return TurnClosureVerdict(status="final_answer")

        high_risk_turn = bool(facts.get("pending_plan_revision")) or bool(facts.get("confirmed_sensitive_action")) or lane == "slow"
        if high_risk_turn:
            return TurnClosureVerdict(
                status="incomplete_promise",
                should_retry=True,
                reason="Judge unavailable and the draft is short, non-question, and non-structured on a high-risk turn.",
            )
        return TurnClosureVerdict(status="final_answer")

    def _extract_json_object(self, text: str) -> dict | None:
        raw = (text or "").strip()
        if not raw:
            return None
        fenced = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        for candidate in (fenced, raw):
            if candidate.startswith("{") and candidate.endswith("}"):
                try:
                    parsed = json.loads(candidate)
                except json.JSONDecodeError:
                    continue
                if isinstance(parsed, dict):
                    return parsed
        start = raw.find("{")
        end = raw.rfind("}")
        if start >= 0 and end > start:
            try:
                parsed = json.loads(raw[start : end + 1])
            except json.JSONDecodeError:
                return None
            return parsed if isinstance(parsed, dict) else None
        return None

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

    async def _judge_turn_closure(
        self,
        *,
        user_message: str,
        candidate_reply: str,
        history: list[dict],
        lane: str,
        triage_reason: str,
        facts: dict[str, object],
    ) -> TurnClosureVerdict | None:
        if self._reply_contains_pending_action_block(candidate_reply):
            return TurnClosureVerdict(status="action_pending")
        if not (candidate_reply or "").strip():
            return TurnClosureVerdict(
                status="incomplete_promise",
                should_retry=True,
                reason="The candidate reply is empty.",
            )

        recent_history_lines: list[str] = []
        for item in history[-4:]:
            role = str(item.get("role", "")).strip() or "unknown"
            content = _strip_machine_pending_blocks(str(item.get("content", "")).strip())
            if content:
                recent_history_lines.append(f"{role}: {content[:350]}")

        service = self._get_semantic_judge_service()
        if service is None:
            return None
        parsed = await service.judge_turn_closure(
            user_message=user_message,
            candidate_reply=candidate_reply,
            history_lines=recent_history_lines,
            lane=lane,
            triage_reason=triage_reason,
            facts=facts,
        )
        if not isinstance(parsed, dict):
            return None
        status = str(parsed.get("turn_status", "")).strip()
        if not status:
            return None
        return TurnClosureVerdict(
            status=status,
            should_retry=bool(parsed.get("should_retry", False)),
            reason=str(parsed.get("reason", "")).strip(),
        )

    async def _resolve_turn_closure_verdict(
        self,
        *,
        user_message: str,
        candidate_reply: str,
        history: list[dict],
        lane: str,
        triage_reason: str,
        facts: dict[str, object],
    ) -> TurnClosureVerdict:
        verdict = await self._judge_turn_closure(
            user_message=user_message,
            candidate_reply=candidate_reply,
            history=history,
            lane=lane,
            triage_reason=triage_reason,
            facts=facts,
        )
        if verdict is None:
            verdict = self._deterministic_turn_closure_fallback(
                candidate_reply=candidate_reply,
                lane=lane,
                facts=facts,
            )
        return verdict

    async def _judge_pending_action_stage(
        self,
        *,
        user_message: str,
        candidate_reply: str,
    ) -> PendingActionStageVerdict | None:
        if self._reply_contains_pending_action_block(candidate_reply):
            return PendingActionStageVerdict(decision="approval_ready")
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        parsed = await service.judge_pending_action_stage(
            user_message=user_message,
            candidate_reply=candidate_reply,
        )
        if not isinstance(parsed, dict):
            return None
        return PendingActionStageVerdict(
            decision=str(parsed.get("decision", "")).strip(),
            action_kind=str(parsed.get("action_kind", "")).strip(),
            title=str(parsed.get("title", "")).strip(),
            summary=str(parsed.get("summary", "")).strip(),
            reason=str(parsed.get("reason", "")).strip(),
        )

    async def _judge_pending_action_user_intent(
        self,
        *,
        user_message: str,
        pending_action: PendingSensitiveAction,
    ) -> PendingActionUserIntentVerdict | None:
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        parsed = await service.judge_pending_action_user_intent(
            user_message=user_message,
            pending_title=pending_action.title,
            pending_summary=pending_action.summary,
        )
        if not isinstance(parsed, dict):
            return None
        return PendingActionUserIntentVerdict(
            decision=str(parsed.get("decision", "")).strip(),
            reason=str(parsed.get("reason", "")).strip(),
        )

    async def _judge_folder_organizer_follow_up(
        self,
        *,
        user_message: str,
        pending_action: PendingSensitiveAction,
    ) -> PendingActionUserIntentVerdict | None:
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        parsed = await service.judge_folder_plan_follow_up(
            user_message=user_message,
            pending_title=pending_action.title,
            pending_summary=pending_action.summary,
        )
        if not isinstance(parsed, dict):
            return None
        return PendingActionUserIntentVerdict(
            decision=str(parsed.get("decision", "")).strip(),
            reason=str(parsed.get("reason", "")).strip(),
        )

    async def _judge_route_semantically(self, user_message: str) -> TriageDecision | None:
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        parsed = await service.judge_route(user_message=user_message)
        if not isinstance(parsed, dict):
            return None
        lane = str(parsed.get("lane", "")).strip()
        if lane not in {"fast", "slow"}:
            return None
        reason = str(parsed.get("reason", "")).strip() or ("default-fast" if lane == "fast" else "deep-analysis-request")
        return TriageDecision(lane=lane, reason=reason)

    async def _judge_folder_organizer_request(
        self,
        *,
        user_message: str,
    ) -> FolderOrganizerRequestVerdict | None:
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        parsed = await service.judge_folder_organizer_request(user_message=user_message)
        if not isinstance(parsed, dict):
            return None
        return FolderOrganizerRequestVerdict(
            decision=str(parsed.get("decision", "")).strip(),
            reason=str(parsed.get("reason", "")).strip(),
        )

    async def _judge_folder_organizer_preview_context(
        self,
        *,
        user_message: str,
        preview_summary: str,
        preview_payload: dict[str, object],
    ) -> FolderOrganizerPreviewContextVerdict | None:
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        parsed = await service.judge_folder_organizer_preview_context(
            user_message=user_message,
            preview_summary=preview_summary,
            preview_payload=preview_payload,
        )
        if not isinstance(parsed, dict):
            return None
        reply_language = str(parsed.get("reply_language", "")).strip().casefold()
        if reply_language not in {"es", "en"}:
            reply_language = "en"
        return FolderOrganizerPreviewContextVerdict(
            reply_language=reply_language,
            has_executable_plan=_coerce_semantic_bool(parsed.get("has_executable_plan")),
            conceptual_plan_requested=_coerce_semantic_bool(parsed.get("conceptual_plan_requested")),
            status_summary=str(parsed.get("status_summary", "")).strip(),
            reason=str(parsed.get("reason", "")).strip(),
        )
