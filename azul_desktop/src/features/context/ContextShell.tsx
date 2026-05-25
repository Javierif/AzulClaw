import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

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

export function ContextShell({
  headerPortalTarget = null,
}: {
  headerPortalTarget?: HTMLElement | null;
}) {
  const { t } = useTranslation();
  const [activeTab, setActiveTab] = useState<ContextTab>("overview");
  const [processes, setProcesses] = useState<ProcessSummary[]>(processItems);
  const [records, setRecords] = useState<MemoryRecord[]>(memoryItems);
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [workspacePath, setWorkspacePath] = useState(".");
  const [entries, setEntries] = useState<WorkspaceEntry[]>(workspaceEntries);

  const CONTEXT_TABS: Array<{ id: ContextTab; labelKey: string }> = [
    { id: "overview", labelKey: "context.tabs.overview" },
    { id: "processes", labelKey: "context.tabs.processes" },
    { id: "memory", labelKey: "context.tabs.memory" },
    { id: "workspace", labelKey: "context.tabs.workspace" },
  ];

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
        <p className="eyebrow">{t("context.eyebrow")}</p>
        <h2 className="section-topbar-title">{t("context.title")}</h2>
      </div>
      <div className="section-topbar-actions filter-row">
        <span className="status-pill">{processes.length} {t("context.processes")}</span>
        <span className="status-pill">{learned.length} {t("context.memories")}</span>
        <span className="status-pill">{entries.length} {t("context.workspaceItems")}</span>
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
            <span className="runtime-kv-key">{t("context.workspaceRoot")}</span>
            <code className="runtime-kv-code">{workspaceRoot}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">{t("context.currentPath")}</span>
            <code className="runtime-kv-code">{workspacePath}</code>
          </div>
        </div>

        <div className="panel-tabs" role="tablist" aria-label={t("context.categories")}>
          {CONTEXT_TABS.map((tab) => (
            <button
              key={tab.id}
              type="button"
              role="tab"
              aria-selected={activeTab === tab.id}
              className={`panel-tab${activeTab === tab.id ? " panel-tab-active" : ""}`}
              onClick={() => setActiveTab(tab.id)}
            >
              {t(tab.labelKey)}
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
          <p className="hint-text">{t("context.footer")}</p>
          <button type="button" className="ghost-button" onClick={() => void refreshContext()}>
            {t("context.refresh")}
          </button>
        </div>
      </div>
    </section>
  );
}
