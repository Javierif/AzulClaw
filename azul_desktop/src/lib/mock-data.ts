import type {
  ChatExchange,
  MemoryRecord,
  ProcessSummary,
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
    startedAt: "12:04",
  },
  {
    id: "p2",
    title: "Clasificar notas en Inbox",
    status: "waiting",
    skill: "Workspace",
    startedAt: "11:51",
  },
  {
    id: "p3",
    title: "Resumen semanal",
    status: "done",
    skill: "Memory",
    startedAt: "10:30",
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
