export type AppView =
  | "chat"
  | "hatching"
  | "skills"
  | "processes"
  | "memory"
  | "workspace"
  | "settings";

export interface WorkspaceEntry {
  name: string;
  kind: "file" | "folder";
  path: string;
}

export interface ProcessSummary {
  id: string;
  title: string;
  status: "running" | "waiting" | "done" | "failed";
  skill: string;
  startedAt: string;
}

export interface MemoryRecord {
  id: string;
  title: string;
  kind: "preference" | "episodic" | "semantic" | "session";
  source: string;
  pinned?: boolean;
}

export interface ChatExchange {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface HatchingProfile {
  name: string;
  role: string;
  mission: string;
  tone: string;
  style: string;
  autonomy: string;
  archetype: string;
  workspace_root: string;
  confirm_sensitive_actions: boolean;
  is_hatched: boolean;
  completed_at: string;
  skills: string[];
  skill_configs: Record<string, Record<string, string>>;
}
