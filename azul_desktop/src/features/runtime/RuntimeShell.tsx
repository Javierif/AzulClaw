import { useEffect, useState } from "react";

import { Toggle } from "../../components/Toggle";
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

const laneDescriptions: Record<string, string> = {
  auto: "Let AzulClaw choose based on the task complexity",
  fast: "Always use the fast model — quick, lightweight tasks",
  slow: "Always use the slow model — deliberate, context-heavy tasks",
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

        {/* ── Header ─────────────────────────────────── */}
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Runtime</p>
            <h2>Models, heartbeats and automations</h2>
          </div>
          <div className="filter-row">
            <span className="status-pill">{runtime.jobs_total} jobs</span>
            <span className="status-pill">{runtime.processes_visible} processes</span>
          </div>
        </div>

        {/* ── Default lane selector ───────────────────── */}
        <div className="runtime-lane-bar subcard">
          <div className="runtime-lane-meta">
            <p className="eyebrow">Default lane</p>
            <p className="runtime-lane-hint">
              {laneDescriptions[runtime.default_lane]}
            </p>
          </div>
          <div className="runtime-lane-options">
            {laneOptions.map((lane) => (
              <button
                key={lane}
                type="button"
                className={`runtime-lane-btn${lane === runtime.default_lane ? " runtime-lane-btn-active" : ""}`}
                onClick={() => setRuntime((current) => ({ ...current, default_lane: lane }))}
              >
                <span className="runtime-lane-btn-dot" />
                {lane}
              </button>
            ))}
          </div>
        </div>

        {/* ── Model cards ─────────────────────────────── */}
        <div className="two-column-grid">
          {runtime.models.map((model) => (
            <section key={model.id} className="subcard runtime-model-card">
              <div className="runtime-model-header">
                <div>
                  <p className="eyebrow">{model.lane === "fast" ? "Fast Brain" : "Slow Brain"}</p>
                  <h3 className="runtime-model-name">{model.label}</h3>
                  <p className="runtime-model-desc">{model.description}</p>
                </div>
                <span className={`runtime-status-dot${model.available ? " runtime-status-dot-ok" : " runtime-status-dot-err"}`} title={model.available ? "Available" : "Unavailable"} />
              </div>

              <div className="runtime-kv-list">
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">Provider</span>
                  <span className="runtime-kv-val">{model.provider}</span>
                </div>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">Deployment</span>
                  <code className="runtime-kv-code">{model.deployment}</code>
                </div>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">Status</span>
                  <span className={`status-tag ${model.available ? "status-done" : "status-failed"}`}>
                    {model.available ? "Available" : "Unavailable"}
                  </span>
                </div>
              </div>

              <label className="toggle-row runtime-toggle">
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
                Response streaming
              </label>

              {model.probe_detail ? (
                <div className="runtime-probe">
                  <span className="runtime-kv-key">Probe</span>
                  <code className="inline-code">{model.probe_detail}</code>
                </div>
              ) : null}

              {model.last_error ? (
                <div className="error-block">
                  <span className="error-block-label">Last error</span>
                  <code className="error-code">{model.last_error}</code>
                </div>
              ) : null}
            </section>
          ))}
        </div>

        {/* ── Heartbeat + New job ──────────────────────── */}
        <div className="two-column-grid">
          <section className="subcard form-section">
            <div className="runtime-section-header">
              <div>
                <p className="eyebrow">Heartbeat</p>
                <h3>Periodic checklist</h3>
              </div>
              <Toggle
                checked={runtime.heartbeat.enabled}
                label="Enable workspace heartbeat"
                onChange={(val) =>
                  setRuntime((current) => ({
                    ...current,
                    heartbeat: { ...current.heartbeat, enabled: val },
                  }))
                }
              />
            </div>

            <div className="runtime-top-row">
              <label className="form-field runtime-every-field" style={{ flex: 1 }}>
                <span>Interval in seconds</span>
                <div className="runtime-every-wrap">
                  <input
                    type="number"
                    min={60}
                    disabled={!runtime.heartbeat.enabled}
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
                </div>
              </label>
            </div>

            <label className="form-field">
              <span>Heartbeat prompt</span>
              <textarea
                rows={4}
                className="runtime-prompt-textarea"
                disabled={!runtime.heartbeat.enabled}
                value={runtime.heartbeat.prompt}
                onChange={(event) =>
                  setRuntime((current) => ({
                    ...current,
                    heartbeat: { ...current.heartbeat, prompt: event.target.value },
                  }))
                }
              />
            </label>

            <div className="runtime-kv-list">
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">File</span>
                <code className="runtime-kv-code" style={{ maxWidth: "60%", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {runtime.heartbeat.heartbeat_file}
                </code>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Last result</span>
                <span className="runtime-kv-val">{runtime.heartbeat.last_result || "no executions yet"}</span>
              </div>
            </div>

            <button
              type="button"
              className="primary-button"
              disabled={!runtime.heartbeat.enabled}
              onClick={() => void handleRuntimeSave()}
            >
              Save runtime
            </button>
          </section>

          <section className="subcard form-section">
            <div>
              <p className="eyebrow">New job</p>
              <h3>Local cron</h3>
            </div>

            <div className="runtime-top-row">
              <label className="form-field" style={{ flex: 1 }}>
                <span>Brain</span>
                <div className="runtime-every-wrap">
                  <select
                    value={form.lane}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, lane: event.target.value as Lane }))
                    }
                  >
                    {laneOptions.map((lane) => (
                      <option key={lane} value={lane}>{lane}</option>
                    ))}
                  </select>
                </div>
              </label>
              <label className="form-field runtime-every-field">
                <span>Interval in seconds</span>
                <div className="runtime-every-wrap">
                  <input
                    type="number"
                    min={60}
                    value={form.intervalSeconds}
                    onChange={(event) =>
                      setForm((current) => ({ ...current, intervalSeconds: event.target.value }))
                    }
                  />
                </div>
              </label>
            </div>

            <label className="form-field">
              <span>Name</span>
              <input
                value={form.name}
                onChange={(event) => setForm((current) => ({ ...current, name: event.target.value }))}
              />
            </label>

            <label className="form-field">
              <span>Prompt</span>
              <textarea
                rows={3}
                className="runtime-prompt-textarea"
                value={form.prompt}
                onChange={(event) => setForm((current) => ({ ...current, prompt: event.target.value }))}
              />
            </label>

            <button
              type="button"
              className="primary-button"
              onClick={() => void handleCreateJob()}
              disabled={!form.name.trim() || !form.prompt.trim()}
            >
              Create job
            </button>
          </section>
        </div>

        {/* ── Scheduled jobs ──────────────────────────── */}
        <section className="subcard">
          <div className="panel-heading" style={{ marginBottom: "12px" }}>
            <div>
              <p className="eyebrow">Jobs</p>
              <h3>Scheduled automations</h3>
            </div>
            <span className="status-pill">{jobs.length} total</span>
          </div>

          {jobs.length === 0 ? (
            <p style={{ color: "var(--muted)", margin: 0 }}>No jobs created yet.</p>
          ) : (
            jobs.map((job) => (
              <article key={job.id} className="list-row">
                <div style={{ minWidth: 0, flex: 1 }}>
                  <div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
                    <strong>{job.name}</strong>
                    <span className={`status-tag ${job.enabled ? "status-done" : "status-waiting"}`}>
                      {job.enabled ? "enabled" : "disabled"}
                    </span>
                  </div>
                  <p style={{ marginTop: "4px", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{job.prompt}</p>
                  <p>
                    {job.schedule_kind === "every"
                      ? `Every ${job.interval_seconds}s`
                      : `At ${job.run_at || "pending date"}`}
                    {" · "}
                    <span style={{ fontFamily: "ui-monospace, monospace", fontSize: "0.82rem" }}>{job.lane}</span>
                  </p>
                </div>
                <div className="filter-row" style={{ flexShrink: 0 }}>
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
              </article>
            ))
          )}
        </section>

      </div>
    </section>
  );
}
