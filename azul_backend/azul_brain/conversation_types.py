"""Plain data contracts shared across the conversation orchestrator.

These dataclasses and constants carry no behaviour and no dependencies on the
orchestrator's runtime state, so they live apart from ``conversation.py`` to keep
that module focused on orchestration logic. They remain re-exported from
``conversation`` for backward-compatible imports.
"""

from dataclasses import dataclass

TURN_CLOSURE_FAILURE_TEXT = (
    "I couldn't complete that request reliably just now, so I stopped instead of ending with an incomplete promise. "
    "Please try again or ask me to rerun the plan."
)


@dataclass
class ConversationReply:
    """Enriched reply from the orchestrator."""

    text: str
    model_id: str = ""
    model_label: str = ""
    process_id: str = ""
    attempt_count: int = 0
    skipped_models: list[dict[str, str]] | None = None
    failed_attempts: list[dict[str, str]] | None = None
    lane: str = "auto"
    triage_reason: str = ""
    conversation_title: str | None = None
    turn_status: str = "final_answer"
    workflow_events: list[dict] | None = None


@dataclass
class TurnClosureVerdict:
    status: str
    should_retry: bool = False
    reason: str = ""


@dataclass
class PendingActionStageVerdict:
    decision: str
    action_kind: str = ""
    title: str = ""
    summary: str = ""
    reason: str = ""


@dataclass
class PendingActionUserIntentVerdict:
    decision: str
    reason: str = ""


@dataclass
class CapabilityContractVerdict:
    decision: str
    should_retry: bool = False
    turn_status: str = "final_answer"
    reason: str = ""
    guidance: str = ""


@dataclass
class FolderOrganizerRequestVerdict:
    decision: str
    reason: str = ""


@dataclass
class FolderOrganizerPreviewContextVerdict:
    reply_language: str = "en"
    has_executable_plan: bool = False
    conceptual_plan_requested: bool = False
    status_summary: str = ""
    reason: str = ""
