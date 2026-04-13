import { useEffect, useState } from "react";

import { Tooltip } from "../../components/Tooltip";
import { loadWorkspace } from "../../lib/api";
import { workspaceEntries } from "../../lib/mock-data";

export function WorkspaceShell() {
  const [workspaceRoot, setWorkspaceRoot] = useState("");
  const [entries, setEntries] = useState(workspaceEntries);

  useEffect(() => {
    let isMounted = true;

    loadWorkspace().then((data) => {
      if (isMounted) {
        setWorkspaceRoot(data.root);
        setEntries(data.entries);
      }
    });

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <section className="detail-layout">
      <div className="card panel-stack">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Workspace</p>
            <h2>AzulClaw operative sandbox</h2>
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

        <div className="list-detail-grid">
          <div className="subcard">
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
            <h3>/Generated/weekly-summary.md</h3>
            <p>
              Weekly summary prepared by AzulClaw from notes moved from Inbox
              and Projects.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
