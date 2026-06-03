"""Sensitive-action (HITL) and pending-decision handling for the orchestrator.

Extracted from ``conversation.py`` as a mixin. Covers staging approval cards,
judging confirmation intent, executing approved actions, and resolving
structured approve/reject decisions (including heartbeat automations). Relies on
orchestrator state (``self.pending_sensitive_actions``, ``self.heartbeat_intents``)
and on sibling mixin methods resolved via the combined MRO.
"""

import logging
from collections.abc import Awaitable, Callable

from .conversation_types import ConversationReply
from .runtime.pending_action_intent import (
    PENDING_SENSITIVE_ACTION_APPROVAL_PROMPT,
    PENDING_SENSITIVE_ACTION_CARD_ONLY_CONFIRMATION,
    PendingSensitiveAction,
    PendingSensitiveActionService,
)

LOGGER = logging.getLogger(__name__)


class SensitiveActionMixin:
    """Staging, confirmation, execution, and decision handling for sensitive actions."""

    def _is_confirming_sensitive_action(self, history: list[dict], user_message: str) -> bool:
        """Legacy typed confirmations are no longer accepted for sensitive actions."""
        return False

    def _get_pending_sensitive_action_service(self) -> PendingSensitiveActionService | None:
        service = getattr(self, "pending_sensitive_actions", None)
        return service if isinstance(service, PendingSensitiveActionService) else None

    async def _consume_pending_action_follow_up_context(
        self,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
    ) -> list[str]:
        service = self._get_pending_sensitive_action_service()
        if service is None:
            return []
        pending = service.get_pending_action_for_context(user_id, conversation_id)
        if pending is None:
            return []
        if pending.action_kind != "folder_organizer":
            service.discard_pending_action_for_context(user_id, conversation_id)
            return [
                "The user is no longer approving the previous sensitive action card. "
                "Treat the previous approval card as obsolete and continue with the new request."
            ]
        follow_up_verdict = await self._judge_folder_organizer_follow_up(
            user_message=user_message,
            pending_action=pending,
        )
        if follow_up_verdict is None or follow_up_verdict.decision != "revise_plan":
            service.discard_pending_action_for_context(user_id, conversation_id)
            return [
                "The user has moved on from the previous Folder Organizer approval flow. "
                "Treat the previous approval card as obsolete and continue with the new request."
            ]

        snapshot = pending.plan_snapshot if isinstance(pending.plan_snapshot, dict) else {}
        preview = snapshot.get("preview") if isinstance(snapshot.get("preview"), dict) else {}
        tool_arguments = snapshot.get("tool_arguments") if isinstance(snapshot.get("tool_arguments"), dict) else {}

        guidance = [
            "The user is revising a previously reviewed Folder Organizer plan.",
            "Only replace the previous approval card if you produce a revised plan or a new executable approval in this same turn.",
            "Reuse the reviewed preview and update the plan in this same turn.",
            "Do not say that you will inspect, analyze, or review the folder later if a usable preview already exists.",
            "Only ask to rescan if the preview is missing, unusable, expired, or the user explicitly requested a fresh scan.",
            "Return the revised plan now, incorporating the user's latest categorization changes.",
            "If confirmation is still required before moving files, replace the old card with a new exact Folder Organizer approval block.",
        ]
        if pending.source_user_message:
            guidance.append(f"Original reviewed request:\n{pending.source_user_message}")
        if pending.summary:
            guidance.append(f"Previous approved summary:\n{pending.summary}")
        if preview:
            preview_lines: list[str] = []
            summary = str(preview.get("summary", "")).strip()
            if summary:
                preview_lines.append(f"- Preview summary: {summary}")
            relative_path = str(preview.get("relative_path", tool_arguments.get("relative_path", "."))).strip() or "."
            preview_lines.append(f"- Scope: {relative_path}")
            if bool(preview.get("recursive", tool_arguments.get("recursive", False))):
                preview_lines.append("- Mode: recursive preview")
            batch_count = preview.get("remaining_batch_count", preview.get("batch_count"))
            if batch_count not in {None, ""}:
                preview_lines.append(f"- Pending batches: {batch_count}")
            categories = preview.get("semantic_custom_categories")
            if isinstance(categories, list):
                labels = [str(item).strip() for item in categories if str(item).strip()]
                if labels:
                    preview_lines.append(f"- Semantic categories: {', '.join(labels[:8])}")
            overrides = tool_arguments.get("category_overrides", preview.get("category_overrides"))
            if isinstance(overrides, dict) and overrides:
                formatted = ", ".join(
                    f"{str(key).strip()} -> {str(value).strip()}"
                    for key, value in list(overrides.items())[:8]
                    if str(key).strip() and str(value).strip()
                )
                if formatted:
                    preview_lines.append(f"- Existing overrides: {formatted}")
            guidance.append("Reviewed preview context:\n" + "\n".join(preview_lines))
        return ["\n\n".join(guidance)]

    async def _maybe_stage_sensitive_action_card(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
        reply_text: str,
        allow_pending_action_staging: bool,
        revision_label: str = "",
    ) -> str:
        if not allow_pending_action_staging:
            return reply_text
        service = self._get_pending_sensitive_action_service()
        if service is None:
            return reply_text
        try:
            stage_verdict = await self._judge_pending_action_stage(
                user_message=user_message,
                candidate_reply=reply_text,
            )
            if stage_verdict is None or stage_verdict.decision != "approval_ready":
                return reply_text
            return service.maybe_stage_action(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=user_message,
                assistant_response=reply_text,
                semantic_action_kind=stage_verdict.action_kind,
                semantic_title=stage_verdict.title,
                semantic_summary=stage_verdict.summary,
                revision_label=revision_label,
            )
        except Exception as error:
            LOGGER.warning("[PendingActions] Could not stage sensitive action card: %s", error)
            return reply_text

    async def _try_handle_sensitive_action_confirmation_attempt(
        self,
        user_id: str,
        user_message: str,
        *,
        conversation_id: str | None = None,
    ) -> ConversationReply | None:
        service = self._get_pending_sensitive_action_service()
        if service is None:
            return None
        try:
            pending = service.get_pending_action_for_context(user_id, conversation_id)
        except Exception as error:
            LOGGER.warning("[PendingActions] Typed confirmation check failed: %s", error)
            return None
        if pending is None:
            return None
        verdict = await self._judge_pending_action_user_intent(
            user_message=user_message,
            pending_action=pending,
        )
        if verdict is None or verdict.decision not in {"approve", "reject"}:
            return None
        await self.persist_with_vector_memory(
            user_id,
            "user",
            user_message,
            conversation_id=conversation_id,
        )
        await self.persist_with_vector_memory(
            user_id,
            "assistant",
            PENDING_SENSITIVE_ACTION_CARD_ONLY_CONFIRMATION,
            conversation_id=conversation_id,
        )
        return ConversationReply(
            text=PENDING_SENSITIVE_ACTION_CARD_ONLY_CONFIRMATION,
            lane="fast",
            triage_reason="pending-action-card-only",
            turn_status="approval_required",
        )

    async def _invoke_approved_sensitive_action(
        self,
        *,
        user_id: str,
        pending_action: PendingSensitiveAction,
        conversation_id: str | None,
    ) -> ConversationReply:
        history = self._load_chat_history(user_id, conversation_id, limit=12)
        execution_prompt = (
            f"{PENDING_SENSITIVE_ACTION_APPROVAL_PROMPT}\n\n"
            f"Original user request:\n{pending_action.source_user_message or pending_action.summary}\n\n"
            f"Approved action summary:\n{pending_action.summary}"
        )
        semantic_memories = await self.retrieve_semantic_memories(user_id, execution_prompt)
        user_knowledge = self.retrieve_user_knowledge(user_id)
        messages = self.build_agent_messages(
            history,
            semantic_memories,
            execution_prompt,
            user_knowledge,
            confirmed_sensitive_action=True,
        )
        reply = await self.invoke_messages(
            messages,
            execution_prompt,
            lane="slow",
            source="pending-action",
            title="Main conversation",
        )
        return ConversationReply(
            text=reply.text,
            model_id=reply.model_id,
            model_label=reply.model_label,
            process_id=reply.process_id,
            attempt_count=reply.attempt_count,
            skipped_models=reply.skipped_models,
            failed_attempts=reply.failed_attempts,
            lane="slow",
            triage_reason="pending-action",
            conversation_title=reply.conversation_title,
            turn_status=reply.turn_status,
        )

    async def _invoke_approved_sensitive_action_stream(
        self,
        *,
        user_id: str,
        pending_action: PendingSensitiveAction,
        conversation_id: str | None,
        on_delta: Callable[[str], Awaitable[None]],
    ) -> ConversationReply:
        history = self._load_chat_history(user_id, conversation_id, limit=12)
        execution_prompt = (
            f"{PENDING_SENSITIVE_ACTION_APPROVAL_PROMPT}\n\n"
            f"Original user request:\n{pending_action.source_user_message or pending_action.summary}\n\n"
            f"Approved action summary:\n{pending_action.summary}"
        )
        semantic_memories = await self.retrieve_semantic_memories(user_id, execution_prompt)
        user_knowledge = self.retrieve_user_knowledge(user_id)
        messages = self.build_agent_messages(
            history,
            semantic_memories,
            execution_prompt,
            user_knowledge,
            confirmed_sensitive_action=True,
        )
        reply = await self.invoke_messages_stream(
            messages,
            execution_prompt,
            lane="slow",
            source="pending-action",
            title="Main conversation",
            on_delta=on_delta,
            tools_enabled=True,
        )
        return ConversationReply(
            text=reply.text,
            model_id=reply.model_id,
            model_label=reply.model_label,
            process_id=reply.process_id,
            attempt_count=reply.attempt_count,
            skipped_models=reply.skipped_models,
            failed_attempts=reply.failed_attempts,
            lane="slow",
            triage_reason="pending-action",
            conversation_title=reply.conversation_title,
            turn_status=reply.turn_status,
        )

    async def _try_handle_heartbeat_intent(
        self,
        user_id: str,
        user_message: str,
        conversation_id: str | None = None,
    ) -> ConversationReply | None:
        """Intercepts chat turns that create or confirm heartbeat automations."""
        try:
            outcome = await self.heartbeat_intents.handle_message(user_id, user_message, conversation_id)
        except Exception as error:
            LOGGER.warning("[Heartbeats] Intent flow failed, falling back to chat: %s", error)
            return None

        if outcome is None:
            return None

        await self.persist_with_vector_memory(
            user_id,
            "user",
            user_message,
            conversation_id=conversation_id,
        )
        await self.persist_with_vector_memory(
            user_id,
            "assistant",
            outcome.response,
            conversation_id=conversation_id,
        )
        return ConversationReply(
            text=outcome.response,
            lane="fast",
            triage_reason="heartbeat-intent",
            turn_status=self._derive_turn_status_from_text(outcome.response, default="final_answer"),
        )

    async def _try_handle_pending_action_decision(
        self,
        user_id: str,
        action_id: str,
        decision: str,
        *,
        conversation_id: str | None = None,
    ) -> ConversationReply | None:
        """Handles structured chat approvals for pending sensitive actions."""
        normalized_decision = str(decision or "").strip().lower()
        if normalized_decision not in {"approve", "reject"}:
            return None
        heartbeat_service = getattr(self, "heartbeat_intents", None)
        heartbeat_outcome = (
            heartbeat_service.handle_pending_decision(
                user_id,
                str(action_id or "").strip(),
                "approve" if normalized_decision == "approve" else "reject",
            )
            if heartbeat_service is not None
            else None
        )
        if heartbeat_outcome is not None:
            await self.persist_with_vector_memory(
                user_id,
                "assistant",
                heartbeat_outcome.response,
                conversation_id=conversation_id,
            )
            return ConversationReply(
                text=heartbeat_outcome.response,
                lane="fast",
                triage_reason="pending-action",
                turn_status=self._derive_turn_status_from_text(heartbeat_outcome.response, default="final_answer"),
            )

        service = self._get_pending_sensitive_action_service()
        if service is None:
            return None
        try:
            sensitive_outcome = service.handle_pending_decision(
                user_id,
                str(action_id or "").strip(),
                "approve" if normalized_decision == "approve" else "reject",
            )
        except Exception as error:
            LOGGER.warning("[PendingActions] Could not resolve pending decision: %s", error)
            return None
        if sensitive_outcome is None:
            return None
        if sensitive_outcome.kind == "reject" or sensitive_outcome.action is None:
            await self.persist_with_vector_memory(
                user_id,
                "assistant",
                sensitive_outcome.response,
                conversation_id=conversation_id,
            )
            return ConversationReply(
                text=sensitive_outcome.response,
                lane="fast",
                triage_reason="pending-action",
                turn_status=self._derive_turn_status_from_text(sensitive_outcome.response, default="final_answer"),
            )

        try:
            reply = await self._invoke_approved_sensitive_action(
                user_id=user_id,
                pending_action=sensitive_outcome.action,
                conversation_id=conversation_id,
            )
        except Exception as error:
            failure_text = f"Could not execute the approved action: {error}"
            try:
                service.mark_execution_failed(sensitive_outcome.action, failure_text)
            except Exception as receipt_error:
                LOGGER.warning("[PendingActions] Could not mark execution failure: %s", receipt_error)
            await self.persist_with_vector_memory(
                user_id,
                "assistant",
                failure_text,
                conversation_id=conversation_id,
            )
            return ConversationReply(
                text=failure_text,
                lane="slow",
                triage_reason="pending-action",
                turn_status="tool_failure",
            )
        try:
            if reply.turn_status == "tool_failure":
                service.mark_execution_failed(sensitive_outcome.action, reply.text)
            else:
                service.mark_execution_completed(sensitive_outcome.action, reply.text)
        except Exception as receipt_error:
            LOGGER.warning("[PendingActions] Could not mark execution completion: %s", receipt_error)
        await self.persist_with_vector_memory(
            user_id,
            "assistant",
            reply.text,
            conversation_id=conversation_id,
        )
        return reply
