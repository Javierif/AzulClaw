import { useEffect, useMemo, useRef, useState } from "react";

import {
  decideWorkflowRequest,
  deleteDraftAttachment,
  deleteConversation,
  getConversationMessages,
  listConversations,
  sendDesktopMessageStream,
  uploadDraftAttachments,
} from "../../lib/api";
import { readClipboardFilePaths, readLocalFilesFromPaths } from "../../lib/desktop-attachments";
import {
  DEFAULT_CONVERSATION_TITLE,
  normalizeConversationTitle,
  withNormalizedConversationTitles,
} from "../../lib/conversation-copy";
import type {
  AttachmentSummary,
  ChatDebugTrace,
  ChatExchange,
  ConversationSummary,
  SkillWorkflowEvent,
  ThinkingProgress,
} from "../../lib/contracts";
import { isTauri } from "@tauri-apps/api/core";
import { listen, TauriEvent } from "@tauri-apps/api/event";
import { MessageContent } from "./MessageContent";

type ChatMessageItem = ChatExchange & {
  kind: "text" | "thinking" | "pending";
  progress?: ThinkingProgress;
  debugTrace?: ChatDebugTrace;
};

type DragDropPayload = {
  paths?: string[];
};

type HeartbeatConfirmationDetails = {
  actionKind: string;
  actionId: string;
  title: string;
  summary: string;
  approveLabel: string;
  rejectLabel: string;
  approvalStatus: string;
  approvalStatusLabel: string;
  name: string;
  schedule: string;
  action: string;
  delivery: string;
};

type PendingSensitiveActionDetails = {
  actionKind: string;
  actionId: string;
  title: string;
  summary: string;
  approveLabel: string;
  rejectLabel: string;
  approvalStatus: string;
  approvalStatusLabel: string;
  strippedContent: string;
  revisionLabel: string;
  executionBinding: string;
  scope: string;
  batches: string;
  customCategories: string;
  planHash: string;
  previewSummary: string;
  previewMode: string;
  previewDepth: string;
  previewCategories: string;
};

type WorkflowApprovalDetails = {
  runId: string;
  requestId: string;
  skillId: string;
  actionKind: string;
  title: string;
  summary: string;
  approveLabel: string;
  rejectLabel: string;
  approvalStatus: string;
  approvalStatusLabel: string;
};

/** Shown only in the UI until the user sends their first message (not persisted). */
const WELCOME_MESSAGE_ID = "welcome-greeting";

/** Synthetic row in “Recent conversations” for the unsaved draft session. */
const DRAFT_SESSION_ID = "__draft_session__";
const CHAT_SIDEBAR_STATE_KEY = "azul_chat_sidebar_collapsed";
const CHAT_DEBUG_TRACE_STORAGE_KEY = "azul_chat_debug_traces_v1";

function createWelcomeMessage(): ChatMessageItem {
  return {
    id: WELCOME_MESSAGE_ID,
    role: "assistant",
    content:
      "Hey there! I'm glad you're here. Tell me what you're working on, or ask me anything — I'm all ears. How can I help today?",
    created_at: new Date().toISOString(),
    kind: "text",
  };
}

