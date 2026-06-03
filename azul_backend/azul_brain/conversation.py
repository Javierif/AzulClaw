"""Reusable conversation services for the bot and desktop API."""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable

from agent_framework import Message

from .conversation_types import (
    CapabilityContractVerdict,
    ConversationReply,
    FolderOrganizerPreviewContextVerdict,
    FolderOrganizerRequestVerdict,
    PendingActionStageVerdict,
    PendingActionUserIntentVerdict,
    TURN_CLOSURE_FAILURE_TEXT,
    TurnClosureVerdict,
)
from .conversation_helpers import (
    _utcnow_iso,
    should_skip_vectorization,
)
from .attachments import AttachmentError
from .cortex.fast.commentary import (
    build_commentary,
    build_progress_snapshot,
)
from .cortex.fast.triage import TriageDecision
from .conversation_actions import SensitiveActionMixin
from .conversation_attachments import AttachmentMixin
from .conversation_inference import InferenceMixin
from .conversation_judges import SemanticJudgeMixin
from .conversation_memory import MemoryMixin
from .conversation_progress import ProgressMixin
from .conversation_routing import RoutingMixin
from .conversation_skills import SkillWorkflowMixin
from .conversation_titles import TitleMixin
from .conversation_turn_closure import TurnClosureMixin
from .runtime.agent_runtime import AgentRuntimeManager
from .runtime.heartbeat_intent import HeartbeatIntentService
from .runtime.pending_action_intent import (
    PendingSensitiveActionService,
    pending_sensitive_action_capture_context,
)
from .runtime.semantic_judge import SemanticJudgeService
from .runtime.skill_workflow_runtime import SkillWorkflowRuntime

LOGGER = logging.getLogger(__name__)


