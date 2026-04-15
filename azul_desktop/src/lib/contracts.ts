export type AppView =
  | "chat"
  | "hatching"
  | "skills"
  | "processes"
  | "heartbeats"
  | "runtime"
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
  kind: string;
  lane: string;
  startedAt: string;
  updatedAt?: string;
  detail?: string;
  modelLabel?: string;
}

export interface MemoryRecord {
  id: string;
  title: string;
  content?: string;
  kind: "preference" | "episodic" | "semantic" | "session";
  source: string;
  pinned?: boolean;
  created_at?: string;
}

export interface ChatExchange {
  id: string;
  role: "user" | "assistant";
  content: string;
}

export interface ConversationSummary {
  id: string;
  title: string;
  updated_at: string;
}

export interface ThinkingStep {
  id: string;
  label: string;
  status: "pending" | "active" | "done";
}

export interface ThinkingPhase {
  id: string;
  label: string;
  status: "pending" | "active" | "done";
  steps: ThinkingStep[];
}

export interface ThinkingProgress {
  title: string;
  summary: string;
  badge: string;
  active_count: number;
  phases: ThinkingPhase[];
}

export interface ChatRuntimeMeta {
  lane: string;
  model_id: string;
  model_label: string;
  process_id: string;
  triage_reason?: string;
}

export interface ChatStreamEvent {
  type: "start" | "commentary" | "progress" | "delta" | "done" | "error";
  text?: string;
  reply?: string;
  history?: ChatExchange[];
  runtime?: ChatRuntimeMeta;
  message?: string;
  progress?: ThinkingProgress;
  conversation_id?: string;
  /** Present on ``done`` when the server has a conversation row title (sidebar + top bar). */
  conversation_title?: string;
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
  /** Effective SQLite path (server-computed; not stored in profile JSON). */
  memory_db_path?: string;
  /** Present on ``POST /api/desktop/data-wipe`` responses only. */
  restart_required?: boolean;
}

export interface RuntimeModelStatus {
  id: string;
  label: string;
  lane: "fast" | "slow";
  provider: "azure" | "openai";
  deployment: string;
  enabled: boolean;
  streaming_enabled: boolean;
  available: boolean;
  cooldown_until: string;
  last_error: string;
  description: string;
  probe_detail: string;
}

export interface RuntimeOverview {
  default_lane: "auto" | "fast" | "slow";
  models: RuntimeModelStatus[];
  scheduler_running: boolean;
  scheduler_last_error: string;
  jobs_total: number;
  jobs_running: number;
  processes_visible: number;
}

export interface ScheduledJob {
  id: string;
  name: string;
  prompt: string;
  lane: "auto" | "fast" | "slow";
  schedule_kind: "at" | "every";
  run_at: string;
  interval_seconds: number;
  enabled: boolean;
  system: boolean;
  source: string;
  created_at: string;
  updated_at: string;
  last_run_at: string;
  next_run_at: string;
}
