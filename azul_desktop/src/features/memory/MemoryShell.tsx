import { useEffect, useState } from "react";

import { loadMemory } from "../../lib/api";
import { memoryItems } from "../../lib/mock-data";
import type { MemoryRecord } from "../../lib/contracts";

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
    <section className="detail-layout">
      <div className="card panel-stack">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Memory</p>
            <h2>Agent memory</h2>
          </div>
          <div className="action-row">
            <button type="button" className="ghost-button">
              Clear session
            </button>
            <button type="button" className="primary-button">
              Export
            </button>
          </div>
        </div>

        <div className="list-detail-grid">
          <div className="subcard">
            {records.map((record) => (
              <article
                key={record.id}
                className={`list-row${selected?.id === record.id ? " list-row--active" : ""}`}
                onClick={() => setSelected(record)}
                style={{ cursor: "pointer" }}
              >
                <div>
                  <strong>{record.title}</strong>
                  <p>{record.source}</p>
                </div>
                <div className="list-row-meta">
                  <span className="memory-kind">{record.kind}</span>
                  {record.pinned && <span className="memory-kind">pinned</span>}
                </div>
              </article>
            ))}
          </div>

          <div className="subcard">
            {selected ? (
              <>
                <p className="eyebrow">{selected.kind}</p>
                <h3>{selected.title}</h3>
                <p>Source: {selected.source}</p>
                {selected.pinned && <p><em>Pinned memory — will persist across sessions.</em></p>}
              </>
            ) : (
              <p>Select a memory record to preview it.</p>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}
