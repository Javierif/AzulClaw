"""Turn-closure enforcement for the orchestrator.

Extracted from ``conversation.py`` as a mixin. Stages any pending sensitive
action card, resolves the closure verdict, and retries/recovers the draft when
it fails to validly close the turn (including Folder Organizer capability
recovery). Relies on sibling mixin methods (judges, inference, sensitive
actions, folder-organizer recovery) resolved via the combined MRO.
"""

from agent_framework import Content

from .conversation_helpers import _map_verdict_to_turn_status
from .conversation_types import TURN_CLOSURE_FAILURE_TEXT, ConversationReply, TurnClosureVerdict

_TURN_CLOSURE_ALLOWED_STATUSES = {"final_answer", "blocking_question", "action_pending", "tool_failure"}


class TurnClosureMixin:
    """Enforces valid turn closure with staged retries and recovery."""

    async def _enforce_turn_closure(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        history: list[dict],
        user_message: str,
        reply: ConversationReply,
        lane: str,
        route_reason: str,
        tools_enabled: bool,
        semantic_memories: list[dict] | None = None,
        base_extra_system_messages: list[str] | None = None,
        confirmed_sensitive_action: bool = False,
        user_knowledge: list[dict] | None = None,
        document_context: str = "",
        visual_contents: list[Content] | None = None,
        allow_pending_action_staging: bool = True,
        pending_plan_revision: bool = False,
    ) -> ConversationReply:
        staged_text = await self._maybe_stage_sensitive_action_card(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
            reply_text=reply.text,
            allow_pending_action_staging=allow_pending_action_staging,
            revision_label="Plan revised" if pending_plan_revision else "",
        )
        facts = {
            "confirmed_sensitive_action": confirmed_sensitive_action,
            "pending_plan_revision": pending_plan_revision,
            "pending_action_block_present": self._reply_contains_pending_action_block(staged_text),
            "document_context_present": bool(document_context.strip()),
            "visual_inputs_present": bool(visual_contents),
            "tools_enabled": tools_enabled,
        }
        capability_verdict = await self._judge_folder_organizer_capability_contract(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
            candidate_reply=staged_text,
        )
        verdict = await self._resolve_turn_closure_verdict(
            user_message=user_message,
            candidate_reply=staged_text,
            history=history,
            lane=lane,
            triage_reason=route_reason,
            facts=facts,
        )
        if capability_verdict is not None and capability_verdict.decision == "invalid":
            verdict = TurnClosureVerdict(
                status="incomplete_promise",
                should_retry=capability_verdict.should_retry or True,
                reason=capability_verdict.reason or verdict.reason,
            )
        if verdict.status in _TURN_CLOSURE_ALLOWED_STATUSES or not verdict.should_retry:
            reply.text = staged_text
            reply.turn_status = _map_verdict_to_turn_status(verdict.status)
            return reply
        runtime_execute = getattr(getattr(self, "runtime_manager", None), "execute_messages", None)
        if not callable(runtime_execute):
            reply.text = staged_text
            reply.turn_status = self._derive_turn_status_from_text(staged_text, default="final_answer")
            return reply

        retry_system_messages = [
            *(base_extra_system_messages or []),
            (
                "Your previous draft did not validly close the turn. "
                "Do not end with promised future work. "
                "Complete the work now, ask one concrete blocking question, create a structured pending action if approval is required, "
                "or report a real limitation. "
                f"Previous draft:\n{staged_text}"
            ),
        ]
        if capability_verdict is not None and capability_verdict.decision == "invalid":
            retry_system_messages.append(
                "Capability contract correction: "
                + (
                    capability_verdict.guidance
                    or "Respect the actual Folder Organizer capabilities and do not ask for unsupported path/configuration input."
                )
            )
        retry_messages = self.build_agent_messages(
            history,
            semantic_memories or [],
            user_message,
            user_knowledge,
            document_context=document_context,
            visual_contents=visual_contents,
            confirmed_sensitive_action=confirmed_sensitive_action,
            extra_system_messages=retry_system_messages,
        )
        rewritten = await self.invoke_messages(
            retry_messages,
            user_message,
            lane=lane,
            source="turn-closure-retry",
            title="Main conversation",
            tools_enabled=tools_enabled,
        )
        rewritten.text = await self._maybe_stage_sensitive_action_card(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
            reply_text=rewritten.text,
            allow_pending_action_staging=allow_pending_action_staging,
            revision_label="Plan revised" if pending_plan_revision else "",
        )
        rewritten_verdict = await self._resolve_turn_closure_verdict(
            user_message=user_message,
            candidate_reply=rewritten.text,
            history=history,
            lane=lane,
            triage_reason=route_reason,
            facts={
                **facts,
                "pending_action_block_present": self._reply_contains_pending_action_block(rewritten.text),
            },
        )
        rewritten_capability_verdict = await self._judge_folder_organizer_capability_contract(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
            candidate_reply=rewritten.text,
        )
        if rewritten_capability_verdict is not None and rewritten_capability_verdict.decision == "invalid":
            recovered = await self._recover_folder_organizer_reply_from_preview(
                user_id=user_id,
                conversation_id=conversation_id,
                history=history,
                user_message=user_message,
                lane=lane,
                semantic_memories=semantic_memories,
                user_knowledge=user_knowledge,
                document_context=document_context,
                visual_contents=visual_contents,
                confirmed_sensitive_action=confirmed_sensitive_action,
            )
            if recovered is not None:
                recovered_capability_verdict = await self._judge_folder_organizer_capability_contract(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_message=user_message,
                    candidate_reply=recovered.text,
                )
                recovered_turn_verdict = await self._resolve_turn_closure_verdict(
                    user_message=user_message,
                    candidate_reply=recovered.text,
                    history=history,
                    lane=lane,
                    triage_reason=route_reason,
                    facts=facts,
                )
                if (
                    (recovered_capability_verdict is None or recovered_capability_verdict.decision != "invalid")
                    and recovered_turn_verdict.status in _TURN_CLOSURE_ALLOWED_STATUSES
                ):
                    recovered.turn_status = _map_verdict_to_turn_status(recovered_turn_verdict.status)
                    return recovered
            preview_safe_reply = await self._build_folder_organizer_preview_safe_reply(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=user_message,
            )
            if preview_safe_reply:
                rewritten.text = preview_safe_reply
                rewritten.turn_status = "final_answer"
                return rewritten
            rewritten.text = TURN_CLOSURE_FAILURE_TEXT
            rewritten.turn_status = "tool_failure"
            return rewritten
        if rewritten_verdict.status in _TURN_CLOSURE_ALLOWED_STATUSES or not rewritten_verdict.should_retry:
            rewritten.turn_status = _map_verdict_to_turn_status(rewritten_verdict.status)
            return rewritten

        recovered = await self._recover_folder_organizer_reply_from_preview(
            user_id=user_id,
            conversation_id=conversation_id,
            history=history,
            user_message=user_message,
            lane=lane,
            semantic_memories=semantic_memories,
            user_knowledge=user_knowledge,
            document_context=document_context,
            visual_contents=visual_contents,
            confirmed_sensitive_action=confirmed_sensitive_action,
        )
        if recovered is not None:
            recovered_capability_verdict = await self._judge_folder_organizer_capability_contract(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=user_message,
                candidate_reply=recovered.text,
            )
            recovered_turn_verdict = await self._resolve_turn_closure_verdict(
                user_message=user_message,
                candidate_reply=recovered.text,
                history=history,
                lane=lane,
                triage_reason=route_reason,
                facts=facts,
            )
            if (
                (recovered_capability_verdict is None or recovered_capability_verdict.decision != "invalid")
                and recovered_turn_verdict.status in _TURN_CLOSURE_ALLOWED_STATUSES
            ):
                recovered.turn_status = _map_verdict_to_turn_status(recovered_turn_verdict.status)
                return recovered
        preview_safe_reply = await self._build_folder_organizer_preview_safe_reply(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
        )
        if preview_safe_reply:
            rewritten.text = preview_safe_reply
            rewritten.turn_status = "final_answer"
            return rewritten
        rewritten.text = TURN_CLOSURE_FAILURE_TEXT
        rewritten.turn_status = "tool_failure"
        return rewritten
