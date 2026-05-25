import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";

import {
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
import type { AttachmentSummary, ChatExchange, ConversationSummary, ThinkingProgress } from "../../lib/contracts";
import { isTauri } from "@tauri-apps/api/core";
import { listen, TauriEvent } from "@tauri-apps/api/event";
import { MessageContent } from "./MessageContent";
import i18n from "../../lib/i18n";

type ChatMessageItem = ChatExchange & {
  kind: "text" | "thinking" | "pending";
  progress?: ThinkingProgress;
};

type DragDropPayload = {
  paths?: string[];
};

type HeartbeatConfirmationDetails = {
  name: string;
  schedule: string;
  action: string;
  delivery: string;
};

/** Shown only in the UI until the user sends their first message (not persisted). */
const WELCOME_MESSAGE_ID = "welcome-greeting";

/** Synthetic row in “Recent conversations” for the unsaved draft session. */
const DRAFT_SESSION_ID = "__draft_session__";
const CHAT_SIDEBAR_STATE_KEY = "azul_chat_sidebar_collapsed";

function createWelcomeMessage(): ChatMessageItem {
  return {
    id: WELCOME_MESSAGE_ID,
    role: "assistant",
    content: i18n.t("chat.welcomeMessage"),
    created_at: new Date().toISOString(),
    kind: "text",
  };
}

function toUiMessages(items: ChatExchange[]): ChatMessageItem[] {
  return items.map((item) => ({ ...item, kind: "text" }));
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
      sameAttachments(item.attachments, other.attachments)
    );
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
    return i18n.t("chat.attachmentVisualAnalysis");
  }
  if (attachment.kind === "image") {
    return i18n.t("chat.attachmentImage");
  }
  if (attachment.page_count > 1) {
    return i18n.t("chat.attachmentPages", { count: attachment.page_count });
  }
  return attachment.kind === "text" ? i18n.t("chat.attachmentText") : i18n.t("chat.attachmentDocument");
}

function phaseStatusLabel(status: "pending" | "active" | "done") {
  if (status === "done") {
    return i18n.t("chat.phaseStatusDone");
  }
  if (status === "active") {
    return i18n.t("chat.phaseStatusActive");
  }
  return i18n.t("chat.phaseStatusPending");
}

function parseHeartbeatConfirmation(content: string): HeartbeatConfirmationDetails | null {
  const text = (content || "").trim();
  if (!text.startsWith("I can create this heartbeat:")) {
    return null;
  }

  const block = (label: string, nextLabels: string[]) => {
    const stops = [
      ...nextLabels.map((nextLabel) => `^${nextLabel}:\\s*`),
      "^Reply\\b",
      "(?![\\s\\S])",
    ].join("|");
    return text.match(new RegExp(`^${label}:\\s*([\\s\\S]*?)(?=${stops})`, "m"))?.[1]?.trim();
  };

  const name = block("Name", ["Schedule"])?.replace(/\s+/g, " ");
  const schedule = block("Schedule", ["Action"])?.replace(/^`|`$/g, "").trim();
  const action = block("Action", ["Delivery"]);
  const delivery = block("Delivery", [])?.replace(/\s+/g, " ") || "desktop chat";
  if (!name || !schedule || !action) {
    return null;
  }

  return { name, schedule, action, delivery };
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
  const { t } = useTranslation();
  return (
    <article className="message-bubble message-assistant message-heartbeat-card">
      <span className="message-role">{t("chat.assistant")}</span>
      <div className="heartbeat-confirm-card">
        <div className="heartbeat-confirm-head">
          <div>
            <p className="heartbeat-confirm-eyebrow">{t("chat.heartbeatDraft")}</p>
            <h3>{details.name}</h3>
          </div>
          <span className="heartbeat-confirm-schedule">{details.schedule}</span>
        </div>
        <div className="heartbeat-confirm-body">
          <div>
            <span>{t("chat.heartbeatAction")}</span>
            <p>{details.action}</p>
          </div>
          <div>
            <span>{t("chat.heartbeatDelivery")}</span>
            <p>{details.delivery}</p>
          </div>
        </div>
        <div className="heartbeat-confirm-actions">
          <button
            type="button"
            className="ghost-button"
            onClick={onCancel}
            disabled={disabled}
          >
            {t("common.cancel")}
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={onCreate}
            disabled={disabled}
          >
            {t("heartbeats.create")}
          </button>
        </div>
      </div>
    </article>
  );
}

