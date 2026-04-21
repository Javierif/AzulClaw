import type {
  ChatExchange,
  ChatStreamEvent,
  ChatRuntimeMeta,
  ConversationSummary,
  HatchingProfile,
  JobRunResult,
  MemoryRecord,
  ProcessSummary,
  RuntimeOverview,
  ScheduledJob,
  WorkspaceEntry,
} from "./contracts";
import {
  chatMessages,
  defaultChatRuntime,
  defaultHatchingProfile,
  memoryItems,
  processItems,
  runtimeOverview,
  scheduledJobs,
  workspaceEntries,
} from "./mock-data";

const DEFAULT_API_BASE = import.meta.env.DEV ? "" : "http://localhost:3978";

function getApiBase() {
  return (import.meta.env.VITE_AZUL_API_BASE ?? DEFAULT_API_BASE).replace(/\/$/, "");
}

async function fetchJson<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${getApiBase()}${path}`, init);
  if (!response.ok) {
    throw new Error(`HTTP ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function sendDesktopMessage(
  message: string,
  userId = "desktop-user",
): Promise<{ reply: string; history: ChatExchange[]; runtime: ChatRuntimeMeta }> {
  try {
    const data = await fetchJson<{ reply: string; history: ChatExchange[]; runtime: ChatRuntimeMeta }>(
      "/api/desktop/chat",
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ user_id: userId, message }),
      },
    );
    return data;
  } catch {
    return {
      reply:
        "Could not reach the real backend. Keeping the visual shell active with fallback data.",
      history: chatMessages,
      runtime: defaultChatRuntime,
    };
  }
}

export async function listConversations(userId = "desktop-user"): Promise<ConversationSummary[]> {
  try {
    const data = await fetchJson<{ items: ConversationSummary[] }>(
      `/api/desktop/conversations?user_id=${encodeURIComponent(userId)}`,
    );
    return data.items;
  } catch {
    return [];
  }
}

export async function createConversation(userId = "desktop-user"): Promise<{ id: string; title: string }> {
  return fetchJson<{ id: string; title: string }>("/api/desktop/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ user_id: userId }),
  });
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await fetchJson<{ deleted: boolean }>(
    `/api/desktop/conversations/${encodeURIComponent(conversationId)}`,
    { method: "DELETE" },
  );
}

export async function getConversationMessages(
  conversationId: string,
  userId = "desktop-user",
): Promise<ChatExchange[]> {
  const data = await fetchJson<{ messages: { role: string; content: string }[] }>(
    `/api/desktop/conversations/${encodeURIComponent(conversationId)}/messages?user_id=${encodeURIComponent(userId)}`,
  );
  return data.messages.map((m, i) => ({
    id: `loaded-${i}`,
    role: m.role as "user" | "assistant",
    content: m.content,
  }));
}

