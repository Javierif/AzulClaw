import type { ConversationSummary } from "./contracts";

/** Single source of truth for empty / default conversation labels in the shell. */
export const DEFAULT_CONVERSATION_TITLE = "New conversation";

/** Maps legacy DB copy and normalizes empty titles for the top bar and side list. */
export function normalizeConversationTitle(title: string | undefined | null): string {
  const t = (title ?? "").trim();
  if (t === "" || t === "New chat") {
    return DEFAULT_CONVERSATION_TITLE;
  }
  return t;
}

/** Normalizes titles on every list from the API (SQLite may still have `New chat`). */
export function withNormalizedConversationTitles(
  chats: ConversationSummary[],
): ConversationSummary[] {
  return chats.map((c) => ({
    ...c,
    title: normalizeConversationTitle(c.title),
  }));
}
