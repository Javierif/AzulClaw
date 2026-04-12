import { useEffect, useState } from "react";

import { loadMemory } from "../../lib/api";
import type { MemoryRecord } from "../../lib/contracts";
import { memoryItems } from "../../lib/mock-data";

const kindColor: Record<string, string> = {
  preference: "status-done",
  episodic: "status-running",
  semantic: "status-waiting",
  session: "",
};

export function MemoryShell() {
  const [records, setRecords] = useState<MemoryRecord[]>(memoryItems);
  const [selected, setSelected] = useState<MemoryRecord | null>(memoryItems[0] ?? null);

  useEffect(() => {
    let isMounted = true;

    loadMemory().then((data) => {
      if (isMounted) {
        setRecords(data);
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
            <p className="eyebrow">Memory</p>
            <h2>Agent memory</h2>
          </div>
          <div className="action-row">
            <button type="button" className="ghost-button">Clear session</button>
            <button type="button" className="primary-button">Export</button>
          </div>
        </div>

        <div className="list-detail-grid" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
          {/* ── Record list (scrolls) ────────────── */}
          <div className="subcard process-list-panel">
            {records.map((record) => (
              <article
                key={record.id}
                className={`process-row${selected?.id === record.id ? " process-row-active" : ""}`}
                onClick={() => setSelected(record)}
              >
                <div className="process-row-body">
                  <div className="process-row-title">
                    {record.pinned ? <span className="memory-pin">●</span> : null}
                    <strong>{record.title}</strong>
                  </div>
                  <p className="process-row-meta">{record.source}</p>
                </div>
                <div className="process-row-aside">
                  <span className={`status-tag ${kindColor[record.kind] ?? ""}`}>{record.kind}</span>
                </div>
              </article>
            ))}
          </div>

          {/* ── Detail panel (fixed) ─────────────── */}
          <div className="subcard form-section" style={{ overflow: "hidden" }}>
            {selected ? (
              <>
                <div>
                  <p className="eyebrow">{selected.kind}</p>
                  <h3 style={{ margin: "4px 0 0" }}>{selected.title}</h3>
                </div>

                <div className="runtime-kv-list">
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Source</span>
                    <span className="runtime-kv-val">{selected.source}</span>
                  </div>
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Kind</span>
                    <span className={`status-tag ${kindColor[selected.kind] ?? ""}`}>{selected.kind}</span>
                  </div>
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Pinned</span>
                    <span className="runtime-kv-val">{selected.pinned ? "Yes" : "No"}</span>
                  </div>
                </div>

                {selected.pinned ? (
                  <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--muted)", fontStyle: "italic" }}>
                    Persists across sessions.
                  </p>
                ) : null}

                <div className="filter-row" style={{ marginTop: "auto" }}>
                  <button type="button" className="ghost-button">Unpin</button>
                  <button type="button" className="ghost-button">Delete</button>
                </div>
              </>
            ) : (
              <p style={{ color: "var(--muted)", margin: 0 }}>Select a memory record to preview it.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
