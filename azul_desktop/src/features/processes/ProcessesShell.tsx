import { useEffect, useState } from "react";

import { loadProcesses } from "../../lib/api";
import { processItems } from "../../lib/mock-data";

export function ProcessesShell() {
  const [items, setItems] = useState(processItems);

  useEffect(() => {
    let isMounted = true;

    loadProcesses().then((data) => {
      if (isMounted) {
        setItems(data);
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
            <p className="eyebrow">Processes</p>
            <h2>Actividad interna del agente</h2>
          </div>
          <div className="filter-row">
            <span className="status-pill status-pill-live">Running</span>
            <span className="status-pill">Waiting</span>
            <span className="status-pill">Done</span>
          </div>
        </div>

        <div className="list-detail-grid">
          <div className="subcard">
            {items.map((item) => (
              <article key={item.id} className="list-row">
                <div>
                  <strong>{item.title}</strong>
                  <p>{item.skill}</p>
                </div>
                <div className="list-row-meta">
                  <span className={`status-tag status-${item.status}`}>{item.status}</span>
                  <span>{item.startedAt}</span>
                </div>
              </article>
            ))}
          </div>

          <div className="subcard">
            <p className="eyebrow">Detalle</p>
            <h3>Revisar documentos de Projects</h3>
            <p>Estado: running</p>
            <p>Skill: Workspace</p>
            <p>Ultimo paso: lectura de archivos y preparacion de resumen.</p>
            <div className="action-row">
              <button type="button" className="ghost-button">
                Pause
              </button>
              <button type="button" className="primary-button">
                Approve
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
