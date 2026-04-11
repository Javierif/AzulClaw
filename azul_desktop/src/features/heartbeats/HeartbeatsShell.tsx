import { useEffect, useState } from "react";

import {
  deleteJob,
  loadJobs,
  loadRuntime,
  runJob,
  saveJob,
} from "../../lib/api";
import type { RuntimeOverview, ScheduledJob } from "../../lib/contracts";
import { runtimeOverview, scheduledJobs } from "../../lib/mock-data";

/* ── Helpers ───────────────────────────────────── */

const INTERVAL_PRESETS = [
  { label: "5 min", seconds: 300 },
  { label: "15 min", seconds: 900 },
  { label: "30 min", seconds: 1800 },
  { label: "1 hour", seconds: 3600 },
  { label: "2 hours", seconds: 7200 },
] as const;

function humanInterval(seconds: number): string {
  if (seconds < 60) return `${seconds}s`;
  if (seconds < 3600) {
    const m = Math.round(seconds / 60);
    return `${m} min`;
  }
  const h = seconds / 3600;
  if (Number.isInteger(h)) return `${h}h`;
  return `${h.toFixed(1)}h`;
}

function timeAgo(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    const diff = Date.now() - d.getTime();
    if (diff < 0) {
      const abs = Math.abs(diff);
      if (abs < 60_000) return `in ${Math.round(abs / 1000)}s`;
      if (abs < 3_600_000) return `in ${Math.round(abs / 60_000)} min`;
      return `in ${(abs / 3_600_000).toFixed(1)}h`;
    }
    if (diff < 60_000) return `${Math.round(diff / 1000)}s ago`;
    if (diff < 3_600_000) return `${Math.round(diff / 60_000)} min ago`;
    return `${(diff / 3_600_000).toFixed(1)}h ago`;
  } catch {
    return iso;
  }
}

/* ── Types ─────────────────────────────────────── */

type JobDraft = {
  name: string;
  prompt: string;
  intervalSeconds: string;
};

const emptyDraft: JobDraft = { name: "", prompt: "", intervalSeconds: "3600" };

/* ── Component ─────────────────────────────────── */

