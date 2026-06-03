"""Reusable conversation services for the bot and desktop API."""

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict

from agent_framework import Content, Message

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
    PROGRESS_UPDATE_MAX_SECONDS,
    PROGRESS_UPDATE_MIN_SECONDS,
    _extract_first_tool_text,
    _folder_organizer_conceptual_taxonomy,
    _folder_organizer_payload_has_planned_moves,
    _format_folder_organizer_preview_text,
    _is_placeholder_conversation_title,
    _strip_markdown_emphasis,
    _utcnow_iso,
    extract_result_text,
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
from .conversation_titles import TitleMixin
from .conversation_turn_closure import TurnClosureMixin
from .runtime.agent_runtime import AgentRuntimeManager
from .runtime.heartbeat_intent import HeartbeatIntentService
from .runtime.pending_action_intent import (
    FOLDER_ORGANIZER_SKILL_ID,
    PendingSensitiveAction,
    PendingSensitiveActionService,
    pending_sensitive_action_capture_context,
)
from .runtime.semantic_judge import SemanticJudgeService
from .runtime.skill_workflow_runtime import SkillWorkflowEvent, SkillWorkflowRun, SkillWorkflowRuntime
from .api.skill_services import (
    get_skill_workflow_capability_prompt,
    list_enabled_workflow_runtime_specs,
)

