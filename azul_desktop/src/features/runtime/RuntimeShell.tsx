import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import { loadRuntime } from "../../lib/api";
import type { RuntimeOverview } from "../../lib/contracts";
import { runtimeOverview } from "../../lib/mock-data";

const laneOptions = ["auto", "fast", "slow"] as const;
type Lane = (typeof laneOptions)[number];

export function RuntimeShell() {
  const { t } = useTranslation();
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
            <p className="eyebrow">{t("runtime.eyebrow")}</p>
            <h2>{t("runtime.modelsAndInference")}</h2>
          </div>
          <div className="filter-row">
            <span
              className={`status-pill${runtime.scheduler_running ? " status-pill-live" : ""}`}
            >
              {runtime.scheduler_running ? t("runtime.schedulerRunning") : t("runtime.schedulerStopped")}
            </span>
            <span className="status-pill">{runtime.jobs_total} {t("runtime.jobs")}</span>
            <span className="status-pill">{runtime.jobs_running} {t("runtime.running")}</span>
          </div>
        </div>

        <div className="runtime-lane-bar subcard">
          <div className="runtime-lane-meta">
            <p className="eyebrow">{t("runtime.defaultLane")}</p>
            <p className="runtime-lane-hint">
              {t(`runtime.lane.${runtime.default_lane}Desc`)}
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
                {t(`runtime.lane.${lane}`)}
              </button>
            ))}
          </div>
        </div>

        <div className="two-column-grid">
          {runtime.models.map((model) => (
            <section key={model.id} className="subcard runtime-model-card">
              <div className="runtime-model-header">
                <div>
                  <p className="eyebrow">{model.lane === "fast" ? t("runtime.fastBrain") : t("runtime.slowBrain")}</p>
                  <h3 className="runtime-model-name">{model.label}</h3>
                  <p className="runtime-model-desc">{model.description}</p>
                </div>
                <span
                  className={`runtime-status-dot${model.available ? " runtime-status-dot-ok" : " runtime-status-dot-err"}`}
                  title={model.available ? t("runtime.available") : t("runtime.unavailable")}
                />
              </div>

              <div className="runtime-kv-list">
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">{t("runtime.provider")}</span>
                  <span className="runtime-kv-val">{model.provider}</span>
                </div>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">{t("runtime.deployment")}</span>
                  <code className="runtime-kv-code">{model.deployment}</code>
                </div>
                <div className="runtime-kv-row">
                  <span className="runtime-kv-key">{t("runtime.status")}</span>
                  <span className={`status-tag ${model.available ? "status-done" : "status-failed"}`}>
                    {model.available ? t("runtime.available") : t("runtime.unavailable")}
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
                {t("runtime.streaming")}
              </label>

              {model.probe_detail ? (
                <div className="runtime-probe">
                  <span className="runtime-kv-key">{t("runtime.probe")}</span>
                  <code className="inline-code">{model.probe_detail}</code>
                </div>
              ) : null}

              {model.last_error ? (
                <div className="error-block">
                  <span className="error-block-label">{t("runtime.lastError")}</span>
                  <code className="error-code">{model.last_error}</code>
                </div>
              ) : null}
            </section>
          ))}
        </div>

        {runtime.scheduler_last_error ? (
          <div className="error-block">
            <span className="error-block-label">{t("runtime.schedulerError")}</span>
            <code className="error-code">{runtime.scheduler_last_error}</code>
          </div>
        ) : null}

      </div>
    </section>
  );
}
