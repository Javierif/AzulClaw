"""Skill-workflow and Folder Organizer handling for the orchestrator.

Extracted from ``conversation.py`` as a mixin. Covers marketplace skill-workflow
selection/execution, semantic grouping overrides, workflow message localization
and rendering, plan follow-ups, and the Folder Organizer preview/recovery and
capability-contract flows. Relies on orchestrator state and on sibling mixin
methods resolved via the combined MRO.

The ``list_enabled_workflow_runtime_specs`` binding is imported here, so tests
that stub it must patch ``conversation_skills.list_enabled_workflow_runtime_specs``.
"""

import json
import logging
from dataclasses import asdict

from agent_framework import Content

from .api.skill_services import (
    get_skill_workflow_capability_prompt,
    list_enabled_workflow_runtime_specs,
)
from .conversation_helpers import (
    _extract_first_tool_text,
    _folder_organizer_conceptual_taxonomy,
    _folder_organizer_payload_has_planned_moves,
    _format_folder_organizer_preview_text,
    _strip_markdown_emphasis,
    should_skip_vectorization,
)
from .conversation_types import (
    CapabilityContractVerdict,
    ConversationReply,
    FolderOrganizerPreviewContextVerdict,
    PendingActionUserIntentVerdict,
)
from .runtime.pending_action_intent import FOLDER_ORGANIZER_SKILL_ID
from .runtime.skill_workflow_runtime import SkillWorkflowEvent, SkillWorkflowRun, SkillWorkflowRuntime

LOGGER = logging.getLogger(__name__)

# Generic skill-workflow listing protocol contract used by semantic grouping.
# These describe the neutral shape of a workflow list tool's response, not any
# single skill — the skill-specific framing is declared in its manifest.
_WORKFLOW_LISTING_ENTRIES_FIELD = "entries"
_WORKFLOW_LISTING_ITEM_FIELD = "path"
_WORKFLOW_LISTING_ITEM_FILTER_FIELD = "kind"
_WORKFLOW_LISTING_ITEM_FILTER_VALUE = "file"
_MAX_SEMANTIC_GROUPING_ITEMS = 300


class SkillWorkflowMixin:
    """Marketplace skill-workflow execution and Folder Organizer flows."""

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