class ConversationOrchestrator(
    MemoryMixin,
    TitleMixin,
    AttachmentMixin,
    RoutingMixin,
    ProgressMixin,
    SensitiveActionMixin,
    SemanticJudgeMixin,
    InferenceMixin,
    TurnClosureMixin,
    SkillWorkflowMixin,
):
    """Orchestrates memory, semantic retrieval, and agent invocation."""

    def __init__(
        self,
        mcp_client,
        runtime_manager: AgentRuntimeManager,
        skill_workflow_runtime: SkillWorkflowRuntime | None = None,
    ):
        self.mcp_client = mcp_client
        self.runtime_manager = runtime_manager
        self.skill_workflow_runtime = skill_workflow_runtime or SkillWorkflowRuntime()
        self.heartbeat_intents = HeartbeatIntentService(
            runtime_manager=runtime_manager,
            store=runtime_manager.store,
        )
        self.pending_sensitive_actions = PendingSensitiveActionService()
        self.semantic_judges = SemanticJudgeService(runtime_manager)
        self._setup_memory_layers()

    async def process_message(
        self,
        *,
        user_id: str,
        user_message: str,
        lane: str = "auto",
        source: str = "chat",
        store_memory: bool = True,
        title: str | None = None,
    ) -> str:
        """Builds context, runs inference, and persists the conversation if applicable."""
        route = await self.resolve_route_async(user_message, lane)
        effective_lane = route.lane
        history = self.memory.get_history(user_id, limit=12)
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        user_knowledge = self.retrieve_user_knowledge(user_id)
        messages = self.build_agent_messages(history, semantic_memories, user_message, user_knowledge)

        LOGGER.info("[Brain] Message received. History=%s Knowledge=%s", len(history), len(user_knowledge))
        reply = await self.invoke_messages(
            messages,
            user_message,
            lane=effective_lane,
            source=source,
            title=title or "Main conversation",
        )

        if store_memory:
            await self.persist_with_vector_memory(user_id, "user", user_message)
            await self.persist_with_vector_memory(user_id, "assistant", reply.text)
            # Fire-and-forget preference extraction (skip if message contains credentials)
            if self.preference_extractor and not should_skip_vectorization(user_message):
                self.preference_extractor.fire_and_forget(user_id, user_message, reply.text)

        return reply.text

    async def process_user_message(
        self,
        user_id: str,
        user_message: str,
        lane: str = "auto",
        conversation_id: str | None = None,
        attachment_ids: list[str] | None = None,
    ) -> ConversationReply:
        """Builds context, runs inference, and persists the conversation."""
        heartbeat_reply = await self._try_handle_heartbeat_intent(
            user_id,
            user_message,
            conversation_id=conversation_id,
        )
        if heartbeat_reply is not None:
            return heartbeat_reply
        pending_confirmation_reply = await self._try_handle_sensitive_action_confirmation_attempt(
            user_id,
            user_message,
            conversation_id=conversation_id,
        )
        if pending_confirmation_reply is not None:
            return pending_confirmation_reply

        workflow_plan_follow_up = await self._try_handle_skill_workflow_plan_follow_up(
            user_id=user_id,
            user_message=user_message,
            conversation_id=conversation_id,
        )
        if workflow_plan_follow_up is not None:
            return workflow_plan_follow_up

        workflow_reply = await self._try_handle_marketplace_skill_workflow(
            user_id=user_id,
            user_message=user_message,
            conversation_id=conversation_id,
        )
        if workflow_reply is not None:
            return workflow_reply

        pending_follow_up_system_messages = await self._consume_pending_action_follow_up_context(
            user_id,
            conversation_id,
            user_message,
        )
        pending_plan_revision = bool(pending_follow_up_system_messages)
        history = self._load_chat_history(user_id, conversation_id, limit=12)
        confirmed_sensitive_action = self._is_confirming_sensitive_action(history, user_message)
        route = (
            TriageDecision(lane="slow", reason="explicit-confirmation")
            if confirmed_sensitive_action and lane == "auto"
            else await self.resolve_route_async(user_message, lane)
        )
        if pending_follow_up_system_messages and lane == "auto":
            route = TriageDecision(lane="slow", reason="pending-action-revision")
        effective_lane = route.lane
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        user_knowledge = self.retrieve_user_knowledge(user_id)
        folder_preview_system_messages = await self._maybe_prepare_folder_organizer_plan_context(
            user_message=user_message,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        try:
            document_context, visual_contents, has_visual_inputs, effective_lane = self._prepare_attachment_inputs(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=user_message,
                lane=effective_lane,
                attachment_ids=attachment_ids,
            )
        except AttachmentError as error:
            error_text = str(error)
            await self.persist_with_vector_memory(
                user_id,
                "user",
                user_message,
                conversation_id=conversation_id,
            )
            await self.persist_with_vector_memory(
                user_id,
                "assistant",
                error_text,
                conversation_id=conversation_id,
            )
            return ConversationReply(text=error_text, lane=effective_lane, turn_status="tool_failure")
        messages = self.build_agent_messages(
            history,
            semantic_memories,
            user_message,
            user_knowledge,
            document_context=document_context,
            visual_contents=visual_contents,
            confirmed_sensitive_action=confirmed_sensitive_action,
            extra_system_messages=[*pending_follow_up_system_messages, *folder_preview_system_messages],
        )

        LOGGER.info("[Brain] Message received. History=%s Knowledge=%s", len(history), len(user_knowledge))
        with pending_sensitive_action_capture_context(user_id, conversation_id):
            reply = await self.invoke_messages(
                messages,
                user_message,
                lane=effective_lane,
                source="chat",
                title="Main conversation",
                tools_enabled=not has_visual_inputs,
            )
        reply = await self._enforce_turn_closure(
            user_id=user_id,
            conversation_id=conversation_id,
            history=history,
            user_message=user_message,
            reply=reply,
            lane=effective_lane,
            route_reason=route.reason,
            tools_enabled=not has_visual_inputs,
            semantic_memories=semantic_memories,
            base_extra_system_messages=[*pending_follow_up_system_messages, *folder_preview_system_messages],
            confirmed_sensitive_action=confirmed_sensitive_action,
            user_knowledge=user_knowledge,
            document_context=document_context,
            visual_contents=visual_contents,
            allow_pending_action_staging=not confirmed_sensitive_action,
            pending_plan_revision=pending_plan_revision,
            folder_organizer_context_hint=bool(folder_preview_system_messages),
        )

        user_message_id = await self.persist_with_vector_memory(
            user_id, "user", user_message, conversation_id=conversation_id
        )
        if attachment_ids and conversation_id and user_message_id and hasattr(self.memory, "bind_draft_attachments_to_message"):
            self.memory.bind_draft_attachments_to_message(
                attachment_ids=[str(item).strip() for item in attachment_ids if str(item).strip()],
                user_id=user_id,
                conversation_id=conversation_id,
                message_id=user_message_id,
            )
        await self.persist_with_vector_memory(user_id, "assistant", reply.text, conversation_id=conversation_id)
        # Fire-and-forget preference extraction (skip if message contains credentials)
        if self.preference_extractor and not should_skip_vectorization(user_message):
            self.preference_extractor.fire_and_forget(user_id, user_message, reply.text)
        if conversation_id:
            reply.conversation_title = self.memory.get_conversation_title(conversation_id)
        reply.triage_reason = (
            route.reason if effective_lane == route.lane else f"{route.reason}|visual-fallback-fast"
        )
        return reply

    def _load_chat_history(
        self,
        user_id: str,
        conversation_id: str | None,
        *,
        limit: int,
    ) -> list[dict]:
        """Returns conversation history, with a RAM fallback when SQLite is unavailable."""
        if not conversation_id:
            return self.memory.get_history(user_id, limit=limit)

        history = self.memory.get_conversation_messages(conversation_id, limit=limit)
        if history:
            return history
        if getattr(self.memory, "_conn", None) is None:
            return self.memory.get_history(user_id, limit=limit)
        return history

    async def process_user_message_stream(
        self,
        user_id: str,
        user_message: str,
        *,
        lane: str = "auto",
        conversation_id: str | None = None,
        attachment_ids: list[str] | None = None,
        on_delta: Callable[[str], Awaitable[None]],
        on_commentary: Callable[[str], Awaitable[None]] | None = None,
        on_progress: Callable[[str, dict], Awaitable[None]] | None = None,
    ) -> ConversationReply:
        """Builds context, runs inference, and emits streaming if applicable."""
        heartbeat_reply = await self._try_handle_heartbeat_intent(
            user_id,
            user_message,
            conversation_id=conversation_id,
        )
        started_at = _utcnow_iso()
        if heartbeat_reply is not None:
            if on_commentary is not None:
                await on_commentary("Processing that request now.")
            if on_progress is not None:
                await on_progress(
                    "progress-init",
                    build_progress_snapshot(
                        user_message,
                        reason=heartbeat_reply.triage_reason or "heartbeat-intent",
                        lane=heartbeat_reply.lane or "fast",
                        stage="delegated",
                        event_type="progress-init",
                        summary="Processing that request now.",
                        current_step_label="Processing confirmation",
                        started_at=started_at,
                        last_updated_at=_utcnow_iso(),
                    ),
                )
            await on_delta(heartbeat_reply.text)
            return heartbeat_reply
        pending_confirmation_reply = await self._try_handle_sensitive_action_confirmation_attempt(
            user_id,
            user_message,
            conversation_id=conversation_id,
        )
        if pending_confirmation_reply is not None:
            if on_commentary is not None:
                await on_commentary("Processing that request now.")
            if on_progress is not None:
                await on_progress(
                    "progress-init",
                    build_progress_snapshot(
                        user_message,
                        reason=pending_confirmation_reply.triage_reason or "pending-action-card-only",
                        lane=pending_confirmation_reply.lane or "fast",
                        stage="delegated",
                        event_type="progress-init",
                        summary="Processing that request now.",
                        current_step_label="Processing confirmation",
                        started_at=started_at,
                        last_updated_at=_utcnow_iso(),
                    ),
                )
            await on_delta(pending_confirmation_reply.text)
            return pending_confirmation_reply

        workflow_plan_follow_up = await self._try_handle_skill_workflow_plan_follow_up(
            user_id=user_id,
            user_message=user_message,
            conversation_id=conversation_id,
        )
        if workflow_plan_follow_up is not None:
            if on_commentary is not None:
                await on_commentary("Preparing the workflow approval now.")
            if on_progress is not None:
                await on_progress(
                    "progress-init",
                    build_progress_snapshot(
                        user_message,
                        reason=workflow_plan_follow_up.triage_reason or "skill-workflow-plan-approved",
                        lane=workflow_plan_follow_up.lane or "fast",
                        stage="delegated",
                        event_type="progress-init",
                        summary="Preparing the workflow approval now.",
                        current_step_label="Preparing approval",
                        started_at=started_at,
                        last_updated_at=_utcnow_iso(),
                    ),
                )
            await on_delta(workflow_plan_follow_up.text)
            return workflow_plan_follow_up

        workflow_reply = await self._try_handle_marketplace_skill_workflow(
            user_id=user_id,
            user_message=user_message,
            conversation_id=conversation_id,
        )
        if workflow_reply is not None:
            if on_commentary is not None:
                await on_commentary("Running the installed skill workflow now.")
            if on_progress is not None:
                await on_progress(
                    "progress-init",
                    build_progress_snapshot(
                        user_message,
                        reason=workflow_reply.triage_reason or "skill-workflow",
                        lane=workflow_reply.lane or "fast",
                        stage="delegated",
                        event_type="progress-init",
                        summary="Running the installed skill workflow now.",
                        current_step_label="Running skill workflow",
                        started_at=started_at,
                        last_updated_at=_utcnow_iso(),
                    ),
                )
            await on_delta(workflow_reply.text)
            return workflow_reply

        pending_follow_up_system_messages = await self._consume_pending_action_follow_up_context(
            user_id,
            conversation_id,
            user_message,
        )
        pending_plan_revision = bool(pending_follow_up_system_messages)
        folder_preview_system_messages = await self._maybe_prepare_folder_organizer_plan_context(
            user_message=user_message,
            user_id=user_id,
            conversation_id=conversation_id,
        )
        route = await self.resolve_route_async(user_message, lane)
        effective_lane = route.lane
        if pending_follow_up_system_messages and lane == "auto":
            route = TriageDecision(lane="slow", reason="pending-action-revision")
            effective_lane = route.lane
        last_visible_update = time.monotonic()
        user_message_id: str | None = None
        user_turn_persisted = False
        attachments_bound = False
        assistant_persisted = False
        streamed_fragments: list[str] = []
        initial_commentary = build_commentary(
            user_message,
            reason=route.reason,
            lane=effective_lane,
        )
        progress_blueprint: dict | None = None

        async def ensure_user_turn_persisted() -> str | None:
            nonlocal user_message_id, user_turn_persisted, attachments_bound
            if not user_turn_persisted:
                user_message_id = await self.persist_with_vector_memory(
                    user_id,
                    "user",
                    user_message,
                    conversation_id=conversation_id,
                )
                user_turn_persisted = True
            if (
                not attachments_bound
                and attachment_ids
                and conversation_id
                and user_message_id
                and hasattr(self.memory, "bind_draft_attachments_to_message")
            ):
                self.memory.bind_draft_attachments_to_message(
                    attachment_ids=[str(item).strip() for item in attachment_ids if str(item).strip()],
                    user_id=user_id,
                    conversation_id=conversation_id,
                    message_id=user_message_id,
                )
                attachments_bound = True
            return user_message_id

        async def persist_assistant_reply(text: str) -> None:
            nonlocal assistant_persisted
            if assistant_persisted:
                return
            await self.persist_with_vector_memory(
                user_id,
                "assistant",
                text,
                conversation_id=conversation_id,
            )
            assistant_persisted = True

        async def emit_commentary(text: str) -> None:
            nonlocal last_visible_update
            last_visible_update = time.monotonic()
            if on_commentary is not None:
                await on_commentary(text)

        async def emit_progress(
            event_type: str,
            *,
            stage: str,
            summary: str,
            current_step_label: str = "",
            tick: int = 0,
            blueprint: dict | None = None,
        ) -> None:
            nonlocal last_visible_update
            if on_progress is None:
                return
            last_visible_update = time.monotonic()
            await on_progress(
                event_type,
                build_progress_snapshot(
                    user_message,
                    reason=route.reason,
                    lane=effective_lane,
                    stage=stage,
                    event_type=event_type,
                    tick=tick,
                    summary=summary,
                    current_step_label=current_step_label,
                    started_at=started_at,
                    last_updated_at=_utcnow_iso(),
                    blueprint=blueprint,
                ),
            )

        first_delta_seen = False

        async def emit_delta(text: str, *, mark_streaming: bool = True) -> None:
            nonlocal first_delta_seen, last_visible_update
            if mark_streaming and not first_delta_seen:
                first_delta_seen = True
                await emit_progress(
                    "progress-update",
                    stage="finalizing",
                    summary="Streaming answer now.",
                    current_step_label="Streaming answer",
                    blueprint=progress_blueprint,
                )
            last_visible_update = time.monotonic()
            if text:
                streamed_fragments.append(text)
            await on_delta(text)

        history = self._load_chat_history(user_id, conversation_id, limit=12)
        confirmed_sensitive_action = self._is_confirming_sensitive_action(history, user_message)
        if confirmed_sensitive_action and lane == "auto":
            route = TriageDecision(lane="slow", reason="explicit-confirmation")
            effective_lane = route.lane
        is_first_turn = len(history) == 0
        requested_attachment_ids = [str(item).strip() for item in (attachment_ids or []) if str(item).strip()]
        await ensure_user_turn_persisted()
        await emit_commentary(initial_commentary)
        await emit_progress(
            "progress-init",
            stage="delegated",
            summary=initial_commentary,
        )
        if requested_attachment_ids:
            await emit_progress(
                "progress-update",
                stage="delegated",
                summary="Reading files and gathering context.",
                current_step_label="Reading files",
            )
        try:
            document_context, visual_contents, has_visual_inputs, effective_lane = self._prepare_attachment_inputs(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=user_message,
                lane=effective_lane,
                attachment_ids=attachment_ids,
            )
        except AttachmentError as error:
            error_text = str(error)
            if on_commentary is not None:
                await on_commentary("I hit an issue while preparing the request.")
            if on_progress is not None:
                await on_progress(
                    "progress-done",
                    build_progress_snapshot(
                        user_message,
                        reason=route.reason,
                        lane=effective_lane,
                        stage="done",
                        event_type="progress-done",
                        summary="I hit an issue while preparing the request.",
                        current_step_label="Preparation failed",
                        started_at=started_at,
                        last_updated_at=_utcnow_iso(),
                    ),
                )
            await ensure_user_turn_persisted()
            await persist_assistant_reply(error_text)
            await emit_delta(error_text, mark_streaming=False)
            return ConversationReply(text=error_text, lane=effective_lane, turn_status="tool_failure")
        semantic_memories = await self.retrieve_semantic_memories(user_id, user_message)
        user_knowledge = self.retrieve_user_knowledge(user_id)

        if effective_lane == "slow":
            initial_commentary, progress_blueprint = await self.generate_fast_visible_plan(
                user_message,
                reason=route.reason,
            )
            await emit_commentary(initial_commentary)
            await emit_progress(
                "progress-update",
                stage="delegated",
                summary=initial_commentary,
                blueprint=progress_blueprint,
            )
        messages = self.build_agent_messages(
            history,
            semantic_memories,
            user_message,
            user_knowledge,
            document_context=document_context,
            visual_contents=visual_contents,
            confirmed_sensitive_action=confirmed_sensitive_action,
            extra_system_messages=[*pending_follow_up_system_messages, *folder_preview_system_messages],
        )
        context_commentary = "I now have the necessary context. Preparing the full response."
        if effective_lane == "slow":
            await emit_commentary(context_commentary)
            await emit_progress(
                "progress-update",
                stage="context-ready",
                summary=context_commentary,
                blueprint=progress_blueprint,
            )
        await emit_progress(
            "progress-update",
            stage="finalizing" if effective_lane == "fast" else "context-ready",
            summary="Waiting for model response.",
            current_step_label="Waiting for model response",
            blueprint=progress_blueprint,
        )
        commentary_task: asyncio.Task | None = None
        defer_stream_until_final_reply = bool(folder_preview_system_messages)
        if effective_lane == "slow":
            commentary_task = asyncio.create_task(
                self._slow_commentary_loop(
                    user_message,
                    reason=route.reason,
                    on_commentary=emit_commentary,
                    on_progress=emit_progress,
                    started_at=started_at,
                    progress_blueprint=progress_blueprint,
                )
            )
        idle_task = asyncio.create_task(
            self._progress_idle_watchdog(
                get_last_visible_update=lambda: last_visible_update,
                emit_commentary=emit_commentary,
                emit_progress=emit_progress,
                lane_getter=lambda: effective_lane,
                blueprint_getter=lambda: progress_blueprint,
            )
        )

        LOGGER.info("[Brain] Streaming message received. History=%s", len(history))
        try:
            with pending_sensitive_action_capture_context(user_id, conversation_id):
                if defer_stream_until_final_reply:
                    reply = await self.invoke_messages(
                        messages,
                        user_message,
                        lane=effective_lane,
                        source="chat",
                        title="Main conversation",
                        tools_enabled=not has_visual_inputs,
                    )
                else:
                    reply = await self.invoke_messages_stream(
                        messages,
                        user_message,
                        lane=effective_lane,
                        source="chat",
                        title="Main conversation",
                        on_delta=emit_delta,
                        tools_enabled=not has_visual_inputs,
                    )
        except Exception:
            partial_reply = "".join(streamed_fragments)
            if partial_reply.strip():
                await persist_assistant_reply(partial_reply)
            raise
        finally:
            idle_task.cancel()
            try:
                await idle_task
            except asyncio.CancelledError:
                pass
            except Exception as error:
                LOGGER.warning("[Brain] Idle watchdog failed: %s", error)
            if commentary_task is not None:
                commentary_task.cancel()
                try:
                    await commentary_task
                except asyncio.CancelledError:
                    pass
                except Exception as error:
                    LOGGER.warning("[Brain] Slow commentary loop failed: %s", error)

        reply = await self._enforce_turn_closure(
            user_id=user_id,
            conversation_id=conversation_id,
            history=history,
            user_message=user_message,
            reply=reply,
            lane=effective_lane,
            route_reason=route.reason,
            tools_enabled=not has_visual_inputs,
            semantic_memories=semantic_memories,
            base_extra_system_messages=[*pending_follow_up_system_messages, *folder_preview_system_messages],
            confirmed_sensitive_action=confirmed_sensitive_action,
            user_knowledge=user_knowledge,
            document_context=document_context,
            visual_contents=visual_contents,
            allow_pending_action_staging=not confirmed_sensitive_action,
            pending_plan_revision=pending_plan_revision,
            folder_organizer_context_hint=bool(folder_preview_system_messages),
        )
        if defer_stream_until_final_reply and reply.text:
            await emit_delta(reply.text, mark_streaming=False)
        await ensure_user_turn_persisted()
        await persist_assistant_reply(reply.text)
        # Fire-and-forget preference extraction (skip if message contains credentials)
        if self.preference_extractor and not should_skip_vectorization(user_message):
            self.preference_extractor.fire_and_forget(user_id, user_message, reply.text)
        # Title from first substantive exchange (skip greeting-only first turns).
        # Quick clip first; then await fast LLM so the stream "done" carries the final title.
        if self._should_generate_conversation_title(
            conversation_id, user_message, is_first_turn=is_first_turn
        ):
            quick = self._finalize_generated_title("", user_message)
            if quick:
                self.memory.update_conversation_title(conversation_id, quick)
            await self._refine_conversation_title_with_llm(
                conversation_id, user_message, reply.text
            )
        if conversation_id:
            reply.conversation_title = self.memory.get_conversation_title(conversation_id)
        reply.triage_reason = (
            route.reason if effective_lane == route.lane else f"{route.reason}|visual-fallback-fast"
        )
        if not first_delta_seen:
            await emit_progress(
                "progress-done",
                stage="done",
                summary="Process complete. Delivering the final response.",
                blueprint=progress_blueprint,
            )
        return reply
