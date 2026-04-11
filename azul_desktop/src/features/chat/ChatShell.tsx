import { useEffect, useRef, useState } from "react";

import { sendDesktopMessageStream } from "../../lib/api";
import { Tooltip } from "../../components/Tooltip";
import type { ChatExchange, ThinkingProgress } from "../../lib/contracts";
import { chatMessages, defaultChatRuntime } from "../../lib/mock-data";

type ChatMessageItem = ChatExchange & {
  kind: "text" | "thinking" | "loading";
  progress?: ThinkingProgress;
};

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

export function ChatShell({
  onThinkingChange,
  onTypingChange,
  onAnswerStart,
}: {
  onThinkingChange?: (thinking: boolean) => void;
  onTypingChange?: (typing: boolean) => void;
  onAnswerStart?: () => void;
}) {
  const [messages, setMessages] = useState<ChatMessageItem[]>(() => toUiMessages(chatMessages));
  const [draft, setDraft] = useState("");
  const [isSending, setIsSending] = useState(false);
  const [runtime, setRuntime] = useState(defaultChatRuntime);
  const bottomRef = useRef<HTMLDivElement>(null);
  const streamMessageIdRef = useRef("");
  const answerStartedRef = useRef(false);
  const streamBufferRef = useRef("");
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

  async function handleSend() {
    const trimmed = draft.trim();
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
    setMessages((current) => [
      ...current,
      nextUserMessage,
      {
        id: commentaryMessageId,
        role: "assistant",
        content: "",
        kind: "loading",
      },
    ]);
    setDraft("");

    try {
      const response = await sendDesktopMessageStream(trimmed, (event) => {
        if (event.type === "commentary" && event.text) {
          if (!answerStartedRef.current) { answerStartedRef.current = true; onAnswerStart?.(); }
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

        if (event.type === "done" && event.runtime) {
          flushStreamBuffer();
          setRuntime(event.runtime);
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
      });

      setRuntime(response.runtime);
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

  return (
    <section className="chat-layout">
      <div className="chat-panel card">
        <div className="chat-header">
          <div>
            <p className="eyebrow">Main Session</p>
            <h2>Main conversation</h2>
          </div>
          <div className="action-row">
            <span className="status-pill">Internal triage</span>
          </div>
        </div>

        <div className="message-list">
          {messages.map((message) =>
            message.kind === "thinking" && message.role === "assistant" ? (
              <ThinkingCard key={message.id} message={message} />
            ) : message.kind === "loading" ? (
              <article key={message.id} className="message-bubble message-assistant">
                <span className="message-role">AzulClaw</span>
                <div className="message-wave">
                  <span className="message-wave-dot" />
                  <span className="message-wave-dot" />
                  <span className="message-wave-dot" />
                </div>
              </article>
            ) : (
              <article
                key={message.id}
                className={`message-bubble message-${message.role}`}
              >
                <span className="message-role">
                  {message.role === "user" ? "You" : "AzulClaw"}
                </span>
                <p>{message.content}</p>
              </article>
            ),
          )}
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
                  const wasEmpty = !draft.trim();
                  const isEmpty = !val.trim();
                  setDraft(val);
                  if (wasEmpty && !isEmpty) onTypingChange?.(true);
                  if (!wasEmpty && isEmpty) onTypingChange?.(false);
                }}
                onKeyDown={handleKeyDown}
              />
            </label>
            <div className="composer-bottom">
              <div className="composer-actions">
                <button type="button" className="ghost-button-mini" title="Attach a local file" aria-label="Attach file" disabled>
                  File
                </button>
                <button type="button" className="ghost-button-mini" title="Search agent preferences" aria-label="Add Memory" disabled>
                  Memory
                </button>
                <button type="button" className="ghost-button-mini" title="Search a Workspace document" aria-label="Add from Workspace" disabled>
                  Workspace
                </button>
              </div>
              <button
                type="button"
                className="composer-send-btn"
                onClick={() => void handleSend()}
                disabled={!draft.trim()}
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
        <div className="context-header">
          <p className="eyebrow">Live Context</p>
          <h2>Session activity</h2>
        </div>

        <section className="context-section">
          <p className="context-section-label">Current status</p>
          <div className="context-kv-list">
            <div className="context-kv-row">
              <span className="context-kv-key">Lane</span>
              <span className="context-kv-value">{runtime.lane || "—"}</span>
            </div>
            <div className="context-kv-row">
              <span className="context-kv-key">Triage</span>
              <span className="context-kv-value">{runtime.triage_reason || "—"}</span>
            </div>
            <div className="context-kv-row">
              <span className="context-kv-key">Model</span>
              <span className="context-kv-value">{runtime.model_label || "—"}</span>
            </div>
            <div className="context-kv-row">
              <span className="context-kv-key">Process</span>
              <span className="context-kv-value context-kv-mono">{runtime.process_id || "—"}</span>
            </div>
          </div>
        </section>

        <section className="context-section">
          <p className="context-section-label">Recent memory</p>
          <p className="context-body">No memory indexed yet.</p>
        </section>

        <section className="context-section">
          <p className="context-section-label">Workspace</p>
          <p className="context-body context-kv-mono">~/Desktop/AzulWorkspace</p>
        </section>
      </aside>
    </section>
  );
}
