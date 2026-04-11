import { useEffect, useState } from "react";

import { loadRuntime } from "../../lib/api";
import type { RuntimeOverview } from "../../lib/contracts";
import { runtimeOverview } from "../../lib/mock-data";

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
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Automations</p>
            <h2>Scheduler &amp; runtime</h2>
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

        <div className="three-column-grid">
          <section className="subcard">
            <p className="eyebrow">Jobs</p>
            <h3>Overview</h3>
            <p><strong>Total jobs:</strong> {runtime.jobs_total}</p>
            <p><strong>Active jobs:</strong> {runtime.jobs_running}</p>
            <p><strong>Visible processes:</strong> {runtime.processes_visible}</p>
          </section>

          <section className="subcard">
            <p className="eyebrow">Inference</p>
            <h3>Default lane</h3>
            <p><strong>Active route:</strong> {runtime.default_lane}</p>
            <p>Defines which brain to use when the request doesn't force a specific lane.</p>
            <p><strong>Brains:</strong> configuration lives in Settings.</p>
          </section>

          <section className="subcard">
            <p className="eyebrow">Scheduler</p>
            <h3>Scheduled jobs</h3>
            <p>
              <strong>Status:</strong>{" "}
              <span className={`status-tag ${runtime.scheduler_running ? "status-done" : "status-waiting"}`}>
                {runtime.scheduler_running ? "Running" : "Stopped"}
              </span>
            </p>
            {runtime.scheduler_last_error && (
              <p style={{ color: "var(--danger)", fontSize: "0.88rem", marginTop: 4 }}>
                {runtime.scheduler_last_error}
              </p>
            )}
            <p style={{ marginTop: 8, color: "var(--muted)", fontSize: "0.88rem" }}>
              Manage all scheduled jobs from the <strong>Heartbeats</strong> view in the sidebar.
            </p>
          </section>
        </div>
      </div>
    </section>
  );
}