export async function sendDesktopMessageStream(
  message: string,
  onEvent: (event: ChatStreamEvent) => void,
  userId = "desktop-user",
  conversationId?: string,
): Promise<{
  reply: string;
  history: ChatExchange[];
  runtime: ChatRuntimeMeta;
  conversation_id?: string;
  conversation_title?: string;
}> {
  let receivedContent = false;
  let finalReply = "";
  let finalHistory = chatMessages;
  let finalRuntime = defaultChatRuntime;
  let finalConversationId: string | undefined;
  let finalConversationTitle: string | undefined;

  try {
    const response = await fetch(`${getApiBase()}/api/desktop/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ user_id: userId, message, conversation_id: conversationId }),
    });
    if (!response.ok || !response.body) {
      throw new Error(`HTTP ${response.status}`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";

    while (true) {
      const { value, done } = await reader.read();
      if (done) {
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      const lines = buffer.split("\n");
      buffer = lines.pop() || "";

      for (const rawLine of lines) {
        const line = rawLine.trim();
        if (!line) {
          continue;
        }
        const event = JSON.parse(line) as ChatStreamEvent;
        onEvent(event);
        if (event.type === "commentary" || event.type === "progress" || event.type === "delta" || event.type === "done") {
          receivedContent = true;
        }
        if (event.type === "done") {
          finalReply = event.reply || "";
          finalHistory = event.history || finalHistory;
          finalRuntime = event.runtime || finalRuntime;
          finalConversationId = event.conversation_id;
          finalConversationTitle = event.conversation_title;
        }
        if (event.type === "error") {
          throw new Error(event.message || "Streaming failed");
        }
      }
    }

    if (buffer.trim()) {
      const event = JSON.parse(buffer.trim()) as ChatStreamEvent;
      onEvent(event);
      if (event.type === "commentary" || event.type === "progress" || event.type === "delta" || event.type === "done") {
        receivedContent = true;
      }
      if (event.type === "done") {
        finalReply = event.reply || "";
        finalHistory = event.history || finalHistory;
        finalRuntime = event.runtime || finalRuntime;
        finalConversationId = event.conversation_id;
        finalConversationTitle = event.conversation_title;
      }
      if (event.type === "error") {
        throw new Error(event.message || "Streaming failed");
      }
    }

    return {
      reply: finalReply,
      history: finalHistory,
      runtime: finalRuntime,
      conversation_id: finalConversationId,
      conversation_title: finalConversationTitle,
    };
  } catch {
    if (receivedContent) {
      return {
        reply: finalReply,
        history: finalHistory,
        runtime: finalRuntime,
        conversation_id: finalConversationId,
        conversation_title: finalConversationTitle,
      };
    }
    return sendDesktopMessage(message, userId);
  }
}

export async function loadProcesses(): Promise<ProcessSummary[]> {
  try {
    const data = await fetchJson<{ items: ProcessSummary[] }>("/api/desktop/processes");
    return data.items.map((item) => ({
      ...item,
      startedAt: (item as unknown as { started_at?: string }).started_at || item.startedAt,
      updatedAt: (item as unknown as { updated_at?: string }).updated_at || item.updatedAt,
      modelLabel: (item as unknown as { model_label?: string }).model_label || item.modelLabel,
    }));
  } catch {
    return processItems;
  }
}

export async function loadMemory(userId = "desktop-user"): Promise<MemoryRecord[]> {
  try {
    const data = await fetchJson<{ items: MemoryRecord[] }>(
      `/api/desktop/memory?user_id=${encodeURIComponent(userId)}`,
    );
    return data.items;
  } catch {
    return memoryItems;
  }
}

export async function deleteMemory(id: string, userId = "desktop-user"): Promise<void> {
  await fetchJson<{ deleted: boolean }>(
    `/api/desktop/memory/${encodeURIComponent(id)}?user_id=${encodeURIComponent(userId)}`,
    { method: "DELETE" },
  );
}

export async function loadWorkspace(path = "."): Promise<{
  root: string;
  current_path: string;
  entries: WorkspaceEntry[];
}> {
  try {
    return await fetchJson(`/api/desktop/workspace?path=${encodeURIComponent(path)}`);
  } catch {
    return {
      root: "C:/Users/javie/Desktop/AzulWorkspace",
      current_path: ".",
      entries: workspaceEntries,
    };
  }
}

export async function loadHatching(): Promise<HatchingProfile> {
  try {
    return await fetchJson<HatchingProfile>("/api/desktop/hatching");
  } catch {
    const local = localStorage.getItem("azul_mock_profile");
    if (local) {
      try {
        return JSON.parse(local) as HatchingProfile;
      } catch (e) {
        /* ignore */
      }
    }
    return defaultHatchingProfile;
  }
}

export async function saveHatching(profile: HatchingProfile): Promise<HatchingProfile> {
  try {
    return await fetchJson<HatchingProfile>("/api/desktop/hatching", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(profile),
    });
  } catch {
    localStorage.setItem("azul_mock_profile", JSON.stringify(profile));
    return profile;
  }
}

/** Must match ``WIPE_CONFIRMATION_PHRASE`` in ``azul_brain/api/services.py``. */
export const DATA_WIPE_CONFIRM_PHRASE = "RESET_ALL_LOCAL_DATA";

export async function wipeLocalUserData(confirm: string): Promise<HatchingProfile> {
  try {
    const response = await fetch(`${getApiBase()}/api/desktop/data-wipe`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirm }),
    });
    let data: { error?: string } & Partial<HatchingProfile> = {};
    try {
      data = (await response.json()) as { error?: string } & Partial<HatchingProfile>;
    } catch {
      /* non-JSON body */
    }
    if (!response.ok) {
      throw new Error(data.error || `Request failed (${response.status})`);
    }
    return data as HatchingProfile;
  } catch (error) {
    if (error instanceof Error && error.message && !error.message.includes("Failed to fetch")) {
      throw error;
    }
    localStorage.removeItem("azul_mock_profile");
    return {
      ...defaultHatchingProfile,
      is_hatched: false,
      completed_at: "",
      restart_required: true,
    };
  }
}

export async function loadRuntime(): Promise<RuntimeOverview> {
  try {
    return await fetchJson<RuntimeOverview>("/api/desktop/runtime");
  } catch {
    return runtimeOverview;
  }
}

export async function saveRuntime(payload: {
  default_lane?: "auto" | "fast" | "slow";
  models?: Array<{ id: string; streaming_enabled?: boolean; enabled?: boolean; deployment?: string }>;
}): Promise<RuntimeOverview> {
  try {
    return await fetchJson<RuntimeOverview>("/api/desktop/runtime", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  } catch {
    return runtimeOverview;
  }
}

export async function loadJobs(): Promise<ScheduledJob[]> {
  try {
    const data = await fetchJson<{ items: ScheduledJob[] }>("/api/desktop/jobs");
    return data.items;
  } catch {
    return scheduledJobs;
  }
}

export async function saveJob(payload: {
  id?: string;
  name: string;
  prompt: string;
  lane: "auto" | "fast" | "slow";
  schedule_kind: "at" | "every" | "cron";
  interval_seconds?: number;
  cron_expression?: string;
  run_at?: string;
  enabled?: boolean;
  delivery_kind?: "desktop_chat" | "none";
  delivery_user_id?: string;
  delivery_conversation_id?: string;
}): Promise<ScheduledJob> {
  return fetchJson<ScheduledJob>("/api/desktop/jobs", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function runJob(jobId: string): Promise<JobRunResult> {
  return fetchJson<JobRunResult>(`/api/desktop/jobs/${encodeURIComponent(jobId)}/run`, {
    method: "POST",
  });
}

export async function deleteJob(jobId: string): Promise<void> {
  await fetchJson(`/api/desktop/jobs/${encodeURIComponent(jobId)}`, {
    method: "DELETE",
  });
}
