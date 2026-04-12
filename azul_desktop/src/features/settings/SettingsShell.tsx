import { useEffect, useState } from "react";

import { DATA_WIPE_CONFIRM_PHRASE, loadRuntime, saveRuntime, wipeLocalUserData } from "../../lib/api";
import type { HatchingProfile, RuntimeOverview } from "../../lib/contracts";
import { runtimeOverview } from "../../lib/mock-data";

interface SettingsShellProps {
  /** After a successful wipe, parent should replace profile (triggers onboarding when ``is_hatched`` is false). */
  onLocalDataWiped?: (profile: HatchingProfile) => void;
}

export function SettingsShell({ onLocalDataWiped }: SettingsShellProps) {
  const [runtime, setRuntime] = useState<RuntimeOverview>(runtimeOverview);
  const [isSaving, setIsSaving] = useState(false);
  const [wipeModalOpen, setWipeModalOpen] = useState(false);
  const [wipePhrase, setWipePhrase] = useState("");
  const [wipeBusy, setWipeBusy] = useState(false);
  const [wipeError, setWipeError] = useState("");
  const [wipeCopied, setWipeCopied] = useState(false);

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

  function handleCopyPhrase() {
    void navigator.clipboard.writeText(DATA_WIPE_CONFIRM_PHRASE);
    setWipeCopied(true);
    setTimeout(() => setWipeCopied(false), 2000);
  }

  function openWipeModal() {
    setWipePhrase("");
    setWipeError("");
    setWipeModalOpen(true);
  }

  function closeWipeModal() {
    if (wipeBusy) return;
    setWipeModalOpen(false);
    setWipePhrase("");
    setWipeError("");
  }

  async function handleWipeLocalData() {
    setWipeError("");
    if (wipePhrase.trim() !== DATA_WIPE_CONFIRM_PHRASE) {
      setWipeError(`Type exactly: ${DATA_WIPE_CONFIRM_PHRASE}`);
      return;
    }
    setWipeBusy(true);
    try {
      const result = await wipeLocalUserData(wipePhrase.trim());
      setWipeModalOpen(false);
      setWipePhrase("");
      onLocalDataWiped?.(result);
    } catch (error) {
      const detail = error instanceof Error ? error.message : String(error);
      setWipeError(detail || "Wipe failed.");
    } finally {
      setWipeBusy(false);
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

        {onLocalDataWiped && (
          <section className="subcard" style={{ borderColor: "var(--danger, #b42318)", marginTop: "8px" }}>
            <p className="eyebrow" style={{ color: "var(--danger, #b42318)" }}>Data &amp; onboarding</p>
            <h3 className="settings-card-title">Erase local AzulClaw data</h3>
            <p className="hint-text" style={{ marginBottom: "14px" }}>
              Removes the SQLite memory database and resets your hatching profile.
              You will go through onboarding again. Restart the brain process afterward so memory reopens cleanly.
            </p>
            <button
              type="button"
              className="primary-button"
              style={{ background: "var(--danger, #b42318)", borderColor: "var(--danger, #b42318)" }}
              onClick={openWipeModal}
            >
              Erase data and reset onboarding
            </button>
          </section>
        )}

        {wipeModalOpen && (
          <div className="hw-modal-backdrop" onClick={closeWipeModal}>
            <section
              className="hw-modal-card"
              role="dialog"
              aria-modal="true"
              style={{ maxWidth: "480px" }}
              onClick={(e) => e.stopPropagation()}
            >
              <div className="hw-modal-head">
                <div>
                  <p className="hw-label" style={{ color: "var(--danger, #b42318)" }}>DESTRUCTIVE ACTION</p>
                  <h3 className="hw-modal-title">Confirm data erasure</h3>
                </div>
                <button type="button" className="hw-btn-ghost" onClick={closeWipeModal} disabled={wipeBusy}>Close</button>
              </div>

              <p className="hw-inline-note" style={{ marginBottom: "16px" }}>
                This will permanently delete all memory and reset your profile.
                To confirm, copy and type the phrase below.
              </p>

              <div style={{ display: "flex", alignItems: "center", gap: "10px", background: "var(--surface-2, #1e1e2e)", borderRadius: "6px", padding: "10px 14px", marginBottom: "16px" }}>
                <code style={{ flex: 1, fontSize: "0.85rem", letterSpacing: "0.04em", userSelect: "all" }}>
                  {DATA_WIPE_CONFIRM_PHRASE}
                </code>
                <button type="button" className="ghost-button" style={{ flexShrink: 0 }} onClick={handleCopyPhrase}>
                  {wipeCopied ? "Copied" : "Copy"}
                </button>
              </div>

              <label className="hw-modal-field">
                <span className="hw-field-label">Type the phrase to confirm</span>
                <input
                  className="hw-modal-input hw-input-mono"
                  type="text"
                  autoComplete="off"
                  placeholder="Paste or type here..."
                  value={wipePhrase}
                  onChange={(e) => setWipePhrase(e.target.value)}
                  onKeyDown={(e) => { if (e.key === "Enter" && !wipeBusy) void handleWipeLocalData(); }}
                  autoFocus
                />
              </label>

              {wipeError && (
                <p className="hw-inline-note hw-inline-note-warning" style={{ marginTop: "8px" }}>{wipeError}</p>
              )}

              <div className="hw-modal-actions" style={{ marginTop: "16px" }}>
                <button type="button" className="hw-btn-ghost" onClick={closeWipeModal} disabled={wipeBusy}>Cancel</button>
                <button
                  type="button"
                  className="hw-btn-primary"
                  style={{ background: "var(--danger, #b42318)", borderColor: "var(--danger, #b42318)" }}
                  disabled={wipeBusy || wipePhrase.trim() !== DATA_WIPE_CONFIRM_PHRASE}
                  onClick={() => void handleWipeLocalData()}
                >
                  {wipeBusy ? "Wiping…" : "Erase everything"}
                </button>
              </div>
            </section>
          </div>
        )}

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
