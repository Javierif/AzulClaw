import { useEffect, useState } from "react";

import { loadWorkspace } from "../../lib/api";
import { workspaceEntries } from "../../lib/mock-data";

export function WorkspaceShell() {
  const [workspaceRoot, setWorkspaceRoot] = useState("C:\\Users\\javie\\Desktop\\AzulWorkspace");
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
            <h2>Sandbox operativo de AzulClaw</h2>
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
          <strong>Ruta activa</strong>
          <code>{workspaceRoot}</code>
          <span>AzulClaw solo puede leer y escribir dentro de esta jaula.</span>
        </div>

        <div className="list-detail-grid">
          <div className="subcard">
            {entries.map((entry) => (
              <article key={entry.path} className="list-row">
                <div>
                  <strong>{entry.name}</strong>
                  <p>{entry.path}</p>
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
              Resumen semanal preparado por AzulClaw a partir de notas movidas
              desde Inbox y Projects.
            </p>
          </div>
        </div>
      </div>
    </section>
  );
}
