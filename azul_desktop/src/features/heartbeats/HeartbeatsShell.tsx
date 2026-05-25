import { useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";

import { SectionTopbarPortal } from "../../components/SectionTopbarPortal";
import {
  deleteJob,
  loadJobs,
  loadRuntime,
  runJob,
  saveJob,
} from "../../lib/api";
import type {
  RuntimeOverview,
  ScheduledJob,
  ScheduledJobSecurityPolicy,
} from "../../lib/contracts";
import { runtimeOverview, scheduledJobs } from "../../lib/mock-data";
import i18n from "../../lib/i18n";

const INTERVAL_PRESETS = [
  { label: "5 min", seconds: 300 },
  { label: "15 min", seconds: 900 },
  { label: "30 min", seconds: 1800 },
  { label: "1 hour", seconds: 3600 },
  { label: "2 hours", seconds: 7200 },
] as const;

const fallbackSystemPolicy: ScheduledJobSecurityPolicy = {
  origin: "system",
  protected: true,
  execution_mode: "workspace_heartbeat",
  workspace_access: "heartbeat_md",
  tools_enabled: true,
  memory_context: "none",
  delivery_kind: "desktop_chat",
  suppress_noop_output: true,
  can_delete: false,
};

const fallbackUserPolicy: ScheduledJobSecurityPolicy = {
  origin: "user",
  protected: false,
  execution_mode: "proactive_message",
  workspace_access: "none",
  tools_enabled: false,
  memory_context: "none",
  delivery_kind: "desktop_chat",
  suppress_noop_output: false,
  can_delete: true,
};

type EditorMode = "create" | "edit";

type HeartbeatEditorForm = {
  id?: string;
  name: string;
  prompt: string;
  scheduleKind: "at" | "every" | "cron";
  intervalSeconds: string;
  cronExpression: string;
  runAt: string;
  enabled: boolean;
};

const emptyEditorForm: HeartbeatEditorForm = {
  name: "",
  prompt: "",
  scheduleKind: "every",
  intervalSeconds: "3600",
  cronExpression: "",
  runAt: "",
  enabled: true,
};

function humanInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
  const hours = seconds / 3600;
  return Number.isInteger(hours) ? `${hours} hour${hours === 1 ? "" : "s"}` : `${hours.toFixed(1)}h`;
}

