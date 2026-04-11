import { useEffect, useState } from "react";

import {
  deleteJob,
  loadJobs,
  loadRuntime,
  runHeartbeat,
  runJob,
  saveJob,
  saveRuntime,
} from "../../lib/api";
import type { RuntimeOverview, ScheduledJob } from "../../lib/contracts";
import { runtimeOverview, scheduledJobs } from "../../lib/mock-data";

/* ── Helpers ───────────────────────────────────── */

const INTERVAL_PRESETS = [
  { label: "5 min", seconds: 300 },
  { label: "15 min", seconds: 900 },
  { label: "30 min", seconds: 1800 },
  { label: "1 hora", seconds: 3600 },
  { label: "2 horas", seconds: 7200 },
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
      if (abs < 60_000) return `en ${Math.round(abs / 1000)}s`;
      if (abs < 3_600_000) return `en ${Math.round(abs / 60_000)} min`;
      return `en ${(abs / 3_600_000).toFixed(1)}h`;
    }
    if (diff < 60_000) return `hace ${Math.round(diff / 1000)}s`;
    if (diff < 3_600_000) return `hace ${Math.round(diff / 60_000)} min`;
    return `hace ${(diff / 3_600_000).toFixed(1)}h`;
  } catch {
    return iso;
  }
}

function resultClass(result: string): string {
  if (!result) return "hb-metric-idle";
  const upper = result.toUpperCase();
  if (upper.includes("ERROR")) return "hb-metric-error";
  if (upper.includes("SKIP")) return "hb-metric-skip";
  if (upper.includes("OK") || upper === "HEARTBEAT_OK") return "hb-metric-ok";
  return "hb-metric-ok";
}

function progressPercent(runtime: RuntimeOverview): number {
  const { last_run_at, next_run_at } = runtime.heartbeat;
  if (!last_run_at || !next_run_at) return 0;
  try {
    const start = new Date(last_run_at).getTime();
    const end = new Date(next_run_at).getTime();
    const now = Date.now();
    const total = end - start;
    if (total <= 0) return 100;
    const elapsed = now - start;
    return Math.min(100, Math.max(0, (elapsed / total) * 100));
  } catch {
    return 0;
  }
}

/* ── Types ─────────────────────────────────────── */

const laneOptions = ["auto", "fast", "slow"] as const;
type Lane = (typeof laneOptions)[number];
type JobDraft = {
  name: string;
  prompt: string;
  lane: Lane;
  intervalSeconds: string;
};

const emptyDraft: JobDraft = { name: "", prompt: "", lane: "fast", intervalSeconds: "3600" };

/* ── Component ─────────────────────────────────── */

