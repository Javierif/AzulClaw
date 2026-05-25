import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import type { ProcessSummary } from "../../lib/contracts";

interface ContextProcessesPanelProps {
  items: ProcessSummary[];
  onRefresh: () => void | Promise<void>;
}

export function ContextProcessesPanel({ items, onRefresh }: ContextProcessesPanelProps) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<ProcessSummary | null>(items[0] ?? null);

  useEffect(() => {
    setSelected((current) => {
      if (!items.length) return null;
      if (!current) return items[0] ?? null;
      return items.find((item) => item.id === current.id) ?? items[0] ?? null;
    });
  }, [items]);

  return (
    <section className="subcard panel-tab-panel" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
      <div className="panel-heading" style={{ flexShrink: 0 }}>
        <div>
          <p className="eyebrow">{t("process.eyebrow")}</p>
          <h3>{t("process.agentActivity")}</h3>
        </div>
        <div className="filter-row">
          <span className="process-count-pill process-count-running">
            <span className="process-count-dot process-dot-running" />
            {items.filter((item) => item.status === "running").length} {t("process.running")}
          </span>
          <span className="process-count-pill process-count-waiting">
            <span className="process-count-dot process-dot-waiting" />
            {items.filter((item) => item.status === "waiting").length} {t("process.waiting")}
          </span>
          <span className="process-count-pill process-count-done">
            <span className="process-count-dot process-dot-done" />
            {items.filter((item) => item.status === "done").length} {t("process.done")}
          </span>
        </div>
      </div>

      <div className="list-detail-grid" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
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

        <div className="subcard form-section" style={{ overflow: "hidden" }}>
          {selected ? (
            <>
              <div>
                <div style={{ display: "flex", alignItems: "center", gap: "10px", marginBottom: "6px" }}>
                  <span className={`process-status-dot process-dot-${selected.status}`} />
                  <p className="eyebrow" style={{ margin: 0 }}>{selected.status}</p>
                </div>
                <h3 style={{ margin: "0 0 4px" }}>{selected.title}</h3>
                <p style={{ margin: 0 }}>{selected.detail || t("process.noDetail")}</p>
              </div>

              <div className="runtime-kv-list" style={{ marginTop: "4px" }}>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">{t("process.skill")}</span>
                  <span className="runtime-kv-val">{selected.skill}</span>
                </div>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">{t("process.kind")}</span>
                  <code className="runtime-kv-code">{selected.kind}</code>
                </div>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">{t("process.lane")}</span>
                  <code className="runtime-kv-code">{selected.lane}</code>
                </div>
                {selected.modelLabel ? (
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">{t("process.model")}</span>
                    <span className="runtime-kv-val">{selected.modelLabel}</span>
                  </div>
                ) : null}
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">{t("process.started")}</span>
                  <span className="runtime-kv-val">{selected.startedAt}</span>
                </div>
                {selected.updatedAt ? (
                  <div className="runtime-kv-row">
                    <span className="runtime-kv-key">{t("process.updated")}</span>
                    <span className="runtime-kv-val">{selected.updatedAt}</span>
                  </div>
                ) : null}
              </div>

              <div className="filter-row" style={{ marginTop: "auto" }}>
                <button type="button" className="ghost-button" onClick={() => void onRefresh()}>
                  {t("process.refresh")}
                </button>
                <button type="button" className="primary-button">{t("process.runtimeLink")}</button>
              </div>
            </>
          ) : (
            <p style={{ color: "var(--muted)", margin: 0 }}>{t("process.selectProcess")}</p>
          )}
        </div>
      </div>
    </section>
  );
}
