import { invoke, isTauri } from "@tauri-apps/api/core";

export async function storeAzureOpenAiApiKey(secret: string): Promise<void> {
  if (!isTauri()) return;
  await invoke("store_azure_openai_api_key", { secret });
}

export async function loadAzureOpenAiApiKey(): Promise<string | null> {
  if (!isTauri()) return null;
  const value = await invoke<string | null>("load_azure_openai_api_key");
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

export async function hasAzureOpenAiApiKey(): Promise<boolean> {
  if (!isTauri()) return false;
  return await invoke<boolean>("has_azure_openai_api_key");
}

export async function clearAzureOpenAiApiKey(): Promise<void> {
  if (!isTauri()) return;
  await invoke("clear_azure_openai_api_key");
}

export async function isAzureOpenAiApiKeyStorageAvailable(): Promise<boolean> {
  if (!isTauri()) return false;
  return await invoke<boolean>("is_azure_openai_api_key_storage_available");
}
