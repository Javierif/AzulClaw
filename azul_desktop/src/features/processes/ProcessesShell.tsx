import { useEffect, useState } from "react";

import { loadProcesses } from "../../lib/api";
import { processItems } from "../../lib/mock-data";

export function ProcessesShell() {
  const [items, setItems] = useState(processItems);
  const selected = items[0];

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
                  <p>{item.skill} · {item.kind}</p>
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
            <h3>{selected?.title || "Sin procesos"}</h3>
            <p>Estado: {selected?.status || "idle"}</p>
            <p>Origen: {selected?.skill || "runtime"}</p>
            <p>Cerebro: {selected?.lane || "auto"} {selected?.modelLabel ? `· ${selected.modelLabel}` : ""}</p>
            <p>Detalle: {selected?.detail || "Esperando nuevas ejecuciones."}</p>
            <div className="action-row">
              <button type="button" className="ghost-button">
                Refresh
              </button>
              <button type="button" className="primary-button">
                Runtime
              </button>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