LOGGER = logging.getLogger(__name__)
# Generic skill-workflow listing protocol contract used by semantic grouping.
# These describe the neutral shape of a workflow list tool's response, not any
# single skill — the skill-specific framing is declared in its manifest.
_WORKFLOW_LISTING_ENTRIES_FIELD = "entries"
_WORKFLOW_LISTING_ITEM_FIELD = "path"
_WORKFLOW_LISTING_ITEM_FILTER_FIELD = "kind"
_WORKFLOW_LISTING_ITEM_FILTER_VALUE = "file"
_MAX_SEMANTIC_GROUPING_ITEMS = 300


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

    async def _maybe_prepare_folder_organizer_plan_context(
        self,
        *,
        user_message: str,
        user_id: str,
        conversation_id: str | None,
    ) -> list[str]:
        if self._enabled_workflow_spec_for_skill(FOLDER_ORGANIZER_SKILL_ID) is not None:
            return []
        verdict = await self._judge_folder_organizer_request(user_message=user_message)
        if verdict is None or verdict.decision not in {"plan_request", "preview_request"}:
            return []
        mcp_client = getattr(self, "mcp_client", None)
        if mcp_client is None:
            return []
        try:
            result = await mcp_client.call_tool(
                "preview_folder_organization",
                {"recursive": True, "include_moves": False},
                skill_id=FOLDER_ORGANIZER_SKILL_ID,
            )
        except Exception as error:
            capability_prompt = self._skill_capability_prompt(FOLDER_ORGANIZER_SKILL_ID)
            return [
                "Folder Organizer factual preflight failed before drafting the answer.",
                f"Preview error: {error}",
                f"Skill capability contract:\n{capability_prompt}" if capability_prompt else "",
                "Explain the limitation concretely.",
            ]
        preview_text = _format_folder_organizer_preview_text(_extract_first_tool_text(result))
        capability_prompt = self._skill_capability_prompt(FOLDER_ORGANIZER_SKILL_ID)
        guidance = [
            "A real Folder Organizer preview just ran for the configured target folder.",
            "Use the preview result below as the factual basis for your answer.",
            "Respond in the same language as the user.",
            f"Skill capability contract:\n{capability_prompt}" if capability_prompt else "",
            f"Preview result:\n{preview_text}",
        ]
        return ["\n".join(part for part in guidance if part.strip())]

    def _skill_capability_prompt(self, skill_id: str) -> str:
        cache = getattr(self, "_skill_capability_prompt_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            self._skill_capability_prompt_cache = cache
        if skill_id not in cache:
            try:
                cache[skill_id] = get_skill_workflow_capability_prompt(skill_id)
            except Exception as error:
                LOGGER.warning("[Skills] Could not load capability prompt for %s: %s", skill_id, error)
                cache[skill_id] = ""
        return str(cache.get(skill_id, "")).strip()

    def _enabled_workflow_spec_for_skill(self, skill_id: str) -> dict[str, object] | None:
        for spec in list_enabled_workflow_runtime_specs():
            if str(spec.get("skill_id", "")).strip() == skill_id:
                return spec
        return None

    async def _select_marketplace_skill_workflow_spec(self, *, user_message: str) -> dict[str, object] | None:
        specs = list_enabled_workflow_runtime_specs()
        if not specs:
            return None
        service = getattr(self, "semantic_judges", None)
        judge = getattr(service, "judge_skill_workflow_route", None)
        if not callable(judge):
            return None
        try:
            verdict = await judge(user_message=user_message, workflow_specs=specs)
        except Exception as error:
            LOGGER.debug("[Skills] Generic workflow routing failed: %s", error)
            return None
        if not isinstance(verdict, dict) or str(verdict.get("decision", "")).strip() != "run_workflow":
            return None
        selected_skill_id = str(verdict.get("skill_id", "")).strip()
        if not selected_skill_id:
            return None
        return next((spec for spec in specs if str(spec.get("skill_id", "")).strip() == selected_skill_id), None)

    def _workflow_input_payload(self, *, spec: dict[str, object], user_message: str) -> dict[str, object]:
        defaults = spec.get("input_defaults", {})
        payload: dict[str, object] = {}
        if isinstance(defaults, dict):
            for key, value in defaults.items():
                if isinstance(value, dict):
                    payload[str(key)] = dict(value)
                elif isinstance(value, list):
                    payload[str(key)] = list(value)
                else:
                    payload[str(key)] = value
        payload["prompt"] = user_message
        return payload

    async def _invoke_skill_workflow_tool(
        self,
        *,
        skill_id: str,
        tool_name: str,
        arguments: dict[str, object],
    ) -> object:
        mcp_client = getattr(self, "mcp_client", None)
        if mcp_client is None:
            raise ValueError("Skill MCP runtime is not available.")
        result = await mcp_client.call_tool(tool_name, arguments, skill_id=skill_id)
        content = getattr(result, "content", None)
        if isinstance(content, list) and content:
            text = getattr(content[0], "text", None)
            if isinstance(text, str):
                stripped = text.strip()
                if stripped.startswith("{"):
                    try:
                        return json.loads(stripped)
                    except json.JSONDecodeError:
                        return {"text": stripped}
                return {"text": stripped}
        value = getattr(result, "value", None)
        if value is not None:
            return value
        return result

    @staticmethod
    def _spec_semantic_grouping(spec: dict[str, object]) -> dict[str, object]:
        """Returns the skill-declared semantic-grouping framing block (empty when absent)."""
        grouping = spec.get("semantic_grouping", {})
        return grouping if isinstance(grouping, dict) else {}

    @staticmethod
    def _spec_config_flag_enabled(spec: dict[str, object], config_key: str) -> bool:
        """Reads a boolean skill config flag by its declared config key."""
        if not config_key:
            return False
        env = spec.get("env", {})
        if not isinstance(env, dict):
            return False
        env_key = f"AZUL_SKILL_CONFIG_{config_key.upper()}"
        return str(env.get(env_key, "")).strip().lower() == "true"

    @staticmethod
    def _spec_config_int(
        spec: dict[str, object],
        config_key: str,
        *,
        default: int,
        minimum: int,
        maximum: int,
    ) -> int:
        """Reads a clamped integer skill config value by its declared config key."""
        env = spec.get("env", {})
        raw = (
            str(env.get(f"AZUL_SKILL_CONFIG_{config_key.upper()}", "")).strip()
            if config_key and isinstance(env, dict)
            else ""
        )
        try:
            value = int(raw)
        except (TypeError, ValueError):
            return default
        return value if minimum <= value <= maximum else default

    async def _maybe_build_semantic_overrides(
        self,
        *,
        spec: dict[str, object],
        user_id: str,
        conversation_id: str | None,
        user_message: str,
    ) -> dict[str, str] | None:
        """Generic semantic grouping driven by the skill's manifest. Returns None when not applicable.

        The skill declares its framing under ``workflow.semantic_grouping`` (which config flag
        gates it, which list tool to call, the item/group wording and grouping criterion). The
        brain stays skill-agnostic — no skill-specific names live here.
        """
        grouping = self._spec_semantic_grouping(spec)
        if not grouping:
            return None
        if not self._spec_config_flag_enabled(spec, str(grouping.get("enabled_config", "")).strip()):
            return None
        skill_id = str(spec.get("skill_id", "")).strip()
        list_tool = str(grouping.get("list_tool", "")).strip()
        if not skill_id or not list_tool:
            return None
        service = getattr(self, "semantic_judges", None)
        judge = getattr(service, "judge_skill_workflow_semantic_groups", None)
        if not callable(judge):
            return None
        list_arguments: dict[str, object] = {"recursive": True}
        depth_config = str(grouping.get("depth_config", "")).strip()
        depth_argument = str(grouping.get("depth_argument", "")).strip()
        if depth_config and depth_argument:
            list_arguments[depth_argument] = self._spec_config_int(
                spec, depth_config, default=3, minimum=1, maximum=8
            )
        try:
            listing = await self._invoke_skill_workflow_tool(
                skill_id=skill_id,
                tool_name=list_tool,
                arguments=list_arguments,
            )
        except Exception as error:
            LOGGER.debug("[Skills] Semantic listing failed: %s", error)
            return None
        entries = listing.get(_WORKFLOW_LISTING_ENTRIES_FIELD) if isinstance(listing, dict) else None
        if not isinstance(entries, list):
            return None
        items: list[str] = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            if entry.get(_WORKFLOW_LISTING_ITEM_FILTER_FIELD) != _WORKFLOW_LISTING_ITEM_FILTER_VALUE:
                continue
            value = str(entry.get(_WORKFLOW_LISTING_ITEM_FIELD, "")).strip()
            if value and value not in items:
                items.append(value)
            if len(items) >= _MAX_SEMANTIC_GROUPING_ITEMS:
                break
        if not items:
            return None
        skill_name = str(spec.get("skill_name", skill_id)).strip() or skill_id
        try:
            verdict = await judge(
                user_message=user_message,
                items=items,
                item_kind=str(grouping.get("item_kind", "")).strip() or "items",
                group_kind=str(grouping.get("group_kind", "")).strip() or "groups",
                grouping_context=str(grouping.get("context", "")).strip(),
                skill_name=skill_name,
                language_sample=self._workflow_language_sample(
                    user_id=user_id,
                    conversation_id=conversation_id,
                    user_message=user_message,
                ),
            )
        except Exception as error:
            LOGGER.debug("[Skills] Semantic grouping judge failed: %s", error)
            return None
        if not isinstance(verdict, dict) or not isinstance(verdict.get("groups"), dict):
            return None
        allowed = set(items)
        overrides: dict[str, str] = {}
        for raw_path, raw_name in verdict["groups"].items():
            path = str(raw_path).strip()
            name = str(raw_name).strip()
            if path in allowed and name:
                overrides[path] = name
        return overrides or None

    def _workflow_language_sample(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
    ) -> str:
        samples: list[str] = []
        try:
            history = self._load_chat_history(user_id, conversation_id, limit=8)
        except Exception:
            history = []
        for entry in history:
            if not isinstance(entry, dict):
                continue
            if str(entry.get("role", "")).strip() != "user":
                continue
            content = str(entry.get("content", "")).strip()
            if content:
                samples.append(content)
        if user_message.strip():
            samples.append(user_message.strip())
        deduped: list[str] = []
        for sample in samples:
            if sample not in deduped:
                deduped.append(sample)
        return "\n".join(deduped[-6:])

    async def localize_workflow_message(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        source_text: str,
        phase: str,
        skill_name: str = "Skill workflow",
    ) -> str:
        """Rewrites a workflow status message in the conversation's language. Falls back to source_text."""
        source = source_text.strip()
        if not source:
            return source
        language_sample = self._workflow_language_sample(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message="",
        )
        rendered = await self._render_workflow_message(
            user_message=language_sample or source,
            phase=phase,
            source_summary=source,
            skill_name=skill_name,
            language_sample=language_sample,
        )
        if not rendered:
            return source
        plan_markdown = str(rendered.get("plan_markdown", "")).strip()
        next_step = str(rendered.get("next_step", "")).strip()
        if plan_markdown and next_step:
            return f"{plan_markdown}\n\n{next_step}"
        return plan_markdown or source

    async def _render_workflow_message(
        self,
        *,
        user_message: str,
        phase: str,
        source_summary: str,
        skill_name: str,
        language_sample: str = "",
    ) -> dict[str, object] | None:
        service = self._get_semantic_judge_service()
        renderer = getattr(service, "render_skill_workflow_message", None)
        if not callable(renderer):
            return None
        try:
            rendered = await renderer(
                user_message=user_message,
                phase=phase,
                source_summary=source_summary,
                skill_name=skill_name,
                language_sample=language_sample,
            )
        except Exception as error:
            LOGGER.debug("[Skills] Workflow message rendering failed: %s", error)
            return None
        return rendered if isinstance(rendered, dict) else None

    async def _render_skill_workflow_reply(
        self,
        *,
        user_message: str,
        events: list[SkillWorkflowEvent],
        skill_name: str = "Skill workflow",
        language_sample: str = "",
    ) -> tuple[str, str]:
        request_event = next((event for event in events if event.type == "request_info"), None)
        if request_event is not None:
            summary = str(request_event.data.get("summary", "")).strip()
            rendered = await self._render_workflow_message(
                user_message=user_message,
                phase="awaiting_approval",
                source_summary=summary,
                skill_name=skill_name,
                language_sample=language_sample,
            )
            if rendered:
                plan_short = _strip_markdown_emphasis(str(rendered.get("plan_short", "")).strip())
                next_step = str(rendered.get("next_step", "")).strip()
                if plan_short:
                    request_event.data["summary"] = plan_short
                if next_step:
                    return next_step, "approval_required"
            text = (
                f"Approval request from {skill_name}.\n\n"
                f"{summary or 'There are changes ready to review.'}\n\n"
                "Use the approval controls below to apply or cancel."
            )
            return text, "approval_required"
        completed_event = next((event for event in events if event.type == "completed"), None)
        if completed_event is not None:
            summary = str(completed_event.data.get("summary", "")).strip()
            status = str(completed_event.data.get("status", "")).strip()
            phase = "plan_ready" if status == "plan_ready" else "plan_only"
            rendered = await self._render_workflow_message(
                user_message=user_message,
                phase=phase,
                source_summary=summary,
                skill_name=skill_name,
                language_sample=language_sample,
            )
            if rendered:
                plan_markdown = str(rendered.get("plan_markdown", "")).strip() or summary
                next_step = str(rendered.get("next_step", "")).strip()
                text = f"{plan_markdown}\n\n{next_step}".strip() if next_step else plan_markdown
                if text:
                    return text, "final_answer"
            text = f"{skill_name} completed the installed skill workflow."
            if summary:
                text = f"{text}\n\n{summary}"
            elif status:
                text = f"{text}\n\nStatus: {status}"
            return text, "final_answer"
        failed_event = next((event for event in events if event.type == "failed"), None)
        if failed_event is not None:
            error = str(failed_event.data.get("error", "Workflow failed.")).strip()
            return error, "tool_failure"
        return f"{skill_name} started the workflow, but it did not return a final result.", "tool_failure"

    def _latest_executable_workflow_plan(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        skill_id: str,
    ) -> tuple[SkillWorkflowRun, dict[str, object], dict[str, object]] | None:
        workflow_runtime = getattr(self, "skill_workflow_runtime", None)
        if not isinstance(workflow_runtime, SkillWorkflowRuntime):
            return None
        spec = self._enabled_workflow_spec_for_skill(skill_id)
        if spec is None:
            return None
        matching_runs: list[SkillWorkflowRun] = []
        for run in workflow_runtime.store.list_runs():
            if run.skill_id != skill_id:
                continue
            if run.user_id != user_id:
                continue
            if str(run.conversation_id or "").strip() != str(conversation_id or "").strip():
                continue
            if run.status == "waiting_for_human":
                return None
            if run.status != "completed":
                continue
            completed = run.metadata.get("completed") if isinstance(run.metadata, dict) else None
            if not isinstance(completed, dict):
                continue
            organization_plan = completed.get("organization_plan")
            if not isinstance(organization_plan, dict):
                continue
            if not bool(organization_plan.get("executable")):
                continue
            matching_runs.append(run)
        if not matching_runs:
            return None
        latest = max(matching_runs, key=lambda item: item.updated_at or item.created_at)
        completed = latest.metadata.get("completed") if isinstance(latest.metadata, dict) else {}
        if not isinstance(completed, dict):
            return None
        organization_plan = completed.get("organization_plan")
        if not isinstance(organization_plan, dict):
            return None
        return latest, spec, organization_plan

    async def _judge_skill_workflow_plan_follow_up(
        self,
        *,
        user_message: str,
        skill_name: str,
        organization_plan: dict[str, object],
    ) -> PendingActionUserIntentVerdict | None:
        service = self._get_semantic_judge_service()
        if service is None:
            return None
        judge = getattr(service, "judge_skill_workflow_plan_follow_up", None)
        if not callable(judge):
            return None
        parsed = await judge(
            user_message=user_message,
            skill_name=skill_name,
            plan_summary=str(organization_plan.get("summary", "")).strip(),
            organization_plan=organization_plan,
        )
        if not isinstance(parsed, dict):
            return None
        return PendingActionUserIntentVerdict(
            decision=str(parsed.get("decision", "")).strip(),
            reason=str(parsed.get("reason", "")).strip(),
        )

    async def _persist_skill_workflow_failure_reply(
        self,
        *,
        user_id: str,
        user_message: str,
        conversation_id: str | None,
        skill_id: str,
        error: str,
        skill_name: str = "Skill workflow",
    ) -> ConversationReply:
        failed_event = SkillWorkflowEvent(
            type="failed",
            run_id="",
            skill_id=skill_id,
            data={"error": error},
        )
        text, turn_status = await self._render_skill_workflow_reply(
            user_message=user_message,
            events=[failed_event],
            skill_name=skill_name,
            language_sample=self._workflow_language_sample(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=user_message,
            ),
        )
        user_message_id = await self.persist_with_vector_memory(
            user_id,
            "user",
            user_message,
            conversation_id=conversation_id,
        )
        await self.persist_with_vector_memory(
            user_id,
            "assistant",
            text,
            conversation_id=conversation_id,
        )
        if self.preference_extractor and not should_skip_vectorization(user_message):
            self.preference_extractor.fire_and_forget(user_id, user_message, text)
        reply = ConversationReply(
            text=text,
            lane="fast",
            triage_reason="skill-workflow-failed",
            turn_status=turn_status,
            workflow_events=[asdict(failed_event)],
        )
        if conversation_id and user_message_id and hasattr(self.memory, "get_conversation_title"):
            reply.conversation_title = self.memory.get_conversation_title(conversation_id)
        return reply

    async def _start_marketplace_skill_workflow(
        self,
        *,
        spec: dict[str, object],
        user_id: str,
        user_message: str,
        conversation_id: str | None,
        input_payload: dict[str, object] | None = None,
        triage_reason: str = "skill-workflow",
    ) -> ConversationReply:
        skill_id = str(spec.get("skill_id", "")).strip()
        skill_name = str(spec.get("skill_name", skill_id)).strip() or skill_id or "Skill workflow"
        workflow_runtime = getattr(self, "skill_workflow_runtime", None)
        if not isinstance(workflow_runtime, SkillWorkflowRuntime):
            return await self._persist_skill_workflow_failure_reply(
                user_id=user_id,
                user_message=user_message,
                conversation_id=conversation_id,
                skill_id=skill_id,
                skill_name=skill_name,
                error=(
                    f"{skill_name} has an enabled marketplace workflow, but the workflow runtime is not available. "
                    "I stopped instead of using an embedded fallback path."
                ),
            )
        try:
            run, events = await workflow_runtime.start_isolated_workflow(
                spec=spec,
                user_id=user_id,
                conversation_id=conversation_id,
                input_payload=input_payload
                if isinstance(input_payload, dict)
                else self._workflow_input_payload(spec=spec, user_message=user_message),
                tool_invoker=lambda tool_name, arguments: self._invoke_skill_workflow_tool(
                    skill_id=skill_id,
                    tool_name=tool_name,
                    arguments=arguments,
                ),
            )
        except Exception as error:
            LOGGER.warning("[Skills] %s workflow failed; embedded fallback is disabled: %s", skill_id, error)
            return await self._persist_skill_workflow_failure_reply(
                user_id=user_id,
                user_message=user_message,
                conversation_id=conversation_id,
                skill_id=skill_id,
                skill_name=skill_name,
                error=(
                    f"{skill_name} could not run the installed skill workflow. "
                    "I stopped instead of using an embedded fallback path."
                ),
            )
        text, turn_status = await self._render_skill_workflow_reply(
            user_message=user_message,
            events=events,
            skill_name=skill_name,
            language_sample=self._workflow_language_sample(
                user_id=user_id,
                conversation_id=conversation_id,
                user_message=user_message,
            ),
        )
        workflow_events = [asdict(event) for event in events]
        workflow_events.insert(
            0,
            {
                "type": "status",
                "run_id": run.run_id,
                "skill_id": run.skill_id,
                "data": {"status": run.status},
            },
        )
        user_message_id = await self.persist_with_vector_memory(
            user_id,
            "user",
            user_message,
            conversation_id=conversation_id,
        )
        await self.persist_with_vector_memory(
            user_id,
            "assistant",
            text,
            conversation_id=conversation_id,
        )
        if self.preference_extractor and not should_skip_vectorization(user_message):
            self.preference_extractor.fire_and_forget(user_id, user_message, text)
        reply = ConversationReply(
            text=text,
            lane="fast",
            triage_reason=triage_reason,
            turn_status=turn_status,
            workflow_events=workflow_events,
        )
        if conversation_id and user_message_id and hasattr(self.memory, "get_conversation_title"):
            reply.conversation_title = self.memory.get_conversation_title(conversation_id)
        return reply

    async def _try_handle_marketplace_skill_workflow(
        self,
        *,
        user_id: str,
        user_message: str,
        conversation_id: str | None,
    ) -> ConversationReply | None:
        spec = await self._select_marketplace_skill_workflow_spec(user_message=user_message)
        if spec is None:
            return None
        input_payload: dict[str, object] | None = None
        overrides = await self._maybe_build_semantic_overrides(
            spec=spec,
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
        )
        override_argument = str(self._spec_semantic_grouping(spec).get("override_argument", "")).strip()
        if overrides and override_argument:
            input_payload = self._workflow_input_payload(spec=spec, user_message=user_message)
            for argument_key in ("preview_arguments", "execution_arguments"):
                arguments = input_payload.get(argument_key)
                if not isinstance(arguments, dict):
                    arguments = {}
                arguments[override_argument] = dict(overrides)
                input_payload[argument_key] = arguments
        return await self._start_marketplace_skill_workflow(
            spec=spec,
            user_id=user_id,
            user_message=user_message,
            conversation_id=conversation_id,
            input_payload=input_payload,
        )

    async def _try_handle_skill_workflow_plan_follow_up(
        self,
        *,
        user_id: str,
        user_message: str,
        conversation_id: str | None,
    ) -> ConversationReply | None:
        latest = self._latest_executable_workflow_plan(
            user_id=user_id,
            conversation_id=conversation_id,
            skill_id=FOLDER_ORGANIZER_SKILL_ID,
        )
        if latest is None:
            return None
        _previous_run, spec, organization_plan = latest
        skill_id = str(spec.get("skill_id", "")).strip()
        skill_name = str(spec.get("skill_name", skill_id)).strip() or skill_id or "Skill workflow"
        verdict = await self._judge_skill_workflow_plan_follow_up(
            user_message=user_message,
            skill_name=skill_name,
            organization_plan=organization_plan,
        )
        if verdict is None:
            return None
        if verdict.decision == "approve_plan":
            payload = self._workflow_input_payload(spec=spec, user_message=user_message)
            payload["approved_organization_plan"] = organization_plan
            return await self._start_marketplace_skill_workflow(
                spec=spec,
                user_id=user_id,
                user_message=user_message,
                conversation_id=conversation_id,
                input_payload=payload,
                triage_reason="skill-workflow-plan-approved",
            )
        if verdict.decision == "reject_plan":
            text = f"{skill_name} plan was cancelled. No files were moved."
            user_message_id = await self.persist_with_vector_memory(
                user_id,
                "user",
                user_message,
                conversation_id=conversation_id,
            )
            await self.persist_with_vector_memory(
                user_id,
                "assistant",
                text,
                conversation_id=conversation_id,
            )
            reply = ConversationReply(
                text=text,
                lane="fast",
                triage_reason="skill-workflow-plan-rejected",
                turn_status="final_answer",
            )
            if conversation_id and user_message_id and hasattr(self.memory, "get_conversation_title"):
                reply.conversation_title = self.memory.get_conversation_title(conversation_id)
            return reply
        return None

    async def _build_folder_organizer_preview_safe_reply(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str = "",
    ) -> str | None:
        service = self._get_pending_sensitive_action_service()
        if service is None:
            return None
        preview = service.preview_store.get_for_context(user_id, conversation_id)
        if preview is None:
            return None
        payload = preview.preview_payload if isinstance(preview.preview_payload, dict) else {}
        relative_path = str(payload.get("relative_path", preview.relative_path or ".")).strip() or "."
        summary = str(payload.get("summary", preview.summary or "")).strip()
        context_verdict = await self._judge_folder_organizer_preview_context(
            user_message=user_message,
            preview_summary=summary,
            preview_payload=payload,
        )
        if context_verdict is None:
            context_verdict = FolderOrganizerPreviewContextVerdict(
                reply_language="en",
                has_executable_plan=_folder_organizer_payload_has_planned_moves(payload),
                conceptual_plan_requested=False,
                status_summary=summary,
            )
        blocked_items = payload.get("blocked_items") if isinstance(payload.get("blocked_items"), list) else []
        semantic_categories = (
            payload.get("semantic_custom_categories")
            if isinstance(payload.get("semantic_custom_categories"), list)
            else []
        )
        spanish = context_verdict.reply_language == "es"
        lines: list[str] = []
        if spanish:
            lines.append(f"He revisado el estado actual de la carpeta objetivo configurada (`{relative_path}`).")
        else:
            lines.append(f"I reviewed the current state of the configured target folder (`{relative_path}`).")
        status_summary = context_verdict.status_summary or summary
        if status_summary:
            lines.append(status_summary)
        if blocked_items:
            blocked_paths = [
                str(item.get("source_relative_path", "")).strip()
                for item in blocked_items
                if isinstance(item, dict) and str(item.get("source_relative_path", "")).strip()
            ]
            if blocked_paths:
                shown = ", ".join(blocked_paths[:5])
                suffix = " ..." if len(blocked_paths) > 5 else ""
                label = "Elementos bloqueados" if spanish else "Blocked items"
                lines.append(f"{label}: {shown}{suffix}")
        if semantic_categories:
            category_label = "Categorias semanticas revisadas" if spanish else "Reviewed semantic categories"
            lines.append(
                f"{category_label}: "
                + ", ".join(str(item).strip() for item in semantic_categories if str(item).strip())
            )
        semantic_enabled = bool(payload.get("semantic_categorization_enabled")) or str(
            payload.get("categorization_mode", "")
        ).strip().casefold() == "semantic"
        if not context_verdict.has_executable_plan and context_verdict.conceptual_plan_requested:
            lines.append(
                _folder_organizer_conceptual_taxonomy(
                    reply_language=context_verdict.reply_language,
                    semantic_enabled=semantic_enabled,
                )
            )
        if spanish:
            lines.append(
                "Puedo seguir refinando nombres y reglas de agrupacion sobre esta vista revisada, "
                "pero solo pedire aprobacion cuando exista un plan ejecutable concreto."
            )
        else:
            lines.append(
                "If you want, I can keep refining the naming and grouping rules based on this reviewed preview, "
                "but I will only ask for approval when there is a concrete executable plan."
            )
        return "\n\n".join(part for part in lines if part.strip())

    async def _recover_folder_organizer_reply_from_preview(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        history: list[dict],
        user_message: str,
        lane: str,
        semantic_memories: list[dict] | None,
        user_knowledge: list[dict] | None,
        document_context: str = "",
        visual_contents: list[Content] | None = None,
        confirmed_sensitive_action: bool = False,
    ) -> ConversationReply | None:
        service = self._get_pending_sensitive_action_service()
        if service is None:
            return None
        preview = service.preview_store.get_for_context(user_id, conversation_id)
        if preview is None:
            return None
        preview_payload = preview.preview_payload if isinstance(preview.preview_payload, dict) else {}
        preview_text = _format_folder_organizer_preview_text(
            json.dumps(preview_payload, ensure_ascii=False) if preview_payload else preview.summary
        )
        capability_prompt = self._skill_capability_prompt(FOLDER_ORGANIZER_SKILL_ID)
        messages = self.build_agent_messages(
            history,
            semantic_memories or [],
            user_message,
            user_knowledge or [],
            document_context=document_context,
            visual_contents=visual_contents,
            confirmed_sensitive_action=confirmed_sensitive_action,
            extra_system_messages=[
                (
                    "A real Folder Organizer preview already ran for the configured target folder. "
                    "Use that preview as the factual basis of your answer. "
                    "Respond in the same language as the user. "
                    "Do not ask for the configured path again. "
                    "Do not claim unsupported actions such as enabling semantic mode or creating empty skeleton folders. "
                    "If no files are ready to organize, do not ask for approval. "
                    "Instead, clearly explain the current state and provide the best conceptual organization/taxonomy plan you can infer from the user's request."
                ),
                f"Skill capability contract:\n{capability_prompt}" if capability_prompt else "",
                f"Folder Organizer preview facts:\n{preview_text}",
            ],
        )
        return await self.invoke_messages(
            messages,
            user_message,
            lane=lane,
            source="folder-plan-recovery",
            title="Main conversation",
            tools_enabled=False,
        )

    def _folder_organizer_context_active(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
        candidate_reply: str,
    ) -> bool:
        if "folder organizer" in (user_message or "").casefold():
            return True
        if "folder organizer" in (candidate_reply or "").casefold():
            return True
        service = self._get_pending_sensitive_action_service()
        if service is None:
            return False
        if service.get_pending_action_for_context(user_id, conversation_id) is not None:
            return True
        return service.preview_store.get_for_context(user_id, conversation_id) is not None

    async def _judge_folder_organizer_capability_contract(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
        candidate_reply: str,
    ) -> CapabilityContractVerdict | None:
        if not self._folder_organizer_context_active(
            user_id=user_id,
            conversation_id=conversation_id,
            user_message=user_message,
            candidate_reply=candidate_reply,
        ):
            return None
        service = self._get_semantic_judge_service()
        pending_service = self._get_pending_sensitive_action_service()
        if service is None or pending_service is None:
            return None
        preview = pending_service.preview_store.get_for_context(user_id, conversation_id)
        pending = pending_service.get_pending_action_for_context(user_id, conversation_id)
        capability_prompt = self._skill_capability_prompt(FOLDER_ORGANIZER_SKILL_ID)
        capability_facts = {
            "configured_root_already_known": True,
            "supports_preview": True,
            "supports_move_existing_files": True,
            "supports_create_empty_subfolders": False,
            "supports_semantic_runtime_toggle": False,
            "preview_available": preview is not None,
            "preview_summary": preview.summary if preview is not None else "",
            "pending_approval_available": pending is not None,
            "pending_action_kind": pending.action_kind if pending is not None else "",
            "uses_relative_scope_inside_configured_root": True,
            "capability_prompt": capability_prompt,
        }
        parsed = await service.judge_capability_contract(
            capability_name="Folder Organizer",
            user_message=user_message,
            candidate_reply=candidate_reply,
            capability_facts=capability_facts,
        )
        if not isinstance(parsed, dict):
            return None
        return CapabilityContractVerdict(
            decision=str(parsed.get("decision", "")).strip(),
            should_retry=bool(parsed.get("should_retry", False)),
            turn_status=str(parsed.get("turn_status", "")).strip() or "final_answer",
            reason=str(parsed.get("reason", "")).strip(),
            guidance=str(parsed.get("guidance", "")).strip(),
        )

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
