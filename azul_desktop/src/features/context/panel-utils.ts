import type { MemoryRecord } from "../../lib/contracts";

export const MEMORY_KIND_LABEL: Record<string, string> = {
  preference: "Preference",
  semantic: "Context",
  episodic: "Conversation",
  session: "Session",
};

export const MEMORY_KIND_COLOR: Record<string, string> = {
  preference: "status-done",
  semantic: "status-waiting",
  episodic: "status-running",
  session: "",
};

export const MEMORY_SOURCE_LABEL: Record<string, string> = {
  featured: "Featured",
  "hatching-profile": "Profile setup",
  extractor: "Auto-learned",
  extracted: "Auto-learned",
  user: "You said",
  assistant: "Assistant said",
};

export function formatMemoryDate(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, {
      month: "short",
      day: "numeric",
      year: "numeric",
    });
  } catch {
    return "";
  }
}

export function isDeletableMemory(record: MemoryRecord): boolean {
  return !record.pinned;
}

export function getLearnedMemory(records: MemoryRecord[]): MemoryRecord[] {
  return records
    .filter((record) => record.kind === "preference" || record.kind === "semantic" || record.kind === ("fact" as string))
    .sort((a, b) => {
      const aFeatured = a.source === "featured" || a.source === "hatching-profile" ? 0 : 1;
      const bFeatured = b.source === "featured" || b.source === "hatching-profile" ? 0 : 1;
      return aFeatured - bFeatured;
    });
}
