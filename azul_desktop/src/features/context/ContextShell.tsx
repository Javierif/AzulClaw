import { useEffect, useState } from "react";

import { SectionTopbarPortal } from "../../components/SectionTopbarPortal";
import { deleteMemory, loadMemory, loadProcesses, loadWorkspace } from "../../lib/api";
import type { MemoryRecord, ProcessSummary, WorkspaceEntry } from "../../lib/contracts";
import { memoryItems, processItems, workspaceEntries } from "../../lib/mock-data";
import { ContextMemoryPanel } from "./ContextMemoryPanel";
import { ContextOverviewPanel } from "./ContextOverviewPanel";
import { ContextProcessesPanel } from "./ContextProcessesPanel";
import { ContextWorkspacePanel } from "./ContextWorkspacePanel";
import { getLearnedMemory } from "./panel-utils";

type ContextTab = "overview" | "processes" | "memory" | "workspace";

const CONTEXT_TABS: Array<{ id: ContextTab; label: string }> = [
  { id: "overview", label: "Overview" },
  { id: "processes", label: "Processes" },
  { id: "memory", label: "Memory" },
  { id: "workspace", label: "Workspace" },
];

export function ContextShell({
  headerPortalTarget = null,
}: {
  headerPortalTarget?: HTMLElement | null;
}) {
  const [activeTab, setActiveTab] = useState<ContextTab>("overview");
  const [processes, setProcesses] = useState<ProcessSummary[]>(processItems);
  const [records, setRecords] = useState<MemoryRecord[]>(memoryItems);
  const [workspaceRoot, setWorkspaceRoot] = useState("C:/Users/javie/Desktop/AzulWorkspace");
  const [workspacePath, setWorkspacePath] = useState(".");
  const [entries, setEntries] = useState<WorkspaceEntry[]>(workspaceEntries);

  async function refreshProcesses() {
    const data = await loadProcesses();
    setProcesses(data);
  }

  async function refreshMemory() {
    const data = await loadMemory();
    setRecords(data);
  }

  async function refreshWorkspace() {
    const data = await loadWorkspace();
    setWorkspaceRoot(data.root);
    setWorkspacePath(data.current_path);
    setEntries(data.entries);
  }

  async function refreshContext() {
    const [nextProcesses, nextMemory, nextWorkspace] = await Promise.all([
      loadProcesses(),
      loadMemory(),
      loadWorkspace(),
    ]);
    setProcesses(nextProcesses);
    setRecords(nextMemory);
    setWorkspaceRoot(nextWorkspace.root);
    setWorkspacePath(nextWorkspace.current_path);
    setEntries(nextWorkspace.entries);
  }

  useEffect(() => {
    let isMounted = true;

    void (async () => {
      try {
        const [nextProcesses, nextMemory, nextWorkspace] = await Promise.all([
          loadProcesses(),
          loadMemory(),
          loadWorkspace(),
        ]);
        if (!isMounted) return;
        setProcesses(nextProcesses);
        setRecords(nextMemory);
        setWorkspaceRoot(nextWorkspace.root);
        setWorkspacePath(nextWorkspace.current_path);
        setEntries(nextWorkspace.entries);
      } catch {
        /* keep existing fallback data when the backend is temporarily unavailable */
      }
    })();

    return () => {
      isMounted = false;
    };
  }, []);

  async function handleDeleteMemory(record: MemoryRecord) {
    await deleteMemory(record.id);
    setRecords((current) => current.filter((item) => item.id !== record.id));
  }

  const learned = getLearnedMemory(records);
  const headerContent = (
    <div className="section-topbar">
      <div className="section-topbar-copy">
        <p className="eyebrow">Context</p>
        <h2 className="section-topbar-title">Operational state and local context</h2>
      </div>
      <div className="section-topbar-actions filter-row">
        <span className="status-pill">{processes.length} processes</span>
        <span className="status-pill">{learned.length} memories</span>
        <span className="status-pill">{entries.length} workspace items</span>
      </div>
    </div>
  );

  return (
    <section className="single-panel-layout">
      <SectionTopbarPortal
        target={headerPortalTarget}
        fallback={<div className="section-page-header-fallback">{headerContent}</div>}
      >
        {headerContent}
      </SectionTopbarPortal>
      <div className="card panel-stack">
        <div className="runtime-kv-list context-summary-bar">
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Workspace root</span>
            <code className="runtime-kv-code">{workspaceRoot}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Current path</span>
            <code className="runtime-kv-code">{workspacePath}</code>
          </div>
        </div>

        <div className="panel-tabs" role="tablist" aria-label="Context categories">
          {CONTEXT_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`panel-tab${activeTab === tab.id ? " panel-tab-active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {tab.label}
            </button>
          ))}
        </div>

        {activeTab === "overview" ? (
          <ContextOverviewPanel
            processes={processes}
            records={records}
            workspaceRoot={workspaceRoot}
            entries={entries}
            onSelectTab={setActiveTab}
          />
        ) : null}
        {activeTab === "processes" ? (
          <ContextProcessesPanel items={processes} onRefresh={refreshProcesses} />
        ) : null}
        {activeTab === "memory" ? (
          <ContextMemoryPanel records={records} onDelete={handleDeleteMemory} />
        ) : null}
        {activeTab === "workspace" ? (
          <ContextWorkspacePanel workspaceRoot={workspaceRoot} entries={entries} />
        ) : null}

        <div className="panel-footer">
          <p className="hint-text">Context groups live process visibility, durable memory, and workspace scope.</p>
          <button type="button" className="ghost-button" onClick={() => void refreshContext()}>
            Refresh context
          </button>
        </div>
      </div>
    </section>
  );
}
