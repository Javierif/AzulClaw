import type {
  ChatExchange,
  ChatRuntimeMeta,
  HatchingProfile,
  MemoryRecord,
  MemorySettings,
  ProcessSummary,
  RuntimeOverview,
  ScheduledJob,
  WorkspaceEntry,
} from "./contracts";

export const chatMessages: ChatExchange[] = [];

export const processItems: ProcessSummary[] = [
  {
    id: "p1",
    title: "Review Projects documents",
    status: "running",
    skill: "Workspace",
    kind: "agent-run",
    lane: "slow",
    startedAt: "12:04",
    updatedAt: "12:05",
    detail: "Reading workspace documents and preparing context.",
    modelLabel: "Slow Brain",
  },
  {
    id: "p2",
    title: "Organise Inbox notes",
    status: "waiting",
    skill: "Workspace",
    kind: "agent-run",
    lane: "fast",
    startedAt: "11:51",
    updatedAt: "11:51",
    detail: "Pending next scheduler run.",
  },
  {
    id: "p3",
    title: "Weekly summary",
    status: "done",
    skill: "Memory",
    kind: "agent-run",
    lane: "slow",
    startedAt: "10:30",
    updatedAt: "10:32",
    detail: "Last summary completed and persisted.",
    modelLabel: "Slow Brain",
  },
];

export const memoryItems: MemoryRecord[] = [
  {
    id: "mem1",
    title: "User prefers direct answers",
    kind: "preference",
    source: "Hatching",
    pinned: true,
  },
  {
    id: "mem2",
    title: "Recurring error with Azure auth",
    kind: "episodic",
    source: "Session from April 8th",
  },
  {
    id: "mem3",
    title: "AzulClaw architecture summary",
    kind: "semantic",
    source: "Indexed document",
  },
];

export const memorySettings: MemorySettings = {
  memory_db_path: "C:\\Users\\<user>\\Desktop\\AzulWorkspace\\.azul\\azul_memory.db",
  memory_db_path_override: "",
  default_memory_db_path: "C:\\Users\\<user>\\Desktop\\AzulWorkspace\\.azul\\azul_memory.db",
  vector_memory_enabled: true,
};

export const workspaceEntries: WorkspaceEntry[] = [
  { name: "Inbox", kind: "folder", path: "/Inbox" },
  { name: "Projects", kind: "folder", path: "/Projects" },
  { name: "Generated", kind: "folder", path: "/Generated" },
  { name: "weekly-summary.md", kind: "file", path: "/Generated/weekly-summary.md" },
  { name: "notes-refactor.txt", kind: "file", path: "/Inbox/notes-refactor.txt" },
];

export const defaultHatchingProfile: HatchingProfile = {
  name: "AzulClaw",
  role: "Local technical companion",
  mission: "Help you without losing safety or context.",
  tone: "Direct",
  style: "Explanatory",
  autonomy: "Moderately autonomous",
  archetype: "Companion",
  workspace_root: "C:\\Users\\<user>\\Desktop\\AzulWorkspace",
  confirm_sensitive_actions: true,
  is_hatched: false,
  completed_at: "",
  skills: ["Email", "Telegram", "Workspace"],
  skill_configs: {},
};

export const defaultChatRuntime: ChatRuntimeMeta = {
  lane: "auto",
  model_id: "slow",
  model_label: "Slow Brain",
  process_id: "local-fallback",
};

export const runtimeOverview: RuntimeOverview = {
  default_lane: "auto",
  models: [
    {
      id: "fast",
      label: "Fast Brain",
      lane: "fast",
      provider: "azure",
      deployment: "gpt-4o-mini",
      enabled: true,
      streaming_enabled: true,
      available: true,
      cooldown_until: "",
      last_error: "",
      description: "Quick turns, heartbeats and lightweight tasks.",
      probe_detail: "Azure configuration ready",
    },
    {
      id: "slow",
      label: "Slow Brain",
      lane: "slow",
      provider: "azure",
      deployment: "gpt-4o",
      enabled: true,
      streaming_enabled: false,
      available: true,
      cooldown_until: "",
      last_error: "",
      description: "Deliberate tasks with more context.",
      probe_detail: "Azure configuration ready",
    },
  ],
  scheduler_running: true,
  scheduler_last_error: "",
  jobs_total: 2,
  jobs_running: 0,
  processes_visible: processItems.length,
};

export const scheduledJobs: ScheduledJob[] = [
  {
    id: "system-heartbeat",
    name: "System heartbeat",
    prompt: "System heartbeat. Read HEARTBEAT.md if it exists. Follow it strictly. If nothing needs attention, respond exactly HEARTBEAT_OK.",
    lane: "fast",
    schedule_kind: "every",
    run_at: "",
    interval_seconds: 900,
    cron_expression: "",
    enabled: true,
    system: true,
    source: "system",
    delivery_kind: "desktop_chat",
    delivery_user_id: "desktop-user",
    delivery_conversation_id: "",
    created_at: "",
    updated_at: "",
    last_run_at: "",
    next_run_at: "",
  },
  {
    id: "job-demo",
    name: "Operational summary",
    prompt: "Review the workspace and summarize active blockers.",
    lane: "slow",
    schedule_kind: "every",
    run_at: "",
    interval_seconds: 3600,
    cron_expression: "",
    enabled: true,
    system: false,
    source: "user",
    delivery_kind: "desktop_chat",
    delivery_user_id: "desktop-user",
    delivery_conversation_id: "",
    created_at: "",
    updated_at: "",
    last_run_at: "",
    next_run_at: "",
  },
];
