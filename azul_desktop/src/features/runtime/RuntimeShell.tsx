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
            <h2>Scheduler y runtime</h2>
          </div>
          <div className="filter-row">
            <span
              className={`status-pill${runtime.scheduler_running ? " status-pill-live" : ""}`}
            >
              {runtime.scheduler_running ? "Scheduler activo" : "Scheduler detenido"}
            </span>
            <span className="status-pill">{runtime.jobs_total} jobs</span>
            <span className="status-pill">{runtime.jobs_running} running</span>
          </div>
        </div>

        <div className="three-column-grid">
          <section className="subcard">
            <p className="eyebrow">Jobs</p>
            <h3>Resumen</h3>
            <p><strong>Jobs totales:</strong> {runtime.jobs_total}</p>
            <p><strong>Jobs activos:</strong> {runtime.jobs_running}</p>
            <p><strong>Procesos visibles:</strong> {runtime.processes_visible}</p>
          </section>

          <section className="subcard">
            <p className="eyebrow">Inference</p>
            <h3>Lane por defecto</h3>
            <p><strong>Ruta activa:</strong> {runtime.default_lane}</p>
            <p>Define qué cerebro usar cuando la petición no fuerce una lane concreta.</p>
            <p><strong>Brains:</strong> la configuración vive en Settings.</p>
          </section>

          <section className="subcard">
            <p className="eyebrow">Heartbeat</p>
            <h3>Pulso del workspace</h3>
            <p>
              <strong>Estado:</strong>{" "}
              <span
                className={`status-tag ${runtime.heartbeat.enabled ? "status-done" : "status-waiting"}`}
              >
                {runtime.heartbeat.enabled ? "Activo" : "Pausado"}
              </span>
            </p>
            <p><strong>Intervalo:</strong> {runtime.heartbeat.interval_seconds}s</p>
            <p style={{ marginTop: 8, color: "var(--muted)", fontSize: "0.88rem" }}>
              Gestiona los heartbeats y jobs programados desde la vista <strong>Heartbeats</strong> en el menú lateral.
            </p>
          </section>
        </div>
      </div>
    </section>
  );
}