function lastRunLabel(iso: string): string {
  if (!iso) return i18n.t("heartbeats.noRuns");
  try {
    const date = new Date(iso);
    const diff = Date.now() - date.getTime();
    if (diff < 60_000) return `${Math.max(1, Math.round(diff / 1000))}s ago`;
    if (diff < 3_600_000) return `${Math.round(diff / 60_000)} min ago`;
    if (diff < 86_400_000) return `${(diff / 3_600_000).toFixed(1)}h ago`;
    return date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

function nextRunLabel(iso: string): string {
  if (!iso) return i18n.t("heartbeats.pending");
  try {
    const date = new Date(iso);
    const diff = date.getTime() - Date.now();
    const abs = Math.abs(diff);
    if (abs < 60_000) {
      const seconds = Math.max(1, Math.round(abs / 1000));
      return diff >= 0 ? `In ${seconds}s` : `${seconds}s overdue`;
    }
    if (abs < 3_600_000) {
      const minutes = Math.round(abs / 60_000);
      return diff >= 0 ? `In ${minutes} min` : `${minutes} min overdue`;
    }
    if (abs < 86_400_000) {
      const hours = (abs / 3_600_000).toFixed(1);
      return diff >= 0 ? `In ${hours}h` : `${hours}h overdue`;
    }
    const absolute = date.toLocaleString([], { dateStyle: "medium", timeStyle: "short" });
    return diff >= 0 ? absolute : `${absolute} overdue`;
  } catch {
    return iso;
  }
}

function scheduleLabel(job: ScheduledJob): string {
  if (job.schedule_kind === "cron") return `Cron ${job.cron_expression || "pending"}`;
  if (job.schedule_kind === "every") return `Every ${humanInterval(job.interval_seconds)}`;
  if (!job.run_at) return "Once pending";
  try {
    return `Once ${new Date(job.run_at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
  } catch {
    return `Once ${job.run_at}`;
  }
}

function scheduleSummary(form: HeartbeatEditorForm, protectedJob: boolean): string {
  const scheduleKind = protectedJob ? "every" : form.scheduleKind;
  if (scheduleKind === "cron") {
    return form.cronExpression.trim() ? `Cron ${form.cronExpression.trim()}` : "Cron schedule";
  }
  if (scheduleKind === "at") {
    if (!form.runAt) return "One-time run";
    try {
      return `Once ${new Date(form.runAt).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}`;
    } catch {
      return "One-time run";
    }
  }
  return `Every ${humanInterval(Math.max(60, Number(form.intervalSeconds) || 3600))}`;
}

function buildTagsFromPolicy(policy: ScheduledJobSecurityPolicy): string[] {
  return [
    policy.origin === "system" ? "System" : "User",
    policy.protected ? "Protected" : "",
    policy.workspace_access === "heartbeat_md" ? "HEARTBEAT.md" : "No workspace",
    policy.tools_enabled ? "Tools" : "No tools",
    policy.delivery_kind === "desktop_chat" ? "Chat" : "No delivery",
  ].filter(Boolean);
}

function getPolicy(job: ScheduledJob): ScheduledJobSecurityPolicy {
  return job.security_policy ?? (job.system ? fallbackSystemPolicy : fallbackUserPolicy);
}

function getTags(job: ScheduledJob): string[] {
  if (job.tags?.length) return job.tags;
  return buildTagsFromPolicy(getPolicy(job));
}

function sortJobs(a: ScheduledJob, b: ScheduledJob): number {
  const protectedDelta = Number(getPolicy(b).protected) - Number(getPolicy(a).protected);
  if (protectedDelta !== 0) return protectedDelta;
  const aTime = a.next_run_at ? new Date(a.next_run_at).getTime() : Number.MAX_SAFE_INTEGER;
  const bTime = b.next_run_at ? new Date(b.next_run_at).getTime() : Number.MAX_SAFE_INTEGER;
  return aTime - bTime;
}

function toDateTimeLocalValue(iso: string): string {
  if (!iso) return "";
  const parsed = new Date(iso);
  if (Number.isNaN(parsed.getTime())) return "";
  const year = parsed.getFullYear();
  const month = String(parsed.getMonth() + 1).padStart(2, "0");
  const day = String(parsed.getDate()).padStart(2, "0");
  const hours = String(parsed.getHours()).padStart(2, "0");
  const minutes = String(parsed.getMinutes()).padStart(2, "0");
  return `${year}-${month}-${day}T${hours}:${minutes}`;
}

function buildEditorForm(job?: ScheduledJob): HeartbeatEditorForm {
  if (!job) return emptyEditorForm;
  return {
    id: job.id,
    name: job.name,
    prompt: job.prompt,
    scheduleKind: job.schedule_kind,
    intervalSeconds: String(job.interval_seconds || 3600),
    cronExpression: job.cron_expression || "",
    runAt: toDateTimeLocalValue(job.run_at),
    enabled: job.enabled,
  };
}

function modeLabel(policy: ScheduledJobSecurityPolicy): string {
  return policy.execution_mode === "workspace_heartbeat" ? i18n.t("heartbeats.modeWorkspace") : i18n.t("heartbeats.modeProactive");
}

function visibleTags(job: ScheduledJob): string[] {
  const policy = getPolicy(job);
  return getTags(job).filter((tag) => {
    if (tag === "Chat") return false;
    if (tag === "No delivery") return false;
    if (tag === "Tools" && policy.execution_mode === "workspace_heartbeat") return false;
    return true;
  });
}

export function HeartbeatsShell({
  headerPortalTarget = null,
}: {
  headerPortalTarget?: HTMLElement | null;
}) {
  const { t } = useTranslation();
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);
  const [jobs, setJobs] = useState<ScheduledJob[]>(scheduledJobs);
  const [editorMode, setEditorMode] = useState<EditorMode | null>(null);
  const [editorJobId, setEditorJobId] = useState<string | null>(null);
  const [editorForm, setEditorForm] = useState<HeartbeatEditorForm>(emptyEditorForm);
  const [runStatus, setRunStatus] = useState<Record<string, string>>({});
  const [runOutput, setRunOutput] = useState<Record<string, string>>({});

  useEffect(() => {
    let isMounted = true;

    async function refresh() {
      const [runtimeData, jobData] = await Promise.all([loadRuntime(), loadJobs()]);
      if (!isMounted) return;
      setRuntime(runtimeData);
      setJobs(jobData);
    }

    void refresh();
    const pollId = window.setInterval(() => void refresh(), 10_000);
    return () => {
      isMounted = false;
      window.clearInterval(pollId);
    };
  }, []);

  useEffect(() => {
    if (editorMode === null) return undefined;
    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setEditorMode(null);
        setEditorJobId(null);
        setEditorForm(emptyEditorForm);
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [editorMode]);

  const orderedJobs = useMemo(() => [...jobs].sort(sortJobs), [jobs]);
  const editingJob = editorJobId ? jobs.find((job) => job.id === editorJobId) ?? null : null;
  const editorPolicy = editingJob ? getPolicy(editingJob) : fallbackUserPolicy;
  const editorTags = editingJob ? getTags(editingJob) : buildTagsFromPolicy(fallbackUserPolicy);
  const editorOpen = editorMode !== null;
  const protectedEditor = editorMode === "edit" && editorPolicy.protected;
  const headerContent = (
    <div className="section-topbar">
      <div className="section-topbar-copy">
        <p className="eyebrow">{t("heartbeats.automations")}</p>
        <h2 className="section-topbar-title">{t("heartbeats.title")}</h2>
        <p className="section-topbar-description">{t("heartbeats.description")}</p>
      </div>
      <div className="section-topbar-actions filter-row">
        <span className={`status-pill${runtime.scheduler_running ? " status-pill-live" : ""}`}>
          {runtime.scheduler_running ? t("heartbeats.schedulerRunning") : t("heartbeats.schedulerStopped")}
        </span>
        <span className="status-pill">
          {runtime.jobs_total} {runtime.jobs_total === 1 ? t("heartbeats.heartbeat") : t("heartbeats.heartbeats")}
        </span>
        <button type="button" className="primary-button" onClick={openCreateModal}>
          {t("heartbeats.newHeartbeat")}
        </button>
      </div>
    </div>
  );

  function openCreateModal() {
    setEditorMode("create");
    setEditorJobId(null);
    setEditorForm(emptyEditorForm);
  }

  function openEditModal(job: ScheduledJob) {
    setEditorMode("edit");
    setEditorJobId(job.id);
    setEditorForm(buildEditorForm(job));
  }

  function closeEditor() {
    setEditorMode(null);
    setEditorJobId(null);
    setEditorForm(emptyEditorForm);
  }

  function updateEditorForm(patch: Partial<HeartbeatEditorForm>) {
    setEditorForm((current) => ({ ...current, ...patch }));
  }

  function handleSelectScheduleKind(kind: "at" | "every" | "cron") {
    if (protectedEditor && kind !== "every") return;
    updateEditorForm({ scheduleKind: kind });
  }

  async function persistJobUpdate(job: ScheduledJob, patch: Partial<ScheduledJob>) {
    const saved = await saveJob({
      id: job.id,
      name: patch.name ?? job.name,
      prompt: patch.prompt ?? job.prompt,
      lane: patch.lane ?? job.lane,
      schedule_kind: patch.schedule_kind ?? job.schedule_kind,
      interval_seconds: patch.interval_seconds ?? job.interval_seconds,
      cron_expression: patch.cron_expression ?? job.cron_expression,
      run_at: patch.run_at ?? job.run_at,
      enabled: patch.enabled ?? job.enabled,
      delivery_kind: patch.delivery_kind ?? job.delivery_kind,
      delivery_conversation_id: patch.delivery_conversation_id ?? job.delivery_conversation_id,
    });
    setJobs((current) => current.map((item) => (item.id === saved.id ? saved : item)));
  }

  async function handleSaveEditor() {
    if (!editorForm.name.trim() || !editorForm.prompt.trim()) return;

    const scheduleKind = protectedEditor ? "every" : editorForm.scheduleKind;
    const payload = {
      id: editorMode === "edit" ? editorJobId ?? undefined : undefined,
      name: editorForm.name.trim(),
      prompt: editorForm.prompt.trim(),
      lane: "auto" as const,
      schedule_kind: scheduleKind,
      enabled: editorForm.enabled,
      interval_seconds:
        scheduleKind === "every" ? Math.max(60, Number(editorForm.intervalSeconds) || 3600) : undefined,
      cron_expression: scheduleKind === "cron" ? editorForm.cronExpression.trim() : undefined,
      run_at:
        scheduleKind === "at" && editorForm.runAt
          ? new Date(editorForm.runAt).toISOString()
          : undefined,
      delivery_kind: editingJob?.delivery_kind,
      delivery_conversation_id: editingJob?.delivery_conversation_id || undefined,
    };

    const saved = await saveJob(payload);
    setJobs((current) => [saved, ...current.filter((job) => job.id !== saved.id)]);
    closeEditor();
  }

  async function handleToggleJob(job: ScheduledJob) {
    await persistJobUpdate(job, { enabled: !job.enabled });
  }

  async function handleRunJob(job: ScheduledJob) {
    setRunStatus((current) => ({ ...current, [job.id]: i18n.t("common.loading") }));
    setRunOutput((current) => ({ ...current, [job.id]: "" }));
    try {
      const result = await runJob(job.id);
      const deliveredTitle = result.delivery?.conversation_title;
      const deliveryError = (result.delivery?.error || "").trim();
      const output = [
        (result.response || result.error || "").trim(),
        deliveryError ? `Delivery issue: ${deliveryError}` : "",
      ]
        .filter(Boolean)
        .join("\n\n");
      setRunStatus((current) => ({
        ...current,
        [job.id]:
          deliveryError
            ? result.ok
              ? `Run completed, delivery issue: ${deliveryError}`
              : `Run failed, delivery issue: ${deliveryError}`
            : result.delivery?.kind === "desktop_chat" && deliveredTitle
              ? i18n.t("heartbeats.deliveredToChat", { title: deliveredTitle })
              : result.delivery?.kind === "desktop_chat"
                ? i18n.t("heartbeats.deliveredDesktop")
                : result.ok
                  ? i18n.t("heartbeats.runCompleted")
                  : i18n.t("heartbeats.runFailed"),
      }));
      if (output && output !== "HEARTBEAT_OK" && output !== "HEARTBEAT_SKIP") {
        setRunOutput((current) => ({ ...current, [job.id]: output }));
      }
      setJobs(await loadJobs());
    } catch {
      setRunStatus((current) => ({ ...current, [job.id]: i18n.t("heartbeats.runFailed") }));
    }
  }

  return (
    <section className="single-panel-layout">
      <SectionTopbarPortal
        target={headerPortalTarget}
        fallback={<div className="section-page-header-fallback">{headerContent}</div>}
      >
        {headerContent}
      </SectionTopbarPortal>
      <div className="card panel-stack">
        <div className="hb-board">
          <div className="hb-section-head">
            <div>
              <h3>{t("heartbeats.scheduled")}</h3>
            </div>
          </div>

          {orderedJobs.length === 0 ? (
            <div className="hb-empty">
              <p>{t("heartbeats.noHeartbeats")}</p>
              <button
                type="button"
                className="ghost-button"
                style={{ marginTop: 12 }}
                onClick={openCreateModal}
              >
                {t("heartbeats.createFirst")}
              </button>
            </div>
          ) : (
            <div className="hb-job-list">
              {orderedJobs.map((job) => {
                const policy = getPolicy(job);
                const tags = visibleTags(job);
                return (
                  <article
                    key={job.id}
                    className={`hb-job-card${policy.protected ? " hb-job-card-protected" : ""}`}
                  >
                    <div className="hb-job-main">
                      <div className="hb-card-heading">
                        <p className="hb-job-name">{job.name}</p>
                        <p className="hb-job-prompt">{job.prompt}</p>
                      </div>
                      <div className="hb-card-badges">
                        <span className={`hb-state-pill ${job.enabled ? "hb-state-pill-on" : "hb-state-pill-off"}`}>
                          {job.enabled ? t("heartbeats.enabled") : t("heartbeats.paused")}
                        </span>
                        {tags.map((tag) => (
                          <span key={tag} className="hb-job-tag">
                            {tag}
                          </span>
                        ))}
                      </div>
                    </div>

                    <div className="hb-card-stats">
                      <div className="hb-card-stat">
                        <span>{t("heartbeats.schedule")}</span>
                        <strong>{scheduleLabel(job)}</strong>
                      </div>
                      <div className="hb-card-stat">
                        <span>{t("heartbeats.nextRun")}</span>
                        <strong>{nextRunLabel(job.next_run_at)}</strong>
                      </div>
                      <div className="hb-card-stat">
                        <span>{t("heartbeats.lastRun")}</span>
                        <strong>{lastRunLabel(job.last_run_at)}</strong>
                      </div>
                      <div className="hb-card-stat">
                        <span>{t("heartbeats.mode")}</span>
                        <strong>{modeLabel(policy)}</strong>
                      </div>
                    </div>

                    <div className="hb-card-footer">
                      <div className="hb-job-actions">
                        <button
                          type="button"
                          className="hb-action-primary"
                          onClick={() => openEditModal(job)}
                        >
                          {t("heartbeats.edit")}
                        </button>
                        <button
                          type="button"
                          className="hb-action-secondary"
                          onClick={() => void handleToggleJob(job)}
                        >
                          {job.enabled ? t("common.pause") : t("common.enable")}
                        </button>
                        <button
                          type="button"
                          className="hb-action-secondary"
                          onClick={() => void handleRunJob(job)}
                        >
                          {t("common.runNow")}
                        </button>
                        {policy.can_delete ? (
                          <button
                            type="button"
                            className="hb-icon-btn hb-icon-btn-danger"
                            title="Delete"
                            aria-label={`Delete ${job.name}`}
                            onClick={() =>
                              void deleteJob(job.id).then(() =>
                                setJobs((current) => current.filter((item) => item.id !== job.id)),
                              )
                            }
                          >
                            x
                          </button>
                        ) : null}
                      </div>

                      {runStatus[job.id] ? <span className="hb-job-tag hb-job-runtime-tag">{runStatus[job.id]}</span> : null}
                    </div>

                    {runOutput[job.id] ? (
                      <div className="hb-run-output">
                        <span>{t("heartbeats.latestOutput")}</span>
                        <p>{runOutput[job.id]}</p>
                      </div>
                    ) : null}
                  </article>
                );
              })}
            </div>
          )}
        </div>
      </div>

      {editorOpen ? (
        <div className="hb-modal-overlay" onClick={closeEditor}>
          <div className="hb-modal-card hb-editor-modal-card" onClick={(event) => event.stopPropagation()}>
            <div className="hb-modal-header">
              <div>
                <p className="eyebrow">{editorMode === "create" ? t("heartbeats.newHeartbeatEyebrow") : t("heartbeats.editHeartbeatEyebrow")}</p>
                <h3>{editorMode === "create" ? t("heartbeats.create") : editorForm.name || t("heartbeats.editHeartbeatEyebrow")}</h3>
              </div>
              <button type="button" className="hb-modal-close" onClick={closeEditor}>
                x
              </button>
            </div>

            <div className="hb-editor-hero">
              <div>
                <p className="hb-editor-hero-title">
                  {editorMode === "create" ? t("heartbeats.scheduleNewHeartbeat") : scheduleSummary(editorForm, protectedEditor)}
                </p>
              </div>
              <div className="hb-editor-tag-row">
                {editorTags.map((tag) => (
                  <span key={tag} className="hb-job-tag">
                    {tag}
                  </span>
                ))}
              </div>
            </div>

            <div className="hb-editor-layout">
              <div className="hb-editor-main">
                <div className="hb-editor-panel">
                  <div className="hb-editor-panel-head">
                    <span>{t("heartbeats.basics")}</span>
                    <em>{modeLabel(editorPolicy)}</em>
                  </div>
                  <label className="form-field">
                    <span>{t("heartbeats.name")}</span>
                    <input
                      value={editorForm.name}
                      placeholder={t("heartbeats.namePlaceholder")}
                      onChange={(event) => updateEditorForm({ name: event.target.value })}
                    />
                  </label>

                  <label className="form-field">
                    <span>{t("heartbeats.instructions")}</span>
                    <textarea
                      className="hb-editor-textarea"
                      rows={8}
                      value={editorForm.prompt}
                      placeholder={t("heartbeats.instructionsPlaceholder")}
                      onChange={(event) => updateEditorForm({ prompt: event.target.value })}
                    />
                  </label>
                </div>
              </div>

              <div className="hb-editor-side">
                <div className="hb-editor-panel">
                  <div className="hb-editor-panel-head">
                    <span>{t("heartbeats.scheduleSection")}</span>
                    <em>{scheduleSummary(editorForm, protectedEditor)}</em>
                  </div>
                  <div className="hb-schedule-kind-row">
                    <button
                      type="button"
                      className={`hb-schedule-kind${editorForm.scheduleKind === "every" || protectedEditor ? " hb-schedule-kind-active" : ""}`}
                      onClick={() => handleSelectScheduleKind("every")}
                    >
                      {t("heartbeats.everyBtn")}
                    </button>
                    <button
                      type="button"
                      className={`hb-schedule-kind${editorForm.scheduleKind === "cron" && !protectedEditor ? " hb-schedule-kind-active" : ""}`}
                      onClick={() => handleSelectScheduleKind("cron")}
                      disabled={protectedEditor}
                    >
                      {t("heartbeats.cronBtn")}
                    </button>
                    <button
                      type="button"
                      className={`hb-schedule-kind${editorForm.scheduleKind === "at" && !protectedEditor ? " hb-schedule-kind-active" : ""}`}
                      onClick={() => handleSelectScheduleKind("at")}
                      disabled={protectedEditor}
                    >
                      {t("heartbeats.onceBtn")}
                    </button>
                  </div>

                  {protectedEditor ? (
                    <p className="hb-editor-note">
                      {t("heartbeats.protectedNote")}
                    </p>
                  ) : null}

                  {(editorForm.scheduleKind === "every" || protectedEditor) ? (
                    <div className="hb-schedule-detail">
                      <p className="hb-schedule-detail-label">{t("heartbeats.repeatInterval")}</p>
                      <div className="hb-interval-row">
                        {INTERVAL_PRESETS.map((preset) => (
                          <button
                            key={preset.seconds}
                            type="button"
                            className={`hb-interval-chip${Number(editorForm.intervalSeconds) === preset.seconds ? " hb-interval-chip-active" : ""}`}
                            onClick={() => updateEditorForm({ intervalSeconds: String(preset.seconds) })}
                          >
                            {preset.label}
                          </button>
                        ))}
                      </div>
                      <label className="form-field">
                        <span>{t("heartbeats.customInterval")}</span>
                        <input
                          type="number"
                          min={60}
                          value={editorForm.intervalSeconds}
                          onChange={(event) => updateEditorForm({ intervalSeconds: event.target.value })}
                        />
                      </label>
                    </div>
                  ) : null}

                  {editorForm.scheduleKind === "cron" && !protectedEditor ? (
                    <div className="hb-schedule-detail">
                      <p className="hb-schedule-detail-label">{t("heartbeats.cronTiming")}</p>
                      <label className="form-field">
                        <span>{t("heartbeats.cronExpression")}</span>
                        <input
                          value={editorForm.cronExpression}
                          placeholder={t("heartbeats.cronPlaceholder")}
                          onChange={(event) => updateEditorForm({ cronExpression: event.target.value })}
                        />
                      </label>
                    </div>
                  ) : null}

                  {editorForm.scheduleKind === "at" && !protectedEditor ? (
                    <div className="hb-schedule-detail">
                      <p className="hb-schedule-detail-label">{t("heartbeats.oneTimeTiming")}</p>
                      <label className="form-field">
                        <span>{t("heartbeats.runAt")}</span>
                        <input
                          type="datetime-local"
                          value={editorForm.runAt}
                          onChange={(event) => updateEditorForm({ runAt: event.target.value })}
                        />
                      </label>
                    </div>
                  ) : null}
                </div>

                <div className="hb-editor-panel">
                  <div className="hb-editor-panel-head">
                    <span>{t("heartbeats.statusSection")}</span>
                    <em>{editorForm.enabled ? t("heartbeats.willRunOnSchedule") : t("heartbeats.paused")}</em>
                  </div>
                  <div className="hb-segment-row">
                    <button
                      type="button"
                      className={`hb-interval-chip${editorForm.enabled ? " hb-interval-chip-active" : ""}`}
                      onClick={() => updateEditorForm({ enabled: true })}
                    >
                      {t("heartbeats.enabled")}
                    </button>
                    <button
                      type="button"
                      className={`hb-interval-chip${!editorForm.enabled ? " hb-interval-chip-active" : ""}`}
                      onClick={() => updateEditorForm({ enabled: false })}
                    >
                      {t("heartbeats.paused")}
                    </button>
                  </div>

                  <div className="hb-editor-capability-list">
                    <div className="hb-editor-capability">
                      <span>{t("heartbeats.workspaceAccess")}</span>
                      <strong>{editorPolicy.workspace_access === "heartbeat_md" ? t("heartbeats.heartbeatMdOnly") : t("common.disabled")}</strong>
                    </div>
                    <div className="hb-editor-capability">
                      <span>{t("heartbeats.tools")}</span>
                      <strong>{editorPolicy.tools_enabled ? t("heartbeats.available") : t("common.disabled")}</strong>
                    </div>
                    <div className="hb-editor-capability">
                      <span>{t("heartbeats.delivery")}</span>
                      <strong>{editorPolicy.delivery_kind === "desktop_chat" ? t("heartbeats.desktopChat") : t("heartbeats.noDelivery")}</strong>
                    </div>
                  </div>
                </div>
              </div>
            </div>

            <div className="hb-editor-footer">
              <span />
              <div className="filter-row" style={{ justifyContent: "flex-end" }}>
                <button type="button" className="ghost-button" onClick={closeEditor}>
                  {t("heartbeats.cancel")}
                </button>
                <button
                  type="button"
                  className="primary-button"
                  disabled={!editorForm.name.trim() || !editorForm.prompt.trim()}
                  onClick={() => void handleSaveEditor()}
                >
                  {editorMode === "create" ? t("heartbeats.create") : t("heartbeats.save")}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </section>
  );
}
