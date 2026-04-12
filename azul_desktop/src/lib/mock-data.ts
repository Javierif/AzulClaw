import type {
  ChatExchange,
  ChatRuntimeMeta,
  HatchingProfile,
  MemoryRecord,
  ProcessSummary,
  RuntimeOverview,
  ScheduledJob,
  WorkspaceEntry,
} from "./contracts";

export const chatMessages: ChatExchange[] = [
  {
    id: "m1",
    role: "user",
    content: "I want to review the Projects folder and summarise the key items.",
  },
  {
    id: "m2",
    role: "assistant",
    content:
      "I'm preparing the workspace context. I'll first read the Projects folder and then generate an actionable summary.",
  },
];

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
  workspace_root: "C:\\Users\\javie\\Desktop\\AzulWorkspace",
  confirm_sensitive_actions: true,
  is_hatched: false,
  completed_at: "",
  skills: ["Email", "Telegram", "Workspace", "Memory"],
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
  heartbeat: {
    enabled: true,
    interval_seconds: 900,
    prompt: "System heartbeat.",
    next_run_at: "",
    last_run_at: "",
    last_result: "",
    workspace_root: "C:\\Users\\javie\\Desktop\\AzulWorkspace",
    heartbeat_file: "C:\\Users\\javie\\Desktop\\AzulWorkspace\\HEARTBEAT.md",
  },
  jobs_total: 1,
  jobs_running: 0,
  processes_visible: processItems.length,
};

export const scheduledJobs: ScheduledJob[] = [
  {
    id: "job-demo",
    name: "Operational summary",
    prompt: "Review the workspace and summarise active blockers.",
    lane: "slow",
    schedule_kind: "every",
    run_at: "",
    interval_seconds: 3600,
    enabled: true,
    created_at: "",
    updated_at: "",
    last_run_at: "",
    next_run_at: "",
  },
];
