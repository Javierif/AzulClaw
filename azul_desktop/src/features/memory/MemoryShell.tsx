import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { deleteMemory, loadMemory } from "../../lib/api";
import type { MemoryRecord } from "../../lib/contracts";
import { memoryItems } from "../../lib/mock-data";

const KIND_COLOR: Record<string, string> = {
  preference: "status-done",
  semantic: "status-waiting",
  episodic: "status-running",
  session: "",
};

function formatDate(iso: string): string {
  if (!iso) return "";
  try {
    return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
  } catch {
    return "";
  }
}

function isDeletable(record: MemoryRecord): boolean {
  return !record.pinned;
}

export function MemoryShell() {
  const { t } = useTranslation();
  const [records, setRecords] = useState<MemoryRecord[]>(memoryItems);
  const [selected, setSelected] = useState<MemoryRecord | null>(memoryItems[0] ?? null);
  const [isDeleting, setIsDeleting] = useState(false);
  const [deleteError, setDeleteError] = useState("");

  useEffect(() => {
    let isMounted = true;
    loadMemory().then((data) => {
      if (!isMounted) return;
      setRecords(data);
      setSelected(data[0] ?? null);
    });
    return () => { isMounted = false; };
  }, []);

  async function handleDelete(record: MemoryRecord) {
    if (!isDeletable(record)) return;
    setDeleteError("");
    setIsDeleting(true);
    try {
      await deleteMemory(record.id);
      const next = records.filter((r) => r.id !== record.id);
      setRecords(next);
      setSelected(next[0] ?? null);
    } catch {
      setDeleteError(t("memory.deleteError"));
    } finally {
      setIsDeleting(false);
    }
  }

  const learned = records
    .filter((r) => r.kind === "preference" || r.kind === "semantic")
    .sort((a, b) => {
      const aFeatured = a.source === "featured" || a.source === "hatching-profile" ? 0 : 1;
      const bFeatured = b.source === "featured" || b.source === "hatching-profile" ? 0 : 1;
      return aFeatured - bFeatured;
    });

  const kindLabel = (kind: string) => t(`memory.kinds.${kind}`, { defaultValue: kind });
  const sourceLabel = (source: string) => {
    const key = source === "hatching-profile" ? "hatchingProfile" : source;
    return t(`memory.sources.${key}`, { defaultValue: source });
  };

  return (
    <section className="detail-layout" style={{ display: "flex", flexDirection: "column", overflow: "hidden" }}>
      <div className="card panel-stack" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>

        <div className="panel-heading" style={{ flexShrink: 0 }}>
          <div>
            <p className="eyebrow">{t("memory.eyebrow")}</p>
            <h2>{t("memory.agentMemory")}</h2>
          </div>
          <div className="filter-row">
            <span className="status-pill">{learned.length} {t("memory.learned")}</span>
          </div>
        </div>

        <div className="list-detail-grid" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>

          <div className="subcard process-list-panel" style={{ overflowY: "auto" }}>
            {learned.map((record) => (
              <article
                key={record.id}
                className={`process-row${selected?.id === record.id ? " process-row-active" : ""}`}
                onClick={() => { setSelected(record); setDeleteError(""); }}
              >
                <div className="process-row-body">
                  <div className="process-row-title">
                    {record.pinned && <span style={{ color: "var(--accent, #2563eb)", marginRight: "6px", fontSize: "0.6rem" }}>●</span>}
                    <strong style={{ fontSize: "0.82rem" }}>{record.title}</strong>
                  </div>
                  <p className="process-row-meta">{sourceLabel(record.source)}</p>
                </div>
                <div className="process-row-aside">
                  <span className={`status-tag ${KIND_COLOR[record.kind] ?? ""}`}>{kindLabel(record.kind)}</span>
                </div>
              </article>
            ))}

            {records.length === 0 && (
              <p style={{ padding: "20px 14px", color: "var(--muted)", margin: 0, fontSize: "0.85rem" }}>
                {t("memory.noMemoriesYet")}
              </p>
            )}
          </div>

          <div className="subcard form-section" style={{ overflow: "auto", display: "flex", flexDirection: "column", gap: "16px" }}>
            {selected ? (
              <>
                <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "12px", flexWrap: "wrap" }}>
                  <span className={`status-tag ${KIND_COLOR[selected.kind] ?? ""}`}>
                    {kindLabel(selected.kind)}
                  </span>
                  {selected.pinned && (
                    <span style={{ fontSize: "0.75rem", color: "var(--accent, #2563eb)", fontWeight: 600, letterSpacing: "0.04em" }}>
                      ● {t("memory.pinned")}
                    </span>
                  )}
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
                    <span className="runtime-kv-key">{t("memory.source")}</span>
                    <span className="runtime-kv-val">{sourceLabel(selected.source)}</span>
                  </div>
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">{t("memory.kind")}</span>
                    <span className={`status-tag ${KIND_COLOR[selected.kind] ?? ""}`}>{kindLabel(selected.kind)}</span>
                  </div>
                  {selected.created_at && (
                    <div className="runtime-kv-row">
                      <span className="runtime-kv-key">{t("memory.saved")}</span>
                      <span className="runtime-kv-val">{formatDate(selected.created_at)}</span>
                    </div>
                  )}
                  {selected.pinned && (
                    <div className="runtime-kv-row">
                      <span className="runtime-kv-key">{t("memory.persistence")}</span>
                      <span className="runtime-kv-val" style={{ color: "var(--accent, #2563eb)" }}>{t("memory.savedAcrossSessions")}</span>
                    </div>
                  )}
                </div>

                {deleteError && (
                  <p style={{ margin: 0, fontSize: "0.82rem", color: "var(--danger, #b42318)" }}>{deleteError}</p>
                )}

                {isDeletable(selected) && (
                  <div style={{ marginTop: "auto", display: "flex", justifyContent: "flex-end" }}>
                    <button
                      type="button"
                      className="ghost-button"
                      style={{ color: "var(--danger, #b42318)", borderColor: "var(--danger, #b42318)" }}
                      disabled={isDeleting}
                      onClick={() => void handleDelete(selected)}
                    >
                      {isDeleting ? t("common.deleting") : t("memory.deleteMemory")}
                    </button>
                  </div>
                )}
              </>
            ) : (
              <p style={{ color: "var(--muted)", margin: 0, fontSize: "0.85rem" }}>
                {t("memory.selectRecord")}
              </p>
            )}
          </div>

        </div>
      </div>
    </section>
  );
}
