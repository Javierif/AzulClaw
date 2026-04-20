import { useEffect, useMemo, useRef, useState } from "react";

import {
  deleteConversation,
  getConversationMessages,
  listConversations,
  sendDesktopMessageStream,
} from "../../lib/api";
import { Tooltip } from "../../components/Tooltip";
import {
  DEFAULT_CONVERSATION_TITLE,
  normalizeConversationTitle,
  withNormalizedConversationTitles,
} from "../../lib/conversation-copy";
import type { ChatExchange, ConversationSummary, ThinkingProgress } from "../../lib/contracts";

type ChatMessageItem = ChatExchange & {
  kind: "text" | "thinking" | "pending";
  progress?: ThinkingProgress;
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

function createWelcomeMessage(): ChatMessageItem {
  return {
    id: WELCOME_MESSAGE_ID,
    role: "assistant",
    content:
      "Hey there! I'm glad you're here. Tell me what you're working on, or ask me anything — I'm all ears. How can I help today?",
    kind: "text",
  };
}

function toUiMessages(items: ChatExchange[]): ChatMessageItem[] {
  return items.map((item) => ({ ...item, kind: "text" }));
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

function parseHeartbeatConfirmation(content: string): HeartbeatConfirmationDetails | null {
  const text = (content || "").trim();
  if (!text.startsWith("I can create this heartbeat:")) {
    return null;
  }

  const name = text.match(/^Name:\s*(.+)$/m)?.[1]?.trim();
  const schedule = text.match(/^Schedule:\s*`?([^`\n]+)`?$/m)?.[1]?.trim();
  const action = text.match(/^Action:\s*(.+)$/m)?.[1]?.trim();
  const delivery = text.match(/^Delivery:\s*(.+)$/m)?.[1]?.trim() || "desktop chat";
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
  return (
    <article className="message-bubble message-assistant message-heartbeat-card">
      <span className="message-role">AzulClaw</span>
      <div className="heartbeat-confirm-card">
        <div className="heartbeat-confirm-head">
          <div>
            <p className="heartbeat-confirm-eyebrow">Heartbeat draft</p>
            <h3>{details.name}</h3>
          </div>
          <span className="heartbeat-confirm-schedule">{details.schedule}</span>
        </div>
        <div className="heartbeat-confirm-body">
          <div>
            <span>Action</span>
            <p>{details.action}</p>
          </div>
          <div>
            <span>Delivery</span>
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
            Cancel
          </button>
          <button
            type="button"
            className="primary-button"
            onClick={onCreate}
            disabled={disabled}
          >
            Create heartbeat
          </button>
        </div>
      </div>
    </article>
  );
}

function ThinkingCard({ message }: { message: ChatMessageItem }) {
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
      ? `${progress.active_count} open subtasks`
      : "process complete";

  return (
    <article className="message-bubble message-assistant message-thinking">
      <div className="thinking-card">
        <div className="thinking-topline">
          <span className="message-role">AzulClaw</span>
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
            {expanded ? "Hide" : "Expand"}
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

export function ChatShell({
  onThinkingChange,
  onTypingChange,
  onAnswerStart,
  onTitleChange,
  onRegisterNewChat,
}: {
  onThinkingChange?: (thinking: boolean) => void;
  onTypingChange?: (typing: boolean) => void;
  onAnswerStart?: () => void;
  onTitleChange?: (title: string) => void;
  onRegisterNewChat?: (fn: () => void) => void;
}) {
  const [messages, setMessages] = useState<ChatMessageItem[]>([]);
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [recentChats, setRecentChats] = useState<ConversationSummary[]>([]);
  const [sessionListTitle, setSessionListTitle] = useState(DEFAULT_CONVERSATION_TITLE);
  const streamMessageIdRef = useRef("");
  const streamBufferRef = useRef("");
  const answerStartedRef = useRef(false);
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamPumpRef = useRef<number | null>(null);

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
        return [...current, { id: messageId, role: "assistant", content: slice, kind: "text" }];
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
        return [...current, { id: messageId, role: "assistant", content: pending, kind: "text" }];
      });
    }
    streamBufferRef.current = "";
    if (streamPumpRef.current !== null) {
      window.clearInterval(streamPumpRef.current);
      streamPumpRef.current = null;
    }
  }

  // On mount: load existing conversations; never create one here (lazy creation on first send)
  useEffect(() => {
    let cancelled = false;
    async function initConversation() {
      try {
        const chatsRaw = await listConversations();
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
          setMessages(toUiMessages(msgs));
        } else {
          setMessages([createWelcomeMessage()]);
          setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
          onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
        }
      } catch {
        setMessages([createWelcomeMessage()]);
        setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
        onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
      }
    }
    void initConversation();
    return () => { cancelled = true; };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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
        const chatsRaw = await listConversations();
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
        setMessages(toUiMessages(msgs));
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
  }, [conversationId, isSending, onTitleChange]);

  // Register the new-chat handler with the parent (topbar button)
  const handleNewChatRef = useRef<() => void>(() => {});
  const currentMessagesRef = useRef(messages);
  currentMessagesRef.current = messages;

  handleNewChatRef.current = () => {
    const msgs = currentMessagesRef.current;
    const onlyWelcome = msgs.length === 1 && msgs[0]?.id === WELCOME_MESSAGE_ID;
    if (msgs.length === 0 || onlyWelcome) return;
    setConversationId(null);
    setMessages([createWelcomeMessage()]);
    setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
    onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
  };

  useEffect(() => {
    onRegisterNewChat?.(() => void handleNewChatRef.current());
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleLoadConversation(id: string, title: string) {
    try {
      const msgs = await getConversationMessages(id);
      const t = normalizeConversationTitle(title);
      setMessages(toUiMessages(msgs));
      setConversationId(id);
      setSessionListTitle(t);
      onTitleChange?.(t);
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
        setMessages([createWelcomeMessage()]);
        setSessionListTitle(DEFAULT_CONVERSATION_TITLE);
        onTitleChange?.(DEFAULT_CONVERSATION_TITLE);
      }
    }

    try {
      await deleteConversation(id);
    } catch { /* ignore — already removed from UI */ }
  }

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  useEffect(() => {
    return () => {
      if (streamPumpRef.current !== null) {
        window.clearInterval(streamPumpRef.current);
      }
    };
  }, []);

  async function handleSend(messageOverride?: string) {
    const trimmed = (messageOverride ?? draft).trim();
    if (!trimmed || isSending) {
      return;
    }

    setIsSending(true);
    answerStartedRef.current = false;
    onTypingChange?.(false);
    onThinkingChange?.(true);
    const now = Date.now();
    const nextUserMessage: ChatMessageItem = {
      id: `user-${now}`,
      role: "user",
      content: trimmed,
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
          kind: "pending",
        },
      ];
    });
    if (!messageOverride) {
      setDraft("");
    }

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
          const effectiveTitle = (looksPlaceholder ? trimmed.slice(0, 88) : titleFromStream).trim();
          if (cid && effectiveTitle && isFirstBoundSend) {
            const t = normalizeConversationTitle(effectiveTitle);
            setRecentChats((prev) => {
              const has = prev.some((c) => c.id === cid);
              if (!has) {
                return [{ id: cid, title: t, updated_at: new Date().toISOString() }, ...prev];
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
                kind: "text",
              },
            ];
          });
        }
      }, "desktop-user", conversationId ?? undefined);

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
        const streamTitle = looksPlaceholder ? trimmed.slice(0, 88).trim() : rawTitle;
        let merged = chats;
        if (effectiveId && streamTitle) {
          const t = normalizeConversationTitle(streamTitle);
          const idx = chats.findIndex((c) => c.id === effectiveId);
          merged =
            idx === -1
              ? [{ id: effectiveId, title: t, updated_at: new Date().toISOString() }, ...chats]
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
      listConversations().then(applyList).catch(() => {});
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
              kind: "text",
            },
          ];
        });
      }
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
    };
    if (conversationId === null) {
      return [draft, ...recentChats];
    }
    return recentChats;
  }, [conversationId, recentChats, sessionListTitle]);

  const onlyWelcomeGreeting =
    messages.length === 1 && messages[0]?.id === WELCOME_MESSAGE_ID;

  /** Draft session before the first user message (new conversation, nothing sent yet). */
  const hasUserMessage = messages.some((m) => m.role === "user");

  return (
    <section className="chat-layout">
      <div className="chat-panel card">
        <div className="message-list">
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
                <span className="message-role">
                  {message.role === "user" ? "You" : "AzulClaw"}
                </span>
                <p>{message.content}</p>
              </article>
            );
          })}
          <div ref={bottomRef} />
        </div>

        <div className="composer">
          <div className="composer-wrapper">
            <label className="composer-field">
              <span className="sr-only">Message AzulClaw</span>
              <textarea
                placeholder="Type a message... (Enter to send, Shift+Enter for new line)"
                rows={1}
                value={draft}
                onChange={(event) => {
                  const val = event.target.value;
                  setDraft(val);
                  onTypingChange?.(val.trim().length > 0);
                }}
                onKeyDown={handleKeyDown}
              />
            </label>
            <div className="composer-bottom">
              <div className="composer-actions">
                <button type="button" className="ghost-button-mini" title="Attach a local file" aria-label="Attach file">
                  File
                </button>
                <button type="button" className="ghost-button-mini" title="Search agent preferences" aria-label="Add Memory">
                  Memory
                </button>
                <button type="button" className="ghost-button-mini" title="Search a Workspace document" aria-label="Add from Workspace">
                  Workspace
                </button>
              </div>
              <button
                type="button"
                className={`composer-send-btn ${isSending ? "composer-send-btn-loading" : ""}`}
                onClick={() => void handleSend()}
                disabled={isSending || !draft.trim()}
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

      <aside className="context-panel card">
        <div className="context-panel-head">
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

        {/* ── Recent conversations ─────────────────── */}
        <section className="context-section context-section-grow">
          <p className="eyebrow">Recent conversations</p>
          <div className="recent-chats-list">
            {conversationRows.map((c) => {
              const isDraft = c.id === DRAFT_SESSION_ID;
              const isActive =
                conversationId === null ? isDraft : c.id === conversationId;
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
                    <span className="recent-chat-title">{normalizeConversationTitle(c.title)}</span>
                    {isDraft ? (
                      !hasUserMessage ? (
                        <span className="recent-chat-date">just now</span>
                      ) : null
                    ) : formatRelativeDate(c.updated_at) ? (
                      <span className="recent-chat-date">{formatRelativeDate(c.updated_at)}</span>
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

      </aside>
    </section>
  );
}
