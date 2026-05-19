import { invoke, isTauri } from "@tauri-apps/api/core";

type NativeFilePayload = {
  name: string;
  mime_type?: string;
  data_base64: string;
};

export async function readClipboardFilePaths(): Promise<string[]> {
  if (!isTauri()) return [];
  try {
    return await invoke<string[]>("read_clipboard_file_paths");
  } catch {
    return [];
  }
}

export async function readLocalFilesFromPaths(paths: string[]): Promise<File[]> {
  if (!isTauri() || paths.length === 0) return [];
  try {
    const payload = await invoke<NativeFilePayload[]>("read_local_files", { paths });
    return payload.map((item) => {
      const bytes = Uint8Array.from(atob(item.data_base64), (char) => char.charCodeAt(0));
      return new File([bytes], item.name, { type: item.mime_type || "application/octet-stream" });
    });
  } catch {
    return [];
  }
}
