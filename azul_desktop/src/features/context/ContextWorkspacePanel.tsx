import { useMemo } from "react";

import { Tooltip } from "../../components/Tooltip";
import type { WorkspaceEntry } from "../../lib/contracts";

interface ContextWorkspacePanelProps {
  workspaceRoot: string;
  entries: WorkspaceEntry[];
}

function getWorkspacePreview(entries: WorkspaceEntry[]): WorkspaceEntry | null {
  return entries.find((entry) => entry.kind === "file") ?? entries[0] ?? null;
}

export function ContextWorkspacePanel({ workspaceRoot, entries }: ContextWorkspacePanelProps) {
  const preview = useMemo(() => getWorkspacePreview(entries), [entries]);

  return (
    <section className="subcard panel-tab-panel" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">Workspace</p>
          <h3>AzulClaw operative sandbox</h3>
        </div>
        <div className="action-row">
          <button type="button" className="ghost-button">
            New folder
          </button>
          <button type="button" className="primary-button">
            Open in system
          </button>
        </div>
      </div>

      <div className="workspace-banner">
        <strong>Active path</strong>
        <Tooltip text={workspaceRoot} className="workspace-banner-path">
          {workspaceRoot}
        </Tooltip>
        <span>AzulClaw can only read and write within this sandbox.</span>
      </div>

      <div className="list-detail-grid" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <div className="subcard" style={{ overflowY: "auto" }}>
          {entries.map((entry) => (
            <article key={entry.path} className="list-row">
              <div style={{ minWidth: 0 }}>
                <strong>{entry.name}</strong>
                <Tooltip text={entry.path} className="list-path">
                  {entry.path}
                </Tooltip>
              </div>
              <div className="list-row-meta">
                <span className="memory-kind">{entry.kind}</span>
              </div>
            </article>
          ))}
        </div>

        <div className="subcard">
          <p className="eyebrow">Preview</p>
          <h3>{preview ? preview.path : "No preview available"}</h3>
          <p>
            {preview?.kind === "file"
              ? `Workspace file ${preview.name} is available inside the active sandbox.`
              : preview?.kind === "folder"
                ? `${preview.name} is part of the current sandbox structure.`
                : "The workspace listing is empty."}
          </p>
        </div>
      </div>
    </section>
  );
}
