import { useEffect, useState } from "react";

import { loadProcesses } from "../../lib/api";
import type { ProcessSummary } from "../../lib/contracts";
import { processItems } from "../../lib/mock-data";

export function ProcessesShell() {
  const [items, setItems] = useState<ProcessSummary[]>(processItems);
  const [selected, setSelected] = useState<ProcessSummary | null>(processItems[0] ?? null);

  useEffect(() => {
    let isMounted = true;

    loadProcesses().then((data) => {
      if (isMounted) {
        setItems(data);
        setSelected(data[0] ?? null);
      }
    });

    return () => {
      isMounted = false;
    };
  }, []);

  return (
    <section className="detail-layout" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="card panel-stack" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <div className="panel-heading" style={{ flexShrink: 0 }}>
          <div>
            <p className="eyebrow">Processes</p>
            <h2>Agent internal activity</h2>
          </div>
          <div className="filter-row">
            <span className="process-count-pill process-count-running">
              <span className="process-count-dot process-dot-running" />
              {items.filter((i) => i.status === "running").length} running
            </span>
            <span className="process-count-pill process-count-waiting">
              <span className="process-count-dot process-dot-waiting" />
              {items.filter((i) => i.status === "waiting").length} waiting
            </span>
            <span className="process-count-pill process-count-done">
              <span className="process-count-dot process-dot-done" />
              {items.filter((i) => i.status === "done").length} done
            </span>
          </div>
        </div>

        <div className="list-detail-grid" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          {/* ── Process list (scrolls) ───────────── */}
          <div className="subcard process-list-panel">
            {items.map((item) => (
              <article
                key={item.id}
                className={`process-row${selected?.id === item.id ? " process-row-active" : ""}`}
                onClick={() => setSelected(item)}
              >
                <div className="process-row-body">
                  <div className="process-row-title">
                    <span className={`process-status-dot process-dot-${item.status}`} />
                    <strong>{item.title}</strong>
                  </div>
                  <p className="process-row-meta">{item.skill} · {item.kind}</p>
                </div>
                <div className="process-row-aside">
                  <span className={`status-tag status-${item.status}`}>{item.status}</span>
                  <span className="process-row-time">{item.startedAt}</span>
                </div>
              </article>
            ))}
          </div>

          {/* ── Detail panel (fixed, no scroll) ──── */}
          <div className="subcard form-section" style={{ overflow: "hidden" }}>
            {selected ? (
              <>
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                    <span className={`process-status-dot process-dot-${selected.status}`} />
                    <p className="eyebrow" style={{ margin: 0 }}>{selected.status}</p>
                  </div>
                  <h3 style={{ margin: "0 0 4px" }}>{selected.title}</h3>
                  <p style={{ margin: 0 }}>{selected.detail || "No detail available."}</p>
                </div>

                <div className="runtime-kv-list" style={{ marginTop: "4px" }}>
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Skill</span>
                    <span className="runtime-kv-val">{selected.skill}</span>
                  </div>
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Kind</span>
                    <code className="runtime-kv-code">{selected.kind}</code>
                  </div>
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Lane</span>
                    <code className="runtime-kv-code">{selected.lane}</code>
                  </div>
                  {selected.modelLabel ? (
                    <div className="runtime-kv-row">
                      <span className="runtime-kv-key">Model</span>
                      <span className="runtime-kv-val">{selected.modelLabel}</span>
                    </div>
                  ) : null}
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Started</span>
                    <span className="runtime-kv-val">{selected.startedAt}</span>
                  </div>
                  {selected.updatedAt ? (
                    <div className="runtime-kv-row">
                      <span className="runtime-kv-key">Updated</span>
                      <span className="runtime-kv-val">{selected.updatedAt}</span>
                    </div>
                  ) : null}
                </div>

                <div className="filter-row" style={{ marginTop: "auto" }}>
                  <button type="button" className="ghost-button">Refresh</button>
                  <button type="button" className="primary-button">Runtime →</button>
                </div>
              </>
            ) : (
              <p style={{ color: "var(--muted)", margin: 0 }}>Select a process to see its details.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