function loadStoredDebugTraceMap(): Record<string, ChatDebugTrace> {
  try {
    const raw = localStorage.getItem(CHAT_DEBUG_TRACE_STORAGE_KEY);
    if (!raw) {
      return {};
    }
    const parsed = JSON.parse(raw) as Record<string, ChatDebugTrace>;
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch {
    return {};
  }
}

function persistStoredDebugTraceMap(map: Record<string, ChatDebugTrace>): void {
  try {
    localStorage.setItem(CHAT_DEBUG_TRACE_STORAGE_KEY, JSON.stringify(map));
  } catch {
    /* ignore persistence failures */
  }
}

function normalizeExchangeId(item: ChatExchange & { message_id?: string }, fallbackId: string): string {
  return item.id?.trim() || item.message_id?.trim() || fallbackId;
}

function toUiMessages(items: ChatExchange[]): ChatMessageItem[] {
  return items.map((item, index) => ({
    ...item,
    id: normalizeExchangeId(item as ChatExchange & { message_id?: string }, `loaded-${index}`),
    kind: "text",
  }));
}

function applyStoredDebugTraces(
  items: ChatMessageItem[],
  debugTraceByMessageId: Record<string, ChatDebugTrace>,
): ChatMessageItem[] {
  return items.map((item) => ({
    ...item,
    debugTrace: item.debugTrace ?? debugTraceByMessageId[item.id],
  }));
}

function mergeMessageMetadata(current: ChatMessageItem[], next: ChatMessageItem[]): ChatMessageItem[] {
  const currentById = new Map(current.map((item) => [item.id, item]));
  return next.map((item) => {
    const existing = currentById.get(item.id);
    if (!existing) {
      return item;
    }
    return {
      ...item,
      debugTrace: item.debugTrace ?? existing.debugTrace,
      workflow_events:
        item.workflow_events && item.workflow_events.length
          ? item.workflow_events
          : existing.workflow_events,
      approval_action_id: item.approval_action_id ?? existing.approval_action_id,
      approval_status: item.approval_status ?? existing.approval_status,
      approval_status_label: item.approval_status_label ?? existing.approval_status_label,
    };
  });
}

function sameSkippedModels(
  a: ChatDebugTrace["skipped_models"] = [],
  b: ChatDebugTrace["skipped_models"] = [],
): boolean {
  if ((a?.length || 0) !== (b?.length || 0)) {
    return false;
  }
  return (a || []).every((item, index) => {
    const other = (b || [])[index];
    return (
      item.model_id === other.model_id &&
      item.model_label === other.model_label &&
      item.lane === other.lane &&
      item.reason === other.reason &&
      item.reason_label === other.reason_label &&
      item.detail === other.detail
    );
  });
}

function sameFailedAttempts(
  a: ChatDebugTrace["failed_attempts"] = [],
  b: ChatDebugTrace["failed_attempts"] = [],
): boolean {
  if ((a?.length || 0) !== (b?.length || 0)) {
    return false;
  }
  return (a || []).every((item, index) => {
    const other = (b || [])[index];
    return (
      item.model_id === other.model_id &&
      item.label === other.label &&
      item.error === other.error
    );
  });
}

function sameDebugTrace(a?: ChatDebugTrace, b?: ChatDebugTrace): boolean {
  if (!a && !b) {
    return true;
  }
  if (!a || !b) {
    return false;
  }
  return (
    a.lane_label === b.lane_label &&
    a.reason_label === b.reason_label &&
    a.triage_reason === b.triage_reason &&
    a.turn_status === b.turn_status &&
    a.model_label === b.model_label &&
    a.process_id === b.process_id &&
    (a.attempt_count || 0) === (b.attempt_count || 0) &&
    a.started_at === b.started_at &&
    a.completed_at === b.completed_at &&
    (a.elapsed_ms || 0) === (b.elapsed_ms || 0) &&
    a.summary === b.summary &&
    sameSkippedModels(a.skipped_models, b.skipped_models) &&
    sameFailedAttempts(a.failed_attempts, b.failed_attempts)
  );
}

function sameAttachments(a: AttachmentSummary[] = [], b: AttachmentSummary[] = []): boolean {
  if (a.length !== b.length) {
    return false;
  }
  return a.every((item, index) => {
    const other = b[index];
    return (
      item.id === other.id &&
      item.filename === other.filename &&
      item.mime_type === other.mime_type &&
      item.size_bytes === other.size_bytes &&
      item.kind === other.kind &&
      item.extraction_status === other.extraction_status &&
      item.page_count === other.page_count &&
      (item.preview?.thumbnail_data_uri || "") === (other.preview?.thumbnail_data_uri || "") &&
      (item.preview?.snippet || "") === (other.preview?.snippet || "") &&
      (item.preview?.width || 0) === (other.preview?.width || 0) &&
      (item.preview?.height || 0) === (other.preview?.height || 0) &&
      (item.preview?.page_count || 0) === (other.preview?.page_count || 0) &&
      (item.preview?.pages_with_text || 0) === (other.preview?.pages_with_text || 0) &&
      (item.preview?.avg_chars_per_page || 0) === (other.preview?.avg_chars_per_page || 0)
    );
  });
}

function sameMessages(a: ChatMessageItem[], b: ChatMessageItem[]): boolean {
  if (a.length !== b.length) {
    return false;
  }
  return a.every((item, index) => {
    const other = b[index];
    return (
      item.id === other.id &&
      item.role === other.role &&
      item.kind === other.kind &&
      item.content === other.content &&
      item.created_at === other.created_at &&
      (item.approval_action_id || "") === (other.approval_action_id || "") &&
      (item.approval_status || "") === (other.approval_status || "") &&
      (item.approval_status_label || "") === (other.approval_status_label || "") &&
      JSON.stringify(item.workflow_events || []) === JSON.stringify(other.workflow_events || []) &&
      sameAttachments(item.attachments, other.attachments) &&
      sameDebugTrace(item.debugTrace, other.debugTrace)
    );
  });
}

function attachWorkflowEventsToLastAssistant(
  items: ChatMessageItem[],
  workflowEvents: SkillWorkflowEvent[] = [],
): ChatMessageItem[] {
  if (!workflowEvents.length) {
    return items;
  }
  const targetIndex = [...items].reverse().findIndex((item) => item.role === "assistant");
  if (targetIndex < 0) {
    return items;
  }
  const index = items.length - 1 - targetIndex;
  return items.map((item, currentIndex) =>
    currentIndex === index
      ? { ...item, workflow_events: workflowEvents }
      : item,
  );
}

function workflowStatusLabel(status: string): string {
  switch (status) {
    case "pending":
      return "Awaiting approval";
    case "approved":
      return "Approved";
    case "rejected":
      return "Rejected";
    case "completed":
      return "Completed";
    case "failed":
      return "Failed";
    default:
      return status ? status.charAt(0).toUpperCase() + status.slice(1) : "Awaiting approval";
  }
}

function workflowApprovalFromEvents(
  events: SkillWorkflowEvent[] = [],
  approvalStatus?: string,
  approvalStatusLabel?: string,
): WorkflowApprovalDetails | null {
  const requestEvent = events.find((event) => event.type === "request_info");
  if (!requestEvent) {
    return null;
  }
  const data = requestEvent.data || {};
  const labels = (data.labels && typeof data.labels === "object" ? data.labels : {}) as Record<string, unknown>;
  const requestId = String(data.request_id || "").trim();
  const runId = String(data.run_id || requestEvent.run_id || "").trim();
  if (!requestId || !runId) {
    return null;
  }
  const status = approvalStatus?.trim() || "pending";
  return {
    runId,
    requestId,
    skillId: String(data.skill_id || requestEvent.skill_id || "").trim(),
    actionKind: String(data.action_kind || "").trim(),
    title: String(data.title || "Skill workflow approval").trim(),
    summary: String(data.summary || "Approve the requested workflow action.").trim(),
    approveLabel: String(labels.approve || "Approve").trim(),
    rejectLabel: String(labels.reject || "Cancel").trim(),
    approvalStatus: status,
    approvalStatusLabel: approvalStatusLabel?.trim() || workflowStatusLabel(status),
  };
}

function markWorkflowApprovalStatus(
  items: ChatMessageItem[],
  requestId: string,
  status: string,
  statusLabel: string,
): ChatMessageItem[] {
  return items.map((item) => {
    const details = workflowApprovalFromEvents(item.workflow_events);
    if (!details || details.requestId !== requestId) {
      return item;
    }
    return {
      ...item,
      approval_action_id: requestId,
      approval_status: status,
      approval_status_label: statusLabel,
    };
  });
}

function formatAttachmentSize(sizeBytes: number): string {
  if (sizeBytes >= 1024 * 1024) {
    return `${(sizeBytes / (1024 * 1024)).toFixed(1)} MB`;
  }
  if (sizeBytes >= 1024) {
    return `${Math.round(sizeBytes / 1024)} KB`;
  }
  return `${sizeBytes} B`;
}

function attachmentPreviewUrl(attachment: AttachmentSummary): string {
  return attachment.preview?.thumbnail_data_uri?.trim() || "";
}

function attachmentStatusLabel(attachment: AttachmentSummary): string {
  if (attachment.extraction_status === "low_text_quality") {
    return "Visual analysis";
  }
  if (attachment.kind === "image") {
    return "Image";
  }
  if (attachment.page_count > 1) {
    return `${attachment.page_count} pages`;
  }
  return attachment.kind === "text" ? "Text" : "Document";
}

function phaseStatusLabel(status: "pending" | "active" | "done") {
  if (status === "done") {
    return "Done";
  }
  if (status === "active") {
    return "In progress";
  }
  return "Pending";
}

function isProgressEventType(
  type: string,
): type is "progress-init" | "progress-update" | "progress-idle" | "progress-done" {
  return type === "progress-init" || type === "progress-update" || type === "progress-idle" || type === "progress-done";
}

function secondsSince(timestamp?: string): number | null {
  if (!timestamp) {
    return null;
  }
  const millis = Date.parse(timestamp);
  if (Number.isNaN(millis)) {
    return null;
  }
  return Math.max(0, Math.floor((Date.now() - millis) / 1000));
}

function formatElapsedLabel(timestamp?: string): string {
  const seconds = secondsSince(timestamp);
  if (seconds === null) {
    return "Working now";
  }
  if (seconds < 60) {
    return `${seconds}s working`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder > 0 ? `${minutes}m ${remainder}s working` : `${minutes}m working`;
}

function formatLastUpdatedLabel(timestamp?: string): string {
  const seconds = secondsSince(timestamp);
  if (seconds === null) {
    return "Last updated just now";
  }
  if (seconds <= 1) {
    return "Last updated just now";
  }
  return `Last updated ${seconds}s ago`;
}

function createInitialThinkingProgress(startedAt: string): ThinkingProgress {
  return {
    event_type: "progress-init",
    title: "Preparing request",
    summary: "Connecting to the cognitive layer.",
    badge: "Working",
    lane: "auto",
    lane_label: "Working",
    triage_reason: "starting",
    reason_label: "Starting request",
    current_step_label: "Starting request",
    started_at: startedAt,
    last_updated_at: startedAt,
    active_count: 1,
    phases: [],
  };
}

function formatDebugElapsed(trace?: ChatDebugTrace): string {
  const millis = trace?.elapsed_ms;
  if (!millis || millis < 0) {
    return "Completed";
  }
  const seconds = Math.max(1, Math.round(millis / 1000));
  if (seconds < 60) {
    return `Completed in ${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  const remainder = seconds % 60;
  return remainder > 0 ? `Completed in ${minutes}m ${remainder}s` : `Completed in ${minutes}m`;
}

function formatAttemptLabel(trace?: ChatDebugTrace): string {
  const count = Math.max(0, trace?.attempt_count || 0);
  if (count <= 1) {
    return "1 attempt";
  }
  const normalizedModel = trace?.model_label?.toLowerCase() || "";
  const fallbackUsed =
    trace?.lane_label?.toLowerCase().includes("fast") &&
    (normalizedModel.includes("lento") || normalizedModel.includes("slow"));
  return fallbackUsed ? `${count} attempts · fallback used` : `${count} attempts`;
}

function getFastSkipDetail(trace?: ChatDebugTrace) {
  return (trace?.skipped_models || []).find((item) => item.model_id === "fast" || item.lane === "fast");
}

function getFastFailureDetail(trace?: ChatDebugTrace) {
  return (trace?.failed_attempts || []).find((item) => item.model_id === "fast");
}

function formatDiagnosticTooltip(detail: string): string {
  const raw = detail.trim();
  if (!raw) {
    return "";
  }
  try {
    const parsed = JSON.parse(raw) as { cooldown_until?: string; last_error?: string };
    if (parsed && typeof parsed === "object" && (parsed.cooldown_until || parsed.last_error)) {
      const untilRaw = String(parsed.cooldown_until || "").trim();
      const previousError = String(parsed.last_error || "").trim();
      const millis = untilRaw ? Date.parse(untilRaw) : Number.NaN;
      const timePart = !Number.isNaN(millis)
        ? (() => {
            const local = new Intl.DateTimeFormat(undefined, {
              day: "2-digit",
              month: "short",
              year: "numeric",
              hour: "2-digit",
              minute: "2-digit",
              second: "2-digit",
            }).format(new Date(millis));
            const secondsRemaining = Math.max(0, Math.ceil((millis - Date.now()) / 1000));
            return secondsRemaining > 0
              ? `Cooldown active until ${local} (${secondsRemaining}s remaining).`
              : `Cooldown lifted at ${local}.`;
          })()
        : "";
      if (previousError && timePart) {
        return `Fast brain was skipped because a previous execution failed. ${timePart} Previous error: ${previousError}`;
      }
      if (previousError) {
        return `Fast brain was skipped because a previous execution failed. Previous error: ${previousError}`;
      }
      if (timePart) {
        return `Fast brain was skipped because it was still cooling down. ${timePart}`;
      }
    }
  } catch {
    /* detail is plain text */
  }
  const millis = Date.parse(raw);
  if (!Number.isNaN(millis)) {
    const local = new Intl.DateTimeFormat(undefined, {
      day: "2-digit",
      month: "short",
      year: "numeric",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    }).format(new Date(millis));
    const secondsRemaining = Math.max(0, Math.ceil((millis - Date.now()) / 1000));
    if (secondsRemaining > 0) {
      return `Cooling down until ${local} (${secondsRemaining}s remaining).`;
    }
    return `Cooldown expired at ${local}.`;
  }
  return raw;
}

function TraceInfoHint({ text }: { text: string }) {
  if (!text.trim()) {
    return null;
  }
  return (
    <span className="assistant-trace-hint" title={text} aria-label={text}>
      ?
    </span>
  );
}

function approvalStatusSummary(status: string, title: string): string {
  const subject = title.trim() || "Approval";
  switch (status) {
    case "superseded":
      return `${subject} approval was replaced after the conversation continued.`;
    case "rejected":
      return `${subject} approval was canceled.`;
    case "expired":
      return `${subject} approval expired before execution.`;
    case "running":
      return `${subject} approval was accepted and is running.`;
    case "completed":
    case "approved":
      return `${subject} approval was accepted and completed.`;
    case "failed":
      return `${subject} approval was accepted but the execution failed.`;
    default:
      return `${subject} approval is no longer active.`;
  }
}

function CompactApprovalNotice({
  title,
  status,
  statusLabel,
}: {
  title: string;
  status: string;
  statusLabel: string;
}) {
  return (
    <div className="compact-approval-notice">
      <span className={`pending-approval-status-chip pending-approval-status-${status}`}>
        {statusLabel}
      </span>
      <span className="compact-approval-text">{approvalStatusSummary(status, title)}</span>
    </div>
  );
}

function inferApprovalStatusFromError(message: string): { status: string; label: string } | null {
  const normalized = (message || "").trim().toLowerCase().split(/\s+/).join(" ");
  if (!normalized) {
    return null;
  }
  if (normalized.includes("replaced by a newer reviewed plan")) {
    return { status: "superseded", label: "Superseded" };
  }
  if (
    normalized.includes("no longer available") ||
    normalized.includes("no longer active") ||
    normalized.includes("approval expired") ||
    normalized.includes("pending action not found")
  ) {
    return { status: "expired", label: "Expired" };
  }
  if (normalized.includes("already running")) {
    return { status: "running", label: "Running" };
  }
  if (normalized.includes("already processed")) {
    return { status: "completed", label: "Completed" };
  }
  if (normalized.includes("already canceled")) {
    return { status: "rejected", label: "Rejected" };
  }
  if (normalized.includes("execution failed")) {
    return { status: "failed", label: "Failed" };
  }
  return null;
}

function markApprovalMessageStatus(
  items: ChatMessageItem[],
  actionId: string,
  status: string,
  statusLabel: string,
): ChatMessageItem[] {
  return items.map((item) => {
    if (item.role !== "assistant") {
      return item;
    }
    const details =
      parsePendingSensitiveAction(item.content, item.approval_status, item.approval_status_label) ||
      parseHeartbeatConfirmation(item.content, item.approval_status, item.approval_status_label);
    if (!details || details.actionId !== actionId) {
      return item;
    }
    return {
      ...item,
      approval_action_id: actionId,
      approval_status: status,
      approval_status_label: statusLabel,
    };
  });
}

function AssistantTraceDisclosure({ trace }: { trace: ChatDebugTrace }) {
  const [expanded, setExpanded] = useState(false);
  const completedLabel = formatDebugElapsed(trace);
  const attemptsLabel = formatAttemptLabel(trace);
  const fastSkip = getFastSkipDetail(trace);
  const fastFailure = getFastFailureDetail(trace);
  const fastSkipHint = fastSkip?.detail ? formatDiagnosticTooltip(fastSkip.detail) : "";
  const fastFailureHint = fastFailure?.error?.trim() || "";
  return (
    <div className="assistant-trace">
      {expanded ? (
        <div className="assistant-trace-panel">
          <div className="assistant-trace-grid">
            <section className="assistant-trace-card">
              <span className="assistant-trace-label">Route</span>
              <strong>{trace.lane_label}</strong>
            </section>
            <section className="assistant-trace-card">
              <span className="assistant-trace-label">Reason</span>
              <strong>{trace.reason_label}</strong>
            </section>
            <section className="assistant-trace-card">
              <span className="assistant-trace-label">Model</span>
              <strong>{trace.model_label || "Unknown"}</strong>
            </section>
            <section className="assistant-trace-card">
              <span className="assistant-trace-label">Runtime</span>
              <strong>{completedLabel}</strong>
            </section>
            {trace.turn_status ? (
              <section className="assistant-trace-card">
                <span className="assistant-trace-label">Turn Status</span>
                <strong>{trace.turn_status}</strong>
              </section>
            ) : null}
            <section className="assistant-trace-card">
              <span className="assistant-trace-label">Attempts</span>
              <strong>{attemptsLabel}</strong>
            </section>
            {fastSkip ? (
              <section className="assistant-trace-card">
                <span className="assistant-trace-label">Fast Brain</span>
                <strong>Skipped before execution</strong>
              </section>
            ) : null}
            {fastSkip ? (
              <section className="assistant-trace-card">
                <span className="assistant-trace-label">Skip Reason</span>
                <strong className="assistant-trace-value-with-hint">
                  <span>{fastSkip.reason_label}</span>
                  <TraceInfoHint text={fastSkipHint} />
                </strong>
              </section>
            ) : null}
            {fastFailure ? (
              <section className="assistant-trace-card">
                <span className="assistant-trace-label">Fast Brain</span>
                <strong>Failed during execution</strong>
              </section>
            ) : null}
            {fastFailure ? (
              <section className="assistant-trace-card">
                <span className="assistant-trace-label">Failure Reason</span>
                <strong className="assistant-trace-value-with-hint">
                  <span>Model execution error</span>
                  <TraceInfoHint text={fastFailureHint} />
                </strong>
              </section>
            ) : null}
            {trace.summary ? (
              <section className="assistant-trace-card assistant-trace-card-wide">
                <span className="assistant-trace-label">Final State</span>
                <strong>{trace.summary}</strong>
              </section>
            ) : null}
          </div>
        </div>
      ) : null}
      <button
        type="button"
        className="assistant-trace-toggle"
        onClick={() => setExpanded((current) => !current)}
        aria-expanded={expanded}
        aria-label={expanded ? "Collapse response trace" : "Expand response trace"}
      >
        <span className={`assistant-trace-arrow${expanded ? " assistant-trace-arrow-open" : ""}`} aria-hidden="true">
          ^
        </span>
      </button>
    </div>
  );
}

type PendingApprovalDetails = {
  actionKind: string;
  actionId: string;
  title: string;
  summary: string;
  approveLabel: string;
  rejectLabel: string;
  strippedContent: string;
  revisionLabel: string;
  executionBinding: string;
  scope: string;
  batches: string;
  customCategories: string;
  planHash: string;
  previewSummary: string;
  previewMode: string;
  previewDepth: string;
  previewCategories: string;
  name: string;
  schedule: string;
  action: string;
  delivery: string;
};

function parsePendingApproval(content: string): PendingApprovalDetails | null {
  const text = (content || "").trim();
  const start = text.indexOf("[PENDING_ACTION:approval]");
  if (start < 0) {
    return null;
  }
  const end = text.indexOf("[/PENDING_ACTION]", start);
  if (end < 0) {
    return null;
  }
  const blockText = text.slice(start, end + "[/PENDING_ACTION]".length);
  const strippedContent = `${text.slice(0, start)}${text.slice(end + "[/PENDING_ACTION]".length)}`.trim();
  const field = (label: string, nextLabels: string[]) => {
    const stops = [
      ...nextLabels.map((nextLabel) => `^${nextLabel}:\\s*`),
      "^\\[/PENDING_ACTION\\]",
      "(?![\\s\\S])",
    ].join("|");
    return blockText.match(new RegExp(`^${label}:\\s*([\\s\\S]*?)(?=${stops})`, "m"))?.[1]?.trim();
  };
  const actionId = field("ActionId", ["ActionKind"])?.replace(/\s+/g, " ");
  const actionKind = field("ActionKind", ["Title"])?.replace(/\s+/g, " ") || "";
  const title = field("Title", ["Summary", "Name", "RevisionLabel"])?.replace(/\s+/g, " ") || "";
  const summary = field("Summary", ["RevisionLabel", "ExecutionBinding", "Scope", "Batches", "CustomCategories", "PlanHash", "PreviewSummary", "PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const revisionLabel = field("RevisionLabel", ["ExecutionBinding", "Scope", "Batches", "CustomCategories", "PlanHash", "PreviewSummary", "PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const executionBinding = field("ExecutionBinding", ["Scope", "Batches", "CustomCategories", "PlanHash", "PreviewSummary", "PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const scope = field("Scope", ["Batches", "CustomCategories", "PlanHash", "PreviewSummary", "PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const batches = field("Batches", ["CustomCategories", "PlanHash", "PreviewSummary", "PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const customCategories = field("CustomCategories", ["PlanHash", "PreviewSummary", "PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const planHash = field("PlanHash", ["PreviewSummary", "PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const previewSummary = field("PreviewSummary", ["PreviewMode", "PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const previewMode = field("PreviewMode", ["PreviewDepth", "PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const previewDepth = field("PreviewDepth", ["PreviewCategories", "Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const previewCategories = field("PreviewCategories", ["Name", "Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const name = field("Name", ["Schedule", "Action", "Delivery", "ApproveLabel"])?.replace(/\s+/g, " ") || "";
  const schedule = field("Schedule", ["Action", "Delivery", "ApproveLabel"])?.replace(/^`|`$/g, "").trim() || "";
  const action = field("Action", ["Delivery", "ApproveLabel"]) || "";
  const delivery = field("Delivery", ["ApproveLabel"])?.replace(/\s+/g, " ") || "desktop chat";
  const approveLabel = field("ApproveLabel", ["RejectLabel"])?.replace(/\s+/g, " ") || "Approve";
  const rejectLabel = field("RejectLabel", [])?.replace(/\s+/g, " ") || "Cancel";
  if (!actionId || !actionKind) {
    return null;
  }
  return {
    actionKind,
    actionId,
    title,
    summary,
    approveLabel,
    rejectLabel,
    strippedContent,
    revisionLabel,
    executionBinding,
    scope,
    batches,
    customCategories,
    planHash,
    previewSummary,
    previewMode,
    previewDepth,
    previewCategories,
    name,
    schedule,
    action,
    delivery,
  };
}

function parseHeartbeatConfirmation(
  content: string,
  approvalStatus?: string,
  approvalStatusLabel?: string,
): HeartbeatConfirmationDetails | null {
  const details = parsePendingApproval(content);
  if (!details || details.actionKind !== "heartbeat_create" || !details.name || !details.schedule || !details.action) {
    return null;
  }
  return {
    actionKind: details.actionKind,
    actionId: details.actionId,
    title: details.title,
    summary: details.summary,
    approveLabel: details.approveLabel,
    rejectLabel: details.rejectLabel,
    approvalStatus: approvalStatus?.trim() || "pending",
    approvalStatusLabel: approvalStatusLabel?.trim() || "Awaiting approval",
    name: details.name,
    schedule: details.schedule,
    action: details.action,
    delivery: details.delivery,
  };
}

function parsePendingSensitiveAction(
  content: string,
  approvalStatus?: string,
  approvalStatusLabel?: string,
): PendingSensitiveActionDetails | null {
  const details = parsePendingApproval(content);
  if (!details || details.actionKind === "heartbeat_create") {
    return null;
  }
  return {
    actionKind: details.actionKind,
    actionId: details.actionId,
    title: details.title,
    summary: details.summary,
    approveLabel: details.approveLabel,
    rejectLabel: details.rejectLabel,
    approvalStatus: approvalStatus?.trim() || "pending",
    approvalStatusLabel: approvalStatusLabel?.trim() || "Awaiting approval",
    strippedContent: details.strippedContent,
    revisionLabel: details.revisionLabel,
    executionBinding: details.executionBinding,
    scope: details.scope,
    batches: details.batches,
    customCategories: details.customCategories,
    planHash: details.planHash,
    previewSummary: details.previewSummary,
    previewMode: details.previewMode,
    previewDepth: details.previewDepth,
    previewCategories: details.previewCategories,
  };
}

function HeartbeatConfirmationCard({
  details,
  disabled,
  onCreate,
  onCancel,
}: {
  details: HeartbeatConfirmationDetails;
  disabled: boolean;
  onCreate: () => void;
  onCancel: () => void;
}) {
  const isPending = details.approvalStatus === "pending";
  return (
    <article className="message-bubble message-assistant message-heartbeat-card">
      <span className="message-role">AzulClaw</span>
      <div className="heartbeat-confirm-card">
        <div className="heartbeat-confirm-head">
          <div>
            <p className="heartbeat-confirm-eyebrow">{details.title || "Heartbeat draft"}</p>
            <h3>{details.name}</h3>
          </div>
          <span className="heartbeat-confirm-schedule">{details.schedule}</span>
        </div>
        <div className="pending-approval-status-row">
          <span className={`pending-approval-status-chip pending-approval-status-${details.approvalStatus}`}>
            {details.approvalStatusLabel}
          </span>
        </div>
        <div className="heartbeat-confirm-body">
          {details.summary ? (
            <div>
              <span>Summary</span>
              <p>{details.summary}</p>
            </div>
          ) : null}
          <div>
            <span>Action</span>
            <p>{details.action}</p>
          </div>
          <div>
            <span>Delivery</span>
            <p>{details.delivery}</p>
          </div>
        </div>
        {isPending ? (
          <div className="heartbeat-confirm-actions">
            <button
              type="button"
              className="ghost-button"
              onClick={onCancel}
              disabled={disabled}
            >
              {details.rejectLabel}
            </button>
            <button
              type="button"
              className="primary-button"
              onClick={onCreate}
              disabled={disabled}
            >
              {details.approveLabel}
            </button>
          </div>
        ) : null}
      </div>
    </article>
  );
}

function ThinkingCard({ message }: { message: ChatMessageItem }) {
  const progress = message.progress;
  const [expanded, setExpanded] = useState(false);
  const [timeTick, setTimeTick] = useState(0);
  const [openPhases, setOpenPhases] = useState<string[]>(() =>
    progress ? progress.phases.filter((phase) => phase.status === "active").map((phase) => phase.id) : [],
  );

  useEffect(() => {
    if (!progress) {
      return;
    }
    setOpenPhases((current) => {
      const next = new Set(current);
      for (const phase of progress.phases) {
        if (phase.status === "active") {
          next.add(phase.id);
        }
      }
      return Array.from(next);
    });
  }, [progress]);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setTimeTick((current) => current + 1);
    }, 1000);
    return () => window.clearInterval(timer);
  }, []);

  if (!progress) {
    return null;
  }
  void timeTick;

  const statusText =
    progress.active_count > 0
      ? `${progress.active_count} open subtasks`
      : "process complete";
  const laneLabel = progress.lane_label?.trim() || progress.badge;
  const reasonLabel = progress.reason_label?.trim() || progress.triage_reason?.trim() || "Process update";
  const currentStepLabel = progress.current_step_label?.trim() || progress.summary || progress.title;
  const elapsedLabel = formatElapsedLabel(progress.started_at);
  const lastUpdatedLabel = formatLastUpdatedLabel(progress.last_updated_at);

  return (
    <article className="message-bubble message-assistant message-thinking">
      <div className="thinking-card">
        <div className="thinking-topline">
          <span className="message-role">AzulClaw</span>
          <span className="thinking-badge">{laneLabel}</span>
        </div>

        <div className="thinking-header">
          <div>
            <h3>{message.content || progress.title}</h3>
            <p>{progress.title}</p>
          </div>
        </div>

        <div className="thinking-meta">
          <span className={`thinking-spinner ${progress.active_count === 0 ? "thinking-spinner-done" : ""}`} />
          <span>{statusText}</span>
        </div>

        {expanded ? (
          <div className="thinking-phase-list">
            <div className="thinking-detail-grid">
              <section className="thinking-detail-card">
                <span className="thinking-detail-label">Route</span>
                <strong>{laneLabel}</strong>
              </section>
              <section className="thinking-detail-card">
                <span className="thinking-detail-label">Reason</span>
                <strong>{reasonLabel}</strong>
              </section>
              <section className="thinking-detail-card thinking-detail-card-wide">
                <span className="thinking-detail-label">Current State</span>
                <strong>{currentStepLabel}</strong>
                <p>{progress.summary}</p>
              </section>
            </div>

            <div className="thinking-timing-row">
              <span className="thinking-route-chip">{elapsedLabel}</span>
              <span className="thinking-route-chip">{lastUpdatedLabel}</span>
            </div>

            <div className="thinking-steps-heading">
              <span className="thinking-detail-label">Steps</span>
            </div>
            {progress.phases.map((phase) => {
              const isOpen = openPhases.includes(phase.id);
              return (
                <section key={phase.id} className={`thinking-phase thinking-phase-${phase.status}`}>
                  <button
                    type="button"
                    className="thinking-phase-header"
                    onClick={() =>
                      setOpenPhases((current) =>
                        current.includes(phase.id)
                          ? current.filter((item) => item !== phase.id)
                          : [...current, phase.id],
                      )
                    }
                  >
                    <div className="thinking-phase-title">
                      <span className={`thinking-status-dot thinking-status-${phase.status}`} />
                      <strong>{phase.label}</strong>
                    </div>
                    <div className="thinking-phase-meta">
                      <span>{phaseStatusLabel(phase.status)}</span>
                      <span>{isOpen ? "-" : "+"}</span>
                    </div>
                  </button>

                  {isOpen ? (
                    <ul className="thinking-step-list">
                      {phase.steps.map((step) => (
                        <li key={step.id} className={`thinking-step thinking-step-${step.status}`}>
                          <span className={`thinking-step-check thinking-step-check-${step.status}`} />
                          <span>{step.label}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </section>
              );
            })}
          </div>
        ) : null}

        <button
          type="button"
          className="thinking-disclosure"
          onClick={() => setExpanded((current) => !current)}
          aria-expanded={expanded}
          aria-label={expanded ? "Collapse process details" : "Expand process details"}
        >
          <span className={`thinking-disclosure-arrow${expanded ? " thinking-disclosure-arrow-open" : ""}`} aria-hidden="true">
            ^
          </span>
        </button>
      </div>
    </article>
  );
}

function PendingSensitiveActionCard({
  details,
  disabled,
  onApprove,
  onReject,
}: {
  details: PendingSensitiveActionDetails;
  disabled: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const [showReviewedPlan, setShowReviewedPlan] = useState(false);
  const isPending = details.approvalStatus === "pending";
  const hasReviewedPlanDetails = Boolean(
    details.previewSummary || details.previewMode || details.previewDepth || details.previewCategories,
  );

  return (
    <div className="heartbeat-confirm-card">
      <div className="heartbeat-confirm-head">
        <div>
          <p className="heartbeat-confirm-eyebrow">Sensitive action approval</p>
          <h3>{details.title}</h3>
        </div>
      </div>
      {details.revisionLabel ? (
        <div className="pending-sensitive-revision-row">
          <span className="pending-sensitive-chip pending-sensitive-chip-revision">{details.revisionLabel}</span>
        </div>
      ) : null}
      <div className="pending-approval-status-row">
        <span className={`pending-approval-status-chip pending-approval-status-${details.approvalStatus}`}>
          {details.approvalStatusLabel}
        </span>
      </div>
      {details.executionBinding || details.scope || details.batches || details.customCategories ? (
        <div className="pending-sensitive-meta-row">
          {details.executionBinding ? (
            <span className="pending-sensitive-chip pending-sensitive-chip-strong">
              {details.executionBinding}
            </span>
          ) : null}
          {details.scope ? (
            <span className="pending-sensitive-chip" title={details.scope}>
              Scope: {details.scope}
            </span>
          ) : null}
          {details.batches ? (
            <span className="pending-sensitive-chip" title={details.batches}>
              {details.batches}
            </span>
          ) : null}
          {details.customCategories ? (
            <span className="pending-sensitive-chip" title={details.customCategories}>
              {details.customCategories}
            </span>
          ) : null}
        </div>
      ) : null}
      <div className="heartbeat-confirm-body">
        <div>
          <span>Action</span>
          <p>{details.summary}</p>
        </div>
      </div>
      {hasReviewedPlanDetails ? (
        <div className="pending-sensitive-reviewed-plan">
          <button
            type="button"
            className="pending-sensitive-reviewed-plan-toggle"
            onClick={() => setShowReviewedPlan((current) => !current)}
            aria-expanded={showReviewedPlan}
            aria-label={showReviewedPlan ? "Hide reviewed plan details" : "View reviewed plan details"}
          >
            <span>View reviewed plan</span>
            <span
              className={`pending-sensitive-reviewed-plan-arrow${showReviewedPlan ? " pending-sensitive-reviewed-plan-arrow-open" : ""}`}
              aria-hidden="true"
            >
              ^
            </span>
          </button>
          {showReviewedPlan ? (
            <div className="pending-sensitive-reviewed-plan-panel">
              {details.previewSummary ? (
                <section className="pending-sensitive-reviewed-plan-card pending-sensitive-reviewed-plan-card-wide">
                  <span className="pending-sensitive-reviewed-plan-label">Preview Summary</span>
                  <strong>{details.previewSummary}</strong>
                </section>
              ) : null}
              {details.previewMode ? (
                <section className="pending-sensitive-reviewed-plan-card">
                  <span className="pending-sensitive-reviewed-plan-label">Preview Mode</span>
                  <strong>{details.previewMode}</strong>
                </section>
              ) : null}
              {details.previewDepth ? (
                <section className="pending-sensitive-reviewed-plan-card">
                  <span className="pending-sensitive-reviewed-plan-label">Depth</span>
                  <strong>{details.previewDepth}</strong>
                </section>
              ) : null}
              {details.previewCategories ? (
                <section className="pending-sensitive-reviewed-plan-card pending-sensitive-reviewed-plan-card-wide">
                  <span className="pending-sensitive-reviewed-plan-label">Semantic Categories</span>
                  <strong>{details.previewCategories}</strong>
                </section>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : null}
      {details.planHash ? (
        <div className="pending-sensitive-footer-note">
          <span className="pending-sensitive-hash" title={`Reviewed plan hash ${details.planHash}`}>
            Plan {details.planHash}
          </span>
          <span className="pending-sensitive-note">Bound to the reviewed preview</span>
        </div>
      ) : null}
      {isPending ? (
        <div className="heartbeat-confirm-actions">
          <button
            type="button"
            className="ghost-button"
            onClick={onReject}
            disabled={disabled}
          >
            {details.rejectLabel}
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={onApprove}
            disabled={disabled}
          >
            {details.approveLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function WorkflowApprovalCard({
  details,
  disabled,
  onApprove,
  onReject,
}: {
  details: WorkflowApprovalDetails;
  disabled: boolean;
  onApprove: () => void;
  onReject: () => void;
}) {
  const isPending = details.approvalStatus === "pending";
  return (
    <div className="heartbeat-confirm-card">
      <div className="heartbeat-confirm-head">
        <div>
          <p className="heartbeat-confirm-eyebrow">Workflow approval</p>
          <h3>{details.title}</h3>
        </div>
      </div>
      <div className="pending-approval-status-row">
        <span className={`pending-approval-status-chip pending-approval-status-${details.approvalStatus}`}>
          {details.approvalStatusLabel}
        </span>
      </div>
      <div className="heartbeat-confirm-body">
        <div>
          <span>Action</span>
          <p>{details.summary}</p>
        </div>
        {details.skillId ? (
          <div>
            <span>Skill</span>
            <p>{details.skillId}</p>
          </div>
        ) : null}
      </div>
      {isPending ? (
        <div className="heartbeat-confirm-actions">
          <button type="button" className="ghost-button" onClick={onReject} disabled={disabled}>
            {details.rejectLabel}
          </button>
          <button type="button" className="primary-button" onClick={onApprove} disabled={disabled}>
            {details.approveLabel}
          </button>
        </div>
      ) : null}
    </div>
  );
}

function AttachmentList({
  attachments,
  compact = false,
  onRemove,
}: {
  attachments: AttachmentSummary[];
  compact?: boolean;
  onRemove?: (attachmentId: string) => void;
}) {
  if (attachments.length === 0) {
    return null;
  }

  return (
    <div className={`attachment-list${compact ? " attachment-list-compact" : ""}`}>
      {attachments.map((attachment) => {
        const previewUrl = attachmentPreviewUrl(attachment);
        return (
          <article key={attachment.id} className="attachment-chip">
            {previewUrl ? (
              <img src={previewUrl} alt={attachment.filename} className="attachment-chip-thumb" />
            ) : (
              <div className="attachment-chip-icon" aria-hidden="true">
                {attachment.kind === "image" ? "IMG" : attachment.mime_type === "application/pdf" ? "PDF" : "DOC"}
              </div>
            )}
            <div className="attachment-chip-body">
              <strong title={attachment.filename}>{attachment.filename}</strong>
              <span>
                {attachmentStatusLabel(attachment)} · {formatAttachmentSize(attachment.size_bytes)}
              </span>
            </div>
            {onRemove ? (
              <button
                type="button"
                className="attachment-chip-remove"
                onClick={() => onRemove(attachment.id)}
                aria-label={`Remove ${attachment.filename}`}
              >
                ×
              </button>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}


function formatRelativeDate(isoString: string): string {
  try {
    const date = new Date(isoString.endsWith("Z") ? isoString : isoString + "Z");
    const diffMs = Date.now() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    if (diffMins < 60) return `${diffMins}m ago`;
    const diffHours = Math.floor(diffMins / 60);
    if (diffHours < 24) return `${diffHours}h ago`;
    const diffDays = Math.floor(diffHours / 24);
    return `${diffDays}d ago`;
  } catch {
    return "";
  }
}

function parseTimestamp(isoString: string | undefined): Date | null {
  const value = (isoString || "").trim();
  if (!value) {
    return null;
  }
  const normalized = value.endsWith("Z") || value.includes("+")
    ? value
    : `${value.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? null : date;
}

function formatMessageTimestamp(isoString: string | undefined): string {
  const date = parseTimestamp(isoString);
  if (!date) {
    return "";
  }
  return new Intl.DateTimeFormat(undefined, {
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

export function ChatShell({
  onThinkingChange,
  onTypingChange,
  onAnswerStart,
  onTitleChange,
  onRegisterNewChat,
  focusRequestNonce = 0,
  unreadByConversationId = {},
  externalConversationRequest,
}: {
  onThinkingChange?: (thinking: boolean) => void;
  onTypingChange?: (typing: boolean) => void;
  onAnswerStart?: () => void;
  onTitleChange?: (title: string) => void;
  onRegisterNewChat?: (fn: () => void) => void;
  focusRequestNonce?: number;
  unreadByConversationId?: Record<string, boolean>;
  externalConversationRequest?: { conversationId: string; title: string; nonce: number } | null;
}) {
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [draft, setDraft] = useState("");
  const [draftAttachments, setDraftAttachments] = useState<AttachmentSummary[]>([]);
  const [attachmentError, setAttachmentError] = useState("");
  const [isUploadingAttachments, setIsUploadingAttachments] = useState(false);
  const [isDragActive, setIsDragActive] = useState(false);
  const [isSending, setIsSending] = useState(false);
  const [isFetchingSearch, setIsFetchingSearch] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [recentChats, setRecentChats] = useState<ConversationSummary[]>([]);
  const [conversationSearch, setConversationSearch] = useState("");
  const [sessionListTitle, setSessionListTitle] = useState(DEFAULT_CONVERSATION_TITLE);
  const [isConversationPanelCollapsed, setIsConversationPanelCollapsed] = useState(() => {
    try {
      return localStorage.getItem(CHAT_SIDEBAR_STATE_KEY) === "1";
    } catch {
      return false;
    }
  });
  const streamMessageIdRef = useRef("");
  const streamBufferRef = useRef("");
  const answerStartedRef = useRef(false);
  const turnClosedRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const messageListRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const streamPumpRef = useRef<number | null>(null);
  const hasLoadedConversationsRef = useRef(false);
  const lastHandledExternalConversationRef = useRef(0);
  const suppressConversationRefreshUntilRef = useRef(0);
  const debugTraceByMessageIdRef = useRef<Record<string, ChatDebugTrace>>(loadStoredDebugTraceMap());

  useEffect(() => {
    if (focusRequestNonce <= 0) {
      return;
    }
    composerInputRef.current?.focus();
  }, [focusRequestNonce]);

  function isMessageListNearBottom(): boolean {
    const list = messageListRef.current;
    if (!list) {
      return true;
    }
    return list.scrollHeight - list.scrollTop - list.clientHeight < 96;
  }

  function scrollMessageListToBottom() {
    const list = messageListRef.current;
    if (!list) {
      return;
    }
    list.scrollTo({ top: list.scrollHeight, behavior: "auto" });
  }

  function replaceMessagesIfChanged(nextMessages: ChatMessageItem[], shouldAutoScroll: boolean) {
    setMessages((current) => {
      const withStoredTrace = applyStoredDebugTraces(nextMessages, debugTraceByMessageIdRef.current);
      const merged = mergeMessageMetadata(current, withStoredTrace);
      if (sameMessages(current, merged)) {
        return current;
      }
      shouldAutoScrollRef.current = shouldAutoScroll;
      return merged;
    });
  }

  function storeDebugTrace(messageId: string, trace: ChatDebugTrace) {
    const normalizedId = messageId.trim();
    if (!normalizedId) {
      return;
    }
    debugTraceByMessageIdRef.current = {
      ...debugTraceByMessageIdRef.current,
      [normalizedId]: trace,
    };
    persistStoredDebugTraceMap(debugTraceByMessageIdRef.current);
  }

  async function removeDraftAttachmentFromServer(attachmentId: string) {
    try {
      await deleteDraftAttachment(attachmentId);
    } catch {
      /* best effort */
    }
  }

  async function clearDraftAttachments() {
    const ids = draftAttachments.map((attachment) => attachment.id);
    setDraftAttachments([]);
    if (ids.length === 0) {
      return;
    }
    await Promise.all(ids.map((id) => removeDraftAttachmentFromServer(id)));
  }

  async function addFilesToDraft(files: File[]) {
    const nextFiles = files.filter((file) => file.size > 0);
    if (nextFiles.length === 0) {
      return;
    }
    setAttachmentError("");
    setIsUploadingAttachments(true);
    try {
      const uploaded = await uploadDraftAttachments(nextFiles, "desktop-user", conversationId ?? undefined);
      setDraftAttachments((current) => {
        const existingIds = new Set(current.map((item) => item.id));
        return [...current, ...uploaded.filter((item) => !existingIds.has(item.id))];
      });
    } catch (error) {
      setAttachmentError(error instanceof Error ? error.message : "Could not attach the selected files.");
    } finally {
      setIsUploadingAttachments(false);
    }
  }

  async function addLocalPathsToDraft(paths: string[]) {
    const files = await readLocalFilesFromPaths(paths);
    await addFilesToDraft(files);
  }

  async function handleRemoveDraftAttachment(attachmentId: string) {
    setDraftAttachments((current) => current.filter((attachment) => attachment.id !== attachmentId));
    await removeDraftAttachmentFromServer(attachmentId);
  }

  function ensureStreamPump() {
    if (streamPumpRef.current !== null) {
      return;
    }
    streamPumpRef.current = window.setInterval(() => {
      const messageId = streamMessageIdRef.current;
      if (!messageId || !streamBufferRef.current) {
        return;
      }
      const slice = streamBufferRef.current.slice(0, 12);
      streamBufferRef.current = streamBufferRef.current.slice(12);
      setMessages((current) => {
        const existing = current.find((item) => item.id === messageId);
        if (existing) {
          return current.map((item) =>
            item.id === messageId ? { ...item, content: `${item.content}${slice}` } : item,
          );
        }
        return [...current, { id: messageId, role: "assistant", content: slice, created_at: new Date().toISOString(), kind: "text" }];
      });
      if (!streamBufferRef.current && streamPumpRef.current !== null) {
        window.clearInterval(streamPumpRef.current);
        streamPumpRef.current = null;
      }
    }, 30);
  }

  function flushStreamBuffer() {
    const messageId = streamMessageIdRef.current;
    const pending = streamBufferRef.current;
    if (messageId && pending) {
      setMessages((current) => {
        const existing = current.find((item) => item.id === messageId);
        if (existing) {
          return current.map((item) =>
            item.id === messageId ? { ...item, content: `${item.content}${pending}` } : item,
          );
        }
        return [...current, { id: messageId, role: "assistant", content: pending, created_at: new Date().toISOString(), kind: "text" }];
      });
    }
    streamBufferRef.current = "";
    if (streamPumpRef.current !== null) {
      window.clearInterval(streamPumpRef.current);
      streamPumpRef.current = null;
    }
  }

  async function handleClipboardPaste(event: React.ClipboardEvent<HTMLTextAreaElement>) {
    const clipboardFiles: File[] = [];
    for (const item of Array.from(event.clipboardData.items || [])) {
      if (item.kind === "file") {
        const file = item.getAsFile();
        if (file) {
          clipboardFiles.push(file);
        }
      }
    }

    if (clipboardFiles.length > 0) {
      event.preventDefault();
      await addFilesToDraft(clipboardFiles);
      return;
    }

    if (isTauri()) {
      const clipboardPaths = await readClipboardFilePaths();
      if (clipboardPaths.length > 0) {
        event.preventDefault();
        await addLocalPathsToDraft(clipboardPaths);
      }
    }
  }

  async function handleDomFileDrop(fileList: FileList | null) {
    const files = Array.from(fileList || []);
    if (files.length === 0) {
      return;
    }
    await addFilesToDraft(files);
  }

  // On mount: load existing conversations; never create one here (lazy creation on first send)
  useEffect(() => {
    let cancelled = false;
    async function initConversation() {
      try {
        const chatsRaw = await listConversations("desktop-user", conversationSearch);
        if (cancelled) return;
        const chats = withNormalizedConversationTitles(chatsRaw);
        setRecentChats(chats);
        if (chats.length > 0) {
          // Prefer a conversation that already has a real title (not the default placeholder)
          const withMessages =
            chats.find((c) => normalizeConversationTitle(c.title) !== DEFAULT_CONVERSATION_TITLE) ??
            chats[0];
          const msgs = await getConversationMessages(withMessages.id);
          if (cancelled) return;
          const t = normalizeConversationTitle(withMessages.title);
          setConversationId(withMessages.id);
          setSessionListTitle(t);
          onTitleChange?.(t);
          replaceMessagesIfChanged(toUiMessages(msgs), true);
        } else {
          replaceMessagesIfChanged([createWelcomeMessage()], true);
          setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
          onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
        }
      } catch {
        replaceMessagesIfChanged([createWelcomeMessage()], true);
        setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
        onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
      } finally {
        hasLoadedConversationsRef.current = true;
      }
    }
    void initConversation();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!hasLoadedConversationsRef.current) {
      return;
    }
    let cancelled = false;
    setIsFetchingSearch(true);
    const debounceId = window.setTimeout(() => {
      void (async () => {
        try {
          const chatsRaw = await listConversations("desktop-user", conversationSearch);
          if (cancelled) return;
          setRecentChats(withNormalizedConversationTitles(chatsRaw));
        } catch {
          /* ignore search refresh failures */
        } finally {
          if (!cancelled) setIsFetchingSearch(false);
        }
      })();
    }, 180);

    return () => {
      cancelled = true;
      window.clearTimeout(debounceId);
    };
  }, [conversationSearch]);

  useEffect(() => {
    let cancelled = false;
    let pollId: number | null = null;

    async function refresh() {
      if (cancelled) {
        return;
      }
      if (isSending) {
        if (!cancelled) {
          pollId = window.setTimeout(() => void refresh(), 5_000);
        }
        return;
      }

      try {
        const chatsRaw = await listConversations("desktop-user", conversationSearch);
        if (cancelled) return;
        const chats = withNormalizedConversationTitles(chatsRaw);
        setRecentChats(chats);

        if (!conversationId) {
          return;
        }

        const active = chats.find((chat) => chat.id === conversationId);
        if (!active) {
          return;
        }

        if (Date.now() < suppressConversationRefreshUntilRef.current) {
          return;
        }

        const msgs = await getConversationMessages(conversationId);
        if (cancelled) return;
        replaceMessagesIfChanged(toUiMessages(msgs), isMessageListNearBottom());
        const title = normalizeConversationTitle(active.title);
        setSessionListTitle(title);
        onTitleChange?.(title);
      } catch {
        /* ignore background refresh failures */
      } finally {
        if (!cancelled) {
          pollId = window.setTimeout(() => void refresh(), 5_000);
        }
      }
    }

    pollId = window.setTimeout(() => void refresh(), 5_000);

    return () => {
      cancelled = true;
      if (pollId !== null) {
        window.clearTimeout(pollId);
      }
    };
  }, [conversationId, conversationSearch, isSending, onTitleChange]);

  useEffect(() => {
    if (!externalConversationRequest) {
      return;
    }
    if (externalConversationRequest.nonce === lastHandledExternalConversationRef.current) {
      return;
    }
    lastHandledExternalConversationRef.current = externalConversationRequest.nonce;
    void handleLoadConversation(
      externalConversationRequest.conversationId,
      normalizeConversationTitle(externalConversationRequest.title),
    );
  }, [externalConversationRequest]);

  // Register the new-chat handler with the parent (topbar button)
  const handleNewChatRef = useRef<() => void>(() => {});
  const currentMessagesRef = useRef(messages);
  currentMessagesRef.current = messages;

  handleNewChatRef.current = () => {
    const msgs = currentMessagesRef.current;
    const onlyWelcome = msgs.length === 1 && msgs[0]?.id === WELCOME_MESSAGE_ID;
    if (msgs.length === 0 || onlyWelcome) return;
    void clearDraftAttachments();
    setConversationId(null);
    replaceMessagesIfChanged([createWelcomeMessage()], true);
    setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
    onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
    setAttachmentError("");
  };

  useEffect(() => {
    onRegisterNewChat?.(() => void handleNewChatRef.current());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleLoadConversation(id: string, title: string) {
    try {
      await clearDraftAttachments();
      const msgs = await getConversationMessages(id);
      const t = normalizeConversationTitle(title);
      replaceMessagesIfChanged(toUiMessages(msgs), true);
      setConversationId(id);
      setSessionListTitle(t);
      onTitleChange?.(t);
      setAttachmentError("");
    } catch { /* ignore */ }
  }

  async function handleDeleteConversation(id: string) {
    // Optimistic removal so the UI responds immediately
    const remaining = recentChats.filter((c) => c.id !== id);
    setRecentChats(remaining);

    const wasActive = id === conversationId;
    if (wasActive) {
      if (remaining.length > 0) {
        await handleLoadConversation(remaining[0].id, remaining[0].title);
      } else {
        setConversationId(null);
        replaceMessagesIfChanged([createWelcomeMessage()], true);
        setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
        onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
        void clearDraftAttachments();
      }
    }

    try {
      await deleteConversation(id);
    } catch { /* ignore — already removed from UI */ }
  }

  useEffect(() => {
    if (!shouldAutoScrollRef.current) {
      return;
    }
    scrollMessageListToBottom();
  }, [messages]);

  useEffect(() => {
    return () => {
      if (streamPumpRef.current !== null) {
        window.clearInterval(streamPumpRef.current);
      }
    };
  }, []);

  useEffect(() => {
    try {
      localStorage.setItem(CHAT_SIDEBAR_STATE_KEY, isConversationPanelCollapsed ? "1" : "0");
    } catch {
      /* ignore persistence failures */
    }
  }, [isConversationPanelCollapsed]);

  useEffect(() => {
    if (!isTauri()) {
      return;
    }
    let cancelled = false;
    let unlistenDrop: (() => void) | null = null;
    let unlistenEnter: (() => void) | null = null;
    let unlistenLeave: (() => void) | null = null;

    void (async () => {
      unlistenEnter = await listen<DragDropPayload>(TauriEvent.DRAG_ENTER, () => {
        if (!cancelled) {
          setIsDragActive(true);
        }
      });
      unlistenLeave = await listen<DragDropPayload>(TauriEvent.DRAG_LEAVE, () => {
        if (!cancelled) {
          setIsDragActive(false);
        }
      });
      unlistenDrop = await listen<DragDropPayload>(TauriEvent.DRAG_DROP, async (event) => {
        if (cancelled) {
          return;
        }
        setIsDragActive(false);
        const paths = Array.isArray(event.payload?.paths) ? event.payload.paths : [];
        if (paths.length > 0) {
          await addLocalPathsToDraft(paths);
        }
      });
    })();

    return () => {
      cancelled = true;
      void unlistenDrop?.();
      void unlistenEnter?.();
      void unlistenLeave?.();
    };
  }, [conversationId]);

  async function handleSend(messageOverride?: string) {
    const trimmed = (messageOverride ?? draft).trim();
    if ((!trimmed && draftAttachments.length === 0) || isSending || isUploadingAttachments) {
      return;
    }
    shouldAutoScrollRef.current = true;
    const sendingAttachments = draftAttachments;
    const sendingAttachmentIds = sendingAttachments.map((attachment) => attachment.id);
    const titleSeed = trimmed || sendingAttachments[0]?.filename || "Attachment chat";

    setIsSending(true);
    answerStartedRef.current = false;
    turnClosedRef.current = false;
    onTypingChange?.(false);
    onThinkingChange?.(true);
    const now = Date.now();
    const sentAt = new Date(now).toISOString();
    const initialThinkingProgress = createInitialThinkingProgress(sentAt);
    const nextUserMessage: ChatMessageItem = {
      id: `user-${now}`,
      role: "user",
      content: trimmed || "Analyze the attached files.",
      created_at: sentAt,
      attachments: sendingAttachments,
      kind: "text",
    };
    const commentaryMessageId = `assistant-commentary-${now + 1}`;
    const assistantMessageId = `assistant-final-${now + 2}`;
    const traceStartedAt = sentAt;
    let latestProgressSnapshot: ThinkingProgress | undefined = initialThinkingProgress;
    streamMessageIdRef.current = assistantMessageId;
    streamBufferRef.current = "";
    setMessages((current) => {
      const withoutWelcome = current.filter((m) => m.id !== WELCOME_MESSAGE_ID);
      return [
        ...withoutWelcome,
        nextUserMessage,
        {
          id: commentaryMessageId,
          role: "assistant",
          content: initialThinkingProgress.summary,
          created_at: sentAt,
          kind: "thinking",
          progress: initialThinkingProgress,
        },
      ];
    });
    if (!messageOverride) {
      setDraft("");
    }
    setDraftAttachments([]);
    setAttachmentError("");

    /** Only the first reply that attaches a conversation should set the sidebar title (not every turn). */
    const isFirstBoundSend = conversationId === null;

    try {
      const response = await sendDesktopMessageStream(trimmed, (event) => {
        if (event.type === "commentary" && event.text) {
          if (answerStartedRef.current || turnClosedRef.current) {
            return;
          }
          setMessages((current) => {
            const existing = current.find((item) => item.id === commentaryMessageId);
            if (existing) {
              return current.map((item) =>
                item.id === commentaryMessageId
                  ? item.kind === "thinking" && item.progress
                    ? {
                        ...item,
                        kind: "thinking",
                        content: event.text || item.content,
                        progress: {
                          ...item.progress,
                          summary: event.text || item.progress.summary,
                          last_updated_at: new Date().toISOString(),
                        },
                      }
                    : { ...item, kind: "text", content: event.text || item.content }
                  : item,
              );
            }
            return [
              ...current,
              {
                id: commentaryMessageId,
                role: "assistant",
                content: event.text || "",
                created_at: new Date().toISOString(),
                kind: "text",
              },
            ];
          });
        }

        if (isProgressEventType(event.type) && event.progress) {
          if (event.type === "progress-done") {
            return;
          }
          if (answerStartedRef.current || turnClosedRef.current) {
            return;
          }
          const progress = event.progress;
          latestProgressSnapshot = progress;
          setMessages((current) => {
            const existing = current.find((item) => item.id === commentaryMessageId);
            if (existing) {
              return current.map((item) =>
                item.id === commentaryMessageId
                  ? {
                      ...item,
                      kind: "thinking",
                      content: progress.summary || item.content,
                      progress,
                    }
                  : item,
              );
            }
            return [
              ...current,
              {
                id: commentaryMessageId,
                role: "assistant",
                content: progress.summary,
                created_at: new Date().toISOString(),
                kind: "thinking",
                progress,
              },
            ];
          });
        }

        if (event.type === "delta") {
          if (!answerStartedRef.current) {
            answerStartedRef.current = true;
            onAnswerStart?.();
            setMessages((current) => current.filter((item) => item.id !== commentaryMessageId));
          }
          streamBufferRef.current += event.text || "";
          ensureStreamPump();
        }

        if (event.type === "done") {
          turnClosedRef.current = true;
          suppressConversationRefreshUntilRef.current = 0;
          flushStreamBuffer();
          const normalizedHistory = attachWorkflowEventsToLastAssistant(
            event.history?.length ? toUiMessages(event.history) : [],
            event.runtime?.workflow_events || [],
          );
          const completedAt = new Date().toISOString();
          const elapsedMs = Math.max(0, Date.parse(completedAt) - Date.parse(traceStartedAt));
          const runtime = event.runtime;
          const debugTrace: ChatDebugTrace = {
            lane_label:
              latestProgressSnapshot?.lane_label?.trim() ||
              (runtime?.lane === "slow" ? "Slow brain" : runtime?.lane === "fast" ? "Fast brain" : "Working"),
            reason_label:
              latestProgressSnapshot?.reason_label?.trim() ||
              runtime?.triage_reason?.trim() ||
              "Process completed",
            triage_reason: runtime?.triage_reason?.trim() || latestProgressSnapshot?.triage_reason?.trim() || "",
            turn_status: runtime?.turn_status?.trim() || "",
            model_label: runtime?.model_label?.trim() || "",
            process_id: runtime?.process_id?.trim() || "",
            attempt_count: runtime?.attempt_count ?? 0,
            skipped_models: runtime?.skipped_models || [],
            failed_attempts: runtime?.failed_attempts || [],
            started_at: latestProgressSnapshot?.started_at || traceStartedAt,
            completed_at: completedAt,
            elapsed_ms: elapsedMs,
            summary: latestProgressSnapshot?.summary?.trim() || "",
          };
          const persistedAssistantMessage = [...normalizedHistory]
            .reverse()
            .find((item) => item.role === "assistant");
          if (persistedAssistantMessage?.id) {
            storeDebugTrace(persistedAssistantMessage.id, debugTrace);
          }
          storeDebugTrace(assistantMessageId, debugTrace);
          const cid = event.conversation_id;
          const titleFromStream = event.conversation_title?.trim();
          const looksPlaceholder =
            !titleFromStream ||
            titleFromStream === DEFAULT_CONVERSATION_TITLE ||
            titleFromStream.toLowerCase() === "new chat";
          const effectiveTitle = (looksPlaceholder ? titleSeed.slice(0, 88) : titleFromStream).trim();
          if (cid && effectiveTitle && isFirstBoundSend) {
            const t = normalizeConversationTitle(effectiveTitle);
            setRecentChats((prev) => {
              const has = prev.some((c) => c.id === cid);
              if (!has) {
                return [{ id: cid, title: t, updated_at: new Date().toISOString(), has_unread: false }, ...prev];
              }
              return prev.map((c) => (c.id === cid ? { ...c, title: t } : c));
            });
            if (conversationId === null || conversationId === cid) {
              setSessionListTitle(t);
              onTitleChange?.(t);
            }
          }
          // Capture the conversation ID assigned by the backend (first send creates one)
          if (cid) {
            setConversationId(cid);
          }
          if (normalizedHistory.length) {
            replaceMessagesIfChanged(normalizedHistory, true);
            return;
          }
          setMessages((current) => {
            const withoutCommentary = current.filter((item) => item.id !== commentaryMessageId);
            const existing = current.find((item) => item.id === assistantMessageId);
            if (existing) {
              return withoutCommentary.map((item) =>
                item.id === assistantMessageId
                  ? { ...item, content: event.reply || item.content, debugTrace }
                  : item,
              );
            }
            return [
              ...withoutCommentary,
              {
                id: assistantMessageId,
                role: "assistant",
                content: event.reply || "",
                created_at: new Date().toISOString(),
                kind: "text",
                debugTrace,
              },
            ];
          });
        }

        if (event.type === "error") {
          turnClosedRef.current = true;
          suppressConversationRefreshUntilRef.current = Date.now() + 30_000;
          flushStreamBuffer();
          setMessages((current) => {
            const withoutCommentary = current.filter((item) => item.id !== commentaryMessageId);
            const existing = current.find((item) => item.id === assistantMessageId);
            if (existing) {
              const existingContent = existing.content.trim();
              return withoutCommentary.map((item) =>
                item.id === assistantMessageId
                  ? {
                      ...item,
                      content: existingContent || event.message || "Could not complete the response.",
                    }
                  : item,
              );
            }
            return [
              ...withoutCommentary,
              {
                id: assistantMessageId,
                role: "assistant",
                content: event.message || "Could not complete the response.",
                created_at: new Date().toISOString(),
                kind: "text",
              },
            ];
          });
        }
      }, "desktop-user", conversationId ?? undefined, sendingAttachmentIds);

      // Refresh recent chats — use stream response id (first send had conversationId null in closure)
      const applyList = (chatsRaw: Awaited<ReturnType<typeof listConversations>>) => {
        const chats = withNormalizedConversationTitles(chatsRaw);
        const effectiveId = response.conversation_id ?? conversationId;
        if (!isFirstBoundSend) {
          setRecentChats(chats);
          const updated = effectiveId ? chats.find((c) => c.id === effectiveId) : undefined;
          if (updated) {
            const t = normalizeConversationTitle(updated.title);
            setSessionListTitle(t);
            onTitleChange?.(t);
          }
          return;
        }
        const rawTitle = response.conversation_title?.trim();
        const looksPlaceholder =
          !rawTitle ||
          rawTitle === DEFAULT_CONVERSATION_TITLE ||
          rawTitle.toLowerCase() === "new chat";
        const streamTitle = looksPlaceholder ? titleSeed.slice(0, 88).trim() : rawTitle;
        let merged = chats;
        if (effectiveId && streamTitle) {
          const t = normalizeConversationTitle(streamTitle);
          const idx = chats.findIndex((c) => c.id === effectiveId);
          merged =
            idx === -1
              ? [{ id: effectiveId, title: t, updated_at: new Date().toISOString(), has_unread: false }, ...chats]
              : chats.map((c) => (c.id === effectiveId ? { ...c, title: t } : c));
        }
        setRecentChats(merged);
        const updated = effectiveId ? merged.find((c) => c.id === effectiveId) : undefined;
        if (updated) {
          const t = normalizeConversationTitle(updated.title);
          setSessionListTitle(t);
          onTitleChange?.(t);
        }
      };
      listConversations("desktop-user", conversationSearch).then(applyList).catch(() => {});
      // The stream "done" event is the authoritative source for the final assistant row.
      setAttachmentError("");
    } catch (error) {
      turnClosedRef.current = true;
      suppressConversationRefreshUntilRef.current = Date.now() + 30_000;
      const message = error instanceof Error ? error.message : "Could not complete the response.";
      setDraftAttachments(sendingAttachments);
      setAttachmentError(message);
      setMessages((current) => {
        const withoutCommentary = current.filter((item) => item.id !== commentaryMessageId);
        const existing = current.find((item) => item.id === assistantMessageId);
        if (existing) {
          const existingContent = existing.content.trim();
          return withoutCommentary.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: existingContent || message }
              : item,
          );
        }
        return [
          ...withoutCommentary,
          {
            id: assistantMessageId,
            role: "assistant",
            content: message,
            created_at: new Date().toISOString(),
            kind: "text",
          },
        ];
      });
    } finally {
      flushStreamBuffer();
      // Remove the pending bubble if it never received a commentary/progress event
      setMessages((current) => current.filter((item) => item.kind !== "pending"));
      onThinkingChange?.(false);
      setIsSending(false);
    }
  }

  async function handlePendingActionDecision(actionId: string, decision: "approve" | "reject") {
    if (isSending || isUploadingAttachments) {
      return;
    }
    shouldAutoScrollRef.current = true;
    setIsSending(true);
    answerStartedRef.current = false;
    turnClosedRef.current = false;
    onTypingChange?.(false);
    onThinkingChange?.(true);
    setAttachmentError("");
    const now = Date.now();
    const sentAt = new Date(now).toISOString();
    const commentaryMessageId = `assistant-commentary-${now + 1}`;
    const assistantMessageId = `assistant-final-${now + 2}`;
    const initialThinkingProgress = createInitialThinkingProgress(sentAt);
    let latestProgressSnapshot: ThinkingProgress | undefined = initialThinkingProgress;
    const traceStartedAt = sentAt;
    streamMessageIdRef.current = assistantMessageId;
    streamBufferRef.current = "";
    setMessages((current) => [
      ...current,
      {
        id: commentaryMessageId,
        role: "assistant",
        content: initialThinkingProgress.summary,
        created_at: sentAt,
        kind: "thinking",
        progress: initialThinkingProgress,
      },
    ]);

    try {
      const response = await sendDesktopMessageStream(
        "",
        (event) => {
          if (event.type === "commentary" && event.text) {
            if (answerStartedRef.current || turnClosedRef.current) {
              return;
            }
            setMessages((current) =>
              current.map((item) =>
                item.id === commentaryMessageId
                  ? item.kind === "thinking" && item.progress
                    ? {
                        ...item,
                        content: event.text || item.content,
                        progress: {
                          ...item.progress,
                          summary: event.text || item.progress.summary,
                          last_updated_at: new Date().toISOString(),
                        },
                      }
                    : { ...item, content: event.text || item.content }
                  : item,
              ),
            );
          }
          if (isProgressEventType(event.type) && event.progress) {
            if (event.type === "progress-done") {
              return;
            }
            if (answerStartedRef.current || turnClosedRef.current) {
              return;
            }
            latestProgressSnapshot = event.progress;
            setMessages((current) =>
              current.map((item) =>
                item.id === commentaryMessageId
                  ? {
                      ...item,
                      kind: "thinking",
                      content: event.progress?.summary || item.content,
                      progress: event.progress,
                    }
                  : item,
              ),
            );
          }
          if (event.type === "delta") {
            if (!answerStartedRef.current) {
              answerStartedRef.current = true;
              onAnswerStart?.();
              setMessages((current) => current.filter((item) => item.id !== commentaryMessageId));
            }
            streamBufferRef.current += event.text || "";
            ensureStreamPump();
          }
          if (event.type === "done") {
            turnClosedRef.current = true;
            suppressConversationRefreshUntilRef.current = 0;
            flushStreamBuffer();
            const normalizedHistory = attachWorkflowEventsToLastAssistant(
              event.history?.length ? toUiMessages(event.history) : [],
              event.runtime?.workflow_events || [],
            );
            const completedAt = new Date().toISOString();
            const elapsedMs = Math.max(0, Date.parse(completedAt) - Date.parse(traceStartedAt));
            const runtime = event.runtime;
            const debugTrace: ChatDebugTrace = {
              lane_label:
                latestProgressSnapshot?.lane_label?.trim() ||
                (runtime?.lane === "slow" ? "Slow brain" : runtime?.lane === "fast" ? "Fast brain" : "Working"),
              reason_label:
                latestProgressSnapshot?.reason_label?.trim() ||
                runtime?.triage_reason?.trim() ||
                "Process completed",
              triage_reason: runtime?.triage_reason?.trim() || latestProgressSnapshot?.triage_reason?.trim() || "",
              turn_status: runtime?.turn_status?.trim() || "",
              model_label: runtime?.model_label?.trim() || "",
              process_id: runtime?.process_id?.trim() || "",
              attempt_count: runtime?.attempt_count ?? 0,
              skipped_models: runtime?.skipped_models || [],
              failed_attempts: runtime?.failed_attempts || [],
              started_at: latestProgressSnapshot?.started_at || traceStartedAt,
              completed_at: completedAt,
              elapsed_ms: elapsedMs,
              summary: latestProgressSnapshot?.summary?.trim() || "",
            };
            const persistedAssistantMessage = [...normalizedHistory].reverse().find((item) => item.role === "assistant");
            if (persistedAssistantMessage?.id) {
              storeDebugTrace(persistedAssistantMessage.id, debugTrace);
            }
            storeDebugTrace(assistantMessageId, debugTrace);
            setMessages((current) => {
              const cleaned = current.filter((item) => item.id !== commentaryMessageId && item.id !== assistantMessageId);
              const withMetadata = applyStoredDebugTraces(mergeMessageMetadata(cleaned, normalizedHistory), debugTraceByMessageIdRef.current);
              return withMetadata;
            });
            if (event.conversation_id) {
              setConversationId(event.conversation_id);
            }
          }
        },
        "desktop-user",
        conversationId ?? undefined,
        [],
        { id: actionId, decision },
      );
      const effectiveId = response.conversation_id ?? conversationId;
      if (effectiveId) {
        setConversationId(effectiveId);
      }
    } catch (error) {
      const errorText = error instanceof Error ? error.message : String(error);
      const statusUpdate = inferApprovalStatusFromError(errorText);
      const effectiveId = conversationId;
      if (effectiveId) {
        try {
          const refreshed = await getConversationMessages(effectiveId);
          const refreshedUi = applyStoredDebugTraces(
            mergeMessageMetadata(currentMessagesRef.current, toUiMessages(refreshed)),
            debugTraceByMessageIdRef.current,
          );
          setMessages(refreshedUi);
        } catch {
          setMessages((current) => {
            const cleaned = current.filter((item) => item.id !== commentaryMessageId && item.id !== assistantMessageId);
            return statusUpdate
              ? markApprovalMessageStatus(cleaned, actionId, statusUpdate.status, statusUpdate.label)
              : cleaned;
          });
        }
      } else {
        setMessages((current) => {
          const cleaned = current.filter((item) => item.id !== commentaryMessageId && item.id !== assistantMessageId);
          return statusUpdate
            ? markApprovalMessageStatus(cleaned, actionId, statusUpdate.status, statusUpdate.label)
            : cleaned;
        });
      }
      if (!statusUpdate) {
        setMessages((current) => [
          ...current,
          {
            id: `assistant-error-${Date.now()}`,
            role: "assistant",
            content: errorText,
            created_at: new Date().toISOString(),
            kind: "text",
          },
        ]);
      }
    } finally {
      flushStreamBuffer();
      turnClosedRef.current = true;
      setIsSending(false);
      onThinkingChange?.(false);
    }
  }

  async function handleWorkflowApprovalDecision(details: WorkflowApprovalDetails, decision: "approve" | "reject") {
    if (isSending || isUploadingAttachments) {
      return;
    }
    setIsSending(true);
    onThinkingChange?.(true);
    try {
      const result = await decideWorkflowRequest(details.runId, details.requestId, decision);
      const status = result.status || (decision === "approve" ? "approved" : "rejected");
      setMessages((current) =>
        markWorkflowApprovalStatus(
          current,
          details.requestId,
          status,
          workflowStatusLabel(status),
        ),
      );
      if (result.reply?.trim()) {
        setMessages((current) => [
          ...current,
          {
            id: `assistant-workflow-result-${Date.now()}`,
            role: "assistant",
            content: result.reply?.trim() || "",
            created_at: new Date().toISOString(),
            kind: "text",
            workflow_events: Array.isArray(result.events) ? result.events : [],
          },
        ]);
      }
      if (conversationId) {
        try {
          const refreshed = await getConversationMessages(conversationId);
          const refreshedUi = applyStoredDebugTraces(
            mergeMessageMetadata(currentMessagesRef.current, toUiMessages(refreshed)),
            debugTraceByMessageIdRef.current,
          );
          setMessages((current) => mergeMessageMetadata(refreshedUi, current));
        } catch {
          /* keep local status update */
        }
      }
    } catch (error) {
      setMessages((current) => [
        ...current,
        {
          id: `assistant-error-${Date.now()}`,
          role: "assistant",
          content: error instanceof Error ? error.message : String(error),
          created_at: new Date().toISOString(),
          kind: "text",
        },
      ]);
    } finally {
      setIsSending(false);
      onThinkingChange?.(false);
    }
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      void handleSend();
    }
  }

  const conversationRows = useMemo((): ConversationSummary[] => {
    const draft: ConversationSummary = {
      id: DRAFT_SESSION_ID,
      title: normalizeConversationTitle(sessionListTitle),
      updated_at: new Date().toISOString(),
      has_unread: false,
    };
    if (!conversationSearch.trim() && conversationId === null) {
      return [draft, ...recentChats];
    }
    return recentChats;
  }, [conversationId, conversationSearch, recentChats, sessionListTitle]);

  const onlyWelcomeGreeting =
    messages.length === 1 && messages[0]?.id === WELCOME_MESSAGE_ID;

  const activePendingSensitiveActionId = useMemo(() => {
    for (let index = messages.length - 1; index >= 0; index -= 1) {
      const message = messages[index];
      if (message.role !== "assistant") {
        continue;
      }
      const details = parsePendingSensitiveAction(
        message.content,
        message.approval_status,
        message.approval_status_label,
      );
      if (details && details.approvalStatus === "pending") {
        return details.actionId;
      }
    }
    return null;
  }, [messages]);

  /** Draft session before the first user message (new conversation, nothing sent yet). */
  const hasUserMessage = messages.some((m) => m.role === "user");
  const isSearchingConversations = conversationSearch.trim().length > 0;
  const searchSummary = isSearchingConversations
    ? `${conversationRows.length} result${conversationRows.length === 1 ? "" : "s"}`
    : `${recentChats.length} saved`;

  return (
    <section className={`chat-layout${isConversationPanelCollapsed ? " chat-layout-sidebar-collapsed" : ""}`}>
      <div
        className={`chat-panel card${isDragActive ? " chat-panel-dragging" : ""}`}
        onDragOver={(event) => {
          event.preventDefault();
          setIsDragActive(true);
        }}
        onDragLeave={(event) => {
          if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
            return;
          }
          setIsDragActive(false);
        }}
        onDrop={(event) => {
          event.preventDefault();
          setIsDragActive(false);
          void handleDomFileDrop(event.dataTransfer.files);
        }}
      >
        <div
          className="message-list"
          ref={messageListRef}
          onScroll={() => {
            shouldAutoScrollRef.current = isMessageListNearBottom();
          }}
        >
          {messages.map((message, index) => {
              const heartbeatDetails =
                message.role === "assistant"
                  ? parseHeartbeatConfirmation(
                      message.content,
                      message.approval_status,
                      message.approval_status_label,
                    )
                  : null;
              const pendingSensitiveAction =
                message.role === "assistant"
                  ? parsePendingSensitiveAction(
                      message.content,
                      message.approval_status,
                      message.approval_status_label,
                    )
                  : null;
              const workflowApproval =
                message.role === "assistant"
                  ? workflowApprovalFromEvents(
                      message.workflow_events,
                      message.approval_status,
                      message.approval_status_label,
                    )
                  : null;

            if (message.kind === "thinking" && message.role === "assistant") {
              return <ThinkingCard key={message.id} message={message} />;
            }

            if (message.kind === "pending") {
              return (
                <article key={message.id} className="message-bubble message-assistant">
                  <span className="message-role">AzulClaw</span>
                  <p style={{ display: "flex", alignItems: "center", gap: "5px", margin: 0, padding: "4px 0" }}>
                    <span className="message-wave-dot" />
                    <span className="message-wave-dot" />
                    <span className="message-wave-dot" />
                  </p>
                </article>
              );
            }

              if (heartbeatDetails) {
                if (heartbeatDetails.approvalStatus !== "pending") {
                  return (
                    <article
                      key={message.id}
                      className={`message-bubble message-${message.role}`}
                    >
                      <div className="message-meta">
                        <span className="message-role">AzulClaw</span>
                        {formatMessageTimestamp(message.created_at) ? (
                          <time className="message-timestamp" dateTime={message.created_at}>
                            {formatMessageTimestamp(message.created_at)}
                          </time>
                        ) : null}
                      </div>
                      <CompactApprovalNotice
                        title={heartbeatDetails.title || heartbeatDetails.name}
                        status={heartbeatDetails.approvalStatus}
                        statusLabel={heartbeatDetails.approvalStatusLabel}
                      />
                    </article>
                  );
                }
                return (
                  <HeartbeatConfirmationCard
                    key={message.id}
                    details={heartbeatDetails}
                  disabled={isSending}
                  onCreate={() => void handlePendingActionDecision(heartbeatDetails.actionId, "approve")}
                  onCancel={() => void handlePendingActionDecision(heartbeatDetails.actionId, "reject")}
                />
              );
            }

              if (pendingSensitiveAction) {
                const isActivePendingSensitiveAction =
                  pendingSensitiveAction.approvalStatus === "pending" &&
                  pendingSensitiveAction.actionId === activePendingSensitiveActionId;
                if (pendingSensitiveAction.approvalStatus !== "pending") {
                  return (
                    <article
                      key={message.id}
                      className="message-bubble message-assistant"
                    >
                      <div className="message-meta">
                        <span className="message-role">AzulClaw</span>
                        {formatMessageTimestamp(message.created_at) ? (
                          <time className="message-timestamp" dateTime={message.created_at}>
                            {formatMessageTimestamp(message.created_at)}
                          </time>
                        ) : null}
                      </div>
                      {pendingSensitiveAction.strippedContent ? (
                        <MessageContent content={pendingSensitiveAction.strippedContent} role={message.role} />
                      ) : null}
                      <CompactApprovalNotice
                        title={pendingSensitiveAction.title}
                        status={pendingSensitiveAction.approvalStatus}
                        statusLabel={pendingSensitiveAction.approvalStatusLabel}
                      />
                    </article>
                  );
                }
                return (
                  <article
                    key={message.id}
                    className="message-bubble message-assistant message-heartbeat-card"
                  >
                  <div className="message-meta">
                    <span className="message-role">AzulClaw</span>
                    {formatMessageTimestamp(message.created_at) ? (
                      <time className="message-timestamp" dateTime={message.created_at}>
                        {formatMessageTimestamp(message.created_at)}
                      </time>
                    ) : null}
                  </div>
                  {pendingSensitiveAction.strippedContent ? (
                    <MessageContent content={pendingSensitiveAction.strippedContent} role={message.role} />
                  ) : null}
                    <PendingSensitiveActionCard
                      details={pendingSensitiveAction}
                      disabled={isSending || !isActivePendingSensitiveAction}
                      onApprove={() => void handlePendingActionDecision(pendingSensitiveAction.actionId, "approve")}
                      onReject={() => void handlePendingActionDecision(pendingSensitiveAction.actionId, "reject")}
                    />
                  </article>
                );
            }

              if (workflowApproval) {
                if (workflowApproval.approvalStatus !== "pending") {
                  return (
                    <article key={message.id} className="message-bubble message-assistant">
                      <div className="message-meta">
                        <span className="message-role">AzulClaw</span>
                        {formatMessageTimestamp(message.created_at) ? (
                          <time className="message-timestamp" dateTime={message.created_at}>
                            {formatMessageTimestamp(message.created_at)}
                          </time>
                        ) : null}
                      </div>
                      <MessageContent content={message.content} role={message.role} />
                      <CompactApprovalNotice
                        title={workflowApproval.title}
                        status={workflowApproval.approvalStatus}
                        statusLabel={workflowApproval.approvalStatusLabel}
                      />
                    </article>
                  );
                }
                return (
                  <article
                    key={message.id}
                    className="message-bubble message-assistant message-heartbeat-card"
                  >
                    <div className="message-meta">
                      <span className="message-role">AzulClaw</span>
                      {formatMessageTimestamp(message.created_at) ? (
                        <time className="message-timestamp" dateTime={message.created_at}>
                          {formatMessageTimestamp(message.created_at)}
                        </time>
                      ) : null}
                    </div>
                    <MessageContent content={message.content} role={message.role} />
                    <WorkflowApprovalCard
                      details={workflowApproval}
                      disabled={isSending}
                      onApprove={() => void handleWorkflowApprovalDecision(workflowApproval, "approve")}
                      onReject={() => void handleWorkflowApprovalDecision(workflowApproval, "reject")}
                    />
                  </article>
                );
              }

            return (
              <article
                key={message.id}
                className={`message-bubble message-${message.role}`}
              >
                <div className="message-meta">
                  <span className="message-role">
                    {message.role === "user" ? "You" : "AzulClaw"}
                  </span>
                  {formatMessageTimestamp(message.created_at) ? (
                    <time className="message-timestamp" dateTime={message.created_at}>
                      {formatMessageTimestamp(message.created_at)}
                    </time>
                  ) : null}
                </div>
                <MessageContent content={message.content} role={message.role} />
                <AttachmentList attachments={message.attachments || []} compact />
                {message.role === "assistant" && message.debugTrace ? (
                  <AssistantTraceDisclosure trace={message.debugTrace} />
                ) : null}
              </article>
            );
          })}
          <div ref={bottomRef} />
        </div>

        <div className="composer">
          <div className="composer-wrapper">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              hidden
              accept=".png,.jpg,.jpeg,.gif,.webp,.pdf,.docx,.txt,.md,.csv"
              onChange={(event) => {
                const files = Array.from(event.target.files || []);
                if (files.length > 0) {
                  void addFilesToDraft(files);
                }
                event.currentTarget.value = "";
              }}
            />
            <AttachmentList attachments={draftAttachments} onRemove={(attachmentId) => void handleRemoveDraftAttachment(attachmentId)} />
            <label className="composer-field">
              <span className="sr-only">Message AzulClaw</span>
              <textarea
                ref={composerInputRef}
                placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
                rows={1}
                value={draft}
                onChange={(event) => {
                  const val = event.target.value;
                  setDraft(val);
                  onTypingChange?.(val.trim().length > 0);
                }}
                onKeyDown={handleKeyDown}
                onPaste={(event) => { void handleClipboardPaste(event); }}
              />
            </label>
            {attachmentError ? (
              <p className="composer-attachment-error">{attachmentError}</p>
            ) : null}
            <div className="composer-bottom">
              <div className="composer-actions">
                <button
                  type="button"
                  className="ghost-button-mini"
                  title="Attach a local file"
                  aria-label="Attach file"
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isSending || isUploadingAttachments}
                >
                  {isUploadingAttachments ? "Adding..." : "File"}
                </button>
              </div>
              <button
                type="button"
                className={`composer-send-btn ${isSending ? "composer-send-btn-loading" : ""}`}
                onClick={() => void handleSend()}
                disabled={isSending || isUploadingAttachments || (!draft.trim() && draftAttachments.length === 0)}
                aria-busy={isSending}
              >
                Send
              </button>
            </div>
          </div>
          <div className="composer-footer">
            <span className="hint-text">AzulClaw can make mistakes. Consider verifying important information.</span>
          </div>
        </div>
      </div>

      <aside className={`context-panel card${isConversationPanelCollapsed ? " context-panel-collapsed" : ""}`}>
        <div className="context-panel-header">
          <div className="context-panel-header-copy">
            <p className="eyebrow context-panel-title-eyebrow">Conversations</p>
            {!isConversationPanelCollapsed ? (
              <span className="context-panel-title-meta">{searchSummary}</span>
            ) : null}
          </div>
          <button
            type="button"
            className={`context-panel-toggle${isConversationPanelCollapsed ? " context-panel-toggle-collapsed" : ""}`}
            aria-label={isConversationPanelCollapsed ? "Expand conversations panel" : "Collapse conversations panel"}
            aria-expanded={!isConversationPanelCollapsed}
            title={isConversationPanelCollapsed ? "Expand conversations" : "Collapse conversations"}
            onClick={() => setIsConversationPanelCollapsed((current) => !current)}
          >
            <span
              className={`context-panel-toggle-chevron${isConversationPanelCollapsed ? " context-panel-toggle-chevron-collapsed" : ""}`}
              aria-hidden="true"
            />
          </button>
        </div>

        {isConversationPanelCollapsed ? (
          <div className="context-panel-collapsed-body">
            <div className="context-panel-collapsed-tally" aria-hidden="true">
              <span className="context-panel-collapsed-count">{conversationRows.length}</span>
              <span className="context-panel-collapsed-label">chats</span>
            </div>
            <button
              type="button"
              className="context-panel-collapsed-new"
              title="New conversation"
              aria-label="New conversation"
              disabled={onlyWelcomeGreeting || messages.length === 0}
              onClick={() => void handleNewChatRef.current()}
            >
              +
            </button>
          </div>
        ) : (
          <>
        <div className="context-search-minimal">
          <input
            type="search"
            className="search-conversations-input"
            value={conversationSearch}
            onChange={(event) => setConversationSearch(event.target.value)}
            placeholder="Search conversations..."
          />
        </div>

        {/* ── Recent conversations ─────────────────── */}
        <section className="context-section context-section-grow">
          <div className="context-section-heading">
            <p className="eyebrow context-section-eyebrow">
              <span>{isSearchingConversations ? "Matching conversations" : "Recent conversations"}</span>
              {isFetchingSearch && <span className="search-spinner" />}
            </p>
          </div>
          <div className="recent-chats-list">
            {conversationRows.length === 0 && conversationSearch.trim() ? (
              <p className="recent-chat-empty">No conversations match this search.</p>
            ) : conversationRows.map((c) => {
              const isDraft = c.id === DRAFT_SESSION_ID;
              const isActive =
                conversationId === null ? isDraft : c.id === conversationId;
              const showUnreadDot =
                !isDraft &&
                !isActive &&
                Boolean(unreadByConversationId[c.id]);
              const snippet = (c.snippet || "").trim();
              const relativeDate = formatRelativeDate(c.updated_at);
              return (
                <div
                  key={c.id}
                  className={`recent-chat-item${isActive ? " recent-chat-item-active" : ""}`}
                  onClick={() => {
                    if (isDraft) return;
                    void handleLoadConversation(c.id, normalizeConversationTitle(c.title));
                  }}
                >
                  <div className="recent-chat-info">
                    <div className="recent-chat-title-row">
                      {showUnreadDot ? <span className="recent-chat-unread-dot" aria-hidden="true" /> : null}
                      <span className="recent-chat-title">{normalizeConversationTitle(c.title)}</span>
                    </div>
                    {snippet ? (
                      <span className="recent-chat-snippet">{snippet}</span>
                    ) : null}
                    {isDraft ? (
                      !hasUserMessage ? (
                        <span className="recent-chat-date">just now</span>
                      ) : null
                    ) : relativeDate ? (
                      <span className="recent-chat-date">{relativeDate}</span>
                    ) : null}
                  </div>
                  {!isDraft && (
                    <button
                      type="button"
                      className="recent-chat-delete"
                      title="Delete conversation"
                      onClick={(e) => { e.stopPropagation(); void handleDeleteConversation(c.id); }}
                    >
                      ✕
                    </button>
                  )}
                </div>
              );
            })}
          </div>
        </section>

        <div className="context-panel-actions">
          <button
            type="button"
            className="new-chat-btn new-chat-btn-full"
            onClick={() => void handleNewChatRef.current()}
            disabled={messages.length === 0 || onlyWelcomeGreeting}
            title={onlyWelcomeGreeting || messages.length === 0 ? "Send a message first" : "Start a new conversation"}
          >
            {DEFAULT_CONVERSATION_TITLE}
          </button>
        </div>
          </>
        )}

      </aside>
    </section>
  );
}