function ThinkingCard({ message }: { message: ChatMessageItem }) {
  const { t } = useTranslation();
  const progress = message.progress;
  const [expanded, setExpanded] = useState(true);
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

  if (!progress) {
    return null;
  }

  const statusText =
    progress.active_count > 0
      ? t("chat.openSubtasks", { count: progress.active_count })
      : t("chat.processComplete");

  return (
    <article className="message-bubble message-assistant message-thinking">
      <div className="thinking-card">
        <div className="thinking-topline">
          <span className="message-role">{t("chat.assistant")}</span>
          <span className="thinking-badge">{progress.badge}</span>
        </div>

        <div className="thinking-header">
          <div>
            <h3>{message.content || progress.title}</h3>
            <p>{progress.title}</p>
          </div>
          <button
            type="button"
            className="thinking-toggle"
            onClick={() => setExpanded((current) => !current)}
          >
            {expanded ? t("chat.hide") : t("chat.expand")}
          </button>
        </div>

        <div className="thinking-meta">
          <span className={`thinking-spinner ${progress.active_count === 0 ? "thinking-spinner-done" : ""}`} />
          <span>{statusText}</span>
        </div>

        {expanded ? (
          <div className="thinking-phase-list">
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
      </div>
    </article>
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
  const { t } = useTranslation();
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
  const bottomRef = useRef<HTMLDivElement>(null);
  const messageListRef = useRef<HTMLDivElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const composerInputRef = useRef<HTMLTextAreaElement>(null);
  const shouldAutoScrollRef = useRef(true);
  const streamPumpRef = useRef<number | null>(null);
  const hasLoadedConversationsRef = useRef(false);
  const lastHandledExternalConversationRef = useRef(0);

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
      if (sameMessages(current, nextMessages)) {
        return current;
      }
      shouldAutoScrollRef.current = shouldAutoScroll;
      return nextMessages;
    });
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
    onTypingChange?.(false);
    onThinkingChange?.(true);
    const now = Date.now();
    const sentAt = new Date(now).toISOString();
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
          content: "",
          created_at: sentAt,
          kind: "pending",
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
          setMessages((current) => {
            const existing = current.find((item) => item.id === commentaryMessageId);
            if (existing) {
              return current.map((item) =>
                item.id === commentaryMessageId ? { ...item, kind: "text", content: event.text || item.content } : item,
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

        if (event.type === "progress" && event.progress) {
          const progress = event.progress;
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
          if (!answerStartedRef.current) { answerStartedRef.current = true; onAnswerStart?.(); }
          streamBufferRef.current += event.text || "";
          ensureStreamPump();
        }

        if (event.type === "done") {
          flushStreamBuffer();
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
          setMessages((current) => {
            const existing = current.find((item) => item.id === assistantMessageId);
            if (existing) {
              return current.map((item) =>
                item.id === assistantMessageId
                  ? { ...item, content: event.reply || item.content }
                  : item,
              );
            }
            return [
              ...current,
              {
                id: assistantMessageId,
                role: "assistant",
                content: event.reply || "",
                created_at: new Date().toISOString(),
                kind: "text",
              },
            ];
          });
        }

        if (event.type === "error") {
          flushStreamBuffer();
          setMessages((current) => {
            const existing = current.find((item) => item.id === assistantMessageId);
            if (existing) {
              return current.map((item) =>
                item.id === assistantMessageId
                  ? { ...item, content: event.message || "Could not complete the response." }
                  : item,
              );
            }
            return [
              ...current,
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
      if (response.reply) {
        flushStreamBuffer();
        setMessages((current) => {
          const existing = current.find((item) => item.id === assistantMessageId);
          if (existing) {
            return current.map((item) =>
              item.id === assistantMessageId
                ? { ...item, content: response.reply || item.content }
                : item,
            );
          }
          return [
            ...current,
            {
              id: assistantMessageId,
              role: "assistant",
              content: response.reply,
              created_at: new Date().toISOString(),
              kind: "text",
            },
          ];
        });
      }
      setAttachmentError("");
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not complete the response.";
      setDraftAttachments(sendingAttachments);
      setAttachmentError(message);
      setMessages((current) => {
        const existing = current.find((item) => item.id === assistantMessageId);
        if (existing) {
          return current.map((item) =>
            item.id === assistantMessageId
              ? { ...item, content: message }
              : item,
          );
        }
        return [
          ...current,
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

  /** Draft session before the first user message (new conversation, nothing sent yet). */
  const hasUserMessage = messages.some((m) => m.role === "user");
  const isSearchingConversations = conversationSearch.trim().length > 0;
  const searchSummary = isSearchingConversations
    ? `${conversationRows.length} ${conversationRows.length === 1 ? t("chat.result") : t("chat.results")}`
    : `${recentChats.length} ${t("chat.saved")}`;

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
          {messages.map((message) => {
            const heartbeatDetails =
              message.role === "assistant"
                ? parseHeartbeatConfirmation(message.content)
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
              return (
                <HeartbeatConfirmationCard
                  key={message.id}
                  details={heartbeatDetails}
                  disabled={isSending}
                  onCreate={() => void handleSend("yes, create it")}
                  onCancel={() => void handleSend("no")}
                />
              );
            }

            return (
              <article
                key={message.id}
                className={`message-bubble message-${message.role}`}
              >
                <div className="message-meta">
                  <span className="message-role">
                    {message.role === "user" ? t("chat.you") : t("chat.assistant")}
                  </span>
                  {formatMessageTimestamp(message.created_at) ? (
                    <time className="message-timestamp" dateTime={message.created_at}>
                      {formatMessageTimestamp(message.created_at)}
                    </time>
                  ) : null}
                </div>
                <MessageContent content={message.content} role={message.role} />
                <AttachmentList attachments={message.attachments || []} compact />
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
              <span className="sr-only">{t("chat.messageAzulClaw")}</span>
              <textarea
                ref={composerInputRef}
                placeholder={t("chat.typeMessage")}
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
                  title={t("chat.attachFile")}
                  aria-label={t("chat.attachFileLabel")}
                  onClick={() => fileInputRef.current?.click()}
                  disabled={isSending || isUploadingAttachments}
                >
                  {isUploadingAttachments ? t("chat.addingFile") : t("chat.file")}
                </button>
              </div>
              <button
                type="button"
                className={`composer-send-btn ${isSending ? "composer-send-btn-loading" : ""}`}
                onClick={() => void handleSend()}
                disabled={isSending || isUploadingAttachments || (!draft.trim() && draftAttachments.length === 0)}
                aria-busy={isSending}
              >
                {t("chat.send")}
              </button>
            </div>
          </div>
          <div className="composer-footer">
            <span className="hint-text">{t("chat.disclaimer")}</span>
          </div>
        </div>
      </div>

      <aside className={`context-panel card${isConversationPanelCollapsed ? " context-panel-collapsed" : ""}`}>
        <div className="context-panel-header">
          <div className="context-panel-header-copy">
            <p className="eyebrow context-panel-title-eyebrow">{t("chat.conversations")}</p>
            {!isConversationPanelCollapsed ? (
              <span className="context-panel-title-meta">{searchSummary}</span>
            ) : null}
          </div>
          <button
            type="button"
            className={`context-panel-toggle${isConversationPanelCollapsed ? " context-panel-toggle-collapsed" : ""}`}
            aria-label={isConversationPanelCollapsed ? t("chat.expandConversations") : t("chat.collapseConversations")}
            aria-expanded={!isConversationPanelCollapsed}
            title={isConversationPanelCollapsed ? t("chat.expandConversationsTitle") : t("chat.collapseConversationsTitle")}
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
              <span className="context-panel-collapsed-label">{t("chat.chats")}</span>
            </div>
            <button
              type="button"
              className="context-panel-collapsed-new"
              title={t("chat.newConversation")}
              aria-label={t("chat.newConversation")}
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
            placeholder={t("chat.searchConversations")}
          />
        </div>

        {/* ── Recent conversations ─────────────────── */}
        <section className="context-section context-section-grow">
          <div className="context-section-heading">
            <p className="eyebrow context-section-eyebrow">
              <span>{isSearchingConversations ? t("chat.matchingConversations") : t("chat.recentConversations")}</span>
              {isFetchingSearch && <span className="search-spinner" />}
            </p>
          </div>
          <div className="recent-chats-list">
            {conversationRows.length === 0 && conversationSearch.trim() ? (
              <p className="recent-chat-empty">{t("chat.noConversationsMatch")}</p>
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
                        <span className="recent-chat-date">{t("chat.justNow")}</span>
                      ) : null
                    ) : relativeDate ? (
                      <span className="recent-chat-date">{relativeDate}</span>
                    ) : null}
                  </div>
                  {!isDraft && (
                    <button
                      type="button"
                      className="recent-chat-delete"
                      title={t("chat.deleteConversation")}
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
            title={onlyWelcomeGreeting || messages.length === 0 ? t("chat.sendFirst") : t("chat.newConversation")}
          >
            {t("chat.newConversation")}
          </button>
        </div>
          </>
        )}

      </aside>
    </section>
  );
}
