import type { MemoryRecord, ProcessSummary, WorkspaceEntry } from "../../lib/contracts";
import { getLearnedMemory, MEMORY_SOURCE_LABEL } from "./panel-utils";

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
            <p className="eyebrow">Processes</p>
            <h3>Current activity</h3>
          </div>
          <button type="button" className="ghost-button" onClick={() => onSelectTab("processes")}>
            Open tab
          </button>
        </div>
        <div className="filter-row">
          <span className="process-count-pill process-count-running">
            <span className="process-count-dot process-dot-running" />
            {processes.filter((item) => item.status === "running").length} running
          </span>
          <span className="process-count-pill process-count-waiting">
            <span className="process-count-dot process-dot-waiting" />
            {processes.filter((item) => item.status === "waiting").length} waiting
          </span>
          <span className="process-count-pill process-count-done">
            <span className="process-count-dot process-dot-done" />
            {processes.filter((item) => item.status === "done").length} done
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
            <p className="eyebrow">Memory</p>
            <h3>Learned context</h3>
          </div>
          <button type="button" className="ghost-button" onClick={() => onSelectTab("memory")}>
            Open tab
          </button>
        </div>
        <div className="filter-row">
          <span className="status-pill">{learned.length} learned</span>
          <span className="status-pill">{pinned} pinned</span>
        </div>
        <div className="context-overview-list">
          {recentMemory.map((record) => (
            <div key={record.id} className="context-overview-row">
              <div>
                <strong>{record.title}</strong>
                <p>{MEMORY_SOURCE_LABEL[record.source] ?? record.source}</p>
              </div>
              <span className="status-tag">{record.kind}</span>
            </div>
          ))}
        </div>
      </article>

      <article className="subcard context-overview-card">
        <div className="context-overview-head">
          <div>
            <p className="eyebrow">Workspace</p>
            <h3>Sandbox visibility</h3>
          </div>
          <button type="button" className="ghost-button" onClick={() => onSelectTab("workspace")}>
            Open tab
          </button>
        </div>
        <div className="runtime-kv-list">
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Root</span>
            <code className="runtime-kv-code">{workspaceRoot || "Unavailable"}</code>
          </div>
          <div className="runtime-kv-row">
            <span className="runtime-kv-key">Visible entries</span>
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
