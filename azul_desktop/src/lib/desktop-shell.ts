import { invoke, isTauri } from "@tauri-apps/api/core";

import type { DesktopShellPreferences } from "./contracts";

const DESKTOP_SHELL_STORAGE_KEY = "azul_desktop_shell_preferences";

export const defaultDesktopShellPreferences: DesktopShellPreferences = {
  tray_icon_enabled: false,
  global_shortcut_enabled: false,
  close_to_tray_enabled: false,
  global_shortcut: "Ctrl+Alt+Space",
};

function normalizeDesktopShellPreferences(
  preferences: Partial<DesktopShellPreferences> | null | undefined,
): DesktopShellPreferences {
  const trayIconEnabled = preferences?.tray_icon_enabled === true;
  const globalShortcutEnabled = preferences?.global_shortcut_enabled === true;
  const shortcut = (preferences?.global_shortcut || defaultDesktopShellPreferences.global_shortcut).trim();

  return {
    tray_icon_enabled: trayIconEnabled,
    global_shortcut_enabled: globalShortcutEnabled,
    close_to_tray_enabled: trayIconEnabled && preferences?.close_to_tray_enabled === true,
    global_shortcut: shortcut || defaultDesktopShellPreferences.global_shortcut,
  };
}

export async function loadDesktopShellPreferences(): Promise<DesktopShellPreferences> {
  if (isTauri()) {
    const value = await invoke<DesktopShellPreferences>("load_desktop_shell_preferences");
    return normalizeDesktopShellPreferences(value);
  }

  try {
    const raw = localStorage.getItem(DESKTOP_SHELL_STORAGE_KEY);
    if (!raw) {
      return defaultDesktopShellPreferences;
    }
    return normalizeDesktopShellPreferences(JSON.parse(raw) as DesktopShellPreferences);
  } catch {
    return defaultDesktopShellPreferences;
  }
}

export async function saveDesktopShellPreferences(
  preferences: DesktopShellPreferences,
): Promise<DesktopShellPreferences> {
  const normalized = normalizeDesktopShellPreferences(preferences);

  if (isTauri()) {
    const saved = await invoke<DesktopShellPreferences>("save_desktop_shell_preferences", {
      preferences: normalized,
    });
    return normalizeDesktopShellPreferences(saved);
  }

  localStorage.setItem(DESKTOP_SHELL_STORAGE_KEY, JSON.stringify(normalized));
  return normalized;
}