export function HeartbeatsShell() {
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);
  const [jobs, setJobs] = useState<ScheduledJob[]>(scheduledJobs);
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<JobDraft>(emptyDraft);
  const [editingSystemPrompt, setEditingSystemPrompt] = useState(false);

  /* Polling */
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

  /* Derived data */
  const systemJob = jobs.find((j) => j.system);
  const userJobs = jobs.filter((j) => !j.system);

  /* Actions */
  async function handleToggleJob(job: ScheduledJob) {
    const saved = await saveJob({
      ...job,
      enabled: !job.enabled,
    } as Parameters<typeof saveJob>[0] & { id: string; enabled: boolean });
    setJobs((cur) => cur.map((j) => (j.id === saved.id ? saved : j)));
  }

  async function handleUpdateSystemJob(patch: Partial<ScheduledJob>) {
    if (!systemJob) return;
    const saved = await saveJob({
      id: systemJob.id,
      name: systemJob.name,
      prompt: patch.prompt ?? systemJob.prompt,
      lane: "auto",
      schedule_kind: systemJob.schedule_kind,
      interval_seconds: patch.interval_seconds ?? systemJob.interval_seconds,
      enabled: patch.enabled ?? systemJob.enabled,
    });
    setJobs((cur) => cur.map((j) => (j.id === saved.id ? saved : j)));
  }

  async function handleCreateJob() {
    if (!form.name.trim() || !form.prompt.trim()) return;
    const saved = await saveJob({
      name: form.name.trim(),
      prompt: form.prompt.trim(),
      lane: "auto",
      schedule_kind: "every",
      interval_seconds: Number(form.intervalSeconds) || 3600,
    });
    setJobs((cur) => [saved, ...cur.filter((j) => j.id !== saved.id)]);
    setForm(emptyDraft);
    setModalOpen(false);
  }

  const systemIsPreset = systemJob
    ? INTERVAL_PRESETS.some((p) => p.seconds === systemJob.interval_seconds)
    : false;

  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        {/* ── Header ─────────────────────────────── */}
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Automations</p>
            <h2>Heartbeats</h2>
            <p style={{ color: "var(--muted)", fontSize: "0.88rem", marginTop: 4, maxWidth: 520 }}>
              Heartbeats are recurring tasks that keep the agent alive. Each one runs on a schedule, checks the workspace, and acts on what it finds.
            </p>
          </div>
          <div className="filter-row">
            <span className={`status-pill${runtime.scheduler_running ? " status-pill-live" : ""}`}>
              {runtime.scheduler_running ? "Scheduler running" : "Scheduler stopped"}
            </span>
            <span className="status-pill">
              {runtime.jobs_total} {runtime.jobs_total === 1 ? "heartbeat" : "heartbeats"}
            </span>
          </div>
        </div>

        {/* ── System Heartbeat Job ────────────────── */}
        {systemJob && (
          <div className="subcard" style={{ borderLeft: "3px solid var(--brand)" }}>
            <div className="hb-section-head">
              <div>
                <p className="eyebrow" style={{ display: "flex", alignItems: "center", gap: 6 }}>
                  <span>🔒</span> System
                </p>
                <h3>{systemJob.name}</h3>
              </div>
              <div className="filter-row">
                <span
                  className={`status-pill${systemJob.enabled ? " status-pill-live" : ""}`}
                  style={{ cursor: "pointer" }}
                  onClick={() => void handleUpdateSystemJob({ enabled: !systemJob.enabled })}
                  title={systemJob.enabled ? "Click to pause" : "Click to enable"}
                >
                  {systemJob.enabled ? "Active" : "Paused"}
                </span>
              </div>
            </div>

            {/* Interval chips */}
            <div style={{ marginTop: 12 }}>
              <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>Interval</span>
              <div className="hb-interval-row" style={{ marginTop: 8 }}>
                {INTERVAL_PRESETS.map((p) => (
                  <button
                    key={p.seconds}
                    type="button"
                    className={`hb-interval-chip${systemJob.interval_seconds === p.seconds ? " hb-interval-chip-active" : ""}`}
                    onClick={() => void handleUpdateSystemJob({ interval_seconds: p.seconds })}
                  >
                    {p.label}
                  </button>
                ))}
                <button
                  type="button"
                  className={`hb-interval-chip${!systemIsPreset ? " hb-interval-chip-active" : ""}`}
                  onClick={() => {
                    const raw = prompt("Interval in seconds:", String(systemJob.interval_seconds));
                    if (raw) {
                      const val = Number(raw);
                      if (val >= 60) void handleUpdateSystemJob({ interval_seconds: val });
                    }
                  }}
                >
                  {systemIsPreset ? "Custom" : humanInterval(systemJob.interval_seconds)}
                </button>
              </div>
            </div>

            {/* Prompt accordion */}
            <div className={`hb-accordion${editingSystemPrompt ? " hb-accordion-open" : ""}`}>
              <button
                type="button"
                className="hb-accordion-trigger"
                onClick={() => setEditingSystemPrompt((o) => !o)}
              >
                <span>Edit heartbeat prompt</span>
                <span className="hb-accordion-chevron">▼</span>
              </button>
              {editingSystemPrompt && (
                <div className="hb-accordion-body">
                  <textarea
                    className="form-field"
                    rows={4}
                    defaultValue={systemJob.prompt}
                    onBlur={(e) => {
                      const newPrompt = e.target.value.trim();
                      if (newPrompt && newPrompt !== systemJob.prompt) {
                        void handleUpdateSystemJob({ prompt: newPrompt });
                      }
                    }}
                    style={{
                      width: "100%",
                      border: "1px solid var(--line)",
                      borderRadius: 14,
                      padding: "12px 14px",
                      background: "var(--surface-strong)",
                      color: "var(--text)",
                      resize: "vertical",
                      font: "inherit",
                    }}
                  />
                </div>
              )}
            </div>

            {/* Status row */}
            <div className="hb-metrics" style={{ marginTop: 12 }}>
              <div className="hb-metric">
                <p className="hb-metric-label">Next run</p>
                <p className="hb-metric-value">
                  {systemJob.next_run_at ? timeAgo(systemJob.next_run_at) : "pending"}
                </p>
              </div>
              <div className="hb-metric">
                <p className="hb-metric-label">Last run</p>
                <p className="hb-metric-value">
                  {systemJob.last_run_at ? timeAgo(systemJob.last_run_at) : "no runs yet"}
                </p>
              </div>

            </div>

            <div className="action-row" style={{ marginTop: 12 }}>
              <button
                type="button"
                className="ghost-button"
                onClick={() => void runJob(systemJob.id)}
              >
                ▶ Run now
              </button>
            </div>
          </div>
        )}

        {/* ── Custom Heartbeats ──────────────────── */}
        <div className="subcard">
          <div className="hb-section-head">
            <div>
              <p className="eyebrow">Custom</p>
              <h3>Your heartbeats</h3>
            </div>
            <button
              type="button"
              className="primary-button"
              onClick={() => setModalOpen(true)}
            >
              + New heartbeat
            </button>
          </div>

          {userJobs.length === 0 ? (
            <div className="hb-empty">
              <p style={{ fontSize: "1.5rem", marginBottom: 4 }}>📋</p>
              <p>No custom heartbeats yet</p>
              <button
                type="button"
                className="ghost-button"
                style={{ marginTop: 12 }}
                onClick={() => setModalOpen(true)}
              >
                Create your first
              </button>
            </div>
          ) : (
            <div className="hb-job-list">
              {userJobs.map((job) => (
                <article key={job.id} className="hb-job-card">
                  <div>
                    <p className="hb-job-name">{job.name}</p>
                    <p className="hb-job-prompt">{job.prompt}</p>
                    <div className="hb-job-tags">
                      <span className="hb-job-tag">
                        {job.schedule_kind === "every"
                          ? `every ${humanInterval(job.interval_seconds)}`
                          : `at ${job.run_at || "pending"}`}
                      </span>

                      <span
                        className="hb-job-tag"
                        style={
                          job.enabled
                            ? { color: "#10b981", borderColor: "rgba(16,185,129,0.25)" }
                            : { color: "#fbbf24", borderColor: "rgba(251,191,36,0.25)" }
                        }
                      >
                        {job.enabled ? "enabled" : "paused"}
                      </span>
                      {job.last_run_at && (
                        <span className="hb-job-tag" title={job.last_run_at}>
                          last: {timeAgo(job.last_run_at)}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="hb-job-actions">
                    <button
                      type="button"
                      className="hb-icon-btn"
                      title={job.enabled ? "Pause" : "Enable"}
                      onClick={() => void handleToggleJob(job)}
                    >
                      {job.enabled ? "⏸" : "▶"}
                    </button>
                    <button
                      type="button"
                      className="hb-icon-btn"
                      title="Run now"
                      onClick={() => void runJob(job.id)}
                    >
                      ▶
                    </button>
                    <button
                      type="button"
                      className="hb-icon-btn hb-icon-btn-danger"
                      title="Delete"
                      onClick={() =>
                        void deleteJob(job.id).then(() =>
                          setJobs((cur) => cur.filter((j) => j.id !== job.id)),
                        )
                      }
                    >
                      ✕
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </div>
      </div>

      {/* ── Create Heartbeat Modal ─────────────── */}
      {modalOpen && (
        <div className="hb-modal-overlay" onClick={() => setModalOpen(false)}>
          <div className="hb-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="hb-modal-header">
              <div>
                <p className="eyebrow">New</p>
                <h3>Create heartbeat</h3>
              </div>
              <button
                type="button"
                className="hb-modal-close"
                onClick={() => setModalOpen(false)}
              >
                ✕
              </button>
            </div>

            <label className="form-field">
              <span>Name</span>
              <input
                value={form.name}
                placeholder="e.g. Weekly summary"
                onChange={(e) => setForm((c) => ({ ...c, name: e.target.value }))}
              />
            </label>

            <label className="form-field">
              <span>Prompt</span>
              <textarea
                rows={4}
                value={form.prompt}
                placeholder="What should the agent do on each run..."
                onChange={(e) => setForm((c) => ({ ...c, prompt: e.target.value }))}
              />
            </label>



            <div>
              <span style={{ fontSize: "0.85rem", color: "var(--muted)", display: "block", marginBottom: 8 }}>
                Interval
              </span>
              <div className="hb-interval-row">
                {INTERVAL_PRESETS.map((p) => (
                  <button
                    key={p.seconds}
                    type="button"
                    className={`hb-interval-chip${Number(form.intervalSeconds) === p.seconds ? " hb-interval-chip-active" : ""}`}
                    onClick={() => setForm((c) => ({ ...c, intervalSeconds: String(p.seconds) }))}
                  >
                    {p.label}
                  </button>
                ))}
              </div>
            </div>

            <div className="action-row" style={{ justifyContent: "flex-end" }}>
              <button
                type="button"
                className="ghost-button"
                onClick={() => setModalOpen(false)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={!form.name.trim() || !form.prompt.trim()}
                onClick={() => void handleCreateJob()}
              >
                Create heartbeat
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
