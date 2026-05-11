import type {
  ChatExchange,
  ChatStreamEvent,
  ChatRuntimeMeta,
  BackendStatus,
  BackendAuthStatus,
  AzureDeploymentOption,
  AzureKeyVaultOption,
  AzureKeyVaultSecretOption,
  AzureOpenAIResourceOption,
  AzureSubscriptionOption,
  ConversationSummary,
  HatchingProfile,
  JobRunResult,
  MemoryRecord,
  MemorySettings,
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
  memorySettings,
  processItems,
  runtimeOverview,
  scheduledJobs,
  workspaceEntries,
} from "./mock-data";
import {
  clearAzureOpenAiApiKey,
  hasAzureOpenAiApiKey,
  isAzureOpenAiApiKeyStorageAvailable,
  storeAzureOpenAiApiKey,
} from "./desktop-secrets";
import { isTauri } from "@tauri-apps/api/core";

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

function cloneProfileWithSafeAzureConfig(profile: HatchingProfile): HatchingProfile {
  const azure = profile.skill_configs?.Azure;
  if (!azure) return profile;

  const safeAzure: Record<string, string> = { ...azure };
  delete safeAzure.apiKey;
  if (safeAzure.authMethod === "api_key") {
    safeAzure.apiKeyStored = safeAzure.apiKeyStored === "true" ? "true" : "false";
  } else {
    delete safeAzure.apiKeyStored;
  }

  return {
    ...profile,
    skill_configs: {
      ...profile.skill_configs,
      Azure: safeAzure,
    },
  };
}

