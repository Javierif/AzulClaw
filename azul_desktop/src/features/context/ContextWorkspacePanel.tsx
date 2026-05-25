import { useMemo } from "react";
import { useTranslation } from "react-i18next";

import { Tooltip } from "../../components/Tooltip";
import type { WorkspaceEntry } from "../../lib/contracts";

interface ContextWorkspacePanelProps {
  workspaceRoot: string;
  entries: WorkspaceEntry[];
}

function getWorkspacePreview(entries: WorkspaceEntry[]): WorkspaceEntry | null {
  return entries.find((entry) => entry.kind === "file") ?? entries[0] ?? null;
}

export function ContextWorkspacePanel({ workspaceRoot, entries }: ContextWorkspacePanelProps) {
  const { t } = useTranslation();
  const preview = useMemo(() => getWorkspacePreview(entries), [entries]);

  function previewText(): string {
    if (!preview) return t("workspace.workspaceEmpty");
    if (preview.kind === "file") return t("workspace.workspaceFile", { name: preview.name });
    return t("workspace.workspaceFolder", { name: preview.name });
  }

  return (
    <section className="subcard panel-tab-panel" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
      <div className="panel-heading">
        <div>
          <p className="eyebrow">{t("workspace.eyebrow")}</p>
          <h3>{t("workspace.sandbox")}</h3>
        </div>
        <div className="action-row">
          <button type="button" className="ghost-button">
            {t("workspace.newFolder")}
          </button>
          <button type="button" className="primary-button">
            {t("workspace.openSystem")}
          </button>
        </div>
      </div>

      <div className="workspace-banner">
        <strong>{t("workspace.activePath")}</strong>
        <Tooltip text={workspaceRoot} className="workspace-banner-path">
          {workspaceRoot}
        </Tooltip>
        <span>{t("workspace.sandboxNote")}</span>
      </div>

      <div className="list-detail-grid" style={{ flex: 1, minHeight: 0, overflow: "hidden" }}>
        <div className="subcard" style={{ overflowY: "auto" }}>
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
          <p className="eyebrow">{t("workspace.preview")}</p>
          <h3>{preview ? preview.path : t("workspace.noPreview")}</h3>
          <p>{previewText()}</p>
        </div>
      </div>
    </section>
  );
}
