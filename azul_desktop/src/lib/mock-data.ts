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
    content: "Quiero revisar el contenido de Projects y resumir lo importante.",
  },
  {
    id: "m2",
    role: "assistant",
    content:
      "Estoy preparando el contexto del workspace. Primero leere la carpeta Projects y luego generare un resumen accionable.",
  },
];

export const processItems: ProcessSummary[] = [
  {
    id: "p1",
    title: "Revisar documentos de Projects",
    status: "running",
    skill: "Workspace",
    kind: "agent-run",
    lane: "slow",
    startedAt: "12:04",
    updatedAt: "12:05",
    detail: "Leyendo documentos del workspace y preparando contexto.",
    modelLabel: "Cerebro lento",
  },
  {
    id: "p2",
    title: "Clasificar notas en Inbox",
    status: "waiting",
    skill: "Workspace",
    kind: "agent-run",
    lane: "fast",
    startedAt: "11:51",
    updatedAt: "11:51",
    detail: "Pendiente de nueva ejecucion del scheduler.",
  },
  {
    id: "p3",
    title: "Resumen semanal",
    status: "done",
    skill: "Memory",
    kind: "agent-run",
    lane: "slow",
    startedAt: "10:30",
    updatedAt: "10:32",
    detail: "Ultimo resumen completado y persistido.",
    modelLabel: "Cerebro lento",
  },
];

export const memoryItems: MemoryRecord[] = [
  {
    id: "mem1",
    title: "Javier prefiere respuestas directas",
    kind: "preference",
    source: "Hatching",
    pinned: true,
  },
  {
    id: "mem2",
    title: "Error recurrente con Azure auth",
    kind: "episodic",
    source: "Sesion del 8 de abril",
  },
  {
    id: "mem3",
    title: "Resumen de arquitectura de AzulClaw",
    kind: "semantic",
    source: "Documento indexado",
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
  role: "Companero tecnico local",
  mission: "Ayudarte sin perder seguridad ni contexto.",
  tone: "Directo",
  style: "Explicativo",
  autonomy: "Autonomo moderado",
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
  model_label: "Cerebro lento",
  process_id: "local-fallback",
};

export const runtimeOverview: RuntimeOverview = {
  default_lane: "auto",
  models: [
    {
      id: "fast",
      label: "Cerebro rapido",
      lane: "fast",
      provider: "azure",
      deployment: "gpt-4o-mini",
      enabled: true,
      streaming_enabled: true,
      available: true,
      cooldown_until: "",
      last_error: "",
      description: "Turnos rapidos, heartbeats y tareas ligeras.",
      probe_detail: "Configuracion Azure lista",
    },
    {
      id: "slow",
      label: "Cerebro lento",
      lane: "slow",
      provider: "azure",
      deployment: "gpt-4o",
      enabled: true,
      streaming_enabled: false,
      available: true,
      cooldown_until: "",
      last_error: "",
      description: "Tareas deliberadas y con mas contexto.",
      probe_detail: "Configuracion Azure lista",
    },
  ],
  heartbeat: {
    enabled: true,
    interval_seconds: 900,
    prompt: "Heartbeat del sistema.",
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
    name: "Resumen operativo",
    prompt: "Revisa el workspace y resume bloqueos activos.",
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
