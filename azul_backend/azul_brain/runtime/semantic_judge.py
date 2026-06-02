"""Reusable semantic judges backed by the fast lane."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any

from agent_framework import Message

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class SemanticJudgeRequest:
    """Low-variance classification task executed by the fast lane."""

    title: str
    source: str
    prompt: str
    instructions: str


class SemanticJudgeService:
    """Centralises small semantic routing/classification tasks for the project."""

    def __init__(self, runtime_manager: Any):
        self.runtime_manager = runtime_manager

    async def judge_json(self, request: SemanticJudgeRequest) -> dict[str, Any] | None:
        """Runs a fast semantic judge that must return a JSON object."""
        try:
            result = await self.runtime_manager.execute_messages(
                messages=[Message(role="user", contents=request.prompt)],
                lane="fast",
                title=request.title,
                source=request.source,
                kind="agent-run",
                tools_enabled=False,
                instructions=request.instructions,
            )
        except Exception as error:
            LOGGER.debug("[SemanticJudge] %s failed: %s", request.source, error)
            return None
        return self._extract_json_object(getattr(result, "text", ""))

    async def judge_structured_output(
        self,
        *,
        title: str,
        source: str,
        prompt: str,
        response_format: Any,
    ) -> Any | None:
        """Runs a fast semantic judge using native structured output when supported."""
        try:
            result = await self.runtime_manager.execute_messages(
                messages=[Message(role="user", contents=prompt)],
                lane="fast",
                title=title,
                source=source,
                kind="agent-run",
                tools_enabled=False,
                instructions=None,
                response_format=response_format,
            )
        except Exception as error:
            LOGGER.debug("[SemanticJudge] %s failed: %s", source, error)
            return None
        return getattr(result, "value", None)

    async def judge_route(self, *, user_message: str) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Route judge",
                source="route-judge",
                prompt=(
                    "You classify the internal processing lane for a user request.\n"
                    "Return JSON only with keys: lane, reason.\n\n"
                    "Allowed lane values:\n"
                    "- fast\n"
                    "- slow\n\n"
                    "Use fast for lightweight replies, greetings, direct questions, and simple follow-ups.\n"
                    "Use slow when the request needs deeper analysis, tools, multi-step planning, file or workspace reasoning, or broad technical work.\n"
                    "Reason must be one of: empty, short-utterance, short-question, long-request, complex-request, deep-analysis-request, default-fast.\n\n"
                    f"User message:\n{user_message}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"lane":"fast|slow","reason":"empty|short-utterance|short-question|long-request|complex-request|deep-analysis-request|default-fast"}'
                ),
            )
        )

    async def judge_turn_closure(
        self,
        *,
        user_message: str,
        candidate_reply: str,
        history_lines: list[str],
        lane: str,
        triage_reason: str,
        facts: dict[str, object],
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Turn closure judge",
                source="turn-closure-judge",
                prompt=(
                    "You are a turn-closure validator for a chat orchestrator.\n"
                    "Classify whether the candidate assistant reply is a valid end to this turn.\n"
                    "Return JSON only with keys: turn_status, should_retry, reason.\n\n"
                    "Allowed turn_status values:\n"
                    "- final_answer\n"
                    "- blocking_question\n"
                    "- action_pending\n"
                    "- tool_failure\n"
                    "- incomplete_promise\n\n"
                    "Use incomplete_promise when the assistant says it will do work later, says it will prepare or inspect something, "
                    "or otherwise defers the task without delivering a concrete result, a real blocking question, a pending action, or a real failure. "
                    "This also includes replies that say they will execute, apply, move, run, or reorganize something now or next, but do not report any actual tool result in this turn.\n"
                    "Use blocking_question only when the reply clearly asks the user for necessary information that blocks progress now.\n"
                    "Use action_pending only when the reply requires structured approval before proceeding.\n"
                    "Use tool_failure only when the reply reports a concrete limitation or execution failure.\n"
                    "Use final_answer when the reply actually delivers the requested outcome for this turn.\n\n"
                    f"Turn facts:\n{json.dumps(facts, ensure_ascii=False)}\n\n"
                    f"Lane: {lane}\n"
                    f"Triage reason: {triage_reason}\n\n"
                    f"Recent history:\n{chr(10).join(history_lines) or '(none)'}\n\n"
                    f"User message:\n{user_message}\n\n"
                    f"Candidate assistant reply:\n{candidate_reply}\n"
                ),
                instructions=(
                    "Return JSON only. Do not explain. "
                    'Schema: {"turn_status":"final_answer|blocking_question|action_pending|tool_failure|incomplete_promise",'
                    '"should_retry":true|false,"reason":"short reason"}'
                ),
            )
        )

    async def judge_pending_action_stage(
        self,
        *,
        user_message: str,
        candidate_reply: str,
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Pending action stage judge",
                source="pending-action-stage-judge",
                prompt=(
                    "You classify whether an assistant reply is ready to show a structured approval card for a sensitive action.\n"
                    "Return JSON only with keys: decision, action_kind, title, summary, reason.\n\n"
                    "Allowed decision values:\n"
                    "- approval_ready\n"
                    "- blocking_input\n"
                    "- no_sensitive_action\n\n"
                    "Use approval_ready only when the assistant is asking the user to approve execution now.\n"
                    "Use approval_ready when the assistant already presents a finished plan or reviewed preview and asks the user to confirm, approve, or apply the execution now, even if it is phrased as a question.\n"
                    "Do not use approval_ready when the assistant is only asking permission to inspect, scan, preview, analyze, or prepare a plan.\n"
                    "Use blocking_input when the reply still needs information, clarification, or permission before a plan preview or execution can be prepared.\n"
                    "Use no_sensitive_action when the reply does not stage an approval at all.\n"
                    "Use action_kind = folder_organizer when the reply is about applying folder organization changes.\n"
                    "Use action_kind = generic for other sensitive approvals. Otherwise use none.\n\n"
                    f"User message:\n{user_message}\n\n"
                    f"Assistant reply:\n{candidate_reply}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"decision":"approval_ready|blocking_input|no_sensitive_action",'
                    '"action_kind":"folder_organizer|generic|none","title":"optional short title",'
                    '"summary":"optional short summary","reason":"short reason"}'
                ),
            )
        )

    async def judge_pending_action_user_intent(
        self,
        *,
        user_message: str,
        pending_title: str,
        pending_summary: str,
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Pending action intent judge",
                source="pending-action-intent-judge",
                prompt=(
                    "You classify whether a user message is trying to approve or reject an existing sensitive action card.\n"
                    "Return JSON only with keys: decision, reason.\n\n"
                    "Allowed decision values:\n"
                    "- approve\n"
                    "- reject\n"
                    "- other\n\n"
                    "Use approve only when the user is clearly trying to accept or execute the pending action.\n"
                    "Use reject only when the user is clearly trying to cancel or decline the pending action.\n"
                    "Use other for revisions, questions, new requests, or anything ambiguous.\n\n"
                    f"Pending action title: {pending_title}\n"
                    f"Pending action summary: {pending_summary}\n"
                    f"User message:\n{user_message}\n"
                ),
                instructions='Return JSON only. Schema: {"decision":"approve|reject|other","reason":"short reason"}',
            )
        )

    async def judge_heartbeat_route(
        self,
        *,
        user_message: str,
        has_pending: bool,
        response_format: Any,
    ) -> Any | None:
        return await self.judge_structured_output(
            title="Heartbeat semantic routing",
            source="heartbeat-router",
            prompt=(
                "You are a semantic router for AzulClaw chat turns. "
                "Select exactly one route: create_heartbeat, confirm_pending, cancel_pending, or none. "
                "Use create_heartbeat only when the user is asking to create a recurring automation, heartbeat, or scheduled task. "
                "For create_heartbeat, return a draft with a short name, the action prompt, a standard 5-field Linux cron "
                "expression evaluated in the machine's local timezone, and lane='fast' unless the user explicitly asks for deep reasoning. "
                "Use confirm_pending or cancel_pending only when has_pending is true and the user is clearly confirming or cancelling "
                "the pending heartbeat draft. If the schedule is ambiguous or cannot be represented as cron, still use create_heartbeat "
                "but leave draft.cron_expression empty.\n\n"
                f"Input:\n{json.dumps({'has_pending': has_pending, 'message': user_message}, ensure_ascii=False)}"
            ),
            response_format=response_format,
        )

    async def judge_folder_plan_follow_up(
        self,
        *,
        user_message: str,
        pending_title: str,
        pending_summary: str,
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Folder plan follow-up judge",
                source="folder-plan-follow-up-judge",
                prompt=(
                    "You classify whether a user message is revising an existing Folder Organizer plan or moving on.\n"
                    "Return JSON only with keys: decision, reason.\n\n"
                    "Allowed decision values:\n"
                    "- revise_plan\n"
                    "- move_on\n"
                    "- other\n\n"
                    "Use revise_plan when the user is changing categories, scope, naming, structure, folders, or organization rules for the current folder plan.\n"
                    "Use move_on when the user is no longer working on that plan and has switched topics.\n"
                    "Use other when the message is too ambiguous to trust.\n\n"
                    f"Pending action title: {pending_title}\n"
                    f"Pending action summary: {pending_summary}\n"
                    f"User message:\n{user_message}\n"
                ),
                instructions='Return JSON only. Schema: {"decision":"revise_plan|move_on|other","reason":"short reason"}',
            )
        )

    async def judge_folder_organizer_request(
        self,
        *,
        user_message: str,
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Folder Organizer request judge",
                source="folder-organizer-request-judge",
                prompt=(
                    "You classify whether the user is asking to inspect, plan, preview, or apply work with the Folder Organizer capability.\n"
                    "Return JSON only with keys: decision, reason.\n\n"
                    "Allowed decision values:\n"
                    "- plan_request\n"
                    "- preview_request\n"
                    "- apply_request\n"
                    "- other\n\n"
                    "Use plan_request when the user wants an organization plan based on the configured folder contents.\n"
                    "Use preview_request when the user explicitly asks to scan, inspect, preview, dry-run, or enumerate proposed changes.\n"
                    "Use apply_request when the user is clearly asking to execute or apply an already reviewed folder organization plan.\n"
                    "Use other for unrelated requests.\n\n"
                    f"User message:\n{user_message}\n"
                ),
                instructions='Return JSON only. Schema: {"decision":"plan_request|preview_request|apply_request|other","reason":"short reason"}',
            )
        )

    async def judge_skill_workflow_route(
        self,
        *,
        user_message: str,
        workflow_specs: list[dict[str, Any]],
    ) -> dict[str, Any] | None:
        candidates: list[dict[str, Any]] = []
        for spec in workflow_specs:
            activation = spec.get("activation", {}) if isinstance(spec.get("activation", {}), dict) else {}
            capabilities = spec.get("capabilities", []) if isinstance(spec.get("capabilities", []), list) else []
            candidates.append(
                {
                    "skill_id": str(spec.get("skill_id", "")).strip(),
                    "skill_name": str(spec.get("skill_name", "")).strip(),
                    "description": str(spec.get("description", "")).strip(),
                    "capabilities": capabilities,
                    "workflow_intents": activation.get("workflow_intents", [])
                    if isinstance(activation.get("workflow_intents", []), list)
                    else [],
                    "workflow_examples": activation.get("workflow_examples", [])
                    if isinstance(activation.get("workflow_examples", []), list)
                    else [],
                }
            )

        return await self.judge_json(
            SemanticJudgeRequest(
                title="Skill workflow router",
                source="skill-workflow-router",
                prompt=(
                    "You are a semantic router for installed AzulClaw marketplace skill workflows.\n"
                    "Choose exactly one workflow only when the user is asking for that installed skill capability to run now.\n"
                    "A workflow may already be bound to a pre-configured target (such as a specific folder), so the user does not have to name that target explicitly. "
                    "Treat vague references like 'that folder', 'the folder', 'my files', or 'my desktop' as referring to the workflow's configured target when the requested action matches its intents.\n"
                    "Do not choose a workflow for unrelated chat, generic advice, or when the user is only asking about the skill conceptually.\n"
                    "Return JSON only with keys: decision, skill_id, reason.\n\n"
                    "Allowed decision values:\n"
                    "- run_workflow\n"
                    "- none\n\n"
                    f"Installed workflow candidates:\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
                    f"User message:\n{user_message}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"decision":"run_workflow|none","skill_id":"selected skill id or empty","reason":"short reason"}'
                ),
            )
        )

    async def judge_folder_organizer_preview_context(
        self,
        *,
        user_message: str,
        preview_summary: str,
        preview_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Folder Organizer preview context judge",
                source="folder-organizer-preview-context-judge",
                prompt=(
                    "You classify the current Folder Organizer preview context for a fallback response.\n"
                    "Use semantic reasoning over the user request and the structured preview payload. Do not rely on literal keyword matching.\n"
                    "Return JSON only with keys: reply_language, has_executable_plan, conceptual_plan_requested, status_summary, reason.\n\n"
                    "reply_language must be the best response language for the user: es, en, or other.\n"
                    "has_executable_plan is true only when the preview indicates existing files can actually be moved now. "
                    "It may be true even if raw moves are omitted for batching, when the preview summary or metadata clearly says files are ready.\n"
                    "conceptual_plan_requested is true when the user asks for a naming taxonomy, grouping strategy, or organization plan beyond merely reporting the scan.\n"
                    "status_summary should be one short user-facing sentence in reply_language that accurately summarizes the preview state.\n\n"
                    f"User message:\n{user_message}\n\n"
                    f"Preview summary:\n{preview_summary}\n\n"
                    f"Preview payload:\n{json.dumps(preview_payload, ensure_ascii=False)}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"reply_language":"es|en|other","has_executable_plan":true|false,'
                    '"conceptual_plan_requested":true|false,"status_summary":"short sentence","reason":"short reason"}'
                ),
            )
        )

    async def render_skill_workflow_message(
        self,
        *,
        user_message: str,
        phase: str,
        source_summary: str,
        skill_name: str,
        language_sample: str = "",
    ) -> dict[str, Any] | None:
        """Rewrites an internal workflow summary into a localized, well-formatted user message."""
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Skill workflow message renderer",
                source="skill-workflow-message-renderer",
                prompt=(
                    "You write the user-facing chat text for an installed skill workflow.\n"
                    "Decide the output language from how the USER writes (use the language reference below as the authority). "
                    "Support any language. The internal source summary is always written in English and must NEVER influence the output language.\n"
                    "Rewrite the internal source summary; keep every concrete number, destination/group name, and the blocked-item count, but never invent data.\n"
                    "Do not include internal identifiers, request ids, tokens, checkpoint ids, or long raw file-path dumps (a couple of example names is fine).\n\n"
                    "Return JSON only with keys: language, plan_markdown, plan_short, next_step.\n"
                    "- plan_markdown: a clear, friendly Markdown message presenting the plan. Use a short intro line and bullet points or compact sections so it is easy to scan. This is shown as a chat bubble that renders Markdown.\n"
                    "- plan_short: ONE or TWO plain sentences (no Markdown, no line breaks) summarizing what will happen, for a small confirmation card. Mention the total moves and the main destination groups, plus blocked count if any.\n"
                    "- next_step: ONE short sentence with the call to action, matching the phase below.\n\n"
                    "Phase meanings (controls only the next_step wording and tone):\n"
                    "- plan_ready: a plan with moves is ready but NOT applied yet. next_step should invite the user to confirm so it gets applied. Do NOT tell them to 'review' as a separate step, because the plan is already shown above.\n"
                    "- plan_only: there is nothing to move right now. next_step should explain the state and any blocked items; there is nothing to apply.\n"
                    "- awaiting_approval: an approval card with Apply/Cancel buttons is shown directly below this message. next_step must tell the user to use those buttons to apply or cancel, and must NOT repeat the full plan or mention ids.\n"
                    "- executed: the approved changes were ALREADY applied successfully. plan_markdown should confirm what was done (counts, destinations); next_step can be a brief closing remark or empty.\n\n"
                    f"Skill name: {skill_name}\n"
                    f"Phase: {phase}\n"
                    f"Language reference (recent user messages — match this language):\n{language_sample or user_message}\n\n"
                    f"Latest user message:\n{user_message}\n\n"
                    f"Internal source summary (English):\n{source_summary}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"language":"<language code or name>","plan_markdown":"<markdown in user language>",'
                    '"plan_short":"<1-2 plain sentences>","next_step":"<one short sentence>"}'
                ),
            )
        )

    async def judge_skill_workflow_semantic_groups(
        self,
        *,
        user_message: str,
        items: list[str],
        item_kind: str = "items",
        group_kind: str = "groups",
        grouping_context: str = "",
        skill_name: str = "",
        language_sample: str = "",
    ) -> dict[str, Any] | None:
        """Generic semantic grouping: maps each item to a user-intended group label. No lists/regex.

        The caller supplies the domain framing (``item_kind``, ``group_kind`` and the
        ``grouping_context`` criterion) so this primitive stays reusable across skills
        rather than being hard-coded for any one of them.
        """
        if not items:
            return None
        skill_line = f"Skill name: {skill_name}\n" if skill_name.strip() else ""
        context_line = (
            f"Grouping intent (how to group): {grouping_context}\n" if grouping_context.strip() else ""
        )
        return await self.judge_json(
            SemanticJudgeRequest(
                title="Skill workflow semantic grouping",
                source="skill-workflow-semantic-grouping",
                prompt=(
                    f"You assign {item_kind} to {group_kind} that reflect what the user wants.\n"
                    "Use semantic reasoning over the meaning of the user's request and each item. "
                    "Do not rely on keyword lists or regex; infer intent in any language.\n"
                    "Group items the way the user describes. "
                    f"Choose short, human-readable {group_kind} written in the SAME language the user writes in.\n"
                    "Only include an item when you are confident it belongs to one of the groups the user "
                    "described; omit any item you are unsure about so it keeps its default handling.\n"
                    "Never invent items that are not in the provided list. Assign each chosen item to exactly "
                    "one group label, and follow any formatting rule given in the grouping intent below.\n\n"
                    "Return JSON only with key 'groups': an object mapping each chosen item's exact value "
                    "to its group label. Return an empty object when nothing should be grouped.\n\n"
                    f"{skill_line}"
                    f"{context_line}"
                    f"Language reference (recent user messages — match this language for labels):\n"
                    f"{language_sample or user_message}\n\n"
                    f"User request:\n{user_message}\n\n"
                    f"Items:\n{json.dumps(items, ensure_ascii=False)}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"groups":{"<item>":"<group label>"}}'
                ),
            )
        )

    async def judge_skill_workflow_plan_follow_up(
        self,
        *,
        user_message: str,
        skill_name: str,
        plan_summary: str,
        organization_plan: dict[str, Any],
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title=f"{skill_name} plan follow-up judge",
                source="skill-workflow-plan-follow-up-judge",
                prompt=(
                    "You classify the user's follow-up to a previously generated executable skill workflow plan.\n"
                    "Use semantic reasoning over the user message and the structured plan. Do not use literal keyword matching.\n"
                    "Return JSON only with keys: decision, reason.\n\n"
                    "Allowed decision values:\n"
                    "- approve_plan: the user accepts the reviewed plan and wants the workflow to proceed toward execution approval.\n"
                    "- reject_plan: the user declines or cancels the reviewed plan.\n"
                    "- revise_plan: the user asks to change, refine, regenerate, or inspect the plan before execution approval.\n"
                    "- unrelated: the user is not responding to this plan.\n\n"
                    f"Skill name: {skill_name}\n\n"
                    f"Plan summary:\n{plan_summary}\n\n"
                    f"Structured organization plan:\n{json.dumps(organization_plan, ensure_ascii=False)}\n\n"
                    f"User follow-up:\n{user_message}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"decision":"approve_plan|reject_plan|revise_plan|unrelated","reason":"short reason"}'
                ),
            )
        )

    async def judge_capability_contract(
        self,
        *,
        capability_name: str,
        user_message: str,
        candidate_reply: str,
        capability_facts: dict[str, Any],
    ) -> dict[str, Any] | None:
        return await self.judge_json(
            SemanticJudgeRequest(
                title=f"{capability_name} capability contract judge",
                source="capability-contract-judge",
                prompt=(
                    "You validate whether an assistant reply respects a capability contract.\n"
                    "Return JSON only with keys: decision, should_retry, turn_status, reason, guidance.\n\n"
                    "Allowed decision values:\n"
                    "- valid\n"
                    "- invalid\n\n"
                    "Allowed turn_status values:\n"
                    "- final_answer\n"
                    "- blocking_question\n"
                    "- approval_required\n"
                    "- tool_failure\n\n"
                    "Mark invalid when the reply asks for inputs that capability facts say are already known, "
                    "claims unsupported actions, asks for approval too early, or promises execution now without a real executable plan or tool result.\n"
                    "If invalid, set should_retry=true and provide short guidance describing how the reply must be corrected.\n\n"
                    f"Capability name: {capability_name}\n"
                    f"Capability facts:\n{json.dumps(capability_facts, ensure_ascii=False)}\n\n"
                    f"User message:\n{user_message}\n\n"
                    f"Candidate assistant reply:\n{candidate_reply}\n"
                ),
                instructions=(
                    "Return JSON only. "
                    'Schema: {"decision":"valid|invalid","should_retry":true|false,'
                    '"turn_status":"final_answer|blocking_question|approval_required|tool_failure",'
                    '"reason":"short reason","guidance":"short guidance"}'
                ),
            )
        )

    def _extract_json_object(self, text: str) -> dict[str, Any] | None:
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
