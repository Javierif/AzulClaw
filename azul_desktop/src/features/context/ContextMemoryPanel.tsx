import { useEffect, useState } from "react";

import type { MemoryRecord } from "../../lib/contracts";
import {
  formatMemoryDate,
  getLearnedMemory,
  isDeletableMemory,
  MEMORY_KIND_COLOR,
  MEMORY_KIND_LABEL,
  MEMORY_SOURCE_LABEL,
} from "./panel-utils";

interface ContextMemoryPanelProps {
  records: MemoryRecord[];
  onDelete: (record: MemoryRecord) => Promise<void>;
}

export function ContextMemoryPanel({ records, onDelete }: ContextMemoryPanelProps) {
  const learned = getLearnedMemory(records);
  const [selected, setSelected] = useState<MemoryRecord | null>(learned[0] ?? null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    setSelected((current) => {
      if (!learned.length) return null;
      if (!current) return learned[0] ?? null;
      return learned.find((record) => record.id === current.id) ?? learned[0] ?? null;
    });
  }, [learned]);

  async function handleDelete(record: MemoryRecord) {
    if (!isDeletableMemory(record)) return;
    setDeleteError("");
    setIsDeleting(true);
    try {
      await onDelete(record);
    } catch {
      setDeleteError("Could not delete. Check the backend is running.");
    } finally {
      setIsDeleting(false);
    }
  }

  return (
    <section className="subcard panel-tab-panel" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
      <div className="panel-heading" style={{ flexShrink: 0 }}>
        <div>
          <p className="eyebrow">Memory</p>
          <h3>Agent memory</h3>
        </div>
        <div className="filter-row">
          <span className="status-pill">{learned.length} learned</span>
        </div>
      </div>

      <div className="list-detail-grid" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <div className="subcard process-list-panel" style={{ overflowY: "auto" }}>
          {learned.map((record) => (
            <article
              key={record.id}
              className={`process-row${selected?.id === record.id ? " process-row-active" : ""}`}
              onClick={() => {
                setSelected(record);
                setDeleteError("");
              }}
            >
              <div className="process-row-body">
                <div className="process-row-title">
                  {record.pinned ? (
                    <span style={{ color: "var(--accent, #2563eb)", marginRight: "6px", fontSize: "0.6rem" }}>●</span>
                  ) : null}
                  <strong style={{ fontSize: "0.82rem" }}>{record.title}</strong>
                </div>
                <p className="process-row-meta">{MEMORY_SOURCE_LABEL[record.source] ?? record.source}</p>
              </div>
              <div className="process-row-aside">
                <span className={`status-tag ${MEMORY_KIND_COLOR[record.kind] ?? ""}`}>
                  {MEMORY_KIND_LABEL[record.kind] ?? record.kind}
                </span>
              </div>
            </article>
          ))}

          {records.length === 0 ? (
            <p style={{ padding: "20px 14px", color: "var(--muted)", margin: 0, fontSize: "0.85rem" }}>
              No memories yet. Chat with AzulClaw to start building context.
            </p>
          ) : null}
        </div>

        <div className="subcard form-section" style={{ overflow: "auto", display: "flex", flexDirection: "column", gap: "16px" }}>
          {selected ? (
            <>
              <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                <span className={`status-tag ${MEMORY_KIND_COLOR[selected.kind] ?? ""}`}>
                  {MEMORY_KIND_LABEL[selected.kind] ?? selected.kind}
                </span>
                {selected.pinned ? (
                  <span style={{ fontSize: "0.75rem", color: "var(--accent, #2563eb)", fontWeight: 600, letterSpacing: "0.04em" }}>
                    ● PINNED
                  </span>
                ) : null}
              </div>

              <div style={{
                background: "var(--surface-2, rgba(255,255,255,0.04))",
                border: "1px solid var(--line, rgba(255,255,255,0.08))",
                borderLeft: "3px solid var(--accent, #2563eb)",
                borderRadius: "6px",
                padding: "14px 16px",
              }}>
                <p style={{ margin: 0, lineHeight: 1.65, fontSize: "0.92rem", color: "var(--text, #e2e8f0)", wordBreak: "break-word" }}>
                  {selected.content || selected.title}
                </p>
              </div>

              <div className="runtime-kv-list">
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">Source</span>
                  <span className="runtime-kv-val">{MEMORY_SOURCE_LABEL[selected.source] ?? selected.source}</span>
                </div>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">Kind</span>
                  <span className={`status-tag ${MEMORY_KIND_COLOR[selected.kind] ?? ""}`}>
                    {MEMORY_KIND_LABEL[selected.kind] ?? selected.kind}
                  </span>
                </div>
                {selected.created_at ? (
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Saved</span>
                    <span className="runtime-kv-val">{formatMemoryDate(selected.created_at)}</span>
                  </div>
                ) : null}
                {selected.pinned ? (
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">Persistence</span>
                    <span className="runtime-kv-val" style={{ color: "var(--accent, #2563eb)" }}>Saved across sessions</span>
                  </div>
                ) : null}
              </div>

              {deleteError ? (
                <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--danger, #b42318)" }}>{deleteError}</p>
              ) : null}

              {isDeletableMemory(selected) ? (
                <div style={{ marginTop: "auto", display: "flex", justifyContent: "flex-end" }}>
                  <button
                    type="button"
                    className="ghost-button"
                    style={{ color: "var(--danger, #b42318)", borderColor: "var(--danger, #b42318)" }}
                    disabled={isDeleting}
                    onClick={() => void handleDelete(selected)}
                  >
                    {isDeleting ? "Deleting..." : "Delete memory"}
                  </button>
                </div>
              ) : null}
            </>
          ) : (
            <p style={{ color: "var(--muted)", margin: 0, fontSize: "0.85rem" }}>
              Select a memory record to preview it.
            </p>
          )}
        </div>
      </div>
    </section>
  );
}
