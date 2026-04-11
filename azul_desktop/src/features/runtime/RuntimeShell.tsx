import { useEffect, useState } from "react";

import { deleteJob, loadJobs, loadRuntime, runJob, saveJob, saveRuntime } from "../../lib/api";
import type { RuntimeOverview, ScheduledJob } from "../../lib/contracts";
import { runtimeOverview, scheduledJobs } from "../../lib/mock-data";

const laneOptions = ["auto", "fast", "slow"] as const;
type Lane = (typeof laneOptions)[number];
type JobDraft = {
  name: string;
  prompt: string;
  lane: Lane;
  intervalSeconds: string;
};

export function RuntimeShell() {
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);
  const [jobs, setJobs] = useState<ScheduledJob[]>(scheduledJobs);
  const [form, setForm] = useState<JobDraft>({
    name: "",
    prompt: "",
    lane: "fast",
    intervalSeconds: "3600",
  });

  useEffect(() => {
    let isMounted = true;

    Promise.all([loadRuntime(), loadJobs()]).then(([runtimeData, jobData]) => {
      if (!isMounted) {
        return;
      }
      setRuntime(runtimeData);
      setJobs(jobData);
    });

    return () => {
      isMounted = false;
    };
  }, []);

  async function handleRuntimeSave() {
    const next = await saveRuntime({
      default_lane: runtime.default_lane,
      heartbeat_enabled: runtime.heartbeat.enabled,
      heartbeat_interval_seconds: runtime.heartbeat.interval_seconds,
      heartbeat_prompt: runtime.heartbeat.prompt,
      models: runtime.models.map((model) => ({
        id: model.id,
        streaming_enabled: model.streaming_enabled,
      })),
    });
    setRuntime((current) => ({
      ...current,
      ...next,
      heartbeat: next.heartbeat ?? current.heartbeat,
    }));
  }

  async function handleCreateJob() {
    if (!form.name.trim() || !form.prompt.trim()) {
      return;
    }
    const saved = await saveJob({
      name: form.name.trim(),
      prompt: form.prompt.trim(),
      lane: form.lane,
      schedule_kind: "every",
      interval_seconds: Number(form.intervalSeconds) || 3600,
    });
    setJobs((current) => [saved, ...current.filter((item) => item.id !== saved.id)]);
    setForm({ name: "", prompt: "", lane: "fast", intervalSeconds: "3600" });
  }

  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Runtime</p>
            <h2>Modelos, heartbeats y automatizaciones</h2>
          </div>
          <div className="filter-row">
            <span className="status-pill">{runtime.jobs_total} jobs</span>
            <span className="status-pill">{runtime.processes_visible} procesos</span>
          </div>
        </div>

        <div className="three-column-grid">
          {runtime.models.map((model) => (
            <section key={model.id} className="subcard">
              <p className="eyebrow">{model.lane === "fast" ? "Fast Brain" : "Slow Brain"}</p>
              <h3>{model.label}</h3>
              <p>{model.description}</p>
              <p><strong>Proveedor:</strong> {model.provider}</p>
              <p><strong>Deployment:</strong> {model.deployment}</p>
              <p><strong>Estado:</strong> {model.available ? "Disponible" : "No disponible"}</p>
              <label className="toggle-row">
                <input
                  type="checkbox"
                  checked={model.streaming_enabled}
                  onChange={(event) =>
                    setRuntime((current) => ({
                      ...current,
                      models: current.models.map((item) =>
                        item.id === model.id
                          ? { ...item, streaming_enabled: event.target.checked }
                          : item,
                      ),
                    }))
                  }
                />
                Streaming de respuestas
              </label>
              <p><strong>Probe:</strong> {model.probe_detail}</p>
              {model.last_error ? <p><strong>Ultimo error:</strong> {model.last_error}</p> : null}
            </section>
          ))}

          <section className="subcard">
            <p className="eyebrow">Default lane</p>
            <h3>Ruta de inferencia</h3>
            <div className="filter-row">
              {laneOptions.map((lane) => (
                <button
                  key={lane}
                  type="button"
                  className={lane === runtime.default_lane ? "primary-button" : "ghost-button"}
                  onClick={() => setRuntime((current) => ({ ...current, default_lane: lane }))}
                >
                  {lane}
                </button>
              ))}
            </div>
          </section>
        </div>

        <div className="two-column-grid">
          <section className="subcard form-section">
            <div>
              <p className="eyebrow">Heartbeat</p>
              <h3>Checklist periodica</h3>
            </div>
            <label className="toggle-row">
              <input
                type="checkbox"
                checked={runtime.heartbeat.enabled}
                onChange={(event) =>
                  setRuntime((current) => ({
                    ...current,
                    heartbeat: { ...current.heartbeat, enabled: event.target.checked },
                  }))
                }
              />
              Activar heartbeat del workspace
            </label>
            <label className="form-field">
              <span>Intervalo en segundos</span>
              <input
                type="number"
                min={60}
                value={runtime.heartbeat.interval_seconds}
                onChange={(event) =>
                  setRuntime((current) => ({
                    ...current,
                    heartbeat: {
                      ...current.heartbeat,
                      interval_seconds: Number(event.target.value) || 900,
                    },
                  }))
                }
              />
            </label>
            <label className="form-field">
              <span>Prompt de heartbeat</span>
              <textarea
                rows={5}
                value={runtime.heartbeat.prompt}
                onChange={(event) =>
                  setRuntime((current) => ({
                    ...current,
                    heartbeat: { ...current.heartbeat, prompt: event.target.value },
                  }))
                }
              />
            </label>
            <p><strong>Archivo:</strong> {runtime.heartbeat.heartbeat_file}</p>
            <p><strong>Ultimo resultado:</strong> {runtime.heartbeat.last_result || "sin ejecuciones"}</p>
            <button type="button" className="primary-button" onClick={() => void handleRuntimeSave()}>
              Guardar runtime
            </button>
          </section>

          <section className="subcard form-section">
            <div>
              <p className="eyebrow">Nuevo job</p>
              <h3>Cron local</h3>
            </div>
            <label className="form-field">
              <span>Nombre</span>
              <input
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </label>
            <label className="form-field">
              <span>Prompt</span>
              <textarea
                rows={4}
                value={form.prompt}
                onChange={(event) => setForm((current) => ({ ...current, prompt: event.target.value }))}
              />
            </label>
            <label className="form-field">
              <span>Cerebro</span>
              <select
                  value={form.lane}
                  onChange={(event) =>
                    setForm((current) => ({ ...current, lane: event.target.value as Lane }))
                }
              >
                {laneOptions.map((lane) => (
                  <option key={lane} value={lane}>
                    {lane}
                  </option>
                ))}
              </select>
            </label>
            <label className="form-field">
              <span>Intervalo (segundos)</span>
              <input
                type="number"
                min={60}
                value={form.intervalSeconds}
                onChange={(event) =>
                  setForm((current) => ({ ...current, intervalSeconds: event.target.value }))
                }
              />
            </label>
            <button type="button" className="primary-button" onClick={() => void handleCreateJob()}>
              Crear job
            </button>
          </section>
        </div>

        <div className="subcard">
          <p className="eyebrow">Jobs</p>
          <h3>Automatizaciones programadas</h3>
          {jobs.length === 0 ? <p>No hay jobs creados todavia.</p> : null}
          {jobs.map((job) => (
            <article key={job.id} className="list-row">
              <div>
                <strong>{job.name}</strong>
                <p>{job.prompt}</p>
                <p>
                  {job.schedule_kind === "every"
                    ? `Cada ${job.interval_seconds}s`
                    : `En ${job.run_at || "fecha pendiente"}`}
                </p>
              </div>
              <div className="list-row-meta">
                <span className={`status-tag ${job.enabled ? "status-done" : "status-waiting"}`}>
                  {job.enabled ? "enabled" : "disabled"}
                </span>
                <span>{job.lane}</span>
                <div className="action-row">
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() => void runJob(job.id)}
                  >
                    Run now
                  </button>
                  <button
                    type="button"
                    className="ghost-button"
                    onClick={() =>
                      void deleteJob(job.id).then(() =>
                        setJobs((current) => current.filter((item) => item.id !== job.id)),
                      )
                    }
                  >
                    Delete
                  </button>
                </div>
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
