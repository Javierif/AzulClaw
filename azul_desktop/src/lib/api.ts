import type { ChatExchange, MemoryRecord, ProcessSummary, WorkspaceEntry } from "./contracts";
import {
  chatMessages,
  memoryItems,
  processItems,
  workspaceEntries,
} from "./mock-data";

const DEFAULT_API_BASE = "http://localhost:3978";

function getApiBase() {
  return (import.meta.env.VITE_AZUL_API_BASE || DEFAULT_API_BASE).replace(/\/$/, "");
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
): Promise<{ reply: string; history: ChatExchange[] }> {
  try {
    const data = await fetchJson<{ reply: string; history: ChatExchange[] }>(
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
        "No he podido contactar con el backend real. Mantengo el shell visual activo con datos de fallback.",
      history: chatMessages,
    };
  }
}

export async function loadProcesses(): Promise<ProcessSummary[]> {
  try {
    const data = await fetchJson<{ items: ProcessSummary[] }>("/api/desktop/processes");
    return data.items;
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
