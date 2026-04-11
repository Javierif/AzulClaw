import { useEffect, useState } from "react";

import { loadRuntime, saveRuntime } from "../../lib/api";
import type { RuntimeOverview } from "../../lib/contracts";
import { runtimeOverview } from "../../lib/mock-data";

const laneOptions = ["auto", "fast", "slow"] as const;

export function SettingsShell() {
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);
  const [isSaving, setIsSaving] = useState(false);

  useEffect(() => {
    let isMounted = true;

    async function refreshSettings() {
      const runtimeData = await loadRuntime();
      if (!isMounted) {
        return;
      }
      setRuntime(runtimeData);
    }

    void refreshSettings();
    const pollId = window.setInterval(() => {
      void refreshSettings();
    }, 10000);

    return () => {
      isMounted = false;
      window.clearInterval(pollId);
    };
  }, []);

  async function handleSave() {
    setIsSaving(true);
    try {
      const next = await saveRuntime({
        default_lane: runtime.default_lane,
        models: runtime.models.map((model) => ({
          id: model.id,
          streaming_enabled: model.streaming_enabled,
        })),
      });
      setRuntime(next);
    } finally {
      setIsSaving(false);
    }
  }

  return (
    <section className="single-panel-layout">
      <div className="card panel-stack">
        <div className="panel-heading">
          <div>
            <p className="eyebrow">Settings</p>
            <h2>Brains e inferencia</h2>
          </div>
          <div className="filter-row">
            <span className="status-pill">{runtime.default_lane}</span>
            <span className="status-pill">{runtime.models.length} modelos</span>
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
            <p className="eyebrow">Inference</p>
            <h3>Ruta por defecto</h3>
            <p>Define qué cerebro usar cuando la petición no fuerce una lane concreta.</p>
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
            <p><strong>Heartbeat:</strong> movido a la vista Heartbeats.</p>
            <p><strong>Heartbeats del usuario:</strong> los programados también viven en Heartbeats.</p>
          </section>
        </div>

        <div className="panel-footer">
          <p className="hint-text">Las preferencias cognitivas ya no comparten pantalla con los heartbeats.</p>
          <button
            type="button"
            className="primary-button"
            disabled={isSaving}
            onClick={() => void handleSave()}
          >
            {isSaving ? "Guardando..." : "Guardar settings"}
          </button>
        </div>
      </div>
    </section>
  );
}
