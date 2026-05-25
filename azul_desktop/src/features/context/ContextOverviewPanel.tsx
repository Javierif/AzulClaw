import { useTranslation } from "react-i18next";

import type { MemoryRecord, ProcessSummary, WorkspaceEntry } from "../../lib/contracts";
import { getLearnedMemory, memorySourceLabel } from "./panel-utils";

interface ContextOverviewPanelProps {
  processes: ProcessSummary[];
  records: MemoryRecord[];
  workspaceRoot: string;
  entries: WorkspaceEntry[];
  onSelectTab: (tab: "processes" | "memory" | "workspace") => void;
}

export function ContextOverviewPanel({
  processes,
  records,
  workspaceRoot,
  entries,
  onSelectTab,
}: ContextOverviewPanelProps) {
  const { t } = useTranslation();
  const learned = getLearnedMemory(records);
  const pinned = learned.filter((record) => record.pinned).length;
  const recentProcesses = processes.slice(0, 3);
  const recentMemory = learned.slice(0, 3);
  const visibleEntries = entries.slice(0, 5);

  return (
    <section className="panel-tab-panel context-overview-grid">
      <article className="subcard context-overview-card">
        <div className="context-overview-head">
          <div>
            <p className="eyebrow">{t("context.tabs.processes")}</p>
            <h3>{t("context.overview.currentActivity")}</h3>
          </div>
          <button type="button" className="ghost-button" onClick={() => onSelectTab("processes")}>
            {t("context.overview.openTab")}
          </button>
        </div>
        <div className="filter-row">
          <span className="process-count-pill process-count-running">
            <span className="process-count-dot process-dot-running" />
            {processes.filter((item) => item.status === "running").length} {t("context.overview.running")}
          </span>
          <span className="process-count-pill process-count-waiting">
            <span className="process-count-dot process-dot-waiting" />
            {processes.filter((item) => item.status === "waiting").length} {t("context.overview.waiting")}
          </span>
          <span className="process-count-pill process-count-done">
            <span className="process-count-dot process-dot-done" />
            {processes.filter((item) => item.status === "done").length} {t("context.overview.done")}
          </span>
        </div>
        <div className="context-overview-list">
          {recentProcesses.map((item) => (
            <div key={item.id} className="context-overview-row">
              <div>
                <strong>{item.title}</strong>
                <p>{item.skill} · {item.kind}</p>
              </div>
              <span className={`status-tag status-${item.status}`}>{item.status}</span>
            </div>
          ))}
        </div>
      </article>

      <article className="subcard context-overview-card">
        <div className="context-overview-head">
          <div>
            <p className="eyebrow">{t("context.tabs.memory")}</p>
            <h3>{t("context.overview.learnedContext")}</h3>
          </div>
          <button type="button" className="ghost-button" onClick={() => onSelectTab("memory")}>
            {t("context.overview.openTab")}
          </button>
        </div>
        <div className="filter-row">
          <span className="status-pill">{learned.length} {t("context.overview.learned")}</span>
          <span className="status-pill">{pinned} {t("context.overview.pinned")}</span>
        </div>
        <div className="context-overview-list">
          {recentMemory.map((record) => (
            <div key={record.id} className="context-overview-row">
              <div>
                <strong>{record.title}</strong>
                <p>{memorySourceLabel(record.source)}</p>
              </div>
              <span className="status-tag">{record.kind}</span>
            </div>
          ))}
        </div>
      </article>

      <article className="subcard context-overview-card">
        <div className="context-overview-head">
          <div>
            <p className="eyebrow">{t("context.tabs.workspace")}</p>
            <h3>{t("context.overview.sandboxVisibility")}</h3>
          </div>
          <button type="button" className="ghost-button" onClick={() => onSelectTab("workspace")}>
            {t("context.overview.openTab")}
          </button>
        </div>
        <div className="runtime-kv-list">
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">{t("context.overview.root")}</span>
            <code className="runtime-kv-code">{workspaceRoot || t("context.overview.unavailable")}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">{t("context.overview.visibleEntries")}</span>
            <span className="runtime-kv-val">{entries.length}</span>
          </div>
        </div>
        <div className="context-overview-list">
          {visibleEntries.map((entry) => (
            <div key={entry.path} className="context-overview-row">
              <div>
                <strong>{entry.name}</strong>
                <p>{entry.path}</p>
              </div>
              <span className="status-tag">{entry.kind}</span>
            </div>
          ))}
        </div>
      </article>
    </section>
  );
}