export function HeartbeatsShell() {
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);
  const [jobs, setJobs] = useState<ScheduledJob[]>(scheduledJobs);
  const [isSaving, setIsSaving] = useState(false);
  const [isRunningHeartbeat, setIsRunningHeartbeat] = useState(false);
  const [promptOpen, setPromptOpen] = useState(false);
  const [modalOpen, setModalOpen] = useState(false);
  const [form, setForm] = useState<JobDraft>(emptyDraft);

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

  /* Actions */
  async function handleSave() {
    setIsSaving(true);
    try {
      const next = await saveRuntime({
        heartbeat_enabled: runtime.heartbeat.enabled,
        heartbeat_interval_seconds: runtime.heartbeat.interval_seconds,
        heartbeat_prompt: runtime.heartbeat.prompt,
      });
      setRuntime(next);
    } finally {
      setIsSaving(false);
    }
  }

  async function handleRunHeartbeat() {
    setIsRunningHeartbeat(true);
    try {
      const next = await runHeartbeat();
      setRuntime(next);
    } finally {
      setIsRunningHeartbeat(false);
    }
  }

  async function handleCreateJob() {
    if (!form.name.trim() || !form.prompt.trim()) return;
    const saved = await saveJob({
      name: form.name.trim(),
      prompt: form.prompt.trim(),
      lane: form.lane,
      schedule_kind: "every",
      interval_seconds: Number(form.intervalSeconds) || 3600,
    });
    setJobs((cur) => [saved, ...cur.filter((j) => j.id !== saved.id)]);
    setForm(emptyDraft);
    setModalOpen(false);
  }

  const isPreset = INTERVAL_PRESETS.some((p) => p.seconds === runtime.heartbeat.interval_seconds);

  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        {/* ── Header ─────────────────────────────── */}
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Heartbeats</p>
            <h2>Pulso del workspace</h2>
          </div>
          <div className="filter-row">
            <span className={`status-pill${runtime.scheduler_running ? " status-pill-live" : ""}`}>
              {runtime.scheduler_running ? "Scheduler activo" : "Scheduler detenido"}
            </span>
            <span className={`status-pill${runtime.heartbeat.enabled ? " status-pill-live" : ""}`}>
              {runtime.heartbeat.enabled ? "Heartbeat activo" : "Heartbeat pausado"}
            </span>
          </div>
        </div>

        {/* ── Top Grid: System Config + Status ──── */}
        <div className="two-column-grid">
          {/* System Heartbeat Config */}
          <section className="subcard form-section">
            <div>
              <p className="eyebrow">Sistema</p>
              <h3>Heartbeat del workspace</h3>
            </div>

            <label className="toggle-row">
              <input
                type="checkbox"
                checked={runtime.heartbeat.enabled}
                onChange={(e) =>
                  setRuntime((c) => ({
                    ...c,
                    heartbeat: { ...c.heartbeat, enabled: e.target.checked },
                  }))
                }
              />
              Activar heartbeat periódico
            </label>

            {/* Interval chips */}
            <div>
              <span style={{ fontSize: "0.85rem", color: "var(--muted)" }}>Intervalo</span>
              <div className="hb-interval-row" style={{ marginTop: 8 }}>
                {INTERVAL_PRESETS.map((p) => (
                  <button
                    key={p.seconds}
                    type="button"
                    className={`hb-interval-chip${runtime.heartbeat.interval_seconds === p.seconds ? " hb-interval-chip-active" : ""}`}
                    onClick={() =>
                      setRuntime((c) => ({
                        ...c,
                        heartbeat: { ...c.heartbeat, interval_seconds: p.seconds },
                      }))
                    }
                  >
                    {p.label}
                  </button>
                ))}
                <button
                  type="button"
                  className={`hb-interval-chip${!isPreset ? " hb-interval-chip-active" : ""}`}
                  onClick={() => {
                    const raw = prompt("Intervalo en segundos:", String(runtime.heartbeat.interval_seconds));
                    if (raw) {
                      const val = Number(raw);
                      if (val >= 60) {
                        setRuntime((c) => ({
                          ...c,
                          heartbeat: { ...c.heartbeat, interval_seconds: val },
                        }));
                      }
                    }
                  }}
                >
                  {isPreset ? "Personalizado" : humanInterval(runtime.heartbeat.interval_seconds)}
                </button>
              </div>
            </div>

            {/* Prompt - Accordion */}
            <div className={`hb-accordion${promptOpen ? " hb-accordion-open" : ""}`}>
              <button
                type="button"
                className="hb-accordion-trigger"
                onClick={() => setPromptOpen((o) => !o)}
              >
                <span>Editar prompt del heartbeat</span>
                <span className="hb-accordion-chevron">▼</span>
              </button>
              {promptOpen && (
                <div className="hb-accordion-body">
                  <textarea
                    className="form-field"
                    rows={5}
                    value={runtime.heartbeat.prompt}
                    onChange={(e) =>
                      setRuntime((c) => ({
                        ...c,
                        heartbeat: { ...c.heartbeat, prompt: e.target.value },
                      }))
                    }
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

            <div className="action-row">
              <button
                type="button"
                className="primary-button"
                disabled={isSaving}
                onClick={() => void handleSave()}
              >
                {isSaving ? "Guardando..." : "Guardar"}
              </button>
              <button
                type="button"
                className="ghost-button"
                disabled={isRunningHeartbeat}
                onClick={() => void handleRunHeartbeat()}
              >
                {isRunningHeartbeat ? "Ejecutando..." : "▶ Ejecutar ahora"}
              </button>
            </div>
          </section>

          {/* Status metrics */}
          <section className="subcard">
            <p className="eyebrow">Estado</p>
            <h3>Scheduler</h3>

            <div className="hb-metrics" style={{ marginTop: 12 }}>
              <div className="hb-metric">
                <p className="hb-metric-label">Próximo</p>
                <p className="hb-metric-value">
                  {runtime.heartbeat.next_run_at
                    ? timeAgo(runtime.heartbeat.next_run_at)
                    : "pendiente"}
                </p>
              </div>
              <div className="hb-metric">
                <p className="hb-metric-label">Última ejecución</p>
                <p className="hb-metric-value">
                  {runtime.heartbeat.last_run_at
                    ? timeAgo(runtime.heartbeat.last_run_at)
                    : "sin ejecuciones"}
                </p>
              </div>
              <div className="hb-metric">
                <p className="hb-metric-label">Resultado</p>
                <p className={`hb-metric-value ${resultClass(runtime.heartbeat.last_result)}`}>
                  {runtime.heartbeat.last_result || "—"}
                </p>
              </div>
            </div>

            {/* Progress bar */}
            {runtime.heartbeat.enabled && runtime.heartbeat.next_run_at && (
              <div className="hb-progress-track">
                <div
                  className="hb-progress-fill"
                  style={{ width: `${progressPercent(runtime)}%` }}
                />
              </div>
            )}

            {/* Details pills */}
            <div className="hb-details-row">
              <span className="hb-detail-chip" title={runtime.heartbeat.workspace_root}>
                📁 {runtime.heartbeat.workspace_root}
              </span>
              <span className="hb-detail-chip" title={runtime.heartbeat.heartbeat_file}>
                📄 HEARTBEAT.md
              </span>
            </div>

            {/* Errors */}
            {runtime.heartbeat.last_error && (
              <div className="hb-error-alert">
                <strong>Error heartbeat:</strong> {runtime.heartbeat.last_error}
              </div>
            )}
            {runtime.scheduler_last_error && (
              <div className="hb-error-alert">
                <strong>Error scheduler:</strong> {runtime.scheduler_last_error}
              </div>
            )}
          </section>
        </div>

        {/* ── Jobs Section ──────────────────────── */}
        <div className="subcard">
          <div className="hb-section-head">
            <div>
              <p className="eyebrow">Programados</p>
              <h3>Jobs del usuario</h3>
            </div>
            <button
              type="button"
              className="primary-button"
              onClick={() => setModalOpen(true)}
            >
              + Nuevo job
            </button>
          </div>

          {jobs.length === 0 ? (
            <div className="hb-empty">
              <p style={{ fontSize: "1.5rem", marginBottom: 4 }}>📋</p>
              <p>No hay jobs programados todavía</p>
              <button
                type="button"
                className="ghost-button"
                style={{ marginTop: 12 }}
                onClick={() => setModalOpen(true)}
              >
                Crear el primero
              </button>
            </div>
          ) : (
            <div className="hb-job-list">
              {jobs.map((job) => (
                <article key={job.id} className="hb-job-card">
                  <div>
                    <p className="hb-job-name">{job.name}</p>
                    <p className="hb-job-prompt">{job.prompt}</p>
                    <div className="hb-job-tags">
                      <span className="hb-job-tag">
                        {job.schedule_kind === "every"
                          ? `cada ${humanInterval(job.interval_seconds)}`
                          : `en ${job.run_at || "pendiente"}`}
                      </span>
                      <span className="hb-job-tag">{job.lane}</span>
                      <span
                        className={`hb-job-tag`}
                        style={
                          job.enabled
                            ? { color: "#10b981", borderColor: "rgba(16,185,129,0.25)" }
                            : { color: "#fbbf24", borderColor: "rgba(251,191,36,0.25)" }
                        }
                      >
                        {job.enabled ? "activo" : "pausado"}
                      </span>
                    </div>
                  </div>
                  <div className="hb-job-actions">
                    <button
                      type="button"
                      className="hb-icon-btn"
                      title="Ejecutar ahora"
                      onClick={() => void runJob(job.id)}
                    >
                      ▶
                    </button>
                    <button
                      type="button"
                      className="hb-icon-btn hb-icon-btn-danger"
                      title="Eliminar"
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

      {/* ── Create Job Modal ───────────────────── */}
      {modalOpen && (
        <div className="hb-modal-overlay" onClick={() => setModalOpen(false)}>
          <div className="hb-modal-card" onClick={(e) => e.stopPropagation()}>
            <div className="hb-modal-header">
              <div>
                <p className="eyebrow">Nuevo</p>
                <h3>Crear job programado</h3>
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
              <span>Nombre</span>
              <input
                value={form.name}
                placeholder="Ej: Resumen operativo"
                onChange={(e) => setForm((c) => ({ ...c, name: e.target.value }))}
              />
            </label>

            <label className="form-field">
              <span>Prompt</span>
              <textarea
                rows={4}
                value={form.prompt}
                placeholder="Qué quieres que haga el agente en cada ejecución..."
                onChange={(e) => setForm((c) => ({ ...c, prompt: e.target.value }))}
              />
            </label>

            <label className="form-field">
              <span>Cerebro</span>
              <select
                value={form.lane}
                onChange={(e) => setForm((c) => ({ ...c, lane: e.target.value as Lane }))}
              >
                {laneOptions.map((lane) => (
                  <option key={lane} value={lane}>
                    {lane === "auto" ? "Auto" : lane === "fast" ? "Rápido" : "Lento"}
                  </option>
                ))}
              </select>
            </label>

            <div>
              <span style={{ fontSize: "0.85rem", color: "var(--muted)", display: "block", marginBottom: 8 }}>
                Intervalo
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
                Cancelar
              </button>
              <button
                type="button"
                className="primary-button"
                disabled={!form.name.trim() || !form.prompt.trim()}
                onClick={() => void handleCreateJob()}
              >
                Crear job
              </button>
            </div>
          </div>
        </div>
      )}
    </section>
  );
}
