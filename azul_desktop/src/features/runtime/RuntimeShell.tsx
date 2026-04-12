import { useEffect, useState } from "react";

import { loadRuntime } from "../../lib/api";
import type { RuntimeOverview } from "../../lib/contracts";
import { runtimeOverview } from "../../lib/mock-data";

const laneOptions = ["auto", "fast", "slow"] as const;
type Lane = (typeof laneOptions)[number];

const laneDescriptions: Record<string, string> = {
  auto: "Let AzulClaw choose based on the task complexity",
  fast: "Always use the fast model — quick, lightweight tasks",
  slow: "Always use the slow model — deliberate, context-heavy tasks",
};

export function RuntimeShell() {
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);

  useEffect(() => {
    let isMounted = true;

    async function refreshRuntimePanel() {
      const runtimeData = await loadRuntime();
      if (!isMounted) return;
      setRuntime(runtimeData);
    }

    void refreshRuntimePanel();
    const pollId = window.setInterval(() => void refreshRuntimePanel(), 10_000);

    return () => {
      isMounted = false;
      window.clearInterval(pollId);
    };
  }, []);

  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">

        {/* ── Header ─────────────────────────────────── */}
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Runtime</p>
            <h2>Models and inference</h2>
          </div>
          <div className="filter-row">
            <span
              className={`status-pill${runtime.scheduler_running ? " status-pill-live" : ""}`}
            >
              {runtime.scheduler_running ? "Scheduler running" : "Scheduler stopped"}
            </span>
            <span className="status-pill">{runtime.jobs_total} jobs</span>
            <span className="status-pill">{runtime.jobs_running} running</span>
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
                onClick={() => setRuntime((current) => ({ ...current, default_lane: lane as Lane }))}
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

        {/* ── Scheduler status ─────────────────────────── */}
        {runtime.scheduler_last_error ? (
          <div className="error-block">
            <span className="error-block-label">Scheduler error</span>
            <code className="error-code">{runtime.scheduler_last_error}</code>
          </div>
        ) : null}

      </div>
    </section>
  );
}
