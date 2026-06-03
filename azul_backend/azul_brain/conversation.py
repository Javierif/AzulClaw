"""Reusable conversation services for the bot and desktop API."""

import asyncio
import json
import logging
import os
import time
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from pathlib import Path

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
    _coerce_semantic_bool,
    _extract_first_tool_text,
    _folder_organizer_conceptual_taxonomy,
    _folder_organizer_payload_has_planned_moves,
    _format_folder_organizer_preview_text,
    _is_placeholder_conversation_title,
    _map_verdict_to_turn_status,
    _random_progress_delay_seconds,
    _strip_machine_pending_blocks,
    _strip_markdown_emphasis,
    _utcnow_iso,
    extract_result_text,
    should_skip_vectorization,
)
from .attachments import (
    AttachmentError,
    build_attachment_context,
    build_vision_capability_error,
    render_pdf_pages_as_data_uris,
)
from .cortex.fast.commentary import (
    build_commentary,
    build_progress_snapshot,
    normalize_fast_visible_commentary,
    normalize_fast_visible_plan,
    prompt_for_fast_visible_commentary,
    prompt_for_fast_visible_plan,
)
from .cortex.fast.triage import TriageDecision, classify_message
from .conversation_memory import MemoryMixin
from .runtime.agent_runtime import AgentRuntimeManager
from .runtime.approval_protocol import contains_pending_action_block
from .runtime.heartbeat_intent import HeartbeatIntentService
from .runtime.pending_action_intent import (
    FOLDER_ORGANIZER_SKILL_ID,
    PENDING_SENSITIVE_ACTION_APPROVAL_PROMPT,
    PENDING_SENSITIVE_ACTION_CARD_ONLY_CONFIRMATION,
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
_TURN_CLOSURE_ALLOWED_STATUSES = {"final_answer", "blocking_question", "action_pending", "tool_failure"}


class ConversationOrchestrator(MemoryMixin):
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

    def _should_generate_conversation_title(
        self,
        conversation_id: str | None,
        user_message: str,
        *,
        is_first_turn: bool,
    ) -> bool:
        """Sidebar title once: first substantive turn, or next substantive turn if still placeholder."""
        if not conversation_id:
            return False
        if is_first_turn:
            return True
        current = self.memory.get_conversation_title(conversation_id)
        return _is_placeholder_conversation_title(current)

    def _finalize_generated_title(self, title: str, source_message: str) -> str:
        """Falls back to the user's message only when the model returned nothing usable."""
        cleaned = (title or "").strip().strip('"').strip()
        if cleaned:
            return cleaned
        fallback = source_message[:60].strip()
        return fallback

    async def _refine_conversation_title_with_llm(
        self,
        conversation_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """LLM sidebar title from the first exchange (question + answer excerpt)."""
        deployment = os.environ.get("AZURE_OPENAI_FAST_DEPLOYMENT", "").strip()
        if not deployment:
            return

        ans = (assistant_reply or "").strip()
        if len(ans) > 1200:
            ans = ans[:1200] + "â€¦"

        title_prompt = (
            "You name chat threads for a sidebar list.\n\n"
            f"User asked:\n\"\"\"{user_message[:500]}\"\"\"\n\n"
            f"Assistant answered (excerpt):\n\"\"\"{ans}\"\"\"\n\n"
            "Write ONE short title (4â€“7 words) summarizing the topic or outcome of this exchange. "
            "Prefer concrete subject matter (e.g. weather in Barcelona, Python error) over generic words. "
            "Do not start with Hello, Hi, or Hey. "
            "Do not use 'Conversation Starter', 'New chat', 'Chat', or 'Main conversation'. "
            "Reply with the title only, no quotes."
        )
        try:
            result = await self.runtime_manager.execute_messages(
                messages=[Message(role="user", contents=title_prompt)],
                lane="fast",
                title="Conversation title",
                source="conversation-title",
                kind="agent-run",
                tools_enabled=False,
                instructions="Return only a concise conversation title.",
            )
            raw = (result.text or "").strip()
            title = self._finalize_generated_title(raw, user_message)
            if title:
                self.memory.update_conversation_title(conversation_id, title)
                return
        except Exception as error:
            LOGGER.debug("[Brain] Title generation failed: %s", error)

    def _reply_contains_pending_action_block(self, text: str) -> bool:
        return contains_pending_action_block(text)

    def _looks_like_blocking_question(self, text: str) -> bool:
        candidate = (text or "").strip()
        return "?" in candidate or "Â¿" in candidate

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

    async def invoke_messages(
        self,
        messages: list[Message],
        user_message: str,
        *,
        lane: str,
        source: str,
        title: str,
        tools_enabled: bool = True,
    ) -> ConversationReply:
        """Invokes the agent with structured messages and falls back on content filter errors."""
        try:
            result = await self.runtime_manager.execute_messages(
                messages=messages,
                lane=lane,
                title=title,
                source=source,
                kind="agent-run",
                tools_enabled=tools_enabled,
            )
            return ConversationReply(
                text=result.text,
                model_id=result.model.id if result.model else "",
                model_label=result.model.label if result.model else "",
                process_id=result.process_id,
                attempt_count=result.attempt_count,
                skipped_models=result.skipped_models,
                failed_attempts=result.failed_attempts,
                lane=lane,
                turn_status="final_answer",
            )
        except Exception as error:
            error_text = str(error)
            if "content_filter" in error_text or "ResponsibleAIPolicyViolation" in error_text:
                fallback = self._fallback_for_filtered_prompt(user_message)
                if fallback:
                    LOGGER.warning("[Brain] Azure filtered the prompt. Using local fallback.")
                    return ConversationReply(text=fallback, lane=lane, turn_status="final_answer")
            return ConversationReply(
                text=(
                    "Could not execute the cognitive layer yet. "
                    "Check Azure OpenAI configuration and Microsoft Entra authentication.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,
                turn_status="tool_failure",
            )

    async def invoke_messages_stream(
        self,
        messages: list[Message],
        user_message: str,
        *,
        lane: str,
        source: str,
        title: str,
        on_delta: Callable[[str], Awaitable[None]],
        tools_enabled: bool = True,
    ) -> ConversationReply:
        """Invokes the agent and emits deltas when the runtime uses streaming."""
        try:
            result = await self.runtime_manager.execute_messages_stream(
                messages=messages,
                lane=lane,
                title=title,
                source=source,
                kind="agent-run",
                on_delta=on_delta,
                tools_enabled=tools_enabled,
            )
            return ConversationReply(
                text=result.text,
                model_id=result.model.id if result.model else "",
                model_label=result.model.label if result.model else "",
                process_id=result.process_id,
                attempt_count=result.attempt_count,
                skipped_models=result.skipped_models,
                failed_attempts=result.failed_attempts,
                lane=lane,
                turn_status="final_answer",
            )
        except Exception as error:
            error_text = str(error)
            if "content_filter" in error_text or "ResponsibleAIPolicyViolation" in error_text:
                fallback = self._fallback_for_filtered_prompt(user_message)
                if fallback:
                    LOGGER.warning("[Brain] Azure filtered the prompt. Using local fallback.")
                    await on_delta(fallback)
                    return ConversationReply(text=fallback, lane=lane, turn_status="final_answer")
            return ConversationReply(
                text=(
                    "Could not execute the cognitive layer yet. "
                    "Check Azure OpenAI configuration and Microsoft Entra authentication.\n"
                    f"Technical detail: {error}"
                ),
                lane=lane,
                turn_status="tool_failure",
            )

    async def generate_fast_visible_plan(self, user_message: str, *, reason: str) -> tuple[str, dict]:
        """Asks the fast brain for the first visible narration and a summarised plan."""
        prompt_messages = [
            Message(role=item["role"], contents=item["text"])
            for item in prompt_for_fast_visible_plan(user_message, reason=reason)
        ]
        try:
            reply = await self.invoke_messages(
                prompt_messages,
                user_message,
                lane="fast",
                source="commentary",
                title="Visible narration",
            )
            return normalize_fast_visible_plan(reply.text, user_message=user_message, reason=reason)
        except Exception as error:
            LOGGER.warning("[Brain] Fast visible plan failed: %s", error)
            fallback_commentary = build_commentary(user_message, reason=reason, lane="slow")
            fallback_progress = build_progress_snapshot(
                user_message,
                reason=reason,
                lane="slow",
                stage="delegated",
                summary=fallback_commentary,
            )
            fallback_blueprint = {
                "title": fallback_progress["title"],
                "badge": fallback_progress["badge"],
                "summary": {"thinking": fallback_progress["summary"]},
                "phases": [
                    {
                        "id": phase["id"],
                        "label": phase["label"],
                        "steps": [step["label"] for step in phase["steps"]],
                    }
                    for phase in fallback_progress["phases"]
                ],
            }
            return fallback_commentary, fallback_blueprint

    async def generate_fast_visible_commentary(
        self,
        user_message: str,
        *,
        reason: str,
        lane: str,
    ) -> str:
        """Asks the fast brain for the first visible bubble for any route."""
        prompt_messages = [
            Message(role=item["role"], contents=item["text"])
            for item in prompt_for_fast_visible_commentary(user_message, reason=reason, lane=lane)
        ]
        try:
            reply = await self.invoke_messages(
                prompt_messages,
                user_message,
                lane="fast",
                source="commentary",
                title="First visible bubble",
            )
            return normalize_fast_visible_commentary(
                reply.text,
                user_message=user_message,
                reason=reason,
                lane=lane,
            )
        except Exception as error:
            LOGGER.warning("[Brain] Fast visible commentary failed: %s", error)
            return build_commentary(user_message, reason=reason, lane=lane)

    def _fallback_for_filtered_prompt(self, user_message: str) -> str | None:
        """Returns a safe response if Azure filters a simple request."""
        normalized = (user_message or "").strip().lower()
        if normalized in {"hola", "buenas", "hey", "hello", "holi"}:
            return "Hi. I'm active and ready to help."
        if normalized in {"gracias", "muchas gracias"}:
            return "You're welcome."
        if normalized in {"que tal", "como estas", "cÃ³mo estÃ¡s"}:
            return "I'm operational and ready to work with you."
        return None

    def build_agent_messages(
        self,
        history: list[dict],
        semantic_memories: list[dict],
        user_message: str,
        user_knowledge: list[dict] | None = None,
        document_context: str = "",
        visual_contents: list[Content] | None = None,
        confirmed_sensitive_action: bool = False,
        extra_system_messages: list[str] | None = None,
    ) -> list[Message]:
        """Converts history and context into real messages for the framework."""
        messages: list[Message] = []

        # Inject user knowledge split by source so the LLM knows which takes precedence
        if user_knowledge:
            explicit = [k for k in user_knowledge if k.get("source") == "extractor"]
            baseline = [k for k in user_knowledge if k.get("source") == "hatching-profile"]

            sections: list[str] = []
            if explicit:
                lines = "\n".join(f"- {k['content']}" for k in explicit if k.get("content"))
                sections.append("What the user has told you directly (higher priority):\n" + lines)
            if baseline:
                lines = "\n".join(f"- {k['content']}" for k in baseline if k.get("content"))
                sections.append("Initial setup preferences (use as baseline, explicit statements above override these):\n" + lines)

            if sections:
                messages.append(
                    Message(
                        role="system",
                        contents="\n\n".join(sections),
                    )
                )

        if confirmed_sensitive_action:
            messages.append(
                Message(
                    role="system",
                    contents=(
                        "The user is explicitly confirming a previously proposed sensitive action. "
                        "Do not ask for confirmation again unless the scope has changed. "
                        "Execute the relevant tool now and report the concrete result. "
                        "Never claim a filesystem action was executed unless a tool result confirms it. "
                        "If the previous plan needs to be rebuilt, do so and then execute it in the same turn."
                    ),
                )
            )

        for system_message in extra_system_messages or []:
            text = str(system_message or "").strip()
            if text:
                messages.append(Message(role="system", contents=text))

        for item in history:
            role = item.get("role", "user")
            if role not in {"user", "assistant"}:
                continue
            content = _strip_machine_pending_blocks(str(item.get("content", "")).strip())
            if content:
                messages.append(Message(role=role, contents=content))

        if semantic_memories:
            memory_lines: list[str] = []
            for memory in semantic_memories:
                content = str(memory.get("content", "")).strip()
                if not content:
                    continue
                source = str(memory.get("source", "chat"))
                similarity = float(memory.get("similarity", 0.0))
                hybrid_score = memory.get("hybrid_score")
                if hybrid_score is not None:
                    memory_lines.append(f"- ({source}, hybrid={hybrid_score:.4f}) {content}")
                else:
                    memory_lines.append(f"- ({source}, sim={similarity:.2f}) {content}")

            if memory_lines:
                messages.append(
                    Message(
                        role="assistant",
                        contents="Retrieved context for this conversation:\n" + "\n".join(memory_lines),
                    )
                )

        if document_context.strip():
            messages.append(
                Message(
                    role="assistant",
                    contents="Document context for this conversation:\n" + document_context.strip(),
                )
            )

        user_contents: list[Content] = [Content.from_text(user_message)]
        if visual_contents:
            user_contents.extend(visual_contents)
        messages.append(Message(role="user", contents=user_contents))
        return messages

    def _supports_visual_inputs(self, lane: str) -> bool:
        checker = getattr(self.runtime_manager, "supports_multimodal_input", None)
        if callable(checker):
            return bool(checker(lane))
        return False

    def _resolve_visual_lane(self, preferred_lane: str) -> str:
        """Chooses a lane that can accept visual inputs, preferring fast as fallback."""
        if self._supports_visual_inputs(preferred_lane):
            return preferred_lane
        if preferred_lane != "fast" and self._supports_visual_inputs("fast"):
            LOGGER.info(
                "[Brain] Falling back from lane=%s to lane=fast for visual input support.",
                preferred_lane,
            )
            return "fast"
        return preferred_lane

    def _load_attachment(self, attachment_id: str, user_id: str) -> dict | None:
        loader = getattr(self.memory, "get_attachment", None)
        if not callable(loader):
            return None
        return loader(attachment_id, user_id)

    def _prepare_attachment_inputs(
        self,
        *,
        user_id: str,
        conversation_id: str | None,
        user_message: str,
        lane: str,
        attachment_ids: list[str] | None,
    ) -> tuple[str, list[Content], bool, str]:
        requested_ids = [str(item).strip() for item in (attachment_ids or []) if str(item).strip()]
        current_attachments = [
            attachment
            for attachment in (self._load_attachment(attachment_id, user_id) for attachment_id in requested_ids)
            if attachment is not None
        ]
        if requested_ids and len(current_attachments) != len(requested_ids):
            missing = sorted(set(requested_ids) - {str(item["id"]) for item in current_attachments})
            raise AttachmentError(f"Attachment not found: {', '.join(missing)}")

        conversation_attachments: list[dict] = []
        if conversation_id and hasattr(self.memory, "list_conversation_attachments"):
            conversation_attachments = self.memory.list_conversation_attachments(conversation_id, user_id)
        all_attachments = [
            *conversation_attachments,
            *[
                item
                for item in current_attachments
                if str(item["id"]) not in {str(existing.get("id")) for existing in conversation_attachments}
            ],
        ]

        document_context, _ = build_attachment_context(all_attachments, user_message)

        visual_candidates = [
            item
            for item in current_attachments
            if item.get("kind") == "image" or item.get("extraction_status") == "low_text_quality"
        ]
        if not visual_candidates and conversation_id and hasattr(self.memory, "list_recent_visual_attachments"):
            visual_candidates = self.memory.list_recent_visual_attachments(conversation_id, user_id, limit=2)

        if not visual_candidates:
            return document_context, [], False, lane

        selected_lane = self._resolve_visual_lane(lane)
        if not self._supports_visual_inputs(selected_lane):
            raise AttachmentError(build_vision_capability_error())

        visual_contents: list[Content] = []
        for attachment in visual_candidates:
            mime_type = str(attachment.get("mime_type", "")).lower()
            storage_path = Path(str(attachment.get("storage_path", "")))
            if not storage_path.exists():
                continue
            if mime_type.startswith("image/"):
                visual_contents.append(Content.from_data(storage_path.read_bytes(), mime_type))
                continue
            if mime_type == "application/pdf":
                for data_uri in render_pdf_pages_as_data_uris(storage_path):
                    visual_contents.append(Content.from_uri(data_uri, media_type="image/png"))

        return document_context, visual_contents, bool(visual_contents), selected_lane

    def resolve_route(self, user_message: str, requested_lane: str = "auto") -> TriageDecision:
        """Cheap deterministic fallback route."""
        normalized = (requested_lane or "").strip().lower()
        if normalized in {"fast", "slow"}:
            return TriageDecision(lane=normalized, reason="explicit")
        if normalized == "auto":
            return classify_message(user_message)

        default_lane = self.runtime_manager.load_settings().default_lane
        if default_lane == "auto":
            return classify_message(user_message)
        return TriageDecision(lane=default_lane, reason="runtime-default")

    def resolve_lane(self, user_message: str, requested_lane: str = "auto") -> str:
        """Backwards compatibility helper to get only the lane."""
        return self.resolve_route(user_message, requested_lane).lane

    async def resolve_route_async(self, user_message: str, requested_lane: str = "auto") -> TriageDecision:
        """Determines the effective cognitive route for this turn."""
        normalized = (requested_lane or "").strip().lower()
        if normalized in {"fast", "slow"}:
            return TriageDecision(lane=normalized, reason="explicit")
        if normalized != "auto":
            default_lane = self.runtime_manager.load_settings().default_lane
            if default_lane != "auto":
                return TriageDecision(lane=default_lane, reason="runtime-default")
        semantic_route = await self._judge_route_semantically(user_message)
        if semantic_route is not None:
            return semantic_route
        return self.resolve_route(user_message, requested_lane)

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

    async def _slow_commentary_loop(
        self,
        user_message: str,
        *,
        reason: str,
        on_commentary: Callable[[str], Awaitable[None]],
        on_progress: Callable[..., Awaitable[None]] | None = None,
        started_at: str,
        progress_blueprint: dict | None = None,
    ) -> None:
        """Emits lightweight feedback while the slow brain is still working."""
        updates = [
            "Still thinking this through to give you a thorough answer.",
            "Structuring the response and making sure the approach makes sense.",
            "Almost there. Closing the key points before responding.",
        ]
        index = 0
        while True:
            await asyncio.sleep(_random_progress_delay_seconds())
            commentary = updates[index % len(updates)]
            await on_commentary(commentary)
            if on_progress is not None:
                stage = "thinking" if index < 2 else "finalizing"
                await on_progress(
                    "progress-update",
                    stage=stage,
                    summary=commentary,
                    tick=index,
                    blueprint=progress_blueprint,
                )
            index += 1

    async def _progress_idle_watchdog(
        self,
        get_last_visible_update: Callable[[], float],
        emit_commentary: Callable[[str], Awaitable[None]],
        emit_progress: Callable[..., Awaitable[None]],
        lane_getter: Callable[[], str],
        blueprint_getter: Callable[[], dict | None],
    ) -> None:
        """Emits a reassurance update when the user has not seen progress for a while."""
        idle_commentary = "Still processing this; I haven't stalled."
        next_idle_after = _random_progress_delay_seconds()
        while True:
            await asyncio.sleep(1.0)
            if (time.monotonic() - get_last_visible_update()) < next_idle_after:
                continue
            lane = lane_getter()
            blueprint = blueprint_getter()
            await emit_commentary(idle_commentary)
            await emit_progress(
                "progress-idle",
                stage="thinking" if lane == "slow" else "delegated",
                summary=idle_commentary,
                blueprint=blueprint,
            )
            next_idle_after = _random_progress_delay_seconds()