async function prepareProfileForPersistence(profile: HatchingProfile): Promise<HatchingProfile> {
  const azure = profile.skill_configs?.Azure;
  if (!azure) return profile;

  const authMethod = (azure.authMethod ?? "").trim();
  const apiKey = (azure.apiKey ?? "").trim();
  const apiKeyStored = (azure.apiKeyStored ?? "").trim() === "true";
  const nextAzure: Record<string, string> = { ...azure };
  const desktop = isTauri();
  const secureStorageAvailable = desktop && (await isAzureOpenAiApiKeyStorageAvailable());

  if (authMethod === "api_key") {
    if (!secureStorageAvailable) {
      delete nextAzure.apiKeyStored;
      return {
        ...profile,
        skill_configs: {
          ...profile.skill_configs,
          Azure: nextAzure,
        },
      };
    }
    if (apiKey) {
      await storeAzureOpenAiApiKey(apiKey);
      nextAzure.apiKeyStored = "true";
    } else if (apiKeyStored) {
      nextAzure.apiKeyStored = "true";
    } else {
      await clearAzureOpenAiApiKey();
      delete nextAzure.apiKeyStored;
    }
  } else if (secureStorageAvailable) {
    await clearAzureOpenAiApiKey();
    delete nextAzure.apiKeyStored;
  } else {
    delete nextAzure.apiKeyStored;
  }

  delete nextAzure.apiKey;
  return {
    ...profile,
    skill_configs: {
      ...profile.skill_configs,
      Azure: nextAzure,
    },
  };
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

export async function loadMemorySettings(): Promise<MemorySettings> {
  try {
    return await fetchJson<MemorySettings>("/api/desktop/memory/settings");
  } catch {
    return memorySettings;
  }
}

export async function saveMemorySettings(payload: {
  memory_db_path_override: string;
  vector_memory_enabled: boolean;
}): Promise<MemorySettings> {
  return fetchJson<MemorySettings>("/api/desktop/memory/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
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
    const profile = await fetchJson<HatchingProfile>("/api/desktop/hatching");
    const azure = profile.skill_configs?.Azure;
    if (azure && isTauri() && (await isAzureOpenAiApiKeyStorageAvailable())) {
      const legacyApiKey = (azure.apiKey ?? "").trim();
      if (legacyApiKey) {
        const migrated = cloneProfileWithSafeAzureConfig({
          ...profile,
          skill_configs: {
            ...profile.skill_configs,
            Azure: {
              ...azure,
              authMethod: "api_key",
              apiKeyStored: "true",
              keyVaultUrl: "",
              keyVaultName: "",
              keyVaultResourceGroup: "",
              microsoftAppIdSecretName: "",
              microsoftAppPasswordSecretName: "",
              microsoftAppTenantIdSecretName: "",
            },
          },
        });
        try {
          await storeAzureOpenAiApiKey(legacyApiKey);
        } catch {
          return cloneProfileWithSafeAzureConfig(profile);
        }
        try {
          await fetchJson<HatchingProfile>("/api/desktop/hatching", {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(migrated),
          });
        } catch {
          /* best effort migration */
        }
        return migrated;
      }

      if ((azure.authMethod ?? "").trim() === "api_key") {
        const stored = await hasAzureOpenAiApiKey();
        return cloneProfileWithSafeAzureConfig({
          ...profile,
          skill_configs: {
            ...profile.skill_configs,
            Azure: {
              ...azure,
              apiKeyStored: stored ? "true" : "false",
            },
          },
        });
      }
    }
    return cloneProfileWithSafeAzureConfig(profile);
  } catch {
    const local = localStorage.getItem("azul_mock_profile");
    if (local) {
      try {
        return cloneProfileWithSafeAzureConfig(JSON.parse(local) as HatchingProfile);
      } catch (e) {
        /* ignore */
      }
    }
    return defaultHatchingProfile;
  }
}

export async function saveHatching(profile: HatchingProfile): Promise<HatchingProfile> {
  try {
    const safeProfile = await prepareProfileForPersistence(profile);
    return await fetchJson<HatchingProfile>("/api/desktop/hatching", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(safeProfile),
    });
  } catch {
    const safeProfile = cloneProfileWithSafeAzureConfig(profile);
    localStorage.setItem("azul_mock_profile", JSON.stringify(safeProfile));
    return safeProfile;
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
    if (isTauri()) {
      await clearAzureOpenAiApiKey();
    }
    return data as HatchingProfile;
  } catch (error) {
    if (error instanceof Error && error.message && !error.message.includes("Failed to fetch")) {
      throw error;
    }
    if (isTauri()) {
      await clearAzureOpenAiApiKey();
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

export async function loadBackendStatus(): Promise<BackendStatus> {
  try {
    return await fetchJson<BackendStatus>("/api/desktop/backend/status");
  } catch (error) {
    return {
      status: "offline",
      api_base: getApiBase() || "http://localhost:3978",
      runtime_dir: "",
      log_dir: "",
      models_total: 0,
      models_enabled: 0,
      scheduler_running: false,
      auth: {
        mode: "entra",
        startup_enabled: true,
        status: "failed",
        detail: "Backend unreachable",
        last_error: error instanceof Error ? error.message : "Backend unreachable",
        last_success_at: "",
      },
      logs: [],
      error: error instanceof Error ? error.message : "Backend unreachable",
    };
  }
}

export async function ensureBackendAuth(): Promise<BackendAuthStatus> {
  return fetchJson<BackendAuthStatus>("/api/desktop/backend/auth/ensure", {
    method: "POST",
  });
}

export async function connectAzure(payload: {
  auth_mode?: "entra" | "api_key";
  tenant_id: string;
  client_id: string;
  endpoint: string;
  deployment: string;
  fast_deployment?: string;
  embedding_deployment?: string;
  key_vault_url?: string;
  access_token?: string;
  expires_on?: number;
  scope?: string;
  api_key?: string;
}): Promise<BackendAuthStatus> {
  return fetchJson<BackendAuthStatus>("/api/desktop/azure/connect", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function hydrateAzureKeyVaultSecrets(payload: {
  key_vault_url: string;
  access_token: string;
  expires_on: number;
  microsoft_app_id_secret_name?: string;
  microsoft_app_password_secret_name?: string;
  microsoft_app_tenant_id_secret_name?: string;
}): Promise<{ hydrated: string[] }> {
  return fetchJson<{ hydrated: string[] }>("/api/desktop/azure/key-vault/hydrate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

export async function discoverAzureSubscriptions(accessToken: string): Promise<AzureSubscriptionOption[]> {
  const data = await fetchJson<{ items: AzureSubscriptionOption[] }>("/api/desktop/azure/discovery/subscriptions", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: accessToken }),
  });
  return data.items;
}

export async function discoverAzureResources(
  accessToken: string,
  subscriptionId: string,
): Promise<AzureOpenAIResourceOption[]> {
  const data = await fetchJson<{ items: AzureOpenAIResourceOption[] }>("/api/desktop/azure/discovery/resources", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: accessToken, subscription_id: subscriptionId }),
  });
  return data.items;
}

export async function discoverAzureKeyVaults(
  accessToken: string,
  subscriptionId: string,
): Promise<AzureKeyVaultOption[]> {
  const data = await fetchJson<{ items: AzureKeyVaultOption[] }>("/api/desktop/azure/discovery/key-vaults", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ access_token: accessToken, subscription_id: subscriptionId }),
  });
  return data.items;
}

export async function discoverAzureKeyVaultSecrets(
  accessToken: string,
  vaultUrl: string,
): Promise<AzureKeyVaultSecretOption[]> {
  const data = await fetchJson<{ items: AzureKeyVaultSecretOption[] }>("/api/desktop/azure/discovery/key-vault-secrets", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      access_token: accessToken,
      vault_url: vaultUrl,
    }),
  });
  return data.items;
}

export async function discoverAzureDeployments(
  accessToken: string,
  subscriptionId: string,
  resourceGroup: string,
  accountName: string,
): Promise<AzureDeploymentOption[]> {
  const data = await fetchJson<{ items: AzureDeploymentOption[] }>("/api/desktop/azure/discovery/deployments", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      access_token: accessToken,
      subscription_id: subscriptionId,
      resource_group: resourceGroup,
      account_name: accountName,
    }),
  });
  return data.items;
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
