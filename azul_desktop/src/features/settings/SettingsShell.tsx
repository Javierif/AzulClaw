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
            <h2>Preferences and configuration</h2>
          </div>
          <div className="filter-row">
            <span className="status-pill">{runtime.default_lane}</span>
            <span className="status-pill">{runtime.models.length} models</span>
          </div>
        </div>

        <div className="three-column-grid">

          {/* ── Identity ──────────────────────────── */}
          <section className="subcard settings-card">
            <div className="settings-card-header">
              <div className="settings-card-icon settings-icon-identity">
                <span>✦</span>
              </div>
              <div>
                <p className="eyebrow">Identity</p>
                <h3 className="settings-card-title">Personality</h3>
              </div>
            </div>

            <p className="settings-card-desc">
              Configure how AzulClaw presents itself — its name, tone, style and base role.
            </p>

            <div className="runtime-kv-list">
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Name</span>
                <span className="runtime-kv-val">AzulClaw</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Archetype</span>
                <span className="runtime-kv-val">Guardian</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Tone</span>
                <span className="runtime-kv-val">Direct</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Style</span>
                <span className="runtime-kv-val">Explanatory</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Autonomy</span>
                <span className="runtime-kv-val">Moderately autonomous</span>
              </div>
            </div>

            <div className="settings-card-footer">
              <button type="button" className="skill-action-btn">Edit in Hatching →</button>
            </div>
          </section>

          {/* ── Models ────────────────────────────── */}
          <section className="subcard settings-card">
            <div className="settings-card-header">
              <div className="settings-card-icon settings-icon-models">
                <span>⬡</span>
              </div>
              <div>
                <p className="eyebrow">Models</p>
                <h3 className="settings-card-title">Providers</h3>
              </div>
            </div>

            <p className="settings-card-desc">
              Configure AI providers, deployments and fallback strategy for fast and slow lanes.
            </p>

            <div className="runtime-kv-list">
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Fast lane</span>
                <code className="runtime-kv-code">gpt-4o-mini</code>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Slow lane</span>
                <code className="runtime-kv-code">gpt-4o</code>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Provider</span>
                <span className="runtime-kv-val">Azure OpenAI</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Default lane</span>
                <code className="runtime-kv-code">{runtime.default_lane}</code>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Streaming</span>
                <span className="runtime-kv-val">Fast only</span>
              </div>
            </div>

            <div className="settings-card-footer">
              <button type="button" className="skill-action-btn">Configure in Runtime →</button>
            </div>
          </section>

          {/* ── Security ──────────────────────────── */}
          <section className="subcard settings-card">
            <div className="settings-card-header">
              <div className="settings-card-icon settings-icon-security">
                <span>◈</span>
              </div>
              <div>
                <p className="eyebrow">Security</p>
                <h3 className="settings-card-title">Approvals</h3>
              </div>
            </div>

            <p className="settings-card-desc">
              Manage which actions require human confirmation and control the sandbox boundaries.
            </p>

            <div className="runtime-kv-list">
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Confirm sensitive</span>
                <span className="status-tag status-done">Enabled</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Sandbox</span>
                <span className="status-tag status-done">Active</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Path escaping</span>
                <span className="status-tag status-done">Blocked</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Destructive ops</span>
                <span className="status-tag status-done">Guarded</span>
              </div>
              <div className="runtime-kv-row">
                <span className="runtime-kv-key">Credentials</span>
                <span className="status-tag status-waiting">Review</span>
              </div>
            </div>

            <div className="settings-card-footer">
              <button type="button" className="skill-action-btn">Review security →</button>
            </div>
          </section>

        </div>

        <div className="panel-footer">
          <p className="hint-text">Heartbeat and scheduled job settings live in the Heartbeats view.</p>
          <button
            type="button"
            className="primary-button"
            disabled={isSaving}
            onClick={() => void handleSave()}
          >
            {isSaving ? "Saving..." : "Save settings"}
          </button>
        </div>
      </div>
    </section>
  );
}
