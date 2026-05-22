import { isTauri } from "@tauri-apps/api/core";
import { getCurrentWindow } from "@tauri-apps/api/window";

import type { ConversationSummary } from "./contracts";

const DEFAULT_NOTIFICATION_BODY = "Open AzulClaw to read the latest message.";
const MAX_NOTIFICATION_BODY = 160;
const shownMessageIds = new Set<string>();

function supportsDesktopNotifications(): boolean {
  return (
    typeof window !== "undefined" &&
    isTauri() &&
    "Notification" in window
  );
}

function buildNotificationBody(summary: ConversationSummary): string {
  const preview = (summary.last_message_preview || "").trim();
  if (!preview) {
    return DEFAULT_NOTIFICATION_BODY;
  }
  if (preview.length <= MAX_NOTIFICATION_BODY) {
    return preview;
  }
  return `${preview.slice(0, MAX_NOTIFICATION_BODY - 3).trimEnd()}...`;
}

async function ensureNotificationPermission(): Promise<boolean> {
  if (!supportsDesktopNotifications()) {
    return false;
  }
  if (Notification.permission === "granted") {
    return true;
  }
  if (Notification.permission === "denied") {
    return false;
  }
  try {
    return (await Notification.requestPermission()) === "granted";
  } catch {
    return false;
  }
}

async function shouldShowNotificationNow(): Promise<boolean> {
  if (!supportsDesktopNotifications()) {
    return false;
  }
  try {
    const currentWindow = getCurrentWindow();
    const [focused, minimized] = await Promise.all([
      currentWindow.isFocused(),
      currentWindow.isMinimized(),
    ]);
    return minimized || !focused;
  } catch {
    return false;
  }
}

export async function focusDesktopWindow(): Promise<void> {
  if (!isTauri()) {
    return;
  }
  try {
    const currentWindow = getCurrentWindow();
    await currentWindow.unminimize();
    await currentWindow.setFocus();
  } catch {
    /* best effort */
  }
}

export async function notifyUnreadConversation(
  summary: ConversationSummary,
  onClick: () => void | Promise<void>,
): Promise<boolean> {
  const messageId = (summary.last_message_id || "").trim();
  if (
    !summary.has_unread ||
    summary.last_message_role !== "assistant" ||
    !messageId ||
    shownMessageIds.has(messageId)
  ) {
    return false;
  }
  if (!(await shouldShowNotificationNow())) {
    return false;
  }
  if (!(await ensureNotificationPermission())) {
    return false;
  }

  const notification = new Notification(`AzulClaw - ${summary.title}`, {
    body: buildNotificationBody(summary),
    tag: `conversation:${summary.id}`,
  });
  shownMessageIds.add(messageId);
  notification.onclick = () => {
    notification.close();
    void (async () => {
      await focusDesktopWindow();
      await onClick();
    })();
  };
  return true;
}
